from __future__ import annotations

import json
from typing import Any

import httpx

from ..types import Expert
from . import validate_args
from .rule import RuleExtractor


class OpenAICompatExtractor:
    """Calls a tiny model behind any OpenAI-compatible server (llama.cpp, vLLM, Ollama).

    The model sees one tool (the expert's schema) and must call it. Invalid output falls
    back to the rule extractor so a weak model never breaks a request.
    """

    name = "openai_compat"

    def __init__(self, base_url: str = "http://localhost:11434/v1", model: str = "functiongemma", timeout: float = 30.0,
                 fallback: bool = True, mode: str = "schema"):
        # mode "schema": server-side constrained JSON (works with any model, needs no tool support)
        # mode "tools":  native tool calling (needs a model/server with a tool template)
        assert mode in ("schema", "tools")
        self.mode = mode
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.fallback = RuleExtractor() if fallback else None

    def extract(self, message: str, expert: Expert) -> dict[str, Any]:
        body = {"model": self.model, "temperature": 0}
        if self.mode == "tools":
            body["messages"] = [{"role": "user", "content": message}]
            body["tools"] = [{"type": "function", "function": {
                "name": expert.name, "description": expert.description, "parameters": expert.parameters}}]
            body["tool_choice"] = {"type": "function", "function": {"name": expert.name}}
        else:
            props = ", ".join(f"{k} ({v.get('description', v.get('type'))})"
                              for k, v in expert.parameters.get("properties", {}).items())
            body["messages"] = [
                {"role": "system", "content": f"Extract the arguments for the '{expert.name}' tool "
                 f"({expert.description}) from the user's message. Fields: {props}. Reply with JSON only. /no_think"},
                {"role": "user", "content": message}]
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": expert.name, "schema": expert.parameters, "strict": True}}
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout)
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            if self.mode == "tools":
                call = msg["tool_calls"][0]["function"]
                args = json.loads(call["arguments"]) if isinstance(call["arguments"], str) else call["arguments"]
            else:
                args = json.loads(msg["content"])
            validate_args(args, expert.parameters)
            return args
        except Exception:
            if self.fallback is None:
                raise
            return self.fallback.extract(message, expert)
