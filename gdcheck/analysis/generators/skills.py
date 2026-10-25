"""Skill point moves (port of the .35 `skillpunkte` module - Task 7 of release .67/.68).

Question: which point brings the least, where would it bring more?

Algorithm (marginal value + swap)
  1. Per skill with points: effective rank = points + gear bonuses (RuleContext.
     effective_level), ultimate rank = length of the level arrays, max rank from
     skillMaxLevel (index schema 5+), else estimated as ultimate - 10 (rule of most skills:
     12/22, 16/26, 10/20).
  2. Values per rank from the level arrays of the skill and its buff record. The difference
     rank r -> r+1 is the marginal value of a point.
  3. Valued on the common scale (value_scale: 1 = ~1 % more main damage or Health), only what
     can be measured there:
        permanent/toggle   in full (character sheet)
        temporary          x uptime duration/cooldown, 0 when neither on the hotbar nor
                           used through the base skill or a binding
        enemy_debuff       resistance reduction on the main damage (0.3 per point, like
                           'rr'), DA reduction like OA
        skill_only/proc    NOT on the scale: the damage of an attack depends on weapon, speed
                           and share of the total damage (no DPS model). Shown is only by how
                           many % the skill's own flat damage grows per point.
     Mastery bar: attributes and Health per rank, up to 50.
  4. Swap (greedy): the point with the smallest loss moves to the skill with the largest
     gain while gain - loss >= 1 point of the scale. Never below 1 point (modifiers,
     bindings, unlocks depend on it); the mastery bar is never lowered.

All values are guide values (G). The rows per skill are always shown.

The rule context is analysis.save_facts.RuleContext (.35: pruefungen.Kontext).

Old -> new: SCHWELLE -> MIN_ADVANTAGE, MAX_ZUEGE -> MAX_MOVES, MASTERY_MAX, GEPFLEGT_AB ->
KEPT_UP_FROM, AUSLAUF_PCT -> FLAT_TAIL_PCT, quellen -> value_records, ultimate -> ultimate_rank,
max_rang -> max_rank, felder_bei -> fields_at, differenz -> difference, gegner_wert ->
enemy_value, eigener_schaden -> own_damage_growth, _wirkung -> _effect, _anteil -> uptime,
_wert_fn -> _value_fn, _skill_liste -> _skills_with_points, bewerten -> assess, tauschen ->
greedy_moves, zusammenfassen -> merge_moves; zusatz_wert / _eigene moved to value_scale
(extra_value / own_value).
"""
from __future__ import annotations

from .. import damage_types as DT
from ..save_facts import (EFFECT_ENEMY_DEBUFF, EFFECT_PERMANENT, EFFECT_SKILL_ONLY,
                          EFFECT_TEMPORARY, EFFECT_TOGGLE, EFFECT_WEAPON_CONDITION, buff_record,
                          leveled_fields, parent_skill)
from .value_scale import RESIST_DAMAGE_TYPES, WEIGHTS, ValueProfile, own_value, skill_name

MIN_ADVANTAGE = 1.0     # minimum advantage of a move (common scale)
MAX_MOVES = 40
MASTERY_MAX = 50
KEPT_UP_FROM = 0.95     # uptime from which a buff counts in full
# attack skill: below this % more own flat damage per point the last point counts as
# "flat tail" (G)
FLAT_TAIL_PCT = 2.0

_DOT_BASE = {"InternalTrauma": "SlowPhysical", "Bleeding": "SlowBleeding",
             "Burn": "SlowFire", "Frostburn": "SlowCold",
             "Electrocute": "SlowLightning", "PoisonDot": "SlowPoison",
             "VitalityDecay": "SlowLife"}


# ---------------------------------------------------------------------------
# values per rank
# ---------------------------------------------------------------------------

