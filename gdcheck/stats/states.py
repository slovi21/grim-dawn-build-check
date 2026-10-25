"""Combat states (combat spec V2 2.1, 11, 14; plan CA3).

    STEADY           sheet + maintained buffs kept up (duration / cooldown >= 95%, X-018) = snapshot "combat"
    PREBUFFED        same values; the enemy debuffs of the opener are not up yet (targets only)
    PURGED           monster purge: every buff, toggle and aura goes unless notDispelable in the
                     game database (Werewolf/Wereraven form, Blade Barrier stay) - EXACT (DB flag)
    SLOWED           STEADY values, action rate x (1 - slow)
    CC_LOCKED        STEADY values, no new actions (rate 0); passive regeneration stays
    CIRCUIT_BREAKER  STEADY + one low-health proc buff (e.g. Ghoulish Hunger) - never in STEADY

Every state lists its active buffs with id, kind and whether a purge removes it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..data import player_skills as PS
from ..domain.enums import Activity
from .aggregate import aggregate
from .sheet import evaluate

ALWAYS_ON, MAINTAINED, PROC_BUFF = "ALWAYS_ON", "PLAYER_MAINTAINED", "PROC_BUFF"


@dataclass(frozen=True)
class BuffInfo:
    id: str                  # record stem, e.g. "werewolf1", "bloodofdreeg2"
    label: str
    kind: str                # ALWAYS_ON | PLAYER_MAINTAINED | PROC_BUFF
    purgeable: bool


@dataclass(frozen=True)
class CombatState:
    name: str
    values: object           # stats.sheet.State
    action_factor: float     # x action rate of STEADY (1 = unchanged, 0 = locked)
    active_buffs: tuple
    notes: tuple = field(default_factory=tuple)
    confidence: str = "EXACT"
    health_share: float = 1.0   # share of max Health this state is evaluated at

    def buff_ids(self) -> set:
        return {b.id for b in self.active_buffs}


def _stem(record: str) -> str:
    return record.rsplit("/", 1)[-1].removesuffix(".dbr")


def always_on(sources, keep=frozenset()) -> list:
    """Toggles, forms and auras with points (and their modifiers). Purgeable unless the skill
    (or its parent) is notDispelable in the game database."""
    return [BuffInfo(_stem(s.record), s.label, ALWAYS_ON, (s.parent or s.record) not in keep and s.record not in keep)
            for s in sources.skills
            if s.activity == Activity.TOGGLE and s.points > 0 and "/devotion/" not in s.record]


def maintained(sources, snap) -> list:
    kept = {b["record"] for b in snap.buffs if b.get("kept_up")}
    return [BuffInfo(_stem(s.record), s.label, MAINTAINED, True) for s in sources.skills
            if s.activity == Activity.TEMPORARY and s.points > 0
            and (s.record in kept or (s.parent or "") in kept)]


def circuit_breakers(sources) -> list:
    """Low-health procs that change values (Ghoulish Hunger, ...)."""
    out = []
    for p in sources.procs:
        if p.trigger != "LowHealth":
            continue
        eff = [e for e in sources.effects if e.group == p.skill_record and e.activity == Activity.PROC
               and e.key != "skillExperienceLevels"]
        if eff:
            out.append((p, eff))
    return out


def build(ctx, sources, snap, slow: float = 0.5) -> dict:
    """{name: CombatState}. `slow` = share of action speed lost in SLOWED (scenario value)."""
    keep = PS.not_dispelable(ctx.game_version or "1.3.0.8",
                             str((getattr(getattr(ctx, "index", None), "meta", None) or {}).get("fingerprint") or ""))
    base = always_on(sources, keep)
    kept = maintained(sources, snap)
    steady = tuple(base + kept)
    slow = min(max(slow, 0.0), 1.0)
    states = {
        "STEADY": CombatState("STEADY", snap.combat, 1.0, steady),
        "PREBUFFED": CombatState("PREBUFFED", snap.combat, 1.0, steady,
                                 ("opening: your enemy debuffs are not applied yet",)),
        "PURGED": CombatState("PURGED", snap.purged, 1.0, tuple(b for b in base if not b.purgeable),
                              ("monster purge: all buffs, toggles and auras removed except notDispelable "
                               "skills of the game database (e.g. the Werewolf form)",), "EXACT"),
        "SLOWED": CombatState("SLOWED", snap.combat, 1.0 - slow, steady,
                              (f"scenario: {slow * 100:.0f}% slower actions",), "DERIVED"),
        "CC_LOCKED": CombatState("CC_LOCKED", snap.combat, 0.0, steady,
                                 ("stunned/frozen/petrified: no new actions, regeneration continues",)),
    }
    by_src: dict = {}
    for e in sources.effects:
        by_src.setdefault(e.source_id, []).append(e)
    for proc, eff in circuit_breakers(sources):
        ids = {id(e) for e in eff}

        def weight(e, ids=ids):
            return 1.0 if id(e) in ids else 0.0
        extra, extra_prov = aggregate(sources.effects, weight)
        fields = dict(snap.combat.fields)
        prov = {k: list(v) for k, v in snap.combat.provenance.items()}
        for k, v in extra.items():
            fields[k] = fields.get(k, 0.0) + v
            prov.setdefault(k, []).extend(extra_prov.get(k, []))
        values = evaluate("circuit_breaker", ctx, sources, fields, prov, by_src)
        name = "CIRCUIT_BREAKER" if "CIRCUIT_BREAKER" not in states else f"CIRCUIT_BREAKER {proc.label}"
        thr = proc.trigger_param
        states[name] = CombatState(
            name, values, 1.0, steady + (BuffInfo(_stem(proc.skill_record), proc.label, PROC_BUFF, True),),
            (f"{proc.label}: below {thr:g}% Health (game database), cooldown {proc.cooldown or 0:g} s"
             if thr else f"{proc.label}: low-health trigger, cooldown {proc.cooldown or 0:g} s",
             "own snapshot - never part of STEADY"), "EXACT", health_share=(thr or 33.0) / 100)
    return states
