"""Analysis -> ReportModel (AP8). Player View (6 areas with a value, max 3 issues, max 3 actions,
max 3 bosses), Expert View (E1-E6), Detail (D1-D6, D11). Only selection and wording
happen here - every number comes from the analysis; the area texts and D11 live in area_text.py."""
from __future__ import annotations

import math
import re
from pathlib import Path

from ..analysis import readiness as RD
from ..analysis.readiness import AREAS
from ..domain.enums import LiveStatus
from ..data.text import field_label
from . import area_text as AT
from . import build_tables as BT
from .score_text import score_parts as _score_parts
from .model import Area, PlayerAction, PlayerBoss, PlayerIssue, Report, Table
from .text_util import int_text as _int_text
from .text_util import num as _n
from .text_util import player_now as _player_now
from .text_util import worst_first as _worst_first

PROFILE_NAME = {"general": "General Ultimate", "sr": "Shattered Realm", "ascendant": "Ascendant",
                "celestial": "Celestials"}
MASTERY_SHORT = {"Demolitionist": "Demo", "Occultist": "Occu", "Nightblade": "NB", "Arcanist": "Arca"}
CLASS_SHORT = {"Sorcerer": "Sorc", "Sorceress": "Sorc", "Blademaster": "BM", "Elementalist": "Ele",
               "Saboteur": "Sabo", "Witch Hunter": "WH", "Warder": "Warder", "Battlemage": "BaMa",
               "Pyromancer": "Pyro", "Vindicator": "Vindi"}
STATUS_WORD = {"OK": "OK", "RISK": "RISK", "GAP": "GAP", "INFO": "INFO"}
ROOT = Path(__file__).resolve().parents[2]


def _signed(d, player: bool = False):
    unit = d.unit if d.unit == "%" else ""
    ch = d.change
    if player and abs(ch) < 0.5:              # B7: no decimals in the Player View - never '+0%'
        return f"{d.label} {'+' if ch > 0 else '-'}<1{unit}"
    num = f"{ch:,.1f}" if unit == "%" and abs(ch) < 1 and not player else f"{ch:,.0f}"
    return f"{d.label} {'+' if ch > 0 else ''}{num}{unit}"


def class_line(ch) -> str:
    """In-game class and mastery names (.62, product review: 'BM (Soldier/NB)' next to full names)."""
    ms = [n for _, n, _ in ch.masteries]
    cls = ch.class_name or (ms[0] if ms else "?")
    if len(ms) >= 2:
        return f"{cls} ({'/'.join(ms)})"
    return cls


def version_line(ctx, meta) -> tuple[str, list]:
    gv = ctx.game_version
    st = ctx.live_status
    warnings = []
    if st == LiveStatus.MATCHES_LIVE:
        txt = f"Game data {gv} - matches live patch {ctx.options.live_version}" + (
            f" (checked {ctx.options.live_checked})" if ctx.options.live_checked else "")
    elif st == LiveStatus.MATCHES_INSTALLED:
        txt = f"Game data {gv} - matches your installed game"
    elif st == LiveStatus.MISMATCH:
        live = ctx.options.live_version or "your installed game"
        txt = f"Game data {gv} - DOES NOT MATCH {live}"
        warnings.append(f"Game data {gv} does not match {live}: item values and rules may be "
                        "outdated - rebuild the game data with GD-Tools before acting on the advice.")
    else:
        txt = f"Game data {gv} - live patch not checked"
    if meta.get("mechanics_note"):
        warnings.append(meta["mechanics_note"])
    return txt, warnings


# ---------------------------------------------------------------------------
# Player View
# ---------------------------------------------------------------------------

def player_issues(a) -> list[PlayerIssue]:
    out = []
    for i in a.issues:
        if i.self_penalty:
            title = re.sub(r"^\s*drawback for you:\s*", "", i.title, flags=re.I)
            detail = re.sub(r"(-?\d+(?:\.\d+)?) -> (-?\d+(?:\.\d+)?) ?%", r"\1% to \2%", i.detail)
            short = _penalty_short(detail)
            if re.search(r"\d", short):           # B3: the values are sheet values (E1 shows combat values)
                short = short.rstrip(" .") + " (character sheet)."
            out.append(PlayerIssue(f"Drawback for you: {title} - {short}".rstrip(" -"), _penalty_why(detail),
                                   "self_penalty"))
    for i in a.issues:
        if i.severity == "ERROR" and not i.self_penalty:
            out.append(PlayerIssue(_issue_title(i), short_why(i.fix), "error"))
    imp = (getattr(a, "score", None) or {}).get("impact", {})          # stand-ins have no score: impact 0, then fact, then id
    key = lambda r: (-imp.get(r.id, 0.0), r.basis != "fact", r.id)       # noqa: E731
    gaps = sorted((r for r in a.results if r.status == "gap"), key=key)
    tights = sorted((r for r in a.results if r.status == "tight"), key=key)
    for r in gaps:
        if r.basis == "fact":
            out.append(_target_issue(r, a))
    # 9.2: a community target is a top problem only when no fact target is open (.61); a fact target
    # within its tolerance ('tight') is not open (.62, code review: it suppressed real community gaps)
    fact_open = any(r.basis == "fact" for r in gaps)
    if not fact_open:
        out.extend(_target_issue(r, a) for r in gaps)
    for i in a.issues:
        if i.severity == "WARNING" and not i.self_penalty:
            out.append(PlayerIssue(_issue_title(i), short_why(i.fix), "warning"))
    # .72 (N4): a target within its tolerance comes after the open gaps and the warnings - before, Aether 80/83 led
    # Cpt Piercewolf over a missing Life Leech Resistance
    # I4: 9.2 holds for a community 'tight' too - it is a top problem only while no fact target is a gap; a fact 'tight' stays (N4)
    out.extend(_target_issue(r, a) for r in tights if r.basis == "fact" or not fact_open)
    return out[:3]


NOT_BY_GEAR = ("dummy_ttk_mid", "dummy_ttk_end", "rr_main")      # no proposal changes them: skills, devotions, play style do (K7, m2)


def _target_issue(r, a) -> PlayerIssue:
    """The top issue of a target that is a gap or tight: a fact target 'must be', a community target 'recommended'. The Training
    Dummy time and the resistance reduction add ', not by gear' - the reason ('Gear cannot close this ...') opens only in the HTML."""
    fact = r.basis == "fact"
    now = _worst_first(_player_now(r, a), 9) if fact else _player_now(r, a)
    tail = ", not by gear" if r.id in NOT_BY_GEAR else ""
    return PlayerIssue(f"{r.label} {now} ({'must be' if fact else 'recommended'} {r.target_text.split(' (')[0]}){tail}",
                       _player_why(r), "must" if fact else "recommended", r.id)


def _below_cap(detail: str) -> tuple[str, str, str] | None:
    """(when, causes, resistances) of a self-penalty detail that pushes resistances below the cap - 'when' and 'causes' are
    the head 'While ...: -33% ...; Physical 11% to -1%', the resistances the BELOW CAP part with equal changes grouped
    ('Fire, Cold, Lightning 81% to 48%'); None without a BELOW CAP part."""
    if " BELOW CAP: " not in detail:
        return None
    head, below = detail.split(" BELOW CAP: ", 1)
    when, _, parts = head.partition(": ")
    groups: dict = {}
    for x in below.rstrip(" .").split(", "):
        name, _, change = x.partition(" ")
        groups.setdefault(change, []).append(name)
    return when, parts, "; ".join(f"{', '.join(n)} {c}" for c, n in groups.items())