def value_records(rn, index) -> list:
    """Records carrying the values of a skill: the skill itself and its buff."""
    rec = index.record(rn) or {}
    out = [rec] if rec else []
    buff = buff_record(rec, index) if rec else None
    if buff and buff is not rec:
        out.append(buff)
    return out


def ultimate_rank(recs):
    lengths = [len(v) for r in recs for k, v in r.items()
               if not k.startswith("_") and isinstance(v, list) and v
               and isinstance(v[0], (int, float))]
    return max(lengths) if lengths else None


def max_rank(rn, recs) -> tuple[int, bool]:
    """(max rank, estimated?) - skillMaxLevel from index schema 5 on."""
    if "_classtraining" in rn:
        return MASTERY_MAX, False
    for r in recs:
        v = r.get("skillMaxLevel")
        if isinstance(v, (int, float)) and v > 0:
            return int(v), False
    u = ultimate_rank(recs)
    if not u:
        return 1, True                      # transmuters etc.: one point
    return (u - 10 if u > 10 else u), True


def fields_at(recs, rank) -> dict:
    out = {}
    if rank < 1:
        return out
    for r in recs:
        for k, v in leveled_fields(r, rank).items():
            out[k] = out.get(k, 0.0) + v
    return out


def difference(new, old) -> dict:
    d = {k: new.get(k, 0.0) - old.get(k, 0.0) for k in set(new) | set(old)}
    return {k: v for k, v in d.items() if abs(v) > 1e-9}


# ---------------------------------------------------------------------------
# value of a field delta per effect kind
# ---------------------------------------------------------------------------

def enemy_value(delta, pr: ValueProfile) -> float:
    """Debuff on enemies: resistance reduction for the main damage and DA reduction. Fields in
    the debuff record are 'defensive...' with a minus (Curse of Frailty: defensiveBleeding -25)
    or offensive...Reduction."""
    mt = pr.main_type
    if not mt:
        return 0.0
    g, p = WEIGHTS, 0.0
    elemental = mt in DT.ELEMENTAL or mt in ("Burn", "Frostburn", "Electrocute")
    for res, types in RESIST_DAMAGE_TYPES.items():
        if mt in types:
            p += g["rr"] * -delta.get(f"defensive{res}", 0.0)
            for kind in ("Absolute", "Percent"):
                p += g["rr"] * delta.get(f"offensive{res}ResistanceReduction{kind}Min", 0.0)
    if elemental:
        p += g["rr"] * -delta.get("defensiveElementalResistance", 0.0)
        for kind in ("Absolute", "Percent"):
            p += g["rr"] * delta.get(f"offensiveElementalResistanceReduction{kind}Min", 0.0)
    for kind in ("Absolute", "Percent"):
        p += g["rr"] * delta.get(f"offensiveTotalResistanceReduction{kind}Min", 0.0)
    oa = (pr.abilities or {}).get("oa") or 0
    da_minus = -delta.get("characterDefensiveAbility", 0.0) \
        + delta.get("offensiveSlowDefensiveAbilityMin", 0.0)
    if oa and da_minus:
        p += g["oa"] * da_minus / oa * 100      # -X DA on the enemy ~ +X OA (G)
    return p


def own_damage_growth(rank_from, recs, rank, pr: ValueProfile):
    """% more own flat damage (main and secondary types) from rank_from -> rank."""
    types = {pr.main_type} | {t for t, (f, _a, _n) in DT.DAMAGE_TYPES.items()
                              if f in (pr.secondary_fields or ())}
    types.discard(None)

    def total(r):
        f = fields_at(recs, r)
        s = 0.0
        for t in types:
            base = _DOT_BASE.get(t, t)
            mn = f.get(f"offensive{base}Min", 0.0)
            mx = f.get(f"offensive{base}Max", 0.0) or mn
            s += (mn + mx) / 2
        return s
    a, b = total(rank_from), total(rank)
    if a <= 0:
        return None
    return (b - a) / a * 100


