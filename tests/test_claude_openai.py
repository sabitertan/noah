"""Claude (through the Anthropic SDK) and OpenAI as hosted providers, and the build-mode advice to use a bigger model. Network faked."""
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from noah import anthropic_api, builder, generate, llm, server
from noah.registry import load_experts
from tests.fakes import anycall, http, reply, resp

AN_KEY, OA_KEY = "sk-ant-api03-secret-value", "sk-proj-secret-value"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    for p in ("AN", "OA"):
        monkeypatch.setenv(f"NOAH_{p}_TOKEN_FILE", str(tmp_path / f"{p}_key"))
        monkeypatch.setenv(f"NOAH_{p}_MODELS", str(tmp_path / f"{p}_models.json"))
    monkeypatch.setattr(server, "PROFILES", tmp_path / "profiles.json")
    return tmp_path


@pytest.fixture
def client(env: Path) -> TestClient:
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


class FakeAnthropic:
    """Stands in for anthropic.Anthropic: records each messages.create call and answers with `content`."""
    calls: list[dict[str, Any]] = []
    content: list[Any] = []
    stop_reason = "end_turn"
    error: Exception | None = None

    def __init__(self, api_key: str, **kw: Any):
        self.api_key = api_key
        self.messages = SimpleNamespace(create=self._create)
        self.models = SimpleNamespace(list=lambda: [SimpleNamespace(id="claude-opus-5-5", display_name="Claude Opus 5.5")])

    def _create(self, **kw: Any) -> Any:
        if FakeAnthropic.error:
            raise FakeAnthropic.error
        FakeAnthropic.calls.append({"api_key": self.api_key, **kw})
        return SimpleNamespace(content=FakeAnthropic.content, stop_reason=FakeAnthropic.stop_reason)


@pytest.fixture
def fake_claude(monkeypatch: pytest.MonkeyPatch) -> type[FakeAnthropic]:
    FakeAnthropic.calls, FakeAnthropic.content, FakeAnthropic.stop_reason, FakeAnthropic.error = [], [], "end_turn", None
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    return FakeAnthropic


def text(t: str) -> Any:
    return SimpleNamespace(type="text", text=t)


