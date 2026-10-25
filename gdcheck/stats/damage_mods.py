"""Global damage % per type incl. the attribute share (port of .35 schaden.py)."""
from __future__ import annotations

TYPES = {
    "Physical": ("offensivePhysicalModifier", "direct_phys", "Physical"),
    "Pierce": ("offensivePierceModifier", "direct_phys", "Pierce"),
    "Fire": ("offensiveFireModifier", "direct_magic", "Fire"),
    "Cold": ("offensiveColdModifier", "direct_magic", "Cold"),
    "Lightning": ("offensiveLightningModifier", "direct_magic", "Lightning"),
    "Poison": ("offensivePoisonModifier", "direct_magic", "Acid"),
    "Life": ("offensiveLifeModifier", "direct_magic", "Vitality"),
    "Aether": ("offensiveAetherModifier", "direct_magic", "Aether"),
    "Chaos": ("offensiveChaosModifier", "direct_magic", "Chaos"),
    "InternalTrauma": ("offensiveSlowPhysicalModifier", "dot_phys", "Internal Trauma"),
    "Bleeding": ("offensiveSlowBleedingModifier", "dot_phys", "Bleeding"),
    "Burn": ("offensiveSlowFireModifier", "dot_magic", "Burn"),
    "Frostburn": ("offensiveSlowColdModifier", "dot_magic", "Frostburn"),
    "Electrocute": ("offensiveSlowLightningModifier", "dot_magic", "Electrocute"),
    "PoisonDot": ("offensiveSlowPoisonModifier", "dot_magic", "Poison"),
    "VitalityDecay": ("offensiveSlowLifeModifier", "dot_magic", "Vitality Decay"),
}
ELEMENTAL = ("Fire", "Cold", "Lightning")


def attribute_share(kind: str, cunning: float, spirit: float) -> float:
    """combatformulas.dbr 1.3.0.8: +1% per 2.15 Spirit (magic direct), 2.00 (magic DoT),
    2.45 Cunning (physical/pierce), 2.15 Cunning (trauma/bleed)."""
    return {"direct_magic": spirit / 2.15, "dot_magic": spirit / 2.00,
            "direct_phys": cunning / 2.45, "dot_phys": cunning / 2.15}.get(kind, 0.0)


def per_type(fields: dict, cunning: float, spirit: float) -> dict:
    """{type: {"own", "all", "elemental", "attribute", "total", "name", "kind"}}"""
    alld = fields.get("offensiveTotalDamageModifier", 0.0)
    elem = fields.get("offensiveElementalModifier", 0.0)
    out = {}
    for t, (f, kind, name) in TYPES.items():
        own = fields.get(f, 0.0)
        a = attribute_share(kind, cunning, spirit)
        e = elem if t in ELEMENTAL else 0.0
        out[t] = {"own": own, "all": alld, "elemental": e, "attribute": a,
                  "total": own + alld + e + a, "name": name, "kind": kind}
    return out


def main_type(mods: dict) -> str | None:
    """Strongest type by own % (+elemental, +attribute); None without own %."""
    cands = [(v["own"] + v["elemental"] + v["attribute"], t) for t, v in mods.items() if v["own"] > 0]
    return max(cands)[1] if cands else None
