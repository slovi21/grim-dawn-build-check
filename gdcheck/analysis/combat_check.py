"""Boss Combat Check V1 (combat spec V2 14; plan CA6.3-6.6).

Per boss of the outgoing target states (Loghorrean = story boss, Callagadra =
superboss) and per combat state (STEADY, PURGED, SLOWED, CC_LOCKED,
CIRCUIT_BREAKER): outgoing DpS and life steal (same damage events), incoming
damage through the Order of Defense, the largest modelled hit (spike), sustain
and the key figures Sustain Ratio, Leech Coverage, Spike Margin, net health rate
and TTD (steady approximation). Verdict LOW / MEDIUM / HIGH combat risk with
reasons - never win/lose, never a time to kill.

Boss model (KG-08, schema 2 boss file): first-level skills plus autocasts, damaging auras of the
boss and of spawned monsters, the spawned monsters' own attacks, the enrage below a Health threshold
(own row ENRAGED) and the boss's own attack-speed slow for SLOWED - all values from the game
database, the use rates HEURISTIC.

Confidence: outgoing from animation frames (TESTED/ASSUMED; ESTIMATE where the base frames are
defaults); incoming HEURISTIC (monster
level = player level through the boss's charLevel equation, boss action rate assumed, first-level skills only - the
spike is a modelled LOWER bound)."""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from ..combat import actions as ACT
from ..combat import dps as DPS
from ..combat import frames as FR
from ..combat import sustain as SUS
from ..combat import target_state as TS
from ..data import bosses as BD
from ..data import gametables as GT
from ..mechanics import defense as DEF
from ..mechanics import hit as HIT
from ..mechanics import survival as SV
from ..mechanics.policy import Confidence, rule
from ..stats import states as ST

ROOT = Path(__file__).resolve().parents[2]
PLAYER_RES = {"Physical": "Physical", "Pierce": "Pierce", "Fire": "Fire", "Cold": "Cold",
              "Lightning": "Lightning", "Poison": "Acid", "Life": "Vitality", "Aether": "Aether",
              "Chaos": "Chaos", "Bleeding": "Bleeding", "InternalTrauma": "Physical", "Burn": "Fire",
              "Frostburn": "Cold", "Electrocute": "Lightning", "PoisonDot": "Acid", "VitalityDecay": "Vitality"}
BOSS_KEYS = (("Loghorrean", "Loghorrean, the Voice of Ch'thon"), ("Callagadra", "Callagadra, Scion of the Sands"))
BASIC_PER_S = BD.BASIC_PER_S          # HEURISTIC: basic attacks per second of a boss
NO_CD_EVERY_S = BD.NO_CD_EVERY_S      # HEURISTIC: a skill without cooldown is used about every 6 s
STATES = ("STEADY", "PURGED", "SLOWED", "CC_LOCKED", "CIRCUIT_BREAKER")


@dataclass(frozen=True)
class StateCheck:
    state: str
    dps: float
    life_steal: float
    regen: float
    heals: float
    total_sustain: float
    incoming_dps: float
    spike: float
    spike_skill: str
    spike_margin: float
    sustain_ratio: float
    leech_coverage: float
    net_rate: float
    ttd: float
    notes: tuple = ()
    pct_current_per_s: float = 0.0   # share of CURRENT health lost per second (never kills alone)
    health: float = 0.0              # health this state is evaluated at
    dot_per_s: float = 0.0           # your DoT per second on the boss (dps = hits only)
    pct_spike: float = 0.0           # largest share of CURRENT health one landed hit takes (.67: not the spike)
    pct_spike_skill: str = ""
    # .56 provenance (X-002) - the parts the numbers are summed from, never part of equality/hash
    attack_parts: tuple = field(default=(), compare=False)    # AttackPart per modelled boss attack
    sustain_terms: tuple = field(default=(), compare=False)   # (label, HP/s): sums to total_sustain
    leech_inputs: tuple = field(default=(), compare=False)    # (source label, life steal %) of this state
    rotation: object = field(default=None, compare=False)     # combat.dps.Rotation of this state

    @property
    def dps_total(self) -> float:
        """Hits + DoT per second (review 02.10.2026: E7 showed hits only - Callagadra resists 85% of
        every hit type but not bleeding, so a bleed build's damage looked a third of E2's)."""
        return self.dps + self.dot_per_s


