// AI audit log: every model call with purpose, model, tokens, latency, prompt and response
import { api } from "../api.js";
import { store } from "../store.js";
import * as C from "../components.js";
import * as F from "../format.js";

const { ref, computed, onMounted, onBeforeUnmount } = Vue;

export default {
  components: C.all,
  setup() {
    const d = ref(null), sel = ref(null), item = ref(null), purpose = ref("");
    async function load() { d.value = await api.get("/api/ai/log?limit=200"); }
    async function open(r) { sel.value = r.id; item.value = null; item.value = await api.get(`/api/ai/log/${r.id}`); }
    onMounted(load);
    const t = setInterval(load, 10000);
    onBeforeUnmount(() => clearInterval(t));
    const purposes = computed(() => [...new Set((d.value?.rows || []).map((r) => r.purpose))]);
    const rows = computed(() => (d.value?.rows || []).filter((r) => !purpose.value || r.purpose === purpose.value));
    return { d, sel, item, open, purpose, purposes, rows, F, store };
  },
  template: `<div>
  <div class="page-head"><div><div class="eyebrow">AI governance</div><h1>AI audit log</h1>
    <p>Every sentence the AI writes is traceable: which task or page asked, the model and version that answered, the exact system prompt and facts it was given, its response, tokens and latency. Facts are computed in Python; the model only writes from them.</p></div></div>
  <template v-if="d">
  <div class="kpis mb">
    <Kpi label="Model calls" :value="F.n(d.stats.calls)" icon="bot" />
    <Kpi label="Succeeded" :value="d.stats.calls ? F.pct(d.stats.ok / d.stats.calls, 0) : '–'" icon="circle-check" />
    <Kpi label="Input tokens" :value="F.compact(d.stats.input_tokens)" icon="download" />
    <Kpi label="Output tokens" :value="F.compact(d.stats.output_tokens)" icon="upload" />
    <Kpi label="Average latency" :value="d.stats.avg_latency_ms ? (d.stats.avg_latency_ms / 1000).toFixed(1) + 's' : '–'" icon="timer" />
    <Kpi label="Model" small :value="d.status.model" :sub="'effort ' + d.status.effort + ' · ' + (d.status.configured ? 'key configured' : 'no key: templates only')" icon="sparkles" />
  </div>
  <div class="card"><div class="card-head"><h3>Calls</h3><div class="sub">Newest first</div><div class="actions"><select class="input" v-model="purpose"><option value="">All purposes</option><option v-for="p in purposes" :key="p">{{ p }}</option></select></div></div>
    <div class="card-body flush"><DataTable :columns="[{ key: 'ts', label: 'Time' }, { key: 'purpose', label: 'Purpose' }, { key: 'task', label: 'Source' }, { key: 'served_by', label: 'Model' }, { key: 'status', label: 'Status' },
        { key: 'latency_ms', label: 'Latency', align: 'r' }, { key: 'input_tokens', label: 'In', align: 'r' }, { key: 'output_tokens', label: 'Out', align: 'r' }, { key: 'preview', label: 'Response', sortable: false }]"
      :rows="rows" row-key="id" clickable :selected="sel" @row-click="open" :max-height="640" compact>
      <template #cell-ts="{ value }"><span class="nowrap">{{ F.dt(value, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' }) }}</span></template>
      <template #cell-purpose="{ value }"><span class="chip violet">{{ value }}</span></template>
      <template #cell-task="{ row }"><span class="small">{{ row.task && row.task !== 'interactive' ? 'pipeline · ' + row.task : 'app' }}</span></template>
      <template #cell-served_by="{ row }"><span class="mono small">{{ row.served_by || row.model }}</span></template>
      <template #cell-status="{ value }"><span class="chip" :class="value === 'ok' ? 'green' : 'red'">{{ value }}</span></template>
      <template #cell-latency_ms="{ value }">{{ value ? (value / 1000).toFixed(1) + 's' : '–' }}</template>
      <template #cell-preview="{ value }"><span class="small muted ellipsis" style="display:inline-block;max-width:420px">{{ value }}</span></template>
    </DataTable></div></div>
  </template>
  <Drawer :open="!!sel" title="Model call" @close="sel = null" wide>
    <div v-if="!item" class="skeleton"></div>
    <template v-else>
      <div class="row wrap mb" style="gap:6px"><span class="chip violet">{{ item.purpose }}</span><span class="chip">{{ item.served_by || item.model }}</span><span class="chip">{{ item.latency_ms }} ms</span>
        <span class="chip">{{ item.input_tokens }} in · {{ item.output_tokens }} out</span><span class="chip" v-if="item.run_id">run {{ item.run_id }} · {{ item.task }}</span></div>
      <label class="lbl">System prompt</label><pre class="json">{{ item.system }}</pre>
      <label class="lbl mt">User prompt</label><pre class="json">{{ item.prompt }}</pre>
      <label class="lbl mt">Response</label><div class="ai-panel"><div class="ai-body"><Markdown :text="item.response || item.error || ''" /></div></div>
    </template>
  </Drawer>
</div>`,
};
