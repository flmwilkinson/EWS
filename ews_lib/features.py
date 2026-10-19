"""Feature engineering and forward-looking targets.

Produces one row per customer per month with four kinds of feature:
  * levels       - where the customer is now (utilisation 87%)
  * changes      - how fast it is moving (utilisation +22pp in 3 months)
  * relatives    - against their own history or peers (salary 45% below 6m average)
  * interactions - combinations that matter more together (high utilisation in a
                   region where unemployment is rising)

All rolling windows are computed on a wide customer x month grid, which keeps the
code readable and fast enough for serverless notebooks.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Sentiment and topic scoring for contact notes (offline, deterministic)
# ---------------------------------------------------------------------------
_LEX = {
    "hardship": ["lost job", "redundan", "hours have been cut", "struggling", "can't manage", "don't know how",
                 "income has halved", "falling behind", "worried", "takings down", "quiet", "food shopping",
                 "gone up", "jumped", "reduce", "options"],
    "vulnerability": ["illness", "sick pay", "caring for", "tearful", "not sleeping", "anxious", "specialist support"],
    "refusal": ["refused", "won't pay", "low priority", "stop calling", "aggressive", "disputes", "no callback",
                "declined i&e", "ready", "may not happen"],
    "positive": ["thanked", "back on track", "positive", "pleased", "clear the arrears", "new job"],
}
_WEIGHTS = {"hardship": -0.5, "vulnerability": -0.6, "refusal": -0.7, "positive": 0.8}


def score_note(text: str) -> dict:
    t = (text or "").lower()
    hits = {k: sum(1 for w in ws if w in t) for k, ws in _LEX.items()}
    raw = sum(_WEIGHTS[k] * min(v, 2) for k, v in hits.items())
    topic = max(hits, key=hits.get) if max(hits.values()) > 0 else "servicing"
    return dict(sentiment=float(np.tanh(raw)), topic=topic, **{f"is_{k}": hits[k] > 0 for k in _LEX})


def score_notes(notes: pd.DataFrame) -> pd.DataFrame:
    scored = notes.copy()
    s = scored.note_text.apply(score_note).apply(pd.Series)
    scored = pd.concat([scored.reset_index(drop=True), s.reset_index(drop=True)], axis=1)
    scored["month"] = pd.to_datetime(scored.created_ts).dt.to_period("M").dt.to_timestamp()
    return scored


# ---------------------------------------------------------------------------
# Wide-grid helpers
# ---------------------------------------------------------------------------
def _wide(df, col, idx, months, fill=np.nan):
    w = df.pivot_table(index="customer_id", columns="month", values=col, aggfunc="first")
    return w.reindex(index=idx, columns=months).astype(float).fillna(fill) if fill is not None else \
        w.reindex(index=idx, columns=months).astype(float)


def _roll(w, n, fn="mean"):
    r = w.T.rolling(n, min_periods=1)
    return getattr(r, fn)().T


def _lag(w, n):
    return w.shift(n, axis=1)


def _lead_max(w, n):
    """Max over the next n months (t+1..t+n)."""
    return w.T[::-1].rolling(n, min_periods=1).max()[::-1].T.shift(-1, axis=1)


def _streak(bool_w):
    a = bool_w.values.astype(int)
    out = np.zeros_like(a)
    for j in range(a.shape[1]):
        out[:, j] = (out[:, j - 1] + 1) * a[:, j] if j else a[:, j]
    return pd.DataFrame(out, index=bool_w.index, columns=bool_w.columns)


# ---------------------------------------------------------------------------
# Feature dictionary (drives documentation, explanations and the app)
# ---------------------------------------------------------------------------
FEATURE_DICTIONARY = [
    # name, family, kind, description
    ("util", "Card behaviour", "Level", "Statement balance / credit limit"),
    ("util_chg_3m", "Card behaviour", "Change", "Utilisation now minus 3 months ago (pp)"),
    ("util_rel_6m", "Card behaviour", "Relative", "Utilisation / own 6-month average"),
    ("util_max_6m", "Card behaviour", "Level", "Highest utilisation in 6 months"),
    ("headroom_gbp", "Card behaviour", "Level", "Unused limit in GBP"),
    ("pay_ratio", "Card behaviour", "Level", "Payment / statement balance"),
    ("pay_ratio_chg_3m", "Card behaviour", "Change", "Payment ratio now minus 3-month average"),
    ("min_pay_streak", "Card behaviour", "Level", "Consecutive months paying only the minimum"),
    ("cash_adv_months_3m", "Card behaviour", "Level", "Months with a cash advance in last 3"),
    ("cash_adv_to_limit_3m", "Card behaviour", "Relative", "Cash advances in 3 months / limit"),
    ("overlimit_6m", "Card behaviour", "Level", "Months over limit in last 6"),
    ("missed_6m", "Card behaviour", "Level", "Missed payments in last 6 months"),
    ("returned_pay_6m", "Card behaviour", "Level", "Returned card payments in last 6 months"),
    ("cycles_now", "Card behaviour", "Level", "Current cycles delinquent"),
    ("spend_chg_3m", "Card behaviour", "Change", "Purchases this month / 3-month average"),
    ("cost_to_pay_ratio_6m", "Card behaviour", "Relative", "Interest and fees / payments over 6 months (persistent debt)"),
    ("salary_missing_3m", "Cross-product", "Level", "Months in last 3 with no expected salary credit"),
    ("income_chg_vs_6m", "Cross-product", "Relative", "Income this month / 6-month average - 1"),
    ("salary_days_late", "Cross-product", "Level", "Days salary arrived later than usual"),
    ("od_days", "Cross-product", "Level", "Days overdrawn this month"),
    ("od_days_chg_3m", "Cross-product", "Change", "Overdraft days now minus 3-month average"),
    ("od_peak_util", "Cross-product", "Level", "Peak overdraft usage / overdraft limit"),
    ("returned_dd_3m", "Cross-product", "Level", "Returned direct debits in last 3 months"),
    ("gambling_share", "Cross-product", "Relative", "Gambling spend / income (3-month)"),
    ("gambling_chg_3m", "Cross-product", "Change", "Gambling share now minus 3 months ago"),
    ("bnpl_share", "Cross-product", "Relative", "BNPL repayments / income"),
    ("payday_lender_3m", "Cross-product", "Level", "Payday lender payment seen in last 3 months"),
    ("other_arrears", "Cross-product", "Level", "Cycles delinquent on mortgage or loan with us"),
    ("dsr", "Cross-product", "Relative", "Debt service ratio: card minimum + mortgage + loans + external / income"),
    ("dsr_chg_3m", "Cross-product", "Change", "DSR now minus 3 months ago"),
    ("refix_shock_fwd", "Forward-looking", "Level", "Expected payment rise if a fixed rate ends in the next 6 months"),
    ("dscr", "Cross-product", "Level", "Business debt service cover (sole traders)"),
    ("dscr_chg_3m", "Cross-product", "Change", "DSCR now minus 3 months ago"),
    ("bureau_score", "Credit bureau", "Level", "Generic bureau score (0-999)"),
    ("bureau_score_chg_3m", "Credit bureau", "Change", "Bureau score change over 3 months"),
    ("ext_debt_to_income", "Credit bureau", "Relative", "External debt / annual income"),
    ("ext_debt_chg_6m", "Credit bureau", "Change", "External debt growth over 6 months (%)"),
    ("searches_3m", "Credit bureau", "Level", "Credit searches in last 3 months"),
    ("new_accounts_6m", "Credit bureau", "Level", "New external accounts in last 6 months"),
    ("ext_worst_6m", "Credit bureau", "Level", "Worst external arrears status in 6 months"),
    ("ext_arrears_now", "Credit bureau", "Level", "Currently in arrears with another lender"),
    ("ccj_flag", "Credit bureau", "Level", "County court judgment registered"),
    ("unemp_rate", "Macroeconomic", "Level", "Regional unemployment rate"),
    ("unemp_chg_12m", "Macroeconomic", "Change", "Regional unemployment change over 12 months (pp)"),
    ("unemp_fwd_12m", "Forward-looking", "Change", "Expected regional unemployment change, next 12 months (pp)"),
    ("hpi_yoy", "Macroeconomic", "Level", "Regional house price growth (%)"),
    ("bank_rate", "Macroeconomic", "Level", "Bank Rate (%)"),
    ("rate_sensitive", "Macroeconomic", "Level", "Tracker mortgage or fix ending within 12 months"),
    ("util_x_unemp", "Interaction", "Interaction", "Utilisation x regional unemployment change"),
    ("util_x_searches", "Interaction", "Interaction", "Utilisation x credit searches (credit hungry and stretched)"),
    ("income_drop_x_od", "Interaction", "Interaction", "Income drop x overdraft days (shock without buffer)"),
    ("dsr_x_rate_sensitive", "Interaction", "Interaction", "DSR x rate sensitivity"),
    ("sentiment_3m", "Conduct and sentiment", "Level", "Average note sentiment in last 3 months (-1 to 1)"),
    ("hardship_notes_3m", "Conduct and sentiment", "Level", "Notes mentioning hardship in last 3 months"),
    ("vulnerability_notes_3m", "Conduct and sentiment", "Level", "Notes indicating vulnerability in last 3 months"),
    ("refusal_notes_3m", "Conduct and sentiment", "Level", "Notes showing refusal or dispute in last 3 months"),
    ("ptp_broken_3m", "Collections", "Level", "Broken promises to pay in last 3 months"),
    ("rpc_rate_3m", "Collections", "Relative", "Right-party contacts / attempts in last 3 months"),
    ("forbearance_active", "Collections", "Level", "Currently in a forbearance arrangement"),
    ("age", "Customer", "Level", "Age"),
    ("tenure_months", "Customer", "Level", "Months as a customer"),
    ("vulnerability_flag", "Customer", "Level", "Recorded vulnerability (FG21/1)"),
]
FEATURES = [f[0] for f in FEATURE_DICTIONARY]
FEATURE_FAMILY = {f[0]: f[1] for f in FEATURE_DICTIONARY}

# A compact, interpretable set for the scorecard and weighted models
SCORECARD_FEATURES = [
    "util", "util_chg_3m", "pay_ratio", "min_pay_streak", "cash_adv_months_3m", "missed_6m",
    "salary_missing_3m", "income_chg_vs_6m", "od_days", "returned_dd_3m", "gambling_share",
    "dsr", "bureau_score", "searches_3m", "ext_worst_6m", "unemp_chg_12m", "hardship_notes_3m",
]

# Signals the real-time service updates from event streams as they happen: current account transactions,
# card cash advances, credit bureau triggers and contact centre notes. Everything else comes from the
# monthly card statement, the monthly bureau refresh, other products and macro data.
REALTIME_SIGNALS = [
    "salary_missing_3m", "income_chg_vs_6m", "salary_days_late", "od_days", "od_days_chg_3m", "od_peak_util",
    "returned_dd_3m", "gambling_share", "gambling_chg_3m", "bnpl_share", "payday_lender_3m", "income_drop_x_od",
    "cash_adv_months_3m", "cash_adv_to_limit_3m",
    "searches_3m", "ext_arrears_now", "util_x_searches",
    "sentiment_3m", "hardship_notes_3m", "refusal_notes_3m",
]

RISK_BUCKETS = {0: "Up to date", 1: "1 cycle delinquent (30 days)", 2: "2 cycles delinquent (60 days)",
                3: "3+ cycles delinquent (90+ days)", 4: "Charged off"}


def feature_dictionary_frame():
    return pd.DataFrame(FEATURE_DICTIONARY, columns=["feature", "family", "kind", "description"])


# ---------------------------------------------------------------------------
# Panel build
# ---------------------------------------------------------------------------
def build_panel(s: dict, first_feature_month="2024-10-01") -> pd.DataFrame:
    cm = s["card_monthly"].copy()
    cm["month"] = pd.to_datetime(cm.month)
    months = sorted(cm.month.unique())
    idx = sorted(s["customers"].customer_id.unique())
    W = lambda df, col, fill=np.nan: _wide(df, col, idx, months, fill)

    cust = s["customers"].set_index("customer_id").reindex(idx)
    net_income_base = cust.gross_income_annual * 0.74 / 12

    # ---- card ---------------------------------------------------------------
    util = W(cm, "utilisation")
    limit = W(cm, "credit_limit")
    bal = W(cm, "closing_balance")
    stmt = W(cm, "statement_balance", 0)
    pay = W(cm, "payment_amount", 0)
    mind = W(cm, "minimum_due", 0)
    cash = W(cm, "cash_advance_amt", 0)
    purch = W(cm, "purchases", 0)
    intf = W(cm, "interest_charged", 0) + W(cm, "fees_charged", 0)
    miss = W(cm, "missed_payment_flag", 0)
    retp = W(cm, "returned_payment_flag", 0)
    overl = W(cm, "overlimit_flag", 0)
    cyc = W(cm, "cycles_delinquent", 0)
    status = cm.pivot_table(index="customer_id", columns="month", values="account_status", aggfunc="first").reindex(index=idx, columns=months)
    co = (status == "Charged off").astype(float)
    forb = W(cm, "forbearance_flag", 0)

    pay_ratio = (pay / stmt.where(stmt > 0)).clip(0, 1).fillna(1.0)
    F = {}
    F["util"] = util.clip(0, 3)
    F["util_chg_3m"] = util - _lag(util, 3)
    F["util_rel_6m"] = (util / _roll(util, 6).clip(lower=0.01)).clip(0, 5)
    F["util_max_6m"] = _roll(util, 6, "max")
    F["headroom_gbp"] = (limit - stmt).clip(lower=0)
    F["pay_ratio"] = pay_ratio
    F["pay_ratio_chg_3m"] = pay_ratio - _roll(pay_ratio, 3)
    min_only = (pay <= mind * 1.05) & (pay > 0) & (stmt > 50)
    F["min_pay_streak"] = _streak(min_only)
    F["cash_adv_months_3m"] = _roll((cash > 0).astype(float), 3, "sum")
    F["cash_adv_to_limit_3m"] = _roll(cash, 3, "sum") / limit
    F["overlimit_6m"] = _roll(overl, 6, "sum")
    F["missed_6m"] = _roll(miss, 6, "sum")
    F["returned_pay_6m"] = _roll(retp, 6, "sum")
    F["cycles_now"] = cyc
    F["spend_chg_3m"] = purch / _roll(purch, 3).replace(0, np.nan)
    F["cost_to_pay_ratio_6m"] = _roll(intf, 6, "sum") / _roll(pay, 6, "sum").replace(0, np.nan)

    # ---- current account ----------------------------------------------------
    ca = s["current_account_monthly"].copy()
    ca["month"] = pd.to_datetime(ca.month)
    has_ca = pd.Series(idx, index=idx).isin(set(ca.customer_id))
    sal = W(ca, "salary_credit_amt")
    oth = W(ca, "other_income_credit_amt", 0)
    income = sal.fillna(0) + oth
    salaried_hist = _roll((sal > 0).astype(float), 6, "max").shift(1, axis=1).fillna(0) > 0
    F["salary_missing_3m"] = _roll(((sal.fillna(0) <= 0) & salaried_hist).astype(float), 3, "sum")
    inc6 = _roll(income, 6).shift(1, axis=1)
    F["income_chg_vs_6m"] = (income / inc6.replace(0, np.nan) - 1).clip(-1, 3)
    F["salary_days_late"] = W(ca, "salary_days_late", 0)
    od = W(ca, "overdraft_days", 0)
    F["od_days"] = od
    F["od_days_chg_3m"] = od - _roll(od, 3).shift(1, axis=1)
    F["od_peak_util"] = W(ca, "overdraft_peak_util", 0)
    F["returned_dd_3m"] = _roll(W(ca, "returned_dd_count", 0), 3, "sum")
    inc_ref = _roll(income, 3).replace(0, np.nan)
    inc_ref = inc_ref.fillna(pd.DataFrame(np.repeat(net_income_base.values[:, None], len(months), 1), index=idx, columns=months))
    gs = _roll(W(ca, "gambling_spend", 0), 3) / inc_ref
    F["gambling_share"] = gs.clip(0, 2)
    F["gambling_chg_3m"] = gs - _lag(gs, 3)
    F["bnpl_share"] = (_roll(W(ca, "bnpl_repayments", 0), 3) / inc_ref).clip(0, 2)
    F["payday_lender_3m"] = _roll(W(ca, "payday_lender_payment_flag", 0), 3, "max")

    # ---- other products, DSR, refix ------------------------------------------
    op = s["other_product_monthly"].copy()
    op["month"] = pd.to_datetime(op.month)
    F["other_arrears"] = W(op, "other_product_cycles_delinquent", 0)
    mort_due = W(op, "mortgage_payment_due", 0)
    loan_due = W(op, "loan_payment_due", 0)
    bu = s["bureau_monthly"].copy()
    bu["month"] = pd.to_datetime(bu.month)
    ext_debt = W(bu, "total_external_debt").T.ffill().T
    income_for_dsr = _roll(income, 3).where(np.repeat(has_ca.values[:, None], len(months), 1), np.nan)
    income_for_dsr = income_for_dsr.fillna(pd.DataFrame(np.repeat(net_income_base.values[:, None], len(months), 1), index=idx, columns=months))
    dsr = (mind + mort_due + loan_due + 0.03 * ext_debt.fillna(0)) / income_for_dsr.replace(0, np.nan)
    F["dsr"] = dsr.clip(0, 3)
    F["dsr_chg_3m"] = F["dsr"] - _lag(F["dsr"], 3)

    hold = s["product_holdings"].copy()
    mort = hold[(hold.product_type == "Mortgage")]
    refix = pd.DataFrame(0.0, index=idx, columns=months)
    rate_sens = pd.DataFrame(0.0, index=idx, columns=months)
    for _, h in mort.iterrows():
        if h.customer_id not in refix.index:
            continue
        if h.rate_type == "Tracker":
            rate_sens.loc[h.customer_id] = 1.0
            continue
        fe = pd.Timestamp(h.fix_end_month)
        shock = max(0.0, (4.6 - h.original_rate_pct) * 0.13)
        for j, m in enumerate(months):
            gap = (fe.year - m.year) * 12 + fe.month - m.month
            if 0 < gap <= 6:
                refix.iat[refix.index.get_loc(h.customer_id), j] = shock
            if 0 < gap <= 12:
                rate_sens.iat[rate_sens.index.get_loc(h.customer_id), j] = 1.0
    F["refix_shock_fwd"] = refix
    F["rate_sensitive"] = rate_sens

    biz = s["business_monthly"].copy()
    if len(biz):
        biz["month"] = pd.to_datetime(biz.month)
        dscr = W(biz, "dscr")
    else:
        dscr = pd.DataFrame(np.nan, index=idx, columns=months)
    F["dscr"] = dscr.clip(-2, 10)
    F["dscr_chg_3m"] = dscr - _lag(dscr, 3)

    # ---- bureau ---------------------------------------------------------------
    bs = W(bu, "bureau_score")
    F["bureau_score"] = bs
    F["bureau_score_chg_3m"] = bs - _lag(bs, 3)
    gross = pd.DataFrame(np.repeat(cust.gross_income_annual.values[:, None], len(months), 1), index=idx, columns=months)
    F["ext_debt_to_income"] = (ext_debt / gross).clip(0, 5)
    F["ext_debt_chg_6m"] = (ext_debt / _lag(ext_debt, 6).replace(0, np.nan) - 1).clip(-1, 5)
    F["searches_3m"] = _roll(W(bu, "searches_this_month", 0), 3, "sum")
    F["new_accounts_6m"] = _roll(W(bu, "new_accounts_this_month", 0), 6, "sum")
    ews = W(bu, "worst_external_status", 0)
    F["ext_worst_6m"] = _roll(ews, 6, "max")
    F["ext_arrears_now"] = (ews > 0).astype(float)
    F["ccj_flag"] = W(bu, "ccj_flag", 0)
    stale = W(bu, "bureau_stale_flag", 0) if "bureau_stale_flag" in bu else None

    # ---- macro ---------------------------------------------------------------
    mac = s["macro_regional_monthly"].copy()
    mac["month"] = pd.to_datetime(mac.month)
    mac = mac.sort_values(["region", "month"])
    mac["unemp_chg_12m"] = mac.groupby("region").unemployment_rate.diff(12)
    mac["unemp_chg_6m"] = mac.groupby("region").unemployment_rate.diff(6)
    # Point-in-time forward view: momentum projection for history; the economics
    # team's scenario-weighted forecast replaces it for the latest month.
    mac["unemp_fwd_12m"] = (mac["unemp_chg_6m"] * 1.5).fillna(0)
    fc = s.get("macro_forecast")
    if fc is not None and len(fc):
        w = fc.assign(x=fc.unemp_change_12m_pp * fc.scenario_weight).groupby("region").x.sum()
        latest = mac.month.max()
        mac.loc[mac.month == latest, "unemp_fwd_12m"] = mac.loc[mac.month == latest, "region"].map(w).values
    reg = cust.region
    def macro_wide(col):
        p = mac.pivot(index="region", columns="month", values=col).reindex(columns=months)
        return pd.DataFrame(p.reindex(reg.values).values, index=idx, columns=months)
    F["unemp_rate"] = macro_wide("unemployment_rate")
    F["unemp_chg_12m"] = macro_wide("unemp_chg_12m").T.bfill().T
    F["unemp_fwd_12m"] = macro_wide("unemp_fwd_12m")
    F["hpi_yoy"] = macro_wide("hpi_yoy_pct")
    F["bank_rate"] = macro_wide("bank_rate_pct")

    # ---- interactions ----------------------------------------------------------
    F["util_x_unemp"] = F["util"] * F["unemp_chg_12m"].clip(lower=0)
    F["util_x_searches"] = F["util"] * F["searches_3m"]
    F["income_drop_x_od"] = (-F["income_chg_vs_6m"]).clip(lower=0).fillna(0) * F["od_days"]
    F["dsr_x_rate_sensitive"] = F["dsr"] * (F["rate_sensitive"] + F["refix_shock_fwd"])

    # ---- notes / sentiment -------------------------------------------------------
    notes = score_notes(s["contact_notes"]) if len(s["contact_notes"]) else None
    if notes is not None:
        agg = notes.groupby(["customer_id", "month"]).agg(
            sent=("sentiment", "sum"), n=("sentiment", "size"), hard=("is_hardship", "sum"),
            vul=("is_vulnerability", "sum"), ref=("is_refusal", "sum")).reset_index()
        n3 = _roll(W(agg, "n", 0), 3, "sum")
        F["sentiment_3m"] = (_roll(W(agg, "sent", 0), 3, "sum") / n3.replace(0, np.nan)).fillna(0)
        F["hardship_notes_3m"] = _roll(W(agg, "hard", 0), 3, "sum")
        F["vulnerability_notes_3m"] = _roll(W(agg, "vul", 0), 3, "sum")
        F["refusal_notes_3m"] = _roll(W(agg, "ref", 0), 3, "sum")
    else:
        for k in ["sentiment_3m", "hardship_notes_3m", "vulnerability_notes_3m", "refusal_notes_3m"]:
            F[k] = pd.DataFrame(0.0, index=idx, columns=months)

    # ---- collections -------------------------------------------------------------
    ce = s["collections_events"].copy()
    if len(ce):
        ce["month"] = pd.to_datetime(ce.month)
        ce["broken"] = ce.promise_to_pay.fillna(False).astype(bool) & (ce.ptp_kept.fillna(True) == False)
        ce["rpc"] = ce.right_party_contact.astype(float)
        F["ptp_broken_3m"] = _roll(W(ce, "broken", 0), 3, "sum")
        att = _roll(W(ce, "contact_attempts", 0), 3, "sum")
        F["rpc_rate_3m"] = (_roll(W(ce, "rpc", 0), 3, "sum") / att.replace(0, np.nan)).fillna(0)
    else:
        F["ptp_broken_3m"] = F["rpc_rate_3m"] = pd.DataFrame(0.0, index=idx, columns=months)
    F["forbearance_active"] = forb

    # ---- static --------------------------------------------------------------------
    for col in ["age", "tenure_months", "vulnerability_flag"]:
        F[col] = pd.DataFrame(np.repeat(cust[col].astype(float).values[:, None], len(months), 1), index=idx, columns=months)
    F["tenure_months"] = F["tenure_months"] + np.arange(len(months))[None, :]

    # ---- targets (forward looking), labelled from historical risk buckets ------------------
    bucket = cyc.clip(upper=3).where(co == 0, 4.0)   # 0 up to date, 1-3 cycles delinquent (3 = 3+), 4 charged off
    bad_now = bucket >= 2
    fut_bucket = _lead_max(bucket, 6)                 # worst risk bucket reached in the next 6 months
    fut_bad = (fut_bucket >= 2).astype(float).where(fut_bucket.notna())
    fut_miss3 = _lead_max(miss, 3)
    fe = s["forbearance_events"].copy()
    fe["month"] = pd.to_datetime(fe.start_month)
    fe["one"] = 1.0
    forb_start = W(fe.drop_duplicates(["customer_id", "month"]), "one", 0)
    fut_forb = _lead_max(forb_start, 6)
    cyc_next2 = pd.DataFrame(np.fmin(cyc.shift(-1, axis=1).values, cyc.shift(-2, axis=1).values), index=idx, columns=months)
    T = {}
    F["risk_bucket"] = bucket
    T["worst_bucket_6m"] = fut_bucket.where(~bad_now)
    T["bad_6m"] = fut_bad.where(~bad_now)
    T["arrears_3m"] = fut_miss3.where((cyc == 0) & (co == 0))
    T["forbearance_6m"] = fut_forb.where((forb == 0) & (co == 0))
    T["cure_2m"] = (cyc_next2 == 0).astype(float).where((cyc > 0) & (co == 0) & cyc_next2.notna())

    # ---- stack --------------------------------------------------------------------------
    mi = pd.MultiIndex.from_product([idx, months], names=["customer_id", "month"])
    flat = lambda w: w.reindex(index=idx, columns=months).values.ravel()
    panel = pd.DataFrame({k: flat(v) for k, v in {**F, **T}.items()}, index=mi).reset_index()
    panel["account_status"] = flat(status)
    if stale is not None:
        panel["bureau_stale_flag"] = flat(stale)
    static = cust[["region", "segment", "employment_type", "housing_status", "gross_income_annual"]].reset_index()
    panel = panel.merge(static, on="customer_id", how="left")
    panel = panel[panel.month >= pd.Timestamp(first_feature_month)]
    # Horizon availability: targets are only observable where the window has closed
    last = max(months)
    for tgt, h in [("bad_6m", 6), ("worst_bucket_6m", 6), ("arrears_3m", 3), ("forbearance_6m", 6), ("cure_2m", 2)]:
        panel.loc[panel.month > last - pd.DateOffset(months=h), tgt] = np.nan
    panel = panel.replace([np.inf, -np.inf], np.nan)
    return panel.reset_index(drop=True)


def cant_pay_training_set(panel: pd.DataFrame, collections: pd.DataFrame) -> pd.DataFrame:
    """Label = agent-recorded arrears reason, joined to features at that month."""
    cant = {"Job loss", "Income reduction", "Illness / life event", "Payment shock", "Business downturn"}
    wont = {"Refused to discuss", "Dispute"}
    ce = collections.dropna(subset=["arrears_reason"]).copy()
    ce["month"] = pd.to_datetime(ce.month)
    ce = ce[ce.arrears_reason.isin(cant | wont)]
    ce["cant_pay"] = ce.arrears_reason.isin(cant).astype(float)
    ce = ce.drop_duplicates(["customer_id", "month"])
    return panel.merge(ce[["customer_id", "month", "cant_pay", "arrears_reason"]], on=["customer_id", "month"])
