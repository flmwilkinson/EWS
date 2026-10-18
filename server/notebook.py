"""Notebook format: plain Python files with `# %%` cell markers (the percent format).

    # %% [markdown]
    # # Title
    # Some explanation.

    # %% Optional cell title
    code...

Parsing and serialising round-trip, so cells edited in the browser save back to
the same readable .py file that also runs from the command line.
"""
from __future__ import annotations

import re

HEADER = re.compile(r"^# %%(?P<rest>.*)$")


def parse(text: str) -> list[dict]:
    lines = text.splitlines()
    cells, cur = [], None

    def close():
        if cur is None:
            return
        body = cur.pop("_lines")
        while body and not body[-1].strip():
            body.pop()
        while body and not body[0].strip() and cur["type"] == "code":
            body.pop(0)
            cur["start_line"] += 1
        if cur["type"] == "markdown":
            cur["source"] = "\n".join(l[2:] if l.startswith("# ") else (l[1:] if l.startswith("#") else l) for l in body)
        else:
            cur["source"] = "\n".join(body)
        if cur["source"].strip() or cur["type"] == "code" and cur.get("title"):
            cells.append(cur)

    for i, line in enumerate(lines):
        m = HEADER.match(line)
        if m:
            close()
            rest = m.group("rest").strip()
            if rest.startswith("[markdown]"):
                cur = dict(type="markdown", title=None, start_line=i + 1, _lines=[])
            else:
                cur = dict(type="code", title=rest or None, start_line=i + 1, _lines=[])
        else:
            if cur is None:
                cur = dict(type="code", title=None, start_line=i, _lines=[])
            cur["_lines"].append(line)
    close()
    for n, c in enumerate(cells):
        c["index"] = n
    return cells


def serialise(cells: list[dict]) -> str:
    out = []
    for c in cells:
        if c["type"] == "markdown":
            out.append("# %% [markdown]")
            out.extend(("# " + l) if l.strip() else "#" for l in c["source"].splitlines())
        else:
            out.append(f"# %% {c['title']}" if c.get("title") else "# %%")
            out.extend(c["source"].splitlines())
        out.append("")
    return "\n".join(out).rstrip() + "\n"
