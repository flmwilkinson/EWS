// Early Warning Studio: shell, router and boot
import { store, boot, setTheme } from "./store.js";
import { api } from "./api.js";
import * as C from "./components.js";
import * as F from "./format.js";
import { route, go, parse, KNOWN } from "./router.js";

import Workflows from "./pages/workflows.js";
import Workspace from "./pages/workspace.js";
import Catalog from "./pages/catalog.js";
import Sql from "./pages/sql.js";
import Models from "./pages/models.js";
import Connectors from "./pages/connectors.js";
import Apis from "./pages/apis.js";
import Overview from "./pages/overview.js";
import Live from "./pages/live.js";
import Watchlist from "./pages/watchlist.js";
import Customer from "./pages/customer.js";
import Triggers from "./pages/triggers.js";
import Decisioning from "./pages/decisioning.js";
import DataQuality from "./pages/dq.js";
import Monitoring from "./pages/monitoring.js";
import Ask from "./pages/ask.js";
import AiLog from "./pages/ailog.js";

const { createApp, reactive, computed } = Vue;

export const NAV = [
  { section: "Platform" },
  { key: "workflows", label: "Workflows", icon: "workflow", page: Workflows },
  { key: "workspace", label: "Workspace", icon: "folder-code", page: Workspace, flush: true },
  { key: "catalog", label: "Catalog", icon: "database", page: Catalog },
  { key: "sql", label: "SQL editor", icon: "square-terminal", page: Sql },
  { key: "models", label: "Models", icon: "brain-circuit", page: Models },
  { key: "connectors", label: "Connectors", icon: "cable", page: Connectors },
  { key: "apis", label: "Real-time APIs", icon: "webhook", page: Apis },
  { section: "Early warning" },
  { key: "overview", label: "Portfolio overview", icon: "layout-dashboard", page: Overview },
  { key: "live", label: "Live monitor", icon: "activity", page: Live },
  { key: "watchlist", label: "Watchlist", icon: "list-checks", page: Watchlist },
  { key: "triggers", label: "Triggers and KRIs", icon: "siren", page: Triggers },
  { key: "decisioning", label: "Decisioning", icon: "split", page: Decisioning },
  { key: "dq", label: "Data quality", icon: "shield-check", page: DataQuality },
  { key: "monitoring", label: "Model monitoring", icon: "gauge", page: Monitoring },
  { section: "AI" },
  { key: "ask", label: "Ask the EWS", icon: "message-square-text", page: Ask },
  { key: "ai-log", label: "AI audit log", icon: "scan-search", page: AiLog },
];
const PAGES = Object.fromEntries(NAV.filter((n) => n.key).map((n) => [n.key, n]));
PAGES.customer = { key: "customer", label: "Customer 360", icon: "user", page: Customer };

Object.keys(PAGES).forEach((k) => KNOWN.add(k));
parse();
// every page opens at the top
let lastPage = route.name;
window.addEventListener("hashchange", () => {
  if (route.name !== lastPage) { lastPage = route.name; requestAnimationFrame(() => { const c = document.querySelector(".content"); if (c) c.scrollTop = 0; }); }
});

const Search = {
  components: { Icon: C.Icon, TierBadge: C.TierBadge },
  data: () => ({ q: "", results: [], open: false, sel: 0, t: null }),
  methods: {
    input() {
      clearTimeout(this.t);
      if (!this.q.trim()) { this.results = []; return; }
      this.t = setTimeout(async () => {
        try { const r = await api.get(`/api/ews/watchlist?tier=&q=${encodeURIComponent(this.q.trim())}&limit=8&sort=pd_6m`); this.results = r.rows; this.sel = 0; this.open = true; }
        catch (e) { this.results = []; }
      }, 160);
    },
    pick(r) { this.open = false; this.q = ""; go(`customer/${r.customer_id}`); },
    blur() { setTimeout(() => { this.open = false; }, 150); },
    key(e) {
      if (e.key === "ArrowDown") { this.sel = Math.min(this.sel + 1, this.results.length - 1); e.preventDefault(); }
      else if (e.key === "ArrowUp") { this.sel = Math.max(this.sel - 1, 0); e.preventDefault(); }
      else if (e.key === "Enter" && this.results[this.sel]) this.pick(this.results[this.sel]);
      else if (e.key === "Escape") this.open = false;
    },
  },
  template: `<div class="search"><Icon name="search" :size="15" />
    <input v-model="q" @input="input" @keydown="key" @focus="open = results.length > 0" @blur="blur" placeholder="Find a customer: ID or name (Priya, Dan…)" />
    <div class="search-results" v-if="open && results.length">
      <div v-for="(r, i) in results" :key="r.customer_id" :class="{ sel: i === sel }" @mousedown.prevent="pick(r)">
        <span class="mono small">{{ r.customer_id }}</span><b v-if="r.persona">{{ r.persona }}</b><span class="spacer"></span><TierBadge :tier="r.tier" /></div></div></div>`,
};

