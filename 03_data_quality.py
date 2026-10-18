# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Data quality: rules, remediation and commentary
# MAGIC
# MAGIC 1. **Rules as data.** The rule catalogue (`ews_ops.dq_rules`) holds one SQL test per rule across seven dimensions: completeness, uniqueness, validity, consistency, referential integrity, timeliness and distribution. Each has a severity, a tolerance and a named remediation.
# MAGIC 2. **Run** every rule against bronze with Spark SQL → `ews_ops.dq_results`.
# MAGIC 3. **Remediate** into silver: deduplicate, quarantine invalid rows (`ews_silver.dq_quarantine`), impute and flag, carry forward the late bureau file as stale → `ews_ops.dq_remediation_log`.
# MAGIC 4. **Commentary.** An LLM writes the data quality paragraph for the run pack from the structured results → `ews_ops.dq_commentary`.

# COMMAND ----------

# MAGIC %pip install -q lightgbm scikit-learn

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys, datetime as dt
sys.path.append(os.getcwd())
import pandas as pd
from ews_lib import config as C, dq, commentary
from ews_lib.generator import LANDING_FORMATS
from ews_lib.dbx import write_table, read_table, log_step, make_llm, RUN_ID

NB = "03_data_quality"
bronze = {k: C.table(C.SCHEMA_BRONZE, k) for k in LANDING_FORMATS if not k.startswith(("generator_truth", "personas"))}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. The rule catalogue

# COMMAND ----------

rules = dq.rules_frame()
write_table(spark, rules, C.table(C.SCHEMA_OPS, "dq_rules"))
display(rules[["rule_id", "table", "dimension", "description", "severity", "threshold", "action", "sql"]])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Run the rules on bronze

# COMMAND ----------

with log_step(spark, "run_dq_rules", NB) as rec:
    results = dq.run_rules(lambda q: spark.sql(q).toPandas(), bronze, run_id=RUN_ID)
    rec["rows_written"] = write_table(spark, results, C.table(C.SCHEMA_OPS, "dq_results"), mode="append")

display(results[["rule_id", "table_name", "dimension", "description", "severity", "records_tested",
                 "records_failed", "fail_rate", "threshold", "status", "action"]])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Remediate into silver

# COMMAND ----------

with log_step(spark, "conform_to_silver", NB) as rec:
    b = {k: read_table(spark, t).drop(columns=["_source_file", "_loaded_at"], errors="ignore") for k, t in bronze.items()}
    silver, quarantine, remediation = dq.conform_to_silver(b)
    n = 0
    for k, df in silver.items():
        n += write_table(spark, df, C.table(C.SCHEMA_SILVER, k))
    write_table(spark, quarantine, C.table(C.SCHEMA_SILVER, "dq_quarantine"))
    remediation["run_id"] = RUN_ID
    write_table(spark, remediation, C.table(C.SCHEMA_OPS, "dq_remediation_log"), mode="append")
    rec["rows_written"] = n

display(remediation)

# COMMAND ----------

display(spark.table(C.table(C.SCHEMA_SILVER, "dq_quarantine")).groupBy("source_table", "rule_id", "reason").count())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. AI-written data quality commentary
# MAGIC The model receives only the structured results below, never customer records. If no endpoint is reachable the template version is used.

# COMMAND ----------

facts = dq.dq_summary_facts(results, remediation)
llm = make_llm()
text, source = commentary.dq_commentary(facts, llm)
write_table(spark, pd.DataFrame([dict(run_id=RUN_ID, created_at=dt.datetime.utcnow(), source=source,
                                      endpoint=getattr(llm, "endpoint", None), commentary=text)]),
            C.table(C.SCHEMA_OPS, "dq_commentary"), mode="append")
print(f"[{source}]\n\n{text}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### The same idea in SQL with `ai_query`
# MAGIC Analysts can call the model straight from SQL, for example to add a plain-English line to every failed rule.

# COMMAND ----------

if llm:
    display(spark.sql(f"""
      SELECT rule_id, description, records_failed,
             ai_query('{llm.endpoint}',
                      CONCAT('In one sentence, explain to a credit risk manager why this data issue matters for an early warning model: ',
                             description, '. Records failing: ', CAST(records_failed AS STRING))) AS why_it_matters
      FROM {C.table(C.SCHEMA_OPS, 'dq_results')}
      WHERE run_id = '{RUN_ID}' AND status = 'FAIL'
    """))

# COMMAND ----------

# MAGIC %md
# MAGIC **Next:** `04_feature_engineering`.
