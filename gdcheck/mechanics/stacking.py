"""Evaluate a stacking policy over several values (pure)."""
from __future__ import annotations

from .policy import AUDIT_CLASS, Confidence, StackingPolicy, rule


@rule("stacking", Confidence.EXACT, "Game Guide: buffs/debuffs of the same kind - strongest; "
      "'-X' stat debuffs of different sources stack; item modifiers stack")
def resolve(policy: StackingPolicy, values) -> tuple[float, str]:
    """(effective value, audit class) for the values of several instances."""
    values = [float(v) for v in values]
    if not values:
        return 0.0, AUDIT_CLASS[policy]
    if policy in (StackingPolicy.ADDITIVE, StackingPolicy.INDEPENDENT_PROC):
        return sum(values), AUDIT_CLASS[policy]
    if policy in (StackingPolicy.MAX_VALUE, StackingPolicy.REFRESH_DURATION, StackingPolicy.REPLACE):
        return max(values, key=abs), AUDIT_CLASS[policy]
    if policy in (StackingPolicy.CAPPED, StackingPolicy.INACTIVE):
        return 0.0, AUDIT_CLASS[policy]
    return sum(values), AUDIT_CLASS[policy]
