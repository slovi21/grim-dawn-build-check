#!/usr/bin/env python3
"""Build the site of the online check: the page, the engine zip, the pinned self-hosted Pyodide and (maybe) the data.

    python web/build_site.py --out <empty folder> [--site public/site] [--data-bundle <folder>]
                             [--pyodide-cache <folder>] [--detail on|off] [--pin web/pyodide.json]

--site is the page as it lies in the repository (it must hold check.html with the placeholder `<!-- robots -->`); it is
copied to --out, and then the builder adds:

    app/gdcheck-<VERSION>-<sha12>.zip   the engine (gdcheck/ without bytecode), deterministic, the content hash in the name
    pyodide/<version>/<files>           the files of web/pyodide.json, from the pinned release archive, and
                                        THIRD_PARTY_NOTICES.txt (web/: MPL-2.0 text, source link; required)
    data/<zip>, data/current.json       the data package - only in the state "open"
    check/config.json                   what the page reads: engine, pyodide, notices, data, state, detail (relative paths)
    check.html                          `<!-- robots -->` becomes the noindex tag, or disappears (state "open")

--data-bundle is the folder `python tools/web_data.py build` wrote (current.json and the zip it names). Without it the
state is "closed". The states (spec 4.7, 4.10): closed (no pointer), updating (whole data of another index schema: E5,
no data in the artifact), open. A package whose SHA256 or size differs from its pointer, that is damaged, or whose index
the browser could not load (it unpacks to more than MAX_INDEX_UNPACKED bytes, is no index) is not a state: the build
stops (exit code 1) and so does the deploy. A symbolic link anywhere in --site stops the build as well.

Pyodide comes from web/pyodide.json (version, GitHub release URL, SHA256): downloaded into a cache folder of its own,
checked BEFORE it is unpacked, and only the pinned files are copied (no tar extraction: nothing can leave the folder).
Of the core archive the browser needs exactly what package.json lists as the package files: pyodide.mjs (the loader),
pyodide.asm.mjs and pyodide.asm.wasm (the runtime), python_stdlib.zip (the standard library) and pyodide-lock.json (read
by the loader). Not copied: pyodide.js (the classic-script loader; the worker is a module), the .d.ts files, python,
python.bat, python.exe and python_cli_entry.mjs (the command-line wrapper for Node); package.json is copied as well, but the
license notice is THIRD_PARTY_NOTICES.txt. A stop (exit code 1) removes what the builder wrote to --out. Exit codes: 0 built, 1 the deploy must stop, 2 wrong call.

The deploy workflow of the public repository (.github/workflows/pages.yml) runs this file, so tools/export_public.py
exports it with web/pyodide.json, THIRD_PARTY_NOTICES.txt and smoke.mjs. Only the standard library is used."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gdcheck.app import patch_bundle as PB  # noqa: E402
from gdcheck.data.index import MAX_INDEX_UNPACKED, SUPPORTED_SCHEMA, GameIndex  # noqa: E402

CLOSED, UPDATING, OPEN = "closed", "updating", "open"
STATES = (CLOSED, UPDATING, OPEN)
PLACEHOLDER = b"<!-- robots -->"
NOINDEX = b'<meta name="robots" content="noindex">'
MAX_DOWNLOAD = 64 * 1024 * 1024          # the real archive is 6.7 MB
MAX_MEMBER = 64 * 1024 * 1024            # the largest member (the wasm) is 9.6 MB
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
ENGINE_SUFFIXES = (".py", ".json", ".txt", ".woff2")        # nothing else belongs in gdcheck/: any other file stops the build
TEXT_SUFFIXES = (".py", ".json", ".txt")        # line endings are normalized to LF: a Windows checkout gives the same zip
PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
DATA_FILE = re.compile(r"gd-data-[A-Za-z0-9._+-]+\.zip")
SHA256 = re.compile(r"[0-9a-f]{64}")
ARCHIVE_DIR = "pyodide/"                  # the core archive's one top-level folder
NOTICES = HERE / "THIRD_PARTY_NOTICES.txt"      # the license notice shipped next to the Pyodide files (MPL-2.0)
NOTICES_NAME = "THIRD_PARTY_NOTICES.txt"
INDEX_OBJECTS = ("records", "tags", "tabellen", "controller")      # the sections of the index that must be JSON objects
POINTER_FORMAT = 1
POINTER_KEYS = ("format", "game_version", "file", "sha256", "size", "index_schema", "built", "release")   # canonical order


# ---------------------------------------------------------------- engine zip
def engine_version(repo: Path) -> str:
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', (Path(repo) / "gdcheck" / "__init__.py").read_text(encoding="utf-8"), re.M)
    if not m or not PLAIN.fullmatch(m.group(1)):
        raise ValueError("gdcheck/__init__.py has no usable VERSION")
    return m.group(1)


def _engine_files(repo: Path) -> list[tuple[str, bytes]]:
    items = []
    for p in (Path(repo) / "gdcheck").rglob("*"):
        if not p.is_file() or "__pycache__" in p.parts or p.suffix in (".pyc", ".pyo"):
            continue
        if p.suffix not in ENGINE_SUFFIXES:
            raise ValueError(f"{p.relative_to(repo).as_posix()}: a file the engine zip does not know "
                             f"(only {', '.join(ENGINE_SUFFIXES)}) - a dirty working copy?")
        blob = p.read_bytes()
        if p.suffix in TEXT_SUFFIXES:
            blob = blob.replace(b"\r\n", b"\n")
        items.append((p.relative_to(repo).as_posix(), blob))
    return sorted(items)


def engine_zip(repo: Path, out: Path) -> Path:
    """`out`/app/gdcheck-<VERSION>-<sha12>.zip: every file of gdcheck/ (packages, targets.json, ruleset.json, fonts) except
    bytecode; a file with another ending than .py, .json, .txt, .woff2 stops the build (ValueError). Deterministic: sorted entries, a fixed time stamp, fixed attributes, text files with LF. sha12 is the first
    12 hex digits of a hash over the names and contents (not over the compressed bytes: the name does not depend on the
    zlib that built it)."""
    files = _engine_files(repo)
    if not files:
        raise ValueError("no engine files found")
    h = hashlib.sha256()
    for name, blob in files:
        h.update(f"{name}\0{len(blob)}\0".encode("utf-8"))
        h.update(blob)
    target = Path(out) / "app" / f"gdcheck-{engine_version(repo)}-{h.hexdigest()[:12]}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name, blob in files:
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.create_system = 3
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, blob, compresslevel=9)
    tmp.replace(target)
    return target


# ---------------------------------------------------------------- Pyodide
def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _plain(name, what: str) -> str:
    if not isinstance(name, str) or not PLAIN.fullmatch(name):
        raise ValueError(f"{what} {name!r} is not a plain file name")
    return name


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is followed only to an https URL (urllib would also follow http and ftp)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise ValueError(f"{req.full_url}: redirected to {newurl!r} - only https redirects are followed")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_HttpsOnlyRedirect)


def _download(url: str, dest: Path) -> None:
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "grim-dawn-build-check-site-builder"})
    total = 0
    try:
        with _OPENER.open(req, timeout=120) as resp, part.open("wb") as fh:
            for chunk in iter(lambda: resp.read(1 << 20), b""):
                total += len(chunk)
                if total > MAX_DOWNLOAD:
                    raise ValueError(f"{url}: larger than {MAX_DOWNLOAD} bytes - not the pinned archive")
                fh.write(chunk)
        part.replace(dest)
    except BaseException:
        part.unlink(missing_ok=True)                      # no half-written file stays in the cache
        raise


def fetch_pyodide(pin: dict, cache: Path, site: Path, allow_file: bool = False) -> Path:
    """The pinned Pyodide files in `site`/pyodide/<version>/ (returned; a folder of that name that the page already had is
    replaced). The archive lives in a cache folder of its own (<cache>/<version>-<sha12>/); a cached file that does not
    match the pin is replaced, a download that does not match is deleted and refused (ValueError) - the SHA256 is checked
    before the archive is opened. The pin must be consistent: the archive is named pyodide-core-<version>.tar.bz2 and has
    the pinned size. Only the files the pin lists, and its license carrier, are read out of the archive; they are written by
    name, so no member name can reach a path. Only https URLs (and https redirects); `allow_file` (set by tests only, main's
    command line has no way to set it) also takes file:// URLs."""
    version = _plain(pin.get("version"), "pin version")
    url, sha = str(pin.get("url") or ""), str(pin.get("sha256") or "").lower()
    if urllib.parse.urlsplit(url).scheme not in (("https", "file") if allow_file else ("https",)):
        raise ValueError(f"pin url {url!r}: only https is allowed")
    if not SHA256.fullmatch(sha):
        raise ValueError("pin sha256 is not 64 hex digits")
    size = pin.get("size")
    if type(size) is not int or size <= 0:
        raise ValueError("pin size is not a positive integer")
    files = [_plain(n, "pinned file") for n in pin.get("files") or ()]
    carrier = _plain((pin.get("license") or {}).get("file"), "license file")
    if not files:
        raise ValueError("pin lists no files")
    archive_name = _plain(urllib.parse.unquote(urllib.parse.urlsplit(url).path).rsplit("/", 1)[-1], "archive name")
    if archive_name != f"pyodide-core-{version}.tar.bz2":
        raise ValueError(f"pin: the url names {archive_name}, not pyodide-core-{version}.tar.bz2")
    folder = Path(cache) / f"{version}-{sha[:12]}"
    archive = folder / archive_name
    folder.mkdir(parents=True, exist_ok=True)
    if archive.is_file() and (archive.stat().st_size != size or _sha256_of(archive) != sha):
        archive.unlink()                                  # a damaged or foreign cache entry is never used
    if not archive.is_file():
        print(f"downloading {url}")
        _download(url, archive)
        got, n = _sha256_of(archive), archive.stat().st_size
        if got != sha:
            archive.unlink()
            raise ValueError(f"SHA256 mismatch for {archive_name}: pinned {sha}, downloaded {got}")
        if n != size:
            archive.unlink()
            raise ValueError(f"size mismatch for {archive_name}: pinned {size}, downloaded {n}")
    payload: dict[str, bytes] = {}
    with tarfile.open(archive, "r:bz2") as t:
        members = {m.name: m for m in t.getmembers()}
        for name in dict.fromkeys([*files, carrier]):
            m = members.get(ARCHIVE_DIR + name)
            if m is None or not m.isreg() or m.size > MAX_MEMBER:
                raise ValueError(f"{archive_name}: {name} is missing, not a plain file or too large")
            src = t.extractfile(m)
            if src is None:
                raise ValueError(f"{archive_name}: {name} cannot be read")
            payload[name] = src.read()
    target = Path(site) / "pyodide" / version
    if target.exists():
        shutil.rmtree(target)                             # nothing of an earlier or foreign state stays in this folder
    target.mkdir(parents=True)
    for name, blob in payload.items():
        (target / name).write_bytes(blob)
    return target


