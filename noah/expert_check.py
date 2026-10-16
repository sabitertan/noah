"""Check an expert's files before they are saved: lint what can be read statically, then load the experts in a child process.

`static(files, folder)` parses YAML, validates the manifest, compiles every Python file and lints it with pyflakes. It never
runs the draft. `load(root, name)` runs the draft for real, in a separate process with a time limit: it loads *every* expert
the way the server does (so a draft cannot leave the rest unable to load), imports the tool and calls `extract_rule` on the
expert's own examples. Run as `python -m noah.expert_check <root> <name> <out.json>` (the packaged server takes
`--check-experts` with the same arguments).
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, cast

import yaml

try:
    from pyflakes import checker as _pyflakes, messages as _pf
    # what certainly fails when the code runs; everything else pyflakes reports (unused imports, shadowing) is a warning
    _fatal: tuple[type, ...] = (_pf.UndefinedName, _pf.UndefinedLocal, _pf.UndefinedExport, _pf.DuplicateArgument,
                                _pf.ReturnOutsideFunction, _pf.YieldOutsideFunction, _pf.ContinueOutsideLoop, _pf.BreakOutsideLoop,
                                _pf.TwoStarredExpressions, _pf.TooManyExpressionsInStarredAssignment, _pf.ForwardAnnotationSyntaxError)
except ImportError:  # optional: without it Python files are still compiled and loaded, just not linted
    _pyflakes = None
    _fatal = ()

LOAD_TIMEOUT = float(os.environ.get("NOAH_CHECK_TIMEOUT", "20"))  # seconds for loading every expert and trying the rules
Problem = dict[str, Any]


def problem(file: str, message: str, line: int = 0, col: int = 0, severity: str = "error", source: str = "check") -> Problem:
    return {"file": file, "line": line, "col": col, "message": message, "severity": severity, "source": source}


def _yaml(name: str, text: str, out: list[Problem]) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        out.append(problem(name, f"{e.problem or e}", (mark.line + 1) if mark else 0, (mark.column + 1) if mark else 0, source="yaml"))
    except yaml.YAMLError as e:
        out.append(problem(name, str(e), source="yaml"))
    return None


def _python(name: str, text: str, out: list[Problem]) -> None:
    try:
        tree = ast.parse(text, filename=name)
    except SyntaxError as e:
        out.append(problem(name, e.msg, e.lineno or 0, e.offset or 0, source="python"))
        return
    if _pyflakes is None:
        return
    for m in _pyflakes.Checker(tree, filename=name).messages:
        fatal = isinstance(m, _fatal)
        out.append(problem(name, m.message % m.message_args, m.lineno, getattr(m, "col", 0) + 1,
                           "error" if fatal else "warning", "pyflakes"))


def _str_list(m: dict[str, Any], key: str) -> bool:
    v = m.get(key)
    return isinstance(v, list) and all(isinstance(x, str) for x in cast(list[Any], v))


def _manifest(m: Any, folder: str, files: dict[str, str], out: list[Problem]) -> None:
    """What the loader and the engines rely on. A wrong manifest otherwise only shows up as a crash when the server starts."""
    f = "expert.yaml"
    if not isinstance(m, dict):
        out.append(problem(f, "expert.yaml must be a mapping (key: value lines)."))
        return
    m = cast(dict[str, Any], m)
    if m.get("name") != folder:
        out.append(problem(f, f"name must be '{folder}', the expert's folder name."))
    if not isinstance(m.get("description"), str) or not m["description"].strip():
        out.append(problem(f, "description is required: one sentence on what this expert does."))
    if not _str_list(m, "examples") or not m["examples"]:
        out.append(problem(f, "examples must be a list of example messages (at least one)."))
    kind = m.get("kind", "tool")
    if kind not in ("tool", "generate"):
        out.append(problem(f, "kind must be 'tool' or 'generate'."))
    model = m.get("model", {})
    if not isinstance(model, dict):
        out.append(problem(f, "model must be a mapping, e.g. {backend: rule}."))
        model = {}
    params = m.get("parameters", {"type": "object"})
    if not isinstance(params, dict) or cast(dict[str, Any], params).get("type", "object") != "object":
        out.append(problem(f, "parameters must be a JSON schema object: {type: object, properties: {...}}."))
    if kind == "generate":
        if not cast(dict[str, Any], model).get("id"):
            out.append(problem(f, "a generate expert needs model.id (the model it uses)."))
        if not isinstance(m.get("system"), str) or not m["system"].strip():
            out.append(problem(f, "a generate expert needs a system prompt (system:)."))
    if kind == "tool":
        tool = m.get("tool", "tool.py")
        if not isinstance(tool, str) or not tool.endswith(".py") or "/" in tool or "\\" in tool:
            out.append(problem(f, "tool must name a .py file in this folder."))
        elif tool not in files:
            out.append(problem(f, f"this tool expert runs {tool}, but there is no such file."))
    shots = m.get("shots", [])
    if not isinstance(shots, list) or not all(isinstance(s, dict) and isinstance(cast(dict[str, Any], s).get("user"), str)
                                              and isinstance(cast(dict[str, Any], s).get("assistant"), str) for s in cast(list[Any], shots)):
        out.append(problem(f, "shots must be a list of {user: ..., assistant: ...} pairs."))
    if "strip" in m and not _str_list(m, "strip"):
        out.append(problem(f, "strip must be a list of regular expressions."))
    for p in cast(list[str], m["strip"]) if _str_list(m, "strip") else []:
        try:
            re.compile(p)
        except re.error as e:
            out.append(problem(f, f"strip: '{p}' is not a valid regular expression ({e})."))


def static(files: dict[str, str], folder: str) -> list[Problem]:
    out: list[Problem] = []
    if "expert.yaml" not in files:
        out.append(problem("expert.yaml", "Every expert needs an expert.yaml."))
    for name, text in sorted(files.items()):
        if name.endswith((".yaml", ".yml")):
            data = _yaml(name, text, out)
            if name == "expert.yaml" and not any(p["file"] == name for p in out):
                _manifest(data, folder, files, out)
        elif name.endswith(".json"):
            try:
                json.loads(text)
            except json.JSONDecodeError as e:
                out.append(problem(name, e.msg, e.lineno, e.colno, source="json"))
        elif name.endswith(".py"):
            _python(name, text, out)
    return out


# ---- loading, in the child process ----
def _frames(tb: Any) -> list[tuple[Path, int]]:
    return [(Path(f.filename).resolve(), f.lineno or 0) for f in traceback.extract_tb(tb)]


def _where(tb: Any, folder: Path) -> tuple[str, int]:
    """The deepest traceback frame inside the expert's folder: the line to point the editor at."""
    for path, line in reversed(_frames(tb)):
        if path.parent == folder:
            return path.name, line
    return "", 0


