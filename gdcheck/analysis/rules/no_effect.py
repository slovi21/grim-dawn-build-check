"""Category A - no effect: a condition of the bonus is not met (weapon type, conversion,
rank cap). Port of the .35 pruefungen rules A1/A1b, A3, A4 and A5.

A2 (+skills on unused skills), A6 (speed above the cap) and A7 (shield values without a shield)
are not ported: the stacking audit (gdcheck/stats/audit.py) covers them.

Sources (as of 26.09.2026): weapon conditions grimdawn.fandom.com/wiki/<constellation> (one page
per constellation); conversion grimdawn.fandom.com/wiki/Game_Mechanics and Crate forum (Zantai
2017: damage is never converted twice).
"""
from __future__ import annotations

from ..damage_types import CONVERSION_TYPES, main_damage
from ..effect_uptime import with_buff
from ..save_facts import (DEVOTION_WEAPON, EFFECT_PERMANENT, EFFECT_TOGGLE, RuleContext,
                          conversion_entries, parent_skill, weapon_categories)
from .findings import ERROR, NOTE, Finding, points

# conversion source type -> percentage field of the character sheet
CONVERSION_FIELD = {
    "Physical": "offensivePhysicalModifier", "Pierce": "offensivePierceModifier",
    "Fire": "offensiveFireModifier", "Cold": "offensiveColdModifier",
    "Lightning": "offensiveLightningModifier", "Poison": "offensivePoisonModifier",
    "Life": "offensiveLifeModifier", "Aether": "offensiveAetherModifier",
    "Chaos": "offensiveChaosModifier",
}
# item parts whose conversions act on all damage of the character (A5)
_CONVERTING_PARTS = ("basename", "prefix_name", "suffix_name", "modifier_name", "relic_name",
                     "relic_bonus", "augment_name", "ascended_name", "ascended_2h_name")


def check_a1(ctx: RuleContext) -> list[Finding]:
    """A1: devotion points in a constellation whose weapon condition the active weapon set does not
    meet (ERROR). A1b: a constellation whose condition is met but which is not taken (NOTE; never
    for the two-handed condition)."""
    main, off = ctx.weapon_types()
    present = weapon_categories(main, off)
    nodes: dict = {}
    for s in ctx.skills:
        rn = s["skill_name"]
        if s.get("level", 0) < 1 or "/devotion/" not in rn:
            continue
        name = ctx.index.display(rn)
        if name in DEVOTION_WEAPON and not rn.endswith("_skill.dbr"):
            nodes.setdefault(name, []).append(rn)
    out = []
    for name, recs in nodes.items():
        condition, text = DEVOTION_WEAPON[name]
        if condition in present:
            continue
        weapons = ", ".join(t.replace("WeaponMelee_", "").replace("WeaponHunting_", "")
                            .replace("WeaponArmor_", "") for t in main + off if t) or "none"
        out.append(Finding(
            "A1", "A", ERROR, f"{name}: weapon requirement not met",
            f"{points(len(recs), 'devotion point')} in {name}. Requirement: {text}. "
            f"Active weapons: {weapons}. The bonuses do not apply.",
            "Wiki (constellation page)",
            f"Move the points out of {name} - first check whether its affinity "
            f"is needed by other constellations."))
    for name, (condition, text) in DEVOTION_WEAPON.items():
        if condition in present and name not in nodes and condition != "two_handed":
            out.append(Finding(
                "A1b", "A", NOTE,
                f"{name} devotion is available for your equipped weapon, but the constellation is not taken",
                f"Requirement {text} is met.", "Wiki (constellation page)",
                "Consider it for wasted points (mind the affinity)."))
    return out


