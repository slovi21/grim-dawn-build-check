"""Boss damage profiles from the game data (port of the .35 bossdaten.py).

Profile per boss (monster record + level-1 skill records): damage types
(sorted by number of skills), leech, crowd control, DA shred, resistances,
monster classification. Limits: second-level skills (debuff records, pets)
are not included; damage amounts per difficulty are equations - only the
types count. The resistance reduction of the Ravagers is NOT in the level-1
skills (it sits in their debuffs - analysis.bosses.ravager_rr reads them; boss_hazards reads RR,
reflect and purge of every boss for the expert table E5).
Display names of boss/monster skills and records: skill_label (B1).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..mechanics.policy import Confidence, rule

TYPEN = {"Physical": "Physical", "Pierce": "Pierce", "Fire": "Fire", "Cold": "Cold",
         "Lightning": "Lightning", "Poison": "Acid", "Life": "Vitality", "Aether": "Aether",
         "Chaos": "Chaos", "Bleeding": "Bleeding"}
DOT = {"Physical": "Physical", "Fire": "Fire", "Cold": "Cold", "Lightning": "Lightning",
       "Poison": "Acid", "Life": "Vitality", "Bleeding": "Bleeding"}
CC = {"offensiveFreezeMin": "Freeze", "offensiveStunMin": "Stun",
      "offensivePetrifyMin": "Petrify", "offensiveKnockdownMin": "Knockdown"}
# Passive Grundwerte aller Monster - kein eigener Angriff
PASSIV = ("resists_", "armorbase", "damage_totaladjuster", "boss_chest", "ascendantmode",
          "passiveproperties", "damagebase_")


def _zahl(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, list):
        z = [_zahl(x) for x in v]
        z = [x for x in z if x is not None]
        return max(z) if z else None
    try:
        s = str(v).strip().strip("[]").split(",")[0].split(";")[0]
        return float(s)
    except (ValueError, IndexError):
        return None


# ------------------------------------------------------------------ display names (B1)
NAME_TAG_FIELDS = ("skillDisplayName", "description", "itemNameTag")
_GAME_TEXTS: dict = {}


def _game_texts(idx) -> frozenset:
    """All localised texts of the index (once per index): an index name is shown only if it is one of
    them - the index falls back to a record's FileDescription, a designer note ('500% game breaking
    mechanics ...' on resists_heroboss), which is never a game text."""
    key = id(idx)
    if key not in _GAME_TEXTS:
        tags = getattr(idx, "tags", None) or {}
        _GAME_TEXTS.clear()                      # one index per process in practice - keep one entry
        _GAME_TEXTS[key] = frozenset(t for t in (idx.tag(k) for k in tags) if t)
    return _GAME_TEXTS[key]


_OF = re.compile(r"^([a-z]{3,}?[a-np-z])of([a-z]{3,})$")       # 'roarofmadness'; never 'roof', 'proofing'


def _split_of(word: str) -> list[str]:
    """'roarofmadness' -> ['roar', 'of', 'madness'] (.67). Guards: at least 3 letters on both sides and no
    'o' right before (roof/proof/spoof: 'oof'), letters only - 'profit', 'offensive', 'waterproofing' stay."""
    m = _OF.match(word)
    return [m.group(1), "of", m.group(2)] if m else [word]


def fallback_label(record: str, boss: str | None = None) -> str:
    """Readable stand-in for a record without a game text: file stem without '.dbr', '_' -> space, every
    word capitalised, a trailing level/variant number removed ('armorbase05' -> 'Armorbase'). .67: the
    boss's own name as first word is dropped (`boss` given), glued 'of' is split and a variant suffix
    '<x>secondary' reads '(<x>)' - 'loghorrean_roarofmadness_voidsecondary' of Loghorrean ->
    'Roar of Madness (void)'. A spawned monster's prefix (Sandscion) stays: it names who attacks; so does
    the boss name before a single word ('Loghorrean Void', the spawned void)."""
    stem = str(record).replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".dbr")
    words = [w for w in stem.replace("_", " ").split() if w]
    while len(words) > 1 and words[-1].isdigit():
        words.pop()
    if words:
        words[-1] = words[-1].rstrip("0123456789") or words[-1]
    variant = ""
    if len(words) > 1 and len(words[-1]) > len("secondary") and words[-1].lower().endswith("secondary"):
        variant = words.pop()[:-len("secondary")].lower()
    words = [x for w in words for x in _split_of(w)]
    first = str(boss or "").split()
    own = re.sub(r"[^a-z]", "", first[0].lower()) if first else ""
    if own and len(words) > 2 and words[0].lower() == own:      # a one-word rest keeps it: 'Loghorrean Void'
        words = words[1:]
    text = " ".join(w if w == "of" else w[:1].upper() + w[1:] for w in words) or stem
    return f"{text} ({variant})" if variant else text


def skill_label(idx, record: str, boss: str | None = None) -> str:
    """Display name of a boss/monster skill or monster record (B1): the game text of skillDisplayName /
    description / itemNameTag (via idx.tag), else the index name if it is a game text, else the
    fallback name (no boss skill carries a display tag - the fallback is the normal case). Never
    '.dbr', never snake_case."""
    if not record:
        return ""
    if idx is not None:
        rec = idx.record(record) if hasattr(idx, "record") else None
        for f in NAME_TAG_FIELDS:
            t = (rec or {}).get(f)
            if isinstance(t, str) and t:
                text = idx.tag(t)
                if text:
                    return text
        name = (rec or {}).get("_name")
        if isinstance(name, str) and name and name in _game_texts(idx):
            return name
    return fallback_label(record, boss)


def profil(monster: dict, skills: dict) -> dict:
    """monster: Felder des Monster-Records; skills: {pfad: Felder}."""
    zaehl = {}
    leech = da_minus = False
    cc = set()
    basis = None
    for pfad, f in skills.items():
        kurz = pfad.rsplit("/", 1)[-1]
        if kurz.startswith("damagebase_"):
            basis = kurz.split("_")[1].rstrip("0123456789").capitalize()
        if any(p in kurz for p in PASSIV):
            continue
        typen = set()
        for roh, name in TYPEN.items():
            if (_zahl(f.get(f"offensive{roh}Min")) or 0) > 0:
                typen.add(name)
        for roh, name in DOT.items():
            if (_zahl(f.get(f"offensiveSlow{roh}Min")) or 0) > 0:
                typen.add(name)
        if (_zahl(f.get("offensiveElementalMin")) or 0) > 0:
            typen |= {"Fire", "Cold", "Lightning"}
        for t in typen:
            zaehl[t] = zaehl.get(t, 0) + 1
        leech |= (_zahl(f.get("offensiveLifeLeechMin")) or 0) > 0
        da_minus |= (_zahl(f.get("offensiveSlowDefensiveAbilityMin")) or 0) > 0
        for feld, name in CC.items():
            if (_zahl(f.get(feld)) or 0) > 0:
                cc.add(name)
    if basis and basis in TYPEN.values():
        zaehl[basis] = zaehl.get(basis, 0) + 1          # Grundangriff der Waffe
    res = {}
    for roh, name in TYPEN.items():
        v = _zahl(monster.get(f"defensive{roh}"))
        if v:
            res[name] = v
    return {"typen": [t for t, _ in sorted(zaehl.items(), key=lambda x: (-x[1], x[0]))],
            "anzahl": zaehl, "leech": leech, "cc": sorted(cc), "da_minus": da_minus,
            "res": res, "klasse": monster.get("monsterClassification") or ""}


