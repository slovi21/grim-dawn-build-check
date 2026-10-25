"""Incoming damage - Order of Defense (pure, versioned; combat spec V2 12).

Order quoted from the Game Guide 'Combat' (checked 2026-10-01):
  1 Fumble, Dodge and Projectile Deflection
  2 Chance to hit via Offensive Ability vs. Defensive Ability
  3 Shield
  4 % Reduced Damage from Monster Types
  5 Resistances
  6 Armor
  7 Reduced Damage from Monster Types
  8 % Damage Absorption
  9 Damage Absorption
Steps 1, 2 and 4/5/8 are multiplications (order does not matter); 3, 6, 7 and 9
subtract flat amounts, so their position matters - the function keeps the order.
`expected=True` gives the expected value per attack (avoidance, misses and crits
weighted), `expected=False` the landed hit (spike: no avoidance, no miss)."""
from __future__ import annotations

from .armor import armor_hit
from . import hit as H
from .policy import Confidence, rule

ORDER_VERSION = "GD 1.3.0.8 / Game Guide Combat 2026-10-01"
DOT_TYPES = ("Bleeding", "InternalTrauma", "Burn", "Frostburn", "Electrocute", "PoisonDot", "VitalityDecay")


def _landed(direct: dict, res: dict, armor, absorption, block_amount, pct_reduced_monster,
            flat_reduced_monster, pct_absorption, flat_absorption, blocked: bool):
    """Steps 3-9 for ONE landed hit (flat steps act per hit)."""
    dmg = dict(direct)
    if blocked and block_amount > 0:                                     # 3 shield (flat)
        total = sum(dmg.values())
        if total > 0:
            cut = min(total, block_amount)
            dmg = {t: v * (1 - cut / total) for t, v in dmg.items()}
    dmg = {t: v * (1 - pct_reduced_monster / 100) for t, v in dmg.items()}            # 4
    dmg = {t: v * (1 - min(res.get(t, 0.0), 100.0) / 100) for t, v in dmg.items()}    # 5
    after_res = sum(dmg.values())
    if "Physical" in dmg and armor > 0:                                  # 6 armor, physical only
        dmg["Physical"] = armor_hit(dmg["Physical"], armor, absorption)
    after_armor = sum(dmg.values())
    total = after_armor
    if flat_reduced_monster > 0 and total > 0:                           # 7
        total = max(0.0, total - flat_reduced_monster)
    total *= (1 - pct_absorption / 100)                                  # 8
    total = max(0.0, total - flat_absorption)                            # 9
    return total, dmg, after_res, after_armor


@rule("order_of_defense", Confidence.EXACT, "Game Guide Combat - Order of Defense (quoted list)")
def incoming(direct: dict, res: dict, *, armor: float = 0.0, absorption: float = 0.7,
             attacker_oa: float = 0.0, defender_da: float = 0.0, avoid: float = 0.0,
             block_chance: float = 0.0, block_amount: float = 0.0,
             pct_reduced_monster: float = 0.0, flat_reduced_monster: float = 0.0,
             pct_absorption: float = 0.0, flat_absorption: float = 0.0,
             crit_damage: float = 0.0, expected: bool = True) -> dict:
    """Health lost from one attack. direct: type -> raw damage. Flat steps (shield, armor,
    flat reductions/absorption) are applied per landed hit and per crit tier; avoidance,
    misses and block chance are weighted afterwards (expected value). `expected=False`:
    one landed, unblocked, non-crit hit (spike)."""
    raw = sum(direct.values())
    args = (res, armor, absorption, block_amount, pct_reduced_monster, flat_reduced_monster,
            pct_absorption, flat_absorption)
    if not expected:
        total, dmg, r5, r6 = _landed(direct, *args, blocked=False)
        trace: tuple = (("raw", raw), ("resistances", r5), ("armor", r6), ("absorption", total))
        return {"total": total, "by_type": dmg, "trace": trace, "version": ORDER_VERSION}
    outs = H.outcomes(H.pth(attacker_oa, defender_da), crit_damage) if attacker_oa > 0 else [(1.0, 1.0)]
    bc = min(max(block_chance, 0.0), 1.0)
    keep = 1 - min(max(avoid, 0.0), 1.0)
    total = r5 = r6 = 0.0
    by_type: dict = {}
    for prob, mult in outs:
        hit = {t: v * mult for t, v in direct.items()}
        for blocked, share in ((True, bc), (False, 1 - bc)):
            if share <= 0:
                continue
            t_, dmg, a5, a6 = _landed(hit, *args, blocked=blocked)
            w = keep * prob * share
            total += w * t_
            r5 += w * a5
            r6 += w * a6
            for k, v in dmg.items():
                by_type[k] = by_type.get(k, 0.0) + w * v
    trace = (("raw", raw), ("avoidance / hit / crit / block weighted", raw * keep), ("resistances", r5),
             ("armor", r6), ("absorption", total))
    return {"total": total, "by_type": by_type, "trace": trace, "version": ORDER_VERSION}
