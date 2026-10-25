"""Report.html - modern single-file layout (AP8.7, N-012, X-016).

Player View as cards (works at 390 px without horizontal scrolling), Expert and
Detail blocks collapsible, search box over all tables, light/dark theme from
the system, status always as word + colour (WCAG AA), no external resources."""
from __future__ import annotations

import html

from . import bars as BR

CSS = """
:root{--bg:#f6f7f9;--card:#ffffff;--ink:#17202b;--muted:#5a6573;--line:#c3cad3;--accent:#2f5d8a;
--ok:#1f7a45;--ok-bg:#e6f4ec;--risk:#8a5a00;--risk-bg:#fdf1db;--gap:#a1261d;--gap-bg:#fbe7e5;
--info:#475569;--info-bg:#eef1f5;--warn-bg:#fff4d6;--warn-line:#e0a400;--shadow:0 1px 2px rgba(16,24,40,.06),0 1px 3px rgba(16,24,40,.08)}
@media (prefers-color-scheme:dark){:root{--bg:#0f141a;--card:#171e26;--ink:#e6ebf0;--muted:#9aa7b4;
--line:#3a4655;--accent:#7fb0e0;--ok:#6fd39a;--ok-bg:#16301f;--risk:#f3c164;--risk-bg:#33270f;--gap:#ff8f84;
--gap-bg:#3a1714;--info:#aab6c3;--info-bg:#1f2833;--warn-bg:#33290f;--warn-line:#e0a400;--shadow:none}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,
"Helvetica Neue",Arial,sans-serif}
main{max-width:1120px;margin:0 auto;padding:16px}
h1{font-size:1.45rem;margin:.2rem 0 .1rem;letter-spacing:-.01em}
h2{font-size:1.05rem;margin:1.6rem 0 .6rem;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.sub{color:var(--muted);font-size:.92rem}
.top{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;justify-content:space-between}
.score{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 16px;box-shadow:var(--shadow);
min-width:220px}
.score b{font-size:2rem;display:block;line-height:1.1}
.score span{color:var(--muted);font-size:.85rem}
.warn{background:var(--warn-bg);border-left:4px solid var(--warn-line);padding:10px 14px;border-radius:8px;margin:12px 0}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}@media (max-width:900px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media (max-width:440px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;box-shadow:var(--shadow)}
.card h3{margin:0 0 6px;font-size:.95rem;display:flex;justify-content:space-between;gap:8px;align-items:center}
.card p{margin:0;color:var(--muted);font-size:.92rem}
.chip{font-size:.75rem;font-weight:700;padding:2px 8px;border-radius:999px;letter-spacing:.04em;white-space:nowrap}
.OK,.LOW{color:var(--ok);background:var(--ok-bg)}.RISK,.MEDIUM{color:var(--risk);background:var(--risk-bg)}
.GAP,.HIGH{color:var(--gap);background:var(--gap-bg)}.INFO{color:var(--info);background:var(--info-bg)}
ol.list{margin:0;padding-left:1.3rem}ol.list li{margin:.35rem 0}
.act{display:grid;gap:10px}
.act .card h3{font-size:1rem}
.kv{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:.9rem;margin-top:4px}
.kv .g{color:var(--ok)}.kv .c{color:var(--gap)}
.tag{display:inline-block;font-size:.75rem;color:var(--muted);border:1px solid var(--line);border-radius:6px;padding:0 6px;margin:4px 4px 0 0}
.boss{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
details{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:10px 0;box-shadow:var(--shadow)}
summary{cursor:pointer;padding:12px 14px;font-weight:600;list-style:none;display:flex;justify-content:space-between}
summary::after{content:"+";color:var(--muted)}details[open] summary::after{content:"-"}
summary:focus-visible,input:focus-visible,button:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.tbl{overflow-x:auto;padding:0 14px 12px}
table{border-collapse:collapse;width:100%;font-size:.86rem}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;white-space:nowrap}
td.num{white-space:nowrap}
.note{color:var(--muted);font-size:.82rem;margin:6px 0 0}
.tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
input[type=search]{flex:1;min-width:200px;padding:8px 10px;border-radius:8px;border:1px solid var(--line);
background:var(--card);color:var(--ink);font:inherit}
button{padding:8px 12px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);font:inherit;cursor:pointer}
footer{color:var(--muted);font-size:.8rem;margin:24px 0}
a{color:var(--accent)}
.hide{display:none}
.score small{display:block;color:var(--muted);font-size:.78rem;max-width:260px;margin-top:4px}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.tools input{flex:1 1 100%}
.rbar{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin:12px 0 4px}
.rbar .seg{min-width:0}
.rbar .track,.bar{background:var(--line);overflow:hidden}
.rbar .track{height:14px;border-radius:7px}
.rbar .fill{display:block;height:100%;background:var(--ok)}
.rbar .lab{display:block;margin-top:3px;font-size:.78rem;color:var(--muted);overflow-wrap:anywhere}
.rbar .here .lab{color:var(--ink);font-weight:700}
.rbar .here .track{outline:2px solid var(--accent);outline-offset:1px}
.bar{height:8px;border-radius:4px;margin:6px 0 8px}
.bar .fill{display:block;height:100%}
.bar.s-OK .fill{background:var(--ok)}.bar.s-RISK .fill{background:var(--risk)}.bar.s-GAP .fill{background:var(--gap)}
@media print{details{break-inside:avoid}details:not([open]){display:none}.tools{display:none}}
"""

