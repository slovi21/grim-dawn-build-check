"""Devotion plans (port of the .35 `devotionen` module - Task 7 of release .67/.68).

Question: which constellations bring the least per point, and is there a swap - or a full
reset - that brings more without breaking affinity, resistances or bound Celestial Powers?

Data
  - Stars: records/skills/devotion/tier<N>_<NN><a-h>[_skill].dbr. All stars of one prefix
    belong to one constellation (display name = name of the star records); exception
    Crossroads (tier1_15): every star is a constellation of its own with one point.
  - Affinity (need and bonus) is NOT in the star records but in the constellation records
    under records/ui/skills/devotion/ (index schema 5+, "sternbilder"). Without them the
    generator runs in "value only" mode: ranking per point, only swaps that cannot touch
    affinity.

Rules [Crate Game Guide, Devotion]
  - Affinity comes only from a COMPLETE constellation; it is not consumed. A complete
    constellation carries its own need.
  - Points in a constellation whose affinity another one needs cannot be removed.

Algorithm
  1. Value per constellation = value of all taken stars together (value_scale, same scale as
     rerolls and skills), divided per point. Celestial Powers are not valued (procs) -
     constellations with a bound power are locked. Weapon condition not met -> value 0.
  2. Candidates = every constellation not taken, complete, weapon condition met.
  3. Swap: 1-2 out, 1-2 in, points <= free + out. Valid when (a) every remaining
     constellation covers its need from the complete ones (incl. itself), (b) every new one
     covers its need WITHOUT the removed ones and without itself (order in game: remove
     first, then place), (c) no resistance falls below cap or need (swap check). Additive
     preselection, the best 60 recalculated exactly.
  4. Full plan (user 30.09.2026): reset everything and find the best set for the build, with
     build order and the values lost and gained. Hill climbing from the current set and from
     "locked only"; per step 0-2 out, 0-3 in; until no step brings >= PLAN_STEP. Then a
     build order with stepping stones (Crossroads and small constellations) removed again at
     the end; never more than the devotion points at once. Every step is valid (.69): a stone is
     taken back only when every remaining constellation AND the one placed next stay covered;
     an order that does not replay under the game rules is never delivered (none is better).

De-globalised: the build-order search was a recursive depth-first search that needed about
1,000 frames for 55-point plans - the .35 adapter raised sys.setrecursionlimit around the run
(.45). It is iterative now (explicit stack, same visiting order, same node budget), so the
interpreter limit is never touched. The swap check is passed in (Resistances.swap_check bound
to the combat sheet), not read from module state.

Limits: "Bonus to All Pets" is not on the stars in the index - for pet builds the values are
too low (note in the report).

Old -> new: STERN_RE -> STAR_RE, AFFINITAETEN -> AFFINITIES, LOHNT_AB -> MIN_ADVANTAGE,
VORAUSWAHL -> PRESELECTION, sternbilder -> constellations, _affinitaet_name -> affinity_name,
affinitaeten -> affinities, _verweise -> _references, summe_affinitaet -> affinity_sum,
gedeckt -> covered, zustand_gueltig -> state_valid, _felder -> star_fields, _wert -> _value,
_verlust -> _loss, bewerten -> assess, _traegt -> _carries, tausch_suche -> swap_search,
PLAN_BUDGET, SCHUTZ_NAMEN -> GUARDED_NAMES, _schutz_soll -> guard_targets, schutz_verletzt ->
guards_broken, schutz_zeilen -> guard_rows, PLAN_SCHRITT -> PLAN_STEP, PLAN_RUNDEN ->
PLAN_ROUNDS, reihenfolge -> build_order, reihenfolge_suche -> build_order_search (iterative),
_reihenfolge_greedy -> _build_order_greedy, plan -> plan, NICHT_BEWERTET -> NOT_VALUED,
reihenfolge_pruefen -> check_build_order. Result keys: see generators/__init__.py.
"""
from __future__ import annotations

import itertools
import re
import weakref
from types import MappingProxyType

from ..save_facts import DEVOTION_WEAPON, leveled_fields, weapon_categories
from .targets import BOSS_DAMAGE_TYPES, LEECH_FIELD, TARGETS
from .value_scale import ValueProfile, own_value, skill_name, stat_deltas

STAR_RE = re.compile(r"^records/skills/devotion/(tier\d+_\d+)([a-z])(_skill)?\.dbr$")
CROSSROADS = "tier1_15"
AFFINITIES = ("Ascendant", "Chaos", "Eldritch", "Order", "Primordial")
MIN_ADVANTAGE = 2.0     # minimum advantage of a swap or plan B (common scale)
PRESELECTION = 60
PLAN_BUDGET = 55
PLAN_STEP = 0.3
PLAN_ROUNDS = 40
SEARCH_NODES = 30000    # node budget of the build-order search (deterministic, no time limit)


