"""Facts behind the Sustain and Damage rows of the Player View (.70, plan A.1).

What the player asked for: Sustain = own life leech, procs that save you at low health, a panic button;
Damage = the main damage types (above 900 % on the character sheet) and the resistance reduction that works
on them. All of it comes from database fields - no skill or item name is hard coded:

  skill / proc heal   skillLifeBonus, skillLifePercent (record and buff), characterHealIncreasePercent
  absorption          damageAbsorptionPercent, damageAbsorption (record and buff)
  low-health trigger  controller triggerParam (procs), lifeMonitorPercent (passives, Skill_PassiveOnLife*)
  cooldown            skillCooldownTime (record, else buff)

The fields are EXACT; the thresholds (5 % life leech, 20 % heal or absorption, at most 50 % Health for a
trigger, cooldown up to 120 s, 900 % main type, 2 of 3 kinds of resistance reduction) are decisions of the
user and the orchestrator (04.10.2026) and HEURISTIC - every one of them is shown in the detail table D11.
Celestial Powers bound to a skill count as low-health triggers (their controller names the threshold) but
never as a panic button: a bound power fires by chance. A power bound to a toggle that is switched off in
the save does nothing and counts for nothing (review 04.10.2026)."""
from __future__ import annotations

from dataclasses import dataclass

from ..combat import actions as ACT
from ..combat import sustain as SUS
from ..combat import target_state as TS
from ..domain import activity as ACTV
from ..domain.enums import Activity, Stage
from ..domain.fields import value_at
from ..mechanics import resist as RES
from ..mechanics import survival as SV
from ..mechanics.policy import Confidence, rule
from . import archetype as ARCH
from . import damage_types as DT
from . import stage as STG

MAIN_DAMAGE_PCT = 900.0        # a type above this (character sheet, no attribute share) is a main type
MAIN_TYPES_MAX = 3
LIFE_LEECH_MIN = 5.0           # own life leech that counts as sustain (% of attack damage)
PANIC_MIN_PCT = 20.0           # heal or absorption in % of the maximum Health
PANIC_MAX_COOLDOWN = 120.0     # s - a button you can press again within the fight
TRIGGER_MAX_PCT = 50.0         # a low-health trigger fires at most at this % of Health to count


@dataclass(frozen=True)
class SustainFacts:
    life_leech: float
    leech_sources: tuple       # ((label, %), ...) largest first
    breakers: tuple            # ((label, threshold % | None, "power" | "item" | "skill"), ...) earliest first
    panic: tuple               # ((label, "heal" | "absorb", % of max Health, cooldown s), ...) strongest first

    def count(self) -> int:
        """How many of the three sustain features the build has (own leech, trigger, panic button)."""
        return int(self.life_leech >= LIFE_LEECH_MIN) + int(bool(self.breakers)) + int(bool(self.panic))


KINDS_OK = 2.0                 # the kinds of resistance reduction (1 permanent, 1/2 weak) that make a type OK
KINDS_RISK = 0.5               # from this a type is a RISK, below it a GAP


def kinds_status(kinds: float) -> str:
    """'GAP' | 'RISK' | 'OK' of a main type with this sum of kinds of resistance reduction."""
    return "OK" if kinds >= KINDS_OK else "RISK" if kinds >= KINDS_RISK else "GAP"


@dataclass(frozen=True)
class MainType:
    name: str                  # damage name of the player ('Chaos', 'Acid', 'Burn')
    pct: float                 # character sheet value without the attribute share
    res: str                   # resistance type that works on it (Burn -> Fire)
    rr: tuple                  # (stacking, reduced %, flat) of the enemy resistance reduction in force
    rr_sources: tuple = ()     # readable sources for D11
    own: float = 0.0           # parts of pct for D11: own bonus, all damage, elemental
    everything: float = 0.0
    elemental: float = 0.0
    rr_weak: tuple = (0.0, 0.0, 0.0)    # per kind the strongest source that exists but is not permanent
    rr_why: tuple = ("", "", "")        # why it is not permanent ('not kept up', 'on chance', ...)

    def state(self, i: int) -> str:
        """Kind `i` (0 stacking, 1 reduced, 2 flat) of the resistance reduction: "full" when a permanent source works, "half"
        when one exists but is not permanent (debuff not kept up, chance, one skill), else "none"."""
        return "full" if self.rr[i] >= 1.0 else "half" if self.rr_weak[i] >= 1.0 else "none"

    def kinds(self) -> float:
        """How many of the three kinds of resistance reduction work on this type (they stack): a permanent
        one counts 1, one that exists but is not permanent (debuff not kept up, chance, one skill) counts 1/2."""
        return sum({"full": 1.0, "half": 0.5, "none": 0.0}[self.state(i)] for i in range(3))


RR_WEIGHTS = (0.5, 0.3, 0.2)   # stacking, reduced, flat: they stack; the stacking one is the strongest (user 04.10.2026)


