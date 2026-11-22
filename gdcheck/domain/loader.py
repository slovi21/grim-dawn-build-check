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
from ..mechanics import requirements as RQ
from . import activity as A
from . import forms as FM
from . import item_values as IV
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


def _chance_based(rec, key: str, level: int = 1) -> bool:
    """'15% chance of ...' on gear: the field has a <name>Chance companion below 100 (a set: at its piece count)."""
    stem = key[:-3] if key.endswith("Min") else (key[:-3] if key.endswith("Max") else None)
    if key.endswith("DurationMin"):
        stem = key[: -len("DurationMin")]
    if not stem:
        return False
    c = value_at(rec.get(stem + "Chance"), level)
    return c is not None and 0 < c < 100


SET_FOLDER = "/lootsets/"


def set_members(rec) -> list:
    m = (rec or {}).get("setMembers") or []
    return [x for x in (m if isinstance(m, list) else [m]) if isinstance(x, str) and x]


def set_listing(index) -> dict:
    """{member item record: (set records that list it, ...)} over all set records of the index, cached on the index.
    Needed because patch 1.3.1 gave some awakened items an itemSetName that points to the NORMAL set, while only the
    awakened set record lists them (The Magelord c104, Festerblaze c203, Luminari Regalia c106)."""
    cached = getattr(index, "_set_listing", None)
    if cached is not None:
        return cached
    out: dict = {}
    for name in list(getattr(index, "records", {}) or {}):
        if SET_FOLDER not in name:
            continue
        for member in set_members(index.record(name)):
            out.setdefault(member, ())
            out[member] = tuple(sorted(set(out[member]) | {name}))
    try:
        index._set_listing = out
    except AttributeError:          # an index object without a writable __dict__: compute again next time
        pass
    return out


def worn_sets(index, basenames) -> dict:
    """{set record: {different worn members}} - the piece count of a set is the size of its set: an item counts for
    every set record that lists it; an item no set record lists counts for its itemSetName (ASSUMED; an index before
    schema 11 has no set records - the set stays unresolved)."""
    listing = set_listing(index)
    out: dict = {}
    for base in basenames:
        if not base:
            continue
        sets = listing.get(base)
        if not sets:                    # only a set folder counts: some items name records/storyelements/signs/signset.dbr
            s = (index.record(base) or {}).get("itemSetName")
            sets = (s,) if isinstance(s, str) and SET_FOLDER in s.replace("\\", "/").lower() else ()
        for s in sets:
            out.setdefault(s, set()).add(base)
    return out


