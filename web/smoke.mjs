#!/usr/bin/env node
// Pyodide smoke of the online check: the engine in Pyodide, run by Node, on the site folder web/build_site.py builds.
//
//   node web/smoke.mjs --site <_site> [--profile P] [--save <gdc>]... [--detail] [--boot-only] --out <json>
//
// It does what the page's worker does: reads check/config.json, loads the pinned Pyodide from the site, writes the engine
// zip and the data package into Pyodide's file system, imports gdcheck.app.web, calls prepare() and then check_path() for
// every save. Nothing else is read from the machine: no PC data, no network (the Pyodide of the site, the engine zip and
// the data package of the site folder are all it uses).
//
//   --site       the folder build_site.py wrote (check/config.json, pyodide/, app/, data/)
//   --save       a player.gdc (or any file: a file that is not a save becomes a message); repeatable, checked in order
//   --profile    the content profile (general|sr|ascendant|celestial) for the --save options that FOLLOW it; default general
//   --detail     run every save twice: without the detail run, then with it
//   --boot-only  stop after the engine import and the selftest: no data package needed (the closed site works)
//   --out        the JSON file this writes:
//     pyodide_version, node, state (config), boot_s, boot_cpu_s, prepare_s, prepare_cpu_s (null with --boot-only),
//     game_version, checks [{save, path, profile, detail, ok, seconds, cpu_s, html_sha256, html_len, message, heap_bytes}],
//     heap_bytes_max, heap_bytes_after_prepare, rss_bytes_max, cpu_s_total, wall_s_total,
//     modules (sys.modules at the end), modules_before_engine, selftest (web.selftest()).
//   seconds/boot_s/prepare_s are wall-clock; the *cpu_s values are the user+system time of this process (what a busy
//   machine does not stretch). html_sha256 is the SHA256 of the report's UTF-8 bytes.
//
// A file larger than web.MAX_SAVE_BYTES is not copied into Pyodide: a copy of it is mounted (Node only), so the engine
// must refuse it without reading it. Exit codes: 0 done (a check may still say ok=false: that is data), 1 failed
// (the JSON then has an "error" key), 2 wrong call. Standard library of Node only; no npm packages.
import { createHash } from "node:crypto";
import { copyFileSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const T0 = performance.now();
const wall = () => (performance.now() - T0) / 1000;
const cpu = () => { const u = process.cpuUsage(); return (u.user + u.system) / 1e6; };
// Pyodide (314.0.7) refuses a Node Buffer ("Unknown typed array type 'Buffer'"): hand it a plain Uint8Array view.
const u8 = (b) => new Uint8Array(b.buffer, b.byteOffset, b.length);
const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");
const PROFILES = ["general", "sr", "ascendant", "celestial"];

function usage(msg) {
  if (msg) console.error(msg);
  console.error("usage: node web/smoke.mjs --site <_site> [--profile P] [--save <gdc>]... [--detail] [--boot-only] --out <json>");
  process.exit(2);
}

function parse(argv) {
  const o = { saves: [], detail: false, bootOnly: false, profile: "general" };
  const value = (i) => { if (i + 1 >= argv.length) usage(`${argv[i]} needs a value`); return argv[i + 1]; };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--site") { o.site = value(i++); }
    else if (a === "--out") { o.out = value(i++); }
    else if (a === "--profile") {
      o.profile = value(i++);
      if (!PROFILES.includes(o.profile)) usage(`unknown profile ${o.profile}`);
    }
    else if (a === "--save") { o.saves.push({ path: value(i++), profile: o.profile }); }
    else if (a === "--detail") { o.detail = true; }
    else if (a === "--boot-only") { o.bootOnly = true; }
    else usage(`unknown argument ${a}`);
  }
  if (!o.site || !o.out) usage("--site and --out are required");
  return o;
}

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
def _modules():
    return _json.dumps(sorted(_sys.modules))
