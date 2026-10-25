"""Damage per action (AP5a) - expected values per hit, no time model (ADR-4).

Per skill on the hotbar (attacks, spells, form abilities, default attack):
  1. flat damage: all combat sources except the other hand's weapon, x %WD of
     the skill (skill %WD + %WD of its modifiers: skill modifiers with points
     and item modifiers - every instance counts, two Cleavers = +20%), plus the
     skill's own flat damage and that of its modifiers at their rank;
  2. conversion: skill first, then global on the rest (never twice);
  3. % bonuses of the type (incl. attribute share) + local % of the skill,
     x total damage multiplier, x Onslaught multiplier for cooldown skills;
  4. per target: resistance after the RR chain, PtH/crit vs the target DA.
Form abilities hit with BOTH weapons (TESTED, golden 30.09.2026).
Weapon '% Armor Piercing': last conversion Physical -> Pierce per hand (TESTED).
Not included (stated in the report): DoT conversion,
WPS mix, pets, retaliation. Procs' own damage: proc_actions() (KG-07).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import Activity
from ..domain.fields import conversions, leveled_fields, value_at
from ..domain import activity as ACT
from ..mechanics import armor as ARM
from ..mechanics import hit as HIT
from ..mechanics import leech as LEECH
from ..mechanics import onslaught as ONS
from ..mechanics import resist as RES
from ..mechanics.conversion import armor_piercing, convert
from ..mechanics.policy import Confidence, weakest
from ..stats import damage_mods as DM
from . import events as E

DIRECT = ("Physical", "Pierce", "Fire", "Cold", "Lightning", "Poison", "Life", "Aether", "Chaos")
DOT = {"Physical": "InternalTrauma", "Bleeding": "Bleeding", "Fire": "Burn", "Cold": "Frostburn",
       "Lightning": "Electrocute", "Poison": "PoisonDot", "Life": "VitalityDecay"}
RES_OF = {"Physical": "Physical", "InternalTrauma": "Physical", "Pierce": "Pierce", "Fire": "Fire",
          "Burn": "Fire", "Cold": "Cold", "Frostburn": "Cold", "Lightning": "Lightning",
          "Electrocute": "Lightning", "Poison": "Poison", "PoisonDot": "Poison", "Life": "Life",
          "VitalityDecay": "Life", "Aether": "Aether", "Chaos": "Chaos", "Bleeding": "Bleeding"}
CONV_NAME = {"Vitality": "Life", "Acid": "Poison"}
ELEMENTAL = ("Fire", "Cold", "Lightning")


def action_confidence(conversion=(), onslaught: bool = False, double_hit: bool = False, finale: bool = False,
                      armor_piercing: bool = False, proc: bool = False, vs_target: bool = False) -> str:
    """X-009: confidence of one action = the weakest rule of its damage chain - the conversion step
    (EXACT, TESTED for a pro-rata split of global conversions), Onslaught stacks (ASSUMED until an in-game
    golden at 3/6 stacks, requirements K5), both weapons of a form ability, a charged finale, weapon
    armor piercing and the expected hit against a target (PtH/crit vs its DA - the vs-target cells; TESTED),
    a proc's own damage (DERIVED)."""
    levels = [Confidence(c) for c in conversion if c]
    if onslaught:
        levels.append(Confidence.ASSUMED)
    if double_hit or finale or armor_piercing or vs_target:
        levels.append(Confidence.TESTED)
    if proc:
        levels.append(Confidence.DERIVED)
    return weakest(*levels).value


@dataclass(frozen=True)
class TargetResult:
    target: str
    expected_direct: float
    expected_dot: float
    pth: float
    hit_factor: float


