"""OpenAI support: use OpenAI's models through the OpenAI API with your own API key. Nothing is downloaded.

Talks only to the fixed OpenAI host (its chat-completions endpoint, like every other provider). The key is write-only.
A ChatGPT subscription (Plus, Pro) does not include API access: API use is billed per token at platform.openai.com.
"""
from __future__ import annotations

from typing import Any
import re

import httpx
from fastapi import HTTPException

from . import hosted, llm

CHAT = re.compile(r"^(gpt-|o\d|chatgpt-)")                                  # chat models, not embeddings, audio or images
NOT_CHAT = re.compile(r"(audio|realtime|transcribe|tts|image|search|embedding|instruct)")


def _list(key: str) -> list[str]:
    r = httpx.get(f"{llm.OA_BASE}/models", headers={"Authorization": f"Bearer {key}"}, timeout=15)
    if r.status_code in (401, 403):
        raise HTTPException(400, "OpenAI rejected this API key.")
    r.raise_for_status()
    return sorted((m["id"] for m in r.json().get("data", []) if CHAT.match(m["id"]) and not NOT_CHAT.search(m["id"])), reverse=True)


def _check_key(body: hosted.TokenBody) -> str:
    try:
        _list(body.token)
    except httpx.HTTPError:
        raise HTTPException(502, "Could not reach OpenAI to check the key.")
    return "your OpenAI key"


_hosted = hosted.make(llm.OA, _check_key)
router, my_models, status = _hosted.router, _hosted.my_models, _hosted.status


@router.get("/search")
def search(q: str = "") -> dict[str, Any]:
    key, _ = llm.token(llm.OA)
    if not key:
        return {"hosted": [], "error": "Connect your OpenAI API key to see the models it can use."}
    try:
        ids = _list(key)
    except (httpx.HTTPError, HTTPException):
        return {"hosted": [], "error": "Could not list OpenAI models with this key."}
    terms = q.lower().split()
    return {"hosted": [{"id": m, "name": "", "tools": True, "structured": True, "logprobs": False, "free": False, "price_in": None, "context": 0}
                       for m in ids if all(t in m.lower() for t in terms)][:60], "error": ""}
