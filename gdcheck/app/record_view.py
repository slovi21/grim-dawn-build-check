"""Print a record raw from the Grim Dawn database - with all fields (port of the .35 db_record).

The game data index holds bonus and meta fields only. Where something is missing there (example: the
bonuses of the werewolf form), this tool shows the record as it stands in database.arz / GDX*.arz and
follows references to other records (fields whose value ends in .dbr).

    python -m gdcheck.app.record_view records/skills/playerclass10/werewolf1.dbr
    python -m gdcheck.app.record_view werewolf --search         (search record names)
    python -m gdcheck.app.record_view <record> --depth 2        (follow references 2 levels)
    python -m gdcheck.app.record_view <record> --gd-dir "C:\\Steam\\steamapps\\common\\Grim Dawn"
    python -m gdcheck.app.record_view --bosses [--index gd-index-<version>.json.gz]   (boss dump)

Same output as db_record. English flags since .68; the German --suche/--tiefe/--alle/--bosse still work
(hidden aliases). Please send the complete output to Claude in the chat.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..data.arz import ArzFile, arz_paths

MAX_HITS = 150
FULL_OUTPUT = 5            # up to this many hits are all printed in full

# Bosses for the boss database (30.09.2026): display names in the game language. --bosses searches
# records/creatures/ for records whose "description" points to a tag with this text and prints them
# with their skills. Tags without an index: this list (as of 1.3.0.8).
BOSS_TAGS = {
    "tagGDX2EndlessDungeon_Boss08": "Alkamir, Shattered Executioner",
    "tagGhostBoss05": "Alkamos, Lord Executioner",
    "tagGDX2Boss_Special": "Callagadra, Scion of the Sands",
    "tagGDX2EndlessDungeon_Special04": "Crate of Entertainment",
    "tagNPC_Homestead_03": "Avatar of Mogdrogen",
    "tagGDX1Boss_Wendigo01": "Ravager of Minds",
    "tagNemesis_Clone01": "Clone of John Bourbon",
}
BOSS_NAMES = ("Ravager of", "Lokarr", "Clone of John Bourbon", "Callagadra",
              "Crate of Entertainment", "Avatar of Mogdrogen", "Alkamir", "Alkamos")
# Hidden in the search (graphics, sound, UI, test data) - --all shows them
UNIMPORTANT = ("records/fx/", "records/sounds/", "records/ui/", "records/sandbox/",
               "records/effects/", "/decals/", "_footstep", "charactersounds")


def read(dbs, name):
    """(record, file name) - later databases override earlier ones."""
    for db in reversed(dbs):
        if name in db.records:
            rec = db.read_record(name)
            if rec is not None:
                return rec, getattr(db, "path", Path("?")).name
    return None, None


def search(dbs, term, all_records=False):
    """All words of the term must occur in the name (case-insensitive). Graphics, sound and test
    records are hidden unless all_records=True."""
    words = [w for w in term.lower().replace("\\", "/").split() if w]
    names = set()
    for db in dbs:
        for n in db.records:
            nl = n.lower()
            if all(w in nl for w in words) and (all_records or not any(u in nl for u in UNIMPORTANT)):
                names.add(n)
    return sorted(names)


def show(dbs, name, depth=1, out=print):
    """Print a record and follow its references `depth` levels (each record once; iterative, in the
    order of the .35 recursion)."""
    seen = set()
    stack = [(name, 0)]
    while stack:
        current, level = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        rec, source = read(dbs, current)
        indent = "    " * level
        if rec is None:
            out(f"{indent}{current}: not found")
            continue
        out(f"{indent}=== {current}   [{source}, type {rec.get('_type', '?')}]")
        refs = []
        for k in sorted(k for k in rec if not k.startswith("_")):
            v = rec[k]
            out(f"{indent}  {k} = {v}")
            for w in (v if isinstance(v, list) else [v]):
                if isinstance(w, str) and w.lower().endswith(".dbr") and w.startswith("records/"):
                    refs.append(w)
        if level < depth:
            follow = [w for w in refs if not w.startswith(("records/fx/", "records/sounds/", "records/ui/"))]
            stack.extend((w, level + 1) for w in reversed(follow))


def boss_tags(index=None):
    """Tag -> name: the fixed list plus every index tag whose text starts with a boss name (finds
    Ravager of Souls/Flesh, Lokarr variants)."""
    tags = dict(BOSS_TAGS)
    if index:
        try:
            from ..data.index import GameIndex
            for k, v in (GameIndex(index).tags or {}).items():
                if isinstance(v, str) and v.startswith(BOSS_NAMES) and "{" not in v \
                        and len(v) < 60 and not k.lower().startswith(("ach", "tagq")):
                    tags.setdefault(k, v)
        except Exception as ex:                                 # noqa: BLE001
            print(f"Index not read ({ex}); using the built-in boss list.")
    return tags


def bosses(dbs, index=None, out=print, depth=1):
    """Print all creature records whose 'description' is a boss tag."""
    tags = boss_tags(index)
    candidates = sorted({n for db in dbs for n in db.records if n.lower().startswith("records/creatures/")})
    out(f"Searching {len(candidates)} creature records for {len(tags)} boss names ...")
    found = {}
    for i, n in enumerate(candidates, 1):
        rec, _q = read(dbs, n)
        if rec and rec.get("description") in tags:
            found.setdefault(rec["description"], []).append(n)
        if i % 5000 == 0:
            print(f"  {i}/{len(candidates)} ...", file=sys.stderr)
    for tag, name in sorted(tags.items(), key=lambda x: x[1]):
        names = found.get(tag, [])
        out(f"\n##### {name}  ({tag}): {len(names)} record(s)")
        for n in names[:4]:
            show(dbs, n, depth, out=out)
    return found


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Print a raw record from the game database.")
    ap.add_argument("--bosses", dest="bosses", action="store_true",
                    help="dump all boss records (Alkamir, superbosses) with their skills")
    ap.add_argument("--index", default=None, help="game data index for boss names (optional)")
    ap.add_argument("record", nargs="*",
                    help="record path (records/...dbr) or search term(s); several words = all must match")
    ap.add_argument("--search", dest="search", action="store_true",
                    help="list record names containing this term")
    ap.add_argument("--depth", dest="depth", type=int, default=1,
                    help="follow references this many levels deep (default 1)")
    ap.add_argument("--gd-dir", default=None, help="Grim Dawn folder; searched if omitted")
    ap.add_argument("--all", dest="all", action="store_true",
                    help="also show graphics/sound/test records in the search")
    # the German flags of older GD-Tools versions and notes keep working, but are not shown (.68)
    for old, dest in (("--bosse", "bosses"), ("--suche", "search"), ("--alle", "all")):
        ap.add_argument(old, dest=dest, action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    ap.add_argument("--tiefe", dest="depth", type=int, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    if args.gd_dir:
        gd = Path(args.gd_dir)
    else:
        from .local_env import installed_game_dir
        gd = installed_game_dir()
    if gd is None:
        sys.exit("Grim Dawn installation not found - please pass --gd-dir.")
    dbs = [ArzFile(p) for p in arz_paths(gd)]
    if not dbs:
        sys.exit(f"No .arz database in {gd}.")

    if args.bosses:
        bosses(dbs, args.index)
        return 0
    if not args.record:
        ap.error("record or search term missing (or use --bosses)")
    term = " ".join(args.record)
    if args.search or not term.lower().endswith(".dbr"):
        hits = search(dbs, term, args.all)
        note = "" if args.all else " (without graphics/sound/test; --all shows them)"
        print(f"{len(hits)} records contain '{term}'{note}:")
        for n in hits[:MAX_HITS]:
            print("  " + n)
        if len(hits) > MAX_HITS:
            print(f"  ... and {len(hits) - MAX_HITS} more - narrow the term.")
        # few hits: print them all in full right away (30.09.2026)
        if 1 <= len(hits) <= FULL_OUTPUT and not args.search:
            for n in hits:
                print()
                show(dbs, n, max(0, args.depth))
        return 0
    show(dbs, term.replace("\\", "/"), max(0, args.depth))
    return 0


if __name__ == "__main__":
    sys.exit(main())
