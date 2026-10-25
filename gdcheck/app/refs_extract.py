"""Reference data from the game's .arz databases (.53, patch readiness).

    python -m gdcheck.app.refs_extract <game folder | folder with database.arz, GDX1-4.arz>
                                       --out <folder> --version <label> --index <gd-index-*.json.gz>
                                       [--only anim|boss|vendor|design]

Writes, next to each other and atomically:
    anim-tables-<version>.json    player animation tables (male, female, every shapeshift form) per weapon
                                  mode: basic attack/spell lists and special animations (.anm path,
                                  slot speed multiplier, weight); player speed multipliers, dual wield
                                  factor, skillSpecialAnimationName of every player/devotion/item skill
    boss-combat-<version>.json    per boss: level range, attribute equations, skill levels, the skill graph
                                  (KG-08, data.boss_graph) and fight phases; the fields of every graph record;
                                  schema 4 (.54): 'monsters' - animation table, weapon mode, attack speed and
                                  attack skill of every boss and spawned monster - and 'anim_tables' (their
                                  attack/spell pools and the special animations their skills name);
                                  schema 5 (.72): 'benchmarks' - raw database fields of the Training Dummy and
                                  Mogara (life equation, skill levels, own and passive defenses, the dummy's
                                  low-health shield) for the damage benchmark (data.benchmarks)
    player-skills-<version>.json  records with notDispelable = 1 (a purge cannot remove them)
    vendor-ranks-<version>.json   (.66) faction vendor augments: faction slot and the reputation tier
                                  they need (lowest tier whose vendor table lists them)
    design-data-<version>.json    (.67) facts for build designs the index does not keep: set bonuses,
                                  conversions, celestial power triggers, star weapon requirements, skill
                                  tree and skill facts (data.design_data)
Every file carries game_version and the fingerprint of the index - the loaders (data.versioned) pick
the file whose fingerprint equals the game data, not the one with the right label. Expansion
databases override the base game; survival mode databases are never read.

Before .53 this ran only in the cloud from uploaded .arz files (tools/anim_extract.py,
tools/boss_extract.py - now thin wrappers). GD-Tools runs it on the PC after an index rebuild for new
game data. Exit codes: 0 written, 2 no .arz databases in the folder, 3 a part failed (the other parts
are still written).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from ..data import benchmarks as BM
from ..data import boss_graph as BG
from ..data import design_data as DD
from ..data.arz import ArzFile, arz_files

TABLES = {"male": "records/creatures/pc/anm_malepc.dbr", "female": "records/creatures/pc/anm_femalepc.dbr"}
PC = {"male": "records/creatures/pc/malepc01.dbr", "female": "records/creatures/pc/femalepc01.dbr"}
PLAYER_SKILLS = ("records/skills/playerclass", "records/skills/devotion/", "records/skills/itemskills",
                 "records/skills/default/", "records/items/")
KEEP_PREFIX = ("offensive", "weaponDamagePct", "skillCooldownTime", "skillActiveDuration", "Class",
               "skillTargetRadius", "projectileFragments", "skillProjectileNumber", "damageAbsorption",
               "skillChargeLevel", "buffSkillName", "spawnObjects", "skillDisplayName",
               "defensiveProtection", "defensiveAbsorption", "characterDefensiveAbility",
               "characterOffensiveAbility", "lifeMonitorPercent", "characterAttackSpeed",
               "characterTotalSpeedModifier", "damageAbsorptionPercent", "petLimit", "petPeriod",
               "spawnObjectsTimeToLive", "petBurstSpawn", "skillLevel", "skillName", "attackSkillName",
               "specialAttackSkillName", "charLevel", "autoCastSkill", "autoCastController",
               # .54 (KG-08 step 3): animation of a skill, cast speed, charges, instant casts
               "skillSpecialAnimationName", "characterSpellCastSpeed", "skillChargeDuration", "instantCast",
               # .72 (B1): monster life modifiers of the passives (Training Dummy health, armorbase04)
               "characterLife")
COMBAT_SCHEMA = 5          # .54: + 'monsters' and 'anim_tables'; .72: + 'benchmarks' (Training Dummy, Mogara)
LOOT_DEPTH = 4             # nested loot tables (master table -> item table -> item)
EQUIP_MIN = 50.0           # chanceToEquip<Hand> below this: mostly empty-handed (ASSUMED)
BIO_KEYS =("characterDefensiveAbility", "characterOffensiveAbility", "characterStrength", "characterDexterity",
            "characterLife")


def _f(v, default=1.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _keep(k, v):
    if not k.startswith(KEEP_PREFIX) or v in (0, 0.0, "", None, []):
        return False
    if isinstance(v, list) and not any(x not in (0, 0.0, "") for x in v):
        return False
    return not k.endswith(("Global", "XOR"))


class Db:
    """The merged game database: later archives (expansions) override earlier ones; records cached."""

    def __init__(self, files):
        # paths, or objects with read_record/record_names (tests)
        self.archives = [f if hasattr(f, "read_record") else ArzFile(f) for f in files]
        self._cache: dict = {}
        self._names: list | None = None

    def read(self, rec):
        if rec not in self._cache:
            r = None
            for a in self.archives:
                r = a.read_record(rec) or r
            self._cache[rec] = r
        return self._cache[rec]

    def read_uncached(self, rec):
        r = None
        for a in self.archives:
            r = a.read_record(rec) or r
        return r

    def names(self) -> list:
        if self._names is None:
            self._names = sorted({n for a in self.archives for n in a.record_names()})
        return self._names


# ------------------------------------------------------------------------------- animation tables
def table(rec: dict) -> dict:
    """{prefix: {"attack": [...], "spell": [...], "special": {name: [...]}}}; entries (anm, speed, weight)."""
    out: dict = {}
    for k, v in rec.items():
        m = re.fullmatch(r"([a-zA-Z0-9]+?)AttackAnim(\d+)", k)
        if m and v:
            p, n = m.groups()
            out.setdefault(p, {}).setdefault("attack", []).append(
                (str(v), _f(rec.get(f"{p}AttackAnimSpeed{n}")), _f(rec.get(f"{p}AttackAnimWeight{n}"), 100.0)))
            continue
        m = re.fullmatch(r"([a-zA-Z0-9]+?)SpellAttackAnim(\d*)", k)
        if m and v:
            p, n = m.groups()
            out.setdefault(p, {}).setdefault("spell", []).append(
                (str(v), _f(rec.get(f"{p}SpellAttackAnimSpeed{n}")), _f(rec.get(f"{p}SpellAttackAnimWeight{n}"), 100.0)))
            continue
        m = re.fullmatch(r"([a-zA-Z0-9]+?)SpecialAnimRef(\d+)", k)
        if m and v:
            p, n = m.groups()
            anm = rec.get(f"{p}SpecialAnim{n}")
            if anm:
                out.setdefault(p, {}).setdefault("special", {}).setdefault(str(v), []).append(
                    (str(anm), _f(rec.get(f"{p}SpecialAnimSpeed{n}")), 100.0))
            continue
        m = re.fullmatch(r"([a-zA-Z0-9]+?)SpecialAnimPoolRef(\d+)", k)
        if m and v:
            p, n = m.groups()
            for var in "ABCDEFGH":
                anm = rec.get(f"{p}SpecialAnimPool{n}{var}")
                if anm:
                    out.setdefault(p, {}).setdefault("special", {}).setdefault(str(v), []).append(
                        (str(anm), _f(rec.get(f"{p}SpecialAnimPoolSpeed{n}{var}")),
                         _f(rec.get(f"{p}SpecialAnimPoolWeight{n}{var}"), 100.0)))
    for p in out.values():                         # deterministic order
        for key in ("attack", "spell"):
            if key in p:
                p[key] = sorted(p[key])
        if "special" in p:
            p["special"] = {n: sorted(e) for n, e in sorted(p["special"].items())}
    return dict(sorted(out.items()))


def anim_tables(db: Db, version: str, fingerprint: str) -> dict:
    read = db.read_uncached
    tables = {}
    for key, rec in TABLES.items():
        r = read(rec)
        if r:
            tables[key] = {"record": rec, "prefixes": table(r)}
    forms, anim_names = {}, {}
    for n in db.names():
        if not n.startswith(PLAYER_SKILLS):
            continue
        r = read(n) or {}
        if r.get("replacementAnims"):
            t = str(r["replacementAnims"])
            tr = read(t)
            forms[n] = t
            if tr and t not in tables:
                tables[t] = {"record": t, "prefixes": table(tr)}
        if r.get("skillSpecialAnimationName"):
            anim_names[n] = str(r["skillSpecialAnimationName"])
    player = {}
    for key, rec in PC.items():
        r = read(rec) or {}
        player[key] = {"record": rec, "characterAttackSpeed": _f(r.get("characterAttackSpeed"), 1.0),
                       "characterSpellCastSpeed": _f(r.get("characterSpellCastSpeed"), 1.0)}
    ge = read("records/game/gameengine.dbr") or {}
    return {"game_version": version, "fingerprint": fingerprint,
            "source": "game database .arz (gdcheck.app.refs_extract)",
            "frames_per_second": 30, "dw_weapon_speed_factor": _f(ge.get("dwWeaponSpeedFactor"), 0.5),
            "player": player, "tables": tables, "forms": forms, "skill_animation": anim_names}


# ------------------------------------------------------------------------------- monster animations (.54)
def _loot_items(read, rec: str, weight: float = 1.0, depth: int = 0, out: dict | None = None) -> dict:
    """{item record: probability} of an item record or a loot table (lootNameN / lootWeightN, nested)."""
    out = {} if out is None else out
    r = read(rec) or {}
    cls = str(r.get("Class", ""))
    if cls.startswith(("LootItemTable", "LootMasterTable")):
        if depth >= LOOT_DEPTH:
            return out
        pairs = []
        for k, v in r.items():
            m = re.fullmatch(r"lootName(\d+)", k)
            if m and v:
                pairs.append((str(v), _f(r.get(f"lootWeight{m.group(1)}"), 1.0)))
        total = sum(w for _n, w in pairs if w > 0)
        for n, w in sorted(pairs):
            if w > 0 and total > 0:
                _loot_items(read, n, weight * w / total, depth + 1, out)
    elif r:
        out[rec] = out.get(rec, 0.0) + weight
    return out


def equipped_weapon(read, r: dict) -> dict | None:
    """{'main'|'off': {'class', 'share', 'base_speed', 'items'}} of the weapons a monster equips: the
    slot items (loot tables resolved, weighted), the most likely item Class and the weighted mean
    characterBaseAttackSpeed of that Class. Hands with chanceToEquip below EQUIP_MIN are empty."""
    hands = {}
    for hand, key in (("RightHand", "main"), ("LeftHand", "off")):
        if _f(r.get(f"chanceToEquip{hand}"), 0.0) < EQUIP_MIN:
            continue
        slots = [(str(r[f"loot{hand}Item{i}"]), _f(r.get(f"chanceToEquip{hand}Item{i}"), 0.0))
                 for i in range(1, 7) if r.get(f"loot{hand}Item{i}")]
        slots = [(it, w) for it, w in slots if w > 0]
        total = sum(w for _it, w in slots)
        items: dict = {}
        for it, w in slots:
            _loot_items(read, it, w / total, 0, items)
        by_class: dict = {}
        for it, p in items.items():
            f = read(it) or {}
            c = str(f.get("Class", ""))
            if c.startswith("Weapon"):
                by_class.setdefault(c, []).append((p, _f(f.get("characterBaseAttackSpeed"), 0.0)))
        if not by_class:
            continue
        all_p = sum(p for v in by_class.values() for p, _s in v)
        cls, vals = max(by_class.items(), key=lambda kv: (sum(p for p, _s in kv[1]), kv[0]))
        cp = sum(p for p, _s in vals)
        hands[key] = {"class": cls, "share": round(cp / all_p, 4),
                      "base_speed": round(sum(p * s for p, s in vals) / cp, 4), "items": [it for it, _w in slots]}
    return hands or None


def monster_mode(prefixes: dict, hands: dict | None) -> tuple[str | None, str | None]:
    """(weapon mode prefix, note) of a monster: the mode of its weapons if the table has it; a table
    with one animation set only ('unarmed') is that set; otherwise unarmed (ASSUMED)."""
    from ..data import animation as AD
    if not prefixes:
        return None, "no animations"
    hands = hands or {}
    want = AD.weapon_mode((hands.get("main") or {}).get("class"), (hands.get("off") or {}).get("class"))
    if want in prefixes:
        return want, None
    if list(prefixes) == ["unarmed"]:
        return "unarmed", None
    use = "unarmed" if "unarmed" in prefixes else sorted(prefixes)[0]
    return use, f"weapon mode {want} not in the animation table - {use} used (ASSUMED)"


def monster_anim(read, mrec: str, special_names, anim_store: dict) -> dict:
    """Animation info of one monster (boss or spawned): its animation table - or, without a table,
    the animation fields of the record itself (traps; official modding guide: 'generally you should
    use an Animation Set') -, weapon mode, attack/cast speed, attack skill. The record's own slot
    speeds are NOT applied when a table is set (ASSUMED: the table replaces the record's animation
    fields; 3,007 of 3,087 monster records carry template default speeds 1.25/0.75 or 1.0/1.0 next to
    a table); a differing value is kept as 'record_attack_speed' for the report's range. The table
    entries go to `anim_store` once per table and mode, specials only those `special_names` uses."""
    r = read(mrec) or {}
    tab_rec = str(r.get("charAnimationTableName") or "")
    trec = read(tab_rec) if tab_rec else None
    if trec:
        key, prefixes, source = tab_rec, table(trec), "table"
    else:
        key, prefixes, source = mrec, table(r), "record"
    hands = equipped_weapon(read, r)
    mode, note = monster_mode(prefixes, hands)
    info = {"table": key if mode else None, "source": source, "mode": mode,
            "attack_speed": _f(r.get("characterAttackSpeed"), 1.0),
            "cast_speed": _f(r.get("characterSpellCastSpeed"), 1.0),
            "attack_skill": str(r["attackSkillName"]) if r.get("attackSkillName") else None}
    if hands:
        info["weapon"] = hands
    if note:
        info["mode_note"] = note
    if mode:
        p = prefixes[mode]
        slot = anim_store.setdefault(key, {}).setdefault(mode, {})
        for k in ("attack", "spell"):
            if p.get(k):
                slot[k] = p[k]
        sp = {n: e for n, e in (p.get("special") or {}).items() if n in special_names}
        if sp:
            slot["special"] = dict(sorted({**slot.get("special", {}), **sp}.items()))
        if source == "table":
            own = [_f(r.get(f"{mode}AttackAnimSpeed{i}")) for i in range(1, 10)
                   if r.get(f"{mode}AttackAnimSpeed{i}") not in (None, "")]
            if own and any(abs(v - 1.0) > 1e-6 for v in own):
                info["record_attack_speed"] = round(sum(own) / len(own), 4)
    return info


# ------------------------------------------------------------------------------- bosses + player skills
def cooldown_refresh_entry(r: dict) -> dict | None:
    """{"skill", "chance" (% per rank, list or number), "amount" (s), "trigger"} of a record that reduces
    another skill's cooldown ('x% Chance on Critical Attack to reduce cooldown of <skill> by y Seconds',
    tagSkillCooldownRefreshName); None without a target skill, chance or amount (the template default
    trigger list alone is no effect)."""
    skill, chance, amount = r.get("refreshCooldownSkill"), r.get("refreshCooldownChance"), r.get("refreshCooldownAmount")
    trig = str(r.get("refreshCooldownTrigger") or "")
    if not skill or not chance or not amount or not trig or ";" in trig:
        return None
    if isinstance(chance, list):
        chance = [float(x) for x in chance]
    return {"skill": str(skill), "chance": chance if isinstance(chance, list) else float(chance),
            "amount": round(float(amount), 6), "trigger": trig}


def _num(v):
    """First entry of a per-level list, else the value (the benchmark fields are scalars or per-level lists)."""
    return v[0] if isinstance(v, list) and v else v


def _shield(read, levels: dict) -> dict | None:
    """The low-health shield of a monster: the passive with lifeMonitorPercent among its skills. Absorption
    and duration sit on the passive itself (Training Dummy, 1.3.0.8) or on its buffSkillName record."""
    for srec in sorted(levels):
        sr = read(srec) or {}
        pct = _num(sr.get("lifeMonitorPercent"))
        if not pct:
            continue
        buff = read(str(sr["buffSkillName"])) if sr.get("buffSkillName") else {}
        buff = buff or {}
        out = {"skill": srec, "life_pct": _f(pct)}
        for key, field in (("absorption_pct", "damageAbsorptionPercent"), ("duration_s", "skillActiveDuration"),
                           ("heal_pct", "skillLifePercent")):
            v = _num(sr.get(field)) or _num(buff.get(field))
            out[key] = _f(v, 0.0) if v else 0.0
        return out
    return None


def benchmark_entries(read, records: dict) -> tuple[dict, dict]:
    """({key: entry}, {skill record: kept fields}) of the damage benchmarks (.72, schema 5 'benchmarks').
    `records` = {key: monster record}. An entry holds the raw database fields the targets are computed from
    (field list: data.benchmarks): name tag, level range and charLevel equation, the bio equations, the skill
    level equations, the monster's own defensive* fields, the defensive* fields of its passives, the
    classification and the low-health shield. A record the databases lack gives no entry."""
    entries, skills = {}, {}
    for key, rec in sorted(records.items()):
        r = read(rec)
        if not r:
            continue
        levels = {}
        for k, v in r.items():
            if k.startswith("skillName") and v:
                n = k[len("skillName"):]
                levels[str(v)] = str(r.get(f"skillLevel{n}", "1"))
        passive_def = {}
        for srec in levels:
            sr = read(srec) or {}
            skills.setdefault(srec, {k: v for k, v in sr.items() if _keep(k, v)})
            if "Passive" in str(sr.get("Class", "")):
                d = {k: v for k, v in sr.items() if k.startswith(BM.DEFENSIVE_PREFIX) and
                     not k.startswith(("defensiveProtection", "defensiveAbsorption")) and v not in (0, 0.0, "", None, [])}
                if d:
                    passive_def[srec] = d
        bio = read(str(r.get("characterAttributeEquations") or "")) or {}
        entries[key] = {
            "record": rec, "name_tag": r.get("description"), "minLevel": r.get("minLevel"),
            "maxLevel": r.get("maxLevel"), "charLevel": r.get("charLevel"),
            "attribute_equations": {k: str(bio[k]) for k in BIO_KEYS if bio.get(k) not in (None, "")},
            "skill_levels": levels,
            "defensive": {k: v for k, v in r.items() if k.startswith(BM.DEFENSIVE_PREFIX) and
                          v not in (0, 0.0, "", None, [])},
            "passive_defensive": passive_def,
            "monsterClassification": r.get("monsterClassification"),
            "shield": _shield(read, levels)}
    return entries, skills


def boss_combat(db: Db, bosse_roh: dict, version: str, fingerprint: str) -> tuple[dict, dict]:
    """(boss-combat payload, player-skills payload)."""
    read = db.read
    wanted = {e["record"]: name for name, e in (bosse_roh or {}).items() if e.get("record")}
    out, skill_records = {}, {}
    monsters: dict = {}
    anim_store: dict = {}
    for rec, name in sorted(wanted.items(), key=lambda x: x[1]):
        r = read(rec)                           # later archives override earlier ones
        if not r:
            continue
        skills = {}
        for k, v in r.items():
            if k.startswith("skillName") and v:
                n = k[len("skillName"):]
                skills[str(v)] = str(r.get(f"skillLevel{n}", "1"))
        if r.get("attackSkillName"):
            skills.setdefault(str(r["attackSkillName"]), "1")
        for srec in skills:
            if srec not in skill_records:
                sr = read(srec) or {}
                skill_records[srec] = {k: v for k, v in sr.items() if _keep(k, v)}
        bio = read(str(r.get("characterAttributeEquations") or "")) or {}
        out[name] = {"record": rec, "minLevel": r.get("minLevel"), "maxLevel": r.get("maxLevel"),
                     "attribute_equations": {k: str(bio[k]) for k in BIO_KEYS if bio.get(k) not in (None, "")},
                     "attribute_record": r.get("characterAttributeEquations"),
                     "charLevel": r.get("charLevel"), "skill_levels": skills,
                     "defensiveSlowLifeLeach": r.get("defensiveSlowLifeLeach", 0.0),
                     "attackSkillName": r.get("attackSkillName"),
                     "characterBaseAttackSpeedTag": r.get("characterBaseAttackSpeedTag"),
                     "characterAttackSpeed": r.get("characterAttackSpeed")}
        graph = BG.walk(read, rec)
        out[name]["graph"] = graph
        out[name]["phases"] = BG.phases(read, db.names(), rec)
        for node in graph:
            if node["record"] not in skill_records:
                skill_records[node["record"]] = {k: v for k, v in (read(node["record"]) or {}).items() if _keep(k, v)}
        owners = [rec] + [n["record"] for n in graph if n["kind"] == "monster"]
        for m in dict.fromkeys(owners):
            if m in monsters:
                continue
            own = set(skills) if m == rec else set()
            own |= {n["record"] for n in graph if n["owner"] == m and n["kind"] == "skill"}
            names = {str((read(s) or {}).get("skillSpecialAnimationName")) for s in own}
            monsters[m] = monster_anim(read, m, names - {"None", ""}, anim_store)
    bench, bench_skills = benchmark_entries(read, BM.BENCHMARK_RECORDS)
    for srec, kept in bench_skills.items():
        skill_records.setdefault(srec, kept)
    not_dispelable = []
    refresh = {}
    for n in db.names():
        if not (n.startswith("records/skills/playerclass") or "/devotion/" in n or "/itemskills" in n):
            continue                            # /itemskills: also itemskillsgdx1-3 (item skill modifiers)
        r = db.read_uncached(n) or {}
        if r.get("notDispelable") == 1 and "/itemskillsgdx" not in n:
            not_dispelable.append(n)
        e = cooldown_refresh_entry(r)
        if e:
            refresh[n] = e
    player = {"game_version": version, "fingerprint": fingerprint,
              "source": "game database .arz (gdcheck.app.refs_extract)",
              "not_dispelable": sorted(set(not_dispelable)),
              "cooldown_refresh": dict(sorted(refresh.items())),
              "note": "not_dispelable: skills/buffs a monster purge (SkillBuff_DispelMagic) cannot remove; "
                      "every other buff, toggle and aura is dispelable (notDispelable 0). cooldown_refresh: "
                      "records that reduce another skill's cooldown (refreshCooldown*: target skill, % chance "
                      "per rank, seconds, trigger) - the index does not keep these fields"}
    cf = read("records/game/combatformulas.dbr") or {}
    ge = read("records/game/gameengine.dbr") or {}
    formulas = {"defensiveAbilityEquation": cf.get("defensiveAbilityEquation"),
                "offensiveAbilityEquation": cf.get("offensiveAbilityEquation"),
                "armorDefensiveAbsorption": ge.get("armorDefensiveAbsorption")}
    combat = {"game_version": version, "fingerprint": fingerprint,
              "source": "game database .arz (gdcheck.app.refs_extract)",
              "schema": COMBAT_SCHEMA, "graph_schema": BG.SCHEMA, "formulas": formulas, "bosses": out,
              "benchmarks": bench, "skills": skill_records, "monsters": dict(sorted(monsters.items())),
              "anim_tables": dict(sorted(anim_store.items()))}
    return combat, player


# ------------------------------------------------------------------------------- faction vendors (.66, A2)
VENDOR_TIERS = ("Friendly", "Respected", "Honored", "Revered")     # = data.text.REPUTATION_TIERS names
VENDOR_CLASSES = ("ItemEnchantment",)                               # what the gear proposals can buy
NPC_PREFIX = "records/creatures/npcs/"


def vendor_ranks(db: Db, version: str, fingerprint: str) -> dict:
    """{record: {"faction": save slot key, "rank": tier}} of every faction vendor augment. A faction market
    (factionmarket.tpl) names one table per tier (<tier>NormalTable, factiontier.tpl, marketStaticItems);
    the required rank is the lowest tier whose table lists the item. The faction is the gamefactions.dbr
    slot key of the merchant's faction controller (npcmerchant.tpl 'factions' -> 'marketFileName') - the
    items' own factionSource says 'User0' for the Rovers, whose save slot is factionDrifters. An item
    sold by two factions or a market without a single faction is left out (unknown, never guessed)."""
    read = db.read_uncached
    slots = {str(v): k for k, v in (read("records/game/gamefactions.dbr") or {}).items()
             if k.startswith("faction") and isinstance(v, str) and v.startswith("records/")}
    market_faction: dict = {}
    markets: dict = {}
    for n in db.names():
        if not n.startswith(NPC_PREFIX):
            continue
        r = read(n) or {}
        tpl = r.get("templateName")
        if tpl == "database/templates/factionmarket.tpl":
            markets[n] = r
        elif tpl == "database/templates/npcmerchant.tpl":
            slot = slots.get(str(r.get("factions") or ""))
            mf = r.get("marketFileName") or []
            for m in [mf] if isinstance(mf, str) else mf:
                if slot:
                    market_faction.setdefault(str(m), set()).add(slot)
    items: dict = {}
    conflict: set = set()
    for n, r in sorted(markets.items()):
        fs = market_faction.get(n) or set()
        if len(fs) != 1:
            continue
        faction = next(iter(fs))
        for i, tier in enumerate(VENDOR_TIERS):
            tab = read(str(r.get(f"{tier.lower()}NormalTable") or "")) or {}
            stock = tab.get("marketStaticItems") or []
            for it in [stock] if isinstance(stock, str) else stock:
                it = str(it)
                if str((db.read(it) or {}).get("Class")) not in VENDOR_CLASSES:
                    continue
                old = items.get(it)
                if old and old["faction"] != faction:
                    conflict.add(it)
                elif not old or VENDOR_TIERS.index(old["rank"]) > i:
                    items[it] = {"faction": faction, "rank": tier}
    return {"game_version": version, "fingerprint": fingerprint,
            "source": "game database .arz (gdcheck.app.refs_extract)",
            "note": "faction vendor augments: lowest reputation tier whose vendor table lists the item; "
                    "faction = save slot key (data.text.FACTION_SLOT_KEYS)",
            "items": {k: v for k, v in sorted(items.items()) if k not in conflict}}


def write_json(target: Path, payload: dict) -> None:
    """Atomic write (a half-written reference file is never read)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(target)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gdcheck.app.refs_extract", description=__doc__.split("\n")[0])
    ap.add_argument("folder", help="Grim Dawn installation folder, or a folder with database.arz and GDX1-4.arz")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--version", required=True, help="game version label of these databases")
    ap.add_argument("--index", required=True, help="gd-index-*.json.gz built from the same game data")
    ap.add_argument("--only", choices=("anim", "boss", "vendor", "design"), help="one part only")
    a = ap.parse_args(argv)
    files = arz_files(Path(a.folder))
    if not files or files[0].name.lower() != "database.arz":
        print(f"No game database (database\\database.arz) found in {a.folder}.\n  What you can do: give the "
              "Grim Dawn installation folder, e.g. C:\\Steam\\steamapps\\common\\Grim Dawn.", file=sys.stderr)
        return 2
    if sys.maxsize <= 2 ** 32:
        print("NOTE: 32-bit Python - reading the game databases needs about 1 GB of memory and may fail.",
              file=sys.stderr)
    from . import pipeline as PL
    idx = PL.load_index(Path(a.index))
    fp = str((idx.meta or {}).get("fingerprint") or "")
    iv = str((idx.meta or {}).get("game_version") or "")
    if iv and iv != a.version:
        print(f"NOTE: the index says game version {iv}, --version says {a.version} - files are labelled "
              f"{a.version}, the fingerprint {fp or '?'} decides which game data they belong to.")
    out = Path(a.out)
    print(f"Reading {len(files)} game databases: {', '.join(f.name for f in files)} ...")
    db = Db(files)
    rc = 0
    if a.only in (None, "anim"):
        try:
            data = anim_tables(db, a.version, fp)
            if not data["tables"]:
                raise ValueError("no animation table records in the databases")
            write_json(out / f"anim-tables-{a.version}.json", data)
            print(f"  anim-tables-{a.version}.json: {len(data['tables'])} tables, {len(data['forms'])} forms, "
                  f"{len(data['skill_animation'])} skills")
        except Exception as ex:                 # noqa: BLE001 - one part failing must not stop the other
            print(f"Animation tables could not be read: {type(ex).__name__}: {ex}", file=sys.stderr)
            rc = 3
    if a.only in (None, "boss"):
        try:
            combat, player = boss_combat(db, getattr(idx, "bosse_roh", None) or {}, a.version, fp)
            if not combat["bosses"]:            # an empty file would win on the fingerprint and hide the checks
                raise ValueError("no boss record of the index found in the databases")
            write_json(out / f"boss-combat-{a.version}.json", combat)
            write_json(out / f"player-skills-{a.version}.json", player)
            print(f"  boss-combat-{a.version}.json: {len(combat['bosses'])} bosses; player-skills-{a.version}.json: "
                  f"{len(player['not_dispelable'])} not dispelable")
        except Exception as ex:                 # noqa: BLE001
            print(f"Boss data could not be read: {type(ex).__name__}: {ex}", file=sys.stderr)
            rc = 3
    if a.only in (None, "vendor"):
        try:
            vendor = vendor_ranks(db, a.version, fp)
            if not vendor["items"]:
                raise ValueError("no faction vendor tables in the databases")
            write_json(out / f"vendor-ranks-{a.version}.json", vendor)
            print(f"  vendor-ranks-{a.version}.json: {len(vendor['items'])} faction vendor augments")
        except Exception as ex:                 # noqa: BLE001
            print(f"Faction vendor tables could not be read: {type(ex).__name__}: {ex}", file=sys.stderr)
            rc = 3
    if a.only in (None, "design"):
        try:
            design = DD.extract(db.read_uncached, db.names(), a.version, fp)
            if not design["sets"] or not design["celestial_powers"]:
                raise ValueError("no item sets or celestial powers in the databases")
            write_json(out / f"{DD.PREFIX}-{a.version}.json", design)
            print(f"  {DD.PREFIX}-{a.version}.json: {len(design['sets'])} sets, {len(design['celestial_powers'])} "
                  f"celestial powers, {len(design['skills'])} mastery skills")
        except Exception as ex:                 # noqa: BLE001
            print(f"Design data could not be read: {type(ex).__name__}: {ex}", file=sys.stderr)
            rc = 3
    return rc


if __name__ == "__main__":
    from . import errors
    raise SystemExit(errors.run(main, "reference data"))
