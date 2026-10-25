"""Stage (EARLY / MID / ENDGAME) - the single decision table of Anforderungsheft V2, 9.1."""
from __future__ import annotations

from ..domain.enums import Stage
from ..mechanics import resist as RES

EARLY_BELOW = 85
MID_GATES = {"health_melee": 17000.0, "health_other": 14000.0, "da": 2600.0, "physical": 20.0}
ABSORPTION_NEGLIGIBLE = 0.005        # rest <= 0.5% of Health per physical hit


def ultimate_resists(ctx, snap) -> dict:
    """Resistances as they would be on Ultimate (kept-up buffs included), capped at 80."""
    out = {}
    for n in RES.RESISTS:
        r = snap.combat.resists[n]
        buff = r["value"] - snap.sheet.resists[n]["value"]
        val = snap.sheet.resists[n]["raw"] - ctx.ultimate_penalty.get(RES.FIELD[n], 0.0) + buff
        out[n] = {"value": val, "target": min(r["cap"], 80.0)}
    return out


def absorption_reached(snap) -> tuple[bool, float]:
    """(reached, extra damage per physical hit). Below 100% counts as reached when
    (1 - absorption) x hit-weighted armor <= 0.5% of Health."""
    a = snap.sheet.absorption
    extra = (1 - a / 100) * snap.sheet.armor_weighted
    health = snap.sheet.abilities["health"]
    return (a >= 99.95 or (health > 0 and extra <= ABSORPTION_NEGLIGIBLE * health)), extra


def gates(ctx, snap, melee: bool | None = None) -> list[str]:
    """MID gates that fail (strict, no tolerance)."""
    fail = []
    lvl = ctx.character.level
    if lvl < 100:
        fail.append(f"level {lvl} < 100")
    under = [n for n, r in ultimate_resists(ctx, snap).items() if round(r["value"]) < r["target"]]
    if under:
        fail.append("on Ultimate below 80%: " + ", ".join(under))
    h = snap.sheet.abilities["health"]
    melee = snap.melee if melee is None else melee
    hg = MID_GATES["health_melee"] if melee else MID_GATES["health_other"]
    if h < hg:
        fail.append(f"Health {h:,.0f} < {hg:,.0f}")
    da = snap.combat.abilities["da"]
    if da < MID_GATES["da"]:
        fail.append(f"DA {da:.0f} (combat) < {MID_GATES['da']:.0f}")
    ph = min(snap.combat.resists["Physical"]["value"], snap.combat.resists["Physical"]["cap"])
    if ph < MID_GATES["physical"]:
        fail.append(f"Physical {ph:.0f}% < {MID_GATES['physical']:.0f}%")
    ok, _ = absorption_reached(snap)
    if not ok:
        fail.append(f"Armor Absorption {snap.sheet.absorption:.1f}% < 100%")
    return fail


def stage(ctx, snap, melee: bool | None = None) -> tuple[Stage, list[str]]:
    lvl = ctx.character.level
    if lvl < EARLY_BELOW:
        return Stage.EARLY, [f"level {lvl} < {EARLY_BELOW}"]
    fails = gates(ctx, snap, melee)
    return (Stage.MID if fails else Stage.ENDGAME), fails
