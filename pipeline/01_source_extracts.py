# %% [markdown]
# # 01 · Source extracts
#
# Lands 30 months (April 2024 to September 2026) of month-end extracts for a UK credit card book of **12,000
# customers**, one extract per source system, in the format each system delivers. Files land in `data/landing/`;
# the ingestion task reads them through the connector definitions.
#
# | Source system | Landing file | Grain | What it carries |
# |---|---|---|---|
# | CRM | `crm/customers.csv` | customer | age, region, segment, employment, income, housing, tenure, vulnerability, consent |
# | Cards | `cards/card_accounts.csv`, `cards/card_monthly.parquet` | account; account × month | limit, balances, spend, cash, interest, fees, minimum due, payment, missed / returned payment, over limit, cycles delinquent |
# | Current accounts | `current_accounts/current_account_monthly.parquet` | customer × month | salary, other income, lateness, overdraft days and peak use, returned direct debits, gambling, BNPL, payday lender |
# | Lending | `lending/product_holdings.csv`, `lending/other_product_monthly.parquet` | holding; customer × month | mortgages (fixed or tracker, fix end date), personal loans, payments due, arrears |
# | Business banking | `business_banking/business_monthly.parquet` | sole trader × month | turnover, costs, EBITDA, debt service, DSCR, business overdraft days |
# | Credit bureau | `bureau/bureau_monthly.psv` | customer × month | external debt and limits, accounts, searches, new accounts, worst external status, CCJ, score |
# | Economics | `macro/macro_regional_monthly.csv`, `macro/macro_forecast.csv` | region × month; region × scenario | unemployment, house prices, CPI, Bank Rate, wages; base, downside and upside outlook |
# | Collections | `collections/collections_events.csv`, `collections/forbearance_events.csv` | account × month; arrangement | contact attempts, right-party contact, promises to pay, arrears reason, forbearance |
# | Contact centre | `contact_centre/contact_notes.jsonl` | note | free-text agent notes |
#
# **How distress builds up**, and why early warning signals lead defaults:
# shock (job loss, income cut, mortgage refix, illness, gambling, business downturn, bust-out) → savings run down →
# the gap is borrowed (card, overdraft, cash, new credit) → utilisation, searches and returned direct debits rise →
# arrears with other lenders → arrears with us → 60+ days past due. Some defaults come without warning, so no model
# is perfect.
#
# The extracts carry the kinds of defect real feeds carry (duplicates, missing incomes, zero limits, orphan rows,
# a late bureau file, a missing macro value), which the data quality task must find and remediate.

# %% Settings
import time

import pandas as pd

from ews_lib import config as C, generator
from ews_lib.runtime import chart, display, metrics

N_CUSTOMERS = C.N_CUSTOMERS   # book size
SEED = C.SEED                 # extract build seed
print(f"Building extracts for {N_CUSTOMERS:,} customers, {C.FIRST_MONTH} to {C.LAST_MONTH}")

# %% Build 30 months of portfolio history
t0 = time.time()
raw = generator.generate_all(n_customers=N_CUSTOMERS, seed=SEED, with_defects=True)
cm = raw["card_monthly"]
metrics({"Customers": f"{raw['customers'].customer_id.nunique():,}", "Months": cm.month.nunique(),
         "Card statements": f"{len(cm):,}", "Contact notes": f"{len(raw['contact_notes']):,}",
         "Seconds": round(time.time() - t0, 1)})

# %% Land the files in their source formats
written = generator.write_landing(raw, C.LANDING_DIR)
landed = pd.DataFrame([(k, generator.LANDING_FORMATS[k][0].upper(), generator.LANDING_FORMATS[k][1], n)
                       for k, (p, n) in written.items() if not k.startswith(("generator_truth", "personas"))],
                      columns=["dataset", "format", "landing_file", "rows"])
display(landed, title="Files landed in data/landing")

# %% [markdown]
# ## Customers in focus
# Six customers the monitoring team follows closely through October.

# %% Customers in focus
display(raw["personas"][["customer_id", "name", "segment", "region", "employment", "income", "housing"]],
        title="Customers in focus")

# %% How the book has evolved
trend = (cm.groupby("month").agg(avg_utilisation=("utilisation", "mean"),
                                 in_arrears=("cycles_delinquent", lambda x: (x > 0).mean()),
                                 sixty_plus=("cycles_delinquent", lambda x: (x >= 2).mean()))
           .reset_index())
trend["month"] = pd.to_datetime(trend.month).dt.strftime("%b %y")
chart("line", trend.month, {"In arrears": trend.in_arrears, "60+ days past due": trend.sixty_plus},
      title="Arrears rates across the book", y_format="pct")
chart("line", trend.month, {"Average utilisation": trend.avg_utilisation}, title="Average card utilisation", y_format="pct")
