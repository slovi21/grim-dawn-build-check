"""Web entry point: the pure functions the online check calls (Pyodide in a worker) and the tests call.

    prepare(bundle, folder)  -> WebData     verify and unpack the data package, load its index (no cache)
    check(gdc, data, ...)    -> dict        {ok, html, title, message}: never raises for a bad file or an engine error
    check_path(path, data)   -> dict        the same for a file in the worker's file system (it is deleted after)
    analyse_gdc_bytes(...)   -> str         the report HTML, errors raised (what check wraps)
    selftest()               -> dict        engine, Python and the stdlib parts the engine needs

Layer rule (tests/architecture/test_layers.py): nothing at module level reaches the command line, the PC's
environment (local_env), the friendly error frame, the index build, the .arz reader or the game folder's fingerprint,
and no process, registry, socket or browser module - a browser has none of them. Errors are told apart on purpose:
only the parse of the file may say "not a readable Grim Dawn character save"; a failure in the analysis or the render
of a save that was read says "The check failed (<Type>)" with the issue link, because it is the engine's, not the
player's."""
from __future__ import annotations

import gzip
import hashlib
import html
import os
import platform
import re
import zlib
from dataclasses import dataclass
from pathlib import Path

from .. import VERSION
from ..data.index import MAX_INDEX_UNPACKED, SUPPORTED_SCHEMA, GameIndex, SchemaError
from ..data.savefile import GDFormatError
from ..domain.context import Options
from ..domain.enums import ContentProfile
from ..report import render_html
from . import patch_bundle as PB
from . import pipeline as PL
from . import product

MAX_SAVE_BYTES = 16 * 1024 * 1024
PROFILES = tuple(p.value for p in ContentProfile)

DAMAGED = "damaged"
UPDATING = "updating"
DAMAGED_TEXT = "The game data of the online check is damaged. Reload the page; if it stays, use the installer."
UPDATING_TEXT = "Game data is being updated - please use the installer meanwhile."

_PARSE_ERRORS = (GDFormatError, ValueError, EOFError, IndexError, KeyError)      # the same set the command line names
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)


class WebError(Exception):
    """The data package cannot be used. `state` is what the page shows: "damaged" or "updating"."""

    def __init__(self, message: str, state: str):
        super().__init__(message)
        self.message = message
        self.state = state


@dataclass(frozen=True)
class WebData:
    index: GameIndex
    game_version: str
    built: str
    folder: Path


def prepare(bundle: Path, folder: Path) -> WebData:
    """Verify the data package (a patch bundle), unpack it into `folder`, load its index (no cache), then make the engine
    read its reference files from `folder` (the two environment variables are set last: a failed prepare leaves them as
    they were). Raises WebError: "updating" if the package is whole but of another index schema (E5), "damaged" for
    everything else that goes wrong - never another exception. Only the web variant (format 2, web: true) is a data
    package of the online check, and its index may not unpack to more than MAX_INDEX_UNPACKED bytes. `folder` should be
    empty or the package's own: the index is the file the manifest names, not whatever else lies there."""
    bundle, folder = Path(bundle), Path(folder)
    try:
        problems, man = PB.check(bundle)
    except Exception as ex:                                  # noqa: BLE001 - a broken zip fails in many ways (zlib.error ...)
        raise WebError(DAMAGED_TEXT, DAMAGED) from ex
    if man.get("format") != PB.WEB_FORMAT or man.get("web") is not True:      # a whole normal bundle is still no web package
        raise WebError(DAMAGED_TEXT, DAMAGED)
    if problems:
        if PB.schema_only(problems, man):                    # the checksums matched: this data is whole, only of another schema
            raise WebError(UPDATING_TEXT, UPDATING)
        raise WebError(DAMAGED_TEXT, DAMAGED)
    try:
        PB.extract(bundle, folder)
    except Exception as ex:                                  # noqa: BLE001 - the folder cannot be written, or the zip fails late
        raise WebError(DAMAGED_TEXT, DAMAGED) from ex
    try:
        index, _none = GameIndex.load(folder / f"gd-index-{man.get('game_version')}.json.gz", make_cache=False,
                                      max_bytes=MAX_INDEX_UNPACKED)          # a gzip bomb stops here, not in the browser's memory
    except SchemaError as ex:                                # a newer schema passes verify, the load refuses it
        raise WebError(UPDATING_TEXT, UPDATING) from ex
    except Exception as ex:                                  # noqa: BLE001 - not found, unreadable, not an index (RecursionError ...)
        raise WebError(DAMAGED_TEXT, DAMAGED) from ex
    os.environ["GDCHECK_DATA_DIR"] = str(folder)
    os.environ["GDCHECK_PC_DATA"] = "0"                      # all reference files come from the package
    return WebData(index, index.game_version, str(index.meta.get("erstellt") or "?"), folder)


