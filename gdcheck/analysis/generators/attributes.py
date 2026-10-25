"""Spread of the attribute points (port of the .35 `attribute` module - Task 7 of release
.67/.68).

Every attribute point gives +8 to Physique, Cunning or Spirit (playerlevels.dbr,
strengthIncrement etc.). The effect depends on the character:

  Physique  +2.5 Health and +0.5 DA per Physique             [DB combatformulas]
  Cunning   +1 Health, +0.5 OA, +1 % Physical/Pierce per 2.45,
            +1 % Bleeding/Internal Trauma per 2.15            [DB]
  Spirit    +1.5 Health, +2 Energy, +1 % magic direct damage per 2.15,
            +1 % magic DoT per 2.00                           [DB]
  Everything is multiplied by the % bonuses on the attribute (e.g. +8 % Physique) and then
  by %Health/%DA/%OA. Points and flat gear bonuses behave the same - so a point is +8 flat
  on the attribute and the abilities function computes the rest.

Algorithm
  1. Points per attribute from the savefile: (base value - 50) / 8.
  2. Value of a spread (guide value, same scale as value_scale):
        100 * ln(new/old) per quantity, weighted with value_scale.WEIGHTS (main damage 1.0,
        secondary 0.25, Health 1.0 or 1.5 below the Health target, DA 0.8, OA 0.5). ln instead
        of linear so wide shifts are not overrated; for small steps 1 point = 1 % more main
        damage or Health as everywhere else. Health bends at the target.
  3. EVERY spread with the same number of points is computed (5,671 for 105 points) - no
     greedy step, no local optimum.
  4. Constraints: minimum final values per attribute (e.g. item requirements). The index does
     NOT know requirements - without them 0 applies and the report says to check them.

Limits (named in the report): Energy and regeneration do not count; OA is weighted flat (the
real value depends on the enemies' DA); a reset needs a Tonic of Reshaping.

Old -> new: ATTRIBUTE -> ATTRIBUTES, FELD -> FIELD, NAME -> NAME, KURZ -> SHORT, GRUND ->
BASE, JE_PUNKT -> PER_POINT, LOHNT_AB -> MIN_GAIN (now the `min_gain` parameter),
HEALTH_VERLUST_MAX -> MAX_HEALTH_LOSS, punkte_jetzt -> points_now, kennwerte -> key_values,
_health_nutzen -> health_utility, wert -> spread_value, _verschoben -> _shifted, bewerten ->
assess, verteilung_text -> spread_text. Result keys: see generators/__init__.py.
"""
from __future__ import annotations

import math

from .. import damage_types as DT
from .value_scale import WEIGHTS, ValueProfile

ATTRIBUTES = ("physique", "cunning", "spirit")
FIELD = {"physique": "characterStrength", "cunning": "characterDexterity",
         "spirit": "characterIntelligence"}
NAME = {"physique": "Physique", "cunning": "Cunning", "spirit": "Spirit"}
SHORT = {"physique": "P", "cunning": "C", "spirit": "S"}

BASE = 50           # start value per attribute (malepc01.dbr)
PER_POINT = 8       # playerlevels.dbr strengthIncrement
# From this gain on a new spread is worth a hint (guide value).
MIN_GAIN = 1.0
# Guard (review 30.09.2026, Cpt Spin 83/0/18 -> 10/91/0 cost ~3,000 Health): a spread may not
# push Health below the stage target and never cost more than this share (G).
MAX_HEALTH_LOSS = 0.03


def points_now(base: dict, start: float = BASE, per: float = PER_POINT) -> dict:
    """Points spent per attribute from the base values of the savefile."""
    return {a: max(0, int(round(((base.get(a) or start) - start) / per))) for a in ATTRIBUTES}


def _secondary_types(pr: ValueProfile) -> list:
    return [t for t, (fld, _a, _n) in DT.DAMAGE_TYPES.items() if fld in (pr.secondary_fields or ())]


def key_values(sheet: dict, abilities_fn, pr: ValueProfile) -> dict:
    """Health, DA, OA, attributes and damage (%) of a character sheet."""
    ab = abilities_fn(sheet)
    pct = {e["typ"]: e["summe"] for e in DT.damage_percentages(sheet, ab.get("cunning"), ab.get("spirit"))}
    return {"health": ab.get("health") or 0.0, "da": ab.get("da") or 0.0,
            "oa": ab.get("oa") or 0.0, "energy": ab.get("energy") or 0.0,
            "physique": ab.get("physique") or 0.0, "cunning": ab.get("cunning") or 0.0,
            "spirit": ab.get("spirit") or 0.0,
            "main": pct.get(pr.main_type) if pr.main_type else None,
            "secondary": {t: pct.get(t, 0.0) for t in _secondary_types(pr)}}


