// Model monitoring: RAG pack against appetite, trends, stability, early warning effectiveness, flows
import { api } from "../api.js";
import { store } from "../store.js";
import * as C from "../components.js";
import * as F from "../format.js";
import { barChart, lineChart, status } from "../charts.js";

const { ref, computed, watch, onMounted } = Vue;
const AREA = { "Score PSI vs development": "Stability", "Gini (matured month)": "Discrimination", "Gini drop vs development": "Discrimination",
  "Calibration ratio (predicted / actual)": "Calibration", "Bad capture by watchlist": "Early warning", "Median lead time (months)": "Early warning",
  "Watchlist precision (tier 2+)": "Early warning", "Max feature CSI": "Stability" };
const FMT = { "Bad capture by watchlist": (v) => F.pct(v, 0), "Watchlist precision (tier 2+)": (v) => F.pct(v, 1), "Median lead time (months)": (v) => v.toFixed(0) + " months" };

export default {
  components: C.all,
  setup() {
    const d = ref(null), err = ref(null);
    async function load() { try { d.value = await api.get("/api/ews/monitoring"); err.value = null; } catch (e) { err.value = e; } }
    onMounted(load);
    watch(() => store.dataVersion, load);
    const fmtv = (m) => (FMT[m.metric] || ((v) => F.dec(v, 3)))(m.value);
    const appetite = (m) => {
      const [a, r, dir] = d.value.appetite[m.metric];
      const f = FMT[m.metric] || ((v) => v);
      if (dir === "high_bad") return `amber ≥ ${f(a)} · red ≥ ${f(r)}`;
      if (dir === "low_bad") return `amber ≤ ${f(a)} · red ≤ ${f(r)}`;
      return `amber ±${F.pct(a, 0)} · red ±${F.pct(r, 0)}`;
    };
    const giniOpt = computed(() => lineChart({ x: d.value.performance.map((r) => r.month_label), y: "dec2", yMin: 0.5, yMax: 0.9, legend: false,
      series: [{ name: "Gini", data: d.value.performance.map((r) => r.gini) }],
      markLines: [{ value: 0.7, label: "amber 0.70", color: status.warning }, { value: 0.65, label: "red 0.65", color: status.critical }] }));
    const psiOpt = computed(() => lineChart({ x: d.value.stability.map((r) => r.month_label), y: "dec2", legend: false, yMin: 0,
      series: [{ name: "Score PSI", data: d.value.stability.map((r) => r.score_psi) }],
      markLines: [{ value: 0.1, label: "amber 0.10", color: status.warning }, { value: 0.25, label: "red 0.25", color: status.critical }] }));
    const calOpt = computed(() => barChart({ x: d.value.calibration.map((r) => r.risk_band), y: "pct", series: [
      { name: "Predicted", data: d.value.calibration.map((r) => r.predicted) }, { name: "Actual", data: d.value.calibration.map((r) => r.actual) }] }));
    const leadOpt = computed(() => barChart({ x: d.value.lead_distribution.map((r) => (r.months === 0 ? "Not flagged" : r.months + "m")), y: "int", legend: false,
      series: [{ name: "Customers who went 60+ DPD", data: d.value.lead_distribution.map((r) => ({ value: r.customers, itemStyle: { color: r.months === 0 ? status.critical : "#2a78d6" } })) }] }));
    const csiOpt = computed(() => {
      const c = d.value.csi.slice(0, 12).reverse();
      const o = barChart({ x: c.map((r) => r.feature), y: "dec2", horizontal: true, legend: false, series: [{ name: "CSI", data: c.map((r) => ({ value: r.csi, itemStyle: { color: r.csi >= 0.25 ? status.critical : r.csi >= 0.1 ? status.warning : "#2a78d6" } })) }],
        markLines: [{ value: 0.1, label: "0.10", color: status.warning }] });
      o.yAxis.inverse = false; return o;
    });
    const movesOpt = computed(() => {
      const mv = d.value.moves;
      const x = [...new Set(mv.map((r) => r.month_label))].slice(1);
      const get = (m, k) => mv.find((r) => r.month_label === m && r.movement === k)?.customers || 0;
      return barChart({ x, y: "int", series: [{ name: "Escalated", data: x.map((m) => get(m, "Escalated")), color: F.tierColor(3) }, { name: "De-escalated", data: x.map((m) => get(m, "De-escalated")), color: "#1baf7a" },
        { name: "Cured from arrears", data: x.map((m) => get(m, "Cured from arrears")), color: "#2a78d6" }] });
    });
    const trans = computed(() => {
      const rows = d.value?.transitions || [];
      const cols = F.TIERS.filter((t) => rows.length && t in rows[0]);
      const mx = Math.max(1, ...rows.flatMap((r) => cols.map((c) => (r.from_tier === c ? 0 : r[c] || 0))));
      return { rows, cols, mx };
    });
    const cell = (r, c) => r[c] || 0;
    const shade = (r, c) => { const v = cell(r, c); if (!v) return "transparent"; if (r.from_tier === c) return "var(--surface-3)";
      const up = F.TIER_NUM[c] > F.TIER_NUM[r.from_tier]; return `color-mix(in srgb, ${up ? "var(--bad)" : "var(--ok)"} ${Math.round(12 + 50 * Math.sqrt(v / trans.value.mx))}%, transparent)`; };
    const counts = computed(() => { const m = d.value?.metrics || []; return { Green: m.filter((x) => x.status === "Green").length, Amber: m.filter((x) => x.status === "Amber").length, Red: m.filter((x) => x.status === "Red").length }; });
    return { d, err, fmtv, appetite, giniOpt, psiOpt, calOpt, leadOpt, csiOpt, movesOpt, trans, cell, shade, counts, AREA, F };
  },
  template: `<div>
  <div class="page-head"><div><div class="eyebrow">Monthly RAG pack · model and system</div><h1>Model monitoring</h1>
    <p>Discrimination and calibration on matured out-of-time months, stability on every month to September 2026, and the test that matters most for an early warning system: were customers flagged before they missed a payment, and how early?</p></div>
    <div class="actions" v-if="d"><span class="rag Green"><i></i>{{ counts.Green }} green</span><span class="rag Amber"><i></i>{{ counts.Amber }} amber</span><span class="rag Red"><i></i>{{ counts.Red }} red</span></div></div>
  <NeedPipeline v-if="err" what="the monitoring pack" />
  <template v-else-if="d">
  <div class="grid g4 mb">
    <div v-for="m in d.metrics" :key="m.metric" class="kpi"><i class="bar" :style="{ background: m.status === 'Red' ? 'var(--bad)' : m.status === 'Amber' ? 'var(--warn)' : 'var(--ok)' }"></i>
      <div class="label">{{ AREA[m.metric] }} · {{ m.metric }}</div><div class="row" style="align-items:baseline"><div class="value">{{ fmtv(m) }}</div><span class="spacer"></span><RagBadge :status="m.status" /></div>
      <div class="sub">{{ appetite(m) }}</div></div>
  </div>
  <div class="grid g-3-2 mb">
    <AiPanel kind="monitoring_commentary" title="Monitoring commentary" subtitle="For the model risk committee: what needs action and who owns it" :stored="d.commentary" />
    <Card title="Early warning lead time" sub="Months between first flag (Watch or Early intervention) and the first missed payment, for customers who reached 60+ DPD"><EChart :option="leadOpt" :height="270" /></Card>
  </div>
  <div class="grid g3 mb">
    <Card title="Discrimination" sub="Gini by matured observation month"><EChart :option="giniOpt" :height="230" /></Card>
    <Card title="Score stability" sub="PSI against the development sample"><EChart :option="psiOpt" :height="230" /></Card>
    <Card title="Calibration by risk band" sub="Latest matured month"><EChart :option="calOpt" :height="230" /></Card>
  </div>
  <div class="grid g3">
    <Card title="Inputs that moved most" sub="Characteristic stability index, latest month against development"><EChart :option="csiOpt" :height="300" /></Card>
    <Card title="Watchlist flows" sub="Movements by month"><EChart :option="movesOpt" :height="300" /></Card>
    <Card title="Tier transitions" sub="Last month (rows) to this month (columns); red = escalation, green = de-escalation">
      <div class="tbl-wrap"><table class="tbl compact"><thead><tr><th>From \\ to</th><th v-for="c in trans.cols" :key="c" class="r" style="font-size:10.5px">{{ c.replace('Early intervention', 'Early int.') }}</th></tr></thead>
        <tbody><tr v-for="r in trans.rows" :key="r.from_tier"><td class="small strong">{{ r.from_tier }}</td>
          <td v-for="c in trans.cols" :key="c" class="r num" :style="{ background: shade(r, c), fontWeight: r.from_tier === c ? 400 : 600 }">{{ cell(r, c) ? F.n(cell(r, c)) : '' }}</td></tr></tbody></table></div></Card>
  </div>
  </template>
</div>`,
};
