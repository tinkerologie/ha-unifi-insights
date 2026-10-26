# Copyright 2026 UniFi Insights contributors
"""Tests for the export_network_rules action and its builder."""

from __future__ import annotations

import copy
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.components.diagnostics.const import REDACTED
from homeassistant.core import Context, SupportsResponse
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
)
from unifi_official_api import (
    ApiKeyAuth,
    ConnectionType,
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiNotFoundError,
)
from unifi_official_api.network.client import UniFiNetworkClient
from unifi_official_api.network.models import (
    FirewallRule,
    NatRule,
    PolicyBasedRoute,
    PortForward,
    StaticRoute,
    TrafficRule,
)

from custom_components.unifi_insights.const import (
    DOMAIN,
    SERVICE_EXPORT_NETWORK_RULES,
)
from custom_components.unifi_insights.coordinators.base import (
    UnifiInsightsBaseCoordinator,
)
from custom_components.unifi_insights.coordinators.rule_audit import (
    RULE_TYPES,
    _export_record,
    async_build_rule_export,
)
from custom_components.unifi_insights.diagnostics import (
    RULE_EXPORT_SAFE_KEYS,
    _safe_export_value,
    redact_network_rule_export,
)
from custom_components.unifi_insights.services import (
    async_setup_services,
    async_unload_services,
)
from tests.fixtures.http_fakes import FakeJsonResponse
from tests.fixtures.network_rule_responses import (
    nat_rule_record,
    port_forward_record,
    static_route_record,
    traffic_rule_record,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

_SITES: dict[str, Any] = {
    "site1": {"id": "site1", "name": "Home", "internalReference": "default"},
    "site2": {"id": "site2", "name": "Lab"},
}
_CONSOLE: dict[str, Any] = {"entry_id": "e1", "title": "UDM"}

# rule type -> (client attribute, list method)
_LISTERS: dict[str, tuple[str, str]] = {
    "firewall_rule": ("firewall", "list_rules"),
    "policy_based_route": ("routes", "list_routes"),
    "port_forward": ("port_forwards", "list_port_forwards"),
    "traffic_rule": ("traffic_rules", "list_traffic_rules"),
    "nat_rule": ("nat_rules", "list_nat_rules"),
    "static_route": ("static_routes", "list_static_routes"),
}


def _client(**results: Any) -> MagicMock:
    """Return a client whose six list methods return or raise per rule type."""
    client = MagicMock()
    for rule_type, (attribute, method) in _LISTERS.items():
        result = results.get(rule_type, [])
        if isinstance(result, Exception):
            lister = AsyncMock(side_effect=result)
        else:
            lister = AsyncMock(return_value=result)
        setattr(getattr(client, attribute), method, lister)
    return client


def _lister(client: MagicMock, rule_type: str) -> AsyncMock:
    attribute, method = _LISTERS[rule_type]
    lister: AsyncMock = getattr(getattr(client, attribute), method)
    return lister


async def _build(
    client: UniFiNetworkClient | MagicMock, **kwargs: Any
) -> dict[str, Any]:
    options: dict[str, Any] = {"site_ids": None, "rule_types": None}
    options.update(kwargs)
    return await async_build_rule_export(
        client,
        _SITES,
        console=_CONSOLE,
        is_unsupported=UnifiInsightsBaseCoordinator._is_unsupported_response,
        **options,
    )


def _entry(title: str, coordinator: MagicMock, sites: dict[str, Any]) -> MagicMock:
    coordinator.data = {"sites": sites}
    return MagicMock(
        entry_id=f"id-{title}",
        title=title,
        runtime_data=MagicMock(coordinator=coordinator),
    )


def _doc() -> dict[str, Any]:
    """Return a one-site, one-port-forward raw document."""
    return {
        "format_version": 1,
        "generated_at": "2026-01-02T03:04:05+00:00",
        "redacted": False,
        "console": _CONSOLE,
        "sites": {
            "site1": {
                "name": "Home",
                "rule_types": {
                    "port_forward": {
                        "status": "ok",
                        "count": 1,
                        "rules": [port_forward_record(fwd="192.0.2.50")],
                    }
                },
            }
        },
    }


async def _call(
    hass: HomeAssistant, entries: list[MagicMock], data: dict[str, Any], **kw: Any
) -> Any:
    await async_setup_services(hass)
    try:
        with patch.object(hass.config_entries, "async_entries", return_value=entries):
            return await hass.services.async_call(
                DOMAIN,
                SERVICE_EXPORT_NETWORK_RULES,
                data,
                blocking=True,
                return_response=True,
                **kw,
            )
    finally:
        await async_unload_services(hass)


# One record per family, each with sensitive values in nested positions. Every
# address is from an RFC 5737 documentation range, every name ends in .example.
_SENSITIVE: dict[str, tuple[str, ...]] = {
    "firewall_rule": (
        "198.51.100.7",
        "203.0.113.10",
        "aa:bb:cc:dd:ee:10",
        "fw-blocked.example",
        "::2a:3b:4c:5d",
    ),
    "policy_based_route": (
        "pbr-routed.example",
        "198.51.100.20",
        "198.51.100.30",
        "203.0.113.1",
        "203.0.113.21",
        "aa:bb:cc:dd:ee:20",
    ),
    "port_forward": ("192.0.2.50", "198.51.100.0/24", "203.0.113.7", "203.0.113.8"),
    "traffic_rule": (
        "tr-blocked.example",
        "198.51.100.101",
        "198.51.100.110",
        "203.0.113.9",
        "aa:bb:cc:dd:ee:30",
    ),
    "nat_rule": ("192.0.2.53", "192.0.2.54", "198.51.100.44"),
    "static_route": (
        "198.51.100.128/25",
        "192.0.2.1",
        "198.51.100.77",
        "aa:bb:cc:dd:ee:40",
    ),
}
# Rule names and descriptions: free text that often holds an address or a host
# name, so the redacted export must not carry them.
_NAMES = ("Block cams", "VPN out", "Plex", "Bedtime", "All DNS to resolver")


def _six_models() -> dict[str, Any]:
    """Return one parsed rule of every family, as the endpoints return them."""
    return {
        "firewall_rule": FirewallRule.model_validate(
            {
                "id": "fw1",
                "name": "Block cams",
                "sourceAddress": "203.0.113.10",
                "source": {
                    "trafficFilter": {
                        "type": "IP_ADDRESS",
                        "ipAddressFilter": {
                            "type": "IP_ADDRESSES",
                            "items": [{"type": "IP_ADDRESS", "value": "198.51.100.7"}],
                        },
                        "macAddressFilter": {"macAddresses": ["aa:bb:cc:dd:ee:10"]},
                    }
                },
                "destination": {
                    "trafficFilter": {
                        "domainFilter": {"domains": ["fw-blocked.example"]},
                        "ipv6IidFilter": {"items": [{"value": "::2a:3b:4c:5d"}]},
                        "portFilter": {"items": [{"value": 443}]},
                    }
                },
            }
        ),
        "policy_based_route": PolicyBasedRoute.model_validate(
            {
                "_id": "pbr1",
                "description": "VPN out",
                "domains": [{"domain": "pbr-routed.example"}],
                "ipRanges": [{"ip_start": "198.51.100.20", "ip_stop": "198.51.100.30"}],
                "nextHop": "203.0.113.1",
                "ips": ["203.0.113.21"],
                "targetDevices": [{"type": "CLIENT", "clientMac": "aa:bb:cc:dd:ee:20"}],
            }
        ),
        "port_forward": PortForward.model_validate(
            port_forward_record(
                fwd="192.0.2.50",
                destination_ips=["203.0.113.8"],
            )
        ),
        "traffic_rule": TrafficRule.model_validate(
            traffic_rule_record(
                domains=["tr-blocked.example"],
                ip_addresses=[
                    {
                        "ip_or_subnet": "203.0.113.9/32",
                        "ip_version": "v4",
                        "port_ranges": [],
                        "ports": [],
                    }
                ],
                ip_ranges=[
                    {
                        "ip_start": "198.51.100.101",
                        "ip_stop": "198.51.100.110",
                        "ip_version": "v4",
                    }
                ],
                target_devices=[{"client_mac": "aa:bb:cc:dd:ee:30", "type": "CLIENT"}],
            )
        ),
        "nat_rule": NatRule.model_validate(
            nat_rule_record(
                source_filter={
                    "filter_type": "ADDRESS_AND_PORT",
                    "firewall_group_ids": [],
                    "invert_address": False,
                    "invert_port": False,
                    "address": "198.51.100.44",
                    "port": "1024",
                }
            )
        ),
        "static_route": StaticRoute.model_validate(
            static_route_record(
                **{
                    "static-route_network": "198.51.100.128/25",
                    "static-route_nexthop": "192.0.2.1",
                    # A nested copy, so the key-based redaction is proven to
                    # recurse into this family too.
                    "hops": [{"static-route_nexthop": "198.51.100.77"}],
                    "gateway_device": "aa:bb:cc:dd:ee:40",
                }
            )
        ),
    }


def _dump(model: Any) -> dict[str, Any]:
    """Return what the console sent: the fields set on the model, by alias."""
    dumped: dict[str, Any] = model.model_dump(
        mode="json", by_alias=True, exclude_unset=True
    )
    return dumped


def _six_family_document() -> dict[str, Any]:
    """Return a raw export document holding the six sensitive records."""
    return {
        "format_version": 1,
        "generated_at": "2026-01-02T03:04:05+00:00",
        "redacted": False,
        "console": _CONSOLE,
        "sites": {
            "site1": {
                "name": "Home",
                "rule_types": {
                    rule_type: {
                        "status": "ok",
                        "count": 1,
                        "rules": [_dump(model)],
                    }
                    for rule_type, model in _six_models().items()
                },
            }
        },
    }


def _assert_absent(dumped: str, literals: tuple[str, ...]) -> None:
    for literal in literals:
        assert literal not in dumped, f"{literal} leaked"


async def test_export_document_shape_and_fresh_fetch(freezer: Any) -> None:
    """The document is versioned, JSON-safe and read from the console now."""
    freezer.move_to("2026-01-02 03:04:05+00:00")
    client = _client(port_forward=[PortForward.model_validate(port_forward_record())])

    document = await _build(client)

    assert document["format_version"] == 1
    assert document["generated_at"] == "2026-01-02T03:04:05+00:00"
    assert document["redacted"] is False
    assert document["console"] == _CONSOLE
    assert list(document["sites"]) == ["site1", "site2"]
    site1 = document["sites"]["site1"]
    assert site1["name"] == "Home"
    assert tuple(site1["rule_types"]) == RULE_TYPES
    assert site1["rule_types"]["port_forward"] == {
        "status": "ok",
        "count": 1,
        "rules": [port_forward_record()],
    }
    _lister(client, "firewall_rule").assert_any_await("site1", strict=True)
    _lister(client, "port_forward").assert_awaited_once_with("default", strict=True)
    assert json.dumps(document)


async def test_export_record_passes_plain_dicts_through() -> None:
    """A lister that returns plain dicts is exported unchanged."""
    record = {"_id": "x", "name": "Plain"}
    document = await _build(_client(port_forward=[record]))

    assert document["sites"]["site1"]["rule_types"]["port_forward"]["rules"] == [record]


async def test_site_without_classic_name_is_unsupported_for_classic_families() -> None:
    """Without an internalReference only the official firewall API can be read."""
    client = _client()

    document = await _build(client, site_ids=["site2"])

    types = document["sites"]["site2"]["rule_types"]
    assert types["firewall_rule"]["status"] == "ok"
    for rule_type in RULE_TYPES[1:]:
        assert types[rule_type] == {"status": "unsupported", "count": 0, "rules": []}
        _lister(client, rule_type).assert_not_awaited()
    _lister(client, "firewall_rule").assert_awaited_once_with("site2", strict=True)


async def test_family_status_unsupported_and_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A family that cannot be read says so; the others are still exported."""
    caplog.set_level(logging.DEBUG)
    client = _client(
        nat_rule=UniFiNotFoundError("nope", status_code=404),
        static_route=UniFiConnectionError("secret-host 192.0.2.99"),
        traffic_rule=UniFiAuthenticationError("bad key 192.0.2.98", status_code=401),
        port_forward=[PortForward.model_validate(port_forward_record())],
    )

    document = await _build(client, site_ids=["site1"])

    types = document["sites"]["site1"]["rule_types"]
    assert types["nat_rule"] == {"status": "unsupported", "count": 0, "rules": []}
    assert types["static_route"] == {"status": "failed", "count": 0, "rules": []}
    assert types["traffic_rule"] == {"status": "failed", "count": 0, "rules": []}
    assert types["port_forward"]["status"] == "ok"
    assert types["port_forward"]["count"] == 1
    dumped = json.dumps(document)
    for text in (dumped, caplog.text):
        assert "192.0.2.99" not in text
        assert "192.0.2.98" not in text
        assert "secret-host" not in text
    assert "UniFiConnectionError" in caplog.text
    assert "UniFiAuthenticationError" in caplog.text


async def test_rule_types_filter() -> None:
    """Only the requested families are fetched and reported."""
    client = _client()

    document = await _build(client, rule_types=["nat_rule"], site_ids=["site1"])

    assert list(document["sites"]["site1"]["rule_types"]) == ["nat_rule"]
    _lister(client, "nat_rule").assert_awaited_once_with("default", strict=True)
    for rule_type in RULE_TYPES:
        if rule_type != "nat_rule":
            _lister(client, rule_type).assert_not_awaited()


async def test_every_fetch_failed_raises() -> None:
    """When nothing could be read at all, the export is an error."""
    client = _client(
        **{rule_type: UniFiConnectionError("x") for rule_type in RULE_TYPES}
    )

    with pytest.raises(HomeAssistantError) as err:
        await _build(client, site_ids=["site1"])

    assert err.value.translation_key == "export_network_rules_failed"


async def test_one_failed_family_does_not_fail_the_export() -> None:
    """A single failing family is a status, not an error."""
    client = _client(nat_rule=UniFiConnectionError("x"))

    document = await _build(client, site_ids=["site1"])

    types = document["sites"]["site1"]["rule_types"]
    assert types["nat_rule"]["status"] == "failed"
    assert types["firewall_rule"]["status"] == "ok"


async def test_malformed_site_exports_without_a_name() -> None:
    """A site value that is not a mapping has no name and no classic site name."""
    client = _client()

    document = await async_build_rule_export(
        client,
        {"site1": "not-a-mapping"},
        console=_CONSOLE,
        site_ids=None,
        rule_types=None,
        is_unsupported=UnifiInsightsBaseCoordinator._is_unsupported_response,
    )

    site = document["sites"]["site1"]
    assert site["name"] is None
    assert site["rule_types"]["firewall_rule"]["status"] == "ok"
    for rule_type in RULE_TYPES:
        if rule_type != "firewall_rule":
            assert site["rule_types"][rule_type]["status"] == "unsupported"
            _lister(client, rule_type).assert_not_awaited()


async def test_no_matching_site_returns_empty_sites() -> None:
    """An unknown site id selects nothing and fetches nothing."""
    client = _client()

    document = await _build(client, site_ids=["nope"])

    assert document["sites"] == {}
    for rule_type in RULE_TYPES:
        _lister(client, rule_type).assert_not_awaited()


# --- real transport and real endpoints ---------------------------------------

# rule type -> the end of the request path of its list call
_PATH_ENDS: dict[str, str] = {
    "firewall_rule": "/firewall/policies",
    "policy_based_route": "/trafficroutes",
    "port_forward": "/rest/portforward",
    "traffic_rule": "/trafficrules",
    "nat_rule": "/nat",
    "static_route": "/rest/routing",
}
# What each endpoint answers when it has no rules.
_EMPTY_ANSWER: dict[str, Any] = {
    "firewall_rule": {
        "offset": 0,
        "limit": 200,
        "count": 0,
        "totalCount": 0,
        "data": [],
    },
    "policy_based_route": [],
    "port_forward": {"meta": {"rc": "ok"}, "data": []},
    "traffic_rule": [],
    "nat_rule": [],
    "static_route": {"meta": {"rc": "ok"}, "data": []},
}
_SENTINEL = "192.0.2.123"
# A valid record per family, with sentinel values a log must never show. The
# marker key comes first so it is inside any 500 character log excerpt.
_GOOD_RECORDS: dict[str, dict[str, Any]] = {
    "firewall_rule": {"x_marker": "secret-198.51.100.1", "id": "fw1", "name": "SSH"},
    "policy_based_route": {"x_marker": "secret-198.51.100.2", "_id": "pbr1"},
    "port_forward": port_forward_record(
        x_marker="secret-198.51.100.3", fwd="192.0.2.88"
    ),
    "traffic_rule": traffic_rule_record(x_marker="secret-198.51.100.4"),
    "nat_rule": nat_rule_record(x_marker="secret-198.51.100.5"),
    "static_route": static_route_record(x_marker="secret-198.51.100.6"),
}
# A record per family that fails its model and quotes the sentinel as input.
_INVALID_RECORDS: dict[str, dict[str, Any]] = {
    "firewall_rule": {"name": _SENTINEL},
    "policy_based_route": {"_id": "pbr-bad", "network_ids": [{"id": _SENTINEL}]},
    "port_forward": {"_id": 5, "name": _SENTINEL},
    "traffic_rule": {"description": _SENTINEL},
    "nat_rule": {"description": _SENTINEL},
    "static_route": {"name": _SENTINEL},
}


_CLASSIC_TYPES = [
    "policy_based_route",
    "port_forward",
    "traffic_rule",
    "nat_rule",
    "static_route",
]


class _RoutedSession:
    """Answer each rule list request with the body registered for its family."""

    closed = False

    def __init__(self, bodies: dict[str, Any]) -> None:
        self._bodies = {**_EMPTY_ANSWER, **bodies}
        self.requests: list[str] = []

    def request(self, method: str, url: object, **kwargs: Any) -> FakeJsonResponse:
        path = str(url)
        self.requests.append(path)
        for rule_type, end in _PATH_ENDS.items():
            if path.endswith(end):
                body = self._bodies[rule_type]
                return (
                    body
                    if isinstance(body, FakeJsonResponse)
                    else FakeJsonResponse(body)
                )
        msg = f"unexpected request {method} {path}"
        raise AssertionError(msg)

    async def close(self) -> None:
        self.closed = True


def _real_client(session: _RoutedSession) -> UniFiNetworkClient:
    return UniFiNetworkClient(
        auth=ApiKeyAuth(api_key="test-key"),
        base_url="https://192.168.1.1",
        connection_type=ConnectionType.LOCAL,
        session=session,  # type: ignore[arg-type]
    )


async def _export_real(bodies: dict[str, Any]) -> dict[str, Any]:
    """Export site1 through the real client and the real six endpoints."""
    return await _build(
        _real_client(_RoutedSession(bodies)), site_ids=["site1"], rule_types=None
    )


def _statuses(document: dict[str, Any]) -> dict[str, str]:
    types = document["sites"]["site1"]["rule_types"]
    return {rule_type: types[rule_type]["status"] for rule_type in RULE_TYPES}


@pytest.mark.parametrize("rule_type", RULE_TYPES)
@pytest.mark.parametrize(
    "body",
    [None, {}, {"data": None}, {"data": "unexpected"}, "text", [1, 2]],
)
async def test_export_rejects_malformed_list_response(
    rule_type: str, body: Any
) -> None:
    """A broken answer is a failed family, never an empty successful one."""
    document = await _export_real({rule_type: body})

    types = document["sites"]["site1"]["rule_types"]
    assert types[rule_type] == {"status": "failed", "count": 0, "rules": []}
    for other in RULE_TYPES:
        if other != rule_type:
            assert types[other] == {"status": "ok", "count": 0, "rules": []}


@pytest.mark.parametrize("rule_type", RULE_TYPES)
async def test_export_invalid_record_marks_family_failed(rule_type: str) -> None:
    """One bad record fails the family; it is never exported as 1 of 2."""
    records = [_GOOD_RECORDS[rule_type], _INVALID_RECORDS[rule_type]]

    document = await _export_real({rule_type: records})

    types = document["sites"]["site1"]["rule_types"]
    assert types[rule_type] == {"status": "failed", "count": 0, "rules": []}
    assert [t for t in RULE_TYPES if types[t]["status"] != "ok"] == [rule_type]


@pytest.mark.parametrize("rule_type", RULE_TYPES)
@pytest.mark.parametrize(
    "body", [[], {"data": []}, {"meta": {"rc": "ok"}, "data": []}, None]
)
async def test_export_valid_empty_list_is_ok(rule_type: str, body: Any) -> None:
    """A real empty answer is an ok family with no rules."""
    if body is None:
        body = _EMPTY_ANSWER[rule_type]

    document = await _export_real({rule_type: body})

    assert _statuses(document) == dict.fromkeys(RULE_TYPES, "ok")
    for exported in document["sites"]["site1"]["rule_types"].values():
        assert (exported["count"], exported["rules"]) == (0, [])


async def test_export_valid_answers_are_exported_whole() -> None:
    """Valid answers of all six real endpoints come back as ok, with their rules."""
    document = await _export_real(
        {rule_type: [record] for rule_type, record in _GOOD_RECORDS.items()}
    )

    assert _statuses(document) == dict.fromkeys(RULE_TYPES, "ok")
    types = document["sites"]["site1"]["rule_types"]
    for rule_type in RULE_TYPES:
        assert types[rule_type]["count"] == 1
        assert types[rule_type]["rules"][0]["x_marker"].startswith("secret-")


@pytest.mark.parametrize(
    "rule_type",
    ["policy_based_route", "port_forward", "traffic_rule", "nat_rule", "static_route"],
)
async def test_export_error_envelope_is_failed(
    caplog: pytest.LogCaptureFixture, rule_type: str
) -> None:
    """A 2xx error envelope is a failed read, never "ok" and never "unsupported"."""
    caplog.set_level(logging.DEBUG)
    body = {"meta": {"rc": "error", "msg": f"secret {_SENTINEL}"}, "data": []}

    document = await _export_real({rule_type: body})

    types = document["sites"]["site1"]["rule_types"]
    assert types[rule_type] == {"status": "failed", "count": 0, "rules": []}
    assert _SENTINEL not in caplog.text


@pytest.mark.parametrize("rule_type", _CLASSIC_TYPES)
@pytest.mark.parametrize(
    "meta", [{"rc": "ERROR"}, {"rc": "fail"}, {}, "error", None], ids=repr
)
async def test_export_envelope_without_rc_ok_is_failed(
    rule_type: str, meta: Any
) -> None:
    """A classic envelope that does not say rc ok fails the family."""
    document = await _export_real({rule_type: {"meta": meta, "data": []}})

    types = document["sites"]["site1"]["rule_types"]
    assert types[rule_type] == {"status": "failed", "count": 0, "rules": []}
    for other in RULE_TYPES:
        if other != rule_type:
            assert types[other]["status"] == "ok"


@pytest.mark.parametrize("rule_type", RULE_TYPES)
@pytest.mark.parametrize(
    "body",
    [
        pytest.param("<html>Sign in</html>", id="html"),
        pytest.param('{"data": [{"_id": "a"', id="truncated-json"),
    ],
)
async def test_export_non_json_answer_is_failed(
    caplog: pytest.LogCaptureFixture, rule_type: str, body: str
) -> None:
    """A 2xx answer that is not JSON is a failed read, not an absent list."""
    caplog.set_level(logging.DEBUG)

    document = await _export_real({rule_type: FakeJsonResponse(body, is_json=False)})

    types = document["sites"]["site1"]["rule_types"]
    assert types[rule_type] == {"status": "failed", "count": 0, "rules": []}
    for other in RULE_TYPES:
        if other != rule_type:
            assert types[other]["status"] == "ok"
    assert "Sign in" not in caplog.text
    assert '"_id"' not in caplog.text


async def test_export_all_non_json_raises_export_failed() -> None:
    """When every list answers a login page, the action fails."""
    bodies = {
        rule_type: FakeJsonResponse("<html>Sign in</html>", is_json=False)
        for rule_type in RULE_TYPES
    }

    with pytest.raises(HomeAssistantError) as err:
        await _export_real(bodies)

    assert err.value.translation_key == "export_network_rules_failed"


@pytest.mark.parametrize("rule_type", RULE_TYPES)
@pytest.mark.parametrize("status", [403, 404, 400])
async def test_export_refused_request_is_unsupported(
    rule_type: str, status: int
) -> None:
    """A console that refuses a list (403, 404, other 4xx) does not offer it."""
    document = await _export_real({rule_type: FakeJsonResponse(None, status=status)})

    types = document["sites"]["site1"]["rule_types"]
    assert types[rule_type] == {"status": "unsupported", "count": 0, "rules": []}
    for other in RULE_TYPES:
        if other != rule_type:
            assert types[other]["status"] == "ok"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            {"data": [{"id": "fw1", "name": "A"}], "totalCount": 5}, id="below-total"
        ),
        pytest.param(
            {"data": [{"id": f"fw{n}", "name": "A"} for n in range(200)]},
            id="full-page-without-total",
        ),
    ],
)
async def test_export_incomplete_firewall_list_is_failed(body: dict[str, Any]) -> None:
    """A firewall list the console's own total contradicts is a failed family."""
    document = await _export_real({"firewall_rule": body})

    types = document["sites"]["site1"]["rule_types"]
    assert types["firewall_rule"] == {"status": "failed", "count": 0, "rules": []}
    assert types["port_forward"]["status"] == "ok"


@pytest.mark.parametrize("redact", [True, False])
@pytest.mark.parametrize("invalid", [False, True])
async def test_export_transport_logs_no_sensitive_values(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    redact: bool,  # noqa: FBT001
    invalid: bool,  # noqa: FBT001
) -> None:
    """Real client, real endpoints, DEBUG: no rule value or input_value is logged."""
    caplog.set_level(logging.DEBUG)
    bodies = {
        rule_type: [
            record,
            *([_INVALID_RECORDS[rule_type]] if invalid else []),
        ]
        for rule_type, record in _GOOD_RECORDS.items()
    }
    session = _RoutedSession(bodies)
    client = _real_client(session)

    async def export(
        site_ids: list[str] | None, rule_types: list[str] | None
    ) -> dict[str, Any]:
        return await _build(client, site_ids=site_ids, rule_types=rule_types)

    coordinator = MagicMock()
    coordinator.async_export_network_rules = AsyncMock(side_effect=export)

    call = _call(
        hass,
        [_entry("UDM", coordinator, _SITES)],
        {"site_id": "site1", "redact": redact},
    )
    if invalid:
        # Every family fails, which is an error of the action.
        with pytest.raises(HomeAssistantError) as err:
            await call
        assert err.value.translation_key == "export_network_rules_failed"
    else:
        assert (await call)["redacted"] is redact

    # Every family was really requested, so the log below is not trivially clean.
    assert len(session.requests) == len(RULE_TYPES)
    assert "<body omitted" in caplog.text
    for text in (
        caplog.text,
        *(record.getMessage() for record in caplog.records),
        *(record.exc_text or "" for record in caplog.records),
    ):
        for sentinel in (
            _SENTINEL,
            "192.0.2.88",
            "secret-198.51.100",
            "input_value",
            "blocked.example",
            "Plex",
            "SSH",
        ):
            assert sentinel not in text, sentinel


def _two_rules(rule_type: str) -> list[Any]:
    """Return two parsed rules of one family, with distinct ids."""
    if rule_type == "firewall_rule":
        return [
            # "action" sorts before "id" in the canonical JSON, so the JSON order
            # of this pair (fw-b, fw-a) is the opposite of its id order.
            FirewallRule.model_validate(
                {"id": "fw-a", "name": "A", "action": "z", "index": 1}
            ),
            FirewallRule.model_validate(
                {"id": "fw-b", "name": "B", "action": "a", "index": 2}
            ),
        ]
    if rule_type == "policy_based_route":
        return [
            PolicyBasedRoute.model_validate({"_id": "pbr-a", "description": "A"}),
            PolicyBasedRoute.model_validate({"_id": "pbr-b", "description": "B"}),
        ]
    if rule_type == "port_forward":
        return [
            PortForward.model_validate(port_forward_record(_id="pf-a", name="A")),
            PortForward.model_validate(port_forward_record(_id="pf-b", name="B")),
            # Same id as the first: the record content breaks the tie.
            PortForward.model_validate(port_forward_record(_id="pf-a", name="A2")),
        ]
    if rule_type == "traffic_rule":
        return [
            TrafficRule.model_validate(traffic_rule_record(_id="tr-a")),
            TrafficRule.model_validate(traffic_rule_record(_id="tr-b")),
        ]
    if rule_type == "nat_rule":
        return [
            NatRule.model_validate(nat_rule_record(_id="nat-a", rule_index=1)),
            NatRule.model_validate(nat_rule_record(_id="nat-b", rule_index=2)),
        ]
    return [
        StaticRoute.model_validate(static_route_record(_id="sr-a")),
        StaticRoute.model_validate(static_route_record(_id="sr-b")),
    ]


_TWO_SITES: dict[str, Any] = {
    "site-a": {"id": "site-a", "name": "A", "internalReference": "default"},
    "site-b": {"id": "site-b", "name": "B", "internalReference": "lab"},
}


async def _export_two_sites(
    rules: dict[str, list[Any]], sites: dict[str, Any]
) -> dict[str, Any]:
    return await async_build_rule_export(
        _client(**rules),
        sites,
        console=_CONSOLE,
        site_ids=None,
        rule_types=None,
        is_unsupported=UnifiInsightsBaseCoordinator._is_unsupported_response,
    )


async def test_export_unchanged_rules_diff_clean(freezer: Any) -> None:
    """The same rules in another server order give the same document."""
    freezer.move_to("2026-01-02 03:04:05+00:00")
    forward = {rule_type: _two_rules(rule_type) for rule_type in RULE_TYPES}
    backward = {rule_type: rules[::-1] for rule_type, rules in forward.items()}

    first = await _export_two_sites(forward, _TWO_SITES)
    second = await _export_two_sites(backward, dict(reversed(_TWO_SITES.items())))

    assert first == second
    assert json.dumps(first) == json.dumps(second)
    assert list(second["sites"]) == ["site-a", "site-b"]
    rules = first["sites"]["site-a"]["rule_types"]["port_forward"]["rules"]
    assert [(rule["_id"], rule["name"]) for rule in rules] == [
        ("pf-a", "A"),
        ("pf-a", "A2"),
        ("pf-b", "B"),
    ]
    firewall = first["sites"]["site-a"]["rule_types"]["firewall_rule"]["rules"]
    assert [rule["id"] for rule in firewall] == ["fw-a", "fw-b"]
    # The order fields of a record are data, not a sort result.
    nat = first["sites"]["site-a"]["rule_types"]["nat_rule"]["rules"]
    assert [rule["rule_index"] for rule in nat] == [1, 2]


async def test_export_real_change_still_differs(freezer: Any) -> None:
    """A rule that changed on the console shows up as a different document."""
    freezer.move_to("2026-01-02 03:04:05+00:00")
    before = {rule_type: _two_rules(rule_type) for rule_type in RULE_TYPES}
    after = {rule_type: _two_rules(rule_type) for rule_type in RULE_TYPES}
    after["static_route"][0].enabled = False

    first = await _export_two_sites(before, _TWO_SITES)
    second = await _export_two_sites(after, _TWO_SITES)

    assert first != second
    changed = [
        (site_id, rule_type)
        for site_id in first["sites"]
        for rule_type in RULE_TYPES
        if first["sites"][site_id]["rule_types"][rule_type]
        != second["sites"][site_id]["rule_types"][rule_type]
    ]
    assert changed == [("site-a", "static_route"), ("site-b", "static_route")]


class _Color(Enum):
    RED = "red"


async def test_export_model_value_types_are_json_safe() -> None:
    """Non-primitive values of an extra-allow model come out as plain JSON."""
    model = PortForward.model_validate(
        port_forward_record(
            x_when=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            x_color=_Color.RED,
            x_amount=Decimal("1.50"),
            x_tags={"tag"},
            x_nested=StaticRoute.model_validate(static_route_record(_id="sr-n")),
        )
    )

    document = await _build(_client(port_forward=[model]), site_ids=["site1"])

    json.dumps(document)
    rule = document["sites"]["site1"]["rule_types"]["port_forward"]["rules"][0]
    assert rule["x_when"] == "2026-01-02T03:04:05Z"
    assert rule["x_color"] == "red"
    assert rule["x_amount"] == "1.50"
    assert rule["x_tags"] == ["tag"]
    assert isinstance(rule["x_nested"], dict)
    assert rule["x_nested"]["_id"] == "sr-n"


def test_redaction_removes_sensitive_values_of_every_family() -> None:
    """Every family is redacted, nested filters and rule names included."""
    document = _six_family_document()
    original = copy.deepcopy(document)
    raw_dump = json.dumps(document)
    # The literals are really in the raw document, so their absence below is
    # the redaction's doing.
    for literals in _SENSITIVE.values():
        for literal in literals:
            assert literal in raw_dump, f"{literal} missing from the raw fixture"
    for name in _NAMES:
        assert name in raw_dump

    redacted = redact_network_rule_export(document)

    dumped = json.dumps(redacted)
    for literals in _SENSITIVE.values():
        _assert_absent(dumped, literals)
    _assert_absent(dumped, _NAMES)
    assert '"port": "53"' in dumped
    assert '"_id": "pf-plex"' in dumped
    assert redacted["redacted"] is True
    assert document == original
    # The filter objects of a firewall policy go with the whole source and
    # destination, not value by value.
    firewall = redacted["sites"]["site-1"]["rule_types"]["firewall_rule"]["rules"][0]
    assert firewall["source"] == REDACTED
    assert firewall["destination"] == REDACTED
    assert firewall["name"] == REDACTED


def test_redaction_covers_every_rule_family() -> None:
    """The redaction fixture has a sensitive record for each exported family."""
    assert set(_SENSITIVE) == set(RULE_TYPES)
    assert set(_six_models()) == set(RULE_TYPES)


# Unknown future keys, one holding an address and one an object.
_FUTURE: dict[str, Any] = {
    "x_future_target": "198.51.100.77",
    "x_future_object": {"nested": "198.51.100.78", "deeper": {"host": "deep.example"}},
}
_FUTURE_LEAKS = ("198.51.100.77", "198.51.100.78", "deep.example")
# The structural facts of each family that the redacted export must keep.
_STRUCTURE: dict[str, dict[str, Any]] = {
    "firewall_rule": {
        "id": "fw-keep",
        "enabled": True,
        "index": 4,
        "action": {"type": "ALLOW", "allowReturnTraffic": False},
        "connectionStateFilter": ["NEW", "ESTABLISHED"],
        "loggingEnabled": True,
        "ipProtocolScope": {
            "ipVersion": "IPV4_AND_IPV6",
            "protocolFilter": {"type": "NAMED_PROTOCOL", "name": "TCP_UDP"},
        },
    },
    "policy_based_route": {
        "_id": "pbr-keep",
        "enabled": True,
        "killSwitchEnabled": True,
        "matchingTarget": "INTERNET",
    },
    "port_forward": {
        "_id": "pf-keep",
        "enabled": True,
        "proto": "tcp_udp",
        "dst_port": "8000-8100",
        "fwd_port": "80,443",
        "log": True,
        "src_limiting_enabled": False,
        "pfwd_interface": "wan",
    },
    "traffic_rule": {
        "_id": "tr-keep",
        "enabled": False,
        "action": "BLOCK",
        "matching_target": "INTERNET",
    },
    "nat_rule": {
        "_id": "nat-keep",
        "enabled": True,
        "type": "DNAT",
        "protocol": "tcp_udp",
        "ip_version": "IPV4",
        "rule_index": 3,
        "port": "53",
    },
    "static_route": {
        "_id": "sr-keep",
        "enabled": True,
        "type": "static-route",
        "static-route_type": "nexthop-route",
        "static-route_distance": 1,
        "gateway_type": "default",
    },
}


def _leaky_models() -> dict[str, tuple[Any, tuple[str, ...]]]:
    """
    Return, per family, a real model and the strings that must never be exported.

    Every record has free text with an address, each identifier or reference
    field of its family and the unknown future keys of ``_FUTURE``.
    """
    firewall = {
        **_STRUCTURE["firewall_rule"],
        "name": "SSH 192.0.2.51",
        "description": "desc 198.51.100.9",
        "source": {
            "zoneId": "zone-secret-src",
            "trafficFilter": {
                "type": "IP_ADDRESS",
                "ipAddressFilter": {
                    "type": "IP_ADDRESSES",
                    "items": [{"type": "IP_ADDRESS", "value": "2001:db8::51"}],
                },
            },
        },
        "destination": {
            "zoneId": "zone-secret-dst",
            "trafficFilter": {
                "type": "IP_ADDRESS",
                "ipAddressFilter": {
                    "type": "TRAFFIC_MATCHING_LIST",
                    "trafficMatchingListId": "tml-secret",
                },
            },
        },
        **_FUTURE,
    }
    route = {
        **_STRUCTURE["policy_based_route"],
        "description": "Route 203.0.113.0/24",
        "networkIds": ["pbr-net-secret"],
        "networkId": "pbr-net2-secret",
        "vpnClientId": "pbr-vpn-secret",
        "domains": [{"domain": "pbr.example"}],
        "nextHop": "203.0.113.1",
        "targetDevices": [
            {
                "type": "CLIENT",
                "clientMac": "aa:bb:cc:dd:ee:21",
                "networkId": "pbr-tnet-secret",
                "client_id": "pbr-client-secret",
            }
        ],
        **_FUTURE,
    }
    forward = port_forward_record(
        **_STRUCTURE["port_forward"],
        name="Forward 192.0.2.9",
        fwd="192.0.2.9",
        src="198.51.100.0/24",
        destination_ip="203.0.113.7",
        site_id="pf-site-secret",
        **_FUTURE,
    )
    traffic = traffic_rule_record(
        **_STRUCTURE["traffic_rule"],
        description="Block 192.0.2.60",
        domains=["tr.example"],
        network_ids=["tr-net-secret"],
        target_devices=[
            {
                "type": "CLIENT",
                "client_mac": "aa:bb:cc:dd:ee:31",
                "client_id": "tr-client-secret",
                "network_id": "tr-tnet-secret",
            }
        ],
        **_FUTURE,
    )
    nat = nat_rule_record(
        **_STRUCTURE["nat_rule"],
        description="NAT 198.51.100.7",
        in_interface="nat-in-secret",
        out_interface="nat-out-secret",
        ip_address="2001:db8::77",
        source_filter={
            "filter_type": "ADDRESS_AND_PORT",
            "network_conf_id": "nat-nc-secret",
            "firewall_group_ids": ["nat-grp-secret"],
            "invert_address": False,
            "invert_port": False,
            "address": "192.0.2.88",
        },
        **_FUTURE,
    )
    static = static_route_record(
        **{
            **_STRUCTURE["static_route"],
            "name": "Route 203.0.113.0/24",
            "static-route_network": "203.0.113.0/24",
            "static-route_nexthop": "192.0.2.1",
            "static-route_interface": "sr-net-secret",
            "gateway_device": "aa:bb:cc:dd:ee:41",
            "site_id": "sr-site-secret",
            **_FUTURE,
        }
    )
    return {
        "firewall_rule": (
            FirewallRule.model_validate(firewall),
            (
                "SSH 192.0.2.51",
                "192.0.2.51",
                "desc 198.51.100.9",
                "zone-secret-src",
                "zone-secret-dst",
                "tml-secret",
                "2001:db8::51",
                *_FUTURE_LEAKS,
            ),
        ),
        "policy_based_route": (
            PolicyBasedRoute.model_validate(route),
            (
                "Route 203.0.113.0/24",
                "pbr-net-secret",
                "pbr-net2-secret",
                "pbr-vpn-secret",
                "pbr.example",
                "203.0.113.1",
                "aa:bb:cc:dd:ee:21",
                "pbr-tnet-secret",
                "pbr-client-secret",
                *_FUTURE_LEAKS,
            ),
        ),
        "port_forward": (
            PortForward.model_validate(forward),
            (
                "Forward 192.0.2.9",
                "198.51.100.0/24",
                "203.0.113.7",
                "pf-site-secret",
                *_FUTURE_LEAKS,
            ),
        ),
        "traffic_rule": (
            TrafficRule.model_validate(traffic),
            (
                "Block 192.0.2.60",
                "tr.example",
                "tr-net-secret",
                "aa:bb:cc:dd:ee:31",
                "tr-client-secret",
                "tr-tnet-secret",
                *_FUTURE_LEAKS,
            ),
        ),
        "nat_rule": (
            NatRule.model_validate(nat),
            (
                "NAT 198.51.100.7",
                "nat-in-secret",
                "nat-out-secret",
                "2001:db8::77",
                "nat-nc-secret",
                "nat-grp-secret",
                "192.0.2.88",
                *_FUTURE_LEAKS,
            ),
        ),
        "static_route": (
            StaticRoute.model_validate(static),
            (
                "Route 203.0.113.0/24",
                "192.0.2.1",
                "sr-net-secret",
                "aa:bb:cc:dd:ee:41",
                "sr-site-secret",
                *_FUTURE_LEAKS,
            ),
        ),
    }


def _single_rule_document(rule_type: str, record: Any) -> dict[str, Any]:
    """Return a raw export of one site holding one record of one family."""
    return {
        "format_version": 1,
        "generated_at": "2026-01-02T03:04:05+00:00",
        "redacted": False,
        "console": _CONSOLE,
        "sites": {
            "site1": {
                "name": "Home",
                "rule_types": {
                    rule_type: {"status": "ok", "count": 1, "rules": [record]}
                },
            }
        },
    }


@pytest.mark.parametrize("rule_type", RULE_TYPES)
async def test_redacted_export_scrubs_text_identifiers_and_unknown_fields(
    hass: HomeAssistant, rule_type: str
) -> None:
    """Names, references and unknown keys are replaced; the structure stays."""
    model, leaks = _leaky_models()[rule_type]
    document = _single_rule_document(rule_type, _export_record(model))
    original = copy.deepcopy(document)
    raw_dump = json.dumps(document)
    for leak in leaks:
        assert leak in raw_dump, f"{leak} missing from the raw fixture"
    coordinator = MagicMock()
    coordinator.async_export_network_rules = AsyncMock(return_value=document)

    response = await _call(hass, [_entry("UDM", coordinator, _SITES)], {})

    _assert_absent(json.dumps(response), leaks)
    family = response["sites"]["site-1"]["rule_types"][rule_type]
    assert family["status"] == "ok"
    assert family["count"] == 1
    rule = family["rules"][0]
    for key, value in _STRUCTURE[rule_type].items():
        assert rule[key] == value
    assert rule["x_future_target"] == REDACTED
    assert rule["x_future_object"] == REDACTED
    assert document == original


@pytest.mark.parametrize("rule_type", RULE_TYPES)
def test_redacted_export_keeps_exactly_the_allow_listed_values(rule_type: str) -> None:
    """The keys that keep their value are the allow-list, nothing more."""
    model, _ = _leaky_models()[rule_type]
    record = _export_record(model)

    redacted = redact_network_rule_export(_single_rule_document(rule_type, record))

    rule = redacted["sites"]["site-1"]["rule_types"][rule_type]["rules"][0]
    assert set(rule) == set(record)
    kept = {key for key, value in rule.items() if value != REDACTED}
    assert kept
    assert kept == RULE_EXPORT_SAFE_KEYS[rule_type] & set(record)


def test_every_rule_type_has_an_allow_list() -> None:
    """A family without an allow-list would have all its values replaced."""
    assert set(RULE_EXPORT_SAFE_KEYS) == set(RULE_TYPES)


@pytest.mark.parametrize(
    "value",
    [
        "192.0.2.1",
        "2001:db8::1",
        "198.51.100.0/24",
        "host.example",
        "https://host.example/path",
        "My rule",
        "a" * 65,
        "line\n",
    ],
)
def test_safe_export_value_rejects_unsafe_strings(value: str) -> None:
    """Addresses, CIDRs, domains, URLs, free text and long strings are replaced."""
    assert _safe_export_value(value) == REDACTED


@pytest.mark.parametrize(
    "value",
    [
        "TCP_UDP",
        "nexthop-route",
        "8000-8100",
        "80,443",
        "",
        "a" * 64,
        True,
        5,
        1.5,
        None,
    ],
)
def test_safe_export_value_keeps_structural_values(value: Any) -> None:
    """Protocol names, port lists, numbers, booleans and None are kept."""
    assert _safe_export_value(value) == value


def test_safe_export_value_checks_lists_and_mappings() -> None:
    """Containers are checked item by item; an odd key replaces the mapping."""
    assert _safe_export_value(["TCP", "192.0.2.1", 3, None, ["a b", "ok"]]) == [
        "TCP",
        REDACTED,
        3,
        None,
        [REDACTED, "ok"],
    ]
    assert _safe_export_value(
        {"type": "IP", "items": [{"value": "192.0.2.1"}, {"value": "80"}], "n": 2}
    ) == {"type": "IP", "items": [{"value": REDACTED}, {"value": "80"}], "n": 2}
    for odd_keys in ({"aa:bb": "ok"}, {"": "ok"}, {"a" * 65: "ok"}, {1: "ok"}):
        assert _safe_export_value(odd_keys) == REDACTED
    for other in (object(), b"bytes", ("a", "b"), {"a"}):
        assert _safe_export_value(other) == REDACTED


_PINNED_ALLOW_LIST: dict[str, frozenset[str]] = {
    "firewall_rule": frozenset(
        {
            "id",
            "enabled",
            "action",
            "index",
            "logging",
            "loggingEnabled",
            "protocol",
            "ipProtocolScope",
            "connectionStateFilter",
        }
    ),
    "policy_based_route": frozenset(
        {
            "_id",
            "enabled",
            "killSwitch",
            "kill_switch",
            "killSwitchEnabled",
            "kill_switch_enabled",
            "matchingTarget",
            "matching_target",
        }
    ),
    "port_forward": frozenset(
        {
            "_id",
            "enabled",
            "proto",
            "dst_port",
            "fwd_port",
            "log",
            "src_limiting_enabled",
            "pfwd_interface",
        }
    ),
    "traffic_rule": frozenset({"_id", "enabled", "action", "matching_target"}),
    "nat_rule": frozenset(
        {
            "_id",
            "enabled",
            "type",
            "protocol",
            "ip_version",
            "logging",
            "rule_index",
            "exclude",
            "is_predefined",
            "setting_preference",
            "port",
        }
    ),
    "static_route": frozenset(
        {
            "_id",
            "enabled",
            "type",
            "static-route_type",
            "static-route_distance",
            "gateway_type",
        }
    ),
}


def test_rule_export_allow_list_is_pinned() -> None:
    """Widening the allow-list is a deliberate edit of this literal too."""
    assert RULE_EXPORT_SAFE_KEYS == _PINNED_ALLOW_LIST


def test_unknown_rule_type_keeps_no_value() -> None:
    """A rule type without an allow-list has every value replaced."""
    record = {"_id": "abc", "enabled": True, "proto": "tcp"}

    redacted = redact_network_rule_export(_single_rule_document("vpn_client", record))

    rule = redacted["sites"]["site-1"]["rule_types"]["vpn_client"]["rules"][0]
    assert rule == {"_id": REDACTED, "enabled": REDACTED, "proto": REDACTED}


def test_redacted_export_anonymises_mac_under_kept_key() -> None:
    """A MAC that passes the string check is still anonymised afterwards."""
    record = {"_id": "aabbccddeeff", "pfwd_interface": "aa-bb-cc-dd-ee-ff"}
    document = _single_rule_document("port_forward", record)

    redacted = redact_network_rule_export(document)

    dumped = json.dumps(redacted)
    assert "aabbccddeeff" not in dumped
    assert "aa-bb-cc-dd-ee-ff" not in dumped
    rule = redacted["sites"]["site-1"]["rule_types"]["port_forward"]["rules"][0]
    assert rule["_id"].startswith("**REDACTED-MAC-")
    assert rule["pfwd_interface"].startswith("**REDACTED-MAC-")


def test_record_with_a_key_that_is_data_is_replaced_whole() -> None:
    """An address, a host name or a number as a key replaces the whole record."""
    record = {
        "_id": "keep",
        "enabled": True,
        "192.0.2.77": "x",
        "host.example": 1,
        5: 2,
    }

    redacted = redact_network_rule_export(_single_rule_document("port_forward", record))

    rules = redacted["sites"]["site-1"]["rule_types"]["port_forward"]["rules"]
    assert rules == [REDACTED]
    dumped = json.dumps(redacted)
    for name in ("192.0.2.77", "host.example", '"5"'):
        assert name not in dumped


@pytest.mark.parametrize("status", ["ok", "failed", "unsupported"])
def test_redacted_export_keeps_family_status_and_count(status: str) -> None:
    """Status and count of every family survive redaction unchanged."""
    document = _single_rule_document("nat_rule", {"_id": "n", "enabled": True})
    family = document["sites"]["site1"]["rule_types"]["nat_rule"]
    family["status"] = status
    family["count"] = 7

    redacted = redact_network_rule_export(document)

    out = redacted["sites"]["site-1"]["rule_types"]["nat_rule"]
    assert (out["status"], out["count"]) == (status, 7)


def test_redacted_export_checks_family_status_and_count() -> None:
    """Status and count go through the same value check as any kept value."""
    document = _single_rule_document("nat_rule", {"_id": "n"})
    family = document["sites"]["site1"]["rule_types"]["nat_rule"]
    family["status"] = "198.51.100.12"
    family["count"] = "host.example"

    redacted = redact_network_rule_export(document)

    out = redacted["sites"]["site-1"]["rule_types"]["nat_rule"]
    assert (out["status"], out["count"]) == (REDACTED, REDACTED)


def test_export_does_not_invent_fields() -> None:
    """An exported rule has exactly the keys the console sent, nothing else."""
    policy = {
        "id": "fw-spec",
        "name": "Allow DNS",
        "description": None,
        "enabled": True,
        "index": 3,
        "action": {"type": "ALLOW", "allowReturnTraffic": True},
        "source": {"zoneId": "zone-src", "trafficFilter": None},
        "destination": {"zoneId": "zone-dst"},
        "ipProtocolScope": {"ipVersion": "IPV4"},
        "connectionStateFilter": ["NEW", "ESTABLISHED"],
        "ipsecFilter": None,
        "loggingEnabled": True,
        "metadata": {"origin": "USER_DEFINED"},
    }

    record = _export_record(FirewallRule.model_validate(policy))

    assert set(record) == set(policy)
    assert "logging" not in record
    assert record["description"] is None
    assert record["loggingEnabled"] is True
    without_enabled = _export_record(
        FirewallRule.model_validate({"id": "x", "name": "n"})
    )
    assert without_enabled == {"id": "x", "name": "n"}
    assert "enabled" not in without_enabled
    assert "action" not in without_enabled


@pytest.mark.parametrize("rule_type", [t for t in RULE_TYPES if t != "firewall_rule"])
def test_export_exact_key_set_of_every_other_family(rule_type: str) -> None:
    """Every family exports the keys it was given, nested objects included."""
    inputs: dict[str, dict[str, Any]] = {
        "policy_based_route": {
            "_id": "pbr-1",
            "enabled": False,
            "matchingTarget": "INTERNET",
            "nextHop": None,
            "x_future": {"a": 1},
            "targetDevices": [{"type": "CLIENT", "clientMac": "aa:bb:cc:dd:ee:01"}],
        },
        "port_forward": port_forward_record(src=None, x_future="y"),
        "traffic_rule": traffic_rule_record(x_future=[1]),
        "nat_rule": nat_rule_record(),
        "static_route": static_route_record(),
    }
    models = {
        "policy_based_route": PolicyBasedRoute,
        "port_forward": PortForward,
        "traffic_rule": TrafficRule,
        "nat_rule": NatRule,
        "static_route": StaticRoute,
    }
    sent = inputs[rule_type]

    record = _export_record(models[rule_type].model_validate(sent))

    assert set(record) == set(sent)
    assert record == sent


@pytest.mark.parametrize(
    "model", [NatRule, StaticRoute], ids=["nat_rule", "static_route"]
)
def test_export_keeps_enabled_as_sent_for_nat_and_static_routes(model: Any) -> None:
    """The enabled validator's result is exported when the key was sent only."""
    base = {"_id": "r1"}

    assert _export_record(model.model_validate({**base, "enabled": "yes"})) == {
        "_id": "r1",
        "enabled": None,
    }
    assert _export_record(model.model_validate({**base, "enabled": False})) == {
        "_id": "r1",
        "enabled": False,
    }
    assert _export_record(model.model_validate(base)) == base


def test_redacted_export_pseudonymises_sites_and_console() -> None:
    """Site ids become site-1, site-2 by sorted id; names and console go."""
    port_forwards = {
        "status": "ok",
        "count": 2,
        "rules": [port_forward_record(_id="a"), port_forward_record(_id="b")],
    }
    document = {
        "format_version": 1,
        "generated_at": "2026-01-02T03:04:05+00:00",
        "redacted": False,
        "console": {"entry_id": "entry-secret", "title": "Title Secret"},
        "sites": {
            "zz-home-id": {
                "name": "Home Secret",
                "rule_types": {"port_forward": port_forwards},
            },
            "aa-lab-id": {
                "name": "Lab Secret",
                "rule_types": {
                    "nat_rule": {"status": "failed", "count": 0, "rules": []}
                },
            },
        },
    }
    original = copy.deepcopy(document)

    redacted = redact_network_rule_export(document)

    assert set(redacted["sites"]) == {"site-1", "site-2"}
    assert redacted["sites"]["site-1"]["name"] == REDACTED
    assert redacted["sites"]["site-1"]["rule_types"] == {
        "nat_rule": {"status": "failed", "count": 0, "rules": []}
    }
    assert redacted["sites"]["site-2"]["name"] == REDACTED
    site2 = redacted["sites"]["site-2"]["rule_types"]["port_forward"]
    assert (site2["status"], site2["count"], len(site2["rules"])) == ("ok", 2, 2)
    assert redacted["console"] == {"entry_id": REDACTED, "title": REDACTED}
    assert redacted["format_version"] == 1
    assert redacted["generated_at"] == "2026-01-02T03:04:05+00:00"
    assert redacted["redacted"] is True
    _assert_absent(
        json.dumps(redacted),
        (
            "zz-home-id",
            "aa-lab-id",
            "entry-secret",
            "Title Secret",
            "Home Secret",
            "Lab Secret",
        ),
    )
    assert document == original


def test_redacted_export_fails_closed_on_unexpected_shapes() -> None:
    """Whatever the builder does not produce is replaced, never copied."""
    redacted = redact_network_rule_export(
        {
            "format_version": 1,
            "x_future_top": "198.51.100.1",
            "console": "198.51.100.2",
            "sites": {
                "s1": {
                    "name": "n",
                    "x_future_site": "198.51.100.3",
                    "rule_types": {
                        "vpn_client": {
                            "status": "ok",
                            "count": 1,
                            "x_future_family": "198.51.100.4",
                            "rules": ["198.51.100.5", {"a": "198.51.100.6"}],
                        },
                        "nat_rule": "198.51.100.7",
                    },
                },
                "s2": "198.51.100.8",
                "s3": {"name": "198.51.100.10"},
                "s4": {
                    "rule_types": {
                        "nat_rule": {"status": "ok"},
                        "static_route": {"rules": "198.51.100.11"},
                    }
                },
            },
        }
    )

    assert redacted["x_future_top"] == REDACTED
    assert redacted["console"] == REDACTED
    site1 = redacted["sites"]["site-1"]
    assert site1["x_future_site"] == REDACTED
    assert site1["rule_types"]["nat_rule"] == REDACTED
    assert site1["rule_types"]["vpn_client"] == {
        "status": "ok",
        "count": 1,
        "x_future_family": REDACTED,
        "rules": [REDACTED, {"a": REDACTED}],
    }
    assert redacted["sites"]["site-2"] == REDACTED
    assert redacted["sites"]["site-3"] == {"name": REDACTED}
    assert redacted["sites"]["site-4"] == {
        "rule_types": {
            "nat_rule": {"status": "ok"},
            "static_route": {"rules": REDACTED},
        }
    }
    assert "198.51.100" not in json.dumps(redacted)
    assert redact_network_rule_export({"sites": "198.51.100.9"}) == {
        "sites": REDACTED,
        "redacted": True,
    }


async def test_unredacted_export_is_unchanged_by_the_redaction_code(
    hass: HomeAssistant, freezer: Any
) -> None:
    """With redact false the service returns real site ids and the model dumps."""
    freezer.move_to("2026-01-02 03:04:05+00:00")
    models = {rule_type: model for rule_type, (model, _) in _leaky_models().items()}
    client = _client(**{rule_type: [model] for rule_type, model in models.items()})

    async def export(
        site_ids: list[str] | None, rule_types: list[str] | None
    ) -> dict[str, Any]:
        return await _build(client, site_ids=site_ids, rule_types=rule_types)

    coordinator = MagicMock()
    coordinator.async_export_network_rules = AsyncMock(side_effect=export)

    response = await _call(
        hass,
        [_entry("UDM", coordinator, _SITES)],
        {"site_id": "site1", "redact": False},
    )

    assert response["redacted"] is False
    assert response["console"] == _CONSOLE
    assert list(response["sites"]) == ["site1"]
    assert response["sites"]["site1"]["name"] == "Home"
    types = response["sites"]["site1"]["rule_types"]
    for rule_type, model in models.items():
        assert types[rule_type] == {
            "status": "ok",
            "count": 1,
            "rules": [_export_record(model)],
        }
        assert types[rule_type]["rules"] == [_dump(model)]


async def test_service_redacts_by_default(hass: HomeAssistant) -> None:
    """Without arguments the response is redacted and targets the only console."""
    coordinator = MagicMock()
    coordinator.async_export_network_rules = AsyncMock(return_value=_doc())

    response = await _call(hass, [_entry("UDM", coordinator, _SITES)], {})

    coordinator.async_export_network_rules.assert_awaited_once_with(None, None)
    assert response["redacted"] is True
    assert "192.0.2.50" not in json.dumps(response)


async def test_service_raw_export_and_arguments(hass: HomeAssistant) -> None:
    """redact false returns the records unchanged; arguments reach the facade."""
    coordinator = MagicMock()
    coordinator.async_export_network_rules = AsyncMock(return_value=_doc())

    response = await _call(
        hass,
        [_entry("UDM", coordinator, _SITES)],
        {"site_id": "site1", "rule_types": "port_forward", "redact": False},
    )

    coordinator.async_export_network_rules.assert_awaited_once_with(
        ["site1"], ["port_forward"]
    )
    assert response == _doc()


async def test_service_exports_all_six_families_end_to_end(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    freezer: Any,
) -> None:
    """Through the real builder and service: redacted by default, raw on request."""
    freezer.move_to("2026-01-02 03:04:05+00:00")
    caplog.set_level(logging.DEBUG)
    models = _six_models()
    client = _client(**{rule_type: [model] for rule_type, model in models.items()})

    async def export(
        site_ids: list[str] | None, rule_types: list[str] | None
    ) -> dict[str, Any]:
        return await async_build_rule_export(
            client,
            _SITES,
            console=_CONSOLE,
            site_ids=site_ids,
            rule_types=rule_types,
            is_unsupported=UnifiInsightsBaseCoordinator._is_unsupported_response,
        )

    coordinator = MagicMock()
    coordinator.async_export_network_rules = AsyncMock(side_effect=export)
    entries = [_entry("UDM", coordinator, _SITES)]
    every_literal = tuple(
        literal for literals in _SENSITIVE.values() for literal in literals
    )

    redacted = await _call(hass, entries, {"site_id": "site1"})
    raw = await _call(hass, entries, {"site_id": "site1", "redact": False})

    redacted_dump = json.dumps(redacted)
    _assert_absent(redacted_dump, every_literal)
    assert redacted["redacted"] is True
    assert redacted["generated_at"] == "2026-01-02T03:04:05+00:00"
    _assert_absent(redacted_dump, _NAMES)
    raw_types = raw["sites"]["site1"]["rule_types"]
    assert raw["redacted"] is False
    assert {rule_type: raw_types[rule_type]["rules"] for rule_type in RULE_TYPES} == {
        rule_type: [_dump(model)] for rule_type, model in models.items()
    }
    raw_dump = json.dumps(raw)
    for literal in every_literal:
        assert literal in raw_dump
    for name in _NAMES:
        assert name in raw_dump
    # Neither mode writes a rule value or a credential to the log, and the
    # document carries no secret.
    for literal in every_literal:
        assert literal not in caplog.text
    for text in (redacted_dump, raw_dump):
        assert "api_key" not in text.lower()
    assert raw["console"] == _CONSOLE
    assert redacted["console"] == {"entry_id": REDACTED, "title": REDACTED}


async def test_service_requires_response(hass: HomeAssistant) -> None:
    """Calling the action without return_response is rejected."""
    await async_setup_services(hass)
    try:
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(
                DOMAIN, SERVICE_EXPORT_NETWORK_RULES, {}, blocking=True
            )
        assert err.value.translation_key == "service_lacks_response_request"
        assert hass.services.has_service(DOMAIN, SERVICE_EXPORT_NETWORK_RULES)
    finally:
        await async_unload_services(hass)
    assert not hass.services.has_service(DOMAIN, SERVICE_EXPORT_NETWORK_RULES)


async def test_service_rejects_non_admin(hass: HomeAssistant) -> None:
    """A non-admin user cannot export the rules, even with a response request."""
    coordinator = MagicMock()
    coordinator.async_export_network_rules = AsyncMock(return_value=_doc())
    user = await hass.auth.async_create_user("non_admin_user")
    user.is_owner = False
    user.groups = []
    user.invalidate_cache()

    with pytest.raises(Unauthorized):
        await _call(
            hass,
            [_entry("UDM", coordinator, _SITES)],
            {},
            context=Context(user_id=user.id),
        )

    coordinator.async_export_network_rules.assert_not_awaited()


@pytest.mark.parametrize(
    "data",
    [{"rule_types": ["vpn_client"]}, {"unknown": 1}, {"redact": "maybe"}],
)
async def test_service_rejects_bad_input(
    hass: HomeAssistant, data: dict[str, Any]
) -> None:
    """Unknown rule types, unknown fields and non-boolean redact are invalid."""
    coordinator = MagicMock()
    coordinator.async_export_network_rules = AsyncMock(return_value=_doc())

    with pytest.raises(vol.Invalid):
        await _call(hass, [_entry("UDM", coordinator, _SITES)], data)

    coordinator.async_export_network_rules.assert_not_awaited()


async def test_service_console_selection(hass: HomeAssistant) -> None:
    """The console comes from console_id, the site, or the only one configured."""
    first = MagicMock()
    first.async_export_network_rules = AsyncMock(return_value=_doc())
    second = MagicMock()
    second.async_export_network_rules = AsyncMock(return_value=_doc())
    entries = [
        _entry("UDM", first, {"site1": {"id": "site1"}}),
        _entry("Cabin", second, {"site9": {"id": "site9"}}),
    ]

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, entries, {})
    assert err.value.translation_key == "export_console_required"
    first.async_export_network_rules.assert_not_awaited()
    second.async_export_network_rules.assert_not_awaited()

    await _call(hass, entries, {"console_id": "cabin"})
    second.async_export_network_rules.assert_awaited_once_with(None, None)
    first.async_export_network_rules.assert_not_awaited()

    await _call(hass, entries, {"site_id": "site1"})
    first.async_export_network_rules.assert_awaited_once_with(["site1"], None)

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, entries, {"console_id": "Cabin", "site_id": "site1"})
    assert err.value.translation_key == "export_site_not_on_console"
    assert err.value.translation_placeholders == {
        "site_id": "site1",
        "console": "Cabin",
    }

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, entries, {"console_id": "nope"})
    assert err.value.translation_key == "export_target_not_found"
    assert err.value.translation_placeholders == {"target": "nope"}


