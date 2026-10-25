"""Animation frames -> action time (pure; PDF 'Frames, Animationsgeschwindigkeit und DPS V1',
checked 2026-10-01 against the sources and the game database; playback rule .52).

    base_frames = playback_frames(header keyframes)          (N-1 frame steps, ruleset R4)
    raw_frames = base_frames / player_multiplier / skill_multiplier / speed_factor
    frames     = quantize(raw_frames)                       (engine frame steps)
    actions/s  = 30 / frames

- 30 animation frames = 1 second: Zantai (Crate, 2020) and the FoA mechanics test (2026); every
  .anm file of the game stores 30 fps in its header (GrimDawner GameData.md).        EXACT
- playback: an .anm header holds N keyframes (frames 0..N-1) -> N-1 frame steps. Zantai (Crate
  forum 99960): basic attack 18 and Belgothian's Shears 28 frames "before modifiers"; the 1.3.0.8
  headers hold 19 and 29. Long files hold 601/721/961/1201 keyframes (20/24/32/40 s). The FoA
  values equal the headers (most likely read from them). Rule parameter 'n-1' (default) | 'n'. DERIVED
- player multiplier 1.25: Zantai; game database malepc01.dbr characterAttackSpeed and
  characterSpellCastSpeed = 1.25 (read from the database, not hard coded here).      EXACT
- skill multiplier: the animation slot's '...AnimSpeedN' in the animation table.     EXACT per version
- quantization (rule pair rounding x playback): two measurements, both with played frames below
  x.5 under N-1, exclude 'nearest' with N-1 and unrounded times without a per-action delay -
  Crate forum 153788 (mi023: 19-keyframe skills at 200% 'indeed ~3.7'/s: N-1 7.2 -> up 8 = 3.75,
  nearest 7 = 4.29) and the user video of 02.10.2026 (Cpt Wolf, Feral Claws 27 keyframes at 227%
  sheet speed, 60 fps VSync: 340 +- 3 ms per action; N-1 9.16 -> up 10 = 333 ms, nearest 9 = 300 ms;
  KG-09). 'up' given N-1 is TESTED; still open: playback N (with up or to nearest), a per-action
  delay ('nearest + one frame') and the render-frame share of the +2% (VSync 60). The report shows
  the rule pairs still open as the span (alternative_rules), not the excluded ones.
- speed factor: min(summed speed % x (1 + weapon base attack speed), cap) x (1 - slow) / 100 (ASSUMED
  form of the pre-1.3 forum formula, .58); shapeshift forms use the weapons in hand (.59, ASSUMED);
  casts use no weapon speed.
"""
from __future__ import annotations

import math

from .policy import Confidence, rule

FRAMES_PER_SECOND = 30.0
EPS = 1e-4                       # float32 database values: 1.3999999 must not add a frame
ROUNDING = ("up", "nearest")
PLAYBACK = ("n-1", "n")


@rule("animation_playback", Confidence.DERIVED,
      "Zantai (Crate forum 99960): basic attack 18 / Belgothian's Shears 28 frames before modifiers = .anm "
      "headers 19 / 29 minus 1 (N keyframes = N-1 frame steps)")
def playback_frames(header_frames: float, rule: str = "n-1") -> float:
    """Frames the engine plays for an animation with `header_frames` keyframes."""
    if rule not in PLAYBACK:
        raise ValueError(f"unknown playback rule: {rule!r}")
    f = float(header_frames)
    return max(f - 1.0, 1.0) if rule == "n-1" else max(f, 1.0)


@rule("animation_raw_frames", Confidence.EXACT,
      "Zantai (Crate forum 99960): basic attack 18 frames / 1.25 = 14.4; 30 frames = 1 s")
def raw_frames(base_frames: float, player_multiplier: float, skill_multiplier: float,
               speed_factor: float) -> float:
    m = max(player_multiplier, 1e-9) * max(skill_multiplier, 1e-9) * max(speed_factor, 1e-9)
    return max(float(base_frames), 0.0) / m


@rule("animation_quantize", Confidence.TESTED,
      "given N-1 played frames: rounding up - Crate forum 153788 (19 keyframes at 200%: ~3.7/s = 8 frames; "
      "nearest 7 = 4.29/s) and user video 02.10.2026 (Feral Claws 27 keyframes at 227%: 340 +- 3 ms = "
      "10 frames + 2%; nearest 9 = 300 ms); 2 data points, both below x.5, no per-action delay assumed")
def quantize(raw: float, rounding: str = "up") -> int:
    if rounding not in ROUNDING:
        raise ValueError(f"unknown rounding rule: {rounding!r}")
    if raw <= 0:
        return 1
    if rounding == "up":
        return max(1, math.ceil(raw - EPS))
    return max(1, math.floor(raw + 0.5 + EPS))


