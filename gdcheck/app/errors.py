"""User-friendly error messages for the local tools (GD-Tools, Tests.bat, the PC tools).

The player sees a framed explanation instead of a Python traceback:

    ----------------------------------------------------------------------
     PROBLEM in <tool>: <title>
     What happened:   ...
     Probable cause:  ...
     What you can do: ...
     Technical:       PermissionError: [WinError 32] ... (cli.py:93)
     Details for Claude: ClaudeSkill-Data\\out\\logs\\error-<tool>-<time>.txt
    ----------------------------------------------------------------------

The full traceback, the versions and the command line go into that log file (never environment
variables). Friendly mode is on only when the caller asks for it (GDCHECK_FRIENDLY=1, set by
GD-Tools and Tests.bat) - without it the tools keep printing the traceback, so tests and Claude's
own runs still see every crash. Exit codes: 70 = internal error, 130 = cancelled.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

from . import product

EXIT_INTERNAL, EXIT_CANCELLED = 70, 130
LINE = "-" * 70
MAX_LOGS = 20
# Windows error numbers (winerror) and NTSTATUS exit codes of a crashed process
WIN = {5: "access", 32: "locked", 33: "locked", 112: "disk_full", 206: "long_path", 225: "virus",
       362: "cloud", 1920: "access"}
NTSTATUS = {3221225786: "cancelled", -1073741510: "cancelled", 3221225477: "native_crash",
            -1073741819: "native_crash", 3221225725: "native_crash", -1073741571: "native_crash"}


def friendly_mode() -> bool:
    return os.environ.get("GDCHECK_FRIENDLY") == "1"


def log_dir() -> Path:
    """GDCHECK_LOG_DIR (set by GD-Tools), else ClaudeSkill-Data/out/logs next to the skill, else temp."""
    env = os.environ.get("GDCHECK_LOG_DIR")
    if env:
        return Path(env)
    root = Path(__file__).resolve().parents[2]
    data = root.parent / "ClaudeSkill-Data"
    if data.is_dir():
        return data / "out" / "logs"
    import tempfile
    return Path(tempfile.gettempdir()) / "gdcheck-logs"


def _kind(exc: BaseException) -> str:
    if isinstance(exc, KeyboardInterrupt):
        return "cancelled"
    if isinstance(exc, MemoryError):
        return "memory"
    win = getattr(exc, "winerror", None)
    if win in WIN:
        return WIN[win]
    name = type(exc).__name__
    if name == "GDFormatError":
        return "save"
    if isinstance(exc, ModuleNotFoundError):
        return "install"
    if isinstance(exc, FileNotFoundError):
        return "missing"
    if isinstance(exc, PermissionError):
        return "access"
    if isinstance(exc, (IsADirectoryError, NotADirectoryError)):
        return "path"
    if isinstance(exc, OSError):
        import errno
        if exc.errno == errno.ENOSPC:
            return "disk_full"
        if exc.errno == errno.ENAMETOOLONG:
            return "long_path"
        if exc.errno in (errno.EACCES, errno.EPERM):
            return "access"
        if "gzip" in str(exc).lower() or name == "BadGzipFile":
            return "damaged"
        return "os"
    if isinstance(exc, (EOFError, UnicodeDecodeError)) or name in ("JSONDecodeError", "BadZipFile", "error"):
        return "damaged"
    return "bug"


TEXTS = {
    "cancelled": ("Cancelled", "You stopped the program (Ctrl+C or closing the window).",
                  "Nothing to repair - start it again when you like."),
    "memory": ("Not enough memory", "The computer ran out of free memory (RAM).",
               "Close other programs (browser, game) and start again."),
    "locked": ("A file is in use by another program",
               "Windows did not allow access because another program has the file open - often Excel/"
               "Word/a browser showing a report, OneDrive syncing or the virus scanner checking it.",
               "Close the report/file, wait a few seconds and try again. If the folder is in OneDrive, "
               "pause the sync or move ClaudeSkill-Data out of OneDrive."),
    "access": ("No permission for a file or folder",
               "Windows refused to read or write the file (protected folder, read-only file, virus "
               "scanner or a file opened by another program).",
               "Close programs that show the file, check that the folder is not read-only, and do not "
               "keep ClaudeSkill under C:\\Program Files. Try again."),
    "disk_full": ("The disk is full", "There was no free space left to write the file.",
                  "Free some disk space (empty the recycle bin, delete old reports in "
                  "ClaudeSkill-Data\\out) and try again."),
    "long_path": ("The file path is too long",
                  "Windows limits paths to about 260 characters; this path is longer.",
                  "Move the Grim Dawn/ClaudeSkill folder to a shorter path (e.g. C:\\Games) or enable "
                  "long paths in Windows."),
    "virus": ("The virus scanner blocked a file", "Windows Defender (or another scanner) stopped the "
              "file from opening.",
              "Allow the ClaudeSkill folder in the scanner or restore the file from the quarantine, then "
              "try again."),
    "cloud": ("The file is only in the cloud", "OneDrive has not downloaded the file to this PC yet.",
              "Right-click the folder -> 'Always keep on this device', wait for the download, try again."),
    "save": ("The save file could not be read",
             "player.gdc is incomplete or in an unknown format - usually the game was writing it "
             "at that moment.",
             "Close Grim Dawn (or wait until the character is saved) and start again. If it stays, "
             "use Extras 3 (diagnose a player.gdc) and send the result to Claude."),
    "install": ("A part of the program is missing", "Python could not find a module of the tools - the "
                "ClaudeSkill folder is incomplete or mixed from two versions.",
                "Delete the folder ClaudeSkill and unpack ClaudeSkill.zip again (your data in "
                "ClaudeSkill-Data stays)."),
    "missing": ("A file or folder was not found", "The program needed a file that does not exist (moved, "
                "deleted or not created yet).",
                "Check the path in the line 'Technical' below. Game data index missing: GD-Tools builds it "
                "at the next start (or Extras 2)."),
    "path": ("A path points to the wrong kind of object", "A folder was given where a file was expected "
             "(or the other way round).", "Check the path in the line 'Technical' below."),
    "damaged": ("A data file is damaged or incomplete", "A file could not be decoded - it was probably cut "
                "off while it was written (crash, full disk, cloud sync).",
                "Rebuild the game data index (GD-Tools -> Extras 2). For overview problems delete "
                "ClaudeSkill-Data\\out\\overview-cache.json - it is rebuilt automatically."),
    "os": ("Windows reported a file system error", "Reading or writing a file failed.",
           "Try again; if it stays, send the log file named below to Claude."),
    "native_crash": ("A helper module crashed", "A compiled helper (for example the lz4 module) crashed "
                     "inside Python.", "Uninstall lz4 (py -m pip uninstall lz4) - the tools work without "
                     "it, only slower - and try again."),
    "bug": ("Unexpected program error", "The tools ran into a situation they do not handle - this is a "
            "bug in the tools, not a problem with your PC or your character.",
            "Send the log file named below to Claude; it contains everything needed to fix it."),
}


def _text(kind: str) -> tuple[str, str, str]:
    """TEXTS[kind]; in product mode (GDCHECK_PRODUCT=1) the replacement that does not name Claude."""
    if product.enabled() and kind in product.TEXTS:
        return product.TEXTS[kind]
    return TEXTS[kind]


def explain(exc: BaseException) -> tuple[str, str, str, str]:
    """(kind, title, cause, what to do)."""
    k = _kind(exc)
    title, cause, fix = _text(k)
    return k, title, cause, fix


def _where(exc: BaseException) -> str:
    tb = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    if not tb:
        return ""
    f = tb[-1]
    return f" ({Path(f.filename).name}:{f.lineno})"


def write_log(tool: str, exc: BaseException | None, extra: str = "") -> Path | None:
    """Full details for Claude; never raises. Keeps the newest MAX_LOGS logs."""
    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d_%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in tool)[:40]
        path = d / f"error-{safe}-{stamp}.txt"
        try:
            from .. import VERSION
        except Exception:                               # noqa: BLE001
            VERSION = "?"
        lines = [f"tool: {tool}", f"time: {time.strftime('%Y-%m-%d %H:%M:%S')}", f"skill: {VERSION}",
                 f"python: {sys.version.split()[0]} ({sys.executable})", f"platform: {sys.platform}",
                 "command: " + " ".join(sys.argv), ""]
        if exc is not None:
            lines += traceback.format_exception(type(exc), exc, exc.__traceback__)
        if extra:
            lines += ["", extra]
        path.write_text("\n".join(x.rstrip("\n") for x in lines) + "\n", encoding="utf-8")
        logs = sorted(d.glob("error-*.txt"), key=lambda p: p.stat().st_mtime)
        for old in logs[:-MAX_LOGS]:
            try:
                old.unlink()
            except OSError:
                pass
        return path
    except Exception:                                   # noqa: BLE001 - the report must never fail
        return None


def frame(tool: str, title: str, what: str, cause: str, fix: str, technical: str = "",
          log: Path | None = None, label: str = "PROBLEM") -> str:
    """label PROBLEM (something failed) or NOTE (nothing is broken, information for the player)."""
    rows = [LINE, f" {label} in {tool}: {title}", f" What happened:   {what}", f" Probable cause:  {cause}",
            f" What you can do: {fix}"]
    if technical:
        rows.append(f" Technical:       {technical}")
    if log:
        rows.append(f" {product.LOG_LABEL}: {log}" if product.enabled() else f" Details for Claude: {log}")
    rows.append(LINE)
    return "\n".join(rows)


def report(exc: BaseException, tool: str, stream=None) -> int:
    """Print the framed explanation (ASCII only), write the log; returns the exit code. Never raises."""
    stream = stream or sys.stderr
    try:
        sys.stdout.flush()
    except Exception:                                   # noqa: BLE001
        pass
    kind, title, cause, fix = explain(exc)
    log = None if kind == "cancelled" else write_log(tool, exc)
    what = {"cancelled": "The program was stopped before it finished."}.get(
        kind, f"{tool} stopped before it finished.")
    tech = f"{type(exc).__name__}: {str(exc)[:200]}{_where(exc)}" if kind != "cancelled" else ""
    try:
        text = frame(tool, title, what, cause, fix, tech, log)
        print(text.encode("ascii", "replace").decode("ascii"), file=stream)
        stream.flush()
    except Exception:                                   # noqa: BLE001
        pass
    return EXIT_CANCELLED if kind == "cancelled" else EXIT_INTERNAL


def run(main, tool: str, *args) -> int:
    """Call main(*args); in friendly mode an unexpected error becomes a framed message + log."""
    if not friendly_mode():
        return main(*args)
    try:
        rc = main(*args)
    except SystemExit as ex:                            # argparse, sys.exit("message") - keep as is
        raise ex
    except BaseException as ex:                         # noqa: BLE001
        return report(ex, tool)
    return rc


# Names of the tools as the player reads them (one place: GD-Tools, safe_run, the tools themselves)
TOOL_NAMES = {
    "gdcheck": "build check",
    "gdcheck.app.overview_cli": "overview",
    "gdcheck.app.anim_frames": "animation frames",
    "gdcheck.app.refs_extract": "reference data",
    "gdcheck.app.patch_bundle": "patch bundle",
    "gdcheck.app.patch_diff": "patch report",
    "gdcheck.app.design": "build design",
    "gdcheck.app.index_build": "game data index",
    "gdcheck.app.record_view": "database record",
    "gdcheck.app.save_diag": "save diagnosis",
    "gdcheck.app.save_report": "save reader",
}


def tool_name(module: str) -> str:
    """'gdcheck.app.anim_frames' -> 'animation frames' (unknown module: last part, readable)."""
    return TOOL_NAMES.get(module) or module.rsplit(".", 1)[-1].replace("_", " ")


# Exit codes that mean the same in every tool
EXIT_TEXTS = {
    1: ("The tool stopped with an error", "The tool itself reported the problem - see the lines above.",
        "Follow the hint above; if it is unclear, send this window's text to Claude."),
    2: ("The tool was started in a way it does not accept",
        "Usually GD-Tools and the skill folder do not belong together (one of them is older).",
        "Unpack ClaudeSkill.zip again (your data in ClaudeSkill-Data stays); if it stays, send this "
        "window's text to Claude."),
    EXIT_INTERNAL: ("Unexpected program error", "The tool hit a bug (details in the log file above).",
                    "Send the log file named above to Claude."),
    EXIT_CANCELLED: ("Cancelled", "The run was stopped.", "Start it again when you like."),
}
_INDEX_TEXT = ("Game data index missing or not usable", "The tools need the game data index of your installation.",
               "GD-Tools -> Extras 2 (rebuild game data index), then try again.")
# Exit codes with a meaning of their own per tool (review 02.10.2026: code 3 of the animation frames
# was explained as 'index missing' - wrong advice). Optional 4th entry: label (NOTE = nothing broken).
TOOL_EXIT_TEXTS = {
    "build check": {
        2: ("The save file could not be read (or the call was wrong)",
            "player.gdc was missing, incomplete or in use - or the tool was called with a wrong option.",
            "Close Grim Dawn or wait until the character is saved, then try again."),
        3: _INDEX_TEXT,
        4: ("A result file could not be written, or a file of the tools is damaged",
            "Either the report file is open in another program / the folder is read-only, or a file of "
            "the tools is damaged - the lines above say which.",
            "Follow the 'What you can do' line above (close the report, or unpack ClaudeSkill.zip again)."),
    },
    "overview": {3: _INDEX_TEXT},
    "animation frames": {
        2: ("Not a Grim Dawn installation folder", "The folder set in GD-Tools has no resources\\ folder.",
            "GD-Tools -> Extras 9 (run setup again) and choose the Grim Dawn folder."),
        3: ("Animation frames not read - nothing is broken",
            "Some game files are built differently than the tool expects; nothing was changed. The build "
            "check keeps using the community and default frames.",
            "Send ClaudeSkill-Data\\out\\anim-frames-diagnose.txt to Claude. GD-Tools tries again by "
            "itself after the next skill update.", "NOTE"),
    },
    "reference data": {
        2: ("Game database not found", "The folder set in GD-Tools has no database\\database.arz.",
            "GD-Tools -> Extras 9 (run setup again) and choose the Grim Dawn folder."),
        3: ("Part of the reference data not read - nothing is broken",
            "The animation tables or the boss data of the new game version could not be read; the check uses "
            "the data of the last known version and marks it ASSUMED.",
            "Send the lines above to Claude. GD-Tools tries again by itself after the next skill update.",
            "NOTE"),
    },
}


def _tool_key(tool: str) -> str:
    return tool_name(tool) if ("." in tool or tool == "gdcheck") else tool


def _exit_entry(name: str, rc: int) -> tuple[str, ...] | None:
    """Table entry for a tool's exit code (tool table first, then the common one); product mode swaps in
    the replacement of an entry that names Claude."""
    pro = product.enabled()
    tool, common = TOOL_EXIT_TEXTS.get(name) or {}, EXIT_TEXTS
    if pro:
        tool = {**tool, **(product.TOOL_EXIT_TEXTS.get(name) or {})}
        common = {**common, **product.EXIT_TEXTS}
    return tool.get(rc) or common.get(rc)


def exit_text(rc: int, tool: str) -> str | None:
    """Framed explanation of a child tool's exit code (None for 0). `tool`: module or readable name."""
    if rc == 0:
        return None
    name = _tool_key(tool)
    label = "PROBLEM"
    kind = NTSTATUS.get(rc)
    if kind:
        title, cause, fix = _text(kind)
    else:
        entry = _exit_entry(name, rc) or (
            "The tool stopped with an error", f"It ended with code {rc}.",
            product.UNKNOWN_EXIT_FIX if product.enabled() else "Send this window's text to Claude.")
        title, cause, fix = entry[:3]
        label = entry[3] if len(entry) > 3 else label
    return frame(name, title, f"{name} ended with code {rc}.", cause, fix, label=label)


def write_retry(path: Path, text: str, wait_s: float = 1.0) -> None:
    """Write a text file; one retry after a short wait when Windows reports the file as locked or
    access denied (virus scanner, OneDrive, a browser showing the report)."""
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as ex:
        if getattr(ex, "winerror", None) in (5, 32, 33) or isinstance(ex, PermissionError):
            time.sleep(wait_s)
            path.write_text(text, encoding="utf-8")
        else:
            raise
