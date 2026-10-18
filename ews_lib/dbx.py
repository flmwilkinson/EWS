"""Databricks helpers: Delta read/write, pipeline run logging and the LLM call.

Only the notebooks import this module; the rest of ews_lib is plain pandas so it
can be unit tested anywhere.
"""
from __future__ import annotations

import datetime as dt
import re
import time
import uuid
from contextlib import contextmanager

import numpy as np
import pandas as pd

from ews_lib.config import LLM_ENDPOINT, LLM_FALLBACK_ENDPOINTS, SCHEMA_OPS, table

RUN_ID = uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# pandas -> Delta
# ---------------------------------------------------------------------------
def sanitize(pdf: pd.DataFrame) -> pd.DataFrame:
    """Make a pandas frame safe for spark.createDataFrame (Arrow) and Delta column rules."""
    df = pdf.copy()
    df.columns = [re.sub(r"[^0-9a-zA-Z_]", "_", str(c)).strip("_").lower() for c in df.columns]
    for c in df.columns:
        s = df[c]
        # Month-end style dates travel as DATE, not TIMESTAMP, so no time zone can shift them
        if pd.api.types.is_datetime64_any_dtype(s) and getattr(s.dt, "tz", None) is None:
            nn = s.dropna()
            if len(nn) and (nn == nn.dt.normalize()).all():
                df[c] = pd.Series([v.date() if pd.notna(v) else None for v in s], index=s.index, dtype=object)
            continue
        if isinstance(s.dtype, pd.CategoricalDtype):
            df[c] = s.astype(str)
            continue
        if s.dtype != object:
            continue
        nn = s.dropna()
        if len(nn) == 0:
            df[c] = s.astype("string")
            continue
        types = {type(v) for v in nn}
        if types <= {bool, np.bool_}:
            df[c] = s.astype("boolean")
        elif types <= {int, float, np.integer, np.floating, np.int64, np.float64}:
            df[c] = pd.to_numeric(s, errors="coerce")
        elif types <= {dt.date}:
            pass  # already DATE values
        elif types <= {pd.Timestamp, dt.datetime, dt.date}:
            df[c] = pd.to_datetime(s)
        elif types <= {str}:
            df[c] = s.astype("string")
        else:
            df[c] = s.map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else str(v)).astype("string")
    return df


def write_table(spark, pdf: pd.DataFrame, fqname: str, mode="overwrite"):
    sdf = spark.createDataFrame(sanitize(pdf))
    (sdf.write.mode(mode).option("overwriteSchema", "true" if mode == "overwrite" else "false")
        .option("mergeSchema", "true" if mode == "append" else "false").saveAsTable(fqname))
    return len(pdf)


def read_table(spark, fqname: str) -> pd.DataFrame:
    df = spark.table(fqname).toPandas()
    for c in df.columns:  # DATE columns come back as python dates; the library works in pandas timestamps
        if df[c].dtype == object:
            nn = df[c].dropna()
            if len(nn) and isinstance(nn.iloc[0], dt.date) and not isinstance(nn.iloc[0], dt.datetime):
                df[c] = pd.to_datetime(df[c])
    return df


# ---------------------------------------------------------------------------
# Pipeline run log (ews_ops.pipeline_runs)
# ---------------------------------------------------------------------------
@contextmanager
def log_step(spark, step: str, notebook: str, run_id: str = RUN_ID):
    """Times a pipeline step and records it, including failures."""
    rec = dict(run_id=run_id, notebook=notebook, step=step, started_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None), rows_written=0,
               status="RUNNING", message=None)
    t0 = time.time()
    try:
        yield rec
        rec["status"] = "SUCCEEDED"
    except Exception as e:
        rec["status"] = "FAILED"
        rec["message"] = str(e)[:500]
        raise
    finally:
        rec["finished_at"] = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        rec["duration_s"] = round(time.time() - t0, 2)
        try:
            write_table(spark, pd.DataFrame([rec]), table(SCHEMA_OPS, "pipeline_runs"), mode="append")
        except Exception as log_err:  # logging must never break the pipeline
            print("Run log not written:", log_err)


# ---------------------------------------------------------------------------
# LLM access (Foundation Model APIs)
# ---------------------------------------------------------------------------
def available_chat_endpoints():
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        names = [e.name for e in w.serving_endpoints.list()]
        return [n for n in names if any(k in n for k in ("claude", "llama", "gpt", "gemini", "qwen", "mixtral", "dbrx"))]
    except Exception as e:
        print("Could not list serving endpoints:", e)
        return []


def pick_endpoint(preferred=LLM_ENDPOINT):
    avail = available_chat_endpoints()
    for name in [preferred] + LLM_FALLBACK_ENDPOINTS:
        if name in avail:
            return name
    return avail[0] if avail else None


def make_llm(endpoint=None, max_tokens=450, temperature=0.2):
    """Return callable(prompt) -> text, or None if no endpoint is reachable."""
    endpoint = endpoint or pick_endpoint()
    if not endpoint:
        print("No chat endpoint found: commentary will use templates.")
        return None
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.serving import ChatMessage, ChatMessageRole
    w = WorkspaceClient()

    def llm(prompt: str) -> str:
        r = w.serving_endpoints.query(name=endpoint, messages=[ChatMessage(role=ChatMessageRole.USER, content=prompt)],
                                      max_tokens=max_tokens, temperature=temperature)
        return r.choices[0].message.content

    llm.endpoint = endpoint
    print("LLM endpoint:", endpoint)
    return llm
