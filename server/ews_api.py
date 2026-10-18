"""Data for the EWS pages: overview, watchlist, customer 360, triggers, decisioning, DQ, models, monitoring.

"Current" figures come from the real-time service when it is loaded (month-end
batch position plus any October events played so far); history comes from the
monthly batch tables.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from ews_lib import actions, models, registry, runtime, triggers
from ews_lib.config import TIERS
from ews_lib.features import FEATURE_DICTIONARY, FEATURE_FAMILY
from ews_lib.monitoring import APPETITE
from server import data as D

router = APIRouter(prefix="/api/ews")
SVC = {}  # filled by app.py: live service
HIDDEN = {"shock", "shock_day", "clean_days", "due_day", "pay_day", "last_event"}  # simulator internals, never shown
TIER_ORDER = [TIERS[k] for k in sorted(TIERS)]
FEATURE_DESC = {f[0]: f[3] for f in FEATURE_DICTIONARY}
CUSTOMER_LEVEL_EXCLUDE = {"T23", "T25"}  # regional macro: amplifiers, not customer evidence


def need(name):
    df = D.table(name)
    if df is None:
        raise HTTPException(404, f"{name} has not been built yet. Run the pipeline.")
    return df


def current():
    """(frame, source) for the latest position of every customer."""
    live = SVC.get("live")
    f = live.frame() if live is not None else None
    if f is not None:
        batch = need("gold.ews_decisions_latest")
        gone = batch[(batch.tier >= 5) & ~batch.customer_id.isin(f.customer_id)]  # charged off: no live events, still on the books
        f = pd.concat([f, gone], ignore_index=True) if len(gone) else f
        return f.drop(columns=[c for c in HIDDEN if c in f.columns]), "live"
    return need("gold.ews_decisions_latest"), "batch"


def _balance(df):
    if "statement_balance" in df and "util" in df:
        # live events move utilisation; balance follows it
        return (df.util.clip(0, 1.5) * df.credit_limit).fillna(df.statement_balance).fillna(0)
    return pd.Series(0.0, index=df.index)


def _thr():
    return need("ops.kri_thresholds")


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
@router.get("/overview")
def overview():
    cur, source = current()
    batch = need("gold.ews_decisions_latest")
    thr = _thr()
    names = dict(zip(thr.trigger_id, thr["name"]))
    live = SVC.get("live")
    tiers_now = cur.tier.value_counts().reindex(range(6), fill_value=0)
    tiers_batch = batch.tier.value_counts().reindex(range(6), fill_value=0)
    bal = _balance(cur)
    kpis = dict(customers=int(len(cur)), watchlist=int(cur.tier.between(1, 3).sum()),
                watchlist_batch=int(batch.tier.between(1, 3).sum()),
                tiers={TIERS[k]: int(v) for k, v in tiers_now.items()},
                tiers_batch={TIERS[k]: int(v) for k, v in tiers_batch.items()},
                balance_at_risk=float(bal[cur.tier >= 2].sum()), total_balance=float(bal.sum()),
                expected_bad_balance=float((cur.pd_6m * bal).sum()), avg_pd=float(cur.pd_6m.mean()),
                limit_increase_eligible=int(cur.limit_and_activation.astype(str).str.startswith("Eligible").sum()),
                proactive_contact=int(cur.support.astype(str).str.startswith("Proactive").sum()),
                specialist_support=int(cur.support.astype(str).str.contains("specialist", case=False).sum()))
    tiers_hist = D.table("ops.monitoring_watchlist_tiers")
    moves = D.table("ops.monitoring_watchlist_moves")
    scores = D.sql("SELECT month, AVG(pd_6m) AS predicted, AVG(bad_6m) AS actual, COUNT(*) AS n FROM gold.ews_scores_monthly GROUP BY 1 ORDER BY 1")
    # current trigger hits (customer level)
    hits = cur.fired_triggers.dropna().astype(str).str.split(",").explode()
    hits = hits[hits.astype(bool) & ~hits.isin(CUSTOMER_LEVEL_EXCLUDE)]
    top = hits.value_counts().head(12)
    dims = dict(zip(thr.trigger_id, thr.dimension))
    region = (cur.assign(watch=cur.tier.between(1, 3), ei=cur.tier == 3, coll=cur.tier >= 4, bal=bal)
              .groupby("region").agg(customers=("customer_id", "size"), watch=("watch", "mean"), early=("ei", "sum"),
                                     collections=("coll", "sum"), avg_pd=("pd_6m", "mean"), balance=("bal", "sum")).reset_index()
              .sort_values("watch", ascending=False))
    seg = (cur.assign(watch=cur.tier.between(1, 3)).groupby("segment").agg(customers=("customer_id", "size"), watch=("watch", "mean"),
                                                                            avg_pd=("pd_6m", "mean")).reset_index())
    bands = cur.risk_band.value_counts().reindex(list("ABCDEFG"), fill_value=0)
    region_tier = cur.groupby(["region", "tier_name"]).size().rename("n").reset_index()
    comm = D.table("gold.portfolio_commentary")
    as_at = pd.Timestamp(batch.month.max())
    return dict(
        source=source, as_at=as_at.strftime("%B %Y"), live=live.status_dict() if live else None, kpis=kpis,
        tier_trend=D.records(tiers_hist.assign(month=tiers_hist.month.map(D.month_label))) if tiers_hist is not None else [],
        moves_trend=D.records(moves.assign(month=moves.month.map(D.month_label))) if moves is not None else [],
        pd_trend=D.records(scores.assign(month=scores.month.map(D.month_label))),
        top_triggers=[dict(trigger_id=k, name=names.get(k, k), dimension=dims.get(k), customers=int(v)) for k, v in top.items()],
        regions=D.records(region), segments=D.records(seg),
        bands=[dict(band=b, customers=int(v)) for b, v in bands.items()],
        region_tier=D.records(region_tier),
        commentary=D.records(comm.tail(1))[0] if comm is not None and len(comm) else None)


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------
WL_COLS = ["customer_id", "persona", "tier", "tier_name", "tier_reason", "movement", "pd_6m", "risk_band", "p_arrears_3m",
           "p_forbearance_6m", "propensity_to_pay", "p_cant_pay", "pay_lean", "breadth", "n_red", "n_amber", "fired_triggers",
           "util", "dsr", "credit_limit", "region", "segment", "support", "collections", "limit_and_activation", "live_events",
           "top_drivers", "driver_1"]


@router.get("/watchlist")
def watchlist(tier: str = "1,2,3", region: str = "", segment: str = "", lean: str = "", band: str = "", trigger: str = "",
              q: str = "", changed: bool = False, sort: str = "pd_6m", desc: bool = True, offset: int = 0, limit: int = 50):
    cur, source = current()
    batch = need("gold.ews_decisions_latest")[["customer_id", "tier_name"]].rename(columns={"tier_name": "tier_month_end"})
    df = cur.merge(batch, on="customer_id", how="left")
    df["balance"] = _balance(df)
    df["changed_since_month_end"] = df.tier_name != df.tier_month_end
    thr = _thr()
    names = dict(zip(thr.trigger_id, thr["name"]))
    base = df
    tiers = [int(t) for t in D.parse_list(tier)]
    if tiers:
        df = df[df.tier.isin(tiers)]
    for col, val in [("region", region), ("segment", segment), ("pay_lean", lean), ("risk_band", band)]:
        vals = D.parse_list(val)
        if vals:
            df = df[df[col].isin(vals)]
    if trigger:
        df = df[df.fired_triggers.fillna("").str.split(",").apply(lambda xs: trigger in xs)]
    if changed:
        df = df[df.changed_since_month_end]
    if q:
        ql = q.lower()
        df = df[df.customer_id.str.lower().str.contains(ql) | df.persona.fillna("").str.lower().str.contains(ql)]
    total = len(df)
    if sort in df.columns:
        df = df.sort_values(sort, ascending=not desc, na_position="last")

    def facet(col, frame):
        return frame[col].fillna("(none)").value_counts().to_dict()

    page = df.iloc[offset: offset + limit].copy()
    page["trigger_names"] = page.fired_triggers.fillna("").map(
        lambda s: [names.get(t, t) for t in s.split(",") if t and t not in CUSTOMER_LEVEL_EXCLUDE][:6])
    cols = WL_COLS + ["balance", "tier_month_end", "changed_since_month_end", "trigger_names"]
    filt = df  # facets reflect current filters except their own dimension would be ideal; keep simple and useful
    return dict(source=source, total=total, offset=offset, limit=limit, rows=D.records(page, cols),
                facets=dict(tier={TIERS[int(k)]: int(v) for k, v in base.tier.value_counts().sort_index().items()},
                            region=facet("region", filt), segment=facet("segment", filt), pay_lean=facet("pay_lean", filt),
                            risk_band=facet("risk_band", filt)),
                totals=dict(balance=float(df.balance.sum()), avg_pd=float(df.pd_6m.mean()) if total else 0.0,
                            changed=int(df.changed_since_month_end.sum())))


# ---------------------------------------------------------------------------
# Customer 360
# ---------------------------------------------------------------------------
@router.get("/customer/{cid}")
def customer(cid: str):
    live = SVC.get("live")
    lv = live.customer(cid) if live is not None else None
    batch = need("gold.ews_decisions_latest")
    brow = batch[batch.customer_id == cid]
    if lv is None and brow.empty:
        raise HTTPException(404, f"Customer {cid} not found")
    state = lv["state"] if lv else {k: runtime.jsonable(v) for k, v in brow.iloc[0].to_dict().items()}
    for k in HIDDEN:
        state.pop(k, None)
    thr = _thr()
    # every trigger evaluated against the current values, including near misses
    trig = []
    for t in thr.itertuples():
        v = state.get(t.feature)
        if v is None or (isinstance(v, float) and np.isnan(v)):
            level = None
        else:
            fires = (lambda x, th: x >= th) if t.direction == ">=" else (lambda x, th: x <= th)
            level = "Red" if fires(v, t.red) else ("Amber" if fires(v, t.amber) else "None")
        trig.append(dict(trigger_id=t.trigger_id, name=t.name, dimension=t.dimension, kind=t.kind, feature=t.feature,
                         description=FEATURE_DESC.get(t.feature), direction=t.direction, value=runtime.jsonable(v),
                         amber=runtime.jsonable(t.amber), red=runtime.jsonable(t.red), level=level, use_cases=t.use_cases))
    # model drivers for the current position
    drivers = []
    b = live.bundles["pd_6m"] if live is not None and live.bundles is not None else D.champion_bundle()
    if b is not None:
        row = pd.DataFrame([state])
        try:
            for f in b["features"]:
                row[f] = pd.to_numeric(row[f], errors="coerce") if f in row else np.nan
            c = models.contributions(b, row).iloc[0]
            for f, v in c.sort_values(key=np.abs, ascending=False).head(14).items():
                drivers.append(dict(feature=f, family=FEATURE_FAMILY.get(f), description=FEATURE_DESC.get(f),
                                    value=runtime.jsonable(row[f].iloc[0]), contribution=round(float(v), 4)))
        except Exception:
            drivers = []
    hist = D.sql("SELECT month, pd_6m, tier, tier_name, risk_band, util, dsr, bureau_score, cycles_now, breadth, n_red, n_amber, "
                 "p_arrears_3m, salary_missing_3m, od_days, searches_3m, movement, tier_reason FROM gold.ews_scores_monthly "
                 "WHERE customer_id = ? ORDER BY month", [cid])
    card = D.sql("SELECT month, credit_limit, statement_balance, payment_amount, minimum_due, cash_advance_amt, missed_payment_flag, "
                 "utilisation, cycles_delinquent, account_status FROM silver.card_monthly WHERE customer_id = ? ORDER BY month", [cid])
    ca = D.sql("SELECT month, salary_credit_amt, other_income_credit_amt, overdraft_days, returned_dd_count, gambling_spend "
               "FROM silver.current_account_monthly WHERE customer_id = ? ORDER BY month", [cid])
    bu = D.sql("SELECT month, bureau_score, total_external_debt, searches_this_month, worst_external_status FROM silver.bureau_monthly "
               "WHERE customer_id = ? ORDER BY month", [cid])
    notes = D.sql("SELECT created_ts, channel, direction, note_text, sentiment, topic FROM silver.contact_notes_scored "
                  "WHERE customer_id = ? ORDER BY created_ts DESC LIMIT 12", [cid])
    fired_hist = D.sql("SELECT month, trigger_id, severity FROM gold.ews_triggers_fired WHERE customer_id = ?", [cid])
    prof = D.sql("SELECT * FROM silver.customers WHERE customer_id = ?", [cid])
    acct = D.sql("SELECT product, open_date, credit_limit_at_open, apr_pct, payment_method FROM silver.card_accounts WHERE customer_id = ?", [cid])
    hold = D.sql("SELECT product_type, rate_type, original_rate_pct, fix_end_month, balance, monthly_payment FROM silver.product_holdings "
                 "WHERE customer_id = ?", [cid])
    summ = D.table("gold.customer_summaries")
    persona = D.table("bronze.personas")
    p = persona[persona.customer_id == cid] if persona is not None else pd.DataFrame()
    for frame in (hist, card, ca, bu, fired_hist):
        if len(frame):
            frame["month"] = frame.month.map(D.month_label)
    return dict(
        customer_id=cid, source="live" if lv else "batch", state=state,
        profile=D.records(prof)[0] if len(prof) else {}, account=D.records(acct)[0] if len(acct) else {},
        holdings=D.records(hold), persona=D.records(p)[0] if len(p) else None,
        month_end=D.records(brow, ["tier_name", "pd_6m", "risk_band", "tier_reason", "top_drivers"])[0] if len(brow) else None,
        triggers=trig, drivers=drivers,
        decisions={u: state.get(u) for u in actions.USE_CASES}, use_case_labels=actions.USE_CASE_LABELS,
        history=D.records(hist), card=D.records(card), current_account=D.records(ca), bureau=D.records(bu),
        notes=D.records(notes), fired_history=D.records(fired_hist),
        events=lv["events"] if lv else [], alerts=lv["alerts"] if lv else [],
        summaries=D.records(summ[summ.customer_id == cid]) if summ is not None else [])


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------
@router.get("/triggers")
def triggers_page():
    thr = _thr()
    eff = D.table("ops.trigger_effectiveness")
    cur, source = current()
    hits = cur.fired_triggers.dropna().astype(str).str.split(",").explode()
    now = hits[hits.astype(bool)].value_counts().to_dict()
    rates = D.table("ops.monitoring_trigger_rates")
    out = []
    for t in thr.to_dict("records"):
        e = eff[eff.trigger_id == t["trigger_id"]] if eff is not None else pd.DataFrame()
        ea, er = e[e.severity == "Amber"], e[e.severity == "Red"]
        trend = []
        if rates is not None:
            r = rates[(rates.trigger_id == t["trigger_id"])].groupby("month").hits.sum().sort_index()
            trend = [int(x) for x in r.values]
        out.append({**{k: runtime.jsonable(v) for k, v in t.items()}, "description": FEATURE_DESC.get(t["feature"]),
                    "lift_amber": D.num(ea.lift.iloc[0], None) if len(ea) else None,
                    "lift_red": D.num(er.lift.iloc[0], None) if len(er) else None,
                    "capture_amber": D.num(ea.capture_rate.iloc[0], None) if len(ea) else None,
                    "hit_rate_amber": D.num(ea.hit_rate.iloc[0], None) if len(ea) else None,
                    "bad_rate_amber": D.num(ea.bad_rate_when_fired.iloc[0], None) if len(ea) else None,
                    "firing_now": int(now.get(t["trigger_id"], 0)), "trend": trend})
    base = float(thr.base_bad_rate.iloc[0]) if "base_bad_rate" in thr else None
    return dict(source=source, base_bad_rate=base, triggers=out)


@router.get("/triggers/{tid}")
def trigger_detail(tid: str):
    thr = _thr()
    t = thr[thr.trigger_id == tid]
    if t.empty:
        raise HTTPException(404, tid)
    t = t.iloc[0]
    cur, _ = current()
    firing = cur[cur.fired_triggers.fillna("").str.split(",").apply(lambda xs: tid in xs)].sort_values("pd_6m", ascending=False)
    dist = None
    if t.feature in cur.columns:
        x = pd.to_numeric(cur[t.feature], errors="coerce").dropna()
        if len(x) and x.nunique() > 1:
            lo, hi = np.nanquantile(x, [0.005, 0.995])
            lo, hi = min(lo, float(t.amber), float(t.red)), max(hi, float(t.amber), float(t.red))
            counts, edges = np.histogram(x.clip(lo, hi), bins=30, range=(lo, hi))
            dist = dict(counts=counts.tolist(), edges=[round(float(e), 4) for e in edges])
    return dict(trigger={k: runtime.jsonable(v) for k, v in t.to_dict().items()}, description=FEATURE_DESC.get(t.feature),
                firing=len(firing), distribution=dist,
                customers=D.records(firing, ["customer_id", "persona", "tier_name", "pd_6m", "risk_band", t.feature, "region", "segment",
                                             "fired_triggers"], limit=100))


# ---------------------------------------------------------------------------
# Decisioning
# ---------------------------------------------------------------------------
@router.get("/decisions")
def decisions():
    cur, source = current()
    cur = cur.copy()
    cur["balance"] = _balance(cur)
    uses = []
    for u in actions.USE_CASES:
        g = cur.groupby(u).agg(customers=("customer_id", "size"), balance=("balance", "sum"), avg_pd=("pd_6m", "mean"),
                               headroom=("headroom_gbp", "sum")).reset_index().rename(columns={u: "action"})
        g = g.sort_values("customers", ascending=False)
        uses.append(dict(key=u, label=actions.USE_CASE_LABELS[u], actions=D.records(g)))
    matrix = []
    for t in TIER_ORDER:
        sub = cur[cur.tier_name == t]
        if not len(sub):
            continue
        matrix.append(dict(tier=t, customers=int(len(sub)),
                           **{u: (sub[u].value_counts().index[0] if sub[u].notna().any() else None) for u in actions.USE_CASES}))
    coll = cur[cur.tier == 4]
    lean = cur[cur.tier >= 3].groupby(["tier_name", "pay_lean"]).size().rename("customers").reset_index()
    eligible = cur[cur.limit_and_activation.astype(str).str.startswith("Eligible")]
    risky_headroom = cur[cur.tier.between(2, 3)].headroom_gbp.sum()
    return dict(source=source, use_cases=uses, matrix=matrix, labels=actions.USE_CASE_LABELS,
                pay_lean=D.records(lean),
                highlights=dict(limit_increase_customers=int(len(eligible)), limit_increase_avg_pd=float(eligible.pd_6m.mean()) if len(eligible) else 0,
                                headroom_watch_ei=float(risky_headroom), collections=int(len(coll)),
                                collections_cant_pay=int((coll.pay_lean == "Can't pay").sum()),
                                collections_wont_pay=int((coll.pay_lean == "Won't pay").sum()),
                                suppressed=int(cur.offers_engagement.astype(str).str.startswith("Suppress").sum()),
                                targeted=int(cur.offers_engagement.astype(str).str.startswith("Target").sum())))


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------
@router.get("/dq")
def dq_page():
    res = need("ops.dq_results")
    runs = res.groupby("run_id").agg(ts=("run_ts", "max"), rules=("rule_id", "size"), passed=("status", lambda s: int((s == "PASS").sum())),
                                     failed=("status", lambda s: int((s == "FAIL").sum()))).reset_index().sort_values("ts")
    last = res[res.run_id == runs.run_id.iloc[-1]]
    rem = D.table("ops.dq_remediation_log")
    rem = rem[rem.run_id == rem.run_id.iloc[-1]] if rem is not None and len(rem) else rem
    q = D.table("silver.dq_quarantine")
    quarantine = q.groupby(["source_table", "rule_id", "reason"]).size().rename("rows").reset_index() if q is not None else None
    comm = D.table("ops.dq_commentary")
    expl = D.table("ops.dq_rule_explanations")
    tables = []
    for t in ["customers", "card_accounts", "card_monthly", "current_account_monthly", "bureau_monthly", "macro_regional_monthly",
              "contact_notes", "collections_events", "product_holdings", "other_product_monthly", "business_monthly"]:
        b, s = D.version(f"bronze.{t}"), D.version(f"silver.{t}")
        bh = D.lh.history(f"bronze.{t}")[0] if b else None
        sh = D.lh.history(f"silver.{t}")[0] if s else None
        tables.append(dict(table=t, bronze_rows=bh["rows"] if bh else None, silver_rows=sh["rows"] if sh else None,
                           loaded_at=bh["ts"] if bh else None))
    return dict(results=D.records(last), runs=D.records(runs), remediation=D.records(rem), quarantine=D.records(quarantine),
                commentary=D.records(comm.tail(1))[0] if comm is not None and len(comm) else None,
                explanations=D.records(expl), tables=tables)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
@router.get("/models")
def models_page():
    comp = need("gold.model_comparison")
    comm = D.table("gold.model_comparison_commentary")
    reg = [m for m in registry.list_models()]
    return dict(comparison=D.records(comp), roc=D.records(D.table("gold.model_roc")),
                deciles=D.records(D.table("gold.model_deciles")), importance=D.records(D.table("gold.feature_importance")),
                scorecard=D.records(D.table("gold.scorecard_points")), secondary=D.records(D.table("gold.secondary_models")),
                commentary=D.records(comm.tail(1))[0] if comm is not None and len(comm) else None,
                registry=reg, serving=registry.serving_manifest())


# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------
@router.get("/monitoring")
def monitoring_page():
    m = need("ops.monitoring_metrics")
    last_run = m.run_id.iloc[-1]
    hist = m.groupby(["run_id", "metric"]).value.last().reset_index()
    perf = D.table("ops.monitoring_performance")
    stab = D.table("ops.monitoring_stability")
    csi = D.table("ops.monitoring_csi")
    calib = D.table("ops.monitoring_calibration")
    lead = D.table("ops.monitoring_lead_time")
    trans = D.table("ops.monitoring_transition_matrix")
    moves = D.table("ops.monitoring_watchlist_moves")
    comm = D.table("ops.monitoring_commentary")
    lead_dist = lead.lead_months.value_counts().sort_index() if lead is not None else pd.Series(dtype=int)
    for f in (perf, stab, moves):
        if f is not None:
            f["month_label"] = f.month.map(D.month_label)
    return dict(metrics=D.records(m[m.run_id == last_run]), appetite={k: list(v) for k, v in APPETITE.items()},
                history=D.records(hist), performance=D.records(perf), stability=D.records(stab), csi=D.records(csi),
                calibration=D.records(calib), lead_distribution=[dict(months=int(k), customers=int(v)) for k, v in lead_dist.items()],
                transitions=D.records(trans), moves=D.records(moves),
                commentary=D.records(comm.tail(1))[0] if comm is not None and len(comm) else None)


@router.get("/status")
def status():
    """Which parts of the platform have data (drives empty states in the UI)."""
    need_tables = ["gold.ews_decisions_latest", "gold.model_comparison", "ops.dq_results", "ops.monitoring_metrics"]
    return {t: D.exists(t) for t in need_tables}
