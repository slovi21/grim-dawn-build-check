"""SustainEngine (combat spec V2 11; plan CA5): life steal, regeneration, skill and
proc heals per second for one target and one combat state - kept apart and summed.

Life steal comes from the rotation (same damage events as the DpS). Regeneration:
combat values + kept-up maintained regeneration buffs. Proc heals: rate per instance
(attack procs at the action rate, 'hit by enemy' procs at the incoming hit rate of
the scenario). Circuit breakers (low health) are separate states, never added here."""
from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import Activity
from ..domain.fields import value_at
from ..mechanics import procs as PR
from ..mechanics import survival as SV
from . import dps as DPS


@dataclass(frozen=True)
class Sustain:
    target: str
    state: str
    life_steal: float
    regen: float
    skill_heals: float
    proc_heals: float
    total: float
    parts: tuple            # (label, HP/s, confidence)


def regen_per_s(snap, sources, values=None, buff_ids=None) -> float:
    """buff_ids: active buffs of the state (record stems); None = all kept-up buffs (STEADY)."""
    v = values or snap.combat
    kept = {b["record"] for b in snap.buffs if b.get("kept_up")}
    if buff_ids is not None:
        kept = {r for r in kept if r.rsplit("/", 1)[-1].removesuffix(".dbr") in buff_ids}
    flat = v.fields.get("characterLifeRegen", 0.0)
    pct = v.fields.get("characterLifeRegenModifier", 0.0)
    for e in sources.effects:          # maintained regeneration buffs (not part of the combat values)
        base = e.group[4:] if e.group.startswith("mod:") else e.group
        if e.activity == Activity.TEMPORARY and base in kept:
            if e.key == "characterLifeRegen":
                flat += e.value
            elif e.key == "characterLifeRegenModifier":
                pct += e.value
    return SV.health_regen(flat, pct, physique=v.abilities.get("physique", 0.0))


def heal_values(rec, buff, level):
    """(flat, % of max health) a skill heals: its own record plus its buff record (Blood of Dreeg heals in
    the buff). Pure - the same sum for the sustain engine and the area facts (panic button)."""
    flat = pct = 0.0
    for r in (rec or {}, buff or {}):
        f = r.get("f") or r
        flat += float(value_at(f.get("skillLifeBonus"), level) or 0.0)
        pct += float(value_at(f.get("skillLifePercent"), level) or 0.0)
    return flat, pct


def heal_fields(idx, record, level):
    """Heal of a skill record by name (heal_values of the record and its buff record)."""
    from ..domain import activity as ACTV
    r = idx.record(record) or {}
    return heal_values(r, ACTV.buff_record(r, idx), level)


def sustain(ctx, sources, snap, actions, target: str, state, incoming_hits_per_s: float = 1.0,
            action_override: float | None = None, procs=(), frames=None) -> Sustain:
    values = state.values
    max_hp = values.abilities["health"]
    heal_inc = values.fields.get("characterHealIncreasePercent", 0.0)
    rot = DPS.rotation(actions, snap, target, state.name, state.action_factor, action_override,
                       values=values, procs=procs, frames=frames)
    regen = regen_per_s(snap, sources, values, state.buff_ids())
    parts = [("life steal", rot.life_steal_per_s, "ESTIMATE"), ("health regeneration", regen, "DERIVED")]
    if rot.proc_life_steal_per_s:
        parts.append(("  of it from procs' own damage", rot.proc_life_steal_per_s, "ESTIMATE"))
    proc_total = 0.0
    eps_attack = rot.actions_per_s
    for p in sources.procs:
        if p.trigger == "LowHealth":
            continue
        flat, pct = heal_fields(ctx.index, p.skill_record, p.level)
        if not (flat or pct):
            continue
        eps = eps_attack if p.trigger.startswith("Attack") else (incoming_hits_per_s if p.trigger == "HitByEnemy" else 0.0)
        rate = PR.proc_rate(eps, p.chance, p.cooldown or 0.0)
        hps = rate * SV.heal_amount(flat, pct, max_hp, heal_inc)
        if hps:
            proc_total += hps
            parts.append((p.label, hps, "ESTIMATE"))
    skill_total = 0.0
    for st in sources.skills:
        if st.points < 1 or st.record not in ctx.character.hotbar or st.parent:
            continue
        flat, pct = heal_fields(ctx.index, st.record, st.level)
        rec = ctx.index.record(st.record) or {}
        from ..domain import activity as ACTV
        buff = ACTV.buff_record(rec, ctx.index) or {}
        cd = value_at((rec.get("f") or rec).get("skillCooldownTime"), st.level) or \
            value_at((buff.get("f") or buff).get("skillCooldownTime"), st.level)
        cdr = values.fields.get("skillCooldownReduction", 0.0)
        cd = cd * (1 - min(max(cdr, 0.0), 100.0) / 100) if cd else cd
        if (flat or pct) and cd and state.action_factor > 0:
            hps = SV.heal_amount(flat, pct, max_hp, heal_inc) / cd
            skill_total += hps
            parts.append((st.label, hps, "DERIVED"))
    total = SV.total_sustain(rot.life_steal_per_s, regen, skill_total, proc_total)
    return Sustain(target, state.name, rot.life_steal_per_s, regen, skill_total, proc_total, total, tuple(parts))
