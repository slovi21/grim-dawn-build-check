// Module worker of the online check: Pyodide runs the engine here, never on the page's thread.
//
//   page -> worker   {type:"boot", config, base, selftest}     load Pyodide, the engine zip and (config.data) the game data
//                    {type:"check", buffer, profile, detail}   check one player.gdc (the buffer is transferred)
//   worker -> page   {type:"progress", step}                   step: engine | data | read | report
//                    {type:"ready", game_version}              booted; game_version is null without game data
//                    {type:"selftest", result}                 only when the boot asked for it (?selftest)
//                    {type:"result", html, title}              the report (title is plain text: the page sets it as text)
//                    {type:"error", step, state, message}      state: damaged | updating (the game data), rejected (a
//                                                              message about this file; the worker stays usable),
//                                                              failed (the engine could not run; the page ends the worker)
// "Stop" is the page ending this worker (terminate): nothing here has to cooperate.
//
// Every URL is relative to the page (`base`) and made by siteURL(), which refuses another origin. Pyodide is loaded from
// the site with an explicit indexURL: its default points to a CDN, which must never be used. The save exists only in this
// worker's memory and is deleted after the check.
// Same flow as web/smoke.mjs, which runs it in Node against the same site folder.

const PY_SETUP = `
import json as _json, sys as _sys
from pathlib import Path as _Path
from gdcheck.app import web as _web
_data = None
def _prepare():
    global _data
    try:
        _data = _web.prepare(_Path("/tmp/gd-data.zip"), _Path("/tmp/gd-data"))
    except _web.WebError as ex:
        return {"state": ex.state, "message": ex.message}
    return {"state": "ok", "game_version": _data.game_version}
def _check(path, profile, detail):
    return _web.check_path(_Path(path), _data, profile, detail)
`;

// The page's meta CSP does not govern a worker and GitHub Pages sends no CSP header, so "same origin only" is held here:
// every URL the worker fetches or imports is `p` resolved against `from` (default: the page) and must be on the page's origin.
export function siteURL(p, base, from = base) {
  const u = new URL(p, from);
  if (u.origin !== new URL(base).origin) throw new Error(`${u.href} is not on the origin of the page`);
  return u.href;
}

let py = null;          // the Pyodide instance
let hasData = false;    // _prepare() succeeded: _check() may run

const post = (msg) => self.postMessage(msg);
const progress = (step) => post({ type: "progress", step });
const fail = (step, state, message) => post({ type: "error", step, state, message });
const hex = (buf) => Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");

// Pyodide wants a plain Uint8Array view, not a Node Buffer (see smoke.mjs); fetch() gives an ArrayBuffer.
async function fetchBytes(url, cache) {
  const res = await fetch(url, { cache });
  if (!res.ok) throw new Error(`${res.status} for ${url}`);
  return new Uint8Array(await res.arrayBuffer());
}

async function boot(msg) {
  const { config, base } = msg;
  const url = (p, from) => siteURL(p, base, from);       // a value from config.json or the pointer on another origin throws

  progress("engine");
  let engineOk = false;
  try {
    const { loadPyodide } = await import(url(config.pyodide + "pyodide.mjs"));
    py = await loadPyodide({ indexURL: url(config.pyodide) });                  // never the loader's CDN default
    py.unpackArchive(await fetchBytes(url(config.engine), "force-cache"), "zip", { extractDir: "/engine" });
    py.runPython("import sys\nsys.path.insert(0, '/engine')");
    py.runPython(PY_SETUP);
    engineOk = true;
    if (msg.selftest) post({ type: "selftest", result: JSON.parse(py.runPython("_json.dumps(_web.selftest())")) });
  } catch (ex) {
    fail("engine", "failed", "The analysis engine could not be loaded. Check your connection and reload the page.");
    return;
  }

  if (!config.data) { post({ type: "ready", game_version: null }); return; }
  progress("data");
  let blob;
  try {
    const pointerUrl = url(config.data);
    const ptr = await (await fetch(pointerUrl, { cache: "no-cache" })).json();
    blob = await fetchBytes(url(ptr.file, pointerUrl), "force-cache");
    // The package is checked against its pointer here, and once more inside prepare() (its own checksums).
    if (blob.length !== ptr.size) throw new Error("size differs from the pointer");
    if (globalThis.crypto && crypto.subtle) {
      if (hex(await crypto.subtle.digest("SHA-256", blob)) !== ptr.sha256) throw new Error("SHA256 differs from the pointer");
    }
  } catch (ex) {
    fail("data", "damaged", "The game data of the online check is damaged. Reload the page; if it stays, use the installer.");
    return;
  }
  try {
    py.FS.writeFile("/tmp/gd-data.zip", blob);
    const proxy = py.globals.get("_prepare")();
    const r = proxy.toJs({ dict_converter: Object.fromEntries });
    proxy.destroy();
    py.FS.unlink("/tmp/gd-data.zip");
    if (r.state !== "ok") { fail("data", r.state, r.message); return; }       // damaged | updating, texts from the engine
    hasData = true;
    post({ type: "ready", game_version: r.game_version });
  } catch (ex) {
    fail("data", "damaged", "The game data of the online check is damaged. Reload the page; if it stays, use the installer.");
  }
}

function runCheck(msg) {
  if (!py || !hasData) { fail("report", "failed", "The analysis engine is not ready. Reload the page."); return; }
  progress("read");
  try {
    py.FS.writeFile("/tmp/upload.gdc", new Uint8Array(msg.buffer));            // check_path deletes it after the check
  } catch (ex) {
    fail("read", "failed", "The save could not be read. Reload the page and try again.");
    return;
  }
  progress("report");
  try {
    const proxy = py.globals.get("_check")("/tmp/upload.gdc", msg.profile, msg.detail === true);
    const r = proxy.toJs({ dict_converter: Object.fromEntries });
    proxy.destroy();
    if (r.ok === true) post({ type: "result", html: r.html, title: r.title });
    else fail("report", "rejected", r.message);                                // the engine's message about this file
  } catch (ex) {
    try { py.FS.unlink("/tmp/upload.gdc"); } catch (_) { /* check_path removed it */ }
    fail("report", "failed", "The check failed unexpectedly. Reload the page and try again.");
  }
}

self.onmessage = (event) => {
  const msg = event.data || {};
  if (msg.type === "boot") boot(msg);
  else if (msg.type === "check") runCheck(msg);
};
