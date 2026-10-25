"""DpS and life steal per second at a known action rate (combat spec V2 3.3, 7.1; plan CA4, AP-F5).

Action rate: from the animation frames of the action (combat.frames: base frames / 1.25 /
slot multiplier / speed, quantized to whole frames, 30 frames = 1 s); a cooldown skill at most
once per cooldown and its own animation locks the character (action lock). Without animation
tables (other game version) the old estimate applies: attacks/s = 1.5 x attack speed, spells/s
= 1.0 x cast speed (ESTIMATE). Default rotation: the spender (if any) once per cooldown, the rest
of the time the strongest non-cooldown action (hits + DoT). DpS = E[damage] / E[time] over that
cycle (hits); DoT per second separately: each skill's DoT at uptime min(1, uses/s x duration), DoTs of
different skills stack (community primer, Crate forum 37738). Values
per state: the speed scales with the state's action factor (SLOWED: slower animation, frames
quantized again; CC_LOCKED = 0). The rotation is never more certain than its weakest input."""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..mechanics import procs as PR
from ..mechanics.policy import Confidence, rule
from . import frames as FR
from . import rate as RATE

BASE_CASTS_PER_S = 1.0


@dataclass(frozen=True)
class Rotation:
    target: str
    state: str
    main: str | None
    spender: str | None
    actions_per_s: float          # main action rate in this state
    dps_direct: float
    dot_per_s: float
    life_steal_per_s: float
    cycle_s: float
    confidence: str = "ESTIMATE"
    assumptions: tuple = ()
    proc_dps: float = 0.0             # part of dps_direct from procs' own damage (KG-07)
    proc_dot_per_s: float = 0.0       # part of dot_per_s from procs
    proc_life_steal_per_s: float = 0.0
    proc_detail: tuple = ()           # (label, procs/s, direct per proc, heal per proc, DoT per proc)
    main_timing: object = None        # combat.frames.Timing of the main action (None: estimate/override)
    spender_timing: object = None
    refresh: tuple = ()               # (cooldown before, expected cooldown, refresh_terms) of the spender


def action_timing(action, snap, factor: float = 1.0, override: float | None = None, values=None,
                  frames=None) -> tuple:
    """(actions per second, Timing or None). Override > animation frames > 1.5 x speed estimate."""
    v = values or snap.combat
    if override:
        return max(0.0, override * factor), None
    if frames is not None:
        t = frames.timing(action, v.speeds, factor)
        if t is not None:
            return t.actions_per_s, t
    if action.kind == "attack":
        sp = v.speeds["attack"]
        base = RATE.BASE_ATTACKS_PER_S * (min(sp["value"], sp["cap"]) if sp["cap"] else sp["value"]) / 100
    else:
        sp = v.speeds["cast"]
        base = BASE_CASTS_PER_S * (min(sp["value"], sp["cap"]) if sp["cap"] else sp["value"]) / 100
    return max(0.0, base * factor), None


def action_rate(action, snap, factor: float = 1.0, override: float | None = None, values=None,
                frames=None) -> float:
    return action_timing(action, snap, factor, override, values, frames)[0]


def _alt_text(t, frames: int) -> str:
    """'9 frames = 3.33/s if the engine plays all 27 keyframes and rounds to nearest' - the open rule
    pairs (KG-09) that give `frames`."""
    pairs = {p for p, f in zip(t.alt_rules, t.alt_frames) if f == frames}
    hf = getattr(t, "header_frames", None)
    keys = f"all {hf:g} keyframes" if hf else "all keyframes"
    if pairs and all(pb == "n" for _rd, pb in pairs):
        rounds = {rd for rd, _pb in pairs}
        why = f"if the engine plays {keys}" + ("" if rounds == {"up", "nearest"} else
                                                " and rounds to nearest" if rounds == {"nearest"} else " (rounded up)")
    else:
        why = "under another open timing rule (" + ", ".join(f"{rd}/{pb}" for rd, pb in sorted(pairs)) + ")"
    return f"{frames} frames = {30 / frames:.2f}/s {why}"


