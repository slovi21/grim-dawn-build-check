"""Global damage percentages per type and the main damage type (port of the .35 schaden
module, Task 4a). Pure functions over a sheet dict, no state.

Sources:
  - attribute share from combatformulas.dbr (1.3.0.8), confirms Val's Steam guide (2017):
        magic direct damage        +1 % per 2.15 Spirit   (intelligenceDV/215)
        magic damage over time     +1 % per 2.00 Spirit   (/200)
        Physical and Pierce        +1 % per 2.45 Cunning  (dexterityDV/245)
        Internal Trauma, Bleeding  +1 % per 2.15 Cunning  (/215)
    The attribute share adds to the percentage bonuses [Guide].
  - "+% All Damage" acts on every type, not on retaliation [Guide].
  - "+% Elemental" acts fully on Fire, Cold, Lightning, NOT on Burn, Frostburn,
    Electrocute [Wiki].
  - Only GLOBAL sources count; percentages on skill modifiers apply to that skill only
    [Crate forum, Stupid_Dragon 2020].

Target 2000-3000 % for the main damage type: user proposal, community experience, not proven
in any of the checked sources.

Dict entries and the kind values (direkt_magisch, dot_magisch, direkt_phys, dot_phys) keep the
legacy spelling so the generators switch by import. Old -> new: TYPEN -> DAMAGE_TYPES,
ELEMENTAR -> ELEMENTAL, UMWANDLUNG_TYPEN -> CONVERSION_TYPES, RICHTWERT_MIN/MAX ->
TARGET_MIN/MAX, attributanteil -> attribute_share, prozente -> damage_percentages,
hauptschaden -> main_damage, pet_anteil -> pet_share, ist_pet_build -> is_pet_build.
"""
from __future__ import annotations

from ..stats import damage_mods as _DM
from . import archetype as _AT

TARGET_MIN = 2000
TARGET_MAX = 3000

# type: (field, kind, name)
DAMAGE_TYPES = {
    "Physical":        ("offensivePhysicalModifier", "direkt_phys", "Physical"),
    "Pierce":          ("offensivePierceModifier", "direkt_phys", "Pierce"),
    "Fire":            ("offensiveFireModifier", "direkt_magisch", "Fire"),
    "Cold":            ("offensiveColdModifier", "direkt_magisch", "Cold"),
    "Lightning":       ("offensiveLightningModifier", "direkt_magisch", "Lightning"),
    "Poison":          ("offensivePoisonModifier", "direkt_magisch", "Acid"),
    "Life":            ("offensiveLifeModifier", "direkt_magisch", "Vitality"),
    "Aether":          ("offensiveAetherModifier", "direkt_magisch", "Aether"),
    "Chaos":           ("offensiveChaosModifier", "direkt_magisch", "Chaos"),
    "InternalTrauma":  ("offensiveSlowPhysicalModifier", "dot_phys", "Internal Trauma"),
    "Bleeding":        ("offensiveSlowBleedingModifier", "dot_phys", "Bleeding"),
    "Burn":            ("offensiveSlowFireModifier", "dot_magisch", "Burn"),
    "Frostburn":       ("offensiveSlowColdModifier", "dot_magisch", "Frostburn"),
    "Electrocute":     ("offensiveSlowLightningModifier", "dot_magisch", "Electrocute"),
    "PoisonDot":       ("offensiveSlowPoisonModifier", "dot_magisch", "Poison"),
    "VitalityDecay":   ("offensiveSlowLifeModifier", "dot_magisch", "Vitality Decay"),
}
ELEMENTAL = ("Fire", "Cold", "Lightning")

# conversion type names (conversionInType) -> affected types
CONVERSION_TYPES = {
    "Physical": ("Physical",), "Pierce": ("Pierce",), "Fire": ("Fire",),
    "Cold": ("Cold",), "Lightning": ("Lightning",), "Poison": ("Poison",),
    "Life": ("Life",), "Aether": ("Aether",), "Chaos": ("Chaos",),
    "Elemental": ELEMENTAL,
}
_PET_MARKERS = _AT.PET_TOKENS


# legacy kind names of DAMAGE_TYPES -> the kind names of stats.damage_mods (one formula, review .68)
_KIND = {"direkt_magisch": "direct_magic", "dot_magisch": "dot_magic", "direkt_phys": "direct_phys",
         "dot_phys": "dot_phys"}


def attribute_share(kind: str, cunning: float, spirit: float) -> float:
    """stats.damage_mods.attribute_share with the legacy kind names (unknown kind: 0)."""
    return _DM.attribute_share(_KIND.get(kind, ""), cunning, spirit)


def damage_percentages(sheet: dict, cunning: float | None = None, spirit: float | None = None) -> list:
    """Global damage percentages per type, descending. Without attributes the attribute share
    is left out (attribut None)."""
    every = sheet.get("offensiveTotalDamageModifier", 0)
    elem = sheet.get("offensiveElementalModifier", 0)
    out = []
    for typ, (fld, kind, name) in DAMAGE_TYPES.items():
        own = sheet.get(fld, 0)
        attr = attribute_share(kind, cunning or 0, spirit or 0) if cunning is not None else None
        total = own + every + (elem if typ in ELEMENTAL else 0) + (attr or 0)
        out.append({"typ": typ, "name": name, "art": kind, "eigen": own, "alle": every,
                    "elementar": elem if typ in ELEMENTAL else 0, "attribut": attr, "summe": total})
    out.sort(key=lambda e: -e["summe"])
    return out


def main_damage(sheet: dict, cunning=None, spirit=None) -> dict | None:
    """Strongest type by its own percentage bonus (without the shares equal for every type, so
    "+% All Damage" does not distort the choice)."""
    candidates = [e for e in damage_percentages(sheet, cunning, spirit) if e["eigen"] > 0]
    if not candidates:
        return None
    return max(candidates, key=lambda e: e["eigen"] + e["elementar"] + (e["attribut"] or 0))


def pet_share(skills, index=None) -> float:
    """Share of skill points in summons of summons + own attacks (incl. modifiers) for the save's skill
    list: 0 = no pet, 1 = pure pet build (archetype.pet_share_of, the same rule as the archetype)."""
    return _AT.pet_share_of(((s.get("skill_name", ""), s.get("level", 0)) for s in skills or []), index)


def is_pet_build(skills, index=None) -> bool:
    """Pure pet build: at least 60 % of the points in summons. Without an index the old
    threshold of 30 points applies."""
    if index is None:
        return sum(s.get("level", 0) for s in skills or []
                   if any(t in s.get("skill_name", "").lower() for t in _PET_MARKERS)) >= 30
    return pet_share(skills, index) >= _AT.PET_SHARE
