"""Onslaught stacks (Fangs of Asterkarn) - pure."""
from __future__ import annotations

from .policy import Confidence, rule

PER_STACK = 0.15


@rule("onslaught_multiplier", Confidence.EXACT,
      "Game Guide Combat: 15% per stack; Zantai (Crate forum 155852): '+15% absolute damage "
      "(calculated after other bonuses are applied)' = own multiplier. 1.3.1 playtest: 12% (not live)")
def onslaught_multiplier(stacks: int, per_stack: float = PER_STACK) -> float:
    return 1.0 + per_stack * max(0, stacks)


@rule("onslaught_max", Confidence.EXACT,
      "max stacks = sum of the instances: Game Guide Item Skills 'two items that alter the same skill "
      "... stack their benefits'; Zantai: '+X Onslaught Stacks increase the maximum stacks'")
def onslaught_max(generator_levels) -> int:
    return int(sum(max(0, int(x)) for x in generator_levels))


@rule("is_spender", Confidence.EXACT,
      "Game Guide Combat: cooldown abilities consume stacks, except debuffs, pet summons and "
      "auto-cast item procs")
def is_spender(cooldown: float | None, is_debuff: bool = False, is_pet: bool = False,
               is_autocast: bool = False) -> bool:
    return cooldown is not None and cooldown > 0 and not (is_debuff or is_pet or is_autocast)


@rule("onslaught_expiry", Confidence.TESTED,
      "stacks fall off after skillComboChargeDuration (Gruldir's Cleaver: 5 s, game database); "
      "a later timeline uses this state function (combat spec V2 8, RG-07)")
def onslaught_state(stacks: int, age_s: float, duration_s: float = 5.0) -> int:
    """Stacks still available `age_s` seconds after the last stack was gained."""
    return max(0, int(stacks)) if age_s <= duration_s else 0


@rule("onslaught_expected", Confidence.ASSUMED,
      "stacks a spender finds: every use of a generator adds one stack (up to the maximum), they fall off "
      "skillComboChargeDuration after the last gain, so the uses of the generator in the window before the spender "
      "(its cooldown without the spender's own animation, at most the duration) are the stacks; the spender takes "
      "them (one modelled spender per rotation). Not confirmed in game (plan .72 N5, KG-09)")
def expected_stacks(n_max: int, uses_per_s: float, window_s: float) -> int:
    """Whole stacks a spender finds: min(maximum, generator uses per second x window); 0 without generator
    uses, a window or a maximum."""
    if n_max <= 0 or uses_per_s <= 0 or window_s <= 0:
        return 0
    return max(0, min(int(n_max), int(uses_per_s * window_s + 1e-9)))
