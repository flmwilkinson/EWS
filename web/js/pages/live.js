// Live monitor: the real-time EWS. Events in, re-scoring, alerts with AI summaries, tier changes, actions out.
// Incoming events are buffered and applied five times a second, and each list is its own component, so the page stays
// smooth at the fastest replay speed.
import { api, subscribe, streamGet } from "../api.js";
import { store, toast } from "../store.js";
import { go } from "../router.js";
import * as C from "../components.js";
import * as F from "../format.js";
import { barChart, lineChart, SERIES } from "../charts.js";

const { ref, shallowRef, reactive, computed, markRaw, onMounted, onBeforeUnmount } = Vue;

const INJECT = [
  { label: "Missed payment reported by another lender", type: "bureau_alert", params: { worst_status: 2, score_drop: 60, note: "Missed payment reported by another lender" } },
  { label: "Expected salary not received", type: "salary_missing", params: { note: "Expected salary credit from employer not received" } },
  { label: "Hardship call to the contact centre", type: "note", params: { category: "hardship", text: "Customer called: hours have been cut and they are struggling to pay. Asked what help is available." } },
  { label: "Card payment missed", type: "payment_due", params: { outcome: "missed" } },
  { label: "£400 cash advance", type: "cash_advance", params: { amount: 400 } },
  { label: "£350 to an online gambling operator", type: "gambling", params: { amount: 350 } },
  { label: "Direct debit returned unpaid", type: "returned_dd", params: { count: 1 } },
  { label: "Two hard credit searches", type: "credit_search", params: { count: 2 } },
];
const CONN_LABEL = { cards: "Cards", current_account: "Current account", bureau: "Credit bureau", contact_centre: "Contact centre", lending: "Mortgages", business: "Business banking" };
const OUT_LABEL = { decision_engine: "Decision engine", crm_actions: "CRM next best action", collections_out: "Collections strategy", marketing: "Marketing suppression" };
const OUT_ICON = { decision_engine: "credit-card", crm_actions: "headset", collections_out: "hand-coins", marketing: "megaphone" };

const LiveClock = {
  components: C.all,
  props: { status: String },
  emits: ["act"],
  setup(props, { emit }) {
    const speed = computed({ get: () => store.live?.day_seconds || 8, set: (v) => emit("act", "speed", { seconds_per_day: v }) });
    const progress = computed(() => (store.live?.day || 0) / 31);
    return { store, F, speed, progress };
  },
  template: `<div class="card mb"><div class="card-body" style="padding:14px 18px"><div class="row" style="gap:16px">
    <span class="pill" :class="{ live: status === 'running' }" style="cursor:default"><span class="dot" :class="status === 'running' ? 'bad pulse' : status === 'paused' ? 'warn' : ''"></span>
      {{ status === 'running' ? 'LIVE' : status === 'paused' ? 'PAUSED' : status === 'finished' ? 'MONTH END' : 'READY' }}</span>
    <div style="min-width:0;flex:1"><div class="clock ellipsis">{{ F.simClock(store.live?.clock) }}</div>
      <div class="small muted ellipsis">Intraday event stream · one business day every {{ speed }} s · {{ store.live?.champion }} serving</div></div>
    <div style="width:220px;flex:none"><div class="row small muted" style="margin-bottom:4px"><span>Day {{ store.live?.day || 0 }} of 31</span><span class="spacer"></span><span>{{ F.pct(progress, 0) }}</span></div>
      <PBar :value="progress" color="var(--bad)" /></div>
    <Seg v-model="speed" :options="[{ value: 4, label: 'Fast' }, { value: 8, label: 'Normal' }, { value: 15, label: 'Slow' }]" />
    <button class="btn" @click="$emit('act', 'reset')" title="Back to the September month-end position"><Icon name="rotate-ccw" :size="14" />Reset</button>
    <button v-if="status === 'running'" class="btn" @click="$emit('act', 'pause')"><Icon name="pause" :size="14" />Pause</button>
    <button v-else class="btn primary" @click="$emit('act', 'start')" :disabled="status === 'finished'"><Icon name="play" :size="14" />{{ status === 'paused' ? 'Resume' : 'Start live feed' }}</button>
  </div></div></div>`,
};

