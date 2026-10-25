"""Diagnosis of an unreadable player.gdc (port of the .35 gd_diag onto gdcheck.data.savefile).

When the save reader stops with a message like
    "string length 2652427363 at offset 14291 exceeds file size"
the parser lost its step inside a block and reads garbage as a length. Almost always a patch
inserted fields the parser does not know yet.

Steps:
  1. Parse normally and report how far each block gets.
  2. Try the known variable layouts - number fields per item, extra bytes per skill, extra fields
     per stash page - each as a complete pass over the file.

The judge is not plausibility but the checksum: every block carries one, and it only matches when
the parser ends exactly at the right place. The result is also re-encrypted and compared with the
original; a byte-identical round trip proves the variant instead of guessing it.

    python -m gdcheck.app.save_diag "<path to player.gdc>"

Prints format facts and the character name only. Same output as gd_diag; the layout variants go to
savefile.parse_character/write_character as an explicit Hooks object (gd_diag swapped the module's
functions). Exit codes: 0 diagnosis printed, 1 file missing or unreadable, 3 not a save (shorter than
the character header or header damaged) - a short message instead of a traceback (.68). Backup times
are printed in UTC.
"""
from __future__ import annotations

import itertools
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..data import savefile as S

# ---------------------------------------------------------------------------
# Step 1: normal pass, block by block
# ---------------------------------------------------------------------------


def walk(data: bytes) -> dict:
    """Like parse_character, but reports the state instead of only stopping."""
    report = {"blocks": [], "fehler": None, "header": None}
    try:                                     # shorter than the header / not a save: no traceback (.68)
        r = S.GDReader(data)
        magic = r.read_u32()
        report["magic_ok"] = (magic == S.GDC_MAGIC)
        report["preamble_version"] = r.read_u32()
        report["header"] = {
            "name": r.read_wstr(),
            "male": r.read_bool(),
            "class_tag": r.read_str(),
            "level": r.read_u32(),
            "hardcore": r.read_bool(),
            "expansion_status": r.read_byte(),
        }
    except S.GDFormatError as e:
        report["header_error"] = str(e)
        return report
    try:
        r.verify_checksum()
        report["header_checksum"] = "ok"
    except S.GDFormatError as e:
        report["header_checksum"] = f"FEHLER: {e}"
        return report

    report["data_version"] = r.read_u32()
    r.read_bytes(16)

    while r.remaining() > 0:
        offset = r.pos
        try:
            block_id, end = r.begin_block()
        except S.GDFormatError as e:
            report["fehler"] = f"block header at offset {offset}: {e}"
            return report

        payload_start = r.pos
        length = end - payload_start
        entry = {"id": block_id, "offset": offset, "laenge": length,
                 "version": None, "status": "", "pruefsumme": "-"}

        reader = S._BLOCK_READERS.get(block_id)
        if reader is None:
            entry["status"] = "unknown block, kept opaque"
            r.read_bytes(length)
        else:
            try:
                block = reader(r, end)
                entry["version"] = block.get("version")
                rest = end - r.pos
                if rest == 0:
                    entry["status"] = "complete"
                elif rest > 0:
                    entry["status"] = f"{rest} bytes trailing fields (unnamed)"
                    r.read_bytes(rest)
                else:
                    entry["status"] = f"OVERRUN by {-rest} bytes"
            except S.GDFormatError as e:
                entry["status"] = f"ABORTED at offset {r.pos}: {e}"
                report["blocks"].append(entry)
                report["fehler"] = f"Block {block_id}"
                return report

        try:
            r.end_block(block_id, end)
            entry["pruefsumme"] = "ok"
        except S.GDFormatError as e:
            entry["pruefsumme"] = f"ERROR: {e}"
            report["blocks"].append(entry)
            report["fehler"] = f"checksum block {block_id}"
            return report
        report["blocks"].append(entry)
    return report


# ---------------------------------------------------------------------------
# Step 2: try layout variants
# ---------------------------------------------------------------------------

def make_item_reader(tail: int, stack_at: int = 0):
    def read_item(r, v2=False):
        item = {
            "basename": r.read_str(), "prefix_name": r.read_str(),
            "suffix_name": r.read_str(), "modifier_name": r.read_str(),
            "transmute_name": r.read_str(), "seed": r.read_u32(),
            "relic_name": r.read_str(), "relic_bonus": r.read_str(),
            "relic_seed": r.read_u32(), "augment_name": r.read_str(),
            "unknown": r.read_u32(), "augment_seed": r.read_u32(),
            "relic_completion_level": r.read_u32(),
        }
        if not v2:
            item["stack_count"] = r.read_u32()
            return item
        vals = [r.read_u32() for _ in range(tail)]
        item["_tail"] = vals
        item["stack_count"] = vals[stack_at] if 0 <= stack_at < len(vals) else 0
        return item
    return read_item


