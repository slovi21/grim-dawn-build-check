"""Report v2, block 3 of the view: the improvements (spec 3.3 rows "Bereichsgruppen" to "Reroll-Vorschläge", 3.6, mockup block 3).

`improvements(a, g)` takes the finished analysis and its guide and returns one `ImproveArea` per area the guide shows (in the
order of what the open targets cost) with a `Suggestion` for every proposal of the area and the notes under them. It words and
chooses; it rates nothing: the numbers come from the proposals (`gain`, `cost`, `detail` with the four values of
`counterfactual.annotate`) and the guide, and the score formula stays in `readiness._term`.

The rules of the decisions are kept here and tested:
  E2  a consumable never opens a gate: its impact says "Covers an Endgame gate while it runs"
  E3  no score number next to an unrated loss, and the cost then ends with "; damage not re-rated"

The numbering of the suggestions is `view.suggestion_ids` (block 1 links to it), the order of the fastest-first view is
`TIER_RANK * 100 + index` (`fastest_order`), the "Why" link of a suggestion is `why_ref`. Every text has a limit (spec 3.7): what
16 words, gain 20, where 12, a note 30; the cost is never cut."""
from __future__ import annotations

import dataclasses
import math
import re

from ..analysis import guide as GD
from ..analysis import readiness as RD
from ..analysis import targets as TGT
from ..analysis.proposals import effort as EF
from ..domain.enums import Stage
from . import common as CM
from . import view as V
from . import view_areas as VAR
from . import view_what as VW
from .view_what import fit as _fit, join as _join, plural as _plural
from .view_overview import LEVER_MIN, GATE_TARGETS, LOSS_END, resist_caps, gate_rows, lower_first, value_text, open_gate_rows, tile_label, tile_values

WHAT_WORDS = 16
GAIN_WORDS = 20
WHERE_WORDS = 12
NOTE_WORDS = 30
GAIN_PARTS = 3                 # values named in a gain before "+k more"
PICK_NAMES = 3                 # proposals named in a "Pick one" note
MOST_OF_THE_GAP = 0.6          # a partial step closes "most of the gap" from this share of the deficit, "half" from the next
HALF_OF_THE_GAP = 0.4
NO_FIX = "No single change in your gear, stash or reputation vendors closes it."
OPEN_CHECKS = "{} have open checks in their panels."   # the quiet bar where the panel of the area shows an open check
PERMANENT_PREFIX = "Permanent instead: "               # starts the "what" of a permanent counterpart; render_html styles it
_STATUS = {"OK": "OK", "RISK": "Risk", "GAP": "Gap", "INFO": "Info"}

# the row of block 4 that explains a proposal: by its kind first, else by the target it addresses (the first prefix that fits)
WHY_REF = {"refund": "x-selfpen", "bind": "x-power", "devotion_swap": "x-value", "devotion": "x-value", "skills": "x-value",
           "attributes": "x-attributes", "uptime": "x-rr", "reroll": "x-effort"}
WHY_TARGET = (("level", "x-gates"), ("res_", "x-res"), ("ravager_", "x-res"), ("fire_", "x-res"), ("lightning_", "x-res"),
              ("health", "x-health"), ("da_", "x-da"), ("physical", "x-physical"), ("absorption", "x-absorption"),
              ("armor", "x-armor"), ("leech", "x-leech"), ("freeze", "x-cc"), ("stun", "x-cc"), ("slow", "x-cc"),
              ("pth", "x-hit"), ("dummy", "x-dummy"), ("rr_main", "x-rr"))
WHY_DEFAULT = "x-score"


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _words(text: str) -> int:
    return len(text.split())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def _name(area: str) -> str:
    return area.replace("&", "and")


def _code(area: str) -> str:
    return V.AREA_IDS[area][1] if area in V.AREA_IDS else _slug(area)


# ---------------------------------------------------------------------------
# why, order
# ---------------------------------------------------------------------------

