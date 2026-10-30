"""Report v2, block 2 of the view: the area panels (spec 3.3 rows "Health" to "Sustain, Skills, Devotions", mockup block 2).

`groups(a, g)` takes the finished analysis and its guide and returns the three groups of the mockup - Defense (Health,
Survival, Resistances, Crowd Control, Bosses), Offense (Damage, Resistance Reduction) and Build (Sustain, Skills, Devotions) -
as `view.Group` objects. It chooses and words; it rates nothing: statuses come from `a.areas` and the target results, targets
from `a.results` and `targets.json` (`targets.threshold_for`), the proposals from the guide.

Axes (plan task 9): Health, Defensive Ability and Physical Resistance run from 0 to 1.25 x the largest of value, Endgame target
and stage target; Armor Absorption from 70 to 100; Life Leech Resistance from -50 to 50; Freeze, Stun and Slow Resistance from 0 to
100; the Training Dummy time from 0 to the larger of the slow end of its range and the Mid target. The dashed part of a meter is
the best change the guide shows for the value; the boss matrix lists one row per fight with the most frequent weak spots as
columns (COLUMNS_MAX).

Every key line, note and boss line has at most KEY_WORDS words. A panel never has an empty value: a special character (pet build,
levelling, no damage, no boss data) gets a key line that says why the panel has nothing to show."""
from __future__ import annotations

import functools
import math
import re
from collections import Counter
from typing import NamedTuple

from ..analysis import archetype as ARCH
from ..analysis import area_facts as AF
from ..analysis import build_quality as BQ
from ..analysis import guide as GD
from ..analysis import readiness as RD
from ..analysis import stage as STG
from ..analysis import targets as TGT
from ..domain.enums import Stage
from . import common as CM
from . import text_util as TU
from . import view as V
from .view_overview import LEVER_MIN

KEY_WORDS = 20             # the key line, the note under the meters and the line under the boss table (spec 3.7)
CHECKS_MAX = 3             # checks of one warning rule in a panel (the rest is counted in one more)
COLUMNS_MAX = 4            # boss matrix: the most frequent weak spots
BOSS_ROWS = 6              # boss matrix: fights shown; the rest is counted in one line
LABEL_MAX = 97.0           # a label at the end of a bar keeps clear of the edge (mockup)
LABEL_EDGE = 3.0           # ... and a label wider than one character at the start
LABEL_GAP = 10.0           # two labels on one axis need this many points between them
AXIS_ROOM = 1.25           # Health, Defensive Ability, Physical Resistance: axis = this x the largest mark
SHOWN_TYPES = 2            # resistances named in the key line of the board
HOW_RATED = {"p-health": "x-health", "p-survival": "x-physical", "p-res": "x-res", "p-cc": "x-cc", "p-bosses": "x-bosses",
             "p-damage": "x-dummy", "p-rr": "x-rr", "p-sustain": "x-sustain", "p-skills": "x-skills",
             V.DEVOTIONS_PANEL: "x-power"}
SKILL_KINDS = ("refund", "skills", "attributes")             # the proposals the Skills panel counts
DEVOTION_KINDS = ("bind", "devotion", "devotion_swap")       # ... and the Devotions panel
_WORD = {"OK": "OK", "RISK": "Risk", "GAP": "Gap", "INFO": "Info"}
_RESULT_WORD = {"ok": "OK", "tight": "Risk", "gap": "Gap", "info": "Info"}
_TONE = {"OK": "ok", "Risk": "risk", "Gap": "gap", "Info": "info"}
_STAGE_WORD = {Stage.MID: "Mid", Stage.ENDGAME: "Endgame"}
_RES_NAME = {"Acid": "Poison & Acid"}                         # how the game calls the type
_COLOURS = {t.lower() for t in ("Fire", "Cold", "Lightning", "Acid", "Pierce", "Bleeding", "Vitality", "Aether", "Chaos")}
_COLOUR_ALIAS = {"poison": "acid", "life": "vitality"}      # damage names of the sheet that have another key


@functools.cache
def _data() -> dict:
    return TGT.load()


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _words(text: str) -> int:
    return len(str(text).split())


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _join(items) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _pos(x: float, lo: float, hi: float) -> float:
    """Where `x` sits on the axis `lo` to `hi`, in points 0 to 100 (held inside the bar)."""
    return round(min(max((x - lo) / (hi - lo) * 100.0, 0.0), 100.0), 1) if hi > lo else 0.0


def _label_at(at: float, text: str) -> float:
    return min(max(at, LABEL_EDGE if len(text) > 1 else 0.0), LABEL_MAX)


def _declutter(ticks) -> tuple:
    """`ticks` as (priority, Tick), lower priority number first: a label is dropped when it sits closer than LABEL_GAP to a
    label that was kept. The result is in axis order."""
    kept: list = []
    for _prio, t in sorted(ticks, key=lambda x: x[0]):
        if all(abs(t.at - k.at) >= LABEL_GAP for k in kept):
            kept.append(t)
    return tuple(sorted(kept, key=lambda t: t.at))


def _tone(word: str) -> str:
    return _TONE.get(word) or ("gap" if word.endswith(" high") else "risk")


NOTHING_TO_DO, NO_SINGLE_FIX = "Nothing to do", "No single fix yet"


def _panel(pid: str, title: str, word: str, left: tuple, **kw) -> V.Panel:
    """A panel with its foot: `left` is the (text, ref) in front - a plain text has the ref "" - then "How rated". A panel with
    an open check never says "Nothing to do": the check is the reason it is not."""
    if left and left[0] == NOTHING_TO_DO and any(not c.ok for c in kw.get("checks") or ()):
        left = (NO_SINGLE_FIX, "")
    return V.Panel(pid, title, word, tone=_tone(word), foot=(*([left] if left else []), ("How rated", HOW_RATED[pid])), **kw)