def _penalty_why(detail: str) -> str:
    """The reason of a self-penalty (.75, N-011: at most 25 words like every reason; it was the whole detail, 36 words for Cpt
    Slovi): what falls below the cap comes first, so the cut never drops it, then the causes; the 'while the effect is
    active' lead stays in the title line, the full text in D1 and the Expert View."""
    below = _below_cap(detail)
    if below is None:
        return short_why(detail)
    _when, parts, res = below
    items = [x for x in parts.rstrip(" .").split("; ") if x]
    phys = [x for x in items if x.startswith("Physical ")]           # a result (sheet value before -> after), not a cause
    causes = [x for x in items if not x.startswith("Physical ")]    # (review .75)
    return short_why(f"Below the cap: {res}" + "".join(f"; {x}" for x in phys) + ". Cause: " + "; ".join(causes) + ".")


def _penalty_short(detail: str) -> str:
    """Self-penalty line for the Player View (.62, N-011: Cpt Slovi's was 55 words): when it pushes
    resistances below the cap, only those (equal changes grouped) plus Physical; the full text stays in
    the reason and in D1."""
    below = _below_cap(detail)
    if below is None:
        return detail
    when, parts, res = below
    phys = next((p for p in parts.rstrip(".").split("; ") if p.startswith("Physical ")), "")
    return f"{when}: {res} (below the cap)" + (f"; {phys}" if phys else "") + "."


def _issue_title(i, words: int = 16) -> str:
    """Title plus the first sentence of the detail when it is short (.62, product review: 'Unspent points'
    without a count, 'Kraken: weapon requirement not met' without saying what is needed)."""
    sentences = [x.rstrip(".") for x in re.split(r"(?<=[.!?])\s", str(getattr(i, "detail", "") or "").strip()) if x]
    first = next((x for x in sentences if x.startswith("Requirement:")), sentences[0] if sentences else "")
    if (not first or len(first.split()) > words or first.lower() in i.title.lower()
            or "savefile" in first.lower()):
        return i.title
    return f"{i.title} - " + ("needs " + first.split(":", 1)[1].strip() if first.startswith("Requirement:") else first)


def short_why(text: str, words: int = 25) -> str:
    """N-011: a reason of at most 25 words, cut at a word boundary (before: 160 characters, cut mid-word).
    A cut never leaves an open bracket or a trailing dash (.62, code review)."""
    w = str(text or "").split()
    if len(w) <= words:
        return " ".join(w)
    cut = " ".join(w[:words])
    if cut.count("(") > cut.count(")"):
        cut = cut[:cut.rfind("(")]
    return cut.rstrip(" ,;:-(") + " ..."


def _player_why(r) -> str:
    """Reason of a target in plain words (.62): the target's own player_why from targets.json; never
    the source note, which names database files and sources (N-008, review of .61)."""
    return short_why(getattr(r, "player_why", "") or getattr(r, "player_text", "") or r.label)


def _lost_grouped(lost) -> list[str]:
    """Player View (A4, actions <=2 lines): '+50% Aether Damage', '+50% Fire Damage' -> one
    'you lose +50% Aether and Fire Damage' - every drawback still named, only shorter."""
    groups: dict = {}
    other = []
    for x in lost:
        m = re.fullmatch(r"(\S+) (.+) Damage", str(x))
        if m:
            groups.setdefault(m.group(1), []).append(m.group(2))
        else:
            other.append(str(x))
    parts = [f"{v} " + (", ".join(n[:-1]) + " and " + n[-1] if len(n) > 1 else n[0]) + " Damage"
             for v, n in groups.items()]
    return ([f"you lose {', '.join(parts)}"] if parts else []) + [f"you lose {x}" for x in other]


def _cost_text(p, player: bool = False) -> str:
    """Every drawback is named (N-006) - costs are never cut off; lost bound Celestial Powers
    count as a drawback (the counterfactual cannot see procs)."""
    lost = [f"loses Celestial Power {n} (bound to {b}" + (", your low-health circuit breaker" if t == "LowHealth"
            else "") + ")" for n, b, t in (p.detail.get("lost_powers") or ())]
    raw = list(p.detail.get("lost") or ())
    lost += _lost_grouped(raw) if player else [f"you lose {x}" for x in raw]
    if p.cost or lost:
        return ", ".join([_signed(d, player) for d in p.cost] + lost)
    if p.kind == "consumable":
        if not p.detail.get("ointment"):
            return "none (temporary)"
        return "uses your ointment slot (one at a time)" if player else \
            "uses your ointment slot - only one ointment works at a time"
    return "none for your stats"


_GAIN_FOR = {"health": ("Health",), "da": ("Defensive Ability",), "physical": ("Physical Res",),
             "absorption": ("Armor Absorption",), "armor": ("Armor (weighted)",), "leech": ("Life Leech Res",),
             "freeze": ("Freeze Res",), "stun": ("Stun Res",), "slow": ("Slow Res",), "pth": ("Offensive Ability",)}


def _gain_text(p, a, n: int = 3, player: bool = True) -> str:
    """Gains that touch an open target first, then the rest; '+k more' instead of silently cutting
    (.62, product review: Cpt Cheat's Aether +18% that closes a gap was the 4th value and hidden). E3 uses
    it too (.67, Cpt Piercewolf: Aether Res +20% closed the target but was the 5th gain), with the
    expert number format (player=False)."""
    open_ids = [r.id for r in a.results if r.status in ("gap", "tight")]
    below = set()
    for r in a.results:
        if r.id in ("res_ultimate", "res_own_cap") and r.status in ("gap", "tight") and "below: " in r.now_text:
            below |= {x.rsplit(" ", 1)[0] + " Res" for x in r.now_text.split("below: ")[-1].split(", ")}
        elif r.status in ("gap", "tight") and r.id.startswith(("ravager", "fire_", "lightning_")):
            below.add(r.label.split(" buffer")[0].split()[-1] + " Res")

    def touches(d) -> bool:
        if d.label in below:
            return True
        return any(d.label in labs for k, labs in _GAIN_FOR.items() if any(i.startswith(k) for i in open_ids))
    gains = sorted(p.gain, key=lambda d: not touches(d))
    txt = ", ".join(_signed(d, player) for d in gains[:n])
    return txt + (f" +{len(gains) - n} more" if len(gains) > n else "")


def player_actions(a) -> list[PlayerAction]:
    out = []
    for p in a.player_actions:
        tags = []
        if p.in_stock:
            tags.append(p.source if p.kind == "consumable" and p.source else "in your stash")
        elif p.source:
            tags.append(p.source)
        if p.consumable:
            tags.append(f"consumable, {p.consumable}")
        gain = _gain_text(p, a)
        if not gain:                     # .62: no measurable gain - never claim one ('closes a target')
            continue
        cost = _cost_text(p, True)
        n = 3
        while n > 1 and _action_line_len(p.change, gain, cost, tags) > PLAYER_LINE_BUDGET:
            n -= 1                       # .67: fewer gains (target fixes first), costs are never cut (N-006)
            gain = _gain_text(p, a, n)
        out.append(PlayerAction(p.change, gain, cost, tags, p.priority, p.effort))
    return out


