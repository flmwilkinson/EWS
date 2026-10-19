// Customer 360: everything the EWS knows about one customer, why, and what happens next
import { api, subscribe } from "../api.js";
import { store, toast } from "../store.js";
import { route, go } from "../router.js";
import * as C from "../components.js";
import * as F from "../format.js";
import { barChart, lineChart, SERIES } from "../charts.js";

const { ref, computed, watch, onMounted, onBeforeUnmount } = Vue;
const DIM_ORDER = ["Card behaviour", "Cross-product", "Credit bureau", "Forward-looking", "Conduct and sentiment", "Collections", "Model", "EWI status", "Macroeconomic"];
const INJECT = [
  ["Missed payment reported by another lender", "bureau_alert", { worst_status: 2, score_drop: 60, note: "Missed payment reported by another lender" }],
  ["Expected salary not received", "salary_missing", { note: "Expected salary credit from employer not received" }],
  ["Hardship call", "note", { category: "hardship", text: "Customer called: struggling to keep up with payments after a drop in income. Asked about options." }],
  ["Refusal to engage", "note", { category: "refusal", text: "Customer refused to discuss the account and said they will pay when they are ready." }],
  ["Card payment missed", "payment_due", { outcome: "missed" }],
  ["Statement paid in full", "payment_due", { outcome: "full" }],
  ["£400 cash advance", "cash_advance", { amount: 400 }],
];

