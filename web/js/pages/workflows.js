// Workflows: the pipeline DAG, run history, run matrix and live task detail
import { api, subscribe } from "../api.js";
import { store, toast } from "../store.js";
import { applyEvent, cellProgress } from "../runstate.js";
import { route } from "../router.js";
import * as C from "../components.js";
import * as F from "../format.js";
import { RunCompare } from "../compare.js";

const { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } = Vue;
const NODE_W = 166, COL = 192, ROW = 98, NODE_H = 76;
const REFERENCE = new Set(["bronze.generator_truth", "bronze.personas"]);  // validation reference tables, not shown as outputs
const shown = (ts) => (ts || []).filter((t) => !REFERENCE.has(t.table));

export default {
  components: { ...C.all, RunCompare },
  setup() {
    const tasks = computed(() => store.pipeline?.tasks || []);
    const selRunId = ref(null);
    const selTask = ref(null);
    const view = ref(route.query.view || "history");
    const details = reactive({});      // key -> full task state for the selected run
    const pendingFetch = {};
    const wrap = ref(null), scale = ref(1);
    const showReset = ref(false), clearHistory = ref(false);

    const runsList = computed(() => Object.values(store.runs).sort((a, b) => b.started_at - a.started_at));
    const run = computed(() => (selRunId.value ? store.runs[selRunId.value] : null));
    const stateOf = (k) => (!run.value ? "pending" : run.value.task_state?.[k] ? run.value.task_state[k].status || "pending" : "notinrun");
    const tsOf = (k) => run.value?.task_state?.[k] || {};

    // pick the active or latest run
    watch(() => [store.activeRunId, runsList.value.length], () => {
      if (store.activeRunId && selRunId.value !== store.activeRunId) selectRun(store.activeRunId);
      else if (!selRunId.value && runsList.value.length) selectRun(runsList.value[0].run_id);
    }, { immediate: true });

    function selectRun(id) {
      selRunId.value = id;
      Object.keys(details).forEach((k) => delete details[k]);
      if (selTask.value) loadDetail(selTask.value);
      // running tasks need their cells for progress bars
      const r = store.runs[id];
      Object.entries(r?.task_state || {}).forEach(([k, t]) => { if (t.status === "running") loadDetail(k); });
    }
    async function loadDetail(key) {
      if (!selRunId.value || !run.value?.task_state?.[key]) return;
      if (stateOf(key) === "pending") { details[key] = { key, status: "pending", cells: null }; return; }
      const rid = selRunId.value;
      pendingFetch[key] = [];
      try {
        const t = await api.get(`/api/runs/${rid}/tasks/${key}`);
        if (rid !== selRunId.value) return;
        for (const ev of pendingFetch[key] || []) if (["cell_start", "cell_end", "progress"].includes(ev.event)) applyEvent(t, ev);
        details[key] = t;
      } catch (e) { /* not started */ }
      delete pendingFetch[key];
    }
    const unsub = subscribe("runs", (ev) => {
      if (ev.type !== "cell" || ev.run_id !== selRunId.value) return;
      const key = ev.task_key;
      if (ev.ev.event === "notebook") { details[key] = { key, status: "running", cells: null }; applyEvent(details[key], ev.ev); return; }
      if (pendingFetch[key]) { pendingFetch[key].push(ev.ev); return; }
      if (details[key]) applyEvent(details[key], ev.ev);
      else loadDetail(key);
    });
    onBeforeUnmount(unsub);

    function pickTask(k) { selTask.value = k; if (!details[k] || stateOf(k) !== "pending") loadDetail(k); }

    // layout
    const width = computed(() => Math.max(...tasks.value.map((t) => t.col), 0) * COL + NODE_W);
    const height = computed(() => 3 * ROW - (ROW - NODE_H));
    const pos = (t) => ({ left: t.col * COL + "px", top: t.row * ROW + "px" });
    const edges = computed(() => {
      const out = [];
      const by = Object.fromEntries(tasks.value.map((t) => [t.key, t]));
      for (const t of tasks.value) for (const d of t.deps) {
        const a = by[d], x1 = a.col * COL + NODE_W, y1 = a.row * ROW + NODE_H / 2, x2 = t.col * COL, y2 = t.row * ROW + NODE_H / 2;
        const mx = (x1 + x2) / 2;
        const up = stateOf(d), down = stateOf(t.key);
        out.push({ id: d + t.key, d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`,
          cls: up === "succeeded" && down === "running" ? "flow" : up === "succeeded" && down !== "pending" ? "done" : "" });
      }
      return out;
    });
    function fit() { if (wrap.value) scale.value = Math.min(1.08, (wrap.value.clientWidth - 8) / width.value); }
    let ro;
    onMounted(() => { ro = new ResizeObserver(fit); if (wrap.value) ro.observe(wrap.value); nextTick(fit); if (route.query.task) pickTask(route.query.task); });
    onBeforeUnmount(() => ro?.disconnect());
    watch(width, () => nextTick(fit));

    async function start(scope = "all", task = null) {
      try { const r = await api.post("/api/runs", { scope, task }); toast(scope === "all" ? "Pipeline run started" : `Run started: ${task}`, "info"); selRunId.value = r.run_id; }
      catch (e) { toast(e.message, "bad"); }
    }
    async function cancel() { if (run.value) { await api.post(`/api/runs/${run.value.run_id}/cancel`); toast("Cancelling run", "warn"); } }
    async function doReset() {
      try { await api.post("/api/admin/reset", { clear_history: clearHistory.value }); toast("Environment reset: lakehouse, models and landing files cleared", "ok");
        showReset.value = false;
        if (clearHistory.value) { Object.keys(store.runs).forEach((k) => delete store.runs[k]); selRunId.value = null; }
        store.dataVersion++;
      } catch (e) { toast(e.message, "bad"); }
    }

    const summary = computed(() => {
      const r = run.value; if (!r) return null;
      const ts = Object.values(r.task_state || {});
      return { ok: ts.filter((t) => t.status === "succeeded").length, total: ts.length,
        duration: r.status === "RUNNING" ? (Date.now() / 1000 - r.started_at) : r.duration };
    });
    const now = ref(Date.now());
    const tick = setInterval(() => (now.value = Date.now()), 1000);
    onBeforeUnmount(() => clearInterval(tick));
    const elapsed = (t) => (t.status === "running" && t.started_at ? (now.value / 1000 - t.started_at) : t.duration);

    const matrixRuns = computed(() => runsList.value.slice(0, 14).reverse());
    const detail = computed(() => (selTask.value ? details[selTask.value] : null));
    const taskMeta = computed(() => tasks.value.find((t) => t.key === selTask.value));
    const runningCell = computed(() => detail.value?.cells?.find((c) => c.status === "running"));
    const tail = (cell) => {
      const s = (cell?.outputs || []).filter((o) => o.kind === "stream").map((o) => o.text).join("");
      return s.split("\n").filter(Boolean).slice(-6).join("\n");
    };
    return { store, F, tasks, run, runsList, selRunId, selectRun, selTask, pickTask, stateOf, tsOf, pos, edges, width, height, wrap, scale,
      start, cancel, summary, view, matrixRuns, detail, taskMeta, runningCell, tail, details, cellProgress, elapsed, now,
      showReset, clearHistory, doReset, shown };
  },
  template: `<div>
  <div class="page-head">
    <div><div class="eyebrow">Job · {{ tasks.length }} tasks · {{ store.pipeline?.cluster }}</div><h1>{{ store.pipeline?.name || 'Credit card EWS monthly pipeline' }}</h1>
      <p>The month-end build of the credit card early warning system: land and check the data, build monthly and real-time signals, train and compare models, score and tier every card customer, monitor, write the AI commentary and publish to the real-time service. Each task is a notebook in the Workspace.</p></div>
    <div class="actions">
      <button class="btn" @click="showReset = true" :disabled="!!store.activeRunId"><Icon name="rotate-ccw" :size="14" />Reset environment</button>
      <button v-if="store.activeRunId" class="btn danger" @click="cancel"><Icon name="square" :size="14" />Cancel run</button>
      <button class="btn primary" @click="start('all')" :disabled="!!store.activeRunId"><Icon name="play" :size="14" />Run pipeline</button>
    </div>
  </div>

  <div class="card mb">
    <div class="card-head">
      <template v-if="run"><StatusIcon :status="run.status" :size="20" />
        <div><h3>Run {{ run.run_id }} <span class="status-label" :class="run.status">· {{ run.status.toLowerCase() }}</span></h3>
          <div class="sub">{{ run.scope === 'all' ? 'Full pipeline' : run.scope.replace(':', ' · ') }} · started {{ F.dt(run.started_at) }} · {{ run.trigger }} by {{ run.triggered_by }}</div></div></template>
      <template v-else><StatusIcon status="pending" :size="20" /><div><h3>No runs yet</h3><div class="sub">Every task shows a red cross until it has run. Press Run pipeline.</div></div></template>
      <div class="actions" v-if="run && summary">
        <span class="chip"><Icon name="check" :size="12" />{{ summary.ok }}/{{ summary.total }} tasks</span>
        <span class="chip"><Icon name="timer" :size="12" />{{ F.dur(summary.duration) }}</span>
        <span class="chip"><Icon name="table" :size="12" />{{ F.compact(run.rows_written) }} rows written</span>
        <span class="chip violet"><Icon name="sparkles" :size="12" />{{ run.llm_calls || 0 }} AI calls</span>
      </div>
    </div>
    <div class="card-body">
      <div class="dag-wrap" ref="wrap" :style="{ height: (height * scale + 14) + 'px' }">
        <div class="dag" :style="{ width: width + 'px', height: height + 'px', transform: 'scale(' + scale + ')' }">
          <svg class="edges" :width="width" :height="height"><path v-for="e in edges" :key="e.id" :d="e.d" :class="e.cls" /></svg>
          <div v-for="t in tasks" :key="t.key" class="node" :class="[stateOf(t.key), { sel: selTask === t.key }]" :style="pos(t)" @click="pickTask(t.key)">
            <span class="n-group" :class="t.group">{{ t.group }}</span>
            <div class="n-top"><StatusIcon :status="stateOf(t.key)" /><div class="n-title">{{ t.title }}</div></div>
            <div class="n-file">{{ t.notebook }}</div>
            <div class="n-meta">
              <template v-if="stateOf(t.key) === 'pending'"><span>{{ run ? 'Queued' : 'Not run' }}</span></template>
              <template v-else-if="stateOf(t.key) === 'notinrun'"><span>Not in this run</span></template>
              <template v-else><span v-if="elapsed(tsOf(t.key))"><Icon name="timer" :size="11" /> {{ F.dur(elapsed(tsOf(t.key))) }}</span>
                <span v-if="tsOf(t.key).rows_written">{{ F.compact(tsOf(t.key).rows_written) }} rows</span>
                <span v-if="tsOf(t.key).llm_calls" style="color:#6250d6">✦ {{ tsOf(t.key).llm_calls }}</span></template>
            </div>
            <div class="progress" v-if="stateOf(t.key) === 'running'"><i :style="{ width: (cellProgress(details[t.key]) * 100) + '%' }"></i></div>
          </div>
        </div>
      </div>
      <div class="row small muted" style="gap:16px;margin-top:4px">
        <span class="row" style="gap:5px"><StatusIcon status="pending" :size="14" />Not run yet</span>
        <span class="row" style="gap:5px"><StatusIcon status="running" :size="14" />Running</span>
        <span class="row" style="gap:5px"><StatusIcon status="succeeded" :size="14" />Succeeded</span>
        <span class="row" style="gap:5px"><StatusIcon status="failed" :size="14" />Failed</span>
        <span class="row" style="gap:5px"><StatusIcon status="skipped" :size="14" />Skipped (upstream failed)</span>
        <span class="spacer"></span><span>Click a task to see its cells, output and lineage</span>
      </div>
    </div>
  </div>

  <RunCompare v-if="view === 'compare'" :runs="runsList" v-model:view="view" />
  <div class="grid g-3-2" v-else>
    <div class="card">
      <div class="card-head"><h3>Run history</h3><div class="sub">{{ runsList.length }} runs</div>
        <div class="actions"><Seg v-model="view" :options="[{ value: 'history', label: 'List' }, { value: 'matrix', label: 'Matrix' }, { value: 'compare', label: 'Compare' }]" /></div></div>
      <div class="card-body flush" v-if="view === 'history'">
        <DataTable :columns="[{ key: 'status', label: 'Status' }, { key: 'run_id', label: 'Run' }, { key: 'started_at', label: 'Started' },
            { key: 'duration', label: 'Duration', align: 'r' }, { key: 'scope', label: 'Scope' }, { key: 'tasks', label: 'Tasks', sortable: false },
            { key: 'rows_written', label: 'Rows', align: 'r' }, { key: 'llm_calls', label: 'AI', align: 'r' }]"
          :rows="runsList" row-key="run_id" clickable :selected="selRunId" @row-click="r => selectRun(r.run_id)" :max-height="430" empty="No runs yet">
          <template #cell-status="{ row }"><span class="row" style="gap:6px"><StatusIcon :status="row.status" :size="16" /><span class="status-label" :class="row.status">{{ row.status.toLowerCase() }}</span></span></template>
          <template #cell-run_id="{ row }"><span class="mono small">{{ row.run_id }}</span></template>
          <template #cell-started_at="{ row }"><span class="nowrap">{{ F.dt(row.started_at) }}</span></template>
          <template #cell-duration="{ row }">{{ row.status === 'RUNNING' ? F.dur(now / 1000 - row.started_at) : F.dur(row.duration) }}</template>
          <template #cell-scope="{ row }"><span class="small">{{ row.scope === 'all' ? 'Full pipeline' : row.scope.replace('only:', 'Only ').replace('from:', 'From ') }}</span></template>
          <template #cell-tasks="{ row }"><span class="mini-tasks"><i v-for="k in row.tasks" :key="k" :class="row.task_state?.[k]?.status" :title="k + ': ' + (row.task_state?.[k]?.status || '')"></i></span></template>
          <template #cell-rows_written="{ row }">{{ F.compact(row.rows_written) }}</template>
          <template #cell-llm_calls="{ row }">{{ row.llm_calls || 0 }}</template>
        </DataTable>
      </div>
      <div class="card-body" v-else>
        <div class="tbl-wrap"><table class="tbl compact"><thead><tr><th>Task</th><th v-for="r in matrixRuns" :key="r.run_id" class="c" :title="r.run_id">{{ F.dt(r.started_at, { day: '2-digit', month: 'short' }) }}<br><span class="tiny muted">{{ F.dt(r.started_at, { hour: '2-digit', minute: '2-digit' }) }}</span></th></tr></thead>
          <tbody><tr v-for="t in tasks" :key="t.key"><td class="nowrap">{{ t.title }}</td>
            <td v-for="r in matrixRuns" :key="r.run_id" class="c"><span class="matrix-cell" :class="r.task_state?.[t.key]?.status" :title="(r.task_state?.[t.key]?.status || 'not in run') + (r.task_state?.[t.key]?.duration ? ' · ' + F.dur(r.task_state[t.key].duration) : '')" @click="selectRun(r.run_id); pickTask(t.key)" style="cursor:pointer"></span></td></tr></tbody></table></div>
      </div>
    </div>

    <div class="card">
      <template v-if="taskMeta">
        <div class="card-head"><StatusIcon :status="stateOf(taskMeta.key)" :size="20" /><div><h3>{{ taskMeta.title }}</h3><div class="sub">{{ taskMeta.desc }}</div></div></div>
        <div class="card-body">
          <div class="row wrap mb" style="gap:6px">
            <a class="btn sm" :href="'#/workspace/pipeline/' + taskMeta.notebook"><Icon name="file-code" :size="13" />Open notebook</a>
            <button class="btn sm" @click="start('only', taskMeta.key)" :disabled="!!store.activeRunId"><Icon name="play" :size="13" />Run task</button>
            <button class="btn sm" @click="start('from', taskMeta.key)" :disabled="!!store.activeRunId"><Icon name="skip-forward" :size="13" />Run from here</button>
            <span class="spacer"></span><span class="small muted" v-if="tsOf(taskMeta.key).duration || tsOf(taskMeta.key).status === 'running'">{{ F.dur(elapsed(tsOf(taskMeta.key))) }}</span>
          </div>
          <div v-if="detail?.error" class="out-error mb" style="white-space:pre-wrap">{{ detail.error }}</div>
          <div v-if="!detail || !detail.cells" class="muted small" style="padding:10px 0">{{ stateOf(taskMeta.key) === 'pending' ? 'This task has not run yet in the selected run.' : stateOf(taskMeta.key) === 'notinrun' ? 'This task is not part of the selected run. Pick another run in the history, or run it.' : 'Loading…' }}</div>
          <template v-else>
            <div class="eyebrow">Cells</div>
            <div v-for="c in detail.cells.filter(c => c.type === 'code')" :key="c.index" class="row" style="padding:5px 0;border-bottom:1px solid var(--border)">
              <StatusIcon :status="c.status" :size="16" /><span class="grow ellipsis small" :class="{ strong: c.status === 'running' }">{{ c.title || ('Cell ' + c.index) }}</span>
              <span class="mono tiny muted">{{ c.duration !== null && c.duration !== undefined ? F.dur(c.duration) : '' }}</span></div>
            <pre v-if="runningCell && tail(runningCell)" class="out-stream mt" style="background:var(--code-bg);border:1px solid var(--border);border-radius:7px;padding:8px 10px;max-height:150px">{{ tail(runningCell) }}</pre>
            <template v-if="shown(detail.reads).length"><div class="eyebrow mt">Read</div>
              <div class="out-chips"><a v-for="t in shown(detail.reads)" :key="t.table" class="chip" :href="'#/catalog/' + t.table">{{ t.table }} v{{ t.version }}</a></div></template>
            <template v-if="shown(detail.tables).length"><div class="eyebrow mt">Wrote</div>
              <div class="out-chips"><a v-for="t in shown(detail.tables)" :key="t.table + t.version" class="chip blue" :href="'#/catalog/' + t.table">{{ t.table }} v{{ t.version }} · {{ F.compact(t.rows) }}</a></div></template>
          </template>
        </div>
      </template>
      <Empty v-else icon="workflow" title="Select a task" text="Click any task in the graph to see its cells tick off as they run, the tables it read and wrote, and its output." />
    </div>
  </div>

  <Drawer :open="showReset" title="Reset environment" @close="showReset = false">
    <p>Deletes the pipeline outputs so the next run starts from nothing: landing files, every lakehouse table, the model registry and the serving manifest. Notebooks and their revision history are kept.</p>
    <label class="row" style="margin:14px 0"><input type="checkbox" v-model="clearHistory" />Also clear run history and the AI audit log</label>
    <div class="row"><button class="btn danger" @click="doReset"><Icon name="rotate-ccw" :size="14" />Reset</button><button class="btn ghost" @click="showReset = false">Cancel</button></div>
  </Drawer>
</div>`,
};
