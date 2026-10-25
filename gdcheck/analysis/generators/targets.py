"""Endgame target values the candidate generators value against (port of the parts of the .35
`ziele` module the generators read - Task 7 of release .67/.68).

Derived on 30.09.2026 from the requirements of the Celestial bosses (Ultimate): Avatar of
Mogdrogen, Ravager (Flesh/Souls/Minds), Callagadra, Crate of Entertainment, Lokarr, Clone of
John Bourbon. Almost all are guide values (G) - player reports, mostly before 1.3; 1.3.0.0
raised the Health of the superbosses by about 15 % [Crate patch notes].

The target rows, stages and boss verdicts of `ziele` are not ported here: the engine report
(analysis/targets.py, analysis/stage.py, analysis/bosses.py) replaced them.

Old -> new: SOLL -> TARGETS (keys physical_kampf -> physical_combat,
ruestung_gewichtet -> armor_weighted, oa_kampf -> oa_combat, pth_ziel -> pth, rest_boss_res ->
boss_res_left), LEECH_FELD -> LEECH_FIELD, LEECH_ABZUG_STD ->
LEECH_PENALTY_DEFAULT, BOSS_TYPEN -> BOSS_DAMAGE_TYPES, leech_abzug -> leech_penalty.
"""
from __future__ import annotations

from types import MappingProxyType

TARGETS = MappingProxyType({
    "physical_combat": 30.0,    # Physical in combat (user 30.09.2026: 40 -> 30, 40 = special target)
    "da": 3000.0,              # in combat (buffs >= 95 % active) - Crate forum: "at least 3k",
                               # Steam: 2800 minimum; Crate itself: 2800 + 450
    "health": 20000.0,         # melee (user 30.09.2026: 22k -> 20k; 22k not documented)
    "armor_weighted": 3000.0,   # armor weighted by hit zone
    "freeze": 50.0, "stun": 50.0,   # Crate's crates freeze, Crate stuns (leap)
    "oa_combat": 3000.0,       # crits against high boss DA (G) - OR PtH >= 100 against
    "pth": 100.0,              # boss DA 3000 after your DA reduction (user 30.09.2026)
    "boss_res_left": 10.0,    # bonus value only (no target since 30.09.2026)
    "leech": 50.0,             # Life Leech Resistance after the penalty (G): leeching bosses
                               # (Alkamir, SR mutator "Leeching") heal less
    "max_res": 83.0,           # bonus value only (no target since 30.09.2026): each point above
                               # 80 is about 5 % less damage of that type
})
LEECH_FIELD = "defensiveSlowLifeLeach"      # "% Life Leech Resistance" (tag DefenseLifeLeach)
LEECH_PENALTY_DEFAULT = 25.0                # Ultimate (balancingadjustment...players01.dbr)

# damage types of the superbosses -> bosses
BOSS_DAMAGE_TYPES = MappingProxyType({
    "Fire": "Crate, Lokarr", "Lightning": "Mogdrogen", "Chaos": "Callagadra",
    "Pierce": "Callagadra", "Aether": "Ravager of Souls", "Vitality": "Ravager of Souls",
    "Bleeding": "Ravager of Flesh"})


def leech_penalty(index, difficulty: int) -> float:
    """Life Leech Resistance penalty of a difficulty (0 Normal, 1 Elite, 2 Ultimate) from the
    game database; fallback 25 on Ultimate."""
    tab = ((getattr(index, "tabellen", None) or {}).get(
        "records/game/balancingadjustment_mp+difficulty_players01.dbr")) or {}
    arr = tab.get(LEECH_FIELD)
    if isinstance(arr, list) and len(arr) >= 12:
        return -float(arr[difficulty * 4])
    return LEECH_PENALTY_DEFAULT if difficulty == 2 else 0.0
