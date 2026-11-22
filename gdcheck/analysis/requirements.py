"""Worn items the game switches off (gap 1 of M14, decision 69).

An item whose Physique, Cunning or Spirit requirement the character does not meet gives nothing in the game (its
background turns red on the character sheet; Cpt Wolf 19.11.2026: Spirit 327, amulet and both rings of level 94 need
at least 364). The check takes such items out before anything is computed - like the game - and names them as the
first problem. An item that loses its attribute bonuses can switch off another one, so it repeats until nothing changes.
The items are taken out of `Character.worn` and of the save document (`Character.doc`): the save facts, the rules and
the item skill bonuses read the document (review .85, MAJOR 1).

No false hint (decision 69 (5)): an item is switched off only when the attribute of the check is more than
REQ_TOLERANCE below the requirement (the in-game check found the check's attributes up to 7 points off), and only when
the index knows the item's cost record and its equation evaluates (gdcheck.mechanics.requirements).

Weapons are not switched off (review .85, MAJOR 2): how the game treats a weapon whose requirement is not met is not
confirmed in game, and taking it out would turn a melee build into a caster while the weapon rules still see it. Only the
twelve equipment slots (EQUIP_SLOTS) are checked; a weapon that misses its requirement gets no hint (a missed hint, never
a false one).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from ..data import gametables as GT
from ..data.text import EQUIP_SLOTS
from ..domain import loader
from ..mechanics import requirements as RQ
from ..stats import sheet as SH

REQ_TOLERANCE = 10          # points; HEURISTIC from the in-game check of M14 (attribute deviations up to 7)
CHECKED_SLOTS = frozenset(EQUIP_SLOTS)          # not "Main hand"/"Off hand" (see the module text)
REDUCTION_FIELDS = ("characterGlobalReqReduction",) + tuple(
    f"character{cat}{name}ReqReduction" for cat in ("Armor", "Jewelry", "Shield", "Weapon", "Melee", "Hunting")
    for name in RQ.ATTRS.values())


@dataclass(frozen=True)
class DisabledItem:
    """One attribute an item misses; an item that misses two attributes has two entries (review .85, MAJOR 3)."""
    slot: str               # "Amulet", "Ring 1", ... as the character holds it
    name: str               # the item's name (prefix, base, suffix)
    attribute: str          # "physique" | "cunning" | "spirit"
    have: int               # the attribute of the check without the switched-off items, rounded down (shown_attribute)
    need: int               # the requirement (at least: affixes are not counted)


ATTR_LABEL = {"physique": "Physique", "cunning": "Cunning", "spirit": "Spirit"}


def shown_attribute(value) -> int:
    """The attribute as the hint shows it: rounded down, so the shown gap to the requirement is always more than
    REQ_TOLERANCE, as the rule says (Cpt Blade: 356.8 -> 356, needs 367; rounded it read 357, exactly 10 below;
    review .85, MINOR 5)."""
    return int(math.floor(value)) if isinstance(value, (int, float)) and math.isfinite(value) else 0


def slots(disabled) -> list[str]:
    """The slots of the switched-off items, each once, in the order of the entries."""
    out: list[str] = []
    for d in disabled:
        if d.slot not in out:
            out.append(d.slot)
    return out


def _join(words) -> str:
    words = list(words)
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _items_text(disabled) -> str:
    """'Amulet (Peerless Eye of Beronath), Ring 1 and Ring 2 (Entropic Coil)': slots of the same item are joined."""
    groups: dict[str, list[str]] = {}
    for d in disabled:
        group = groups.setdefault(d.name, [])
        if d.slot not in group:
            group.append(d.slot)
    return ", ".join(f"{_join(where)} ({name})" if name else _join(where) for name, where in groups.items())


def texts(disabled) -> dict[str, str]:
    """The words of the hint (decision 69 (2)): which items, every attribute they miss with what the check counts and
    what the items need, the way out. {"title", "short", "detail", "fix"}; {} without switched-off items."""
    if not disabled:
        return {}
    attrs: dict[str, list] = {}
    for d in disabled:
        attrs.setdefault(d.attribute, []).append(d)
    need = {a: max(d.need for d in ds) for a, ds in attrs.items()}
    have = {a: ds[0].have for a, ds in attrs.items()}
    values = "; ".join(f"{ATTR_LABEL[a]} {have[a]}, needs at least {need[a]}" for a in attrs)
    raise_ = _join(f"{ATTR_LABEL[a]} to {need[a]}" for a in attrs)
    title = f"Switched off by the game: {_items_text(disabled)}"
    per_slot = "; ".join(
        f"{slot} needs " + _join(f"{ATTR_LABEL[d.attribute]} {d.need}" for d in disabled if d.slot == slot)
        for slot in slots(disabled))
    return {"title": title, "short": f"{title} - {values}",
            "detail": f"{per_slot}; you have {', '.join(f'{ATTR_LABEL[a]} {have[a]}' for a in attrs)}. The game gives "
                      f"nothing for these items - every value of this report is without them.",
            "fix": f"Raise {raise_} or swap the item{'s' if len(slots(disabled)) > 1 else ''}."}


def item_requirements(index, item: dict, reductions: dict) -> dict[str, int]:
    """{attribute: requirement} of one worn item; {} when the index cannot tell (no cost record, older index)."""
    rec = index.record(item.get("basename")) or {}
    formulas = GT.cost_formulas(index, rec.get("itemCostName"))
    if not formulas:
        return {}
    return RQ.requirements(rec.get("Class"), rec.get("itemLevel"), rec, formulas, reductions)


def _slot_name(i: int) -> str:
    return EQUIP_SLOTS[i] if i < len(EQUIP_SLOTS) else f"Slot {i}"           # as domain.character.from_doc names it


def doc_without(doc, gone) -> dict:
    """A copy of the save document whose equipment slots `gone` are empty (basename "", as the game writes an empty
    slot). Shallow: the document of the character is not changed."""
    if not gone or not isinstance(doc, dict) or not doc.get("blocks"):
        return doc
    blocks = []
    for b in doc["blocks"]:
        if isinstance(b, dict) and b.get("id") == 3 and b.get("equipment"):
            b = dict(b, equipment=[dict(it, basename="") if it and _slot_name(i) in gone else it
                                   for i, it in enumerate(b["equipment"])])
        blocks.append(b)
    return dict(doc, blocks=blocks)


def _misses(index, worn, abilities: dict, reductions: dict, keep=frozenset()) -> list[DisabledItem]:
    """Every attribute a checked item misses by more than the tolerance; `keep`: (slot, attribute) pairs listed anyway
    (an item already switched off keeps the attribute it was switched off for)."""
    out = []
    for w in worn:
        if w.slot not in CHECKED_SLOTS:
            continue
        for attr, need in sorted(item_requirements(index, w.item, reductions).items()):
            have = abilities.get(attr)
            missed = isinstance(have, (int, float)) and have + REQ_TOLERANCE < need
            if missed or (w.slot, attr) in keep:
                out.append(DisabledItem(w.slot, index.item_name(w.item), attr, shown_attribute(have), need))
    return out


def active_state(ctx):
    """(ctx, sources, snapshot, disabled items) of the character as the game runs it: items whose requirement is not
    met are taken out of the character (`worn` and the save document), repeated until nothing changes. Without such items
    ctx is returned as it is, and the sources and snapshot are the ones the analysis needs anyway (no extra work)."""
    ch = ctx.character
    off: dict[tuple[str, str], DisabledItem] = {}
    for _ in range(len(ch.worn) + 1):
        gone = frozenset(slot for slot, _attr in off)
        cur = ctx if not gone else replace(ctx, character=replace(
            ch, worn=[w for w in ch.worn if w.slot not in gone], doc=doc_without(ch.doc, gone)))
        sources = loader.build_sources(cur.character, cur.index)
        snap = SH.build(cur, sources)
        abilities, fields = snap.sheet.abilities, snap.sheet.fields
        reductions = {k: fields.get(k, 0.0) for k in REDUCTION_FIELDS if fields.get(k)}
        found = _misses(ctx.index, cur.character.worn, abilities, reductions)
        if not found:
            # every switched-off item against the final attributes: all it misses now, with the values without them
            final = _misses(ctx.index, [w for w in ch.worn if w.slot in gone], abilities, reductions, frozenset(off))
            return cur, sources, snap, tuple(final)
        off.update(((d.slot, d.attribute), d) for d in found)
    raise RuntimeError("switching off items did not settle")         # cannot happen: each round takes one item at least
