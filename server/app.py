"""Early Warning Studio: FastAPI application.

    python -m server            then open http://127.0.0.1:8765
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ews_lib import llm, official_data
from server import ai_api, ews_api, platform_api, realtime_api
from server.config import WEB_DIR
from server.db import DB
from server.events import broker
from server.jobs import JobRunner
from server.live_service import LiveService

db = DB()
live = LiveService(db)
jobs = JobRunner(db, on_published=live.load)
llm.on_call = db.log_llm  # calls made by the server itself (dashboards, live alerts) land in the audit log too
for mod in (platform_api, ews_api, ai_api, realtime_api):
    mod.SVC.update(db=db, jobs=jobs, live=live)


@asynccontextmanager
async def lifespan(app):
    broker.bind(asyncio.get_running_loop())
    threading.Thread(target=official_data.fetch_all, daemon=True, name="official-warmup").start()
    await asyncio.get_running_loop().run_in_executor(None, live.load)
    yield
    live.shutdown()


app = FastAPI(title="Early Warning Studio API", version="1.0",
              description="Real-time early warning system for a retail card book. "
                          "The **Real-time EWS API** section is what a decision engine, CRM or collections platform would call.",
              lifespan=lifespan)
for r in (platform_api.router, ews_api.router, ai_api.router, realtime_api.router):
    app.include_router(r)


@app.middleware("http")
async def api_headers(request: Request, call_next):
    """Standard headers on the real-time API: a request id for tracing and the model version that answered."""
    if not request.url.path.startswith("/api/v1"):
        return await call_next(request)
    t0 = time.perf_counter()
    rid = request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex[:16]}"
    response = await call_next(request)
    response.headers["X-Request-Id"] = rid
    response.headers["X-Response-Time-Ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
    if live.manifest:
        m = live.manifest["models"]["pd_6m"]
        response.headers["X-Model-Version"] = f"{m['name']}/v{m['version']}"
    response.headers["X-API-Version"] = "2026-10-01"
    return response


@app.get("/api/stream/{channel}", include_in_schema=False)
async def stream(channel: str):
    if channel not in ("runs", "live", "platform"):
        raise HTTPException(404)
    return StreamingResponse(broker.stream(channel), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# -- live controls -------------------------------------------------------------------------------
class SpeedBody(BaseModel):
    seconds_per_day: float | None = None  # only the speed action uses it


@app.get("/api/live/state", include_in_schema=False)
def live_state():
    return live.snapshot()


@app.post("/api/live/{action}", include_in_schema=False)
def live_action(action: str, body: SpeedBody | None = None):
    if action == "start":
        live.start()
    elif action == "pause":
        live.pause()
    elif action == "reset":
        live.reset()
    elif action == "speed" and body and body.seconds_per_day:
        live.set_speed(body.seconds_per_day)
    else:
        raise HTTPException(400)
    return live.status_dict()


@app.get("/api/apis/calls", include_in_schema=False)
def api_calls(limit: int = 30):
    return realtime_api.recent_calls(limit)


@app.get("/api/health", include_in_schema=False)
def health():
    return dict(ok=True, llm=llm.status(), live=live.status_dict(), running=jobs.busy())


# -- front end ---------------------------------------------------------------------------------------
for sub in ("vendor", "css", "js", "img"):
    if (WEB_DIR / sub).exists():
        app.mount(f"/{sub}", StaticFiles(directory=WEB_DIR / sub), name=sub)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})
