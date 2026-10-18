# Databricks notebook source
# MAGIC %md
# MAGIC # 06 · Build and compare models: scorecard versus machine learning
# MAGIC
# MAGIC Six approaches to the same target (**60+ DPD within 6 months**), trained on October 2024 to June 2025, calibrated on July to September 2025 and judged **out of time** on October 2025 to March 2026.
# MAGIC
# MAGIC | # | Approach | What it is | Features |
# MAGIC |---|---|---|---|
# MAGIC | 1 | Weighted scoring model | Expert weights on KRIs, scored 0 / 0.5 / 1 against the trigger thresholds; only the PD mapping is fitted | 17 KRIs |
# MAGIC | 2 | Logistic regression scorecard | Quantile binning, weight of evidence, logistic regression, points scaled at 600 = 50:1 odds, 20 points to double the odds | 17 characteristics |
# MAGIC | 3 | Decision tree | Single tree, depth 5 | all |
# MAGIC | 4 | Random forest | 200 trees, depth 9 | all |
# MAGIC | 5 | Gradient boosting (LightGBM) | Monotone constraints on key features so risk can only rise with utilisation, missed payments, searches and so on | all |
# MAGIC | 6 | Neural network | Two-layer perceptron with early stopping | all |
# MAGIC
# MAGIC All six are Platt-calibrated so the probabilities are comparable. **Age and vulnerability are excluded from every PD model by design**: vulnerability is used to route treatment, never to price or limit credit.
# MAGIC
# MAGIC **Champion rule:** the best out-of-time Gini among models that give exact per-customer reasons (scorecard, LightGBM with native SHAP, weighted model, tree), with calibration within ±25% and score PSI below 0.25. Random forest and the neural network stay as challengers.
# MAGIC
# MAGIC The same notebook also trains the secondary models: **probability of arrears (3m)**, **probability of forbearance (6m)**, **propensity to pay** (cure within 2 months, collections population) and **can't pay versus won't pay**.

# COMMAND ----------

# MAGIC %pip install -q lightgbm scikit-learn

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys, json, datetime as dt
sys.path.append(os.getcwd())
import numpy as np, pandas as pd, joblib, mlflow
import matplotlib.pyplot as plt
from sklearn.metrics import roc_curve
from ews_lib import config as C, models as mo, features as fe, commentary
from ews_lib.dbx import write_table, read_table, log_step, make_llm, RUN_ID

NB = "06_train_compare_models"
panel = read_table(spark, C.table(C.SCHEMA_GOLD, "customer_month_features"))
collections = read_table(spark, C.table(C.SCHEMA_SILVER, "collections_events"))
user = spark.sql("SELECT current_user()").first()[0]
mlflow.set_experiment(f"/Users/{user}/ews_model_comparison")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Train and compare the six PD models

# COMMAND ----------

with log_step(spark, "train_compare_pd_models", NB) as rec, mlflow.start_run(run_name=f"ews_pd6m_{RUN_ID}") as parent:
    results, fitted, (dev, cal, oot) = mo.train_and_compare(panel)
    champion, reason = mo.choose_champion(results)
    for _, r in results.iterrows():
        with mlflow.start_run(run_name=r.model, nested=True):
            mlflow.log_params(dict(model=r.model, n_features=int(r.n_features), target="bad_6m",
                                   dev_window="2024-10..2025-06", calibration_window="2025-07..2025-09",
                                   oot_window="2025-10..2026-03"))
            mlflow.log_metrics({k: float(r[k]) for k in ["dev_gini", "gini", "ks", "brier", "log_loss",
                                                         "top_decile_capture", "lift_top_decile",
                                                         "calibration_ratio", "psi_vs_dev", "train_seconds"]
                                if pd.notna(r[k])})
            mlflow.set_tag("champion", str(r.model == champion))
    mlflow.set_tag("champion_model", champion)
    results["run_id"] = RUN_ID
    results["is_champion"] = results.model == champion
    rec["rows_written"] = write_table(spark, results, C.table(C.SCHEMA_GOLD, "model_comparison"))

print("Champion:", champion, "|", reason)
display(results[["model", "is_champion", "n_features", "dev_gini", "gini", "gini_drop_dev_to_oot", "ks", "brier",
                 "top_decile_capture", "calibration_ratio", "psi_vs_dev", "train_seconds", "explainability",
                 "governance_effort", "typical_use"]])

# COMMAND ----------

# MAGIC %md
# MAGIC ## ROC curves and calibration (out of time)

# COMMAND ----------

y = oot.bad_6m.values
fig, ax = plt.subplots(1, 2, figsize=(14, 5.5))
for name, f in fitted.items():
    fpr, tpr, _ = roc_curve(y, f["p_oot"])
    g = results.set_index("model").loc[name, "gini"]
    ax[0].plot(fpr, tpr, lw=2.5 if name == champion else 1.2, label=f"{name} (Gini {g:.3f})")
ax[0].plot([0, 1], [0, 1], "k:", lw=0.8)
ax[0].set(title="ROC, out of time", xlabel="False positive rate", ylabel="True positive rate")
ax[0].legend(fontsize=8, loc="lower right")
for name in [champion, "Logistic regression scorecard"]:
    dt_ = mo.decile_table(y, fitted[name]["p_oot"])
    ax[1].plot(dt_.predicted, dt_.actual, "o-", label=name)
