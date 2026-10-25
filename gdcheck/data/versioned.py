"""Version-bound reference files: which references/<prefix>-<version>.json fits the game data (.53).

The identity of the game data is the database fingerprint of the index (size + head/tail of every
.arz and the EN text archive). The version label is only a name: GD-Tools takes it from log.html or
its settings and it is stale after a Steam patch until the game ran once (critic review .53).

Order (best first; ties: the skill's own file before an extra folder unless noted):
    1. same fingerprint                     the file was extracted from exactly this game data
    2. same label, fingerprint unknown      (no note) - an extra folder first (PC read of this game)
    3. same label, other fingerprint        another build of that version: extra folder first, NOTE
    4. newest version <= the requested one  ASSUMED + NOTE ("... of game version X used for Y")
    5. any other file (newer / no number)   ASSUMED + NOTE
Extra folders: GDCHECK_DATA_DIR (a patch bundle unpacked in a chat, the out/ folder found by local
mode, or ClaudeSkill-Data itself - then its out/ too), then the PC's
ClaudeSkill-Data/out unless GDCHECK_PC_DATA=0 or GDCHECK_PC_FRAMES=0 (set by the tests). The
environment is read on every call - never inside a cache. The animation frames keep their own guard
(data.animation.frames_table, .52).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REFS = ROOT / "references"
PC_OUT = ROOT.parent / "ClaudeSkill-Data" / "out"


def version_key(label) -> tuple | None:
    """'1.3.0.8' -> (1, 3, 0, 8); None for labels without numbers ('db0e25...', '?')."""
    parts = str(label or "").strip().split(".")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def extra_folders() -> list[Path]:
    out = []
    d = os.environ.get("GDCHECK_DATA_DIR")
    if d:
        out.append(Path(d))
        if (Path(d) / "out").is_dir():          # ClaudeSkill-Data itself (local mode, .67)
            out.append(Path(d) / "out")
    if os.environ.get("GDCHECK_PC_DATA", "1") != "0" and os.environ.get("GDCHECK_PC_FRAMES", "1") != "0":
        out.append(PC_OUT)
    return out


@lru_cache(maxsize=64)
def _read(path: str, mtime: float, size: int) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def read(path: Path) -> dict:
    """JSON of a reference file ({} if unreadable); cached per path and modification."""
    try:
        st = Path(path).stat()
    except OSError:
        return {}
    return _read(str(path), st.st_mtime, st.st_size)


@dataclass(frozen=True)
class Resolved:
    path: Path | None
    label: str                  # game version the file was made for
    origin: str                 # skill | extra
    match: str                  # fingerprint | label | other build | older version | other version | none
    note: str | None = None     # set when the file does not belong to exactly this game data

    @property
    def data(self) -> dict:
        return read(self.path) if self.path else {}


def _label(prefix: str, path: Path, data: dict) -> str:
    return str(data.get("game_version") or path.stem[len(prefix) + 1:])


def resolve(prefix: str, version: str | None, fingerprint: str | None = None,
            folder: Path | None = None, extras: list | None = None) -> Resolved:
    """The best <prefix>-*.json for the game data (version label + fingerprint) - see the docstring.
    `folder` replaces the skill's references/ (tests); `extras` replaces the extra folders."""
    base = Path(folder) if folder else REFS
    places = [(base, "skill")] + [(Path(p), "extra") for p in (extra_folders() if extras is None else extras)]
    ver, fp = str(version or ""), str(fingerprint or "")
    vk = version_key(ver)
    best, best_rank = None, None
    for where, origin in places:
        try:
            files = sorted(where.glob(f"{prefix}-*.json"))
        except OSError:
            continue
        for path in files:
            data = read(path)
            if not data:
                continue
            label, ffp = _label(prefix, path, data), str(data.get("fingerprint") or "")
            own = 0 if origin == "skill" else 1
            if fp and ffp and ffp == fp:
                rank, match = (0, own), "fingerprint"
            elif label == ver and not (fp and ffp):
                rank, match = (1, 1 - own), "label"
            elif label == ver:
                rank, match = (2, 1 - own), "other build"
            else:
                k = version_key(label)
                if vk and k and k <= vk:
                    rank, match = (3, tuple(-x for x in k), own), "older version"
                else:
                    rank, match = (4, tuple(-x for x in (k or ())), own), "other version"
            if best_rank is None or rank < best_rank:
                best, best_rank = (path, label, origin, match), rank
    if best is None:
        return Resolved(None, "", "", "none", None)
    path, label, origin, match = best
    note = None
    if match == "other build":
        note = (f"{path.name}: another build of game version {label} (different game data fingerprint) "
                "- used as it is (ASSUMED)")
    elif match in ("older version", "other version"):
        note = (f"{prefix} of game version {label} used for {ver or '?'} - not read from this game data "
                "(ASSUMED)")
    return Resolved(path, label, origin, match, note)


def index_identity(index) -> tuple[str, str]:
    """(version label, fingerprint) of an index object (GameIndex or a stand-in)."""
    meta = getattr(index, "meta", None) or {}
    return str(meta.get("game_version") or getattr(index, "game_version", "") or ""), str(meta.get("fingerprint") or "")
