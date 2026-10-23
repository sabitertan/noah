import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noah import builder, generate, server
from noah.registry import load_experts
from noah.types import Expert

from .fakes import anycall, reply

MARKUP = '<h1>Todos</h1>\n<input id="new"><button id="add">Add</button>\n<ul id="list"></ul>'
STYLES = "body { font-family: sans-serif; }"
SCRIPT = "document.querySelector('#add').addEventListener('click', () => {}); // </script> stays inside"
PLAN = {"title": "Todo list", "summary": "Keep a list of things to do.", "features": ["add a todo", "tick it off"],
        "steps": [{"expert": "javascript", "task": "add and tick todos"}, {"expert": "css", "task": "a clean look"},
                  {"expert": "html", "task": "an input, a button and a list"}, {"expert": "weather", "task": "ignored"}]}


@pytest.fixture(scope="module")
def experts() -> dict[str, Expert]:
    return load_experts("experts")


def fake_models(monkeypatch: pytest.MonkeyPatch, planner: str = json.dumps(PLAN)) -> list[dict[str, Any]]:
    """The planner answers `planner`; each expert answers with its part in a fenced block, like the chat experts do."""
    calls: list[dict[str, Any]] = []

    def plan_chat(url: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        calls.append(body)
        return reply(planner)

    def expert_chat(url: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        calls.append(body)
        system = body["messages"][0]["content"]
        lang, code = ("css", STYLES) if "CSS expert" in system else ("js", SCRIPT) if "JavaScript expert" in system else ("html", MARKUP)
        return reply(f"Here it is.\n\n```{lang}\n{code}\n```")

    monkeypatch.setattr(builder, "chat", anycall(plan_chat))
    monkeypatch.setattr(generate, "chat", anycall(expert_chat))
    return calls


def test_builders_are_the_experts_that_produce_a_part(experts: dict[str, Expert]) -> None:
    assert [(n, e.produces) for n, e in builder.build_experts(experts).items()] == [("html", "html"), ("css", "css"), ("javascript", "js")]


def test_plan_keeps_known_experts_in_build_order(experts: dict[str, Expert], monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_models(monkeypatch)
    p = builder.plan("a todo app", experts, "http://x/v1")
    assert [s["expert"] for s in p["steps"]] == ["html", "css", "javascript"]  # markup first, the weather step dropped
    assert p["title"] == "Todo list" and p["features"] == ["add a todo", "tick it off"]
    assert p["planner"] == "qwen2.5-coder:1.5b"  # no planner chosen: the markup expert's model plans
    assert calls[0]["response_format"]["json_schema"]["schema"]["properties"]["steps"]["items"]["properties"]["expert"]["enum"] == ["html", "css", "javascript"]


@pytest.mark.parametrize("answer", ["not json", json.dumps({"title": "x", "steps": []})])
def test_a_useless_plan_still_builds_the_markup(experts: dict[str, Expert], monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    fake_models(monkeypatch, planner=answer)
    steps = builder.plan("a stopwatch", experts, "http://x/v1")["steps"]
    assert steps[0] == {"expert": "html", "part": "html", "task": "a stopwatch"}


def test_without_a_markup_expert_nothing_can_be_built(experts: dict[str, Expert], monkeypatch: pytest.MonkeyPatch) -> None:
    fake_models(monkeypatch)
    with pytest.raises(builder.BuildError, match="HTML"):
        builder.plan("a todo app", {n: e for n, e in experts.items() if n != "html"}, "http://x/v1")


def test_later_parts_see_the_markup(experts: dict[str, Expert], monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_models(monkeypatch)
    html, p, steps = builder.build("a todo app", experts, "http://x/v1")
    assert [s["part"] for s in steps] == ["html", "css", "js"] and not any(s["error"] for s in steps)
    assert MARKUP not in calls[1]["messages"][-1]["content"]          # the markup step has nothing to look at yet
    assert all(MARKUP in c["messages"][-1]["content"] for c in calls[2:])  # styles and script are written against it
    assert calls[1]["max_tokens"] == builder.STEP_TOKENS
    assert "<title>Todo list</title>" in html and MARKUP in html and STYLES in html
    assert "<\\/script> stays inside" in html and html.count("</script>") == 1  # a part can't close its own tag early


def test_extract_code_handles_whole_pages_and_cut_off_answers() -> None:
    page = "```html\n<!DOCTYPE html><html><head><link rel=stylesheet href=x.css></head><body><p>hi</p><script>x()</script></body></html>\n```"
    assert builder.extract_code(page, "html") == "<p>hi</p>"
    assert builder.extract_code("Sure:\n```javascript\nlet a = 1;", "js") == "let a = 1;"  # hit the token limit before the closing fence
    assert builder.extract_code("```css\na{}\n```\n```html\n<b>\n```", "css") == "a{}"
    assert builder.extract_code("<style>p { color: red }</style>", "css") == "p { color: red }"


def test_build_endpoints_run_a_whole_build_and_trace_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_models(monkeypatch)
    monkeypatch.setattr(server, "DISABLED", tmp_path / "disabled_experts.json")
    monkeypatch.setattr(server, "trace", server.TraceStore(tmp_path / "t.jsonl"))
    client = TestClient(server.app, base_url="http://127.0.0.1:8765")
    p = client.post("/api/build/plan", json={"message": "a todo app", "model": "planner:7b"}).json()["plan"]
    assert p["planner"] == "planner:7b"
    parts: dict[str, str] = {}
    steps: list[dict[str, Any]] = []
    for i in range(len(p["steps"])):
        s = client.post("/api/build/step", json={"plan": p, "index": i, "parts": parts}).json()
        parts[s["part"]] = s["code"]
        steps.append(s)
    html = client.post("/api/build/finish", json={"message": "a todo app", "plan": p, "parts": parts, "steps": steps, "total_ms": 5}).json()["html"]
    assert MARKUP in html and SCRIPT.split("//")[0] in html
    record = server.trace.read()[-1]
    assert record["mode"] == "build" and record["engine"] == "planner:planner:7b"
    assert [r["expert"] for r in record["results"]] == ["html", "css", "javascript"] and "code" not in record["results"][0]
    assert client.post("/api/build/step", json={"plan": p, "index": 7}).status_code == 400


def test_builder_is_a_default_profile_and_build_a_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "PROFILES", tmp_path / "profiles.json")
    client = TestClient(server.app, base_url="http://127.0.0.1:8765")
    assert {p["name"]: p["mode"] for p in client.get("/api/profiles").json()}["Builder"] == "build"
    assert "build" in client.get("/api/config").json()["modes"]


def test_a_saved_list_from_before_build_mode_gains_a_builder_and_keeps_the_rest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    saved = [{"name": "Default", "engine": "embedding", "mode": "route", "extractor": "openai_compat", "model": "qwen2.5:0.5b", "threshold": 0.63}]
    (tmp_path / "profiles.json").write_text(json.dumps(saved))
    monkeypatch.setattr(server, "PROFILES", tmp_path / "profiles.json")
    client = TestClient(server.app, base_url="http://127.0.0.1:8765")
    got = client.get("/api/profiles").json()
    assert got[0] == saved[0] and [(p["name"], p["mode"]) for p in got[1:]] == [("Builder", "build")]
    assert json.loads((tmp_path / "profiles.json").read_text()) == saved  # a read never rewrites the user's file


# ---- changing an existing app instead of starting over ----
BASE = {"plan": {"title": "Recipe book", "summary": "Recipes with search.", "features": ["search recipes"]},
        "parts": {"html": MARKUP, "css": STYLES, "js": SCRIPT}}


def test_a_change_rewrites_only_the_parts_the_planner_names(experts: dict[str, Expert], monkeypatch: pytest.MonkeyPatch) -> None:
    change = {"title": "Blue Background Web App", "summary": "x", "features": ["Search recipes", "blue background"],
              "steps": [{"expert": "css", "task": "make the page background blue"}]}
    calls = fake_models(monkeypatch, planner=json.dumps(change))
    html, p, steps = builder.build("make background blue", experts, "http://x/v1", base=BASE)
    assert p["title"] == "Recipe book" and p["change"] == "make background blue"  # the app keeps its name
    assert p["features"] == ["search recipes", "blue background"]                  # kept, plus the new one (case-insensitive)
    assert [s["part"] for s in steps] == ["css"]
    planner_ask = calls[0]["messages"][-1]["content"]
    assert MARKUP in planner_ask and "make background blue" in planner_ask and "changes to a small web app" in calls[0]["messages"][0]["content"]
    css_ask = calls[1]["messages"][-1]["content"]
    assert STYLES in css_ask and MARKUP in css_ask and "Return the complete updated CSS" in css_ask
    assert MARKUP in html and SCRIPT.split("//")[0] in html  # untouched parts carry over


def test_a_change_with_no_usable_plan_revises_every_part(experts: dict[str, Expert], monkeypatch: pytest.MonkeyPatch) -> None:
    fake_models(monkeypatch, planner="not json")
    p = builder.plan("add a dark mode", experts, "http://x/v1", base=BASE)
    assert [s["part"] for s in p["steps"]] == ["html", "css", "js"] and p["base_parts"] == BASE["parts"]


def test_a_rewrite_that_lost_most_of_the_part_keeps_the_current_one(experts: dict[str, Expert], monkeypatch: pytest.MonkeyPatch) -> None:
    long_js = SCRIPT + "\n" + "\n".join(f"function f{i}() {{ return {i}; }}" for i in range(40))
    monkeypatch.setattr(generate, "chat", anycall(lambda url, body, t: reply("```js\nbody.style.background = 'blue';\n```")))
    p = {"title": "T", "summary": "s", "change": "blue", "steps": [{"expert": "javascript", "part": "js", "task": "blue"}]}
    s = builder.run_step(p, 0, {"html": MARKUP, "js": long_js}, experts, "http://x/v1")
    assert s["code"] == long_js and "kept the current JS" in s["error"]


def test_a_version_saved_without_its_parts_is_split_back_into_them() -> None:
    page = builder.assemble({"title": "T"}, BASE["parts"])
    assert builder.split(page) == BASE["parts"]
