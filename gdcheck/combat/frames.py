"""Action time from animation frames (AP-F2/F5, PDF 'Frames, Animationsgeschwindigkeit und DPS V1').

Per action: the animation table (active shapeshift form -> its 'unarmed' table, else male/female),
the weapon mode prefix (weapons in hand), the animation (skillSpecialAnimationName, modifiers
override it; none -> basic attack or basic spell list), the pool entries with their slot speed
multiplier and weight, the speed type (attack / cast / movement) and the weapon base speed.

    speed factor = min(summed % x (1 + weapon base speed), cap) x (1 - slow)   [casts: no weapon;
                   forms use the weapons in hand since .59, ASSUMED]
    base frames  = header keyframes - 1 (ruleset 'anim_playback' R4, DERIVED; 'n' = header)
    raw frames   = base frames / 1.25 (player) / slot multiplier / speed factor
    action time  = E over the pool of quantize(raw frames) / 30

Header frames: data.animation (assets EXACT > community TESTED > default ESTIMATE). Rounding and
playback from the ruleset ('frame_rounding' up, 'anim_playback' n-1); the span shows the rule pairs
the measurements leave open (mechanics.animation.alternative_rules: playback N, up or to nearest),
never the excluded (nearest, N-1). The action time is never better than its weakest input.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from ..data import animation as AD
from ..domain.enums import Activity
from ..mechanics import animation as AN

ORDER = ("EXACT", "TESTED", "DERIVED", "ASSUMED", "ESTIMATE", "HEURISTIC")
# Skills that move the character as part of the action: the travel time is not in the animation.
MOVEMENT = {"Skill_AttackPathCharge", "Skill_AttackWeaponCharge", "Skill_AttackRadiusLeap"}


def weakest(*conf: str) -> str:
    return max((c for c in conf if c in ORDER), key=ORDER.index, default="ESTIMATE")


@dataclass(frozen=True)
class Profile:
    action_id: str
    table: str                 # "male" | "female" | form animation table record
    prefix: str                # weapon mode prefix
    animation: str             # special animation name, '@attack' or '@spell'
    found: bool                # False: the table lacks the special animation -> basic list (ASSUMED)
    speed_type: str            # attack | cast | movement
    entries: tuple             # data.animation.Entry
    player_multiplier: float
    weapon_offsets: tuple      # characterBaseAttackSpeed of the weapon(s) the action uses
    dual_wield_factor: float
    form: str | None           # form skill record whose table is used
    notes: tuple = ()


@dataclass(frozen=True)
class Timing:
    action_id: str
    speed_type: str
    speed_pct: float           # summed speed %, capped, without the weapon factor (min(raw, cap))
    speed_factor: float        # incl. weapon base speed and state factor
    frames: int | None         # quantized frames (ruleset rounding); None if default frames or mixed pool
    frames_alt: int | None     # the same with the other rounding rule (counterfactual, same playback)
    raw_frames: float | None
    time_s: float              # expected action time with the ruleset rounding
    time_alt_s: float          # ... with the other rounding rule (counterfactual)
    actions_per_s: float
    source: str                # assets | community | default | mixed
    confidence: str
    next_breakpoint_pct: float | None    # sheet speed % that saves one frame (single frame value only)
    breakpoint_reachable: bool
    notes: tuple = ()
    base_frames: float | None = None     # played frames of the pool's single value (None: mixed pool)
    skill_multiplier: float | None = None
    player_multiplier: float = 1.25
    rounding: str = "up"
    header_frames: float | None = None   # .anm keyframes (or header-equivalent) behind base_frames
    playback: str = "n-1"
    # rule pairs still open (KG-09) -> their frames (single-value pool)
    alt_rules: tuple = ()
    alt_frames: tuple = ()

    @property
    def breakpoint_measured(self) -> bool:
        """The next frame step is only worth naming when the base frames are measured."""
        return self.next_breakpoint_pct is not None and self.source in ("assets", "community")

    @property
    def span(self) -> tuple | None:
        """(fewest, most) frames over the configured and the still open rule pairs; None if equal."""
        if self.frames is None or not self.alt_frames:
            return None
        lo, hi = min(self.frames, *self.alt_frames), max(self.frames, *self.alt_frames)
        return None if lo == hi else (lo, hi)

    @property
    def other_frames(self) -> tuple:
        """Frame counts of the open rule pairs that differ from the configured one (sorted)."""
        return tuple(sorted({f for f in self.alt_frames if f != self.frames}))


def _weapons(ctx) -> dict:
    """{'main'|'off': (Class, characterBaseAttackSpeed)} of the active weapon set."""
    out = {}
    idx = ctx.index
    for w in getattr(ctx.character, "worn", ()) or ():
        if w.slot in ("Main hand", "Off hand"):
            r = idx.record(w.item.get("basename", "")) or {}
            f = r.get("f") or r
            klass = r.get("Class") or f.get("Class") or ""
            out["main" if w.slot == "Main hand" else "off"] = (str(klass), float(f.get("characterBaseAttackSpeed") or 0.0))
    return out


def active_form(tab: dict, sources) -> str | None:
    """Form skill whose table applies: a shapeshift toggle with points (Werewolf, Wereraven) or an
    item-granted form (Fangs). Several -> the first by record name (noted by the caller)."""
    forms = tab.get("forms") or {}
    act = sorted({s.record for s in sources.skills if s.record in forms and s.points > 0
                  and s.activity == Activity.TOGGLE}
                 | {g.skill_record for g in getattr(sources, "granted", ()) if g.skill_record in forms
                    and g.activity == Activity.TOGGLE})
    return act[0] if act else None


def profile(ctx, sources, action, tab: dict | None = None) -> Profile | None:
    """Animation profile of one ActionResult (None: no animation tables for this game version)."""
    tab = tab if tab is not None else AD.tables(getattr(ctx, "game_version", None) or "1.3.0.8", None,
                                                _fingerprint(ctx))
    if not tab:
        return None
    rn = action.action_id
    states = {s.record: s for s in sources.skills}
    st = states.get(rn)
    forms = tab.get("forms") or {}
    notes = []
    form = st.form if st is not None and st.form in forms else None
    if form is None:
        form = active_form(tab, sources)
        if form and len({s.record for s in sources.skills if s.record in forms and s.points > 0
                         and s.activity == Activity.TOGGLE}) > 1:
            notes.append("several forms with points: the first one's animations used (ASSUMED)")
    male = bool(getattr(ctx.character, "male", True))
    weapons = _weapons(ctx)
    rec = ctx.index.record(rn) or {}
    klass = str((rec.get("f") or rec).get("Class") or rec.get("Class") or "")
    kind = getattr(action, "kind", "attack")
    if kind == "proc":
        kind = "attack" if action.wd_pct else "spell"
    speed_type = "cast" if kind == "spell" else "attack"
    if klass in MOVEMENT:
        speed_type = "movement"
        notes.append("moves the character: travel time is not in the animation (ESTIMATE)")
    if form:
        table_key, prefix = forms[form], "unarmed"
        # .59 (KG-11): the form attacks with the weapons' speed, as the sheet shows (ASSUMED)
        used_f = [w for w in (weapons.get("main"), weapons.get("off")) if w and w[0] in AD.WEAPONS]
        offsets: tuple = () if speed_type == "cast" else tuple(w[1] for w in used_f[:2])
    else:
        table_key = "male" if male else "female"
        main, off = weapons.get("main"), weapons.get("off")
        prefix = AD.weapon_mode(main[0] if main else None, off[0] if off else None)
        used = [w for w in (main, off) if w and w[0] in AD.WEAPONS]
        if speed_type == "cast" or prefix == "unarmed":
            offsets = ()
        elif prefix in ("dHanded", "dualRanged"):
            offsets = tuple(w[1] for w in used[:2])
        else:
            offsets = tuple(w[1] for w in used[:1])
    overrides = [s.record for s in sources.skills if s.parent == rn and s.points > 0]
    name = AD.animation_name(tab, rn, overrides)
    entries, anim, found = AD.entries(tab, table_key, prefix, name, speed_type == "cast")
    if not found:
        notes.append(f"animation '{name}' not in the {prefix} table: basic list used (ASSUMED)")
    if not entries:
        return None
    pm = AD.player_multiplier(tab, male, speed_type)
    return Profile(rn, table_key, prefix, anim, found, speed_type, entries, pm, offsets,
                   float(tab.get("dw_weapon_speed_factor") or 0.5), form, tuple(notes))


def timing(prof: Profile, speeds: dict, ftab: dict, factor: float = 1.0, rounding: str = "up",
           extra_conf=(), playback: str = "n-1") -> Timing:
    """Action time of a profile at the given speeds ({'attack'|'cast': {'value', 'cap', 'raw'}})."""
    sp = speeds.get("cast" if prof.speed_type == "cast" else "attack") or {"value": 100.0, "cap": 0.0}
    shown = float(sp.get("value", 100.0))
    cap = float(sp.get("cap") or 0.0)
    raw = sp.get("raw")
    sf = AN.speed_factor(shown, cap, prof.weapon_offsets, prof.dual_wield_factor, form=prof.form is not None,
                         raw_pct=float(raw) if raw is not None else None,
                         slow_pct=float(sp.get("slow") or 0.0))
    sf_state = sf * max(factor, 0.0)
    notes = list(prof.notes)
    other = "nearest" if rounding == "up" else "up"
    if sf_state <= 0:
        return Timing(prof.action_id, prof.speed_type, shown, 0.0, None, None, None, 0.0, 0.0, 0.0,
                      "none", "EXACT", None, False, tuple(notes + ["no new actions in this state"]),
                      rounding=rounding)
    pool, srcs, confs, heads, hpool = [], set(), [], set(), []
    for e in prof.entries:
        fr = AD.base_frames(ftab, prof.table, prof.animation, e, prof.prefix)
        pool.append((AN.playback_frames(fr.frames, playback), e.skill_multiplier, e.weight))
        hpool.append((fr.frames, e.skill_multiplier, e.weight))
        heads.add(fr.frames)
        srcs.add(fr.source)
        confs.append(fr.confidence)
    t_main = AN.pool_time(pool, prof.player_multiplier, sf_state, rounding)
    t_alt = AN.pool_time(pool, prof.player_multiplier, sf_state, other)
    alts = tuple(AN.alternative_rules(rounding, playback))

    def _pool(pb):
        return [(AN.playback_frames(h, pb), m, w) for h, m, w in hpool]
    alt_times = [AN.pool_time(_pool(pb), prof.player_multiplier, sf_state, rd) for rd, pb in alts]
    uniform = len({(f, m) for f, m, _w in pool}) == 1
    source = srcs.pop() if len(srcs) == 1 else "mixed"
    frames = frames_alt = raw = base = mult = None
    alt_frames: tuple = ()
    bp, reach = None, False
    if uniform:
        f0, m0, _ = pool[0]
        base, mult = f0, m0
        raw = AN.raw_frames(f0, prof.player_multiplier, m0, sf_state)
        if source != "default":
            frames, frames_alt = AN.quantize(raw, rounding), AN.quantize(raw, other)
            h0 = hpool[0][0]
            alt_frames = tuple(AN.quantize(AN.raw_frames(AN.playback_frames(h0, pb), prof.player_multiplier,
                                                         m0, sf_state), rd) for rd, pb in alts)
        need = AN.next_breakpoint(f0, prof.player_multiplier, m0, sf_state, rounding)
        if need is not None and factor > 0:
            # .59: as a SHEET value (the sheet includes the weapon speed, KG-11) - the cap limits it
            slow = float(sp.get("slow") or 0.0)
            pct = need / max(factor, 1e-9) / max(1.0 - slow / 100.0, 0.05) * 100.0
            bp, reach = pct, (not cap or pct <= cap + 1e-6)
    conf = weakest("DERIVED" if prof.form else "EXACT", *confs,
                   "DERIVED" if playback == "n-1" else "ASSUMED",
                   "ASSUMED" if not prof.found else "EXACT",
                   "ASSUMED" if prof.weapon_offsets else "EXACT",
                   "ESTIMATE" if prof.speed_type == "movement" else "EXACT",
                   AN.rounding_confidence(rounding, playback) if abs(t_main - t_alt) > 1e-12 else "EXACT",
                   *extra_conf)
    if source in ("default", "mixed"):
        notes.append("frames not verified: default frames (ESTIMATE)")
    if source in ("assets", "mixed") and ftab.get("assets_note"):
        notes.append(str(ftab["assets_note"]))
    if (rounding, playback) in AN.EXCLUDED_RULES and abs(t_main - t_alt) > 1e-12:
        notes.append(f"rule pair ({rounding}, {playback}) is contradicted by the measurements (KG-09) - ASSUMED")
    open_range = (min(alt_times), max(alt_times)) if alt_times else ()
    if open_range and (open_range[0] < t_main - 1e-12 or open_range[1] > t_main + 1e-12):
        notes.append("playback rule matters here (keyframes - 1 or all keyframes, DERIVED)")
    return Timing(prof.action_id, prof.speed_type, shown, sf_state, frames, frames_alt, raw, t_main, t_alt,
                  1.0 / t_main if t_main > 0 else 0.0, source, conf, bp, reach, tuple(notes), base, mult,
                  prof.player_multiplier, rounding, (heads.pop() if uniform and len(heads) == 1 else None),
                  playback, alts, alt_frames)


def _fingerprint(ctx) -> str:
    return str((getattr(getattr(ctx, "index", None), "meta", None) or {}).get("fingerprint") or "")


class FrameModel:
    """Profiles of one character, timings per state on demand (cached per action and factor)."""

    def __init__(self, ctx, sources, rounding: str | None = None):
        ver = getattr(ctx, "game_version", None) or "1.3.0.8"
        self.tab = AD.tables(ver, None, _fingerprint(ctx))
        self.ftab = AD.frames_table(ver)
        rs = getattr(ctx, "ruleset", None) or {}
        self.rounding = rounding or (rs.get("frame_rounding") if hasattr(rs, "get") else None) or "up"
        self.playback = (rs.get("anim_playback") if hasattr(rs, "get") else None) or "n-1"
        self.ctx, self.sources = ctx, sources
        used = str(self.tab.get("_version") or "")
        self.version_note = None
        bad = [f"{k} '{v}'" for k, v, ok in (("frame_rounding", self.rounding, AN.ROUNDING),
                                              ("anim_playback", self.playback, AN.PLAYBACK)) if v not in ok]
        if bad:                                    # a broken ruleset never stops the report (review .52)
            self.rounding = self.rounding if self.rounding in AN.ROUNDING else "up"
            self.playback = self.playback if self.playback in AN.PLAYBACK else "n-1"
            self.version_note = f"unknown ruleset value {', '.join(bad)} - default used (ASSUMED)"
        tab_note = self.tab.get("_note") if self.tab else None
        if self.tab and "_note" not in self.tab and used and used != str(ver):   # a table without resolver note
            tab_note = (f"animation tables of game version {used} used for {ver} - slot multipliers "
                        "not checked for this version (ASSUMED)")
        if tab_note:
            self.version_note = f"{self.version_note}; {tab_note}" if self.version_note else tab_note
        self._prof: dict = {}
        self._cache: dict = {}

    @property
    def available(self) -> bool:
        return bool(self.tab)

    def profile(self, action) -> Profile | None:
        if action.action_id not in self._prof:
            self._prof[action.action_id] = profile(self.ctx, self.sources, action, self.tab) if self.tab else None
        return self._prof[action.action_id]

    def timing(self, action, speeds: dict, factor: float = 1.0) -> Timing | None:
        prof = self.profile(action)
        if prof is None:
            return None
        # .62 (code review): every speed input of timing() is in the key - raw, slow and the weapon
        # factor change the result since .58 while value/cap can stay the same
        key = (action.action_id, round(factor, 9),
               tuple(sorted((k, v.get("value"), v.get("cap"), v.get("raw"), v.get("slow"), v.get("weapon_factor"))
                            for k, v in speeds.items())))
        if key not in self._cache:
            t = timing(prof, speeds, self.ftab, factor, self.rounding, ("ASSUMED",) if self.version_note else (),
                       self.playback)
            if self.version_note:
                t = dataclasses.replace(t, notes=t.notes + (self.version_note,))
            self._cache[key] = t
        return self._cache[key]
