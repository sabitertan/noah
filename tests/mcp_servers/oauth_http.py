"""An MCP server over Streamable HTTP that requires OAuth, for tests/test_mcp_oauth.py and the browser build's check.

Its authorization server runs in the same process and approves every request at once: /authorize redirects straight back
to the client with a code (no login page). Everything else is the real thing: protected-resource and authorization-server
metadata, dynamic client registration, PKCE, access and refresh tokens. CORS is open, so a page on another origin (the
browser build) can use it too.

On its own:  python tests/mcp_servers/oauth_http.py 8931            (OAuth)
             python tests/mcp_servers/oauth_http.py 8932 --no-auth  (no sign-in)
"""
from __future__ import annotations

import secrets
import socket
import sys
import threading
import time
from typing import Any

import uvicorn
from mcp.server.auth.provider import AccessToken, AuthorizationCode, AuthorizationParams, RefreshToken, construct_redirect_uri
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from mcp_types import ToolAnnotations
from starlette.middleware.cors import CORSMiddleware


class AutoApprove:
    """An authorization server that says yes to everyone, and counts how often it was asked."""

    def __init__(self) -> None:
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.access: dict[str, AccessToken] = {}
        self.refresh: dict[str, RefreshToken] = {}
        self.authorized = 0
        self.lifetime = 3600

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        assert client_info.client_id
        self.clients[client_info.client_id] = client_info

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        self.authorized += 1
        code = secrets.token_urlsafe(24)
        self.codes[code] = AuthorizationCode(code=code, scopes=params.scopes or ["notes"], expires_at=time.time() + 300,
                                             client_id=client.client_id or "", code_challenge=params.code_challenge,
                                             redirect_uri=params.redirect_uri, resource=params.resource,
                                             redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly)
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        return self.codes.get(authorization_code)

    def _issue(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        self.access[access] = AccessToken(token=access, client_id=client_id, scopes=scopes, resource=resource,
                                          expires_at=int(time.time()) + self.lifetime)
        self.refresh[refresh] = RefreshToken(token=refresh, client_id=client_id, scopes=scopes, resource=resource)
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=self.lifetime, refresh_token=refresh, scope=" ".join(scopes))

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        self.codes.pop(authorization_code.code, None)
        return self._issue(authorization_code.client_id, authorization_code.scopes, authorization_code.resource)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        return self.refresh.get(refresh_token)

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        self.refresh.pop(refresh_token.token, None)
        return self._issue(refresh_token.client_id, scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        t = self.access.get(token)
        return t if t and (t.expires_at or 0) > time.time() else None

    async def revoke_token(self, token: Any) -> None:
        self.access.pop(getattr(token, "token", ""), None)

    def expire_access_tokens(self) -> None:
        self.access.clear()


def make_app(base: str, auth: bool = True) -> tuple[Any, AutoApprove | None]:
    provider = AutoApprove() if auth else None
    settings = AuthSettings(issuer_url=base, resource_server_url=f"{base}/mcp", required_scopes=["notes"], validate_token_resource=True,  # pyright: ignore[reportArgumentType]
                            client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=["notes"], default_scopes=["notes"])) if auth else None
    srv = MCPServer("secure-notes", auth_server_provider=provider, auth=settings)
    notes: list[str] = []

    @srv.tool(description="Save a short private note.")
    def add_secret_note(text: str) -> str:
        notes.append(text)
        return f"saved private note {len(notes)}"

    @srv.tool(description="List the private notes.", annotations=ToolAnnotations(read_only_hint=True))
    def list_secret_notes() -> str:
        return "\n".join(notes) or "no private notes yet"

    asgi = srv.streamable_http_app(transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    return CORSMiddleware(asgi, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                          expose_headers=["mcp-session-id", "mcp-protocol-version", "www-authenticate"]), provider


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Running:
    """The server on a free port in a background thread: `.url` is its MCP endpoint, `.provider` its authorization server."""

    def __init__(self, auth: bool = True, port: int = 0) -> None:
        self.port = port or free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.url = f"{self.base}/mcp"
        app, self.provider = make_app(self.base, auth)
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        while not self.server.started:
            time.sleep(0.02)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(5)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8931
    app, _ = make_app(f"http://127.0.0.1:{port}", auth="--no-auth" not in sys.argv)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
