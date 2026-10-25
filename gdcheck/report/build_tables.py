"""Detail tables of the build rating and the damage benchmark (.72): D12 "Skill points by value", D13 "Devotions by
value", D14 "Damage benchmark" and the E6 lines "Skills and devotions" and "Damage benchmark". Only wording and order
happen here - every number comes from analysis/build_quality.py (a BuildQuality, found as `a.build_quality`; absent or
None = the analysis ran without the rating) and analysis/benchmark.py (a Benchmark, `a.benchmark`; absent or None = no
benchmark: a run below Ultimate, a pet build, an old boss file).

The values are points of the common value scale. The scale only ranks candidates, so the notes promise no gain and
no percentage; E3 recalculates every proposal."""
from __future__ import annotations

import math

from ..analysis.build_quality import by_per_point, by_weight
from ..analysis.targets import seconds_text
from ..combat.target_state import BOSS_NAME
from .model import Table

COLUMNS_12 = ["Skill", "Points", "Rank", "Last point", "Next point", "Note"]
COLUMNS_13 = ["Constellation", "Points", "Value", "Per point", "Celestial power", "Note"]
NOTES_12 = [
    "Value points on one common scale, heaviest last point first: the last point is what a respec takes away, the "
    "next point what one more point adds. The scale only ranks - it is no gain you can expect (E3 recalculates every "
    "proposal).",
    "Attacks, spells and procs have no damage model here and are not rated; a skill with one point has no last "
    "point. Rank is with gear bonuses, then the maximum rank."]
NOTES_13 = [
    "Value points on the same scale, heaviest per point first. Celestial Powers are not valued; a constellation "
    "with a bound power stays, and so does one whose affinity another constellation needs.",
    "A swap shown in D7 or E3 keeps affinity, the resistance caps and every bound power."]


def _quality(a):
    """The BuildQuality when there is something to show (a pet build, a levelling character - not rated - and a
    failure show nothing), else None."""
    bq = getattr(a, "build_quality", None)
    return bq if bq is not None and not bq.pet and not bq.early and not bq.error else None


def _num(v, digits: int = 2) -> str:
    return "-" if v is None else f"{v:.{digits}f}"


def d12(a) -> Table:
    bq = _quality(a)
    rows = [] if bq is None else [
        [s.name, str(s.points), f"{s.rank}/{s.max_rank}", _num(s.last), _num(s.next), s.note or "-"]
        for s in by_weight(bq.skills)]
    return Table("D12", "Skill points by value", COLUMNS_12, rows, list(NOTES_12))


def d13(a) -> Table:
    bq = _quality(a)
    rows = [] if bq is None else [
        [d.name, str(d.points), _num(d.value, 1), _num(d.per_point), _power(d), d.note or "-"]
        for d in by_per_point(bq.devotions)]
    return Table("D13", "Devotions by value", COLUMNS_13, rows, list(NOTES_13))


def _power(d) -> str:
    if not d.power:
        return "-"
    return f"{d.power} (bound to {d.bound_to})" if d.bound_to else f"{d.power} (not bound)"


def quality_tables(a) -> list[Table]:
    """[D12, D13] when the analysis carries a usable rating, else [] (the detail view stays as it was)."""
    return [d12(a), d13(a)] if _quality(a) is not None else []


def _names(items, key, limit) -> str:
    return ", ".join(f"{n} {_num(v)}" for n, v in sorted(items, key=key)[:limit])


def skills_row(a) -> list[str] | None:
    """E6 line ["Skills and devotions", text]: where the weight sits and what is worth changing; None for a pet
    build or without a rating."""
    bq = getattr(a, "build_quality", None)
    if bq is None or bq.pet or bq.early:
        return None
    if bq.error:
        return ["Skills and devotions", f"could not be rated ({bq.error})"]
    last = [(s.name, s.last) for s in bq.skills if s.last is not None]
    per = [(d.name, d.per_point) for d in bq.devotions if not d.bound_to]
    blind = bq.unvalued_gain()           # m8: the part of the value that comes out of skills the scale cannot rate (as D11 says)
    parts = [f"skills: {bq.skill_points_moved} of {bq.skill_points} points worth moving (value {bq.skill_gain:.1f}"
             + (f", {blind:.1f} of it from skills the scale cannot rate" if blind > 0 else "") + ")"]
    if last:
        parts.append("weakest last points " + _names(last, lambda x: (x[1], x[0]), 2)
                     + ", heaviest " + _names(last, lambda x: (-x[1], x[0]), 1))
    if bq.unrated_points:
        parts.append(f"{bq.unrated_points} points not rated, {bq.attack_points} of them attacks and spells")
    swap = bq.swap
    parts.append("devotions: " + ("weakest per point " + _names(per, lambda x: (x[1], x[0]), 1) + ", " if per else "")
                 + ("best swap " + " + ".join(swap["out"]) + " for " + " + ".join(swap["in"])
                    + f" (value +{swap['value']:.1f})" if swap else "no swap worth it"))
    if bq.free_devotion_points:
        parts.append(f"{bq.free_devotion_points} devotion points free")
    return ["Skills and devotions", "; ".join(parts) + " (D12, D13)"]


