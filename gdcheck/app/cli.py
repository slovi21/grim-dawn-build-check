"""Command line: python -m gdcheck <player.gdc> [options]

Local mode (.67, read-only): python -m gdcheck --where [--json] | --character NAME | --all
finds game data, saves and ClaudeSkill-Data on this machine (gdcheck.app.local_env).

Exit codes: 0 ok, 2 savefile problem, 3 index problem, 4 configuration problem."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

from ..analysis.targets import TargetsError
from ..data.index import IndexError_
from ..data.savefile import GDFormatError
from ..domain.context import Options
from ..domain.enums import ContentProfile
from ..report import render_html, render_md, render_text
from . import errors as ERR
from . import local_env as LOCAL
from . import pipeline as PL
from . import product

EXIT_OK, EXIT_SAVE, EXIT_INDEX, EXIT_CONFIG = 0, 2, 3, 4


def parse(argv):
    ap = argparse.ArgumentParser(prog="gdcheck", description="Grim Dawn build check")
    ap.add_argument("savefile", nargs="?", help="player.gdc (or the character folder); "
                    "without it: --where, --character or --all (local mode)")
    ap.add_argument("--where", action="store_true",
                    help="local mode: show the game data, saves and index found on this machine (read-only)")
    ap.add_argument("--character", metavar="NAME", help="local mode: check this character (see --where)")
    ap.add_argument("--all", action="store_true", help="local mode: overview of all characters found")
    ap.add_argument("--index", help="gd-index-*.json.gz (default: GDCHECK_INDEX, else assets/ or "
                                    "ClaudeSkill-Data/out)")
    ap.add_argument("--profile", choices=[p.value for p in ContentProfile], default="general",
                    help="content goal: general (Ultimate), sr, ascendant, celestial")
    ap.add_argument("--hardcore", action="store_true", default=None, help="treat as hardcore")
    ap.add_argument("--difficulty", choices=["normal", "elite", "ultimate"])
    ap.add_argument("--live-version", help="live patch found by a web check, e.g. 1.3.0.8")
    ap.add_argument("--live-checked", help="date of that check, e.g. 2026-10-01")
    ap.add_argument("--installed-fingerprint", help="fingerprint of the local game data (GD-Tools)")
    ap.add_argument("--onslaught", type=int, help="Onslaught stacks for cooldown skills (default 0)")
    ap.add_argument("--boss-level", type=int, help="monster level for the boss combat check "
                    "(default: highest level of your greatest monsters killed)")
    ap.add_argument("--action-rate", type=float, help="your actions per second for DpS / life steal per second "
                    "(default: from the animation frames of each action)")
    ap.add_argument("--md", nargs="?", const="player", help="chat Markdown: player[,expert][,detail]")
    ap.add_argument("--text", nargs="?", const="player,expert", help="plain text (console)")
    ap.add_argument("--html", metavar="FILE", help="write Report.html")
    ap.add_argument("--json", nargs="?", const="-", metavar="FILE",
                    help="write the report model as JSON (no FILE or '-': print it); with --where: the findings")
    ap.add_argument("--detail", action="store_true", help="also compute rerolls, devotion plans, "
                                                         "skill and attribute moves (slower)")
    ap.add_argument("--mechanics-link", default="../../ClaudeSkill/GameMechanics.html")
    return ap.parse_args(argv)


def utf8_streams(*streams) -> None:
    """Windows: a piped stdout/stderr is cp1252 - a save path or character name outside it raised
    UnicodeEncodeError in --where (review .67). Switch such a stream to UTF-8 with errors="replace";
    streams that are UTF-8 already or cannot be reconfigured (StringIO, replaced streams) stay as they are."""
    for s in streams or (sys.stdout, sys.stderr):
        enc = str(getattr(s, "encoding", "") or "").lower().replace("-", "").replace("_", "")
        if enc == "utf8":
            continue
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def say(text: str, end: str = "\n", file=None) -> None:
    """print() that never fails on characters the stream cannot encode (replaced by '?')."""
    out = sys.stdout if file is None else file
    try:
        print(text, end=end, file=out)
    except UnicodeEncodeError:
        enc = getattr(out, "encoding", None) or "ascii"
        print(text.encode(enc, "replace").decode(enc, "replace"), end=end, file=out)


def _report_hint(log) -> str:
    """Where the player sends a log: Claude in the skill, the issue tracker in product mode."""
    if product.enabled():
        return f"open an issue at {product.ISSUES_URL} and attach {log}"
    return f"send {log} to Claude"


def main(argv=None) -> int:
    a = parse(sys.argv[1:] if argv is None else argv)
    utf8_streams()
    if a.where or a.character or a.all:
        return local_main(a)
    if not a.savefile:
        print("No savefile given.\n  What you can do: python -m gdcheck <player.gdc>, or let the check find the "
              "saves itself: --where (what is found), --character NAME, --all.", file=sys.stderr)
        return EXIT_SAVE
    return check(a, Path(a.savefile))


def local_main(a) -> int:
    """--where / --all / --character: never writes into the folders it finds."""
    env = LOCAL.detect()
    if a.where:
        if a.json:
            if a.json == "-":                       # ASCII: safe on any console or pipe encoding
                say(json.dumps(env.to_dict(), indent=1, ensure_ascii=True))
            else:
                ERR.write_retry(Path(a.json), json.dumps(env.to_dict(), indent=1, ensure_ascii=False))
        else:
            say(LOCAL.describe(env), end="")
        return EXIT_OK
    for n in env.notes:
        say(f"Local mode: {n}", file=sys.stderr)
    if a.all:
        return overview(a, env)
    try:
        ch = LOCAL.find_character(env, a.character)
    except ValueError as ex:
        say(f"{ex}\n  What you can do: python -m gdcheck --where lists the characters found "
            f"(local mode: {env.mode}).", file=sys.stderr)
        return EXIT_SAVE
    with local_run(env):
        return check(a, ch.gdc, env)


@contextlib.contextmanager
def local_run(env):
    """Reference files of this game data from ClaudeSkill-Data/out; error logs of a mounted or WSL run
    go to this machine's temp folder, never into the player's folders."""
    keys = ("GDCHECK_DATA_DIR", "GDCHECK_LOG_DIR")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        if env.out_dir is not None:
            os.environ["GDCHECK_DATA_DIR"] = str(env.out_dir)
        if env.mode in ("mounted", "wsl") and not saved["GDCHECK_LOG_DIR"]:
            os.environ["GDCHECK_LOG_DIR"] = str(Path(tempfile.gettempdir()) / "gdcheck-logs")
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def overview(a, env) -> int:
    """--all: the table of GD-Tools' overview (newest save per character, cache in memory only)."""
    from . import overview_cli as OV
    chars = env.defaults()
    if not chars:
        print("No characters found.\n  What you can do: connect the Grim Dawn save folder "
              "(Documents\\My Games\\Grim Dawn\\save) or upload player.gdc.", file=sys.stderr)
        return EXIT_SAVE
    idx = Path(a.index) if a.index else env.index
    if idx is None:
        print("No game data index found.\n  What you can do: start GD-Tools once (it builds the index).",
              file=sys.stderr)
        return EXIT_INDEX
    saves = [c.gdc for c in chars]
    with local_run(env):
        try:
            cache = OV.evaluate(saves, idx, {})
        except IndexError_ as ex:
            print(f"{ex}\n  What you can do: rebuild the game data index (GD-Tools -> Extras 2).", file=sys.stderr)
            return EXIT_INDEX
    print(f"Local mode ({env.mode}): {len(saves)} characters, index {idx.name} ({env.index_state})")
    print("\n".join(OV.overview_lines(saves, cache)))
    return EXIT_OK


