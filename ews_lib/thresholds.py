"""Trigger catalogue and threshold seeding.

Each trigger has a policy (expert) amber/red threshold. For continuous KRIs the
seeding step also proposes data-calibrated thresholds: the most lenient value at
which the 6-month bad rate among triggered customers reaches a target multiple
of the portfolio bad rate, subject to a cap on how many customers it fires for.
Both values are stored so a model risk reviewer can see the difference.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

# trigger_id, name, dimension, kind, source feature, direction, policy amber, policy red, calibrate?, use cases
CATALOGUE = [
    ("T01", "High utilisation", "Card behaviour", "KRI", "util", ">=", 0.80, 0.95, True, "Limit, Exposure, Offers"),
    ("T02", "Utilisation rising fast", "Card behaviour", "KRI", "util_chg_3m", ">=", 0.20, 0.35, True, "Limit, Exposure, Support"),
    ("T03", "Minimum payments only", "Card behaviour", "KRI", "min_pay_streak", ">=", 3, 6, False, "Support, Pricing"),
    ("T04", "Payment ratio falling", "Card behaviour", "KRI", "pay_ratio_chg_3m", "<=", -0.15, -0.30, True, "Support, Limit"),
    ("T05", "Cash advance use", "Card behaviour", "KRI", "cash_adv_months_3m", ">=", 1, 2, False, "Exposure, Support"),
    ("T06", "Over limit", "Card behaviour", "KRI", "overlimit_6m", ">=", 1, 2, False, "Exposure, Limit"),
    ("T07", "Returned card payment", "Card behaviour", "KRI", "returned_pay_6m", ">=", 1, 2, False, "Support, Collections"),
    ("T08", "Salary not received", "Cross-product", "KRI", "salary_missing_3m", ">=", 1, 2, False, "Support, Limit"),
    ("T09", "Income falling", "Cross-product", "KRI", "income_chg_vs_6m", "<=", -0.20, -0.40, True, "Support, Limit, Pricing"),
    ("T10", "Overdraft reliance", "Cross-product", "KRI", "od_days", ">=", 10, 20, True, "Support, Exposure"),
    ("T11", "Returned direct debits", "Cross-product", "KRI", "returned_dd_3m", ">=", 1, 3, False, "Support, Collections"),
    ("T12", "Arrears on another product with us", "Cross-product", "KRI", "other_arrears", ">=", 1, 2, False, "Exposure, Collections"),
    ("T13", "Gambling spend share", "Cross-product", "KRI", "gambling_share", ">=", 0.10, 0.25, True, "Support, Offers"),
    ("T14", "BNPL reliance", "Cross-product", "KRI", "bnpl_share", ">=", 0.05, 0.10, True, "Support, Limit"),
    ("T15", "Payday lender use", "Cross-product", "KRI", "payday_lender_3m", ">=", 1, 1, False, "Support, Limit"),
    ("T16", "Debt service ratio (DSR)", "Cross-product", "KRI", "dsr", ">=", 0.45, 0.60, True, "Approval, Limit, Pricing"),
    ("T17", "Business DSCR (sole traders)", "Cross-product", "KRI", "dscr", "<=", 1.25, 1.00, False, "Approval, Limit, Exposure"),
    ("T18", "Arrears with another lender", "Credit bureau", "KRI", "ext_worst_6m", ">=", 1, 2, False, "Limit, Exposure, Support"),
    ("T19", "Credit searches", "Credit bureau", "KRI", "searches_3m", ">=", 3, 6, True, "Limit, Offers"),
    ("T20", "External debt growth", "Credit bureau", "KRI", "ext_debt_chg_6m", ">=", 0.25, 0.50, True, "Limit, Exposure"),
    ("T21", "County court judgment", "Credit bureau", "KRI", "ccj_flag", ">=", 1, 1, False, "Collections, Exposure"),
    ("T22", "Bureau score deteriorating", "Credit bureau", "KRI", "bureau_score_chg_3m", "<=", -50, -100, True, "Limit, Pricing"),
    ("T23", "Regional unemployment rising", "Macroeconomic", "KRI", "unemp_chg_12m", ">=", 0.5, 1.0, False, "Limit, Exposure"),
    ("T24", "Fixed rate ending (payment shock)", "Forward-looking", "KRI", "refix_shock_fwd", ">=", 0.15, 0.30, False, "Support, Limit"),
    ("T25", "Regional unemployment outlook", "Forward-looking", "KRI", "unemp_fwd_12m", ">=", 0.75, 1.50, False, "Limit, Exposure"),
    ("S01", "Hardship language in contact", "Conduct and sentiment", "Sentiment", "hardship_notes_3m", ">=", 1, 2, False, "Support"),
    ("S02", "Vulnerability indicated", "Conduct and sentiment", "Sentiment", "vulnerability_notes_3m", ">=", 1, 1, False, "Support"),
    ("S03", "Refusal or dispute language", "Conduct and sentiment", "Sentiment", "refusal_notes_3m", ">=", 1, 2, False, "Collections"),
    ("S04", "Negative sentiment", "Conduct and sentiment", "Sentiment", "sentiment_3m", "<=", -0.30, -0.60, False, "Support"),
    ("C01", "In arrears (collections status)", "Collections", "Status", "cycles_now", ">=", 1, 2, False, "Collections"),
    ("C02", "Broken promise to pay", "Collections", "Status", "ptp_broken_3m", ">=", 1, 2, False, "Collections"),
    ("C03", "In forbearance", "Collections", "Status", "forbearance_active", ">=", 1, 1, False, "Support, Collections"),
    ("M01", "PD 6m high", "Model", "Model", "pd_6m", ">=", 0.05, 0.10, False, "Approval, Limit, Pricing, Exposure"),
    ("M02", "PD doubled in 3 months", "Model", "Model", "pd_ratio_3m", ">=", 2.0, 4.0, False, "Limit, Support"),
    ("M03", "Probability of arrears 3m", "Model", "Model", "p_arrears_3m", ">=", 0.10, 0.25, False, "Support"),
    ("M04", "Probability of forbearance 6m", "Model", "Model", "p_forbearance_6m", ">=", 0.05, 0.15, False, "Support"),
    ("M05", "Low propensity to pay", "Model", "Model", "propensity_to_pay", "<=", 0.40, 0.20, False, "Collections"),
    ("E01", "Persistent EWI status", "EWI status", "Status", "months_on_watch", ">=", 3, 6, False, "Limit, Exposure, Support"),
]
CATALOGUE_COLS = ["trigger_id", "name", "dimension", "kind", "feature", "direction", "policy_amber",
                  "policy_red", "calibrate", "use_cases"]


def catalogue_frame():
    return pd.DataFrame(CATALOGUE, columns=CATALOGUE_COLS)


def _fires(x, direction, thr):
    return (x >= thr) if direction == ">=" else (x <= thr)


def _nice(x):
    if x == 0 or not np.isfinite(x):
        return x
    mag = 10 ** math.floor(math.log10(abs(x)))
    step = mag / 2
    return round(round(x / step) * step, 6)


def seed_thresholds(panel, target="bad_6m", amber_lift=3.0, red_lift=6.0, amber_max_hit=0.06, red_max_hit=0.02):
    """Return the threshold table with policy, calibrated and selected values plus evidence."""
    df = panel[panel[target].notna()]
    base = df[target].mean()
    rows = []
    for t in CATALOGUE:
        tid, name, dim, kind, feat, direc, pa, pr, cal, uses = t
        rec = dict(zip(CATALOGUE_COLS, t))
        rec.update(base_bad_rate=base, calibrated_amber=None, calibrated_red=None)
        if cal and feat in df:
            x = df[feat]
            ok = x.notna()
            xs, ys = x[ok].values, df.loc[ok, target].values
            if direc == ">=":
                cands = np.unique(np.nanquantile(xs, np.linspace(0.50, 0.995, 120)))
            else:
                cands = np.unique(np.nanquantile(xs, np.linspace(0.005, 0.50, 120)))[::-1]
            amber = red = None
            # Materiality floor: a calibrated threshold must be at least half as severe as policy
            floor = 0.5 * pa
            cands = cands[cands >= floor] if direc == ">=" else cands[cands <= floor]
            for c in cands:
                f = _fires(xs, direc, c)
                hit = f.mean()
                if hit == 0 or f.sum() < 30:
                    continue
                lift = ys[f].mean() / base
                if amber is None and lift >= amber_lift and hit <= amber_max_hit:
                    amber = c
                if red is None and lift >= red_lift and hit <= red_max_hit:
                    red = c
            rec["calibrated_amber"] = _nice(amber) if amber is not None else None
            rec["calibrated_red"] = _nice(red) if red is not None else None
        sel_a = rec["calibrated_amber"] if rec["calibrated_amber"] is not None else pa
        sel_r = rec["calibrated_red"] if rec["calibrated_red"] is not None else pr
        # Red must be strictly more severe than amber. If calibration pushed amber past the
        # red value, fall back to policy red, or scale amber by the policy red / amber ratio.
        stricter = (lambda r_, a_: r_ > a_) if direc == ">=" else (lambda r_, a_: r_ < a_)
        if not stricter(sel_r, sel_a) and pa != pr:
            sel_r = pr if stricter(pr, sel_a) else _nice(sel_a * pr / pa)
        rec["amber"] = sel_a
        rec["red"] = sel_r
        rec["threshold_source"] = "Calibrated" if rec["calibrated_amber"] is not None else "Policy"
        rows.append(rec)
    return pd.DataFrame(rows)


def trigger_effectiveness(panel, thresholds, target="bad_6m"):
    """Hit rate, bad rate when triggered, lift and capture for each feature-based trigger."""
    df = panel[panel[target].notna()]
    base = df[target].mean()
    bads = df[target].sum()
    out = []
    for _, t in thresholds.iterrows():
        if t.feature not in df:
            continue
        x = df[t.feature]
        for sev, thr in [("Amber", t.amber), ("Red", t.red)]:
            f = _fires(x, t.direction, thr).fillna(False)
            n = int(f.sum())
            br = df.loc[f, target].mean() if n else np.nan
            out.append(dict(trigger_id=t.trigger_id, name=t["name"], dimension=t.dimension, severity=sev,
                            threshold=thr, hit_rate=round(n / len(df), 4),
                            bad_rate_when_fired=round(br, 4) if n else None,
                            lift=round(br / base, 2) if n else None,
                            capture_rate=round(df.loc[f, target].sum() / bads, 4) if n else 0.0))
    return pd.DataFrame(out)
