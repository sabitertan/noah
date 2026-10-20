"""Talk to MCP servers: list their tools and call one.

A server is described the way MCP clients usually do (Claude Desktop, VS Code, Cursor):
`{"url": "https://…/mcp", "headers": {...}}` for a remote server (Streamable HTTP), or
`{"command": "npx", "args": [...], "env": {...}}` for a local one started as a child process (stdio).
`${NAME}` anywhere in a value is replaced with the environment variable NAME, so tokens can stay out of the files.

Connections are kept open and shared: a local server is started once and reused until it has been idle for a while. They
all live on one background event loop (`_Hub`), because the MCP SDK must open and close a connection in the same task,
while Noah calls tools from whichever thread or loop is handling a request.

Sign-in (OAuth): a remote server without an Authorization header gets the SDK's OAuth client (discovery, dynamic client
registration, PKCE, refresh). Signing in is always the user's explicit step (`sign_in`, the expert's Sign in button): it
returns the authorization URL for a popup, and `finish_sign_in` takes the code from Noah's callback route. Tokens are kept in
`data/mcp_oauth/` (owner-only) and refreshed by the SDK. A server that needs signing in during a chat raises SignInNeeded
instead of waiting for a browser nobody opened.

In the browser build (Pyodide) there are no threads or sockets, so the page does the talking: its MCP client (the official
TypeScript SDK, frontend/src/lib/mcp.js) connects, signs in (a popup, tokens in the browser's storage) and calls tools,
and `_page` waits for it with Pyodide's run_sync (WebAssembly JSPI), the way `web:` models are called. Only remote servers
work there, and only ones that allow the page's origin (CORS).
"""
from __future__ import annotations

import asyncio
import atexit
import base64
import concurrent.futures
import hashlib
import importlib.util
import json
import mimetypes
import os
import re
import shutil
import sys
import threading
import time
from collections.abc import Coroutine
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from . import filesvc

LIST_TIMEOUT = float(os.environ.get("NOAH_MCP_LIST_TIMEOUT", "180"))   # the first `npx -y …` downloads the server
CALL_TIMEOUT = float(os.environ.get("NOAH_MCP_CALL_TIMEOUT", "120"))
IDLE = float(os.environ.get("NOAH_MCP_IDLE", "600"))                   # seconds before an unused connection is closed
INPROC: dict[str, Any] = {}   # tests: {"inproc": "<key>"} connects to INPROC[key], an in-process MCPServer
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class McpError(RuntimeError):
    """Something about an MCP server went wrong; the message is shown to the user."""


class SignInNeeded(McpError):
    """The server wants OAuth and there is no (usable) token: the user has to press Sign in."""


IN_BROWSER = sys.platform == "emscripten"


def unavailable() -> str:
    """Why MCP servers cannot be used here, or '' when they can."""
    if IN_BROWSER:
        try:
            ffi: Any = importlib.import_module("pyodide.ffi")
        except ImportError:
            return "MCP servers need the page's MCP client, which is missing."
        return "" if ffi.can_run_sync() else ("In the browser, MCP servers need a browser that lets Noah's Python wait for the page "
                                               "(WebAssembly JSPI): a recent Chrome or Edge.")
    if importlib.util.find_spec("mcp") is None:
        return "MCP support is not installed (pip install mcp)."
    return ""


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        def var(m: re.Match[str]) -> str:
            if m.group(1) not in os.environ:
                raise McpError(f"The MCP server settings use ${{{m.group(1)}}}, but no such environment variable is set.")
            return os.environ[m.group(1)]
        return _VAR.sub(var, value)
    if isinstance(value, list):
        return [_expand(v) for v in cast(list[Any], value)]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in cast(dict[str, Any], value).items()}
    return value


