// Portfolio overview: the EWS position of the book, with history and an AI executive summary
import { api } from "../api.js";
import { store } from "../store.js";
import { go } from "../router.js";
import * as C from "../components.js";
import * as F from "../format.js";
import { barChart, lineChart, tierStack, SERIES } from "../charts.js";

const { ref, computed, watch, onMounted, onBeforeUnmount } = Vue;

export default {
  components: C.all,
  setup() {
    const d = ref(null), err = ref(null);
    async function load() { try { d.value = await api.get("/api/ews/overview"); err.value = null; } catch (e) { err.value = e; } }
    onMounted(load);
    watch(() => store.dataVersion, load);
    // refresh live figures as the stream moves
    let t = setInterval(() => { if (store.live?.status === "running") load(); }, 6000);
    onBeforeUnmount(() => clearInterval(t));

    const k = computed(() => d.value?.kpis);
    const months = computed(() => [...new Set((d.value?.tier_trend || []).map((r) => r.month))]);
    const tierOpt = computed(() => tierStack({ x: months.value, rows: d.value.tier_trend }));
    const pdOpt = computed(() => {
      const r = (d.value?.pd_trend || []).filter((x) => x.actual !== null);
      const all = d.value?.pd_trend || [];
      return lineChart({ x: all.map((x) => x.month), y: "pct", series: [
        { name: "Predicted (avg 6-month PD)", data: all.map((x) => x.predicted) },
        { name: "Actual 6-month bad rate", data: all.map((x) => x.actual), color: SERIES[store.theme === "dark" ? "dark" : "light"][1] }] });
    });
    const bandOpt = computed(() => barChart({ x: d.value.bands.map((b) => b.band), series: [{ name: "Customers", data: d.value.bands.map((b) => b.customers) }], y: "int", labels: true }));
    const trigOpt = computed(() => {
      const t = d.value.top_triggers.slice(0, 10);
      return barChart({ x: t.map((x) => x.name), series: [{ name: "Customers firing", data: t.map((x) => x.customers) }], y: "int", horizontal: true, labels: true, grid: { right: 40 } });
    });
    const movesOpt = computed(() => {
      const mv = d.value.moves_trend || [];
      const x = [...new Set(mv.map((r) => r.month))].slice(1);
      const get = (m, k) => mv.find((r) => r.month === m && r.movement === k)?.customers || 0;
      return barChart({ x, y: "int", series: [{ name: "Escalated", data: x.map((m) => get(m, "Escalated")), color: F.tierColor(3) },
        { name: "De-escalated", data: x.map((m) => get(m, "De-escalated")), color: SERIES.light[2] }] });
    });
    const ladder = computed(() => {
      if (!k.value) return [];
      const tot = k.value.customers || 1;
      return F.TIERS.map((t, i) => ({ t, i, n: k.value.tiers[t] || 0, share: (k.value.tiers[t] || 0) / tot, delta: (k.value.tiers[t] || 0) - (k.value.tiers_batch[t] || 0) }));
    });
    const live = computed(() => d.value?.source === "live" && store.live?.day > 0);
    const asAt = computed(() => (live.value ? `Live · day ${store.live.day} · ${F.simClock(store.live.clock)}` : `Month end · ${d.value?.as_at}`));
    const onTrig = (p) => { const t = d.value.top_triggers[p.dataIndex]; if (t) go(`triggers/${t.trigger_id}`); };
    const onBand = (p) => go(`watchlist?band=${d.value.bands[p.dataIndex].band}&tier=`);
    const maxWatch = computed(() => Math.max(...(d.value?.regions || []).map((r) => r.watch), 0.01));
    return { d, err, k, tierOpt, pdOpt, bandOpt, trigOpt, movesOpt, ladder, F, live, asAt, onTrig, onBand, go, maxWatch, store };
  },
  template: `<div>
  <div class="page-head"><div><div class="eyebrow">{{ d ? asAt : 'Portfolio' }}</div><h1>Credit card portfolio</h1>
    <p>Where the credit card book sits on the early warning ladder, how it got there and what is driving it. Figures update as live events arrive.</p></div>
    <div class="actions"><a class="btn" href="#/watchlist"><Icon name="list-checks" :size="14" />Watchlist</a><a class="btn primary" href="#/live"><Icon name="activity" :size="14" />Live monitor</a></div></div>
  <NeedPipeline v-if="err" what="the portfolio view" />
  <template v-else-if="d">
    <div class="kpis mb">
      <Kpi label="Customers" :value="F.n(k.customers)" icon="users" sub="active card accounts" />
      <Kpi label="On watchlist" :value="F.n(k.watchlist)" icon="list-checks" :delta="k.watchlist - k.watchlist_batch" :sub="F.pct(k.watchlist / k.customers) + ' of book · vs month end'" color="var(--tier-2)" :flash-key="k.watchlist" />
      <Kpi label="Early intervention" :value="F.n(k.tiers['Early intervention'])" icon="siren" :delta="k.tiers['Early intervention'] - k.tiers_batch['Early intervention']" sub="vs month end" color="var(--tier-3)" :flash-key="k.tiers['Early intervention']" />
      <Kpi label="Collections" :value="F.n(k.tiers['Collections'])" icon="hand-coins" :delta="k.tiers['Collections'] - k.tiers_batch['Collections']" sub="vs month end" color="var(--tier-4)" />
      <Kpi label="Balance at risk" :value="F.gbp(k.balance_at_risk)" icon="wallet" :sub="'Watch or worse · ' + F.pct(k.balance_at_risk / k.total_balance) + ' of balances'" />
      <Kpi label="Expected bad balance" :value="F.gbp(k.expected_bad_balance)" icon="trending-up" sub="6-month PD × balance" />
      <Kpi label="Average 6-month PD" :value="F.pct(k.avg_pd, 2)" icon="gauge" />
    </div>

    <div class="card mb"><div class="card-head"><h3>The early warning ladder</h3><div class="sub">Customers by tier now, and the change since the September month-end batch</div>
      <div class="actions"><span class="small muted">Escalation is immediate; de-escalation needs a clean cure period</span></div></div>
      <div class="card-body">
        <div style="display:flex;height:14px;border-radius:7px;overflow:hidden;gap:2px;margin:4px 0 14px">
          <div v-for="s in ladder" :key="s.t" :style="{ flex: Math.max(s.share, 0.012), background: 'var(--tier-' + s.i + ')' }" :title="s.t + ': ' + s.n"></div></div>
        <div class="grid" style="grid-template-columns:repeat(6,minmax(0,1fr));gap:10px">
          <a v-for="s in ladder" :key="s.t" class="decision" style="text-decoration:none;cursor:pointer" :href="'#/watchlist?tier=' + s.i">
            <div class="l"><TierBadge :tier="s.i" /></div>
            <div class="row" style="align-items:baseline;margin-top:6px"><span style="font-size:20px;font-weight:650;color:var(--text)">{{ F.n(s.n) }}</span>
              <span class="small muted">{{ F.pct(s.share) }}</span></div>
            <div class="small" :class="s.delta > 0 && s.i > 0 ? 'delta-up' : s.delta < 0 && s.i > 0 ? 'delta-down' : 'delta-flat'">{{ s.delta === 0 ? 'no change' : (s.delta > 0 ? '▲ ' : '▼ ') + F.n(Math.abs(s.delta)) + ' since month end' }}</div>
          </a></div></div></div>

    <div class="grid g-3-2 mb">
      <Card title="Watchlist by month" sub="Monthly batch: Monitor, Watch, Early intervention and Collections"><EChart :option="tierOpt" :height="300" /></Card>
      <AiPanel kind="portfolio_live" title="Executive summary" subtitle="For the chief risk officer · regenerate to write from the live position" :stored="d.commentary" />
    </div>
    <div class="grid g2 mb">
      <Card title="Predicted versus actual" sub="Average 6-month PD against the realised 6-month bad rate (blank until outcomes mature)"><EChart :option="pdOpt" :height="260" /></Card>
      <Card title="Escalations and de-escalations" sub="Tier movements by month"><EChart :option="movesOpt" :height="260" /></Card>
    </div>
    <div class="grid g2 mb">
      <Card title="Top signals now" sub="Customer-level triggers firing (regional macro excluded); click to drill in"><EChart :option="trigOpt" :height="320" @click="onTrig" /></Card>
      <Card title="Risk bands" sub="Customers by 6-month PD band, A (lowest) to G; click to filter the watchlist"><EChart :option="bandOpt" :height="320" @click="onBand" /></Card>
    </div>
    <div class="grid g-3-2">
      <Card title="Regional concentration" sub="Share of customers on the watchlist (Monitor to Early intervention)" flush>
        <DataTable :columns="[{ key: 'region', label: 'Region' }, { key: 'customers', label: 'Customers', align: 'r' }, { key: 'watch', label: 'On watchlist', align: 'r' },
          { key: 'early', label: 'Early intervention', align: 'r' }, { key: 'collections', label: 'Collections', align: 'r' }, { key: 'avg_pd', label: 'Avg PD', align: 'r' }, { key: 'balance', label: 'Balance', align: 'r' }]"
          :rows="d.regions" row-key="region" clickable @row-click="r => go('watchlist?region=' + encodeURIComponent(r.region))">
          <template #cell-region="{ value }"><b>{{ value }}</b></template>
          <template #cell-customers="{ value }">{{ F.n(value) }}</template>
          <template #cell-watch="{ value }"><div class="cellbar"><span>{{ F.pct(value) }}</span><PBar :value="value" :max="maxWatch" color="var(--tier-2)" /></div></template>
          <template #cell-avg_pd="{ value }">{{ F.pct(value, 2) }}</template><template #cell-balance="{ value }">{{ F.gbp(value) }}</template>
        </DataTable></Card>
      <Card title="Signals into decisions" sub="What the six business uses are doing with the EWS right now">
        <div class="grid g2" style="gap:10px">
          <a class="decision" href="#/decisioning" style="text-decoration:none"><div class="l"><Icon name="credit-card" :size="13" />Limit increase eligible</div><div class="v" style="font-size:20px;font-weight:650">{{ F.n(k.limit_increase_eligible) }}</div><div class="small muted">Performing, bands A–B</div></a>
          <a class="decision" href="#/decisioning" style="text-decoration:none"><div class="l"><Icon name="headset" :size="13" />Proactive contact</div><div class="v" style="font-size:20px;font-weight:650">{{ F.n(k.proactive_contact) }}</div><div class="small muted">before a missed payment</div></a>
          <a class="decision" href="#/decisioning" style="text-decoration:none"><div class="l"><Icon name="shield-alert" :size="13" />Specialist support</div><div class="v" style="font-size:20px;font-weight:650">{{ F.n(k.specialist_support) }}</div><div class="small muted">vulnerability routing</div></a>
          <a class="decision" href="#/decisioning" style="text-decoration:none"><div class="l"><Icon name="megaphone" :size="13" />Credit marketing suppressed</div><div class="v" style="font-size:20px;font-weight:650">{{ F.n(k.watchlist + k.tiers['Collections'] + k.tiers['Recoveries']) }}</div><div class="small muted">on watchlist or worse</div></a>
        </div>
        <div class="mt"><div class="eyebrow">Watchlist share by segment</div>
          <div v-for="s in d.segments" :key="s.segment" class="row" style="margin:6px 0"><span class="small" style="width:140px">{{ s.segment }}</span><PBar :value="s.watch" :max="0.3" color="var(--tier-2)" style="flex:1" /><span class="small num" style="width:48px;text-align:right">{{ F.pct(s.watch) }}</span></div></div>
      </Card>
    </div>
  </template>
  <div v-else class="skeleton" style="height:200px"></div>
</div>`,
};
