"""Design data (.67): the game-database facts a build design needs that the index does not keep.

references/design-data-<version>.json is written by gdcheck.app.refs_extract (part 'design') from the
.arz files - on the PC by GD-Tools after an index rebuild, in a chat from uploaded .arz files. The
index keeps bonus fields of items, skills and devotion stars; a design also needs:

    sets              item set records (records/items/lootsets/): members, bonuses per number of pieces,
                      '+N to all skills of a mastery', item skill modifiers with the piece count that
                      activates them (itemSkillModifierControl)
    modifiers         skill modifier/transmuter records with their conversions (also the second pair
                      conversionInType2/OutType2/Percentage2) and their bonus fields
    celestial_powers  every devotion power: trigger (controller triggerType, chanceToRun, triggerParam),
                      internal cooldown, the skill templates it may be bound to and its black list
    star_weapons      devotion stars that need a weapon type (Ranged1h, Axe2h, Shield, ...)
    skill_tree        position of every mastery skill in the skill window (row = base skill + modifiers)
    skills            per mastery skill: template, subSkillName (a modifier that changes only one granted
                      skill), exclusiveSkill, skillDependancy, grantedSkills, energy cost, absorption

Pure: extract(read, names, ...) with read(record) -> dict | None; resolve(index) picks the file whose
fingerprint equals the game data (data.versioned).
"""
from __future__ import annotations

import re

from . import versioned as V

PREFIX = "design-data"
SCHEMA = 1
SET_DIR = "records/items/lootsets/"
DEVOTION_DIR = "records/skills/devotion/"
SKILL_DIR = re.compile(r"^records/skills/playerclass\d+/")
UI_SKILL = re.compile(r"^records/ui/skills/(class\d+)/(skill\d+|classtraining)\.dbr$")
STAR = re.compile(r"^records/skills/devotion/tier\d+_\d+[a-z]\.dbr$")
WEAPON_FLAGS = ("Axe", "Axe2h", "Dagger", "Mace", "Mace2h", "Offhand", "Ranged1h", "Ranged2h", "Scepter",
                "Shield", "Spear", "Spear2h", "Staff", "Sword", "Sword2h")
CONVERSION_FIELDS = (("conversionInType", "conversionOutType", "conversionPercentage"),
                     ("conversionInType2", "conversionOutType2", "conversionPercentage2"))
BONUS_PREFIX = ("offensive", "defensive", "character", "retaliation", "skill", "weaponDamagePct",
                "damageAbsorption", "lifeMonitorPercent")
SKILL_KEYS = {"subSkillName": "sub_skill", "skillDependancy": "dependency", "skillDependancyAll": "dependency_all",
              "grantedSkills": "granted", "skillManaCost": "energy_cost", "damageAbsorptionPercent": "absorption",
              "lifeMonitorPercent": "life_monitor", "buffSkillName": "buff", "skillTier": "tier"}


def _nonzero(v) -> bool:
    if isinstance(v, list):
        return any(x not in (0, 0.0, "", None) for x in v)
    return v not in (0, 0.0, "", None)


def _num_list(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, (int, float)) for x in v)


def _base(path) -> str:
    """'database/templates/skill_attackweapon.tpl' / '.../skill_attackweapon.dbr' -> 'skill_attackweapon'."""
    return str(path or "").replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()


def _trim(lst: list) -> list:
    out = list(lst)
    while out and out[-1] in (0, 0.0):
        out.pop()
    return out


def conversions(rec: dict) -> list:
    """[[from, to, percent], ...] of a record (first and second conversion pair)."""
    out = []
    for fi, fo, fp in CONVERSION_FIELDS:
        a, b, p = rec.get(fi), rec.get(fo), rec.get(fp)
        if a and b and isinstance(p, (int, float)) and p > 0:
            out.append([str(a), str(b), float(p)])
    return out


def bonus_fields(rec: dict) -> dict:
    out = {}
    for k, v in rec.items():
        if not k.startswith(BONUS_PREFIX) or k.endswith(("Global", "XOR")) or not _nonzero(v):
            continue
        if isinstance(v, (int, float)) or _num_list(v):
            out[k] = v
    return out


def _as_list(v) -> list:
    if v in (None, ""):
        return []
    return [str(x) for x in (v if isinstance(v, list) else [v]) if x]


def item_set(read, name: str, rec: dict) -> dict:
    """Members, bonuses per piece count (index i = i + 1 pieces), mastery ranks and skill modifiers."""
    fields, mastery, skills, mods = {}, [], [], []
    control = rec.get("itemSkillModifierControl") or []
    control = control if isinstance(control, list) else [control]
    for k, v in rec.items():
        m = re.fullmatch(r"augmentMasteryName(\d+)", k)
        if m and v:
            mastery.append({"mastery": str(v), "ranks": _trim(list(rec.get(f"augmentMasteryLevel{m.group(1)}") or []))})
            continue
        m = re.fullmatch(r"augmentSkillName(\d+)", k)
        if m and v:
            skills.append({"skill": str(v), "ranks": _trim(list(rec.get(f"augmentSkillLevel{m.group(1)}") or []))})
            continue
        m = re.fullmatch(r"modifierSkillName(\d+)", k)
        if m and v:
            on = [i + 1 for i, c in enumerate(control) if c] or [len(rec.get("setMembers") or [])]
            mods.append({"modifier": str(v), "modified": str(rec.get(f"modifiedSkillName{m.group(1)}") or ""),
                         "pieces": on[0]})
            continue
        if k.startswith(BONUS_PREFIX) and _num_list(v) and _nonzero(v) and not k.endswith(("Global", "XOR")):
            fields[k] = _trim(v)
    for e in mods:
        e["modifier_fields"] = bonus_fields(read(e["modifier"]) or {})
        e["conversions"] = conversions(read(e["modifier"]) or {})
    return {"name": str(rec.get("FileDescription") or name.rsplit("/", 1)[-1]),
            "members": _as_list(rec.get("setMembers")), "fields": dict(sorted(fields.items())),
            "mastery": mastery, "skills": skills, "skill_modifiers": mods}


