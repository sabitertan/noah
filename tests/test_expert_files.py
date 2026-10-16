"""Editing every file of an expert: nothing broken is ever written, every save is a version, and Python needs this machine."""
import shutil
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from noah import expert_check, expert_files, server

CODE = {"X-Noah-Code": "1"}


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    dst = tmp_path / "experts"
    shutil.copytree(Path("experts"), dst, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(server, "EXPERTS_DIR", dst)
    monkeypatch.setattr(server, "VERSIONS", tmp_path / "versions")
    monkeypatch.setattr(server, "DISABLED", tmp_path / "disabled.json")
    monkeypatch.setattr(server, "PROFILES", tmp_path / "profiles.json")
    monkeypatch.setattr(server, "CODE_EDIT", "1")   # the test client is not a loopback address; the guard has its own tests below
    server._reload_experts()  # pyright: ignore[reportPrivateUsage]
    yield dst
    monkeypatch.undo()
    server._reload_experts()  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def client(root: Path) -> TestClient:
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


def files(client: TestClient, name: str = "calc") -> dict[str, Any]:
    r = client.get(f"/api/experts/{name}/files", headers=CODE)
    assert r.status_code == 200
    return r.json()


def save(client: TestClient, new: dict[str, str], base: str, name: str = "calc", note: str = "") -> Any:
    return client.put(f"/api/experts/{name}/files", json={"files": new, "base": base, "note": note}, headers=CODE)


def messages(report: dict[str, Any], severity: str = "error") -> list[str]:
    return [p["message"] for p in report["problems"] if p["severity"] == severity]


# ---- reading ----
def test_lists_the_editable_files_and_nothing_else(client: TestClient, root: Path) -> None:
    (root / "calc" / "README.md").write_text("notes")
    (root / "calc" / "logo.png").write_bytes(b"\x89PNG")
    (root / "calc" / ".secret.txt").write_text("hidden")
    f = files(client)
    assert sorted(f["files"]) == ["README.md", "expert.yaml", "tool.py"] and f["code_editable"] is True
    assert f["hash"] == expert_files.digest(f["files"])


# ---- checking ----
@pytest.mark.parametrize("file, old, new, words", [   # old "": append `new`
    ("tool.py", "", "\nx = undefined_thing\n", "undefined name"),
    ("tool.py", "", "\ndef broken(:\n", "invalid syntax"),
    ("tool.py", "", "\nraise RuntimeError('boom at import')\n", "boom at import"),
    ("tool.py", "text = message.lower()", "text = message.lower(); 1 / 0", "zerodivisionerror"),
    ("tool.py", "def run(", "def walk(", "run(args)"),
    ("expert.yaml", "", "\n  oops: [\n", "not allowed here"),
    ("expert.yaml", "name: calc", "name: other", "folder name"),
    ("expert.yaml", "examples:", "old_examples:", "examples"),
])
def test_a_broken_draft_is_reported_with_where_and_nothing_is_written(client: TestClient, root: Path, file: str, old: str, new: str, words: str) -> None:
    f = files(client)
    before = (root / "calc" / file).read_text()
    text: str = f["files"][file]
    draft = {**f["files"], file: text.replace(old, new) if old else text + new}
    check = client.post("/api/experts/calc/files/check", json={"files": draft}, headers=CODE).json()
    assert not check["ok"] and any(words in m.lower() for m in messages(check)), check
    assert all(p["file"] == file for p in check["problems"] if p["severity"] == "error")
    r = save(client, draft, f["hash"])
    assert r.status_code == 422 and r.json()["saved"] is False
    assert (root / "calc" / file).read_text() == before
    assert "36" in client.post("/api/ask", json={"message": "calculate 15% of 240"}).json()["results"][0]["output"]  # still works


def test_errors_point_at_the_line(client: TestClient) -> None:
    f = files(client)
    src = f["files"]["tool.py"]
    report = client.post("/api/experts/calc/files/check", json={"files": {**f["files"], "tool.py": src + "\ny = nope\n"}}, headers=CODE).json()
    p = next(p for p in report["problems"] if "nope" in p["message"])
    assert p["file"] == "tool.py" and p["line"] == len(src.splitlines()) + 2 and p["col"] >= 1


def test_warnings_do_not_block_a_save(client: TestClient, root: Path) -> None:
    f = files(client)
    r = save(client, {**f["files"], "tool.py": "import os\n" + f["files"]["tool.py"]}, f["hash"])
    assert r.status_code == 200 and r.json()["saved"] and "'os' imported but unused" in messages(r.json()["report"], "warning")
    assert (root / "calc" / "tool.py").read_text().startswith("import os\n")


def test_lint_only_never_runs_the_draft(client: TestClient, tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    f = files(client)
    draft = {**f["files"], "tool.py": f["files"]["tool.py"] + f"\nopen({str(marker)!r}, 'w').close()\n"}
    report = client.post("/api/experts/calc/files/check?load=false", json={"files": draft}, headers=CODE).json()
    assert report["ok"] and report["loaded"] is False and not marker.exists()
    assert client.post("/api/experts/calc/files/check", json={"files": draft}, headers=CODE).json()["loaded"] is True
    assert marker.exists()   # the full check does run it (in a child process): that is why it needs the code permission


def test_a_hanging_import_times_out(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(expert_check, "LOAD_TIMEOUT", 2.0)
    f = files(client)
    draft = {**f["files"], "tool.py": "import time\ntime.sleep(30)\n" + f["files"]["tool.py"]}
    report = client.post("/api/experts/calc/files/check", json={"files": draft}, headers=CODE).json()
    assert not report["ok"] and "longer than" in messages(report)[0]


def test_another_broken_expert_is_a_warning_not_a_block(client: TestClient, root: Path) -> None:
    (root / "weather" / "tool.py").write_text("raise ImportError('weather was already broken')\n")
    f = files(client)
    r = save(client, {**f["files"], "NOTES.md": "fine"}, f["hash"])
    assert r.status_code == 200, r.json()
    assert any("weather" in m for m in messages(r.json()["report"], "warning"))
    cfg = client.get("/api/config").json()   # the server keeps going without it and says why, so it can be opened and fixed
    assert "weather" not in [e["name"] for e in cfg["experts"]] and "already broken" in cfg["expert_errors"]["weather"]
    w = files(client, "weather")
    fixed = save(client, {**w["files"], "tool.py": (Path("experts") / "weather" / "tool.py").read_text()}, w["hash"], name="weather")
    assert fixed.status_code == 200 and "weather" in [e["name"] for e in client.get("/api/config").json()["experts"]]
    assert client.get("/api/config").json()["expert_errors"] == {}


# ---- saving ----
def test_a_good_save_is_written_reloaded_and_used(client: TestClient, root: Path) -> None:
    f = files(client)
    src = f["files"]["tool.py"].replace('return f"{expr} = ', 'return f"result: {expr} = ')
    r = save(client, {**f["files"], "tool.py": src, "NOTES.md": "# calc\n"}, f["hash"], note="prefix answers")
    assert r.status_code == 200 and r.json()["saved"] and r.json()["version"] == 2
    assert (root / "calc" / "NOTES.md").read_text() == "# calc\n"
    assert client.post("/api/ask", json={"message": "calculate 15% of 240"}).json()["results"][0]["output"].startswith("result: ")


def test_a_file_left_out_is_removed_but_other_files_are_kept(client: TestClient, root: Path) -> None:
    (root / "calc" / "NOTES.md").write_text("x")
    (root / "calc" / "data.bin").write_bytes(b"\0\1")
    f = files(client)
    del f["files"]["NOTES.md"]
    assert save(client, f["files"], f["hash"]).status_code == 200
    assert not (root / "calc" / "NOTES.md").exists() and (root / "calc" / "data.bin").exists()


def test_a_stale_editor_cannot_overwrite_newer_files(client: TestClient, root: Path) -> None:
    f = files(client)
    (root / "calc" / "NOTES.md").write_text("written by hand meanwhile")
    r = save(client, {**f["files"], "tool.py": f["files"]["tool.py"] + "\n# mine\n"}, f["hash"])
    assert r.status_code == 409 and "# mine" not in (root / "calc" / "tool.py").read_text()


@pytest.mark.parametrize("bad", ["../escape.py", "sub/dir.py", ".hidden.py", "run.sh", "x" * 70 + ".py", ""])
def test_file_names_cannot_leave_the_folder_or_add_other_kinds(client: TestClient, root: Path, bad: str) -> None:
    f = files(client)
    r = save(client, {**f["files"], bad: "print(1)\n"}, f["hash"])
    assert r.status_code == 400 and not (root / "escape.py").exists() and not (root / "calc" / "run.sh").exists()


def test_the_manifest_cannot_point_the_tool_outside_the_folder(client: TestClient, root: Path) -> None:
    f = files(client)
    r = save(client, {**f["files"], "expert.yaml": f["files"]["expert.yaml"] + "tool: ../weather/tool.py\n"}, f["hash"])
    assert r.status_code == 422 and "tool must name a .py file" in " ".join(messages(r.json()["report"]))


def test_size_limits(client: TestClient) -> None:
    f = files(client)
    assert save(client, {**f["files"], "big.txt": "x" * (expert_files.MAX_FILE + 1)}, f["hash"]).status_code == 400
    many = {**f["files"], **{f"n{i}.md": "" for i in range(expert_files.MAX_FILES)}}
    assert save(client, many, f["hash"]).status_code == 400


# ---- versions ----
def test_versions_start_with_the_original_and_switching_brings_it_back(client: TestClient, root: Path) -> None:
    original = (root / "calc" / "tool.py").read_text()
    f = files(client)
    h = save(client, {**f["files"], "tool.py": original + "\n# one\n"}, f["hash"], note="one").json()["hash"]
    h = save(client, {**f["files"], "tool.py": original + "\n# two\n", "NOTES.md": "n"}, h, note="two").json()["hash"]
    vs = client.get("/api/experts/calc/versions").json()
    assert [(v["id"], v["note"], v["current"]) for v in vs] == [(3, "two", True), (2, "one", False), (1, "As it was before editing in Noah", False)]
    assert client.get("/api/experts/calc/versions/2").json()["files"]["tool.py"].endswith("# one\n")
    r = client.post("/api/experts/calc/versions/1/restore", json={"base": h}, headers=CODE)
    assert r.status_code == 200 and r.json()["version"] == 4
    assert (root / "calc" / "tool.py").read_text() == original and not (root / "calc" / "NOTES.md").exists()
    vs = client.get("/api/experts/calc/versions").json()
    assert vs[0]["note"] == "Switched to version 1" and vs[0]["current"] and not any(v["current"] for v in vs[1:])


def test_saving_the_same_files_twice_keeps_one_version(client: TestClient) -> None:
    f = files(client)
    new = {**f["files"], "NOTES.md": "same"}
    h = save(client, new, f["hash"]).json()["hash"]
    assert save(client, new, h).json()["version"] == 2
    assert len(client.get("/api/experts/calc/versions").json()) == 2


def test_a_version_that_no_longer_loads_is_refused(client: TestClient, root: Path) -> None:
    f = files(client)
    h = save(client, {**f["files"], "NOTES.md": "x"}, f["hash"]).json()["hash"]
    (root / "calc" / "helper.py").write_text("")   # the version below needs no helper; the current folder has an extra file
    vdir = server.VERSIONS / "calc"
    v = next(vdir.glob("1.json"))
    v.write_text(v.read_text().replace("def run(", "def walk("))
    h = expert_files.digest(expert_files.read_files(root / "calc"))
    r = client.post("/api/experts/calc/versions/1/restore", json={"base": h}, headers=CODE)
    assert r.status_code == 422 and (root / "calc" / "NOTES.md").exists()


def test_settings_form_saves_are_versions_too(client: TestClient) -> None:
    m = client.get("/api/experts/calc/manifest").json()
    client.put("/api/experts/calc/manifest", json={"description": m["description"], "examples": m["examples"] + ["add 2 and 2"]})
    assert [v["note"] for v in client.get("/api/experts/calc/versions").json()] == ["Edited settings", "As it was before editing in Noah"]
    r = client.post("/api/experts", json={"name": "pirate", "description": "Pirate talk.", "examples": ["talk like a pirate"],
                                          "model": "qwen2.5:0.5b", "system": "Talk like a pirate."})
    assert r.status_code == 201 and [v["note"] for v in client.get("/api/experts/pirate/versions").json()] == ["Created in Noah"]


def test_versions_of_unknown_or_invalid_names(client: TestClient) -> None:
    assert client.get("/api/experts/calc/versions/99").status_code == 404
    assert client.get("/api/experts/ghost/versions").status_code == 404
    assert client.get("/api/experts/..%2F..%2Fetc/versions/1").status_code in (400, 404)


# ---- who may change code ----
def test_only_python_needs_the_code_permission(client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "CODE_EDIT", "")   # default: loopback only, and the test client is not loopback
    f = client.get("/api/experts/calc/files", headers=CODE).json()
    assert f["code_editable"] is False and "NOAH_CODE_EDIT" in f["code_note"]
    code = {**f["files"], "tool.py": f["files"]["tool.py"] + "\n# x\n"}
    assert save(client, code, f["hash"]).status_code == 403
    assert client.post("/api/experts/calc/files/check", json={"files": code}, headers=CODE).status_code == 403   # loading would run it
    assert client.post("/api/experts/calc/files/check?load=false", json={"files": code}, headers=CODE).status_code == 200
    assert save(client, {**f["files"], "NOTES.md": "fine"}, f["hash"]).status_code == 200                       # non-code files: yes
    assert "# x" not in (root / "calc" / "tool.py").read_text()


@pytest.mark.parametrize("setting, client_addr, host, header, allowed", [
    ("", ("127.0.0.1", 5000), "localhost:8765", True, True),
    ("", ("127.0.0.1", 5000), "localhost:8765", False, False),      # no header: a form or fetch from another site
    ("", ("127.0.0.1", 5000), "evil.example:8765", True, False),    # DNS rebinding: right address, wrong host name
    ("", ("192.168.1.9", 5000), "localhost:8765", True, False),     # another machine
    ("1", ("192.168.1.9", 5000), "noah.lan:8765", True, True),      # explicitly allowed
    ("1", ("192.168.1.9", 5000), "noah.lan:8765", False, False),
    ("0", ("127.0.0.1", 5000), "localhost:8765", True, False),      # explicitly off
])
def test_code_permission(root: Path, monkeypatch: pytest.MonkeyPatch, setting: str, client_addr: tuple[str, int], host: str, header: bool, allowed: bool) -> None:
    monkeypatch.setattr(server, "CODE_EDIT", setting)
    c = TestClient(server.app, base_url=f"http://{host}", client=client_addr)
    f = c.get("/api/experts/calc/files", headers=CODE if header else {}).json()
    assert f["code_editable"] is allowed
