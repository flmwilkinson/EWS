// Shared UI components
import { ICONS } from "./icons.js";
import { api, streamGet } from "./api.js";
import { store, toast } from "./store.js";
import { EChart, barChart, lineChart } from "./charts.js";
import * as F from "./format.js";

const { ref, computed, watch, onMounted, onBeforeUnmount, nextTick, h } = Vue;

export const Icon = {
  props: { name: String, size: { type: [Number, String], default: 16 }, stroke: { type: [Number, String], default: 2 } },
  template: `<svg class="icon" :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" :stroke-width="stroke"
    stroke-linecap="round" stroke-linejoin="round" v-html="paths" style="flex:none"></svg>`,
  computed: { paths() { return ICONS[this.name] || ICONS["circle-dot"]; } },
};

const STATUS_ICON = { notinrun: "circle-dot", pending: "circle-x", running: "loader-circle", succeeded: "circle-check", ok: "circle-check", failed: "circle-x",
  error: "circle-x", skipped: "circle-pause", cancelled: "circle-pause", none: "circle-dot", SUCCEEDED: "circle-check", FAILED: "circle-x",
  RUNNING: "loader-circle", CANCELLED: "circle-pause" };
export const StatusIcon = {
  components: { Icon },
  props: { status: String, size: { type: Number, default: 18 } },
  template: `<span class="st" :class="cls" :title="label"><Icon :name="icon" :size="size" :stroke="status==='failed'||status==='error' ? 2.4 : 2" /></span>`,
  computed: {
    s() { return (this.status || "pending").toLowerCase(); },
    cls() { return this.s; },
    icon() { return STATUS_ICON[this.s] || "circle-x"; },
    label() { return { pending: "Not run yet", running: "Running", succeeded: "Succeeded", ok: "Succeeded", failed: "Failed", error: "Failed",
      skipped: "Skipped", cancelled: "Cancelled", notinrun: "Not part of this run" }[this.s] || this.s; },
  },
};

export const TierBadge = {
  props: { tier: [String, Number], lg: Boolean },
  template: `<span class="tier" :class="['t' + num, { lg }]">{{ name }}</span>`,
  computed: {
    num() { return typeof this.tier === "number" ? this.tier : F.TIER_NUM[this.tier] ?? 0; },
    name() { return typeof this.tier === "number" ? F.TIERS[this.tier] : this.tier; },
  },
};

export const Band = { props: { band: String }, template: `<span class="band" :class="'b' + band" :title="'Risk band ' + band">{{ band || '–' }}</span>` };

export const Kpi = {
  components: { Icon },
  props: { label: String, value: [String, Number], sub: String, icon: String, color: String, delta: Number, deltaGoodWhen: { type: String, default: "down" },
    deltaFmt: { type: String, default: "int" }, flashKey: [String, Number], small: Boolean },
  template: `<div class="kpi" :class="{ flash: flashing }"><i v-if="color" class="bar" :style="{ background: color }"></i>
    <div class="label"><Icon v-if="icon" :name="icon" :size="14" />{{ label }}</div>
    <div class="value" :class="{ sm: small }" :title="String(value ?? '')">{{ value }}</div>
    <div class="sub" v-if="sub || delta !== undefined">
      <span v-if="delta !== undefined && delta !== null" :class="deltaCls">{{ deltaText }}</span><span v-if="sub">{{ sub }}</span></div></div>`,
  data: () => ({ flashing: false }),
  computed: {
    deltaCls() { if (!this.delta) return "delta-flat"; const bad = this.deltaGoodWhen === "down" ? this.delta > 0 : this.delta < 0; return bad ? "delta-up" : "delta-down"; },
    deltaText() { const d = this.delta || 0; const s = this.deltaFmt === "pct" ? F.pct(Math.abs(d), 1) : F.n(Math.abs(d)); return d === 0 ? "no change" : (d > 0 ? "▲ " : "▼ ") + s; },
  },
  watch: { flashKey(a, b) { if (b !== undefined && a !== b) { this.flashing = false; requestAnimationFrame(() => { this.flashing = true; setTimeout(() => (this.flashing = false), 1300); }); } } },
};

