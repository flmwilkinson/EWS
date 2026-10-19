# %% [markdown]
# # 04 · Data quality commentary (AI)
#
# The AI writes the data quality paragraph for the monthly EWS pack. It receives **only structured facts**
# (rule results and remediation counts), never customer records, and is told to use only those facts.
# If the model is unavailable the deterministic template is used, so the pipeline never depends on a live call.
#
# The second cell applies the model **row by row**, like `ai_query` in SQL: a one-line "why it matters" for each
# failed rule, run concurrently.

# %% Facts for this run
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from ews_lib import commentary, dq, llm
from ews_lib.lakehouse import lh
from ews_lib.runtime import display, run_context

results = lh.read("ops.dq_results")
results = results[results.run_id == results.run_id.iloc[-1]]
remediation = lh.read("ops.dq_remediation_log")
remediation = remediation[remediation.run_id == remediation.run_id.iloc[-1]]
facts = dq.dq_summary_facts(results, remediation)
print(f"{facts['rules_run']} rules, {facts['failed']} failed, {len(facts['critical_failures'])} critical. Model: {llm.model()}")

# %% Commentary for the EWS pack
prompt, fallback = commentary.build_dq(facts)
text, source, meta = llm.generate(prompt, fallback, purpose="dq_commentary", echo=True)
lh.write("ops.dq_commentary", pd.DataFrame([dict(run_id=run_context()["run_id"], created_at=dt.datetime.now(), source=source,
                                                 model=meta.get("model"), commentary=text)]),
         mode="append", description="AI-written data quality commentary, one row per run")

# %% Why each failed rule matters (row-wise AI)
failed = results[results.status == "FAIL"][["rule_id", "description", "records_failed", "fail_rate", "action"]]


def why(r):
    p = (f"In one sentence of at most 30 words, tell a credit risk manager why this data issue matters for an early "
         f"warning model and its decisions. Issue: {r.description}. Records failing: {r.records_failed:,} "
         f"({r.fail_rate:.2%}). Remediation applied: {r.action}.")
    t, s, _ = llm.generate(p, fallback=f"Remediated by: {r.action.lower()}.", purpose="dq_rule_explainer", max_tokens=300)
    return t


with ThreadPoolExecutor(max_workers=8) as pool:
    failed["why_it_matters"] = list(pool.map(why, failed.itertuples()))
lh.write("ops.dq_rule_explanations", failed.assign(run_id=run_context()["run_id"]), description="AI one-liners per failed rule")
display(failed, title="Failed rules, explained")