# one 'Do this next' line of the chat view must fit two lines of 120 characters (.67, Cpt Piercewolf:
# 'Overseer's Nyarlathon's Gaze of Balanced Steel instead of ... of Kings' + 3 gains + 4 costs = 3 lines)
PLAYER_LINE_BUDGET = 236


def _action_line_len(what: str, gain: str, cost: str, tags) -> int:
    """Length of the chat line render_md writes for one action ('1. **what** - gain: ... · cost: ...')."""
    return len(f"1. **{what}** - gain: {gain} · cost: {cost}" + (f" · {', '.join(tags)}" if tags else ""))


BOSS_LEGEND = ("Risk: HIGH = a resistance more than 10 short or 3+ weak spots; MEDIUM = 1-2 weak spots; "
               "LOW = none found.")


def boss_fights(a) -> dict:
    """Boss name -> fight key (B2): forms and phases of one fight share the key. The link is the boss
    data's spawn-on-death chain ('phases', data.boss_graph), never the name - Cpt Slovi: 'The Final
    Form ...' and 'The Actually Final Form ...' are one fight. Bosses without data: their own key."""
    try:
        from ..data import bosses as BD
        from ..data import versioned as V
        ver, fp = V.index_identity(a.ctx.index)
        data = (BD.combat_meta(ROOT, ver or "1.3.0.8", fp) or {}).get("bosses") or {}
    except (OSError, ValueError, AttributeError, TypeError):   # no boss data: every boss is its own fight
        data = {}
    parent: dict = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for b in data.values():
        recs = [b.get("record")] + [ph.get("record") for ph in b.get("phases") or ()]
        recs = [r for r in recs if r]
        for r in recs[1:]:
            parent[find(r)] = find(recs[0])
    return {name: find(b["record"]) for name, b in data.items() if b.get("record")}


def only_with_consumable(b) -> bool:
    """N-010: the named consumable closes every reason - the boss is prepared only with it. The DA-shred
    reason has no consumable field, so a boss with it is never 'with consumable'; nor is one that needs two
    ointments at once (only one works at a time)."""
    return bool(b.consumable) and bool(b.reasons) and not getattr(b, "ointments", ()) \
        and not any(r.startswith("lowers your DA") for r in b.reasons)


def player_bosses(a) -> list[PlayerBoss]:
    """B2: one line per fight (the worst form - a.bosses is sorted worst first), up to three reasons."""
    out, seen = [], set()
    fights = boss_fights(a) if a.bosses else {}
    for b in a.bosses:
        key = fights.get(b.name, b.name)
        if key in seen:
            continue
        seen.add(key)
        reasons = [r.replace(" Res ", " Resistance ") for r in b.reasons[:3]]
        if len(b.reasons) > 3:
            reasons.append(f"+{len(b.reasons) - 3} more")
        reason = "; ".join(reasons) or "no specific weakness found"
        prepare = ""
        if b.consumable:
            prepare = "prepare: " + re.sub(r" \((.*)\)$", r", \1", b.consumable) + (
                " (with consumable)" if only_with_consumable(b) else "")
        out.append(PlayerBoss(b.name, b.risk, reason, prepare))
        if len(out) >= 3:
            break
    return out


# ---------------------------------------------------------------------------
# Expert View
# ---------------------------------------------------------------------------

# Which snapshot a target metric reads (analysis.targets.metric) - B3: every E1 value names its basis
COMBAT_METRICS = {"da_combat", "physical_combat", "freeze_res", "stun_res", "slow_res", "pth_vs_boss_da",
                  "res_buffer", "res_ultimate_count"}
SHEET_METRICS = {"health", "absorption", "armor_weighted", "leech_res", "res_at_cap_count"}
# B6: expert-only label - the value is a PtH, not a percentage; targets.json keeps the plain label, which
# can reach the Player View as a 'recommended' issue where 'PtH' is not allowed (N-008)
E1_LABEL = {"pth_vs_boss_da": ("Hit chance", "PtH (Probability to Hit value, not a %)")}
_METRIC_OF: dict = {}


def _metric_of(tid: str) -> str:
    if not _METRIC_OF:
        from ..analysis import targets as TG
        try:
            _METRIC_OF.update({t["id"]: t["metric"] for t in TG.load()["targets"]})
        except Exception:                 # noqa: BLE001 - the label is cosmetic, never fail the report
            _METRIC_OF["_"] = ""
    return _METRIC_OF.get(tid, "")


def _value_basis(tid: str) -> str:
    m = _metric_of(tid)
    return "combat value" if m in COMBAT_METRICS else "character sheet" if m in SHEET_METRICS else "-"


def _purge_line(a) -> str:
    """X-010: buff-on (combat) and buff-off (after a monster purge) values side by side."""
    c, p = a.snap.combat, a.snap.purged

    def res(s):
        r = s.resists["Physical"]
        return min(r["value"], r["cap"])

    def capped(s):
        return sum(1 for n, r in s.resists.items() if n != "Physical" and r["value"] >= r["cap"] - 1e-9)
    n = sum(1 for k in c.resists if k != "Physical")
    return (f"Buffs on / after a monster purge (X-010): Physical Res {res(c):.0f}% with buffs, {res(p):.0f}% "
            f"after purge; Defensive Ability {c.abilities['da']:,.0f} / {p.abilities['da']:,.0f}; Health "
            f"{c.abilities['health']:,.0f} / {p.abilities['health']:,.0f}; resistances at your cap "
            f"{capped(c)}/{n} / {capped(p)}/{n}.")


def e1(a) -> Table:
    rows = []
    group = {"core": "Core", "cc_sustain": "CC & Sustain", "offense": "Offense", "scenario": "Scenario"}
    for r in a.results:
        if r.status == "info":                  # .72: a target without a value (dummy time, resistance reduction of a pet build) has no row
            continue
        label = r.label
        old_new = E1_LABEL.get(_metric_of(r.id))
        if old_new:
            label = label.replace(*old_new)
        rows.append([group.get(r.group, r.group), label, r.now_text, _value_basis(r.id), r.target_text, r.basis,
                     r.status])
    notes = [f"Stage {a.stage.value}; tight = within tolerance (Res 3, Physical 4, Health/DA/armor 5%, resistance reduction 0.2, dummy time 25%). "
             "combat value = character sheet + buffs kept up (duration / cooldown >= 95%, X-018); character "
             "sheet = as the game's character sheet shows it. " + _purge_line(a)]
    if a.stage_fails:                           # .72: one note (a note costs two Markdown lines, the Expert View has 150)
        notes[0] += " Not ENDGAME yet: " + "; ".join(a.stage_fails[:4])
    if any(r.id.startswith("ravager_") for r in a.results):
        from ..analysis.bosses import boss_rr
        rr, src = boss_rr(a.ctx.index)
        notes.append("Ravager targets = your cap + the boss's resistance reduction: " + "; ".join(
            f"{b} {t} -{v} ({src.get(f'{b}/{t}', 'game guide')})" for b, types in rr.items() for t, v in types.items()))
    return Table("E1", "Key values", ["Group", "Value", "Now", "Sheet / combat value", "Target", "Basis", "Status"],
                 rows, notes)


def _frames_cell(t) -> str:
    if t is None or t.actions_per_s <= 0:
        return ""
    fr = f"{t.frames} frames" if t.frames is not None else f"~{t.time_s * 30:.0f} frames (default)"
    return f"{fr}, {t.actions_per_s:.2f}/s" + (" + travel" if t.speed_type == "movement" else "")


