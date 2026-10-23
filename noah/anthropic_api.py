"""Claude through the official Anthropic SDK, behind the same OpenAI-shaped call as every other model (llm.chat).

The rest of Noah builds OpenAI chat-completion bodies; this turns one into a Messages API request and the reply back into a
chat completion. Only what Noah sends is translated: system/user/assistant text, tools, and JSON-schema output. Sampling
settings (temperature, top_p) are dropped because the Messages API no longer takes them, and a forced tool choice becomes
"auto" (current Claude models reject forcing; the prompt and the single tool already steer it). Log-probs don't exist here,
so the llm_choice engine falls back to the answered letter.
"""
from __future__ import annotations

from typing import Any
import copy
import json

from .llm import LLMError

try:
    import anthropic
except ImportError:  # the browser build has no Anthropic SDK
    anthropic = None

MIN_TOKENS = 16000  # thinking is always on for current models and counts against max_tokens: a small cap cuts the answer off
ERRORS = {
    400: "Claude could not run {name} with this request.",
    401: "Anthropic rejected the API key. Check it on the Models page.",
    403: "This API key may not use {name}.",
    404: "{name} is not a Claude model this key can use. Pick one from the Claude tab on the Models page.",
    429: "Anthropic is rate limiting this key. Wait a moment and retry.",
}


def available() -> bool:
    return anthropic is not None


def _closed(schema: Any) -> Any:
    """Structured output wants every object closed (additionalProperties: false)."""
    if isinstance(schema, dict):
        out: dict[str, Any] = {k: _closed(v) for k, v in schema.items()}  # pyright: ignore[reportUnknownVariableType]
        if out.get("type") == "object":
            out["additionalProperties"] = False
        return out
    if isinstance(schema, list):
        return [_closed(v) for v in schema]  # pyright: ignore[reportUnknownVariableType]
    return schema


def request(body: dict[str, Any], model: str) -> dict[str, Any]:
    """The Messages API arguments for an OpenAI-shaped chat body."""
    system = "\n\n".join(m["content"] for m in body["messages"] if m["role"] == "system" and m.get("content"))
    messages = [{"role": m["role"], "content": m.get("content") or ""} for m in body["messages"] if m["role"] in ("user", "assistant")]
    req: dict[str, Any] = {"model": model, "max_tokens": max(int(body.get("max_tokens") or body.get("max_completion_tokens") or 0), MIN_TOKENS),
                           "messages": messages}
    if system:
        req["system"] = system.replace("/no_think", "").strip()  # a hint for Qwen models; meaningless to Claude
    fmt = body.get("response_format") or {}
    if fmt.get("type") == "json_schema":
        req["output_config"] = {"format": {"type": "json_schema", "schema": _closed(copy.deepcopy(fmt["json_schema"]["schema"]))}}
    if body.get("tools"):
        req["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                         "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}} for t in body["tools"]]
        req["tool_choice"] = {"type": "none"} if body.get("tool_choice") == "none" else {"type": "auto"}
    return req


def reply(message: Any) -> dict[str, Any]:
    """A Messages API response as a chat completion: text joined, tool calls with JSON-string arguments."""
    text = "".join(b.text for b in message.content if b.type == "text")
    calls = [{"id": b.id, "type": "function", "function": {"name": b.name, "arguments": json.dumps(b.input)}}
             for b in message.content if b.type == "tool_use"]
    if message.stop_reason == "refusal" and not text:
        raise LLMError("Claude declined this request.", 400)
    msg: dict[str, Any] = {"role": "assistant", "content": text, **({"tool_calls": calls} if calls else {})}
    return {"choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if calls else "stop"}]}


def chat(body: dict[str, Any], model: str, api_key: str, timeout: float) -> dict[str, Any]:
    if anthropic is None:
        raise LLMError("Claude models need the Anthropic SDK (pip install anthropic), which this build does not include.")
    client = anthropic.Anthropic(api_key=api_key, timeout=max(timeout, 120.0), max_retries=2)
    try:
        return reply(client.messages.create(**request(body, model)))
    except anthropic.APIStatusError as e:  # never echo the request: it carries the key
        raise LLMError(ERRORS.get(e.status_code, "Anthropic returned HTTP {status} for {name}.").format(name=model, status=e.status_code),
                       e.status_code) from None
    except anthropic.APIConnectionError:
        raise LLMError("Could not reach the Anthropic API.") from None


def models(api_key: str) -> list[dict[str, Any]]:
    """The Claude models this key can use, newest first. Raises LLMError when the key is rejected."""
    if anthropic is None:
        raise LLMError("This build does not include the Anthropic SDK.")
    try:
        return [{"id": m.id, "name": m.display_name} for m in anthropic.Anthropic(api_key=api_key, timeout=20.0).models.list()]
    except anthropic.APIStatusError as e:
        raise LLMError(ERRORS.get(e.status_code, "Anthropic returned HTTP {status}.").format(name="", status=e.status_code), e.status_code) from None
    except anthropic.APIConnectionError:
        raise LLMError("Could not reach the Anthropic API.") from None
