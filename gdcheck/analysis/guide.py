"""Report v2: the guide - which proposals the report shows and in which order (spec 3.5 point 8, 3.6).

It is built lazily by the view factory, never by `analysis.run()`: the analysis has no field for it and md, text and json
pay nothing. `build_for(a)` takes the proposals of the analysis and adds the kinds only the guide knows (bind, refund,
devotion swap, more cooldown reduction), then derives:

  shown      per target the best permanent proposal and the best temporary one (a consumable), grouped by area. At most
             AREA_LIMIT per area, the areas in the order of what their open targets cost; the pair of one target stays whole,
             and no player action of the analysis is ever dropped
  area       the areas with what they are worth when solved (`impact` x width of the band of the open targets), the open
             Endgame gates they hold and the proposals shown for them
  steps      what to do next, at most STEP_LIMIT, fastest first
  ghost      the score after the steps whose change is modelled and loses nothing unrated (the others are left out, spec 3.4)
  conflicts  proposals that want the same slot part, or the one ointment slot
  residual   what stays short in an area after its permanent proposals together

Decisions E2 (a consumable opens no gate) and E3 (no number next to an unrated loss) are kept by what the proposals carry
(`CF.annotate`); this module reads those values through `v2` and never recomputes a score."""
from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from ..domain.enums import Stage
from . import readiness as RD
from .proposals import counterfactual as CF
from .proposals import effort as EF
from .proposals import fixes as FIX
from .proposals import select as SEL
from .proposals import uptime as UPP

STEP_LIMIT = 4
AREA_LIMIT = 3
MOVED_SHARE = 0.01      # a target counts as moved by a change when its deficit shrinks by this share of the target (HEURISTIC)

SKILLS_AREA = "Skills & Devotions"
_SKILL_KINDS = ("bind", "refund", "skills", "devotion", "devotion_swap", "attributes")
# the area an Endgame gate belongs to (the area of its target in targets.json)
GATE_AREA = {"level": "Survival", "resistances": "Resistances", "health": "Health", "da": "Survival",
             "physical": "Survival", "absorption": "Survival"}
# the unit of a target's value by its metric (targets.json): what `Residual.unit` says about `deficit`
_UNIT = {"physical_combat": "%", "leech_res": "%", "freeze_res": "%", "stun_res": "%", "slow_res": "%",
         "absorption": "%", "res_buffer": "%", "greatest_hit_share": "%", "res_ultimate_count": "types",
         "res_at_cap_count": "types", "dummy_ttk": "s"}
DEVOTION_PLAN_SLOT = "Devotions:plan"
_PART_WORD = {"augment_name": "augment", "relic_name": "component", "item": "item", "prefix_name": "prefix",
              "suffix_name": "suffix", "ascended_name": "ascended affix"}


@dataclass(frozen=True)
class Conflict:
    """Proposals in `shown` that want the same thing: `slot` is a slot part ("Amulet:augment_name") or "ointment";
    `label` names it for the reader ("Amulet augment", "Ointment"); `members` come best first."""
    slot: str
    label: str
    members: tuple


@dataclass(frozen=True)
class Lever:
    """One area: `worth` is the score its open targets cost (points, unrounded, 0 in EARLY), `gates` the Endgame gates
    still open in it (0 in EARLY), `members` the proposals shown for it, in the order they are listed."""
    name: str
    worth: float
    gates: int
    members: tuple


@dataclass(frozen=True)
class Residual:
    """A target that stays a gap in `area` although the permanent proposals shown for the area are all done; `deficit` is
    the distance left in the target's own `unit`, `now_text` / `target_text` its two values after the change, `applied` the
    proposals the change is made of."""
    area: str
    target: str
    label: str
    deficit: float
    now_text: str
    target_text: str
    applied: tuple          # ids of the proposals that were combined (consumables and a second change of one slot part are left out)
    unit: str               # of `deficit`: "%", "types", "s" or "" for a plain number (Health, Defensive Ability, level)


@dataclass(frozen=True)
class Guide:
    fixes: tuple
    shown: tuple
    area: tuple
    steps: tuple
    ghost: int | None
    ghost_steps: tuple
    endgame_after: bool
    conflicts: tuple
    residual: tuple
    unresolved: int


class V2(NamedTuple):
    score_delta: float | None
    gates_opened: tuple
    deficits_after: dict
    loses_unrated: bool


def v2(p) -> V2:
    """The four report v2 values of a proposal with their defaults (None, (), {}, False) for one that lacks them."""
    d = p.detail
    return V2(d.get("score_delta"), d.get("gates_opened", ()), d.get("deficits_after", {}),
              bool(d.get("loses_unrated", False)))


def unresolved(sources) -> int:
    """How many different records of the save are not in the game data (a mod, a newer patch)."""
    return len({s.record_id for s in sources.sources if s.unresolved})


# ---------------------------------------------------------------------------
# steps, conflicts
# ---------------------------------------------------------------------------

def _step_key(p) -> tuple:
    d = v2(p).score_delta
    return (EF.TIER_RANK[EF.tier(p)], d is None, -(d or 0.0), p.sort_key())