def _child(root: Path, name: str) -> list[Problem]:
    from noah.registry import load_experts

    root = root.resolve()
    folder = root / name
    manifest = cast(dict[str, Any], yaml.safe_load((folder / "expert.yaml").read_text(encoding="utf-8")))
    tool = str(manifest.get("tool", "tool.py"))
    errors: dict[str, BaseException] = {}
    experts = load_experts(root, errors)
    # Another expert that does not load was broken before this draft (the server leaves it out): say so, don't block.
    out: list[Problem] = [problem("expert.yaml", f"another expert, '{other}', does not load ({type(e).__name__}: {e}). Open it to fix it.",
                                  severity="warning", source="load") for other, e in sorted(errors.items()) if other != name]
    if name in errors:
        e = errors[name]
        file, line = _where(e.__traceback__, folder)
        return [problem(file or "expert.yaml", f"loading failed: {type(e).__name__}: {e}", line, source="load"), *out]
    ex = experts.get(name)
    if ex is None:
        return [problem("expert.yaml", "the expert did not load (is its name the folder name?)", source="load"), *out]
    if ex.kind != "tool":
        return out
    if not callable(ex.run):
        out.append(problem(tool, "a tool expert needs a run(args) function.", source="load"))
    if ex.extract_rule is not None:
        required = cast(list[str], ex.parameters.get("required", []))
        for example in ex.examples:
            try:
                args: Any = ex.extract_rule(example)
            except Exception as e:
                file, line = _where(e.__traceback__, folder)
                out.append(problem(file or tool, f"extract_rule({example!r}) raised {type(e).__name__}: {e}", line, source="rule"))
                continue
            if not isinstance(args, dict):
                out.append(problem(tool, f"extract_rule({example!r}) must return a dict, not {type(args).__name__}.", source="rule"))
            elif missing := [k for k in required if k not in args]:
                out.append(problem(tool, f"extract_rule({example!r}) leaves out {', '.join(missing)} (the LLM extractor can still fill it).",
                                   severity="warning", source="rule"))
    return out


def main(argv: list[str]) -> None:
    root, name, dest = Path(argv[0]), argv[1], Path(argv[2])
    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # a print() in a tool must not end up in our answer
    try:
        result = _child(root, name)
    finally:
        sys.stdout = real_stdout
    dest.write_text(json.dumps(result), encoding="utf-8")


def load(root: Path, name: str, cwd: Path | None = None) -> list[Problem]:
    """Run `_child` in a fresh interpreter. A hang, a crash or os._exit in the draft only takes that process down."""
    with tempfile.TemporaryDirectory(prefix="noah-check-") as tmp:
        dest = Path(tmp) / "result.json"
        args = [str(root), name, str(dest)]
        cmd = [sys.executable, "--check-experts", *args] if getattr(sys, "frozen", False) else [sys.executable, "-m", "noah.expert_check", *args]
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}  # no __pycache__ next to the draft copy
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=LOAD_TIMEOUT, cwd=cwd, env=env)
        except subprocess.TimeoutExpired:
            return [problem("expert.yaml", f"loading took longer than {LOAD_TIMEOUT:g} s. Does a tool do slow work (network, big files) when it is imported?", source="load")]
        if not dest.is_file():
            tail = (proc.stderr or "").strip().splitlines()[-3:]
            return [problem("expert.yaml", "loading crashed" + (": " + " / ".join(tail) if tail else f" (exit code {proc.returncode})"), source="load")]
        return cast(list[Problem], json.loads(dest.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main(sys.argv[1:])
