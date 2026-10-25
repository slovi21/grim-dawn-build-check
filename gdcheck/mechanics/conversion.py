"""Damage conversion (pure)."""
from __future__ import annotations

from .policy import Confidence, rule


ELEMENTAL = ("Fire", "Cold", "Lightning")
_WEAKER = {"EXACT": 0, "TESTED": 1, "ASSUMED": 2}


@rule("conversion", Confidence.EXACT, "Game Guide Combat order: base skill > skill modifiers > "
      "conversion on modifiers/transmuter > global conversion; never twice; >100% pro rata; within a stage "
      "'X to Y' of one type before 'Elemental to Y' on the rest (Crate forum 89135, TESTED)")
def convert(atoms: dict, skill_rules=(), global_rules=()) -> tuple[dict, str]:
    """atoms {type: damage}; rules [(from, to, percent)].

    Returns (converted atoms, confidence). Skill conversion first, global only on the unconverted rest.
    In each stage the rules of single types act first and 'Elemental -> X' then acts on what is left of
    Fire, Cold and Lightning (TESTED). 'X -> Elemental' gives a third to each element (ASSUMED, the
    game shows it as Elemental damage). Several global sources on the same type above 100% are split
    pro rata - that case is TESTED, not EXACT (X-009)."""
    rest = dict(atoms)
    done: dict = {}
    conf = "EXACT"

    def weaker(level):
        nonlocal conf
        if _WEAKER[level] > _WEAKER[conf]:
            conf = level

    def run(pool, rules, is_global, elemental):
        by_from: dict = {}
        for src, dst, pct in rules:
            for one in (ELEMENTAL if elemental else (src,)):
                by_from.setdefault(one, []).append((dst, pct))
        for src in sorted(by_from):
            amount = pool.get(src, 0.0)
            if amount <= 0:
                continue
            targets = by_from[src]
            total = sum(p for _, p in targets)
            if is_global and len(targets) > 1 and total > 100:   # .62: only the pro-rata split is TESTED
                weaker("TESTED")
            if elemental:
                weaker("TESTED")
            factor = min(1.0, 100.0 / total) if total > 100 else 1.0
            for dst, pct in targets:
                part = amount * pct / 100 * factor
                pool[src] -= part
                if dst == "Elemental":
                    weaker("ASSUMED")
                    for t in ELEMENTAL:
                        done[t] = done.get(t, 0.0) + part / 3
                else:
                    done[dst] = done.get(dst, 0.0) + part

    def apply(pool, rules, is_global):
        run(pool, [r for r in rules if r[0] != "Elemental"], is_global, False)
        run(pool, [r for r in rules if r[0] == "Elemental"], is_global, True)
    apply(rest, skill_rules, False)
    apply(rest, global_rules, True)
    for k, v in rest.items():
        if v > 1e-12:
            done[k] = done.get(k, 0.0) + v
    return done, conf


@rule("armor_piercing", Confidence.TESTED,
      "weapon '% Armor Piercing' (offensivePierceRatioMin) converts the physical damage of a weapon "
      "attack to Pierce, last after all other conversions, incl. flat physical from gear and skills "
      "(community: Steam threads 592937761422606103, 4854406759563886286; no developer quote)")
def armor_piercing(atoms: dict, ratio_pct: float) -> dict:
    """atoms after convert(); ratio_pct of the remaining Physical becomes Pierce (max 100)."""
    r = min(max(ratio_pct or 0.0, 0.0), 100.0) / 100
    phys = atoms.get("Physical", 0.0)
    if r <= 0 or phys <= 0:
        return dict(atoms)
    out = dict(atoms)
    out["Physical"] = phys * (1 - r)
    out["Pierce"] = out.get("Pierce", 0.0) + phys * r
    if out["Physical"] <= 1e-12:
        del out["Physical"]
    return out