_PROFILE_CACHE: dict = {}
_FILE_CACHE: dict = {}


def from_index(db) -> dict:
    """{Anzeigename: profil} aus dem Index (Schema 6), sonst {}. Cached per index object
    (the raw boss data of a loaded index never changes)."""
    roh = getattr(db, "bosse_roh", None) or {}
    key = id(roh)
    hit = _PROFILE_CACHE.get(key)
    if hit is not None and hit[0] is roh:
        return hit[1]
    out = _from_index(roh)
    _PROFILE_CACHE.clear()
    _PROFILE_CACHE[key] = (roh, out)
    return out


def _from_index(roh) -> dict:
    out = {}
    for name, e in roh.items():
        skills = e.get("skills") or {}
        out[name] = profil(e.get("monster") or {}, skills)
    return out


def from_file(pfad: Path) -> dict:
    """JSON file, cached by path + modification time + size (read-only use)."""
    try:
        st = Path(pfad).stat()
        key = (str(pfad), st.st_mtime_ns, st.st_size)
        if key not in _FILE_CACHE:
            _FILE_CACHE[key] = json.loads(Path(pfad).read_text(encoding="utf-8"))
        return _FILE_CACHE[key]
    except (OSError, ValueError):
        return {}


def load(db, basis: Path) -> tuple[dict, str]:
    """(profiles, source) - index first (schema 6+), then the bosse-<version>.json that fits the game
    data (data.versioned; .53: the version key is 'game_version' - 'gd_version' never existed)."""
    p = from_index(db)
    if p:
        return p, "game database (index)"
    from . import versioned as V
    ver, fp = V.index_identity(db)
    r = V.resolve("bosse", ver or "1.3.0.8", fp, Path(basis) / "references")
    d = from_file(r.path) if r.path else {}
    if d:
        return d, f"game database (boss dump {r.label})" + (f" - {r.note}" if r.note else "")
    return {}, ""


def combat_meta(basis: Path, version: str = "1.3.0.8", fingerprint: str | None = None) -> dict:
    """references/boss-combat-<version>.json (refs_extract / tools/boss_extract.py) that fits the game
    data (data.versioned): level range and skill level equations per boss; {} if missing. '_note'
    says why the file may not fit (ASSUMED), '_file' names it."""
    from . import versioned as V
    r = V.resolve("boss-combat", version, fingerprint, Path(basis) / "references")
    d = r.data                                   # parsed once per file (versioned cache)
    if not d.get("bosses"):
        return {}
    out = dict(d)
    out["_note"], out["_file"], out["_label"] = r.note, r.path.name, r.label
    return out


def life_leech_resistance(raw: dict, meta: dict | None, player_level: int,
                          difficulty_bonus: float = 0.0, level_override: int | None = None, label=None) -> dict:
    """Life Leech Resistance of a boss in % (combat spec V2 5): monster record + its
    passive skills at their skill level + the difficulty bonus of the game database
    (added like resistances, capped at 100 - ASSUMED, plan E8)."""
    from ..mechanics import monster as MON
    meta = meta or {}
    level = MON.monster_level(player_level, meta.get("minLevel"), meta.get("maxLevel"), level_override,
                              meta.get("charLevel"))
    parts = []
    own = _zahl((raw.get("monster") or {}).get("defensiveSlowLifeLeach")) or 0.0
    if own:
        parts.append(("monster record", own))
    for rec, fields in sorted((raw.get("skills") or {}).items()):
        v = (fields or {}).get("defensiveSlowLifeLeach")
        if not v:
            continue
        eq = (meta.get("skill_levels") or {}).get(rec)
        if eq is None and isinstance(v, list):
            continue                            # level unknown -> do not guess a list entry
        lv = MON.skill_level(eq, level) if eq is not None else 1
        val = MON.at_level(v, lv)
        if val:
            parts.append(((label(rec) if label else rec.rsplit("/", 1)[-1]) + f" (skill level {lv})", val))
    if difficulty_bonus:
        parts.append(("difficulty", float(difficulty_bonus)))
    total = min(100.0, max(0.0, sum(v for _, v in parts)))
    return {"value": total, "parts": parts, "monster_level": level,
            "complete": not any(isinstance((f or {}).get("defensiveSlowLifeLeach"), list)
                                and (meta.get("skill_levels") or {}).get(r) is None
                                for r, f in (raw.get("skills") or {}).items())}


DMG_TYPES = ("Physical", "Pierce", "Fire", "Cold", "Lightning", "Poison", "Life", "Aether", "Chaos")
SLOW_TYPES = {"Bleeding": "Bleeding", "Physical": "InternalTrauma", "Fire": "Burn", "Cold": "Frostburn",
              "Lightning": "Electrocute", "Poison": "PoisonDot", "Life": "VitalityDecay"}
NON_ATTACK = ("Passive", "Buff", "Spawn", "Generator", "OnDeath", "Pet")
# skills that hit once per use and put a debuff (buffSkillName) on you - per-use rate, not a 1/s aura
BUFF_ATTACKS = ("Skill_AttackBuffRadius", "Skill_AttackBuff", "Skill_BuffAttackRadiusDuration",
                "Skill_BuffAttackRadiusDrop", "Skill_BuffAttackRadiusLightning")
# Boss behaviour (HEURISTIC, KG-08): how often a monster uses its attacks.
BASIC_PER_S = 1.0          # basic attacks per second (x the record's characterAttackSpeed, ASSUMED)
NO_CD_EVERY_S = 6.0        # a skill without cooldown is used about every 6 s
AURA_TICK_PER_S = 1.0      # damaging aura (radius toggle): one application per second (ASSUMED)
AURA_EXPOSURE = 0.5        # share of the time you stand in one spawned aura (HEURISTIC; you move out)
ATTACK_EDGES = ("skillName", "specialAttackSkillName", "attackSkillName", "initialSkillName")


def _uses(attack: dict, speed: float = 1.0) -> float:
    share = attack.get("share", 1.0)            # charged finale: its strike or the normal strikes
    if attack["basic"]:
        return BASIC_PER_S * speed * share
    return (1.0 / attack["cooldown"] if attack["cooldown"] else 1.0 / NO_CD_EVERY_S) * share


