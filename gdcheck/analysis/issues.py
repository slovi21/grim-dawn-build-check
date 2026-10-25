"""Issues: build errors, self-penalties, bindings, point spread (rule catalogue A-E in
analysis/rules, ported from .35 pruefungen in .68), plus model limits (F-007). Self-penalty always
first (N-019)."""
from __future__ import annotations

from dataclasses import dataclass

SEVERITY_RANK = {"ERROR": 0, "WARNING": 1, "NOTE": 2, "INFO": 3}


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


def _from_finding(f) -> Issue:
    return Issue(f.code, f.category, f.severity, f.title, f.detail, f.evidence, f.fix,
                 f.check_in_game, self_penalty=f.code.startswith("C1"))


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
        out.append(Issue("L1", "L", "INFO", "Pet bonuses are not modelled",
                         "'Bonus to All Pets' is not in the game data index - pet damage and pet "
                         "devotions are valued too low.", "index schema 9", "", ""))
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


def issues(ctx, snap, sources) -> list[Issue]:
    found = [_from_finding(f) for f in rule_findings(ctx, snap)]
    found += model_limits(ctx, snap, sources)
    found.sort(key=lambda i: (not i.self_penalty, SEVERITY_RANK.get(i.severity, 9), i.code, i.title))
    return found