def make_item_writer():
    def write_item(w, item, v2=False):
        for k in ("basename", "prefix_name", "suffix_name", "modifier_name", "transmute_name"):
            w.write_str(item[k])
        w.write_u32(item["seed"])
        w.write_str(item["relic_name"])
        w.write_str(item["relic_bonus"])
        w.write_u32(item["relic_seed"])
        w.write_str(item["augment_name"])
        w.write_u32(item["unknown"])
        w.write_u32(item["augment_seed"])
        w.write_u32(item["relic_completion_level"])
        if not v2:
            w.write_u32(item["stack_count"])
            return
        for v in item.get("_tail", []):
            w.write_u32(v)
    return write_item


def make_skill_reader(extra: int):
    def read_skill(r, v2=False):
        s = {"skill_name": r.read_str(), "level": r.read_u32(), "enabled": r.read_bool()}
        if v2:
            s["_extra"] = [r.read_byte() for _ in range(extra)]
        s.update({
            "devotion_level": r.read_u32(), "devotion_experience": r.read_u32(),
            "sublevel": r.read_u32(), "skill_active": r.read_bool(),
            "skill_transition": r.read_bool(),
            "autocast_skill_name": r.read_str(),
            "autocast_controller_name": r.read_str(),
        })
        return s
    return read_skill


def make_skill_writer(extra: int):
    def write_skill(w, s, v2=False):
        w.write_str(s["skill_name"])
        w.write_u32(s["level"])
        w.write_bool(s["enabled"])
        if v2:
            for b in s.get("_extra", [0] * extra):
                w.write_byte(b)
        w.write_u32(s["devotion_level"])
        w.write_u32(s["devotion_experience"])
        w.write_u32(s["sublevel"])
        w.write_bool(s["skill_active"])
        w.write_bool(s["skill_transition"])
        w.write_str(s["autocast_skill_name"])
        w.write_str(s["autocast_controller_name"])
    return write_skill


def make_page_reader(extra: int):
    def read_stash_page(r, v2, float_xy=False):
        _, sub_end = r.begin_block(expected_id=0)
        page = {"width": r.read_u32(), "height": r.read_u32()}
        items = []
        for _ in range(r.read_u32()):
            it = r.hooks.read_item(r, v2)
            if float_xy:
                it["x"] = r.read_float()
                it["y"] = r.read_float()
            else:
                it["x"] = r.read_u32()
                it["y"] = r.read_u32()
            items.append(it)
        page["items"] = items
        if v2:
            page["extra"] = [r.read_u32() for _ in range(extra)]
        r.end_block(0, sub_end)
        return page
    return read_stash_page


def make_page_writer(extra: int):
    def write_stash_page(w, page, v2, float_xy=False):
        w.begin_block(0)
        w.write_u32(page["width"])
        w.write_u32(page["height"])
        w.write_u32(len(page["items"]))
        for it in page["items"]:
            w.hooks.write_item(w, it, v2)
            if float_xy:
                w.write_float(it["x"])
                w.write_float(it["y"])
            else:
                w.write_u32(it["x"])
                w.write_u32(it["y"])
        if v2:
            for v in page.get("extra", [0] * extra):
                w.write_u32(v)
        w.end_block()
    return write_stash_page


def layout(tail: int, skill_extra: int, page_extra: int) -> S.Hooks:
    """Hooks for one layout variant (number fields per item, extra bytes per skill, extra fields per
    stash page)."""
    return S.Hooks(read_item=make_item_reader(tail), write_item=make_item_writer(),
                   read_skill=make_skill_reader(skill_extra), write_skill=make_skill_writer(skill_extra),
                   read_stash_page=make_page_reader(page_extra),
                   write_stash_page=make_page_writer(page_extra))


def attempt(data, tail, skill_extra, page_extra, roundtrip=False) -> dict:
    hooks = layout(tail, skill_extra, page_extra)
    try:
        doc = S.parse_character(data, hooks)
        open_blocks = sum(1 for b in doc["blocks"] if b.get("_extra"))
        rt = None
        if roundtrip:
            try:
                rt = (S.write_character(doc, hooks) == data)
            except Exception:                                   # noqa: BLE001
                rt = False
        return {"ok": True, "restfelder": open_blocks, "roundtrip": rt, "doc": doc}
    except Exception as e:                                      # noqa: BLE001
        return {"ok": False, "fehler": f"{type(e).__name__}: {e}"}


def search(data, known, out=print) -> list:
    out()
    out("  Searching for the matching layout")
    out("  (number fields per item x extra bytes per skill x extra fields per stash page)")
    out()
    hits = []
    for tail, skill_extra, page_extra in itertools.product(range(0, 10), range(0, 4), range(0, 10)):
        res = attempt(data, tail, skill_extra, page_extra)
        if not res["ok"]:
            continue
        hits.append((tail, skill_extra, page_extra))
        mark = "   (= bisherige Annahme)" if (tail, skill_extra, page_extra) == known else ""
        out(f"    Items {tail}, Skills {skill_extra}, Stash {page_extra}:  "
            f"parses completely, {res['restfelder']} blocks with trailing fields{mark}")
    if not hits:
        out("    None of the 400 combinations parses the file completely.")
        out("    The change is elsewhere - please send the output back.")
    return hits