# ------------------------------------------------------------------ basic attacks from frames (.54, KG-08 step 3)
def _anm_key(path) -> str:
    """One spelling for table paths (= app.anim_frames.norm): lower case, '/', no leading './' or '/'."""
    p = str(path).replace("\\", "/").lower().strip()
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


@rule("boss_basic_attack_frames", Confidence.DERIVED,
      "basic attacks per second from the monster's animation frames with the player's engine rules "
      "(keyframes - 1, record characterAttackSpeed, slot multiplier, whole frames, pool by weight); "
      "an upper bound - the AI pauses and moves (.54, KG-08 step 3)")
def basic_attack_timing(combat: dict, record: str, frames, speed_pct: float = 0.0, finale_n: int = 0,
                        special: str | None = None, charge_pct: float = 0.0, cast: bool = False) -> dict | None:
    """Basic attacks (strikes) per second of a monster from its animation frames - the player's
    engine rules (mechanics.animation): played frames = keyframes - 1 (ruleset 'anim_playback'),
    raw = played / (characterAttackSpeed of the record x slot multiplier x speed factor), whole
    frames (ruleset 'frame_rounding'; the range holds the rule pairs still open, KG-09 - the player
    measurements applied to monsters by analogy, ASSUMED), a pool averaged by weight. characterAttackSpeed plays the
    part of the player's 1.25 (same record field, DERIVED); speed factor = (1 + weapon base speed,
    ASSUMED as for the player; none unarmed and for casts) x (1 + `speed_pct` / 100). An attack skill
    that is no weapon skill (`cast`: projectile, spell) uses the spell pool and characterSpellCastSpeed
    as the player's casts do. An attack skill with a special animation plays it on every strike -
    every strike is a use of the skill, as the player model times Cadence (ASSUMED); for a charged
    finale (finale_n > 0) 'special on the finale only' is the alternative in the range. `frames`:
    object with ftab/rounding/playback (combat.frames.FrameModel). None without boss file schema 4,
    frames or a pool with weight. The rate is an UPPER bound: the AI pauses, moves and uses skills
    (HEURISTIC uptime 100%)."""
    from ..mechanics import animation as AN
    info = (combat.get("monsters") or {}).get(record)
    ftab = getattr(frames, "ftab", None)
    if not info or not ftab or not info.get("mode"):
        return None
    pre = ((combat.get("anim_tables") or {}).get(info.get("table")) or {}).get(info["mode"]) or {}
    rounding = getattr(frames, "rounding", "up") or "up"
    playback = getattr(frames, "playback", "n-1") or "n-1"
    assets = ftab.get("assets") or {}
    notes = []

    def pool(entries, pb=playback):
        out, heads, miss = [], [], 0
        for anm, mult, w in entries:
            try:
                mult, w = float(mult), float(w)
            except (TypeError, ValueError):
                miss += 1
                continue
            if w <= 0 or mult <= 0:                 # weight 0: never picked (review .54: no division by 0)
                continue
            h = assets.get(anm) or assets.get(_anm_key(anm))
            if h:
                out.append((AN.playback_frames(float(h), pb), mult, w))
                heads.append(int(h))
            else:
                miss += 1
        return out, heads, miss

    kind = "spell" if cast else "attack"
    atk, heads, miss = pool(pre.get(kind) or [])
    if not atk:
        return None
    sp, sp_heads, sp_miss = pool((pre.get("special") or {}).get(special) or []) if special else ([], [], 0)
    if special and not sp:
        notes.append(f"special animation '{special}' not in the table or without frames - {kind} pool (ASSUMED)")
    if miss or sp_miss:
        notes.append(f"{miss + sp_miss} animation(s) without frames left out, weights renormalised (ASSUMED)")
    char = float(info.get("cast_speed" if cast else "attack_speed") or 1.0)
    w = info.get("weapon") or {}
    offs = [float(h.get("base_speed") or 0.0) for h in (w.get("main"), w.get("off"))
            if h and str(h.get("class", "")).startswith("Weapon") and "Armor" not in str(h.get("class", ""))]
    # shields/off-hands carry no speed; two weapons averaged; unarmed and casts: none (as for the player)
    w_off = 0.0 if cast or info["mode"] == "unarmed" else AN.weapon_offset(offs)

    def rate(pct, rnd, how, entries=None, special_entries=None):
        sf = max(1.0 + w_off, 0.05) * max(1.0 + pct / 100.0, 0.05)
        t_pool = AN.pool_time(entries or atk, char, sf, rnd)
        spe = special_entries if special_entries is not None else sp
        t_sp = AN.pool_time(spe, char, sf, rnd) if spe else 0.0
        if sp and how == "finale":
            cycle = finale_n * t_pool + t_sp
            return (finale_n + 1) / cycle if cycle > 0 else 0.0
        t = t_sp if sp and how == "every" else t_pool
        return 1.0 / t if t > 0 else 0.0

    how = "every" if sp else "pool"
    main = rate(speed_pct, rounding, how)
    if main <= 0:
        return None
    alts = {}
    for rd, pb in AN.alternative_rules(rounding, playback):   # rule pairs still open (KG-09), same engine
        a_atk = pool(pre.get(kind) or [], pb)[0]
        a_sp = pool((pre.get("special") or {}).get(special) or [], pb)[0] if sp else []
        alts[f"{'all keyframes' if pb == 'n' else 'keyframes - 1'}, {'up' if rd == 'up' else 'to nearest'}"] = \
            rate(speed_pct, rd, how, a_atk, a_sp)
    if sp:
        alts["pool only"] = rate(speed_pct, rounding, "pool")
        if finale_n > 0:
            alts["special on the finale only"] = rate(speed_pct, rounding, "finale")
    if charge_pct:
        alts["no charges"] = rate(speed_pct - charge_pct, rounding, how)
    if info.get("record_attack_speed") and not cast:     # the record's slot speed INSTEAD of the table's
        r = rate(speed_pct, rounding, how, [(f, float(info["record_attack_speed"]), wt) for f, _m, wt in atk])
        if abs(r - main) > 1e-12:
            alts["record slot speed instead of the table's"] = r
    alts = {k: v for k, v in alts.items() if v > 0}
    lo, hi = min([main, *alts.values()]), max([main, *alts.values()])
    pool_txt = "/".join(str(h) for h in heads)
    what = f"{kind} pool {pool_txt} keyframes"
    if sp:
        what = (f"'{special}' {'/'.join(str(h) for h in sp_heads)} keyframes on every strike"
                + (f" (finale every {finale_n + 1}.)" if finale_n > 0 else "") + f"; {kind} pool {pool_txt}")
    sf_txt = (f"{'cast ' if cast else ''}x{char:.2f}" + (f", {speed_pct:+g}% speed" if speed_pct else "")
              + (f", weapon {w_off:+.2f}" if w_off else ""))
    out = {"rate": main, "span": (lo, hi), "alternatives": alts, "heads": heads, "special": special if sp else None,
           "char_speed": char, "speed_pct": speed_pct, "weapon_offset": w_off, "mode": info["mode"], "cast": cast,
           "notes": notes + ([str(ftab["assets_note"])] if ftab.get("assets_note") else []),
           "what": what, "speed": sf_txt, "range_label": None}
    out["text"] = timing_text(out)
    return out


