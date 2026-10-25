"""Save + index -> SourceSet (AP2.6).

Every worn record part, every item modifier, every item skill and every skill
with points becomes its own instance. The loader never merges two instances
because their records look alike (P0, ADR-2) - two Gruldir's Cleavers give two
ModifierInstances on Feral Claws, two Entropic Coils give two ProcInstances.
How values combine is decided later by the StackingPolicy of each effect.
"""
from __future__ import annotations

import re

from ..data.text import short
from . import activity as A
from .character import ITEM_PARTS, Character
from .enums import Activity, SourceKind, StackingPolicy
from .fields import conversions, leveled_fields, per_item, value_at
from .sources import (ConversionRule, EffectInstance, GrantedSkill, ModifierInstance,
                      ProcInstance, SkillBonus, SkillState, SourceInstance, SourceSet)

# .62 (product review: 'anight_12a on Belgothian's Carnage'): a part without a display name is named by its role
PART_LABEL = {"prefix_name": "prefix", "suffix_name": "suffix", "modifier_name": "item modifier",
              "transmute_name": "transmute", "relic_name": "component", "relic_bonus": "completion bonus",
              "augment_name": "augment", "ascended_name": "ascended affix", "ascended_2h_name": "ascended affix"}

# "Reduced" effects: only the strongest active source applies (Game Guide; .35 rule B1)
NON_STACKING = {
    "offensiveTotalResistanceReductionPercentMin", "offensiveTotalResistanceReductionAbsoluteMin",
    "offensiveElementalResistanceReductionPercentMin", "offensiveElementalResistanceReductionAbsoluteMin",
    "offensivePhysicalResistanceReductionPercentMin", "offensivePhysicalResistanceReductionAbsoluteMin",
    "offensiveSlowDefensiveReductionMin", "offensiveSlowDefensiveAbilityMin",
    "offensiveSlowOffensiveReductionMin", "offensiveSlowOffensiveAbilityMin",
    "offensiveTotalDamageReductionPercentMin", "offensiveTotalDamageReductionAbsoluteMin",
}
_FLAT_DAMAGE = re.compile(r"^offensive(Slow)?(Physical|Pierce|Fire|Cold|Lightning|Poison|Life|"
                          r"Aether|Chaos|Elemental|Bleeding)(Min|Max|DurationMin)$")
KIND = {"item": SourceKind.ITEM, "affix": SourceKind.AFFIX, "component": SourceKind.COMPONENT,
        "component_bonus": SourceKind.COMPONENT_BONUS, "augment": SourceKind.AUGMENT,
        "ascended": SourceKind.ASCENDED, "transmuter": SourceKind.TRANSMUTER}


def _policy(key: str) -> StackingPolicy:
    return StackingPolicy.MAX_VALUE if key in NON_STACKING else StackingPolicy.ADDITIVE


def _numeric(rec) -> dict:
    """Scalar bonus fields of an item record (items have no rank arrays)."""
    out = {}
    for k, v in leveled_fields(rec, 1).items():
        if isinstance(rec.get(k), (int, float)) and not isinstance(rec.get(k), bool):
            out[k] = v
    return out


def _chance_based(rec, key: str) -> bool:
    """'15% chance of ...' on gear: the field has a <name>Chance companion below 100."""
    stem = key[:-3] if key.endswith("Min") else (key[:-3] if key.endswith("Max") else None)
    if key.endswith("DurationMin"):
        stem = key[: -len("DurationMin")]
    if not stem:
        return False
    c = rec.get(stem + "Chance")
    return isinstance(c, (int, float)) and not isinstance(c, bool) and 0 < c < 100


def _item_skill_level(raw) -> int:
    try:
        return max(1, int(float(raw)))
    except (TypeError, ValueError):
        return 1


