"""`web:` models (noah/webllm.py): the list the model pickers offer, and the call that hands a request to the page's WebLLM.
The browser parts (Pyodide's run_sync, the page's `noahWebLLM`) are stand-ins here; the browser build exercises the real ones."""
import json
import sys
import types
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from noah import llm, server, webllm
from noah.extractors.openai_compat import OpenAICompatExtractor
from noah.registry import load_experts

MODEL = "Qwen2.5-0.5B-Instruct-q4f16_1-MLC"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("NOAH_WEB_MODELS", str(tmp_path / "web_models.json"))
    return TestClient(server.app)


class Page:
    """Stands in for Pyodide (run_sync) and the page (noahWebLLM): records the request, returns `reply`."""

    def __init__(self, reply: dict[str, Any], jspi: bool = True):
        self.reply, self.jspi, self.sent = reply, jspi, []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ffi = types.ModuleType("pyodide.ffi")
        ffi.can_run_sync = lambda: self.jspi  # type: ignore[attr-defined]
        ffi.run_sync = lambda awaitable: awaitable  # type: ignore[attr-defined]  # the stand-in "promise" is the answer itself
        js = types.ModuleType("js")
        js.noahWebLLM = lambda body, ms: (self.sent.append((json.loads(body), ms)), json.dumps(self.reply))[1]  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pyodide", types.ModuleType("pyodide"))
        monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi)
        monkeypatch.setitem(sys.modules, "js", js)


@pytest.fixture
def page(monkeypatch: pytest.MonkeyPatch) -> Iterator[Page]:
    p = Page({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
    p.install(monkeypatch)
    yield p


# ---- the list behind the model pickers ----
def test_downloaded_models_are_listed_for_the_pickers(client: TestClient) -> None:
    assert client.get("/api/config").json()["web_models"] == []
    assert client.post("/api/web/my", json={"id": MODEL}).json() == {"models": ["web:" + MODEL]}
    assert client.post("/api/web/my", json={"id": "web:" + MODEL}).json() == {"models": ["web:" + MODEL]}  # no duplicates
    assert client.get("/api/config").json()["web_models"] == ["web:" + MODEL]
    assert client.delete(f"/api/web/my/{MODEL}").json() == {"models": []}


def test_ids_that_are_not_webllm_ids_are_refused(client: TestClient) -> None:
    for bad in ("", "../x", "a/b", "x" * 200, "has space"):
        assert client.post("/api/web/my", json={"id": bad}).status_code == 400


# ---- calling a web model ----
def test_outside_the_browser_it_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "js", None)  # an import of js fails, as it does in CPython
    with pytest.raises(llm.LLMError, match="browser version of Noah") as e:
        llm.chat("http://localhost:11434/v1", {"model": "web:" + MODEL, "messages": []}, 30)
    assert e.value.fatal


def test_the_page_gets_the_bare_id_and_the_timeout_in_ms(page: Page) -> None:
    reply = llm.chat("http://unused/v1", {"model": "web:" + MODEL, "messages": [{"role": "user", "content": "hi"}]}, 30)
    assert reply["choices"][0]["message"]["content"] == "ok"
    body, ms = page.sent[0]
    assert body["model"] == MODEL and body["messages"][0]["content"] == "hi" and ms == 30000


def test_without_jspi_it_asks_for_another_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    Page({}, jspi=False).install(monkeypatch)
    with pytest.raises(llm.LLMError, match="JSPI"):
        webllm.chat({"model": "web:" + MODEL}, 30)


def test_setup_problems_are_fatal_and_bad_answers_are_not(monkeypatch: pytest.MonkeyPatch) -> None:
    Page({"error": "not downloaded", "setup": True}).install(monkeypatch)
    with pytest.raises(llm.LLMError) as e:
        webllm.chat({"model": "web:" + MODEL}, 30)
    assert e.value.fatal
    Page({"error": "grammar failed", "setup": False}).install(monkeypatch)
    with pytest.raises(llm.LLMError) as e:
        webllm.chat({"model": "web:" + MODEL}, 30)
    assert not e.value.fatal


def test_an_extractor_on_a_web_model_falls_back_to_rules_after_a_bad_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    Page({"error": "grammar failed", "setup": False}).install(monkeypatch)
    calc = load_experts(Path("experts"))["calc"]
    args = OpenAICompatExtractor(model="web:" + MODEL).extract("calculate 15% of 240", calc)
    assert args  # the rule extractor answered