def timing_text(timing: dict) -> str:
    """'<rate>/s from animation frames (<pool>; <speed>; range <lo>-<hi>/s[ incl. <label>])' from the
    structured fields of a basic-attack timing (B8a: formatted on output, never patched with a regex)."""
    lo, hi = timing["span"]
    label = timing.get("range_label")
    return (f"{timing['rate']:.2f}/s from animation frames ({timing['what']}; {timing['speed']}; "
            f"range {lo:.2f}-{hi:.2f}/s" + (f" incl. {label}" if label else "") + ")")


def _is_cast(f: dict) -> bool:
    """An attack skill without weapon (projectile, spell, radius cast) is timed as a cast (spell pool,
    cast speed) - the player model's rule for actions without weapon damage."""
    cls = str(f.get("Class", ""))
    return bool(cls) and "Weapon" not in cls and not f.get("weaponDamagePct")


def _speed_mods(skill_levels: dict, skills: dict, level: int, ascendant: bool,
                attack_skill: str | None, label=None) -> tuple[float, float, int, str | None, list, bool]:
    """(passive speed %, attack-skill charge %, finale_n, special animation, parts, cast) of one
    monster: attack (cast for a cast attack skill) + total speed modifiers of its passives at their
    skill level (the ascendant adjustment only in ascendant mode); a charged attack skill at full
    charge (last list value, ASSUMED - the boss keeps attacking, charges last skillChargeDuration)."""
    from ..mechanics import monster as MON
    passive, charge, finale_n, special, parts = 0.0, 0.0, 0, None, []
    cast = _is_cast(skills.get(attack_skill) or {}) if attack_skill else False
    key = "characterSpellCastSpeedModifier" if cast else "characterAttackSpeedModifier"
    for rec, eq in sorted(skill_levels.items()):
        f = skills.get(rec) or {}
        cls = str(f.get("Class", ""))
        stem = rec.rsplit("/", 1)[-1].removesuffix(".dbr")
        if "ascendantmode" in stem and not ascendant:
            continue
        lv = MON.skill_level(eq, level)
        if rec == attack_skill:
            v = f.get(key)
            c = float(v[-1]) if "Charged" in cls and isinstance(v, list) and v else MON.at_level(v, lv)
            if c:
                charge += c
                parts.append(f"{(label or _raw)(rec)} {c:+g}%" + (" at full charge (ASSUMED)" if "Charged" in cls else ""))
            if "ChargedFinale" in cls:
                finale_n = int(MON.at_level(f.get("skillChargeLevel"), lv) or 0)
            special = str(f["skillSpecialAnimationName"]) if f.get("skillSpecialAnimationName") else None
        elif skill_kind(cls) == "passive":
            v = MON.at_level(f.get(key), lv) + MON.at_level(f.get("characterTotalSpeedModifier"), lv)
            if v:
                passive += v
                parts.append(f"{(label or _raw)(rec)} {v:+g}%")
    return passive, charge, finale_n, special, parts, cast


def _buff_pct(buffs) -> float:
    """Largest '+N%' of the named speed buffs ('name +N%')."""
    best = 0.0
    for b in buffs or ():
        try:
            best = max(best, float(str(b).rsplit(" ", 1)[-1].rstrip("%")))
        except ValueError:
            continue
    return best


def _add_alternative(timing: dict, label: str, rate: float) -> None:
    """One more alternative in a basic-attack range (span and text follow)."""
    if not rate or rate <= 0:
        return
    timing["alternatives"][label] = rate
    timing["span"] = (min(timing["span"][0], rate), max(timing["span"][1], rate))
    timing["range_label"] = label
    timing["text"] = timing_text(timing)


def _weapon_attack(record: str, mods: dict) -> dict:
    """Basic weapon swing of an armed monster without an attack skill: 100% weapon damage of its damage
    passive (the item's own damage is not added, ASSUMED - KG-08 step 4)."""
    a = {"skill": record, "name": "weapon attack", "class": "Weapon", "skill_level": 1, "basic": True, "flat": {},
         "dot_total": {}, "weapon_pct": 100.0, "pct_current_life": 0.0, "cooldown": None, "source": "boss",
         "count": 1.0, "slows": [], "attack_speed_slow": 0.0, "aura": False}
    _scale(a, mods)
    return a


def _speed_buffs(recs, skills: dict, level_of, ally_only: bool = False, label=None) -> list:
    """['name +N%'] of self/ally speed buffs (BuffSelf toggles, ally auras) - when the AI uses them is
    not in the game database, so they are named, not modelled. `ally_only` (spawns, .62 code review):
    only radius/other buffs can reach the boss - a spawn's self buff never speeds the boss up."""
    out = []
    for rec in recs:
        f = skills.get(rec) or {}
        cls = str(f.get("Class", ""))
        if "Buff" not in cls or "Debuf" in cls or "Passive" in cls:
            continue
        if ally_only and not any(t in cls for t in ("Radius", "Other")):
            continue
        child = skills.get(f.get("buffSkillName")) if isinstance(f.get("buffSkillName"), str) else None
        lv = level_of(rec)
        from ..mechanics import monster as MON
        v = sum(MON.at_level(src.get(k), lv) for src in (f, child or {})
                for k in ("characterAttackSpeedModifier", "characterTotalSpeedModifier"))
        if v > 0:
            out.append(f"{(label or _raw)(rec)} {v:+g}%")
    return sorted(set(out))


def skill_kind(cls: str) -> str | None:
    """passive | aura (radius toggle, ticks while you stand in it) | buff_attack (hit + debuff per
    use) | attack | None (buffs on itself, spawns, ...)."""
    if "Passive" in cls and "OnLife" not in cls:
        return "passive"
    if "Toggled" in cls and "Radius" in cls:
        return "aura"
    if cls in BUFF_ATTACKS:
        return "buff_attack"
    if any(k in cls for k in NON_ATTACK):
        return None
    return "attack"


