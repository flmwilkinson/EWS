"""Notebook runtime: display(), chart(), metrics() and the event hook.

When a notebook runs inside the Early Warning Studio kernel, `set_emitter` is
called with a function that sends structured events back to the server (cell
output, table writes, LLM calls). Run from the command line, the same calls
fall back to plain printed output, so every notebook also works as a script.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os

import numpy as np
import pandas as pd

_emit = None


def set_emitter(fn):
    global _emit
    _emit = fn


def emit(event: dict):
    if _emit is not None:
        try:
            _emit(event)
        except Exception:  # output must never break the notebook
            pass


def in_kernel() -> bool:
    return _emit is not None


def run_context() -> dict:
    return dict(run_id=os.getenv("EWS_RUN_ID", "interactive"), task=os.getenv("EWS_TASK", "interactive"))


# ---------------------------------------------------------------------------
# JSON-safe conversion
# ---------------------------------------------------------------------------
def jsonable(v):
    if v is None:
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(v, (pd.Timestamp, dt.datetime)):
        return None if pd.isna(v) else v.isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)


def frame_payload(df: pd.DataFrame, max_rows=50):
    df = df.reset_index(drop=True) if not isinstance(df.index, pd.RangeIndex) else df
    head = df.head(max_rows)
    cols = [str(c) for c in head.columns]
    dtypes = {str(c): str(t) for c, t in head.dtypes.items()}
    rows = [[jsonable(v) for v in r] for r in head.itertuples(index=False, name=None)]
    return dict(columns=cols, dtypes=dtypes, rows=rows, total_rows=int(len(df)))


# ---------------------------------------------------------------------------
# Public helpers used in notebooks
# ---------------------------------------------------------------------------
def display(obj, title: str | None = None, max_rows: int = 50):
    """Show a DataFrame, Series, dict or text as rich cell output."""
    if isinstance(obj, pd.Series):
        obj = obj.reset_index()
    if isinstance(obj, pd.DataFrame):
        if in_kernel():
            emit(dict(event="display", kind="table", title=title, data=frame_payload(obj, max_rows)))
        else:
            if title:
                print(f"\n{title}")
            with pd.option_context("display.max_columns", 30, "display.width", 200):
                print(obj.head(max_rows).to_string())
        return
    if isinstance(obj, dict):
        if in_kernel():
            emit(dict(event="display", kind="kv", title=title, data=jsonable(obj)))
        else:
            print(json.dumps(jsonable(obj), indent=2))
        return
    if in_kernel():
        emit(dict(event="display", kind="markdown", title=title, data=str(obj)))
    else:
        print(obj)


def metrics(items: dict, title: str | None = None):
    """A row of KPI tiles: {"Rows": 12000, "Gini": "0.719"}."""
    if in_kernel():
        emit(dict(event="display", kind="metrics", title=title, data=[dict(label=k, value=jsonable(v)) for k, v in items.items()]))
    else:
        print("  ".join(f"{k}: {v}" for k, v in items.items()))


def chart(kind: str, x, series: dict, title: str | None = None, y_format: str | None = None, x_label=None, y_label=None,
          height: int = 260, horizontal: bool = False):
    """A small chart in the cell output.

    kind      "bar" | "line" | "scatter"
    x         category labels or numeric x values
    series    {"Series name": [values]}
    y_format  None | "pct" | "int" | "dec2"
    """
    payload = dict(kind=kind, x=jsonable(list(x)), series=[dict(name=k, data=jsonable(list(v))) for k, v in series.items()],
                   y_format=y_format, x_label=x_label, y_label=y_label, height=height, horizontal=horizontal)
    if in_kernel():
        emit(dict(event="display", kind="chart", title=title, data=payload))
    else:
        print(f"[chart] {title or kind}: {len(payload['x'])} points, series {list(series)}")


def progress(label: str, done: int, total: int):
    """Progress bar inside the running cell (for long loops)."""
    if in_kernel():
        emit(dict(event="progress", label=label, done=int(done), total=int(total)))
    else:
        print(f"  {label}: {done}/{total}")
