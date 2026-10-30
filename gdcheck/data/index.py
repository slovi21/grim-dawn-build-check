"""Game data index (gd-index-<version>.json.gz) with a fast load cache.

GameIndex.load(path, cache=<bytes|None>) - the cache is a marshal dump of the
parsed JSON keyed by index hash + Python version (index_cache.py). Writing
the cache file is the job of gdcheck.app (only app writes files)."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from . import index_cache
from .text import FACTION_FALLBACK_NAMES, FACTION_SLOT_KEYS, clean_text, ist_wertname, short

SUPPORTED_SCHEMA = 9
# The most an index of the online check may be once unpacked. The web index of the golden game data is 33.0 MB (31.4 MiB;
# the whole index, with the loot tables, 130 MiB and never online): 128 MiB leaves room to grow and stops a small file
# that unpacks to gigabytes (a gzip bomb with matching checksums) before it is parsed.
MAX_INDEX_UNPACKED = 128 * 1024 * 1024


class IndexError_(Exception):
    """Index missing, unreadable or of an unsupported schema."""


class SchemaError(IndexError_):
    """The index is whole but of another schema than this engine reads (the web entry says "updating" to that)."""


def read_json(path: Path, max_bytes: int | None = None) -> dict:
    """The parsed index. With `max_bytes` the unpacked JSON may not be longer than that (IndexError_ otherwise): at most
    max_bytes + 1 bytes are ever unpacked (in blocks of 1 MiB, so the memory held stays near the limit), so a small file
    that expands to gigabytes is refused before it is parsed."""
    try:
        if max_bytes is None:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return json.load(fh)
        raw = bytearray()
        with gzip.open(path, "rb") as fh:
            while len(raw) <= max_bytes:
                block = fh.read(min(1 << 20, max_bytes + 1 - len(raw)))
                if not block:
                    break
                raw += block
        if len(raw) > max_bytes:
            raise IndexError_(f"index is larger than {max_bytes:,} bytes unpacked: {path}")
        return json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, EOFError) as ex:
        raise IndexError_(f"index could not be read: {path} ({ex})") from ex


class GameIndex:
    """Game data from a gd-index-*.json.gz (schema 9). Same interface as the
    .35 IndexDB, so the frozen candidate generators can use it unchanged."""

    def __init__(self, pfad: str | Path, daten: dict | None = None, loaders: dict | None = None):
        self.pfad = Path(pfad)
        if daten is None:
            daten = read_json(self.pfad)
        self.meta = daten.get("_meta", {})
        self.records = daten.get("records", {})
        self.tags = daten.get("tags", {})
        self.tabellen = daten.get("tabellen", {})
        # ab Schema 2: Ausloeser der Item-Skills und Zeitfelder in "m"
        self.controller = daten.get("controller", {})
        self.schema = self.meta.get("schema", 1)
        # ab Schema 4: Item -> {"prefix": [[affix, gewicht]], "suffix": [...]}
        self._loaders = dict(loaders or {})
        self._raw = {n: daten[n] for n in ("loot_tabellen", "bosse") if n in daten}
        self._loot = None
        self._bosse = None
        # ab Schema 5: UI-Records der Sternbilder (Affinitaet, Sterne)
        self.sternbild_roh = {k.lower(): v for k, v in (daten.get("sternbilder") or {}).items()}
        # ab Schema 6: Boss-Monster mit Skills (bossdaten.py)

        self._cache: dict[str, dict | None] = {}

    # -- Beschreibung ------------------------------------------------------

    @property
    def game_version(self) -> str:
        return self.meta.get("game_version", "?")

    @property
    def fingerprint(self) -> str:
        return self.meta.get("fingerprint", "")

    def beschreibung(self) -> str:
        m = self.meta
        return (f"Index {m.get('game_version','?')} of {m.get('erstellt','?')}. "
                f"{m.get('records', len(self.records)):,} records, "
                f"{len(self.tags):,} tags")

    # -- NameDB-Schnittstelle ----------------------------------------------

    def record(self, name):
        """Record als flaches dict, wie es die Auswertung erwartet.

        Zusammengesetzt aus den Metafeldern und den Bonusfeldern des Index.
        Enthaelt 'Class', damit die Passiv-/Aura-Erkennung greift, und
        'buffSkillName', damit Aura-Werte gefunden werden."""
        if not name:
            return None
        if name in self._cache:
            return self._cache[name]
        eintrag = self.records.get(name)
        if eintrag is None:
            self._cache[name] = None
            return None
        rec: dict = {"_recordname": name, "_type": eintrag.get("t", "")}
        rec.update(eintrag.get("m") or {})
        rec.update(eintrag.get("f") or {})
        rec.setdefault("Class", eintrag.get("t", ""))
        if eintrag.get("n"):
            rec["_name"] = eintrag["n"]
        self._cache[name] = rec
        return rec

    def tag(self, tag_name):
        if not tag_name:
            return ""
        return clean_text(self.tags.get(tag_name, ""))

    def display(self, dbr, _tiefe=0):
        """Anzeigename. Der Index haelt ihn bereits aufgeloest vor.

        Skills ohne eigenen Namen (Auren, Toggles, Pet-Modifikatoren) tragen
        ihn im Buff- oder Pet-Skill: buffSkillName, petSkillName, sonst die
        Nachbar-Records <name>_buff.dbr / <name>buff.dbr (Field Command)."""
        if not dbr:
            return ""
        eigen = self._display_eigen(dbr)
        if _tiefe == 0 and eigen:
            return self._buff_name(dbr) or eigen
        if eigen or _tiefe > 1:
            return eigen
        e = self.records.get(dbr) or {}
        m = e.get("m") or {}
        kandidaten = [m.get("buffSkillName"), m.get("petSkillName")]
        if dbr.endswith(".dbr"):
            kandidaten += [dbr[:-4] + "_buff.dbr", dbr[:-4] + "buff.dbr"]
        for k in kandidaten:
            if isinstance(k, str) and k and k != dbr:
                n = self.display(k, _tiefe + 1)
                if n:
                    return n
        return ""

    def _buff_name(self, dbr):
        """Name of the buff of a mastery or item skill that has no skillDisplayName of its own: the index holds a
        technical text for it ('Attack Buff Radius', 'Applies Warcry aoe buff'), the buff record the real name
        (Catalog.display, A5/K9). Celestial Powers (records/skills/devotion/) keep their label."""
        if not str(dbr).startswith("records/skills/") or str(dbr).startswith("records/skills/devotion/"):
            return ""
        m = (self.records.get(dbr) or {}).get("m") or {}
        buff = m.get("buffSkillName")
        if m.get("skillDisplayName") or not isinstance(buff, str) or not buff or buff == dbr:
            return ""
        if not ((self.records.get(buff) or {}).get("m") or {}).get("skillDisplayName"):
            return ""
        return self._display_eigen(buff)

    def _display_eigen(self, dbr):
        if not dbr:
            return ""
        eintrag = self.records.get(dbr)
        if eintrag is None:
            return ""
        if eintrag.get("n"):
            return eintrag["n"]
        # Rueckfall ueber die Tags, falls beim Indexbau kein Name gefunden wurde
        for feld in ("itemNameTag", "description", "skillDisplayName",
                     "lootRandomizerName", "skillTabTitle"):
            t = (eintrag.get("m") or {}).get(feld)
            if isinstance(t, str) and t:
                name = self.tag(t)
                if name:
                    return name
        return ""

    def item_name(self, item):
        """Praefix + Basis + Suffix, ohne Affixe, die nur Bonuswerte sind."""
        teile = []
        pre = self.display(item.get("prefix_name"))
        base = self.display(item.get("basename"))
        suf = self.display(item.get("suffix_name"))
        if pre and not ist_wertname(pre):
            teile.append(pre)
        teile.append(base or short(item.get("basename", "")))
        if suf and not ist_wertname(suf):
            teile.append(suf)
        return " ".join(t for t in teile if t)

    def faction_name(self, slot):
        """Fraktionsname. Der Index fuehrt gamefactions.dbr nicht, deshalb
        die feste Zuordnung wie im Rueckfallbetrieb."""
        key = FACTION_SLOT_KEYS[slot] if slot < len(FACTION_SLOT_KEYS) else f"Slot {slot}"
        name = self.tag("tagFaction" + key.replace("faction", ""))
        if name:
            return name
        return FACTION_FALLBACK_NAMES.get(key, key)

    # -- Zusatz, den NameDB nicht hat --------------------------------------

    def tabelle(self, pfad: str) -> dict:
        """Eine der mitgelieferten Level-/Formeltabellen."""
        return self.tabellen.get(pfad, {})

    def _nach_typ(self, typ: str) -> list:
        """Records of one class, built once per index (gear search runs ~250 queries)."""
        cache = self.__dict__.setdefault("_typ_cache", {})
        if typ not in cache:
            cache[typ] = [(k, v) for k, v in self.records.items() if v.get("t") == typ]
        return cache[typ]

    def suche(self, **bedingungen):
        """Records filtern. Beispiele:

            db.suche(typ="ItemEnchantment", slot="ring", feld="defensiveAether")
            db.suche(feld="characterDefensiveAbility", mindestens=30)
        """
        typ = bedingungen.get("typ")
        slot = bedingungen.get("slot")
        feld = bedingungen.get("feld")
        mindestens = bedingungen.get("mindestens", 0)
        pfad_enthaelt = bedingungen.get("pfad_enthaelt")

        treffer = []
        quelle = self._nach_typ(typ) if typ else self.records.items()
        for k, v in quelle:
            if pfad_enthaelt and pfad_enthaelt not in k:
                continue
            if slot:
                slots = (v.get("m") or {}).get("slots") or []
                if slot not in slots:
                    continue
            if feld:
                wert = (v.get("f") or {}).get(feld)
                if not isinstance(wert, (int, float)) or wert <= mindestens:
                    continue
            treffer.append((k, v))
        if feld:
            treffer.sort(key=lambda x: -(x[1]["f"][feld]))
        return treffer


    # -- lazily loaded large parts ------------------------------------------

    def _part(self, name):
        if name in self._raw:
            return self._raw.pop(name) or {}
        loader = self._loaders.pop(name, None)
        return (loader() if loader else {}) or {}

    @property
    def loot_tabellen(self) -> dict:
        """ab Schema 4: Item -> {"prefix": [[affix, weight]], ...} (lower-case keys)"""
        if self._loot is None:
            self._loot = {k.lower(): v for k, v in self._part("loot_tabellen").items()}
        return self._loot

    @property
    def bosse_roh(self) -> dict:
        """ab Schema 6: boss monsters with their skills"""
        if self._bosse is None:
            self._bosse = self._part("bosse")
        return self._bosse

    # -- English aliases ------------------------------------------------------
    search = suche
    table = tabelle

    @classmethod
    def load(cls, path, cache: bytes | None = None, make_cache: bool = True,
             max_bytes: int | None = None) -> tuple["GameIndex", bytes | None]:
        """(index, new cache bytes or None). Pass the bytes of an existing cache
        file; if they do not match the index, the JSON is read and fresh cache
        bytes are returned for the caller to store. With make_cache=False no
        cache is built (the browser keeps none): the second item is None. With
        max_bytes the JSON read from the file may not be longer unpacked (IndexError_)."""
        path = Path(path)
        if not path.exists():
            raise IndexError_(f"index not found: {path}")
        key = index_cache.key(path) if (cache is not None or make_cache) else None      # the key hashes the whole file
        if cache is not None:
            hit = index_cache.loads(cache, key)
            if hit is not None:
                core, loaders = hit
                return cls._checked(path, core, loaders), None
        data = read_json(path, max_bytes)
        return cls._checked(path, data), (index_cache.dumps(data, key) if make_cache else None)

    @classmethod
    def _checked(cls, path, data, loaders=None):
        idx = cls(path, data, loaders)
        if idx.schema != SUPPORTED_SCHEMA:
            raise SchemaError(f"index schema {idx.schema} not supported (need {SUPPORTED_SCHEMA}); "
                              "rebuild it with GD-Tools")
        return idx
