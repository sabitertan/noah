"""OpenRouter support: hosted models served by its API (nothing downloaded), next to Hugging Face and local ones. Network faked."""
import json
import stat
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from noah import llm, openrouter, server
from noah.extractors.openai_compat import OpenAICompatExtractor
from noah.registry import load_experts
from tests.fakes import anycall, http, reply, resp

KEY = "sk-or-v1-secret-key-value"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("OPENROUTER_API_KEY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NOAH_OR_TOKEN_FILE", str(tmp_path / "or_key"))
    monkeypatch.setenv("NOAH_OR_MODELS", str(tmp_path / "or_models.json"))
    monkeypatch.setenv("NOAH_HF_TOKEN_FILE", str(tmp_path / "hf_token"))
    monkeypatch.setenv("NOAH_HF_MODELS", str(tmp_path / "hf_models.json"))
    monkeypatch.setattr(server, "PROFILES", tmp_path / "profiles.json")
    monkeypatch.setattr(server, "DISABLED", tmp_path / "d.json")
    monkeypatch.setattr(openrouter, "_cache", None)
    return tmp_path


@pytest.fixture
def client(env: Path) -> TestClient:
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


# ---- routing a model name ----
def test_openrouter_models_go_to_openrouter_with_the_key(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm.save_token(KEY, llm.OR)
    seen: dict[str, Any] = {}
    monkeypatch.setattr(httpx, "post", http(lambda url, **kw: seen.update(url=url, kw=kw) or resp(body=reply())))
    llm.chat("http://localhost:11434/v1", {"model": "or:meta-llama/llama-3.3-70b-instruct:free"}, 5)
    assert seen["url"] == llm.OR_BASE + "/chat/completions"
    assert seen["kw"]["json"]["model"] == "meta-llama/llama-3.3-70b-instruct:free"   # prefix stripped, :free kept
    assert seen["kw"]["headers"] == {"Authorization": f"Bearer {KEY}"}


def test_each_provider_uses_only_its_own_key(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm.save_token("hf_only_a_hugging_face_token", llm.HF)
    monkeypatch.setattr(httpx, "post", anycall(lambda *a, **k: pytest.fail("a Hugging Face token must never go to OpenRouter")))
    with pytest.raises(llm.LLMError, match="OpenRouter.*API key"):
        llm.chat("", {"model": "or:openai/gpt-4o-mini"}, 5)
    llm.clear_token(llm.HF)
    llm.save_token(KEY, llm.OR)
    with pytest.raises(llm.LLMError, match="Hugging Face.*token"):
        llm.chat("", {"model": "hf:org/model"}, 5)


def test_provider_detection(env: Path) -> None:
    assert llm.provider_of("or:a/b") is llm.OR and llm.provider_of("hf:a/b") is llm.HF
    assert llm.provider_of("qwen2.5:0.5b") is None and llm.provider_of("hf.co/x/y:Q4_K_M") is None   # local names untouched
    assert llm.is_hosted("or:a/b") and not llm.is_hosted("qwen2.5:0.5b")


@pytest.mark.parametrize("status,words,fatal", [(401, "rejected the API key", True), (402, "credits", True), (403, "blocked", True),
                                                (404, "not available", False), (429, "rate limiting", True), (400, "could not run", False)])
def test_errors_are_explained_and_never_leak_the_key(env: Path, monkeypatch: pytest.MonkeyPatch, status: int, words: str, fatal: bool) -> None:
    llm.save_token(KEY, llm.OR)
    monkeypatch.setattr(httpx, "post", http(lambda url, **kw: resp(status, {"error": {"message": "x"}}, url)))
    with pytest.raises(llm.LLMError) as e:
        llm.chat("", {"model": "or:org/model"}, 5)
    assert words in str(e.value) and KEY not in str(e.value) and e.value.status == status and e.value.fatal is fatal


EXPERT = next(iter(load_experts("experts").values()))


def test_extractor_retries_with_tool_calls_when_structured_output_is_refused(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm.save_token(KEY, llm.OR)
    shapes: list[str] = []

    def post(url: str, **kw: Any) -> httpx.Response:
        shapes.append("schema" if "response_format" in kw["json"] else "tools")
        if "response_format" in kw["json"]:
            return resp(400, {}, url)
        args = {k: "x" for k in EXPERT.parameters.get("required", [])}
        return resp(body={"choices": [{"message": {"tool_calls": [{"function": {"arguments": json.dumps(args)}}]}}]})
    monkeypatch.setattr(httpx, "post", post)
    assert isinstance(OpenAICompatExtractor(model="or:org/model", fallback=False).extract("anything", EXPERT), dict)
    assert shapes == ["schema", "tools"]


# ---- key endpoints ----
def test_key_is_checked_saved_privately_and_never_returned(client: TestClient, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: dict[str, Any] = {}
    monkeypatch.setattr(httpx, "get", http(lambda url, **kw: asked.update(url=url, h=kw["headers"]) or resp(200, {"data": {"label": "my-laptop"}}, url)))
    r = client.put("/api/or/token", json={"token": KEY})
    assert r.status_code == 200 and r.json() == {"configured": True, "source": "file", "name": "my-laptop"}
    assert asked["url"] == llm.OR_BASE + "/key" and asked["h"] == {"Authorization": f"Bearer {KEY}"}
    assert KEY not in r.text and KEY not in client.get("/api/or/status").text and KEY not in client.get("/api/config").text
    f = env / "or_key"
    assert f.read_text() == KEY
    if sys.platform != "win32":
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert client.delete("/api/or/token").json()["configured"] is False and not f.exists()


def test_a_rejected_key_is_not_saved_and_env_keys_are_read_only(client: TestClient, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", http(lambda url, **kw: resp(401, {}, url)))
    assert client.put("/api/or/token", json={"token": KEY}).status_code == 400 and not (env / "or_key").exists()
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    assert client.get("/api/or/status").json() == {"configured": True, "source": "env"}
    assert client.delete("/api/or/token").status_code == 400


def test_the_two_providers_keep_separate_keys_and_lists(client: TestClient, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", http(lambda url, **kw: resp(200, {"data": {"label": "k"}, "name": "ada"}, url)))
    client.put("/api/or/token", json={"token": KEY})
    assert client.get("/api/hf/status").json()["configured"] is False
    client.post("/api/or/my", json={"id": "openai/gpt-4o-mini"})
    client.post("/api/hf/my", json={"id": "Qwen/Qwen2.5-7B-Instruct"})
    cfg = client.get("/api/config").json()
    assert cfg["or_models"] == ["or:openai/gpt-4o-mini"] and cfg["hf_models"] == ["hf:Qwen/Qwen2.5-7B-Instruct"]
    assert cfg["or"]["configured"] is True and cfg["hf"]["configured"] is False


# ---- search ----
LIST: dict[str, Any] = {"data": [
    {"id": "meta-llama/llama-3.3-70b-instruct:free", "name": "Meta: Llama 3.3 70B Instruct (free)", "context_length": 65536,
     "pricing": {"prompt": "0", "completion": "0"}, "architecture": {"output_modalities": ["text"]},
     "supported_parameters": ["tools", "tool_choice", "structured_outputs"], "hugging_face_id": "meta-llama/Llama-3.3-70B-Instruct"},
    {"id": "openai/gpt-4o-mini", "name": "OpenAI: GPT-4o-mini", "context_length": 128000,
     "pricing": {"prompt": "0.00000015", "completion": "0.0000006"}, "architecture": {"output_modalities": ["text"]},
     "supported_parameters": ["tools", "response_format", "logprobs"]},
    {"id": "openrouter/auto", "name": "Auto Router", "context_length": 2000000, "pricing": {"prompt": "-1", "completion": "-1"},
     "architecture": {"output_modalities": ["text"]}, "supported_parameters": []},
    {"id": "google/imagen-x", "name": "Imagen", "pricing": {}, "architecture": {"output_modalities": ["image"]}, "supported_parameters": []},
]}


def test_search_summarises_capabilities_and_prices(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", http(lambda url, **kw: resp(200, LIST, url)))
    hosted = client.get("/api/or/search").json()["hosted"]
    assert [m["id"] for m in hosted] == ["meta-llama/llama-3.3-70b-instruct:free", "openai/gpt-4o-mini", "openrouter/auto"]   # image model dropped
    free, mini, auto = hosted
    assert (free["free"], free["tools"], free["structured"], free["price_in"], free["hf_id"]) == (True, True, True, None, "meta-llama/Llama-3.3-70B-Instruct")
    assert (mini["price_in"], mini["logprobs"], mini["structured"], mini["context"]) == (0.15, True, True, 128000)    # per million tokens, no float noise
    assert auto["price_in"] is None and auto["free"] is False                                                        # "-1" means priced per request


def test_search_matches_names_and_survives_openrouter_being_down(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", http(lambda url, **kw: resp(200, LIST, url)))
    assert [m["id"] for m in client.get("/api/or/search", params={"q": "gpt mini"}).json()["hosted"]] == ["openai/gpt-4o-mini"]
    monkeypatch.setattr(openrouter, "_cache", None)
    monkeypatch.setattr(httpx, "get", http(lambda url, **kw: resp(500, {}, url)))
    j = client.get("/api/or/search").json()
    assert j["hosted"] == [] and "OpenRouter" in j["error"]


# ---- the user's list and the test button ----
def test_my_models_accept_openrouter_id_shapes(client: TestClient) -> None:
    for good in ["openai/gpt-4o-mini", "or:meta-llama/llama-3.3-70b-instruct:free", "~anthropic/claude-sonnet-latest", "x-ai/grok-3-mini:nitro"]:
        assert client.post("/api/or/my", json={"id": good}).status_code == 200, good
    client.post("/api/or/my", json={"id": "openai/gpt-4o-mini"})                      # duplicate
    assert client.get("/api/config").json()["or_models"] == ["or:openai/gpt-4o-mini", "or:meta-llama/llama-3.3-70b-instruct:free",
                                                              "or:~anthropic/claude-sonnet-latest", "or:x-ai/grok-3-mini:nitro"]
    assert client.post("/api/or/my", json={"id": "no slash"}).status_code == 400
    assert "or:openai/gpt-4o-mini" not in client.delete("/api/or/my/openai/gpt-4o-mini").json()["models"]


def test_test_button(client: TestClient, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "API key" in client.post("/api/or/test", json={"id": "openai/gpt-4o-mini"}).json()["error"]
    llm.save_token(KEY, llm.OR)
    seen: dict[str, Any] = {}
    monkeypatch.setattr(httpx, "post", http(lambda url, **kw: seen.update(url=url, m=kw["json"]["model"]) or resp(body=reply("ok"))))
    j = client.post("/api/or/test", json={"id": "or:openai/gpt-4o-mini"}).json()
    assert j["ok"] is True and j["reply"] == "ok" and seen["m"] == "openai/gpt-4o-mini" and seen["url"].startswith(llm.OR_BASE)
    monkeypatch.setattr(httpx, "post", http(lambda url, **kw: resp(402, {}, url)))
    assert "credits" in client.post("/api/or/test", json={"id": "openai/gpt-4o-mini"}).json()["error"]
