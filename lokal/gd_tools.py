#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gd_tools.py — Bedienmenue fuer die Grim-Dawn-Werkzeuge
======================================================

Findet Spielinstallation, Spielversion und Charaktere selbst und ruft
die Werkzeuge auf (Charakterbericht: gdcheck.app.save_report, Index: gdcheck.app.index_build).
Ersetzt das frueher hier verwendete PowerShell-Skript: dieselbe Aufgabe, aber in derselben
Sprache wie die Werkzeuge — dadurch testbar und ohne Windows-Eigenheiten bei Kodierung,
Ausfuehrungsrichtlinie und Laufwerksaufloesung.

Start:  Doppelklick auf GD-Tools.bat
        oder  python gd_tools.py

Aufbau (wird bei jedem Update komplett ersetzt, nie von Hand bearbeiten):
    ClaudeSkill\\GD-Tools.bat, Tests.bat, gd_tools.py
    ClaudeSkill\\gdcheck\\   Engine; PC-Werkzeuge in gdcheck\\app\\ (save_report, index_build, record_view, save_diag, ...)
    ClaudeSkill\\tests\\     Tests des Skills

Eigene Daten liegen AUSSERHALB, damit das Ersetzen nichts loescht:
    ClaudeSkill-Daten\\gd_tools.json   Einstellungen
    ClaudeSkill-Daten\\out\\           Berichte, JSON, Spieldaten-Index
Beim ersten Start nach dem Umbau werden alte Daten aus ClaudeSkill\\out\\
und ClaudeSkill\\gd_tools.json automatisch dorthin verschoben.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Product mode (public installer): only the installer's start.cmd sets GDCHECK_PRODUCT=1. Read from the
# environment here because nothing of gdcheck may be imported before skripte_pruefen(). Without the flag
# the launcher behaves exactly as it always did (GD-Tools.bat, ClaudeSkill.zip).
PRODUCT = os.environ.get("GDCHECK_PRODUCT") == "1"
PRODUCT_NAME = "Grim Dawn Build Check"            # = gdcheck.app.product.NAME (a test keeps them equal)
LAUNCHER = PRODUCT_NAME if PRODUCT else "GD-Tools"   # what the hints call the program the player starts
# The bundled embeddable Python runs isolated and ignores PYTHON* variables, so UTF-8 mode and unbuffered
# output go on the command line (before -m); kind_env() sets the same through the environment.
PY_FLAGS = ("-X", "utf8", "-u") if PRODUCT else ()
# Root of the tools: the folder that contains gdcheck\ (ClaudeSkill\). Every tool is a gdcheck module
# started with python -m (.68: the .35 code of gdcheck\legacy35\ is gone).
ROOT = HERE
# Nothing runs without these files (paths below ClaudeSkill\) - checked at the start (skripte_pruefen), so a
# partial extraction shows the help text instead of an ImportError (local_env/fingerprint: review .67).
# .68: character report, index build, record view, save diagnosis and page check are gdcheck.app modules;
# every child tool starts through gdcheck.app.safe_run (needs gdcheck.app.errors and the package files),
# the character check is gdcheck/__main__.py.
PFLICHT = (
    "gdcheck/__main__.py", "gdcheck/app/__init__.py", "gdcheck/app/safe_run.py", "gdcheck/app/errors.py",
    "gdcheck/data/__init__.py",
    "gdcheck/app/local_env.py", "gdcheck/data/fingerprint.py", "gdcheck/app/index_build.py",
    "gdcheck/app/record_view.py", "gdcheck/app/save_diag.py", "gdcheck/app/page_check.py",
    "gdcheck/app/save_report.py", "gdcheck/app/product.py", "gdcheck/data/arz.py", "gdcheck/data/savefile.py")
# Files of the old scripts\ folder (up to .43) - removed on start, only these names.
ALTE_SKRIPTE = ("affixe.py", "assess.py", "attribute.py", "befund_layout.py", "bericht_html.py", "bossdaten.py",
                "build_index.py", "db_record.py", "devotionen.py", "gd_diag.py", "gd_extract.py", "gd_indexdb.py",
                "gd_labels.py", "kampagne.py", "kampf.py", "mechanik_seite.py", "pruefungen.py", "schaden.py",
                "skillpunkte.py", "treffer.py", "verlauf.py", "wirkdauer.py", "ziele.py", "build_index_test.py")
# Files of earlier releases that the current one no longer ships, by folder (paths below ClaudeSkill\).
# Unpacking a new ClaudeSkill.zip over an old folder keeps them; on start they are removed (.68): only
# these file names and their compiled copies in __pycache__, then each folder if it is empty. Foreign
# files stay. .68 removed the .35 code (gdcheck\legacy35\, gdcheck\analysis\_legacy\) with its
# legacy tests, contract fixtures and tools (git diff --name-status <.67> <.68> | grep ^D: 51 files;
# .66 -> .67 deleted none). .69: _legacy\devotions.py (adapter of the parallel line .67 = ClaudeSkill 37) is
# gone too, analysis\design\devotions.py replaces it. A legacy test module left behind would fail the test
# run (test_no_legacy_package reads this literal with ast - keep it a plain dict of tuples).
ALTE_ORDNER = {
    "gdcheck/legacy35": ("__init__.py", "affixe.py", "assess_core.py", "attribute.py", "bossdaten.py",
                         "build_index.py", "db_record.py", "devotionen.py", "gd_diag.py", "gd_extract.py",
                         "gd_indexdb.py", "gd_labels.py", "kampf.py", "mechanik_seite.py", "pruefungen.py",
                         "schaden.py", "skillpunkte.py", "wirkdauer.py", "ziele.py"),
    "gdcheck/analysis/_legacy": ("__init__.py", "devotions.py", "moves.py", "rules.py"),     # devotions.py: ClaudeSkill 37
    "gdcheck/analysis/proposals": ("legacy_moves.py",),
    "tests": ("boss_fixture.json", "dump_parse.py", "fake_arz.py", "indexsuche.py", "referenz_cpt_wolf.json"),
    "tests/legacy35": ("legacy_affixe.py", "legacy_format.py", "legacy_kampf.py", "legacy_pruefungen.py",
                       "legacy_punkte.py", "legacy_referenz.py", "legacy_wirkdauer.py", "legacy_zielwerte.py",
                       "test_legacy35.py"),
    "tests/fixtures/legacy_parity": ("contract_2026-10-01.46.json", "contract_2026-10-02.49.json",
                                     "contract_2026-10-02.51.json", "contract_2026-10-03.59.json",
                                     "contract_2026-10-03.61.json", "contract_2026-10-03.62.json",
                                     "contract_2026-10-03.63.json", "contract_2026-10-03.64.json",
                                     "contract_2026-10-03.65.json", "contract_2026-10-03.66.json",
                                     "contract_2026-10-03.67.json"),
    "tests/regression": ("test_legacy_contract.py",),
    "tests/unit": ("test_legacy_moves.py",),
    "tools": ("legacy_contract_export.py",),
}


def _fehlt(root: Path) -> list[str]:
    if not (root / "gdcheck" / "__init__.py").is_file():
        return ["gdcheck\\__init__.py"]
    return [n for n in PFLICHT if not (root / n).is_file()]


def skripte_pruefen() -> bool:
    """Is ClaudeSkill complete (gdcheck\\ with the tool modules)? Otherwise detect a nested
    extraction (Windows 'Extract all' creates ClaudeSkill\\ClaudeSkill\\) and use that,
    or say clearly what to do."""
    global ROOT
    fehlt = _fehlt(ROOT)
    if not fehlt:
        return True
    for kand in () if PRODUCT else (HERE / "ClaudeSkill", HERE.parent / "ClaudeSkill"):
        if kand != ROOT and not _fehlt(kand):
            print(f"  [!]      ClaudeSkill found in {kand} - using it. Better: extract")
            print("           ClaudeSkill.zip once more into the Grim Dawn folder.")
            ROOT = kand
            return True
    if PRODUCT:
        print(f"  [error]  {PRODUCT_NAME} is incomplete - missing in {ROOT}:")
        print("           " + ", ".join(fehlt))
        print(f"           Fix: run the {PRODUCT_NAME} installer again (your data stays).")
        return False
    print(f"  [error]  ClaudeSkill is incomplete - missing in {ROOT}:")
    print("           " + ", ".join(fehlt))
    print("           Fix: extract ClaudeSkill.zip INTO THE GRIM DAWN FOLDER (the zip already")
    print("           contains the folder ClaudeSkill\\).")
    return False


def alte_skripte_entfernen() -> list[str]:
    """Remove the old scripts\\ folder (up to .43) and the files of earlier releases (ALTE_ORDNER: the .35
    code, legacy tests, contract fixtures and tools of .44-.67): only the known file names (and their
    compiled copies), then each folder if it is empty. Foreign files stay. Returns the removed files as
    paths below ClaudeSkill\\ ('scripts/assess.py', 'gdcheck/legacy35/kampf.py'); .pyc copies are not counted."""
    alt = HERE / "scripts"
    weg = []
    if alt.is_dir():
        for name in ALTE_SKRIPTE:
            f = alt / name
            if f.is_file():
                try:
                    f.unlink()
                    weg.append(f"scripts/{name}")
                except OSError:
                    pass
        shutil.rmtree(alt / "__pycache__", ignore_errors=True)
        try:
            alt.rmdir()
        except OSError:
            pass
    for basis in dict.fromkeys((HERE, ROOT)):
        for rel, namen in ALTE_ORDNER.items():
            weg += [f"{rel}/{n}" for n in _ordner_leeren(basis / rel, namen)]
    return weg


def _ordner_leeren(ordner: Path, namen: tuple[str, ...]) -> list[str]:
    """Delete the known files of a folder, their .pyc in __pycache__, then __pycache__ and the folder if
    they are empty (a folder that still holds current or foreign files stays)."""
    weg = []
    if not ordner.is_dir():
        return weg
    for name in namen:
        f = ordner / name
        if f.is_file():
            try:
                f.unlink()
                weg.append(name)
            except OSError:
                pass
    cache = ordner / "__pycache__"
    if cache.is_dir():
        stems = {Path(n).stem for n in namen if n.endswith(".py")}
        for f in cache.glob("*.pyc"):
            if f.name.split(".", 1)[0] in stems:
                try:
                    f.unlink()
                except OSError:
                    pass
        try:
            cache.rmdir()
        except OSError:
            pass
    try:
        ordner.rmdir()
    except OSError:
        pass
    return weg


CONTRACT_ORDNER = "tests/fixtures/report_contract"
CONTRACT_NAME = re.compile(r"contract_\d{4}-\d{2}-\d{2}\.\d+\.json")


