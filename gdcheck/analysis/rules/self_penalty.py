"""Category C - self-penalty: port of the .35 pruefungen rule C1.

Worn gear or skills that lower YOUR OWN values (minus in the tooltip). Many players do not read
the minus as a drawback for themselves, so the rule computes the resistances while the penalty is
active: one below the cap makes it an ERROR, otherwise a WARNING.
"""
from __future__ import annotations

from ..effect_uptime import effect_sources, uptime
from ..save_facts import EFFECT_ENEMY_DEBUFF, EFFECT_SKILL_ONLY, RuleContext, stat_text
from .findings import ERROR, WARNING, Finding

# own values a self-penalty can lower
OWN_FIELDS = ("defensiveFire", "defensiveCold", "defensiveLightning", "defensivePoison",
              "defensivePierce", "defensiveBleeding", "defensiveLife", "defensiveAether",
              "defensiveChaos", "defensivePhysical", "defensiveElementalResistance",
              "characterDefensiveAbility", "characterDefensiveAbilityModifier",
              "characterLife", "characterLifeModifier", "defensiveProtection",
              "defensiveProtectionModifier")
RESIST_NAMES = {"defensiveFire": "Fire", "defensiveCold": "Cold",
                "defensiveLightning": "Lightning", "defensivePoison": "Acid",
                "defensivePierce": "Pierce", "defensiveBleeding": "Bleeding",
                "defensiveAether": "Aether", "defensiveChaos": "Chaos",
                "defensiveLife": "Vitality", "defensivePhysical": "Physical"}
RESIST_CAP = 80
_ELEMENTAL = ("defensiveFire", "defensiveCold", "defensiveLightning")


def net_resistance(ctx: RuleContext, fld: str) -> float:
    """Sheet resistance incl. elemental/all resistance minus the difficulty penalty."""
    value = ctx.sheet.get(fld, 0)
    if fld in _ELEMENTAL:
        value += ctx.sheet.get("defensiveElementalResistance", 0)
    if fld != "defensivePhysical":
        value += max(ctx.sheet.get("defensiveAllResistance", 0), 0)
    return value - (ctx.penalty or {}).get(fld, 0)


def resistance_cap(ctx: RuleContext, fld: str) -> float:
    cap = RESIST_CAP + ctx.sheet.get(fld + "MaxResist", 0)
    if fld != "defensivePhysical":
        cap += ctx.sheet.get("defensiveAllMaxResist", 0)
    return cap


def check_c1(ctx: RuleContext, sources: list | None = None) -> list[Finding]:
    """C1: one finding per source that lowers own values (enemy debuffs and attack records are
    skipped; so are skills whose values act on one skill only or on enemies)."""
    sources = sources if sources is not None else effect_sources(ctx)
    out = []
    for q in sources:
        if any(t in k for k in q["klassen"] for t in ("Debuf", "Contageous", "Attack")):
            continue
        if q["art"] == "skill" and q.get("wirkung") in (EFFECT_SKILL_ONLY, EFFECT_ENEMY_DEBUFF):
            continue
        neg = {f: v for f, v in q["felder"].items() if f in OWN_FIELDS and v < 0}
        if not neg:
            continue
        temporary = any("Buff" in k for k in q["klassen"]) or q["art"] == "item_skill"
        parts, below_cap = [], []
        for f, v in neg.items():
            t = f"{stat_text(f, v, ctx.index) or f'{v:g} {f}'}"
            affected = (list(_ELEMENTAL) if f == "defensiveElementalResistance" else
                        [f] if f in RESIST_NAMES else [])
            if f == "defensiveElementalResistance":
                t += " (on Fire/Cold/Lightning)"
            parts.append(t)
            for rf in affected:
                # .62 (product review): a permanent drawback is already in the sheet values - show
                # 'without it -> now' instead of subtracting it a second time
                before = net_resistance(ctx, rf) - (0 if temporary else v)
                after = before + v
                if rf == "defensivePhysical":
                    parts.append(f"Physical {before:.0f} -> {after:.0f} %")
                elif after < resistance_cap(ctx, rf):
                    below_cap.append(f"{RESIST_NAMES[rf]} {before:.0f} -> {after:.0f} %")
        share = uptime(ctx, dict(q, record=q.get("record") or q.get("gruppe")), next(iter(neg)))["anteil"]
        when = ("permanently" if not temporary else
                "while the effect is active" + ("" if share is None else
                                                f" (about {share*100:.0f}% of the time)"))
        detail = f"{when[0].upper() + when[1:]}: " + "; ".join(parts) + "."
        if below_cap:
            detail += " BELOW CAP: " + ", ".join(below_cap) + "."
        what = "item" if q["art"] in ("ausruestung", "item_skill") else "skill"
        out.append(Finding(
            "C1", "C", ERROR if below_cap else WARNING,
            f"DRAWBACK FOR YOU: {q['quelle']}", detail, "DB",
            (f"This {what} lowers your own stats. Keep it only if "
             "the upside clearly outweighs it; otherwise replace it. Avoid triggering it "
             "in dangerous fights." if temporary else
             f"This {what} permanently lowers your own stats - "
             "decide deliberately whether the upside is worth it."),
            "Confirm sign and duration in the tooltip.",
            subject=(q.get("record") or "") if q["art"] == "skill" else ""))
    return out
