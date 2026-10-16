"""Noah in the browser: the same FastAPI app, run by Pyodide (CPython compiled to WebAssembly) in a Web Worker.

The static build (`cd frontend && npm run build:wasm`) sends every `/api/...` request of the page to `handle()`, which calls
the ASGI app directly: there is no server and no socket. What a browser lacks is filled in before `noah.server` is imported:

- threads: Starlette runs sync endpoints in a thread pool; here they run inline (the worker has nothing else to do);
- sockets: httpx and urllib go through the browser (synchronous XMLHttpRequest, allowed in a worker, and fetch), so the
  servers Noah talks to (Ollama, Hugging Face, OpenRouter, Azure, Open-Meteo) must allow this page's origin (CORS);
- processes: the expert check loads drafts in-process instead of in a child process (`expert_check.load`).

Models still run in Ollama on the user's own computer: its address is a setting (`/api/wasm/ollama`), kept with the rest
of the user's data in the browser's storage.
"""
from __future__ import annotations

import importlib
import io
import json
import os
import shutil
from email.message import Message
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

OLLAMA_FILE = Path("data/ollama_url")
DEFAULT_OLLAMA = "http://localhost:11434"
# headers a page may not set (the browser does) or that no longer match once the browser has decoded the body
_FORBIDDEN = {"host", "connection", "content-length", "accept-encoding", "user-agent", "keep-alive", "transfer-encoding", "te", "upgrade"}
_DROP = {"content-encoding", "content-length", "transfer-encoding"}


# ---- HTTP through the browser ----
def _browser(module: str) -> Any:
    """`js` and `pyodide.*` exist only inside Pyodide."""
    return importlib.import_module(module)


def _xhr(method: str, url: str, headers: list[tuple[str, str]], body: bytes | None, timeout: float | None) -> tuple[int, list[tuple[str, str]], bytes]:
    """A blocking request. Synchronous XMLHttpRequest is allowed in a worker, which lets sync Python code (httpx.get,
    urllib.urlopen) work unchanged."""
    js = _browser("js")
    x: Any = js.XMLHttpRequest.new()
    x.open(method, url, False)
    x.responseType = "arraybuffer"
    if timeout:
        x.timeout = int(timeout * 1000)
    for k, v in headers:
        if k.lower() not in _FORBIDDEN:
            x.setRequestHeader(k, v)
    try:
        x.send(js.Uint8Array.new(body) if body else None)
    except Exception as e:  # a JsException: refused, unreachable, blocked by CORS, or timed out
        raise OSError(f"could not reach {urlsplit(url).netloc} from the browser ({e}). Is it running, and does it allow this page (CORS)?") from e
    if x.status == 0:
        raise OSError(f"could not reach {urlsplit(url).netloc} from the browser. Is it running, and does it allow this page (CORS)?")
    pairs = [tuple(line.split(":", 1)) for line in str(x.getAllResponseHeaders()).split("\r\n") if ":" in line]
    out = [(k.strip(), v.strip()) for k, v in pairs if k.strip().lower() not in _DROP]
    return int(x.status), out, bytes(x.response.to_py()) if x.response is not None else b""


class _SyncTransport(httpx.BaseTransport):
    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        timeout: Any = request.extensions.get("timeout", {}).get("read")
        try:
            status, headers, content = _xhr(request.method, str(request.url), list(request.headers.items()), request.read() or None, timeout)
        except OSError as e:
            raise httpx.ConnectError(str(e), request=request) from e
        return httpx.Response(status, headers=headers, content=content, request=request)


class _AsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        pyfetch = _browser("pyodide.http").pyfetch
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _FORBIDDEN}
        body = await request.aread()
        try:
            r: Any = await pyfetch(str(request.url), method=request.method, headers=headers, body=body or None)
            content = await r.bytes()
        except Exception as e:
            raise httpx.ConnectError(f"could not reach {request.url.host} from the browser ({e})", request=request) from e
        kept = [(k, v) for k, v in dict(r.headers).items() if k.lower() not in _DROP]
        return httpx.Response(int(r.status), headers=kept, content=content, request=request)


class _UrlResponse(io.BytesIO):
    """Enough of http.client.HTTPResponse for code written against urllib.request.urlopen."""

    def __init__(self, url: str, status: int, headers: list[tuple[str, str]], body: bytes):
        super().__init__(body)
        self.url, self.status, self.code, self.reason = url, status, status, ""
        self.headers = self.msg = Message()
        for k, v in headers:
            self.headers[k] = v

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self.url

    def info(self) -> Message:
        return self.headers


def _urlopen(url: Any, data: bytes | None = None, timeout: float | None = None, **_: Any) -> _UrlResponse:
    import urllib.error
    import urllib.request

    req = url if isinstance(url, urllib.request.Request) else urllib.request.Request(str(url), data=data)
    body: Any = req.data if req.data is not None else data
    status, headers, content = _xhr(req.get_method(), req.full_url, list(req.header_items()), body, timeout)
    if status >= 400:
        raise urllib.error.HTTPError(req.full_url, status, "", Message(), io.BytesIO(content))
    return _UrlResponse(req.full_url, status, headers, content)


