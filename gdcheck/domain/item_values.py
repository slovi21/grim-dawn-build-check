"""Item values as the game rolls them (M16, decision 71).

The numbers of an item in the game are not the values of its database record: the game rolls every value of the base item
and of its prefix, suffix and modifier from the item's seed (+-20 % for the base item, the spread of the affix for an affix) and
scales the offensive damage values by the item's `attributeScalePercent`. That `modifier_name` is the "modifier" part of the roll
is shown by Cpt Piercewolf (Physique 1,402 in the game only with it, 1,354 without; check of 21.11.2026). Not rolled: components,
augments, ascended affixes and set bonuses (their values are fixed), nor the completion bonus (`relic_bonus`, own seed `relic_seed`:
open). The video check of 21.11.2026 showed that this explains every
percentage point the check had been missing (docs/projekt/VIDEO-ABGLEICH-PLAN.md). The roll itself is
gdcheck.mechanics.item_roll; this module only lays its result over the records of one worn item.

The loader (domain.loader) takes the record of each part of an item through `with_rolled` before it reads any value from it.
An item without a seed, a part whose record the index does not know (a mod item, a newer patch) and an index before schema 12
(no roll fields) are left as they were: the values of the record, as before M16. A seed outside 0 .. 2^32 - 1 is no seed (E2).
In a what-if (counterfactual: a reroll, an augment, a component) a swapped part is rolled alone and every other part keeps
the values it has: `replaced_part`.
"""
from __future__ import annotations

from ..mechanics import item_roll as IR

# the parts of an item that are rolled, as the save names them -> as the roll names them
ROLLED_PARTS = (("basename", "base"), ("prefix_name", "prefix"), ("suffix_name", "suffix"), ("modifier_name", "modifier"))
_ROLLED_KEYS = frozenset(k for k, _p in ROLLED_PARTS)
MIN_SCHEMA = 12                 # the first index schema that carries the roll fields (attributeScalePercent, lootRandomizerJitter)
MAX_SEED = 0xFFFFFFFF           # the seed is an unsigned 32 bit number (mechanics.item_roll.RollStream)
PIN_KEY = "roll_pin"            # not a save field: `replaced_part` sets it on a counterfactual item, (item before, swapped keys)


def index_schema(index) -> int:
    """The schema of the index (0 for an object without meta: a test double, which is then never rolled)."""
    try:
        return int((getattr(index, "meta", None) or {}).get("schema") or 0)
    except (TypeError, ValueError):
        return 0


def valid_seed(seed) -> bool:
    """A seed the game can have written: an integer from 0 to 2^32 - 1. Every real save has one; an artificial item may not (E2:
    -1 or 2^32 made the stream raise and stopped the loader). Anything else counts as "no seed": the item is not rolled."""
    return isinstance(seed, int) and not isinstance(seed, bool) and 0 <= seed <= MAX_SEED


def rolled_values(index, item: dict) -> dict:
    """{save part key ("basename" | "prefix_name" | "suffix_name" | "modifier_name"): {field: rolled value}} of one worn item, {} when it cannot
    be rolled (see the module text). The values replace the record's values of the same field.

    An item made by `replaced_part` (a counterfactual: one part swapped) is not rolled again as a whole: its other parts keep
    the values they had before the swap, only the swapped part takes its values from the roll of the new item."""
    seed = item.get("seed")
    if not valid_seed(seed) or not item.get("basename"):
        return {}
    pin = item.get(PIN_KEY)
    if pin:
        before, keys = pin
        out = {k: v for k, v in rolled_values(index, before).items() if k not in keys}
        out.update({k: v for k, v in _rolled_once(index, item, seed).items() if k in keys})
        return out
    return _rolled_once(index, item, seed)


def unrolled(index, item: dict) -> bool:
    """True for an item the game rolled (it has a valid seed) that this check could not roll: a part its index does not
    know (a mod, a newer patch) or an index before schema 12. Such an item shows the values of its records (E2: the
    report says so)."""
    if not valid_seed(item.get("seed")) or not item.get("basename"):
        return False
    if index_schema(index) < MIN_SCHEMA:
        return True
    return any(index.record(item[key]) is None for key, _p in ROLLED_PARTS if item.get(key))