def why_ref_of_target(target: str) -> str:
    """The row of block 4 for a target id of targets.json ("res_ultimate" -> "x-res"); "x-score" for an unknown one."""
    return next((ref for prefix, ref in WHY_TARGET if target.startswith(prefix)), WHY_DEFAULT)


def why_ref(p) -> str:
    """The row of block 4 a suggestion's "Why" points at: the kind of a refund, bind, swap, generated change or uptime; an
    ointment for a resistance target is "x-ointment"; gear and the other consumables go by the target they address."""
    if p.kind in WHY_REF:
        return WHY_REF[p.kind]
    target = p.detail.get("target") or p.id
    if p.kind == "consumable" and p.detail.get("ointment") and target.startswith("res_"):
        return "x-ointment"
    return why_ref_of_target(target)


def fastest_order(props) -> dict:
    """Proposal id -> the CSS `order` of the fastest-first view: `TIER_RANK * 100 + index`, the index counted from 0 inside the
    tier in the order of `guide.sort_steps` (the larger gain first). A tier never reaches into the next one (index <= 99)."""
    count: dict = {}
    out: dict = {}
    for p in GD.sort_steps(props):
        rank = EF.TIER_RANK[EF.tier(p)]
        out[p.id] = rank * 100 + min(count.get(rank, 0), 99)
        count[rank] = count.get(rank, 0) + 1
    return out


# ---------------------------------------------------------------------------
# what
# ---------------------------------------------------------------------------

def _gear_what(p) -> list:
    d = p.detail
    base = CM.action_text(p)
    old = d.get("old_name")
    structured = bool(d.get("name") and d.get("slot") and d.get("slot_part"))
    if structured and old and old != "(empty)":
        return [f"{base}, replacing {old}", base]
    return [base]


def what_of(p, a, permanent_instead: bool = False) -> str:
    """What the player does, as an imperative of at most 16 words. A permanent counterpart of a temporary proposal for the same
    target starts with "Permanent instead:" (mockup)."""
    d = p.detail
    if p.kind == "consumable":
        variants = [f"{CM.action_text(p)} before hard fights", CM.action_text(p)]
    elif p.kind == "gear":
        variants = _gear_what(p)
    elif p.kind in ("skills", "devotion", "attributes"):
        variants = VW.generated_what(p, a)
    elif p.kind == "reroll" and d.get("affix"):
        variants = [f"Reroll the {d.get('reroll_kind', 'affix')} '{d['affix']}' on your {str(d.get('slot', 'item')).lower()}"]
    else:
        variants = [CM.action_text(p)]
    if permanent_instead:
        variants = [PERMANENT_PREFIX + v for v in variants]
    return _fit(variants, WHAT_WORDS)


# ---------------------------------------------------------------------------
# gain
# ---------------------------------------------------------------------------

def _gain_parts(p, a, caps) -> list:
    """The gains of a proposal as phrases, those that touch an open target first: the first as "Armor Absorption 92% to 99%" or
    "Health +936, to 17,557", the others as "Armor +204". A gain a cap swallows (121% to 125% on a cap of 80) is left out."""
    parts = []
    for d in CM.ordered_gains(p, a):
        cap = caps.get(d.label)
        now, new = (d.now, d.new) if cap is None else (min(d.now, cap), min(d.new, cap))
        change = new - now
        if change <= 1e-9 or (d.unit != "%" and f"{change:,.0f}" == "0"):
            continue
        name = CM.view_label(d.label)
        if not parts:
            parts.append(f"{name} {value_text(now, d.unit)} to {value_text(new, d.unit)}" if d.unit == "%"
                         else f"{name} +{change:,.0f}, to {new:,.0f}")
        else:
            parts.append(f"{name} +{value_text(change, d.unit)}" if d.unit == "%" else f"{name} +{change:,.0f}")
    return parts


