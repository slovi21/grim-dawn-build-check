"""Report v2: the short text the "Copy summary" button puts in the clipboard (milestone M7a).

`summary(o)` takes the overview of the view model (`view.Overview`, block 1) and returns a few lines of Markdown a player can
paste into a forum or a Reddit post: the class, the score with the stage, the verdict, the open Endgame gates, the first steps and
one line naming the tool. It chooses and words; it computes nothing: the score, the gates and the steps are the ones the page
shows, so there is no second source for them (the score formula stays in `readiness._term`). No character name is in it - the
player decides what to call the build when pasting.

The text is plain data for a `data-copy` attribute: `render_html` escapes it where it is inserted."""
from __future__ import annotations

import re

from . import view as V

TOOL_NAME = "Grim Dawn Build Check"
TOOL_URL = "https://slovi21.github.io/grim-dawn-build-check/"
MAX_LINES = 12                  # a post, not a report
MAX_STEPS = 3
_STAGE = {"Early": "levelling", "Mid": "mid game", "Endgame": "endgame"}     # Track.stage -> the words after the score


def _after_first_sentence(text: str) -> str:
    """The verdict without its first sentence ("Mid game, 51 of 100."): the score line says that part itself."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
    return parts[1] if len(parts) > 1 else ""


def _class_line(o: V.Overview) -> str:
    level = next((f.value for f in o.facts if f.label == "Level"), "")
    named = f"**{o.cls}** {o.masteries}".strip() if o.cls and o.cls != "?" else ""      # an unknown class is left out, not shown as "?"
    return (named + (f", level {level}" if named else f"Level {level}")) if level else named


def _gate_line(o: V.Overview) -> str:
    if o.gates:
        n = len(o.gates)
        return f"{n} open Endgame gate{'' if n == 1 else 's'}: {', '.join(g.label for g in o.gates)}."
    return "All Endgame gates are met." if o.track.stage == "Endgame" else ""


def _step_lines(o: V.Overview) -> list[str]:
    if not o.steps:
        return []
    shown = o.steps[:MAX_STEPS]
    # a step can cost something (resistance, health ...): the page says what, the pasted text only that it can
    lines = ["Do this next (some steps have a cost, see the report):" if any(s.cost for s in shown) else "Do this next:"]
    for k, s in enumerate(shown, 1):
        lines.append(f"{k}. {s.what}" + (f" - {s.result}" if s.result else ""))
    return lines


def summary(o: V.Overview) -> str:
    """At most `MAX_LINES` lines of Markdown; the paragraphs are separated by one blank line (a forum joins single lines)."""
    score = f"**{o.track.score} of 100, {_STAGE.get(o.track.stage, o.track.stage.lower())}.** {_after_first_sentence(o.verdict)}"
    blocks = [[_class_line(o)], [score.strip()], [_gate_line(o)], _step_lines(o),
              [f"Checked with [{TOOL_NAME}]({TOOL_URL})"]]
    return "\n\n".join("\n".join(b) for b in blocks if b and b[0])