def _own_fix_foot(left: tuple, checks) -> tuple:
    """The foot of a panel whose open checks name their own fix ("spend them", "switch it on or move the points"): a "Nothing to
    do" or "No single fix yet" under them would contradict them, so there is none. A link to a fix stays."""
    if left and left[0] in (NOTHING_TO_DO, NO_SINGLE_FIX) and any(not c.ok for c in checks):
        return ()
    return left


def _result(a, prefix: str):
    return next((r for r in a.results if r.id.startswith(prefix)), None)


def _fights(a) -> list:
    """One boss per fight (the worst form - `a.bosses` is sorted worst first), as the Player View counts them (B2)."""
    if not a.bosses:
        return []
    fights = CM.boss_fights(a)
    first: dict = {}
    for b in a.bosses:
        first.setdefault(fights.get(b.name, b.name), b)
    return list(first.values())


# ---------------------------------------------------------------------------
# the line in front of "How rated"
# ---------------------------------------------------------------------------

def _lever(g) -> str:
    """The area the guide ranks first by what it is worth when solved, if it is worth a point or more and has a fix."""
    top = max(g.area, key=lambda lv: lv.worth, default=None)
    return top.name if top is not None and top.members and RD.round_half_up(top.worth) >= LEVER_MIN else ""


def _count_text(members) -> str:
    perm = sum(1 for p in members if p.kind != "consumable")
    temp = len(members) - perm
    if perm and temp:
        return f"{perm} permanent and {_plural(temp, 'temporary fix', 'temporary fixes')}"
    return _plural(len(members), "fix", "fixes") if perm else _plural(temp, "temporary fix", "temporary fixes")


def _nothing(word: str) -> tuple:
    return (NOTHING_TO_DO if word in ("OK", "Info") else NO_SINGLE_FIX, "")


def _area_foot(g, name: str, word: str) -> tuple:
    """"1 permanent and 1 temporary fix, 1 still open" linking to the area of block 3; the biggest lever says what it is worth;
    an area without a fix says so in plain text (no link: block 3 has nothing to show for it)."""
    lv = next((x for x in g.area if x.name == name), None)
    if lv is None or not lv.members:
        return _nothing(word)
    if _lever(g) == name:
        return (f"Biggest lever, about +{RD.round_half_up(lv.worth)} if solved", V.area_anchor(name))
    still = sum(1 for r in g.residual if r.area == name)
    text = _count_text(lv.members) + (f", {still} still open" if still else "")
    return (text, V.area_anchor(name))


def _kind_foot(g, kinds: tuple, word: str) -> tuple:
    """The fixes of some kinds (wherever the guide put them): one refund names its points, a single other fix links to its
    suggestion, several link to the area of the first."""
    ps = [p for p in g.shown if p.kind in kinds]
    if not ps:
        return _nothing(word)
    ids = V.suggestion_ids(g)
    if len(ps) == 1:
        p = ps[0]
        text = f"Refund {_plural(int(p.detail.get('points') or 1), 'point')}" if p.kind == "refund" else "1 fix"
        return (text, ids[p.id])
    lv = next(x for x in g.area if ps[0] in x.members)
    return (_plural(len(ps), "fix", "fixes"), V.area_anchor(lv.name))


# ---------------------------------------------------------------------------
# meters
# ---------------------------------------------------------------------------

class _Spec(NamedTuple):
    name: str
    prefix: str            # the id prefix of its target in targets.json
    metric: str
    label: str             # the label of its gain in a proposal
    percent: bool
    lo: float | None       # a fixed axis; None: 0 to AXIS_ROOM x the largest mark
    hi: float | None


_SPECS = {
    "health": _Spec("Health", "health_", "health", "Health", False, None, None),
    "absorption": _Spec("Armor Absorption", "absorption", "absorption", "Armor Absorption", True, 70.0, 100.0),
    "physical": _Spec("Physical Resistance", "physical_", "physical_combat", "Physical Res", True, None, None),
    "da": _Spec("Defensive Ability", "da_", "da_combat", "Defensive Ability", False, None, None),
    "leech": _Spec("Life Leech Resistance", "leech_", "leech_res", "Life Leech Res", True, -50.0, 50.0),
    "freeze": _Spec("Freeze Resistance", "freeze", "freeze_res", "Freeze Res", True, 0.0, 100.0),
    "stun": _Spec("Stun Resistance", "stun", "stun_res", "Stun Res", True, 0.0, 100.0),
    "slow": _Spec("Slow Resistance", "slow_", "slow_res", "Slow Res", True, 0.0, 100.0),
}


def _value(a, key: str) -> float:
    snap = a.snap
    if key == "health":
        return snap.sheet.abilities["health"]
    if key == "da":
        return snap.combat.abilities["da"]
    if key == "physical":
        r = snap.combat.resists["Physical"]
        return min(r["value"], r["cap"])
    if key == "absorption":
        return snap.sheet.absorption
    if key == "leech":
        return snap.sheet.leech["value"]
    return snap.combat.fields.get(TGT.FIELD[_SPECS[key].metric], 0.0)


def _cap_of(a, key: str) -> float:
    return a.snap.combat.resists["Physical"]["cap"] if key == "physical" else math.inf


def _num(spec: _Spec, v: float, short: bool = False) -> str:
    """A number of a meter: a percent with its sign, Health as "17k" on a tick, other numbers with thousands."""
    if spec.percent:
        return f"{v:.0f}%"
    return (f"{v / 1000:.1f}".rstrip("0").rstrip(".") + "k") if short else f"{v:,.0f}"