def _notices() -> bytes:
    path = Path(NOTICES)
    if not path.is_file():
        raise ValueError(f"the third-party notices file {path} is missing - Pyodide must not be served without it")
    return path.read_bytes()


def add_notices(pyodide_dir: Path) -> Path:
    """web/THIRD_PARTY_NOTICES.txt (name, version, MPL-2.0 text, source link) next to the Pyodide files; required."""
    target = Path(pyodide_dir) / NOTICES_NAME
    target.write_bytes(_notices())
    return target


# ---------------------------------------------------------------- data package
def _pointer(path: Path) -> dict:
    try:
        ptr = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        raise ValueError(f"data pointer {path} is not readable: {ex}") from ex
    if not isinstance(ptr, dict):
        raise ValueError("data pointer is not an object")
    if not isinstance(ptr.get("file"), str) or not DATA_FILE.fullmatch(ptr["file"]):
        raise ValueError(f"data pointer: file {ptr.get('file')!r} is not a gd-data-*.zip name")
    if not SHA256.fullmatch(str(ptr.get("sha256") or "")):
        raise ValueError("data pointer: sha256 is not 64 lower-case hex digits")
    if type(ptr.get("size")) is not int or type(ptr.get("index_schema")) is not int:
        raise ValueError("data pointer: size and index_schema must be integers")
    unknown = sorted(set(ptr) - set(POINTER_KEYS))
    if unknown:
        raise ValueError(f"data pointer: unknown keys {unknown} (the pointer has no fingerprint and nothing else)")
    if type(ptr.get("format")) is not int or ptr["format"] != POINTER_FORMAT:
        raise ValueError(f"data pointer: format {ptr.get('format')!r}, expected {POINTER_FORMAT}")
    if "release" not in ptr:                              # pages.yml downloads the package from that release: it is required
        raise ValueError("data pointer: release is missing (the deploy downloads the package from that GitHub release)")
    strings = [ptr.get("game_version"), ptr["release"], *(ptr[k] for k in ("built",) if k in ptr)]
    if not all(isinstance(v, str) for v in strings):
        raise ValueError("data pointer: game_version, release and built must be strings")
    return {k: ptr[k] for k in POINTER_KEYS if k in ptr}