def check_a3(ctx: RuleContext) -> list[Finding]:
    """A3: a skill converts 100 % of a type away - '+% <type>' from gear/skills does not apply to
    that skill (NOTE)."""
    out = []
    for s in ctx.skills:
        rn = s["skill_name"]
        if s.get("level", 0) < 1:
            continue
        lvl = ctx.effective_level(rn)
        for rec in with_buff(ctx, rn):
            for c in conversion_entries(rec, lvl):
                if c["prozent"] < 100:
                    continue
                fld = CONVERSION_FIELD.get(c["von"])
                value = ctx.sheet.get(fld, 0) if fld else 0
                if value <= 0:
                    continue
                parent = parent_skill(rn, ctx.index)
                target = ctx.name(parent) if parent else "the skill"
                out.append(Finding(
                    "A3", "A", NOTE,
                    f"at least +{value:.0f}% {c['von']} does not apply to {target}",
                    f"{ctx.name(rn)} converts 100% {c['von']} to {c['nach']} "
                    f"(value from gear/skills, without attribute share). "
                    f"Converted damage uses the modifiers of the target type.",
                    "Wiki (Game Mechanics)",
                    f"{c['von']} % bonuses are dead weight for {target}; when swapping, "
                    f"prefer {c['nach']} bonuses. Still applies to other sources "
                    f"of this type (e.g. bleed parts, weapon damage of other skills)."))
    return out


def check_a4(ctx: RuleContext) -> list[Finding]:
    """A4: points + gear bonuses above the skill's last rank (the length of its rank arrays)."""
    out = []
    for s in ctx.skills:
        rn, p = s["skill_name"], s.get("level", 0)
        if p < 1 or "/devotion/" in rn or "_classtraining" in rn:
            continue
        lvl = ctx.effective_level(rn)
        lengths = [len(v) for r in with_buff(ctx, rn) for v in r.values()
                   if isinstance(v, list) and v and isinstance(v[0], (int, float))]
        if not lengths:
            continue
        cap = max(lengths)
        if lvl > cap:
            out.append(Finding(
                "A4", "A", NOTE, f"{ctx.name(rn)}: {lvl - cap} ranks above the cap",
                f"Points {p} + bonuses give rank {lvl}, cap {cap}.", "DB",
                "Move points or +skill bonuses elsewhere."))
    return out


def check_a5(ctx: RuleContext) -> list[Finding]:
    """A5: conversion on gear or from permanent buffs/auras AWAY from the main damage type (ERROR).

    Such conversions act on all damage of the character [Wiki]. Damage is never converted twice
    [Crate, Zantai 2017] - only original damage of the main type is affected."""
    main = main_damage(ctx.sheet, ctx.attributes.get("cunning"), ctx.attributes.get("spirit"))
    if not main:
        return []
    sources = []
    for slot, it in ctx.worn:
        for key in _CONVERTING_PARTS:
            rec = ctx.record(it.get(key))
            for c in conversion_entries(rec, 1):
                sources.append((f"{ctx.item_name(it)} ({slot})", c))
    for s in ctx.skills:
        rn = s["skill_name"]
        if s.get("level", 0) < 1 or ctx.effect_kind(rn) not in (EFFECT_PERMANENT, EFFECT_TOGGLE):
            continue
        for rec in with_buff(ctx, rn):
            for c in conversion_entries(rec, ctx.effective_level(rn)):
                sources.append((f"{ctx.name(rn)} (Buff/Aura)", c))
    out = []
    for label, c in sources:
        if main["typ"] in CONVERSION_TYPES.get(c["von"], ()) and c["nach"] != c["von"]:
            out.append(Finding(
                "A5", "A", ERROR,
                f"{label} converts {c['prozent']:g}% {main['name']} to {c['nach']}",
                f"{main['name']} is your strongest damage type ({main['summe']:.0f}% global). "
                f"Conversion on gear or from buffs affects all damage; the "
                f"converted part then uses {c['nach']} bonuses instead of your "
                f"{main['name']} bonuses. Only original {main['name']} damage is "
                f"affected - damage is never converted twice.",
                "Wiki (Conversion), Crate-Forum (Zantai 2017)",
                "Swap the item or affix; reroll at the Inventor if it is an affix.",
                "Compare the damage breakdown in the skill tooltip."))
    return out
