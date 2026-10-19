# Copyright 2026 UniFi Insights contributors
"""Verify the Access coordinator's state, feature denial, commands and frames."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_API_KEY, EVENT_HOMEASSISTANT_STOP
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiError,
    UniFiRateLimitError,
    UniFiResponseError,
    UniFiValidationError,
)
from unifi_official_api.access.models.devices import AccessDevice
from unifi_official_api.access.models.doors import AccessDoor
from unifi_official_api.access.models.emergency import (
    AccessEmergencyStatus,
)

from custom_components.unifi_insights.const import (
    CONF_ACCESS_TOKEN,
    CONF_CONSOLE_ID,
    DOMAIN,
)
from custom_components.unifi_insights.coordinators import access as access_module
from custom_components.unifi_insights.coordinators.access import (
    ACCESS_DEBOUNCE_SECONDS,
    UnifiInsightsAccessCoordinator,
    access_doorbell_signal,
    access_result_signal,
    async_dispatch_access_result,
    emergency_target,
    resolve_access_namespace,
)

if TYPE_CHECKING:
    from homeassistant.core import Event

NAMESPACE = "aa:bb:cc:dd:ee:01"
ENTRY_DATA = {
    CONF_CONSOLE_ID: NAMESPACE,
    CONF_API_KEY: "api-key-value",
    CONF_ACCESS_TOKEN: "access-token-value",
}


def _door(door_id: str, **fields: Any) -> AccessDoor:
    """Build a door the way the client parses the console response."""
    return AccessDoor.model_validate({"id": door_id, "name": door_id, **fields})


def _device(device_id: str, **fields: Any) -> AccessDevice:
    """Build a device the way the client parses the console response."""
    return AccessDevice.model_validate({"id": device_id, "name": device_id, **fields})


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


async def test_refresh_normalizes_doors_devices_and_emergency(hass: Any) -> None:
    """Relay, position, device and emergency values are kept or set to None."""
    client = _client(
        doors=[*DOORS, _door("", name="no id")],
        devices=DEVICES,
        emergency=AccessEmergencyStatus.model_validate(
            {"lockdown": True, "evacuation": False}
        ),
    )
    coordinator = await _ready(hass, client)

    assert coordinator.namespace == NAMESPACE
    assert coordinator.data["doors"]["door-gate"] == {
        "id": "door-gate",
        "name": "door-gate",
        "is_bind_hub": True,
        "lock_relay": "lock",
        "position": "close",
    }
    assert coordinator.data["doors"]["door-plain"]["position"] is None
    assert coordinator.data["devices"]["gate-hub"]["is_online"] is True
    assert coordinator.data["devices"]["reader"]["is_online"] is None
    assert coordinator.data["emergency"] == {"lockdown": True, "evacuation": False}
    assert "" not in coordinator.data["doors"]


async def test_refresh_with_unknown_emergency_fields_keeps_none(hass: Any) -> None:
    """Emergency values the console omits stay unknown instead of becoming False."""
    client = _client(emergency=AccessEmergencyStatus.model_validate({}))
    coordinator = await _ready(hass, client)

    assert coordinator.data["emergency"] == {"lockdown": None, "evacuation": None}


async def test_derived_door_sets_follow_exact_gate_linkage(hass: Any) -> None:
    """Only exact location_id links a UGT gate; names and ordinary doors never do."""
    client = _client(
        doors=[
            _door("door-gate", is_bind_hub=True),
            _door("door-plain", is_bind_hub=True),
            _door("door-named", full_name="UGT Gate door-gate", is_bind_hub=True),
        ],
        devices=[
            _device("gate-hub", type="UGT", location_id="door-gate"),
            _device("reader", type="UA-G2-PRO", location_id="door-plain"),
            _device("gate-by-name", type="UA-G2-PRO", full_name="door-named"),
            _device("orphan-gate", type="UGT", location_id="missing-door"),
        ],
    )
    coordinator = await _ready(hass, client)

    assert coordinator.gate_door_ids == frozenset({"door-gate"})
    assert coordinator.lock_door_ids == frozenset({"door-plain", "door-named"})
    assert coordinator.unlock_door_ids == frozenset({"door-plain", "door-named"})
    assert coordinator.doorbell_device_ids == frozenset({"reader", "gate-by-name"})


async def test_unbound_door_is_not_unlockable_and_gate_is_not_a_lock(hass: Any) -> None:
    """A door without a hub bond cannot be unlocked remotely."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)

    assert "door-unbound" not in coordinator.unlock_door_ids
    assert "door-gate" not in coordinator.lock_door_ids
    assert "door-gate" not in coordinator.unlock_door_ids


async def test_403_on_one_feature_disables_only_that_feature(hass: Any) -> None:
    """A denied emergency read leaves doors and devices working."""
    client = _client(doors=DOORS, devices=DEVICES)
    client.emergency.get = AsyncMock(
        side_effect=UniFiAuthenticationError("Denied", status_code=403)
    )
    coordinator = await _ready(hass, client)

    assert coordinator.feature_available("doors")
    assert coordinator.feature_available("devices")
    assert not coordinator.feature_available("emergency")
    assert coordinator.data["emergency"] is None
    assert "door-gate" in coordinator.data["doors"]
    assert not coordinator.auth_failed


async def test_denied_feature_is_not_requested_again(hass: Any) -> None:
    """Once denied, a feature is skipped on later polls until the entry reloads."""
    client = _client(doors=DOORS)
    client.devices.get_all = AsyncMock(
        side_effect=UniFiResponseError("Not found", status_code=404)
    )
    coordinator = await _ready(hass, client)
    assert not coordinator.feature_available("devices")

    await coordinator.async_refresh()

    assert client.devices.get_all.await_count == 1


