# Copyright 2026 UniFi Insights contributors
"""Tests for classifying Network/Protect probe results."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from pydantic import BaseModel, ValidationError
from unifi_official_api import (
    ApiKeyAuth,
    ConnectionType,
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiNotFoundError,
    UniFiRateLimitError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from unifi_official_api.innerspace import UniFiInnerSpaceClient
from unifi_official_api.network import UniFiNetworkClient
from unifi_official_api.protect import UniFiProtectClient

from custom_components.unifi_insights.probe import (
    ProbeStatus,
    async_probe_carrier_fabric,
    async_probe_innerspace,
    async_probe_network,
    async_probe_protect,
    async_probe_with_client,
    classify_error,
    is_transient_error,
)


def _validation_error() -> ValidationError:
    class _Model(BaseModel):
        value: int

    try:
        _Model.model_validate({"value": "not an int"})
    except ValidationError as err:
        return err
    raise AssertionError  # pragma: no cover


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (UniFiAuthenticationError("Unauthorized", status_code=401), "auth_failed"),
        (UniFiAuthenticationError("Forbidden", status_code=403), "auth_failed"),
        (UniFiConnectionError("Connection refused"), "unreachable"),
        (UniFiTimeoutError("Timed out"), "unreachable"),
        (UniFiRateLimitError("Slow down", status_code=429), "unreachable"),
        (UniFiResponseError("Bad gateway", status_code=502), "unreachable"),
        (UniFiResponseError("Server error", status_code=500), "unreachable"),
        (UniFiNotFoundError("Not found", status_code=404), "unsupported"),
        (UniFiResponseError("Bad request", status_code=400), "unsupported"),
        (UniFiResponseError("HTML page", status_code=200), "unsupported"),
        (UniFiResponseError("Redirect", status_code=302), "unsupported"),
        (ValueError("Unexpected"), "error"),
    ],
    ids=[
        "401",
        "403",
        "connection",
        "timeout",
        "429",
        "502",
        "500",
        "404",
        "400",
        "200-non-json",
        "302",
        "unexpected",
    ],
)
def test_classify_error(error: Exception, expected: str) -> None:
    """Each client error maps to one probe status."""
    assert classify_error(error) == ProbeStatus(expected)
    assert is_transient_error(error) is (expected == "unreachable")


async def test_probe_network_statuses() -> None:
    """Sites are available, an empty list is empty, errors are classified."""
    client = MagicMock()
    client.sites.get_all = AsyncMock(return_value=[MagicMock(id="default")])
    result = await async_probe_network(client)
    assert result.status is ProbeStatus.AVAILABLE
    assert len(result.sites) == 1

    client.sites.get_all = AsyncMock(return_value=[])
    assert (await async_probe_network(client)).status is ProbeStatus.EMPTY

    error = UniFiResponseError("Server error", status_code=503)
    client.sites.get_all = AsyncMock(side_effect=error)
    result = await async_probe_network(client)
    assert result.status is ProbeStatus.UNREACHABLE
    assert result.error is error
    client.sites.get_all.assert_awaited_once_with(expected_unsupported=True)


@pytest.mark.parametrize(
    ("cameras", "nvr", "expected"),
    [
        ([MagicMock()], None, "available"),
        ([], MagicMock(id="nvr1"), "available"),
        ([], None, "empty"),
        ([], ValueError("NVR not found"), "empty"),
        ([], "validation", "error"),
        ([], UniFiResponseError("Server error", status_code=503), "unreachable"),
        ([], UniFiAuthenticationError("Unauthorized", status_code=401), "auth_failed"),
        (UniFiTimeoutError("Timed out"), None, "unreachable"),
        (UniFiNotFoundError("Not found", status_code=404), None, "unsupported"),
    ],
    ids=[
        "cameras",
        "camera-free-nvr",
        "no-nvr-record",
        "nvr-not-found",
        "nvr-parse-error",
        "nvr-5xx",
        "nvr-401",
        "cameras-timeout",
        "cameras-404",
    ],
)
async def test_probe_protect_statuses(
    cameras: object, nvr: object, expected: str
) -> None:
    """A camera-free NVR is usable; the NVR probe's own errors are classified."""
    client = MagicMock()
    if isinstance(cameras, Exception):
        client.cameras.get_all = AsyncMock(side_effect=cameras)
    else:
        client.cameras.get_all = AsyncMock(return_value=cameras)
    if nvr == "validation":
        client.nvr.get = AsyncMock(side_effect=_validation_error())
    elif isinstance(nvr, Exception):
        client.nvr.get = AsyncMock(side_effect=nvr)
    else:
        client.nvr.get = AsyncMock(return_value=nvr)

    result = await async_probe_protect(client)

    assert result.status is ProbeStatus(expected)
    client.cameras.get_all.assert_awaited_once_with(expected_unsupported=True)


