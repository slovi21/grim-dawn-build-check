"""Report.html v2 - the single-file page of the approved mockup (docs/design/report-mockup-v1.html), spec 3.10.

`render(rep)` asks `rep.view()` for the `ReportView` (built lazily, only here) and formats its four blocks: the overview (stage
track, gate tiles, steps, watch-outs), the area panels, the improvements and the explanation tables. It builds nothing and rates
nothing; every string it prints is escaped. The page has no external resource: the fonts are embedded (`render_assets`), the
script only toggles the order of the improvements and opens the explanation a link points into.

Contract with the sweep: `data-block` is `overview`, `areas`, `improve` or `explain`; a suggestion carries `data-proposal`,
`data-effort` and `data-status`. Element ids: `p-` panels, `a-` areas, `s-` suggestions, `n-` notes, `x-` explanation rows."""
from __future__ import annotations

import html

from .render_assets import CSS, FONT_FILES, SCRIPT, font_faces
from .view_improve import PERMANENT_PREFIX

__all__ = ["CSS", "FONT_FILES", "SCRIPT", "font_faces", "render"]

TONES = ("ok", "risk", "gap", "info")
_TONE_OF = {"high": "gap", "medium": "risk", "low": "ok"}
KEYS = ("fire", "cold", "lightning", "acid", "pierce", "bleeding", "vitality", "aether", "chaos")
SIZES = ("wide", "third")
RR_STATE = {"full": "permanent", "half": "not permanent, counts half", "none": "missing"}

SPRITE = ('<svg width="0" height="0" style="position:absolute" aria-hidden="true">'
          '<symbol id="i-lock" viewBox="0 0 16 16"><rect x="2.5" y="7" width="11" height="8" rx="1.5" fill="currentColor"/>'
          '<path d="M5 7V5a3 3 0 0 1 6 0v2" fill="none" stroke="currentColor" stroke-width="1.8"/></symbol>'
          '<symbol id="i-yes" viewBox="0 0 18 18"><circle cx="9" cy="9" r="8" fill="none" stroke="currentColor" stroke-width="1.6"/>'
          '<path d="M5.2 9.3l2.5 2.5 5-5.4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
          'stroke-linejoin="round"/></symbol>'
          '<symbol id="i-no" viewBox="0 0 18 18"><circle cx="9" cy="9" r="8" fill="none" stroke="currentColor" stroke-width="1.6"/>'
          '<path d="M6 6l6 6M12 6l-6 6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></symbol></svg>')


# ---------------------------------------------------------------------------
# small pieces
# ---------------------------------------------------------------------------

def e(t) -> str:
    return html.escape(str(t))


def _tone(status) -> str:
    """The colour class of a status word ("Risk", "5 high"): one of TONES, never the text itself."""
    word = str(status).strip().lower().split(" ")[0] if str(status).strip() else ""
    if word in TONES:
        return word
    if word in _TONE_OF:
        return _TONE_OF[word]
    tail = str(status).strip().lower().rsplit(" ", 1)[-1]
    return _TONE_OF.get(tail, "info")


def _st(status, tone: str | None = None) -> str:
    tone = tone if tone in TONES else _tone(status)
    return f'<span class="st {tone}">{e(status)}</span>'


def _pos(x) -> str:
    """A position or width on a bar, 0 to 100, as a CSS number."""
    try:
        v = min(max(float(x), 0.0), 100.0)
    except (TypeError, ValueError):
        v = 0.0
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _rank(rank) -> int:
    try:
        n = min(max(int(rank), 1), 4)
    except (TypeError, ValueError):
        n = 1
    return n


def _eff(rank, word) -> str:
    return (f'<span class="eff" data-e="{_rank(rank)}"><span class="pips"><i></i><i></i><i></i><i></i></span>'
            f'{e(word)}</span>')


def _lead(text, lead) -> str:
    """`text` with its start `lead` in bold (the lead is always the start of the whole text)."""
    text, lead = str(text), str(lead or "")
    if lead and text.startswith(lead):
        return f"<b>{e(lead)}</b>{e(text[len(lead):])}"
    return e(text)


def _bold(text, part) -> str:
    """`text` with the first `part` in bold."""
    text, part = str(text), str(part or "")
    if part and part in text:
        before, after = text.split(part, 1)
        return f"{e(before)}<b>{e(part)}</b>{e(after)}"
    return e(text)