async def test_404_on_doors_denies_doors_without_failing_refresh(hass: Any) -> None:
    """A console without the doors endpoint still refreshes the other features."""
    client = _client(devices=DEVICES)
    client.doors.get_all = AsyncMock(
        side_effect=UniFiResponseError("Not found", status_code=404)
    )
    coordinator = await _ready(hass, client)

    assert not coordinator.feature_available("doors")
    assert coordinator.data["doors"] == {}
    assert coordinator.feature_available("devices")


async def test_401_expires_access_without_reauth(hass: Any) -> None:
    """A rejected token stops polling and websocket, and never starts reauth."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    client.doors.get_all = AsyncMock(
        side_effect=UniFiAuthenticationError("Rejected", status_code=401)
    )

    await coordinator.async_refresh()

    assert coordinator.auth_failed
    assert coordinator.update_interval is None
    assert coordinator.data == {"doors": {}, "devices": {}, "emergency": None}
    assert coordinator.last_update_success
    assert not coordinator.feature_available("doors")


async def test_auth_failure_at_first_refresh_is_not_a_reauth(hass: Any) -> None:
    """The first refresh returns normally so the parent can read auth_failed."""
    client = _client()
    client.doors.get_all = AsyncMock(
        side_effect=UniFiAuthenticationError("Rejected", status_code=401)
    )
    coordinator = UnifiInsightsAccessCoordinator(hass, _entry(hass), client)
    _shutdown_with_hass(hass, coordinator)

    await coordinator.async_refresh()

    assert coordinator.auth_failed


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_socket_failure_publishes_availability_without_waiting_for_poll(
    hass: Any, status: int
) -> None:
    """Entities receive permanent socket failures even when polling stops."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    available: list[bool] = []
    remove_listener = coordinator.async_add_listener(
        lambda: available.append(coordinator.feature_available("notifications"))
    )
    try:
        coordinator._handle_socket_stop(status)
        await hass.async_block_till_done()
        assert available == [False]
        if status == 401:
            await coordinator.async_refresh()
            client.doors.get_all.assert_awaited_once()
            client.devices.get_all.assert_awaited_once()
            client.emergency.get.assert_awaited_once()
    finally:
        remove_listener()


@pytest.mark.parametrize(
    "error",
    [
        UniFiConnectionError("Unreachable"),
        UniFiRateLimitError("Slow down", status_code=429, retry_after=5),
        UniFiResponseError("Server error", status_code=HTTPStatus.BAD_GATEWAY),
    ],
)
async def test_transient_failure_keeps_last_snapshot(
    hass: Any, error: Exception
) -> None:
    """Connection, rate-limit and server failures keep the previous data."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    client.doors.get_all = AsyncMock(side_effect=error)

    await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert not coordinator.auth_failed
    assert "door-gate" in coordinator.data["doors"]


async def test_emergency_403_is_not_a_transient_failure(hass: Any) -> None:
    """Failures other than 403 and 404 on a feature still fail the refresh."""
    client = _client(doors=DOORS)
    client.emergency.get = AsyncMock(
        side_effect=UniFiResponseError(
            "Boom", status_code=HTTPStatus.INTERNAL_SERVER_ERROR
        )
    )
    coordinator = UnifiInsightsAccessCoordinator(hass, _entry(hass), client)
    _shutdown_with_hass(hass, coordinator)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_unexpected_error_propagates_unwrapped(hass: Any) -> None:
    """An unclassified error propagates unwrapped, not as UpdateFailed."""
    client = _client()
    client.doors.get_all = AsyncMock(side_effect=RuntimeError("unexpected"))
    coordinator = UnifiInsightsAccessCoordinator(hass, _entry(hass), client)
    _shutdown_with_hass(hass, coordinator)

    with pytest.raises(RuntimeError):
        await coordinator._async_update_data()


async def test_unlock_requires_bound_non_gate_door(hass: Any) -> None:
    """Unlock is refused for unbound doors and for gate doors, before any request."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)

    for door_id in ("door-unbound", "door-gate"):
        with pytest.raises(HomeAssistantError) as err:
            await coordinator.async_unlock_door(door_id)
        assert err.value.translation_key == "door_not_bound"
    client.doors.unlock.assert_not_awaited()


async def test_unlock_sends_remote_unlock_then_refreshes(hass: Any) -> None:
    """A successful unlock is followed by an authoritative refresh, not local state."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    client.doors.get_all.reset_mock()

    await coordinator.async_unlock_door("door-plain")

    client.doors.unlock.assert_awaited_once_with("door-plain")
    client.doors.get_all.assert_awaited_once()


async def test_unlock_rejection_is_translated_and_leaves_state(hass: Any) -> None:
    """A rejected unlock raises a translated error and does not change data."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    before = dict(coordinator.data["doors"])
    client.doors.unlock = AsyncMock(
        side_effect=UniFiResponseError("Rejected", status_code=400)
    )

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_unlock_door("door-plain")

    assert err.value.translation_key == "unlock_failed"
    assert coordinator.data["doors"] == before


async def test_unlock_403_is_permission_denied(hass: Any) -> None:
    """A 403 on a command names the missing permission, not the cause of the denial."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    client.doors.unlock = AsyncMock(
        side_effect=UniFiAuthenticationError("Denied", status_code=403)
    )

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_unlock_door("door-plain")

    assert err.value.translation_key == "permission_denied"
    assert not coordinator.auth_failed


async def test_command_401_expires_access(hass: Any) -> None:
    """A rejected token during a command expires Access as a poll would."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    client.doors.unlock = AsyncMock(
        side_effect=UniFiAuthenticationError("Rejected", status_code=401)
    )

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_unlock_door("door-plain")

    assert err.value.translation_key == "access_auth_failed"
    assert coordinator.auth_failed


