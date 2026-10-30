#!/usr/bin/env python3
"""Build the Windows installer of Grim Dawn Build Check.

    python installer/build_installer.py --version <VERSION.txt> [--out dist] [--embed-zip FILE]
                                        [--no-iscc | --iscc PATH]

Steps: (1) fetch the pinned embeddable Python (installer/python-embed.json, SHA256 checked, cached in
build/cache), (2) stage it with the engine and the launcher in <out>/stage, (3) smoke-test the staged copy
with the bundled interpreter, (4) when Inno Setup's ISCC is available, build <out>/GrimDawnBuildCheck-Setup.exe
and <out>/SHA256SUMS.txt. Only the standard library is used."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SETUP_NAME = "GrimDawnBuildCheck-Setup.exe"
SUMS_NAME = "SHA256SUMS.txt"

# (source relative to the repo, name inside the install folder)
APP_FILES = [
    ("gdcheck", "gdcheck"),
    ("lokal/gd_tools.py", "gd_tools.py"),
    ("installer/start.cmd", "start.cmd"),
    ("installer/gdcheck.ico", "gdcheck.ico"),
    ("LICENSE", "LICENSE.txt"),
    ("VERSION.txt", "VERSION.txt"),
]

_LABEL = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.(\d+)$")


def inno_version(label: str) -> str:
    """Release label '2026-10-03.75' -> Inno Setup version '2026.10.3.75'."""
    m = _LABEL.match(label)
    if not m:
        raise ValueError(f"release label must look like YYYY-MM-DD.NN, got {label!r}")
    return ".".join(str(int(g)) for g in m.groups())


def pth_text(zip_name: str) -> str:
    """Content of the embeddable's python3xx._pth: the standard library zip and the install folder only.
    No 'import site' line, so neither site-packages nor a user site can leak into the bundled Python."""
    return f"{zip_name}\n.\n"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    actual = sha256_of(path)
    if actual.lower() != expected.strip().lower():
        raise ValueError(f"SHA256 mismatch for {path.name}: expected {expected.lower()}, got {actual}")


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "grim-dawn-build-check-installer"})
    with urllib.request.urlopen(req, timeout=120) as resp, part.open("wb") as fh:
        shutil.copyfileobj(resp, fh)
    part.replace(dest)


def fetch_embeddable(pin: dict, cache: Path) -> Path:
    """The pinned embeddable in the cache; downloaded when absent or when the cached file has a wrong hash."""
    target = cache / pin["url"].rsplit("/", 1)[1]
    if target.is_file():
        try:
            verify_sha256(target, pin["sha256"])
            return target
        except ValueError:
            target.unlink()
    print(f"Downloading {pin['url']}")
    download(pin["url"], target)
    verify_sha256(target, pin["sha256"])           # a bad download is an error, not a retry
    return target


def _force_remove(func, path, _exc):
    """rmtree handler: a read-only attribute (also copied from the source folders) must not block cleanup."""
    os.chmod(path, 0o700)
    func(path)


def _rmtree(path: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_force_remove)
    else:                                            # onexc arrived in 3.12
        shutil.rmtree(path, onerror=_force_remove)


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy `src` below `dst` without __pycache__ and *.pyc. Folder attributes are not copied: a read-only
    flag on a source folder must not make the staged copy impossible to delete or to overwrite."""
    dst.mkdir(parents=True, exist_ok=True)
    for entry in sorted(src.iterdir()):
        if entry.name == "__pycache__" or entry.suffix == ".pyc":
            continue
        if entry.is_dir():
            _copy_tree(entry, dst / entry.name)
        else:
            shutil.copy2(entry, dst / entry.name)


def stage(repo: Path, embed_zip: Path, out: Path) -> Path:
    """Unpack the embeddable into `out`, replace its ._pth and copy APP_FILES. `out` is emptied first."""
    repo, out = Path(repo).resolve(), Path(out).resolve()
    if out == repo or out in repo.parents:
        raise ValueError(f"refusing to stage into {out}: it contains the repository")
    for src, _dst in APP_FILES:
        if not (repo / src).exists():
            hint = " (generate it with python tools/make_icon.py)" if src.endswith(".ico") else ""
            raise ValueError(f"{src} is missing in {repo}{hint}")
    with zipfile.ZipFile(embed_zip) as z:
        names = [n for n in z.namelist() if "/" not in n]
        pths = [n for n in names if re.fullmatch(r"python3\d*\._pth", n)]
        zips = [n for n in names if re.fullmatch(r"python3\d*\.zip", n)]
        if len(pths) != 1 or len(zips) != 1:
            raise ValueError(f"expected exactly one python3*._pth and one python3*.zip in {Path(embed_zip).name}, "
                             f"found {pths} and {zips}")
        if out.exists():
            _rmtree(out)
        out.mkdir(parents=True)
        z.extractall(out)
    (out / pths[0]).write_text(pth_text(zips[0]), encoding="ascii", newline="\n")
    for src, dst in APP_FILES:
        s, d = repo / src, out / dst
        if s.is_dir():
            _copy_tree(s, d)
        else:
            shutil.copy2(s, d)
    return out


# ---------------------------------------------------------------------------------------------- smoke test
def _smoke_env(data_dir: Path) -> dict:
    """What a player's machine looks like for the bundled Python: no Python on PATH, no PYTHON* variables, a
    throw-away data folder (the real %LOCALAPPDATA%\\GrimDawnBuildCheck is never touched)."""
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("PYTHON", "GDCHECK_"))}
    env["PATH"] = str(Path(sysroot) / "System32")
    env["GDCHECK_NOPAUSE"] = "1"
    env["GDCHECK_DATA_DIR"] = str(data_dir)
    return env


