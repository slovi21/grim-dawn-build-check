"""Resistances: difficulty penalty, caps, need, resistance reduction (pure)."""
from __future__ import annotations

from .policy import Confidence, rule

RESISTS = ("Fire", "Cold", "Lightning", "Acid", "Pierce", "Bleeding", "Vitality", "Aether", "Chaos")
FIELD = {"Fire": "defensiveFire", "Cold": "defensiveCold", "Lightning": "defensiveLightning",
         "Acid": "defensivePoison", "Pierce": "defensivePierce", "Bleeding": "defensiveBleeding",
         "Vitality": "defensiveLife", "Aether": "defensiveAether", "Chaos": "defensiveChaos",
         "Physical": "defensivePhysical"}
ELEMENTAL = ("Fire", "Cold", "Lightning")


@rule("raw_resist", Confidence.EXACT, "Game Guide; 'Elemental' counts on Fire/Cold/Lightning, "
      "'% Total Resist' (1.3) on all nine non-physical types")
def raw_resist(fields: dict, name: str) -> float:
    """Resistance of one type before the difficulty penalty."""
    v = fields.get(FIELD[name], 0.0)
    if name in ELEMENTAL:
        v += fields.get("defensiveElementalResistance", 0.0)
    if name in RESISTS:
        v += max(fields.get("defensiveAllResistance", 0.0), 0.0)
    return v


@rule("own_cap", Confidence.EXACT, "playerDefenseCap 80 + '% Maximum <type> Resistance' (+ all), max 95")
def own_cap(fields: dict, name: str, base: float = 80.0, hard: float = 95.0) -> float:
    k = base + fields.get(FIELD[name] + "MaxResist", 0.0)
    if name in RESISTS:
        k += fields.get("defensiveAllMaxResist", 0.0)
    return min(k, hard)


@rule("rr_chain", Confidence.TESTED, "Crate forum 47174 (controlled tests), wiki example")
def rr_chain(res: float, minus_pct=(), reduced_pct=(), reduced_flat=()) -> float:
    """Target resistance after RR: all type-1 '-X%' add up; then only the
    strongest type-2 'X% Reduced' (multiplicative, only on positive res);
    last only the strongest type-3 'Reduced by X'."""
    r = res - sum(minus_pct)
    z = max(reduced_pct, default=0.0)
    if r > 0:
        r = r * (1 - z / 100)
    return r - max(reduced_flat, default=0.0)


@rule("res_factor", Confidence.EXACT, "Game Guide Combat")
def res_factor(res_after_rr: float, cap: float = 100.0) -> float:
    """Share of damage that passes (negative resistance raises damage)."""
    return 1 - min(res_after_rr, cap) / 100