export const Card = {
  props: { title: String, sub: String, flush: Boolean, pad: Boolean },
  template: `<div class="card"><div class="card-head" v-if="title || $slots.actions"><div><h3>{{ title }}</h3><div class="sub" v-if="sub">{{ sub }}</div></div>
    <div class="actions"><slot name="actions" /></div></div><div :class="flush ? 'card-body flush' : 'card-body'"><slot /></div></div>`,
};

export const Tabs = {
  components: { Icon },
  props: { tabs: Array, modelValue: String, inCard: Boolean },
  emits: ["update:modelValue"],
  template: `<div class="tabs" :class="{ 'in-card': inCard }"><button v-for="t in tabs" :key="t.key" :class="{ on: modelValue === t.key }" @click="$emit('update:modelValue', t.key)">
    <Icon v-if="t.icon" :name="t.icon" :size="14" />{{ t.label }}<span v-if="t.count !== undefined" class="chip" style="height:18px">{{ t.count }}</span></button></div>`,
};

export const Seg = {
  props: { options: Array, modelValue: [String, Number] },
  emits: ["update:modelValue"],
  template: `<div class="seg"><button v-for="o in options" :key="o.value" :class="{ on: modelValue === o.value }" @click="$emit('update:modelValue', o.value)">{{ o.label }}</button></div>`,
};

export const DataTable = {
  props: { columns: Array, rows: Array, rowKey: { type: String, default: "id" }, clickable: Boolean, selected: [String, Number], maxHeight: [String, Number],
    sortable: { type: Boolean, default: true }, compact: Boolean, mono: Boolean, externalSort: Object, empty: { type: String, default: "No rows" } },
  emits: ["row-click", "sort"],
  data() { return { sortKey: null, sortDesc: true }; },
  computed: {
    sorted() {
      if (this.externalSort || !this.sortKey) return this.rows || [];
      const k = this.sortKey, d = this.sortDesc ? -1 : 1;
      return [...(this.rows || [])].sort((a, b) => {
        const x = a[k], y = b[k];
        if (x === y) return 0; if (x === null || x === undefined) return 1; if (y === null || y === undefined) return -1;
        return (x > y ? 1 : -1) * d;
      });
    },
    curKey() { return this.externalSort ? this.externalSort.key : this.sortKey; },
    curDesc() { return this.externalSort ? this.externalSort.desc : this.sortDesc; },
  },
  methods: {
    sort(c) {
      if (!this.sortable || c.sortable === false) return;
      if (this.externalSort) { this.$emit("sort", { key: c.key, desc: this.externalSort.key === c.key ? !this.externalSort.desc : true }); return; }
      if (this.sortKey === c.key) this.sortDesc = !this.sortDesc; else { this.sortKey = c.key; this.sortDesc = true; }
    },
    show(c, r) { const v = r[c.key]; return c.fmt ? c.fmt(v, r) : (v === null || v === undefined || v === "" ? "–" : v); },
  },
  template: `<div class="tbl-wrap" :style="maxHeight ? { maxHeight: typeof maxHeight === 'number' ? maxHeight + 'px' : maxHeight } : null">
    <table class="tbl" :class="{ compact, mono }"><thead><tr>
      <th v-for="c in columns" :key="c.key" :class="[c.align, { sortable: sortable && c.sortable !== false }]" :style="c.width ? { width: c.width } : null" @click="sort(c)" :title="c.title">
        {{ c.label }}<span v-if="curKey === c.key">{{ curDesc ? ' ↓' : ' ↑' }}</span></th></tr></thead>
    <tbody><tr v-for="(r, i) in sorted" :key="r[rowKey] ?? i" :class="{ click: clickable, sel: selected !== undefined && r[rowKey] === selected }" @click="clickable && $emit('row-click', r)">
      <td v-for="c in columns" :key="c.key" :class="[c.align, c.cls]"><slot :name="'cell-' + c.key" :row="r" :value="r[c.key]">{{ show(c, r) }}</slot></td></tr>
      <tr v-if="!sorted.length"><td :colspan="columns.length" class="muted" style="text-align:center;padding:22px">{{ empty }}</td></tr>
    </tbody></table></div>`,
};

