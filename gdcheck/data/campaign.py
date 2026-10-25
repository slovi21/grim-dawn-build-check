"""Campaign progress per difficulty from quests.gdd (port of .35 kampagne.py).

Token -> campaign part (30.09.2026, from the game's achievement texts and the
token sequences of 19 characters): main game done = FINALBOSS_SLAIN, AoM =
AREAF_SHAPEROFFLESH_KILLED, FG = AREAG_KORVAAK_KILLED, FoA: only 'started'
is detectable (no token for Prodromus). Short form 'M a f r': capital = done,
small = started, '-' = not started."""
from __future__ import annotations

from pathlib import Path

from .savefile import GDFormatError, GDReader

TEILE = (
    # Kuerzel, Name, Praefixe fuer "begonnen", Token fuer "fertig"
    ("M", "Main game", ("DC_", "WM_", "HS_", "AREAC_", "AREAD_", "NECROPOLIS", "FORTIKON",
                         "WIGHTMIRE", "BURRWITCH", "WARDEN_"), "FINALBOSS_SLAIN"),
    ("A", "Ashes of Malmouth", ("AREAE_", "AREAF_", "GDX1_STARTED"), "AREAF_SHAPEROFFLESH_KILLED"),
    ("F", "Forgotten Gods", ("AREAG_",), "AREAG_KORVAAK_KILLED"),
    ("R", "Fangs of Asterkarn", ("AREAH_",), None),
)
SCHWIERIGKEITEN = ("Normal", "Elite", "Ultimate")


def tokens_lesen(pfad) -> list[str]:
    """Quest-Tokens (Block 10) einer quests.gdd."""
    d = Path(pfad).read_bytes()
    r = GDReader(d)
    if r.read_u32() != 0x58545351:                     # 'QSTX'
        raise GDFormatError("not a quests.gdd (magic QSTX missing)")
    r.read_u32()
    r.read_bytes(16)
    bid, end = r.begin_block()
    r.read_u32()                                        # Version des Blocks
    n = r.read_u32()
    out = [r.read_str() for _ in range(n)]
    r.end_block(bid, end)
    return out


def stand(tokens) -> dict:
    """{Kuerzel: "done" | "started" | "-"} und Akt 1."""
    t = set(tokens)
    out = {}
    for kurz, _name, praefixe, fertig in TEILE:
        if fertig and fertig in t:
            out[kurz] = "done"
        elif any(x.startswith(praefixe) for x in t):
            out[kurz] = "started"
        else:
            out[kurz] = "-"
    out["act1"] = "WARDEN_SLAIN" in t
    return out


def ordner_finden(gdc: Path) -> Path | None:
    """levels_world001.map neben der player.gdc (Spielordner) oder im Export von
    GD-Tools (<Name>.gdc + <Name>/levels_world001.map)."""
    gdc = Path(gdc)
    for kand in (gdc.parent / "levels_world001.map",
                 gdc.parent / gdc.stem / "levels_world001.map"):
        if kand.is_dir():
            return kand
    return None


def read(gdc) -> dict:
    """{Schwierigkeit: stand} fuer alle vorhandenen quests.gdd; {} ohne Dateien."""
    ordner = ordner_finden(gdc)
    if not ordner:
        return {}
    out = {}
    for s in SCHWIERIGKEITEN:
        q = ordner / s / "quests.gdd"
        if q.is_file():
            try:
                out[s] = stand(tokens_lesen(q))
            except (GDFormatError, OSError):
                continue
    return out


def short_form(st: dict | None) -> str:
    """'M a - r': Grossbuchstabe = fertig, klein = begonnen, '-' = nicht."""
    if not st:
        return "?"
    teile = []
    for kurz, *_ in TEILE:
        w = st.get(kurz, "-")
        teile.append(kurz if w == "done" else (kurz.lower() if w == "started" else "-"))
    return " ".join(teile)


def long_form(st: dict | None) -> str:
    if not st:
        return "no quest data"
    wort = {"done": "done", "started": "started", "-": "not started"}
    return ", ".join(f"{name} {wort[st.get(kurz, '-')]}" for kurz, name, *_ in TEILE)
