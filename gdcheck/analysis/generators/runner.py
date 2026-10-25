"""Entry point of the candidate generators: attributes, skill points, devotions and rerolls
(port of the .35 `assess_core.bewertungsprofil / punkte_bewertungen / reroll_empfehlungen` and
of its .35 adapter moves.py - Task 7 of release .67/.68).

The generators are fed with the NEW sheet values (stats.sheet). Their own value scale only
ranks candidates; the numbers shown to the player are recalculated by the counterfactual
engine (analysis/proposals/generated.py).

Everything a run needs is explicit: GeneratorParams (difficulty penalty, Life Leech penalty,
weapon speed factor, stage targets, melee) and GeneratorInput (save facts, rule context,
sheet, kept-up buffs). Nothing is written to module state or to the index; the build-order
search does not touch the interpreter's recursion limit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType

from ..save_facts import RuleContext, SaveFacts, build_rule_context, build_save_facts
from . import attributes as AT
from . import devotions as DV
from . import rerolls as RR
from . import skills as SK
from .abilities import abilities
from .resistances import Resistances
from .targets import LEECH_FIELD, LEECH_PENALTY_DEFAULT, TARGETS
from .value_scale import HEALTH_TARGET, ValueProfile, make_profile


@dataclass(frozen=True)
class GeneratorParams:
    """Everything that used to be module state of the .35 generators."""
    penalty: MappingProxyType          # resist field -> difficulty penalty (.35 assess_core.ABZUG)
    leech_penalty: float = LEECH_PENALTY_DEFAULT
    weapon_factor: float = 1.0         # attack speed weapon factor, <= 1 (.35 affixe.WEAPON_FACTOR)
    melee: bool = False
    stage_targets: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))
    with_reroll: bool = True
    attribute_min_gain: float = AT.MIN_GAIN

    @classmethod
    def from_context(cls, ctx, snap, stage_targets: dict, melee: bool,
                     with_reroll: bool = True) -> "GeneratorParams":
        # .59 (Paket 5): the sheet attack speed is the summed bonus x weapon factor (KG-11) -
        # the generators' cap check uses it
        factor = min(float(snap.sheet.speeds["attack"].get("weapon_factor", 1.0)), 1.0)
        return cls(penalty=MappingProxyType({k: v for k, v in ctx.penalty.items() if k != LEECH_FIELD}),
                   leech_penalty=ctx.penalty.get(LEECH_FIELD, LEECH_PENALTY_DEFAULT),
                   weapon_factor=factor, melee=melee,
                   stage_targets=MappingProxyType(dict(stage_targets)), with_reroll=with_reroll)


@dataclass(frozen=True, eq=False)
class GeneratorInput:
    facts: SaveFacts
    rules: RuleContext
    sheet: dict                    # character sheet (stats.sheet fields)
    buff: dict                     # kept-up buffs: combat - sheet per field
    abilities: dict                # sheet abilities (health, da, oa, attributes ...)

    @property
    def index(self):
        return self.rules.index

    @property
    def combat(self) -> dict:
        out = dict(self.sheet)
        for f, v in self.buff.items():
            out[f] = out.get(f, 0) + v
        return out


def buff_delta(snap) -> dict:
    """Kept-up buffs = combat - sheet per field (fix .44: was combat - combat = 0, so the
    candidate generators never saw kept-up buffs)."""
    c, s = snap.combat.fields, snap.sheet.fields
    return {k: c.get(k, 0.0) - s.get(k, 0.0) for k in set(c) | set(s)
            if abs(c.get(k, 0.0) - s.get(k, 0.0)) > 1e-9}


def make_input(character, index, snap) -> GeneratorInput:
    sheet = dict(snap.sheet.fields)
    facts = build_save_facts(character, index)
    return GeneratorInput(facts=facts, rules=build_rule_context(facts, index, sheet=sheet),
                          sheet=sheet, buff=buff_delta(snap), abilities=dict(snap.sheet.abilities))


def value_profile(inp: GeneratorInput, params: GeneratorParams, resists: Resistances) -> ValueProfile:
    """The profile of the common value scale - one for attributes, skills, devotions and
    rerolls, so they all rank alike."""
    index, facts = inp.index, inp.facts
    targets = params.stage_targets
    return make_profile(
        inp.sheet, inp.combat, inp.abilities, resists.rows(inp.sheet, inp.buff), dict(inp.rules.points),
        params.melee, {"cunning": inp.abilities["cunning"], "spirit": inp.abilities["spirit"]}, index,
        abilities_fn=lambda new: abilities(facts, new, index),
        health_target=HEALTH_TARGET if params.melee else 0,
        weapon_factor=params.weapon_factor, pet=False, leech_penalty=params.leech_penalty,
        physical_target=targets.get("physical", TARGETS["physical_combat"]),
        da_target=targets.get("da", TARGETS["da"]),
        health_goal=targets.get("health", TARGETS["health"]),
        leech_target=targets.get("leech", TARGETS["leech"]))


SECTIONS = ("attributes", "skills", "devotions", "rerolls")


def _guarded(fn) -> dict | list:
    """A failing generator must not stop the report - its section carries the error (E6 names it)."""
    try:
        return fn()
    except Exception as ex:                                      # noqa: BLE001
        return {"error": f"{type(ex).__name__}: {ex}"}


def failures(raw: dict) -> dict:
    """{section: "<type>: <message>"} of the generators that failed in a run() result (else {})."""
    return {k: v["error"] for k, v in (raw or {}).items()
            if isinstance(v, dict) and set(v) == {"error"} and isinstance(v["error"], str)}


def generate(inp: GeneratorInput, params: GeneratorParams, override=None, sections=None,
             light: bool = False) -> dict:
    """{"attributes", "skills", "devotions"[, "rerolls"]} - keys documented in __init__.

    override: {section: fn(inp, profile)} replaces that generator (a hook for checks, e.g. a forced
    failure - no module patching needed); None = the real generators.
    sections: only these generators run and only their keys come back (None = all; "rerolls" still
    needs params.with_reroll when sections is None).
    light: the build-check mode (one pass of skills and devotions per analysis, so it must be cheap):
    the devotion generator skips the full plans A/B and searches 1-for-1 swaps only; the skill
    generator is the full one (5-11 ms). Everything else is unchanged."""
    wanted = SECTIONS if sections is None else tuple(sections)
    unknown = set(wanted) - set(SECTIONS)
    if unknown:
        raise ValueError(f"unknown generator section(s): {sorted(unknown)}")
    if sections is None and not params.with_reroll:
        wanted = tuple(n for n in wanted if n != "rerolls")
    index = inp.index
    if not hasattr(index, "records"):
        return {"rerolls": []} if "rerolls" in wanted else {}
    resists = Resistances(dict(params.penalty))
    combat = inp.combat
    pr = value_profile(inp, params, resists)
    override = dict(override or {})
    unknown = set(override) - set(SECTIONS)
    if unknown:
        raise ValueError(f"unknown generator section(s): {sorted(unknown)}")

    def swap_check(delta):
        # cap/need against the combat value (kept-up buffs count)
        return resists.swap_check(combat, delta)

    def section(name, fn):
        hook = override.get(name)
        return _guarded((lambda: hook(inp, pr)) if hook is not None else fn)

    def devotions():
        if light:
            return DV.assess(inp.rules, inp.facts, pr, swap_check, plans=False, swap_limits=(1, 1))
        return DV.assess(inp.rules, inp.facts, pr, swap_check)

    def rerolls():
        level = (inp.facts.get("charakter") or {}).get("level") or 100
        return RR.recommendations(list(inp.rules.worn), index, pr, swap_check, level)

    makers = {
        "attributes": lambda: AT.assess(inp.facts, inp.sheet, pr.abilities_fn, pr,
                                        min_gain=params.attribute_min_gain),
        "skills": lambda: SK.assess(inp.rules, pr),
        "devotions": devotions,
        "rerolls": rerolls,
    }
    return {name: section(name, makers[name]) for name in SECTIONS if name in wanted}


def run(ctx, snap, stage_targets: dict, melee: bool, with_reroll: bool = True, override=None,
        sections=None, light: bool = False) -> dict:
    """Candidates of one analysed character (ctx: domain Context, snap: stats snapshot);
    override, sections, light: see generate()."""
    params = GeneratorParams.from_context(ctx, snap, stage_targets, melee, with_reroll)
    return generate(make_input(ctx.character, ctx.index, snap), params, override, sections, light)


def constellations(index) -> dict:
    return DV.constellations(index)