def e2(a) -> Table:
    from ..combat import frames as FR
    fm = FR.FrameModel(a.ctx, a.sources)
    rows = []
    timings = []
    max_health = a.snap.combat.abilities["health"]
    capped = False
    for x in a.actions[:5]:
        t = {tr.target.split(" (")[0]: tr for tr in x.targets}
        cells = [x.label, f"{x.rank}", f"{_n(x.wd_pct)}%" if x.wd_pct else "-", _n(x.direct), _n(x.dot)]
        for name in ("Dummy", "Loghorrean", "Callagadra"):
            tr = t.get(name)
            if tr is None:
                cells.append("-")
            elif tr.expected_dot > 0:          # a hit and a DoT per second are different units - shown apart
                cells.append(f"{_n(tr.expected_direct)} + {_n(tr.expected_dot)}/s (PtH {tr.pth:.0f})")
            else:
                cells.append(f"{_n(tr.expected_direct)} (PtH {tr.pth:.0f})")
        ls = {h.target.split(" (")[0]: 0.0 for h in x.heals if h.target}
        for h in x.heals:
            if h.target:
                ls[h.target.split(" (")[0]] += h.raw_heal
        if any(v > max_health for v in ls.values()):
            capped = True           # B6: one action never heals more than your max Health
        cells.append(" / ".join(_n(min(ls.get(n, 0.0), max_health)) for n in ("Loghorrean", "Callagadra"))
                     if x.heals else "-")
        cells.append(x.confidence)
        note = []
        if x.double_hit:
            note.append("both weapons")
        if x.spender and x.onslaught_max:
            note.append(f"Onslaught spender (max {x.onslaught_max})")
        tm = fm.timing(x, a.snap.combat.speeds) if fm.available and not a.ctx.options.action_rate else None
        if tm is not None and _frames_cell(tm):
            note.append(_frames_cell(tm))
            timings.append((x, tm))
        cells.append(", ".join(note) or "-")
        rows.append(cells)
    notes = []
    if timings:
        bp = next(((x, t) for x, t in timings if not x.cooldown and t.breakpoint_measured), None)
        srcs = sorted({t.source for _x, t in timings})
        played = ("keyframes of the .anm file - 1, per Crate" if all(getattr(t, "playback", "n-1") == "n-1"
                                                                      for _x, t in timings)
                  else "keyframes of the .anm file as they are (ruleset 'n')")
        notes.append(f"Action time from animation frames: 30 frames = 1 s; played frames ({played}) / 1.25 "
                     "(player) / skill slot multiplier / speed incl. weapon base speed, whole frames rounded up "
                     "(ruleset R4; movement skills: + travel time, not modelled); "
                     "keyframes " + ", ".join({"assets": "from the game files (EXACT)",
                                               "community": "published by the community (TESTED)",
                                               "default": "default 19 (ESTIMATE)",
                                               "mixed": "partly default (ESTIMATE)"}[s_] for s_ in srcs)
                     + (f". Next frame for {bp[0].label}: {math.ceil(bp[1].next_breakpoint_pct - 1e-6)}% "
                        f"{'attack' if bp[1].speed_type == 'movement' else bp[1].speed_type} "
                        f"speed{'' if bp[1].breakpoint_reachable else ' (above your cap)'}" if bp else "") + ".")
    if a.onslaught:
        sp = a.onslaught[0]
        notes.append(f"{sp.label} by Onslaught stacks: " + " / ".join(
            f"{o.onslaught}: {_n(o.direct)}" for o in a.onslaught)
            + " per hit (+15% per stack, own multiplier after all other bonuses - Crate)")
    shred = (f"DA shred on the target: {_n(a.da_shred['total'])} ({'; '.join(a.da_shred['parts']) or 'none'}). "
             "Per hit = no crit, no target; vs target = expected hit + DoT per second, incl. resistances "
             "after your RR, PtH/crit vs the target DA, physical hits minus target armor. Per second values: "
             "E7 (action rate from animation frames).")
    # the benchmark targets (Training Dummy, Mogara) have their defense in D14; here only the bosses of the Expert View
    dn = [f"{t.name.split(' (')[0]}: {t.defense_note}" for t in a.targets_states
          if t.defense_note and t.role not in ("dummy", "bench_boss")]
    notes.append(("Boss defense - " + " | ".join(dn) + ". " if dn else "") + shred)      # .72: one note (Expert View budget)
    notes.append("Life steal per action = from the same damage events: gear/buff life steal x "
                 "min(1, WD/100) + skill life steal, direct damage only, after the boss Life Leech "
                 "Resistance (Callagadra " + next((f"{t.llr * 100:.0f}%" for t in a.targets_states
                                                   if t.name.startswith("Callagadra")), "-") + ", TESTED by the community)"
                 + (f"; capped at max Health ({_n(max_health)})" if capped else "") + ". Confidence = weakest rule "
                 "of the action's damage chain (conversion, Onslaught, both weapons, finale, armor piercing, expected hit "
                 "vs the target; X-009).")
    return Table("E2", "Damage per action", ["Skill", "Rank", "WD", "Per hit", "DoT/s", "vs Dummy",
                                             "vs Loghorrean", "vs Callagadra", "Life steal Loghorrean / Callagadra",
                                             "Confidence",
                                             "Notes"], rows, notes)


def _fig(v, digits=2) -> str:
    import math
    if v is None:
        return "-"
    if isinstance(v, float) and math.isinf(v):
        return "stable"
    return f"{v:.{digits}f}"


def _counted(parts) -> list[str]:
    """Identical entries once with their count, first-seen order: two equal Entropy procs (two items with
    the same proc) read '2x Entropy 0.29/s x 7,524', not the same text twice (review .67)."""
    counts: dict = {}
    for p in parts:
        counts[p] = counts.get(p, 0) + 1
    return [p if n == 1 else f"{n}x {p}" for p, n in counts.items()]


def _e7_sources(a) -> list:
    """Where the E7 numbers come from (combat spec V2 20 'Explainability'). .56: the STEADY rotation
    of the combat check itself (no second calculation that could drift)."""
    out = []
    prov = a.snap.combat.provenance.get("offensiveLifeLeechMin", [])
    if prov:
        parts = ", ".join(f"{(a.sources.source(s).label if a.sources.source(s) else s)} {v:g}" for s, v in prov)
        out.append(f"Life steal sources (STEADY): {parts} = {sum(v for _, v in prov):g}% x min(1, WD/100), "
                   "direct damage only")
    timing = []
    for c in a.combat_checks:
        tgt = next((t for t in a.targets_states if t.name == c.target), None)
        rot = getattr(c.states.get("STEADY"), "rotation", None)
        if rot is None:
            continue
        spent = next((x.onslaught for x in a.actions if x.label == rot.spender), 0) if rot.spender else 0
        once = "" if _refresh_text(rot) else f" once per {rot.cycle_s:.1f} s"   # said with the refresh
        line = (f"{c.boss.split(',')[0]}: rotation {rot.main}" + (f" + {rot.spender}{once} "
                f"with {spent} Onslaught stacks" if rot.spender else "")
                + f" at {rot.actions_per_s:.2f} actions/s" + _refresh_text(rot) + "; Life Leech Resistance "
                + f"{c.llr * 100:.0f}% = {tgt.llr_note if tgt else '-'}")
        shown = [x for x in rot.proc_detail if round(x[2]) or round(x[4])]     # .67: never 'x 0 = 0 DpS'
        if shown:
            line += ("; procs' own damage (STEADY) " + "; ".join(_counted(
                f"{n.split(' (')[0]} {r:.2f}/s x {d:,.0f}" + (f" + {t:,.0f} DoT" if round(t) else "")
                + (f", life steal {h:,.0f}" if h else "")
                for n, r, d, h, t in shown)) + f" = {rot.proc_dps:,.0f} DpS"
                + (f" + {rot.proc_dot_per_s:,.0f} DoT/s" if round(rot.proc_dot_per_s) else "")
                + f", {rot.proc_life_steal_per_s:,.0f} life steal/s (ESTIMATE)")
        out.append(line)
        timing += [n for n in rot.assumptions[:2] if ("frames" in n or "action rate" in n) and n not in timing]
    if timing:
        out.append("Action time (STEADY): " + "; ".join(timing))
    return out


