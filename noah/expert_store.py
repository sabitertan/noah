"""Create, edit and delete experts from the UI by writing `experts/<name>/expert.yaml`.

What the UI may change is deliberately limited. A *generate* expert is pure data (description, examples, a model and a prompt),
so it can be fully created, edited and deleted. A *tool* expert runs Python (`tool.py`); a browser must never be able to write
code the server then executes, so for those only the routing text (description and examples) is editable here. Code stays in files.
"""
from __future__ import annotations

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
    m = _load(path)
    kind = m.get("kind", "tool")
    return {"name": name, "kind": kind, "description": m.get("description", ""), "examples": m.get("examples", []),
            "model": cast(dict[str, Any], m.get("model") or {}).get("id", ""), "system": m.get("system", ""), "shots": m.get("shots", []),
            "strip": m.get("strip", []), "license": m.get("license", ""),
            "parameters": m.get("parameters", {}), "editable": "all" if kind == "generate" else "routing"}


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


def update(root: Path, name: str, body: ExpertIn) -> None:
    path = _manifest_path(root, name)
    if not path.is_file():
        raise ExpertError(f"Unknown expert '{name}'", 404)
    if body.name and body.name != name:
        raise ExpertError("An expert's name cannot be changed. Create a new one and delete this one instead.")
    data = _load(path)
    data["description"], data["examples"] = body.description, body.examples        # routing text: allowed for every kind
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
    if _load(path).get("kind", "tool") != "generate":
        raise ExpertError("This expert runs Python code. Delete its folder yourself if you really want it gone.")
    path.unlink()
    try:
        path.parent.rmdir()          # only if nothing else is in the folder: never remove files we did not write
    except OSError:
        pass
