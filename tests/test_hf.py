"""Hugging Face support: hosted models (no download) next to local ones. The network is faked; no token is needed."""
import contextlib
import json
import stat
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from noah import hf, llm, server
from noah.extractors.openai_compat import OpenAICompatExtractor
from noah.registry import load_experts

TOKEN = "hf_secret_token_value"


def resp(status=200, body=None, url="http://x/"):
    return httpx.Response(status, json=body if body is not None else {}, request=httpx.Request("POST", url))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.setenv("NOAH_HF_TOKEN_FILE", str(tmp_path / "hf_token"))
    monkeypatch.setattr(hf, "MY_MODELS", tmp_path / "hf_models.json")
    monkeypatch.setattr(server, "PROFILES", tmp_path / "profiles.json")
    monkeypatch.setattr(server, "DISABLED", tmp_path / "d.json")
    hf._cache.clear()
    return tmp_path


@pytest.fixture
def client(env):
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


def with_token(env):
    llm.save_token(TOKEN)


def reply(text="ok"):
    return {"choices": [{"message": {"content": text}}]}


# ---- routing a model name to the right server ----
def test_local_models_are_untouched(env, monkeypatch):
    seen = {}
    monkeypatch.setattr(httpx, "post", lambda url, **kw: seen.update(url=url, kw=kw) or resp(body=reply()))
    llm.chat("http://localhost:11434/v1", {"model": "qwen2.5:0.5b"}, 5)
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    assert seen["kw"]["json"]["model"] == "qwen2.5:0.5b" and "headers" not in seen["kw"]


def test_hosted_model_goes_to_the_router_with_the_token(env, monkeypatch):
    with_token(env)
    seen = {}
    monkeypatch.setattr(httpx, "post", lambda url, **kw: seen.update(url=url, kw=kw) or resp(body=reply()))
    llm.chat("http://localhost:11434/v1", {"model": "hf:Qwen/Qwen2.5-7B-Instruct:fastest"}, 5)
    assert seen["url"] == llm.HF_ROUTER + "/chat/completions"
    assert seen["kw"]["json"]["model"] == "Qwen/Qwen2.5-7B-Instruct:fastest"      # prefix stripped, suffix kept
    assert seen["kw"]["headers"] == {"Authorization": f"Bearer {TOKEN}"}


def test_hosted_model_without_a_token_says_so(env, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("must not call out without a token"))
    with pytest.raises(llm.LLMError, match="token"):
        llm.chat("", {"model": "hf:Qwen/Qwen2.5-7B-Instruct"}, 5)


@pytest.mark.parametrize("status,words", [(401, "rejected the token"), (402, "credits"), (403, "permission"),
                                          (404, "not available"), (429, "rate limiting")])
def test_hosted_errors_are_explained_and_never_leak_the_token(env, monkeypatch, status, words):
    with_token(env)
    monkeypatch.setattr(httpx, "post", lambda url, **kw: resp(status, {"error": "x"}, url))
    with pytest.raises(llm.LLMError) as e:
        llm.chat("", {"model": "hf:org/model"}, 5)
    assert words in str(e.value) and TOKEN not in str(e.value) and e.value.status == status


def test_env_token_wins_over_the_file(env, monkeypatch):
    with_token(env)
    monkeypatch.setenv("HF_TOKEN", "hf_from_env")
    assert llm.token() == ("hf_from_env", "env")


# ---- argument extractor on a hosted model ----
EXPERT = next(iter(load_experts("experts").values()))


def test_extractor_tries_tool_calls_when_the_provider_lacks_structured_output(env, monkeypatch):
    with_token(env)
    calls = []

    def post(url, **kw):
        calls.append(kw["json"])
        if "response_format" in kw["json"]:
            return resp(422, {}, url)
        args = {k: "x" for k in EXPERT.parameters.get("required", [])}
        return resp(body={"choices": [{"message": {"tool_calls": [{"function": {"arguments": json.dumps(args)}}]}}]})
    monkeypatch.setattr(httpx, "post", post)
    out = OpenAICompatExtractor(model="hf:org/model", fallback=False).extract("anything", EXPERT)
    assert [("response_format" in c, "tools" in c) for c in calls] == [(True, False), (False, True)]
    assert isinstance(out, dict)


