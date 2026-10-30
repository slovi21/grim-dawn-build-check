"""Report v2: the proposal that keeps an enemy debuff - and with it the stacking kind of your resistance reduction -
up all the time with more cooldown reduction (spec 3.5 point 6).

It is for the guide only (`analysis/guide.build_for`), never part of `Analysis.proposals`: md, text and json output stay
as they were.

The candidate is a main damage type whose stacking kind of the resistance reduction exists but is "not kept up"
(`MainType.rr[i] < 1 <= rr_weak[i]`, `rr_why[i] == "not kept up"`). The debuff is read from the same rows
(`snap.enemy_debuffs`) that `combat.target_state.resistance_detail` sums for `rr_weak` and `rr_why`, so there is no second
detection. A debuff that is not on the hotbar, has no cooldown or no known duration is left out: no cooldown reduction
fixes that.

The missing cooldown reduction goes into the counterfactual as a synthetic permanent source. The area facts are computed
again from it and replace the target inputs of the baseline (`rr_kinds`), so the resistance reduction is re-rated for real
- the one change `compare` cannot judge from the baseline's own inputs (decision E3). Nothing is lost: more cooldown
reduction takes no damage and no resistance reduction away, so `loses_unrated` stays False."""
from __future__ import annotations

import math

from ...combat import target_state as TS
from ...mechanics.policy import Confidence, rule
from ...mechanics.uptime import KEPT_UP
from . import counterfactual as CF
from .model import Proposal

MAX_EXTRA_CDR = 25.0           # % - more than this is no sensible gear change (HEURISTIC, see extra_cdr)
MIN_DEBUFF_REDUCTION = 1.0     # a debuff that takes less than a point off the resistance is no kind of reduction


def needed_cdr(duration: float, cooldown: float, kept_up: float = KEPT_UP) -> float:
    """The cooldown reduction in % at which `duration / (cooldown x (1 - CDR))` reaches `kept_up` (`cooldown` is the
    base cooldown, without any reduction); 0 when the effect is kept up without it."""
    return max(0.0, 100.0 * (1.0 - duration / (kept_up * cooldown)))


@rule("uptime_extra_cdr", Confidence.HEURISTIC,
      "the whole percent of cooldown reduction on top of the sheet value that keeps a debuff up (duration / effective "
      "cooldown >= 95 %, X-018), 1 to 25 %; a larger gap is not a gear change any more (decision of the plan, 26.10.2026)")
def extra_cdr(needed: float, cdr_now: float) -> int | None:
    """`needed - cdr_now` rounded up to a whole percent (the epsilon keeps an exact 5.0 at 5); None when nothing is
    missing or when it is more than MAX_EXTRA_CDR."""
    extra = math.ceil(needed - cdr_now - 1e-9)
    return extra if 1 <= extra <= MAX_EXTRA_CDR else None


def _candidates(base, res: str, cdr_now: float, threshold: float) -> list:
    """[(reduction of `res`, extra CDR, debuff row)] of the debuffs that are not kept up, sit on the hotbar, have a duration
    and a cooldown, take at least a point off `res` and need no more than the cap."""
    out = []
    for d in base.snap.enemy_debuffs:
        value, duration, cooldown = TS.debuff_reduction(d, res), d.get("duration"), d.get("cooldown")
        if d.get("kept_up") or not d.get("on_hotbar") or not duration or not cooldown or value < MIN_DEBUFF_REDUCTION:
            continue
        base_cooldown = cooldown / (1.0 - cdr_now / 100.0)             # the row holds the cooldown with the CDR in force
        extra = extra_cdr(needed_cdr(duration, base_cooldown, threshold), cdr_now)
        if extra is not None:
            out.append((value, extra, d))
    return out


def proposals(base, facts) -> list[Proposal]:
    """One proposal per debuff that keeps a stacking kind of a main damage type from being permanent: the strongest
    debuff of the type (the lowest extra CDR, then the label, at a tie) that the cap allows. Main types that share the
    resistance (Fire and Burn) share the kind, and a debuff two types share is proposed once. The counterfactual re-rates
    `rr_main` from the area facts of the changed character."""
    if facts is None:
        return []
    cdr_now = min(max(float(base.snap.sheet.fields.get("skillCooldownReduction", 0.0) or 0.0), 0.0), 100.0)
    threshold = base.ctx.ruleset.get("kept_up_threshold", KEPT_UP)
    out, seen, kinds = [], set(), set()
    for m in getattr(facts, "main_types", ()) or ():
        for i, (now, weak, why) in enumerate(zip(m.rr, m.rr_weak, m.rr_why)):
            if not (now < 1.0 <= weak and why == "not kept up") or (m.res, i) in kinds:
                continue
            found = sorted((c for c in _candidates(base, m.res, cdr_now, threshold) if c[2]["record"] not in seen),
                           key=lambda c: (-c[0], c[1], c[2]["label"]))
            if not found:
                continue
            value, extra, d = found[0]
            seen.add(d["record"])
            # the debuff lowers every resistance type it names: the same kind of those types is up with it, a weaker debuff
            # for them would be a second step towards what the first one already gives
            kinds |= {(res, i) for res in TS.RES_TYPES if TS.debuff_reduction(d, res) >= MIN_DEBUFF_REDUCTION}
            out.append(_proposal(base, m, i, value, extra, d, cdr_now, threshold))
    return out


def _proposal(base, m, i: int, value: float, extra: int, d: dict, cdr_now: float, threshold: float) -> Proposal:
    change = (("cdr", extra, d["record"]),)
    ctx2, src2, snap2, extra2 = CF.apply_changes(base, change)
    cmp = CF.compare(base, ctx2, snap2, extra=extra2, src2=src2)
    label, delta = d["label"], cmp["score_delta"]
    medium = (delta is not None and delta >= 1) or "rr_main" in cmp["fixes"]
    return Proposal(
        f"uptime:{d['record']}", "uptime", "Offense", f"Keep {label} up", f"Keep {label} up all the time",
        cmp["gains"], cmp["costs"], cmp["fixes"], cmp["breaks"], "MEDIUM" if medium else "LOW", "high",
        explain=(f"{label} lasts {d['duration']:g} s and recharges in {d['cooldown']:g} s: it is up "
                 f"{d['uptime']:.0%} of the time",
                 f"about {extra}% more cooldown reduction from gear, devotions or skills keeps it up "
                 f"({threshold:.0%} or more)"),
        score=cmp["score"],
        detail={**CF.annotate(cmp), "target": f"rr_main:uptime:{label}", "debuff": label,
                "uptime_now": float(d["uptime"]), "cdr": cdr_now,   # cdr: the CDR in force now, the extra one is in `apply`
                "type": m.name, "kind_index": i, "value": value,
                "duration": float(d["duration"]), "cooldown": float(d["cooldown"]), "tier": "Farm",
                "apply": change})