def check_server(server: Any) -> dict[str, Any]:
    """A server description in the shape Noah stores, or McpError saying what is wrong with it."""
    if not isinstance(server, dict):
        raise McpError("A server is described by a url or a command.")
    s = cast(dict[str, Any], server)
    if "inproc" in s:
        return {"inproc": str(s["inproc"])}
    if s.get("url"):
        url = str(s["url"]).strip()
        if not re.match(r"^https?://[^\s/]+", url):
            raise McpError(f"{url} is not an http(s) address.")
        headers = s.get("headers") or {}
        if not isinstance(headers, dict) or not all(isinstance(v, str) for v in cast(dict[str, Any], headers).values()):
            raise McpError("headers must map names to text values.")
        return {"url": url, **({"headers": headers} if headers else {}), **({"oauth": True} if s.get("oauth") is True else {})}
    if s.get("command"):
        args, env = s.get("args") or [], s.get("env") or {}
        if not isinstance(args, list) or not all(isinstance(a, str) for a in cast(list[Any], args)):
            raise McpError("args must be a list of text values.")
        if not isinstance(env, dict) or not all(isinstance(v, str) for v in cast(dict[str, Any], env).values()):
            raise McpError("env must map names to text values.")
        out: dict[str, Any] = {"command": str(s["command"]).strip(), "args": args}
        if env:
            out["env"] = env
        if s.get("cwd"):
            out["cwd"] = str(s["cwd"])
        return out
    raise McpError("A server needs either a url (remote) or a command (started on this computer).")


def describe_server(server: dict[str, Any]) -> str:
    """For the UI and errors: where it is, without header or env values (they may be secrets)."""
    if server.get("url"):
        return str(server["url"])
    if server.get("command"):
        return " ".join([server["command"], *server.get("args", [])])
    return f"in-process {server.get('inproc')}"


# ---- OAuth ----
DEFAULT_REDIRECT = "http://127.0.0.1:8765/api/mcp/oauth/callback"   # until a sign-in tells us where Noah really is
SIGN_IN_TIMEOUT = 600.0


def _oauth_dir() -> Path:
    return Path(os.environ.get("NOAH_MCP_OAUTH", "data/mcp_oauth"))


def _token_path(url: str) -> Path:
    return _oauth_dir() / f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.json"


class _Storage:
    """The SDK's TokenStorage, as one owner-only file per server: its tokens and Noah's registration with its auth server."""

    def __init__(self, url: str) -> None:
        self.path = _token_path(url)

    def read(self) -> dict[str, Any]:
        try:
            return cast(dict[str, Any], json.loads(self.path.read_text()))
        except (OSError, ValueError):
            return {}

    def write(self, key: str, value: dict[str, Any] | None) -> None:
        data = self.read()
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data))
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    async def get_tokens(self) -> Any:
        """The SDK only refreshes a token whose expiry it knows, and it does not learn that from storage: so the expiry is
        kept here as a time, and an access token that has run out is handed over blank, which makes the SDK refresh first."""
        from mcp.shared.auth import OAuthToken
        t = dict(self.read().get("tokens") or {})
        if not t:
            return None
        expires_at = t.pop("expires_at", None)
        if expires_at is not None:
            left = int(expires_at - time.time())
            if left <= 30:
                t["access_token"] = ""
            t["expires_in"] = max(left, 0)
        return OAuthToken.model_validate(t)

    async def set_tokens(self, tokens: Any) -> None:
        data = tokens.model_dump(mode="json", exclude_none=True)
        if tokens.expires_in:
            data["expires_at"] = time.time() + tokens.expires_in
        self.write("tokens", data)

    async def get_client_info(self) -> Any:
        from mcp.shared.auth import OAuthClientInformationFull
        c = self.read().get("client")
        return OAuthClientInformationFull.model_validate(c) if c else None

    async def set_client_info(self, client_info: Any) -> None:
        self.write("client", client_info.model_dump(mode="json", exclude_none=True))

    def redirect(self) -> str:
        uris = self.read().get("client", {}).get("redirect_uris") or []
        return str(uris[0]) if uris else ""


class _SignIn:
    """One sign-in in progress: the authorization URL for the popup, then the code from the callback."""

    def __init__(self, redirect: str) -> None:
        loop = asyncio.get_running_loop()
        self.redirect = redirect
        self.url: asyncio.Future[str] = loop.create_future()
        self.code: asyncio.Future[Any] = loop.create_future()
        self.conn: _Conn | None = None


