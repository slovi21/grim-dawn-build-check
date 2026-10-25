"""Generated documentation (AP10.9, replaces .35 mechanik_seite).

The targets table in references/target-values.md and in the game mechanics
page is generated from analysis/targets.json, so the documents can never
drift from the values the check uses. The text between the markers
<!-- targets:begin --> and <!-- targets:end --> is replaced; everything else
stays hand-written.

    python -m gdcheck.app.docs_gen            # update both files
    python -m gdcheck.app.docs_gen --check    # exit 1 if a file is out of date
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

from ..analysis import targets as TGT

ROOT = Path(__file__).resolve().parents[2]
BEGIN, END = "<!-- targets:begin -->", "<!-- targets:end -->"


def _threshold(t) -> str:
    v = t.get("threshold")
    if v is None:
        return "-"
    s = f"{v:,.0f}" if isinstance(v, (int, float)) and float(v).is_integer() else str(v)
    if t.get("scale_with_level"):
        s += f" (scaled to level {t['scale_with_level']})"
    return s


def rows(data: dict) -> list[tuple]:
    out = []
    for t in data["targets"]:
        out.append((t["id"], t["label"], "/".join(t["stages"]), t["profile"], t["archetype"],
                    _threshold(t), t["basis"], t["type"], t.get("kind", "floor"), f"{t.get('weight', 1):g}",
                    (f"yes, full at {t['margin_at']:g}x" if t.get("margin_at") else "yes") if t.get("margin") else "-",
                    "yes" if t.get("gate") else "-",      # T5: a gate target lowers the whole score when it is open
                    t["source"]))
    return out


COLS = ("Id", "Target", "Stages", "Profile", "Archetype", "Threshold", "Basis", "Type", "Kind", "Weight", "Reserve",
        "Gate", "Source")


def markdown(data: dict) -> str:
    lines = [f"Generated from analysis/targets.json version {data['version']} "
             f"(game {data['game_version']}) - do not edit by hand.", "",
             "| " + " | ".join(COLS) + " |", "|" + "---|" * len(COLS)]
    for r in rows(data):
        lines.append("| " + " | ".join(str(c).replace("|", "/") for c in r) + " |")
    return "\n".join(lines)


def html_table(data: dict) -> str:
    head = "".join(f"<th>{html.escape(c)}</th>" for c in COLS)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>"
                   for r in rows(data))
    return (f"<p class=\"stand\">Generated from <code>analysis/targets.json</code> version "
            f"{html.escape(data['version'])} (game {html.escape(data['game_version'])}).</p>"
            f"<div class=\"breit\"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>")


def splice(text: str, block: str, heading: str) -> str:
    """Replace the marked block; append it under `heading` when no markers exist."""
    if BEGIN in text and END in text:
        a = text.index(BEGIN) + len(BEGIN)
        b = text.index(END)
        return text[:a] + "\n" + block + "\n" + text[b:]
    return text.rstrip("\n") + f"\n\n{heading}\n\n{BEGIN}\n{block}\n{END}\n"


def splice_html(text: str, block: str) -> str:
    if BEGIN in text and END in text:
        return splice(text, block, "")
    sec = (f"<section id=\"targets-table\">\n<h2>Targets used by the check</h2>\n{BEGIN}\n{block}\n{END}\n"
           "</section>\n")
    i = text.find("<section id=\"revisionen\">")
    if i < 0:
        i = text.rfind("</main>")
    if i < 0:
        i = text.rfind("</body>")
    return text[:i] + sec + text[i:] if i >= 0 else text + sec


def outputs(data: dict | None = None, root: Path = ROOT) -> dict:
    data = data or TGT.load()
    md_path = root / "references" / "target-values.md"
    page = root / "references" / "grim-dawn-mechanics.html"
    res = {}
    if md_path.exists():
        res[md_path] = splice(md_path.read_text(encoding="utf-8"), markdown(data),
                              "## Targets used by the check (generated)")
    if page.exists():
        res[page] = splice_html(page.read_text(encoding="utf-8"), html_table(data))
    return res


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    stale = []
    for path, text in outputs().items():
        if path.read_text(encoding="utf-8") != text:
            stale.append(path)
            if "--check" not in argv:
                path.write_text(text, encoding="utf-8")
    for p in stale:
        print(("out of date: " if "--check" in argv else "updated: ") + str(p.relative_to(ROOT)))
    return 1 if (stale and "--check" in argv) else 0


if __name__ == "__main__":
    raise SystemExit(main())
