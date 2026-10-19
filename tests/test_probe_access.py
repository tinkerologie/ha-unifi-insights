# Copyright 2026 UniFi Insights contributors
"""Tests for the Access probe: every status the config flow maps."""

from __future__ import annotations

import json

from unifi_official_api.access import UniFiAccessClient

from custom_components.unifi_insights.probe import ProbeStatus, async_probe_access
from tests.fixtures.http_fakes import FakeResponse, fake_session


def _client(*responses: FakeResponse) -> UniFiAccessClient:
    return UniFiAccessClient(
        token="token-value",  # noqa: S106 - fixture token, not a credential
        host="192.168.1.1",
        session=fake_session(*responses),
    )


def _doors(data: list[dict[str, object]]) -> FakeResponse:
    return FakeResponse(200, json.dumps({"code": "SUCCESS", "data": data}))


async def test_doors_mean_available() -> None:
    result = await async_probe_access(_client(_doors([{"id": "d1"}])))
    assert result.status is ProbeStatus.AVAILABLE
    assert result.error is None


async def test_no_doors_mean_empty() -> None:
    result = await async_probe_access(_client(_doors([])))
    assert result.status is ProbeStatus.EMPTY


async def test_rejected_token_is_auth_failed() -> None:
    result = await async_probe_access(_client(FakeResponse(401, "{}")))
    assert result.status is ProbeStatus.AUTH_FAILED


async def test_forbidden_token_is_auth_failed() -> None:
    result = await async_probe_access(_client(FakeResponse(403, "{}")))
    assert result.status is ProbeStatus.AUTH_FAILED


async def test_missing_api_is_unsupported() -> None:
    result = await async_probe_access(_client(FakeResponse(404, "<html>")))
    assert result.status is ProbeStatus.UNSUPPORTED


async def test_web_page_is_unsupported() -> None:
    result = await async_probe_access(_client(FakeResponse(200, "<html>ui</html>")))
    assert result.status is ProbeStatus.UNSUPPORTED


async def test_server_error_is_unreachable() -> None:
    result = await async_probe_access(_client(FakeResponse(503, "{}")))
    assert result.status is ProbeStatus.UNREACHABLE


async def test_envelope_without_success_code_is_unsupported() -> None:
    # The console answered, but not with the documented SUCCESS envelope, so
    # it is treated as an API that is not usable here, not as a fault.
    result = await async_probe_access(
        _client(FakeResponse(200, json.dumps({"data": 1})))
    )
    assert result.status is ProbeStatus.UNSUPPORTED
    assert result.error is not None