def _ln(new, old):
    if not new or not old or new <= 0 or old <= 0:
        return 0.0
    return 100.0 * math.log(new / old)


def health_utility(h, target):
    """Continuous utility curve: 1.5 per % below the target, 1.0 per % above."""
    w_below, w_above = WEIGHTS["health_below_target"], WEIGHTS["health"]
    if not target:
        return w_above * 100.0 * math.log(max(h, 1.0))
    return (w_below * 100.0 * math.log(max(min(h, target), 1.0))
            + w_above * 100.0 * math.log(max(h, target) / target))


def spread_value(new: dict, old: dict, pr: ValueProfile) -> float:
    """Value (guide value) of 'new' against 'old' in points of the common scale."""
    g = WEIGHTS
    p = 0.0
    if new.get("main") is not None and old.get("main") is not None:
        p += g["damage"] * _ln(100 + new["main"], 100 + old["main"])
    for t, v in (new.get("secondary") or {}).items():
        p += g["secondary"] * _ln(100 + v, 100 + (old.get("secondary") or {}).get(t, 0.0))
    target = pr.health_target or 0
    p += health_utility(new["health"], target) - health_utility(old["health"], target)
    p += g["da"] * _ln(new["da"], old["da"])
    p += g["oa"] * _ln(new["oa"], old["oa"])
    return p


def _shifted(sheet, now, spread, per):
    new = dict(sheet)
    for a in ATTRIBUTES:
        d = (spread[a] - now[a]) * per
        if d:
            new[FIELD[a]] = new.get(FIELD[a], 0.0) + d
    return new


def assess(facts, sheet: dict, abilities_fn, pr: ValueProfile, minimum: dict | None = None,
           per: float = PER_POINT, start: float = BASE, min_gain: float = MIN_GAIN) -> dict | None:
    """Best spread of the attribute points the character has.

    facts         SaveFacts (attribute_basis)
    sheet         character sheet
    abilities_fn  sheet -> abilities (abilities.abilities with facts and index)
    pr            ValueProfile (main damage, secondary types, Health target)
    minimum       {attribute: final value} - e.g. item requirements"""
    base = facts.get("attribute_basis") or {}
    if not all(base.get(a) for a in ATTRIBUTES):
        return None
    now = points_now(base, start, per)
    n = sum(now.values())
    minimum = {a: float(v) for a, v in (minimum or {}).items() if v}
    ref = key_values(sheet, abilities_fn, pr)

    h_target = pr.health_goal or pr.health_target or 0
    h_min = max(min(ref["health"], h_target), ref["health"] * (1 - MAX_HEALTH_LOSS))

    def ok(k):
        return (all(k[a] + 1e-6 >= minimum.get(a, 0) for a in ATTRIBUTES)
                and k["health"] + 1e-6 >= h_min)

    best, best_value, best_k = dict(now), 0.0, ref
    checked = rejected = 0
    for p in range(n + 1):
        for c in range(n - p + 1):
            v = {"physique": p, "cunning": c, "spirit": n - p - c}
            k = key_values(_shifted(sheet, now, v, per), abilities_fn, pr)
            checked += 1
            if not ok(k):
                rejected += 1
                continue
            w = spread_value(k, ref, pr)
            if w > best_value + 1e-9:
                best, best_value, best_k = v, w, k

    # value of a single point from today, per attribute
    per_point = []
    for a in ATTRIBUTES:
        v = dict(now)
        v[a] += 1
        k = key_values(_shifted(sheet, now, v, per), abilities_fn, pr)
        per_point.append({"attribute": a, "name": NAME[a],
                          "health": k["health"] - ref["health"], "da": k["da"] - ref["da"],
                          "oa": k["oa"] - ref["oa"], "energy": k["energy"] - ref["energy"],
                          "main": (k["main"] - ref["main"]) if ref["main"] is not None else None,
                          "value": spread_value(k, ref, pr)})

    # below the threshold the difference is noise of the guide weights: the current spread stays
    # the target, without a requirement warning
    worth_it = best_value >= min_gain and best != now
    near = None
    if not worth_it:
        if best != now:
            near = {"spread": best, "gain": best_value}
        best, best_k = dict(now), ref
    moved_from = [a for a in ATTRIBUTES if best[a] < now[a]]
    notes = []
    if moved_from and not all(minimum.get(a) for a in moved_from):
        notes.append("requirements")
    return {"current": now, "total": n, "best": best, "gain": best_value,
            "values_current": ref, "values_best": best_k, "per_point": per_point,
            "minimum": minimum, "checked": checked, "rejected": rejected,
            "main_name": (pr.main or {}).get("name"),
            "worth_it": worth_it, "notes": notes, "moved_from": moved_from, "near": near,
            "health_min": h_min}


def spread_text(v: dict) -> str:
    return "/".join(str(v[a]) for a in ATTRIBUTES)
