# Copyright 2026 UniFi Insights contributors
"""Tests for classifying Network/Protect probe results."""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
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
from unifi_official_api.access import UniFiAccessClient
from unifi_official_api.innerspace import UniFiInnerSpaceClient
from unifi_official_api.network import UniFiNetworkClient
from unifi_official_api.protect import UniFiProtectClient

from custom_components.unifi_insights.probe import (
    ProbeResult,
    ProbeStatus,
    _failed_probe,
    async_probe_access,
    async_probe_carrier_fabric,
    async_probe_innerspace,
    async_probe_network,
    async_probe_protect,
    async_probe_with_client,
    classify_error,
    describe_error,
    is_forbidden,
    is_malformed_response,
    is_transient_error,
)
from tests.fixtures.http_fakes import FakeResponse, fake_session

# What unifi-official-api 3.0.0 raises for a successful answer that holds no
# record, and for one whose record fails validation.
_NO_RECORD = UniFiNotFoundError("NVR not found", status_code=200)
_MALFORMED = UniFiResponseError(
    "NVR response failed validation (uptime)", status_code=200
)
_NON_JSON_PAGE = UniFiResponseError(
    "API returned non-JSON response (status 200)",
    status_code=200,
    response_body="<html>UniFi OS</html>",
)


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
        ([], _NO_RECORD, "empty"),
        ([], _MALFORMED, "error"),
        ([], _NON_JSON_PAGE, "unsupported"),
        ([], UniFiNotFoundError("Not found", status_code=404), "unsupported"),
        ([], UniFiResponseError("Bad request", status_code=400), "unsupported"),
        ([], UniFiResponseError("Server error", status_code=503), "unreachable"),
        ([], UniFiAuthenticationError("Unauthorized", status_code=401), "auth_failed"),
        ([], RuntimeError("Unexpected"), "error"),
        (UniFiTimeoutError("Timed out"), None, "unreachable"),
        (UniFiNotFoundError("Not found", status_code=404), None, "unsupported"),
    ],
    ids=[
        "cameras",
        "camera-free-nvr",
        "no-nvr-record",
        "nvr-200-no-record",
        "nvr-200-malformed",
        "nvr-200-non-json",
        "nvr-404",
        "nvr-400",
        "nvr-5xx",
        "nvr-401",
        "nvr-unexpected",
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
    if isinstance(nvr, Exception):
        client.nvr.get = AsyncMock(side_effect=nvr)
    else:
        client.nvr.get = AsyncMock(return_value=nvr)

    result = await async_probe_protect(client)

    assert result.status is ProbeStatus(expected)
    raised = cameras if isinstance(cameras, Exception) else nvr
    assert result.error is (raised if isinstance(raised, Exception) else None)
    client.cameras.get_all.assert_awaited_once_with(expected_unsupported=True)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_MALFORMED, True),
        (_NO_RECORD, False),
        (_NON_JSON_PAGE, False),
        (UniFiResponseError("Bad request", status_code=400), False),
        (UniFiNotFoundError("Not found", status_code=404), False),
        (ValueError("Unexpected"), False),
    ],
    ids=["malformed", "no-record", "non-json", "400", "404", "other"],
)
def test_is_malformed_response(error: Exception, *, expected: bool) -> None:
    """Only a 200 JSON answer whose record failed to parse is malformed."""
    assert is_malformed_response(error) is expected


def _protect_client(*responses: FakeResponse) -> UniFiProtectClient:
    return UniFiProtectClient(
        auth=ApiKeyAuth(api_key="test-key"),
        base_url="https://192.168.1.1",
        connection_type=ConnectionType.LOCAL,
        session=fake_session(*responses),
    )


@pytest.mark.parametrize(
    ("nvr_body", "expected", "error_type"),
    [
        ({"data": []}, "empty", UniFiNotFoundError),
        ({}, "empty", UniFiNotFoundError),
        ({"id": "nvr-1", "uptime": "not-a-number"}, "error", UniFiResponseError),
        ({"id": "nvr-1", "name": "NVR"}, "available", None),
    ],
    ids=["no-record", "empty-object", "malformed", "nvr"],
)
async def test_probe_protect_nvr_through_the_library(
    nvr_body: dict[str, object], expected: str, error_type: type[Exception] | None
) -> None:
    """The real NVR endpoint's 3.0.0 errors reach the probe's mapping."""
    client = _protect_client(
        FakeResponse(200, "[]"), FakeResponse(200, json.dumps(nvr_body))
    )

    result = await async_probe_protect(client)

    assert result.status is ProbeStatus(expected)
    if error_type is None:
        assert result.error is None
    else:
        assert type(result.error) is error_type
        assert getattr(result.error, "status_code", None) == 200


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
        (
            UniFiResponseError(
                "/v1/project response failed validation (id)", status_code=200
            ),
            [],
            [],
            "error",
        ),
        (UniFiAuthenticationError("Forbidden", status_code=403), [], [], "auth_failed"),
        (_NON_JSON_PAGE, [], [], "unsupported"),
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
        "200-malformed-error",
        "403-auth-failed",
        "200-non-json-unsupported",
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


