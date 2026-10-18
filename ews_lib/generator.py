"""Synthetic retail bank portfolio generator.

The generator simulates customers month by month with a simple causal story so
that early warning signals genuinely lead defaults:

    shock (job loss, income cut, refix, illness, gambling, bust-out)
      -> savings buffer depletes
      -> unfunded gap is borrowed (card spend, overdraft, cash, new credit)
      -> utilisation rises, searches rise, min-pay-only, returned direct debits
      -> external arrears, then internal arrears, then 60+ DPD / charge-off

Every customer-month also carries noise so the models are not perfect.

The generator also writes `generator_truth`, the latent cause and stress index.
This is never used as a model input. It exists so the demo can show, honestly,
where each model is right or wrong.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from ews_lib.config import (
    FIRST_MONTH, LAST_MONTH, N_CUSTOMERS, REGIONS, REGION_WEIGHTS, SEED,
)

# ---------------------------------------------------------------------------
# Demo personas: real-looking stories the presenter can click into.
# Their history is clean up to the last month; the live simulator drives them.
# ---------------------------------------------------------------------------
PERSONAS = {
    "C900001": dict(name="Priya", story="Made redundant; salary stops mid-October (can't pay)",
                    age=38, region="West Midlands", segment="Mass market",
                    employment="Employed full-time", income=36_000, housing="Rent"),
    "C900002": dict(name="Dan", story="Gambling escalating while salary is stable (won't pay risk)",
                    age=29, region="North West", segment="Mass market",
                    employment="Employed full-time", income=41_000, housing="Living with family"),
    "C900003": dict(name="Grace", story="Fixed-rate mortgage ends December 2026; payment up ~40%",
                    age=47, region="South East", segment="Mass affluent",
                    employment="Employed full-time", income=58_000, housing="Mortgage (with us)"),
    "C900004": dict(name="Lee", story="Sole trader café; turnover falls and DSCR drops below 1",
                    age=51, region="North East", segment="Sole trader",
                    employment="Self-employed", income=34_000, housing="Mortgage (other lender)"),
    "C900005": dict(name="Sarah", story="Healthy, rising income: eligible for limit increase and offers",
                    age=33, region="London", segment="Mass affluent",
                    employment="Employed full-time", income=72_000, housing="Rent"),
    "C900006": dict(name="Mark", story="Clean with us, but misses a payment at another lender",
                    age=42, region="Yorkshire and The Humber", segment="Mass market",
                    employment="Employed full-time", income=39_000, housing="Mortgage (other lender)"),
}

# Illustrative macro paths (not official statistics). Swap in ONS / BoE series
# for a client build.
_BANK_RATE = {  # month -> Bank Rate %, piecewise constant
    "2024-04": 5.25, "2024-08": 5.00, "2024-11": 4.75, "2025-02": 4.50,
    "2025-05": 4.25, "2025-08": 4.00, "2025-12": 3.75, "2026-04": 3.75,
}
_REGION_BASE_UNEMP = {
    "North East": 5.6, "North West": 4.4, "Yorkshire and The Humber": 4.5,
    "East Midlands": 4.2, "West Midlands": 5.2, "East of England": 3.9,
    "London": 5.3, "South East": 3.7, "South West": 3.4, "Wales": 4.1,
    "Scotland": 4.2, "Northern Ireland": 2.9,
}
_STRESSED_REGIONS = {"North East": 1.3, "West Midlands": 1.1}  # extra pp by late 2026


def month_list(first=FIRST_MONTH, last=LAST_MONTH):
    return list(pd.date_range(first, last, freq="MS"))


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


# ---------------------------------------------------------------------------
# Macro
# ---------------------------------------------------------------------------
def generate_macro(months, rng):
    rows = []
    n = len(months)
    for region in REGIONS:
        base = _REGION_BASE_UNEMP[region]
        drift = np.linspace(0, 0.6, n)  # gentle national rise
        stress = np.zeros(n)
        if region in _STRESSED_REGIONS:
            start = int(n * 0.55)
            stress[start:] = np.linspace(0, _STRESSED_REGIONS[region], n - start)
        noise = rng.normal(0, 0.06, n).cumsum() * 0.3
        unemp = base + drift + stress + noise
        hpi = 2.5 + rng.normal(0, 0.4, n).cumsum() * 0.2 - (stress * 1.5)
        for i, m in enumerate(months):
            key = m.strftime("%Y-%m")
            rate = [v for k, v in _BANK_RATE.items() if k <= key][-1]
            cpi = 3.2 - 1.0 * np.sin(i / 6) + 0.3 * (i > 14) + rng.normal(0, 0.1)
            rows.append(dict(region=region, month=m.date(), unemployment_rate=round(unemp[i], 2),
                             hpi_yoy_pct=round(hpi[i], 2), cpi_yoy_pct=round(cpi, 2),
                             bank_rate_pct=rate, wage_growth_yoy_pct=round(4.5 - i * 0.04 + rng.normal(0, 0.15), 2)))
    macro = pd.DataFrame(rows)

    # Forward-looking scenario table, as at the latest month: what the bank's
    # economics team expects over the next 12 months, by region.
    last = macro[macro.month == months[-1].date()]
    fc = []
    for _, r in last.iterrows():
        extra = _STRESSED_REGIONS.get(r.region, 0) * 0.6
        for scen, w, d_unemp, d_hpi in [("Base", 0.6, 0.3, 1.5), ("Downside", 0.3, 1.4, -4.0), ("Upside", 0.1, -0.2, 3.5)]:
            fc.append(dict(region=r.region, as_at_month=r.month, scenario=scen, scenario_weight=w,
                           unemp_change_12m_pp=round(d_unemp + extra * (1.5 if scen == "Downside" else 1), 2),
                           hpi_change_12m_pct=round(d_hpi - extra * 2, 2),
                           bank_rate_12m_pct=3.25 if scen != "Downside" else 2.75))
    return macro, pd.DataFrame(fc)


# ---------------------------------------------------------------------------
# Customers and products (static)
# ---------------------------------------------------------------------------
def generate_customers(n, rng):
    ids = [f"C{i:06d}" for i in range(1, n + 1)] + list(PERSONAS)
    n_all = len(ids)
    age = np.clip(rng.normal(44, 14, n_all), 19, 86).astype(int)
    region = rng.choice(REGIONS, n_all, p=np.array(REGION_WEIGHTS) / sum(REGION_WEIGHTS))
    z = rng.normal(0, 1, n_all)  # latent credit quality (higher = riskier)

    seg = np.where(rng.random(n_all) < 0.06, "Sole trader",
          np.where((age < 27) & (rng.random(n_all) < 0.6), "Young / thin file",
          np.where(rng.random(n_all) < 0.2, "Mass affluent", "Mass market")))
    emp = np.where(seg == "Sole trader", "Self-employed",
          np.where(age >= 67, "Retired",
          np.where(age < 23, rng.choice(["Student", "Employed part-time", "Employed full-time"], n_all, p=[0.4, 0.3, 0.3]),
                   rng.choice(["Employed full-time", "Employed part-time", "Self-employed"], n_all, p=[0.82, 0.11, 0.07]))))
    inc = np.exp(rng.normal(np.log(31_000), 0.42, n_all))
    inc = np.where(seg == "Mass affluent", inc * 2.1, inc)
    inc = np.where(seg == "Young / thin file", inc * 0.72, inc)
    inc = np.where(emp == "Retired", inc * 0.68, inc)
    inc = np.where(emp == "Student", inc * 0.35, inc)
    inc = np.where(emp == "Employed part-time", inc * 0.55, inc)
    inc = inc * np.exp(-0.08 * z)
    housing = np.where(age < 25, rng.choice(["Rent", "Living with family"], n_all, p=[0.45, 0.55]),
              np.where(age > 65, rng.choice(["Owner outright", "Mortgage (with us)", "Rent"], n_all, p=[0.7, 0.1, 0.2]),
                       rng.choice(["Mortgage (with us)", "Mortgage (other lender)", "Rent", "Owner outright", "Living with family"],
                                  n_all, p=[0.26, 0.24, 0.33, 0.09, 0.08])))
    cust = pd.DataFrame(dict(
        customer_id=ids, age=age, region=region, segment=seg, employment_type=emp,
        gross_income_annual=np.round(inc, -2), housing_status=housing,
        tenure_months=rng.integers(3, 300, n_all),
        has_current_account=rng.random(n_all) < 0.86,
        has_personal_loan=rng.random(n_all) < 0.16,
        preferred_channel=rng.choice(["App", "Web", "Phone", "Branch"], n_all, p=[0.58, 0.2, 0.15, 0.07]),
        vulnerability_flag=rng.random(n_all) < 0.035,
        marketing_consent=rng.random(n_all) < 0.71,
    ))
    cust["_z"] = z
    # Persona overrides
    for cid, p in PERSONAS.items():
        i = cust.index[cust.customer_id == cid][0]
        cust.loc[i, ["age", "region", "segment", "employment_type", "gross_income_annual", "housing_status"]] = [
            p["age"], p["region"], p["segment"], p["employment"], p["income"], p["housing"]]
        cust.loc[i, ["has_current_account", "vulnerability_flag", "_z"]] = [True, False, -0.6]
        cust.loc[i, "tenure_months"] = 96
    cust.loc[cust.customer_id == "C900004", "has_personal_loan"] = True
    return cust


def generate_products(cust, months, rng):
    n = len(cust)
    z = cust["_z"].values
    inc = cust.gross_income_annual.values
    limit = np.clip(np.round(inc * rng.uniform(0.08, 0.24, n) * np.exp(-0.25 * z) / 250) * 250, 500, 18_000)
    apr = np.clip(np.round(22.9 + 3.5 * z + rng.normal(0, 1.5, n), 1), 18.9, 34.9)
    products = rng.choice(["Classic", "Rewards", "Balance Transfer", "Credit Builder"], n, p=[0.45, 0.25, 0.18, 0.12])
    products = np.where(cust.segment.values == "Young / thin file", "Credit Builder", products)
    open_months_ago = np.minimum(cust.tenure_months.values, rng.integers(6, 200, n))
    open_date = [(months[0] - pd.DateOffset(months=int(m))).date() for m in open_months_ago]
    acc = pd.DataFrame(dict(
        account_id=[f"A{c[1:]}" for c in cust.customer_id], customer_id=cust.customer_id,
        product=products, open_date=open_date, credit_limit_at_open=limit, apr_pct=apr,
        payment_method=rng.choice(["Direct debit - minimum", "Direct debit - full", "Direct debit - fixed", "Manual"],
                                  n, p=[0.28, 0.22, 0.12, 0.38]),
    ))
    # Other holdings (mortgage with us, personal loan)
    hold = []
    mort_mask = cust.housing_status.values == "Mortgage (with us)"
    for i in np.where(mort_mask)[0]:
        fix_end = months[0] + pd.DateOffset(months=int(rng.integers(-6, 60)))
        rtype = "Fixed" if rng.random() < 0.86 else "Tracker"
        bal = float(np.round(inc[i] * rng.uniform(2.0, 4.2), -3))
        hold.append(dict(customer_id=cust.customer_id.iat[i], product_type="Mortgage", rate_type=rtype,
                         original_rate_pct=round(rng.uniform(1.4, 2.6), 2) if rtype == "Fixed" else None,
                         fix_end_month=fix_end.date() if rtype == "Fixed" else None, balance=bal,
                         monthly_payment=round(bal * 0.0045, 0)))
    for i in np.where(cust.has_personal_loan.values)[0]:
        bal = float(np.round(rng.uniform(2_000, 20_000), -2))
        hold.append(dict(customer_id=cust.customer_id.iat[i], product_type="Personal loan", rate_type="Fixed",
                         original_rate_pct=round(rng.uniform(6, 14), 2), fix_end_month=None,
                         balance=bal, monthly_payment=round(bal / 48, 0)))
    hold = pd.DataFrame(hold)
    # Persona: Grace's fix ends December 2026, Lee has a business loan
    g = hold.index[(hold.customer_id == "C900003") & (hold.product_type == "Mortgage")]
    hold.loc[g, ["rate_type", "original_rate_pct", "fix_end_month", "balance", "monthly_payment"]] = [
        "Fixed", 1.69, pd.Timestamp("2026-12-01").date(), 265_000.0, 1_160.0]
    return acc, hold


# ---------------------------------------------------------------------------
# Monthly simulation
# ---------------------------------------------------------------------------
def simulate(cust, acc, hold, macro, months, rng):
    n = len(cust)
    T = len(months)
    ids = cust.customer_id.values
    persona_mask = np.isin(ids, list(PERSONAS))
    z = cust["_z"].values
    seg = cust.segment.values
    emp = cust.employment_type.values
    region = cust.region.values
    base_net = cust.gross_income_annual.values * 0.74 / 12
    has_ca = cust.has_current_account.values

    # Static behaviours
    limit = acc.credit_limit_at_open.values.astype(float)
    apr = acc.apr_pct.values / 100
    transactor = rng.random(n) < np.clip(0.38 - 0.12 * z, 0.05, 0.75)
    pay_ratio = np.clip(rng.beta(2, 9, n), 0.03, 0.6)
    card_share = rng.uniform(0.05, 0.22, n)
    essential_ratio = np.clip(0.50 + 0.07 * z + rng.normal(0, 0.06, n), 0.35, 0.82)
    resilience = np.exp(rng.normal(np.log(0.9), 0.9, n)) * np.exp(-0.35 * z)
    resilience = np.where(seg == "Mass affluent", resilience * 2.5, resilience)
    resilience = np.where(seg == "Young / thin file", resilience * 0.4, resilience)
    savings = base_net * resilience
    od_limit = np.where(has_ca & (rng.random(n) < 0.72), np.round(rng.uniform(250, 2500, n), -1), 0.0)
    gamble_base = np.where(rng.random(n) < 0.08, rng.uniform(0.005, 0.04, n), 0.0)
    bnpl_user = rng.random(n) < 0.18
    engagement = rng.beta(4, 2, n)  # propensity to engage with collections
    pay_priority = rng.normal(0, 0.9, n)  # unobserved: how much this card is prioritised over other bills
    payday = rng.integers(1, 29, n)

    # Mortgage / loan payments and refix shocks
    mort_pay = np.zeros(n)
    loan_pay = np.zeros(n)
    refix_month_idx = np.full(n, -1)
    refix_ratio = np.ones(n)
    idx_of = {c: i for i, c in enumerate(ids)}
    for _, h in hold.iterrows():
        i = idx_of[h.customer_id]
        if h.product_type == "Mortgage":
            mort_pay[i] = h.monthly_payment
            if h.rate_type == "Fixed" and h.fix_end_month is not None:
                fe = pd.Timestamp(h.fix_end_month)
                k = (fe.year - months[0].year) * 12 + fe.month - months[0].month
                if 0 <= k < T + 6:
                    refix_month_idx[i] = k
                    refix_ratio[i] = 1 + (4.6 - h.original_rate_pct) * 0.13
        else:
            loan_pay[i] = h.monthly_payment
    rent = np.where(cust.housing_status.values == "Rent", base_net * rng.uniform(0.25, 0.38, n), 0.0)
    rent_base = rent.copy()

    ext_debt = np.maximum(0, cust.gross_income_annual.values * rng.uniform(0, 0.35, n) * (1 + 0.35 * z))
    ext_debt = np.where(seg == "Young / thin file", ext_debt * 0.3, ext_debt)
    ext_accounts = np.clip(np.round(1 + ext_debt / 4000 + rng.poisson(1, n)), 0, 15)

    # Business (sole traders)
    st = seg == "Sole trader"
    turnover_base = np.where(st, cust.gross_income_annual.values * rng.uniform(2.4, 3.6, n) / 12, 0)
    cost_ratio = np.where(st, rng.uniform(0.62, 0.78, n), 0)
    biz_debt_service = np.where(st, turnover_base * rng.uniform(0.10, 0.17, n), 0)
    biz_shock = np.ones(n)

    # Shock state
    job_lost = np.zeros(n, bool)
    job_months = np.zeros(n)
    reemploy_in = np.zeros(n)
    cut_factor = np.ones(n)
    cut_left = np.zeros(n)
    life_left = np.zeros(n)
    gamble_esc = np.zeros(n)
    gamble_esc_on = np.zeros(n, bool)
    strategic_start = np.where((rng.random(n) < 0.006) & ~persona_mask, rng.integers(4, T, n), 10_000)
    cause = np.array(["None"] * n, dtype=object)
    vulnerable = cust.vulnerability_flag.values.copy()
    stress = np.zeros(n)
    cost_up = np.zeros(n)

    # Account state
    bal = np.where(transactor, limit * rng.uniform(0.02, 0.12, n), limit * rng.beta(2, 3, n))
    cycles = np.zeros(n, int)
    charged_off = np.zeros(n, bool)
    forb_left = np.zeros(n, int)
    fees_prev = np.zeros(n)
    ext_cycles = np.zeros(n, int)
    other_cycles = np.zeros(n, int)
    searches_hist = []
    newacc_hist = []
    min_only_streak = np.zeros(n, int)
    ccj = np.zeros(n, bool)
    sudden_on = np.zeros(n, bool)

    unemp_lookup = macro.set_index(["region", "month"]).unemployment_rate
    out = {k: [] for k in ["card", "ca", "bureau", "other", "biz", "coll", "forb", "notes", "truth"]}
    note_id = 0
    no_shock = persona_mask  # personas: no random shocks in history

    for t, m in enumerate(months):
        md = m.date()
        reg_unemp = np.array([unemp_lookup[(r, md)] for r in region])
        unemp_excess = reg_unemp - np.array([_REGION_BASE_UNEMP[r] for r in region])

        # ---- shocks -------------------------------------------------------
        employed = np.isin(emp, ["Employed full-time", "Employed part-time"])
        h_job = (0.0040 * np.exp(0.35 * z) * (1 + 0.45 * unemp_excess) * np.where(emp == "Employed part-time", 1.5, 1))
        new_job_loss = employed & ~job_lost & (rng.random(n) < h_job) & ~no_shock
        job_lost |= new_job_loss
        reemploy_in = np.where(new_job_loss, rng.geometric(0.10, n), reemploy_in)
        cause = np.where(new_job_loss & (cause == "None"), "Job loss", cause)
        job_months = np.where(job_lost, job_months + 1, 0)
        back = job_lost & (job_months > reemploy_in)
        job_lost &= ~back
        cut_factor = np.where(back, rng.uniform(0.75, 1.0, n), cut_factor)

        new_cut = (cut_left == 0) & (rng.random(n) < 0.0028 * np.exp(0.25 * z)) & ~no_shock & ~job_lost
        cut_factor = np.where(new_cut, rng.uniform(0.55, 0.85, n), cut_factor)
        cut_left = np.where(new_cut, rng.integers(3, 12, n), np.maximum(cut_left - 1, 0))
        cut_factor = np.where((cut_left == 0) & ~back & (cut_factor < 1), np.minimum(cut_factor + 0.1, 1), cut_factor)
        cause = np.where(new_cut & (cause == "None"), "Income reduction", cause)

        new_life = (life_left == 0) & (rng.random(n) < 0.0016) & ~no_shock
        life_left = np.where(new_life, rng.integers(6, 18, n), np.maximum(life_left - 1, 0))
        vulnerable |= new_life & (rng.random(n) < 0.7)
        cause = np.where(new_life & (cause == "None"), "Illness / life event", cause)

        new_gamble = (gamble_base > 0) & ~gamble_esc_on & (rng.random(n) < 0.010) & ~no_shock
        gamble_esc_on |= new_gamble
        gamble_esc = np.where(gamble_esc_on, np.minimum(gamble_esc + rng.uniform(0.03, 0.08, n), 0.55), 0)
        cause = np.where(new_gamble & (cause == "None"), "Gambling", cause)

        refix_now = refix_month_idx == t
        cost_up = np.where(refix_now, mort_pay * (refix_ratio - 1), cost_up)
        cause = np.where(refix_now & (refix_ratio > 1.25) & (cause == "None") & (resilience < 2), "Payment shock", cause)
        new_rent = (rent > 0) & (rng.random(n) < 0.004)
        rent = np.where(new_rent, rent * rng.uniform(1.08, 1.2, n), rent)

        strategic_now = t >= strategic_start
        cause = np.where((t == strategic_start), "Strategic / unwilling", cause)

        # ---- income -------------------------------------------------------
        sector = np.where(st & (rng.random(n) < 0.004) & ~no_shock, rng.uniform(0.6, 0.85, n), 1.0)
        biz_shock = np.minimum(biz_shock * sector + 0.01, 1.0) if t > 0 else biz_shock
        cause = np.where((sector < 1) & (cause == "None"), "Business downturn", cause)
        turnover = turnover_base * biz_shock * np.exp(rng.normal(0, 0.12, n)) * (1 - 0.03 * unemp_excess)
        ebitda = turnover * (1 - cost_ratio)
        dscr = np.where(st, ebitda / np.maximum(biz_debt_service, 1), np.nan)

        income = base_net * cut_factor
        income = np.where(job_lost & (job_months > 1), base_net * 0.18, income)  # benefits only
        income = np.where(life_left > 0, income * 0.75, income)
        income = np.where(emp == "Self-employed", base_net * np.exp(rng.normal(0, 0.18, n)) * cut_factor, income)
        profit_base = np.maximum(turnover_base * (1 - cost_ratio) - biz_debt_service, 1)
        income = np.where(st, base_net * np.clip((ebitda - biz_debt_service) / profit_base, 0, 2), income)
        income *= (1 + 0.002 * t) * np.exp(rng.normal(0, 0.04, n))  # wage drift, overtime and bonus noise
        salary_paid = employed & ~(job_lost & (job_months > 1))
        salary_amt = np.where(salary_paid, income, 0.0)
        salary_amt = np.where(job_lost & (job_months == 1), base_net * 1.6, salary_amt)  # final pay + redundancy

        # ---- spending, gap, stress ---------------------------------------
        gamble_share = gamble_base + gamble_esc
        gamble_spend = base_net * gamble_share
        essentials = base_net * essential_ratio * (1 + 0.0025 * t) + cost_up + (rent - rent_base)
        essentials = np.where(life_left > 0, essentials * 1.08, essentials)
        debt_payments = mort_pay + loan_pay + ext_debt * 0.03
        disposable = income - essentials - debt_payments - gamble_spend
        surplus = np.maximum(disposable, 0)
        gap = np.maximum(-disposable, 0)
        draw = np.minimum(gap, savings)
        savings = savings - draw + 0.35 * surplus
        unfunded = gap - draw
        gap_ratio = unfunded / np.maximum(base_net, 1)
        stress = 0.6 * stress + gap_ratio + 0.9 * np.maximum(gamble_share - 0.1, 0)
        stress = np.where(strategic_now, stress * 0.5, stress)

        # ---- card ----------------------------------------------------------
        active = ~charged_off
        headroom = np.maximum(limit - bal, 0)
        spend = base_net * card_share * np.exp(rng.normal(0, 0.25, n))
        spend += np.minimum(0.45 * unfunded, headroom * 0.8)
        cash_p = np.clip(0.008 + 0.30 * np.minimum(stress, 1.2) + 0.25 * (gamble_esc > 0.15), 0, 0.8)
        cash = np.where(rng.random(n) < cash_p, np.minimum(headroom * rng.uniform(0.1, 0.5, n), 50 + unfunded * 0.4), 0)
        bust = strategic_now & (t - strategic_start <= 1)
        spend = np.where(bust, spend + headroom * 0.6, spend)
        cash = np.where(bust, headroom * 0.3, cash)
        cash = np.round(cash, -1)
        revolve = ~transactor | (stress > 0.25)
        interest = np.where(revolve, bal * apr / 12, 0)
        stmt = np.where(active, bal + spend + cash + interest + fees_prev, 0)
        min_due = np.where(stmt > 0, np.maximum(np.minimum(stmt, 25), 0.01 * stmt + interest + fees_prev), 0)

        overlimit_prev = bal > limit
        p_miss = _sigmoid(-5.6 + 3.0 * np.minimum(stress, 2.5) + 0.55 * z + 2.0 * (gamble_esc > 0.2)
                          + 0.8 * overlimit_prev + 0.9 * (cycles > 0) + pay_priority)
        p_miss = np.maximum(p_miss, 0.006)  # forgetful payers
        dd_bounce = (acc.payment_method.str.startswith("Direct").values & has_ca & (stress > 0.7)
                     & (rng.random(n) < 0.35))
        # Sudden, unsignalled defaults (death, emigration, fraud without a bust-out): keeps models honest
        sudden = (rng.random(n) < 0.0009) & ~persona_mask & (cycles == 0)
        sudden_on = sudden_on | sudden
        cause = np.where(sudden & (cause == 'None'), 'Sudden / unexplained', cause)
        miss = (rng.random(n) < p_miss) | dd_bounce | (strategic_now & ~bust) | sudden_on
        in_forb = forb_left > 0
        miss = np.where(in_forb, False, miss)
        miss &= active & (stmt > 0)
        ratio_eff = pay_ratio * np.clip(1 - np.minimum(stress, 1), 0.05, 1)
        pay = np.where(transactor & (stress < 0.25), stmt, np.maximum(min_due, ratio_eff * stmt))
        pay = np.where(in_forb, np.round(min_due * rng.uniform(0.15, 0.5, n), 0), pay)
        partial = miss & (rng.random(n) < 0.15)
        pay = np.where(miss, np.where(partial, min_due * rng.uniform(0.2, 0.8, n), 0), pay)
        pay = np.minimum(pay, stmt)
        min_only = active & ~miss & ~in_forb & (pay <= min_due * 1.05) & (stmt > 50)
        min_only_streak = np.where(min_only, min_only_streak + 1, 0)

        cure_p = _sigmoid(1.2 - 2.8 * np.minimum(stress, 2) - 1.0 * strategic_now)
        cured = active & ~miss & (cycles > 0) & ~in_forb & (rng.random(n) < cure_p)
        cycles = np.where(miss, cycles + 1, np.where(cured, 0, cycles))
        new_co = active & (cycles >= 6)
        fees = np.where(miss, 12.0, 0) + np.where(stmt - pay > limit, 12.0, 0)
        bal = np.where(active, np.maximum(stmt - pay, 0), 0)
        overlimit = active & (bal > limit)

        # ---- forbearance & collections ------------------------------------
        cant_pay = np.isin(cause, ["Job loss", "Income reduction", "Illness / life event", "Payment shock", "Business downturn"])
        p_forb = np.where(cycles > 0, 0.16, np.where(stress > 0.9, 0.02, 0)) * np.where(cant_pay, 1.6, 0.4) * engagement
        new_forb = active & ~in_forb & (rng.random(n) < p_forb)
        forb_len = rng.integers(3, 7, n)
        forb_left = np.where(new_forb, forb_len, np.maximum(forb_left - 1, 0))
        for i in np.where(new_forb)[0]:
            out["forb"].append(dict(customer_id=ids[i], account_id=acc.account_id.iat[i], start_month=md,
                                    arrangement_type=rng.choice(["Reduced payment plan", "Payment holiday", "Interest and charges freeze", "Breathing Space"],
                                                                p=[0.55, 0.15, 0.22, 0.08]),
                                    duration_months=int(forb_len[i]), income_expenditure_completed=bool(rng.random() < 0.85)))
        coll_idx = np.where(active & (cycles > 0))[0]
        for i in coll_idx:
            contacted = rng.random() < 0.55 + 0.35 * engagement[i]
            rpc = contacted and rng.random() < 0.8
            willing = 0.75 if cant_pay[i] else (0.25 if strategic_now[i] or cause[i] == "Gambling" else 0.5)
            ptp = rpc and rng.random() < willing
            reason = None
            if rpc and rng.random() < 0.75:
                true = cause[i] if cause[i] != "None" else rng.choice(["Overcommitted", "Forgot / admin"])
                if rng.random() < 0.15:
                    true = rng.choice(["Overcommitted", "Job loss", "Dispute", "Refused to discuss"])
                reason = {"Strategic / unwilling": rng.choice(["Refused to discuss", "Dispute"]), "Gambling": "Overcommitted"}.get(true, true)
            out["coll"].append(dict(customer_id=ids[i], account_id=acc.account_id.iat[i], month=md,
                                    cycles_delinquent=int(cycles[i]), contact_attempts=int(rng.integers(1, 9)),
                                    right_party_contact=bool(rpc), promise_to_pay=bool(ptp),
                                    ptp_amount=float(round(min_due[i] * rng.uniform(0.5, 1.5), 0)) if ptp else None,
                                    ptp_kept=bool(ptp and rng.random() < (0.7 if cant_pay[i] else 0.35)) if ptp else None,
                                    arrears_reason=reason))

        # ---- current account ----------------------------------------------
        s1 = np.minimum(stress, 1.5)
        od_days = np.where(od_limit > 0, np.clip(rng.poisson(0.6 + 22 * s1 / 1.5), 0, 31), 0)
        od_days = np.where(od_limit > 0, np.maximum(od_days, np.where(rng.random(n) < 0.18, rng.integers(1, 6, n), 0)), 0)
        od_peak = np.where(od_limit > 0, np.minimum(1.0, 0.05 * rng.random(n) + 0.9 * s1 / 1.5 + 0.05), 0)
        rdd = rng.poisson(0.015 + 1.4 * np.maximum(stress - 0.5, 0))
        bnpl = np.where(bnpl_user | (stress > 0.5), rng.uniform(0, 60, n) + 120 * s1, 0)
        payday_loan = (stress > 0.7) & (rng.random(n) < 0.15)
        late_salary = np.where(salary_paid & (rng.random(n) < 0.02 + 0.05 * (emp == "Employed part-time")), rng.integers(1, 8, n), 0)
        ca_rows = pd.DataFrame(dict(
            customer_id=ids, month=md, salary_credit_amt=np.round(salary_amt, 2),
            other_income_credit_amt=np.round(np.where(salary_paid, 0, income), 2),
            salary_day=np.where(salary_paid, payday, None), salary_days_late=late_salary,
            overdraft_limit=od_limit, overdraft_days=od_days, overdraft_peak_util=np.round(od_peak, 3),
            returned_dd_count=rdd, gambling_spend=np.round(gamble_spend * np.exp(rng.normal(0, 0.2, n)), 2),
            bnpl_repayments=np.round(bnpl, 2), payday_lender_payment_flag=payday_loan,
            essential_spend=np.round(essentials, 2),
            eom_balance=np.round(np.where(stress > 0.3, -od_peak * od_limit, savings * 0.3 + rng.uniform(50, 900, n)), 2),
        ))
        out["ca"].append(ca_rows[has_ca])

        # ---- bureau (external) ------------------------------------------
        searches = rng.poisson(0.12 + 1.1 * np.minimum(stress, 1.5) + 0.8 * bust)
        new_acc = rng.binomial(searches, 0.25)
        searches_hist.append(searches)
        newacc_hist.append(new_acc)
        ext_debt = np.maximum(0, ext_debt * 0.985 + 0.35 * unfunded + new_acc * rng.uniform(500, 3000, n))
        ext_accounts = ext_accounts + new_acc
        p_ext = _sigmoid(-6.1 + 2.7 * np.minimum(stress, 2.5) + 0.5 * z + rng.normal(0, 0.4, n))
        ext_miss = rng.random(n) < p_ext
        ext_cure = (ext_cycles > 0) & ~ext_miss & (rng.random(n) < _sigmoid(1.3 - 2.2 * np.minimum(stress, 2)))
        ext_cycles = np.where(ext_miss, np.minimum(ext_cycles + 1, 6), np.where(ext_cure, 0, ext_cycles))
        ccj |= (ext_cycles >= 4) & (rng.random(n) < 0.1)
        out["bureau"].append(pd.DataFrame(dict(
            customer_id=ids, month=md, total_external_debt=np.round(ext_debt, 0),
            external_card_limit_total=np.round(ext_debt * rng.uniform(1.3, 2.5, n) + 500, -2),
            active_external_accounts=ext_accounts.astype(int), searches_this_month=searches,
            new_accounts_this_month=new_acc, worst_external_status=ext_cycles,
            ccj_flag=ccj, insolvency_flag=ccj & (ext_cycles >= 6) & (rng.random(n) < 0.05),
            bureau_score=np.clip(np.round(760 - 55 * z - 70 * np.minimum(stress, 2) - 45 * ext_cycles
                                          - 120 * ccj - 6 * searches + rng.normal(0, 18, n)), 0, 999).astype(int),
        )))

        # ---- other internal products --------------------------------------
        holder = (mort_pay > 0) | (loan_pay > 0)
        p_oth = _sigmoid(-7.0 + 2.6 * np.minimum(stress, 2.5) + 0.5 * z)
        o_miss = holder & (rng.random(n) < p_oth)
        other_cycles = np.where(o_miss, other_cycles + 1, np.where(rng.random(n) < 0.5, 0, other_cycles))
        out["other"].append(pd.DataFrame(dict(
            customer_id=ids, month=md, mortgage_payment_due=np.round(mort_pay + cost_up, 2),
            loan_payment_due=loan_pay, other_product_cycles_delinquent=np.where(holder, other_cycles, 0)))[holder])

        # ---- business (sole traders) ------------------------------------
        if st.any():
            out["biz"].append(pd.DataFrame(dict(
                customer_id=ids, month=md, turnover=np.round(turnover, 0), operating_costs=np.round(turnover * cost_ratio, 0),
                ebitda=np.round(ebitda, 0), debt_service=np.round(biz_debt_service, 0), dscr=np.round(dscr, 2),
                business_overdraft_days=np.clip(rng.poisson(1 + 18 * np.maximum(1.3 - np.nan_to_num(dscr, nan=3.0), 0)), 0, 31)))[st])

        # ---- card output ----------------------------------------------------
        status = np.where(charged_off, "Charged off", np.where(new_co, "Charged off",
                 np.where(in_forb, "Forbearance", np.where(cycles > 0, "Arrears", "Up to date"))))
        out["card"].append(pd.DataFrame(dict(
            account_id=acc.account_id.values, customer_id=ids, month=md, credit_limit=limit,
            statement_balance=np.round(stmt, 2), closing_balance=np.round(bal, 2),
            purchases=np.round(np.where(active, spend, 0), 2), cash_advance_amt=np.where(active, cash, 0),
            interest_charged=np.round(np.where(active, interest, 0), 2), fees_charged=np.where(active, fees_prev, 0),
            minimum_due=np.round(min_due, 2), payment_amount=np.round(np.where(active, pay, 0), 2),
            missed_payment_flag=miss, returned_payment_flag=dd_bounce & active,
            overlimit_flag=overlimit, cycles_delinquent=np.where(charged_off, 6, cycles),
            account_status=status, forbearance_flag=in_forb | new_forb,
        )))
        out["truth"].append(pd.DataFrame(dict(customer_id=ids, month=md, stress_index=np.round(stress, 3),
                                              distress_cause=cause, unfunded_gap=np.round(unfunded, 0),
                                              savings_buffer=np.round(savings, 0))))

        # ---- contact notes ------------------------------------------------
        p_note = 0.02 + np.where(cant_pay & (stress > 0.35), 0.22, 0) + np.where(cycles > 0, 0.35, 0) \
            + np.where(vulnerable & (stress > 0.2), 0.1, 0) + np.where(np.isin(cause, ["Gambling", "Strategic / unwilling"]) & (cycles > 0), 0.1, 0)
        for i in np.where(rng.random(n) < p_note)[0]:
            note_id += 1
            out["notes"].append(_make_note(note_id, ids[i], m, cause[i], stress[i], cycles[i], vulnerable[i],
                                           strategic_now[i], rng))

        charged_off |= new_co
        fees_prev = fees
        limit = np.where((t % 6 == 5) & (stress < 0.05) & (cycles == 0) & (bal / np.maximum(limit, 1) > 0.5)
                         & (rng.random(n) < 0.25) & ~charged_off & ~persona_mask, np.round(limit * 1.25, -2), limit)

    card = pd.concat(out["card"], ignore_index=True)
    card["utilisation"] = np.round(card.statement_balance / card.credit_limit.replace(0, np.nan), 4)  # statement balance / limit
    return dict(
        card_monthly=card,
        current_account_monthly=pd.concat(out["ca"], ignore_index=True),
        bureau_monthly=pd.concat(out["bureau"], ignore_index=True),
        other_product_monthly=pd.concat(out["other"], ignore_index=True),
        business_monthly=pd.concat(out["biz"], ignore_index=True) if out["biz"] else pd.DataFrame(),
        collections_events=pd.DataFrame(out["coll"]),
        forbearance_events=pd.DataFrame(out["forb"]),
        contact_notes=pd.DataFrame(out["notes"]),
        generator_truth=pd.concat(out["truth"], ignore_index=True),
    )


# ---------------------------------------------------------------------------
# Contact notes (free text that a sentiment model or LLM can read)
# ---------------------------------------------------------------------------
_NOTE_BANK = {
    "hardship": [
        "Cust called worried about upcoming payments. Lost job last month, looking for work. Asked what options are available.",
        "Customer explained hours have been cut at work and they are struggling to cover rent and bills. Wants to keep up but can't manage full amount.",
        "Inbound chat: 'I don't know how I am going to pay this month, everything has gone up'. Offered I&E, customer agreed.",
        "Cust upset, partner has left and income has halved. Asked if payments can be reduced for a few months.",
        "Customer said they have been using the card for food shopping since redundancy. Signposted to free debt advice.",
        "Called to say mortgage payment has jumped after fixed rate ended and they are falling behind on other bills.",
        "Business has been very quiet, takings down a lot. Customer worried about paying the card and the business loan.",
    ],
    "vulnerability": [
        "Customer disclosed ongoing illness and is off work on statutory sick pay. Very anxious about debts. Vulnerability recorded.",
        "Cust mentioned caring for a parent full time now and finding it hard to keep track of bills. Agreed to send summary by post.",
        "Customer was tearful on the call, said they are not sleeping because of money worries. Referred to specialist support team.",
    ],
    "refusal": [
        "Customer refused to discuss account, said they will pay when they are ready. Call ended by customer.",
        "Cust disputes interest charges and says they won't pay until refunded. No affordability issues mentioned.",
        "Outbound call. Third party answered, customer unavailable. Third attempt this week, no callback received.",
        "Customer said other bills come first and this card is low priority. Declined I&E.",
        "Customer aggressive on the call, said stop calling. Promise to pay given but customer said it may not happen.",
    ],
    "neutral": [
        "Customer called to update address details. No other issues.",
        "Cust asked about how interest is calculated on cash withdrawals. Explained.",
        "Customer requested a replacement card, original damaged.",
        "Asked about balance transfer offers. Not currently eligible, explained criteria.",
        "Customer queried a transaction at an online retailer, confirmed genuine.",
    ],
    "positive": [
        "Customer thanked agent for help setting up a payment plan, says things are getting back on track with new job.",
        "Cust called to say they will clear the arrears on Friday when paid. Very positive call.",
        "Customer pleased with the app and asked about increasing their limit.",
    ],
}


def _make_note(note_id, cid, month, cause, stress, cycles, vulnerable, strategic, rng):
    if strategic or (cause == "Gambling" and cycles > 0 and rng.random() < 0.6):
        cat = "refusal"
    elif vulnerable and stress > 0.2 and rng.random() < 0.5:
        cat = "vulnerability"
    elif cause != "None" and stress > 0.35:
        cat = "hardship" if rng.random() < 0.8 else "refusal"
    elif cycles > 0:
        cat = rng.choice(["hardship", "refusal", "positive"], p=[0.35, 0.35, 0.3])
    else:
        cat = rng.choice(["neutral", "positive"], p=[0.8, 0.2])
    text = rng.choice(_NOTE_BANK[cat])
    ts = month + pd.Timedelta(days=int(rng.integers(0, 27)), hours=int(rng.integers(8, 20)), minutes=int(rng.integers(0, 59)))
    return dict(note_id=f"N{note_id:07d}", customer_id=cid, created_ts=ts,
                channel=rng.choice(["Phone", "Chat", "Branch", "Email", "Complaint"], p=[0.5, 0.3, 0.07, 0.1, 0.03]),
                direction="Outbound" if cycles > 0 and rng.random() < 0.6 else "Inbound",
                note_text=text)


# ---------------------------------------------------------------------------
# Data defects: injected deliberately so the DQ layer has something to find
# ---------------------------------------------------------------------------
def inject_defects(dfs, rng):
    d = {k: v.copy() for k, v in dfs.items()}
    c = d["customers"]
    c.loc[c.sample(frac=0.006, random_state=1).index, "gross_income_annual"] = np.nan
    c.loc[c.sample(n=3, random_state=2).index, "age"] = [151, -1, 0]
    d["customers"] = pd.concat([c, c.sample(n=12, random_state=3)], ignore_index=True)  # duplicates

    cm = d["card_monthly"]
    cm.loc[cm.sample(frac=0.003, random_state=4).index, "closing_balance"] = np.nan
    bad = cm.sample(n=20, random_state=5).index
    cm.loc[bad, "credit_limit"] = 0
    cm.loc[cm.sample(n=8, random_state=6).index, "payment_amount"] = -50.0
    orphans = cm.sample(n=15, random_state=7).copy()
    orphans["account_id"] = [f"AX{i:05d}" for i in range(15)]
    d["card_monthly"] = pd.concat([cm, orphans, cm.sample(n=25, random_state=8)], ignore_index=True)

    b = d["bureau_monthly"]
    last = b.month.max()
    ni = d["customers"].drop_duplicates("customer_id").set_index("customer_id").region
    late = b[(b.month == last) & (b.customer_id.map(ni).isin(["Scotland"]))].sample(frac=0.6, random_state=9).index
    d["bureau_monthly"] = b.drop(late)  # late bureau file for part of Scotland

    mac = d["macro_regional_monthly"]
    d["macro_regional_monthly"] = mac[~((mac.region == "Northern Ireland") & (mac.month == mac.month.max()))]

    ca = d["current_account_monthly"]
    ca.loc[ca.sample(n=10, random_state=10).index, "salary_credit_amt"] = -1.0
    n_ = d["contact_notes"]
    n_.loc[n_.sample(n=min(6, len(n_)), random_state=11).index, "note_text"] = ""
    return d


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def generate_all(n_customers=N_CUSTOMERS, seed=SEED, with_defects=True):
    rng = np.random.default_rng(seed)
    months = month_list()
    macro, forecast = generate_macro(months, rng)
    cust = generate_customers(n_customers, rng)
    acc, hold = generate_products(cust, months, rng)
    sim = simulate(cust, acc, hold, macro, months, rng)
    dfs = dict(customers=cust.drop(columns=["_z"]), card_accounts=acc, product_holdings=hold,
               macro_regional_monthly=macro, macro_forecast=forecast, **sim)
    dfs["personas"] = pd.DataFrame([dict(customer_id=k, **v) for k, v in PERSONAS.items()])
    if with_defects:
        dfs = inject_defects(dfs, rng)
    return dfs


# File formats deliberately vary, as they would from real source systems
LANDING_FORMATS = {
    "customers": ("csv", "crm/customers.csv"),
    "card_accounts": ("csv", "cards/card_accounts.csv"),
    "card_monthly": ("parquet", "cards/card_monthly.parquet"),
    "current_account_monthly": ("parquet", "current_accounts/current_account_monthly.parquet"),
    "product_holdings": ("csv", "lending/product_holdings.csv"),
    "other_product_monthly": ("parquet", "lending/other_product_monthly.parquet"),
    "business_monthly": ("parquet", "business_banking/business_monthly.parquet"),
    "bureau_monthly": ("psv", "bureau/bureau_monthly.psv"),
    "macro_regional_monthly": ("csv", "macro/macro_regional_monthly.csv"),
    "macro_forecast": ("csv", "macro/macro_forecast.csv"),
    "collections_events": ("csv", "collections/collections_events.csv"),
    "forbearance_events": ("csv", "collections/forbearance_events.csv"),
    "contact_notes": ("jsonl", "contact_centre/contact_notes.jsonl"),
    "generator_truth": ("parquet", "_truth/generator_truth.parquet"),
    "personas": ("csv", "_truth/personas.csv"),
}


def write_landing(dfs, root):
    written = {}
    for name, (fmt, rel) in LANDING_FORMATS.items():
        df = dfs[name]
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if fmt == "csv":
            df.to_csv(path, index=False)
        elif fmt == "psv":
            df.to_csv(path, index=False, sep="|")
        elif fmt == "parquet":
            df.to_parquet(path, index=False)
        elif fmt == "jsonl":
            with open(path, "w") as f:
                for rec in df.to_dict("records"):
                    rec = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in rec.items()}
                    f.write(json.dumps(rec) + "\n")
        written[name] = (path, len(df))
    return written