@dataclass(frozen=True)
class ActionResult:
    action_id: str
    label: str
    kind: str                       # attack | spell
    rank: int
    wd_pct: float
    wd_base: float
    wd_mod: float
    double_hit: bool
    hits_per_action: int
    onslaught: int                  # stacks applied (0 for non-spenders)
    onslaught_max: int
    spender: bool
    direct: float                   # per action, no crit, no target ("sheet")
    dot: float                      # DoT per second at full uptime
    parts: dict                     # type -> damage (direct + dot) on the sheet
    targets: tuple                  # TargetResult per target state
    speed: float                    # attack or cast speed % (comparison index, no seconds)
    proc_candidates: tuple          # labels of procs that roll on this action
    assumptions: tuple
    confidence: str
    events: tuple = ()              # DamageEvents: sheet + per target, main/off hand
    heals: tuple = ()               # HealingEvents (life steal), filled by combat.leech
    cooldown: float | None = None   # effective cooldown in s (after cooldown reduction)
    proc: object = None             # ProcInstance when this result is a proc's own damage (KG-07)
    dot_duration: float = 0.0       # longest DoT duration in s (proc DoT uptime)
    cooldown_refresh: tuple = ()    # (label, chance 0..1, seconds, trigger[, skills that must be used]):
                                    # procs that cut this cooldown (combat.dps.refresh_terms)
    chain: tuple = ()               # records of the skill and its modifiers with points (refresh triggers)


def _avg(f, t, slow=False):
    pre = "offensiveSlow" if slow else "offensive"
    mn = f.get(f"{pre}{t}Min", 0.0) or 0.0
    mx = f.get(f"{pre}{t}Max", 0.0) or 0.0
    if slow:
        return mn if (f.get(f"{pre}{t}DurationMin", 0.0) or 0.0) > 0 else 0.0
    return (mn + mx) / 2 if mx else mn


def atoms(f: dict) -> tuple[dict, dict]:
    direct = {t: _avg(f, t) for t in DIRECT}
    el = _avg(f, "Elemental")
    for t in ELEMENTAL:
        direct[t] += el / 3
    dot = {DOT[t]: _avg(f, t, slow=True) for t in DOT}
    return ({k: v for k, v in direct.items() if v > 0}, {k: v for k, v in dot.items() if v > 0})


def _add(*ds, factor=1.0):
    out: dict = {}
    for d in ds:
        for k, v in d.items():
            out[k] = out.get(k, 0.0) + v * factor
    return out


def _minus(a, b):
    return {k: a.get(k, 0.0) - b.get(k, 0.0) for k in set(a) | set(b)}


def _rules(conv):
    out = []
    for src, dst, pct in conv:
        out.append((CONV_NAME.get(src, src), CONV_NAME.get(dst, dst), float(pct)))
    return out


def _flat_basis(values, sources):
    """(main-hand basis, off-hand basis): flat damage of all combat sources, without
    the other hand's weapon; DoT per source (damage/s x own duration rule)."""
    f = values.fields
    direct_all, _ = atoms(f)
    per_source: dict = {}
    for key, contribs in values.provenance.items():
        if not key.startswith("offensiveSlow"):
            continue
        for sid, v in contribs:
            per_source.setdefault(sid, {})[key] = per_source.setdefault(sid, {}).get(key, 0.0) + v
    hand = {}
    for e in sources.effects:
        if e.scope:
            hand.setdefault(e.scope, {})
            hand[e.scope][e.key] = hand[e.scope].get(e.key, 0.0) + e.value
    oh_d, oh_t = atoms(hand.get("weapon:Off hand", {}))
    mh_d, _ = atoms(hand.get("weapon:Main hand", {}))
    dot_all = _add(*[atoms(v)[1] for v in per_source.values()])
    has_off_weapon = bool(hand.get("weapon:Off hand")) and any(
        hand["weapon:Off hand"].get(f"offensive{t}Min") for t in ("Physical", "Pierce"))
    return (_minus(direct_all, oh_d), _minus(dot_all, oh_t)), (_minus(direct_all, mh_d), {}), has_off_weapon


