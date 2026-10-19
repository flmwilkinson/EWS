"""Compare two pipeline runs: what changed in the code, and what changed in the results.

When a run finishes, `snapshot()` records the state of the system after it (model performance, the watchlist,
early warning performance, data quality) in runs.summary. Snapshots are kept in the metadata database, so runs
can still be compared after the lakehouse has been reset. Each task run also records a hash of the notebook it
executed (task_runs.code_hash, content in code_blobs), which gives the code diff between two runs.
"""
from __future__ import annotations

import difflib
import json

from ews_lib.lakehouse import lh
from server.config import TASKS

TIER_ORDER = ["Monitor", "Watch", "Early intervention", "Collections", "Recoveries"]
WATCHLIST = ["Monitor", "Watch", "Early intervention"]  # tiers 1-3; collections and recoveries are counted separately
MONITORING = {  # metric in ops.monitoring_metrics -> (label, format, higher is better)
    "Bad capture by watchlist": ("Bad customers flagged before 60+ days past due", "pct", True),
    "Median lead time (months)": ("Median warning lead time (months)", "dec1", True),
    "Watchlist precision (tier 2+)": ("Watchlist precision (Watch or worse)", "pct", True),
    "Gini (matured month)": ("Gini, latest matured month", "dec3", True),
    "Score PSI vs development": ("Score stability (PSI vs development)", "dec3", False),
}


def _read(name):
    try:
        return lh.read(name) if lh.exists(name) else None
    except Exception:
        return None


def snapshot() -> dict:
    """Key results as they stand now (called when a run finishes)."""
    out = {}
    mc = _read("gold.model_comparison")
    if mc is not None and len(mc):
        champ = mc[mc.is_champion]
        out["models"] = dict(champion=champ.model.iloc[0] if len(champ) else None,
                             rows=mc[["model", "n_features", "dev_gini", "gini", "ks", "calibration_ratio", "is_champion"]]
                             .to_dict("records"))
    dec = _read("gold.ews_decisions_latest")
    if dec is not None and "tier_name" in dec:
        counts = dec.tier_name.value_counts().to_dict()
        out["watchlist"] = dict(customers=int(len(dec)), on_watchlist=int(sum(counts.get(t, 0) for t in WATCHLIST)),
                                tiers={t: int(counts.get(t, 0)) for t in TIER_ORDER})
    mm = _read("ops.monitoring_metrics")
    if mm is not None and len(mm):
        last = mm[mm.month == mm.month.max()]
        out["monitoring"] = {r.metric: float(r.value) for r in last.itertuples() if r.metric in MONITORING}
    dq = _read("ops.dq_results")
    if dq is not None and len(dq):
        out["dq"] = dict(rules=int(len(dq)), failed=int((dq.status == "FAIL").sum()),
                         critical_failed=int(((dq.status == "FAIL") & (dq.severity == "Critical")).sum()),
                         records_failed=int(dq.records_failed.sum()))
    return out


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------
def _row(label, a, b, fmt="int", better=None, group=None):
    delta = (b - a) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
    return dict(group=group, label=label, a=a, b=b, delta=delta, fmt=fmt, better=better)