def _run(cmd, env, what: str, cwd: Path) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=300)
    except (OSError, subprocess.SubprocessError) as ex:
        raise RuntimeError(f"smoke test '{what}' could not run: {ex}") from ex
    text = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    return p.returncode, text


def smoke(stage_dir: Path) -> None:
    """Run the staged app with the bundled Python. RuntimeError (with the program's output) on any failure.
    Leaves nothing behind: the throw-away data folder and the __pycache__ folders the runs create are removed."""
    stage_dir = Path(stage_dir)
    py = str(stage_dir / "python.exe")
    data_dir = stage_dir.with_name(stage_dir.name + ".smoke-data")
    env = _smoke_env(data_dir)
    try:
        # Neutral working folder: from the repo root a broken ._pth would let `-m gdcheck` import the repo copy.
        data_dir.mkdir(parents=True, exist_ok=True)
        for args in (["-m", "gdcheck", "--help"], ["-m", "gdcheck", "--where"],
                     ["-c", "import gdcheck.app.product, gdcheck.app.index_build, "
                            "gdcheck.app.refs_extract, gdcheck.app.anim_frames"],
                     # exactly how gd_tools starts a child tool in product mode (PY_FLAGS, then safe_run)
                     ["-u", "-m", "gdcheck.app.safe_run", "gdcheck", "--help"]):
            what = " ".join(["python", "-X", "utf8", *args])
            rc, text = _run([py, "-X", "utf8", *args], env, what, data_dir)
            if rc != 0:
                raise RuntimeError(f"smoke test '{what}' exited with {rc}:\n{text}")
        # The launcher itself: /s keeps the quotes around a path with spaces or non-ASCII characters intact.
        start = stage_dir / "start.cmd"
        rc, text = _run(f'cmd.exe /d /s /c ""{start}" --selftest"', env, "start.cmd --selftest", data_dir)
        if rc not in (0, 1):
            raise RuntimeError(f"smoke test 'start.cmd --selftest' exited with {rc}:\n{text}")
        if "Traceback" in text:
            raise RuntimeError(f"smoke test 'start.cmd --selftest' crashed:\n{text}")
        if "Grim Dawn" not in text:
            raise RuntimeError(f"smoke test 'start.cmd --selftest' printed no Grim Dawn line:\n{text}")
    finally:
        _rmtree(data_dir) if data_dir.exists() else None
        for cache in list(stage_dir.rglob("__pycache__")):
            _rmtree(cache)


# ------------------------------------------------------------------------------------------------- Inno Setup
def find_iscc(explicit: str | None = None) -> Path | None:
    """--iscc argument, then PATH, then the default Inno Setup 6 folder."""
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    found = shutil.which("iscc")
    if found:
        return Path(found)
    pf = os.environ.get("ProgramFiles(x86)")
    if pf:
        p = Path(pf) / "Inno Setup 6" / "ISCC.exe"
        if p.is_file():
            return p
    return None


def write_sums(out: Path, names: list[str]) -> Path:
    """sha256sum-compatible list ('<hash>  <file>', LF) so `sha256sum -c` and Get-FileHash both work."""
    lines = [f"{sha256_of(out / n)}  {n}\n" for n in names]
    target = out / SUMS_NAME
    target.write_text("".join(lines), encoding="ascii", newline="\n")
    return target


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Stage, smoke-test and (with Inno Setup) build the installer.")
    ap.add_argument("--version", required=True, metavar="LABEL", help="release label; must equal VERSION.txt")
    ap.add_argument("--out", default=str(REPO / "dist"), help="output folder (default: dist)")
    ap.add_argument("--embed-zip", metavar="PATH", help="use this embeddable Python zip (hash still checked)")
    ap.add_argument("--no-iscc", action="store_true", help="stage and smoke-test only")
    ap.add_argument("--iscc", metavar="PATH", help="path to ISCC.exe")
    a = ap.parse_args(argv)

    recorded = (REPO / "VERSION.txt").read_text(encoding="utf-8").strip()
    if a.version != recorded:
        print(f"Error: --version {a.version} does not match VERSION.txt ({recorded}).", file=sys.stderr)
        return 2
    try:
        inno = inno_version(a.version)
    except ValueError as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 2

    pin = json.loads((REPO / "installer" / "python-embed.json").read_text(encoding="utf-8"))
    try:
        embed = Path(a.embed_zip) if a.embed_zip else fetch_embeddable(pin, REPO / "build" / "cache")
        verify_sha256(embed, pin["sha256"])
        out = Path(a.out).resolve()
        stage_dir = stage(REPO, embed, out / "stage")
        print(f"Staged {stage_dir}")
        smoke(stage_dir)
        print("Smoke test passed (help, --where, imports, start.cmd --selftest).")
    except (ValueError, RuntimeError, OSError) as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    if a.no_iscc:
        print("Skipping Inno Setup (--no-iscc).")
        return 0
    iscc = find_iscc(a.iscc)
    if iscc is None:
        print("Error: ISCC.exe not found (use --iscc PATH, put it on PATH, or pass --no-iscc).", file=sys.stderr)
        return 1
    setup = out / SETUP_NAME
    setup.unlink(missing_ok=True)
    cmd = [str(iscc), f"/DAppVersion={inno}", f"/DStageDir={stage_dir}", f"/O{out}",
           str(REPO / "installer" / "gdcheck.iss")]
    if subprocess.run(cmd).returncode != 0 or not setup.is_file():
        print("Error: Inno Setup did not produce the installer.", file=sys.stderr)
        return 1
    print(f"Wrote {write_sums(out, [SETUP_NAME])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