SCRIPT = """
const q=document.getElementById('q');
function filter(){const t=q.value.trim().toLowerCase();
 document.querySelectorAll('details.blk').forEach(d=>{let any=false;
  d.querySelectorAll('tbody tr').forEach(r=>{const hit=!t||r.textContent.toLowerCase().includes(t);
   r.classList.toggle('hide',!hit);any=any||hit;});
  if(t){d.open=any;d.classList.toggle('hide',!any&&!d.querySelector('summary').textContent.toLowerCase().includes(t));}
  else{d.classList.remove('hide');}});
 const n=document.querySelectorAll('details.blk tbody tr:not(.hide)').length;
 document.getElementById('qres').textContent=t?(n?n+' rows match':'No matches'):'';}
q.addEventListener('input',filter);
document.getElementById('openall').onclick=()=>document.querySelectorAll('details.blk').forEach(d=>d.open=true);
document.getElementById('closeall').onclick=()=>document.querySelectorAll('details.blk').forEach(d=>d.open=false);
"""


def e(t) -> str:
    return html.escape(str(t))


CHIP = {"ok": "OK", "tight": "RISK", "gap": "GAP", "info": "INFO", "HIGH": "HIGH", "MEDIUM": "MEDIUM",
        "LOW": "LOW"}


def _cell(c) -> str:
    c = str(c)
    if c in CHIP:
        return f"<td><span class='chip {CHIP[c]}'>{e(CHIP[c])}</span></td>"
    return f"<td>{e(c)}</td>"


def _table(t, open_=False) -> str:
    head = "".join(f"<th scope='col'>{e(c)}</th>" for c in t.columns)
    body = "".join("<tr>" + "".join(_cell(c) for c in r) + "</tr>" for r in t.rows) or \
        f"<tr><td colspan='{len(t.columns)}'>-</td></tr>"
    notes = "".join(f"<p class='note'>{e(n)}</p>" for n in t.notes)
    return (f"<details class='blk' id='{e(t.key)}'{' open' if open_ else ''}><summary>{e(t.key)} {e(t.title)}"
            f"</summary><div class='tbl' tabindex='0' role='region' aria-label='{e(t.title)}'>"
            f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
            f"{notes}</div></details>")


def _readiness_bar(rep) -> str:
    """One bar, three bands with their ranges, green up to the score, the current band marked."""
    score = rep.readiness["score"]
    segs = []
    for name, low, high in rep.readiness["bands"]:
        here = low <= score <= high
        cls, cur = ("seg here", " aria-current='true'") if here else ("seg", "")
        segs.append(f"<div class='{cls}'{cur}><div class='track' aria-hidden='true'><span class='fill' "
                    f"style='width:{BR.pct(BR.fill(score, low, high))}%'></span></div>"
                    f"<span class='lab'>{e(name)} {low}-{high}{' (now)' if here else ''}</span></div>")
    # no role='meter' here: the children of a meter are presentational, the band names must stay readable
    return (f"<section class='rbar' role='group' aria-label='Build Readiness {e(score)} of 100'>"
            + "".join(segs) + "</section>")


def _area_bar(a) -> str:
    v = BR.html_value(a.value, a.status)
    if v is None:                                       # INFO: no bar, the status stays a word
        return ""
    return (f"<div class='bar s-{e(a.status)}' role='meter' aria-valuemin='0' aria-valuemax='100' "
            f"aria-valuenow='{v}' aria-label='{e(a.name)} {v} of 100'><span class='fill' "
            f"style='width:{BR.pct(v / 100)}%'></span></div>")


