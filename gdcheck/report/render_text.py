"""Plain text renderer (GD-Tools console, 78 columns)."""
from __future__ import annotations

import textwrap

from . import bars as BR

WIDTH = 78
NAME_W = 21              # "Resistance Reduction" (20) + 1 space
AREA_TEXT_COL = 37       # 2 spaces + name (21) + status (6) + bar (7) + 1 space: the hanging indent of an area row


def _wrap(text, indent=0):
    """Lines of at most WIDTH characters; continuation lines are indented by `indent` + 2 (hanging indent).
    K2 (.70): the old width WIDTH - indent made every wrapped row narrower than the page."""
    return textwrap.wrap(str(text), WIDTH, subsequent_indent=" " * (indent + 2)) or [""]


def table_text(t) -> list[str]:
    out = ["", f"{t.key} {t.title}", "-" * min(WIDTH, len(t.key) + len(t.title) + 1)]
    widths = [len(c) for c in t.columns]
    for r in t.rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(str(c)))
    fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
    line = fmt.format(*t.columns)
    if len(line) <= WIDTH:                      # fits: aligned columns, never cut values
        out.append(line)
        for r in t.rows:
            out.append(fmt.format(*[str(c) for c in r]).rstrip())
    else:
        for r in t.rows:
            out += _wrap(" | ".join(f"{c}: {v}" for c, v in zip(t.columns, r)))
    for n in t.notes:
        out += _wrap(n)
    return out


def render(rep, views=("player", "expert")) -> str:
    h = rep.header
    out = _wrap(f"{h['name']} - Level {h['level']} {h['class']} - {h['difficulty']} - {h['stage']}")
    out += _wrap(h["version"] + f" - Profile: {h['profile']}")
    for w in rep.warnings:
        out += ["", *_wrap("WARNING: " + w)]
    out += ["", rep.readiness["label"].upper()]
    if rep.readiness.get("bands"):
        out.append(BR.text_readiness(rep.readiness["score"], rep.readiness["bands"]))
    if rep.readiness.get("band"):
        out += _wrap(rep.readiness["band"])
    out.append("")
    for a in rep.areas:
        out += _wrap(f"  {a.name:<{NAME_W}}{a.status:<6}{BR.text_area(a.value, a.status)} {a.text}", AREA_TEXT_COL - 2)
    out += ["", "Top issues"]
    issues = [ln for i, x in enumerate(rep.issues, 1) for ln in _wrap(f"  {i}. {x.text}", 3)]
    out += issues or ["  none"]
    out += ["", "Do this next"]
    for i, x in enumerate(rep.actions, 1):
        out += _wrap(f"  {i}. {x.what}")
        out += _wrap(f"     gain: {x.gain}", 5)
        out += _wrap(f"     cost: {x.cost}" + (f" ({', '.join(x.tags)})" if x.tags else ""), 5)
    if not rep.actions:
        out.append("  nothing urgent")
    out += ["", "Boss preparation"]
    for b in rep.bosses:
        name = f"{b.name:<28}" if len(b.name) <= 26 else f"{b.name}  "   # long names: never glued to the risk
        out += _wrap(f"  {name}{b.risk:<7}{b.reason}" + (f"; {b.prepare}" if b.prepare else ""), 2)
    if getattr(rep, "boss_legend", ""):
        out += _wrap(f"  {rep.boss_legend}", 2)
    if not rep.bosses:
        out.append("  starts at MID")
    if "expert" in views:
        out += ["", "=" * WIDTH, "EXPERT VIEW"]
        for t in rep.expert:
            out += table_text(t)
    if "detail" in views:
        out += ["", "=" * WIDTH, "DETAIL"]
        for t in rep.detail:
            out += table_text(t)
    return "\n".join(out).rstrip() + "\n"