export const Markdown = { props: { text: String }, template: `<div class="md" v-html="html"></div>`, computed: { html() { return F.md(this.text); } } };

export const Empty = {
  components: { Icon },
  props: { icon: { type: String, default: "database" }, title: String, text: String },
  template: `<div class="empty"><div class="icon-wrap"><Icon :name="icon" :size="24" /></div><h3>{{ title }}</h3><p>{{ text }}</p><slot /></div>`,
};

export const NeedPipeline = {
  components: { Empty, Icon },
  props: { what: { type: String, default: "this page" } },
  template: `<Empty icon="workflow" title="Nothing here yet" :text="'Run the EWS pipeline to build ' + what + '. Every task writes its tables to the lakehouse as it finishes.'">
    <a class="btn primary" href="#/workflows"><Icon name="play" :size="14" />Open Workflows</a></Empty>`,
};

export const Drawer = {
  components: { Icon },
  props: { open: Boolean, title: String, wide: Boolean },
  emits: ["close"],
  template: `<teleport to="body"><template v-if="open"><div class="drawer-mask" @click="$emit('close')"></div>
    <div class="drawer" :class="{ wide }"><div class="drawer-head"><h3>{{ title }}</h3><slot name="head" /><span class="spacer"></span>
    <button class="icon-btn" @click="$emit('close')"><Icon name="x" /></button></div><div class="drawer-body"><slot /></div></div></template></teleport>`,
};

export const JsonView = { props: { value: null }, template: `<pre class="json" v-html="html"></pre>`, computed: { html() { return F.jsonHtml(this.value); } } };

export const Spark = {
  props: { data: Array, width: { type: Number, default: 90 }, height: { type: Number, default: 24 }, color: { type: String, default: "var(--accent)" }, bars: Boolean },
  computed: {
    pts() {
      const d = (this.data || []).map((v) => (typeof v === "number" ? v : 0));
      if (d.length < 2) return "";
      const mx = Math.max(...d), mn = Math.min(...d, 0), r = mx - mn || 1;
      return d.map((v, i) => `${(i / (d.length - 1)) * this.width},${this.height - 2 - ((v - mn) / r) * (this.height - 4)}`).join(" ");
    },
    rects() {
      const d = (this.data || []).map((v) => (typeof v === "number" ? v : 0));
      const mx = Math.max(...d, 1), w = this.width / Math.max(d.length, 1);
      return d.map((v, i) => ({ x: i * w + 0.5, w: Math.max(w - 1.5, 1), h: Math.max((v / mx) * (this.height - 2), v ? 1.5 : 0) }));
    },
  },
  template: `<svg :width="width" :height="height" style="display:block;overflow:visible">
    <template v-if="bars"><rect v-for="(r, i) in rects" :key="i" :x="r.x" :y="height - r.h" :width="r.w" :height="r.h" rx="1" :fill="color" /></template>
    <polyline v-else-if="pts" :points="pts" fill="none" :stroke="color" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" /></svg>`,
};

export const PBar = {
  props: { value: Number, max: { type: Number, default: 1 }, color: String },
  template: `<div class="pbar"><i :style="{ width: Math.min(100, Math.max(0, (value || 0) / (max || 1) * 100)) + '%', background: color || null }"></i></div>`,
};

