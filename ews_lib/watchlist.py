"""Watchlist tiering with escalation and persistence.

Escalation is immediate. De-escalation needs a run of clean periods (the cure
period) and steps down one tier at a time, so customers do not flicker on and
off the list from one month to the next.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ews_lib.config import CURE_MONTHS, TIERS


def raw_tier(r):
    """Tier implied by this period's evidence alone. Returns (tier, reason)."""
    if r.get("account_status") == "Charged off" or r.get("cycles_now", 0) >= 3:
        return 5, "3+ cycles delinquent or charged off"
    if r.get("cycles_now", 0) >= 1:
        return 4, "In arrears"
    pd6 = r.get("pd_6m", 0) or 0
    n_red, n_amber, breadth = r.get("n_red", 0), r.get("n_amber", 0), r.get("breadth", 0)
    # Early intervention
    if pd6 >= 0.20:
        return 3, "PD 6m in band G"
    if r.get("dims_red", 0) >= 2 and pd6 >= 0.10:
        return 3, "Red signals in two or more dimensions with PD 6m above 10%"
    if n_red >= 1 and pd6 >= 0.15:
        return 3, "Red signal with PD 6m above 15%"
    if r.get("sev_T08", 0) == 2 and pd6 >= 0.03:
        return 3, "Salary missing for two months"
    if r.get("sev_S01", 0) == 2 or r.get("sev_S02", 0) >= 1:
        return 3, "Hardship or vulnerability disclosed"
    if (r.get("p_arrears_3m", 0) or 0) >= 0.30:
        return 3, "Probability of arrears above 30%"
    if r.get("sev_S01", 0) >= 1 and n_red >= 1:
        return 3, "Hardship disclosed alongside a red signal"
    # Watch
    if pd6 >= 0.075:
        return 2, "PD 6m above 7.5%"
    if n_red >= 1 and pd6 >= 0.04:
        return 2, "Red signal with PD 6m above 4%"
    if breadth >= 3 and pd6 >= 0.025:
        return 2, "Amber signals across three or more dimensions"
    if r.get("sev_M02", 0) >= 1 and pd6 >= 0.04:
        return 2, "PD doubled in 3 months"
    if r.get("sev_T24", 0) == 2 and (r.get("sev_T16", 0) >= 1 or r.get("sev_S01", 0) >= 1):
        return 2, "Forward-looking: payment shock ahead with stretched affordability"
    if breadth >= 4:
        return 2, "Signals across four or more dimensions"
    # Monitor
    if pd6 >= 0.03:
        return 1, "PD 6m above 3%"
    if r.get("sev_T18", 0) >= 1:
        return 1, "Arrears reported by another lender"
    if r.get("sev_T17", 0) >= 1:
        return 1, "Business DSCR below 1.25"
    if n_red >= 1 and pd6 >= 0.015:
        return 1, "Single red signal with PD 6m above 1.5%"
    if breadth >= 2 and pd6 >= 0.015:
        return 1, "Amber signals in two dimensions"
    return 0, "No material signals"


def apply_persistence(prev_tier, clean_run, raw, cure_periods=CURE_MONTHS):
    """Return (tier, clean_run, movement)."""
    if prev_tier is None:
        return raw, 0, "New"
    if raw >= prev_tier:
        return raw, 0, ("Escalated" if raw > prev_tier else "Unchanged")
    # Leaving collections: Watch for post-arrears monitoring
    if prev_tier >= 4 and raw < 4:
        return max(raw, 2), 0, "Cured from arrears"
    clean_run += 1
    if clean_run >= cure_periods:
        return prev_tier - 1, 0, "De-escalated"
    return prev_tier, clean_run, "Held (cure period)"


def run_monthly(scored: pd.DataFrame) -> pd.DataFrame:
    """Sequential tiering across months. `scored` needs one row per customer-month with
    trigger summaries and model outputs."""
    scored = scored.sort_values(["month", "customer_id"])
    state = {}
    rows = []
    for m, g in scored.groupby("month", sort=True):
        recs = g.to_dict("records")
        for r in recs:
            cid = r["customer_id"]
            prev = state.get(cid)
            rt, reason = raw_tier(r)
            # Persistent watch: six or more months at Watch escalates to Early intervention
            if prev and prev["tier"] == 2 and prev["months_on_watch"] >= 6 and rt == 2 and (r.get("pd_6m") or 0) >= 0.075:
                rt, reason = 3, "Persistent watch status (6+ months)"
            tier, clean, move = apply_persistence(prev["tier"] if prev else None,
                                                  prev["clean"] if prev else 0, rt)
            mow = (prev["months_on_watch"] + 1 if prev and tier >= 1 else (1 if tier >= 1 else 0))
            state[cid] = dict(tier=tier, clean=clean, months_on_watch=mow)
            rows.append(dict(customer_id=cid, month=m, raw_tier=rt, tier=tier, tier_name=TIERS[tier],
                             tier_reason=reason, movement=move, prev_tier=prev["tier"] if prev else None,
                             months_on_watch=mow, cure_progress=clean))
    return pd.DataFrame(rows)


def transition_matrix(wl: pd.DataFrame, month_from, month_to):
    a = wl[wl.month == month_from][["customer_id", "tier"]]
    b = wl[wl.month == month_to][["customer_id", "tier"]]
    m = a.merge(b, on="customer_id", suffixes=("_from", "_to"))
    tab = pd.crosstab(m.tier_from.map(TIERS), m.tier_to.map(TIERS))
    order = [TIERS[k] for k in sorted(TIERS)]
    return tab.reindex(index=[o for o in order if o in tab.index], columns=[o for o in order if o in tab.columns]).fillna(0).astype(int)
