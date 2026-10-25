"""Build quality (.72): are the skill points and the devotions well placed? (the "Skills & Devotions" row of the
overview, the detail tables D12 and D13)

Question of the player: "is my skilling ok, which skill points weigh how much for my build? Likewise the devotions."
The answer already existed in the candidate generators and was shown nowhere:

  skills      per skill the value of its LAST point (what a respec takes away) and of its NEXT point, and the greedy
              moves "this point is worth more elsewhere" (generators/skills.py assess)
  devotions   per constellation the value and the value per point, and the best swap that keeps affinity, the
              resistance caps and every bound Celestial Power (generators/devotions.py assess, light mode)

wasted = skill_gain + devotion_gain, in points of the common value scale (value_scale.py: 1 = about +1 % of the main
damage type or of Health). The scale only RANKS candidates - "the numbers only rank candidates" - so nothing here
promises a percentage: the player texts name skill points, never "worth ~6 % more". Status OK < 5, RISK < 15, else
GAP, counted on wasted_modelled() (decision 04.10.2026: the part that comes from skills the scale cannot value is a
model gap, not waste); value = 100 - ceil(wasted_modelled). Pet builds (the pet damage is not on the sheet), levelling
characters (stage EARLY) and failures are INFO.

What the scale cannot see: attacks and spells have no damage model (skills.py "not rated: attack/spell (no DPS
model)"), procs and skills without rank values are not rated either. Their share of the skill points is part of the
result (attack_points, unrated_points) so the text can say "attacks not rated" when it is high.

Light mode: generators.run(..., sections=("skills", "devotions"), light=True) - the full skill generator, the devotion
generator without the plans A/B and with 1-for-1 swaps, about 20 ms per analysis (PERFORMANCE.md .72). The
generator output is turned into plain frozen values here; the player texts live in report/area_text.py, the detail
tables in report/build_tables.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..mechanics.policy import Confidence, rule
from . import generators as GEN
from .proposals.generated import stage_targets
from .save_facts import EFFECT_SKILL_ONLY

SECTIONS = ("skills", "devotions")
RISK_FROM = 5.0         # wasted value points from which the status is RISK
GAP_FROM = 15.0         # ... and GAP
ATTACKS_HIGH_FROM = 1 / 3   # share of the skill points in attacks and spells (not rated) from which the text says so
NO_VALUE = 0.005        # a rated skill whose last and next point are worth less than this has no modelled value


@rule("build_quality", Confidence.HEURISTIC,
      "release .72: wasted = skill_gain + devotion_gain on the common value scale (1 = about +1 % of the main "
      "damage type or of Health; it ranks candidates, it is no measured gain). OK below 5, RISK below 15, else GAP - "
      "thresholds chosen by looking at the 20 test saves (docs of task Q2), not derived from the game")
def status_for(wasted: float) -> str:
    return "GAP" if wasted >= GAP_FROM else "RISK" if wasted >= RISK_FROM else "OK"


@dataclass(frozen=True)
class SkillValue:
    name: str
    points: int                 # points spent
    rank: int                   # effective rank (points + gear bonuses)
    max_rank: int
    last: float | None          # value of the last point (None: one point only, or not rated)
    next: float | None          # value of the next point (None: at the cap, or not rated)
    rated: bool                 # False: attack/spell, proc or no rank values - see note
    note: str


@dataclass(frozen=True)
class SkillMove:
    source: str                 # skill the points come from
    target: str                 # skill that is worth more
    points: int
    loss: float
    gain: float

    @property
    def unvalued(self) -> bool:
        """The points come from a skill with no modelled value (loss 0): a model gap, not proof of waste."""
        return self.loss < NO_VALUE


@dataclass(frozen=True)
class DevotionValue:
    name: str
    points: int
    total: int                  # stars of the constellation
    value: float                # value of the taken stars on the common scale (the Celestial Power is not valued)
    per_point: float
    power: str | None           # the taken Celestial Power
    bound_to: str | None        # skill the power is bound to (a bound power locks the constellation)
    note: str


@dataclass(frozen=True)
class BuildQuality:
    skills: tuple               # SkillValue, heaviest last point first
    moves: tuple                # SkillMove
    skill_points_moved: int
    skill_gain: float           # value points the greedy moves add (gain - loss)
    devotions: tuple            # DevotionValue
    swap: dict | None           # best 1-for-1 swap: {"out": (names), "in": (names), "points_out", "points_in", "value"}
    devotion_gain: float        # value of that swap (0 without one)
    free_devotion_points: int
    pet: bool = False
    error: str | None = None
    skill_points: int = 0       # points in skills outside the mastery bars
    unrated_points: int = 0     # ... of which not rated (attacks, spells, procs, no rank values)
    attack_points: int = 0      # ... of which attacks and spells (no DPS model)
    early: bool = False         # levelling (stage EARLY): the points are still being placed, not rated

    def wasted(self) -> float:
        return self.skill_gain + self.devotion_gain

    def unvalued_gain(self) -> float:
        """The part of skill_gain whose moves take points from a skill with NO modelled value (Rebuke: reflected and
        retaliation damage, Curse of Frailty: a debuff for other damage types ...). The scale cannot see what such a
        skill does - the gain is real on the scale, but the "waste" it is measured against may not be."""
        return sum(m.gain - m.loss for m in self.moves if m.unvalued)

    def wasted_modelled(self) -> float:
        """wasted() without the moves out of skills the scale cannot value."""
        return max(0.0, self.wasted() - self.unvalued_gain())

    def points_modelled(self) -> int:
        """skill_points_moved without the points taken from skills the scale cannot value."""
        return sum(m.points for m in self.moves if not m.unvalued)

    def value(self) -> int | None:
        """100 - the modelled waste, rounded up (decision 04.10.2026: a move out of a skill the scale cannot value is a
        model gap, not a waste - see wasted_modelled)."""
        if self.pet or self.early or self.error:
            return None
        return max(0, 100 - math.ceil(self.wasted_modelled() - 1e-9))

    def status(self) -> str:
        if self.pet or self.early or self.error:
            return "INFO"
        return status_for(self.wasted_modelled())

    def attack_share(self) -> float:
        return self.attack_points / self.skill_points if self.skill_points else 0.0

    def unrated_share(self) -> float:
        return self.unrated_points / self.skill_points if self.skill_points else 0.0

    def attacks_unrated(self) -> bool:
        """A third or more of the skill points sit in attacks and spells the scale cannot rate - the text says
        "attacks not rated" then."""
        return self.attack_share() >= ATTACKS_HIGH_FROM - 1e-9


def _plural(n: int) -> str:
    return f"{n} point" + ("" if n == 1 else "s")


def _no_value(entry: dict) -> bool:
    """A rated skill whose last and next point are worth nothing on the scale: the fields of the skill (reflected
    damage, retaliation, crowd control, a debuff for another damage type ...) are not what the scale measures."""
    last, nxt = entry.get("last"), entry.get("next")
    return (last is not None or nxt is not None) and max(last or 0.0, nxt or 0.0) < NO_VALUE


def _skill_notes(entry: dict, moves) -> str:
    parts = [entry.get("note") or ""]
    if entry.get("mastery"):
        parts.append("mastery bar")
    elif entry.get("rated") and _no_value(entry):
        parts.append("no value on the scale (its effect may not be modelled)")
    own = entry.get("own_next")
    if not entry.get("rated") and own is not None:
        parts.append(f"own flat damage {own:+.0f}% with the next point")
    parts += [f"{_plural(m.points)} worth moving to {m.target}" for m in moves if m.source == entry["name"]]
    parts += [f"{_plural(m.points)} worth taking from {m.source}" for m in moves if m.target == entry["name"]]
    if entry.get("full"):
        parts.append("max rank")
    return "; ".join(p for p in parts if p)


def _weight(s: SkillValue):
    """Heaviest last point first; then rated skills without a last point (next point); unrated skills last."""
    if s.last is not None:
        return (0, -s.last, s.name)
    if s.rated:
        return (1, -(s.next or 0.0), s.name)
    return (2, -s.points, s.name)


def by_weight(skills) -> list:
    """The skills in the order of D12: the weight of the last point, highest first."""
    return sorted(skills, key=_weight)


def by_per_point(devotions) -> list:
    """The constellations in the order of D13: value per point, highest first."""
    return sorted(devotions, key=lambda d: (-d.per_point, d.name))


def _devotion_note(e: dict) -> str:
    parts = []
    if e.get("bound"):
        parts.append("locked: its power is bound")
    if not e.get("complete"):
        parts.append(f"incomplete ({e['points']} of {e['total']})")
    if e.get("carries"):
        parts.append("affinity needed by " + ", ".join(e["carries"]))
    if not e.get("weapon_ok", True):
        parts.append("weapon condition not met (no value)")
    elif e.get("no_values"):
        parts.append("no stat values")
    elif e.get("power") and not e.get("fields"):
        parts.append("power only (not valued)")
    return "; ".join(parts)


def _empty(**kw) -> BuildQuality:
    base = dict(skills=(), moves=(), skill_points_moved=0, skill_gain=0.0, devotions=(), swap=None,
                devotion_gain=0.0, free_devotion_points=0)
    return BuildQuality(**{**base, **kw})


def from_raw(raw: dict) -> BuildQuality:
    """BuildQuality from the result of generators.run(sections=("skills", "devotions")). A failed or missing
    section makes the whole rating INFO (a half sum would read as a fit)."""
    problems = [f"{k} ({v})" for k, v in GEN.failures(raw).items()]
    problems += [f"{k} (no result)" for k in SECTIONS if k not in raw]
    if problems:
        return _empty(error="; ".join(problems))
    sk, dv = raw["skills"], raw["devotions"]
    moves = tuple(SkillMove(m["from"], m["to"], int(m["points"]), float(m["loss"]), float(m["gain"]))
                  for m in sk.get("merged") or [])
    entries = sk.get("entries") or []
    skills = tuple(by_weight(SkillValue(
        e["name"], int(e["points"]), int(e["rank"]), int(e["max"]), e.get("last"), e.get("next"),
        bool(e.get("rated")), _skill_notes(e, moves)) for e in entries))
    counted = [e for e in entries if not e.get("mastery")]
    devotions = tuple(by_per_point(DevotionValue(
        e["name"], int(e["points"]), int(e["total"]), float(e["value"]), float(e["per_point"]), e.get("power"),
        e.get("bound_to"), _devotion_note(e)) for e in dv.get("taken") or []))
    best = (dv.get("swaps") or [None])[0]
    swap = None if best is None else {"out": tuple(best["out"]), "in": tuple(best["in"]),
                                      "points_out": best["points_out"], "points_in": best["points_in"],
                                      "value": float(best["value"])}
    return BuildQuality(
        skills=skills, moves=moves, skill_points_moved=sum(m.points for m in moves),
        skill_gain=float(sk.get("gain") or 0.0), devotions=devotions, swap=swap,
        devotion_gain=swap["value"] if swap else 0.0, free_devotion_points=int(dv.get("free") or 0),
        skill_points=sum(int(e["points"]) for e in counted),
        unrated_points=sum(int(e["points"]) for e in counted if not e.get("rated")),
        attack_points=sum(int(e["points"]) for e in counted
                          if not e.get("rated") and e.get("effect") == EFFECT_SKILL_ONLY))


def assess(ctx, snap, results: dict, melee: bool, pet: bool = False, early: bool = False) -> BuildQuality:
    """Rate the skills and devotions of one analysed character. results: {id: TargetResult} (the stage targets
    the generators rate against); melee: the archetype; pet: a pet build is not rated (INFO); early: a levelling
    character (stage EARLY) is not rated either (INFO) - its points are still being placed, 55 free devotion points
    are not a gap. Neither runs a generator. A failure becomes INFO with the error - the rating never stops an
    analysis."""
    if pet or early:
        return _empty(pet=pet, early=early and not pet)
    try:
        raw = GEN.run(ctx, snap, stage_targets(results), melee, with_reroll=False, sections=SECTIONS, light=True)
        return from_raw(raw)
    except Exception as ex:                                              # noqa: BLE001
        return _empty(error=f"{type(ex).__name__}: {ex}")