def _value_text(spec: _Spec, key: str, v: float, ref: float | None) -> str:
    if key == "absorption":                                   # B7 with one decimal: 99.96 short of 100 reads 99.9%
        return f"{math.floor(v * 10 + 1e-9) / 10 if ref is not None and v < ref else v:.1f}%"
    n = TU.toward_gap(v, ref)                                  # B7: a value short of its target never reads as met
    return f"{n}%" if spec.percent else f"{n:,}"


def _grid(spec: _Spec, key: str, lo, hi, stage_t, end_t, pos) -> tuple:
    """The labels along a meter: the start, the target of the stage (mid), the Endgame target and - on a free axis - its end.
    Life Leech Resistance has a fixed grid, the crowd control bars only mark their target."""
    def tick(v, text=None, mid=False):
        t = text or _num(spec, v, True)
        return V.Tick(_label_at(pos(v), t), t, mid)
    if key == "leech":
        return tuple(tick(v, f"{v:.0f}%", v == stage_t) for v in (-50.0, -25.0, 0.0, 25.0))
    if key in ("freeze", "stun", "slow"):
        return (tick(0.0, "0%"), tick(stage_t, mid=True)) if stage_t is not None else ()
    out = [(1, tick(lo, "0" if lo == 0 else None))]
    if stage_t is not None:
        out.append((0, tick(stage_t, mid=True)))
    if end_t is not None and end_t != stage_t:
        out.append((2, tick(end_t)))
    if hi not in (stage_t, end_t):
        out.append((3, tick(hi)))
    return _declutter(out)


def _ghost(a, g, spec: _Spec, key: str, value: float, pos):
    """(position, proposal) of the best change the guide shows for this value - the largest new value inside the cap -, or None.
    The text under the meters names it. It is a value, not a score: an unrated loss does not hide it (E3 is about scores)."""
    best = None
    for p in g.shown:
        for d in p.gain:
            new = min(d.new, _cap_of(a, key))
            if d.label == spec.label and pos(new) > pos(value) + 0.05 and (best is None or new > best[0]):
                best = (new, p)
    return None if best is None else (pos(best[0]), best[1])


def _meter(a, g, key: str):
    """(Meter, ghost proposal or None) of one value, or None when no target of any stage names it."""
    spec = _SPECS[key]
    r = _result(a, spec.prefix)
    stage_t = r.target if r is not None and isinstance(r.target, (int, float)) else None
    end_t = TGT.threshold_for(_data(), spec.metric, Stage.ENDGAME, a.archetype == "melee", a.ctx.profile.value)
    if stage_t is None and end_t is None:
        return None
    v = _value(a, key)
    lo, hi = (spec.lo, spec.hi) if spec.hi is not None else (0.0, AXIS_ROOM * max(v, end_t or 0.0, stage_t or 0.0) or 1.0)
    pos = lambda x: _pos(x, lo, hi)                                                           # noqa: E731
    target = _num(spec, stage_t) if stage_t is not None else f"{_num(spec, end_t)} later"
    ghost = _ghost(a, g, spec, key, v, pos)
    status = _RESULT_WORD[r.status] if stage_t is not None and r is not None else "Info"
    later = pos(end_t) if end_t is not None and a.stage is not Stage.ENDGAME and end_t != stage_t else None
    m = V.Meter(spec.name, _value_text(spec, key, v, stage_t if stage_t is not None else end_t), target, status, pos(v),
                _grid(spec, key, lo, hi, stage_t, end_t, pos), ghost[0] if ghost else None,
                pos(stage_t) if stage_t is not None else None, later, negative=key == "leech" and v < 0)
    return m, (ghost[1] if ghost else None)


def _via(p) -> str:
    """How a change gets there: "with Royal Jelly Essence", "after refunding Absolute Zero"."""
    if p.kind in ("consumable", "gear"):
        return "with " + " ".join(x for x in (CM.item_name(p), CM.slot_phrase(p)) if x)
    return "after " + CM.action_gerund(p)


def _dashed(ghosts, room: int = KEY_WORDS) -> str:
    """"Dashed: with Royal Jelly Essence." - the change behind each dashed part; with several each is named by its meter."""
    if not ghosts:
        return ""
    if len(ghosts) == 1:
        text = f"Dashed: {_via(ghosts[0][1])}."
        return text if _words(text) <= room else ""
    parts: list = []
    for name, p in ghosts:
        trial = "Dashed: " + ", ".join(parts + [f"{name} {_via(p)}"]) + "."
        if _words(trial) > room:
            break
        parts.append(f"{name} {_via(p)}")
    return "Dashed: " + ", ".join(parts) + "." if parts else ""


def _meters(a, g, keys) -> tuple:
    """(meters, the note about their dashed parts) of the given values."""
    found = [(k, _meter(a, g, k)) for k in keys]
    found = [(k, x) for k, x in found if x is not None]
    meters = tuple(x[0] for _k, x in found)
    ghosts = [(x[0].name, x[1]) for _k, x in found if x[1] is not None]
    return meters, ghosts


# ---------------------------------------------------------------------------
# Defense
# ---------------------------------------------------------------------------

