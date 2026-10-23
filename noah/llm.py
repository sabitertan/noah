"""Where a model is served from, and one way to call it.

A model name is local (served by Ollama or any OpenAI-compatible server at `base_url`) or hosted by a provider:
`hf:<org>/<name>` goes to Hugging Face's Inference Providers router, `or:<org>/<name>` to OpenRouter, `oa:<model>` to
OpenAI and `an:<model>` to Anthropic (Claude, through its SDK: anthropic_api.py), each with the user's own key. Hosted models are called through the provider's API; nothing is downloaded. `web:<id>` runs in the
visitor's browser with WebLLM (browser build only, see webllm.py).
"""
from __future__ import annotations

from typing import Any
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

HF_PREFIX = "hf:"
HF_ROUTER = os.environ.get("NOAH_HF_ROUTER", "https://router.huggingface.co/v1")
HF_HUB = os.environ.get("NOAH_HF_HUB", "https://huggingface.co")
AZ_PREFIX = "az:"
OR_PREFIX = "or:"
WEB_PREFIX = "web:"  # runs in the visitor's browser (webllm.py), not a hosted provider
OR_BASE = os.environ.get("NOAH_OR_BASE", "https://openrouter.ai/api/v1")
OA_PREFIX, AN_PREFIX = "oa:", "an:"
OA_BASE = os.environ.get("NOAH_OA_BASE", "https://api.openai.com/v1")
HF_ID = re.compile(r"^[\w.\-]+/[\w.\-]+(:[\w\-]+)?$")  # org/name, optionally :provider or :fastest / :cheapest
OR_ID = re.compile(r"^~?[\w.\-]+/[\w.\-+~]+(:[\w\-]+)?$")  # org/name, optionally :free / :nitro / :floor
OA_ID = re.compile(r"^[\w.\-:]{1,120}$")  # gpt-5, o4-mini, ft:gpt-4o-mini:org::abc
AN_ID = re.compile(r"^claude-[\w.\-]{1,80}$")  # claude-opus-5-5
AZ_ID = re.compile(r"^[\w.\-]{1,64}$")  # an Azure deployment name (the name you gave the deployment, not the model)


@dataclass(frozen=True)
class Provider:
    key: str                 # "hf" | "or": the /api/<key> routes and the config field
    label: str
    prefix: str
    base: str
    secret: str              # what the user calls the credential: "token" / "API key"
    envs: tuple[str, ...]    # environment variables that can hold it
    file_env: str
    file_default: str
    models_env: str
    models_default: str
    id_re: re.Pattern[str]
    errors: dict[int, str] = field(default_factory=lambda: {})  # HTTP status -> plain-words explanation ({name} = the model)
    id_hint: str = "Use the form organisation/model-name, e.g. Qwen/Qwen2.5-7B-Instruct."
    connector: str = ""      # module with connect() -> (base_url, headers), for providers whose endpoint and sign-in are per user
    sdk: bool = False        # called through the provider's own SDK, not /chat/completions (Anthropic: anthropic_api.py)


HF = Provider("hf", "Hugging Face", HF_PREFIX, HF_ROUTER, "token", ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"),
              "NOAH_HF_TOKEN_FILE", "data/hf_token", "NOAH_HF_MODELS", "data/hf_models.json", HF_ID, {
    401: "Hugging Face rejected the token. Check it on the Models page.",
    402: "Hugging Face says you are out of Inference Providers credits.",
    403: "The token may not be allowed to call Inference Providers (create one with that permission), or the model is gated.",
    404: "{name} is not available on Hugging Face Inference Providers.",
    422: "Hugging Face could not run {name} with this request (the provider may not support tools or structured output).",
    429: "Hugging Face is rate limiting this token. Wait a moment and retry.",
})
OR = Provider("or", "OpenRouter", OR_PREFIX, OR_BASE, "API key", ("OPENROUTER_API_KEY",),
              "NOAH_OR_TOKEN_FILE", "data/openrouter_key", "NOAH_OR_MODELS", "data/openrouter_models.json", OR_ID, {
    400: "OpenRouter could not run {name} with this request (it may not support tools or structured output).",
    401: "OpenRouter rejected the API key. Check it on the Models page.",
    402: "OpenRouter says you are out of credits (or {name} needs a paid balance).",
    403: "OpenRouter blocked the request (key permissions or content moderation).",
    404: "{name} is not available on OpenRouter right now, or no provider serves it with these settings.",
    429: "OpenRouter is rate limiting this key (free models have low limits). Wait a moment and retry.",
})
AZ = Provider("az", "Azure Foundry", AZ_PREFIX, "", "API key", ("AZURE_FOUNDRY_API_KEY", "AZURE_OPENAI_API_KEY", "AZURE_INFERENCE_CREDENTIAL"),
              "NOAH_AZ_TOKEN_FILE", "data/azure_key", "NOAH_AZ_MODELS", "data/azure_models.json", AZ_ID, {
    400: "Azure Foundry could not run deployment {name} with this request (it may not support tools or structured output).",
    401: "Azure Foundry rejected the credentials. Check the key, or that key access is enabled on the resource (otherwise sign in with the Azure CLI).",
    403: "Your Azure identity may lack a role on this resource (for example Cognitive Services OpenAI User), or the content filter blocked the request.",
    404: "No deployment named {name} on this resource. Use the deployment name from the Foundry portal, not the model name.",
    429: "Azure Foundry is rate limiting this deployment (quota). Wait a moment and retry.",
}, "Use the deployment name from the Foundry portal, e.g. gpt-4o-mini or my-llama.", "azure")
OA = Provider("oa", "OpenAI", OA_PREFIX, OA_BASE, "API key", ("OPENAI_API_KEY",),
              "NOAH_OA_TOKEN_FILE", "data/openai_key", "NOAH_OA_MODELS", "data/openai_models.json", OA_ID, {
    400: "OpenAI could not run {name} with this request (it may not support tools or structured output).",
    401: "OpenAI rejected the API key. Check it on the Models page.",
    403: "This OpenAI key or project may not use {name}.",
    404: "{name} is not an OpenAI model this key can use.",
    429: "OpenAI is rate limiting this key, or the account is out of credits. Check platform.openai.com/usage.",
}, "Use an OpenAI model id, e.g. gpt-5-mini.")
AN = Provider("an", "Claude", AN_PREFIX, "", "API key", ("ANTHROPIC_API_KEY",),
              "NOAH_AN_TOKEN_FILE", "data/anthropic_key", "NOAH_AN_MODELS", "data/anthropic_models.json", AN_ID,
              id_hint="Use a Claude model id, e.g. claude-opus-5-5.", sdk=True)