class AttackPart(NamedTuple):
    """One modelled boss attack against your values in one state (.56 provenance): incoming =
    sum(flat_expected x uses_per_s), % current health = sum(pct_expected x uses_per_s), spike =
    max(flat_hit), the largest % current health hit apart (max(pct_hit), .67)."""
    name: str
    kind: str              # boss | boss aura | autocast | spawn | spawn aura
    uses_per_s: float
    rate: str              # how the use rate was set
    flat_hit: float        # landed flat damage of one hit (no crit, after your defenses)
    flat_expected: float   # expected landed damage of one use (hit chance, DoT included)
    pct_hit: float         # share of current health one landed hit takes
    pct_expected: float    # expected share per use
    origin: str = ""       # spawned by / autocast on (data.bosses 'spawned_by' / 'source')


@dataclass(frozen=True)
class BossCombatCheck:
    boss: str
    target: str
    level: int
    llr: float
    states: dict
    risk: str
    reasons: tuple
    assumptions: tuple
    gaps: tuple = field(default_factory=tuple)
    risk_no_spawns: str = ""        # .54: risk with the spawned monsters killed or avoided ('' = no spawns)
    data: str = ""                  # .56: boss data file, game version and fingerprint it belongs to


def monster_level(ctx) -> tuple[int, str]:
    return TS.boss_level(ctx)


def _player_res(values) -> dict:
    out = {}
    for t, name in PLAYER_RES.items():
        r = values.resists.get(name)
        if r:
            out[t] = min(r["value"], r["cap"])
    return out


MELEE_CLASSES = ("Weapon", "Melee")


def _avoid(attack, f) -> float:
    cls = attack["class"]
    if "Projectile" in cls:
        return f.get("characterDeflectProjectile", 0.0) / 100
    if any(k in cls for k in MELEE_CLASSES):
        return f.get("characterDodgePercent", 0.0) / 100
    return 0.0


@rule("boss_hit_landed", Confidence.DERIVED,
      "a boss hit after your Order of Defense: hit chance (boss OA vs your DA), avoidance, resistances, "
      "armor and absorption; expected value (with DoT) for the rates, single landed hit for the spike")
def _incoming(attack, values, boss_oa, expected: bool) -> float:
    """Flat damage of one attack after the Order of Defense (+ DoT for the expected value).
    % current health damage is NOT included (see _pct_current)."""
    res = _player_res(values)
    f = values.fields
    hit = DEF.incoming(attack["raw"], res, armor=values.armor_weighted, absorption=values.absorption / 100,
                       attacker_oa=boss_oa if expected else 0.0, defender_da=values.abilities["da"],
                       avoid=_avoid(attack, f), block_chance=f.get("defensiveBlockChance", 0.0) / 100,
                       block_amount=f.get("defensiveBlock", 0.0), expected=expected)["total"]
    if expected:
        keep = 1 - _avoid(attack, f)
        hitf = HIT.expected_hit(HIT.pth(boss_oa, values.abilities["da"]))["hit"] if boss_oa else 1.0
        hit += keep * hitf * sum(v * (1 - min(res.get(t, 0.0), 100.0) / 100) for t, v in attack["dot_raw"].items())
    return max(0.0, hit)


@rule("pct_current_health", Confidence.DERIVED,
      "% current health damage of a boss attack after hit chance and your resistance to it - shrinks "
      "with your health, never kills alone (review CA6)")