def alte_contracts_entfernen() -> list[str]:
    """.69: the report contract baseline is versioned (contract_<version>.json, one file per package). A new
    zip unpacked over an older folder keeps the old one - remove every versioned contract_*.json except the own
    version (VERSION.txt). Only if the own baseline is there (never leave the folder without one), only names
    of this pattern (foreign files stay). The differential baselines (actions_*, rotation_*) are not touched:
    the tests take the newest file and the release ships the history. Returns the removed paths."""
    version = None
    for basis in dict.fromkeys((ROOT, HERE)):
        try:
            version = (basis / "VERSION.txt").read_text(encoding="utf-8").strip() or None
        except OSError:
            continue
        if version:
            break
    if not version:
        return []
    weg = []
    for basis in dict.fromkeys((HERE, ROOT)):
        ordner = basis / CONTRACT_ORDNER
        if not (ordner / f"contract_{version}.json").is_file():
            continue
        for f in sorted(ordner.glob("contract_*.json")):
            if f.name != f"contract_{version}.json" and CONTRACT_NAME.fullmatch(f.name):
                try:
                    f.unlink()
                    weg.append(f"{CONTRACT_ORDNER}/{f.name}")
                except OSError:
                    pass
    return weg


def aufraeumen_meldung(weg: list[str]) -> list[str]:
    """Lines for the start screen: how many files of which kind were removed (counted per file)."""
    skripte = [n for n in weg if n.startswith("scripts/")]
    code = [n for n in weg if n.startswith("gdcheck/")]
    rest = [n for n in weg if n not in skripte and n not in code]
    out = []
    if skripte:
        out.append(f"old scripts\\ folder cleaned: {len(skripte)} file(s) removed (the tools are in gdcheck now)")
    if code:
        out.append(f"old .35 code removed from gdcheck\\: {len(code)} file(s) (replaced by the new modules)")
    if rest:
        out.append(f"old legacy tests and tools removed from tests\\ and tools\\: {len(rest)} file(s)")
    return out


def datenordner(basis: Path) -> Path:
    """User data folder next to ClaudeSkill\\. The German edition called it
    ClaudeSkill-Daten; it is renamed once, or - if both exist - whatever is
    missing in the new folder is copied over (never overwritten)."""
    import shutil
    neu, alt = basis / "ClaudeSkill-Data", basis / "ClaudeSkill-Daten"
    try:
        return _datenordner(neu, alt, shutil)
    except OSError:                         # never stop the start because of the data folder
        return neu


def _datenordner(neu: Path, alt: Path, shutil) -> Path:
    if alt.is_dir() and not neu.exists():
        try:
            alt.rename(neu)
        except OSError:
            return alt
    elif alt.is_dir() and neu.is_dir():
        for f in alt.rglob("*"):
            ziel = neu / f.relative_to(alt)
            if f.is_file() and not ziel.exists():
                ziel.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(f, ziel)
                except OSError:
                    pass
    return neu


# Product mode: the installer's start.cmd names the data folder. Without the flag GDCHECK_DATA_DIR is
# ignored - patch_bundle.py lets skill users set it for their own purposes, GD-Tools must not follow.
DATEN = (Path(os.environ["GDCHECK_DATA_DIR"]) if PRODUCT and os.environ.get("GDCHECK_DATA_DIR")
         else datenordner(HERE.parent))
OUT = DATEN / "out"
SETTINGS = DATEN / "gd_tools.json"
LOGS = OUT / "logs"


def _errors():
    """gdcheck.app.errors (friendly texts) - None if the gdcheck folder is broken."""
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from gdcheck.app import errors
        return errors
    except Exception:                       # noqa: BLE001 - the texts must work without it
        return None


def _product():
    """gdcheck.app.product (texts of the public installer) - only used in product mode, after skripte_pruefen."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from gdcheck.app import product
    return product


def erklaere(e: BaseException) -> str:
    """One line for a warning: plain title + what to do + technical detail."""
    ERR = _errors()
    if ERR is None:
        return f"{type(e).__name__}: {e}"
    _k, title, _cause, fix = ERR.explain(e)
    return f"{title}. {fix} (Technical: {type(e).__name__}: {str(e)[:120]})"


def problem(e: BaseException, wo: str) -> int:
    """Framed explanation for an unexpected error inside GD-Tools (with a log file); returns the code."""
    ERR = _errors()
    if ERR is not None:
        os.environ.setdefault("GDCHECK_LOG_DIR", str(LOGS))
        return ERR.report(e, wo, stream=sys.stdout)
    print(f"\n  PROBLEM in {wo}: {type(e).__name__}: {e}")
    if PRODUCT:
        print(f"  {PRODUCT_NAME} may be incomplete - run its installer again (your data stays).")
    else:
        print("  The ClaudeSkill folder may be incomplete - unpack ClaudeSkill.zip again.")
    return 70


def daten_umziehen() -> list[str]:
    """Verschiebt Nutzerdaten einmalig aus ClaudeSkill\\ nach ClaudeSkill-Daten\\.

    Vorhandene Dateien am Ziel werden nie ueberschrieben."""
    import shutil
    meldungen = []
    OUT.mkdir(parents=True, exist_ok=True)
    alt_out = HERE / "out"
    if alt_out.is_dir():
        for f in alt_out.iterdir():
            ziel = OUT / f.name
            if not ziel.exists():
                shutil.move(str(f), str(ziel))
                meldungen.append(f"out\\{f.name}")
        try:
            alt_out.rmdir()
        except OSError:
            pass
    alt_cfg = HERE / "gd_tools.json"
    if alt_cfg.is_file():
        if not SETTINGS.exists():
            shutil.move(str(alt_cfg), str(SETTINGS))
            meldungen.append("gd_tools.json")
    return meldungen


def alte_dateien_aufraeumen() -> list[str]:
    """Start-up cleanup after an update unpacked over an older folder: remains of the old flat layout
    (all scripts directly in ClaudeSkill\\), the old scripts\\ folder and the files of earlier releases
    (ALTE_ORDNER). Only known names - foreign files stay. Returns the lines for the start screen."""
    flach = []
    for name in ("gd_extract.py", "gd_diag.py", "gd_labels.py", "build_index.py",
                 "pruefungen.py", "zielwerte.md", "GD-Tools.ps1"):
        f = HERE / name
        if f.is_file():
            try:
                f.unlink()
                flach.append(name)
            except OSError:
                pass
    shutil.rmtree(HERE / "__pycache__", ignore_errors=True)
    out = [f"old file removed: {name}" for name in flach]
    out += aufraeumen_meldung(alte_skripte_entfernen())
    contracts = alte_contracts_entfernen()
    if contracts:
        out.append(f"old report contract baselines removed from tests\\fixtures\\report_contract\\: "
                   f"{len(contracts)} file(s)")
    return out


def einstellungen_laden() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def einstellungen_speichern(daten: dict) -> None:
    try:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)       # first product start: the data folder is not there yet
        SETTINGS.write_text(json.dumps(daten, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    except OSError as e:
        warn(f"Settings could not be saved: {erklaere(e)}")


def _local():
    """gdcheck.app.local_env: the search functions shared with the skill's local mode (.67). Imported
    only when used - after skripte_pruefen() - so an incomplete ClaudeSkill folder still shows its help."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from gdcheck.app import local_env
    return local_env


# ---------------------------------------------------------------------------
# Ausgabe
# ---------------------------------------------------------------------------

def head(text):
    print()
    print("=" * 70)
    print(f"  {text}")
    print("=" * 70)


def ok(t):
    print(f"  [ok]     {t}")


def warn(t):
    print(f"  [!]      {t}")


def bad(t):
    print(f"  [error]  {t}")


def exists(p) -> bool:
    """Existenzpruefung, die bei fehlendem Laufwerk nicht knallt."""
    try:
        return Path(p).exists()
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Installation finden
# ---------------------------------------------------------------------------

def is_gd_dir(p) -> bool:
    return _local().is_game_dir(p)


# Wird in main() gesetzt, sobald die Installation gefunden ist. Daraus laesst
# sich der Steam-Ordner ableiten, auch wenn er auf E: oder unter "Program
# Files (x86)" eines anderen Laufwerks liegt.
GD_DIR: Path | None = None


def steam_roots() -> list[Path]:
    """Possible Steam main folders (userdata\\<ID>\\219990 lives there): derived from the installation,
    registry, fixed list, default folders on C: to H: (gdcheck.app.local_env.steam_roots)."""
    return _local().steam_roots(GD_DIR)


def steam_libraries() -> list[Path]:
    """steamapps\\common folders of all Steam libraries."""
    return _local().steam_libraries(steam_roots())


def find_game_dir(hint=None) -> Path | None:
    """Order: --gd-dir > folders above the script (ClaudeSkill inside the installation) > Steam >
    default paths."""
    if hint and not is_gd_dir(hint):
        warn(f"--gd-dir does not point to an installation: {Path(hint)}")
    le = _local()

    def libraries():                        # read Steam's files only when the folders above do not fit
        yield from steam_libraries()
    return le.find_game_dir(hint, [HERE] + list(HERE.parents)[:3], libraries(), le.fallback_game_dirs(),
                            le.gog_game_dirs())


def remembered_game_dir(cfg: dict) -> str | None:
    """The folder typed by hand on an earlier start (cfg["gd_dir"]) - only while it still is a game folder."""
    saved = cfg.get("gd_dir")
    return saved if isinstance(saved, str) and saved and is_gd_dir(saved) else None



VERSION_MUSTER = re.compile(r"^\d{1,3}(\.\d{1,5}){1,3}$")