def _html_of(ch, index, options: Options) -> str:
    return render_html.render(PL.report_for(PL.analyse_character(ch, index, options), index), "")


def analyse_gdc_bytes(gdc: bytes, index: GameIndex, options: Options | None = None) -> str:
    """The HTML report of a player.gdc, as the command line writes it (without the link to the game mechanics page,
    which the online check does not host). Raises what the parse or the engine raises."""
    return _html_of(PL.character_from_bytes(gdc, index), index, options or Options())


def _fail(message: str) -> dict:
    return {"ok": False, "html": "", "title": "", "message": message}


def _check_failed(ex: Exception) -> str:
    return f"The check failed ({type(ex).__name__}). Please open an issue at {product.ISSUES_URL}."


def _size_problem(size: int) -> str:
    if size == 0:
        return "This file is empty."
    if size > MAX_SAVE_BYTES:
        return "This file is too large to be a player.gdc."
    return ""


def check(gdc: bytes, data: WebData, profile: str = "general", detail: bool = False) -> dict:
    """Check a player.gdc: {"ok", "html", "title", "message"}. A wrong profile is a programming error (ValueError);
    everything the player can cause is a message. `title` is plain text with the player's content in it (the character's
    name): the page must set it with textContent, never through innerHTML."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r} (one of {', '.join(PROFILES)})")
    problem = _size_problem(len(gdc))                         # before anything is parsed
    if problem:
        return _fail(problem)
    try:
        ch = PL.character_from_bytes(bytes(gdc), data.index)
    except _PARSE_ERRORS:
        return _fail("This file is not a readable Grim Dawn character save (player.gdc).")
    except Exception as ex:                                   # noqa: BLE001 - not a parse error: the engine's, not the player's
        return _fail(_check_failed(ex))
    try:
        page = _html_of(ch, data.index, Options(profile=ContentProfile(profile), detail=detail))
    except Exception as ex:                                   # noqa: BLE001 - the page must get a message, not a traceback
        return _fail(_check_failed(ex))
    m = _TITLE.search(page)
    return {"ok": True, "html": page, "title": html.unescape(m.group(1)) if m else "", "message": ""}


def check_path(path: Path, data: WebData, profile: str = "general", detail: bool = False) -> dict:
    """check() of a file: read it, check it, delete it (the save stays in the worker only as long as it is checked)."""
    path = Path(path)
    try:
        problem = _size_problem(path.stat().st_size)          # a huge file is not read at all
        if problem:
            return _fail(problem)
        return check(path.read_bytes(), data, profile, detail)
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def selftest() -> dict:
    """What a page with no game data can still show: the engine and the Python it runs on, and whether the standard
    library parts the engine needs (hashes without OpenSSL, gzip, zlib) work in this runtime."""
    parts = {"sha1": hashlib.sha1(b"").hexdigest() == "da39a3ee5e6b4b0d3255bfef95601890afd80709",
             "sha256": hashlib.sha256(b"").hexdigest()
             == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
             "gzip": gzip.decompress(gzip.compress(b"gd")) == b"gd",
             "zlib": zlib.decompress(zlib.compress(b"gd")) == b"gd"}
    return {"ok": all(parts.values()), "engine": VERSION, "python": platform.python_version(),
            "schema": SUPPORTED_SCHEMA, "parts": parts}