def _sentence_gain(p) -> str:
    """The gain of a kind that changes no value of the sheet: bind, uptime, devotion swap. "" for any other kind."""
    d = p.detail
    if p.kind == "bind":
        return f"{d.get('power', 'The power')} starts to trigger with {d.get('host', 'your attack')}. Its damage is not modelled"
    if p.kind == "uptime":
        sign = "%" if d.get("kind_index") == 1 else ""
        return (f"Up {float(d.get('uptime_now', 0)) * 100:.0f}% to {VW.uptime_after(p) * 100:.0f}% of the time. "
                f"Its -{float(d.get('value', 0)):g}{sign} {d.get('type', 'enemy')} Resistance then counts as permanent")
    if p.kind == "devotion_swap":
        vp = float(d.get("value_points", 0))
        more = f", about +{vp:.0f}% damage or Health" if round(vp) >= 1 else ""
        return f"+{vp:.1f} value points{more}"
    return ""


def gain_of(p, a, caps) -> str:
    """What the proposal gives, of at most 20 words: up to three values (the ones that touch an open target first), then
    "+k more". A rerolled part says it is the best example roll."""
    suffix = " (best example roll)" if p.kind == "reroll" else ""
    sentence = _sentence_gain(p)
    if sentence:
        return sentence
    parts = _gain_parts(p, a, caps)
    if not parts:
        return "A small gain" + suffix
    for n in range(min(GAIN_PARTS, len(parts)), 0, -1):
        text = ". ".join(parts[:n]) + (f" +{len(parts) - n} more" if len(parts) > n else "") + suffix
        if _words(text) <= GAIN_WORDS:
            return text
    return CM.short_why(parts[0] + suffix, GAIN_WORDS - 1)


# ---------------------------------------------------------------------------
# cost, where
# ---------------------------------------------------------------------------

def _cost_extra(p, a) -> str:
    """What a kind that changes no value of the sheet costs on top of the losses its proposal names; "" for the others."""
    if p.kind == "devotion_swap":
        return "Aether Crystals for the refund. Affinity and bound powers stay"
    if p.kind == "uptime":
        return f"about {VW.extra_cdr(p)}% Cooldown Reduction on your gear"
    if p.kind == "attributes":
        down, _up = VW.attribute_moves(p, a)
        names = [k for _n, k in down] or ["Cunning", "Spirit"]
        return f"check the {_join(names)} your items require first"
    return ""


def _cost_body(p, a) -> str:
    """The cost text of the proposal (every loss named, N-006); a kind with a cost of its own adds it after the losses, it never
    replaces them."""
    if p.kind == "bind":
        return "none"
    extra = _cost_extra(p, a)
    if not extra:
        return CM.cost_text(p, True, CM.view_label)
    d = p.detail
    return f"{CM.cost_text(p, True, CM.view_label)}, {extra}" if p.cost or d.get("lost") or d.get("lost_powers") else extra


def cost_of(p, a) -> tuple[str, bool]:
    """("Cost: ...", hard): every drawback is named and the line is never cut (N-006). A proposal that loses something the model
    does not re-rate (E3) ends it with "; damage not re-rated". `hard`: a stat, a damage line or a power is lost."""
    body = _cost_body(p, a)
    if GD.v2(p).loses_unrated:
        body += "; " + LOSS_END
    d = p.detail
    return f"Cost: {body}", bool(p.cost or d.get("lost") or d.get("lost_powers"))


_BUY = re.compile(r"^buy: (?P<name>.+?)(?: \((?P<rank>[^()]*)\))?$")


def _gear_where(p) -> str:
    src = p.source or ""
    if src == "in your stash":
        return "It is in your stash"
    if src == "craft or find":
        return "Craft or find it"
    m = _BUY.match(src)
    if not m:
        return "Check the vendors"
    name, rank = m.group("name"), m.group("rank")
    pieces = [("you are " + x[5:]) if x.startswith("you: ") else x for x in (rank or "").split(", ") if x]
    return _fit([", ".join([name, *pieces]), name], WHERE_WORDS)