def timing_note(label: str, t) -> str:
    """'Feral Claws 26 frames (.anm 27 - 1) / 1.25 / 1.00 / 2.20 = 9.45 -> 10 frames (game files) = 3.00/s ... [ASSUMED]'."""
    if t is None:
        return f"{label}: action rate 1.5 x attack speed / 1.0 x cast speed (ESTIMATE)"
    if t.actions_per_s <= 0:
        return f"{label}: no actions in this state"
    src = {"assets": "game files", "community": "community TESTED",
           "default": "default frames ESTIMATE", "mixed": "partly default frames ESTIMATE"}.get(t.source, t.source)
    kind = "attack" if t.speed_type == "movement" else t.speed_type
    if t.base_frames is not None and t.raw_frames is not None:
        hf = getattr(t, "header_frames", None)
        kind_ = {"assets": ".anm", "community": "community", "default": "default"}.get(t.source, "")
        played = (f"{t.base_frames:g} frames ({kind_} {hf:g} - 1)" if hf is not None and abs(hf - 1 - t.base_frames) < 1e-9
                  else f"{t.base_frames:g} base frames")
        calc = (f"{played} / {t.player_multiplier:g} / {t.skill_multiplier:.2f} / "
                f"{t.speed_factor:.2f} = {t.raw_frames:.2f} -> ")
        head = f"{label} {calc}{t.frames if t.frames is not None else round(t.time_s * 30)} frames"
    else:
        head = f"{label} ~{t.time_s * 30:.1f} frames (pool average)"
    out = f"{head} ({src}) = {t.actions_per_s:.2f}/s"
    if t.speed_type == "movement":
        out += " + travel time (not modelled)"
    if t.span and t.other_frames:
        out += " (" + "; ".join(_alt_text(t, f) for f in t.other_frames) + ")"
    if t.next_breakpoint_pct is not None:
        out += (f"; next frame at {math.ceil(t.next_breakpoint_pct - 1e-6)}% {kind} speed"
                + ("" if t.breakpoint_reachable else " (above your cap)")
                + ("" if t.breakpoint_measured else " (from default frames, ESTIMATE)"))
    return out + f" [{t.confidence}]"


def _per_target(action, target: str):
    dmg = sum(e.direct for e in action.events if e.target == target)
    dot = sum(e.dot for e in action.events if e.target == target)
    heal = sum(h.raw_heal for h in action.heals if h.target == target)
    return dmg, dot, heal


def _trigger_rate(proc, rates: dict, by_id: dict, target: str) -> float:
    """Events per second that can roll this proc: bound celestial power -> only its skill;
    unbound -> every attack of the rotation, spells included (Zantai, Steam 2016: 'On attack
    effects trigger off of attacks, any attacks. That includes spells.'); *Crit x crit share."""
    eps = 0.0
    for aid, r in rates.items():
        act = by_id[aid]
        if proc.bound_to is not None and proc.bound_to != aid:
            continue
        share = 1.0
        if "Crit" in proc.trigger:
            ev = [e for e in act.events if e.target == target and e.hand != "off"]
            share = ev[0].crit_share if ev else 0.0
        eps += r * share
    return eps


def proc_part(procs, rates: dict, by_id: dict, target: str) -> tuple:
    """(direct dps, dot per s, life steal per s, detail) of the procs' own damage."""
    dps = dot = heal = 0.0
    detail = []
    for pa in procs:
        p = pa.proc
        eps = _trigger_rate(p, rates, by_id, target)
        rate = PR.proc_rate(eps, p.chance, p.cooldown or 0.0)
        d, t, h = _per_target(pa, target)
        if rate <= 0 or (d <= 0 and t <= 0):
            continue
        up = min(1.0, rate * pa.dot_duration) if pa.dot_duration else 0.0
        dps += rate * d
        dot += up * t
        heal += rate * h
        detail.append((pa.label, rate, d, h, t))
    return dps, dot, heal, tuple(detail)


DEFAULT_DOT_S = 3.0      # DoT duration when the skill records name none (gear DoT) - ASSUMED, typical value