async def test_commands_are_refused_after_expiry(hass: Any) -> None:
    """Once expired, every command raises without calling the console."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    coordinator.auth_failed = True

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_unlock_door("door-plain")

    assert err.value.translation_key == "access_auth_failed"
    client.doors.unlock.assert_not_awaited()


async def test_connection_error_on_command_is_unreachable(hass: Any) -> None:
    """A transport failure during a command maps to the unreachable key."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    client.doors.unlock = AsyncMock(side_effect=UniFiConnectionError("Down"))

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_unlock_door("door-plain")

    assert err.value.translation_key == "access_unreachable"


async def test_lock_uses_reset_rule_never_keep_lock(hass: Any) -> None:
    """Lock sends the documented reset rule and reads the relay back."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)

    await coordinator.async_lock_door("door-plain")

    client.doors.set_lock_rule.assert_awaited_once_with("door-plain", rule_type="reset")


async def test_lock_refuses_gate_door(hass: Any) -> None:
    """A gate door has no lock entity, so the coordinator refuses to lock it."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_lock_door("door-gate")

    assert err.value.translation_key == "door_not_lockable"
    client.doors.set_lock_rule.assert_not_awaited()


async def test_lock_rule_404_is_unsupported(hass: Any) -> None:
    """A console too old for lock rules gets the unsupported message."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    client.doors.set_lock_rule = AsyncMock(
        side_effect=UniFiResponseError("Not found", status_code=404)
    )

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_lock_door("door-plain")

    assert err.value.translation_key == "lock_rule_unsupported"


async def test_lock_other_response_error_is_lock_failed(hass: Any) -> None:
    """Other rejections while locking are reported as a failed lock."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    client.doors.set_lock_rule = AsyncMock(
        side_effect=UniFiResponseError("Bad", status_code=422)
    )

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_lock_door("door-plain")

    assert err.value.translation_key == "lock_failed"


async def test_lock_unexpected_api_error_is_lock_failed(hass: Any) -> None:
    """A non-response API error while locking is reported as a failed lock."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    client.doors.set_lock_rule = AsyncMock(side_effect=UniFiValidationError("Bad"))

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_lock_door("door-plain")

    assert err.value.translation_key == "lock_failed"


async def test_gate_command_sends_control_query_on_gate_only(hass: Any) -> None:
    """open, close and stop reach the gate door; an ordinary door is refused."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)

    for command in ("open", "close", "stop"):
        await coordinator.async_gate_command("door-gate", command)
        client.doors.unlock.assert_awaited_with("door-gate", control_cmd=command)

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_gate_command("door-plain", "open")
    assert err.value.translation_key == "gate_command_unsupported"
    assert client.doors.unlock.await_count == 3


async def test_gate_command_rejects_unknown_command(hass: Any) -> None:
    """Only open, close and stop are sent; anything else is a validation error."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_gate_command("door-gate", "sideways")  # type: ignore[arg-type]

    assert err.value.translation_key == "gate_command_invalid"
    client.doors.unlock.assert_not_awaited()


async def test_gate_command_console_refusal_is_rejected(hass: Any) -> None:
    """A console that refuses gate control raises gate_command_rejected."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    client.doors.unlock = AsyncMock(
        side_effect=UniFiResponseError("Refused", status_code=400)
    )

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_gate_command("door-gate", "open")

    assert err.value.translation_key == "gate_command_rejected"
    assert coordinator.data["doors"]["door-gate"]["position"] == "close"


async def test_gate_command_unknown_error_is_unreachable(hass: Any) -> None:
    """A gate command whose transport fails names the unreachable console."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    client.doors.unlock = AsyncMock(side_effect=UniFiConnectionError("Down"))

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_gate_command("door-gate", "stop")

    assert err.value.translation_key == "access_unreachable"


def test_emergency_target_clears_other_mode_on_enable() -> None:
    """Enabling one mode clears the other."""
    assert emergency_target(
        "lockdown", enabled=True, lockdown=False, evacuation=True
    ) == (
        True,
        False,
    )
    assert emergency_target(
        "evacuation", enabled=True, lockdown=True, evacuation=False
    ) == (False, True)


def test_emergency_target_keeps_other_mode_on_disable() -> None:
    """Disabling one mode keeps the other at its reported value."""
    assert emergency_target(
        "lockdown", enabled=False, lockdown=True, evacuation=True
    ) == (
        False,
        True,
    )
    assert emergency_target(
        "evacuation", enabled=False, lockdown=True, evacuation=True
    ) == (True, False)


async def test_emergency_mode_reads_writes_and_reads_back(hass: Any) -> None:
    """Read before write, write the computed pair, then read back to confirm."""
    client = _client(
        doors=DOORS,
        emergency=AccessEmergencyStatus.model_validate(
            {"lockdown": False, "evacuation": True}
        ),
    )
    coordinator = await _ready(hass, client)
    readback = AccessEmergencyStatus.model_validate(
        {"lockdown": True, "evacuation": False}
    )
    client.emergency.get = AsyncMock(
        side_effect=[
            AccessEmergencyStatus.model_validate(
                {"lockdown": False, "evacuation": True}
            ),
            readback,
            readback,
        ]
    )

    await coordinator.async_set_emergency_mode("lockdown", enabled=True)

    client.emergency.set.assert_awaited_once_with(lockdown=True, evacuation=False)
    assert client.emergency.get.await_count == 3
    assert coordinator.data["emergency"] == {"lockdown": True, "evacuation": False}


async def test_emergency_mode_unknown_current_state_refuses(hass: Any) -> None:
    """Without a known current pair the coordinator will not compute a target."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    client.emergency.get = AsyncMock(
        return_value=AccessEmergencyStatus.model_validate({})
    )

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_set_emergency_mode("evacuation", enabled=True)

    assert err.value.translation_key == "emergency_state_unknown"
    client.emergency.set.assert_not_awaited()