def rr_weighted(perm, weak) -> float:
    """The weighted kinds of resistance reduction, 0..1, from the permanent values (stacking, reduced %, flat) and the
    strongest values that exist but are not permanent: 0.5 / 0.3 / 0.2, a kind that is not permanent counts half."""
    return sum(w * (1.0 if now >= 1.0 else 0.5 if weak_ >= 1.0 else 0.0)
               for w, now, weak_ in zip(RR_WEIGHTS, perm, weak))


def rr_score(m: "MainType") -> float:
    """Resistance reduction of a main type for the score target 'rr_main', 0..1: the three kinds weighted
    0.5 / 0.3 / 0.2, a kind that exists but is not permanent counts half (N3, user 04.10.2026)."""
    return rr_weighted(m.rr, m.rr_weak)


@dataclass(frozen=True)
class AreaFacts:
    resist_share: float | None
    sustain: SustainFacts | None
    main_types: tuple
    early: bool = False
    pet: bool = False          # pet build: its damage comes from the pets, not from the sheet (E4)


# -- low-health triggers, panic buttons ---------------------------------------------------------

def _short(label: str) -> str:
    """'Last Stand (Ring of Doom, Ring 1)' -> 'Last Stand'."""
    return str(label).split(" (")[0]


def skill_cooldown(rec, buff, level) -> float:
    """Cooldown in s of a skill: its own record, else its buff record (0 = none)."""
    for r in (rec or {}, buff or {}):
        cd = value_at((r.get("f") or r).get("skillCooldownTime"), level)
        if cd:
            return float(cd)
    return 0.0


@rule("panic_button", Confidence.HEURISTIC,
      "a skill you press on purpose: cooldown up to 120 s and a heal of 20 % of the maximum Health or more, or "
      "damageAbsorptionPercent 20 or more, or flat damageAbsorption of 20 % of the maximum Health or more "
      "(skillLifeBonus / skillLifePercent / damageAbsorption* / skillCooldownTime are database fields, "
      "the thresholds are a decision of the user 04.10.2026)")
def panic_kind(rec: dict, buff: dict | None, level: int, max_health: float,
               heal_increase: float) -> tuple[str, float] | None:
    """('heal' | 'absorb', % of the maximum Health) when the skill is a panic button, else None."""
    cd = skill_cooldown(rec, buff, level)
    if not 0 < cd <= PANIC_MAX_COOLDOWN or max_health <= 0:
        return None
    flat, pct = SUS.heal_values(rec, buff, level)
    heal = SV.heal_amount(flat, pct, max_health, heal_increase) / max_health * 100
    percent = flat = 0.0                       # one shield written two ways: the larger counts, not the sum
    for r in (rec or {}, buff or {}):
        r = r.get("f") or r
        percent += float(value_at(r.get("damageAbsorptionPercent"), level) or 0.0)
        flat += float(value_at(r.get("damageAbsorption"), level) or 0.0) / max_health * 100
    absorb = max(percent, flat)
    best = max((heal, "heal"), (absorb, "absorb"), key=lambda x: (x[0], x[1] == "heal"))
    return (best[1], best[0]) if best[0] >= PANIC_MIN_PCT else None


def _switched_off(ctx, sources) -> set:
    """Records of toggles that are switched off in the save (skill_active False): a power bound to one of them
    does nothing. A shapeshift has no on/off state of its own (check E6 skips it as well)."""
    off = {e["skill_name"] for e in ctx.character.skill_entries if not e.get("skill_active")}
    return {st.record for st in sources.skills
            if st.activity == Activity.TOGGLE and st.klass != "Skill_Shapeshift" and st.record in off}


def _breakers(ctx, sources) -> tuple:
    found = {}
    off = _switched_off(ctx, sources)
    for p in sources.procs:
        if p.trigger == "LowHealth" and p.bound_to not in off:
            found[(_short(p.label), p.trigger_param, "power" if p.bound_to else "item")] = p.skill_record
    for st in sources.skills:
        if st.points >= 1 and st.klass.startswith("Skill_PassiveOnLife"):
            thr = value_at((ctx.index.record(st.record) or {}).get("lifeMonitorPercent"), st.level)
            found[(_short(st.label), thr, "skill")] = st.record
    keep = [k for k in found if k[1] is None or 0 < k[1] <= TRIGGER_MAX_PCT]
    return tuple(sorted(keep, key=lambda k: (k[1] is None, -(k[1] or 0.0), k[0], k[2]))), set(found.values())


