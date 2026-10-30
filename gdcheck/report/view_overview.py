"""Report v2, block 1 of the view: the overview (spec 3.3 to 3.7, decisions E2 and E3).

`overview(a, g, versions, meta)` takes the finished analysis, its guide and the report meta and returns the `Overview` of
`view.py`: the header facts, the verdict, the stage track with the ghost, the gate tiles, the steps, the lever line and the
watch-outs. It chooses and words; it rates nothing. The score, the gates and their solutions come from the analysis
(`readiness`, `stage.gate_list`, the proposals of the guide), and the formula of the score stays in `readiness._term`.

Two rules of the decisions are kept here and tested:
  E2  a consumable never closes a gate: the verdict counts only permanent steps of the tiers Now and Vendor, a tile names a
      consumable as "covers it while it runs", and the ghost note never names a gate a consumable would open
  E3  no score number next to an unrated loss (the lever line skips such a proposal)

References are element ids without '#'; `view.suggestion_ids` numbers the suggestions of block 3."""
from __future__ import annotations

import functools
import re
from collections import Counter

from ..analysis import guide as GD
from ..analysis import readiness as RD
from ..analysis import stage as STG
from ..analysis import targets as TGT
from ..analysis.proposals import effort as EF
from ..domain.enums import Stage
from . import common as CM
from . import view as V
from . import view_what as VW

STAGE_WORD = {Stage.EARLY: "Levelling", Stage.MID: "Mid game", Stage.ENDGAME: "Endgame"}
STAGE_LABEL = {"EARLY": "Early", "MID": "Mid", "ENDGAME": "Endgame"}
QUICK_TIERS = ("Now", "Vendor")          # the tiers that count as "quick steps" in the verdict
WATCH_LIMIT = 4
FIX_WORDS = 10                           # the solution line of a gate tile (spec 3.7)
STEP_WORDS = 12                          # what a step says (spec 3.7)
HEAD_WORDS = 16                          # the first line of a watch-out (spec 3.7)
LOSS_END = "damage not re-rated"                 # ends the cost of a step that loses damage the model does not re-rate (E3)
LEVER_MIN = 1                            # the lever line needs a gain of at least this many points (rounded)
_GATE_NAME = {"resistances": "Resistances"}      # how the ghost note and the "already met" line call a gate
# the targets a gate is made of (prefixes of the ids in targets.json); a consumable that moves one of them covers the gate
GATE_TARGETS = {"level": ("level_",), "resistances": ("res_ultimate",), "health": ("health_",), "da": ("da_",),
                 "physical": ("physical_",), "absorption": ("absorption",)}
_GATE_TOLERANCE = {"resistances": "res", "physical": "physical", "health": "relative", "da": "relative"}
_GATE_DELTA = {"health": "Health", "da": "Defensive Ability", "physical": "Physical Res", "absorption": "Armor Absorption"}
# the weak spots of a boss (BossRisk.weak) as verbs for "they ..." / "it ..."
_SPOT = {"Leech": ("leech your life", "leeches your life"), "Stun": ("stun you", "stuns you"),
         "Freeze": ("freeze you", "freezes you"), "Physical": ("hit physical", "hits physical"),
         "DA shred": ("shred your Defensive Ability", "shreds your Defensive Ability"),
         "Two ointments": ("need two ointments", "needs two ointments")}


_word = CM.number_word          # moved to common.py (the areas block uses it too)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def _join(items) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _words(text: str) -> int:
    return len(text.split())


def gate_rows(a) -> tuple:
    return STG.gate_list(a.ctx, a.snap, a.archetype == "melee")


def open_gate_rows(a, gates) -> list:
    """The open gates; only a Mid character has them (before level 85 they do not count, in the Endgame all are met)."""
    return [x for x in gates if not x.met] if a.stage is Stage.MID else []


# ---------------------------------------------------------------------------
# header
# ---------------------------------------------------------------------------

def class_parts(ch) -> tuple[str, str]:
    """(class name, the masteries as "A and B"): the class falls back to the first mastery, then to "?"."""
    names = [n for _, n, _ in ch.masteries]
    cls = ch.class_name or (names[0] if names else "?")
    rest = names if ch.class_name else names[1:]
    text = " and ".join(rest)
    return cls, ("" if text == cls else text)