TRIGGER_WORD = {"AttackEnemyCrit": "crits", "AttackEnemy": "attacks"}


def _refresh_text(rot) -> str:
    """The spender's cooldown refresh of the STEADY rotation (combat.dps.refresh_terms): '; Rip and Tear once
    per 3.6 s: cooldown 4.0 s, Recklessness refreshes 0.8 s on 20% of crits (ESTIMATE)'."""
    refresh = getattr(rot, "refresh", ()) or ()
    if not refresh or not rot.spender:
        return ""
    cd0, _cd, terms = refresh
    parts = ", ".join(f"{label} refreshes {amount:g} s on {chance * 100:g}% of {TRIGGER_WORD.get(trig, trig)}"
                      for label, chance, amount, trig, _share in terms)
    return f"; {rot.spender} once per {rot.cycle_s:.1f} s: cooldown {cd0:.1f} s, {parts} (ESTIMATE)"


def _per_boss_notes(checks) -> list:
    """Assumptions and gaps of the boss checks: a line all bosses share once, a boss-specific line with
    the boss name (review .54: E7 showed only the first boss's lines - Callagadra's frame rate, enraged
    recount and gaps were missing)."""
    def name(c):
        return c.boss.split(",")[0]
    out = []
    for i in range(max(len(c.assumptions) for c in checks)):
        lines = [(name(c), c.assumptions[i]) for c in checks if i < len(c.assumptions)]
        if len({line for _n, line in lines}) == 1 and len(lines) == len(checks):
            out.append(lines[0][1])                          # the same for every boss: once
        else:                                                # one paragraph, each boss named
            out.append(" | ".join(f"{n} - {line}" for n, line in lines))
    if all(tuple(c.gaps) == tuple(checks[0].gaps) for c in checks):
        out.append("Not modelled: " + "; ".join(checks[0].gaps))
    else:
        out.append("Not modelled - " + " - ".join(f"{name(c)}: " + "; ".join(c.gaps) for c in checks))
    return out


E7_COLUMNS = ("dps", "dot", "life_steal", "regen_heals", "sustain", "incoming", "pct_current", "spike",
              "spike_margin", "sustain_ratio", "ttd", "risk")     # stable ids of the E7 cells (.56)
TOP_TERMS = 8


def _conf(rules, *extra) -> str:
    from ..mechanics.policy import RULES, Confidence, weakest
    levels = [RULES[r][0] for r in rules if r in RULES] + [Confidence(x) for x in extra if x]
    return weakest(*levels).value


def _cell(value, unit, rules, terms=(), inputs=(), refs=(), extra_conf=(), note="") -> dict:
    """Provenance of one E7 cell (X-002): value, rule chain (mechanics.policy.RULES names), weakest
    confidence, additive terms that sum to the value (largest TOP_TERMS + 'rest'), multiplicative
    inputs and references to other cells of the row."""
    terms = sorted(terms, key=lambda t: (-abs(t["value"]), t["name"], t.get("origin", "")))
    if len(terms) > TOP_TERMS + 1:
        rest = terms[TOP_TERMS:]
        terms = terms[:TOP_TERMS] + [{"name": f"rest ({len(rest)} more)", "value": round(sum(t["value"] for t in rest), 6)}]
    if isinstance(value, (int, float)):
        value = round(float(value), 6)
    out = {"value": value, "unit": unit, "rules": list(rules),
           "confidence": _conf(rules, *extra_conf)}
    if terms:
        out["terms"] = terms
    if inputs:
        out["inputs"] = list(inputs)
    if refs:
        out["refs"] = list(refs)
    if note:
        out["note"] = note
    return out


def _e7_cells(c, name, s) -> dict:
    """{column id: provenance} of one E7 row - from the parts the combat check summed (no recalculation)."""
    rot = s.rotation
    rot_inputs = []
    if rot is not None:
        t = rot.main_timing
        rot_inputs = [{"name": "main action", "value": rot.main}, {"name": "spender", "value": rot.spender},
                      {"name": "actions per second", "value": round(rot.actions_per_s, 6)},
                      {"name": "action time", "value": (f"{t.frames} frames ({t.source})" if t is not None
                                                        and getattr(t, "frames", None) else "estimate/override")}]
        if rot.proc_detail:
            rot_inputs.append({"name": "procs' own damage per second", "value": round(rot.proc_dps, 6)})
    rot_conf = (getattr(rot, "confidence", "") or "",)
    hits = [{"name": "hits", "value": round(s.dps, 6)}, {"name": "damage over time", "value": round(s.dot_per_s, 6)}]
    parts = s.attack_parts
    frames_used = any(p.rate.startswith("basic attack from animation frames") for p in parts)
    use_rules = ["boss_use_rate"] + (["boss_basic_attack_frames"] if frames_used else [])
    def term(p, per_use):
        t = {"name": p.name, "kind": p.kind, "uses_per_s": round(p.uses_per_s, 6), "rate": p.rate,
             "per_use": round(per_use, 6), "value": round(per_use * p.uses_per_s, 6)}
        if p.origin:
            t["origin"] = p.origin
        return t
    inc_terms = [term(p, p.flat_expected) for p in parts if p.flat_expected * p.uses_per_s > 0]
    pct_terms = [term(p, p.pct_expected) for p in parts if p.pct_expected * p.uses_per_s > 0]
    # .67: spike = largest flat hit; the largest % current health hit is an input apart (never kills alone)
    top = max((p for p in parts if p.flat_hit > 0), key=lambda p: (p.flat_hit, p.name), default=None)
    spike_terms = [{"name": f"{top.name}: landed hit", "value": round(top.flat_hit, 6)}] if top is not None else []
    pct_inputs = ([{"name": f"{s.pct_spike_skill}: share of current health (not in the spike)",
                    "value": round(s.pct_spike, 6)}] if s.pct_spike else [])
    leech = [{"name": n, "value": v, "unit": "% of direct damage x min(1, WD/100)"} for n, v in s.leech_inputs]
    out = {
        "dps": _cell(s.dps_total, "/s", ["rotation", "dps"], hits, rot_inputs, extra_conf=rot_conf),
        "dot": _cell(s.dot_per_s, "/s", ["rotation", "dps"], refs=["dps"], extra_conf=rot_conf),
        "life_steal": _cell(s.life_steal, "HP/s", ["life_steal", "life_steal_wd_scale", "target_llr"],
                            inputs=leech + [{"name": "boss Life Leech Resistance", "value": round(c.llr, 6)}],
                            refs=["dps"], extra_conf=rot_conf),
        "regen_heals": _cell(s.regen + s.heals, "HP/s", ["health_regen", "heal_amount"],
                             [{"name": n, "value": round(v, 6)} for n, v in s.sustain_terms if n != "life steal"]),
        "sustain": _cell(s.total_sustain, "HP/s", ["life_steal", "health_regen", "heal_amount"],
                         [{"name": n, "value": round(v, 6)} for n, v in s.sustain_terms], extra_conf=rot_conf),
        "incoming": _cell(s.incoming_dps, "/s", ["boss_incoming_sum", "boss_hit_landed"] + use_rules, inc_terms),
        "pct_current": _cell(s.pct_current_per_s, "share of current health/s",
                             ["boss_incoming_sum", "pct_current_health"] + use_rules, pct_terms),
        "spike": _cell(s.spike, "hit", ["boss_incoming_sum", "boss_hit_landed", "pct_current_health"], spike_terms,
                       pct_inputs, note="largest single landed flat hit, no crit - a lower bound; % current "
                                        "health hits apart"),
        "spike_margin": _cell(s.spike_margin, "ratio", ["spike_margin"],
                              inputs=[{"name": "health", "value": round(s.health, 6)}], refs=["spike"]),
        "sustain_ratio": _cell(s.sustain_ratio, "ratio", ["sustain_ratio"], refs=["sustain", "incoming"]),
        "ttd": _cell(None if s.ttd == float("inf") else s.ttd, "s", ["ttd_steady"],
                     inputs=[{"name": "health", "value": round(s.health, 6)}], refs=["sustain", "incoming"],
                     note="stable" if s.ttd == float("inf") else ""),
    }
    if name in ("STEADY", "NO_SPAWNS"):
        risk = c.risk if name == "STEADY" else c.risk_no_spawns
        out["risk"] = _cell(risk, "class", ["combat_risk"], inputs=list(c.reasons) if name == "STEADY" else [],
                            refs=["spike_margin", "sustain_ratio"])
    return out


