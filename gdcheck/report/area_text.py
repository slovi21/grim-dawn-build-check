"""Texts of the eight overview rows of the Player View and the detail table D11 (.70/.72, plan A.3 / A.5, K1).

Skills & Devotions, Health, Resistances, Survival, Sustain, Crowd Control, Damage, Resistance Reduction: at most
12 words per row, worst first, whole
numbers rounded down (B7, text_util). Only wording happens here - status, value and facts come from
analysis.readiness and analysis.area_facts (F-008). Decisions of 04.10.2026 that differ from the plan text:
E1 (a Life Leech Res gap is named first in the Resistances row), E4 (pet build), E5 (the earliest low-health
trigger up to 50 % Health is named), K6 (names are shortened before a part is dropped), K7 (EARLY texts)."""
from __future__ import annotations

import math

from ..analysis import area_facts as AF
from ..analysis import build_quality as BQ
from ..analysis import targets as TGT
from ..domain.enums import SourceKind
from .model import Table
from .text_util import num as _n
from .text_util import player_now as _player_now
from .text_util import worst_first as _worst_first

WORDS = 12
PET_TEXT = "pet build: damage comes from your pets"
RR_PET_TEXT = "pet build: pet damage is not on your sheet"
NO_SKILL = "no damaging skill on the hotbar"
NO_DAMAGE_TO_DUMMY = "no damage to the dummy"
seconds_text = TGT.seconds_text          # '~23 s' / '~2 min' - the one place that rounds a time (never reads as met)
_RANK = {"gap": 0, "tight": 1, "ok": 2, "info": 3}
_KINDS_RANK = {"GAP": 0, "RISK": 1, "OK": 2}          # the same scale for the status of a main type


def fit(parts: list[str], limit: int = WORDS, sep: str = "; ") -> str:
    """The leading parts that fit in `limit` words, joined with `sep` - the first part always stays, a later
    part is dropped whole, never cut in the middle."""
    out = ""
    for p in parts:
        cand = f"{out}{sep}{p}" if out else p
        if out and len(cand.split()) > limit:
            break
        out = cand
    return out


def _floor(x: float) -> int:
    return int(math.floor(x + 1e-9))


def _target(r) -> str:
    return r.target_text.split(" (")[0]


# -- Health / Survival / Crowd Control -----------------------------------------------------------------

def _health(area, a) -> str:
    """A.3: '{ist} Health - 15% above the recommended 20,000 for melee' (from 1% over), '... at the recommended ...'
    (met without a full percent to spare), '... - recommended 17,000 for melee' (a gap, unchanged)."""
    w = area["worst"]
    sheet = a.snap.sheet.abilities["health"]
    if w is None:
        return f"{_n(sheet)} Health"
    melee = " for melee" if a.archetype == "melee" else ""
    now = _player_now(w, a)
    if w.status != "ok":
        return f"{now} Health - recommended {_target(w)}{melee}"
    over = _floor((w.now / w.target - 1) * 100) if w.target else 0
    if over >= 1:
        return f"{now} Health - {over}% above the recommended {_target(w)}{melee}"
    return f"{now} Health - at the recommended {_target(w)}{melee}"


def _survival(area, a) -> str:
    w, st = area["worst"], area["status"]
    if st == "OK":
        phys = a.snap.combat.resists["Physical"]
        return f"{_n(a.snap.combat.abilities['da'])} Defensive Ability, Physical Res {_floor(min(phys['value'], phys['cap']))}%"
    base = f"{w.label} {_player_now(w, a)} - target {w.target_text}"
    for hint in community_hint(a, w, "Survival"):         # with its qualifier '(combat)' if it fits, else without
        if len(f"{base}; {hint}".split()) <= WORDS:
            return f"{base}; {hint}"
    return base


def community_hint(a, shown, area: str) -> list[str]:
    """A3: the largest relative gap among the area's community targets as 'recommended <target> (you <value>)'
    (the label with and without its qualifier) - only a hint in the row; the status and the top issues
    stay with rule 9.2 (N-005)."""
    cands = [r for r in a.results if r.area == area and r.basis != "fact" and r.status in ("gap", "tight")
             and r is not shown and isinstance(r.target, (int, float)) and r.target and (r.deficit or 0) > 0]
    if not cands:
        return []
    r = max(cands, key=lambda r: r.deficit / abs(r.target))
    tail = f"{r.target_text.split(' (')[0]} (you {_player_now(r, a)})"
    return [f"recommended {lab} {tail}" for lab in dict.fromkeys((r.label, r.label.split(" (")[0]))]


