"""Local mode (.67): find the Grim Dawn game data, the saves and ClaudeSkill-Data on the machine the
check runs on - the player's Windows PC (Claude desktop client, GD-Tools), WSL, or a Cowork VM with
folders of the PC mounted under $HOME/mnt.

Modes
    windows   Windows: registry/Steam libraries, Documents (also OneDrive, "Dokumente"), Steam cloud
    wsl       Linux under WSL: the same layout below /mnt/<drive>/ (<drive>:\\Users\\<name>\\Documents ...)
    mounted   folders under $HOME/mnt/<folder>, classified by their content (never /mnt/user-data,
              /mnt/skills or the skill's own folder); folders down to 4 levels below a mount are
              classified (each looks at most 3 levels further for save\\main\\_<Name>\\player.gdc),
              symbolic links that leave the mount and bulky folders (AppData, node_modules, ...) are
              skipped, and the scan has a time limit and a folder budget. WSL also scans $HOME/mnt
              when /mnt/<drive>/ lacks the game data, the saves or ClaudeSkill-Data.
    none      nothing found: the player uploads player.gdc (or connects the folder in Cowork)

Read-only: this module never writes, renames or creates a file. Overrides (environment):
GDCHECK_GAME_DIR, GDCHECK_SAVE_DIR, GDCHECK_DATA_DIR (ClaudeSkill-Data or an unpacked patch bundle),
GDCHECK_INDEX, GDCHECK_LOCAL=0 (switch off). GD-Tools (lokal/gd_tools.py) uses the same functions.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .. import VERSION
from ..data import fingerprint as FP
from . import product

ROOT = Path(__file__).resolve().parents[2]
MODES = ("windows", "wsl", "mounted", "none")
INDEX_SCHEMA = 9                        # must match gdcheck.app.index_build.INDEX_SCHEMA
SAVE_DEPTH = 3                          # save\main\_<Name>, save\user\_<Name>, mod branches
MOUNT_DEPTH = 4
MOUNT_MAX_DIRS = 4000                   # folders the mount scan classifies at most
TIME_LIMIT = 8.0                        # seconds for the folder search (mount scan)
CHAR_TIME_LIMIT = 8.0                   # own budget for listing the characters below known save roots
# never entered by the mount scan: no saves inside, often huge (never the Steam/Program Files folders)
SKIP_DIRS = ("appdata", "node_modules", "$recycle.bin", ".git", "__pycache__", "system volume information",
             "windows", ".venv", "site-packages")
DATA_NAMES = ("claudeskill-data", "claudeskill-daten")
EXCLUDED_MOUNT_NAMES = ("user-data", "skills", "outputs", "uploads")
EXCLUDED_PATHS = (Path("/mnt/user-data"), Path("/mnt/skills"))
WSL_SKIP_USERS = ("public", "default", "default user", "all users")
DRIVES = "CDEFGH"
STEAM_FIXED = (("C", ("Program Files (x86)", "Steam")), ("C", ("Program Files", "Steam")), ("C", ("Steam",)),
               ("D", ("Steam",)), ("E", ("Steam",)), ("F", ("Steam",)))
GOG_GAMES_KEY = r"SOFTWARE\WOW6432Node\GOG.com\Games"       # one subkey per game: gameName, path, ...
GD_FALLBACKS = (("C", ("Program Files (x86)", "Steam", "steamapps", "common", "Grim Dawn")),
                ("C", ("Steam", "steamapps", "common", "Grim Dawn")),
                ("D", ("Steam", "steamapps", "common", "Grim Dawn")),
                ("D", ("SteamLibrary", "steamapps", "common", "Grim Dawn")),
                ("E", ("SteamLibrary", "steamapps", "common", "Grim Dawn")),
                ("C", ("GOG Games", "Grim Dawn")))
FOLDERID_DOCUMENTS = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"
# Steam EAppState bits (SteamKit; not documented by Valve): 4 = fully installed; these mean "files
# are being changed now or are broken". 2 (update queued) and 512 (paused) leave the files as they are.
STEAM_BUSY = (32 | 128 | 256 | 1024 | 2048 | 65536 | 131072 | 262144 | 524288 | 1048576 | 2097152
              | 4194304 | 8388608)
VERSION_IN_TEXT = re.compile(r"\b(\d{1,2}(?:\.\d{1,5}){2,3})\b")


# ---------------------------------------------------------------------------- results
@dataclass(frozen=True)
class LocalChar:
    name: str
    gdc: Path
    branch: str                         # folder between save root and _<Name> ("main", "user"; "-" = none)
    root: Path                          # the save root it was found under
    mtime: float
    has_quests: bool                    # levels_world001.map next to it (campaign progress)

    def to_dict(self, default: bool = False) -> dict:
        return {"name": self.name, "gdc": str(self.gdc), "branch": self.branch, "root": str(self.root),
                "mtime": self.mtime, "modified": datetime.fromtimestamp(self.mtime).isoformat(timespec="seconds"),
                "has_quests": self.has_quests, "default": default}


@dataclass(frozen=True)
class LocalEnv:
    mode: str                           # windows | wsl | mounted | none
    game_dir: Path | None
    save_roots: tuple
    characters: tuple                   # LocalChar, newest first
    data_dir: Path | None
    out_dir: Path | None
    index: Path | None
    index_state: str                    # current | stale | missing | unchecked
    fingerprint: str | None
    game_version: str | None
    version_source: str
    notes: tuple

    def defaults(self) -> tuple:
        """The newest save of every character name (duplicates: Steam cloud + local, mod branch)."""
        seen, out = set(), []
        for c in self.characters:
            if c.name.lower() not in seen:
                seen.add(c.name.lower())
                out.append(c)
        return tuple(out)

    def to_dict(self) -> dict:
        defaults = {id(c) for c in self.defaults()}
        s = lambda p: str(p) if p is not None else None      # noqa: E731
        return {"schema": 1, "skill_version": VERSION, "mode": self.mode, "game_dir": s(self.game_dir),
                "save_roots": [str(p) for p in self.save_roots],
                "characters": [c.to_dict(id(c) in defaults) for c in self.characters],
                "data_dir": s(self.data_dir), "out_dir": s(self.out_dir), "index": s(self.index),
                "index_state": self.index_state, "fingerprint": self.fingerprint,
                "game_version": self.game_version, "version_source": self.version_source,
                "notes": list(self.notes)}


# ---------------------------------------------------------------------------- small helpers
def exists(p) -> bool:
    """Existence check that does not fail on a missing drive or a bad name."""
    try:
        return Path(p).exists()
    except (OSError, ValueError):
        return False


def is_dir(p) -> bool:
    try:
        return Path(p).is_dir()
    except (OSError, ValueError):
        return False


def is_game_dir(p) -> bool:
    return p is not None and exists(Path(p) / "database" / "database.arz")


def _unique(paths: Iterable[Path]) -> list[Path]:
    seen, out = set(), []
    for p in paths:
        key = str(p).lower().rstrip("\\/")
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _resolved(p: Path) -> Path:
    try:
        return Path(p).resolve()
    except (OSError, RuntimeError):
        return Path(os.path.abspath(p))


def _subdirs(folder: Path) -> list[Path]:
    try:
        return sorted(p for p in Path(folder).iterdir() if p.is_dir())
    except OSError:
        return []


def current_system() -> str:
    """windows | wsl | linux (anything else counts as linux: macOS, the cloud)."""
    if sys.platform.startswith("win"):
        return "windows"
    try:
        with open("/proc/version", encoding="utf-8", errors="replace") as fh:
            if "microsoft" in fh.read(400).lower():
                return "wsl"
    except OSError:
        pass
    return "linux"


def windows_drives() -> dict:
    return {d: Path(f"{d}:\\") for d in DRIVES}


def wsl_drives(wsl_root: Path) -> dict:
    return {d: Path(wsl_root) / d.lower() for d in DRIVES if is_dir(Path(wsl_root) / d.lower())}


def wsl_path(text: str, wsl_root: Path = Path("/mnt")) -> Path:
    """'D:\\SteamLibrary' -> /mnt/d/SteamLibrary (paths in Steam's files are Windows paths)."""
    t = str(text).replace("\\\\", "\\")
    m = re.match(r"^([A-Za-z]):[\\/]?(.*)$", t)
    if not m:
        return Path(t)
    parts = [x for x in re.split(r"[\\/]+", m.group(2)) if x]
    return Path(wsl_root, m.group(1).lower(), *parts)


# ---------------------------------------------------------------------------- Steam and the game
def registry_steam_paths() -> list[Path]:
    """Steam folder from the Windows registry (empty elsewhere)."""
    out = []
    try:
        import winreg
    except ImportError:
        return out
    for hive, key, value in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(hive, key) as k:
                out.append(Path(str(winreg.QueryValueEx(k, value)[0]).replace("/", "\\")))
        except OSError:
            pass
    return out


def _winreg_entries(hive: str, key: str) -> list[dict]:
    """Values gameName and path of every subkey of HKLM\\<key> (empty without Windows or the key)."""
    if not sys.platform.startswith("win"):
        return []
    import winreg
    out: list[dict] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_READ) as parent:
            names = [winreg.EnumKey(parent, i) for i in range(winreg.QueryInfoKey(parent)[0])]
    except OSError:
        return out
    for name in names:
        entry: dict = {}
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{key}\\{name}", 0, winreg.KEY_READ) as sub:
                for value in ("gameName", "path"):
                    try:
                        entry[value] = winreg.QueryValueEx(sub, value)[0]
                    except OSError:
                        pass
        except OSError:
            continue
        out.append(entry)
    return out


