# Early Warning Studio: build plan

> **Status (18 October 2026): built and verified.** Every page below exists. A full pipeline run from an empty `data/` folder succeeds through the app (11 tasks, about 3½ minutes, 32 Claude calls). The live month plays all 31 days with the personas following their stories. Editing `MAX_GINI_GAP` and using **Run from here** swaps the champion to the scorecard and back, and the real-time service reloads each time. See `README.md` for how to run it and the recording script.

A local, Databricks-style platform for a real-time retail bank early warning (EWI) demo. One Python process serves a web app with two halves:

- **Under the hood (for modelling and engineering teams):** a workspace of editable pipeline notebooks, a job runner with a live DAG (red crosses turn into green ticks), run history, a lakehouse catalog, a SQL editor, a model registry and data connectors.
- **The EWS itself (for credit risk and the business):** portfolio dashboards, a real-time event stream that escalates customers through the watchlist, drill-downs to a single customer, decisioning across six use cases, data quality and model monitoring, all with AI-written commentary from Claude.

No Streamlit, no Databricks, no Spark. Synthetic data only.

---

## 1. What the recording needs to show

| # | Moment | Where in the app |
|---|---|---|
| 1 | The data sources and how they connect (stream, CDC, REST, SFTP batch, webhooks), plus outbound action feeds | **Connectors** |
| 2 | The pipeline as code: open a notebook, read it, change a parameter, save | **Workspace** |
| 3 | Run the pipeline: each task flips from ✕ to a spinner to ✓, cells tick inside the running notebook, logs stream | **Workflows** |
| 4 | Past runs: table and matrix of task outcomes, durations, rows written; drill into any run | **Workflows → Runs** |
| 5 | Lakehouse tables (bronze, silver, gold, ops) with schema, sample rows, versions, lineage; ad hoc SQL | **Catalog**, **SQL** |
| 6 | Data quality results and an AI commentary on fitness for scoring | **Data quality** |
| 7 | Six models compared (weighted score, logistic scorecard, tree, forest, LightGBM, MLP): Gini, KS, calibration, stability, explainability; champion rule; AI commentary | **Models** |
| 8 | Portfolio view: tiers, bands, movements, regions, balance at risk, AI executive summary | **Overview** |
| 9 | Real-time: events arrive from connectors, customers are re-scored, triggers fire, tiers escalate, alerts carry AI summaries; inject an event through the API | **Live** |
| 10 | Drill-down: watchlist → customer 360 (history, triggers vs thresholds, model drivers, secondary models, can't pay vs won't pay, notes, decisions, AI explanation) | **Watchlist**, **Customer** |
| 11 | One set of signals, six consumers: limits and activation, pricing, exposure, offers, support, collections | **Decisioning** |
| 12 | Model and EWS monitoring: RAG against appetite, Gini and PSI over time, CSI, lead time, transitions, AI commentary | **Monitoring** |
| 13 | Real-time scoring and event APIs (OpenAPI), and an AI analyst that answers questions with SQL | **APIs**, **Ask AI** |

---

## 2. Architecture

```
browser (Vue 3, ECharts, CodeMirror; all vendored, no build step)
   │  REST /api/*        SSE /api/stream/runs, /api/stream/live, /api/ai/*
   ▼
FastAPI server (server/)
   ├── job runner ──spawns──► kernel subprocess per task (server/kernel.py)
   │                             executes pipeline/NN_*.py cell by cell,
   │                             emits JSON events: cell start/end, stdout,
   │                             display(table), table written, LLM call
   ├── live service: LiveSimulator in a background thread, paced by wall clock
   ├── AI service: Claude via the Anthropic SDK, streamed to the browser
   ├── metadata: SQLite (runs, task runs, cell outputs, revisions, LLM audit log)
   └── lakehouse reader: DuckDB over parquet
                    ▲
ews_lib/ (shared engine, used by notebooks and server)
   generator, dq, features, thresholds, models, triggers, watchlist,
   actions, scoring, monitoring, commentary, live
   + lakehouse.py (versioned parquet tables), registry.py (model registry), llm.py (Claude)
```

### Folder layout

```
ews_demo/
  PLAN.md, README.md, requirements.txt, start.ps1, .env (yours, not committed)
  ews_lib/            engine (existing, patched for local use and pandas 3)
  pipeline/           the notebooks the app shows, edits and runs (# %% cells)
  server/             FastAPI app, job runner, kernel, live service, AI service
  web/                index.html, css/, js/ (pages and components), vendor/
  data/               runtime only: landing/, lakehouse/, models/, meta.db
  00_setup.py … app/  original Databricks version, left untouched
```

### Key design choices

