// SQL editor over the lakehouse (read-only DuckDB)
import { api } from "../api.js";
import { toast } from "../store.js";
import * as C from "../components.js";
import { barChart, lineChart } from "../charts.js";

const { ref, computed, onMounted } = Vue;
const EXAMPLES = [
  ["Watchlist by tier and region", `SELECT region, tier_name, COUNT(*) AS customers, ROUND(AVG(pd_6m), 4) AS avg_pd_6m
FROM gold.ews_decisions_latest
WHERE tier BETWEEN 1 AND 4
GROUP BY ALL
ORDER BY region, MIN(tier)`],
  ["Most frequent triggers this month", `SELECT trigger_id, trigger_name, dimension, severity, COUNT(DISTINCT customer_id) AS customers
FROM gold.ews_triggers_fired
WHERE month = (SELECT MAX(month) FROM gold.ews_triggers_fired)
GROUP BY ALL
ORDER BY customers DESC
LIMIT 15`],
  ["Champion versus challengers", `SELECT model, is_champion, ROUND(dev_gini, 3) AS dev_gini, ROUND(gini, 3) AS oot_gini,
       ROUND(calibration_ratio, 3) AS calibration, explainability
FROM gold.model_comparison
ORDER BY gini DESC`],
  ["Predicted versus actual bad rate by month", `SELECT month, ROUND(AVG(pd_6m), 4) AS predicted, ROUND(AVG(bad_6m), 4) AS actual, COUNT(*) AS customers
FROM gold.ews_scores_monthly
GROUP BY month
ORDER BY month`],
  ["Can't pay versus won't pay in collections", `SELECT pay_lean, COUNT(*) AS customers, ROUND(AVG(propensity_to_pay), 3) AS avg_propensity_to_pay,
       ROUND(AVG(p_cant_pay), 3) AS avg_p_cant_pay
FROM gold.ews_decisions_latest
WHERE tier = 4
GROUP BY pay_lean`],
  ["Data quality history across runs", `SELECT run_id, MIN(run_ts) AS run_ts, SUM(CASE WHEN status = 'PASS' THEN 1 ELSE 0 END) AS passed,
       SUM(CASE WHEN status = 'FAIL' THEN 1 ELSE 0 END) AS failed
FROM ops.dq_results
GROUP BY run_id
ORDER BY run_ts`],
];

export default {
  components: C.all,
  setup() {
    let initial = EXAMPLES[0][1];
    try { const s = sessionStorage.getItem("ews-sql"); if (s) { initial = s; sessionStorage.removeItem("ews-sql"); } } catch (e) {}
    const q = ref(initial), res = ref(null), err = ref(null), running = ref(false), view = ref("table");
    async function run() {
      running.value = true; err.value = null;
      try { res.value = await api.post("/api/sql", { query: q.value }); } catch (e) { err.value = e.message; res.value = null; }
      running.value = false;
    }
    onMounted(run);
    const fmt = (v) => (v === null || v === undefined ? "null" : typeof v === "number" ? (Number.isInteger(v) ? v.toLocaleString("en-GB") : +v.toFixed(4)) : v);
    const cols = computed(() => (res.value?.columns || []).map((c) => ({ key: c.name, label: c.name, fmt, cls: "mono",
      align: /int|float|double|decimal/.test(c.type) ? "r" : "" })));
    const numericCols = computed(() => (res.value?.columns || []).filter((c) => /int|float|double|decimal/.test(c.type)).map((c) => c.name));
    const chartable = computed(() => res.value && res.value.rows.length > 1 && numericCols.value.length && res.value.columns.length >= 2);
    const option = computed(() => {
      if (!chartable.value) return null;
      const xcol = res.value.columns.find((c) => !numericCols.value.includes(c.name))?.name || res.value.columns[0].name;
      const ys = numericCols.value.filter((c) => c !== xcol).slice(0, 4);
      const x = res.value.rows.map((r) => String(r[xcol]).slice(0, 24));
      const series = ys.map((y) => ({ name: y, data: res.value.rows.map((r) => r[y]) }));
      return /month|date|ts/.test(xcol) ? lineChart({ x, series }) : barChart({ x, series });
    });
    return { q, res, err, run, running, cols, EXAMPLES, view, chartable, option, fmt };
  },
  template: `<div>
  <div class="page-head"><div><div class="eyebrow">DuckDB over the lakehouse · read only</div><h1>SQL editor</h1>
    <p>Query any bronze, silver, gold or ops table directly. Ctrl+Enter runs the query.</p></div>
    <div class="actions"><select class="input" @change="e => { q = EXAMPLES[e.target.value][1]; run(); }"><option v-for="(e, i) in EXAMPLES" :key="i" :value="i">{{ e[0] }}</option></select>
      <button class="btn primary" @click="run" :disabled="running"><Icon :name="running ? 'loader-circle' : 'play'" :size="14" />Run</button></div></div>
  <div class="card mb"><CodeEditor v-model="q" mode="sql" @run="run" @save="run" /></div>
  <div v-if="err" class="out-error mb">{{ err }}</div>
  <div class="card" v-if="res">
    <div class="card-head"><h3>Results</h3><div class="sub">{{ res.row_count.toLocaleString() }} rows · {{ res.elapsed_ms }} ms</div>
      <div class="actions"><Seg v-if="chartable" v-model="view" :options="[{ value: 'table', label: 'Table' }, { value: 'chart', label: 'Chart' }]" /></div></div>
    <div class="card-body flush" v-if="view === 'table' || !chartable"><DataTable :columns="cols" :rows="res.rows" :max-height="560" compact /></div>
    <div class="card-body" v-else><EChart :option="option" :height="380" /></div>
  </div></div>`,
};
