"""Damage benchmark: seconds until the Training Dummy's shield and until Mogara is down (.72, plan A.4 + N5).

The Training Dummy heals fully and absorbs everything at 15 % health (database: targetdummy_lowhealthshield_01),
so what the community calls a "dummy kill time" is the time to take 85 % of its health. Mogara (Jagged Waste) is
a second, real boss target for the Expert View; against her the whole health counts.

    seconds = to_kill / dps       dps = dps_direct + dot_per_s of DPS.rotation (procs included) against the
                                  target state: armor on physical hits, resistances after your reduction, PtH vs
                                  the target's DA - all inside the action model's target events

Onslaught (plan N5): the central value uses the EXPECTED stacks - the community times come from real play and
a spender finds the stacks its generator built (mechanics.onslaught.expected_stacks); --onslaught N overrides.
The range: `low` = the faster end (maximum stacks, the model's own rotation); `high` = the slower end, the worse
of 0 stacks and the rotation slack (the action model's rotation is 4-9 % too fast against play, KG-09). The
confidence is never above ASSUMED (health formula, expected stacks, no movement or target switching).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..combat import actions as ACT
from ..combat import dps as DPS
from ..combat import frames as FR
from ..data import benchmarks as BD
from ..mechanics import onslaught as ONS
from ..mechanics.policy import Confidence, rule

ROTATION_SLACK = 0.09          # KG-09: the modelled rotation is 4-9 % faster than play (upper end)
DEFAULT_DURATION_S = 5.0       # stacks last 5 s after the last gain (Gruldir's Cleaver, game database)


@dataclass(frozen=True)
class BenchRow:
    """One benchmark target. `seconds` is the central value, [`low`, `high`] the range (see module doc);
    all three None when the build deals no damage to the target. `rotation` and `state` are the inputs the
    detail table (D14) shows."""
    role: str                      # "dummy" | "bench_boss"
    label: str                     # "Training Dummy" | "Mogara, the Prime Matriarch"
    level: int
    health: float
    to_kill: float                 # health x kill share (the dummy: 85 %)
    dps: float                     # dps_direct + dot_per_s of the central rotation
    seconds: float | None
    low: float | None
    high: float | None
    confidence: str
    notes: tuple = ()              # the rotation's assumptions
    stacks: int = 0                # Onslaught stacks of the central value
    stacks_max: int = 0
    stacks_note: str = ""          # "expected 6 of 6 ..." | "set by you ..." | ""
    seconds_zero: float | None = None   # the same with 0 stacks
    seconds_max: float | None = None    # the same with the maximum stacks
    state: object = field(default=None, compare=False)       # combat.target_state.TargetState
    rotation: object = field(default=None, compare=False)   # combat.dps.Rotation of the central value


@dataclass(frozen=True)
class Benchmark:
    dummy: BenchRow | None
    boss: BenchRow | None
    pet: bool = False


def applies(ctx) -> bool:
    """The benchmark rates against Ultimate monsters (the Training Dummy and Mogara at the Ultimate adjustments), so it
    runs for Ultimate only - the same gate as the boss combat check. A Normal or Elite character gets no dummy time:
    it would be a time against monsters it does not fight (decision 04.10.2026)."""
    return ctx.difficulty.value == 2


def _rotation(state, acts, snap, procs, frames, action_rate):
    return DPS.rotation(acts, snap, state.name, "STEADY", 1.0, action_rate, procs=procs, frames=frames)


def _seconds(to_kill: float, rot) -> float | None:
    dps = rot.dps_direct + rot.dot_per_s
    return to_kill / dps if dps > 0 else None


def bench_row(state, actions, snap, procs, frames, action_rate=None, actions_zero=None, actions_max=None,
              label: str = "", stacks: int = 0, stacks_max: int = 0, stacks_note: str = "") -> BenchRow:
    """The row of one target state. `actions` give the central value; `actions_zero` / `actions_max` (the same
    actions at 0 / maximum Onslaught stacks, None without a spender) give the ends of the range."""
    rot = _rotation(state, actions, snap, procs, frames, action_rate)
    to_kill = state.health * state.kill_share
    dps = rot.dps_direct + rot.dot_per_s
    conf = FR.weakest(rot.confidence, Confidence.ASSUMED.value)
    common = dict(role=state.role, label=label or state.name, level=state.level, health=state.health,
                  to_kill=to_kill, dps=dps, confidence=conf, notes=tuple(rot.assumptions), stacks=stacks,
                  stacks_max=stacks_max, stacks_note=stacks_note, state=state, rotation=rot)
    t = _seconds(to_kill, rot)
    if t is None:
        return BenchRow(seconds=None, low=None, high=None, **common)
    t_zero = t_max = t
    if actions_zero is not None:
        t_zero = _seconds(to_kill, _rotation(state, actions_zero, snap, procs, frames, action_rate)) or t
    if actions_max is not None:
        t_max = _seconds(to_kill, _rotation(state, actions_max, snap, procs, frames, action_rate)) or t
    return BenchRow(seconds=t, low=min(t, t_max), high=max(t * (1 + ROTATION_SLACK), t_zero),
                    seconds_zero=t_zero, seconds_max=t_max, **common)


def _generators(sources, idx) -> tuple[set, float]:
    """(records of the generator skills, stack duration s): skills with their own skillComboChargeLevel and the
    targets of item skill modifiers that add stacks (the same sources as combat.actions._onslaught_max)."""
    gens, duration = set(), 0.0
    for m in sources.modifiers:
        if m.combo_level:
            gens.add(m.target_skill)
            duration = max(duration, float(m.combo_duration or 0.0))
    for st in sources.skills:
        if st.points < 1 and not st.form:
            continue
        r = idx.record(st.record) or {}
        own = r.get("skillComboChargeLevel")
        if own and (max(own) if isinstance(own, list) else own):
            gens.add(st.record)
    return gens, duration or DEFAULT_DURATION_S


def _expected(ctx, sources, snap, acts_max, n_max, state, procs, frames) -> tuple[int, str]:
    """(expected stacks, note) of the rotation on `state`: the main action is the generator when its skill (or
    one of its modifiers) is one; the window is the time between two spender uses without the spender's own
    animation, at most the stack duration."""
    rot = _rotation(state, acts_max, snap, procs, frames, ctx.options.action_rate)
    if not rot.spender:
        return 0, "no Onslaught spender in the rotation"
    main = next((a for a in acts_max if a.label == rot.main), None)
    gens, duration = _generators(sources, ctx.index)
    if main is None or not gens & ({main.action_id} | set(getattr(main, "chain", ()) or ())):
        return 0, f"{rot.main} builds no stacks (generator not in the rotation)"
    ts = 1.0 / rot.spender_timing.actions_per_s if rot.spender_timing and rot.spender_timing.actions_per_s else 0.0
    window = min(max(rot.cycle_s - ts, 0.0), duration)
    n = ONS.expected_stacks(n_max, rot.actions_per_s, window)
    return n, (f"expected {n} of {n_max} Onslaught stacks: {rot.main} is used about "
               f"{rot.actions_per_s * window:.0f} times between two {rot.spender} uses (stacks last {duration:g} s)")


def _variants(ctx, sources, snap, actions, procs, tstates, frames, dummy):
    """(central actions, actions at 0 stacks | None, actions at max stacks | None, stacks, max, note)."""
    n_max = max((a.onslaught_max for a in actions if a.spender and a.onslaught_max), default=0)
    if not n_max:
        return actions, None, None, 0, 0, ""
    user = ctx.options.onslaught
    acts_max = ACT.damage_per_action(ctx, sources, snap, tstates, onslaught=n_max)
    if user is not None:                      # `actions` were built with these stacks (analysis.run)
        stacks = max(0, min(int(user), n_max))
        zero = actions if stacks == 0 else ACT.damage_per_action(ctx, sources, snap, tstates, onslaught=0)
        return (actions, zero, acts_max, stacks, n_max,
                f"{stacks} of {n_max} Onslaught stacks set by you (--onslaught)")
    stacks, note = _expected(ctx, sources, snap, acts_max, n_max, dummy, procs, frames)
    if stacks >= n_max:
        central = acts_max
    elif stacks == 0:
        central = actions
    else:
        central = ACT.damage_per_action(ctx, sources, snap, tstates, onslaught=stacks)
    return central, actions, acts_max, stacks, n_max, note


@rule("dummy_benchmark", Confidence.ASSUMED,
      "seconds to the Training Dummy's shield (85 % of its health, database: low-health shield at 15 %) and to "
      "Mogara: health / (dps_direct + dot_per_s) of DPS.rotation against the target state; health formula ASSUMED "
      "(mechanics.monster.monster_health), Onslaught stacks expected (ASSUMED), rotation 4-9 % faster than play "
      "(KG-09); no movement, no target switching")
def run(ctx, sources, snap, actions, procs, tstates, frames, pet: bool = False) -> Benchmark:
    """The benchmark rows of a character. No benchmark (dummy None) without a dummy state with health (an old
    boss-combat file), for pet builds (`pet`: their damage is not on your sheet) and for builds without actions
    (seconds None)."""
    if pet:
        return Benchmark(None, None, True)
    dummy = next((t for t in tstates if t.role == "dummy" and t.health > 0), None)
    if dummy is None:
        return Benchmark(None, None)
    boss = next((t for t in tstates if t.role == "bench_boss" and t.health > 0), None)
    central, zero, mx, stacks, n_max, note = _variants(ctx, sources, snap, actions, procs, tstates, frames, dummy)
    rate = ctx.options.action_rate

    def row(state):
        return bench_row(state, central, snap, procs, frames, rate, zero, mx, BD.BENCHMARK_LABELS[state.role],
                         stacks, n_max, note)
    return Benchmark(row(dummy), row(boss) if boss else None)


def target_inputs(bench: Benchmark | None) -> dict:
    """The score's benchmark input {"dummy_ttk": seconds | None}. None without a benchmark (below Ultimate, a file
    without benchmarks) and for pet builds: not measurable, the target is `info`. A build that deals no damage to
    the dummy is measured: seconds None with "dummy_no_damage" - the target is a gap with credit 0 (I3)."""
    if bench is None or bench.pet or bench.dummy is None:
        return {"dummy_ttk": None}
    if bench.dummy.seconds is None:
        return {"dummy_ttk": None, "dummy_no_damage": True}
    return {"dummy_ttk": bench.dummy.seconds}
