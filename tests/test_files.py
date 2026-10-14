import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from noah import filesvc, server
from noah.engines import make_engine
from noah.extractors import make_extractor
from noah.orchestrator import Orchestrator
from noah.registry import load_experts
from noah.trace import TraceStore
from tests.fakes import returns


def make_pdf(text: str) -> bytes:
    stream = f"BT /F1 14 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", list[int]()
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    r = tmp_path / "files"
    (r / "sub").mkdir(parents=True)
    (r / "notes.md").write_text("# Title\n\nHello **world**")
    (r / "plain.txt").write_text("remember the milk")
    (r / "expenses.csv").write_text("date,amount\n2026-01-01,5\n2026-01-02,7\n")
    (r / "report.pdf").write_bytes(make_pdf("Quarterly revenue grew"))
    (r / "broken.pdf").write_bytes(b"")
    (r / "budget.xlsx").write_bytes(b"PK\x03\x04binary")
    (r / "run.sh").write_text("echo hi")
    (r / "sub" / "deep.txt").write_text("deep file")
    (tmp_path / "secret.txt").write_text("top secret")
    monkeypatch.setenv("NOAH_FILES_DIR", str(r))
    return r


# ---- previews -------------------------------------------------------------------------------------------------

def test_previews_by_type(root: Path) -> None:
    md = filesvc.preview(filesvc.safe_path("notes.md"))
    assert md["kind"] == "markdown" and "Hello **world**" in md["text"]
    csv_ = filesvc.preview(filesvc.safe_path("expenses.csv"))
    assert csv_["rows"] == [["date", "amount"], ["2026-01-01", "5"], ["2026-01-02", "7"]]
    pdf = filesvc.preview(filesvc.safe_path("report.pdf"))
    assert "Quarterly revenue grew" in pdf["text"]
    assert filesvc.preview(filesvc.safe_path("plain.txt"))["text"] == "remember the milk"


def test_unreadable_and_unsupported_fall_back_cleanly(root: Path) -> None:
    assert "Could not read" in filesvc.preview(filesvc.safe_path("broken.pdf"))["error"]
    x = filesvc.preview(filesvc.safe_path("budget.xlsx"))
    assert x["kind"] == "binary" and "can't preview .xlsx" in x["error"]


def test_large_text_is_truncated(root: Path) -> None:
    (root / "big.txt").write_text("x" * (filesvc.TEXT_BYTES + 500))
    pv = filesvc.preview(filesvc.safe_path("big.txt"))
    assert pv["truncated"] and len(pv["text"]) == filesvc.TEXT_BYTES


def test_binary_disguised_as_text_is_not_dumped(root: Path) -> None:
    (root / "fake.txt").write_bytes(b"abc\x00\x01\x02binary")
    assert "error" in filesvc.preview(filesvc.safe_path("fake.txt"))


# ---- confinement ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["../secret.txt", "sub/../../secret.txt", "/etc/passwd", "C:\\Windows\\win.ini", "", "missing.txt", "sub"])
def test_safe_path_rejects(root: Path, bad: str) -> None:
    with pytest.raises(filesvc.FileAccessError):
        filesvc.safe_path(bad)


def test_symlink_escape_rejected(root: Path, tmp_path: Path) -> None:
    (root / "link.txt").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(filesvc.FileAccessError):
        filesvc.safe_path("link.txt")


def test_search_skips_hidden_and_ranks_names_first(root: Path) -> None:
    (root / ".hidden.txt").write_text("milk")
    names = [p.name for _, p, _ in filesvc.search("milk")]
    assert ".hidden.txt" not in names and names[0] == "plain.txt"  # content hit
    assert filesvc.search("expenses")[0][1].name == "expenses.csv"
    assert filesvc.search("zzzznothing") == []


# ---- the expert -----------------------------------------------------------------------------------------------

def run_tool(args: dict[str, Any]) -> dict[str, Any]:
    run = load_experts("experts")["files"].run
    assert run is not None
    return cast(dict[str, Any], run(args))


def rule_of(message: str) -> dict[str, Any]:
    rule = load_experts("experts")["files"].extract_rule
    assert rule is not None
    return rule(message)


def test_expert_opens_clear_winner_immediately(root: Path) -> None:
    out = run_tool({"query": "expenses"})
    assert out["files"][0]["name"] == "expenses.csv" and out["files"][0]["preview"]["rows"]
    assert out["text"].startswith("Opened expenses.csv")


def test_expert_offers_download_for_unpreviewable(root: Path) -> None:
    out = run_tool({"query": "budget"})
    f = out["files"][0]
    assert "preview" not in f and not f["previewable"] and f["openable"]
    assert "can't preview .xlsx" in out["text"] and "download" in out["text"]


