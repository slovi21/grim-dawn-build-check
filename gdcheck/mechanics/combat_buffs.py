"""Combat buffs with a running time and the energy budget (pure; M18, rules a and b).

A buff that a skill or a devotion power starts is on only part of the fight. `triggered_uptime` gives that part from the number
of rolls per second, the chance of one roll, the buff's duration and its own cooldown. `energy_regen` is the sheet's energy
regeneration and `energy_run` the fight against the dummy with a pool that runs dry: the damage rate holds until the pool is
empty and then falls to what the regeneration pays for.
"""
from __future__ import annotations

import math

from .policy import Confidence, rule
from .procs import proc_rate

ENERGY_REGEN_BASE = 6.5       # energy per second at Spirit 50 with no bonus at all (level 1 and level 100 alike, E3)
SPIRIT_FLAT_BASE = 50.0       # the Spirit above 50 adds (Spirit - 50) / 100 energy per second to it
SPIRIT_FLAT_PER = 0.01
SPIRIT_PCT_PER = 100.0 / 384.0   # percent per Spirit point (Spirit / 384 = 0.2604 %) - on the flat bonuses of the gear only


@rule("triggered_uptime", Confidence.DERIVED,
      "a buff rolled by events (a skill's use, a bound celestial power) with a chance per roll, a duration and its own "
      "cooldown: one start per E[interval] of the proc formula (combat spec V2 9), on for `duration` after each start, "
      "at most all the time; no cooldown and a sure roll (Deadly Momentum after every finale) = min(1, rate x duration)")
def triggered_uptime(events_per_s: float, chance: float, duration: float, cooldown: float = 0.0) -> float:
    """Share of the fight a triggered buff is on, 0..1."""
    if duration <= 0 or events_per_s <= 0 or chance <= 0:
        return 0.0
    return min(1.0, proc_rate(events_per_s, chance, cooldown) * duration)


@rule("energy_regen", Confidence.TESTED,
      "energy regeneration = (6.5 + (Spirit - 50) / 100) + flat x (1 + percent / 100) x (1 + Spirit / 384): the first bracket "
      "is the attribute part (the base 6.5 and the flat Spirit term 8.93 at Spirit 943 = the 15.43 of the sheet's tooltip), the "
      "percentage of the gear and of the Spirit are two factors that scale the flat bonuses of gear, devotions and skills "
      "and neither the base nor the Spirit's flat term (the 55.88 of the same tooltip = 7.7 x 2.1 x 3.456). Sheet readings of "
      "the game (1.3.0.8 / 1.3.1.2): Cpt Blade 39.40, Cpt Wolf 17.03 and 29.39 (02.10.2026), Cpt Magic 71.31 and 43.60 "
      "(off hand taken off), Cpt Piercewolf 38.26 - all to 0.01 with one constant (6.5), no value fitted; Battle Surge adds "
      "its flat x both factors (Cpt Wolf +12.61, Cpt Piercewolf +19.49). Sources: Crate forum 'Energy Regen Math' (base 6.50, "
      "Spirit/100 flat, percent only on the flat bonuses), wikiwiki.jp/gdcrate Character/Player (Spirit / 384, 6 + Spirit / 100)")
def energy_regen(flat: float, percent: float, spirit: float) -> float:
    """Energy per second at rest from the sheet's `characterManaRegen` / `characterManaRegenModifier` and the Spirit."""
    spirit = max(0.0, spirit)
    attribute = ENERGY_REGEN_BASE + max(0.0, spirit - SPIRIT_FLAT_BASE) * SPIRIT_FLAT_PER
    gear = max(0.0, flat) * max(0.0, 1 + percent / 100) * (1 + spirit * SPIRIT_PCT_PER / 100)
    return attribute + gear


@rule("usable_energy", Confidence.TESTED,
      "the energy a character can spend = its maximum less what auras and toggles keep reserved (`characterManaLimitReserve`, an "
      "absolute number, the sheet's 'Energy Reserved'; never below 0). Videos of 21.11.2026: Cpt Wolf maximum 1,503, reserved "
      "531, 971 at a full bar (1,503 - 531 = 972); Cpt Piercewolf maximum 3,254, reserved 936, first reading 2,317 (3,254 - 936 "
      "= 2,318)")
def usable_energy(maximum: float, reserved: float) -> float:
    """The energy pool of a fight: `maximum` less `reserved`, at least 0."""
    return max(0.0, float(maximum or 0.0) - max(0.0, float(reserved or 0.0)))


@rule("energy_run", Confidence.DERIVED,
      "a pool P with regeneration r and a rotation that spends c per second: while c > r the pool falls by c - r per second and "
      "is empty after P / (c - r) s; from then on the rotation runs at r / c of its rate (every action costs energy in the same "
      "proportion); a rotation with c <= r is never limited. Video of Cpt Wolf, 21.11.2026: 1.62 %/s with energy, 0.78 - 0.96 "
      "%/s with an empty pool (energy 31 - 131 of 1,502 for 60 s)")
def energy_run(pool: float, regen: float, cost_per_s: float) -> tuple[float, float]:
    """(seconds until the pool is empty, rate factor afterwards). (inf, 1.0) when the regeneration pays for the rotation."""
    if cost_per_s <= max(regen, 0.0) + 1e-9:
        return math.inf, 1.0
    return max(pool, 0.0) / (cost_per_s - max(regen, 0.0)), max(regen, 0.0) / cost_per_s


def seconds_with_energy(to_kill: float, rate: float, dry_after: float, factor: float) -> float:
    """Seconds to deal `to_kill` at `rate` per second until `dry_after`, at rate x `factor` after it."""
    if rate <= 0:
        return math.inf
    if dry_after == math.inf or rate * dry_after >= to_kill:
        return to_kill / rate
    return dry_after + (to_kill - rate * dry_after) / (rate * factor) if factor > 0 else math.inf
