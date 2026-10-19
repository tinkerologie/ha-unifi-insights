# Copyright (c) 2026 Ruaan Deysel
"""Tests for crash guards protecting Carrier Fabric entries."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_insights import CarrierFabricData
from custom_components.unifi_insights.const import (
    CONF_CARRIER_ORG_ID,
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_CARRIER_FABRIC,
    DOMAIN,
)
from custom_components.unifi_insights.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.unifi_insights.websocket_api import (
    ERR_ENTRY_NOT_LOADED,
    _RequestError,
    _resolve_entry,
    ws_protect_sources,
    ws_topology_sources,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
def carrier_entry_with_runtime_data(hass):
    """Create a mock Carrier Fabric entry with CarrierFabricData runtime data."""
    mock_coordinator = MagicMock()
    mock_coordinator.data = {
        "org_id": "org_xyz999",
        "service_plans": {"plan_1": {"id": "plan_1", "name": "Plan 1"}},
        "subscribers": {"sub_1": {"id": "sub_1", "name": "Sub 1"}},
        "summary": {
            "total_subscribers": 1,
            "suspended_subscribers": 0,
            "active_service_plans": 1,
        },
    }
    mock_client = MagicMock()
    mock_client.close = AsyncMock()

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Carrier Fabric",
        unique_id="carrier_org_xyz999",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            CONF_API_KEY: "top_secret_carrier_key",
            CONF_CARRIER_ORG_ID: "org_xyz999",
        },
    )
    entry.add_to_hass(hass)
    # Loaded, as a running entry is: only loaded entries are ever enumerated.
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = CarrierFabricData(
        client=mock_client,
        coordinator=mock_coordinator,
    )
    return entry


async def test_websocket_guards_ignore_carrier_fabric_entry(
    hass, carrier_entry_with_runtime_data
):
    """Test websocket endpoints ignore or reject Carrier Fabric entries safely."""
    # 1. _resolve_entry raises _RequestError with ERR_ENTRY_NOT_LOADED
    with pytest.raises(_RequestError) as exc_info:
        _resolve_entry(hass, carrier_entry_with_runtime_data.entry_id)
    assert exc_info.value.code == ERR_ENTRY_NOT_LOADED

    # 2. ws_topology_sources runs without error and yields no sources for carrier entry
    mock_connection = MagicMock()
    msg = {"id": 1, "type": "unifi_insights/topology/sources"}
    ws_topology_sources(hass, mock_connection, msg)
    mock_connection.send_result.assert_called_once_with(1, [])

    # 3. ws_protect_sources runs without error and yields no sources for carrier entry
    mock_connection.reset_mock()
    msg = {"id": 2, "type": "unifi_insights/protect/sources"}
    ws_protect_sources(hass, mock_connection, msg)
    mock_connection.send_result.assert_called_once_with(2, [])


async def test_diagnostics_for_carrier_fabric_entry(
    hass, carrier_entry_with_runtime_data
):
    """Test diagnostics returns redacted entry and coordinator summary."""
    diag = await async_get_config_entry_diagnostics(
        hass, carrier_entry_with_runtime_data
    )

    assert "entry" in diag
    assert "summary" in diag

    # Verify sensitive API key is redacted
    entry_dict = diag["entry"]
    assert entry_dict["data"][CONF_API_KEY] == "**REDACTED**"

    # Verify summary is present
    summary = diag["summary"]
    assert summary["total_subscribers"] == 1
    assert summary["suspended_subscribers"] == 0
    assert summary["active_service_plans"] == 1
