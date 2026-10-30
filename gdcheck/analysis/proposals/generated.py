"""Skill points, devotions, attributes and rerolls (X-019, X-020) - candidates from the
generators (analysis/generators, ported from .35 in Task 7), every proposal recalculated by
the counterfactual engine. (Until .68 a module of another name.)"""
from __future__ import annotations

from ...data.text import short
from .. import generators as GEN
from . import counterfactual as CF
from .model import Proposal


def stage_targets(results: dict) -> dict:
    """The stage targets the candidate generators rate against, from the target results {id: TargetResult}
    (public since .72: the build check in analysis/build_quality.py asks for them without a counterfactual
    baseline)."""
    pick = lambda pre, d: next((x.target for i, x in results.items() if i.startswith(pre) and x.target), d)  # noqa
    return {"physical": pick("physical", 30), "da": pick("da_", 3000), "health": pick("health", 20000),
            "leech": pick("leech", 50.0)}


def _stage_targets(base) -> dict:
    return stage_targets(base.results)


def _lost_powers(base, sb: dict, removed) -> list[tuple]:
    """Bound Celestial Powers of removed constellations: (power, bound skill, trigger). They
    are no character value, so the counterfactual cannot see them - named as a cost."""
    out = []
    procs = {p.skill_record: p for p in base.sources.procs}
    for k in removed:
        power = (sb.get(k) or {}).get("power")
        if not power or power not in base.ctx.character.bindings:
            continue
        bound = base.ctx.character.bindings[power][0]
        p = procs.get(power)
        out.append((base.ctx.index.display(power) or short(power), base.ctx.index.display(bound) or short(bound),
                    p.trigger if p else ""))
    return out