def _merged(rec: str, skills: dict) -> dict:
    """Fields of a skill plus the offensive fields of its debuff (buffSkillName -> SkillBuff_Debuf*)."""
    f = dict(skills.get(rec) or {})
    child = f.get("buffSkillName")
    if isinstance(child, str):
        cf = skills.get(child) or {}
        if str(cf.get("Class", "")).startswith("SkillBuff_Debuf"):
            for k, v in cf.items():
                if k.startswith("offensive") and k not in f:
                    f[k] = v
    return f


def _monster_attacks(skill_levels: dict, skills: dict, level: int, total: float, ascendant: bool,
                     attack_skill: str | None) -> tuple[list, dict]:
    """Passives (weapon flat damage, % damage), damaging attacks and auras of one monster's own skills."""
    from ..mechanics import monster as MON
    type_mod = {t: 0.0 for t in DMG_TYPES}
    weapon = {t: 0.0 for t in DMG_TYPES}
    attacks = []
    for rec, eq in sorted(skill_levels.items()):
        f = skills.get(rec) or {}
        cls = str(f.get("Class", ""))
        lv = MON.skill_level(eq, level)
        stem = rec.rsplit("/", 1)[-1].removesuffix(".dbr")
        if "ascendantmode" in stem and not ascendant:
            continue
        kind = "attack" if rec == attack_skill else skill_kind(cls)
        if kind == "passive":
            total += MON.at_level(f.get("offensiveTotalDamageModifier"), lv)
            for t in DMG_TYPES:
                if not f.get(f"offensive{t}ModifierChance"):
                    type_mod[t] += MON.at_level(f.get(f"offensive{t}Modifier"), lv)
                lo = MON.at_level(f.get(f"offensive{t}Min"), lv)
                hi = MON.at_level(f.get(f"offensive{t}Max"), lv) or lo
                weapon[t] += (lo + hi) / 2
            continue
        if kind is None:
            continue
        a = _attack(rec, _merged(rec, skills) if kind in ("aura", "buff_attack") else f, lv, rec == attack_skill)
        if a:
            a["aura"] = kind == "aura"
            attacks.append(a)
            n = int(MON.at_level(f.get("skillChargeLevel"), lv)) if "ChargedFinale" in cls else 0
            if n > 0:                                   # every (n+1). strike is the finale (Cadence rule)
                a["share"] = 1.0 / (n + 1)
                a["name"], a["variant"] = f"{stem} (finale, every {n + 1}. strike)", f"finale, every {n + 1}. strike"
                attacks.append(dict(a, flat={}, dot_total={}, weapon_pct=100.0, pct_current_life=0.0, slows=[],
                                    attack_speed_slow=0.0, share=n / (n + 1), name=f"{stem} (normal strikes)",
                                    variant="normal strikes"))
    mods = {"total_pct": total, "type_pct": type_mod, "weapon_flat": weapon}
    for a in attacks:
        _scale(a, mods)
    return attacks, mods


def _attack(rec: str, f: dict, lv: int, basic: bool = False) -> dict | None:
    from ..mechanics import monster as MON
    flat = {}
    for t in DMG_TYPES:
        lo = MON.at_level(f.get(f"offensive{t}Min"), lv)
        hi = MON.at_level(f.get(f"offensive{t}Max"), lv) or lo
        if lo or hi:
            flat[t] = (lo + hi) / 2
    dot = {}
    for t, dt in SLOW_TYPES.items():
        per_s = MON.at_level(f.get(f"offensiveSlow{t}Min"), lv)
        dur = MON.at_level(f.get(f"offensiveSlow{t}DurationMin"), lv)
        if per_s and dur:
            dot[dt] = per_s * dur
    wd = MON.at_level(f.get("weaponDamagePct"), lv)
    if basic and not wd:
        wd = 100.0
    pct_life = MON.at_level(f.get("offensivePercentCurrentLifeMin"), lv)
    slows = []
    for kind, key in (("attack", "offensiveSlowAttackSpeed"), ("total", "offensiveSlowTotalSpeed")):
        v = MON.at_level(f.get(key + "Min"), lv)
        if v:
            slows.append((kind, v, MON.at_level(f.get(key + "DurationMin"), lv)))
    if not (flat or dot or wd or pct_life):
        return None
    return {"skill": rec, "name": rec.rsplit("/", 1)[-1].removesuffix(".dbr"), "class": str(f.get("Class", "")),
            "skill_level": lv, "basic": basic, "flat": flat, "dot_total": dot, "weapon_pct": wd,
            "pct_current_life": pct_life, "cooldown": MON.at_level(f.get("skillCooldownTime"), lv) or None,
            "source": "boss", "count": 1.0, "slows": slows,
            "attack_speed_slow": max([v for k, v, _d in slows if k == "attack"] or [0.0])}


def _scale(a: dict, mods: dict) -> None:
    total, type_mod, weapon = mods["total_pct"], mods["type_pct"], mods["weapon_flat"]
    raw = dict(a["flat"])
    if a["weapon_pct"]:
        for t, v in weapon.items():
            if v:
                raw[t] = raw.get(t, 0.0) + v * a["weapon_pct"] / 100
    a["raw"] = {t: v * max(0.0, 1 + (total + type_mod.get(t, 0.0)) / 100) for t, v in raw.items() if v}
    a["dot_raw"] = {t: v * max(0.0, 1 + total / 100) for t, v in a["dot_total"].items()}


@rule("boss_use_rate", Confidence.HEURISTIC,
      "how often a boss and its spawns act: basic attacks from animation frames (upper bound) or 1/s x "
      "attack speed, skills once per cooldown or every 6 s, autocasts chance x parent rate, auras 1 tick/s "
      "(spawned: one at a time, half the time), spawns always alive and attacking (KG-08)")
