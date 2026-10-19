# Early Warning Studio

A local, Databricks-style platform demonstrating a real-time early warning system (EWS) for a UK credit card book. One Python process serves a web app with two halves:

- **Under the hood**, for modelling and engineering teams: editable pipeline notebooks, a job runner whose DAG flips from red crosses to green ticks as tasks run, run history and run comparison, a versioned lakehouse catalog with lineage, a SQL editor, a model registry, connectors and real-time APIs.
- **The EWS itself**, for credit risk and the business: portfolio overview, a live event stream that re-scores customers and escalates them through the watchlist, Customer 360 drill-downs, triggers and KRIs, decisioning across six business uses, data quality and model monitoring. AI (Claude, through the Anthropic API) writes the commentary throughout.

The bank's own systems (cards, current accounts, bureau, collections and so on) are represented by a calibrated synthetic portfolio. The official macroeconomic connectors (Bank of England, ONS, HM Land Registry) and the Claude connection are real, live calls.

## Quick start

```powershell
.\start.ps1 -Install        # first time: installs requirements.txt, then starts the app
.\start.ps1                 # afterwards
```

Or `pip install -r requirements.txt` then `python -m server`, and open http://127.0.0.1:8765.

For AI commentary, copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` (the model defaults to `claude-sonnet-5-5`). Without a key every AI panel falls back to template text, so the demo still runs.

On first launch the dashboards are empty. Open **Workflows** and press **Run pipeline**. A full run takes about 3½ minutes and makes about 30 Claude calls, and the real-time service loads the published models when it finishes.

## What is where

| Folder | Contents |
|---|---|
| `pipeline/` | The 11 notebooks the app shows, edits and runs: plain `.py` files with `# %%` cells |
| `ews_lib/` | The engine: generator, data quality, features, thresholds, six early warning models plus secondary models, triggers, watchlist, decisions, monitoring, live simulator, lakehouse, registry, Claude client |
| `server/` | FastAPI app, job runner, cell-by-cell kernel, live service, AI endpoints, real-time API |
| `web/` | Front end (Vue 3, ECharts, CodeMirror; all vendored, so it runs offline apart from Claude) |
| `data/` | Created at run time: landing files, lakehouse tables, model registry, metadata database |
| `scripts/run_pipeline.py` | Runs the notebooks from the command line through the same kernel |
| `scripts/record_demo.py` | Records the two-minute demo video (see `RECORDING.md`) |
| `00_setup.py` … `09_live_simulator.py`, `app/` | The original Databricks notebooks and Streamlit app, unchanged |
| `PLAN.md` | The build plan and the full demo storyline |

## Pages

| Platform | Early warning | AI |
|---|---|---|
| **Workflows**: DAG, run history and matrix, compare runs, live task detail | **Portfolio overview**: ladder, trends, regions, executive summary | **Ask the EWS**: questions answered with read-only SQL, queries shown |
| **Workspace**: edit and run notebooks, revisions | **Live monitor**: events, alerts, personas, inject events | **AI audit log**: every call with prompt, response, tokens, latency |
| **Catalog**: tables, versions, lineage | **Watchlist** → **Customer 360** | |
| **SQL editor** | **Triggers and KRIs**: policy vs calibrated thresholds | |
| **Models**: scorecard vs machine learning, registry | **Decisioning**: six business uses | |
| **Connectors**: sources in, actions out, live official macro APIs | **Data quality**, **Model monitoring** | |
| **Real-time APIs**: try-it console, request log, `/docs` | | |

## How the models are built

- **Target labels from historical risk buckets.** Every card account sits in a risk bucket each month: up to date, 1, 2 or 3+ cycles delinquent, or charged off. Each customer-month is labelled with the worst bucket reached in the following six months, and the event is bucket 2+ (60+ days past due) or charge-off. The bucket table is shown in `07_train_models.py`.
- **Monthly and real-time inputs.** Monthly: the card statement, the bureau refresh, other products and macro. Real-time: the signals the live service updates as events arrive (current account transactions, card cash advances, bureau triggers and contact centre notes; `REALTIME_SIGNALS` in `ews_lib/features.py`). The same models re-score customers between month ends.
- **Six approaches compared out of time** (weighted scoring, logistic scorecard, decision tree, random forest, LightGBM, neural network), a champion rule, and secondary models for arrears, forbearance, propensity to pay and can't pay versus won't pay.
- **Compare runs.** Each run records the notebook code it ran and a summary of the results when it finishes. **Workflows → Compare** shows the code diff between two runs and the model, watchlist, early warning and data quality results side by side. To see what real-time data adds, set `USE_REALTIME_SIGNALS = False` in `07_train_models.py`, run from that task, set it back and run again.