def _pct_current(attack, values, boss_oa, expected: bool) -> float:
    """Share of CURRENT health one use takes (0..1): reduced by the hit chance/avoidance
    (expected) and your resistance to it. It shrinks with your health, so it never kills
    alone - reported apart from the flat incoming damage (review CA6, P0)."""
    pct = (attack.get("pct_current_life") or 0.0) / 100
    if not pct:
        return 0.0
    f = values.fields
    pct *= (1 - f.get("defensivePercentCurrentLife", 0.0) / 100)
    if expected:
        pct *= (1 - _avoid(attack, f))
        if boss_oa:
            pct *= HIT.expected_hit(HIT.pth(boss_oa, values.abilities["da"]))["hit"]
    return max(0.0, min(pct, 1.0))


SLOW_RES_CAP = 80.0        # community (Crate forum 44089): Slow Resistance works up to 80% (ASSUMED)


def boss_slowed(state, values, slow):
    """SLOWED with the boss's own strongest slow (game database): attack-speed slows lower your attack
    speed, total-speed slows attack and cast speed; your Slow Resistance lowers the slow (ASSUMED:
    magnitude, capped at 80%); applied to the capped sheet speed (ASSUMED, over-cap speed does not
    buffer a slow). The frames are rounded again from the slowed speed."""
    name, pct, kind, aura, dur = slow
    res = min(max(values.fields.get("defensiveTotalSpeedResistance", 0.0), 0.0), SLOW_RES_CAP)
    eff = min(max(pct * (1 - res / 100), 0.0), 95.0)
    sp = {k: dict(v) for k, v in values.speeds.items()}
    for k in (("attack", "cast") if kind == "total" else ("attack",)):
        if k in sp:
            sp[k]["value"] = max(min(sp[k]["value"], sp[k]["cap"] or sp[k]["value"]) * (1 - eff / 100), 1.0)
            sp[k]["slow"] = eff                    # the frame model slows its own capped speed (.58)
    slowed = dataclasses.replace(values, speeds=sp)
    what = "attack and cast speed" if kind == "total" else "attack speed"
    when = "while you stand in it" if aura else f"for {dur:g} s after its hit"
    note = (f"{name.split(': ')[-1]}: -{pct:g}% {what} {when} (game database); your Slow Resistance "
            f"{res:g}% -> -{eff:.0f}% (ASSUMED: lowers the slow, cap 80%)")
    return dataclasses.replace(state, values=slowed, action_factor=1.0, notes=(note,), confidence="DERIVED")


def _enraged(attacks, enr) -> list:
    """Boss attacks below the enrage threshold: damage up; basic attacks and the autocasts riding on
    them more often (attack speed: from the frames at the enrage speed when the profile has them,
    else linear); spawned monsters and their auras unchanged."""
    out = []
    for a in attacks:
        if a.get("source", "boss") in ("boss", "boss aura") or str(a.get("source", "")).startswith("autocast"):
            a = dict(a)
            f = 1 + enr["damage_pct"] / 100
            a["raw"] = {t: v * f for t, v in a["raw"].items()}
            a["dot_raw"] = {t: v * f for t, v in a["dot_raw"].items()}
            if "rate_enraged" in a:              # .54: frames recounted at the enrage speed (data.bosses)
                a["rate"] = a["rate_enraged"]
            else:
                faster = a["basic"] or a.get("parent_basic")
                a["rate"] = _uses_per_s(a) * (1 + enr["attack_speed_pct"] / 100 if faster else 1.0)
                if a.get("parent_basic") and a.get("cooldown"):
                    a["rate"] = min(a["rate"], 1.0 / a["cooldown"])
        out.append(a)
    return out


