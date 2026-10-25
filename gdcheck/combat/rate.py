"""Rate/uptime estimate (AP5b, X-021) - ESTIMATE, Expert View only, never a Player
View traffic light. Every number states its assumption."""
from __future__ import annotations

from ..mechanics import procs as PR
from ..mechanics import uptime as UP

BASE_ATTACKS_PER_S = 1.5        # fallback without animation tables: average weapon at 100% attack speed


def attacks_per_second(snap, actions=(), frames=None) -> float:
    """Actions per second of the main attack: animation frames of the strongest action without a
    cooldown (combat.frames), else the old estimate 1.5 x attack speed."""
    return main_attack_rate(snap, actions, frames)[0]


def main_attack_rate(snap, actions=(), frames=None) -> tuple:
    """(actions/s, label of the action or None, Timing or None)."""
    if frames is not None and getattr(frames, "available", False):
        fill = [a for a in actions if not a.cooldown and getattr(a, "kind", "") != "proc"]
        best = None
        for a in fill:
            t = frames.timing(a, snap.combat.speeds)
            if t is not None and t.actions_per_s > 0:
                key = (a.direct + a.dot) * t.actions_per_s
                if best is None or key > best[0]:
                    best = (key, a.label, t)
        if best is not None:
            return best[2].actions_per_s, best[1], best[2]
    sp = snap.combat.speeds["attack"]
    return BASE_ATTACKS_PER_S * (min(sp["value"], sp["cap"]) if sp["cap"] else sp["value"]) / 100, None, None


def proc_rates(snap, sources, events_per_s: float | None = None, basis: str | None = None) -> list[dict]:
    eps = events_per_s if events_per_s is not None else attacks_per_second(snap)
    why = basis or "average weapon x attack speed, ESTIMATE"
    out = []
    for p in sorted(sources.procs, key=lambda p: (p.label, p.id)):
        if p.bound_to is not None or "Attack" not in p.trigger:
            continue
        out.append({"label": p.label, "chance": p.chance, "cooldown": p.cooldown,
                    "per_second": PR.proc_rate(eps, p.chance, p.cooldown or 0.0),
                    "assumption": f"{eps:.2f} attacks/s ({why}), every attack eligible",
                    "confidence": "ESTIMATE"})
    return out


def rr_profiles(snap) -> list[dict]:
    """Peak / steady / average of each enemy debuff (X-008)."""
    out = []
    for d in snap.enemy_debuffs:
        u = d.get("uptime")
        for key, v in sorted(d["values"].items()):
            if key.startswith("defensive") and v < 0:
                prof = UP.rr_profile(-v, u)
                out.append({"debuff": d["label"], "field": key, **prof, "uptime": u,
                            "assumption": "recast as soon as ready" if u is not None else "uptime unknown"})
    return out