def _later(a) -> str:
    """K7: a stage without a target for the area."""
    stage = a.stage.value
    return f"{stage.lower()} stage: targets come later" if stage != "ENDGAME" else "no target for this build"


def _crowd(area, a) -> str:
    w, st = area["worst"], area["status"]
    if st == "INFO":
        return _later(a)
    if st == "OK":
        return "Freeze and Stun resisted"
    cc = w.label.replace(" Res", "")
    return f"{cc} {_player_now(w, a)} - you can be {'frozen' if cc == 'Freeze' else 'stunned' if cc == 'Stun' else 'slowed'}"


# -- Resistances -----------------------------------------------------------------------------------------

def _count_part(r, a, names: int | None = None) -> str:
    """The resistance-count target as text: 'all at your cap' or '4 of 9 below cap: Fire 54%, ...'. `names`
    limits the listed resistances (worst first); the default is three for up to three, else two."""
    if r.status == "ok":
        return "all at your cap" if a.stage.value == "ENDGAME" else "all at 80% on Ultimate"
    now = _player_now(r, a)
    n = now.split("below: ")[-1].count(",") + 1        # .62: count + lowest + '+k more' (was cut at 70)
    names = names or (3 if n <= 3 else 2)
    return f"{n} of 9 below cap: {_worst_first(now, names).split(': ', 1)[-1]}"


def _leech_forms(leech, a) -> list[str]:
    """The life leech resistance gap, long to short: with the recommendation, without it, 'Leech Res -25%'."""
    now = _player_now(leech, a)
    return [f"Life Leech Res {now} (recommended {_target(leech)})", f"Life Leech Res {now}", f"Leech Res {now}"]


def _fact_first(first_for, leech, a) -> str:
    """A gap in a fact comes first and never drops; the life leech resistance gap follows, as long as the
    12 words allow it: the long forms of the leech part first, then fewer resistance names (the worst one
    always stays), and at last the shortest leech part (review 04.10.2026, item 1)."""
    forms = _leech_forms(leech, a)
    for names in (None, 2, 1):
        first = first_for(names)
        for form in forms:
            if len(f"{first}; {form}".split()) <= WORDS:
                return f"{first}; {form}"
    return f"{first_for(1)}; {forms[-1]}"


def _resistances(area, a) -> str:
    w, st = area["worst"], area["status"]
    rs = area["results"]
    count = next((r for r in rs if r.id in ("res_ultimate", "res_own_cap")), None)
    leech = next((r for r in rs if r.id.startswith("leech") and r.status in ("gap", "tight")), None)
    if st == "OK":
        return _count_part(count, a) if count else "all at your cap"
    # the part in front: the worst target, or - when that is the leech - the worst gap in a fact
    head = w if w is not leech else next((r for r in rs if r.basis == "fact" and r.status in ("gap", "tight")), None)

    def first_for(names):
        if head.id in ("res_ultimate", "res_own_cap"):
            return _count_part(head, a, names)
        return f"{head.label} {_player_now(head, a)} - needs {_target(head)}"
    if head is not None and leech is not None:
        return _fact_first(first_for, leech, a)
    if head is not None:
        return first_for(None)
    rest = ("others at cap" if a.stage.value == "ENDGAME" else "others at 80%") if (
        count is None or count.status == "ok") else _count_part(count, a)
    return fit([_leech_forms(leech, a)[0], rest])        # E1: only the leech is the gap - it comes first


# -- Sustain ---------------------------------------------------------------------------------------------

def sustain_text(s) -> str:
    """'life leech 41%, Untamed Rage at 50%, panic button Rallying Cry' - own life leech, the earliest
    low-health trigger, the strongest panic button. Too long (12 words): the trigger name becomes
    'low-health trigger at 50%', then the panic name 'a panic button', last the leech part 'leech 41%' (K6)."""
    leech = _floor(s.life_leech)

    def leech_part(short: bool) -> str:
        word = "leech" if short else "life leech"
        if leech < 1:
            return "no leech" if short else "no life leech"
        return f"{word} only {leech}%" if s.life_leech < AF.LIFE_LEECH_MIN else f"{word} {leech}%"

    def trigger(named: bool) -> str:
        if not s.breakers:
            return "no low-health trigger"
        name, thr, _kind = s.breakers[0]
        at = f"{_floor(thr)}%" if thr else "low health"
        if named:
            return f"{name} at {at}"
        return f"low-health trigger at {at}" if thr else "a low-health trigger"

    def panic(named: bool) -> str:
        if not s.panic:
            return "no panic button"
        return f"panic button {s.panic[0][0]}" if named else "a panic button"

    text = ""
    for named_t, named_p, short in ((True, True, False), (False, True, False), (False, False, False),
                                    (False, False, True)):
        text = ", ".join((leech_part(short), trigger(named_t), panic(named_p)))
        if len(text.split()) <= WORDS:
            break
    return text


