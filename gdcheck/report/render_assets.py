"""Assets of the HTML report v2: the style sheet and script of the approved mockup (docs/design/report-mockup-v1.html) and the
embedded fonts. No external resource: the fonts are `data:` URIs read from gdcheck/report/fonts (copies of public/site/fonts,
SIL Open Font License, see OFL.txt there).

Changes against the mockup: `.gates` fits as many tiles as the width allows (the engine has up to six gates, not four), the
"Pick one" note carries its words itself, a resistance reduction slot has the three states of `RRSlot.state`, and the script
guards `history.replaceState` (a sandboxed frame throws a SecurityError there, and every "Why" link would stay dead)."""
from __future__ import annotations

import base64
import functools
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent / "fonts"
# (family, weight, file) - the six faces the style sheet uses
FONT_FILES = (("IM Fell English", 400, "im-fell-english-400.woff2"), ("Barlow", 400, "barlow-400.woff2"),
              ("Barlow", 500, "barlow-500.woff2"), ("Barlow", 600, "barlow-600.woff2"),
              ("Barlow Semi Condensed", 500, "barlow-semi-condensed-500.woff2"),
              ("Barlow Semi Condensed", 600, "barlow-semi-condensed-600.woff2"))


@functools.cache
def font_faces() -> str:
    """The @font-face rules with the font files embedded as base64 data URIs. A file that is missing (a damaged install) is
    left out: the style sheet falls back to the system fonts of its stack, the report is still written."""
    rules = []
    for family, weight, name in FONT_FILES:
        try:
            data = base64.b64encode((FONT_DIR / name).read_bytes()).decode("ascii")
        except OSError:
            continue
        rules.append(f'@font-face {{ font-family: "{family}"; font-style: normal; font-weight: {weight}; font-display: swap; '
                     f'src: url("data:font/woff2;base64,{data}") format("woff2"); }}')
    return "\n".join(rules)