def _health_key(a) -> tuple[str, str]:
    """(key line, its bold part): how far Health is from the target and why this target applies."""
    r = _result(a, "health_")
    if r is None or not isinstance(r.target, (int, float)):
        return "", ""
    value = a.snap.sheet.abilities["health"]
    now, target = TU.toward_gap(value, r.target), round(r.target)
    early = a.stage is Stage.EARLY
    melee = " melee" if a.archetype == "melee" and not early else ""
    if r.status != "ok":
        lead, text = f"{target - now:,} short", f"{target - now:,} short of the{melee} target"
    else:
        over = int(math.floor((value / r.target - 1) * 100 + 1e-9)) if r.target else 0
        lead = f"{over}% above" if over >= 1 else "At"
        text = f"{lead} the{melee} target"
    if early:
        why = "It is the Mid target scaled down to your level."
        text += f" for level {a.ctx.character.level}."
    elif a.archetype == "melee":
        why = "Your main attack uses weapon damage, so the melee target applies."
        text += "."
    else:
        why = ("Your damage comes from pets, so the lower target applies." if a.archetype == "pet" else
               "Your main attack is not a melee weapon attack, so the lower target applies.")
        text += "."
    return f"{text} {why}", lead


def _health(a, g) -> V.Panel:
    word = _WORD[a.areas["Health"]["status"]]
    meters, ghosts = _meters(a, g, ("health",))
    key, bold = _health_key(a)
    return _panel("p-health", "Health", word, _area_foot(g, "Health", word), key=key, key_bold=bold, meters=meters,
                  note=_dashed(ghosts))


def _survival(a, g) -> V.Panel:
    word = _WORD[a.areas["Survival"]["status"]]
    meters, ghosts = _meters(a, g, ("absorption", "physical", "da"))
    return _panel("p-survival", "Survival", word, _area_foot(g, "Survival", word), meters=meters, note=_dashed(ghosts))


def _res_name(row) -> str:
    return _RES_NAME.get(row.name, row.name)


def _cell(row) -> V.ResCell:
    """One resistance. A cell that is short shows its value rounded down and by how much it is short (it never reads as met);
    a cell at its target shows the value as it counts and what it has over the shown value."""
    key = row.name.lower()
    if row.met:
        return V.ResCell(_res_name(row), f"{round(row.shown)}%", f"{max(round(row.value - row.shown), 0)} spare", False, key)
    floor = math.floor(row.value + 1e-9)
    return V.ResCell(_res_name(row), f"{floor}%", f"{max(math.ceil(row.target - floor - 1e-9), 1)} short", True, key)


def _res_key(a, rows, cells) -> tuple[str, str]:
    """(key line, its bold part): how many of the nine are at their cap, the worst ones that are short, the leech gap."""
    met = sum(1 for r in rows if r.met)
    head = "All 9 at cap." if met == 9 else f"{met} of 9 at cap."
    lows = sorted(((int(c.spare.split()[0]), c.name) for c in cells if c.low), key=lambda x: (-x[0], x[1]))
    leech, count = _result(a, "leech_"), next((r for r in a.results if r.id in ("res_ultimate", "res_own_cap")), None)
    tail = ""
    if leech is not None and leech.status == "gap":
        tail = ("Life Leech Resistance is the real gap." if count is None or count.status in ("ok", "tight")
                else "Life Leech Resistance is a gap too.")
    elif leech is not None and leech.status == "tight":
        tail = "Life Leech Resistance is a little short."
    for named in range(min(len(lows), SHOWN_TYPES), 0, -1):         # fewer names until the line fits
        text = " and ".join(f"{name} is {n} short" for n, name in lows[:named])
        text += f", plus {len(lows) - named} more." if len(lows) > named else "."
        key = " ".join(x for x in (head, text, tail) if x)
        if _words(key) <= KEY_WORDS:
            return key, head
    return " ".join(x for x in (head, tail) if x), head


def _leech_note(a, fights, ghost) -> str:
    parts = []
    pen = a.snap.sheet.leech.get("penalty") or 0.0
    if pen > 0:
        parts.append(f"{a.ctx.difficulty.label} takes {pen:.0f} points off.")
    n = sum(1 for b in fights if "Leech" in b.weak)
    if n:
        parts.append(_cap(f"{CM.number_word(n)} {'boss leeches' if n == 1 else 'bosses leech'} life."))
    base = " ".join(parts)
    dashed = _dashed([("Life Leech Resistance", ghost)] if ghost is not None else [], KEY_WORDS - _words(base))
    return " ".join(x for x in (base, dashed) if x)


def _resistances(a, g) -> V.Panel:
    word = _WORD[a.areas["Resistances"]["status"]]
    rows = TGT.resist_rows(a.ctx, a.snap, a.stage)
    cells = tuple(_cell(r) for r in rows)
    key, bold = _res_key(a, rows, cells)
    found = _meter(a, g, "leech")
    meters = (found[0],) if found else ()
    note = _leech_note(a, _fights(a), found[1] if found else None)
    return _panel("p-res", "Resistances", word, _area_foot(g, "Resistances", word), key=key, key_bold=bold, cells=cells,
                  meters=meters, note=note, size="wide")


def _crowd_foot(a, word) -> tuple:
    """"22 bosses can stun you": the crowd control that most fights can put on you, linked to the Bosses panel."""
    fights = _fights(a)
    count = {k: sum(1 for b in fights if k in b.weak) for k in ("Stun", "Freeze")}
    kind = max(count, key=lambda k: (count[k], k == "Stun"))
    return (f"{_plural(count[kind], 'boss', 'bosses')} can {kind.lower()} you", "p-bosses") if count[kind] else _nothing(word)