async def test_emergency_mode_not_applied_is_reported(hass: Any) -> None:
    """If the console reads back a different pair, the error says so."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    client.emergency.get = AsyncMock(
        return_value=AccessEmergencyStatus.model_validate(
            {"lockdown": False, "evacuation": False}
        )
    )

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_set_emergency_mode("lockdown", enabled=True)

    assert err.value.translation_key == "emergency_not_applied"


async def test_emergency_write_failure_is_translated(hass: Any) -> None:
    """A failed emergency write raises a translated error."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    client.emergency.set = AsyncMock(
        side_effect=UniFiResponseError("Rejected", status_code=400)
    )

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_set_emergency_mode("lockdown", enabled=True)

    assert err.value.translation_key == "emergency_failed"


async def test_emergency_mode_is_serialized(hass: Any) -> None:
    """Concurrent changes run read, write, read-back as one unit, never interleaved."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    console = {"lockdown": False, "evacuation": False}
    order: list[str] = []

    async def _get() -> AccessEmergencyStatus:
        order.append("get")
        await asyncio.sleep(0)
        return AccessEmergencyStatus.model_validate(dict(console))

    async def _set(*, lockdown: bool, evacuation: bool) -> None:
        order.append("set")
        console.update(lockdown=lockdown, evacuation=evacuation)

    client.emergency.get = AsyncMock(side_effect=_get)
    client.emergency.set = AsyncMock(side_effect=_set)

    await asyncio.gather(
        coordinator.async_set_emergency_mode("lockdown", enabled=True),
        coordinator.async_set_emergency_mode("evacuation", enabled=True),
    )

    # Each command reads, writes, reads back, then refreshes the state.
    assert order == ["get", "set", "get", "get"] * 2
    assert console == {"lockdown": False, "evacuation": True}
    assert coordinator.data["emergency"] == {"lockdown": False, "evacuation": True}


async def test_user_pin_rejects_non_digits_before_any_call(hass: Any) -> None:
    """A PIN that is not digits only is refused locally; the console is not called."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)

    for pin in ("", "12ab", "\uff11\uff12\uff13", " 1234"):
        with pytest.raises(HomeAssistantError) as err:
            await coordinator.async_set_user_pin("user-1", pin)
        assert err.value.translation_key == "pin_invalid_format"
    client.users.set_pin.assert_not_awaited()


async def test_user_pin_success_assigns_digits(hass: Any) -> None:
    """A digits-only PIN is sent to the assign endpoint."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)

    await coordinator.async_set_user_pin("user-1", "483920")

    client.users.set_pin.assert_awaited_once_with("user-1", "483920")


async def test_user_pin_length_rejection_is_validation_error(hass: Any) -> None:
    """The console's length rejection names the requirement without echoing the PIN."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    client.users.set_pin = AsyncMock(
        side_effect=UniFiResponseError(
            "Length invalid",
            status_code=400,
            api_error_code="CODE_CREDS_PIN_CODE_CREDS_LENGTH_INVALID",
        )
    )

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_set_user_pin("user-1", "9")

    assert err.value.translation_key == "pin_rejected"
    assert "9" not in str(err.value)


async def test_user_pin_other_api_error_is_assign_failed(hass: Any) -> None:
    """Other PIN rejections map to the generic assign failure."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    client.users.set_pin = AsyncMock(
        side_effect=UniFiResponseError("Other", status_code=409, api_error_code="X")
    )

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_set_user_pin("user-1", "483920")

    assert err.value.translation_key == "pin_assign_failed"


async def test_user_pin_client_validation_error_is_invalid_format(hass: Any) -> None:
    """A client-side PIN refusal is reported as an invalid format."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    client.users.set_pin = AsyncMock(side_effect=UniFiValidationError("PIN digits"))

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_set_user_pin("user-1", "483920")

    assert err.value.translation_key == "pin_invalid_format"


async def test_user_pin_unexpected_error_is_assign_failed(hass: Any) -> None:
    """An API error that is neither transport nor response is the assign failure."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    client.users.set_pin = AsyncMock(side_effect=UniFiError("Unexpected"))

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_set_user_pin("user-1", "483920")

    assert err.value.translation_key == "pin_assign_failed"


async def test_remove_pin_and_status_paths(hass: Any) -> None:
    """PIN removal and user status changes call their endpoints and map errors."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)

    await coordinator.async_remove_user_pin("user-1")
    client.users.remove_pin.assert_awaited_once_with("user-1")
    await coordinator.async_set_user_status("user-1", "DEACTIVATED")
    client.users.set_status.assert_awaited_once_with("user-1", "DEACTIVATED")

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_set_user_status("user-1", "PENDING")
    assert err.value.translation_key == "status_invalid"

    client.users.remove_pin = AsyncMock(side_effect=UniFiError("Unexpected"))
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_remove_user_pin("user-1")
    assert err.value.translation_key == "pin_remove_failed"

    client.users.set_status = AsyncMock(side_effect=UniFiError("Unexpected"))
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_set_user_status("user-1", "ACTIVE")
    assert err.value.translation_key == "status_update_failed"