# -- D14 and the E6 line "Damage benchmark" -----------------------------------------------------------------------------

COLUMNS_14 = ["Target", "Level", "Health", "To kill", "Armor (absorbed)", "DA / OA", "Resistance of your main types",
              "Life Leech Res", "Your damage/s (hits + DoT)", "Time", "Range", "Confidence"]


def _benchmark(a):
    """The Benchmark when there is a dummy to show (no benchmark, a pet build and an old boss file show nothing)."""
    bm = getattr(a, "benchmark", None)
    return bm if bm is not None and not bm.pet and bm.dummy is not None else None


def _span(low: float, high: float) -> str:
    """'20-23 s' (from 120 s '2-3 min'), the low end rounded down and the high end up: the central value is inside.
    Minutes only when the low end is a minute or more too - '50-130 s', never '0-3 min' (review m1)."""
    if low >= 60 and high >= 119.5:
        return f"{math.floor(low / 60)}-{math.ceil(high / 60)} min"
    return f"{math.floor(low)}-{math.ceil(high)} s"


def _main_resistances(a, state) -> str:
    """'Chaos 5% (-35 stacking, -20% reduced, -0 flat)': the target's resistance to each main type of yours and the
    resistance reduction in force on it (the damage model applies it)."""
    out = []
    for m in tuple(getattr(getattr(a, "area_facts", None), "main_types", ()) or ()):
        keys = [m.res] + [k for k, v in BOSS_NAME.items() if v == m.res]
        key = next((k for k in keys if k in state.res), None)
        if key is None:
            continue
        t1, t2, t3 = state.rr.get(key, (0.0, 0.0, 0.0))
        out.append(f"{m.name} {state.res[key]:.0f}% (-{t1:.0f} stacking, -{t2:.0f}% reduced, -{t3:.0f} flat)")
    return "; ".join(out) or "-"


def _benchmark_cells(a, row) -> list[str]:
    st = row.state
    share = 100 * row.to_kill / row.health if row.health else 100.0
    timed = row.seconds is not None
    return [row.label, str(row.level), f"{row.health:,.0f}", f"{row.to_kill:,.0f} ({share:.0f}%)",
            f"{st.armor:,.0f} ({100 * st.armor_absorption:.0f}%)",
            f"{st.da_base:,.0f} ({st.da:,.0f} after your DA shred) / {st.oa:,.0f}",
            _main_resistances(a, st), f"{100 * st.llr:.0f}% ({st.llr_note})" if st.llr_note else f"{100 * st.llr:.0f}%",
            f"{row.dps:,.0f}", seconds_text(row.seconds) if timed else "-",
            _span(row.low, row.high) if timed else "-", row.confidence]


def d14(a) -> Table:
    """D14 'Damage benchmark': the Training Dummy and Mogara with every input of the time and its range."""
    bm = _benchmark(a)
    rows = [] if bm is None else [_benchmark_cells(a, r) for r in (bm.dummy, bm.boss) if r is not None]
    notes = []
    if bm is not None:
        d = bm.dummy
        notes = [
            "The Training Dummy heals fully and absorbs all damage at 15% life (database: its low-health shield), so the "
            "time is the time to take 85% of its health; Mogara counts the whole health. Time = to kill / (hits + damage "
            "over time per second of your rotation, procs included) against the target's armor, Defensive Ability and "
            "resistances after your reduction; no movement, no target switching.",
            "The Training Dummy's Life Leech Resistance is 500% in the database (capped at 100%): life steal against it is "
            "0, the time is not affected. Its level is yours + 2, the health follows the level.",
            "Health formula ASSUMED (level equation x (1 + difficulty table + the armor passive's life modifier); checked "
            "against community values for levels 50-85 only). Range = fastest (maximum Onslaught stacks) to slowest "
            "(no stacks, or the rotation 9% slower than play); the time uses the expected stacks"
            + (f": {d.stacks_note}." if d.stacks_note else "."),
        ]
        if d.notes:
            notes.append("Rotation: " + "; ".join(d.notes) + ".")
    return Table("D14", "Damage benchmark", COLUMNS_14, rows, notes)


def benchmark_tables(a) -> list[Table]:
    """[D14] when the analysis carries a benchmark with a dummy, else []."""
    return [d14(a)] if _benchmark(a) is not None else []


def benchmark_row(a) -> list[str] | None:
    """E6 line ["Damage benchmark", text]: the dummy and Mogara in one sentence; None without a benchmark."""
    bm = _benchmark(a)
    if bm is None:
        return None

    def part(row, shield: bool) -> str:
        head = f"{row.label} ({'Ultimate, ' if row.role == 'dummy' else ''}level {row.level}): {row.to_kill:,.0f} Health" \
               + (" to its shield" if shield else "")
        if row.seconds is None:
            return f"{head}, no damage to it"
        return f"{head}, {seconds_text(row.seconds)} (range {_span(row.low, row.high)}, {row.confidence})"
    text = part(bm.dummy, True) + ("; " + part(bm.boss, False) if bm.boss is not None else "")
    return ["Damage benchmark", f"{text} (D14)"]