@pytest.mark.parametrize(
    ("data", "key", "target"),
    [
        ({"console_id": "bad"}, "export_target_not_found", "bad"),
        ({"site_id": "bad"}, "export_target_not_found", "bad"),
        ({"console_id": "UDM"}, "export_target_ambiguous", "UDM"),
        ({"site_id": "site1"}, "export_target_ambiguous", "site1"),
    ],
)
async def test_export_target_errors_are_translated(
    hass: HomeAssistant, data: dict[str, Any], key: str, target: str
) -> None:
    """Unknown or ambiguous consoles and sites raise translated errors."""
    coordinators = [MagicMock(), MagicMock()]
    entries = [
        _entry("UDM", coordinators[0], {"site1": {"id": "site1"}}),
        _entry("UDM", coordinators[1], {"site1": {"id": "site1"}}),
    ]
    for coordinator in coordinators:
        coordinator.async_export_network_rules = AsyncMock(return_value=_doc())

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, entries, data)

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == key
    assert err.value.translation_placeholders == {"target": target}
    for coordinator in coordinators:
        coordinator.async_export_network_rules.assert_not_awaited()


async def test_export_site_shared_by_two_consoles_is_resolved_by_console(
    hass: HomeAssistant,
) -> None:
    """Naming the console by entry ID resolves a site that two consoles share."""
    first, second = MagicMock(), MagicMock()
    for coordinator in (first, second):
        coordinator.async_export_network_rules = AsyncMock(return_value=_doc())
    entries = [
        _entry("UDM", first, {"site1": {"id": "site1"}}),
        _entry("Cabin", second, {"site1": {"id": "site1"}}),
    ]

    await _call(hass, entries, {"site_id": "site1", "console_id": "id-Cabin"})

    second.async_export_network_rules.assert_awaited_once_with(["site1"], None)
    first.async_export_network_rules.assert_not_awaited()


