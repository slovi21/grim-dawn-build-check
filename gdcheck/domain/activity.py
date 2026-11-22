"""How a skill or effect works: permanent, toggle, temporary, proc ... (port of the
.35 skill_wirkung, the basis of 'character sheet = permanent + toggles')."""
from __future__ import annotations

import re

from ..data import design_data as DD
from ..data.text import short
from . import skill_facts as SF
from .enums import Activity

# Devotions with a weapon condition the index does not carry (wiki single pages;
# not proven complete - missing entries lead to too high values).
DEVOTION_WEAPON = {
    "records/skills/devotion/tier2_21": ("two_handed", "Kraken"),
}
# B5 (M17): the player skills the game database marks `dualWieldOnly = 1` (game data 1.3.1.2, read from database.arz): they
# act only while two one-handed weapons are held. The index keeps no such field (it is not part of schema 12; adding it is a
# schema change), so the records are listed here - complete for 1.3.1.2: every record of records/skills/playerclass* with the
# field. The item skills with the field (deadlymeans, dunefiendblades, fatebreakerwill, scorvguile, relic_bloodbath, ...) are
# skills an item grants; the loader does not apply a weapon condition to them. A patch that adds records needs this list
# read again.
DUAL_WIELD_ONLY = {
    "records/skills/playerclass04/wpattack0.dbr": "Dual Blades",
    "records/skills/playerclass04/wpattack1.dbr": "Belgothian's Shears",
    "records/skills/playerclass04/wpattack2.dbr": "Amarasta's Quick Cut",
    "records/skills/playerclass04/wpattack3.dbr": "Whirling Death",
    "records/skills/playerclass04/wpattack5.dbr": "Execution",
    "records/skills/playerclass04/nightbladeenchant1b.dbr": "Breath of Belgothian",
    "records/skills/playerclass10/passive01.dbr": "Implements of War",
    "records/skills/playerclass10/rallyingcry1b.dbr": "Frenzied Cry",
}
# B3 (M17): `subSkillName` of the two modifiers that change one granted skill of a form only (game data 1.3.1.2: exactly these
# two records have the field). The design data carries it (data.design_data.skill_links), but the online version does not read
# the design data (patch_bundle.WEB_PREFIXES), so the sheet would differ there: this table is the fallback, equal to the data.
SUB_SKILL = {
    "records/skills/playerclass10/werewolf2.dbr": "records/skills/playerclass10/werewolf1_skill01_claws.dbr",       # Voracity
    "records/skills/playerclass10/wereraven2.dbr": "records/skills/playerclass10/wereraven1_skill01_icicles.dbr",
}
# E2: `skillDependancy` of the skills the game database gives one (game data 1.3.1.2: these 16 records): the base skills a
# modifier or transmuter changes, in the order of the record. Like SUB_SKILL it is the fallback for the online version,
# which does not read the design data (patch_bundle.WEB_PREFIXES) - without it Ulzuin's Chosen (passive2) had Blast Shield
# as its parent online (naming convention: passive1) and stunjacks1 locally, and Form of the Beast (passive04) none.
# Equal to the design data (tests/unit/test_e2_skill_links.py). KG-46: where a modifier has several, the first is taken.
SKILL_DEPENDENCY = {
    "records/skills/playerclass02/canisterbomb1b.dbr": ("records/skills/playerclass02/canisterbomb1.dbr",),
    "records/skills/playerclass02/canisterbomb2.dbr": ("records/skills/playerclass02/canisterbomb1.dbr",),
    "records/skills/playerclass02/passive2.dbr": (
        "records/skills/playerclass02/stunjacks1.dbr",
        "records/skills/playerclass02/grenado1.dbr",
        "records/skills/playerclass02/canisterbomb1.dbr",
    ),
    "records/skills/playerclass04/wpattack1.dbr": ("records/skills/playerclass04/wpattack0.dbr",),
    "records/skills/playerclass04/wpattack2.dbr": ("records/skills/playerclass04/wpattack0.dbr",),
    "records/skills/playerclass04/wpattack3.dbr": ("records/skills/playerclass04/wpattack0.dbr",),
    "records/skills/playerclass04/wpattack4.dbr": (
        "records/skills/playerclass04/wpattack1.dbr",
        "records/skills/playerclass04/wpattack2.dbr",
        "records/skills/playerclass04/wpattack3.dbr",
    ),
    "records/skills/playerclass04/wpattack5.dbr": ("records/skills/playerclass04/wpattack0.dbr",),
    "records/skills/playerclass06/savagestrike2.dbr": ("records/skills/playerclass06/savagestrike1.dbr",),
    "records/skills/playerclass06/weaponpool01.dbr": ("records/skills/playerclass06/passive01.dbr",),
    "records/skills/playerclass06/weaponpool02.dbr": ("records/skills/playerclass06/passive01.dbr",),
    "records/skills/playerclass10/passive04.dbr": (
        "records/skills/playerclass10/wereraven1.dbr",
        "records/skills/playerclass10/werewolf1.dbr",
    ),
    "records/skills/playerclass10/wereraven2.dbr": ("records/skills/playerclass10/wereraven1.dbr",),
    "records/skills/playerclass10/wereraven3.dbr": ("records/skills/playerclass10/wereraven1.dbr",),
    "records/skills/playerclass10/werewolf2.dbr": ("records/skills/playerclass10/werewolf1.dbr",),
    "records/skills/playerclass10/werewolf3.dbr": ("records/skills/playerclass10/werewolf1.dbr",),
}
MODIFIER_CLASSES = ("Skill_Modifier", "Skill_Transmuter")
_PARENT = re.compile(r"^(.*?)(\d+)([a-z]?)$")
_MOD_STEM = re.compile(r"^(.*?)_mod\d*$")
_FORM = re.compile(r"^(.*/[^/]+?)_skill\d+[^/]*\.dbr$")


