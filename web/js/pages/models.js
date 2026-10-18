// Models: scorecard versus machine learning, champion rule, ROC, calibration, drivers, scorecard, secondary models, registry
import { api } from "../api.js";
import { store } from "../store.js";
import * as C from "../components.js";
import * as F from "../format.js";
import { barChart, lineChart, SERIES } from "../charts.js";

const { ref, computed, watch, onMounted } = Vue;
const ORDER = ["Weighted scoring model", "Logistic regression scorecard", "Decision tree", "Random forest", "Gradient boosting (LightGBM)", "Neural network (MLP)"];
const SHORT = { "Weighted scoring model": "Weighted score", "Logistic regression scorecard": "Scorecard", "Decision tree": "Decision tree", "Random forest": "Random forest",
  "Gradient boosting (LightGBM)": "LightGBM", "Neural network (MLP)": "Neural network" };

export default {
  components: C.all,
  setup() {
    const d = ref(null), err = ref(null), tab = ref("compare"), scChar = ref(null);
    async function load() { try { d.value = await api.get("/api/ews/models"); err.value = null; } catch (e) { err.value = e; } }
    onMounted(load);
    watch(() => store.dataVersion, load);
    const pal = computed(() => SERIES[store.theme === "dark" ? "dark" : "light"]);
    const color = (m) => pal.value[ORDER.indexOf(m)] || "#888";
    const comp = computed(() => (d.value?.comparison || []).slice().sort((a, b) => b.gini - a.gini));
    const champ = computed(() => comp.value.find((m) => m.is_champion));
    const giniOpt = computed(() => barChart({ x: comp.value.map((m) => SHORT[m.model]), y: "dec2", series: [
      { name: "Development Gini", data: comp.value.map((m) => m.dev_gini), color: "#9aa4b2" }, { name: "Out-of-time Gini", data: comp.value.map((m) => m.gini), color: pal.value[0] }] }));
    const rocOpt = computed(() => {
      const roc = d.value?.roc || [];
      const series = ORDER.filter((m) => roc.some((r) => r.model === m)).map((m) => ({
        name: SHORT[m], type: "line", showSymbol: false, data: roc.filter((r) => r.model === m).map((r) => [r.fpr, r.tpr]),
        lineStyle: { width: m === champ.value?.model ? 3 : 1.6, color: color(m) }, itemStyle: { color: color(m) } }));
      series.push({ name: "Random", type: "line", data: [[0, 0], [1, 1]], showSymbol: false, lineStyle: { type: "dashed", width: 1, color: "#9aa4b2" }, silent: true, tooltip: { show: false } });
      return { grid: { left: 8, right: 16, top: 40, bottom: 8, containLabel: true }, legend: { top: 0, left: 0, data: series.slice(0, -1).map((s) => s.name) },
        tooltip: { trigger: "axis", valueFormatter: (v) => F.pct(v, 1) },
        xAxis: { type: "value", min: 0, max: 1, name: "False positive rate", nameLocation: "middle", nameGap: 26, axisLabel: { formatter: (v) => F.pct(v, 0) } },
        yAxis: { type: "value", min: 0, max: 1, name: "True positive rate", nameLocation: "middle", nameGap: 40, axisLabel: { formatter: (v) => F.pct(v, 0) } }, series };
    });
    const calOpt = computed(() => {
      const dec = d.value?.deciles || [];
      const ms = [champ.value?.model, "Logistic regression scorecard"].filter((v, i, a) => v && a.indexOf(v) === i);
      const x = [...new Set(dec.map((r) => r.decile))];
      const series = [];
      ms.forEach((m) => {
        series.push({ name: `${SHORT[m]}: actual`, data: x.map((i) => dec.find((r) => r.model === m && r.decile === i)?.actual), color: color(m) });
        series.push({ name: `${SHORT[m]}: predicted`, data: x.map((i) => dec.find((r) => r.model === m && r.decile === i)?.predicted), color: color(m), dashed: true });
      });
      return lineChart({ x: x.map((i) => "D" + i), series, y: "pct" });
    });
    const impOpt = computed(() => {
      const imp = (d.value?.importance || []).slice(0, 15).reverse();
      const o = barChart({ x: imp.map((r) => r.feature), series: [{ name: "Share of explained risk", data: imp.map((r) => r.share) }], y: "pct", horizontal: true, legend: false });
      o.yAxis.inverse = false;
      return o;
    });
    const famOpt = computed(() => {
      const fam = {};
      (d.value?.importance || []).forEach((r) => (fam[r.family] = (fam[r.family] || 0) + r.share));
      const e = Object.entries(fam).sort((a, b) => b[1] - a[1]);
      return barChart({ x: e.map((x) => x[0]), series: [{ name: "Share", data: e.map((x) => x[1]) }], y: "pct", horizontal: true, legend: false, labels: true, grid: { right: 44 } });
    });
    const scChars = computed(() => {
      const by = {};
      (d.value?.scorecard || []).forEach((r) => { (by[r.feature] ||= { feature: r.feature, iv: r.information_value, coef: r.coefficient, bins: [] }).bins.push(r); });
      return Object.values(by).sort((a, b) => b.iv - a.iv);
    });
    const scOpt = (c) => barChart({ x: c.bins.map((b) => b.bin), series: [{ name: "Points", data: c.bins.map((b) => b.points) }], y: "int", legend: false, labels: true });
    const regGroups = computed(() => {
      const g = {};
      (d.value?.registry || []).forEach((m) => (g[m.name] ||= []).push(m));
      return Object.entries(g).map(([name, vs]) => ({ name, versions: vs.sort((a, b) => b.version - a.version) }))
        .sort((a, b) => (a.versions[0].family === "pd_6m" ? 0 : 1) - (b.versions[0].family === "pd_6m" ? 0 : 1) || a.name.localeCompare(b.name));
    });
    const stageChip = (s) => (s === "Champion" ? "green" : s === "Challenger" ? "blue" : "");
    const PROF = (m) => [m.explainability, m.governance_effort, m.typical_use];
    return { d, err, tab, comp, champ, giniOpt, rocOpt, calOpt, impOpt, famOpt, scChars, scChar, scOpt, regGroups, stageChip, F, SHORT, color, PROF };
  },
  template: `<div>
  <div class="page-head"><div><div class="eyebrow">Target: 60+ days past due within 6 months · out of time October 2025 to March 2026</div><h1>Models</h1>
    <p>Six approaches to the same PD, compared like for like: traditional (weighted scoring, logistic scorecard) against machine learning (tree, forest, gradient boosting, neural network). All are Platt-calibrated, so probabilities are comparable.</p></div>
    <div class="actions" v-if="champ"><span class="chip green" style="height:28px;font-size:12.5px"><Icon name="target" :size="13" />Champion: {{ champ.model }}</span>
      <a class="btn" href="#/workspace/pipeline/07_train_models.py"><Icon name="file-code" :size="14" />Training notebook</a></div></div>
  <NeedPipeline v-if="err" what="and compare the models" />
  <template v-else-if="d">
  <Tabs v-model="tab" :tabs="[{ key: 'compare', label: 'Scorecard vs machine learning', icon: 'scale' }, { key: 'champion', label: 'Champion drivers', icon: 'target' },
    { key: 'scorecard', label: 'Scorecard points', icon: 'list' }, { key: 'secondary', label: 'Secondary models', icon: 'layers' }, { key: 'registry', label: 'Registry and serving', icon: 'package' }]" />
  <template v-if="tab === 'compare'">
    <div class="card mb"><div class="card-head"><h3>Out-of-time comparison</h3><div class="sub">Sorted by out-of-time Gini. The champion must also give exact per-customer reasons, calibrate within ±25% and have PSI below 0.25.</div></div>
      <div class="card-body flush"><DataTable :columns="[{ key: 'model', label: 'Approach' }, { key: 'n_features', label: 'Features', align: 'r' }, { key: 'dev_gini', label: 'Dev Gini', align: 'r' },
          { key: 'gini', label: 'OOT Gini', align: 'r' }, { key: 'gini_drop_dev_to_oot', label: 'Drop', align: 'r' }, { key: 'ks', label: 'KS', align: 'r' }, { key: 'brier', label: 'Brier', align: 'r' },
          { key: 'top_decile_capture', label: 'Top-decile capture', align: 'r' }, { key: 'calibration_ratio', label: 'Calibration', align: 'r' }, { key: 'psi_vs_dev', label: 'PSI', align: 'r' },
          { key: 'explainability', label: 'Explainability' }, { key: 'governance_effort', label: 'Governance' }, { key: 'train_seconds', label: 'Train', align: 'r' }]" :rows="comp" row-key="model">
        <template #cell-model="{ row }"><span class="row" style="gap:7px"><i :style="{ width: '10px', height: '10px', borderRadius: '3px', background: color(row.model), display: 'inline-block' }"></i><b>{{ row.model }}</b>
          <span v-if="row.is_champion" class="chip green" style="height:18px">Champion</span></span><div class="tiny muted" style="margin-left:17px">{{ row.typical_use }}</div></template>
        <template #cell-dev_gini="{ value }">{{ F.dec(value) }}</template><template #cell-gini="{ value }"><b>{{ F.dec(value) }}</b></template>
        <template #cell-gini_drop_dev_to_oot="{ value }"><span :class="value > 0.1 ? 'delta-up' : ''">{{ value > 0 ? '−' : '+' }}{{ F.dec(Math.abs(value)) }}</span></template>
        <template #cell-ks="{ value }">{{ F.dec(value) }}</template><template #cell-brier="{ value }">{{ F.dec(value, 4) }}</template>
        <template #cell-top_decile_capture="{ value }">{{ F.pct(value, 0) }}</template><template #cell-calibration_ratio="{ value }">{{ F.dec(value, 2) }}</template>
        <template #cell-psi_vs_dev="{ value }">{{ F.dec(value, 3) }}</template><template #cell-train_seconds="{ value }">{{ value }}s</template>
        <template #cell-explainability="{ value }"><span class="small">{{ value }}</span></template><template #cell-governance_effort="{ value }"><span class="small">{{ value }}</span></template>
      </DataTable></div></div>
    <div class="grid g-3-2 mb">
      <AiPanel kind="model_comparison" title="Model comparison for the modelling team" subtitle="Discrimination, calibration, stability and explainability, and the champion choice" :stored="d.commentary" />
      <Card title="Development versus out of time" sub="A big drop means the model learned the development sample, not the problem"><EChart :option="giniOpt" :height="290" /></Card>
    </div>
    <div class="grid g2">
      <Card title="ROC curves, out of time" sub="Champion drawn heavier"><EChart :option="rocOpt" :height="340" /></Card>
      <Card title="Calibration by score decile" sub="Champion and scorecard: predicted (dashed) against actual bad rate"><EChart :option="calOpt" :height="340" /></Card>
    </div>
  </template>
  <template v-else-if="tab === 'champion'">
    <div class="grid g-3-2"><Card :title="'Top drivers: ' + (champ?.model || '')" sub="Mean absolute contribution to the log-odds on a 6,000-row out-of-time sample (native SHAP for LightGBM)"><EChart :option="impOpt" :height="440" /></Card>
      <Card title="By evidence family" sub="Where the explained risk comes from"><EChart :option="famOpt" :height="300" />
        <p class="small muted">Age and vulnerability are excluded from every PD model by design. Monotone constraints mean risk can only rise with utilisation, missed payments, searches, DSR and overdraft use, so every explanation reads the right way round.</p></Card></div>
  </template>
  <template v-else-if="tab === 'scorecard'">
    <div class="grid g-1-2"><Card title="Characteristics" sub="Ranked by information value · click for bins" flush>
        <DataTable compact :columns="[{ key: 'feature', label: 'Characteristic' }, { key: 'iv', label: 'IV', align: 'r', fmt: v => F.dec(v) }, { key: 'coef', label: 'Coefficient', align: 'r', fmt: v => F.dec(v) }, { key: 'nb', label: 'Bins', align: 'r' }]"
          :rows="scChars.map(c => ({ ...c, nb: c.bins.length }))" row-key="feature" clickable :selected="scChar?.feature" @row-click="r => scChar = scChars.find(c => c.feature === r.feature)" :max-height="520" /></Card>
      <Card :title="scChar ? 'Points by bin: ' + scChar.feature : 'Scorecard points'" sub="600 points = 50:1 good:bad odds; 20 points double the odds. Every point is traceable to a binned characteristic.">
        <template v-if="scChar"><EChart :option="scOpt(scChar)" :height="260" /><DataTable compact :columns="[{ key: 'bin', label: 'Bin' }, { key: 'woe', label: 'WoE', align: 'r', fmt: v => F.dec(v) }, { key: 'points', label: 'Points', align: 'r' }]" :rows="scChar.bins" row-key="bin" /></template>
        <div v-else class="muted">Pick a characteristic on the left.</div></Card></div>
  </template>
  <template v-else-if="tab === 'secondary'">
    <div class="grid g4 mb"><div v-for="m in d.secondary" :key="m.target" class="kpi"><div class="label">{{ m.model }}</div><div class="value">{{ F.dec(m.gini) }}</div><div class="sub">Gini · KS {{ F.dec(m.ks) }} · calibration {{ F.dec(m.calibration_ratio, 2) }}</div></div></div>
    <Card title="What each secondary model is for">
      <div class="grid g2" style="gap:10px">
        <div class="decision"><div class="l">Probability of arrears (3 months)</div><div class="v">Up-to-date customers likely to miss a payment soon: drives pre-delinquency reminders and the early intervention tier.</div></div>
        <div class="decision"><div class="l">Probability of forbearance (6 months)</div><div class="v">Who is heading for a forbearance conversation: lets support teams plan capacity and make contact first.</div></div>
        <div class="decision"><div class="l">Propensity to pay (cure within 2 months)</div><div class="v">For customers already in arrears: self-cure nudges for the likely payers, agent time for the rest.</div></div>
        <div class="decision"><div class="l">Can't pay versus won't pay</div><div class="v">Trained on agent-recorded arrears reasons. Separates affordability from willingness so treatment fits; disclosed hardship or refusal overrides it.</div></div>
      </div></Card>
  </template>
  <template v-else>
    <div class="grid g2"><div v-for="g in regGroups" :key="g.name" class="card"><div class="card-head"><span class="conn-icon" style="width:30px;height:30px"><Icon name="package" :size="15" /></span>
        <div><h3 class="mono">{{ g.name }}</h3><div class="sub">{{ g.versions[0].description }}</div></div><div class="actions"><span class="chip" :class="stageChip(g.versions[0].stage)">{{ g.versions[0].stage }}</span></div></div>
      <div class="card-body flush"><DataTable compact :columns="[{ key: 'version', label: 'Version', fmt: v => 'v' + v }, { key: 'stage', label: 'Stage' }, { key: 'gini', label: 'Gini', align: 'r' }, { key: 'n_features', label: 'Features', align: 'r' }, { key: 'registered_at', label: 'Registered', fmt: v => F.dt(v) }, { key: 'run_id', label: 'Run' }]"
        :rows="g.versions.map(v => ({ ...v, gini: v.metrics?.gini }))" row-key="version">
        <template #cell-stage="{ value }"><span class="chip" :class="stageChip(value)">{{ value }}</span></template><template #cell-gini="{ value }">{{ F.dec(value) }}</template>
        <template #cell-run_id="{ value }"><span class="mono tiny">{{ value }}</span></template></DataTable></div></div></div>
    <Card class="mt" title="Serving manifest" sub="What the real-time service has loaded"><JsonView :value="d.serving" /></Card>
  </template>
  </template>
</div>`,
};
