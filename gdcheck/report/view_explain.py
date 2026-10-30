"""Report v2, block 4 of the view: the explanation tables (spec 3.3 row "Score und Stufen ... Datenbasis", mockup block 4).

`explain(a, versions, meta=None)` returns the five collapsed tables under the report - the score and the stages, the targets by
area, the terms, the effort tiers and the data basis - as `view.XTable` objects. Every row that a "Why", "How rated" or "What
this means" link of blocks 1 to 3 points at has its id (`X_IDS`, the fixed list of the spec). The numbers are read, not typed:
the bands from `readiness`, the gates from `stage.MID_GATES`, the targets of the character from `targets.json`
(`targets.threshold_for`, for the archetype and the profile of the character), the thresholds of the build check from its module.

The text of a row is for the reader of the report; this block may use the words the Player View avoids (HEURISTIC, ESTIMATE
are the basis labels of the mockup)."""
from __future__ import annotations

import re

from ..analysis import area_facts as AF
from ..analysis import build_quality as BQ
from ..analysis import guide as GD
from ..analysis import readiness as RD
from ..analysis import stage as STG
from ..analysis import targets as TGT
from ..analysis.proposals import effort as EF
from ..domain.enums import Stage
from . import common as CM
from . import view as V

X_IDS = ("x-score", "x-gates", "x-est",
         "x-health", "x-res", "x-leech", "x-absorption", "x-armor", "x-physical", "x-da", "x-cc", "x-sustain", "x-dummy", "x-hit",
         "x-rr", "x-skills", "x-bosses",
         "x-sheet", "x-combat", "x-selfpen", "x-ointment", "x-slot", "x-power", "x-value", "x-attributes",
         "x-effort", "x-data")

EST = ("Model estimate, not a forecast. Each proposal is recalculated on the whole character; damage and resistance reduction "
       "are not re-rated (est.). Consumables count while they run.")
_BASIS_ORDER = ("fact", "community")
_EFFORT = {"Now": "In your bags or your stash, or a free click.",
           "Vendor": "Craft at the Blacksmith or buy from a faction vendor you already have the reputation for.",
           "Respec": "Spirit Guide (skills, devotions) or Tonic of Reshaping (attributes).",
           "Farm": "Needs something you do not have yet: a higher reputation, more cooldown reduction or an Inventor reroll."}
_TERMS = (
    ("x-sheet", "Character sheet", "Permanent bonuses and toggles, as the game shows them."),
    ("x-combat", "Combat value", "Character sheet plus buffs you keep up at least 95% of the time."),
    ("x-selfpen", "Self-penalty", "A skill that lowers your own stats permanently. Always listed first."),
    ("x-ointment", "Ointment slot", "Only one ointment works at a time, 15 minutes. Elixirs combine with it."),
    ("x-slot", "One change per slot", "Two proposals for the same item slot exclude each other."),
    ("x-power", "Celestial Power", "Works only when bound to a skill in the devotion screen."),
    ("x-value", "Value points",
     "One common scale for skill and devotion points. 1 point is about +1% main damage or Health."),
    ("x-attributes", "Attribute points",
     "Physique gives Health and Defensive Ability. Cunning and Spirit only for item requirements or deliberate scaling."))


def _n(x: float) -> str:
    return f"{x:,.0f}"


# ---------------------------------------------------------------------------
# targets
# ---------------------------------------------------------------------------

def _basis(data: dict, metrics, extra: str = "") -> str:
    """The basis of the targets that measure `metrics`: "fact", "community" or "fact + community", then the fixed label."""
    found = {t["basis"] for t in data["targets"] if t["metric"] in metrics}
    label = " + ".join(b for b in _BASIS_ORDER if b in found)
    return f"{label}, {extra}" if label and extra else label or extra


class _Targets:
    """The thresholds of the character's own targets: its archetype (melee or not) and profile pick the row of targets.json."""

    def __init__(self, a):
        self.data, self.melee, self.profile = TGT.load(), a.archetype == "melee", a.ctx.profile.value
        self.base_cap = a.ctx.ruleset.get("resist_base_cap", 80) if getattr(a.ctx, "ruleset", None) else 80

    def at(self, metric: str, stage: Stage, fmt) -> str:
        v = TGT.threshold_for(self.data, metric, stage, self.melee, self.profile)
        return "none" if v is None else fmt(v)


def _row(t: _Targets, rid: str, name: str, what: str, metric: str, fmt, basis: str) -> V.XRow:
    return V.XRow(rid, (name, what, t.at(metric, Stage.MID, fmt), t.at(metric, Stage.ENDGAME, fmt)), basis)


