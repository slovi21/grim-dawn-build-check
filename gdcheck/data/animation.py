"""Player animation tables and animation frames (AP-F2/F3, plan 01./02.10.2026, .52).

references/anim-tables-<version>.json (tools/anim_extract.py, from the .arz files): per animation
table (male, female, every shapeshift form) and weapon mode prefix the basic attack list, the basic
spell list and the special animations by name - each entry (.anm path, speed multiplier, weight).

references/anim-frames-<version>.json: HEADER frames (keyframe count N of the .anm file), in this
order of precedence
    assets     .anm header per file, read from the game files (gdcheck.app.anim_frames)  EXACT
               (1.3.0.8: shipped with the skill, 773/773 table animations, read 02.10.2026)
    community  FoA mechanics test 2026 - published as header / slot multiplier, so the
               header-equivalent is frames x multiplier ('after_slot_multiplier')       TESTED
    defaults   19 keyframes basic attack and everything else                          ESTIMATE
How many frames the engine PLAYS (N-1 per Crate, ruleset 'anim_playback') is decided in
combat.frames, not here. A value never comes without a source; a default is reported with
frames = None (FR-08).

Version guard (critic review .52): assets of another game version are used as ASSUMED with a note;
the PC tool's file of version V replaces the assets when the shipped file is not V and merges when it
is; for the shipped version the shipped values win unless the PC file comes from another build
(different Creatures.arc sizes) - a stale PC file cannot hide a later reader fix.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PC_OUT = ROOT.parent / "ClaudeSkill-Data" / "out"       # GD-Tools output on the PC (= versioned.PC_OUT)
BASIC_ATTACK, BASIC_SPELL = "@attack", "@spell"

# Weapon classes of the game database -> weapon mode prefix of the animation tables.
TWO_HANDED = {"WeaponMelee_Sword2h": "sword2h", "WeaponMelee_Axe2h": "axe2h", "WeaponMelee_Mace2h": "mace2h",
              "WeaponMelee_Spear2h": "spear2h", "WeaponHunting_Ranged2h": "ranged2h"}
MELEE_1H = {"WeaponMelee_Sword", "WeaponMelee_Axe", "WeaponMelee_Mace", "WeaponMelee_Dagger",
            "WeaponMelee_Scepter"}
RANGED_1H = {"WeaponHunting_Ranged1h"}
WEAPONS = set(TWO_HANDED) | MELEE_1H | RANGED_1H


@dataclass(frozen=True)
class Entry:
    anm: str
    skill_multiplier: float
    weight: float


def _version_key(p: Path):
    parts = p.stem.rsplit("-", 1)[-1].split(".")
    return tuple(int(x) if x.isdigit() else 0 for x in parts)


def _load(prefix: str, version: str, folder: Path | None = None) -> tuple[dict, str]:
    base = Path(folder) if folder else ROOT / "references"
    exact = base / f"{prefix}-{version}.json"
    cands = [exact] + sorted(base.glob(f"{prefix}-*.json"), key=_version_key, reverse=True)
    for cand in cands:
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        return data, str(data.get("game_version") or cand.stem.rsplit("-", 1)[-1])
    return {}, ""


def tables(version: str = "1.3.0.8", folder: str | None = None, fingerprint: str | None = None) -> dict:
    """The animation tables that fit the game data (data.versioned: fingerprint > label > newest
    older version); {} if no file is there. '_version' = version of the file used, '_note' = why it
    may not fit (ASSUMED). `folder` replaces references/ and switches the extra folders off (tests)."""
    from . import versioned as V
    r = V.resolve("anim-tables", version, fingerprint, Path(folder) if folder else None,
                  [] if folder else None)
    data = r.data
    if not data:
        return {}
    data = dict(data)
    data["_version"], data["_note"], data["_file"] = r.label, r.note, r.path.name if r.path else ""
    return data


tables.cache_clear = lambda: None           # type: ignore[attr-defined]  # no cache any more (.53)


def frames_table(version: str = "1.3.0.8", folder: str | None = None, pc_out: str | None = None) -> dict:
    """Frame table of the skill (references/) plus the asset frames the PC tool read from the installed
    game (ClaudeSkill-Data/out/anim-frames-<version>.json, or a patch bundle in GDCHECK_DATA_DIR) - see
    the version guard in the docstring. The environment is read here, outside the cache (.53)."""
    if pc_out is None and folder is None:
        from . import versioned as V
        for extra in V.extra_folders():
            if (Path(extra) / f"anim-frames-{version}.json").is_file():
                pc_out = str(extra)
                break
    return _frames_table(version, folder, pc_out)


@lru_cache(maxsize=8)
def _frames_table(version: str, folder: str | None, pc_out: str | None) -> dict:
    data, ver = _load("anim-frames", version, Path(folder) if folder else None)
    data = dict(data) if data else {}
    if data:
        data["_version"] = ver
        if data.get("assets"):
            data["assets_version"] = str(data.get("assets_version") or ver)
            data["assets_origin"] = "skill"
    # GDCHECK_PC_FRAMES=0 / GDCHECK_PC_DATA=0 (set by the tests): never the PC's own frames - the goldens
    # stay the same on every machine (review 02.10.2026); an explicit pc_out is always used
    pc = Path(pc_out) if pc_out else None
    if pc is not None:
        try:
            extra = json.loads((pc / f"anim-frames-{version}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            extra = {}
        if extra.get("assets"):
            _merge_pc(data, extra, version)
    av = str(data.get("assets_version") or "")
    if data.get("assets") and av != str(version):
        data["assets_confidence"] = "ASSUMED"
        data["assets_note"] = (f"animation frames of game version {av} used for {version} - not read from "
                               "this version (ASSUMED)")
    return data


def other_build(a: dict | None, b: dict | None) -> bool:
    """Both carry a Creatures.arc size signature and the two differ (same version label, other build)."""
    if not a or not b:
        return False
    keys = set(a) & set(b)
    return bool(keys) and any(int(a[k]) != int(b[k]) for k in keys)


def _merge_pc(data: dict, extra: dict, version: str) -> None:
    pv = str(extra.get("assets_version") or extra.get("game_version") or version)
    shipped_same = bool(data.get("assets")) and str(data.get("assets_version")) == pv
    if shipped_same and not other_build(data.get("archives"), extra.get("archives")):
        data["assets_pc_ignored"] = (f"PC file {pv} ignored: the skill ships the frames of this version "
                                     "(same build)")
        return
    base = (data.get("assets") or {}) if shipped_same else {}
    data["assets"] = {**base, **extra["assets"]}
    data["assets_source"] = extra.get("source", "")
    data["assets_confidence"] = extra.get("assets_confidence", "EXACT")
    data["assets_version"] = pv
    data["assets_origin"] = "pc"
    if extra.get("archives"):
        data["archives"] = extra["archives"]
    data.setdefault("_version", version)


def weapon_mode(main_class: str | None, off_class: str | None) -> str:
    """Prefix of the animation table for the weapons in hand (active weapon set)."""
    m, o = main_class or "", off_class or ""
    if m in TWO_HANDED:
        return TWO_HANDED[m]
    if m in RANGED_1H and o in RANGED_1H:
        return "dualRanged"
    if m in MELEE_1H and o in MELEE_1H:
        return "dHanded"
    if m in RANGED_1H:
        return "ranged1h"
    if m in MELEE_1H:
        return "sHanded"
    return "unarmed"


def player_multiplier(tab: dict, male: bool, speed_type: str) -> float:
    rec = (tab.get("player") or {}).get("male" if male else "female") or {}
    key = "characterSpellCastSpeed" if speed_type == "cast" else "characterAttackSpeed"
    return float(rec.get(key) or 1.0)


def animation_name(tab: dict, record: str, overrides=()) -> str | None:
    """skillSpecialAnimationName of the skill; the last modifier/transmuter in `overrides` that
    has its own name replaces it (the modifier changes the animation, e.g. Shadow Strike runes)."""
    names = tab.get("skill_animation") or {}
    name = names.get(record)
    for o in overrides:
        name = names.get(o) or name
    return name


def entries(tab: dict, table_key: str, prefix: str, name: str | None, spell: bool) -> tuple[tuple, str, bool]:
    """(entries, animation key, found) of an action. A special animation the table does not
    have falls back to the basic list (found = False, ASSUMED)."""
    t = ((tab.get("tables") or {}).get(table_key) or {}).get("prefixes") or {}
    p = t.get(prefix) or {}
    if name:
        sp = (p.get("special") or {}).get(name)
        if sp:
            return tuple(Entry(a, float(m), float(w)) for a, m, w in sp), name, True
    if spell:
        lst = list(p.get("spell") or []) + list((t.get(prefix + "Spell") or {}).get("attack") or [])
        key = BASIC_SPELL
    else:
        lst = list(p.get("attack") or [])
        key = BASIC_ATTACK
    return tuple(Entry(a, float(m), float(w)) for a, m, w in lst), (name or key), not name


@dataclass(frozen=True)
class Frames:
    frames: float
    source: str          # assets | community | default
    confidence: str      # EXACT | TESTED | ESTIMATE
    note: str = ""


def base_frames(ftab: dict, table_key: str, animation: str, entry: Entry, prefix: str = "") -> Frames:
    """HEADER frames (keyframes) of one pool entry: asset header > community measurement > default."""
    assets = ftab.get("assets") or {}
    a = assets.get(entry.anm) or assets.get(entry.anm.lower())
    if a:
        # ASSUMED: header check failed on the PC, or frames of another game version
        return Frames(float(a), "assets", str(ftab.get("assets_confidence") or "EXACT"), ".anm header")
    for c in ftab.get("community") or ():
        tables_ok = c.get("table") in (table_key, "*") or (c.get("table") == "player" and table_key in ("male", "female"))
        if tables_ok and c.get("animation") == animation and (not c.get("prefix") or c.get("prefix") == prefix):
            f, note = float(c["frames"]), c.get("source", "")
            if c.get("after_slot_multiplier"):
                # published after the slot multiplier: 19 at 1.5 = 28.5 header-equivalent (bug fix .52:
                # the timing divided by the multiplier a second time)
                f *= max(entry.skill_multiplier, 1e-9)
                note = f"community {c['frames']:g} x {entry.skill_multiplier:.2f}; {note}"
            return Frames(f, "community", c.get("confidence", "TESTED"), note)
    d = ftab.get("defaults") or {}
    if animation == BASIC_ATTACK:
        return Frames(float(d.get("basic_attack", 19)), "default", "ESTIMATE", "default basic attack frames")
    return Frames(float(d.get("other", 19)), "default", "ESTIMATE", "default frames")


frames_table.cache_clear = _frames_table.cache_clear     # type: ignore[attr-defined]
