# Copyright 2026 UniFi Insights contributors
"""Tests for rule audit integration in UnifiInsightsConfigCoordinator."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from unifi_official_api import (
    UniFiConnectionError,
    UniFiNotFoundError,
)
from unifi_official_api.network.endpoints.port_forwards import (
    PortForwardsEndpoint,
)
from unifi_official_api.network.models import (
    FirewallRule,
    NatRule,
    PolicyBasedRoute,
    PortForward,
    StaticRoute,
    TrafficRule,
)

from custom_components.unifi_insights.coordinators.config import (
    UnifiInsightsConfigCoordinator,
)
from custom_components.unifi_insights.coordinators.config_sections import (
    SECTION_OUTCOME_FAILED,
    async_fetch_site_routes,
)
from tests.fixtures.assertions import patch_attribute
from tests.fixtures.coordinator_clients import (
    _create_mock_model,
    _create_mock_network_client,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.fixture
def coordinator(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> UnifiInsightsConfigCoordinator:
    """Create a config coordinator with mocked network client."""
    client = _create_mock_network_client()
    site = _create_mock_model(
        {"id": "default", "name": "Default", "internalReference": "default"}
    )
    client.sites.get_all = AsyncMock(return_value=[site])
    for endpoint, method in (
        ("firewall", "list_rules"),
        ("routes", "list_routes"),
        ("port_forwards", "list_port_forwards"),
        ("traffic_rules", "list_traffic_rules"),
        ("nat_rules", "list_nat_rules"),
        ("static_routes", "list_static_routes"),
    ):
        setattr(getattr(client, endpoint), method, AsyncMock(return_value=[]))
    return UnifiInsightsConfigCoordinator(
        hass=hass, network_client=client, protect_client=None, entry=mock_config_entry
    )


MODELS_BY_TYPE: dict[str, tuple[type, str]] = {
    "firewall_rule": (FirewallRule, "name"),
    "policy_based_route": (PolicyBasedRoute, "description"),
    "port_forward": (PortForward, "name"),
    "traffic_rule": (TrafficRule, "description"),
    "nat_rule": (NatRule, "description"),
    "static_route": (StaticRoute, "name"),
}


@pytest.mark.parametrize(
    ("client_lister", "record_id_key", "rule_type"),
    [
        ("firewall.list_rules", "id", "firewall_rule"),
        ("routes.list_routes", "_id", "policy_based_route"),
        ("port_forwards.list_port_forwards", "_id", "port_forward"),
        ("traffic_rules.list_traffic_rules", "_id", "traffic_rule"),
        ("nat_rules.list_nat_rules", "_id", "nat_rule"),
        ("static_routes.list_static_routes", "_id", "static_route"),
    ],
)
async def test_first_refresh_is_silent_then_change_is_reported(
    coordinator: UnifiInsightsConfigCoordinator,
    client_lister: str,
    record_id_key: str,
    rule_type: str,
) -> None:
    """First refresh establishes baseline; subsequent rule change is detected."""
    model_cls, name_field = MODELS_BY_TYPE[rule_type]
    rule_id = "test_rule_1"
    model_on = model_cls(
        **{record_id_key: rule_id, name_field: "Test Rule", "enabled": True}
    )
    model_off = model_cls(
        **{record_id_key: rule_id, name_field: "Test Rule", "enabled": False}
    )

    endpoint_name, method_name = client_lister.split(".")
    endpoint = getattr(coordinator.network_client, endpoint_name)
    setattr(endpoint, method_name, AsyncMock(return_value=[model_on]))

    await coordinator._async_update_data()
    assert coordinator.data["rule_changes"] == {}

    setattr(endpoint, method_name, AsyncMock(return_value=[model_off]))

    await coordinator._async_update_data()
    changes = coordinator.data["rule_changes"].get("default", [])
    assert len(changes) == 1
    change = changes[0]
    assert (change["change_type"], change["rule_type"], change["rule_id"]) == (
        "modified",
        rule_type,
        rule_id,
    )
    assert change["changed_fields"] == ["enabled"]


@pytest.mark.parametrize(
    "error",
    [
        UniFiConnectionError("boom"),
        UniFiNotFoundError("Not found", status_code=404),
    ],
)
async def test_failed_or_unsupported_fetch_reports_no_deletions(
    coordinator: UnifiInsightsConfigCoordinator, error: Exception
) -> None:
    """Failed or unsupported fetch keeps baseline and reports no deletions."""
    pf1 = _create_mock_model({"id": "pf1", "name": "Port Forward 1", "enabled": True})
    coordinator.network_client.port_forwards.list_port_forwards = AsyncMock(
        return_value=[pf1]
    )
    await coordinator._async_update_data()

    coordinator.network_client.port_forwards.list_port_forwards = AsyncMock(
        side_effect=error
    )
    await coordinator._async_update_data()
    assert coordinator.data["rule_changes"] == {}

    coordinator.network_client.port_forwards.list_port_forwards = AsyncMock(
        return_value=[pf1]
    )
    await coordinator._async_update_data()
    assert coordinator.data["rule_changes"] == {}


async def test_policy_based_route_failure_reports_no_deletions(
    coordinator: UnifiInsightsConfigCoordinator,
) -> None:
    """Failure fetching policy-based routes reports no spurious deletions."""
    route1 = _create_mock_model({"id": "r1", "description": "Route 1", "enabled": True})
    coordinator.network_client.routes.list_routes = AsyncMock(return_value=[route1])
    await coordinator._async_update_data()

    coordinator.network_client.routes.list_routes = AsyncMock(
        side_effect=UniFiConnectionError("boom")
    )
    await coordinator._async_update_data()
    assert coordinator.data["policy_based_routes"]["default"] == {}
    assert coordinator.data["rule_changes"] == {}

    coordinator.network_client.routes.list_routes = AsyncMock(return_value=[route1])
    await coordinator._async_update_data()
    assert coordinator.data["rule_changes"] == {}


async def test_own_switch_toggle_is_reported(
    coordinator: UnifiInsightsConfigCoordinator,
) -> None:
    """In-place coordinator data mutation by switches still diffs correctly."""
    pf1 = _create_mock_model({"id": "pf1", "name": "Port Forward 1", "enabled": True})
    coordinator.network_client.port_forwards.list_port_forwards = AsyncMock(
        return_value=[pf1]
    )
    await coordinator._async_update_data()

    # Simulate switch optimistic in-place update
    coordinator.data["port_forwards"]["default"]["pf1"]["enabled"] = False

    pf1_disabled = _create_mock_model(
        {"id": "pf1", "name": "Port Forward 1", "enabled": False}
    )
    coordinator.network_client.port_forwards.list_port_forwards = AsyncMock(
        return_value=[pf1_disabled]
    )
    await coordinator._async_update_data()
    changes = coordinator.data["rule_changes"].get("default", [])
    assert len(changes) == 1
    assert changes[0]["changed_fields"] == ["enabled"]


async def test_audit_error_does_not_fail_the_refresh(
    coordinator: UnifiInsightsConfigCoordinator, caplog: pytest.LogCaptureFixture
) -> None:
    """An exception in audit processing is caught and logged, not failing refresh."""
    coordinator._rule_audit.process = MagicMock(side_effect=RuntimeError("bug"))
    with caplog.at_level(logging.ERROR):
        result = await coordinator._async_update_data()

    assert result is coordinator.data
    assert coordinator.data["rule_changes"] == {}
    assert coordinator._available is True
    assert "rule change detection failed" in caplog.text


async def test_no_sites_clears_rule_changes(
    coordinator: UnifiInsightsConfigCoordinator,
) -> None:
    """When no sites are returned, rule_changes dict is cleared."""
    pf1 = _create_mock_model({"id": "pf1", "name": "Port Forward 1", "enabled": True})
    coordinator.network_client.port_forwards.list_port_forwards = AsyncMock(
        return_value=[pf1]
    )
    await coordinator._async_update_data()

    pf1_mod = _create_mock_model(
        {"id": "pf1", "name": "Port Forward 1", "enabled": False}
    )
    coordinator.network_client.port_forwards.list_port_forwards = AsyncMock(
        return_value=[pf1_mod]
    )
    await coordinator._async_update_data()
    assert coordinator.data["rule_changes"] != {}

    coordinator.network_client.sites.get_all = AsyncMock(return_value=[])
    await coordinator._async_update_data()
    assert coordinator.data["rule_changes"] == {}


async def test_fetch_optional_section_records_outcomes(
    coordinator: UnifiInsightsConfigCoordinator,
) -> None:
    """_fetch_optional_section records ok, unsupported, and failed in outcomes."""
    outcomes: dict[tuple[str, str], str] = {}

    fetch_ok = AsyncMock(return_value=["item1"])
    res_ok = await coordinator._fetch_optional_section(
        "section_a", "s1", fetch_ok, outcomes=outcomes
    )
    assert res_ok == ["item1"]
    assert outcomes[("section_a", "s1")] == "ok"

    fetch_unsupp = AsyncMock(
        side_effect=UniFiNotFoundError("Not found", status_code=404)
    )
    res_unsupp = await coordinator._fetch_optional_section(
        "section_b", "s1", fetch_unsupp, outcomes=outcomes
    )
    assert res_unsupp == []
    assert outcomes[("section_b", "s1")] == "unsupported"

    fetch_fail = AsyncMock(side_effect=UniFiConnectionError("boom"))
    res_fail = await coordinator._fetch_optional_section(
        "section_c", "s1", fetch_fail, outcomes=outcomes
    )
    assert res_fail is None
    assert outcomes[("section_c", "s1")] == "failed"

    # Call without outcomes keyword still works
    res_no_outcomes = await coordinator._fetch_optional_section(
        "section_d", "s1", fetch_ok
    )
    assert res_no_outcomes == ["item1"]


async def test_empty_2xx_body_reports_no_deletions(
    coordinator: UnifiInsightsConfigCoordinator, monkeypatch
) -> None:
    """An empty 2xx body is not reported as every rule being deleted."""
    stub_client = MagicMock()
    stub_client.build_legacy_api_path = MagicMock(return_value="/dummy/path")
    envelope = {"data": [{"_id": "pf1", "name": "PF 1", "enabled": True}]}
    stub_client._get = AsyncMock(side_effect=[envelope, None, envelope])
    patch_attribute(
        monkeypatch,
        coordinator.network_client,
        "port_forwards",
        PortForwardsEndpoint(stub_client),
    )

    await coordinator._async_update_data()
    assert coordinator.data["rule_changes"] == {}

    await coordinator._async_update_data()
    assert coordinator.data["rule_changes"] == {}

    await coordinator._async_update_data()
    assert coordinator.data["rule_changes"] == {}


@pytest.mark.parametrize("record_outcome", [True, False])
async def test_failed_route_fetch_returns_nothing_with_or_without_outcomes(
    record_outcome: bool,  # noqa: FBT001
) -> None:
    """A failed policy-based route fetch yields no routes; the outcome is optional."""
    client = MagicMock()
    client.routes.list_routes = AsyncMock(side_effect=UniFiConnectionError("down"))
    outcomes: dict[tuple[str, str], str] | None = {} if record_outcome else None

    routes = await async_fetch_site_routes(
        client, dict, "site1", "default", outcomes=outcomes
    )

    assert routes == {}
    if record_outcome:
        assert outcomes == {("policy_based_routes", "site1"): SECTION_OUTCOME_FAILED}
    else:
        assert outcomes is None
