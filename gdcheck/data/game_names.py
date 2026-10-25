"""Name resolution straight from a Grim Dawn installation (.arz databases + Text_XX.arc), without
the game data index - port of the .35 extractor's NameDB/NullDB (gd_extract part 5) for the
character text report (gdcheck.app.save_report). Same interface as data.index.GameIndex
(record / tag / display / item_name / faction_name)."""
from __future__ import annotations

import re
from pathlib import Path

from .arz import ArzFile, load_localization
from .text import FACTION_FALLBACK_NAMES, FACTION_SLOT_KEYS, clean_text, ist_wertname, short

__all__ = ["GameNames", "NullNames"]

_DISPLAY_FIELDS = ("itemNameTag", "description", "skillDisplayName", "lootRandomizerName", "skillTabTitle")


class GameNames:
    """Resolves DBR paths to readable names. Needs the game installation folder."""

    def __init__(self, game_dir, lang="EN"):
        self.game_dir = Path(game_dir)
        db_paths = [self.game_dir / "database" / "database.arz"]
        loc_paths = [self.game_dir / "resources" / f"Text_{lang}.arc"]
        for i in (1, 2, 3, 4):
            p = self.game_dir / f"gdx{i}" / "database" / f"GDX{i}.arz"
            if p.exists():
                db_paths.append(p)
                lp = self.game_dir / f"gdx{i}" / "resources" / f"Text_{lang}.arc"
                if lp.exists():
                    loc_paths.append(lp)
        if not db_paths[0].exists():
            raise FileNotFoundError(
                f"database.arz not found in {self.game_dir}. "
                f"--gd-dir must point to the Grim Dawn install folder.")
        self.dbs = [ArzFile(p) for p in db_paths]
        self.loc: dict[str, str] = {}
        for p in loc_paths:
            if p.exists():
                self.loc.update(load_localization(p))
        self._cache: dict[str, dict | None] = {}

    def record(self, name):
        if not name:
            return None
        if name in self._cache:
            return self._cache[name]
        rec = None
        for db in reversed(self.dbs):          # later expansions win
            rec = db.read_record(name)
            if rec is not None:
                break
        self._cache[name] = rec
        return rec

    def tag(self, tag_name):
        if not tag_name:
            return ""
        return clean_text(self.loc.get(tag_name, ""))

    def display(self, dbr):
        """Display name of any DBR record."""
        rec = self.record(dbr)
        if rec is None:
            return ""
        for field in _DISPLAY_FIELDS:
            t = rec.get(field)
            if isinstance(t, str) and t:
                name = self.tag(t)
                if name:
                    return name
        fd = rec.get("FileDescription")
        if isinstance(fd, str) and fd:
            return clean_text(fd)
        return ""

    def item_name(self, item):
        """Full item name: prefix + base + suffix (affixes that only carry a bonus line are left out)."""
        parts = []
        pre = self.display(item.get("prefix_name"))
        base = self.display(item.get("basename"))
        suf = self.display(item.get("suffix_name"))
        if pre and not ist_wertname(pre):
            parts.append(pre)
        parts.append(base or short(item.get("basename", "")))
        if suf and not ist_wertname(suf):
            parts.append(suf)
        return " ".join(p for p in parts if p)

    def faction_name(self, slot):
        """Slot -> faction name (gamefactions.dbr, then the tag, then the fixed table)."""
        if slot >= len(FACTION_SLOT_KEYS):
            return f"Slot {slot}"
        key = FACTION_SLOT_KEYS[slot]
        gf = self.record("records/game/gamefactions.dbr") or {}
        ctrl_path = gf.get(key)
        if isinstance(ctrl_path, str) and ctrl_path:
            ctrl = self.record(ctrl_path)
            if ctrl:
                n = self.tag(ctrl.get("description"))
                if n:
                    return n
        n = self.tag("tagFaction" + key.replace("faction", ""))
        if n:
            return n
        n = FACTION_FALLBACK_NAMES.get(key, "")
        if n:
            return n
        if isinstance(ctrl_path, str) and ctrl_path:
            base = ctrl_path.rsplit("/", 1)[-1].replace(".dbr", "")
            return re.sub(r"^faction(gdx\d*_)?", "", base).replace("_", " ").title()
        return key


class NullNames:
    """Fallback without a game installation: short DBR names only, no records (no calculated values)."""

    def display(self, dbr):
        return ""

    def tag(self, tag_name):
        return ""

    def item_name(self, item):
        return short(item.get("basename", ""))

    def faction_name(self, slot):
        key = FACTION_SLOT_KEYS[slot] if slot < len(FACTION_SLOT_KEYS) else f"Slot {slot}"
        return FACTION_FALLBACK_NAMES.get(key, key)