def where_of(p) -> str:
    """Where the player does it, of at most 12 words."""
    d = p.detail
    if p.kind == "consumable":
        n = int(d.get("in_stock") or 0)
        return f"You have {n} in your bags and stash" if p.in_stock and n else "Craft at the Blacksmith or buy"
    if p.kind == "gear":
        return _gear_where(p)
    if p.kind == "bind":
        return "Devotion screen, right-click the power"
    if p.kind in ("refund", "skills", "devotion", "devotion_swap"):
        return "Spirit Guide"
    if p.kind == "attributes":
        return "Tonic of Reshaping"
    if p.kind == "reroll":
        return "Inventor (Alteration)"
    if p.kind == "uptime":
        who, dur, cd = d.get("debuff", "It"), float(d.get("duration", 0)), float(d.get("cooldown", 0))
        return _fit([f"{who} lasts {dur:g} s and recharges in {cd:g} s", f"It lasts {dur:g} s and recharges in {cd:g} s"],
                    WHERE_WORDS)
    return "Check the vendors"


# ---------------------------------------------------------------------------
# impact
# ---------------------------------------------------------------------------

def _covers_open_gate(p, a) -> bool:
    """A consumable that moves a target of a gate that is still open (E2: it covers the gate while it runs)."""
    if p.kind != "consumable":
        return False
    for gate in open_gate_rows(a, gate_rows(a)):
        if any(f.startswith(GATE_TARGETS[gate.id]) for f in p.fixes):
            return True
    return False


def _closed_share(p, a, area: str | None) -> float | None:
    """The share of the deficit the proposal closes: of its own target, else of the target it moves most (by weight) in `area`.
    None when it moves no target."""
    res = {r.id: r for r in a.results}
    after = GD.v2(p).deficits_after
    target = p.detail.get("target")
    if target not in res or target not in after:
        moved = [k for k, left in after.items() if k in res and left is not None and (res[k].deficit or 0.0) - left > 1e-9
                 and (area is None or res[k].area == area)]
        if not moved:
            return None
        target = max(moved, key=lambda k: (res[k].weight, k))
    r, left = res[target], after[target]
    if not r.deficit or r.deficit <= 0 or left is None:
        return None
    return (r.deficit - left) / r.deficit


def impact_of(p, a, area: str | None = None) -> tuple[str, bool]:
    """(words, highlighted) of the impact column, the first rule that fits (spec 3.6): a gate opens; a consumable covers a gate
    while it runs (E2); a rated score gain of a point or more (E3: never next to an unrated loss); how much of the gap a
    partial step closes; free (a bind); a small gain."""
    v = GD.v2(p)
    if v.gates_opened:
        return "Opens an Endgame gate", True
    if _covers_open_gate(p, a):
        return "Covers an Endgame gate while it runs", True
    if not v.loses_unrated and v.score_delta is not None:
        n = RD.round_half_up(v.score_delta)
        if n >= LEVER_MIN:
            return f"about +{n} score (est.)", False
    share = _closed_share(p, a, area)
    if share is not None:
        if share >= MOST_OF_THE_GAP - 1e-9:
            return "most of the gap", False
        return ("half of the gap" if share >= HALF_OF_THE_GAP - 1e-9 else "not enough alone"), False
    return ("free" if p.kind == "bind" else "small gain"), False


# ---------------------------------------------------------------------------
# suggestions
# ---------------------------------------------------------------------------

def _replaces(before, p) -> bool:
    """`p` is the permanent counterpart of the temporary `before` that stands right above it: a consumable and a lasting change
    for the same target that give a value in common."""
    return (before.kind == "consumable" and p.kind != "consumable"
            and (before.detail.get("target") or before.id) == (p.detail.get("target") or p.id)
            and bool({d.label for d in before.gain} & {d.label for d in p.gain}))


