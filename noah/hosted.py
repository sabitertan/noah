"""The routes every hosted-model provider shares: key status, saving a key, the user's list of models, and a test call.

A provider module (hf.py, openrouter.py) calls `make()` and adds its own search on top. The key is write-only:
the API says whether one is set, never what it is.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import llm


class TokenBody(BaseModel):
    token: str


class ModelBody(BaseModel):
    id: str


@dataclass
class Hosted:
    router: APIRouter
    my_models: Callable[[], list[str]]   # bare ids, no prefix
    status: Callable[[], dict]


def make(p: llm.Provider, validate: Callable[[str], str]) -> Hosted:
    """`validate(secret)` returns the account name or raises HTTPException when the provider rejects the secret."""
    router = APIRouter(prefix=f"/api/{p.key}")
    models_file = lambda: Path(os.environ.get(p.models_env, p.models_default))  # noqa: E731  (read per call: tests and the desktop app set it late)

    def my_models() -> list[str]:
        try:
            return [m for m in json.loads(models_file().read_text()) if p.id_re.match(m)]
        except (OSError, ValueError):
            return []

    def save(ids: list[str]) -> None:
        f = models_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(ids, indent=1))

    def status() -> dict:
        secret, source = llm.token(p)
        return {"configured": bool(secret), "source": source}

    def listing() -> dict:
        return {"models": [p.prefix + m for m in my_models()]}

    @router.get("/status")
    def get_status():
        return status()

    @router.put("/token")
    def put_token(body: TokenBody):
        tok = body.token.strip()
        if not 8 <= len(tok) <= 300 or re.search(r"\s", tok):
            raise HTTPException(400, f"That does not look like a {p.label} {p.secret}.")
        name = validate(tok)
        llm.save_token(tok, p)
        return {**status(), "name": name}

    @router.delete("/token")
    def delete_token():
        if llm.token(p)[1] == "env":
            raise HTTPException(400, f"The {p.secret} comes from the {p.envs[0]} environment variable; unset it there.")
        llm.clear_token(p)
        return status()

    @router.post("/my")
    def add_my(body: ModelBody):
        mid = body.id.strip()
        if mid.startswith(p.prefix):
            mid = mid[len(p.prefix):]
        if not p.id_re.match(mid):
            raise HTTPException(400, "Use the form organisation/model-name, e.g. Qwen/Qwen2.5-7B-Instruct.")
        ids = my_models()
        if mid not in ids:
            save([*ids, mid])
        return listing()

    @router.delete("/my/{model_id:path}")
    def remove_my(model_id: str):
        save([m for m in my_models() if m != model_id])
        return listing()

    @router.post("/test")
    def test(body: ModelBody):
        """One tiny request, so the user knows the key and model work before wiring them into an orchestrator."""
        mid = body.id[len(p.prefix):] if body.id.startswith(p.prefix) else body.id
        if not p.id_re.match(mid):
            raise HTTPException(400, f"Not a {p.label} model id.")
        t0 = time.perf_counter()
        try:
            reply = llm.chat("", {"model": p.prefix + mid, "max_tokens": 16, "temperature": 0,
                                  "messages": [{"role": "user", "content": "Reply with the single word: ok"}]}, 60)
            return {"ok": True, "reply": (reply["choices"][0]["message"].get("content") or "").strip()[:80],
                    "ms": round((time.perf_counter() - t0) * 1000)}
        except llm.LLMError as e:
            return {"ok": False, "error": str(e)}
        except (httpx.HTTPError, KeyError, ValueError):
            return {"ok": False, "error": f"Could not reach {p.label}."}

    return Hosted(router, my_models, status)
