"""Stage (EARLY / MID / ENDGAME) - the single decision table of Anforderungsheft V2, 9.1."""
from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True)
class Gate:
    """One MID gate as data. `met` is authoritative (strict, no tolerance), `text` is the failing line ('' when met).
    Armor Absorption counts as met below 100% when the rest is within 0.5% of Health per hit (`absorption_reached`),
    so compare `met`, never `now >= target`.
    `now` / `target` / `gap` are in the gate's own `unit`: "" a plain number (level, Health, Defensive Ability),
    "types" a count of resistance types (resistances: types at their Ultimate target of 9), "%" a percent (Physical
    Resistance, Armor Absorption). `gap` is the distance still to go, 0 when met; for the resistance gate it is the
    worst type's shortfall in points (target - round(value) of `worst`).
    Only the resistance gate names types: `worst` is the type with the smallest round(value) - target (the first in
    the fixed type order on a tie), `below` every type with round(value) < target, in the fixed type order."""
    id: str
    label: str
    now: float
    target: float
    unit: str
    met: bool
    text: str
    worst: str | None = None
    below: tuple[str, ...] = ()
    gap: float = 0.0


GATE_IDS = ("level", "resistances", "health", "da", "physical", "absorption")


def gate_list(ctx, snap, melee: bool | None = None) -> tuple[Gate, ...]:
    """The six MID gates in fixed order, met or not (strict, no tolerance)."""
    lvl = ctx.character.level
    ur = ultimate_resists(ctx, snap)
    margin = {n: round(r["value"]) - r["target"] for n, r in ur.items()}
    under = tuple(n for n, r in ur.items() if round(r["value"]) < r["target"])
    worst = min(margin, key=margin.get)                # first of the type order on a tie
    h = snap.sheet.abilities["health"]
    melee = snap.melee if melee is None else melee
    hg = MID_GATES["health_melee"] if melee else MID_GATES["health_other"]
    da = snap.combat.abilities["da"]
    ph = min(snap.combat.resists["Physical"]["value"], snap.combat.resists["Physical"]["cap"])
    ok, _ = absorption_reached(snap)
    a = snap.sheet.absorption

    def gate(gid, label, now, target, unit, met, text, gap=None, **kw):
        gap = target - now if gap is None else gap
        return Gate(gid, label, now, target, unit, met, "" if met else text, gap=0.0 if met else float(gap), **kw)

    return (
        gate("level", "Level", lvl, 100, "", lvl >= 100, f"level {lvl} < 100"),
        gate("resistances", "Resistances on Ultimate", len(ur) - len(under), len(ur), "types", not under,
             "on Ultimate below 80%: " + ", ".join(under), worst=worst, below=under, gap=-margin[worst]),
        gate("health", "Health", h, hg, "", h >= hg, f"Health {h:,.0f} < {hg:,.0f}"),
        gate("da", "Defensive Ability", da, MID_GATES["da"], "", da >= MID_GATES["da"],
             f"DA {da:.0f} (combat) < {MID_GATES['da']:.0f}"),
        gate("physical", "Physical Resistance", ph, MID_GATES["physical"], "%", ph >= MID_GATES["physical"],
             f"Physical {ph:.0f}% < {MID_GATES['physical']:.0f}%"),
        gate("absorption", "Armor Absorption", a, 100.0, "%", ok, f"Armor Absorption {a:.1f}% < 100%"),
    )


def gates(ctx, snap, melee: bool | None = None) -> list[str]:
    """MID gates that fail (strict, no tolerance)."""
    return [g.text for g in gate_list(ctx, snap, melee) if not g.met]


def stage(ctx, snap, melee: bool | None = None) -> tuple[Stage, list[str]]:
    lvl = ctx.character.level
    if lvl < EARLY_BELOW:
        return Stage.EARLY, [f"level {lvl} < {EARLY_BELOW}"]
    fails = gates(ctx, snap, melee)
    return (Stage.MID if fails else Stage.ENDGAME), fails