## Recording

**`RECORDING.md`** has the two-minute shot list, voiceover lines and pre-flight checklist. A recorded take (with and without captions) is in `recordings/`, and `python scripts/record_demo.py` re-records it.

### Full walkthrough (12–15 minutes)

For a live session rather than a video. Before you start: in **Workflows**, press **Reset environment** and tick "Also clear run history", so you start from all red crosses. Use a browser window around 1600–1920 px wide.

1. **Connectors** (1 min). Walk the flow strip: nine sources (Kafka, CDC, REST, SFTP, webhooks) → lakehouse and engine → real-time service → four action feeds. Open a sample payload. Press **Test live call** on the Claude card.
2. **Workflows** (1 min). Eleven tasks, all red crosses. Press **Run pipeline**, then go to the Workspace while it runs.
3. **Workspace** (2 min). Open `05_feature_engineering.py` while it runs: cells tick green with their tables and charts appearing underneath. Then open `07_train_models.py`: the six approaches and the champion rule.
4. **Workflows** (1 min). Back to the DAG as the last ticks land. Click **Train and compare models**: cells, durations, tables read and written. Show the run history, the **Matrix** view and **Compare**: pick two runs to see the code that changed between them and the results side by side.
5. **Data quality → Models** (2 min). The failed rules, remediation and the AI commentary; per-rule "why it matters". On Models: scorecard against machine learning, the neural network's overfitting, ROC and calibration, champion drivers, scorecard points, the registry.
6. **Live-edit moment, optional** (3 min of run time; cut it in the edit). In `07_train_models.py` set `MAX_GINI_GAP = 0.02`, save, press **Run from here**. The scorecard becomes champion, everything downstream re-runs and the real-time service reloads it. Revert from **Revisions** and run from here again.
7. **Portfolio overview → Live monitor** (3 min). Press **Start live feed** (Normal speed). Events arrive by source; personas escalate: Priya's salary stops, Dan's gambling grows, Grace's fixed rate ends, Lee's café struggles, Mark is reported by another lender, Sarah stays healthy. Persona alerts carry Claude explanations as they happen. Use **Inject an event** to send a bureau alert for Mark through `POST /api/v1/events`.
8. **Customer 360** (2 min). Click Priya or Dan: AI explanation, six decisions, model outputs, risk tier history, drivers, every trigger against its thresholds, October events. Press **Score now via API**.
9. **Decisioning → Monitoring → Ask the EWS** (2 min). The decision matrix; the RAG pack and lead time; ask "Which regions have the most customers in Early intervention, and what share lean can't pay?"
10. **AI audit log** (30 s). Every AI sentence is traceable to its prompt and facts.

## Configuration

Set in `.env` or the environment.

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | none | Enables Claude; without it, template text is used |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | Model for commentary, explanations and Ask the EWS |
| `EWS_LLM_EFFORT` | `low` | Effort for commentary; Ask the EWS uses `medium` |
| `EWS_N_CUSTOMERS` | `12000` | Book size (plus six personas) |
| `EWS_MAX_PARALLEL` | `2` | Pipeline tasks that run at once |
| `EWS_PORT` | `8765` | Web port |

## Memory

A full run peaks at roughly 3–4 GB across the server and two task processes. Windows fails allocations once the commit limit is reached, even with physical memory free, so a machine already busy with VS Code, Chrome, WSL or Docker can hit `MemoryError`. If a task fails that way, close some applications, or set `EWS_MAX_PARALLEL=1` and `EWS_N_CUSTOMERS=8000`, then use **Run from here** on the failed task.

The server caps OpenBLAS at four threads for itself and every task process; uncapped, each Python process commits over 1 GB on a 20-core machine just by importing numpy.

## Notes for the audience

- **Portfolio data.** The portfolio is built by a causal generator (`ews_lib/generator.py`, used by `01_source_extracts.py`) so signals genuinely lead defaults; some defaults are sudden, so no model is perfect. Its hidden outcome reference (`bronze.generator_truth`) is never a model input and is hidden from the Catalog view. The six customers in focus follow scripted October stories.
- **Official data is live.** Every pipeline run pulls Bank Rate (BoE IUDBEDR), unemployment by region (ONS MGSX, YCNC–YCNN, ZSFB), CPI (ONS D7G7), earnings (ONS KAI9) and the UK HPI (HM Land Registry) into `bronze.macro_official_series`; the models themselves use the scenario from the economics data mart.
- **AI is grounded.** Facts are computed in Python and passed as JSON; the model writes only from them, and every prompt and response is in the AI audit log.
- **Real time is simulated.** The live service plays October 2026 forward a business day at a time (4, 8 or 15 seconds per day), re-scoring every customer an event touches with the published champion.