# ---------------------------------------------------------------------------
# assessment
# ---------------------------------------------------------------------------

def _effect(ctx, rn, recs):
    w = ctx.effect_kind(rn)
    if len(recs) > 1 and any(t in str(recs[1].get("Class", "") or recs[1].get("_type", ""))
                             for t in ("Debuf", "Contageous")):
        w = EFFECT_ENEMY_DEBUFF
    return w


def uptime(ctx, rn, recs, rank):
    """(uptime, reason) of a temporary buff (duration/cooldown), 0 when unused."""
    base = parent_skill(rn, ctx.index) or rn
    bound = {z for z, _ in ctx.bindings.values()}
    if base not in ctx.hotbar and rn not in ctx.hotbar and base not in bound:
        return 0.0, "not on the hotbar"
    brecs = value_records(base, ctx.index)
    duration = cd = None
    for r in reversed(brecs):
        duration = duration or r.get("skillActiveDuration")
        cd = cd or r.get("skillCooldownTime")
    if isinstance(duration, list):
        duration = duration[min(len(duration), max(1, rank)) - 1]
    if isinstance(cd, list):
        cd = cd[min(len(cd), max(1, rank)) - 1]
    if not duration:
        return None, "duration unknown"
    cdr = min(max(ctx.sheet.get("skillCooldownReduction", 0), 0), 100) / 100
    cd_eff = (cd or 0) * (1 - cdr)
    u = min(1.0, duration / cd_eff) if cd_eff > 0 else 1.0
    return (1.0 if u >= KEPT_UP_FROM else u), f"{duration:g} s / {cd_eff:.1f} s"


def _value_fn(ctx, pr, rn, recs, effect):
    """delta -> value (common scale), or None when it cannot be valued."""
    if effect in (EFFECT_PERMANENT, EFFECT_TOGGLE):
        return lambda d, r: own_value(pr, d)
    if effect == EFFECT_TEMPORARY:
        def f(d, r):
            u, _ = uptime(ctx, rn, recs, r)
            return None if u is None else u * own_value(pr, d)
        return f
    if effect == EFFECT_ENEMY_DEBUFF:
        return lambda d, r: enemy_value(d, pr)
    if effect == EFFECT_WEAPON_CONDITION:
        return lambda d, r: 0.0
    return None


def _skills_with_points(ctx):
    out = []
    for s in ctx.skills:
        rn, p = s["skill_name"], s.get("level", 0)
        if p < 1 or "/playerclass" not in rn or "/devotion/" in rn:
            continue
        out.append((rn, p))
    return out


