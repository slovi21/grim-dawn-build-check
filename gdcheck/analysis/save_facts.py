"""Save facts: the new-code replacement for the .35 `gd_extract.analyse()` document and the
.35 `pruefungen.Kontext` (Task 4a of release .67/.68, "move + rename + de-globalise").

The .35 consumers (rules, devotion/skill/attribute/reroll generators and their helpers) read a
report document `rep` from `G.analyse(doc, db)`, a rule context `P.Kontext` and a set of `G.*`
helpers. This module provides all of them on top of data/savefile.py (parsing),
data/describe.py (readable part), data/index.py (records), domain/fields.py and
domain/activity.py (record helpers) - stdlib only, no module state. Its output was proven
identical to the .35 code for all test saves and is pinned since .68, when the .35 code was
deleted (tests/regression/test_save_facts_golden.py).

Dict-shaped data keeps the legacy keys ("berechnet", "eintraege", "wirkung", "werte", "von",
"nach", "prozent", ...) so a consumer switches by changing its import, not its indexing.
Rename those keys when the last consumer of a structure is ported.

Inventory - what the legacy consumers read
==========================================

analyse() document (`rep`) -> SaveFacts (Mapping with the same keys; typed properties)

  =================================================  ============================  =========================
  rep key                                            read by                       SaveFacts property
  =================================================  ============================  =========================
  charakter.level                                    assess_core.abilities,        .character
                                                     reroll_empfehlungen
  attribute_basis.{physique,cunning,spirit}          assess_core.abilities,        .base_attributes
                                                     attribute.bewerten, P.regel_e
  fortschritt.{attributpunkte,skillpunkte,           P.regel_e, devotionen         .progress
    devotionspunkte}_frei, devotionspunkte_gesamt,   .bewerten, assess_core
    schwierigkeit.zuletzt_gespielt_roh               .schwierigkeit_aus_save
  ausruestung[] / waffensets[].items[]               assess_core.leere_slots       .equipment / .weapon_sets
  berechnet.ausruestung.{summe,pro_item}             assess_core.summe, P.A6       .gear_stats
  berechnet.skills_und_devotionen.{summe,eintraege,  assess_core.summe/ruestung/   .skill_stats
    beitragende}                                     nicht_im_bogen/gepflegte_
                                                     buffs, P.A6
  (all other keys of analyse())                      render_text only (Task 5)     .document[...]
  =================================================  ============================  =========================

pruefungen.Kontext -> RuleContext (build_rule_context(facts, index, sheet, penalty, attributes))

  ======================  =====================  ==================================================
  Kontext                 RuleContext            used by
  ======================  =====================  ==================================================
  doc / db / rep          doc / index / facts    all rules; wirkdauer (db.records, db.schema)
  ist / abzug             sheet / penalty        A3 A4 A6 B1 C1, wirkdauer.anteil, skillpunkte
  attribute               attributes             A5 (main damage type)
  B                       blocks                 -
  skills                  skills                 rules, devotionen, skillpunkte (raw block 8 skills)
  punkte                  points                 A2 D4 E4, eff_level
  boni                    bonuses                eff_level (legacy dict keys alle/mastery/einzeln)
  worn / waffen           worn / weapons         A2 A7 B C, weapon_types
  hotbar                  hotbar                 D3 E5, wirkdauer, skillpunkte
  bindungen               bindings               D E5, wirkdauer, devotionen, skillpunkte
                                                 (block 8 skills + item_skills, .67 fix)
  rec() / name()          record() / name()      all rules
  item_name()             item_name()            A2 B C
  wirkung()               effect_kind()          D E, skillpunkte
  eff_level()             effective_level()      A3 A4 A5, wirkdauer, skillpunkte (form-aware)
  waffen_typen()          weapon_types()         A1 A7, devotionen
  ======================  =====================  ==================================================

Helpers (old -> new; module in brackets when not this one)

  G.WIRKUNG_DAUERHAFT/TOGGLE/ZEITWEISE/PROC/   EFFECT_PERMANENT/TOGGLE/TEMPORARY/PROC/SKILL_ONLY/
    NUR_SKILL/WAFFE/GEGNER, WIRKUNG_IM_BOGEN     WEAPON_CONDITION/ENEMY_DEBUFF, ON_SHEET_EFFECTS
                                                 (same string values as domain.enums.Activity)
  G.DEVOTION_WAFFENBEDINGUNG                   domain.activity.DEVOTION_WEAPON ("zweihand" ->
                                                 "two_handed")
  G._klassen_wirkung / G.skill_wirkung         class_effect / skill_effect
  G.eltern_skill / G._buff_record              parent_skill / buff_record (domain.activity)
  G._leveled_fields / G._numeric_fields        leveled_fields (domain.fields) / numeric_fields
  G._conversions                               conversion_entries (dicts von/nach/prozent)
  G._item_source_records / _getragene_items    item_source_records / worn_items
  G._zweihand_aktiv                            two_handed_active
  G.skill_boni / G.wirksames_level / _stufe    skill_bonuses / effective_level / item_skill_level
  G._item_skill_beitraege                      item_skill_contributions
  G.compute_gear_stats / compute_skill_stats   gear_stats / skill_stats
  G.stat_text                                  stat_text (table: data.stat_labels <- gd_labels)
  G.get_block / EQUIP_SLOTS / short            data.savefile.get_block / data.text.EQUIP_SLOTS/short
  G.STAT_PREFIXES / NICHT_SUMMIERBAR           domain.fields.STAT_PREFIXES / PER_ITEM
  G.name_vorab                                 item_label
  G.analyse                                    build_save_facts(character, index, options)
  P.Kontext                                    RuleContext / build_rule_context
  P.form_von                                   form_of (exact .35 rule: Class from the index "m")
  P.DEVOTION_WAFFE / P._waffen_kategorien      DEVOTION_WEAPON / weapon_categories; codes
                                                 zweihand->two_handed, fernkampf->ranged,
                                                 streitkolben->mace, schild->shield,
                                                 schwert->sword, axt->axe, lantern->lantern
                                                 (P.regel_a1 compares the literal "zweihand")
  P._wert_bei                                  domain.fields.value_at
  P._mit_buff / P.effektquellen                with_buff / effect_sources (analysis.effect_uptime)
  W.anteil / erwartungswert / lueckenanteil    uptime / expected_value / gap_share (effect_uptime)
  W.dauer_feld / chance_feld / PASSIV_KLASSEN  duration_field / chance_field / PASSIVE_CLASSES
    / AUTOMATISCH                                / AUTOMATIC_CLASSES (effect_uptime)
  S.TYPEN / ELEMENTAR / UMWANDLUNG_TYPEN       DAMAGE_TYPES / ELEMENTAL / CONVERSION_TYPES
  S.attributanteil / prozente / hauptschaden   attribute_share / damage_percentages / main_damage
  S.pet_anteil / ist_pet_build / RICHTWERT_*   pet_share / is_pet_build / TARGET_MIN/MAX
                                                 (analysis.damage_types)
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from ..data import stat_labels
from ..data.describe import describe
from ..data.text import EQUIP_SLOTS, short
from ..domain.activity import DEVOTION_WEAPON as DEVOTION_WEAPON_CONDITION
from ..domain.activity import buff_record, class_activity, parent_skill
from ..domain.fields import STAT_PREFIXES, STAT_SKIP, conversions, leveled_fields, per_item

__all__ = [
    "EFFECT_PERMANENT", "EFFECT_TOGGLE", "EFFECT_TEMPORARY", "EFFECT_PROC", "EFFECT_SKILL_ONLY",
    "EFFECT_WEAPON_CONDITION", "EFFECT_ENEMY_DEBUFF", "ON_SHEET_EFFECTS", "DEVOTION_WEAPON",
    "ITEM_PART_KEYS", "SaveFacts", "SaveFactsOptions", "RuleContext", "build_save_facts",
    "build_rule_context", "buff_record", "class_effect", "conversion_entries", "effective_level",
    "form_of", "gear_stats", "item_label", "item_skill_contributions", "item_skill_level",
    "item_source_records", "leveled_fields", "numeric_fields", "parent_skill", "skill_bonuses",
    "skill_effect", "skill_stats", "stat_text", "two_handed_active", "weapon_categories",
    "worn_items",
]

# ---------------------------------------------------------------------------------------------
# Effect kinds (how a skill's values act). The character sheet in game shows permanent bonuses
# and switched-on toggles; temporary buffs, procs and attack-skill modifiers are not on it
# (reference: Cpt Wolf sheet 1.3.0.8 - Physical 11 % although Aspect of the Guardian, Ghoulish
# Hunger and Kraken offered another 15 %). Same values as domain.enums.Activity.
# ---------------------------------------------------------------------------------------------
EFFECT_PERMANENT = "permanent"
EFFECT_TOGGLE = "toggle"                     # acts while switched on
EFFECT_TEMPORARY = "temporary"               # buff with a duration, must be recast
EFFECT_PROC = "proc"                         # triggers only under a condition
EFFECT_SKILL_ONLY = "skill_only"             # boosts only one attack/spell
EFFECT_WEAPON_CONDITION = "weapon_condition"  # weapon condition not met
EFFECT_ENEMY_DEBUFF = "enemy_debuff"         # debuff on enemies, no character value
ON_SHEET_EFFECTS = (EFFECT_PERMANENT, EFFECT_TOGGLE)

# Constellation (display name) -> (weapon category, requirement text). [Wiki single pages; not
# proven complete - constellations not listed have no KNOWN weapon condition]
DEVOTION_WEAPON = {
    "Kraken": ("two_handed", "two-handed weapon (melee or ranged)"),
    "Hydra": ("ranged", "ranged weapon"),
    "Rhowan's Scepter": ("mace", "mace"),
    "Shieldmaiden": ("shield", "shield"),
    "Blades of Nadaan": ("sword", "sword"),
    "Berserker": ("axe", "axe"),
    "Oklaine's Lantern": ("lantern", "scepter, dagger or caster off-hand"),
}

ITEM_PART_KEYS = ("basename", "prefix_name", "suffix_name", "modifier_name", "transmute_name",
                  "relic_name", "relic_bonus", "augment_name", "ascended_name", "ascended_2h_name")
_WEAPON_SLOTS = ("Main hand", "Off hand")


# ---------------------------------------------------------------------------------------------
# Record helpers
# ---------------------------------------------------------------------------------------------

def weapon_categories(main_types, off_types) -> set:
    """Weapon categories of the active weapon set from the record classes (Class/_type)."""
    every = [t for t in list(main_types) + list(off_types) if t]
    out = set()
    if any(t.endswith("2h") for t in main_types):
        out.add("two_handed")
    if any(t.startswith("WeaponHunting") for t in every):
        out.add("ranged")
    if any(t in ("WeaponMelee_Mace", "WeaponMelee_Mace2h") for t in every):
        out.add("mace")
    if "WeaponArmor_Shield" in off_types:
        out.add("shield")
    if any(t in ("WeaponMelee_Sword", "WeaponMelee_Sword2h") for t in every):
        out.add("sword")
    if any(t in ("WeaponMelee_Axe", "WeaponMelee_Axe2h") for t in every):
        out.add("axe")
    if any(t in ("WeaponMelee_Scepter", "WeaponMelee_Dagger", "WeaponArmor_Offhand") for t in every):
        out.add("lantern")
    return out


def numeric_fields(rec) -> dict:
    """Scalar bonus fields of a record (items carry no rank arrays; lists are skipped).
    Conversion fields are never summed - see conversion_entries."""
    out = {}
    if not rec:
        return out
    for key, val in rec.items():
        if key.startswith("_") or key in STAT_SKIP or key.startswith("conversion"):
            continue
        if not key.startswith(STAT_PREFIXES):
            continue
        if isinstance(val, (int, float)) and not isinstance(val, bool) and val:
            out[key] = float(val)
    return out


def conversion_entries(rec, level=None) -> list:
    """Damage conversions of a record as legacy dicts {"von", "nach", "prozent"}."""
    return [{"von": src, "nach": dst, "prozent": pct} for src, dst, pct in conversions(rec, level)]


def item_source_records(item) -> list:
    """Every record path that contributes values to an item (None for empty parts)."""
    return [item.get(k) for k in ITEM_PART_KEYS]


def item_label(item, index) -> str:
    return index.item_name(item) or short(item.get("basename", ""))


def _blocks(doc) -> dict:
    return {b["id"]: b for b in doc["blocks"] if "id" in b}


def worn_items(doc) -> list:
    """Equipped items plus the active weapon set (first set not marked unused, else set 1)."""
    b3 = _blocks(doc).get(3, {})
    worn = list(b3.get("equipment", []))
    for ws in b3.get("weapon_sets", []):
        if not ws.get("unused", False):
            worn += list(ws.get("items", []))
            break
    else:
        if b3.get("weapon_sets"):
            worn += list(b3["weapon_sets"][0].get("items", []))
    return [it for it in worn if it and it.get("basename")]


def two_handed_active(doc) -> bool:
    """A two-handed weapon in the active weapon set (no fallback to set 1 - as .35). Empty slots
    are skipped (.35 raised IndexError on an empty weapon slot; only Kraken nodes ask)."""
    for ws in (_blocks(doc).get(3) or {}).get("weapon_sets", []):
        if not ws.get("unused", False):
            for it in ws.get("items", []):
                parts = ((it or {}).get("basename") or "").rsplit("/", 2)
                if len(parts) > 1 and "2h" in parts[-2]:
                    return True
            return False
    return False


def class_effect(cls: str) -> str | None:
    """Effect kind from the skill class alone (None for modifiers/transmuters)."""
    a = class_activity(cls)
    return a.value if a is not None else None


def skill_effect(rec_name: str, rec, index, doc=None) -> tuple[str, str]:
    """(effect kind, reason) of a skill record. Modifiers/transmuters inherit the kind of their
    base skill; a base skill whose buff is a debuff on enemies (Curse of Frailty -> Vulnerability)
    makes the modifier an enemy debuff."""
    for prefix, (condition, name) in DEVOTION_WEAPON_CONDITION.items():
        if rec_name.startswith(prefix) and condition == "two_handed" and doc is not None \
                and not two_handed_active(doc):
            return EFFECT_WEAPON_CONDITION, f"{name}: two-handed weapon only"
    cls = str((rec or {}).get("Class", ""))
    kind = class_effect(cls)
    if kind is not None:
        return kind, cls
    parent = parent_skill(rec_name, index)
    if parent is None:
        return EFFECT_PERMANENT, f"{cls}, base skill unknown"
    prec = index.record(parent) or {}
    pcls = str(prec.get("Class", ""))
    pbuff = buff_record(prec, index) if prec else None
    bcls = str((pbuff or {}).get("Class", "") or (pbuff or {}).get("_type", ""))
    if any(t in bcls for t in ("Debuf", "Contageous")):
        return (EFFECT_ENEMY_DEBUFF, f"{cls} of {short(parent)}: debuff on enemies "
                                     f"({short(pbuff.get('_recordname', ''))})")
    return class_effect(pcls) or EFFECT_PERMANENT, f"{cls} of {short(parent)} ({pcls})"


def form_of(rec_name: str, index) -> str | None:
    """Shapeshift record an ability belongs to (<form>_skillNN_<name>.dbr next to <form>.dbr).
    Exact .35 rule: the class is read from the index meta fields only (domain.activity.form_of
    also falls back to the record type)."""
    m = re.match(r"^(.*/[^/]+?)_skill\d+[^/]*\.dbr$", rec_name or "")
    if not m:
        return None
    form = m.group(1) + ".dbr"
    cls = str(((getattr(index, "records", {}) or {}).get(form) or {}).get("m", {}).get("Class", ""))
    return form if "Shapeshift" in cls else None


# ---------------------------------------------------------------------------------------------
# Skill ranks: points + gear bonuses
# ---------------------------------------------------------------------------------------------

def skill_bonuses(doc, index) -> dict:
    """+skill bonuses of the worn gear: {"alle": all skills, "mastery": {bar record: n},
    "einzeln": {skill record: n}} (legacy keys)."""
    every, mastery, single = 0, {}, {}
    for item in worn_items(doc):
        for dbr in item_source_records(item):
            rec = index.record(dbr) if dbr else None
            if not rec:
                continue
            v = rec.get("augmentAllLevel")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                every += int(v)
            for n in range(1, 6):
                name, lvl = rec.get(f"augmentMasteryName{n}"), rec.get(f"augmentMasteryLevel{n}")
                if isinstance(name, str) and name and isinstance(lvl, (int, float)):
                    mastery[name] = mastery.get(name, 0) + int(lvl)
                name, lvl = rec.get(f"augmentSkillName{n}"), rec.get(f"augmentSkillLevel{n}")
                if isinstance(name, str) and name and isinstance(lvl, (int, float)):
                    single[name] = single.get(name, 0) + int(lvl)
    return {"alle": every, "mastery": mastery, "einzeln": single}


def effective_level(rec_name: str, points: int, bonuses: dict) -> int:
    """Points + bonuses. Bonuses act only on mastery skills with at least one point - not on
    devotions or the mastery bar. The cap at the ultimate rank follows from the array length."""
    if points < 1 or "/devotion/" in rec_name or "_classtraining" in rec_name:
        return points
    if "/playerclass" not in rec_name:
        return points
    folder = rec_name.rsplit("/", 1)[0]
    m = re.search(r"playerclass(\d+)", rec_name)
    mastery_rec = f"{folder}/_classtraining_class{m.group(1)}.dbr" if m else ""
    return (points + bonuses["alle"] + bonuses["mastery"].get(mastery_rec, 0)
            + bonuses["einzeln"].get(rec_name, 0))


def item_skill_level(value, default: int = 1) -> int:
    """itemSkillLevelEq is mostly a number as text ("2"); formulas -> default."""
    try:
        return max(1, int(float(value)))
    except (TypeError, ValueError):
        return default


def item_skill_contributions(doc, index, points_per_skill: dict) -> list:
    """Values items give through skills: [(entry, conversions)].

    1. Item auras: itemSkillName with a *Toggled* class (Wildhowl Aura: +10 % Health, +15 %
       Armor), rank from itemSkillLevelEq, kind toggle (assumed switched on).
    2. Item modifiers: modifierSkillName<n> acts on modifiedSkillName<n> (medal: Werewolf +40
       OA/DA). Global values count only when the target skill has points and is itself
       permanent/toggle; otherwise they apply to that skill only. The same modifier twice counts
       once. Reference: Cpt Wolf, grimtools NXlyDmLN - Health 16262, DA 3282 exact."""
    out, seen = [], set()
    for item in worn_items(doc):
        for dbr in item_source_records(item):
            irec = index.record(dbr) if dbr else None
            if not irec:
                continue
            iname = item_label(item, index)
            sk = irec.get("itemSkillName")
            srec = index.record(sk) if isinstance(sk, str) and sk else None
            if srec and "Toggled" in str(srec.get("Class", "")) and ("aura", sk) not in seen:
                seen.add(("aura", sk))
                lvl = item_skill_level(irec.get("itemSkillLevelEq") or irec.get("itemSkillLevel"))
                buff = buff_record(srec, index) or srec
                values = leveled_fields(buff, lvl)
                if values:
                    name = index.display(sk) or index.display(buff.get("_recordname", "")) or short(sk)
                    out.append(({"skill": f"{name} (item aura of {iname})", "record": sk,
                                 "punkte": 0, "level": lvl, "class": str(srec.get("Class", "")),
                                 "wirkung": EFFECT_TOGGLE, "grund": "item aura, assumed switched on",
                                 "werte": values, "werte_aus": buff.get("_recordname", sk)},
                                conversion_entries(buff, lvl)))
            for n in range(1, 6):
                mod, target = irec.get(f"modifierSkillName{n}"), irec.get(f"modifiedSkillName{n}")
                if not (isinstance(mod, str) and mod and isinstance(target, str) and target):
                    continue
                if points_per_skill.get(target, 0) < 1 or ("mod", mod) in seen:
                    continue
                seen.add(("mod", mod))
                mrec, trec = index.record(mod), index.record(target)
                if not mrec or not trec:
                    continue
                values, conv = leveled_fields(mrec, 1), conversion_entries(mrec, 1)
                if not values and not conv:
                    continue
                tkind, _ = skill_effect(target, trec, index, doc)
                kind = tkind if tkind in ON_SHEET_EFFECTS else EFFECT_SKILL_ONLY
                tname = index.display(target) or short(target)
                out.append(({"skill": f"{tname}: modifier from {iname}", "record": mod,
                             "punkte": 0, "level": 1, "class": "Skill_Modifier",
                             "wirkung": kind, "grund": f"item modifier for {tname} ({tkind})",
                             "werte": values, "ziel": target},
                            conv))
    return out


# ---------------------------------------------------------------------------------------------
# Calculated part of the document ("berechnet")
# ---------------------------------------------------------------------------------------------

def gear_stats(doc, index) -> dict:
    """Sum of every bonus field of the worn gear (legacy "berechnet.ausruestung")."""
    totals, per_item_rows, unresolved, single, all_conv, skill_mods = {}, [], [], {}, [], []
    for item in worn_items(doc):
        item_sum, item_conv = {}, []
        for dbr in item_source_records(item):
            if not dbr:
                continue
            rec = index.record(dbr)
            if rec is None:
                unresolved.append(dbr)
                continue
            for k, v in numeric_fields(rec).items():
                item_sum[k] = item_sum.get(k, 0.0) + v
            item_conv += conversion_entries(rec)
            # skill modifiers an item/affix (also ascended) puts on one skill
            for n in range(1, 6):
                target, mod = rec.get(f"modifiedSkillName{n}"), rec.get(f"modifierSkillName{n}")
                if isinstance(target, str) and isinstance(mod, str) and target and mod:
                    skill_mods.append({"skill": target.lower(), "modifier": mod.lower(),
                                       "quelle": item_label(item, index)})
        for k, v in item_sum.items():
            if per_item(k):
                single.setdefault(k, []).append((item_label(item, index), v))
                continue
            totals[k] = totals.get(k, 0.0) + v
        name = item_label(item, index)
        entry = {"item": name, "record": item.get("basename"), "werte": item_sum}
        if item_conv:
            entry["umwandlungen"] = item_conv
            all_conv += [dict(c, quelle=name) for c in item_conv]
        per_item_rows.append(entry)
    return {"summe": totals, "pro_item": per_item_rows, "nicht_gefunden": unresolved,
            "anzahl_items": len(per_item_rows), "umwandlungen": all_conv,
            "nicht_summierbar": single, "skill_modifikatoren": skill_mods}


def _is_debuff(rec) -> bool:
    return any(t in str((rec or {}).get("Class", "")) for t in ("Debuf", "Contageous"))


def skill_stats(doc, index, include_devotions: bool = True) -> dict:
    """Bonus fields of every skill and devotion, split by effect kind (legacy
    "berechnet.skills_und_devotionen"). 'summe' is what the in-game sheet shows: permanent
    bonuses and toggles (all toggles assumed on)."""
    skills = _blocks(doc).get(8, {}).get("skills", [])
    bonuses = skill_bonuses(doc, index)
    by_kind, entries, all_conv = {}, [], []
    for s in skills:
        points = s.get("level", 0)
        if points < 1:
            continue
        rec_name = s["skill_name"]
        if "/devotion/" in rec_name and not include_devotions:
            continue
        if "/itemskills" in rec_name and ("potion" in rec_name or "container" in rec_name):
            continue                                         # potion effects
        rec = index.record(rec_name)
        if rec is None:
            continue
        lvl = effective_level(rec_name, points, bonuses)
        values, conv, source = leveled_fields(rec, lvl), conversion_entries(rec, lvl), rec_name
        if not values and not conv:
            buff = buff_record(rec, index)
            if buff:
                values, conv = leveled_fields(buff, lvl), conversion_entries(buff, lvl)
                source = buff.get("_recordname", rec_name)
        if not values and not conv:
            continue
        kind, reason = skill_effect(rec_name, rec, index, doc)
        if source != rec_name and _is_debuff(index.record(source)):
            kind, reason = EFFECT_ENEMY_DEBUFF, f"debuff on enemies ({short(source)})"
        name = index.display(rec_name) or index.display(source) or short(rec_name)
        entry = {"skill": name, "record": rec_name, "punkte": points, "level": lvl,
                 "class": str(rec.get("Class", "")), "wirkung": kind, "grund": reason,
                 "werte": values}
        if source != rec_name:
            entry["werte_aus"] = source
        if conv:
            entry["umwandlungen"] = conv
            all_conv += [dict(c, quelle=f"{name} (Lvl {lvl}, {kind})") for c in conv]
        entries.append(entry)
        pot = by_kind.setdefault(kind, {})
        for k, v in values.items():
            pot[k] = pot.get(k, 0.0) + v

    points_per_skill = {s["skill_name"]: s.get("level", 0) for s in skills}
    for entry, conv in item_skill_contributions(doc, index, points_per_skill):
        entries.append(entry)
        if conv:
            all_conv += [dict(c, quelle=f"{entry['skill']} ({entry['wirkung']})") for c in conv]
        pot = by_kind.setdefault(entry["wirkung"], {})
        for k, v in entry["werte"].items():
            pot[k] = pot.get(k, 0.0) + v

    total = {}
    for kind in ON_SHEET_EFFECTS:
        for k, v in by_kind.get(kind, {}).items():
            total[k] = total.get(k, 0.0) + v
    timed = (EFFECT_TEMPORARY, EFFECT_PROC)
    return {
        "summe": total,
        "nach_wirkung": by_kind,
        "eintraege": entries,
        "skill_boni": bonuses,
        "beitragende": [e for e in entries if e["wirkung"] in ON_SHEET_EFFECTS],
        "auren": [e for e in entries if e["wirkung"] in timed],
        "auren_summe": {k: v for kind in timed for k, v in by_kind.get(kind, {}).items()},
        "aktive_skills_ignoriert": [
            {"skill": e["skill"], "class": e["class"], "felder": len(e["werte"]), "wirkung": e["wirkung"]}
            for e in entries
            if e["wirkung"] in (EFFECT_SKILL_ONLY, EFFECT_WEAPON_CONDITION, EFFECT_ENEMY_DEBUFF)],
        "umwandlungen": all_conv,
    }


def stat_text(field_name: str, value: float, index=None) -> str | None:
    """One bonus line in game wording ('+552% Vitality Decay Damage'): the game's own tag of the
    same name first, then the data.stat_labels table. "" = internal field, None = unknown."""
    if index is not None and hasattr(index, "tag"):
        template = index.tag(field_name)
        if template and "{0}" in template:
            number = f"{value:.0f}" if abs(value - round(value)) < 0.005 else f"{value:.2f}"
            return template.replace("{0}", number).replace("{3}", "").strip()
    return stat_labels.label(field_name, value)


# ---------------------------------------------------------------------------------------------
# SaveFacts
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SaveFactsOptions:
    include_devotions: bool = True      # devotions in skill_stats (analyse(): always True)


@dataclass(frozen=True, eq=False)
class SaveFacts(Mapping):
    """The analyse() document of a save as a read-only mapping (same keys, so `rep.get(...)`
    code works unchanged) plus typed views. Built once per run; never mutate the dicts."""
    doc: dict = field(repr=False)
    document: dict = field(repr=False)

    def __getitem__(self, key):
        return self.document[key]

    def __iter__(self):
        return iter(self.document)

    def __len__(self):
        return len(self.document)

    @property
    def character(self) -> dict:          # charakter: name, level, maennlich, hardcore, ...
        return self.document["charakter"]

    @property
    def base_attributes(self) -> dict:    # attribute_basis: physique, cunning, spirit, ...
        return self.document["attribute_basis"]

    @property
    def progress(self) -> dict:           # fortschritt: free points, difficulty, ...
        return self.document["fortschritt"]

    @property
    def equipment(self) -> list:          # ausruestung (described items, slot names)
        return self.document["ausruestung"]

    @property
    def weapon_sets(self) -> list:        # waffensets
        return self.document["waffensets"]

    @property
    def gear_stats(self) -> dict:         # berechnet.ausruestung
        return self.document["berechnet"]["ausruestung"]

    @property
    def skill_stats(self) -> dict:        # berechnet.skills_und_devotionen
        return self.document["berechnet"]["skills_und_devotionen"]


def build_save_facts(character, index, options: SaveFactsOptions | None = None) -> SaveFacts:
    """SaveFacts of a domain Character (or a parsed save dict) - replaces G.analyse(doc, db)."""
    opts = options or SaveFactsOptions()
    doc = character.doc if hasattr(character, "doc") else character
    document = describe(doc, index)
    document["berechnet"] = {
        "hinweis": "Bonuses summed from database.arz. The savefile itself contains no calculated values.",
        "ausruestung": gear_stats(doc, index),
        "skills_und_devotionen": skill_stats(doc, index, opts.include_devotions),
    }
    return SaveFacts(doc=doc, document=document)


# ---------------------------------------------------------------------------------------------
# RuleContext (replaces pruefungen.Kontext)
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True, eq=False)
class RuleContext:
    facts: SaveFacts
    doc: dict
    index: object
    sheet: dict                 # current values (legacy 'ist')
    penalty: dict               # difficulty resistance penalty (legacy 'abzug')
    attributes: dict            # {"cunning": ..., "spirit": ...} final attributes
    blocks: dict
    skills: list                # raw block 8 skill entries
    points: dict                # skill record -> points (block 8 'skills' only)
    bonuses: dict               # skill_bonuses(): alle / mastery / einzeln
    worn: list                  # [(slot, raw item)] equipment + active weapon set
    weapons: list               # raw items of the active weapon set (may hold None)
    hotbar: set
    bindings: dict              # celestial power -> (bound skill, controller)

    def record(self, name):
        return self.index.record(name) if name else None

    def name(self, rec_name) -> str:
        return self.index.display(rec_name) or short(rec_name)

    def item_name(self, item) -> str:
        return self.index.item_name(item) or short(item.get("basename", ""))

    def effect_kind(self, rec_name) -> str:
        return skill_effect(rec_name, self.record(rec_name), self.index, self.doc)[0]

    def effective_level(self, rec_name) -> int:
        """Rank incl. gear bonuses. Abilities of a shapeshift (werewolf1_skill01_claws) run at the
        rank of the form (tooltip 30.09.2026: Werewolf 8 + 18 = 26 -> Feral Claws rank 26)."""
        form = form_of(rec_name, self.index)
        target = form or rec_name
        return effective_level(target, self.points.get(target, 0), self.bonuses)

    def weapon_types(self) -> tuple[list, list]:
        main, off = [], []
        for j, it in enumerate(self.weapons):
            t = str((self.record((it or {}).get("basename")) or {}).get("Class", ""))
            (main if j == 0 else off).append(t)
        return main, off


def build_rule_context(facts: SaveFacts, index, sheet: dict | None = None, penalty: dict | None = None,
                       attributes: dict | None = None) -> RuleContext:
    """RuleContext of a save - replaces P.Kontext(doc, db, rep, ist, abzug, attribute)."""
    doc = facts.doc
    B = _blocks(doc)
    b8, b3 = B.get(8) or {}, B.get(3) or {}
    skills = list(b8.get("skills", []))
    worn = []
    for i, it in enumerate(b3.get("equipment") or []):
        if it and it.get("basename"):
            worn.append((EQUIP_SLOTS[i] if i < len(EQUIP_SLOTS) else f"Slot {i}", it))
    weapons = []
    for ws in b3.get("weapon_sets") or []:
        if not ws.get("unused", False):
            weapons = list(ws.get("items") or [])
            break
    for j, it in enumerate(weapons):
        if it and it.get("basename"):
            worn.append((_WEAPON_SLOTS[j] if j < 2 else f"Weapon {j}", it))
    # Bindings to form abilities (Feral Claws, Rip and Tear) and item/rune skills are only in
    # block 8 'item_skills' (.67, Cpt Firewolf/Raven/Piercewolf, Skelleton Master).
    bindings = {}
    for s in skills + list(b8.get("item_skills") or []):
        if s.get("autocast_skill_name"):
            bindings[s["autocast_skill_name"]] = (s["skill_name"], s.get("autocast_controller_name", ""))
    return RuleContext(
        facts=facts, doc=doc, index=index, sheet=sheet or {}, penalty=penalty or {},
        attributes=attributes or {}, blocks=B, skills=skills,
        points={s["skill_name"]: s.get("level", 0) for s in skills},
        bonuses=skill_bonuses(doc, index), worn=worn, weapons=weapons,
        hotbar={h.get("skill_name") for h in (B.get(14) or {}).get("hotslots") or [] if h.get("skill_name")},
        bindings=bindings)