def dot_uptime(action, uses_per_s: float) -> float:
    """Share of the time the DoT of one action runs: it refreshes on every use (the same skill does
    not stack on itself), min(1, uses/s x duration); no duration in the skill records (DoT from gear)
    -> 3 s (ASSUMED) - a main attack still refreshes it, a spender once per cooldown may not."""
    if uses_per_s <= 0:
        return 0.0
    dur = getattr(action, "dot_duration", 0.0) or DEFAULT_DOT_S
    return min(1.0, uses_per_s * dur)


REFRESH_TRIGGERS = ("AttackEnemy", "AttackEnemyCrit")   # the rotation's own attacks roll these


def refresh_terms(spender, main, target: str) -> list:
    """[(label, chance 0..1, seconds, trigger, share)] of the spender's cooldown refreshes that the main
    action rolls: a trigger of the rotation's own attacks (REFRESH_TRIGGERS), and for an item skill modifier
    on an attack or spell (5th entry: the skills that must be used) only when the main action is that skill or
    one of its modifiers; share = crit share of the main action on AttackEnemyCrit, else 1."""
    ev = [e for e in main.events if e.target == target and e.hand != "off"]
    crit = ev[0].crit_share if ev else 0.0
    used_skills = {main.action_id} | set(getattr(main, "chain", ()) or ())
    out = []
    for entry in getattr(spender, "cooldown_refresh", ()) or ():
        label, chance, amount, trig = entry[:4]
        via = entry[4] if len(entry) > 4 else ()
        if trig not in REFRESH_TRIGGERS or (via and not used_skills & set(via)):
            continue
        out.append((label, chance, amount, trig, crit if trig == "AttackEnemyCrit" else 1.0))
    return out


@rule("cooldown_refresh", Confidence.DERIVED,
      "cooldown refresh procs (refreshCooldown*, game database): each main action between two spender uses "
      "rolls chance (x crit share for AttackEnemyCrit) to cut the spender's cooldown by the amount; expected "
      "cooldown cd = cd0 - k x rate x (cd - spender time), at least the spender's own action time; one roll "
      "per action and target like the other procs (KG-07), the spender's own hit not counted")
def refreshed_cooldown(spender, main, target: str, cd0: float, rate: float, spender_s: float) -> tuple:
    """(expected cooldown in s, note or None). k = sum(chance x share x seconds) per main action."""
    k, used = 0.0, []
    for label, chance, amount, trig, share in refresh_terms(spender, main, target):
        k += chance * share * amount
        used.append(f"{label} {chance * 100:g}%" + (f" x crit {share * 100:.1f}%" if trig == "AttackEnemyCrit" else "")
                    + f" x {amount:g} s")
    if k <= 0 or rate <= 0:
        return cd0, None
    cd = max(spender_s, min(cd0, (cd0 + k * rate * spender_s) / (1 + k * rate)))
    return cd, (f"{spender.label} cooldown {cd0:g} s -> {cd:.2f} s expected: {', '.join(used)} per {main.label} "
                f"action ({k:.3f} s each, {rate:.2f}/s; game database, ESTIMATE - one roll per action and "
                "target; more targets in reach cut it further)")


@rule("rotation", Confidence.DERIVED,
      "main action plus the Onslaught spender once per cooldown; per-second values = expected damage / "
      "expected action time (animation frames, combat spec V2 3.3); confidence of the result = its "
      "weakest input")