const LiveFeed = {
  components: C.all,
  props: { events: Array },
  setup(props) {
    const filter = ref("signals");
    const rows = computed(() => {
      const ev = props.events;
      if (filter.value === "signals") return ev.filter((e) => !e.routine).slice(0, 90);
      if (filter.value === "personas") return ev.filter((e) => e.persona).slice(0, 90);
      return ev.slice(0, 120);
    });
    const routineShare = computed(() => { const n = props.events.length || 1; return props.events.filter((e) => e.routine).length / n; });
    return { filter, rows, routineShare, F };
  },
  template: `<div class="card"><div class="card-head"><Icon name="radio-tower" :size="16" /><h3>Event stream</h3><div class="sub">{{ F.pct(routineShare, 0) }} routine</div>
    <div class="actions"><Seg v-model="filter" :options="[{ value: 'signals', label: 'Signals' }, { value: 'all', label: 'All' }, { value: 'personas', label: 'In focus' }]" /></div></div>
    <div class="card-body flush feed" style="height:560px">
      <div v-if="!rows.length" class="empty" style="padding:60px 10px"><p>Press <b>Start live feed</b>. Events arrive from card authorisations, the current account ledger, bureau alerts and the contact centre.</p></div>
      <div v-for="e in rows" :key="e.event_id" class="feed-row" :class="{ routine: e.routine, persona: e.persona }">
        <span class="t">{{ F.simTime(e.event_ts) }}</span><span><span class="src" :class="F.srcClass(e.source)">{{ e.source }}</span></span>
        <div class="what"><a :href="'#/customer/' + e.customer_id" style="color:inherit"><b>{{ e.persona || e.customer_id }}</b></a>
          <span v-if="e.persona" class="mono tiny muted"> {{ e.customer_id }}</span> · {{ e.event_type }}<span v-if="e.injected" class="chip blue" style="margin-left:6px;height:18px">via API</span>
          <div class="d small">{{ e.detail }}</div></div>
      </div></div></div>`,
};

const LiveAlerts = {
  components: C.all,
  props: { alerts: Array, names: Object },
  setup() {
    function explain(a) {
      a.ai_summary = ""; a.ai_streaming = true;
      streamGet(`/api/ai/stream?kind=alert&id=${a.alert_id}`, (ev) => {
        if (ev.type === "delta") a.ai_summary = ev.replace ? ev.text : a.ai_summary + ev.text;
        else if (ev.type === "done") { a.ai_streaming = false; a.ai_model = ev.model; }
        else if (ev.type === "error") { a.ai_streaming = false; toast(ev.message, "bad"); }
      });
    }
    const inline = (t) => F.md(t).replace(/^<p>|<\/p>\s*$/g, "");
    return { explain, inline, F, go };
  },
  template: `<div class="card"><div class="card-head"><Icon name="bell-ring" :size="16" /><h3>Alerts</h3><div class="sub">Tier changes and new red signals, re-scored in real time</div></div>
    <div class="card-body feed" style="height:560px;padding-top:6px">
      <div v-if="!alerts.length" class="muted small" style="padding:30px 6px;text-align:center">No alerts yet. One amber signal on its own does not escalate a customer: the EWS waits for depth and breadth.</div>
      <div v-for="a in alerts.slice(0, 50)" :key="a.alert_id" class="alert-card" :class="'t' + F.TIER_NUM[a.tier_after]" @click="go('customer/' + a.customer_id)">
        <div class="row" style="gap:6px"><template v-if="a.tier_before !== a.tier_after"><TierBadge :tier="a.tier_before" /><Icon name="arrow-right" :size="13" /></template><TierBadge :tier="a.tier_after" />
          <span class="spacer"></span><span class="mono tiny muted">{{ F.simTime(a.alert_ts) }}</span></div>
        <div class="row" style="margin-top:7px;gap:8px"><b>{{ a.persona || a.customer_id }}</b><span v-if="a.persona" class="mono tiny muted">{{ a.customer_id }}</span>
          <span class="small muted">· {{ a.alert_type }}</span><span class="spacer"></span>
          <span class="small num">PD {{ F.pct(a.pd_before, 1) }} → <b :style="{ color: a.pd_after > a.pd_before ? 'var(--bad-text)' : 'var(--ok-text)' }">{{ F.pct(a.pd_after, 1) }}</b></span></div>
        <div class="row wrap" style="gap:4px;margin-top:6px" v-if="a.new_triggers"><span v-for="t in a.new_triggers.split(',').filter(Boolean)" :key="t" class="chip red">{{ names[t] || t }}</span></div>
        <div class="small" style="margin-top:6px"><b>Action:</b> {{ a.recommended_action }} <span v-if="F.TIER_NUM[a.tier_after] >= 3 && a.pay_lean" class="chip outline" style="margin-left:4px">{{ a.pay_lean }}</span></div>
        <div v-if="a.ai_summary !== undefined && a.ai_summary !== null" class="summary ai" :class="{ caret: a.ai_streaming }"><span class="chip violet" style="height:18px;margin-right:6px"><Icon name="sparkles" :size="11" />AI</span><span v-html="inline(a.ai_summary)"></span></div>
        <template v-else><div class="summary muted clamp3" :title="a.summary">{{ a.summary }}</div><button class="btn xs ai" style="margin-top:6px" @click.stop="explain(a)"><Icon name="sparkles" :size="11" />Explain with AI</button></template>
      </div></div></div>`,
};

