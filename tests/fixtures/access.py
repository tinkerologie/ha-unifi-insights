# Copyright 2026 UniFi Insights contributors
"""Shared UniFi Access test data and client doubles."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.const import CONF_API_KEY, EVENT_HOMEASSISTANT_STOP
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
)
from unifi_official_api.access.models.devices import AccessDevice
from unifi_official_api.access.models.doors import AccessDoor
from unifi_official_api.access.models.emergency import AccessEmergencyStatus

from custom_components.unifi_insights.const import (
    CONF_ACCESS_TOKEN,
    CONF_CONSOLE_ID,
    DOMAIN,
)
from custom_components.unifi_insights.coordinators.access import (
    UnifiInsightsAccessCoordinator,
)

if TYPE_CHECKING:
    from homeassistant.core import Event

# --- Builders ----------------------------------------------------------------


def _door(door_id: str, **fields: Any) -> AccessDoor:
    """Build a door the way the client parses the console response."""
    return AccessDoor.model_validate({"id": door_id, "name": door_id, **fields})


def _device(device_id: str, **fields: Any) -> AccessDevice:
    """Build a device the way the client parses the console response."""
    return AccessDevice.model_validate({"id": device_id, "name": device_id, **fields})


def _entry(hass: Any, data: dict[str, Any] | None = None) -> MockConfigEntry:
    """Return a config entry with the Access credentials and console id."""
    entry = MockConfigEntry(domain=DOMAIN, data=data or dict(ENTRY_DATA))
    entry.add_to_hass(hass)
    return entry


def _shutdown_with_hass(hass: Any, coordinator: UnifiInsightsAccessCoordinator) -> None:
    """Stop the socket and poll timer when the test's hass stops.

    These tests build standalone entries and never unload them, so the
    coordinator's entry unload hook never runs. This listener stops the socket
    and poll timer for those standalone test entries.
    """

    async def _on_stop(_: Event) -> None:
        await coordinator.async_stop_websocket()
        await coordinator.async_shutdown()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop)


# --- Access console ----------------------------------------------------------

NAMESPACE = "aa:bb:cc:dd:ee:01"


ENTRY_DATA = {
    CONF_CONSOLE_ID: NAMESPACE,
    CONF_API_KEY: "api-key-value",
    CONF_ACCESS_TOKEN: "access-token-value",
}


def _client(
    doors: list[AccessDoor] | None = None,
    devices: list[AccessDevice] | None = None,
    emergency: AccessEmergencyStatus | None = None,
) -> MagicMock:
    """Return a client double whose calls match the Access endpoint surface."""
    client = MagicMock(name="access_client")
    client.doors.get_all = AsyncMock(return_value=doors or [])
    client.doors.get = AsyncMock()
    client.doors.unlock = AsyncMock(return_value=None)
    client.doors.set_lock_rule = AsyncMock(return_value=None)
    client.devices.get_all = AsyncMock(return_value=devices or [])
    client.emergency.get = AsyncMock(
        return_value=emergency
        or AccessEmergencyStatus.model_validate(
            {"lockdown": False, "evacuation": False}
        )
    )
    client.emergency.set = AsyncMock(return_value=None)
    client.users.set_pin = AsyncMock(return_value=None)
    client.users.remove_pin = AsyncMock(return_value=None)
    client.users.set_status = AsyncMock(return_value=None)
    client.close = AsyncMock()
    return client


async def _ready(
    hass: Any, client: MagicMock, entry: MockConfigEntry | None = None
) -> UnifiInsightsAccessCoordinator:
    """Return a coordinator after one successful refresh, torn down with hass."""
    coordinator = UnifiInsightsAccessCoordinator(hass, entry or _entry(hass), client)
    _shutdown_with_hass(hass, coordinator)
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    return coordinator


DOORS = [
    _door(
        "door-gate",
        is_bind_hub=True,
        door_lock_relay_status="lock",
        door_position_status="close",
    ),
    _door(
        "door-plain",
        is_bind_hub=True,
        door_lock_relay_status="unlock",
        door_position_status=None,
    ),
    _door("door-unbound", is_bind_hub=False),
]


DEVICES = [
    _device("gate-hub", type="UGT", location_id="door-gate", is_online=True),
    _device("reader", type="UA-G2-PRO", location_id="door-plain", is_online=None),
    _device("hub", type="UAH", location_id=None),
]
