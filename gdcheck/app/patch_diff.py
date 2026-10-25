"""Patch report: what a new game version changes for the player's characters (.53).

    python -m gdcheck.app.patch_diff --old <gd-index-old.json.gz> --new <gd-index-new.json.gz>
                                     <save files or folders ...> [--md] [--out <file>]

Per character, with the old and with the new game data (each check complete, same save):
    key numbers   Health, OA, DA, the resistances (value after the difficulty penalty, before the
                  cap), E7 DpS STEADY against every checked boss (hits + DoT), readiness score -
                  only what changed, biggest change first
    records       items, affixes, components, augments, skills (incl. modifiers/transmuters) and
                  devotion powers the character uses whose game data changed: changed fields
                  old -> new (skills: the value at the character's rank), fields that are new or gone
    rules         version-specific rules applied to the new data (ruleset R5) and every reference file
                  that does not belong to the new game data (ASSUMED)
Not covered: item sets (their records are not in the index - set bonuses still count in the numbers).
A character whose check fails with one of the two data sets is reported with the reason; the others go
on. All characters run with the old data first, then with the new (caches hold one index at a time).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..data.index import SUPPORTED_SCHEMA, IndexError_
from ..domain.context import Options
from . import pipeline as PL
from .local_env import index_meta

EXIT_INDEX = 3                          # as cli.EXIT_INDEX

RESISTS = ("Fire", "Cold", "Lightning", "Acid", "Vitality", "Pierce", "Bleeding", "Aether", "Chaos", "Physical")
MAX_FIELDS = 6
SKIP_FIELDS = ("_recordname", "_type", "_name")


@dataclass
class Run:
    numbers: dict = field(default_factory=dict)     # label -> value
    records: dict = field(default_factory=dict)     # record -> (label, rank)
    notes: tuple = ()
    error: str | None = None


@dataclass
class CharDiff:
    name: str
    numbers: list = field(default_factory=list)     # (label, old, new)
    records: list = field(default_factory=list)     # (label, record, [lines])
    notes: tuple = ()
    error: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.numbers or self.records)


def run_one(gdc: Path, index) -> Run:
    try:
        a = PL.analyse_file(Path(gdc), index, Options())
    except Exception as ex:                      # noqa: BLE001 - one save must not stop the report
        return Run(error=f"{type(ex).__name__}: {ex}")
    v = a.snap.combat
    nums = {"Health": v.abilities.get("health"), "Offensive Ability": v.abilities.get("oa"),
            "Defensive Ability": v.abilities.get("da")}
    for r in RESISTS:
        if r in v.resists:
            nums[f"{r} Resistance"] = (v.resists[r] or {}).get("value")
    for c in a.combat_checks or ():
        st = (c.states or {}).get("STEADY")
        if st is not None:
            nums[f"DpS vs {c.boss.split(',')[0]}"] = getattr(st, "dps_total", None) or st.dps
    nums["Readiness score"] = (a.score or {}).get("score")
    recs = {}
    for s in a.sources.sources:
        if s.record_id and not s.unresolved:
            recs.setdefault(s.record_id, (s.label, None))
    for s in a.sources.skills:
        if s.points > 0 or s.level > 0:
            recs[s.record] = (s.label, max(int(s.level or 0), 1))
    notes = tuple(getattr(a.ctx, "rule_notes", ()) or ()) + PL.reference_notes(a.ctx.index)
    return Run(nums, recs, notes)


def _val(v, rank):
    if isinstance(v, list):
        if rank and v:
            return v[min(rank, len(v)) - 1]
        return f"{v[0]}..{v[-1]}" if v else "[]"
    return v


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:,.6g}" if abs(v) < 1e6 else f"{v:,.0f}"
    return str(v)


def record_changes(old_rec: dict | None, new_rec: dict | None, rank: int | None) -> list[str]:
    """Readable lines for one record (empty list = no change)."""
    if old_rec == new_rec:
        return []
    if old_rec is None:
        return ["new in this game version"]
    if new_rec is None:
        return ["gone from the game data"]
    lines, keys = [], sorted(set(old_rec) | set(new_rec))
    for k in keys:
        if k in SKIP_FIELDS:
            continue
        a, b = old_rec.get(k), new_rec.get(k)
        if a == b:
            continue
        if k not in old_rec:
            lines.append(f"{k}: new field ({_fmt(_val(b, rank))})")
        elif k not in new_rec:
            lines.append(f"{k}: field gone (was {_fmt(_val(a, rank))})")
        else:
            va, vb = _val(a, rank), _val(b, rank)
            if va == vb:                         # a per-rank list changed elsewhere, not at this rank
                continue
            lines.append(f"{k}: {_fmt(va)} -> {_fmt(vb)}" + (f" (rank {rank})" if rank and isinstance(a, list) else ""))
    if len(lines) > MAX_FIELDS:
        lines = lines[:MAX_FIELDS] + [f"... {len(lines) - MAX_FIELDS} more fields"]
    return lines


def compare_runs(name: str, old: Run, new: Run, old_index, new_index) -> CharDiff:
    d = CharDiff(name, notes=new.notes)
    if old.error or new.error:
        d.error = "; ".join(x for x in (f"old data: {old.error}" if old.error else "",
                                        f"new data: {new.error}" if new.error else "") if x)
        return d
    for k in list(old.numbers) + [k for k in new.numbers if k not in old.numbers]:
        a, b = old.numbers.get(k), new.numbers.get(k)
        if a is None and b is None:
            continue
        if a is None or b is None or abs(float(a) - float(b)) > max(1e-6, 1e-4 * abs(float(a))):
            d.numbers.append((k, a, b))

    def size(t):
        _k, a, b = t
        if a is None or b is None:
            return float("inf")
        return abs(float(b) - float(a)) / max(abs(float(a)), 1.0)
    d.numbers.sort(key=size, reverse=True)
    for rec in sorted(set(old.records) | set(new.records)):
        label, rank = new.records.get(rec) or old.records.get(rec)
        lines = record_changes(old_index.record(rec), new_index.record(rec), rank)
        if lines:
            d.records.append((label or rec.rsplit("/", 1)[-1], rec, lines))
    return d


def compare(old_index, new_index, saves) -> list[CharDiff]:
    saves = [Path(s) for s in saves]
    olds = [run_one(s, old_index) for s in saves]            # old data for all first (one index cached)
    news = [run_one(s, new_index) for s in saves]
    return [compare_runs(s.stem, o, n, old_index, new_index) for s, o, n in zip(saves, olds, news)]


def _num(v) -> str:
    if v is None:
        return "-"
    return f"{float(v):,.0f}" if abs(float(v)) >= 100 else f"{float(v):,.1f}"


def render(diffs, old_index, new_index, md: bool = False) -> str:
    ov, nv = old_index.meta.get("game_version", "?"), new_index.meta.get("game_version", "?")
    h1, h2 = ("# ", "## ") if md else ("", "")
    out = [f"{h1}Patch report: game data {ov} -> {nv}",
           f"old: {ov} (fingerprint {old_index.meta.get('fingerprint', '?')}), new: {nv} "
           f"(fingerprint {new_index.meta.get('fingerprint', '?')}). Item sets are not covered (not in the "
           "game data index); their bonuses still count in the numbers.", ""]
    for d in diffs:
        out.append(f"{h2}{d.name}")
        if d.error:
            out += [f"not compared: {d.error}", ""]
            continue
        if not d.changed:
            out.append("no change in the numbers and in the game data of the items and skills used")
        for k, a, b in d.numbers:
            pct = "" if a in (None, 0) or b is None else f" ({(float(b) / float(a) - 1) * 100:+.1f}%)"
            out.append(f"{'- ' if md else '  '}{k}: {_num(a)} -> {_num(b)}{pct}")
        if d.records:
            out.append(f"{'- ' if md else '  '}changed game data of what you use ({len(d.records)}):")
            for label, rec, lines in d.records:
                out.append(f"{'  - ' if md else '    '}{label} ({rec.rsplit('/', 1)[-1]}): " + "; ".join(lines))
        for n in d.notes:
            out.append(f"{'- ' if md else '  '}note: {n}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _saves(paths) -> list[Path]:
    out = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out += sorted(p.rglob("player.gdc")) or sorted(p.glob("*.gdc"))
        elif p.is_file():
            out.append(p)
    return out


def _name(p: Path) -> str:
    return p.parent.name.lstrip("_") if p.name.lower() == "player.gdc" else p.stem


def _load(path, which: str):
    """The index of the old or new game data. An index of another schema cannot be read (.70: schema 9 only):
    the old one can still be schema 8 right after a skill update that came with a game patch."""
    try:
        return PL.load_index(Path(path))
    except IndexError_ as ex:
        schema = index_meta(Path(path)).get("schema", "?")
        what = (f"Patch report skipped: the index of the {which} game data ({Path(path).name}) has schema {schema}, "
                f"this version reads schema {SUPPORTED_SCHEMA}.\n  What you can do: ")
        raise IndexError_(what + ("nothing for this update - an index of old game data is not rebuilt. The next patch "
                                  "is compared again (GD-Tools renews the kept copy)." if which == "old"
                                  else f"rebuild the index with GD-Tools. ({ex})")) from ex


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gdcheck.app.patch_diff", description=__doc__.split("\n")[0])
    ap.add_argument("saves", nargs="+", help="player.gdc files or folders with them")
    ap.add_argument("--old", required=True, help="index of the old game data")
    ap.add_argument("--new", required=True, help="index of the new game data")
    ap.add_argument("--md", action="store_true", help="Markdown instead of text")
    ap.add_argument("--out", help="write the report to this file")
    a = ap.parse_args(argv)
    saves = _saves(a.saves)
    if not saves:
        print("No save files (player.gdc) found.", file=sys.stderr)
        return 2
    try:
        old_i, new_i = _load(a.old, "old"), _load(a.new, "new")
    except IndexError_ as ex:
        print(ex, file=sys.stderr)
        return EXIT_INDEX
    diffs = compare(old_i, new_i, saves)
    for d, s in zip(diffs, saves):
        d.name = _name(s)
    text = render(diffs, old_i, new_i, a.md)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"Patch report written: {a.out} ({len(diffs)} characters, "
              f"{sum(1 for d in diffs if d.changed)} with changes)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    from . import errors
    raise SystemExit(errors.run(main, "patch report"))
