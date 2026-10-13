"""Hugging Face support: use a model hosted on Hugging Face (no download) or pull a GGUF repo into local Ollama.

Everything here talks to fixed Hugging Face hosts (or the local Ollama), never to a URL the browser supplies.
The token is write-only: the API says whether one is set, never what it is.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import llm

router = APIRouter(prefix="/api/hf")
MY_MODELS = Path(os.environ.get("NOAH_HF_MODELS", "data/hf_models.json"))
REPO = re.compile(r"^[\w.\-]+/[\w.\-]+$")
QUANT = re.compile(r"^[\w.\-]+$")
QUANT_IN_NAME = re.compile(r"(?<![A-Za-z0-9])((?:UD-)?(?:IQ\d(?:_[A-Z0-9]+)*|Q\d(?:_[A-Z0-9]+)*|BF16|F16|F32))(?![A-Za-z0-9])", re.I)
_cache: dict[str, tuple[float, object]] = {}


def ollama_url() -> str:
    return os.environ.get("NOAH_LLM_URL", "http://localhost:11434")


def my_models() -> list[str]:
    try:
        return [m for m in json.loads(MY_MODELS.read_text()) if llm.HF_ID.match(m)]
    except (OSError, ValueError):
        return []


def _save_my(ids: list[str]) -> None:
    MY_MODELS.parent.mkdir(parents=True, exist_ok=True)
    MY_MODELS.write_text(json.dumps(ids, indent=1))


def status() -> dict:
    tok, source = llm.token()
    return {"configured": bool(tok), "source": source}


@router.get("/status")
def get_status():
    return status()


class TokenBody(BaseModel):
    token: str


@router.put("/token")
def put_token(body: TokenBody):
    tok = body.token.strip()
    if not 8 <= len(tok) <= 300 or re.search(r"\s", tok):
        raise HTTPException(400, "That does not look like a Hugging Face token.")
    try:
        r = httpx.get(f"{llm.HF_HUB}/api/whoami-v2", headers={"Authorization": f"Bearer {tok}"}, timeout=10)
    except httpx.HTTPError:
        raise HTTPException(502, "Could not reach Hugging Face to check the token.")
    if r.status_code != 200:
        raise HTTPException(400, "Hugging Face rejected this token.")
    llm.save_token(tok)
    return {**status(), "name": r.json().get("name", "")}


@router.delete("/token")
def delete_token():
    if llm.token()[1] == "env":
        raise HTTPException(400, "The token comes from the HF_TOKEN environment variable; unset it there.")
    llm.clear_token()
    return status()


def _cached(key: str, ttl: float, fetch):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = fetch()
    _cache[key] = (time.time(), value)
    return value


def _hosted_models() -> list[dict]:
    """Models the Inference Providers router can run, boiled down to what a user needs to choose."""
    def fetch():
        r = httpx.get(f"{llm.HF_ROUTER}/models", timeout=15)
        r.raise_for_status()
        out = []
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


class ModelBody(BaseModel):
    id: str


@router.post("/my")
def add_my(body: ModelBody):
    mid = body.id.strip()
    if mid.startswith(llm.HF_PREFIX):
        mid = mid[len(llm.HF_PREFIX):]
    if not llm.HF_ID.match(mid):
        raise HTTPException(400, "Use the form organisation/model-name, e.g. Qwen/Qwen2.5-7B-Instruct.")
    ids = my_models()
    if mid not in ids:
        _save_my([*ids, mid])
    return {"models": [llm.HF_PREFIX + m for m in my_models()]}


@router.delete("/my/{model_id:path}")
def remove_my(model_id: str):
    _save_my([m for m in my_models() if m != model_id])
    return {"models": [llm.HF_PREFIX + m for m in my_models()]}


@router.post("/test")
def test(body: ModelBody):
    """One tiny request, so the user knows the token and model work before wiring them into an orchestrator."""
    model = body.id if llm.is_hf(body.id) else llm.HF_PREFIX + body.id
    if not llm.HF_ID.match(model[len(llm.HF_PREFIX):]):
        raise HTTPException(400, "Not a Hugging Face model id.")
    t0 = time.perf_counter()
    try:
        reply = llm.chat("", {"model": model, "max_tokens": 16, "temperature": 0,
                              "messages": [{"role": "user", "content": "Reply with the single word: ok"}]}, 60)
        return {"ok": True, "reply": (reply["choices"][0]["message"].get("content") or "").strip()[:80],
                "ms": round((time.perf_counter() - t0) * 1000)}
    except llm.LLMError as e:
        return {"ok": False, "error": str(e)}
    except (httpx.HTTPError, KeyError, ValueError):
        return {"ok": False, "error": "Could not reach Hugging Face."}
