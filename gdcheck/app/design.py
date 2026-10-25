"""Build design: check a planned build against the game database, or look up facts for one.

    python -m gdcheck.app.design lint <build.json> [--md] [--json FILE]
    python -m gdcheck.app.design set "<set or item name>"
    python -m gdcheck.app.design skills <mastery>
    python -m gdcheck.app.design devotions [--weapons Axe Sword | melee | ranged | caster]

Needs the game data index and references/design-data-<version>.json (refs_extract, part 'design').
Exit codes: 0 ok (lint: no ERROR), 1 lint found ERRORs, 3 index problem, 4 build.json unreadable or an
argument not understood (unknown weapon type, unknown mastery), 6 design data missing for this game data."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..analysis.design import facts as FA
from ..analysis.design import rules as RL
from ..analysis.design import spec as SP
from ..analysis.design.catalog import Catalog
from ..analysis.design.spec import WEAPONS
from ..data import design_data as DD
from ..data.index import IndexError_
from ..domain.context import load_ruleset
from ..report import design as RD
from . import errors as ERR
from . import pipeline as PL

EXIT_OK, EXIT_ERRORS, EXIT_INDEX, EXIT_SPEC, EXIT_DESIGN_DATA = 0, 1, 3, 4, 6

# collective words for --weapons -> the real weapon types (said in the output; Shield is not in any group)
WEAPON_GROUPS = {"melee": ("Axe", "Axe2h", "Dagger", "Mace", "Mace2h", "Spear", "Spear2h", "Sword", "Sword2h"),
                 "ranged": ("Ranged1h", "Ranged2h"),
                 "caster": ("Offhand", "Scepter", "Staff")}


def expand_weapons(names) -> tuple[list, list, list]:
    """(real weapon types, unknown names, 'group = types' lines): exact types case-insensitive, groups and the
    one-handed aliases (Axe1h = Axe) mapped."""
    canon = {w.lower(): w for w in WEAPONS}
    out, unknown, said = [], [], []
    for n in names:
        key = str(n).lower()
        if key in WEAPON_GROUPS:
            said.append(f"{key} = {', '.join(WEAPON_GROUPS[key])}")
            types = WEAPON_GROUPS[key]
        elif key in SP.WEAPON_ALIASES:
            said.append(f"{key} = {SP.WEAPON_ALIASES[key]}")
            types = (SP.WEAPON_ALIASES[key],)
        elif key in canon:
            types = (canon[key],)
        else:
            unknown.append(n)
            continue
        out += [t for t in types if t not in out]
    return out, unknown, said


def parse(argv):
    ap = argparse.ArgumentParser(prog="python -m gdcheck.app.design", description=__doc__.split("\n")[0])
    ap.add_argument("--index", help="gd-index-*.json.gz (default: assets/)")
    ap.add_argument("--refs", help="folder with design-data-<version>.json instead of references/ (no extra folders)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("lint", help="check a build.json")
    p.add_argument("build", help="build.json")
    p.add_argument("--md", action="store_true", help="chat Markdown (default)")
    p.add_argument("--json", metavar="FILE", help="also write the result as JSON")
    p = sub.add_parser("set", help="an item set: members, bonuses per piece count, conversions")
    p.add_argument("name", nargs="+")
    p = sub.add_parser("skills", help="every skill of a mastery: tier, ranks, base skill, facts")
    p.add_argument("mastery")
    p = sub.add_parser("devotions", help="every constellation: points, affinity, weapon need, power trigger")
    p.add_argument("--weapons", nargs="*", default=[], help="your weapon types (marks constellations you cannot use)")
    return ap.parse_args(argv)


def load(index_arg, refs=None) -> tuple[Catalog | None, str, int]:
    idx_path = Path(index_arg) if index_arg else PL.default_index()
    if idx_path is None:
        print("No game data index (assets/gd-index-*.json.gz).\n  What you can do: rebuild it with GD-Tools.",
              file=sys.stderr)
        return None, "", EXIT_INDEX
    try:
        idx = PL.load_index(idx_path)
    except IndexError_ as ex:
        print(f"Game data index unreadable: {ex}\n  What you can do: rebuild it with GD-Tools.", file=sys.stderr)
        return None, "", EXIT_INDEX
    res = DD.resolve(idx, Path(refs), []) if refs else DD.resolve(idx)
    if res.path is None:
        print("No design data (references/design-data-<version>.json) for this game data.\n  What you can do: "
              "GD-Tools writes it with the reference data after an index rebuild (refs_extract), or send "
              "the game's .arz files / a patch bundle to Claude. Numbers are never taken from the web instead.",
              file=sys.stderr)
        return None, "", EXIT_DESIGN_DATA
    note = res.note or f"design data {res.path.name} ({res.match})"
    return Catalog(idx, res.data), note, EXIT_OK


def _print_table(title, cols, rows) -> None:
    print("\n".join(RD._table("", title, cols, rows)).replace("####  ", "#### "))


def main(argv=None) -> int:
    a = parse(sys.argv[1:] if argv is None else argv)
    said: list = []
    if a.cmd == "devotions":
        a.weapons, unknown, said = expand_weapons(a.weapons)
        if unknown:
            print(f"unknown weapon type {', '.join(repr(u) for u in unknown)}\n  What you can do: use one of "
                  f"{', '.join(WEAPONS)}, a one-handed name ({', '.join(SP.WEAPON_ALIASES)}) or a collective word: "
                  f"{', '.join(WEAPON_GROUPS)} (mapped to the real types, said in the output).", file=sys.stderr)
            return EXIT_SPEC
    cat, note, rc = load(a.index, a.refs)
    if cat is None:
        return rc
    if a.cmd == "lint":
        try:
            raw = json.loads(Path(a.build).read_text(encoding="utf-8"))
        except (OSError, ValueError) as ex:
            print(f"build.json unreadable: {ex}\n  What you can do: check the file (JSON, UTF-8).", file=sys.stderr)
            return EXIT_SPEC
        try:
            spec = SP.resolve(raw, cat)
        except SP.SpecError as ex:
            print(f"build.json has the wrong shape: {ex}\n  What you can do: compare it with "
                  "references/design-example-cold-werewolf-reaver.json.", file=sys.stderr)
            return EXIT_SPEC
        d = RL.run_all(spec, cat, load_ruleset())
        d.data_note = note
        print(RD.render_md(d, cat))
        if a.json:                  # sorted keys: the affinity dicts were in set order (PYTHONHASHSEED), .69
            Path(a.json).write_text(json.dumps(RD.to_json(d, cat), indent=1, sort_keys=True, default=str), encoding="utf-8")
        return EXIT_ERRORS if any(f.status == "ERROR" for f in d.findings) else EXIT_OK
    if a.cmd == "set":
        title, cols, rows = FA.set_table(cat, " ".join(a.name))
    elif a.cmd == "skills":
        title, cols, rows = FA.skills_table(cat, a.mastery)
    else:
        title, cols, rows = FA.devotions_table(cat, a.weapons)
    if not cols:
        print(title, file=sys.stderr)
        return EXIT_SPEC
    for line in said:
        print(line)
    if said:
        print("Shield is in no group - name it if you carry one.\n")
    _print_table(title, cols, rows)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(ERR.run(main, "build design"))
