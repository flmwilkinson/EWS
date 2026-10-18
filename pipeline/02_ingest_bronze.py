# %% [markdown]
# # 02 · Ingest source files into bronze
#
# Each source system has a **connector definition** (`ews_lib/connectors.py`): protocol, endpoint, authentication,
# cadence and the datasets it delivers. This task walks the inbound connectors, reads each landed file with the
# reader for its format, and writes it **unchanged** to the `bronze` schema with three lineage columns:
# `_connector`, `_source_file` and `_loaded_at`.
#
# Bronze keeps the defects on purpose. The data quality task finds them and decides what to do.
#
# The definitions record each system's production interface: Kafka topics for card authorisations and ledger postings,
# an SFTP drop for the monthly bureau file, REST APIs for CRM and lending, webhooks for collections.

# %% Connectors and readers
import datetime as dt
import json

import pandas as pd

from ews_lib import config as C, connectors
from ews_lib.generator import LANDING_FORMATS
from ews_lib.lakehouse import lh
from ews_lib.runtime import display, metrics

DATE_COLUMNS = ["month", "start_month", "as_at_month", "fix_end_month", "open_date", "created_ts"]


def read_landed(fmt: str, path) -> pd.DataFrame:
    if fmt == "csv":
        df = pd.read_csv(path)
    elif fmt == "psv":
        df = pd.read_csv(path, sep="|")
    elif fmt == "parquet":
        df = pd.read_parquet(path)
    elif fmt == "jsonl":
        with open(path, encoding="utf-8") as f:
            df = pd.DataFrame([json.loads(line) for line in f])
    else:
        raise ValueError(f"No reader for format {fmt}")
    for c in DATE_COLUMNS:  # schema inference: ISO dates arrive as text in CSV and JSON
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


display(pd.DataFrame([dict(connector=c["name"], protocol=c["protocol"], cadence=c["cadence"], datasets=", ".join(c["tables"]))
                      for c in connectors.INBOUND]), title="Inbound connectors")

# %% Load every dataset into bronze
loaded_at = dt.datetime.now()
log = []
for conn in connectors.INBOUND:
    print(f"→ {conn['name']} ({conn['protocol']})")
    for name in conn["tables"]:
        fmt, rel = LANDING_FORMATS[name]
        df = read_landed(fmt, C.LANDING_DIR / rel)
        df["_connector"] = conn["id"]
        df["_source_file"] = rel
        df["_loaded_at"] = loaded_at
        lh.write(f"bronze.{name}", df, description=f"{conn['name']}: {rel} as delivered")
        print(f"    {name:<26} {len(df):>9,} rows  ({fmt})")
        log.append(dict(connector=conn["id"], dataset=name, format=fmt, rows=len(df), columns=df.shape[1]))

# %% Official macroeconomic sources (live API pull)
# Live calls to the Bank of England, ONS and HM Land Registry public APIs. The models use the economics team's
# regional scenario; the official series land beside it in bronze for comparison and calibration.
# If a source is unreachable, the last good pull is used and flagged.
from ews_lib import official_data

official = official_data.fetch_all()
lh.write("bronze.macro_official_series", official_data.to_frame(official),
         description="Official UK macro series pulled live: Bank Rate (BoE), unemployment, CPI, earnings (ONS), house prices (HM Land Registry)")
display(pd.DataFrame([dict(provider=official_data.BY_ID[k]["provider"], series=official_data.BY_ID[k]["series"],
                           title=official_data.BY_ID[k]["title"], latest_period=(r.get("latest") or {}).get("period"),
                           latest_value=(r.get("latest") or {}).get("value"), pull="live" if r.get("live") else "cached",
                           latency_ms=r.get("latency_ms"), next_release=r.get("next_release"))
                      for k, r in official.items()]), title="Official sources")

# %% Reference tables (not model inputs)
# Outcome reference data held for model validation and the list of customers in focus. No model reads them.
for name in ["generator_truth", "personas"]:
    fmt, rel = LANDING_FORMATS[name]
    lh.write(f"bronze.{name}", read_landed(fmt, C.LANDING_DIR / rel), description="Reference data for validation: not a model input")

ingested = pd.DataFrame(log)
metrics({"Datasets": len(ingested), "Rows ingested": f"{ingested.rows.sum():,}", "Connectors": ingested.connector.nunique()})
display(ingested, title="Ingestion log")
