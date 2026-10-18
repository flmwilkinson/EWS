# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Load raw files into bronze Delta tables
# MAGIC
# MAGIC Reads each landed file with the Spark reader for its format and writes it unchanged to `ews_bronze`, adding two lineage columns: the source file and the load timestamp. Bronze keeps the defects; notebook 03 deals with them.

# COMMAND ----------

# MAGIC %pip install -q lightgbm scikit-learn

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys
sys.path.append(os.getcwd())
import pandas as pd
from pyspark.sql import functions as F
from ews_lib import config as C
from ews_lib.generator import LANDING_FORMATS
from ews_lib.dbx import log_step

NB = "02_load_bronze"

# COMMAND ----------

def read_landed(fmt, path):
    if fmt == "csv":
        return spark.read.option("header", True).option("inferSchema", True).csv(path)
    if fmt == "psv":
        return spark.read.option("header", True).option("inferSchema", True).option("sep", "|").csv(path)
    if fmt == "parquet":
        return spark.read.parquet(path)
    if fmt == "jsonl":
        return spark.read.json(path)
    raise ValueError(fmt)

loaded = []
for name, (fmt, rel) in LANDING_FORMATS.items():
    target = C.table(C.SCHEMA_BRONZE, name if not name.startswith(("generator_truth", "personas")) else f"_{name}")
    with log_step(spark, f"load_{name}", NB) as rec:
        df = (read_landed(fmt, f"{C.landing_path()}/{rel}")
              .withColumn("_source_file", F.lit(rel))
              .withColumn("_loaded_at", F.current_timestamp()))
        df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)
        rec["rows_written"] = spark.table(target).count()
    loaded.append((target, fmt, rec["rows_written"]))

display(pd.DataFrame(loaded, columns=["table", "source_format", "rows"]))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Inspect a bronze table
# MAGIC Every table is a Delta table in Unity Catalog, so it has a version history and lineage you can open from Catalog Explorer.

# COMMAND ----------

display(spark.table(C.table(C.SCHEMA_BRONZE, "card_monthly")).limit(10))

# COMMAND ----------

display(spark.sql(f"DESCRIBE HISTORY {C.table(C.SCHEMA_BRONZE, 'card_monthly')}").select("version", "timestamp", "operation", "operationMetrics"))

# COMMAND ----------

# MAGIC %md
# MAGIC `_generator_truth` holds the simulator's hidden cause of distress for each customer-month. It is **never** used as a model input; it exists so the demo can show where models are right or wrong.
# MAGIC
# MAGIC **Next:** `03_data_quality`.