# ---------------------------------------------------------------------------
# constellations from the index
# ---------------------------------------------------------------------------

# One scan of the ~32,000 index records per index (the build check and the proposals both ask).
# Keyed weakly by the index, so an index is never kept alive; every caller gets its own COPY, because
# the callers sort, extend and rename (design/catalog.py) - a shared dict would leak those edits.
_CONSTELLATIONS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_AFFINITIES: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _copy_constellations(sb: dict) -> dict:
    return {k: {**v, "stars": list(v["stars"])} for k, v in sb.items()}


def _copy_affinities(aff: dict | None) -> dict | None:
    if aff is None:
        return None
    return {k: {**v, "needs": dict(v["needs"]), "gives": dict(v["gives"])} for k, v in aff.items()}


def _memo(cache, index, anchors: tuple, key, build):
    """cache[index] = (anchors, key, value). The value is valid while the anchors (the data objects it was
    built from: records, sternbild_roh) are the very same objects and key (the star sets) is equal - a
    replaced dict is never answered from a stale entry. An index that cannot be weakly referenced or
    hashed is simply not cached."""
    try:
        hit = cache.get(index)
    except TypeError:
        return build()
    if hit is not None and hit[1] == key and len(hit[0]) == len(anchors) \
            and all(x is y for x, y in zip(hit[0], anchors)):
        return hit[2]
    value = build()
    try:
        cache[index] = (anchors, key, value)
    except TypeError:
        pass
    return value


def constellations(index) -> dict:
    """{key: {"name", "stars": [path], "power": path|None}}. Key = prefix (tier2_11), for
    Crossroads the star itself. Memoised per index; the result is the caller's own copy."""
    anchors = (getattr(index, "records", None),)
    return _copy_constellations(_memo(_CONSTELLATIONS, index, anchors, None, lambda: _constellations(index)))


def _constellations(index) -> dict:
    out = {}
    for path, e in (getattr(index, "records", None) or {}).items():
        m = STAR_RE.match(path)
        if not m:
            continue
        prefix, _letter, power = m.groups()
        key = path[len("records/skills/devotion/"):-4] if prefix == CROSSROADS else prefix
        sb = out.setdefault(key, {"name": "", "stars": [], "power": None})
        sb["stars"].append(path)
        if power:
            sb["power"] = path
        elif not sb["name"]:
            sb["name"] = e.get("n") or ""
    for s, sb in out.items():
        sb["stars"].sort()
        if not sb["name"]:
            sb["name"] = (index.display(sb["stars"][0]) if hasattr(index, "display") else "") or s
        if s.startswith(CROSSROADS):
            # five one-star constellations with the same name - make them distinguishable
            sb["name"] = f"{sb['name'] or 'Crossroads'} {s[-1].upper()}"
    return out


def affinity_name(value, tags=None) -> str | None:
    t = str(value or "")
    if tags and t in tags:
        t = tags[t]
    tl = t.lower()
    for a in AFFINITIES:
        if a.lower() in tl:
            return a
    short = {"asc": "Ascendant", "cha": "Chaos", "eld": "Eldritch", "ord": "Order",
             "pri": "Primordial"}
    for k, a in short.items():
        if k in tl:
            return a
    return None


_AFF_FIELD = re.compile(r"^affinity(required|given)(name)?(\d*)$", re.I)


def affinities(index, sb: dict) -> dict | None:
    """{key: {"needs": {aff: n}, "gives": {aff: n}, "record"}} from the constellation records
    (index schema 5+). None when missing. Memoised per index and set of stars; the result is the
    caller's own copy."""
    anchors = (getattr(index, "sternbild_roh", None), getattr(index, "tags", None))
    stars = tuple((k, tuple(v["stars"])) for k, v in sorted(sb.items()))
    return _copy_affinities(_memo(_AFFINITIES, index, anchors, stars, lambda: _affinities(index, sb)))


