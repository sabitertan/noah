"""MCP servers as experts: adding one from pasted JSON, routing a message to it, the tool shortlist and pick, asking before a tool
that may change something, and saving images. The server is a real MCP server run in-process (mcp_client.INPROC), so the
client, the session and the result conversion are the real ones; only the small model is a stand-in."""
import base64
import json
import re
import types
import shutil
import sys
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver import Image, MCPServer
from mcp_types import ToolAnnotations

from noah import embedder, expert_files, mcp_client, mcp_expert, server

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def notes_server() -> MCPServer:
    srv = MCPServer("notes")
    saved: list[str] = []

    @srv.tool(description="Save a short note for later.")
    def add_note(text: str) -> str:
        saved.append(text)
        return f"saved note {len(saved)}"

    @srv.tool(description="List every saved note.", annotations=ToolAnnotations(read_only_hint=True))
    def list_notes() -> str:
        return "\n".join(saved) or "no notes yet"

    @srv.tool(description="Current weather and temperature for a city.", annotations=ToolAnnotations(read_only_hint=True))
    def get_weather(city: str) -> str:
        return f"{city}: 21 C, sunny"

    @srv.tool(description="Draw a tiny chart picture.", annotations=ToolAnnotations(read_only_hint=True))
    def draw_chart() -> Image:
        return Image(data=PNG, format="png")

    return srv


EXAMPLES = {
    "add_note": ["remember to buy milk", "note that the meeting moved to 3pm", "save a note: call Ana"],
    "list_notes": ["show my notes", "what did I write down", "read me my notes"],
    "get_weather": ["weather in Paris", "is it raining in Oslo", "how warm is it in Rome"],
    "draw_chart": ["draw a chart", "make me a picture of a chart", "plot a small graph"],
}


DESCRIPTIONS = {"Save a short note for later.": "add_note", "List every saved note.": "list_notes", "Draw a tiny chart picture.": "draw_chart",
                "Current weather and temperature for a city.": "get_weather", "Weather for a city": "get_weather"}