def proposals(base, score, generator_override=None) -> list[Proposal]:
    """generator_override: {section: fn(inp, profile)} replaces a candidate generator (generators.generate)."""
    out = []
    try:
        raw = GEN.run(base.ctx, base.snap, _stage_targets(base), base.melee, override=generator_override)
    except Exception as ex:                                      # noqa: BLE001
        base.ctx.extra["generator_error"] = f"{type(ex).__name__}: {ex}"
        return out
    base.ctx.extra["generator_moves"] = raw
    failed = GEN.failures(raw)                  # one generator failed: E6 names it as a model limit (.68)
    if failed:
        base.ctx.extra["generator_failures"] = failed
    try:
        base.ctx.extra["constellation_names"] = {k: v.get("name", k) for k, v in GEN.constellations(base.ctx.index).items()}
    except Exception:                                            # noqa: BLE001
        base.ctx.extra["constellation_names"] = {}
    names = {}
    for rec in base.ctx.character.skills:
        names[base.ctx.index.display(rec) or short(rec)] = rec
    # skill points
    sk = raw.get("skills") or {}
    moves = sk.get("merged") or []
    if moves:
        change = {}
        pts = dict(base.ctx.character.skills)
        for m in moves[:4]:
            a, b = names.get(m["from"]), names.get(m["to"])
            if a and b:
                pts[a] = pts.get(a, 0) - m["points"]
                pts[b] = pts.get(b, 0) + m["points"]
                change[a], change[b] = pts[a], pts[b]
        if change:
            ctx2, src2, snap2 = CF.snapshot_for(base, CF.with_skills(base.ctx.character, change))
            cmp = CF.compare(base, ctx2, snap2, src2=src2)
            text = "; ".join(f"{m['points']} pt {m['from']} -> {m['to']}" for m in moves[:4])
            out.append(Proposal("skills:moves", "skills", "Skills", "Move skill points", text,
                                cmp["gains"], cmp["costs"], cmp["fixes"], cmp["breaks"],
                                "LOW" if not cmp["fixes"] else "MEDIUM", "low",
                                explain=("respec at the Spirit Guide", "skills without a damage model "
                                         "(attacks, procs) are valued by their own flat damage only"),
                                score=cmp["score"], detail={"target": "skills", "moves": moves[:4],
                                                          **CF.annotate(cmp),
                                                          "apply": (("skills", tuple(sorted(change.items()))),)}))
    # devotions: plan A (and B)
    dv = raw.get("devotions") or {}
    sb = None
    for key, label in (("plan", "A"), ("plan_b", "B")):
        plan = dv.get(key)
        if not plan or not (plan.get("out") or plan.get("in")):
            continue
        sb = sb or GEN.constellations(base.ctx.index)
        change = {}
        for k in plan.get("out") or []:
            for star in (sb.get(k) or {}).get("stars", []):
                change[star] = 0
        for k in plan.get("in") or []:
            for star in (sb.get(k) or {}).get("stars", []):
                change[star] = 1
        ctx2, src2, snap2 = CF.snapshot_for(base, CF.with_skills(base.ctx.character, change))
        cmp = CF.compare(base, ctx2, snap2, src2=src2)
        nm = lambda k: (sb.get(k) or {}).get("name", k)  # noqa: E731
        text = f"Devotion plan {label}: out {', '.join(nm(k) for k in plan.get('out') or []) or '-'}; " \
               f"in {', '.join(nm(k) for k in plan.get('in') or []) or '-'}"
        lost = _lost_powers(base, sb, plan.get("out") or [])
        breaker = any(trig == "LowHealth" for _n, _b, trig in lost)
        out.append(Proposal(f"devotion:plan{label}", "devotion", "Devotions", f"Devotion plan {label}", text,
                            cmp["gains"], cmp["costs"], cmp["fixes"], cmp["breaks"],
                            "MEDIUM" if cmp["fixes"] and not cmp["breaks"] and not breaker else "LOW", "medium",
                            explain=("full reset of the devotions at a shrine (costs Aether Crystals)",
                                     "build order in the detail view (D7)"),
                            score=cmp["score"], detail={"target": f"devotion{label}", "plan": plan,
                                                        "lost_powers": tuple(lost), **CF.annotate(cmp),
                                                        "apply": (("skills", tuple(sorted(change.items()))),)}))
    # attributes
    at = raw.get("attributes") or {}
    if at.get("worth_it") and at.get("best") and at.get("best") != at.get("current"):
        base_attr = dict(base.ctx.character.attributes_base)
        per = base.ctx.attribute_constants.get("per_point", 8.0)
        for k in ("physique", "cunning", "spirit"):
            base_attr[k] = (base_attr.get(k) or 0) + (at["best"][k] - at["current"][k]) * per
        ctx2, src2, snap2 = CF.snapshot_for(base, CF.with_attributes(base.ctx.character, base_attr))
        cmp = CF.compare(base, ctx2, snap2, src2=src2)
        b = at["best"]
        out.append(Proposal("attributes:best", "attributes", "Attributes", "Attribute points",
                            f"spread {b['physique']} Physique / {b['cunning']} Cunning / {b['spirit']} Spirit",
                            cmp["gains"], cmp["costs"], cmp["fixes"], cmp["breaks"], "LOW", "medium",
                            explain=("needs a Tonic of Reshaping", "check Required Cunning/Spirit of worn items first"),
                            score=cmp["score"], detail={"target": "attributes", **CF.annotate(cmp),
                                                        "apply": (("attributes", tuple(sorted(base_attr.items()))),)}))
    out += reroll_proposals(base, raw)
    return out


# ---------------------------------------------------------------------------
# rerolls (Inventor, Alteration)
# ---------------------------------------------------------------------------
# Until .68 this branch compared the verdict with "REROLL" while the generator writes "reroll"
# (generators/rerolls.py), so no reroll proposal ever reached E3/D4. Fixed; the proposals are
# EXPERT ONLY (detail "expert_only", never a 'Do this next' action - select.player_actions):
#   - the Inventor's Alteration needs the Fangs of Asterkarn questline and Ashes of Awakening,
#     which drop mostly in Ascendant Mode (Crate, Grim Misadventure #190) - nothing for levelling:
#     no proposal in EARLY (the per-affix verdicts stay in D8);
#   - the recalculated gain is that of the BEST example roll; a real roll is random (on average
#     1/share rolls until a better one, the cost rises with every roll), so the gain is optimistic;
#   - the recalculation sees no damage from lost or won "+skills" / "+% damage" lines, and the
#     generator cannot value affixes without stats in the index (pet bonuses) - those are skipped;
#   - like every gear proposal it must pass counterfactual.worth_it (no break, score >= NET_GATE).
REROLL_PARTS = {"prefix": "prefix_name", "suffix": "suffix_name", "ascended": "ascended_name"}