def facts_of(a) -> tuple:
    ch = a.ctx.character
    diff = a.ctx.difficulty.label
    if diff != ch.difficulty_label:                       # .62: the difficulty the check used, not the save's
        diff = f"{diff} (checked; last played {ch.difficulty_label})"
    stats = ch.stats
    deaths = int(stats.get("death_count") or 0)
    played = f"{(stats.get('playtime_seconds') or 0) / 3600:.0f} h, {deaths} death{'' if deaths == 1 else 's'}"
    return (V.Fact("Level", str(ch.level)), V.Fact("Difficulty", diff),
            V.Fact("Goal", CM.PROFILE_NAME.get(a.ctx.profile.value, a.ctx.profile.value)), V.Fact("Played", played))


def stale_line(ctx) -> str:
    """The line about game data that were not checked against the live patch; "" when they were (or match the installed
    game), because then there is nothing to warn about. It carries what to do, so the watch-outs do not repeat it."""
    state, gv = CM.version_state(ctx), ctx.game_version
    if state == "unconfirmed":
        return (f"Game data {gv}, version not confirmed: Grim Dawn has not run since its last update. "
                "Start the game once, then check again.")
    if state == "mismatch":
        return (f"Game data {gv} does not match {ctx.options.live_version or 'your installed game'}. "
                "Item values and rules may be outdated: rebuild the game data with GD-Tools.")
    if state == "unchecked":
        return f"Game data {gv}. The live patch was not checked for this report."
    return ""


# ---------------------------------------------------------------------------
# verdict
# ---------------------------------------------------------------------------

def _quick_closed(g) -> set:
    """The gates the permanent steps of the tiers Now and Vendor open (E2: a consumable never counts)."""
    out: set = set()
    for p in g.steps:
        if p.kind != "consumable" and EF.tier(p) in QUICK_TIERS:
            out |= set(GD.v2(p).gates_opened)
    return out


def sentence_two(stage, open_gates, g) -> str:
    if stage is Stage.ENDGAME:
        return "Every Endgame gate is met."
    if stage is Stage.EARLY:
        return f"The Endgame gates count from level {STG.EARLY_BELOW}."
    n = len(open_gates)
    k = len(_quick_closed(g) & {x.id for x in open_gates})
    if k == 0:
        return f"{_cap(_word(n))} Endgame gate{'' if n == 1 else 's'} still open."
    if k == n:
        return "The last Endgame gate closes with a quick step." if n == 1 else \
            f"All {_word(n)} open Endgame gates close with quick steps."
    return f"{_cap(_word(k))} of {_word(n)} open Endgame gates {'closes' if k == 1 else 'close'} with quick steps."


def verdict(a, g, gates=None) -> str:
    """At most three sentences and 24 words: the stage and the score, what the gates need, and the area that costs most."""
    gates = gate_rows(a) if gates is None else gates
    text = f"{STAGE_WORD[a.stage]}, {a.score['score']} of 100. {sentence_two(a.stage, open_gate_rows(a, gates), g)}"
    top = max(g.area, key=lambda lv: lv.worth, default=None)
    if top is not None and top.worth >= 1.0:
        text += f" {top.name} {'hold' if top.name.endswith('s') else 'holds'} your score down."   # Resistances hold
    return text


# ---------------------------------------------------------------------------
# track, ghost
# ---------------------------------------------------------------------------

def _steps_phrase(g) -> str:
    nums = [str(i) for i in g.ghost_steps]
    if len(g.ghost_steps) == len(g.steps):
        return "the steps below"
    return ("step " if len(nums) == 1 else "steps ") + _join(nums)


def ghost_above(g, score) -> int | None:
    """The ghost score when it is above the score (the only case it is shown), else None."""
    return g.ghost if g.ghost is not None and g.ghost > score else None


def ghost_note(g, shown, labels=None) -> str:
    """The line under the track (spec 3.4). A ghost above the score gets its number: "Dashed: about 57 after the steps below
    (est.)" (`shown`, see `ghost_above`), or the numbers of the steps it counts when some are left out. The permanent steps that open every gate say so.
    Without a number above the score the note names the gates the permanent steps open; consumables open none (E2)."""
    labels = labels or {}
    parts = []
    if shown is not None:
        parts.append(f"Dashed: about {shown} after {_steps_phrase(g)} (est.).")
    if g.endgame_after:
        parts.append("Endgame opens after these steps (est.).")
    elif shown is None:
        opened: list = []
        for p in g.steps:
            if p.kind != "consumable":
                opened += [i for i in GD.v2(p).gates_opened if i not in opened]
        if opened:
            parts.append(f"The steps below close {_join(labels.get(i, i) for i in opened)} (est.).")
    return " ".join(parts)


