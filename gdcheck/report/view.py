"""Report v2: the view model - plain frozen data the HTML renderer formats and nothing else (spec 3.1, 3.2).

`ReportView` has the four blocks of the mockup (docs/design/report-mockup-v1.html): the overview (`Overview`, built by
`view_overview`), the area panels (`Group`, task 9), the improvements (`ImproveArea`, task 10) and the explanation tables
(`XTable`, task 10). A text that carries a bold lead in the mockup is stored whole, and its bold start separately as `*_lead`
or `lead` (the lead is always the start of the whole text), so a check of the words counts the whole text.

References (`ref`, `fix_ref`, `lever_ref`) are element ids without the '#': `s-<area>-<n>` a suggestion of block 3, `a-<area>`
an area of block 3, `n-<slot>` a note of block 3, `p-<panel>` a panel of block 2, `x-<topic>` a row of block 4.
`suggestion_ids(guide)` is the one place that numbers the suggestions; block 3 must use it, or the links of block 1 break.

The types of blocks 2 to 4 are a first cut from the mockup; the tasks that build those blocks extend them where the mockup
needs more."""
from __future__ import annotations

import re
from dataclasses import dataclass

# area -> (id of its section in block 3, code in the ids of its suggestions)
AREA_IDS = {"Health": ("a-health", "hp"), "Resistances": ("a-res", "res"), "Survival": ("a-survival", "surv"),
            "Sustain": ("a-sustain", "sus"), "Crowd Control": ("a-cc", "cc"), "Damage": ("a-damage", "dmg"),
            "Resistance Reduction": ("a-rr", "rr"), "Skills & Devotions": ("a-build", "sd")}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


DEVOTIONS_PANEL = "p-devotions"   # the Devotions panel of block 2 - always rendered, so a link to it never dangles


def area_anchor(name: str) -> str:
    """The id of an area's section in block 3 ("a-res")."""
    return AREA_IDS[name][0] if name in AREA_IDS else f"a-{_slug(name)}"


def suggestion_ids(guide) -> dict:
    """Proposal id -> id of its suggestion in block 3 ("s-res-2": the code of its area and its place, from 1, among the
    proposals the guide shows for the area, in the order of `Lever.members`). Every proposal is in one area only."""
    out: dict = {}
    for lever in guide.area:
        code = AREA_IDS[lever.name][1] if lever.name in AREA_IDS else _slug(lever.name)
        for k, p in enumerate(lever.members, 1):
            out[p.id] = f"s-{code}-{k}"
    return out


def note_id(conflict) -> str:
    """The id of the "Pick one" note of a conflict ("n-amulet": the slot, or "n-ointment")."""
    return "n-" + _slug(conflict.slot.split(":")[0])


# ---------------------------------------------------------------------------
# block 1: overview
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Fact:
    label: str
    value: str


@dataclass(frozen=True)
class Track:
    """The stage track. `bands` are (label, low, high) of Early, Mid and Endgame; `stage` the label of the current one;
    `ghost` the score after the steps, only when it is above the score; `locked` whether Endgame is still closed and
    `open_gates` how many gates keep it closed (0 before level 85)."""
    score: int
    bands: tuple
    stage: str
    ghost: int | None
    ghost_note: str
    locked: bool
    open_gates: int


@dataclass(frozen=True)
class GateTile:
    """One open Endgame gate. `label` names the value that is short, `now` and `target` are written out ("79%", "17,000"),
    `fraction` is now / target between 0 and 1, `status` "Risk" or "Gap". `fix` is the whole line under the bar and
    `fix_lead` its bold start (the name of the fix, or "No single fix yet."); `fix_ref` the suggestion (or the area) it
    links to, "" without a link."""
    id: str
    label: str
    now: str
    target: str
    fraction: float
    status: str
    fix: str
    fix_ref: str
    fix_lead: str = ""


@dataclass(frozen=True)
class Step:
    """One step of "Do next, fastest first": `what` links to the suggestion `ref`; `effort` is the tier word and
    `effort_rank` its number (1 Now ... 4 Farm); `result` says what changes, `cost` what it costs ("" without a cost)."""
    what: str
    ref: str
    effort: str
    effort_rank: int
    result: str
    cost: str = ""


@dataclass(frozen=True)
class Watch:
    """One "Watch out" item: `head` is the whole first line, `lead` its bold start, `extra` the second line, `link` the
    words of the link at its end and `ref` where it points ("" when there is no link)."""
    status: str
    head: str
    lead: str
    extra: str
    link: str
    ref: str


@dataclass(frozen=True)
class Overview:
    """Block 1. `cls` is the class name and `masteries` the two masteries ("Demolitionist and Arcanist"); `stale` the line
    about game data that were not checked against the live patch ("" when they were); `gates_met` the labels of the gates
    that are already met; `lever` the bold start of the lever line and `lever_note` its sentence about the worth."""
    name: str
    cls: str
    masteries: str
    facts: tuple
    stale: str
    verdict: str
    track: Track
    gate_head: str
    gates: tuple
    gates_met: tuple
    steps: tuple
    lever: str
    lever_ref: str
    watch: tuple
    lever_note: str = ""


