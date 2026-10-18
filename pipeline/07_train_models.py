# %% [markdown]
# # 07 · Build and compare models: scorecard versus machine learning
#
# Six approaches to the same target (**60+ days past due within 6 months**), trained on October 2024 to June 2025,
# Platt-calibrated on July to September 2025 and judged **out of time** on October 2025 to March 2026.
#
# | # | Approach | What it is | Features |
# |---|---|---|---|
# | 1 | Weighted scoring model | Expert weights on KRIs, scored 0 / 0.5 / 1 against amber and red; only the PD mapping is fitted | 17 KRIs |
# | 2 | Logistic regression scorecard | Quantile binning, weight of evidence, logistic regression, points at 600 = 50:1 odds, 20 points to double | 17 characteristics |
# | 3 | Decision tree | Single tree, depth 5 | all |
# | 4 | Random forest | 200 trees, depth 9 | all |
# | 5 | Gradient boosting (LightGBM) | Monotone constraints: risk can only rise with utilisation, missed payments, searches… | all |
# | 6 | Neural network (MLP) | Two hidden layers with early stopping | all |
#
# **Age and vulnerability are excluded from every PD model by design.** Vulnerability routes treatment; it never
# prices or limits credit.
#
# **Champion rule:** the best out-of-time Gini among models that give exact per-customer reasons (scorecard,
# LightGBM with native SHAP, weighted model, tree), with calibration within ±25% and score PSI below 0.25.
# Set `MAX_GINI_GAP` above zero to prefer the fully transparent scorecard when it is that close to the best.
#
# Secondary models: **probability of arrears (3m)**, **probability of forbearance (6m)**, **propensity to pay**
# (cure within 2 months, collections population) and **can't pay versus won't pay**.

# %% Settings and data
import numpy as np
import pandas as pd

from ews_lib import features as fe, models as mo, registry
from ews_lib.lakehouse import lh
from ews_lib.runtime import chart, display, metrics, progress

TARGET = "bad_6m"
MAX_GINI_GAP = 0.0   # e.g. 0.02: choose the scorecard if it is within 2 Gini points of the best eligible model

panel = lh.read("gold.customer_month_features")
dev, cal, oot = mo.split(panel, TARGET)
metrics({"Development": f"{len(dev):,}", "Calibration": f"{len(cal):,}", "Out of time": f"{len(oot):,}",
         "Dev bad rate": f"{dev[TARGET].mean():.2%}", "OOT bad rate": f"{oot[TARGET].mean():.2%}"}, title="Samples")

# %% Train the six PD approaches
rows, fitted = [], {}
zoo = mo.model_zoo()
for i, (name, (model, feats)) in enumerate(zoo.items()):
    progress("Training models", i, len(zoo))
    row, fitted[name] = mo.fit_one(name, model, feats, dev, cal, oot, TARGET)
    rows.append(row)
    print(f"{name:<32} dev Gini {row['dev_gini']:.3f}   OOT Gini {row['gini']:.3f}   KS {row['ks']:.3f}   "
          f"calibration {row['calibration_ratio']:.2f}   {row['train_seconds']:.1f}s")
progress("Training models", len(zoo), len(zoo))
results = mo.finalise_results(rows)

# %% Choose the champion
champion, reason = mo.choose_champion(results, max_gini_gap=MAX_GINI_GAP)
results["is_champion"] = results.model == champion
lh.write("gold.model_comparison", results, description="Six PD approaches compared out of time, with the champion flagged")
print(f"Champion: {champion}\n{reason}")
display(results[["model", "is_champion", "n_features", "dev_gini", "gini", "gini_drop_dev_to_oot", "ks", "brier",
                 "top_decile_capture", "calibration_ratio", "psi_vs_dev", "explainability", "governance_effort"]],
        title="Out-of-time comparison")
order = results.sort_values("gini", ascending=False)
chart("bar", order.model, {"Development Gini": order.dev_gini, "Out-of-time Gini": order.gini},
      title="Discrimination in development and out of time", y_format="dec2", height=300)

# %% ROC curves and calibration (out of time)
y = oot[TARGET].values
roc = pd.concat([mo.roc_points(y, f["p_oot"]).assign(model=n) for n, f in fitted.items()], ignore_index=True)
deciles = pd.concat([mo.decile_table(y, f["p_oot"]).assign(model=n) for n, f in fitted.items()], ignore_index=True)
lh.write("gold.model_roc", roc, description="Out-of-time ROC curve points per model")
lh.write("gold.model_deciles", deciles, description="Predicted and actual bad rate by score decile, per model")
for name in [champion, "Logistic regression scorecard"]:
    d = deciles[deciles.model == name]
    chart("line", d.decile, {"Predicted": d.predicted, "Actual": d.actual}, title=f"Calibration by decile: {name}", y_format="pct")