def _affinities(index, sb: dict) -> dict | None:
    """The uncached build of affinities().

    Read generically, because the field names could not be checked against the database:
    every field affinityRequired<n>/affinityGiven<n> with a matching ...Name<n>; stars through
    every .dbr reference in the record or in a referenced record (buttons) of the folder."""
    raw = getattr(index, "sternbild_roh", None) or {}
    if not raw:
        return None
    tags = getattr(index, "tags", None) or {}
    star_to = {s: k for k, v in sb.items() for s in v["stars"]}
    out = {}
    for path, rec in raw.items():
        values, names = {}, {}
        for k, v in rec.items():
            m = _AFF_FIELD.match(k)
            if not m:
                continue
            kind, is_name, nr = m.group(1).lower(), bool(m.group(2)), m.group(3) or "0"
            if isinstance(v, list):          # arrays instead of numbered fields
                for i, x in enumerate(v):
                    (names if is_name else values)[(kind, f"{nr}.{i}")] = x
            else:
                (names if is_name else values)[(kind, nr)] = v
        if not values:
            continue
        needs, gives = {}, {}
        for (kind, nr), n in values.items():
            a = affinity_name(names.get((kind, nr)), tags)
            if a and isinstance(n, (int, float)) and n > 0:
                target = needs if kind == "required" else gives
                target[a] = target.get(a, 0) + int(n)
        stars = set()
        for v in _references(rec):
            if v in star_to:
                stars.add(v)
            elif v in raw:
                stars |= {x for x in _references(raw[v]) if x in star_to}
        keys = {star_to[s] for s in stars}
        if len(keys) != 1:
            continue
        out[keys.pop()] = {"needs": needs, "gives": gives, "record": path}
    return out or None


def _references(rec):
    for v in rec.values():
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, str) and x.lower().endswith(".dbr"):
                yield x.replace("\\", "/").lower()


# ---------------------------------------------------------------------------
# affinity checks
# ---------------------------------------------------------------------------

def affinity_sum(complete: set, aff: dict) -> dict:
    s = {}
    for k in complete:
        for a, n in (aff.get(k) or {}).get("gives", {}).items():
            s[a] = s.get(a, 0) + n
    return s


def covered(needs: dict, stock: dict) -> bool:
    return all(stock.get(a, 0) >= n for a, n in (needs or {}).items())


def state_valid(taken: set, complete: set, aff: dict) -> bool:
    stock = affinity_sum(complete, aff)
    return all(covered((aff.get(k) or {}).get("needs"), stock) for k in taken)


# ---------------------------------------------------------------------------
# assessment
# ---------------------------------------------------------------------------

def star_fields(index, stars) -> dict:
    out = {}
    for s in stars:
        if s.endswith("_skill.dbr"):
            continue
        for k, v in leveled_fields(index.record(s) or {}, 1).items():
            out[k] = out.get(k, 0.0) + v
    return out


def _value(fields, pr) -> float:
    """Same valuation as skill points: affix value + extra fields (multiplicative damage,
    dodge/deflect, resistance reduction)."""
    return own_value(pr, fields)


def _loss(fields, pr) -> float:
    if not fields:
        return 0.0
    return -_value({k: -v for k, v in fields.items()}, pr)