def _second_level_note(prof) -> str:
    """One line: what the second level adds (autocasts, spawned monsters, auras, enrage, phases)."""
    at = prof.get("attacks") or []
    auto = sorted({a.get("label") or a["name"] for a in at if str(a.get("source", "")).startswith("autocast")})
    spawn = sorted({a["source"][6:] for a in at if str(a.get("source", "")).startswith("spawn")})
    aura = [a for a in at if a.get("aura")]
    parts = []
    if auto:
        parts.append("autocasts " + ", ".join(auto))
    if spawn:
        alive: dict = {}
        for a in at:
            if str(a.get("source", "")).startswith("spawn"):
                who = a.get("monster_label") or a["source"][6:]
                alive[who] = max(alive.get(who, 0.0), a.get("count", 1.0))
        parts.append("spawned " + ", ".join(f"{k} ~{v:.1f} alive" for k, v in sorted(alive.items()))
                     + " (upper bound: always in reach and attacking, never killed - row NO_SPAWNS: killed or avoided)")
    if aura:
        parts.append(f"damaging auras one at a time {sum(a['rate'] for a in aura if a['source'] != 'boss aura'):.2f}"
                     " ticks/s (HEURISTIC)")
    enr = prof.get("enrage")
    if enr:
        parts.append(f"enrage below {enr['life_pct']:g}% Health (own row)")
    ph = prof.get("phases") or []
    if ph:
        parts.append(f"fight phase {sum(1 for p in ph if p['position'] <= 0)} of {len(ph)} (attack speed "
                     f"{prof.get('attack_speed', 1.0):.2f})")
    return "; ".join(parts) if parts else "none"


def _basic_note(prof) -> str:
    """Boss basic attack rate: from the animation frames (.54) or the heuristic."""
    b = prof.get("basic")
    if not b:
        txt = (f"basic attack {BASIC_PER_S:g}/s x the record's attack speed ({prof.get('attack_speed', 1.0):.2f}) "
               "(HEURISTIC, no animation frames for this boss)")
    else:
        txt = f"basic attack {b['text']}"
        if prof.get("speed_parts"):
            txt += " [" + ", ".join(prof["speed_parts"]) + "]"
        be = prof.get("basic_enraged")
        if be:
            txt += f", enraged {be['rate']:.2f}/s (frames at {be['speed_pct']:+g}% speed)"
        txt += " - an upper bound, the AI pauses and moves (HEURISTIC)"
        if b.get("notes"):
            txt += "; " + "; ".join(b["notes"])
    sb = prof.get("spawn_basic") or {}
    if sb:
        txt += "; spawned monsters' basic attacks per monster: " + "; ".join(
            f"{k} {v.split(' (')[0]}" for k, v in sorted(sb.items()))
    return txt


def _uses_per_s(attack) -> float:
    if "rate" in attack:                       # data.bosses (KG-08): incl. count, autocast chance, auras
        return attack["rate"]
    if attack["basic"]:
        return BASIC_PER_S
    return 1.0 / attack["cooldown"] if attack["cooldown"] else 1.0 / NO_CD_EVERY_S


