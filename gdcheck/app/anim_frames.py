"""Frame count of every .anm animation of the installed game (AP-F4) - PC tool, called by GD-Tools.

    python -m gdcheck.app.anim_frames <Grim Dawn folder> [--out FOLDER] [--version 1.3.0.8]

.anm layout (bealex/GrimDawner, Documentation/GameData.md): 'ANM', a version byte, then how many
bones move, over how many frames, at how many frames per second (30 in the base game); then one
track per bone: a length-prefixed name and 14 floats per frame (translation, quaternion, scale,
second quaternion = 56 bytes). "What follows the tracks is plain text" - CallbackPoint { name =
"RightHandHit" frame = 11 }, CreateEntity, RemoveEntity; a few files end in a blank line.

Validation per file (review 02.10.2026: the .50 check demanded that the file ends right after the
tracks and so rejected every attack animation - they all carry a text trailer):
  - the whole file is read (length = the size in the archive table);
  - the tracks are walked with 56 bytes per frame: every bone name has 1..128 printable characters
    with a letter, and every track ends inside the file - names at exactly the predicted positions
    prove the frame count of the header;
  - what follows must be text (printable, tab, CR, LF; a u32 length prefix is counted apart);
    a binary trailer is rejected;
  - 30 fps and 1..600 frames.
Other strides are only measured for the diagnosis, never used. The unit length of the two
quaternions of frame 0 is reported as a second, independent sign (not a reject reason).

Archives: <game>/resources/*.arc, then gdx1, gdx2, gdx3 (an expansion overrides the base game; if
the expansion copy is rejected, the base value is dropped too - it may be stale).
Animation key = archive name + entry path, lower case, '/' ('creatures/pc/werewolf/anm/...anm') -
the form the animation tables of the game database use.

Writes <out>/anim-frames-<version>.json {"assets": {key: frames}, ...} and on EVERY run
<out>/anim-frames-diagnose.txt (ASCII, bounded) - with it one more run tells layout and naming.
Exit codes: 0 written, 2 not a Grim Dawn folder, 3 nothing usable or the names do not match
(nothing written). The skill reads the JSON from references/ or from ClaudeSkill-Data/out/
(data.animation.frames_table).
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import re
import struct
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from ..data.arz import GDFormatError, LazyArcFile
from . import product

ROOT = Path(__file__).resolve().parents[2]
EXPANSIONS = ("", "gdx1", "gdx2", "gdx3")
MAX_FRAMES = 600
STRIDE = 56                       # 14 floats per frame (GrimDawner)
NAME_MAX = 128
ALT_STRIDES = (28, 32, 36, 40, 44, 48, 52, 60, 64, 72, 80)     # diagnosis only
TEXT = frozenset(range(0x20, 0x7F)) | {9, 10, 13}
USABLE = ("exact", "text trailer", "prefixed text trailer")
CHECK = {"records/creatures/pc/anm_werewolf.dbr|DoubleClaw": 27}      # community value (FoA test)
DIAG_NAME = "anim-frames-diagnose.txt"
DIAG_MAX = 100_000


class AnmError(ValueError):
    def __init__(self, msg: str, starts=()):
        super().__init__(msg)
        self.starts = list(starts)          # track starts found before the walk failed (diagnosis)


def norm(path: str) -> str:
    """One spelling for archive entries and table paths: lower case, '/', no leading './' or '/'."""
    p = path.replace("\\", "/").lower().strip()
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _name_ok(raw: bytes) -> bool:
    raw = raw.rstrip(b"\0")
    return bool(raw) and all(0x20 <= c < 0x7F for c in raw) and any(chr(c).isalpha() for c in raw)


def walk(data: bytes, bones: int, frames: int, stride: int = STRIDE, start: int = 16) -> tuple:
    """(end of the last track, bone names, track starts) - AnmError when a name is not where the
    header says it is."""
    pos, names, starts = start, [], []
    for k in range(bones):
        if pos + 4 > len(data):
            raise AnmError(f"bone {k + 1}: file ends before the name", starts + [pos])
        n = struct.unpack_from("<I", data, pos)[0]
        if not 1 <= n <= NAME_MAX:
            raise AnmError(f"bone {k + 1}: name length {n}", starts + [pos])
        raw = data[pos + 4:pos + 4 + n]
        if len(raw) < n or not _name_ok(raw):
            raise AnmError(f"bone {k + 1}: name not readable", starts + [pos])
        names.append(raw.rstrip(b"\0").decode("ascii"))
        starts.append(pos)
        pos += 4 + n + stride * frames
        if pos > len(data):
            raise AnmError(f"bone {k + 1}: track runs past the end", starts)
    return pos, names, starts


def trailer_kind(t: bytes) -> str:
    if not t:
        return "exact"
    body = t.rstrip(b"\0")
    if all(c in TEXT for c in body):
        return "text trailer"
    if len(t) >= 4 and struct.unpack_from("<I", t, 0)[0] == len(t) - 4 and \
            all(c in TEXT for c in t[4:].rstrip(b"\0")):
        return "prefixed text trailer"
    return "binary trailer"


def _quat_ok(data: bytes, first_track: int, frames: int) -> bool | None:
    if frames < 1 or first_track + 4 > len(data):
        return None
    n = struct.unpack_from("<I", data, first_track)[0]
    p = first_track + 4 + n
    if p + STRIDE > len(data):
        return None
    f = struct.unpack_from("<14f", data, p)
    norms = [math.sqrt(sum(x * x for x in f[a:a + 4])) for a in (3, 10)]
    return all(abs(v - 1.0) < 0.02 for v in norms)


def analyse(data: bytes, size: int) -> dict:
    """Header, walk and trailer of one .anm file. status: one of USABLE, 'binary trailer',
    'walk failed', 'out of range', 'short read', 'not an .anm file', 'implausible header'."""
    out = {"version": None, "bones": None, "frames": None, "fps": None, "status": "", "reason": "",
           "trailer": b"", "names": [], "starts": [], "quat_ok": None, "stride_hint": []}
    if len(data) != size:
        out.update(status="short read", reason=f"read {len(data)} of {size} bytes")
        return out
    if len(data) < 16 or data[:3] != b"ANM":
        out.update(status="not an .anm file", reason=repr(data[:4]))
        return out
    out["version"] = data[3]
    bones, frames, fps = struct.unpack_from("<III", data, 4)
    out.update(bones=bones, frames=frames, fps=fps)
    if not 1 <= bones <= 4096 or frames > 100_000:
        out.update(status="implausible header", reason=f"{bones} bones, {frames} frames")
        return out
    try:
        end, names, starts = walk(data, bones, frames)
    except AnmError as ex:
        out.update(status="walk failed", reason=str(ex), starts=ex.starts)
        for s in ALT_STRIDES:
            try:
                e2, _n, _s = walk(data, bones, frames, s)
            except AnmError:
                continue
            if trailer_kind(data[e2:]) in USABLE:
                out["stride_hint"].append(s)
        return out
    out.update(names=names[:3], starts=starts, trailer=data[end:], quat_ok=_quat_ok(data, 16, frames))
    kind = trailer_kind(data[end:])
    if kind not in USABLE:
        out.update(status=kind, reason=f"{len(data) - end} bytes after the tracks are not text")
        return out
    if fps != 30 or not 1 <= frames <= MAX_FRAMES:
        out.update(status="out of range", reason=f"{frames} frames at {fps} fps")
        return out
    out["status"] = kind
    return out


def parse_header(data: bytes, size: int) -> dict:
    """Compatibility (.47-.50): header fields + 'fits' = usable by the walk above."""
    if len(data) < 16 or data[:3] != b"ANM":
        raise AnmError("not an .anm file")
    a = analyse(data, size)
    if a["status"] == "implausible header":
        raise AnmError(f"implausible counts (version {a['version']}): {a['reason']}")
    return {"version": a["version"], "bones": a["bones"], "frames": a["frames"], "fps": a["fps"],
            "fits": a["status"] in USABLE, "status": a["status"]}


def archives(game: Path) -> list[Path]:
    out = []
    for sub in EXPANSIONS:
        res = (game / sub / "resources") if sub else (game / "resources")
        if res.is_dir():
            out += sorted(p for p in res.glob("*.arc"))
    return out


def scan(game: Path, log=print, wanted: frozenset | set = frozenset()) -> dict:
    """Read every .anm of the installation. `wanted`: table paths (normalized) - their files are
    sampled first for the diagnosis."""
    frames: dict = {}
    by_entry: dict = {}
    status_of: dict = {}                      # key -> status of the copy that counts (later archive wins)
    stats: Counter = Counter()
    per_version: dict = {}
    per_archive: list = []
    bad: list = []
    samples: list = []                        # (is_table, size, key, analysis, head hex, tail hex, boundaries)
    trailers: list = []                       # (is_table, key, trailer text)
    small_rejected: list = []                 # (size, key, data) table files <= 8 KB, rejected
    trailer_sizes: Counter = Counter()
    keywords: Counter = Counter()
    reasons: Counter = Counter()
    hints: Counter = Counter()
    fps_hist: Counter = Counter()
    frames_hist: Counter = Counter()
    quat: Counter = Counter()
    stale: list = []
    for arc_path in archives(game):
        rel = arc_path.relative_to(game).as_posix() if arc_path.is_relative_to(game) else str(arc_path)
        try:
            arc = LazyArcFile(arc_path)
        except (GDFormatError, OSError) as ex:
            log(f"  skipped {rel}: {ex}")
            per_archive.append({"archive": rel, "error": str(ex)})
            continue
        names = list(arc.names())
        anm = sorted(n for n in names if n.lower().endswith(".anm"))
        info = {"archive": rel, "bytes": _size(arc_path), "entries": len(names), "anm": len(anm),
                "first": anm[:3], "status": Counter()}
        per_archive.append(info)
        if not anm:
            continue
        log(f"  {rel}: {len(anm)} animations")
        stem = norm(arc_path.stem)
        for name in anm:
            entry = norm(name)
            key = f"{stem}/{entry}"
            is_table = key in wanted or entry in wanted
            size = arc.size(name) or 0
            try:
                data = arc.read_file(name) or b""
                a = analyse(data, size)
            except Exception as ex:                       # noqa: BLE001 - one broken entry never stops the run
                stats["unreadable"] += 1
                info["status"]["unreadable"] += 1
                bad.append(f"{key}: {type(ex).__name__}: {ex}")
                if key in frames:                         # unreadable expansion copy: base value may be stale
                    stale.append(key)
                    frames.pop(key, None)
                    by_entry.pop(entry, None)
                status_of[key] = "unreadable"
                continue
            st = a["status"]
            info["status"][st] += 1
            v = per_version.setdefault(a["version"], Counter())
            v["files"] += 1
            v[st] += 1
            if st in USABLE:
                v["ok"] += 1
                t = a["trailer"]
                trailer_sizes[_bucket(len(t))] += 1
                for word in _keywords(t):
                    keywords[word] += 1
                if a["quat_ok"] is not None:
                    quat["unit" if a["quat_ok"] else "not unit"] += 1
                if t and (is_table or len(trailers) < 3) and len(trailers) < 6:
                    trailers.append((is_table, key, t[:1000]))
                frames[key] = a["frames"]
                by_entry[entry] = a["frames"]
            else:
                if key in frames:                         # expansion copy rejected: base value may be stale
                    stale.append(key)
                    frames.pop(key, None)
                    by_entry.pop(entry, None)
                bad.append(f"{key}: {st} ({a['reason']})")
                if st == "walk failed":
                    reasons[_generic(a["reason"])] += 1
                    for s in a["stride_hint"]:
                        hints[s] += 1
                if st == "out of range":
                    fps_hist[a["fps"]] += 1
                    frames_hist[_bucket(a["frames"] or 0, (1, 31, 61, 121, 241, 601))] += 1
                if len(samples) < 400:
                    samples.append((is_table, size, key, _strip(a), data[:64].hex(" "), data[-64:].hex(" "),
                                    [data[s:s + 32].hex(" ") for s in a["starts"][:3]]))
                if is_table and size <= 8192:
                    small_rejected.append((size, key, data))
                    small_rejected = sorted(small_rejected)[:2]
            status_of[key] = st
    return {"frames": frames, "by_entry": by_entry, "status_of": status_of, "per_version": per_version,
            "per_archive": per_archive, "problems": bad, "stats": stats, "samples": samples,
            "trailers": trailers, "small_rejected": small_rejected, "trailer_sizes": trailer_sizes,
            "keywords": keywords, "reasons": reasons, "hints": hints, "fps_hist": fps_hist,
            "frames_hist": frames_hist, "quat": quat, "stale": stale}


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return -1


def _bucket(n: int, edges=(1, 64, 256, 1024, 4096)) -> str:
    if n == 0 and edges[0] == 1:
        return "0"
    lo = 0
    for e in edges:
        if n < e:
            return f"{lo}-{e - 1}"
        lo = e
    return f">={lo}"


def _keywords(t: bytes) -> list:
    words = []
    for line in t.decode("ascii", "replace").splitlines():
        w = line.strip().split("{")[0].split(" ")[0].strip()
        if w and w[0].isalpha():
            words.append(w[:40])
    return words


def _generic(reason: str) -> str:
    """'bone 3: name length 1065353216' -> 'bone 3: name length N' (histogram key)."""
    return re.sub(r"\b\d{3,}\b", "N", reason)[:60]


def _strip(a: dict) -> dict:
    return {k: a[k] for k in ("version", "bones", "frames", "fps", "status", "reason", "stride_hint")}


def _prefix_paths(pre: dict) -> set:
    paths = set()
    for key in ("attack", "spell"):
        paths |= {norm(a) for a, _m, _w in pre.get(key) or ()}
    for lst in (pre.get("special") or {}).values():
        paths |= {norm(a) for a, _m, _w in lst}
    return paths


def table_paths(tables: dict) -> set:
    paths = set()
    for t in (tables.get("tables") or {}).values():
        for pre in (t.get("prefixes") or {}).values():
            paths |= _prefix_paths(pre)
    return paths


def monster_paths(combat: dict) -> set:
    """Animations of the bosses and their spawned monsters (boss-combat schema 4 'anim_tables', .54)."""
    paths = set()
    for t in (combat.get("anim_tables") or {}).values():
        for pre in t.values():
            paths |= _prefix_paths(pre)
    return paths


def remap(res: dict, tables: dict, extra=()) -> dict:
    """Frames keyed by the paths the animation tables use. Expected: '<archive>/<entry>'
    ('creatures/pc/...'); if an archive stores its entries with the top folder already in the
    name, the entry name itself matches - both are tried, the table path is the key."""
    out = dict(res["frames"])
    for path in table_paths(tables) | set(extra):
        if path in out:
            continue
        for cand in (path, path.split("/", 1)[-1]):
            if cand in res["by_entry"]:
                out[path] = res["by_entry"][cand]
                break
    return out


def present(res: dict, tables: dict) -> dict:
    """Table path -> status of its file in the archives (absent paths are missing)."""
    status = res.get("status_of") or {}
    by_entry = {k.split("/", 1)[-1]: v for k, v in status.items()}
    by_entry.update({k: v for k, v in status.items()})
    out = {}
    for path in table_paths(tables):
        st = status.get(path) or by_entry.get(path) or by_entry.get(path.split("/", 1)[-1])
        if st:
            out[path] = st
    return out


def coverage(frames: dict, tables: dict) -> tuple[int, int]:
    """(animations of the player tables found in the archives, animations in the tables)."""
    paths = table_paths(tables)
    return len(paths & set(frames)), len(paths)


def archive_signature(res: dict) -> dict:
    """Byte sizes of the Creatures.arc archives (all player animations live there): the build
    signature the skill compares before it trusts its shipped frames for this game (.52)."""
    return {a["archive"]: int(a["bytes"]) for a in res.get("per_archive") or ()
            if "bytes" in a and Path(a["archive"]).name.lower() == "creatures.arc"}


def check_known(frames: dict, tables: dict) -> list[str]:
    """Reader check: the Werewolf basic attack header holds the published 27 keyframes (FoA test).
    It proves the reader, not the playback length (N-1 per Crate, ruleset 'anim_playback')."""
    out = []
    for key, expected in CHECK.items():
        table, anim = key.split("|")
        entries = (((tables.get("tables") or {}).get(table) or {}).get("prefixes") or {}).get(
            "unarmed", {}).get("special", {}).get(anim) or []
        got = sorted({frames.get(norm(a)) for a, _m, _w in entries} - {None})
        if not got:
            out.append(f"{anim}: no frames found for its animations")
        elif got == [expected]:
            out.append(f"{anim}: header {expected} keyframes - matches the published value (reader OK)")
        else:
            out.append(f"{anim}: header {got} keyframes, published {expected} - the values differ "
                       f"(game data or reader changed) - frames kept as read, please report")
    return out


# ----------------------------------------------------------------------------------- diagnosis
def diagnose(game: Path, res: dict | None, tables: dict | None, version: str, args_line: str,
             notes=(), extra=()) -> str:
    """Bounded ASCII report for Claude: per archive and layout, trailers, rejected samples, table
    coverage, DoubleClaw, two small rejected table files as base64 (real regression fixtures)."""
    from .. import VERSION
    L = [f"anim-frames diagnosis - skill {VERSION} - {date.today().isoformat()}",
         f"game folder: {game}", f"call: {args_line}",
         f"game version label: {version}; animation tables: {(tables or {}).get('_version', '-')}", ""]
    L += [f"note: {x}" for x in extra]
    if res is None:
        try:
            top = sorted(p.name for p in game.iterdir())[:40]
        except OSError as ex:
            top = [f"(not readable: {ex})"]
        L += ["folder content: " + ", ".join(top)]
        return _ascii("\n".join(L))
    L.append("ARCHIVES (path, bytes, entries, .anm, first entry names, status counts)")
    for a in res["per_archive"]:
        if "error" in a:
            L.append(f"  {a['archive']}: not readable - {a['error']}")
            continue
        st = ", ".join(f"{k} {v}" for k, v in sorted(a["status"].items()))
        L.append(f"  {a['archive']}: {a['bytes']:,} bytes, {a['entries']} entries, {a['anm']} .anm; "
                 f"{', '.join(ascii(x) for x in a['first']) or '-'}; {st or '-'}")
    L.append("")
    L.append("PER .anm VERSION: " + "; ".join(f"version {v}: " + ", ".join(f"{k} {n}" for k, n in sorted(c.items()))
                                             for v, c in sorted(res["per_version"].items(), key=lambda x: str(x[0]))))
    L.append("TRAILER SIZES (usable files): " + ", ".join(f"{k}: {v}" for k, v in sorted(res["trailer_sizes"].items())))
    L.append("TRAILER KEYWORDS: " + ", ".join(f"{k} {v}" for k, v in res["keywords"].most_common(15)))
    L.append("FRAME 0 QUATERNIONS (unit length = layout sign): " + ", ".join(f"{k} {v}" for k, v in res["quat"].items()))
    L.append("WALK FAILURES: " + ", ".join(f"{k}: {v}" for k, v in res["reasons"].most_common(10)))
    L.append("OTHER STRIDES THAT WOULD WALK: " + ", ".join(f"{k} bytes: {v}" for k, v in sorted(res["hints"].items())))
    L.append("OUT OF RANGE fps: " + ", ".join(f"{k}: {v}" for k, v in sorted(res["fps_hist"].items(), key=str))
             + "; frames: " + ", ".join(f"{k}: {v}" for k, v in sorted(res["frames_hist"].items())))
    if res["stale"]:
        L.append(f"EXPANSION COPY REJECTED, BASE VALUE DROPPED: {len(res['stale'])} (e.g. {res['stale'][:3]})")
    if tables:
        want = table_paths(tables)
        pres = present(res, tables)
        usable = {p for p, s in pres.items() if s in USABLE}
        L.append("")
        L.append(f"TABLE COVERAGE: {len(want)} animations in the tables; {len(pres)} present in the archives; "
                 f"{len(usable)} usable")
        rej = Counter(s for s in pres.values() if s not in USABLE)
        L.append("  present but rejected: " + (", ".join(f"{k} {v}" for k, v in rej.most_common()) or "none"))
        absent = sorted(want - set(pres))
        if absent:
            base = {}
            for k in res["status_of"]:
                base.setdefault(k.rsplit("/", 1)[-1], []).append(k)
            L.append(f"  absent: {len(absent)}, e.g.:")
            for p in absent[:10]:
                cand = base.get(p.rsplit("/", 1)[-1], [])[:3]
                L.append(f"    {ascii(p)} -> same file name in the archives: {cand or 'none'}")
        ww = ((tables.get("tables") or {}).get("records/creatures/pc/anm_werewolf.dbr") or {}).get("prefixes") or {}
        dc = list(dict.fromkeys(norm(a) for a, _m, _w in ww.get("unarmed", {}).get("special", {}).get("DoubleClaw", [])))
        L.append("  DoubleClaw files: " + "; ".join(f"{p} -> {pres.get(p, 'absent')}" for p in dc))
    L += [f"check: {n}" for n in notes]
    L.append("")
    L.append("TRAILER SAMPLES (first 1000 characters)")
    for is_table, key, t in res["trailers"]:
        L.append(f"  {'[table] ' if is_table else ''}{key}: {ascii(t.decode('ascii', 'replace'))}")
    L.append("")
    L.append("REJECTED SAMPLES (table files first, smallest first)")
    for is_table, size, key, a, head, tail, bounds in sorted(res["samples"], key=lambda s: (not s[0], s[1]))[:6]:
        L.append(f"  {'[table] ' if is_table else ''}{key}: {size} bytes, {a}")
        L.append(f"    first 64 bytes: {head}")
        for i, b in enumerate(bounds):
            L.append(f"    track {i + 1} start: {b}")
        L.append(f"    last 64 bytes: {tail}")
    L.append("")
    L.append("SMALL REJECTED TABLE FILES (base64, for regression tests)")
    for size, key, data in res["small_rejected"]:
        L.append(f"  {key} ({size} bytes):")
        b64 = base64.b64encode(data).decode("ascii")
        L += [f"    {b64[i:i + 100]}" for i in range(0, len(b64), 100)]
    L.append("")
    L.append("FIRST PROBLEMS")
    L += [f"  {ascii(p)}" for p in res["problems"][:40]]
    return _ascii("\n".join(L))


def _ascii(text: str) -> str:
    text = text.encode("ascii", "replace").decode("ascii")
    if len(text) > DIAG_MAX:
        text = text[:DIAG_MAX] + f"\n... cut at {DIAG_MAX:,} characters"
    return text + "\n"


def _write_diag(out_dir: Path, text: str) -> Path | None:
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / DIAG_NAME
        p.write_text(text, encoding="ascii", errors="replace")
        return p
    except OSError as ex:
        print(f"  (diagnosis not written: {ex})", file=sys.stderr)
        return None


def main(argv=None) -> int:
    from .. import VERSION
    ap = argparse.ArgumentParser(prog="python -m gdcheck.app.anim_frames", description=__doc__.split("\n")[0])
    ap.add_argument("game", help="Grim Dawn installation folder")
    ap.add_argument("--out", help="output folder (default: ClaudeSkill-Data/out next to the skill - never the "
                                  "shipped references/, .52)")
    ap.add_argument("--version", help="game version label (default: newest animation tables)")
    ap.add_argument("--tables", help="anim-tables JSON to check the names against (default: references/)")
    a = ap.parse_args(argv)
    game = Path(a.game)
    from ..data import animation as AD
    out_dir = Path(a.out) if a.out else AD.PC_OUT
    line = " ".join(argv if argv is not None else sys.argv[1:])
    if not (game / "resources").is_dir():
        _write_diag(out_dir, diagnose(game, None, None, a.version or "-", line,
                                      extra=["no resources folder - not a Grim Dawn installation folder"]))
        print(f"Not a Grim Dawn folder (no resources\\): {game}\n  What you can do: give the installation "
              "folder, e.g. C:\\Steam\\steamapps\\common\\Grim Dawn.", file=sys.stderr)
        return 2
    if a.tables:
        tab = json.loads(Path(a.tables).read_text(encoding="utf-8"))
        tab.setdefault("_version", tab.get("game_version"))
    else:
        tab = AD.tables(a.version or "1.3.0.8")
    ver = a.version or tab.get("_version") or "1.3.0.8"
    from ..data import bosses as BD
    mon = monster_paths(BD.combat_meta(AD.ROOT, ver))       # boss animations (.54); {} without a boss file
    wanted = frozenset(table_paths(tab))
    print(f"Reading .anm files from {game} ...")
    res = scan(game, wanted=wanted)
    for v, c in sorted(res["per_version"].items(), key=lambda x: str(x[0])):
        rej = {k: n for k, n in c.items() if k not in ("files", "ok") and k not in USABLE}
        print(f"  .anm version {v}: {c['ok']} of {c['files']} files used"
              + (f" ({c['text trailer']} with event text after the tracks)" if c["text trailer"] else "")
              + ("".join(f", {n} {k}" for k, n in sorted(rej.items()))))
    res["frames"] = remap(res, tab, mon)
    notes = check_known(res["frames"], tab)
    for n in notes:
        print("  check: " + n)
    found, want_n = coverage(res["frames"], tab)
    pres = present(res, tab)
    print(f"  {found} of {want_n} animations of the player tables read ({len(pres)} present in the archives)")
    m_found = len(mon & set(res["frames"]))
    if mon:
        print(f"  {m_found} of {len(mon)} animations of the bosses and their spawned monsters read")
    diag = _write_diag(out_dir, diagnose(game, res, tab, ver, line, notes))
    where = f" ({diag})" if diag else ""
    if not res["frames"]:
        print(f"No animation file of your game could be read with the known layout. Nothing written - the "
              f"check keeps community/default frames. Details: {DIAG_NAME}{where}", file=sys.stderr)
        return 3
    if want_n and len(pres) < 0.5 * want_n:
        print(f"The animation names of the game database were not found in the archives ({len(pres)} of "
              f"{want_n}). Nothing written. Details: {DIAG_NAME}{where}", file=sys.stderr)
        return 3
    if want_n and found == 0:                 # review .51: never a green "0 of 773 read"
        print(f"None of the {want_n} player animations could be read (the files are there, their layout is not "
              f"the known one). Nothing written. Details: {DIAG_NAME}{where}", file=sys.stderr)
        return 3
    # the reader is checked against the published header of the Werewolf basic attack
    trusted = not any(("differ" in n) or ("no frames" in n) for n in notes)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"anim-frames-{ver}.json"
    base: dict = {}                           # community/defaults live in the shipped references only
    payload = {
        "game_version": ver, "schema": 2, "frames_per_second": 30, "skill_version": VERSION,
        "assets_version": ver, "archives": archive_signature(res),
        "source": f".anm files of the installed game ({game.name}), read {date.today().isoformat()} "
                  "by gdcheck.app.anim_frames",
        "assets": dict(sorted(res["frames"].items())),
        "assets_confidence": "EXACT" if trusted else "ASSUMED",
        "coverage": [found, want_n], "coverage_monsters": [m_found, len(mon)], "present": len(pres),
        "layout": {str(v): dict(c) for v, c in res["per_version"].items()},
        "checks": notes,
        "problems": res["problems"][:50],
        "community": base.get("community", []), "defaults": base.get("defaults", {})}
    target.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"Written {target} ({len(res['frames'])} animations)")
    if want_n and found < 0.9 * want_n:
        print(low_coverage_note(found, want_n, where))
    return 0


def low_coverage_note(found: int, want_n: int, where: str) -> str:
    """The NOTE after a run that read fewer than 90 % of the player animations."""
    send = (f"open an issue at {product.ISSUES_URL} and attach {DIAG_NAME}{where}" if product.enabled()
            else f"send {DIAG_NAME}{where} to Claude")
    return (f"NOTE: only {found} of {want_n} player animations could be read - the rest keeps community/"
            f"default frames. Please {send}.")


if __name__ == "__main__":
    from . import errors
    raise SystemExit(errors.run(main, "animation frames"))
