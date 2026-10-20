"""Create, edit and delete experts from the UI by writing `experts/<name>/expert.yaml`.

What the UI may change is deliberately limited. A *generate* expert is pure data (description, examples, a model and a prompt),
so it can be fully created, edited and deleted. A *tool* expert runs Python (`tool.py`); a browser must never be able to write
code the server then executes, so for those only the routing text (description and examples) is editable here. Code stays in files.
An *mcp* expert is an MCP server (`server:` in expert.yaml, its tools in tools.json). Adding one may start a program, so the
server routes check for permission first (like code); here its routing text and the model that picks its tools can change.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, Field, field_validator

NAME = re.compile(r"^[a-z][a-z0-9_]{1,31}$")   # a folder name and the tool name an LLM orchestrator calls
RESERVED = {"none", "new"}                      # "none" is the orchestrator's decline tool; "new" is the UI route for a fresh expert


class ExpertError(ValueError):
    """The request is not allowed or not valid; the message is shown to the user."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Shot(BaseModel):
    user: str = Field(min_length=1, max_length=2000)
    assistant: str = Field(min_length=1, max_length=4000)


class ExpertIn(BaseModel):
    name: str = ""                                   # create only; a name never changes afterwards
    description: str = Field(min_length=1, max_length=500)
    examples: list[str] = Field(min_length=1, max_length=20)
    model: str = Field(default="", max_length=200)   # generate experts: a model reference (local name, hf:…, or:…, az:…)
    choose_tool: bool = False                         # mcp experts: the model chooses among the closest tools (else the closest wins)
    defaults: dict[str, str] = Field(default_factory=dict[str, str], max_length=20)   # mcp experts: argument values when a message gives none
    system: str = Field(default="", max_length=4000)
    shots: list[Shot] = Field(default_factory=list[Shot], max_length=6)
    strip: list[str] = Field(default_factory=list[str], max_length=10)
    license: str = Field(default="user", max_length=60)

    @field_validator("description", "license", "model", "system")
    @classmethod
    def _trim(cls, v: str) -> str:
        return v.strip()

    @field_validator("examples")
    @classmethod
    def _examples(cls, v: list[str]) -> list[str]:
        out = [e.strip() for e in v if e.strip()]
        if not out:
            raise ValueError("add at least one example message")
        if any(len(e) > 200 for e in out):
            raise ValueError("each example must be 200 characters or fewer")
        return out

    @field_validator("strip")
    @classmethod
    def _strip(cls, v: list[str]) -> list[str]:
        out = [p for p in (x.strip() for x in v) if p]
        for p in out:
            if len(p) > 200:
                raise ValueError("a pattern is longer than 200 characters")
            try:
                re.compile(p)
            except re.error as e:
                raise ValueError(f"'{p}' is not a valid regular expression ({e})")
        return out


def _dir(root: Path, name: str) -> Path:
    if not NAME.match(name) or name in RESERVED:
        raise ExpertError("Use a name of 2-32 lowercase letters, digits or underscores, starting with a letter (not 'none' or 'new').")
    path = (root / name).resolve()
    if path.parent != root.resolve():
        raise ExpertError("Invalid expert name.")
    return path


def _manifest_path(root: Path, name: str) -> Path:
    return _dir(root, name) / "expert.yaml"


def _literal(dumper: yaml.SafeDumper, text: str) -> Any:
    return cast(Any, dumper).represent_scalar("tag:yaml.org,2002:str", text, style="|" if "\n" in text else None)  # multi-line prompts stay readable


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(str, _literal)


