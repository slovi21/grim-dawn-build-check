"""Read access for build designs: the game index plus design-data (data.design_data), by name.

Everything here is a lookup of game-database facts; no rule decides anything. Names are matched
case-insensitively and without punctuation ("Amatok, the Spirit of Winter" = "Amatok the Spirit of
Winter"); a prefix is accepted when it names exactly one entry ("Murmur")."""
from __future__ import annotations

import re

from . import devotions as DV

MASTERY_RE = re.compile(r"^records/skills/(playerclass\d+)/_classtraining_class\d+\.dbr$")
PET_LIMITS = ("skillMaxLevel", "skillUltimateLevel", "skillTier")     # a pet modifier takes them from its pet skill
ROW_TOLERANCE = 40          # skill window: a transmuter sits 32 px below its base skill (1.3.0.8)


def norm(name) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def match(name: str, options: dict, prefer=None) -> tuple[str | None, list, str]:
    """(key, candidates, how) for name among {key: display}. how: 'exact', 'record' (a record path or its
    basename was given), 'prefix' (unique match at a word boundary - reported to the player), 'prefer'
    (several entries with the same name, prefer(keys) chose one), '' (no match; candidates listed)."""
    raw = str(name or "").strip()
    if "/" in raw or raw.lower().endswith(".dbr"):
        tail = raw.lower().removesuffix(".dbr")
        hits = [k for k in options if k.lower().removesuffix(".dbr").endswith(tail)]
        if len(hits) == 1:
            return hits[0], [], "record"
        return None, sorted(hits)[:8], ""
    n = norm(raw)
    exact = [k for k, v in options.items() if norm(v) == n]
    if len(exact) == 1:
        return exact[0], [], "exact"
    if exact:
        chosen = prefer(exact) if prefer else None
        if chosen:
            return chosen, sorted(exact), "prefer"
        return None, sorted(exact)[:8], ""
    pref = [k for k, v in options.items() if norm(v).startswith(n + " ")]
    if len(pref) == 1:
        return pref[0], [], "prefix"
    return None, sorted({options[k] for k in pref})[:8], ""


def pick(name: str, options: dict) -> tuple[str | None, list]:
    """(key, []) for an exact, record or unique-prefix match, else (None, candidates)."""
    key, cand, _how = match(name, options)
    return key, ([] if key else cand)


ITEM_SLOT = {"ArmorProtective_Head": "head", "ArmorProtective_Chest": "chest", "ArmorProtective_Shoulders": "shoulders",
             "ArmorProtective_Hands": "hands", "ArmorProtective_Legs": "legs", "ArmorProtective_Feet": "feet",
             "ArmorProtective_Waist": "waist", "ArmorJewelry_Amulet": "amulet", "ArmorJewelry_Medal": "medal",
             "ArmorJewelry_Ring": "ring", "ItemArtifact": "relic"}
SLOT_LIMIT = {"ring": 2, "weapon": 2}
WEAPON_TYPE = {"WeaponMelee_Axe": "Axe", "WeaponMelee_Axe2h": "Axe2h", "WeaponMelee_Mace": "Mace",
               "WeaponMelee_Mace2h": "Mace2h", "WeaponMelee_Sword": "Sword", "WeaponMelee_Sword2h": "Sword2h",
               "WeaponMelee_Spear2h": "Spear2h", "WeaponMelee_Dagger": "Dagger", "WeaponMelee_Scepter": "Scepter",
               "WeaponHunting_Ranged1h": "Ranged1h", "WeaponHunting_Ranged2h": "Ranged2h",
               "WeaponArmor_Shield": "Shield", "WeaponArmor_Offhand": "Offhand"}
TWO_HANDED = ("Axe2h", "Mace2h", "Sword2h", "Spear2h", "Ranged2h")


