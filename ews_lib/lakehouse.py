"""A small lakehouse: versioned parquet tables in four schemas, queried with DuckDB.

    data/lakehouse/<schema>/<table>/v0007.parquet   one file per version
    data/lakehouse/<schema>/<table>/_log.jsonl      one line per write (the table history)

Every write records which pipeline run and task produced it, which gives the
catalog its lineage and history views. Readers always see the latest complete
version: files are written to a temporary name and renamed into place.

    from ews_lib.lakehouse import lh
    lh.write("silver.card_monthly", df, description="Conformed card statements")
    df = lh.read("silver.card_monthly")
    lh.sql("SELECT tier_name, COUNT(*) FROM gold.ews_decisions_latest GROUP BY 1")
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time

import numpy as np
import pandas as pd

from ews_lib import runtime
from ews_lib.config import LAKEHOUSE_DIR, LAKEHOUSE_SCHEMAS

KEEP_VERSIONS = 3  # parquet files kept per table; the log keeps every entry


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Make a frame safe for parquet: clean column names, no mixed object columns."""
    out = df.copy()
    out.columns = ["".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(c)).strip("_").lower() for c in out.columns]
    for c in out.columns:
        s = out[c]
        if isinstance(s.dtype, pd.CategoricalDtype):
            out[c] = s.astype(str)
            continue
        if s.dtype != object:
            continue
        nn = s.dropna()
        if len(nn) == 0:
            out[c] = s.astype("string")
            continue
        types = {type(v) for v in nn}
        if types <= {bool, np.bool_}:
            out[c] = s.astype("boolean")
        elif types <= {int, float, np.integer, np.floating, np.int64, np.float64}:
            out[c] = pd.to_numeric(s, errors="coerce")
        elif types <= {pd.Timestamp, dt.datetime, dt.date}:
            out[c] = pd.to_datetime(s)
        elif types <= {str}:
            out[c] = s.astype("string")
        elif types & {list, dict, tuple}:
            out[c] = s.map(lambda v: None if v is None else json.dumps(runtime.jsonable(v))).astype("string")
        else:
            out[c] = s.map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else str(v)).astype("string")
    return out