def _crowd(a, g) -> V.Panel:
    word = _WORD[a.areas["Crowd Control"]["status"]]
    keys = ["freeze", "stun"] + (["slow"] if _result(a, "slow_") is not None else [])
    found = [(k, _meter(a, g, k)) for k in keys]
    found = [x[1] for x in found if x[1] is not None]
    if a.stage is Stage.ENDGAME:
        meters = tuple(m for m, _p in found)
        low = [m.name for m in meters if m.status != "OK"]
        n = len(meters)
        every = (f"{meters[0].name} reaches" if n == 1 else "Both resistances reach" if n == 2 else
                 f"All {CM.number_word(n)} resistances reach")
        key = (f"{every} the Endgame target." if not low
               else f"{_join(low)} {'is' if len(low) == 1 else 'are'} below the Endgame target.")
    else:
        meters = tuple(m for m, _p in found if m.later is not None and m.fill < m.later)    # only what is short of Endgame
        key = "Targets start in Endgame. " + ("Already worth knowing:" if meters else "You are already above them.")
    return _panel("p-cc", "Crowd Control", word, _crowd_foot(a, word), key=key, meters=meters)


# -- bosses ----------------------------------------------------------------------------------------------------

_TITLE = {"Leech": "leeches your life", "Stun": "can stun you", "Freeze": "can freeze you", "Physical": "hits physical",
          "DA shred": "lowers your Defensive Ability", "Two ointments": "needs two ointments at once"}
# the weak spots as verbs for "they ..." / "it ..."
_VERB = {"Leech": ("leech", "leeches"), "Stun": ("stun", "stuns"), "Freeze": ("freeze", "freezes"),
         "Physical": ("hit physical", "hits physical"), "DA shred": ("shred your Defensive Ability", "shreds your Defensive Ability"),
         "Two ointments": ("need two ointments", "needs two ointments")}


def _columns(rows) -> tuple:
    """The most frequent weak spots of the shown fights, at most COLUMNS_MAX; a tie keeps the order the spots first appear in."""
    order: dict = {}
    for b in rows:
        for w in b.weak:
            order.setdefault(w, len(order))
    count = Counter(w for b in rows for w in b.weak)
    return tuple(sorted(count, key=lambda w: (-count[w], order[w]))[:COLUMNS_MAX])


def _more(rest, columns) -> str:
    """The fights that are not in the matrix, and the two weak spots most of them share (in the order of the columns)."""
    count = Counter(w for b in rest for w in b.weak)
    seen: dict = {}
    for b in rest:
        for w in b.weak:
            seen.setdefault(w, len(seen))
    top = sorted(sorted(count, key=lambda w: (-count[w], seen[w]))[:2],
                 key=lambda w: columns.index(w) if w in columns else len(columns))
    single = len(rest) == 1
    verbs = [(_VERB.get(w) or (f"hit {w}", f"hits {w}"))[1 if single else 0] for w in top]
    return f"{'it' if single else 'they'} {' and '.join(verbs)}" if verbs else ""


def _boss_table(fights) -> tuple:
    """(status word, table, key, the fights in the table) of the Bosses panel: the high-risk fights, else the medium ones."""
    risk = {k: [b for b in fights if b.risk == k] for k in ("HIGH", "MEDIUM")}
    level = "HIGH" if risk["HIGH"] else "MEDIUM" if risk["MEDIUM"] else ""
    if not level:
        return "OK", None, "No boss is a high or medium risk for your build.", []
    shown, extra = risk[level][:BOSS_ROWS], risk[level][BOSS_ROWS:]
    medium = risk["MEDIUM"] if level == "HIGH" else []
    columns = _columns(shown)
    more = ""
    if extra or medium:
        head = [f"{len(extra)} more at {level.lower()} risk"] if extra else []
        if medium:
            head.append(f"{len(medium)} {'' if extra else 'more '}at medium risk")
        verbs = _more(extra + medium, columns)
        more = " and ".join(head) + (f": {verbs}." if verbs else ".")
    rows = tuple((b.name.split(",")[0], tuple(c in b.weak for c in columns),
                  tuple((_TITLE.get(c) or f"{c} resistance too low") if c in b.weak else "" for c in columns)) for b in shown)
    table = V.BossTable("High risk" if level == "HIGH" else "Medium risk", columns, rows, more)
    return f"{len(risk[level])} {level.lower()}", table, "", shown


def _boss_foot(g, shown) -> tuple:
    """"Vilescar Ointment before them": the one ointment all shown bosses name, when the guide shows it as a fix."""
    names = {re.sub(r" \(.*\)$", "", b.consumable) for b in shown if b.consumable}
    if len(names) != 1:
        return ()
    name = next(iter(names))
    p = next((p for p in g.shown if p.kind == "consumable" and CM.item_name(p) == name), None)
    return (f"{name} before them", V.suggestion_ids(g)[p.id]) if p is not None else ()


def _bosses(a, g) -> V.Panel:
    fights = _fights(a)
    if not fights:
        key = (f"Boss preparation starts at level {STG.EARLY_BELOW}." if a.stage is Stage.EARLY
               else "No boss data to check against.")
        return _panel("p-bosses", "Bosses", "Info", (), key=key)
    word, table, key, shown = _boss_table(fights)
    return _panel("p-bosses", "Bosses", word, _boss_foot(g, shown) if table else (), key=key, bosses=table)


# ---------------------------------------------------------------------------
# Offense
# ---------------------------------------------------------------------------

def _span(secs: float) -> str:
    return f"{secs:.0f} s" if secs < 120 else f"{secs / 60:.1f}".rstrip("0").rstrip(".") + " min"


def _about(secs: float, target) -> str:
    """"about 2 minutes" / "about 31 seconds" - the rounding of the Player View (a time never reads as met)."""
    return "about " + TGT.seconds_text(secs, target).lstrip("~").replace(" min", " minutes").replace(" s", " seconds")


def _colour(name: str) -> str:
    key = _COLOUR_ALIAS.get(name.lower(), name.lower())
    return key if key in _COLOURS else ""


