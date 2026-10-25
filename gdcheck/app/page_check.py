"""Version check of the offline page references/grim-dawn-mechanics.html (port of the .35
mechanik_seite; GD-Tools runs it at the start).

The page carries in its head the game version and the fingerprint of the game data it was written for:
    <meta name="gd-version" content="1.3.0.8">
    <meta name="gd-fingerprint" content="0e250f41e62ddc25">
If the fingerprint of the current game data (or of the index) differs, a patch came out - then the
page has to be updated in Claude.
"""
from __future__ import annotations

import re
from pathlib import Path

FILE_NAME = "grim-dawn-mechanics.html"
_META = re.compile(r'<meta\s+name="gd-(version|fingerprint|page-version)"\s+content="([^"]*)"', re.I)
# Page version (meta gd-page-version): <game version>-r<revision>, e.g. 1.3.0.8-r4. The revision counts
# every change of the page's content and starts again at r1 after a patch (user request 27.09.2026).


def page(base: Path) -> Path:
    """Path of the page below the skill or ClaudeSkill folder."""
    return Path(base) / "references" / FILE_NAME


def meta(path: Path) -> dict:
    """{version, fingerprint, page-version} from the page head ({} if unreadable)."""
    try:
        head = Path(path).read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return {}
    return {k.lower(): v for k, v in _META.findall(head)}


def note(path: Path, fingerprint: str | None, version: str | None = None) -> str | None:
    """Text for the player when the page does not match the game data (None if it does)."""
    m = meta(path)
    if not m:
        return None
    if fingerprint and m.get("fingerprint") and m["fingerprint"] != fingerprint:
        now = f"version {version}, game data {fingerprint}" if version else f"game data {fingerprint}"
        return (f"The game mechanics page is for version {m.get('version', '?')} "
                f"(game data {m['fingerprint']}); now installed: {now}. "
                f"A patch was released: have Claude update the page in chat "
                f"(check the patch notes).")
    return None
