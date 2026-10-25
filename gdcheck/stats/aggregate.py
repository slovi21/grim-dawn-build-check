"""Combine effect instances into field totals under their stacking policies."""
from __future__ import annotations

from ..domain.enums import StackingPolicy


def aggregate(effects, weight=lambda e: 1.0) -> tuple[dict, dict]:
    """(totals {key: value}, provenance {key: [(source_id, value), ...]}).

    ADDITIVE adds every instance; MAX_VALUE/REFRESH/REPLACE keep, per group and
    key, the strongest instance (strongest absolute value). weight(e) scales an
    effect (uptime of kept-up buffs); 0 drops it."""
    totals: dict = {}
    prov: dict = {}
    groups: dict = {}
    for e in effects:
        w = weight(e)
        if not w:
            continue
        v = e.value * w
        if e.policy in (StackingPolicy.MAX_VALUE, StackingPolicy.REFRESH_DURATION, StackingPolicy.REPLACE):
            groups.setdefault((e.key, e.group), []).append((e.source_id, v))
            continue
        if e.policy in (StackingPolicy.CAPPED, StackingPolicy.INACTIVE):
            continue
        totals[e.key] = totals.get(e.key, 0.0) + v
        prov.setdefault(e.key, []).append((e.source_id, v))
    for (key, _g), vals in sorted(groups.items()):
        sid, v = max(vals, key=lambda x: (abs(x[1]), x[0]))
        totals[key] = totals.get(key, 0.0) + v
        prov.setdefault(key, []).append((sid, v))
    return totals, prov
