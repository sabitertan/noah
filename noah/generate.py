from __future__ import annotations

from typing import Any
import re

from .llm import chat
from .types import Expert


def generate(base_url: str, model: str, system: str, message: str, timeout: float = 180.0,
             max_tokens: int = 700, shots: list[dict[str, Any]] | None = None) -> str:
    """Ask a small model behind an OpenAI-compatible server to answer directly."""
    body = {"model": model, "temperature": 0.2, "max_tokens": max_tokens, "messages": [
        *([{"role": "system", "content": system}] if system else []),
        *[m for sh in (shots or []) for m in ({"role": "user", "content": sh["user"]},
                                              {"role": "assistant", "content": sh["assistant"]})],
        {"role": "user", "content": message}]}
    text = chat(base_url, body, timeout)["choices"][0]["message"]["content"] or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def answer(expert: Expert, message: str, llm_url: str, timeout: float = 180.0) -> str:
    """A generate expert's reply: its own model, system prompt and shots, then its output filters. Used for real requests and for "Try it"."""
    out = generate(llm_url, expert.model["id"], expert.system, message, timeout=timeout, shots=expert.shots)
    for pattern in expert.strip:
        out = re.sub(pattern, "", out, flags=re.S | re.I)
    return re.sub(r"\n{3,}", "\n\n", out).strip()