def render(rep, mechanics_link: str = "../../ClaudeSkill/GameMechanics.html") -> str:
    h = rep.header
    parts = [f"<header class='top'><div><h1>{e(h['name'])}</h1>"
             f"<div class='sub'>Level {e(h['level'])} {e(h['class'])} · {e(h['difficulty'])} · {e(h['stage'])}"
             + (" · Hardcore" if h.get("hardcore") else "") + "</div>"
             f"<div class='sub'>{e(h['version'])} · Profile: {e(h['profile'])}"
             + (f" · Ultimate campaign: {e(h['campaign'])}" if h.get("campaign") else "") + "</div></div>"
             f"<div class='score'><span>Build Readiness</span><b>{e(rep.readiness['score'])}/100</b>"
             f"<span>{e(h['profile'])} ({e(h['stage'])})</span>"
             + (f"<small>{e(rep.readiness['band'])}</small>" if rep.readiness.get('band') else "")
             + "</div></header>"]
    if rep.readiness.get("bands"):
        parts.append(_readiness_bar(rep))
    for w in rep.warnings:
        parts.append(f"<div class='warn' role='alert'><strong>Warning:</strong> {e(w)}</div>")
    parts.append("<h2>Where you stand</h2><section class='grid' aria-label='Areas'>")
    for a in rep.areas:
        parts.append(f"<article class='card'><h3>{e(a.name)}<span class='sr-only'>: </span>"
                     f"<span class='chip {e(a.status)}'>{e(a.status)}</span>"
                     f"</h3>{_area_bar(a)}<p>{e(a.text)}</p></article>")
    parts.append("</section><h2>Top issues</h2><section class='card'>")
    parts.append("<ol class='list'>" + "".join(                     # N-011: the reason opens on demand
        f"<li><details><summary>{e(i.text)}</summary><p>{e(i.why)}</p></details></li>" if getattr(i, "why", "")
        else f"<li>{e(i.text)}</li>" for i in rep.issues) + "</ol>"
                 if rep.issues else "<p>None.</p>")
    parts.append("</section><h2>Do this next</h2><section class='act'>")
    for i, x in enumerate(rep.actions, 1):
        tags = "".join(f"<span class='tag'>{e(t)}</span>" for t in x.tags)
        parts.append(f"<article class='card'><h3>{i}. {e(x.what)}</h3><div class='kv'><span class='g'>Gain: {e(x.gain)}</span>"
                     f"<span class='c'>Cost: {e(x.cost)}</span></div>{tags}</article>")
    if not rep.actions:
        parts.append("<article class='card'><p>Nothing urgent - see the Expert View for fine-tuning.</p></article>")
    parts.append("</section><h2>Boss preparation</h2><section class='act'>")
    for b in rep.bosses:
        parts.append(f"<article class='card boss'><div><strong>{e(b.name)}</strong><p>{e(b.reason)}"
                     + (f"<br>{e(b.prepare)}" if b.prepare else "") + f"</p></div><span class='chip {e(b.risk)}'>"
                     f"{e(b.risk)} RISK</span></article>")
    if getattr(rep, "boss_legend", ""):
        parts.append(f"<p class='note'>{e(rep.boss_legend)}</p>")
    if not rep.bosses:
        parts.append("<article class='card'><p>Boss preparation starts at MID (Ultimate story bosses).</p></article>")
    parts.append("</section><h2>Expert view</h2><div class='tools'><input id='q' type='search' "
                 "placeholder='Search tables' aria-label='Search expert and detail tables'>"
                 "<button id='openall' type='button'>Expand all</button><button id='closeall' type='button'>"
                 "Collapse all</button></div><p id='qres' class='note' aria-live='polite'></p>")
    for i, t in enumerate(rep.expert):
        parts.append(_table(t, open_=(i == 0)))
    parts.append("<h2>Details</h2>")
    for t in rep.detail:
        parts.append(_table(t))
    parts.append(f"<footer>Grim Dawn build check {e(rep.meta.get('skill', ''))} · ruleset {e(rep.meta.get('ruleset', ''))}"
                 + (f" · <a href='{e(mechanics_link)}'>Game mechanics page</a>" if mechanics_link else "")
                 + " · tables per hit; E7 expected "
                 "values per second (no fight simulation); bosses are preparation/risk, never win/lose.</footer>")
    title = f"{h['name']} - Build check"
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{e(title)}</title><style>{CSS}</style></head><body><main>"
            + "".join(parts) + f"</main><script>{SCRIPT}</script></body></html>")