def _version_kandidaten(data: bytes) -> list[tuple[str, str]]:
    """Alle VS_FIXEDFILEINFO-Bloecke einer exe auswerten.

    Aufbau ab der Signatur 0xFEEF04BD:
        +0  dwSignature        +4  dwStrucVersion
        +8  dwFileVersionMS   +12  dwFileVersionLS
       +16  dwProductVersionMS +20 dwProductVersionLS

    Crate fuellt FileVersion und ProductVersion NICHT gleich, und die
    Signaturbytes koennen zufaellig auch anderswo in der Datei stehen.
    Deshalb werden alle Fundstellen gesammelt statt der ersten vertraut."""
    out = []
    pos = 0
    sig = b"\xbd\x04\xef\xfe"
    while True:
        idx = data.find(sig, pos)
        if idx < 0 or idx + 24 > len(data):
            break
        pos = idx + 4
        struc = struct.unpack_from("<I", data, idx + 4)[0]
        if struc >> 16 == 0:              # dwStrucVersion sollte >= 1.0 sein
            continue
        for label, off in (("FileVersion (Struktur)", 8),
                           ("ProductVersion (Struktur)", 16)):
            ms, ls = struct.unpack_from("<II", data, idx + off)
            teile = (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
            if not any(teile):
                continue
            out.append((label, ".".join(str(x) for x in teile)))

    # Zusaetzlich der Textblock StringFileInfo: dort koennen andere Werte
    # stehen als in der festen Struktur.
    for schluessel in ("ProductVersion", "FileVersion"):
        muster = schluessel.encode("utf-16-le") + b"\x00\x00"
        p = 0
        while True:
            i = data.find(muster, p)
            if i < 0:
                break
            p = i + len(muster)
            roh = data[p:p + 80]
            try:
                text = roh.decode("utf-16-le", errors="ignore")
            except Exception:
                break
            text = text.split("\x00")[0].strip()
            if text and VERSION_MUSTER.match(text):
                out.append((f"{schluessel} (Text)", text))
    return out


def exe_version(gd: Path, alle: bool = False):
    """Spielversion aus der exe lesen.

    Bevorzugt ProductVersion: bei Grim Dawn steht in FileVersion eine andere,
    intern gefuehrte Nummer (beobachtet: 0.3.0.0, waehrend das Spiel 1.3.0.8
    anzeigt). Kandidaten, deren erste Zahl 0 ist, gelten daher als unplausibel."""
    kandidaten = []
    for rel in ("Grim Dawn.exe", r"x64\Grim Dawn.exe", "GrimDawn.exe"):
        p = gd / rel
        if not exists(p):
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        for label, v in _version_kandidaten(data):
            kandidaten.append((rel, label, v))
    if alle:
        return kandidaten

    def rang(eintrag):
        _, label, v = eintrag
        erste = int(v.split(".")[0])
        return (0 if erste > 0 else 1,                 # 0.x.y.z ist unplausibel
                0 if label == "ProductVersion" else 1)  # ProductVersion bevorzugen

    kandidaten.sort(key=rang)
    return kandidaten[0][2] if kandidaten else None


# ---------------------------------------------------------------------------
# Spielstaende finden
# ---------------------------------------------------------------------------

def steam_buildid(gd: Path) -> str | None:
    """BuildID aus Steams appmanifest_219990.acf.

    Das ist nicht die Spielversion, aendert sich aber bei jedem Patch. Damit
    laesst sich zuverlaessig erkennen, DASS ein Patch kam - auch wenn die
    Versionsnummer in der exe nicht gepflegt ist."""
    try:
        steamapps = gd.parent.parent          # ...\steamapps\common\Grim Dawn
        acf = steamapps / "appmanifest_219990.acf"
        if not exists(acf):
            return None
        text = acf.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'"buildid"\s+"(\d+)"', text)
        return m.group(1) if m else None
    except (OSError, ValueError):
        return None


def _steam_updated(gd: Path) -> float | None:
    """LastUpdated (epoch seconds) from Steam's appmanifest_219990.acf: when Steam last changed the game files."""
    try:
        acf = gd.parent.parent / "appmanifest_219990.acf"
        if not exists(acf):
            return None
        m = re.search(r'"LastUpdated"\s+"(\d+)"', acf.read_text(encoding="utf-8", errors="replace"))
        return float(m.group(1)) if m else None
    except (OSError, ValueError):
        return None


def game_files_time(gd: Path) -> float | None:
    """When the game files were last changed: the newest of the executables' mtimes and Steam's LastUpdated."""
    times = []
    for rel in ("Grim Dawn.exe", r"x64\Grim Dawn.exe"):
        try:
            times.append((gd / rel).stat().st_mtime)
        except OSError:
            pass
    times.append(_steam_updated(gd))
    times = [t for t in times if t]
    return max(times) if times else None


def log_is_stale(gd: Path, log: Path) -> bool:
    """The game rewrites log.html at every start. A log older than the game files was written before the last
    patch (Steam updates the game without starting it), so the version in it is the previous one."""
    try:
        written = log.stat().st_mtime
    except OSError:
        return False
    changed = game_files_time(gd)
    return changed is not None and written < changed


def version_bestimmen(gd: Path, vorgabe: str | None, cfg: dict) -> tuple[str | None, str | None, bool]:
    """(version, source, confirmed) of the installed game and the matching entry in cfg. Order: command line >
    log.html (only while it is not older than the game files) > the version in the exe (when plausible, first
    number above 0) > the stale log.html with a warning (not stored, and the only unconfirmed result) > the
    saved version."""
    if vorgabe:
        return vorgabe, "command line", True
    aus_log = log_version(gd)
    if aus_log:
        version, datei = aus_log
        quelle = f"log.html ({datei.parent})"
        if not log_is_stale(gd, datei):
            cfg["version"] = version
            return version, quelle, True
        exe = exe_version(gd)
        if exe and exe.split(".")[0].isdigit() and int(exe.split(".")[0]) > 0:
            cfg["version"] = exe
            return exe, "Grim Dawn.exe (log.html is older than the game files)", True
        warn("log.html is older than the game files - the version label may be outdated. "
             "Start Grim Dawn once to refresh it.")
        return version, quelle + ", older than the game files", False
    if cfg.get("version"):
        return cfg["version"], f"saved in {SETTINGS.name}", True
    return None, None, True


def steam_status(gd) -> dict | None:
    """Grim Dawn in Steam (.53): build id, branch (BetaKey: playtest/beta) and whether Steam is still
    updating (StateFlags). None without appmanifest_219990.acf (GOG, other folder)."""
    return _local().steam_status(gd)


def bekannter_ordner(folderid: str) -> Path | None:
    """Windows known folder from the shell API - "Documents" may be redirected anywhere."""
    return _local().known_folder(folderid)


def dokument_ordner() -> list[Path]:
    """Alle plausiblen Dokumentenordner, inklusive OneDrive-Umleitungen."""
    return _local().documents_folders()


def log_suchorte(gd: Path | None = None) -> list[Path]:
    """Orte, an denen Grim Dawn sein Protokoll ablegen kann."""
    return _local().log_places(dokument_ordner(), gd)


def _log_klartext(datei: Path, bytes_: int = 200_000) -> str:
    """Head of the game log as plain text (Grim Dawn writes HTML)."""
    return _local().log_text(datei, bytes_)


def log_version(gd: Path | None = None):
    """(Spielversion, Datei) aus dem juengsten log.html - die zuverlaessigste Quelle - oder None."""
    return _local().log_version(log_suchorte(gd))


def log_dateien(gd: Path | None = None) -> list[Path]:
    """Alle gefundenen Protokolldateien, juengste zuerst."""
    return _local().log_files(log_suchorte(gd))


def save_roots(hint=None) -> list[Path]:
    return _local().save_roots(hint, dokument_ordner(), steam_roots())


MAX_SAVE_DEPTH = 3


def characters(hint=None) -> list[dict]:
    """Alle Charakterordner unter den Save-Wurzeln (save\\main\\_<Name>, save\\user\\_<Name>, Mod-Zweige;
    bis MAX_SAVE_DEPTH Ebenen tief), juengste zuerst. Dieselbe Datei ueber zwei Wurzeln nur einmal."""
    return [{"name": c.name, "gdc": c.gdc, "zweig": c.branch, "quelle": c.root,
             "mtime": datetime.fromtimestamp(c.mtime)}
            for c in _local().characters(save_roots(hint), MAX_SAVE_DEPTH)]



# ---------------------------------------------------------------------------
# Aufrufe
# ---------------------------------------------------------------------------

def kind_env() -> dict:
    """Environment of a child tool: friendly error texts, log folder, unbuffered output."""
    return dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONPATH=str(ROOT),
                PYTHONUNBUFFERED="1", GDCHECK_FRIENDLY="1", GDCHECK_LOG_DIR=str(LOGS))


def _tee_datei(tee: Path):
    """Open the copy of the output; a locked file (open in Word/Excel, OneDrive) gets a new name."""
    try:
        tee.parent.mkdir(parents=True, exist_ok=True)
        return open(tee, "w", encoding="utf-8"), tee
    except OSError:
        anders = tee.with_name(f"{tee.stem}-{datetime.now():%H%M%S}{tee.suffix}")
        try:
            warn(f"{tee.name} is in use (open in another program?) - writing {anders.name} instead.")
            return open(anders, "w", encoding="utf-8"), anders
        except OSError as e:
            warn(f"No copy of the output written: {erklaere(e)}")
            return None, None


def werkzeug_name(module: str) -> str:
    """Readable tool name ('gdcheck.app.anim_frames' -> 'animation frames')."""
    ERR = _errors()
    return ERR.tool_name(module) if ERR else module.rsplit(".", 1)[-1].replace("_", " ")


def erklaere_code(rc: int, module: str) -> None:
    """After a child tool failed: what the exit code means for THIS tool and what to do (rc 70/130:
    the child already printed its own explanation)."""
    if rc in (0, 70):
        return
    if rc == 130:
        warn("Cancelled.")
        return
    ERR = _errors()
    text = ERR.exit_text(rc, module) if ERR else f"  {werkzeug_name(module)} ended with code {rc}."
    if text:
        print(text)


def run_module(module: str, args: list[str], tee: Path | None = None) -> int:
    """python -m <module> from the ClaudeSkill folder (package gdcheck, rebuild 2026-10-01), started
    through gdcheck.app.safe_run: a crash of the tool becomes a framed explanation + log file."""
    cmd = [sys.executable, *PY_FLAGS, "-m", "gdcheck.app.safe_run", module] + args
    shown = [sys.executable, "-m", module] + args
    env = kind_env()
    wo = werkzeug_name(module)
    print()
    print("  > " + " ".join(f'"{c}"' if " " in c else c for c in shown))
    print()
    fh = None
    if tee is not None:
        fh, _ = _tee_datei(tee)
    try:
        if fh is None:
            rc = subprocess.call(cmd, env=env, cwd=str(ROOT))
        else:
            with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                  encoding="utf-8", errors="replace", bufsize=1, env=env,
                                  cwd=str(ROOT)) as proc, fh:
                for line in proc.stdout:
                    sys.stdout.write(line)
                    fh.write(line)
                rc = proc.wait()
    except KeyboardInterrupt:
        rc = 130
    except OSError as e:                    # Python itself could not be started
        warn(f"{wo} could not be started: {erklaere(e)}")
        return 1
    erklaere_code(rc, module)
    return rc


KURZLISTE = 12


