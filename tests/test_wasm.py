"""The browser build's Python side (noah/wasm.py), run here in CPython: the request bridge, expert seeding, the Ollama setting
and the in-process expert check. The browser-only parts (XMLHttpRequest, fetch) are exercised by the browser build itself."""
import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noah import expert_check, server, wasm


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A user's browser storage: cwd with experts/ and data/, the way start() leaves it."""
    shutil.copytree(Path("experts"), tmp_path / "experts", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(server, "EXPERTS_DIR", tmp_path / "experts")
    monkeypatch.setattr(server, "VERSIONS", tmp_path / "data" / "expert_versions")
    monkeypatch.setattr(server, "DISABLED", tmp_path / "data" / "disabled_experts.json")
    monkeypatch.setattr(server, "PROFILES", tmp_path / "data" / "profiles.json")
    monkeypatch.setattr(server, "CODE_EDIT", "1")
    monkeypatch.setattr(wasm, "_app", server.app)
    server._reload_experts()  # pyright: ignore[reportPrivateUsage]
    yield tmp_path
    monkeypatch.undo()
    server._reload_experts()  # pyright: ignore[reportPrivateUsage]


def call(method: str, url: str, body: Any = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else b""
    r = asyncio.run(wasm.handle(method, url, [["content-type", "application/json"], ["x-noah-code", "1"]], data))
    return r["status"], json.loads(r["body"]) if r["body"] else None


# ---- the bridge ----
def test_requests_reach_the_app_without_a_server(home: Path) -> None:
    status, cfg = call("GET", "/api/config")
    assert status == 200 and "calc" in [e["name"] for e in cfg["experts"]]
    status, out = call("POST", "/api/ask", {"message": "calculate 15% of 240", "engine": "keyword", "extractor": "rule"})
    assert status == 200 and out["results"][0]["output"] == "15/100*240 = 36"


def test_query_strings_errors_and_headers_come_back(home: Path) -> None:
    assert call("GET", "/api/experts/ghost/files")[0] == 404
    assert call("GET", "/api/traces?limit=1")[0] == 200
    r = asyncio.run(wasm.handle("GET", "/api/config", [], b""))
    assert ["content-type", "application/json"] in r["headers"]


def test_the_page_is_not_this_machine(home: Path) -> None:
    """Opening a file in another app needs the computer running Noah; in the browser there is none."""
    status, _ = call("POST", "/api/files/open", {"path": "x.txt"})
    assert status == 403


def test_editing_and_versions_work_through_the_bridge(home: Path) -> None:
    _, f = call("GET", "/api/experts/calc/files")
    src = f["files"]["tool.py"].replace('return f"{expr} = ', 'return f"browser: {expr} = ')
    status, saved = call("PUT", "/api/experts/calc/files", {"files": {**f["files"], "tool.py": src}, "base": f["hash"]})
    assert status == 200 and saved["version"] == 2
    _, out = call("POST", "/api/ask", {"message": "calculate 15% of 240", "engine": "keyword", "extractor": "rule"})
    assert out["results"][0]["output"].startswith("browser: ")


# ---- the check runs in-process in the browser ----
def test_the_load_check_runs_in_process_without_subprocesses(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_processes(*_: Any, **__: Any) -> Any:
        raise AssertionError("no subprocess in the browser")

    monkeypatch.setattr(expert_check.subprocess, "run", no_processes)
    monkeypatch.setattr(sys, "platform", "emscripten")
    _, f = call("GET", "/api/experts/calc/files")
    bad = {**f["files"], "tool.py": f["files"]["tool.py"] + "\nraise SystemExit(3)\n"}
    status, r = call("PUT", "/api/experts/calc/files", {"files": bad, "base": f["hash"]})
    assert status == 422 and "SystemExit" in r["report"]["problems"][0]["message"]
    status, _ = call("PUT", "/api/experts/calc/files", {"files": {**f["files"], "NOTES.md": "ok"}, "base": f["hash"]})
    assert status == 200


# ---- seeding the user's experts from the ones shipped with the page ----
def test_seed_copies_each_shipped_expert_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundled = tmp_path / "bundled"
    shutil.copytree(Path("experts"), bundled, ignore=shutil.ignore_patterns("__pycache__"))
    user = tmp_path / "user"
    (user / "experts").mkdir(parents=True)
    monkeypatch.chdir(user)
    first = wasm.seed(bundled)
    assert "calc" in first and (user / "experts" / "calc" / "tool.py").exists()
    assert json.loads((user / "data" / "disabled_experts.json").read_text()) == wasm.OFF_IN_BROWSER   # need local programs
    (user / "experts" / "calc" / "tool.py").write_text("# mine\n")
    shutil.rmtree(user / "experts" / "css")
    assert wasm.seed(bundled) == []
    assert (user / "experts" / "calc" / "tool.py").read_text() == "# mine\n" and not (user / "experts" / "css").exists()
    shutil.copytree(bundled / "css", bundled / "newone")                      # a later version of the site ships a new expert
    assert wasm.seed(bundled) == ["newone"]
    (user / "data" / "disabled_experts.json").write_text("[]")                 # the user's own switches are kept
    wasm.seed(bundled)
    assert json.loads((user / "data" / "disabled_experts.json").read_text()) == []


# ---- the Ollama address ----
def test_the_ollama_address_is_saved_and_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(server, "OLLAMA", server.OLLAMA)
    monkeypatch.setenv("NOAH_LLM_URL", "http://localhost:11434")
    app = FastAPI()
    wasm.add_routes(app)
    c = TestClient(app)
    assert c.get("/api/wasm/ollama").json() == {"url": "http://localhost:11434", "default": "http://localhost:11434"}
    r = c.put("/api/wasm/ollama", json={"url": " http://192.168.1.5:11434/ "})
    assert r.status_code == 200 and r.json()["url"] == "http://192.168.1.5:11434"
    assert server.OLLAMA == "http://192.168.1.5:11434" and wasm.ollama_url() == "http://192.168.1.5:11434"
    from noah import hf
    assert hf.ollama_url() == "http://192.168.1.5:11434"
    assert c.put("/api/wasm/ollama", json={"url": "localhost:11434"}).status_code == 400
    assert c.put("/api/wasm/ollama", json={"url": "javascript:alert(1)"}).status_code == 400
    assert c.put("/api/wasm/ollama", json={"url": ""}).json()["url"] == wasm.DEFAULT_OLLAMA


def test_without_fastembed_the_default_orchestrator_routes_by_keywords(monkeypatch: pytest.MonkeyPatch) -> None:
    """In the browser there is no ONNX runtime, so the embedding engine is not offered and Default uses keywords."""
    import importlib.util
    real = importlib.util.find_spec

    def find_spec(name: str, *a: Any, **k: Any) -> Any:
        return None if name == "fastembed" else real(name, *a, **k)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    import importlib
    fresh = importlib.reload(server)
    try:
        assert fresh.DEFAULT_PROFILES[0]["engine"] == "keyword" and not fresh.HAS_EMBEDDING
        assert "embedding" not in TestClient(fresh.app).get("/api/config").json()["engines"]
    finally:
        monkeypatch.undo()
        importlib.reload(server)