def assess(ctx, facts, pr: ValueProfile, swap_check=None, plans: bool = True,
           swap_limits: tuple[int, int] = (2, 2)) -> dict:
    """Taken constellations, candidates, swaps and plans A/B. ctx: RuleContext; swap_check:
    delta -> (rows, problems) against the combat sheet.

    Light mode (the build check, every analysis): plans=False leaves plan / plan_b None - the plan search
    is nearly all of the run time (about 0.2-1.4 s) and the build check does not need it - and
    swap_limits=(max out, max in) bounds the swap search (default 2 for 2)."""
    index = ctx.index
    sb = constellations(index)
    if not sb:
        return {"taken": [], "candidates": [], "swaps": [], "mode": "none"}
    aff = affinities(index, sb)
    main, off = ctx.weapon_types()
    categories = weapon_categories(main, off)
    star_to = {s: k for k, v in sb.items() for s in v["stars"]}
    taken_stars = [s["skill_name"] for s in ctx.skills
                   if s.get("level", 0) > 0 and s["skill_name"] in star_to]
    per_sb = {}
    for s in taken_stars:
        per_sb.setdefault(star_to[s], []).append(s)
    free = ((facts.get("fortschritt") or {}).get("devotionspunkte_frei") or 0)
    # points the character has (shrines) - not a flat 55 (review 30.09.2026: Cpt Boom with 8
    # points got a 55-point plan)
    budget = (facts.get("fortschritt") or {}).get("devotionspunkte_gesamt") or PLAN_BUDGET

    def weapon_ok(name):
        if name not in DEVOTION_WEAPON:
            return True
        return DEVOTION_WEAPON[name][0] in categories

    taken = []
    for k, stars in per_sb.items():
        info = sb[k]
        f = star_fields(index, stars)
        ok = weapon_ok(info["name"])
        v = _loss(f, pr) if ok else 0.0
        power = info["power"] if info["power"] in stars else None
        bound = bool(power and power in ctx.bindings)
        points = len(stars)
        taken.append({
            "key": k, "name": info["name"], "points": points,
            "total": len(info["stars"]), "complete": points == len(info["stars"]),
            "value": v, "per_point": v / points if points else 0.0, "fields": f,
            "power": index.display(power) if power else None, "bound": bound,
            "bound_to": skill_name(index, ctx.bindings[power][0]) if bound else None,
            "weapon_ok": ok, "no_values": not f and not power,
            "gives": (aff or {}).get(k, {}).get("gives", {}),
            "needs": (aff or {}).get(k, {}).get("needs", {})})
    taken.sort(key=lambda e: (e["bound"], e["per_point"], e["name"]))

    candidates = []
    for k, info in sb.items():
        if k in per_sb or not weapon_ok(info["name"]):
            continue
        f = star_fields(index, info["stars"])
        if not f:
            continue
        v = _value(f, pr)
        candidates.append({"key": k, "name": info["name"],
                           "points": len(info["stars"]), "value": v,
                           "per_point": v / len(info["stars"]), "fields": f,
                           "power": index.display(info["power"]) if info["power"] else None,
                           "needs": (aff or {}).get(k, {}).get("needs", {}),
                           "gives": (aff or {}).get(k, {}).get("gives", {})})
    candidates.sort(key=lambda e: (-e["per_point"], e["name"]))

    max_out, max_in = swap_limits
    if aff:
        swaps = swap_search(taken, candidates, aff, free, pr, swap_check, max_out=max_out, max_in=max_in)
    else:
        # Without affinity data only swaps that never touch affinity are safe: out only
        # INCOMPLETE constellations (never give affinity) without a bound power, in only
        # Crossroads (needs no affinity). [Crate Game Guide, Devotion]
        swaps = swap_search(
            [e for e in taken if not e["complete"]],
            [e for e in candidates if e["key"].startswith(CROSSROADS)],
            {}, free, pr, swap_check, safe=True, max_out=max_out, max_in=max_in + 1)
    stock = affinity_sum({e["key"] for e in taken if e["complete"]}, aff or {})
    for e in taken:
        e["carries"] = _carries(e, taken, aff) if aff else None
    plan_a = plan(index, sb, aff, taken, weapon_ok, pr, swap_check, budget=budget) if aff and plans else None
    # variant B: powers of the tier-1 constellations freed (mostly small procs); shown only when
    # it brings more than variant A
    tier1 = {e["key"] for e in taken if e["bound"] and e["key"].startswith("tier1_")}
    plan_b = plan(index, sb, aff, taken, weapon_ok, pr, swap_check, freed=tier1,
                  budget=budget) if aff and tier1 and plans else None
    if plan_b and plan_a and plan_b["value"] < plan_a["value"] + MIN_ADVANTAGE:
        plan_b = None
    return {"taken": taken, "candidates": candidates, "swaps": swaps,
            "plan": plan_a, "plan_b": plan_b,
            "mode": "full" if aff else "value only", "free": free,
            "affinity": stock, "pet": bool(pr.pet),
            "points": sum(e["points"] for e in taken), "budget": budget}


def _carries(e, taken, aff) -> list:
    """Names of the constellations that no longer cover their need without e."""
    if not e["complete"] or not e["gives"]:
        return []
    rest = {x["key"] for x in taken if x is not e}
    complete = {x["key"] for x in taken if x["complete"] and x is not e}
    stock = affinity_sum(complete, aff)
    return [x["name"] for x in taken if x["key"] in rest and not covered(x["needs"], stock)]


def _problems(swap_check, delta) -> list:
    return list(swap_check(delta)[1]) if swap_check else []