def _panic(ctx, sources, snap, trigger_records) -> tuple:
    idx, hot = ctx.index, ctx.character.hotbar
    max_hp = float(snap.combat.abilities["health"])
    inc = float(snap.combat.fields.get("characterHealIncreasePercent", 0.0))
    cands = [(st.label, st.record, st.level) for st in sources.skills
             if st.points >= 1 and not st.parent and st.record in hot
             and st.activity not in (Activity.PERMANENT, Activity.TOGGLE) and not st.klass.startswith("Skill_PassiveOn")]
    cands += [(g.label, g.skill_record, g.level) for g in sources.granted
              if g.activity == Activity.SKILL_ONLY and g.skill_record in hot]
    found = {}
    for label, record, level in cands:
        if record in trigger_records:
            continue
        rec = idx.record(record) or {}
        buff = ACTV.buff_record(rec, idx)
        kind = panic_kind(rec, buff, level, max_hp, inc)
        if kind:
            found[(_short(label), kind[0], kind[1], skill_cooldown(rec, buff, level))] = None
    return tuple(sorted(found, key=lambda k: (-k[2], k[0])))


@rule("area_sustain", Confidence.HEURISTIC,
      "Sustain = own life leech (5 % or more), a low-health trigger (at most 50 % Health) and a panic button; "
      "the three features are database facts, the counting and the thresholds are a decision of the user "
      "04.10.2026")
def sustain_facts(ctx, sources, snap) -> SustainFacts:
    prov = snap.combat.provenance.get("offensiveLifeLeechMin", [])        # the same sum as the E7 note
    leech = sorted(((sources.source(sid).label if sources.source(sid) else str(sid), float(v)) for sid, v in prov),
                   key=lambda x: (-x[1], x[0]))
    breakers, trigger_records = _breakers(ctx, sources)
    return SustainFacts(sum(v for _l, v in leech), tuple(leech), breakers,
                        _panic(ctx, sources, snap, trigger_records))


# -- main damage types ----------------------------------------------------------------------------

@rule("area_damage", Confidence.HEURISTIC,
      "main damage type = a type above 900 % on the character sheet (own bonus, all damage, elemental; no "
      "attribute share - the sheet does not show it), at most three, with its resistance reduction of the three "
      "kinds (stacking, reduced, flat); a kind that exists but is not permanent counts half (review 04.10.2026); "
      "decision of the user 04.10.2026")
def main_types(snap, sources=None) -> tuple:
    detail = TS.resistance_detail(snap, sources)
    none = TS.RRDetail((0.0, 0.0, 0.0))
    out = []
    for e in DT.damage_percentages(snap.sheet.fields):
        if e["summe"] <= MAIN_DAMAGE_PCT or not (e["eigen"] > 0 or e["elementar"] > 0):
            continue
        res = ACT.RES_OF.get(e["typ"], e["typ"])
        rr = detail.get(res, none)
        out.append(MainType(e["name"], float(e["summe"]), res, tuple(rr.perm), rr.sources, float(e["eigen"]),
                            float(e["alle"]), float(e["elementar"]), rr.weak, rr.why))
    return tuple(out[:MAIN_TYPES_MAX])


# -- resistances ---------------------------------------------------------------------------------------

def resist_share(ctx, snap, stage: Stage) -> float:
    """Sum over the nine types of min(max(value, 0), target) / sum of the targets. EARLY and MID: the 80 % on
    Ultimate (the value rounded, as the status counts it); ENDGAME: the own cap of each type."""
    if stage == Stage.ENDGAME:
        pairs = [(snap.sheet.resists[n]["value"], snap.sheet.resists[n]["cap"]) for n in RES.RESISTS]
    else:
        pairs = [(round(r["value"]), r["target"]) for r in STG.ultimate_resists(ctx, snap).values()]
    total = sum(z for _v, z in pairs)
    return sum(min(max(v, 0.0), z) for v, z in pairs) / total if total else 1.0


def area_facts(ctx, sources, snap, stage: Stage) -> AreaFacts:
    return AreaFacts(resist_share(ctx, snap, stage), sustain_facts(ctx, sources, snap),
                     main_types(snap, sources), stage == Stage.EARLY, ARCH.is_pet(ctx))


# -- inputs of the score targets -----------------------------------------------------------------------------

def target_inputs(facts) -> dict:
    """The value of the target 'rr_main' (analysis.targets 'extra'): (type, score, permanent kinds, kinds that
    are not permanent) of the main type with the lowest score (the larger one at a tie). Pet build, no facts:
    None - the target is then info and does not count. A build without a main damage type (it deals no damage
    above 900%) has measured nothing to reduce: "rr_no_main", the target is a gap with credit 0 (I3)."""
    main = tuple(getattr(facts, "main_types", ()) or ())
    if facts is None or getattr(facts, "pet", False) or getattr(facts, "early", False):
        return {"rr_kinds": None}
    if not main:
        return {"rr_kinds": None, "rr_no_main": True}
    m = min(main, key=lambda x: (rr_score(x), -x.pct))
    perm = sum(1 for now in m.rr if now >= 1.0)
    weak = sum(1 for now, w in zip(m.rr, m.rr_weak) if now < 1.0 <= w)
    return {"rr_kinds": (m.name, rr_score(m), perm, weak)}
