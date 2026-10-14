"""Azure Foundry: your own resource's deployments through the OpenAI-compatible v1 route. Network and the Azure CLI are faked."""
import json
import stat
import subprocess
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from noah import azure, llm, server

KEY = "azure-secret-key-1234567890"
ENDPOINT = "https://my-resource.openai.azure.com"


def resp(status=200, body=None, url="http://x/"):
    return httpx.Response(status, json=body if body is not None else {}, request=httpx.Request("POST", url))


@pytest.fixture
def env(tmp_path, monkeypatch):
    for var in (*llm.AZ.envs, *azure.ENV_ENDPOINTS, "NOAH_AZURE_ALLOW_LOCAL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NOAH_AZ_TOKEN_FILE", str(tmp_path / "az_key"))
    monkeypatch.setenv("NOAH_AZ_SETTINGS", str(tmp_path / "az.json"))
    monkeypatch.setenv("NOAH_AZ_MODELS", str(tmp_path / "az_models.json"))
    monkeypatch.setattr(server, "PROFILES", tmp_path / "profiles.json")
    monkeypatch.setattr(server, "DISABLED", tmp_path / "d.json")
    monkeypatch.setattr(azure, "_cli_cache", None)
    return tmp_path


@pytest.fixture
def client(env):
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


def connect_with_key(client, monkeypatch, probe=200):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: resp(probe, {}, url))
    return client.put("/api/az/token", json={"token": KEY, "endpoint": ENDPOINT, "auth": "key"})


def reply(text="ok"):
    return {"choices": [{"message": {"content": text}}]}


# ---- the key only goes to Azure hosts ----
@pytest.mark.parametrize("url,expected", [
    ("https://my-resource.openai.azure.com", ENDPOINT),
    ("https://my-resource.openai.azure.com/openai/v1/", ENDPOINT),                       # pasted with the path
    ("HTTPS://My-Resource.OpenAI.Azure.com/some/portal/path?x=1", ENDPOINT),
    ("https://my-resource.services.ai.azure.com/models", "https://my-resource.services.ai.azure.com"),
    ("https://r1.cognitiveservices.azure.com/", "https://r1.cognitiveservices.azure.com"),
    ("https://gov-res.openai.azure.us", "https://gov-res.openai.azure.us"),
])
def test_azure_endpoints_are_accepted_and_trimmed(url, expected):
    assert azure.normalize_endpoint(url) == expected


@pytest.mark.parametrize("url", [
    "http://my-resource.openai.azure.com",                 # not https
    "https://evil.example.com", "https://openai.azure.com.evil.com", "https://evil.com/my.openai.azure.com",
    "https://my-resource.openai.azure.com@evil.com", "https://user:pw@my-resource.openai.azure.com",
    "https://my-resource.openai.azure.com:8443", "https://openai.azure.com", "https://-bad.openai.azure.com",
    "https://localhost", "https://127.0.0.1", "", "not a url",
])
def test_everything_else_is_refused_so_a_key_cannot_be_sent_elsewhere(url):
    with pytest.raises(ValueError):
        azure.normalize_endpoint(url)


# ---- routing ----
def test_a_deployment_is_called_on_the_users_resource_with_the_key(env, monkeypatch):
    llm.save_token(KEY, llm.AZ)
    (env / "az.json").write_text(json.dumps({"endpoint": ENDPOINT, "auth": "key"}))
    seen = {}
    monkeypatch.setattr(httpx, "post", lambda url, **kw: seen.update(url=url, kw=kw) or resp(body=reply()))
    llm.chat("http://localhost:11434/v1", {"model": "az:my-gpt-4o-mini"}, 5)
    assert seen["url"] == ENDPOINT + "/openai/v1/chat/completions"        # v1 route: no api-version
    assert seen["kw"]["json"]["model"] == "my-gpt-4o-mini"                 # the deployment name, prefix stripped
    assert seen["kw"]["headers"] == {"Authorization": f"Bearer {KEY}"}


def test_missing_endpoint_or_key_is_explained_and_nothing_is_sent(env, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("must not call out"))
    with pytest.raises(llm.LLMError, match="no endpoint"):
        llm.chat("", {"model": "az:d"}, 5)
    (env / "az.json").write_text(json.dumps({"endpoint": ENDPOINT, "auth": "key"}))
    with pytest.raises(llm.LLMError, match="no API key"):
        llm.chat("", {"model": "az:d"}, 5)


def test_environment_variables_configure_everything(env, monkeypatch):
    monkeypatch.setenv("AZURE_FOUNDRY_ENDPOINT", "https://env-res.openai.azure.com/openai/v1")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", KEY)
    assert azure.status() == {"configured": True, "source": "env", "endpoint": "https://env-res.openai.azure.com", "auth": "key"}
    assert azure.connect() == ("https://env-res.openai.azure.com/openai/v1", {"Authorization": f"Bearer {KEY}"})


