"""Edit every file of an expert from the UI, with a check before anything is written and a version kept for every save.

An expert is a flat folder (`experts/<name>/`). The editable files are its text files: manifest, Python, notes and data
(`EDITABLE`). A save goes like this: lint and validate the draft (`expert_check.static`), load *all* experts with the draft in
place in a separate process (`expert_check.load`), and only if neither reports an error write the files and record a version.
Versions are full snapshots in `<versions>/<name>/<id>.json`, so switching to one is a save of its files, checked the same way.

Python is code the server runs, so whether a request may change it is the caller's decision (`allow_code`): see
`server._code_edit_allowed`.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, cast

import yaml

from . import expert_check
from .expert_store import ExpertError, _dir  # pyright: ignore[reportPrivateUsage]  (one definition of a valid expert folder)

EDITABLE = (".py", ".yaml", ".yml", ".md", ".txt", ".json")
MAX_FILE = 256 * 1024       # bytes; experts are small, and every version keeps a copy of every file
MAX_FILES = 40
KEEP_VERSIONS = 200         # per expert; the oldest go first
Problem = expert_check.Problem


def _valid_file_name(name: str) -> bool:
    return (0 < len(name) <= 64 and not name.startswith(".") and name.endswith(EDITABLE)
            and all(c.isalnum() or c in "_.-" for c in name))


def _folder(root: Path, name: str, must_exist: bool = True) -> Path:
    path = _dir(root, name)
    if must_exist and not (path / "expert.yaml").is_file():
        raise ExpertError(f"Unknown expert '{name}'", 404)
    return path


def read_files(folder: Path) -> dict[str, str]:
    """The editable files as they are on disk. Anything else in the folder (caches, binaries) is left alone and not shown."""
    out: dict[str, str] = {}
    for p in sorted(folder.iterdir()) if folder.is_dir() else []:
        if p.is_file() and _valid_file_name(p.name):
            try:
                out[p.name] = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
    return out


def digest(files: dict[str, str]) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode() + b"\0" + files[name].encode() + b"\0")
    return h.hexdigest()[:16]


def code_changed(before: dict[str, str], after: dict[str, str]) -> bool:
    """Python changed, or the MCP server an expert starts (`server:` in expert.yaml can name any program to run)."""
    py = {n for n in (*before, *after) if n.endswith(".py")}
    return any(before.get(n) != after.get(n) for n in py) or _server(before) != _server(after)


def _server(files: dict[str, str]) -> Any:
    try:
        m: Any = yaml.safe_load(files.get("expert.yaml", "")) or {}
    except yaml.YAMLError:
        return files.get("expert.yaml")   # unreadable: compare the text, so any edit counts
    return cast(dict[str, Any], m).get("server") if isinstance(m, dict) else None


def _shape(files: dict[str, str]) -> None:
    """Refuse what we would never write, before looking at the content."""
    if len(files) > MAX_FILES:
        raise ExpertError(f"An expert can have at most {MAX_FILES} files.")
    for name, text in files.items():
        if not _valid_file_name(name):
            raise ExpertError(f"'{name}' is not a file name the editor can use: letters, digits, '_', '-' and '.', "
                              f"no folders, ending in {', '.join(EDITABLE)}.")
        if len(text.encode("utf-8")) > MAX_FILE:
            raise ExpertError(f"{name} is larger than {MAX_FILE // 1024} KB.")


def _stage(root: Path, name: str, files: dict[str, str], dest: Path) -> Path:
    """A copy of every expert with this one's editable files replaced by the draft: what the server would load after saving."""
    shutil.copytree(root, dest, ignore=shutil.ignore_patterns("__pycache__", ".*"))
    folder = dest / name
    folder.mkdir(exist_ok=True)
    for old in read_files(folder):
        (folder / old).unlink()
    for fname, text in files.items():
        (folder / fname).write_text(text, encoding="utf-8")
    return dest


def check(root: Path, name: str, files: dict[str, str], *, load: bool = True) -> dict[str, Any]:
    """Lint, then (if that found no error) load. Loading runs the draft's code, so the caller must allow that first."""
    _folder(root, name, must_exist=False)
    _shape(files)
    problems = expert_check.static(files, name)
    loaded = False
    if load and not any(p["severity"] == "error" for p in problems):
        with tempfile.TemporaryDirectory(prefix="noah-stage-") as tmp:
            problems += expert_check.load(_stage(root, name, files, Path(tmp) / "experts"), name, cwd=Path.cwd())
        loaded = True
    errors = sum(p["severity"] == "error" for p in problems)
    return {"ok": errors == 0, "errors": errors, "warnings": len(problems) - errors, "loaded": loaded, "problems": problems}


