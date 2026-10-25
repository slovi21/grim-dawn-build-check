"""Chat Markdown renderer (Player View first; Expert/Detail on request)."""
from __future__ import annotations

from . import bars as BR


def _cell(t) -> str:
    return str(t).replace("|", "/").replace("\n", " ")


def table_md(t) -> list[str]:
    """A table with its heading. No blank line between heading and table (an ATX heading ends at its line break; one line
    per table is what keeps the Expert View under its 150 lines with air, .75); the blank line after the table and before a
    note stays - a note glued to the table would become one of its rows."""
    out = [f"#### {t.key} {t.title}", "| " + " | ".join(t.columns) + " |",
           "|" + "---|" * len(t.columns)]
    for r in t.rows:
        out.append("| " + " | ".join(_cell(c) for c in r) + " |")
    if not t.rows:
        out.append("| " + " | ".join(["-"] * len(t.columns)) + " |")
    for n in t.notes:
        out.append(f"\n_{n}_")
    out.append("")
    return out


def player(rep) -> list[str]:
    """Player View in at most 30 lines (chapter 10, A4) - also with a version mismatch, the Ascendant
    note and 3 issues / 3 actions / 3 bosses (worst case 29 lines with three warnings: .72 merged the two
    header lines and put the readiness bar into the score heading, which pays for the eighth area row). Blank
    lines only before the risk legend: every section title is a heading, which ends the area table and the
    lists before it (Markdown); several warnings are one list in one quote.
    Dropped against .65: the score band line (HTML and text keep it) and the 'More details' line, now
    part of the header line; dropped in .72: the Profile text (the score heading names it)."""
    h = rep.header
    out = [f"**{h['name']}** · Level {h['level']} {h['class']} · {h['difficulty']} · {h['stage']}"
           + (" · Hardcore" if h.get("hardcore") else "")
           + f" · {h['version']}" + (f" · Ultimate campaign: {h['campaign']}" if h.get("campaign") else "")
           + " · details: Expert View"]
    if len(rep.warnings) == 1:
        out.append(f"> **WARNING:** {rep.warnings[0]}")
    else:
        out += [f"> - **WARNING:** {w}" for w in rep.warnings]
    # the bar is part of the score heading: the heading ends the lines above and keeps the table below intact; the
    # coloured squares are the status colour (OK green, RISK yellow, GAP red), filled up to the value
    bar = BR.md_readiness(rep.readiness["score"], rep.readiness["bands"]) if rep.readiness.get("bands") else ""
    out += [f"### {rep.readiness['label']}" + (f" · {bar}" if bar else "")]
    out += ["| Area | Status | Bar | In short |", "|---|---|---|---|"]
    for a in rep.areas:
        st = f"**{a.status}**" if a.status in ("GAP", "RISK") else a.status
        out.append(f"| {a.name} | {st} | {BR.md_area(a.value, a.status)} | {_cell(a.text)} |")
    out += ["#### Top issues"]
    out += [f"{i}. {x.text}" for i, x in enumerate(rep.issues, 1)] or ["none"]
    out += ["#### Do this next"]
    if rep.actions:
        for i, x in enumerate(rep.actions, 1):
            tags = f" · {', '.join(x.tags)}" if x.tags else ""
            out.append(f"{i}. **{x.what}** - gain: {x.gain} · cost: {x.cost}{tags}")
    else:
        out.append("nothing urgent - see the Expert View for fine-tuning")
    out += ["#### Boss preparation"]
    if rep.bosses:
        for b in rep.bosses:
            out.append(f"- {b.name}: **{b.risk}** risk - {b.reason}" + (f" · {b.prepare}" if b.prepare else ""))
        if getattr(rep, "boss_legend", ""):         # the blank line keeps it out of the last list item
            out += ["", f"_{rep.boss_legend}_"]
    else:
        out.append("- starts at MID (Ultimate story bosses)")
    return out


def render(rep, views=("player",)) -> str:
    out = []
    if "player" in views:
        out += player(rep)
    if "expert" in views:
        out += ["", "---", "", "### Expert View", ""]
        for t in rep.expert:
            out += table_md(t)
    if "detail" in views:
        out += ["", "---", "", "### Detail", ""]
        for t in rep.detail:
            out += table_md(t)
    return "\n".join(out).rstrip() + "\n"