export default {
  components: C.all,
  setup() {
    const d = ref(null), err = ref(null), tab = ref("card"), showAll = ref(false);
    const apiDrawer = ref(false), apiRes = ref(null), injectKind = ref(0), injecting = ref(false);
    const cid = computed(() => route.params[0]);
    async function load() { if (!cid.value) return; try { d.value = await api.get(`/api/ews/customer/${cid.value}`); err.value = null; } catch (e) { err.value = e; d.value = null; } }
    onMounted(load);
    watch(cid, load);
    watch(() => store.dataVersion, load);
    const unsub = subscribe("live", (ev) => { if ((ev.type === "alert" && ev.alert.customer_id === cid.value) || (ev.type === "event" && ev.event.customer_id === cid.value && !ev.event.routine)) setTimeout(load, 300); });
    onBeforeUnmount(unsub);

    const s = computed(() => d.value?.state || {});
    const name = computed(() => d.value?.persona?.name || `Customer ${cid.value}`);
    const p = computed(() => d.value?.profile || {});
    const fired = computed(() => (d.value?.triggers || []).filter((t) => t.level === "Red" || t.level === "Amber")
      .sort((a, b) => (a.level === b.level ? DIM_ORDER.indexOf(a.dimension) - DIM_ORDER.indexOf(b.dimension) : a.level === "Red" ? -1 : 1)));
    const near = computed(() => (d.value?.triggers || []).filter((t) => t.level === "None" && t.value !== null && closeness(t) > 0.75).sort((a, b) => closeness(b) - closeness(a)).slice(0, 6));
    const allByDim = computed(() => DIM_ORDER.map((dim) => ({ dim, rows: (d.value?.triggers || []).filter((t) => t.dimension === dim) })).filter((g) => g.rows.length));
    function closeness(t) { const v = t.direction === ">=" ? t.value : -t.value, a = t.direction === ">=" ? t.amber : -t.amber; if (a <= 0) return v / (Math.abs(a) || 1) + 1; return v / a; }
    function gauge(t) {
      const sign = t.direction === ">=" ? 1 : -1;
      const v = (t.value ?? 0) * sign, a = t.amber * sign, r = t.red * sign;
      const lo = Math.min(0, v, a), hi = Math.max(r, v, a) * 1.2 + (Math.max(r, v, a) === lo ? 1 : 0);
      const pos = (x) => ((x - lo) / (hi - lo || 1)) * 100;
      return { fill: Math.max(0, Math.min(100, pos(v))), a: pos(a), r: pos(r), color: t.level === "Red" ? "var(--bad)" : t.level === "Amber" ? "var(--warn)" : "var(--accent)" };
    }
    const histOpt = computed(() => {
      const h = d.value?.history || [];
      const pts = h.map((r) => ({ value: r.pd_6m, itemStyle: { color: F.tierColor(r.tier) }, symbolSize: 9 }));
      const o = lineChart({ x: h.map((r) => r.month), y: "pct", series: [{ name: "6-month PD", data: pts }], legend: false });
      o.tooltip.formatter = (ps) => { const r = h[ps[0].dataIndex]; return `<b>${r.month}</b><br>PD ${F.pct(r.pd_6m, 2)} · band ${r.risk_band}<br>${r.tier_name}${r.movement && r.movement !== "Unchanged" ? " (" + r.movement + ")" : ""}<br><span style="opacity:.7">${r.tier_reason || ""}</span>`; };
      return o;
    });
    const driverOpt = computed(() => {
      const ds = (d.value?.drivers || []).slice(0, 12).reverse();
      const o = barChart({ x: ds.map((x) => `${x.feature} = ${F.fval(x.feature, x.value)}`), series: [{ name: "Contribution to log-odds", data: ds.map((x) => ({ value: +x.contribution.toFixed(3),
        itemStyle: { color: x.contribution > 0 ? "#d03b3b" : "#1baf7a", borderRadius: x.contribution > 0 ? [0, 4, 4, 0] : [4, 0, 0, 4] } })) }], y: "dec2", horizontal: true, legend: false });
      o.yAxis.inverse = false; o.grid.left = 8;
      o.tooltip.formatter = (ps) => { const x = ds[ps[0].dataIndex]; return `<b>${x.feature}</b> (${x.family})<br>${x.description || ""}<br>Value ${F.fval(x.feature, x.value)} · contribution ${x.contribution > 0 ? "+" : ""}${x.contribution.toFixed(3)}`; };
      return o;
    });
    const cardOpt = computed(() => {
      const c = d.value?.card || [];
      return lineChart({ x: c.map((r) => r.month), y: "gbp", series: [{ name: "Credit limit", data: c.map((r) => r.credit_limit), dashed: true, showSymbol: false },
        { name: "Statement balance", data: c.map((r) => r.statement_balance), area: true }, { name: "Payment", data: c.map((r) => r.payment_amount) }] });
    });
    const salOpt = computed(() => { const c = d.value?.current_account || []; return barChart({ x: c.map((r) => r.month), y: "gbp", series: [{ name: "Salary credit", data: c.map((r) => r.salary_credit_amt) }], legend: false }); });
    const odOpt = computed(() => { const c = d.value?.current_account || []; return barChart({ x: c.map((r) => r.month), y: "int", series: [{ name: "Days overdrawn", data: c.map((r) => r.overdraft_days), color: SERIES.light[1] }], legend: false }); });
    const buOpt = computed(() => { const c = d.value?.bureau || []; return lineChart({ x: c.map((r) => r.month), series: [{ name: "Bureau score", data: c.map((r) => r.bureau_score) }], legend: false, yMin: "dataMin" }); });
    const timeline = computed(() => {
      const ev = (d.value?.events || []).map((e) => ({ ...e, kind: "event", ts: e.event_ts }));
      const al = (d.value?.alerts || []).map((a) => ({ ...a, kind: "alert", ts: a.alert_ts }));
      return [...ev, ...al].sort((a, b) => (a.ts < b.ts ? 1 : -1));
    });
    const autoAi = computed(() => (s.value.tier ?? 0) >= 1 || !!d.value?.persona);
    async function scoreApi() {
      apiDrawer.value = true; apiRes.value = null;
      try { apiRes.value = await api.post("/api/v1/score", { customer_id: cid.value }); } catch (e) { apiRes.value = { error: e.message }; }
    }
    async function inject() {
      injecting.value = true;
      const [label, type, params] = INJECT[injectKind.value];
      try { const r = await api.post("/api/v1/events", { customer_id: cid.value, event_type: type, params });
        toast(`${label}: ${r.before.tier_name} → ${r.after.tier_name}, PD ${F.pct(r.before.pd_6m, 2)} → ${F.pct(r.after.pd_6m, 2)} (${r.latency_ms} ms)`, "ok", 6000); await load(); }
      catch (e) { toast(e.message, "bad"); }
      injecting.value = false;
    }
    const sentiment = (v) => (v <= -0.3 ? "red" : v >= 0.3 ? "green" : "");
    const tierStrip = computed(() => (d.value?.history || []).map((r) => ({ c: `var(--tier-${r.tier})`, t: `${r.month}: ${r.tier_name}` })));
    return { d, err, s, name, p, fired, near, allByDim, gauge, histOpt, driverOpt, cardOpt, salOpt, odOpt, buOpt, timeline, tab, showAll, F, go, autoAi,
      apiDrawer, apiRes, scoreApi, INJECT, injectKind, inject, injecting, sentiment, tierStrip, cid, USE: F.USE_CASES, store };
  },
  template: `<div>
  <div v-if="err" class="card"><Empty icon="user" title="Customer not found" :text="err.message"><a class="btn" href="#/watchlist">Back to the watchlist</a></Empty></div>
  <template v-else-if="d">
  <div class="card mb"><div class="card-body" style="padding:16px 18px">
    <div class="c360-head">
      <Avatar :name="d.persona?.name || cid.slice(-2)" lg />
      <div class="grow"><div class="row" style="gap:10px"><h1 style="font-size:21px">{{ name }}</h1><span class="mono small muted">{{ cid }}</span>
        <span class="chip" v-if="d.source === 'live'"><span class="dot bad" style="width:6px;height:6px"></span>live position · day {{ store.live?.day }}</span></div>
        <div class="small muted" style="margin-top:3px">{{ p.segment }} · {{ p.region }} · age {{ p.age }} · {{ p.employment_type }} · income {{ F.gbp(p.gross_income_annual, false) }} · {{ p.housing_status }} · customer for {{ Math.round((s.tenure_months || p.tenure_months) / 12) }} years</div>
        <div class="small muted">{{ d.account.product }} card · limit {{ F.gbp(s.credit_limit, false) }} · APR {{ d.account.apr_pct }}% · {{ d.account.payment_method }}</div></div>
      <div style="text-align:right"><div class="row" style="justify-content:flex-end;gap:8px"><TierBadge :tier="s.tier ?? 0" lg /><Band :band="s.risk_band" /></div>
        <div style="font-size:26px;font-weight:700;letter-spacing:-.02em;margin-top:4px">{{ F.pct(s.pd_6m, 2) }} <span class="small muted" style="font-weight:500">6-month PD</span></div>
        <div class="small muted" v-if="d.month_end">Month end: {{ d.month_end.tier_name }} · {{ F.pct(d.month_end.pd_6m, 2) }}</div></div>
    </div>
    <div class="row wrap" style="margin-top:14px;gap:8px">
      <span class="spacer"></span>
      <button class="btn sm" @click="scoreApi"><Icon name="zap" :size="13" />Score now via API</button>
      <select class="input" style="height:28px;font-size:12.5px" v-model="injectKind"><option v-for="(o, i) in INJECT" :key="i" :value="i">{{ o[0] }}</option></select>
      <button class="btn sm" @click="inject" :disabled="injecting || store.live?.status === 'not_loaded'"><Icon :name="injecting ? 'loader-circle' : 'send'" :size="13" />Inject event</button>
      <a class="btn sm ghost" href="#/watchlist"><Icon name="chevron-left" :size="13" />Watchlist</a></div>
  </div></div>

  <div class="grid g-3-2 mb">
    <AiPanel kind="customer" :id="cid" title="Why this customer, and what to do" subtitle="Written from the tier, signals, model drivers, notes and live events" :auto="autoAi" :key="cid + (autoAi ? 'a' : 'm')" />
    <Card title="Decisions across the bank" sub="One set of signals, six consumers">
      <div class="grid g2" style="gap:8px"><div v-for="[k, label, icon] in USE" :key="k" class="decision"><div class="l"><Icon :name="icon" :size="13" />{{ label }}</div><div class="v">{{ d.decisions[k] || '–' }}</div></div></div>
    </Card>
  </div>

  <div class="kpis mb">
    <Kpi label="6-month PD (champion)" :value="F.pct(s.pd_6m, 2)" :sub="'band ' + s.risk_band + ' · ' + (s.pd_ratio_3m ? '×' + s.pd_ratio_3m.toFixed(2) + ' vs 3 months ago' : '')" icon="gauge" />
    <Kpi label="Probability of arrears, 3m" :value="F.pct(s.p_arrears_3m, 1)" icon="clock" />
    <Kpi label="Probability of forbearance, 6m" :value="F.pct(s.p_forbearance_6m, 1)" icon="hand-coins" />
    <Kpi label="Propensity to pay" :value="s.propensity_to_pay === null || s.propensity_to_pay === undefined ? 'n/a' : F.pct(s.propensity_to_pay, 0)" :sub="s.cycles_now > 0 ? 'cure within 2 months' : 'applies once in arrears'" icon="banknote" />
    <Kpi label="Can't pay vs won't pay" :value="(s.tier ?? 0) >= 3 ? s.pay_lean : 'n/a'" :sub="'P(can\\'t pay) ' + F.pct(s.p_cant_pay, 0) + ((s.tier ?? 0) < 3 ? ' · used from early intervention' : '')" icon="scale" />
    <Kpi label="Signal depth and breadth" :value="(s.n_red || 0) + ' red · ' + (s.n_amber || 0) + ' amber'" :sub="(s.breadth || 0) + ' evidence dimensions'" icon="siren" />
  </div>

  <div class="grid g-3-2 mb">
    <Card title="Risk tier history" sub="Monthly batch, April 2025 to September 2026; marker colour is the tier that month">
      <EChart :option="histOpt" :height="250" />
      <div class="strip mt"><i v-for="(x, i) in tierStrip" :key="i" :style="{ background: x.c }" :title="x.t"></i></div>
      <div class="row tiny muted" style="margin-top:4px"><span>{{ d.history[0]?.month }}</span><span class="spacer"></span><span>{{ d.history[d.history.length - 1]?.month }}</span></div>
    </Card>
    <Card title="What drives the model score" sub="Champion model contributions to the log-odds: red pushes risk up, green pulls it down">
      <EChart v-if="d.drivers.length" :option="driverOpt" :height="300" /><div v-else class="muted small">Model drivers appear once the models are published.</div></Card>
  </div>

  <div class="grid g2 mb">
    <Card :title="'Signals firing (' + fired.length + ')'" sub="Each trigger against its amber and red thresholds">
      <template #actions><button class="btn xs" @click="showAll = !showAll">{{ showAll ? 'Hide' : 'Show' }} all 38</button></template>
      <div v-if="!fired.length" class="muted small">No triggers firing.</div>
      <div v-for="t in fired" :key="t.trigger_id" class="trig-row">
        <span class="chip" :class="t.level === 'Red' ? 'red' : 'amber'" style="width:22px;justify-content:center;padding:0">{{ t.level[0] }}</span>
        <div style="min-width:0"><div class="ellipsis"><b>{{ t.name }}</b></div><div class="tiny muted ellipsis">{{ t.dimension }} · {{ t.feature }} {{ t.direction }} {{ F.fval(t.feature, t.amber) }} / {{ F.fval(t.feature, t.red) }}</div></div>
        <div class="gauge"><div class="fill" :style="{ width: gauge(t).fill + '%', background: gauge(t).color }"></div><span class="mark a" :style="{ left: gauge(t).a + '%' }"></span><span class="mark r" :style="{ left: gauge(t).r + '%' }"></span></div>
        <b class="num" style="text-align:right">{{ F.fval(t.feature, t.value) }}</b></div>
      <template v-if="near.length && !showAll"><div class="eyebrow mt">Close to amber</div>
        <div v-for="t in near" :key="t.trigger_id" class="trig-row"><span class="chip" style="width:22px;justify-content:center;padding:0">·</span>
          <div style="min-width:0"><div class="ellipsis">{{ t.name }}</div><div class="tiny muted">amber {{ F.fval(t.feature, t.amber) }}</div></div>
          <div class="gauge"><div class="fill" :style="{ width: gauge(t).fill + '%', background: gauge(t).color }"></div><span class="mark a" :style="{ left: gauge(t).a + '%' }"></span><span class="mark r" :style="{ left: gauge(t).r + '%' }"></span></div>
          <span class="num" style="text-align:right">{{ F.fval(t.feature, t.value) }}</span></div></template>
      <template v-if="showAll"><div v-for="g in allByDim" :key="g.dim"><div class="eyebrow mt">{{ g.dim }}</div>
        <div v-for="t in g.rows" :key="t.trigger_id" class="trig-row"><span class="chip" :class="t.level === 'Red' ? 'red' : t.level === 'Amber' ? 'amber' : ''" style="width:22px;justify-content:center;padding:0">{{ t.level ? t.level[0] : '–' }}</span>
          <div style="min-width:0"><div class="ellipsis">{{ t.trigger_id }} · {{ t.name }}</div><div class="tiny muted ellipsis">{{ t.description }}</div></div>
          <div class="gauge" v-if="t.value !== null"><div class="fill" :style="{ width: gauge(t).fill + '%', background: gauge(t).color }"></div><span class="mark a" :style="{ left: gauge(t).a + '%' }"></span><span class="mark r" :style="{ left: gauge(t).r + '%' }"></span></div><span v-else class="tiny muted">no data</span>
          <span class="num" style="text-align:right">{{ F.fval(t.feature, t.value) }}</span></div></div></template>
    </Card>
    <div class="card"><Tabs in-card v-model="tab" :tabs="[{ key: 'card', label: 'Card', icon: 'credit-card' }, { key: 'ca', label: 'Current account', icon: 'wallet' },
        { key: 'bureau', label: 'Bureau', icon: 'building-2' }, { key: 'notes', label: 'Contact notes', icon: 'message-square-text', count: d.notes.length },
        { key: 'live', label: 'October events', icon: 'activity', count: timeline.length }, { key: 'products', label: 'Products', icon: 'package' }]" />
      <div class="card-body">
        <EChart v-if="tab === 'card'" :option="cardOpt" :height="300" />
        <template v-else-if="tab === 'ca'"><div v-if="!d.current_account.length" class="muted small">No current account with the bank.</div>
          <template v-else><div class="out-title">Salary credits</div><EChart :option="salOpt" :height="140" /><div class="out-title mt">Days overdrawn</div><EChart :option="odOpt" :height="140" /></template></template>
        <EChart v-else-if="tab === 'bureau'" :option="buOpt" :height="300" />
        <div v-else-if="tab === 'notes'" class="col" style="gap:10px;max-height:320px;overflow:auto"><div v-if="!d.notes.length" class="muted small">No contact notes.</div>
          <div v-for="(n, i) in d.notes" :key="i" class="decision"><div class="l"><span>{{ F.dt(n.created_ts, { day: '2-digit', month: 'short', year: 'numeric' }) }} · {{ n.channel }} · {{ n.direction }}</span><span class="spacer"></span>
            <span class="chip" :class="sentiment(n.sentiment)">{{ n.topic }} · {{ n.sentiment.toFixed(2) }}</span></div><div class="v">{{ n.note_text }}</div></div></div>
        <div v-else-if="tab === 'live'" class="timeline" style="max-height:320px;overflow:auto"><div v-if="!timeline.length" class="muted small">No live events yet. Start the live feed or inject an event.</div>
          <div v-for="(x, i) in timeline" :key="i" class="tl-item" :class="{ alert: x.kind === 'alert' }">
            <div class="tiny muted">{{ F.simClock(x.ts) }}</div>
            <div v-if="x.kind === 'event'"><span class="src" :class="F.srcClass(x.source)">{{ x.source }}</span> <b>{{ x.event_type }}</b> <span class="muted">{{ x.detail }}</span></div>
            <div v-else><b>{{ x.alert_type }}:</b> {{ x.tier_before }} → {{ x.tier_after }} · PD {{ F.pct(x.pd_before, 1) }} → {{ F.pct(x.pd_after, 1) }}</div></div></div>
        <div v-else class="col"><div class="decision" v-for="(h, i) in d.holdings" :key="i"><div class="l">{{ h.product_type }} · {{ h.rate_type }}</div>
          <div class="v">Balance {{ F.gbp(h.balance, false) }} · payment {{ F.gbp(h.monthly_payment, false) }}/month<span v-if="h.fix_end_month"> · fixed at {{ h.original_rate_pct }}% until {{ F.dt(h.fix_end_month, { month: 'short', year: 'numeric' }) }}</span></div></div>
          <div class="decision"><div class="l">Credit card · {{ d.account.product }}</div><div class="v">Opened {{ F.dt(d.account.open_date, { month: 'short', year: 'numeric' }) }} · limit at open {{ F.gbp(d.account.credit_limit_at_open, false) }} · now {{ F.gbp(s.credit_limit, false) }}</div></div></div>
      </div></div>
  </div>

  <Drawer :open="apiDrawer" title="POST /api/v1/score" @close="apiDrawer = false" wide>
    <div class="row mb"><span class="method POST">POST</span><span class="mono">/api/v1/score</span><span class="spacer"></span><span class="chip green" v-if="apiRes && !apiRes.error">{{ apiRes.latency_ms }} ms</span></div>
    <label class="lbl">Request</label><JsonView :value="{ customer_id: cid }" />
    <label class="lbl mt">Response</label><div v-if="!apiRes" class="skeleton"></div><JsonView v-else :value="apiRes" />
  </Drawer>
  </template>
  <div v-else class="skeleton" style="height:240px"></div>
</div>`,
};
