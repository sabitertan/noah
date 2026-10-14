"""Typed shapes for the stand-ins tests patch in, so strict type checking knows their parameters."""
from typing import Any, Protocol

import httpx


class HttpCall(Protocol):
    """httpx.get / httpx.post as the code under test calls them: a URL, then keyword arguments."""

    def __call__(self, url: str, /, **kw: Any) -> Any: ...


class AnyCall(Protocol):
    def __call__(self, *a: Any, **k: Any) -> Any: ...


def http(fn: HttpCall) -> HttpCall:
    return fn


def anycall(fn: AnyCall) -> AnyCall:
    return fn


def returns(value: Any) -> AnyCall:
    """A stand-in that ignores its arguments and returns `value`."""
    return lambda *a, **k: value


def resp(status: int = 200, body: Any = None, url: str = "http://x/") -> httpx.Response:
    return httpx.Response(status, json=body if body is not None else {}, request=httpx.Request("POST", url))


def reply(text: str = "ok") -> dict[str, Any]:
    """An OpenAI-style chat completion with one message."""
    return {"choices": [{"message": {"content": text}}]}