# --- Access probing ----------------------------------------------------------

# Tests for the Access probe: every status the config flow maps.


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


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (UniFiAuthenticationError("private", status_code=401), "authentication"),
        (UniFiAuthenticationError("private", status_code=403), "forbidden"),
        (UniFiRateLimitError("private", status_code=429), "rate_limited"),
        (UniFiTimeoutError("private"), "timeout"),
        (UniFiConnectionError("private"), "connection"),
        (UniFiNotFoundError("private", status_code=404), "not_found"),
        (UniFiResponseError("private", status_code=200), "malformed_response"),
        (_NON_JSON_PAGE, "non_json_response"),
        (UniFiResponseError("private", status_code=502), "http_error"),
        (RuntimeError("private"), "unexpected"),
    ],
)
def test_sanitised_error_detail(error: Exception, category: str) -> None:
    """All categories expose class/status evidence without exception messages."""
    detail = describe_error(error)
    assert detail.error_category == category
    assert detail.http_status == getattr(error, "status_code", None)
    assert detail.error_type == (
        "other" if isinstance(error, RuntimeError) else type(error).__name__
    )
    result = _failed_probe(classify_error(error), error, "project")
    assert result.endpoint == "project"
    assert result.http_status == detail.http_status
    assert result.error_type == detail.error_type
    assert result.error_category == category
    assert result.error is error


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        (ProbeStatus.AUTH_FAILED, UniFiAuthenticationError("x", status_code=403), True),
        (
            ProbeStatus.AUTH_FAILED,
            UniFiAuthenticationError("x", status_code=401),
            False,
        ),
        (ProbeStatus.AUTH_FAILED, UniFiResponseError("x", status_code=403), False),
        (ProbeStatus.ERROR, UniFiAuthenticationError("x", status_code=403), False),
        (ProbeStatus.AVAILABLE, None, False),
    ],
)
def test_is_forbidden(
    status: ProbeStatus, error: Exception | None, *, expected: bool
) -> None:
    """Only an auth failure backed by a library auth 403 proves forbidden."""
    assert is_forbidden(ProbeResult(status, error)) is expected


@pytest.mark.parametrize("remote", [False, True])
async def test_innerspace_real_client_forbidden_routes(
    caplog: pytest.LogCaptureFixture, *, remote: bool
) -> None:
    """
    Record actual connector/proxy paths, headers and current 403 contract.

    The log assertions are a contract guard: library 3.1.0 already drops the
    403 body, so they would pass before #470 too.
    """
    client = UniFiInnerSpaceClient(
        auth=ApiKeyAuth(api_key="synthetic-key"),
        **(
            {
                "console_id": "synthetic-console",
                "connection_type": ConnectionType.REMOTE,
            }
            if remote
            else {
                "base_url": "https://192.168.1.1",
                "connection_type": ConnectionType.LOCAL,
            }
        ),
    )
    path = (
        "/v1/connector/consoles/synthetic-console/innerspace/integration/v1/project"
        if remote
        else "/proxy/innerspace/integration/v1/project"
    )
    body = '{"type":"access","message":"Headers missing. Access denied"}'
    response = MagicMock()
    response.status = 403
    response.text = AsyncMock(return_value=body)
    response.json = AsyncMock(return_value=json.loads(body))
    response.headers = {}
    response.method = "GET"
    response.history = ()
    response.url = MagicMock()
    response.url.path = path
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.request.return_value = ctx
    client._ensure_session = AsyncMock(return_value=session)
    client._throttle = AsyncMock()
    # The dependency logs response bodies at DEBUG; this contract covers the
    # integration's probe logs and the normal library logging level.
    caplog.set_level(logging.INFO, logger="unifi_official_api")
    with caplog.at_level(
        logging.DEBUG, logger="custom_components.unifi_insights.probe"
    ):
        with pytest.raises(UniFiAuthenticationError) as raised:
            await client.get_project(expected_unsupported=True)
        result = await async_probe_innerspace(client)
    assert raised.value.status_code == 403
    assert result.status is ProbeStatus.AUTH_FAILED
    assert result.http_status == 403
    assert result.error_category == "forbidden"
    assert result.endpoint == "project"
    args, kwargs = session.request.call_args
    assert str(args[1] if len(args) > 1 else kwargs["url"]).endswith(path)
    assert kwargs["headers"]["X-API-Key"] == "synthetic-key"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "Headers missing" not in caplog.text
    assert body not in caplog.text