def check(ctx, sources, snap, actions, targets, onslaught: int = 0, procs=None, frames=None) -> list[BossCombatCheck]:
    """`procs` (ACT.proc_actions of all targets) and `frames` (FR.FrameModel) of the caller are reused when given
    (.72: the damage benchmark needs the same objects); the other states are computed for the bosses only - the
    benchmark targets (dummy, Mogara) are not checked here."""
    from ..data import versioned as V
    ver, fp = V.index_identity(ctx.index)
    combat = BD.combat_meta(ROOT, ver or "1.3.0.8", fp)
    if not combat or ctx.difficulty.value != 2:
        return []                                   # the modelled bosses are Ultimate bosses
    _level, level_src = monster_level(ctx)
    diff_total = GT.monster_adjustment_damage(ctx.index, 2)
    states = ST.build(ctx, sources, snap)
    fm = frames if frames is not None else FR.FrameModel(ctx, sources)
    bosses = [t for t in targets if t.role in ("boss", "")]       # not the benchmark targets (dummy, Mogara)
    acts_by_state = {"STEADY": actions}
    procs_by_state = {"STEADY": procs if procs is not None else ACT.proc_actions(ctx, sources, snap, bosses)}
    for name, stt in states.items():
        if stt.values is not snap.combat:
            acts_by_state[name] = ACT.damage_per_action(ctx, sources, snap, bosses, onslaught=onslaught,
                                                        values=stt.values)
            procs_by_state[name] = ACT.proc_actions(ctx, sources, snap, bosses, values=stt.values)
    out = []
    for key, boss in BOSS_KEYS:
        tgt = next((t for t in targets if t.name.startswith(key)), None)
        meta = (combat.get("bosses") or {}).get(boss)
        if not tgt or not meta:
            continue
        lv = TS.boss_monster_level(ctx, meta)
        prof = BD.attack_profile(boss, combat, lv, diff_total, ctx.profile.value == "ascendant", frames=fm,
                                 label=lambda rec, b=boss: BD.skill_label(ctx.index, rec, b))
        if not prof["attacks"]:
            continue
        boss_oa = tgt.oa or tgt.da_base           # game database (KG-03); fallback HEURISTIC = its DA
        rows = {}
        bstates = dict(states)
        if prof.get("slows") and "SLOWED" in bstates:      # the boss's own slow (game database, KG-08)
            bstates["SLOWED"] = boss_slowed(bstates["SLOWED"], snap.combat, prof["slows"][0])
        enr = prof.get("enrage")
        spawned = any(_is_spawn(a) and a.get("rate", 0.0) > 0 for a in prof["attacks"])
        rows_ns: dict = {}
        bstates_ns = dict(states)                 # .54 review: without spawns no spawn slow (Callagadra's tornado)
        own_slows = BD.slows_of([a for a in prof["attacks"] if not _is_spawn(a)]) if spawned else []
        if own_slows and "SLOWED" in bstates_ns:
            bstates_ns["SLOWED"] = boss_slowed(bstates_ns["SLOWED"], snap.combat, own_slows[0])

        def numbers(sname, stt, enraged):
            """Your side of one state: DpS, DoT, life steal, regeneration, heals, sustain, notes, health."""
            vals = stt.values
            acts = acts_by_state.get(sname, actions)
            prc = procs_by_state.get(sname, procs_by_state["STEADY"])
            rot = DPS.rotation(acts, snap, tgt.name, sname, stt.action_factor, ctx.options.action_rate,
                               values=vals, procs=prc, frames=fm)
            sus = SUS.sustain(ctx, sources, snap, acts, tgt.name, stt, 1.0, ctx.options.action_rate, procs=prc,
                              frames=fm)
            hp = vals.abilities["health"] * stt.health_share     # circuit breaker: below its trigger threshold
            dps, dot, life_steal, total = rot.dps_direct, rot.dot_per_s, sus.life_steal, sus.total
            notes = stt.notes
            if enraged and enr:                   # the boss absorbs part of your damage -> less life steal
                keep = max(0.0, 1 - enr["absorption_pct"] / 100)
                dps, dot, life_steal = dps * keep, dot * keep, life_steal * keep
                total = sus.total - sus.life_steal + life_steal
                notes = (f"boss below {enr['life_pct']:g}% Health ({BD.skill_label(ctx.index, enr['skill'], boss)}): "
                         f"+{enr['damage_pct']:g}% damage, +{enr['attack_speed_pct']:g}% attack speed, "
                         f"{enr['absorption_pct']:g}% damage absorption (game database); your STEADY values",)
            leech = tuple((sources.source(sid).label if sources.source(sid) else str(sid), float(v))
                          for sid, v in (getattr(vals, "provenance", None) or {}).get("offensiveLifeLeechMin", []))
            terms = (("life steal", life_steal), ("regeneration", sus.regen), ("skill heals", sus.skill_heals),
                     ("proc heals", sus.proc_heals))           # after the enrage scaling (critic .56)
            return dict(vals=vals, hp=hp, dps=dps, life_steal=life_steal, regen=sus.regen,
                        heals=sus.skill_heals + sus.proc_heals, total=total, notes=notes, dot=dot,
                        sustain_terms=terms, leech_inputs=leech, rotation=rot)

        for sname in STATES + (("ENRAGED",) if enr and "STEADY" in bstates else ()):
            enraged = sname == "ENRAGED"
            key = "STEADY" if enraged else sname
            stt = bstates.get(key)
            if stt is None:
                continue
            attacks = _enraged(prof["attacks"], enr) if enraged and enr else prof["attacks"]
            nb = numbers(sname, stt, enraged)
            rows[sname] = _state_row(sname, attacks, boss_oa, **nb)
            if spawned:                           # .54: the same state with the spawned monsters killed/avoided
                stt_ns = bstates_ns.get(key)
                nb_ns = nb if stt_ns is stt else numbers(sname, stt_ns, enraged)
                rows_ns[sname] = _state_row(sname, [a for a in attacks if not _is_spawn(a)], boss_oa, **nb_ns)
        rec = ctx.character.stats.get("greatest_damage_received") or 0.0
        st_spike = rows["STEADY"].spike if "STEADY" in rows else 0.0
        ratio = rec / st_spike if rec and st_spike else None
        risk, reasons = verdict(rows, ratio, prof["gaps"])
        risk_ns = ""
        if spawned and "STEADY" in rows_ns:
            s0, s1 = rows["STEADY"], rows_ns["STEADY"]
            share = 1 - s1.incoming_dps / s0.incoming_dps if s0.incoming_dps else 0.0
            rows["NO_SPAWNS"] = dataclasses.replace(s1, state="NO_SPAWNS", notes=(
                f"spawned monsters killed or avoided: their attacks and auras left out ({share * 100:.0f}% of the "
                f"STEADY incoming damage, {(s0.pct_current_per_s - s1.pct_current_per_s) * 100:.1f} points of "
                "% current Health/s) - the lower end; STEADY counts them always alive, in reach and attacking "
                "(upper bound, HEURISTIC)",))
            risk_ns, why = risk_without_spawns(rows_ns, ratio, prof["gaps"], risk, share)
            if why:
                reasons.append(why)
        out.append(BossCombatCheck(
            boss, tgt.name, lv, tgt.llr, rows, risk, tuple(reasons),
            (f"monster level {lv}: {level_src} ({TS.boss_level_basis(ctx, meta)}; area cap ASSUMED not binding); "
             + (f"boss OA {boss_oa:,.0f}, DA {tgt.da_base:,.0f}, armor {tgt.armor:,.0f} at "
                f"{tgt.armor_absorption * 100:.0f}% absorption from the game database (DERIVED)" if tgt.oa else
                f"boss OA = its DA estimate {boss_oa:.0f} (HEURISTIC)"),
             "boss actions: " + _basic_note(prof) + f"; skills once per cooldown, without cooldown every "
             f"{NO_CD_EVERY_S:g} s (HEURISTIC); second level from the game database (KG-08): "
             + _second_level_note(prof),
             "spike = largest modelled landed flat hit, without crit (lower bound); the largest % current "
             "health hit is shown apart: it shrinks with your health and never kills alone; "
             "circuit breaker state evaluated at the trigger threshold from the game database "
             "(Ghoulish Hunger: 45% Health)",
             (f"your outgoing damage: action rate set by you ({ctx.options.action_rate:g}/s, --action-rate)"
              if ctx.options.action_rate else
              "your outgoing damage: action time from animation frames (30 frames = 1 s, player x1.25, "
              "skill slot multiplier, speed incl. weapon base speed, whole frames rounded up - details in "
              "the rotation lines)" if fm.available else "your outgoing damage: action rate 1.5 x attack "
              "speed (ESTIMATE, no animation tables for this game version)")
             + f"; Life Leech Resistance of the boss {tgt.llr * 100:.0f}% - lowers life steal (TESTED by the "
             "community, no developer quote)"),
            tuple(prof["gaps"]), risk_ns,
            f"{combat.get('_file', 'boss-combat')} (game {combat.get('_label') or combat.get('game_version', '?')}, "
            f"fingerprint {combat.get('fingerprint') or '?'})"))
    return out


