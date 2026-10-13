"""OpenRouter support: use any model OpenRouter serves through its API with your key. Nothing is downloaded.

Talks only to the fixed OpenRouter host. The key is write-only: the API says whether one is set, never what it is.
"""
from __future__ import annotations

import time

import httpx
from fastapi import HTTPException

from . import hosted, llm

_cache: tuple[float, list[dict]] | None = None


def _check_key(key: str) -> str:
    try:
        r = httpx.get(f"{llm.OR_BASE}/key", headers={"Authorization": f"Bearer {key}"}, timeout=10)
    except httpx.HTTPError:
        raise HTTPException(502, "Could not reach OpenRouter to check the key.")
    if r.status_code != 200:
        raise HTTPException(400, "OpenRouter rejected this API key.")
    return (r.json().get("data") or {}).get("label", "") or "your key"


_hosted = hosted.make(llm.OR, _check_key)
router, my_models, status = _hosted.router, _hosted.my_models, _hosted.status


def _per_million(price) -> float | None:
    try:
        v = float(price) * 1_000_000
    except (TypeError, ValueError):
        return None
    return round(v, 4) if v >= 0 else None  # a negative price means "decided per request" (routers)


def _summary(m: dict) -> dict:
    params = set(m.get("supported_parameters") or [])
    price = m.get("pricing") or {}
    free = m["id"].endswith(":free") or (price.get("prompt") == "0" and price.get("completion") == "0")
    return {"id": m["id"], "name": m.get("name", ""), "tools": "tools" in params,
            "structured": bool(params & {"structured_outputs", "response_format"}), "logprobs": "logprobs" in params,
            "free": free, "price_in": None if free else _per_million(price.get("prompt")),
            "context": m.get("context_length") or 0, "hf_id": m.get("hugging_face_id") or ""}


def _models() -> list[dict]:
    global _cache
    if _cache and time.time() - _cache[0] < 600:
        return _cache[1]
    r = httpx.get(f"{llm.OR_BASE}/models", timeout=20)  # public: no key needed to browse
    r.raise_for_status()
    out = [_summary(m) for m in r.json().get("data", [])
           if "text" in (m.get("architecture") or {}).get("output_modalities", ["text"]) and llm.OR_ID.match(m.get("id", ""))]
    _cache = (time.time(), out)
    return out


@router.get("/search")
def search(q: str = ""):
    terms = q.lower().split()
    try:
        found = [m for m in _models() if all(t in (m["id"] + " " + m["name"]).lower() for t in terms)]
    except (httpx.HTTPError, ValueError, KeyError):
        return {"hosted": [], "error": "Could not reach the OpenRouter model list."}
    return {"hosted": found[:60], "error": ""}