def _track(a, g, open_gates, labels) -> V.Track:
    score = a.score["score"]
    ghost = ghost_above(g, score)
    return V.Track(score, tuple((STAGE_LABEL[name], low, high) for name, low, high in RD.bands()),
                   STAGE_LABEL[a.stage.value], ghost, ghost_note(g, ghost, labels), a.stage is not Stage.ENDGAME,
                   len(open_gates))


# ---------------------------------------------------------------------------
# gate tiles
# ---------------------------------------------------------------------------

def gate_head(open_n: int) -> str:
    if open_n == 0:
        return "Endgame reached: all six gates met"
    return f"Endgame opens when {open_n} more gate{' is' if open_n == 1 else 's are'} met"


@functools.cache
def _tolerance() -> tuple:
    tol = TGT.load().get("tolerance", {})
    return tuple(sorted(tol.items()))


def _status(g) -> str:
    """"Risk" when the distance is within the tolerance of targets.json (resistances 3 points, Physical 4 points, Health and
    Defensive Ability 5% of the target); Armor Absorption and Level have none, so they are always a "Gap"."""
    kind = _GATE_TOLERANCE.get(g.id)
    if kind is None:
        return "Gap"
    tol = dict(_tolerance()).get(kind, 0.0)
    allowed = g.target * tol if kind == "relative" else tol
    return "Risk" if g.gap <= allowed + 1e-9 else "Gap"


def _fmt(x: float, unit: str) -> str:
    return f"{x:.0f}%" if unit == "%" else f"{x:,.0f}"


def tile_label(g) -> str:
    if g.id != "resistances" or not g.worst:
        return g.label
    more = len(g.below) - (1 if g.worst in g.below else 0)
    return f"{g.worst} Resistance" + (f" (+{more} more)" if more > 0 else "")


def tile_values(g, rows) -> tuple[str, str, float]:
    """(now, target, fraction): the resistance gate shows the worst type ("79%" of "80%"), the others their own value."""
    row = next((r for r in rows if r.name == g.worst), None) if g.id == "resistances" else None
    if row is not None:
        now, target = round(row.value), row.target
        return f"{now:.0f}%", f"{target:.0f}%", _fraction(now, target)
    if g.id == "resistances":
        return f"{g.now:.0f}", f"{g.target:.0f} types", _fraction(g.now, g.target)
    return _fmt(g.now, g.unit), _fmt(g.target, g.unit), _fraction(g.now, g.target)


def _fraction(now: float, target: float) -> float:
    return min(max(now / target, 0.0), 1.0) if target else 0.0


def _best(props, guide):
    """The best of `props`, one that is a step of the guide before any other (the tile then names the step the reader
    is told to do), the usual order of the steps within each."""
    step_ids = {p.id for p in guide.steps}
    return GD.sort_steps([p for p in props if p.id in step_ids] or props)[0]


def _closer(g, guide):
    """The best permanent proposal of the guide that opens the gate, else the best consumable that covers a target of it."""
    permanent = [p for p in guide.shown if p.kind != "consumable" and g.id in GD.v2(p).gates_opened]
    if permanent:
        return _best(permanent, guide), False
    prefixes = GATE_TARGETS.get(g.id, ())
    covering = [p for p in guide.shown if p.kind == "consumable" and any(f.startswith(prefixes) for f in p.fixes)]
    return (_best(covering, guide), True) if covering else (None, False)


def resist_caps(rows) -> dict:
    return {f"{r.name} Res": r.cap for r in rows}


def value_text(x: float, unit: str) -> str:
    if unit == "%":
        return (f"{x:.0f}" if abs(x - round(x)) < 0.05 else f"{x:.1f}") + "%"
    return f"{x:,.0f}"


def _partial(g, guide, caps) -> str:
    """The best permanent step that moves the gate's value without closing it: "14% after refunding Absolute Zero"."""
    want = f"{g.worst} Res" if g.id == "resistances" else _GATE_DELTA.get(g.id)
    best = None
    for p in guide.shown:
        if p.kind == "consumable":
            continue
        for d in p.gain:
            if d.label == want and d.change > 0 and (best is None or d.new > best[0].new):
                best = (d, p)
    if best is None:
        return ""
    d, p = best
    return f"{value_text(min(d.new, caps.get(d.label, d.new)), d.unit)} after {CM.action_gerund(p)}"


