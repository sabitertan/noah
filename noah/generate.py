from __future__ import annotations

import re

from .llm import chat


def generate(base_url: str, model: str, system: str, message: str, timeout: float = 180.0,
             max_tokens: int = 700, shots: list[dict] | None = None) -> str:
    """Ask a small model behind an OpenAI-compatible server to answer directly."""
    body = {"model": model, "temperature": 0.2, "max_tokens": max_tokens, "messages": [
        *([{"role": "system", "content": system}] if system else []),
        *[m for sh in (shots or []) for m in ({"role": "user", "content": sh["user"]},
                                              {"role": "assistant", "content": sh["assistant"]})],
        {"role": "user", "content": message}]}
    text = chat(base_url, body, timeout)["choices"][0]["message"]["content"] or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
