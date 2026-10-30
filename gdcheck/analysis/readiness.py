"""Build Readiness (one score, Anforderungsheft V2 9.3) and the eight player areas with a value 0-100 (.70, .72)."""
from __future__ import annotations

import dataclasses
import math
from fractions import Fraction

from ..domain.enums import Stage
from . import stage as STG
from .area_facts import kinds_status

BAND = {Stage.EARLY: (0, 33), Stage.MID: (34, 32), Stage.ENDGAME: (67, 33)}
GROUPS = ("core", "cc_sustain", "offense", "scenario")
# K4 (user 04.10.2026): the build check on top; Resistance Reduction is its own row since .72
AREAS = ("Skills & Devotions", "Health", "Resistances", "Survival", "Sustain", "Crowd Control", "Damage",
         "Resistance Reduction")
HEALTH_BAR_AT = 1.5         # the Health bar is full at 1.5 times the recommended Health (.72, plan A.3)
MARGIN_SHARE = 0.2          # the share of the band the reserve can fill
MARGIN_AT = 1.5             # a scaling target's reserve is full at 150 % of the target
OVERCAP_FULL = 15.0         # a resistance's reserve is full 15 points above its cap


def settings(data: dict | None) -> dict:
    """The score block of targets.json ({"margin_share", "margin_at", "overcap_full"}); defaults without it."""
    sc = (data or {}).get("score") or {}
    return {"margin_share": float(sc.get("margin_share", MARGIN_SHARE)), "margin_at": float(sc.get("margin_at", MARGIN_AT)),
            "overcap_full": float(sc.get("overcap_full", OVERCAP_FULL))}


def _rated(results):
    return [r for r in results if r.status != "info" and credit(r) is not None]


def credit(r) -> float | None:
    """How far a target is met, 0..1 (the 'f' of the score): see fulfilment; None for an info result."""
    return fulfilment(r)


def margin(r, s: dict | None = None) -> float | None:
    """The reserve of a target above its threshold, 0..1 (the 'm' of the score); None for a target without one
    and for an info result (N1).
    - A scaling target: clip((q - 1) / (margin_at - 1)) with q = now / target (target / now where lower is better),
      full at margin_at times the target (default 1.5; Health and the dummy time carry `margin_at` 3 in targets.json, I2),
      0 below it.
    - A resistance: its mean over-cap (points above the cap, each type up to 15) / 15.
    - A floor target without a reserve (freeze, stun, leech, absorption, the scenarios, the resistance reduction) is None,
      not m = 0: plan N1 says "m = 0" for them, N2 (which wins) averages M over the targets that carry a reserve only,
      so a floor target never pulls M down (review m3)."""
    s = s or settings(None)
    now, target = getattr(r, "now", None), getattr(r, "target", None)
    if not getattr(r, "margin", False) or r.status == "info" or not isinstance(now, (int, float)):
        return None
    if getattr(r, "kind", "floor") == "scaling":
        if not isinstance(target, (int, float)) or target <= 0:
            return None
        if getattr(r, "lower_is_better", False):
            q = math.inf if now <= 0 else target / now
        else:
            q = now / target
        at = getattr(r, "margin_at", None) or s["margin_at"]
        return min(max((q - 1) / (at - 1), 0.0), 1.0)
    over = getattr(r, "overcap", None)
    return None if over is None else min(max(over / s["overcap_full"], 0.0), 1.0)


def group_shares(results) -> dict[str, float]:
    """Group -> weighted share met (the 'R' of the group), over the targets that are rated."""
    out = {}
    for g in GROUPS:
        rs = [r for r in _rated(results) if r.group == g]
        if rs:
            out[g] = sum(r.weight * credit(r) for r in rs) / sum(r.weight for r in rs)
    return out


def weight_shares(results) -> dict[str, float]:
    """Group -> its share of the weight of all rated targets (E6: 'Core 47% of the score')."""
    rs = _rated(results)
    total = sum(r.weight for r in rs)
    return {g: sum(r.weight for r in rs if r.group == g) / total
            for g in GROUPS if total and any(r.group == g for r in rs)}


def round_half_up(x: float) -> int:
    """The one rounding of the score (every stage): half up, the way a player rounds - Python's round() takes 50.5 to 50 (half
    to even). Exact on the float, so the value just below x.5 stays below."""
    return math.floor(Fraction(x) + Fraction(1, 2))


