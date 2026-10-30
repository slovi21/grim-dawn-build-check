"""Report v2: the proposal kinds that fix something without a gear change - bind an unbound celestial power, refund a
skill that lowers your own values, swap a devotion (spec 3.5 point 5).

They are for the guide only (`analysis/guide.build_for`), never part of `Analysis.proposals`: md, text and json output
stay as they were.

  bind    D1 "power is not bound": the main attack (else a free action) takes the power. Costs nothing and changes no
          character value, so there is no counterfactual (`gain`, `cost` and `fixes` stay empty, the v2 keys of `detail`
          are None / empty); the power's own damage is not modelled.
  refund  C1 of a skill: the skill (and its modifiers) goes back to 0 points and the character is rated again like any
          skill change. The points taken away are not re-rated (decision E3), so `loses_unrated` is True by the
          conservative rule - the cost line names what the skill gave instead.
  swap    the best 1-for-1 devotion swap of the build check. The value points rank candidates and are no measured
          gain, so there is no counterfactual either (the v2 keys of `detail` are None / empty, like bind).
"""
from __future__ import annotations

from ...data.text import short
from ..archetype import main_action
from ..rules.findings import points
from . import counterfactual as CF
from .model import Proposal


def _name(index, record: str) -> str:
    return index.display(record) or short(record)


def bind_proposals(base, issues, actions) -> list[Proposal]:
    """One proposal per D1 issue (`subject` = the power record). The host is the main attack of the build
    (`archetype.main_action`, never the plain weapon attack) when it holds no power yet, else the first action that is
    not the plain weapon attack and holds no power. A host taken by an earlier proposal is skipped. No host, no
    proposal."""
    index = base.ctx.index
    taken = {target for target, _ in base.ctx.character.bindings.values()}
    out = []
    for issue in issues:
        if issue.code != "D1" or not issue.subject:
            continue
        host = main_action(actions)
        if host is None or host.action_id in taken:
            host = next((x for x in actions if "defaultweaponattack" not in x.action_id
                         and x.action_id not in taken), None)
        if host is None:
            break
        taken.add(host.action_id)
        power, host_name = _name(index, issue.subject), host.label or _name(index, host.action_id)
        out.append(Proposal(
            f"bind:{issue.subject}", "bind", "Devotions", f"Bind {power}", f"Bind {power} to {host_name}",
            (), (), (), (), "MEDIUM", "low",
            explain=("devotion screen: right-click the power and pick the skill",
                     "the power's own damage is not modelled"),
            detail={"target": f"bind:{issue.subject}", "power": power, "host": host_name, "tier": "Now",
                    "score_delta": None, "gates_opened": (), "deficits_after": {},
                    "loses_unrated": False, "power_record": issue.subject, "host_record": host.action_id}))
    return out


def _children(base, record: str) -> list[str]:
    """Modifiers and transmuters of a skill that carry points."""
    skills = base.ctx.character.skills
    return sorted(s.record for s in base.sources.skills if s.parent == record and skills.get(s.record, 0) > 0)


def refund_proposals(base, issues) -> list[Proposal]:
    """One proposal per C1 issue of a skill the character has points in (`subject`; a self-penalty of an item has none).
    The skill and its modifiers go to 0 points; a refund that makes a target worse is dropped."""
    character, skills = base.ctx.character, base.ctx.character.skills
    out, seen = [], set()
    for issue in issues:
        subject = issue.subject
        if not issue.code.startswith("C1") or skills.get(subject, 0) <= 0 or subject in seen:
            continue
        seen.add(subject)
        records = [subject] + _children(base, subject)
        ctx2, src2, snap2 = CF.snapshot_for(base, CF.with_skills(character, {r: 0 for r in records}))
        cmp = CF.compare(base, ctx2, snap2, src2=src2)
        if cmp["breaks"]:
            continue
        name, spent = _name(base.ctx.index, subject), sum(skills[r] for r in records)
        change = (f"Refund the point in {name}" if spent == 1 else f"Refund the {spent} points in {name}")
        out.append(Proposal(
            f"refund:{subject}", "refund", "Skills", f"Refund {name}", change,
            cmp["gains"], cmp["costs"], cmp["fixes"], (), "HIGH", "low",
            explain=("take the points back at the Spirit Guide",
                     f"{name} lowers your own values; what it gives you is not recalculated"),
            score=cmp["score"],
            detail={**CF.annotate(cmp), "target": f"refund:{subject}", "points": spent,
                    "lost": (f"what {name} gives you",), "tier": "Respec",
                    "apply": (("skills", tuple((r, 0) for r in records)),)}))
    return out


def swap_proposals(base, quality) -> list[Proposal]:
    """The best devotion swap of the build check (`BuildQuality.swap`), if there is one. Affinity, resistance caps and
    bound powers are kept by the generator; the value points only rank, so nothing is recalculated."""
    swap = None if quality is None or quality.error else quality.swap
    if not swap:
        return []
    out_, in_ = tuple(swap["out"]), tuple(swap["in"])
    return [Proposal(
        "devotion_swap", "devotion_swap", "Devotions", "Swap devotions",
        f"Swap {' + '.join(out_)} for {' + '.join(in_)}", (), (), (), (), "LOW", "medium",
        explain=("respec the constellations at the Spirit Guide (costs Aether Crystals for the refund)",
                 f"{points(swap['points_out'])} out, {points(swap['points_in'])} in; affinity, resistance caps and "
                 "bound powers stay",
                 "value points rank candidates; they are no measured gain"),
        score=float(swap["value"]),
        detail={"target": "devotion_swap", "out": out_, "in": in_, "value_points": float(swap["value"]),
                "tier": "Respec", "score_delta": None, "gates_opened": (), "deficits_after": {},
                "loses_unrated": False})]
