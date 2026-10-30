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
or drive letters, size caps, sha256 of every file, index schema >= 9 (the one the engine loads, data.index.SUPPORTED_SCHEMA), one fingerprint everywhere, all
four .arz databases and the EN text; a bundle from a Steam beta/playtest branch is usable in a chat
but never goes into the skill package (shippable = False). Exit codes: 0 ok, 3 not usable.

Web variant (online check, B2; tools/web_data.py): build(web=True) writes bundle format 2 - the same file layout and the
same checks in verify, but the index without the loot tables and with trimmed tags (web_index), the reference files
anim-tables, boss-combat, player-skills, anim-frames and vendor-ranks, and a manifest with web, dropped and tags_kept.
Normal bundles stay format 1 without vendor-ranks, so an older skill can still read every bundle it could read before
(E6). verify marks a web bundle as never shippable: it is not complete enough for the skill package.
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

from ..data.index import SUPPORTED_SCHEMA as MIN_INDEX_SCHEMA   # strict since .70: an older index cannot be loaded; not index_build's
                                                                # INDEX_SCHEMA, so the web entry (B1) never loads the .arz reader
from ..data.text import clean_text

FORMAT = 1                  # every normal bundle: byte for byte what it was before the web variant (E6)
WEB_FORMAT = 2              # the web variant of the online check (build(web=True)) and nothing else
ACCEPTED_FORMATS = (FORMAT, WEB_FORMAT)
MAX_FILE = 40 * 1024 * 1024
MAX_TOTAL = 60 * 1024 * 1024
REF_PREFIXES = ("anim-tables", "boss-combat", "player-skills", "anim-frames", "design-data")
WEB_PREFIXES = ("anim-tables", "boss-combat", "player-skills", "anim-frames", "vendor-ranks")    # design-data: not read online
NAME_RE = re.compile(r"(gd-index-[A-Za-z0-9._+-]+\.json\.gz|(%s)-[A-Za-z0-9._+-]+\.json|manifest\.json)"
                     % "|".join(REF_PREFIXES + ("vendor-ranks",)))
ARZ_NEEDED = ("database.arz", "GDX1.arz", "GDX2.arz", "GDX3.arz")

# -- the web variant (spec 4.10): what the browser does not need stays out ---------------------------------------------
WEB_DROP = ("loot_tabellen",)       # only the rerolls read the loot tables (analysis/generators/rerolls.py); online they are off
_TAG_SECTIONS = ("records", "controller", "sternbilder", "bosse", "tabellen")      # where a tag key can be a string value
# Tags the engine looks up with a key it builds itself (not a string stored in the data), found by the inventory of
# every index.tag()/db.tag() call of gdcheck (online-check B2, step 1). The other call sites need no prefix:
# index.py:154 / game_names.py:69 and :100 / bosses.py:110 / devotions.py:208 read keys stored in records, controller or
# sternbilder (rule a); benchmarks.py:35 reads boss-combat's name_tag (build passes the reference files' strings, rule
# a); save_facts.py:511 asks with a record's field name (rule b); describe.py:76 turns monster tags of the save into
# text for the save report, which the online check does not make. Everything else goes.
TAG_KEEP_PREFIXES = (
    "tagSkillClassName",    # the class name of the save: domain/character.py:153 looks up the header's player_class_tag
                            # (tagSkillClassName0104 = Blademaster, ...); the key comes from the save, not from a record
    "tagFaction",           # data/index.py:176 and data/game_names.py:103: "tagFaction" + the slot's name (Survivors, User2 ...)
)
# The fallback if the exhaustive comparison (tests/integration/test_web_index_equal.py) finds a difference that a keep
# rule cannot fix: drop only these prefixes (quest, dialog, lore, notes) and keep everything else. Empty = not in use.
TAG_DENY_PREFIXES: tuple = ()


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


def _strings(obj, out: set, keys: bool = False) -> set:
    """Every string value in a parsed JSON (dict keys too with keys=True), collected into `out`."""
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, str):
            out.add(o)
        elif isinstance(o, dict):
            if keys:
                out.update(k for k in o if isinstance(k, str))
            stack.extend(o.values())
        elif isinstance(o, (list, tuple)):
            stack.extend(o)
    return out


