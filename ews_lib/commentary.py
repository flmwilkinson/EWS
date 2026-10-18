"""AI-written commentary with deterministic fallbacks.

Each commentary has a `build_*` function that returns (prompt, fallback):

    prompt    the user message for Claude: a task plus structured facts as JSON
    fallback  template text built from the same facts, used if the model is unavailable

The LLM never sees raw customer data beyond the structured facts passed in, and
the system prompt (ews_lib.llm.SYSTEM) tells it to use only those facts. The
older `*_commentary(facts, llm)` functions are kept for the Databricks notebooks.
"""
from __future__ import annotations

import json

SYSTEM_STYLE = (
    "You are a credit risk analyst at a UK retail bank writing for the credit risk committee. "
    "Write plain, specific British English. Use only the facts provided; do not invent numbers. "
    "No headings, no bullet points, no marketing language."
)


def _j(obj, limit=7000):
    return json.dumps(obj, default=str)[:limit]


def _ask(llm, prompt, fallback):
    if llm is None:
        return fallback, "template"
    try:
        text = llm(f"{SYSTEM_STYLE}\n\n{prompt}")
        if text and len(text.strip()) > 20:
            return text.strip(), "llm"
    except Exception:
        pass
    return fallback, "template"


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------
def build_dq(facts: dict):
    crit = facts["critical_failures"]
    other = facts["other_failures"]
    lines = [f"{facts['rules_run']} data quality rules ran; {facts['passed']} passed and {facts['failed']} failed."]
    if crit:
        lines.append("Critical: " + "; ".join(f"{c['description'].lower()} failed for {c['records_failed']:,} records "
                                              f"({c['fail_rate']:.2%}), remediated by '{c['action'].lower()}'" for c in crit) + ".")
    if other:
        worst = sorted(other, key=lambda x: -x["fail_rate"])[:3]
        lines.append("Largest other issues: " + "; ".join(f"{c['description'].lower()} ({c['records_failed']:,} records, {c['fail_rate']:.2%})" for c in worst) + ".")
    stale = [r for r in facts["remediation"] if "stale" in r["action"].lower() or "forward" in r["action"].lower()]
    if stale:
        lines.append("Stale inputs carried forward: " + "; ".join(f"{r['table_name']} ({r['rows_affected']:,} rows)" for r in stale) +
                     ". Scores for affected customers use last month's bureau or macro data and are flagged.")
    fallback = " ".join(lines)
    prompt = ("Write a 130-170 word data quality commentary for this month's early warning system run. Open with a clear "
              "verdict on whether the data is fit for scoring. Then say which issues matter for model outputs and why, and "
              "what each data owner should fix at source.\n\nFacts (JSON):\n" + _j(facts))
    return prompt, fallback


def dq_commentary(facts: dict, llm=None):
    return _ask(llm, *build_dq(facts))


# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------
def build_monitoring(facts: dict):
    red = [m for m in facts["metrics"] if m["status"] == "Red"]
    amber = [m for m in facts["metrics"] if m["status"] == "Amber"]
    lines = [f"Champion model: {facts['champion']}. Latest matured out-of-time Gini {facts['latest_gini']:.3f} "
             f"against {facts['dev_gini']:.3f} at development."]
    lines.append(f"Score PSI against development is {facts['score_psi']:.3f}.")
    if red or amber:
        lines.append("Metrics outside appetite: " + "; ".join(f"{m['metric']} {m['value']:.3g} ({m['status'].lower()})" for m in red + amber) + ".")
    lines.append(f"The watchlist holds {facts['watchlist_size']:,} customers ({facts['watchlist_share']:.1%} of the book); "
                 f"{facts['escalations']:,} escalated and {facts['de_escalations']:,} de-escalated this month.")
    lines.append(f"Median warning lead time before 60+ days past due was {facts['median_lead_months']:.0f} months, and "
                 f"{facts['bad_capture']:.0%} of customers who went bad were on the watchlist beforehand.")
    if facts.get("drifting_features"):
        lines.append("Largest input shifts: " + ", ".join(f"{d['feature']} (CSI {d['csi']:.2f})" for d in facts["drifting_features"][:3]) + ".")
    fallback = " ".join(lines)
    prompt = ("Write a 160-210 word monitoring commentary on the early warning system for the model risk committee. Cover "
              "discrimination, calibration, stability, watchlist movements and early warning effectiveness against appetite. "
              "Finish by saying what needs action, if anything, and who owns it (model owner, data owner or credit strategy).\n\n"
              "Facts (JSON):\n" + _j(facts))
    return prompt, fallback


def monitoring_commentary(facts: dict, llm=None):
    return _ask(llm, *build_monitoring(facts))