def test_resolve_namespace_uses_console_id_only(hass: Any) -> None:
    """The namespace is the canonical console id, never the entry id or a credential."""
    entry = MockConfigEntry(domain=DOMAIN, data=dict(ENTRY_DATA))
    assert resolve_access_namespace(entry) == NAMESPACE


def test_resolve_namespace_refuses_missing_or_credential_identity() -> None:
    """No console id, or one equal to the API key or Access token, has no namespace."""
    missing = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "x"})
    assert resolve_access_namespace(missing) is None

    token_equal = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSOLE_ID: "access-token-value",
            CONF_API_KEY: "api-key-value",
            CONF_ACCESS_TOKEN: "access-token-value",
        },
    )
    assert resolve_access_namespace(token_equal) is None


async def test_namespace_is_none_without_valid_console_id(hass: Any) -> None:
    """Without a namespace, the coordinator still polls but exposes no identity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "api-key-value", CONF_ACCESS_TOKEN: "access-token-value"},
    )
    entry.add_to_hass(hass)
    coordinator = await _ready(hass, _client(doors=DOORS), entry)

    assert coordinator.namespace is None
    assert coordinator.feature_available("doors")


async def test_webhook_active_flag_notifies_listeners(hass: Any) -> None:
    """Turning the webhook on or off updates listeners so result entities discover."""
    coordinator = await _ready(hass, _client(doors=DOORS))
    listener = MagicMock()
    coordinator.async_add_listener(listener)

    coordinator.async_set_webhook_active(active=True)
    assert coordinator.webhook_active
    listener.assert_called()

    coordinator.async_set_webhook_active(active=False)
    assert not coordinator.webhook_active


async def test_dispatch_access_result_sends_only_documented_payload(hass: Any) -> None:
    """A verified result is delivered on the entry-scoped result signal."""
    entry = _entry(hass)
    received: list[dict[str, Any]] = []

    async_dispatcher_connect(
        hass,
        access_result_signal(entry.entry_id),
        received.append,
    )
    async_dispatch_access_result(
        hass,
        entry.entry_id,
        {"door_id": "door-plain", "result": "granted", "authentication": "nfc"},
    )
    await hass.async_block_till_done()

    assert received == [
        {"door_id": "door-plain", "result": "granted", "authentication": "nfc"}
    ]
    assert access_doorbell_signal(entry.entry_id) != access_result_signal(
        entry.entry_id
    )


async def test_websocket_start_is_gated_and_idempotent(hass: Any) -> None:
    """The socket starts once, only while the devices feature is available."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    started = asyncio.Event()

    async def _run(*_args: Any, **_kwargs: Any) -> None:
        started.set()
        await asyncio.Event().wait()

    with patch(
        "custom_components.unifi_insights.coordinators.access.AccessNotificationSocket"
    ) as socket_cls:
        socket_cls.return_value.run = _run
        await coordinator.async_start_websocket()
        await coordinator.async_start_websocket()
        await started.wait()
        socket_cls.assert_called_once_with(client)
        await coordinator.async_stop_websocket()
        await coordinator.async_stop_websocket()
    assert coordinator._ws_task is None


async def test_websocket_does_not_start_when_devices_denied(hass: Any) -> None:
    """Without device permission the notification socket is never started."""
    client = _client(doors=DOORS)
    client.devices.get_all = AsyncMock(
        side_effect=UniFiAuthenticationError("Denied", status_code=403)
    )
    coordinator = await _ready(hass, client)

    with patch(
        "custom_components.unifi_insights.coordinators.access.AccessNotificationSocket"
    ) as socket_cls:
        await coordinator.async_start_websocket()

    socket_cls.assert_not_called()


async def test_websocket_does_not_start_after_expiry(hass: Any) -> None:
    """An expired token never starts the socket."""
    coordinator = await _ready(hass, _client(doors=DOORS, devices=DEVICES))
    coordinator.auth_failed = True

    with patch(
        "custom_components.unifi_insights.coordinators.access.AccessNotificationSocket"
    ) as socket_cls:
        await coordinator.async_start_websocket()

    socket_cls.assert_not_called()


async def test_stop_websocket_without_start_is_safe(hass: Any) -> None:
    """Stopping before any start, and twice, does nothing."""
    coordinator = await _ready(hass, _client(doors=DOORS))

    await coordinator.async_stop_websocket()
    await coordinator.async_stop_websocket()

    assert coordinator._ws_task is None


async def test_remote_view_frame_dispatches_ring_without_storing_it(hass: Any) -> None:
    """A ring is delivered with only the documented keys and is not kept in data."""
    entry = _entry(hass)
    coordinator = await _ready(hass, _client(doors=DOORS, devices=DEVICES), entry)
    received: list[dict[str, Any]] = []

    async_dispatcher_connect(
        hass, access_doorbell_signal(entry.entry_id), received.append
    )

    coordinator._handle_frame(
        {
            "event": "access.remote_view",
            "data": {
                "device_id": "reader",
                "device_name": "Front reader",
                "door_name": "Front",
                "device_type": "UA-G2-PRO",
                "request_id": "secret-request-id",
                "create_time": 1694771479,
            },
        }
    )
    await hass.async_block_till_done()

    assert received == [
        {
            "device_id": "reader",
            "device_name": "Front reader",
            "door_name": "Front",
            "device_type": "UA-G2-PRO",
        }
    ]
    assert "secret-request-id" not in repr(coordinator.data)