def _time_axis(a, r, dummy) -> V.TimeAxis:
    """The Training Dummy time on its axis: 0 to the larger of the slow end of the range and the Mid target. The goals are the
    Endgame target and - before the Endgame - the Mid target."""
    melee, profile = a.archetype == "melee", a.ctx.profile.value
    mid_t = TGT.threshold_for(_data(), "dummy_ttk", Stage.MID, melee, profile) or 0.0
    end_t = TGT.threshold_for(_data(), "dummy_ttk", Stage.ENDGAME, melee, profile) or 0.0
    top = max(dummy.high, mid_t) or 1.0
    pos = lambda s: _pos(s, 0.0, top)                                                          # noqa: E731
    stage_t = r.target if r is not None else None
    goals = sorted({t for t in ((end_t,) if a.stage is Stage.ENDGAME else (end_t, mid_t)) if t})
    marks = tuple(V.Tick(pos(t), _span(t), t == stage_t) for t in goals)
    you = f"you {TGT.seconds_text(dummy.seconds, stage_t)}"
    ticks = [(1, V.Tick(_label_at(0.0, "0 s"), "0 s")), (3, V.Tick(_label_at(pos(top), _span(top)), _span(top))),
             (0, V.Tick(_label_at(pos(dummy.seconds), you), you, tone=_RESULT_WORD[r.status].lower() if r else ""))]
    ticks += [(2, V.Tick(_label_at(t.at, t.text), t.text, t.mid)) for t in marks]
    lo_txt, hi_txt = _span(dummy.low), _span(dummy.high)
    targets = (f"Mid target {mid_t:.0f} seconds, Endgame target {end_t:.0f} seconds." if a.stage is Stage.MID
               else f"Endgame target {end_t:.0f} seconds.")
    label = f"Your time {_about(dummy.seconds, stage_t)}{f', range {lo_txt} to {hi_txt}' if lo_txt != hi_txt else ''}. {targets}"
    return V.TimeAxis(label, pos(dummy.seconds), pos(dummy.low), pos(dummy.high), marks, _declutter(ticks))


def _no_time(a, facts, dummy) -> str:
    """Why the Damage panel has no Training Dummy time: a pet build, a levelling character, no benchmark or no damage."""
    if getattr(facts, "pet", False):
        return "Pet build: the damage of your pets is not on your sheet."
    if a.stage is Stage.EARLY:
        return "Levelling: damage is rated from Mid."
    if dummy is None:
        return "The Training Dummy time needs Ultimate difficulty."
    return "Your build deals no damage to the Training Dummy." if dummy.seconds is None else ""


def _damage(a, g) -> V.Panel:
    word = _WORD[a.areas["Damage"]["status"]]
    foot = _area_foot(g, "Damage", word)
    facts, dummy = a.area_facts, getattr(a.benchmark, "dummy", None)
    why = _no_time(a, facts, dummy)
    if why:
        return _panel("p-damage", "Damage", word, foot, key=why)
    r = _result(a, "dummy_ttk")
    about = _about(dummy.seconds, r.target if r is not None else None)
    key = f"The Training Dummy reaches its shield in {about}."
    if r is not None:
        key += f" {_STAGE_WORD[a.stage]} target is {r.target:.0f} seconds."
    chips = [V.Chip(m.name, f"{math.floor(m.pct):,}%", _colour(m.name) or _colour(m.res))
             for m in getattr(facts, "main_types", ())]
    main = getattr(getattr(dummy, "rotation", None), "main", None) or getattr(ARCH.main_action(a.actions), "label", "")
    if main:
        chips.append(V.Chip("Main attack", main))
    return _panel("p-damage", "Damage", word, foot, key=key, key_bold=about, time=_time_axis(a, r, dummy), chips=tuple(chips))


# -- resistance reduction -------------------------------------------------------------------------------------

_DEBUFF = re.compile(r"^(?P<who>.+) \(enemy debuff(?P<weak>, not kept up)?\)$")
_LINE = re.compile(r"^(?P<who>.+) \((?P<word>reduced|flat), (?P<why>[^()]+)\)$")
_WHY = {"on chance": "only on chance", "one skill": "one skill only", "temporary": "only while it lasts"}
_KINDS = ("Stacking", "Reduced", "Flat")


def source_name(raw: str) -> str:
    """The name of a source without its rank, its slot and the item it sits on: "Hexxer's Kiss on Herald of Blazing Ends
    (Main hand)" -> "Hexxer's Kiss"; "Ring of Steel: modifier from Grasp of Unchained Might (Hands)" -> "Ring of Steel"."""
    name = re.sub(r"\s*\([^)]*\)", "", raw).strip()
    for sep in (" on ", ": "):
        name = name.split(sep, 1)[0]
    return name.strip()


def _names(raws) -> str:
    names = list(dict.fromkeys(source_name(r) for r in raws))
    if len(names) <= 2:
        return " and ".join(names)
    return f"{names[0]}, {names[1]} and {len(names) - 2} more"


def _sources(m) -> dict:
    """The lines of `MainType.rr_sources` by kind: {0: {"perm": [...], "weak": [...]}, 1: {"perm": (who, why)...}, 2: ...}."""
    out = {i: {"perm": [], "weak": []} for i in range(3)}
    for line in m.rr_sources:
        d, k = _DEBUFF.match(line), _LINE.match(line)
        if d:
            out[0]["weak" if d.group("weak") else "perm"].append(d.group("who"))
        elif k:
            i = 1 if k.group("word") == "reduced" else 2
            out[i]["perm" if k.group("why") == "strongest" else "weak"].append((k.group("who"), k.group("why")))
    return out


