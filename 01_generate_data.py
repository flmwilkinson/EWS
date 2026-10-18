# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Generate synthetic source data
# MAGIC
# MAGIC Simulates 30 months (April 2024 to September 2026) for a UK credit card book of 12,000 customers plus six named demo personas, and lands the files in the landing volume in the formats real source systems would send.
# MAGIC
# MAGIC | Source system | File | Grain | What it carries |
# MAGIC |---|---|---|---|
# MAGIC | CRM | `crm/customers.csv` | customer | age, region, segment, employment, income, housing, tenure, vulnerability flag, marketing consent |
# MAGIC | Cards | `cards/card_accounts.csv` | account | product, open date, limit at open, APR, payment method |
# MAGIC | Cards | `cards/card_monthly.parquet` | account × month | limit, statement and closing balance, purchases, cash advances, interest, fees, minimum due, payment, missed / returned payment, over limit, cycles delinquent, status |
# MAGIC | Current accounts | `current_accounts/current_account_monthly.parquet` | customer × month | salary credit, other income, salary days late, overdraft days and peak use, returned direct debits, gambling spend, BNPL repayments, payday lender flag, essential spend |
# MAGIC | Lending | `lending/product_holdings.csv`, `lending/other_product_monthly.parquet` | holding; customer × month | mortgage (fixed / tracker, fix end date), personal loans; payments due and arrears |
# MAGIC | Business banking | `business_banking/business_monthly.parquet` | sole trader × month | turnover, costs, EBITDA, debt service, DSCR, business overdraft days |
# MAGIC | Credit bureau | `bureau/bureau_monthly.psv` (pipe delimited) | customer × month | external debt and limits, active accounts, searches, new accounts, worst external status, CCJ, insolvency, bureau score |
# MAGIC | Economics team | `macro/macro_regional_monthly.csv`, `macro/macro_forecast.csv` | region × month; region × scenario | unemployment, house prices, CPI, Bank Rate, wage growth; 12-month base / downside / upside outlook |
# MAGIC | Collections | `collections/collections_events.csv`, `collections/forbearance_events.csv` | account × month; arrangement | contact attempts, right-party contact, promise to pay and whether kept, agent-recorded arrears reason; forbearance type and length |
# MAGIC | Contact centre | `contact_centre/contact_notes.jsonl` | note | free-text agent notes (hardship, vulnerability, refusal, servicing) |
# MAGIC
# MAGIC The generator follows a causal chain so early warning signals genuinely lead defaults: **shock** (job loss, income cut, mortgage refix, illness, gambling, business downturn, bust-out) → **savings buffer runs down** → **the gap is borrowed** (card spend, overdraft, cash advances, new credit) → **utilisation, searches and returned direct debits rise** → **external arrears** → **internal arrears** → **60+ days past due**. Some defaults are sudden with no warning, so no model is perfect.
# MAGIC
# MAGIC It also injects deliberate data defects (duplicates, missing incomes, zero limits, orphan rows, a late bureau file, a missing macro value) for notebook 03 to find.

# COMMAND ----------

# MAGIC %pip install -q lightgbm scikit-learn

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys, time
sys.path.append(os.getcwd())
import pandas as pd
from ews_lib import config as C, generator
from ews_lib.dbx import log_step

NB = "01_generate_data"

# COMMAND ----------

with log_step(spark, "generate_synthetic_portfolio", NB) as rec:
    t0 = time.time()
    raw = generator.generate_all(n_customers=C.N_CUSTOMERS, seed=C.SEED, with_defects=True)
    written = generator.write_landing(raw, C.landing_path())
    rec["rows_written"] = sum(n for _, n in written.values())
    print(f"Generated and landed in {time.time() - t0:.0f}s")

display(pd.DataFrame([(k, p.replace(C.landing_path() + "/", ""), n) for k, (p, n) in written.items()],
                     columns=["dataset", "file", "rows"]))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Files in the landing volume

# COMMAND ----------

display(dbutils.fs.ls(C.landing_path()))

# COMMAND ----------

# MAGIC %md
# MAGIC ## The six demo personas
# MAGIC Their history is clean. In notebook 09 the live simulator plays their stories forward day by day.

# COMMAND ----------

display(raw["personas"])

# COMMAND ----------

# MAGIC %md
# MAGIC ## A quick look at the book over time

# COMMAND ----------

cm = raw["card_monthly"]
trend = (cm.groupby("month").agg(avg_utilisation=("utilisation", "mean"),
                                 in_arrears=("cycles_delinquent", lambda x: (x > 0).mean()),
                                 sixty_plus=("cycles_delinquent", lambda x: (x >= 2).mean()))
           .reset_index())
display(trend)

# COMMAND ----------

# MAGIC %md
# MAGIC **Next:** `02_load_bronze`.