class FakeModel:
    """Answers the four kinds of request the MCP expert makes: write examples, name a tool (structured output), call a tool
    (tool calling), fill a tool's arguments."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.pick: tuple[str, dict[str, Any]] | None = None
        self.choice: str | None = None   # the tool it names; None: the first it is offered (the closest)

    def post(self, url: str, json: Any = None, timeout: Any = None, **_: Any) -> httpx.Response:
        self.requests.append(json)
        schema = (json.get("response_format") or {}).get("json_schema", {})
        message: dict[str, Any]
        if schema.get("name") == "examples":
            said = re.search(r"What the tool does: (.*)", json["messages"][1]["content"]).group(1)  # type: ignore[union-attr]
            message = {"content": _json({"examples": EXAMPLES[DESCRIPTIONS[said]]})}
        elif schema.get("name") == "pick":
            message = {"content": _json({"tool": self.choice or schema["schema"]["properties"]["tool"]["enum"][0]})}
        elif json.get("tools"):
            name, args = self.pick or ("none", {})
            message = {"content": None, "tool_calls": [{"function": {"name": name, "arguments": _json(args)}}]}
        else:   # the extractor, filling one tool's arguments
            text = json["messages"][-1]["content"]
            city = re.search(r"in (\w+)", text)
            message = {"content": _json({"city": city.group(1)} if city else {"text": text})}
        return httpx.Response(200, json={"choices": [{"message": message}]}, request=httpx.Request("POST", url))


def _json(v: Any) -> str:
    return json.dumps(v)


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> FakeModel:
    fake = FakeModel()
    monkeypatch.setattr(httpx, "post", fake.post)
    return fake


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: FakeModel) -> Iterator[Path]:
    """A Noah with the calc expert, an empty data folder, and the notes MCP server reachable as {"inproc": "notes"}."""
    (tmp_path / "experts").mkdir()
    shutil.copytree(Path("experts/calc"), tmp_path / "experts" / "calc", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(server, "EXPERTS_DIR", tmp_path / "experts")
    monkeypatch.setattr(server, "VERSIONS", tmp_path / "data" / "versions")
    monkeypatch.setattr(server, "DISABLED", tmp_path / "data" / "disabled.json")
    monkeypatch.setattr(server, "CODE_EDIT", "1")
    monkeypatch.setenv("NOAH_MCP_ALLOWED", str(tmp_path / "data" / "mcp_allowed.json"))
    monkeypatch.setenv("NOAH_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setitem(mcp_client.INPROC, "notes", notes_server())
    server._reload_experts()  # pyright: ignore[reportPrivateUsage]
    yield tmp_path
    mcp_client.close_all()   # the next test's server is a new one under the same name
    monkeypatch.undo()
    server._reload_experts()  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def client(home: Path) -> TestClient:
    return TestClient(server.app)


CODE = {"x-noah-code": "1"}
NOTES = json.dumps({"mcpServers": {"Notes": {"inproc": "notes"}}})


def add_notes(client: TestClient, model_id: str = "qwen2.5:0.5b") -> dict[str, Any]:
    r = client.post("/api/mcp/servers", json={"config": NOTES, "model": model_id}, headers=CODE)
    assert r.status_code == 200, r.text
    return r.json()


def ask(client: TestClient, message: str, **kw: Any) -> dict[str, Any]:
    body = {"message": message, "engine": "keyword", "extractor": "openai_compat", "model": "qwen2.5:0.5b", **kw}
    r = client.post("/api/ask", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ---- pasted configuration ----
def test_pasted_configs_in_the_usual_shapes() -> None:
    claude = mcp_expert.parse_config('{"mcpServers": {"GitHub Tools": {"url": "https://api.example.com/mcp", "headers": {"Authorization": "Bearer ${GH}"}}}}')
    assert claude == [("github_tools", {"url": "https://api.example.com/mcp", "headers": {"Authorization": "Bearer ${GH}"}})]
    vscode = mcp_expert.parse_config('{"servers": {"fs": {"type": "stdio", "command": "npx", "args": ["-y", "server-fs", "D:/notes"]}}}')
    assert vscode == [("fs", {"command": "npx", "args": ["-y", "server-fs", "D:/notes"]})]
    assert mcp_expert.parse_config('{"url": "http://localhost:8000/mcp"}', "local") == [("local", {"url": "http://localhost:8000/mcp"})]
    assert mcp_expert.expert_name("9 Lives!") == "mcp_9_lives"


@pytest.mark.parametrize("text, why", [
    ("not json", "not valid JSON"),
    ('{"url": "http://x/mcp"}', "name"),
    ('{"mcpServers": {"a": {"url": "ftp://x"}}}', "not an http"),
    ('{"mcpServers": {"a": {"type": "sse", "url": "http://x/sse"}}}', "SSE"),
    ('{"mcpServers": {"a": {"command": "npx", "args": "-y x"}}}', "args"),
    ('{"other": 1}', "No servers"),
])
def test_bad_configs_say_what_is_wrong(text: str, why: str) -> None:
    with pytest.raises(mcp_client.McpError, match=why):
        mcp_expert.parse_config(text)


def test_env_vars_fill_secrets_and_a_missing_one_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOAH_TEST_TOKEN", "abc")
    assert mcp_client._expand({"h": ["Bearer ${NOAH_TEST_TOKEN}"]}) == {"h": ["Bearer abc"]}  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(mcp_client.McpError, match="NOAH_NOT_SET"):
        mcp_client._expand("${NOAH_NOT_SET}")  # pyright: ignore[reportPrivateUsage]
    assert mcp_client.describe_server({"url": "https://x/mcp", "headers": {"Authorization": "secret"}}) == "https://x/mcp"


# ---- adding a server ----
def test_adding_a_server_makes_one_expert_with_its_tools_and_examples(client: TestClient, home: Path) -> None:
    out = add_notes(client)
    assert out["failed"] == [] and out["added"][0] | {"problems": []} == {
        "name": "notes", "server": "in-process notes", "tools": 4, "with_examples": 4, "problems": [], "sign_in": False, "url": ""}
    tools = json.loads((home / "experts" / "notes" / "tools.json").read_text())
    assert {t["name"]: t["examples"] for t in tools} == EXAMPLES
    assert next(t for t in tools if t["name"] == "list_notes")["annotations"]["read_only_hint"] is True
    card = next(e for e in client.get("/api/config").json()["experts"] if e["name"] == "notes")
    assert card["kind"] == "mcp" and sorted(card["tools"]) == sorted(EXAMPLES) and card["enabled"]
    assert any(v["note"] == "Added MCP server" for v in client.get("/api/experts/notes/versions").json())


def test_adding_needs_the_same_permission_as_code(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "CODE_EDIT", "")   # default: only someone at the computer running Noah
    assert client.post("/api/mcp/servers", json={"config": NOTES}, headers=CODE).status_code == 403


def test_without_a_model_tools_route_by_their_descriptions(client: TestClient, home: Path) -> None:
    out = add_notes(client, model_id="")
    assert out["added"][0]["with_examples"] == 0
    assert ask(client, "what is the weather and temperature in Paris")["results"][0]["output"] == "Paris: 21 C, sunny"


def test_a_name_that_is_taken_is_reported_not_overwritten(client: TestClient) -> None:
    add_notes(client)
    out = client.post("/api/mcp/servers", json={"config": NOTES}, headers=CODE).json()
    assert out["added"] == [] and "already exists" in out["failed"][0]["error"]


def test_an_unreachable_server_is_reported(client: TestClient) -> None:
    out = client.post("/api/mcp/servers", json={"config": '{"mcpServers": {"gone": {"command": "noah-no-such-program"}}}'}, headers=CODE).json()
    assert out["added"] == [] and "Could not connect" in out["failed"][0]["error"]


# ---- answering ----
def test_a_read_only_tool_runs_right_away(client: TestClient) -> None:
    add_notes(client)
    out = ask(client, "is it raining in Oslo")
    r = out["results"][0]
    assert out["decisions"][0]["expert"] == "notes"
    assert r["expert"] == "notes" and r["output"] == "Oslo: 21 C, sunny" and r["confirm"] is None
    assert r["args"] == {"tool": "get_weather", "arguments": {"city": "Oslo"}} and r["extractor"].startswith("mcp:get_weather")


def test_other_experts_still_win_their_messages(client: TestClient) -> None:
    add_notes(client)
    assert ask(client, "calculate 15% of 240")["results"][0]["expert"] == "calc"


def test_a_tool_that_may_change_something_asks_first(client: TestClient) -> None:
    add_notes(client)
    r = ask(client, "remember to buy milk")["results"][0]
    assert r["confirm"] == {"expert": "notes", "tool": "add_note", "arguments": {"text": "remember to buy milk"}}
    assert "wants to run **add_note**" in r["output"]
    assert ask(client, "show my notes")["results"][0]["output"] == "no notes yet"   # nothing ran
    ran = client.post("/api/mcp/run", json=r["confirm"]).json()
    assert ran["output"] == "saved note 1" and ran["error"] is None
    assert ask(client, "show my notes")["results"][0]["output"] == "remember to buy milk"


def test_always_allow_stops_asking_for_that_tool(client: TestClient) -> None:
    add_notes(client)
    r = ask(client, "remember to buy milk")["results"][0]
    client.post("/api/mcp/run", json={**r["confirm"], "always": True})
    again = ask(client, "note that the meeting moved to 3pm")["results"][0]
    assert again["confirm"] is None and again["output"] == "saved note 2"


def test_run_checks_the_tool_and_its_arguments(client: TestClient) -> None:
    add_notes(client)
    assert client.post("/api/mcp/run", json={"expert": "notes", "tool": "nope"}).status_code == 404
    assert client.post("/api/mcp/run", json={"expert": "notes", "tool": "add_note", "arguments": {}}).status_code == 400
    assert client.post("/api/mcp/run", json={"expert": "calc", "tool": "x"}).status_code == 404


def test_images_become_files_in_the_chat(client: TestClient, home: Path) -> None:
    add_notes(client)
    r = ask(client, "draw a chart")["results"][0]
    assert r["expert"] == "notes" and len(r["attachments"]) == 1
    f = r["attachments"][0]
    assert f["kind"] == "image" and f["path"].startswith("mcp/notes-") and (home / "files" / f["path"]).read_bytes() == PNG


def let_model_choose(client: TestClient) -> None:
    r = client.put("/api/experts/notes/manifest", json={"description": "Notes", "examples": ["take a note"], "model": "qwen2.5:0.5b", "choose_tool": True})
    assert r.status_code == 200 and r.json()["choose_tool"] is True


def test_a_tool_clearly_ahead_is_taken_without_asking_the_model(client: TestClient, model: FakeModel, monkeypatch: pytest.MonkeyPatch) -> None:
    add_notes(client)
    monkeypatch.setattr(mcp_expert, "CLEAR_LEAD", -1.0)   # always clear
    model.choice = "add_note"   # would be wrong; it is never asked
    r = ask(client, "is it raining in Oslo")["results"][0]
    assert r["args"]["tool"] == "get_weather" and r["extractor"] == "mcp:get_weather · closest tool, arguments by qwen2.5:0.5b"
    assert not any(q.get("tools") or (q.get("response_format") or {}).get("json_schema", {}).get("name") == "pick" for q in model.requests)


def test_when_the_closest_tools_are_close_the_model_names_one_of_them(client: TestClient, model: FakeModel, monkeypatch: pytest.MonkeyPatch) -> None:
    add_notes(client)
    monkeypatch.setattr(mcp_expert, "CLEAR_LEAD", 2.0)   # never clear
    model.choice = "get_weather"
    r = ask(client, "notes: is it raining in Oslo")["results"][0]
    assert r["args"] == {"tool": "get_weather", "arguments": {"city": "Oslo"}} and r["extractor"] == "mcp:get_weather · chosen by qwen2.5:0.5b"
    asked = next(q for q in model.requests if (q.get("response_format") or {}).get("json_schema", {}).get("name") == "pick")
    assert asked["messages"][1]["content"] == "is it raining in Oslo"   # without the expert's name
    assert set(asked["response_format"]["json_schema"]["schema"]["properties"]["tool"]["enum"]) <= set(EXAMPLES)
    assert not any(q.get("tools") for q in model.requests)


def test_when_allowed_and_the_closest_tools_are_close_the_model_chooses(client: TestClient, model: FakeModel, monkeypatch: pytest.MonkeyPatch) -> None:
    add_notes(client)
    let_model_choose(client)
    monkeypatch.setattr(mcp_expert, "CLEAR_LEAD", 2.0)   # never clear: always ask the model
    model.pick = ("list_notes", {"request": "notes please"})
    r = ask(client, "show my notes")["results"][0]
    assert r["args"] == {"tool": "list_notes", "arguments": {}}   # the stand-in "request" argument is dropped
    assert r["extractor"] == "mcp:list_notes · picked by qwen2.5:0.5b"
    picked = next(q for q in model.requests if q.get("tools"))
    assert {t["function"]["name"] for t in picked["tools"]} <= {*EXAMPLES, "none"} and len(picked["tools"]) <= mcp_expert.SHORTLIST + 1


def test_a_model_that_picks_nothing_falls_back_to_the_closest_tool(client: TestClient, model: FakeModel, monkeypatch: pytest.MonkeyPatch) -> None:
    add_notes(client)
    let_model_choose(client)
    monkeypatch.setattr(mcp_expert, "CLEAR_LEAD", 2.0)
    model.pick = ("none", {})
    assert ask(client, "weather in Paris")["results"][0]["output"] == "Paris: 21 C, sunny"


def test_examples_that_are_no_request_are_skipped_and_the_name_is_left_out() -> None:
    tool = {"name": "list_releases", "description": "List releases", "examples": [
        "I would type: list_releases owner/repo", "list_releases owner: octocat", "Enter your GitHub repository name, then the page",
        "list_commits -o my_repo -p 1", "list branches {owner=octocat}", "show the releases of octocat/hello-world",
        "show the releases of octocat/hello-world"]}
    assert mcp_expert.tool_expert(tool).examples == ["show the releases of octocat/hello-world"]
    assert mcp_expert._without("GitHub: list my repos", "github") == "list my repos"  # pyright: ignore[reportPrivateUsage]
    assert mcp_expert._without("list files on my_drive", "my_drive") == "list files on"  # pyright: ignore[reportPrivateUsage]
    assert mcp_expert._without("github", "github") == "github"  # pyright: ignore[reportPrivateUsage]


GH_TOOL = {"name": "list_branches", "input_schema": {"type": "object", "required": ["owner", "repo"], "properties": {
    "owner": {"type": "string"}, "repo": {"type": "string"}, "page": {"type": "number"}}}}


def test_placeholders_for_required_arguments_are_asked_for_not_sent() -> None:
    made_up = {"owner": "your_username", "repo": "<repository>"}
    assert mcp_expert.missing(made_up, GH_TOOL, "list my branches", {}) == ["owner", "repo"]
    assert mcp_expert.missing(made_up, GH_TOOL, "list my branches", {"owner": "sabitertan"}) == ["repo"]
    assert mcp_expert.missing({"owner": "octocat", "repo": "hello-world"}, GH_TOOL, "branches of octocat/hello-world", {}) == []
    assert mcp_expert.missing({"owner": "my-org"}, GH_TOOL, "branches of my-org's noah", {}) == ["repo"]   # said, but repo is not given


def test_shortlist_without_embeddings_uses_keywords(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedder, "AVAILABLE", False)
    tools = [{"name": n, "description": d, "examples": EXAMPLES[n]} for n, d in
             [("add_note", "Save a note"), ("get_weather", "Weather for a city"), ("list_notes", "List notes")]]
    assert mcp_expert.shortlist("is it raining in Oslo", tools)[0][1]["name"] == "get_weather"


# ---- keeping it up to date, and safe ----
def test_refresh_keeps_examples_of_unchanged_tools(client: TestClient, home: Path, model: FakeModel) -> None:
    add_notes(client)
    path = home / "experts" / "notes" / "tools.json"
    tools = json.loads(path.read_text())
    tools[0]["examples"] = ["my own example"]
    path.write_text(json.dumps(tools))
    server._reload_experts()  # pyright: ignore[reportPrivateUsage]
    before = len(model.requests)
    out = client.post("/api/mcp/notes/refresh", json={}).json()
    assert out == {"tools": 4, "with_examples": 4, "problems": []}
    assert json.loads(path.read_text())[0]["examples"] == ["my own example"] and len(model.requests) == before   # nothing re-written
    out = client.post("/api/mcp/notes/refresh", json={"rewrite": True}).json()
    assert out["with_examples"] == 4 and json.loads(path.read_text())[0]["examples"] == EXAMPLES["add_note"]


def test_changing_the_server_in_the_editor_counts_as_code() -> None:
    yaml_a = "name: notes\nkind: mcp\nserver: {command: npx}\n"
    assert expert_files.code_changed({"expert.yaml": yaml_a}, {"expert.yaml": yaml_a.replace("npx", "evil")})
    assert not expert_files.code_changed({"expert.yaml": yaml_a}, {"expert.yaml": yaml_a + "description: other\n"})


def test_an_mcp_expert_can_be_deleted_and_edited(client: TestClient, home: Path) -> None:
    add_notes(client)
    m = client.get("/api/experts/notes/manifest").json()
    assert m["editable"] == "mcp" and m["server"] == "in-process notes" and len(m["tools"]) == 4
    r = client.put("/api/experts/notes/manifest", json={"description": "My notes and the weather", "examples": ["take a note"], "model": "qwen2.5:1.5b"})
    assert r.status_code == 200 and r.json()["model"] == "qwen2.5:1.5b"
    assert client.delete("/api/experts/notes").status_code == 200 and not (home / "experts" / "notes").exists()


class BrowserPage:
    """Stands in for Pyodide (run_sync) and the page's MCP client (js.noahMcp): records each request, answers from `replies`."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, jspi: bool = True, **replies: Any) -> None:
        self.sent: list[dict[str, Any]] = []
        ffi, js = types.ModuleType("pyodide.ffi"), types.ModuleType("js")
        ffi.can_run_sync = lambda: jspi  # type: ignore[attr-defined]
        ffi.run_sync = lambda answer: answer  # type: ignore[attr-defined]
        js.noahMcp = lambda body, ms: (self.sent.append(json.loads(body)), json.dumps(replies[json.loads(body)["op"]]))[1]  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pyodide", types.ModuleType("pyodide"))
        monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi)
        monkeypatch.setitem(sys.modules, "js", js)
        monkeypatch.setattr(mcp_client, "IN_BROWSER", True)