def _skill_records(rec_name, states, sources, idx, rank):
    """[(record, fields, conversions, wd)] of a skill, its modifiers with points and
    the item modifiers on it (rank 1)."""
    out = []
    for r in (idx.record(rec_name), ACT.buff_record(idx.record(rec_name) or {}, idx)):
        if r:
            out.append((r, leveled_fields(r, max(rank, 1)), conversions(r, max(rank, 1)), 0.0))
    for st in states:
        if st.parent == rec_name and st.points > 0 and st.record != rec_name:
            r = idx.record(st.record) or {}
            out.append((r, leveled_fields(r, st.level), conversions(r, st.level),
                        float(value_at(r.get("weaponDamagePct"), st.level) or 0.0)))
    for m in sources.modifiers:
        if m.target_skill == rec_name:
            out.append(({}, dict(m.fields), list(m.conversions), m.wd_pct))
    return out


LIFE_STEAL = "offensiveLifeLeechMin"     # % Attack Damage Converted to Health


NO_OWN_DAMAGE = ("Skill_BuffSelf", "Skill_SpawnPet")


def _proc_hits(rec) -> bool:
    """A proc deals its own damage unless it is a self buff or a pet summon without a weapon strike (.67):
    the offensive fields of Amatok's Grasp / Ulzaad's Decree / Sineater (Skill_BuffSelf*) are added to YOUR
    attacks while the buff runs, Living Shadow (Skill_SpawnPet) summons a pet - E7 showed 'Amatok's Grasp
    0.08/s x 0 = 0 DpS' and counted Ulzaad's Decree's flat Physical as a hit of its own."""
    rec = rec or {}
    if not str(rec.get("Class", "")).startswith(NO_OWN_DAMAGE):
        return True
    return float(value_at(rec.get("weaponDamagePct"), 1) or 0.0) > 0


def _debuff_attack(st) -> bool:
    """Attack skill whose damage AND enemy debuff sit in its buff record (Skill_Attack*Debuf: Devouring Swarm,
    Blade Trap - the skill record holds only Class + buffSkillName). The loader keeps it an enemy debuff (its
    -Res applies to the target); it is an action too (.67, Cpt Piercewolf: E2 had no Devouring Swarm)."""
    return st.activity == Activity.ENEMY_DEBUFF and st.klass.startswith("Skill_Attack") and "Debuf" in st.klass


def _local_life_steal(recs) -> float:
    """Skill life steal of the action: the field on the skill, its buff record and its
    modifiers (fraction). These records are SKILL_ONLY - never on the sheet, so the
    global value from the snapshot cannot contain them (no double count)."""
    return sum(float(f.get(LIFE_STEAL, 0.0) or 0.0) for _r, f, _c, _w in recs) / 100


def _heals(events, wd, global_rate, local_rate, llr_by_target) -> list:
    """One HealingEvent per damage event with direct damage (combat spec V2 6)."""
    out = []
    for e in events:
        if not e.has_direct_damage or (global_rate <= 0 and local_rate <= 0):
            continue
        llr = llr_by_target.get(e.target, 0.0) if e.target else 0.0
        h = LEECH.heal(e.direct, wd, e.weapon_attack, global_rate, local_rate, llr)
        out.append(E.HealingEvent(e.action_id, e.hand, e.target, e.direct, h["global_rate"],
                                  h["local_rate"], llr, h["heal_global"], h["heal_local"], h["raw"],
                                  "gear/buff life steal x min(1, %WD/100) + skill life steal, "
                                  "direct damage only, after target Life Leech Resistance"))
    return out


def _refresh_table(ctx) -> dict:
    """refreshCooldown* records of the game data (references/player-skills, data.player_skills)."""
    from ..data import player_skills as PS
    fp = str((getattr(getattr(ctx, "index", None), "meta", None) or {}).get("fingerprint") or "")
    return PS.cooldown_refresh(getattr(ctx, "game_version", None) or "1.3.0.8", fp)


