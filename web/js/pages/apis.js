// Real-time APIs: endpoint catalogue, try-it console, curl snippets and a live request log
import { api } from "../api.js";
import { store, toast } from "../store.js";
import * as C from "../components.js";
import * as F from "../format.js";

const { ref, computed, onMounted } = Vue;
const ENDPOINTS = [
  { method: "POST", path: "/api/v1/score", title: "Score a customer", desc: "All five models, 38 triggers, the watchlist tier and the six decisions, with the top model drivers. Optional feature overrides make it a what-if.",
    body: { customer_id: "C900001", features: { salary_missing_3m: 2, util: 0.97, od_days: 18 } } },
  { method: "POST", path: "/api/v1/events", title: "Ingest an event", desc: "Apply an event (bureau alert, missed salary, hardship call, missed payment), then re-score, re-trigger and re-tier the customer immediately. Tier changes flow to the outbound connectors.",
    body: { customer_id: "C900006", event_type: "bureau_alert", params: { worst_status: 2, score_drop: 60, note: "Second missed payment reported by another lender" } } },
  { method: "GET", path: "/api/v1/customers/C900002/decision", title: "Current decisions", desc: "What the decision engine, pricing, CRM and collections should do for this customer now." },
  { method: "GET", path: "/api/v1/health", title: "Service health", desc: "Status of the real-time service and the model versions it is serving." },
];

export default {
  components: C.all,
  setup() {
    const sel = ref(0), body = ref(JSON.stringify(ENDPOINTS[0].body, null, 2)), res = ref(null), busy = ref(false), ms = ref(null), code = ref(null), log = ref([]);
    const headers = ref(null);
    function pick(i) { sel.value = i; body.value = ENDPOINTS[i].body ? JSON.stringify(ENDPOINTS[i].body, null, 2) : ""; res.value = null; }
    async function send() {
      const e = ENDPOINTS[sel.value];
      busy.value = true; headers.value = null; const t0 = performance.now();
      try {
        const r = await fetch(e.path, e.method === "POST" ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(JSON.parse(body.value)) } : {});
        code.value = r.status;
        headers.value = Object.fromEntries(["content-type", "x-request-id", "x-model-version", "x-response-time-ms", "x-api-version"]
          .filter((h) => r.headers.get(h)).map((h) => [h.replace(/(^|-)([a-z])/g, (m, a, b) => a + b.toUpperCase()), r.headers.get(h)]));
        res.value = await r.json();
      } catch (err) { res.value = { error: err.message }; code.value = 400; }
      ms.value = Math.round(performance.now() - t0); busy.value = false; loadLog();
    }
    async function loadLog() { log.value = await api.get("/api/apis/calls?limit=25"); }
    onMounted(loadLog);
    const curl = computed(() => {
      const e = ENDPOINTS[sel.value], url = `${location.origin}${e.path}`;
      return e.method === "POST" ? `curl -X POST ${url} \\\n  -H "Content-Type: application/json" \\\n  -d '${(body.value || "{}").replace(/\s*\n\s*/g, " ")}'` : `curl ${url}`;
    });
    function copy() { navigator.clipboard?.writeText(curl.value); toast("curl copied", "ok", 1600); }
    const calls = computed(() => { const seen = new Set(log.value.map((x) => x.id)); return [...store.apiCalls.filter((x) => !seen.has(x.id)), ...log.value].slice(0, 25); });
    const base = location.origin + "/api/v1";
    return { ENDPOINTS, sel, pick, body, res, send, busy, ms, code, curl, copy, calls, F, headers, base };
  },
  template: `<div>
  <div class="page-head"><div><div class="eyebrow">Served by the real-time EWS · OpenAPI documented</div><h1>Real-time APIs</h1>
    <p>What a credit decision engine, CRM or collections platform calls. Scoring runs the published champion models in-process, so a decision comes back in milliseconds.</p>
    <div class="row small" style="gap:8px;margin-top:8px"><span class="chip mono">{{ base }}</span><span class="chip">API version 2026-10-01</span><span class="chip">OpenAPI 3.1</span><span class="chip">JSON over HTTPS</span><span class="chip">X-Request-Id on every response</span></div></div>
    <div class="actions"><a class="btn" href="/docs" target="_blank"><Icon name="book-open" :size="14" />OpenAPI docs</a></div></div>
  <div class="grid g-1-2 mb">
    <div class="col" style="gap:10px">
      <div v-for="(e, i) in ENDPOINTS" :key="e.path" class="conn-card" :style="{ cursor: 'pointer', borderColor: sel === i ? 'var(--accent)' : null, boxShadow: sel === i ? '0 0 0 3px var(--accent-soft)' : null }" @click="pick(i)">
        <div class="row"><span class="method" :class="e.method">{{ e.method }}</span><span class="mono small strong">{{ e.path }}</span></div>
        <div><b>{{ e.title }}</b><div class="small muted">{{ e.desc }}</div></div></div>
    </div>
    <div class="card"><div class="card-head"><span class="method" :class="ENDPOINTS[sel].method">{{ ENDPOINTS[sel].method }}</span><h3 class="mono">{{ ENDPOINTS[sel].path }}</h3>
      <div class="actions"><button class="btn sm" @click="copy"><Icon name="copy" :size="13" />curl</button><button class="btn sm primary" @click="send" :disabled="busy"><Icon :name="busy ? 'loader-circle' : 'send'" :size="13" />Send request</button></div></div>
      <div class="card-body">
        <template v-if="ENDPOINTS[sel].method === 'POST'"><label class="lbl">Request body</label><CodeEditor v-model="body" mode="javascript" :line-numbers="false" /></template>
        <label class="lbl mt">curl</label><pre class="json">{{ curl }}</pre>
        <div class="row mt"><label class="lbl" style="margin:0">Response</label><span class="spacer"></span><span v-if="code" class="chip" :class="code === 200 ? 'green' : 'red'">{{ code }}</span><span v-if="ms !== null" class="chip">{{ ms }} ms round trip</span><span v-if="res?.latency_ms" class="chip blue">{{ res.latency_ms }} ms server</span></div>
        <div v-if="headers" class="out-table mt" style="max-height:none"><table class="tbl compact"><tbody>
          <tr v-for="(v, k) in headers" :key="k"><td class="mono" style="width:190px;color:var(--text-3)">{{ k }}</td><td class="mono">{{ v }}</td></tr></tbody></table></div>
        <div v-if="!res" class="muted small" style="padding:14px 0">Send a request to see the response.</div><JsonView v-else :value="res" style="margin-top:6px;max-height:520px" />
      </div></div>
  </div>
  <Card title="Request log" sub="Every call to the real-time API, newest first" flush>
    <DataTable compact :columns="[{ key: 'ts', label: 'Time' }, { key: 'method', label: 'Method' }, { key: 'path', label: 'Path' }, { key: 'status', label: 'Status', align: 'r' }, { key: 'latency_ms', label: 'Latency', align: 'r' }]" :rows="calls" row-key="id" empty="No calls yet">
      <template #cell-ts="{ value }">{{ F.dt(value, { hour: '2-digit', minute: '2-digit', second: '2-digit' }) }}</template>
      <template #cell-method="{ value }"><span class="method" :class="value">{{ value }}</span></template><template #cell-path="{ value }"><span class="mono small">{{ value }}</span></template>
      <template #cell-status="{ value }"><span class="chip" :class="value === 200 ? 'green' : 'red'">{{ value }}</span></template><template #cell-latency_ms="{ value }">{{ value }} ms</template></DataTable></Card>
</div>`,
};
