"""Batch scoring: models -> triggers -> watchlist -> actions -> explanations."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ews_lib import actions, models, triggers, watchlist
from ews_lib.features import FEATURE_FAMILY


def score_frame(df: pd.DataFrame, bundles: dict) -> pd.DataFrame:
    """Add every model output to a feature frame (any number of months)."""
    out = df.copy()
    out["pd_6m"] = models.predict(bundles["pd_6m"], out)
    out["risk_band"] = out.pd_6m.apply(models.band)
    out["p_arrears_3m"] = models.predict(bundles["arrears_3m"], out)
    out["p_forbearance_6m"] = models.predict(bundles["forbearance_6m"], out)
    ptp = models.predict(bundles["cure_2m"], out)
    out["propensity_to_pay"] = np.where(out.cycles_now.fillna(0) > 0, ptp, np.nan)
    out["p_cant_pay"] = models.predict(bundles["cant_pay"], out)
    return out


def add_pd_momentum(scored: pd.DataFrame) -> pd.DataFrame:
    s = scored.sort_values(["customer_id", "month"]).copy()
    prev = s.groupby("customer_id").pd_6m.shift(3)
    s["pd_ratio_3m"] = (s.pd_6m / prev).round(3)
    return s


def run_batch(panel: pd.DataFrame, bundles: dict, thresholds: pd.DataFrame, months, customers: pd.DataFrame):
    """Score the given months, evaluate triggers, tier the watchlist sequentially."""
    lookback = sorted(panel.month.unique())
    first = pd.Timestamp(months[0]) - pd.DateOffset(months=3)
    base = panel[(panel.month >= first) & (panel.month <= pd.Timestamp(months[-1]))]
    # Accounts charged off in an earlier month have left the book
    prev_status = base.sort_values("month").groupby("customer_id").account_status.shift(1)
    base = base[~(prev_status == "Charged off")]
    scored = add_pd_momentum(score_frame(base, bundles))
    scored = scored[scored.month.isin(pd.to_datetime(months))]
    fired, summ = triggers.evaluate(scored, thresholds)
    wl = watchlist.run_monthly(summ)
    full = summ.merge(wl, on=["customer_id", "month"], how="left")
    full = full.merge(customers[["customer_id", "marketing_consent"]], on="customer_id", how="left")
    return full, fired


def latest_decisions(full: pd.DataFrame) -> pd.DataFrame:
    latest = full[full.month == full.month.max()].copy()
    return actions.decide_frame(latest)


def explain(df: pd.DataFrame, bundle: dict, k=3) -> pd.DataFrame:
    """Top risk drivers per row, in words."""
    contrib = models.contributions(bundle, df)
    drivers = models.top_drivers(contrib, df, k=k)
    df = df.copy()
    df["top_drivers"] = [
        "; ".join(f"{d['feature']} = {_fmt(d['feature'], d['value'])} ({FEATURE_FAMILY.get(d['feature'], '')})" for d in ds)
        for ds in drivers]
    df["driver_1"] = [ds[0]["feature"] if ds else None for ds in drivers]
    return df


def _fmt(f, v):
    if v is None:
        return "n/a"
    if f in ("util", "util_max_6m", "pay_ratio", "dsr", "gambling_share", "bnpl_share", "od_peak_util",
             "ext_debt_to_income", "cash_adv_to_limit_3m"):
        return f"{v:.0%}"
    if f in ("util_chg_3m", "pay_ratio_chg_3m", "dsr_chg_3m"):
        return f"{v * 100:+.1f}pp"
    if f in ("income_chg_vs_6m", "ext_debt_chg_6m", "refix_shock_fwd"):
        return f"{v:+.0%}"
    if abs(v) >= 100:
        return f"{v:,.0f}"
    return f"{v:.2g}"