def _load_index(blob: bytes, man: dict) -> None:
    """The index of the package, loaded once the way the browser loads it (web.prepare): at most MAX_INDEX_UNPACKED bytes
    unpacked, then parsed completely. Stops (ValueError) for what the browser could not use: a gzip that expands beyond the
    limit (the checksums say nothing about that), JSON that is no index, a section of the wrong type, nesting too deep,
    another schema than the manifest says, no index of the game version's name."""
    name = f"gd-index-{man.get('game_version')}.json.gz"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                path.write_bytes(z.read(name))
            idx, _none = GameIndex.load(path, make_cache=False, max_bytes=MAX_INDEX_UNPACKED)
            wrong = [n for n in INDEX_OBJECTS if not isinstance(getattr(idx, n), dict)]
        except Exception as ex:                           # noqa: BLE001 - schema, nesting, wrong types: many ways to fail
            raise ValueError(f"data package: the index cannot be loaded ({type(ex).__name__}: {ex})") from ex
    if wrong:
        raise ValueError(f"data package: {', '.join(wrong)} of the index must be JSON objects")


def place_data(site: Path, pointer: Path | None, bundle: Path | None) -> str:
    """Put the data package into `site`/data/ and name the state of the page.
    "closed": no pointer (the data folder of the copied page is removed too). Stops with ValueError when the bundle's
    size or SHA256 differs from the pointer's, or when the package is damaged or no web variant. "updating": whole data of
    another index schema (the package's own schema line, patch_bundle.schema_only, or a manifest/pointer schema that is
    not SUPPORTED_SCHEMA; the pointer's schema must equal the package's, else ValueError): fail-closed, nothing of it goes into
    the artifact (E5). Otherwise the index is loaded once, as the browser loads it (_load_index: at most MAX_INDEX_UNPACKED
    bytes unpacked, parsed completely; ValueError if that fails) and the state is "open"; data/current.json is written from
    the checked pointer fields only."""
    site = Path(site)
    data_dir = site / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    if pointer is None:
        return CLOSED
    if bundle is None:
        raise ValueError("a data pointer without its package")
    ptr = _pointer(pointer)                               # only the checked fields, in a fixed order
    blob = Path(bundle).read_bytes()
    if len(blob) != ptr["size"]:
        raise ValueError(f"data package: {len(blob)} bytes, the pointer says {ptr['size']}")
    if hashlib.sha256(blob).hexdigest() != ptr["sha256"]:
        raise ValueError("data package: SHA256 differs from the pointer's")
    try:
        problems, man = PB.check(io.BytesIO(blob))        # the bytes that were hashed, not the file read a second time
    except Exception as ex:                               # noqa: BLE001 - a broken zip fails in many ways
        raise ValueError(f"data package unreadable: {ex}") from ex
    if man and (man.get("format") != PB.WEB_FORMAT or man.get("web") is not True):
        raise ValueError("data package is not the web variant (format 2, web: true) - it must never go online")
    if man and ptr["index_schema"] != man.get("index_schema"):
        raise ValueError(f"data pointer says index schema {ptr['index_schema']}, the package {man.get('index_schema')}")
    if problems:
        if PB.schema_only(problems, man):
            return UPDATING
        raise ValueError(f"data package damaged: {problems[0]}")
    if ptr["game_version"] != str(man.get("game_version")):
        raise ValueError("data pointer and package name different game versions")
    if man.get("index_schema") != SUPPORTED_SCHEMA:
        return UPDATING
    _load_index(blob, man)                                # the deploy finds out what the browser would: nothing is written yet
    data_dir.mkdir(parents=True)
    (data_dir / ptr["file"]).write_bytes(blob)
    (data_dir / "current.json").write_bytes((json.dumps(ptr, indent=1) + "\n").encode("utf-8"))   # only the checked fields
    return OPEN


