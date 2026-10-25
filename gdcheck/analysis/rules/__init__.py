"""Rule catalogue D1 ("Build errors and notes"): points, affixes and effects that do nothing or
less than they seem. Port of the .35 pruefungen module (Task 6 of release .68: moved, renamed and
de-globalised; it reads analysis.save_facts).

  module         rules              category
  no_effect      A1, A1b, A3-A5     A  no effect (weapon condition, conversion, rank cap)
  stacking       B2                 B  stacking (missing resistance-reduction type)
  self_penalty   C1                 C  self-penalty
  bindings       D1-D4              D  bindings of celestial powers
  spread         E1-E6              E  point spread

Not ported - the stacking audit (gdcheck/stats/audit.py) replaces them: A2 (+skills without
effect), A6 (speed above the cap), A7 (shield values without a shield), B1/B1d (non-stacking
'Reduced' effects).

Every check is `check_<id>(ctx: RuleContext) -> list[Finding]`; B2 and C1 also take the effect
sources so they are computed once per run.
"""
from __future__ import annotations

from ..effect_uptime import effect_sources
from ..save_facts import RuleContext, SaveFacts
from .bindings import check_d1, check_d2, check_d3, check_d4
from .findings import CATEGORY_NAMES, ERROR, NOTE, SEVERITY_RANK, WARNING, Finding
from .no_effect import check_a1, check_a3, check_a4, check_a5
from .self_penalty import check_c1
from .spread import check_e1, check_e2, check_e3, check_e4, check_e5, check_e6
from .stacking import check_b2

__all__ = ["CATEGORY_NAMES", "ERROR", "NOTE", "RULES", "SEVERITY_RANK", "WARNING", "Finding",
           "run_rules"]

# (id, check, takes the effect sources) in catalogue order
RULES = (
    ("A1", check_a1, False), ("A3", check_a3, False), ("A4", check_a4, False), ("A5", check_a5, False),
    ("B2", check_b2, True), ("C1", check_c1, True),
    ("D1", check_d1, False), ("D2", check_d2, False), ("D3", check_d3, False), ("D4", check_d4, False),
    ("E1", check_e1, False), ("E2", check_e2, False), ("E3", check_e3, False), ("E4", check_e4, False),
    ("E5", check_e5, False), ("E6", check_e6, False),
)


def run_rules(facts: SaveFacts, ctx: RuleContext) -> list[Finding]:
    """Every finding of the catalogue, sorted by severity, category and code (stable within)."""
    if ctx.facts is not facts:
        raise ValueError("run_rules: the RuleContext was built from other SaveFacts")
    sources = effect_sources(ctx)
    found: list[Finding] = []
    for _id, check, needs_sources in RULES:
        found += check(ctx, sources) if needs_sources else check(ctx)
    found.sort(key=lambda f: (SEVERITY_RANK[f.severity], f.category, f.code))
    return found
