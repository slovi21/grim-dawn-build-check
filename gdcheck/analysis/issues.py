"""Issues: build errors, self-penalties, bindings, point spread (rule catalogue A-E in
analysis/rules, ported from .35 pruefungen in .68), plus model limits (F-007). Self-penalty always
first (N-019)."""
from __future__ import annotations

from dataclasses import dataclass

from ..data import gametables as GT

SEVERITY_RANK = {"ERROR": 0, "WARNING": 1, "NOTE": 2, "INFO": 3}
# M13, patch 1.3.1.0 (Crate forum 160806): the fix text of model limit L1 from game version 1.3.1 on (<= 160 characters,
# the width of the D1 column)
PET_BASE_RESISTS = ("Since 1.3.1 pets have non-Physical base resistances from 1% at level 1 to 80% by level 80; "
                    "most pet resist bonuses of gear and devotions are gone.")


@dataclass(frozen=True)
class Issue:
    code: str
    category: str
    severity: str              # ERROR / WARNING / NOTE / INFO
    title: str
    detail: str
    evidence: str
    fix: str
    check_in_game: str
    self_penalty: bool = False
    subject: str = ""          # report v2: the record the issue is about (see Finding.subject)


def _from_finding(f) -> Issue:
    return Issue(f.code, f.category, f.severity, f.title, f.detail, f.evidence, f.fix,
                 f.check_in_game, self_penalty=f.code.startswith("C1"), subject=f.subject)


def rule_findings(ctx, snap) -> list:
    """Rule catalogue A-E (analysis/rules) on the save facts and the NEW sheet values."""
    from . import rules
    from .save_facts import build_rule_context, build_save_facts
    facts = build_save_facts(ctx.character, ctx.index)
    attributes = {"cunning": snap.sheet.abilities["cunning"], "spirit": snap.sheet.abilities["spirit"]}
    rctx = build_rule_context(facts, ctx.index, dict(snap.sheet.fields), dict(ctx.penalty), attributes)
    return rules.run_rules(facts, rctx)


def model_limits(ctx, snap, sources) -> list[Issue]:
    out = []
    pet = sum(p for r, p in ctx.character.skills.items()
              if any(t in r.lower() for t in ("summon", "raiseskeleton", "pets/", "_pet")))
    if pet >= 10:
        # M13: since 1.3.1 pets have base resistances and most pet resistance bonuses are gone (ruleset
        # pet_base_resists). The title reaches E6 and D1, the fix column D1 - the detail is shown nowhere.
        base = bool((getattr(ctx, "ruleset", None) or {}).get("pet_base_resists"))
        out.append(Issue("L1", "L", "INFO",
                         "Pet bonuses and pet resistances are not modelled" if base else "Pet bonuses are not modelled",
                         "'Bonus to All Pets' is not in the game data index - pet damage and pet "
                         "devotions are valued too low.", "index schema 9", PET_BASE_RESISTS if base else "", ""))
    if GT.table(ctx.index, GT.COST_PREFIX + ".dbr"):
        # .85 (gap 1 of M14): the index knows the requirements; worn items that miss them are taken out (issue G0), but
        # a proposed respec is not checked against them
        out.append(Issue("L2", "L", "INFO", "Attribute moves ignore item requirements",
                         "Worn items whose requirement is not met are taken out (they give nothing in game); a "
                         "proposed respec is not checked against the requirements of your gear.", "index schema 10", "",
                         "Required Cunning/Spirit on worn items before a respec"))
    else:
        out.append(Issue("L2", "L", "INFO", "Item attribute requirements are not in the index",
                         "Lowering Cunning or Spirit can make worn items unusable - check the requirement "
                         "in game before a respec.", "index schema 9", "", "Required Cunning/Spirit on worn items"))
    if any(s.form for s in sources.skills):
        out.append(Issue("L3", "L", "INFO", "Shapeshift weapon damage is engine behaviour",
                         "Form abilities hit with both weapons (tested against the tooltip, +-2%); "
                         "the mechanism is not in the game database.", "golden test 30.09.2026", "", ""))
    out.append(Issue("L4", "L", "INFO", "Boss damage is a model",
                     "Boss and spawn damage come from the database at your difficulty (E7); their action "
                     "rates and AI behaviour are estimates - ratings are preparation/risk, never win/lose.",
                     "game database; KG-08", "", ""))
    return out


def disabled_issue(disabled) -> Issue | None:
    """G0 (.85, decision 69): worn items the game switches off - an ERROR that always comes first."""
    from .requirements import texts
    t = texts(disabled)
    if not t:
        return None
    return Issue("G0", "G", "ERROR", t["title"], t["detail"],
                 "requirement = cost formula of the item in the game database (item level, value lines; affixes not "
                 "counted, so at least)", t["fix"], "a red background behind the item on the character sheet")


def issues(ctx, snap, sources, disabled=()) -> list[Issue]:
    found = [_from_finding(f) for f in rule_findings(ctx, snap)]
    found += model_limits(ctx, snap, sources)
    g0 = disabled_issue(disabled)
    found += [g0] if g0 else []
    found.sort(key=lambda i: (i.code != "G0", not i.self_penalty, SEVERITY_RANK.get(i.severity, 9), i.code, i.title))
    return found
