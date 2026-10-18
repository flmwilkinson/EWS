"""Real-time EWS API (documented at /docs).

    POST /api/v1/score                         score a customer now, optionally with what-if feature values
    POST /api/v1/events                        ingest an event: apply, re-score, re-trigger and re-tier immediately
    GET  /api/v1/customers/{id}/decision       the current decisions for all six business uses
    GET  /api/v1/health                        service status and the models being served
"""
from __future__ import annotations

import json
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ews_lib import actions, runtime
from server.events import broker

router = APIRouter(prefix="/api/v1", tags=["Real-time EWS API"])
SVC = {}

EVENT_TYPES = Literal["card_spend", "cash_advance", "overdraft_day", "salary_missing", "salary_paid", "returned_dd", "gambling",
                      "bnpl", "credit_search", "bureau_alert", "note", "mortgage_maturity", "business_takings", "payment_due"]


def _log(request: Request, status: int, t0: float, body=None, resp=None):
    ms = round((time.perf_counter() - t0) * 1000, 1)
    rid = SVC["db"].log_api(request.method, request.url.path, status, ms,
                            json.dumps(body, default=str)[:4000] if body is not None else None,
                            json.dumps(resp, default=str)[:8000] if resp is not None else None)
    broker.publish("platform", dict(type="api_call", id=rid, method=request.method, path=request.url.path, status=status, latency_ms=ms,
                                    ts=time.time()))


def _live():
    live = SVC.get("live")
    if live is None or live.sim is None:
        raise HTTPException(503, "The real-time service has no published models yet. Run the pipeline (task 11 publishes).")
    return live


class ScoreRequest(BaseModel):
    customer_id: str | None = Field(None, description="Score this customer as they stand now", examples=["C900001"])
    features: dict[str, Any] | None = Field(None, description="Optional feature values to override (a what-if)",
                                            examples=[{"util": 0.97, "salary_missing_3m": 1}])


@router.post("/score", summary="Score a customer in real time")
def score(req: ScoreRequest, request: Request):
    t0 = time.perf_counter()
    try:
        out = _live().score(req.customer_id, req.features)
    except KeyError:
        _log(request, 404, t0, req.model_dump())
        raise HTTPException(404, f"Unknown customer {req.customer_id}")
    _log(request, 200, t0, req.model_dump(), out)
    return out


class EventRequest(BaseModel):
    customer_id: str = Field(..., examples=["C900006"])
    event_type: EVENT_TYPES = Field(..., examples=["bureau_alert"])
    params: dict[str, Any] = Field(default_factory=dict, examples=[{"worst_status": 2, "score_drop": 60,
                                                                    "note": "Second missed payment reported by another lender"}])


@router.post("/events", summary="Ingest an event and re-score immediately")
def ingest(req: EventRequest, request: Request):
    t0 = time.perf_counter()
    try:
        out = _live().inject(req.customer_id, req.event_type, req.params)
    except KeyError:
        _log(request, 404, t0, req.model_dump())
        raise HTTPException(404, f"Unknown customer {req.customer_id}")
    out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    _log(request, 200, t0, req.model_dump(), out)
    return out


@router.get("/customers/{cid}/decision", summary="Current decisions for all six business uses")
def decision(cid: str, request: Request):
    t0 = time.perf_counter()
    c = _live().customer(cid)
    if c is None:
        _log(request, 404, t0)
        raise HTTPException(404, f"Unknown customer {cid}")
    s = c["state"]
    out = dict(customer_id=cid, tier=s.get("tier"), tier_name=s.get("tier_name"), pd_6m=s.get("pd_6m"), risk_band=s.get("risk_band"),
               pay_lean=s.get("pay_lean"), decisions={actions.USE_CASE_LABELS[u]: s.get(u) for u in actions.USE_CASES})
    _log(request, 200, t0, None, out)
    return out


@router.get("/health", summary="Service status")
def health():
    live = SVC.get("live")
    st = live.status_dict() if live else dict(status="not_loaded")
    return dict(status=st["status"], live_day=st.get("day"), champion=st.get("champion"), published_at=st.get("published_at"),
                models=runtime.jsonable(live.manifest.get("models")) if live and live.manifest else None)


def recent_calls(limit=30):
    return SVC["db"].query("SELECT id, ts, method, path, status, latency_ms, request, response FROM api_calls ORDER BY id DESC LIMIT ?", (limit,))