# ---------------------------------------------------------------------------
# Model comparison
# ---------------------------------------------------------------------------
def build_model_comparison(results, champion, reason):
    r = results.set_index("model")
    best = results.sort_values("gini", ascending=False).iloc[0]
    sc = r.loc["Logistic regression scorecard"]
    ch = r.loc[champion]
    nn = r.loc["Neural network (MLP)"] if "Neural network (MLP)" in r.index else None
    lines = [f"{best.model} has the highest out-of-time Gini ({best.gini:.3f}). The champion is {champion} "
             f"({ch.gini:.3f}). Reason: {reason}."]
    lines.append(f"The logistic scorecard reaches {sc.gini:.3f}, {100 * (ch.gini - sc.gini):.1f} Gini points behind the champion, "
                 f"with every point traceable to a binned characteristic; it stays as the benchmark challenger.")
    if nn is not None:
        lines.append(f"The neural network drops from {nn.dev_gini:.3f} in development to {nn.gini:.3f} out of time, "
                     f"the largest fall of any model, which is typical of tabular credit data without sequence features.")
    lines.append(f"The weighted scoring model ({r.loc['Weighted scoring model'].gini:.3f}) needs no outcome data and is the "
                 f"fallback for new products; the single decision tree ({r.loc['Decision tree'].gini:.3f}) is best kept for "
                 f"segmentation and strategy design.")
    fallback = " ".join(lines)
    facts = results[["model", "dev_gini", "gini", "ks", "brier", "top_decile_capture", "calibration_ratio",
                     "psi_vs_dev", "explainability", "governance_effort"]].to_dict("records")
    prompt = ("Write a 160-210 word comparison of six credit risk models built for an early warning system, for a model "
              "development team choosing between a traditional scorecard and machine learning. Explain the trade-off between "
              "discrimination, calibration, stability and explainability, call out overfitting where dev and out-of-time Gini "
              f"diverge, and justify the champion choice: {champion} ({reason}).\n\nResults (JSON):\n" + _j(facts))
    return prompt, fallback


def model_comparison_commentary(results, champion, reason, llm=None):
    return _ask(llm, *build_model_comparison(results, champion, reason))


# ---------------------------------------------------------------------------
# Portfolio executive summary
# ---------------------------------------------------------------------------
def build_portfolio(facts: dict):
    t = facts["tiers"]
    lines = [f"As at {facts['as_at']}, {facts['watchlist']:,} of {facts['customers']:,} customers "
             f"({facts['watchlist'] / max(facts['customers'], 1):.1%}) are on the watchlist and {t.get('Collections', 0):,} are in collections."]
    lines.append(f"Early intervention holds {t.get('Early intervention', 0):,} customers. Balance at risk in Watch or worse is "
                 f"£{facts['balance_at_risk_gbp'] / 1e6:.1f}m.")
    if facts.get("top_triggers"):
        lines.append("The most frequent signals are " + ", ".join(f"{x['name'].lower()} ({x['customers']:,})" for x in facts["top_triggers"][:3]) + ".")
    if facts.get("top_regions"):
        lines.append("Concentration is highest in " + ", ".join(f"{x['region']} ({x['share']:.1%} on watch)" for x in facts["top_regions"][:2]) + ".")
    fallback = " ".join(lines)
    prompt = ("Write a 140-180 word executive summary of the retail card book's early warning position for the chief risk "
              "officer. Lead with the headline movement, then the main drivers, regional and segment concentrations, and the "
              "forward-looking risks that appear in the facts (such as payment shocks). End with the two actions that matter most. "
              "Only write about what the facts contain: never mention that a figure is missing or unavailable.\n\n"
              "Facts (JSON):\n" + _j(facts))
    return prompt, fallback


# ---------------------------------------------------------------------------
# Customer summary (why flagged, what to do)
# ---------------------------------------------------------------------------
def customer_facts(r: dict, fired_names: list) -> dict:
    keep = ["customer_id", "tier_name", "tier_reason", "movement", "risk_band", "pd_6m", "p_arrears_3m",
            "p_forbearance_6m", "propensity_to_pay", "p_cant_pay", "top_drivers", "util", "util_chg_3m",
            "salary_missing_3m", "income_chg_vs_6m", "od_days", "searches_3m", "ext_worst_6m", "dsr", "dscr",
            "gambling_share", "hardship_notes_3m", "refusal_notes_3m", "refix_shock_fwd", "pay_lean", "segment", "region",
            "limit_and_activation", "pricing", "exposure", "offers_engagement", "support", "collections"]
    f = {k: r.get(k) for k in keep if k in r}
    f["signals"] = fired_names
    return f


def _recommended(f):
    for k in ("collections", "support", "limit_and_activation"):
        v = f.get(k)
        if v and v != "None" and not str(v).startswith("Pre-delinquency"):
            return v
    return f.get("limit_and_activation")


def build_customer(facts: dict, events: list | None = None):
    pd6 = facts.get("pd_6m") or 0
    sig = facts.get("signals") or []
    lean = {"Can't pay": " Signals point to affordability (can't pay) rather than willingness.",
            "Won't pay": " Signals point to willingness (won't pay) more than affordability."}.get(facts.get("pay_lean"), "")
    fallback = (f"{facts.get('tier_name')}: {facts.get('tier_reason', '')}. 6-month PD {pd6:.1%}, band "
                f"{facts.get('risk_band')}. Signals: {', '.join(sig[:5]) if sig else 'none'}. Main model drivers: "
                f"{facts.get('top_drivers') or 'n/a'}.{lean} Recommended: {_recommended(facts)}.")
    ev = f"\n\nRecent events (most recent last):\n{_j(events[-12:], 2500)}" if events else ""
    prompt = ("Write a 80-110 word note for a credit risk analyst on this customer's early warning position: the tier and "
              "why, what changed, whether the evidence suggests can't pay or won't pay (and how confident that is), and the "
              "recommended next action. If the customer is Performing, say so in one sentence and name what would change it. "
              "Refer to the customer as 'the customer'.\n\nFacts (JSON):\n" + _j(facts) + ev)
    return prompt, fallback


def customer_summary(facts: dict, llm=None):
    return _ask(llm, *build_customer(facts))