def _gate_of(r) -> float:
    """What a gate target (`gate: true`) counts in the gate G: `gate_credit` where the target has one (res_own_cap: the
    resistances against the game rule 80, not the own cap); else met in its tolerance band (`tight`) counts as met, the
    same definition as rule 9.2 (not open), a real gap with its credit."""
    g = getattr(r, "gate_credit", None)
    return g if g is not None else (1.0 if r.status in ("ok", "tight") else credit(r))


def _parts(rs, s: dict) -> tuple[float, float, float]:
    """(R, M, G) of the rated results: the weighted share met, the weighted reserve over the margin targets (a floor target
    without a reserve is left out, N2) and the gate over the gate targets (no gate target rated -> 1)."""
    w = sum(r.weight for r in rs)
    met = sum(r.weight * credit(r) for r in rs) / w if w else 1.0
    mr = [(r, margin(r, s)) for r in rs if margin(r, s) is not None]
    mw = sum(r.weight for r, _m in mr)
    res = sum(r.weight * m for r, m in mr) / mw if mw else 0.0
    gt = [r for r in rs if getattr(r, "gate", False)]
    gw = sum(r.weight for r in gt)
    gate = sum(r.weight * _gate_of(r) for r in gt) / gw if gw else 1.0
    return met, res, gate


def _term(met: float, res: float, gate: float, s: dict) -> float:
    """The unrounded band term G x ((1 - share) x R + share x M) - the one place of the formula; the score is
    low + width x term, the impact of a target is the difference of two terms."""
    return gate * ((1 - s["margin_share"]) * met + s["margin_share"] * res)


def impact(results, s: dict | None = None) -> dict[str, float]:
    """Target id -> what it costs of the score: the band term with the target met minus the term now (counterfactual,
    not rounded; times the width it is the points), only for a target that is not fully met. The reserve of the target
    stays as measured. The order of the top issues follows it (N4, 9.2): a gate target costs more than its weight
    share because it also lifts the gate."""
    s = s or settings(None)
    rs = _rated(results)
    if not rs:
        return {}
    now = _term(*_parts(rs, s), s)
    out = {}
    for i, r in enumerate(rs):
        if credit(r) < 1 - 1e-9:
            met_r = dataclasses.replace(r, status="ok", credit=1.0, gate_credit=None)
            out[r.id] = _term(*_parts(rs[:i] + [met_r] + rs[i + 1:], s), s) - now
    return out


def score_delta(now_results, new_results, stage: Stage, s: dict | None = None) -> float | None:
    """What a counterfactual adds to the unrounded score: the width of the band times the difference of the two band terms
    (`_term`, the one place of the formula), each term held to 0..1 so the new score stays inside the band. Both result
    lists are rated the same way as the score (`_rated`). None in EARLY: its score is level progress and the Ultimate
    resistances, not the band term."""
    if stage == Stage.EARLY:
        return None
    s = s or settings(None)
    width = BAND[stage][1]

    def term(results):
        return min(max(_term(*_parts(_rated(results), s), s), 0.0), 1.0)
    return width * (term(new_results) - term(now_results))


def score(ctx, snap, stage: Stage, results, s: dict | None = None) -> dict:
    """The score (N2): R = weighted share met over all rated targets, M = weighted reserve over the margin
    targets only (`margin: true` in targets.json; a floor target without a reserve is left out, not counted as 0 - N2 over N1), R_floor = weighted share met over the floor targets;
    Score = low + width x G x ((1 - share) x R + share x M) - the score counts only as far as the gate targets
    are met (I1, review 05.10.2026). G = weighted share met over the gate targets (`gate: true`: resistances, Armor
    Absorption, resistance reduction); a gate target in its tolerance band (`tight`) counts as met there, the same
    definition as rule 9.2 (not open), a real gap counts with its credit; the own-cap resistances are measured against
    the game rule 80 (`gate_credit`), the own cap refines R only. No gate target rated -> G = 1.
    `floor` stays in the result for the readers of the old figure. EARLY: level progress and Ultimate resistances
    (unchanged since 9.3)."""
    s = s or settings(None)
    rs = _rated(results)
    w = sum(r.weight for r in rs)
    met, res, gate = _parts(rs, s)
    fl = [r for r in rs if getattr(r, "kind", "floor") == "floor"]
    fw = sum(r.weight for r in fl)
    floor = sum(r.weight * credit(r) for r in fl) / fw if fw else 1.0
    raw = None
    if stage == Stage.EARLY:
        ur = STG.ultimate_resists(ctx, snap)
        share = sum(1 for r in ur.values() if round(r["value"]) >= r["target"]) / len(ur)
        total = round_half_up(25 * min(ctx.character.level / STG.EARLY_BELOW, 1) + 8 * share)
    else:
        low, width = BAND[stage]
        raw = low + width * _term(met, res, gate, s)
        total = round_half_up(raw)
    return {"score": max(0, min(100, total)), "stage": stage.value,
            "parts": {g: int(math.floor(100 * v + 1e-9)) for g, v in group_shares(results).items()},
            "ok": sum(r.status == "ok" for r in results), "tight": sum(r.status == "tight" for r in results),
            "gap": sum(r.status == "gap" for r in results),
            "gap_fact": sum(r.status == "gap" and r.basis == "fact" for r in results),
            "targets": len(results), "met": met, "margin": res, "floor": floor, "gate": gate, "raw": raw,
            "open": any(r.status in ("gap", "tight") for r in results), "impact": impact(results),
            "weight_rated": w, "weight_all": sum(r.weight for r in results)}