def cast_buff(rec_name: str, cls: str) -> bool:
    """M18 (a): a `Skill_BuffSelfDuration` of a player class is a buff the player casts on the hotbar, with a duration and a
    cooldown (Pneumatic Burst: 60 s every 8 s; Overguard, Mirror of Ereoctes, Call of the Grave, Ascension). The class name alone
    says PROC, which is right for the same class on a devotion power or an item (Ghoulish Hunger, Giant's Blood: they fire on a
    trigger), so the record's folder decides."""
    return cls == "Skill_BuffSelfDuration" and "/playerclass" in (rec_name or "")


def class_activity(cls: str, rec_name: str = "") -> Activity | None:
    if cast_buff(rec_name, cls):
        return Activity.TEMPORARY
    if cls == "Skill_Mastery":
        return Activity.PERMANENT
    if cls.startswith("Skill_PassiveOn"):
        return Activity.PROC
    if "Passive" in cls:
        return Activity.PERMANENT
    if "Toggled" in cls or cls == "Skill_Shapeshift":
        return Activity.TOGGLE
    if "BuffSelfDuration" in cls or "OnHit" in cls or "OnAttack" in cls:
        return Activity.PROC
    if "Buff" in cls:
        return Activity.TEMPORARY
    if cls in ("Skill_Modifier", "Skill_Transmuter"):
        return None
    return Activity.SKILL_ONLY


def permanent_part(rec, key: str) -> bool:
    """B6 (M17): the part of a proc skill the sheet shows all the time. A Skill_OnHitAttackRadius (Vindictive Flame, Counter
    Strike) is a buff you keep up that also fires an attack on a hit: its attack and stun fields (`offensive*`) are the
    proc, its character, retaliation and defensive fields act permanently. Cpt Magic (video of 21.11.2026): attack speed 140,
    cast speed 194, run speed 135, regeneration 468.73 and Fire retaliation 6,816 add up only with Vindictive Flame's
    +20 % speed, 274 regeneration and 339 retaliation (the buff icon is on the bar before any fight)."""
    return str((rec or {}).get("Class", "")) == "Skill_OnHitAttackRadius" \
        and key.startswith(("character", "retaliation", "defensive"))