def attack_profile(name: str, combat: dict, level: int, difficulty_total_pct: float = 0.0,
                   ascendant: bool = False, frames=None, label=None) -> dict:
    """Modelled attacks of one boss at monster level `level` (plan CA6.2a, KG-08).

    First level: every active skill with flat damage and the basic attack; monster passives give
    the weapon damage (flat) and % damage modifiers; the difficulty adds its total damage %; radius
    toggles of the boss are auras (tick 1/s, ASSUMED, you fight in their radius), attack-buffs hit
    once per use with their debuff. Second level (boss file schema 3, data.boss_graph): autocast
    attacks (chance x the parent's uses; only 'on attack' triggers), spawned monsters with their
    own attacks and auras (alive = min(petLimit, lifetime x spawn rate); spawn pets pick their
    monster by skill level, generators from a pool; monster level = boss level, ASSUMED; spawned
    auras one at a time AURA_EXPOSURE of the time, HEURISTIC), the enrage below a Health threshold,
    the fight phases and the boss's slows. Every attack carries `rate` (uses per second incl.
    count) and `source`.
    .54 (KG-08 step 3): with boss file schema 4 and `frames` (combat.frames.FrameModel) the basic
    attacks of the boss and of spawned monsters come from their animation frames
    (basic_attack_timing, upper bound); boss basic attacks and the autocasts riding on them carry
    `rate_enraged` (frames recounted at the enrage speed). Without them: BASIC_PER_S x attack speed.
    `label(record)` (B1, e.g. skill_label with the index) names skills and monsters in every text
    (gaps, speed parts, spawn lists, each attack's 'label' and 'origin'); without it the file stems
    (identifiers: 'name' and 'source' always keep them).
    Returns {"attacks", "mods", "gaps", "level", "enrage", "phases", "attack_speed", "slows", "basic",
    "basic_enraged", "spawn_basic", "speed_parts"}."""
    from ..mechanics import monster as MON
    boss = (combat.get("bosses") or {}).get(name)
    skills = combat.get("skills") or {}
    if not boss:
        return {"attacks": [], "mods": {}, "gaps": ["no boss data"], "level": level, "enrage": None,
                "phases": [], "attack_speed": 1.0, "slows": [], "basic": None, "basic_enraged": None,
                "spawn_basic": {}, "speed_parts": []}
    speed = float(boss.get("characterAttackSpeed") or 1.0)
    total0 = float(difficulty_total_pct)
    atk_skill = boss.get("attackSkillName")
    attacks, mods = _monster_attacks(boss.get("skill_levels") or {}, skills, level, total0, ascendant, atk_skill)
    passive, charge, finale_n, special, speed_parts, cast = _speed_mods(boss.get("skill_levels") or {}, skills,
                                                                        level, ascendant, atk_skill, label)
    timing = basic_attack_timing(combat, boss["record"], frames, passive + charge, finale_n, special, charge, cast)
    for a in attacks:
        if a["aura"]:
            a["rate"], a["source"] = AURA_TICK_PER_S, "boss aura"
        elif a["basic"] and timing:
            a["rate"], a["rate_source"] = timing["rate"] * a.get("share", 1.0), "frames"
        else:
            a["rate"] = _uses(a, speed)
    graph = boss.get("graph")
    if not graph or not isinstance(graph[0].get("edges"), list):
        attacks = _labelled(attacks, label)
        return {"attacks": attacks, "mods": mods, "level": level, "enrage": None, "phases": [],
                "attack_speed": speed, "slows": _slows(attacks), "basic": timing, "basic_enraged": None,
                "spawn_basic": {}, "speed_parts": speed_parts,
                "gaps": ["second-level skills, pets and spawned objects not modelled",
                         "boss phases and enrage not modelled"]}
    root = boss["record"]
    node = {(n["record"], n["owner"]): n for n in graph}
    by_record: dict = {}
    for n in graph:
        by_record.setdefault(n["record"], n)
    unmodelled: set = set()

    stem = label or _raw                             # display name (identifiers use _raw)

    def asc_skip(rec):
        return "ascendantmode" in rec and not ascendant

    def lv_of(rec, owner, fallback_eq=None):
        n = node.get((rec, owner))
        eq = n.get("level_eq") if n and n.get("level_eq") is not None else fallback_eq
        return MON.skill_level(eq, level) if eq is not None else 1

    first = [n for n in graph if n["owner"] == root and n["parent"] == root and n["kind"] == "skill"]
    autocast_children = {c for n in first if n.get("autocast")
                         for via, c in n.get("edges", []) if via == "autoCastSkill"}
    attacks = [a for a in attacks if a["skill"] not in autocast_children]      # secondaries: via the chance
    monsters = combat.get("monsters") or {}
    spawn_basic: dict = {}
    no_attack_skill: set = set()
    weapon_only: set = set()
    buffs: set = set(_speed_buffs([n["record"] for n in first], skills, lambda r: lv_of(r, root), label=label))
    enrage = None
    for n in first:
        f = skills.get(n["record"]) or {}
        if n.get("life_pct") and "OnLife" in str(f.get("Class", "")) and not asc_skip(n["record"]):
            lv = lv_of(n["record"], root)
            total_speed = MON.at_level(f.get("characterTotalSpeedModifier"), lv)
            enrage = {"skill": n["record"], "life_pct": float(n["life_pct"]),
                      "damage_pct": MON.at_level(f.get("offensiveDamageMultModifier"), lv),
                      "attack_speed_pct": MON.at_level(f.get("characterAttackSpeedModifier"), lv) + total_speed,
                      "cast_speed_pct": MON.at_level(f.get("characterSpellCastSpeedModifier"), lv) + total_speed,
                      "absorption_pct": MON.at_level(f.get("damageAbsorptionPercent"), lv)}
            break
    timing_enr = None
    if enrage and timing:                            # frames recounted at the enrage speed (not x linear)
        extra_pct = enrage["cast_speed_pct" if cast else "attack_speed_pct"]
        timing_enr = basic_attack_timing(combat, boss["record"], frames, passive + charge + extra_pct, finale_n,
                                         special, charge, cast)
    rate_of: dict = {}
    rate_of_enr: dict = {}
    for a in attacks:                                # all strikes of a skill (finale + normal strikes)
        rate_of[a["skill"]] = rate_of.get(a["skill"], 0.0) + a["rate"]
        if a["basic"] and timing_enr and a.get("rate_source") == "frames":
            a["rate_enraged"] = timing_enr["rate"] * a.get("share", 1.0)
            rate_of_enr[a["skill"]] = rate_of_enr.get(a["skill"], 0.0) + a["rate_enraged"]
    extra: list = []

    def spawned(spawn_rec, owner, spawn_rate, by, depth=0):
        f = skills.get(spawn_rec) or {}
        lv = lv_of(spawn_rec, owner)
        limit = MON.at_level(f.get("petLimit"), lv) or 1.0
        ttl = MON.at_level(f.get("spawnObjectsTimeToLive"), lv)
        burst = MON.at_level(f.get("petBurstSpawn"), lv) or 1.0
        alive_total = min(limit, (ttl * spawn_rate * burst) if ttl else limit)
        objs = f.get("spawnObjects")
        if isinstance(objs, list) and "Generator" not in str(f.get("Class", "")):
            pool = {str(_pick_at_level(objs, lv)): 1.0}                      # spawn pets: one monster per skill level
        else:
            lst = [o for o in (objs if isinstance(objs, list) else [objs]) if o]
            pool = {m: lst.count(m) / len(lst) for m in dict.fromkeys(lst)} if lst else {}
        out = []
        for m, share in pool.items():
            mnode = by_record.get(m)
            if mnode is None or mnode.get("kind") != "monster" or "Destructible" in mnode.get("class", ""):
                if mnode is None:
                    unmodelled.add(f"{stem(spawn_rec)}: {stem(m)} without monster data")
                continue
            alive = alive_total * share
            mine = [n for n in graph if n["owner"] == m and n["parent"] == m and n["kind"] == "skill"]
            own = {n["record"]: n.get("level_eq") for n in mine if n["via"] in ATTACK_EDGES and not asc_skip(n["record"])}
            minfo = monsters.get(m)
            if minfo is not None:                     # schema 4: the record's attackSkillName (.54 fix: a graph
                m_atk = minfo.get("attack_skill")     # node reached first through skillName hid it - tentacles)
            else:
                m_atk = next((n["record"] for n in mine if n["via"] == "attackSkillName"), None)
            m_attacks, _m_mods = _monster_attacks(own, skills, level, total0, ascendant, m_atk)
            m_speed = float(mnode.get("attack_speed") or 1.0)
            mp, mc, mf, msp, _parts, mcast = _speed_mods(own, skills, level, ascendant, m_atk, label)
            main_hand = str((((minfo or {}).get("weapon") or {}).get("main") or {}).get("class", ""))
            armed = main_hand.startswith("Weapon") and "Armor" not in main_hand
            mt = basic_attack_timing(combat, m, frames, mp + mc, mf, msp, mc, mcast) if (m_atk or armed) else None
            if mt:
                spawn_basic[stem(m)] = mt["text"]
            if not m_atk and armed:                   # KG-08 step 4 (.60): armed, no attack skill
                if mt and any(_m_mods["weapon_flat"].values()):
                    m_attacks.append(_weapon_attack(m, _m_mods))
                    weapon_only.add(stem(m))
                else:
                    no_attack_skill.add(stem(m))
            for a in m_attacks:
                a.update(source=f"spawn {_raw(m)}", count=alive, name=f"{_raw(m)}: {a['name']}", spawned_by=by,
                         monster=m,
                         alive=alive_total)
                if a["aura"]:
                    a["rate"] = 0.0
                elif a["basic"] and mt:
                    a["rate"], a["rate_source"] = mt["rate"] * a.get("share", 1.0) * alive, "frames"
                else:
                    a["rate"] = _uses(a, m_speed) * alive
                out.append(a)
            buffs.update(_speed_buffs([n["record"] for n in mine], skills, lambda r, _m=m: lv_of(r, _m), ally_only=True,
                                      label=label))
            for n in mine:
                if asc_skip(n["record"]):
                    continue
                nf = skills.get(n["record"]) or {}
                ncls = str(nf.get("Class", ""))
                if depth < 2 and ("Generator" in ncls or "SpawnPet" in ncls):
                    per = MON.at_level(nf.get("petPeriod"), lv_of(n["record"], m))
                    ccd = MON.at_level(nf.get("skillCooldownTime"), lv_of(n["record"], m))
                    sub = 1.0 / per if per else (1.0 / ccd if ccd else 1.0 / NO_CD_EVERY_S)
                    out += spawned(n["record"], m, sub * alive, f"{stem(n['record'])} of {stem(m)}", depth + 1)
                if n.get("autocast"):
                    unmodelled.add(f"autocasts of spawned monsters ({stem(m)}: {stem(n['record'])})")
        return out

    for n in first:
        rec = n["record"]
        if asc_skip(rec):
            continue
        f = skills.get(rec) or {}
        cls = str(f.get("Class", ""))
        lv = lv_of(rec, root)
        cd = MON.at_level(f.get("skillCooldownTime"), lv)
        base_rate = rate_of.get(rec, (1.0 / cd) if cd else 1.0 / NO_CD_EVERY_S)
        if "Generator" in cls or "SpawnPet" in cls:
            period = MON.at_level(f.get("petPeriod"), lv)
            extra += spawned(rec, root, 1.0 / period if period else base_rate, stem(rec))
        ac = n.get("autocast")
        if not ac:
            continue
        for via, c in n.get("edges", []):
            if via != "autoCastSkill" or asc_skip(c):
                continue
            chance = float(ac.get("chance") or 0.0) / 100
            if ac.get("trigger") != "AttackEnemy":
                unmodelled.add(f"{stem(c)} (cast when the boss is hit: {ac.get('trigger')})")
                continue
            cf = skills.get(c) or {}
            ccls = str(cf.get("Class", ""))
            clv = lv_of(c, root, n.get("level_eq"))
            ccd = MON.at_level(cf.get("skillCooldownTime"), clv)
            r = base_rate * chance
            if ccd:
                r = min(r, 1.0 / ccd)
            r_enr = None
            if rec in rate_of_enr:                    # rides on the basic attack: follows its enraged rate
                r_enr = rate_of_enr[rec] * chance
                if ccd:
                    r_enr = min(r_enr, 1.0 / ccd)
            if "SpawnPet" in ccls or "Generator" in ccls:
                extra += spawned(c, root, r, f"{stem(c)} ({chance * 100:.0f}% on {stem(rec)})")
                continue
            kind = skill_kind(ccls) or "attack"
            a = _attack(c, _merged(c, skills) if kind != "attack" else cf, clv)
            if a:
                _scale(a, mods)
                a.update(source=f"autocast {chance * 100:.0f}% on {_raw(rec)}", rate=r, aura=False,
                         origin_label=f"autocast {chance * 100:.0f}% on {stem(rec)}",
                         parent_basic=rec == boss.get("attackSkillName"))
                if r_enr is not None:
                    a["rate_enraged"] = r_enr
                extra.append(a)
    auras = [a for a in extra if a.get("aura")]
    n_auras = sum(a["count"] for a in auras)
    for a in auras:                                  # one spawned aura at a time: time share by count
        a["rate"] = AURA_TICK_PER_S * AURA_EXPOSURE * a["count"] / n_auras if n_auras > 0 else 0.0
    phases = boss.get("phases") or []
    gaps = []
    if phases and any(p["position"] < 0 for p in phases):
        gaps.append(f"earlier fight phases ({sum(1 for p in phases if p['position'] < 0)}) not modelled - the last "
                    "phase is the boss record")
    if unmodelled:
        gaps.append("not modelled: " + "; ".join(sorted(unmodelled)))
    if no_attack_skill:
        gaps.append("plain weapon attacks of armed spawned monsters without an attack skill not modelled ("
                    + ", ".join(sorted(no_attack_skill)) + ")")
    if weapon_only:
        gaps.append("plain weapon attacks of armed spawned monsters without an attack skill: weapon damage of their "
                    "damage passive at 100% weapon damage, the equipped item's own damage not added (ASSUMED) - "
                    + ", ".join(sorted(weapon_only)))
    best_buff = _buff_pct(buffs)
    in_range = False
    if timing and best_buff > 0:
        alt = basic_attack_timing(combat, boss["record"], frames, passive + charge + best_buff, finale_n, special,
                                  charge, cast)
        if alt:
            _add_alternative(timing, f"speed buff +{best_buff:g}%", alt["rate"])
            in_range = True
    if buffs:
        gaps.append("speed buffs - when the AI uses them (or the boss stands in a spawn's aura) is not in the game "
                    + (f"database: the largest (+{best_buff:g}%) is the upper end of the boss's basic-attack range, "
                       "not in the rate: " if in_range else "database: named, not modelled: ")
                    + ", ".join(sorted(buffs)))
    if timing or spawn_basic:
        gaps.append("basic attacks from animation frames are an upper bound (the AI pauses, moves, uses skills); "
                    "skill rates HEURISTIC; spawned auras one at a time half of the time (HEURISTIC)")
    else:
        gaps.append("boss and spawn action rates HEURISTIC; spawned auras one at a time half of the time (HEURISTIC)")
    every = _labelled(attacks + extra, label)
    return {"attacks": every, "mods": mods, "gaps": gaps, "level": level, "enrage": enrage,
            "phases": phases, "attack_speed": speed, "slows": _slows(every), "basic": timing,
            "basic_enraged": timing_enr, "spawn_basic": spawn_basic, "speed_parts": speed_parts}