def _shorten(text: str, room: int) -> str:
    """`text` cut to `room` words including the " ..." that marks the cut."""
    return text if _words(text) <= room else CM.short_why(text, max(room - 1, 1))


def _fix(g, guide, ids, caps) -> tuple[str, str, str]:
    """(line, bold lead, ref) of a tile: the permanent closer ("... closes it"), else the consumable ("... covers it while
    it runs"), else "No single fix yet." and the best partial step."""
    if g.id == "level":
        return "Reach level 100.", "", ""
    p, temporary = _closer(g, guide)
    if p is None:
        lead = "No single fix yet."
        part = _partial(g, guide, caps)
        line = f"{lead} {part}" if part and _words(f"{lead} {part}") <= 10 else lead
        return line, lead, V.area_anchor(GD.GATE_AREA[g.id])
    tail = "covers it while it runs" if temporary else "closes it"
    room = FIX_WORDS - _words(tail)
    if p.kind in ("consumable", "gear"):
        lead = CM.item_name(p)
        line = " ".join(x for x in (lead, CM.slot_phrase(p), tail) if x)
        if _words(line) > FIX_WORDS:                   # drop the slot, then shorten the name; the tail always stays
            lead = _shorten(lead, room)
            line = f"{lead} {tail}"
    else:
        lead = _shorten(CM.action_text(p), FIX_WORDS - 1 - _words(tail))        # room for "That <tail>."
        line = f"{lead}{'' if lead.endswith('...') else '.'} That {tail}."
    return line, lead, ids.get(p.id, V.area_anchor(GD.GATE_AREA[g.id]))


def tile(g, guide, rows=()) -> V.GateTile:
    """The tile of one open gate. `rows` are the nine resistances (`targets.resist_rows`); the resistance tile names the
    value of the worst one."""
    now, target, fraction = tile_values(g, rows)
    line, lead, ref = _fix(g, guide, V.suggestion_ids(guide), resist_caps(rows))
    return V.GateTile(g.id, tile_label(g), now, target, fraction, _status(g), line, ref, lead)


# ---------------------------------------------------------------------------
# steps, lever
# ---------------------------------------------------------------------------

def _where(p) -> str:
    """Where to get it, as a short sentence: the count in the bags, the Blacksmith, the stash, a vendor."""
    src = p.source or ""
    if p.kind == "consumable":
        n = int(p.detail.get("in_stock") or 0)
        return f"You have {n} in your bags." if p.in_stock and n else "Craft it at the Blacksmith or buy it."
    if p.kind != "gear":
        return ""
    if src == "in your stash":
        return "It is in your stash."
    if src == "craft or find":
        return "Craft or find it."
    m = re.match(r"buy: (.+?) \(", src)
    return f"Buy it from {m.group(1)}." if m else ""


def _headline(p, a, caps) -> str:
    """What changes, as the player reads it: the first value that touches an open target. A proposal that closes a target
    or a gate names the new value ("Health 17,557"), one that only moves it says from where ("Physical Resistance 9% to
    14%"). Proposals without a modelled gain (bind, more cooldown reduction, a devotion swap) say what happens."""
    for d in CM.ordered_gains(p, a):
        cap = caps.get(d.label)
        now, new = (d.now, d.new) if cap is None else (min(d.now, cap), min(d.new, cap))
        if (new - now if d.better_if_higher else now - new) <= 1e-9:
            continue                                   # nothing gained inside the cap (121% to 80% would read as a loss)
        if p.fixes or GD.v2(p).gates_opened:
            return f"{CM.view_label(d.label)} {value_text(new, d.unit)}."
        return f"{CM.view_label(d.label)} {value_text(now, d.unit)} to {value_text(new, d.unit)}."
    det = p.detail
    if p.kind == "bind":
        return f"{det.get('power', 'The power')} now triggers with {det.get('host', 'your attack')}."
    if p.kind == "uptime":
        debuff, now, after = det.get("debuff", "The debuff"), float(det.get("uptime_now", 0)) * 100, VW.uptime_after(p) * 100
        if round(after) >= 100:
            return f"{debuff} is up all the time, not {now:.0f}% of it."
        return f"{debuff} is up {after:.0f}% of the time instead of {now:.0f}%."
    if p.kind == "devotion_swap":
        return f"The build check rates it {float(det.get('value_points', 0)):.1f} value points higher."
    return ""


