"""Counterfactual engine (X-011): apply a change to a copy of the character, rebuild
the sources, the snapshot and the targets, and compare. Every proposal is judged
against the same targets as the current state; a change that pushes a resistance
below its target or turns an ok target into a gap is flagged (guard)."""
from __future__ import annotations

import copy
import functools
from dataclasses import replace

from ...combat import actions as ACT
from ...combat import target_state as TS
from ...domain import loader
from ...domain.enums import Activity, SourceKind, StackingPolicy
from ...domain.sources import EffectInstance, SourceInstance, SourceSet
from ...mechanics import resist as RES
from ...stats import sheet as SH
from .. import area_facts as AF
from .. import damage_types as DT
from .. import readiness as RD
from .. import stage as STG
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
        self.gates = STG.gate_list(ctx, snap, melee)  # report v2: the six gates once, open ones are compared with
        self.settings = RD.settings(targets_data)    # the score block the analysis was scored with

    @property
    def endgame(self) -> bool:
        return getattr(self.stage, "value", str(self.stage)) == "ENDGAME"

    @functools.cached_property
    def damage_now(self) -> dict:
        return _main_percentages(self.snap)

    @functools.cached_property
    def watch_now(self) -> dict:
        return _watched(self.snap, self.main_types)

    @functools.cached_property
    def rr_now(self) -> dict:
        return _rr_weighted(TS.resistance_detail(self.snap, self.sources))


def baseline_of(a) -> Baseline:
    """The baseline of a finished analysis, built again from its parts: the targets are rated with the same inputs
    (`Analysis.extra`), so `results` match `a.results`."""
    from .. import targets as TGT
    return Baseline(a.ctx, a.sources, a.snap, a.stage, a.archetype == "melee", TGT.load(), a.extra,
                    main_damage_types(a.actions))


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


def cdr_source(record: str, extra: float) -> SourceSet:
    """A permanent `skillCooldownReduction` of `extra` % as one more source (what a gear change would add); `record` is the
    debuff the cooldown reduction keeps up, the source id is `uptime:<record>`."""
    sid = f"uptime:{record}"
    src = SourceInstance(sid, SourceKind.GRANTED_SKILL, sid, None, record, None, "Cooldown reduction")
    eff = EffectInstance(f"{sid}:cdr", sid, "skillCooldownReduction", float(extra), Activity.PERMANENT,
                         StackingPolicy.ADDITIVE, "uptime")
    return SourceSet(sources=(src,), effects=(eff,))


def apply_changes(base: Baseline, changes) -> tuple:
    """(ctx2, src2, snap2, extra2) of the baseline's character with the `changes` applied. A change is a tuple, the form
    `Proposal.detail["apply"]` holds:
      ("item_part", slot, part, record)   one part of a worn item replaced
      ("item", slot, item_dict)           a worn item replaced as a whole
      ("skills", ((record, points), ...)) skill and constellation points set (0 removes)
      ("attributes", ((name, value), ...)) the base attributes replaced
      ("consumable", SourceSet)           the sources of a consumable, counted while it runs
      ("cdr", extra, record)              `extra` % more cooldown reduction that keeps the debuff `record` up
    Order: the character first (in the order given), then the sources of the changed character, then the extra sources.
    Several "cdr" changes make one source of the largest extra, not their sum. `extra2` is the baseline's own target inputs
    (the same object) unless a "cdr" change is applied: then the area facts are computed again from the changed character
    and replace the inputs they feed, which is the only way the resistance reduction is re-rated (decision E3)."""
    character, changed, extra_sources, cdr = base.ctx.character, False, [], None
    for kind, *args in changes:
        if kind == "item_part":
            character, changed = with_item_part(character, *args), True
        elif kind == "item":
            character, changed = with_item(character, *args), True
        elif kind == "skills":
            character, changed = with_skills(character, dict(args[0])), True
        elif kind == "attributes":
            character, changed = with_attributes(character, dict(args[0])), True
        elif kind == "consumable":
            extra_sources.append(args[0])
        elif kind == "cdr":
            cdr = args if cdr is None or args[0] > cdr[0] else cdr
        else:
            raise ValueError(f"unknown change {kind!r}")
    src = loader.build_sources(character, base.ctx.index) if changed else base.sources
    for more in extra_sources + ([cdr_source(cdr[1], cdr[0])] if cdr else []):
        src = src.plus(more)
    ctx2, src2, snap2 = snapshot_for(base, character if changed else None, src)
    extra2 = base.extra
    if cdr:
        extra2 = {**base.extra, **AF.target_inputs(AF.area_facts(ctx2, src2, snap2, base.stage))}
    return ctx2, src2, snap2, extra2


