"""Signing in to an MCP server with OAuth (server and desktop builds). The server is a real one over HTTP with its own
authorization server (tests/mcp_servers/oauth_http.py: discovery, dynamic client registration, PKCE, refresh tokens); the
"browser" is httpx following the sign-in page's redirect back to Noah's callback route."""
import json
import shutil
import time
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from noah import mcp_client, mcp_expert, server
from tests.mcp_servers.oauth_http import Running

CODE = {"x-noah-code": "1"}


@pytest.fixture(scope="module")
def remote() -> Iterator[Running]:
    srv = Running(auth=True)
    yield srv
    srv.stop()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    (tmp_path / "experts").mkdir()
    shutil.copytree(Path("experts/calc"), tmp_path / "experts" / "calc", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(server, "EXPERTS_DIR", tmp_path / "experts")
    monkeypatch.setattr(server, "VERSIONS", tmp_path / "data" / "versions")
    monkeypatch.setattr(server, "DISABLED", tmp_path / "data" / "disabled.json")
    monkeypatch.setattr(server, "CODE_EDIT", "1")
    monkeypatch.setenv("NOAH_MCP_OAUTH", str(tmp_path / "data" / "mcp_oauth"))
    monkeypatch.setenv("NOAH_MCP_ALLOWED", str(tmp_path / "data" / "mcp_allowed.json"))
    server._reload_experts()  # pyright: ignore[reportPrivateUsage]
    yield TestClient(server.app)
    mcp_client.close_all()
    monkeypatch.undo()
    server._reload_experts()  # pyright: ignore[reportPrivateUsage]


def add(client: TestClient, remote: Running) -> dict[str, Any]:
    config = json.dumps({"mcpServers": {"secure": {"url": remote.url}}})
    r = client.post("/api/mcp/servers", json={"config": config}, headers=CODE)
    assert r.status_code == 200, r.text
    return r.json()


def sign_in(client: TestClient) -> httpx.Response:
    """What the Sign in button and the popup do: start, follow the authorization page back to Noah, finish."""
    start = client.post("/api/mcp/secure/signin").json()
    assert set(start) == {"url"}, start
    auth = httpx.get(start["url"], follow_redirects=False)            # the server's sign-in page approves at once
    back = urlsplit(auth.headers["location"])
    assert back.path == server.OAUTH_CALLBACK
    return client.get(f"{back.path}?{back.query}")


def card(client: TestClient) -> dict[str, Any]:
    return next(e for e in client.get("/api/config").json()["experts"] if e["name"] == "secure")


def test_a_server_that_needs_sign_in_is_added_and_asks_for_it(client: TestClient, remote: Running) -> None:
    out = add(client, remote)
    assert out["failed"] == [] and out["added"][0]["sign_in"] is True and out["added"][0]["tools"] == 0
    assert card(client) | {"tools": []} == card(client) and card(client)["oauth"] is True and card(client)["signed_in"] is False
    r = client.post("/api/mcp/secure/refresh", json={})
    assert r.status_code == 401 and "Sign in" in r.json()["detail"]


def test_signing_in_lists_the_tools_and_they_run(client: TestClient, remote: Running) -> None:
    add(client, remote)
    page = sign_in(client)
    assert page.status_code == 200 and "Signed in" in page.text and "noah-mcp-oauth" in page.text
    assert card(client)["signed_in"] is True
    assert client.post("/api/mcp/secure/refresh", json={}).json()["tools"] == 2
    secure = server.experts["secure"]
    assert "add_secret_note" in secure.description   # routing text Noah wrote before the tools were known is rewritten
    assert mcp_expert.call(secure, "list_secret_notes", {})["text"] == "no private notes yet"


def test_tokens_are_kept_and_refreshed_without_signing_in_again(client: TestClient, remote: Running, monkeypatch: pytest.MonkeyPatch) -> None:
    assert remote.provider
    monkeypatch.setattr(remote.provider, "lifetime", 35)   # access tokens that are nearly used up when they are issued
    add(client, remote)
    sign_in(client)
    client.post("/api/mcp/secure/refresh", json={})
    asked = remote.provider.authorized
    mcp_client.close_all()                                   # like a restart: the next call reconnects from the stored tokens
    secure = server.experts["secure"]
    assert mcp_expert.call(secure, "add_secret_note", {"text": "a"})["text"].startswith("saved private note")
    time.sleep(6)                                            # past the stored expiry (30 s margin): the refresh token gets a new one
    mcp_client.close_all()
    assert mcp_expert.call(secure, "list_secret_notes", {})["text"]
    assert remote.provider.authorized == asked               # nobody was sent to the sign-in page again
    assert len(remote.provider.access) >= 2                  # and a new access token was issued


def test_signing_out_forgets_the_tokens(client: TestClient, remote: Running, tmp_path: Path) -> None:
    add(client, remote)
    sign_in(client)
    assert list((tmp_path / "data" / "mcp_oauth").iterdir())
    assert client.delete("/api/mcp/secure/signin").json() == {"signed_in": False}
    assert not any("tokens" in json.loads(f.read_text()) for f in (tmp_path / "data" / "mcp_oauth").iterdir())
    assert client.post("/api/mcp/secure/refresh", json={}).status_code == 401


def test_during_a_chat_a_missing_sign_in_is_said_not_waited_for(client: TestClient, remote: Running) -> None:
    add(client, remote)
    secure = server.experts["secure"]
    with pytest.raises(mcp_client.SignInNeeded, match="press Sign in"):
        mcp_client.call_tool(secure.server, "secure", "list_secret_notes", {})


def test_a_stale_or_refused_sign_in_shows_why(client: TestClient, remote: Running) -> None:
    add(client, remote)
    stale = client.get(f"{server.OAUTH_CALLBACK}?state=nope&code=x")
    assert "Sign-in failed" in stale.text and "expired" in stale.text
    refused = client.get(f"{server.OAUTH_CALLBACK}?error=access_denied&error_description=You+said+no")
    assert "Sign-in failed" in refused.text and "You said no" in refused.text


def test_the_callback_works_without_the_desktop_apps_launch_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "LAUNCH_TOKEN", "secret")
    assert client.get("/api/config").status_code == 401
    assert client.get(f"{server.OAUTH_CALLBACK}?state=x&code=y").status_code == 200
