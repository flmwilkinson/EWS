"""Trigger evaluation: turns features and model outputs into amber / red signals.

Breadth matters as much as depth. A customer with one red signal is different
from one with amber signals across card, current account and bureau data, so
the summary counts distinct evidence dimensions as well as the signals.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Dimensions that count towards breadth (customer-level evidence)
EVIDENCE_DIMENSIONS = ["Card behaviour", "Cross-product", "Credit bureau", "Forward-looking",
                       "Conduct and sentiment", "Collections"]
# Regional macro triggers amplify customer evidence but never escalate on their own
MACRO_TRIGGERS = {"T23", "T25"}


def evaluate(df: pd.DataFrame, thresholds: pd.DataFrame, key_cols=("customer_id", "month")):
    """Return (long table of fired triggers, df with summary columns added)."""
    key_cols = list(key_cols)
    fired = []
    sev = pd.DataFrame(0, index=df.index, columns=thresholds.trigger_id.values, dtype="int8")
    for _, t in thresholds.iterrows():
        if t.feature not in df:
            continue
        x = df[t.feature]
        if t.direction == ">=":
            amber, red = x >= t.amber, x >= t.red
        else:
            amber, red = x <= t.amber, x <= t.red
        level = np.where(red.fillna(False), 2, np.where(amber.fillna(False), 1, 0))
        sev[t.trigger_id] = level
        hit = level > 0
        if hit.any():
            f = df.loc[hit, key_cols].copy()
            f["trigger_id"] = t.trigger_id
            f["trigger_name"] = t["name"]
            f["dimension"] = t.dimension
            f["severity"] = np.where(level[hit] == 2, "Red", "Amber")
            f["value"] = x[hit].astype(float).values
            f["threshold"] = np.where(level[hit] == 2, t.red, t.amber)
            fired.append(f)
    fired = pd.concat(fired, ignore_index=True) if fired else pd.DataFrame(
        columns=key_cols + ["trigger_id", "trigger_name", "dimension", "severity", "value", "threshold"])

    dim_of = dict(zip(thresholds.trigger_id, thresholds.dimension))
    out = df.copy()
    cust_ids = [c for c in sev.columns if c not in MACRO_TRIGGERS and dim_of[c] in EVIDENCE_DIMENSIONS]
    model_ids = [c for c in sev.columns if dim_of[c] == "Model"]
    out["n_amber"] = (sev[cust_ids] == 1).sum(axis=1)
    out["n_red"] = (sev[cust_ids] == 2).sum(axis=1)
    out["n_model_flags"] = (sev[model_ids] > 0).sum(axis=1)
    dims = {}
    for d in EVIDENCE_DIMENSIONS:
        ids = [c for c in cust_ids if dim_of[c] == d]
        dims[d] = sev[ids].max(axis=1) if ids else 0
    dimf = pd.DataFrame(dims)
    macro_hit = sev[[c for c in MACRO_TRIGGERS if c in sev]].max(axis=1) > 0
    out["breadth"] = (dimf > 0).sum(axis=1) + (macro_hit & ((dimf > 0).sum(axis=1) > 0)).astype(int)
    out["dims_red"] = (dimf == 2).sum(axis=1)
    out["macro_amplifier"] = macro_hit
    for tid in ["S01", "S02", "M02", "T08", "T16", "T17", "T18", "T24"]:
        out[f"sev_{tid}"] = sev[tid] if tid in sev else 0
    out["fired_triggers"] = _fast_join(sev)
    return fired, out


def _fast_join(sev):
    cols = np.array(sev.columns)
    m = sev.values > 0
    return [",".join(cols[row]) for row in m]
