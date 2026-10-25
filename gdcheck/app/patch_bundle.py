"""Patch bundle: the new game data of the player's PC for Claude (.53, patch readiness).

    python -m gdcheck.app.patch_bundle build  --index <gd-index-*.json.gz> --data <folder> --out <zip>
                                              [--label-source <text>] [--label-confirmed 0|1]
                                              [--buildid <n>] [--branch <name>]
    python -m gdcheck.app.patch_bundle verify <zip>
    python -m gdcheck.app.patch_bundle unpack <zip> <folder>     # verify, then extract (for GDCHECK_DATA_DIR)

The bundle holds what the skill needs for a new game version and nothing personal: the index (stored
as it is, no second compression), the reference files of that game data (anim-tables, boss-combat,
player-skills, anim-frames, design-data (.67) - each must carry the index fingerprint) and manifest.json (bundle format,
skill version, version label and where it came from, Steam build id and branch, fingerprint, index
schema and language, .arz list, per file size/sha256/fingerprint). Never in a bundle: saves, logs,
character names, absolute paths (the frames diagnosis names the game folder - it stays on the PC).

verify (Claude runs it before using a bundle): file names from the allowlist only, no folders, no '..'
or drive letters, size caps, sha256 of every file, index schema >= 9 (the one of index_build), one fingerprint everywhere, all
four .arz databases and the EN text; a bundle from a Steam beta/playtest branch is usable in a chat
but never goes into the skill package (shippable = False). Exit codes: 0 ok, 3 not usable.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

from .index_build import INDEX_SCHEMA as MIN_INDEX_SCHEMA      # strict since .70: an older index cannot be loaded

FORMAT = 1
MAX_FILE = 40 * 1024 * 1024
MAX_TOTAL = 60 * 1024 * 1024
REF_PREFIXES = ("anim-tables", "boss-combat", "player-skills", "anim-frames", "design-data")
NAME_RE = re.compile(r"(gd-index-[A-Za-z0-9._+-]+\.json\.gz|(%s)-[A-Za-z0-9._+-]+\.json|manifest\.json)"
                     % "|".join(REF_PREFIXES))
ARZ_NEEDED = ("database.arz", "GDX1.arz", "GDX2.arz", "GDX3.arz")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _meta_from_head(raw: str) -> dict:
    start = raw.find('"_meta":')
    if start < 0:
        return {}
    pos = start + len('"_meta":')
    while pos < len(raw) and raw[pos].isspace():
        pos += 1
    meta, _ = json.JSONDecoder().raw_decode(raw, pos)
    return meta if isinstance(meta, dict) else {}


def index_meta(path: Path) -> dict:
    """The _meta block of an index without unpacking all of it."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return _meta_from_head(fh.read(16384))


def _meta_of(data: bytes) -> dict:
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as fh:
        return _meta_from_head(fh.read(16384).decode("utf-8", "replace"))


def _pick(data: Path, prefix: str, ver: str, fp: str) -> tuple[dict | None, str]:
    """The reference file of exactly this game data: by fingerprint (the label may be stale - review
    .53), the index's label first; the frames carry no index fingerprint - they must be read for this
    label (assets_version)."""
    cands = sorted(data.glob(f"{prefix}-*.json"), key=lambda p: (p.name != f"{prefix}-{ver}.json", p.name))
    for p in cands:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        if prefix == "anim-frames":
            if p.name == f"{prefix}-{ver}.json" and str(d.get("assets_version") or d.get("game_version") or "") == ver:
                return d, "ok"
            continue
        if fp and str(d.get("fingerprint") or "") == fp:
            return d, "ok" if p.name == f"{prefix}-{ver}.json" else f"read as {p.name}"
    return None, "missing for this game data"


