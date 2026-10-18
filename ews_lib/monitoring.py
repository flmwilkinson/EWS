"""Ongoing monitoring of the EWS: model performance, stability, triggers and watchlist.

Every metric has an amber and red appetite so the output reads as a RAG pack.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from ews_lib.models import psi

APPETITE = {  # metric: (amber, red, direction) - direction "high_bad" means higher values are worse
    "Score PSI vs development": (0.10, 0.25, "high_bad"),
    "Gini (matured month)": (0.70, 0.65, "low_bad"),
    "Gini drop vs development": (0.05, 0.10, "high_bad"),
    "Calibration ratio (predicted / actual)": (0.20, 0.35, "abs_dev_from_1"),
    "Bad capture by watchlist": (0.60, 0.45, "low_bad"),
    "Median lead time (months)": (2.0, 1.0, "low_bad"),
    "Watchlist precision (tier 2+)": (0.10, 0.05, "low_bad"),
    "Max feature CSI": (0.10, 0.25, "high_bad"),
}


def rag(metric, value):
    a, r, d = APPETITE[metric]
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "Grey"
    if d == "high_bad":
        return "Red" if value >= r else ("Amber" if value >= a else "Green")
    if d == "low_bad":
        return "Red" if value <= r else ("Amber" if value <= a else "Green")
    dev = abs(value - 1)
    return "Red" if dev >= r else ("Amber" if dev >= a else "Green")


def performance_by_month(scored, target="bad_6m"):
    rows = []
    for m, g in scored[scored[target].notna()].groupby("month"):
        if g[target].nunique() < 2:
            continue
        auc = roc_auc_score(g[target], g.pd_6m)
        rows.append(dict(month=m, n=len(g), bads=int(g[target].sum()), gini=round(2 * auc - 1, 4),
                         predicted_bad_rate=round(g.pd_6m.mean(), 5), actual_bad_rate=round(g[target].mean(), 5)))
    return pd.DataFrame(rows)


def calibration_by_band(scored, month, target="bad_6m"):
    g = scored[(scored.month == month) & scored[target].notna()]
    return g.groupby("risk_band").agg(n=(target, "size"), predicted=("pd_6m", "mean"),
                                      actual=(target, "mean")).reset_index()


def stability(dev_scores, scored, features, dev_frame):
    rows = []
    for m, g in scored.groupby("month"):
        rows.append(dict(month=m, score_psi=round(psi(dev_scores, g.pd_6m.values), 4)))
    latest = scored[scored.month == scored.month.max()]
    csi = []
    macro = {"unemp_rate", "unemp_chg_12m", "unemp_fwd_12m", "hpi_yoy", "bank_rate", "util_x_unemp", "tenure_months"}
    for f in features:
        if f in macro:  # region-level series move by design; tracked in the macro pack instead
            continue
        a, b = dev_frame[f].dropna().values, latest[f].dropna().values
        if len(np.unique(a)) < 5 or len(b) < 100:
            continue
        csi.append(dict(feature=f, csi=round(psi(a, b), 4)))
    return pd.DataFrame(rows), pd.DataFrame(csi).sort_values("csi", ascending=False)


def early_warning_effectiveness(full, panel, horizon_months=6):
    """Did the EWS flag customers before their first missed payment?

    For customers who reach 60+ DPD in the scored window, find the start of that
    arrears episode (first missed payment) and look back 12 months for a
    pre-delinquency flag. Collections tiers do not count: being in arrears is
    not an early warning.
    """
    p = panel[["customer_id", "month", "cycles_now", "account_status"]].sort_values(["customer_id", "month"])
    p["is_bad"] = (p.cycles_now >= 2) | (p.account_status == "Charged off")
    start = full.month.min() + pd.DateOffset(months=horizon_months)
    fb = p[p.is_bad].groupby("customer_id").month.min()
    fb = fb[fb >= start]
    pre = full[full.tier.between(2, 3)][["customer_id", "month"]]
    pre_any = full[full.tier.between(1, 3)][["customer_id", "month"]]
    rows = []
    for cid, bm in fb.items():
        hist = p[(p.customer_id == cid) & (p.month <= bm)]
        clean = hist[hist.cycles_now == 0]
        arrears_start = (clean.month.max() + pd.DateOffset(months=1)) if len(clean) else hist.month.min()
        win = lambda f: f[(f.customer_id == cid) & (f.month < arrears_start) & (f.month >= arrears_start - pd.DateOffset(months=12))]
        w, wa = win(pre), win(pre_any)
        lead = lambda w: ((arrears_start.year - w.month.min().year) * 12 + arrears_start.month - w.month.min().month) if len(w) else 0
        rows.append(dict(customer_id=cid, bad_month=bm, arrears_start=arrears_start, lead_months=lead(w), lead_months_any=lead(wa)))
    lt = pd.DataFrame(rows)
    cap = lt[lt.lead_months > 0]
    entries = full[(full.tier.between(2, 3)) & (full.prev_tier.fillna(0) < 2) & full.bad_6m.notna()]
    precision = entries.bad_6m.mean() if len(entries) else np.nan
    return dict(n_bads=len(lt), capture=len(cap) / max(len(lt), 1),
                capture_incl_monitor=float((lt.lead_months_any > 0).mean()) if len(lt) else 0.0,
                median_lead=float(cap.lead_months.median()) if len(cap) else 0.0,
                lead_distribution=lt.lead_months.value_counts().sort_index().to_dict(),
                precision=float(precision) if precision == precision else None), lt


def trigger_rates(fired, full):
    base = full.groupby("month").customer_id.nunique().rename("customers")
    fr = fired.groupby(["month", "trigger_id", "trigger_name", "severity"]).customer_id.nunique().rename("hits").reset_index()
    fr = fr.merge(base, on="month")
    fr["hit_rate"] = (fr.hits / fr.customers).round(5)
    return fr


def watchlist_flows(full):
    g = full.groupby(["month", "tier_name"]).customer_id.nunique().rename("customers").reset_index()
    mv = full.groupby(["month", "movement"]).customer_id.nunique().rename("customers").reset_index()
    return g, mv


def metrics_table(champion, ref_gini, perf, stab, csi, ew, calib_ratio, month):
    """ref_gini: out-of-time Gini recorded at model approval."""
    latest_perf = perf.iloc[-1] if len(perf) else None
    vals = {
        "Score PSI vs development": float(stab.iloc[-1].score_psi),
        "Gini (matured month)": float(latest_perf.gini) if latest_perf is not None else np.nan,
        "Gini drop vs development": float(ref_gini - latest_perf.gini) if latest_perf is not None else np.nan,
        "Calibration ratio (predicted / actual)": float(calib_ratio),
        "Bad capture by watchlist": float(ew["capture"]),
        "Median lead time (months)": float(ew["median_lead"]),
        "Watchlist precision (tier 2+)": float(ew["precision"]) if ew["precision"] is not None else np.nan,
        "Max feature CSI": float(csi.csi.max()) if len(csi) else 0.0,
    }
    rows = []
    for k, v in vals.items():
        a, r, _ = APPETITE[k]
        rows.append(dict(month=month, model=champion, metric=k, value=round(v, 4), amber=a, red=r, status=rag(k, v)))
    return pd.DataFrame(rows)