// ---------------------------------------------------------------------------
// AI panel: stored text plus live regeneration, prompt transparency, model footer
// ---------------------------------------------------------------------------
export const AiPanel = {
  components: { Icon, Drawer, Markdown },
  props: { kind: String, id: String, title: { type: String, default: "AI commentary" }, stored: Object, auto: Boolean, compact: Boolean,
    subtitle: String },
  setup(props) {
    const text = ref(props.stored?.commentary || props.stored?.summary || "");
    const streaming = ref(false), meta = ref(null), err = ref(null);
    const showPrompt = ref(false), prompt = ref(null);
    let stop = null;
    const source = ref(props.stored ? props.stored.source : null);
    function run() {
      stop?.(); text.value = ""; streaming.value = true; err.value = null; meta.value = null;
      const q = new URLSearchParams({ kind: props.kind }); if (props.id) q.set("id", props.id);
      stop = streamGet(`/api/ai/stream?${q}`, (ev) => {
        if (ev.type === "delta") text.value = ev.replace ? ev.text : text.value + ev.text;
        else if (ev.type === "done") { streaming.value = false; meta.value = ev; source.value = ev.source; if (ev.text) text.value = ev.text; }
        else if (ev.type === "error") { streaming.value = false; err.value = ev.message; }
      });
    }
    async function openPrompt() {
      showPrompt.value = true; prompt.value = null;
      const q = new URLSearchParams({ kind: props.kind }); if (props.id) q.set("id", props.id);
      try { prompt.value = await api.get(`/api/ai/prompt?${q}`); } catch (e) { prompt.value = { error: e.message }; }
    }
    function copy() { navigator.clipboard?.writeText(text.value); toast("Copied to clipboard", "ok", 1800); }
    watch(() => props.stored, (s) => { if (!streaming.value && s) { text.value = s.commentary || s.summary || ""; source.value = s.source; } });
    watch(() => props.id, () => { if (props.auto) run(); });
    onMounted(() => { if (props.auto || (!text.value && props.kind)) run(); });
    onBeforeUnmount(() => stop?.());
    const footer = computed(() => {
      if (streaming.value) return `Writing with ${store.health?.llm?.model || "Claude"}…`;
      if (meta.value?.source === "llm") return `${meta.value.model} · ${(meta.value.latency_ms / 1000).toFixed(1)}s · ${meta.value.output_tokens} tokens · just now`;
      if (meta.value?.source === "template" || source.value === "template") return "Template text: Claude was not available";
      if (props.stored) {
        const c = props.stored.call;
        const when = props.stored.created_at ? F.ago(props.stored.created_at) : "";
        return `Written by ${props.stored.model || c?.served_by || c?.model || "Claude"} during the pipeline run ${when ? "· " + when : ""}`;
      }
      return "";
    });
    return { text, streaming, meta, err, run, openPrompt, showPrompt, prompt, copy, footer, source };
  },
  template: `<div class="ai-panel"><div class="ai-head"><span class="ai-mark"><Icon name="sparkles" :size="14" /></span>
      <div><h3>{{ title }}</h3><div class="small muted" v-if="subtitle">{{ subtitle }}</div></div><span class="spacer"></span>
      <button class="btn xs ghost" @click="openPrompt" title="See exactly what the model was given"><Icon name="eye" :size="13" />Prompt</button>
      <button class="btn xs ghost" @click="copy" :disabled="!text"><Icon name="copy" :size="13" /></button>
      <button class="btn xs ai" @click="run" :disabled="streaming"><Icon :name="streaming ? 'loader-circle' : 'refresh-cw'" :size="13" />{{ text ? 'Regenerate' : 'Generate' }}</button></div>
    <div class="ai-body" :class="{ caret: streaming }">
      <div v-if="!text && streaming"><div class="skeleton" style="width:92%"></div><div class="skeleton" style="width:80%"></div><div class="skeleton" style="width:64%"></div></div>
      <Markdown v-else-if="text" :text="text" /><div v-else class="muted">No commentary yet.</div>
      <div v-if="err" class="small" style="color:var(--bad-text)">{{ err }}</div></div>
    <div class="ai-foot"><Icon name="bot" :size="13" /><span>{{ footer }}</span><span class="spacer"></span><span>Facts computed in Python; the model writes only from them</span></div>
    <Drawer :open="showPrompt" title="What the model was given" @close="showPrompt=false" wide>
      <div v-if="!prompt" class="skeleton"></div>
      <template v-else-if="prompt.error"><p class="muted">{{ prompt.error }}</p></template>
      <template v-else><div class="row small muted mb"><span class="chip violet">{{ prompt.model }}</span><span>purpose: {{ prompt.purpose }}</span></div>
        <label class="lbl">System prompt</label><pre class="json" style="white-space:pre-wrap">{{ prompt.system }}</pre>
        <label class="lbl mt">User prompt (task and structured facts)</label><pre class="json" style="white-space:pre-wrap">{{ prompt.prompt }}</pre>
        <label class="lbl mt">Fallback if the model is unavailable</label><pre class="json" style="white-space:pre-wrap">{{ prompt.fallback }}</pre></template>
    </Drawer></div>`,
};

