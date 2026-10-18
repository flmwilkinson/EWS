"""Cell-by-cell notebook executor, run as a subprocess by the job runner.

    python -m server.kernel pipeline/05_feature_engineering.py

Executes each code cell in order in one namespace and writes one JSON event
per line to stdout, prefixed with a marker so the runner can tell events from
anything a C library prints directly:

    notebook      the parsed cells
    cell_start    a cell began
    stream        printed output (stdout or stderr)
    display       a table, chart, metrics or markdown from ews_lib.runtime
    progress      a progress bar update
    table_written a lakehouse write (lineage)
    llm_call      a Claude call (model, tokens, latency)
    cell_end      status ok | error, with duration and the traceback on error
    notebook_end  status ok | failed

Line numbers in tracebacks match the .py file because each cell is compiled
at its original offset.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")  # before numpy loads: see server/__main__.py

import io
import json
import sys
import threading
import time
import traceback
import warnings

MARK = "@@EWS@@ "
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.notebook import parse  # noqa: E402


class Channel:
    def __init__(self):
        self.out = io.TextIOWrapper(os.fdopen(os.dup(1), "wb"), encoding="utf-8", line_buffering=True)
        self.lock = threading.Lock()
        self.cell = None

    def send(self, ev: dict):
        ev.setdefault("cell", self.cell)
        ev.setdefault("ts", time.time())
        line = MARK + json.dumps(ev, default=str) + "\n"
        with self.lock:
            self.out.write(line)
            self.out.flush()


class CellStream(io.TextIOBase):
    """Replaces sys.stdout / sys.stderr: forwards printed text as stream events, throttled."""

    def __init__(self, ch: Channel, name: str):
        self.ch, self.name = ch, name
        self.buf = []
        self.last = time.time()
        self.lock = threading.Lock()

    def writable(self):
        return True

    def write(self, s):
        if not s:
            return 0
        with self.lock:
            self.buf.append(s)
            if "\n" in s and time.time() - self.last > 0.05 or sum(map(len, self.buf)) > 4000:
                self._flush()
        return len(s)

    def flush(self):
        with self.lock:
            self._flush()

    def _flush(self):
        if self.buf:
            self.ch.send(dict(event="stream", name=self.name, text="".join(self.buf)))
            self.buf = []
        self.last = time.time()


def _short_tb(path):
    """Traceback limited to notebook and project frames."""
    etype, evalue, tb = sys.exc_info()
    frames = [f for f in traceback.extract_tb(tb) if f.filename == path or ROOT in os.path.abspath(f.filename)]
    frames = [f for f in frames if not f.filename.endswith("kernel.py")]
    lines = traceback.format_list(frames) + traceback.format_exception_only(etype, evalue)
    return "".join(lines), etype.__name__, str(evalue)


def main(path: str):
    warnings.filterwarnings("ignore")
    path = os.path.abspath(path)
    ch = Channel()
    from ews_lib import runtime
    runtime.set_emitter(ch.send)
    with open(path, encoding="utf-8") as f:
        cells = parse(f.read())
    ch.send(dict(event="notebook", path=path, cells=[dict(index=c["index"], type=c["type"], title=c.get("title"),
                                                          start_line=c["start_line"]) for c in cells]))
    out, err = CellStream(ch, "stdout"), CellStream(ch, "stderr")
    sys.stdout, sys.stderr = out, err
    os.chdir(ROOT)
    ns = {"__name__": "__main__", "__file__": path}
    t_nb = time.time()
    for c in cells:
        if c["type"] != "code":
            continue
        ch.cell = c["index"]
        ch.send(dict(event="cell_start"))
        t0 = time.time()
        try:
            code = "\n" * c["start_line"] + c["source"]
            exec(compile(code, path, "exec"), ns)
            out.flush(); err.flush()
            ch.send(dict(event="cell_end", status="ok", duration=round(time.time() - t0, 3)))
        except BaseException:
            out.flush(); err.flush()
            tb, ename, evalue = _short_tb(path)
            ch.send(dict(event="cell_end", status="error", duration=round(time.time() - t0, 3),
                         error=dict(ename=ename, evalue=evalue[:2000], traceback=tb[-6000:])))
            ch.cell = None
            ch.send(dict(event="notebook_end", status="failed", duration=round(time.time() - t_nb, 2)))
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
            os._exit(1)
    ch.cell = None
    ch.send(dict(event="notebook_end", status="ok", duration=round(time.time() - t_nb, 2)))
    sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    ch.out.flush()
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1])
