"""Category D - bindings of celestial powers: port of the .35 pruefungen rules D1-D4.

  D1  power not bound - it never triggers (WARNING)
  D2  on-hit/low-health power bound to a temporary buff (WARNING)
  D3  bound skill neither on the hotbar nor triggering by itself (WARNING)
  D4  power bound to a skill with few points (NOTE) - or to a skill granted by an item or rune:
      replacing that item/rune drops the skill and the binding (.68)

Bindings come from block 8 'skills' AND 'item_skills' (RuleContext.bindings, .67 fix).
Sources: Steam discussions 2257935717966739304 and 2994292014277354080 (bindings); weapon pool
skills trigger on default attacks [Wiki, Guide Val]; a pet summon's pet triggers the power
[Crate Game Guide, Devotion].
"""
from __future__ import annotations

from ..save_facts import EFFECT_SKILL_ONLY, EFFECT_TEMPORARY, RuleContext, item_source_records
from .findings import NOTE, WARNING, Finding, points

_DEFENSIVE_TRIGGERS = ("hit", "health", "block")
# Form abilities run at the rank of their shapeshift and have no points of their own - binding a
# power to them is the normal case, not a dependency worth a note (Werewolf: Feral Claws, Rip and
# Tear; Wereraven: Ice Talons, Cold Snap).
FORM_ABILITY_PREFIXES = ("werewolf1_skill", "wereraven1_skill01", "wereraven1_skill02")
LOW_POINTS = 6


def celestial_powers(ctx: RuleContext) -> list[str]:
    """Celestial power records with a point in them, in save order."""
    return [s["skill_name"] for s in ctx.skills
            if s.get("level", 0) > 0 and "/devotion/" in s["skill_name"]
            and s["skill_name"].endswith("_skill.dbr")]


def _bound(ctx: RuleContext):
    """(power, bound skill, trigger controller) for every bound power."""
    for power in celestial_powers(ctx):
        if power in ctx.bindings:
            target, trigger = ctx.bindings[power]
            yield power, target, trigger


def is_form_ability(rec_name: str) -> bool:
    return (rec_name or "").rsplit("/", 1)[-1].startswith(FORM_ABILITY_PREFIXES)


def item_skill_source(ctx: RuleContext, skill: str) -> tuple[str, str, str] | None:
    """(kind, item name, slot) when `skill` is granted by worn gear rather than learned: kind "rune"
    for a rune (records/items/enchants/runes/), else "item". Read from block 8 'item_skills' (its
    source record) and matched against the parts of the worn items; None for learned skills."""
    if ctx.points.get(skill, 0) > 0:
        return None
    for entry in (ctx.blocks.get(8) or {}).get("item_skills") or []:
        src = entry.get("unknown") or ""
        if entry.get("skill_name") != skill or not src.startswith("records/items/"):
            continue
        kind = "rune" if "/enchants/runes/" in src else "item"
        for slot, it in ctx.worn:
            if src in item_source_records(it):
                return kind, ctx.item_name(it), slot
        return kind, ctx.name(src), ""
    return None


def check_d1(ctx: RuleContext) -> list[Finding]:
    return [Finding("D1", "D", WARNING, f"{ctx.name(k)} is not bound",
                    "The celestial power never triggers.", "Savefile",
                    "Bind it to a suitable skill.", subject=k)
            for k in celestial_powers(ctx) if k not in ctx.bindings]


def check_d2(ctx: RuleContext) -> list[Finding]:
    out = []
    for power, target, trigger in _bound(ctx):
        if any(t in trigger.lower() for t in _DEFENSIVE_TRIGGERS) \
                and ctx.effect_kind(target) == EFFECT_TEMPORARY:
            out.append(Finding(
                "D2", "D", WARNING, f"{ctx.name(power)} bound to a temporary buff",
                f"Bound to {ctx.name(target)} (temporary). On-hit/low-health triggers "
                "only work while the buff is running.", "Forum",
                "Bind it to an aura or toggle that is always on."))
    return out


def check_d3(ctx: RuleContext) -> list[Finding]:
    out = []
    for power, target, _trigger in _bound(ctx):
        cls = str((ctx.record(target) or {}).get("Class", ""))
        # weapon pool skills and passives trigger by themselves; a pet summon's pet triggers it
        automatic = cls.startswith(("Skill_WPAttack", "Skill_OnHit")) or "Passive" in cls \
            or "SpawnPet" in cls
        if target not in ctx.hotbar and not automatic \
                and ctx.effect_kind(target) in (EFFECT_SKILL_ONLY, EFFECT_TEMPORARY):
            out.append(Finding(
                "D3", "D", WARNING, f"{ctx.name(power)}: bound skill not on the hotbar",
                f"Bound to {ctx.name(target)}, which is not on the hotbar.",
                "Savefile", "Put the skill on the hotbar or rebind the power."))
    return out


def check_d4(ctx: RuleContext) -> list[Finding]:
    out = []
    for power, target, _trigger in _bound(ctx):
        if ctx.effect_kind(target) != EFFECT_SKILL_ONLY or ctx.points.get(target, 0) > LOW_POINTS \
                or is_form_ability(target):
            continue
        pname, tname = ctx.name(power), ctx.name(target)
        source = item_skill_source(ctx, target)
        if source:
            kind, item, slot = source
            where = f"{item} ({slot})" if slot else item
            if kind == "rune":
                title, origin, what = f"rune on {item}", f"the rune on {where}", "that item or rune"
            else:
                title, origin, what = f"from {item}", where, "that item"
            out.append(Finding(
                "D4", "D", NOTE, f"{pname} depends on {tname} ({title})",
                f"{tname} comes from {origin}; if you replace {what}, rebind the power.", "Savefile",
                f"If you replace {what}, rebind {pname}."))
            continue
        out.append(Finding(
            "D4", "D", NOTE, f"{pname} depends on {tname}",
            f"{tname} has {points(ctx.points.get(target, 0))}. If you drop the "
            "skill, the power must be rebound.", "eigen"))
    return out
