"""Source and sink connectors: one definition used by ingestion, the live feed and the UI.

Each inbound connector says where a source system's data comes from (protocol,
endpoint, cadence), which landing files it delivers for the monthly batch and
which live event sources it carries in real time. Outbound connectors are where
EWS decisions go: the credit decision engine, CRM, collections and marketing.

In this demo every connector is simulated: batch files come from the synthetic
generator and live events from the live simulator. The definitions are written
the way a client build would configure real ones.
"""
from __future__ import annotations

INBOUND = [
    dict(id="crm", name="CRM customer master", system="Customer relationship management", protocol="REST API",
         endpoint="GET https://crm.internal/api/v3/customers?updated_since={watermark}", auth="OAuth2 client credentials",
         cadence="Daily delta, 02:00", format="JSON → CSV extract", tables=["customers"], live_sources=[],
         fields=["customer_id", "age", "region", "segment", "employment_type", "gross_income_annual", "housing_status",
                 "tenure_months", "vulnerability_flag", "marketing_consent"],
         sample={"customer_id": "C900001", "segment": "Mass market", "region": "West Midlands", "employment_type": "Employed full-time",
                 "gross_income_annual": 36000, "vulnerability_flag": False, "marketing_consent": True, "updated_at": "2026-10-01T02:00:11Z"}),
    dict(id="cards", name="Card processing platform", system="Cards (authorisations, statements, payments)", protocol="Kafka stream + batch",
         endpoint="kafka://cards.transactions.v1 · sftp://cards/statements/YYYYMM.parquet", auth="mTLS",
         cadence="Real time (transactions); monthly statements", format="Avro events; Parquet statements",
         tables=["card_accounts", "card_monthly"], live_sources=["Cards"],
         fields=["account_id", "credit_limit", "statement_balance", "purchases", "cash_advance_amt", "minimum_due",
                 "payment_amount", "missed_payment_flag", "overlimit_flag", "cycles_delinquent"],
         sample={"topic": "cards.transactions.v1", "partition": 3, "offset": 88412207, "key": "A900001",
                 "value": {"event": "AUTHORISATION", "account_id": "A900001", "amount_gbp": 250.00, "mcc": "6011",
                           "merchant": "ATM withdrawal", "channel": "ATM", "ts": "2026-10-08T13:42:10Z"}}),
    dict(id="current_account", name="Current account ledger", system="Core banking (current accounts)", protocol="CDC stream",
         endpoint="kafka://corebanking.ledger.cdc (Debezium)", auth="mTLS",
         cadence="Real time (ledger postings); monthly aggregates", format="JSON change events",
         tables=["current_account_monthly"], live_sources=["Current account"],
         fields=["salary_credit_amt", "salary_days_late", "overdraft_days", "overdraft_peak_util", "returned_dd_count",
                 "gambling_spend", "bnpl_repayments", "payday_lender_payment_flag"],
         sample={"op": "c", "source": {"table": "ledger_postings", "lsn": 99821331},
                 "after": {"account": "CA-900002", "posting_type": "DEBIT", "amount_gbp": 120.00, "counterparty_category": "Gambling",
                           "balance_after": -310.55, "posted_at": "2026-10-08T21:14:03Z"}}),
    dict(id="lending", name="Mortgages and loans", system="Lending platform", protocol="REST API",
         endpoint="GET https://lending.internal/api/v2/holdings", auth="OAuth2 client credentials",
         cadence="Daily", format="JSON", tables=["product_holdings", "other_product_monthly"], live_sources=["Mortgages"],
         fields=["product_type", "rate_type", "fix_end_month", "balance", "monthly_payment", "other_product_cycles_delinquent"],
         sample={"customer_id": "C900003", "product_type": "Mortgage", "rate_type": "Fixed", "original_rate_pct": 1.69,
                 "fix_end_month": "2026-12-01", "reversion_rate_pct": 4.89, "monthly_payment": 1160.0}),
    dict(id="business", name="Business banking", system="Business current accounts and loans", protocol="REST API",
         endpoint="GET https://business.internal/api/v1/cashflow", auth="OAuth2 client credentials",
         cadence="Weekly takings; monthly accounts", format="JSON", tables=["business_monthly"], live_sources=["Business banking"],
         fields=["turnover", "operating_costs", "ebitda", "debt_service", "dscr", "business_overdraft_days"],
         sample={"customer_id": "C900004", "week_ending": "2026-10-16", "takings_gbp": 4120, "takings_vs_4wk_avg": -0.31, "dscr_estimate": 0.92}),
    dict(id="bureau", name="Credit reference agency", system="External credit bureau", protocol="SFTP batch + webhook",
         endpoint="sftp://cra/inbound/bureau_YYYYMM.psv · POST /webhooks/cra-alerts", auth="SSH key; HMAC-signed webhooks",
         cadence="Monthly full file; daily trigger alerts", format="Pipe-delimited file; JSON alerts",
         tables=["bureau_monthly"], live_sources=["Credit bureau"],
         fields=["total_external_debt", "active_external_accounts", "searches_this_month", "new_accounts_this_month",
                 "worst_external_status", "ccj_flag", "bureau_score"],
         sample={"alert_type": "ARREARS_REPORTED", "subject_ref": "C900006", "lender_type": "Personal loan",
                 "status_from": 0, "status_to": 1, "score_change": -55, "reported_at": "2026-10-05T06:00:00Z"}),
    dict(id="macro", name="Economics team data mart", system="Internal economics: regional macro history and forward scenarios",
         protocol="SFTP batch", endpoint="sftp://economics/outbound/regional_macro_YYYYMM.csv · scenarios_YYYYQn.csv",
         auth="SSH key", cadence="Monthly; scenarios quarterly", format="CSV", tables=["macro_regional_monthly", "macro_forecast"],
         live_sources=[], fields=["region", "unemployment_rate", "hpi_yoy_pct", "cpi_yoy_pct", "bank_rate_pct", "scenario",
                                  "scenario_weight", "unemp_change_12m_pp"],
         sample={"region": "North East", "month": "2026-09", "unemployment_rate": 7.4, "bank_rate_pct": 3.75,
                 "scenario": "Downside", "scenario_weight": 0.3, "unemp_change_12m_pp": 2.5}),
    dict(id="collections", name="Collections platform", system="Collections and recoveries", protocol="Webhook events",
         endpoint="POST /webhooks/collections", auth="HMAC-signed webhooks",
         cadence="Real time", format="JSON", tables=["collections_events", "forbearance_events"], live_sources=[],
         fields=["contact_attempts", "right_party_contact", "promise_to_pay", "ptp_kept", "arrears_reason", "arrangement_type"],
         sample={"event": "PROMISE_TO_PAY", "account_id": "A004417", "amount_gbp": 85.0, "due": "2026-10-23", "agent_id": "AG-1182"}),
    dict(id="contact_centre", name="Contact centre notes", system="Telephony, chat and complaints", protocol="Event stream",
         endpoint="kafka://contact.interactions.v1", auth="mTLS", cadence="Real time", format="JSON lines",
         tables=["contact_notes"], live_sources=["Contact centre"],
         fields=["note_id", "customer_id", "created_ts", "channel", "direction", "note_text"],
         sample={"note_id": "N0091022", "customer_id": "C900001", "channel": "Phone", "direction": "Inbound",
                 "note_text": "Customer called: made redundant last month, looking for work..."}),
]

