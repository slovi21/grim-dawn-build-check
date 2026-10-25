"""Monster level and skill levels of monster records (pure, combat spec V2 / plan E3)."""
from __future__ import annotations

from .policy import Confidence, rule

_ALLOWED = set("0123456789 .+-*/()")


@rule("monster_level", Confidence.DERIVED,
      "monster level is not in the save: area level = player level (Ultimate scales to you; the area caps of "
      "the map files are not in the database - ASSUMED not binding), then the monster record's charLevel "
      "equation (bosses e.g. 'charLevel*1+3', game database), clamped to its minLevel/maxLevel; video "
      "03.10.2026: Loghorrean 103 at player level 100 (KG-08)")
def monster_level(player_level: int, min_level: int | None = None, max_level: int | None = None,
                  override: int | None = None, char_level=None) -> int:
    """`override` (--boss-level) is the monster level itself; else `char_level` (the record's charLevel
    equation) is applied to the player level - an equation that cannot be read falls back to the player
    level (level_basis names it). Integer result (truncation like skill_level, ASSUMED)."""
    if override:
        lv = int(override)
    elif char_level not in (None, ""):
        try:
            lv = int(equation(char_level, int(player_level)) + 1e-9)
        except (ValueError, SyntaxError, ZeroDivisionError, TypeError):
            lv = int(player_level)
    else:
        lv = int(player_level)
    if min_level:
        lv = max(lv, int(min_level))
    if max_level:
        lv = min(lv, int(max_level))
    return max(1, lv)


def level_basis(char_level=None, override: int | None = None) -> tuple[str, str]:
    """(confidence word, note) of monster_level for the reports - one wording for E2, E7 and the life
    leech note: DERIVED from the charLevel equation (or set with --boss-level); ASSUMED with a note when the
    record has no equation or one that cannot be read (player level used)."""
    if override:
        return "DERIVED", ""
    if char_level in (None, ""):
        return "ASSUMED", "no charLevel equation in the boss record - your level used"
    try:
        equation(char_level, 100)
    except (ValueError, SyntaxError, ZeroDivisionError, TypeError):
        return "ASSUMED", f"charLevel equation {char_level!r} not readable - your level used"
    return "DERIVED", ""


@rule("skill_level", Confidence.EXACT,
      "monster record skillLevelN equations (game database), integer result, at least 1")
def skill_level(equation, char_level: int) -> int:
    """Evaluate 'charLevel/4+1', '(charLevel/26)+3', '6' ... Integer division like the engine
    (truncation, ASSUMED for non-integer results); unsafe text raises ValueError."""
    if equation is None or equation == "":
        return 1
    if isinstance(equation, (int, float)):
        return max(1, int(equation))
    text = str(equation).strip().replace("charLevel", str(int(char_level)))
    if not text or not set(text) <= _ALLOWED:
        raise ValueError(f"unsupported skill level equation: {equation!r}")
    value = eval(text, {"__builtins__": {}}, {})  # noqa: S307 - digits and operators only
    return max(1, int(value))


def at_level(values, level: int) -> float:
    """Value of a per-level array (1-based, last entry for higher levels) or a scalar."""
    if isinstance(values, list):
        if not values:
            return 0.0
        return float(values[min(max(level, 1), len(values)) - 1])
    return float(values or 0.0)


_EQ_ALLOWED = _ALLOWED | {"^"}


@rule("monster_equation", Confidence.EXACT, "attribute equations of the monster bio record (game database)")
def equation(text, char_level: int) -> float:
    """Evaluate a bio equation like '((charLevel*195)^1.53)+50000' ('^' = power) at a level.
    Only digits, operators and charLevel are accepted; anything else raises ValueError."""
    if text is None or text == "":
        return 0.0
    if isinstance(text, (int, float)):
        return float(text)
    expr = str(text).strip().replace("charLevel", str(int(char_level)))
    if not set(expr) <= _EQ_ALLOWED:
        raise ValueError(f"unsupported monster equation: {text!r}")
    return float(eval(expr.replace("^", "**"), {"__builtins__": {}}, {}))  # noqa: S307 - checked charset


DA_EQUATION = ("(defensiveAbilityDV + (characterLevelDV * 12) + ((strengthDV + bonusDV) *0.5)) * "
               "(1 + (defensiveAbilityModifierDV / 100))+53")
OA_EQUATION = ("(offensiveAbilityDV + (characterLevelDV * 12) + ((dexterityDV + bonusDV) *0.5)) * "
               "(1 + (offensiveAbilityModifierDV / 100))+53")


@rule("monster_abilities", Confidence.EXACT,
      "combatformulas.dbr defensiveAbilityEquation / offensiveAbilityEquation (same as the player), "
      "monster values from its bio record + passives + difficulty table")
def abilities(level: int, da_flat: float, da_mod: float, strength: float,
              oa_flat: float, oa_mod: float, dexterity: float) -> dict:
    da = (da_flat + level * 12 + strength * 0.5) * (1 + da_mod / 100) + 53
    oa = (oa_flat + level * 12 + dexterity * 0.5) * (1 + oa_mod / 100) + 53
    return {"da": da, "oa": oa}


@rule("monster_health", Confidence.ASSUMED,
      "life equation of the monster bio record at the monster level x (1 + life modifiers / 100) x (1 + players' "
      "life multiplier / 100); the modifiers (difficulty table characterLifeModifier, passives' "
      "characterLifeModifier at their skill level) ADD before the factor. Checked against the community's Training "
      "Dummy health at player level 50/70/85 (8.68 / 10.71 / 12.24 M = bio x 6.78 = 1 + (580 - 2) / 100, "
      "Crate forum 44194 and related threads); above level 90 the passive's array rises and is not confirmed")
def monster_health(life_equation, level: int, life_modifier_pct: float, life_mult_pct: float = 0.0) -> float:
    """Health of a monster: equation(level) x (1 + modifiers/100) x (1 + multiplier/100). Unsafe equation
    text raises ValueError (monster.equation)."""
    return equation(life_equation, level) * (1 + life_modifier_pct / 100) * (1 + life_mult_pct / 100)