def _step_what(p, a) -> str:
    """What the player does, in at most 12 words. A skill, devotion or attribute change lists every move or constellation in its
    change line (25 words for a devotion plan): the step says the short form of the suggestion, the full change is in the
    suggestion it links to."""
    text = CM.action_text(p)
    short = VW.generated_what(p, a)
    if _words(text) <= STEP_WORDS or not short:           # any other kind stays whole: a too long step is rule 16's to report
        return text
    return VW.fit(short, STEP_WORDS)


def step_of(p, a, ids, caps) -> V.Step:
    tier = EF.tier(p)
    result = " ".join(x for x in (_headline(p, a, caps), _where(p)) if x)
    unrated = GD.v2(p).loses_unrated
    has_cost = bool(p.cost or p.detail.get("lost") or p.detail.get("lost_powers")) or unrated
    cost = f"Costs: {CM.cost_text(p, True, CM.view_label)}{'; ' + LOSS_END if unrated else ''}." if has_cost else ""
    return V.Step(_step_what(p, a), ids.get(p.id, ""), tier, EF.TIER_RANK[tier], result, cost)


def lever_of(g, ids) -> tuple[str, str, str]:
    """(what, note, ref): the proposal of the guide with the largest rated score gain that is no step (spec 3.3) - one that
    loses something unrated is left out (E3) - if it is worth a point or more. Without one there is no lever line."""
    best = None
    step_ids = {p.id for p in g.steps}
    for p in g.shown:
        if p.id in step_ids:
            continue
        v = GD.v2(p)
        if not v.loses_unrated and v.score_delta is not None and (best is None or v.score_delta > GD.v2(best).score_delta):
            best = p
    if best is None:
        return "", "", ""
    worth = RD.round_half_up(GD.v2(best).score_delta)
    if worth < LEVER_MIN:
        return "", "", ""
    return lower_first(CM.action_text(best)), f"That alone is worth about +{worth} score (est.).", ids.get(best.id, "")


# ---------------------------------------------------------------------------
# watch out
# ---------------------------------------------------------------------------

_EFFECT = re.compile(r"-(\d+(?:\.\d+)?)(%?) (.+)")


def _lowers(detail: str) -> str:
    """"lowers your own Physical Resistance by 5%, permanently" out of "Permanently: -5% Physical Resistance; Physical 14 ->
    9 %." - the first effect and how many more there are."""
    when_raw, _, rest = detail.partition(": ")
    when = "permanently" if when_raw.lower().startswith("permanent") else "while it is active"
    effects = []
    for part in rest.split(" BELOW CAP:")[0].rstrip(". ").split("; "):
        m = _EFFECT.fullmatch(part)
        if m:
            effects.append(f"{re.sub(r' [(]on .+[)]$', '', m.group(3))} by {m.group(1)}{m.group(2)}")
    if not effects:
        return f"weakens you, {when}"
    more = len(effects) - 1
    return f"lowers your own {effects[0]}{f' and {more} more' if more else ''}, {when}"


def _penalty_items(a) -> list:
    out = []
    for i in a.issues:
        if not i.self_penalty:
            continue
        name = CM.penalty_name(i.title)                                                  # "(rank 1)", "(item skill of ...)"
        what = "skill" if i.subject else "item"
        out.append(V.Watch("Gap", f"{name} {_lowers(i.detail)}.", name,
                           f"{'A' if what == 'skill' else 'An'} {what} that weakens you always comes first.", "Why",
                           "x-selfpen"))
    return out


def _error_items(a) -> list:
    return [V.Watch("Gap", CM.short_why(i.title, 16), CM.short_why(i.title, 16), CM.short_why(i.fix, 14), "", "")
            for i in a.issues if i.severity == "ERROR" and not i.self_penalty]


def _warning_items(a, meta) -> list:
    """What the report lists as warnings besides the version line (that is the data line of the header): a note of the
    mechanics reference, the Ascendant profile."""
    out = []
    if meta.get("mechanics_note"):
        head = CM.short_why(meta["mechanics_note"], 16)
        out.append(V.Watch("Risk", head, head, "", "What this means", "x-data"))
    if a.ctx.profile.value == "ascendant":
        head = "Ascendant uses the General Ultimate targets."
        out.append(V.Watch("Risk", head, head, "Monster values use the Ascendant adjustment.", "What this means", "x-score"))
    return out


