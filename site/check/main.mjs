// Page logic of the online check (check.html). The engine runs in check/worker.mjs; this file owns the page:
// the states, the file, the profile, the progress, Stop and the report in a sandboxed iframe.
//
// States (main#app[data-state]): start -> closed | updating | unsupported | damaged   (nothing else works then)
//                                start -> idle -> loading -> ready -> checking -> result | failed
// The save is read with File.arrayBuffer() and handed to the worker (transferred). It is never stored or sent.

const MAX_SAVE_BYTES = 16 * 1024 * 1024;       // the engine's limit (gdcheck.app.web.MAX_SAVE_BYTES); a test pins the equality
const SLOW_AFTER_MS = 30000;

const STEP_TEXT = {
  engine: "Loading the analysis engine ...",
  data: "Loading the game data ...",
  read: "Reading your save ...",
  report: "Building the report ...",
};

const $ = (id) => document.getElementById(id);
const app = $("app");
const els = {
  drop: $("drop"), file: $("file"), profile: $("profile"), status: $("status"), spin: $("spin"), slow: $("slow"),
  stop: $("stop"), retry: $("retry"), result: $("result"), title: $("result-title"), host: $("report-host"),
  deeper: $("deeper"), again: $("again"), selftest: $("selftest"),
};

let config = null;         // check/config.json
let worker = null;
let booted = false;        // the worker said "ready"
let pending = null;        // {file, profile, detail} waiting for the worker, or being checked
let current = null;        // the File of the report on screen (kept as a reference only: its bytes are read again for "Deeper analysis")
let slowTimer = null;
let quietBoot = false;     // the worker runs only for ?selftest (closed/updating page)
const query = new URLSearchParams(location.search);

function setState(state) {
  app.dataset.state = state;
}

function showMessage(which) {
  for (const box of document.querySelectorAll("[data-msg]")) box.hidden = box.dataset.msg !== which;
}

function say(text, kind) {
  els.status.textContent = text;
  els.status.className = kind || "";
}

function busy(on) {
  els.spin.hidden = !on;
  els.stop.hidden = !on;
}

// ---- the hint after 30 s in one step ("This takes longer on this device. You can keep waiting.")
function watch(on) {
  clearTimeout(slowTimer);
  els.slow.hidden = true;
  if (on) slowTimer = setTimeout(() => { els.slow.hidden = false; }, SLOW_AFTER_MS);
}

// ---- the worker
function supported() {
  return typeof Worker === "function" && typeof WebAssembly === "object";
}

function startWorker() {
  let moduleRead = false;
  const options = { get type() { moduleRead = true; return "module"; } };          // a browser without module workers never reads it
  const w = new Worker(new URL("./worker.mjs", import.meta.url), options);
  if (!moduleRead) {
    w.terminate();
    return null;
  }
  w.onmessage = (e) => { if (w === worker) onWorker(e.data || {}); };     // a worker that was ended (Stop, damaged data) stays silent
  w.onerror = () => onWorker({ type: "error", step: "engine", state: "failed", message: "The analysis engine could not be loaded. Check your connection and reload the page." });
  return w;
}

function endWorker() {
  if (worker) worker.terminate();
  worker = null;
  booted = false;
  watch(false);
}

// quiet: boot only for ?selftest on a closed or updating page (no game data): the page keeps its state and shows no check UI
function boot(quiet) {
  quietBoot = quiet;
  worker = startWorker();
  if (!worker) { showMessage("unsupported"); setState("unsupported"); return; }
  if (!quiet) {
    setState("loading");
    busy(true);
    say(STEP_TEXT.engine);
    watch(true);
  }
  worker.postMessage({ type: "boot", config, base: document.baseURI, selftest: query.has("selftest") });
}

function onWorker(msg) {
  if (quietBoot) {
    if (msg.type === "selftest") showSelftest(msg.result);
    else if (msg.type === "error") { els.selftest.textContent = `selftest failed: ${msg.message}`; els.selftest.hidden = false; }
    return;
  }
  if (msg.type === "progress") {
    say(STEP_TEXT[msg.step] || "Working ...");
    watch(true);
  } else if (msg.type === "selftest") {
    showSelftest(msg.result);
  } else if (msg.type === "ready") {
    booted = true;
    watch(false);
    if (pending) runPending();
    else { busy(false); say(msg.game_version ? `Ready. Game data ${msg.game_version}.` : "Engine loaded (no game data)."); setState("ready"); }
  } else if (msg.type === "result") {
    showResult(msg);
  } else if (msg.type === "error") {
    onError(msg);
  }
}

function showSelftest(r) {
  r = r || {};
  els.selftest.textContent = `engine ${r.engine}\npython ${r.python}\nindex schema ${r.schema}\nselftest ${r.ok ? "ok" : "FAILED"}\n` +
    Object.entries(r.parts || {}).map(([k, v]) => `${k}: ${v ? "ok" : "FAILED"}`).join("\n");
  els.selftest.hidden = false;
}

