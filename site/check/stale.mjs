// Patch guard: tells the visitor when the game data of this site are older than the current game version, and when the
// live game version was last checked. Loaded by index.html and check.html as an external module (the CSP of check.html
// allows scripts from 'self' only).
//
// The workflow writes data/live.json ({"live_version": "1.3.1.1", "checked": "2026-10-30", "seen": {"<source>": {"version":
// ..., "answered": "2026-10-30"}, ...}, ...}) next to data/current.json ({"game_version": "1.3.0.8", ...}). This module reads
// both files from the SAME origin (relative URLs; the browser asks no other site). It shows up to two things, both set with
// textContent, never as markup:
//  - the warning "Heads-up ..." (role="status"): only when live_version is higher than game_version. Its truth depends on
//    these two versions alone, not on how old the last check is: a stale date never adds or removes the warning.
//  - one line "live-status": the data version and the day of the last successful check, as long as that check is at most
//    3 days old by the clock of the browser and so is the last answer of EACH source; otherwise "Whether the game data
//    are current has not been checked ...", and when only one source has been silent, the line names it (Entscheidung 66 a).
// A missing, broken or unreachable file, or a version that is not four numbers, means: no warning, no error. The names of
// the two sources are the only text taken over from the file's keys, and only from this fixed list.

const FOUR_NUMBERS = /^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$/;
const DATE_TEXT = /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/;
export const MAX_AGE_DAYS = 3;          // a check from this many days ago still counts as current; one day older does not
export const MAX_FUTURE_DAYS = 1;       // a date this many days ahead of the browser clock is tolerated (time zones); more is not

function key(version) {
  return typeof version === "string" && FOUR_NUMBERS.test(version) ? version.split(".").map(Number) : null;
}

// true when `live` is a higher version than `data` (numbers, not text: 1.3.10.0 is newer than 1.3.9.0); invalid gives false
export function isNewer(live, data) {
  const a = key(live);
  const b = key(data);
  if (!a || !b) return false;
  for (let i = 0; i < 4; i += 1) {
    if (a[i] !== b[i]) return a[i] > b[i];
  }
  return false;
}

// the number of the day (days since 1970-01-01) of a date written YYYY-MM-DD, or null when it is not a real date
export function dayNumber(text) {
  const m = typeof text === "string" ? DATE_TEXT.exec(text) : null;
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const t = new Date(Date.UTC(y, mo - 1, d));
  t.setUTCFullYear(y);                 // years 0..99 would otherwise be read as 1900..1999
  if (t.getUTCFullYear() !== y || t.getUTCMonth() !== mo - 1 || t.getUTCDate() !== d) return null;
  return Math.round(t.getTime() / 86400000);
}

// today's calendar day by the clock of the browser (local date), as a day number
function today(now) {
  return Math.round(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) / 86400000);
}

// the two sources of the guard as live.json names them in "seen", and how the line calls them (a fixed list: nothing of
// the file's keys is shown unless it is one of these)
const SOURCE_LABELS = [["steam-news", "Steam news"], ["crate-forum", "the Crate forum"]];

// the sources of live.json whose last answer is more than MAX_AGE_DAYS old or unknown (never answered, no real date, a date
// too far ahead), as [{label, since}] in the fixed order; `since` is the day of the last answer when it is believable, else
// null. A live.json without "seen" says nothing about single sources: [].
export function silentSources(live, now = new Date()) {
  const seen = live && typeof live === "object" ? live.seen : null;
  if (!seen || typeof seen !== "object" || Array.isArray(seen)) return [];
  const out = [];
  for (const [id, label] of SOURCE_LABELS) {
    if (!Object.prototype.hasOwnProperty.call(seen, id)) continue;
    const entry = seen[id];
    const answered = entry && typeof entry === "object" && typeof entry.answered === "string" ? entry.answered : null;
    const day = dayNumber(answered);
    const age = day === null ? null : today(now) - day;
    if (age !== null && age <= MAX_AGE_DAYS && age >= -MAX_FUTURE_DAYS) continue;
    out.push({ label, since: age !== null && age >= -MAX_FUTURE_DAYS ? answered : null });
  }
  return out;
}

// the text of the status line for the contents of live.json and current.json (anything, also null) at the time `now`
export function statusLine(live, current, now = new Date()) {
  const liveObj = live && typeof live === "object" ? live : {};
  const dataVersion = current && typeof current === "object" ? current.game_version : null;
  const checked = typeof liveObj.checked === "string" ? liveObj.checked : null;
  const day = dayNumber(checked);
  const age = day === null ? null : today(now) - day;      // days since the check; negative: the date is in the future
  const known = key(liveObj.live_version) !== null && key(dataVersion) !== null;
  if (known && age !== null && age <= MAX_AGE_DAYS && age >= -MAX_FUTURE_DAYS) {
    const silent = silentSources(liveObj, now);
    if (silent.length === 0) return `Game data ${dataVersion}; live game version last checked on ${checked}.`;
    const named = silent.map((s) => (s.since ? `${s.label} (last answer ${s.since})` : s.label));
    return `Whether the game data are current has not been checked against ${named.join(" and ")}.`;
  }
  // a date from the future cannot be trusted as "since": it is left out
  const since = age !== null && age >= -MAX_FUTURE_DAYS ? ` since ${checked}` : "";
  return `Whether the game data are current has not been checked${since}.`;
}

export const FETCH_TIMEOUT_MS = 5000;   // a file that does not answer in this time counts as missing: the check goes on

async function readJSON(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(url, { cache: "no-cache", signal: controller.signal });
    return res.ok ? await res.json() : null;
  } catch (ex) {
    return null;                       // unreachable, too slow or not JSON: the notice is optional
  } finally {
    clearTimeout(timer);
  }
}

async function load() {
  const [live, current] = await Promise.all([readJSON("data/live.json"), readJSON("data/current.json")]);
  return { live, current };
}

function versions(live, current) {
  return {
    liveVersion: live && typeof live === "object" ? live.live_version : null,
    dataVersion: current && typeof current === "object" ? current.game_version : null,
  };
}

function show(info) {
  const note = document.createElement("p");
  note.className = "stale";
  note.setAttribute("role", "status");
  note.setAttribute("data-testid", "stale-notice");
  note.textContent = `Heads-up: the game data on this page is for Grim Dawn ${info.data}. ` +
    `The current game version is ${info.live}, so some values may differ until the data is updated.`;
  document.body.insertBefore(note, document.body.firstChild);
}

// the status line goes just above the footer of the page (at the end of the page when there is none)
function showStatus(text) {
  const line = document.createElement("p");
  line.className = "live-status";
  line.setAttribute("data-testid", "live-status");
  line.textContent = text;
  const footer = document.querySelector ? document.querySelector("footer") : null;
  if (footer && footer.parentNode) footer.parentNode.insertBefore(line, footer);
  else document.body.appendChild(line);
}

async function run() {
  try {
    const { live, current } = await load();
    const { liveVersion, dataVersion } = versions(live, current);
    const info = isNewer(liveVersion, dataVersion) ? { live: liveVersion, data: dataVersion } : null;
    try {
      if (info) show(info);
      showStatus(statusLine(live, current));
    } catch (ex) {
      // a page without the expected structure: the answer for the engine does not depend on showing anything
    }
    return info;
  } catch (ex) {
    return null;                       // never a console error for an optional hint
  }
}

// the result of the one check this page makes: {live, data} or null (check/main.mjs hands it to the engine)
export const done = typeof document === "undefined" ? Promise.resolve(null) : run();
