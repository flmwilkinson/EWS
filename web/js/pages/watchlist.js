// Watchlist: filter, sort and drill into customers
import { api } from "../api.js";
import { store } from "../store.js";
import { route, go } from "../router.js";
import * as C from "../components.js";
import * as F from "../format.js";

const { ref, reactive, computed, watch, onMounted, onBeforeUnmount } = Vue;

export default {
  components: C.all,
  setup() {
    const q = route.query;
    const f = reactive({ tier: q.tier !== undefined ? q.tier : "1,2,3", region: q.region || "", segment: q.segment || "", lean: q.lean || "", band: q.band || "",
      trigger: q.trigger || "", q: q.q || "", changed: false, sort: "pd_6m", desc: true, offset: 0, limit: 50 });
    const d = ref(null), err = ref(null), loading = ref(false), trig = ref([]);
    async function load() {
      loading.value = true;
      const p = new URLSearchParams(Object.entries(f).map(([k, v]) => [k, String(v)]));
      try { d.value = await api.get(`/api/ews/watchlist?${p}`); err.value = null; } catch (e) { err.value = e; }
      loading.value = false;
    }
    let t;
    watch(() => ({ ...f }), (n, o) => { if (o && n.offset === o.offset) f.offset = 0; clearTimeout(t); t = setTimeout(load, 120); }, { deep: true });
    onMounted(async () => { load(); try { const r = await api.get("/api/ews/triggers"); trig.value = r.triggers; } catch (e) {} });
    watch(() => store.dataVersion, load);
    const refresh = setInterval(() => { if (store.live?.status === "running") load(); }, 7000);
    onBeforeUnmount(() => clearInterval(refresh));
    const tiers = computed(() => f.tier.split(",").filter(Boolean).map(Number));
    function toggleTier(i) { const s = new Set(tiers.value); s.has(i) ? s.delete(i) : s.add(i); f.tier = [...s].sort().join(","); }
    const cols = [
      { key: "customer_id", label: "Customer" }, { key: "tier", label: "Tier" }, { key: "tier_reason", label: "Why", sortable: false },
      { key: "pd_6m", label: "PD 6m", align: "r" }, { key: "risk_band", label: "Band", align: "c" }, { key: "p_arrears_3m", label: "P(arrears 3m)", align: "r" },
      { key: "pay_lean", label: "Can't / won't", sortable: false }, { key: "trigger_names", label: "Signals", sortable: false }, { key: "breadth", label: "Breadth", align: "r", title: "Evidence dimensions with a signal" },
      { key: "balance", label: "Balance", align: "r" }, { key: "region", label: "Region" }, { key: "next", label: "Next action", sortable: false },
    ];
    const next = (r) => (r.tier >= 4 ? r.collections : r.support !== "None" ? r.support : r.limit_and_activation);
    const pages = computed(() => (d.value ? Math.ceil(d.value.total / f.limit) : 0));
    const facet = (k) => Object.entries(d.value?.facets?.[k] || {}).sort((a, b) => b[1] - a[1]);
    return { f, d, err, loading, cols, tiers, toggleTier, next, pages, facet, F, go, trig, store };
  },
  template: `<div>
  <div class="page-head"><div><div class="eyebrow">{{ d?.source === 'live' ? 'Live position · day ' + (store.live?.day || 0) : 'Month-end position' }}</div><h1>Watchlist</h1>
    <p>Every customer the EWS is watching, why, how likely they are to miss a payment, whether it looks like can't pay or won't pay, and what happens next. Click a row for the full picture.</p></div></div>
  <NeedPipeline v-if="err" what="the watchlist" />
  <template v-else>
  <div class="card mb"><div class="card-body" style="padding:12px 14px">
    <div class="row wrap" style="gap:6px">
      <span class="small muted" style="margin-right:4px">Tier</span>
      <button v-for="(t, i) in F.TIERS" :key="t" class="chip click" :class="{ sel: tiers.includes(i) }" @click="toggleTier(i)">{{ t }} <span style="opacity:.75">{{ F.n(d?.facets?.tier?.[t] || 0) }}</span></button>
      <span class="spacer"></span>
      <label class="row small" style="gap:6px"><input type="checkbox" v-model="f.changed" />Changed since month end</label>
      <div class="search" style="width:240px"><Icon name="search" :size="15" /><input v-model="f.q" placeholder="Customer ID or name" /></div>
    </div>
    <div class="row wrap" style="gap:8px;margin-top:10px">
      <select class="input" v-model="f.region"><option value="">All regions</option><option v-for="[k, v] in facet('region')" :key="k" :value="k">{{ k }} ({{ v }})</option></select>
      <select class="input" v-model="f.segment"><option value="">All segments</option><option v-for="[k, v] in facet('segment')" :key="k" :value="k">{{ k }} ({{ v }})</option></select>
      <select class="input" v-model="f.lean"><option value="">Can't or won't pay: any</option><option v-for="[k, v] in facet('pay_lean')" :key="k" :value="k">{{ k }} ({{ v }})</option></select>
      <select class="input" v-model="f.band"><option value="">All risk bands</option><option v-for="b in 'ABCDEFG'" :key="b" :value="b">Band {{ b }}</option></select>
      <select class="input" v-model="f.trigger" style="max-width:260px"><option value="">Any signal</option><option v-for="t in trig" :key="t.trigger_id" :value="t.trigger_id">{{ t.trigger_id }} · {{ t.name }}</option></select>
      <span class="spacer"></span>
      <template v-if="d"><span class="chip">{{ F.n(d.total) }} customers</span><span class="chip">{{ F.gbp(d.totals.balance) }} balance</span><span class="chip">avg PD {{ F.pct(d.totals.avg_pd, 1) }}</span>
        <span class="chip amber" v-if="d.totals.changed">{{ F.n(d.totals.changed) }} moved since month end</span></template>
    </div></div></div>
  <div class="card" :style="{ opacity: loading ? .7 : 1 }">
    <DataTable :columns="cols" :rows="d?.rows || []" row-key="customer_id" clickable @row-click="r => go('customer/' + r.customer_id)"
      :external-sort="{ key: f.sort, desc: f.desc }" @sort="s => { f.sort = s.key; f.desc = s.desc; }" empty="No customers match these filters">
      <template #cell-customer_id="{ row }"><div class="row" style="gap:8px"><Avatar :name="row.persona || row.customer_id.slice(-2)" style="width:26px;height:26px;font-size:10px" />
        <div><b v-if="row.persona">{{ row.persona }}</b><div class="mono" :class="row.persona ? 'tiny muted' : 'small'">{{ row.customer_id }}</div></div></div></template>
      <template #cell-tier="{ row }"><div class="col" style="gap:2px"><TierBadge :tier="row.tier" /><span v-if="row.changed_since_month_end" class="tiny muted">was {{ row.tier_month_end }}</span></div></template>
      <template #cell-tier_reason="{ value }"><span class="small clamp2" style="min-width:150px;max-width:220px" :title="value">{{ value }}</span></template>
      <template #cell-pd_6m="{ value }"><div class="cellbar"><b>{{ F.pct(value, 1) }}</b><PBar :value="value" :max="0.4" :color="value > 0.1 ? 'var(--bad)' : value > 0.03 ? 'var(--warn)' : 'var(--ok)'" /></div></template>
      <template #cell-risk_band="{ value }"><Band :band="value" /></template>
      <template #cell-p_arrears_3m="{ value }">{{ F.pct(value, 1) }}</template>
      <template #cell-pay_lean="{ row }"><span v-if="row.tier >= 3" class="chip" :class="row.pay_lean === 'Won\\'t pay' ? 'red' : row.pay_lean === 'Can\\'t pay' ? 'blue' : ''">{{ row.pay_lean }}</span><span v-else class="muted small">n/a</span></template>
      <template #cell-trigger_names="{ value }"><div class="row wrap" style="gap:3px;max-width:300px"><span v-for="t in (value || []).slice(0, 2)" :key="t" class="chip">{{ t }}</span>
        <span v-if="(value || []).length > 2" class="chip outline" :title="value.slice(2).join(', ')">+{{ value.length - 2 }}</span></div></template>
      <template #cell-balance="{ value }">{{ F.gbp(value, false) }}</template>
      <template #cell-region="{ row }"><span class="small">{{ row.region }}</span><div class="tiny muted">{{ row.segment }}</div></template>
      <template #cell-next="{ row }"><span class="small clamp2" style="min-width:180px;max-width:260px" :title="next(row)">{{ next(row) }}</span></template>
    </DataTable>
    <div class="tbl-foot" v-if="d && d.total"><span>Showing {{ f.offset + 1 }}–{{ Math.min(f.offset + f.limit, d.total) }} of {{ F.n(d.total) }}</span><span class="spacer"></span>
      <button class="btn sm" :disabled="f.offset === 0" @click="f.offset = Math.max(0, f.offset - f.limit)"><Icon name="chevron-left" :size="13" />Previous</button>
      <button class="btn sm" :disabled="f.offset + f.limit >= d.total" @click="f.offset += f.limit">Next<Icon name="chevron-right" :size="13" /></button></div>
  </div>
  </template>
</div>`,
};
