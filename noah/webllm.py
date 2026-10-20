"""Models that run in the visitor's browser with WebLLM (WebGPU): `web:<WebLLM model id>`, browser build only.

The weights are downloaded into the browser's cache from the Models page (WebLLM tab) and run on the visitor's GPU, inside
the page. Python runs in a Web Worker (noah/wasm.py) and its model calls are synchronous, so `chat()` hands the request to
the page and waits for the reply with Pyodide's `run_sync`, which suspends the WebAssembly stack until a JavaScript promise
settles (JSPI). The page translates the OpenAI-style request into what WebLLM accepts (frontend/src/lib/webllm-openai.js).

A server or desktop Noah cannot use these models: its Python is not in the browser, so it cannot reach the visitor's GPU.

The user's list of web models (`data/web_models.json`) only says which ones to offer in the model pickers; the page keeps
it in step with what is actually downloaded.
"""
from __future__ import annotations

import importlib
import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .llm import WEB_PREFIX as PREFIX, LLMError

ID = re.compile(r"^[\w.\-]{1,128}$")   # a WebLLM model id, e.g. Qwen2.5-0.5B-Instruct-q4f16_1-MLC

router = APIRouter(prefix="/api/web")


class ModelBody(BaseModel):
    id: str


def _models_file() -> Path:
    return Path(os.environ.get("NOAH_WEB_MODELS", "data/web_models.json"))   # read per call: tests set it late


def my_models() -> list[str]:
    """Bare ids, no prefix."""
    try:
        return [m for m in json.loads(_models_file().read_text()) if isinstance(m, str) and ID.match(m)]
    except (OSError, ValueError):
        return []


def _save(ids: list[str]) -> dict[str, Any]:
    f = _models_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(ids, indent=1))
    return {"models": [PREFIX + m for m in ids]}


def _bare(model_id: str) -> str:
    mid = model_id.strip().removeprefix(PREFIX)
    if not ID.match(mid):
        raise HTTPException(400, "Not a WebLLM model id, e.g. Qwen2.5-0.5B-Instruct-q4f16_1-MLC.")
    return mid


def add_my(body: ModelBody) -> dict[str, Any]:
    mid, ids = _bare(body.id), my_models()
    return _save(ids if mid in ids else [*ids, mid])


def remove_my(model_id: str) -> dict[str, Any]:
    mid = _bare(model_id)
    return _save([m for m in my_models() if m != mid])


router.add_api_route("/my", add_my, methods=["POST"])
router.add_api_route("/my/{model_id:path}", remove_my, methods=["DELETE"])


def chat(body: dict[str, Any], timeout: float) -> dict[str, Any]:
    """One chat completion from the page's WebLLM engine. `timeout` covers generating, not loading the model onto the GPU."""
    try:
        ffi: Any = importlib.import_module("pyodide.ffi")
        js: Any = importlib.import_module("js")
    except ImportError:
        raise LLMError(f"{body.get('model')} runs inside a web browser (WebLLM) and only the browser version of Noah can use it. "
                       "Pick a local or hosted model here.") from None
    if not ffi.can_run_sync():
        raise LLMError("This browser cannot pause Noah's Python while an in-browser model answers (it needs WebAssembly JSPI). "
                       "Use a recent Chrome or Edge.")
    payload = json.dumps({**body, "model": str(body.get("model", "")).removeprefix(PREFIX)})
    reply: dict[str, Any] = json.loads(ffi.run_sync(js.noahWebLLM(payload, int(timeout * 1000))))
    if "error" in reply:
        # a setup problem (no WebGPU, model not downloaded) must be seen; a bad answer (status 500) may fall back
        raise LLMError(reply["error"], None if reply.get("setup") else 500)
    return reply
