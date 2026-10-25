"""How a skill or effect works: permanent, toggle, temporary, proc ... (port of the
.35 skill_wirkung, the basis of 'character sheet = permanent + toggles')."""
from __future__ import annotations

import re

from ..data.text import short
from .enums import Activity

# Devotions with a weapon condition the index does not carry (wiki single pages;
# not proven complete - missing entries lead to too high values).
DEVOTION_WEAPON = {
    "records/skills/devotion/tier2_21": ("two_handed", "Kraken"),
}
_PARENT = re.compile(r"^(.*?)(\d+)([a-z]?)$")
_FORM = re.compile(r"^(.*/[^/]+?)_skill\d+[^/]*\.dbr$")


def class_activity(cls: str) -> Activity | None:
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


def parent_skill(rec_name: str, index) -> str | None:
    """Base skill of a modifier/transmuter by naming convention
    (witchfire2 -> witchfire1, werewolf1b -> werewolf1)."""
    folder, _, file = rec_name.rpartition("/")
    stem = file[:-4] if file.endswith(".dbr") else file
    m = _PARENT.match(stem)
    if not m:
        return None
    cand = f"{folder}/{m.group(1)}1.dbr"
    if cand == rec_name or index.record(cand) is None:
        return None
    return cand


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
    cls = str((rec or {}).get("Class", ""))
    a = class_activity(cls)
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
    pa = class_activity(pcls) or Activity.PERMANENT
    return pa, f"{cls} of {short(parent)} ({pcls})"
