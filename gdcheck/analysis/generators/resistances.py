"""Resistance rows, needs and the swap check of the candidate generators (port of the
resistance part of the .35 `assess_core` module - Task 7 of release .67/.68).

A swap (reroll, devotion plan) is checked against the totals: one that pushes any resistance
below its need is rejected. That check found two errors during the .35 build that were
invisible slot by slot.

De-globalised: the difficulty penalty per resistance (.35 module dict `assess_core.ABZUG`,
cleared and refilled by the adapter before every run) is the `penalty` of a `Resistances`
object.

Old -> new: KAPPE -> BASE_CAP, BEDARF -> NEED, PHYSICAL_ZIEL -> PHYSICAL_TARGET,
OVERCAP_PUFFER -> OVERCAP_BUFFER, ABSORPTION_BASIS/ZIEL/BONUS_ZIEL -> ABSORPTION_BASE/TARGET/
BONUS_TARGET, RESIST_NICHT_PHYSICAL -> NON_PHYSICAL, wirksam -> Resistances.effective,
kappe -> cap, bedarf -> need, resistenzen -> Resistances.rows, absorption_anzeige ->
absorption_display, tausch_pruefen -> Resistances.swap_check. Row keys: feld -> field,
wert -> value, bedarf -> need, kappe -> cap, roh -> raw, abzug -> penalty, angezeigt -> shown,
kampf -> combat, luecke -> gap, verschwendet -> wasted; swap rows: vorher/nachher -> before/after.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType

BASE_CAP = 80

# need = cap + resistance reduction of the enemies. Numbers from GrimTools 1.2.1.0, not
# re-checked for 1.3.
NEED = MappingProxyType({
    "defensiveElementalResistance": (115, "Elemental"),
    "defensiveFire": (115, "Fire"),
    "defensiveCold": (115, "Cold"),
    "defensiveLightning": (115, "Lightning"),
    "defensivePoison": (110, "Acid"),
    "defensiveLife": (110, "Vitality"),      # against Ravager of Souls 119
    "defensiveAether": (108, "Aether"),      # against Ravager of Souls 119
    "defensiveChaos": (105, "Chaos"),
    "defensivePierce": (100, "Pierce"),
    "defensiveBleeding": (100, "Bleeding"),  # against Ravager of Flesh 105
})
PHYSICAL_TARGET = 30          # guide value, forum, before 1.3
OVERCAP_BUFFER = 20           # above it counts as re-assignable

# armor [wiki Game Mechanics, grimdawn.com combat guide, Crate forum]
ABSORPTION_BASE = 70.0        # % without bonuses
ABSORPTION_TARGET = 100.0     # display; capped at 100 per Crate (2015)
# bonuses act multiplicatively: 70 * (1 + x/100). 100 needs +42.86 %.
ABSORPTION_BONUS_TARGET = (ABSORPTION_TARGET / ABSORPTION_BASE - 1) * 100

NON_PHYSICAL = ("defensiveFire", "defensiveCold", "defensiveLightning",
                "defensivePoison", "defensivePierce", "defensiveBleeding",
                "defensiveLife", "defensiveAether", "defensiveChaos")
_ELEMENTS = ("defensiveFire", "defensiveCold", "defensiveLightning")


def cap(sheet, fld) -> float:
    """Max resistance: 80 plus "+X % to Maximum <type> Resistance" and "+X Maximum All
    Resistances". Physical has 80 as well."""
    if fld == "defensiveElementalResistance":
        # Elemental has no own cap - it acts on Fire/Cold/Lightning.
        fld = "defensiveFire"
    k = BASE_CAP + sheet.get(fld + "MaxResist", 0)
    if fld in NON_PHYSICAL:
        k += sheet.get("defensiveAllMaxResist", 0)
    return k


def need(sheet, fld) -> float:
    """need = own cap + enemy resistance reduction. Who raises the cap needs correspondingly
    more to keep it under debuffs."""
    return NEED[fld][0] + (cap(sheet, fld) - BASE_CAP)


def absorption_display(bonus) -> float:
    return min(ABSORPTION_TARGET, ABSORPTION_BASE * (1 + bonus / 100))


