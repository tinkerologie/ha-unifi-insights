# Copyright 2026 UniFi Insights contributors
"""Tests for the privacy-safe UniFi Mobility diagnostics summary."""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api import UniFiResponseError

from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.fixtures.mobility_responses import (
    BRANCH_ROUTER_ID,
    DEVICE_SUMMARIES,
    OFFICE_ROUTER_DETAIL,
    OFFICE_ROUTER_ID,
    PENDING_WORKSPACE_ID,
    WORKSPACE_ID,
    WORKSPACES,
    mobility_client_mock,
)

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.usefixtures(
    "mock_network_client",
    "mock_protect_client",
    "mock_site_manager_client",
    "enable_custom_integrations",
)

# Everything identifying in the spec examples: names, ids, addresses, the
# carrier, the SSID, VPN and rule names, and the GPS fix.
_PRIVATE_VALUES = [
    WORKSPACE_ID,
    PENDING_WORKSPACE_ID,
    OFFICE_ROUTER_ID,
    BRANCH_ROUTER_ID,
    "Headquarters",
    "Invited Workspace",
    "Office Router",
    "Branch Router",
    "00:1A:2B:3C:4D:5E",
    "AA:BB:CC:DD:EE:FF",
    "203.0.113.42",
    "192.168.1.1",
    "AT&T",
    "UniFi-LTE",
    "Branch-Private",
    "HQ-VPN",
    "Block-IoT-Outbound",
    "37.7749",
    "-122.4194",
    "1709712000000",
]


def _serve(client: MagicMock) -> MagicMock:
    """Make the autouse Mobility client mock answer with the spec examples."""
    examples = mobility_client_mock()
    client.list_workspaces = examples.list_workspaces
    client.list_devices = examples.list_devices
    client.get_device = examples.get_device
    return examples


def _entry(entry_id: str, console_id: str, connection_type: str) -> MockConfigEntry:
    """Return a config entry on the shared test key."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": connection_type,
            "console_id": console_id,
            "api_key": "cloud-key",
            "host": "https://192.0.2.1",
        },
        unique_id=console_id,
        entry_id=entry_id,
    )


async def _load(hass: HomeAssistant, *entries: MockConfigEntry) -> None:
    """Add the entries and set up the domain, which loads all of them."""
    for entry in entries:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entries[0].entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_mobility_diagnostics_are_counts_only(
    hass: HomeAssistant, mock_mobility_client: MagicMock
) -> None:
    """The summary explains Mobility health without exposing the account."""
    _serve(mock_mobility_client)
    entry = _entry("01REMOTE", "console", "remote")
    await _load(hass, entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["mobility"] == {
        "owner": True,
        "access": "granted",
        "last_update_success": True,
        "last_error_type": None,
        "update_interval_seconds": 300,
        "updated_at": diagnostics["mobility"]["updated_at"],
        "workspaces": {"total": 2, "by_status": {"ACTIVE": 1, "PENDING": 1}},
        "devices": {
            "total": 2,
            "by_state": {"CONNECTED": 1, "DISCONNECTED": 1},
            "by_model": {"UMR": 1, "UMR Ultra": 1},
            "with_location": 1,
            "detail_unavailable": 0,
        },
    }
    dump = json.dumps(diagnostics, default=str)
    leaked = [value for value in _PRIVATE_VALUES if value in dump]
    assert leaked == []


async def test_unexpected_api_values_are_bucketed(
    hass: HomeAssistant, mock_mobility_client: MagicMock
) -> None:
    """Values outside the published enums cannot carry free text out."""
    examples = _serve(mock_mobility_client)
    workspaces = copy.deepcopy(WORKSPACES)
    workspaces[1]["status"] = "Private Workspace Label"
    examples.list_workspaces.return_value = workspaces
    summaries = copy.deepcopy(DEVICE_SUMMARIES)
    summaries[1].update(state="Custom state", model="Prototype Serial 1234")

    async def list_devices(workspace_id: str) -> list[dict[str, Any]]:
        return summaries if workspace_id == WORKSPACE_ID else []

    async def get_device(workspace_id: str, device_id: str) -> dict[str, Any]:
        if device_id == OFFICE_ROUTER_ID:
            return copy.deepcopy(OFFICE_ROUTER_DETAIL)
        msg = "upstream_error"
        raise UniFiResponseError(msg, status_code=500)

    mock_mobility_client.list_devices.side_effect = list_devices
    mock_mobility_client.get_device.side_effect = get_device
    entry = _entry("01REMOTE", "console", "remote")
    await _load(hass, entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    mobility = diagnostics["mobility"]
    assert mobility["workspaces"]["by_status"] == {"ACTIVE": 1, "other": 1}
    assert mobility["devices"]["by_state"] == {"CONNECTED": 1, "other": 1}
    assert mobility["devices"]["by_model"] == {"UMR": 1, "other": 1}
    assert mobility["devices"]["detail_unavailable"] == 1
    dump = json.dumps(diagnostics, default=str)
    for private in ("Private Workspace Label", "Custom state", "Prototype Serial"):
        assert private not in dump


async def test_only_the_owner_reports_a_mobility_summary(
    hass: HomeAssistant, mock_mobility_client: MagicMock
) -> None:
    """A second entry on the key says it is not the owner; local entries say nothing."""
    _serve(mock_mobility_client)
    owner = _entry("01A", "console-a", "remote")
    second = _entry("01B", "console-b", "remote")
    local = _entry("01C", "console-c", "local")
    await _load(hass, owner, second, local)

    owner_diagnostics = await async_get_config_entry_diagnostics(hass, owner)
    second_diagnostics = await async_get_config_entry_diagnostics(hass, second)
    local_diagnostics = await async_get_config_entry_diagnostics(hass, local)

    assert owner_diagnostics["mobility"]["owner"] is True
    assert second_diagnostics["mobility"] == {"owner": False}
    assert "mobility" not in local_diagnostics