def e7(a) -> Table:
    rows = []
    cells = []
    for c in a.combat_checks:
        boss = c.boss.split(",")[0]
        for name, s in c.states.items():
            cells.append({"boss": c.boss, "state": name, "data": c.data, "cells": _e7_cells(c, name, s)})
            ttd = "stable" if s.ttd == float("inf") else ("< 1 s" if s.ttd < 1 else f"{s.ttd:.0f} s")
            rows.append([f"{boss} (lvl {c.level})", name, _n(s.dps_total), _n(s.dot_per_s), _n(s.life_steal),
                         _n(s.regen + s.heals),
                         _n(s.total_sustain), _n(s.incoming_dps), f"{s.pct_current_per_s * 100:.1f}%",
                         f"{_n(s.spike)} ({s.spike_skill})" + (f"; {s.pct_spike * 100:.0f}% current Health "
                                                                  f"({s.pct_spike_skill})" if s.pct_spike else ""),
                         _fig(s.spike_margin),
                         _fig(s.sustain_ratio), ttd,
                         (c.risk if name == "STEADY" else (getattr(c, "risk_no_spawns", "") if name == "NO_SPAWNS"
                                                            else "")) or "-"])      # .67: never an empty cell
    notes = []
    risk_lines = [f"{c.boss.split(',')[0]}: {c.risk} combat risk (HEURISTIC) - "
                  + ("; ".join(c.reasons) if c.reasons else "no modelled weakness") for c in a.combat_checks]
    if risk_lines:                                   # one paragraph (.61: expert view <= 150 lines, Cpt Slovi)
        notes.append(" | ".join(risk_lines))
    notes += _e7_sources(a)
    if a.combat_checks:
        notes += _per_boss_notes(a.combat_checks)
    if a.plausibility:
        notes.append("Plausibility: " + a.plausibility)
    legend = ("DpS = hits + damage over time per second; 'of which DoT/s' is the DoT part (it ignores "
              "the boss's resistances to hits - Callagadra resists 85% of every hit type but not bleeding). "
              "% current Health/s = share of your CURRENT health taken per second by % current health "
              "attacks - it shrinks with your health and never kills alone. Sources, rules and confidence "
              "of every cell: JSON output (--json), table E7 'cells'. ")
    ratio_note = legend + ("Sustain ratio > 1.2 reserve, 1.0-1.2 fragile, < 1.0 negative trend; spike margin > 1.5 "
                  "comfortable, < 1.0 the modelled hit is lethal. TTD = steady estimate, never a fight forecast.")
    if any("NO_SPAWNS" in c.states for c in a.combat_checks):
        ratio_note += (" NO_SPAWNS = STEADY with the boss's spawned monsters killed or avoided (their attacks, auras "
                       "and % current Health damage left out) - the lower end of the range; STEADY counts them "
                       "always alive, in reach and attacking (upper bound). The risk rating uses STEADY.")
    notes.append(ratio_note)
    return Table("E7", "Boss combat check (modelled)", ["Boss", "State", "DpS", "of which DoT/s", "Life steal/s",
                                                        "Regen + heals/s", "Sustain/s", "Incoming/s",
                                                        "% current Health/s", "Biggest hit", "Spike margin",
                                                        "Sustain ratio", "TTD", "Risk"], rows, notes, cells)


def _target_name(a, tid: str) -> str:
    """Target label instead of the internal id (.62, product review: 'leech_mid', 'res_ultimate' in E3)."""
    return next((r.label for r in a.results if r.id == tid), tid)


def _cut(text: str, n: int) -> str:
    """Cut at a word boundary with '...' (.62: E4/D1 were cut mid-word at 140/120 characters)."""
    t = str(text or "")
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(" ,;:-(") + " ..."


def e3(a) -> Table:
    """One row per target. A target from 'Do this next' shows exactly the variant the
    Player View recommends (slot conflicts already resolved), marked with its number,
    so every player action can be traced to its expert row."""
    from ..analysis.proposals.select import best_per_target
    picked = {(p.detail.get("target") or p.id): (i, p) for i, p in enumerate(a.player_actions, 1)}
    rows, seen = [], set()
    ordered = [p for _, p in sorted(picked.values(), key=lambda x: x[0])]
    ordered += [p for p in best_per_target(a.proposals) if (p.detail.get("target") or p.id) not in picked]
    for p in ordered:
        if p.change in seen or len(seen) >= 8:
            continue
        seen.add(p.change)
        nr = picked.get(p.detail.get("target") or p.id)
        mark = str(nr[0]) if nr and nr[1] is p else "-"
        rows.append([mark, p.category, p.change, _gain_text(p, a, n=4, player=False) or "-",
                     _cost_text(p),
                     f"{p.priority} / {p.effort}", ", ".join(_target_name(a, t) for t in p.fixes) or "-"])
    return Table("E3", "Now -> new (each proposal recalculated on its own)",
                 ["Action", "Category", "Change", "Gain", "Cost", "Priority / effort", "Targets fixed"], rows,
                 ["Shown only when worth it on the whole character: gains - costs - lost damage of your main "
                  "damage types + a bonus per point of a closed target >= 2, or a closed must-target (.64); in "
                  "ENDGAME the first 30 resistance points above the cap count as a buffer against enemy "
                  "resistance reduction.",
                  "Each line is a full recalculation of the character with that one change "
                  "(same targets); combinations can interact.",
                  "Action = number in 'Do this next'. There a slot is used only once, so a target can get "
                  "its second-best variant (e.g. Ring 1 instead of the amulet)."] + _search_notes(a))


