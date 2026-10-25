"""build.json - one planned build, resolved against the game data
(example: references/design-example-cold-werewolf-reaver.json).

    {"name": "Cold Werewolf Reaver", "game_version": "1.3.0.8", "level": 100,
     "masteries": {"Berserker": 50, "Nightblade": 50},
     "damage_type": "Cold", "main_skill": "Feral Claws",
     "weapons": ["Axe"], "dual_wield": true,
     "items": ["Cowl of Fenris", "Armor of Fenris", ...],
     "skills": {"Werewolf": 16, "Heart of Asterkarn": 12, ...},
     "devotions": ["Crossroads (Chaos)", "Jackal", ..., "-Crossroads (Chaos)", ..., "Leviathan"],
     "bindings": {"Elemental Storm": "Feral Claws", ...},
     "attributes": {"Physique": 107, "Cunning": 0, "Spirit": 0}}

devotions: the build order; '-Name' refunds a constellation. When the order fails, the final set of the
steps is searched for an order that works (proposed, never applied).
items: display names ('Mythical ...' for the mythical version) or record names (gearhead/d307_head);
several items with the same name (level variants): the highest level up to the build level is taken and
named. weapons: weapon types (Axe, Sword2h, Ranged1h, Shield, ...) - the weapons among the items are
added. Names are display names; unknown names are reported, a unique prefix is accepted and named
(catalog.match) - never silently guessed. Wrong JSON shapes raise SpecError (exit code 4).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import Catalog, match

DAMAGE_TYPES = ("Physical", "Pierce", "Fire", "Cold", "Lightning", "Acid", "Vitality", "Aether", "Chaos",
                "Bleeding")
WEAPONS = ("Axe", "Axe2h", "Dagger", "Mace", "Mace2h", "Offhand", "Ranged1h", "Ranged2h", "Scepter",
           "Shield", "Spear", "Spear2h", "Staff", "Sword", "Sword2h")
# the one-handed names players use (Axe1h = Axe); no 'Spear1h': spears are two-handed in Grim Dawn
WEAPON_ALIASES = {"axe1h": "Axe", "sword1h": "Sword", "mace1h": "Mace"}


def canonical_weapon(name: str) -> tuple[str | None, bool]:
    """(weapon type, was an alias) of a weapon name of build.json; (None, False) when unknown."""
    alias = WEAPON_ALIASES.get(str(name).lower())
    if alias:
        return alias, True
    return (name, False) if name in WEAPONS else (None, False)


@dataclass
class Resolved:
    raw: dict
    name: str = ""
    level: int = 100
    masteries: dict = field(default_factory=dict)        # mastery record -> bar points
    mastery_names: dict = field(default_factory=dict)    # mastery record -> display
    skills: dict = field(default_factory=dict)           # skill record -> points
    items: list = field(default_factory=list)            # item records
    steps: list = field(default_factory=list)            # [("+"/"-", constellation key)]
    bindings: dict = field(default_factory=dict)         # power record -> host skill record
    attributes: dict = field(default_factory=dict)       # Physique/Cunning/Spirit -> points
    weapons: list = field(default_factory=list)
    dual_wield: bool = False
    damage_type: str = ""
    main_skill: str | None = None                        # skill record
    problems: list = field(default_factory=list)         # (field, message) - names that did not resolve
    notes: list = field(default_factory=list)            # (field, message) - prefix matches, chosen variants


class SpecError(ValueError):
    """build.json has the wrong shape (a list where an object belongs, text where a number belongs)."""


def _obj(raw, key) -> dict:
    v = raw.get(key) or {}
    if not isinstance(v, dict):
        raise SpecError(f"'{key}' must be an object {{name: value}}, not {type(v).__name__}")
    return v


def _lst(raw, key) -> list:
    v = raw.get(key) or []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise SpecError(f"'{key}' must be a list of names")
    return v


def _int(v, where) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v) or v < 0:
        raise SpecError(f"{where}: '{v}' is no whole number >= 0")
    return int(v)


def _skill_options(cat: Catalog, masteries) -> dict:
    """{record: display} of every skill of the chosen masteries and the skills their forms grant."""
    out = {}
    for m in masteries:
        for r in cat.tree_skills(m):
            out[r] = cat.display(r)
            for g in cat.granted(r):
                out[g] = cat.display(g)
    return out


def _note(s: Resolved, fld: str, name: str, key, how: str, cat_display, cand=()) -> None:
    if how == "prefix":
        s.notes.append((fld, f"'{name}' read as '{cat_display(key)}'"))
    elif how == "prefer":
        s.notes.append((fld, f"'{name}': {len(cand)} items of that name - took {key.removeprefix('records/items/')} "
                             f"(give the record name for another one)"))


def resolve(raw: dict, cat: Catalog) -> Resolved:
    if not isinstance(raw, dict):
        raise SpecError("build.json must be one JSON object")
    s = Resolved(raw=raw, name=str(raw.get("name") or "build"), level=_int(raw.get("level", 100), "level") or 100)
    known = cat.masteries()
    for name, bar in _obj(raw, "masteries").items():
        rec, cand, how = match(name, {v: k for k, v in known.items()})
        if rec is None:
            s.problems.append(("masteries", f"unknown mastery '{name}' (known: {', '.join(sorted(known))})"))
            continue
        s.masteries[rec] = _int(bar, f"masteries.{name}")
        s.mastery_names[rec] = cat.display(rec)
    if len(s.masteries) > 2:
        s.problems.append(("masteries", f"{len(s.masteries)} masteries - a character has at most two"))
    opts = _skill_options(cat, s.masteries)
    granted = {g: r for m in s.masteries for r in cat.tree_skills(m) for g in cat.granted(r)}
    for name, pts in _obj(raw, "skills").items():
        rec, cand, how = match(name, opts)
        if rec is None:
            s.problems.append(("skills", f"'{name}' is no skill of {', '.join(s.mastery_names.values()) or '?'}"
                               + (f" (did you mean: {', '.join(cand)})" if cand else "")))
            continue
        if rec in granted:
            s.problems.append(("skills", f"{cat.display(rec)} has no points of its own - it runs on the rank of "
                                         f"{cat.display(granted[rec])}"))
            continue
        _note(s, "skills", name, rec, how, cat.display)
        s.skills[rec] = s.skills.get(rec, 0) + _int(pts, f"skills.{name}")
    items = cat.items()

    def best_level(cands):
        fit = [c for c in cands if cat.item_level(c) <= s.level] or list(cands)
        return max(fit, key=lambda c: (cat.item_level(c), c))
    for name in _lst(raw, "items"):
        rec, cand, how = match(name, items, best_level)
        if rec is None:
            s.problems.append(("items", f"unknown item '{name}'" + (f" ({', '.join(cand)})" if cand else "")))
        else:
            _note(s, "items", name, rec, how, cat.display, cand)
            s.items.append(rec)
    cons = cat.constellation_names()
    for entry in _lst(raw, "devotions"):
        e = entry.strip()
        action = "-" if e.startswith("-") else "+"
        key, cand, how = match(e.lstrip("+-").strip(), cons)
        if key is None:
            s.problems.append(("devotions", f"unknown constellation '{e}'" + (f" ({', '.join(cand)})" if cand else "")))
        else:
            _note(s, "devotions", e.lstrip("+-").strip(), key, how, lambda k: cons[k])
            s.steps.append((action, key))
    powers = cat.power_names()
    short = {k: v.split(" - ")[-1] for k, v in powers.items()}
    for pname, host in _obj(raw, "bindings").items():
        prec, cand, how = match(pname, powers)
        if prec is None:
            prec, cand, how = match(pname, short)
        hrec, hcand, hhow = match(str(host), opts)
        if prec is None:
            s.problems.append(("bindings", f"unknown celestial power '{pname}'" + (f" ({', '.join(cand)})" if cand else "")))
        elif hrec is None:
            s.problems.append(("bindings", f"'{host}' (host of {pname}) is no skill of this build"))
        else:
            _note(s, "bindings", str(host), hrec, hhow, cat.display)
            s.bindings[prec] = hrec
    for k, v in _obj(raw, "attributes").items():
        kk = str(k).capitalize()
        if kk not in ("Physique", "Cunning", "Spirit"):
            s.problems.append(("attributes", f"unknown attribute '{k}'"))
        else:
            s.attributes[kk] = _int(v, f"attributes.{k}")
    for w in _lst(raw, "weapons"):
        wtype, alias = canonical_weapon(w)
        if wtype is None:
            s.problems.append(("weapons", f"unknown weapon type '{w}' (one of {', '.join(WEAPONS)})"))
            continue
        if alias:
            s.notes.append(("weapons", f"'{w}' read as '{wtype}'"))
        if wtype not in s.weapons:
            s.weapons.append(wtype)
    for it in s.items:
        slot, wtype = cat.item_slot(it)
        if wtype and wtype not in s.weapons:
            s.weapons.append(wtype)
    s.dual_wield = bool(raw.get("dual_wield"))
    dt = str(raw.get("damage_type") or "").capitalize()
    if dt and dt not in DAMAGE_TYPES:
        s.problems.append(("damage_type", f"unknown damage type '{dt}'"))
    s.damage_type = dt
    if raw.get("main_skill"):
        rec, cand, how = match(str(raw["main_skill"]), opts)
        if rec is None:
            s.problems.append(("main_skill", f"'{raw['main_skill']}' is no skill of this build"))
        s.main_skill = rec
    return s
