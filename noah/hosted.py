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
from typing import Any, Callable

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import llm


class TokenBody(BaseModel):
    token: str = ""
    endpoint: str = ""   # Azure only: the resource URL
    auth: str = "key"    # Azure only: "key" or "cli" (sign in with the Azure CLI)


class ModelBody(BaseModel):
    id: str


@dataclass
class Hosted:
    router: APIRouter
    my_models: Callable[[], list[str]]   # bare ids, no prefix
    status: Callable[[], dict[str, Any]]


def make(p: llm.Provider, validate: Callable[[TokenBody], str], *, save_credentials: Callable[[TokenBody], None] | None = None,
         clear_credentials: Callable[[], None] | None = None, status_fn: Callable[[], dict[str, Any]] | None = None) -> Hosted:
    """`validate(body)` returns the account name or raises HTTPException when the provider rejects the credentials.
    The hooks let a provider with more than a key (Azure: an endpoint and a sign-in method) store and report its own settings."""
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

    def key_status() -> dict[str, Any]:
        secret, source = llm.token(p)
        return {"configured": bool(secret), "source": source}
    status = status_fn or key_status

    def listing() -> dict[str, Any]:
        return {"models": [p.prefix + m for m in my_models()]}

    def get_status() -> dict[str, Any]:
        return status()

    def put_token(body: TokenBody) -> dict[str, Any]:
        body.token = body.token.strip()
        if body.auth != "cli" and (not 8 <= len(body.token) <= 300 or re.search(r"\s", body.token)):
            raise HTTPException(400, f"That does not look like a {p.label} {p.secret}.")
        name = validate(body)
        if save_credentials:
            save_credentials(body)
        else:
            llm.save_token(body.token, p)
        return {**status(), "name": name}

    def delete_token() -> dict[str, Any]:
        if status().get("source") == "env":
            raise HTTPException(400, f"The {p.secret} comes from the {p.envs[0]} environment variable; unset it there.")
        (clear_credentials or (lambda: llm.clear_token(p)))()
        return status()

    def add_my(body: ModelBody) -> dict[str, Any]:
        mid = body.id.strip()
        if mid.startswith(p.prefix):
            mid = mid[len(p.prefix):]
        if not p.id_re.match(mid):
            raise HTTPException(400, p.id_hint)
        ids = my_models()
        if mid not in ids:
            save([*ids, mid])
        return listing()

    def remove_my(model_id: str) -> dict[str, Any]:
        save([m for m in my_models() if m != model_id])
        return listing()

    def test(body: ModelBody) -> dict[str, Any]:
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

    # registered explicitly (not with decorators) so the type checker sees these closures are used
    router.add_api_route("/status", get_status, methods=["GET"])
    router.add_api_route("/token", put_token, methods=["PUT"])
    router.add_api_route("/token", delete_token, methods=["DELETE"])
    router.add_api_route("/my", add_my, methods=["POST"])
    router.add_api_route("/my/{model_id:path}", remove_my, methods=["DELETE"])
    router.add_api_route("/test", test, methods=["POST"])
    return Hosted(router, my_models, status)