async def test_probe_with_client_classifies_context_errors() -> None:
    """An error while opening the client is classified, not raised."""
    context = MagicMock()
    context.__aenter__ = AsyncMock(side_effect=UniFiConnectionError("Refused"))
    context.__aexit__ = AsyncMock(return_value=None)

    result = await async_probe_with_client(context, async_probe_network)

    assert result.status is ProbeStatus.UNREACHABLE


@pytest.mark.parametrize(
    ("project", "floor_plans", "inventory", "expected"),
    [
        (
            MagicMock(
                project=MagicMock(id="proj-1"),
                plans=[],
                products=[],
            ),
            [],
            [],
            "available",
        ),
        (
            MagicMock(project=None, plans=[], products=[]),
            [MagicMock(id="fp-1")],
            [],
            "available",
        ),
        (
            MagicMock(project=None, plans=[], products=[]),
            [],
            [MagicMock(id="inv-1")],
            "available",
        ),
        (
            MagicMock(project=None, plans=[], products=[]),
            [],
            [],
            "empty",
        ),
        (
            UniFiNotFoundError("Not found", status_code=404),
            [],
            [],
            "unsupported",
        ),
        (
            UniFiAuthenticationError("Unauthorized", status_code=401),
            [],
            [],
            "auth_failed",
        ),
        (
            UniFiTimeoutError("Timed out"),
            [],
            [],
            "unreachable",
        ),
        (
            MagicMock(project=None, plans=[], products=[]),
            UniFiResponseError("Bad gateway", status_code=502),
            [],
            "unreachable",
        ),
    ],
    ids=[
        "project-id",
        "floor-plans-fallback",
        "inventory-fallback",
        "all-empty",
        "404-unsupported",
        "401-auth-failed",
        "timeout-unreachable",
        "secondary-502-unreachable",
    ],
)
async def test_probe_innerspace_statuses(
    project: object,
    floor_plans: object,
    inventory: object,
    expected: str,
) -> None:
    """InnerSpace probe classifies available, empty, unsupported, and errors."""
    client = MagicMock()
    if isinstance(project, Exception):
        client.get_project = AsyncMock(side_effect=project)
    else:
        client.get_project = AsyncMock(return_value=project)
    if isinstance(floor_plans, Exception):
        client.list_floor_plans = AsyncMock(side_effect=floor_plans)
    else:
        client.list_floor_plans = AsyncMock(return_value=floor_plans)
    client.list_access_points = AsyncMock(return_value=[])
    client.list_switches = AsyncMock(return_value=[])
    client.list_inventory = AsyncMock(return_value=inventory)

    result = await async_probe_innerspace(client)

    assert result.status is ProbeStatus(expected)
    client.get_project.assert_awaited_once_with(expected_unsupported=True)


async def test_probe_innerspace_html_response_logs_no_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """InnerSpace probe classifies HTML as UNSUPPORTED without logging WARNING."""
    client = UniFiInnerSpaceClient(
        auth=ApiKeyAuth(api_key="test-key"),
        base_url="https://192.168.1.1",
        connection_type=ConnectionType.LOCAL,
    )
    response = MagicMock()
    response.status = 200
    response.text = AsyncMock(
        return_value="<!doctype html><html lang='en'><title>UniFi OS</title></html>"
    )
    response.headers = {}
    response.method = "GET"
    response.history = ()
    response.url = MagicMock()
    response.url.path = "/proxy/innerspace/integration/v1/project"
    response.json = AsyncMock(
        side_effect=aiohttp.ContentTypeError(MagicMock(), MagicMock())
    )

    request_ctx = MagicMock()
    request_ctx.__aenter__ = AsyncMock(return_value=response)
    request_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.request = MagicMock(return_value=request_ctx)
    client._ensure_session = AsyncMock(return_value=mock_session)
    client._throttle = AsyncMock()

    with caplog.at_level(logging.DEBUG):
        result = await async_probe_innerspace(client)

    assert result.status is ProbeStatus.UNSUPPORTED
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "InnerSpace API probe (project): unsupported" in r.getMessage()
        and "200" in r.getMessage()
        for r in caplog.records
        if r.levelno == logging.DEBUG
    )


