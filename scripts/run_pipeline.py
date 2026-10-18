"""Run the pipeline notebooks from the command line through the same kernel the app uses.

    python scripts/run_pipeline.py              # every task in order
    python scripts/run_pipeline.py 07 08        # just these tasks
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = "@@EWS@@ "


def run(nb):
    env = dict(os.environ, EWS_RUN_ID=f"cli-{int(time.time())}", EWS_TASK=os.path.basename(nb)[:-3], PYTHONUTF8="1")
    env.setdefault("OPENBLAS_NUM_THREADS", "4")
    p = subprocess.Popen([sys.executable, "-u", "-m", "server.kernel", nb], cwd=ROOT, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    titles = {}
    for line in p.stdout:
        if not line.startswith(MARK):
            print("   [raw]", line.rstrip()[:200])
            continue
        ev = json.loads(line[len(MARK):])
        e = ev["event"]
        if e == "notebook":
            titles = {c["index"]: c.get("title") or f"cell {c['index']}" for c in ev["cells"]}
        elif e == "cell_end":
            mark = "OK " if ev["status"] == "ok" else "ERR"
            print(f"   {mark} {titles.get(ev['cell'])!s:<55} {ev['duration']:>7.2f}s")
            if ev["status"] != "ok":
                print(ev["error"]["traceback"])
        elif e == "table_written":
            print(f"        wrote {ev['table']:<40} v{ev['version']:<3} {ev['rows']:>9,} rows")
        elif e == "llm_call":
            print(f"        llm {ev.get('purpose')}: {ev.get('status')} {ev.get('latency_ms')}ms {ev.get('output_tokens')} tok {ev.get('error') or ''}")
        elif e == "notebook_end":
            print(f"   == {ev['status']} in {ev['duration']}s")
    return p.wait()


if __name__ == "__main__":
    nbs = sorted(f for f in os.listdir(os.path.join(ROOT, "pipeline")) if f.endswith(".py"))
    pick = sys.argv[1:]
    if pick:
        nbs = [n for n in nbs if any(n.startswith(k) for k in pick)]
    for nb in nbs:
        print(f"\n## {nb}")
        if run(os.path.join("pipeline", nb)) != 0:
            sys.exit(1)