# ---------------------------------------------------------------- config and robots
def write_config(site: Path, engine: Path, pyodide_dir: Path, data_state: str, detail: bool) -> Path:
    """`site`/check/config.json, read by the page. All paths are relative to the site root (where check.html lies)."""
    if data_state not in STATES:
        raise ValueError(f"unknown state {data_state!r}")
    site = Path(site)
    cfg = {
        "engine": Path(engine).relative_to(site).as_posix(),
        "pyodide": Path(pyodide_dir).relative_to(site).as_posix() + "/",
        "data": "data/current.json" if data_state == OPEN else None,
        "state": data_state,
        "detail": bool(detail),
        "notices": (Path(pyodide_dir) / NOTICES_NAME).relative_to(site).as_posix(),      # the footer links to it
    }
    path = site / "check" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(cfg, indent=1) + "\n").encode("utf-8"))
    return path


def check_page(site: Path) -> None:
    """The page must be able to carry the noindex tag: exactly one placeholder in check.html (a pre-check of the build)."""
    if (Path(site) / "check.html").read_bytes().count(PLACEHOLDER) != 1:
        raise ValueError("check.html must contain the placeholder <!-- robots --> exactly once")


def check_no_links(site: Path, is_link=os.path.islink) -> None:
    """No symbolic link anywhere in the page folder (ValueError, naming it): copytree would copy what the link points to,
    so a link in the repository to the checkout's .git/config (it holds the job's token) would be published. `is_link` is
    for the tests (a machine may lack the right to make links)."""
    site = Path(site)
    for p in sorted(site.rglob("*")):
        if is_link(p):
            raise ValueError(f"{p.relative_to(site).as_posix()} is a symbolic link: the page folder may hold files only")