class Lakehouse:
    def __init__(self, root=LAKEHOUSE_DIR):
        self.root = root
        self._local = threading.local()
        self._db = None
        self._sig = None
        self._db_lock = threading.Lock()

    # -- paths ---------------------------------------------------------------
    def _dir(self, name: str):
        schema, table = name.split(".", 1)
        if schema not in LAKEHOUSE_SCHEMAS:
            raise ValueError(f"Unknown schema '{schema}'. Use one of {LAKEHOUSE_SCHEMAS}.")
        return self.root / schema / table

    def _log(self, d) -> list[dict]:
        p = d / "_log.jsonl"
        if not p.exists():
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    # -- write -----------------------------------------------------------------
    def write(self, name: str, df: pd.DataFrame, mode: str = "overwrite", description: str | None = None) -> int:
        t0 = time.time()
        d = self._dir(name)
        d.mkdir(parents=True, exist_ok=True)
        log = self._log(d)
        data = _prepare(df)
        if mode == "append" and log:
            data = pd.concat([self.read(name), data], ignore_index=True)
        version = (log[-1]["version"] + 1) if log else 1
        path = d / f"v{version:04d}.parquet"
        tmp = d / f".v{version:04d}.parquet.tmp"
        data.to_parquet(tmp, index=False)
        os.replace(tmp, path)
        ctx = runtime.run_context()
        entry = dict(version=version, ts=dt.datetime.now().isoformat(timespec="seconds"), operation="APPEND" if mode == "append" else "OVERWRITE",
                     rows=int(len(data)), n_columns=int(data.shape[1]), file=path.name, bytes=path.stat().st_size,
                     run_id=ctx["run_id"], task=ctx["task"],
                     description=description or (log[-1].get("description") if log else None),
                     columns=[dict(name=c, type=str(t)) for c, t in data.dtypes.items()])
        with open(d / "_log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        for old in sorted(d.glob("v*.parquet"))[:-KEEP_VERSIONS]:
            old.unlink(missing_ok=True)
        runtime.emit(dict(event="table_written", table=name, version=version, rows=entry["rows"],
                          columns=entry["n_columns"], bytes=entry["bytes"], seconds=round(time.time() - t0, 2)))
        if not runtime.in_kernel():
            print(f"  wrote {name:<40} v{version:<3} {len(data):>10,} rows")
        return int(len(data))

    # -- read ------------------------------------------------------------------
    def current_file(self, name: str):
        d = self._dir(name)
        log = self._log(d)
        if not log:
            raise FileNotFoundError(f"Table {name} does not exist yet. Run the task that builds it first.")
        return d / log[-1]["file"]

    def read(self, name: str, columns=None, version: int | None = None) -> pd.DataFrame:
        if version is not None:
            path = self._dir(name) / f"v{version:04d}.parquet"
        else:
            path = self.current_file(name)
        df = pd.read_parquet(path, columns=columns)
        runtime.emit(dict(event="table_read", table=name, version=int(path.stem[1:]), rows=int(len(df))))
        return df

    def exists(self, name: str) -> bool:
        try:
            return bool(self._log(self._dir(name)))
        except ValueError:
            return False

    def history(self, name: str) -> list[dict]:
        d = self._dir(name)
        log = self._log(d)
        present = {p.name for p in d.glob("v*.parquet")}
        for e in log:
            e["available"] = e["file"] in present
        return list(reversed(log))

    def tables(self) -> list[dict]:
        out = []
        for schema in LAKEHOUSE_SCHEMAS:
            sd = self.root / schema
            if not sd.exists():
                continue
            for td in sorted(p for p in sd.iterdir() if p.is_dir()):
                log = self._log(td)
                if not log:
                    continue
                last = log[-1]
                out.append(dict(schema=schema, table=td.name, full_name=f"{schema}.{td.name}", version=last["version"],
                                rows=last["rows"], n_columns=last["n_columns"], bytes=last["bytes"], updated_at=last["ts"],
                                run_id=last.get("run_id"), task=last.get("task"), description=last.get("description")))
        return out

    def drop_all(self):
        import shutil
        if self.root.exists():
            shutil.rmtree(self.root)

    # -- SQL ---------------------------------------------------------------------
    def _signature(self):
        sig = []
        for schema in LAKEHOUSE_SCHEMAS:
            sd = self.root / schema
            if sd.exists():
                for td in sd.iterdir():
                    lp = td / "_log.jsonl"
                    if lp.exists():
                        sig.append((schema, td.name, lp.stat().st_mtime_ns))
        return tuple(sorted(sig))

    def connection(self):
        """A DuckDB cursor for this thread. All cursors share one in-memory database (one buffer pool, capped),
        which holds a view per table; the views are refreshed when any table changes."""
        import duckdb
        with self._db_lock:
            if self._db is None:
                self._db = duckdb.connect()
                self._db.execute(f"SET memory_limit='{os.getenv('EWS_DUCKDB_MEMORY', '768MB')}'")
                self._db.execute("SET threads=4")
                self._sig = None
            sig = self._signature()
            if sig != self._sig:
                for schema in LAKEHOUSE_SCHEMAS:
                    self._db.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
                    for (v,) in self._db.execute(f"SELECT view_name FROM duckdb_views() WHERE schema_name = '{schema}'").fetchall():
                        self._db.execute(f"DROP VIEW IF EXISTS {schema}.{v}")
                for t in self.tables():
                    path = str(self.current_file(t["full_name"])).replace("\\", "/")
                    self._db.execute(f"CREATE OR REPLACE VIEW {t['full_name']} AS SELECT * FROM read_parquet('{path}')")
                self._sig = sig
        cur = getattr(self._local, "cur", None)
        if cur is None:
            cur = self._local.cur = self._db.cursor()
        return cur

    def sql(self, query: str, params=None) -> pd.DataFrame:
        con = self.connection()
        return con.execute(query, params or []).df()

    def sql_on(self, frames: dict):
        """Return run_sql(query) over in-memory frames, e.g. for the data quality rules on bronze data."""
        import duckdb
        con = duckdb.connect()
        for k, v in frames.items():
            con.register(k, v)
        return lambda q: con.execute(q).df()


lh = Lakehouse()
