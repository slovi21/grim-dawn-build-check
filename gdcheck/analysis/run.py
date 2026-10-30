"""Full analysis of one character (everything below the report): snapshot, audit,
actions, stage, targets, score, areas, issues, bosses, proposals."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..combat import actions as ACT
from ..combat import frames as FR
from ..combat import rate as RATE
from ..combat import target_state as TS
from ..domain import loader
from ..domain.enums import Stage
from ..stats import audit as AUD
from ..stats import sheet as SH
from . import archetype as ARCH
from . import area_facts as AF
from . import benchmark as BM
from . import bosses as BOSS
from . import build_quality as BQ
from . import combat_check as CC
from . import issues as ISS
from . import readiness as RD
from . import stage as STG
from . import targets as TGT
from .proposals import consumables as CON
from .proposals import counterfactual as CF
from .proposals import gear as GEAR
from .proposals import select as SEL

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Analysis:
    ctx: object
    sources: object
    snap: object
    audit: list
    actions: list
    onslaught: list               # spender actions at 0 / half / max stacks
    targets_states: list
    da_shred: dict
    archetype: str
    stage: object
    stage_fails: list
    results: list
    score: dict
    areas: dict
    issues: list
    bosses: list
    proposals: list
    player_actions: list
    rates: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    combat_checks: list = field(default_factory=list)   # Boss Combat Check V1 (Expert View E7)
    plausibility: str | None = None
    area_facts: object = None     # .70: AreaFacts (sustain, main damage types, resistance share)
    benchmark: object = None      # .72: analysis.benchmark.Benchmark (Training Dummy, Mogara); None = not measured
    build_quality: object = None  # .72: analysis.build_quality.BuildQuality; None = the build check did not run


def run(ctx, sources=None, detail: bool = False, generator_override=None) -> Analysis:
    """generator_override (detail run only): {section: fn(inp, profile)} replaces a candidate generator -
    the hook that checks a generator failure end to end (proposals.generated)."""
    sources = sources or loader.build_sources(ctx.character, ctx.index)
    snap = SH.build(ctx, sources)
    aud = AUD.audit(ctx, sources, snap)
    tstates = TS.targets(ctx, snap, sources, ROOT)
    shred = TS.da_shred(snap, sources)
    acts = ACT.damage_per_action(ctx, sources, snap, tstates, onslaught=ctx.options.onslaught or 0)
    ons = []
    spenders = [a for a in acts if a.spender and a.onslaught_max]
    if spenders:
        n = spenders[0].onslaught_max
        for k in sorted({0, n // 2, n}):
            for a in ACT.damage_per_action(ctx, sources, snap, tstates, onslaught=k):
                if a.action_id == spenders[0].action_id:
                    ons.append(a)
    arch = ARCH.archetype(ctx, snap, acts)
    melee = arch == "melee"
    stg, fails = STG.stage(ctx, snap, melee)
    data = TGT.load()
    facts = AF.area_facts(ctx, sources, snap, stg)
    # .72: the damage benchmark rates against Ultimate monsters (the combat check stops below Ultimate the same way), so a
    # Normal or Elite character gets no dummy time; the shared procs and frame model are computed once for both
    fm = FR.FrameModel(ctx, sources)
    procs = ACT.proc_actions(ctx, sources, snap, tstates) if BM.applies(ctx) else None
    bench = BM.run(ctx, sources, snap, acts, procs, tstates, fm, pet=(arch == "pet")) if BM.applies(ctx) else None
    extra = {"da_shred": shred["total"], **AF.target_inputs(facts), **BM.target_inputs(bench)}
    results = TGT.evaluate(ctx, snap, stg, data, extra, melee)
    sc = RD.score(ctx, snap, stg, results, RD.settings(data))
    issues = ISS.issues(ctx, snap, sources)
    quality = BQ.assess(ctx, snap, {r.id: r for r in results}, melee, pet=(arch == "pet"),
                        early=(stg is Stage.EARLY)) if ctx.options.build_quality else None
    areas = RD.areas(results, issues, facts, quality)
    bosses = BOSS.assess(ctx, snap, stg, results, melee)
    base = CF.Baseline(ctx, sources, snap, stg, melee, data, extra, CF.main_damage_types(acts))
    props = GEAR.proposals(base, ctx.options.counterfactual_max_evals, ctx.options.top_k)
    props += CON.proposals(base, GEAR.target_fields(base))
    if detail:
        from .proposals import generated as GENP
        props += GENP.proposals(base, sc, generator_override)
    player = SEL.player_actions(props)
    eps, main_lbl, _t = RATE.main_attack_rate(snap, acts, fm)
    basis = f"{main_lbl}, animation frames" if main_lbl else None
    rates = {"procs": RATE.proc_rates(snap, sources, eps, basis), "rr": RATE.rr_profiles(snap),
             "attacks_per_second": eps}
    checks = CC.check(ctx, sources, snap, acts, tstates, ctx.options.onslaught or 0, procs, fm)
    return Analysis(ctx, sources, snap, aud, acts, ons, tstates, shred, arch, stg, fails, results, sc,
                    areas, issues, bosses, props, player, rates, extra, checks, CC.plausibility(ctx, checks), facts,
                    bench, quality)