def _targets_table(a) -> V.XTable:
    t = _Targets(a)
    pct, num, secs = (lambda v: f"{v:g}%"), (lambda v: f"{v:,.0f}"), (lambda v: f"{v:g} s")
    cc_names = ["Freeze", "Stun"] + (["Slow"] if t.at("slow_res", Stage.ENDGAME, pct) != "none" else [])
    w, kinds = AF.RR_WEIGHTS, CM.number_word(len(AF.RR_WEIGHTS)).capitalize()
    all_kinds = lambda stage: t.at("rr_kinds", stage, lambda v: f"all {len(w)}")                  # noqa: E731

    def each(stage) -> str:
        """The crowd control target of a stage: "50% each" when freeze and stun share it."""
        found = {t.at(m, stage, pct) for m in ("freeze_res", "stun_res")}
        return "none" if found == {"none"} else f"{found.pop()} each" if len(found) == 1 else "see the targets"
    b = lambda *m, extra="": _basis(t.data, m, extra)                                             # noqa: E731
    rows = [
        _row(t, "x-health", "Health", "Character sheet. Melee target when the main attack uses weapon damage.", "health", num,
             b("health")),
        V.XRow("x-res", ("Resistances", "After the Ultimate penalty, against 80% and then your own cap (80 plus max bonuses, "
                         "up to 95).", f"{t.base_cap:g}%", "your cap"), b("res_ultimate_count", "res_at_cap_count")),
        _row(t, "x-leech", "Life Leech Resistance", "Character sheet. Ultimate takes 25 off.", "leech_res", pct, b("leech_res")),
        _row(t, "x-absorption", "Armor Absorption", "Character sheet.", "absorption", pct, b("absorption")),
        _row(t, "x-armor", "Armor", "Armor weighted by the zone it covers: chest and legs count most.", "armor_weighted", num,
             b("armor_weighted")),
        _row(t, "x-physical", "Physical Resistance", "Combat value.", "physical_combat", pct, b("physical_combat")),
        _row(t, "x-da", "Defensive Ability", "Combat value.", "da_combat", num, b("da_combat")),
        V.XRow("x-cc", ("Crowd Control", f"{', '.join(cc_names[:-1])} and {cc_names[-1]} Resistance, combat value.",
                        each(Stage.MID), each(Stage.ENDGAME)), b("freeze_res", "stun_res", "slow_res")),
        V.XRow("x-sustain", ("Sustain", f"Life leech {AF.LIFE_LEECH_MIN:.0f}% or more, a low-health trigger up to "
                                        f"{AF.TRIGGER_MAX_PCT:.0f}% Health, a panic button of {AF.PANIC_MIN_PCT:.0f}% Health or "
                                        "more. Two of three is OK.", "2 of 3", "2 of 3"), "HEURISTIC"),
        _row(t, "x-dummy", "Damage", "Time until the Training Dummy (Ultimate, your level +2) reaches its shield at 15% life.",
             "dummy_ttk", secs, b("dummy_ttk", extra="ESTIMATE")),
        _row(t, "x-hit", "Hit chance", "Your chance to hit a boss that has 3,000 Defensive Ability after your shred.",
             "pth_vs_boss_da", num, b("pth_vs_boss_da")),
        V.XRow("x-rr", ("Resistance Reduction", f"{kinds} kinds on your weakest main type: stacking (weight {w[0]:g}), reduced "
                        f"({w[1]:g}), flat ({w[2]:g}). A kind that is not permanent counts half.", all_kinds(Stage.MID),
                        all_kinds(Stage.ENDGAME)), b("rr_kinds")),
        V.XRow("x-skills", ("Skills", "Points by value. Attacks and spells are not rated.", f"under {BQ.RISK_FROM:.0f} wasted",
                            f"under {BQ.RISK_FROM:.0f} wasted"), "HEURISTIC"),
        V.XRow("x-bosses", ("Boss risk", "Preparation check against the boss profile from the game database. High: a "
                            "resistance more than 10 short, two ointments needed at once, or three medium reasons.",
                            "no forecast", "no forecast"), "HEURISTIC"),
    ]
    return V.XTable("t-targets", "Targets by area", ("Area", "What counts", "Mid", "Endgame", "Basis"), tuple(rows))


# ---------------------------------------------------------------------------
# score, gates
# ---------------------------------------------------------------------------

def _gates_text(a) -> str:
    t, mg = _Targets(a), STG.MID_GATES
    level = TGT.threshold_for(t.data, "level", Stage.MID, False, "any") or 100
    absorption = t.at("absorption", Stage.MID, lambda v: f"{v:g}%")
    return (f"Level {level:g} plus {CM.number_word(len(STG.GATE_IDS) - 1)} more: resistances at {t.base_cap:g}% on Ultimate, Health "
            f"{_n(mg['health_melee'])} (melee) or {_n(mg['health_other'])}, Defensive Ability {_n(mg['da'])}, Physical Resistance "
            f"{mg['physical']:g}%, Armor Absorption {absorption}. A rest up to {STG.ABSORPTION_NEGLIGIBLE * 100:g}% of Health "
            "counts as met.")