function onError(msg) {
  watch(false);
  busy(false);
  if (msg.state === "damaged" || msg.state === "updating") {            // the game data: the page changes its state (E5)
    endWorker();
    pending = null;
    showMessage(msg.state);
    setState(msg.state);
  } else if (msg.state === "rejected") {                                 // this file was refused; the worker stays
    pending = null;
    say(msg.message, "error");
    setState("ready");
  } else {                                                               // the engine could not run: start over on demand
    endWorker();
    say(msg.message, "error");
    els.retry.hidden = false;
    setState("failed");
  }
}

// ---- the file
function takeFile(files) {
  if (!files || files.length !== 1) { say("Please choose exactly one file (your player.gdc).", "error"); return; }
  const file = files[0];
  if (file.size === 0) { say("This file is empty.", "error"); return; }
  if (file.size > MAX_SAVE_BYTES) { say("This file is too large to be a player.gdc.", "error"); return; }
  say("");
  start(file, config.detail !== false);                                  // the page's default (E1); "Deeper analysis" only when it is off
}

function start(file, detail) {
  pending = { file, profile: els.profile.value, detail };
  els.retry.hidden = true;
  if (booted) runPending();
  else if (!worker) boot(false);
}

async function runPending() {
  const job = pending;
  setState("checking");
  busy(true);
  say(STEP_TEXT.read);
  watch(true);
  const buffer = await job.file.arrayBuffer();                           // the bytes exist only in memory
  if (pending !== job || !worker) return;                                // Stop was pressed meanwhile
  current = job.file;
  worker.postMessage({ type: "check", buffer, profile: job.profile, detail: job.detail }, [buffer]);
}

function showResult(msg) {
  const job = pending;
  pending = null;
  watch(false);
  busy(false);
  say("");
  els.title.textContent = msg.title || "Report";                         // the character's name: text only, never markup
  els.host.textContent = "";
  const frame = document.createElement("iframe");
  frame.setAttribute("sandbox", "allow-scripts");                        // scripts of the report, no same-origin access
  frame.setAttribute("allow", "clipboard-write");                        // only for the report's "Copy summary" button
  frame.setAttribute("title", "Build check report");
  frame.setAttribute("data-testid", "report");
  frame.id = "report";
  frame.srcdoc = msg.html;
  els.host.appendChild(frame);
  els.deeper.hidden = !(config.detail === false && !(job && job.detail));
  els.result.hidden = false;
  setState("result");
  els.again.focus();
}

function again() {
  els.host.textContent = "";
  els.result.hidden = true;
  current = null;
  say("");
  setState(booted ? "ready" : "idle");
  els.file.focus();
}

function stop() {
  pending = null;
  endWorker();
  busy(false);
  say("Stopped. Choose a file to start again.");
  setState("idle");
  els.file.focus();
}

// ---- start
async function main() {
  if (!supported()) { showMessage("unsupported"); setState("unsupported"); return; }
  try {
    const res = await fetch("check/config.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(String(res.status));
    config = await res.json();
  } catch (ex) {
    showMessage("damaged");
    setState("damaged");
    return;
  }
  if (config.notices) {
    $("notices-link").setAttribute("href", config.notices);
    $("notices-wrap").hidden = false;
  }
  if (config.state === "closed" || config.state === "updating") {
    showMessage(config.state);
    setState(config.state);
    if (query.has("selftest")) boot(true);                               // ?selftest still shows the engine and Python versions
    return;
  }
  if (config.state !== "open" || !config.data) { showMessage("damaged"); setState("damaged"); return; }

  els.file.addEventListener("change", () => { takeFile(els.file.files); els.file.value = ""; });
  for (const type of ["dragenter", "dragover"]) {
    els.drop.addEventListener(type, (e) => { e.preventDefault(); els.drop.classList.add("over"); });
  }
  for (const type of ["dragleave", "drop"]) {
    els.drop.addEventListener(type, (e) => { e.preventDefault(); els.drop.classList.remove("over"); });
  }
  els.drop.addEventListener("drop", (e) => takeFile(e.dataTransfer && e.dataTransfer.files));
  for (const type of ["dragover", "drop"]) window.addEventListener(type, (e) => e.preventDefault());   // a miss must not open the file
  els.stop.addEventListener("click", stop);
  els.retry.addEventListener("click", () => { els.retry.hidden = true; say(""); setState("idle"); if (pending) start(pending.file, pending.detail); });
  els.again.addEventListener("click", again);
  els.deeper.addEventListener("click", () => { if (current) { els.result.hidden = true; els.host.textContent = ""; start(current, true); } });
  say("");
  setState("idle");
  if (query.has("selftest")) boot(false);                                // open page: boots now, shows the result, then is ready
}

main();