# ---- Claude: an OpenAI-shaped call in, a Messages API request out ----
def test_claude_models_go_through_the_sdk_with_the_key(env: Path, fake_claude: type[FakeAnthropic]) -> None:
    llm.save_token(AN_KEY, llm.AN)
    fake_claude.content = [text("Hello")]
    out = llm.chat("http://localhost:11434/v1", {"model": "an:claude-opus-5-5", "temperature": 0.2, "max_tokens": 700, "messages": [
        {"role": "system", "content": "Be brief. /no_think"}, {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"}, {"role": "user", "content": "again"}]}, 30)
    assert out["choices"][0]["message"]["content"] == "Hello"
    call = fake_claude.calls[0]
    assert call["api_key"] == AN_KEY and call["model"] == "claude-opus-5-5" and call["system"] == "Be brief."
    assert [m["role"] for m in call["messages"]] == ["user", "assistant", "user"]
    assert "temperature" not in call and call["max_tokens"] == anthropic_api.MIN_TOKENS  # room for thinking, no sampling settings


def test_claude_json_schema_and_tools_are_translated(env: Path, fake_claude: type[FakeAnthropic]) -> None:
    llm.save_token(AN_KEY, llm.AN)
    schema = {"type": "object", "properties": {"city": {"type": "string"}, "when": {"type": "object", "properties": {}}}}
    fake_claude.content = [text('{"city": "Oslo"}')]
    llm.chat("", {"model": "an:claude-sonnet-5-5", "messages": [{"role": "user", "content": "x"}],
                  "response_format": {"type": "json_schema", "json_schema": {"name": "w", "schema": schema}}}, 30)
    sent = fake_claude.calls[0]["output_config"]["format"]["schema"]
    assert sent["additionalProperties"] is False and sent["properties"]["when"]["additionalProperties"] is False
    assert "additionalProperties" not in schema  # the expert's own schema is untouched

    fake_claude.content = [SimpleNamespace(type="tool_use", id="t1", name="weather", input={"city": "Oslo"})]
    out = llm.chat("", {"model": "an:claude-sonnet-5-5", "messages": [{"role": "user", "content": "x"}],
                        "tools": [{"type": "function", "function": {"name": "weather", "description": "d", "parameters": schema}}],
                        "tool_choice": {"type": "function", "function": {"name": "weather"}}}, 30)
    call = fake_claude.calls[1]
    assert call["tools"][0]["input_schema"] == schema and call["tool_choice"] == {"type": "auto"}  # forcing is rejected by current models
    assert json.loads(out["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]) == {"city": "Oslo"}


def test_claude_errors_are_plain_words_and_never_echo_the_key(env: Path, fake_claude: type[FakeAnthropic]) -> None:
    with pytest.raises(llm.LLMError, match="no API key is set"):
        llm.chat("", {"model": "an:claude-opus-5-5", "messages": [{"role": "user", "content": "x"}]}, 30)
    llm.save_token(AN_KEY, llm.AN)
    fake_claude.error = anthropic.AuthenticationError("bad key " + AN_KEY, response=httpx.Response(401, request=httpx.Request("POST", "https://x/")), body=None)
    with pytest.raises(llm.LLMError) as e:
        llm.chat("", {"model": "an:claude-opus-5-5", "messages": [{"role": "user", "content": "x"}]}, 30)
    assert e.value.status == 401 and e.value.fatal and AN_KEY not in str(e.value)
    fake_claude.error, fake_claude.stop_reason = None, "refusal"
    with pytest.raises(llm.LLMError, match="declined"):
        llm.chat("", {"model": "an:claude-opus-5-5", "messages": [{"role": "user", "content": "x"}]}, 30)


def test_claude_tab_suggests_models_then_lists_the_keys_own(client: TestClient, fake_claude: type[FakeAnthropic]) -> None:
    assert [m["id"] for m in client.get("/api/an/search").json()["hosted"]][:2] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert client.put("/api/an/token", json={"token": AN_KEY}).json()["configured"] is True
    assert client.get("/api/an/search?q=opus").json()["hosted"][0]["name"] == "Claude Opus 5.5"
    assert client.post("/api/an/my", json={"id": "claude-opus-5-5"}).json() == {"models": ["an:claude-opus-5-5"]}
    assert client.post("/api/an/my", json={"id": "gpt-5"}).status_code == 400
    cfg = client.get("/api/config").json()
    assert cfg["an_models"] == ["an:claude-opus-5-5"] and cfg["an"]["configured"] and AN_KEY not in json.dumps(cfg)


# ---- OpenAI: its own chat-completions endpoint ----
def test_openai_models_go_to_openai_with_the_key(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm.save_token(OA_KEY, llm.OA)
    seen: list[dict[str, Any]] = []

    def post(url: str, **kw: Any) -> httpx.Response:
        seen.append({"url": url, **kw})
        if "max_tokens" in kw["json"]:  # a reasoning model: wants max_completion_tokens and the default temperature
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens'."}},
                                  request=httpx.Request("POST", url))
        return resp(200, reply("ok"), url)

    monkeypatch.setattr(httpx, "post", http(post))
    out = llm.chat("http://localhost:11434/v1", {"model": "oa:gpt-5-mini", "temperature": 0, "max_tokens": 50, "messages": []}, 30)
    assert out["choices"][0]["message"]["content"] == "ok"
    assert seen[0]["url"] == "https://api.openai.com/v1/chat/completions" and seen[0]["headers"]["Authorization"] == f"Bearer {OA_KEY}"
    assert seen[1]["json"]["model"] == "gpt-5-mini" and seen[1]["json"]["max_completion_tokens"] == 256 and "temperature" not in seen[1]["json"]


def test_openai_tab_lists_chat_models_for_the_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "Connect" in client.get("/api/oa/search").json()["error"]
    rows = [{"id": i} for i in ("gpt-5-mini", "text-embedding-3-small", "gpt-4o-mini-tts", "o4-mini", "dall-e-3", "gpt-5")]
    monkeypatch.setattr(httpx, "get", http(lambda url, **kw: httpx.Response(200 if kw["headers"]["Authorization"] == f"Bearer {OA_KEY}" else 401,
                                                                             json={"data": rows}, request=httpx.Request("GET", url))))
    assert client.put("/api/oa/token", json={"token": "sk-wrong-key-value"}).status_code == 400
    assert client.put("/api/oa/token", json={"token": OA_KEY}).status_code == 200
    assert [m["id"] for m in client.get("/api/oa/search").json()["hosted"]] == ["o4-mini", "gpt-5-mini", "gpt-5"]


# ---- build mode: say when the models are too small, and let one setting change every builder ----
@pytest.mark.parametrize("model,small", [("qwen2.5-coder:1.5b", True), ("qwen3:0.6b", True), ("or:meta-llama/llama-3.2-3b-instruct:free", True),
                                         ("web:Qwen2.5-0.5B-Instruct-q4f16_1-MLC", True), ("qwen2.5-coder:14b", False),
                                         ("hf:Qwen/Qwen2.5-7B-Instruct", False), ("gpt-oss:20b", False), ("an:claude-opus-5-5", False),
                                         ("oa:gpt-5-mini", False), ("devstral", False)])
def test_small_models_are_recognised_by_their_size(model: str, small: bool) -> None:
    assert builder.is_small(model) is small


def test_a_builder_model_replaces_every_experts_own_and_silences_the_advice(monkeypatch: pytest.MonkeyPatch) -> None:
    experts = load_experts("experts")
    models: list[str] = []
    monkeypatch.setattr(builder, "chat", anycall(lambda url, body, t: models.append(body["model"]) or reply("not json")))
    monkeypatch.setattr(generate, "chat", anycall(lambda url, body, t: models.append(body["model"]) or reply("```html\n<p>x</p>\n```")))
    _, p, _ = builder.build("a clock", experts, "http://x/v1")
    assert p["small"] == ["qwen2.5-coder:1.5b"]  # the default models: tell the user
    models.clear()
    _, p, _ = builder.build("a clock", experts, "http://x/v1", build_model="an:claude-opus-5-5")
    assert set(models) == {"an:claude-opus-5-5"} and p["small"] == []  # planner falls back to it too
