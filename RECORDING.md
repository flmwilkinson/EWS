# Recording the demo

Two videos are recorded automatically, each in a captioned version and a clean version for your own voiceover (1920×1080):

| Video | Files in `recordings/` | Length | Story |
|---|---|---|---|
| **The two-minute story** | `early_warning_studio_2min.mp4`, `early_warning_studio_2min_clean.mp4` | 1:59 | How to build an early warning system: the process, the dashboard, queuing a run, the code, the live result |
| **The product tour** | `early_warning_studio_tour.mp4`, `early_warning_studio_tour_clean.mp4` | about 2:00 | A fresh pipeline run on camera (time-lapse), a notebook, the official data APIs, the live monitor, a customer |

To re-record, with the app running (`.\start.ps1`):

```powershell
python scripts/record_demo.py --story build   # the two-minute story (needs a finished full pipeline run; about 4 minutes)
python scripts/record_demo.py --story tour    # the product tour: resets and runs the pipeline on camera (about 10 minutes)
```

## The two-minute story

**The ask:** how to build an early warning system. **The arc:** the process → the dashboard → queue a run → the code → the result, live → the customer.

| Time | Scene | On screen | Voiceover |
|---|---|---|---|
| 0:00–0:04 | **Title** | "Building a real-time early warning system" | "This is how we'd build a real-time early warning system for your card book." |
| 0:04–0:20 | **1 · The process** | Workflows: the pipeline, all green. The cursor runs along it left to right. | "It's one pipeline, from source data to decisions. Source extracts land, data quality checks and remediates them, features are built, trigger thresholds are calibrated, six models are trained and compared, every customer is scored and tiered, the system is monitored, and Claude writes the commentary. The result is published to a real-time service." |
| 0:20–0:41 | **2 · The dashboard** | Portfolio overview: KPIs and the early warning ladder, then the watchlist trend with the AI executive summary, then the top signals and risk bands. | "The dashboard shows where the book sits on the ladder, how that's moved month by month, and what's driving it, with an executive summary written for the CRO from the numbers on the page." |
| 0:41–0:51 | **3 · Queue a run** | Workflows: select **Train and compare models**, press **Run task**. The task starts spinning; the run appears in the history. | "Anything can be re-run on demand. Here we queue a retrain of the models." |
| 0:51–1:13 | **4 · The code** | **Open notebook**: the cells tick from red crosses to green as they run; the training cell prints each model's Gini; a quick scroll through the code. | "Every step is plain Python in a notebook your team can read and change. The cells run in order and turn green, and here a traditional scorecard is being compared with gradient boosting, a random forest and a neural network, out of time." |
| 1:13–1:34 | **5 · The result, live** | Live monitor, **Resume**: events stream in, alerts appear with explanations, a customer escalates. | "And this is the result: every event is re-scored as it arrives. One amber signal doesn't escalate anyone; depth and breadth of evidence do. Each alert carries the action and an explanation." |
| 1:34–1:55 | **The customer** | Dan's Customer 360: the AI explanation, the six decisions, the model drivers. | "Every alert drills down to the customer: why they're flagged, what changed, whether it looks like can't pay or won't pay, and what each part of the bank does next." |
| 1:55–1:59 | **End card** | "One set of signals · six decisions, in real time" | "One set of signals, six decisions, in real time." |

### Recording it yourself

1. Before you start: close memory-heavy applications, start the app, and run the full pipeline once (Workflows → **Run pipeline**, about 3½ minutes). Use a 1920×1080 browser window in full screen (F11) at 100% zoom, light theme.
2. Prepare the live feed: **Live monitor** → **Fast** → **Start live feed**, press **Pause** as the clock reaches day 9, then choose **Normal**.
3. Go to **Workflows** and start recording. Follow the table above: move the mouse slowly, pause about two seconds after each click, and scroll a notch at a time.
4. After recording, open **Workflows**, select **Publish to real time** and press **Run task**, so the real-time service serves the models you just retrained.

## The product tour

| Time | Scene | What to do |
|---|---|---|
| 0:00–0:04 | Title | Title card |
| 0:04–0:33 | Workflows | Start from red crosses (**Reset environment**, tick **Also clear run history**), press **Run pipeline**, speed this part up 12× in the edit and say so on screen |
| 0:33–0:47 | Workspace | Open `07_train_models.py` and scroll through the output |
| 0:47–1:04 | Connectors | Scroll to **Official macroeconomic sources**, press **Fetch latest** on the Bank of England card, show regional unemployment |
| 1:04–1:29 | Live monitor | Pre-roll to day 9 as above, then **Resume** |
| 1:29–1:52 | Customer 360 | Click **Dan**, let the explanation finish, scroll slowly |
| 1:52–1:56 | End card | |

## If something goes wrong

- **A task fails with `MemoryError`**: close applications, select the failed task in Workflows and press **Run from here**.
- **The live feed is quiet**: customers take a few business days to escalate. Pre-roll further on **Fast** before pausing.
- **AI panels show template text**: the Claude key is missing or unreachable. Check `.env` and the Claude card on **Connectors** (**Test live call**).