def swap_search(taken, candidates, aff, free, pr, swap_check=None, safe=False,
                max_out: int = 2, max_in: int = 2) -> list:
    """1..max_out out, 1..max_in in (default 2 for 2). Additive preselection, then recalculated exactly.

    safe=True: the pools are prefiltered so that affinity plays no role (value-only mode);
    then also 0 out (free points); the caller allows max_in = 3 there (Crossroads)."""
    out_pool = [e for e in taken if not e["bound"]]
    in_pool = candidates[:40]
    taken_k = {e["key"] for e in taken}
    complete_k = {e["key"] for e in taken if e["complete"]}
    proposals = []
    for n_out in range(0 if safe else 1, max_out + 1):
        for out in itertools.combinations(out_pool, n_out):
            ok_ = {e["key"] for e in out}
            rest, rest_complete = taken_k - ok_, complete_k - ok_
            if not safe and not state_valid(rest, rest_complete, aff):
                continue
            stock = affinity_sum(rest_complete, aff)
            budget = free + sum(e["points"] for e in out)
            loss = sum(e["value"] for e in out)
            for n_in in range(1, max_in + 1):
                for new in itertools.combinations(in_pool, n_in):
                    if sum(e["points"] for e in new) > budget:
                        continue
                    # remove first, then place: every new constellation must be covered by the
                    # rest (plus the new ones already placed)
                    ok, v = True, dict(stock)
                    for e in [] if safe else sorted(new, key=lambda x: sum(x["needs"].values())):
                        if not covered(e["needs"], v):
                            ok = False
                            break
                        for a, n in e["gives"].items():
                            v[a] = v.get(a, 0) + n
                    if not ok:
                        continue
                    gain = sum(e["value"] for e in new)
                    proposals.append((gain - loss, out, new))
    proposals.sort(key=lambda x: (-x[0], [e["name"] for e in x[1]], [e["name"] for e in x[2]]))
    exact, seen = [], set()
    for _, out, new in proposals[:PRESELECTION]:
        delta = {}
        for e in new:
            for k, x in e["fields"].items():
                delta[k] = delta.get(k, 0.0) + x
        for e in out:
            for k, x in e["fields"].items():
                delta[k] = delta.get(k, 0.0) - x
        delta = {k: x for k, x in delta.items() if abs(x) > 1e-9}
        w = _value(delta, pr)
        problems = _problems(swap_check, delta)
        key = (tuple(sorted(e["name"] for e in out)), tuple(sorted(e["name"] for e in new)))
        if key in seen:
            continue
        seen.add(key)
        exact.append({"out": [e["name"] for e in out], "in": [e["name"] for e in new],
                      "points_out": sum(e["points"] for e in out),
                      "points_in": sum(e["points"] for e in new),
                      "value": w, "problems": problems, "delta": delta})
    good = [t for t in exact if not t["problems"] and t["value"] >= MIN_ADVANTAGE]
    good.sort(key=lambda t: -t["value"])
    return good[:3]


# ---------------------------------------------------------------------------
# full plan: reset every devotion and place them anew
# ---------------------------------------------------------------------------

# Guarded values (user 30.09.2026: "do not forget values like Physical Resistance in the
# devotions"): a plan may not lower them while they are below the endgame target
# (targets.TARGETS) or would fall below it.
GUARDED_NAMES = MappingProxyType({
    "defensivePhysical": "Physical Resistance", "defensiveFreeze": "Freeze Resistance",
    "defensiveStun": "Stun Resistance", "defensivePetrify": "Petrify Resistance",
    "defensiveTrap": "Entrapment Resistance", "defensiveTotalSpeedResistance": "Slow Resistance",
    "defensiveDisruption": "Skill Disruption Protection",
    "defensiveProtection": "Armor (flat)", "defensiveProtectionModifier": "Armor %",
    "defensiveAbsorptionModifier": "Armor Absorption bonus %",
    "characterDodgePercent": "Chance to Avoid Melee %",
    "characterDeflectProjectile": "Chance to Avoid Projectiles %",
    "defensivePercentReflectionResistance": "Reflected Damage Reduction %",
    "defensiveBlockModifier": "Shield Block Chance %",
    "defensiveSlowLifeLeach": "Life Leech Resistance (gear)",
    "defensiveAllMaxResist": "Max All Resistances",
    **{f"defensive{t}MaxResist": f"Max {n} Resistance" for t, n in (
        ("Fire", "Fire"), ("Cold", "Cold"), ("Lightning", "Lightning"), ("Chaos", "Chaos"),
        ("Pierce", "Pierce"), ("Aether", "Aether"), ("Life", "Vitality"),
        ("Bleeding", "Bleeding"), ("Poison", "Acid"))},
})


def guard_targets(pr: ValueProfile | None = None) -> dict:
    penalty = pr.leech_penalty if pr is not None else 25.0
    return {"defensivePhysical": pr.physical_target if pr is not None else TARGETS["physical_combat"],
            "defensiveFreeze": TARGETS["freeze"],
            "defensiveStun": TARGETS["stun"], "defensiveTrap": 50.0,
            LEECH_FIELD: (pr.leech_target if pr is not None else TARGETS["leech"]) + penalty,
            "defensiveTotalSpeedResistance": 50.0}


def guards_broken(delta, pr: ValueProfile) -> list:
    """Names of the guarded values the plan pushes below their target (or lowers further when
    they already are below). DA and Health likewise."""
    combat = pr.combat or pr.sheet or {}
    out = []
    for f, goal in guard_targets(pr).items():
        d = delta.get(f, 0.0)
        if d < -1e-9 and combat.get(f, 0.0) + d < goal:
            out.append(GUARDED_NAMES.get(f, f))
    res = pr.resists or {}
    for name, _boss in BOSS_DAMAGE_TYPES.items():
        fld = {"Vitality": "defensiveLife"}.get(name, f"defensive{name}")
        d = delta.get(fld + "MaxResist", 0.0) + delta.get("defensiveAllMaxResist", 0.0)
        cap = (res.get(fld) or {}).get("cap", 80.0)
        if d < -1e-9 and cap + d < TARGETS["max_res"]:
            out.append(f"Max {name} Resistance")
    w = stat_deltas(delta, pr)
    health_goal = pr.health_goal if pr.health_goal is not None else TARGETS["health"]
    for k, goal, name in (("da", pr.da_target, "Defensive Ability"),
                          ("health", health_goal, "Health")):
        a, n = w.get(k, (0, 0))
        if n < a - 0.5 and n < goal:
            out.append(name)
    return out


