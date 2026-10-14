from __future__ import annotations

import json
from typing import Any

from ..llm import LLMError, chat, is_hosted
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

    def _body(self, mode: str, message: str, expert: Expert) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "temperature": 0}
        if mode == "tools":
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
        return body

    def _try(self, mode: str, message: str, expert: Expert) -> dict[str, Any]:
        msg = chat(self.base_url, self._body(mode, message, expert), self.timeout)["choices"][0]["message"]
        if mode == "tools":
            call = msg["tool_calls"][0]["function"]
            args = json.loads(call["arguments"]) if isinstance(call["arguments"], str) else call["arguments"]
        else:
            args = json.loads(msg["content"])
        validate_args(args, expert.parameters)
        return args

    def extract(self, message: str, expert: Expert) -> dict[str, Any]:
        # Hosted models vary in what a provider supports (structured output vs tool calls), so try the other shape too.
        modes = [self.mode, *(["tools" if self.mode == "schema" else "schema"] if is_hosted(self.model) else [])]
        for mode in modes:
            try:
                return self._try(mode, message, expert)
            except LLMError as e:
                if e.fatal:
                    raise  # a bad token must be seen, not hidden behind the rule fallback
            except Exception:
                pass
        if self.fallback is None:
            raise RuntimeError(f"{self.model} returned no usable arguments")
        return self.fallback.extract(message, expert)
