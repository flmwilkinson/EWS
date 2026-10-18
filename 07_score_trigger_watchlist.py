# Databricks notebook source
# MAGIC %md
# MAGIC # 07 · Score, trigger, escalate and decide
# MAGIC
# MAGIC For the last 18 months (so the watchlist has history and persistence), every active customer gets:
# MAGIC 1. **Model outputs**: 6-month PD and risk band (A to G), probability of arrears, probability of forbearance, propensity to pay (if in arrears), can't pay versus won't pay.
# MAGIC 2. **Triggers**: amber / red against the seeded thresholds, counted by **depth** (how many red) and **breadth** (how many evidence dimensions). Regional macro triggers amplify customer evidence but never escalate on their own.
# MAGIC 3. **Watchlist tier**: Performing → Monitor → Watch → Early intervention → Collections → Recoveries. Escalation is immediate; de-escalation needs three clean months and steps down one tier at a time.
# MAGIC 4. **Decisions** for six business uses: approval / activation and limits, pricing, exposure management, offers and engagement, support and early intervention, collections treatment.
# MAGIC 5. **Explanations**: top model drivers per customer and an AI-written summary for the watchlist.

# COMMAND ----------

# MAGIC %pip install -q lightgbm scikit-learn

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys, datetime as dt
sys.path.append(os.getcwd())
import pandas as pd, joblib
from ews_lib import config as C, scoring as sc, commentary, actions
from ews_lib.dbx import write_table, read_table, log_step, make_llm, RUN_ID

NB = "07_score_trigger_watchlist"
panel = read_table(spark, C.table(C.SCHEMA_GOLD, "customer_month_features"))
customers = read_table(spark, C.table(C.SCHEMA_SILVER, "customers"))
thr = read_table(spark, C.table(C.SCHEMA_OPS, "kri_thresholds"))
bundles = joblib.load(f"{C.artifacts_path()}/ews_bundles.joblib")
print("Champion PD model:", bundles["pd_6m"].get("name"))

# COMMAND ----------

with log_step(spark, "score_trigger_watchlist", NB) as rec:
    months = sorted(panel.month.unique())[-18:]
    full, fired = sc.run_batch(panel, bundles, thr, months, customers)
    rec["rows_written"] = write_table(spark, full, C.table(C.SCHEMA_GOLD, "ews_scores_monthly"))
    write_table(spark, fired, C.table(C.SCHEMA_GOLD, "ews_triggers_fired"))
    wl_cols = ["customer_id", "month", "tier", "tier_name", "raw_tier", "tier_reason", "movement", "prev_tier",
               "months_on_watch", "cure_progress", "pd_6m", "risk_band", "n_amber", "n_red", "breadth", "fired_triggers"]
    write_table(spark, full[wl_cols], C.table(C.SCHEMA_GOLD, "watchlist_monthly"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Watchlist by month

# COMMAND ----------

display(full.groupby(["month", "tier", "tier_name"]).customer_id.nunique().rename("customers").reset_index())

# COMMAND ----------

# MAGIC %md
# MAGIC ## Latest month: decisions for each business use

# COMMAND ----------

with log_step(spark, "decide_and_explain", NB) as rec:
    latest = sc.latest_decisions(full)
    cm = read_table(spark, C.table(C.SCHEMA_SILVER, "card_monthly"))
    lim = cm[cm.month == cm.month.max()][["customer_id", "credit_limit", "statement_balance"]]
    latest = latest.merge(lim, on="customer_id", how="left")
    on_list = sc.explain(latest[latest.tier >= 1], bundles["pd_6m"])
    latest = latest.merge(on_list[["customer_id", "top_drivers", "driver_1"]], on="customer_id", how="left")
    rec["rows_written"] = write_table(spark, latest, C.table(C.SCHEMA_GOLD, "ews_decisions_latest"))

for u, label in actions.USE_CASE_LABELS.items():
    print(f"\n{label}")
    print(latest[u].value_counts().to_string())

# COMMAND ----------

# MAGIC %md
# MAGIC ## Decision matrix: tier against use case

# COMMAND ----------

matrix = (latest.groupby("tier_name")[list(actions.USE_CASE_LABELS)]
          .agg(lambda s: s.value_counts().index[0]).rename(columns=actions.USE_CASE_LABELS))
order = [C.TIERS[k] for k in sorted(C.TIERS)]
display(matrix.reindex([o for o in order if o in matrix.index]).reset_index())

# COMMAND ----------

# MAGIC %md
# MAGIC ## AI-written summaries for the highest-risk new entrants and the personas
# MAGIC Each summary is written from structured facts only: tier and reason, PD and band, the signals that fired, the top model drivers, the can't / won't pay lean and the recommended actions.

# COMMAND ----------

llm = make_llm()
fired_latest = fired[fired.month == full.month.max()]
names = fired_latest.groupby("customer_id").trigger_name.apply(list).to_dict()
pick = pd.concat([
    latest[(latest.tier.between(2, 3)) & (latest.movement == "Escalated")].sort_values("pd_6m", ascending=False).head(20),
    latest[latest.customer_id.str.startswith("C9")],
]).drop_duplicates("customer_id")
rows = []
for r in pick.to_dict("records"):
    text, source = commentary.customer_summary(commentary.customer_facts(r, names.get(r["customer_id"], [])), llm)
    rows.append(dict(customer_id=r["customer_id"], month=r["month"], tier_name=r["tier_name"], pd_6m=r["pd_6m"],
                     pay_lean=r.get("pay_lean"), summary=text, source=source, created_at=dt.datetime.utcnow()))
summaries = pd.DataFrame(rows)
write_table(spark, summaries, C.table(C.SCHEMA_GOLD, "ews_customer_summaries"))
display(summaries)

# COMMAND ----------

# MAGIC %md
# MAGIC ## LLM sentiment on contact notes (optional)
# MAGIC Notebook 04 scored notes with a transparent lexicon. Here the same notes are classified by the LLM with `ai_query`, so the two can be compared.

# COMMAND ----------

if llm:
    display(spark.sql(f"""
      SELECT customer_id, note_text, topic AS lexicon_topic, ROUND(sentiment, 2) AS lexicon_sentiment,
             ai_query('{llm.endpoint}',
               CONCAT('Classify this bank contact centre note into exactly one of: hardship, vulnerability, refusal, positive, servicing. ',
                      'Reply with the single word only. Note: ', note_text)) AS llm_topic
      FROM {C.table(C.SCHEMA_SILVER, 'contact_notes_scored')}
      WHERE created_ts >= '2026-07-01'
      LIMIT 25
    """))

# COMMAND ----------

# MAGIC %md
# MAGIC **Next:** `08_monitoring`.
