from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from .types import Expert


def _load_one(manifest_path: Path) -> Expert:
    manifest = yaml.safe_load(manifest_path.read_text())
    kind = manifest.get("kind", "tool")
    module = None
    if kind == "tool":
        tool_path = manifest_path.parent / manifest.get("tool", "tool.py")
        spec = importlib.util.spec_from_file_location(f"noah_expert_{manifest['name']}", tool_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {tool_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return Expert(
        name=manifest["name"],
        description=manifest["description"],
        examples=manifest.get("examples", []),
        parameters=manifest.get("parameters", {"type": "object", "properties": {}}),
        model=manifest.get("model", {"backend": "rule"}),
        license=manifest.get("license", "unknown"),
        run=getattr(module, "run", None),
        extract_rule=getattr(module, "extract_rule", None),
        kind=kind,
        system=manifest.get("system", ""),
        shots=manifest.get("shots", []),
        strip=manifest.get("strip", []),
    )


def load_experts(root: str | Path, errors: dict[str, BaseException] | None = None) -> dict[str, Expert]:
    """Load every `<root>/<name>/expert.yaml` + `tool.py` pair.

    Without `errors`, one broken expert stops the load. With it, a broken expert is left out and its exception is kept
    under its folder name, so one bad file cannot take every other expert (or the server) down with it."""
    experts: dict[str, Expert] = {}
    for manifest_path in sorted(Path(root).glob("*/expert.yaml")):
        if errors is None:
            ex = _load_one(manifest_path)
        else:
            try:
                ex = _load_one(manifest_path)
            except (Exception, SystemExit) as e:  # SystemExit: a tool that calls sys.exit() at import time
                errors[manifest_path.parent.name] = e
                continue
        experts[ex.name] = ex
    return experts