def web_index(data: dict, keep=(), deny=None) -> dict:
    """The index of the web variant: without WEB_DROP and with the tags the engine can still ask for (spec 4.10).
    A tag stays if
      (a) its key is a string value in records, controller, sternbilder, bosse or tabellen, or is in `keep` (build
          passes the strings of the reference files: boss-combat names the benchmarks' name tags),
      (b) its key is a field name of a record (save_facts.stat_text looks the field up as a tag),
      (c) its key starts with one of TAG_KEEP_PREFIXES (keys the engine builds itself),
      (d) its cleaned text is a record's name: bosses._game_texts decides from the set of all tag texts whether an index
          name is a game text - a record name that is one must stay one.
    With `deny` (default TAG_DENY_PREFIXES, normally empty) the rule turns around: only the tags with a listed prefix
    can go, and still only if none of (a)-(d) keeps them. `_meta.tags_kept` is the number of tags left; the input is not
    changed."""
    deny = TAG_DENY_PREFIXES if deny is None else tuple(deny)
    tags = data.get("tags") or {}
    named = set(keep)
    for section in _TAG_SECTIONS:
        _strings(data.get(section), named)
    names = set()
    for rec in (data.get("records") or {}).values():
        named.update(rec.get("m") or ())                                            # (b)
        named.update(rec.get("f") or ())
        if rec.get("n"):
            names.add(rec["n"])
    kept = {}
    for k, v in tags.items():
        if k in named or k.startswith(TAG_KEEP_PREFIXES) or clean_text(v) in names:
            kept[k] = v
        elif deny and not k.startswith(deny):
            kept[k] = v
    out = {k: v for k, v in data.items() if k not in WEB_DROP}
    out["tags"] = kept
    out["_meta"] = {**(data.get("_meta") or {}), "tags_kept": len(kept)}
    return out


def _web_refusal(meta: dict, ver: str, label_confirmed, branch: str) -> str:
    """Why this game data must not go online ("" = fine): the web variant is public, so it takes only what the skill
    package takes - the public Steam branch, a confirmed version label, all four databases, the EN text."""
    if branch != "public":
        return (f"Steam branch '{branch}' (playtest/beta) - the web variant is built from the public branch only"
                if branch else "Steam branch not given - say --branch public (the web variant is built from the public branch only)")
    if label_confirmed is not True:
        return "version label not confirmed (the game had not run since the update)"
    arz = {q.get("datei") for q in meta.get("quellen") or () if isinstance(q, dict)}
    if not set(ARZ_NEEDED) <= arz:
        return f"game databases missing ({', '.join(sorted(set(ARZ_NEEDED) - arz))})"
    if str(meta.get("sprache") or "") != "EN":
        return f"index language {meta.get('sprache')} - the web variant ships EN"
    if int(meta.get("schema") or 0) < MIN_INDEX_SCHEMA:
        return f"index schema {meta.get('schema')} - at least {MIN_INDEX_SCHEMA} needed"
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", ver) or not meta.get("fingerprint"):
        return "no game version or fingerprint in the index"
    return ""