def _raw(record) -> str:
    """File stem of a record - the identifier in 'name'/'source' (unit tests and joins rely on it)."""
    return str(record).rsplit("/", 1)[-1].removesuffix(".dbr")


def attack_label(attack: dict, label) -> str:
    """Display name of one modelled attack: '<monster>: <skill> (<variant>)' with `label(record)`;
    a monster's plain weapon swing is 'weapon attack'."""
    own = "weapon attack" if attack.get("monster") and attack["skill"] == attack["monster"] else label(attack["skill"])
    if attack.get("variant"):
        own = f"{own} ({attack['variant']})"
    return f"{label(attack['monster'])}: {own}" if attack.get("monster") else own


def _labelled(attacks: list, label) -> list:
    """Every attack gets its display 'label' (raw name without `label`)."""
    for a in attacks:
        a["label"] = attack_label(a, label) if label else a["name"]
        if a.get("monster"):
            a["monster_label"] = label(a["monster"]) if label else _raw(a["monster"])
    return attacks


def _pick_at_level(values, level):
    """Entry of a per-level list (records, not numbers)."""
    if isinstance(values, list):
        return values[min(max(level, 1), len(values)) - 1] if values else None
    return values


def slows_of(attacks) -> list:
    """Public: the slows of a list of attacks (combat_check: the slows without the spawned monsters)."""
    return _slows(attacks)