def _main_percentages(snap) -> dict:
    """Damage type -> its percentage on the character sheet (own bonus, all damage, elemental; no attribute share)."""
    return {e["typ"]: e["summe"] for e in DT.damage_percentages(snap.sheet.fields)}


# What `compare` cannot re-rate (decision E3, fix round 1): the dummy time and the resistance reduction stay those of the
# current state, and neither skill ranks nor flat damage are in the target values. A "+N score" must never sit next to
# such a loss, so the mark `loses_unrated` is deliberately conservative. It is True when ANY of these holds
# (`damage_lost`, `rr_lost`, `takes_points_away`, the `unrated` argument of `compare`):
#   - a lost line is not a plain "+N% <type> Damage" line (a "+N to all skills", "+N to <skill>", flat "+N Fire Damage"),
#     or it is a main type's percentage, or an Elemental bonus while a main type is elemental;
#   - the sheet percentage of a main type falls by one point or more, or its flat damage / the skill levels on the sheet fall;
#   - the weighted resistance reduction kinds of a main type fall;
#   - the change takes points from a skill, a constellation star or an attribute (their damage effect is not re-rated);
#   - the caller says so (`unrated`: a gear part gives up a skill rank bonus, `rank_bonus_lost`).
_ELEMENTAL_RES = ("Fire", "Cold", "Lightning")
_SKILL_LEVEL_FIELDS = ("augmentAllLevel",)


def _flat_fields(main_types) -> tuple:
    """Sheet fields of the flat damage of the main types (direct and over time, plus the elemental flat of an elemental one)."""
    out = []
    for t in sorted(main_types):
        fld = DT.DAMAGE_TYPES.get(t, ("",))[0]
        if fld.endswith("Modifier"):
            out += [fld[:-len("Modifier")] + "Min", fld[:-len("Modifier")] + "Max"]
        if ACT.RES_OF.get(t, t) in _ELEMENTAL_RES:
            out += ["offensiveElementalMin", "offensiveElementalMax"]
    return tuple(dict.fromkeys(out)) + _SKILL_LEVEL_FIELDS


def _watched(snap, main_types) -> dict:
    f = snap.sheet.fields
    return {k: float(f.get(k, 0.0) or 0.0) for k in _flat_fields(main_types)}


def _line_loses(base: Baseline, line: str) -> bool:
    """True for a lost line `lost_damage_value` does not weigh: anything that is not "+N% <label>", or an Elemental bonus
    while a main type is elemental."""
    if "%" not in line:
        return True
    label = line.split("% ", 1)[-1].replace(" Damage", "")
    return label == "Elemental" and any(ACT.RES_OF.get(t, t) in _ELEMENTAL_RES for t in base.main_types)


def damage_lost(base: Baseline, snap2, lost) -> bool:
    """True when a change gives up damage of the main skill that `compare` does not re-rate (rule above): lost lines
    (`lost_damage_value`, `_line_loses`), a main damage type whose sheet percentage falls by one percentage point or more,
    or flat damage of a main type / skill levels (`augmentAllLevel`) on the sheet that fall."""
    if lost_damage_value(base, lost) < 0 or any(_line_loses(base, x) for x in lost or ()):
        return True
    now, new = base.damage_now, _main_percentages(snap2)
    if any(now[t] - new[t] >= 1.0 - 1e-9 for t in base.main_types if t in now and t in new):
        return True
    after = _watched(snap2, base.main_types)
    return any(after[k] < v - 1e-9 for k, v in base.watch_now.items())


def takes_points_away(before, after) -> bool:
    """True when a character after a change has fewer points in any skill or constellation star (a removed one included)
    or in any attribute than before: the damage those points fed is not re-rated."""
    if any((after.skills.get(rec) or 0) < pts for rec, pts in before.skills.items()):
        return True
    return any((after.attributes_base.get(k) or 0) < (v or 0) for k, v in before.attributes_base.items())