def _linked(text, lead, link, ref) -> str:
    """`text` with its bold start and the words `link` as a link to `ref` (an id without '#')."""
    text, lead, link = str(text), str(lead or ""), str(link or "")
    head, rest = (f"<b>{e(lead)}</b>", text[len(lead):]) if lead and text.startswith(lead) else ("", text)
    if link and ref and link in rest:
        before, after = rest.split(link, 1)
        return f'{head}{e(before)}<a href="#{e(ref)}">{e(link)}</a>{e(after)}'
    return head + e(rest)


def _what(text) -> str:
    """The "what" of a suggestion. A "Permanent instead:" prefix gets its own span: the fastest-first view re-orders the cards by
    CSS, where the prefix would point at a card that is no longer above it, so it hides the span (the text itself is unchanged)."""
    text = str(text)
    if text.startswith(PERMANENT_PREFIX):
        return f'<span class="perm-pre">{e(PERMANENT_PREFIX)}</span>{e(text[len(PERMANENT_PREFIX):])}'
    return e(text)


def _a(text, ref, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return f'<a{c} href="#{e(ref)}">{e(text)}</a>' if ref else e(text)


def _id(i) -> str:
    return f' id="{e(i)}"' if i else ""


# ---------------------------------------------------------------------------
# block 1: overview
# ---------------------------------------------------------------------------

def _track(t) -> str:
    bands = list(t.bands)
    last = bands[-1][0] if bands else "Endgame"
    aria = (f"Build Readiness {t.score} of 100. " + ", ".join(f"{n} {lo} to {hi}" for n, lo, hi in bands) + "."
            + (f" {last} is locked." if t.locked else ""))
    cells = []
    for k, (name, _lo, _hi) in enumerate(bands):
        cls = "band" + (" cur" if name == t.stage else "") + (" end" if t.locked and k == len(bands) - 1 else "")
        cells.append(f'<div class="{cls}"><span>{e(name)}</span></div>')
    ghost = (f'<div class="ghost" style="--from:{_pos(t.score)}; --to:{_pos(t.ghost)}"></div>'
             if t.ghost is not None and t.ghost > t.score else "")
    door = ""
    if t.locked:
        title = f"{last} is locked: {t.open_gates} gate{'' if t.open_gates == 1 else 's'} open" if t.open_gates else f"{last} is locked"
        door = f'<div class="door" title="{e(title)}"><svg aria-hidden="true"><use href="#i-lock"/></svg></div>'
    scale = "".join(f'<span style="--v:{_pos(v)}">{e(v)}</span>' for v in [b[1] for b in bands] + [100])
    note = f"{e(t.ghost_note)} " if t.ghost_note else ""
    return (f'<div class="track" role="img" aria-label="{e(aria)}">{"".join(cells)}'
            f'<div class="fill" style="--v:{_pos(t.score)}"></div>{ghost}'
            f'<div class="marker" style="--v:{_pos(t.score)}">{e(t.score)}</div>{door}</div>'
            f'<div class="scale" aria-hidden="true">{scale}</div>'
            f'<p class="ghost-note">{note}<a href="#x-score">How the score works</a></p>')


def _tile(g) -> str:
    tone = _tone(g.status)
    nofix = str(g.fix_lead).startswith("No single fix")
    fix = _lead(g.fix, g.fix_lead)
    inner = (f'<span class="g-top"><span class="g-name">{e(g.label)}</span>{_st(g.status)}</span>'
             f'<span class="g-val">{e(g.now)} <small>of {e(g.target)}</small></span>'
             f'<span class="bar" role="img" aria-label="{e(g.label)} {e(g.now)} of {e(g.target)}">'
             f'<i style="--p:{_pos(g.fraction * 100)}; --c:var(--{tone})"></i></span>'
             f'<span class="g-fix">{fix}</span>')
    cls = "gate nofix" if nofix else "gate"
    if g.fix_ref:
        return f'<a class="{cls}" href="#{e(g.fix_ref)}">{inner}</a>'
    return f'<div class="{cls}">{inner}</div>'


def _step(s) -> str:
    what = f'<span class="what">{_a(s.what, s.ref)}</span>'
    cost = f' <span class="neg">{e(s.cost)}</span>' if s.cost else ""
    return f'<li>{what}{_eff(s.effort_rank, s.effort)}<span class="s-out">{e(s.result)}{cost}</span></li>'


def _watch(w) -> str:
    extra = e(w.extra) + (f' {_a(w.link, w.ref)}' if w.link and w.ref else "")
    return (f'<li>{_st(w.status)}<div><p>{_lead(w.head, w.lead)}</p>'
            + (f"<p>{extra}</p>" if extra.strip() else "") + "</div></li>")


def _overview(o) -> str:
    stale = (f'<p class="stale">{e(o.stale)} <a href="#x-data">What this means</a></p>' if o.stale else "")
    gates = ""
    if o.gate_head:
        gates += (f'<div class="gates-head"><h2>{e(o.gate_head)}</h2><a class="why" href="#x-gates">Why these gates</a></div>')
    if o.gates:
        gates += '<div class="gates">' + "".join(_tile(g) for g in o.gates) + "</div>"
    if o.gates_met:
        gates += f'<p class="gates-met">Already met: {e(", ".join(o.gates_met))}.</p>'
    steps = ('<ol class="steps">' + "".join(_step(s) for s in o.steps) + "</ol>" if o.steps
             else '<p class="calm">Nothing urgent.</p>')
    lever = ""
    if o.lever:
        link = f' <a href="#{e(o.lever_ref)}">See how</a>' if o.lever_ref else ""
        note = f" {e(o.lever_note)}" if o.lever_note else ""
        lever = f'<p class="lever">Biggest score lever: <b>{e(o.lever)}</b>.{note}{link}</p>'
    watch = ('<ul class="warn">' + "".join(_watch(w) for w in o.watch) + "</ul>" if o.watch
             else '<p class="calm">Nothing to watch out for.</p>')
    facts = "".join(f"<div><dt>{e(f.label)}</dt><dd>{e(f.value)}</dd></div>" for f in o.facts)
    return (f'<header class="hero" data-block="overview"><div class="wrap"><div class="who"><div><h1>{e(o.name)}</h1>'
            f'<p class="class"><b>{e(o.cls)}</b> {e(o.masteries)}</p></div><dl class="facts">{facts}</dl></div>{stale}'
            f'<p class="verdict">{e(o.verdict)}</p>{_track(o.track)}{gates}'
            f'<div class="now"><div><h2>Do next, fastest first</h2>{steps}{lever}</div>'
            f'<div><h2>Watch out</h2>{watch}</div></div></div></header>')


# ---------------------------------------------------------------------------
# block 2: areas
# ---------------------------------------------------------------------------

def _ticks(ticks) -> str:
    out = []
    for t in ticks:
        tone = _tone(t.tone) if t.tone else ""
        cls = " ".join(c for c in ("mid" if t.mid else "", f"tone-{tone}" if tone else "") if c)
        attr = f' class="{cls}"' if cls else ""
        out.append(f'<span{attr} style="--t:{_pos(t.at)}">{e(t.text)}</span>')
    return f'<div class="ticks">{"".join(out)}</div>' if out else ""


def _meter(m) -> str:
    colour = f"var(--{_tone(m.status)})"
    ghost = (f'<s style="--from:{_pos(m.fill)}; --to:{_pos(m.ghost_to)}"></s>'
             if m.ghost_to is not None and m.ghost_to > m.fill else "")
    marks = ((f'<u style="--t:{_pos(m.mid)}"></u>' if m.mid is not None else "")
             + (f'<u class="later" style="--t:{_pos(m.later)}"></u>' if m.later is not None else ""))
    if m.negative:
        fill = f'<span class="neg-bar" style="--from:{_pos(m.fill)}; --to:{_pos(m.mid if m.mid is not None else m.fill)}"></span>'
    else:
        fill = f'<i style="--p:{_pos(m.fill)}; --c:{colour}"></i>'
    return (f'<div class="meter"><span class="m-name">{e(m.name)}</span>'
            f'<span class="m-val{" gap" if m.negative else ""}">{e(m.value)} <small>/ {e(m.target)}</small></span>'
            f'<span class="m-bar" role="img" aria-label="{e(m.name)} {e(m.value)} of {e(m.target)}">{fill}{ghost}{marks}</span>'
            f'{_ticks(m.ticks)}</div>')


def _res_cell(c) -> str:
    colour = f"var(--{c.key})" if c.key in KEYS else "var(--dim)"
    return (f'<div class="res{" low" if c.low else ""}" role="listitem" style="--t:{colour}"><span class="r-name">{e(c.name)}</span>'
            f'<span class="r-val">{e(c.value)}</span> <span class="r-spare">{e(c.spare)}</span></div>')


def _rr_slot(s) -> str:
    state = s.state if s.state in RR_STATE else "none"
    return (f'<div class="rr-slot"><span class="rr-kind">{e(s.kind)} <b>{e(s.amount)}</b></span>'
            f'<span class="rr-cell {state}"><i></i></span><span class="rr-src">{e(s.source)}'
            f'<span class="sr"> ({RR_STATE[state]})</span></span></div>')


def _check(c) -> str:
    icon, cls, label = ("i-yes", "yes", "done") if c.ok else ("i-no", "no", "not done")
    sub = f"<small>{e(c.sub)}</small>" if c.sub else ""
    return (f'<li><svg class="{cls}" role="img" aria-label="{label}"><use href="#{icon}"/></svg>'
            f"<span>{e(c.text)}{sub}</span></li>")


def _boss_table(b) -> str:
    head = "".join(f'<th scope="col">{e(c)}</th>' for c in b.columns)
    rows = []
    for name, flags, titles in b.rows:
        dots = []
        for k, flag in enumerate(flags):
            title = titles[k] if k < len(titles) else ""
            if flag:
                dots.append(f'<td><span class="dot" title="{e(title)}"></span><span class="sr">{e(title)}</span></td>')
            else:
                dots.append('<td><span class="dot off"></span><span class="sr">no</span></td>')
        rows.append(f"<tr><td>{e(name)}</td>{''.join(dots)}</tr>")
    more = f'<p class="boss-more">{e(b.more)}</p>' if b.more else ""
    return (f'<table class="boss"><thead><tr><th scope="col">{e(b.head)}</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>{more}')


def _time(t) -> str:
    goals = "".join(f'<span class="t-goal{"" if g.mid else " later"}" style="--v:{_pos(g.at)}"></span>' for g in t.goals)
    return (f'<div><div class="time" role="img" aria-label="{e(t.label)}"><span class="t-axis"></span>'
            f'<span class="t-range" style="--a:{_pos(t.low)}; --b:{_pos(t.high)}"></span>{goals}'
            f'<span class="t-you" style="--v:{_pos(t.you)}"></span></div>{_ticks(t.ticks)}</div>')


def _chip(c) -> str:
    dot, style = ("<i></i>", f' style="--t:var(--{c.key})"') if c.key in KEYS else ("", "")
    value = f" <b>{e(c.value)}</b>" if c.value else ""
    return f'<span class="chip"{style}>{dot}{e(c.text)}{value}</span>'


def _foot(items) -> str:
    out = [f'<a href="#{e(ref)}">{e(text)}</a>' if ref else f'<span class="muted">{e(text)}</span>' for text, ref in items]
    return f'<div class="p-foot">{"".join(out)}</div>'


def _panel(p) -> str:
    tone = p.tone if p.tone in TONES else _tone(p.status)
    cls = "panel" + (f" {p.size}" if p.size in SIZES else "")
    key = f'<p class="p-key">{_bold(p.key, p.key_bold)}</p>' if p.key else ""
    note = f'<p class="p-key">{e(p.note)}</p>' if p.note else ""
    meters = f'<div class="meters">{"".join(_meter(m) for m in p.meters)}</div>' if p.meters else ""
    body = ""
    if p.cells:
        body += (f'<div class="res-split"><div class="res-board" role="list">{"".join(_res_cell(c) for c in p.cells)}</div>'
                 f'<div class="meters">{"".join(_meter(m) for m in p.meters)}{note}</div></div>')
        note = ""
    else:
        body += meters
    if p.time is not None:
        body += _time(p.time)
    if p.chips:
        body += f'<div class="chips">{"".join(_chip(c) for c in p.chips)}</div>'
    if p.rr:
        body += f'<div class="rr">{"".join(_rr_slot(s) for s in p.rr)}</div>'
    if p.checks:
        body += f'<ul class="checks">{"".join(_check(c) for c in p.checks)}</ul>'
    if p.bosses is not None:
        body += _boss_table(p.bosses)
    return (f'<article class="{cls}"{_id(p.id)} data-status="{e(p.status)}"><div class="p-head"><h4>{e(p.title)}</h4>'
            f'{_st(p.status, tone)}</div>{key}{body}{note}{_foot(p.foot)}</article>')


def _areas(groups) -> str:
    body = "".join(f'<div class="group"><h3>{e(g.title)}</h3><div class="panels">{"".join(_panel(p) for p in g.panels)}</div></div>'
                   for g in groups)
    return ('<section class="sec" id="areas" data-block="areas" aria-labelledby="areas-h"><div class="sec-head">'
            '<h2 id="areas-h">Areas</h2><p>Each number next to its target. Solid tick: the target for your stage. '
            f'Grey tick: the Endgame target.</p></div>{body}</section>')


# ---------------------------------------------------------------------------
# block 3: improvements
# ---------------------------------------------------------------------------

def _impact(s) -> str:
    lock = '<svg aria-hidden="true"><use href="#i-lock"/></svg>' if s.impact_key else ""
    return f'<span class="imp{" key" if s.impact_key else ""}">{lock}{e(s.impact)}</span>'


def _suggestion(s, status) -> str:
    try:
        order = int(s.order)
    except (TypeError, ValueError):
        order = 0
    lines = [f'<span class="area-tag">{e(s.area)}</span>', f'<p class="s-what">{_what(s.what)}</p>']
    if s.gain:
        lines.append(f'<p class="s-gain">{e(s.gain)}</p>')
    if s.cost:
        lines.append(f'<p class="s-cost{" hard" if s.cost_hard else ""}">{e(s.cost)}</p>')
    if s.where:
        lines.append(f'<p class="s-where">{e(s.where)}</p>')
    why = _a("Why", s.why_ref, "why") if s.why_ref else ""
    return (f'<article class="sug"{_id(s.id)} data-proposal="{e(s.proposal)}" data-effort="{e(s.effort)}" '
            f'data-status="{e(status)}" style="--o:{order}"><div>{_eff(s.effort_rank, s.effort)}</div>'
            f'<div class="s-body">{"".join(lines)}</div><div class="imp-col">{_impact(s)}</div>{why}</article>')


def _note(n) -> str:
    cls = "note pick" if n.pick else "note"
    return f'<p class="{cls}"{_id(n.id)}>{_linked(n.text, n.lead, n.link, n.ref)}</p>'


def _quiet(items) -> str:
    return ('<div class="quiet">' + "".join(f"<span>{_lead(n.text, n.lead)}</span>" for n in items) + "</div>") if items else ""


def _area(a) -> str:
    notes = sorted(a.notes, key=lambda n: 0 if n.ref else 1)               # a cross reference opens the section, the rest follows
    tag = f'<span class="lever-tag">{e(a.tag)}</span>' if a.tag else ""
    return (f'<div class="area"{_id(a.id)} data-status="{e(a.status)}"><div class="area-head"><h3>{e(a.name)}</h3>'
            f'{_st(a.status)}{tag}</div>' + "".join(_note(n) for n in notes if n.ref)
            + "".join(_suggestion(s, a.status) for s in a.suggestions) + "".join(_note(n) for n in notes if not n.ref)
            + _quiet(a.quiet) + "</div>")


def _improve(areas) -> str:
    return ('<section class="sec" id="improve" data-block="improve" aria-labelledby="improve-h"><div class="sec-head">'
            '<h2 id="improve-h">Improvements</h2><div class="view" role="group" aria-label="Order">'
            '<button type="button" aria-pressed="true" data-view="area">By area</button>'
            '<button type="button" aria-pressed="false" data-view="fast">Fastest first</button></div></div>'
            f'<div class="imps" id="imps">{"".join(_area(a) for a in areas)}</div></section>')


# ---------------------------------------------------------------------------
# block 4: explanations
# ---------------------------------------------------------------------------

def _x_row(t, k, r) -> str:
    cells = list(r.cells)
    cols = len(t.columns)
    first = _eff(k + 1, cells[0]) if t.id == "t-effort" and cells else e(cells[0]) if cells else ""
    tds = [f"<td>{first}</td>"] + [f"<td>{e(c)}</td>" for c in cells[1:]]
    if r.conf and len(cells) < cols:
        tds.append(f'<td><span class="conf">{e(r.conf)}</span></td>')
    tds += ["<td></td>"] * (cols - len(tds))
    return f"<tr{_id(r.id)}>{''.join(tds)}</tr>"


def _x_table(t) -> str:
    head = "".join(f"<th>{e(c)}</th>" for c in t.columns)
    rows = "".join(_x_row(t, k, r) for k, r in enumerate(t.rows))
    return (f'<div class="tbl"{_id(t.id)}><h3>{e(t.title)}</h3><div class="scroll"><table class="x"><thead><tr>{head}</tr></thead>'
            f"<tbody>{rows}</tbody></table></div></div>")


def _explain(tables) -> str:
    return ('<details class="explain" id="explain" data-block="explain"><summary><h2>How this is rated</h2>'
            '<span class="sum-note">Targets, terms and data basis. Every "Why" and "How rated" link opens this.</span></summary>'
            + "".join(_x_table(t) for t in tables) + "</details>")


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

def render(rep, mechanics_link: str = "../../ClaudeSkill/GameMechanics.html") -> str:
    v = rep.view()
    link = f' · <a href="{e(mechanics_link)}">Game mechanics page</a>' if mechanics_link else ""
    title = f"{v.overview.name} - Build check"
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{e(title)}</title><style>{font_faces()}\n{CSS}</style></head><body>{SPRITE}"
            f"{_overview(v.overview)}<main class=\"wrap\">{_areas(v.groups)}{_improve(v.improvements)}{_explain(v.explain)}</main>"
            f'<footer class="wrap">{e(v.footer)}{link}</footer><script>{SCRIPT}</script></body></html>')