class _Builder:
    def __init__(self, ch: Character, index):
        self.ch, self.idx = ch, index
        self.sources, self.effects, self.modifiers = [], [], []
        self.procs, self.convs, self.granted, self.bonuses = [], [], [], []
        self.weapon_items = [w for w in ch.worn if w.slot in ("Main hand", "Off hand")]
        self._eid = 0

    def eff(self, sid, key, value, act, group, scope=None, condition=None):
        self._eid += 1
        self.effects.append(EffectInstance(f"e{self._eid}", sid, key, float(value), act,
                                           _policy(key), group if key not in NON_STACKING else key,
                                           scope, condition))

    # -- items ---------------------------------------------------------------

    def items(self):
        for w in self.ch.worn:
            name = self.idx.item_name(w.item) or short(w.basename)
            for part, kind in ITEM_PARTS:
                rec_name = w.item.get(part)
                if not rec_name:
                    continue
                sid = f"item:{w.instance_id}:{part}"
                rec = self.idx.record(rec_name)
                label = f"{name} ({w.slot})" if part == "basename" else \
                    f"{self.idx.display(rec_name) or PART_LABEL.get(part, short(rec_name))} on {name} ({w.slot})"
                self.sources.append(SourceInstance(sid, KIND[kind], w.instance_id, w.slot, rec_name,
                                                   w.instance_id, label, unresolved=rec is None))
                if rec is None:
                    continue
                scope = f"weapon:{w.slot}" if w.slot in ("Main hand", "Off hand") and \
                    self._is_weapon(w) else None
                for k, v in _numeric(rec).items():
                    if per_item(k):
                        continue
                    act = Activity.PROC if _chance_based(rec, k) else Activity.PERMANENT
                    self.eff(sid, k, v, act, rec_name,
                             scope=scope if _FLAT_DAMAGE.match(k) else None,
                             condition="chance on hit" if act == Activity.PROC else None)
                for src, dst, pct in conversions(rec):
                    self.convs.append(ConversionRule(f"{sid}:conv:{src}>{dst}", sid, "global", None,
                                                     src, dst, pct, Activity.PERMANENT))
                self._skill_bonuses(sid, rec)
                self._item_modifiers(sid, rec, w, name)
                self._item_skill(sid, rec, w, name)

    def _is_weapon(self, w) -> bool:
        cls = str((self.idx.record(w.basename) or {}).get("Class", ""))
        return cls.startswith(("WeaponMelee", "WeaponHunting"))

    def _skill_bonuses(self, sid, rec):
        v = rec.get("augmentAllLevel")
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
            self.bonuses.append(SkillBonus(sid, "all", None, int(v)))
        for n in range(1, 6):
            mname, mlvl = rec.get(f"augmentMasteryName{n}"), rec.get(f"augmentMasteryLevel{n}")
            if isinstance(mname, str) and mname and isinstance(mlvl, (int, float)):
                self.bonuses.append(SkillBonus(sid, "mastery", mname, int(mlvl)))
            sname, slvl = rec.get(f"augmentSkillName{n}"), rec.get(f"augmentSkillLevel{n}")
            if isinstance(sname, str) and sname and isinstance(slvl, (int, float)):
                self.bonuses.append(SkillBonus(sid, "skill", sname, int(slvl)))

    def _item_modifiers(self, sid, rec, w, name):
        for n in range(1, 6):
            mod, target = rec.get(f"modifierSkillName{n}"), rec.get(f"modifiedSkillName{n}")
            if not (isinstance(mod, str) and mod and isinstance(target, str) and target):
                continue
            mrec = self.idx.record(mod)
            msid = f"{sid}:mod{n}"
            label = f"{self.idx.display(target) or short(target)}: modifier from {name} ({w.slot})"
            self.sources.append(SourceInstance(msid, SourceKind.ITEM_MODIFIER, w.instance_id, w.slot,
                                               mod, w.instance_id, label, unresolved=mrec is None))
            if mrec is None:
                continue
            f = leveled_fields(mrec, 1)
            self.modifiers.append(ModifierInstance(
                msid, msid, target, mod, tuple(sorted(f.items())), tuple(conversions(mrec, 1)),
                wd_pct=float(value_at(mrec.get("weaponDamagePct"), 1) or 0.0),
                combo_level=float(f.get("skillComboChargeLevel", 0.0)),
                combo_duration=float(f.get("skillComboChargeDuration", 0.0))))

    def _item_skill(self, sid, rec, w, name):
        sk = rec.get("itemSkillName")
        if not (isinstance(sk, str) and sk):
            return
        srec = self.idx.record(sk)
        lvl = _item_skill_level(rec.get("itemSkillLevelEq") or rec.get("itemSkillLevel"))
        gsid = f"{sid}:skill"
        label = f"{self.idx.display(sk) or short(sk)} ({name}, {w.slot})"
        self.sources.append(SourceInstance(gsid, SourceKind.GRANTED_SKILL, w.instance_id, w.slot, sk,
                                           w.instance_id, label, unresolved=srec is None))
        if srec is None:
            return
        ctrl = rec.get("itemSkillAutoController")
        if isinstance(ctrl, str) and ctrl:
            c = (getattr(self.idx, "controller", {}) or {}).get(ctrl) or {}
            chance = float(c.get("chanceToRun") or _chance_from_name(ctrl)) / 100
            cd = value_at((srec or {}).get("skillCooldownTime"), lvl)
            self.procs.append(ProcInstance(gsid, gsid, sk, label, str(c.get("triggerType") or ctrl),
                                           chance, cd, lvl, trigger_param=c.get("triggerParam")))
            return
        cls = str(srec.get("Class", ""))
        if "Toggled" in cls:
            buff = A.buff_record(srec, self.idx) or srec
            self.granted.append(GrantedSkill(gsid, gsid, sk, label, lvl, Activity.TOGGLE))
            # identical item auras: both instances stay, only one aura is active (ASSUMED)
            for k, v in leveled_fields(buff, lvl).items():
                self._eid += 1
                self.effects.append(EffectInstance(f"e{self._eid}", gsid, k, v, Activity.TOGGLE,
                                                   StackingPolicy.MAX_VALUE,
                                                   k if k in NON_STACKING else f"granted:{sk}"))
            for src, dst, pct in conversions(buff, lvl):
                self.convs.append(ConversionRule(f"{gsid}:conv:{src}>{dst}", gsid, "global", None,
                                                 src, dst, pct, Activity.TOGGLE))
        else:
            self.granted.append(GrantedSkill(gsid, gsid, sk, label, lvl, Activity.SKILL_ONLY))

    # -- skills --------------------------------------------------------------

    def effective_level(self, rec_name: str, points: int) -> int:
        if points < 1 or "/devotion/" in rec_name or "_classtraining" in rec_name \
                or "/playerclass" not in rec_name:
            return points
        folder = rec_name.rsplit("/", 1)[0]
        m = re.search(r"playerclass(\d+)", rec_name)
        mastery = f"{folder}/_classtraining_class{m.group(1)}.dbr" if m else ""
        lvl = points
        for b in self.bonuses:
            if b.kind == "all" or (b.kind == "mastery" and b.target == mastery) \
                    or (b.kind == "skill" and b.target == rec_name):
                lvl += b.levels
        return lvl

    def skills(self) -> list:
        states = []
        pts = self.ch.skills
        for rec_name, points in sorted(pts.items()):
            form = A.form_of(rec_name, self.idx)
            if points < 1 and not form and "defaultweaponattack" not in rec_name:
                continue
            if "/itemskills" in rec_name and ("potion" in rec_name or "container" in rec_name):
                continue
            rec = self.idx.record(rec_name)
            if rec is None:
                self.sources.append(SourceInstance(f"skill:{rec_name}", SourceKind.SKILL, rec_name, None,
                                                   rec_name, None, short(rec_name), unresolved=True))
                continue
            if "/devotion/" in rec_name and rec_name.endswith("_skill.dbr"):
                lvl = max(1, self.ch.devotion_levels.get(rec_name, 1)) if points > 0 else 0
            else:
                lvl = self.effective_level(form, pts.get(form, 0)) if form else \
                    self.effective_level(rec_name, points)
            act, why = A.skill_activity(rec_name, rec, self.idx, self.weapon_items)
            values_rec = rec
            fields = leveled_fields(rec, lvl)
            conv = conversions(rec, lvl)
            if not fields and not conv:
                buff = A.buff_record(rec, self.idx)
                if buff:
                    values_rec = buff
                    fields, conv = leveled_fields(buff, lvl), conversions(buff, lvl)
                    if any(t in str(buff.get("Class", "")) for t in ("Debuf", "Contageous")):
                        act, why = Activity.ENEMY_DEBUFF, f"debuff on enemies ({short(buff.get('_recordname', ''))})"
            parent = A.parent_skill(rec_name, self.idx)
            kind = SourceKind.DEVOTION if "/devotion/" in rec_name else SourceKind.SKILL
            sid = f"{kind.value}:{rec_name}"
            label = self.idx.display(rec_name) or short(rec_name)
            states.append(SkillState(rec_name, label, points, lvl, act, why, str(rec.get("Class", "")),
                                     rec_name in self.ch.hotbar or (parent in self.ch.hotbar if parent else False),
                                     parent, form, values_rec.get("_recordname", rec_name)))
            if points < 1:
                continue          # form abilities and the default attack carry no own stats
            self.sources.append(SourceInstance(sid, kind, rec_name, None, rec_name, None,
                                               f"{label} (rank {lvl})"))
            for k, v in fields.items():
                self.eff(sid, k, v, act, parent or rec_name)
            stage = "skill" if act == Activity.SKILL_ONLY else "global"
            for src, dst, pct in conv:
                self.convs.append(ConversionRule(f"{sid}:conv:{src}>{dst}", sid, stage,
                                                 (parent or rec_name) if stage == "skill" else None,
                                                 src, dst, pct, act))
        return states

    def modifier_effects(self, states):
        """Global values of item modifiers count when the target skill has points and
        works permanently/as toggle (form, aura); otherwise only for that skill."""
        by_rec = {s.record: s for s in states}
        for m in self.modifiers:
            target = by_rec.get(m.target_skill)
            if target is None or target.points < 1:
                act = Activity.SKILL_ONLY
            else:
                # temporary buffs and enemy debuffs keep their nature (Blood of Dreeg +DA
                # only while the buff runs; Curse of Frailty -DA on enemies)
                act = target.activity if target.activity in (
                    Activity.PERMANENT, Activity.TOGGLE, Activity.TEMPORARY, Activity.ENEMY_DEBUFF) \
                    else Activity.SKILL_ONLY
            for k, v in m.fields:
                self.eff(m.source_id, k, v, act, f"mod:{m.target_skill}")
            stage = "global" if act in (Activity.PERMANENT, Activity.TOGGLE) else "skill"
            for src, dst, pct in m.conversions:
                self.convs.append(ConversionRule(f"{m.source_id}:conv:{src}>{dst}", m.source_id, stage,
                                                 None if stage == "global" else m.target_skill,
                                                 src, dst, pct, act))

    def celestial_procs(self):
        for power, (bound, ctrl) in sorted(self.ch.bindings.items()):
            rec = self.idx.record(power)
            if rec is None or self.ch.skills.get(power, 0) < 1:
                continue            # binding left over after a devotion respec (.35 rule)
            lvl = max(1, self.ch.devotion_levels.get(power, 1))
            c = (getattr(self.idx, "controller", {}) or {}).get(ctrl) or {}
            chance = float(c.get("chanceToRun") or _chance_from_name(ctrl)) / 100
            sid = f"devotion:{power}"
            if not any(s.id == sid for s in self.sources):
                self.sources.append(SourceInstance(sid, SourceKind.DEVOTION, power, None, power, None,
                                                   self.idx.display(power) or short(power)))
            self.procs.append(ProcInstance(f"{sid}:proc", sid, power, self.idx.display(power) or short(power),
                                           str(c.get("triggerType") or ctrl), chance,
                                           value_at(rec.get("skillCooldownTime"), lvl), lvl, bound_to=bound,
                                           trigger_param=c.get("triggerParam")))


def _chance_from_name(ctrl: str) -> float:
    m = re.search(r"_(\d+)%", ctrl or "")
    return float(m.group(1)) if m else 0.0


def build_sources(ch: Character, index) -> SourceSet:
    b = _Builder(ch, index)
    b.items()                       # also collects the skill bonuses of the gear
    states = b.skills()
    b.modifier_effects(states)
    b.celestial_procs()
    return SourceSet(tuple(b.sources), tuple(b.effects), tuple(b.modifiers), tuple(b.procs),
                     tuple(b.convs), tuple(b.granted), tuple(b.bonuses), tuple(states))


def effective_level(ch: Character, index, sources: SourceSet, rec_name: str, points: int) -> int:
    """Effective rank with the skill bonuses of a (counterfactual) source set."""
    b = _Builder(ch, index)
    b.bonuses = list(sources.skill_bonuses)
    return b.effective_level(rec_name, points)
