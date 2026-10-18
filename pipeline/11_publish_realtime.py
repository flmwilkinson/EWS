# %% [markdown]
# # 11 · Publish to the real-time service
#
# Promotes the batch outputs to the **real-time EWS service**:
#
# * the champion models from the registry (PD plus the four secondary models),
# * the approved trigger thresholds,
# * the latest month-end position of every customer (the starting state for intra-month events).
#
# The task smoke-tests the bundle on the customers in focus, measures scoring latency, then writes the **serving
# manifest**. The service watches the manifest and hot-reloads, after which:
#
# * `POST /api/v1/score` scores a customer (or a feature payload) in real time,
# * `POST /api/v1/events` ingests an event (a missed payment reported by a bureau, a salary that did not arrive)
#   and re-scores, re-triggers and re-tiers the customer immediately,
# * the **Live** page streams events, alerts and tier changes.

# %% Load what will be served
import time

import pandas as pd

from ews_lib import models as mo, registry, scoring as sc, triggers
from ews_lib.lakehouse import lh
from ews_lib.runtime import display, metrics

FAMILIES = {"pd_6m": "pd_6m", "arrears_3m": "p_arrears_3m", "forbearance_6m": "p_forbearance_6m",
            "cure_2m": "propensity_to_pay", "cant_pay": "cant_pay_vs_wont_pay"}
bundles = {k: registry.load_champion(f) for k, f in FAMILIES.items()}
thr = lh.read("ops.kri_thresholds")
state = lh.read("gold.ews_decisions_latest")
display(pd.DataFrame([dict(role=k, model=b["_registry"]["name"], version=b["_registry"]["version"], features=len(b["features"]))
                      for k, b in bundles.items()]), title="Champion models to serve")

# %% Smoke test on the customers in focus
personas = state[state.persona.notna()]
scored = sc.score_frame(personas, bundles)
_, summ = triggers.evaluate(scored.assign(pd_ratio_3m=1.0), thr)
check = scored[["customer_id", "persona", "pd_6m", "risk_band", "p_arrears_3m", "p_cant_pay"]].assign(
    batch_pd=personas.pd_6m.values, triggers=summ.fired_triggers.values)
assert (check.pd_6m - check.batch_pd).abs().max() < 1e-9, "Serving bundle does not reproduce the batch scores"
display(check.round(4), title="Customers in focus: served model reproduces the batch scores")

# %% Latency test
sample = state.sample(min(2000, len(state)), random_state=1)
t0 = time.perf_counter()
for _ in range(3):
    mo.predict(bundles["pd_6m"], sample)
batch_ms = (time.perf_counter() - t0) / 3 * 1000
one = state.iloc[[0]]
t0 = time.perf_counter()
for _ in range(50):
    sc.score_frame(one, bundles)
single_ms = (time.perf_counter() - t0) / 50 * 1000
metrics({"Single customer, all five models": f"{single_ms:.1f} ms", "PD for 2,000 customers": f"{batch_ms:.0f} ms",
         "Customers in starting state": f"{len(state):,}"}, title="Scoring latency")

# %% Write the serving manifest
manifest = registry.publish(dict(
    models={k: b["_registry"] for k, b in bundles.items()},
    champion_approach=bundles["pd_6m"]["name"],
    thresholds=dict(table="ops.kri_thresholds", version=lh.history("ops.kri_thresholds")[0]["version"], triggers=len(thr)),
    state=dict(table="gold.ews_decisions_latest", version=lh.history("gold.ews_decisions_latest")[0]["version"],
               customers=len(state), as_at=str(pd.Timestamp(state.month.max()).date())),
    latency_ms=dict(single_customer=round(single_ms, 2), batch_2000=round(batch_ms, 1)),
))
print("Published. The real-time service reloads automatically.")
display({k: v for k, v in manifest.items() if k in ("champion_approach", "models", "thresholds", "state", "published_at")}, title="Serving manifest")