const LivePersonas = {
  components: C.all,
  props: { personas: Array, history: Array, changed: Object },
  setup(props) {
    const spark = computed(() => {
      const out = {};
      for (const p of props.history) (out[p.customer_id] ||= []).push(p.pd_6m);
      return out;
    });
    return { spark, F, go };
  },
  template: `<div class="card"><div class="card-head"><Icon name="users" :size="16" /><h3>Customers in focus</h3><div class="sub">Followed in real time</div></div>
    <div class="card-body col" style="gap:8px">
      <div v-for="p in personas" :key="p.customer_id + ':' + (changed[p.customer_id] || 0)" class="persona-card" :class="{ changed: changed[p.customer_id] }" @click="go('customer/' + p.customer_id)">
        <div class="row"><Avatar :name="p.persona" /><div class="grow"><div class="row"><b>{{ p.persona }}</b><span class="spacer"></span><TierBadge :tier="p.tier_name" /></div>
          <div class="tiny muted ellipsis">{{ p.segment }} · {{ p.region }}</div></div></div>
        <div class="row" style="margin-top:6px"><span class="small num">PD <b>{{ F.pct(p.pd_6m, 1) }}</b> · band {{ p.risk_band }}</span><span class="spacer"></span><Spark :data="spark[p.customer_id] || []" :width="96" :height="22" :color="'var(--tier-' + (p.tier || 0) + ')'" /></div>
      </div></div></div>`,
};

