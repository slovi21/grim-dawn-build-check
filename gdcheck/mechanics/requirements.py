"""Attribute requirements of items (gap 1 of M14, decision 69) - pure.

The game computes the Physique, Cunning and Spirit an item needs from the equations of its cost record
(`itemCostName` -> records/game/itemcostformulas*.dbr): `<prefix>StrengthEquation` (Physique), `...DexterityEquation`
(Cunning), `...IntelligenceEquation` (Spirit), with the variables `itemLevel` and, for rings and amulets,
`totalAttCount`. The equations are evaluated by a small parser of their own (numbers, + - * / ^, brackets, variables) -
never by eval. Anything the parser or the data cannot answer gives no requirement, so the check never switches an item
off on a guess.
"""
from __future__ import annotations

import math

from .policy import Confidence, rule

ATTRS = {"physique": "Strength", "cunning": "Dexterity", "spirit": "Intelligence"}
TWO_HANDED_MELEE = {"axe2h", "mace2h", "sword2h", "spear2h"}
NO_REQUIREMENT = {"medal"}                      # medals: level only (grimtools 1.3.1.2: 404 of 404 without attributes)
_DIGITS = "0123456789"
_NAME_START = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"


class _Bad(Exception):
    """The equation cannot be evaluated (syntax, unknown variable, no real result)."""


def _tokens(expr: str) -> list:
    """Numbers (1, 2.5, .5), names (ASCII letters, digits, _) and the operators + - * / ^ ( ); anything else is an error."""
    out, i, n = [], 0, len(expr)
    while i < n:
        c = expr[i]
        if c in " \t\r\n":
            i += 1
        elif c in _DIGITS or (c == "." and i + 1 < n and expr[i + 1] in _DIGITS):
            j = i
            while j < n and expr[j] in _DIGITS:
                j += 1
            if j < n and expr[j] == ".":
                j += 1
                if j >= n or expr[j] not in _DIGITS:
                    raise _Bad("number")
                while j < n and expr[j] in _DIGITS:
                    j += 1
            out.append(("num", float(expr[i:j])))
            i = j
        elif c in _NAME_START:
            j = i
            while j < n and (expr[j] in _NAME_START or expr[j] in _DIGITS):
                j += 1
            out.append(("name", expr[i:j]))
            i = j
        elif c in "+-*/^()":
            out.append(("op", c))
            i += 1
        else:
            raise _Bad(c)
    return out


class _Parser:
    """expr := term (('+'|'-') term)*; term := unary (('*'|'/') unary)*; unary := '-' unary | power;
    power := atom ('^' unary)?  (right-associative); atom := number | name | '(' expr ')'."""

    def __init__(self, tokens, variables):
        self.t, self.i, self.v = tokens, 0, variables

    def _peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def _take(self, op=None):
        tok = self._peek()
        if tok[0] is None or (op is not None and tok != ("op", op)):
            raise _Bad(op)
        self.i += 1
        return tok

    def parse(self) -> float:
        value = self._expr()
        if self.i != len(self.t):
            raise _Bad("trailing")
        return value

    def _expr(self) -> float:
        value = self._term()
        while self._peek() in (("op", "+"), ("op", "-")):
            op = self._take()[1]
            right = self._term()
            value = value + right if op == "+" else value - right
        return value

    def _term(self) -> float:
        value = self._unary()
        while self._peek() in (("op", "*"), ("op", "/")):
            op = self._take()[1]
            right = self._unary()
            if op == "/" and right == 0:
                raise _Bad("division by zero")
            value = value * right if op == "*" else value / right
        return value

    def _unary(self) -> float:
        if self._peek() == ("op", "-"):
            self._take()
            return -self._unary()
        return self._power()

    def _power(self) -> float:
        base = self._atom()
        if self._peek() == ("op", "^"):
            self._take()
            exponent = self._unary()
            result = base ** exponent
            if isinstance(result, complex) or math.isnan(result) or math.isinf(result):
                raise _Bad("no real result")
            return result
        return base

    def _atom(self) -> float:
        kind, value = self._take()
        if kind == "num":
            return value
        if kind == "name":
            if value not in self.v:
                raise _Bad(value)
            return float(self.v[value])
        if (kind, value) == ("op", "("):
            inner = self._expr()
            self._take(")")
            return inner
        raise _Bad(value)


def evaluate(expr, variables: dict) -> float | None:
    """The value of a cost equation of the game, or None (no text, syntax error, unknown variable, division by zero,
    no real or no finite result, nesting too deep)."""
    if not isinstance(expr, str) or not expr.strip():
        return None
    try:
        value = _Parser(_tokens(expr), variables).parse()
    except (_Bad, OverflowError, ValueError, TypeError, ZeroDivisionError, RecursionError):
        return None
    return value if math.isfinite(value) else None             # 10^300*10^300 is inf without an exception


def equation_prefix(item_class) -> str | None:
    """The prefix of the cost equations of an item class: the part after '_' with a small first letter
    ("ArmorJewelry_Ring" -> "ring"), "melee2h" for two-handed melee weapons; None for classes without a requirement."""
    if not isinstance(item_class, str) or "_" not in item_class:
        return None
    part = item_class.split("_", 1)[1]
    if not part:
        return None
    prefix = part[0].lower() + part[1:]
    if prefix.lower() in TWO_HANDED_MELEE:
        return "melee2h"
    return None if prefix in NO_REQUIREMENT else prefix