class _Shared:
    """What every suggestion of one report needs once: the numbering, the order, the caps, the permanent counterparts (a
    suggestion that stands directly below the temporary one it replaces)."""

    def __init__(self, a, g, extra=()):
        props = list(g.shown) + [p for p in extra if p.id not in {q.id for q in g.shown}]
        self.ids = V.suggestion_ids(g)
        self.order = fastest_order(props)
        self.caps = resist_caps(TGT.resist_rows(a.ctx, a.snap, a.stage))
        self.instead = {p.id for lv in g.area for before, p in zip(lv.members, lv.members[1:]) if _replaces(before, p)}
        self.lever = {p.id: lv.name for lv in g.area for p in lv.members}


def _area_tag(p, lever: str) -> str:
    if lever == GD.SKILLS_AREA:
        return "Devotions" if p.kind in ("bind", "devotion", "devotion_swap") else "Skills"
    return _name(lever)


def _suggestion(p, a, sh: _Shared, lever: str | None) -> V.Suggestion:
    tier = EF.tier(p)
    cost, hard = cost_of(p, a)
    impact, key = impact_of(p, a, lever)
    lever = lever or p.category
    sid = sh.ids.get(p.id) or f"s-{_code(lever)}-0"
    return V.Suggestion(sid, p.id, _area_tag(p, lever), tier, EF.TIER_RANK[tier], what_of(p, a, p.id in sh.instead),
                        gain_of(p, a, sh.caps), cost, hard, where_of(p), impact, key, why_ref(p),
                        sh.order.get(p.id, EF.TIER_RANK[tier] * 100))


def suggestion(p, a, g, area: str | None = None) -> V.Suggestion:
    """The suggestion of one proposal (also one that is not part of the guide); `area` is the name of the area it is listed in,
    by default the one of the guide that holds it."""
    sh = _Shared(a, g, (p,))
    return _suggestion(p, a, sh, area or sh.lever.get(p.id))


# ---------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------

def _note_fit(parts) -> str:
    """The parts of a note as one text of at most 30 words: the last parts go first."""
    for k in range(len(parts), 0, -1):
        text = " ".join(parts[:k])
        if _words(text) <= NOTE_WORDS:
            return text
    return CM.short_why(parts[0], NOTE_WORDS - 1)


def _pick_name(p) -> str:
    """What a "Pick one" note calls a proposal: a devotion plan by its short name ("Devotion plan A"), the change line before the
    first colon; a reroll by its part ("rerolling the prefix 'X'"); anything else by `item_name`. The change line of a plan or a
    reroll is a whole sentence and would cut the note inside a name."""
    d = p.detail
    if d.get("plan"):
        return p.change.split(":")[0]
    if p.kind == "reroll" and d.get("affix"):
        return f"rerolling the {d.get('reroll_kind', 'affix')} '{d['affix']}'"
    return CM.item_name(p)


def _pick_note(c) -> V.Note:
    names = [_pick_name(p) for p in c.members]
    listed = _join(names) if len(names) <= PICK_NAMES else f"{', '.join(names[:PICK_NAMES])} and {len(names) - PICK_NAMES} more"
    if c.label == "Ointment":
        parts = [f"Pick one: {listed} share your ointment slot.", "Only one ointment works at a time."]
    else:
        both = "both" if len(names) == 2 else "all"
        parts = [f"Pick one: {listed} {both} want your {c.label.lower()}.", "Only one fits."]
    return V.Note(V.note_id(c), _note_fit(parts), "Pick one:", pick=True)


def _name_of(label: str) -> str:
    """The name of a target for a sentence: "Physical Res (combat)" -> "Physical Resistance"."""
    name = re.sub(r"\s*\([^)]*\)", "", label).strip()
    if name.startswith("Resistances"):
        return "Resistances"
    return name[:-4] + " Resistance" if name.endswith(" Res") else name


def _short_by(r) -> str:
    """How far a residual target stays short, in its own unit, rounded up so that it never reads as met; "" when the target
    has no plain distance (resistance reduction counts kinds)."""
    n = math.ceil(r.deficit - 1e-9)
    if r.unit == "%":
        return f"{n}%"
    if r.unit == "types":
        return _plural(n, "type")
    if r.unit == "s":
        return f"{n} s"
    if r.unit == "" and r.target.startswith(("health", "da_", "armor", "level")):
        return f"{n:,}"
    return ""


