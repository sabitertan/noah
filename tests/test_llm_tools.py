import asyncio

import httpx
import pytest

from noah.engines.llm_tools import LLMToolsEngine, expert_tools, parse_functiongemma
from noah.extractors import make_extractor
from noah.orchestrator import Orchestrator
from noah.registry import load_experts


@pytest.fixture(scope="module")
def experts():
    return load_experts("experts")


def fake_post(content=None, tool_calls=None):
    def post(url, json=None, timeout=None):
        body = {"choices": [{"message": {"content": content, "tool_calls": tool_calls}}]}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))
    return post


def test_parse_functiongemma_forms():
    assert parse_functiongemma("call:weather{city:<escape>Paris<escape>") == [("weather", {"city": "Paris"})]  # truncated
    assert parse_functiongemma("<start_function_call>call:calc{expression:<escape>1+2<escape>}<end_function_call>") == [
        ("calc", {"expression": "1+2"})]
    assert parse_functiongemma("call:files{query:<escape>a<escape>,limit:5}") == [("files", {"query": "a", "limit": 5})]
    assert parse_functiongemma("Sorry, I cannot help") == []


def test_tools_include_none_and_synthesize_request_param(experts):
    tools = {t["function"]["name"]: t["function"] for t in expert_tools(experts)}
    assert "none" in tools
    assert "request" in tools["csharp"]["parameters"]["properties"]  # generate experts have no params of their own
    assert "city" in tools["weather"]["parameters"]["properties"]


def test_engine_native_tool_calls_and_none(experts, monkeypatch):
    calls = [{"function": {"name": "weather", "arguments": '{"city": "Oslo"}'}}]
    monkeypatch.setattr(httpx, "post", fake_post(tool_calls=calls))
    d = LLMToolsEngine(model="m").decide("weather in Oslo", experts)
    assert d[0].expert == "weather" and d[0].args == {"city": "Oslo"} and d[0].score == 1.0
    assert all(x.score == 0.0 for x in d[1:]) and len(d) == len(experts)

    monkeypatch.setattr(httpx, "post", fake_post(tool_calls=[{"function": {"name": "none", "arguments": "{}"}}]))
    assert all(x.score == 0.0 for x in LLMToolsEngine(model="m").decide("joke", experts))


def test_engine_functiongemma_text_format(experts, monkeypatch):
    monkeypatch.setattr(httpx, "post", fake_post(content="<start_function_call>call:calc{expression:<escape>2+2<escape>}"))
    d = LLMToolsEngine(model="m").decide("2+2", experts)
    assert d[0].expert == "calc" and d[0].args == {"expression": "2+2"}


def test_orchestrator_uses_model_supplied_args_and_skips_extractor(experts, monkeypatch):
    monkeypatch.setattr(httpx, "post", fake_post(tool_calls=[{"function": {"name": "calc", "arguments": '{"expression": "6*7"}'}}]))
    orch = Orchestrator(experts, LLMToolsEngine(model="m"), make_extractor("rule"))
    out = asyncio.run(orch.ask("whatever the user typed"))
    assert out[0].expert == "calc" and out[0].extractor == "orchestrator" and "42" in out[0].output


def test_no_expert_called_gives_no_match(experts, monkeypatch):
    monkeypatch.setattr(httpx, "post", fake_post(content="I cannot help with that"))
    orch = Orchestrator(experts, LLMToolsEngine(model="m"), make_extractor("rule"))
    assert asyncio.run(orch.ask("tell me a joke"))[0].expert == "no_match"
