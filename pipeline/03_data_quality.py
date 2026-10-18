# %% [markdown]
# # 03 · Data quality: rules, remediation and silver
#
# 1. **Rules are data, not code.** Each rule in the catalogue (`ews_lib/dq.py`) is one SQL test with a dimension,
#    severity, tolerance and a named remediation. Seven dimensions: completeness, uniqueness, validity, consistency,
#    referential integrity, timeliness and distribution.
# 2. **Run** every rule against bronze with DuckDB SQL → `ops.dq_results` (appended, so results build a history across runs).
# 3. **Remediate into silver:** deduplicate, quarantine invalid rows (`silver.dq_quarantine`), impute and flag,
#    carry the late bureau file forward as stale → `ops.dq_remediation_log`.
#
# The AI commentary on these results runs as its own task (04) in parallel with feature engineering.

# %% Load bronze
import pandas as pd

from ews_lib import dq
from ews_lib.generator import LANDING_FORMATS
from ews_lib.lakehouse import lh
from ews_lib.runtime import chart, display, metrics, run_context

SOURCES = [k for k in LANDING_FORMATS if k not in ("generator_truth", "personas")]
bronze = {k: lh.read(f"bronze.{k}").drop(columns=["_connector", "_source_file", "_loaded_at"], errors="ignore") for k in SOURCES}
print(f"Loaded {len(bronze)} bronze tables, {sum(len(v) for v in bronze.values()):,} rows")

# %% The rule catalogue
rules = dq.rules_frame()
lh.write("ops.dq_rules", rules, description="Data quality rule catalogue: one SQL test per rule")
display(rules[["rule_id", "table", "dimension", "description", "severity", "threshold", "action"]], title=f"{len(rules)} rules")

# %% Run the rules on bronze
results = dq.run_rules(lh.sql_on(bronze), {k: k for k in SOURCES}, run_id=run_context()["run_id"])
lh.write("ops.dq_results", results, mode="append", description="Rule results, one row per rule per run")
metrics({"Rules run": len(results), "Passed": int((results.status == "PASS").sum()),
         "Failed": int((results.status == "FAIL").sum()),
         "Critical failures": int(((results.status == "FAIL") & (results.severity == "Critical")).sum())})
display(results[["rule_id", "table_name", "dimension", "description", "severity", "records_tested", "records_failed",
                 "fail_rate", "threshold", "status"]], title="Results")

# %% Pass and fail by dimension
by_dim = results.assign(passed=results.status == "PASS", failed=results.status == "FAIL").groupby("dimension")[["passed", "failed"]].sum()
chart("bar", by_dim.index, {"Passed": by_dim.passed, "Failed": by_dim.failed}, title="Rules by dimension", y_format="int")

# %% Remediate into silver
silver, quarantine, remediation = dq.conform_to_silver(bronze)
for name, df in silver.items():
    lh.write(f"silver.{name}", df, description=f"Conformed {name}: remediation applied")
lh.write("silver.dq_quarantine", quarantine, description="Rows rejected by data quality rules, with the reason")
remediation["run_id"] = run_context()["run_id"]
lh.write("ops.dq_remediation_log", remediation, mode="append", description="What each remediation changed, per run")
display(remediation, title="Remediation log")

# %% What went to quarantine
display(quarantine.groupby(["source_table", "rule_id", "reason"]).size().rename("rows").reset_index(), title="Quarantined rows")