CSS = r"""
:root {
  --iron: #22262b;
  --plate: #2a2f35;
  --plate-2: #323840;
  --seam: #3e454e;
  --bone: #ece5d8;
  --ash: #aaa398;
  --dim: #817b72;
  --brass: #d3ab62;
  --brass-soft: #d3ab6233;
  --brass-dim: #8a7142;

  --ok: #6bb995;
  --risk: #e6ab3e;
  --gap: #ec6a52;
  --info: #94a0ae;

  --fire: #ef7b45; --cold: #6cb6f0; --lightning: #9aa8ff; --acid: #9cc94a; --pierce: #c8c0ae;
  --bleeding: #c9434f; --vitality: #a774d9; --aether: #4fd6b8; --chaos: #d6497e;

  --display: "IM Fell English", "Iowan Old Style", Georgia, serif;
  --text: "Barlow", "Segoe UI", system-ui, sans-serif;
  --narrow: "Barlow Semi Condensed", "Barlow", "Segoe UI", sans-serif;

  --r-panel: 6px;
  --gutter: clamp(16px, 4vw, 40px);
}

* { box-sizing: border-box; }
html { background: var(--iron); color-scheme: dark; }
body {
  margin: 0;
  background: var(--iron);
  color: var(--bone);
  font: 400 16px/1.5 var(--text);
  font-variant-numeric: tabular-nums;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--brass); text-decoration-color: #d3ab6266; text-underline-offset: 3px; }
a:hover { text-decoration-color: var(--brass); }
:focus-visible { outline: 2px solid var(--brass); outline-offset: 3px; border-radius: 3px; }
b, strong { font-weight: 600; }
.wrap { max-width: 1120px; margin: 0 auto; padding: 0 var(--gutter); }
.num { font-family: var(--narrow); font-weight: 600; }
.muted { color: var(--ash); }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

/* Status: always word + colour */
.st { display: inline-flex; align-items: center; gap: 6px; font: 600 13px/1 var(--narrow); padding: 4px 8px 4px 7px; border-radius: 999px; white-space: nowrap; }
.st::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.st.ok { color: var(--ok); background: #6bb9951f; }
.st.risk { color: var(--risk); background: #e6ab3e1f; }
.st.gap { color: var(--gap); background: #ec6a5224; }
.st.info { color: var(--info); background: #94a0ae1f; }

/* Effort: fewer pips = faster */
.eff { display: inline-flex; align-items: center; gap: 8px; font: 600 13px/1 var(--narrow); color: var(--bone); white-space: nowrap; }
.eff .pips { display: inline-flex; gap: 2px; }
.eff .pips i { width: 6px; height: 10px; border-radius: 1px; background: var(--seam); }
.eff[data-e="1"] .pips i:nth-child(-n+1),
.eff[data-e="2"] .pips i:nth-child(-n+2),
.eff[data-e="3"] .pips i:nth-child(-n+3),
.eff[data-e="4"] .pips i:nth-child(-n+4) { background: var(--brass); }

.imp { display: inline-flex; align-items: center; gap: 6px; font: 600 13px/1.2 var(--narrow); color: var(--ash); }
.imp.key { color: var(--brass); }
.imp svg { width: 13px; height: 13px; flex: none; }

.why { font: 500 13px/1 var(--narrow); white-space: nowrap; }

/* ---------- 1  Overview ---------- */
.hero { padding: clamp(28px, 5vw, 56px) 0 32px; border-bottom: 1px solid var(--seam); }
.who { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 12px 32px; overflow-wrap: anywhere; }
h1 { font: 400 clamp(44px, 7vw, 68px)/0.95 var(--display); margin: 0; letter-spacing: -0.01em; overflow-wrap: anywhere; }
.class { margin: 10px 0 0; font-size: 19px; color: var(--ash); }
.class b { color: var(--bone); font-weight: 500; }
.facts { display: flex; flex-wrap: wrap; gap: 4px 28px; margin: 0; }
.facts div { display: flex; flex-direction: column; }
.facts dt { font: 500 13px/1.2 var(--narrow); color: var(--dim); }
.facts dd { margin: 0; font: 600 17px/1.3 var(--narrow); }
.stale { margin: 14px 0 0; font-size: 14px; color: var(--risk); }

.verdict { margin: 40px 0 52px; font: 400 clamp(24px, 3.2vw, 31px)/1.25 var(--display); max-width: 30em; }

/* stage track - the one loud element */
.track { position: relative; display: grid; grid-template-columns: 34fr 33fr 33fr; height: 54px; border-radius: 4px; background: var(--plate); overflow: visible; }
.band { position: relative; display: flex; align-items: center; padding: 0 14px; font: 600 14px/1 var(--narrow); color: var(--dim); border-right: 1px solid var(--iron); }
.band.end { padding-left: 34px; border-right: 0; background: repeating-linear-gradient(135deg, transparent 0 7px, #ffffff08 7px 14px); }
.band.cur { color: var(--bone); }
.fill { position: absolute; inset: 0 auto 0 0; width: calc(var(--v) * 1%); background: linear-gradient(90deg, #d3ab6214, #d3ab6259); border-right: 2px solid var(--brass); border-radius: 4px 0 0 4px; pointer-events: none; transform-origin: left; animation: fill 900ms cubic-bezier(.2,.7,.2,1) both; }
.ghost { position: absolute; top: 0; bottom: 0; left: calc(var(--from) * 1%); width: calc((var(--to) - var(--from)) * 1%); border: 1px dashed #d3ab6299; border-left: 0; border-radius: 0 3px 3px 0; pointer-events: none; }
.marker { position: absolute; top: -34px; left: calc(var(--v) * 1%); transform: translateX(-50%); font: 600 22px/1 var(--narrow); color: var(--brass); }
.marker::after { content: ""; position: absolute; left: 50%; top: 26px; width: 0; height: 0; border: 5px solid transparent; border-top-color: var(--brass); transform: translateX(-50%); }
.door { position: absolute; top: 50%; left: 67%; transform: translate(-50%, -50%); width: 30px; height: 30px; border-radius: 50%; display: grid; place-items: center; background: var(--iron); border: 2px solid var(--gap); color: var(--gap); }
.door svg { width: 14px; height: 14px; }
.scale { position: relative; height: 22px; font: 500 12px/22px var(--narrow); color: var(--dim); }
.scale span { position: absolute; left: calc(var(--v) * 1%); transform: translateX(-50%); }
.scale span:first-child { transform: none; }
.scale span:last-child { transform: translateX(-100%); }
.ghost-note { margin: 2px 0 0; font-size: 14px; color: var(--ash); }
@keyframes fill { from { transform: scaleX(0); } to { transform: scaleX(1); } }

.gates-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px; margin: 30px 0 12px; }
.gates-head h2 { margin: 0; font: 600 17px/1.3 var(--text); }
.gates { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
.gate { display: grid; gap: 8px; padding: 14px 14px 12px; border-radius: var(--r-panel); background: var(--plate); border: 1px solid var(--seam); color: var(--bone); text-decoration: none; }
.gate:hover { border-color: var(--brass-dim); background: var(--plate-2); }
.gate .g-top { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.gate .g-name { font-weight: 600; }
.gate .g-val { font: 600 22px/1 var(--narrow); }
.gate .g-val small { font-size: 15px; color: var(--ash); font-weight: 500; }
.bar { position: relative; height: 6px; border-radius: 3px; background: var(--iron); }
.bar i { position: absolute; inset: 0 auto 0 0; width: calc(var(--p) * 1%); border-radius: 3px; background: var(--c, var(--brass)); }
.gate .g-fix { font-size: 14px; color: var(--ash); }
.gate .g-fix b { color: var(--brass); font-weight: 500; }
.gate.nofix .g-fix b { color: var(--ash); }

.now { display: grid; grid-template-columns: 1.25fr 1fr; gap: 20px 40px; padding: 36px 0 44px; }
.now h2 { margin: 0 0 12px; font: 600 17px/1.3 var(--text); }
.steps { list-style: none; margin: 0; padding: 0; counter-reset: step; }
.steps li { counter-increment: step; display: grid; grid-template-columns: 28px 1fr auto; gap: 2px 12px; padding: 12px 0; border-top: 1px solid var(--seam); }
.steps li::before { content: counter(step); grid-row: span 2; font: 600 20px/1.15 var(--narrow); color: var(--brass); }
.steps .what { font-weight: 600; }
.steps .what a { color: var(--bone); text-decoration: none; }
.steps .what a:hover { color: var(--brass); }
.steps .s-out { grid-column: 2; font-size: 15px; color: var(--ash); }
.steps .s-out .neg { color: var(--gap); }
.steps .eff { grid-row: 1; grid-column: 3; justify-self: end; }
.lever { margin: 14px 0 0; padding: 12px 14px; border-radius: var(--r-panel); background: var(--brass-soft); font-size: 15px; }
.lever b { color: var(--brass); }

.gates-met { margin: 12px 0 0; font-size: 14px; color: var(--ash); }
.calm { margin: 0; padding: 12px 0; border-top: 1px solid var(--seam); font-size: 15px; color: var(--ash); }

.warn { list-style: none; margin: 0; padding: 0; }
.warn li { display: grid; grid-template-columns: auto 1fr; gap: 12px; align-items: start; padding: 12px 0; border-top: 1px solid var(--seam); font-size: 15px; }
.warn li .st { margin-top: 1px; }
.warn li p { margin: 0; }
.warn li p + p { color: var(--ash); font-size: 14px; margin-top: 2px; }

/* ---------- section titles ---------- */
.sec { padding: 52px 0 8px; border-top: 1px solid var(--seam); }
.sec-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 10px 24px; margin-bottom: 22px; }
.sec-head h2 { margin: 0; font: 400 clamp(30px, 4vw, 39px)/1.1 var(--display); }
.sec-head p { margin: 0; color: var(--ash); font-size: 15px; max-width: 36em; }

/* ---------- 2  Areas ---------- */
.group { margin: 0 0 30px; }
.group > h3 { margin: 0 0 12px; font: 600 15px/1.3 var(--text); color: var(--ash); }
.panels { display: grid; grid-template-columns: repeat(6, 1fr); gap: 14px; }
.panel { grid-column: span 3; display: flex; flex-direction: column; gap: 14px; padding: 18px 18px 14px; border-radius: var(--r-panel); background: var(--plate); border: 1px solid var(--seam); scroll-margin-top: 24px; }
.panel.wide { grid-column: span 6; }
.panel.third { grid-column: span 2; }
.p-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.p-head h4 { margin: 0; font: 600 18px/1.2 var(--text); }
.p-key { margin: 0; font-size: 15px; color: var(--ash); }
.p-key b { color: var(--bone); }
.p-foot { margin-top: auto; display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px 16px; padding-top: 10px; border-top: 1px solid var(--seam); font: 500 13px/1.3 var(--narrow); }
.p-foot a { text-decoration: none; }
.p-foot a:hover { text-decoration: underline; }

/* meter row: label, bar with target ticks, value */
.meters { display: grid; gap: 14px; }
.meter { display: grid; grid-template-columns: 1fr auto; gap: 6px 12px; align-items: baseline; }
.meter .m-name { font-size: 15px; }
.meter .m-val { font: 600 17px/1 var(--narrow); text-align: right; }
.meter .m-val small { font-weight: 500; color: var(--ash); font-size: 14px; }
.meter .m-val.gap { color: var(--gap); }
.meter .m-bar { grid-column: 1 / -1; position: relative; height: 10px; border-radius: 5px; background: var(--iron); }
.meter .m-bar i { position: absolute; inset: 0 auto 0 0; width: calc(var(--p) * 1%); border-radius: 5px; background: var(--c); }
.meter .m-bar u { position: absolute; top: -4px; bottom: -4px; left: calc(var(--t) * 1%); width: 2px; background: var(--bone); text-decoration: none; }
.meter .m-bar u.later { background: var(--dim); }
.meter .m-bar s { position: absolute; top: 0; bottom: 0; left: calc(var(--from) * 1%); width: calc((var(--to) - var(--from)) * 1%); border: 1px dashed var(--ok); border-left: 0; border-radius: 0 5px 5px 0; text-decoration: none; }
.ticks { grid-column: 1 / -1; position: relative; height: 16px; font: 500 12px/16px var(--narrow); color: var(--dim); }
.ticks span { position: absolute; left: calc(var(--t) * 1%); transform: translateX(-50%); white-space: nowrap; }
.ticks span.mid { color: var(--bone); }
.ticks span.tone-ok { color: var(--ok); }
.ticks span.tone-risk { color: var(--risk); }
.ticks span.tone-gap { color: var(--gap); }
.ticks span.tone-info { color: var(--info); }

/* resistance board */
.res-split { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 18px 28px; align-items: start; }
.meter .m-bar .neg-bar { position: absolute; top: 0; bottom: 0; left: calc(var(--from) * 1%); width: calc((var(--to) - var(--from)) * 1%); background: var(--gap); border-radius: 5px 0 0 5px; }
.res-board { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.res { position: relative; padding: 9px 10px 8px 14px; border-radius: 4px; background: var(--plate-2); }
.res::before { content: ""; position: absolute; left: 0; top: 8px; bottom: 8px; width: 3px; border-radius: 2px; background: var(--t); }
.res .r-name { display: block; font: 500 13px/1.2 var(--narrow); color: var(--ash); }
.res .r-val { font: 600 20px/1.2 var(--narrow); }
.res .r-spare { font: 500 12px/1 var(--narrow); color: var(--dim); }
.res.low { background: #e6ab3e1c; box-shadow: inset 0 0 0 1px #e6ab3e66; }
.res.low .r-val { color: var(--risk); }
.res.low .r-spare { color: var(--risk); }

/* resistance reduction slots */
.rr { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.rr-slot { display: grid; gap: 4px; padding: 10px; border-radius: 4px; background: var(--plate-2); }
.rr-slot .rr-kind { display: flex; justify-content: space-between; align-items: baseline; font: 600 14px/1.2 var(--narrow); }
.rr-slot .rr-kind b { font-size: 20px; }
.rr-slot .rr-cell { height: 10px; border-radius: 3px; background: var(--iron); overflow: hidden; position: relative; }
.rr-slot .rr-cell i { position: absolute; inset: 0 auto 0 0; width: 50%; background: repeating-linear-gradient(135deg, #e6ab3e 0 4px, #e6ab3e55 4px 8px); }
.rr-slot .rr-cell.full i { width: 100%; background: var(--ok); }
.rr-slot .rr-cell.none i { width: 0; }
.rr-slot .rr-src { font-size: 13px; line-height: 1.3; color: var(--ash); }

/* damage time */
.time { position: relative; height: 34px; margin-top: 8px; }
.time .t-axis { position: absolute; left: 0; right: 0; top: 14px; height: 6px; border-radius: 3px; background: var(--iron); }
.time .t-range { position: absolute; top: 12px; height: 10px; left: calc(var(--a) * 1%); width: calc((var(--b) - var(--a)) * 1%); border-radius: 5px; background: #ec6a5266; }
.time .t-you { position: absolute; top: 6px; left: calc(var(--v) * 1%); width: 4px; height: 22px; margin-left: -2px; border-radius: 2px; background: var(--gap); }
.time .t-goal { position: absolute; top: 4px; left: calc(var(--v) * 1%); width: 2px; height: 26px; background: var(--bone); }
.time .t-goal.later { background: var(--dim); }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip { display: inline-flex; align-items: baseline; gap: 6px; padding: 5px 10px; border-radius: 4px; background: var(--plate-2); font-size: 14px; }
.chip b { font: 600 15px/1 var(--narrow); }
.chip i { width: 8px; height: 8px; border-radius: 50%; background: var(--t); align-self: center; }

/* checks */
.checks { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
.checks li { display: grid; grid-template-columns: 20px 1fr; gap: 10px; font-size: 15px; }
.checks svg { width: 18px; height: 18px; margin-top: 2px; }
.checks .yes { color: var(--ok); }
.checks .no { color: var(--gap); }
.checks small { display: block; color: var(--ash); font-size: 13px; }

/* boss matrix */
.boss { width: 100%; border-collapse: collapse; font-size: 14px; }
.boss th, .boss td { padding: 7px 8px; text-align: center; border-top: 1px solid var(--seam); }
.boss th { font: 500 13px/1.2 var(--narrow); color: var(--ash); border-top: 0; }
.boss th:first-child, .boss td:first-child { text-align: left; padding-left: 0; }
.boss td:first-child { font-weight: 500; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: var(--gap); }
.dot.off { background: transparent; box-shadow: inset 0 0 0 1px var(--seam); }
.boss-more { margin: 0; font-size: 14px; color: var(--ash); }

/* ---------- 3  Improvements ---------- */
.view { display: inline-flex; padding: 3px; border-radius: 6px; background: var(--plate); border: 1px solid var(--seam); }
.view button { font: 600 14px/1 var(--narrow); color: var(--ash); background: transparent; border: 0; border-radius: 4px; padding: 8px 12px; cursor: pointer; }
.view button[aria-pressed="true"] { background: var(--plate-2); color: var(--bone); }
.imps { display: flex; flex-direction: column; gap: 26px; }
.area { display: flex; flex-direction: column; }
.area-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; padding-bottom: 10px; border-bottom: 1px solid var(--seam); }
.area-head h3 { margin: 0; font: 600 19px/1.2 var(--text); }
.area-head .lever-tag { font: 500 14px/1 var(--narrow); color: var(--ash); }
.sug { display: grid; grid-template-columns: 130px 1fr 190px 52px; gap: 6px 18px; align-items: start; padding: 14px 0; border-bottom: 1px solid var(--seam); scroll-margin-top: 24px; }
.sug .s-what { margin: 0; font-weight: 600; }
.sug .s-gain, .sug .s-cost { margin: 3px 0 0; font-size: 15px; }
.sug .s-gain { color: var(--ok); }
.sug .s-cost { color: var(--ash); }
.sug .s-cost.hard { color: var(--risk); }
.sug .s-where { margin: 3px 0 0; font-size: 14px; color: var(--dim); }
.sug .eff { margin-top: 3px; }
.sug .imp { margin-top: 3px; }
.sug .why { margin-top: 4px; justify-self: end; }
.sug .area-tag { display: none; font: 500 13px/1 var(--narrow); color: var(--dim); margin-bottom: 4px; }
.note { padding: 12px 0 12px 148px; border-bottom: 1px solid var(--seam); font-size: 15px; color: var(--ash); }
.note b { color: var(--bone); font-weight: 500; }
.note.pick { color: var(--bone); }
.note.pick b { color: var(--brass); font-weight: 600; }
.quiet { display: flex; flex-wrap: wrap; gap: 6px 28px; padding: 14px 0 0; font-size: 15px; color: var(--ash); }
.quiet span b { color: var(--bone); font-weight: 500; }

/* fastest-first view: same elements, re-ordered by CSS */
.imps.fast { gap: 0; border-top: 1px solid var(--seam); }
.imps.fast .area { display: contents; }
.imps.fast .area-head, .imps.fast .note, .imps.fast .quiet { display: none; }
.imps.fast .sug { order: var(--o); }
.imps.fast .area-tag { display: block; }
.imps.fast .perm-pre { display: none; }

.flash { animation: flash 1.6s ease-out; }
@keyframes flash { 0% { background: #d3ab6240; } 100% { background: transparent; } }

/* ---------- 4  Explanations ---------- */
.explain { margin: 52px 0 0; border-top: 1px solid var(--seam); }
.explain > summary { list-style: none; cursor: pointer; display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px 24px; padding: 40px 0 24px; }
.explain > summary::-webkit-details-marker { display: none; }
.explain > summary h2 { margin: 0; font: 400 clamp(30px, 4vw, 39px)/1.1 var(--display); display: flex; align-items: center; gap: 14px; }
.explain > summary h2::before { content: ""; width: 10px; height: 10px; border-right: 2px solid var(--brass); border-bottom: 2px solid var(--brass); transform: rotate(-45deg); transition: transform 160ms; }
.explain[open] > summary h2::before { transform: rotate(45deg); }
.explain > summary .sum-note { color: var(--ash); font-size: 15px; }
.tbl { margin: 0 0 34px; }
.tbl h3 { margin: 0 0 10px; font: 600 17px/1.3 var(--text); }
.scroll { overflow-x: auto; }
table.x { width: 100%; min-width: 640px; border-collapse: collapse; font-size: 14px; line-height: 1.4; }
table.x th { text-align: left; font: 500 13px/1.2 var(--narrow); color: var(--ash); padding: 8px 12px 8px 0; border-bottom: 1px solid var(--seam); }
table.x td { padding: 9px 12px 9px 0; border-bottom: 1px solid var(--seam); vertical-align: top; }
table.x td:first-child { font-weight: 600; white-space: nowrap; }
table.x tr { scroll-margin-top: 24px; }
table.x tr.flash td { animation: flash 1.6s ease-out; }
.conf { font: 500 12px/1 var(--narrow); color: var(--ash); padding: 3px 6px; border-radius: 3px; background: var(--plate-2); white-space: nowrap; }

footer { padding: 26px 0 48px; color: var(--dim); font-size: 13px; }

@media (max-width: 900px) {
  .now { grid-template-columns: 1fr; }
  .panel, .panel.third { grid-column: span 6; }
  .sug { grid-template-columns: 1fr auto; }
  .sug > .s-body { grid-column: 1 / -1; grid-row: 2; }
  .sug > .imp-col { grid-column: 1 / -1; grid-row: 3; }
  .sug .why { grid-row: 1; grid-column: 2; }
  .note { padding-left: 0; }
  .res-split { grid-template-columns: 1fr; }
}
@media (max-width: 520px) {
  .band span { display: none; }
  .res-board { grid-template-columns: repeat(2, 1fr); }
  .rr { grid-template-columns: 1fr; }
  .steps li { grid-template-columns: 24px 1fr; }
  .steps .eff { grid-row: auto; grid-column: 2; justify-self: start; margin-top: 4px; }
}
@media (prefers-reduced-motion: reduce) {
  .fill, .flash, table.x tr.flash td { animation: none; }
}
"""

SCRIPT = r"""
(function () {
  var imps = document.getElementById('imps');
  var buttons = document.querySelectorAll('.view button');
  buttons.forEach(function (b) {
    b.addEventListener('click', function () {
      var fast = b.dataset.view === 'fast';
      imps.classList.toggle('fast', fast);
      buttons.forEach(function (o) { o.setAttribute('aria-pressed', String(o === b)); });
    });
  });

  var explain = document.getElementById('explain');
  function reveal(id, smooth) {
    var el = id && document.getElementById(id);
    if (!el) return;
    if (explain.contains(el)) explain.open = true;
    el.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'center' });
    el.classList.remove('flash');
    void el.offsetWidth;
    el.classList.add('flash');
  }
  document.addEventListener('click', function (e) {
    var a = e.target.closest('a[href^="#"]');
    if (!a) return;
    var id = a.getAttribute('href').slice(1);
    if (!document.getElementById(id)) return;
    e.preventDefault();
    try { history.replaceState(null, '', '#' + id); } catch (err) { /* a sandboxed frame has no history to write */ }
    var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    reveal(id, !reduce);
  });
  if (location.hash) reveal(location.hash.slice(1), false);
})();
"""