def _has_stats(rec) -> bool:
    """True when the affix record carries at least one numeric bonus the value scale can see
    (pet affixes like "Subjugator's" / "of Dire Fangs" are empty in the index -> valued 0)."""
    return any(isinstance(v, (int, float)) and not isinstance(v, bool) and v
               and k not in ("levelRequirement",) and not k.startswith("_")
               for k, v in (rec or {}).items())


def reroll_skip_reason(entry: dict, stage, index) -> str | None:
    """Why a generator reroll entry gives no proposal (None = it is a candidate)."""
    if str(entry.get("verdict") or "").lower() != "reroll" or not entry.get("candidates"):
        return "verdict"
    if entry.get("kind") not in REROLL_PARTS:
        return "kind"
    if getattr(stage, "value", str(stage)) == "EARLY":
        return "early"
    if not entry.get("exact"):
        return "estimated pool"
    if index is not None and not _has_stats(index.record(entry.get("record") or "")):
        return "affix not modelled"
    return None


def reroll_text(r: dict, best: dict) -> str:
    share = r.get("better_share") or 0.0
    rolls = f", ~{1 / share:.0f} rolls" if share > 0 else ""
    return (f"Inventor: reroll {r['kind']} '{r['affix']}' on {r['item']} - {share * 100:.0f}% of rolls "
            f"better{rolls}, e.g. {best['name']}")


def reroll_proposals(base, raw) -> list[Proposal]:
    out = []
    rr = raw.get("rerolls") if isinstance(raw, dict) else None
    for r in rr if isinstance(rr, list) else []:
        if reroll_skip_reason(r, base.stage, base.ctx.index):
            continue
        best = r["candidates"][0]
        part = REROLL_PARTS[r["kind"]]
        ctx2, src2, snap2 = CF.snapshot_for(base, CF.with_item_part(base.ctx.character, r["slot"], part,
                                                                 best["record"]))
        idx, worn = base.ctx.index, base.ctx.character.worn_by_slot().get(r["slot"])
        lost_rank = CF.rank_bonus_lost(CF.rank_bonuses(idx, [(worn.item if worn else {}).get(part)]),
                                       CF.rank_bonuses(idx, [best["record"]]))
        cmp = CF.compare(base, ctx2, snap2, src2=src2, unrated=lost_rank)
        if not CF.worth_it(base, cmp):
            continue
        out.append(Proposal(f"reroll:{r['slot']}:{part}", "reroll", "Gear", f"Reroll {r['affix']} ({r['slot']})",
                            reroll_text(r, best),
                            cmp["gains"], cmp["costs"], cmp["fixes"], cmp["breaks"], "LOW", "high",
                            source="Inventor (Alteration, after the Asterkarn questline)",
                            explain=("expert only: gain of the best example roll, real rolls are random",
                                     "cost: Ashes of Awakening + Iron Bits, rising per roll; item becomes "
                                     "untradeable",
                                     f"pool of {r.get('pool')} affixes from the loot tables; lost +skills/"
                                     "damage not recalculated"),
                            score=cmp["score"], detail={"target": f"reroll:{r['slot']}:{part}",
                                                        "slot_part": f"{r['slot']}:{part}",
                                                        "reroll_kind": r["kind"], "affix": r["affix"], "slot": r["slot"],
                                                        "expert_only": True, **CF.annotate(cmp),
                                                        "apply": (("item_part", r["slot"], part, best["record"]),)}))
    return out