def _oauth(url: str, signin: _SignIn | None) -> Any:
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    storage = _Storage(url)
    redirect = signin.redirect if signin else storage.redirect() or DEFAULT_REDIRECT
    if signin and storage.redirect() and storage.redirect() != redirect:
        storage.write("client", None)   # registered for another address of Noah: register again

    async def on_redirect(auth_url: str) -> None:
        if signin is None:
            raise SignInNeeded(f"{url} needs you to sign in. Open this expert on the Experts page and press Sign in.")
        state = parse_qs(urlsplit(auth_url).query).get("state", [""])[0]
        _get_hub().pending[state] = signin
        signin.url.set_result(auth_url)

    async def on_callback() -> Any:
        assert signin is not None
        return await asyncio.wait_for(signin.code, SIGN_IN_TIMEOUT)

    meta = OAuthClientMetadata(client_name="Noah", redirect_uris=[redirect], response_types=["code"],  # pyright: ignore[reportArgumentType]
                               grant_types=["authorization_code", "refresh_token"], token_endpoint_auth_method="none")
    return OAuthClientProvider(server_url=url, client_metadata=meta, storage=storage, redirect_handler=on_redirect, callback_handler=on_callback)


async def _open(server: dict[str, Any], stack: AsyncExitStack, signin: _SignIn | None = None) -> Any:
    from mcp import Client, StdioServerParameters

    s = _expand(server)
    if "inproc" in s:
        return await stack.enter_async_context(Client(INPROC[s["inproc"]]))
    if "url" in s:
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        headers: dict[str, str] = s.get("headers") or {}
        auth = None if any(k.lower() == "authorization" for k in headers) else _oauth(s["url"], signin)   # a token given by hand wins
        http = await stack.enter_async_context(httpx2.AsyncClient(headers=headers, auth=auth, timeout=httpx2.Timeout(30, read=300)))
        return await stack.enter_async_context(Client(streamable_http_client(s["url"], http_client=http), read_timeout_seconds=CALL_TIMEOUT))
    from mcp.client.stdio import get_default_environment

    command = shutil.which(s["command"]) or s["command"]   # Windows: npx is npx.cmd
    params = StdioServerParameters(command=command, args=s.get("args", []), env={**get_default_environment(), **s.get("env", {})},
                                   cwd=s.get("cwd"))
    return await stack.enter_async_context(Client(params, read_timeout_seconds=CALL_TIMEOUT))


class _Conn:
    def __init__(self, signin: _SignIn | None = None) -> None:
        self.ready: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.stop = asyncio.Event()
        self.used = time.monotonic()
        self.signin = signin


