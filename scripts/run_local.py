"""Run the whole EWS pipeline locally (no Databricks) to validate the library.

    pip install pandas numpy scikit-learn lightgbm pyarrow duckdb
    python scripts/run_local.py            # full run, ~3-4 minutes
    python scripts/run_local.py --live 10  # also play 10 live days

Outputs land in ./local_output as parquet, one file per table the notebooks
would write to Unity Catalog.
"""
import argparse
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402
import pyarrow as pa  # noqa: E402

from ews_lib import (commentary, dq, features as fe, generator, live, models as mo, monitoring as mon,  # noqa: E402
                     scoring as sc, thresholds as th)
from ews_lib.config import OOT_OBS  # noqa: E402
from ews_lib.dbx import sanitize  # noqa: E402

OUT = os.path.join(ROOT, "local_output")
os.makedirs(OUT, exist_ok=True)


def save(name, df):
    df = sanitize(df)
    pa.Table.from_pandas(df)  # proves the frame converts cleanly through Arrow, as Spark will
    df.to_parquet(os.path.join(OUT, f"{name}.parquet"), index=False)
    print(f"  wrote {name:<34} {len(df):>9,} rows")


def step(msg):
    print(f"\n== {msg} ({time.strftime('%H:%M:%S')})")


def main(live_days=0):
    step("01 generate")
    raw = generator.generate_all()
    generator.write_landing(raw, os.path.join(OUT, "landing"))

    step("03 data quality")
    con = duckdb.connect()
    for k, v in raw.items():
        con.register(k, v)
    res = dq.run_rules(lambda q: con.execute(q).df(), {k: k for k in raw})
    silver, quarantine, remediation = dq.conform_to_silver(raw)
    save("ops_dq_results", res)
    save("ops_dq_remediation_log", remediation)
    save("silver_dq_quarantine", quarantine)
    dq_text = commentary.dq_commentary(dq.dq_summary_facts(res, remediation))[0]
    print(dq_text)
    save("ops_dq_commentary", pd.DataFrame([dict(created_at=pd.Timestamp.now(), source="template", commentary=dq_text)]))

    step("04 features")
    panel = fe.build_panel(silver)
    save("gold_customer_month_features", panel)

    step("05 thresholds")
    dev, cal, oot = mo.split(panel)
    thr = th.seed_thresholds(dev)
    save("ops_kri_thresholds", thr)
    save("ops_trigger_effectiveness", th.trigger_effectiveness(dev, thr))

    step("06 models")
    results, fitted, _ = mo.train_and_compare(panel)
    champ, why = mo.choose_champion(results)
    save("gold_model_comparison", results)
    save("gold_scorecard_points", fitted["Logistic regression scorecard"]["model"].scorecard_table())
    print(results[["model", "dev_gini", "gini", "ks", "brier", "calibration_ratio", "psi_vs_dev"]].to_string())
    print("Champion:", champ, "-", why)
    mc_text = commentary.model_comparison_commentary(results, champ, why)[0]
    print(mc_text)
    results["is_champion"] = results.model == champ
    save("gold_model_comparison", results)
    save("gold_model_comparison_commentary", pd.DataFrame([dict(created_at=pd.Timestamp.now(), commentary=mc_text)]))
    bundles = {"pd_6m": fitted[champ]}
    for tgt in ["arrears_3m", "forbearance_6m", "cure_2m"]:
        bundles[tgt] = mo.train_secondary(panel, tgt)
    bundles["cant_pay"] = mo.train_cant_pay(fe.cant_pay_training_set(panel, silver["collections_events"]))

    step("07 score, trigger, watchlist")
    months = sorted(panel.month.unique())[-18:]
    full, fired = sc.run_batch(panel, bundles, thr, months, silver["customers"])
    latest = sc.latest_decisions(full)
    wl_latest = sc.explain(latest[latest.tier >= 1], bundles["pd_6m"])
    save("gold_ews_scores_monthly", full)
    save("gold_ews_triggers_fired", fired)
    save("gold_ews_decisions_latest", latest)
    save("gold_watchlist_latest_explained", wl_latest)
    print(full[full.month == full.month.max()].tier_name.value_counts().to_string())

    step("08 monitoring")
    oot_full = full[full.month >= pd.Timestamp(OOT_OBS[0])]
    perf = mon.performance_by_month(oot_full)
    stab, csi = mon.stability(mo.predict(bundles["pd_6m"], dev), full, bundles["pd_6m"]["features"], dev)
    ew, lead = mon.early_warning_effectiveness(full, panel)
    ref_gini = results.set_index("model").loc[champ, "gini"]
    last = perf.iloc[-1]
    metrics = mon.metrics_table(champ, ref_gini, perf, stab, csi, ew, last.predicted_bad_rate / last.actual_bad_rate, full.month.max())
    save("ops_monitoring_metrics", metrics.assign(created_at=pd.Timestamp.now()))
    print(metrics[["metric", "value", "status"]].to_string())
    tiers_now = full[full.month == full.month.max()]
    tiers_prev = full[full.month == sorted(full.month.unique())[-2]]
    moves = tiers_now.movement.value_counts()
    facts = dict(champion=champ, latest_gini=float(last.gini), dev_gini=float(ref_gini),
                 score_psi=float(stab.iloc[-1].score_psi), metrics=metrics.to_dict("records"),
                 watchlist_size=int(tiers_now.tier.between(1, 3).sum()),
                 watchlist_share=float(tiers_now.tier.between(1, 3).mean()),
                 escalations=int(moves.get("Escalated", 0)), de_escalations=int(moves.get("De-escalated", 0)),
                 median_lead_months=ew["median_lead"], bad_capture=ew["capture"],
                 drifting_features=csi.head(3).to_dict("records"))
    mon_text = commentary.monitoring_commentary(facts)[0]
    print(mon_text)
    save("ops_monitoring_commentary", pd.DataFrame([dict(created_at=pd.Timestamp.now(), commentary=mon_text)]))
    save("ops_pipeline_runs", pd.DataFrame([dict(notebook="run_local", step="all", status="SUCCEEDED", rows_written=0,
                                                 duration_s=0.0, finished_at=pd.Timestamp.now())]))

    if live_days:
        step(f"09 live simulator ({live_days} days)")
        cm = silver["card_monthly"]
        lim = cm[cm.month == cm.month.max()][["customer_id", "credit_limit"]]
        sim = live.LiveSimulator(latest.merge(lim, on="customer_id", how="left"), bundles, thr, personas=raw["personas"])
        acc = {k: [] for k in ["events", "alerts", "kpis", "personas"]}
        for _ in range(live_days):
            o = sim.tick()
            for key in acc:
                acc[key].append(o[key])
            k = o["kpis"].iloc[0]
            print(f"  day {int(k.sim_day):>2}: {int(k.events_today):>3} events, {int(k.alerts_today):>2} alerts, watchlist {int(k.watchlist_total):,}")
        for key, frames in acc.items():
            save(f"gold_live_{key}", pd.concat(frames, ignore_index=True))
        save("gold_live_watchlist", sim.watchlist_snapshot())
        save("gold_live_persona_cards", sim.persona_cards())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", type=int, default=0)
    main(ap.parse_args().live)
