import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from noah import server


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(server, "PROFILES", tmp_path / "profiles.json")
    monkeypatch.setattr(server, "DISABLED", tmp_path / "disabled_experts.json")
    monkeypatch.setattr(server, "trace", server.TraceStore(tmp_path / "t.jsonl"))
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


def names(client: TestClient):
    return {p["name"]: p.get("enabled", True) for p in client.get("/api/profiles").json()}


def test_defaults_are_enabled_and_untouched_by_reads(client: TestClient, tmp_path: Path) -> None:
    assert all(names(client).values())
    assert not (tmp_path / "profiles.json").exists()  # reading defaults must not create the file


def test_toggle_persists_and_keeps_other_fields(client: TestClient, tmp_path: Path) -> None:
    r = client.put("/api/profiles/Nemotron orchestrator", json={"enabled": False})
    assert r.status_code == 200 and r.json() == {"name": "Nemotron orchestrator", "enabled": False}
    assert names(client)["Nemotron orchestrator"] is False and names(client)["Default"] is True
    saved = {p["name"]: p for p in json.loads((tmp_path / "profiles.json").read_text())}
    assert saved["Nemotron orchestrator"]["engine"] == "llm_tools" and saved["Default"]["threshold"] == 0.63
    assert client.put("/api/profiles/Nemotron orchestrator", json={"enabled": True}).status_code == 200
    assert names(client)["Nemotron orchestrator"] is True


def test_cannot_disable_the_last_enabled_orchestrator(client: TestClient) -> None:
    others = [n for n in names(client) if n != "Default"]
    for n in others:
        assert client.put(f"/api/profiles/{n}", json={"enabled": False}).status_code == 200
    r = client.put("/api/profiles/Default", json={"enabled": False})
    assert r.status_code == 400 and "At least one" in r.json()["detail"]
    assert names(client)["Default"] is True


def test_unknown_profile_is_404_and_names_may_contain_slashes(client: TestClient) -> None:
    assert client.put("/api/profiles/nope", json={"enabled": False}).status_code == 404
    profs = client.get("/api/profiles").json() + [{"name": "a/b c", "engine": "keyword", "mode": "route",
                                                    "extractor": "rule", "model": "", "threshold": 0.1}]
    assert client.put("/api/profiles", json=profs).status_code == 200
    assert client.put("/api/profiles/a/b c", json={"enabled": False}).status_code == 200
    assert names(client)["a/b c"] is False


def test_full_replace_must_keep_one_enabled(client: TestClient) -> None:
    profs = [{**p, "enabled": False} for p in client.get("/api/profiles").json()]
    assert client.put("/api/profiles", json=profs).status_code == 400
    assert all(names(client).values())  # nothing was written


def test_profiles_saved_before_the_switch_count_as_enabled(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "profiles.json").write_text(json.dumps([{"name": "Old", "engine": "keyword", "mode": "route",
                                                          "extractor": "rule", "model": "", "threshold": 0.1}]))
    assert names(client) == {"Old": True, "Builder": True}  # a list from before build mode also gets the builder


ASK = {"message": "calculate 2 + 2", "engine": "keyword", "mode": "route", "extractor": "rule", "threshold": 0.1}


def test_ask_refuses_a_disabled_profile_but_serves_enabled_and_unnamed(client: TestClient) -> None:
    client.put("/api/profiles/Nemotron orchestrator", json={"enabled": False})
    r = client.post("/api/ask", json={**ASK, "profile": "Nemotron orchestrator"})
    assert r.status_code == 409 and "disabled" in r.json()["detail"]
    ok = client.post("/api/ask", json={**ASK, "profile": "Default"})
    assert ok.status_code == 200 and ok.json()["results"][0]["expert"] == "calc"
    assert client.post("/api/ask", json=ASK).status_code == 200  # no profile name: unchanged behaviour (CLI, scripts)
