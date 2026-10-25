"""Player skill facts the index does not keep (references/player-skills-<version>.json,
written by tools/boss_extract.py from the .arz files)."""
from __future__ import annotations

from functools import lru_cache


def not_dispelable(version: str = "1.3.0.8", fingerprint: str | None = None) -> frozenset:
    """Records with notDispelable = 1 (a monster purge cannot remove them, e.g. werewolf1), from the
    player-skills file that fits the game data (data.versioned, .53)."""
    from . import versioned as V
    r = V.resolve("player-skills", version, fingerprint)
    return _frozen(str(r.path) if r.path else "", tuple(r.data.get("not_dispelable") or ()))


@lru_cache(maxsize=8)
def _frozen(_path: str, items: tuple) -> frozenset:
    return frozenset(items)


def cooldown_refresh(version: str = "1.3.0.8", fingerprint: str | None = None) -> dict:
    """{record: {"skill", "chance" (% per rank), "amount" (s), "trigger"}} - records that reduce another
    skill's cooldown (refreshCooldown* fields, not in the index), from the player-skills file that fits the
    game data; {} for an older file without them."""
    from . import versioned as V
    r = V.resolve("player-skills", version, fingerprint)
    return r.data.get("cooldown_refresh") or {}
