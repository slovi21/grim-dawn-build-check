"""Attributes, OA/DA, Health and Energy (pure; constants passed in)."""
from __future__ import annotations

from .policy import Confidence, rule

DEFAULTS = {"base_attribute": 50.0, "life_per_physique": 2.5, "life_per_cunning": 1.0,
            "life_per_spirit": 1.5, "mana_per_spirit": 2.0, "base_life": 250.0,
            "base_mana": 250.0, "base_da": 65.0, "base_oa": 65.0, "per_level_ability": 12.0,
            "ability_per_attribute": 0.5, "ability_constant": 53.0}


def attribute(base: float, flat: float, pct: float) -> float:
    return (base + flat) * (1 + pct / 100)


@rule("abilities", Confidence.EXACT, "combatformulas.dbr, playerlevels.dbr, malepc01.dbr; "
      "checked against the in-game sheet (Cpt Wolf)")
def abilities(base: dict, fields: dict, level: int, c: dict | None = None) -> dict:
    """base {physique, cunning, spirit} from the savefile; fields = summed bonus fields."""
    c = {**DEFAULTS, **(c or {})}
    phy = attribute(base.get("physique") or 0, fields.get("characterStrength", 0),
                    fields.get("characterStrengthModifier", 0))
    cun = attribute(base.get("cunning") or 0, fields.get("characterDexterity", 0),
                    fields.get("characterDexterityModifier", 0))
    spi = attribute(base.get("spirit") or 0, fields.get("characterIntelligence", 0),
                    fields.get("characterIntelligenceModifier", 0))
    k = c["ability_per_attribute"]
    da = ((fields.get("characterDefensiveAbility", 0) + c["base_da"] + level * c["per_level_ability"]
           + phy * k) * (1 + fields.get("characterDefensiveAbilityModifier", 0) / 100)) \
        + c["ability_constant"]
    oa = ((fields.get("characterOffensiveAbility", 0) + c["base_oa"] + level * c["per_level_ability"]
           + cun * k) * (1 + fields.get("characterOffensiveAbilityModifier", 0) / 100)) \
        + c["ability_constant"]
    a0 = c["base_attribute"]
    health = ((c["base_life"] + (phy - a0) * c["life_per_physique"] + (cun - a0) * c["life_per_cunning"]
               + (spi - a0) * c["life_per_spirit"] + fields.get("characterLife", 0))
              * (1 + fields.get("characterLifeModifier", 0) / 100))
    energy = ((c["base_mana"] + (spi - a0) * c["mana_per_spirit"] + fields.get("characterMana", 0))
              * (1 + fields.get("characterManaModifier", 0) / 100))
    return {"physique": phy, "cunning": cun, "spirit": spi, "da": da, "oa": oa,
            "health": health, "energy": energy}
