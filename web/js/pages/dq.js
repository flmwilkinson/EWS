// Data quality: rule results, remediation, quarantine, reconciliation, AI commentary
import { api } from "../api.js";
import { store } from "../store.js";
import * as C from "../components.js";
import * as F from "../format.js";
import { barChart, lineChart, status } from "../charts.js";

const { ref, computed, watch, onMounted } = Vue;
const SEV = { Critical: "red", High: "amber", Medium: "" };

export default {
  components: C.all,
  setup() {
    const d = ref(null), err = ref(null);
    async function load() { try { d.value = await api.get("/api/ews/dq"); err.value = null; } catch (e) { err.value = e; } }
    onMounted(load);
    watch(() => store.dataVersion, load);
    const res = computed(() => {
      const ex = Object.fromEntries((d.value?.explanations || []).map((e) => [e.rule_id, e.why_it_matters]));
      return (d.value?.results || []).map((r) => ({ ...r, why: ex[r.rule_id] })).sort((a, b) => (a.status === b.status ? 0 : a.status === "FAIL" ? -1 : 1));
    });
    const dimOpt = computed(() => {
      const dims = [...new Set(res.value.map((r) => r.dimension))];
      return barChart({ x: dims, stacked: true, y: "int", series: [
        { name: "Passed", color: status.good, data: dims.map((x) => res.value.filter((r) => r.dimension === x && r.status === "PASS").length) },
        { name: "Failed", color: status.critical, data: dims.map((x) => res.value.filter((r) => r.dimension === x && r.status === "FAIL").length) }] });
    });
    const runOpt = computed(() => {
      const r = d.value?.runs || [];
      return lineChart({ x: r.map((x) => F.dt(x.ts)), y: "int", series: [{ name: "Rules failed", data: r.map((x) => x.failed), color: status.critical }, { name: "Rules passed", data: r.map((x) => x.passed), color: status.good }] });
    });
    const k = computed(() => {
      const r = res.value;
      return { rules: r.length, passed: r.filter((x) => x.status === "PASS").length, failed: r.filter((x) => x.status === "FAIL").length,
        critical: r.filter((x) => x.status === "FAIL" && x.severity === "Critical").length, quarantined: (d.value?.quarantine || []).reduce((a, q) => a + q.rows, 0),
        remediated: (d.value?.remediation || []).reduce((a, q) => a + q.rows_affected, 0) };
    });
    return { d, err, res, dimOpt, runOpt, k, F, SEV };
  },
  template: `<div>
  <div class="page-head"><div><div class="eyebrow">Rules as data · DuckDB SQL over bronze</div><h1>Data quality</h1>
    <p>Seven dimensions of checks run on every load. Failures are remediated into silver with a named action (deduplicate, quarantine, impute and flag, carry forward as stale), and an AI commentary says whether the data is fit for scoring.</p></div></div>
  <NeedPipeline v-if="err" what="the data quality results" />
  <template v-else-if="d">
  <div class="kpis mb">
    <Kpi label="Rules run" :value="k.rules" icon="list-checks" :sub="d.runs.length + ' runs recorded'" />
    <Kpi label="Passed" :value="k.passed" icon="circle-check" color="var(--ok)" />
    <Kpi label="Failed" :value="k.failed" icon="circle-x" color="var(--bad)" />
    <Kpi label="Critical failures" :value="k.critical" icon="triangle-alert" color="var(--bad)" sub="all remediated" />
    <Kpi label="Rows remediated" :value="F.n(k.remediated)" icon="refresh-cw" />
    <Kpi label="Rows quarantined" :value="F.n(k.quarantined)" icon="shield-alert" />
  </div>
  <div class="grid g-3-2 mb">
    <AiPanel kind="dq_commentary" title="Data quality commentary" subtitle="Is the data fit for scoring? What should data owners fix at source?" :stored="d.commentary" />
    <Card title="Results by dimension"><EChart :option="dimOpt" :height="260" /></Card>
  </div>
  <div class="card mb"><div class="card-head"><h3>Rule results</h3><div class="sub">Latest run. Each failed rule also gets a one-line AI explanation of why it matters.</div></div>
    <div class="card-body flush"><DataTable :columns="[{ key: 'status', label: 'Status' }, { key: 'rule_id', label: 'Rule' }, { key: 'description', label: 'Check' }, { key: 'dimension', label: 'Dimension' },
        { key: 'severity', label: 'Severity' }, { key: 'records_tested', label: 'Tested', align: 'r' }, { key: 'records_failed', label: 'Failed', align: 'r' },
        { key: 'fail_rate', label: 'Fail rate', align: 'r' }, { key: 'threshold', label: 'Tolerance', align: 'r' }, { key: 'action', label: 'Remediation' }, { key: 'why', label: 'Why it matters (AI)', sortable: false }]"
      :rows="res" row-key="rule_id" :sortable="true">
      <template #cell-status="{ value }"><span class="row" style="gap:5px"><StatusIcon :status="value === 'PASS' ? 'succeeded' : 'failed'" :size="16" /><span class="small strong" :style="{ color: value === 'PASS' ? 'var(--ok-text)' : 'var(--bad-text)' }">{{ value }}</span></span></template>
      <template #cell-rule_id="{ value }"><span class="mono small strong">{{ value }}</span></template>
      <template #cell-description="{ row }"><b>{{ row.description }}</b><div class="tiny muted mono">{{ row.table_name }}</div></template>
      <template #cell-severity="{ value }"><span class="chip" :class="SEV[value]">{{ value }}</span></template>
      <template #cell-records_tested="{ value }">{{ F.n(value) }}</template><template #cell-records_failed="{ value }"><b>{{ F.n(value) }}</b></template>
      <template #cell-fail_rate="{ value }">{{ F.pct(value, 2) }}</template><template #cell-threshold="{ value }">{{ F.pct(value, 1) }}</template>
      <template #cell-action="{ value }"><span class="small">{{ value }}</span></template>
      <template #cell-why="{ value }"><span v-if="value" class="small clamp3" style="max-width:340px;color:var(--text)" :title="value"><Icon name="sparkles" :size="11" style="color:#6250d6;margin-right:3px" />{{ value }}</span></template>
    </DataTable></div></div>
  <div class="grid g3">
    <Card title="Remediation log" sub="What each action changed" flush><DataTable compact :columns="[{ key: 'rule_id', label: 'Rule' }, { key: 'table_name', label: 'Table' }, { key: 'action', label: 'Action' }, { key: 'rows_affected', label: 'Rows', align: 'r' }]" :rows="d.remediation" row-key="rule_id" /></Card>
    <Card title="Bronze to silver" sub="Row reconciliation by table" flush><DataTable compact :columns="[{ key: 'table', label: 'Table' }, { key: 'bronze_rows', label: 'Bronze', align: 'r', fmt: v => F.n(v) }, { key: 'silver_rows', label: 'Silver', align: 'r', fmt: v => F.n(v) }, { key: 'diff', label: 'Change', align: 'r', fmt: v => (v > 0 ? '+' : '') + F.n(v) }]"
      :rows="d.tables.map(t => ({ ...t, diff: (t.silver_rows || 0) - (t.bronze_rows || 0) }))" row-key="table" /></Card>
    <Card title="Run history" sub="Pass and fail counts across pipeline runs"><EChart :option="runOpt" :height="220" />
      <div class="eyebrow mt">Quarantine</div><div v-for="q in d.quarantine" :key="q.rule_id + q.source_table" class="row small" style="padding:3px 0"><span class="mono">{{ q.rule_id }}</span><span class="grow">{{ q.reason }} · {{ q.source_table }}</span><b>{{ q.rows }}</b></div></Card>
  </div>
  </template>
</div>`,
};