@dataclass(frozen=True)
class Resistances:
    """Resistances after the difficulty penalty (Elite/Ultimate) - need and cap refer to the
    value AFTER the penalty, as the character window shows it. Physical is not penalised."""
    penalty: dict = field(default_factory=dict)      # resist field -> penalty (positive)

    def effective(self, sheet, fld) -> float:
        """Actual resistance of a type. defensiveElementalResistance counts in full on Fire,
        Cold and Lightning; defensiveAllResistance ("% Total Resist", new in 1.3.0.0) counts on
        all nine types except Physical. Reading the fields one by one reports gaps that do not
        exist."""
        value = sheet.get(fld, 0)
        if fld in _ELEMENTS:
            value += sheet.get("defensiveElementalResistance", 0)
        if fld in NON_PHYSICAL:
            value += max(sheet.get("defensiveAllResistance", 0), 0)
        return value - self.penalty.get(fld, 0)

    def rows(self, sheet, buff=None) -> list:
        """Resistance rows. 'value' = character sheet (cap/gap), 'combat' = sheet + kept-up
        buffs (buffer up to the need). buff = {field: value}."""
        buff = buff or {}
        out = []
        for fld, (_, name) in NEED.items():
            if fld == "defensiveElementalResistance":
                continue          # goes into Fire/Cold/Lightning
            value = self.effective(sheet, fld)
            extra = buff.get(fld, 0)
            if fld in _ELEMENTS:
                extra += buff.get("defensiveElementalResistance", 0)
            combat = value + extra
            k, b = cap(sheet, fld), need(sheet, fld)
            out.append({
                "field": fld, "name": name, "value": value, "need": b, "cap": k,
                "raw": value + self.penalty.get(fld, 0), "penalty": self.penalty.get(fld, 0),
                "shown": min(value, k), "combat": combat, "buff": extra,
                "gap": max(0, b - combat),
                "wasted": max(0, combat - b - OVERCAP_BUFFER),
            })
        phys = sheet.get("defensivePhysical", 0)
        kp = cap(sheet, "defensivePhysical")
        pk = phys + buff.get("defensivePhysical", 0)
        out.append({"field": "defensivePhysical", "name": "Physical", "value": phys,
                    "raw": phys, "penalty": 0, "shown": min(phys, kp), "cap": kp,
                    "combat": min(pk, kp), "buff": buff.get("defensivePhysical", 0),
                    "need": PHYSICAL_TARGET, "gap": max(0, PHYSICAL_TARGET - pk),
                    "wasted": 0})
        out.sort(key=lambda z: (-z["gap"], -z["wasted"]))
        return out

    def swap_check(self, sheet, delta) -> tuple[list, list]:
        """Check a proposal against the totals: (rows, problems). problems lists the
        resistances that would fall below their need - if not empty, the swap is out."""
        rows, problems = [], []
        # a change of Elemental Resistance acts on all three types
        d_elem = delta.get("defensiveElementalResistance", 0)
        if d_elem:
            for part in _ELEMENTS:
                old = self.effective(sheet, part)
                new = old + d_elem + delta.get(part, 0)
                if new < need(sheet, part) and new < old:
                    problems.append(NEED[part][1])
        for fld in sorted(delta, key=lambda f: (-abs(delta[f]), f)):
            d = delta[fld]
            if abs(d) < 0.5:
                continue
            entry = {"field": fld, "delta": d}
            if fld in NEED:
                b, name = need(sheet, fld), NEED[fld][1]
                before = self.effective(sheet, fld)
                after = before + d
                entry.update({"before": before, "after": after, "need": b, "name": name})
                # a problem is only what pushes a resistance below its need or widens an
                # existing gap - not a gap that was there before and stays unchanged
                if after < b and d < 0:
                    problems.append(name)
            elif fld == "defensiveAbsorptionModifier" and d < 0:
                old_a = absorption_display(sheet.get(fld, 0))
                new_a = absorption_display(sheet.get(fld, 0) + d)
                entry.update({"before": old_a, "after": new_a, "name": "Absorption"})
                if new_a < ABSORPTION_TARGET and new_a < old_a:
                    problems.append(f"Armor Absorption {old_a:.0f} -> {new_a:.0f}%")
            elif fld == "defensivePhysical" and d < 0 and \
                    sheet.get(fld, 0) + d < PHYSICAL_TARGET:
                problems.append("Physical")
            rows.append(entry)
        return rows, problems
