import asyncio
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Iterator, cast

import pytest
from fastapi.testclient import TestClient

from noah import server
from noah.engines import make_engine
from noah.extractors import make_extractor
from noah.orchestrator import Orchestrator
from noah.registry import load_experts

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")


class FakeSdServer(BaseHTTPRequestHandler):
    """sd-server's POST /v1/images/generations: records each request body, answers with one base64 PNG (or `reply`)."""

    seen: list[dict[str, Any]] = []
    reply: tuple[int, dict[str, Any]] = (200, {})

    def do_POST(self) -> None:
        FakeSdServer.seen.append({"path": self.path, **json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
        status, body = FakeSdServer.reply
        if status == 0:   # the server dies mid-request (out of memory, killed)
            self.close_connection = True
            return
        data = json.dumps(body or {"created": 0, "output_format": "png", "data": [{"b64_json": base64.b64encode(PNG).decode()}]}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def sd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    httpd = HTTPServer(("127.0.0.1", 0), FakeSdServer)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    FakeSdServer.seen, FakeSdServer.reply = [], (200, {})
    monkeypatch.setenv("NOAH_IMAGE_URL", f"http://127.0.0.1:{httpd.server_address[1]}/")
    monkeypatch.setenv("NOAH_FILES_DIR", str(tmp_path / "files"))
    yield tmp_path / "files"
    httpd.shutdown()


def tool(name: str) -> Any:
    return getattr(load_experts("experts")["image"], name)


def test_rule_keeps_the_description_and_picks_a_shape() -> None:
    rule = tool("extract_rule")
    assert rule("draw a red fox in a snowy forest") == {"prompt": "red fox in a snowy forest", "shape": "square"}
    assert rule("Please generate an image of a cyberpunk city at night")["prompt"] == "a cyberpunk city at night"
    assert rule("make me a wide wallpaper of mountains")["shape"] == "landscape"
    assert rule("create a tall portrait of a knight")["shape"] == "portrait"
    assert rule("a lighthouse at dusk")["prompt"] == "a lighthouse at dusk"   # nothing to strip: the whole message is the prompt


def test_generates_saves_and_previews(sd: Path) -> None:
    out = tool("run")({"prompt": "a red fox", "shape": "landscape"})
    assert FakeSdServer.seen == [{"path": "/v1/images/generations", "prompt": "a red fox", "n": 1, "size": "1344x768", "output_format": "png"}]
    f = out["files"][0]
    saved = sd / f["path"]
    assert f["path"].startswith("generated/") and f["path"].endswith("-a-red-fox.png") and saved.read_bytes() == PNG
    assert f["kind"] == "image" and f["preview"]["url"] == "/api/files/image?path=" + f["path"].replace("/", "%2F")
    assert f["path"] in out["text"]


def test_unknown_shape_falls_back_to_square(sd: Path) -> None:
    tool("run")({"prompt": "x", "shape": "hexagon"})
    assert FakeSdServer.seen[0]["size"] == "1024x1024"


@pytest.mark.parametrize("reply, message", [
    ((500, {"error": "out of memory"}), r"refused the request \(HTTP 500\)"),
    ((200, {"data": list[Any]()}), "without an image"),
    ((200, {"data": [{"b64_json": base64.b64encode(b"<svg/>").decode()}]}), "not a PNG"),
])
def test_bad_answers_are_explained_and_nothing_is_saved(sd: Path, reply: tuple[int, dict[str, Any]], message: str) -> None:
    FakeSdServer.reply = reply
    with pytest.raises(RuntimeError, match=message):
        tool("run")({"prompt": "x"})
    assert not (sd / "generated").exists() or not any((sd / "generated").iterdir())


def test_server_dying_mid_request_is_not_reported_as_missing(sd: Path) -> None:
    FakeSdServer.reply = (0, {})
    with pytest.raises(RuntimeError, match="stopped before sending"):
        tool("run")({"prompt": "x"})


def test_no_server_says_how_to_start_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOAH_IMAGE_URL", "http://127.0.0.1:9")   # discard port: nothing listens
    with pytest.raises(RuntimeError, match="No image server is running.*image_server.sh"):
        tool("run")({"prompt": "x"})


def test_routes_and_runs_through_the_orchestrator(sd: Path) -> None:
    experts = load_experts("experts")
    orch = Orchestrator(experts, make_engine("embedding"), make_extractor("rule"), threshold=0.63)
    out = asyncio.run(orch.ask("paint a watercolor of a fox in the snow"))
    assert out[0].expert == "image" and out[0].error is None
    assert out[0].attachments[0]["kind"] == "image" and FakeSdServer.seen[0]["prompt"] == "watercolor of a fox in the snow"


def test_image_endpoint_is_inline_raster_only(sd: Path) -> None:
    (sd / "generated").mkdir(parents=True)
    (sd / "generated" / "a.png").write_bytes(PNG)
    (sd / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")
    client = TestClient(server.app, base_url="http://127.0.0.1:8765")
    r = client.get("/api/files/image", params={"path": "generated/a.png"})
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert "attachment" not in r.headers.get("content-disposition", "") and r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert client.get("/api/files/image", params={"path": "logo.svg"}).status_code == 400   # SVG can carry script: download only
    assert client.get("/api/files/image", params={"path": "../secret.png"}).status_code == 404
    pv = cast(dict[str, Any], client.get("/api/files/preview", params={"path": "generated/a.png"}).json())
    assert pv["previewable"] and pv["preview"]["kind"] == "image"