@pytest.mark.parametrize("status,words,fatal", [(401, "rejected the credentials", True), (403, "role", True), (404, "No deployment named", False),
                                                (429, "rate limiting", True), (400, "could not run", False)])
def test_errors_are_explained_and_never_leak_the_key(env, monkeypatch, status, words, fatal):
    llm.save_token(KEY, llm.AZ)
    (env / "az.json").write_text(json.dumps({"endpoint": ENDPOINT, "auth": "key"}))
    monkeypatch.setattr(httpx, "post", lambda url, **kw: resp(status, {"error": {"message": "nope"}}, url))
    with pytest.raises(llm.LLMError) as e:
        llm.chat("", {"model": "az:my-deploy"}, 5)
    assert words in str(e.value) and KEY not in str(e.value) and e.value.fatal is fatal
    if status == 404:
        assert "my-deploy" in str(e.value)


def test_reasoning_models_get_the_parameters_they_accept(env, monkeypatch):
    llm.save_token(KEY, llm.AZ)
    (env / "az.json").write_text(json.dumps({"endpoint": ENDPOINT, "auth": "key"}))
    bodies = []

    def post(url, **kw):
        bodies.append(kw["json"])
        if "max_tokens" in kw["json"]:
            return resp(400, {"error": {"message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."}}, url)
        return resp(body=reply("done"))
    monkeypatch.setattr(httpx, "post", post)
    out = llm.chat("", {"model": "az:gpt-5-mini", "max_tokens": 8, "temperature": 0, "messages": []}, 5)
    assert out["choices"][0]["message"]["content"] == "done" and len(bodies) == 2
    retry = bodies[1]
    assert "max_tokens" not in retry and "temperature" not in retry and retry["max_completion_tokens"] >= 256


def test_an_unrelated_400_is_not_retried_and_other_providers_never_retry(env, monkeypatch):
    llm.save_token(KEY, llm.AZ)
    (env / "az.json").write_text(json.dumps({"endpoint": ENDPOINT, "auth": "key"}))
    calls = []
    monkeypatch.setattr(httpx, "post", lambda url, **kw: calls.append(1) or resp(400, {"error": {"message": "bad json"}}, url))
    with pytest.raises(llm.LLMError):
        llm.chat("", {"model": "az:d", "max_tokens": 8}, 5)
    assert len(calls) == 1


# ---- keyless: the Azure CLI ----
def fake_az(monkeypatch, token="entra-token-abc", returncode=0, expires_in=3600):
    calls = []
    monkeypatch.setattr(azure.shutil, "which", lambda name: "/usr/bin/az")

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, json.dumps({"accessToken": token, "expires_on": time.time() + expires_in}), "")
    monkeypatch.setattr(azure.subprocess, "run", run)
    return calls


def test_cli_sign_in_uses_the_foundry_token_scope_and_is_cached(env, monkeypatch):
    (env / "az.json").write_text(json.dumps({"endpoint": ENDPOINT, "auth": "cli"}))
    calls = fake_az(monkeypatch)
    assert azure.status()["configured"] is True                                      # no key needed in this mode
    assert azure.connect() == (ENDPOINT + "/openai/v1", {"Authorization": "Bearer entra-token-abc"})
    azure.connect()
    assert len(calls) == 1 and calls[0][1:4] == ["account", "get-access-token", "--resource"] and "https://ai.azure.com" in calls[0]


def test_an_expiring_cli_token_is_fetched_again(env, monkeypatch):
    (env / "az.json").write_text(json.dumps({"endpoint": ENDPOINT, "auth": "cli"}))
    calls = fake_az(monkeypatch, expires_in=60)                                       # inside the 2 minute safety margin
    azure.connect(); azure.connect()
    assert len(calls) == 2


def test_cli_problems_are_explained(env, monkeypatch):
    (env / "az.json").write_text(json.dumps({"endpoint": ENDPOINT, "auth": "cli"}))
    monkeypatch.setattr(azure.shutil, "which", lambda name: None)
    with pytest.raises(llm.LLMError, match="not found"):
        azure.connect()
    fake_az(monkeypatch, returncode=1)
    with pytest.raises(llm.LLMError, match="az login"):
        azure.connect()


# ---- connection endpoints ----
def test_connecting_with_a_key_saves_privately_and_never_returns_it(client, env, monkeypatch):
    r = connect_with_key(client, monkeypatch)
    assert r.status_code == 200 and r.json() == {"configured": True, "source": "file", "endpoint": ENDPOINT, "auth": "key", "name": "my-resource"}
    for text in (r.text, client.get("/api/az/status").text, client.get("/api/config").text):
        assert KEY not in text
    assert (env / "az_key").read_text() == KEY and json.loads((env / "az.json").read_text()) == {"endpoint": ENDPOINT, "auth": "key"}
    if sys.platform != "win32":
        assert stat.S_IMODE((env / "az_key").stat().st_mode) == 0o600
    cleared = client.delete("/api/az/token").json()
    assert cleared["configured"] is False and not (env / "az_key").exists() and not (env / "az.json").exists()


