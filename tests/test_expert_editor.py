"""Creating, editing and deleting experts from the UI. Generate experts are data and fully editable; tool experts run code and are not."""
import shutil
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from noah import expert_store, server
from tests.fakes import anycall

GOOD: dict[str, Any] = {"name": "pirate", "description": "Answers in pirate speak.", "examples": ["talk like a pirate", "say it like a pirate would"],
        "model": "qwen2.5:0.5b", "system": "You are a pirate. Answer briefly, in pirate speak.", "license": "user"}


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    dst = tmp_path / "experts"
    shutil.copytree(Path("experts"), dst, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(server, "EXPERTS_DIR", dst)
    monkeypatch.setattr(server, "DISABLED", tmp_path / "disabled.json")
    monkeypatch.setattr(server, "PROFILES", tmp_path / "profiles.json")
    server._reload_experts()  # pyright: ignore[reportPrivateUsage]
    yield dst
    monkeypatch.undo()
    server._reload_experts()  # pyright: ignore[reportPrivateUsage]  (leave the real experts loaded for the other test files)


@pytest.fixture
def client(root: Path) -> TestClient:
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


def names(client: TestClient):
    return [e["name"] for e in client.get("/api/config").json()["experts"]]


# ---- create ----
def test_a_new_generate_expert_is_written_loaded_and_visible(client: TestClient, root: Path) -> None:
    r = client.post("/api/experts", json=GOOD)
    assert r.status_code == 201 and r.json()["kind"] == "generate" and r.json()["editable"] == "all"
    m = yaml.safe_load((root / "pirate" / "expert.yaml").read_text())
    assert m["name"] == "pirate" and m["kind"] == "generate" and m["model"] == {"backend": "openai_compat", "id": "qwen2.5:0.5b"}
    assert m["examples"] == GOOD["examples"] and "tool" not in m                      # never a tool: no code path from the browser
    assert "pirate" in names(client)
    card = next(e for e in client.get("/api/config").json()["experts"] if e["name"] == "pirate")
    assert card["kind"] == "generate" and card["enabled"] is True and card["model"] == "qwen2.5:0.5b"


def test_the_new_expert_is_routed_to_without_a_restart(client: TestClient) -> None:
    client.post("/api/experts", json=GOOD)
    from noah.engines import make_engine
    best = make_engine("keyword").decide("talk like a pirate", server._active_experts())[0]  # pyright: ignore[reportPrivateUsage]
    assert best.expert == "pirate"


def test_prompts_with_special_characters_round_trip(client: TestClient, root: Path) -> None:
    nasty = 'Line one: "quoted"\n  - not a list\n# not a comment\nunicode: café ✓ {braces} [brackets] & ampersand'
    client.post("/api/experts", json={**GOOD, "system": nasty, "examples": ["what's up: a 'test'", "x # y", "z: w"]})
    m = client.get("/api/experts/pirate/manifest").json()
    assert m["system"] == nasty and m["examples"] == ["what's up: a 'test'", "x # y", "z: w"]
    assert "\n" in (root / "pirate" / "expert.yaml").read_text() and len(server.experts) >= 8   # file still loads with everything else


def test_shots_and_strip_are_saved_only_when_given(client: TestClient, root: Path) -> None:
    client.post("/api/experts", json=GOOD)
    assert "shots" not in yaml.safe_load((root / "pirate" / "expert.yaml").read_text())
    client.put("/api/experts/pirate/manifest", json={**GOOD, "shots": [{"user": "hi", "assistant": "Arr!"}], "strip": [r"```\w*"]})
    m = yaml.safe_load((root / "pirate" / "expert.yaml").read_text())
    assert m["shots"] == [{"user": "hi", "assistant": "Arr!"}] and m["strip"] == ["```\\w*"]
    client.put("/api/experts/pirate/manifest", json=GOOD)                                # clearing removes the keys again
    assert "shots" not in yaml.safe_load((root / "pirate" / "expert.yaml").read_text())


# ---- validation ----
@pytest.mark.parametrize("name", ["../evil", "a/b", "..", "Pirate", "1pirate", "p", "x" * 40, "has space", "none", "new", "pi-rate", ""])
def test_bad_names_never_touch_the_disk(client: TestClient, root: Path, name: str) -> None:
    before = sorted(p.name for p in root.parent.iterdir()) + sorted(p.name for p in root.iterdir())
    r = client.post("/api/experts", json={**GOOD, "name": name})
    assert r.status_code == 400
    assert sorted(p.name for p in root.parent.iterdir()) + sorted(p.name for p in root.iterdir()) == before


@pytest.mark.parametrize("patch,words", [
    ({"description": ""}, "description"), ({"examples": []}, "examples"), ({"examples": ["  ", ""]}, "example"),
    ({"examples": ["x" * 201]}, "200"), ({"examples": ["a"] * 21}, "examples"), ({"strip": ["(unclosed"]}, "regular expression"),
    ({"strip": ["x" * 201]}, "200"), ({"shots": [{"user": "", "assistant": "a"}]}, "shots"), ({"system": "x" * 4001}, "system"),
    ({"model": ""}, "model"), ({"system": "  "}, "system prompt"),
])
def test_invalid_fields_are_refused_with_a_reason(client: TestClient, patch: Any, words: str) -> None:
    r = client.post("/api/experts", json={**GOOD, **patch})
    assert r.status_code in (400, 422) and words in r.text.lower() and "pirate" not in names(client)


def test_duplicate_and_built_in_names_are_refused(client: TestClient) -> None:
    assert client.post("/api/experts", json=GOOD).status_code == 201
    assert client.post("/api/experts", json=GOOD).status_code == 409
    assert client.post("/api/experts", json={**GOOD, "name": "calc"}).status_code == 409


# ---- edit ----
def test_editing_a_generate_expert_changes_its_prompt_model_and_routing(client: TestClient, root: Path) -> None:
    client.post("/api/experts", json=GOOD)
    r = client.put("/api/experts/pirate/manifest", json={**GOOD, "description": "Pirate talk.", "examples": ["arr"], "model": "hf:org/model", "system": "New prompt."})
    assert r.status_code == 200
    card = next(e for e in client.get("/api/config").json()["experts"] if e["name"] == "pirate")
    assert card["description"] == "Pirate talk." and card["examples"] == ["arr"] and card["model"] == "hf:org/model"
    assert client.get("/api/experts/pirate/manifest").json()["system"] == "New prompt."


def test_a_name_cannot_be_changed_and_unknown_experts_are_404(client: TestClient) -> None:
    client.post("/api/experts", json=GOOD)
    assert client.put("/api/experts/pirate/manifest", json={**GOOD, "name": "buccaneer"}).status_code == 400
    assert client.put("/api/experts/ghost/manifest", json=GOOD).status_code == 404
    assert client.get("/api/experts/ghost/manifest").status_code == 404
    assert client.get("/api/experts/..%2Fcalc/manifest").status_code in (400, 404)


def test_a_tool_expert_only_lets_you_change_how_it_is_routed(client: TestClient, root: Path) -> None:
    tool_py = (root / "calc" / "tool.py").read_text()
    before = yaml.safe_load((root / "calc" / "expert.yaml").read_text())
    m = client.get("/api/experts/calc/manifest").json()
    assert m["kind"] == "tool" and m["editable"] == "routing"
    r = client.put("/api/experts/calc/manifest", json={"description": "Does maths.", "examples": ["add 2 and 2"], "system": "ignore me", "model": "evil", "strip": ["x"], "license": "GPL"})
    assert r.status_code == 200
    after = yaml.safe_load((root / "calc" / "expert.yaml").read_text())
    assert after["description"] == "Does maths." and after["examples"] == ["add 2 and 2"]
    for key in ("kind", "parameters", "model", "license", "name"):                    # everything else is untouched
        assert after.get(key) == before.get(key), key
    assert "system" not in after and "strip" not in after
    assert (root / "calc" / "tool.py").read_text() == tool_py                          # and the code is never written
    assert next(e for e in client.get("/api/config").json()["experts"] if e["name"] == "calc")["kind"] == "tool"


def test_no_request_can_add_code_to_an_expert(client: TestClient, root: Path) -> None:
    client.post("/api/experts", json={**GOOD, "system": "x", "name": "pirate", "tool": "tool.py", "kind": "tool"})
    assert yaml.safe_load((root / "pirate" / "expert.yaml").read_text())["kind"] == "generate"
    assert not (root / "pirate" / "tool.py").exists() and "tool" not in yaml.safe_load((root / "pirate" / "expert.yaml").read_text())


# ---- delete ----
def test_deleting_a_generate_expert_removes_it_and_its_switch_state(client: TestClient, root: Path) -> None:
    client.post("/api/experts", json=GOOD)
    client.put("/api/experts/pirate", json={"enabled": False})
    assert client.delete("/api/experts/pirate").json() == {"deleted": "pirate"}
    assert "pirate" not in names(client) and not (root / "pirate").exists()
    assert "pirate" not in server._disabled()  # pyright: ignore[reportPrivateUsage]


def test_deleting_keeps_files_it_did_not_write_and_refuses_tool_experts(client: TestClient, root: Path) -> None:
    client.post("/api/experts", json=GOOD)
    (root / "pirate" / "notes.txt").write_text("mine")
    client.delete("/api/experts/pirate")
    assert (root / "pirate" / "notes.txt").read_text() == "mine" and not (root / "pirate" / "expert.yaml").exists()
    r = client.delete("/api/experts/calc")
    assert r.status_code == 400 and "calc" in names(client) and (root / "calc" / "tool.py").exists()
    assert client.delete("/api/experts/ghost").status_code == 404


def test_a_failed_write_leaves_the_old_manifest(client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client.post("/api/experts", json=GOOD)
    before = (root / "pirate" / "expert.yaml").read_text()
    def replace(*a: Any) -> None:
        raise OSError("disk full")
    monkeypatch.setattr(expert_store.os, "replace", replace)
    with pytest.raises(OSError):
        expert_store.update(root, "pirate", expert_store.ExpertIn.model_validate({**GOOD, "description": "changed"}))
    monkeypatch.undo()
    assert (root / "pirate" / "expert.yaml").read_text() == before and not list((root / "pirate").glob(".expert-*"))


# ---- try it ----
def test_try_it_runs_the_draft_without_saving_and_applies_the_output_filter(client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    def post(url: str, **kw: Any) -> httpx.Response:
        sent.update(url=url, body=kw["json"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "```text\nArr, matey!\n```\n\n\n\nbye"}}]}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    r = client.post("/api/experts/preview", json={**GOOD, "message": "hello", "strip": [r"```\w*"], "shots": [{"user": "a", "assistant": "b"}]})
    j = r.json()
    assert j["ok"] is True and "```" not in j["output"] and j["output"].startswith("Arr, matey!") and "\n\n\n" not in j["output"]
    assert sent["body"]["model"] == "qwen2.5:0.5b" and sent["body"]["messages"][0] == {"role": "system", "content": GOOD["system"]}
    assert [m["content"] for m in sent["body"]["messages"][1:]] == ["a", "b", "hello"]                      # shots, then the message
    assert not (root / "pirate").exists() and "pirate" not in names(client)                                  # nothing was saved


def test_try_it_needs_a_model_and_prompt_and_reports_a_dead_model_plainly(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.post("/api/experts/preview", json={**GOOD, "message": "hi", "model": ""}).status_code == 400
    monkeypatch.setattr(httpx, "post", anycall(lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down"))))
    j = client.post("/api/experts/preview", json={**GOOD, "message": "hi"}).json()
    assert j["ok"] is False and "model" in j["error"].lower()