def transmuter_cap(rec) -> int | None:
    """B7 (M17): the highest rank of a transmuter - `skillUltimateLevel`, else `skillMaxLevel` of its record (Blight of
    Ch'thon and Frenzied Cry: 1; Cpt Wolf, video of 21.11.2026: 1/1 in the skill window, the check had 2 from '+1 to all
    Berserker skills'). None for any other skill and for a record without either field."""
    if str((rec or {}).get("Class", "")) != "Skill_Transmuter":
        return None
    for key in ("skillUltimateLevel", "skillMaxLevel"):
        v = rec.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 1:
            return int(v)
    return None


def _is_modifier(rec) -> bool:
    return str((rec or {}).get("Class", "")) in MODIFIER_CLASSES


def skill_link(rec_name: str, index) -> dict:
    """{"dependency": (base skills), "sub_skill": record or None} of a skill: the design data (data.design_data.skill_links) where
    it is read, else the tables SUB_SKILL and SKILL_DEPENDENCY, which equal it (E2: the online version reads no design data)."""
    link = DD.skill_links(index).get(rec_name) or {}
    return {"dependency": tuple(link.get("dependency") or SKILL_DEPENDENCY.get(rec_name, ())),
            "sub_skill": link.get("sub_skill") or SUB_SKILL.get(rec_name)}


def parent_skill(rec_name: str, index) -> str | None:
    """The skill a modifier/transmuter changes (B3, M17: a modifier acts only for its own skill).

    A Skill_Modifier / Skill_Transmuter takes, in this order: (1) `subSkillName` - the one granted skill it changes
    (Voracity -> Feral Claws, not the Werewolf form); (2) the first base skill of `skillDependancy` (Form of the Beast ->
    Werewolf); both fields are in the design data (data.design_data.skill_links), not in the index; the tables SUB_SKILL and
    SKILL_DEPENDENCY back them up where the design data is not read, the online version (E2, `skill_link`); (3) the naming
    convention (witchfire2 -> witchfire1, werewolf1b -> werewolf1) - a candidate that is a modifier itself is no base skill
    (ringofsteel_mod0 once found ringofsteel_mod1); (4) the name without its suffix: ringofsteel_mod1 -> ringofsteel,
    chillingsurge2 -> chillingsurge.
    Any other skill (a weapon pool attack, a secondary skill) keeps the naming convention only. None when nothing is
    found: the caller then has no base skill. With several base skills the first one found in the index is taken (KG-46:
    the game's own choice is open - Ulzuin's Chosen names stunjacks1, grenado1 and canisterbomb1)."""
    folder, _, file = rec_name.rpartition("/")
    stem = file[:-4] if file.endswith(".dbr") else file
    modifier = _is_modifier(index.record(rec_name))
    if modifier:
        link = skill_link(rec_name, index)
        sub = link["sub_skill"]
        if sub and sub != rec_name and index.record(sub) is not None:
            return sub
        for dep in link["dependency"]:
            if dep != rec_name and index.record(dep) is not None:
                return dep
    m = _PARENT.match(stem)
    if m:
        cand = f"{folder}/{m.group(1)}1.dbr"
        found = index.record(cand)
        if cand != rec_name and found is not None and not (modifier and _is_modifier(found)):
            return cand
    if modifier:
        suffixed = _MOD_STEM.match(stem)
        for base in ([suffixed.group(1)] if suffixed else []) + ([m.group(1)] if m else []):
            for cand in (f"{folder}/{base}.dbr", f"{folder}/{base}1.dbr"):
                found = index.record(cand)
                if cand != rec_name and found is not None and not _is_modifier(found):
                    return cand
    return None


def dual_wielding(weapon_items, index) -> bool:
    """Two weapons in the hands: both slots hold a one-handed melee or ranged weapon (no shield, focus or off-hand, no
    two-hander). The same notion as the sheet's weapon speed (two weapons averaged)."""
    n = 0
    for w in weapon_items:
        base = w.basename if hasattr(w, "basename") else (w.get("basename") or "")
        cls = str((index.record(base) or {}).get("Class", ""))
        if cls.startswith(("WeaponMelee", "WeaponHunting")) and not cls.endswith("2h"):
            n += 1
    return n >= 2


