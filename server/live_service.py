"""Real-time EWS service.

Loads what the publish task put in the serving manifest (champion models,
thresholds, month-end state) and plays October 2026 forward one business day
at a time. Each simulated day is computed by the live simulator, then its
events are released over `day_seconds` of wall-clock time in timestamp order,
each alert straight after the event that caused it, so the browser sees a
continuous stream. Tier changes are pushed to the outbound connectors
(decision engine, CRM, collections, marketing).

The same service backs the real-time API: score a customer, or inject an event
that is applied, re-scored and re-tiered immediately.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from ews_lib import actions, commentary, connectors, llm, models, registry, runtime, scoring, triggers, watchlist
from ews_lib.config import DATA_DIR, TIERS
from ews_lib.features import FEATURE_FAMILY
from ews_lib.lakehouse import lh
from ews_lib.live import LiveSimulator
from server.events import broker

FAMILIES = {"pd_6m": "pd_6m", "arrears_3m": "p_arrears_3m", "forbearance_6m": "p_forbearance_6m",
            "cure_2m": "propensity_to_pay", "cant_pay": "cant_pay_vs_wont_pay"}
LIVE_DAYS = 31
TIER_KEYS = {k: f"tier_{k}_{TIERS[k].lower().replace(' ', '_')}" for k in TIERS}
TIER_NUM = {v: k for k, v in TIERS.items()}


def _clean(d: dict) -> dict:
    return {k: runtime.jsonable(v) for k, v in d.items()}


class LiveService:
    def __init__(self, db):
        self.db = db
        self.lock = threading.RLock()
        self.sim: LiveSimulator | None = None
        self._frame = self._frame_idx = None  # snapshot of the live state for readers, refreshed after each day
        self.bundles = self.thr = self.manifest = None
        self.status = "not_loaded"
        self.message = "Run the pipeline to publish models to the real-time service."
        self.day_seconds = 8.0
        self._gen = 0  # bumped on every (re)load, so a day in flight from the old state stops at once
        self.ai_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="live-ai")
        self._reset_stats()
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="live-loop")
        self.thread.start()

    # -- state ----------------------------------------------------------------------
    def _reset_stats(self):
        self.events = collections.deque(maxlen=6000)
        self.alerts = collections.deque(maxlen=1500)
        self.kpi_history = []
        self.persona_history = []
        self.kpis = {}
        self.clock = None
        self.ai = {}
        self.ev_seq = 0
        self.alerts_total = 0
        self.inbound = {c["id"]: dict(events=0, today=0, per_day=[], last_ts=None, last_event=None) for c in connectors.INBOUND}
        self.outbound = {c["id"]: dict(sent=0, today=0, per_day=[], last_ts=None, last_payload=None) for c in connectors.OUTBOUND}

    def load(self):
        """(Re)load from the serving manifest. Called at start-up and whenever the publish task runs."""
        self._gen += 1
        manifest = registry.serving_manifest()
        if not manifest:
            with self.lock:
                self.status, self.sim = "not_loaded", None
                self.bundles = self.thr = self.manifest = self._state0 = None
                self._frame = self._frame_idx = None
                self.message = "Run the pipeline to publish models to the real-time service."
                self._reset_stats()
            import gc
            gc.collect()
            self._broadcast_status()
            return False
        try:
            bundles = {k: registry.load(m["name"], m["version"]) for k, m in manifest["models"].items()}
            thr = lh.read("ops.kri_thresholds")
            state = lh.read("gold.ews_decisions_latest")
            personas = lh.read("bronze.personas")
        except Exception as e:
            with self.lock:
                self.status, self.message = "error", f"Could not load the serving manifest: {e}"
            self._broadcast_status()
            return False
        with self.lock:
            self.bundles, self.thr, self.manifest = bundles, thr, manifest
            self._state0, self._personas = state, personas
            self.sim = LiveSimulator(state, bundles, thr, personas=personas)
            self._snapshot()
            self._reset_stats()
            self.kpis = self._kpis_now()
            self.clock = (self.sim.start - pd.Timedelta(hours=3)).isoformat()
            self.status = "ready"
            self.message = f"Loaded {manifest['champion_approach']} and {len(thr)} triggers; {len(state):,} customers at {manifest['state']['as_at']}."
        broker.publish("live", dict(type="reset"))
        self._broadcast_status()
        return True

    def reset(self):
        was = self.status
        with self.lock:
            self.status = "resetting"
        self.load()
        return was

    def start(self):
        with self.lock:
            if self.status in ("ready", "paused"):
                self.status = "running"
        self._broadcast_status()

    def pause(self):
        with self.lock:
            if self.status == "running":
                self.status = "paused"
        self._broadcast_status()

    def set_speed(self, seconds_per_day: float):
        self.day_seconds = float(max(2.0, min(seconds_per_day, 60.0)))
        self._broadcast_status()

    def status_dict(self):
        return dict(status=self.status, message=self.message, day=self.sim.day if self.sim else 0, days=LIVE_DAYS,
                    day_seconds=self.day_seconds, clock=self.clock,
                    champion=self.manifest.get("champion_approach") if self.manifest else None,
                    published_at=self.manifest.get("published_at") if self.manifest else None,
                    run_id=self.manifest.get("run_id") if self.manifest else None)

    def _broadcast_status(self):
        broker.publish("live", dict(type="status", **self.status_dict()))

    # -- the loop -----------------------------------------------------------------------
    def _loop(self):
        while not self._stop.is_set():
            if self.status != "running" or self.sim is None:
                time.sleep(0.2)
                continue
            if self.sim.day >= LIVE_DAYS:
                with self.lock:
                    self.status = "finished"
                    self.message = "Month end reached. Reset to replay October."
                self._broadcast_status()
                continue
            try:
                self._play_day()
            except Exception as e:  # keep the service alive and say what happened
                with self.lock:
                    self.status, self.message = "paused", f"Live loop error: {type(e).__name__}: {e}"
                self._broadcast_status()

    def _wait_until(self, target, t_ref, gen):
        """Sleep until wall-clock t_ref[0] + target, honouring pause (which shifts the reference)."""
        while True:
            if self._stop.is_set() or gen != self._gen or self.status in ("resetting", "not_loaded"):
                return False
            if self.status == "paused":
                time.sleep(0.1)
                t_ref[0] += 0.1
                continue
            remaining = t_ref[0] + target - time.time()
            if remaining <= 0:
                return True
            time.sleep(min(remaining, 0.1))

    def _play_day(self):
        t_compute = time.time()
        gen = self._gen
        with self.lock:
            out = self.sim.tick()
            day, today = self.sim.day, self.sim.today
            self._snapshot()
        ev = out["events"]
        al = out["alerts"]
        duration = max(self.day_seconds - (time.time() - t_compute), 1.0)
        open_, close_ = today + pd.Timedelta(hours=7), today + pd.Timedelta(hours=21)
        span = (close_ - open_).total_seconds()
        items = []
        last_frac = {}
        if len(ev):
            ev = ev.sort_values("event_ts")
            for r in ev.to_dict("records"):
                frac = min(max((pd.Timestamp(r["event_ts"]) - open_).total_seconds() / span, 0), 0.97)
                items.append((frac, 0, "event", r))
                last_frac[r["customer_id"]] = max(last_frac.get(r["customer_id"], 0), frac)
        if len(al):
            for r in al.to_dict("records"):
                frac = min(last_frac.get(r["customer_id"], 0.5) + 0.015, 0.99)
                r["alert_ts"] = (open_ + pd.Timedelta(seconds=frac * span)).isoformat()
                items.append((frac, 1, "alert", r))
        items.sort(key=lambda x: (x[0], x[1]))
        for c in self.inbound.values():
            c["today"] = 0
        for c in self.outbound.values():
            c["today"] = 0
        broker.publish("live", dict(type="day_start", day=day, date=today.date().isoformat()))
        t_ref = [time.time()]
        next_clock = 0.0
        for frac, _, kind, r in items:
            while next_clock < frac:  # the simulated clock keeps moving between events
                if not self._wait_until(next_clock * duration, t_ref, gen):
                    return
                self._tick_clock(open_ + pd.Timedelta(seconds=next_clock * span), day)
                next_clock += 0.04
            if not self._wait_until(frac * duration, t_ref, gen):
                return
            self._tick_clock(open_ + pd.Timedelta(seconds=frac * span), day)
            if kind == "event":
                self._publish_event(r)
            else:
                self._publish_alert(r)
        if not self._wait_until(duration, t_ref, gen):
            return
        self._tick_clock(close_, day)
        k = _clean(out["kpis"].iloc[0].to_dict())
        with self.lock:
            self.kpis = k
            self.kpi_history.append(k)
            for c in self.inbound.values():
                c["per_day"].append(c["today"])
            for c in self.outbound.values():
                c["per_day"].append(c["today"])
            snap = self.sim._persona_snapshot()
            self.persona_history.extend(_clean(x) for x in snap[["sim_day", "persona", "customer_id", "pd_6m", "tier", "tier_name"]].to_dict("records"))
        broker.publish("live", dict(type="day_end", day=day, kpis=k, personas=self.persona_cards()))

    def _tick_clock(self, ts, day):
        self.clock = pd.Timestamp(ts).isoformat()
        broker.publish("live", dict(type="clock", clock=self.clock, day=day))

    def _publish_event(self, r):
        self.ev_seq += 1
        r = _clean(r)
        r["event_id"] = f"EV{self.ev_seq:06d}"
        r["connector"] = connectors.LIVE_SOURCE_TO_CONNECTOR.get(r.get("source"))
        c = self.inbound.get(r["connector"])
        if c is not None:
            c["events"] += 1
            c["today"] += 1
            c["last_ts"] = r["event_ts"]
            c["last_event"] = r
        self.events.append(r)
        broker.publish("live", dict(type="event", event=r))

    def _publish_alert(self, r, auto_ai=True):
        r = _clean(r)
        self.alerts.append(r)
        self.alerts_total += 1
        if r.get("tier_before") != r.get("tier_after"):
            self._kpi_move(r["tier_before"], r["tier_after"])
            self._push_outbound(r)
        broker.publish("live", dict(type="alert", alert=r, kpis=self.kpis))
        if auto_ai and r.get("persona") and llm.configured():
            self.ai_pool.submit(self._ai_alert, r["alert_id"])

    def _kpi_move(self, before, after):
        k = self.kpis
        b, a = TIER_NUM.get(before), TIER_NUM.get(after)
        if b is None or a is None:
            return
        k[TIER_KEYS[b]] = k.get(TIER_KEYS[b], 0) - 1
        k[TIER_KEYS[a]] = k.get(TIER_KEYS[a], 0) + 1
        k["watchlist_total"] = sum(k.get(TIER_KEYS[t], 0) for t in (1, 2, 3))

    def _push_outbound(self, a):
        cid = a["customer_id"]
        row = self.sim.S.loc[cid]
        tier = int(row.tier)
        pushes = [("decision_engine", dict(customer_id=cid, ews_tier=tier, tier_name=a["tier_after"],
                                            limit_strategy=row.get("limit_and_activation"), pricing_strategy=row.get("pricing"),
                                            exposure_strategy=row.get("exposure"), effective_from=a["alert_ts"]))]
        if row.get("support") not in (None, "None"):
            pushes.append(("crm_actions", dict(customer_id=cid, action=row.get("support"), reason=a.get("tier_after"),
                                               priority="High" if tier >= 3 else "Normal")))
        if tier >= 4:
            pushes.append(("collections_out", dict(customer_id=cid, treatment=row.get("collections"), pay_lean=row.get("pay_lean"),
                                                   propensity_to_pay=runtime.jsonable(row.get("propensity_to_pay")))))
        if tier >= 1 and TIER_NUM.get(a["tier_before"], 0) == 0:
            pushes.append(("marketing", dict(customer_id=cid, suppress_credit_offers=True, reason=f"EWS tier {tier}")))
        box = DATA_DIR / "outbox"
        box.mkdir(parents=True, exist_ok=True)
        for cid_, payload in pushes:
            with open(box / f"{cid_}.jsonl", "a", encoding="utf-8") as f:  # the outbox each downstream system consumes
                f.write(json.dumps(dict(_clean(payload), sent_at=a["alert_ts"]), default=str) + "\n")
            o = self.outbound[cid_]
            o["sent"] += 1
            o["today"] += 1
            o["last_ts"] = a["alert_ts"]
            o["last_payload"] = _clean(payload)
            broker.publish("live", dict(type="outbound", connector=cid_, payload=o["last_payload"], ts=a["alert_ts"]))

    # -- AI on alerts ----------------------------------------------------------------------------
    def alert(self, alert_id):
        return next((a for a in reversed(self.alerts) if a.get("alert_id") == alert_id), None)

    def alert_prompt(self, alert_id):
        a = self.alert(alert_id)
        if a is None:
            raise KeyError(alert_id)
        cid = a["customer_id"]
        r = {k: runtime.jsonable(v) for k, v in self._frame_idx.loc[cid].to_dict().items()}
        r["customer_id"] = cid
        r["movement"] = a.get("movement")
        names = a.get("fired_triggers") or ""
        catalogue = dict(zip(self.thr.trigger_id, self.thr["name"]))
        facts = commentary.customer_facts(r, [catalogue.get(t, t) for t in names.split(",") if t])
        facts["top_drivers"] = a.get("top_drivers")
        evs = [dict(day=e["sim_day"], source=e["source"], event=e["event_type"], detail=e["detail"])
               for e in self.events if e["customer_id"] == cid][-12:]
        return commentary.build_customer(facts, evs), a

    def _ai_alert(self, alert_id):
        (prompt, fallback), a = self.alert_prompt(alert_id)
        broker.publish("live", dict(type="ai_start", alert_id=alert_id))
        if not llm.configured():
            broker.publish("live", dict(type="ai_done", alert_id=alert_id, text=fallback, source="template"))
            return
        s = llm.stream(prompt, purpose="live_alert_summary", max_tokens=700)
        try:
            for chunk in s:
                broker.publish("live", dict(type="ai_delta", alert_id=alert_id, delta=chunk))
            text, src = s.text.strip(), "llm"
        except Exception:
            text, src = fallback, "template"
        self.ai[alert_id] = dict(text=text, source=src, model=(s.meta or {}).get("served_by") or llm.model())
        a["ai_summary"] = text
        broker.publish("live", dict(type="ai_done", alert_id=alert_id, text=text, source=src,
                                    model=self.ai[alert_id]["model"], latency_ms=(s.meta or {}).get("latency_ms")))

    # -- reads ---------------------------------------------------------------------------------------
    def _kpis_now(self):
        S = self.sim.S
        tiers = S.tier.value_counts().reindex(range(6), fill_value=0)
        bal = (S.util.clip(0, 1.5) * S.get("credit_limit", 3000)).fillna(0)
        return _clean(dict(sim_day=self.sim.day, customers=len(S), events_today=0, alerts_today=0,
                           **{TIER_KEYS[k]: int(v) for k, v in tiers.items()},
                           watchlist_total=int(S.tier.between(1, 3).sum()),
                           balance_at_risk_gbp=float(bal[S.tier >= 2].sum()), avg_pd_6m=float(S.pd_6m.mean())))

    def persona_cards(self):
        if self.sim is None:
            return []
        with self.lock:
            cards = self.sim.persona_cards()
        return [_clean(x) for x in cards.to_dict("records")]

    def snapshot(self, limit_events=150, limit_alerts=60):
        with self.lock:
            return dict(**self.status_dict(), kpis=self.kpis, kpi_history=list(self.kpi_history),
                        persona_history=list(self.persona_history), personas=self.persona_cards() if self.sim else [],
                        events=list(self.events)[-limit_events:], alerts=list(self.alerts)[-limit_alerts:], alerts_total=self.alerts_total,
                        inbound=self.inbound, outbound=self.outbound, ai=self.ai)

    def _snapshot(self):
        """Copy the simulator state for readers (call with the lock held)."""
        f = self.sim.S.reset_index().copy()
        self._frame, self._frame_idx = f, f.set_index("customer_id", drop=False)

    def frame(self) -> pd.DataFrame | None:
        return self._frame if self.sim is not None else None

    def customer(self, cid):
        idx = self._frame_idx
        if self.sim is None or idx is None or cid not in idx.index:
            return None
        row = {k: runtime.jsonable(v) for k, v in idx.loc[cid].to_dict().items()}
        row["customer_id"] = cid
        return dict(state=row, events=[e for e in self.events if e["customer_id"] == cid],
                    alerts=[a for a in self.alerts if a["customer_id"] == cid])

    # -- real-time API -------------------------------------------------------------------------------
    def score(self, customer_id=None, features=None):
        """Score a customer as they stand now, optionally with feature overrides (a what-if). Never changes state."""
        if self.sim is None:
            raise RuntimeError("The real-time service has no published models yet")
        t0 = time.perf_counter()
        idx = self._frame_idx
        if customer_id:
            if customer_id not in idx.index:
                raise KeyError(customer_id)
            base = idx.loc[[customer_id]].reset_index(drop=True)
        else:
            base = self._state0.iloc[[0]].copy()
            base["customer_id"] = "ADHOC"
        for k, v in (features or {}).items():
            base[k] = v
        prev_pd = float(base.pd_6m.iloc[0]) if "pd_6m" in base else None
        scored = scoring.score_frame(base, self.bundles)
        scored["pd_ratio_3m"] = scored.pd_6m / (base.pd_6m / base.pd_ratio_3m.replace(0, np.nan)).fillna(scored.pd_6m)
        fired, summ = triggers.evaluate(scored, self.thr, key_cols=["customer_id"])
        r = summ.iloc[0].to_dict()
        tier, reason = watchlist.raw_tier(r)
        r.update(tier=tier, tier_name=TIERS[tier], tier_reason=reason)
        decisions = actions.decide(r)
        contrib = models.contributions(self.bundles["pd_6m"], summ)
        drivers = models.top_drivers(contrib, summ, k=5)[0]
        ms = (time.perf_counter() - t0) * 1000
        reg = self.bundles["pd_6m"]["_registry"]
        return _clean(dict(
            customer_id=r["customer_id"], pd_6m=r["pd_6m"], previous_pd_6m=prev_pd, risk_band=r["risk_band"],
            p_arrears_3m=r["p_arrears_3m"], p_forbearance_6m=r["p_forbearance_6m"], propensity_to_pay=r["propensity_to_pay"],
            p_cant_pay=r["p_cant_pay"], pay_lean=actions.pay_lean(r.get("p_cant_pay"), r), tier=tier, tier_name=TIERS[tier],
            tier_reason=reason, breadth=r["breadth"], n_red=r["n_red"], n_amber=r["n_amber"])) | dict(
            triggers=[_clean(x) for x in fired[["trigger_id", "trigger_name", "dimension", "severity", "value", "threshold"]].to_dict("records")],
            decisions=decisions,
            top_drivers=[dict(feature=d["feature"], family=FEATURE_FAMILY.get(d["feature"]), value=runtime.jsonable(d["value"]),
                              contribution=round(d["contribution"], 4)) for d in drivers],
            model=dict(name=reg["name"], version=reg["version"], approach=self.bundles["pd_6m"].get("name")),
            latency_ms=round(ms, 2))

    def inject(self, customer_id, event_type, params):
        if self.sim is None:
            raise RuntimeError("The real-time service has no published models yet")
        with self.lock:
            before = self.sim.S.loc[customer_id, ["tier_name", "pd_6m"]].to_dict()
            ev, al = self.sim.inject(customer_id, event_type, params or {})
            after = self.sim.S.loc[customer_id, ["tier_name", "pd_6m", "fired_triggers"]].to_dict()
            self._snapshot()
        for r in ev.to_dict("records"):
            self._publish_event(r)
        alerts = []
        for r in al.to_dict("records"):
            r["alert_ts"] = (self.clock or dt.datetime.now().isoformat())
            r["injected"] = True
            self._publish_alert(r, auto_ai=False)
            alerts.append(_clean(r))
        if alerts and llm.configured():
            self.ai_pool.submit(self._ai_alert, alerts[0]["alert_id"])
        return dict(customer_id=customer_id, before=_clean(before), after=_clean(after), alerts=alerts)

    def shutdown(self):
        self._stop.set()
