"""Source identity: every worn or active source stays its own instance (ADR-2).
Nothing here decides how values combine - that is the StackingPolicy's job."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .enums import Activity, SourceKind, StackingPolicy


@dataclass(frozen=True)
class SourceInstance:
    id: str
    kind: SourceKind
    owner_id: str               # item instance id or skill record
    slot: str | None
    record_id: str
    item_instance_id: str | None
    label: str                  # readable name ("Entropic Coil (Ring 1)")
    unresolved: bool = False    # record not in the index (mod item, newer patch)


@dataclass(frozen=True)
class EffectInstance:
    id: str
    source_id: str
    key: str                    # database field, e.g. defensiveFire
    value: float
    activity: Activity
    policy: StackingPolicy
    group: str                  # same group + key = same effect in game (MAX_VALUE etc.)
    scope: str | None = None    # "weapon:Main hand" for flat damage of one weapon
    condition: str | None = None


@dataclass(frozen=True)
class ModifierInstance:
    id: str
    source_id: str
    target_skill: str
    modifier_record: str
    fields: tuple               # ((key, value), ...)
    conversions: tuple          # ((from, to, pct), ...)
    wd_pct: float = 0.0
    combo_level: float = 0.0    # Onslaught stacks generated (skillComboChargeLevel)
    combo_duration: float = 0.0


@dataclass(frozen=True)
class ProcInstance:
    id: str
    source_id: str
    skill_record: str
    label: str
    trigger: str
    chance: float               # 0..1
    cooldown: float | None
    level: int
    bound_to: str | None = None  # celestial power: the skill it is bound to
    trigger_param: float | None = None  # e.g. LowHealth threshold in % (controller triggerParam)


@dataclass(frozen=True)
class ConversionRule:
    id: str
    source_id: str
    stage: str                  # "skill" | "global"
    skill: str | None           # target skill for stage "skill"
    from_type: str
    to_type: str
    percent: float
    activity: Activity


@dataclass(frozen=True)
class GrantedSkill:
    id: str
    source_id: str
    skill_record: str
    label: str
    level: int
    activity: Activity


@dataclass(frozen=True)
class SkillBonus:
    source_id: str
    kind: str                   # "all" | "mastery" | "skill"
    target: str | None
    levels: int


@dataclass(frozen=True)
class SkillState:
    record: str
    label: str
    points: int
    level: int                  # effective rank (points + bonuses, form rank for form abilities)
    activity: Activity
    reason: str
    klass: str
    on_hotbar: bool
    parent: str | None          # base skill of a modifier/transmuter
    form: str | None            # shapeshift the ability belongs to
    values_record: str          # record that carries the values (buff record for auras)


@dataclass(frozen=True)
class SourceSet:
    sources: tuple = ()
    effects: tuple = ()
    modifiers: tuple = ()
    procs: tuple = ()
    conversions: tuple = ()
    granted: tuple = ()
    skill_bonuses: tuple = ()
    skills: tuple = ()          # SkillState of every skill with points
    _by_id: dict = field(default_factory=dict, compare=False, repr=False)

    def source(self, sid: str) -> SourceInstance | None:
        if not self._by_id:
            self._by_id.update({s.id: s for s in self.sources})
        return self._by_id.get(sid)

    def without(self, source_ids) -> "SourceSet":
        """Copy without the given sources and everything they carry (counterfactuals)."""
        drop = set(source_ids)
        return replace(self, sources=tuple(s for s in self.sources if s.id not in drop),
                       effects=tuple(e for e in self.effects if e.source_id not in drop),
                       modifiers=tuple(m for m in self.modifiers if m.source_id not in drop),
                       procs=tuple(p for p in self.procs if p.source_id not in drop),
                       conversions=tuple(c for c in self.conversions if c.source_id not in drop),
                       granted=tuple(g for g in self.granted if g.source_id not in drop),
                       skill_bonuses=tuple(b for b in self.skill_bonuses if b.source_id not in drop),
                       _by_id={})

    def plus(self, other: "SourceSet") -> "SourceSet":
        return replace(self, sources=self.sources + other.sources,
                       effects=self.effects + other.effects,
                       modifiers=self.modifiers + other.modifiers,
                       procs=self.procs + other.procs,
                       conversions=self.conversions + other.conversions,
                       granted=self.granted + other.granted,
                       skill_bonuses=self.skill_bonuses + other.skill_bonuses,
                       _by_id={})