# ---------------------------------------------------------------------------
# block 2: areas (task 9)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Tick:
    """A label on an axis at `at` (0 to 100 along the bar); `mid` marks the target of the current stage; `tone` colours a label
    that stands for the character's own value (the status word in lower case: "gap")."""
    at: float
    text: str
    mid: bool = False
    tone: str = ""


@dataclass(frozen=True)
class Meter:
    """A measuring bar: the value against its targets. `fill` and the marks are positions on the bar (0 to 100): `mid` the
    target of the current stage (solid), `later` the Endgame target (grey), `ghost_to` where the best shown change would
    take the value (dashed, from `fill`). `negative`: the value is below zero and the bar runs from `fill` to `mid`, the zero
    mark."""
    name: str
    value: str
    target: str
    status: str
    fill: float
    ticks: tuple = ()
    ghost_to: float | None = None
    mid: float | None = None
    later: float | None = None
    negative: bool = False


@dataclass(frozen=True)
class ResCell:
    """One of the nine resistances: `value` ("84%"), `spare` ("96 spare" or "1 short"), `low` when it is short of its target,
    `key` the colour key of its damage type ("fire")."""
    name: str
    value: str
    spare: str
    low: bool = False
    key: str = ""


@dataclass(frozen=True)
class RRSlot:
    """One kind of resistance reduction (Stacking, Reduced, Flat): `amount` ("-100", "-15%" or "none"), `source` where it
    comes from and `state` "full" (permanent), "half" (exists but is not permanent: counts half) or "none"."""
    kind: str
    amount: str
    source: str
    state: str = ""


@dataclass(frozen=True)
class Check:
    """One line of a checklist: a tick or a cross, the text and a small second line."""
    ok: bool
    text: str
    sub: str = ""


@dataclass(frozen=True)
class BossTable:
    """The boss matrix: `columns` are the weak spots, a row is (boss name, one flag per column, one title per column)."""
    head: str
    columns: tuple
    rows: tuple
    more: str = ""


@dataclass(frozen=True)
class TimeAxis:
    """The time-to-shield bar of the Training Dummy: positions on the axis (0 to 100) and the label for screen readers."""
    label: str
    you: float
    low: float
    high: float
    goals: tuple = ()
    ticks: tuple = ()


@dataclass(frozen=True)
class Chip:
    """A small fact: `text` and its bold `value`; `key` the colour key of a damage type ("fire", "" for none)."""
    text: str
    value: str = ""
    key: str = ""


@dataclass(frozen=True)
class Panel:
    """One panel of block 2; `key` is the key sentence (at most 20 words) and `key_bold` the part of it printed bold, `note`
    the line under the meters (at most 20 words), `foot` the links at the bottom as (text, ref) - a ref of "" is plain text.
    `status` is the word shown (OK, Risk, Gap, Info, or "5 high" / "3 medium" on the Bosses panel) and `tone` its colour
    class (ok, risk, gap, info). `size` is "wide", "third" or "" (a normal panel)."""
    id: str
    title: str
    status: str
    key: str = ""
    meters: tuple = ()
    cells: tuple = ()
    rr: tuple = ()
    checks: tuple = ()
    bosses: BossTable | None = None
    time: TimeAxis | None = None
    chips: tuple = ()
    foot: tuple = ()
    size: str = ""
    tone: str = ""
    key_bold: str = ""
    note: str = ""


@dataclass(frozen=True)
class Group:
    title: str
    panels: tuple


# ---------------------------------------------------------------------------
# block 3: improvements (task 10)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Suggestion:
    """One proposal as the reader sees it. `id` is its element id (`suggestion_ids`), `proposal` the id of the proposal;
    `effort` the tier word and `effort_rank` its number, `order` the CSS order of the fastest-first view
    (TIER_RANK * 100 + index); `impact` the words of the impact column and `impact_key` whether they are highlighted."""
    id: str
    proposal: str
    area: str
    effort: str
    effort_rank: int
    what: str
    gain: str
    cost: str
    cost_hard: bool
    where: str
    impact: str
    impact_key: bool
    why_ref: str
    order: int


@dataclass(frozen=True)
class Note:
    """A line under the suggestions of an area (or in its quiet bar). `text` is the whole text and `lead` its bold start; `pick`
    marks the "Pick one" note; `link` is the words of `text` that link to `ref` (an element id without '#'), "" without a link."""
    id: str
    text: str
    lead: str = ""
    pick: bool = False
    ref: str = ""
    link: str = ""


@dataclass(frozen=True)
class ImproveArea:
    id: str
    name: str
    status: str
    tag: str
    suggestions: tuple
    notes: tuple = ()
    quiet: tuple = ()


# ---------------------------------------------------------------------------
# block 4: explanations (task 10)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class XRow:
    id: str
    cells: tuple
    conf: str = ""


@dataclass(frozen=True)
class XTable:
    id: str
    title: str
    columns: tuple
    rows: tuple


@dataclass(frozen=True)
class ReportView:
    overview: Overview
    groups: tuple
    improvements: tuple
    explain: tuple
    footer: str