def _sustain(area, a) -> str:
    s = area.get("sustain") or getattr(a.area_facts, "sustain", None)
    return sustain_text(s) if s is not None else "no sustain data"


# -- Damage ----------------------------------------------------------------------------------------------

_KIND_WORD = ("stacking", "reduced", "flat")
_PERCENT = 1                       # index of the 'reduced' kind: its number carries a %


def _rr_kind(m, i: int, full: bool) -> str:
    """One kind of resistance reduction: '-35 stacking' in force, '-27 stacking (not kept up)' when it exists but
    is not permanent (without the reason when `full` is off), 'no stacking' only when no source exists."""
    now, weak = m.rr[i], m.rr_weak[i]
    word = _KIND_WORD[i]
    if now < 1 and weak < 1:
        return f"no {word}"
    sign = "%" if i == _PERCENT else ""
    text = f"-{_floor(now if now >= 1 else weak)}{sign} {word}"
    return f"{text} ({m.rr_why[i]})" if now < 1 and full and m.rr_why[i] else text


def _rr_part(m, full: bool = True) -> str:
    return ", ".join(_rr_kind(m, i, full) for i in range(3))


def _rr_note(m) -> str:
    """The note that replaces the reasons of the weak kinds when the row is too long."""
    if not any(m.rr[i] < 1 and m.rr_weak[i] >= 1 for i in range(3)):
        return ""
    return "not all permanent" if any(v >= 1 for v in m.rr) else "none permanent"


def _rr_text(head: str, m, more: str) -> str:
    """'Chaos: -35 stacking, no reduced, no flat (+1 more)' in at most 12 words: the reasons of the weak kinds stay
    as long as they fit (before that '(+n more)' goes), then they collapse into one note ('none permanent', 'not
    all permanent'). The type name is the head, the row name says what it is about."""
    note = _rr_note(m)
    for full, with_more in ((True, True), (True, False), (False, True), (False, False)):
        parts = _rr_part(m, full) + ("" if full or not note else f", {note}")
        text = f"{head}: {parts}{more if with_more else ''}"
        if len(text.split()) <= WORDS:
            break
    return text


def rr_text(area, a) -> str:
    """Resistance Reduction row: the main type with the fewest kinds first (the larger one at a tie). A source that
    exists but is not permanent counts half a kind and is named with its reason ('-27 stacking (not kept up)');
    'no ...' stands only where no source exists."""
    facts = getattr(a, "area_facts", None)
    if getattr(facts, "pet", False):
        return RR_PET_TEXT
    if a.stage.value == "EARLY":
        return _later(a)
    main = sorted(area.get("main") or (), key=lambda m: (m.kinds(), -m.pct))
    if not main:
        return "no damage type above 900%"
    return _rr_text(main[0].name, main[0], f" (+{len(main) - 1} more)" if len(main) > 1 else "")


_NO_BENCHMARK_FIELD = object()          # a stand-in without the field (the analysis always has it)


def _dummy_part(a) -> str:
    """'dummy shield in ~23 s' - the time the damage model needs to bring the Training Dummy to its shield (N5).
    No benchmark in the run (below Ultimate: the monsters of the benchmark are Ultimate monsters) and a benchmark
    without a dummy (old boss data) say so (K8); a pet build has no part."""
    bench = getattr(a, "benchmark", _NO_BENCHMARK_FIELD)
    if bench is _NO_BENCHMARK_FIELD or getattr(bench, "pet", False):
        return ""
    if bench is None:                              # K8: the run has none (below Ultimate) - the row says so
        return "dummy time: Ultimate only"
    dummy = getattr(bench, "dummy", None)
    if dummy is None:
        return "dummy not measured"
    if dummy.seconds is None:                      # the build deals no damage to the dummy (Templater)
        return NO_DAMAGE_TO_DUMMY
    return f"dummy shield in {seconds_text(dummy.seconds, _dummy_target(a))}"


