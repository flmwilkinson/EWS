// Ask the EWS: an analyst assistant that answers with SQL over the lakehouse and the live position
import { streamPost } from "../api.js";
import { store } from "../store.js";
import * as C from "../components.js";
import * as F from "../format.js";

const { ref, reactive, nextTick, onBeforeUnmount } = Vue;
const SUGGEST = [
  "Which regions have the most customers in Early intervention, and what share of them lean can't pay?",
  "How many customers have escalated since the September month end, and from which tiers?",
  "What are the five most common signals among Watch customers right now?",
  "Compare the champion and the logistic scorecard on Gini, calibration and explainability.",
  "Which customers face a mortgage payment shock in the next six months, and how many are already on the watchlist?",
  "Is the model still well calibrated? Summarise the latest monitoring metrics against appetite.",
];

export default {
  components: C.all,
  setup() {
    const msgs = reactive([]), input = ref(""), busy = ref(false), scroller = ref(null);
    let ctrl = null;
    async function ask(q) {
      const text = (q ?? input.value).trim(); if (!text || busy.value) return;
      input.value = "";
      msgs.push({ role: "user", content: text });
      const a = reactive({ role: "assistant", content: "", steps: [], meta: null, streaming: true });
      msgs.push(a);
      busy.value = true; ctrl = new AbortController();
      const history = msgs.filter((m) => m !== a).map((m) => ({ role: m.role, content: m.content }));
      try {
        await streamPost("/api/ai/ask", { messages: history }, (ev) => {
          if (ev.type === "delta") a.content += ev.text;
          else if (ev.type === "tool") a.steps.push({ name: ev.name, input: ev.input, result: null, at: a.content.length });
          else if (ev.type === "tool_result") { const s = a.steps[a.steps.length - 1]; if (s) s.result = ev; }
          else if (ev.type === "done") a.meta = ev;
          else if (ev.type === "error") a.content += `\n\n_${ev.message}_`;
          nextTick(() => { if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight; });
        }, ctrl.signal);
      } catch (e) { if (e.name !== "AbortError") a.content += `\n\n_${e.message}_`; }
      a.streaming = false; busy.value = false;
    }
    function key(e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } }
    onBeforeUnmount(() => ctrl?.abort());
    const parts = (m) => {
      // interleave text and tool steps in the order they arrived
      const out = []; let pos = 0;
      for (const s of m.steps) { if (s.at > pos) out.push({ kind: "text", text: m.content.slice(pos, s.at) }); out.push({ kind: "step", step: s }); pos = s.at; }
      if (pos < m.content.length || !m.steps.length) out.push({ kind: "text", text: m.content.slice(pos) });
      return out.filter((p) => p.kind === "step" || p.text.trim());
    };
    return { msgs, input, ask, key, busy, SUGGEST, scroller, parts, F, store };
  },
  template: `<div style="height:100%;display:flex;flex-direction:column">
  <div class="page-head"><div><div class="eyebrow">AI with a read-only SQL tool</div><h1>Ask the EWS</h1>
    <p>Questions in plain English, answered from the lakehouse and the live position. Every query the assistant runs is shown, so analysts can check the numbers.</p></div></div>
  <div ref="scroller" style="flex:1;overflow:auto;min-height:300px">
    <div class="chat">
      <div v-if="!msgs.length"><div class="suggest"><button v-for="s in SUGGEST" :key="s" @click="ask(s)"><Icon name="sparkles" :size="12" style="color:#6250d6;margin-right:5px" />{{ s }}</button></div></div>
      <div v-for="(m, i) in msgs" :key="i" class="msg" :class="m.role">
        <div class="who"><Icon v-if="m.role === 'assistant'" name="sparkles" :size="15" /><Icon v-else name="user" :size="15" /></div>
        <div class="bubble"><template v-if="m.role === 'user'">{{ m.content }}</template>
          <template v-else>
            <template v-for="(p, j) in parts(m)" :key="j">
              <div v-if="p.kind === 'text'" class="ai-body" style="padding:0" ><Markdown :text="p.text" /></div>
              <details v-else class="tool-step"><summary><StatusIcon :status="!p.step.result ? 'running' : p.step.result.ok ? 'succeeded' : 'failed'" :size="15" />
                <span>Ran SQL</span><span class="mono tiny muted ellipsis grow">{{ (p.step.input?.query || '').replace(/\\s+/g, ' ').slice(0, 110) }}</span>
                <span v-if="p.step.result?.preview" class="chip">{{ p.step.result.preview.row_count }} rows</span></summary>
                <pre>{{ p.step.input?.query }}</pre>
                <div v-if="p.step.result?.preview?.rows?.length" class="out-table" style="margin:0 10px 10px;max-height:220px"><table class="tbl compact"><thead><tr><th v-for="c in p.step.result.preview.columns" :key="c">{{ c }}</th></tr></thead>
                  <tbody><tr v-for="(r, k) in p.step.result.preview.rows" :key="k"><td v-for="c in p.step.result.preview.columns" :key="c" class="mono" style="font-size:11.5px">{{ typeof r[c] === 'number' ? +r[c].toFixed(4) : r[c] }}</td></tr></tbody></table></div>
                <pre v-if="p.step.result?.error" style="color:var(--bad-text)">{{ p.step.result.error }}</pre></details>
            </template>
            <div v-if="m.streaming && !m.content" class="row small muted"><StatusIcon status="running" :size="14" />Thinking…</div>
            <div v-if="m.meta?.model" class="tiny muted" style="margin-top:6px">AI · {{ (m.meta.latency_ms / 1000).toFixed(1) }}s · {{ m.steps.length }} queries · {{ F.n((m.meta.input_tokens || 0) + (m.meta.output_tokens || 0)) }} tokens</div>
          </template></div>
      </div>
    </div>
  </div>
  <div class="chat" style="width:100%;padding-top:12px"><div class="composer"><textarea v-model="input" rows="1" @keydown="key" placeholder="Ask about the book, a region, a trigger, a model or a customer…"></textarea>
    <button class="btn primary" @click="ask()" :disabled="busy || !input.trim()"><Icon :name="busy ? 'loader-circle' : 'send'" :size="14" />Ask</button></div>
    <div class="tiny muted" style="margin-top:6px;text-align:center">The assistant can only read; it cannot change any table.</div></div>
</div>`,
};