- **Notebooks are plain `.py` files with `# %%` cell markers.** The app renders markdown cells and code cells, edits them in CodeMirror and saves back to disk with a revision history. The same files run from the command line.
- **The kernel runs each task in its own subprocess.** Cells execute in order in one namespace; each cell reports status, duration, printed output and `display()` tables. A failure stops the notebook and marks downstream tasks as skipped. Edits take effect on the next run because the file is re-read.
- **The lakehouse is versioned parquet** (`data/lakehouse/<schema>/<table>/v0007.parquet` plus a JSON log). Every write records the run and task that produced it, which gives lineage and table history. DuckDB queries it for dashboards and the SQL editor without file locks.
- **The model registry** stores each trained bundle with metrics, features and stage (champion or challenger). The publish task promotes the champion to the real-time service, which reloads it.
- **Real time is a simulation, presented honestly.** The live service plays October 2026 forward one business day at a time (default 8 seconds per day), spreads each day's events across that interval, re-scores touched customers with the champion model, re-evaluates triggers and moves tiers. Alerts follow the event that caused them. The UI labels data and connectors as synthetic.
- **Claude for commentary, always with a fallback.** Every AI panel streams from Claude (`ANTHROPIC_MODEL`, default `claude-sonnet-5-5`), shows model, latency and tokens, and can show the exact prompt. Facts are computed in Python and passed as JSON; the model is told to use only those facts. If the key is missing or a call fails, the deterministic template text appears instead, so the demo never stalls. Every call is logged for audit.

---

## 3. Pipeline (Workflows DAG)

| Task | Notebook | Reads | Writes | Depends on |
|---|---|---|---|---|
| Generate source data | `01_generate_data` | config | `data/landing/*` in source formats (CSV, PSV, parquet, JSONL) | none |
| Ingest to bronze | `02_ingest_bronze` | landing files via connector definitions | `bronze.*` with `_source_file`, `_loaded_at` | 01 |
| Data quality and conform | `03_data_quality` | `bronze.*` | `ops.dq_results`, `ops.dq_remediation_log`, `silver.*`, `silver.dq_quarantine` | 02 |
| DQ commentary (AI) | `04_dq_commentary` | `ops.dq_*` | `ops.dq_commentary` | 03 |
| Feature engineering | `05_feature_engineering` | `silver.*` | `gold.customer_month_features`, `gold.feature_dictionary`, `silver.contact_notes_scored` | 03 |
| Seed thresholds | `06_seed_thresholds` | features | `ops.kri_thresholds`, `ops.trigger_effectiveness` | 05 |
| Train and compare models | `07_train_models` | features, thresholds | registry bundles; `gold.model_comparison`, `gold.model_roc`, `gold.model_deciles`, `gold.feature_importance`, `gold.scorecard_points`, `gold.secondary_models`, commentary | 06 |
| Score, trigger, watchlist, decide | `08_score_watchlist` | features, registry | `gold.ews_scores_monthly`, `gold.ews_triggers_fired`, `gold.ews_decisions_latest`, `gold.customer_summaries` | 07 |
| Build monitoring | `09_monitoring` | scores | `ops.monitoring_metrics`, `ops.monitoring_performance`, `ops.monitoring_stability`, `ops.monitoring_csi`, `ops.ew_lead_times`, `ops.watchlist_transitions` | 08 |
| Monitoring commentary (AI) | `10_monitoring_commentary` | monitoring tables | `ops.monitoring_commentary` | 09 |
| Publish to real-time | `11_publish_realtime` | registry, decisions | serving manifest; live service reloads | 08 |

DQ commentary runs in parallel with feature engineering, and publish runs in parallel with monitoring, so the DAG shows real branching.

Status icons: **✕ red outline** not yet run this run, **spinner** running, **✓ green** succeeded, **✕ solid red** failed (with the error), **– grey** skipped because something upstream failed.

---

## 4. Server API

| Area | Endpoints |
|---|---|
| Workspace | `GET /api/workspace/tree`, `GET/PUT /api/workspace/file`, `GET /api/workspace/revisions`, `POST /api/workspace/revert` |
| Jobs | `GET /api/jobs/pipeline` (DAG), `POST /api/runs` (all, one task, or from a task), `GET /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/tasks/{task}`, `POST /api/runs/{id}/cancel`, `GET /api/runs/matrix` |
| Streams | `GET /api/stream/runs` (run, task and cell events), `GET /api/stream/live` (events, alerts, KPIs, clock) |
| Catalog and SQL | `GET /api/catalog`, `GET /api/catalog/{schema}/{table}` (schema, sample, history, lineage), `POST /api/sql` |
| Models | `GET /api/models`, `GET /api/models/comparison`, `GET /api/models/roc`, `GET /api/models/deciles`, `GET /api/models/importance`, `GET /api/models/scorecard` |
| EWS | `GET /api/ews/overview`, `GET /api/ews/watchlist`, `GET /api/ews/customer/{id}`, `GET /api/ews/triggers`, `GET /api/ews/decisions`, `GET /api/ews/dq`, `GET /api/ews/monitoring` |
| Live | `POST /api/live/start`, `/pause`, `/reset`, `/speed`; `GET /api/live/state` |
| Real-time APIs | `POST /api/v1/score`, `POST /api/v1/events`, `GET /api/v1/customers/{id}/decision` (documented at `/docs`) |
| Connectors | `GET /api/connectors`, `POST /api/connectors/{id}/test` (the Claude connector makes a real call) |
| AI | `POST /api/ai/commentary/{kind}` (streams), `POST /api/ai/customer/{id}` (streams), `POST /api/ai/ask` (analyst with a read-only SQL tool), `GET /api/ai/log` |