def _search_notes(a) -> list[str]:
    """Never hide an incomplete search (AP10.8 review): gear budget used up, candidate generator error."""
    out = []
    if a.ctx.extra.get("gear_budget_reached"):
        out.append(f"Gear search stopped after {a.ctx.extra['gear_budget_reached']} recalculations - targets "
                   "further down the list got no gear proposal.")
    if a.ctx.extra.get("generator_error"):
        out.append(f"Attribute/skill/devotion candidates incomplete: {a.ctx.extra['generator_error']}")
    return out


def e4(a) -> Table:
    rows = [[r.cls, r.subject, r.effect, _cut("; ".join(r.sources), 160)] for r in a.audit]
    return Table("E4", "Stacking audit", ["Class", "Sources", "Effect", "From"], rows)


def e5(a) -> Table:
    """X-012: damage types, then RR / CC / reflect / purge of the boss from the game database in one column
    ('-' when none - one column keeps the expert view within 150 lines), then the gaps and the consumable."""
    rows = [[b.name, b.risk, ", ".join(b.damage_types) or "-",          # B2: every damage type
             b.hazards() or "-",
             "; ".join(b.reasons[:3]) or "-", b.consumable or "-"] for b in a.bosses[:10]]
    notes = ["Risk = preparation check against the boss profile from the game database "
             "(damage types, leech, crowd control, DA shred). No win/lose forecast. Risks: RR = resistance "
             "reduction of its debuffs (counted once each), reflect = damage it sends back while the skill is up, "
             "purge = removes your buffs; skill levels at your level via the boss's charLevel equation (DERIVED; "
             "area cap ASSUMED). Only one ointment works "
             "at a time."]
    if not rows:
        notes.append("Boss preparation starts at MID (Ultimate story bosses).")
    return Table("E5", "Boss preparation", ["Boss", "Risk", "Damage", "Risks (RR, CC, reflect, purge)",
                                            "What is missing", "Consumable"], rows, notes)


def generator_limits(a) -> list[str]:
    """E6 lines for candidate generators that failed (analysis/generators turns a crash into
    {"error": ...} so the report still comes out - but it must never look complete, review .68)."""
    out = []
    extra = getattr(a.ctx, "extra", None) or {}
    err = extra.get("generator_error")
    if err:
        out.append(f"candidate generator failed: {_cut(str(err), 120)} - proposals may be incomplete")
    for section, why in sorted((extra.get("generator_failures") or {}).items()):
        out.append(f"candidate generator failed: {section} ({_cut(str(why), 100)}) - proposals may be incomplete")
    return out


def e6(a, versions) -> Table:
    sc = a.score
    rows = [["Score", f"{sc['score']}/100 ({sc['stage']}): {sc['ok']} ok, {sc['tight']} tight, {sc['gap']} gap"],
            ["Score parts", _score_parts(a)],
            *[r for r in (BT.benchmark_row(a), BT.skills_row(a)) if r],       # .72: only with a benchmark / a build rating
            ["Game data", f"{a.ctx.game_version} ({versions.get('index', '')})"],
            ["Rules", f"ruleset {a.ctx.ruleset_version}, targets {versions.get('targets', '')}, "
                      f"skill {versions.get('skill', '')}"
                      + "".join(f"; {n}" for n in getattr(a.ctx, "rule_notes", ()))],
            ["Boss data", versions.get("boss_data", "-")],
            *([["Reference data", "; ".join(versions["reference_notes"])]] if versions.get("reference_notes") else []),
            ["Difficulty", f"{a.ctx.difficulty.label} (resistance penalty from {a.ctx.penalty_source})"],
            ["Archetype", a.archetype + (" (hardcore)" if a.ctx.hardcore else "")]]
    for i in a.issues:
        if i.code.startswith("L"):
            rows.append(["Model limit", i.title])
    rows += [["Model limit", line] for line in generator_limits(a)]
    # .72: one row for the assumptions and the facts of the game database (a row costs a Markdown line, the Expert View 150)
    rows.append(["Assumptions", "identical item auras count once (ASSUMED); monster level of bosses = your "
                                "level via the boss's charLevel equation (DERIVED; area cap ASSUMED); crits also "
                                "scale DoT and boss Life Leech Resistance lowers life "
                                "steal (TESTED by the community, no developer quote); from the game database: "
                                "purge removes every buff, toggle and aura except notDispelable "
                                "skills (Werewolf/Wereraven form); Ghoulish Hunger at 45% Health; "
                                f"Onslaught +{100 * float(a.ctx.ruleset.get('onslaught_per_stack', 0.15)):g}% "
                                "per stack after all other bonuses ("
                                + ("not confirmed for this game version, ASSUMED" if "onslaught_per_stack" in
                                   getattr(a.ctx, "rule_overrides", ()) else "Crate") + ")"])
    return Table("E6", "Basis", ["Item", "Value"], rows)


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

def details(a) -> list[Table]:
    out = []
    out.append(Table("D1", "Build errors and notes", ["Severity", "Code", "Finding", "Fix"],
                     [[i.severity, i.code, i.title, _cut(i.fix, 160) or "-"] for i in a.issues]))
    rows = []
    for b in a.snap.buffs + a.snap.enemy_debuffs:
        u = b.get("uptime")
        rows.append([b["label"], "enemy debuff" if b in a.snap.enemy_debuffs else "buff",
                     "-" if u is None else f"{u * 100:.0f}%", "yes" if b.get("kept_up") else "no",
                     f"{_n(b.get('duration'))} s / " + (f"{_n(b.get('cooldown'))} s" if b.get('cooldown') else "no cooldown")])
    out.append(Table("D2", "Kept-up buffs and enemy debuffs", ["Skill", "Kind", "Uptime", "Counts in combat",
                                                              "Duration / cooldown"], rows,
                     ["Only buffs and enemy debuffs with duration / cooldown >= 95% (maintained, X-018) count "
                      "in full in the combat values; partial uptime is shown here only."]))
    rows = [[r["label"], f"{r['chance'] * 100:.0f}%", _n(r["cooldown"]) if r["cooldown"] else "-",
             f"{r['per_second']:.2f}/s", r["assumption"]] for r in a.rates.get("procs", [])]
    rows += [[f"{r['debuff']}: {field_label(r['field'])}", "-", "-", f"peak {_n(r['peak'])}, average {_n(r['average'])}",
              r["assumption"]] for r in a.rates.get("rr", [])]
    out.append(Table("D3", "Rates and uptime (ESTIMATE)", ["Effect", "Chance", "Cooldown", "Rate", "Assumption"],
                     rows))
    from ..analysis.proposals.select import by_category
    rows = []
    for cat, ps in by_category(a.proposals, 5).items():
        for p in ps:
            rows.append([cat, p.change, ", ".join(_signed(d) for d in p.gain[:5]) or "-",
                         _cost_text(p), p.source or "-",
                         "; ".join(p.explain)])
    out.append(Table("D4", "All proposals by category", ["Category", "Change", "Gain", "Cost", "Where",
                                                         "Explain"], rows))
    sheet = a.snap.sheet
    rows = []
    for key, label in (("characterLifeModifier", "Health %"), ("characterDefensiveAbility", "DA (flat)"),
                       ("defensivePhysical", "Physical Res"), ("characterAttackSpeedModifier", "Attack Speed %")):
        contrib = sorted(sheet.provenance.get(key, []), key=lambda x: -abs(x[1]))[:6]
        txt = "; ".join(f"{(a.sources.source(s).label if a.sources.source(s) else s)} {_n(v)}" for s, v in contrib)
        rows.append([label, _n(sheet.fields.get(key, 0.0)), txt or "-"])
    out.append(Table("D5", "Where the values come from (sheet)", ["Value", "Total", "Largest sources"], rows,
                     ["source -> rule (stacking policy) -> result; the six largest sources per value "
                      "(E4 names stacking and waste, the JSON carries every E7 number's sources)"]))
    ch = a.ctx.character
    st = ch.stats
    h = sheet.abilities["health"]
    gdr = float(st.get("greatest_damage_received") or 0)
    rows = [["Deaths", _n(st.get("death_count") or 0)],
            ["Greatest hit received", f"{_n(gdr)} = {100 * gdr / h:.0f}% of your Health" if h else _n(gdr)],
            ["Boss kills (Normal / Elite / Ultimate)", " / ".join(_n(x) for x in st.get("boss_kills"))
             if isinstance(st.get("boss_kills"), list) else _n(st.get("boss_kills") or 0)],
            ["Crucible best wave", _n(st.get("survival_greatest_wave") or 0)],
            ["Playtime", f"{(st.get('playtime_seconds') or 0) / 3600:.0f} h"]]
    out.append(Table("D6", "Combat record (from the savefile)", ["Item", "Value"], rows))
    out.append(AT.d11(a))
    out += BT.quality_tables(a)                      # D12, D13 (only with a build rating)
    out += BT.benchmark_tables(a)                    # D14 (only with a benchmark)
    out += generator_tables(a)
    return out


