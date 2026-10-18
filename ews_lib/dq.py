"""Data quality framework.

Rules are data, not code: each rule is a SQL query that returns `tested` and
`failed` counts. The same rule set runs on Spark SQL in Databricks and on
DuckDB locally. Results land in `ews_ops.dq_results`, remediation in
`ews_ops.dq_remediation_log` and rejected rows in `ews_silver.dq_quarantine`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Rule catalogue
# ---------------------------------------------------------------------------
# {t:<name>} placeholders are resolved to fully qualified table names at run time.
DQ_RULES = [
    # Completeness
    dict(rule_id="DQ001", table="customers", dimension="Completeness", severity="High", threshold=0.01,
         action="Impute segment median and flag", description="Gross income populated",
         sql="SELECT COUNT(*) AS tested, SUM(CASE WHEN gross_income_annual IS NULL THEN 1 ELSE 0 END) AS failed FROM {t:customers}"),
    dict(rule_id="DQ002", table="card_monthly", dimension="Completeness", severity="Critical", threshold=0.001,
         action="Derive from statement balance less payment", description="Closing balance populated",
         sql="SELECT COUNT(*) AS tested, SUM(CASE WHEN closing_balance IS NULL THEN 1 ELSE 0 END) AS failed FROM {t:card_monthly}"),
    dict(rule_id="DQ003", table="contact_notes", dimension="Completeness", severity="Medium", threshold=0.01,
         action="Exclude from sentiment scoring", description="Note text is not empty",
         sql="SELECT COUNT(*) AS tested, SUM(CASE WHEN note_text IS NULL OR TRIM(note_text) = '' THEN 1 ELSE 0 END) AS failed FROM {t:contact_notes}"),
    # Uniqueness
    dict(rule_id="DQ010", table="customers", dimension="Uniqueness", severity="Critical", threshold=0.0,
         action="Deduplicate, keep first", description="One row per customer_id",
         sql="SELECT (SELECT COUNT(*) FROM {t:customers}) AS tested, (SELECT COUNT(*) FROM {t:customers}) - (SELECT COUNT(DISTINCT customer_id) FROM {t:customers}) AS failed"),
    dict(rule_id="DQ011", table="card_monthly", dimension="Uniqueness", severity="Critical", threshold=0.0,
         action="Deduplicate, keep first", description="One row per account per month",
         sql="SELECT (SELECT COUNT(*) FROM {t:card_monthly}) AS tested, (SELECT COUNT(*) FROM {t:card_monthly}) - (SELECT COUNT(*) FROM (SELECT DISTINCT account_id, month FROM {t:card_monthly}) x) AS failed"),
    # Validity
    dict(rule_id="DQ020", table="customers", dimension="Validity", severity="High", threshold=0.0,
         action="Quarantine customer", description="Age between 18 and 110",
         sql="SELECT COUNT(*) AS tested, SUM(CASE WHEN age < 18 OR age > 110 THEN 1 ELSE 0 END) AS failed FROM {t:customers}"),
    dict(rule_id="DQ021", table="card_monthly", dimension="Validity", severity="Critical", threshold=0.0,
         action="Replace with prior month limit", description="Credit limit greater than zero",
         sql="SELECT COUNT(*) AS tested, SUM(CASE WHEN credit_limit <= 0 THEN 1 ELSE 0 END) AS failed FROM {t:card_monthly}"),
    dict(rule_id="DQ022", table="card_monthly", dimension="Validity", severity="High", threshold=0.0,
         action="Quarantine row", description="Payment amount not negative",
         sql="SELECT COUNT(*) AS tested, SUM(CASE WHEN payment_amount < 0 THEN 1 ELSE 0 END) AS failed FROM {t:card_monthly}"),
    dict(rule_id="DQ023", table="current_account_monthly", dimension="Validity", severity="High", threshold=0.0,
         action="Set to null and flag income as unverified", description="Salary credit not negative",
         sql="SELECT COUNT(*) AS tested, SUM(CASE WHEN salary_credit_amt < 0 THEN 1 ELSE 0 END) AS failed FROM {t:current_account_monthly}"),
    dict(rule_id="DQ024", table="bureau_monthly", dimension="Validity", severity="Medium", threshold=0.0,
         action="Cap to range", description="Bureau score between 0 and 999",
         sql="SELECT COUNT(*) AS tested, SUM(CASE WHEN bureau_score < 0 OR bureau_score > 999 THEN 1 ELSE 0 END) AS failed FROM {t:bureau_monthly}"),
    # Consistency
    dict(rule_id="DQ030", table="card_monthly", dimension="Consistency", severity="Medium", threshold=0.005,
         action="Recompute utilisation", description="Utilisation equals statement balance / limit (within 1pp)",
         sql="SELECT COUNT(*) AS tested, SUM(CASE WHEN credit_limit > 0 AND statement_balance IS NOT NULL AND ABS(utilisation - statement_balance / credit_limit) > 0.01 THEN 1 ELSE 0 END) AS failed FROM {t:card_monthly}"),
    dict(rule_id="DQ031", table="card_monthly", dimension="Consistency", severity="High", threshold=0.0,
         action="Investigate source", description="Missed payment implies cycles delinquent > 0",
         sql="SELECT COUNT(*) AS tested, SUM(CASE WHEN missed_payment_flag AND cycles_delinquent = 0 THEN 1 ELSE 0 END) AS failed FROM {t:card_monthly}"),
    # Referential integrity
    dict(rule_id="DQ040", table="card_monthly", dimension="Referential integrity", severity="Critical", threshold=0.0,
         action="Quarantine orphan rows", description="Every card month links to a card account",
         sql="SELECT COUNT(*) AS tested, SUM(CASE WHEN a.account_id IS NULL THEN 1 ELSE 0 END) AS failed FROM {t:card_monthly} m LEFT JOIN (SELECT DISTINCT account_id FROM {t:card_accounts}) a ON m.account_id = a.account_id"),
    dict(rule_id="DQ041", table="bureau_monthly", dimension="Referential integrity", severity="Medium", threshold=0.0,
         action="Drop unmatched", description="Every bureau record links to a customer",
         sql="SELECT COUNT(*) AS tested, SUM(CASE WHEN c.customer_id IS NULL THEN 1 ELSE 0 END) AS failed FROM {t:bureau_monthly} b LEFT JOIN (SELECT DISTINCT customer_id FROM {t:customers}) c ON b.customer_id = c.customer_id"),
    # Timeliness / coverage
    dict(rule_id="DQ050", table="bureau_monthly", dimension="Timeliness", severity="High", threshold=0.01,
         action="Carry forward prior month and flag as stale", description="Bureau file covers every active card customer for the latest month",
         sql="SELECT COUNT(*) AS tested, SUM(CASE WHEN b.customer_id IS NULL THEN 1 ELSE 0 END) AS failed FROM (SELECT customer_id FROM {t:card_monthly} WHERE month = (SELECT MAX(month) FROM {t:card_monthly})) c LEFT JOIN (SELECT customer_id FROM {t:bureau_monthly} WHERE month = (SELECT MAX(month) FROM {t:card_monthly})) b ON c.customer_id = b.customer_id"),
    dict(rule_id="DQ051", table="macro_regional_monthly", dimension="Timeliness", severity="Medium", threshold=0.0,
         action="Forward fill last known value and flag", description="Macro series present for every region in the latest month",
         sql="SELECT 12 AS tested, 12 - (SELECT COUNT(DISTINCT region) FROM {t:macro_regional_monthly} WHERE month = (SELECT MAX(month) FROM {t:card_monthly})) AS failed"),
    # Distribution (month on month)
    dict(rule_id="DQ060", table="card_monthly", dimension="Distribution", severity="Medium", threshold=0.0,
         action="Alert data owner", description="Average utilisation moves less than 5pp month on month",
         sql="SELECT 1 AS tested, CASE WHEN ABS(cur.u - prv.u) > 0.05 THEN 1 ELSE 0 END AS failed FROM (SELECT AVG(utilisation) u FROM {t:card_monthly} WHERE month = (SELECT MAX(month) FROM {t:card_monthly}) AND credit_limit > 0) cur CROSS JOIN (SELECT AVG(utilisation) u FROM {t:card_monthly} WHERE month = (SELECT MAX(month) FROM {t:card_monthly} WHERE month < (SELECT MAX(month) FROM {t:card_monthly})) AND credit_limit > 0) prv"),
]


def rules_frame():
    return pd.DataFrame(DQ_RULES)


def run_rules(run_sql, table_names, rules=None, run_id=None):
    """Execute the rule catalogue.

    run_sql      callable(sql) -> pandas DataFrame
    table_names  mapping of logical name -> physical table name
    """
    rules = rules or DQ_RULES
    run_id = run_id or uuid.uuid4().hex[:12]
    ts = datetime.now(timezone.utc).replace(tzinfo=None)
    out = []
    for r in rules:
        sql = r["sql"]
        for k, v in table_names.items():
            sql = sql.replace("{t:%s}" % k, v)
        try:
            res = run_sql(sql)
            tested = int(res.iloc[0]["tested"] or 0)
            failed = int(res.iloc[0]["failed"] or 0)
            err = None
        except Exception as e:  # keep the run going, record the error
            tested, failed, err = 0, 0, str(e)[:300]
        rate = failed / tested if tested else 0.0
        status = "ERROR" if err else ("PASS" if rate <= r["threshold"] else "FAIL")
        out.append(dict(run_id=run_id, run_ts=ts, rule_id=r["rule_id"], table_name=r["table"],
                        dimension=r["dimension"], description=r["description"], severity=r["severity"],
                        threshold=r["threshold"], records_tested=tested, records_failed=failed,
                        fail_rate=round(rate, 6), status=status, action=r["action"], error=err))
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# Remediation: bronze -> silver
# ---------------------------------------------------------------------------
def conform_to_silver(b):
    """Apply the remediation actions named in the rule catalogue.

    Returns (silver dict, quarantine DataFrame, remediation log DataFrame).
    """
    s, quarantine, log = {}, [], []

    def note(rule_id, table, action, n):
        log.append(dict(rule_id=rule_id, table_name=table, action=action, rows_affected=int(n)))

    def quarantine_rows(df, mask, table, rule_id, reason):
        q = df[mask]
        if len(q):
            quarantine.append(pd.DataFrame(dict(source_table=table, rule_id=rule_id, reason=reason,
                                                record_key=q.iloc[:, 0].astype(str).values,
                                                record_json=q.astype(str).to_json(orient="records", lines=True).splitlines())))
        return df[~mask]

    # customers
    c = b["customers"].copy()
    n0 = len(c)
    c = c.drop_duplicates("customer_id", keep="first")
    note("DQ010", "customers", "Deduplicated", n0 - len(c))
    bad_age = (c.age < 18) | (c.age > 110)
    c = quarantine_rows(c, bad_age, "customers", "DQ020", "Age out of range")
    note("DQ020", "customers", "Quarantined", bad_age.sum())
    miss = c.gross_income_annual.isna()
    c["income_imputed_flag"] = miss
    c["gross_income_annual"] = c.gross_income_annual.fillna(c.groupby("segment").gross_income_annual.transform("median"))
    note("DQ001", "customers", "Imputed segment median", miss.sum())
    s["customers"] = c.reset_index(drop=True)
    valid_customers = set(c.customer_id)

    # card accounts
    a = b["card_accounts"].drop_duplicates("account_id")
    a = a[a.customer_id.isin(valid_customers)]
    s["card_accounts"] = a.reset_index(drop=True)

    # card monthly
    m = b["card_monthly"].copy()
    m["month"] = pd.to_datetime(m["month"])
    n0 = len(m)
    m = m.drop_duplicates(["account_id", "month"], keep="first")
    note("DQ011", "card_monthly", "Deduplicated", n0 - len(m))
    m = quarantine_rows(m, ~m.account_id.isin(set(a.account_id)), "card_monthly", "DQ040", "Orphan account")
    note("DQ040", "card_monthly", "Quarantined orphan rows (incl. accounts of quarantined customers)", (~b["card_monthly"].account_id.isin(set(a.account_id))).sum())
    neg = m.payment_amount < 0
    m = quarantine_rows(m, neg, "card_monthly", "DQ022", "Negative payment")
    note("DQ022", "card_monthly", "Quarantined", neg.sum())
    m = m.sort_values(["account_id", "month"])
    zero = m.credit_limit <= 0
    m["credit_limit"] = m.credit_limit.where(~zero).groupby(m.account_id).ffill().bfill()
    note("DQ021", "card_monthly", "Replaced with prior month limit", zero.sum())
    nb = m.closing_balance.isna()
    m["closing_balance"] = m.closing_balance.fillna((m.statement_balance - m.payment_amount).clip(lower=0))
    note("DQ002", "card_monthly", "Derived balance", nb.sum())
    new_util = (m.statement_balance / m.credit_limit).round(4)
    note("DQ030", "card_monthly", "Recomputed utilisation after fixes", ((new_util - m.utilisation).abs() > 0.01).sum())
    m["utilisation"] = new_util
    s["card_monthly"] = m.reset_index(drop=True)

    # current account
    ca = b["current_account_monthly"].copy()
    ca["month"] = pd.to_datetime(ca["month"])
    negs = ca.salary_credit_amt < 0
    ca["income_unverified_flag"] = negs
    ca.loc[negs, "salary_credit_amt"] = np.nan
    note("DQ023", "current_account_monthly", "Set to null and flagged", negs.sum())
    s["current_account_monthly"] = ca[ca.customer_id.isin(valid_customers)].reset_index(drop=True)

    # bureau: carry forward stale records for the latest month
    bu = b["bureau_monthly"].copy()
    bu["month"] = pd.to_datetime(bu["month"])
    bu = bu[bu.customer_id.isin(valid_customers)]
    latest = m.month.max()
    have = set(bu.loc[bu.month == latest, "customer_id"])
    need = set(m.loc[m.month == latest, "customer_id"]) - have
    prev = bu[(bu.month == latest - pd.DateOffset(months=1)) & bu.customer_id.isin(need)].copy()
    prev["month"] = latest
    bu["bureau_stale_flag"] = False
    prev["bureau_stale_flag"] = True
    prev["searches_this_month"] = 0
    prev["new_accounts_this_month"] = 0
    bu = pd.concat([bu, prev], ignore_index=True)
    note("DQ050", "bureau_monthly", "Carried forward prior month (stale)", len(prev))
    s["bureau_monthly"] = bu

    # macro: forward fill missing region-months
    mac = b["macro_regional_monthly"].copy()
    mac["month"] = pd.to_datetime(mac["month"])
    grid = pd.MultiIndex.from_product([mac.region.unique(), sorted(m.month.unique())], names=["region", "month"])
    filled = mac.set_index(["region", "month"]).reindex(grid)
    stale = filled.unemployment_rate.isna()
    filled = filled.groupby(level=0).ffill().reset_index()
    filled["macro_stale_flag"] = stale.values
    note("DQ051", "macro_regional_monthly", "Forward filled", stale.sum())
    s["macro_regional_monthly"] = filled

    # notes
    nt = b["contact_notes"].copy()
    empty = nt.note_text.isna() | (nt.note_text.astype(str).str.strip() == "")
    note("DQ003", "contact_notes", "Excluded empty notes", empty.sum())
    s["contact_notes"] = nt[~empty & nt.customer_id.isin(valid_customers)].reset_index(drop=True)

    for k in ["product_holdings", "other_product_monthly", "business_monthly", "collections_events",
              "forbearance_events", "macro_forecast"]:
        df = b[k].copy()
        for col in ["month", "start_month", "as_at_month", "fix_end_month"]:
            if col in df:
                df[col] = pd.to_datetime(df[col])
        if "customer_id" in df:
            df = df[df.customer_id.isin(valid_customers)]
        s[k] = df.reset_index(drop=True)

    q = pd.concat(quarantine, ignore_index=True) if quarantine else pd.DataFrame(
        columns=["source_table", "rule_id", "reason", "record_key", "record_json"])
    return s, q, pd.DataFrame(log)


def dq_summary_facts(results, remediation):
    """Structured facts handed to the LLM (or the template) for commentary."""
    fails = results[results.status != "PASS"].sort_values(["severity", "fail_rate"], ascending=[True, False])
    return dict(
        rules_run=int(len(results)),
        passed=int((results.status == "PASS").sum()),
        failed=int((results.status == "FAIL").sum()),
        critical_failures=fails[fails.severity == "Critical"][["rule_id", "description", "records_failed", "fail_rate", "action"]].to_dict("records"),
        other_failures=fails[fails.severity != "Critical"][["rule_id", "description", "records_failed", "fail_rate", "action"]].to_dict("records"),
        remediation=remediation.to_dict("records"),
    )
