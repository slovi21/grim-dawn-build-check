"""Stacking policies, confidence levels and the rule registry (pure, stdlib only).

Every mechanics function registers itself with @rule(name, confidence, source)
so the Expert View can show where a number comes from (X-002).
"""
from __future__ import annotations

from enum import Enum


class StackingPolicy(str, Enum):
    ADDITIVE = "ADDITIVE"                    # values add up
    INDEPENDENT_PROC = "INDEPENDENT_PROC"    # separate rolls / cooldowns
    MAX_VALUE = "MAX_VALUE"                  # only the strongest active value
    REFRESH_DURATION = "REFRESH_DURATION"    # same effect refreshes
    REPLACE = "REPLACE"                      # new application replaces the old
    CAPPED = "CAPPED"                        # present, but at the cap (wasted)
    CONDITIONAL = "CONDITIONAL"              # only under a condition
    INACTIVE = "INACTIVE"                    # source not active


# Player-facing class names of the stacking audit (X-004)
AUDIT_CLASS = {
    StackingPolicy.ADDITIVE: "STACKS",
    StackingPolicy.INDEPENDENT_PROC: "INDEPENDENT",
    StackingPolicy.MAX_VALUE: "STRONGEST ONLY",
    StackingPolicy.REFRESH_DURATION: "REFRESH",
    StackingPolicy.REPLACE: "REPLACE",
    StackingPolicy.CAPPED: "CURRENTLY WASTED",
    StackingPolicy.CONDITIONAL: "CONDITIONAL",
    StackingPolicy.INACTIVE: "DEAD",
}


class Confidence(str, Enum):
    EXACT = "EXACT"          # game database or official guide, taken over directly
    TESTED = "TESTED"        # community tests / golden tests against the game
    DERIVED = "DERIVED"      # derived mathematically from EXACT/TESTED rules (combat spec V2)
    ASSUMED = "ASSUMED"      # plausible model, not verified - always visible
    ESTIMATE = "ESTIMATE"    # needs an explicit rate/time assumption (AP5b)
    HEURISTIC = "HEURISTIC"  # rough approximation, preview only


RULES: dict[str, tuple[Confidence, str]] = {}


def rule(name: str, confidence: Confidence, source: str):
    """Register a mechanics function with its confidence and source."""
    def deco(fn):
        RULES[name] = (confidence, source)
        fn.rule_name = name
        fn.confidence = confidence
        fn.source = source
        return fn
    return deco


def weakest(*levels: Confidence) -> Confidence:
    """The weakest confidence of a chain of rules."""
    order = [Confidence.EXACT, Confidence.TESTED, Confidence.DERIVED, Confidence.ASSUMED,
             Confidence.ESTIMATE, Confidence.HEURISTIC]
    return max(levels, key=order.index) if levels else Confidence.EXACT