def _results(sa: dict, sb: dict) -> list[dict]:
    rows = []
    ma, mb = sa.get("models") or {}, sb.get("models") or {}
    if ma or mb:
        g = "Models · out-of-time Gini"
        rows.append(_row("Champion model", ma.get("champion"), mb.get("champion"), "text", group=g))
        by_a = {r["model"]: r for r in ma.get("rows", [])}
        by_b = {r["model"]: r for r in mb.get("rows", [])}
        names = [r["model"] for r in sorted(mb.get("rows") or ma.get("rows", []), key=lambda r: -(r.get("gini") or 0))]
        for n in names:
            rows.append(_row(n, by_a.get(n, {}).get("gini"), by_b.get(n, {}).get("gini"), "dec3", True, g))
        champ = mb.get("champion") or ma.get("champion")
        rows.append(_row("Features used by the champion", by_a.get(champ, {}).get("n_features"),
                         by_b.get(champ, {}).get("n_features"), "int", group=g))
    wa, wb = sa.get("watchlist") or {}, sb.get("watchlist") or {}
    if wa or wb:
        g = "Watchlist · card customers"
        rows.append(_row("On the watchlist", wa.get("on_watchlist"), wb.get("on_watchlist"), group=g))
        for t in TIER_ORDER:
            rows.append(_row(t, (wa.get("tiers") or {}).get(t), (wb.get("tiers") or {}).get(t), group=g))
    oa, ob = sa.get("monitoring") or {}, sb.get("monitoring") or {}
    if oa or ob:
        g = "Early warning performance"
        for metric, (label, fmt, up) in MONITORING.items():
            if metric in oa or metric in ob:
                rows.append(_row(label, oa.get(metric), ob.get(metric), fmt, up, g))
    da, db_ = sa.get("dq") or {}, sb.get("dq") or {}
    if da or db_:
        g = "Data quality"
        rows.append(_row("Rules failed", da.get("failed"), db_.get("failed"), "int", False, g))
        rows.append(_row("Critical rules failed", da.get("critical_failed"), db_.get("critical_failed"), "int", False, g))
        rows.append(_row("Records failing a rule", da.get("records_failed"), db_.get("records_failed"), "int", False, g))
    return rows


def _code(db, a: dict, b: dict) -> list[dict]:
    ta = {t["task_key"]: t for t in db.query("SELECT task_key, code_hash FROM task_runs WHERE run_id=?", (a["run_id"],))}
    tb = {t["task_key"]: t for t in db.query("SELECT task_key, code_hash FROM task_runs WHERE run_id=?", (b["run_id"],))}
    out = []
    for t in TASKS:
        ha, hb = (ta.get(t["key"]) or {}).get("code_hash"), (tb.get(t["key"]) or {}).get("code_hash")
        if not ha or not hb or ha == hb:
            continue
        blobs = {r["hash"]: r["content"] for r in db.query("SELECT hash, content FROM code_blobs WHERE hash IN (?,?)", (ha, hb))}
        if ha not in blobs or hb not in blobs:
            continue
        diff = list(difflib.unified_diff(blobs[ha].splitlines(), blobs[hb].splitlines(), lineterm="", n=1))[2:]
        lines = [dict(kind="add" if l.startswith("+") else "del" if l.startswith("-") else "hunk" if l.startswith("@@") else "ctx",
                      text=l) for l in diff[:80]]
        out.append(dict(task=t["key"], title=t["title"], notebook=t["notebook"],
                        added=sum(1 for l in diff if l.startswith("+")), removed=sum(1 for l in diff if l.startswith("-")),
                        lines=lines))
    return out


def _tasks(db, a: dict, b: dict) -> list[dict]:
    q = "SELECT task_key, status, duration, rows_written, llm_calls FROM task_runs WHERE run_id=?"
    ta = {t["task_key"]: t for t in db.query(q, (a["run_id"],))}
    tb = {t["task_key"]: t for t in db.query(q, (b["run_id"],))}
    return [dict(key=t["key"], title=t["title"], a=ta.get(t["key"]), b=tb.get(t["key"])) for t in TASKS
            if t["key"] in ta or t["key"] in tb]


def _run_public(r: dict) -> dict:
    return {k: r.get(k) for k in ("run_id", "scope", "status", "started_at", "finished_at", "duration", "n_tasks", "n_ok",
                                  "rows_written", "llm_calls", "trigger", "triggered_by")}


def compare(db, run_a: str, run_b: str) -> dict:
    a = db.one("SELECT * FROM runs WHERE run_id=?", (run_a,))
    b = db.one("SELECT * FROM runs WHERE run_id=?", (run_b,))
    if not a or not b:
        raise KeyError("run not found")
    if a["started_at"] > b["started_at"]:  # A is always the earlier run (the baseline)
        a, b = b, a
    sa, sb = json.loads(a.get("summary") or "{}"), json.loads(b.get("summary") or "{}")
    return dict(a=_run_public(a), b=_run_public(b), has_summary=dict(a=bool(sa), b=bool(sb)),
                code=_code(db, a, b), results=_results(sa, sb), tasks=_tasks(db, a, b))