def _dummy_target(a) -> float | None:
    """The target of the dummy time for the rounding (a time never reads as met), None if there is none."""
    r = next((x for x in getattr(a, "results", ()) if x.id.startswith("dummy_ttk")), None)
    return getattr(r, "target", None)


def _damage_hints(area) -> tuple[str, str] | None:
    """(long, short) of the one hint: a conversion issue, else a hit chance on bosses that is not met."""
    if area.get("issue"):
        return "part of your damage bonus is wasted", "bonus partly wasted"
    w = area["worst"]
    if w is not None and getattr(w, "id", "pth_boss") == "pth_boss" and _RANK.get(getattr(w, "status", "info"), 3) < 2:
        return "misses bosses too often", "misses bosses often"
    return None


def damage_text(area, base: str, a) -> str:
    """'Chaos 2,052%, Aether 1,200%; dummy shield in ~23 s; bonus partly wasted' (at most 12 words): the main types
    (largest first), the dummy time, one hint. Too long: the hint gets short, then the types shrink to the first
    one '(+n more)' and the first one - the dummy part never drops. Without a main type the top hit stays and
    'no type above 900%' follows while it fits."""
    facts = getattr(a, "area_facts", None)
    if getattr(facts, "pet", False):
        return PET_TEXT
    early = a.stage.value == "EARLY"
    if early:
        return f"{base}; part of your damage bonus is wasted" if area.get("issue") else base
    dummy = _dummy_part(a)
    if base == NO_SKILL and dummy == NO_DAMAGE_TO_DUMMY:          # the same said twice
        dummy = ""
    hint = _damage_hints(area)
    main = sorted(area.get("main") or (), key=lambda m: -m.pct)
    if not main:
        head = [x for x in (base, dummy) if x]
        for h in hint or ():                                        # long, then short: the hint stays if it fits
            if len("; ".join([*head, h]).split()) <= WORDS:
                return "; ".join([*head, h])
        return fit([*head, "no type above 900%"] if facts is not None and not hint else head)
    names = [f"{m.name} {_floor(m.pct):,}%" for m in main]
    types = [", ".join(names)] + ([f"{names[0]} (+{len(names) - 1} more)"] if len(names) > 1 else []) + [names[0]]
    for h in (hint if hint else (None,)):
        for t in types:
            text = "; ".join(x for x in (t, dummy, h) if x)
            if len(text.split()) <= WORDS:
                return text
    return "; ".join(x for x in (types[-1], dummy, hint[1] if hint else None) if x)


def _damage(area, a) -> str:
    top = next((x for x in a.actions if "defaultweaponattack" not in x.action_id), None)
    base = f"{top.label} ~{top.direct:,.0f} per hit" if top else NO_SKILL
    return damage_text(area, base, a)


def _rr_row(area, a) -> str:
    return rr_text(area, a)


# -- Skills & Devotions ----------------------------------------------------------------------------------

UNSPENT_FROM = 5                   # free devotion points from which the row names them (decision 04.10.2026)


def _points(n: int) -> str:
    return f"{n} point" + ("" if n == 1 else "s")


def build_text(q) -> str:
    """The build check in skill points, never percent (K3). Decisions of 04.10.2026: the row counts the modelled waste
    only (a move out of a skill the scale cannot value is a model gap, no waste); a row in OK says 'fit' for both parts
    (its swap or move is below the threshold), 'attacks not rated' replaces 'skills fit' when a third of the points sit
    in attacks and spells (no value here), free devotion points are named from five. 'attacks not rated' also follows a
    move when the 12 words allow it. A move that comes only out of skills the scale cannot value (from 5 value points,
    I5) reads 'skills: 11 points not rateable' - the check can say neither "fit" nor "worth moving". Levelling characters
    and pet builds are not rated."""
    if q is None:
        return "skills and devotions not checked"
    if getattr(q, "pet", False):
        return "pet build: pet skills are not valued"
    if getattr(q, "early", False):
        return "levelling: skills and devotions not rated yet"
    if getattr(q, "error", None):
        return "skills and devotions could not be checked"
    n, dg, free = q.points_modelled(), q.devotion_gain, q.free_devotion_points
    unrated = q.attacks_unrated()
    worth = q.status() != "OK"
    move = worth and n > 0 and q.skill_gain - q.unvalued_gain() > 0
    swap = worth and dg > 0
    # I5: the whole move comes out of skills the scale cannot value - no waste, but "fit" would claim a verdict it cannot give
    blind = n == 0 and q.unvalued_gain() >= BQ.RISK_FROM
    skills = (f"skills: {_points(n)} worth moving" if move else
              f"skills: {_points(q.skill_points_moved)} not rateable" if blind else
              "skills: attacks not rated" if unrated else "skills fit")
    devotions = (f"devotions: {free} points unspent" if free >= UNSPENT_FROM
                 else "devotions: a swap pays" if swap else "devotions fit")
    if skills == "skills fit" and devotions == "devotions fit":
        return "skills and devotions fit your build"
    text = f"{skills}; {devotions}"
    return fit([text, "attacks not rated"]) if (move or blind) and unrated else text