def guard_rows(delta, pr: ValueProfile) -> list:
    """Before/after of every changed defensive value except the nine resistances with a need
    (those come from the swap check)."""
    combat = pr.combat or pr.sheet or {}
    return [{"name": n, "before": combat.get(f, 0.0), "after": combat.get(f, 0.0) + delta[f]}
            for f, n in GUARDED_NAMES.items() if abs(delta.get(f, 0.0)) > 1e-9]


def _sum_fields(keys, uni) -> dict:
    out = {}
    for k in keys:
        for f, v in uni[k]["fields"].items():
            out[f] = out.get(f, 0.0) + v
    return out


def _difference(new, old) -> dict:
    d = {k: new.get(k, 0.0) - old.get(k, 0.0) for k in set(new) | set(old)}
    return {k: v for k, v in d.items() if abs(v) > 1e-9}


def _valid(keys, uni, aff, locked, budget) -> bool:
    if not locked <= keys or sum(uni[k]["points"] for k in keys) > budget:
        return False
    return state_valid(keys, keys, aff)


def build_order(target, uni, aff, budget=PLAN_BUDGET, every=None, max_nodes=SEARCH_NODES):
    """The fast greedy order first, else the bounded search. Every order that is delivered is replayed
    step by step with the game rules (check_build_order): none is better than an invalid one (.69)."""
    every = every or uni
    pts = {k: v["points"] for k, v in every.items()}

    def valid(order):
        return order is not None and check_build_order(order, target, aff, pts, budget)[0]
    f = _build_order_greedy(target, uni, aff, budget, every)
    if valid(f):
        return f
    f = build_order_search(target, aff, every, budget, max_nodes)
    return f if valid(f) else None


def build_order_search(target, aff, every, budget=PLAN_BUDGET, max_nodes=SEARCH_NODES):
    """Depth-first search with memory, iterative: place the target constellations, place
    stepping stones (constellations outside the target with at most 5 points that give
    affinity) and remove them again, never above the budget. Same step list as build_order()
    or None. The visiting order and the node budget equal the recursive .35 search (the
    recursion needed ~1,000 frames - .45)."""
    target = frozenset(target)
    stone_pool = sorted((k for k in every if k not in target and k in aff
                         and aff[k]["gives"] and every[k]["points"] <= 5),
                        key=lambda k: every[k]["points"])
    pts = {k: every[k]["points"] for k in every}

    def moves_of(placed):
        v = affinity_sum(placed, aff)
        used = sum(pts[k] for k in placed)
        rest = target - placed
        missing = {}
        for k in sorted(rest):
            for a, n in aff[k]["needs"].items():
                missing[a] = max(missing.get(a, 0), n - v.get(a, 0))
        moves = []
        for k in sorted(rest):                       # place a target
            if covered(aff[k]["needs"], v) and used + pts[k] <= budget:
                use = sum(min(n, max(missing.get(a, 0), 0)) for a, n in aff[k]["gives"].items())
                moves.append((0, -use, pts[k], "+", k))
        for k in sorted(placed - target):            # remove a stepping stone
            without = placed - {k}
            if state_valid(without, without, aff):
                moves.append((1, 0, -pts[k], "-", k))
        for k in stone_pool:                         # place a stepping stone
            if k in placed or used + pts[k] > budget or not covered(aff[k]["needs"], v):
                continue
            use = sum(min(n, max(missing.get(a, 0), 0)) for a, n in aff[k]["gives"].items())
            if use > 0:
                moves.append((2, -use / pts[k], pts[k], "+", k))
        return iter([(action, k) for _, _, _, action, k in sorted(moves)])

    seen = set()
    nodes = 1
    root = frozenset()
    seen.add(root)
    if root == target:
        return []
    stack = [(root, [], moves_of(root))]
    while stack:
        placed, steps, todo = stack[-1]
        nxt = next(todo, None)
        if nxt is None:
            stack.pop()
            continue
        action, k = nxt
        new = placed | {k} if action == "+" else placed - {k}
        child = steps + [(action, k, affinity_sum(new, aff), sum(pts[x] for x in new))]
        nodes += 1
        if nodes > max_nodes:
            return None                  # budget used up: no later node can succeed
        if new in seen:
            continue
        seen.add(new)
        if new == target:
            return child
        stack.append((new, child, moves_of(new)))
    return None