def gog_game_dirs(registry: Callable[[str, str], Mapping | Iterable] | None = None) -> list[Path]:
    """Grim Dawn folders GOG Galaxy registered under HKLM\\SOFTWARE\\WOW6432Node\\GOG.com\\Games (GOG has no
    appmanifest and no library list). registry replaces the Windows registry: (hive, key) -> the entries
    below the key, as a dict of entries or a list of entries. Folders without a database.arz are dropped."""
    try:
        found = (registry or _winreg_entries)("HKLM", GOG_GAMES_KEY)
    except OSError:
        return []
    entries = found.values() if isinstance(found, Mapping) else found
    out: list[Path] = []
    for entry in entries or ():
        if not isinstance(entry, Mapping) or not str(entry.get("gameName", "")).strip().lower().startswith("grim dawn"):
            continue
        path = str(entry.get("path") or "").strip()
        if path and is_game_dir(Path(path)):
            out.append(Path(path))
    return _unique(out)


def steam_roots(game_dir=None, drives: Mapping | None = None, registry: bool = True) -> list[Path]:
    """Possible Steam main folders (userdata\\<ID>\\219990 lives there, never in an extra library).
    Order: derived from the installation (<Steam>\\steamapps\\common\\Grim Dawn), registry, fixed
    list, default folders on C: to H:."""
    drives = windows_drives() if drives is None else drives
    cand: list[Path] = []
    if game_dir is not None:
        parts = [x.lower() for x in Path(game_dir).parts]
        if len(parts) >= 3 and parts[-3:-1] == ["steamapps", "common"]:
            cand.append(Path(game_dir).parent.parent.parent)
    if registry:
        cand += registry_steam_paths()
    cand += [Path(drives[d]).joinpath(*rel) for d, rel in STEAM_FIXED if d in drives]
    for d in DRIVES:
        if d in drives:
            r = Path(drives[d])
            cand += [r / "Program Files (x86)" / "Steam", r / "Program Files" / "Steam", r / "Steam"]
    return _unique(cand)