def _is_spawn(attack) -> bool:
    return str(attack.get("source", "")).startswith("spawn")


RISK_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def risk_without_spawns(rows_ns: dict, ratio, gaps, risk: str, share: float) -> tuple[str, str | None]:
    """(rating with the spawned monsters killed or avoided, reason or None). Review .54: the same
    'greatest hit taken' ratio as STEADY (a smaller spike without spawns must not add a warning) and
    never above the STEADY rating; the reason only when the rating drops."""
    r, _why = verdict(rows_ns, ratio, gaps)
    if RISK_RANK.get(r, 0) > RISK_RANK.get(risk, 0):
        r = risk
    if r == risk:
        return r, None
    return r, (f"spawned monsters supply {share * 100:.0f}% of the modelled incoming damage (upper bound: always "
               f"attacking, never killed) - {r} if you kill or avoid them")


def _kind(attack) -> str:
    src = str(attack.get("source", "boss"))
    if src.startswith("spawn"):
        return "spawn aura" if attack.get("aura") else "spawn"
    if src.startswith("autocast"):
        return "autocast"
    return "boss aura" if src == "boss aura" else "boss"


def _rate_how(attack) -> str:
    """How the use rate of an attack was set (data.bosses, KG-08) - for the provenance."""
    if attack.get("rate_source") == "frames":
        return "basic attack from animation frames (upper bound)"
    if attack.get("aura"):
        return ("aura tick 1/s" if _kind(attack) == "boss aura"
                else "aura tick 1/s, one spawned aura at a time, half of the time")
    if str(attack.get("source", "")).startswith("autocast"):
        return "autocast: chance x the parent skill's uses"
    if attack.get("basic"):
        return "basic attack 1/s x attack speed (heuristic)"
    return "once per cooldown" if attack.get("cooldown") else "every 6 s (no cooldown)"


