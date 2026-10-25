"""Candidate generators for attribute points, skill points, devotions and rerolls - the .35
generators moved out of the .35 code (deleted in .68), renamed and de-globalised (Task 7 of release
.67/.68, method: move + rename + de-globalise, not rewrite).

    from gdcheck.analysis import generators
    raw = generators.run(ctx, snap, stage_targets, melee)      # dict, see below

Modules (.35 origin in brackets)
    runner       run(), GeneratorParams, GeneratorInput        (assess_core, adapter moves.py)
    value_scale  ValueProfile, affix_value, extra_value        (affixe, skillpunkte.zusatz_wert)
    resistances  Resistances(penalty).rows / swap_check         (assess_core)
    abilities    abilities(facts, sheet, index)                 (assess_core.abilities)
    attributes   assess() - every attribute spread              (attribute)
    skills       assess() - marginal value and greedy moves     (skillpunkte)
    devotions    assess() - swaps, plans A/B, build order       (devotionen)
    rerolls      recommendations() - Inventor rerolls per affix (affixe)
    targets      TARGETS and Life Leech constants               (ziele, only what the generators read)

The combat kernel of .35 `kampf` is not needed by the generators (only the dead armor/boss
helpers of `assess_core`/`ziele` read it); its functions live in gdcheck.mechanics (hit, resist,
armor, procs, conversion).

Global state removed
    assess_core.ABZUG (dict refilled per run)   -> GeneratorParams.penalty -> Resistances
    affixe.WEAPON_FACTOR (set/reset per run)    -> GeneratorParams.weapon_factor -> ValueProfile
    profile dict mutated per affix ("andere")   -> frozen ValueProfile, `others` per call
    index._klein (cache written onto the index) -> rerolls.RecordCase per run
    sys.setrecursionlimit around the run        -> devotions.build_order_search is iterative
    gd_extract._DB / G.analyse, P.Kontext       -> analysis.save_facts (SaveFacts, RuleContext)
    attribute.LOHNT_AB patched by tests         -> GeneratorParams.attribute_min_gain

Result of run() (English keys; .35 key in brackets)
    "attributes" (attribute): current (jetzt), best (beste), total (gesamt), gain (gewinn),
        values_current/values_best (werte_jetzt/werte_beste), per_point (je_punkt),
        worth_it (lohnt), notes (hinweise), moved_from (weg_von), near (knapp), minimum,
        checked/rejected (geprueft/verworfen), main_name, health_min
    "skills" (skillpunkte): entries (eintraege: points, rank, last, next, own_last, own_next,
        effect, rated, note, full), moves (zuege: from/to/loss/gain), merged (zusammen:
        from/to/points/loss/gain), gain, flat_tail (auslauf), max_estimated, pet
    "devotions" (devotionen): taken (belegt: key, points, total, complete, value, per_point,
        fields, power, bound, bound_to, weapon_ok, gives, needs, carries), candidates,
        swaps (tausche: out/in/points_out/points_in/value/problems/delta), plan / plan_b
        (set, out (raus), in (rein), kept, locked, freed, value, delta, effect, resistances,
        guarded, points, order (reihenfolge: [(action, key, affinity, points)]), info),
        mode, free, affinity, pet, points, budget
    "rerolls" (reroll): [{slot, kind (art), item, affix, record, value, useful (gut),
        wasted (tot), effect, res, extra, skills, carrying (tragend), pool, exact,
        rejected, better_share (besser_anteil), candidates (kandidaten), verdict (urteil),
        rolls_used}]
    A section whose generator failed is {"error": "<type>: <message>"} (fehler); failures(raw) lists
    them and the report names each in E6 as a model limit (.68). generate/run take override=
    {section: fn(inp, profile)} to replace a generator (a forced failure in the tests).
"""
from __future__ import annotations

from .runner import (SECTIONS, GeneratorInput, GeneratorParams, buff_delta, constellations, failures, generate,
                     make_input, run)

__all__ = ["SECTIONS", "GeneratorInput", "GeneratorParams", "buff_delta", "constellations", "failures",
           "generate", "make_input", "run"]
