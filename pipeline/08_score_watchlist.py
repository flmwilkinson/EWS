# %% [markdown]
# # 08 · Score, trigger, escalate and decide
#
# For the last 18 months (so the watchlist has history and persistence), every active customer gets:
#
# 1. **Model outputs** from the registry champions: 6-month PD and risk band (A to G), probability of arrears,
#    probability of forbearance, propensity to pay (if in arrears), can't pay versus won't pay.
# 2. **Triggers**: amber and red against the seeded thresholds, counted by **depth** (how many red) and **breadth**
#    (how many evidence dimensions). Regional macro triggers amplify customer evidence but never escalate on their own.
# 3. **Watchlist tier**: Performing → Monitor → Watch → Early intervention → Collections → Recoveries. Escalation is
#    immediate; de-escalation needs three clean months and steps down one tier at a time.
# 4. **Decisions** for six business uses: approval, activation and limits · pricing · exposure management ·
#    offers and engagement · support and early intervention · collections treatment.
# 5. **Explanations**: the top model drivers per customer, and AI-written summaries for the customers in focus and the
#    highest-risk new entrants.

# %% Load features, thresholds and the champion models
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from ews_lib import actions, commentary, llm, registry, scoring as sc
from ews_lib.config import TIERS
from ews_lib.lakehouse import lh
from ews_lib.runtime import chart, display, metrics

MONTHS_TO_SCORE = 18

panel = lh.read("gold.customer_month_features")
customers = lh.read("silver.customers")
thr = lh.read("ops.kri_thresholds")
bundles = {"pd_6m": registry.load_champion("pd_6m"), "arrears_3m": registry.load_champion("p_arrears_3m"),
           "forbearance_6m": registry.load_champion("p_forbearance_6m"), "cure_2m": registry.load_champion("propensity_to_pay"),
           "cant_pay": registry.load_champion("cant_pay_vs_wont_pay")}
for k, b in bundles.items():
    print(f"{k:<16} {b['_registry']['name']} v{b['_registry']['version']}")

# %% Score every customer-month, evaluate triggers, tier the watchlist
months = sorted(panel.month.unique())[-MONTHS_TO_SCORE:]
full, fired = sc.run_batch(panel, bundles, thr, months, customers)
lh.write("gold.ews_scores_monthly", full, description="Every customer-month: model outputs, trigger summary, tier and movement")
lh.write("gold.ews_triggers_fired", fired, description="One row per trigger fired per customer-month, with value and threshold")
latest_month = full.month.max()
now = full[full.month == latest_month]
metrics({"Scored customer-months": f"{len(full):,}", "Triggers fired": f"{len(fired):,}",
         f"On watchlist ({latest_month:%b %Y})": f"{int(now.tier.between(1, 3).sum()):,}",
         "In collections": f"{int((now.tier == 4).sum()):,}"})

# %% The watchlist over time
tiers = full.groupby(["month", "tier_name"]).customer_id.nunique().unstack(fill_value=0)
labels = [pd.Timestamp(m).strftime("%b %y") for m in tiers.index]
chart("bar", labels, {t: tiers[t] if t in tiers else [0] * len(tiers) for t in ["Monitor", "Watch", "Early intervention", "Collections"]},
      title="Watchlist and collections by month", y_format="int", height=300)
display(now.tier_name.value_counts().reindex([TIERS[k] for k in sorted(TIERS)]).fillna(0).astype(int).rename("customers").reset_index(),
        title=f"Tiers, {latest_month:%B %Y}")

# %% Decisions and explanations for the latest month
latest = sc.latest_decisions(full)
cm = lh.read("silver.card_monthly", columns=["customer_id", "month", "credit_limit", "statement_balance"])
lim = cm[cm.month == cm.month.max()][["customer_id", "credit_limit", "statement_balance"]].drop_duplicates("customer_id")
latest = latest.merge(lim, on="customer_id", how="left")
on_list = sc.explain(latest[latest.tier >= 1], bundles["pd_6m"])
latest = latest.merge(on_list[["customer_id", "top_drivers", "driver_1"]], on="customer_id", how="left")
personas = lh.read("bronze.personas")[["customer_id", "name"]].rename(columns={"name": "persona"})
latest = latest.merge(personas, on="customer_id", how="left")
lh.write("gold.ews_decisions_latest", latest, description="Latest month: scores, tier, top drivers and decisions for all six uses")

matrix = (latest.groupby("tier_name")[actions.USE_CASES].agg(lambda s: s.value_counts().index[0])
          .rename(columns=actions.USE_CASE_LABELS))
display(matrix.reindex([TIERS[k] for k in sorted(TIERS) if TIERS[k] in matrix.index]).reset_index(), title="Decision matrix: most common action by tier")

# %% AI summaries: customers in focus and the highest-risk new entrants
names = fired[fired.month == latest_month].groupby("customer_id").trigger_name.apply(list).to_dict()
pick = pd.concat([latest[latest.persona.notna()],
                  latest[latest.tier.between(2, 3) & (latest.movement == "Escalated")].sort_values("pd_6m", ascending=False).head(12)]
                 ).drop_duplicates("customer_id")


def summarise(r):
    prompt, fallback = commentary.build_customer(commentary.customer_facts(r, names.get(r["customer_id"], [])))
    text, source, _ = llm.generate(prompt, fallback, purpose="customer_summary", max_tokens=600)
    return dict(customer_id=r["customer_id"], month=r["month"], tier_name=r["tier_name"], pd_6m=r["pd_6m"],
                pay_lean=r.get("pay_lean"), summary=text, source=source, created_at=dt.datetime.now())


with ThreadPoolExecutor(max_workers=8) as pool:
    summaries = pd.DataFrame(list(pool.map(summarise, pick.to_dict("records"))))
lh.write("gold.customer_summaries", summaries, description="AI-written watchlist summaries (why flagged, can't vs won't pay, next action)")
print(f"{len(summaries)} summaries, {int((summaries.source == 'llm').sum())} written by {llm.model()}")
display(summaries[["customer_id", "tier_name", "pd_6m", "pay_lean", "summary", "source"]], title="Summaries")
