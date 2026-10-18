# Databricks notebook source
# MAGIC %md
# MAGIC # 05 · Trigger catalogue and threshold seeding
# MAGIC
# MAGIC The EWS uses 38 triggers across nine dimensions. Some are classic KRIs (utilisation, DSR, DSCR), some are not PD-based at all (collections status, broken promises, persistent EWI status, sentiment in contact notes, forward-looking refix shock), and some sit on model outputs (PD, probability of arrears, probability of forbearance, propensity to pay).
# MAGIC
# MAGIC **How thresholds are seeded**
# MAGIC * Every trigger starts with a **policy** amber and red value set by credit risk.
# MAGIC * For continuous KRIs the notebook also proposes a **calibrated** value from the development sample: the most lenient threshold at which the 6-month bad rate among triggered customers is at least **3×** the portfolio rate (amber) or **6×** (red), while firing for no more than 6% (amber) or 2% (red) of customers, and never less than half as severe as policy.
# MAGIC * Both are stored, with the evidence, in `ews_ops.kri_thresholds` so model risk can see where data and policy disagree.

# COMMAND ----------

# MAGIC %pip install -q lightgbm scikit-learn

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys, json, datetime as dt
sys.path.append(os.getcwd())
import pandas as pd
from ews_lib import config as C, thresholds as th, models as mo
from ews_lib.dbx import write_table, read_table, log_step, RUN_ID

NB = "05_seed_thresholds"

# COMMAND ----------

panel = read_table(spark, C.table(C.SCHEMA_GOLD, "customer_month_features"))
dev, cal, oot = mo.split(panel)
print(f"Development sample: {len(dev):,} customer-months, bad rate {dev.bad_6m.mean():.2%}")

# COMMAND ----------

with log_step(spark, "seed_thresholds", NB) as rec:
    thr = th.seed_thresholds(dev)
    thr["version"] = 1
    thr["seeded_at"] = dt.datetime.utcnow()
    thr["status"] = "Proposed - pending model risk review"
    rec["rows_written"] = write_table(spark, thr, C.table(C.SCHEMA_OPS, "kri_thresholds"))
    eff = th.trigger_effectiveness(dev, thr)
    write_table(spark, eff, C.table(C.SCHEMA_OPS, "trigger_effectiveness"))
    os.makedirs(C.artifacts_path(), exist_ok=True)
    thr.to_parquet(f"{C.artifacts_path()}/kri_thresholds.parquet", index=False)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Policy versus calibrated thresholds

# COMMAND ----------

display(thr[["trigger_id", "name", "dimension", "kind", "feature", "direction", "policy_amber", "policy_red",
             "calibrated_amber", "calibrated_red", "amber", "red", "threshold_source", "use_cases"]])

# COMMAND ----------

# MAGIC %md
# MAGIC ## How well does each trigger separate future bads?
# MAGIC `lift` is the bad rate among customers the trigger fires for, divided by the portfolio bad rate. `capture_rate` is the share of all future bads it fires for.

# COMMAND ----------

display(eff.sort_values(["severity", "lift"], ascending=[True, False]))

# COMMAND ----------

# MAGIC %md
# MAGIC Points worth discussing with the modelling team:
# MAGIC * **Data and policy disagree in both directions.** Utilisation of 80% fires for about one customer in ten, above the 6% volume cap, so calibration moves amber to 95%. Overdraft reliance goes the other way: five overdrawn days in a month already triples the bad rate, so calibration flags it well before the policy value of ten.
# MAGIC * Non-PD triggers such as hardship language in notes, a returned direct debit or a payday lender payment have **very high lift but low capture**: rare and strong, which suits escalation rather than ranking.
# MAGIC * Binary triggers (CCJ, payday lender, vulnerability, forbearance) have the same amber and red value by design.
# MAGIC * Macro triggers barely fire in the development window (2024-25) but do fire in 2026 for the stressed regions. They amplify customer evidence in the watchlist rules; they never escalate a customer on their own.
# MAGIC
# MAGIC **Next:** `06_train_compare_models`.
