# %% [markdown]
# # 10 · Monitoring and portfolio commentary (AI)
#
# Two paragraphs for the committee pack, written by the AI from structured facts only:
#
# 1. **Model risk commentary**: discrimination, calibration, stability, watchlist movements and early warning
#    effectiveness against appetite, with owners for any action.
# 2. **Executive summary for the CRO**: the portfolio's early warning position, drivers, concentrations and the
#    forward-looking risks.
#
# Both fall back to template text if the model is unavailable.

# %% Facts from the monitoring pack
import datetime as dt

import pandas as pd

from ews_lib import commentary, llm
from ews_lib.lakehouse import lh
from ews_lib.runtime import run_context

pack = lh.read("ops.monitoring_metrics")
pack = pack[pack.run_id == pack.run_id.iloc[-1]]
csi = lh.read("ops.monitoring_csi")
lead = lh.read("ops.monitoring_lead_time")
perf = lh.read("ops.monitoring_performance")
stab = lh.read("ops.monitoring_stability")
latest = lh.read("gold.ews_decisions_latest")
comparison = lh.read("gold.model_comparison")
champion = comparison[comparison.is_champion].model.iloc[0]
mv = latest.movement.value_counts()
captured = lead[lead.lead_months > 0]
facts = dict(champion=champion, latest_gini=float(perf.gini.iloc[-1]), dev_gini=float(comparison[comparison.is_champion].gini.iloc[0]),
             score_psi=float(stab.score_psi.iloc[-1]), metrics=pack[["metric", "value", "amber", "red", "status"]].to_dict("records"),
             watchlist_size=int(latest.tier.between(1, 3).sum()), watchlist_share=float(latest.tier.between(1, 3).mean()),
             escalations=int(mv.get("Escalated", 0)), de_escalations=int(mv.get("De-escalated", 0)),
             median_lead_months=float(captured.lead_months.median()) if len(captured) else 0.0,
             bad_capture=float(len(captured) / max(len(lead), 1)), drifting_features=csi.head(3).to_dict("records"),
             tier_counts=latest.tier_name.value_counts().to_dict())
print(pack[["metric", "value", "status"]].to_string(index=False))

# %% Model risk commentary
prompt, fallback = commentary.build_monitoring(facts)
text, source, meta = llm.generate(prompt, fallback, purpose="monitoring_commentary", echo=True)
lh.write("ops.monitoring_commentary", pd.DataFrame([dict(run_id=run_context()["run_id"], created_at=dt.datetime.now(), source=source,
                                                         model=meta.get("model"), commentary=text)]),
         mode="append", description="AI-written monitoring commentary, one row per run")

# %% Executive summary for the chief risk officer
fired = lh.read("gold.ews_triggers_fired", columns=["customer_id", "month", "trigger_id", "trigger_name", "dimension"])
fired = fired[fired.month == fired.month.max()]
customer_level = ~fired.dimension.isin(["Model", "Collections", "Macroeconomic"]) & (fired.trigger_id != "T25")  # regional macro amplifies, never leads
top_trig = fired[customer_level].groupby("trigger_name").customer_id.nunique().sort_values(ascending=False).head(5)
reg = latest.groupby("region").tier.apply(lambda t: t.between(1, 3).mean()).sort_values(ascending=False)
seg = latest.groupby("segment").tier.apply(lambda t: t.between(1, 3).mean()).sort_values(ascending=False)
bal = latest.statement_balance.fillna(0)
pfacts = dict(as_at=f"{pd.Timestamp(latest.month.max()):%B %Y}", customers=int(len(latest)), watchlist=int(latest.tier.between(1, 3).sum()),
              tiers=latest.tier_name.value_counts().to_dict(), balance_at_risk_gbp=float(bal[latest.tier >= 2].sum()),
              escalations=int(mv.get("Escalated", 0)), de_escalations=int(mv.get("De-escalated", 0)),
              top_triggers=[dict(name=k, customers=int(v)) for k, v in top_trig.items()],
              top_regions=[dict(region=k, share=float(v)) for k, v in reg.head(3).items()],
              segments=[dict(segment=k, share=float(v)) for k, v in seg.items()],
              refix_shock_customers=int((latest.refix_shock_fwd.fillna(0) >= 0.15).sum()),
              pay_lean_in_collections=latest[latest.tier == 4].pay_lean.value_counts().to_dict(),
              limit_increase_eligible=int(latest.limit_and_activation.str.startswith("Eligible").sum()))
prompt, fallback = commentary.build_portfolio(pfacts)
text, source, meta = llm.generate(prompt, fallback, purpose="portfolio_summary", echo=True)
lh.write("gold.portfolio_commentary", pd.DataFrame([dict(run_id=run_context()["run_id"], created_at=dt.datetime.now(), source=source,
                                                         model=meta.get("model"), commentary=text)]),
         mode="append", description="AI-written executive summary of the portfolio, one row per run")