@rule("boss_incoming_sum", Confidence.DERIVED,
      "incoming per second = sum over the modelled boss attacks of expected landed damage x uses per "
      "second; % current health per second the same with the shares; spike = largest single landed flat hit "
      "(combat spec V2 14); a % current health hit never kills alone and is shown apart (.67)")
def _state_row(sname, attacks, boss_oa, vals, hp, dps, life_steal, regen, heals, total, notes, dot,
               sustain_terms=(), leech_inputs=(), rotation=None) -> StateCheck:
    """One state row: incoming, % current health drain and spike of `attacks` against your values,
    from one AttackPart per attack (computed once - .56)."""
    parts = tuple(AttackPart(a.get("label") or a["name"], _kind(a), _uses_per_s(a), _rate_how(a),
                             _incoming(a, vals, boss_oa, False), _incoming(a, vals, boss_oa, True),
                             _pct_current(a, vals, boss_oa, False), _pct_current(a, vals, boss_oa, True),
                             str(a.get("spawned_by") or ((a.get("origin_label") or a.get("source"))
                                                         if _kind(a) == "autocast" else "") or ""))
                  for a in attacks)
    inc = sum(p.flat_expected * p.uses_per_s for p in parts)
    drain = sum(p.pct_expected * p.uses_per_s for p in parts)
    # .67: the spike is the largest FLAT hit - Loghorrean's Roar of Madness (void) takes 38% of current health
    # with no flat damage, so 'spike margin' was 1 / 0.38 = 2.63 for every character (it never kills alone)
    spikes = [(p.flat_hit, p.name) for p in parts if p.flat_hit > 0]
    spike, spike_skill = max(spikes) if spikes else (0.0, "-")
    pcts = [(p.pct_hit, p.name) for p in parts if p.pct_hit > 0]
    pct_spike, pct_skill = max(pcts) if pcts else (0.0, "")
    net = SV.net_health_rate(total, inc)
    return StateCheck(sname, dps, life_steal, regen, heals, total, inc, spike, spike_skill,
                      SV.spike_margin(hp, spike), SV.sustain_ratio(total, inc), SV.leech_coverage(life_steal, inc),
                      net, SV.ttd_steady(hp, net), notes, drain, hp, dot, pct_spike, pct_skill, parts,
                      tuple(sustain_terms), tuple(leech_inputs), rotation)


