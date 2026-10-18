// Triggers and KRIs: catalogue, thresholds (policy vs calibrated), effectiveness, who is firing now
import { api } from "../api.js";
import { store } from "../store.js";
import { route, go } from "../router.js";
import * as C from "../components.js";
import * as F from "../format.js";
import { barChart } from "../charts.js";

const { ref, computed, watch, onMounted } = Vue;

export default {
  components: C.all,
  setup() {
    const d = ref(null), err = ref(null), dim = ref("All"), sel = ref(null), detail = ref(null);
    async function load() { try { d.value = await api.get("/api/ews/triggers"); err.value = null; } catch (e) { err.value = e; } }
    async function open(id) { sel.value = id; detail.value = null; if (id) detail.value = await api.get(`/api/ews/triggers/${id}`); }
    onMounted(async () => { await load(); if (route.params[0]) open(route.params[0]); });
    watch(() => route.params[0], (id) => open(id || null));
    watch(() => store.dataVersion, load);
    const dims = computed(() => ["All", ...new Set((d.value?.triggers || []).map((t) => t.dimension))]);
    const rows = computed(() => (d.value?.triggers || []).filter((t) => dim.value === "All" || t.dimension === dim.value));
    const fv = (t, v) => F.fval(t.feature, v);
    const scatter = computed(() => {
      const ts = rows.value.filter((t) => t.lift_amber && t.capture_amber !== null);
      return {
        grid: { left: 8, right: 24, top: 20, bottom: 8, containLabel: true },
        tooltip: { trigger: "item", formatter: (p) => { const t = ts[p.dataIndex]; return `<b>${t.trigger_id} · ${t.name}</b><br>Lift ${t.lift_amber.toFixed(1)}× · captures ${F.pct(t.capture_amber)} of future bads<br>Fires for ${F.pct(t.hit_rate_amber, 1)} of customer-months`; } },
        xAxis: { type: "value", name: "Share of future bads captured (amber)", nameLocation: "middle", nameGap: 26, axisLabel: { formatter: (v) => F.pct(v, 0) } },
        yAxis: { type: "log", name: "Lift (bad rate ÷ portfolio rate)", nameLocation: "end", nameTextStyle: { align: "left", padding: [0, 0, 0, -30] }, axisLabel: { formatter: (v) => v + "×" } },
        series: [{ type: "scatter", data: ts.map((t) => [t.capture_amber, t.lift_amber, t.hit_rate_amber]), symbolSize: (v) => 8 + Math.sqrt(v[2]) * 60,
          itemStyle: { color: "#2a78d6", opacity: 0.8, borderColor: store.theme === "dark" ? "#121823" : "#fff", borderWidth: 2 },
          label: { show: true, formatter: (p) => ts[p.dataIndex].trigger_id, position: "right", fontSize: 10.5, color: "inherit" }, labelLayout: { hideOverlap: true } }],
      };
    });
    const histOpt = computed(() => {
      const ds = detail.value?.distribution; if (!ds) return null;
      const t = detail.value.trigger, up = t.direction === ">=";
      const mids = ds.edges.slice(0, -1).map((e, i) => (e + ds.edges[i + 1]) / 2);
      const zone = (m) => (up ? (m >= t.red ? "#d03b3b" : m >= t.amber ? "#fab219" : "#2a78d6") : (m <= t.red ? "#d03b3b" : m <= t.amber ? "#fab219" : "#2a78d6"));
      return barChart({ x: mids.map((m) => F.fval(t.feature, m)), legend: false, y: "int",
        series: [{ name: "Customers", data: ds.counts.map((c, i) => ({ value: c, itemStyle: { color: zone(mids[i]) } })) }], barWidth: "88%" });
    });
    const srcChip = (t) => (t.threshold_source === "Calibrated" ? "blue" : "");
    return { d, err, dim, dims, rows, sel, open, detail, scatter, histOpt, fv, F, go, srcChip };
  },
  template: `<div>
  <div class="page-head"><div><div class="eyebrow">Trigger catalogue · {{ d?.triggers?.length || 0 }} triggers across 9 dimensions</div><h1>Triggers and KRIs</h1>
    <p>Classic KRIs (utilisation, DSR, DSCR) sit alongside signals that are not PD-based at all: collections status, broken promises, persistent EWI status, hardship or refusal language, a fixed rate ending. Thresholds start from credit policy and are calibrated against the data where the evidence supports it.</p></div></div>
  <NeedPipeline v-if="err" what="the trigger catalogue" />
  <template v-else-if="d">
  <div class="kpis mb">
    <Kpi label="Triggers" :value="d.triggers.length" icon="siren" :sub="'across ' + (dims.length - 1) + ' dimensions'" />
    <Kpi label="Calibrated from data" :value="d.triggers.filter(t => t.threshold_source === 'Calibrated').length" icon="sliders-horizontal" sub="others use policy values" />
    <Kpi label="Non-PD signals" :value="d.triggers.filter(t => t.kind !== 'Model' && t.kind !== 'KRI').length" icon="message-square-text" sub="sentiment, collections, EWI status" />
    <Kpi label="Portfolio bad rate" :value="F.pct(d.base_bad_rate, 2)" icon="gauge" sub="6-month, development sample" />
    <Kpi label="Signals firing now" :value="F.n(d.triggers.reduce((a, t) => a + t.firing_now, 0))" icon="bell-ring" :sub="d.source === 'live' ? 'live position' : 'month end'" />
  </div>
  <div class="grid g-1-2 mb">
    <Card title="Strength versus reach" sub="Amber threshold, development sample. Rare and strong signals suit escalation; broad ones suit ranking."><EChart :option="scatter" :height="360" /></Card>
    <div class="card"><div class="card-head"><h3>Catalogue</h3><div class="actions"><select class="input" v-model="dim"><option v-for="x in dims" :key="x">{{ x }}</option></select></div></div>
      <div class="card-body flush"><DataTable :columns="[{ key: 'trigger_id', label: 'ID' }, { key: 'name', label: 'Trigger' }, { key: 'rule', label: 'Rule', sortable: false },
          { key: 'policy', label: 'Policy A / R', sortable: false }, { key: 'selected', label: 'In use A / R', sortable: false }, { key: 'lift_amber', label: 'Lift', align: 'r' },
          { key: 'capture_amber', label: 'Capture', align: 'r' }, { key: 'firing_now', label: 'Firing now', align: 'r' }, { key: 'trend', label: '18-month hits', sortable: false }]"
        :rows="rows" row-key="trigger_id" clickable :selected="sel" @row-click="r => go('triggers/' + r.trigger_id)" :max-height="420" compact>
        <template #cell-trigger_id="{ value }"><span class="mono small strong">{{ value }}</span></template>
        <template #cell-name="{ row }"><div style="min-width:180px"><b>{{ row.name }}</b><div class="tiny muted">{{ row.dimension }} · {{ row.kind }}</div></div></template>
        <template #cell-rule="{ row }"><span class="mono tiny">{{ row.feature }} {{ row.direction }}</span></template>
        <template #cell-policy="{ row }"><span class="num small nowrap">{{ fv(row, row.policy_amber) }} / {{ fv(row, row.policy_red) }}</span></template>
        <template #cell-selected="{ row }"><span class="num small strong nowrap">{{ fv(row, row.amber) }} / {{ fv(row, row.red) }}</span> <span class="chip" :class="srcChip(row)" style="height:18px">{{ row.threshold_source }}</span></template>
        <template #cell-lift_amber="{ value }">{{ value ? value.toFixed(1) + '×' : '–' }}</template>
        <template #cell-capture_amber="{ value }">{{ value !== null && value !== undefined ? F.pct(value, 0) : '–' }}</template>
        <template #cell-firing_now="{ value }"><b>{{ F.n(value) }}</b></template>
        <template #cell-trend="{ value }"><Spark :data="value" bars :width="90" :height="20" /></template>
      </DataTable></div></div>
  </div>
  </template>
  <Drawer :open="!!sel" :title="detail ? detail.trigger.trigger_id + ' · ' + detail.trigger.name : 'Trigger'" @close="go('triggers')" wide>
    <div v-if="!detail" class="skeleton"></div>
    <template v-else>
      <p class="muted" style="margin-top:0">{{ detail.description }}. Fires when <span class="mono">{{ detail.trigger.feature }} {{ detail.trigger.direction }}</span> the threshold. Used for: {{ detail.trigger.use_cases }}.</p>
      <div class="kpis mb">
        <Kpi label="Amber in use" :value="fv(detail.trigger, detail.trigger.amber)" :sub="'policy ' + fv(detail.trigger, detail.trigger.policy_amber) + (detail.trigger.calibrated_amber !== null ? ' · data ' + fv(detail.trigger, detail.trigger.calibrated_amber) : '')" color="var(--warn)" />
        <Kpi label="Red in use" :value="fv(detail.trigger, detail.trigger.red)" :sub="'policy ' + fv(detail.trigger, detail.trigger.policy_red) + (detail.trigger.calibrated_red !== null ? ' · data ' + fv(detail.trigger, detail.trigger.calibrated_red) : '')" color="var(--bad)" />
        <Kpi label="Customers firing now" :value="F.n(detail.firing)" />
        <Kpi label="Threshold source" :value="detail.trigger.threshold_source" :sub="detail.trigger.status" />
      </div>
      <Card v-if="histOpt" title="Where the book sits" sub="Current values across all customers; amber and red zones coloured"><EChart :option="histOpt" :height="240" /></Card>
      <Card class="mt" title="Customers firing now" :sub="detail.firing > 100 ? 'Top 100 by PD' : ''" flush>
        <DataTable :columns="[{ key: 'customer_id', label: 'Customer' }, { key: 'tier_name', label: 'Tier' }, { key: 'pd_6m', label: 'PD 6m', align: 'r' }, { key: 'value', label: 'Value', align: 'r' }, { key: 'region', label: 'Region' }, { key: 'segment', label: 'Segment' }]"
          :rows="detail.customers.map(c => ({ ...c, value: c[detail.trigger.feature] }))" row-key="customer_id" clickable @row-click="r => go('customer/' + r.customer_id)" :max-height="360" compact>
          <template #cell-customer_id="{ row }"><b v-if="row.persona">{{ row.persona }} </b><span class="mono small">{{ row.customer_id }}</span></template>
          <template #cell-tier_name="{ value }"><TierBadge :tier="value" /></template><template #cell-pd_6m="{ value }">{{ F.pct(value, 1) }}</template>
          <template #cell-value="{ value }"><b>{{ fv(detail.trigger, value) }}</b></template></DataTable></Card>
    </template>
  </Drawer>
</div>`,
};
