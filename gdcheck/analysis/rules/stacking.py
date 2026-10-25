"""Category B - stacking: port of the .35 pruefungen rule B2 (missing resistance-reduction types).

The three types of resistance reduction stack with each other [Wiki, Crate forum]:
  type 1  -X% on the enemy (debuff records; stacks)
  type 2  X% Reduced target's Resistances (only the strongest applies)
  type 3  X Reduced target's Resistances, flat (only the strongest applies)

B1/B1d (several sources of the same non-stacking 'Reduced' effect) are not ported: the stacking
audit (gdcheck/stats/audit.py, class STRONGEST ONLY) covers them. The audit has no row for a
MISSING type, so B2 appears exactly once in the report (tests/unit/test_rules_stacking.py).
"""
from __future__ import annotations

from ..effect_uptime import effect_sources
from ..save_facts import RuleContext
from .findings import NOTE, Finding

_TYPE2 = ("offensiveTotalResistanceReductionPercentMin", "offensiveElementalResistanceReductionPercentMin")
_TYPE3 = ("offensiveTotalResistanceReductionAbsoluteMin", "offensiveElementalResistanceReductionAbsoluteMin")


def resistance_reduction_types(sources: list) -> dict:
    """Effect sources (analysis.effect_uptime.effect_sources) per reduction type 1/2/3."""
    type1 = [q["quelle"] for q in sources
             if any("Debuf" in k or "Contageous" in k for k in q["klassen"])
             and any(f.startswith("defensive") and f != "defensiveAbsorption"
                     and isinstance(v, float) and v < 0 for f, v in q["felder"].items())]
    type2 = [q["quelle"] for q in sources if any(q["felder"].get(f) for f in _TYPE2)]
    type3 = [q["quelle"] for q in sources if any(q["felder"].get(f) for f in _TYPE3)]
    return {1: type1, 2: type2, 3: type3}


def check_b2(ctx: RuleContext, sources: list | None = None) -> list[Finding]:
    """B2: a type of resistance reduction is missing entirely (NOTE)."""
    found = resistance_reduction_types(sources if sources is not None else effect_sources(ctx))
    missing = [n for n, k in (("type 1 (-X%, stacks)", 1), ("type 2 (X% Reduced)", 2),
                              ("type 3 (X Reduced, flat)", 3)) if not found[k]]
    if not missing:
        return []
    return [Finding(
        "B2", "B", NOTE, "Resistance reduction: " + ", ".join(missing) + " missing",
        f"Type 1: {len(found[1])} sources, type 2: {len(found[2])}, type 3: {len(found[3])}. "
        "The three types stack with each other - a missing type is wasted potential.",
        "Wiki, Forum", "Look for a source of the missing type (devotion, component, item).")]