def _score_text(a) -> str:
    low = {name: (lo, hi) for name, lo, hi in RD.bands()}
    bands = ", ".join(f"{word} {low[key][0]} to {low[key][1]}" for word, key in (("Early", "EARLY"), ("Mid", "MID"),
                                                                              ("Endgame", "ENDGAME")))
    text = (f"0 to 100. {bands}. The score counts only as far as resistances, Armor Absorption and resistance reduction are met.")
    if a.ctx.profile.value == "ascendant":
        text += " Ascendant uses the General Ultimate targets; monster values use the Ascendant adjustment."
    return text


def _score_table(a) -> V.XTable:
    rows = (V.XRow("x-score", ("Build Readiness", _score_text(a)), "engine rule"),
            V.XRow("x-gates", ("Endgame gates", _gates_text(a)), _basis(TGT.load(), ("level", "res_ultimate_count", "health",
                                                                                      "da_combat", "physical_combat",
                                                                                      "absorption"))),
            V.XRow("x-est", ("est.", EST), "ESTIMATE"))
    return V.XTable("t-score", "Score and stages", ("Item", "Rule", "Basis"), rows)


# ---------------------------------------------------------------------------
# terms, effort, data
# ---------------------------------------------------------------------------

def _terms_table() -> V.XTable:
    return V.XTable("t-terms", "Terms", ("Term", "Meaning"), tuple(V.XRow(rid, (term, text)) for rid, term, text in _TERMS))


def _effort_table() -> V.XTable:
    rows = tuple(V.XRow("x-effort" if i == 0 else "", (tier, _EFFORT[tier])) for i, tier in enumerate(EF.TIERS))
    return V.XTable("t-effort", "Effort", ("Effort", "Meaning"), rows)


def _data_text(a, versions: dict, meta) -> str:
    ctx, state = a.ctx, CM.version_state(a.ctx)
    m = re.fullmatch(r"index (\S+) of (\S+)", str(versions.get("index", "")))
    head = f"{m.group(1)}, index of {m.group(2)}." if m else f"{versions.get('index', 'Game data')}."
    tail = {"live": "Checked against the live patch.",
            "installed": "Matches your installed game. Live patch not checked: if a newer patch is out, item values may be "
                         "outdated.",
            "unconfirmed": "Grim Dawn has not run since its last update: version not confirmed.",
            "mismatch": f"The data does not match {ctx.options.live_version or 'your installed game'}. Item values and rules "
                        "may be outdated.",
            "unchecked": "Live patch not checked: if a newer patch is out, item values may be outdated."}[state]
    parts = [head, tail]
    if meta and meta.get("mechanics_note"):
        parts.append(str(meta["mechanics_note"]))
    n = GD.unresolved(a.sources)
    if n:
        parts.append(f"{n} record{'' if n == 1 else 's'} of your save {'is' if n == 1 else 'are'} not in the game data; "
                     f"{'it is' if n == 1 else 'they are'} not rated.")
    return " ".join(parts)


def _data_table(a, versions: dict, meta) -> V.XTable:
    rows = [V.XRow("x-data", ("Game data", _data_text(a, versions, meta))),
            V.XRow("", ("Rules", f"Ruleset {versions.get('ruleset', '?')}, targets {versions.get('targets', '?')}, "
                                 f"engine {versions.get('skill', '?')}")),
            V.XRow("", ("Bosses", str(versions.get("boss_data", "none")))),
            V.XRow("", ("Not modelled", "Item attribute requirements. Boss damage is a model."))]
    notes = [str(x) for x in versions.get("reference_notes") or ()]
    if notes:
        rows.append(V.XRow("", ("Reference data", " ".join(notes))))
    if a.ctx.options.detail and not getattr(a.ctx.index, "loot_tabellen", None):
        rows.append(V.XRow("", ("Rerolls", "Rerolls need the loot tables (not in this game data).")))
    return V.XTable("t-data", "Data basis", ("Item", "Value"), tuple(rows))


def explain(a, versions: dict, meta: dict | None = None) -> tuple:
    """Block 4 of the report view. `versions` is `pipeline.versions(index)`, `meta` the report meta (its mechanics note is part of
    the data row)."""
    return (_score_table(a), _targets_table(a), _terms_table(), _effort_table(), _data_table(a, versions, meta))