def pick_character(state, gemerkt: str | None = None) -> dict | None:
    """Charakter waehlen. Der zuletzt ausgewertete steht vorn und ist die
    Vorgabe, damit der Normalfall eine einzige Eingabetaste bleibt."""
    chars = characters(state["savedir"])
    if not chars:
        bad("No player.gdc found.")
        print(r"           Expected at: ...\save\main\_<Name>\player.gdc")
        print(r"           (locally under Documents\My Games\Grim Dawn, or in the")
        print(r"            Steam Cloud saves under Steam\userdata\<ID>\219990\remote)")
        print("           Somewhere else: x -> 11 (set save folder).")
        return None

    if gemerkt:
        treffer = [c for c in chars if c["name"] == gemerkt]
        if treffer:
            chars = treffer + [c for c in chars if c["name"] != gemerkt]

    # Charakteruebersicht gleich beim Start (Nutzer 30.09.2026): alle Werte
    # wie in overview-<Datum>.txt, nummeriert zur Auswahl.
    try:
        ue = uebersichten(state, chars)
    except Exception as e:                                      # noqa: BLE001
        warn(f"Overview not available: {erklaere(e)}")
        ue = {}
    alle = True if ue else False
    if ue:
        # nach Score sortiert (Nutzer 30.09.2026), hoechster zuerst
        def score(c):
            u = ue.get(str(c["gdc"])) or {}
            return (-(u.get("punkte") if isinstance(u.get("punkte"), int) else -1),
                    -(u.get("level") or 0), c["name"])
        chars = sorted(chars, key=score)
    while True:
        sichtbar = chars if alle else chars[:KURZLISTE]
        state["zuletzt_gezeigt"] = [c["name"] for c in sichtbar]     # numbers of the list shown last (Extras 14)
        print()
        if ue:
            print("  Characters (sorted by score):")
            zeilen = [uebersicht_zeile(i, c, ue.get(str(c["gdc"])))
                      for i, c in enumerate(sichtbar, 1)]
            for t in uebersicht_tabelle(zeilen):
                print("  " + t)
            import textwrap
            for t in textwrap.wrap(_engine_overview().LEGEND, 100):
                print("  " + t)
            try:
                (OUT / f"overview-{datetime.now():%Y-%m-%d}.txt").write_text(
                    "\n".join(uebersicht_tabelle(zeilen) + ["", _engine_overview().LEGEND]) + "\n",
                    encoding="utf-8")
            except OSError:
                pass
            if gemerkt:
                print(f"  Last evaluated: {gemerkt} (Enter)")
        else:
            print("  Characters (most recently played first):")
            for i, c in enumerate(sichtbar, 1):
                marke = "  <- last evaluated" if c["name"] == gemerkt else ""
                zweig = f"  [{c['zweig']}]" if len({x['zweig'] for x in chars}) > 1 else ""
                print(f"   {i:>2}) {c['name']:<24} {c['mtime']:%Y-%m-%d %H:%M}{zweig}{marke}")
            if not alle and len(chars) > KURZLISTE:
                print(f"    a) show all {len(chars)}")
        print()
        vorgabe_c = next((c for c in sichtbar if c["name"] == gemerkt), sichtbar[0])
        vorgabe = vorgabe_c["name"]
        antwort = input(f"  Number, part of name, Enter = {vorgabe}, "
                        f"x = extras, q = quit: ").strip()
        if antwort.lower() == "q":
            return "ende"

        if antwort.lower() == "x":
            return "extras"
        if not antwort:
            return vorgabe_c
        if antwort.lower() == "a" and not alle:
            alle = True
            continue
        if antwort.isdigit():
            n = int(antwort)
            if 1 <= n <= len(sichtbar):
                return sichtbar[n - 1]
            warn("Invalid number.")
            continue
        passend = [c for c in chars if antwort.lower() in c["name"].lower()]
        if len(passend) == 1:
            return passend[0]
        if not passend:
            warn(f"No character contains '{antwort}'.")
        else:
            warn(f"Ambiguous: {', '.join(c['name'] for c in passend[:6])}")


def action_extract(state, full=True, raw=False, gemerkt=None, char=None):
    head("Evaluate character")
    if char is None:
        char = pick_character(state, gemerkt)
    if isinstance(char, str):
        return char            # "extras" oder "ende"
    if char is None:
        return None
    stamp = datetime.now().strftime("%Y-%m-%d")
    safe = re.sub(r"[^\w-]", "_", char["name"])
    txt = OUT / f"{safe}-{stamp}.txt"
    args = [str(char["gdc"]), "--gd-dir", str(state["gd"]), "--lang", state["lang"],
            "--json", str(OUT / f"{safe}-{stamp}.json")]
    if full:
        args.append("-v")
    if raw:
        args += ["--raw-json", str(OUT / f"{safe}-{stamp}-raw.json")]
    # character text report (.68: gdcheck.app.save_report replaces the .35 gd_extract CLI, same options)
    if run_module("gdcheck.app.save_report", args, tee=txt) != 0:
        return None
    ok(f"Report: {txt}")
    build_pruefen(state, char, safe, stamp)
    return char["name"]


def build_pruefen(state, char, safe, stamp) -> None:
    """Build report of the character check (python -m gdcheck: text + HTML report) as its own file,
    plus a copy of the player.gdc. Both land in ClaudeSkill-Data\\out\\ and can be uploaded in the
    chat as they are."""
    import shutil
    indizes = index_dateien()
    if not indizes:
        warn("Build report skipped: no game data index yet.")
        return
    befund = OUT / f"{safe}-{stamp}-report.txt"
    bericht = OUT / f"{safe}-{stamp}-report.html"
    mechanik = ROOT / "GameMechanics.html"
    try:
        link = os.path.relpath(mechanik, OUT).replace("\\", "/")
    except ValueError:                      # anderes Laufwerk
        link = mechanik.as_uri()
    if PRODUCT:                             # the product ships no mechanics page; "" drops the link
        link = ""
    args = [str(char["gdc"]), "--index", str(indizes[-1]), "--html", str(bericht),
            "--mechanics-link", link, "--text", "player,expert",
            "--profile", (state.get("profile") or {}).get(char["name"], "general")]
    if PRODUCT:                             # E1: the installer's report includes the attribute, skill point, devotion and reroll suggestions (~4.5 s)
        args.append("--detail")
    if index_status(state)[0] == "aktuell":
        args += ["--installed-fingerprint", index_meta(indizes[-1]).get("fingerprint", "")]
    if state.get("version_confirmed") is False:     # a stale log.html names the label: the report says so
        args.append("--version-unconfirmed")
    if run_module("gdcheck", args, tee=befund) == 0:
        ok(f"Build report: {bericht}")
        uebersicht_zeigen(befund)
        try:
            import webbrowser
            webbrowser.open(bericht.as_uri())
        except Exception:                   # Browser ist Komfort, kein Muss
            pass
    else:
        warn(f"Build report failed. Output and error text are in {befund}")
    kopie = OUT / f"{safe}-{stamp}.gdc"
    try:
        shutil.copy2(char["gdc"], kopie)
        ok(f"Savefile copy: {kopie}")
    except OSError as e:
        warn(f"player.gdc could not be copied: {erklaere(e)}")
    if PRODUCT:
        print(f"           The reports are in {OUT}")
    else:
        print("           For Claude: upload the .gdc (and the -report.html if you like).")


def index_dateien() -> list[Path]:
    try:
        return sorted(OUT.glob("gd-index-*.json.gz"))
    except OSError:
        return []


def index_meta(pfad: Path) -> dict:
    """Kopfdaten eines gebauten Index lesen, ohne ihn ganz zu entpacken."""
    return _local().index_meta(pfad)


# Muss zu gdcheck.app.index_build.INDEX_SCHEMA passen.
INDEX_SCHEMA = 9
ZUSTAND = {"missing": "fehlt", "stale": "veraltet", "unchecked": "ungeprueft", "current": "aktuell",
           "unknown": "unbekannt"}


def index_status(state) -> tuple[str, str]:
    """(Zustand, Erklaerung) - passt der vorhandene Index zu den Spieldaten?"""
    zustand, text, _pfad = _local().index_status(index_dateien(), state.get("fingerprint"), INDEX_SCHEMA)
    return ZUSTAND[zustand], text


def action_index(state, still=False):
    """Build the game data index. True = rebuilt, False = the index build failed, None = not started (Steam is
    still updating)."""
    if not still:
        head("Build game data index")
        zustand, text = index_status(state)
        print(f"  Bisher:       {text}")
        print(f"  Installation: {state['gd']}")
        print(f"  Language:     {state['lang']}")
        print(f"  Game data fingerprint: {state.get('fingerprint') or '?'}")
        if state["version"]:
            print(f"  Label:        {state['version']}   (from version detection)")
        else:
            print("  Label:        derived from the fingerprint "
                  "(no version number needed)")
        print("  This takes one to several minutes depending on the PC.")

    steam = steam_status(state["gd"]) or {}
    if steam.get("updating"):
        warn("Steam is still updating Grim Dawn - the index is not rebuilt now.")
        print(f"           Start {LAUNCHER} again when the update is finished (Steam: Grim Dawn 'Play').")
        return
    vorher = index_sichern()
    args = ["--gd-dir", str(state["gd"]), "--lang", state["lang"], "--out", str(OUT)]
    if state["version"]:
        args = ["--version", state["version"]] + args
    if run_module("gdcheck.app.index_build", args) != 0:
        return False
    neu = index_dateien()
    if neu:
        d = max(neu, key=lambda p: p.stat().st_mtime)
        mb = d.stat().st_size / 1048576
        ok(f"Index ready: {d.name}  ({mb:.2f} MB)")
        if mb > 10 and not PRODUCT:         # the limit is claude.ai's upload size
            warn("Over 10 MB - too large for claude.ai (send the patch bundle instead, see below).")
        state["index_fingerprint"] = index_meta(d).get("fingerprint")
        alt = index_aufraeumen(d, vorher)
        nach_index_bau(state, alt, d, steam)
    return True


PREVIOUS = OUT / "previous"


def _kopie_von(fp: str) -> Path | None:
    """The kept copy of the game data with this fingerprint in out\\previous\\ (or None)."""
    try:
        return next(iter(sorted(PREVIOUS.glob(f"gd-index-*-{fp}.json.gz"))), None) if fp else None
    except OSError:
        return None