`;

async function main(o, res) {
  const site = path.resolve(o.site);
  const cfg = JSON.parse(readFileSync(path.join(site, "check", "config.json"), "utf8"));
  res.state = cfg.state;
  if (!o.bootOnly && !cfg.data) throw new Error(`the site is ${cfg.state}: it has no data package (use --boot-only)`);

  // ---- boot: Pyodide, the engine zip, the import (what the worker does before it says "ready")
  const b0 = wall(), c0 = cpu();
  const indexDir = path.join(site, cfg.pyodide);
  const { loadPyodide, version } = await import(pathToFileURL(path.join(indexDir, "pyodide.mjs")).href);
  const py = await loadPyodide({ indexURL: indexDir });
  res.pyodide_version = version;
  const heap = () => (py._module && py._module.HEAPU8 ? py._module.HEAPU8.length : process.memoryUsage().arrayBuffers);
  const note = () => { res.heap_bytes_max = Math.max(res.heap_bytes_max, heap()); return heap(); };
  res.heap_bytes_max = 0;
  res.modules_before_engine = JSON.parse(py.runPython("import json, sys\njson.dumps(sorted(sys.modules))"));
  py.unpackArchive(u8(readFileSync(path.join(site, cfg.engine))), "zip", { extractDir: "/engine" });
  py.runPython("import sys\nsys.path.insert(0, '/engine')");
  py.runPython(PY_SETUP);
  res.boot_s = wall() - b0;
  res.boot_cpu_s = cpu() - c0;
  note();
  res.selftest = JSON.parse(py.runPython("_json.dumps(_web.selftest())"));
  res.prepare_s = res.prepare_cpu_s = res.game_version = null;
  res.heap_bytes_after_prepare = null;

  // ---- prepare: the data package from the pointer (hash checked as the page does), then prepare()
  if (!o.bootOnly) {
    const ptr = JSON.parse(readFileSync(path.join(site, cfg.data), "utf8"));
    const blob = readFileSync(path.join(site, path.dirname(cfg.data), ptr.file));
    if (blob.length !== ptr.size || sha256(blob) !== ptr.sha256) throw new Error("the data package differs from its pointer");
    const p0 = wall(), pc0 = cpu();
    py.FS.writeFile("/tmp/gd-data.zip", u8(blob));
    const prep = py.globals.get("_prepare")();
    const r = prep.toJs({ dict_converter: Object.fromEntries });
    prep.destroy();
    res.prepare_s = wall() - p0;
    res.prepare_cpu_s = cpu() - pc0;
    res.heap_bytes_after_prepare = note();
    if (r.state !== "ok") throw new Error(`prepare said ${r.state}: ${r.message}`);
    res.game_version = r.game_version;
  }

  // ---- checks
  const maxBytes = py.runPython("_web.MAX_SAVE_BYTES");
  const check = py.globals.get("_check");
  const hosts = [];
  const run = (file, profile, detail) => {
    const size = statSync(file).size;
    let target, scratch = null;
    if (size > maxBytes) {                    // never loaded into Pyodide: a copy is mounted, the engine must refuse it by its size
      scratch = mkdtempSync(path.join(tmpdir(), "gdsmoke-"));
      copyFileSync(file, path.join(scratch, "upload.gdc"));
      const mount = `/host${hosts.length}`;
      hosts.push(scratch);
      py.mountNodeFS(mount, scratch);
      target = `${mount}/upload.gdc`;
    } else {
      target = "/tmp/upload.gdc";
    }
    const bytes = scratch ? null : readFileSync(file);
    const w0 = wall(), k0 = cpu();
    if (bytes) py.FS.writeFile(target, u8(bytes));
    const proxy = check(target, profile, detail);
    const r = proxy.toJs({ dict_converter: Object.fromEntries });
    proxy.destroy();
    const seconds = wall() - w0, cpu_s = cpu() - k0;
    if (scratch) rmSync(scratch, { recursive: true, force: true });
    res.checks.push({
      save: path.basename(file, path.extname(file)), path: file, profile, detail, ok: r.ok === true, seconds, cpu_s,
      html_sha256: r.ok ? sha256(Buffer.from(r.html, "utf8")) : null, html_len: r.html.length, message: r.message,
      heap_bytes: note(),
    });
  };
  if (!o.bootOnly) {
    for (const s of o.saves) {
      run(s.path, s.profile, false);
      if (o.detail) run(s.path, s.profile, true);
    }
  }
  check.destroy();
  res.modules = JSON.parse(py.runPython("_modules()"));
}

const o = parse(process.argv.slice(2));
const res = { node: process.version, checks: [], error: null };
let code = 0;
try {
  await main(o, res);
} catch (ex) {
  code = 1;
  res.error = String((ex && ex.stack) || ex);
  console.error(res.error);
}
res.cpu_s_total = cpu();
res.wall_s_total = wall();
res.rss_bytes_max = process.resourceUsage().maxRSS * 1024;     // peak resident set, KiB -> bytes
writeFileSync(o.out, JSON.stringify(res, null, 1) + "\n");
console.log(`smoke: Pyodide ${res.pyodide_version}, boot ${res.boot_s?.toFixed(1)} s, prepare ${res.prepare_s?.toFixed(1) ?? "-"} s, ` +
  `${res.checks.length} check(s), heap max ${(res.heap_bytes_max / 2 ** 20).toFixed(0)} MiB -> ${o.out}`);
process.exit(code);