def _write(path: Path, data: dict[str, Any]) -> None:
    """Atomic: a crash mid-write must not leave a half-written manifest that stops every expert from loading."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".expert-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _load(path: Path) -> dict[str, Any]:
    data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def read(root: Path, name: str) -> dict[str, Any]:
    """The manifest as the editor needs it, plus what may be changed."""
    path = _manifest_path(root, name)
    if not path.is_file():
        raise ExpertError(f"Unknown expert '{name}'", 404)
    try:
        m = _load(path)
    except yaml.YAMLError as e:
        raise ExpertError(f"expert.yaml is not valid YAML ({e.__class__.__name__}). Fix it in the Files tab.", 422)
    kind = m.get("kind", "tool")
    extra: dict[str, Any] = {}
    if kind == "mcp":
        from .mcp_client import describe_server
        tools_path = path.parent / "tools.json"
        try:
            tools = json.loads(tools_path.read_text(encoding="utf-8")) if tools_path.is_file() else []
        except ValueError:
            tools = []
        extra = {"server": describe_server(m.get("server") or {}), "tools": [
            {"name": t.get("name", ""), "description": t.get("description", ""), "examples": t.get("examples", []),
             "read_only": bool(t.get("annotations", {}).get("read_only_hint"))} for t in tools]}
    editable = {"generate": "all", "mcp": "mcp"}.get(kind, "routing")
    return {**extra, "name": name, "kind": kind, "description": m.get("description", ""), "examples": m.get("examples", []),
            "model": cast(dict[str, Any], m.get("model") or {}).get("id", ""), "choose_tool": bool(cast(dict[str, Any], m.get("model") or {}).get("choose_tool")), "defaults": m.get("defaults") or {}, "system": m.get("system", ""), "shots": m.get("shots", []),
            "strip": m.get("strip", []), "license": m.get("license", ""),
            "parameters": m.get("parameters", {}), "editable": editable}


def create(root: Path, body: ExpertIn) -> str:
    """A new generate expert. (Tool experts need code, so they are only ever added as files.)"""
    path = _manifest_path(root, body.name)
    if path.exists():
        raise ExpertError(f"An expert named '{body.name}' already exists.", 409)
    if not body.model:
        raise ExpertError("Choose the model this expert uses.")
    if not body.system:
        raise ExpertError("Write a system prompt: it tells the model what this expert is for.")
    data: dict[str, Any] = {"name": body.name, "kind": "generate", "description": body.description, "examples": body.examples,
                            "model": {"backend": "openai_compat", "id": body.model}, "system": body.system}
    if body.shots:
        data["shots"] = [s.model_dump() for s in body.shots]
    if body.strip:
        data["strip"] = body.strip
    data["license"] = body.license or "user"
    _write(path, data)
    return body.name


def create_mcp(root: Path, data: dict[str, Any], tools: list[dict[str, Any]]) -> str:
    """A new MCP expert: expert.yaml (from mcp_expert.manifest) and tools.json. The caller has checked permission."""
    path = _manifest_path(root, data["name"])
    if path.exists():
        raise ExpertError(f"An expert named '{data['name']}' already exists.", 409)
    write_tools(root, data["name"], tools)
    _write(path, data)
    return data["name"]


def set_server(root: Path, name: str, server: dict[str, Any]) -> None:
    """Noah's own change to an MCP expert's server entry (e.g. marking that it signs in with OAuth)."""
    path = _manifest_path(root, name)
    data = _load(path)
    data["server"] = server
    _write(path, data)


def set_routing(root: Path, name: str, description: str, examples: list[str]) -> None:
    """Rewrite routing text Noah wrote itself (auto_routing: true), e.g. after the tools were listed. The user's is left alone."""
    path = _manifest_path(root, name)
    data = _load(path)
    if data.get("auto_routing"):
        data["description"], data["examples"] = description, examples
        _write(path, data)


def write_tools(root: Path, name: str, tools: list[dict[str, Any]]) -> None:
    folder = _dir(root, name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tools.json").write_text(json.dumps(tools, indent=1, ensure_ascii=False), encoding="utf-8")


def update(root: Path, name: str, body: ExpertIn) -> None:
    path = _manifest_path(root, name)
    if not path.is_file():
        raise ExpertError(f"Unknown expert '{name}'", 404)
    if body.name and body.name != name:
        raise ExpertError("An expert's name cannot be changed. Create a new one and delete this one instead.")
    data = _load(path)
    data["description"], data["examples"] = body.description, body.examples        # routing text: allowed for every kind
    data.pop("auto_routing", None)                                                    # the user's own words now: keep them
    if data.get("kind") == "mcp":   # the model that picks a tool; none: the orchestrator's tiny model
        data["model"] = {"backend": "openai_compat", "id": body.model} if body.model else {"backend": "orchestrator"}
        if body.choose_tool:
            data["model"]["choose_tool"] = True
        defaults = {k.strip(): v.strip() for k, v in body.defaults.items() if k.strip() and v.strip()}
        if any(len(k) > 64 or len(v) > 500 for k, v in defaults.items()):
            raise ExpertError("A default is too long (names up to 64 characters, values up to 500).")
        if defaults:
            data["defaults"] = defaults
        else:
            data.pop("defaults", None)
    if data.get("kind", "tool") == "generate":
        if not body.model or not body.system:
            raise ExpertError("A generate expert needs a model and a system prompt.")
        data["model"] = {**(data.get("model") or {"backend": "openai_compat"}), "id": body.model}
        data["system"], data["license"] = body.system, body.license or data.get("license", "user")
        for key, value in (("shots", [s.model_dump() for s in body.shots]), ("strip", body.strip)):
            if value:
                data[key] = value
            else:
                data.pop(key, None)
    _write(path, data)


def delete(root: Path, name: str) -> None:
    path = _manifest_path(root, name)
    if not path.is_file():
        raise ExpertError(f"Unknown expert '{name}'", 404)
    kind = _load(path).get("kind", "tool")
    if kind not in ("generate", "mcp"):
        raise ExpertError("This expert runs Python code. Delete its folder yourself if you really want it gone.")
    if kind == "mcp":
        (path.parent / "tools.json").unlink(missing_ok=True)
    path.unlink()
    try:
        path.parent.rmdir()          # only if nothing else is in the folder: never remove files we did not write
    except OSError:
        pass