def index_sichern() -> list[Path]:
    """Copy the current index files to out\\previous\\ before a rebuild (.53): the index build writes
    gd-index-<label>.json.gz and overwrites a file of the same label - after a Steam patch the label
    can be stale, the old index would be lost for the patch report. A copy is refreshed (time) when it
    exists already (or renewed when it has an older index schema); the 3 most recently used copies are kept,
    never one made in this call."""
    kopien = []
    for p in index_dateien():
        try:
            PREVIOUS.mkdir(parents=True, exist_ok=True)
            fp = index_meta(p).get("fingerprint") or "unknown"
            ziel = PREVIOUS / f"{p.name[:-len('.json.gz')]}-{fp}.json.gz"
            if not exists(ziel):
                shutil.copy2(p, ziel)
            elif int(index_meta(ziel).get("schema") or 1) < int(index_meta(p).get("schema") or 1):
                # .70: the copy of the same game data is an older schema (the index was rebuilt by a newer
                # skill since): a later patch report could not read it - renew it, never half-written
                tmp = ziel.with_name(ziel.name + ".tmp")
                shutil.copy2(p, tmp)
                tmp.replace(ziel)
            os.utime(ziel, None)                    # "used now" - the rotation keeps it
            kopien.append(ziel)
        except OSError as e:
            warn(f"Could not keep a copy of {p.name}: {erklaere(e)}")
    try:
        alle = sorted(PREVIOUS.glob("gd-index-*.json.gz"), key=lambda q: q.stat().st_mtime, reverse=True)
        for q in alle[3:]:
            if q not in kopien:
                q.unlink()
    except OSError:
        pass
    return kopien


def index_aufraeumen(neu: Path, vorher: list[Path]) -> Path | None:
    """Remove from out\\ the index files of other game data (only when their copy is in previous\\ -
    a failed copy keeps the original) and files of the same game data under another (stale) label;
    return the previous index of OTHER game data (for the patch report), or None."""
    fp = index_meta(neu).get("fingerprint")
    for p in index_dateien():
        if p == neu:
            continue
        pfp = index_meta(p).get("fingerprint")
        if pfp == fp or _kopie_von(pfp):
            try:
                p.unlink()
            except OSError:
                pass
    andere = [q for q in vorher if exists(q) and index_meta(q).get("fingerprint") != fp]
    return max(andere, key=lambda q: q.stat().st_mtime) if andere else None


def _ref_fingerprint(pfad: Path) -> str:
    try:
        with open(pfad, encoding="utf-8") as fh:
            kopf = fh.read(400)                 # "fingerprint" is the first key of the reference files
        m = re.search(r'"fingerprint":\s*"([0-9a-f]*)"', kopf)
        if m:
            return m.group(1)
        return str(json.loads(pfad.read_text(encoding="utf-8")).get("fingerprint") or "")
    except (OSError, ValueError):
        return ""


# Reference files of one game data version; a set is complete only with all of them, vendor ranks included.
REF_PREFIXES = ("anim-tables", "boss-combat", "player-skills", "vendor-ranks", "design-data")


def referenzen_vorhanden(fp: str, ordner=None) -> bool:
    """The skill (references\\) or the PC (out\\) has animation tables, boss data, player skill facts,
    vendor ranks and design data (.67) of exactly this game data (fingerprint)."""
    ordner = ordner or (ROOT / "references", OUT)
    for prefix in REF_PREFIXES:
        if not any(_ref_fingerprint(p) == fp for o in ordner if exists(o) for p in Path(o).glob(f"{prefix}-*.json")):
            return False
    return True


def referenzen_lesen(state, version: str, index: Path) -> bool:
    """Animation tables, boss data and player skill facts of new game data from the .arz files (.53)."""
    marker = OUT / f"refs-{version}.failed"
    try:
        if exists(marker) and f"skill {skill_version()}" in marker.read_text(encoding="utf-8"):
            return False
    except OSError:
        return False
    print("           Reading animation tables and boss data of the new game data (1-3 minutes) ...")
    rc = run_module("gdcheck.app.refs_extract", [str(state["gd"]), "--out", str(OUT), "--version", str(version),
                                                 "--index", str(index)])
    if rc == 0:
        ok("Reference data of the new game data read.")
        try:
            marker.unlink()
        except OSError:
            pass
        return True
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"exit code {rc}\nskill {skill_version()}\n", encoding="utf-8")
    except OSError:
        pass
    return False


def nach_index_bau(state, alt: Path | None, neu: Path, steam: dict | None = None) -> None:
    """After an index rebuild (.53): stale version label, reference data and frames of the new game
    data, the patch bundle for Claude and the patch report for all characters."""
    steam = steam or {}
    m = index_meta(neu)
    ver, fp = str(m.get("game_version") or state.get("version") or "?"), str(m.get("fingerprint") or "")
    am = index_meta(alt) if alt else {}
    label_ok = not (am and am.get("game_version") == ver and am.get("fingerprint") != fp)
    if not label_ok:
        warn(f"The game data changed, but the version label is still {ver} (the game has not run since the "
             "update, log.html is old).")
        print(f"           Start Grim Dawn once (the main menu is enough), close it, then start {LAUNCHER} again.")
    if steam.get("branch", "public") != "public":
        warn(f"Steam branch '{steam['branch']}' (beta/playtest) - the results are for that branch only.")
    if fp and not referenzen_vorhanden(fp):
        referenzen_lesen(state, ver, neu)
    if anim_faellig(ver, state.get("gd")):
        tabellen = OUT / f"anim-tables-{ver}.json"
        anim_frames(state, ver, tabellen if exists(tabellen) and _ref_fingerprint(tabellen) == fp else None)
    if fp and not PRODUCT and not referenzen_vorhanden(fp, (ROOT / "references",)):     # bundle = for Claude
        ziel = OUT / f"patch-{ver}.zip"
        args = ["build", "--index", str(neu), "--data", str(OUT), "--out", str(ziel),
                "--label-source", "log.html / settings", "--label-confirmed", "1" if label_ok else "0",
                "--buildid", str(steam.get("buildid") or ""), "--branch", str(steam.get("branch") or "public")]
        if run_module("gdcheck.app.patch_bundle", args) == 0:
            ok(f"Patch bundle for Claude: {ziel}")
            print("           Send this one file to Claude - the skill then knows your new game data.")
    if not alt and exists(PREVIOUS):                    # e.g. the rebuild after a stale label (Extras 2)
        andere = [q for q in PREVIOUS.glob("gd-index-*.json.gz") if index_meta(q).get("fingerprint") != fp]
        kandidat = max(andere, key=lambda q: q.stat().st_mtime) if andere else None
        if kandidat is not None:
            ov = index_meta(kandidat).get("game_version") or "old"
            if not exists(OUT / f"patch-report-{ov}-to-{ver}.txt"):
                alt, am = kandidat, index_meta(kandidat)
    if alt and am.get("fingerprint") != fp:
        patch_bericht(state, alt, neu)


def patch_bericht(state, alt: Path, neu: Path) -> Path | None:
    """What the new game data changes for every character (gdcheck.app.patch_diff)."""
    chars = characters(state.get("savedir"))
    if not chars:
        warn("No characters found - no patch report (Extras 11 sets the save folder, Extras 16 runs it).")
        return None
    ov, nv = index_meta(alt).get("game_version") or "old", index_meta(neu).get("game_version") or "new"
    ziel = OUT / f"patch-report-{ov}-to-{nv}.txt"
    print(f"           Comparing your {len(chars)} characters with the old and the new game data ...")
    rc = run_module("gdcheck.app.patch_diff", ["--old", str(alt), "--new", str(neu), "--out", str(ziel)]
                    + [str(c["gdc"]) for c in chars])
    if rc == 0:
        ok(f"Patch report: {ziel}")
        return ziel
    return None


def action_patch_bericht(state) -> None:
    head("Patch report: old against new game data")
    d = index_dateien()
    alte = sorted(PREVIOUS.glob("gd-index-*.json.gz"), key=lambda q: q.stat().st_mtime, reverse=True) \
        if exists(PREVIOUS) else []
    neu = max(d, key=lambda p: p.stat().st_mtime) if d else None
    fp = index_meta(neu).get("fingerprint") if neu else None
    alt = next((q for q in alte if index_meta(q).get("fingerprint") != fp), None)
    if not neu or not alt:
        warn("No index of older game data kept yet (out\\previous\\) - normal until the first game patch: "
             f"{LAUNCHER} keeps the old index when it rebuilds after a patch, from then on this report works.")
        return
    patch_bericht(state, alt, neu)


def skill_version() -> str:
    """Version of the skill in the ClaudeSkill folder ('?' if it cannot be read)."""
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from gdcheck import VERSION
        return VERSION
    except Exception:                       # noqa: BLE001
        return "?"


def _disk_sizes(sig: dict, gd) -> dict:
    """Byte sizes of the signature's archives in the game folder (missing archives left out)."""
    out = {}
    for rel in sig:
        try:
            out[rel] = (Path(gd) / rel).stat().st_size
        except OSError:
            continue
    return out


