"""Job runner: executes the pipeline DAG, one kernel subprocess per task.

A run is a set of tasks (all of them, one task, or a task and everything
downstream). Independent tasks run in parallel (MAX_PARALLEL_TASKS). If a task
fails, everything downstream of it is skipped. Every kernel event is folded
into the in-memory run state, broadcast to the browser on the "runs" channel
and persisted to SQLite when the task ends.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time
import uuid

from server.config import JOB_NAME, MAX_PARALLEL_TASKS, PIPELINE_DIR, ROOT, TASK, TASKS, downstream
from server.events import broker

MARK = "@@EWS@@ "
MAX_STREAM_CHARS = 30_000


def _new_cell(c):
    return dict(index=c["index"], type=c["type"], title=c.get("title"), start_line=c.get("start_line"),
                status="pending" if c["type"] == "code" else None, duration=None, outputs=[])


def apply_event(task: dict, ev: dict):
    """Fold one kernel event into a task's state. Mirrored in web/js/runstate.js."""
    e = ev["event"]
    if e == "notebook":
        task["cells"] = [_new_cell(c) for c in ev["cells"]]
        return
    cells = task.get("cells") or []
    cell = cells[ev["cell"]] if ev.get("cell") is not None and ev["cell"] < len(cells) else None
    if e == "cell_start" and cell:
        cell["status"], cell["started_at"] = "running", ev["ts"]
    elif e == "cell_end" and cell:
        cell["status"], cell["duration"] = ev["status"], ev["duration"]
        if ev.get("error"):
            cell["outputs"].append(dict(kind="error", **ev["error"]))
            task["error"] = f"{ev['error']['ename']}: {ev['error']['evalue']}"[:500]
            for c in cells[cell["index"] + 1:]:
                if c["type"] == "code":
                    c["status"] = "skipped"
    elif e == "stream" and cell:
        outs = cell["outputs"]
        if outs and outs[-1]["kind"] == "stream" and outs[-1]["name"] == ev["name"]:
            if len(outs[-1]["text"]) < MAX_STREAM_CHARS:
                outs[-1]["text"] += ev["text"]
        else:
            outs.append(dict(kind="stream", name=ev["name"], text=ev["text"]))
    elif e == "display" and cell:
        cell["outputs"].append(dict(kind=ev["kind"], title=ev.get("title"), data=ev["data"]))
    elif e == "progress" and cell:
        prog = next((o for o in cell["outputs"] if o["kind"] == "progress" and o["label"] == ev["label"]), None)
        if prog:
            prog.update(done=ev["done"], total=ev["total"])
        else:
            cell["outputs"].append(dict(kind="progress", label=ev["label"], done=ev["done"], total=ev["total"]))
    elif e == "table_written":
        task["rows_written"] = task.get("rows_written", 0) + ev["rows"]
        task["tables_written"] = task.get("tables_written", 0) + 1
        task.setdefault("tables", []).append(dict(table=ev["table"], version=ev["version"], rows=ev["rows"]))
        if cell:
            cell["outputs"].append(dict(kind="table_written", table=ev["table"], version=ev["version"], rows=ev["rows"]))
    elif e == "table_read":
        task.setdefault("reads", [])
        if not any(r["table"] == ev["table"] for r in task["reads"]):
            task["reads"].append(dict(table=ev["table"], version=ev["version"], rows=ev["rows"]))
    elif e == "llm_call":
        task["llm_calls"] = task.get("llm_calls", 0) + 1
        if cell:
            cell["outputs"].append(dict(kind="llm", purpose=ev.get("purpose"), model=ev.get("served_by") or ev.get("model"),
                                        status=ev.get("status"), latency_ms=ev.get("latency_ms"),
                                        output_tokens=ev.get("output_tokens"), input_tokens=ev.get("input_tokens")))
    elif e == "model_registered" and cell:
        cell["outputs"].append(dict(kind="model", name=ev["name"], version=ev["version"], stage=ev["stage"]))


def _summary(task):
    return {k: task.get(k) for k in ("key", "status", "started_at", "finished_at", "duration", "rows_written",
                                     "tables_written", "llm_calls", "error", "code_hash")}