def _rr_slot(a, m, i: int, found: dict) -> V.RRSlot:
    state, sign = m.state(i), "%" if i == 1 else ""
    if state == "none":
        return V.RRSlot(_KINDS[i], "none", "No source found", state)
    if state == "full":
        who = _names(found["perm"] if i == 0 else [w for w, _y in found["perm"]])
        return V.RRSlot(_KINDS[i], f"-{math.floor(m.rr[i])}{sign}", who or "No source found", state)
    amount = f"-{math.floor(m.rr_weak[i])}{sign}"
    if i == 0:
        raws = found["weak"]
        row = next((d for d in a.snap.enemy_debuffs if len(raws) == 1 and d.get("label") == raws[0]), None)
        reason = f"up {math.floor(row['uptime'] * 100)}% of the time" if row and row.get("uptime") else "not kept up"
        return V.RRSlot(_KINDS[i], amount, f"{_names(raws)}, {reason}" if raws else reason, state)
    who, reason = found["weak"][0] if found["weak"] else ("", m.rr_why[i])
    text = _WHY.get(reason, reason)
    return V.RRSlot(_KINDS[i], amount, f"{source_name(who)}, {text}" if who else text, state)


def _rr_key(m) -> tuple[str, str]:
    present = sum(1 for i in range(3) if m.state(i) != "none")
    perm = sum(1 for i in range(3) if m.state(i) == "full")
    if present == 0:
        return f"You have no resistance reduction on {m.name}.", ""
    head = "all three kinds" if present == 3 else f"{CM.number_word(present)} of three kinds"
    if perm == present:
        return f"You have {head} on {m.name}, all permanent.", ""
    bold = "none is permanent" if perm == 0 else f"only {CM.number_word(perm)} {'is' if perm == 1 else 'are'} permanent"
    return f"You have {head} on {m.name}, but {bold}. Striped: counts half.", bold


def _rr(a, g) -> V.Panel:
    word = _WORD[a.areas["Resistance Reduction"]["status"]]
    foot = _area_foot(g, "Resistance Reduction", word)
    facts = a.area_facts
    main = sorted(getattr(facts, "main_types", ()) or (), key=lambda m: (m.kinds(), -m.pct))
    why = ("Pet build: the damage of your pets is not on your sheet." if getattr(facts, "pet", False) else
           "Levelling: resistance reduction is rated from Mid." if a.stage is Stage.EARLY else
           "" if main else f"No damage type is above {AF.MAIN_DAMAGE_PCT:.0f}% on your sheet, so there is nothing to reduce.")
    if why:
        return _panel("p-rr", "Resistance Reduction", word, foot, key=why)
    m = main[0]
    found = _sources(m)
    key, bold = _rr_key(m)
    note = f"Shown for {m.name}, the weakest of your {len(main)} main damage types." if len(main) > 1 else ""
    return _panel("p-rr", "Resistance Reduction", word, foot, key=key, key_bold=bold,
                  rr=tuple(_rr_slot(a, m, i, found[i]) for i in range(3)), note=note)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _sustain(a, g) -> V.Panel:
    word = _WORD[a.areas["Sustain"]["status"]]
    foot = _area_foot(g, "Sustain", word)
    s = getattr(a.area_facts, "sustain", None)
    if s is None:
        return _panel("p-sustain", "Sustain", word, foot, key="No sustain data.", size="third")
    floor = lambda v: int(math.floor(v + 1e-9))                                                # noqa: E731
    leech = V.Check(s.life_leech >= AF.LIFE_LEECH_MIN, f"Life leech {floor(s.life_leech)}%",
                    f"needs {AF.LIFE_LEECH_MIN:.0f}%")
    if s.breakers:
        name, thr, _kind = s.breakers[0]
        trigger = V.Check(True, "Low-health trigger", f"{name} at {floor(thr)}%" if thr else f"{name} at low health")
    else:
        trigger = V.Check(False, "Low-health trigger", f"needs one at {AF.TRIGGER_MAX_PCT:.0f}% Health or less")
    panic = V.Check(True, "Panic button", s.panic[0][0]) if s.panic else V.Check(
        False, "Panic button", f"needs a heal or shield of {AF.PANIC_MIN_PCT:.0f}% Health, "
                               f"cooldown up to {AF.PANIC_MAX_COOLDOWN:.0f} s")
    key = "Sustain is not rated while you are levelling." if a.stage is Stage.EARLY else ""
    return _panel("p-sustain", "Sustain", word, foot, key=key, checks=(leech, trigger, panic), size="third")


def _quality_key(q, early: str, pet: str) -> str:
    """Why the build check has no verdict: no check, a pet build, a levelling character or a failure."""
    if q is None:
        return "The build check did not run."
    if q.pet:
        return pet
    if q.early:
        return early
    return "The build check could not run." if q.error else ""


def _skill_checks(a, q) -> list:
    if q is None or q.pet or q.early or q.error:
        return []
    n = q.skill_points
    moved = q.points_modelled() if q.skills_waste_status() != "OK" else 0         # a move below the threshold is not worth it
    rated = [s for s in q.skills if s.rated and s.last is not None and "mastery bar" not in s.note]
    notes = []
    if moved:
        best = max((m for m in q.moves if not m.unvalued), key=lambda m: m.gain - m.loss)
        text, sub = f"{moved} of {_plural(n, 'point')} worth moving", f"{best.source} to {best.target}"
    else:
        weakest = min(rated, key=lambda s: (s.last, s.name)).name if rated else ""
        text, sub = f"{_plural(n, 'point')}, none worth moving", (f"weakest: {weakest}" if weakest else "")
    if q.attacks_unrated():
        notes.append("attacks are not rated")
    return [V.Check(q.skills_waste_status() == "OK", text, "; ".join(x for x in (sub, *notes) if x))]


