# %% [markdown]
# # 09 · Monitoring the early warning system
#
# A monthly RAG pack covering the model and the system around it.
#
# | Area | Metric | Amber | Red |
# |---|---|---|---|
# | Discrimination | Gini on the latest matured month | < 0.70 | < 0.65 |
# | Discrimination | Gini drop against approval | > 5 pts | > 10 pts |
# | Calibration | Predicted ÷ actual bad rate | ±20% | ±35% |
# | Stability | Score PSI against development | > 0.10 | > 0.25 |
# | Stability | Largest feature CSI | > 0.10 | > 0.25 |
# | Early warning | Share of 60+ DPD customers flagged (Watch or Early intervention) before their first missed payment | < 60% | < 45% |
# | Early warning | Median months of warning | < 2 | < 1 |
# | Early warning | Precision: new Watch or Early intervention entrants who go bad within 6 months | < 10% | < 5% |
#
# Outcomes take six months to mature, so performance is measured on the out-of-time months (October 2025 to
# March 2026), while stability, trigger rates and watchlist flows use every month up to September 2026.

# %% Load scores and the champion
import datetime as dt

import pandas as pd

from ews_lib import config as C, models as mo, monitoring as mon, registry, watchlist as wlm
from ews_lib.lakehouse import lh
from ews_lib.runtime import chart, display, metrics, run_context

panel = lh.read("gold.customer_month_features")
full = lh.read("gold.ews_scores_monthly")
fired = lh.read("gold.ews_triggers_fired")
comparison = lh.read("gold.model_comparison")
pd_bundle = registry.load_champion("pd_6m")
champion = pd_bundle["name"]
dev, cal, oot = mo.split(panel)
print(f"Champion: {champion} ({pd_bundle['_registry']['name']} v{pd_bundle['_registry']['version']})")

# %% Performance, stability and early warning effectiveness
perf = mon.performance_by_month(full[full.month >= pd.Timestamp(C.OOT_OBS[0])])
stab, csi = mon.stability(mo.predict(pd_bundle, dev), full, pd_bundle["features"], dev)
ew, lead = mon.early_warning_effectiveness(full, panel)
last = perf.iloc[-1]
calib = mon.calibration_by_band(full, last.month)
ref_gini = float(comparison.set_index("model").loc[champion, "gini"])
pack = mon.metrics_table(champion, ref_gini, perf, stab, csi, ew, last.predicted_bad_rate / last.actual_bad_rate, full.month.max())
pack = pack.assign(run_id=run_context()["run_id"], created_at=dt.datetime.now())
lh.write("ops.monitoring_metrics", pack, mode="append", description="RAG metrics against appetite, one set per run")
metrics({m.metric: f"{m.value:.3g} · {m.status}" for m in pack.itertuples()}, title="RAG pack")

# %% Write the monitoring tables
rates = mon.trigger_rates(fired, full)
tiers, moves = mon.watchlist_flows(full)
months = sorted(full.month.unique())
trans = wlm.transition_matrix(full, months[-2], months[-1]).reset_index().rename(columns={"tier_from": "from_tier"})
for name, df, desc in [("monitoring_performance", perf, "Gini and bad rates by matured observation month"),
                       ("monitoring_calibration", calib, "Predicted and actual bad rate by risk band"),
                       ("monitoring_stability", stab, "Score PSI against development by month"),
                       ("monitoring_csi", csi, "Characteristic stability index per feature, latest month"),
                       ("monitoring_lead_time", lead, "Months of warning before each customer's first missed payment"),
                       ("monitoring_trigger_rates", rates, "Trigger hit rates by month"),
                       ("monitoring_watchlist_tiers", tiers, "Customers by tier by month"),
                       ("monitoring_watchlist_moves", moves, "Escalations and de-escalations by month"),
                       ("monitoring_transition_matrix", trans, "Tier transitions, last month to this month")]:
    lh.write(f"ops.{name}", df, description=desc)

# %% Discrimination and stability over time
lbl = lambda s: [pd.Timestamp(m).strftime("%b %y") for m in s]
chart("line", lbl(perf.month), {"Gini": perf.gini}, title="Gini by matured observation month (appetite: amber 0.70, red 0.65)", y_format="dec2")
chart("line", lbl(stab.month), {"Score PSI": stab.score_psi}, title="Score PSI against development (amber 0.10, red 0.25)", y_format="dec2")
chart("bar", calib.risk_band, {"Predicted": calib.predicted, "Actual": calib.actual},
      title=f"Calibration by risk band ({pd.Timestamp(last.month):%b %Y})", y_format="pct")
ld = pd.Series(ew["lead_distribution"]).sort_index()
chart("bar", [str(i) for i in ld.index], {"Customers": ld.values}, title="Months of warning before the first missed payment (0 = not flagged)", y_format="int")

# %% Watchlist transitions and drifting inputs
display(trans, title=f"Tier transitions, {pd.Timestamp(months[-2]):%b} → {pd.Timestamp(months[-1]):%b %Y}")
display(csi.head(15), title="Inputs that moved most (CSI)")