def test_a_bad_token_is_not_hidden_by_the_rule_fallback(env, monkeypatch):
    with_token(env)
    monkeypatch.setattr(httpx, "post", lambda url, **kw: resp(401, {}, url))
    with pytest.raises(llm.LLMError, match="rejected"):
        OpenAICompatExtractor(model="hf:org/model", fallback=True).extract("anything", EXPERT)


# ---- token endpoints ----
def test_token_is_saved_privately_and_never_returned(client, env, monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: resp(200, {"name": "ada"}, url))
    r = client.put("/api/hf/token", json={"token": TOKEN})
    assert r.status_code == 200 and r.json() == {"configured": True, "source": "file", "name": "ada"}
    assert TOKEN not in r.text and TOKEN not in client.get("/api/hf/status").text and TOKEN not in client.get("/api/config").text
    f = env / "hf_token"
    assert f.read_text() == TOKEN
    if sys.platform != "win32":
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert client.delete("/api/hf/token").json()["configured"] is False and not f.exists()


def test_a_rejected_token_is_not_saved(client, env, monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: resp(401, {}, url))
    assert client.put("/api/hf/token", json={"token": TOKEN}).status_code == 400
    assert not (env / "hf_token").exists()
    assert client.put("/api/hf/token", json={"token": "no spaces allowed here"}).status_code == 400


def test_an_env_token_cannot_be_deleted_from_the_ui(client, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", TOKEN)
    assert client.get("/api/hf/status").json() == {"configured": True, "source": "env"}
    assert client.delete("/api/hf/token").status_code == 400


# ---- search, quantisations, pull ----
ROUTER = {"data": [
    {"id": "Qwen/Qwen2.5-7B-Instruct", "providers": [
        {"provider": "a", "status": "live", "supports_tools": True, "pricing": {"input": 0.3, "output": 0.6}, "context_length": 32768},
        {"provider": "b", "status": "live", "supports_structured_output": True, "pricing": {"input": 0.2, "output": 0.5}}]},
    {"id": "meta-llama/Llama-3.2-1B", "providers": [{"provider": "a", "status": "live", "is_free": True}]},
    {"id": "dead/Model", "providers": [{"provider": "a", "status": "error"}]},
]}


def fake_get(router=ROUTER, hub=None, hub_status=200):
    def get(url, **kw):
        if url.endswith("/models") and "router" in url:
            return resp(200, router, url)
        if "/api/models/" in url:
            return resp(hub_status, hub or {}, url)
        if url.endswith("/api/models"):
            return resp(hub_status, hub or [], url)
        return resp(404, {}, url)
    return get


def test_search_lists_hosted_and_gguf_separately(client, monkeypatch):
    monkeypatch.setattr(httpx, "get", fake_get(hub=[{"id": "unsloth/Qwen2.5-7B-GGUF", "downloads": 9, "likes": 2}, {"id": "bad id", "downloads": 1}]))
    j = client.get("/api/hf/search", params={"q": "qwen 7b"}).json()
    assert [m["id"] for m in j["hosted"]] == ["Qwen/Qwen2.5-7B-Instruct"]
    m = j["hosted"][0]
    assert (m["providers"], m["tools"], m["structured"], m["price_in"], m["context"]) == (2, True, True, 0.2, 32768)
    assert [m["id"] for m in j["gguf"]] == ["unsloth/Qwen2.5-7B-GGUF"] and j["error"] == ""   # malformed ids dropped


def test_search_survives_one_source_being_down(client, monkeypatch):
    monkeypatch.setattr(httpx, "get", fake_get(hub_status=500))
    j = client.get("/api/hf/search", params={"q": "llama"}).json()
    assert [m["id"] for m in j["hosted"]] == ["meta-llama/Llama-3.2-1B"] and j["gguf"] == [] and "GGUF" in j["error"]


def test_quants_sum_split_files_and_skip_projectors(client, monkeypatch):
    sib = [{"rfilename": "m-Q4_K_M.gguf", "size": 4000}, {"rfilename": "m-Q8_0-00001-of-00002.gguf", "size": 5000},
           {"rfilename": "m-Q8_0-00002-of-00002.gguf", "size": 3000}, {"rfilename": "mmproj-F16.gguf", "size": 9},
           {"rfilename": "README.md", "size": 1}]
    monkeypatch.setattr(httpx, "get", fake_get(hub={"siblings": sib}))
    assert client.get("/api/hf/quants", params={"repo": "o/m"}).json()["quants"] == [{"quant": "Q4_K_M", "size": 4000}, {"quant": "Q8_0", "size": 8000}]


@pytest.mark.parametrize("repo", ["a/b/c", "../etc", "a/..", "no-slash", "evil.com:80/x"])
def test_pull_and_quants_reject_bad_repo_ids(client, monkeypatch, repo):
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: pytest.fail("must not reach Ollama"))
    assert client.post("/api/hf/pull", json={"repo": repo}).status_code == 400
    assert client.get("/api/hf/quants", params={"repo": repo}).status_code == 400


