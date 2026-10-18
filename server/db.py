"""Platform metadata in SQLite: runs, task runs, lineage, notebook revisions, LLM and API audit logs."""
from __future__ import annotations

import json
import sqlite3
import threading
import time

from server.config import META_DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, job TEXT, trigger TEXT, triggered_by TEXT, scope TEXT, tasks TEXT,
    status TEXT, started_at REAL, finished_at REAL, duration REAL,
    n_tasks INTEGER, n_ok INTEGER DEFAULT 0, n_failed INTEGER DEFAULT 0, n_skipped INTEGER DEFAULT 0,
    rows_written INTEGER DEFAULT 0, llm_calls INTEGER DEFAULT 0, note TEXT
);
CREATE TABLE IF NOT EXISTS task_runs (
    run_id TEXT, task_key TEXT, status TEXT, started_at REAL, finished_at REAL, duration REAL,
    rows_written INTEGER DEFAULT 0, tables_written INTEGER DEFAULT 0, llm_calls INTEGER DEFAULT 0,
    error TEXT, cells TEXT, PRIMARY KEY (run_id, task_key)
);
CREATE TABLE IF NOT EXISTS lineage (
    run_id TEXT, task_key TEXT, table_name TEXT, direction TEXT, version INTEGER, rows INTEGER, ts REAL
);
CREATE INDEX IF NOT EXISTS lineage_table ON lineage(table_name);
CREATE TABLE IF NOT EXISTS revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT, saved_at REAL, content TEXT, note TEXT
);
CREATE INDEX IF NOT EXISTS revisions_path ON revisions(path);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, purpose TEXT, model TEXT, served_by TEXT, status TEXT,
    latency_ms INTEGER, first_token_ms INTEGER, input_tokens INTEGER, output_tokens INTEGER,
    run_id TEXT, task TEXT, prompt TEXT, system TEXT, response TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, method TEXT, path TEXT, status INTEGER, latency_ms REAL,
    request TEXT, response TEXT
);
"""


class DB:
    def __init__(self, path=META_DB):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.con.execute("PRAGMA journal_mode=WAL")
            self.con.executescript(SCHEMA)
            # Runs interrupted by a server restart can never finish
            self.con.execute("UPDATE runs SET status='CANCELLED', note='Server restarted' WHERE status IN ('RUNNING','QUEUED')")
            self.con.execute("UPDATE task_runs SET status='cancelled' WHERE status IN ('running','pending')")

    def execute(self, sql, params=()):
        with self.lock:
            return self.con.execute(sql, params)

    def query(self, sql, params=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.con.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # -- runs -------------------------------------------------------------------
    def insert_run(self, run: dict):
        self.execute("INSERT INTO runs (run_id, job, trigger, triggered_by, scope, tasks, status, started_at, n_tasks) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     (run["run_id"], run["job"], run["trigger"], run["triggered_by"], run["scope"], json.dumps(run["tasks"]),
                      run["status"], run["started_at"], len(run["tasks"])))
        for k in run["tasks"]:
            self.execute("INSERT INTO task_runs (run_id, task_key, status) VALUES (?,?,?)", (run["run_id"], k, "pending"))

    def update_run(self, run_id, **fields):
        cols = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE runs SET {cols} WHERE run_id=?", (*fields.values(), run_id))

    def update_task(self, run_id, task_key, **fields):
        if "cells" in fields and not isinstance(fields["cells"], str):
            fields["cells"] = json.dumps(fields["cells"], default=str)
        cols = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE task_runs SET {cols} WHERE run_id=? AND task_key=?", (*fields.values(), run_id, task_key))

    # -- audit ------------------------------------------------------------------
    def log_llm(self, r: dict):
        self.execute("INSERT INTO llm_calls (ts, purpose, model, served_by, status, latency_ms, first_token_ms, input_tokens, "
                     "output_tokens, run_id, task, prompt, system, response, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (r.get("started_at") or time.time(), r.get("purpose"), r.get("model"), r.get("served_by"), r.get("status"),
                      r.get("latency_ms"), r.get("first_token_ms"), r.get("input_tokens"), r.get("output_tokens"),
                      r.get("run_id"), r.get("task"), r.get("prompt"), r.get("system"), r.get("response"), r.get("error")))

    def log_api(self, method, path, status, latency_ms, request=None, response=None):
        cur = self.execute("INSERT INTO api_calls (ts, method, path, status, latency_ms, request, response) VALUES (?,?,?,?,?,?,?)",
                           (time.time(), method, path, status, latency_ms, request, response))
        return cur.lastrowid