def assess(ctx, pr: ValueProfile) -> dict:
    """Marginal value per skill and move proposals. ctx: RuleContext."""
    index = ctx.index
    entries = []
    for rn, p in _skills_with_points(ctx):
        recs = value_records(rn, index)
        if not recs:
            continue
        mastery = "_classtraining" in rn
        u = ultimate_rank(recs)
        mx, estimated = max_rank(rn, recs)
        rank = p if mastery else ctx.effective_level(rn)
        bonus = rank - p
        effect = EFFECT_PERMANENT if mastery else _effect(ctx, rn, recs)
        fn = _value_fn(ctx, pr, rn, recs, effect)
        no_values = not fields_at(recs, max(rank, 1)) or (u is None and not mastery)
        if no_values:
            fn = None
        e = {"record": rn, "name": skill_name(index, rn), "points": p, "rank": rank, "bonus": bonus,
             "max": mx, "max_estimated": estimated, "ultimate": u,
             "effect": effect, "mastery": mastery, "rated": fn is not None,
             "last": None, "next": None, "own_last": None,
             "own_next": None, "note": ""}
        can_grow = p < mx and (u is None or rank + 1 <= u)
        if fn is not None:
            def marginal(r_from, r_to, _fn=fn, _recs=recs):
                d = difference(fields_at(_recs, r_to), fields_at(_recs, r_from))
                return _fn(d, r_to) if d else 0.0
            e["_marginal"] = marginal
            if p >= 2 and not mastery:
                e["last"] = marginal(rank - 1, rank)
            if can_grow:
                e["next"] = marginal(rank, rank + 1)
            if effect == EFFECT_TEMPORARY:
                a, reason = uptime(ctx, rn, recs, rank)
                e["note"] = ("uptime " + (f"{a * 100:.0f}%" if a is not None else "?")
                             + f" ({reason})")
        else:
            if p >= 2 and rank >= 2:
                e["own_last"] = own_damage_growth(rank - 1, recs, rank, pr)
            if can_grow:
                e["own_next"] = own_damage_growth(rank, recs, rank + 1, pr)
            if no_values and effect != EFFECT_SKILL_ONLY:
                e["note"] = ("not rated: no rank values in the index" if u is None
                             and fields_at(recs, max(rank, 1)) == {} else
                             "not rated: single-rank skill")
            else:
                e["note"] = ("not rated: attack/spell (no DPS model)"
                             if effect == EFFECT_SKILL_ONLY else "not rated: proc")
        if u and rank > u:
            e["note"] = (e["note"] + "; " if e["note"] else "") + f"{rank - u} ranks above cap"
        if not can_grow and p >= mx:
            e["full"] = True
        entries.append(e)

    moves = greedy_moves(entries)
    for e in entries:
        e.pop("_marginal", None)
    gain = sum(z["gain"] - z["loss"] for z in moves)
    flat_tail = [e for e in entries if e["own_last"] is not None
                 and e["own_last"] < FLAT_TAIL_PCT and e["points"] >= 2]
    return {"entries": entries, "moves": moves, "gain": gain,
            "merged": merge_moves(moves), "flat_tail": flat_tail,
            "max_estimated": any(e["max_estimated"] for e in entries if not e["mastery"]),
            "pet": bool(pr.pet)}


def greedy_moves(entries) -> list:
    """Greedy: smallest loss -> largest gain while it pays."""
    state = {e["record"]: dict(e) for e in entries if e.get("_marginal")}
    moves = []
    for _ in range(MAX_MOVES):
        sources = [e for e in state.values() if e["last"] is not None
                   and e["points"] >= 2 and not e["mastery"]]
        sinks = [e for e in state.values() if e["next"] is not None and e["next"] > 0]
        if not sources or not sinks:
            break
        q = min(sources, key=lambda e: e["last"])
        s = max((e for e in sinks if e is not q), key=lambda e: e["next"], default=None)
        if s is None or s["next"] - q["last"] < MIN_ADVANTAGE:
            break
        moves.append({"from": q["name"], "to": s["name"], "loss": q["last"], "gain": s["next"]})
        # advance the state
        q["points"] -= 1
        q["rank"] -= 1
        q["last"] = q["_marginal"](q["rank"] - 1, q["rank"]) if q["points"] >= 2 else None
        q["next"] = q["_marginal"](q["rank"], q["rank"] + 1)
        s["points"] += 1
        s["rank"] += 1
        s["last"] = s["_marginal"](s["rank"] - 1, s["rank"])
        grow = s["points"] < s["max"] and (s["ultimate"] is None or s["rank"] + 1 <= s["ultimate"])
        s["next"] = s["_marginal"](s["rank"], s["rank"] + 1) if grow else None
    return moves


def merge_moves(moves) -> list:
    """Single moves merged per pair (from, to)."""
    pairs = {}
    for z in moves:
        k = (z["from"], z["to"])
        e = pairs.setdefault(k, {"from": z["from"], "to": z["to"], "points": 0,
                                 "loss": 0.0, "gain": 0.0})
        e["points"] += 1
        e["loss"] += z["loss"]
        e["gain"] += z["gain"]
    return sorted(pairs.values(), key=lambda e: -(e["gain"] - e["loss"]))
