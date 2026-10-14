from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Expert:
    """A loaded expert: manifest data plus its tool entrypoint."""

    name: str
    description: str
    examples: list[str]
    parameters: dict[str, Any]
    model: dict[str, Any]
    license: str
    run: Callable[[dict[str, Any]], str] | None = None
    extract_rule: Callable[[str], dict[str, Any]] | None = None
    kind: str = "tool"  # "tool": extract args then call tool.py; "generate": the expert's own model answers
    system: str = ""  # system prompt for generate experts
    strip: list[str] = field(default_factory=list[str])  # regexes removed from generated output (hard domain guard)
    shots: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])  # few-shot user/assistant pairs that pin the output style

    @property
    def card_text(self) -> str:
        return " ".join([self.name, self.description, *self.examples])


@dataclass
class Decision:
    expert: str
    score: float
    args: dict[str, Any] | None = None  # set when an LLM orchestrator already chose the arguments


@dataclass
class Routed:
    """Message sent from the router node to expert nodes."""

    message: str
    decisions: list[Decision]
    selected: list[str] = field(default_factory=list[str])


@dataclass
class ExpertResult:
    expert: str
    args: dict[str, Any]
    output: str
    extractor: str
    latency_ms: float
    error: str | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])  # e.g. files the user can open or download
