# Copyright 2026 UniFi Insights contributors
"""Tests for owner-scoped UniFi Mobility polling."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_IGNORE, ConfigEntryDisabler
from homeassistant.core import CoreState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_insights.api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiRateLimitError,
    UniFiResponseError,
)
from custom_components.unifi_insights.const import (
    DOMAIN,
    SCAN_INTERVAL_MOBILITY,
    SCAN_INTERVAL_MOBILITY_IDLE,
)
from custom_components.unifi_insights.coordinators.mobility import (
    _OWNERS,
    UnifiInsightsMobilityCoordinator,
    _key_fingerprint,
    _owners,
    async_hand_over_mobility,
    async_setup_mobility,
    is_mobility_owner,
)
from tests.fixtures.mobility_responses import (
    BRANCH_ROUTER_ID,
    OFFICE_ROUTER_ID,
    PENDING_WORKSPACE_ID,
    WORKSPACE_ID,
    mobility_client_mock,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER_NAME = "custom_components.unifi_insights.coordinators.mobility"


async def test_owners_registry_uses_hass_key(hass: HomeAssistant) -> None:
    """Ownership persists under the typed key without a domain data container."""
    assert _OWNERS not in hass.data
    assert DOMAIN not in hass.data

    owners = _owners(hass)

    assert owners == {}
    assert hass.data[_OWNERS] is owners
    owners["account-fingerprint"] = "entry-1"
    assert _owners(hass) is owners
    assert _owners(hass) == {"account-fingerprint": "entry-1"}
    assert DOMAIN not in hass.data


def _remote_entry(
    entry_id: str = "01REMOTE", api_key: str = "cloud-key", **kwargs: Any
) -> MockConfigEntry:
    """Return a remote (cloud) config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": "remote",
            "console_id": f"console-{entry_id}",
            "api_key": api_key,
        },
        entry_id=entry_id,
        **kwargs,
    )


def _coordinator(
    hass: HomeAssistant, client: Any = None
) -> UnifiInsightsMobilityCoordinator:
    """Build a coordinator bound to a remote entry."""
    entry = _remote_entry()
    entry.add_to_hass(hass)
    return UnifiInsightsMobilityCoordinator(
        hass, entry, client or mobility_client_mock()
    )


async def test_refresh_builds_snapshot_from_spec_examples(
    hass: HomeAssistant,
) -> None:
    """Active workspaces are polled and device detail is merged per router."""
    client = mobility_client_mock()
    coordinator = _coordinator(hass, client)

    data = await coordinator._async_update_data()

    assert data["access"] == "granted"
    assert data["updated_at"] is not None
    assert set(data["workspaces"]) == {WORKSPACE_ID, PENDING_WORKSPACE_ID}
    assert data["workspaces"][WORKSPACE_ID] == {
        "name": "Headquarters",
        "status": "ACTIVE",
        "is_owner": True,
        "device_ids": [OFFICE_ROUTER_ID, BRANCH_ROUTER_ID],
    }
    assert data["workspaces"][PENDING_WORKSPACE_ID]["device_ids"] == []
    # A pending invitation is listed but never polled.
    client.list_devices.assert_awaited_once_with(WORKSPACE_ID)
    office = data["devices"][OFFICE_ROUTER_ID]
    assert office["workspace_id"] == WORKSPACE_ID
    assert office["detail_available"] is True
    assert office["client_count"] == 5
    assert office["location"]["latitude"] == 37.7749
    assert coordinator.update_interval == SCAN_INTERVAL_MOBILITY
    assert coordinator.last_error_type is None


