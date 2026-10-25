"""Overview of many characters in one process (index loaded once):

    python -m gdcheck.app.overview_cli --cache overview-cache.json [--index X] a.gdc b.gdc ...

Only changed saves (mtime, index, skill version) are evaluated again. Prints the
table sorted by Build Readiness (format of overview-<date>.txt)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import VERSION
from ..analysis import overview as OV
from ..data import campaign as CAMP
from ..domain.context import Options
from . import pipeline as PL

HEAD = ("#", "Character", "Class", "Lvl", "Stage", "Score", "Dummy s", "GAPs e/m/h", "Actions H/M/L", "Ultimate")
WIDTHS = (3, 18, 34, 3, 7, 5, 7, 10, 13, 8)
LEGEND = ("Sorted by Build Readiness 0-100 (profile General Ultimate): EARLY 0-33, MID 34-66, "
          "ENDGAME 67-100 (100 = every target met with room to spare in ENDGAME). GAPs e/m/h = easy/medium/hard "
          "to fix. Dummy s = seconds the damage model needs to bring the Training Dummy to its shield (Ultimate, MID and "
          "ENDGAME only; fewer is better; '-' = none; comparable only at the same character level, the dummy level = yours + 2). Actions H/M/L = recommended actions by priority High/Medium/Low. Ultimate = campaign "
          "on Ultimate: M main game, A AoM, F FG, R FoA (capital = done, small = started, - = not "
          "started; 'n rg' = no quest file, n rift gates unlocked on Ultimate). Abbreviations as in the "
          "Crate forum list (Demo, Occu, NB, Arca, Sorc, BM, ...).")
CLASS_SHORT = {"Sorcerer": "Sorc", "Sorceress": "Sorc", "Blademaster": "BM"}


def save_path(p) -> Path:
    """A character folder stands for its player.gdc."""
    p = Path(p)
    return p / "player.gdc" if p.is_dir() else p


def evaluate(paths, index_path, cache: dict) -> dict:
    index = None
    for p in paths:
        p = save_path(p)
        try:
            key = f"{p.stat().st_mtime:.0f}|{Path(index_path).name}|{VERSION}"
        except OSError:
            cache[str(p)] = {"k": "", "u": {"fehler": "file not found"}}
            continue
        if (cache.get(str(p)) or {}).get("k") == key:
            continue
        if index is None:
            index = PL.load_index(index_path)
        try:
            a = PL.analyse_file(p, index, Options(build_quality=False))   # the overview skips the build check
            u = OV.row(a, CAMP.read(p))
        except Exception as ex:                                   # noqa: BLE001
            u = {"fehler": f"{type(ex).__name__}: {ex}"[:160]}
        cache[str(p)] = {"k": key, "u": u}
    return cache


def ultimate_cell(u: dict) -> str:
    """Ultimate column: campaign letters from quests.gdd, else rift gates on Ultimate."""
    if u.get("kampagne_ultimate"):
        return CAMP.short_form(u["kampagne_ultimate"])
    if u.get("ultimate"):
        return "?" if u.get("rift_ultimate") is None else f"{u['rift_ultimate']} rg"
    return "-"


def dummy_cell(u: dict) -> str:
    """Dummy s column: whole seconds (from 120 s minutes, '86m') of the Training Dummy shield time, '-' without one."""
    secs = u.get("dummy_s")
    if not isinstance(secs, (int, float)):
        return "-"
    return f"{round(secs / 60)}m" if secs >= 119.5 else str(round(secs))


def line(nr, name, u) -> tuple:
    if not u or u.get("fehler"):
        return (str(nr), name, "not readable - see below", "-", "-", "-", "-", "-", "-", "-")
    ga, ac = u.get("gap_aufwand") or {}, u.get("aktionen") or {}
    cls = CLASS_SHORT.get(u.get("klasse"), u.get("klasse")) or "?"
    kk = u.get("klassen_kurz") or ""
    return (str(nr), name, cls + (f" ({kk})" if "/" in kk else ""), str(u.get("level", "-")),
            u.get("stufe", "-"), str(u.get("punkte", "-")), dummy_cell(u),
            f"{ga.get('easy', 0)}/{ga.get('medium', 0)}/{ga.get('hard', 0)}",
            f"{ac.get('HIGH', 0)}/{ac.get('MEDIUM', 0)}/{ac.get('LOW', 0)}", ultimate_cell(u))


def table(rows) -> list[str]:
    fmt = lambda z: "  ".join(str(v)[:b].ljust(b) for v, b in zip(z, WIDTHS)).rstrip()  # noqa: E731
    return [fmt(HEAD), "  ".join("-" * b for b in WIDTHS)] + [fmt(r) for r in rows]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gdcheck-overview")
    ap.add_argument("saves", nargs="+")
    ap.add_argument("--index")
    ap.add_argument("--cache")
    a = ap.parse_args(argv)
    idx = Path(a.index) if a.index else PL.default_index()
    if idx is None:
        print("No game data index found.", file=sys.stderr)
        return 3
    cache = {}
    if a.cache and Path(a.cache).exists():
        try:
            cache = json.loads(Path(a.cache).read_text(encoding="utf-8"))
        except ValueError:
            cache = {}
    cache = evaluate(a.saves, idx, cache)
    if a.cache:
        Path(a.cache).write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print("\n".join(overview_lines(a.saves, cache)))
    return 0


def overview_lines(saves, cache: dict) -> list[str]:
    """Table sorted by Build Readiness, the unreadable saves and the legend (used by --all, too)."""
    items = [(save_path(p), (cache.get(str(save_path(p))) or {}).get("u")) for p in saves]

    def name_of(p, u):
        # the save folder name tells characters with the same in-game name apart
        if p.name.lower() == "player.gdc":
            return p.parent.name.lstrip("_")
        return p.stem.replace("_", " ")
    items.sort(key=lambda x: (-((x[1] or {}).get("punkte") or -1), -((x[1] or {}).get("level") or 0),
                              name_of(*x)))
    rows = [line(i, name_of(p, u), u) for i, (p, u) in enumerate(items, 1)]
    out = table(rows)
    errors = [(name_of(p, u), u["fehler"]) for p, u in items if u and u.get("fehler")]
    if errors:
        out.append("")
        out += [f"Not readable: {n} - {msg}" for n, msg in errors]
    return out + ["", LEGEND]


if __name__ == "__main__":
    from . import errors
    sys.exit(errors.run(main, "overview"))
