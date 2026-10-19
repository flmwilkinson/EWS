// Compare runs: the code that changed between two runs, and the results side by side
import { api } from "./api.js";
import * as C from "./components.js";
import * as F from "./format.js";

const { ref, computed, watch } = Vue;

const fmt = (v, f) => (v === null || v === undefined ? "–" : f === "text" ? v : f === "pct" ? F.pct(v, 1) : f === "dec3" ? F.dec(v, 3)
  : f === "dec1" ? F.dec(v, 1) : f === "dur" ? F.dur(v) : F.n(v));
const fmtDelta = (d, f) => {
  if (d === null || d === undefined || Math.abs(d) < 1e-9) return "";
  const s = d > 0 ? "+" : "−", a = Math.abs(d);
  return s + (f === "pct" ? (a * 100).toFixed(1) + " pts" : f === "dec3" ? a.toFixed(3) : f === "dec1" ? a.toFixed(1) : f === "dur" ? F.dur(a) : F.n(a));
};
const scopeLabel = (s) => (s === "all" ? "Full pipeline" : (s || "").replace("only:", "Only ").replace("from:", "From "));

export const RunCompare = {
  components: C.all,
  props: { runs: Array, view: String },
  emits: ["update:view"],
  setup(props) {
    const done = computed(() => props.runs.filter((r) => r.status === "SUCCEEDED"));
    // default: the latest successful run (B) against the last earlier run whose model training code was different (A)
    const a = ref(null), b = ref(null);
    const modelsHash = (r) => r?.task_state?.models?.code_hash;
    function defaults() {
      const ok = done.value;
      if (!ok.length) return;
      if (!b.value || !ok.find((r) => r.run_id === b.value)) b.value = ok[0].run_id;
      if (!a.value || a.value === b.value || !ok.find((r) => r.run_id === a.value)) {
        const rb = ok.find((r) => r.run_id === b.value);
        const prev = ok.filter((r) => r.started_at < rb.started_at);
        const withModels = prev.filter((r) => (r.tasks || []).includes("models"));
        a.value = (withModels.find((r) => modelsHash(r) && modelsHash(rb) && modelsHash(r) !== modelsHash(rb)) || withModels[0] || prev[0] || {}).run_id || null;
      }
    }
    watch(() => done.value.map((r) => r.run_id).join(), defaults, { immediate: true });
    const data = ref(null), err = ref(null), loading = ref(false);
    async function load() {
      if (!a.value || !b.value || a.value === b.value) { data.value = null; return; }
      loading.value = true; err.value = null;
      try { data.value = await api.get(`/api/runs/compare?a=${a.value}&b=${b.value}`); } catch (e) { err.value = e.message; }
      loading.value = false;
    }
    watch([a, b], load, { immediate: true });
    const groups = computed(() => {
      const out = [];
      for (const r of data.value?.results || []) {
        let g = out.find((x) => x.name === r.group);
        if (!g) out.push((g = { name: r.group, rows: [] }));
        g.rows.push(r);
      }
      return out;
    });
    const tone = (r) => {
      if (r.fmt === "text") return r.a !== r.b ? "changed" : "";
      if (r.delta === null || r.delta === undefined || Math.abs(r.delta) < 1e-9 || r.better === null || r.better === undefined) return "";
      return (r.delta > 0) === r.better ? "better" : "worse";
    };
    const label = (r) => `${r.run_id} · ${scopeLabel(r.scope)} · ${F.dt(r.started_at)}`;
    return { a, b, done, data, err, loading, groups, fmt, fmtDelta, tone, label, scopeLabel, F };
  },
  template: `<div class="card">
    <div class="card-head"><h3>Compare runs</h3><div class="sub">What changed in the code, and what changed in the results</div>
      <div class="actions">
        <select class="input sm" v-model="a" style="max-width:330px"><option v-for="r in done" :key="r.run_id" :value="r.run_id">A · {{ label(r) }}</option></select>
        <Icon name="arrow-right" :size="14" />
        <select class="input sm" v-model="b" style="max-width:330px"><option v-for="r in done" :key="r.run_id" :value="r.run_id">B · {{ label(r) }}</option></select>
        <Seg :model-value="view" @update:model-value="v => $emit('update:view', v)" :options="[{ value: 'history', label: 'List' }, { value: 'matrix', label: 'Matrix' }, { value: 'compare', label: 'Compare' }]" />
      </div></div>
    <div class="card-body">
      <Empty v-if="done.length < 2" icon="git-compare" title="Two successful runs needed" text="Run the pipeline, or a task, twice to compare the results." />
      <div v-else-if="err" class="out-error">{{ err }}</div>
      <template v-else-if="data">
        <div class="grid g2 mb">
          <div v-for="(r, i) in [data.a, data.b]" :key="r.run_id" class="cmp-run">
            <span class="cmp-tag" :class="i ? 'b' : 'a'">{{ i ? 'B' : 'A' }}</span>
            <div><div class="strong">Run {{ r.run_id }} <span class="muted small">· {{ scopeLabel(r.scope) }}</span></div>
              <div class="small muted">Started {{ F.dt(r.started_at) }} · {{ F.dur(r.duration) }} · {{ r.n_ok }}/{{ r.n_tasks }} tasks · {{ F.compact(r.rows_written) }} rows</div></div>
          </div>
        </div>
        <div class="grid cmp-grid">
          <div>
            <div class="eyebrow">Code changes</div>
            <div v-if="!data.code.length" class="small muted cmp-none">No notebook changed between these runs.</div>
            <div v-for="c in data.code" :key="c.task" class="cmp-diff">
              <div class="cmp-diff-head"><Icon name="file-code" :size="13" /><span class="mono">pipeline/{{ c.notebook }}</span><span class="spacer"></span>
                <span class="mono tiny" style="color:var(--ok-text)">+{{ c.added }}</span><span class="mono tiny" style="color:var(--bad-text)">−{{ c.removed }}</span></div>
              <pre class="cmp-lines"><div v-for="(l, i) in c.lines" :key="i" :class="l.kind">{{ l.text }}</div></pre>
            </div>
            <div class="eyebrow mt">Tasks</div>
            <table class="tbl compact"><thead><tr><th>Task</th><th class="r">A</th><th class="r">B</th></tr></thead>
              <tbody><tr v-for="t in data.tasks" :key="t.key"><td class="nowrap">{{ t.title }}</td>
                <td class="r nowrap"><template v-if="t.a"><StatusIcon :status="t.a.status" :size="13" /> {{ F.dur(t.a.duration) }}</template><span v-else class="muted tiny">not in run</span></td>
                <td class="r nowrap"><template v-if="t.b"><StatusIcon :status="t.b.status" :size="13" /> {{ F.dur(t.b.duration) }}</template><span v-else class="muted tiny">not in run</span></td></tr></tbody></table>
          </div>
          <div>
            <div class="eyebrow">Results after each run</div>
            <table class="tbl compact cmp-table"><thead><tr><th>Measure</th><th class="r">A</th><th class="r">B</th><th class="r">Change</th></tr></thead>
              <tbody><template v-for="g in groups" :key="g.name">
                <tr class="cmp-group"><td colspan="4">{{ g.name }}</td></tr>
                <tr v-for="r in g.rows" :key="g.name + r.label" :class="tone(r)">
                  <td>{{ r.label }}</td><td class="r mono">{{ fmt(r.a, r.fmt) }}</td><td class="r mono strong">{{ fmt(r.b, r.fmt) }}</td>
                  <td class="r mono cmp-delta">{{ r.fmt === 'text' ? (r.a !== r.b ? 'changed' : '') : fmtDelta(r.delta, r.fmt) }}</td></tr>
              </template></tbody></table>
            <div v-if="!data.has_summary.a || !data.has_summary.b" class="tiny muted mt">Results are recorded when a run finishes; runs from before this was switched on show only timings.</div>
          </div>
        </div>
      </template>
      <div v-else-if="loading" class="muted small">Loading…</div>
    </div>
  </div>`,
};
