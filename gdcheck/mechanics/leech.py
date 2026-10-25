"""Life steal = % Attack Damage Converted to Health, ADctH (pure, combat spec V2 4-5, 10).

Game Guide Combat (checked 2026-10-01): life steal on gear applies only to weapon
attacks and scales with % weapon damage, '% Weapon damage beyond 100% on skills will
not scale life steal any further'; 'only the direct damage is considered'; life steal
on a skill 'applies to all of that skill's direct damage'.
All rates here are fractions (0.12 = 12 %)."""
from __future__ import annotations

from .policy import Confidence, rule


@rule("life_steal_wd_scale", Confidence.EXACT, "Game Guide Combat: scales with %WD, not beyond 100%")
def global_scale(wd_pct: float) -> float:
    return min(1.0, max(wd_pct, 0.0) / 100.0)


@rule("target_llr", Confidence.TESTED,
      "monster Life Leech Resistance also lowers the attacker's life steal - community consensus "
      "(Crate forum 46968: 'heros and bosses have resistance to life leech ... Superbosses ... 95%'; "
      "Steam: reduce it with Will of Ratosh / reduced resistance); no developer quote")
def after_target(rate: float, llr: float) -> float:
    return max(rate, 0.0) * (1.0 - min(max(llr, 0.0), 1.0))


@rule("life_steal", Confidence.EXACT,
      "Game Guide Combat: gear life steal on weapon attacks only, scaled by min(1, %WD/100); "
      "skill life steal on all direct damage of the skill; DoT never")
def heal(direct: float, wd_pct: float, weapon_attack: bool, global_rate: float,
         local_rate: float, llr: float = 0.0) -> dict:
    """Raw heal of ONE damage event. `direct` = final direct damage of the event (after
    hit/crit and target mitigation); DoT must not be passed in."""
    direct = max(direct, 0.0)
    g = global_rate * global_scale(wd_pct) if weapon_attack else 0.0
    hg = direct * after_target(g, llr)
    hl = direct * after_target(max(local_rate, 0.0), llr)
    return {"global_rate": g, "local_rate": max(local_rate, 0.0), "heal_global": hg,
            "heal_local": hl, "raw": hg + hl}


@rule("overheal", Confidence.EXACT, "combat spec V2 10: effective = min(raw, missing health)")
def effective(raw_heal: float, max_health: float, current_health: float) -> tuple[float, float]:
    """(effective heal, overheal) - needs the current health of a timeline."""
    missing = max(0.0, max_health - current_health)
    eff = min(max(raw_heal, 0.0), missing)
    return eff, max(raw_heal, 0.0) - eff