# Rule pairs (rounding, playback) the measurements exclude (KG-09): forum 153788 and the video of
# 02.10.2026 both measured the 'up' frame count where 'nearest' with N-1 gives one frame less.
EXCLUDED_RULES = frozenset({("nearest", "n-1")})


def alternative_rules(rounding: str = "up", playback: str = "n-1") -> list:
    """Rule pairs (rounding, playback) still open besides the configured one - the report's span.
    Default (up, n-1): [(up, n), (nearest, n)]."""
    return [(r, p) for p in PLAYBACK for r in ROUNDING
            if (r, p) != (rounding, playback) and (r, p) not in EXCLUDED_RULES]


def rounding_confidence(rounding: str = "up", playback: str = "n-1") -> str:
    """Confidence of the rounding where it matters: TESTED for 'up' with N-1 (two measurements),
    ASSUMED otherwise (open under N; contradicted for the excluded pairs)."""
    return "TESTED" if (rounding, playback) == ("up", "n-1") else "ASSUMED"


def actions_per_second(frames: float) -> float:
    return FRAMES_PER_SECOND / frames if frames and frames > 0 else 0.0


def action_time(frames: float) -> float:
    return frames / FRAMES_PER_SECOND if frames and frames > 0 else 0.0


@rule("animation_pool", Confidence.DERIVED,
      "a pool picks one animation per action by weight: long-run rate = 1 / E[action time], each entry "
      "quantized on its own")
def pool_time(entries, player_multiplier: float, speed_factor: float, rounding: str = "up") -> float:
    """entries: [(base_frames, skill_multiplier, weight)]; expected action time in seconds."""
    total_w = sum(max(w, 0.0) for _f, _m, w in entries)
    if total_w <= 0:
        return 0.0
    t = 0.0
    for f, m, w in entries:
        t += max(w, 0.0) * action_time(quantize(raw_frames(f, player_multiplier, m, speed_factor), rounding))
    return t / total_w


@rule("weapon_speed_factor", Confidence.ASSUMED,
      "min((1 + weapon base attack speed) x summed sheet speed, cap): the weapon speed multiplies, the cap limits "
      "the result (Crate forum 35306 'Character sheet attack speed = (IBV+WSM*100)*(1+AS/100)', 35411); dual wield "
      "averages the two weapons (gameengine dwWeaponSpeedFactor 0.5); pre-1.3 community formula")
def speed_factor(sheet_speed_pct: float, cap_pct: float, weapon_offsets=(), dual_wield_factor: float = 0.5,
                 form: bool = False, raw_pct: float | None = None, slow_pct: float = 0.0) -> float:
    """Speed factor of an action (1.0 = 100 %). `raw_pct` (uncapped 100 + bonuses) when known, else the
    sheet value; the weapon's base speed multiplies, then the cap applies (.58 - before: cap first),
    then a slow. Casts pass no weapon offsets. Shapeshift forms use the weapons in hand like the
    sheet does (.59, KG-11: Cpt Wolf's sheet shows 227% in the form - below the 230% cap, which the
    summed 239% without the weapon factor could not show; players report dual wield faster in the
    form) - ASSUMED; `form` is kept for callers and no longer changes the factor."""
    off = weapon_offset(weapon_offsets, dual_wield_factor)
    base = raw_pct if raw_pct is not None else sheet_speed_pct
    shown = max(base, 1.0) * max(1.0 + off, 0.05)
    if cap_pct:
        shown = min(shown, cap_pct)
    return shown * max(1.0 - slow_pct / 100.0, 0.05) / 100.0


def weapon_offset(weapon_offsets=(), dual_wield_factor: float = 0.5) -> float:
    """characterBaseAttackSpeed of the weapon(s) used: one weapon as is, two weapons averaged."""
    offs = [float(o or 0.0) for o in weapon_offsets]
    if len(offs) >= 2:
        return (offs[0] + offs[1]) * dual_wield_factor
    return offs[0] if offs else 0.0


def next_breakpoint(base_frames: float, player_multiplier: float, skill_multiplier: float,
                    speed: float, rounding: str = "up") -> float | None:
    """Smallest speed factor that saves one more frame, None at 1 frame (rounding: rule pair of KG-09)."""
    frames = quantize(raw_frames(base_frames, player_multiplier, skill_multiplier, speed), rounding)
    if frames <= 1:
        return None
    # 'up': raw <= frames - 1 (+EPS) gives frames - 1; 'nearest': raw < frames - 0.5 - EPS
    target = frames - 1 if rounding == "up" else frames - 0.5 - 2 * EPS
    return base_frames / (player_multiplier * skill_multiplier * target) + 1e-9