// ---------------------------------------------------------------------------
// CodeMirror wrapper
// ---------------------------------------------------------------------------
export const CodeEditor = {
  props: { modelValue: String, mode: { type: String, default: "python" }, readOnly: Boolean, firstLine: { type: Number, default: 1 },
    full: Boolean, placeholder: String, lineNumbers: { type: Boolean, default: true } },
  emits: ["update:modelValue", "save", "run", "blur", "focus"],
  template: `<div class="cell-cm" :class="{ full }" ref="el"></div>`,
  mounted() {
    this.cm = CodeMirror(this.$refs.el, {
      value: this.modelValue || "", mode: this.mode === "sql" ? "text/x-sql" : this.mode, theme: "ews", lineNumbers: this.lineNumbers,
      firstLineNumber: this.firstLine, readOnly: this.readOnly, viewportMargin: this.full ? 10 : Infinity, indentUnit: 4,
      matchBrackets: true, styleActiveLine: true, lineWrapping: this.mode === "markdown", placeholder: this.placeholder,
      extraKeys: { "Ctrl-S": () => this.$emit("save"), "Cmd-S": () => this.$emit("save"), "Shift-Enter": () => this.$emit("run"),
        "Ctrl-Enter": () => this.$emit("run"), "Ctrl-/": "toggleComment", Tab: (cm) => cm.execCommand(cm.somethingSelected() ? "indentMore" : "insertSoftTab") },
    });
    this.cm.on("change", () => { const v = this.cm.getValue(); if (v !== this.modelValue) this.$emit("update:modelValue", v); });
    this.cm.on("blur", () => this.$emit("blur"));
    this.cm.on("focus", () => this.$emit("focus"));
  },
  watch: {
    modelValue(v) { if (this.cm && v !== this.cm.getValue()) { const c = this.cm.getCursor(); this.cm.setValue(v || ""); this.cm.setCursor(c); } },
    firstLine(v) { this.cm?.setOption("firstLineNumber", v); },
    readOnly(v) { this.cm?.setOption("readOnly", v); },
  },
  methods: { focus() { this.cm?.focus(); }, refresh() { this.cm?.refresh(); } },
};