def rank_bonuses(idx, records) -> dict:
    """{('all',) | ('skill', record) | ('mastery', record): levels} of the skill rank bonuses ("+N to all skills",
    "+N to <skill>", "+N to all skills of <mastery>") of item records (base, affixes, augment, component ...), summed."""
    out: dict = {}
    for rec in records:
        r = idx.record(rec) if rec else None
        if not r:
            continue
        if r.get("augmentAllLevel"):
            out[("all",)] = out.get(("all",), 0.0) + float(r["augmentAllLevel"])
        for kind in ("Skill", "Mastery"):
            for i in range(1, 6):
                name, lv = r.get(f"augment{kind}Name{i}"), r.get(f"augment{kind}Level{i}")
                if name and lv:
                    key = (kind.lower(), name)
                    out[key] = out.get(key, 0.0) + float(lv)
    return out


def rank_bonus_lost(old: dict, new: dict) -> bool:
    """True when a part change gives up skill levels: some bonus of `old` has fewer levels in `new`."""
    return any(new.get(k, 0.0) < v - 1e-9 for k, v in old.items())


def _rr_weighted(detail) -> dict:
    return {rf: AF.rr_weighted(d.perm, d.weak) for rf, d in detail.items()}


def rr_lost(base: Baseline, snap2, src2) -> bool:
    """True when a change gives up resistance reduction on a resistance type of a main damage type (a DoT counts through
    its resistance): the weighted kinds of `resistance_detail` (a permanent kind 1, one that is not permanent 1/2,
    weighted like `rr_main`) fall. `src2` is the source set of the change; without it the baseline's sources stand in."""
    now = base.rr_now
    new = _rr_weighted(TS.resistance_detail(snap2, base.sources if src2 is None else src2))
    types = {ACT.RES_OF.get(t, t) for t in base.main_types}
    return any(new.get(rf, 0.0) < now[rf] - 1e-9 for rf in types if rf in now)


def annotate(cmp: dict, temporary: bool = False) -> dict:
    """The four report v2 values of a comparison for `Proposal.detail`. A temporary step (a consumable) opens no gate:
    it covers the target while it runs (decision E2)."""
    return {"score_delta": cmp["score_delta"], "gates_opened": () if temporary else cmp["gates_opened"],
            "deficits_after": cmp["deficits_after"], "loses_unrated": cmp["loses_unrated"]}


def compare(base: Baseline, ctx2, snap2, main_dph=None, lost=None, extra: dict | None = None, src2=None,
            unrated: bool = False) -> dict:
    """Compare a changed character with the baseline. `extra` replaces the target inputs of the baseline for the new
    rating (default: the baseline's own, so the dummy time and the resistance reduction are NOT re-rated - that is what
    `loses_unrated` marks); `src2` is the source set of the change (for the resistance reduction it gives up).
    Besides gains, costs, fixes and breaks the result holds `score_delta` (unrounded points, None in EARLY),
    `gates_opened` (ids of the gates open now and met after), `deficits_after` (open target -> its deficit after) and
    `loses_unrated` (conservative, see the rule above `damage_lost`; `unrated=True` is the caller's own knowledge of a
    loss, e.g. a skill rank bonus of a replaced gear part)."""
    from ..targets import evaluate
    new = {r.id: r for r in evaluate(ctx2, snap2, base.stage, base.data, base.extra if extra is None else extra,
                                     base.melee)}
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
    after = {g.id: g for g in STG.gate_list(ctx2, snap2, base.melee)}
    opened = tuple(g.id for g in base.gates if not g.met and after[g.id].met)
    deficits = {i: (None if i not in new or new[i].deficit is None else max(new[i].deficit, 0.0))
                for i, r in base.results.items() if r.status in ("gap", "tight")}
    return {"gains": tuple(gains), "costs": tuple(costs), "fixes": fixes, "breaks": breaks,
            "results": new, "score": score, "net": net,
            "score_delta": RD.score_delta(list(base.results.values()), list(new.values()), base.stage, base.settings),
            "gates_opened": opened, "deficits_after": deficits,
            "loses_unrated": bool(unrated or takes_points_away(base.ctx.character, ctx2.character)
                                  or damage_lost(base, snap2, lost) or rr_lost(base, snap2, src2))}


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
