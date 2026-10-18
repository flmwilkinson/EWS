"""Model development: six approaches to the same 6-month PD, compared like for like.

  1. Weighted scoring model   expert weights on KRIs, no fitting beyond a PD mapping
  2. Logistic scorecard       WoE binning + logistic regression + points (PDO)
  3. Decision tree            single interpretable tree
  4. Random forest            bagged trees
  5. Gradient boosting        LightGBM with monotone constraints on key features
  6. Neural network           multi-layer perceptron

All six are calibrated with Platt scaling on the last three months of the
development window and judged on the out-of-time sample.

Secondary models (probability of arrears, forbearance, propensity to pay,
can't pay vs won't pay) use LightGBM with the same helpers.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score, roc_curve
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier

from ews_lib.config import OOT_OBS, RISK_BANDS, TRAIN_OBS
from ews_lib.features import FEATURES, SCORECARD_FEATURES

# Vulnerability is used to route treatment, never to price or limit credit
EXCLUDED_FROM_PD = {"vulnerability_flag", "vulnerability_notes_3m", "age"}  # age excluded by design
ML_FEATURES = [f for f in FEATURES if f not in EXCLUDED_FROM_PD]

MONOTONE_UP = {"util", "util_chg_3m", "missed_6m", "cash_adv_months_3m", "min_pay_streak", "salary_missing_3m",
               "ext_worst_6m", "returned_dd_3m", "dsr", "searches_3m", "od_days"}
MONOTONE_DOWN = {"bureau_score", "pay_ratio"}


# ---------------------------------------------------------------------------
# Data split
# ---------------------------------------------------------------------------
def split(panel, target="bad_6m"):
    # Fixed row order: tables read back from Delta come in any order, and bagging depends on it
    df = panel[panel[target].notna()].sort_values(["month", "customer_id"]).reset_index(drop=True)
    tr = df[(df.month >= TRAIN_OBS[0]) & (df.month <= TRAIN_OBS[1])]
    oot = df[(df.month >= OOT_OBS[0]) & (df.month <= OOT_OBS[1])]
    cal_start = pd.Timestamp(TRAIN_OBS[1]) - pd.DateOffset(months=2)
    dev, cal = tr[tr.month < cal_start], tr[tr.month >= cal_start]
    return dev, cal, oot


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def ks_stat(y, p):
    fpr, tpr, _ = roc_curve(y, p)
    return float(np.max(tpr - fpr))


def psi(expected, actual, bins=10):
    q = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    q[0], q[-1] = -np.inf, np.inf
    e = np.histogram(expected, q)[0] / len(expected)
    a = np.histogram(actual, q)[0] / len(actual)
    e, a = np.clip(e, 1e-6, None), np.clip(a, 1e-6, None)
    return float(np.sum((a - e) * np.log(a / e)))


def evaluate(y, p, p_train=None):
    y = np.asarray(y)
    p = np.clip(np.asarray(p), 1e-6, 1 - 1e-6)
    auc = roc_auc_score(y, p)
    order = np.argsort(-p)
    top = order[: max(1, len(p) // 10)]
    return dict(auc=round(auc, 4), gini=round(2 * auc - 1, 4), ks=round(ks_stat(y, p), 4),
                brier=round(brier_score_loss(y, p), 5), log_loss=round(log_loss(y, p), 5),
                top_decile_capture=round(y[top].sum() / max(y.sum(), 1), 4),
                lift_top_decile=round(y[top].mean() / max(y.mean(), 1e-9), 2),
                predicted_bad_rate=round(p.mean(), 5), actual_bad_rate=round(y.mean(), 5),
                calibration_ratio=round(p.mean() / max(y.mean(), 1e-9), 3),
                psi_vs_dev=round(psi(p_train, p), 4) if p_train is not None else None)


def decile_table(y, p):
    d = pd.DataFrame(dict(y=y, p=p))
    d["decile"] = pd.qcut(d.p.rank(method="first"), 10, labels=False) + 1
    return d.groupby("decile").agg(n=("y", "size"), predicted=("p", "mean"), actual=("y", "mean")).reset_index()


def band(pd_value):
    for b, lo, hi in RISK_BANDS:
        if lo <= pd_value < hi:
            return b
    return RISK_BANDS[-1][0]


# ---------------------------------------------------------------------------
# Platt calibration
# ---------------------------------------------------------------------------
class Identity:
    def __call__(self, raw):
        return raw


class Platt:
    def fit(self, raw, y):
        x = np.log(np.clip(raw, 1e-6, 1 - 1e-6) / (1 - np.clip(raw, 1e-6, 1 - 1e-6))).reshape(-1, 1)
        self.lr = LogisticRegression(C=1e6).fit(x, y)
        return self

    def __call__(self, raw):
        x = np.log(np.clip(raw, 1e-6, 1 - 1e-6) / (1 - np.clip(raw, 1e-6, 1 - 1e-6))).reshape(-1, 1)
        return self.lr.predict_proba(x)[:, 1]


# ---------------------------------------------------------------------------
# 1. Weighted scoring model (expert)
# ---------------------------------------------------------------------------
EXPERT_WEIGHTS = {  # feature: (weight, amber point, red point, direction)
    "util": (14, 0.80, 0.95, 1), "util_chg_3m": (8, 0.20, 0.35, 1), "pay_ratio": (5, 0.10, 0.03, -1),
    "min_pay_streak": (7, 3, 6, 1), "cash_adv_months_3m": (8, 1, 2, 1), "missed_6m": (14, 1, 2, 1),
    "salary_missing_3m": (9, 1, 2, 1), "income_chg_vs_6m": (6, -0.20, -0.40, -1), "od_days": (5, 10, 20, 1),
    "returned_dd_3m": (6, 1, 3, 1), "gambling_share": (5, 0.10, 0.25, 1), "dsr": (6, 0.45, 0.60, 1),
    "bureau_score": (10, 600, 500, -1), "searches_3m": (5, 3, 6, 1), "ext_worst_6m": (12, 1, 2, 1),
    "unemp_chg_12m": (2, 0.5, 1.0, 1), "hardship_notes_3m": (5, 1, 2, 1),
}


class WeightedScore:
    """Each KRI scores 0 (below amber), 0.5 (amber) to 1 (red), linearly between; weighted sum."""
    name = "Weighted scoring model"

    def _points(self, X):
        total = np.zeros(len(X))
        for f, (w, a, r, d) in EXPERT_WEIGHTS.items():
            x = X[f].fillna(a if d > 0 else a).values.astype(float)
            if d < 0:
                x, a, r = -x, -a, -r
            s = np.where(x >= r, 1.0, np.where(x >= a, 0.5 + 0.5 * (x - a) / max(r - a, 1e-9), 0.0))
            total += w * s
        return total

    def fit(self, X, y):
        pts = self._points(X).reshape(-1, 1)
        self.map = LogisticRegression().fit(pts, y)
        return self

    def predict_proba(self, X):
        p = self.map.predict_proba(self._points(X).reshape(-1, 1))[:, 1]
        return np.column_stack([1 - p, p])

    def contributions(self, X):
        out = {}
        for f, (w, a, r, d) in EXPERT_WEIGHTS.items():
            x = X[f].fillna(a).values.astype(float)
            aa, rr = (a, r) if d > 0 else (-a, -r)
            xx = x if d > 0 else -x
            out[f] = w * np.where(xx >= rr, 1.0, np.where(xx >= aa, 0.5 + 0.5 * (xx - aa) / max(rr - aa, 1e-9), 0.0))
        return pd.DataFrame(out, index=X.index)


# ---------------------------------------------------------------------------
# 2. WoE logistic scorecard
# ---------------------------------------------------------------------------
class WoEScorecard:
    name = "Logistic regression scorecard"

    def __init__(self, features=SCORECARD_FEATURES, n_bins=6, pdo=20, base_score=600, base_odds=50):
        self.features, self.n_bins = features, n_bins
        self.pdo, self.base_score, self.base_odds = pdo, base_score, base_odds

    def _bin_edges(self, x):
        q = np.unique(np.nanquantile(x, np.linspace(0, 1, self.n_bins + 1)))
        if len(q) < 3:
            q = np.unique(np.concatenate([[np.nanmin(x)], [np.nanmedian(x) + 1e-9], [np.nanmax(x)]]))
        q[0], q[-1] = -np.inf, np.inf
        return q

    def _bin(self, f, x):
        b = pd.cut(x, self.edges[f], labels=False, include_lowest=True)
        return b.fillna(-1).astype(int)  # -1 = missing bin

    def fit(self, X, y):
        y = np.asarray(y)
        self.edges, self.woe, self.iv = {}, {}, {}
        tot_g, tot_b = (y == 0).sum(), (y == 1).sum()
        for f in self.features:
            self.edges[f] = self._bin_edges(X[f].values)
            b = self._bin(f, X[f])
            tab = pd.DataFrame(dict(b=b.values, y=y)).groupby("b").y.agg(["sum", "count"])
            bad = tab["sum"] + 0.5
            good = tab["count"] - tab["sum"] + 0.5
            woe = np.log((good / tot_g) / (bad / tot_b))
            self.woe[f] = woe.to_dict()
            self.iv[f] = float(((good / tot_g - bad / tot_b) * woe).sum())
        W = self._woe_frame(X)
        self.lr = LogisticRegression(C=1.0, max_iter=500).fit(W, y)
        self.factor = self.pdo / np.log(2)
        self.offset = self.base_score - self.factor * np.log(self.base_odds)
        return self

    def _woe_frame(self, X):
        return pd.DataFrame({f: self._bin(f, X[f]).map(self.woe[f]).fillna(0).values for f in self.features}, index=X.index)

    def predict_proba(self, X):
        p = self.lr.predict_proba(self._woe_frame(X))[:, 1]
        return np.column_stack([1 - p, p])

    def score_points(self, X):
        """Credit score: higher is better (log-odds of good scaled with PDO)."""
        logit_bad = self.lr.decision_function(self._woe_frame(X))
        return self.offset + self.factor * (-logit_bad)

    def scorecard_table(self):
        n = len(self.features)
        rows = []
        for i, f in enumerate(self.features):
            coef = self.lr.coef_[0][i]
            for b, w in sorted(self.woe[f].items()):
                e = self.edges[f]
                rng_ = "Missing" if b == -1 else f"{e[b]:.3g} to {e[b + 1]:.3g}"
                pts = -(coef * w + self.lr.intercept_[0] / n) * self.factor + self.offset / n
                rows.append(dict(feature=f, bin=rng_, woe=round(w, 4), coefficient=round(coef, 4),
                                 points=round(pts), information_value=round(self.iv[f], 4)))
        return pd.DataFrame(rows)

    def contributions(self, X):
        W = self._woe_frame(X)
        return W * self.lr.coef_[0]


# ---------------------------------------------------------------------------
# 3-6. Machine learning models
# ---------------------------------------------------------------------------
def _sk_pipeline(est, scale=False):
    steps = [SimpleImputer(strategy="median")]
    if scale:
        steps.append(StandardScaler())
    steps.append(est)
    return make_pipeline(*steps)


def make_lgbm(features, monotone=True, **kw):
    import lightgbm as lgb
    cons = [1 if f in MONOTONE_UP else (-1 if f in MONOTONE_DOWN else 0) for f in features] if monotone else None
    params = dict(n_estimators=250, learning_rate=0.04, num_leaves=15, min_child_samples=600,
                  subsample=0.8, subsample_freq=1, colsample_bytree=0.6, reg_lambda=20.0, verbose=-1,
                  monotone_constraints=cons, random_state=7)
    params.update(kw)
    return lgb.LGBMClassifier(**params)


def model_zoo():
    return {
        "Weighted scoring model": (WeightedScore(), list(EXPERT_WEIGHTS)),
        "Logistic regression scorecard": (WoEScorecard(), SCORECARD_FEATURES),
        "Decision tree": (_sk_pipeline(DecisionTreeClassifier(max_depth=5, min_samples_leaf=300, random_state=7)), ML_FEATURES),
        "Random forest": (_sk_pipeline(RandomForestClassifier(n_estimators=200, max_depth=9, min_samples_leaf=50,
                                                               n_jobs=-1, random_state=7, class_weight="balanced_subsample")), ML_FEATURES),
        "Gradient boosting (LightGBM)": (make_lgbm(ML_FEATURES), ML_FEATURES),
        "Neural network (MLP)": (_sk_pipeline(MLPClassifier(hidden_layer_sizes=(64, 32), alpha=1e-3, early_stopping=True,
                                                            max_iter=200, random_state=7), scale=True), ML_FEATURES),
    }


MODEL_PROFILE = {  # qualitative columns for the comparison table
    "Weighted scoring model": dict(explainability="Very high", governance_effort="Low", needs_data_history="None",
                                   typical_use="Policy rules, new products with no history"),
    "Logistic regression scorecard": dict(explainability="Very high", governance_effort="Low",
                                          needs_data_history="Moderate", typical_use="Regulatory / IRB-style models, adverse action reasons"),
    "Decision tree": dict(explainability="High", governance_effort="Low", needs_data_history="Moderate",
                          typical_use="Segmentation, strategy trees"),
    "Random forest": dict(explainability="Medium (needs SHAP)", governance_effort="Medium",
                          needs_data_history="High", typical_use="Challenger models"),
    "Gradient boosting (LightGBM)": dict(explainability="Medium-high (native SHAP, monotone constraints)",
                                         governance_effort="Medium", needs_data_history="High",
                                         typical_use="Behavioural EWS, collections"),
    "Neural network (MLP)": dict(explainability="Low", governance_effort="High", needs_data_history="Very high",
                                 typical_use="Rich sequence data (transactions), rarely tabular"),
}


def fit_one(name, model, feats, dev, cal, oot, target="bad_6m"):
    """Fit one approach, Platt-calibrate it on the calibration window and score it out of time."""
    t0 = time.time()
    model.fit(dev[feats], dev[target].values)
    raw_cal = model.predict_proba(cal[feats])[:, 1]
    platt = Platt().fit(raw_cal, cal[target].values)
    p_dev = platt(model.predict_proba(dev[feats])[:, 1])
    p_oot = platt(model.predict_proba(oot[feats])[:, 1])
    secs = time.time() - t0
    m = evaluate(oot[target].values, p_oot, p_train=p_dev)
    m_dev = evaluate(dev[target].values, p_dev)
    row = dict(model=name, n_features=len(feats), train_seconds=round(secs, 1), dev_gini=m_dev["gini"], **m, **MODEL_PROFILE[name])
    return row, dict(model=model, calibrator=platt, features=feats, p_oot=p_oot, name=name)


def finalise_results(rows):
    res = pd.DataFrame(rows).sort_values("gini", ascending=False).reset_index(drop=True)
    res["gini_drop_dev_to_oot"] = (res.dev_gini - res.gini).round(4)
    return res


def train_and_compare(panel, target="bad_6m"):
    dev, cal, oot = split(panel, target)
    results, fitted = [], {}
    for name, (model, feats) in model_zoo().items():
        row, fitted[name] = fit_one(name, model, feats, dev, cal, oot, target)
        results.append(row)
    return finalise_results(results), fitted, (dev, cal, oot)


def roc_points(y, p, n=120):
    """Down-sampled ROC curve for charting."""
    fpr, tpr, _ = roc_curve(y, p)
    idx = np.unique(np.linspace(0, len(fpr) - 1, min(n, len(fpr))).astype(int))
    return pd.DataFrame(dict(fpr=np.round(fpr[idx], 4), tpr=np.round(tpr[idx], 4)))


# Governance rule for this demo: the champion must give exact per-customer reasons
# (native contributions) so that every limit, pricing or treatment decision can be
# explained to the customer and to the model validator. Random forest and the MLP
# can still run as challengers.
CHAMPION_ELIGIBLE = ["Gradient boosting (LightGBM)", "Logistic regression scorecard", "Weighted scoring model", "Decision tree"]


def choose_champion(results, max_gini_gap=0.0):
    """Pick the best OOT Gini among well calibrated, stable models. Set `max_gini_gap`
    above zero to prefer the interpretable scorecard when it is that close to the best."""
    ok = results[(results.calibration_ratio.between(0.8, 1.25)) & (results.psi_vs_dev.fillna(0) < 0.25)
                 & results.model.isin(CHAMPION_ELIGIBLE)]
    ok = ok if len(ok) else results
    best = ok.sort_values("gini", ascending=False).iloc[0]
    sc = ok[ok.model == "Logistic regression scorecard"]
    if len(sc) and best.gini - sc.iloc[0].gini <= max_gini_gap:
        return "Logistic regression scorecard", "Within %.0f Gini points of the best model and fully explainable" % (max_gini_gap * 100)
    return best.model, (f"Highest out-of-time Gini ({best.gini:.3f}) among models with exact per-customer "
                        f"explanations, acceptable calibration and stability")


# ---------------------------------------------------------------------------
# Secondary models
# ---------------------------------------------------------------------------
def train_secondary(panel, target, features=None, monotone=False):
    features = features or ML_FEATURES
    dev, cal, oot = split(panel, target)
    m = make_lgbm(features, monotone=monotone, n_estimators=300)
    m.fit(dev[features], dev[target].values)
    platt = Platt().fit(m.predict_proba(cal[features])[:, 1], cal[target].values)
    p = platt(m.predict_proba(oot[features])[:, 1])
    return dict(model=m, calibrator=platt, features=features,
                metrics=evaluate(oot[target].values, p, platt(m.predict_proba(dev[features])[:, 1])))


def train_cant_pay(training_set, features=None):
    features = features or ML_FEATURES + ["vulnerability_notes_3m"]
    features = [f for f in dict.fromkeys(features) if f in training_set]
    df = training_set.sort_values(["month", "customer_id"]).reset_index(drop=True)
    cut = df.month.quantile(0.75)
    dev, test = df[df.month <= cut], df[df.month > cut]
    m = make_lgbm(features, monotone=False, n_estimators=200, min_child_samples=30)
    m.fit(dev[features], dev.cant_pay.values)
    p = m.predict_proba(test[features])[:, 1]
    return dict(model=m, calibrator=Identity(), features=features,
                metrics=evaluate(test.cant_pay.values, p), n_train=len(dev), n_test=len(test))


def predict(bundle, X):
    raw = bundle["model"].predict_proba(X[bundle["features"]])[:, 1]
    return bundle["calibrator"](raw)


# ---------------------------------------------------------------------------
# Explainability
# ---------------------------------------------------------------------------
def contributions(bundle, X):
    """Per-feature contribution to the log-odds, for the champion model."""
    model, feats = bundle["model"], bundle["features"]
    if hasattr(model, "booster_"):
        c = model.predict(X[feats], pred_contrib=True)
        return pd.DataFrame(c[:, :-1], columns=feats, index=X.index)
    if hasattr(model, "contributions"):
        return model.contributions(X[feats])
    raise ValueError("No contribution method for this model")


def top_drivers(contrib: pd.DataFrame, X: pd.DataFrame, k=3):
    """Return, per row, the k features pushing risk up the most, with values."""
    out = []
    vals = contrib.values
    cols = np.array(contrib.columns)
    for i in range(len(contrib)):
        order = np.argsort(-vals[i])[:k]
        out.append([dict(feature=cols[j], contribution=float(vals[i, j]),
                         value=None if pd.isna(X.iloc[i][cols[j]]) else float(X.iloc[i][cols[j]]))
                     for j in order if vals[i, j] > 0])
    return out
