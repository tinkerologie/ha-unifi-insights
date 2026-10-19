# Copyright 2026 UniFi Insights contributors
"""aiohttp transport doubles for tests that drive a real ``unifi_official_api`` client.

The integration tests that exercise the library at its HTTP seam (voucher
buttons, rule export, Access probing) replay canned responses through these
doubles instead of patching client methods.
"""

from __future__ import annotations

import json
from typing import Any, Self
from unittest.mock import MagicMock

from aiohttp import ClientSession
from yarl import URL


class FakeJsonResponse:
    """Minimal aiohttp response replacement for transport tests."""

    def __init__(
        self,
        body: Any = None,
        status: int = 200,
        headers: dict[str, str] | None = None,
        *,
        is_json: bool = True,
    ) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}
        self.url = URL("https://192.168.1.1")
        self.method = "GET"
        self.history = ()
        self._is_json = is_json

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def text(self) -> str:
        if isinstance(self._body, (dict, list)):
            return json.dumps(self._body)
        if self._body is None:
            return ""
        return str(self._body)

    async def json(self) -> Any:
        if not self._is_json or self._body is None:
            msg = "No JSON body"
            raise ValueError(msg)
        return self._body


class FakeQueuedSession:
    """Mock session recording requests and returning queued responses."""

    closed = False

    def __init__(
        self, responses: list[FakeJsonResponse | dict[str, Any] | list[Any] | None]
    ) -> None:
        self._responses = iter(
            [
                r if isinstance(r, FakeJsonResponse) else FakeJsonResponse(r)
                for r in responses
            ]
        )
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: object, **kwargs: Any) -> FakeJsonResponse:
        self.requests.append({"method": method, "url": str(url), **kwargs})
        return next(self._responses)

    async def close(self) -> None:
        self.closed = True


class FakeResponse:
    """Minimal aiohttp response double with a raw text body."""

    def __init__(
        self,
        status: int,
        body: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}
        self.url = URL("https://192.168.1.1:12445/api/v1/developer/doors")
        self.method = "GET"
        self.history: tuple[Any, ...] = ()
        self.content_type = "application/json"

    async def text(self) -> str:
        return self._body

    async def json(self) -> Any:
        return json.loads(self._body)


class _FakeRequest:
    """Async context manager returned by ``_FakeSession.request``."""

    def __init__(self, response: FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> FakeResponse:
        return self._response

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeSession:
    """Records requests and replays canned responses in order."""

    def __init__(self, *responses: FakeResponse) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: URL,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> _FakeRequest:
        self.calls.append(
            {
                "method": method,
                "url": str(url),
                "params": params,
                "json": json,
                "headers": headers or {},
                "kwargs": kwargs,
            }
        )
        return _FakeRequest(self._responses.pop(0))


def fake_session(*responses: FakeResponse) -> ClientSession:
    """``ClientSession`` spec double that replays ``responses`` in order."""
    fake = _FakeSession(*responses)
    session = MagicMock(spec=ClientSession)
    session.closed = False
    session.request.side_effect = fake.request
    session.calls = fake.calls
    return session
