"""Finding: one result of the rule catalogue (port of the .35 pruefungen `_befund` dict).

Legacy dict key -> Finding field: id -> code, kategorie -> category, schwere -> severity,
titel -> title, detail -> detail, beleg -> evidence, vorschlag -> fix,
pruefen_im_spiel -> check_in_game. Severities keep the legacy values (ERROR / WARNING / NOTE):
ERROR = certainly without effect, WARNING = probably a disadvantage, NOTE = worth a look, may be
intended.
"""
from __future__ import annotations

from dataclasses import dataclass

ERROR, WARNING, NOTE = "ERROR", "WARNING", "NOTE"
SEVERITY_RANK = {ERROR: 0, WARNING: 1, NOTE: 2}

CATEGORY_NAMES = {"A": "No effect", "B": "Does not stack", "C": "Self-penalty",
                  "D": "Bindings", "E": "Point spread"}


@dataclass(frozen=True)
class Finding:
    code: str              # A1, A1b, A3, ..., E6
    category: str          # A..E
    severity: str          # ERROR / WARNING / NOTE
    title: str
    detail: str
    evidence: str          # DB / Wiki / Guide / Forum / Savefile / own ("eigen")
    fix: str = ""
    check_in_game: str = ""
    subject: str = ""      # report v2: the record the finding is about (D1: the power, C1 of a skill: the skill)


def points(n: int, word: str = "point") -> str:
    """'1 point' / '2 points' (.59, Paket 5: the PC run showed 'War Cry: 1 points')."""
    return f"{n} {word}" + ("" if n == 1 else "s")