def _item_skill_level(raw, item_level=None) -> int:
    """The rank of the skill an item grants: `itemSkillLevelEq` is a number ('2') or an equation of the item's level
    ('itemLevel/4+1' - 839 records of 1.3.1.2, almost every monster infrequent; M18: it was read as rank 1 before, Cpt Magic's
    Ring of Flame is rank 17 at item level 65, not 1). Truncated like the skill ranks; 1 when nothing can be read."""
    try:
        return max(1, int(float(raw)))
    except (TypeError, ValueError):
        pass
    if isinstance(raw, str) and isinstance(item_level, (int, float)):
        value = RQ.evaluate(raw.replace("itemlevel", "itemLevel"), {"itemLevel": float(item_level)})
        if value is not None:
            return max(1, int(value + 1e-9))
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
            rolled = IV.rolled_values(self.idx, w.item)      # M16: the values the game rolls from the item's seed
            relic = IV.is_relic(self.idx.record(w.basename))
            for part, kind in ITEM_PARTS:
                rec_name = w.item.get(part)
                if not rec_name:
                    continue
                if relic and part == "modifier_name":
                    # M17 (found with the video check of 21.11.2026): the crafting modifier of a RELIC gives nothing in the
                    # game and takes no draw of its roll (item_values._roll). Three exact cases, each the whole gap left in
                    # its sheet: Cpt Wolf's Electrocute +17 (the modifier's rolled value), Cpt Piercewolf's Bleeding
                    # Resistance +4 and Cpt Blade's Health +88 (rolled 57 x 1.54). Any other item keeps its modifier
                    # (helmet +52 Health, Physique +4 % at Cpt Piercewolf: exact).
                    continue
                sid = f"item:{w.instance_id}:{part}"
                rec = IV.with_rolled(self.idx.record(rec_name), rolled, part)
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

    def item_sets(self):
        """Set bonuses (gap 2, index schema 11): per set the number of DIFFERENT worn members (two equal set
        rings count once - ASSUMED, the in-game set tooltip lists each member once). The set record's arrays are
        indexed by that number (value_at: index n - 1): values, conversions, +skill/+mastery ranks; skill
        modifiers from the piece count itemSkillModifierControl switches on (else all pieces); the set's own
        item skill (proc or aura) where itemSkillLevel is above 0. A set record missing in the index (index
        before schema 11) is listed as unresolved, never guessed. Which set an item belongs to: worn_sets()."""
        worn = worn_sets(self.idx, [w.basename for w in self.ch.worn])
        for set_rec, bases in sorted(worn.items()):
            srec = self.idx.record(set_rec)
            members = set_members(srec)
            n = len(bases)
            name = self.idx.display(set_rec) or short(set_rec)
            sid = f"set:{set_rec}"
            self.sources.append(SourceInstance(sid, SourceKind.SET, set_rec, None, set_rec, None,
                                               f"{name} set ({n} of {len(members) or '?'} pieces)",
                                               unresolved=srec is None))
            if srec is None or n < 1:
                continue
            for k, v in leveled_fields(srec, n).items():
                if per_item(k) or k == "augmentAllLevel":
                    continue
                act = Activity.PROC if _chance_based(srec, k, n) else Activity.PERMANENT
                self.eff(sid, k, v, act, set_rec, condition="chance on hit" if act == Activity.PROC else None)
            for src, dst, pct in conversions(srec, n):
                self.convs.append(ConversionRule(f"{sid}:conv:{src}>{dst}", sid, "global", None,
                                                 src, dst, pct, Activity.PERMANENT))
            all_lv = value_at(srec.get("augmentAllLevel"), n)
            if all_lv:
                self.bonuses.append(SkillBonus(sid, "all", None, int(all_lv)))
            for i in range(1, 6):
                for kind, nf, lf in (("mastery", f"augmentMasteryName{i}", f"augmentMasteryLevel{i}"),
                                     ("skill", f"augmentSkillName{i}", f"augmentSkillLevel{i}")):
                    target, lv = srec.get(nf), value_at(srec.get(lf), n)
                    if isinstance(target, str) and target and lv:
                        self.bonuses.append(SkillBonus(sid, kind, target, int(lv)))
            control = srec.get("itemSkillModifierControl")
            control = control if isinstance(control, list) else ([control] if control else [])
            on = next((i + 1 for i, c in enumerate(control) if c), len(members) or n)
            if n >= on:
                self._item_modifiers(sid, srec, None, name, where=f"{name} set")
            sk = srec.get("itemSkillName")
            lv = value_at(srec.get("itemSkillLevel"), n)
            if isinstance(sk, str) and sk and lv and lv >= 1:
                self._grant(f"{sid}:skill", sk, int(lv), srec.get("itemSkillAutoController"),
                            f"{self.idx.display(sk) or short(sk)} ({name} set)", set_rec, None)

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

    def _item_modifiers(self, sid, rec, w, name, where=None):
        """Skill modifiers of an item part (w) or of a set (w None, `where` names the set)."""
        owner, slot = (w.instance_id, w.slot) if w is not None else (sid, None)
        for n in range(1, 6):
            mod, target = rec.get(f"modifierSkillName{n}"), rec.get(f"modifiedSkillName{n}")
            if not (isinstance(mod, str) and mod and isinstance(target, str) and target):
                continue
            mrec = self.idx.record(mod)
            msid = f"{sid}:mod{n}"
            label = f"{self.idx.display(target) or short(target)}: modifier from {where or f'{name} ({slot})'}"
            self.sources.append(SourceInstance(msid, SourceKind.ITEM_MODIFIER, owner, slot,
                                               mod, owner if w is not None else None, label,
                                               unresolved=mrec is None))
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
        base = self.idx.record(getattr(w, "basename", None)) or {}
        lvl = _item_skill_level(rec.get("itemSkillLevelEq") or rec.get("itemSkillLevel"),
                                rec.get("itemLevel") or base.get("itemLevel"))
        self._grant(f"{sid}:skill", sk, lvl, rec.get("itemSkillAutoController"),
                    f"{self.idx.display(sk) or short(sk)} ({name}, {w.slot})", w.instance_id, w.slot)

    def _grant(self, gsid, sk, lvl, ctrl, label, owner, slot):
        """The skill an item part or a set grants: a proc (auto controller), a toggled aura or a skill to use."""
        srec = self.idx.record(sk)
        self.sources.append(SourceInstance(gsid, SourceKind.GRANTED_SKILL, owner, slot, sk,
                                           owner if slot else None, label, unresolved=srec is None))
        if srec is None:
            return
        usable, _why = A.item_skill_weapon(sk, self.weapon_items, self.idx)
        if not usable:
            # M18 (d): the held weapons do not satisfy the skill's weapon flags (Blazing Charge on a bow, Bear Claw on a
            # one-hander): it is no action, no proc and no aura - the same state as a skill whose weapon condition is unmet
            self.granted.append(GrantedSkill(gsid, gsid, sk, label, lvl, Activity.WEAPON_CONDITION_UNMET))
            return
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
            # B2 (M17, decision 71): an aura an ITEM grants acts once per item. Two items with the same aura both act
            # (Cpt Magic, video of 21.11.2026: main hand off, Fire 2857 -> 2467 = -390, not -315: both "Burning Weapons"
            # flints count, 75 points of Fire and of Burn each). The effect group is the skill and the owner (item
            # instance or set), so one item's aura counts once, however many of its parts grant it. 'Reduced' effects
            # (NON_STACKING) stay one group per key: only the strongest source applies. Auras of skills are built in
            # skills() (one group per skill) and are not touched.
            for k, v in leveled_fields(buff, lvl).items():
                self._eid += 1
                self.effects.append(EffectInstance(f"e{self._eid}", gsid, k, v, Activity.TOGGLE,
                                                   StackingPolicy.MAX_VALUE,
                                                   k if k in NON_STACKING else f"granted:{sk}:{owner}"))
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
        cap = A.transmuter_cap(self.idx.record(rec_name))
        return lvl if cap is None else min(lvl, cap)

    def _form_switched_off(self, rec_name, form, parent, levelled, counted) -> bool:
        """True for a skill that belongs to a levelled form that does not count and to no form that does. A skill belongs to
        a form when it is the form, an ability of it (`form`) or a modifier of it (`parent`, `subSkillName`, `skillDependancy`
        of the design data, also through an ability: Voracity -> Feral Claws -> Werewolf). Form of the Beast depends on both
        forms: it stays on whichever counts."""
        link = A.skill_link(rec_name, self.idx)          # E2: the tables where the design data is not read (online)
        cands = {rec_name, form, parent, link["sub_skill"], *link["dependency"]}
        cands |= {A.form_of(c, self.idx) for c in cands if c}
        belongs = {c for c in cands if c in levelled}
        return bool(belongs) and counted.record not in belongs

    def _counting_form(self, rec_name, parent, counted):
        """E2: the parent of a modifier that depends on several forms (Form of the Beast: Wereraven, Werewolf) is the form that
        counts (domain.forms), not the first one of its record - a name only: the group of its values changes, and both forms
        are notDispelable, so the sheet is the same. Any other parent stays (Voracity: Feral Claws by `subSkillName`)."""
        link = A.skill_link(rec_name, self.idx)
        if counted is None or link["sub_skill"] or parent == counted.record or len(link["dependency"]) < 2:
            return parent
        return counted.record if counted.record in link["dependency"] and parent in link["dependency"] else parent

    def skills(self) -> list:
        states = []
        pts = self.ch.skills
        # M19 (decision 67): with several levelled forms only the one with more points counts (domain.forms); the others,
        # their abilities and their modifiers are switched off (FORM_ONLY: not on the sheet, not in the fight)
        levelled = FM.levelled_forms(pts, self.idx)
        counted = FM.choose(levelled)
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
            parent = self._counting_form(rec_name, parent, counted)
            kind = SourceKind.DEVOTION if "/devotion/" in rec_name else SourceKind.SKILL
            sid = f"{kind.value}:{rec_name}"
            label = self.idx.display(rec_name) or short(rec_name)
            switched_off = len(levelled) > 1 and self._form_switched_off(rec_name, form, parent, levelled, counted)
            if switched_off:
                act, why = Activity.FORM_ONLY, f"not counted: {short(counted.record)} has more points (or wins a tie)"
            states.append(SkillState(rec_name, label, points, lvl, act, why, str(rec.get("Class", "")),
                                     rec_name in self.ch.hotbar or (parent in self.ch.hotbar if parent else False),
                                     parent, form, values_rec.get("_recordname", rec_name)))
            if points < 1:
                continue          # form abilities and the default attack carry no own stats
            self.sources.append(SourceInstance(sid, kind, rec_name, None, rec_name, None,
                                               f"{label} (rank {lvl})"))
            for k, v in fields.items():
                # B6 (M17): Vindictive Flame is a proc skill with a permanent part (speed, regeneration, retaliation)
                self.eff(sid, k, v, Activity.PERMANENT if A.permanent_part(rec, k) and not switched_off else act,
                         parent or rec_name)
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
    b.item_sets()                   # set bonuses at the worn piece count (before the skill ranks)
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
