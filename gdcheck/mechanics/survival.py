"""Sustain and survival key figures (pure, combat spec V2 11, 13; tests BS-01..BS-06).

All rates in HP per second. TTD uses math.inf as the documented sentinel for a
non-negative net rate - which never means 'immortal' (spikes and CC remain)."""
from __future__ import annotations

import math

from .policy import Confidence, rule


@rule("health_regen", Confidence.TESTED,
      "0.04 per Physique above 50 (wikiwiki.jp/gdcrate: Physique/25 - 2; fandom: +0.04 per point beyond the base 50) "
      "+ flat characterLifeRegen x (1 + characterLifeRegenModifier/100) - the % bonus does not scale the Physique part; "
      "exact against the character sheet of Cpt Wolf 02.10.2026 (491.40/s, KG-11); the Game Guide's 0.05 per Physique "
      "and a base of 1.0 give 615/s there")
def health_regen(flat: float, pct: float, base: float = 0.0, physique: float = 0.0,
                 per_physique: float = 0.04, physique_base: float = 50.0) -> float:
    phys = max(0.0, physique - physique_base) * per_physique if physique else 0.0
    return max(0.0, base + phys + flat * (1 + pct / 100))


@rule("heal_amount", Confidence.ASSUMED,
      "skill/proc heal = (skillLifeBonus + skillLifePercent x max health) x (1 + healing increase); "
      "healing increase not applied to regeneration or life steal")
def heal_amount(flat: float, pct_of_max: float, max_health: float, heal_increase_pct: float = 0.0) -> float:
    return max(0.0, (flat + pct_of_max / 100 * max_health) * (1 + heal_increase_pct / 100))


def total_sustain(life_steal: float, regen: float, skill_heals: float = 0.0, proc_heals: float = 0.0,
                  potions: float = 0.0) -> float:
    return sum(max(0.0, x) for x in (life_steal, regen, skill_heals, proc_heals, potions))


@rule("sustain_ratio", Confidence.DERIVED, "combat spec V2 13.1")
def sustain_ratio(total_sustain_hps: float, incoming_dps: float) -> float:
    return math.inf if incoming_dps <= 0 else max(total_sustain_hps, 0.0) / incoming_dps


@rule("leech_coverage", Confidence.DERIVED, "combat spec V2 13.2")
def leech_coverage(life_steal_hps: float, incoming_dps: float) -> float:
    return math.inf if incoming_dps <= 0 else max(life_steal_hps, 0.0) / incoming_dps


@rule("spike_margin", Confidence.DERIVED, "combat spec V2 13.3")
def spike_margin(current_health: float, spike: float) -> float:
    return math.inf if spike <= 0 else current_health / spike


def net_health_rate(total_sustain_hps: float, incoming_dps: float) -> float:
    return total_sustain_hps - incoming_dps


@rule("ttd_steady", Confidence.DERIVED,
      "combat spec V2 13.4: health / -net rate; inf (sentinel) if the net rate is >= 0 - never "
      "a real time of death and never 'immortal'")
def ttd_steady(current_health: float, net_rate: float) -> float:
    return current_health / -net_rate if net_rate < 0 else math.inf


def band(value: float, edges, labels):
    """HEURISTIC UI band: first label whose lower edge the value reaches (edges descending)."""
    for edge, label in zip(edges, labels):
        if value > edge:
            return label
    return labels[-1]


SUSTAIN_BANDS = ((1.20, 1.00, 0.80), ("reserve", "positive, fragile", "negative trend", "insufficient"))
SPIKE_BANDS = ((1.5, 1.2, 1.0), ("comfortable", "robust", "critical", "lethal"))
