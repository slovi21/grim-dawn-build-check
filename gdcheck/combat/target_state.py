"""Target states for the action model: dummy, story boss, superboss (AP5a.4).

Resistances come from the monster record + the difficulty adjustment of the
game database (capped at 100). Boss DA, OA, armor and armor absorption come from
the game database (bio equations + passives + difficulty table, combatformulas.dbr)
at the monster level of the save (KG-03); without boss data the old HEURISTIC DA
(2500 / 3000) and armor 0 stay as fallback.
Your resistance reduction: type 1 from kept-up enemy debuffs (adds up), types
2/3 the strongest source; DA shred: kept-up '-X DA' debuffs add up plus the
strongest 'Reduced DA' on hit."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..data import benchmarks as BM
from ..data import gametables as GT
from ..data.bosses import combat_meta, life_leech_resistance
from ..data.bosses import load as load_bosses
from ..domain.enums import Activity

RES_TYPES = ("Physical", "Pierce", "Fire", "Cold", "Lightning", "Poison", "Life", "Aether",
             "Chaos", "Bleeding")
BOSS_NAME = {"Poison": "Acid", "Life": "Vitality"}
ELEMENTAL = ("Fire", "Cold", "Lightning")


@dataclass(frozen=True)
class TargetState:
    name: str
    res: dict = field(default_factory=dict)       # type -> % (Physical, Poison, Life ... names)
    da: float = 2000.0
    da_base: float = 2000.0
    rr: dict = field(default_factory=dict)        # type -> (t1, t2, t3)
    confidence: str = "HEURISTIC"
    armor: float = 0.0             # from the boss armor passive (armorbaseNN.defensiveProtection), else 0
    armor_absorption: float = 0.56  # 70% (gameengine.dbr) x (1 - 20%) Ultimate (difficulty table)
    llr: float = 0.0               # life leech resistance 0..1 (combat spec V2 5)
    llr_note: str = ""
    oa: float = 0.0                # boss OA (game database); 0 = unknown
    defense_note: str = ""
    # .72 (benchmark): which target this is and what a time measurement against it needs
    role: str = ""                 # "dummy" | "bench_boss" (Mogara) | "boss" (Loghorrean, Callagadra)
    level: int = 0                 # monster level (0 = unknown / heuristic)
    health: float = 0.0            # max health from the game database (0 = unknown); ASSUMED formula, health_note
    kill_share: float = 1.0        # share of the health to take (Training Dummy: shield at 15 % -> 0.85)
    health_note: str = ""


def _rr_keys(rf: str, kind: str) -> tuple:
    """The fields that reduce the resistance `rf`: kind 'Percent' (reduced) or 'Absolute' (flat) - the total one,
    the one of the type and, for the elements, the elemental one."""
    keys = [f"offensiveTotalResistanceReduction{kind}Min", f"offensive{rf}ResistanceReduction{kind}Min"]
    if rf in ELEMENTAL:
        keys.append(f"offensiveElementalResistanceReduction{kind}Min")
    return tuple(keys)


def _debuff_stack(snap, rf: str, kept_up: bool) -> float:
    """Type 1: the sum of the enemy debuffs (kept up or not) that lower `rf`."""
    t1 = 0.0
    for d in snap.enemy_debuffs:
        if bool(d.get("kept_up")) is kept_up:
            v = d["values"]
            t1 += -v.get(f"defensive{rf}", 0.0)
            if rf in ELEMENTAL:
                t1 += -v.get("defensiveElementalResistance", 0.0)
    return max(t1, 0.0)


def resistance_reduction(snap) -> dict:
    """{type: (stacking, reduced %, flat)} in force: kept-up enemy debuffs add up, 'reduced' and 'flat' take the
    strongest source on the sheet (types 2 and 3 of the game guide)."""
    f = snap.combat.fields
    return {rf: (_debuff_stack(snap, rf, True),
                 max(f.get(k, 0.0) for k in _rr_keys(rf, "Percent")),
                 max(f.get(k, 0.0) for k in _rr_keys(rf, "Absolute"))) for rf in RES_TYPES}


@dataclass(frozen=True)
class RRDetail:
    """The resistance reduction of one type: what is in force (`perm`, as resistance_reduction) and, per kind,
    the strongest source that exists but is not permanent (`weak`) with the reason (`why`)."""
    perm: tuple
    weak: tuple = (0.0, 0.0, 0.0)          # (stacking, reduced %, flat) - 0 = no such source
    why: tuple = ("", "", "")              # 'not kept up' | 'on chance' | 'one skill' | 'temporary' | ''
    sources: tuple = ()                    # readable lines for the detail table


# an effect that exists but works only now and then; ENEMY_DEBUFF effects are counted as debuffs of the snapshot
_WEAK = (Activity.PROC, Activity.SKILL_ONLY, Activity.TEMPORARY, Activity.FORM_ONLY)


def _weak_reason(act, source, chance_records) -> str:
    if act == Activity.PROC or (source is not None and source.record_id in chance_records):
        return "on chance"
    return "one skill" if act == Activity.SKILL_ONLY else "temporary"


def resistance_detail(snap, sources=None) -> dict:
    """{type: RRDetail}: the numbers in force are those of resistance_reduction (one source for the action
    model and the Damage row); the weak ones come from the debuffs that are not kept up and, with `sources`
    (SourceSet), from effects of procs, skills and temporary buffs (review 04.10.2026, item 2)."""
    rr = resistance_reduction(snap)
    effects = tuple(getattr(sources, "effects", ()) or ())
    chance = {p.skill_record for p in (getattr(sources, "procs", ()) or ())}
    prov = snap.combat.provenance
    out = {}
    for rf in RES_TYPES:
        weak, why, lines = [0.0, 0.0, 0.0], ["", "", ""], []
        debuffs = [d for d in snap.enemy_debuffs
                   if (-d["values"].get(f"defensive{rf}", 0.0)
                       - (d["values"].get("defensiveElementalResistance", 0.0) if rf in ELEMENTAL else 0.0)) > 0]
        lines += [f"{d['label']} (enemy debuff)" for d in debuffs if d.get("kept_up")]
        lines += [f"{d['label']} (enemy debuff, not kept up)" for d in debuffs if not d.get("kept_up")]
        weak[0] = _debuff_stack(snap, rf, False)
        why[0] = "not kept up" if weak[0] else ""
        for i, (word, kind) in enumerate((("reduced", "Percent"), ("flat", "Absolute")), start=1):
            keys = _rr_keys(rf, kind)
            best = max(((v, sid) for k in keys for sid, v in prov.get(k, [])), default=None)
            if best:                                                   # the permanent source named as before
                who = sources.source(best[1]) if sources is not None else None
                lines.append(f"{who.label if who else best[1]} ({word}, strongest)")
            top = max(((e.value, e) for e in effects if e.key in keys and e.activity in _WEAK),
                      key=lambda x: (x[0], x[1].source_id), default=None)
            if top and top[0] > 0:
                who = sources.source(top[1].source_id)
                weak[i] = top[0]
                why[i] = _weak_reason(top[1].activity, who, chance)
                lines.append(f"{who.label if who else top[1].source_id} ({word}, {why[i]})")
        out[rf] = RRDetail(rr[rf], tuple(weak), tuple(why), tuple(lines))
    return out


def da_shred(snap, sources) -> dict:
    stack, parts = 0.0, []
    for d in snap.enemy_debuffs:
        if d.get("kept_up"):
            v = -(d["values"].get("characterDefensiveAbility") or 0.0)
            if v > 0:
                stack += v
                parts.append(f"{d['label']} -{v:.0f}")
    reduced, who = 0.0, ""
    for sid, v in snap.combat.provenance.get("offensiveSlowDefensiveAbilityMin", []):
        if v > reduced:
            reduced, who = v, (sources.source(sid).label if sources.source(sid) else sid)
    if reduced:
        parts.append(f"{who} {reduced:.0f} reduced (on hit, strongest)")
    return {"total": stack + reduced, "stack": stack, "reduced": reduced, "parts": parts}


def boss_level(ctx) -> tuple[int, str]:
    """Area level the boss values start from: the player level (Ultimate scales the monsters to you;
    each boss then adds its record's charLevel equation, mechanics.monster.monster_level - KG-08, video
    03.10.2026: Loghorrean 103 at level 100); --boss-level sets the monster level itself. The greatest
    monsters killed in the save (Nemeses, Crucible, Shattered Realm) are not the open-world boss level.
    One source for LLR, boss damage and the combat check."""
    if ctx.options.boss_level:
        return int(ctx.options.boss_level), "set with --boss-level"
    return ctx.character.level, ("your level (Ultimate monsters scale to it; area caps of the map files are "
                                 "not in the game database) + the boss record's charLevel equation")


def boss_monster_level(ctx, meta: dict | None) -> int:
    """Monster level of one boss: boss_level(ctx) through the boss record's charLevel equation and
    minLevel/maxLevel (boss-combat data); --boss-level is taken as it is."""
    meta = meta or {}
    from ..mechanics import monster as MON
    if ctx.options.boss_level:
        return MON.monster_level(0, meta.get("minLevel"), meta.get("maxLevel"), int(ctx.options.boss_level))
    return MON.monster_level(ctx.character.level, meta.get("minLevel"), meta.get("maxLevel"),
                             char_level=meta.get("charLevel"))


def boss_level_basis(ctx, meta: dict | None) -> str:
    """'DERIVED' or 'ASSUMED; <why>' for boss_monster_level (mechanics.monster.level_basis) - the same word in
    the boss defense note (E2), the life leech note and E7."""
    from ..mechanics import monster as MON
    word, note = MON.level_basis((meta or {}).get("charLevel"), ctx.options.boss_level)
    return f"{word}; {note}" if note else word


HEURISTIC_DUMMY = "Dummy (0 res, DA 2000)"


def _benchmark_state(ctx, meta, key, entry, adj, rr, shred, label) -> TargetState | None:
    """One benchmark target from its boss-combat entry (monster level, armor, absorption, DA/OA, resistances,
    life leech resistance, health) - None when the formulas of the file do not fit (a patch changed them)."""
    from ..data.bosses import defense_profile_of
    lv = BM.monster_level_of(entry, ctx.character.level) if key == "dummy" else boss_monster_level(ctx, entry)
    dp = defense_profile_of(entry, meta, lv, adj, ctx.profile.value == "ascendant", label=label)
    if not dp:
        return None
    own = BM.own_defensive(entry, meta, lv)
    res = {rf: min(100.0, own.get(f"defensive{rf}", 0.0) + adj["res"].get(BOSS_NAME.get(rf, rf), 0.0))
           for rf in RES_TYPES}
    leech = own.get("defensiveSlowLifeLeach", 0.0) + adj.get("leech", 0.0)
    hp = BM.health_profile(entry, meta, lv, GT.monster_life_adjustment(ctx.index, 2), label=label)
    name = BM.label_of(ctx.index, key, entry).split(",")[0] if key != "dummy" else "Dummy"
    note = "; ".join(dp["parts"]) + f" (monster level {lv}, DERIVED from the record's charLevel equation; equations EXACT)"
    return TargetState(
        f"{name} (Ultimate, DA {dp['da']:,.0f})", res, max(0.0, dp["da"] - shred), dp["da"], rr, "DERIVED",
        dp["armor"], dp["absorption"], llr=min(100.0, leech) / 100,
        llr_note=f"{own.get('defensiveSlowLifeLeach', 0.0):g} own + {adj.get('leech', 0.0):g} difficulty, capped at 100",
        oa=dp["oa"], defense_note=note, role=key, level=lv, health=hp.get("health", 0.0),
        kill_share=BM.kill_share(entry),
        health_note=("; ".join(hp["parts"]) + " - formula ASSUMED (checked on community values for levels 50-85, "
                     "the passive's +% at higher levels is not)") if hp else "")


def benchmark_states(ctx, meta: dict, adj: dict, rr: dict, shred: float) -> list[TargetState]:
    """[Training Dummy, Mogara] from boss-combat `benchmarks` (schema 5); without them (an older file, other
    formulas) only the old HEURISTIC dummy - no health, no Mogara, so no benchmark (analysis.benchmark)."""
    from ..data.bosses import skill_label

    def lab(rec):
        return skill_label(ctx.index, rec)
    out = []
    for key in ("dummy", "bench_boss"):
        entry = BM.benchmark_entry(meta, key)
        st = _benchmark_state(ctx, meta, key, entry, adj, rr, shred, lab) if entry else None
        if st is None and key == "dummy":
            st = TargetState(HEURISTIC_DUMMY, {}, max(0.0, 2000 - shred), 2000, rr,
                             llr_note="training dummy: no life leech resistance", role="dummy")
        if st is not None:
            out.append(st)
    return out


def targets(ctx, snap, sources, base_path) -> list[TargetState]:
    profiles, _ = load_bosses(ctx.index, base_path)
    adj = GT.monster_adjustment(ctx.index, 2)
    rr = resistance_reduction(snap)
    shred = da_shred(snap, sources)["total"]
    raw_all = getattr(ctx.index, "bosse_roh", None) or {}
    from ..data import versioned as V
    ver, fp = V.index_identity(ctx.index)
    meta = combat_meta(base_path, ver or "1.3.0.8", fp)
    bench = benchmark_states(ctx, meta, adj, rr, shred)
    out = bench[:1]
    from ..data.bosses import defense_profile, skill_label

    def lab(rec):
        return skill_label(ctx.index, rec)
    for key, da in (("Loghorrean", 2500.0), ("Callagadra", 3000.0)):
        p = next((v for k, v in sorted(profiles.items()) if k.startswith(key)), None)
        if not p:
            continue
        cname = next((k for k in sorted(meta.get("bosses") or {}) if k.startswith(key)), None)
        bmeta = (meta.get("bosses") or {}).get(cname) or {}
        lv = boss_monster_level(ctx, bmeta)
        dp = defense_profile(cname, meta, lv, adj, ctx.profile.value == "ascendant", label=lab) if cname else {}
        armor, absorption, oa = 0.0, 0.56, 0.0
        dnote = "boss DA HEURISTIC, armor 0 (no boss data)"
        conf = "HEURISTIC"
        if dp:
            da, armor, absorption, oa = dp["da"], dp["armor"], dp["absorption"], dp["oa"]
            dnote = "; ".join(dp["parts"]) + f" (monster level {lv}, {boss_level_basis(ctx, bmeta)}; equations EXACT)"
            conf = "DERIVED"
        label = f"{key} (Ultimate, DA {da:,.0f})"
        res = {rf: min(100.0, p["res"].get(BOSS_NAME.get(rf, rf), 0.0) + adj["res"].get(BOSS_NAME.get(rf, rf), 0.0))
               for rf in RES_TYPES}
        name = next((k for k in sorted(raw_all) if k.startswith(key)), None)
        llr, note = 0.0, "unknown (no boss record in the index) - counted as 0"
        if name:
            lmeta = (meta.get("bosses") or {}).get(name)
            ll = life_leech_resistance(raw_all[name], lmeta,
                                       boss_level(ctx)[0], adj.get("leech", 0.0),
                                       int(ctx.options.boss_level) if ctx.options.boss_level else None,
                                       label=lab)
            llr = ll["value"] / 100
            note = (" + ".join(f"{n} {v:g}" for n, v in ll["parts"])
                    + f" (monster level {ll['monster_level']}, {boss_level_basis(ctx, lmeta)})")
        out.append(TargetState(label, res, max(0.0, da - shred), da, rr, conf, armor, absorption, llr=llr,
                               llr_note=note, oa=oa, defense_note=dnote, role="boss", level=lv))
    return out + bench[1:]
