"""Counterfactual engine (X-011): apply a change to a copy of the character, rebuild
the sources, the snapshot and the targets, and compare. Every proposal is judged
against the same targets as the current state; a change that pushes a resistance
below its target or turns an ok target into a gap is flagged (guard)."""
from __future__ import annotations

import copy
from dataclasses import replace

from ...domain import loader
from ...domain.sources import SourceSet
from ...mechanics import resist as RES
from ...stats import sheet as SH
from .model import Delta

KEYS = [("Health", lambda s: s.sheet.abilities["health"], ""),
        ("Defensive Ability", lambda s: s.combat.abilities["da"], ""),
        ("Offensive Ability", lambda s: s.combat.abilities["oa"], ""),
        ("Physical Res", lambda s: min(s.combat.resists["Physical"]["value"], s.combat.resists["Physical"]["cap"]), "%"),
        ("Life Leech Res", lambda s: s.sheet.leech["value"], "%"),
        ("Freeze Res", lambda s: s.combat.fields.get("defensiveFreeze", 0.0), "%"),
        ("Stun Res", lambda s: s.combat.fields.get("defensiveStun", 0.0), "%"),
        ("Slow Res", lambda s: s.combat.fields.get("defensiveTotalSpeedResistance", 0.0), "%"),
        ("Armor Absorption", lambda s: s.sheet.absorption, "%"),
        ("Armor (weighted)", lambda s: s.sheet.armor_weighted, "")]
KEYS += [(f"{n} Res", (lambda n: lambda s: s.combat.resists[n]["value"])(n), "%")
         for n in RES.RESISTS]


# .64: resistance above the cap is a buffer against enemy resistance reduction - the first OVERCAP_BUFFER
# points count OVERCAP_WEIGHT per point in ENDGAME (Ravager of Flesh -25, Ravager of Souls -39 from the game
# database; community advice 15-30 overcap for Ultimate / SR), beyond that and in EARLY/MID 0.2 / 0.1.
OVERCAP_BUFFER = 30.0
OVERCAP_WEIGHT = {"ENDGAME": 0.5, "MID": 0.2, "EARLY": 0.2}
OVERCAP_BEYOND = 0.1
LOST_DAMAGE_WEIGHT = 0.1          # per percent point of a damage type the main action deals (.64, HEURISTIC)
FIX_BONUS_PER_POINT = 2.0         # .64: +2 per point of deficit a proposal closes, at most FIX_BONUS_MAX
FIX_BONUS_MAX = 10.0
NET_GATE = 2.0                    # .64: a proposal must gain at least this much net (community targets)


class Baseline:
    """Current state, computed once; counterfactuals are compared against it."""

    def __init__(self, ctx, sources, snap, stage, melee, targets_data, extra, main_types=None):
        self.ctx, self.sources, self.snap = ctx, sources, snap
        self.stage, self.melee, self.data, self.extra = stage, melee, targets_data, extra
        from ..targets import evaluate
        self.results = {r.id: r for r in evaluate(ctx, snap, stage, targets_data, extra, melee)}
        self.values = {k: f(snap) for k, f, _ in KEYS}
        self.main_types = set(main_types or ())      # damage types of the main action (.64, lost damage)

    @property
    def endgame(self) -> bool:
        return getattr(self.stage, "value", str(self.stage)) == "ENDGAME"


def main_damage_types(actions, share: float = 0.1) -> set:
    """Damage types that make up at least `share` of the main skill's damage (.64): losing '+% Chaos Damage'
    costs a Chaos build, '+% Fire Damage' costs it nothing."""
    main = next((a for a in actions if "defaultweaponattack" not in a.action_id), None)
    parts = getattr(main, "parts", None) or {}
    total = sum(parts.values()) or 1.0
    return {t for t, v in parts.items() if v / total >= share}