def steam_libraries(roots: Iterable[Path], translate: Callable[[str], Path] | None = None) -> list[Path]:
    """steamapps\\common folders of all Steam libraries (libraryfolders.vdf)."""
    libs: list[Path] = []
    for root in roots:
        vdf = Path(root) / "steamapps" / "libraryfolders.vdf"
        if not exists(vdf):
            continue
        libs.append(Path(root) / "steamapps" / "common")
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r'"path"\s+"(.+?)"', text):          # "path"   "D:\\SteamLibrary"
            raw = m.group(1)
            base = translate(raw) if translate else Path(raw.replace("\\\\", "\\"))
            libs.append(base / "steamapps" / "common")
    return libs


def fallback_game_dirs(drives: Mapping | None = None) -> list[Path]:
    drives = windows_drives() if drives is None else drives
    return [Path(drives[d]).joinpath(*rel) for d, rel in GD_FALLBACKS if d in drives]


def find_game_dir(hint=None, near: Iterable[Path] = (), libraries: Iterable[Path] = (),
                  fallbacks: Iterable[Path] = (), gog: Iterable[Path] = ()) -> Path | None:
    """Order: hint > folders around the tools (ClaudeSkill lives inside the installation) > Steam
    libraries > GOG installs (gog_game_dirs) > default paths."""
    if hint and is_game_dir(hint):
        return Path(hint)
    for p in near:
        if is_game_dir(p):
            return Path(p)
    for lib in libraries:
        if is_game_dir(Path(lib) / "Grim Dawn"):
            return Path(lib) / "Grim Dawn"
    for p in gog:
        if is_game_dir(p):
            return Path(p)
    for p in fallbacks:
        if is_game_dir(p):
            return Path(p)
    return None


def installed_game_dir(hint=None) -> Path | None:
    """The Grim Dawn folder for the PC tools' command lines when --gd-dir is omitted (index build,
    record view; GD-Tools always passes it): GDCHECK_GAME_DIR, Steam libraries, default paths, the
    Linux Steam folders of the .35 list."""
    drives = windows_drives() if current_system() == "windows" else {}
    home = Path.home()
    linux = [home / ".steam" / "steam" / "steamapps" / "common" / "Grim Dawn",
             home / ".local" / "share" / "Steam" / "steamapps" / "common" / "Grim Dawn"]
    return find_game_dir(hint or os.environ.get("GDCHECK_GAME_DIR"), (),
                         steam_libraries(steam_roots(None, drives)), fallback_game_dirs(drives) + linux,
                         gog_game_dirs() if current_system() == "windows" else ())


def steam_status(game_dir) -> dict | None:
    """Grim Dawn in Steam (.53): build id, branch (BetaKey) and whether Steam is still updating
    (StateFlags). None without appmanifest_219990.acf (GOG, other folder)."""
    try:
        acf = Path(game_dir).parent.parent / "appmanifest_219990.acf"
        if not exists(acf):
            return None
        text = acf.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError, TypeError):
        return None

    def field(name):
        m = re.search(r'"%s"\s+"([^"]*)"' % name, text)
        return m.group(1) if m else None
    try:
        flags = int(field("StateFlags") or 4)
    except ValueError:
        flags = 4
    beta = (field("BetaKey") or "").strip()
    return {"buildid": field("buildid"), "state_flags": flags,
            "updating": bool(flags & STEAM_BUSY) or not flags & 4,
            "branch": beta if beta and beta.lower() != "public" else "public"}