def _issue_checks(issues, code: str, sub: str, more: str) -> list:
    """One open check per WARNING finding of a rule (its title is the text), the first CHECKS_MAX of them, the rest counted
    ("2 more switched off"). These are the facts the md "Top issues" name, kept on the page."""
    found = [i for i in issues if i.code == code and i.severity == "WARNING" and not i.self_penalty]
    checks = [V.Check(False, i.title, sub) for i in found[:CHECKS_MAX]]
    if len(found) > CHECKS_MAX:
        checks.append(V.Check(False, f"{len(found) - CHECKS_MAX} more {more}", sub))
    return checks


def _binding_checks(issues) -> list:
    """D2 and D3: a power bound to a temporary buff, a power bound to a skill that is not on the hotbar."""
    return (_issue_checks(issues, "D2", "its triggers work only while the buff runs", "powers bound to a buff")
            + _issue_checks(issues, "D3", "put the skill on the hotbar or rebind the power", "bound skills not on the hotbar"))


def _unspent_checks(parts) -> list:
    """"6 skill points unspent": a tick below `BQ.UNSPENT_FROM` points, a cross from there on; only the cross says "spend them"."""
    return [V.Check(n < BQ.UNSPENT_FROM, f"{_plural(n, word)} unspent", "" if n < BQ.UNSPENT_FROM else "spend them")
            for n, word in parts if n]


def _penalty_sub(detail: str) -> str:
    """"-5% Physical Resistance" out of "Permanently: -5% Physical Resistance; Physical 14 -> 9 %." (the first effect)."""
    effects = [p for p in detail.partition(": ")[2].split(" BELOW CAP:")[0].rstrip(". ").split("; ")
               if re.fullmatch(r"-\d+(?:\.\d+)?%? .+", p)]
    if not effects:
        return "lowers your own values"
    first = re.sub(r" [(]on .+[)]$", "", effects[0])
    return first + (f", +{len(effects) - 1} more" if len(effects) > 1 else "")


def _skills_checks(a) -> list:
    """The checks of the Skills panel; `has_open_check` reads the same list."""
    q = a.build_quality
    checks = _skill_checks(a, q)
    checks += [V.Check(False, f"{CM.penalty_name(i.title)} hurts you", _penalty_sub(i.detail))
               for i in a.issues if i.self_penalty and i.subject]
    attribute, skill = (q.free_attribute_points, q.free_skill_points) if q is not None else BQ.unspent_points(a.ctx)[:2]
    checks += _unspent_checks(((skill, "skill point"), (attribute, "attribute point")))
    checks += _issue_checks(a.issues, "E6", "switch it on or move the points", "switched off")
    checks += _issue_checks(a.issues, "E5", "neither on the hotbar nor bound to a power", "not on the hotbar")
    return checks


def _skills(a, g) -> V.Panel:
    q = a.build_quality
    word = _WORD[q.skills_status() if q is not None else "INFO"]
    checks = _skills_checks(a)
    key = _quality_key(q, "Levelling: skill points are still being placed, so they are not rated.",
                       "Pet build: the skills and devotions of your pets are not valued.")
    return _panel("p-skills", "Skills", word, _own_fix_foot(_kind_foot(g, SKILL_KINDS, word), checks), key=key,
                  checks=tuple(checks), size="third")


def _devotions_checks(a) -> list:
    """The checks of the Devotions panel; `has_open_check` reads the same list."""
    q = a.build_quality
    checks = []
    if q is not None and not (q.pet or q.early or q.error):
        spent = sum(d.points for d in q.devotions)
        free = q.free_devotion_points
        notes = ([f"{_plural(free, 'devotion point')} unspent"] if free else []) + (
            ["a swap pays" if q.devotions_waste_status() != "OK" else "1 swap gains a little"] if q.swap else [])
        checks.append(V.Check(free < BQ.UNSPENT_FROM, f"{spent} of {spent + free} points spent", "; ".join(notes)))
    else:
        checks += _unspent_checks(((BQ.unspent_points(a.ctx)[2], "devotion point"),))
    for i in a.issues:
        if i.code == "D1":
            checks.append(V.Check(False, i.title.replace(" is not bound", " not bound"), "the power never triggers"))
    checks += _binding_checks(a.issues)
    return checks


def _devotions(a, g) -> V.Panel:
    q = a.build_quality
    word = _WORD[q.devotions_status() if q is not None else "INFO"]
    checks = _devotions_checks(a)
    key = _quality_key(q, "Levelling: devotions are still being placed, so they are not rated.",
                       "Pet build: the skills and devotions of your pets are not valued.")
    return _panel(V.DEVOTIONS_PANEL, "Devotions", word, _own_fix_foot(_kind_foot(g, DEVOTION_KINDS, word), checks), key=key,
                  checks=tuple(checks), size="third")


def has_open_check(a, area: str) -> bool:
    """Does a panel of this area of block 3 show an open check (a cross)? Only "Skills & Devotions" has panels with checks. Block 3
    reads this where it would say "nothing to do": the panel and the quiet bar must not contradict each other."""
    return area == GD.SKILLS_AREA and any(not c.ok for c in _skills_checks(a) + _devotions_checks(a))


# ---------------------------------------------------------------------------
# the groups
# ---------------------------------------------------------------------------

def groups(a, g) -> tuple:
    """Block 2 of the report view: Defense, Offense and Build with their panels."""
    return (V.Group("Defense", (_health(a, g), _survival(a, g), _resistances(a, g), _crowd(a, g), _bosses(a, g))),
            V.Group("Offense", (_damage(a, g), _rr(a, g))),
            V.Group("Build", (_sustain(a, g), _skills(a, g), _devotions(a, g))))
