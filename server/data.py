"""Cached access to lakehouse tables for the API: a table is re-read only when its version changes."""
from __future__ import annotations

import json
import threading

import numpy as np
import pandas as pd

from ews_lib import runtime
from ews_lib.lakehouse import lh

_cache: dict[str, tuple[int, pd.DataFrame]] = {}
_lock = threading.Lock()


def version(name: str):
    try:
        d = lh._dir(name)
        log = lh._log(d)
        return log[-1]["version"] if log else None
    except Exception:
        return None


def table(name: str, columns=None) -> pd.DataFrame | None:
    v = version(name)
    if v is None:
        return None
    key = name if columns is None else f"{name}:{','.join(columns)}"
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == v:
            return hit[1]
    df = lh.read(name, columns=columns)
    with _lock:
        _cache[key] = (v, df)
    return df


def exists(name):
    return version(name) is not None


def records(df: pd.DataFrame | None, cols=None, limit=None):
    if df is None:
        return []
    if cols:
        df = df[[c for c in cols if c in df.columns]]
    if limit:
        df = df.head(limit)
    out = []
    for r in df.to_dict("records"):
        out.append({k: runtime.jsonable(v) for k, v in r.items()})
    return out


def sql(query: str, params=None):
    return lh.sql(query, params)


def month_label(m):
    return pd.Timestamp(m).strftime("%b %y")


def parse_list(v):
    if v is None:
        return []
    return [x for x in str(v).split(",") if x]


def safe_json(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def num(v, default=0.0):
    try:
        f = float(v)
        return default if np.isnan(f) else f
    except (TypeError, ValueError):
        return default


_champ = {}


def champion_bundle():
    """The registry's PD champion, cached by registry version (used when the live service is not loaded)."""
    from ews_lib import registry
    m = registry.champion_of("pd_6m")
    if m is None:
        return None
    key = (m["name"], m["version"])
    if _champ.get("key") != key:
        _champ.update(key=key, bundle=registry.load(*key))
    return _champ["bundle"]