def _build_order_greedy(target, uni, aff, budget=PLAN_BUDGET, every=None):
    """Build order of the target set from zero points.

    [(action, key, affinity_after, points_after)] or None. action: '+' place, '-' remove a
    stepping stone. A constellation may be placed only when its need is covered by the
    complete ones (Crate Game Guide). Stepping stones come from 'every' (not in the target)."""
    every = every or uni
    placed, steps, stones = [], [], []
    rest = set(target)

    def stock():
        return affinity_sum(set(placed), aff)

    def points():
        return sum(every[k]["points"] for k in placed)

    def removable(new=None):
        """Stones that may go now: every remaining constellation stays covered and, when `new` is to be
        placed next, `new` stays covered too (placing checks the need against the others as they are
        AFTER the removal - a stone is never taken back before the one that needs it is placed)."""
        out = []
        for s in stones:
            without = set(placed) - {s}
            if not state_valid(without, without, aff):
                continue
            if new is not None and not covered(aff[new]["needs"], affinity_sum(without, aff)):
                continue
            out.append(s)
        return out

    def make_room(new):
        """Take stones back until `new` fits the budget; False when that is not possible without
        breaking a constellation or the cover of `new`."""
        while points() + every[new]["points"] > budget:
            gone = removable(new)
            if not gone:
                return False
            placed.remove(gone[0])
            stones.remove(gone[0])
            steps.append(("-", gone[0], stock(), points()))
        return True

    for _ in range(200):
        if not rest:
            break
        v = stock()
        placeable = [k for k in sorted(rest) if covered(aff[k]["needs"], v)]
        blocked = [k for k in sorted(rest) if k not in placeable]
        missing = {}
        for k in sorted(rest):
            for a, n in aff[k]["needs"].items():
                missing[a] = max(missing.get(a, 0), n - v.get(a, 0))

        def use_of(k):
            return sum(min(n, max(missing.get(a, 0), 0)) for a, n in aff[k]["gives"].items())
        # while something is blocked, put no points into constellations that unlock nothing -
        # otherwise the room for stepping stones is missing later
        if blocked:
            placeable = [k for k in placeable if use_of(k) > 0]
        if placeable:
            k = max(placeable, key=lambda k: (use_of(k), -every[k]["points"]))
            if not make_room(k):
                return None
            placed.append(k)
            rest.discard(k)
            steps.append(("+", k, stock(), points()))
            continue

        # stepping stone for the constellation with the smallest shortfall
        def shortfall(k):
            return {a: n - v.get(a, 0) for a, n in aff[k]["needs"].items() if n > v.get(a, 0)}
        target_k = min(sorted(rest), key=lambda k: sum(shortfall(k).values()))
        fb = shortfall(target_k)
        cands = [s for s in every if s not in target and s not in placed
                 and covered(aff[s]["needs"], v)
                 and any(a in fb for a in aff[s]["gives"])]
        if not cands:
            return None
        s = max(cands, key=lambda s: (sum(min(n, fb.get(a, 0)) for a, n in aff[s]["gives"].items())
                                      / every[s]["points"], -every[s]["points"]))
        if not make_room(s):
            return None
        placed.append(s)
        stones.append(s)
        steps.append(("+", s, stock(), points()))
    if rest:
        return None
    for _ in range(len(stones)):
        gone = removable()
        if not gone:
            return None
        s = gone[0]
        placed.remove(s)
        stones.remove(s)
        steps.append(("-", s, stock(), points()))
    return steps


