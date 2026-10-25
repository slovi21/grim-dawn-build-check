"""Fingerprint of the installed game data (the identity of a game data index).

Same value as build_index.db_fingerprint of the .35 tools (the index stores it in its _meta):
sha1 over name, size and the first/last 64 KB of every .arz database and every Text_EN.arc,
sorted by file name, first 16 hex digits. Only reads; a patch changes it, a reinstall or a new
file time does not.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK = 65536
EXPANSIONS = (1, 2, 3, 4)


def arz_paths(game_dir) -> list[Path]:
    """All .arz databases in load order: base first, then the expansions."""
    gd = Path(game_dir)
    out = []
    base = gd / "database" / "database.arz"
    if base.exists():
        out.append(base)
    for i in EXPANSIONS:
        p = gd / f"gdx{i}" / "database" / f"GDX{i}.arz"
        if p.exists():
            out.append(p)
    return out


def arc_paths(game_dir, lang: str = "EN") -> list[Path]:
    """Text archives of one language in the same order."""
    gd = Path(game_dir)
    out = []
    base = gd / "resources" / f"Text_{lang}.arc"
    if base.exists():
        out.append(base)
    for i in EXPANSIONS:
        p = gd / f"gdx{i}" / "resources" / f"Text_{lang}.arc"
        if p.exists():
            out.append(p)
    return out


def db_fingerprint(game_dir) -> str:
    h = hashlib.sha1()
    files = sorted(arz_paths(game_dir) + arc_paths(game_dir, "EN"), key=lambda p: p.name.lower())
    for p in files:
        try:
            size = p.stat().st_size
            with open(p, "rb") as fh:
                head = fh.read(CHUNK)
                fh.seek(max(0, size - CHUNK))
                tail = fh.read(CHUNK)
        except OSError:
            continue
        h.update(p.name.lower().encode())
        h.update(str(size).encode())
        h.update(head)
        h.update(tail)
    return h.hexdigest()[:16]