# fields of an item record that are no value line of its tooltip
_NOT_A_LINE = {"Class", "itemClassification", "itemLevel", "levelRequirement", "itemSkillName", "itemSkillLevelEq",
               "itemSkillAutoController", "itemSkillLevel", "itemSetName", "artifactClassification", "relicBonus",
               "factionSource", "itemCost", "itemCostName", "soulbound", "buffSkillName", "petSkillName",
               "conversionInType", "conversionOutType", "conversionInType2", "conversionOutType2", "weaponDamagePct",
               "attributeScalePercent", "lootRandomizerJitter"}         # roll fields of the index (schema 12), no value line
_NOT_A_LINE_PREFIX = ("_", "skill", "augmentSkillLevel", "augmentMasteryLevel", "modifiedSkillName")
# parts of one line: a damage range, a damage over time with its chance and duration, a chance of a modifier
# (longest first, so "DurationMin" is cut as a whole)
_LINE_SUFFIX = ("DurationMin", "DurationMax", "Chance", "Min", "Max")


def _line_key(field: str) -> str:
    if not field.startswith(("offensive", "retaliation")):
        return field
    cut = True
    while cut:
        cut = False
        for suffix in _LINE_SUFFIX:
            if field.endswith(suffix) and len(field) > len(suffix):
                field, cut = field[: -len(suffix)], True
                break
    return field


def _counts(value) -> bool:
    if isinstance(value, (int, float)):
        return value != 0
    return isinstance(value, str) and bool(value.strip())


@rule("attribute_count", Confidence.TESTED,
      "totalAttCount of the cost equations = the value lines of the item: every field other than 0 counts 1, a damage "
      "range, a damage over time with chance and duration and each conversion (types and percentage) count 1, each "
      "augmentSkillName/augmentMasteryName/augmentAllLevel and each skill modifier block counts 1, the granted skill "
      "counts 0 (grimtools 1.3.1.2: 388 of 388 rings and 420 of 420 amulets exact, research M14, 20.11.2026; the 10 "
      "jewelry records with a second conversion were not among them). Affixes are not counted: a lower bound, so the "
      "requirement is never too high")
def attribute_count(fields: dict) -> int:
    lines = set()
    for field, value in (fields or {}).items():
        if field.startswith("conversionPercentage"):             # conversionPercentage, conversionPercentage2
            if _counts(value):
                lines.add(field)
            continue
        if field in _NOT_A_LINE or field.startswith(_NOT_A_LINE_PREFIX) or not _counts(value):
            continue
        lines.add(_line_key(field))
    return len(lines)


def reduction_categories(item_class) -> tuple[str, ...]:
    """The categories of requirement reductions (character<Category><Attr>ReqReduction) that apply to an item class;
    a doubtful category is included, so a reduction lowers the requirement rather than switching an item off."""
    c = item_class or ""
    if c.startswith("ArmorJewelry"):
        return ("Jewelry",)
    if c.startswith("ArmorProtective"):
        return ("Armor",)
    if c == "WeaponArmor_Shield":
        return ("Shield", "Armor")
    if c.startswith("WeaponHunting"):
        return ("Weapon", "Hunting")
    if c.startswith("WeaponMelee"):
        return ("Weapon", "Melee")
    if c.startswith("WeaponArmor"):
        return ("Weapon",)
    return ()


def round_half_up(value: float) -> int:
    """Rounded half up like the game's requirements (92.5 -> 93, 74.49 -> 74); requirements are positive."""
    return int(math.floor(value + 0.5))


@rule("item_requirement", Confidence.TESTED,
      "Physique/Cunning/Spirit an item needs: <prefix><Strength|Dexterity|Intelligence>Equation of its itemCostName "
      "record (game database), reduced by characterGlobalReqReduction and character<Category><Attr>ReqReduction (%), "
      "rounded half up (grimtools 1.3.1.2 matched for every armor, weapon and jewelry class; research M14, 20.11.2026)")
def requirements(item_class, item_level, fields: dict, formulas: dict, reductions: dict) -> dict[str, int]:
    """{attribute: requirement} of one item; only attributes with an equation that evaluates (no entry: no requirement
    known - never switch the item off on a guess)."""
    prefix = equation_prefix(item_class)
    if prefix is None or not isinstance(item_level, (int, float)) or not math.isfinite(item_level) or not formulas:
        return {}
    variables = {"itemLevel": float(item_level), "totalAttCount": float(attribute_count(fields))}
    out = {}
    for attr, name in ATTRS.items():
        value = evaluate(formulas.get(f"{prefix}{name}Equation"), variables)
        if value is None:
            continue
        cut = (reductions or {}).get("characterGlobalReqReduction", 0.0) or 0.0
        cut += sum((reductions or {}).get(f"character{cat}{name}ReqReduction", 0.0) or 0.0
                   for cat in reduction_categories(item_class))
        value *= max(0.0, 1 - cut / 100)
        if not math.isfinite(value):                           # a broken reduction value: no requirement known
            continue
        need = round_half_up(value)
        if need > 0:
            out[attr] = need
    return out
