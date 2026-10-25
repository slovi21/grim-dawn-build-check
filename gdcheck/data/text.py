"""Small text helpers and fixed name tables shared by the data layer
(taken over from the .35 extractor, part 5)."""
from __future__ import annotations

import re


def short(dbr):
    """records/items/gearhead/d223_head.dbr -> d223_head"""
    if not dbr:
        return ""
    return dbr.rsplit("/", 1)[-1].replace(".dbr", "")


FACTION_SLOT_KEYS = [
    "factionPlayer", "factionSurvivors", "factionAetherials", "factionCthonians",
    "factionOutlaws", "factionBeasts", "factionDrifters", "factionNeutralNPC",
] + [f"factionUser{i}" for i in range(2, 41)]

FACTION_FALLBACK_NAMES = {
    "factionPlayer": "Player",
    "factionSurvivors": "Devil's Crossing",
    "factionAetherials": "Aetherials",
    "factionCthonians": "Chthonians",
    "factionOutlaws": "Cronley's Gang",
    "factionBeasts": "Beasts",
    "factionDrifters": "Rovers",
    "factionNeutralNPC": "Neutral NPCs",
}

# Reputation tiers (.63: official wiki grimdawn.fandom.com/wiki/Factions - Tolerated 0, Friendly 1,500,
# Respected 5,000, Honored 10,000, Revered 25,000; Hostile 1-1,499, Despised 1,500, Hated 8,000, Nemesis
# 20,000 infamy). Before: an empirical table (Revered at 40,000) that called 25,000 'Honored'.
REPUTATION_TIERS = [
    ("Nemesis", -1e12), ("Hated", -19999), ("Despised", -7999), ("Hostile", -1499),
    ("Tolerated", 0), ("Friendly", 1500), ("Respected", 5000), ("Honored", 10000),
    ("Revered", 25000),
]

MASTERY_NAMES = {
    1: "Soldier", 2: "Demolitionist", 3: "Occultist", 4: "Nightblade",
    5: "Arcanist", 6: "Shaman", 7: "Inquisitor", 8: "Necromancer",
    9: "Oathkeeper", 10: "Berserker",
}

EQUIP_SLOTS = [
    "Head", "Amulet", "Chest", "Legs", "Feet", "Hands",
    "Ring 1", "Ring 2", "Waist", "Shoulders", "Medal", "Relic",
]

DIFFICULTIES = ["Normal", "Elite", "Ultimate"]


def reputation_tier(value: float) -> str:
    name = REPUTATION_TIERS[0][0]
    for tier, threshold in REPUTATION_TIERS:
        if value >= threshold:
            name = tier
    return name


def ist_wertname(text):
    """True, wenn ein aufgeloester Affixname in Wahrheit nur ein Bonuswert ist.

    Crafting- und Unique-Affixe haben keinen eigenen Namen; ihr Lokalisierungs-
    Tag liefert stattdessen die Bonuszeile. Heraus kaemen sonst Item-Namen wie
    "25 Vortex Stone resist 30% + 154 retal" oder "Modifier: 12%"."""
    t = (text or "").strip()
    if not t:
        return False
    if t[0].isdigit() or t[0] in "+-":
        return True
    return "%" in t


def clean_text(s):
    if not s:
        return ""
    return re.sub(r"\^.", "", str(s)).replace("{^E}", "").strip()


_GENDER_RE = re.compile(r"\[ms\](.*?)\[fs\](.*)", re.S)


def gender_text(s, male=True):
    """'[ms]Sorcerer[fs]Sorceress' -> Form passend zum Geschlecht des Charakters."""
    if not s:
        return ""
    m = _GENDER_RE.search(s)
    if not m:
        return s
    return (m.group(1) if male else m.group(2)).strip()




is_value_name = ist_wertname


_FIELD_EXTRA = {"defensiveFreeze": "Freeze Resistance", "defensiveStun": "Stun Resistance",
                "defensivePetrify": "Petrify Resistance", "defensiveTrap": "Trap Resistance",
                "defensiveSlowLifeLeach": "Life Leech Resistance", "defensiveSlowManaLeach": "Energy Leech Resistance",
                "defensiveTotalSpeedResistance": "Slow Resistance", "defensiveDisruption": "Skill Disruption Protection",
                "defensiveElementalResistance": "Elemental Resistance", "defensiveAllResistance": "all Resistances",
                "defensiveAbsorption": "Armor Absorption", "defensiveProtection": "Armor",
                "characterLife": "Health", "characterLifeModifier": "Health %",
                "characterDefensiveAbility": "Defensive Ability", "characterOffensiveAbility": "Offensive Ability"}


def field_label(field: str) -> str:
    """Readable game term for a database field (player-facing text never shows raw field names)."""
    from ..mechanics.resist import FIELD
    rev = {v: f"{k} Resistance" for k, v in FIELD.items()}
    if field in rev:
        return rev[field]
    if field in _FIELD_EXTRA:
        return _FIELD_EXTRA[field]
    for f, k in rev.items():
        if field == f + "MaxResist":
            return "Maximum " + k
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", re.sub(r"^(defensive|offensive|character)", "", field)).strip()
    return words[:1].upper() + words[1:] if words else field
