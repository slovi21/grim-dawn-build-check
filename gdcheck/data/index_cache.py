"""Load cache for the game data index: marshal bytes, keyed by index hash and
Python version (marshal is version specific). The big, rarely needed parts
(loot tables for rerolls, raw boss records) are separate sections that are
only unmarshalled on first use. Pure bytes in/out - the app layer decides
where the cache file lives."""
from __future__ import annotations

import hashlib
import marshal
import sys
from pathlib import Path

MAGIC = b"GDIX2"
LAZY = ("loot_tabellen", "bosse")


def key(index_path: Path) -> str:
    h = hashlib.sha1()
    with open(index_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return f"{h.hexdigest()}-py{sys.version_info[0]}{sys.version_info[1]}"


def file_name(index_path: Path) -> str:
    return f"{Path(index_path).name}.py{sys.version_info[0]}{sys.version_info[1]}.cache"


def dumps(data: dict, k: str) -> bytes:
    core = {kk: vv for kk, vv in data.items() if kk not in LAZY}
    parts = [marshal.dumps(core)] + [marshal.dumps(data.get(name) or {}) for name in LAZY]
    kb = k.encode("ascii")
    out = bytearray(MAGIC + len(kb).to_bytes(2, "little") + kb)
    for p in parts:
        out += len(p).to_bytes(8, "little") + p
    return bytes(out)


def loads(blob: bytes, k: str):
    """(core dict, {name: loader}) or None if the cache is foreign, stale or corrupt."""
    try:
        if not blob.startswith(MAGIC):
            return None
        n = int.from_bytes(blob[5:7], "little")
        if blob[7:7 + n].decode("ascii") != k:
            return None
        pos, chunks = 7 + n, []
        for _ in range(1 + len(LAZY)):
            ln = int.from_bytes(blob[pos:pos + 8], "little")
            pos += 8
            if ln <= 0 or pos + ln > len(blob):
                return None
            chunks.append((pos, ln))
            pos += ln
        mv = memoryview(blob)
        core = marshal.loads(mv[chunks[0][0]:chunks[0][0] + chunks[0][1]])
        if not isinstance(core, dict) or "records" not in core:
            return None
        loaders = {name: (lambda a=a, b=b: marshal.loads(mv[a:a + b]))
                   for name, (a, b) in zip(LAZY, chunks[1:])}
        return core, loaders
    except (ValueError, EOFError, TypeError, UnicodeDecodeError):
        return None
