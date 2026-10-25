"""Player-View terminology (N-008, N-016): forbidden technical terms and the
abbreviations allowed in the Expert View (Crate forum 'GD Acronym and
Abbreviation List'). Versioned - the terminology test reads this file."""
from __future__ import annotations

import re

VERSION = "W1"

# Not allowed in the Player View (whole words, case-insensitive)
FORBIDDEN = ("PtH", "RR", "DpH", "DPS", "EXACT", "TESTED", "HEURISTIC", "ASSUMED", "ESTIMATE",
             "confidence", "fact", "community", "proxy", "index", "record", "records", "schema",
             "dbr", "provenance", "counterfactual", "ruleset", "golden", "xfail")
_FORB_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in FORBIDDEN) + r")\b", re.I)
_SECTION_RE = re.compile(r"(?:->|see)\s*(?:section\s*)?\d+(?:\.\d+)?\b|\bA\d{1,2}\b|\bE\d\b")

# Abbreviations (Crate list) the Expert View may use
GLOSSARY = {"OA": "Offensive Ability", "DA": "Defensive Ability", "RR": "Resistance Reduction",
            "Res": "Resistance", "CC": "Crowd Control", "CDR": "Cooldown Reduction",
            "DoT": "Damage over Time", "PtH": "Probability to Hit", "WPS": "Weapon Pool Skill",
            "SR": "Shattered Realm", "AS": "Attack Speed", "WD": "Weapon Damage",
            "Occu": "Occultist", "Demo": "Demolitionist", "NB": "Nightblade", "Arca": "Arcanist",
            "Sorc": "Sorcerer", "BM": "Blademaster", "DpH": "Damage per Hit"}

GERMAN_WORDS = ("und", "oder", "nicht", "mit", "ohne", "Schaden", "Ziel", "Lücke", "Ruestung",
                "Rüstung", "Kampf", "Stufe", "Vorschlag", "Spieler", "Werte", "fehlt", "Bogen")
_DE_RE = re.compile(r"\b(" + "|".join(GERMAN_WORDS) + r")\b")


def forbidden_in(text: str) -> list[str]:
    hits = [m.group(0) for m in _FORB_RE.finditer(text)]
    hits += [m.group(0) for m in _SECTION_RE.finditer(text)]
    return hits


def german_in(text: str) -> list[str]:
    return [m.group(0) for m in _DE_RE.finditer(text)]
