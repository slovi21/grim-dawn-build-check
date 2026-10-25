"""Product mode: the public installer's launcher sets GDCHECK_PRODUCT=1 and the engine then speaks to
players who have never heard of Claude. Layer rule: imports only os; errors.py imports this module."""
from __future__ import annotations

import os

ENV_FLAG = "GDCHECK_PRODUCT"
NAME = "Grim Dawn Build Check"
ISSUES_URL = "https://github.com/slovi21/grim-dawn-build-check/issues"
LOG_LABEL = "Log file"

# The uninstaller deletes the data folder, so "reinstall" would read as uninstall + install and lose it:
# the players are told to run the installer over the existing installation (it keeps the data folder).
INSTALLER_AGAIN = f"Run the {NAME} installer again (your data stays)"
_AGAIN = INSTALLER_AGAIN
_AGAIN_MID = f"run the {NAME} installer again (your data stays)"
_REPORT_MID = f"open an issue at {ISSUES_URL} and attach"
_REPORT_FIRST = f"Open an issue at {ISSUES_URL} and attach"

# Replacements for the entries of errors.TEXTS / EXIT_TEXTS / TOOL_EXIT_TEXTS that name Claude, same keys
# and tuple layout (title, cause, what to do). tests/unit/test_product_mode.py keeps the sets in step.
TEXTS: dict[str, tuple[str, str, str]] = {
    "locked": ("A file is in use by another program",
               "Windows did not allow access because another program has the file open - often Excel/"
               "Word/a browser showing a report, OneDrive syncing or the virus scanner checking it.",
               "Close the report/file, wait a few seconds and try again. If the data folder is in "
               "OneDrive, pause the sync or move it out of OneDrive."),
    "access": ("No permission for a file or folder",
               "Windows refused to read or write the file (protected folder, read-only file, virus "
               "scanner or a file opened by another program).",
               "Close programs that show the file, check that the folder is not read-only and try again."),
    "disk_full": ("The disk is full", "There was no free space left to write the file.",
                  "Free some disk space (empty the recycle bin, delete old reports in the "
                  f"{NAME} data folder) and try again."),
    "long_path": ("The file path is too long",
                  "Windows limits paths to about 260 characters; this path is longer.",
                  "Move the Grim Dawn folder to a shorter path (e.g. C:\\Games) or enable long paths in "
                  "Windows."),
    "virus": ("The virus scanner blocked a file", "Windows Defender (or another scanner) stopped the "
              "file from opening.",
              f"Allow the {NAME} folder in the scanner or restore the file from the quarantine, then "
              "try again."),
    "save": ("The save file could not be read",
             "player.gdc is incomplete or in an unknown format - usually the game was writing it "
             "at that moment.",
             f"Close Grim Dawn (or wait until the character is saved) and start again. If it stays, {_REPORT_MID} "
             "the log file."),
    "install": ("A part of the program is missing", f"{NAME} is incomplete or mixed from two versions.",
                f"{_AGAIN}."),
    "damaged": ("A data file is damaged or incomplete", "A file could not be decoded - it was probably cut "
                "off while it was written (crash, full disk, cloud sync).",
                "Start the program again. For overview problems delete overview-cache.json in the data folder - "
                "it is rebuilt automatically. "
                f"If it stays, {_AGAIN_MID}."),
    "os": ("Windows reported a file system error", "Reading or writing a file failed.",
           f"Try again; if it stays, {_REPORT_MID} the log file named below."),
    "bug": ("Unexpected program error", "The tools ran into a situation they do not handle - this is a "
            "bug in the tools, not a problem with your PC or your character.",
            f"{_REPORT_FIRST} the log file named below; it contains everything needed to fix it."),
}

EXIT_TEXTS: dict[int, tuple[str, str, str]] = {
    1: ("The tool stopped with an error", "The tool itself reported the problem - see the lines above.",
        f"Follow the hint above; if it is unclear, {_REPORT_MID} this window's text."),
    2: ("The tool was started in a way it does not accept",
        "Usually the program files are incomplete or mixed from two versions.",
        f"{_AGAIN}; if it stays, {_REPORT_MID} this window's text."),
    70: ("Unexpected program error", "The tool hit a bug (details in the log file above).",
         f"{_REPORT_FIRST} the log file named above."),
}

# Per tool (key = errors.tool_name): codes with a meaning of their own; optional 4th entry = label
TOOL_EXIT_TEXTS: dict[str, dict[int, tuple[str, ...]]] = {
    "build check": {
        4: ("A result file could not be written, or a file of the tools is damaged",
            "Either the report file is open in another program / the folder is read-only, or a file of "
            "the tools is damaged - the lines above say which.",
            f"Follow the 'What you can do' line above (close the report, or {_AGAIN_MID})."),
    },
    "animation frames": {
        3: ("Animation frames not read - nothing is broken",
            "Some game files are built differently than the tool expects; nothing was changed. The build "
            "check keeps using the community and default frames.",
            f"{_REPORT_FIRST} anim-frames-diagnose.txt from the data folder. The next update tries "
            "again by itself.", "NOTE"),
    },
    "reference data": {
        3: ("Part of the reference data not read - nothing is broken",
            "The animation tables or the boss data of the new game version could not be read; the check uses "
            "the data of the last known version and marks it ASSUMED.",
            f"{_REPORT_FIRST} the lines above. The next update tries again by itself.", "NOTE"),
    },
}

# exit_text for a code no table knows
UNKNOWN_EXIT_FIX = f"{_REPORT_FIRST} this window's text."


def enabled() -> bool:
    return os.environ.get(ENV_FLAG) == "1"