def _boss_items(a) -> list:
    fights = CM.boss_fights(a) if a.bosses else {}
    seen: set = set()
    high = []
    for b in a.bosses:                                   # worst first; one entry per fight
        key = fights.get(b.name, b.name)
        if b.risk == "HIGH" and key not in seen:
            seen.add(key)
            high.append(b)
    if not high:
        return []
    plural = len(high) > 1
    lead = f"{len(high)} bosses are a high risk" if plural else "1 boss is a high risk"
    head = f"{lead}."
    phrases: list = []
    for w, _n in Counter(w for b in high for w in b.weak).most_common(3):       # the most common weak spots, while they fit
        phrase = (_SPOT.get(w) or (f"hit {w}", f"hits {w}"))[0 if plural else 1]
        text = f"{lead}: {'they' if plural else 'it'} {_join(phrases + [phrase])}."
        if phrases and _words(text) > HEAD_WORDS:
            break
        phrases.append(phrase)
        head = text
    names = {re.sub(r" \(.*\)$", "", b.consumable) for b in high if b.consumable}
    listed = f"{'They are' if plural else 'It is'} listed in the Bosses panel."
    extra = f"Switch to {next(iter(names))} before them." if len(names) == 1 else listed    # only when all agree
    return [V.Watch("High", head, lead, extra, "Which bosses", "p-bosses")]


def _unbound_items(a, g, ids) -> list:
    out = []
    for i in a.issues:
        if i.code != "D1":
            continue
        bind = next((p for p in g.shown if p.kind == "bind" and p.detail.get("power_record") == i.subject), None)
        head = f"{i.title} to a skill. The power never triggers." if i.title.endswith("is not bound") else f"{i.title}."
        out.append(V.Watch("Risk", head, i.title, "Bind it in the devotion screen.", "How",
                           ids.get(bind.id, "") if bind else V.DEVOTIONS_PANEL))   # no bind shown (pet build): the panel is always there
    return out


def _conflict_items(g) -> list:
    out = []
    for c in sorted(g.conflicts, key=lambda c: -len(c.members)):      # the slot most proposals want first
        ointment = c.label == "Ointment"
        lead = "Your ointment slot" if ointment else f"Your {c.label.lower()}"
        out.append(V.Watch("Risk", f"{lead} is wanted by {_word(len(c.members))} proposals.", lead,
                           "Only one ointment works at a time." if ointment else "Only one fits.", "Compare them",
                           V.note_id(c)))
    return out


def _unknown_items(g) -> list:
    if g.unresolved <= 0:
        return []
    n = g.unresolved
    lead = f"{n} record{'' if n == 1 else 's'}"
    return [V.Watch("Info", f"{lead} in your save {'is' if n == 1 else 'are'} not in the game data.", lead,
                    "They are not rated.", "What this means", "x-data")]


def watch_items(a, g, meta, ids) -> tuple:
    """At most four, most important first: a skill that weakens you, build errors, warnings about the data, high-risk
    bosses, an unbound celestial power, slots several proposals want, records the game data do not know."""
    items = (_penalty_items(a) + _error_items(a) + _warning_items(a, meta) + _boss_items(a)
             + _unbound_items(a, g, ids) + _conflict_items(g) + _unknown_items(g))
    return tuple(items[:WATCH_LIMIT])


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------

def overview(a, g, versions, meta) -> V.Overview:
    """Block 1 of the report view. `versions` is accepted for the other blocks' signature (the data basis is block 4)."""
    ch = a.ctx.character
    gates = gate_rows(a)
    open_ = open_gate_rows(a, gates)
    ids = V.suggestion_ids(g)
    rows = TGT.resist_rows(a.ctx, a.snap, a.stage)
    labels = {x.id: _GATE_NAME.get(x.id, x.label) for x in gates}
    tiles = tuple(tile(x, g, rows) for x in open_)
    if a.stage is Stage.MID:
        head, met = gate_head(len(open_)), tuple(labels[x.id] for x in gates if x.met)
    else:
        head, met = ("Endgame reached: all six gates met" if a.stage is Stage.ENDGAME else ""), ()
    caps = resist_caps(rows)
    cls, masteries = class_parts(ch)
    lever, note, ref = lever_of(g, ids)
    return V.Overview(ch.name, cls, masteries, facts_of(a), stale_line(a.ctx), verdict(a, g, gates),
                      _track(a, g, open_, labels), head, tiles, met, tuple(step_of(p, a, ids, caps) for p in g.steps),
                      lever, ref, watch_items(a, g, meta, ids), note)