class _Hub:
    """One background thread with an event loop that owns every connection."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.conns: dict[str, _Conn] = {}
        self.pending: dict[str, _SignIn] = {}   # OAuth state -> the sign-in waiting for its callback
        threading.Thread(target=self.loop.run_forever, name="noah-mcp", daemon=True).start()

    def run(self, coro: Coroutine[Any, Any, Any], timeout: float) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise McpError(f"The MCP server did not answer within {timeout:.0f} s.") from None

    async def _hold(self, key: str, server: dict[str, Any], conn: _Conn) -> None:
        try:
            async with AsyncExitStack() as stack:
                conn.ready.set_result(await _open(server, stack, conn.signin))
                await conn.stop.wait()
        except BaseException as e:  # noqa: BLE001  (an ExceptionGroup from the transport too)
            if not conn.ready.done():
                conn.ready.set_exception(e)
        finally:
            if self.conns.get(key) is conn:
                del self.conns[key]

    async def client(self, server: dict[str, Any]) -> Any:
        now = time.monotonic()
        for k, c in list(self.conns.items()):   # close what nobody used for a while
            if now - c.used > IDLE:
                c.stop.set()
                self.conns.pop(k, None)
        key = json.dumps(server, sort_keys=True)
        conn = self.conns.get(key)
        if conn is None:
            conn = self.conns[key] = _Conn()
            asyncio.ensure_future(self._hold(key, server, conn))
        elif conn.signin and not conn.ready.done():
            raise SignInNeeded(f"Signing in to {describe_server(server)} is not finished yet. Finish it in the sign-in window, or press Sign in again.")
        conn.used = now
        return await self._ready(conn, server)

    async def _ready(self, conn: _Conn, server: dict[str, Any]) -> Any:
        try:
            return await conn.ready
        except BaseException as e:
            root = _root(e)
            if isinstance(root, McpError):
                raise root from None
            raise McpError(f"Could not connect to the MCP server ({describe_server(server)}): {_reason(e)}") from None

    async def sign_in(self, server: dict[str, Any], redirect: str) -> dict[str, Any]:
        """Start signing in: {"url": the authorization page} for the popup, or {"signed_in": True} if no sign-in was needed."""
        await self.drop(server)
        signin = _SignIn(redirect)
        key = json.dumps(server, sort_keys=True)
        conn = signin.conn = self.conns[key] = _Conn(signin)
        asyncio.ensure_future(self._hold(key, server, conn))
        await asyncio.wait([signin.url, conn.ready], return_when=asyncio.FIRST_COMPLETED)
        if signin.url.done():
            return {"url": signin.url.result()}
        await self._ready(conn, server)
        return {"signed_in": True}

    async def finish_sign_in(self, state: str, code: str, iss: str | None) -> None:
        """The authorization server sent the user back with a code: exchange it and finish connecting."""
        from mcp.shared.auth import AuthorizationCodeResult

        signin = self.pending.pop(state, None)
        if signin is None or signin.code.done():
            raise McpError("This sign-in has expired or was already used. Press Sign in again.")
        signin.code.set_result(AuthorizationCodeResult(code=code, state=state, iss=iss))
        assert signin.conn is not None
        try:
            await asyncio.wait_for(asyncio.shield(signin.conn.ready), 60)
        except BaseException as e:
            root = _root(e)
            raise root if isinstance(root, McpError) else McpError(f"Signing in failed: {_reason(e)}") from None
        signin.conn.signin = None   # an ordinary kept connection from now on

    async def drop(self, server: dict[str, Any]) -> None:
        conn = self.conns.pop(json.dumps(server, sort_keys=True), None)
        if conn:
            conn.stop.set()

    async def close_all(self) -> None:
        for c in list(self.conns.values()):
            c.stop.set()
        self.conns.clear()
        await asyncio.sleep(0.5)   # let the servers shut down


def _root(e: BaseException) -> BaseException:
    """The first real cause inside exception groups (the transport's task groups wrap what went wrong)."""
    while getattr(e, "exceptions", None):
        e = cast(Any, e).exceptions[0]
    return e


def _reason(e: BaseException) -> str:
    e = _root(e)
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__


_hub: _Hub | None = None
_hub_lock = threading.Lock()


def _get_hub() -> _Hub:
    global _hub
    if why := unavailable():
        raise McpError(why)
    with _hub_lock:
        if _hub is None:
            _hub = _Hub()
            atexit.register(lambda: _hub and _hub.run(_hub.close_all(), 5))
        return _hub


async def _with_retry(hub: _Hub, server: dict[str, Any], work: Any) -> Any:
    """Run `work(client)`; if a kept connection has gone stale (the server exited, the session expired), reconnect once."""
    for attempt in (1, 2):
        client = await hub.client(server)
        try:
            return await work(client)
        except McpError:
            raise
        except Exception as e:
            await hub.drop(server)
            if isinstance(_root(e), McpError):
                raise _root(e) from None
            if attempt == 2:
                raise McpError(f"The MCP server ({describe_server(server)}) failed: {_reason(e)}") from None


def disconnect(server: dict[str, Any]) -> None:
    """Close a kept connection (a local server is stopped); the next call reconnects."""
    if IN_BROWSER:
        _page("disconnect", server, 10)
    elif _hub is not None:
        _hub.run(_hub.drop(server), 10)


def close_all() -> None:
    if _hub is not None:
        _hub.run(_hub.close_all(), 10)


def _page(op: str, server: dict[str, Any], timeout: float, **kw: Any) -> Any:
    """Browser build: the page's MCP client does `op`; Python waits for it (run_sync)."""
    ffi: Any = importlib.import_module("pyodide.ffi")
    js: Any = importlib.import_module("js")
    body = json.dumps({"op": op, "server": _expand(server), **kw})
    reply: dict[str, Any] = json.loads(ffi.run_sync(js.noahMcp(body, int(timeout * 1000))))
    if "error" in reply:
        raise (SignInNeeded if reply.get("sign_in") else McpError)(reply["error"])
    return reply.get("result")


def _tool(t: dict[str, Any]) -> dict[str, Any]:
    """A tool as MCP sends it (camelCase JSON, from either SDK) in the shape Noah keeps in tools.json."""
    names = {"readOnlyHint": "read_only_hint", "destructiveHint": "destructive_hint", "idempotentHint": "idempotent_hint",
             "openWorldHint": "open_world_hint", "title": "title"}
    return {"name": t["name"], "title": t.get("title") or "", "description": (t.get("description") or "").strip(),
            "input_schema": t.get("inputSchema") or {"type": "object", "properties": {}},
            "annotations": {names[k]: v for k, v in (t.get("annotations") or {}).items() if k in names}}


def sign_in(server: dict[str, Any], redirect: str) -> dict[str, Any]:
    if not server.get("url"):
        raise McpError("Only remote (url) servers sign in.")
    if IN_BROWSER:
        raise McpError("In the browser, signing in happens in the page (the expert's Sign in button).")
    hub = _get_hub()
    return hub.run(hub.sign_in(server, redirect), 60)


def finish_sign_in(state: str, code: str, iss: str | None = None) -> None:
    hub = _get_hub()
    hub.run(hub.finish_sign_in(state, code, iss), 90)


def signed_in(server: dict[str, Any]) -> bool:
    return bool(server.get("url")) and bool(_Storage(str(server["url"])).read().get("tokens"))


def sign_out(server: dict[str, Any]) -> None:
    if server.get("url"):
        _token_path(str(server["url"])).unlink(missing_ok=True)
    disconnect(server)


def list_tools(server: dict[str, Any]) -> list[dict[str, Any]]:
    """Every tool the server offers: name, description, input schema and annotations (read-only, destructive, …)."""
    if IN_BROWSER:
        return [_tool(t) for t in _page("list_tools", server, LIST_TIMEOUT)]

    async def work(client: Any) -> list[dict[str, Any]]:
        tools: list[Any] = []
        cursor = None
        while True:
            page = await client.list_tools(cursor=cursor)
            tools += page.tools
            cursor = page.next_cursor
            if not cursor:
                return [_tool(t.model_dump(mode="json", by_alias=True, exclude_none=True)) for t in tools]

    hub = _get_hub()
    return hub.run(_with_retry(hub, server, work), LIST_TIMEOUT)


def call_tool(server: dict[str, Any], expert: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """{text, files, is_error}: text parts joined; images and binary resources saved under the files folder (data/files/mcp/)
    so the chat can show or download them like any file."""
    if IN_BROWSER:
        return _result(_page("call_tool", server, CALL_TIMEOUT, tool=tool, arguments=arguments), expert)

    async def work(client: Any) -> Any:
        res = await client.call_tool(tool, arguments)
        return res.model_dump(mode="json", by_alias=True, exclude_none=True)

    hub = _get_hub()
    return _result(hub.run(_with_retry(hub, server, work), CALL_TIMEOUT), expert)


def _save(expert: str, data: bytes, mime: str) -> dict[str, Any]:
    ext = mimetypes.guess_extension(mime or "") or ".bin"
    folder = filesvc.files_root() / "mcp"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{expert}-{hashlib.sha256(data).hexdigest()[:12]}{ext}"
    path.write_bytes(data)
    return filesvc.describe(path)


def _result(res: dict[str, Any], expert: str) -> dict[str, Any]:
    """A tool result as MCP sends it (camelCase JSON, from either SDK)."""
    text: list[str] = []
    files: list[dict[str, Any]] = []
    for block in res.get("content") or []:
        kind = block.get("type", "")
        if kind == "text":
            text.append(block.get("text", ""))
        elif kind in ("image", "audio"):
            files.append(_save(expert, base64.b64decode(block["data"]), block.get("mimeType", "")))
        elif kind == "resource":
            r = block.get("resource") or {}
            if r.get("text") is not None:
                text.append(r["text"])
            elif r.get("blob"):
                files.append(_save(expert, base64.b64decode(r["blob"]), r.get("mimeType", "")))
        elif kind == "resource_link":
            text.append(f"{block.get('name', '')}: {block.get('uri', '')}")
    if not text and res.get("structuredContent") is not None:
        text.append("```json\n" + json.dumps(res["structuredContent"], indent=2, ensure_ascii=False) + "\n```")
    return {"text": "\n\n".join(text).strip(), "files": files, "is_error": bool(res.get("isError"))}