@pytest.mark.parametrize(
    "frame",
    [
        {"event": "access.remote_view", "data": "not-a-dict"},
        {"event": "access.remote_view", "data": {"device_id": ""}},
        {"event": "access.remote_view", "data": {"device_id": None}},
        {"event": "access.remote_view"},
        {"event": "access.unknown", "data": {"device_id": "reader"}},
    ],
)
async def test_unusable_frames_are_dropped(hass: Any, frame: dict[str, Any]) -> None:
    """Frames without a usable device id or a known event never dispatch."""
    entry = _entry(hass)
    coordinator = await _ready(hass, _client(doors=DOORS), entry)
    received: list[dict[str, Any]] = []

    async_dispatcher_connect(
        hass, access_doorbell_signal(entry.entry_id), received.append
    )

    coordinator._handle_frame(frame)
    await hass.async_block_till_done()

    assert received == []


async def test_status_frame_schedules_one_debounced_refresh(hass: Any) -> None:
    """Repeated status frames collapse into one refresh after the debounce window."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    client.doors.get_all.reset_mock()

    coordinator._handle_frame({"event": "access.remote_view.change", "data": {}})
    coordinator._handle_frame({"event": "access.remote_view.change", "data": {}})
    assert coordinator._debounce_cancel is not None

    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=ACCESS_DEBOUNCE_SECONDS + 1),
    )
    await hass.async_block_till_done()

    assert coordinator._debounce_cancel is None
    client.doors.get_all.assert_awaited()


async def test_stop_cancels_pending_debounce(hass: Any) -> None:
    """Stopping the socket cancels a refresh that was waiting on the debounce."""
    coordinator = await _ready(hass, _client(doors=DOORS))
    coordinator._handle_frame({"event": "access.remote_view.change", "data": {}})
    assert coordinator._debounce_cancel is not None

    await coordinator.async_stop_websocket()

    assert coordinator._debounce_cancel is None


async def test_refresh_with_no_frames_leaves_debounce_clear(hass: Any) -> None:
    """Without status frames there is no pending refresh."""
    coordinator = await _ready(hass, _client(doors=DOORS))

    assert coordinator._debounce_cancel is None
    assert set(coordinator.data["doors"]) == {"door-gate", "door-plain", "door-unbound"}


def _blocking_doors_read(client: MagicMock) -> tuple[asyncio.Queue[None], list[bool]]:
    """Make the doors read wait until cancelled; report each entry and cancellation."""
    started: asyncio.Queue[None] = asyncio.Queue()
    cancelled: list[bool] = []

    async def _blocked_read() -> None:
        started.put_nowait(None)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    client.doors.get_all.side_effect = _blocked_read
    return started, cancelled


def _fire_debounce(hass: Any) -> None:
    """Fire the debounce timer through HA's native timer, past its window."""
    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=ACCESS_DEBOUNCE_SECONDS + 1),
    )


async def test_stop_settles_debounced_refresh_blocked_in_the_endpoint(
    hass: Any,
) -> None:
    """Stop cancels a debounced refresh waiting on the console and waits for it."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    started, cancelled = _blocking_doors_read(client)
    snapshot = coordinator.data

    coordinator._handle_frame({"event": "access.remote_view.change", "data": {}})
    _fire_debounce(hass)
    await started.get()

    await coordinator.async_stop_websocket()
    await coordinator.async_stop_websocket()

    assert cancelled == [True]
    assert coordinator.data is snapshot


async def test_overlapping_debounced_refreshes_are_all_cancelled(hass: Any) -> None:
    """A status burst longer than one debounce window leaves no refresh running.

    Home Assistant serializes refreshes on one lock, so the second refresh waits
    behind the blocked first. Stop must cancel both, not only the newest, and
    the queued one must never reach the endpoint after stop.
    """
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    started, cancelled = _blocking_doors_read(client)

    coordinator._handle_frame({"event": "access.remote_view.change", "data": {}})
    _fire_debounce(hass)
    await started.get()
    coordinator._handle_frame({"event": "access.remote_view.change", "data": {}})
    _fire_debounce(hass)

    await coordinator.async_stop_websocket()

    assert cancelled == [True]
    assert started.empty()


async def test_unauthorized_debounced_refresh_expires_without_awaiting_itself(
    hass: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """A 401 that arrives after the refresh yields expires Access without self-awaiting.

    The reply is gated, so the refresh is registered as a task before the 401.
    """
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    started: asyncio.Queue[None] = asyncio.Queue()
    release = asyncio.Event()

    rejected = UniFiAuthenticationError("Rejected", status_code=401)

    async def _rejected_read() -> None:
        started.put_nowait(None)
        await release.wait()
        raise rejected

    client.doors.get_all.side_effect = _rejected_read
    coordinator._handle_frame({"event": "access.remote_view.change", "data": {}})
    _fire_debounce(hass)
    await started.get()

    with caplog.at_level(logging.WARNING):
        release.set()
        async with asyncio.timeout(5):
            await hass.async_block_till_done(wait_background_tasks=True)

    assert coordinator.auth_failed
    assert coordinator.update_interval is None
    assert caplog.text.count("token was rejected") == 1


async def test_refresh_handles_client_models_as_attributes(hass: Any) -> None:
    """Client models are read through attributes, including undocumented fields."""
    client = _client(devices=[_device("gate-hub", type="UGT", location_id="door-gate")])
    client.doors.get_all = AsyncMock(
        return_value=[_door("door-gate", is_bind_hub=True, door_position_status="open")]
    )
    coordinator = await _ready(hass, client)

    assert coordinator.gate_door_ids == frozenset({"door-gate"})
    assert coordinator.data["doors"]["door-gate"]["position"] == "open"


async def test_every_translated_error_names_the_integration_and_has_a_message(
    hass: Any,
) -> None:
    """Each raised key belongs to the domain and has a non-empty, plain message."""

    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    failures = [
        coordinator.async_unlock_door("door-unbound"),
        coordinator.async_lock_door("door-gate"),
        coordinator.async_gate_command("door-plain", "open"),
        coordinator.async_gate_command("door-gate", "sideways"),  # type: ignore[arg-type]
        coordinator.async_set_user_pin("user-1", "x"),
        coordinator.async_set_user_status("user-1", "PENDING"),
    ]
    keys: set[str] = set()
    for failure in failures:
        with pytest.raises(HomeAssistantError) as err:
            await failure
        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key
        keys.add(err.value.translation_key)

    translations = json.loads(
        (
            Path(access_module.__file__).parent.parent / "translations" / "en.json"
        ).read_text()
    )
    exceptions = translations["exceptions"]
    for key in keys:
        message = exceptions[key]["message"]
        assert message.strip()
        assert "{" not in message


async def test_user_commands_refresh_after_success(hass: Any) -> None:
    """Each successful user command reads the authoritative state back."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)

    for command in (
        coordinator.async_set_user_pin("user-1", "483920"),
        coordinator.async_remove_user_pin("user-1"),
        coordinator.async_set_user_status("user-1", "ACTIVE"),
    ):
        client.doors.get_all.reset_mock()
        await command
        client.doors.get_all.assert_awaited_once()