def set_robots(site: Path, data_state: str) -> None:
    """closed/updating: the placeholder in check.html becomes the noindex tag (E4); open: it disappears. A page without
    exactly one placeholder cannot carry the tag, so the build stops. The bytes around it (line endings) stay."""
    path = Path(site) / "check.html"
    check_page(site)
    raw = path.read_bytes()
    path.write_bytes(raw.replace(PLACEHOLDER, b"" if data_state == OPEN else NOINDEX))


# ---------------------------------------------------------------- command line
def _sizes(out: Path) -> str:
    """Raw size of the artifact and an estimate of what a server sends: gzip (level 6) for what a server compresses, the
    raw size for what is compressed already (.zip, .woff2, .gz)."""
    raw = gz = 0
    groups: dict[str, list] = {}
    for p in sorted(Path(out).rglob("*")):
        if not p.is_file():
            continue
        blob = p.read_bytes()
        est = len(blob) if p.suffix in (".zip", ".gz", ".woff2") else len(gzip.compress(blob, 6))
        raw, gz = raw + len(blob), gz + est
        g = groups.setdefault(p.relative_to(out).parts[0] if len(p.relative_to(out).parts) > 1 else "(page)", [0, 0])
        g[0] += len(blob)
        g[1] += est
    lines = [f"  {k:<10} {v[0] / 1048576:8.2f} MB raw  {v[1] / 1048576:8.2f} MB gz-estimate" for k, v in sorted(groups.items())]
    lines.append(f"  {'total':<10} {raw / 1048576:8.2f} MB raw  {gz / 1048576:8.2f} MB gz-estimate")
    return "\n".join(lines)


def _clean(out: Path, created: bool) -> None:
    """After a stop: what the builder put into `out` goes again - the folder itself only if the builder made it."""
    if created:
        shutil.rmtree(out, ignore_errors=True)
    elif out.is_dir():
        for child in out.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)


def main(argv=None, allow_file: bool = False) -> int:
    ap = argparse.ArgumentParser(prog="python web/build_site.py", description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, help="the folder for the site (must not exist or be empty)")
    ap.add_argument("--site", default=str(ROOT / "public" / "site"), help="the page as it lies in the repository")
    ap.add_argument("--data-bundle", help="the folder web_data.py wrote: current.json and the zip it names (without: closed)")
    ap.add_argument("--pyodide-cache", default=str(ROOT / "build" / "cache" / "pyodide"))
    ap.add_argument("--detail", choices=("on", "off"), default="on", help="the default of the detail run (E1)")
    ap.add_argument("--pin", default=str(HERE / "pyodide.json"), help="the Pyodide pin (default web/pyodide.json)")
    a = ap.parse_args(argv)
    site, out = Path(a.site), Path(a.out)
    if not (site / "check.html").is_file():
        print(f"the page folder {site} has no check.html", file=sys.stderr)
        return 2
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        print(f"{out} must not exist or be an empty folder", file=sys.stderr)
        return 2
    pointer = bundle = None
    if a.data_bundle:
        pointer = Path(a.data_bundle) / "current.json"
        if not pointer.is_file():
            print(f"{pointer} not found: --data-bundle is the folder web_data.py wrote", file=sys.stderr)
            return 2
    created = not out.exists()
    try:
        pin = json.loads(Path(a.pin).read_text(encoding="utf-8"))
        if pointer is not None:
            bundle = pointer.parent / _pointer(pointer)["file"]
        check_page(site)                                  # the pre-checks: before anything is written, before any data
        check_no_links(site)
        _notices()
        shutil.copytree(site, out, dirs_exist_ok=True)
        pyodide_dir = fetch_pyodide(pin, Path(a.pyodide_cache), out, allow_file)
        add_notices(pyodide_dir)
        engine = engine_zip(ROOT, out)
        state = place_data(out, pointer, bundle)
        write_config(out, engine, pyodide_dir, state, a.detail == "on")
        set_robots(out, state)
    except (ValueError, OSError) as ex:
        _clean(out, created)                              # no half-built site with data stays behind
        print(f"site not built - the deploy must stop: {ex}", file=sys.stderr)
        return 1
    print(f"site: {out} (state {state}, detail {a.detail}, engine {engine.name}, Pyodide {pin['version']})\n{_sizes(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