def _after(applied) -> str:
    n = len(applied)
    return "after it" if n <= 1 else "after both" if n == 2 else f"after all {CM.number_word(n)}"


def _residual_note(lv_name: str, r) -> V.Note:
    name = _name_of(r.label)
    verb = "stay" if name == "Resistances" else "stays"
    amount = _short_by(r)
    lead = f"{name} {verb} {amount} short" if amount else f"{name} {verb} short"
    after = f"{lead} {_after(r.applied)}."
    where = [after, f"That leaves {r.now_text}, target {r.target_text}.", NO_FIX] if not amount else [after, NO_FIX]
    text = " ".join(where) if _words(" ".join(where)) <= NOTE_WORDS else " ".join([after, NO_FIX])
    return V.Note(f"n-{_code(lv_name)}-{_slug(r.target)}", text, lead)


def _gate_has_proposal(gate, lv, g, a) -> bool:
    prefixes = GATE_TARGETS[gate.id]
    res = {r.id: r for r in a.results}
    for p in lv.members:
        if p.kind != "consumable" and gate.id in GD.v2(p).gates_opened:
            return True
        if any(f.startswith(prefixes) for f in p.fixes):
            return True
        for k, left in GD.v2(p).deficits_after.items():
            if k.startswith(prefixes) and k in res and left is not None and (res[k].deficit or 0.0) - left > 1e-9:
                return True
    return any(r.target.startswith(prefixes) for r in g.residual if r.area == lv.name)


def _gate_notes(a, g, lv) -> list:
    """The open Endgame gates of the area that no shown proposal closes, covers or moves: "No single change in your gear ..."; the
    level gate has its own words (only levelling closes it)."""
    if a.stage is not Stage.MID:
        return []
    rows = TGT.resist_rows(a.ctx, a.snap, a.stage)
    out = []
    for gate in open_gate_rows(a, gate_rows(a)):
        if GD.GATE_AREA[gate.id] != lv.name:
            continue
        if gate.id == "level":
            lead = f"Reach level {gate.target:g}."
            out.append(V.Note("n-level", f"{lead} No gear change closes this gate.", lead))
        elif not _gate_has_proposal(gate, lv, g, a):
            now, target, _f = tile_values(gate, rows)
            more = len(gate.below) - 1 if gate.id == "resistances" else 0
            label = f"{gate.worst} Resistance" if gate.id == "resistances" and gate.worst else tile_label(gate)
            rest = f", and {_plural(more, 'more type')} {'is' if more == 1 else 'are'} short." if more > 0 else "."
            lead = f"{label} is {now}, target {target}{rest}"
            out.append(V.Note(f"n-{gate.id}", f"{lead} {NO_FIX}", lead))
    return out


def _damage_note(rr) -> V.Note | None:
    """Damage rises with resistance reduction: the first permanent suggestion of that area, linked. `rr` are the (suggestion,
    proposal) pairs of the Resistance Reduction area."""
    first = next((s for s, p in rr if p.kind != "consumable"), None)
    if first is None:
        return None
    link = lower_first(first.what)
    lead = "Resistance Reduction raises your damage."
    return V.Note("n-dmg-rr", f"{lead} Start here: {link}.", lead, ref=first.id, link=link)


def _lever_notes(a, g, lv, sh, conflict_home) -> list:
    notes = [_pick_note(c) for c in g.conflicts if conflict_home.get(c.slot) == lv.name]
    notes += [_residual_note(lv.name, r) for r in g.residual if r.area == lv.name]
    notes += _gate_notes(a, g, lv)
    return notes


# ---------------------------------------------------------------------------
# areas
# ---------------------------------------------------------------------------