# ---------------------------------------------------------------------------- documents and logs
def known_folder(folderid: str) -> Path | None:
    """Windows system folder via SHGetKnownFolderPath ("Documents" may be redirected anywhere -
    OneDrive, a network drive, another drive). None outside Windows."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        guid_type = ctypes.c_byte * 16
        parts = folderid.strip("{}").split("-")
        raw = (int(parts[0], 16).to_bytes(4, "little") + int(parts[1], 16).to_bytes(2, "little")
               + int(parts[2], 16).to_bytes(2, "little") + bytes.fromhex(parts[3]) + bytes.fromhex(parts[4]))
        ptr = ctypes.c_wchar_p()
        rc = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid_type(*raw)), 0, None, ctypes.byref(ptr))
        if rc != 0 or not ptr.value:
            return None
        path = Path(ptr.value)
        ctypes.windll.ole32.CoTaskMemFree(ptr)
        return path
    except Exception:                   # noqa: BLE001 - a guess is enough without it
        return None


def documents_folders(home=None, use_known_folder: bool = True) -> list[Path]:
    """All plausible Documents folders incl. OneDrive redirects ("OneDrive - <Company>", "Dokumente")."""
    home = Path(home) if home is not None else Path(os.path.expanduser("~"))
    official = known_folder(FOLDERID_DOCUMENTS) if use_known_folder else None
    bases = [home / "Documents", home / "Dokumente", home / "OneDrive" / "Documents"]
    try:
        for d in home.iterdir():
            if d.is_dir() and d.name.startswith("OneDrive"):
                bases += [d / "Documents", d / "Dokumente"]
    except OSError:
        pass
    if official:
        bases.insert(0, official)
    return _unique(bases)


def log_places(docs: Iterable[Path], game_dir=None, my_games: Iterable[Path] = ()) -> list[Path]:
    places = [Path(b) / "My Games" / "Grim Dawn" for b in docs] + [Path(p) for p in my_games]
    if game_dir is not None:
        places += [Path(game_dir), Path(game_dir) / "settings"]      # some installs log locally
    return places


def log_files(places: Iterable[Path]) -> list[Path]:
    """All game logs found, newest first."""
    hits = []
    for folder in places:
        if not exists(folder):
            continue
        try:
            hits += [d for d in Path(folder).glob("log*.htm*") if d.is_file()]
            hits += [d for d in Path(folder).glob("log*.txt") if d.is_file()]
        except OSError:
            continue
    hits.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    return hits


def log_text(path: Path, size: int = 200_000) -> str:
    """Head of a log as plain text (Grim Dawn writes HTML: 'Version <b>v1.3.0.8</b>')."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read(size)
    except OSError:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]{0,200}>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"[ \t]{2,}", " ", text)


def log_version(places: Iterable[Path]):
    """(game version, log file) from the newest log.html, or None. Three steps from exact to coarse."""
    for path in log_files(places):
        text = log_text(path)
        if not text:
            continue
        for pattern in (r"Grim\s*Dawn\s+Version\s+v?(\d{1,2}(?:\.\d{1,5}){1,3})",
                        r"Version[:\s]+v?(\d{1,2}(?:\.\d{1,5}){2,3})"):
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                return m.group(1), path
        head = "\n".join(text.splitlines()[:40])
        m = VERSION_IN_TEXT.search(head)
        if m and m.group(1).count(".") >= 2:
            return m.group(1), path
    return None


# ---------------------------------------------------------------------------- saves
def save_roots(hint=None, docs: Iterable[Path] = (), steam: Iterable[Path] = ()) -> list[Path]:
    roots: list[Path] = []
    if hint and exists(hint):
        roots.append(Path(hint))
    for base in docs:
        p = Path(base) / "My Games" / "Grim Dawn" / "save"
        if exists(p):
            roots.append(p)
    for root in steam:
        ud = Path(root) / "userdata"
        if not exists(ud):
            continue
        try:
            accounts = list(ud.iterdir())
        except OSError:
            continue
        for acc in accounts:
            p = acc / "219990" / "remote" / "save"
            if exists(p):
                roots.append(p)
    return _unique(roots)


def walk_dirs(root: Path, depth: int, deadline: float | None = None):
    """Subfolders down to `depth`, robust against permission errors; stops at the deadline."""
    if depth < 0 or (deadline is not None and time.monotonic() > deadline):
        return
    for d in _subdirs(root):
        yield d
        if depth > 0:
            yield from walk_dirs(d, depth - 1, deadline)