def rotation(actions, snap, target: str, state: str = "STEADY", factor: float = 1.0,
             override: float | None = None, cooldowns: dict | None = None, values=None,
             procs=(), frames=None) -> Rotation:
    cooldowns = cooldowns if cooldowns is not None else {a.action_id: a.cooldown for a in actions}
    usable = [a for a in actions if any(e.target == target for e in a.events)]
    if not usable or factor <= 0:
        return Rotation(target, state, None, None, 0.0, 0.0, 0.0, 0.0, 0.0,
                        assumptions=("no new actions in this state",) if factor <= 0 else ())
    fillers = [a for a in usable if not cooldowns.get(a.action_id)]
    spenders = [a for a in usable if a.spender and cooldowns.get(a.action_id)]
    def capped_rate(a):
        r = action_rate(a, snap, 1.0, override, values, frames)
        cd = cooldowns.get(a.action_id)
        return min(r, 1.0 / cd) if cd else r
    def worth(a):                                # hits + DoT per second if used all the time (02.10.2026:
        r = capped_rate(a)                       # by hits alone a bleed build's main attack was missed)
        d, t, _h = _per_target(a, target)
        return d * r + t * dot_uptime(a, r)
    main = max(fillers or usable, key=worth)
    rm, tmain = action_timing(main, snap, factor, override, values, frames)
    if cooldowns.get(main.action_id):            # only cooldown actions: at most once per cooldown
        rm = min(rm, 1.0 / cooldowns[main.action_id])   # a cooldown is time, a slow does not lengthen it
    dm, tm, hm = _per_target(main, target)
    def per_cycle(a):                            # damage of one use incl. its DoT within one cooldown
        cd = cooldowns[a.action_id]
        d, t, _h = _per_target(a, target)
        return d + t * min(getattr(a, "dot_duration", 0.0) or DEFAULT_DOT_S, cd)
    spender = max(spenders, key=per_cycle) if spenders and main.action_id not in \
        {s.action_id for s in spenders} else None
    refresh_note = None
    refresh: tuple = ()
    if spender and rm > 0:
        cd = cooldowns[spender.action_id]
        rs, tsp = action_timing(spender, snap, factor, override, values, frames)
        ts = 1.0 / rs if rs > 0 else cd
        cd0 = cd
        cd, refresh_note = refreshed_cooldown(spender, main, target, cd, rm, ts)
        if refresh_note:
            refresh = (cd0, cd, tuple(refresh_terms(spender, main, target)))
        ds, dts, hs = _per_target(spender, target)
        n_main = max(0.0, (cd - ts) * rm)
        dps = PR.dps([(ds, ts), (dm * n_main, n_main / rm if rm else 0.0)])
        heal = (hs + hm * n_main) / max(cd, ts)
        span = max(cd, ts)
        rates = {spender.action_id: 1.0 / span, main.action_id: n_main / span}
        # DoTs of different skills stack (community primer, Crate forum 37738): the spender's own DoT
        # runs min(1, duration / cycle) of the time; the main attack's DoT is refreshed all the time
        dot_main = tm * dot_uptime(main, n_main / span)
        dot_spender = dts * dot_uptime(spender, 1.0 / span)
    else:
        tsp = None
        dps = PR.dps([(dm, 1.0 / rm)]) if rm > 0 else 0.0
        heal = hm * rm
        span = 1.0 / rm if rm else 0.0
        rates = {main.action_id: rm}
        dot_main, dot_spender = tm * dot_uptime(main, rm), 0.0
    by_id = {a.action_id: a for a in usable}
    pd, pt, ph, detail = proc_part(procs, rates, by_id, target)
    if override:
        notes = [f"action rate set by you: {override:g}/s"]
        confs = ["ASSUMED"]
    else:
        used = [(main.label, tmain)] + ([(spender.label, tsp)] if spender and rm > 0 else [])
        notes = [timing_note(lbl, t) for lbl, t in used]
        confs = [t.confidence if t is not None else "ESTIMATE" for _lbl, t in used]
    notes += ["spender once per cooldown (its animation locks the character), main attack in between"
              if spender else "main attack only",
              "DoT per skill at uptime min(1, uses/s x duration; 3 s when only gear gives the DoT, ASSUMED); "
              "DoTs of different skills stack, the same skill refreshes (community primer, Crate forum 37738 - "
              "TESTED)"]
    if refresh_note:
        notes.append(refresh_note)
        confs.append("ESTIMATE")
    if detail:
        notes.append("procs' own damage: proc formula per instance over the rotation's attacks; bound "
                     "celestial powers only on their skill; DoT uptime min(1, procs/s x duration) (ESTIMATE)")
        confs.append("ESTIMATE")
    conf = FR.weakest(*confs, "ASSUMED")        # rotation policy itself: ASSUMED
    return Rotation(target, state, main.label, spender.label if spender else None, rm, dps + pd,
                    dot_main + dot_spender + pt, heal + ph, span, conf, tuple(notes), pd, pt, ph, detail, tmain, tsp,
                    refresh)