_ROW = {"Skills & Devotions": lambda area, a: build_text(area.get("quality")),
        "Health": _health, "Resistances": _resistances, "Survival": _survival, "Sustain": _sustain,
        "Crowd Control": _crowd, "Damage": _damage, "Resistance Reduction": _rr_row}


def area_text(name: str, area: dict, a) -> str:
    """The text of one overview row (at most 12 words)."""
    return _ROW[name](area, a)


# -- D11 ---------------------------------------------------------------------------------------------------

def _targets(area) -> str:
    return "; ".join(f"{r.label} {r.now_text} vs {r.target_text} ({r.status})" for r in area["results"])


def _skill_bound_leech(a) -> bool:
    prov = a.snap.combat.provenance.get("offensiveLifeLeechMin", [])
    kinds = {a.sources.source(sid).kind for sid, _v in prov if a.sources.source(sid)}
    return bool(kinds & {SourceKind.SKILL, SourceKind.ITEM_MODIFIER})


def _sustain_inputs(s, a) -> str:
    leech = ", ".join(f"{label} {_floor(v)}%" for label, v in s.leech_sources) or "none"
    out = [f"own life leech {_floor(s.life_leech)}% (needs {_floor(AF.LIFE_LEECH_MIN)}%): {leech}"
           + ("; skill-bound leech counted (it works while that skill is used)" if _skill_bound_leech(a) else "")]
    trig = ", ".join(f"{n} ({'at ' + str(_floor(t)) + '%' if t else 'threshold not in the data'}, {k})"
                     for n, t, k in s.breakers) or "none up to 50% Health"
    out.append(f"low-health triggers: {trig}")
    pan = ", ".join(f"{n} ({'heals' if k == 'heal' else 'absorbs'} {_floor(p)}% of max Health, cooldown {_floor(cd)} s)"
                    for n, k, p, cd in s.panic) or "none (heal or absorb of 20% of max Health, cooldown up to 120 s)"
    out.append(f"panic buttons: {pan}")
    return "; ".join(out) + f"; {s.count()} of 3 features"


def _damage_inputs(area, a) -> str:
    facts = getattr(a, "area_facts", None)
    if getattr(facts, "pet", False):
        return "pet build: the damage of your pets is not on the character sheet"
    out = []
    for m in area.get("main") or ():
        out.append(f"{m.name} {_floor(m.pct):,}% (own {_floor(m.own):,}, all damage {_floor(m.everything):,}, "
                   f"elemental {_floor(m.elemental):,})")
    if not out:
        out.append("no damage type above 900% on the character sheet")
    bench = getattr(a, "benchmark", None)
    if bench is not None and getattr(bench, "dummy", None) is not None:
        secs = bench.dummy.seconds
        out.append(("Training Dummy: no damage to it" if secs is None else f"Training Dummy shield in {seconds_text(secs)}")
                   + " (damage model, Level of the dummy = yours + 2)")
    if area["results"]:
        out.append(_targets(area))
    return "; ".join(out)


def _rr_inputs(area, a) -> str:
    facts = getattr(a, "area_facts", None)
    if getattr(facts, "pet", False):
        return "pet build: the damage of your pets is not on the character sheet"
    out = []
    for m in area.get("main") or ():
        src = "; ".join(m.rr_sources) or "no source"
        out.append(f"{m.name}: {_rr_part(m)} [{src}]")
    if not out:
        out.append("no damage type above 900% on the character sheet")
    if area["results"]:
        out.append(_targets(area))
    return "; ".join(out)


