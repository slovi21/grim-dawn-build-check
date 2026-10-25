"""Uptime of effects with duration and cooldown (pure)."""
from __future__ import annotations

from .policy import Confidence, rule


@rule("uptime", Confidence.EXACT, "duration / effective cooldown; upper bound: immediate recast")
def uptime(duration: float | None, cooldown: float | None, cdr_pct: float = 0.0) -> float | None:
    """min(1, duration / (cooldown x (1 - CDR))); None if the duration is unknown."""
    if not duration:
        return None
    if not cooldown:
        return 1.0
    cd = cooldown * (1 - min(max(cdr_pct, 0.0), 100.0) / 100)
    return min(1.0, duration / cd) if cd > 0 else 1.0


KEPT_UP = 0.95     # X-018: a maintained effect counts in full from duration / cooldown >= 95%


@rule("kept_up", Confidence.EXACT, "requirements X-018 (closure 03.10.2026): maintained - full value in the "
      "combat values - when duration / effective cooldown >= 95% (the recast gap is at most 5% of the time)")
def kept_up(u: float | None, threshold: float = KEPT_UP) -> bool:
    return u is not None and u >= threshold - 1e-9


@rule("strongest_over_time", Confidence.ASSUMED, "independent sources; staggering casts beats it")
def strongest_over_time(entries) -> float | None:
    """Mean effect of non-stacking sources [(value, uptime)]: at every moment
    the strongest ACTIVE source applies; weaker ones fill the gaps."""
    entries = list(entries)
    if any(u is None for _, u in entries):
        return None
    rest, total = 1.0, 0.0
    for v, u in sorted(entries, key=lambda x: -x[0]):
        total += v * u * rest
        rest *= (1 - u)
    return total


def rr_profile(value: float, u: float | None) -> dict:
    """Peak / steady / average of a resistance reduction with uptime u (X-008); steady when maintained (X-018)."""
    if u is None:
        return {"peak": value, "steady": None, "average": None}
    return {"peak": value, "steady": value if kept_up(u) else 0.0, "average": value * u}