async def test_access_denied_is_quiet_and_idle(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A key without Mobility scope never asks for reauth and logs only once."""
    client = mobility_client_mock()
    client.list_workspaces.side_effect = UniFiAuthenticationError(
        "Access forbidden", status_code=403
    )
    coordinator = _coordinator(hass, client)
    caplog.set_level(logging.DEBUG, logger=_LOGGER_NAME)

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data["access"] == "denied"
    assert coordinator.data["workspaces"] == {}
    assert coordinator.data["devices"] == {}
    assert coordinator.update_interval == SCAN_INTERVAL_MOBILITY_IDLE
    client.list_devices.assert_not_awaited()
    notices = [
        record
        for record in caplog.records
        if record.name == _LOGGER_NAME and record.levelno >= logging.INFO
    ]
    assert len(notices) == 1
    assert notices[0].levelno == logging.INFO
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


@pytest.mark.parametrize("status", [400, 404])
async def test_workspace_list_client_errors_mean_no_access(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, status: int
) -> None:
    """An account that answers 4xx instead of 403 is treated as having no access."""
    client = mobility_client_mock()
    client.list_workspaces.side_effect = UniFiResponseError(
        "bad_request", status_code=status
    )
    coordinator = _coordinator(hass, client)
    caplog.set_level(logging.DEBUG, logger=_LOGGER_NAME)

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data["access"] == "denied"
    assert coordinator.update_interval == SCAN_INTERVAL_MOBILITY_IDLE
    notices = [
        record
        for record in caplog.records
        if record.levelno >= logging.INFO
        and record.name.startswith("custom_components.unifi_insights")
    ]
    assert len(notices) == 1
    assert notices[0].levelno == logging.INFO


async def test_access_regained_restores_the_normal_interval(
    hass: HomeAssistant,
) -> None:
    """Adding the Mobility scope to the key is picked up on the next poll."""
    client = mobility_client_mock()
    workspaces = client.list_workspaces.return_value
    client.list_workspaces.side_effect = UniFiAuthenticationError(
        "Access forbidden", status_code=403
    )
    coordinator = _coordinator(hass, client)
    await coordinator.async_refresh()

    client.list_workspaces.side_effect = None
    client.list_workspaces.return_value = workspaces
    await coordinator.async_refresh()

    assert coordinator.data["access"] == "granted"
    assert set(coordinator.data["devices"]) == {OFFICE_ROUTER_ID, BRANCH_ROUTER_ID}
    assert coordinator.update_interval == SCAN_INTERVAL_MOBILITY


@pytest.mark.parametrize("workspaces", [[], "pending_only"])
async def test_no_devices_backs_off_to_the_idle_interval(
    hass: HomeAssistant, workspaces: Any
) -> None:
    """Accounts without routers cost one request an hour."""
    client = mobility_client_mock()
    if workspaces == "pending_only":
        workspaces = [
            row
            for row in client.list_workspaces.return_value
            if row["status"] != "ACTIVE"
        ]
    client.list_workspaces.return_value = workspaces
    coordinator = _coordinator(hass, client)

    data = await coordinator._async_update_data()

    assert data["access"] == "granted"
    assert data["devices"] == {}
    assert coordinator.update_interval == SCAN_INTERVAL_MOBILITY_IDLE


async def test_forbidden_workspace_is_skipped(hass: HomeAssistant) -> None:
    """A workspace whose devices the key may not read does not fail the poll."""
    client = mobility_client_mock()
    client.list_devices.side_effect = UniFiAuthenticationError(
        "Access forbidden", status_code=403
    )
    coordinator = _coordinator(hass, client)

    data = await coordinator._async_update_data()

    assert data["access"] == "granted"
    assert data["workspaces"][WORKSPACE_ID]["device_ids"] == []
    assert data["devices"] == {}


@pytest.mark.parametrize(
    "error",
    [
        UniFiResponseError("device not found", status_code=404),
        UniFiResponseError("upstream_error", status_code=500),
        UniFiAuthenticationError("forbidden", status_code=403),
    ],
)
async def test_device_detail_failure_keeps_the_summary(
    hass: HomeAssistant, error: Exception
) -> None:
    """One router's detail failing leaves its summary fields in place."""
    client = mobility_client_mock()
    detail = client.get_device.side_effect

    async def get_device(workspace_id: str, device_id: str) -> dict[str, Any]:
        if device_id == BRANCH_ROUTER_ID:
            raise error
        return await detail(workspace_id, device_id)

    client.get_device.side_effect = get_device
    coordinator = _coordinator(hass, client)

    data = await coordinator._async_update_data()

    branch = data["devices"][BRANCH_ROUTER_ID]
    assert branch["detail_available"] is False
    assert branch["state"] == "DISCONNECTED"
    assert branch["workspace_id"] == WORKSPACE_ID
    assert "client_count" not in branch
    assert data["devices"][OFFICE_ROUTER_ID]["detail_available"] is True


@pytest.mark.parametrize(
    ("method", "error"),
    [
        ("list_workspaces", UniFiConnectionError("offline")),
        ("list_workspaces", UniFiResponseError("server_error", status_code=500)),
        ("list_workspaces", UniFiRateLimitError("rate_limit", status_code=429)),
        ("list_devices", UniFiResponseError("server_error", status_code=500)),
        ("get_device", UniFiRateLimitError("rate_limit", status_code=429)),
    ],
)
async def test_failed_poll_keeps_the_previous_snapshot(
    hass: HomeAssistant, method: str, error: Exception
) -> None:
    """Outages mark the poll failed instead of reporting routers as gone."""
    client = mobility_client_mock()
    coordinator = _coordinator(hass, client)
    await coordinator.async_refresh()
    previous = coordinator.data

    getattr(client, method).side_effect = error
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert coordinator.data is previous
    assert coordinator.last_error_type == type(error).__name__

    getattr(client, method).side_effect = None
    client.list_workspaces.return_value = []
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.last_error_type is None


async def test_unsafe_or_missing_ids_are_skipped(hass: HomeAssistant) -> None:
    """Rows without a usable id are ignored rather than requested."""
    client = mobility_client_mock()
    client.list_workspaces.return_value = [
        *client.list_workspaces.return_value,
        {"workspace_name": "No id", "status": "ACTIVE"},
        {"workspace_id": "../admins", "status": "ACTIVE"},
    ]
    summaries = client.list_devices.side_effect

    async def list_devices(workspace_id: str) -> list[dict[str, Any]]:
        return [*await summaries(workspace_id), {"name": "no id"}, {"id": "a/b"}]

    client.list_devices.side_effect = list_devices
    coordinator = _coordinator(hass, client)

    data = await coordinator._async_update_data()

    assert set(data["workspaces"]) == {WORKSPACE_ID, PENDING_WORKSPACE_ID}
    assert set(data["devices"]) == {OFFICE_ROUTER_ID, BRANCH_ROUTER_ID}
    assert client.list_devices.await_count == 1
    assert client.get_device.await_count == 2


async def test_large_fleet_stretches_the_poll_interval(hass: HomeAssistant) -> None:
    """Polling stays within half of the key's 100 requests per minute."""
    client = mobility_client_mock()
    fleet = [
        {"id": f"00000000-0000-0000-0000-{index:012d}", "state": "CONNECTED"}
        for index in range(300)
    ]
    client.list_devices.side_effect = None
    client.list_devices.return_value = fleet
    client.get_device.side_effect = None
    client.get_device.return_value = {"client_count": 1}
    coordinator = _coordinator(hass, client)

    await coordinator._async_update_data()

    # 1 workspace list + 1 device list + 300 details = 302 requests.
    assert coordinator.update_interval == timedelta(minutes=7)


async def test_owner_is_the_oldest_enabled_remote_entry_with_the_key(
    hass: HomeAssistant,
) -> None:
    """Exactly one entry per cloud key polls Mobility."""
    owner = _remote_entry("01B")
    second = _remote_entry("01C")
    disabled = _remote_entry("01A", disabled_by=ConfigEntryDisabler.USER)
    ignored = _remote_entry("00A", source=SOURCE_IGNORE)
    other_key = _remote_entry("00B", api_key="other-key")
    local = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": "local", "api_key": "cloud-key", "host": "x"},
        entry_id="00C",
    )
    for entry in (owner, second, disabled, ignored, other_key, local):
        entry.add_to_hass(hass)

    assert is_mobility_owner(hass, owner) is True
    assert is_mobility_owner(hass, second) is False
    assert is_mobility_owner(hass, other_key) is True
    assert is_mobility_owner(hass, local) is False