def test_pull_streams_ollama_progress_and_only_asks_for_hf_models(client, monkeypatch):
    asked = {}

    class Fake:
        status_code = 200
        def iter_lines(self):
            yield '{"status":"pulling","total":10,"completed":5}'
            yield '{"status":"success"}'

    @contextlib.contextmanager
    def stream(method, url, **kw):
        asked.update(url=url, json=kw["json"])
        yield Fake()
    monkeypatch.setattr(httpx, "stream", stream)
    r = client.post("/api/hf/pull", json={"repo": "unsloth/Qwen3-4B-GGUF", "quant": "Q4_K_M"})
    assert [json.loads(x)["status"] for x in r.text.splitlines()] == ["pulling", "success"]
    assert asked["json"]["model"] == "hf.co/unsloth/Qwen3-4B-GGUF:Q4_K_M" and asked["url"].endswith("/api/pull")


def test_pull_reports_ollama_being_down(client, monkeypatch):
    @contextlib.contextmanager
    def stream(*a, **k):
        raise httpx.ConnectError("down")
        yield
    monkeypatch.setattr(httpx, "stream", stream)
    assert "Ollama" in json.loads(client.post("/api/hf/pull", json={"repo": "o/m"}).text)["error"]


# ---- the user's own list of hosted models, and config ----
def test_my_models_add_remove_and_show_in_config(client):
    assert client.post("/api/hf/my", json={"id": "Qwen/Qwen2.5-7B-Instruct"}).status_code == 200
    client.post("/api/hf/my", json={"id": "hf:Qwen/Qwen2.5-7B-Instruct"})            # same model, prefixed: no duplicate
    client.post("/api/hf/my", json={"id": "meta-llama/Llama-3.2-1B-Instruct:cheapest"})
    assert client.get("/api/config").json()["hf_models"] == ["hf:Qwen/Qwen2.5-7B-Instruct", "hf:meta-llama/Llama-3.2-1B-Instruct:cheapest"]
    assert client.get("/api/config").json()["hf"] == {"configured": False, "source": ""}
    assert client.post("/api/hf/my", json={"id": "not a model"}).status_code == 400
    assert client.delete("/api/hf/my/Qwen/Qwen2.5-7B-Instruct").json()["models"] == ["hf:meta-llama/Llama-3.2-1B-Instruct:cheapest"]


def test_test_button_reports_success_and_failure_in_plain_words(client, env, monkeypatch):
    assert "token" in client.post("/api/hf/test", json={"id": "o/m"}).json()["error"]
    with_token(env)
    monkeypatch.setattr(httpx, "post", lambda url, **kw: resp(body=reply("ok")))
    j = client.post("/api/hf/test", json={"id": "o/m"}).json()
    assert j["ok"] is True and j["reply"] == "ok"
    monkeypatch.setattr(httpx, "post", lambda url, **kw: resp(402, {}, url))
    assert "credits" in client.post("/api/hf/test", json={"id": "o/m"}).json()["error"]
