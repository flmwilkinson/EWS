// Workspace: file tree, notebook editor with live cell execution, module editor, revisions
import { api, subscribe } from "../api.js";
import { store, toast } from "../store.js";
import { applyEvent } from "../runstate.js";
import { route, go } from "../router.js";
import * as C from "../components.js";
import * as F from "../format.js";

const { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } = Vue;

const MdCell = {
  components: C.all,
  props: { cell: Object },
  data: () => ({ editing: false }),
  methods: { edit() { this.editing = true; this.$nextTick(() => this.$refs.ed?.focus()); } },
  template: `<div class="cell-box"><div v-if="!editing" class="cell-md" @dblclick="edit" title="Double-click to edit"><Markdown :text="cell.source" /></div>
    <CodeEditor v-else ref="ed" v-model="cell.source" mode="markdown" :line-numbers="false" @blur="editing = false" @save="$emit('save')" /></div>`,
};

export default {
  components: { ...C.all, MdCell },
  setup() {
    const tree = ref(null);
    const file = ref(null);          // loaded file
    const cells = ref([]);           // editable cells
    const content = ref("");         // module source
    const original = ref("");
    const run = reactive({ id: null, status: null, task: null, at: null }); // the run whose outputs are shown
    const showRev = ref(false), revs = ref([]), revSel = ref(null);
    const saving = ref(false);
    const path = computed(() => route.params.join("/"));

    async function loadTree() { tree.value = await api.get("/api/workspace/tree"); }
    async function open(p) {
      if (!p) return;
      try { file.value = await api.get(`/api/workspace/file?path=${encodeURIComponent(p)}`); }
      catch (e) { toast(e.message, "bad"); return; }
      cells.value = (file.value.cells || []).map((c) => ({ ...c }));
      content.value = file.value.content;
      original.value = serial();
      run.id = null; run.task = null;
      if (file.value.task) {
        const latest = await api.get(`/api/tasks/${file.value.task}/latest`);
        if (latest.task) { run.id = latest.run_id; run.task = latest.task; run.status = latest.task.status; run.at = latest.task.started_at; }
      }
    }
    const serial = () => (file.value?.kind === "notebook" ? JSON.stringify(cells.value.map((c) => [c.type, c.title || "", c.source])) : content.value);
    const dirty = computed(() => file.value && serial() !== original.value);

    async function save() {
      if (!file.value || !dirty.value) return true;
      saving.value = true;
      try {
        const body = file.value.kind === "notebook" ? { path: file.value.path, cells: cells.value.map(({ type, title, source }) => ({ type, title, source })) }
          : { path: file.value.path, content: content.value };
        const f = await api.put("/api/workspace/file", body);
        file.value = f;
        if (f.cells) { f.cells.forEach((c, i) => { if (cells.value[i]) cells.value[i].start_line = c.start_line; }); }
        original.value = serial();
        toast(`Saved ${f.name}`, "ok", 2000);
        loadTree();
        return true;
      } catch (e) { toast(e.message, "bad", 7000); return false; }
      finally { saving.value = false; }
    }
    async function runNb(scope) {
      if (!(await save())) return;
      try {
        const r = await api.post("/api/runs", { scope, task: file.value.task });
        run.id = r.run_id; run.status = "running"; run.at = Date.now() / 1000;
        run.task = { key: file.value.task, status: "pending", cells: cells.value.map((c, i) => ({ index: i, type: c.type, status: c.type === "code" ? "pending" : null, outputs: [] })) };
        toast(scope === "only" ? "Running notebook as a job" : "Running this notebook and everything downstream", "info");
      } catch (e) { toast(e.message, "bad"); }
    }
    const unsub = subscribe("runs", (ev) => {
      if (!run.id || ev.run_id !== run.id || !file.value) return;
      if (ev.type === "cell" && ev.task_key === file.value.task && run.task) applyEvent(run.task, ev.ev);
      if (ev.type === "task" && ev.task.key === file.value.task) { run.status = ev.task.status; if (run.task) run.task.status = ev.task.status; }
    });
    onBeforeUnmount(unsub);

    const outCell = (i) => run.task?.cells?.[i];
    const stale = computed(() => run.task?.cells && run.task.cells.length !== cells.value.length);

    function addCell(i, type) { cells.value.splice(i + 1, 0, { type, title: type === "code" ? "New cell" : null, source: type === "code" ? "" : "New text", start_line: 0, index: -1 }); }
    function delCell(i) { cells.value.splice(i, 1); }
    async function openRevs() { showRev.value = true; revSel.value = null; revs.value = await api.get(`/api/workspace/revisions?path=${encodeURIComponent(file.value.path)}`); }
    async function viewRev(r) { revSel.value = await api.get(`/api/workspace/revision/${r.id}`); }
    async function restore() {
      const f = await api.post(`/api/workspace/revert/${revSel.value.id}`);
      toast("Revision restored (the current version was kept as a revision)", "ok"); showRev.value = false; open(f.path);
    }
    const keyH = (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); save(); } };
    onMounted(async () => {
      window.addEventListener("keydown", keyH);
      await loadTree();
      if (!path.value) { const first = tree.value.folders[0]?.files[0]; if (first) go(`workspace/${first.path}`); }
      else open(path.value);
    });
    onBeforeUnmount(() => window.removeEventListener("keydown", keyH));
    watch(path, (p) => { if (p) open(p); });
    const taskMeta = computed(() => store.pipeline?.tasks.find((t) => t.key === file.value?.task));
    const busy = computed(() => !!store.activeRunId);
    const firstLine = (c) => (c.start_line || 0) + 1;
    return { tree, file, cells, content, dirty, save, runNb, run, outCell, stale, addCell, delCell, showRev, revs, revSel, openRevs, viewRev, restore,
      saving, path, taskMeta, busy, F, firstLine, store };
  },
  template: `<div class="ws">
  <aside class="ws-tree">
    <template v-for="f in tree?.folders || []" :key="f.name">
      <div class="folder"><Icon name="folder-open" :size="13" />{{ f.name }}</div>
      <a v-for="x in f.files" :key="x.path" class="ws-file" :class="{ active: path === x.path }" :href="'#/workspace/' + x.path">
        <StatusIcon v-if="x.task" :status="x.last_status || 'pending'" :size="15" /><Icon v-else name="file-code" :size="14" />
        <span class="mono ellipsis grow">{{ x.name }}</span></a>
    </template>
  </aside>
  <section class="ws-main" v-if="file">
    <div class="nb-head">
      <Icon :name="file.kind === 'notebook' ? 'file-code' : 'code'" :size="16" />
      <div class="grow" style="min-width:0"><div class="file ellipsis">{{ file.path }} <span v-if="dirty" style="color:var(--warn-text)">●</span></div>
        <div class="small muted ellipsis"><template v-if="taskMeta">Task: <b>{{ taskMeta.title }}</b> · {{ taskMeta.desc }}</template><template v-else>Library module used by the notebooks</template></div></div>
      <span v-if="run.id" class="chip" :title="'Outputs shown from run ' + run.id"><StatusIcon :status="run.status" :size="13" />run {{ run.id }}</span>
      <button class="btn sm" @click="openRevs"><Icon name="history" :size="13" />Revisions <span class="muted">{{ file.revisions }}</span></button>
      <button class="btn sm" @click="save" :disabled="!dirty || saving"><Icon name="save" :size="13" />Save</button>
      <template v-if="file.task"><button class="btn sm" @click="runNb('from')" :disabled="busy" title="Run this notebook, then every task downstream of it"><Icon name="skip-forward" :size="13" />Run from here</button>
        <button class="btn sm primary" @click="runNb('only')" :disabled="busy"><Icon name="play" :size="13" />Run notebook</button></template>
    </div>
    <div v-if="file.kind === 'notebook'" class="nb">
      <div v-if="stale" class="story mb"><Icon name="info" :size="15" />The notebook has changed since run {{ run.id }}; outputs are matched to cells by position. Run it again to refresh them.</div>
      <div v-for="(c, i) in cells" :key="i + ':' + c.type" class="cell">
        <div class="cell-gutter">
          <template v-if="c.type === 'code'"><StatusIcon :status="outCell(i)?.status || (run.id ? 'pending' : 'pending')" />
            <span class="dur" v-if="outCell(i)?.duration !== undefined && outCell(i)?.duration !== null">{{ F.dur(outCell(i).duration) }}</span></template>
          <Icon v-else name="file-text" :size="14" style="color:var(--muted);margin-top:6px" />
        </div>
        <div>
          <MdCell v-if="c.type === 'markdown'" :cell="c" @save="save" />
          <div v-else class="cell-box" :class="{ running: outCell(i)?.status === 'running', error: outCell(i)?.status === 'error' }">
            <div class="cell-title"><Icon name="code" :size="12" /><input v-model="c.title" class="mono" placeholder="Cell title" style="border:0;background:none;outline:none;font-size:12px;font-weight:600;color:var(--text-2);flex:1;min-width:0" />
              <span class="tiny muted">line {{ firstLine(c) }}</span>
              <button class="btn xs ghost" @click="addCell(i, 'code')" title="Add a code cell below"><Icon name="plus" :size="12" />Code</button>
              <button class="btn xs ghost" @click="addCell(i, 'markdown')" title="Add a text cell below"><Icon name="plus" :size="12" />Text</button>
              <button class="btn xs ghost" @click="delCell(i)" title="Delete cell"><Icon name="x" :size="12" /></button></div>
            <CodeEditor v-model="c.source" :first-line="firstLine(c)" @save="save" />
            <CellOutputs v-if="outCell(i)?.outputs?.length" :outputs="outCell(i).outputs" />
          </div>
        </div>
      </div>
    </div>
    <div v-else style="padding:0"><CodeEditor v-model="content" full @save="save" /></div>
  </section>
  <Empty v-else icon="folder-code" title="Open a notebook" text="Pick a notebook or module on the left." style="margin:auto" />

  <Drawer :open="showRev" title="Revision history" @close="showRev = false" wide>
    <div class="grid g-1-2">
      <div><div v-if="!revs.length" class="muted">No revisions yet. Every save keeps the previous version here.</div>
        <div v-for="r in revs" :key="r.id" class="ws-file" :class="{ active: revSel?.id === r.id }" @click="viewRev(r)">
          <Icon name="git-commit-horizontal" :size="14" /><div class="grow"><div class="small strong">{{ F.dt(r.saved_at) }}</div><div class="tiny muted">{{ r.note }}</div></div></div></div>
      <div v-if="revSel"><div class="row mb"><b class="small">Version saved {{ F.dt(revSel.saved_at) }}</b><span class="spacer"></span>
        <button class="btn sm primary" @click="restore"><Icon name="undo-2" :size="13" />Restore this version</button></div>
        <CodeEditor :model-value="revSel.content" read-only /></div>
    </div>
  </Drawer>
</div>`,
};
