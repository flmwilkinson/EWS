"""AI endpoints: streamed commentary and explanations, the prompt behind each, the audit log, and the Ask AI analyst.

Every response streams as server-sent events:
    {"type": "meta", ...}  {"type": "delta", "text": "..."}  {"type": "tool", ...}  {"type": "done", ...}
"""
from __future__ import annotations

import json
import time

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ews_lib import commentary, dq, llm, runtime
from ews_lib.lakehouse import lh
from server import data as D
from server import ews_api

router = APIRouter(prefix="/api/ai")
SVC = {}

STORED = {  # purpose -> (lakehouse table holding the pipeline's latest text)
    "dq_commentary": "ops.dq_commentary", "model_comparison": "gold.model_comparison_commentary",
    "monitoring_commentary": "ops.monitoring_commentary", "portfolio_summary": "gold.portfolio_commentary",
}


def _sse(gen):
    def wrap():
        try:
            for ev in gen:
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        except Exception as e:  # surface errors to the panel instead of a broken stream
            yield f"data: {json.dumps(dict(type='error', message=f'{type(e).__name__}: {e}'))}\n\n"
    return StreamingResponse(wrap(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _stream(prompt, fallback, purpose, max_tokens=1500):
    yield dict(type="meta", purpose=purpose, model=llm.model(), configured=llm.configured())
    if not llm.configured():
        yield dict(type="delta", text=fallback)
        yield dict(type="done", text=fallback, source="template", model=None)
        return
    s = llm.stream(prompt, purpose=purpose, max_tokens=max_tokens)
    try:
        for chunk in s:
            yield dict(type="delta", text=chunk)
        m = s.meta or {}
        yield dict(type="done", text=s.text, source="llm", model=m.get("served_by") or m.get("model"), latency_ms=m.get("latency_ms"),
                   first_token_ms=m.get("first_token_ms"), input_tokens=m.get("input_tokens"), output_tokens=m.get("output_tokens"))
    except Exception as e:
        yield dict(type="delta", text=("\n\n" if s.text else "") + fallback, replace=True)
        yield dict(type="done", text=fallback, source="template", error=str(e)[:200])


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
def _latest_prompt(purpose):
    return SVC["db"].one("SELECT prompt, ts, model FROM llm_calls WHERE purpose=? AND prompt IS NOT NULL ORDER BY id DESC LIMIT 1", (purpose,))


def _portfolio_live_facts():
    cur, source = ews_api.current()
    live = SVC.get("live")
    thr = ews_api._thr()
    names = dict(zip(thr.trigger_id, thr["name"]))
    batch = ews_api.need("gold.ews_decisions_latest")
    hits = cur.fired_triggers.dropna().astype(str).str.split(",").explode()
    hits = hits[hits.astype(bool) & ~hits.isin(ews_api.CUSTOMER_LEVEL_EXCLUDE) & ~hits.str.startswith(("M", "C"))]
    top = hits.value_counts().head(5)
    reg = cur.groupby("region").tier.apply(lambda t: t.between(1, 3).mean()).sort_values(ascending=False)
    bal = ews_api._balance(cur)
    moved = cur.merge(batch[["customer_id", "tier"]], on="customer_id", suffixes=("", "_me"))
    as_at = live.clock if (live and source == "live" and live.sim and live.sim.day > 0) else f"{pd.Timestamp(batch.month.max()):%B %Y} month end"
    return dict(as_at=str(as_at)[:16].replace("T", " "), customers=int(len(cur)), watchlist=int(cur.tier.between(1, 3).sum()),
                watchlist_at_month_end=int(batch.tier.between(1, 3).sum()), tiers=cur.tier_name.value_counts().to_dict(),
                escalated_since_month_end=int((moved.tier > moved.tier_me).sum()),
                de_escalated_since_month_end=int((moved.tier < moved.tier_me).sum()),
                balance_at_risk_gbp=float(bal[cur.tier >= 2].sum()),
                top_triggers=[dict(name=names.get(k, k), customers=int(v)) for k, v in top.items()],
                top_regions=[dict(region=k, share=float(v)) for k, v in reg.head(3).items()],
                refix_shock_customers=int((cur.refix_shock_fwd.fillna(0) >= 0.15).sum()),
                pay_lean_in_collections=cur[cur.tier == 4].pay_lean.value_counts().to_dict(),
                limit_increase_eligible=int(cur.limit_and_activation.astype(str).str.startswith("Eligible").sum()))


def _customer_prompt(cid):
    c = ews_api.customer(cid)
    st = c["state"]
    st["customer_id"] = cid
    fired = [t["name"] for t in c["triggers"] if t["level"] in ("Amber", "Red")]
    facts = commentary.customer_facts(st, fired)
    facts["top_drivers"] = "; ".join(f"{d['feature']} ({d['family']})" for d in c["drivers"][:4] if d["contribution"] > 0)
    evs = [dict(day=e["sim_day"], source=e["source"], event=e["event_type"], detail=e["detail"]) for e in c["events"] if not e.get("routine")]
    notes = [dict(when=n["created_ts"], topic=n["topic"], text=n["note_text"]) for n in c["notes"][:3]]
    if notes:
        facts["recent_contact_notes"] = notes
    return commentary.build_customer(facts, evs)


def build(kind: str, id: str | None = None):
    """(prompt, fallback, purpose) for a commentary kind."""
    if kind in STORED:
        t = D.table(STORED[kind])
        fallback = t.commentary.iloc[-1] if t is not None and len(t) else "No commentary yet. Run the pipeline."
        if kind == "dq_commentary" and D.exists("ops.dq_results"):
            res = D.table("ops.dq_results")
            res = res[res.run_id == res.run_id.iloc[-1]]
            rem = D.table("ops.dq_remediation_log")
            rem = rem[rem.run_id == rem.run_id.iloc[-1]]
            p, fb = commentary.build_dq(dq.dq_summary_facts(res, rem))
            return p, fb, kind
        if kind == "model_comparison" and D.exists("gold.model_comparison"):
            res = D.table("gold.model_comparison")
            champ = res[res.is_champion].model.iloc[0]
            reason = t.reason.iloc[-1] if t is not None and "reason" in t else "Champion rule"
            p, fb = commentary.build_model_comparison(res, champ, reason)
            return p, fb, kind
        stored = _latest_prompt(kind)
        if stored:
            return stored["prompt"], fallback, kind
        if kind == "portfolio_summary":
            p, fb = commentary.build_portfolio(_portfolio_live_facts())
            return p, fb, kind
        raise HTTPException(404, "No prompt recorded yet. Run the pipeline.")
    if kind == "portfolio_live":
        p, fb = commentary.build_portfolio(_portfolio_live_facts())
        return p, fb, "portfolio_summary_live"
    if kind == "customer":
        p, fb = _customer_prompt(id)
        return p, fb, "customer_summary"
    if kind == "alert":
        (p, fb), _ = SVC["live"].alert_prompt(id)
        return p, fb, "live_alert_summary"
    raise HTTPException(400, f"Unknown kind {kind}")


@router.get("/stream")
def stream(kind: str, id: str | None = None):
    p, fb, purpose = build(kind, id)
    return _sse(_stream(p, fb, purpose))


@router.get("/prompt")
def prompt(kind: str, id: str | None = None):
    p, fb, purpose = build(kind, id)
    return dict(purpose=purpose, model=llm.model(), system=llm.SYSTEM, prompt=p, fallback=fb)


@router.get("/stored/{kind}")
def stored(kind: str):
    t = D.table(STORED.get(kind, ""))
    if t is None or not len(t):
        return None
    r = D.records(t.tail(1))[0]
    call = SVC["db"].one("SELECT model, served_by, latency_ms, input_tokens, output_tokens, ts FROM llm_calls WHERE purpose=? AND status='ok' "
                         "ORDER BY id DESC LIMIT 1", (kind,))
    return dict(r, call=call)


@router.get("/log")
def log(limit: int = 100):
    rows = SVC["db"].query("SELECT id, ts, purpose, model, served_by, status, latency_ms, first_token_ms, input_tokens, output_tokens, run_id, task, "
                           "substr(response, 1, 240) AS preview, error FROM llm_calls ORDER BY id DESC LIMIT ?", (limit,))
    stats = SVC["db"].one("SELECT COUNT(*) AS calls, SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
                          "AVG(latency_ms) AS avg_latency_ms, SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok FROM llm_calls")
    return dict(rows=rows, stats=stats, status=llm.status())


@router.get("/log/{call_id}")
def log_item(call_id: int):
    r = SVC["db"].one("SELECT * FROM llm_calls WHERE id=?", (call_id,))
    if not r:
        raise HTTPException(404)
    return r


# ---------------------------------------------------------------------------
# Ask AI: an analyst with a read-only SQL tool over the lakehouse
# ---------------------------------------------------------------------------
ANALYST_SYSTEM = """You are the analyst assistant inside Early Warning Studio, an early warning system (EWS) for a UK retail \
credit card book. Answer the user's question by querying the lakehouse with the run_sql tool (DuckDB SQL), \
then reply in plain British English.

Key tables (query DESCRIBE <table> if you need other columns):
- live_customers: the CURRENT position of every customer, including live intra-month events (October 2026). One row per customer. \
Columns include customer_id, persona, tier (0 Performing, 1 Monitor, 2 Watch, 3 Early intervention, 4 Collections, 5 Recoveries), \
tier_name, tier_reason, pd_6m, risk_band (A best to G worst), p_arrears_3m, p_forbearance_6m, propensity_to_pay, p_cant_pay, pay_lean \
("Can't pay" / "Won't pay" / "Unclear"), fired_triggers (comma-separated trigger ids), breadth, n_red, n_amber, util, dsr, dscr, \
headroom_gbp, credit_limit, region, segment, gross_income_annual, refix_shock_fwd, live_events, and the six decisions: \
limit_and_activation, pricing, exposure, offers_engagement, support, collections.
- gold.ews_decisions_latest: the same columns at September 2026 month end (batch).
- gold.ews_scores_monthly: one row per customer per month, April 2025 to September 2026 (month is a DATE), with pd_6m, tier, tier_name, \
movement, bad_6m (actual outcome where matured) and the features.
- gold.ews_triggers_fired: customer_id, month, trigger_id, trigger_name, dimension, severity (Amber/Red), value, threshold.
- ops.kri_thresholds: trigger_id, name, dimension, feature, direction, amber, red, threshold_source, use_cases.
- gold.model_comparison: model, dev_gini, gini (out of time), ks, brier, calibration_ratio, psi_vs_dev, is_champion.
- ops.monitoring_metrics: metric, value, amber, red, status; ops.dq_results: rule_id, description, status, records_failed, fail_rate.

Rules: query before you state a number, and quote numbers exactly as returned. Prefer one or two well-aimed queries. \
Keep the answer short: two to five sentences, then a compact markdown table (at most 10 rows) only if it helps. \
Say whether the figures are live or month end. Never reveal or guess anything about individuals beyond what the tables hold."""

TOOLS = [
    dict(name="run_sql", description="Run one read-only DuckDB SQL query against the EWS lakehouse and return up to 50 rows as JSON.",
         input_schema=dict(type="object", properties=dict(query=dict(type="string", description="A single SELECT, WITH or DESCRIBE statement")),
                           required=["query"], additionalProperties=False),
         eager_input_streaming=True),
]


def _run_tool(name, inp):
    if name != "run_sql" or not isinstance(inp, dict) or not isinstance(inp.get("query"), str):
        return "Error: run_sql needs a 'query' string.", True, None
    from server.platform_api import FORBIDDEN
    import re
    q = inp["query"].strip().rstrip(";")
    if not re.match(r"^\s*(select|with|describe|from)\b", q, re.I) or FORBIDDEN.search(re.sub(r"'[^']*'", "", q)):
        return "Error: only read-only SELECT, WITH or DESCRIBE queries are allowed.", True, None
    con = lh.connection()
    live = SVC.get("live")
    frame = live.frame() if live is not None else None
    if frame is None:
        frame = D.table("gold.ews_decisions_latest")
    if frame is not None:
        frame = frame.drop(columns=[c for c in ews_api.HIDDEN if c in frame.columns])
        con.register("live_customers", frame)
    try:
        df = con.execute(q).df()
    except Exception as e:
        return f"Error: {str(e).splitlines()[0][:400]}", True, None
    rows = D.records(df.head(50))
    payload = json.dumps(dict(row_count=len(df), columns=list(df.columns), rows=rows), default=str)
    return payload[:12000], False, dict(columns=list(df.columns), rows=rows[:10], row_count=len(df))


class AskBody(BaseModel):
    messages: list[dict]


@router.post("/ask")
def ask(body: AskBody):
    def gen():
        yield dict(type="meta", model=llm.model(), configured=llm.configured())
        if not llm.configured():
            yield dict(type="delta", text="The AI analyst needs an Anthropic API key. Add ANTHROPIC_API_KEY to the .env file and restart.")
            yield dict(type="done", source="template")
            return
        msgs = [dict(role=m["role"], content=m["content"]) for m in body.messages if m.get("content")]
        client = llm.client()
        t0 = time.time()
        tot_in = tot_out = 0
        live = SVC.get("live")
        if live is not None and live.sim is not None and live.sim.day > 0:
            as_at = f"live_customers is the live position as at {str(live.clock)[:16].replace('T', ' ')} (day {live.sim.day} of October 2026)."
        else:
            as_at = "live_customers currently equals the September 2026 month-end position (the live feed has not started)."
        system = f"{ANALYST_SYSTEM}\n\n{as_at}"
        for turn in range(8):
            kwargs = dict(model=llm.model(), max_tokens=8000, system=system, tools=TOOLS, messages=msgs,
                          output_config={"effort": "medium"})
            if llm.model() in llm._FALLBACK_MODELS:
                kwargs.update(betas=["server-side-fallback-2026-07-01"], extra_body={"fallbacks": "default"})
            try:
                with client.beta.messages.stream(**kwargs) as s:
                    for event in s:
                        if event.type == "content_block_delta" and event.delta.type == "text_delta":
                            yield dict(type="delta", text=event.delta.text)
                    final = s.get_final_message()
            except ValueError as e:  # unparseable streamed tool input
                yield dict(type="error", message=f"Tool input could not be parsed: {e}")
                return
            tot_in += final.usage.input_tokens
            tot_out += final.usage.output_tokens
            if final.stop_reason == "refusal":
                yield dict(type="delta", text="\n\nThe model declined this request.")
                break
            uses = [b for b in final.content if b.type == "tool_use"]
            if final.stop_reason == "max_tokens" or not uses:
                break
            msgs.append(dict(role="assistant", content=final.content))
            results = []
            for u in uses:
                yield dict(type="tool", name=u.name, input=u.input)
                out, is_err, preview = _run_tool(u.name, u.input)
                yield dict(type="tool_result", name=u.name, ok=not is_err, preview=preview, error=out if is_err else None)
                results.append(dict(type="tool_result", tool_use_id=u.id, content=out, is_error=is_err))
            msgs.append(dict(role="user", content=results))
            yield dict(type="delta", text="\n\n")
        SVC["db"].log_llm(dict(started_at=t0, purpose="ask_ai", model=llm.model(), served_by=final.model, status="ok",
                               latency_ms=int((time.time() - t0) * 1000), input_tokens=tot_in, output_tokens=tot_out,
                               prompt=json.dumps(body.messages[-1:], default=str)[:4000], system=system,
                               response="".join(b.text for b in final.content if b.type == "text")[:8000]))
        yield dict(type="done", source="llm", model=final.model, latency_ms=int((time.time() - t0) * 1000),
                   input_tokens=tot_in, output_tokens=tot_out)
    return _sse(gen())