def plan(index, sb, aff, taken, weapon_ok, pr, swap_check=None, budget=PLAN_BUDGET, freed=()):
    """Best set for a full reset (locked: bound powers, except those in 'freed' - variant B
    frees the powers of the tier-1 constellations to show what they cost in values)."""
    uni = {}
    for k, info in sb.items():
        if k not in aff or not weapon_ok(info["name"]):
            continue
        f = star_fields(index, info["stars"])
        uni[k] = {"name": info["name"], "points": len(info["stars"]), "fields": f,
                  "power": index.display(info["power"]) if info["power"] else None}
    every = dict(uni)
    for e in taken:                          # also know incomplete ones
        if e["key"] not in every:
            every[e["key"]] = {"name": e["name"], "points": e["total"],
                               "fields": e["fields"], "power": e["power"]}
    locked = {e["key"] for e in taken if e["bound"] and e["key"] in uni and e["key"] not in freed}
    current_fields = {}
    for e in taken:
        for f, v in e["fields"].items():
            current_fields[f] = current_fields.get(f, 0.0) + v

    def exact(keys):
        delta = _difference(_sum_fields(keys, uni), current_fields)
        problems = _problems(swap_check, delta)
        problems += guards_broken(delta, pr)
        return _value(delta, pr), delta, problems

    single = {k: _value(u["fields"], pr) if u["fields"] else 0.0 for k, u in uni.items()}

    def climb(start):
        s = set(start)
        w, _, prob = exact(s)
        if prob:
            w = -1e9
        for _ in range(PLAN_ROUNDS):
            free = budget - sum(uni[k]["points"] for k in s)
            out_pool = sorted(k for k in s if k not in locked)
            in_pool = sorted((k for k in uni if k not in s),
                             key=lambda k: (-single[k] / uni[k]["points"], k))[:30]
            steps = []
            for n_out in (0, 1, 2):
                for out in itertools.combinations(out_pool, n_out):
                    p_out = sum(uni[k]["points"] for k in out)
                    v_out = sum(single[k] for k in out)
                    for n_in in (0, 1, 2, 3):
                        if n_out + n_in == 0:
                            continue
                        pool = in_pool if n_in < 3 else in_pool[:18]
                        for new_in in itertools.combinations(pool, n_in):
                            if sum(uni[k]["points"] for k in new_in) > free + p_out:
                                continue
                            new = (s - set(out)) | set(new_in)
                            if not state_valid(new, new, aff):
                                continue
                            steps.append((sum(single[k] for k in new_in) - v_out, new))
            steps.sort(key=lambda x: (-x[0], sorted(x[1])))
            best = None
            for _, new in steps[:40]:
                w2, _, prob = exact(new)
                if not prob and w2 > w + PLAN_STEP and (best is None or w2 > best[0]):
                    best = (w2, new)
            if not best:
                break
            w, s = best
        return w, s

    start_now = {e["key"] for e in taken if e["complete"] and e["key"] in uni}
    results = [climb(start_now)]
    if _valid(locked, uni, aff, locked, budget):
        results.append(climb(locked))
    results.sort(key=lambda x: -x[0])
    for w, s in results:
        if w <= -1e8:                 # no valid set (guarded values broken)
            continue
        order = build_order(s, uni, aff, budget, every=uni)
        if order is None:
            continue
        _, delta, prob = exact(s)
        now_k = {e["key"] for e in taken}
        effect = stat_deltas(delta, pr)
        # what is lost is only what is taken (Murmur 2 of 6 stars)
        info = dict(every)
        for e in taken:
            if e["key"] in now_k - s:
                info[e["key"]] = {"name": e["name"], "points": e["points"],
                                  "fields": e["fields"],
                                  "power": e["power"] if e["power"] else None,
                                  "of": e["total"]}
        res = []
        if swap_check:
            rows = swap_check(delta)[0]
            res = [z for z in rows if "need" in z]
        return {"set": sorted(s, key=lambda k: uni[k]["name"]),
                "out": sorted(now_k - s, key=lambda k: every[k]["name"]),
                "in": sorted(s - now_k, key=lambda k: uni[k]["name"]),
                "kept": sorted(s & now_k, key=lambda k: uni[k]["name"]),
                "locked": sorted(locked), "freed": sorted(freed), "value": w, "delta": delta,
                "effect": effect, "resistances": res,
                "guarded": guard_rows(delta, pr),
                "points": sum(uni[k]["points"] for k in s),
                "order": order, "info": info}
    return None


# Fields the value scale does NOT cover - always shown one by one for a plan, so a loss (e.g.
# Life Leech from Ghoul) is not lost.
NOT_VALUED = ("offensiveLifeLeechMin", "characterLifeRegen", "characterLifeRegenModifier",
              "characterTotalSpeedModifier", "characterRunSpeedModifier",
              "characterManaRegen", "characterManaRegenModifier", "defensiveProtection",
              "defensiveProtectionModifier", "defensiveAbsorptionModifier",
              "retaliationTotalDamageModifier", "defensiveBlockModifier",
              "defensiveTotalSpeedResistance", "defensiveStun", "defensiveTrap",
              "skillCooldownReduction", "characterConstitutionModifier")


def check_build_order(order, target, aff, points, budget=PLAN_BUDGET) -> tuple[bool, str]:
    """(ok, reason): every step placeable, never above the budget, at the end = target."""
    placed = set()
    for action, k, _v, _n in order:
        if action == "+":
            if not covered(aff[k]["needs"], affinity_sum(placed, aff)):
                return False, f"{k} placed without affinity"
            placed.add(k)
        else:
            placed.discard(k)
            if not state_valid(placed, placed, aff):
                return False, f"removing {k} breaks affinity"
        if sum(points[x] for x in placed) > budget:
            return False, "more than the devotion points"
    ok = placed == set(target)
    return ok, ("ok" if ok else "end state differs from the plan")
