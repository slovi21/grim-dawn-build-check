"""AnalysisContext - everything a run depends on, passed explicitly (ADR-5).
No module keeps run state in globals."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..data import gametables as GT
from .character import Character
from .enums import ContentProfile, Difficulty, LiveStatus

RULESET_FILE = Path(__file__).resolve().parent.parent / "rules" / "ruleset.json"


def load_ruleset(path: Path = RULESET_FILE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _vkey(label) -> tuple | None:
    parts = str(label or "").strip().split(".")
    return tuple(int(p) for p in parts) if parts and all(p.isdigit() for p in parts) else None


def effective_ruleset(ruleset: dict, game_version: str) -> tuple[dict, tuple, tuple]:
    """(rules, notes, keys set by a not yet confirmed block) for a game version (ruleset R5 'by_version', critic review .53):
    every override block whose 'since' version is <= the game version applies, oldest first, compared
    as integer tuples (1.3.1 also covers 1.3.2; 1.3.10 > 1.3.9). Blocks marked ASSUMED (values from a
    playtest) are named in the notes; an unknown label ('?', 'db...') keeps the base rules + a note."""
    blocks = ruleset.get("by_version") or {}
    if not blocks:
        return ruleset, (), ()
    gv = _vkey(game_version)
    if gv is None:
        return ruleset, (f"game version label '{game_version}' unknown - base rules {ruleset.get('version', '?')} "
                         "used, version-specific rules not applied (ASSUMED)",), ()
    rules, notes, keys = dict(ruleset), [], []
    for since in sorted((k for k in blocks if _vkey(k) is not None), key=_vkey):
        if _vkey(since) > gv:
            continue
        block = blocks[since] or {}
        vals = {k: v for k, v in block.items() if not k.startswith("_")}
        rules.update(vals)
        if str(block.get("_status", "")).upper() in ("ASSUMED", "PLAYTEST"):
            keys += list(vals)
            notes.append(f"rules since {since}: " + ", ".join(f"{k} {v}" for k, v in vals.items())
                         + f" - {block.get('_source', 'not confirmed')} (ASSUMED)")
    return rules, tuple(notes), tuple(dict.fromkeys(keys))


@dataclass(frozen=True)
class Options:
    profile: ContentProfile = ContentProfile.GENERAL
    hardcore: bool | None = None            # None = from the savefile
    difficulty: int | None = None           # None = last played (savefile)
    live_version: str | None = None         # from Claude's web check
    live_checked: str | None = None         # date of that check
    installed_fingerprint: str | None = None  # GD-Tools: fingerprint of the local game data
    version_unconfirmed: bool = False       # GD-Tools: the version label comes from a stale log.html
    onslaught: int | None = None            # Onslaught stacks for the spender (None = 0, worst case)
    boss_level: int | None = None           # monster level for boss values (None = player level, plan E3)
    action_rate: float | None = None        # actions per second override for DpS/sustain (None = from speed)
    detail: bool = False
    build_quality: bool = True              # .72: rate skills and devotions in every analysis (analysis/build_quality.py)
    counterfactual_max_evals: int = 80     # fixed work budget (deterministic), not seconds
    top_k: int = 5


@dataclass(frozen=True)
class AnalysisContext:
    index: object
    character: Character
    options: Options
    ruleset: dict
    difficulty: Difficulty
    penalty: dict                           # resist field -> penalty (positive)
    penalty_source: str
    ultimate_penalty: dict
    attribute_constants: dict
    speed_caps: dict
    game_version: str
    ruleset_version: str
    hardcore: bool
    live_status: LiveStatus = LiveStatus.NOT_CHECKED
    extra: dict = field(default_factory=dict, compare=False)
    rule_notes: tuple = ()                  # version-specific rules that are not confirmed (R5)
    rule_overrides: tuple = ()              # ruleset keys set by a not yet confirmed (ASSUMED) block

    @property
    def profile(self) -> ContentProfile:
        return self.options.profile


def live_status(index, options: Options) -> LiveStatus:
    gv = getattr(index, "game_version", "?")
    if options.live_version:
        return LiveStatus.MATCHES_LIVE if options.live_version == gv else LiveStatus.MISMATCH
    if options.installed_fingerprint:
        return LiveStatus.MATCHES_INSTALLED if options.installed_fingerprint == getattr(
            index, "fingerprint", "") else LiveStatus.MISMATCH
    return LiveStatus.NOT_CHECKED


def make_context(index, character: Character, options: Options | None = None,
                 ruleset: dict | None = None) -> AnalysisContext:
    options = options or Options()
    ruleset, notes, keys = effective_ruleset(ruleset or load_ruleset(), getattr(index, "game_version", "?"))
    diff = Difficulty(options.difficulty if options.difficulty is not None else character.difficulty)
    pen, src = GT.resist_penalty(index, diff.value)
    ult, _ = GT.resist_penalty(index, 2)
    attr = {**ruleset.get("attribute_defaults", {}), **GT.attribute_constants(index)}
    return AnalysisContext(
        index=index, character=character, options=options, ruleset=ruleset, difficulty=diff,
        penalty=pen, penalty_source=src, ultimate_penalty=ult, attribute_constants=attr,
        speed_caps=GT.speed_caps(index), game_version=getattr(index, "game_version", "?"),
        ruleset_version=ruleset.get("version", "?"),
        hardcore=character.hardcore if options.hardcore is None else options.hardcore,
        live_status=live_status(index, options), rule_notes=notes, rule_overrides=keys)