// ---------------------------------------------------------------------------
// Notebook cell outputs
// ---------------------------------------------------------------------------
export const OutTable = {
  props: { data: Object, title: String },
  template: `<div><div class="out-title" v-if="title">{{ title }} <span class="muted small" style="font-weight:400">· {{ data.total_rows.toLocaleString() }} rows<span v-if="data.total_rows > data.rows.length">, first {{ data.rows.length }} shown</span></span></div>
    <div class="out-table"><table class="tbl compact"><thead><tr><th v-for="c in data.columns" :key="c">{{ c }}</th></tr></thead>
    <tbody><tr v-for="(r, i) in data.rows" :key="i"><td v-for="(v, j) in r" :key="j" :class="typeof v === 'number' ? 'r' : ''" :title="String(v ?? '')">{{ cell(v) }}</td></tr></tbody></table></div></div>`,
  methods: { cell(v) { if (v === null || v === undefined) return "null"; if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-GB") : (Math.abs(v) < 1000 ? +v.toFixed(4) : v.toLocaleString("en-GB", { maximumFractionDigits: 1 })); if (typeof v === "boolean") return v ? "true" : "false"; return v; } },
};

export const OutChart = {
  components: { EChart },
  props: { data: Object, title: String },
  computed: {
    option() {
      const d = this.data, y = { pct: "pct", int: "int", dec2: "dec2" }[d.y_format] || "none";
      const series = d.series.map((s) => ({ name: s.name, data: s.data }));
      if (d.kind === "line") return lineChart({ x: d.x, series, y, showSymbol: d.x.length < 24 });
      return barChart({ x: d.x, series, y, horizontal: d.horizontal, legend: series.length > 1 });
    },
    height() { return this.data.horizontal ? Math.max(this.data.height || 260, this.data.x.length * 22 + 30) : this.data.height || 260; },
  },
  template: `<div><div class="out-title" v-if="title">{{ title }}</div><EChart :option="option" :height="height" /></div>`,
};

export const CellOutputs = {
  components: { OutTable, OutChart, JsonView, Markdown, Icon, PBar },
  props: { outputs: Array },
  computed: {
    groups() {
      const out = [];
      for (const o of this.outputs || []) {
        if (["table_written", "llm", "model"].includes(o.kind)) {
          const last = out[out.length - 1];
          if (last && last.kind === "chips") last.items.push(o); else out.push({ kind: "chips", items: [o] });
        } else out.push(o);
      }
      return out;
    },
  },
  template: `<div class="outputs" v-if="groups.length"><template v-for="(o, i) in groups" :key="i">
    <pre v-if="o.kind === 'stream'" class="out-stream" :class="o.name">{{ o.text.replace(/\\n$/, '') }}</pre>
    <pre v-else-if="o.kind === 'error'" class="out-error">{{ o.traceback }}</pre>
    <OutTable v-else-if="o.kind === 'table'" :data="o.data" :title="o.title" />
    <OutChart v-else-if="o.kind === 'chart'" :data="o.data" :title="o.title" />
    <div v-else-if="o.kind === 'metrics'"><div class="out-title" v-if="o.title">{{ o.title }}</div>
      <div class="out-metrics"><div v-for="m in o.data" :key="m.label"><div class="l">{{ m.label }}</div><div class="v">{{ m.value }}</div></div></div></div>
    <div v-else-if="o.kind === 'kv'"><div class="out-title" v-if="o.title">{{ o.title }}</div><JsonView :value="o.data" /></div>
    <Markdown v-else-if="o.kind === 'markdown'" :text="o.data" />
    <div v-else-if="o.kind === 'progress'" class="out-progress"><span>{{ o.label }}</span><PBar :value="o.done" :max="o.total" /><span class="mono">{{ o.done }}/{{ o.total }}</span></div>
    <div v-else-if="o.kind === 'chips'" class="out-chips"><template v-for="(c, j) in o.items" :key="j">
      <a v-if="c.kind === 'table_written'" class="chip blue" :href="'#/catalog/' + c.table"><Icon name="table" :size="12" />{{ c.table }} v{{ c.version }} · {{ c.rows.toLocaleString() }} rows</a>
      <span v-else-if="c.kind === 'llm'" class="chip violet"><Icon name="sparkles" :size="12" />{{ c.purpose }} · {{ c.model }} · {{ c.status === 'ok' ? ((c.latency_ms/1000).toFixed(1) + 's · ' + c.output_tokens + ' tok') : c.status }}</span>
      <span v-else-if="c.kind === 'model'" class="chip" :class="c.stage === 'Champion' ? 'green' : ''"><Icon name="package" :size="12" />{{ c.name }} v{{ c.version }} · {{ c.stage }}</span>
    </template></div>
  </template></div>`,
};

export const RagBadge = { props: { status: String }, template: `<span class="rag" :class="status"><i></i>{{ status }}</span>` };

export const Avatar = {
  props: { name: String, lg: Boolean },
  template: `<span class="avatar" :class="{ lg }" :style="{ background: color }">{{ ini }}</span>`,
  computed: { ini() { return F.initials(this.name); }, color() { return F.avatarColor(this.name); } },
};

export const all = { Icon, StatusIcon, TierBadge, Band, Kpi, Card, Tabs, Seg, DataTable, Markdown, Empty, NeedPipeline, Drawer, JsonView, Spark, PBar,
  AiPanel, CodeEditor, CellOutputs, OutTable, OutChart, RagBadge, Avatar, EChart };