def generator_tables(a) -> list[Table]:
    """D7-D10 (only with --detail): devotion plans with build order, rerolls per affix,
    skill point values, attribute spread - candidates of analysis/generators."""
    raw = a.ctx.extra.get("generator_moves") if isinstance(a.ctx.extra, dict) else None
    if not raw:
        return []
    out = []
    dv = raw.get("devotions") or {}
    rows = []
    for key, label in (("plan", "A"), ("plan_b", "B")):
        plan = dv.get(key) or {}
        for step in plan.get("order") or []:
            sign, k, aff, pts = step[0], step[1], step[2], step[3]
            names = a.ctx.extra.get("constellation_names") or {}
            rows.append([label, "add" if sign == "+" else "remove", names.get(k, k),
                         ", ".join(f"{n} {v}" for n, v in sorted(aff.items())), str(pts)])
    out.append(Table("D7", "Devotion plans - build order from zero", ["Plan", "Step", "Constellation",
                                                                      "Affinity after the step", "Points"], rows,
                     ["Plan A keeps every constellation with a bound celestial power; plan B frees the "
                      "powers of tier-1 constellations. Values are recalculated in E3/D4."]))
    rows = []
    for r in raw.get("rerolls") or [] if isinstance(raw.get("rerolls"), list) else []:
        best = (r.get("candidates") or [{}])[0]
        rows.append([r.get("slot", ""), r.get("kind", ""), r.get("affix", ""), r.get("verdict", ""),
                     f"{(r.get('better_share') or 0) * 100:.0f}%", best.get("name", "-"),
                     "; ".join(r.get("wasted") or []) or "-"])
    out.append(Table("D8", "Rerolls (Inventor) per affix", ["Slot", "Affix type", "Affix", "Verdict",
                                                            "Better rolls", "Example", "Wasted now"], rows,
                     ["REROLL = at least 25% of the rolls are better, optional 10-25%, keep below."]))
    sk = raw.get("skills") or {}
    rows = [[m["from"], m["to"], str(m["points"])] for m in sk.get("merged") or []]
    out.append(Table("D9", "Skill point moves", ["From", "To", "Points"], rows,
                     ["Attacks and procs have no damage model in this value - they count with their own "
                      "flat damage only."]))
    at = raw.get("attributes") or {}
    if at.get("current"):
        rows = [["now", str(at["current"].get("physique")), str(at["current"].get("cunning")),
                 str(at["current"].get("spirit"))],
                ["best", str((at.get("best") or {}).get("physique")), str((at.get("best") or {}).get("cunning")),
                 str((at.get("best") or {}).get("spirit"))]]
        out.append(Table("D10", "Attribute points", ["Spread", "Physique", "Cunning", "Spirit"], rows,
                         ["worth it" if at.get("worth_it") else "the current spread is fine",
                          "Check Required Cunning/Spirit of worn items before a reset (Tonic of Reshaping)."]))
    return out


BAND_TEXT = {"EARLY": "EARLY scores 0-33 (levelling; the score grows with level and targets met)",
             "MID": "MID scores 34-66; 66 needs every target of the Ultimate story met with room to spare",
             "ENDGAME": "ENDGAME scores 67-100; 100 needs every target met with room to spare"}


def build(a, versions: dict, meta: dict | None = None) -> Report:
    meta = dict(meta or {})
    ch = a.ctx.character
    vline, warnings = version_line(a.ctx, meta)
    if a.ctx.profile.value == "ascendant":     # .62, product review: output identical to General without a word
        warnings.append("Ascendant: monster values use the Ascendant adjustment; the targets and the score are "
                        "the General Ultimate ones (no Ascendant targets yet).")
    profile = PROFILE_NAME.get(a.ctx.profile.value, a.ctx.profile.value)
    campaign = meta.get("campaign_long") or meta.get("campaign", "")
    diff = a.ctx.difficulty.label             # .62: the difficulty the check used (--difficulty), not the save's
    if diff != ch.difficulty_label:
        diff = f"{diff} (checked; last played {ch.difficulty_label})"
    header = {"name": ch.name, "level": ch.level, "class": class_line(ch), "difficulty": diff,
              "stage": a.stage.value, "hardcore": a.ctx.hardcore, "version": vline, "profile": profile,
              "campaign": campaign}
    readiness = {"score": a.score["score"], "label": f"Build Readiness {a.score['score']}/100 - {profile} ({a.stage.value})",
                 "parts": a.score["parts"], "band": BAND_TEXT[a.stage.value], "bands": RD.bands()}
    areas = [Area(n, STATUS_WORD[a.areas[n]["status"]], _int_text(AT.area_text(n, a.areas[n], a)), a.areas[n]["value"])
             for n in AREAS]
    issues = [PlayerIssue(_int_text(i.text), _int_text(i.why), i.kind, i.target) for i in player_issues(a)]
    actions = [PlayerAction(_int_text(x.what), _int_text(x.gain), _int_text(x.cost), [_int_text(t) for t in x.tags],
                            x.priority, x.effort) for x in player_actions(a)]
    bosses = [PlayerBoss(b.name, b.risk, _int_text(b.reason), _int_text(b.prepare)) for b in player_bosses(a)]
    return Report(
        meta={**meta, "skill": versions.get("skill"), "ruleset": a.ctx.ruleset_version,
              "game_version": a.ctx.game_version, "live_status": a.ctx.live_status.value},
        header=header, readiness=readiness, warnings=warnings, areas=areas,
        issues=issues, actions=actions, bosses=bosses, boss_legend=BOSS_LEGEND if bosses else "",
        expert=[e1(a), e2(a), e3(a), e4(a), e5(a), e6(a, versions)] + ([e7(a)] if a.combat_checks else []),
        detail=details(a))
