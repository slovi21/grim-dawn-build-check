"""Combat buffs with a running time (M18, rule a): the buffs a skill or a devotion power starts during the fight.

The sheet and the STEADY state hold what is on all the time (permanent effects, toggles, a cast buff that is up at least 95 % of
the time - Pneumatic Burst: 60 s duration, 8 s cooldown). What a fight starts and ends again is not on the sheet and was in
no state: Deadly Momentum (the secondary buff of Cadence: every finale starts 5 s of +total damage and flat Physical damage,
video of Cpt Blade) and Dying God - Hungering Void (a celestial power bound to Bonechilling Cry: 33 % per cast starts 20 s of
+Chaos damage, +10 % speed and +18 % critical damage, 30 s cooldown; video of Cpt Wolf, Feral Claws x1.15, speed 180 -> 190 %,
critical damage 22 -> 40 %). `find` lists them for a character, `uptimes` gives the share of the fight each is on for a
rotation, `state_with` the combat values with some of them on.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from ..domain import activity as ACT
from ..domain.enums import Activity
from ..domain.fields import value_at
from ..mechanics import combat_buffs as CB
from ..stats.aggregate import aggregate
from ..stats.sheet import evaluate
from .dps import BASE_CASTS_PER_S

SECONDARY = "SkillSecondary_BuffSelfDuration"
MIN_CAST_S = 1.0 / BASE_CASTS_PER_S      # one cast at normal speed (combat.dps, ESTIMATE): no cooldown skill is cast faster


@dataclass(frozen=True)
class CombatBuff:
    id: str
    label: str
    kind: str                  # "secondary": a skill's own buff | "celestial": a devotion power bound to a skill
    effects: tuple             # the EffectInstance rows the buff carries (what it adds while it is on)
    duration: float            # seconds
    cooldown: float            # the buff's own cooldown in seconds (0 = none)
    chance: float              # per roll, 0..1
    via: tuple                 # skill records whose use rolls the buff
    per_use: float             # rolls per use (a charged finale: one in n + 1 strikes)
    utility_cooldown: float    # > 0: the rolling skill is a cast with this cooldown on the hotbar (Bonechilling Cry), cast on
    #                            cooldown by the model when it is no action of the rotation (ASSUMED)
    note: str = ""


def _skill_value(rec, buff, key, level):
    v = value_at((rec or {}).get(key), level)
    return v if v else (value_at((buff or {}).get(key), level) or 0.0)


def _combat_effect(e) -> bool:
    """The effects of a buff that count in a state (the same exclusions as stats.sheet: a debuff-like negative offensive value
    and regeneration / run speed are not part of the fight values)."""
    return not ((e.key.startswith("offensive") and e.value <= 0) or "Regen" in e.key or "RunSpeed" in e.key)


def find(ctx, sources) -> list:
    """The combat buffs of a character: secondary buffs of skills on the hotbar and celestial buffs bound to a skill."""
    idx = ctx.index
    out: list = []
    for st in sources.skills:
        if st.points < 1 or st.klass != SECONDARY or not st.on_hotbar or not st.parent:
            continue
        rec = idx.record(st.record) or {}
        buff = ACT.buff_record(rec, idx) or {}
        duration = _skill_value(rec, buff, "skillActiveDuration", st.level)
        effects = tuple(e for e in sources.effects if e.source_id == f"skill:{st.record}" and _combat_effect(e))
        if duration <= 0 or not effects:
            continue
        parent = idx.record(st.parent) or {}
        n = int(value_at(parent.get("skillChargeLevel"), max(st.level, 1)) or 0) \
            if "ChargedFinale" in str(parent.get("Class", "")) else 0
        out.append(CombatBuff(
            st.record, st.label, "secondary", effects, duration, 0.0, 1.0, (st.parent,), 1.0 / (n + 1), 0.0,
            f"started by {'the finale (every ' + str(n + 1) + '. strike)' if n else 'every use'} of "
            f"{ctx.index.display(st.parent) or st.parent.rsplit('/', 1)[-1]} (ASSUMED trigger)"))
    for p in sources.procs:
        if not p.bound_to or not p.trigger.startswith("AttackEnemy"):
            continue
        rec = idx.record(p.skill_record) or {}
        cls = str(rec.get("Class", ""))
        if not cls.startswith("Skill_Buff") or "Attack" in cls:
            continue
        buff = ACT.buff_record(rec, idx) or {}
        effects = tuple(e for e in sources.effects if e.source_id == f"devotion:{p.skill_record}"
                        and e.activity in (Activity.TEMPORARY, Activity.PROC) and _combat_effect(e))
        duration = _skill_value(rec, buff, "skillActiveDuration", p.level)
        cooldown = _skill_value(rec, buff, "skillCooldownTime", p.level)
        if duration <= 0 or not effects:
            continue
        bound = idx.record(p.bound_to) or {}
        bound_buff = ACT.buff_record(bound, idx) or {}
        util = _skill_value(bound, bound_buff, "skillCooldownTime", 1) if p.bound_to in ctx.character.hotbar else 0.0
        out.append(CombatBuff(
            p.skill_record, p.label, "celestial", effects, duration, cooldown, p.chance, (p.bound_to,), 1.0, util,
            f"{p.chance * 100:g}% per use of {ctx.index.display(p.bound_to) or p.bound_to.rsplit('/', 1)[-1]}, "
            f"{duration:g} s on, {cooldown:g} s cooldown"))
    return out


def cast_interval(cooldown: float, cdr: float = 0.0) -> float:
    """Seconds between two casts of a skill on the hotbar that is cast on its cooldown: the cooldown less `cdr` percent (0 to
    100), but never shorter than one cast (E1: a cooldown reduction of 100 % or more used to divide by zero). A cast takes at
    least `MIN_CAST_S` - the same estimate as the cast rate of combat.dps - and the cooldown of a skill that is shorter than
    that to begin with stays as it is."""
    cut = min(max(float(cdr or 0.0), 0.0), 100.0) / 100
    return max(cooldown * (1 - cut), min(cooldown, MIN_CAST_S))


def uptimes(buffs, uses: dict, cdr: float = 0.0) -> list:
    """[(buff, share of the fight it is on)] for a rotation: `uses` maps an action record to its uses per second. A buff rolled
    by a skill that is no action of the rotation but a cast with a cooldown on the hotbar is rolled once per cooldown."""
    out = []
    for b in buffs:
        eps = sum(uses.get(v, 0.0) for v in b.via)
        if eps <= 0 and b.utility_cooldown > 0:
            eps = 1.0 / cast_interval(b.utility_cooldown, cdr)
        out.append((b, CB.triggered_uptime(eps * b.per_use, b.chance, b.duration, b.cooldown)))
    return out


def state_with(ctx, sources, snap, buffs):
    """The combat values with the effects of `buffs` on top (the snapshot's own STEADY state without any)."""
    if not buffs:
        return snap.combat
    chosen = {id(e) for b in buffs for e in b.effects}
    extra, extra_prov = aggregate(sources.effects, lambda e: 1.0 if id(e) in chosen else 0.0)
    fields = dict(snap.combat.fields)
    prov = {k: list(v) for k, v in snap.combat.provenance.items()}
    for k, v in extra.items():
        fields[k] = fields.get(k, 0.0) + v
        prov.setdefault(k, []).extend(extra_prov.get(k, []))
    by_src: dict = {}
    for e in sources.effects:
        by_src.setdefault(e.source_id, []).append(e)
    return evaluate("combat+buffs", ctx, sources, fields, prov, by_src)


def combine(parts, ups, base=None):
    """One Rotation from [(weight, Rotation)] - the rotation with each combination of buffs on, weighted by the share of the
    fight that combination lasts. `base` (the rotation without any buff, which is what the caller computed first) names the
    actions and gives everything that is shown rather than averaged - the main action and the spender, the cycle and the
    timings of the animation frames; without it the first part is taken (`masks` lists the combination without any buff
    first). The rates, the damage and the uses are averages over time (E1: they used to come from the buffed part, so the
    cycle and the timing of a row belonged to the rotation with the buff on while the damage was the mix). A single part is
    the whole fight (every buff always on, or none): that rotation itself."""
    if len(parts) == 1:
        only = parts[0][1]
        return only if not ups else dataclasses.replace(only, buffs=tuple((b.label, u, b.note) for b, u in ups))
    base = parts[0][1] if base is None else base

    def avg(attr):
        return sum(w * getattr(r, attr) for w, r in parts)
    uses: dict = {}
    for w, r in parts:
        for k, v in r.uses.items():
            uses[k] = uses.get(k, 0.0) + w * v
    notes = tuple(f"{b.label}: on {u * 100:.0f}% of the fight ({b.note}); the rotation is the mix with and without it"
                  for b, u in ups if u > 0)
    return dataclasses.replace(
        base, actions_per_s=avg("actions_per_s"), dps_direct=avg("dps_direct"), dot_per_s=avg("dot_per_s"),
        life_steal_per_s=avg("life_steal_per_s"), proc_dps=avg("proc_dps"), proc_dot_per_s=avg("proc_dot_per_s"),
        proc_life_steal_per_s=avg("proc_life_steal_per_s"), uses=uses, energy_per_s=avg("energy_per_s"),
        assumptions=base.assumptions + notes, buffs=tuple((b.label, u, b.note) for b, u in ups))


def masks(ups) -> list:
    """[(weight, buffs on)] over the combinations of independent buffs with the given uptimes; weights add up to 1. The
    combination without any buff comes first (when it ever happens: a buff that is on all the time is in every mask)."""
    out = [(1.0, ())]
    for b, u in ups:
        out = [(w * (u if on else 1 - u), on_set + ((b,) if on else ())) for w, on_set in out for on in (False, True)
               if (u if on else 1 - u) > 0]
    return out