def characters(roots: Iterable[Path], depth: int = SAVE_DEPTH, include_root: bool = False,
               deadline: float | None = None) -> list[LocalChar]:
    """All character folders (with player.gdc) below the save roots, newest first. Grim Dawn uses
    save\\main\\_<Name> (mods: save\\user\\_<Name> or own branches). The same file reached via
    two roots is listed once; two different files of one character (Steam cloud + local) both."""
    found, seen = [], set()
    for root in roots:
        root = Path(root)
        dirs = ([root] if include_root else []) + list(walk_dirs(root, depth, deadline))
        for d in dirs:
            gdc = d / "player.gdc"
            if not exists(gdc):
                continue
            key = str(_resolved(gdc)).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                branch = d.parent.relative_to(root).as_posix() if d != root else ""
            except ValueError:
                branch = ""
            try:
                mtime = gdc.stat().st_mtime
            except OSError:
                continue
            found.append(LocalChar(name=d.name[1:] if d.name.startswith("_") else d.name, gdc=gdc,
                                   branch=branch if branch not in ("", ".") else "-", root=root, mtime=mtime,
                                   has_quests=is_dir(d / "levels_world001.map")))
    found.sort(key=lambda c: c.mtime, reverse=True)
    return found


# ---------------------------------------------------------------------------- index
def index_meta(path: Path) -> dict:
    """_meta of a built index without unpacking all of it."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            raw = fh.read(16384)
        mark = '"_meta":'
        start = raw.find(mark)
        if start < 0:
            return {}
        # decode only the _meta block ("records" appears inside _meta as a count, too)
        pos = start + len(mark)
        while pos < len(raw) and raw[pos].isspace():
            pos += 1
        meta, _ = json.JSONDecoder().raw_decode(raw, pos)
        return meta if isinstance(meta, dict) else {}
    except Exception:                   # noqa: BLE001 - unreadable = no metadata
        return {}


def index_files(folder) -> list[Path]:
    try:
        return sorted(Path(folder).glob("gd-index-*.json.gz")) if is_dir(folder) else []
    except OSError:
        return []


def index_status(files: list[Path], fingerprint: str | None, schema: int = INDEX_SCHEMA) -> tuple:
    """(state, explanation, index) for GD-Tools' index files (oldest first): does an index fit the game
    data? state: missing | stale | unchecked | current | unknown (index without fingerprint)."""
    if not files:
        return "missing", "No index built yet.", None
    last = files[-1]
    if not fingerprint:
        m = index_meta(last)
        if m.get("schema", 1) < schema:
            return "stale", (f"{last.name} has schema {m.get('schema', 1)}, the scripts need schema {schema} "
                             "(new fields)."), last
        return "unchecked", f"Game data fingerprint not determinable - {last.name} is used unchecked.", last
    fitting = [(d, m) for d in files for m in [index_meta(d)] if m.get("fingerprint") == fingerprint]
    if fitting:
        d, m = fitting[-1]
        if m.get("schema", 1) < schema:
            return "stale", (f"{d.name} has schema {m.get('schema', 1)}, the scripts need schema {schema} "
                             "(new fields)."), d
        return "current", f"{d.name} matches the game data (built {m.get('erstellt', '?')}).", d
    old = index_meta(last)
    if not old.get("fingerprint"):
        return "unknown", (f"{last.name} comes from an older version without fingerprint - age cannot be "
                           "checked."), last
    return "stale", (f"{last.name} was built from different game data (index {old['fingerprint']}, "
                     f"now {fingerprint})."), last


def index_candidates(out_dir: Path | None, skill_root: Path | None = None) -> list[Path]:
    """Indexes in the order they are tried: out/, out/previous/ (newest name first), assets/."""
    found: list[Path] = []
    if out_dir is not None:
        found += list(reversed(index_files(out_dir))) + list(reversed(index_files(Path(out_dir) / "previous")))
    found += list(reversed(index_files(Path(skill_root or ROOT) / "assets")))
    return found


def _pick_index(out_dir, fingerprint, skill_root=None) -> tuple:
    usable = [p for p in index_candidates(out_dir, skill_root) if index_meta(p).get("schema", 1) >= INDEX_SCHEMA]
    if not usable:
        return None, "missing"
    if not fingerprint:
        return usable[0], "unchecked"
    for p in usable:
        if index_meta(p).get("fingerprint") == fingerprint:
            return p, "current"
    return usable[0], "stale"


def best_index(env: LocalEnv, skill_root: Path | None = None) -> Path | None:
    """The index for this game data: fingerprint match in out/, out/previous/, assets/ - else the
    newest of them (out/ first). GDCHECK_INDEX is applied by detect(), not here."""
    return _pick_index(env.out_dir, env.fingerprint, skill_root)[0]


def _index_state_of(path: Path, fingerprint) -> str:
    fp = index_meta(path).get("fingerprint")
    if not fingerprint or not fp:
        return "unchecked"
    return "current" if fp == fingerprint else "stale"


# ---------------------------------------------------------------------------- mounts
def _is_excluded(p: Path, skill_root: Path) -> bool:
    rp = _resolved(p)
    for ex in EXCLUDED_PATHS + (_resolved(skill_root),):
        if rp == ex or ex in rp.parents:
            return True
    return False


def _is_skill_copy(p: Path) -> bool:
    return exists(Path(p) / "gdcheck" / "__init__.py")


def mount_roots(home=None, skill_root=None) -> list[Path]:
    """Cowork: the player's folders are mounted at $HOME/mnt/<folder> (not the uploads/skills mounts)."""
    base = (Path(home) if home is not None else Path.home()) / "mnt"
    skill = Path(skill_root) if skill_root is not None else ROOT
    out = []
    for d in _subdirs(base):
        if d.name.startswith(".") or d.name.lower() in EXCLUDED_MOUNT_NAMES or _is_excluded(d, skill):
            continue
        out.append(d)
    return out


