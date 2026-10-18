"""Central configuration for the retail bank Early Warning System (EWS) demo.

Everything that a modelling team might want to change sits here: table names,
population size, observation window, target definitions, risk bands and the
LLM endpoint used for commentary.
"""
import os
import pathlib

# ---------------------------------------------------------------------------
# Local platform (Early Warning Studio)
# ---------------------------------------------------------------------------
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = pathlib.Path(os.getenv("EWS_DATA_DIR", PROJECT_ROOT / "data"))
LANDING_DIR = DATA_DIR / "landing"        # files as source systems deliver them
LAKEHOUSE_DIR = DATA_DIR / "lakehouse"    # versioned parquet tables: bronze, silver, gold, ops
MODELS_DIR = DATA_DIR / "models"          # model registry
SERVING_DIR = DATA_DIR / "serving"        # what the real-time service has loaded
LAKEHOUSE_SCHEMAS = ["bronze", "silver", "gold", "ops"]

# Claude for AI commentary (read from .env or the environment)
LLM_MODEL_DEFAULT = "claude-sonnet-5-5"
LLM_EFFORT = os.getenv("EWS_LLM_EFFORT", "low")   # low keeps commentary fast; raise for deeper analysis

# ---------------------------------------------------------------------------
# Unity Catalog locations
# ---------------------------------------------------------------------------
# Databricks Free Edition ships with a catalog called `workspace`. On a full
# workspace you can point this at a dedicated catalog, e.g. "ews_demo".
CATALOG = "workspace"
SCHEMA_BRONZE = "ews_bronze"   # raw files loaded as-is
SCHEMA_SILVER = "ews_silver"   # conformed, quality-checked
SCHEMA_GOLD = "ews_gold"       # features, scores, triggers, watchlist
SCHEMA_OPS = "ews_ops"         # pipeline runs, DQ results, monitoring, thresholds
VOLUME_NAME = "landing"        # UC Volume that acts as the file landing zone


def table(schema: str, name: str) -> str:
    return f"{CATALOG}.{schema}.{name}"


def landing_path() -> str:
    return f"/Volumes/{CATALOG}/{SCHEMA_BRONZE}/{VOLUME_NAME}"


def artifacts_path() -> str:
    """UC Volume holding trained model bundles and thresholds for the live simulator."""
    return f"/Volumes/{CATALOG}/{SCHEMA_OPS}/artifacts"


# ---------------------------------------------------------------------------
# Synthetic portfolio
# ---------------------------------------------------------------------------
SEED = 2026
N_CUSTOMERS = int(os.getenv("EWS_N_CUSTOMERS", 12_000))
FIRST_MONTH = "2024-04-01"      # first month of history
LAST_MONTH = "2026-09-01"       # latest complete month (demo "today" = Oct 2026)
LIVE_START_DATE = "2026-10-01"  # live simulator starts the day after LAST_MONTH ends

# Observation months used for modelling (need 6 months of history before and
# 6 months of outcome after each observation point).
TRAIN_OBS = ("2024-10-01", "2025-09-01")     # in-time development sample
OOT_OBS = ("2025-10-01", "2026-03-01")       # out-of-time validation sample

# ---------------------------------------------------------------------------
# Target definitions (forward looking)
# ---------------------------------------------------------------------------
TARGETS = {
    "bad_6m": "Reaches 2+ missed payments (60+ days past due) or charge-off in the next 6 months",
    "arrears_3m": "Misses at least one payment in the next 3 months (currently up to date)",
    "forbearance_6m": "Enters a forbearance arrangement in the next 6 months",
    "cure_2m": "In arrears today and back to up to date within 2 months (propensity to pay)",
    "cant_pay": "Distress driven by affordability (income / cost shock) rather than willingness",
}

# ---------------------------------------------------------------------------
# Probability risk bands (6-month bad rate)
# ---------------------------------------------------------------------------
RISK_BANDS = [  # (band, lower PD bound inclusive, upper PD bound exclusive)
    ("A", 0.000, 0.005),
    ("B", 0.005, 0.010),
    ("C", 0.010, 0.025),
    ("D", 0.025, 0.050),
    ("E", 0.050, 0.100),
    ("F", 0.100, 0.200),
    ("G", 0.200, 1.001),
]

# ---------------------------------------------------------------------------
# Watchlist tiers
# ---------------------------------------------------------------------------
TIERS = {
    0: "Performing",
    1: "Monitor",
    2: "Watch",
    3: "Early intervention",
    4: "Collections",
    5: "Recoveries",
}
CURE_MONTHS = 3        # clean months needed before a customer can de-escalate a tier
CURE_DAYS_LIVE = 10    # equivalent persistence rule in the live simulator (compressed for demo pacing)

# ---------------------------------------------------------------------------
# LLM commentary
# ---------------------------------------------------------------------------
# Any chat endpoint visible under Serving in your workspace. Notebook 00 lists
# what is available; set the one you want here. If the call fails, every
# commentary function falls back to a deterministic template so the demo never
# breaks in front of an audience.
LLM_ENDPOINT = "databricks-claude-sonnet-4"
LLM_FALLBACK_ENDPOINTS = [
    "databricks-claude-sonnet-4-5",
    "databricks-claude-3-7-sonnet",
    "databricks-meta-llama-3-3-70b-instruct",
    "databricks-gpt-oss-120b",
]

# ---------------------------------------------------------------------------
# UK regions (ITL1) used for macro and geographic concentration
# ---------------------------------------------------------------------------
REGIONS = [
    "North East", "North West", "Yorkshire and The Humber", "East Midlands",
    "West Midlands", "East of England", "London", "South East", "South West",
    "Wales", "Scotland", "Northern Ireland",
]
REGION_WEIGHTS = [0.04, 0.11, 0.08, 0.07, 0.09, 0.09, 0.15, 0.14, 0.09, 0.05, 0.08, 0.01]
