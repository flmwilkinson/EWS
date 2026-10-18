"""Platform API: workspace (notebooks and library), jobs and runs, catalog and lineage, SQL, connectors, admin."""
from __future__ import annotations

import json
import random
import re
import shutil
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ews_lib import connectors as CN, llm, official_data, registry, runtime
from ews_lib.config import DATA_DIR, LANDING_DIR
from ews_lib.lakehouse import lh
from server import data as D
from server.config import NOTEBOOK_TASK, ROOT, TASK, TASKS, WORKSPACE_FOLDERS, JOB_NAME
from server.notebook import parse, serialise

router = APIRouter(prefix="/api")
SVC = {}  # filled by app.py: db, jobs, live


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------
def _safe(path: str):
    p = (ROOT / path).resolve()
    if not any(str(p).startswith(str((ROOT / f).resolve())) for f in WORKSPACE_FOLDERS) or p.suffix != ".py":
        raise HTTPException(400, "Only .py files in the pipeline and ews_lib folders can be opened")
    return p


def _last_task_runs():
    rows = SVC["db"].query("SELECT t.task_key, t.status, t.duration, r.run_id, r.started_at FROM task_runs t JOIN runs r USING(run_id) "
                           "WHERE t.status NOT IN ('pending') ORDER BY r.started_at")
    out = {}
    for r in rows:
        out[r["task_key"]] = r
    return out


@router.get("/workspace/tree")
def tree():
    last = _last_task_runs()
    folders = []
    for f in WORKSPACE_FOLDERS:
        files = []
        for p in sorted((ROOT / f).glob("*.py")):
            if p.name == "__init__.py":
                continue
            text = p.read_text(encoding="utf-8")
            task = NOTEBOOK_TASK.get(p.name) if f == "pipeline" else None
            files.append(dict(path=f"{f}/{p.name}", name=p.name, kind="notebook" if "# %%" in text else "module",
                              lines=text.count("\n") + 1, modified=p.stat().st_mtime, task=task,
                              title=TASK[task]["title"] if task else None,
                              last_status=last.get(task, {}).get("status") if task else None))
        folders.append(dict(name=f, files=files))
    return dict(folders=folders)


@router.get("/workspace/file")
def get_file(path: str):
    p = _safe(path)
    text = p.read_text(encoding="utf-8")
    task = NOTEBOOK_TASK.get(p.name) if path.startswith("pipeline/") else None
    revs = SVC["db"].query("SELECT COUNT(*) AS n FROM revisions WHERE path=?", (path,))[0]["n"]
    return dict(path=path, name=p.name, content=text, kind="notebook" if "# %%" in text else "module",
                cells=parse(text) if "# %%" in text else None, task=task, task_meta=TASK.get(task), revisions=revs,
                modified=p.stat().st_mtime)


class SaveBody(BaseModel):
    path: str
    content: str | None = None
    cells: list[dict] | None = None
    note: str | None = None


@router.put("/workspace/file")
def save_file(body: SaveBody):
    p = _safe(body.path)
    old = p.read_text(encoding="utf-8")
    new = serialise(body.cells) if body.cells is not None else body.content
    if new is None:
        raise HTTPException(400, "Nothing to save")
    try:
        compile(new, str(p), "exec")
    except SyntaxError as e:
        raise HTTPException(422, f"Syntax error on line {e.lineno}: {e.msg}")
    if new != old:
        SVC["db"].execute("INSERT INTO revisions (path, saved_at, content, note) VALUES (?,?,?,?)",
                          (body.path, time.time(), old, body.note or "Saved from the workspace"))
        p.write_text(new, encoding="utf-8")
    return get_file(body.path)


@router.get("/workspace/revisions")
def revisions(path: str):
    return SVC["db"].query("SELECT id, path, saved_at, note, length(content) AS size FROM revisions WHERE path=? ORDER BY id DESC", (path,))


