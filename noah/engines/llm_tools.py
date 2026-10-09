from __future__ import annotations

import json
import re

import httpx

from ..types import Decision, Expert

NONE_TOOL = "none"
_FG_CALL = re.compile(r"call:(\w+)\{(.*?)(?:\}|<end_function_call>|$)", re.S)  # closing brace may be missing
_FG_PARAM = re.compile(r"(\w+):(?:<escape>(.*?)<escape>|([^,}]*))", re.S)


def expert_tools(experts: dict[str, Expert]) -> list[dict]:
    """Experts as OpenAI-style tools, plus an explicit 'none' so the model can decline."""
    tools = []
    for e in experts.values():
        params = e.parameters if e.parameters.get("properties") else {
            "type": "object", "properties": {"request": {"type": "string", "description": "the user's request"}},
            "required": ["request"]}
        tools.append({"type": "function", "function": {"name": e.name, "description": e.description, "parameters": params}})
    tools.append({"type": "function", "function": {
        "name": NONE_TOOL, "description": "Call this when none of the other tools fits the request.",
        "parameters": {"type": "object", "properties": {}}}})
    return tools


def parse_functiongemma(text: str) -> list[tuple[str, dict]]:
    """FunctionGemma emits `call:name{key:<escape>value<escape>,n:3}` as plain text."""
    calls = []
    for name, body in _FG_CALL.findall(text or ""):
        args = {}
        for key, escaped, bare in _FG_PARAM.findall(body):
            val = escaped if escaped or not bare.strip() else bare.strip()
            try:
                args[key] = json.loads(val) if not escaped and val and val[0] in '-0123456789[{"tf' else val
            except ValueError:
                args[key] = val
        calls.append((name, args))
    return calls


class LLMToolsEngine:
    """Decision engine + argument extractor in one: a tool-calling LLM picks the expert(s) itself.

    Works with native tool calls (Nemotron-Orchestrator, Qwen, Llama ...) and with FunctionGemma's text format.
    """

    def __init__(self, base_url: str = "http://localhost:11434/v1", model: str = "", timeout: float = 300.0):
        self.base_url, self.model, self.timeout = base_url.rstrip("/"), model, timeout
        self.name = "llm_tools"

    def _calls(self, message: str, experts: dict[str, Expert]) -> list[tuple[str, dict]]:
        r = httpx.post(f"{self.base_url}/chat/completions", timeout=self.timeout, json={
            "model": self.model, "temperature": 0, "tools": expert_tools(experts),
            "messages": [{"role": "user", "content": message}]})
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        native = [(c["function"]["name"], c["function"]["arguments"]) for c in msg.get("tool_calls") or []]
        if native:
            return [(n, json.loads(a) if isinstance(a, str) else (a or {})) for n, a in native]
        return parse_functiongemma(msg.get("content") or "")

    def decide(self, message: str, experts: dict[str, Expert]) -> list[Decision]:
        chosen: dict[str, Decision] = {}
        for rank, (name, args) in enumerate(self._calls(message, experts)):
            if name in experts and name not in chosen:
                chosen[name] = Decision(name, round(1.0 - rank * 0.01, 2), args or None)
        rest = [Decision(n, 0.0) for n in experts if n not in chosen]
        return [*chosen.values(), *rest]