def _slows(attacks) -> list:
    """[(name, pct, kind 'attack'|'total', aura, duration s)] strongest first - from the database."""
    out = []
    for a in attacks:
        for kind, pct, dur in a.get("slows") or ():
            if a.get("rate", 0.0) > 0:
                out.append((a.get("label") or a["name"], pct, kind, bool(a.get("aura")), dur))
    return sorted(set(out), key=lambda x: (-x[1], x[0]))


DA_EQ_EXPECTED = "defensiveAbilityEquation"


def defense_profile(name: str, combat: dict, level: int, adj: dict, ascendant: bool = False, label=None) -> dict:
    """Armor, armor absorption, DA and OA of one boss at monster level `level` from the game
    database (KG-03): bio record equations + passive skills at their skill level + the
    difficulty table, DA/OA with the combatformulas.dbr equation. {} if the boss data lacks
    the bio equations (older boss-combat file) - the caller keeps its fallback then."""
    return defense_profile_of((combat.get("bosses") or {}).get(name), combat, level, adj, ascendant, label)


def defense_profile_of(boss: dict | None, combat: dict, level: int, adj: dict, ascendant: bool = False,
                       label=None) -> dict:
    """defense_profile of one entry with the bio equations and `skill_levels` of a boss - also a
    `benchmarks` entry (.72: Training Dummy, Mogara; data.benchmarks)."""
    from ..mechanics import monster as MON
    if not boss or not boss.get("attribute_equations"):
        return {}
    formulas = combat.get("formulas") or {}
    if formulas.get("defensiveAbilityEquation") not in (None, MON.DA_EQUATION) or \
            formulas.get("offensiveAbilityEquation") not in (None, MON.OA_EQUATION):
        return {}                                   # a patch changed the formula: do not guess
    bio = boss["attribute_equations"]
    skills = combat.get("skills") or {}
    armor = da_flat = oa_flat = 0.0
    da_mod, oa_mod = adj.get("da_mod", 0.0), adj.get("oa_mod", 0.0)
    abs_mod = adj.get("absorption_mod", 0.0)
    parts = []
    for rec, eq in sorted((boss.get("skill_levels") or {}).items()):
        f = skills.get(rec) or {}
        if "Passive" not in str(f.get("Class", "")):
            continue
        stem = rec.rsplit("/", 1)[-1].removesuffix(".dbr")
        if "ascendantmode" in stem and not ascendant:
            continue
        lv = MON.skill_level(eq, level)
        a = MON.at_level(f.get("defensiveProtection"), lv)
        if a:
            armor += a
            parts.append(f"armor {a:,.0f} ({label(rec) if label else stem}, skill level {lv})")
        da_flat += MON.at_level(f.get("characterDefensiveAbility"), lv)
        da_mod += MON.at_level(f.get("characterDefensiveAbilityModifier"), lv)
        oa_flat += MON.at_level(f.get("characterOffensiveAbility"), lv)
        oa_mod += MON.at_level(f.get("characterOffensiveAbilityModifier"), lv)
        abs_mod += MON.at_level(f.get("defensiveAbsorptionModifier"), lv)
    da_bio = MON.equation(bio.get("characterDefensiveAbility"), level)
    oa_bio = MON.equation(bio.get("characterOffensiveAbility"), level)
    ab = MON.abilities(level, da_bio + da_flat + adj.get("da", 0.0), da_mod,
                       MON.equation(bio.get("characterStrength"), level),
                       oa_bio + oa_flat + adj.get("oa", 0.0), oa_mod,
                       MON.equation(bio.get("characterDexterity"), level))
    base_abs = float(formulas.get("armorDefensiveAbsorption") or 70.0)
    absorption = max(0.0, base_abs * (1 + abs_mod / 100)) / 100
    parts.append(f"DA {ab['da']:,.0f} = ({da_bio:,.0f} bio + {da_flat + adj.get('da', 0.0):,.0f} flat + level "
                 f"{level} x 12 + Strength/2) x (1 {da_mod:+g}%) + 53")
    parts.append(f"OA {ab['oa']:,.0f} (bio {oa_bio:,.0f}, modifier {oa_mod:+g}%)")
    parts.append(f"armor absorption {absorption * 100:.0f}% = {base_abs:g}% x (1 {abs_mod:+g}%)")
    return {"armor": armor, "absorption": absorption, "da": ab["da"], "oa": ab["oa"], "level": level,
            "parts": parts}
