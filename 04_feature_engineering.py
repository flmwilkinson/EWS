# Databricks notebook source
# MAGIC %md
# MAGIC # 04 · Feature engineering and forward-looking targets
# MAGIC
# MAGIC Builds one row per customer per month (`ews_gold.customer_month_features`) from all silver sources.
# MAGIC
# MAGIC **Four kinds of feature**
# MAGIC * **Levels**: where the customer is now (utilisation 87%, DSR 52%, bureau score 612)
# MAGIC * **Changes**: how fast it is moving (utilisation +22pp in 3 months, bureau score −60)
# MAGIC * **Relatives**: against their own history (income 45% below their 6-month average, utilisation 1.8× their norm)
# MAGIC * **Interactions**: combinations that matter more together (high utilisation × rising regional unemployment; utilisation × credit searches; income drop × overdraft days; DSR × rate sensitivity)
# MAGIC
# MAGIC **Seven evidence families:** card behaviour, cross-product (current account, mortgage, loans, business), credit bureau, macroeconomic, forward-looking (mortgage refix shock, regional outlook), conduct and sentiment (contact notes), collections.
# MAGIC
# MAGIC **Targets** (all forward-looking from the observation month)
# MAGIC | Target | Definition | Population |
# MAGIC |---|---|---|
# MAGIC | `bad_6m` | reaches 60+ DPD or charge-off within 6 months | not already 60+ |
# MAGIC | `arrears_3m` | misses a payment within 3 months | up to date today |
# MAGIC | `forbearance_6m` | enters forbearance within 6 months | not in forbearance |
# MAGIC | `cure_2m` (propensity to pay) | back to up to date within 2 months | in arrears today |
# MAGIC | `cant_pay` | agent-recorded arrears reason is affordability, not willingness | collections contacts with a reason |

# COMMAND ----------

# MAGIC %pip install -q lightgbm scikit-learn

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys
sys.path.append(os.getcwd())
import pandas as pd
from ews_lib import config as C, features as fe
from ews_lib.dbx import write_table, read_table, log_step

NB = "04_feature_engineering"
silver_tables = ["customers", "card_accounts", "card_monthly", "current_account_monthly", "product_holdings",
                 "other_product_monthly", "business_monthly", "bureau_monthly", "macro_regional_monthly",
                 "macro_forecast", "collections_events", "forbearance_events", "contact_notes"]

# COMMAND ----------

with log_step(spark, "build_feature_panel", NB) as rec:
    s = {k: read_table(spark, C.table(C.SCHEMA_SILVER, k)) for k in silver_tables}
    panel = fe.build_panel(s)
    rec["rows_written"] = write_table(spark, panel, C.table(C.SCHEMA_GOLD, "customer_month_features"))
    write_table(spark, fe.feature_dictionary_frame(), C.table(C.SCHEMA_GOLD, "feature_dictionary"))
    write_table(spark, fe.score_notes(s["contact_notes"]), C.table(C.SCHEMA_SILVER, "contact_notes_scored"))
print(panel.shape)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Feature dictionary

# COMMAND ----------

display(spark.table(C.table(C.SCHEMA_GOLD, "feature_dictionary")))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Target rates by observation month
# MAGIC Targets are blank where the outcome window has not closed yet (the last 6 months for `bad_6m`).

# COMMAND ----------

display(panel.groupby("month")[["bad_6m", "arrears_3m", "forbearance_6m", "cure_2m"]].mean().reset_index())

# COMMAND ----------

# MAGIC %md
# MAGIC ## Contact notes: offline sentiment and topic scoring
# MAGIC Each note gets a sentiment score (−1 to 1) and a topic (hardship, vulnerability, refusal, positive, servicing). Notebook 07 shows the LLM equivalent.

# COMMAND ----------

display(spark.table(C.table(C.SCHEMA_SILVER, "contact_notes_scored")).select("customer_id", "created_ts", "channel", "note_text", "sentiment", "topic").limit(20))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Example: a customer who went bad, seen through their features
# MAGIC Pick any customer who reached 60+ DPD and look at how the signals built up beforehand.

# COMMAND ----------

bad_ids = panel[panel.cycles_now >= 2].customer_id.unique()
example = bad_ids[0]
cols = ["month", "util", "util_chg_3m", "pay_ratio", "min_pay_streak", "cash_adv_months_3m", "salary_missing_3m",
        "od_days", "searches_3m", "ext_worst_6m", "bureau_score", "dsr", "hardship_notes_3m", "cycles_now"]
display(panel[panel.customer_id == example][cols])

# COMMAND ----------

# MAGIC %md
# MAGIC **Next:** `05_seed_thresholds`.