def skill_frames_status(version, gd=None) -> str:
    """'same': the skill ships the frames of exactly this game version and build (.52) - its own
    references/anim-frames-<version>.json (no fallback to another version), complete, and every
    Creatures.arc of the signature has the recorded size; 'other build': shipped for this version,
    but the archives differ (hotfix with the same label, an expansion not installed); 'none'.
    Mods are never read, so they do not matter here."""
    if not gd:
        return "none"
    try:
        d = json.loads((ROOT / "references" / f"anim-frames-{version}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "none"
    if str(d.get("assets_version") or "") != str(version) or not d.get("assets"):
        return "none"
    found, want = (list(d.get("coverage") or [0, 0]) + [0, 0])[:2]
    sig = d.get("archives") or {}
    if not (want and found >= 0.9 * want) or not isinstance(sig, dict) or not sig:
        return "none"
    try:
        same = _disk_sizes(sig, gd) == {k: int(v) for k, v in sig.items()}
    except (ValueError, TypeError):
        return "none"
    return "same" if same else "other build"


def skill_bringt_frames(version, gd=None) -> bool:
    return skill_frames_status(version, gd) == "same"


def anim_faellig(version, gd=None) -> bool:
    """Read the animation frames (again)? Not when the skill ships them for this game build (.52).
    Another build of a shipped version: only when no read of exactly this build exists (the PC file's
    signature equals the sizes on disk) and this skill version has not failed on it. Else (no shipped
    frames): not read yet, or the last run was made by another skill version and did not read
    everything (review 02.10.2026: a .failed marker blocked every retry, also after the update that
    fixes the reader)."""
    st = skill_frames_status(version, gd)
    if st == "same":
        return False
    js = OUT / f"anim-frames-{version}.json"
    mk = OUT / f"anim-frames-{version}.failed"
    cur = skill_version()
    if st == "other build":
        try:
            if mk.exists() and f"skill {cur}" in mk.read_text(encoding="utf-8"):
                return False
        except OSError:
            return False
        try:
            d = json.loads(js.read_text(encoding="utf-8"))
            sig = json.loads((ROOT / "references" / f"anim-frames-{version}.json").read_text(
                encoding="utf-8")).get("archives") or {}
            pc = {k: int(v) for k, v in (d.get("archives") or {}).items()}
            return not pc or {k: v for k, v in pc.items() if k in sig} != _disk_sizes(sig, gd)
        except (OSError, ValueError, TypeError, AttributeError):
            return True
    if mk.exists():                           # this version already failed: not at every start again
        try:
            if f"skill {cur}" in mk.read_text(encoding="utf-8"):
                return False
        except OSError:
            return False
    if js.exists():
        try:
            d = json.loads(js.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True
        found, want = (list(d.get("coverage") or [0, 0]) + [0, 0])[:2]
        complete = bool(want) and found >= 0.9 * want
        return not complete and d.get("skill_version") != cur
    return True


def anim_frames(state, version=None, tabellen=None) -> bool:
    """Frame counts of all animations (.anm files) for the action time of the check (AP-F4).
    Written next to the index: ClaudeSkill-Data\\out\\anim-frames-<version>.json, plus
    anim-frames-diagnose.txt for Claude. `tabellen`: animation tables of new game data (.53)."""
    args = [str(state["gd"]), "--out", str(OUT)] + (["--version", str(version)] if version else []) \
        + (["--tables", str(tabellen)] if tabellen else [])
    rc = run_module("gdcheck.app.anim_frames", args)
    marker = OUT / f"anim-frames-{version or 'unknown'}.failed"
    if rc == 0:
        try:
            d = json.loads((OUT / f"anim-frames-{version}.json").read_text(encoding="utf-8"))
            found, want = (list(d.get("coverage") or [0, 0]) + [0, 0])[:2]
            ok(f"Animation frames read: {found} of {want} player animations from your game files "
               "(the rest: community/default frames).")
        except (OSError, ValueError):
            ok("Animation frames read.")
        try:
            marker.unlink()
        except OSError:
            pass
        return True
    warn("Animation frames not read (see the note above) - the build check works as before with "
         "community/default frames. Tried again by itself after the next skill update; Extras 15 tries now.")
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"exit code {rc}\nskill {skill_version()}\ndetails: anim-frames-diagnose.txt\n",
                          encoding="utf-8")
    except OSError:
        pass
    return False


ZUSTAND_TEXT = {"fehlt": "missing", "veraltet": "outdated", "aktuell": "up to date"}


def action_environment(state):
    head("Environment")
    print(f"  Script folder: {HERE}")
    print(f"  Python:        {sys.version.split()[0]}  ({sys.executable})")
    print(f"  Installation:  {state['gd']}")
    print(f"  Game version:  {state['version'] or '(unknown)'}")
    for rel, label, v in exe_version(state["gd"], alle=True):
        print(f"                 exe {rel}: {label} = {v}")
    dateien = log_dateien(state["gd"])
    lg = log_version(state["gd"])
    if lg:
        print(f"                 Log: {lg[0]}   ({lg[1]})")
    elif dateien:
        print("                 Log found, but no version in it:")
        print(f"                   {dateien[0]}")
        kopf = [z.strip() for z in _log_klartext(dateien[0], 8000).splitlines()
                if z.strip()][:5]
        for z in kopf:
            print(f"                   > {z[:96]}")
        print("                   (Please report these lines so the detection "
              "can be adjusted.)")
    else:
        print("                 Log: not found. Searched in:")
        for ordner in log_suchorte(state["gd"]):
            print(f"                   {'[ok]' if exists(ordner) else '[  ]'} {ordner}")
    print(f"  Steam BuildID: {state.get('buildid') or '(not found)'}"
          f"   (changes with every patch)")
    print(f"  Settings:      {SETTINGS}")
    print(f"  Fingerprint:   {state.get('fingerprint') or '(not determinable)'}"
          f"   (from the .arz and text files)")
    zustand, text = index_status(state)
    print(f"  Index:         {ZUSTAND_TEXT.get(zustand, zustand)} - {text}")
    print(f"  Language:      {state['lang']}")
    print(f"  Output:        {OUT}")

    print()
    print("  Databases found:")
    total = 0
    for rel in ["database/database.arz"] + [f"gdx{i}/database/GDX{i}.arz" for i in (1, 2, 3, 4)]:
        p = state["gd"] / rel
        if not exists(p):
            continue
        size = p.stat().st_size
        total += size
        print(f"   {rel:<28} {size/1048576:>8.2f} MB")
    print(f"   {'TOTAL':<28} {total/1048576:>8.2f} MB")

    print()
    for rel in [f"resources/Text_{state['lang']}.arc"] + \
               [f"gdx{i}/resources/Text_{state['lang']}.arc" for i in (1, 2, 3, 4)]:
        p = state["gd"] / rel
        if exists(p):
            ok(f"{rel}  ({p.stat().st_size/1048576:.2f} MB)")
    if not exists(state["gd"] / "resources" / f"Text_{state['lang']}.arc"):
        warn(f"Text_{state['lang']}.arc not found - some names stay empty.")

    print()
    print("  Save folder:" + (f"   (set manually: {state['savedir']})" if state.get("savedir") else ""))
    wurzeln = save_roots(state["savedir"])
    for r in wurzeln:
        print(f"   {r}")
    if not wurzeln:
        print("   none found. Searched among others:")
        for r in steam_roots()[:6]:
            print(f"     {r}\\userdata\\<ID>\\219990\\remote\\save")
        print("     Documents\\My Games\\Grim Dawn\\save")
        print("   -> Extras 11: set the save folder manually.")
    chars = characters(state["savedir"])
    print(f"  Characters found: {len(chars)}")
    for c in chars:
        print(f"   {c['name']:<24} {c['mtime']:%Y-%m-%d %H:%M}  [{c['zweig']}]")

    try:
        import lz4  # noqa: F401
        ok("Module lz4 installed (faster database access)")
    except ImportError:
        if not PRODUCT:                     # the product has no pip to install it with
            warn("Module lz4 missing - extras 7 installs it, saves minutes when indexing.")


def action_diag(state):
    head("Diagnose an unreadable player.gdc")
    char = pick_character(state)
    if char is None:
        return
    stamp = datetime.now().strftime("%Y-%m-%d")
    safe = re.sub(r"[^\w-]", "_", char["name"])
    txt = OUT / f"diagnosis-{safe}-{stamp}.txt"
    if run_module("gdcheck.app.save_diag", [str(char["gdc"])], tee=txt) in (0, 3):     # 3: not a save, explained
        ok(f"Diagnosis: {txt}")
        if PRODUCT:
            print(f"           Attach this file to an issue: {_product().ISSUES_URL}")
        else:
            print("           Please send this file back in the chat.")


def action_lz4():
    head("Install module lz4")
    try:
        rc = subprocess.call([sys.executable, "-m", "pip", "install", "lz4"])
    except OSError as e:
        warn(f"pip could not be started: {erklaere(e)}")
        return
    if rc != 0:
        warn("lz4 was not installed (see the pip message above; no internet or no permission?). "
             "The tools also work without it - only slower.")


def action_open_out():
    OUT.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(str(OUT))          # noqa: E501  (nur auf Windows vorhanden)
    else:
        print(f"  Output folder: {OUT}")


EXTRAS = """
----------------------------------------------------------------------
  EXTRAS  (not needed day to day)
   1  Check environment: paths, databases, characters, version, index
   2  Rebuild game data index (force)
   3  Diagnose a player.gdc that cannot be read
   4  Short report instead of the detailed one
   5  Also write the raw document as JSON
   6  Set game version manually
   7  Install module lz4
   8  Open output folder
   9  Run setup again
  10  Show raw database record (for questions from Claude)
  11  Set save folder manually
  12  Boss data for Claude (Alkamir, superbosses; 1-3 minutes)
  13  Export all characters for Claude (one zip, names kept, with quests)
  14  Content goal of a character (General Ultimate, SR, Ascendant, Celestials)
  15  Read animation frames again (action time of the combat check)
  16  Patch report: what the new game data changes for your characters
   q  Back
----------------------------------------------------------------------"""
# Entries that only make sense with Claude (7: pip, 10/12/13: files to upload to the chat). The product hides
# them; the numbers of the other entries stay.
EXTRAS_CLAUDE = ("7", "10", "12", "13")
if PRODUCT:
    EXTRAS = "\n".join(z for z in EXTRAS.split("\n") if (z.split() or [""])[0] not in EXTRAS_CLAUDE)


def einrichtung(state, cfg):
    """Einmalig beim ersten Start auf diesem Rechner."""
    head("Setup")
    if PRODUCT:
        print("  First start on this PC. One-time setup, after that it goes straight to the evaluation.")
    else:
        print("  This script has never run on this PC. Quick one-time setup,")
        print("  after that it starts straight into the evaluation.")
    print()
    print(f"  Installation:  {state['gd']}")
    print(f"  Game version:  {state['version'] or '(not detected)'}")
    print(f"  Output folder: {OUT}")
    anzahl = len(characters(state["savedir"]))
    print(f"  Characters:    {anzahl} found")
    if anzahl == 0:
        warn("No characters found - please enter the save folder.")
        save_ordner_festlegen(state, cfg)

    try:
        import lz4  # noqa: F401
        ok("Module lz4 is installed (faster database access).")
    except ImportError:
        if not PRODUCT:                     # the product has no pip to install it with
            print()
            print("  The module lz4 speeds up reading the game database "
                  "considerably.")
            if input("  Install now? [Y/n] ").strip().lower() in ("", "j", "y"):
                action_lz4()

    zustand, _ = index_status(state)
    if zustand != "aktuell":
        print()
        print("  The game data index is still missing. It is built now;")
        print("  this takes a few minutes and is only needed again")
        print("  after a Grim Dawn patch.")
        action_index(state, still=True)

    cfg["eingerichtet"] = True
    einstellungen_speichern(cfg)
    print()
    ok("Setup complete. From now on it goes straight to the evaluation." if PRODUCT
       else "Setup complete. From now on the script starts right away.")


def save_ordner_festlegen(state, cfg) -> bool:
    """Save-Ordner abfragen, pruefen und merken. True, wenn Charaktere gefunden."""
    print("  Folder that contains the characters, e.g.")
    print(r"    E:\Program Files (x86)\Steam\userdata\34818113\219990\remote\save\main")
    print("  (Enter = cancel)")
    eingabe = input("  Save folder: ").strip().strip('"')
    if not eingabe:
        return False
    if not exists(eingabe):
        bad(f"Folder not found: {eingabe}")
        print("           Copy the path from the Explorer address bar (e.g. ...\\Documents\\My Games\\Grim Dawn\\save\\main).")
        return False
    gefunden = characters(eingabe)
    if not gefunden:
        bad("No player.gdc there (not in subfolders _<Name> either).")
        print("           Choose the folder that contains the _<Name> character folders (save\\main).")
        return False
    state["savedir"] = eingabe
    cfg["savedir"] = eingabe
    einstellungen_speichern(cfg)
    ok(f"{len(gefunden)} character(s) found, folder saved: {eingabe}")
    return True


def action_record(state):
    head("Show raw database record")
    print("  Record path (records/...dbr) or search terms, e.g. playerclass10 werewolf.")
    print("  Several words: all must appear in the name.")
    eingabe = input("  Record or term: ").strip().strip('"')
    if not eingabe:
        return
    safe = re.sub(r"[^\w-]", "_", eingabe.rsplit("/", 1)[-1].replace(".dbr", ""))[:60]
    txt = OUT / f"record-{safe}-{datetime.now():%Y-%m-%d}.txt"
    if eingabe.lower().endswith(".dbr"):
        # Zwei Ebenen: der Record selbst, seine Verweise und deren Verweise.
        args = [eingabe, "--gd-dir", str(state["gd"]), "--depth", "2"]
    else:
        # ohne --search: record_view gibt bei 1-5 Treffern alle Records vollstaendig aus
        # (30.09.2026: mit --search kam nur die Liste an)
        args = eingabe.split() + ["--gd-dir", str(state["gd"]), "--depth", "1"]
    if run_module("gdcheck.app.record_view", args, tee=txt) == 0:
        ok(f"Output: {txt}")
        print("           Please send this file to Claude in the chat.")


def uebersicht_zeigen(befund_txt: Path) -> None:
    """Player View (first block of the text report) once more in the console."""
    try:
        zeilen = befund_txt.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    print()
    for z in zeilen:
        if z.startswith("=" * 20):
            break
        print("  " + z)


def uebersichten(state, chars) -> dict:
    """{save path: overview} - from the cache; changed saves are evaluated again in one process
    (gdcheck.app.overview_cli, ~1-2 s per character; all of them on the first start)."""
    import json
    indizes = index_dateien()
    if not indizes:
        return {}
    cache_datei = OUT / "overview-cache.json"
    try:
        cache = json.loads(cache_datei.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    version = ""
    try:
        # VERSION.txt holds only the version since .36 (before: "Version <v> (...)")
        parts = (ROOT / "VERSION.txt").read_text(encoding="utf-8").split()
        version = parts[1] if parts and parts[0] == "Version" and len(parts) > 1 else parts[0]
    except (OSError, IndexError):
        pass
    # Neubau 2026-10-01: ein Prozess fuer alle geaenderten Spielstaende (Index nur einmal laden)
    if not any((cache.get(str(c["gdc"])) or {}).get("k", "").endswith(version) for c in chars):
        print(f"  Evaluating {len(chars)} character(s) for the overview "
              f"(first start ~1-2 s each, only changed saves next time) ...")
    r = subprocess.run([sys.executable, *PY_FLAGS, "-m", "gdcheck.app.overview_cli", "--index", str(indizes[-1]),
                        "--cache", str(cache_datei)] + [str(c["gdc"]) for c in chars],
                       capture_output=True, text=True, cwd=str(ROOT), encoding="utf-8", errors="replace",
                       env=kind_env())
    if r.returncode != 0:
        zeilen = (r.stderr or "").strip().splitlines()
        rahmen = [z for z in zeilen if z.startswith(" PROBLEM in") or z.startswith(" What you can do")
                  or z.startswith((" Details for Claude", " Log file"))]
        warn("Overview failed - the character list is shown without values.")
        for z in (rahmen or zeilen[-1:] or ["?"]):
            print("           " + z.strip()[:160])
    try:
        cache = json.loads(cache_datei.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    return {str(c["gdc"]): (cache.get(str(c["gdc"])) or {}).get("u") for c in chars}


def _engine_overview():
    """Single source of truth: the table layout, class abbreviations and legend come
    from the engine (gdcheck.app.overview_cli), so PC and Claude show the same table."""
    from gdcheck.app import overview_cli as OC
    return OC


def _kampagne_kurz(u) -> str:
    """Ultimate column of the overview (engine rule)."""
    return _engine_overview().ultimate_cell(u)


def uebersicht_zeile(nr, c, u) -> tuple:
    name = c["name"].replace("_", " ")
    if not u or u.get("fehler"):
        return (str(nr), name, "-", "-", "-", "-", "-", "-", "-", (u or {}).get("fehler", "-"))
    return _engine_overview().line(nr, name, u)


def uebersicht_tabelle(zeilen) -> list[str]:
    return _engine_overview().table(zeilen)


def action_bosse(state):
    """Boss-Records (Alkamir, Superbosse) roh ausgeben - Grundlage fuer die
    Boss-Datenbank im Skill. Dauert einmalig ein bis drei Minuten."""
    head("Boss data for Claude")
    print("  Reads all creature records once (1-3 minutes) and writes the bosses")
    print("  with their skills into one file.")
    txt = OUT / f"bosses-{datetime.now():%Y-%m-%d}.txt"
    args = ["--bosses", "--gd-dir", str(state["gd"])]
    indizes = index_dateien()
    if indizes:
        args += ["--index", str(indizes[-1])]
    if run_module("gdcheck.app.record_view", args, tee=txt) == 0:
        ok(f"Output: {txt}")
        print("           Please upload this file to Claude in the chat.")


def action_export_alle(state):
    """Alle Charaktere als <Name>.gdc in einer Zip - ein Upload fuer Claude.
    (Im Chat ueberschreiben sich mehrere Dateien namens player.gdc.)"""
    import zipfile
    head("Export all characters for Claude")
    chars = characters(state["savedir"])
    if not chars:
        warn("No characters found.")
        return
    ziel = OUT / f"characters-{datetime.now():%Y-%m-%d}.zip"
    namen = set()
    with zipfile.ZipFile(ziel, "w", zipfile.ZIP_DEFLATED) as z:
        for c in chars:
            safe = re.sub(r"[^\w-]", "_", c["name"]) or "character"
            name, i = safe, 2
            while name in namen:                  # gleiche Namen (Hardcore/Softcore)
                name, i = f"{safe}_{i}", i + 1
            namen.add(name)
            z.write(c["gdc"], f"{name}.gdc")
            # Quest-Stand je Schwierigkeit (Akte) - fuer die Charakteruebersicht
            ordner = Path(c["gdc"]).parent
            for q in sorted(ordner.glob("levels_world*/*/quests.gdd")):
                z.write(q, f"{name}/{q.parent.parent.name}/{q.parent.name}/quests.gdd")
    ok(f"{len(namen)} characters: {ziel}")
    print("           Upload this one zip to Claude - the names are kept.")


def mechanik_pruefen(state) -> None:
    """Warnt, wenn die Offline-Seite zur Spielmechanik nach einem Patch
    nicht mehr zu den Spieldaten passt."""
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from gdcheck.app import page_check as PC          # lazy: only after skripte_pruefen
    except Exception as e:
        warn(f"Game mechanics page check not possible: {erklaere(e)}")
        return
    pfad = PC.page(ROOT)
    if not exists(pfad):
        return
    text = PC.note(pfad, state.get("fingerprint"), state.get("version"))
    if text:
        warn("Game mechanics page outdated.")
        print(f"           {text}")
    else:
        sv = PC.meta(pfad).get("page-version", "")
        ok(f"Game mechanics page matches the game data ({pfad.name}"
           + (f", page version {sv}" if sv else "") + ").")


def _relabel_marker(version: str) -> Path:
    return OUT / f"relabel-{version}.failed"


def relabel_gesperrt(version: str) -> bool:
    """A relabel rebuild to this version failed with this app version already: no retry at every start (it
    costs minutes), only after an update (same rule as the refs-<version>.failed marker)."""
    try:
        m = _relabel_marker(version)
        return m.exists() and f"skill {skill_version()}" in m.read_text(encoding="utf-8")
    except OSError:
        return True


def relabel_bauen(state) -> None:
    """Rebuild the index under the confirmed version label; remember a failure for this app version."""
    version = state["version"]
    ok(f"Game version now confirmed as {version} - updating the game data label.")
    erfolg = action_index(state, still=True)        # the reference files are found by fingerprint, not label
    marker = _relabel_marker(version)
    try:
        if erfolg is False:
            OUT.mkdir(parents=True, exist_ok=True)
            marker.write_text(f"relabel failed\nskill {skill_version()}\n", encoding="utf-8")
        elif erfolg is True and marker.exists():
            marker.unlink()
    except OSError:
        pass


def _label_veraltet(state, meta: dict) -> bool:
    """The version is confirmed (fresh log.html, plausible exe or command line) and differs from the label of
    the index that belongs to this installation (same fingerprint): the data are right, only the label is old."""
    version, fp = state.get("version"), state.get("fingerprint")
    return bool(state.get("version_confirmed") and version and fp and meta.get("fingerprint") == fp
                and meta.get("game_version") != version)


def index_pflegen(state, cfg) -> None:
    """Index automatisch auf Stand bringen und den Nutzer informieren."""
    zustand, text = index_status(state)
    if zustand == "aktuell":
        ok(f"Game data index up to date ({Path(text.split()[0]).name}).")
        d = index_dateien()
        if d and _label_veraltet(state, index_meta(d[-1])) and not relabel_gesperrt(state["version"]):
            relabel_bauen(state)
            d = index_dateien()
        ver = (index_meta(d[-1]).get("game_version") if d else None) or state.get("version")
        fp = str(index_meta(d[-1]).get("fingerprint") or "") if d else ""
        if fp and not referenzen_vorhanden(fp):       # the public installer ships no reference data
            referenzen_lesen(state, ver, d[-1])
        if ver and anim_faellig(ver, state.get("gd")):
            print("           Reading the animation frames from your game files (about a minute) ...")
            anim_frames(state, ver)
        return
    if zustand == "ungeprueft":
        warn(text)
        return
    if zustand == "fehlt":
        warn("Game data index missing, building it now.")
    else:
        if "schema" in text:
            warn("The scripts need new fields in the index.")
        else:
            warn("The game data changed - a patch was released.")
        print(f"           {text}")
        print("           The index is being rebuilt now.")
    print("           This takes a few minutes, then it continues "
          "automatically.")
    action_index(state, still=True)


def extras(state, cfg):
    while True:
        print(EXTRAS)
        wahl = input("  Choice: ").strip().lower()
        if wahl in ("q", ""):
            return
        if PRODUCT and wahl in EXTRAS_CLAUDE:
            warn("Please choose a number from the list or q.")
        elif wahl == "1":
            action_environment(state)
        elif wahl == "2":
            action_index(state)
        elif wahl == "3":
            action_diag(state)
        elif wahl == "4":
            action_extract(state, full=False, gemerkt=cfg.get("letzter_charakter"))
        elif wahl == "5":
            action_extract(state, full=True, raw=True,
                           gemerkt=cfg.get("letzter_charakter"))
        elif wahl == "6":
            v = input(f"  New game version (current: {state['version'] or '-'}): ").strip()
            if v and VERSION_MUSTER.match(v):
                state["version"] = v
                cfg["version"] = v
                einstellungen_speichern(cfg)
                ok(f"Version set: {v}")
            elif v:
                bad(f"'{v}' is not a version number - expected four numbers like 1.3.0.8.")
        elif wahl == "7":
            action_lz4()
        elif wahl == "8":
            action_open_out()
        elif wahl == "9":
            einrichtung(state, cfg)
        elif wahl == "10":
            action_record(state)
        elif wahl == "11":
            save_ordner_festlegen(state, cfg)
        elif wahl == "12":
            action_bosse(state)
        elif wahl == "13":
            action_export_alle(state)
        elif wahl == "14":
            profil_festlegen(state, cfg)
        elif wahl == "15":
            d = index_dateien()
            anim_frames(state, index_meta(d[-1]).get("game_version") if d else state.get("version"))
        elif wahl == "16":
            action_patch_bericht(state)
        else:
            warn("Please 1 to 16 or q.")


PROFILE = ("general", "sr", "ascendant", "celestial")


def waehle_name(antwort: str, gezeigt, alle=()) -> tuple:
    """A character from the input: the number in the list shown last (`gezeigt`, 1-based), the whole
    name (any case) or a part of it (looked up among `gezeigt` and `alle`).
    -> (status, payload): ("leer", None) | ("ok", name) | ("mehrdeutig", [names]) |
    ("nummer", None) number outside the list | ("keiner", None) nothing fits."""
    antwort = antwort.strip()
    if not antwort:
        return "leer", None
    if antwort.isdigit() and 1 <= int(antwort) <= len(gezeigt):
        return "ok", gezeigt[int(antwort) - 1]
    namen = list(dict.fromkeys([*gezeigt, *alle]))
    genau = [n for n in namen if n.lower() == antwort.lower()]
    if genau:
        return "ok", genau[0]
    if antwort.isdigit():
        return "nummer", None
    treffer = [n for n in namen if antwort.lower() in n.lower()]
    if len(treffer) == 1:
        return "ok", treffer[0]
    return ("mehrdeutig", treffer) if treffer else ("keiner", None)


def waehle_ziel(antwort: str) -> tuple:
    """A content goal from the input: its number (1-4), its name (any case) or the unambiguous start of
    its name. -> ("leer", None) | ("ok", goal) | ("keiner", None)."""
    antwort = antwort.strip().lower()
    if not antwort:
        return "leer", None
    if antwort.isdigit():
        n = int(antwort)
        return ("ok", PROFILE[n - 1]) if 1 <= n <= len(PROFILE) else ("keiner", None)
    if antwort in PROFILE:
        return "ok", antwort
    treffer = [p for p in PROFILE if p.startswith(antwort)]
    return ("ok", treffer[0]) if len(treffer) == 1 else ("keiner", None)


def profil_festlegen(state, cfg) -> None:
    """N-002: content goal per character, remembered in the settings. .69: the character by number (from
    the list shown last) or by (part of) its name, the goal by number or name; the current goal is shown;
    an invalid input asks again instead of cancelling; Enter changes nothing."""
    alle = [c["name"] for c in characters(state.get("savedir"))]
    gezeigt = [n for n in (state.get("zuletzt_gezeigt") or []) if n in alle] or alle
    if not gezeigt:
        warn("No characters found - nothing to set.")
        return
    state["zuletzt_gezeigt"] = list(gezeigt)
    profile = cfg.get("profile") or {}
    print("  Characters (numbers as in the list shown last):")
    for i, n in enumerate(gezeigt, 1):
        print(f"   {i:>2}) {n:<24} goal: {profile.get(n, 'general')}")
    while True:
        eingabe = input("  Character (number or part of the name, Enter = cancel): ")
        status, wert = waehle_name(eingabe, gezeigt, alle)
        if status == "leer":
            return
        if status == "ok":
            name = wert
            break
        if status == "mehrdeutig":
            warn(f"Ambiguous: {', '.join(wert[:6])} - type more of the name or its number.")
        elif status == "nummer":
            warn(f"Invalid number: the list has 1 to {len(gezeigt)}.")
        else:
            warn(f"No character contains '{eingabe.strip()}'.")
    aktuell = profile.get(name, "general")
    print(f"  {name} - current goal: {aktuell}")
    print("  Goals:  " + "   ".join(f"{i} {p}" for i, p in enumerate(PROFILE, 1)))
    while True:
        eingabe = input("  Goal (number or name, Enter = keep): ")
        status, p = waehle_ziel(eingabe)
        if status == "leer":
            print(f"  Nothing changed: {name} stays at {aktuell}.")
            return
        if status == "ok":
            break
        bad(f"'{eingabe.strip()}' is not a known goal - type 1 to {len(PROFILE)} or one of: "
            f"{', '.join(PROFILE)}.")
    cfg.setdefault("profile", {})[name] = p
    state["profile"] = cfg["profile"]
    einstellungen_speichern(cfg)
    ok(f"{name}: goal {p}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Grim Dawn tools.")
    ap.add_argument("--gd-dir")
    ap.add_argument("--save-dir")
    ap.add_argument("--game-version")
    ap.add_argument("--lang", default="EN")
    ap.add_argument("--char", help="evaluate this character directly, without selection")
    ap.add_argument("--selftest", action="store_true",
                    help="only check detection and exit")
    ap.add_argument("--pfad-von", metavar="NAME",
                    help="only print the path of this character's player.gdc (for Tests.bat)")
    args = ap.parse_args(argv)
    if not skripte_pruefen():
        try:
            input("\n  Enter = close ")
        except EOFError:
            pass
        return 1

    global GD_DIR
    if args.pfad_von:
        cfg0 = einstellungen_laden()
        hint0 = args.gd_dir or remembered_game_dir(cfg0)
        g = find_game_dir(hint0) if not hint0 or is_gd_dir(hint0) else None
        GD_DIR = g
        treffer = [c for c in characters(args.save_dir or cfg0.get("savedir"))
                   if c["name"].lower() == args.pfad_von.lower()]
        if not treffer:
            return 1
        print(treffer[0]["gdc"])
        return 0

    head(PRODUCT_NAME if PRODUCT else "Grim Dawn Tools")
    if not PRODUCT:                         # migrations of the ClaudeSkill.zip layout; the installer has none
        umgezogen = daten_umziehen()
        if umgezogen:
            ok(f"{len(umgezogen)} file(s) moved to {DATEN} "
               "(one time, so updates delete nothing).")
        for zeile in alte_dateien_aufraeumen():
            ok(zeile)
    if sys.version_info < (3, 10):
        bad(f"Python 3.10 or newer needed, found {sys.version.split()[0]}.")
        print("           Install the current Python from python.org (tick 'Add python.exe to PATH').")
        return 1

    cfg = einstellungen_laden()
    gd = find_game_dir(args.gd_dir or remembered_game_dir(cfg))
    if gd is None:
        warn("Grim Dawn installation not found automatically.")
        if args.selftest:
            return 1
        eingabe = input("  Path to the Grim Dawn folder: ").strip().strip('"')
        if not is_gd_dir(eingabe):
            bad("No database.arz there.")
            print("           Give the Grim Dawn installation folder (it contains the folder 'database'), e.g.")
            print("             Steam:  C:\\Program Files (x86)\\Steam\\steamapps\\common\\Grim Dawn")
            print("             GOG:    C:\\GOG Games\\Grim Dawn")
            return 1
        gd = Path(eingabe).resolve()
        cfg["gd_dir"] = str(gd)            # remembered: the next start does not ask again
        einstellungen_speichern(cfg)
    ok(f"Installation: {gd}")
    GD_DIR = gd

    build = steam_buildid(gd)
    version, quelle, bestaetigt = version_bestimmen(gd, args.game_version, cfg)
    if version:
        ok(f"Game version: {version}   (source: {quelle})")
    else:
        warn("Game version not detectable - it is only used as a label.")
    if build:
        cfg["buildid"] = build

    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from gdcheck.data.fingerprint import db_fingerprint      # same value as the index build
        fingerprint = db_fingerprint(gd)
    except Exception as e:
        fingerprint = None
        warn(f"Game data fingerprint not determinable: {erklaere(e)}")

    state = {"gd": gd, "version": version, "lang": args.lang,
             "savedir": args.save_dir or cfg.get("savedir"), "buildid": build,
             "fingerprint": fingerprint, "profile": cfg.get("profile") or {}, "version_confirmed": bestaetigt}
    OUT.mkdir(parents=True, exist_ok=True)
    if fingerprint:
        cfg["fingerprint"] = fingerprint
    einstellungen_speichern(cfg)

    if args.selftest:
        action_environment(state)
        return 0

    if not cfg.get("eingerichtet"):
        einrichtung(state, cfg)
    else:
        index_pflegen(state, cfg)
    mechanik_pruefen(state)

    # --- Alltag: direkt auswerten --------------------------------------
    while True:
        if args.char:
            treffer = [c for c in characters(state["savedir"])
                       if args.char.lower() in c["name"].lower()]
            if not treffer:
                bad(f"No character contains '{args.char}'.")
                print(f"           Check the spelling or start {LAUNCHER} without --char to pick from the list.")
                return 1
            cfg["letzter_charakter"] = treffer[0]["name"]
            einstellungen_speichern(cfg)
            # Direktaufruf: keine Rueckfrage, sonst haengt ein Skriptlauf
            return 0 if action_extract(state, char=treffer[0]) else 1

        try:
            name = action_extract(state, gemerkt=cfg.get("letzter_charakter"))
            if name == "ende":
                break
            if name == "extras":
                extras(state, cfg)
                continue
        except (KeyboardInterrupt, EOFError):
            raise
        except Exception as e:              # noqa: BLE001 - one failed action never ends the menu
            problem(e, LAUNCHER)
            print("  The menu continues - the other functions still work.")
            continue
        if name:
            cfg["letzter_charakter"] = name
            einstellungen_speichern(cfg)

        # direkt zurueck zur Charakteruebersicht (Nutzer 30.09.2026: kein
        # zusaetzliches Enter nach jedem Charakter)
        print()
        print("  " + "-" * 70)

    print()
    print("  Done.")
    return 0


def hauptprogramm(argv=None) -> int:
    """main() with friendly texts: Ctrl+C = cancelled, any unexpected error = framed explanation
    and a log file for Claude instead of a Python traceback."""
    try:
        return main(argv)
    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelled.")
        return 130
    except SystemExit:
        raise
    except BaseException as e:              # noqa: BLE001
        return problem(e, LAUNCHER)


if __name__ == "__main__":
    sys.exit(hauptprogramm())