@pytest.mark.parametrize(
    ("call", "method"),
    [
        (lambda c: c.async_set_user_pin("user-1", "483920"), "set_pin"),
        (lambda c: c.async_remove_user_pin("user-1"), "remove_pin"),
        (lambda c: c.async_set_user_status("user-1", "ACTIVE"), "set_status"),
    ],
)
async def test_unknown_user_is_a_clean_validation_error(
    hass: Any, call: Any, method: str
) -> None:
    """A 404 names the unknown user and carries no console detail as its cause."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    getattr(client.users, method).side_effect = UniFiResponseError(
        "Absent 404 body", status_code=404
    )

    with pytest.raises(ServiceValidationError) as err:
        await call(coordinator)

    assert err.value.translation_key == "access_user_not_found"
    assert err.value.translation_domain == DOMAIN
    assert err.value.__cause__ is None
    assert "404" not in str(err.value)


async def test_command_401_expires_access_once_with_one_warning(
    hass: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """A rejected token during a command expires Access with a single warning."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    client.users.set_status = AsyncMock(
        side_effect=UniFiAuthenticationError("Rejected", status_code=401)
    )

    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            with pytest.raises(HomeAssistantError):
                await coordinator.async_set_user_status("user-1", "ACTIVE")

    assert coordinator.auth_failed
    assert coordinator.update_interval is None
    assert caplog.text.count("token was rejected") == 1


async def test_socket_403_disables_only_notifications(hass: Any) -> None:
    """A permanent 403 on the notification endpoint leaves doors and devices working."""
    coordinator = await _ready(hass, _client(doors=DOORS, devices=DEVICES))
    coordinator._ws_task = None

    coordinator._handle_socket_stop(403)

    assert not coordinator.feature_available("notifications")
    assert coordinator.feature_available("doors")
    assert coordinator.feature_available("devices")
    assert not coordinator.auth_failed


async def test_socket_404_disables_only_notifications(hass: Any) -> None:
    """A permanent 404 on the notification endpoint is the same capability answer."""
    coordinator = await _ready(hass, _client(doors=DOORS, devices=DEVICES))

    coordinator._handle_socket_stop(404)

    assert not coordinator.feature_available("notifications")
    assert coordinator.feature_available("doors")


async def test_socket_401_expires_access_on_a_separate_task(
    hass: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """A socket 401 expires Access without awaiting the socket task that reported it."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    coordinator._ws_task = None

    with caplog.at_level(logging.WARNING):
        coordinator._handle_socket_stop(401)
        await hass.async_block_till_done()

    assert coordinator.auth_failed
    assert coordinator.update_interval is None
    assert caplog.text.count("token was rejected") == 1


async def test_disabled_notifications_block_websocket_start(hass: Any) -> None:
    """Once notifications are denied, starting the socket is a no-op."""
    coordinator = await _ready(hass, _client(doors=DOORS, devices=DEVICES))
    coordinator._handle_socket_stop(403)

    with patch(
        "custom_components.unifi_insights.coordinators.access.AccessNotificationSocket"
    ) as socket_cls:
        await coordinator.async_start_websocket()

    socket_cls.assert_not_called()


async def test_device_without_id_is_not_kept(hass: Any) -> None:
    """A device record with no id is skipped rather than keyed by an empty id."""
    client = _client(doors=DOORS, devices=[_device("")])
    coordinator = await _ready(hass, client)

    assert coordinator.data["devices"] == {}


async def test_expiry_warns_once_when_called_again(
    hass: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """A second expiry for the same token adds no second warning."""
    coordinator = await _ready(hass, _client(doors=DOORS))

    with caplog.at_level(logging.WARNING):
        await coordinator._async_expire(401)
        await coordinator._async_expire(401)

    assert caplog.text.count("token was rejected") == 1


async def test_remove_pin_server_error_is_pin_remove_failed(hass: Any) -> None:
    """A non-404 response error while removing a PIN keeps the specific message."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    client.users.remove_pin = AsyncMock(
        side_effect=UniFiResponseError("Server", status_code=500)
    )

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_remove_user_pin("user-1")

    assert err.value.translation_key == "pin_remove_failed"
    assert err.value.__cause__ is not None