def bands() -> list[list]:
    """[[name, low, high], ...] of the score bands (EARLY 0-33, MID 34-66, ENDGAME 67-100) - the model
    carries them, the renderers draw the readiness bar from them (F-008)."""
    return [[st.value, low, low + width] for st, (low, width) in BAND.items()]


def fulfilment(r) -> float | None:
    """How far a target is met, 0..1 (plan .70 A.1b; .72: a target with its own credit uses it): actual / target capped at 1; a maximum (lower is
    better) is met at or below it, else target / actual; a target of 0 or less is met at or above it, else
    it falls by 1 per 100 points short. None for an info result or values that are no numbers (a target with a
    credit of its own - e.g. 0 for "no damage" - needs no value)."""
    now, target = getattr(r, "now", None), getattr(r, "target", None)
    nums = (int, float)
    if r.status == "info":
        return None
    if getattr(r, "credit", None) is not None:             # .72: the resistances (worst of the nine), a buffer (K9), no damage (I3)
        return min(max(r.credit, 0.0), 1.0)
    if not isinstance(now, nums) or not isinstance(target, nums):
        return None
    if getattr(r, "lower_is_better", False):
        return 1.0 if now <= target else (target / now if target > 0 else 0.0)
    if target > 0:
        return min(max(now / target, 0.0), 1.0)
    return 1.0 if now >= target else min(max(1 - (target - now) / 100, 0.0), 1.0)


STATUS = {"gap": "GAP", "tight": "RISK", "ok": "OK", "info": "INFO"}
COUNT_TARGETS = ("res_ultimate", "res_own_cap")      # replaced by the resistance share in the Resistances value
COUNT_WEIGHT = 1.0                                   # res_* weigh 3 in T4 (I1; 2 until the review of 04.10.2026): the factor keeps the nine resistances at three times the leech resistance in the Resistances value (plan 72 N3, review M6)
_ORDER = {"gap": 0, "tight": 1, "ok": 2, "info": 3}


def _floor_value(fractions, weights=None) -> int | None:
    """Weighted mean of fractions as a whole number 0-100, rounded DOWN (B7): 100 only when all are met."""
    if not fractions:
        return None
    weights = weights or [1.0] * len(fractions)
    return int(math.floor(100 * sum(w * f for w, f in zip(weights, fractions)) / sum(weights) + 1e-9))


def _target_area(name, rs, facts) -> dict:
    rs = sorted(rs, key=lambda r: (_ORDER[r.status], r.basis != "fact", r.id))
    if not rs:
        return {"status": "INFO", "worst": None, "results": [], "value": None}
    share = getattr(facts, "resist_share", None) if name == "Resistances" else None
    counted = name == "Resistances"
    fr = [(share if share is not None and r.id in COUNT_TARGETS else fulfilment(r),
           r.weight * COUNT_WEIGHT if counted and r.id in COUNT_TARGETS else r.weight) for r in rs]
    fr = [(f, w) for f, w in fr if f is not None]
    return {"status": STATUS[rs[0].status], "worst": rs[0], "results": rs,
            "value": _floor_value([f for f, _w in fr], [w for _f, w in fr])}