def _web_index_file(index: Path, named: set) -> tuple[bytes, int]:
    """(gzip of the trimmed index, tags kept). Compact JSON as index_build.encode writes it; no time stamp in the gzip
    header, so the same input gives the same bytes."""
    trimmed = web_index(json.loads(gzip.decompress(index.read_bytes())), keep=named)
    raw = json.dumps(trimmed, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return gzip.compress(raw, 9, mtime=0), trimmed["_meta"]["tags_kept"]


def build(index: Path, data: Path, out: Path, label_source: str = "", label_confirmed: bool | None = None,
          buildid: str = "", branch: str = "", web: bool = False) -> dict:
    """Write the bundle; returns the manifest. Reference files with another fingerprint are left out
    (they belong to other game data).

    web=True writes the web variant of the online check instead (format 2, E6): the trimmed index (web_index),
    WEB_PREFIXES, and a manifest with web, dropped and tags_kept. It is public, so it raises ValueError (nothing is
    written) unless the game data could go into the skill package: public branch, confirmed label, all four databases,
    EN, and every reference file of WEB_PREFIXES present for these game data."""
    from .. import VERSION
    meta = index_meta(index)
    ver, fp = str(meta.get("game_version") or "?"), str(meta.get("fingerprint") or "")
    if web:
        why = _web_refusal(meta, ver, label_confirmed, branch)
        if why:
            raise ValueError(f"not for the web: {why}")
    files, status, named = ({} if web else {index.name: index.read_bytes()}), {}, set()
    for prefix in WEB_PREFIXES if web else REF_PREFIXES:
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
        if web:
            _strings(d, named, keys=True)
    manifest = {
        "format": WEB_FORMAT if web else FORMAT, "skill_version": VERSION, "game_version": ver,
        "label_source": label_source, "label_confirmed": label_confirmed,
        "steam_buildid": buildid, "branch": branch or "public",
        "fingerprint": fp, "index_schema": meta.get("schema"), "language": meta.get("sprache"),
        "arz": [q.get("datei") for q in meta.get("quellen") or () if isinstance(q, dict)],
        "status": status,
    }
    if web:
        incomplete = {k: v for k, v in status.items() if not v.startswith("ok")}
        if incomplete:
            raise ValueError("not for the web: reference data missing ("
                             + ", ".join(f"{k}: {v}" for k, v in sorted(incomplete.items())) + ")")
        files[index.name], kept = _web_index_file(index, named)
        manifest.update(web=True, dropped=list(WEB_DROP), tags_kept=kept)
    manifest["files"] = {n: {"size": len(b), "sha256": _sha(b),
                             "fingerprint": fp} for n, b in sorted(files.items())}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with zipfile.ZipFile(tmp, "w") as z:
        for n, b in sorted(files.items()):
            # the web variant carries no time stamps: the same input gives the same zip (its name is its hash)
            member = zipfile.ZipInfo(n, (1980, 1, 1, 0, 0, 0)) if web else n
            z.writestr(member, b, compress_type=zipfile.ZIP_STORED if n.endswith(".gz") else zipfile.ZIP_DEFLATED)
        z.writestr(zipfile.ZipInfo("manifest.json", (1980, 1, 1, 0, 0, 0)) if web else "manifest.json",
                   json.dumps(manifest, indent=1), compress_type=zipfile.ZIP_DEFLATED)
    tmp.replace(out)
    return manifest


def schema_problem(name: str, schema) -> str:
    """The one line verify writes for an index of a schema the engine cannot load. The only place that words it."""
    return f"{name}: index schema {schema} - at least {MIN_INDEX_SCHEMA} needed"


def schema_only(problems: list, man: dict) -> bool:
    """True if `problems` (from check, not verify: no notes) are exactly the schema line of the manifest's index file:
    whole data of another schema. Compared as a whole, never searched for in text (a file name could imitate it)."""
    return problems == [schema_problem(f"gd-index-{man.get('game_version')}.json.gz", man.get("index_schema"))]


def verify(path: Path) -> tuple[bool, bool, list, dict]:
    """(usable, shippable, problems, manifest)."""
    problems, man = check(path)
    return _judge(problems, man)


def check(path: Path) -> tuple[list, dict]:
    """(problems, manifest): what is wrong with the bundle itself, without the notes verify adds."""
    problems: list = []
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as ex:
        return [f"not a zip file: {ex}"], {}
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
            return problems + ["manifest.json missing"], {}
        if problems:
            return problems, {}
        try:
            man = json.loads(z.read("manifest.json").decode("utf-8"))
        except (ValueError, zipfile.BadZipFile, OSError):
            return ["manifest.json unreadable"], {}
        if not isinstance(man, dict):
            return ["manifest.json is not an object"], {}
        fp = str(man.get("fingerprint") or "")
        if man.get("format") not in ACCEPTED_FORMATS:
            problems.append(f"bundle format {man.get('format')} unknown")
        elif man.get("format") == WEB_FORMAT and man.get("web") is not True:
            problems.append(f"bundle format {WEB_FORMAT} is the web variant - it needs web: true in the manifest")
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
                    problems.append(schema_problem(n, meta.get("schema")))
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
    return problems, man


def _judge(problems: list, man: dict) -> tuple[bool, bool, list, dict]:
    """(usable, shippable, problems + notes, manifest) of what check found."""
    if not man:
        return False, False, problems, {}
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
    if man.get("web") is True:
        notes.append("web variant (format 2: trimmed index, no loot tables) - for the online check only, never into "
                     "the skill package")
    if man.get("label_confirmed") is False:
        notes.append("version label not confirmed (the game had not run since the update) - ask the player "
                     "for the version shown in the game's main menu")
    shippable = usable and not notes
    return usable, shippable, problems + notes, man


def unpack(path: Path, folder: Path) -> tuple[bool, list]:
    usable, _ship, problems, man = verify(path)
    if not usable:
        return False, problems
    extract(path, folder)
    return True, problems


def extract(path: Path, folder: Path) -> None:
    """Write the files of a bundle that check/verify accepted into `folder`. Files of an earlier bundle stay where they
    are: whoever reads the folder picks its files by name (web.prepare does)."""
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            (folder / n).write_bytes(z.read(n))


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