export default {
  components: { ...C.all, LiveClock, LiveFeed, LiveAlerts, LivePersonas },
  setup() {
    const s = reactive({ loaded: false, alerts_total: 0, alerts: [], personas: [], kpi_history: [], persona_history: [], inbound: {}, outbound: {}, outLog: [], kpis: null });
    const events = shallowRef([]);
    const names = ref({});
    const inject = reactive({ cid: "C900006", kind: 0, busy: false, result: null });
    const changed = reactive({});
    async function load() {
      const snap = await api.get("/api/live/state");
      events.value = snap.events.reverse().map(markRaw);
      Object.assign(s, { loaded: true, alerts_total: snap.alerts_total || snap.alerts.length, alerts: snap.alerts.reverse(), personas: snap.personas, kpi_history: snap.kpi_history,
        persona_history: snap.persona_history, inbound: snap.inbound, outbound: snap.outbound, kpis: snap.kpis });
      for (const a of s.alerts) if (snap.ai[a.alert_id]) a.ai_summary = snap.ai[a.alert_id].text;
      store.live = { ...store.live, status: snap.status, day: snap.day, clock: snap.clock, day_seconds: snap.day_seconds, champion: snap.champion, message: snap.message, published_at: snap.published_at };
    }
    const queue = [];
    const unsub = subscribe("live", (ev) => { if (ev.type !== "clock" && ev.type !== "status") queue.push(ev); });
    function apply(evs) {
      const fresh = [];
      for (const ev of evs) {
        if (ev.type === "event") { fresh.push(markRaw(ev.event)); const c = s.inbound[ev.event.connector]; if (c) { c.events++; c.today++; } }
        else if (ev.type === "alert") {
          s.alerts.unshift(ev.alert); s.alerts_total++; if (ev.kpis) s.kpis = ev.kpis;
          const p = s.personas.find((x) => x.customer_id === ev.alert.customer_id);
          if (p && ev.alert.tier_after !== p.tier_name) { p.tier_name = ev.alert.tier_after; p.tier = F.TIER_NUM[ev.alert.tier_after]; p.pd_6m = ev.alert.pd_after; changed[p.customer_id] = Date.now(); }
          else if (p) p.pd_6m = ev.alert.pd_after;
        }
        else if (ev.type === "ai_start") { const a = s.alerts.find((x) => x.alert_id === ev.alert_id); if (a) { a.ai_summary = ""; a.ai_streaming = true; } }
        else if (ev.type === "ai_delta") { const a = s.alerts.find((x) => x.alert_id === ev.alert_id); if (a) a.ai_summary = (a.ai_summary || "") + ev.delta; }
        else if (ev.type === "ai_done") { const a = s.alerts.find((x) => x.alert_id === ev.alert_id); if (a) { a.ai_summary = ev.text; a.ai_streaming = false; a.ai_model = ev.model; } }
        else if (ev.type === "day_end") {
          s.kpi_history.push(ev.kpis); s.kpis = ev.kpis; if (ev.personas) mergePersonas(ev.personas);
          ev.personas?.forEach((p) => s.persona_history.push({ sim_day: ev.day, persona: p.persona, customer_id: p.customer_id, pd_6m: p.pd_6m, tier: p.tier }));
          Object.values(s.inbound).forEach((c) => { c.per_day.push(c.today); c.today = 0; }); Object.values(s.outbound).forEach((c) => { c.per_day.push(c.today); c.today = 0; });
        }
        else if (ev.type === "outbound") { s.outLog.unshift({ ...ev, id: Math.random() }); const o = s.outbound[ev.connector]; if (o) { o.sent++; o.today++; } }
        else if (ev.type === "reset") { load(); return; }
      }
      if (fresh.length) events.value = fresh.reverse().concat(events.value).slice(0, 300);
      if (s.alerts.length > 120) s.alerts.length = 120;
      if (s.outLog.length > 40) s.outLog.length = 40;
    }
    const timer = setInterval(() => { if (queue.length) apply(queue.splice(0)); }, 200);
    function mergePersonas(list) { list.forEach((p) => { const i = s.personas.findIndex((x) => x.customer_id === p.customer_id); if (i >= 0) s.personas[i] = { ...s.personas[i], ...p }; }); }
    onMounted(async () => {
      await load();
      try { const t = await api.get("/api/ews/triggers"); names.value = Object.fromEntries(t.triggers.map((x) => [x.trigger_id, x.name])); } catch (e) {}
    });
    onBeforeUnmount(() => { unsub(); clearInterval(timer); });

    const status = computed(() => store.live?.status);
    const act = async (a, body) => { try { await api.post(`/api/live/${a}`, body); } catch (e) { toast(e.message, "bad"); } };
    const k = computed(() => s.kpis || store.liveKpis || {});
    const totalEvents = computed(() => Object.values(s.inbound).reduce((a, c) => a + (c.events || 0), 0));
    const totalSent = computed(() => Object.values(s.outbound).reduce((a, c) => a + (c.sent || 0), 0));
    async function doInject() {
      inject.busy = true; inject.result = null;
      const preset = INJECT[inject.kind];
      try { inject.result = await api.post("/api/v1/events", { customer_id: inject.cid.trim(), event_type: preset.type, params: preset.params }); }
      catch (e) { toast(e.message, "bad"); }
      inject.busy = false;
    }
    const dayLabel = (d) => `${d} Oct`;
    const wlOpt = computed(() => {
      const h = s.kpi_history;
      const x = h.map((r) => dayLabel(r.sim_day));
      const keys = [["tier_1_monitor", "Monitor", 1], ["tier_2_watch", "Watch", 2], ["tier_3_early_intervention", "Early intervention", 3], ["tier_4_collections", "Collections", 4]];
      return lineChart({ x, y: "int", showSymbol: h.length < 20, series: keys.map(([key, name, t]) => ({ name, data: h.map((r) => r[key]), color: F.tierColor(t) })) });
    });
    const days = computed(() => s.kpi_history.length);
    const tpOpt = computed(() => {
      const n = days.value;
      const keys = Object.keys(CONN_LABEL).filter((c) => s.inbound[c]);
      const x = Array.from({ length: n }, (_, i) => dayLabel(i + 1));
      return barChart({ x, y: "int", stacked: true, series: keys.map((c, i) => ({ name: CONN_LABEL[c], data: s.inbound[c].per_day.slice(0, n), color: SERIES.light[i] })) });
    });
    const personaOpts = computed(() => s.personas.map((p) => ({ id: p.customer_id, name: p.persona })));
    return { s, events, store, status, act, k, totalEvents, totalSent, inject, INJECT, doInject, wlOpt, tpOpt, F, names, changed, days,
      OUT_LABEL, OUT_ICON, personaOpts };
  },
  template: `<div>
  <NeedPipeline v-if="s.loaded && status === 'not_loaded'" what="and publish the models the real-time service runs" />
  <template v-else>
  <LiveClock :status="status" @act="act" />
  <div class="kpis mb" style="grid-template-columns:repeat(8,minmax(0,1fr))">
    <Kpi label="On watchlist" :value="F.n(k.watchlist_total)" color="var(--tier-2)" :flash-key="k.watchlist_total" />
    <Kpi label="Monitor" :value="F.n(k.tier_1_monitor)" color="var(--tier-1)" :flash-key="k.tier_1_monitor" />
    <Kpi label="Watch" :value="F.n(k.tier_2_watch)" color="var(--tier-2)" :flash-key="k.tier_2_watch" />
    <Kpi label="Early intervention" :value="F.n(k.tier_3_early_intervention)" color="var(--tier-3)" :flash-key="k.tier_3_early_intervention" />
    <Kpi label="Collections" :value="F.n(k.tier_4_collections)" color="var(--tier-4)" :flash-key="k.tier_4_collections" />
    <Kpi label="Events scored" :value="F.n(totalEvents)" icon="zap" />
    <Kpi label="Alerts" :value="F.n(s.alerts_total)" icon="bell-ring" />
    <Kpi label="Actions sent" :value="F.n(totalSent)" icon="send" />
  </div>
  <div class="grid mb" style="grid-template-columns:minmax(0,1.15fr) minmax(0,1fr) minmax(0,.85fr);align-items:start">
    <LiveFeed :events="events" />
    <LiveAlerts :alerts="s.alerts" :names="names" />
    <div class="col" style="gap:16px">
      <LivePersonas :personas="s.personas" :history="s.persona_history" :changed="changed" />
      <div class="card"><div class="card-head"><Icon name="webhook" :size="16" /><h3>Inject an event</h3><div class="sub">POST /api/v1/events</div></div>
        <div class="card-body col">
          <div><label class="lbl">Customer</label><div class="row"><input class="input grow" v-model="inject.cid" list="personas-list" />
            <datalist id="personas-list"><option v-for="p in personaOpts" :key="p.id" :value="p.id">{{ p.name }}</option></datalist></div></div>
          <div><label class="lbl">Event</label><select class="input" style="width:100%" v-model="inject.kind"><option v-for="(o, i) in INJECT" :key="i" :value="i">{{ o.label }}</option></select></div>
          <button class="btn primary" @click="doInject" :disabled="inject.busy || status === 'not_loaded'"><Icon :name="inject.busy ? 'loader-circle' : 'send'" :size="14" />Send event</button>
          <div v-if="inject.result" class="decision"><div class="l">Re-scored in {{ inject.result.latency_ms }} ms</div>
            <div class="row" style="margin-top:6px"><TierBadge :tier="inject.result.before.tier_name" /><Icon name="arrow-right" :size="13" /><TierBadge :tier="inject.result.after.tier_name" /></div>
            <div class="small" style="margin-top:4px">PD {{ F.pct(inject.result.before.pd_6m, 2) }} → <b>{{ F.pct(inject.result.after.pd_6m, 2) }}</b></div></div>
        </div></div>
    </div>
  </div>
  <div class="grid g3">
    <Card title="Watchlist through October" sub="Customers by tier at the end of each business day"><EChart v-if="days" :option="wlOpt" :height="250" /><div v-else class="muted small" style="height:250px;display:grid;place-items:center">Builds as days complete</div></Card>
    <Card title="Inbound events by connector" sub="Per business day"><EChart v-if="days" :option="tpOpt" :height="250" /><div v-else class="muted small" style="height:250px;display:grid;place-items:center">Builds as days complete</div></Card>
    <Card title="Actions sent downstream" sub="Every tier change updates the systems that act on it">
      <div class="grid g2" style="gap:8px;margin-bottom:10px"><div v-for="(o, key) in s.outbound" :key="key" class="decision"><div class="l"><Icon :name="OUT_ICON[key]" :size="13" />{{ OUT_LABEL[key] }}</div><div class="v strong">{{ F.n(o.sent) }}</div></div></div>
      <div class="feed" style="max-height:120px"><div v-for="o in s.outLog" :key="o.id" class="small" style="padding:4px 0;border-bottom:1px solid var(--border)">
        <span class="mono tiny muted">{{ F.simTime(o.ts) }}</span> → <b>{{ OUT_LABEL[o.connector] }}</b> · {{ o.payload.customer_id }} · <span class="muted">{{ o.payload.limit_strategy || o.payload.action || o.payload.treatment || (o.payload.suppress_credit_offers ? 'suppress credit offers' : '') }}</span></div></div>
    </Card>
  </div>
  </template>
</div>`,
};