def _has_player(folder: Path) -> bool:
    return exists(Path(folder) / "player.gdc")


def _expired(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() > deadline


def _inside(p: Path, root: Path) -> bool:
    return p == root or root in p.parents


def _is_link(p: Path) -> bool:
    """Symbolic link or (Windows) junction - both can lead anywhere on the machine."""
    try:
        return p.is_symlink() or bool(getattr(p, "is_junction", lambda: False)())
    except OSError:
        return True


def _scan_subdirs(folder: Path, within: Path | None = None) -> list[Path]:
    """Subfolders the mount scan may look into: no dot folders, none of SKIP_DIRS and no link that leads
    out of `within` (the resolved mount) - a mount must never become a search of the whole machine."""
    out = []
    for d in _subdirs(folder):
        if d.name.startswith(".") or d.name.lower() in SKIP_DIRS:
            continue
        if within is not None and _is_link(d) and not _inside(_resolved(d), within):
            continue
        out.append(d)
    return out


def _is_save_root(folder: Path, within: Path | None = None, deadline: float | None = None) -> bool:
    """player.gdc in the folder, in a child (_<Name>) or a grandchild (main\\_<Name>)."""
    if _has_player(folder):
        return True
    for c in _scan_subdirs(folder, within):
        if _expired(deadline):
            return False
        if _has_player(c) or any(_has_player(g) for g in _scan_subdirs(c, within)):
            return True
    return False


def classify(folder, deadline: float | None = None, within: Path | None = None) -> set:
    """What a folder is, by content: game (database\\database.arz), save (characters below it),
    mygames (My Games\\Grim Dawn: a save\\ folder with characters), data (ClaudeSkill-Data).
    Stops looking (and returns what it has) at the deadline; `within`: links leaving it are not followed."""
    folder = Path(folder)
    kinds: set = set()
    if not is_dir(folder):
        return kinds
    if is_game_dir(folder):
        kinds.add("game")
    if (folder.name.lower() in DATA_NAMES or exists(folder / "gd_tools.json")
            or index_files(folder / "out")):
        kinds.add("data")
    if "game" in kinds or _expired(deadline):
        return kinds
    if _is_save_root(folder, within, deadline):
        kinds.add("save")
    elif not _expired(deadline) and is_dir(folder / "save") and _is_save_root(folder / "save", within, deadline):
        kinds.add("mygames")
    if "data" in kinds:
        kinds.discard("save")
    return kinds


def scan_mounts(mounts: Iterable[Path], skill_root: Path, depth: int = MOUNT_DEPTH,
                deadline: float | None = None, max_dirs: int = MOUNT_MAX_DIRS) -> tuple[dict, bool]:
    """Classify every folder below the mounts down to `depth` (a classified folder is not entered).
    Never follows a link out of its mount, never enters SKIP_DIRS or a folder twice; at most `max_dirs`
    folders. Returns ({kind: [folders]}, stopped at the deadline or the folder budget)."""
    found: dict = {"game": [], "save": [], "mygames": [], "data": []}
    stopped = False
    seen: set = set()

    def visit(folder: Path, level: int, within: Path):
        nonlocal stopped
        if stopped:
            return
        if _expired(deadline) or len(seen) >= max_dirs:
            stopped = True
            return
        key = str(_resolved(folder)).lower()
        if key in seen or _is_excluded(folder, skill_root) or _is_skill_copy(folder):
            return
        seen.add(key)
        kinds = classify(folder, deadline, within)
        if _expired(deadline):
            stopped = True
        for k in kinds:
            found[k].append(folder)
        if kinds or level >= depth:
            return
        for d in _scan_subdirs(folder, within):
            visit(d, level + 1, within)
    for m in mounts:
        visit(Path(m), 0, _resolved(Path(m)))
    return found, stopped


# ---------------------------------------------------------------------------- settings
def read_settings(data_dir) -> dict:
    """GD-Tools' gd_tools.json (version, savedir, fingerprint, ...) - {} if missing or broken."""
    if data_dir is None:
        return {}
    try:
        d = json.loads((Path(data_dir) / "gd_tools.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _out_of(data_dir: Path | None) -> Path | None:
    if data_dir is None:
        return None
    if is_dir(Path(data_dir) / "out"):
        return Path(data_dir) / "out"
    return Path(data_dir) if index_files(data_dir) or any(Path(data_dir).glob("*-*.json")) else None


# ---------------------------------------------------------------------------- detect
def detect(environ: Mapping[str, str] | None = None, *, game_dir=None, save_dir=None, data_dir=None,
           system: str | None = None, home=None, drives: Mapping | None = None, wsl_root=None,
           skill_root=None, registry: bool = True, time_limit: float = TIME_LIMIT,
           char_time_limit: float = CHAR_TIME_LIMIT) -> LocalEnv:
    """Find everything the check needs on this machine (read-only). Keyword arguments other than the
    three folders replace the machine's facts (tests simulate Windows and WSL with them).
    time_limit bounds the folder search (mount scan); listing the characters below the save roots found
    has its own budget (char_time_limit), so a slow scan never hides the saves it found."""
    env = os.environ if environ is None else environ
    notes: list[str] = []
    if str(env.get("GDCHECK_LOCAL", "1")).strip() == "0":
        return LocalEnv("none", None, (), (), None, None, None, "missing", None, None, "unknown",
                        ("local mode switched off (GDCHECK_LOCAL=0) - pass the player.gdc yourself",))
    system = system or current_system()
    skill = Path(skill_root) if skill_root is not None else ROOT
    deadline = time.monotonic() + max(0.0, time_limit)

    game = _override(game_dir or env.get("GDCHECK_GAME_DIR"), "GDCHECK_GAME_DIR", notes, game=True)
    save_hint = _override(save_dir or env.get("GDCHECK_SAVE_DIR"), "GDCHECK_SAVE_DIR", notes)
    data = _override(data_dir or env.get("GDCHECK_DATA_DIR"), "GDCHECK_DATA_DIR", notes)
    my_games: list[Path] = []
    stopped = False

    if system in ("windows", "wsl"):
        if system == "windows":
            drv = windows_drives() if drives is None else drives
            docs = documents_folders(home, use_known_folder=home is None)
            translate = None
        else:
            root = Path(wsl_root) if wsl_root is not None else Path("/mnt")
            drv = wsl_drives(root) if drives is None else drives
            docs = []
            for r in drv.values():
                for user in _subdirs(Path(r) / "Users"):
                    if user.name.lower() not in WSL_SKIP_USERS:
                        docs += documents_folders(user, use_known_folder=False)
            translate = lambda t, _r=root: wsl_path(t, _r)        # noqa: E731
            registry = False
        if game is None:
            near = [skill] + list(skill.parents)[:3]
            libs = steam_libraries(steam_roots(None, drv, registry), translate)
            game = find_game_dir(None, near, libs, fallback_game_dirs(drv), gog_game_dirs() if registry else ())
        if data is None:
            data = next((p for p in (skill.parent / "ClaudeSkill-Data",
                                     game / "ClaudeSkill-Data" if game else None) if p and is_dir(p)), None)
        settings = read_settings(data)
        if save_hint is not None:
            roots = [save_hint]
        else:
            roots = save_roots(settings.get("savedir"), docs, steam_roots(game, drv, registry))
        places = log_places(docs, game)
        mode = system
        if system == "wsl" and (game is None or not roots or data is None):
            # WSL inside a Cowork-like VM: the player's folders may be mounted under $HOME/mnt instead
            m_game, m_data, m_roots, my_games, stopped, m_settings = _from_mounts(
                home, skill, game, save_hint, data, deadline)
            if not (game or roots or data) and (m_game or m_roots or m_data):
                mode = "mounted"
            game, data = game or m_game, data or m_data
            settings = settings or m_settings
            roots = _unique(list(roots) + list(m_roots))
            places = log_places(docs, game, my_games)
    else:
        game, data, roots, my_games, stopped, settings = _from_mounts(home, skill, game, save_hint, data,
                                                                      deadline)
        places = log_places([], game, my_games)
        mode = "mounted" if (game or roots or data) else "none"

    # own budget: the known save roots are always walked (depth 3), however long the search took
    char_deadline = time.monotonic() + max(0.0, char_time_limit)
    chars = tuple(characters(roots, include_root=True, deadline=char_deadline))
    if _expired(char_deadline):
        stopped = True
    out = _out_of(data)
    fingerprint = FP.db_fingerprint(game) if game is not None else None
    forced = env.get("GDCHECK_INDEX")
    if forced and exists(forced):
        index, state = Path(forced), _index_state_of(Path(forced), fingerprint)
    else:
        if forced:
            notes.append(f"GDCHECK_INDEX not found: {forced} - the index is chosen automatically")
        index, state = _pick_index(out, fingerprint)
    version, source = _game_version(places, settings, index)
    if stopped:
        notes.append(f"search stopped at the time limit ({time_limit:g} s) or after {MOUNT_MAX_DIRS} folders - "
                     "set GDCHECK_SAVE_DIR / GDCHECK_GAME_DIR to the folders")
    notes += _notes(mode, game, chars, index, state, fingerprint)
    return LocalEnv(mode=mode, game_dir=game,
                    save_roots=tuple(roots), characters=chars, data_dir=data, out_dir=out, index=index,
                    index_state=state, fingerprint=fingerprint, game_version=version, version_source=source,
                    notes=tuple(notes))


def _from_mounts(home, skill: Path, game, save_hint, data, deadline) -> tuple:
    """(game, data, save roots, My Games folders, stopped, settings) from the folders under $HOME/mnt."""
    mounts = mount_roots(home, skill)
    found, stopped = (scan_mounts(mounts, skill, deadline=deadline)
                      if mounts and (game is None or save_hint is None or data is None) else ({}, False))
    if game is None and found.get("game"):
        game = found["game"][0]
    if data is None:
        cands = [game / "ClaudeSkill-Data"] if game else []
        cands += found.get("data", [])
        data = next((p for p in cands if is_dir(p)), None)
    settings = read_settings(data)
    my_games = list(found.get("mygames", []))
    if save_hint is not None:
        roots = [save_hint]
    else:
        roots = _unique(list(found.get("save", [])) + [p / "save" for p in my_games])
        hint = settings.get("savedir")
        if hint and is_dir(hint):
            roots.append(Path(hint))
    return game, data, roots, my_games, stopped, settings


def _override(value, name: str, notes: list, game: bool = False) -> Path | None:
    if not value:
        return None
    p = Path(value)
    if game and not is_game_dir(p):
        notes.append(f"{name} is not a Grim Dawn folder (no database\\database.arz): {p} - searching instead")
        return None
    if not game and not is_dir(p):
        notes.append(f"{name} not found: {p} - searching instead")
        return None
    return p


def _game_version(places, settings: dict, index) -> tuple:
    found = log_version(places)
    if found:
        return found[0], f"log.html ({found[1].parent})"
    if settings.get("version"):
        return str(settings["version"]), "gd_tools.json" if product.enabled() else "gd_tools.json (ClaudeSkill-Data)"
    if index is not None and index_meta(index).get("game_version"):
        return str(index_meta(index)["game_version"]), f"index ({Path(index).name})"
    return None, "unknown"


def _notes(mode, game, chars, index, state, fingerprint) -> list[str]:
    notes = []
    if mode == "none" and not chars:
        return ["no Grim Dawn folders found on this machine - ask the player to upload player.gdc "
                "(save\\main\\_<Name>\\player.gdc) or, in Cowork, to connect the Grim Dawn save folder"]
    if game is None:
        where = "game data not mounted" if mode == "mounted" else "game data not found"
        notes.append(f"{where} - item values come from "
                     + (f"{Path(index).name} (not checked against the installation)" if index else "no index"))
    if not chars:
        notes.append("no characters found (no player.gdc below the save folders) - connect or name the save "
                     "folder (Documents\\My Games\\Grim Dawn\\save)")
    if state == "stale":
        notes.append(f"index {Path(index).name} is from other game data (installed {fingerprint}) - start "
                     "GD-Tools once to rebuild it; item values may be outdated")
    elif state == "missing":
        notes.append("no game data index - start GD-Tools once (it builds the index)")
    dups = {}
    for c in chars:
        dups.setdefault(c.name.lower(), []).append(c)
    for group in dups.values():
        if len(group) > 1:
            notes.append(f"{group[0].name}: {len(group)} saves ("
                         + ", ".join(f"{c.root}" for c in group) + ") - the newest is used")
    return notes


def find_character(env: LocalEnv, name: str) -> LocalChar:
    """Exact name (case-insensitive; duplicates: the newest) > unique part of a name; else ValueError
    with the candidates."""
    want = str(name).strip().lower().lstrip("_")
    exact = [c for c in env.characters if c.name.lower() == want]
    if exact:
        return exact[0]
    part = [c for c in env.defaults() if want in c.name.lower()]
    if len(part) == 1:
        return part[0]
    all_names = ", ".join(c.name for c in env.defaults()) or "none"
    if not part:
        raise ValueError(f"No character named '{name}'. Found: {all_names}")
    raise ValueError(f"'{name}' fits several characters: {', '.join(c.name for c in part)} - give the full name")


def describe(env: LocalEnv) -> str:
    """--where as text for the console and the chat."""
    defaults = {id(c) for c in env.defaults()}
    who = f"{product.NAME} {VERSION}" if product.enabled() else f"skill {VERSION}"
    lines = [f"Local mode:    {env.mode}   ({who})",
             f"Game folder:   {env.game_dir or 'not found'}",
             f"Game version:  {env.game_version or '?'}   (source: {env.version_source})",
             f"Fingerprint:   {env.fingerprint or 'not determinable (no game data)'}",
             f"Data folder:   {env.data_dir or 'not found'}",
             f"Index:         {env.index or 'none'}   ({env.index_state})",
             "Save folders:  " + ("; ".join(str(p) for p in env.save_roots) or "none")]
    if env.characters:
        lines.append(f"Characters ({len(env.characters)}, newest first; * = used for that name):")
        width = max(len(c.name) for c in env.characters)
        for c in env.characters:
            when = datetime.fromtimestamp(c.mtime).strftime("%Y-%m-%d %H:%M")
            lines.append(f"  {'*' if id(c) in defaults else ' '} {c.name.ljust(width)}  {c.branch:<6} {when}  "
                         f"{'quests ' if c.has_quests else '       '}{c.gdc}")
    else:
        lines.append("Characters:    none")
    if env.notes:
        lines.append("Notes:")
        lines += [f"  - {n}" for n in env.notes]
    if env.characters:
        lines.append('Next: python -m gdcheck --character "<name>" --md player   |   python -m gdcheck --all')
    return "\n".join(lines) + "\n"