def _refresh_via(target_skill, idx) -> tuple:
    """Skills whose use rolls an item skill modifier's cooldown refresh: () = any attack when the modifier
    sits on a buff, aura, toggle or passive (Field Command -> Ring of Steel); else the modified skill itself
    (Canister Bomb 'Grenado -2 s' only on Canister Bomb uses)."""
    cls = str((idx.record(target_skill) or {}).get("Class", "")) if target_skill else ""
    if ACT.class_activity(cls) in (Activity.PERMANENT, Activity.TOGGLE, Activity.TEMPORARY):
        return ()
    return (target_skill,) if target_skill else ()


def _cooldown_refresh(rec_name, states, sources, idx, table) -> tuple:
    """(label, chance 0..1, seconds, trigger[, via]) of every source that reduces the cooldown of `rec_name`:
    skill modifiers/transmuters with points at their rank (Recklessness -> Rip and Tear) and item skill
    modifiers (rank 1, every instance counts; `via` = the skills whose use rolls it, _refresh_via) - game
    database (refreshCooldown*)."""
    out = []
    for st in states:
        e = table.get(st.record)
        if e and e["skill"] == rec_name and st.points > 0:
            ch = float(value_at(e["chance"], max(st.level, 1)) or 0.0)
            if ch > 0:
                out.append((st.label, ch / 100, float(e["amount"]), e["trigger"]))
    for m in sources.modifiers:
        e = table.get(m.modifier_record)
        if e and e["skill"] == rec_name:
            ch = float(value_at(e["chance"], 1) or 0.0)
            if ch > 0:
                src = sources.source(m.source_id) if hasattr(sources, "source") else None
                label = getattr(src, "label", "") or "item skill modifier"
                via = _refresh_via(m.target_skill, idx)
                out.append((label, ch / 100, float(e["amount"]), e["trigger"]) + ((via,) if via else ()))
    return tuple(out)


def _onslaught_max(rec_name, states, sources, idx) -> int:
    """Stacks a generator builds: own skillComboChargeLevel + all instances on it."""
    best = 0
    for st in states:
        if st.points < 1 and not st.form:
            continue
        r = idx.record(st.record) or {}
        own = value_at(r.get("skillComboChargeLevel"), max(st.level, 1)) or 0.0
        mods = [m.combo_level for m in sources.modifiers if m.target_skill == st.record]
        total = ONS.onslaught_max([own] + mods)
        best = max(best, total)
    return best


def _pierce_ratio(ctx, idx) -> dict:
    """% Armor Piercing per hand from the weapon record in that hand (active weapon set)."""
    out = {}
    for w in ctx.character.worn:
        if w.slot in ("Main hand", "Off hand"):
            r = idx.record(w.item.get("basename", "")) or {}
            f = r.get("f") or r
            out["main" if w.slot == "Main hand" else "off"] = float(f.get("offensivePierceRatioMin") or 0.0)
    return out


DOT_TYPES = ("Bleeding", "Cold", "Fire", "Lightning", "Life", "Physical", "Poison")


def _dot_duration(recs) -> float:
    """Longest duration of a DAMAGE over time of the skill records (review .51: slows, DA/OA
    reductions and damage-mult debuffs also have offensiveSlow*DurationMin - not a DoT)."""
    keys = {f"offensiveSlow{t}DurationMin" for t in DOT_TYPES}
    return max([float(v or 0.0) for _r, f, _c, _w in recs for k, v in f.items() if k in keys] or [0.0])


def _finale_mix(finale, plain, n: int):
    """Average strike of a charged-finale skill: 1 finale and n normal strikes per cycle."""
    wf, wn = 1.0 / (n + 1), n / (n + 1)

    def mix(a, b):
        return {t: a.get(t, 0.0) * wf + b.get(t, 0.0) * wn for t in set(a) | set(b)
                if a.get(t, 0.0) * wf + b.get(t, 0.0) * wn}
    return E.DamageEvent(finale.action_id, finale.hand, finale.target, finale.wd_pct, finale.weapon_attack,
                         mix(finale.direct_by_type, plain.direct_by_type), mix(finale.dot_by_type, plain.dot_by_type),
                         finale.pre_hit_direct * wf + plain.pre_hit_direct * wn, finale.pth, finale.hit_factor,
                         finale.miss_share, finale.crit_share, finale.onslaught,
                         finale.rules + (f"finale_every_{n + 1}",))


