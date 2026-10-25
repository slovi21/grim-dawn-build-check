"""Category E - point spread: port of the .35 pruefungen rules E1-E6.

  E1  unspent attribute/skill/devotion points (WARNING)
  E2  attribute points outside Physique (NOTE)
  E3  mastery bar below 50 (NOTE)
  E4  a modifier of an attack skill with more points than its base skill (NOTE)
  E5  an attack skill with points, neither on the hotbar nor bound to a power (WARNING)
  E6  a toggle switched off in the save (WARNING)

Free points and base attributes come from the SaveFacts document (legacy keys 'fortschritt' and
'attribute_basis'); everything else from the RuleContext.
"""
from __future__ import annotations

from ..save_facts import EFFECT_SKILL_ONLY, RuleContext, parent_skill
from .findings import NOTE, WARNING, Finding, points

MASTERY_MAX = 50
# never an "unused skill": base skills and potions
BASE_SKILLS = ("records/skills/default/",)


def check_e1(ctx: RuleContext) -> list[Finding]:
    progress = ctx.facts.get("fortschritt") or {}
    free = [(n, progress.get(k) or 0) for n, k in (("attribute", "attributpunkte_frei"),   # .62: was 'Attribut'
                                                   ("skill", "skillpunkte_frei"),
                                                   ("devotion", "devotionspunkte_frei"))]
    free = [(n, v) for n, v in free if v]
    if not free:
        return []
    return [Finding("E1", "E", WARNING, "Unspent points",
                    ", ".join(points(v, f"{n} point") for n, v in free) + " unspent.",
                    "Savefile", "Spend them.")]


def check_e2(ctx: RuleContext) -> list[Finding]:
    base = ctx.facts.get("attribute_basis") or {}
    cun = max(0, round(((base.get("cunning") or 50) - 50) / 8))
    spi = max(0, round(((base.get("spirit") or 50) - 50) / 8))
    if not cun + spi:
        return []
    return [Finding(
        "E2", "E", NOTE, f"{points(cun + spi, 'attribute point')} outside Physique",
        f"Cunning {cun}, Spirit {spi} points.", "Guides, Forum",
        "Only useful for item requirements or deliberate scaling; otherwise Physique. "
        "Section 9 calculates the best spread.")]


def check_e3(ctx: RuleContext) -> list[Finding]:
    return [Finding("E3", "E", NOTE, f"Mastery bar {ctx.name(s['skill_name'])} at {s['level']}/{MASTERY_MAX}",
                    "Attributes and Health are missing.", "Guides")
            for s in ctx.skills
            if "_classtraining" in s["skill_name"] and 0 < s.get("level", 0) < MASTERY_MAX]


def check_e4(ctx: RuleContext) -> list[Finding]:
    out = []
    for s in ctx.skills:
        rn, pm = s["skill_name"], s.get("level", 0)
        if pm < 1:
            continue
        parent = parent_skill(rn, ctx.index)
        if parent and ctx.effect_kind(parent) == EFFECT_SKILL_ONLY:
            pb = ctx.points.get(parent, 0)
            if pm > pb:
                out.append(Finding(
                    "E4", "E", NOTE, f"{ctx.name(rn)} ({pm}) > {ctx.name(parent)} ({pb})",
                    "The modifier only boosts this one skill. More points in the "
                    "modifier than in the skill itself suggests spread-out points.", "eigen",
                    f"Check how much {ctx.name(parent)} is worth in the build."))
    return out


def _active_skills(ctx: RuleContext):
    """(skill entry, class) of skills with points that need the player's doing - no base skills,
    devotions, item skills, modifiers, transmuters, secondary/on-hit/weapon pool skills, passives
    or the mastery bar."""
    for s in ctx.skills:
        rn = s["skill_name"]
        if s.get("level", 0) < 1 or rn.startswith(BASE_SKILLS) or "/devotion/" in rn \
                or "/itemskills" in rn:
            continue
        cls = str((ctx.record(rn) or {}).get("Class", ""))
        if cls in ("Skill_Modifier", "Skill_Transmuter") or "Modifier" in cls \
                or cls.startswith(("SkillSecondary_", "Skill_OnHit", "Skill_WPAttack")) \
                or "Passive" in cls or cls == "Skill_Mastery":
            continue
        yield s, cls


def _is_toggle(cls: str) -> bool:
    return "Toggled" in cls or cls == "Skill_Shapeshift"


def check_e5(ctx: RuleContext) -> list[Finding]:
    bound = {target for target, _ in ctx.bindings.values()}
    return [Finding("E5", "E", WARNING, f"{ctx.name(s['skill_name'])}: {points(s['level'])}, not on the hotbar",
                    "The skill is neither used nor the target of a celestial power.",
                    "Savefile", "Move the points or put the skill on the hotbar.")
            for s, cls in _active_skills(ctx)
            if not _is_toggle(cls) and s["skill_name"] not in ctx.hotbar and s["skill_name"] not in bound]


def check_e6(ctx: RuleContext) -> list[Finding]:
    return [Finding("E6", "E", WARNING, f"{ctx.name(s['skill_name'])} is switched off",
                    "Toggle is off according to the savefile.", "Savefile",
                    "Switch it on or move the points.")
            for s, cls in _active_skills(ctx)
            if _is_toggle(cls) and not s.get("skill_active") and cls != "Skill_Shapeshift"]
