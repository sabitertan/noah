from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from .types import Expert


def load_experts(root: str | Path) -> dict[str, Expert]:
    """Load every `<root>/<name>/expert.yaml` + `tool.py` pair."""
    experts: dict[str, Expert] = {}
    for manifest_path in sorted(Path(root).glob("*/expert.yaml")):
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
        experts[manifest["name"]] = Expert(
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
    return experts
