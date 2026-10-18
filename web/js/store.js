// Global reactive state shared by every page
import { api, subscribe } from "./api.js";

const { reactive } = Vue;

export const store = reactive({
  theme: document.documentElement.dataset.theme || "light",
  health: null,
  pipeline: null,
  runs: {},            // run_id -> run summary (with task_state summaries)
  activeRunId: null,
  live: { status: "not_loaded", day: 0, clock: null },
  liveKpis: null,
  toasts: [],
  dataVersion: 0,      // bumps when the pipeline writes tables, so pages can refetch
  apiCalls: [],
});

export function toast(text, kind = "info", ms = 4200) {
  const id = Math.random().toString(36).slice(2);
  store.toasts.push({ id, text, kind });
  setTimeout(() => { const i = store.toasts.findIndex((t) => t.id === id); if (i >= 0) store.toasts.splice(i, 1); }, ms);
}

export function setTheme(t) {
  store.theme = t;
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("ews-theme", t); } catch (e) {}
}

let bumpTimer = null;
let clockTimer = null, pendingClock = null;  // the simulated clock repaints at most four times a second
function bumpData() {
  clearTimeout(bumpTimer);
  bumpTimer = setTimeout(() => store.dataVersion++, 600);
}

export async function boot() {
  try { store.health = await api.get("/api/health"); store.live = store.health.live; } catch (e) {}
  try { store.pipeline = await api.get("/api/jobs/pipeline"); } catch (e) {}
  try {
    const runs = await api.get("/api/runs?limit=40");
    runs.forEach((r) => { store.runs[r.run_id] = r; if (r.status === "RUNNING") store.activeRunId = r.run_id; });
  } catch (e) {}
  subscribe("runs", (ev) => {
    if (ev.type === "run") {
      const prev = store.runs[ev.run.run_id] || {};
      store.runs[ev.run.run_id] = { ...prev, ...ev.run };
      if (ev.run.status === "RUNNING") store.activeRunId = ev.run.run_id;
      else if (store.activeRunId === ev.run.run_id) {
        store.activeRunId = null;
        const ok = ev.run.status === "SUCCEEDED";
        toast(`Pipeline run ${ok ? "succeeded" : ev.run.status.toLowerCase()} in ${Math.round(ev.run.duration || 0)}s`, ok ? "ok" : "bad", 6000);
        bumpData();
      }
    } else if (ev.type === "task") {
      const r = store.runs[ev.run_id];
      if (r) {
        r.task_state = r.task_state || {};
        r.task_state[ev.task.key] = { ...(r.task_state[ev.task.key] || {}), ...ev.task };
        if (ev.task.status === "failed") toast(`Task failed: ${ev.task.key}. ${ev.task.error || ""}`, "bad", 8000);
        if (ev.task.status === "succeeded") bumpData();
      }
    }
  });
  subscribe("live", (ev) => {
    if (ev.type === "status") store.live = { ...store.live, ...ev };
    else if (ev.type === "clock") { pendingClock = ev; if (!clockTimer) clockTimer = setTimeout(() => { clockTimer = null; store.live.clock = pendingClock.clock; store.live.day = pendingClock.day; }, 250); }
    else if (ev.type === "day_end") store.liveKpis = ev.kpis;
    else if (ev.type === "alert" && ev.kpis) store.liveKpis = ev.kpis;
    else if (ev.type === "reset") { store.liveKpis = null; bumpData(); }
  });
  subscribe("platform", (ev) => {
    if (ev.type === "api_call") { store.apiCalls.unshift(ev); store.apiCalls.splice(50); }
    if (ev.type === "model_registered" && ev.stage === "Champion" && ev.name.startsWith("pd6m")) toast(`Champion registered: ${ev.approach} (v${ev.version})`, "ok");
  });
  setInterval(async () => { try { store.health = await api.get("/api/health"); } catch (e) {} }, 15000);
}