PROVIDERS = (HF, OR, AZ, OA, AN)


class LLMError(RuntimeError):
    """A model call failed for a reason worth telling the user in plain words. `status` is the HTTP status, if any."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def fatal(self) -> bool:  # retrying a different request shape will not help (no/bad key, no credits, rate limit)
        return self.status in (None, 401, 402, 403, 429)


def provider_of(model: str) -> Provider | None:
    m = str(model or "")
    return next((p for p in PROVIDERS if m.startswith(p.prefix)), None)


def is_hosted(model: str) -> bool:
    return provider_of(model) is not None


def is_hf(model: str) -> bool:
    return provider_of(model) is HF


def _token_file(p: Provider) -> Path:
    return Path(os.environ.get(p.file_env, p.file_default))


def token(p: Provider = HF) -> tuple[str, str]:
    """(credential, source) where source is 'env', 'file' or '' when there is none."""
    for var in p.envs:
        if os.environ.get(var, "").strip():
            return os.environ[var].strip(), "env"
    try:
        t = _token_file(p).read_text().strip()
        return (t, "file") if t else ("", "")
    except OSError:
        return "", ""


def save_token(value: str, p: Provider = HF) -> None:
    f = _token_file(p)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(value.strip())
    try:
        f.chmod(0o600)  # owner only (no-op on Windows)
    except OSError:
        pass


def clear_token(p: Provider = HF) -> None:
    try:
        _token_file(p).unlink()
    except OSError:
        pass


def resolve(base_url: str, model: str) -> tuple[str, str, dict[str, Any]]:
    """(base_url, model name to send, extra headers) for a model reference."""
    p = provider_of(model)
    if p is None:
        return base_url, model, {}
    if p.connector:  # endpoint and credentials are the user's own (Azure): the connector builds both
        import importlib
        base, headers = importlib.import_module(f"noah.{p.connector}").connect()
        return base, model[len(p.prefix):], headers
    tok, _ = token(p)
    if not tok:
        raise LLMError(f"This model runs on {p.label} but no {p.secret} is set. Add one on the Models page.")
    return p.base, model[len(p.prefix):], {"Authorization": f"Bearer {tok}"}


def _explain(p: Provider, status: int, model: str) -> str:
    name = model[len(p.prefix):]
    return p.errors.get(status, f"{p.label} returned HTTP {status} for {{name}}.").format(name=name)


def chat(base_url: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    """POST /chat/completions to the right server for body['model'] and return the JSON reply."""
    model = body.get("model", "")
    if str(model).startswith(WEB_PREFIX):
        from . import webllm  # it imports LLMError from here
        return webllm.chat(body, timeout)
    p = provider_of(model)
    if p is not None and p.sdk:
        from . import anthropic_api  # it imports LLMError from here
        tok, _ = token(p)
        if not tok:
            raise LLMError(f"This model runs on {p.label} but no {p.secret} is set. Add one on the Models page.")
        return anthropic_api.chat(body, model[len(p.prefix):], tok, timeout)
    url, name, headers = resolve(base_url, model)
    try:
        endpoint, payload = f"{url.rstrip('/')}/chat/completions", {**body, "model": name}
        if headers:
            r = httpx.post(endpoint, json=payload, timeout=timeout, headers=headers)
        else:  # local calls stay exactly as before
            r = httpx.post(endpoint, json=payload, timeout=timeout)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        if p in (AZ, OA) and e.response.status_code == 400 and (retry := _newer_model_body(body, e.response)):
            return chat(base_url, retry, timeout)  # reasoning models want max_completion_tokens and the default temperature
        if p:
            raise LLMError(_explain(p, e.response.status_code, model), e.response.status_code) from None  # never echo headers
        raise
    return r.json()


def _newer_model_body(body: dict[str, Any], response: httpx.Response) -> dict[str, Any] | None:
    """Newer (reasoning) models on Azure and OpenAI reject max_tokens and a custom temperature. Retry once without them."""
    try:
        text = response.text.lower()
    except Exception:
        return None
    if "max_completion_tokens" not in text and "temperature" not in text:
        return None
    fixed = {k: v for k, v in body.items() if k not in ("max_tokens", "temperature")}
    if "max_tokens" in body:
        fixed["max_completion_tokens"] = max(int(body["max_tokens"]), 256)  # reasoning tokens count against the limit
    return fixed if fixed != body else None
