"""Hugging Face support: use a model hosted on Hugging Face (no download) or pull a GGUF repo into local Ollama.

Everything here talks to fixed Hugging Face hosts (or the local Ollama), never to a URL the browser supplies.
The token is write-only: the API says whether one is set, never what it is.
"""
from __future__ import annotations

from typing import Any, Callable, TypeVar
import json
import os
import re
import time

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import hosted, llm


def _whoami(body: hosted.TokenBody) -> str:
    try:
        r = httpx.get(f"{llm.HF_HUB}/api/whoami-v2", headers={"Authorization": f"Bearer {body.token}"}, timeout=10)
    except httpx.HTTPError:
        raise HTTPException(502, "Could not reach Hugging Face to check the token.")
    if r.status_code != 200:
        raise HTTPException(400, "Hugging Face rejected this token.")
    return r.json().get("name", "")


_hosted = hosted.make(llm.HF, _whoami)
router, my_models, status = _hosted.router, _hosted.my_models, _hosted.status
REPO = re.compile(r"^[\w.\-]+/[\w.\-]+$")
QUANT = re.compile(r"^[\w.\-]+$")
QUANT_IN_NAME = re.compile(r"(?<![A-Za-z0-9])((?:UD-)?(?:IQ\d(?:_[A-Z0-9]+)*|Q\d(?:_[A-Z0-9]+)*|BF16|F16|F32))(?![A-Za-z0-9])", re.I)
T = TypeVar("T")
_cache: dict[str, tuple[float, Any]] = {}


def ollama_url() -> str:
    return os.environ.get("NOAH_LLM_URL", "http://localhost:11434")


def _cached(key: str, ttl: float, fetch: Callable[[], T]) -> T:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = fetch()
    _cache[key] = (time.time(), value)
    return value


def _hosted_models() -> list[dict[str, Any]]:
    """Models the Inference Providers router can run, boiled down to what a user needs to choose."""
    def fetch() -> list[dict[str, Any]]:
        r = httpx.get(f"{llm.HF_ROUTER}/models", timeout=15)
        r.raise_for_status()
        out: list[dict[str, Any]] = []
        for m in r.json().get("data", []):
            live = [p for p in m.get("providers", []) if p.get("status") == "live"]
            prices = [p["pricing"]["input"] for p in live if p.get("pricing")]
            out.append({"id": m["id"], "providers": len(live),
                        "tools": any(p.get("supports_tools") for p in live),
                        "structured": any(p.get("supports_structured_output") for p in live),
                        "free": any(p.get("is_free") for p in live),
                        "price_in": min(prices) if prices else None,  # USD per million input tokens
                        "context": max((p.get("context_length") or 0 for p in live), default=0)})
        return sorted(out, key=lambda m: (-m["providers"], m["id"]))
    return _cached("hosted", 600, fetch)


@router.get("/search")
def search(q: str = ""):
    terms = q.lower().split()
    hosted_err = gguf_err = ""
    try:
        hosted = [m for m in _hosted_models() if all(t in m["id"].lower() for t in terms)][:40]
    except (httpx.HTTPError, ValueError, KeyError):
        hosted, hosted_err = [], "Could not reach the Hugging Face model list."
    try:
        params = {"filter": "gguf", "sort": "downloads", "direction": "-1", "limit": "20", **({"search": q.strip()} if q.strip() else {})}
        r = httpx.get(f"{llm.HF_HUB}/api/models", params=params, timeout=15)
        r.raise_for_status()
        gguf = [{"id": m["id"], "downloads": m.get("downloads", 0), "likes": m.get("likes", 0)} for m in r.json() if REPO.match(m.get("id", ""))]
    except (httpx.HTTPError, ValueError, KeyError):
        gguf, gguf_err = [], "Could not search Hugging Face for GGUF downloads."
    return {"hosted": hosted, "gguf": gguf, "error": hosted_err or gguf_err}


@router.get("/quants")
def quants(repo: str):
    """The GGUF quantisations a repo offers, with sizes, so the user sees what a download costs."""
    if not REPO.match(repo) or ".." in repo:
        raise HTTPException(400, "Not a Hugging Face repo id (org/name).")
    try:
        r = httpx.get(f"{llm.HF_HUB}/api/models/{repo}", params={"blobs": "true"}, timeout=15)
        r.raise_for_status()
        files = r.json().get("siblings", [])
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Could not read that repo from Hugging Face (private, gated or missing?).")
    sizes: dict[str, int] = {}
    for f in files:
        name = f.get("rfilename", "")
        if not name.lower().endswith(".gguf") or "mmproj" in name.lower():
            continue
        m = QUANT_IN_NAME.search(name.rsplit("/", 1)[-1])
        if m:
            q = m.group(1).upper()
            sizes[q] = sizes.get(q, 0) + (f.get("size") or 0)  # split files add up
    return {"repo": repo, "quants": [{"quant": q, "size": s} for q, s in sorted(sizes.items(), key=lambda kv: kv[1])]}


class PullBody(BaseModel):
    repo: str
    quant: str = ""


@router.post("/pull")
def pull(body: PullBody):
    """Stream Ollama's progress while it downloads hf.co/<repo>:<quant>. Closing the request cancels the download."""
    if not REPO.match(body.repo) or ".." in body.repo or (body.quant and not QUANT.match(body.quant)):
        raise HTTPException(400, "Invalid repo or quantisation.")
    name = f"hf.co/{body.repo}" + (f":{body.quant}" if body.quant else "")

    def lines():
        try:
            with httpx.stream("POST", f"{ollama_url()}/api/pull", json={"model": name, "stream": True}, timeout=None) as r:
                if r.status_code != 200:
                    yield json.dumps({"error": f"Ollama refused the download (HTTP {r.status_code})."}) + "\n"
                    return
                for line in r.iter_lines():
                    if line:
                        yield line + "\n"
        except httpx.HTTPError:
            yield json.dumps({"error": "Could not reach Ollama. Is it running?"}) + "\n"
    return StreamingResponse(lines(), media_type="application/x-ndjson")