def proc_actions(ctx, sources, snap, targets, values=None) -> list[ActionResult]:
    """Own damage of every attack-triggered proc (devotion celestial powers, item procs)
    as an action of its own - same damage chain as the skills (KG-07). The rate is
    applied in combat.dps.rotation; HitByEnemy procs and pets are not modelled."""
    return damage_per_action(ctx, sources, snap, targets, 0, values, procs_only=True)


def damage_per_action(ctx, sources, snap, targets, onslaught: int | None = None,
                      values=None, procs_only: bool = False) -> list[ActionResult]:
    """`values`: the State to compute with (default: snapshot 'combat' = STEADY); PURGED and
    CIRCUIT_BREAKER pass their own values so damage and life steal are recalculated."""
    idx = ctx.index
    states = list(sources.skills)
    combat = values or snap.combat
    per_type = {t: v["total"] for t, v in combat.damage.items()}
    mult = 1 + combat.fields.get("offensiveDamageMultModifier", 0.0) / 100
    chd = combat.fields.get("offensiveCritDamageModifier", 0.0) / 100
    oa = combat.abilities["oa"]
    basis_mh, basis_oh, two_weapons = _flat_basis(combat, sources)
    global_conv = _rules([(c.from_type, c.to_type, c.percent) for c in sources.conversions
                          if c.stage == "global" and c.activity in (Activity.PERMANENT, Activity.TOGGLE)])
    n_max = _onslaught_max(None, states, sources, idx)
    global_ll = combat.fields.get(LIFE_STEAL, 0.0) / 100
    cdr = min(max(combat.fields.get("skillCooldownReduction", 0.0), 0.0), 100.0)
    pierce_ratio = _pierce_ratio(ctx, idx)
    refresh_table = _refresh_table(ctx) if not procs_only else {}
    results = []
    hotbar = ctx.character.hotbar
    from ..domain.sources import SkillState
    granted = [SkillState(g.skill_record, g.label.split(" (")[0], 1, g.level, Activity.SKILL_ONLY,
                          "item skill", str((idx.record(g.skill_record) or {}).get("Class", "")),
                          g.skill_record in hotbar, None, None, g.skill_record)
               for g in sources.granted if g.activity == Activity.SKILL_ONLY and g.skill_record in hotbar]
    seen = set()
    if procs_only:
        entries = [(SkillState(p.skill_record, p.label, 1, p.level, Activity.SKILL_ONLY, "proc", "", False,
                               None, None, p.skill_record), p)
                   for p in sorted(sources.procs, key=lambda p: (p.label, p.id))
                   if p.trigger.startswith("AttackEnemy") and _proc_hits(idx.record(p.skill_record))]
    else:
        entries = [(st, None) for st in states + granted]
    for st, proc in entries:
        key = proc.id if proc else st.record
        if key in seen:
            continue
        seen.add(key)
        rn = st.record
        default = "defaultweaponattack" in rn
        if proc is None:
            if st.parent is not None:
                continue
            if rn not in hotbar and not st.form and not default:
                continue
            if st.form and st.form not in hotbar and rn not in hotbar:
                continue
            if not default and st.activity != Activity.SKILL_ONLY and not _debuff_attack(st):
                continue
        rec = idx.record(rn) or {}
        rank = max(st.level, 1)
        wd_base = float(value_at(rec.get("weaponDamagePct"), rank) or 0.0)
        recs = _skill_records(rn, states, sources, idx, rank)
        wd_mod = sum(w for _, _, _, w in recs)
        wd = wd_base + (wd_mod if wd_base else 0.0)
        wd_skill = wd
        cooldown = None if proc else value_at(rec.get("skillCooldownTime"), rank)
        spender = ONS.is_spender(cooldown)
        refresh = _cooldown_refresh(rn, states, sources, idx, refresh_table) if cooldown else ()
        stacks = 0
        if spender and n_max and onslaught:
            stacks = max(0, min(int(onslaught), n_max))
        o_mult = ONS.onslaught_multiplier(stacks, ctx.ruleset.get("onslaught_per_stack", 0.15))
        double = two_weapons and wd > 0 and bool(st.form) and proc is None

        finale_n = 0
        if proc is None and "ChargedFinale" in str(rec.get("Class", "")):
            finale_n = int(value_at(rec.get("skillChargeLevel"), rank) or 0)

        conv_conf: list = []                         # X-009 (.61): confidence of the conversion step
        pierced: list = []                           # weapon armor piercing moved damage (TESTED rule)

        def one(basis, target, hand, plain=False):
            """One hit event of this action with one weapon basis (E10: the skill's own
            flat damage is part of every event; DoT only on the first event). plain = a normal
            weapon strike (100% WD, no skill records) of a charged-finale skill such as Cadence."""
            wd = 100.0 if plain else wd_skill
            direct, dot, local = {}, {}, {}
            skill_conv = []
            for _r, f, conv, _w in ([] if plain else recs):
                d, t = atoms(f)
                direct, dot = _add(direct, d), _add(dot, t)
                for typ, (field_, _k, _n) in DM.TYPES.items():
                    if f.get(field_):
                        local[typ] = local.get(typ, 0.0) + f[field_]
                if f.get("offensiveTotalDamageModifier"):
                    for typ in DM.TYPES:
                        local[typ] = local.get(typ, 0.0) + f["offensiveTotalDamageModifier"]
                skill_conv += _rules(conv)
            if wd:
                direct = _add(direct, {k: v * wd / 100 for k, v in basis[0].items()})
                dot = _add(dot, {k: v * wd / 100 for k, v in basis[1].items()})
            if hand == "off":
                dot = {}
            direct, _cconf = convert(direct, skill_conv, global_conv)
            conv_conf.append(_cconf)
            if wd:                       # weapon armor piercing: last, on the remaining Physical
                before = direct
                direct = armor_piercing(direct, pierce_ratio.get("off" if hand == "off" else "main", 0.0))
                if direct != before:
                    pierced.append(True)
            p = HIT.pth(oa, target.da) if target else 0.0
            hx = HIT.expected_hit(p, chd) if target else {"factor": 1.0, "hit": 1.0, "crit": 0.0}
            hitf = hx["factor"]
            dotf = HIT.dot_factor(hx) if target else 1.0
            d_out, t_out = {}, {}
            pre_hit = 0.0
            for typ, amount in list(direct.items()) + list(dot.items()):
                pct = per_type.get(typ, 0.0) + local.get(typ, 0.0)
                val = amount * (1 + pct / 100) * mult * o_mult
                is_dot = typ in dot
                if target:
                    rf = RES_OF.get(typ, typ)
                    t1, t2, t3 = target.rr.get(rf, (0.0, 0.0, 0.0))
                    res = RES.rr_chain(target.res.get(rf, 0.0), [t1], [t2], [t3])
                    mitigated = val * RES.res_factor(res)
                    if typ == "Physical" and not is_dot and getattr(target, "armor", 0.0):
                        mitigated = ARM.armor_hit(mitigated, target.armor, target.armor_absorption)
                    if not is_dot:
                        pre_hit += mitigated
                    arr = mitigated * (dotf if is_dot else hitf)
                else:
                    arr = val
                    if not is_dot:
                        pre_hit += val
                (t_out if is_dot else d_out)[typ] = (t_out if is_dot else d_out).get(typ, 0.0) + arr
            return E.DamageEvent(
                rn, hand if wd else "spell", target.name if target else None, wd, bool(wd),
                d_out, t_out, pre_hit, p, hitf, HIT.miss_share(p) if target else 0.0, hx["crit"], stacks,
                ("form_double_hit",) if double else ())

        def hit(basis, target, hand):
            ev = one(basis, target, hand)
            return _finale_mix(ev, one(basis, target, hand, plain=True), finale_n) if finale_n else ev

        events = [hit(basis_mh, None, "main")]
        if double:
            events.append(hit(basis_oh, None, "off"))
        raw_d = sum(e.direct for e in events)
        raw_t = sum(e.dot for e in events)
        parts: dict = {}
        for e_ in events:
            parts = _add(parts, e_.direct_by_type, e_.dot_by_type)
        if raw_d <= 0 and raw_t <= 0:
            continue
        tres = []
        for tg in targets:
            tev = [hit(basis_mh, tg, "main")] + ([hit(basis_oh, tg, "off")] if double else [])
            events += tev
            tres.append(TargetResult(tg.name, sum(e.direct for e in tev), sum(e.dot for e in tev),
                                     tev[0].pth, tev[0].hit_factor))
        heals = _heals(events, wd, global_ll, _local_life_steal(recs), {t.name: t.llr for t in targets})
        attack = bool(wd)
        sp = combat.speeds["attack" if attack else "cast"]
        procs = () if proc else tuple(sorted(p.label for p in sources.procs if (p.bound_to == rn) or (
            p.bound_to is None and p.trigger.startswith("AttackEnemy"))))
        assumptions = ["per action; per-second values only in the combat check (action rate from animation frames)",
                       "DoT per second at full uptime"]
        if proc:
            assumptions = ["proc damage per proc; rate in the rotation (proc formula, ESTIMATE)",
                           "proc with %WD uses the main-hand weapon basis (ASSUMED)",
                           "gear life steal x min(1, %WD/100) only if the proc has %WD (Game Guide rule, "
                           "DERIVED for procs)"]
        if stacks:
            assumptions.append(f"Onslaught {stacks}/{n_max} stacks x{o_mult:.2f} as own multiplier "
                               + ("(per-stack value not confirmed for this game version, ASSUMED)"
                                  if "onslaught_per_stack" in getattr(ctx, "rule_overrides", ())
                                  else "(Crate developer post; ASSUMED until an in-game golden at 3/6 stacks)"))
        if any(c != "EXACT" for c in conv_conf):
            assumptions.append("several global conversions of one damage type above 100% split pro rata (TESTED)")
        if double:
            assumptions.append("form ability hits with both weapons (TESTED)")
        if finale_n:
            assumptions.append(f"charged finale: every {finale_n + 1}. strike with the skill's damage, the others "
                               "are weapon attacks at 100% WD - per action = average strike (Cadence: 'magnify the "
                               "power of every third strike', TESTED by the wiki/community)")
        conf = action_confidence(conv_conf, onslaught=bool(stacks), double_hit=double, finale=bool(finale_n),
                                 armor_piercing=bool(pierced), proc=proc is not None, vs_target=bool(tres))
        chain = (rn,) + tuple(sorted({s.record for s in states if s.parent == rn and s.points > 0}))
        results.append(ActionResult(
            rn, st.label, "proc" if proc else ("attack" if attack else "spell"), rank, wd, wd_base, wd_mod if wd_base else 0.0,
            double, 2 if double else 1,
            stacks, n_max, spender, raw_d, raw_t, parts, tuple(tres), min(sp["value"], sp["cap"]),
            procs, tuple(assumptions), conf, tuple(events), tuple(heals),
            (cooldown * (1 - cdr / 100)) if cooldown else None, proc, _dot_duration(recs), refresh, chain))
    results.sort(key=lambda a: (-(a.direct + a.dot), a.action_id))
    return results