def celestial_power(read, rec: dict) -> dict | None:
    """Trigger facts of a power; buff powers (Skill_AttackBuff, Skill_BuffRadius) keep controller,
    templates and black list on their buffSkillName record (e.g. Murmur's Rumor)."""
    ctl_name = rec.get("templateAutoCast")
    if not ctl_name and rec.get("buffSkillName"):
        rec = read(str(rec["buffSkillName"])) or {}
        ctl_name = rec.get("templateAutoCast")
    if not ctl_name:
        return None
    ctl = read(str(ctl_name)) or {}
    cd = rec.get("skillCooldownTime")
    return {"controller": str(ctl_name), "trigger": str(ctl.get("triggerType") or ""),
            "chance": float(ctl.get("chanceToRun") or 0), "param": float(ctl.get("triggerParam") or 0),
            "cooldown": cd if isinstance(cd, list) else float(cd or 0),
            "templates": sorted({_base(t) for t in _as_list(rec.get("skillTemplates"))}),
            "blacklist": sorted(_as_list(rec.get("skillBlackList")))}


def skill_facts(rec: dict) -> dict:
    out = {"template": _base(rec.get("templateName"))}
    if rec.get("exclusiveSkill"):
        out["exclusive"] = True
    for k, key in SKILL_KEYS.items():
        v = rec.get(k)
        if not _nonzero(v):
            continue
        if k in ("skillDependancy", "grantedSkills"):
            out[key] = _as_list(v)
        elif isinstance(v, (str, int, float)) or _num_list(v):
            out[key] = _trim(v) if isinstance(v, list) else v
    if conversions(rec):
        out["conversions"] = conversions(rec)
    return out


def extract(read, names, version: str, fingerprint: str) -> dict:
    """The design-data payload from the merged game database (read(record) -> dict | None)."""
    sets, mods, cps, weapons, tree, skills = {}, {}, {}, {}, {}, {}
    raw_skills: dict = {}
    for n in names:
        if n.startswith(SET_DIR):
            r = read(n) or {}
            if r.get("setMembers"):
                sets[n] = item_set(read, n, r)
        elif n.startswith(DEVOTION_DIR):
            r = read(n) or {}
            if n.endswith("_skill.dbr"):
                cp = celestial_power(read, r)
                if cp:
                    cps[n] = cp
            elif STAR.match(n):
                flags = [f for f in WEAPON_FLAGS if r.get(f)]
                if flags:
                    weapons[n] = flags
        elif n.startswith("records/skills/"):
            r = read(n) or {}
            if SKILL_DIR.match(n):
                raw_skills[n] = r
            if (SKILL_DIR.match(n) and r.get("Class") in ("Skill_Modifier", "Skill_Transmuter")) or conversions(r):
                mods[n] = {"class": str(r.get("Class") or ""), "conversions": conversions(r),
                           "fields": bonus_fields(r)}
        else:
            m = UI_SKILL.match(n)
            if m:
                r = read(n) or {}
                if r.get("skillName"):
                    tree[str(r["skillName"])] = {"mastery": m.group(1), "x": int(r.get("bitmapPositionX") or 0),
                                                 "y": int(r.get("bitmapPositionY") or 0),
                                                 "circular": bool(r.get("isCircular"))}
    # mastery skills: the ones in a skill window and the records they name (buff, granted, sub skill)
    todo = [n for n in tree if n in raw_skills]
    while todo:
        n = todo.pop()
        if n in skills or n not in raw_skills:
            continue
        skills[n] = skill_facts(raw_skills[n])
        for key in ("buff", "granted", "sub_skill"):
            v = skills[n].get(key)
            todo += [x for x in (v if isinstance(v, list) else [v]) if x and x not in skills]
    for s in sets.values():
        for e in s["skill_modifiers"]:
            mods.setdefault(e["modifier"], {"class": "Skill_Modifier", "conversions": e["conversions"],
                                            "fields": e["modifier_fields"]})
    return {"game_version": version, "fingerprint": fingerprint, "schema": SCHEMA,
            "source": "game database .arz (gdcheck.app.refs_extract --only design)",
            "note": "facts for build designs the index does not keep (sets, conversions incl. the second "
                    "pair, celestial power triggers, star weapon requirements, skill tree, skill facts)",
            "sets": sets, "modifiers": mods, "celestial_powers": cps, "star_weapons": weapons,
            "skill_tree": tree, "skills": dict(sorted(skills.items()))}


def resolve(index, folder=None, extras=None) -> V.Resolved:
    """The design-data file of this game data (fingerprint first, see data.versioned)."""
    ver, fp = V.index_identity(index)
    return V.resolve(PREFIX, ver, fp, folder, extras)