async def test_status_server_error_is_status_update_failed(hass: Any) -> None:
    """A non-404 response error while changing status keeps the specific message."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    client.users.set_status = AsyncMock(
        side_effect=UniFiResponseError("Server", status_code=500)
    )

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_set_user_status("user-1", "ACTIVE")

    assert err.value.translation_key == "status_update_failed"


@pytest.mark.parametrize(
    "error",
    [
        UniFiAuthenticationError("Denied", status_code=403),
        UniFiResponseError("Not found", status_code=404),
    ],
)
async def test_denied_devices_refuse_every_door_command_without_http(
    hass: Any, error: Exception
) -> None:
    """With devices denied, no door is a lock, unlock or gate target, nothing sent."""
    client = _client(doors=DOORS)
    client.devices.get_all = AsyncMock(side_effect=error)
    coordinator = await _ready(hass, client)

    assert not coordinator.feature_available("devices")
    assert coordinator.gate_door_ids == frozenset()
    assert coordinator.lock_door_ids == frozenset()
    assert coordinator.unlock_door_ids == frozenset()

    with pytest.raises(HomeAssistantError) as unlock_err:
        await coordinator.async_unlock_door("door-plain")
    with pytest.raises(HomeAssistantError) as lock_err:
        await coordinator.async_lock_door("door-plain")
    with pytest.raises(HomeAssistantError) as gate_err:
        await coordinator.async_gate_command("door-gate", "open")

    assert unlock_err.value.translation_key == "door_not_bound"
    assert lock_err.value.translation_key == "door_not_lockable"
    assert gate_err.value.translation_key == "gate_command_unsupported"
    client.doors.unlock.assert_not_awaited()
    client.doors.set_lock_rule.assert_not_awaited()


async def test_known_gate_door_stays_out_of_lock_set_when_devices_denied(
    hass: Any,
) -> None:
    """A gate door is never classified as a plain lock or unlock target by default."""
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client)
    assert "door-gate" in coordinator.gate_door_ids

    client.devices.get_all = AsyncMock(
        side_effect=UniFiAuthenticationError("Denied", status_code=403)
    )
    await coordinator.async_refresh()

    assert "door-gate" not in coordinator.lock_door_ids
    assert "door-plain" not in coordinator.lock_door_ids
    assert coordinator.lock_door_ids == frozenset()
    assert coordinator.unlock_door_ids == frozenset()
    assert coordinator.gate_door_ids == frozenset()


async def test_empty_device_list_still_locks_ordinary_doors(hass: Any) -> None:
    """A successful devices read with no devices keeps ordinary doors lockable."""
    coordinator = await _ready(hass, _client(doors=DOORS, devices=[]))

    assert coordinator.feature_available("devices")
    assert coordinator.lock_door_ids == frozenset(
        {"door-gate", "door-plain", "door-unbound"}
    )
    assert coordinator.gate_door_ids == frozenset()


async def test_shutdown_stops_polling_and_refuses_later_refreshes(hass: Any) -> None:
    """Public shutdown is idempotent and a refresh after it never reads the console."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    reads = client.doors.get_all.await_count

    await coordinator.async_shutdown()
    await coordinator.async_shutdown()
    await coordinator.async_refresh()

    assert client.doors.get_all.await_count == reads


async def test_shutdown_cancels_an_in_flight_refresh_from_any_caller(hass: Any) -> None:
    """A refresh outside the debounce, such as a command readback, is cancelled too."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    started, cancelled = _blocking_doors_read(client)
    reads = client.doors.get_all.await_count

    refresh = hass.async_create_task(coordinator.async_refresh())
    await started.get()
    async with asyncio.timeout(5):
        await coordinator.async_shutdown()
        await refresh

    assert cancelled == [True]
    assert client.doors.get_all.await_count == reads + 1


async def test_expiry_cancels_a_queued_refresh_without_deadlock(
    hass: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """A 401 on the active refresh cancels a refresh queued behind it, once."""
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    started: asyncio.Queue[None] = asyncio.Queue()
    release = asyncio.Event()
    reads = client.doors.get_all.await_count

    rejected = UniFiAuthenticationError("Rejected", status_code=401)

    async def _rejected_read() -> None:
        started.put_nowait(None)
        await release.wait()
        raise rejected

    client.doors.get_all.side_effect = _rejected_read
    coordinator._handle_frame({"event": "access.remote_view.change", "data": {}})
    _fire_debounce(hass)
    await started.get()
    coordinator._handle_frame({"event": "access.remote_view.change", "data": {}})
    _fire_debounce(hass)

    with caplog.at_level(logging.WARNING):
        release.set()
        async with asyncio.timeout(5):
            await hass.async_block_till_done(wait_background_tasks=True)

    warnings = [r for r in caplog.records if "token was rejected" in r.getMessage()]
    assert coordinator.auth_failed
    assert client.doors.get_all.await_count == reads + 1
    assert len(warnings) == 1


async def test_untracked_refresh_queued_behind_active_read_never_reads_after_shutdown(
    hass: Any,
) -> None:
    """Shutdown cancels the active read; a queued refresh then starts no HTTP call.

    The queued refresh is an ordinary coordinator refresh, not a debounced one, so
    only the shutdown flag stops it. It waits on the lock behind the blocked read.
    """
    client = _client(doors=DOORS)
    coordinator = await _ready(hass, client)
    started, cancelled = _blocking_doors_read(client)
    reads = client.doors.get_all.await_count

    active = hass.async_create_task(coordinator.async_refresh())
    await started.get()
    queued = hass.async_create_task(coordinator.async_refresh())
    await asyncio.sleep(0)
    async with asyncio.timeout(5):
        await coordinator.async_shutdown()
        await active
        await queued

    assert cancelled == [True]
    assert client.doors.get_all.await_count == reads + 1
