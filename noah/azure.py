"""Azure Foundry support: use a model deployment on your own Azure resource through its OpenAI-compatible v1 route.

Unlike the other providers there is no public catalogue and no fixed host: you bring an endpoint
(https://<resource>.openai.azure.com) and the name of a deployment you created. Two ways to sign in:
an API key, or your Azure CLI login (Microsoft Entra ID, keyless). Nothing is downloaded.

The key is sent only to a host that looks like an Azure endpoint (see `normalize_endpoint`), so a mistyped or malicious
URL cannot collect it. Credentials are write-only: the API says whether they are set, never what they are.
"""
from __future__ import annotations

from typing import Any
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from . import hosted, llm

SCOPE_RESOURCE = "https://ai.azure.com"  # Entra token audience for Foundry inference
HOST = re.compile(r"^[a-z0-9][a-z0-9\-]{0,62}\.(openai|services\.ai|cognitiveservices)\.azure\.(com|us|cn)$")
ENV_ENDPOINTS = ("AZURE_FOUNDRY_ENDPOINT", "AZURE_OPENAI_ENDPOINT")
_cli_cache: tuple[str, float] | None = None


def normalize_endpoint(url: str) -> str:
    """'https://my-res.openai.azure.com/openai/v1/' -> 'https://my-res.openai.azure.com'. Raises ValueError if it is not an Azure host."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    local_ok = os.environ.get("NOAH_AZURE_ALLOW_LOCAL") == "1" and host in ("127.0.0.1", "localhost")  # tests only
    if not local_ok and (parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443) or not HOST.match(host)):
        raise ValueError("That is not an Azure endpoint. Use https://<resource>.openai.azure.com or https://<resource>.services.ai.azure.com.")
    return f"{parts.scheme}://{host}" + (f":{parts.port}" if local_ok and parts.port else "")


def _settings_file() -> Path:
    return Path(os.environ.get("NOAH_AZ_SETTINGS", "data/azure_foundry.json"))


def settings() -> dict[str, Any]:
    """{'endpoint', 'auth', 'source'}; the endpoint can come from the environment or from what the user saved."""
    saved: dict[str, Any] = {}
    try:
        saved = json.loads(_settings_file().read_text())
    except (OSError, ValueError):
        pass
    for var in ENV_ENDPOINTS:
        if os.environ.get(var, "").strip():
            try:
                return {"endpoint": normalize_endpoint(os.environ[var]), "auth": saved.get("auth", "key"), "source": "env"}
            except ValueError:
                break
    ep = saved.get("endpoint", "")
    return {"endpoint": ep if ep else "", "auth": saved.get("auth", "key"), "source": "file" if ep else ""}


def status() -> dict[str, Any]:
    s = settings()
    key, key_source = llm.token(llm.AZ)
    ready = bool(s["endpoint"]) and (s["auth"] == "cli" or bool(key))
    source = "env" if ready and s["source"] == "env" and (s["auth"] == "cli" or key_source == "env") else ("file" if ready else "")
    return {"configured": ready, "source": source, "endpoint": s["endpoint"], "auth": s["auth"]}


def cli_token() -> str:
    """A short-lived Entra token from the signed-in Azure CLI (`az login`), cached until shortly before it expires."""
    global _cli_cache
    if _cli_cache and time.time() < _cli_cache[1] - 120:
        return _cli_cache[0]
    az = shutil.which("az")
    if not az:
        raise llm.LLMError("The Azure CLI (az) was not found. Install it and run `az login`, or use an API key.")
    try:
        out = subprocess.run([az, "account", "get-access-token", "--resource", SCOPE_RESOURCE, "--output", "json"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        raise llm.LLMError("The Azure CLI did not answer. Run `az login` and try again.") from None
    if out.returncode != 0:
        raise llm.LLMError("The Azure CLI is not signed in (or cannot reach this resource). Run `az login` and try again.")
    try:
        data = json.loads(out.stdout)
        tok, expires = data["accessToken"], float(data.get("expires_on") or time.time() + 1200)
    except (ValueError, KeyError, TypeError):
        raise llm.LLMError("The Azure CLI returned something unexpected. Run `az login` and try again.") from None
    _cli_cache = (tok, expires)
    return tok


def connect() -> tuple[str, dict[str, Any]]:
    """(base URL, headers) for a call to the user's own Azure resource."""
    s = settings()
    if not s["endpoint"]:
        raise llm.LLMError("This model runs on Azure Foundry but no endpoint is set. Add one on the Models page.")
    if s["auth"] == "cli":
        tok = cli_token()
    else:
        tok, _ = llm.token(llm.AZ)
        if not tok:
            raise llm.LLMError("This model runs on Azure Foundry but no API key is set. Add one on the Models page.")
    return s["endpoint"] + "/openai/v1", {"Authorization": f"Bearer {tok}"}


def _validate(body: hosted.TokenBody) -> str:
    try:
        endpoint = normalize_endpoint(body.endpoint or settings()["endpoint"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    body.endpoint = endpoint
    if body.auth not in ("key", "cli"):
        raise HTTPException(400, "Choose an API key or Azure CLI sign-in.")
    if body.auth == "cli":
        try:
            cli_token()
        except llm.LLMError as e:
            raise HTTPException(400, str(e))
        return "Azure CLI"
    # Best effort: the models listing needs the same credentials. Only a clear rejection blocks saving; a 404 just means
    # this resource does not expose the listing, and the Test button on a deployment is the real check.
    try:
        r = httpx.get(f"{endpoint}/openai/v1/models", headers={"Authorization": f"Bearer {body.token}"}, timeout=10)
    except httpx.HTTPError:
        raise HTTPException(502, "Could not reach that Azure endpoint.")
    if r.status_code in (401, 403):
        raise HTTPException(400, "Azure rejected this API key (or key access is disabled on the resource).")
    return endpoint.split("//", 1)[1].split(".", 1)[0]


def _save(body: hosted.TokenBody) -> None:
    f = _settings_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"endpoint": body.endpoint, "auth": body.auth}))
    if body.auth == "key":
        llm.save_token(body.token, llm.AZ)
    else:
        llm.clear_token(llm.AZ)  # keyless: no key kept on disk


def _clear() -> None:
    global _cli_cache
    _cli_cache = None
    llm.clear_token(llm.AZ)
    try:
        _settings_file().unlink()
    except OSError:
        pass


_hosted = hosted.make(llm.AZ, _validate, save_credentials=_save, clear_credentials=_clear, status_fn=status)
router, my_models = _hosted.router, _hosted.my_models