def test_the_key_is_checked_against_the_endpoint_with_a_bearer_header(client, env, monkeypatch):
    seen = {}
    monkeypatch.setattr(httpx, "get", lambda url, **kw: seen.update(url=url, h=kw["headers"]) or resp(200, {}, url))
    client.put("/api/az/token", json={"token": KEY, "endpoint": ENDPOINT + "/openai/v1/", "auth": "key"})
    assert seen["url"] == ENDPOINT + "/openai/v1/models" and seen["h"] == {"Authorization": f"Bearer {KEY}"}


def test_a_rejected_key_or_a_foreign_host_is_not_saved_and_nothing_is_sent(client, env, monkeypatch):
    assert connect_with_key(client, monkeypatch, probe=401).status_code == 400 and not (env / "az_key").exists()
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("a key must never go to a non-Azure host"))
    r = client.put("/api/az/token", json={"token": KEY, "endpoint": "https://evil.example.com", "auth": "key"})
    assert r.status_code == 400 and "not an Azure endpoint" in r.json()["detail"] and not (env / "az_key").exists()


def test_a_resource_without_a_models_listing_is_still_accepted(client, env, monkeypatch):
    assert connect_with_key(client, monkeypatch, probe=404).status_code == 200


def test_cli_mode_stores_no_key(client, env, monkeypatch):
    (env / "az_key").write_text("old-key-that-should-go")
    fake_az(monkeypatch)
    r = client.put("/api/az/token", json={"endpoint": ENDPOINT, "auth": "cli"})
    assert r.status_code == 200 and r.json()["auth"] == "cli" and r.json()["name"] == "Azure CLI"
    assert not (env / "az_key").exists()
    fake_az(monkeypatch, returncode=1)
    azure._cli_cache = None
    assert client.put("/api/az/token", json={"endpoint": ENDPOINT, "auth": "cli"}).status_code == 400


def test_env_configuration_is_read_only_in_the_ui(client, monkeypatch):
    monkeypatch.setenv("AZURE_FOUNDRY_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("AZURE_FOUNDRY_API_KEY", KEY)
    assert client.get("/api/az/status").json()["source"] == "env"
    assert client.delete("/api/az/token").status_code == 400


# ---- deployments and the test button ----
def test_deployments_are_listed_by_name_and_validated(client):
    for good in ["gpt-4o-mini", "my_llama.v2", "az:prod-gpt5", "DeepSeek-V3"]:
        assert client.post("/api/az/my", json={"id": good}).status_code == 200, good
    client.post("/api/az/my", json={"id": "gpt-4o-mini"})                                   # duplicate
    cfg = client.get("/api/config").json()
    assert cfg["az_models"] == ["az:gpt-4o-mini", "az:my_llama.v2", "az:prod-gpt5", "az:DeepSeek-V3"]
    assert client.post("/api/az/my", json={"id": "has space"}).status_code == 400
    assert client.post("/api/az/my", json={"id": "org/model"}).status_code == 400            # a deployment name, not a model path
    assert "deployment" in client.post("/api/az/my", json={"id": "x/y"}).json()["detail"]
    assert "az:gpt-4o-mini" not in client.delete("/api/az/my/gpt-4o-mini").json()["models"]


def test_the_providers_do_not_share_lists_or_keys(client, env, monkeypatch):
    connect_with_key(client, monkeypatch)
    client.post("/api/az/my", json={"id": "gpt-4o-mini"})
    cfg = client.get("/api/config").json()
    assert cfg["az_models"] == ["az:gpt-4o-mini"] and cfg["hf_models"] == [] and cfg["or_models"] == []
    assert cfg["hf"]["configured"] is False and cfg["or"]["configured"] is False and cfg["az"]["configured"] is True


def test_test_button(client, env, monkeypatch):
    assert "no endpoint" in client.post("/api/az/test", json={"id": "gpt-4o-mini"}).json()["error"]
    connect_with_key(client, monkeypatch)
    seen = {}
    monkeypatch.setattr(httpx, "post", lambda url, **kw: seen.update(url=url, m=kw["json"]["model"]) or resp(body=reply("ok")))
    j = client.post("/api/az/test", json={"id": "az:gpt-4o-mini"}).json()
    assert j["ok"] is True and seen["m"] == "gpt-4o-mini" and seen["url"] == ENDPOINT + "/openai/v1/chat/completions"
    monkeypatch.setattr(httpx, "post", lambda url, **kw: resp(404, {}, url))
    assert "No deployment named gpt-4o-mini" in client.post("/api/az/test", json={"id": "gpt-4o-mini"}).json()["error"]
