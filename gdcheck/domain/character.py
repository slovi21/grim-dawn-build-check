"""Character model built from a parsed save (data.savefile)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..data import savefile
from ..data.text import DIFFICULTIES, EQUIP_SLOTS, MASTERY_NAMES, gender_text, short

ITEM_PARTS = (("basename", "item"), ("prefix_name", "affix"), ("suffix_name", "affix"),
              ("modifier_name", "transmuter"), ("transmute_name", "transmuter"),
              ("relic_name", "component"), ("relic_bonus", "component_bonus"),
              ("augment_name", "augment"), ("ascended_name", "ascended"),
              ("ascended_2h_name", "ascended"))


@dataclass
class WornItem:
    slot: str               # "Head", ..., "Main hand", "Off hand"
    item: dict              # raw item from the save
    instance_id: str        # deterministic: "<slot>#<seed>"

    @property
    def basename(self) -> str:
        return self.item.get("basename") or ""


@dataclass
class Character:
    name: str
    level: int
    male: bool
    hardcore: bool
    class_tag: str
    class_name: str
    masteries: list                 # [(number, name, bar points)]
    worn: list                      # [WornItem] incl. the active weapon set
    weapon_sets: list               # [[WornItem], ...] all sets, active first marked
    active_set: int
    skills: dict                    # record -> points
    devotion_levels: dict           # celestial power record -> power level (from the save)
    skill_entries: list             # raw block 8 skill entries
    hotbar: set
    bindings: dict                  # celestial power record -> (bound skill, controller)
    attributes_base: dict           # physique/cunning/spirit from the save
    progress: dict
    difficulty_raw: int
    stats: dict                     # block 16 (deaths, greatest damage, boss kills ...)
    stock: dict                     # augments/components in bags + character stash: record -> count
    storage_items: list             # every item in bags and stash
    doc: dict = field(repr=False, default_factory=dict)
    source_path: str = ""

    @property
    def difficulty(self) -> int:
        return min(int(self.difficulty_raw) & 0x0F, 2)

    @property
    def difficulty_label(self) -> str:
        return DIFFICULTIES[self.difficulty]

    def worn_by_slot(self) -> dict:
        return {w.slot: w for w in self.worn}


_CLASS_FORM_ABILITY = re.compile(r"^(.*/playerclass\d+/[^/]+?)_skill\d+[^/]*\.dbr$")


def _form_without_points(skill: str, pts: dict, form_hint: str = "") -> bool:
    """A class form ability (<form>_skillNN_<name>.dbr) whose form has no points: a respecced form leaves its
    abilities in 'item_skills' and on the hotbar - adding them gave a rank-1 phantom row (review .67).
    Item-granted forms (not under playerclassNN) are never dropped."""
    forms = set()
    m = _CLASS_FORM_ABILITY.match(skill or "")
    if m:
        forms.add(m.group(1) + ".dbr")
    if form_hint and "/playerclass" in form_hint and form_hint != skill:
        forms.add(form_hint)
    return any(pts.get(f, 0) < 1 for f in forms)


def _skill_points(b8, b14=None) -> dict:
    """Points per skill record from block 8 'skills'. Abilities the save names only elsewhere get 0 points
    (.67): block 8 'item_skills' (abilities with a celestial power bound to them - Feral Claws, Rip and Tear,
    rune skills) and hotbar slots whose item_name is a skill record (a form ability under its form,
    werewolf1_skill01_claws <- werewolf1.dbr). Cpt Firewolf/Raven/Piercewolf have their form abilities only
    there; block 8 points are never overwritten. Abilities of a class form without points are left out."""
    pts = {s["skill_name"]: int(s.get("level", 0) or 0) for s in b8.get("skills", [])}
    learned = dict(pts)
    for s in b8.get("item_skills", []) or []:
        if s.get("skill_name") and not _form_without_points(s["skill_name"], learned):
            pts.setdefault(s["skill_name"], 0)
    for h in (b14 or {}).get("hotslots", []) or []:
        item = str(h.get("item_name") or "")
        if h.get("skill_name") and item.startswith("records/skills/") \
                and not _form_without_points(h["skill_name"], learned, item):
            pts.setdefault(h["skill_name"], 0)
    return pts


def from_doc(doc: dict, display=lambda r: "", tag=lambda t: "") -> Character:
    """Build the Character. display(record) and tag(name) come from the index."""
    B = {b["id"]: b for b in doc["blocks"] if "id" in b}
    h = doc["header"]
    b1, b2, b3 = B.get(1, {}), B.get(2, {}), B.get(3, {})
    b8, b14, b16 = B.get(8, {}), B.get(14, {}), B.get(16, {})

    worn = []
    for i, it in enumerate(b3.get("equipment", []) or []):
        if it and it.get("basename"):
            slot = EQUIP_SLOTS[i] if i < len(EQUIP_SLOTS) else f"Slot {i}"
            worn.append(WornItem(slot, it, f"{slot}#{it.get('seed', 0)}"))
    sets, active = [], 0
    for si, ws in enumerate(b3.get("weapon_sets", []) or []):
        items = []
        for j, it in enumerate(ws.get("items", []) or []):
            if it and it.get("basename"):
                slot = ["Main hand", "Off hand"][j] if j < 2 else f"Weapon {j}"
                items.append(WornItem(slot, it, f"set{si + 1}:{slot}#{it.get('seed', 0)}"))
        sets.append(items)
    for si, ws in enumerate(b3.get("weapon_sets", []) or []):
        if not ws.get("unused", False):
            active = si
            break
    if sets:
        worn += [WornItem(w.slot, w.item, w.instance_id) for w in sets[active]]

    masteries = []
    for s in b8.get("skills", []):
        m = re.search(r"playerclass(\d+)/_classtraining_class\d+\.dbr$", s["skill_name"])
        if m:
            num = int(m.group(1))
            masteries.append((num, display(s["skill_name"]) or MASTERY_NAMES.get(num, f"Class {num}"),
                              int(s.get("level", 0))))
    masteries.sort()
    bindings = {s["autocast_skill_name"]: (s["skill_name"], s.get("autocast_controller_name", ""))
                for s in b8.get("skills", []) if s.get("autocast_skill_name")}
    for s in b8.get("item_skills", []) or []:
        if s.get("autocast_skill_name"):
            bindings[s["autocast_skill_name"]] = (s["skill_name"], s.get("autocast_controller_name", ""))

    storage = [it for sk in (b3.get("sacks") or []) for it in sk.get("items") or []]
    storage += [it for p in ((B.get(4) or {}).get("pages") or []) for it in p.get("items") or []]
    stock: dict = {}
    for it in storage:
        n = it.get("basename") or ""
        if "/enchants/" in n or "/materia/" in n:
            stock[n] = stock.get(n, 0) + max(1, int(it.get("stack_count") or 1))

    return Character(
        name=h["character_name"], level=int(h["character_level"]), male=bool(h.get("male", True)),
        hardcore=bool(h.get("hardcore")), class_tag=h.get("player_class_tag", ""),
        class_name=gender_text(tag(h.get("player_class_tag", "")), h.get("male", True)) or "",
        masteries=masteries, worn=worn, weapon_sets=sets, active_set=active,
        skills=_skill_points(b8, b14),
        devotion_levels={x["skill_name"]: int(x.get("devotion_level") or 0) for x in b8.get("skills", [])
                         if "/devotion/" in x["skill_name"] and x.get("devotion_level")},
        skill_entries=list(b8.get("skills", [])),
        hotbar={x.get("skill_name") for x in (b14.get("hotslots") or []) if x.get("skill_name")},
        bindings=bindings,
        attributes_base={"physique": b2.get("physique"), "cunning": b2.get("cunning"),
                         "spirit": b2.get("spirit")},
        progress={"attribute_points": b2.get("attribute_points"), "skill_points": b2.get("skill_points"),
                  "devotion_points": b2.get("devotion_points"),
                  "devotion_total": b2.get("total_devotion_unlocked"),
                  "greatest_difficulty": b1.get("greatest_difficulty", 0),
                  "riftgates": [len(x) for x in B.get(6, {}).get("teleporter_points", [])]},
        difficulty_raw=b1.get("last_difficulty", 2),
        stats={k: v for k, v in b16.items() if k not in ("id", "version", "skills_map", "_extra")},
        stock=stock, storage_items=storage, doc=doc, source_path=doc.get("_source_path", ""))


def load(path, display=lambda r: "", tag=lambda t: "") -> Character:
    """Read a player.gdc. Raises savefile.GDFormatError for unreadable files."""
    return from_doc(savefile.load_character(path), display, tag)


__all__ = ["Character", "WornItem", "ITEM_PARTS", "from_doc", "load", "short"]