---

## 5. Front end

Light, enterprise look (dark mode available): dark left rail, Inter for text, JetBrains Mono for code, one accent colour, a fixed tier palette (Performing green, Monitor lime, Watch amber, Early intervention orange, Collections red, Recoveries purple). Top bar shows the lakehouse, champion model version, live clock and LLM status.

Navigation:

- **Platform:** Workflows · Workspace · Catalog · SQL · Models · Connectors · APIs
- **Early warning:** Overview · Live · Watchlist · Triggers · Decisioning · Data quality · Monitoring · Ask AI
- **Customer 360** opens from any customer ID anywhere in the app.

Shared components: status icon, KPI tile, card, sortable and paginated table with clickable rows, ECharts wrapper with one theme, tier badge, AI panel (streaming markdown, model and token footer, regenerate, view prompt), CodeMirror cell editor, tabs, drawer, toasts.

---

## 6. Build order

1. **Engine fixes:** run `scripts/run_local.py` on this machine's stack (pandas 3, NumPy 2), fix what breaks, add `lakehouse.py`, `registry.py`, `llm.py`.
2. **Pipeline notebooks:** port the Databricks notebooks 01 to 09 to local `# %%` notebooks against the lakehouse; add the two AI commentary tasks and the publish task; extra outputs for the charts (ROC points, deciles, importance).
3. **Server:** kernel protocol, job runner with DAG and parallel branches, SQLite metadata, SSE, workspace and catalog APIs.
4. **Front end shell and platform pages:** Workflows (DAG, runs, matrix, task detail with cells), Workspace (tree, notebook editor, revisions), Catalog, SQL.
5. **EWS pages:** Overview, Watchlist, Customer 360, Triggers, Decisioning, Data quality, Models, Monitoring.
6. **Real time:** live service, Live page, connectors with live throughput and sample payloads, scoring and event APIs, event injector.
7. **AI:** streaming commentary everywhere, customer explanations, alert summaries, Ask AI with a SQL tool, audit log.
8. **Polish and verify:** full pipeline run from clean, every page checked with data and without, timings for the recording, README with a demo script.

---

## 7. Demo script (target 12 to 15 minutes)

1. **Connectors** (1 min): sources, protocols, cadence; outbound feeds to decision engine, CRM, collections and marketing suppression. Test the Claude connector live.
2. **Workspace** (2 min): open `05_feature_engineering`; levels, changes, relatives, interactions. Open `07_train_models`; the six approaches and the champion rule. Change one setting (for example the champion Gini tolerance) and save.
3. **Workflows** (3 min): run the pipeline. Watch the DAG fill with ticks; click into the training task and watch cells complete with live output. Show the run history and matrix.
4. **Data quality → Models** (2 min): failed rules, remediation, AI commentary; scorecard versus machine learning, calibration, explainability, why the champion won.
5. **Overview → Live** (3 min): start the stream. Events arrive by source; Priya's salary stops and she escalates; Dan's gambling pushes him towards won't pay; inject a bureau alert for Mark through the API and watch him move.
6. **Watchlist → Customer 360** (2 min): Priya's history, triggers against thresholds, model drivers, can't pay lean, notes, decisions, the AI explanation.
7. **Decisioning → Monitoring → Ask AI** (2 min): the same signals drive six business uses; RAG pack with commentary; ask a question in plain English.

---

## 8. Definition of done

- `start.ps1` (or `python -m server`) starts the app on a clean machine with Python 3.11 and the packages in `requirements.txt`.
- A pipeline run from an empty `data/` folder succeeds end to end, and every task, cell and table write appears in the UI as it happens.
- Every page renders without data (with a prompt to run the pipeline) and with data.
- AI panels stream from Claude when the key is set and fall back to templates when it is not.
- The live stream runs for the full 31 days without errors, and the personas follow their stories.
- Editing a notebook and re-running it changes the result and leaves a revision.
