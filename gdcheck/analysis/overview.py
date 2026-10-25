"""Character overview row (N-017): class with masteries, stage, Build Readiness,
GAPs by effort (easy/medium/hard), recommended actions by priority."""
from __future__ import annotations

from .proposals.select import best_per_target

ALWAYS_HARD = ("dummy_ttk_mid", "dummy_ttk_end", "rr_main")
MASTERY_SHORT = {"Demolitionist": "Demo", "Occultist": "Occu", "Nightblade": "NB", "Arcanist": "Arca"}


def gap_effort(a) -> dict:
    """easy = stash or consumable closes it; medium = buy/craft/respec closes it or
    at most 10 below; hard = only other gear, rerolls or rare stats. .72 (K6): the Training Dummy time and the
    resistance reduction of the main type are always hard - no proposal changes them (skills, devotions, play style)."""
    out = {"easy": 0, "medium": 0, "hard": 0}
    fixers: dict = {}
    for p in a.proposals:
        for t in p.fixes:
            fixers.setdefault(t, []).append(p)
    for r in a.results:
        if r.status != "gap":
            continue
        if r.id in ALWAYS_HARD:
            out["hard"] += 1
            continue
        ps = fixers.get(r.id, [])
        if any(p.effort == "low" or p.kind == "consumable" for p in ps):
            out["easy"] += 1
        elif ps or (r.deficit is not None and r.deficit <= 10 and r.id not in ("level_100",)):
            out["medium"] += 1
        else:
            out["hard"] += 1
    return out


def actions_by_priority(a) -> dict:
    out = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for p in best_per_target(a.proposals):
        out[p.priority] = out.get(p.priority, 0) + 1
    return out


def _rift(ch, difficulty: int):
    """Rift gates unlocked on one difficulty (0 Normal, 1 Elite, 2 Ultimate); None if unknown."""
    rg = ch.progress.get("riftgates") or []
    return rg[difficulty] if len(rg) > difficulty else None


def dummy_seconds(a) -> float | None:
    """The Training Dummy shield time of the damage model in s (the overview column 'dummy s'), None without one."""
    r = next((x for x in a.results if x.id.startswith("dummy_ttk") and isinstance(x.now, (int, float))), None)
    return None if r is None else float(r.now)


def row(a, campaign: dict | None = None) -> dict:
    ch = a.ctx.character
    ms = [MASTERY_SHORT.get(n, n) for _, n, _ in ch.masteries]
    camp = (campaign or {}).get("Ultimate")
    return {"name": ch.name, "level": ch.level, "klasse": ch.class_name or (ms[0] if ms else "?"),
            "klassen_kurz": "/".join(ms), "stufe": a.stage.value, "punkte": a.score["score"],
            "ziele": a.score["targets"], "ok": a.score["ok"], "tight": a.score["tight"], "gap": a.score["gap"],
            "gap_fact": a.score["gap_fact"], "gap_aufwand": gap_effort(a), "aktionen": actions_by_priority(a),
            "kampagne_ultimate": camp, "ultimate": ch.progress.get("greatest_difficulty", 0) >= 2,
            "rift_ultimate": _rift(ch, 2), "profile": a.ctx.profile.value, "dummy_s": dummy_seconds(a)}
