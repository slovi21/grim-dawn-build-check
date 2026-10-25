"""Character values by the formulas of the game database, as the candidate generators use them
to value a changed sheet (port of .35 `assess_core.abilities` - Task 7 of release .67/.68).

    DA/OA:  (flat + base + level*12 + attribute*0.5) * (1 + %/100) + 53
            (combatformulas.dbr; base 65 from malepc01.dbr; bonusDV = 0)
    Health: 250 + per attribute point above 50 the increment of playerlevels.dbr
            (Physique 20/8, Cunning 8/8, Spirit 12/8) + flat, then * (1 + %)
    Energy: 250 + (Spirit - 50) * 16/8 + flat, then * (1 + %)

Cross-checked on Cpt Wolf (1.3.0.8): Physique, Cunning, Spirit and Energy exactly as
grimtools, OA exactly as in game. Fallbacks = values of 1.3.0.8.
"""
from __future__ import annotations

_TABLE_LEVEL = "records/creatures/pc/playerlevels.dbr"
_TABLE_PC = "records/creatures/pc/malepc01.dbr"


def _table(index, name) -> dict:
    return (getattr(index, "tabellen", None) or {}).get(name) or {}


def abilities(facts, sheet, index=None) -> dict:
    """{"physique", "cunning", "spirit", "da", "oa", "health", "energy"} of a sheet. facts: the
    SaveFacts mapping (attribute_basis, charakter.level)."""
    base = facts.get("attribute_basis") or {}
    lvl = (facts.get("charakter") or {}).get("level") or 0
    lv = _table(index, _TABLE_LEVEL)
    pc = _table(index, _TABLE_PC)
    inc_attr = float(lv.get("strengthIncrement", 8))
    life_str = float(lv.get("lifeIncrement", 20)) / inc_attr
    life_dex = float(lv.get("lifeIncrementDexterity", 8)) / inc_attr
    life_int = float(lv.get("lifeIncrementIntelligence", 12)) / inc_attr
    mana_int = float(lv.get("manaIncrement", 16)) / inc_attr
    base_attr = float(pc.get("characterStrength", 50))
    base_life = float(pc.get("characterLife", 250))
    base_mana = float(pc.get("characterMana", 250))
    base_da = float(pc.get("characterDefensiveAbility", 65))
    base_oa = float(pc.get("characterOffensiveAbility", 65))

    def attribute(name, flat, pct):
        return ((base.get(name) or 0) + sheet.get(flat, 0)) * (1 + sheet.get(pct, 0) / 100)

    physique = attribute("physique", "characterStrength", "characterStrengthModifier")
    cunning = attribute("cunning", "characterDexterity", "characterDexterityModifier")
    spirit = attribute("spirit", "characterIntelligence", "characterIntelligenceModifier")
    da = ((sheet.get("characterDefensiveAbility", 0) + base_da + lvl * 12 + physique * 0.5)
          * (1 + sheet.get("characterDefensiveAbilityModifier", 0) / 100)) + 53
    oa = ((sheet.get("characterOffensiveAbility", 0) + base_oa + lvl * 12 + cunning * 0.5)
          * (1 + sheet.get("characterOffensiveAbilityModifier", 0) / 100)) + 53
    health = ((base_life + (physique - base_attr) * life_str
               + (cunning - base_attr) * life_dex + (spirit - base_attr) * life_int
               + sheet.get("characterLife", 0))
              * (1 + sheet.get("characterLifeModifier", 0) / 100))
    energy = ((base_mana + (spirit - base_attr) * mana_int + sheet.get("characterMana", 0))
              * (1 + sheet.get("characterManaModifier", 0) / 100))
    return {"physique": physique, "cunning": cunning, "spirit": spirit,
            "da": da, "oa": oa, "health": health, "energy": energy}