async def test_setup_skips_entries_that_do_not_own_mobility(
    hass: HomeAssistant,
) -> None:
    """A second entry on the same key creates no client at all."""
    _remote_entry("01A").add_to_hass(hass)
    second = _remote_entry("01B")
    second.add_to_hass(hass)

    with patch(
        "custom_components.unifi_insights.coordinators.mobility.UniFiMobilityClient"
    ) as client_class:
        assert async_setup_mobility(hass, second, AsyncMock()) is None

    client_class.assert_not_called()


async def test_setup_refreshes_in_the_background(hass: HomeAssistant) -> None:
    """The owner's first poll never holds up config entry setup."""
    entry = _remote_entry()
    entry.add_to_hass(hass)
    client = mobility_client_mock()
    session = AsyncMock()

    with patch(
        "custom_components.unifi_insights.coordinators.mobility.UniFiMobilityClient",
        return_value=client,
    ) as client_class:
        coordinator = async_setup_mobility(hass, entry, session)
        assert coordinator is not None
        await hass.async_block_till_done(wait_background_tasks=True)

    assert client_class.call_args.kwargs["session"] is session
    assert coordinator.config_entry is entry
    client.list_workspaces.assert_awaited_once()
    assert set(coordinator.data["devices"]) == {OFFICE_ROUTER_ID, BRANCH_ROUTER_ID}


async def test_hand_over_only_when_no_entry_is_left_polling(
    hass: HomeAssistant,
) -> None:
    """Hand-over reloads a successor only when Mobility would otherwise stop."""
    owner = _remote_entry("01A")
    sibling = _remote_entry("01B")
    other_key = _remote_entry("01C", api_key="other-key")
    local = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": "local", "api_key": "cloud-key", "host": "x"},
        entry_id="01D",
    )
    for entry in (owner, sibling, other_key, local):
        entry.add_to_hass(hass)

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        # The owner still polls, so a sibling leaving changes nothing.
        _owners(hass)[_key_fingerprint("cloud-key")] = owner.entry_id
        async_hand_over_mobility(hass, sibling)
        # Local entries never poll Mobility.
        async_hand_over_mobility(hass, local)
        # The last entry on a key has nobody to hand over to.
        async_hand_over_mobility(hass, other_key)
        # The successor is not loaded, so its own setup takes Mobility.
        async_hand_over_mobility(hass, owner)
        # Stopping Home Assistant unloads every entry; that is no hand-over.
        _owners(hass)[_key_fingerprint("cloud-key")] = owner.entry_id
        hass.set_state(CoreState.stopping)
        try:
            async_hand_over_mobility(hass, owner)
        finally:
            hass.set_state(CoreState.running)

    reload.assert_not_called()
    assert _owners(hass) == {_key_fingerprint("cloud-key"): owner.entry_id}
