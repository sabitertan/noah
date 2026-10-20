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
    kind: str = "tool"  # "tool": extract args then call tool.py; "generate": the expert's own model answers; "mcp": an MCP server's tools
    system: str = ""  # system prompt for generate experts
    strip: list[str] = field(default_factory=list[str])  # regexes removed from generated output (hard domain guard)
    shots: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])  # few-shot user/assistant pairs that pin the output style
    server: dict[str, Any] = field(default_factory=dict[str, Any])  # mcp: how to reach the server ({url, headers} or {command, args, env})
    tools: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])  # mcp: the server's tools (tools.json), each with examples
    defaults: dict[str, Any] = field(default_factory=dict[str, Any])  # mcp: argument values to use when a message gives none (path: D:/notes)

    @property
    def routing_texts(self) -> list[str]:
        """What a message is compared with. An MCP expert also brings every tool's description and examples, so a message
        that fits one of its tools reaches the expert even though its own description is general."""
        return [self.description, *self.examples, *(t for tool in self.tools for t in [tool.get("description") or tool["name"], *tool.get("examples", [])])]

    @property
    def card_text(self) -> str:
        return " ".join([self.name, *self.routing_texts, *(t["name"].replace("_", " ") for t in self.tools)])


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
    confirm: dict[str, Any] | None = None  # mcp: a tool that may change something, waiting for the user's Run ({expert, tool, arguments})
