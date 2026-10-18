# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Setup
# MAGIC **Retail bank early warning system (EWS) demo**
# MAGIC
# MAGIC This notebook creates the Unity Catalog schemas and volumes, checks that the `ews_lib` library imports, and finds a chat model endpoint for the AI-written commentary.
# MAGIC
# MAGIC | Step | Notebook | Writes |
# MAGIC |---|---|---|
# MAGIC | Generate data | `01_generate_data` | Raw files in the landing volume |
# MAGIC | Load | `02_load_bronze` | `ews_bronze.*` Delta tables |
# MAGIC | Data quality | `03_data_quality` | `ews_ops.dq_*`, `ews_silver.*` |
# MAGIC | Build features | `04_feature_engineering` | `ews_gold.customer_month_features` |
# MAGIC | Seed thresholds | `05_seed_thresholds` | `ews_ops.kri_thresholds` |
# MAGIC | Build and compare models | `06_train_compare_models` | `ews_gold.model_comparison`, MLflow runs |
# MAGIC | Score, trigger, watchlist | `07_score_trigger_watchlist` | `ews_gold.ews_*`, `ews_gold.watchlist_*` |
# MAGIC | Monitoring | `08_monitoring` | `ews_ops.monitoring_*` |
# MAGIC | Live early warning | `09_live_simulator` | `ews_gold.live_*` (read by the app) |
# MAGIC
# MAGIC Run on **serverless** compute. Every notebook can be re-run on its own once the ones before it have run.

# COMMAND ----------

# MAGIC %pip install -q lightgbm scikit-learn

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys
sys.path.append(os.getcwd())  # this folder holds ews_lib/

from ews_lib import config as C
print("Catalog:", C.CATALOG)
print("Schemas:", C.SCHEMA_BRONZE, C.SCHEMA_SILVER, C.SCHEMA_GOLD, C.SCHEMA_OPS)
print("Landing volume:", C.landing_path())
print("Artifacts volume:", C.artifacts_path())

# COMMAND ----------

# MAGIC %md
# MAGIC ## Create schemas and volumes
# MAGIC Free Edition comes with a catalog called `workspace`. If you are on a full workspace and want a dedicated catalog, create it first (`CREATE CATALOG ews_demo`) and change `CATALOG` in `ews_lib/config.py`.

# COMMAND ----------

for schema, comment in [
    (C.SCHEMA_BRONZE, "EWS demo: raw source data as landed"),
    (C.SCHEMA_SILVER, "EWS demo: conformed and quality-checked data"),
    (C.SCHEMA_GOLD, "EWS demo: features, scores, triggers, watchlist and live feed"),
    (C.SCHEMA_OPS, "EWS demo: pipeline runs, data quality, thresholds and monitoring"),
]:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {C.CATALOG}.{schema} COMMENT '{comment}'")

spark.sql(f"CREATE VOLUME IF NOT EXISTS {C.CATALOG}.{C.SCHEMA_BRONZE}.{C.VOLUME_NAME} COMMENT 'Landing zone for source files'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {C.CATALOG}.{C.SCHEMA_OPS}.artifacts COMMENT 'Model bundles and thresholds'")
display(spark.sql(f"SHOW SCHEMAS IN {C.CATALOG} LIKE 'ews*'"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Check the library and its dependencies

# COMMAND ----------

import importlib
for mod in ["generator", "dq", "features", "thresholds", "models", "triggers", "watchlist", "actions",
            "scoring", "commentary", "monitoring", "live", "dbx"]:
    importlib.import_module(f"ews_lib.{mod}")
import lightgbm, sklearn, pandas
print("ews_lib OK | lightgbm", lightgbm.__version__, "| scikit-learn", sklearn.__version__, "| pandas", pandas.__version__)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Find a chat model for the commentary
# MAGIC The commentary notebooks call a Foundation Model endpoint. This lists what your workspace can see and makes a test call. If nothing is available, the demo still runs and uses template text instead.

# COMMAND ----------

from ews_lib import dbx
endpoints = dbx.available_chat_endpoints()
print("Chat endpoints visible:", endpoints)
llm = dbx.make_llm()
if llm:
    print(llm("In one sentence, what is an early warning system in retail credit risk?"))

# COMMAND ----------

# MAGIC %md
# MAGIC If the endpoint you want is not the one picked, set `LLM_ENDPOINT` in `ews_lib/config.py` to one of the names listed above.
# MAGIC
# MAGIC **Next:** run `01_generate_data`.
