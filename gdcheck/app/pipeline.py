"""Wire everything together (the only layer that reads arguments and writes files)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .. import RULESET_VERSION, VERSION
from ..analysis import run as RUN
from ..analysis import targets as TGT
from ..data import campaign as CAMP
from ..data import index_cache
from ..data import savefile as SF
from ..data.index import SUPPORTED_SCHEMA, GameIndex
from ..domain import character, context
from ..report import build as RB

ROOT = Path(__file__).resolve().parents[2]
_INDEX_CACHE: dict = {}


def default_index(fingerprint: str | None = None) -> Path | None:
    """GDCHECK_INDEX, else the index of exactly this game data (fingerprint) in assets/ or the PC's
    ClaudeSkill-Data/out (and out/previous), else the newest found there (out/ before assets/). An index of
    the schema the scripts load comes before one of an old schema (.71: after an update the PC's out/ still
    holds schema 8 until GD-Tools rebuilds it, while out/previous/ may hold a current one)."""
    forced = os.environ.get("GDCHECK_INDEX")
    if forced and Path(forced).is_file():
        return Path(forced)
    from .local_env import index_meta
    data = ROOT.parent / "ClaudeSkill-Data" / "out"
    found = sorted((ROOT / "assets").glob("gd-index-*.json.gz"))          # least preferred first
    for extra in (data / "previous", data):
        found += sorted(extra.glob("gd-index-*.json.gz")) if extra.is_dir() else []
    current = [p for p in found if int(index_meta(p).get("schema") or 1) == SUPPORTED_SCHEMA]
    pool = current or found
    if fingerprint:
        for p in reversed(pool):
            if index_meta(p).get("fingerprint") == fingerprint:
                return p
    return pool[-1] if pool else None


def cache_dir() -> Path:
    d = os.environ.get("GDCHECK_CACHE")
    return Path(d) if d else Path(tempfile.gettempdir())


def load_index(path: Path) -> GameIndex:
    """Index with the marshal cache (written here - app is the only writer)."""
    path = Path(path)
    if str(path) in _INDEX_CACHE:
        return _INDEX_CACHE[str(path)]
    cf = cache_dir() / index_cache.file_name(path)
    blob = None
    try:
        blob = cf.read_bytes() if cf.exists() else None
    except OSError:
        blob = None
    idx, new = GameIndex.load(path, blob)
    if new is not None:
        try:
            cf.parent.mkdir(parents=True, exist_ok=True)
            tmp = cf.with_suffix(".tmp")
            tmp.write_bytes(new)
            tmp.replace(cf)
        except OSError:
            pass                      # read-only location: works, only slower next time
    _INDEX_CACHE[str(path)] = idx
    return idx


def versions(index) -> dict:
    meta = getattr(index, "meta", {}) or {}
    return {"skill": VERSION, "ruleset": RULESET_VERSION, "targets": TGT.load().get("version", "?"),
            "index": f"index {meta.get('game_version', '?')} of {meta.get('erstellt', '?')}",
            "boss_data": _boss_data_version(index), "reference_notes": reference_notes(index)}


def reference_notes(index) -> tuple:
    """Why a version-bound reference file may not fit this game data (ASSUMED notes, .53)."""
    from ..data import animation as AD
    from ..data import versioned as V
    ver, fp = V.index_identity(index) if index is not None else ("", "")
    notes = []
    for prefix in ("anim-tables", "boss-combat", "player-skills", "vendor-ranks", "design-data"):   # vendor ranks: gear.py, design data: design lint (.67)
        r = V.resolve(prefix, ver or "1.3.0.8", fp)
        if r.note:
            notes.append(r.note)
    ft = AD.frames_table(ver or "1.3.0.8")
    if ft.get("assets_note"):
        notes.append(str(ft["assets_note"]))
    return tuple(notes)


def _boss_data_version(index=None) -> str:
    """The boss-combat file the check really uses for this game data (.53: not simply the newest)."""
    from ..data import versioned as V
    ver, fp = V.index_identity(index) if index is not None else ("", "")
    r = V.resolve("boss-combat", ver or "1.3.0.8", fp)
    if r.path is None:
        return "none"
    d = r.data
    if not d:
        return "unreadable"
    return (f"{r.path.name} (game {d.get('game_version', '?')}, {len(d.get('bosses') or {})} bosses)"
            + (f" - {r.note}" if r.note else ""))


def character_from_bytes(gdc: bytes, index: GameIndex) -> character.Character:
    """The character of the bytes of a player.gdc (the web entry: nothing is read from disk, source_path stays empty).
    Raises savefile.GDFormatError (or ValueError/EOFError/IndexError/KeyError) for bytes that are no save."""
    return character.from_doc(SF.parse_character(gdc), index.display, index.tag)


def analyse_character(ch: character.Character, index: GameIndex, options: context.Options):
    ctx = context.make_context(index, ch, options)
    return RUN.run(ctx, detail=options.detail)


def analyse_file(gdc: Path, index: GameIndex, options: context.Options):
    return analyse_character(character.load(gdc, index.display, index.tag), index, options)


def report_for(analysis, index, mechanics_note: str | None = None):
    camp = CAMP.read(analysis.ctx.character.source_path) if analysis.ctx.character.source_path else {}
    meta = {"campaign": CAMP.short_form(camp.get("Ultimate")) if camp.get("Ultimate") else "",
            "campaign_long": CAMP.long_form(camp.get("Ultimate")) if camp.get("Ultimate") else "",
            "mechanics_note": mechanics_note}
    return RB.build(analysis, versions(index), meta)
