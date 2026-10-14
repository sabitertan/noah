import asyncio
import math
import sys
import types
from typing import Any, Callable

import httpx
import pytest

from noah.engines import make_engine
from noah.engines.llm_choice import LLMChoiceEngine, decision_record
from noah.extractors import make_extractor
from noah.orchestrator import Orchestrator
from noah.registry import load_experts
from noah.types import Expert


@pytest.fixture(scope="module")
def experts() -> dict[str, Expert]:
    return load_experts("experts")


def fake_post(content: str, top: list[tuple[str, float]] | None = None,
              seen: list[dict[str, Any]] | None = None) -> Callable[..., httpx.Response]:
    def post(url: str, json: dict[str, Any] | None = None, timeout: Any = None) -> httpx.Response:
        if seen is not None:
            seen.append(json or {})
        logprobs = {"content": [{"token": content, "logprob": 0.0,
                                 "top_logprobs": [{"token": t, "logprob": lp} for t, lp in top]}]} if top else None
        body = {"choices": [{"message": {"content": content}, "logprobs": logprobs}]}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))
    return post


def label_of(record: dict[str, Any], key: str) -> str:
    return next(o["label"] for o in record["options"] if o["key"] == key)


def test_record_letters_experts_and_ends_with_none(experts: dict[str, Expert]) -> None:
    r = decision_record("hi", experts)
    assert r["state"] == "hi" and r["question"]
    assert [o["label"] for o in r["options"]] == list("ABCDEFGHIJKLMNOPQRSTUVWX"[:len(experts) + 1])
    assert r["options"][-1]["key"] == "none" and {o["key"] for o in r["options"][:-1]} == set(experts)


def test_llm_choice_scores_every_expert_from_logprobs(experts: dict[str, Expert], monkeypatch: pytest.MonkeyPatch) -> None:
    r = decision_record("x", experts)
    calc, weather, none = label_of(r, "calc"), label_of(r, "weather"), label_of(r, "none")
    seen: list[dict[str, Any]] = []
    top = [(calc, math.log(0.7)), (none, math.log(0.2)), (weather, math.log(0.1)), ("<think>", -9.0)]
    monkeypatch.setattr(httpx, "post", fake_post(calc, top, seen))
    d = LLMChoiceEngine(model="tev1").decide("what is 2+2", experts)
    assert d[0].expert == "calc" and d[0].score == pytest.approx(0.7, abs=1e-3)
    assert d[1].expert == "weather" and d[1].score == pytest.approx(0.1, abs=1e-3)
    assert "none" not in {x.expert for x in d} and len(d) == len(experts)
    assert seen[0]["model"] == "tev1" and seen[0]["temperature"] == 0 and seen[0]["logprobs"] is True


def test_llm_choice_none_and_letter_only_fallback(experts: dict[str, Expert], monkeypatch: pytest.MonkeyPatch) -> None:
    r = decision_record("x", experts)
    monkeypatch.setattr(httpx, "post", fake_post(label_of(r, "none")))  # server without logprobs
    assert all(x.score == 0.0 for x in LLMChoiceEngine(model="m").decide("tell me a joke", experts))
    monkeypatch.setattr(httpx, "post", fake_post(label_of(r, "weather")))
    d = LLMChoiceEngine(model="m").decide("weather in Oslo", experts)
    assert d[0].expert == "weather" and d[0].score == 1.0


def test_llm_choice_routes_then_extractor_fills_args(experts: dict[str, Expert], monkeypatch: pytest.MonkeyPatch) -> None:
    calc = label_of(decision_record("x", experts), "calc")
    monkeypatch.setattr(httpx, "post", fake_post(calc, [(calc, math.log(0.9))]))
    orch = Orchestrator(experts, LLMChoiceEngine(model="m"), make_extractor("rule"), threshold=0.5)
    out = asyncio.run(orch.ask("what is 6*7"))
    assert out[0].expert == "calc" and out[0].extractor == "rule" and "42" in out[0].output


class FakeRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.preloaded: list[str] = []

    def preload(self, names: list[str]) -> None:
        self.preloaded += names

    def predict(self, state: str, questions: dict[str, Any], **kw: Any) -> dict[str, Any]:
        self.calls.append((state, questions, kw))
        options: list[str] = list(questions["expert"]["criteria"])
        probs = {o: 0.0 for o in options} | {"weather": 0.8, "none": 0.15, "calc": 0.05}
        return {"answers": {"expert": {"type": "choice", "choice": "weather", "probabilities": probs}}}


@pytest.fixture
def fake_laya(monkeypatch: pytest.MonkeyPatch) -> FakeRouter:
    router = FakeRouter()
    monkeypatch.setitem(sys.modules, "laya", types.SimpleNamespace(Router=lambda: router))
    return router


def test_laya_choice_question_and_scores(experts: dict[str, Expert], fake_laya: FakeRouter) -> None:
    d = make_engine("laya").decide("weather in Oslo", experts)
    assert d[0].expert == "weather" and d[0].score == 0.8 and d[1].expert == "calc"
    state, q, kw = fake_laya.calls[0]
    assert state == "weather in Oslo" and kw == {}  # "auto": Laya picks english/multilingual itself
    assert q["expert"]["type"] == "choice" and set(q["expert"]["criteria"]) == {*experts, "none"}


def test_laya_named_checkpoint_is_preloaded_and_used(experts: dict[str, Expert], fake_laya: FakeRouter) -> None:
    make_engine("laya", model="english").decide("hi", experts)
    assert fake_laya.preloaded == ["english"] and fake_laya.calls[0][2] == {"model": "english"}


def test_laya_missing_dependency_says_how_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "laya", None)  # makes `import laya` raise ImportError
    with pytest.raises(RuntimeError, match=r"\.\[laya\]"):
        make_engine("laya")
