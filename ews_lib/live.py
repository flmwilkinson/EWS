"""Live early warning simulator.

Starts from the latest month-end position of every customer and plays forward
one business day per tick. Each tick generates realistic events (card spend,
cash advances, salary credits or their absence, overdraft use, bureau alerts,
contact centre notes, payment due dates), updates the affected customers'
features, re-scores them with the trained models, re-evaluates the triggers and
moves them through the watchlist.

Most of the book is quiet on any day. Three groups create the signal:
  * scripted personas, whose stories the presenter walks through
  * ~1.2% of customers who start to deteriorate (job loss, income cut,
    gambling, over-commitment, bust-out)
  * ~1.5% who generate one-off noise (a single cash advance, one credit search)
    so the audience sees that one amber signal does not trigger an escalation
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ews_lib import actions, commentary, models, triggers, watchlist
from ews_lib.config import CURE_DAYS_LIVE, TIERS
from ews_lib.features import FEATURE_FAMILY

# ---------------------------------------------------------------------------
# Persona scripts: day -> list of (event_type, params)
# ---------------------------------------------------------------------------
PERSONA_SCRIPTS = {
    "C900001": {  # Priya - redundancy, can't pay
        3: [("card_spend", dict(amount=420, merchant="Supermarkets"))],
        6: [("card_spend", dict(amount=380, merchant="Supermarkets"))],
        8: [("cash_advance", dict(amount=250))],
        10: [("overdraft_day", dict(days=3))],
        13: [("overdraft_day", dict(days=3)), ("credit_search", dict(count=2))],
        15: [("salary_missing", dict(note="Expected salary from employer not received"))],
        17: [("note", dict(category="hardship", text="Customer called: made redundant last month, looking for work. Worried about keeping up with the card and rent. Asked what help is available."))],
        19: [("bnpl", dict(amount=160)), ("overdraft_day", dict(days=4))],
        22: [("returned_dd", dict(count=1))],
        25: [("payment_due", dict(outcome="minimum"))],
    },
    "C900002": {  # Dan - gambling, won't pay risk
        2: [("gambling", dict(amount=45))],
        5: [("gambling", dict(amount=80))],
        8: [("gambling", dict(amount=120)), ("cash_advance", dict(amount=200))],
        11: [("overdraft_day", dict(days=4)), ("gambling", dict(amount=150))],
        14: [("gambling", dict(amount=260))],
        16: [("salary_paid", dict(change=0.0))],
        18: [("gambling", dict(amount=400)), ("cash_advance", dict(amount=400))],
        21: [("returned_dd", dict(count=2))],
        24: [("payment_due", dict(outcome="missed"))],
        27: [("note", dict(category="refusal", text="Outbound call: customer said the card is not a priority and refused to discuss income and expenditure. Asked us to stop calling."))],
    },
    "C900003": {  # Grace - refix shock coming in December
        4: [("mortgage_maturity", dict(new_rate=4.89, old_rate=1.69, payment_rise=0.41))],
        9: [("credit_search", dict(count=1))],
        14: [("credit_search", dict(count=2))],
        19: [("note", dict(category="hardship", text="Customer called about her fixed rate ending in December. Worried the new payment will be hard to manage alongside other bills. Asked about product transfer and term extension."))],
        23: [("card_spend", dict(amount=650, merchant="General retail"))],
    },
    "C900004": {  # Lee - sole trader cafe, DSCR falling
        4: [("business_takings", dict(dscr=1.35))],
        9: [("business_takings", dict(dscr=1.18))],
        12: [("overdraft_day", dict(days=5))],
        16: [("business_takings", dict(dscr=0.92)), ("cash_advance", dict(amount=500))],
        20: [("note", dict(category="hardship", text="Customer said the cafe has been very quiet since the factory nearby closed. Takings down by a third. Worried about the business loan and the card."))],
        24: [("returned_dd", dict(count=1)), ("payment_due", dict(outcome="minimum"))],
    },
    "C900005": {  # Sarah - healthy, growth opportunity
        6: [("salary_paid", dict(change=0.12))],
        12: [("card_spend", dict(amount=900, merchant="Travel"))],
        18: [("payment_due", dict(outcome="full"))],
    },
    "C900006": {  # Mark - bureau shows external arrears first
        5: [("bureau_alert", dict(worst_status=1, score_drop=55, note="Missed payment reported by another lender"))],
        7: [("credit_search", dict(count=2))],
        15: [("bureau_alert", dict(worst_status=1, score_drop=10, note="External balance up 1,800"))],
        20: [("payment_due", dict(outcome="full"))],
    },
}

SHOCKS = ["Job loss", "Income reduction", "Gambling", "Overcommitted", "Bust-out"]
ROUTINE_MERCHANTS = ["Supermarkets", "Fuel", "Restaurants", "Online retail", "Travel", "Utilities", "Pharmacy", "Coffee shops",
                     "Transport", "Entertainment", "Clothing", "DIY and home"]


class LiveSimulator:
    def __init__(self, start_frame: pd.DataFrame, bundles: dict, thresholds: pd.DataFrame,
                 start_date="2026-10-01", seed=11, n_deteriorate=0.012, n_noise=0.015, personas=None, routine_per_day=70):
        self.rng = np.random.default_rng(seed)
        self.b = bundles
        self.thr = thresholds
        self.day = 0
        self.start = pd.Timestamp(start_date)
        self.today = self.start - pd.Timedelta(days=1)  # month-end position before the first tick
        S = start_frame.copy()
        S = S[S.tier < 5].set_index("customer_id")
        self.S = S
        n = len(S)
        self.S["due_day"] = self.rng.integers(1, 29, n)
        self.S["pay_day"] = self.rng.integers(1, 29, n)
        self.S["clean_days"] = 0
        self.S["shock"] = None
        self.S["shock_day"] = 999
        self.S["live_events"] = 0
        self.S["last_event"] = ""
        self.persona_names = (personas.set_index("customer_id")["name"].to_dict() if personas is not None else {})
        for cid, script in PERSONA_SCRIPTS.items():
            if cid in self.S.index:
                for d, evs in script.items():
                    for et, p in evs:
                        if et == "salary_missing":
                            self.S.loc[cid, "pay_day"] = d
                        if et == "payment_due":
                            self.S.loc[cid, "due_day"] = d
        pool = self.S.index.difference(list(PERSONA_SCRIPTS))
        w = self.S.loc[pool, "pd_6m"].clip(0.002, 0.3).values
        k = int(n * n_deteriorate)
        det = self.rng.choice(pool, k, replace=False, p=w / w.sum())
        self.S.loc[det, "shock"] = self.rng.choice(SHOCKS, k, p=[0.3, 0.25, 0.15, 0.22, 0.08])
        self.S.loc[det, "shock_day"] = self.rng.integers(1, 18, k)
        rest = pool.difference(det)
        self.noise = set(self.rng.choice(rest, int(n * n_noise), replace=False))
        self.noise_day = {c: int(self.rng.integers(1, 30)) for c in self.noise}
        self.history = []
        self.routine_per_day = routine_per_day
        self.alert_seq = 0

    # ------------------------------------------------------------------
    def _emit(self, events, cid, etype, detail, source):
        events.append(dict(event_ts=self.today + pd.Timedelta(hours=int(self.rng.integers(7, 21)),
                                                               minutes=int(self.rng.integers(0, 60))),
                           sim_day=self.day, customer_id=cid, event_type=etype, detail=detail, source=source,
                           persona=self.persona_names.get(cid), routine=False))

    def _apply(self, cid, etype, p, events):
        S = self.S
        r = S.loc[cid]
        limit = max(float(r.get("credit_limit", 3000) or 3000), 1)
        inc = max(float(r.get("gross_income_annual", 30000) or 30000) * 0.74 / 12, 500)
        if etype == "card_spend":
            S.loc[cid, "util"] = min(r.util + p["amount"] / limit, 1.5)
            S.loc[cid, "util_chg_3m"] = r.util_chg_3m + p["amount"] / limit if pd.notna(r.util_chg_3m) else p["amount"] / limit
            S.loc[cid, "headroom_gbp"] = max(r.headroom_gbp - p["amount"], 0)
            self._emit(events, cid, "Card transaction", f"£{p['amount']:,.0f} at {p.get('merchant', 'retail')}", "Cards")
        elif etype == "cash_advance":
            S.loc[cid, "util"] = min(r.util + p["amount"] / limit, 1.5)
            S.loc[cid, "util_chg_3m"] = (r.util_chg_3m if pd.notna(r.util_chg_3m) else 0) + p["amount"] / limit
            S.loc[cid, "cash_adv_months_3m"] = min((r.cash_adv_months_3m or 0) + 1, 3)
            S.loc[cid, "cash_adv_to_limit_3m"] = (r.cash_adv_to_limit_3m or 0) + p["amount"] / limit
            S.loc[cid, "headroom_gbp"] = max(r.headroom_gbp - p["amount"], 0)
            self._emit(events, cid, "Cash advance", f"£{p['amount']:,.0f} ATM withdrawal on card", "Cards")
        elif etype == "overdraft_day":
            S.loc[cid, "od_days"] = min((r.od_days or 0) + p.get("days", 1), 31)
            S.loc[cid, "od_peak_util"] = min((r.od_peak_util or 0) + 0.15, 1.0)
            S.loc[cid, "od_days_chg_3m"] = (r.od_days_chg_3m or 0) + p.get("days", 1)
            self._emit(events, cid, "Overdraft", f"Overdrawn {p.get('days', 1)} more day(s) this month", "Current account")
        elif etype == "salary_missing":
            S.loc[cid, "salary_missing_3m"] = min((r.salary_missing_3m or 0) + 1, 3)
            S.loc[cid, "income_chg_vs_6m"] = -0.85
            S.loc[cid, "income_drop_x_od"] = 0.85 * (S.loc[cid, "od_days"] or 0)
            S.loc[cid, "dsr"] = min((r.dsr or 0.3) * 3, 3)
            self._emit(events, cid, "Salary not received", p.get("note", "Expected salary credit missing"), "Current account")
        elif etype == "salary_paid":
            S.loc[cid, "income_chg_vs_6m"] = p.get("change", 0.0)
            self._emit(events, cid, "Salary received", f"Salary credited ({p.get('change', 0):+.0%} vs usual)", "Current account")
        elif etype == "returned_dd":
            S.loc[cid, "returned_dd_3m"] = (r.returned_dd_3m or 0) + p.get("count", 1)
            self._emit(events, cid, "Returned direct debit", f"{p.get('count', 1)} direct debit(s) returned unpaid", "Current account")
        elif etype == "gambling":
            S.loc[cid, "gambling_share"] = (r.gambling_share or 0) + p["amount"] / inc
            S.loc[cid, "gambling_chg_3m"] = (r.gambling_chg_3m or 0) + p["amount"] / inc
            self._emit(events, cid, "Gambling transaction", f"£{p['amount']:,.0f} to online gambling operator", "Current account")
        elif etype == "bnpl":
            S.loc[cid, "bnpl_share"] = (r.bnpl_share or 0) + p["amount"] / inc
            self._emit(events, cid, "BNPL repayment", f"£{p['amount']:,.0f} to buy now pay later provider", "Current account")
        elif etype == "credit_search":
            S.loc[cid, "searches_3m"] = (r.searches_3m or 0) + p.get("count", 1)
            S.loc[cid, "util_x_searches"] = S.loc[cid, "util"] * S.loc[cid, "searches_3m"]
            self._emit(events, cid, "Credit search", f"{p.get('count', 1)} hard search(es) by other lenders", "Credit bureau")
        elif etype == "bureau_alert":
            S.loc[cid, "ext_worst_6m"] = max(r.ext_worst_6m or 0, p.get("worst_status", 1))
            S.loc[cid, "ext_arrears_now"] = 1.0
            S.loc[cid, "bureau_score"] = (r.bureau_score or 700) - p.get("score_drop", 40)
            S.loc[cid, "bureau_score_chg_3m"] = (r.bureau_score_chg_3m or 0) - p.get("score_drop", 40)
            self._emit(events, cid, "Bureau alert", p.get("note", "External arrears reported"), "Credit bureau")
        elif etype == "note":
            col = {"hardship": "hardship_notes_3m", "refusal": "refusal_notes_3m", "vulnerability": "vulnerability_notes_3m"}[p["category"]]
            S.loc[cid, col] = (r[col] or 0) + 1
            S.loc[cid, "sentiment_3m"] = min((r.sentiment_3m or 0) - 0.5, -0.4)
            self._emit(events, cid, "Contact centre note", p["text"], "Contact centre")
        elif etype == "mortgage_maturity":
            S.loc[cid, "refix_shock_fwd"] = p["payment_rise"]
            S.loc[cid, "rate_sensitive"] = 1.0
            S.loc[cid, "dsr"] = (r.dsr or 0.3) * (1 + p["payment_rise"] * 0.6)
            S.loc[cid, "dsr_x_rate_sensitive"] = S.loc[cid, "dsr"] * (1 + p["payment_rise"])
            self._emit(events, cid, "Mortgage maturity", f"Fixed rate {p['old_rate']}% ends in December; reversion {p['new_rate']}%, payment up {p['payment_rise']:.0%}", "Mortgages")
        elif etype == "business_takings":
            S.loc[cid, "dscr_chg_3m"] = p["dscr"] - (r.dscr if pd.notna(r.dscr) else 1.8)
            S.loc[cid, "dscr"] = p["dscr"]
            self._emit(events, cid, "Business account", f"Weekly takings down; DSCR now {p['dscr']:.2f}", "Business banking")
        elif etype == "payment_due":
            out = p["outcome"]
            if out == "missed":
                S.loc[cid, "cycles_now"] = (r.cycles_now or 0) + 1
                S.loc[cid, "missed_6m"] = (r.missed_6m or 0) + 1
                S.loc[cid, "pay_ratio"] = 0.0
                S.loc[cid, "pay_ratio_chg_3m"] = -(r.pay_ratio or 0.2)
                self._emit(events, cid, "Payment missed", "Card minimum payment not received by due date", "Cards")
            elif out == "minimum":
                S.loc[cid, "min_pay_streak"] = (r.min_pay_streak or 0) + 1
                S.loc[cid, "pay_ratio_chg_3m"] = 0.03 - (r.pay_ratio or 0.2)
                S.loc[cid, "pay_ratio"] = 0.03
                self._emit(events, cid, "Minimum payment", "Only the minimum payment made", "Cards")
            else:
                S.loc[cid, "pay_ratio"] = 1.0
                S.loc[cid, "min_pay_streak"] = 0
                S.loc[cid, "util"] = max(r.util - 0.3, 0)
                self._emit(events, cid, "Payment received", "Statement balance paid in full", "Cards")

    # ------------------------------------------------------------------
    def _background(self, events):
        rng = self.rng
        S = self.S
        dom = self.today.day
        # Deteriorating customers
        live = S[(S.shock.notna()) & (S.shock_day <= self.day)]
        for cid, r in live.iterrows():
            age = self.day - r.shock_day
            intensity = min(0.25 + 0.05 * age, 0.8)
            sh = r.shock
            if sh in ("Job loss", "Income reduction") and r.pay_day == dom:
                if sh == "Job loss" or rng.random() < 0.5:
                    self._apply(cid, "salary_missing", {}, events)
                else:
                    self._apply(cid, "salary_paid", dict(change=-0.35), events)
            if sh == "Gambling" and rng.random() < intensity:
                self._apply(cid, "gambling", dict(amount=float(rng.integers(50, 600))), events)
            if sh == "Bust-out" and age <= 3 and rng.random() < 0.7:
                self._apply(cid, "card_spend", dict(amount=float(r.headroom_gbp or 500) * 0.4, merchant="Electronics"), events)
            if rng.random() < intensity * 0.35:
                self._apply(cid, "overdraft_day", dict(days=int(rng.integers(1, 4))), events)
            if rng.random() < intensity * 0.15:
                self._apply(cid, "cash_advance", dict(amount=float(rng.integers(5, 40)) * 10), events)
            if rng.random() < intensity * 0.12:
                self._apply(cid, "credit_search", dict(count=1), events)
            if rng.random() < intensity * 0.08:
                self._apply(cid, "returned_dd", dict(count=1), events)
            if sh in ("Job loss", "Income reduction") and rng.random() < intensity * 0.06:
                self._apply(cid, "note", dict(category="hardship", text=rng.choice(commentary_notes["hardship"])), events)
            if sh in ("Bust-out", "Gambling") and rng.random() < intensity * 0.03:
                self._apply(cid, "note", dict(category="refusal", text=rng.choice(commentary_notes["refusal"])), events)
            if rng.random() < intensity * 0.04:
                self._apply(cid, "bureau_alert", dict(worst_status=1, score_drop=int(rng.integers(30, 70))), events)
            if r.due_day == dom:
                p_miss = {"Bust-out": 0.9, "Job loss": 0.45, "Gambling": 0.5, "Income reduction": 0.3, "Overcommitted": 0.35}[sh]
                out = "missed" if rng.random() < p_miss else "minimum"
                self._apply(cid, "payment_due", dict(outcome=out), events)
        # One-off noise
        for cid in [c for c, d in self.noise_day.items() if d == self.day]:
            et = rng.choice(["cash_advance", "credit_search", "overdraft_day", "card_spend"])
            p = {"cash_advance": dict(amount=float(rng.integers(5, 30)) * 10), "credit_search": dict(count=1),
                 "overdraft_day": dict(days=2), "card_spend": dict(amount=float(rng.integers(200, 1200)), merchant="General retail")}[et]
            self._apply(cid, et, p, events)
        # Healthy customers paying on their due date (shown as a sample to keep the feed realistic)
        due = S[(S.due_day == dom) & S.shock.isna() & (S.tier == 0)]
        for cid in due.sample(min(len(due), 4), random_state=self.day).index:
            if cid not in PERSONA_SCRIPTS:
                self._apply(cid, "payment_due", dict(outcome="full"), events)
        self._routine(events)

    def _routine(self, events):
        """Everyday traffic from customers with nothing wrong: small card spends and salary credits.
        Applied and re-scored like any other event; almost always 'no change'."""
        if not self.routine_per_day:
            return
        rng, S, dom = self.rng, self.S, self.today.day
        calm = S[(S.tier == 0) & S.shock.isna() & ~S.index.isin(list(PERSONA_SCRIPTS))]
        n = int(rng.poisson(self.routine_per_day))
        for cid in rng.choice(calm.index, min(n, len(calm)), replace=False):
            amt = float(np.round(np.exp(rng.normal(3.4, 0.8)), 2))
            self._apply(cid, "card_spend", dict(amount=amt, merchant=str(rng.choice(ROUTINE_MERCHANTS))), events)
            events[-1]["routine"] = True
        paid = calm[calm.pay_day == dom]
        for cid in paid.sample(min(len(paid), 12), random_state=self.day).index:
            cur = paid.at[cid, "income_chg_vs_6m"]
            self._apply(cid, "salary_paid", dict(change=float(cur) if pd.notna(cur) else 0.0), events)
            events[-1]["routine"] = True

    # ------------------------------------------------------------------
    def tick(self):
        self.day += 1
        self.today = self.start + pd.Timedelta(days=self.day - 1)
        events = []
        for cid, script in PERSONA_SCRIPTS.items():
            if cid in self.S.index:
                for et, p in script.get(self.day, []):
                    self._apply(cid, et, p, events)
        self._background(events)
        ev = pd.DataFrame(events)
        touched = sorted(set(ev.customer_id)) if len(ev) else []
        alerts = []
        if touched:
            alerts = self._rescore(touched)
        # Quiet de-escalation for clean customers
        self.S.loc[~self.S.index.isin(touched), "clean_days"] += 1
        kpi = self._kpis(len(ev), len(alerts))
        persona_rows = self._persona_snapshot()
        return dict(events=ev, alerts=pd.DataFrame(alerts), kpis=pd.DataFrame([kpi]), personas=persona_rows)

    def _rescore(self, ids, always=()):
        S = self.S
        sub = S.loc[ids].reset_index()
        before = sub.set_index("customer_id")[["tier", "pd_6m", "fired_triggers"]].copy()
        sub["pd_prev_month"] = sub.pd_6m / sub.pd_ratio_3m.replace(0, np.nan)
        sub["pd_6m"] = models.predict(self.b["pd_6m"], sub)
        sub["risk_band"] = sub.pd_6m.apply(models.band)
        sub["p_arrears_3m"] = models.predict(self.b["arrears_3m"], sub)
        sub["p_forbearance_6m"] = models.predict(self.b["forbearance_6m"], sub)
        sub["propensity_to_pay"] = np.where(sub.cycles_now.fillna(0) > 0, models.predict(self.b["cure_2m"], sub), np.nan)
        sub["p_cant_pay"] = models.predict(self.b["cant_pay"], sub)
        sub["pd_ratio_3m"] = (sub.pd_6m / sub.pd_prev_month).fillna(1.0)
        fired, summ = triggers.evaluate(sub, self.thr, key_cols=["customer_id"])
        alerts = []
        recs = summ.to_dict("records")
        for r in recs:
            cid = r["customer_id"]
            prev_tier = int(before.loc[cid, "tier"])
            rt, reason = watchlist.raw_tier(r)
            clean = int(S.loc[cid, "clean_days"])
            if rt >= prev_tier:
                tier, clean, move = rt, 0, ("Escalated" if rt > prev_tier else "Unchanged")
            elif prev_tier >= 4 and rt < 4:
                tier, clean, move = max(rt, 2), 0, "Cured from arrears"
            elif clean >= CURE_DAYS_LIVE:
                tier, clean, move = prev_tier - 1, 0, "De-escalated"
            else:
                tier, move = prev_tier, "Held (cure period)"
            r.update(tier=tier, tier_name=TIERS[tier], tier_reason=reason, movement=move)
            dec = actions.decide(r)
            r.update(dec)
            r["pay_lean"] = actions.pay_lean(r.get("p_cant_pay"), r)
            for k, v in r.items():
                if k in S.columns:
                    S.at[cid, k] = v
            S.at[cid, "clean_days"] = clean
            S.at[cid, "live_events"] = int(S.at[cid, "live_events"]) + 1
            new_trig = set(str(r["fired_triggers"]).split(",")) - set(str(before.loc[cid, "fired_triggers"]).split(",")) - {""}
            new_red = [t for t in new_trig if t in set(fired[(fired.customer_id == cid) & (fired.severity == "Red")].trigger_id)]
            if tier != prev_tier or (new_red and tier <= 3) or (cid in PERSONA_SCRIPTS and new_trig) or cid in always:
                names = fired[fired.customer_id == cid].trigger_name.tolist()
                try:
                    one = summ[summ.customer_id == cid]
                    drv = models.top_drivers(models.contributions(self.b["pd_6m"], one), one, k=3)[0]
                    r["top_drivers"] = "; ".join(f"{d['feature']} ({FEATURE_FAMILY.get(d['feature'], '')})" for d in drv)
                except Exception:
                    r["top_drivers"] = None
                facts = commentary.customer_facts(r, names)
                text, _ = commentary.customer_summary(facts, llm=None)
                self.alert_seq += 1
                alerts.append(dict(alert_id=f"AL{self.alert_seq:05d}", alert_ts=self.today + pd.Timedelta(hours=21), sim_day=self.day,
                                   customer_id=cid, persona=self.persona_names.get(cid),
                                   alert_type=("Tier change" if tier != prev_tier else ("New red signal" if new_red else
                                               ("Re-scored" if cid in always else "Persona update"))),
                                   tier_before=TIERS[prev_tier], tier_after=TIERS[tier], movement=move,
                                   pd_before=float(before.loc[cid, "pd_6m"]), pd_after=float(r["pd_6m"]),
                                   risk_band=r["risk_band"], new_triggers=",".join(sorted(new_trig)),
                                   fired_triggers=r["fired_triggers"], pay_lean=r["pay_lean"],
                                   recommended_action=commentary._recommended(dec), top_drivers=r.get("top_drivers"),
                                   summary=text))
        return alerts

    def inject(self, cid, etype, params):
        """Apply one event now (from the real-time events API) and re-score the customer immediately."""
        if cid not in self.S.index:
            raise KeyError(cid)
        events = []
        self._apply(cid, etype, params, events)
        for e in events:
            e["event_ts"] = pd.Timestamp.now().replace(year=self.today.year, month=self.today.month, day=self.today.day)
            e["injected"] = True
        alerts = self._rescore([cid], always={cid})
        return pd.DataFrame(events), pd.DataFrame(alerts)

    def _kpis(self, n_events, n_alerts):
        S = self.S
        tiers = S.tier.value_counts().reindex(range(6), fill_value=0)
        bal = (S.util.clip(0, 1.5) * S.get("credit_limit", 3000)).fillna(0)
        return dict(sim_day=self.day, sim_date=self.today.date(), customers=len(S), events_today=n_events,
                    alerts_today=n_alerts, **{f"tier_{k}_{TIERS[k].lower().replace(' ', '_')}": int(v) for k, v in tiers.items()},
                    watchlist_total=int((S.tier.between(1, 3)).sum()),
                    balance_at_risk_gbp=float(bal[S.tier >= 2].sum()),
                    avg_pd_6m=float(S.pd_6m.mean()))

    def _persona_snapshot(self):
        cols = ["tier", "tier_name", "pd_6m", "risk_band", "util", "dsr", "dscr", "gambling_share", "searches_3m",
                "salary_missing_3m", "bureau_score", "p_arrears_3m", "p_cant_pay", "fired_triggers"]
        ids = [c for c in PERSONA_SCRIPTS if c in self.S.index]
        snap = self.S.loc[ids, [c for c in cols if c in self.S.columns]].reset_index()
        snap["persona"] = snap.customer_id.map(self.persona_names)
        snap["sim_day"] = self.day
        snap["sim_date"] = self.today.date()
        return snap

    def persona_cards(self):
        """Latest position, decisions and story for each persona (one row each)."""
        from ews_lib.generator import PERSONAS
        keep = ["tier", "tier_name", "tier_reason", "movement", "pd_6m", "risk_band", "p_arrears_3m", "p_forbearance_6m",
                "propensity_to_pay", "p_cant_pay", "pay_lean", "fired_triggers", "util", "dsr", "dscr", "gambling_share",
                "searches_3m", "bureau_score", "salary_missing_3m", "refix_shock_fwd", "segment", "region",
                "limit_and_activation", "pricing", "exposure", "offers_engagement", "support", "collections"]
        ids = [c for c in PERSONA_SCRIPTS if c in self.S.index]
        card = self.S.loc[ids, [c for c in keep if c in self.S.columns]].reset_index()
        card["persona"] = card.customer_id.map(lambda c: PERSONAS[c]["name"])
        card["story"] = card.customer_id.map(lambda c: PERSONAS[c]["story"])
        card["sim_day"] = self.day
        card["sim_date"] = self.today.date()
        return card

    def watchlist_snapshot(self, bundle=None):
        keep = ["tier", "tier_name", "tier_reason", "movement", "pd_6m", "risk_band", "p_arrears_3m",
                "p_forbearance_6m", "propensity_to_pay", "p_cant_pay", "pay_lean", "fired_triggers", "n_amber", "n_red",
                "breadth", "util", "dsr", "segment", "region", "live_events", "limit_and_activation", "pricing",
                "exposure", "offers_engagement", "support", "collections"]
        w = self.S[self.S.tier >= 1]
        w = w[[c for c in keep if c in w.columns]].reset_index()
        w["persona"] = w.customer_id.map(self.persona_names)
        w["as_of_day"] = self.day
        return w


commentary_notes = {
    "hardship": [
        "Customer called worried about upcoming payments; hours have been cut and bills have gone up.",
        "Inbound chat: customer lost their job and asked what options are available for the card.",
        "Customer said they are struggling with rent and food costs and can't manage the full payment.",
    ],
    "refusal": [
        "Customer refused to discuss the account and said they will pay when they are ready.",
        "Customer disputes the balance and says they won't pay until it is resolved. No affordability issue mentioned.",
    ],
}