def value(d, snap_before, endgame: bool = False) -> float:
    """Ranking weight of a change (not shown): ~1 point = 1% Health or 1 Res below cap."""
    lab, ch = d.label, d.change
    if lab == "Health":
        return ch / max(snap_before.sheet.abilities["health"] / 100, 1)
    if lab.startswith("DA"):
        return ch / 25
    if lab.startswith("OA"):
        return ch / 30
    if lab == "Physical Res":
        return ch
    if lab == "Armor Absorption":
        return ch * 0.5
    if lab.startswith("Armor"):
        return ch / 100
    if lab in ("Freeze Res", "Stun Res", "Slow Res"):
        # .63 (Windows run: Wraithwalkers ranked by Stun Res +30% at 130%): a crowd-control duration
        # reduction above 100% does nothing
        return (min(d.new, 100.0) - min(d.now, 100.0)) * 0.3
    if lab == "Life Leech Res":
        return ch * 0.3
    if lab == "Main skill damage":
        return 100 * ch / max(d.now, 1) * 0.5
    if lab.endswith(" Res"):
        n = lab[:-4]
        r = snap_before.combat.resists.get(n)
        if r is not None:
            return _resist_value(r["value"], r["value"] + ch, r["cap"], endgame)
    return 0.0


def _resist_value(now: float, new: float, cap: float, endgame: bool) -> float:
    """Piecewise on the actual position (.64): 1 per point below the cap, OVERCAP_WEIGHT per point in the
    buffer band above it, OVERCAP_BEYOND further up; negative changes mirror it."""
    sign = 1.0 if new >= now else -1.0
    lo, hi = min(now, new), max(now, new)
    band_w = OVERCAP_WEIGHT["ENDGAME" if endgame else "MID"]
    below = max(0.0, min(hi, cap) - lo)
    band = max(0.0, min(hi, cap + OVERCAP_BUFFER) - max(lo, cap))
    beyond = max(0.0, hi - max(lo, cap + OVERCAP_BUFFER))
    return sign * (below + band_w * band + OVERCAP_BEYOND * beyond)


def lost_damage_value(base: Baseline, lost) -> float:
    """Negative ranking value of lost '+X% <type> Damage' lines (.64): only types of the main action count."""
    v = 0.0
    for x in lost or ():
        try:
            pct = float(x.split("%")[0].lstrip("+"))
        except ValueError:
            continue
        label = x.split("% ", 1)[-1].replace(" Damage", "")
        if label == "All" or _dmg_type(label) in ("speed", "crit") or _dmg_type(label) in base.main_types:
            v -= LOST_DAMAGE_WEIGHT * pct
    return v


_LABEL_TO_TYPE = {"Vitality": "Life", "Acid": "Poison", "Vitality Decay": "VitalityDecay", "Burn": "Burn",
                  "Frostburn": "Frostburn", "Electrocute": "Electrocute", "Internal Trauma": "InternalTrauma",
                  "Elemental": "Elemental", "Total Speed": "speed", "Attack Speed": "speed", "Casting Speed": "speed",
                  "Crit Damage": "crit"}


def _dmg_type(label: str) -> str:
    return _LABEL_TO_TYPE.get(label, label)


def fix_bonus(base: Baseline, fixes) -> float:
    """+2 per point of deficit closed, at most +10 (.64, critic: a flat +10 made 29 -> 30 Physical Res worth
    10% Health); fact targets keep the full +10 (they are gates)."""
    total = 0.0
    for t in fixes:
        r = base.results.get(t)
        if r is None:
            continue
        if r.basis == "fact":
            total += FIX_BONUS_MAX
        else:
            deficit = abs(r.deficit) if r.deficit is not None else FIX_BONUS_MAX
            total += min(FIX_BONUS_MAX, FIX_BONUS_PER_POINT * max(deficit, 0.5))
    return total


def snapshot_for(base: Baseline, character=None, sources: SourceSet | None = None):
    ctx = base.ctx if character is None else replace(base.ctx, character=character)
    src = sources if sources is not None else loader.build_sources(ctx.character, ctx.index)
    return ctx, src, SH.build(ctx, src)