# ---- setup ----
def _patch() -> None:
    import anyio.to_thread
    import httpx._client as client  # pyright: ignore[reportPrivateUsage]
    import urllib.request

    async def run_sync(func: Callable[..., Any], *args: Any, **_: Any) -> Any:
        return func(*args)

    anyio.to_thread.run_sync = run_sync  # pyright: ignore[reportAttributeAccessIssue]
    setattr(client, "HTTPTransport", _SyncTransport)       # what httpx.Client / httpx.get build their transport from
    setattr(client, "AsyncHTTPTransport", _AsyncTransport)
    urllib.request.urlopen = _urlopen  # pyright: ignore[reportAttributeAccessIssue]


def ollama_url() -> str:
    try:
        return OLLAMA_FILE.read_text().strip() or DEFAULT_OLLAMA
    except OSError:
        return DEFAULT_OLLAMA


def _use_ollama(url: str) -> None:
    import noah.server as server

    os.environ["NOAH_LLM_URL"] = url   # read per call by hf.ollama_url()
    server.OLLAMA = url                # read per request by the endpoints
    server._engines.clear()            # pyright: ignore[reportPrivateUsage]  (LLM engines keep the address they were made with)


class OllamaIn(BaseModel):
    url: str


def get_ollama() -> dict[str, Any]:
    return {"url": ollama_url(), "default": DEFAULT_OLLAMA}


def put_ollama(body: OllamaIn) -> dict[str, Any]:
    url = body.url.strip().rstrip("/") or DEFAULT_OLLAMA
    if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
        raise HTTPException(400, "Use an address like http://localhost:11434")
    OLLAMA_FILE.parent.mkdir(parents=True, exist_ok=True)
    OLLAMA_FILE.write_text(url)
    _use_ollama(url)
    return get_ollama()


def add_routes(app: FastAPI) -> None:
    """What only the browser build has: where the user's Ollama is."""
    app.add_api_route("/api/wasm/ollama", get_ollama, methods=["GET"])
    app.add_api_route("/api/wasm/ollama", put_ollama, methods=["PUT"])


_app: Any = None


SEEDED = Path("data/seeded_experts.json")
OFF_IN_BROWSER = ["files", "image"]   # they need programs on the user's computer (a files folder, an image server)


def seed(bundled: Path) -> list[str]:
    """Copy the experts shipped with this build into the user's folder: each one once, so one the user deleted stays deleted
    and one they edited is never overwritten. Returns the names copied."""
    try:
        done: list[str] = json.loads(SEEDED.read_text())
    except (OSError, ValueError):
        done = []
    first = not done
    added: list[str] = []
    for src in sorted(p for p in bundled.iterdir() if (p / "expert.yaml").is_file()):
        if src.name not in done and not Path("experts", src.name).exists():
            shutil.copytree(src, Path("experts", src.name), ignore=shutil.ignore_patterns("__pycache__"))
            added.append(src.name)
        done.append(src.name)
    SEEDED.parent.mkdir(parents=True, exist_ok=True)
    SEEDED.write_text(json.dumps(sorted(set(done))))
    disabled = Path("data/disabled_experts.json")
    if first and not disabled.exists():
        disabled.write_text(json.dumps(OFF_IN_BROWSER))
    return added


def start(root: str, bundled: str = "") -> None:
    """Called once by the worker, with the folder that holds this user's experts and data (kept in the browser) and the
    folder of experts shipped with the page."""
    global _app
    os.chdir(root)
    Path("experts").mkdir(exist_ok=True)
    if bundled:
        seed(Path(bundled))
    os.environ.setdefault("NOAH_CODE_EDIT", "1")   # the code runs in this user's own browser tab, nowhere else
    os.environ["NOAH_LLM_URL"] = ollama_url()
    _patch()
    import noah.server as server

    add_routes(server.app)
    _app = server.app


async def handle(method: str, url: str, headers: Any, body: Any) -> dict[str, Any]:
    """One request from the page, answered by the app. `url` is the path and query, e.g. '/api/config?x=1'.
    From JavaScript, `headers` (pairs) and `body` (a Uint8Array) arrive as proxies: they are converted here."""
    if hasattr(headers, "to_py"):
        headers = headers.to_py()
    body = bytes(body.to_py()) if hasattr(body, "to_py") else bytes(body or b"")
    parts = urlsplit(url)
    scope: dict[str, Any] = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method.upper(), "scheme": "http",
        "path": parts.path, "raw_path": parts.path.encode(), "query_string": parts.query.encode(), "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in [*headers, ["host", "localhost"]]],
        "client": ("browser", 0), "server": ("localhost", 80),   # not loopback: "open in another app" is refused, as it should be
    }
    sent = False
    out: dict[str, Any] = {"status": 500, "headers": [], "body": b""}
    chunks: list[bytes] = []

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            out["status"] = message["status"]
            out["headers"] = [[k.decode(), v.decode()] for k, v in message.get("headers", [])]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await _app(scope, receive, send)
    out["body"] = b"".join(chunks)
    return out
