"""A readable label for every record of the game data index (M13, criterion 4).

GameIndex.display gives the name the game shows. Many records have none of their own: skill modifiers and pet bonuses
that an item puts on a skill, granted item skills, monster skills, crafting blueprints. They still turn up in the
patch report and in the index comparison after a patch. `label` names them by the record that points to them, the way
the report already names an item modifier ("Feral Claws: modifier from Gruldir's Tainted Cleaver"), and falls back to
the kind of record plus its file name. Pure: reads the index, writes nothing.
"""
from __future__ import annotations

from .text import short

# Kind of record -> word in front of the file name, for records nothing points to (most specific prefix first).
KIND_WORDS = (
    ("ItemArtifactFormula", "Blueprint"),
    ("ItemAscensionFormula", "Ascension recipe"),
    ("SkillSecondary_PetModifier", "Pet modifier"),
    ("Skill_Modifier", "Skill modifier"),
    ("PetPlayerScaling", "Pet scaling"),
    ("Pet", "Pet"),
    ("Skill", "Skill"),
    ("LootRandomizer", "Affix"),
    ("Item", "Item"),
    ("Armor", "Item"),
    ("Weapon", "Item"),
    ("SetPiece", "World placement"),            # SetPiece, SetPiecePool, SetPiecePart: chests, spawns, quest rooms
)
MONSTER_PATHS = ("records/skills/nonplayerskills", "records/creatures/")


def _references(index) -> dict:
    """Record -> (field, record that points to it, index n of a numbered field) for the pointer fields of the index;
    built once per index and kept on it."""
    cached = getattr(index, "_label_refs", None)
    if cached is not None:
        return cached
    refs: dict = {}
    for name, entry in sorted((getattr(index, "records", None) or {}).items()):
        meta = entry.get("m") or {}
        for n in range(1, 6):
            mod = meta.get(f"modifierSkillName{n}")
            if isinstance(mod, str) and mod:
                refs.setdefault(mod, ("modifier", name, n))
        for field, kind in (("itemSkillName", "granted"), ("petSkillName", "pet"), ("buffSkillName", "buff")):
            target = meta.get(field)
            if isinstance(target, str) and target and target != name:
                refs.setdefault(target, (kind, name, 0))
    try:
        index._label_refs = refs
    except AttributeError:
        pass
    return refs


def _kind_word(index, dbr: str) -> str:
    if dbr.startswith(MONSTER_PATHS):
        return "Monster skill" if dbr.startswith("records/skills/") else "Monster"
    rec_type = ((getattr(index, "records", None) or {}).get(dbr) or {}).get("t", "")
    return next((word for prefix, word in KIND_WORDS if rec_type.startswith(prefix)), "Record")


def label(index, dbr: str, _depth: int = 0) -> str:
    """The name of a record: the game's own name, else the name of the record that points to it, else the kind of
    record and its file name. "" only for a record the index does not hold."""
    if not dbr or dbr not in (getattr(index, "records", None) or {}):
        return ""
    own = index.display(dbr)
    if own:
        return own
    ref = _references(index).get(dbr) if _depth < 3 else None
    if ref is not None:
        kind, parent, n = ref
        parent_label = label(index, parent, _depth + 1)
        if kind == "modifier":
            target = (index.record(parent) or {}).get(f"modifiedSkillName{n}")
            skill = (index.display(target) or short(target)) if isinstance(target, str) and target else "skill"
            return f"{skill}: modifier from {parent_label}"
        if kind == "granted":
            return f"{parent_label}: granted skill"
        if kind == "pet":
            return f"{parent_label}: pet bonus"
        return parent_label
    return f"{_kind_word(index, dbr)} {short(dbr)}"


def is_derived(index, dbr: str) -> bool:
    """True when the label comes from a record that points to it (not from the game's own name, not from the
    file name)."""
    if not dbr or dbr not in (getattr(index, "records", None) or {}) or index.display(dbr):
        return False
    return _references(index).get(dbr) is not None
