# %% [markdown]
# # 05 · Feature engineering and forward-looking targets
#
# Builds one row per customer per month (`gold.customer_month_features`) from every silver source.
#
# **Four kinds of feature**
# * **Levels**: where the customer is now (utilisation 87%, DSR 52%, bureau score 612)
# * **Changes**: how fast it is moving (utilisation +22pp in 3 months, bureau score −60)
# * **Relatives**: against their own history (income 45% below their 6-month average, utilisation 1.8× their norm)
# * **Interactions**: combinations that matter more together (utilisation × rising regional unemployment,
#   utilisation × credit searches, income drop × overdraft days, DSR × rate sensitivity)
#
# **Seven evidence families:** card behaviour, cross-product (current account, mortgage, loans, business),
# credit bureau, macroeconomic, forward-looking (mortgage refix shock, regional outlook), conduct and sentiment
# (contact notes), collections.
#
# **Targets**, all forward-looking from the observation month:
#
# | Target | Definition | Population |
# |---|---|---|
# | `bad_6m` | reaches 60+ days past due or charge-off within 6 months | not already 60+ |
# | `arrears_3m` | misses a payment within 3 months | up to date today |
# | `forbearance_6m` | enters forbearance within 6 months | not in forbearance |
# | `cure_2m` (propensity to pay) | back to up to date within 2 months | in arrears today |
# | `cant_pay` | agent-recorded arrears reason is affordability, not willingness | collections contacts with a reason |

# %% Load silver
import pandas as pd

from ews_lib import features as fe
from ews_lib.lakehouse import lh
from ews_lib.runtime import chart, display, metrics

SILVER = ["customers", "card_accounts", "card_monthly", "current_account_monthly", "product_holdings",
          "other_product_monthly", "business_monthly", "bureau_monthly", "macro_regional_monthly",
          "macro_forecast", "collections_events", "forbearance_events", "contact_notes"]
s = {k: lh.read(f"silver.{k}") for k in SILVER}
print(f"{len(s)} silver tables, {sum(len(v) for v in s.values()):,} rows")

# %% Build the customer-month feature panel
panel = fe.build_panel(s)
lh.write("gold.customer_month_features", panel, description="One row per customer per month: ~60 features and 4 forward-looking targets")
lh.write("gold.feature_dictionary", fe.feature_dictionary_frame(), description="Feature name, evidence family, kind and definition")
metrics({"Customer-months": f"{len(panel):,}", "Customers": f"{panel.customer_id.nunique():,}",
         "Months": panel.month.nunique(), "Features": len(fe.FEATURES)})

# %% The feature dictionary
fd = fe.feature_dictionary_frame()
display(fd, title="Feature dictionary", max_rows=70)
fam = fd.groupby("family").size().sort_values(ascending=False)
chart("bar", fam.index, {"Features": fam.values}, title="Features by evidence family", y_format="int", horizontal=True)

# %% Contact notes: sentiment and topic
# An offline, transparent lexicon scores every note (−1 to 1) and assigns a topic. The live service and the
# customer view show the same notes; the AI can classify them too.
notes = fe.score_notes(s["contact_notes"])
lh.write("silver.contact_notes_scored", notes, description="Contact notes with sentiment (-1 to 1) and topic")
display(notes.groupby("topic").agg(notes=("note_id", "size"), avg_sentiment=("sentiment", "mean")).reset_index(), title="Notes by topic")

# %% Target rates by observation month
rates = panel.groupby("month")[["bad_6m", "arrears_3m", "forbearance_6m"]].mean().reset_index()
rates["month"] = pd.to_datetime(rates.month).dt.strftime("%b %y")
chart("line", rates.month, {"Bad within 6m": rates.bad_6m, "Arrears within 3m": rates.arrears_3m,
                            "Forbearance within 6m": rates.forbearance_6m},
      title="Forward-looking target rates (blank where the window has not closed)", y_format="pct")

# %% How signals build up before a default
bad_ids = panel[panel.cycles_now >= 2].customer_id.unique()
example = bad_ids[len(bad_ids) // 3]
cols = ["month", "util", "util_chg_3m", "pay_ratio", "min_pay_streak", "cash_adv_months_3m", "salary_missing_3m",
        "od_days", "searches_3m", "ext_worst_6m", "bureau_score", "dsr", "hardship_notes_3m", "cycles_now"]
display(panel[panel.customer_id == example][cols].round(3), title=f"Customer {example}: the run-up to 60+ days past due")