def held_weapon_types(weapon_items, index) -> frozenset:
    """The weapon-type flags of the items in the hands: 'Axe', 'Mace2h', 'Scepter', 'Ranged1h', 'Shield', 'Offhand' ... - the
    suffix
    of the item class (WeaponMelee_Scepter, WeaponArmor_Offhand, WeaponHunting_Ranged2h), the same names the game's skill records
    use for their weapon flags."""
    out = set()
    for w in weapon_items:
        base = w.basename if hasattr(w, "basename") else (w.get("basename") or "")
        cls = str((index.record(base) or {}).get("Class", ""))
        if cls.startswith(("WeaponMelee_", "WeaponHunting_", "WeaponArmor_", "WeaponMagical_")):
            out.add(cls.split("_", 1)[1])
    return frozenset(out)


def item_skill_weapon(rec_name: str, weapon_items, index) -> tuple[bool, str]:
    """M18 (d): (usable, reason) of a skill an item grants, by the weapon flags of its record (domain.skill_facts: the index keeps
    them nowhere). A skill that lists weapon types works only while a held item is one of them: Blazing Charge, Chaos Strike and
    the other charge and blink skills of the runes list every melee weapon (one-handed and two-handed), so a bow or a staff ends
    them, a scepter does not; 'Bear Claw' needs a two-hander, 'Myrmidon Strike' a one-hander. A skill without an entry has no
    condition."""
    key = (rec_name or "").removeprefix("records/skills/").removesuffix(".dbr")
    need = SF.ITEM_SKILL_WEAPONS.get(key)
    if not need:
        return True, ""
    held = held_weapon_types(weapon_items, index)
    if held & need:
        return True, ""
    return False, f"needs one of {', '.join(sorted(need))}; held: {', '.join(sorted(held)) or 'nothing'}"


def form_of(rec_name: str, index) -> str | None:
    """Shapeshift record an ability belongs to (<form>_skillNN_<name>.dbr)."""
    m = _FORM.match(rec_name or "")
    if not m:
        return None
    form = m.group(1) + ".dbr"
    cls = str((index.record(form) or {}).get("Class", ""))
    return form if "Shapeshift" in cls else None


def buff_record(rec, index):
    """Aura/buff skills carry their values in the linked buff record
    (buffSkillName, else <skill>_buff.dbr). petSkillName is NOT followed."""
    if not rec:
        return None
    target = rec.get("buffSkillName")
    if isinstance(target, str) and target:
        return index.record(target)
    name = rec.get("_recordname", "")
    if name.endswith(".dbr") and not name.endswith("_buff.dbr"):
        return index.record(name[:-4] + "_buff.dbr")
    return None


def two_handed_active(weapon_items) -> bool:
    for w in weapon_items:
        b = w.basename if hasattr(w, "basename") else (w.get("basename") or "")
        if "2h" in b.rsplit("/", 2)[-2]:
            return True
    return False


def skill_activity(rec_name: str, rec, index, weapon_items=()) -> tuple[Activity, str]:
    for prefix, (cond, name) in DEVOTION_WEAPON.items():
        if rec_name.startswith(prefix) and cond == "two_handed" and not two_handed_active(weapon_items):
            return Activity.WEAPON_CONDITION_UNMET, f"{name}: two-handed weapon only"
    if rec_name in DUAL_WIELD_ONLY and not dual_wielding(weapon_items, index):
        return Activity.WEAPON_CONDITION_UNMET, f"{DUAL_WIELD_ONLY[rec_name]}: two one-handed weapons only (dualWieldOnly)"
    cls = str((rec or {}).get("Class", ""))
    a = class_activity(cls, rec_name)
    if a is not None:
        return a, cls
    parent = parent_skill(rec_name, index)
    if parent is None:
        return Activity.PERMANENT, f"{cls}, base skill unknown"
    prec = index.record(parent) or {}
    pcls = str(prec.get("Class", ""))
    pbuff = buff_record(prec, index) if prec else None
    bcls = str((pbuff or {}).get("Class", "") or (pbuff or {}).get("_type", ""))
    if any(t in bcls for t in ("Debuf", "Contageous")):
        return Activity.ENEMY_DEBUFF, f"{cls} of {short(parent)}: debuff on enemies"
    pa = class_activity(pcls, parent) or Activity.PERMANENT
    return pa, f"{cls} of {short(parent)} ({pcls})"