def sort_steps(props) -> tuple:
    """Fastest first: by effort tier, then the larger score gain first (one without a number after every number of its tier),
    then the usual order of proposals."""
    return tuple(sorted(props, key=_step_key))


def slot_of(p) -> str:
    """The slot part a proposal takes: its own `slot_part`; every devotion plan ("plan A", "plan B") is the same slot, because
    each rebuilds the whole set of constellations at a shrine - one of them is a step, the others are alternatives. Only the
    guide counts them so: the actions of md and text (`Analysis.player_actions`) stay as they were."""
    sp = p.detail.get("slot_part")
    return sp or (DEVOTION_PLAN_SLOT if p.kind == "devotion" and p.detail.get("plan") else "")


def steps_of(shown) -> tuple:
    """The steps out of the shown proposals: `select.player_actions` (one per target, never two changes on one slot part,
    two ointments, a purchase the rank does not allow yet or an expert-only reroll), fastest first."""
    return sort_steps(SEL.player_actions(list(shown), limit=STEP_LIMIT, slot_of=slot_of))


def _slot_label(slot_part: str) -> str:
    slot, part = slot_part.rsplit(":", 1)
    return f"{slot} {_PART_WORD.get(part, part.removesuffix('_name'))}"


def conflicts_of(shown) -> tuple:
    """Groups of two or more shown proposals for one slot part, of two or more ointments, and - as soon as an item swap
    ("Amulet:item") is among them - of everything shown for that slot (a swapped item takes its augment and component
    along, the same rule as `select.slot_clash`): the slot is then "<slot>:item" and the label the slot's name."""
    groups: dict = {}
    for p in sorted(shown, key=lambda p: p.sort_key()):
        if slot_of(p):
            groups.setdefault(slot_of(p), []).append(p)
        if p.kind == "consumable" and p.detail.get("ointment"):
            groups.setdefault("ointment", []).append(p)
    for slot_part in [k for k in groups if k.endswith(":item")]:
        slot = slot_part.rsplit(":", 1)[0]
        parts = [k for k in groups if k != "ointment" and k.rsplit(":", 1)[0] == slot]
        if len(parts) > 1:
            merged = sorted((p for k in parts for p in groups.pop(k)), key=lambda p: p.sort_key())
            groups[slot_part] = merged
    labels = {k: (k.rsplit(":", 1)[0] if k.endswith(":item") and any(slot_of(p) != k for p in ms)
                  else "Ointment" if k == "ointment" else _slot_label(k)) for k, ms in groups.items()}
    return tuple(Conflict(slot, labels[slot], tuple(ms)) for slot, ms in groups.items() if len(ms) > 1)


# ---------------------------------------------------------------------------
# shown, area
# ---------------------------------------------------------------------------

def fill_area(groups, must) -> tuple:
    """The proposals of one area: `groups` are the proposals per target in the order of their worth, `must` the ids of the
    player actions. A group goes in whole while there is room; one that does not fit is left out, except its members that
    are player actions. Room is kept for the player actions of later groups, so the limit never pushes one out."""
    chosen: list = []
    for k, group in enumerate(groups):
        later = sum(1 for g in groups[k + 1:] for p in g if p.id in must)
        if len(group) <= AREA_LIMIT - len(chosen) - later:
            chosen += group
        else:
            chosen += [p for p in group if p.id in must]
    return tuple(chosen)


def _resolve(base, worth: dict, p) -> tuple:
    """(area, worth in points) of a proposal: the area of its target; for a proposal of no target of the rating (a refund, a
    bind, more cooldown reduction) the area of the most valuable target it moves, else its kind decides."""
    res = base.results
    target = p.detail.get("target") or p.id
    for key in (target, target.split(":")[0]):
        if key in res:
            return res[key].area, worth.get(key, 0.0)
    moved = {k for k, after in v2(p).deficits_after.items()
             if k in res and after is not None and res[k].deficit is not None and res[k].deficit - after > 1e-9}
    moved |= {k for k in p.fixes if k in res}
    if moved:
        best = min(moved, key=lambda k: (-worth.get(k, 0.0), k))
        return res[best].area, worth.get(best, 0.0)
    return (SKILLS_AREA if p.kind in _SKILL_KINDS else p.category), 0.0


