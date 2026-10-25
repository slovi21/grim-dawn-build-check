"""Armor and absorption (pure)."""
from __future__ import annotations

from .policy import Confidence, rule


@rule("absorption", Confidence.EXACT, "Game Guide Combat: 70% base, bonuses multiply, max 100%")
def absorption(bonus_pct: float, base: float = 70.0, maximum: float = 100.0) -> float:
    return min(maximum, base * (1 + bonus_pct / 100))


def absorption_bonus_for(target: float = 100.0, base: float = 70.0) -> float:
    """Bonus % needed for the target absorption (42.86 for 100%)."""
    return (target / base - 1) * 100


@rule("armor_hit", Confidence.EXACT, "Game Guide Combat, wiki Armor")
def armor_hit(physical: float, armor: float, absorption_share: float) -> float:
    """Physical damage after the armor of the hit piece; absorption 0..1."""
    return physical - min(physical, armor) * min(max(absorption_share, 0.0), 1.0)


@rule("armor_expected", Confidence.EXACT, "combatRegion...Chance (gameengine.dbr)")
def armor_expected(physical: float, per_slot: dict, absorption_share: float, zones: dict) -> float:
    return sum(share * armor_hit(physical, per_slot.get(z, 0.0), absorption_share)
               for z, share in zones.items())


def armor_weighted(per_slot: dict, zones: dict) -> float:
    """Sum of armor x hit share of the zone (target 'armor weighted')."""
    return sum(share * per_slot.get(z, 0.0) for z, share in zones.items())