@router.get("/workspace/revision/{rid}")
def revision(rid: int):
    r = SVC["db"].one("SELECT * FROM revisions WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404)
    return r


@router.post("/workspace/revert/{rid}")
def revert(rid: int):
    r = revision(rid)
    return save_file(SaveBody(path=r["path"], content=r["content"], note=f"Reverted to revision {rid}"))


# ---------------------------------------------------------------------------
# Jobs and runs
# ---------------------------------------------------------------------------
@router.get("/jobs/pipeline")
def pipeline():
    return dict(name=JOB_NAME, tasks=TASKS, schedule="Monthly, 2nd business day 06:00 · on demand",
                cluster="Python " + __import__("sys").version.split()[0] + " runtime")


class RunBody(BaseModel):
    scope: str = "all"   # all | only | from
    task: str | None = None


@router.post("/runs")
def start_run(body: RunBody):
    if body.scope != "all" and body.task not in TASK:
        raise HTTPException(400, "Unknown task")
    try:
        run_id = SVC["jobs"].start(scope=body.scope, task=body.task)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return dict(run_id=run_id)


@router.get("/runs")
def list_runs(limit: int = 40):
    rows = SVC["db"].query("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,))
    ids = [r["run_id"] for r in rows]
    tasks = SVC["db"].query(f"SELECT run_id, task_key, status, duration, rows_written FROM task_runs WHERE run_id IN ({','.join('?' * len(ids))})",
                            ids) if ids else []
    by = {}
    for t in tasks:
        by.setdefault(t["run_id"], {})[t["task_key"]] = dict(status=t["status"], duration=t["duration"], rows_written=t["rows_written"])
    for r in rows:
        r["tasks"] = json.loads(r["tasks"])
        live = SVC["jobs"].active.get(r["run_id"])
        r["task_state"] = ({k: dict(status=v["status"], duration=v.get("duration"), rows_written=v.get("rows_written"))
                            for k, v in live["task_state"].items()} if live else by.get(r["run_id"], {}))
        if live:
            r.update(status=live["status"], n_ok=live["n_ok"], n_failed=live["n_failed"], rows_written=live["rows_written"])
    return rows


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    r = SVC["jobs"].run_state(run_id)
    if not r:
        raise HTTPException(404)
    return r


@router.get("/runs/{run_id}/tasks/{key}")
def get_task(run_id: str, key: str):
    t = SVC["jobs"].task_state(run_id, key)
    if t is None:
        raise HTTPException(404)
    return {k: runtime.jsonable(v) if k != "cells" else v for k, v in t.items()}


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str):
    return dict(cancelled=SVC["jobs"].cancel(run_id))


@router.get("/tasks/{key}/latest")
def latest_task(key: str):
    """The most recent run of a task (what the notebook view shows when it opens)."""
    for r in SVC["jobs"].active.values():
        if key in r["tasks"]:
            return dict(run_id=r["run_id"], task=get_task(r["run_id"], key))
    row = SVC["db"].one("SELECT t.run_id FROM task_runs t JOIN runs r USING(run_id) WHERE t.task_key=? AND t.status NOT IN ('pending') "
                        "ORDER BY r.started_at DESC LIMIT 1", (key,))
    if not row:
        return dict(run_id=None, task=None)
    return dict(run_id=row["run_id"], task=get_task(row["run_id"], key))


# ---------------------------------------------------------------------------
# Catalog and lineage
# ---------------------------------------------------------------------------
REFERENCE_TABLES = {"bronze.generator_truth", "bronze.personas"}  # validation reference data, not part of the catalogue view


@router.get("/catalog")
def catalog():
    tables = [t for t in lh.tables() if t["full_name"] not in REFERENCE_TABLES]
    schemas = {}
    for t in tables:
        schemas.setdefault(t["schema"], []).append(t)
    desc = dict(bronze="Raw source data as landed, with lineage columns", silver="Conformed and quality-checked",
                gold="Features, scores, watchlist, decisions and model outputs", ops="Data quality, thresholds and monitoring")
    return dict(schemas=[dict(name=s, description=desc[s], tables=schemas.get(s, [])) for s in ["bronze", "silver", "gold", "ops"]],
                models=len(registry.list_models()), total_rows=sum(t["rows"] for t in tables), total_bytes=sum(t["bytes"] for t in tables))


@router.get("/catalog/table")
def table_detail(name: str, limit: int = 100):
    if not lh.exists(name):
        raise HTTPException(404, name)
    hist = lh.history(name)
    sample = D.sql(f"SELECT * FROM {name} LIMIT {int(limit)}")
    db = SVC["db"]
    writers = db.query("SELECT DISTINCT task_key FROM lineage WHERE table_name=? AND direction='write'", (name,))
    readers = db.query("SELECT DISTINCT task_key FROM lineage WHERE table_name=? AND direction='read'", (name,))
    upstream = []
    for w in writers:
        last = db.one("SELECT run_id FROM lineage WHERE table_name=? AND direction='write' AND task_key=? ORDER BY ts DESC LIMIT 1",
                      (name, w["task_key"]))
        ins = db.query("SELECT DISTINCT table_name FROM lineage WHERE run_id=? AND task_key=? AND direction='read'",
                       (last["run_id"], w["task_key"]))
        upstream.append(dict(task=w["task_key"], title=TASK.get(w["task_key"], {}).get("title"), inputs=[i["table_name"] for i in ins]))
    downstream = []
    for r in readers:
        last = db.one("SELECT run_id FROM lineage WHERE table_name=? AND direction='read' AND task_key=? ORDER BY ts DESC LIMIT 1",
                      (name, r["task_key"]))
        outs = db.query("SELECT DISTINCT table_name FROM lineage WHERE run_id=? AND task_key=? AND direction='write'",
                        (last["run_id"], r["task_key"]))
        downstream.append(dict(task=r["task_key"], title=TASK.get(r["task_key"], {}).get("title"), outputs=[o["table_name"] for o in outs]))
    conn = CN.connector_for_table(name.split(".", 1)[1]) if name.startswith("bronze.") else None
    return dict(name=name, history=hist, columns=hist[0]["columns"], description=hist[0].get("description"),
                sample=D.records(sample), upstream=upstream, downstream=downstream,
                connector=dict(id=conn["id"], name=conn["name"], protocol=conn["protocol"]) if conn else None)


class SqlBody(BaseModel):
    query: str
    limit: int = 1000


FORBIDDEN = re.compile(r"\b(copy|attach|detach|install|load|export|import|pragma|create|drop|alter|insert|update|delete|call|set)\b", re.I)


@router.post("/sql")
def run_sql(body: SqlBody):
    q = body.query.strip().rstrip(";")
    if not re.match(r"^\s*(select|with|describe|show|summarize|from)\b", q, re.I) or FORBIDDEN.search(re.sub(r"'[^']*'", "", q)):
        raise HTTPException(400, "Read-only: SELECT, WITH, DESCRIBE, SHOW and SUMMARIZE queries only")
    t0 = time.perf_counter()
    try:
        df = D.sql(f"SELECT * FROM ({q}) AS q LIMIT {int(min(body.limit, 5000))}" if q.lower().startswith(("select", "with", "from")) else q)
    except Exception as e:
        raise HTTPException(400, str(e).split("\n")[0][:500])
    return dict(columns=[dict(name=c, type=str(t)) for c, t in df.dtypes.items()], rows=D.records(df),
                elapsed_ms=round((time.perf_counter() - t0) * 1000, 1), row_count=len(df))


# ---------------------------------------------------------------------------
# Connectors
# ---------------------------------------------------------------------------
@router.get("/connectors")
def connectors():
    live = SVC.get("live")
    snap = live.snapshot(limit_events=0, limit_alerts=0) if live is not None and live.sim is not None else None
    inbound = []
    for c in CN.INBOUND:
        batch = []
        for t in c["tables"]:
            h = lh.history(f"bronze.{t}") if lh.exists(f"bronze.{t}") else []
            batch.append(dict(table=t, rows=h[0]["rows"] if h else None, loaded_at=h[0]["ts"] if h else None,
                              version=h[0]["version"] if h else None))
        inbound.append(dict(c, batch=batch, live=snap["inbound"].get(c["id"]) if snap else None,
                            status="Connected" if any(b["rows"] for b in batch) else "Awaiting first load"))
    outbound = [dict(c, live=snap["outbound"].get(c["id"]) if snap else None, status="Connected") for c in CN.OUTBOUND]
    calls = SVC["db"].one("SELECT COUNT(*) AS n, AVG(latency_ms) AS avg_ms, SUM(output_tokens) AS out_tok, SUM(input_tokens) AS in_tok, "
                          "MAX(ts) AS last_ts FROM llm_calls WHERE status='ok'")
    ai = dict(CN.AI, **llm.status(), calls=calls, status="Connected" if llm.configured() else "No API key")
    return dict(inbound=inbound, outbound=outbound, ai=ai, live_status=live.status_dict() if live else None)


@router.post("/connectors/{cid}/test")
def test_connector(cid: str):
    if cid == "claude":
        if not llm.configured():
            return dict(ok=False, message="ANTHROPIC_API_KEY is not set in .env")
        text, source, meta = llm.generate("Reply with exactly: Connection OK", fallback="", purpose="connector_test", max_tokens=200)
        return dict(ok=source == "llm", latency_ms=meta.get("latency_ms"), model=meta.get("served_by") or meta.get("model"),
                    message=text or meta.get("error"))
    c = CN.by_id().get(cid)
    if not c:
        raise HTTPException(404)
    t0 = time.perf_counter()
    if c in CN.INBOUND:  # the landed extracts are present and readable, and reached bronze
        from ews_lib.generator import LANDING_FORMATS
        files = [LANDING_DIR / LANDING_FORMATS[t][1] for t in c["tables"]]
        missing = [f.name for f in files if not f.exists()]
        rows = sum(lh.history(f"bronze.{t}")[0]["rows"] for t in c["tables"] if lh.exists(f"bronze.{t}"))
        ok = not missing and rows > 0
        msg = (f"Landing check OK · {len(files)} extract(s), {sum(f.stat().st_size for f in files) / 1e6:.1f} MB · {rows:,} rows in bronze"
               if ok else f"Missing: {', '.join(missing) or 'bronze tables'}")
    else:  # the outbox the downstream system consumes is writable
        box = DATA_DIR / "outbox"
        box.mkdir(parents=True, exist_ok=True)
        f = box / f"{cid}.jsonl"
        f.touch()
        n = sum(1 for _ in open(f, encoding="utf-8"))
        ok, msg = True, f"Outbox writable · {n:,} messages delivered to {f.name}"
    return dict(ok=ok, latency_ms=round((time.perf_counter() - t0) * 1000, 1), message=msg, endpoint=c["endpoint"])


@router.get("/connectors/{cid}/sample")
def connector_sample(cid: str):
    c = CN.by_id().get(cid)
    if not c:
        raise HTTPException(404)
    live = SVC.get("live")
    if live is not None and live.sim is not None:
        snap = live.snapshot(limit_events=0, limit_alerts=0)
        st = snap["inbound"].get(cid) or snap["outbound"].get(cid)
        last = st and (st.get("last_event") or st.get("last_payload"))
        if last:
            return dict(source="live", payload=last)
    return dict(source="example", payload=c.get("sample"))


# ---------------------------------------------------------------------------
# Official macroeconomic sources (real public APIs)
# ---------------------------------------------------------------------------
@router.get("/official")
def official():
    cache = official_data.cached()
    scenario = {}
    mac = D.table("silver.macro_regional_monthly")
    if mac is not None and len(mac):
        last = mac[mac.month == mac.month.max()]
        scenario = dict(month=str(last.month.max())[:7], unemployment={r.region: round(float(r.unemployment_rate), 2) for r in last.itertuples()},
                        bank_rate=float(last.bank_rate_pct.iloc[0]), cpi=float(last.cpi_yoy_pct.mean()),
                        hpi={r.region: round(float(r.hpi_yoy_pct), 2) for r in last.itertuples()})
    bronze = lh.history("bronze.macro_official_series")[0] if lh.exists("bronze.macro_official_series") else None
    return dict(sources=[dict(src, result=cache.get(src["id"])) for src in official_data.SOURCES], scenario=scenario,
                bronze=dict(rows=bronze["rows"], ts=bronze["ts"], version=bronze["version"]) if bronze else None)


@router.post("/official/{sid}/fetch")
def official_fetch(sid: str):
    if sid not in official_data.BY_ID:
        raise HTTPException(404)
    return official_data.fetch(sid)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
class ResetBody(BaseModel):
    clear_history: bool = False


@router.post("/admin/reset")
def reset(body: ResetBody):
    """Wipe generated data so the next pipeline run starts from nothing (keeps notebooks and revisions)."""
    if SVC["jobs"].busy():
        raise HTTPException(409, "A run is in progress")
    lh.drop_all()
    registry.clear()
    if LANDING_DIR.exists():
        shutil.rmtree(LANDING_DIR)
    D._cache.clear()
    if body.clear_history:
        for t in ("runs", "task_runs", "lineage", "llm_calls", "api_calls"):
            SVC["db"].execute(f"DELETE FROM {t}")
    SVC["live"].load()
    return dict(ok=True)


@router.get("/admin/summary")
def summary():
    db = SVC["db"]
    last = db.one("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1")
    return dict(last_run=last, tables=len(lh.tables()), models=len(registry.list_models()), llm=llm.status(),
                serving=registry.serving_manifest(), data_dir=str(DATA_DIR))