def build(index: Path, data: Path, out: Path, label_source: str = "", label_confirmed: bool | None = None,
          buildid: str = "", branch: str = "") -> dict:
    """Write the bundle; returns the manifest. Reference files with another fingerprint are left out
    (they belong to other game data)."""
    from .. import VERSION
    meta = index_meta(index)
    ver, fp = str(meta.get("game_version") or "?"), str(meta.get("fingerprint") or "")
    files, status = {index.name: index.read_bytes()}, {}
    for prefix in REF_PREFIXES:
        d, why = _pick(Path(data), prefix, ver, fp)
        if d is None:
            status[prefix] = why
            continue
        # one name and label per bundle: the index's (a file read under a stale label is renamed)
        d["fingerprint"], d["game_version"] = fp, ver
        if prefix == "anim-frames":
            d["assets_version"] = ver
        files[f"{prefix}-{ver}.json"] = json.dumps(d, indent=1, sort_keys=prefix != "anim-frames").encode("utf-8")
        status[prefix] = "ok" if why == "ok" else f"ok ({why})"
    manifest = {
        "format": FORMAT, "skill_version": VERSION, "game_version": ver,
        "label_source": label_source, "label_confirmed": label_confirmed,
        "steam_buildid": buildid, "branch": branch or "public",
        "fingerprint": fp, "index_schema": meta.get("schema"), "language": meta.get("sprache"),
        "arz": [q.get("datei") for q in meta.get("quellen") or () if isinstance(q, dict)],
        "status": status,
        "files": {n: {"size": len(b), "sha256": _sha(b),
                      "fingerprint": fp} for n, b in sorted(files.items())},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with zipfile.ZipFile(tmp, "w") as z:
        for n, b in sorted(files.items()):
            z.writestr(n, b, compress_type=zipfile.ZIP_STORED if n.endswith(".gz") else zipfile.ZIP_DEFLATED)
        z.writestr("manifest.json", json.dumps(manifest, indent=1), compress_type=zipfile.ZIP_DEFLATED)
    tmp.replace(out)
    return manifest


def verify(path: Path) -> tuple[bool, bool, list, dict]:
    """(usable, shippable, problems, manifest)."""
    problems: list = []
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as ex:
        return False, False, [f"not a zip file: {ex}"], {}
    with z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        for i in infos:
            n = i.filename
            if "/" in n or "\\" in n or ".." in n or ":" in n or not NAME_RE.fullmatch(n):
                problems.append(f"file not allowed in a bundle: {n!r}")
            if i.file_size > MAX_FILE:
                problems.append(f"{n}: {i.file_size:,} bytes - over the cap")
        if sum(i.file_size for i in infos) > MAX_TOTAL:
            problems.append("bundle larger than allowed")
        if "manifest.json" not in names:
            return False, False, problems + ["manifest.json missing"], {}
        if problems:
            return False, False, problems, {}
        try:
            man = json.loads(z.read("manifest.json").decode("utf-8"))
        except (ValueError, zipfile.BadZipFile, OSError):
            return False, False, ["manifest.json unreadable"], {}
        if not isinstance(man, dict):
            return False, False, ["manifest.json is not an object"], {}
        fp = str(man.get("fingerprint") or "")
        if man.get("format") != FORMAT:
            problems.append(f"bundle format {man.get('format')} unknown")
        if not fp:
            problems.append("no fingerprint in the manifest")
        listed = man.get("files") or {}
        for n in names:
            if n == "manifest.json":
                continue
            if n not in listed:
                problems.append(f"{n}: not in the manifest")
                continue
            try:
                data = z.read(n)
            except (zipfile.BadZipFile, OSError, ValueError) as ex:
                problems.append(f"{n}: not readable ({ex})")
                continue
            if not isinstance(listed[n], dict) or _sha(data) != listed[n].get("sha256") \
                    or len(data) != listed[n].get("size"):
                problems.append(f"{n}: checksum or size differs from the manifest")
                continue
            if n.startswith("gd-index-"):
                try:
                    meta = _meta_of(data)
                except (OSError, ValueError, EOFError):
                    problems.append(f"{n}: not a readable index")
                    continue
                if str(meta.get("fingerprint") or "") != fp:
                    problems.append(f"{n}: index fingerprint differs from the manifest")
                try:
                    schema = int(meta.get("schema") or 0)
                except (TypeError, ValueError):
                    schema = 0
                if schema < MIN_INDEX_SCHEMA:
                    problems.append(f"{n}: index schema {meta.get('schema')} - at least {MIN_INDEX_SCHEMA} needed")
                if str(meta.get("game_version") or "") != str(man.get("game_version") or ""):
                    problems.append(f"{n}: index version differs from the manifest")
                idx_arz = [q.get("datei") for q in meta.get("quellen") or () if isinstance(q, dict)]
                if idx_arz != list(man.get("arz") or []) or str(meta.get("sprache") or "") != str(man.get("language") or ""):
                    problems.append(f"{n}: databases or language differ from the manifest")
            else:
                try:
                    d = json.loads(data.decode("utf-8"))
                except ValueError:
                    problems.append(f"{n}: not readable JSON")
                    continue
                if str(d.get("fingerprint") or "") != fp:
                    problems.append(f"{n}: belongs to other game data (fingerprint)")
        if not any(n.startswith("gd-index-") for n in names):
            problems.append("no game data index in the bundle")
    usable = not problems
    notes = []
    arz = set(man.get("arz") or ())
    if not set(ARZ_NEEDED) <= arz:
        notes.append(f"game databases missing ({', '.join(sorted(set(ARZ_NEEDED) - arz))}) - an installation "
                     "without all expansions: use it for this player's chat only")
    if str(man.get("language") or "EN").upper() != "EN":
        notes.append(f"index language {man.get('language')} - the skill ships EN")
    if str(man.get("branch") or "public") not in ("public", ""):
        notes.append(f"Steam branch '{man.get('branch')}' (playtest/beta) - never into the skill package")
    incomplete = {k: v for k, v in (man.get("status") or {}).items() if not str(v).startswith("ok")}
    if incomplete:
        notes.append("reference data missing (" + ", ".join(f"{k}: {v}" for k, v in sorted(incomplete.items()))
                     + ") - the checks of this game data would mark them ASSUMED")
    if man.get("label_confirmed") is False:
        notes.append("version label not confirmed (the game had not run since the update) - ask the player "
                     "for the version shown in the game's main menu")
    shippable = usable and not notes
    return usable, shippable, problems + notes, man


def unpack(path: Path, folder: Path) -> tuple[bool, list]:
    usable, _ship, problems, man = verify(path)
    if not usable:
        return False, problems
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            (folder / n).write_bytes(z.read(n))
    return True, problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gdcheck.app.patch_bundle", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--index", required=True)
    b.add_argument("--data", required=True, help="folder with the reference files of this game data")
    b.add_argument("--out", required=True)
    b.add_argument("--label-source", default="")
    b.add_argument("--label-confirmed", choices=("0", "1"))
    b.add_argument("--buildid", default="")
    b.add_argument("--branch", default="")
    v = sub.add_parser("verify")
    v.add_argument("zip")
    u = sub.add_parser("unpack")
    u.add_argument("zip")
    u.add_argument("folder")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        man = build(Path(a.index), Path(a.data), Path(a.out), a.label_source,
                    None if a.label_confirmed is None else a.label_confirmed == "1", a.buildid, a.branch)
        size = Path(a.out).stat().st_size
        print(f"Patch bundle written: {a.out} ({size / 1048576:.1f} MB, game data {man['game_version']}, "
              f"{', '.join(f'{k} {v}' for k, v in man['status'].items())})")
        return 0
    if a.cmd == "verify":
        usable, shippable, problems, man = verify(Path(a.zip))
        print(f"bundle {a.zip}: {'usable' if usable else 'NOT usable'}"
              f"{', may go into the skill package' if shippable else ''}; game data {man.get('game_version', '?')}, "
              f"fingerprint {man.get('fingerprint', '?')}")
        for p in problems:
            print(f"  - {p}")
        return 0 if usable else 3
    ok, problems = unpack(Path(a.zip), Path(a.folder))
    for p in problems:
        print(f"  - {p}")
    print(f"unpacked into {a.folder} - set GDCHECK_DATA_DIR={a.folder} and --index {a.folder}/gd-index-*.json.gz"
          if ok else "not unpacked")
    return 0 if ok else 3


if __name__ == "__main__":
    from . import errors
    raise SystemExit(errors.run(main, "patch bundle"))
