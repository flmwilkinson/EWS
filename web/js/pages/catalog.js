// Catalog: schemas, tables, schema, sample, history and lineage
import { api } from "../api.js";
import { store } from "../store.js";
import { route, go } from "../router.js";
import * as C from "../components.js";
import * as F from "../format.js";

const { ref, computed, watch, onMounted } = Vue;
const SCHEMA_ICON = { bronze: "layers", silver: "shield-check", gold: "target", ops: "gauge" };

export default {
  components: C.all,
  setup() {
    const cat = ref(null), t = ref(null), tab = ref("overview"), filter = ref(""), loading = ref(false);
    const name = computed(() => route.params[0] || null);
    async function load() { cat.value = await api.get("/api/catalog"); if (!name.value && cat.value.schemas[2]?.tables[0]) go(`catalog/${cat.value.schemas[2].tables.find((x) => x.table === "ews_decisions_latest")?.full_name || cat.value.schemas[2].tables[0].full_name}`); }
    async function open(n) { if (!n) return; loading.value = true; try { t.value = await api.get(`/api/catalog/table?name=${encodeURIComponent(n)}&limit=50`); } catch (e) { t.value = null; } loading.value = false; }
    onMounted(async () => { await load(); open(name.value); });
    watch(name, (n) => open(n));
    watch(() => store.dataVersion, async () => { await load(); open(name.value); });
    const filtered = (tables) => tables.filter((x) => !filter.value || x.table.includes(filter.value.toLowerCase()));
    const sampleCols = computed(() => (t.value?.sample?.[0] ? Object.keys(t.value.sample[0]).map((k) => ({ key: k, label: k })) : []));
    const cur = computed(() => t.value?.history?.[0]);
    function querySql() { try { sessionStorage.setItem("ews-sql", `SELECT *\nFROM ${t.value.name}\nLIMIT 100`); } catch (e) {} go("sql"); }
    const taskTitle = (k) => store.pipeline?.tasks.find((x) => x.key === k)?.title || k;
    return { cat, t, tab, filter, filtered, name, sampleCols, cur, F, SCHEMA_ICON, querySql, loading, taskTitle };
  },
  template: `<div>
  <div class="page-head"><div><div class="eyebrow">Lakehouse · versioned parquet, queried with DuckDB</div><h1>Catalog</h1>
    <p>Four schemas, medallion style. Every write is a new table version with the run and task that produced it, so each table has history and lineage.</p></div>
    <div class="actions" v-if="cat"><span class="chip">{{ cat.schemas.reduce((a, s) => a + s.tables.length, 0) }} tables</span><span class="chip">{{ F.compact(cat.total_rows) }} rows</span>
      <span class="chip">{{ F.bytes(cat.total_bytes) }}</span><span class="chip">{{ cat.models }} model versions</span></div></div>
  <NeedPipeline v-if="cat && !cat.schemas.some(s => s.tables.length)" what="the lakehouse" />
  <div v-else class="grid" style="grid-template-columns: 300px minmax(0,1fr); align-items:start">
    <div class="card" style="position:sticky;top:0">
      <div class="card-body"><input class="input" v-model="filter" placeholder="Filter tables" style="width:100%" /></div>
      <div style="max-height:calc(100vh - 230px);overflow:auto;padding:0 8px 10px">
        <template v-for="s in cat?.schemas || []" :key="s.name">
          <div class="folder-head"><Icon :name="SCHEMA_ICON[s.name]" :size="13" />{{ s.name }}<span class="spacer"></span><span class="tiny">{{ s.tables.length }}</span></div>
          <a v-for="x in filtered(s.tables)" :key="x.full_name" class="ws-file" :class="{ active: name === x.full_name }" :href="'#/catalog/' + x.full_name">
            <Icon name="table" :size="13" /><span class="grow ellipsis">{{ x.table }}</span><span class="tiny muted num">{{ F.compact(x.rows) }}</span></a>
        </template>
      </div>
    </div>
    <div v-if="t" class="col" style="gap:16px">
      <div class="card"><div class="card-head"><Icon name="table" :size="18" /><div><h3 class="mono">{{ t.name }}</h3><div class="sub">{{ t.description || 'No description' }}</div></div>
        <div class="actions"><button class="btn sm" @click="querySql"><Icon name="square-terminal" :size="13" />Query in SQL</button></div></div>
        <div class="card-body"><div class="kpis">
          <Kpi label="Rows" :value="F.n(cur.rows)" icon="list" /><Kpi label="Columns" :value="cur.n_columns" icon="layers" />
          <Kpi label="Size" :value="F.bytes(cur.bytes)" icon="database" /><Kpi label="Version" :value="'v' + cur.version" :sub="cur.operation" icon="history" />
          <Kpi label="Updated" :value="F.ago(cur.ts)" :sub="F.dt(cur.ts)" icon="clock" />
          <Kpi label="Written by" small :value="taskTitle(cur.task)" :sub="'run ' + cur.run_id" icon="workflow" /></div></div></div>
      <div class="card"><Tabs in-card v-model="tab" :tabs="[{ key: 'overview', label: 'Columns', icon: 'layers' }, { key: 'sample', label: 'Sample data', icon: 'table' },
          { key: 'history', label: 'History', icon: 'history', count: t.history.length }, { key: 'lineage', label: 'Lineage', icon: 'network' }]" />
        <div class="card-body" v-if="tab === 'overview'">
          <div v-if="t.connector" class="story mb"><Icon name="cable" :size="15" />Ingested through the <b>{{ t.connector.name }}</b> connector ({{ t.connector.protocol }}). <a href="#/connectors">See connector</a></div>
          <DataTable compact :columns="[{ key: 'i', label: '#' }, { key: 'name', label: 'Column' }, { key: 'type', label: 'Type' }]" :rows="t.columns.map((c, i) => ({ ...c, i: i + 1 }))" row-key="name" :max-height="520">
            <template #cell-name="{ value }"><span class="mono">{{ value }}</span></template><template #cell-type="{ value }"><span class="chip">{{ value }}</span></template></DataTable></div>
        <div class="card-body flush" v-else-if="tab === 'sample'"><div class="out-table" style="border:0;max-height:560px">
          <table class="tbl compact"><thead><tr><th v-for="c in sampleCols" :key="c.key">{{ c.key }}</th></tr></thead>
          <tbody><tr v-for="(r, i) in t.sample" :key="i"><td v-for="c in sampleCols" :key="c.key" class="mono" style="font-size:11.5px;white-space:nowrap;max-width:300px;overflow:hidden;text-overflow:ellipsis">{{ r[c.key] === null ? 'null' : (typeof r[c.key] === 'number' ? +r[c.key].toFixed(4) : r[c.key]) }}</td></tr></tbody></table></div></div>
        <div class="card-body flush" v-else-if="tab === 'history'">
          <DataTable :columns="[{ key: 'version', label: 'Version' }, { key: 'ts', label: 'Timestamp' }, { key: 'operation', label: 'Operation' }, { key: 'rows', label: 'Rows', align: 'r' },
            { key: 'bytes', label: 'Size', align: 'r' }, { key: 'task', label: 'Task' }, { key: 'run_id', label: 'Run' }, { key: 'available', label: 'File' }]" :rows="t.history" row-key="version">
            <template #cell-version="{ value }"><b>v{{ value }}</b></template><template #cell-ts="{ value }">{{ F.dt(value) }}</template>
            <template #cell-rows="{ value }">{{ F.n(value) }}</template><template #cell-bytes="{ value }">{{ F.bytes(value) }}</template>
            <template #cell-run_id="{ value }"><span class="mono small">{{ value }}</span></template>
            <template #cell-available="{ value }"><span class="chip" :class="value ? 'green' : ''">{{ value ? 'retained' : 'vacuumed' }}</span></template></DataTable></div>
        <div class="card-body" v-else>
          <div v-if="!t.upstream.length && !t.downstream.length" class="muted">No lineage recorded yet: lineage is captured when tasks run through the job runner.</div>
          <div v-else class="grid" style="grid-template-columns: 1fr 40px 1fr 40px 1fr; align-items:center">
            <div class="col"><div class="eyebrow">Inputs</div><template v-for="u in t.upstream" :key="u.task"><a v-for="i in u.inputs" :key="i" class="arch-box" :href="'#/catalog/' + i"><Icon name="table" :size="13" /><span class="mono small">{{ i }}</span></a>
              <div v-if="!u.inputs.length" class="arch-box"><Icon name="cable" :size="13" />Landing files</div></template></div>
            <Icon name="arrow-right" :size="20" style="color:var(--muted);justify-self:center" />
            <div class="col"><div class="eyebrow">Produced by</div><a v-for="u in t.upstream" :key="u.task" class="arch-box on" :href="'#/workflows'"><Icon name="workflow" :size="13" />{{ u.title }}</a>
              <div class="arch-box on" style="border-width:2px"><Icon name="table" :size="13" /><b class="mono small">{{ t.name }}</b></div></div>
            <Icon name="arrow-right" :size="20" style="color:var(--muted);justify-self:center" />
            <div class="col"><div class="eyebrow">Read by</div><div v-for="d in t.downstream" :key="d.task" class="arch-box"><Icon name="workflow" :size="13" />{{ d.title }}
              <span class="count">{{ d.outputs.length }} outputs</span></div><div v-if="!t.downstream.length" class="muted small">Read by the API and dashboards only</div></div>
          </div></div>
      </div>
    </div>
  </div></div>`,
};
