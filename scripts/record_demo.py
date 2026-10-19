"""Record the demo videos of Early Warning Studio.

    python scripts/record_demo.py --story build              two-minute story: process, dashboard, queue a run, the code, compare runs, live, customer
    python scripts/record_demo.py --story tour               product tour: a fresh pipeline run on camera, a notebook, compare runs, official APIs, live, customer
    python scripts/record_demo.py --story tour --keep-data   tour without the reset and pipeline run

Needs the app running (python -m server), Playwright (pip install playwright) with Microsoft Edge or
Chromium, and ffmpeg on the PATH. A full take takes about seven minutes of wall-clock time.

Writes to recordings/, for each story:
    early_warning_studio_<story>.mp4         with captions
    early_warning_studio_<story>_clean.mp4   no captions, for your own voiceover

The build story needs a finished full pipeline run (Workflows > Run pipeline) before it starts. Both stories compare
the latest run with the last run whose model training code was different: see RECORDING.md for preparing that run.

How it works: the browser's screencast sends a frame whenever the screen changes; each frame is saved with its
timestamp, then ffmpeg rebuilds a constant 30 fps video from those timestamps. The pipeline run is a time-lapse
(the caption says so); everything else plays in real time. Captions are drawn by ffmpeg, so one capture gives
both versions.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.request

from playwright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "recordings"
BASE = os.getenv("EWS_URL", "http://127.0.0.1:8765")
W, H = 1920, 1080
FPS = 30
TIMELAPSE_SECONDS = 16  # the pipeline run is sped up to last this long on screen
LIVE_PREROLL_DAY = 9    # the live feed is quietly played to this day before its scene
DEMO_CUSTOMER = "Dan"   # the persona opened in Customer 360

CURSOR_JS = r"""
(() => {
  const install = () => {
    if (document.getElementById('__cursor')) return;
    const c = document.createElement('div');
    c.id = '__cursor';
    c.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24"><path d="M5 2.5l14 8.2-6.1 1.6-3.2 5.9z" fill="#0f1726" stroke="#ffffff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, { position: 'fixed', left: 0, top: 0, zIndex: 2147483647, pointerEvents: 'none', transform: 'translate(-200px,-200px)', filter: 'drop-shadow(0 2px 3px rgba(0,0,0,.35))' });
    document.documentElement.appendChild(c);
    const tick = document.createElement('div');
    tick.id = '__tick';
    Object.assign(tick.style, { position: 'fixed', right: 0, bottom: 0, width: '1px', height: '1px', zIndex: 2147483647, pointerEvents: 'none', background: 'transparent' });
    document.documentElement.appendChild(tick);
    window.addEventListener('mousemove', (e) => { c.style.transform = `translate(${e.clientX - 4}px, ${e.clientY - 2}px)`; }, true);
    window.addEventListener('mousedown', (e) => {
      const r = document.createElement('div');
      Object.assign(r.style, { position: 'fixed', left: (e.clientX - 18) + 'px', top: (e.clientY - 18) + 'px', width: '36px', height: '36px', borderRadius: '50%',
        border: '3px solid rgba(42,120,214,.85)', zIndex: 2147483646, pointerEvents: 'none', transition: 'transform .45s ease-out, opacity .45s ease-out' });
      document.documentElement.appendChild(r);
      requestAnimationFrame(() => { r.style.transform = 'scale(1.8)'; r.style.opacity = '0'; });
      setTimeout(() => r.remove(), 500);
    }, true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install); else install();
})();
"""

def api(method, path, body=None, timeout=30):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


# ---------------------------------------------------------------------------
# Frame capture
# ---------------------------------------------------------------------------
class Recorder:
    def __init__(self, cdp, frames_dir: pathlib.Path):
        self.cdp, self.dir = cdp, frames_dir
        self.frames, self.segments, self.captions = [], [], []
        self.active, self.speed, self.last, self.n, self.seg, self.target = False, 1.0, 0.0, 0, -1, None

    async def start(self):
        self.cdp.on("Page.screencastFrame", self._frame)
        await self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 88, "maxWidth": W, "maxHeight": H, "everyNthFrame": 2})

    def _frame(self, p):
        asyncio.ensure_future(self.cdp.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]}))
        if not self.active:
            return
        now = time.time()
        min_gap = 0.2 if self.target else self.speed / FPS * 0.9
        if now - self.last < min_gap:
            return
        self.last = now
        self.n += 1
        name = f"f{self.n:06d}.jpg"
        (self.dir / name).write_bytes(base64.b64decode(p["data"]))
        self.frames.append((now, name, self.seg))

    async def begin(self, page, speed=1.0, target=None):
        """Start a segment. With `target` (seconds), the segment is sped up to last that long; its speed is set at end()."""
        if self.segments and self.segments[-1][1] is None:
            self.end()
        self.seg = len(self.segments)
        self.segments.append([time.time(), None, speed])
        self.speed, self.last, self.active, self.target = speed, 0.0, True, target
        await page.evaluate("() => { const t = document.getElementById('__tick'); if (t) t.style.background = t.style.background === 'transparent' ? 'rgba(0,0,0,0.01)' : 'transparent'; }")

    def end(self):
        seg = self.segments[-1]
        seg[1] = time.time()
        if self.target:
            seg[2] = max((seg[1] - seg[0]) / self.target, 1.0)
        self.active, self.target = False, None
        return seg[2]

    def caption(self, text, start=None, end=None):
        self.captions.append([text, start or time.time(), end])

    def close_caption(self):
        if self.captions and self.captions[-1][2] is None:
            self.captions[-1][2] = time.time()

    def timeline(self):
        items, spans, t = [], [], 0.0
        for si, (s, e, sp) in enumerate(self.segments):
            fr = [f for f in self.frames if f[2] == si]
            if not fr:
                continue
            spans.append((fr[0][0], e, sp, t))
            for (ts, name, _), nxt in zip(fr, fr[1:] + [(e, None, None)]):
                d = max((nxt[0] - ts) / sp, 1 / 120)
                items.append((name, d))
                t += d
        return items, spans, t

    @staticmethod
    def out_time(wall, spans):
        for s, e, sp, o in spans:
            if wall <= e:
                return o + max(wall - s, 0) / sp
        s, e, sp, o = spans[-1]
        return o + (e - s) / sp


# ---------------------------------------------------------------------------
# A visible, unhurried hand
# ---------------------------------------------------------------------------
class Hand:
    def __init__(self, page):
        self.page, self.x, self.y = page, W * 0.55, H * 0.55

    async def move(self, x, y, dur=0.7):
        steps = max(int(dur * 60), 8)
        x0, y0 = self.x, self.y
        for i in range(1, steps + 1):
            t = i / steps
            e = t * t * (3 - 2 * t)
            await self.page.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e)
            await asyncio.sleep(dur / steps)
        self.x, self.y = x, y

    async def to(self, locator, dur=0.7, dx=0, dy=0):
        b = await locator.bounding_box()
        await self.move(b["x"] + b["width"] / 2 + dx, b["y"] + b["height"] / 2 + dy, dur)

    async def click(self, locator, dur=0.7, settle=0.35):
        await self.to(locator, dur)
        await asyncio.sleep(settle)
        await self.page.mouse.down()
        await asyncio.sleep(0.09)
        await self.page.mouse.up()

    async def scroll(self, selector, dy, dur):
        steps = max(int(dur * 50), 10)
        done = 0.0
        for i in range(1, steps + 1):
            t = i / steps
            target = dy * (t * t * (3 - 2 * t))
            await self.page.evaluate("([s, d]) => { const el = document.querySelector(s); if (el) el.scrollBy(0, d); }", [selector, target - done])
            done = target
            await asyncio.sleep(dur / steps)

    async def scroll_to(self, selector, element_selector, offset=90, dur=1.6):
        dy = await self.page.evaluate("""([s, e, off]) => { const c = document.querySelector(s), el = document.querySelector(e);
            if (!c || !el) return 0; return el.getBoundingClientRect().top - c.getBoundingClientRect().top - off; }""", [selector, element_selector, offset])
        await self.scroll(selector, dy, dur)


# ---------------------------------------------------------------------------
# The take
# ---------------------------------------------------------------------------
async def wait_for(pred, timeout, every=1.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if pred():
                return True
        except Exception:
            pass
        await asyncio.sleep(every)
    return False


async def preroll_live(day=LIVE_PREROLL_DAY):
    """Quietly play the live feed to a given day, then pause it at Normal speed."""
    print(f"Pre-rolling the live feed to day {day}")
    api("POST", "/api/live/reset")
    await asyncio.sleep(2)
    api("POST", "/api/live/speed", {"seconds_per_day": 4})
    api("POST", "/api/live/start")
    await wait_for(lambda: api("GET", "/api/health")["live"]["day"] >= day, 240)
    api("POST", "/api/live/pause")
    api("POST", "/api/live/speed", {"seconds_per_day": 8})
    await asyncio.sleep(1.0)


async def scroll_to_cell(page, hand: Hand, title, offset=70, dur=1.6):
    """Scroll the notebook so the cell with this title sits near the top."""
    dy = await page.evaluate("""([t, off]) => { const el = [...document.querySelectorAll('.cell-title input')].find((i) => i.value === t);
        const c = document.querySelector('.ws-main'); if (!el || !c) return 0; return el.closest('.cell').getBoundingClientRect().top - c.getBoundingClientRect().top - off; }""",
                             [title, offset])
    await hand.scroll(".ws-main", dy, dur)


async def compare_scene(page, hand: Hand, nav):
    """Workflows > Compare: the code change and the results of two runs, side by side."""
    await hand.click(nav("Workflows"))
    await page.wait_for_selector(".node")
    await asyncio.sleep(0.6)
    await hand.click(page.locator(".seg button", has_text="Compare").first)
    await page.wait_for_selector(".cmp-run", timeout=20000)
    await asyncio.sleep(0.7)
    await hand.move(W * 0.5, H * 0.62, 0.5)
    await hand.scroll_to(".content", ".cmp-run", 74, 1.4)
    await asyncio.sleep(0.6)
    await hand.to(page.locator(".cmp-lines .add").first, 0.7)
    await asyncio.sleep(2.2)
    await hand.to(page.locator(".cmp-table tbody tr").nth(2), 0.8, dx=120)
    await asyncio.sleep(2.2)
    await hand.to(page.locator(".cmp-table tr", has_text="On the watchlist").first, 0.8, dx=120)
    await asyncio.sleep(1.4)
    await hand.to(page.locator(".cmp-table tr", has_text="Bad customers flagged").first, 0.7, dx=120)
    await asyncio.sleep(1.8)


async def take_build(page, rec: Recorder, hand: Hand):
    nav = lambda label: page.locator("a.nav-item", has_text=label).first
    runs = [r for r in api("GET", "/api/runs?limit=10") if r["scope"] == "all"]
    if not runs or runs[0]["status"] != "SUCCEEDED":
        raise SystemExit("Needs a successful full pipeline run: open Workflows and press Run pipeline first.")
    order = [t["key"] for t in api("GET", "/api/jobs/pipeline")["tasks"]]
    await preroll_live()
    await page.goto(BASE + "/#/workflows")
    await page.wait_for_selector(".node", timeout=30000)
    full = runs[0]["run_id"]
    await page.evaluate("(id) => [...document.querySelectorAll('.tbl tbody tr')].find((tr) => tr.textContent.includes(id))?.click()", full)
    await asyncio.sleep(2.5)
    await hand.move(W * 0.12, H * 0.42, 0.2)
    await rec.begin(page)
    await asyncio.sleep(1.2)

    # 1. the process
    rec.caption("1 · A credit card early warning system: eleven pipeline steps, each one a notebook")
    key_steps = {"dq", "features", "models", "score", "publish"}
    for key in ["generate", "ingest", "dq", "dq_commentary", "features", "thresholds", "models", "score", "monitoring", "monitoring_commentary", "publish"]:
        await hand.to(page.locator(".node").nth(order.index(key)), 0.4)
        await asyncio.sleep(0.6 if key in key_steps else 0.2)
    await asyncio.sleep(0.6)
    rec.close_caption()

    # 2. the dashboard
    rec.caption("2 · The card book by risk tier, the main drivers, and an AI summary for the CRO")
    await hand.click(nav("Portfolio overview"))
    await page.wait_for_selector(".kpi")
    await asyncio.sleep(2.0)
    await hand.move(W * 0.55, H * 0.5, 0.6)
    await hand.scroll(".content", 560, 2.2)
    await asyncio.sleep(3.8)
    await hand.scroll(".content", 640, 2.0)
    await asyncio.sleep(1.0)
    rec.close_caption()

    # 3. queue a run
    rec.caption("3 · Run any step on demand: here, retrain the models")
    await hand.click(nav("Workflows"))
    await page.wait_for_selector(".node")
    await asyncio.sleep(0.7)
    await hand.click(page.locator(".node").nth(order.index("models")))
    await asyncio.sleep(1.2)
    await hand.click(page.get_by_role("button", name="Run task"))
    await asyncio.sleep(1.8)
    rec.close_caption()

    # 4. the code
    rec.caption("4 · Target labels come from historical risk buckets; inputs combine monthly and real-time data")
    await hand.click(page.get_by_role("link", name="Open notebook"))
    await page.wait_for_selector(".cell-box")
    await asyncio.sleep(1.0)
    await hand.move(W * 0.6, H * 0.36, 0.6)
    await asyncio.sleep(2.4)
    await hand.move(W * 0.62, H * 0.62, 0.4)
    await scroll_to_cell(page, hand, "Settings and data", 60, 1.2)
    await asyncio.sleep(2.2)
    await scroll_to_cell(page, hand, "Target labels from historical risk buckets", 60, 1.0)
    await asyncio.sleep(2.0)
    rec.close_caption()
    rec.caption("Each cell turns green when it runs, with its output underneath")
    await scroll_to_cell(page, hand, "Train the six model approaches", 60, 1.0)
    await asyncio.sleep(3.4)
    rec.close_caption()

    # 5. compare runs
    rec.caption("5 · Compare runs: monthly data only (A) against monthly plus real-time signals (B)")
    await compare_scene(page, hand, nav)
    rec.close_caption()

    # 6. real time
    rec.caption("6 · Real time: each new card, current account and bureau event is scored as it arrives")
    await hand.click(nav("Live monitor"))
    await page.wait_for_selector(".persona-card")
    await asyncio.sleep(0.8)
    await hand.click(page.get_by_role("button", name="Resume"))
    await asyncio.sleep(2.8)
    await hand.move(W * 0.6, H * 0.5, 0.9)
    await asyncio.sleep(4.6)
    await hand.to(page.locator(".persona-card", has_text=DEMO_CUSTOMER).first, 0.9)
    await asyncio.sleep(1.0)
    rec.close_caption()

    # 7. the customer
    rec.caption("7 · Customer view: why the customer is flagged, what changed and the recommended actions")
    await hand.click(page.locator(".persona-card", has_text=DEMO_CUSTOMER).first, 0.4)
    await page.wait_for_selector(".c360-head")
    await asyncio.sleep(7.0)
    await hand.move(W * 0.78, H * 0.45, 0.8)
    await asyncio.sleep(1.6)
    await hand.scroll(".content", 640, 2.4)
    await asyncio.sleep(2.0)
    rec.close_caption()
    await asyncio.sleep(1.4)
    rec.end()
    api("POST", "/api/live/pause")
    # The queued run registered new model versions; publish so the real-time service serves exactly what is in the registry.
    await wait_for(lambda: api("GET", "/api/runs?limit=1")[0]["status"] != "RUNNING", 400)
    api("POST", "/api/runs", {"scope": "only", "task": "publish"})
    print("Re-published the champions to the real-time service")


async def take(page, rec: Recorder, hand: Hand, keep_data: bool):
    nav = lambda label: page.locator("a.nav-item", has_text=label).first

    # ---- prepare (not recorded) -------------------------------------------------------
    try:
        api("POST", "/api/live/pause")
    except Exception:
        pass
    if not keep_data:
        print("Resetting the environment (run history is kept, for the comparison)")
        api("POST", "/api/admin/reset", {"clear_history": False})
    await page.goto(BASE + "/#/workflows")
    await page.wait_for_selector(".node", timeout=30000)
    await asyncio.sleep(2.5)
    await hand.move(W * 0.7, H * 0.3, 0.2)
    await rec.begin(page)
    await asyncio.sleep(1.2)

    # ---- the pipeline ---------------------------------------------------------------------
    if not keep_data:
        rec.caption("The credit card early warning pipeline: eleven steps, each an editable notebook")
        await asyncio.sleep(2.4)
        await hand.click(page.get_by_role("button", name="Run pipeline"))
        await asyncio.sleep(1.2)
        rec.close_caption()
        rec.end()
        print("Pipeline running (time-lapse)")
        await page.evaluate("""() => { const c = document.getElementById('__cursor'); if (c) c.style.opacity = '0';
            const st = document.createElement('style'); st.id = '__still';
            st.textContent = '*, *::before, *::after { animation: none !important; transition: none !important; }';
            document.head.appendChild(st); }""")  # spinners strobe when sped up; hold them still for the time-lapse
        await rec.begin(page, target=TIMELAPSE_SECONDS)
        rec.caption("Running: data load, quality checks, features, thresholds, models, scoring, monitoring and AI commentary")
        order = [t["key"] for t in api("GET", "/api/jobs/pipeline")["tasks"]]
        shown, t0 = None, time.time()
        while time.time() - t0 < 1200:  # about 7 minutes under recording load; leave headroom
            runs = api("GET", "/api/runs?limit=1")
            r = runs[0] if runs else None
            if r and r["status"] != "RUNNING":
                break
            running = [k for k in order if r and r["task_state"].get(k, {}).get("status") == "running"]
            if running and running[-1] != shown:
                shown = running[-1]
                await page.evaluate("(i) => document.querySelectorAll('.node')[i]?.click()", order.index(shown))
            await asyncio.sleep(1.0)
        rec.close_caption()
        rec.end()
        await page.evaluate("() => { const c = document.getElementById('__cursor'); if (c) c.style.opacity = '1'; document.getElementById('__still')?.remove(); }")
        status = api("GET", "/api/runs?limit=1")[0]["status"]
        print("Pipeline", status)
        if status != "SUCCEEDED":
            raise SystemExit("The pipeline did not succeed; fix that before recording (see Workflows).")
        await asyncio.sleep(2.0)
        await rec.begin(page)
        rec.caption("All steps complete. Each run records the rows written and the AI calls made")
        await hand.move(W * 0.62, H * 0.33, 0.9)
        await asyncio.sleep(3.2)
        rec.close_caption()
    else:
        rec.caption("The credit card early warning pipeline: eleven steps, each an editable notebook")
        await asyncio.sleep(4.0)
        rec.close_caption()

    # ---- a notebook ----------------------------------------------------------------------------
    rec.caption("Each step is a notebook: the code, its output and the tables it writes")
    await hand.click(nav("Workspace"))
    await page.wait_for_selector(".ws-file")
    await asyncio.sleep(1.0)
    await hand.click(page.locator(".ws-file", has_text="07_train_models.py"))
    await page.wait_for_selector(".cell-box .outputs", timeout=20000)
    await asyncio.sleep(1.4)
    await hand.move(W * 0.6, H * 0.36, 0.7)
    await asyncio.sleep(2.4)
    rec.close_caption()
    rec.caption("Target labels come from historical risk buckets; inputs combine monthly and real-time data")
    await asyncio.sleep(1.6)
    await hand.move(W * 0.66, H * 0.62, 0.5)
    await scroll_to_cell(page, hand, "Target labels from historical risk buckets", 60, 1.8)
    await asyncio.sleep(3.0)
    await scroll_to_cell(page, hand, "Choose the champion", 60, 2.0)
    await asyncio.sleep(3.0)
    rec.close_caption()

    # ---- compare runs ----------------------------------------------------------------------------
    rec.caption("Compare runs: monthly data only (A) against monthly plus real-time signals (B)")
    await compare_scene(page, hand, nav)
    rec.close_caption()

    # ---- official sources ----------------------------------------------------------------------
    rec.caption("Official data feeds: Bank of England, ONS and HM Land Registry")
    await hand.click(nav("Connectors"))
    await page.wait_for_selector(".conn-card")
    await asyncio.sleep(1.4)
    await hand.move(W * 0.6, H * 0.6, 0.5)
    await hand.scroll_to(".content", "#official-sources", 80, 1.8)
    await asyncio.sleep(1.2)
    await hand.click(page.get_by_role("button", name="Fetch latest").first)
    await asyncio.sleep(2.4)
    await hand.to(page.locator(".conn-card", has_text="ILO unemployment rate by region").first, 0.9)
    await hand.scroll(".content", 330, 2.2)
    await asyncio.sleep(2.4)
    rec.close_caption()
    rec.end()

    # ---- pre-roll the live feed (not recorded) ---------------------------------------------
    await preroll_live()

    # ---- live ------------------------------------------------------------------------------------
    await rec.begin(page)
    rec.caption("Real time: each new card, current account and bureau event is scored as it arrives")
    await hand.click(nav("Live monitor"))
    await page.wait_for_selector(".persona-card")
    await asyncio.sleep(2.0)
    await hand.click(page.get_by_role("button", name="Resume"))
    await asyncio.sleep(3.0)
    await hand.move(W * 0.42, H * 0.55, 1.2)
    await asyncio.sleep(5.0)
    rec.close_caption()
    rec.caption("Each alert shows the signals, the recommended action and an AI explanation")
    await hand.move(W * 0.62, H * 0.5, 1.2)
    await asyncio.sleep(7.0)
    await hand.to(page.locator(".persona-card", has_text=DEMO_CUSTOMER).first, 1.2)
    await asyncio.sleep(3.0)
    rec.close_caption()

    # ---- customer view ---------------------------------------------------------------------------
    rec.caption(f"Customer view: why {DEMO_CUSTOMER} is flagged, what changed and the recommended actions")
    await hand.click(page.locator(".persona-card", has_text=DEMO_CUSTOMER).first, 0.4)
    await page.wait_for_selector(".c360-head")
    await asyncio.sleep(9.0)
    rec.close_caption()
    rec.caption("Model drivers, and each trigger against its amber and red thresholds")
    await hand.move(W * 0.5, H * 0.6, 0.6)
    await hand.scroll(".content", 600, 3.2)
    await asyncio.sleep(3.5)
    await hand.scroll(".content", 560, 3.0)
    await asyncio.sleep(3.4)
    rec.close_caption()
    await asyncio.sleep(1.6)
    rec.end()
    api("POST", "/api/live/pause")


def encode(rec: Recorder, frames_dir: pathlib.Path, out_dir: pathlib.Path, name: str):
    items, spans, total = rec.timeline()
    lines = ["ffconcat version 1.0"]
    for frame, d in items:
        lines += [f"file '{frame}'", f"duration {d:.4f}"]
    lines.append(f"file '{items[-1][0]}'")
    (frames_dir / "frames.ffconcat").write_text("\n".join(lines))
    font = "C\\:/Windows/Fonts/seguisb.ttf" if sys.platform == "win32" else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    draws = []
    for i, (text, s, e) in enumerate(rec.captions):
        a, b = rec.out_time(s, spans), rec.out_time(e or s + 3, spans)
        (frames_dir / f"cap{i}.txt").write_text(text, encoding="utf-8")
        alpha = f"if(lt(t,{a + 0.35:.2f}),(t-{a:.2f})/0.35,if(gt(t,{b - 0.35:.2f}),({b:.2f}-t)/0.35,1))"
        draws.append(f"drawtext=fontfile='{font}':textfile=cap{i}.txt:fontsize=30:fontcolor=white:box=1:boxcolor=0x0c1424@0.86:boxborderw=20:"
                     f"x=(w-text_w)/2:y=h-text_h-64:enable='between(t,{a:.2f},{b:.2f})':alpha='{alpha}'")
    fade = f"fade=t=in:st=0:d=0.8,fade=t=out:st={max(total - 1.2, 0):.2f}:d=1.2"  # fade in from and out to black
    base = f"fps={FPS},scale={W}:{H}:flags=lanczos,{fade},format=yuv420p"
    out_dir.mkdir(exist_ok=True)
    for suffix, vf in [("_clean", base), ("", ",".join([base] + draws))]:
        out = out_dir / f"early_warning_studio_{name}{suffix}.mp4"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "frames.ffconcat", "-vf", vf,
               "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-movflags", "+faststart", str(out)]
        subprocess.run(cmd, cwd=frames_dir, check=True)
        print(f"Wrote {out}  ({out.stat().st_size / 1e6:.1f} MB)")
    print(f"Video length {total:.0f}s from {len(items)} frames")
    (out_dir / f"captions_{name}.json").write_text(json.dumps([dict(text=t, start=round(rec.out_time(s, spans), 2), end=round(rec.out_time(e or s, spans), 2))
                                                        for t, s, e in rec.captions], indent=2))


async def main(story: str, keep_data: bool):
    health = api("GET", "/api/health")
    if health.get("running"):
        raise SystemExit("A pipeline run is in progress; wait for it to finish.")
    work = OUT / "_frames"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel="msedge", headless=True)
        except Exception:
            browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1)
        await ctx.add_init_script("try { localStorage.setItem('ews-theme', 'light'); } catch (e) {}")
        await ctx.add_init_script(CURSOR_JS)
        page = await ctx.new_page()
        cdp = await ctx.new_cdp_session(page)
        rec = Recorder(cdp, work)
        await rec.start()
        hand = Hand(page)
        try:
            if story == "build":
                await take_build(page, rec, hand)
            else:
                await take(page, rec, hand, keep_data)
        finally:
            await cdp.send("Page.stopScreencast")
            await browser.close()
    encode(rec, work, OUT, {"build": "2min", "tour": "tour"}[story])
    shutil.rmtree(work, ignore_errors=True)


def keep_awake():
    """Stop Windows idle sleep for the rest of this process: a sleep mid-take freezes the pipeline and breaks the timing."""
    if sys.platform == "win32":
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)


if __name__ == "__main__":
    keep_awake()
    ap = argparse.ArgumentParser()
    ap.add_argument("--story", choices=["build", "tour"], default="build", help="build: the two-minute story; tour: the product tour")
    ap.add_argument("--keep-data", action="store_true", help="tour only: skip the reset and pipeline run")
    a = ap.parse_args()
    asyncio.run(main(a.story, a.keep_data))
