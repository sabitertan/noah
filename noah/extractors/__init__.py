from __future__ import annotations

from typing import Any, Protocol

from ..types import Expert


class ArgExtractor(Protocol):
    """Slot 3: turns a message into tool arguments for one expert."""

    name: str

    def extract(self, message: str, expert: Expert) -> dict[str, Any]: ...


def validate_args(args: dict[str, Any], schema: dict[str, Any]) -> None:
    """Minimal JSON-schema check: required keys and primitive types."""
    types = {"string": str, "number": (int, float), "integer": int, "boolean": bool}
    for key in schema.get("required", []):
        if key not in args:
            raise ValueError(f"missing required argument: {key}")
    for key, prop in schema.get("properties", {}).items():
        if key in args and prop.get("type") in types and not isinstance(args[key], types[prop["type"]]):
            raise ValueError(f"argument {key} should be {prop['type']}")


def make_extractor(name: str, **kwargs) -> ArgExtractor:
    from .openai_compat import OpenAICompatExtractor
    from .rule import RuleExtractor

    if name == "rule":
        return RuleExtractor()
    if name == "openai_compat":
        return OpenAICompatExtractor(**kwargs)
    raise ValueError(f"unknown extractor: {name}")