def check(a, gdc: Path, env=None) -> int:
    if gdc.is_dir():
        gdc = gdc / "player.gdc"
    if not gdc.exists():
        print(f"Savefile not found: {gdc}\n  What you can do: check the path (the character folder contains "
              "player.gdc); in GD-Tools pick the character again or set the save folder (Extras 11).",
              file=sys.stderr)
        return EXIT_SAVE
    fingerprint = a.installed_fingerprint or (env.fingerprint if env is not None else None)
    if a.index:
        idx_path = Path(a.index)
    elif env is not None and env.index is not None:
        idx_path = env.index
    else:
        idx_path = PL.default_index(fingerprint)
    if idx_path is None:
        print("No game data index found.\n  What you can do: start GD-Tools - it builds the index of your "
              "installation (a few minutes) - or pass --index.", file=sys.stderr)
        return EXIT_INDEX
    try:
        index = PL.load_index(idx_path)
    except IndexError_ as ex:
        print(f"{ex}\n  What you can do: rebuild the game data index (GD-Tools -> Extras 2), then try again.",
              file=sys.stderr)
        return EXIT_INDEX
    opts = Options(profile=ContentProfile(a.profile), hardcore=a.hardcore,
                   difficulty={"normal": 0, "elite": 1, "ultimate": 2}.get(a.difficulty),
                   live_version=a.live_version, live_checked=a.live_checked,
                   installed_fingerprint=fingerprint, onslaught=a.onslaught,
                   boss_level=a.boss_level, action_rate=a.action_rate,
                   detail=a.detail)
    try:
        analysis = PL.analyse_file(gdc, index, opts)
    except (GDFormatError, ValueError, EOFError, IndexError, KeyError) as ex:
        log = ERR.write_log("build check", ex) if ERR.friendly_mode() else None
        print(f"Not a readable Grim Dawn save: {gdc}\n  {type(ex).__name__}: {ex}\n"
              "  Close Grim Dawn or wait until the character is saved, then try again (the game may have "
              "been writing it)." + (f"\n  If it stays: {_report_hint(log)}." if log else ""), file=sys.stderr)
        return EXIT_SAVE
    except TargetsError as ex:
        again = product.INSTALLER_AGAIN if product.enabled() else "unpack ClaudeSkill.zip again"
        print(f"Configuration problem: {ex}\n  What you can do: {again} (a file of the tools "
              "is damaged or from another version).", file=sys.stderr)
        return EXIT_CONFIG
    rep = PL.report_for(analysis, index)
    views = None
    if a.md:
        views = tuple(v.strip() for v in a.md.split(","))
        print(render_md.render(rep, views), end="")
    if a.text or not (a.md or a.html or a.json):
        views = tuple(v.strip() for v in (a.text or "player,expert").split(","))
        print(render_text.render(rep, views), end="")
    try:
        if a.html:
            ERR.write_retry(Path(a.html), render_html.render(rep, a.mechanics_link))
            if not a.md:
                print(f"HTML report written: {a.html}")
        if a.json:
            doc = json.dumps(rep.to_dict(), indent=1, ensure_ascii=False, default=str)
            if a.json == "-":
                say(doc)
            else:
                ERR.write_retry(Path(a.json), doc)
    except OSError as ex:
        _k, title, cause, fix = ERR.explain(ex)
        print(f"Cannot write the output file: {ex}\n  {title}: {cause}\n  What you can do: {fix}",
              file=sys.stderr)
        return EXIT_CONFIG
    return EXIT_OK