class Catalog:
    def __init__(self, index, design: dict):
        self.index = index
        self.dd = design or {}
        self.tree = self.dd.get("skill_tree") or {}
        self.skills = self.dd.get("skills") or {}
        self.sets = self.dd.get("sets") or {}
        self.mods = self.dd.get("modifiers") or {}
        self.powers = self.dd.get("celestial_powers") or {}
        self.star_weapons = self.dd.get("star_weapons") or {}
        self._cons = None
        self._refresh = None

    # ------------------------------------------------------------------ general
    def rec(self, name) -> dict:
        return self.index.record(name) or {}

    def display(self, rec) -> str:
        """Display name. A mastery skill without its own skillDisplayName shows a technical text in the index
        ('Attack Buff Radius'); its buff record carries the real name (Olexra's Flash Freeze) - A5. Constellation
        powers keep their 'Murmur - Rumor' label. (GameIndex.display still returns the technical text: K9.)"""
        r = self.rec(rec)
        if str(rec).startswith("records/skills/") and not str(rec).startswith("records/skills/devotion/") \
                and r and not r.get("skillDisplayName"):
            buff = (self.skills.get(rec) or {}).get("buff") or r.get("buffSkillName")
            if buff and self.rec(buff).get("skillDisplayName"):
                real = self.index.display(buff)
                if real:
                    return real
        return self.index.display(rec) or str(rec).rsplit("/", 1)[-1].removesuffix(".dbr")

    def table(self, name) -> dict:
        return (getattr(self.index, "tabellen", None) or {}).get(name) or {}

    def tier_levels(self) -> list:
        return list(self.table("records/game/gameengine.dbr").get("skillMasteryTierLevel") or [])

    def player_levels(self) -> dict:
        return self.table("records/creatures/pc/playerlevels.dbr")

    # ------------------------------------------------------------------ masteries and skills
    def masteries(self) -> dict:
        """{display name: mastery record}."""
        out = {}
        for r in self.index.records:
            if MASTERY_RE.match(r):
                out[self.display(r)] = r
        return out

    def mastery_of(self, rec) -> str:
        """'playerclass10' of a mastery skill record."""
        m = re.match(r"^records/skills/(playerclass\d+)/", str(rec))
        return m.group(1) if m else ""

    def tree_skills(self, mastery_rec: str) -> list:
        cls = self.mastery_of(mastery_rec)
        # the design data lists three records the game does not have (bloodofdreeg1b, mindovermatter1b,
        # devouringswarm1b): a skill that is not in the index cannot be learned
        return sorted(r for r in self.tree if self.mastery_of(r) == cls and not MASTERY_RE.match(r)
                      and r in self.index.records)

    def granted(self, rec) -> list:
        return list((self.skills.get(rec) or {}).get("granted") or [])

    def meta(self, rec) -> dict:
        """Meta and bonus fields of a skill, its buff record filling gaps (auras keep them there); a pet modifier
        gets max/ultimate rank and tier from its pet skill (A2)."""
        base = dict(self.rec(rec))
        buff = (self.skills.get(rec) or {}).get("buff") or base.get("buffSkillName")
        if buff:
            for k, v in self.rec(buff).items():
                base.setdefault(k, v)
        pet = base.get("petSkillName")
        if pet:
            pet_rec = self.rec(pet)
            for src in (pet_rec, self.rec(pet_rec.get("buffSkillName") or "")):
                for k in PET_LIMITS:
                    if k in src:
                        base.setdefault(k, src[k])
        return base

    def cooldown_refresh(self) -> dict:
        """{record: {skill, chance, amount, trigger}} of the records that cut another skill's cooldown
        (refreshCooldown*, references/player-skills; {} when the file lacks them)."""
        if self._refresh is None:
            from ...data import player_skills as PS
            self._refresh = PS.cooldown_refresh(self.index.game_version, self.index.fingerprint)
        return self._refresh

    def template(self, rec) -> str:
        return str((self.skills.get(rec) or {}).get("template") or "")

    def needs_parent(self, rec) -> bool:
        """Modifiers, transmuters and secondary skills need their base skill (passives and weapon pool
        skills are circular too but stand alone)."""
        t = self.template(rec)
        return "modifier" in t or "transmuter" in t or t.startswith("skillsecondary")

    def parents(self, rec) -> tuple[list, bool, str]:
        """(possible base skills, all needed, source) - skillDependancy of the record, else the skill
        window: the nearest square skill to the left within ROW_TOLERANCE px up or down (modifiers sit in
        the row of their base skill, transmuters 32-38 px above or below it)."""
        s = self.skills.get(rec) or {}
        if s.get("dependency"):
            return list(s["dependency"]), bool(s.get("dependency_all")), "skillDependancy"
        me = self.tree.get(rec)
        if not me or not self.needs_parent(rec):
            return [], False, ""
        best = None
        for r, t in self.tree.items():
            if t.get("circular") or t["mastery"] != me["mastery"] or MASTERY_RE.match(r):
                continue
            if t["x"] < me["x"] and abs(t["y"] - me["y"]) <= ROW_TOLERANCE:
                if best is None or t["x"] > self.tree[best]["x"]:
                    best = r
        return ([best] if best else []), False, "skill window"

    def value(self, rec, field, rank: int, default=0.0):
        v = self.meta(rec).get(field, default)
        if isinstance(v, list):
            if rank <= 0 or not v:
                return default
            return v[min(rank, len(v)) - 1]
        return v

    # ------------------------------------------------------------------ items and sets
    def items(self) -> dict:
        """{record: display} of equippable items and relics (no blueprints, affixes, monster gear);
        Mythical versions (records/items/upgraded/) carry the game's 'Mythical' prefix."""
        out = {}
        for r, e in self.index.records.items():
            if not r.startswith("records/items/") or r.startswith(("records/items/crafting/", "records/items/lootaffixes/",
                                                                   "records/items/enemygear/")):
                continue
            t = str(e.get("t", ""))
            if e.get("n") and (t.startswith(("Armor", "Weapon")) or t == "ItemArtifact"):
                out[r] = ("Mythical " if "/upgraded/" in r else "") + e["n"]
        return out

    def item_level(self, item_rec) -> int:
        return int(self.rec(item_rec).get("levelRequirement") or 0)

    def item_slot(self, item_rec) -> tuple[str, str | None]:
        """(slot, weapon type) - weapons, shields and off-hands share the two hand slots."""
        t = str(self.rec(item_rec).get("Class") or "")
        if t in WEAPON_TYPE:
            return "weapon", WEAPON_TYPE[t]
        return ITEM_SLOT.get(t, "other"), None

    def item_set(self, item_rec) -> str | None:
        s = self.rec(item_rec).get("itemSetName")
        return str(s) if s else None

    # ------------------------------------------------------------------ devotions
    def constellations(self) -> dict:
        if self._cons is None:
            self._cons = DV.constellations(self.index)
        return self._cons

    def constellation_names(self) -> dict:
        """{key: name} plus the constellation record name (FileDescription) as an alias."""
        out = {}
        for k, c in self.constellations().items():
            out[k] = c["name"]
        return out

    def power_names(self) -> dict:
        """{power record: display}; 'Murmur - Rumor' also answers to 'Rumor'."""
        out = {}
        for k, c in self.constellations().items():
            if c["power"]:
                out[c["power"]] = self.display(c["power"])
        return out

    def weapon_needs(self, key) -> list:
        """Weapon types one of which a constellation's stars need (empty: none)."""
        need = set()
        for s in self.constellations()[key]["stars"]:
            need |= set(self.star_weapons.get(s) or [])
        return sorted(need)
