# Recording the demo

Two videos are recorded automatically, each in a captioned version and a clean version for your own voiceover (1920×1080). Both fade in and out; there are no title cards.

| Video | Files in `recordings/` | Length | Story |
|---|---|---|---|
| **The two-minute story** | `early_warning_studio_2min.mp4`, `early_warning_studio_2min_clean.mp4` | 1:59 | How to build a credit card early warning system: the pipeline, the dashboard, running a step on demand, the code, comparing runs, real time, the customer |
| **The product tour** | `early_warning_studio_tour.mp4`, `early_warning_studio_tour_clean.mp4` | 2:44 | A fresh pipeline run on camera (time-lapse), the model notebook, comparing runs, the official data feeds, real time, the customer |

## Before recording: the comparison run

Both videos open **Workflows → Compare**, which compares the latest run with the last run whose model training code was different. To show what real-time data adds, prepare a run trained on monthly data only:

1. Run the full pipeline once (**Workflows → Run pipeline**, about 3½ minutes).
2. In **Workspace**, open `07_train_models.py`, set `USE_REALTIME_SIGNALS = False` and press **Save**. In **Workflows**, select **Train and compare models** and press **Run from here** (about 2 minutes).
3. Set `USE_REALTIME_SIGNALS = True` again and **Save** (or use **Revisions** to restore it).

Then record. The tour resets the data but keeps the run history, runs the pipeline on camera and compares that run (B, monthly plus real-time) with the monthly-only run (A). The story needs a finished full pipeline run and compares it with the monthly-only run in the same way.

```powershell
python scripts/record_demo.py --story tour    # the product tour: resets and runs the pipeline on camera (about 10 minutes)
python scripts/record_demo.py --story build   # the two-minute story (needs a finished full pipeline run; about 4 minutes)
```

Record the tour first: its pipeline run is the full run the story then uses.

## The two-minute story

**The ask:** how to build an early warning system for credit cards. **The arc:** the pipeline → the dashboard → run a step on demand → the code → compare runs → real time → the customer.

| Time | Scene | On screen | Voiceover |
|---|---|---|---|
| 0:00–0:14 | **1 · The pipeline** | Workflows: the credit card EWS pipeline, all green. The cursor runs along it. | "This is a credit card early warning system, built as one pipeline: source data, data quality, features, thresholds, models, scoring, monitoring, AI commentary, and publishing to a real-time service. Each step is a notebook." |
| 0:14–0:31 | **2 · The dashboard** | Credit card portfolio: the early warning ladder, the watchlist trend, the AI summary, the top signals. | "The dashboard shows the card book by risk tier, how it has moved month by month, the main drivers, and a summary for the CRO written by AI from the figures on the page." |
| 0:31–0:40 | **3 · Run on demand** | Workflows: select **Train and compare models**, press **Run task**. | "Any step can be run on demand. Here we retrain the models." |
| 0:40–1:00 | **4 · The code** | **Open notebook**: the notes on labels and inputs, `USE_REALTIME_SIGNALS`, the risk bucket table, the training cell running. | "The target labels come from historical risk buckets: the worst arrears bucket each account reached in the next six months. The inputs combine monthly data with real-time signals. Each cell turns green as it runs." |
| 1:00–1:23 | **5 · Compare runs** | Workflows → **Compare**: the one-line code change, then Gini, the watchlist and early warning results for A and B. | "Any two runs can be compared. Here, monthly data only against monthly plus real-time signals: one line of code changed, Gini is higher for the main models, and the median warning comes a month earlier." |
| 1:23–1:40 | **6 · Real time** | Live monitor, **Resume**: events arrive, alerts appear with actions and AI explanations. | "In real time, each new card, current account and bureau event is scored as it arrives, and customers move between tiers." |
| 1:40–1:59 | **7 · The customer** | Dan's customer view: the AI explanation, the six decisions, the model drivers. | "Each alert drills down to the customer: why they are flagged, what changed, and the recommended actions across the bank." |

The take runs at 2:09 and is sped up by 8% to 1:59 (`setpts=PTS/1.08`); nobody will notice.

### Recording it yourself

1. Before you start: prepare the comparison run (above), close memory-heavy applications, and use a 1920×1080 browser window in full screen (F11) at 100% zoom, light theme.
2. Prepare the live feed: **Live monitor** → **Fast** → **Start live feed**, press **Pause** as the clock reaches day 9, then choose **Normal**.
3. Go to **Workflows** and start recording. Follow the table above: move the mouse slowly, pause about two seconds after each click, and scroll a notch at a time.
4. After recording, open **Workflows**, select **Publish to real time** and press **Run task**, so the real-time service serves the models you just retrained.

## The product tour

| Time | Scene | What to do |
|---|---|---|
| 0:00–0:07 | Workflows | Press **Run pipeline** (the environment has been reset; the run history is kept) |
| 0:07–0:23 | Workflows | The run, sped up to about 16 seconds on screen, with the loading spinners held still |
| 0:23–0:28 | Workflows | All eleven tasks green |
| 0:28–0:54 | Workspace | Open `07_train_models.py`: the notes on target labels and inputs, the risk bucket table, the six models |
| 0:54–1:20 | Workflows | **Compare**: the one-line code change, then model Gini, the watchlist, early warning performance and data quality for A and B |
| 1:20–1:40 | Connectors | Scroll to **Official macroeconomic sources**, press **Fetch latest** on the Bank of England card, show regional unemployment |
| 1:40–2:12 | Live monitor | Pre-roll to day 9 as above, then **Resume**; alerts with AI explanations |
| 2:12–2:44 | Customer view | Click **Dan**, let the explanation finish, scroll to the model drivers and triggers |

## If something goes wrong

- **The PC sleeps mid-take**: the recorder keeps Windows awake while it runs, but closing the lid still sleeps the machine. Re-run the take.
- **Compare shows the wrong pair of runs**: it picks the latest successful run (B) and the last earlier run whose `07_train_models.py` differed (A). Prepare the comparison run again, or pick the runs in the two drop-downs.
- **A task fails with `MemoryError`**: close applications, select the failed task in Workflows and press **Run from here**.
- **The live feed is quiet**: customers take a few business days to escalate. Pre-roll further on **Fast** before pausing.
- **AI panels show template text**: the AI key is missing or unreachable. Check `.env` and the AI card on **Connectors** (**Test live call**).
