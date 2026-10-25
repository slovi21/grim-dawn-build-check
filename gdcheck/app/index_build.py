"""Game data index builder (port of the .35 tools build_index + the index part of bossdaten).

Reads all .arz databases of the installation plus the localization (Text_XX.arc) and writes ONE
compact, versioned file:

    gd-index-<version>.json.gz

That file is the only game data the skill ships (the archives are too large to upload and too slow
to parse for every question). Runs only on the PC with the Grim Dawn installation:

    python -m gdcheck.app.index_build --version 1.3.0.8
    python -m gdcheck.app.index_build --version 1.3.0.8 --gd-dir "D:\\Steam\\steamapps\\common\\Grim Dawn"
    python -m gdcheck.app.index_build --version 1.3.0.8 --lang EN --out .

Same options and output as the .35 build_index; content: schema 9 (.70) = the .35 index (schema 8) plus the
meta fields NEW_META (the JSON differs from the .35 build in those fields and in the build date
`_meta.erstellt`). The JSON keys stay German - they are the index schema read by gdcheck.data.index.
Standard library only (pure-Python LZ4 from gdcheck.data.savefile).
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import json
import re
import sys
from pathlib import Path

from ..data.arz import ArzFile, arz_paths, load_localization
from ..data.fingerprint import arc_paths, db_fingerprint
from ..data.text import clean_text
from ..domain.fields import STAT_PREFIXES, STAT_SKIP

# ---------------------------------------------------------------------------
# Selection: which records go into the index?
# ---------------------------------------------------------------------------
# The database holds more than 200 record types. A build check needs gear, affixes, sets, skills
# and devotions only; monsters, level art, loot tables, sounds, quests and NPCs stay out (most of
# the database - the reason the index is small).

KEEP_TYPE_PREFIX = ("Armor", "Weapon", "Item", "Skill", "LootRandomizer", "Set", "Pet")

SKIP_TYPE_EXACT = {
    "LootRandomizerTable",   # references to affixes only, no values
    "SkillTree",             # UI structure
    "ItemContainer",
    "FixedItemContainer",
}

# Slot compatibility of components and augments: one boolean field per slot in the augment's own
# record (not at the item). Without them no advice can say whether an augment fits the item.
SLOT_FLAGS = [
    "head", "chest", "shoulders", "legs", "feet", "hands", "waist",
    "ring", "amulet", "medal", "bracelet", "shield", "offhand",
    "axe", "axe2h", "sword", "sword2h", "mace", "mace2h", "dagger",
    "scepter", "spear", "spear2h", "staff", "ranged1h", "ranged2h", "quest",
]

# Schema 9 (.70): strings and fields without a STAT_PREFIXES prefix reach the index through META_FIELDS only.
#   augmentMasteryName3  third "+N to all skills of <mastery>" bonus of an item (68 records; 4 and 5 do not exist)
#   skillDisplayName     name tag of a skill record (an aura or toggle has none, its _buff record has)
#   lifeMonitorPercent   low-health trigger of a passive (fires below this % of Health)
#   damageAbsorption, damageAbsorptionPercent   absorption of shields and buffs (flat per rank / % of damage)
# tools/index_patch.py adds exactly these fields to an existing index from the raw .arz.
NEW_META = ("augmentMasteryName3", "skillDisplayName", "lifeMonitorPercent",
            "damageAbsorption", "damageAbsorptionPercent")

# Descriptive fields kept next to the bonus values ("m" of a record entry).
META_FIELDS = [
    "Class", "itemLevel", "levelRequirement", "itemClassification",
    "itemSetName", "itemSkillName", "itemSkillLevel",
    "augmentSkillName1", "augmentSkillLevel1", "augmentMasteryLevel1",
    "augmentMasteryName1", "artifactClassification", "relicBonus",
    # items carry up to 4-5 skill bonuses (schema 2 lost names 2-5: Wildhowl chest, 26.09.2026)
    "augmentSkillName2", "augmentSkillName3", "augmentSkillName4", "augmentSkillName5",
    "augmentMasteryName2", "augmentMasteryLevel2",
    # rank of the item skill and skill modifiers the item grants (e.g. Werewolf +40 OA/DA)
    "itemSkillLevelEq", "modifierSkillName1", "modifierSkillName2",
    "modifierSkillName3", "modifierSkillName4", "modifierSkillName5",
    "modifiedSkillName1", "modifiedSkillName2", "modifiedSkillName3",
    "modifiedSkillName4", "modifiedSkillName5",
    "factionSource", "itemCost", "soulbound",
    # auras carry their values in the linked buff record
    "buffSkillName", "petSkillName",
    # time fields for the uptime rating (duration / cooldown) - never in "f" (no bonus sums)
    "skillCooldownTime", "skillActiveDuration", "itemSkillAutoController",
    # schema 5 (skill points): max/ultimate rank, tier, weapon damage of attacks - never in "f"
    "skillMaxLevel", "skillUltimateLevel", "skillTier", "weaponDamagePct",
    # schema 9 (.70)
    *NEW_META,
]

# Layout of the index. Higher = new fields; GD-Tools rebuilds older indexes automatically.
# 9: third mastery bonus, skill display tag, low-health trigger and absorption fields (NEW_META);
# 8: affix pools from the real loot fields; 7: monster bonuses per difficulty; 6: bosses; 5: constellations
INDEX_SCHEMA = 9

# Constellations: affinity (need/bonus) and the star mapping live in the UI records of this folder.
CONSTELLATION_PREFIX = "records/ui/skills/devotion/"

# Triggers of item skills (chance, trigger condition). Small, taken over completely.
CONTROLLER_PREFIX = "records/controllers/"

# Tables that make half the character sheet computable: base values per level and the conversion
# attribute point -> Health/Energy/OA/DA.
LEVEL_TABLES = [
    "records/creatures/pc/playerlevels.dbr",       # gain per level, maxima
    "records/creatures/pc/malepc01.dbr",           # start values of the character
    "records/creatures/pc/femalepc01.dbr",
    "records/game/gameengine.dbr",                 # e.g. armorDefensiveAbsorption
    "records/game/combatformulas.dbr",             # DA, OA and hit formulas
    "records/game/balancingadjustment_mp+difficulty_players01.dbr",
    # schema 7: monster bonuses per difficulty/player count and Ascendant
    "records/game/balancingadjustment_mp+difficulty_enemies01.dbr",
    "records/game/balancingadjustment_ultramode_enemies01.dbr",
]

# Fields the display name comes from (order = priority).
NAME_TAG_FIELDS = ("itemNameTag", "skillDisplayName", "lootRandomizerName", "description", "skillTabTitle")


# ---------------------------------------------------------------------------
# Affix pools: which prefixes/suffixes can roll on which item
# ---------------------------------------------------------------------------
# Tables (LootRandomizerTable) hold pairs <x>Name<N> / <x>Weight<N>; nested tables are resolved and
# weights passed on pro rata. Items get their tables directly (fields at the item) or through
# LootItemTable records that name the item. Nothing found: no pool (the evaluation estimates one).

def _number(field: str) -> str:
    m = re.search(r"(\d+)$", field)
    return m.group(1) if m else ""


def table_entries_lv(rec: dict) -> list:
    """[(target.dbr, weight, level_min, level_max)] of a LootRandomizerTable record
    (randomizerName<N>, randomizerWeight<N>, randomizerLevelMin<N>/Max<N>). No window: 0 to 9999."""
    targets, weights, lmin, lmax = {}, {}, {}, {}
    for k, v in rec.items():
        if k.startswith("_"):
            continue
        kl = k.lower()
        values = v if isinstance(v, list) else [v]
        if all(isinstance(x, str) for x in values):
            for i, x in enumerate(values):
                if x.lower().endswith(".dbr"):
                    targets[(_number(k), i)] = x.replace("\\", "/").lower()
            continue
        for i, x in enumerate(values):
            if not isinstance(x, (int, float)):
                continue
            if "levelmin" in kl:
                lmin[(_number(k), i)] = float(x)
            elif "levelmax" in kl:
                lmax[(_number(k), i)] = float(x)
            elif "eight" in kl:
                weights[(_number(k), i)] = float(x)
    return [(t, weights.get(key, 1.0), lmin.get(key, 0.0), lmax.get(key, 9999.0))
            for key, t in sorted(targets.items()) if weights.get(key, 1.0) > 0]


# Real fields of the item tables (LootItemTable_DynWeight, 1.3.0.8): prefixTableName<N> /
# prefixTableWeight<N> / prefixTableLevelMin<N>/Max<N>, the same for suffix, rarePrefix, rareSuffix.
# Up to schema 7 only fields with "randomizer" in the name were searched - the index had 0 pools.
_TABLE_FIELD = re.compile(r"^(rare)?(prefix|suffix)table(name|weight|levelmin|levelmax)(\d+)$", re.I)
KINDS = ("prefix", "suffix", "rare_prefix", "rare_suffix")


def randomizer_refs(rec: dict) -> dict:
    """{kind: [(table.dbr, weight, level_min, level_max)]}, kind in KINDS. Reads the real table
    fields (_TABLE_FIELD) and old fields with "randomizer" in the name (weight 1, no window)."""
    parts = {}
    for k, v in rec.items():
        m = _TABLE_FIELD.match(k)
        if not m:
            continue
        kind = ("rare_" if m.group(1) else "") + m.group(2).lower()
        entry = parts.setdefault((kind, m.group(4)), {})
        entry[m.group(3).lower()] = v
    out = {a: [] for a in KINDS}
    for (kind, _n), e in sorted(parts.items()):
        name = e.get("name")
        if not (isinstance(name, str) and name.lower().endswith(".dbr")):
            continue
        weight = float(e.get("weight", 1) or 0)
        if weight <= 0:
            continue
        out[kind].append((name.replace("\\", "/").lower(), weight,
                          float(e.get("levelmin", 0) or 0),
                          float(e.get("levelmax", 9999) or 9999)))
    for k, v in rec.items():
        kl = k.lower()
        if "randomizer" not in kl or k.startswith("_"):
            continue
        kind = "prefix" if "prefix" in kl else ("suffix" if "suffix" in kl else None)
        if not kind:
            continue
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, str) and x.lower().endswith(".dbr"):
                out[kind].append((x.replace("\\", "/").lower(), 1.0, 0.0, 9999.0))
    return out


def affix_pools(item_refs: dict, tables: dict, depth: int = 3, item_level: dict | None = None) -> dict:
    """item -> {kind: [[affix, share]]}, kind in KINDS.

    item_refs:  {item.dbr: {kind: [tab | (tab, weight[, lmin, lmax])]}}
    tables:     {tab.dbr: [(target, weight[, lmin, lmax])]}; targets that are tables themselves are
                resolved up to `depth` levels.
    item_level: {item.dbr: itemLevel} - tables outside their level window (prefixTableLevelMin/Max)
                drop out; the affixes inside all stay (affix windows apply to the actual drop level,
                which for monster infrequents and scaling items is often above the record's itemLevel:
                19 characters, 01.10.2026 - without filter 489/495 worn affixes in the pool, with the
                lower bound 461, with the full window 423)."""
    def norm(e):
        if isinstance(e, str):
            return e, 1.0, 0.0, 9999.0
        e = tuple(e)
        return (e + (1.0, 0.0, 9999.0)[len(e) - 1:])[:4]

    def fits(lv, lmin, lmax):
        return lv is None or lmin <= lv <= lmax

    def resolve(tab, share, level, collect):
        # recursion bounded by `depth` (3 levels); summing order as in .35 (floats stay identical)
        entries = [norm(e) for e in tables.get(tab) or []]
        total = sum(e[1] for e in entries) or 1.0
        for target, w, _a, _b in entries:
            a = share * w / total
            if target in tables and level < depth:
                resolve(target, a, level + 1, collect)
            elif target not in tables:
                collect[target] = collect.get(target, 0.0) + a
    out = {}
    for item, v in item_refs.items():
        lv = (item_level or {}).get(item)
        entry = {}
        for kind in KINDS:
            tabs = [norm(t) for t in v.get(kind) or []]
            tabs = [t for t in tabs if fits(lv, t[2], t[3])]
            total = sum(t[1] for t in tabs) or 1.0
            collect = {}
            for tab, w, _a, _b in tabs:
                resolve(tab, w / total, 1, collect)
            if collect:
                entry[kind] = [[a, round(g, 6)] for a, g in sorted(collect.items())]
        if entry:
            out[item] = entry
    return out


def wanted(rtype: str) -> bool:
    return rtype.startswith(KEEP_TYPE_PREFIX) and rtype not in SKIP_TYPE_EXACT


def meta_fields(rec: dict, rtype: str) -> dict:
    """The descriptive fields of a raw record ("m" of its index entry): META_FIELDS without empty values, plus the
    slot flags of components and augments. tools/index_patch.py uses the same rule for NEW_META."""
    m = {k: rec[k] for k in META_FIELDS if rec.get(k) not in (None, "", 0)}
    # components and augments only: which slots they fit
    if rtype in ("ItemEnchantment", "ItemRelic"):
        slots = [s for s in SLOT_FLAGS if rec.get(s)]
        if slots:
            m["slots"] = slots
    return m


def bonus_fields(rec: dict) -> dict:
    """All numeric bonus fields - scalars and level-indexed arrays (same filter as the save reader:
    prefix must match, requirements and costs drop out, zero values are left out)."""
    out = {}
    for key, val in rec.items():
        if key.startswith("_") or key in STAT_SKIP:
            continue
        if not any(key.startswith(p) for p in STAT_PREFIXES):
            continue
        if isinstance(val, list):
            if val and isinstance(val[0], (int, float)) and any(val):
                out[key] = [round(float(x), 3) for x in val]
        elif isinstance(val, (int, float)) and not isinstance(val, bool) and val:
            out[key] = round(float(val), 3)
        elif isinstance(val, str) and val and key.startswith("conversion"):
            # conversionPercentage alone is worthless without conversionInType / conversionOutType
            out[key] = val
    return out


# ---------------------------------------------------------------------------
# Bosses (schema 6): boss monsters and their skills (port of bossdaten.boss_tags/boss_auszug)
# ---------------------------------------------------------------------------

SUPERBOSS_NAMES = ("Crate of Entertainment", "Callagadra, Scion of the Sands",
                   "Ravager of Souls", "Ravager of Flesh", "Ravager of Minds",
                   "Avatar of Mogdrogen", "Lokarr", "Clone of John Bourbon")
_ACHIEVEMENT = re.compile(r"^(?:Slay|Defeat)\s+(?:the\s+)?(.+?)(?:\s+(?:in|at)\s+|\.|$)")
BOSS_MONSTER_FIELDS = ("description", "monsterClassification", "charLevel", "attackSkillName")


def _boss_norm(t: str) -> str:
    """'Loghorrean, the Voice' == 'Loghorrean, Voice'."""
    return re.sub(r"\bthe\b|[^a-z]", "", t.lower())


def boss_tags(loc: dict) -> dict:
    """{tag: name} of all bosses: superbosses, bosses from the achievement texts, Shattered Realm
    bosses (tagGDX2EndlessDungeon_Boss*), Nemesis enemies."""
    names = set(SUPERBOSS_NAMES)
    for k, v in loc.items():
        if isinstance(v, str) and k.lower().startswith("ach") and k.endswith("Desc"):
            m = _ACHIEVEMENT.match(v.strip())
            if m and m.group(1) and not m.group(1)[0].isdigit() and "Nemesis" not in v:
                names.add(m.group(1).split(" in the name of")[0].split(" in the lands of")[0]
                          .strip().rstrip(","))
    wanted_names = {_boss_norm(n) for n in names}
    out = {}
    for k, v in loc.items():
        if not isinstance(v, str) or len(v) > 60 or k.lower().startswith("ach"):
            continue
        if _boss_norm(v) in wanted_names or k.startswith(("tagGDX2EndlessDungeon_Boss", "tagNemesis_")):
            out[k] = v
    return out


def boss_extract(dbs, loc: dict, verbose: bool = False, out=print) -> dict:
    """{name: {"record", "monster": {...}, "skills": {path: {...}}}} - per name the monster record
    with the most skills (mostly the highest tier)."""
    tags = boss_tags(loc)

    def read(name):
        for db in reversed(dbs):
            if name in db.records:
                r = db.read_record(name)
                if r is not None:
                    return r
        return None
    result = {}
    seen = set()
    for db in dbs:
        for name, info in db.records.items():
            if name in seen or getattr(info, "rtype", "") != "Monster":
                continue
            seen.add(name)
            rec = read(name) or {}
            tag = rec.get("description")
            if tag not in tags:
                continue
            m = {k: v for k, v in rec.items() if k in BOSS_MONSTER_FIELDS
                 or k.startswith(("defensive", "skillName"))}
            refs = [v for k, v in rec.items() if (k.startswith("skillName") or k == "attackSkillName")
                    and isinstance(v, str) and v.endswith(".dbr")]
            skills = {}
            for s in refs:
                sr = read(s)
                if sr:
                    skills[s] = {k: v for k, v in sr.items() if not k.startswith("_")
                                 and k.startswith(("offensive", "defensive", "weaponDamagePct", "retaliation"))
                                 and isinstance(v, (int, float, list))}
            known = result.get(tags[tag])
            if known is None or len(skills) > len(known["skills"]):
                result[tags[tag]] = {"record": name, "monster": m, "skills": skills}
    if verbose:
        out(f"  Bosses: {len(result)} with skills (of {len(tags)} boss names)")
    return result


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

class NoDatabase(Exception):
    """No .arz database below the game folder."""


def index_data(game_dir: Path, version: str, lang: str = "EN", verbose: bool = False, out=print) -> dict:
    """The index as a dict (what build() writes). `out` receives the progress lines."""
    gd = Path(game_dir)
    dbs = arz_paths(gd)
    if not dbs:
        raise NoDatabase(f"No .arz database found in {gd}. "
                         f"Does --gd-dir really point to the Grim Dawn folder?")

    loc: dict[str, str] = {}
    for p in arc_paths(gd, lang):
        loc.update(load_localization(p))
        if verbose:
            out(f"  Localization: {p.name} from {p.parent.parent.name}  "
                f"({len(loc):,} tags total)".replace(",", "."))
    if not loc:
        out(f"  ! No Text_{lang}.arc found - names stay empty, values are still correct.")

    def display_name(rec: dict) -> str:
        for f in NAME_TAG_FIELDS:
            tag = rec.get(f)
            if isinstance(tag, str) and tag:
                name = clean_text(loc.get(tag, ""))
                if name:
                    return name
        fd = rec.get("FileDescription")
        return clean_text(fd) if isinstance(fd, str) else ""

    records: dict[str, dict] = {}
    controllers: dict[str, dict] = {}
    sources = []
    fields_total = 0

    raw_tables: dict[str, list] = {}       # LootRandomizerTable -> entries
    item_refs: dict[str, dict] = {}        # item -> affix tables
    constellations: dict[str, dict] = {}   # UI records of the constellations
    opened = []
    # loot table diagnosis (schema 7 had 0 pools): count "Loot*" record types and keep the field
    # names of the first LootItemTable record, so -v shows why nothing matches
    loot_types: dict[str, int] = {}
    loot_sample: dict = {}
    for path in dbs:
        db = ArzFile(path)
        opened.append(db)
        n_before = len(records)
        seen = 0
        for name, info in db.records.items():
            if info.rtype.startswith("Loot"):
                loot_types[info.rtype] = loot_types.get(info.rtype, 0) + 1
            if name.startswith(CONTROLLER_PREFIX):
                crec = db.read_record(name)
                if crec:
                    controllers[name] = {k: v for k, v in crec.items()
                                         if not k.startswith("_")
                                         and isinstance(v, (int, float, str))
                                         and v not in ("", 0)}
                continue
            if name.lower().startswith(CONSTELLATION_PREFIX):
                srec = db.read_record(name)
                if srec:
                    constellations[name.lower()] = {
                        k: v for k, v in srec.items() if not k.startswith("_")
                        and isinstance(v, (int, float, str, list)) and v not in ("", 0)}
                continue
            if info.rtype == "LootRandomizerTable":
                trec = db.read_record(name)
                if trec:
                    raw_tables[name.lower()] = table_entries_lv(trec)
                continue
            if info.rtype.startswith("LootItemTable"):
                trec = db.read_record(name) or {}
                if not loot_sample and trec:
                    loot_sample = {"record": name, "typ": info.rtype,
                                   "felder": sorted(k for k in trec if not k.startswith("_"))[:40]}
                refs = randomizer_refs(trec)
                if any(refs.values()):
                    for _k, v in trec.items():
                        for x in (v if isinstance(v, list) else [v]):
                            if isinstance(x, str) and x.lower().endswith(".dbr") \
                                    and "/items/" in x.lower() and "loot" not in x.lower():
                                target = item_refs.setdefault(x.replace("\\", "/"), {a: [] for a in KINDS})
                                for kind in KINDS:
                                    target[kind] += [t for t in refs[kind] if t not in target[kind]]
                continue
            if not wanted(info.rtype):
                continue
            rec = db.read_record(name)
            if not rec:
                continue
            if info.rtype.startswith(("Armor", "Weapon", "Item")):
                refs = randomizer_refs(rec)
                if any(refs.values()):
                    target = item_refs.setdefault(name, {a: [] for a in KINDS})
                    for kind in KINDS:
                        target[kind] += [t for t in refs[kind] if t not in target[kind]]
            seen += 1
            f = bonus_fields(rec)
            m = meta_fields(rec, info.rtype)
            n = display_name(rec)
            if not (f or m or n):
                continue
            entry = {"t": info.rtype}
            if n:
                entry["n"] = n
            if m:
                entry["m"] = m
            if f:
                entry["f"] = f
                fields_total += len(f)
            records[name] = entry            # later .arz wins
        sources.append({
            "datei": path.name,
            "mb": round(path.stat().st_size / 1048576, 2),
            "records_gesamt": len(db.records),
            "davon_uebernommen": seen,
        })
        if verbose:
            out(f"  {path.name:<16} {sources[-1]['mb']:>7.2f} MB  "
                f"{len(db.records):>6} Records  -> {seen:>5} relevant  "
                f"(+{len(records) - n_before} new)")

    if verbose:
        out(f"  Constellation records: {len(constellations)}"
            + ("" if constellations else "  (none - devotion swaps stay 'value only')"))
    # level windows: tables and affixes apply to matching item levels only
    level_lower = {k.lower(): (e.get("m") or {}).get("itemLevel") for k, e in records.items()}
    item_level = {i: level_lower.get(i.lower()) for i in item_refs}
    loot = affix_pools(item_refs, raw_tables, item_level=item_level)
    if verbose:
        out(f"  Affix tables: {len(raw_tables)}, items with affix pools: {len(loot)}"
            + ("" if loot else "  (none found - reroll advice stays estimated)"))
        if not loot:
            out(f"    Loot record types: {loot_types or 'none'}")
            out(f"    Items referenced by item tables: {len(item_refs)}")
            if loot_sample:
                out(f"    First {loot_sample['typ']}: {loot_sample['record']}")
                out(f"    Its fields: {', '.join(loot_sample['felder'])}")

    tables = {}
    for path in LEVEL_TABLES:
        for db in reversed(opened):
            rec = db.read_record(path)
            if rec:
                tables[path] = {k: v for k, v in rec.items()
                                if not k.startswith("_") and isinstance(v, (int, float, list, str))}
                break
    if verbose:
        if tables:
            out(f"  Level and attribute tables: {len(tables)} of {len(LEVEL_TABLES)} found")
            for path in tables:
                out(f"    {path}")
        else:
            out("  ! No level/attribute tables found - Health, OA and DA stay partial sums.")

    # The localization goes into the index: without it a skill outside the installation can
    # resolve neither class names nor the templates of the bonus lines.
    tags = {k: v for k, v in loc.items() if v}
    if verbose:
        out(f"  Localization in index: {len(tags):,} tags".replace(",", "."))
    # schema 6: bosses with their skills (damage profiles, gdcheck.data.bosses)
    try:
        bosses = boss_extract(opened, {k: clean_text(v) for k, v in loc.items() if v}, verbose, out)
    except Exception as e:                                      # noqa: BLE001
        bosses = {}
        if verbose:
            out(f"  ! Bosses not read ({e}) - boss tables use the reference file.")

    if verbose:
        out(f"  Triggers (controllers): {len(controllers)}")
    # _meta stays IN FRONT: GD-Tools (local_env.index_meta) reads only the start of the file
    return {
        "_meta": {
            "schema": INDEX_SCHEMA,
            "game_version": version,
            "fingerprint": db_fingerprint(gd),
            "erstellt": datetime.date.today().isoformat(),
            "sprache": lang,
            "quellen": sources,
            "records": len(records),
            "loot_tabellen": len(loot),
            "sternbild_records": len(constellations),
            "bosse": len(bosses),
            "bonusfelder": fields_total,
            "hinweis": "Derived extract of the Grim Dawn database. "
                       "Gear, affixes, sets, skills and devotions only; "
                       "bonus fields only. Valid only for the "
                       "game_version above.",
        },
        "tabellen": tables,
        "loot_tabellen": loot,
        "sternbilder": constellations,
        "bosse": bosses,
        "tags": tags,
        "controller": controllers,
        "records": records,
    }


def encode(index: dict) -> tuple[bytes, bytes]:
    """(raw JSON, gzip) exactly as the .35 builder wrote them."""
    raw = json.dumps(index, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return raw, gzip.compress(raw, 9)


def default_version(game_dir: Path) -> str:
    """Label without a version number: the database fingerprint (what counts is whether the index
    fits the game data, not what the patch is called)."""
    return "db" + db_fingerprint(Path(game_dir))[:8]


def build(game_dir: Path, out_dir: Path, version: str | None, lang: str = "EN", verbose: bool = False,
          out=print) -> Path:
    """Build the index of `game_dir` into out_dir/gd-index-<version>.json.gz and return the path."""
    version = version or default_version(game_dir)
    index = index_data(Path(game_dir), version, lang, verbose, out)
    _raw, blob = encode(index)
    target = Path(out_dir) / f"gd-index-{version}.json.gz"
    target.write_bytes(blob)
    return target


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Builds the compact game data index for the build check skill.")
    ap.add_argument("--version", default=None,
                    help="game version, four parts, e.g. 1.3.0.8 "
                         "(shown at the bottom of the main menu)")
    ap.add_argument("--gd-dir", default=None,
                    help="Grim Dawn install folder. Searched if omitted.")
    ap.add_argument("--lang", default="EN",
                    help="language code of Text_XX.arc (default EN)")
    ap.add_argument("--out", default=".", help="target folder (default: current)")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.gd_dir:
        gd = Path(args.gd_dir)
    else:
        from .local_env import installed_game_dir
        gd = installed_game_dir()
    if gd is None:
        sys.exit("Grim Dawn installation not found - please pass --gd-dir.")
    verbose = not args.quiet
    version = args.version or default_version(gd)

    print(f"Installation: {gd}")
    try:
        index = index_data(gd, version, args.lang, verbose)
    except NoDatabase as e:
        sys.exit(str(e))
    raw, blob = encode(index)
    target = Path(args.out) / f"gd-index-{version}.json.gz"
    target.write_bytes(blob)

    m = index["_meta"]
    print()
    print(f"Index written: {target}")
    print(f"  Version          {m['game_version']}   language {m['sprache']}")
    print(f"  Fingerprint      {m['fingerprint']}")
    print(f"  Records          {m['records']:,}")
    print(f"  Bonus fields     {m['bonusfelder']:,}")
    print(f"  JSON raw         {len(raw)/1048576:.2f} MB")
    print(f"  gzip             {len(blob)/1048576:.2f} MB")
    if len(blob) > 10 * 1048576:
        print("  ! Over 10 MB - too large for claude.ai. Next step: "
              "drop affixes (LootRandomizer) and low quality tiers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
