"""Where a model is served from, and one way to call it.

A model name is either local (served by Ollama or any OpenAI-compatible server at `base_url`) or hosted on
Hugging Face: `hf:<org>/<name>` goes to Hugging Face's Inference Providers router with the user's token, no download.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import httpx

HF_PREFIX = "hf:"
HF_ROUTER = os.environ.get("NOAH_HF_ROUTER", "https://router.huggingface.co/v1")
HF_HUB = os.environ.get("NOAH_HF_HUB", "https://huggingface.co")
HF_ID = re.compile(r"^[\w.\-]+/[\w.\-]+(:[\w\-]+)?$")  # org/name, optionally :provider or :fastest / :cheapest


class LLMError(RuntimeError):
    """A model call failed for a reason worth telling the user in plain words. `status` is the HTTP status, if any."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def fatal(self) -> bool:  # retrying a different request shape will not help (no/bad token, no credits, rate limit)
        return self.status in (None, 401, 402, 403, 429)


def is_hf(model: str) -> bool:
    return str(model or "").startswith(HF_PREFIX)


def _token_file() -> Path:
    return Path(os.environ.get("NOAH_HF_TOKEN_FILE", "data/hf_token"))


def token() -> tuple[str, str]:
    """(token, source) where source is 'env', 'file' or '' when there is none."""
    for var in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        if os.environ.get(var, "").strip():
            return os.environ[var].strip(), "env"
    try:
        t = _token_file().read_text().strip()
        return (t, "file") if t else ("", "")
    except OSError:
        return "", ""


def save_token(value: str) -> None:
    f = _token_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(value.strip())
    try:
        f.chmod(0o600)  # owner only (no-op on Windows)
    except OSError:
        pass


def clear_token() -> None:
    try:
        _token_file().unlink()
    except OSError:
        pass


def resolve(base_url: str, model: str) -> tuple[str, str, dict]:
    """(base_url, model name to send, extra headers) for a model reference."""
    if not is_hf(model):
        return base_url, model, {}
    tok, _ = token()
    if not tok:
        raise LLMError("This model runs on Hugging Face but no token is set. Add one on the Models page.")
    return HF_ROUTER, model[len(HF_PREFIX):], {"Authorization": f"Bearer {tok}"}


def _explain(status: int, model: str) -> str:
    name = model[len(HF_PREFIX):] if is_hf(model) else model
    return {
        401: "Hugging Face rejected the token. Check it on the Models page.",
        402: "Hugging Face says you are out of Inference Providers credits.",
        403: "The token may not be allowed to call Inference Providers (create one with that permission), or the model is gated.",
        404: f"{name} is not available on Hugging Face Inference Providers.",
        422: f"Hugging Face could not run {name} with this request (the provider may not support tools or structured output).",
        429: "Hugging Face is rate limiting this token. Wait a moment and retry.",
    }.get(status, f"Hugging Face returned HTTP {status} for {name}.")


def chat(base_url: str, body: dict, timeout: float) -> dict:
    """POST /chat/completions to the right server for body['model'] and return the JSON reply."""
    model = body.get("model", "")
    url, name, headers = resolve(base_url, model)
    try:
        extra = {"headers": headers} if headers else {}  # local calls stay exactly as before
        r = httpx.post(f"{url.rstrip('/')}/chat/completions", json={**body, "model": name}, timeout=timeout, **extra)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        if is_hf(model):
            raise LLMError(_explain(e.response.status_code, model), e.response.status_code) from None  # never echo headers
        raise
    return r.json()