const App = {
  components: { ...C.all, Search },
  setup() {
    const page = computed(() => PAGES[route.name]);
    const activeRun = computed(() => (store.activeRunId ? store.runs[store.activeRunId] : null));
    const runProgress = computed(() => {
      const r = activeRun.value; if (!r) return "";
      const ts = Object.values(r.task_state || {});
      return `${ts.filter((t) => t.status === "succeeded").length}/${ts.length}`;
    });
    const liveOn = computed(() => store.live?.status === "running");
    return { store, route, NAV, page, activeRun, runProgress, liveOn, F, go,
      toggleTheme: () => setTheme(store.theme === "dark" ? "light" : "dark") };
  },
  template: `<div class="shell">
    <nav class="nav">
      <div class="brand"><div class="brand-mark"><Icon name="activity" :size="18" :stroke="2.4" /></div>
        <div><div class="brand-name">Early Warning Studio</div><div class="brand-sub">Credit card early warning</div></div></div>
      <div class="nav-scroll">
        <template v-for="(n, i) in NAV" :key="i">
          <div v-if="n.section" class="nav-section">{{ n.section }}</div>
          <a v-else class="nav-item" :class="{ active: route.name === n.key || (n.key === 'watchlist' && route.name === 'customer') }" :href="'#/' + n.key">
            <Icon :name="n.icon" :size="16" />{{ n.label }}
            <span v-if="n.key === 'workflows' && activeRun" class="nav-badge">{{ runProgress }}</span>
            <span v-if="n.key === 'live' && liveOn" class="nav-badge live">LIVE</span></a>
        </template>
      </div>
      <div class="nav-foot">
        <div class="kv"><span>Compute</span><span>Python 3.11 runtime</span></div>
        <div class="kv"><span>Lakehouse</span><span>Parquet + DuckDB</span></div>
        <div class="kv"><span>Champion</span><span>{{ store.live?.champion || '–' }}</span></div>
        <div class="kv"><span>AI</span><span><span class="dot" :class="store.health?.llm?.configured ? 'ok' : 'bad'" style="margin-right:5px"></span>{{ store.health?.llm?.configured ? 'Connected' : 'Not configured' }}</span></div>
      </div>
    </nav>
    <div class="main">
      <header class="topbar">
        <div class="crumbs"><Icon :name="page.icon" :size="15" /><b>{{ page.label }}</b></div>
        <span class="spacer"></span>
        <Search />
        <a v-if="activeRun" class="pill run" href="#/workflows"><StatusIcon status="running" :size="15" />Pipeline running · {{ runProgress }} tasks</a>
        <a class="pill" :class="{ live: liveOn }" href="#/live" v-if="store.live && store.live.status !== 'not_loaded'">
          <span class="dot" :class="liveOn ? 'bad pulse' : ''"></span>{{ liveOn ? 'LIVE' : ({ paused: 'Paused', finished: 'Month end', ready: 'Real-time ready' }[store.live.status] || store.live.status) }}
          <span class="num" v-if="store.live.clock">{{ F.simClock(store.live.clock) }}</span></a>
        <button class="icon-btn" @click="toggleTheme" :title="store.theme === 'dark' ? 'Light mode' : 'Dark mode'"><Icon :name="store.theme === 'dark' ? 'sun' : 'moon'" /></button>
      </header>
      <main class="content" :class="{ flush: page.flush }"><component :is="page.page" :key="route.name" /></main>
    </div>
    <div class="toasts"><div v-for="t in store.toasts" :key="t.id" class="toast" :class="t.kind">
      <StatusIcon :status="t.kind === 'ok' ? 'succeeded' : t.kind === 'bad' ? 'failed' : 'none'" :size="16" /><span>{{ t.text }}</span></div></div>
  </div>`,
};

boot().finally(() => {
  const app = createApp(App);
  app.config.errorHandler = (err) => console.error(err);
  app.mount("#app");
});
