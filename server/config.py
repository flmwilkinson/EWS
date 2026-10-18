"""Server settings and the pipeline definition (the Workflows DAG)."""
from __future__ import annotations

import os
import pathlib

from ews_lib.config import DATA_DIR, PROJECT_ROOT

ROOT = PROJECT_ROOT
PIPELINE_DIR = ROOT / "pipeline"
WEB_DIR = ROOT / "web"
META_DB = DATA_DIR / "meta.db"
HOST = os.getenv("EWS_HOST", "127.0.0.1")
PORT = int(os.getenv("EWS_PORT", "8765"))
MAX_PARALLEL_TASKS = int(os.getenv("EWS_MAX_PARALLEL", "2"))  # set to 1 on a machine short of memory

# Editable in the Workspace (paths relative to ROOT)
WORKSPACE_FOLDERS = ["pipeline", "ews_lib"]

JOB_NAME = "EWS monthly pipeline"

# key, notebook, title, depends on, DAG column, DAG row (0 top, 1 main line, 2 bottom), description
TASKS = [
    dict(key="generate", notebook="01_source_extracts.py", title="Land source extracts", deps=[], col=0, row=1,
         group="Data", desc="Month-end extracts from every source system, landed in their native formats"),
    dict(key="ingest", notebook="02_ingest_bronze.py", title="Ingest to bronze", deps=["generate"], col=1, row=1,
         group="Data", desc="Read every source through its connector into bronze, with lineage columns"),
    dict(key="dq", notebook="03_data_quality.py", title="Data quality", deps=["ingest"], col=2, row=1,
         group="Data", desc="Run the rule catalogue, quarantine and remediate into silver"),
    dict(key="dq_commentary", notebook="04_dq_commentary.py", title="DQ commentary", deps=["dq"], col=3, row=0,
         group="AI", desc="Claude writes the data quality commentary and explains each failed rule"),
    dict(key="features", notebook="05_feature_engineering.py", title="Build features", deps=["dq"], col=3, row=1,
         group="Modelling", desc="Levels, changes, relatives and interactions; forward-looking targets"),
    dict(key="thresholds", notebook="06_seed_thresholds.py", title="Seed thresholds", deps=["features"], col=4, row=1,
         group="Modelling", desc="Policy versus data-calibrated amber and red thresholds for 38 triggers"),
    dict(key="models", notebook="07_train_models.py", title="Train and compare models", deps=["thresholds"], col=5, row=1,
         group="Modelling", desc="Six PD approaches out of time, champion rule, secondary models, registry"),
    dict(key="score", notebook="08_score_watchlist.py", title="Score and watchlist", deps=["models"], col=6, row=1,
         group="EWS", desc="Score 18 months, fire triggers, tier the watchlist, decide for six uses"),
    dict(key="monitoring", notebook="09_monitoring.py", title="Build monitoring", deps=["score"], col=7, row=1,
         group="Monitoring", desc="RAG pack: discrimination, calibration, stability, lead time, flows"),
    dict(key="monitoring_commentary", notebook="10_monitoring_commentary.py", title="Monitoring commentary", deps=["monitoring"],
         col=8, row=1, group="AI", desc="Claude writes the model risk commentary and the CRO summary"),
    dict(key="publish", notebook="11_publish_realtime.py", title="Publish to real time", deps=["score"], col=7, row=2,
         group="EWS", desc="Smoke test, latency test and serving manifest; the live service reloads"),
]
TASK = {t["key"]: t for t in TASKS}
NOTEBOOK_TASK = {t["notebook"]: t["key"] for t in TASKS}


def downstream(key: str) -> list[str]:
    out, frontier = [], [key]
    while frontier:
        k = frontier.pop()
        for t in TASKS:
            if k in t["deps"] and t["key"] not in out:
                out.append(t["key"])
                frontier.append(t["key"])
    return [t["key"] for t in TASKS if t["key"] in out]
