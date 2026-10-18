// Decisioning: one set of signals, six consumers
import { api } from "../api.js";
import { store } from "../store.js";
import { go } from "../router.js";
import * as C from "../components.js";
import * as F from "../format.js";
import { barChart, SERIES } from "../charts.js";

const { ref, computed, watch, onMounted } = Vue;
const ICON = Object.fromEntries(F.USE_CASES.map(([k, , i]) => [k, i]));
const WHY = {
  limit_and_activation: "Credit line increases for the healthy, holds and freezes as risk rises, no new spend once in arrears.",
  pricing: "Promotional rates where risk is low; no upward re-pricing for customers in difficulty; interest freezes where they can't pay.",
  exposure: "Trim unused limits with notice, block cash advances and cap exposure as customers move up the ladder.",
  offers_engagement: "Target upgrades and balance transfers only to the low-risk; suppress credit marketing for anyone being watched.",
  support: "Proactive, early help: payment date alignment, budgeting tools, refix conversations, specialist vulnerability routing.",
  collections: "Treatment by can't pay or won't pay and propensity to pay: self-cure nudges, agent calls, forbearance pathways.",
};

export default {
  components: C.all,
  setup() {
    const d = ref(null), err = ref(null);
    async function load() { try { d.value = await api.get("/api/ews/decisions"); err.value = null; } catch (e) { err.value = e; } }
    onMounted(load);
    watch(() => store.dataVersion, load);
    const opt = (u) => {
      const a = u.actions.slice(0, 6);
      return barChart({ x: a.map((x) => x.action), series: [{ name: "Customers", data: a.map((x) => x.customers) }], y: "int", horizontal: true, labels: true, grid: { right: 50 } });
    };
    const leanOpt = computed(() => {
      const rows = d.value?.pay_lean || [];
      const tiers = ["Early intervention", "Collections", "Recoveries"].filter((t) => rows.some((r) => r.tier_name === t));
      const leans = ["Can't pay", "Unclear", "Won't pay"];
      const colors = [SERIES.light[0], "#9aa4b2", SERIES.light[1]];
      return barChart({ x: tiers, stacked: true, y: "int", series: leans.map((l, i) => ({ name: l, color: colors[i], data: tiers.map((t) => rows.find((r) => r.tier_name === t && r.pay_lean === l)?.customers || 0) })) });
    });
    return { d, err, opt, leanOpt, F, ICON, WHY, go, USE: F.USE_CASES };
  },
  template: `<div>
  <div class="page-head"><div><div class="eyebrow">{{ d?.source === 'live' ? 'Live position' : 'Month end' }}</div><h1>Decisioning</h1>
    <p>The same signals feed six business uses. Each decision is policy as code (<span class="mono">ews_lib/actions.py</span>): credit policy and treatment strategies are versioned, testable and reviewed against Consumer Duty, CONC 6.7, CONC 7 and FG21/1.</p></div></div>
  <NeedPipeline v-if="err" what="the decision matrix" />
  <template v-else-if="d">
  <div class="kpis mb">
    <Kpi label="Limit increases pre-approved" :value="F.n(d.highlights.limit_increase_customers)" :sub="'average PD ' + F.pct(d.highlights.limit_increase_avg_pd, 2)" icon="credit-card" color="var(--ok)" />
    <Kpi label="Targeted for offers" :value="F.n(d.highlights.targeted)" icon="megaphone" sub="low risk, with consent" />
    <Kpi label="Credit marketing suppressed" :value="F.n(d.highlights.suppressed)" icon="shield-alert" />
    <Kpi label="Unused limit, Watch and EI" :value="F.gbp(d.highlights.headroom_watch_ei)" icon="wallet" sub="exposure to manage" color="var(--tier-2)" />
    <Kpi label="In collections" :value="F.n(d.highlights.collections)" :sub="F.n(d.highlights.collections_cant_pay) + ' can\\'t pay · ' + F.n(d.highlights.collections_wont_pay) + ' won\\'t pay'" icon="hand-coins" color="var(--tier-4)" />
  </div>
  <div class="card mb"><div class="card-head"><h3>Decision matrix</h3><div class="sub">Most common action for each tier and use</div></div>
    <div class="card-body flush"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>Tier</th><th class="r">Customers</th><th v-for="[k, l, i] in USE" :key="k"><span class="row" style="gap:5px"><Icon :name="i" :size="12" />{{ l }}</span></th></tr></thead>
      <tbody><tr v-for="r in d.matrix" :key="r.tier" class="click" @click="go('watchlist?tier=' + F.TIER_NUM[r.tier])"><td><TierBadge :tier="r.tier" /></td><td class="r"><b>{{ F.n(r.customers) }}</b></td>
        <td v-for="[k] in USE" :key="k" class="small" style="min-width:150px;vertical-align:top">{{ r[k] }}</td></tr></tbody></table></div></div></div>
  <div class="grid g3 mb">
    <div v-for="u in d.use_cases" :key="u.key" class="card"><div class="card-head"><span class="conn-icon" style="width:30px;height:30px"><Icon :name="ICON[u.key]" :size="15" /></span>
      <div><h3>{{ u.label }}</h3><div class="sub">{{ u.actions.length }} distinct actions</div></div></div>
      <div class="card-body"><p class="small muted" style="margin:0 0 6px">{{ WHY[u.key] }}</p><EChart :option="opt(u)" :height="Math.max(150, Math.min(u.actions.length, 6) * 34 + 20)" /></div></div>
  </div>
  <Card title="Can't pay versus won't pay" sub="Early intervention and beyond: what the customer has told us overrides the model; otherwise the classifier decides">
    <div class="grid g-1-2" style="align-items:center"><EChart :option="leanOpt" :height="240" />
      <div class="small" style="color:var(--text-2);line-height:1.6"><p><b>Can't pay</b> (affordability): forbearance pathway, income and expenditure, reduced payment plan, interest and charges freeze, free debt advice.</p>
        <p><b>Won't pay</b> (willingness): escalated contact strategy, agent call within 48 hours, firm arrears letter.</p>
        <p><b>Unclear</b>: agent call within three days, promise to pay with a reminder; propensity to pay decides whether a self-cure nudge is enough.</p>
        <p class="muted">Vulnerability always routes to the specialist team first, and is never used to price or limit credit.</p></div></div>
  </Card>
  </template>
</div>`,
};
