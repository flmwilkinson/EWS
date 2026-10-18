# %% [markdown]
# # 06 · Trigger catalogue and threshold seeding
#
# The EWS uses **38 triggers across nine dimensions**. Some are classic KRIs (utilisation, DSR, DSCR). Many are not
# PD-based at all: collections status, broken promises to pay, persistent EWI status, hardship or refusal language
# in contact notes, a fixed rate ending. Others sit on model outputs: PD, probability of arrears, probability of
# forbearance, propensity to pay.
#
# **How thresholds are seeded**
# * Every trigger starts with a **policy** amber and red value set by credit risk.
# * For continuous KRIs this task also proposes a **calibrated** value from the development sample: the most lenient
#   threshold at which the 6-month bad rate among triggered customers is at least `AMBER_LIFT` × the portfolio rate
#   (or `RED_LIFT` × for red), while firing for no more than `AMBER_MAX_HIT` (or `RED_MAX_HIT`) of customers, and never
#   less than half as severe as policy.
# * Both are stored with the evidence in `ops.kri_thresholds`, so model risk can see where data and policy disagree.

# %% Calibration settings
import datetime as dt

from ews_lib import models as mo, thresholds as th
from ews_lib.lakehouse import lh
from ews_lib.runtime import chart, display, metrics

AMBER_LIFT, RED_LIFT = 3.0, 6.0          # bad-rate multiple of the portfolio needed to fire
AMBER_MAX_HIT, RED_MAX_HIT = 0.06, 0.02  # maximum share of customers a trigger may fire for

panel = lh.read("gold.customer_month_features")
dev, cal, oot = mo.split(panel)
print(f"Development sample: {len(dev):,} customer-months, 6-month bad rate {dev.bad_6m.mean():.2%}")

# %% Seed the thresholds
thr = th.seed_thresholds(dev, amber_lift=AMBER_LIFT, red_lift=RED_LIFT, amber_max_hit=AMBER_MAX_HIT, red_max_hit=RED_MAX_HIT)
thr["version"] = 1
thr["seeded_at"] = dt.datetime.now()
thr["status"] = "Proposed: pending model risk review"
lh.write("ops.kri_thresholds", thr, description="Trigger catalogue with policy, calibrated and selected amber / red thresholds")
metrics({"Triggers": len(thr), "Dimensions": thr.dimension.nunique(),
         "Calibrated from data": int((thr.threshold_source == "Calibrated").sum()),
         "Policy": int((thr.threshold_source == "Policy").sum())})
display(thr[["trigger_id", "name", "dimension", "kind", "feature", "direction", "policy_amber", "calibrated_amber", "amber",
             "policy_red", "calibrated_red", "red", "threshold_source", "use_cases"]], title="Policy versus calibrated", max_rows=40)

# %% How well each trigger separates future bads
# `lift` = bad rate when the trigger fires ÷ portfolio bad rate. `capture_rate` = share of all future bads it fires for.
eff = th.trigger_effectiveness(dev, thr)
lh.write("ops.trigger_effectiveness", eff, description="Hit rate, bad rate when fired, lift and capture per trigger and severity")
amber = eff[eff.severity == "Amber"].dropna(subset=["lift"]).sort_values("lift", ascending=False).head(15)
chart("bar", amber.name, {"Lift (amber)": amber.lift}, title="Strongest triggers by lift", y_format="dec2", horizontal=True, height=360)
display(eff.sort_values(["severity", "lift"], ascending=[True, False]), title="Trigger effectiveness", max_rows=70)

# %% [markdown]
# Points worth discussing:
# * **Data and policy disagree in both directions.** 80% utilisation fires for about one customer in ten, above the 6%
#   volume cap, so calibration moves amber to 95%. Overdraft reliance goes the other way: five overdrawn days already
#   triples the bad rate, so calibration flags it well before the policy value of ten.
# * Non-PD triggers such as hardship language, a returned direct debit or a payday lender payment have **very high lift
#   but low capture**: rare and strong, which suits escalation rather than ranking.
# * Macro triggers amplify customer evidence in the watchlist rules; they never escalate a customer on their own.