@pytest.mark.parametrize(
    ("client_cls", "probe", "path", "probe_log"),
    [
        (
            UniFiNetworkClient,
            async_probe_network,
            "/proxy/network/integration/v1/sites",
            "Network API probe: unsupported",
        ),
        (
            UniFiProtectClient,
            async_probe_protect,
            "/proxy/protect/integration/v1/cameras",
            "Protect API probe (cameras): unsupported",
        ),
    ],
    ids=["network", "protect"],
)
async def test_probe_absent_application_html_logs_no_warning(
    caplog: pytest.LogCaptureFixture,
    client_cls: type[UniFiNetworkClient | UniFiProtectClient],
    probe: Any,
    path: str,
    probe_log: str,
) -> None:
    """A console without the application does not warn at setup (issue #196).

    It answers the application's path with the UniFi OS HTML page at status
    200. The probe classifies that as UNSUPPORTED, so setup carries on
    without the application; the page is expected here, not a fault.
    """
    client = client_cls(
        auth=ApiKeyAuth(api_key="test-key"),
        base_url="https://192.168.1.1",
        connection_type=ConnectionType.LOCAL,
    )
    response = MagicMock()
    response.status = 200
    response.text = AsyncMock(
        return_value="<!doctype html><html lang='en'><title>UniFi OS</title></html>"
    )
    response.headers = {}
    response.method = "GET"
    response.history = ()
    response.url = MagicMock()
    response.url.path = path
    response.json = AsyncMock(
        side_effect=aiohttp.ContentTypeError(MagicMock(), MagicMock())
    )

    request_ctx = MagicMock()
    request_ctx.__aenter__ = AsyncMock(return_value=response)
    request_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.request = MagicMock(return_value=request_ctx)
    client._ensure_session = AsyncMock(return_value=mock_session)
    client._throttle = AsyncMock()

    with caplog.at_level(logging.DEBUG):
        result = await probe(client)

    assert result.status is ProbeStatus.UNSUPPORTED
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    debug_messages = [
        r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG
    ]
    assert any(
        f"Expected unsupported-endpoint non-JSON response for GET {path}" in m
        for m in debug_messages
    )
    assert any(probe_log in m and "200" in m for m in debug_messages)


@pytest.mark.parametrize(
    (
        "plans",
        "subscribers",
        "expected_status",
        "expected_org_id",
        "expected_missing_scope",
    ),
    [
        (
            [MagicMock(org_id="org-plan", id="p1")],
            [MagicMock(org_id="org-sub", id="s1")],
            ProbeStatus.AVAILABLE,
            "org-plan",
            False,
        ),
        (
            [],
            [MagicMock(org_id="org-sub", id="s1")],
            ProbeStatus.AVAILABLE,
            "org-sub",
            False,
        ),
        (
            [MagicMock(org_id=None, id="p1")],
            [MagicMock(org_id=None, id="s1")],
            ProbeStatus.AVAILABLE,
            None,
            False,
        ),
        (
            [],
            [],
            ProbeStatus.EMPTY,
            None,
            False,
        ),
        (
            UniFiAuthenticationError("Unauthorized", status_code=401),
            [],
            ProbeStatus.AUTH_FAILED,
            None,
            False,
        ),
        (
            UniFiAuthenticationError(
                "Forbidden", status_code=403, api_error_code="insufficient_scope"
            ),
            [],
            ProbeStatus.AUTH_FAILED,
            None,
            True,
        ),
        (
            [MagicMock(org_id="org-plan", id="p1")],
            UniFiAuthenticationError(
                "Forbidden", status_code=403, api_error_code="insufficient_scope"
            ),
            ProbeStatus.AUTH_FAILED,
            "org-plan",
            True,
        ),
        (
            [MagicMock(org_id="org-plan", id="p1")],
            UniFiAuthenticationError("Unauthorized", status_code=401),
            ProbeStatus.AUTH_FAILED,
            "org-plan",
            False,
        ),
        (
            UniFiRateLimitError("Rate limited", status_code=429),
            [],
            ProbeStatus.UNREACHABLE,
            None,
            False,
        ),
        (
            UniFiTimeoutError("Timed out"),
            [],
            ProbeStatus.UNREACHABLE,
            None,
            False,
        ),
        (
            UniFiResponseError("Server error", status_code=500),
            [],
            ProbeStatus.UNREACHABLE,
            None,
            False,
        ),
        (
            ValueError("Unexpected fault"),
            [],
            ProbeStatus.ERROR,
            None,
            False,
        ),
    ],
    ids=[
        "plans-with-org-id",
        "fallback-to-subscriber-org-id",
        "no-org-id-available",
        "both-empty",
        "plans-401-auth-failed",
        "plans-403-missing-scope",
        "subscribers-403-missing-scope",
        "subscribers-401-auth-failed",
        "rate-limit-unreachable",
        "timeout-unreachable",
        "500-unreachable",
        "unexpected-error",
    ],
)
async def test_probe_carrier_fabric_statuses(
    plans: object,
    subscribers: object,
    expected_status: ProbeStatus,
    expected_org_id: str | None,
    *,
    expected_missing_scope: bool,
) -> None:
    """Carrier Fabric probe classifies available, empty, auth, and network errors."""
    client = MagicMock()
    if isinstance(plans, Exception):
        client.service_plans.get_all = AsyncMock(side_effect=plans)
    else:
        client.service_plans.get_all = AsyncMock(return_value=plans)

    if isinstance(subscribers, Exception):
        client.subscribers.get_all = AsyncMock(side_effect=subscribers)
    else:
        client.subscribers.get_all = AsyncMock(return_value=subscribers)

    result = await async_probe_carrier_fabric(client)

    assert result.status is expected_status
    assert result.org_id == expected_org_id
    assert result.missing_scope is expected_missing_scope