def compare(base: Baseline, ctx2, snap2, main_dph=None, lost=None) -> dict:
    from ..targets import evaluate
    new = {r.id: r for r in evaluate(ctx2, snap2, base.stage, base.data, base.extra, base.melee)}
    gains, costs = [], []
    for label, f, unit in KEYS:
        a, b = base.values[label], f(snap2)
        if abs(b - a) < (0.5 if unit != "%" else 0.05):
            continue
        d = Delta(label, a, b, unit)
        (gains if d.good else costs).append(d)
    if main_dph is not None:
        a, b = main_dph
        if abs(b - a) / max(abs(a), 1) > 0.005:
            d = Delta("Main skill damage", a, b, "")
            (gains if d.good else costs).append(d)
    rank = {"gap": 0, "tight": 1, "ok": 2, "info": 2}
    fixes = tuple(sorted(i for i, r in new.items() if i in base.results
                         and rank[r.status] > rank[base.results[i].status]))
    # a target that is already missing must not get worse either (old swap filter, N-006)
    deeper = {i for i, r in new.items() if i in base.results
              and base.results[i].status in ("gap", "tight") and r.status in ("gap", "tight")
              and r.deficit is not None and base.results[i].deficit is not None
              and r.deficit > base.results[i].deficit + max(0.5, 0.005 * abs(r.target or 0))}
    deeper |= _resist_deeper(base, ctx2, snap2)
    breaks = tuple(sorted(deeper | {i for i, r in new.items() if i in base.results
                                    and rank[r.status] < rank[base.results[i].status]}))
    net = sum(value(d, base.snap, base.endgame) for d in gains + costs) + lost_damage_value(base, lost)
    score = net + fix_bonus(base, fixes) - 10 * len(breaks)
    return {"gains": tuple(gains), "costs": tuple(costs), "fixes": fixes, "breaks": breaks,
            "results": new, "score": score, "net": net}


def worth_it(base: Baseline, cmp) -> bool:
    """A proposal is worth showing when its score (net gains - costs - lost damage + fix bonus) reaches
    NET_GATE, or when it closes a fact target (.64; before: any proposal towards a 'gap' target)."""
    if cmp.get("breaks"):
        return False
    if any(base.results[t].basis == "fact" for t in cmp.get("fixes") or () if t in base.results):
        return True
    return cmp.get("score", 0.0) >= NET_GATE


def _resist_deeper(base: Baseline, ctx2, snap2, margin: float = 0.5) -> set:
    """Count targets (res_ultimate, res_own_cap) hide a single resistance that drops further while
    already below its target - the count stays the same. .62 (product review of .61: a test save's
    mask swap cost Aether -27 with Aether already below the cap): such a change breaks the target."""
    from .. import stage as STG
    out = set()
    for tid in ("res_ultimate", "res_own_cap"):
        r = base.results.get(tid)
        if r is None or r.status not in ("gap", "tight"):
            continue
        if tid == "res_ultimate":
            a, b = STG.ultimate_resists(base.ctx, base.snap), STG.ultimate_resists(ctx2, snap2)
            below = [n for n, v in a.items() if round(v["value"]) < v["target"]]
        else:
            a = {n: {"value": v["value"]} for n, v in base.snap.sheet.resists.items()}
            b = {n: {"value": v["value"]} for n, v in snap2.sheet.resists.items()}
            below = [n for n in RES.RESISTS if base.snap.sheet.resists[n]["value"] < base.snap.sheet.resists[n]["cap"]]
        if any(b[n]["value"] < a[n]["value"] - margin for n in below):
            out.add(tid)
    return out


def with_item_part(character, slot: str, part: str, record: str | None):
    """Deep copy of the character with one part of a worn item replaced."""
    ch = copy.copy(character)
    ch.worn = [copy.copy(w) for w in character.worn]
    for w in ch.worn:
        if w.slot == slot:
            w.item = dict(w.item)
            if record:
                w.item[part] = record
            else:
                w.item.pop(part, None)
    return ch


def with_item(character, slot: str, item: dict):
    ch = copy.copy(character)
    ch.worn = [copy.copy(w) for w in character.worn]
    for w in ch.worn:
        if w.slot == slot:
            w.item = dict(item)
    return ch


def with_skills(character, changes: dict):
    """changes {record: new points} (0 removes)."""
    ch = copy.copy(character)
    ch.skills = dict(character.skills)
    for rec, pts in changes.items():
        if pts > 0:
            ch.skills[rec] = pts
        else:
            ch.skills.pop(rec, None)
    return ch


def with_attributes(character, base: dict):
    ch = copy.copy(character)
    ch.attributes_base = dict(base)
    return ch