def _rendered(lv) -> bool:
    """An area gets a section when the guide has a proposal for it, an open gate, or its open targets cost a point."""
    return bool(lv.members or lv.gates or RD.round_half_up(lv.worth) >= LEVER_MIN)


def _tag(lv) -> str:
    if lv.gates:
        return _plural(lv.gates, "Endgame gate")
    if RD.round_half_up(lv.worth) >= LEVER_MIN:
        return f"worth about +{RD.round_half_up(lv.worth)} when solved"
    return "small gains" if lv.members else ""


def _status(a, name: str) -> str:
    return _STATUS[a.areas.get(name, {}).get("status", "INFO")]


def _quiet_item(a, name: str) -> V.Note:
    disp = _name(name)
    status = a.areas.get(name, {}).get("status", "INFO")
    q = a.build_quality
    open_check = VAR.has_open_check(a, name)
    if name == GD.SKILLS_AREA and status == "OK" and q is not None and not (q.pet or q.early or q.error) \
            and q.skills_status() == "OK" and not open_check:
        return V.Note("q-" + _code(name), "Skill points sit well. None worth moving.", "Skill points")
    if name == "Crowd Control" and status == "INFO" and a.stage is not Stage.ENDGAME:
        text = f"{disp} targets come in Endgame."
    elif open_check and status != "INFO":                  # OK or not: the panel's own checks say what to do
        text = OPEN_CHECKS.format(disp)
    elif status == "OK":
        text = f"{disp} nothing to do."
    elif status == "INFO":
        text = (f"{disp} is not rated while you are levelling." if a.stage is Stage.EARLY
                else f"{disp} is not rated for your build.")
    else:
        text = f"{disp} no single fix yet."
    return V.Note("q-" + _code(name), text, disp)


def _empty_area(a) -> V.ImproveArea:
    """No proposal and no area worth a section: say so (no single fix found, or nothing open at all)."""
    states = {r.status for r in a.results}
    if states & {"gap", "tight"}:
        word = "Gap" if "gap" in states else "Risk"
        note = V.Note("n-none", f"No single fix found. {NO_FIX}", "No single fix found.")
    elif VAR.has_open_check(a, GD.SKILLS_AREA):
        word = "Risk"
        note = V.Note("n-none", "No fix to suggest. " + OPEN_CHECKS.format(_name(GD.SKILLS_AREA)), "No fix to suggest.")
    else:
        word = "OK"
        note = V.Note("n-none", "Nothing to do. Every target for your stage is met.", "Nothing to do.")
    return V.ImproveArea("a-none", "Improvements", word, "", (), (note,))


def improvements(a, g) -> tuple:
    """Block 3 of the report view: one section per area of the guide that has something to show, the quiet bar under the last."""
    sh = _Shared(a, g)
    levers = [lv for lv in g.area if _rendered(lv)]
    if not levers:
        return (_empty_area(a),)
    home = {c.slot: sh.lever.get(c.members[0].id) for c in g.conflicts}
    sugs = {lv.name: [(_suggestion(p, a, sh, lv.name), p) for p in lv.members] for lv in levers}
    out = []
    for lv in levers:
        notes = _lever_notes(a, g, lv, sh, home)
        if lv.name == "Damage":
            cross = _damage_note(sugs.get("Resistance Reduction") or [])
            notes += [cross] if cross else []
        if not sugs[lv.name] and not notes:
            notes = [V.Note(f"n-{_code(lv.name)}-none", f"No single fix yet. {NO_FIX}", "No single fix yet.")]
        out.append(V.ImproveArea(V.area_anchor(lv.name), _name(lv.name), _status(a, lv.name), _tag(lv),
                                 tuple(s for s, _p in sugs[lv.name]), tuple(notes)))
    shown = {lv.name for lv in levers}
    quiet = tuple(_quiet_item(a, name) for name in RD.AREAS if name not in shown)
    out[-1] = dataclasses.replace(out[-1], quiet=quiet)
    return tuple(out)