@pytest.mark.parametrize(
    "data",
    [{}, {"rule_types": []}, {"rule_types": None}],
    ids=["omitted", "[]", "None"],
)
async def test_service_empty_rule_types_exports_all(
    hass: HomeAssistant, data: dict[str, Any]
) -> None:
    """No rule types selected means all six, through the real builder."""
    site = {"site1": _SITES["site1"]}
    client = _client()

    async def export(
        site_ids: list[str] | None, rule_types: list[str] | None
    ) -> dict[str, Any]:
        return await async_build_rule_export(
            client,
            site,
            console=_CONSOLE,
            site_ids=site_ids,
            rule_types=rule_types,
            is_unsupported=UnifiInsightsBaseCoordinator._is_unsupported_response,
        )

    coordinator = MagicMock()
    coordinator.async_export_network_rules = AsyncMock(side_effect=export)

    response = await _call(hass, [_entry("UDM", coordinator, site)], data)

    types = response["sites"]["site-1"]["rule_types"]
    assert tuple(types) == RULE_TYPES
    for rule_type in RULE_TYPES:
        _lister(client, rule_type).assert_awaited_once()
    coordinator.async_export_network_rules.assert_awaited_once_with(None, None)


async def test_service_without_console_or_sites(hass: HomeAssistant) -> None:
    """No console, or a console without a Network site, is a validation error."""
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, [], {})
    assert err.value.translation_key == "no_network_console"

    coordinator = MagicMock()
    coordinator.async_export_network_rules = AsyncMock(
        return_value={**_doc(), "sites": {}}
    )
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, [_entry("UDM", coordinator, {})], {})
    assert err.value.translation_key == "export_no_sites"


async def test_service_registered_by_integration_setup(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations: None,
) -> None:
    """The action exists once the integration is set up (async_setup path)."""
    assert hass.services.has_service(DOMAIN, SERVICE_EXPORT_NETWORK_RULES)
    assert (
        hass.services.supports_response(DOMAIN, SERVICE_EXPORT_NETWORK_RULES)
        is SupportsResponse.ONLY
    )