mx = max(ax[1].get_xlim()[1], ax[1].get_ylim()[1])
ax[1].plot([0, mx], [0, mx], "k:", lw=0.8)
ax[1].set(title="Calibration by score decile", xlabel="Predicted bad rate", ylabel="Actual bad rate")
ax[1].legend(fontsize=8)
plt.tight_layout()
display(fig)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Inside the two leading approaches
# MAGIC **Scorecard points table**: every characteristic, bin, weight of evidence and points. This is what a credit committee and a validator can read line by line.

# COMMAND ----------

scorecard = fitted["Logistic regression scorecard"]["model"].scorecard_table()
write_table(spark, scorecard, C.table(C.SCHEMA_GOLD, "scorecard_points"))
display(scorecard)

# COMMAND ----------

# MAGIC %md
# MAGIC **Gradient boosting feature importance** (total gain), grouped by evidence family.

# COMMAND ----------

gbm = fitted["Gradient boosting (LightGBM)"]["model"]
imp = pd.DataFrame(dict(feature=fitted["Gradient boosting (LightGBM)"]["features"],
                        gain=gbm.booster_.feature_importance("gain")))
imp["family"] = imp.feature.map(fe.FEATURE_FAMILY)
imp = imp.sort_values("gain", ascending=False)
imp["share"] = imp.gain / imp.gain.sum()
display(imp.head(25))
display(imp.groupby("family").share.sum().sort_values(ascending=False).reset_index())

# COMMAND ----------

# MAGIC %md
# MAGIC ## AI-written comparison for the modelling team

# COMMAND ----------

llm = make_llm()
text, source = commentary.model_comparison_commentary(results, champion, reason, llm)
write_table(spark, pd.DataFrame([dict(run_id=RUN_ID, created_at=dt.datetime.utcnow(), source=source, champion=champion,
                                      commentary=text)]), C.table(C.SCHEMA_GOLD, "model_comparison_commentary"), mode="append")
print(f"[{source}]\n\n{text}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Secondary models

# COMMAND ----------

with log_step(spark, "train_secondary_models", NB) as rec:
    bundles = {"pd_6m": {k: v for k, v in fitted[champion].items() if k != "p_oot"}}
    bundles["pd_6m"]["name"] = champion
    rows = []
    for tgt, label in [("arrears_3m", "Probability of arrears (3m)"), ("forbearance_6m", "Probability of forbearance (6m)"),
                       ("cure_2m", "Propensity to pay (cure within 2m)")]:
        bundles[tgt] = mo.train_secondary(panel, tgt)
        rows.append(dict(model=label, target=tgt, **bundles[tgt]["metrics"]))
    cp_set = fe.cant_pay_training_set(panel, collections)
    bundles["cant_pay"] = mo.train_cant_pay(cp_set)
    rows.append(dict(model="Can't pay vs won't pay", target="cant_pay", **bundles["cant_pay"]["metrics"]))
    secondary = pd.DataFrame(rows)
    rec["rows_written"] = write_table(spark, secondary, C.table(C.SCHEMA_GOLD, "secondary_model_metrics"))
display(secondary[["model", "target", "auc", "gini", "ks", "brier", "predicted_bad_rate", "actual_bad_rate", "calibration_ratio"]])

# COMMAND ----------

# MAGIC %md
# MAGIC Note on **can't pay versus won't pay**: the label is the arrears reason a collections agent recorded (job loss, illness, payment shock, business downturn → can't pay; refused to discuss, dispute → won't pay). In production the bank would use its own reason codes and income and expenditure outcomes. The decision layer lets what the customer has told us override the model.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Save the bundles and register the champion

# COMMAND ----------

os.makedirs(C.artifacts_path(), exist_ok=True)
bundle_path = f"{C.artifacts_path()}/ews_bundles.joblib"
joblib.dump(bundles, bundle_path)
with open(f"{C.artifacts_path()}/champion.json", "w") as f:
    json.dump(dict(champion=champion, reason=reason, run_id=RUN_ID, saved_at=str(dt.datetime.utcnow())), f)
print("Saved", bundle_path)


class EWSChampion(mlflow.pyfunc.PythonModel):
    """Champion PD model plus its Platt calibration, served as one pyfunc."""
    def load_context(self, context):
        import joblib
        self.bundle = joblib.load(context.artifacts["bundle"])["pd_6m"]

    def predict(self, context, model_input):
        from ews_lib.models import predict
        return predict(self.bundle, model_input)


try:
    from mlflow.models import infer_signature
    mlflow.set_registry_uri("databricks-uc")
    X = oot[bundles["pd_6m"]["features"]].head(200)
    sig = infer_signature(X, mo.predict(bundles["pd_6m"], X))
    kw = {"name": "ews_pd6m"} if int(mlflow.__version__.split(".")[0]) >= 3 else {"artifact_path": "ews_pd6m"}
    with mlflow.start_run(run_name=f"register_champion_{RUN_ID}"):
        mlflow.log_param("champion", champion)
        mlflow.pyfunc.log_model(python_model=EWSChampion(), artifacts={"bundle": bundle_path}, code_paths=["ews_lib"],
                                signature=sig, input_example=X.head(3),
                                registered_model_name=f"{C.CATALOG}.{C.SCHEMA_GOLD}.ews_pd6m", **kw)
    print("Registered", f"{C.CATALOG}.{C.SCHEMA_GOLD}.ews_pd6m")
except Exception as e:
    print("Model registration skipped (the demo does not depend on it):", str(e)[:300])

# COMMAND ----------

# MAGIC %md
# MAGIC Open **Experiments → ews_model_comparison** in the left bar to compare the six runs side by side.
# MAGIC
# MAGIC **Next:** `07_score_trigger_watchlist`.
