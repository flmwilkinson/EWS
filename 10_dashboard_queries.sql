-- Databricks notebook source
-- MAGIC %md
-- MAGIC # 10 · SQL for an AI/BI dashboard (optional)
-- MAGIC
-- MAGIC Each cell is a ready-made dataset for a Databricks AI/BI dashboard. In the dashboard editor choose **Data → Create from SQL**, paste a query, then add the suggested visual. Change `workspace` if you use a different catalog.

-- COMMAND ----------

-- Watchlist pyramid by month (stacked bar: month × customers, colour = tier_name)
SELECT month, tier, tier_name, COUNT(*) AS customers
FROM workspace.ews_gold.watchlist_monthly
GROUP BY ALL
ORDER BY month, tier;

-- COMMAND ----------

-- Risk band mix, latest month (bar)
SELECT risk_band, COUNT(*) AS customers, ROUND(AVG(pd_6m), 4) AS avg_pd
FROM workspace.ews_gold.ews_decisions_latest
GROUP BY risk_band
ORDER BY risk_band;

-- COMMAND ----------

-- Trigger hit rates over time (line: month × hit_rate, colour = trigger)
SELECT month, CONCAT(trigger_id, ' ', trigger_name) AS trigger, severity, hit_rate
FROM workspace.ews_ops.monitoring_trigger_rates
WHERE severity = 'Red'
ORDER BY month;

-- COMMAND ----------

-- Decisions by business use, latest month (table or bar)
SELECT 'Approval, activation and limits' AS use_case, limit_and_activation AS decision, COUNT(*) AS customers FROM workspace.ews_gold.ews_decisions_latest GROUP BY ALL
UNION ALL SELECT 'Pricing', pricing, COUNT(*) FROM workspace.ews_gold.ews_decisions_latest GROUP BY ALL
UNION ALL SELECT 'Exposure management', exposure, COUNT(*) FROM workspace.ews_gold.ews_decisions_latest GROUP BY ALL
UNION ALL SELECT 'Offers and engagement', offers_engagement, COUNT(*) FROM workspace.ews_gold.ews_decisions_latest GROUP BY ALL
UNION ALL SELECT 'Support and early intervention', support, COUNT(*) FROM workspace.ews_gold.ews_decisions_latest GROUP BY ALL
UNION ALL SELECT 'Collections treatment', collections, COUNT(*) FROM workspace.ews_gold.ews_decisions_latest GROUP BY ALL
ORDER BY use_case, customers DESC;

-- COMMAND ----------

-- Monitoring RAG (table with conditional formatting on status)
SELECT metric, value, amber, red, status
FROM workspace.ews_ops.monitoring_metrics
WHERE created_at = (SELECT MAX(created_at) FROM workspace.ews_ops.monitoring_metrics);

-- COMMAND ----------

-- Model comparison (grouped bar: model × dev_gini and gini)
SELECT model, is_champion, dev_gini, gini, ks, brier, calibration_ratio, psi_vs_dev
FROM workspace.ews_gold.model_comparison
ORDER BY gini DESC;

-- COMMAND ----------

-- Data quality results, latest run (table)
SELECT rule_id, table_name, dimension, description, severity, records_failed, fail_rate, status, action
FROM workspace.ews_ops.dq_results
WHERE run_id = (SELECT MAX_BY(run_id, run_ts) FROM workspace.ews_ops.dq_results);

-- COMMAND ----------

-- Live: watchlist by simulated day (line)
SELECT sim_day, tier_1_monitor, tier_2_watch, tier_3_early_intervention, tier_4_collections
FROM workspace.ews_gold.live_kpis
ORDER BY sim_day;

-- COMMAND ----------

-- Live: regional concentration of the watchlist (heatmap: region × tier_name)
SELECT region, tier_name, COUNT(*) AS customers
FROM workspace.ews_gold.live_watchlist
GROUP BY ALL;
