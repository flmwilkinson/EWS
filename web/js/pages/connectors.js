// Connectors: sources in, actions out, and the AI connector. Live throughput, test and sample payloads.
import { api, subscribe } from "../api.js";
import { store, toast } from "../store.js";
import * as C from "../components.js";
import * as F from "../format.js";

const { ref, reactive, computed, onMounted, onBeforeUnmount } = Vue;
const PICON = (p) => (/kafka|event stream/i.test(p) ? "radio-tower" : /cdc/i.test(p) ? "database" : /sftp/i.test(p) ? "folder" : /webhook/i.test(p) ? "webhook" : /https/i.test(p) ? "sparkles" : "globe");


const SHORT = { "Bank of England": "BoE", "Office for National Statistics": "ONS", "HM Land Registry": "HM Land Registry" };
const OfficialSources = {
  components: C.all,
  setup() {
    const d = ref(null), busy = reactive({}), resp = ref(null);
    async function load() { d.value = await api.get("/api/official"); }
    onMounted(load);
    async function fetchNow(src) {
      busy[src.id] = true;
      try { src.result = await api.post(`/api/official/${src.id}/fetch`); if (!src.result.live) toast(`${src.provider}: using the last good pull (${src.result.error || "unavailable"})`, "warn"); }
      catch (e) { toast(e.message, "bad"); }
      busy[src.id] = false;
    }
    async function fetchAll() { await Promise.all(d.value.sources.map(fetchNow)); }
    const value = (r) => (r?.latest?.value !== null && r?.latest?.value !== undefined ? r.latest.value.toFixed(2).replace(/\.?0+$/, "") + "%" : "–");
    const spark = (r) => (r?.observations || []).map((o) => o.value);
    const regions = (src) => (src.result?.regions || []).filter((g) => g.value !== null && g.value !== undefined);
    const scenarioFor = (src, region) => (src.id === "hmlr_ukhpi" ? d.value?.scenario?.hpi?.[region] : d.value?.scenario?.unemployment?.[region]);
    const maxv = (src) => Math.max(...regions(src).map((g) => Math.abs(g.value)), 1);
    const when = (r) => (r?.fetched_at ? F.dt(r.fetched_at, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "not yet");
    const host = (u) => (u || "").replace(/^https:\/\//, "");
    return { d, busy, fetchNow, fetchAll, value, spark, regions, scenarioFor, maxv, when, resp, F, SHORT, host };
  },
  template: `<div class="card mb" v-if="d" id="official-sources">
    <div class="card-head"><span class="conn-icon" style="width:30px;height:30px"><Icon name="landmark" :size="15" /></span>
      <div><h3>Official macroeconomic sources</h3><div class="sub">Live calls to the public APIs of the Bank of England, the Office for National Statistics and HM Land Registry</div></div>
      <div class="actions"><span v-if="d.bronze" class="chip"><Icon name="table" :size="12" />bronze.macro_official_series · {{ d.bronze.rows }} rows</span>
        <button class="btn sm primary" @click="fetchAll"><Icon name="refresh-cw" :size="13" />Fetch all now</button></div></div>
    <div class="card-body">
      <div class="grid g3">
        <div v-for="src in d.sources" :key="src.id" class="conn-card" :style="src.result?.regions ? 'grid-row: span 2' : ''">
          <div class="row"><span class="chip outline mono" style="font-weight:600">{{ SHORT[src.provider] }}</span><span class="chip blue mono">{{ src.series }}</span><span class="spacer"></span>
            <span v-if="busy[src.id]" class="chip"><StatusIcon status="running" :size="12" />calling</span>
            <span v-else-if="src.result?.live" class="chip green"><span class="dot ok" style="width:6px;height:6px"></span>Live · {{ src.result.latency_ms }} ms</span>
            <span v-else-if="src.result" class="chip amber">Cached</span><span v-else class="chip">Not fetched</span></div>
          <div><b>{{ src.title }}</b><div class="tiny muted">{{ src.provider }} · {{ src.dataset }}</div></div>
          <template v-if="!src.result?.regions">
            <div class="row" style="align-items:flex-end"><div><div style="font-size:26px;font-weight:700;letter-spacing:-.02em;line-height:1.1">{{ value(src.result) }}</div>
              <div class="tiny muted">{{ src.result?.latest?.period || '–' }}<span v-if="src.result?.last_change"> · last changed {{ src.result.last_change.period }}</span></div></div>
              <span class="spacer"></span><Spark :data="spark(src.result)" :width="120" :height="34" /></div>
          </template>
          <template v-else>
            <div class="row tiny muted" style="margin-bottom:-4px"><span style="width:150px">Region</span><span class="grow">Official · {{ src.result.latest?.period }}</span><span style="width:70px;text-align:right" title="The economics team scenario the models use">Model scenario</span></div>
            <div v-for="g in regions(src)" :key="g.region" class="row small" style="gap:8px">
              <span style="width:150px" class="ellipsis">{{ g.region }}</span>
              <div class="grow"><div class="row" style="gap:6px"><div class="pbar" style="flex:1;height:6px"><i :style="{ width: (Math.abs(g.value) / maxv(src) * 100) + '%', background: g.value < 0 ? 'var(--ok)' : 'var(--accent)' }"></i></div>
                <b class="num" style="width:44px;text-align:right">{{ g.value.toFixed(1) }}%</b></div></div>
              <span class="num muted" style="width:70px;text-align:right">{{ scenarioFor(src, g.region) !== undefined ? scenarioFor(src, g.region).toFixed(1) + '%' : '–' }}</span></div>
          </template>
          <dl class="kv-list"><dt>Endpoint</dt><dd class="mono tiny clamp2" style="word-break:break-all" :title="src.result?.url || src.url">GET {{ host(src.result?.url || src.url) }}</dd>
            <dt>Format</dt><dd>{{ src.format }} · {{ src.frequency }}</dd><dt>Licence</dt><dd>{{ src.licence }}</dd>
            <dt>Release</dt><dd>{{ src.result?.release_date ? 'published ' + src.result.release_date : 'as published' }}{{ src.result?.next_release ? ' · next ' + src.result.next_release : '' }}</dd>
            <dt>Feeds</dt><dd class="mono tiny">{{ src.feeds }}</dd></dl>
          <div class="row"><button class="btn xs" @click="fetchNow(src)" :disabled="busy[src.id]"><Icon :name="busy[src.id] ? 'loader-circle' : 'download'" :size="12" />Fetch latest</button>
            <button class="btn xs" @click="resp = src" :disabled="!src.result"><Icon name="file-json" :size="12" />Response</button><span class="spacer"></span>
            <span class="tiny muted">{{ when(src.result) }}</span></div>
        </div>
      </div>
      <p class="small muted" style="margin:12px 0 0">The models use the economics team's regional scenario (the "Model scenario" column). The official series are pulled live on every pipeline run into <span class="mono">bronze.macro_official_series</span> for comparison and calibration.</p>
    </div>
    <Drawer :open="!!resp" :title="resp ? resp.provider + ' · ' + resp.series : ''" @close="resp = null" wide>
      <template v-if="resp?.result"><div class="row wrap mb" style="gap:6px"><span class="method GET">GET</span><span class="chip" :class="resp.result.http_status === 200 ? 'green' : 'red'">HTTP {{ resp.result.http_status }}</span>
        <span class="chip">{{ resp.result.content_type }}</span><span class="chip">{{ resp.result.latency_ms }} ms</span><span class="chip" :class="resp.result.live ? 'green' : 'amber'">{{ resp.result.live ? 'live pull' : 'cached' }} · {{ when(resp.result) }}</span></div>
        <label class="lbl">Request</label><pre class="json">GET {{ resp.result.url }}&#10;User-Agent: EarlyWarningStudio/1.0&#10;Accept: application/json, text/csv</pre>
        <label class="lbl mt">Response (excerpt)</label><pre class="json">{{ resp.result.raw_excerpt }}</pre>
        <p class="small muted" v-if="resp.result.title_at_source">Series title at source: {{ resp.result.title_at_source }}</p></template>
    </Drawer>
  </div>`,
};

export default {
  components: { ...C.all, OfficialSources },
  setup() {
    const d = ref(null), tests = reactive({}), sample = ref(null), sampleFor = ref(null);
    async function load() { d.value = await api.get("/api/connectors"); }
    onMounted(load);
    const unsub = subscribe("live", (ev) => {
      if (!d.value) return;
      if (ev.type === "event") { const c = d.value.inbound.find((x) => x.id === ev.event.connector); if (c?.live) { c.live.events++; c.live.today++; c.live.last_ts = ev.event.event_ts; c.pulse = Date.now(); } }
      if (ev.type === "outbound") { const c = d.value.outbound.find((x) => x.id === ev.connector); if (c?.live) { c.live.sent++; c.live.today++; c.pulse = Date.now(); } }
      if (ev.type === "day_end") { d.value.inbound.forEach((c) => c.live && (c.live.per_day.push(c.live.today), (c.live.today = 0))); d.value.outbound.forEach((c) => c.live && (c.live.per_day.push(c.live.today), (c.live.today = 0))); }
    });
    onBeforeUnmount(unsub);
    async function test(c) {
      tests[c.id] = { busy: true };
      try { tests[c.id] = await api.post(`/api/connectors/${c.id}/test`); if (c.id === "claude") load(); }
      catch (e) { tests[c.id] = { ok: false, message: e.message }; }
    }
    async function showSample(c) { sampleFor.value = c; sample.value = null; sample.value = await api.get(`/api/connectors/${c.id}/sample`); }
    const totalIn = computed(() => (d.value?.inbound || []).reduce((a, c) => a + (c.live?.events || 0), 0));
    const totalOut = computed(() => (d.value?.outbound || []).reduce((a, c) => a + (c.live?.sent || 0), 0));
    const liveOn = computed(() => store.live?.status === "running");
    return { d, test, tests, showSample, sample, sampleFor, PICON, F, totalIn, totalOut, liveOn, store };
  },
  template: `<div>
  <div class="page-head"><div><div class="eyebrow">Integration · {{ d ? d.inbound.length + ' sources in, ' + d.outbound.length + ' action feeds out' : '' }}</div><h1>Connectors</h1>
    <p>Every source the EWS reads, how it connects and how often, and every downstream system it updates, with live calls to the official macroeconomic APIs.</p></div></div>
  <template v-if="d">
  <div class="card mb"><div class="card-head"><h3>How data flows</h3><div class="sub">{{ liveOn ? 'Live: counts update as events arrive' : 'Start the live feed to see events flow' }}</div></div>
    <div class="card-body"><div class="grid" style="grid-template-columns:1.2fr 28px 1fr 28px 1fr 28px 1.1fr;align-items:center">
      <div><div class="eyebrow">Sources</div><div v-for="c in d.inbound" :key="c.id" class="arch-box" :class="{ on: c.live?.events }"><Icon :name="PICON(c.protocol)" :size="13" />{{ c.name }}<span class="count">{{ c.live?.events ? F.n(c.live.events) : c.protocol }}</span></div>
        <div class="arch-box on"><Icon name="landmark" :size="13" />BoE · ONS · HM Land Registry<span class="count">6 public APIs</span></div></div>
      <Icon name="arrow-right" :size="20" style="color:var(--muted)" />
      <div><div class="eyebrow">Lakehouse and engine</div>
        <div class="arch-box"><Icon name="layers" :size="13" />Bronze: raw, as delivered</div><div class="arch-box"><Icon name="shield-check" :size="13" />Silver: quality-checked</div>
        <div class="arch-box"><Icon name="target" :size="13" />Gold: ~60 features, 5 models</div><div class="arch-box"><Icon name="siren" :size="13" />38 triggers, watchlist tiers</div>
        <div class="arch-box"><Icon name="split" :size="13" />Decisions for six uses</div></div>
      <Icon name="arrow-right" :size="20" style="color:var(--muted)" />
      <div><div class="eyebrow">Real-time service</div>
        <div class="arch-box" :class="{ on: liveOn }"><Icon name="activity" :size="13" />Event processing<span class="count">{{ F.n(totalIn) }}</span></div>
        <div class="arch-box"><Icon name="zap" :size="13" />POST /api/v1/score</div><div class="arch-box"><Icon name="webhook" :size="13" />POST /api/v1/events</div>
        <div class="arch-box"><Icon name="sparkles" :size="13" />AI explanations</div></div>
      <Icon name="arrow-right" :size="20" style="color:var(--muted)" />
      <div><div class="eyebrow">Actions</div><div v-for="c in d.outbound" :key="c.id" class="arch-box" :class="{ on: c.live?.sent }"><Icon name="send" :size="13" />{{ c.name }}<span class="count">{{ c.live?.sent ? F.n(c.live.sent) : c.protocol }}</span></div></div>
    </div></div></div>

  <OfficialSources />

  <h3 style="font-size:15px;margin:4px 0 10px">Inbound (bank systems)</h3>
  <div class="grid g3 mb">
    <div v-for="c in d.inbound" :key="c.id" class="conn-card">
      <div class="row"><span class="conn-icon"><Icon :name="PICON(c.protocol)" :size="17" /></span><div class="grow"><b>{{ c.name }}</b><div class="tiny muted">{{ c.system }}</div></div>
        <span class="chip" :class="c.status === 'Connected' ? 'green' : ''"><span class="dot" :class="c.status === 'Connected' ? 'ok' : ''" style="width:6px;height:6px"></span>{{ c.status }}</span></div>
      <dl class="kv-list"><dt>Protocol</dt><dd><span class="chip blue">{{ c.protocol }}</span></dd><dt>Endpoint</dt><dd class="mono tiny">{{ c.endpoint }}</dd>
        <dt>Auth</dt><dd>{{ c.auth }}</dd><dt>Cadence</dt><dd>{{ c.cadence }}</dd><dt>Format</dt><dd>{{ c.format }}</dd></dl>
      <div class="decision" style="padding:8px 10px"><div class="l">Batch: last load</div>
        <div v-for="b in c.batch" :key="b.table" class="row small" style="margin-top:3px"><span class="mono tiny grow">bronze.{{ b.table }}</span><span class="num">{{ b.rows ? F.n(b.rows) + ' rows' : 'not loaded' }}</span><span class="tiny muted">{{ b.loaded_at ? F.ago(b.loaded_at) : '' }}</span></div></div>
      <div class="row" v-if="c.live_sources.length"><span class="small muted">Live events</span><b class="num">{{ F.n(c.live?.events || 0) }}</b><span class="tiny muted">({{ c.live?.today || 0 }} today)</span><span class="spacer"></span>
        <Spark :data="c.live?.per_day || []" bars :width="100" :height="22" /></div>
      <div class="row"><button class="btn xs" @click="test(c)"><Icon :name="tests[c.id]?.busy ? 'loader-circle' : 'plug'" :size="12" />Test</button>
        <button class="btn xs" @click="showSample(c)"><Icon name="file-json" :size="12" />Sample payload</button><span class="spacer"></span>
        <span v-if="tests[c.id] && !tests[c.id].busy" class="tiny" :style="{ color: tests[c.id].ok ? 'var(--ok-text)' : 'var(--bad-text)' }">{{ tests[c.id].ok ? '✓ ' + tests[c.id].latency_ms + ' ms' : tests[c.id].message }}</span></div>
    </div>
  </div>

  <h3 style="font-size:15px;margin:4px 0 10px">Outbound and AI</h3>
  <div class="grid g3">
    <div v-for="c in d.outbound" :key="c.id" class="conn-card">
      <div class="row"><span class="conn-icon out"><Icon name="send" :size="17" /></span><div class="grow"><b>{{ c.name }}</b><div class="tiny muted">{{ c.system }}</div></div><span class="chip green">{{ c.status }}</span></div>
      <dl class="kv-list"><dt>Protocol</dt><dd><span class="chip blue">{{ c.protocol }}</span></dd><dt>Endpoint</dt><dd class="mono tiny">{{ c.endpoint }}</dd><dt>Trigger</dt><dd>{{ c.cadence }}</dd>
        <dt>Carries</dt><dd>{{ c.use_cases.join(', ').replaceAll('_', ' ') }}</dd></dl>
      <div class="row"><span class="small muted">Actions sent</span><b class="num">{{ F.n(c.live?.sent || 0) }}</b><span class="spacer"></span><Spark :data="c.live?.per_day || []" bars :width="100" :height="22" color="var(--ok)" /></div>
      <div class="row"><button class="btn xs" @click="test(c)"><Icon :name="tests[c.id]?.busy ? 'loader-circle' : 'plug'" :size="12" />Test</button><button class="btn xs" @click="showSample(c)"><Icon name="file-json" :size="12" />Last payload</button>
        <span class="spacer"></span><span v-if="tests[c.id] && !tests[c.id].busy" class="tiny" style="color:var(--ok-text)">✓ {{ tests[c.id].latency_ms }} ms</span></div>
    </div>
    <div class="conn-card" style="border-color:color-mix(in srgb,#6250d6 35%,var(--border))">
      <div class="row"><span class="conn-icon ai"><Icon name="sparkles" :size="17" /></span><div class="grow"><b>{{ d.ai.name }}</b><div class="tiny muted">{{ d.ai.system }}</div></div>
        <span class="chip" :class="d.ai.configured ? 'green' : 'red'">{{ d.ai.status }}</span></div>
      <dl class="kv-list"><dt>Model</dt><dd class="mono">{{ d.ai.model }}</dd><dt>Endpoint</dt><dd class="mono tiny">{{ d.ai.endpoint }}</dd><dt>Auth</dt><dd>{{ d.ai.auth }}</dd>
        <dt>Calls</dt><dd>{{ F.n(d.ai.calls?.n || 0) }} · {{ F.compact((d.ai.calls?.in_tok || 0) + (d.ai.calls?.out_tok || 0)) }} tokens · avg {{ d.ai.calls?.avg_ms ? (d.ai.calls.avg_ms / 1000).toFixed(1) + 's' : '–' }}</dd></dl>
      <div class="row"><button class="btn xs ai" @click="test({ id: 'claude' })"><Icon :name="tests.claude?.busy ? 'loader-circle' : 'plug'" :size="12" />Test live call</button><a class="btn xs" href="#/ai-log"><Icon name="scan-search" :size="12" />Audit log</a>
        <span class="spacer"></span><span v-if="tests.claude && !tests.claude.busy" class="tiny" :style="{ color: tests.claude.ok ? 'var(--ok-text)' : 'var(--bad-text)' }">{{ tests.claude.ok ? '✓ ' + tests.claude.model + ' · ' + tests.claude.latency_ms + ' ms' : tests.claude.message }}</span></div>
    </div>
  </div>
  </template>
  <Drawer :open="!!sampleFor" :title="sampleFor ? sampleFor.name + ': ' + (sample?.source === 'live' ? 'latest live message' : 'example message') : ''" @close="sampleFor = null">
    <p class="small muted" style="margin-top:0" v-if="sampleFor">{{ sampleFor.protocol }} · <span class="mono">{{ sampleFor.endpoint }}</span></p>
    <div v-if="!sample" class="skeleton"></div><JsonView v-else :value="sample.payload" />
  </Drawer>
</div>`,
};
