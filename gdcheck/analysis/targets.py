"""Targets as data (AP6): load analysis/targets.json, select by stage/profile/
archetype/hardcore, evaluate against the snapshot. Every result carries basis,
source and game version (X-001, F-005)."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from ..domain.enums import Stage
from ..mechanics import hit as HIT
from ..mechanics import resist as RES
from . import stage as STG

FILE = Path(__file__).resolve().parent / "targets.json"
REQUIRED = ("id", "group", "area", "metric", "stages", "profile", "archetype", "type",
            "basis", "source", "label")


class TargetsError(Exception):
    pass


@dataclass(frozen=True)
class TargetResult:
    id: str
    label: str
    group: str
    area: str
    basis: str
    type: str
    now: float | None
    target: float | None
    status: str                # ok / tight / gap / info
    now_text: str
    target_text: str
    deficit: float | None
    source: str
    player_text: str
    weight: float
    field: str | None = None   # main database field to raise (for proposals)
    player_why: str = ""       # plain-language reason for the Player View (.62, N-008/N-011)
    lower_is_better: bool = False   # .70: a maximum (hc_spike) - the area value needs the direction
    kind: str = "floor"        # .72 (N1): 'floor' works up to its threshold, 'scaling' keeps paying off above it
    margin: bool = False       # .72: carries a reserve (scaling targets, resistances: the over-cap)
    credit: float | None = None     # .72: how far the target is met when the plain ratio is not the measure
    overcap: float | None = None    # .72: mean points over the cap of the nine resistances (reserve of the resistances)
    margin_at: float | None = None  # .72 (I2): the reserve is full at this multiple of the target; None = the score default (1.5)
    gate: bool = False         # .74 (I1): a gate target (resistances, Armor Absorption, resistance reduction) - open, it lowers the whole score
    gate_credit: float | None = None  # .74: what it counts in the gate when that differs from status/credit (res_own_cap: against the game rule 80, not the own cap)


def load(path: Path = FILE) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        raise TargetsError(f"targets file unreadable: {path} ({ex})") from ex
    validate(data)
    return data


KINDS = ("floor", "scaling")


def _validate_score(sc: dict) -> None:
    if not 0 <= sc.get("margin_share", 0.2) < 1:
        raise TargetsError("score: margin_share must be at least 0 and below 1")
    if sc.get("margin_at", 1.5) <= 1:
        raise TargetsError("score: margin_at must be above 1")
    for k in ("res_zero_points", "overcap_full"):
        if sc.get(k, 1) <= 0:
            raise TargetsError(f"score: {k} must be above 0")


def validate(data: dict) -> None:
    ids = set()
    _validate_score(data.get("score") or {})
    for t in data.get("targets", []):
        missing = [k for k in REQUIRED if k not in t]
        if missing:
            raise TargetsError(f"target {t.get('id', '?')}: missing {', '.join(missing)}")
        if t["id"] in ids:
            raise TargetsError(f"duplicate target id {t['id']}")
        if t["basis"] not in ("fact", "community"):
            raise TargetsError(f"target {t['id']}: basis must be fact or community")
        if t.get("kind", "floor") not in KINDS:
            raise TargetsError(f"target {t['id']}: kind must be floor or scaling")
        if t.get("weight", 1) <= 0:
            raise TargetsError(f"target {t['id']}: weight must be above 0")
        if not isinstance(t.get("gate", False), bool):
            raise TargetsError(f"target {t['id']}: gate must be true or false")
        at = t.get("margin_at", 2)
        if not isinstance(at, (int, float)) or at <= 1:
            raise TargetsError(f"target {t['id']}: margin_at must be above 1")
        if t["metric"] == "res_buffer":                  # m9: the credit of a buffer divides by param.rr
            rr = (t.get("param") or {}).get("rr")
            if not isinstance(rr, (int, float)) or rr <= 0:
                raise TargetsError(f"target {t['id']}: a res_buffer target needs param.rr above 0")
        ids.add(t["id"])


def selected(data, stage: Stage, profile: str, melee: bool, hardcore: bool) -> list[dict]:
    out = []
    for t in data["targets"]:
        if stage.value not in t["stages"]:
            continue
        if t["profile"] not in ("any", profile):
            continue
        arch = t.get("archetype", "any")
        if arch == "melee" and not melee or arch == "other" and melee:
            continue
        if t.get("hardcore") and not hardcore:
            continue
        out.append(t)
    return out


FIELD = {"health": "characterLifeModifier", "da_combat": "characterDefensiveAbility",
         "physical_combat": "defensivePhysical", "absorption": "defensiveAbsorptionModifier",
         "leech_res": "defensiveSlowLifeLeach", "freeze_res": "defensiveFreeze",
         "stun_res": "defensiveStun", "slow_res": "defensiveTotalSpeedResistance",
         "armor_weighted": "defensiveProtection", "pth_vs_boss_da": "characterOffensiveAbility"}


def _status(now, target, tol_kind, tol, lower_is_better=False):
    if now is None or target is None:
        return "info"
    if lower_is_better:
        if now <= target + 1e-9:
            return "ok"
        return "tight" if tol_kind == "time" and now <= target * (1 + tol["time"]) + 1e-9 else "gap"
    if now >= target - 1e-9:
        return "ok"
    gap = target - now
    if tol_kind in ("res", "physical", "rr") and gap <= tol[tol_kind] + 1e-9:
        return "tight"
    if tol_kind == "relative" and target and gap <= target * tol["relative"] + 1e-9:
        return "tight"
    return "gap"


def _res_pairs(ctx, snap, m) -> list[tuple[float, float]]:
    """(value, target) of the nine resistances: on Ultimate against 80 (or the own cap when lower), at the
    end against the own cap."""
    if m == "res_ultimate_count":
        return [(r["value"], r["target"]) for r in STG.ultimate_resists(ctx, snap).values()]
    rs = snap.sheet.resists
    return [(rs[n]["value"], rs[n]["cap"]) for n in RES.RESISTS]


def _res_deficits(ctx, snap, m) -> list[float]:
    """Points each missing resistance is short of its target (the ones that count as missing only)."""
    ultimate = m == "res_ultimate_count"
    return [t - v for v, t in _res_pairs(ctx, snap, m) if (round(v) < t if ultimate else v < t)]


def res_credit(deficits, res_zero: float) -> float:
    """How far the resistances are met, 0..1 (plan .72 A.1): the worst of the nine decides - 'res_zero'
    points under the target the resistance counts as missing (50% instead of 20% of the damage gets
    through at 30 points)."""
    return min((min(max(1 - d / res_zero, 0.0), 1.0) for d in deficits), default=1.0)


def overcap(ctx, snap, m, full: float = 15.0) -> float:
    """Mean points the nine resistances stand above their target, each type counted up to `full` points (the
    buffer against enemy resistance reduction, N1): one type far above its cap never makes up for another
    that has no buffer."""
    pairs = _res_pairs(ctx, snap, m)
    return sum(min(max(v - t, 0.0), full) for v, t in pairs) / len(pairs)


def gate_credit(ctx, snap, tol_res: float, zero: float) -> float:
    """What the resistances at the end count in the score gate (.74, review): against the game rule - 80 on every
    type (`resist_base_cap` of the ruleset, as in `stage.ultimate_resists`) - not against the own cap. 'Maximum <type>
    Resistance' raises the own cap, never the value, and must not open the gate (the own cap refines R and the reserve
    only). Every resistance up to `tol_res` points short of 80 is met (the same tolerance as the status), below that
    the worst of the nine decides: 1 - gap / res_zero_points."""
    base = float(ctx.ruleset.get("resist_base_cap", 80.0))
    rs = snap.sheet.resists
    defs = [min(rs[n]["cap"], base) - rs[n]["value"] for n in RES.RESISTS if rs[n]["value"] < min(rs[n]["cap"], base)]
    return 1.0 if not defs or max(defs) <= tol_res + 1e-9 else res_credit(defs, zero)


def _count_status(ctx, snap, m, tol_res) -> str:
    """Each of the nine resistances on its own: tight only if every missing one is
    within the tolerance (review 01.10.2026: counts are not resistance points)."""
    defs = _res_deficits(ctx, snap, m)
    if not defs:
        return "ok"
    return "tight" if max(defs) <= tol_res + 1e-9 else "gap"


def seconds_text(secs: float, target: float | None = None) -> str:
    """'~23 s' (from 119.5 s: '~2 min'). Above its target a time never rounds down to the target: 30.4 s of
    30 reads '~31 s', not '~30 s' (B7: a gap never reads as met)."""
    if secs >= 119.5:
        return f"~{round(secs / 60):,} min"
    v = int(round(secs))
    if target is not None and secs > target and v <= target:
        v = math.ceil(secs)
    return f"~{v} s"


# .72 (I3): the inputs that say "measured, and there is no damage": the target is then a gap with credit 0 (it was not met),
# not info; without the flag the value is not measurable (pet build, below Ultimate, a file without benchmarks) and the
# target stays info
NO_DAMAGE = {"rr_kinds": "rr_no_main", "dummy_ttk": "dummy_no_damage"}


def metric(ctx, snap, t, extra) -> tuple:
    """(now, target, now_text, target_text, field)"""
    m, thr = t["metric"], t.get("threshold")
    if t.get("scale_with_level") and thr:
        thr = round(thr * min(ctx.character.level / t["scale_with_level"], 1.0))
    if m == "level":
        return ctx.character.level, thr, str(ctx.character.level), str(thr), None
    if m == "res_ultimate_count":
        ur = STG.ultimate_resists(ctx, snap)
        n = sum(1 for r in ur.values() if round(r["value"]) >= r["target"])
        under = [f"{k} {r['value']:.0f}%" for k, r in ur.items() if round(r["value"]) < r["target"]]
        return n, 9, f"{n}/9" + (f" - below: {', '.join(under)}" if under else ""), "all 9", None
    if m == "res_at_cap_count":
        rs = snap.sheet.resists
        under = [f"{n} {rs[n]['value']:.0f}/{rs[n]['cap']:.0f}%" for n in RES.RESISTS if rs[n]["value"] < rs[n]["cap"]]
        return 9 - len(under), 9, f"{9 - len(under)}/9" + (f" - below: {', '.join(under)}" if under else ""), "all 9", None
    if m == "health":
        h = snap.sheet.abilities["health"]
        return h, thr, f"{h:,.0f}", f"{thr:,.0f}", FIELD[m]
    if m == "da_combat":
        d = snap.combat.abilities["da"]
        return d, thr, f"{d:,.0f}", f"{thr:,.0f}", FIELD[m]
    if m == "physical_combat":
        r = snap.combat.resists["Physical"]
        v = min(r["value"], r["cap"])
        return v, thr, f"{v:.0f}%", f"{thr:.0f}%", FIELD[m]
    if m == "absorption":
        ok, extra_dmg = STG.absorption_reached(snap)
        a = snap.sheet.absorption
        return (100.0 if ok else a), 100.0, f"{a:.1f}%" + (" (enough)" if ok and a < 99.95 else ""), "100%", FIELD[m]
    if m == "armor_weighted":
        v = snap.sheet.armor_weighted
        return v, thr, f"{v:,.0f}", f"{thr:,.0f}", FIELD[m]
    if m == "leech_res":
        v = snap.sheet.leech["value"]
        return v, thr, f"{v:.0f}%", f"{thr:.0f}%", FIELD[m]
    if m in ("freeze_res", "stun_res", "slow_res"):
        v = snap.combat.fields.get(FIELD[m], 0.0)
        return v, thr, f"{v:.0f}%", f"{thr:.0f}%", FIELD[m]
    if m == "pth_vs_boss_da":
        if "da_shred" not in extra:
            return None, thr, "not calculated", f"{thr:.0f}", FIELD[m]
        shred = extra["da_shred"]
        p = HIT.pth(snap.combat.abilities["oa"], max(0.0, 3000.0 - shred))
        return p, thr, f"{p:.0f} (OA {snap.combat.abilities['oa']:,.0f}, shred {shred:.0f})", f"{thr:.0f}", FIELD[m]
    if m == "res_buffer":
        typ, rr = t["param"]["type"], t["param"]["rr"]
        r = snap.combat.resists[typ]
        target = r["cap"] + rr
        return r["value"], target, f"{r['value']:.0f}", f"{target:.0f} (cap {r['cap']:.0f} + {rr})", RES.FIELD[typ]
    if m == "rr_kinds":
        x = extra.get("rr_kinds")                       # (type, weighted score 0..1, permanent kinds, kinds not permanent)
        if not x:
            if extra.get(NO_DAMAGE[m]):
                return None, thr, "no main damage type", "all 3 kinds", None
            return None, thr, "not calculated", "all 3 kinds", None
        typ, score, perm, weak = x
        # .73: short form - the top issue carries 9 words of frame (label, "(recommended all 3 kinds)", ", not by gear"),
        # the long form made the line 18 words ("Chaos: 2 of 3 kinds + 1 not permanent"). "not permanent" is the umbrella
        # term of the area row, D11 and the mechanics page ("temporary" is only one of its four reasons - review .73)
        return score, thr, f"{typ} {perm}/3 kinds" + (f", {weak} not permanent" if weak else ""), "all 3 kinds", None
    if m == "dummy_ttk":
        secs = extra.get("dummy_ttk")
        if secs is None:
            if extra.get(NO_DAMAGE[m]):
                return None, thr, "no damage", f"{thr:.0f} s", None
            return None, thr, "not measured", f"{thr:.0f} s", None
        return secs, thr, seconds_text(secs, thr), f"{thr:.0f} s", None
    if m == "greatest_hit_share":
        hit = float(ctx.character.stats.get("greatest_damage_received") or 0.0)
        h = snap.sheet.abilities["health"]
        share = 100 * hit / h if h else 0.0
        return share, thr, f"{share:.0f}% of Health ({hit:,.0f})", f"under {thr:.0f}%", None
    raise ValueError(f"unknown metric {m}")


def evaluate(ctx, snap, stage: Stage, data: dict, extra: dict | None = None,
             melee: bool | None = None) -> list[TargetResult]:
    extra = extra or {}
    melee = snap.melee if melee is None else melee
    tol = {"res": 3.0, "physical": 4.0, "relative": 0.05, "rr": 0.2, "time": 0.25, **data.get("tolerance", {})}
    zero = float((data.get("score") or {}).get("res_zero_points", 30))
    full = float((data.get("score") or {}).get("overcap_full", 15))
    out = []
    for t in selected(data, stage, ctx.profile.value, melee, ctx.hardcore):
        now, target, nt, tt, field = metric(ctx, snap, t, extra)
        credit = over = gcredit = None
        if t["metric"] in ("res_ultimate_count", "res_at_cap_count"):
            st = _count_status(ctx, snap, t["metric"], tol["res"])
            credit, over = res_credit(_res_deficits(ctx, snap, t["metric"]), zero), overcap(ctx, snap, t["metric"], full)
            if t["metric"] == "res_at_cap_count":
                gcredit = gate_credit(ctx, snap, tol["res"], zero)
        elif now is None and extra.get(NO_DAMAGE.get(t["metric"], "")):       # I3: no damage did not meet the target
            st, credit = "gap", 0.0
        else:
            st = _status(now, target, t.get("tolerance"), tol, t.get("lower_is_better", False))
            if t["metric"] == "res_buffer" and now is not None:      # K9: the share of the buffer that is missing
                credit = min(max(1 - max(target - now, 0.0) / t["param"]["rr"], 0.0), 1.0)
        deficit = None if now is None or target is None else (
            now - target if t.get("lower_is_better") else target - now)
        out.append(TargetResult(t["id"], t["label"], t["group"], t["area"], t["basis"], t["type"],
                                now, target, st, nt, tt, deficit, t["source"],
                                t.get("player_text", t["label"]), float(t.get("weight", 1.0)), field,
                                t.get("player_why", ""), bool(t.get("lower_is_better", False)),
                                t.get("kind", "floor"), bool(t.get("margin", False)), credit, over,
                                None if t.get("margin_at") is None else float(t["margin_at"]), bool(t.get("gate", False)), gcredit))
    return out
