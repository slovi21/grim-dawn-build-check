"""Speed caps (pure)."""
from __future__ import annotations

from .policy import Confidence, rule


@rule("speed_cap", Confidence.EXACT, "gameengine.dbr max speeds + '+% Max Attack/Cast/Movement Speed'")
def speed(bonus_pct: float, total_pct: float, cap: float, cap_bonus: float = 0.0,
          weapon_factor: float = 1.0) -> dict:
    """raw = 100 + bonus + total speed; value = min(raw, cap + max bonus) (summed bonuses, capped - no
    weapon). Attack speed adds `sheet`, `weapon_factor` and `excess` from sheet_attack_speed (ASSUMED,
    .62: split from this EXACT rule, code review of .61)."""
    raw = 100.0 + bonus_pct + total_pct
    limit = cap + cap_bonus
    out = {"raw": raw, "cap": limit, "value": min(raw, limit)}
    out.update(sheet_attack_speed(raw, limit, weapon_factor))
    return out


@rule("sheet_attack_speed", Confidence.ASSUMED,
      "Crate forum 35306/35411 (community, 2016): the weapon base speed multiplies the summed % attack speed, "
      "the cap limits the result; two in-game sheets of Cpt Wolf disagree by 5-7 points (KG-11)")
def sheet_attack_speed(raw: float, limit: float, weapon_factor: float = 1.0) -> dict:
    """`sheet` = min(raw x weapon factor, cap) - the character sheet value and the speed the frame model
    uses; `excess` (wasted) is measured on raw x min(weapon factor, 1), so a slow weapon never reports a
    waste the sheet does not show (Cpt Wolf 02.10.2026: 239% x 0.92 = 220% < 230%, sheet 227% - KG-11)."""
    wf = max(float(weapon_factor), 0.05)
    return {"weapon_factor": wf, "sheet": min(raw * wf, limit), "excess": max(0.0, raw * min(wf, 1.0) - limit)}
