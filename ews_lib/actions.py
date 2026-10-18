"""Decision matrix: what each business area does with the EWS outputs.

One set of signals, six consumers:
  approval / activation and limits, pricing, exposure management,
  offers and engagement, support and early intervention, collections treatment.

Outcomes are illustrative policy, written to show the mechanics. A client build
replaces them with the bank's own credit policy and treatment strategies, and
should be reviewed against Consumer Duty, CONC 6.7 (persistent debt), CONC 7
(arrears, default and recovery) and FG21/1 (vulnerable customers).
"""
from __future__ import annotations

import pandas as pd

USE_CASES = ["limit_and_activation", "pricing", "exposure", "offers_engagement", "support", "collections"]
USE_CASE_LABELS = {
    "limit_and_activation": "Approval, activation and limits",
    "pricing": "Pricing",
    "exposure": "Exposure management",
    "offers_engagement": "Offers and engagement",
    "support": "Support and early intervention",
    "collections": "Collections treatment",
}


def pay_lean(p, r=None):
    """Can't pay vs won't pay.

    What the customer has told us overrides the model: hardship disclosed or salary
    stopped means can't pay; refusal or dispute with no hardship means won't pay.
    Otherwise the classifier decides (base rate in collections is ~80% can't pay).
    """
    r = r or {}
    hardship = (r.get("hardship_notes_3m") or 0) > 0 or (r.get("salary_missing_3m") or 0) > 0
    refusal = (r.get("refusal_notes_3m") or 0) > 0
    if hardship:
        return "Can't pay"
    if refusal:
        return "Won't pay"
    if p is None or p != p:
        return "Unclear"
    return "Can't pay" if p >= 0.80 else ("Won't pay" if p <= 0.55 else "Unclear")


def decide(r: dict) -> dict:
    tier = int(r.get("tier", 0))
    band = r.get("risk_band", "C")
    util = r.get("util", 0) or 0
    headroom = r.get("headroom_gbp", 0) or 0
    lean = pay_lean(r.get("p_cant_pay"), r)
    cant = lean == "Can't pay"
    ptp = r.get("propensity_to_pay")
    vulnerable = bool(r.get("vulnerability_flag")) or (r.get("vulnerability_notes_3m", 0) or 0) > 0
    consent = bool(r.get("marketing_consent", True))
    good_band = band in ("A", "B")
    d = {}

    # 1. Approval, activation and limits
    income_up = (r.get("income_chg_vs_6m") or 0) >= 0.10
    if tier == 0 and good_band and (util >= 0.3 or income_up):
        d["limit_and_activation"] = "Eligible for credit limit increase (pre-approved)"
    elif tier == 0:
        d["limit_and_activation"] = "Standard: no change"
    elif tier == 1:
        d["limit_and_activation"] = "Hold: no limit increases while monitored"
    elif tier == 2:
        d["limit_and_activation"] = "Hold limit; decline new credit applications pending review"
    elif tier == 3:
        d["limit_and_activation"] = "Freeze limit; no new lending"
    else:
        d["limit_and_activation"] = "Account blocked to new spend"

    # 2. Pricing
    if tier == 0 and good_band:
        d["pricing"] = "Eligible for promotional rate on balance transfers"
    elif tier <= 1:
        d["pricing"] = "Standard risk-based APR"
    elif tier == 2:
        d["pricing"] = "No upward re-pricing; fair value check"
    else:
        d["pricing"] = "Consider interest and charges freeze" if cant else "No upward re-pricing"

    # 3. Exposure management
    if tier == 2 and util < 0.5 and headroom > 1500 and not vulnerable:
        d["exposure"] = "Review: reduce unused limit (with notice)"
    elif tier == 2:
        d["exposure"] = "Monitor headroom weekly"
    elif tier == 3:
        d["exposure"] = "Block cash advances; stop limit utilisation growth"
    elif tier >= 4:
        d["exposure"] = "Exposure capped at current balance"
    else:
        d["exposure"] = "No action"

    # 4. Offers and engagement
    if tier == 0 and good_band and consent:
        d["offers_engagement"] = "Target: balance transfer or product upgrade offer"
    elif tier == 0 and consent:
        d["offers_engagement"] = "Standard marketing eligible"
    elif tier == 1:
        d["offers_engagement"] = "Suppress credit offers; budgeting tools content"
    else:
        d["offers_engagement"] = "Suppress all credit marketing"

    # 5. Support and early intervention
    refix = (r.get("refix_shock_fwd") or 0) >= 0.15
    gambling_flag = (r.get("gambling_share") or 0) >= 0.10
    if vulnerable and tier >= 2:
        d["support"] = "Route to specialist support team (vulnerability)"
    elif refix and 1 <= tier <= 3:
        d["support"] = "Proactive contact before the rate change: product transfer, term extension or payment plan options"
    elif tier == 3 and cant:
        d["support"] = "Proactive call: income and expenditure, offer reduced payment plan, signpost free debt advice"
    elif tier == 3:
        d["support"] = "Proactive contact within 5 days; payment date change and reminders"
    elif tier == 2:
        d["support"] = "Digital nudge: budgeting tools, payment reminders, payment date alignment to pay day"
    elif gambling_flag and tier >= 1:
        d["support"] = "Offer gambling spend block; signpost gambling support services"
    else:
        d["support"] = "None"

    # 6. Collections treatment
    gambling = (r.get("gambling_share") or 0) >= 0.10
    if tier == 5:
        d["collections"] = "Recoveries: default notice process, debt sale review"
    elif tier == 4:
        if vulnerable:
            d["collections"] = "Specialist collections team; breathing space checks"
        elif lean == "Can't pay":
            d["collections"] = "Forbearance pathway: I&E, affordable repayment plan, interest freeze"
        elif lean == "Won't pay" or (ptp is not None and ptp < 0.3):
            d["collections"] = "Escalated contact strategy: agent call within 48 hours, firm arrears letter"
        elif ptp is not None and ptp >= 0.6:
            d["collections"] = "Self-cure: app and SMS reminder, no call for 7 days"
        else:
            d["collections"] = "Agent call within 3 days; promise to pay with reminder"
        if gambling:
            d["collections"] += "; offer gambling block and signpost gambling support"
    elif tier == 3:
        d["collections"] = "Pre-delinquency: payment reminder 3 days before due date"
    else:
        d["collections"] = "None"
    return d


def decide_frame(df: pd.DataFrame) -> pd.DataFrame:
    recs = df.to_dict("records")
    out = pd.DataFrame([decide(r) for r in recs], index=df.index)
    out["pay_lean"] = [pay_lean(r.get("p_cant_pay"), r) for r in recs]
    return pd.concat([df, out], axis=1)