class JobRunner:
    def __init__(self, db, on_published=None):
        self.db = db
        self.on_published = on_published
        self.active: dict[str, dict] = {}
        self.procs: dict[tuple, subprocess.Popen] = {}
        self.lock = threading.Lock()

    # -- public ---------------------------------------------------------------------
    def busy(self):
        return any(r["status"] == "RUNNING" for r in self.active.values())

    def start(self, scope: str = "all", task: str | None = None, trigger="Manual", triggered_by="presenter"):
        if self.busy():
            raise RuntimeError("A run is already in progress")
        if scope == "all":
            keys = [t["key"] for t in TASKS]
        elif scope == "only":
            keys = [task]
        elif scope == "from":
            keys = [task] + downstream(task)
        else:
            raise ValueError(scope)
        run_id = dt.datetime.now().strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        run = dict(run_id=run_id, job=JOB_NAME, trigger=trigger, triggered_by=triggered_by,
                   scope=scope if scope == "all" else f"{scope}:{task}", tasks=keys, status="RUNNING",
                   started_at=time.time(), finished_at=None, duration=None, n_tasks=len(keys), n_ok=0, n_failed=0,
                   n_skipped=0, rows_written=0, llm_calls=0,
                   task_state={k: dict(key=k, status="pending", cells=None, rows_written=0, tables_written=0, llm_calls=0)
                               for k in keys})
        self.db.insert_run(run)
        self.active[run_id] = run
        self._broadcast_run(run)
        for k in keys:
            self._broadcast_task(run, run["task_state"][k])
        threading.Thread(target=self._execute, args=(run,), daemon=True, name=f"run-{run_id}").start()
        return run_id

    def cancel(self, run_id):
        run = self.active.get(run_id)
        if not run or run["status"] != "RUNNING":
            return False
        run["cancel"] = True
        for (rid, _), p in list(self.procs.items()):
            if rid == run_id:
                p.kill()
        return True

    def run_state(self, run_id):
        run = self.active.get(run_id)
        if run:
            return self._public(run)
        row = self.db.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
        if not row:
            return None
        row["tasks"] = json.loads(row["tasks"])
        tasks = self.db.query("SELECT * FROM task_runs WHERE run_id=?", (run_id,))
        row["task_state"] = {t["task_key"]: dict(key=t["task_key"], **{k: t[k] for k in ("status", "started_at", "finished_at",
                             "duration", "rows_written", "tables_written", "llm_calls", "error")}) for t in tasks}
        return row

    def task_state(self, run_id, key):
        run = self.active.get(run_id)
        if run:
            return run["task_state"].get(key)
        t = self.db.one("SELECT * FROM task_runs WHERE run_id=? AND task_key=?", (run_id, key))
        if not t:
            return None
        t["key"] = t.pop("task_key")
        t["cells"] = json.loads(t["cells"]) if t.get("cells") else None
        lin = self.db.query("SELECT table_name, direction, version, rows FROM lineage WHERE run_id=? AND task_key=?", (run_id, key))
        t["tables"] = [dict(table=r["table_name"], version=r["version"], rows=r["rows"]) for r in lin if r["direction"] == "write"]
        t["reads"] = [dict(table=r["table_name"], version=r["version"], rows=r["rows"]) for r in lin if r["direction"] == "read"]
        return t

    # -- execution ------------------------------------------------------------------------
    def _execute(self, run):
        state = run["task_state"]
        keys = run["tasks"]
        threads: dict[str, threading.Thread] = {}
        while True:
            if run.get("cancel"):
                for k in keys:
                    if state[k]["status"] == "pending":
                        self._finish_task(run, k, "cancelled")
            for k in keys:
                t = state[k]
                if t["status"] != "pending":
                    continue
                deps = [d for d in TASK[k]["deps"] if d in keys]
                if any(state[d]["status"] in ("failed", "skipped", "cancelled") for d in deps):
                    self._finish_task(run, k, "skipped", error="Upstream task did not succeed")
                    continue
                running = sum(1 for x in keys if state[x]["status"] == "running")
                if all(state[d]["status"] == "succeeded" for d in deps) and running < MAX_PARALLEL_TASKS:
                    t["status"] = "running"
                    t["started_at"] = time.time()
                    self.db.update_task(run["run_id"], k, status="running", started_at=t["started_at"])
                    self._broadcast_task(run, t)
                    th = threading.Thread(target=self._run_task, args=(run, k), daemon=True)
                    threads[k] = th
                    th.start()
            if all(state[k]["status"] not in ("pending", "running") for k in keys):
                break
            time.sleep(0.15)
        statuses = [state[k]["status"] for k in keys]
        run["status"] = ("SUCCEEDED" if all(s == "succeeded" for s in statuses) else
                         "CANCELLED" if run.get("cancel") else "FAILED")
        run["finished_at"] = time.time()
        run["duration"] = round(run["finished_at"] - run["started_at"], 1)
        if run["n_ok"]:
            try:  # the state of the system after this run, for comparing runs later
                from server.run_compare import snapshot
                self.db.update_run(run["run_id"], summary=json.dumps(snapshot(), default=str))
            except Exception as e:
                print("run summary failed:", e)
        self.db.update_run(run["run_id"], status=run["status"], finished_at=run["finished_at"], duration=run["duration"],
                           n_ok=run["n_ok"], n_failed=run["n_failed"], n_skipped=run["n_skipped"],
                           rows_written=run["rows_written"], llm_calls=run["llm_calls"])
        self._broadcast_run(run)
        self.active.pop(run["run_id"], None)

    def _run_task(self, run, key):
        t = run["task_state"][key]
        path = PIPELINE_DIR / TASK[key]["notebook"]
        try:  # record exactly which code ran, so runs can be compared
            t["code_hash"] = self.db.save_code(TASK[key]["notebook"], path.read_text(encoding="utf-8"))
            self.db.update_task(run["run_id"], key, code_hash=t["code_hash"])
        except Exception:
            pass
        env = dict(os.environ, EWS_RUN_ID=run["run_id"], EWS_TASK=key, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        ok_end = False
        try:
            p = subprocess.Popen([sys.executable, "-u", "-m", "server.kernel", str(path)], cwd=str(ROOT), env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                 errors="replace", creationflags=flags)
        except Exception as e:
            self._finish_task(run, key, "failed", error=f"Could not start kernel: {e}")
            return
        self.procs[(run["run_id"], key)] = p
        last_db = time.time()
        for line in p.stdout:
            if line.startswith(MARK):
                try:
                    ev = json.loads(line[len(MARK):])
                except json.JSONDecodeError:
                    continue
            else:  # output written directly to the file descriptor (C extensions)
                cur = next((c["index"] for c in (t.get("cells") or []) if c["status"] == "running"), None)
                ev = dict(event="stream", name="stdout", text=line, cell=cur, ts=time.time())
            self._on_event(run, key, t, ev)
            if ev["event"] == "notebook_end" and ev.get("status") == "ok":
                ok_end = True
            if time.time() - last_db > 3:  # keep the persisted copy fresh for long tasks
                self.db.update_task(run["run_id"], key, cells=t.get("cells"), rows_written=t.get("rows_written", 0))
                last_db = time.time()
        rc = p.wait()
        self.procs.pop((run["run_id"], key), None)
        if run.get("cancel"):
            self._finish_task(run, key, "cancelled", error="Cancelled by user")
        elif rc == 0 and ok_end:
            self._finish_task(run, key, "succeeded")
        else:
            self._finish_task(run, key, "failed", error=t.get("error") or f"Kernel exited with code {rc}")

    def _on_event(self, run, key, t, ev):
        e = ev["event"]
        if e == "llm_call":
            self.db.log_llm(ev)
            run["llm_calls"] += 1
            ev = {k: v for k, v in ev.items() if k not in ("prompt", "system", "response")}
        if e in ("table_written", "table_read"):
            self.db.execute("INSERT INTO lineage (run_id, task_key, table_name, direction, version, rows, ts) VALUES (?,?,?,?,?,?,?)",
                            (run["run_id"], key, ev["table"], "write" if e == "table_written" else "read", ev["version"], ev["rows"], ev["ts"]))
            if e == "table_written":
                run["rows_written"] += ev["rows"]
                broker.publish("platform", dict(type="table_written", table=ev["table"], version=ev["version"], rows=ev["rows"],
                                                run_id=run["run_id"], task=key))
        if e == "published" and self.on_published:
            threading.Thread(target=self.on_published, daemon=True).start()
        if e == "model_registered":
            broker.publish("platform", dict(type="model_registered", **{k: ev.get(k) for k in ("name", "version", "stage", "approach")}))
        apply_event(t, ev)
        if e != "table_read":
            broker.publish("runs", dict(type="cell", run_id=run["run_id"], task_key=key, ev=ev))

    def _finish_task(self, run, key, status, error=None):
        t = run["task_state"][key]
        t["status"] = status
        t["finished_at"] = time.time()
        if t.get("started_at"):
            t["duration"] = round(t["finished_at"] - t["started_at"], 2)
        if error and not t.get("error"):
            t["error"] = error
        if status in ("cancelled", "failed") and t.get("cells"):
            for c in t["cells"]:
                if c["status"] in ("pending", "running"):
                    c["status"] = "skipped"
        if status == "succeeded":
            run["n_ok"] += 1
        elif status == "failed":
            run["n_failed"] += 1
        elif status in ("skipped", "cancelled"):
            run["n_skipped"] += 1
        self.db.update_task(run["run_id"], key, status=status, started_at=t.get("started_at"), finished_at=t["finished_at"],
                            duration=t.get("duration"), rows_written=t.get("rows_written", 0),
                            tables_written=t.get("tables_written", 0), llm_calls=t.get("llm_calls", 0),
                            error=t.get("error"), cells=t.get("cells"))
        self.db.update_run(run["run_id"], n_ok=run["n_ok"], n_failed=run["n_failed"], n_skipped=run["n_skipped"],
                           rows_written=run["rows_written"], llm_calls=run["llm_calls"])
        self._broadcast_task(run, t)
        self._broadcast_run(run)

    # -- broadcasting ------------------------------------------------------------------------
    def _public(self, run):
        out = {k: v for k, v in run.items() if k not in ("task_state", "cancel")}
        out["task_state"] = {k: _summary(v) for k, v in run["task_state"].items()}
        return out

    def _broadcast_run(self, run):
        broker.publish("runs", dict(type="run", run=self._public(run)))

    def _broadcast_task(self, run, t):
        broker.publish("runs", dict(type="task", run_id=run["run_id"], task=_summary(t)))