OUTBOUND = [
    dict(id="decision_engine", name="Credit decision engine", system="Approvals, limits and activation", protocol="REST API",
         endpoint="PUT https://decisioning.internal/api/v1/customer-strategy/{customer_id}", auth="OAuth2 client credentials",
         cadence="On tier change", format="JSON", use_cases=["limit_and_activation", "pricing", "exposure"],
         sample={"customer_id": "C900001", "ews_tier": 3, "limit_strategy": "Freeze limit; no new lending",
                 "pricing_strategy": "No upward re-pricing", "cash_advance_block": True, "effective_from": "2026-10-17T21:00:00Z"}),
    dict(id="crm_actions", name="CRM next best action", system="Engagement and customer contact", protocol="Kafka",
         endpoint="kafka://ews.actions.v1", auth="mTLS", cadence="On tier change", format="JSON",
         use_cases=["support", "offers_engagement"],
         sample={"customer_id": "C900001", "action": "PROACTIVE_CALL", "reason": "Salary not received; hardship disclosed",
                 "script": "Income and expenditure, reduced payment plan, signpost free debt advice", "priority": "High"}),
    dict(id="collections_out", name="Collections strategy", system="Dialler and treatment paths", protocol="REST API",
         endpoint="POST https://collections.internal/api/v1/treatments", auth="OAuth2 client credentials",
         cadence="On tier change", format="JSON", use_cases=["collections"],
         sample={"account_id": "A900002", "treatment": "Escalated contact strategy", "pay_lean": "Won't pay",
                 "propensity_to_pay": 0.21, "contact_within_hours": 48}),
    dict(id="marketing", name="Marketing suppression", system="Campaign management", protocol="SFTP file",
         endpoint="sftp://marketing/inbound/ews_suppression_YYYYMMDD.csv", auth="SSH key", cadence="Daily, 05:00",
         format="CSV", use_cases=["offers_engagement"],
         sample={"customer_id": "C900002", "suppress_credit_offers": True, "eligible_offers": [], "reason": "EWS tier 2"}),
]

AI = dict(id="claude", name="Anthropic Claude", system="AI commentary and explanations", protocol="HTTPS API",
          endpoint="POST https://api.anthropic.com/v1/messages", auth="API key (from .env)", cadence="On demand",
          format="JSON (streamed)")


def by_id():
    return {c["id"]: c for c in INBOUND + OUTBOUND}


def connector_for_table(table: str):
    for c in INBOUND:
        if table in c["tables"]:
            return c
    return None


LIVE_SOURCE_TO_CONNECTOR = {s: c["id"] for c in INBOUND for s in c["live_sources"]}
