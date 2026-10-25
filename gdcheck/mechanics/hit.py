"""Hit chance and critical hits (pure)."""
from __future__ import annotations

from .policy import Confidence, rule

PTH_MIN = 55.0
CRIT_TIERS = ((90, 1.1), (105, 1.2), (120, 1.3), (130, 1.4), (135, 1.5))


@rule("pth", Confidence.EXACT, "combatformulas.dbr probabilityToHitEquation; Game Guide Combat")
def pth(oa: float, da: float, pth_min: float = PTH_MIN) -> float:
    """Probability to hit in percent; never below 55."""
    if oa <= 0:
        return pth_min
    value = (((oa / ((da / 3.5) + oa)) * 300) * 0.3) \
        + (((((oa * 3.25) + 10000) - (da * 3.25)) / 100) * 0.7) - 50
    return max(pth_min, value)


@rule("expected_hit", Confidence.TESTED, "Guide thresholds, crit calculator (Crate forum 95882)")
def expected_hit(p: float, crit_damage: float = 0.0, tiers=CRIT_TIERS) -> dict:
    """Expected damage factor per attack at PtH p and Crit Damage (0.35 = +35%).

    Returns {"factor", "hit", "crit"}: factor includes misses, glancing hits
    below PtH 70 and the crit multipliers (tier widths 89/15/15/10/5/rest)."""
    if p < 70:
        hit = p / 100
        return {"factor": hit * glancing_factor(p), "hit": hit, "crit": 0.0}
    d = max(100.0, p)
    w = [min(p, 89.0),
         min(max(p - 89, 0), 15), min(max(p - 104, 0), 15),
         min(max(p - 119, 0), 10), min(max(p - 129, 0), 5), max(p - 134, 0)]
    mult = [1.0] + [m + crit_damage for _, m in tiers]
    factor = sum(wi * mi for wi, mi in zip(w, mult)) / d
    return {"factor": factor, "hit": min(p, 100) / 100, "crit": sum(w[1:]) / d}


@rule("glancing", Confidence.EXACT, "Game Guide Combat: 'If your PTH is lower than 70, any attacks that "
      "land will do reduced damage' (PTH/70)")
def glancing_factor(p: float) -> float:
    return min(1.0, max(p, 0.0) / 70.0)


@rule("dot_crit", Confidence.TESTED,
      "DoTs can crit when applied and the crit lasts the whole duration (Crate forum 37738 "
      "'DoTs for dummies'); the Game Guide is silent")
def dot_factor(expected: dict) -> float:
    """Factor for DoT applied by a hit: same as direct (ASSUMED, combat plan E9)."""
    return expected["factor"]


@rule("miss_no_dot", Confidence.ASSUMED, "a missed attack applies no DoT (combat spec V2 7)")
def miss_share(p: float) -> float:
    return 1.0 - min(max(p, 0.0), 100.0) / 100.0


def outcomes(p: float, crit_damage: float = 0.0, tiers=CRIT_TIERS) -> list[tuple[float, float]]:
    """[(probability, damage multiplier)] of one attack: landed outcomes only (the miss
    share is 1 - sum of probabilities). sum(prob x mult) == expected_hit(...)['factor']."""
    if p < 70:
        return [(p / 100, glancing_factor(p))]
    d = max(100.0, p)
    w = [min(p, 89.0),
         min(max(p - 89, 0), 15), min(max(p - 104, 0), 15),
         min(max(p - 119, 0), 10), min(max(p - 129, 0), 5), max(p - 134, 0)]
    mult = [1.0] + [m + crit_damage for _, m in tiers]
    return [(wi / d, mi) for wi, mi in zip(w, mult) if wi > 0]