# ---- versions ----
def _vdir(versions: Path, name: str) -> Path:
    return _dir(versions, name)   # the same name rules as an expert folder: no way out of the versions folder


def _version_files(versions: Path, name: str) -> list[Path]:
    d = _vdir(versions, name)
    return sorted(d.glob("*.json"), key=lambda p: int(p.stem)) if d.is_dir() else []


def _load_version(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def record(versions: Path, name: str, files: dict[str, str], note: str) -> dict[str, Any]:
    """Keep a snapshot. Saving the same content twice in a row keeps one version, not two."""
    existing = _version_files(versions, name)
    h = digest(files)
    if existing:
        last = _load_version(existing[-1])
        if last["hash"] == h:
            return last
    vid = int(existing[-1].stem) + 1 if existing else 1
    v: dict[str, Any] = {"id": vid, "at": time.time(), "note": note.strip()[:200], "hash": h, "files": files}
    d = _vdir(versions, name)
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".v-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(v, f)
    os.replace(tmp, d / f"{vid}.json")
    for old in existing[: max(0, len(existing) + 1 - KEEP_VERSIONS)]:
        old.unlink(missing_ok=True)
    return v


def baseline(versions: Path, root: Path, name: str) -> None:
    """Before the first change made in Noah, keep what was there (shipped or hand-written), so it can always be restored."""
    folder = _dir(root, name)
    if not _version_files(versions, name) and (folder / "expert.yaml").is_file():
        record(versions, name, read_files(folder), "As it was before editing in Noah")


def list_versions(versions: Path, root: Path, name: str) -> list[dict[str, Any]]:
    current = digest(read_files(_folder(root, name)))
    out: list[dict[str, Any]] = []
    for p in reversed(_version_files(versions, name)):
        v = _load_version(p)
        out.append({"id": v["id"], "at": v["at"], "note": v["note"], "hash": v["hash"], "files": sorted(v["files"]),
                    "current": v["hash"] == current})
    seen = False
    for v in out:  # the same content can appear twice (A, B, back to A): only the newest is "current"
        v["current"], seen = v["current"] and not seen, seen or v["current"]
    return out


def get_version(versions: Path, name: str, vid: int) -> dict[str, Any]:
    path = _vdir(versions, name) / f"{vid}.json"
    if not path.is_file():
        raise ExpertError(f"{name} has no version {vid}.", 404)
    return _load_version(path)


# ---- saving ----
def _write_all(folder: Path, files: dict[str, str]) -> None:
    """Write every file to a temporary name first, then swap them in, so a failure part-way leaves no half-written file."""
    folder.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[str, Path]] = []
    try:
        for fname, text in files.items():
            fd, tmp = tempfile.mkstemp(dir=folder, prefix=".edit-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            staged.append((fname, Path(tmp)))
        for fname, tmp in staged:
            os.replace(tmp, folder / fname)
    finally:
        for _, tmp in staged:
            tmp.unlink(missing_ok=True)
    for old in read_files(folder):
        if old not in files:
            (folder / old).unlink()
    shutil.rmtree(folder / "__pycache__", ignore_errors=True)  # never let a stale .pyc stand in for the new source


def save(root: Path, versions: Path, name: str, files: dict[str, str], *, base: str, note: str, allow_code: bool) -> dict[str, Any]:
    """Check and write. `base` is the hash the editor loaded: if the files changed since (another tab, a hand edit), refuse."""
    folder = _folder(root, name)
    before = read_files(folder)
    if base and digest(before) != base:
        raise ExpertError("These files changed since you opened them (in another tab or on disk). Reload to see the changes; "
                          "your edits stay in the editor until you do.", 409)
    if code_changed(before, files) and not allow_code:
        raise ExpertError(CODE_REFUSED, 403)
    report = check(root, name, files)
    if not report["ok"]:
        return {"saved": False, "report": report}
    baseline(versions, root, name)
    _write_all(folder, files)
    v = record(versions, name, files, note or "Edited in Noah")
    return {"saved": True, "report": report, "version": v["id"], "hash": v["hash"]}


CODE_REFUSED = ("Python files can only be changed from the computer running Noah (open it at http://localhost:8765), "
                "because the server runs that code. To allow it from elsewhere, start Noah with NOAH_CODE_EDIT=1.")