def _build_inputs(area) -> str:
    q = area.get("quality")
    if q is None:
        return "not checked in this run (the build check runs with the overview)"
    if getattr(q, "pet", False):
        return "pet build: the skills and devotions of your pets are not valued"
    if getattr(q, "early", False):
        return "levelling (stage EARLY): the points are still being placed, not rated"
    if getattr(q, "error", None):
        return f"the check failed: {q.error}"
    gap = _floor(q.unvalued_gain())
    return (f"skill points worth moving {q.skill_points_moved} (gain {_floor(q.skill_gain)} value points"
            + (f", {gap} of them out of skills with no value on the scale: a model gap, not counted" if gap else "")
            + f"); devotion swap gain {_floor(q.devotion_gain)} value points; {q.free_devotion_points} devotion points free; "
            f"wasted {_floor(q.wasted_modelled())} value points (OK below {BUILD_RISK_FROM}, RISK below {BUILD_GAP_FROM}, "
            f"GAP from there); attacks and spells are not rated: {_floor(100 * q.attack_share())}% of the skill points")


BUILD_RISK_FROM, BUILD_GAP_FROM = 5, 15      # the thresholds of analysis.build_quality (value scale, HEURISTIC)


def d11(a) -> Table:
    """D11 'Where you stand - inputs': what each row of the overview is made of (plan A.5)."""
    facts = getattr(a, "area_facts", None)
    rows = []
    for name, ar in a.areas.items():
        if name == "Sustain":
            s = ar.get("sustain")
            inputs = _sustain_inputs(s, a) if s is not None else "no sustain data"
            if ar["status"] == "INFO":
                inputs += " (EARLY: no target yet)"
        elif name == "Damage":
            inputs = _damage_inputs(ar, a)
        elif name == "Resistance Reduction":
            inputs = _rr_inputs(ar, a)
        elif name == "Skills & Devotions":
            inputs = _build_inputs(ar)
        else:
            inputs = _targets(ar) or f"no target at the {a.stage.value} stage"
            if name == "Resistances" and getattr(facts, "resist_share", None) is not None:
                inputs += f"; resistances reached {_floor(100 * facts.resist_share)}% of their targets (value)"
        rows.append([name, ar["status"], "-" if ar["value"] is None else f"{ar['value']}/100", inputs])
    notes = ["Value = how far the targets are met (actual / target, capped at 100%), rounded down; status = the worst "
             "single value. Health: the bar shows the surplus and is full at 1.5 times the recommended Health. "
             "Resistances: the nine resistances weigh 3, the life leech resistance 1. Sustain: one point each for own life leech of 5% or more, a low-health trigger up to 50% "
             "Health, a panic button (heal or absorb of 20% of max Health or more, cooldown up to 120 s); 2 of 3 is OK. "
             "Damage: the Training Dummy shield time (the damage model needs this long to bring the dummy to its shield "
             "at 15% life; 30 s good at ENDGAME, 60 s at MID) and the hit chance on bosses; main types are above 900% on the "
             "character sheet (without the attribute share). Resistance Reduction: 2 of the 3 "
             "kinds of enemy resistance reduction on the weakest main type is OK (a permanent kind counts 1, one that is not "
             "permanent 1/2; 1/2 or more is a RISK, none a GAP); the target weighs stacking 0.5, reduced 0.3, flat 0.2. "
             "Skills & Devotions: skill points and constellations by value (1 value point is about +1% of main damage or "
             "Health); OK below 5 value points that could be earned elsewhere, RISK below 15, GAP from 15. Points that would come "
             "out of skills the scale cannot value (reflected or retaliation damage, a debuff for other damage types) are a model "
             "gap and do not count; attacks and spells are not rated; levelling characters and pet builds are not rated. "
             "These thresholds are HEURISTIC (decision of the user "
             "04.10.2026), not game rules; the database fields behind them are EXACT.",
             "Resistance reduction in force is the one of the damage model (permanent effects, kept-up enemy debuffs); "
             "a source that exists but is not permanent (a debuff that is not kept up, a chance-based proc, an "
             "effect of one skill) counts half a kind and is marked with its reason. Celestial Powers bound to a skill count as "
             "low-health triggers when their controller names a threshold (not when the skill is a toggle that is "
             "switched off), never as a panic button."]
    return Table("D11", "Where you stand - inputs", ["Area", "Status", "Bar", "Inputs"], rows, notes)


__all__ = ["area_text", "build_text", "community_hint", "d11", "damage_text", "fit", "rr_text", "seconds_text",
           "sustain_text"]