def compare_backups(path: Path, data: bytes, out=print) -> None:
    """Check the backup copies in the same folder too: Grim Dawn keeps the previous state as
    player.g00 (or .g01). If a backup still parses but the current file does not, the format change
    happened between the two saves - that narrows the cause down a lot."""
    backups = sorted(p for p in path.parent.glob("player.g*") if p.suffix.lower() != ".gdc" and p.is_file())
    if not backups:
        return
    out()
    out("  Backup copies in the same folder:")
    for b in backups:
        try:
            raw = b.read_bytes()
        except OSError as e:
            out(f"    {b.name}: not readable ({e})")
            continue
        rep = walk(raw)
        # UTC with its label: the same output on every machine (a diagnostic, not a user clock)
        stamp = datetime.fromtimestamp(b.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if rep.get("header_error"):
            out(f"    {b.name}  {len(raw):>8} bytes  {stamp}  -> not readable either ({rep['header_error']})")
        elif rep.get("fehler"):
            last = rep["blocks"][-1]["id"] if rep["blocks"] else "-"
            out(f"    {b.name}  {len(raw):>8} bytes  {stamp}  "
                f"-> also aborts ({rep['fehler']}, last block {last})")
        else:
            out(f"    {b.name}  {len(raw):>8} bytes  {stamp}  "
                f"-> parses COMPLETELY with the previous layout")
            out("       So the format change lies between this and the current save.")


EXIT_NO_FILE = 1          # the file is missing or cannot be read
EXIT_NOT_A_SAVE = 3       # shorter than the character header / header damaged: nothing to diagnose


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv:
        sys.exit('Usage: python -m gdcheck.app.save_diag "<path to player.gdc>"')
    path = Path(argv[0])
    if path.is_dir():
        path = path / "player.gdc"
    try:
        data = path.read_bytes()
    except OSError as e:
        print("=" * 74)
        print(f"  Diagnosis: {path.name}")
        print("=" * 74)
        why = "does not exist" if not path.exists() else f"cannot be read ({type(e).__name__}: {e.strerror or e})"
        print(f"  ! The file {path} {why}.")
        print("    Give the path of a player.gdc (or of its character folder).")
        return EXIT_NO_FILE

    print("=" * 74)
    print(f"  Diagnosis: {path.name}   ({len(data)} bytes)")
    print("=" * 74)

    rep = walk(data)
    if rep.get("header_error"):
        print("  ! Not a readable Grim Dawn save: the character header ends early or is damaged")
        print(f"    ({rep['header_error']}).")
        print("    The file is shorter than the header or no player.gdc - a layout search makes no sense.")
        print("    Use the backup player.g00 next to it, or send this output back.")
        compare_backups(path, data)
        return EXIT_NOT_A_SAVE
    h = rep.get("header") or {}
    print(f"  Magic ok:          {rep.get('magic_ok')}")
    print(f"  preamble_version:  {rep.get('preamble_version')}")
    print(f"  Header checksum:   {rep.get('header_checksum')}")
    print(f"  Character:         {h.get('name')}  Level {h.get('level')}  {h.get('class_tag')}")
    print(f"  expansion_status:  {h.get('expansion_status')}")
    print(f"  data_version:      {rep.get('data_version')}  (known: {sorted(S.KNOWN_DATA_VERSIONS)})")

    print()
    print("  Blocks up to the abort:")
    print(f"    {'ID':>3} {'Vers':>5} {'Offset':>9} {'Length':>9}  Checksum | Status")
    for b in rep["blocks"]:
        print(f"    {b['id']:>3} {str(b['version']):>5} {b['offset']:>9} "
              f"{b['laenge']:>9}  {b['pruefsumme']} | {b['status']}")
    if not rep["fehler"]:
        print("\n  File already parses completely - no layout problem.")
        return 0
    print(f"\n  ! Aborted in: {rep['fehler']}")

    known = (5, 1, 5)                  # what the save reader assumed in .35
    hits = search(data, known)
    if not hits:
        return 0

    print()
    print("  Round-trip proof (re-encrypt and compare with the original):")
    proven = []
    for tail, skill_extra, page_extra in hits:
        res = attempt(data, tail, skill_extra, page_extra, roundtrip=True)
        ok = bool(res.get("roundtrip"))
        print(f"    Items {tail}, Skills {skill_extra}, Stash {page_extra}:  "
              f"{'byte-identical, proven' if ok else 'round trip differs'}")
        if ok:
            proven.append((tail, skill_extra, page_extra))

    print()
    if proven:
        t, s, p = proven[0]
        print(f"  RESULT: {t} number fields per item, {s} extra byte(s) per skill, "
              f"{p} extra fields per stash page")
        if len(proven) > 1:
            print(f"  (ambiguous, also possible: {proven[1:]})")
    else:
        print("  No candidate passes the round trip.")
    compare_backups(path, data)

    print()
    print("  Please send this output back completely.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