@rule("combat_risk", Confidence.HEURISTIC,
      "LOW/MEDIUM/HIGH from spike margin, sustain ratio and the state rows; an incomplete boss model "
      "is never LOW (combat spec V2 13, review CA6)")
def verdict(rows: dict, model_ratio: float | None = None, gaps=()) -> tuple[str, list]:
    """LOW / MEDIUM / HIGH combat risk (HEURISTIC UI classification, spec 13). An incomplete
    boss model (first-level skills only, or your recorded greatest hit well above the
    modelled spike) never yields LOW (review CA6, P1)."""
    s = rows.get("STEADY")
    if s is None:
        return "LOW", []
    hi, mid = [], []
    if s.spike_margin < 1.0:
        hi.append(f"the modelled {s.spike_skill} hit ({s.spike:,.0f}) exceeds your Health")
    elif s.spike_margin < 1.2:
        mid.append(f"small buffer against {s.spike_skill} ({s.spike:,.0f} vs your Health)")
    if s.sustain_ratio < 0.8:
        hi.append(f"sustain covers only {s.sustain_ratio * 100:.0f}% of the modelled incoming damage")
    elif s.sustain_ratio < 1.0:
        mid.append(f"sustain below the modelled incoming damage ({s.sustain_ratio * 100:.0f}%)")
    if s.life_steal and s.leech_coverage < 0.25 and s.sustain_ratio < 1.2:
        mid.append("life steal works poorly against this boss (Life Leech Resistance)")
    p = rows.get("PURGED")
    if p and p.spike_margin < 1.0 <= s.spike_margin:
        mid.append("a purge removes the buffer against the biggest hit")
    w = rows.get("SLOWED")
    if w and w.sustain_ratio < 1.0 <= s.sustain_ratio:
        mid.append("when slowed, your sustain falls below the modelled incoming damage")
    cb = next((v for k, v in rows.items() if k.startswith("CIRCUIT_BREAKER")), None)
    if cb and s.sustain_ratio < 1.0 <= cb.sustain_ratio:
        mid.append("your sustain relies on the low-health proc (circuit breaker)")
    lock = rows.get("CC_LOCKED")
    if lock and lock.ttd < 5:
        mid.append(f"while stunned/frozen you lose all Health in about {lock.ttd:.0f} s (steady estimate)")
    e = rows.get("ENRAGED")
    if e and e.spike_margin < 1.0 <= s.spike_margin:
        hi.append(f"below the enrage threshold the {e.spike_skill} hit ({e.spike:,.0f}) exceeds your Health")
    elif e and e.sustain_ratio < 1.0 <= s.sustain_ratio:
        mid.append("when the boss enrages, your sustain falls below the modelled incoming damage")
    if model_ratio is not None and model_ratio > 1.5:
        mid.append(f"your greatest hit taken is {model_ratio:.1f}x the largest modelled hit of this boss - "
                   "bigger hits come from elsewhere or from stacked hits (not modelled)")
    risk = "HIGH" if hi or len(mid) >= 3 else ("MEDIUM" if mid else "LOW")
    if risk == "LOW" and gaps:
        risk = "MEDIUM"
        mid.append("boss model with HEURISTIC rates and open gaps (" + str(gaps[0])[:80] + ") - never LOW")
    return risk, hi + mid


def plausibility(ctx, checks) -> str | None:
    """Compare the greatest modelled spike with 'greatest damage received' in the save (hint only)."""
    rec = ctx.character.stats.get("greatest_damage_received")
    if not rec or not checks:
        return None
    spike = max(c.states["STEADY"].spike for c in checks if "STEADY" in c.states)
    if spike <= 0 or not math.isfinite(spike):
        return None
    ratio = rec / spike
    txt = f"greatest hit you ever took: {rec:,.0f}; largest modelled boss hit: {spike:,.0f}"
    if ratio > 2:
        return txt + " - bigger hits came from elsewhere (other enemies, earlier phases) or from stacked hits"
    if ratio < 0.5:
        return txt + " - the model may overestimate boss damage"
    return txt + " - same order of magnitude"
