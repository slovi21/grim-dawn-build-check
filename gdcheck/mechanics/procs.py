"""Procs (pure)."""
from __future__ import annotations

import math

from .policy import Confidence, rule


@rule("p_any", Confidence.EXACT, "independent instances (Game Guide Item Skills)")
def p_any(chances) -> float:
    """Probability that at least one of several independent procs fires on one eligible event."""
    q = 1.0
    for c in chances:
        q *= (1 - min(max(c, 0.0), 1.0))
    return 1 - q


@rule("proc_rate", Confidence.DERIVED,
      "combat spec V2 9: periodic opportunities, n_cd = ceil(cd / dt), "
      "E[interval] = n_cd*dt + (1-p)/p*dt; per source instance")
def proc_rate(events_per_s: float, chance: float, icd: float = 0.0) -> float:
    """Procs per second of ONE proc instance with chance p (0..1) and internal cooldown."""
    if events_per_s <= 0 or chance <= 0:
        return 0.0
    p = min(chance, 1.0)
    dt = 1.0 / events_per_s
    n_cd = max(1, math.ceil(icd / dt - 1e-9)) if icd > 0 else 1
    return 1.0 / (n_cd * dt + ((1 - p) / p) * dt)


@rule("proc_rate_raw", Confidence.DERIVED, "combat spec V2 9: lambda_raw = events/s x p (no cooldown)")
def proc_rate_raw(events_per_s: float, chance: float) -> float:
    return max(0.0, events_per_s) * min(max(chance, 0.0), 1.0)


@rule("dps", Confidence.DERIVED, "combat spec V2 3.3: DpS = sum(E[damage]) / sum(E[time]), never "
      "the mean of single-action DpS")
def dps(actions) -> float:
    """actions: iterable of (expected damage, expected time in s) per action in the rotation."""
    dmg = tim = 0.0
    for d, t in actions:
        dmg += max(d, 0.0)
        tim += max(t, 0.0)
    return dmg / tim if tim > 0 else 0.0