def _levers(a, base, fixes) -> tuple:
    res = base.results
    early = base.stage == Stage.EARLY
    width = RD.BAND[base.stage][1]
    worth = {} if early else {t: v * width for t, v in RD.impact(list(res.values()), base.settings).items()}
    must = {p.id for p in a.player_actions}
    groups: dict = {}
    for p in sorted([p for p in a.proposals if not p.detail.get("needs_rank")] + list(fixes), key=lambda p: p.sort_key()):
        groups.setdefault(p.detail.get("target") or p.id, []).append(p)
    by_area: dict = {}
    for ps in groups.values():
        pair = [next((p for p in ps if p.kind != "consumable"), None), next((p for p in ps if p.kind == "consumable"), None)]
        chosen = [p for p in pair if p is not None]
        chosen += [p for p in ps if p.id in must and p not in chosen]
        chosen.sort(key=lambda p: p.sort_key())
        area, value = _resolve(base, worth, chosen[0])
        by_area.setdefault(area, []).append((-value, chosen[0].sort_key(), tuple(chosen)))
    gates: dict = {}
    if not early:
        for g in base.gates:
            if not g.met:
                gates[GATE_AREA[g.id]] = gates.get(GATE_AREA[g.id], 0) + 1
    names = list(RD.AREAS) + sorted(set(by_area) - set(RD.AREAS))
    totals = {name: sum(worth.get(t, 0.0) for t, r in res.items() if r.area == name) for name in names}
    names.sort(key=lambda name: -totals[name])                  # a stable sort: a tie keeps the order of the areas
    used: set = set()
    levers = []
    for name in names:
        # One change (an augment on the amulet) can serve two targets and is then in the analysis twice. The areas are
        # filled one after another, and what an earlier area shows is taken out of the groups before the room is counted,
        # so the place it leaves is refilled and the later area keeps its own proposals.
        entries = sorted(by_area.get(name, ()), key=lambda e: e[:2])
        taken = set(used)
        groups = []
        for _v, _k, grp in entries:
            fresh = []
            for p in grp:
                if p.id not in taken:
                    taken.add(p.id)
                    fresh.append(p)
            if fresh:
                groups.append(tuple(fresh))
        members = fill_area(groups, must)
        used |= {p.id for p in members}
        if totals[name] > 0 or gates.get(name) or members:
            levers.append(Lever(name, totals[name], gates.get(name, 0), members))
    return tuple(levers)


# ---------------------------------------------------------------------------
# ghost, residual
# ---------------------------------------------------------------------------

def _combined(base, props) -> dict:
    """The comparison of the baseline with all the changes of `props` applied together."""
    ctx2, src2, snap2, extra2 = CF.apply_changes(base, [c for p in props for c in p.detail["apply"]])
    return CF.compare(base, ctx2, snap2, extra=extra2, src2=src2)


def _ghost(a, base, steps) -> tuple:
    """(ghost score, 1-based numbers of the steps it counts, whether the permanent ones open every open gate). The steps
    that count are those with a modelled change and no unrated loss (spec 3.4). The ghost is the score plus the combined
    effect of those steps (not the sum of their deltas), held inside the band and never below the score; consumables
    count while they run. None in EARLY and without such a step."""
    if base.stage == Stage.EARLY:
        return None, (), False
    nums = tuple(i for i, p in enumerate(steps, 1) if p.detail.get("apply") and not v2(p).loses_unrated)
    if not nums:
        return None, (), False
    counted = [steps[i - 1] for i in nums]
    cmp = _combined(base, counted)
    low, width = RD.BAND[base.stage]
    raw = RD.round_half_up(a.score["raw"] + cmp["score_delta"])
    ghost = max(a.score["score"], min(max(raw, low), low + width))
    permanent = [p for p in counted if p.kind != "consumable"]
    open_now = {g.id for g in base.gates if not g.met}
    if permanent:
        after = cmp if len(permanent) == len(counted) else _combined(base, permanent)
        return ghost, nums, bool(open_now) and open_now <= set(after["gates_opened"])
    return ghost, nums, False


def _residual(base, levers) -> tuple:
    """Per area: apply its shown permanent proposals together (not two on one slot part) and name every target of the area
    that was moved but is still a gap."""
    out = []
    units = {t["id"]: _UNIT.get(t["metric"], "") for t in base.data["targets"]}
    for lv in levers:
        taken: set = set()
        chosen = []
        for p in lv.members:
            sp = slot_of(p)
            if p.kind == "consumable" or not p.detail.get("apply") or (sp and SEL.slot_clash(sp, taken)):
                continue
            taken |= {sp} if sp else set()
            chosen.append(p)
        if not chosen:
            continue
        cmp = _combined(base, chosen)
        for tid, left in cmp["deficits_after"].items():
            r0, r1 = base.results[tid], cmp["results"].get(tid)
            if (r0.area == lv.name and r1 is not None and r1.status == "gap" and left
                    and r0.deficit is not None and r0.deficit - left >= MOVED_SHARE * abs(r0.target or 0.0) + 1e-9):
                out.append(Residual(lv.name, tid, r0.label, left, r1.now_text, r1.target_text,
                                    tuple(p.id for p in chosen), units.get(tid, "")))
    return tuple(out)


def build_for(a) -> Guide:
    """The guide of a finished analysis (lazy: only the view factory calls it)."""
    base = CF.baseline_of(a)
    fixes = tuple(FIX.bind_proposals(base, a.issues, a.actions) + FIX.refund_proposals(base, a.issues)
                  + FIX.swap_proposals(base, a.build_quality) + UPP.proposals(base, a.area_facts))
    levers = _levers(a, base, fixes)
    shown = tuple(p for lv in levers for p in lv.members)
    steps = steps_of(shown)
    ghost, ghost_steps, endgame_after = _ghost(a, base, steps)
    return Guide(fixes, shown, levers, steps, ghost, ghost_steps, endgame_after, conflicts_of(shown),
                 _residual(base, levers), unresolved(a.sources))