def test_expert_lists_when_ambiguous_and_when_empty(root: Path) -> None:
    ambiguous = run_tool({"query": "txt"})  # plain.txt and sub/deep.txt both match by name
    assert ambiguous["text"].startswith("I found") and "Pick one" in ambiguous["text"]
    assert not any("preview" in f for f in ambiguous["files"]) and len(ambiguous["files"]) >= 2
    assert run_tool({"query": "nothingmatches"})["files"] == []
    assert len(run_tool({"query": ""})["files"]) >= 5  # empty query lists files


def test_rule_extractor_drops_open_verbs() -> None:
    assert rule_of("open my notes file")["query"] == "notes"
    assert rule_of("show me the expenses csv")["query"] == "expenses csv"


def test_traces_do_not_store_file_contents(root: Path, tmp_path: Path) -> None:
    experts = load_experts("experts")
    trace = TraceStore(tmp_path / "t.jsonl")
    orch = Orchestrator(experts, make_engine("keyword"), make_extractor("rule"), trace, threshold=0.0)
    out = asyncio.run(orch.ask("open my notes"))
    assert out[0].attachments and out[0].attachments[0].get("preview")  # the user gets the preview...
    logged = trace.read()[-1]["results"][0]["attachments"][0]
    assert "preview" not in logged  # ...but the trace does not keep it


# ---- HTTP endpoints -------------------------------------------------------------------------------------------

@pytest.fixture
def client(root: Path) -> TestClient:
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


def test_download_is_attachment_and_nosniff(client: TestClient) -> None:
    r = client.get("/api/files/download", params={"path": "notes.md"})
    assert r.status_code == 200 and r.content.startswith(b"# Title")
    assert "attachment" in r.headers["content-disposition"] and r.headers["x-content-type-options"] == "nosniff"


def test_endpoints_refuse_escapes(client: TestClient) -> None:
    for path in ("../secret.txt", "/etc/passwd", "sub/../../secret.txt"):
        assert client.get("/api/files/preview", params={"path": path}).status_code == 404
        assert client.get("/api/files/download", params={"path": path}).status_code == 404
        assert client.post("/api/files/open", json={"path": path}).status_code in (403, 404)


def test_preview_endpoint(client: TestClient) -> None:
    j = client.get("/api/files/preview", params={"path": "expenses.csv"}).json()
    assert j["kind"] == "csv" and j["preview"]["rows"][0] == ["date", "amount"]


def test_open_only_for_local_client_and_safe_types(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []

    def open_with_default_app(p: Path) -> None:
        opened.append(p.name)
    monkeypatch.setattr(filesvc, "open_with_default_app", open_with_default_app)
    monkeypatch.setattr(server, "_is_local", returns(False))
    assert client.post("/api/files/open", json={"path": "report.pdf"}).status_code == 403 and not opened
    monkeypatch.setattr(server, "_is_local", returns(True))
    assert client.post("/api/files/open", json={"path": "report.pdf"}).json()["opened"] and opened == ["report.pdf"]


def test_open_refuses_scripts_even_locally(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_is_local", returns(True))
    r = client.post("/api/files/open", json={"path": "run.sh"})  # real open_with_default_app, allow-list rejects .sh
    assert r.status_code == 400


def test_is_local_rejects_proxied_requests() -> None:
    class Req:
        def __init__(self, host: str, headers: dict[str, str]) -> None: self.client = type("C", (), {"host": host})(); self.headers = headers
    def local(host: str, headers: dict[str, str]) -> bool:
        return server._is_local(cast(Any, Req(host, headers)))  # pyright: ignore[reportPrivateUsage]  (testing the helper itself)
    assert local("127.0.0.1", {})
    assert not local("127.0.0.1", {"x-forwarded-for": "1.2.3.4"})  # reverse proxy on the same box
    assert not local("10.0.0.5", {})


def test_clear_winner_hides_noise_matches(root: Path) -> None:
    # "report" is a name hit for report.pdf; ".pdf" on broken.pdf and the word "report" inside other files are noise
    (root / "other.txt").write_text("see the report for details")
    out = run_tool({"query": "report pdf"})
    assert [f["name"] for f in out["files"]] == ["report.pdf"]


def test_unreadable_winner_is_described_honestly(root: Path) -> None:
    out = run_tool({"query": "broken"})
    assert "couldn't read it" in out["text"] and "Opened" not in out["text"]
    assert "error" in out["files"][0]["preview"]
