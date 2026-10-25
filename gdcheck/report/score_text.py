"""The wording of the score in the Expert View (E6 'Score parts', .72): what each group contributes, the reserve,
the formula with its numbers and how much of the target weight could be rated. Only wording - every number
comes from analysis.readiness (F-008)."""
from __future__ import annotations

import math
from fractions import Fraction

from ..analysis import readiness as RD
from ..domain.enums import Stage

GROUP_LABEL = {"core": "Core targets", "cc_sustain": "CC & sustain", "offense": "Offense", "scenario": "Scenario"}
RESERVE_OF = "Health, DA, armor, Physical Res, hit chance, resistances over the cap, training dummy"
# resistance reduction is capped at 1.0 (all three kinds): it earns no reserve (review C1) and it is one of the three
# things the score gate asks for (.74: no new word - "required" would clash with must / recommended, N-020)
RESERVE_RULE = "Your score counts only as far as resistances, Armor Absorption and resistance reduction are met"


def _pct(x: float) -> int:
    return int(math.floor(100 * x + 1e-9))


def truncate_tenth(x: float) -> float:
    """x cut (not rounded) to one decimal, exact on the float: the E6 line shows the unrounded score this way, so rounding
    the shown value half-up always gives the score - a rounded 37.476 would read '37.5 -> 37' (review .75)."""
    return math.floor(Fraction(x) * 10) / 10


def _g(x: float) -> str:
    return f"{x:g}"


def score_parts(a) -> str:
    """Readable score breakdown: per group the share met (and its share of the score), the reserve, the formula
    with the numbers, the rated weight. EARLY: level and Ultimate resistances."""
    sc = a.score
    share = RD.weight_shares(a.results)
    parts = []
    for g in RD.GROUPS:
        rs = [r for r in a.results if r.group == g and r.status != "info"]
        if g not in sc["parts"] or not rs:
            continue
        n = {k: sum(r.status == k for r in rs) for k in ("ok", "tight", "gap")}
        parts.append(f"{GROUP_LABEL.get(g, g)} {sc['parts'][g]}% met ({_pct(share.get(g, 0.0))}% of the score; "
                     f"{n['ok']} ok, {n['tight']} tight, {n['gap']} gap)")
    if a.stage == Stage.EARLY:
        return "; ".join(parts + ["EARLY score = up to 25 for level progress + up to 8 for Ultimate resistances at target"])
    low, width = RD.BAND[a.stage]
    s = RD.settings(None)
    how = (f"reserve {_pct(sc['margin'])}% ({RESERVE_OF}); "
           f"score = {low} + {width} x {_pct(sc['gate'])}% gate x ({1 - s['margin_share']:g} x {_pct(sc['met'])}% met + "
           f"{s['margin_share']:g} x {_pct(sc['margin'])}% reserve) = {truncate_tenth(sc['raw']):.1f} -> {sc['score']}; "
           f"{_g(round(sc['weight_rated'], 2))} of {_g(round(sc['weight_all'], 2))} weight rated; {RESERVE_RULE}")
    return "; ".join(parts + [how])
