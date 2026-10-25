"""Damage and healing events (combat spec V2 1.1, 3, 6).

One action is split into hit events: a form ability with two weapons gives a
main-hand and an off-hand event. Every event keeps direct damage and DoT apart,
so life steal (direct only), procs and per-hand values can be derived from the
SAME numbers as the damage (spec rule: no separate approximation for sustain).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DamageEvent:
    action_id: str
    hand: str                    # "main" | "off" | "spell"
    target: str | None           # None = sheet (no target, no hit/crit)
    wd_pct: float                # % weapon damage of the action (life steal scaling)
    weapon_attack: bool          # gear life steal applies only to weapon attacks
    direct_by_type: dict         # final expected direct damage per type
    dot_by_type: dict            # final expected DoT per second per type
    pre_hit_direct: float        # direct after resistances/armor, before the hit/crit factor
    pth: float = 0.0
    hit_factor: float = 1.0      # miss + glancing + crit tiers (expected value)
    miss_share: float = 0.0
    crit_share: float = 0.0
    onslaught: int = 0
    rules: tuple = field(default_factory=tuple)

    @property
    def direct(self) -> float:
        return sum(self.direct_by_type.values())

    @property
    def dot(self) -> float:
        return sum(self.dot_by_type.values())

    @property
    def has_direct_damage(self) -> bool:
        return self.direct > 0


@dataclass(frozen=True)
class HealingEvent:
    action_id: str
    hand: str
    target: str | None
    eligible_direct: float       # final direct damage the heal is computed from
    global_rate: float           # gear/buff life steal after the %WD scale (0..1)
    local_rate: float            # skill life steal (0..1)
    llr: float                   # target life leech resistance (0..1)
    heal_global: float
    heal_local: float
    raw_heal: float
    reason: str = ""