def _sustain(facts) -> dict:
    s = getattr(facts, "sustain", None)
    out = {"status": "INFO", "worst": None, "results": [], "value": None}
    if s is None:
        return out
    out["sustain"] = s
    if not facts.early:                                    # EARLY: no sustain target yet
        c = s.count()
        out["status"], out["value"] = "OK" if c >= 2 else "RISK" if c == 1 else "GAP", int(math.floor(100 * c / 3))
    return out


def _health(rs) -> dict:
    """Health: the bar shows the surplus - full at 1.5 times the recommended Health (A.3); the status stays the
    target's (a gap never shows OK)."""
    out = _target_area("Health", rs, None)
    w = out["worst"]
    if w is not None and isinstance(w.now, (int, float)) and isinstance(w.target, (int, float)) and w.target > 0:
        out["value"] = int(math.floor(100 * min(1.0, max(w.now, 0.0) / (HEALTH_BAR_AT * w.target)) + 1e-9))
    return out


def _damage(rs, issues, facts) -> dict:
    """Damage: the Training Dummy time and the hit chance on bosses (weighted mean of their fulfilment, the status
    of the worst). A conversion issue (A3/A5) makes OK/INFO a RISK; a pet build and EARLY have no value."""
    out = _target_area("Damage", rs, None)
    pet = bool(getattr(facts, "pet", False))
    if facts is not None:
        out["main"] = tuple(getattr(facts, "main_types", ()))
    if pet or getattr(facts, "early", False):             # E4: a pet build's damage is not on the sheet
        out["status"], out["value"] = "INFO", None
    if issues and not pet and out["status"] in ("OK", "INFO"):
        conv = [i for i in issues if i.code in ("A3", "A5") and i.severity in ("ERROR", "WARNING")]
        if conv:
            out["status"], out["issue"] = "RISK", conv[0]
    return out


def _resistance_reduction(rs, facts) -> dict:
    """Resistance Reduction: the kinds that work on the main types (sum of the kinds, 1 permanent, 1/2 weak: OK
    from 2, RISK from 1/2) and the target rr_main (the three kinds weighted); the worst decides, the value is
    the mean of the kinds / 3. A pet build and EARLY have no value; a build without a main damage type takes the
    status and the value (0) of the target rr_main (I3)."""
    out = _target_area("Resistance Reduction", rs, None)
    main = tuple(getattr(facts, "main_types", ()))
    out["main"] = main
    if facts is None or getattr(facts, "pet", False) or getattr(facts, "early", False):
        out["status"], out["value"] = "INFO", None
        return out
    if not main:                                    # I3: no main damage type is a gap of the target (credit 0), not info
        return out
    parts = [kinds_status(m.kinds()) for m in main] + [out["status"]]
    out["status"] = min(parts, key=("GAP", "RISK", "OK", "INFO").index)
    out["value"] = _floor_value([min(m.kinds(), 3) / 3 for m in main])
    return out


def _build(quality) -> dict:
    """Skills & Devotions: the value of the points and constellations (analysis.build_quality), INFO without a check."""
    out = {"status": "INFO", "worst": None, "results": [], "value": None, "quality": quality}
    if quality is not None:
        out["status"], out["value"] = quality.status(), quality.value()
    return out


def areas(results, issues=(), facts=None, quality=None) -> dict:
    """Area -> {"status": OK/RISK/GAP/INFO, "worst": TargetResult|None, "results": [...], "value": 0-100|None}.
    The worst single value decides the status; a stage target with a gap can never show OK. The value is
    the fulfilment of the targets, rounded down; a RISK or GAP area never reaches 100. Sustain, Damage and
    Resistance Reduction come from the area facts (analysis.area_facts) and carry them ("sustain", "main") for
    the row text; a Damage conversion issue (A3/A5) makes OK/INFO a RISK; Skills & Devotions comes from the build
    check (`quality`, None = not checked) and carries it ("quality")."""
    out = {}
    for a in AREAS:
        rs = [r for r in results if r.area == a]
        if a == "Sustain":
            out[a] = _sustain(facts)
        elif a == "Damage":
            out[a] = _damage(rs, issues, facts)
        elif a == "Resistance Reduction":
            out[a] = _resistance_reduction(rs, facts)
        elif a == "Health":
            out[a] = _health(rs)
        elif a == "Skills & Devotions":
            out[a] = _build(quality)
        else:
            out[a] = _target_area(a, rs, facts)
        if out[a]["status"] in ("RISK", "GAP") and out[a]["value"] is not None:
            out[a]["value"] = min(out[a]["value"], 99)
    return out
