"""Character text report of a player.gdc - replaces the .35 CLI gd_extract (deleted in .68)
(GD-Tools "Evaluate character", Extras 4/5). Same options, exit codes, text and JSON:

    python -m gdcheck.app.save_report player.gdc [--gd-dir "<Grim Dawn>"] [--lang EN]
                                      [--json FILE] [--raw-json FILE] [-v] [--no-db] [--no-verify]

Parsing: data/savefile.py (with the byte-identical round-trip check), names: data/game_names.py
straight from the installation (.arz + Text_XX.arc, no index needed), document: data/describe.py +
analysis/save_facts.py, text: report/save_text.py. Exit codes: 0 ok, 1 save missing/unreadable,
2 wrong call (argparse).

Changes against .35: the DEVOTIONS block names the skill each celestial power is bound to
("bound to Feral Claws" / "not bound"); the "Name database" line no longer advises 'pip install lz4'
(the new code uses the built-in decoder only).

The JSON keys stay German (charakter, ausruestung, berechnet, ...): they are a data contract for JSON
files players have uploaded and for the code reading them - do not translate them."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..analysis.save_facts import build_save_facts
from ..data import savefile
from ..data.describe import describe
from ..data.game_names import GameNames, NullNames
from ..report.save_text import celestial_bindings, line, render_text

__all__ = ["build_report", "find_game_dir", "main"]

CALC_NOTE = "Bonuses summed from database.arz. The savefile itself contains no calculated values."
_GAME_DIRS = (r"C:\Program Files (x86)\Steam\steamapps\common\Grim Dawn",
              r"C:\Program Files\Steam\steamapps\common\Grim Dawn",
              r"D:\Steam\steamapps\common\Grim Dawn",
              r"D:\SteamLibrary\steamapps\common\Grim Dawn",
              r"C:\GOG Games\Grim Dawn")


def find_game_dir() -> Path | None:
    """The Grim Dawn installation at the usual places (None if not found)."""
    cands = [Path(b) for b in _GAME_DIRS]
    cands += [Path.home() / ".steam/steam/steamapps/common/Grim Dawn",
              Path.home() / ".local/share/Steam/steamapps/common/Grim Dawn"]
    for c in cands:
        if (c / "database" / "database.arz").exists():
            return c
    return None


def has_records(names) -> bool:
    return callable(getattr(names, "record", None))


def build_report(doc: dict, names) -> dict:
    """The report document (legacy keys of gd_extract.analyse): readable part + calculated values
    (None without a database)."""
    if has_records(names):
        rep = build_save_facts(doc, names).document
    else:
        rep = describe(doc, names)
        rep["berechnet"] = {"hinweis": CALC_NOTE, "ausruestung": None, "skills_und_devotionen": None}
    rep["format"]["namensaufloesung"] = has_records(names)
    return rep


def _json_default(o):
    if isinstance(o, bytes):
        return o.hex()
    raise TypeError(f"not serializable: {type(o)}")


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Grim Dawn savegame report - reads player.gdc completely.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python -m gdcheck.app.save_report player.gdc\n"
               "  python -m gdcheck.app.save_report player.gdc --gd-dir \"C:\\...\\Grim Dawn\" -v\n"
               "  python -m gdcheck.app.save_report player.gdc --json char.json\n")
    ap.add_argument("savefile", help="path to player.gdc")
    ap.add_argument("--gd-dir", default=None,
                    help="Grim Dawn install folder (for readable names). Searched automatically if omitted.")
    ap.add_argument("--no-db", action="store_true", help="skip name resolution (short DBR names only)")
    ap.add_argument("--lang", default="EN", help="language code of Text_XX.arc (default EN)")
    ap.add_argument("--json", metavar="FILE", help="also write the report as JSON")
    ap.add_argument("--raw-json", metavar="FILE",
                    help="write the complete raw document as JSON (every field of the file)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="also list inventory, all devotion nodes and skills without points")
    ap.add_argument("--no-verify", action="store_true", help="skip the round-trip check")
    return ap


def _names(args):
    if args.no_db:
        return NullNames(), "off (--no-db)"
    gd = Path(args.gd_dir) if args.gd_dir else find_game_dir()
    if gd is None:
        return NullNames(), "no Grim Dawn installation found (set --gd-dir)"
    try:
        return GameNames(gd, args.lang), f"active ({gd})"
    except Exception as e:                     # noqa: BLE001 - shown in the report, run goes on
        return NullNames(), f"failed: {e}"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    path = Path(args.savefile)
    if path.is_dir():                          # the character folder is accepted too
        path = path / "player.gdc"
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1
    data = path.read_bytes()
    try:
        doc = savefile.parse_character(data)
    except savefile.GDFormatError as e:
        print(f"File could not be read: {e}\n"
              f"(Is this really a player.gdc? Other save files such as "
              f"levels_world001.map or transfer.gst have a different format.)", file=sys.stderr)
        return 1
    doc["_source_path"] = str(path)

    # proof of completeness: encrypt again and compare with the original
    verify_msg = "skipped"
    if not args.no_verify:
        try:
            ok = savefile.write_character(doc) == data
            verify_msg = ("OK - file fully parsed (byte-identical round trip)" if ok
                          else "FAILED - output differs, parser incomplete!")
        except Exception as e:                 # noqa: BLE001  # pragma: no cover
            verify_msg = f"ERROR: {e}"

    names, db_note = _names(args)
    rep = build_report(doc, names)
    rep["format"]["round_trip"] = verify_msg
    rep["format"]["datenbank"] = db_note

    print(render_text(rep, verbose=args.verbose, names=names, bindings=celestial_bindings(doc, names)))
    print(line("Name database", db_note))
    print(line("Round-trip check", verify_msg))

    if args.json:
        Path(args.json).write_text(json.dumps(rep, indent=2, ensure_ascii=False, default=_json_default),
                                   encoding="utf-8")
        print(f"\nReport written: {args.json}")
    if args.raw_json:
        Path(args.raw_json).write_text(json.dumps(doc, indent=2, ensure_ascii=False, default=_json_default),
                                       encoding="utf-8")
        print(f"Raw document written: {args.raw_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
