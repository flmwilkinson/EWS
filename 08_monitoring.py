# Databricks notebook source
# MAGIC %md
# MAGIC # 08 · Monitoring the early warning system
# MAGIC
# MAGIC A monthly RAG pack covering the model and the system around it.
# MAGIC
# MAGIC | Area | Metric | Amber | Red |
# MAGIC |---|---|---|---|
# MAGIC | Discrimination | Gini on the latest matured month | < 0.70 | < 0.65 |
# MAGIC | Discrimination | Gini drop against approval | > 5 pts | > 10 pts |
# MAGIC | Calibration | Predicted / actual bad rate | ±20% | ±35% |
# MAGIC | Stability | Score PSI against development | > 0.10 | > 0.25 |
# MAGIC | Stability | Largest feature CSI | > 0.10 | > 0.25 |
# MAGIC | Early warning | Share of 60+ DPD customers flagged (Watch or Early intervention) before their first missed payment | < 60% | < 45% |
# MAGIC | Early warning | Median months of warning | < 2 | < 1 |
# MAGIC | Early warning | Precision: new Watch / EI entrants who go bad within 6 months | < 10% | < 5% |
# MAGIC
# MAGIC Outcomes take six months to mature, so performance is measured on the out-of-time months (October 2025 to March 2026), while stability, trigger rates and watchlist flows use every month up to September 2026.

# COMMAND ----------

# MAGIC %pip install -q lightgbm scikit-learn

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys, json, datetime as dt
sys.path.append(os.getcwd())
import pandas as pd, joblib
import matplotlib.pyplot as plt
from ews_lib import config as C, models as mo, monitoring as mon, watchlist as wlm, commentary
from ews_lib.dbx import write_table, read_table, log_step, make_llm, RUN_ID

NB = "08_monitoring"
panel = read_table(spark, C.table(C.SCHEMA_GOLD, "customer_month_features"))
full = read_table(spark, C.table(C.SCHEMA_GOLD, "ews_scores_monthly"))
fired = read_table(spark, C.table(C.SCHEMA_GOLD, "ews_triggers_fired"))
comparison = read_table(spark, C.table(C.SCHEMA_GOLD, "model_comparison"))
bundles = joblib.load(f"{C.artifacts_path()}/ews_bundles.joblib")
champion = bundles["pd_6m"].get("name")
dev, cal, oot = mo.split(panel)

# COMMAND ----------

with log_step(spark, "monitoring_pack", NB) as rec:
    perf = mon.performance_by_month(full[full.month >= pd.Timestamp(C.OOT_OBS[0])])
    stab, csi = mon.stability(mo.predict(bundles["pd_6m"], dev), full, bundles["pd_6m"]["features"], dev)
    ew, lead = mon.early_warning_effectiveness(full, panel)
    last = perf.iloc[-1]
    calib = mon.calibration_by_band(full, last.month)
    ref_gini = float(comparison.set_index("model").loc[champion, "gini"])
    metrics = mon.metrics_table(champion, ref_gini, perf, stab, csi, ew,
                                last.predicted_bad_rate / last.actual_bad_rate, full.month.max())
    rates = mon.trigger_rates(fired, full)
    tiers, moves = mon.watchlist_flows(full)
    months = sorted(full.month.unique())
    trans = wlm.transition_matrix(full, months[-2], months[-1]).reset_index().rename(columns={"tier_from": "from_tier"})
    for name, df in [("monitoring_metrics", metrics.assign(run_id=RUN_ID, created_at=dt.datetime.utcnow())), ("monitoring_performance", perf),
                     ("monitoring_calibration", calib), ("monitoring_stability", stab), ("monitoring_csi", csi),
                     ("monitoring_lead_time", lead), ("monitoring_trigger_rates", rates),
                     ("monitoring_watchlist_tiers", tiers), ("monitoring_watchlist_moves", moves),
                     ("monitoring_transition_matrix", trans)]:
        rec["rows_written"] += write_table(spark, df, C.table(C.SCHEMA_OPS, name),
                                           mode="append" if name == "monitoring_metrics" else "overwrite")

display(metrics[["metric", "value", "amber", "red", "status"]])

# COMMAND ----------

fig, ax = plt.subplots(2, 2, figsize=(14, 9))
ax[0, 0].plot(perf.month, perf.gini, "o-"); ax[0, 0].axhline(0.70, color="orange", ls="--"); ax[0, 0].axhline(0.65, color="red", ls="--")
ax[0, 0].set(title="Gini by observation month (matured, out of time)", ylim=(0.5, 0.9))
ax[0, 1].plot(stab.month, stab.score_psi, "o-"); ax[0, 1].axhline(0.10, color="orange", ls="--"); ax[0, 1].axhline(0.25, color="red", ls="--")
ax[0, 1].set(title="Score PSI against development")
ax[1, 0].bar(calib.risk_band, calib.actual, alpha=0.6, label="Actual"); ax[1, 0].plot(calib.risk_band, calib.predicted, "ko-", label="Predicted")
ax[1, 0].set(title=f"Calibration by risk band ({pd.Timestamp(last.month):%b %Y})", yscale="log"); ax[1, 0].legend()
ld = pd.Series(ew["lead_distribution"]).sort_index()
ax[1, 1].bar(ld.index, ld.values); ax[1, 1].set(title="Months of warning before first missed payment (0 = not flagged)", xlabel="months")
plt.tight_layout(); display(fig)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Watchlist flows: last month to this month

# COMMAND ----------

display(trans)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Inputs that moved most (CSI, latest month against development)

# COMMAND ----------

display(csi.head(15))

# COMMAND ----------

# MAGIC %md
# MAGIC ## AI-written monitoring commentary

# COMMAND ----------

now = full[full.month == full.month.max()]
mv = now.movement.value_counts()
facts = dict(champion=champion, latest_gini=float(last.gini), dev_gini=ref_gini, score_psi=float(stab.iloc[-1].score_psi),
             metrics=metrics[["metric", "value", "amber", "red", "status"]].to_dict("records"),
             watchlist_size=int(now.tier.between(1, 3).sum()), watchlist_share=float(now.tier.between(1, 3).mean()),
             escalations=int(mv.get("Escalated", 0)), de_escalations=int(mv.get("De-escalated", 0)),
             median_lead_months=ew["median_lead"], bad_capture=ew["capture"],
             drifting_features=csi.head(3).to_dict("records"),
             tier_counts=now.tier_name.value_counts().to_dict())
llm = make_llm()
text, source = commentary.monitoring_commentary(facts, llm)
write_table(spark, pd.DataFrame([dict(run_id=RUN_ID, created_at=dt.datetime.utcnow(), source=source, commentary=text)]),
            C.table(C.SCHEMA_OPS, "monitoring_commentary"), mode="append")
print(f"[{source}]\n\n{text}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Optional: Databricks data profiling
# MAGIC For automatic drift dashboards on top of this pack, open `ews_gold.ews_scores_monthly` in Catalog Explorer → **Quality** tab → create a **Time series** profile with `month` as the timestamp column and a one-month granularity. Databricks builds profile and drift metric tables and a dashboard for it.
# MAGIC
# MAGIC **Next:** `09_live_simulator`, then open the app.
