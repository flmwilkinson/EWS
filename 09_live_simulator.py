# Databricks notebook source
# MAGIC %md
# MAGIC # 09 · Live early warning simulator
# MAGIC
# MAGIC Plays October 2026 forward one business day per tick, starting from every customer's September month-end position. Each tick:
# MAGIC
# MAGIC 1. generates the day's events: card transactions, cash advances, salary credits (or their absence), overdraft use, returned direct debits, gambling and BNPL payments, credit searches, bureau alerts, mortgage maturity letters, business takings, contact centre notes and payment due dates
# MAGIC 2. updates the features of the customers those events touch
# MAGIC 3. re-scores them with the trained models (PD, arrears, forbearance, propensity to pay, can't / won't pay)
# MAGIC 4. re-evaluates the triggers and moves them through the watchlist
# MAGIC 5. decides the actions for each business use and writes an alert with a summary
# MAGIC 6. appends to the live Delta tables that the **EWS Live** app reads
# MAGIC
# MAGIC | Persona | Story | What to watch for |
# MAGIC |---|---|---|
# MAGIC | Priya | Made redundant; salary stops mid-month | Watch on day 10 from spending and overdraft, Early intervention when the salary does not arrive; can't pay → proactive I&E and payment plan |
# MAGIC | Dan | Gambling rising, salary stable | Moves up tier by tier; misses a payment and refuses to engage → won't pay, firmer collections plus gambling block |
# MAGIC | Grace | Fixed rate ends in December, payment up ~40% | Flagged in October, **before** the payment rises, by a forward-looking trigger the PD model cannot see |
# MAGIC | Lee | Sole trader café, DSCR falls below 1 | Business banking DSCR trigger, then hardship call → Early intervention |
# MAGIC | Sarah | Healthy and earning more | Stays Performing: pre-approved limit increase and a targeted offer |
# MAGIC | Mark | Clean with us, misses a payment elsewhere | The bureau tells us first → Monitor, no limit increases |
# MAGIC
# MAGIC Serverless compute does not support time-based streaming triggers, so this notebook drives the simulation in a loop and writes each day with Delta appends. In production the same scoring code would sit behind Lakeflow pipelines in continuous mode or a model serving endpoint.

# COMMAND ----------

# MAGIC %pip install -q lightgbm scikit-learn

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("days", "31", "Days to simulate")
dbutils.widgets.text("seconds_per_day", "8", "Seconds per simulated day")
dbutils.widgets.dropdown("reset", "yes", ["yes", "no"], "Reset live tables")

# COMMAND ----------

import os, sys, time
sys.path.append(os.getcwd())
import pandas as pd, joblib
from ews_lib import config as C, live
from ews_lib.dbx import write_table, read_table

DAYS = int(dbutils.widgets.get("days"))
PACE = float(dbutils.widgets.get("seconds_per_day"))
G = lambda n: C.table(C.SCHEMA_GOLD, n)
LIVE_TABLES = ["live_events", "live_alerts", "live_kpis", "live_personas", "live_watchlist", "live_persona_cards"]

if dbutils.widgets.get("reset") == "yes":
    for t in LIVE_TABLES:
        spark.sql(f"DROP TABLE IF EXISTS {G(t)}")

start = read_table(spark, G("ews_decisions_latest"))
thr = read_table(spark, C.table(C.SCHEMA_OPS, "kri_thresholds"))
bundles = joblib.load(f"{C.artifacts_path()}/ews_bundles.joblib")
personas = read_table(spark, C.table(C.SCHEMA_BRONZE, "_personas"))
sim = live.LiveSimulator(start, bundles, thr, start_date=C.LIVE_START_DATE, personas=personas)
print(f"Simulator ready: {len(sim.S):,} active customers, {int(sim.S.shock.notna().sum())} will deteriorate, "
      f"{len(sim.noise)} will create one-off noise")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Run
# MAGIC Open the app (or the `ews_gold.live_*` tables) while this runs. Interrupt the cell at any time; everything written so far stays.

# COMMAND ----------

write_table(spark, sim.persona_cards(), G("live_persona_cards"))
write_table(spark, sim.watchlist_snapshot(), G("live_watchlist"))
for _ in range(DAYS):
    t0 = time.time()
    out = sim.tick()
    for name, df in [("live_events", out["events"]), ("live_alerts", out["alerts"]),
                     ("live_kpis", out["kpis"]), ("live_personas", out["personas"])]:
        if len(df):
            write_table(spark, df, G(name), mode="append")
    write_table(spark, sim.watchlist_snapshot(), G("live_watchlist"))
    write_table(spark, sim.persona_cards(), G("live_persona_cards"))
    k = out["kpis"].iloc[0]
    print(f"{k.sim_date}  events {int(k.events_today):>3}  alerts {int(k.alerts_today):>2}  "
          f"watchlist {int(k.watchlist_total):,}  ({time.time() - t0:.1f}s)")
    time.sleep(max(0.0, PACE - (time.time() - t0)))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Persona journeys

# COMMAND ----------

display(spark.sql(f"""
  SELECT sim_day, persona, tier_name, ROUND(pd_6m, 3) AS pd_6m, risk_band, fired_triggers
  FROM {G('live_personas')} ORDER BY persona, sim_day"""))

# COMMAND ----------

display(spark.sql(f"""
  SELECT sim_day, persona, alert_type, tier_before, tier_after, ROUND(pd_after, 3) AS pd_after, pay_lean, recommended_action, summary
  FROM {G('live_alerts')} WHERE persona IS NOT NULL ORDER BY sim_day"""))