# %% Inside the scorecard: points table
scorecard = fitted["Logistic regression scorecard"]["model"].scorecard_table()
lh.write("gold.scorecard_points", scorecard, description="Scorecard characteristics, bins, WoE and points")
iv = scorecard.drop_duplicates("feature").sort_values("information_value", ascending=False)
chart("bar", iv.feature, {"Information value": iv.information_value}, title="Scorecard characteristics by information value",
      y_format="dec2", horizontal=True, height=380)
display(scorecard, title="Scorecard points", max_rows=70)

# %% What drives the champion
sample = oot.sample(min(6000, len(oot)), random_state=7)
contrib = mo.contributions(fitted[champion], sample)
imp = pd.DataFrame(dict(feature=contrib.columns, mean_abs_contribution=np.abs(contrib.values).mean(axis=0)))
imp["family"] = imp.feature.map(fe.FEATURE_FAMILY)
imp["share"] = imp.mean_abs_contribution / imp.mean_abs_contribution.sum()
imp = imp.sort_values("share", ascending=False)
imp["model"] = champion
lh.write("gold.feature_importance", imp, description="Champion: mean absolute contribution to the log-odds per feature")
top = imp.head(15)
chart("bar", top.feature, {"Share of explained risk": top.share}, title=f"Top drivers: {champion}", y_format="pct",
      horizontal=True, height=380)
fam = imp.groupby("family").share.sum().sort_values(ascending=False)
chart("bar", fam.index, {"Share": fam.values}, title="Drivers by evidence family", y_format="pct", horizontal=True)

# %% Secondary models
secondary_rows = []
bundles = {}
for tgt, label in [("arrears_3m", "Probability of arrears (3m)"), ("forbearance_6m", "Probability of forbearance (6m)"),
                   ("cure_2m", "Propensity to pay (cure within 2m)")]:
    bundles[tgt] = mo.train_secondary(panel, tgt)
    secondary_rows.append(dict(model=label, target=tgt, **bundles[tgt]["metrics"]))
    print(f"{label:<38} Gini {bundles[tgt]['metrics']['gini']:.3f}")
cp_set = fe.cant_pay_training_set(panel, lh.read("silver.collections_events"))
bundles["cant_pay"] = mo.train_cant_pay(cp_set)
label = "Can't pay vs won't pay"
secondary_rows.append(dict(model=label, target="cant_pay", **bundles["cant_pay"]["metrics"]))
print(f"{label:<38} Gini {bundles['cant_pay']['metrics']['gini']:.3f}  "
      f"(trained on {bundles['cant_pay']['n_train']:,} agent-recorded reasons)")
secondary = pd.DataFrame(secondary_rows)
lh.write("gold.secondary_models", secondary, description="Secondary EWS models: arrears, forbearance, propensity to pay, can't vs won't pay")
display(secondary[["model", "target", "auc", "gini", "ks", "brier", "predicted_bad_rate", "actual_bad_rate", "calibration_ratio"]],
        title="Secondary models")

# %% Register everything in the model registry
SLUG = {"Weighted scoring model": "pd6m_weighted_score", "Logistic regression scorecard": "pd6m_scorecard",
        "Decision tree": "pd6m_decision_tree", "Random forest": "pd6m_random_forest",
        "Gradient boosting (LightGBM)": "pd6m_lightgbm", "Neural network (MLP)": "pd6m_mlp"}
res = results.set_index("model")
for name in sorted(fitted, key=lambda n: n == champion):  # champion last, so it demotes the previous one
    registry.register(SLUG[name], fitted[name], family="pd_6m", stage="Champion" if name == champion else "Challenger",
                      approach=name, target=TARGET, metrics=res.loc[name, ["dev_gini", "gini", "ks", "brier", "calibration_ratio", "psi_vs_dev"]].to_dict(),
                      description=f"6-month PD: {name}")
for tgt, slug in [("arrears_3m", "p_arrears_3m"), ("forbearance_6m", "p_forbearance_6m"), ("cure_2m", "propensity_to_pay"),
                  ("cant_pay", "cant_pay_vs_wont_pay")]:
    registry.register(slug, dict(bundles[tgt], name=slug), stage="Champion", family=slug, approach="Gradient boosting (LightGBM)",
                      target=tgt, metrics=bundles[tgt]["metrics"], description=secondary.set_index("target").loc[tgt, "model"])
display(pd.DataFrame([m for m in registry.list_models() if m["stage"] != "Archived"])[["name", "version", "stage", "family", "approach", "n_features"]],
        title="Model registry")

# %% AI comparison for the modelling team
from ews_lib import commentary, llm
prompt, fallback = commentary.build_model_comparison(results, champion, reason)
text, source, meta = llm.generate(prompt, fallback, purpose="model_comparison", echo=True)
lh.write("gold.model_comparison_commentary", pd.DataFrame([dict(created_at=pd.Timestamp.now(), source=source, champion=champion,
                                                                reason=reason, commentary=text)]),
         mode="append", description="AI-written model comparison, one row per run")