def replaced_part(item: dict, part: str, record: str | None) -> dict:
    """A copy of `item` with one part replaced by `record` (None removes it): the item of a counterfactual (a reroll at
    the Inventor, an augment, a component).

    The parts of an item are rolled together from one stream of its seed, so a swapped prefix or suffix would move the
    draws of every part after it and give the parts that stay new values. They must not: a part that is not swapped keeps
    EXACTLY the values it has now. The copy therefore remembers the item it comes from (`PIN_KEY`); `rolled_values` takes
    the values of every other part from that item and only the swapped part from the roll of the new item with the
    item's own seed. ASSUMED: that the game gives a swapped affix the values this roll gives is not proven (the real
    values of a reroll are random; these are the values of one example). A part that is not rolled (augment, component)
    needs no pin."""
    new = dict(item)
    if record:
        new[part] = record
    else:
        new.pop(part, None)
    if part in _ROLLED_KEYS:
        new[PIN_KEY] = (item, (part,))
    return new


def _rolled_once(index, item: dict, seed: int) -> dict:
    """The roll of the item as it is (its parts together, ignoring any pin)."""
    # the same item is built over and over (every proposal rebuilds the sources): roll it once per index
    key = (seed, *(item.get(k) or "" for k, _p in ROLLED_PARTS))
    cache = vars(index).setdefault("_roll_cache", {}) if hasattr(index, "__dict__") else None
    if cache is not None and key in cache:
        return cache[key]
    out = _roll(index, item, seed)
    if cache is not None:
        cache[key] = out
    return out


def is_relic(base_record) -> bool:
    """A relic (`ItemArtifact`): the modifier its save entry may carry is no part of it in the game (M17, see _roll)."""
    return str((base_record or {}).get("Class", "")) == "ItemArtifact"


def _roll(index, item: dict, seed: int) -> dict:
    records: dict = {}
    for key, part in ROLLED_PARTS:
        name = item.get(key)
        if not name:
            continue
        rec = index.record(name)
        if rec is None:
            return {}
        if part == "modifier" and is_relic(records.get("base")):
            # M17: the crafting modifier of a relic is neither rolled nor applied. Its draw is not in the game's roll: with
            # it the relic's lines after the character values move by one draw (Cpt Blade's Belgothian's Carnage: Physical
            # 77 / Pierce 52 / Bleeding 70 against 65 / 77 / 52 in the game, OA 53 against 54, flat Pierce 8 against 7),
            # without it every value of the relic is the game's. Cpt Wolf, Cpt Piercewolf and Cpt Blade carry such a modifier.
            continue
        records[part] = rec
    if index_schema(index) < MIN_SCHEMA:
        return {}                                   # an index before schema 12 has no roll fields
    res = IR.roll_item(records["base"], seed, records.get("prefix"), records.get("suffix"), records.get("modifier"))
    out: dict = {}
    for key, part in ROLLED_PARTS:
        values = dict(res.shares.get(part) or {})
        for line in res.proc_lines:
            if line.part == part:
                values.update(proc_line_values(line))
        if values:
            out[key] = values
    return out


def proc_line_values(line) -> dict:
    """The record fields a separated proc line stands for. A flat damage line names the stem (`offensiveFire`: its fields are
    `<stem>Min`, `<stem>Max` and `<stem>DurationMin`), a percentage line the field itself (`...Modifier`: one value)."""
    if line.field.endswith("Modifier"):
        return {line.field: line.minimum}
    out = {f"{line.field}Min": line.minimum}
    if line.maximum is not None:
        out[f"{line.field}Max"] = line.maximum
    if line.duration is not None:
        out[f"{line.field}DurationMin"] = line.duration
    return out


def with_rolled(rec: dict | None, rolled: dict, part: str) -> dict | None:
    """The record of one part with its rolled values laid over it (the record itself when nothing is rolled for it)."""
    values = rolled.get(part)
    if rec is None or not values:
        return rec
    merged = dict(rec)
    merged.update(values)
    return merged
