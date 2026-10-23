"""Claude support: use Anthropic's models through the Anthropic API with your own API key. Nothing is downloaded.

Calls go through the official SDK (anthropic_api.py). The key is write-only: the API says whether one is set, never what it is.
A Claude.ai subscription (Pro, Max) is not an API key and cannot be used here: API use is billed per token at console.anthropic.com.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from . import anthropic_api, hosted, llm

# Shown before a key is set, so people know what to pick. With a key, the live list from the Models API replaces it.
SUGGESTED = [("claude-opus-5-5", "Claude Opus 5.5: the most capable for planning and building apps"),
             ("claude-sonnet-5-5", "Claude Sonnet 5.5: fast and strong at code"),
             ("claude-haiku-5-5", "Claude Haiku 5.5: the fastest and cheapest")]


def _check_key(body: hosted.TokenBody) -> str:
    try:
        anthropic_api.models(body.token)
    except llm.LLMError as e:
        raise HTTPException(400 if e.status in (401, 403) else 502, str(e))
    return "your Anthropic key"


def _status() -> dict[str, Any]:
    secret, source = llm.token(llm.AN)
    return {"configured": bool(secret), "source": source, "available": anthropic_api.available()}


_hosted = hosted.make(llm.AN, _check_key, status_fn=_status)
router, my_models, status = _hosted.router, _hosted.my_models, _hosted.status


def _row(mid: str, name: str) -> dict[str, Any]:
    return {"id": mid, "name": name, "tools": True, "structured": True, "logprobs": False, "free": False, "price_in": None, "context": 0}


@router.get("/search")
def search(q: str = "") -> dict[str, Any]:
    if not anthropic_api.available():
        return {"hosted": [], "error": "Claude models need the Anthropic SDK, which the browser build does not include. Use the server or desktop app."}
    key, _ = llm.token(llm.AN)
    error = ""
    rows = [_row(m, n) for m, n in SUGGESTED]
    if key:
        try:
            rows = [_row(m["id"], m["name"]) for m in anthropic_api.models(key)]
        except llm.LLMError as e:
            error = str(e)
    terms = q.lower().split()
    return {"hosted": [r for r in rows if all(t in (r["id"] + " " + r["name"]).lower() for t in terms)], "error": error}