def test_in_the_browser_without_jspi_it_says_which_browser(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    BrowserPage(monkeypatch, jspi=False)
    r = client.post("/api/mcp/servers", json={"config": NOTES}, headers=CODE)
    assert r.status_code == 400 and "JSPI" in r.json()["detail"]
    assert client.get("/api/config").json()["mcp"]["available"] is False


def test_in_the_browser_the_page_lists_and_calls_tools(client: TestClient, monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    tools = [{"name": "get_weather", "description": "Weather for a city", "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}},
              "annotations": {"readOnlyHint": True}}]
    result = {"content": [{"type": "text", "text": "Oslo: 3 C"}, {"type": "image", "data": base64.b64encode(PNG).decode(), "mimeType": "image/png"}]}
    page = BrowserPage(monkeypatch, list_tools={"result": tools}, call_tool={"result": result})
    config = json.dumps({"mcpServers": {"wx": {"url": "https://mcp.example.com/mcp"}}})
    out = client.post("/api/mcp/servers", json={"config": config, "model": "qwen2.5:0.5b"}, headers=CODE).json()
    assert out["added"][0]["tools"] == 1
    saved = json.loads((home / "experts" / "wx" / "tools.json").read_text())[0]
    assert saved["input_schema"]["properties"] == {"city": {"type": "string"}} and saved["annotations"] == {"read_only_hint": True}
    r = ask(client, "is it raining in Oslo")["results"][0]
    assert r["output"] == "Oslo: 3 C" and r["attachments"][0]["kind"] == "image"
    assert page.sent[-1] == {"op": "call_tool", "server": {"url": "https://mcp.example.com/mcp"}, "tool": "get_weather", "arguments": {"city": "Oslo"}}


def test_in_the_browser_a_server_that_needs_sign_in_is_added_for_the_page_to_sign_in(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    BrowserPage(monkeypatch, list_tools={"error": "https://mcp.example.com/mcp needs you to sign in.", "sign_in": True})
    config = json.dumps({"mcpServers": {"wx": {"url": "https://mcp.example.com/mcp"}}})
    out = client.post("/api/mcp/servers", json={"config": config}, headers=CODE).json()
    assert out["added"][0] | {"problems": []} == {"name": "wx", "server": "https://mcp.example.com/mcp", "tools": 0, "with_examples": 0,
                                                  "problems": [], "sign_in": True, "url": "https://mcp.example.com/mcp"}


def test_a_local_server_runs_as_a_child_process(client: TestClient) -> None:
    """stdio: the transport of `npx …` / `uvx …` servers. The server stays running between calls."""
    script = Path(__file__).parent / "mcp_servers" / "echo_stdio.py"
    config = json.dumps({"mcpServers": {"echo": {"command": sys.executable, "args": [str(script)]}}})
    out = client.post("/api/mcp/servers", json={"config": config, "model": "qwen2.5:0.5b"}, headers=CODE).json()
    assert out["failed"] == [] and out["added"][0]["tools"] == 1
    echo = server.experts["echo"]
    assert mcp_expert.call(echo, "shout", {"text": "hi"})["text"] == "HI"
    assert mcp_expert.call(echo, "shout", {"text": "again"})["text"] == "AGAIN"


# ---- arguments come from the message, or from the expert's defaults ----
FS_TOOL = {"name": "read_text_file", "input_schema": {"type": "object", "required": ["path"], "properties": {
    "path": {"type": "string"}, "head": {"type": "number"}, "tail": {"type": "number"}}}}


def test_made_up_values_are_dropped_or_replaced_by_defaults() -> None:
    made_up = {"path": "C:/Users/username/Documents", "head": 10, "tail": 5}
    assert mcp_expert.ground(made_up, FS_TOOL, "read my shopping list", {"path": "D:/notes"}) == {"path": "D:/notes"}
    assert mcp_expert.ground({"path": "shopping.txt", "head": 10}, FS_TOOL, "first 10 lines of Shopping.txt", {"path": "D:/notes"}) == {
        "path": "shopping.txt", "head": 10}
    assert mcp_expert.ground({"path": "C:/x"}, FS_TOOL, "read it", {}) == {"path": "C:/x"}   # required, no default: the tool will say
    assert mcp_expert.ground({}, FS_TOOL, "read it", {"path": "D:/notes"}) == {"path": "D:/notes"}


def test_defaults_are_set_in_the_settings_and_fill_a_tool_without_a_model(client: TestClient) -> None:
    add_notes(client, model_id="")
    r = client.put("/api/experts/notes/manifest", json={"description": "Notes", "examples": ["x"], "defaults": {"city": "Lisbon"}})
    assert r.status_code == 200 and r.json()["defaults"] == {"city": "Lisbon"}
    assert ask(client, "what is the weather and temperature right now", extractor="rule")["results"][0]["output"] == "Lisbon: 21 C, sunny"
    # with a model that finds no city in the message, the default fills it too
    assert ask(client, "what is the weather and temperature right now")["results"][0]["output"] == "Lisbon: 21 C, sunny"


def test_without_a_model_or_default_a_tool_that_needs_arguments_says_so(client: TestClient) -> None:
    add_notes(client, model_id="")
    r = ask(client, "what is the weather and temperature right now", extractor="rule")["results"][0]
    assert r["error"] and "no model is set" in r["output"]


def test_refresh_rewrites_routing_text_noah_wrote_but_not_the_users(client: TestClient, home: Path) -> None:
    add_notes(client)
    assert server.experts["notes"].description.startswith("Tools from the notes MCP server")
    client.put("/api/experts/notes/manifest", json={"description": "My notes and the weather", "examples": ["take a note"], "model": "qwen2.5:0.5b"})
    client.post("/api/mcp/notes/refresh", json={})
    assert server.experts["notes"].description == "My notes and the weather" and server.experts["notes"].examples == ["take a note"]


def test_when_the_models_pick_lacks_arguments_the_closest_tool_is_tried(monkeypatch: pytest.MonkeyPatch) -> None:
    search = {"name": "search_repositories", "annotations": {"read_only_hint": True}, "input_schema": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}}}
    expert = types.SimpleNamespace(name="github", tools=[search, GH_TOOL], model={"id": "m"}, defaults={})
    monkeypatch.setattr(mcp_expert, "shortlist", lambda *a, **k: [(0.83, search), (0.80, GH_TOOL)])
    monkeypatch.setattr(mcp_expert, "_choose", lambda *a: GH_TOOL)
    fills = {"list_branches": {"owner": "your_username", "repo": "your_repo"}, "search_repositories": {"query": "user:sabitertan"}}
    monkeypatch.setattr(mcp_expert, "_fill", lambda message, tool, *a: fills[tool["name"]])
    monkeypatch.setattr(mcp_expert, "call", lambda e, tool, args: {"text": f"{tool} {args}", "files": []})
    extractor = types.SimpleNamespace(name="rule")
    out = mcp_expert.run(expert, "github list repos of sabitertan", extractor, "")  # type: ignore[arg-type]
    assert out["text"] == "search_repositories {'query': 'user:sabitertan'}" and "closest tool" in out["label"]
    fills["search_repositories"] = {"query": "user:your_username"}
    with pytest.raises(mcp_client.McpError, match=r"search_repositories needs \*\*query\*\* \(the model wrote “user:your_username”\)"):
        mcp_expert.run(expert, "github list my repos", extractor, "")  # type: ignore[arg-type]
