# Copyright 2026 UniFi Insights contributors
"""Verify Access locks, gate covers, unlock buttons, emergency switches and helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from homeassistant.components.cover import CoverDeviceClass, CoverEntityFeature
from homeassistant.const import CONF_API_KEY
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiResponseError,
)
from unifi_official_api.access.models.emergency import (
    AccessEmergencyStatus,
)

from custom_components.unifi_insights import UnifiInsightsData
from custom_components.unifi_insights import button as button_platform
from custom_components.unifi_insights import cover as cover_platform
from custom_components.unifi_insights import lock as lock_platform
from custom_components.unifi_insights import switch as switch_platform
from custom_components.unifi_insights.access_entity import (
    UnifiAccessDoorEntity,
    access_coordinator_for,
    access_unique_id,
    async_discover_access_entities,
    console_device_info,
    door_device_info,
    doorbell_device_info,
)
from custom_components.unifi_insights.carrier_fabric_data import CarrierFabricData
from custom_components.unifi_insights.const import CONF_ACCESS_TOKEN, DOMAIN
from tests.test_access_coordinator import (
    DEVICES,
    DOORS,
    ENTRY_DATA,
    NAMESPACE,
    _client,
    _ready,
)

if TYPE_CHECKING:
    from custom_components.unifi_insights.coordinators.access import (
        UnifiInsightsAccessCoordinator,
    )


def _entry(hass: Any, data: dict[str, Any] | None = None) -> MockConfigEntry:
    """Return an entry carrying a UniFi Insights runtime data object."""
    entry = MockConfigEntry(domain=DOMAIN, data=data or dict(ENTRY_DATA))
    entry.add_to_hass(hass)
    entry.runtime_data = UnifiInsightsData(
        config_coordinator=MagicMock(name="config"),
        device_coordinator=MagicMock(name="device"),
        protect_coordinator=None,
        network_client=MagicMock(name="network"),
        protect_client=None,
    )
    return entry


async def _setup_access(
    hass: Any,
    coordinator: UnifiInsightsAccessCoordinator,
    entry: MockConfigEntry,
) -> None:
    """Attach the Access coordinator the way the parent setup does."""
    entry.runtime_data.access_coordinator = coordinator


def _collect() -> tuple[list[Any], MagicMock]:
    """Return an entity list and the add callback that fills it."""
    added: list[Any] = []
    add = MagicMock(side_effect=added.extend)
    return added, add


async def test_access_coordinator_for_ignores_other_entry_types(hass: Any) -> None:
    """A Carrier Fabric entry carries different runtime data and gets no Access."""
    entry = MockConfigEntry(domain=DOMAIN, data=dict(ENTRY_DATA))
    entry.add_to_hass(hass)
    entry.runtime_data = CarrierFabricData(client=MagicMock(), coordinator=MagicMock())

    assert access_coordinator_for(entry) is None


async def test_access_coordinator_for_none_when_access_is_absent(hass: Any) -> None:
    """An entry without an Access coordinator gets none."""
    entry = _entry(hass)

    assert access_coordinator_for(entry) is None


async def test_access_coordinator_for_returns_the_attached_coordinator(
    hass: Any,
) -> None:
    """The helper returns the coordinator attached to the entry."""
    entry = _entry(hass)
    coordinator = await _ready(hass, _client(doors=DOORS, devices=DEVICES), entry)
    await _setup_access(hass, coordinator, entry)

    assert access_coordinator_for(entry) is coordinator


async def test_identity_helpers_are_namespace_scoped() -> None:
    """Unique ids and device identifiers include the console namespace."""
    assert access_unique_id(NAMESPACE, "door-1", "lock") == (
        f"unifi_insights_access_{NAMESPACE}_door-1_lock"
    )
    assert door_device_info(NAMESPACE, "door-1", "Main")["identifiers"] == {
        (DOMAIN, f"access_door_{NAMESPACE}_door-1")
    }
    assert door_device_info(NAMESPACE, "door-1", None)["name"] == "Access door door-1"
    assert console_device_info(NAMESPACE)["identifiers"] == {
        (DOMAIN, f"access_console_{NAMESPACE}")
    }
    assert (
        doorbell_device_info(NAMESPACE, "reader", None)["name"]
        == "Access device reader"
    )
    assert doorbell_device_info(NAMESPACE, "reader", "Lobby")["name"] == "Lobby"


async def test_discovery_defers_without_namespace(hass: Any) -> None:
    """With no valid namespace nothing is added and no listener is registered."""
    entry = _entry(hass, {CONF_API_KEY: "key", CONF_ACCESS_TOKEN: "token"})
    coordinator = await _ready(hass, _client(doors=DOORS), entry)
    await _setup_access(hass, coordinator, entry)
    added, add = _collect()

    async_discover_access_entities(
        entry,
        coordinator,
        add,
        known_ids=set(),
        keys=lambda: ["door-plain"],
        make=lambda key: MagicMock(name=key),
    )
    await coordinator.async_refresh()

    add.assert_not_called()
    assert added == []


async def test_discovery_adds_new_keys_once_and_unsubscribes_on_unload(
    hass: Any,
) -> None:
    """Discovery adds each key once, then listens for later keys until unload."""
    entry = _entry(hass)
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client, entry)
    await _setup_access(hass, coordinator, entry)
    known: set[str] = set()
    current = {"keys": ["door-plain"]}
    added, add = _collect()

    async_discover_access_entities(
        entry,
        coordinator,
        add,
        known_ids=known,
        keys=lambda: current["keys"],
        make=lambda key: MagicMock(name=key),
    )
    assert len(added) == 1

    current["keys"] = ["door-plain", "door-unbound"]
    await coordinator.async_refresh()
    assert len(added) == 2
    await coordinator.async_refresh()
    assert len(added) == 2
    assert known == {"door-plain", "door-unbound"}


async def test_door_entity_is_unavailable_when_door_disappears(hass: Any) -> None:
    """An entity whose door left the data reports unavailable, not an error."""
    entry = _entry(hass)
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client, entry)
    await _setup_access(hass, coordinator, entry)
    added, add = _collect()
    await lock_platform.async_setup_entry(hass, entry, add)
    lock = next(e for e in added if e._door_id == "door-plain")
    assert lock.available

    client.doors.get_all = AsyncMock(return_value=[])
    await coordinator.async_refresh()

    assert not lock.available
    assert lock.is_locked is None


async def test_unavailable_when_feature_denied_or_last_update_failed(hass: Any) -> None:
    """Entities need a successful refresh and an undenied feature."""
    entry = _entry(hass)
    coordinator = await _ready(hass, _client(doors=DOORS, devices=DEVICES), entry)
    await _setup_access(hass, coordinator, entry)
    added, add = _collect()
    await lock_platform.async_setup_entry(hass, entry, add)
    lock = added[0]

    coordinator._denied.add("doors")
    assert not lock.available
    coordinator._denied.clear()
    coordinator.last_update_success = False
    assert not lock.available


async def test_lock_state_comes_from_relay_and_unknown_is_none(hass: Any) -> None:
    """Relay lock is locked, relay unlock is unlocked, anything else is unknown."""
    entry = _entry(hass)
    doors = [
        *DOORS,
        DOORS[0].model_copy(update={"id": "door-odd", "door_lock_relay_status": None}),
    ]
    coordinator = await _ready(hass, _client(doors=doors, devices=DEVICES), entry)
    await _setup_access(hass, coordinator, entry)
    added, add = _collect()

    await lock_platform.async_setup_entry(hass, entry, add)
    states = {entity._door_id: entity.is_locked for entity in added}

    assert states["door-plain"] is False
    assert states["door-odd"] is None
    assert "door-gate" not in states
    assert {e.unique_id for e in added} == {
        access_unique_id(NAMESPACE, door_id, "lock") for door_id in states
    }


async def test_lock_and_unlock_send_console_commands_only(hass: Any) -> None:
    """Lock sends the reset rule and unlock sends the remote unlock, nothing local."""
    entry = _entry(hass)
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client, entry)
    await _setup_access(hass, coordinator, entry)
    added, add = _collect()
    await lock_platform.async_setup_entry(hass, entry, add)
    lock = next(e for e in added if e._door_id == "door-plain")

    await lock.async_lock()
    client.doors.set_lock_rule.assert_awaited_once_with("door-plain", rule_type="reset")
    await lock.async_unlock()
    client.doors.unlock.assert_awaited_once_with("door-plain")


async def test_lock_command_failure_reaches_the_user(hass: Any) -> None:
    """A failed lock command raises a translated error and does not change state."""
    entry = _entry(hass)
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client, entry)
    await _setup_access(hass, coordinator, entry)
    added, add = _collect()
    await lock_platform.async_setup_entry(hass, entry, add)
    lock = added[0]
    before = lock.is_locked
    client.doors.set_lock_rule = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(HomeAssistantError):
        await lock.async_lock()

    assert lock.is_locked == before


async def test_gate_cover_maps_position_and_features(hass: Any) -> None:
    """A gate cover reports the sensor position, supports open, close and stop."""
    entry = _entry(hass)
    coordinator = await _ready(hass, _client(doors=DOORS, devices=DEVICES), entry)
    await _setup_access(hass, coordinator, entry)
    added, add = _collect()
    await cover_platform.async_setup_entry(hass, entry, add)

    assert [e._door_id for e in added] == ["door-gate"]
    cover = added[0]
    assert cover.device_class is CoverDeviceClass.GATE
    assert cover.supported_features == (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )
    assert cover.is_closed is True
    assert cover.is_opening is None
    assert cover.is_closing is None
    assert cover.available


async def test_gate_cover_without_position_sensor_is_unknown(hass: Any) -> None:
    """With no position sensor the cover does not guess open or closed."""
    entry = _entry(hass)
    gate_without_sensor = DOORS[0].model_copy(update={"door_position_status": None})
    coordinator = await _ready(
        hass, _client(doors=[gate_without_sensor, *DOORS[1:]], devices=DEVICES), entry
    )
    await _setup_access(hass, coordinator, entry)
    added, add = _collect()
    await cover_platform.async_setup_entry(hass, entry, add)

    assert added[0].is_closed is None


async def test_gate_cover_open_close_stop_send_control_query(hass: Any) -> None:
    """Each cover command reaches the gate door with the matching control command."""
    entry = _entry(hass)
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client, entry)
    await _setup_access(hass, coordinator, entry)
    added, add = _collect()
    await cover_platform.async_setup_entry(hass, entry, add)
    cover = added[0]

    await cover.async_open_cover()
    await cover.async_close_cover()
    await cover.async_stop_cover()

    assert client.doors.unlock.await_args_list[-3:] == [
        (("door-gate",), {"control_cmd": "open"}),
        (("door-gate",), {"control_cmd": "close"}),
        (("door-gate",), {"control_cmd": "stop"}),
    ]


async def test_gate_cover_is_absent_for_ordinary_doors(hass: Any) -> None:
    """A cover is never created for a door that a gate hub is not linked to."""
    entry = _entry(hass)
    coordinator = await _ready(hass, _client(doors=DOORS, devices=[]), entry)
    await _setup_access(hass, coordinator, entry)
    added, add = _collect()

    await cover_platform.async_setup_entry(hass, entry, add)

    assert added == []


async def test_gate_cover_rejects_command_on_ordinary_door(hass: Any) -> None:
    """An ordinary door cover that somehow exists still cannot send gate commands."""
    entry = _entry(hass)
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client, entry)
    await _setup_access(hass, coordinator, entry)
    cover = cover_platform.UnifiAccessGateCover(coordinator, "door-plain")

    assert not cover.available
    with pytest.raises(HomeAssistantError) as err:
        await cover.async_open_cover()
    assert err.value.translation_key == "gate_command_unsupported"
    assert err.value.translation_domain == DOMAIN
    assert client.doors.unlock.await_count == 0


async def test_unlock_button_only_for_bound_non_gate_doors(hass: Any) -> None:
    """Unbound and gate doors get no unlock button; bound plain doors do."""
    entry = _entry(hass)
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client, entry)
    await _setup_access(hass, coordinator, entry)
    added, add = _collect()

    button_platform.async_setup_access_buttons(entry, add)

    unlock_ids = {e._door_id for e in added if e.unique_id.endswith("_unlock")}
    assert unlock_ids == {"door-plain"}
    button = next(e for e in added if e._door_id == "door-plain")
    await button.async_press()
    client.doors.unlock.assert_awaited_once_with("door-plain")


async def test_unlock_button_refuses_unbound_door_directly(hass: Any) -> None:
    """A button for a door that stops being bound is unavailable."""
    entry = _entry(hass)
    coordinator = await _ready(hass, _client(doors=DOORS, devices=DEVICES), entry)
    await _setup_access(hass, coordinator, entry)
    button = button_platform.UnifiAccessUnlockButton(coordinator, "door-unbound")

    assert not button.available


async def test_emergency_switches_read_shared_state(hass: Any) -> None:
    """Both switches report the same console state, and unknown reports None."""
    entry = _entry(hass)
    client = _client(
        doors=DOORS,
        emergency=AccessEmergencyStatus.model_validate(
            {"lockdown": True, "evacuation": False}
        ),
    )
    coordinator = await _ready(hass, client, entry)
    await _setup_access(hass, coordinator, entry)
    added, add = _collect()

    switch_platform.async_setup_access_switches(entry, add)

    by_mode = {e._mode: e for e in added}
    assert set(by_mode) == {"lockdown", "evacuation"}
    assert by_mode["lockdown"].is_on is True
    assert by_mode["evacuation"].is_on is False
    assert by_mode["lockdown"].unique_id == access_unique_id(
        NAMESPACE, "emergency", "lockdown"
    )

    coordinator.data["emergency"] = None
    assert by_mode["lockdown"].is_on is None
    assert not by_mode["lockdown"].available


async def test_emergency_switch_turn_on_and_off_route_through_coordinator(
    hass: Any,
) -> None:
    """Turn on and off call the coordinator; the switch never sets its own state."""
    entry = _entry(hass)
    client = _client(
        doors=DOORS,
        emergency=AccessEmergencyStatus.model_validate(
            {"lockdown": False, "evacuation": False}
        ),
    )
    coordinator = await _ready(hass, client, entry)
    await _setup_access(hass, coordinator, entry)
    added, add = _collect()
    switch_platform.async_setup_access_switches(entry, add)
    lockdown = next(e for e in added if e._mode == "lockdown")
    coordinator.async_set_emergency_mode = AsyncMock()

    await lockdown.async_turn_on()
    await lockdown.async_turn_off()

    coordinator.async_set_emergency_mode.assert_has_awaits(
        [
            call("lockdown", enabled=True),
            call("lockdown", enabled=False),
        ]
    )


async def test_emergency_switches_absent_when_emergency_is_denied(hass: Any) -> None:
    """A console without emergency permission gets no emergency switches."""
    entry = _entry(hass)
    client = _client(doors=DOORS)
    client.emergency.get = AsyncMock(
        side_effect=UniFiAuthenticationError("Denied", status_code=403)
    )
    coordinator = await _ready(hass, client, entry)
    await _setup_access(hass, coordinator, entry)
    added, add = _collect()

    switch_platform.async_setup_access_switches(entry, add)

    assert added == []
    assert not coordinator.feature_available("emergency")


async def test_switch_setup_is_a_no_op_without_access(hass: Any) -> None:
    """An entry without Access adds no emergency switches."""
    entry = _entry(hass)
    added, add = _collect()

    switch_platform.async_setup_access_switches(entry, add)

    assert added == []


async def test_button_setup_is_a_no_op_without_access(hass: Any) -> None:
    """An entry without Access adds no unlock buttons."""
    entry = _entry(hass)
    added, add = _collect()

    button_platform.async_setup_access_buttons(entry, add)

    assert added == []


async def test_platforms_are_no_op_for_carrier_fabric_entries(hass: Any) -> None:
    """Carrier Fabric entries have no Access platforms and are not touched."""
    entry = MockConfigEntry(domain=DOMAIN, data=dict(ENTRY_DATA))
    entry.add_to_hass(hass)
    entry.runtime_data = CarrierFabricData(client=MagicMock(), coordinator=MagicMock())
    added, add = _collect()

    await lock_platform.async_setup_entry(hass, entry, add)
    await cover_platform.async_setup_entry(hass, entry, add)

    assert added == []


async def test_lock_entity_device_name_comes_from_the_door(hass: Any) -> None:
    """The door entity's device is named after the door the console reports."""
    entry = _entry(hass)
    named = DOORS[1].model_copy(update={"name": "Lobby door"})
    coordinator = await _ready(hass, _client(doors=[named], devices=[]), entry)
    await _setup_access(hass, coordinator, entry)
    lock = lock_platform.UnifiAccessDoorLock(coordinator, "door-plain")

    assert lock.device_info is not None
    assert lock.device_info["name"] == "Lobby door"


def test_unique_id_helper_is_pure() -> None:
    """The unique id helper joins parts in order without a coordinator."""
    assert access_unique_id("ns", "a", "b") == "unifi_insights_access_ns_a_b"
    assert switch_platform.EMERGENCY_MODES == ("lockdown", "evacuation")


async def test_cover_position_and_missing_door(hass: Any) -> None:
    """An open gate reports not closed; a gate whose door is gone reports unknown."""
    entry = _entry(hass)
    open_gate = DOORS[0].model_copy(update={"door_position_status": "open"})
    coordinator = await _ready(
        hass, _client(doors=[open_gate, *DOORS[1:]], devices=DEVICES), entry
    )
    cover = cover_platform.UnifiAccessGateCover(coordinator, "door-gate")

    assert cover.is_closed is False

    coordinator.data["doors"].pop("door-gate")
    assert cover.is_closed is None


async def test_lock_is_unknown_when_door_is_gone(hass: Any) -> None:
    """A lock whose door left the data reports an unknown lock state."""
    entry = _entry(hass)
    coordinator = await _ready(hass, _client(doors=DOORS, devices=DEVICES), entry)
    lock = lock_platform.UnifiAccessDoorLock(coordinator, "door-plain")

    coordinator.data["doors"].pop("door-plain")

    assert lock.is_locked is None


async def test_entity_requires_a_console_namespace(hass: Any) -> None:
    """Building an Access entity without a valid namespace is refused."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_API_KEY: "key", CONF_ACCESS_TOKEN: "token"}
    )
    entry.add_to_hass(hass)
    coordinator = await _ready(hass, _client(doors=DOORS, devices=DEVICES), entry)

    with pytest.raises(ValueError, match="namespace"):
        lock_platform.UnifiAccessDoorLock(coordinator, "door-plain")


async def test_lock_reports_locked_relay_as_locked(hass: Any) -> None:
    """A relay reported as lock means the door is locked."""
    entry = _entry(hass)
    locked_door = DOORS[1].model_copy(
        update={"id": "door-locked", "door_lock_relay_status": "lock"}
    )
    coordinator = await _ready(hass, _client(doors=[locked_door], devices=[]), entry)
    lock = lock_platform.UnifiAccessDoorLock(coordinator, "door-locked")

    assert lock.is_locked is True


@pytest.mark.parametrize(
    "error",
    [
        UniFiAuthenticationError("Denied", status_code=403),
        UniFiResponseError("Not found", status_code=404),
    ],
)
async def test_devices_denied_at_setup_creates_no_door_entities(
    hass: Any, error: Exception
) -> None:
    """With devices denied, no lock, unlock button or cover is created."""
    entry = _entry(hass)
    client = _client(doors=DOORS)
    client.devices.get_all = AsyncMock(side_effect=error)
    coordinator = await _ready(hass, client, entry)
    await _setup_access(hass, coordinator, entry)
    locks, add_locks = _collect()
    covers, add_covers = _collect()
    buttons, add_buttons = _collect()

    await lock_platform.async_setup_entry(hass, entry, add_locks)
    await cover_platform.async_setup_entry(hass, entry, add_covers)
    button_platform.async_setup_access_buttons(entry, add_buttons)

    assert locks == []
    assert covers == []
    assert buttons == []


async def test_denial_after_setup_makes_lock_button_and_cover_unavailable(
    hass: Any,
) -> None:
    """After devices are denied, existing entities go unavailable and send nothing."""
    entry = _entry(hass)
    client = _client(doors=DOORS, devices=DEVICES)
    coordinator = await _ready(hass, client, entry)
    await _setup_access(hass, coordinator, entry)
    locks, add_locks = _collect()
    covers, add_covers = _collect()
    buttons, add_buttons = _collect()
    await lock_platform.async_setup_entry(hass, entry, add_locks)
    await cover_platform.async_setup_entry(hass, entry, add_covers)
    button_platform.async_setup_access_buttons(entry, add_buttons)
    lock = next(e for e in locks if e._door_id == "door-plain")
    button = next(e for e in buttons if e._door_id == "door-plain")
    cover = covers[0]
    assert lock.available
    assert button.available
    assert cover.available

    client.devices.get_all = AsyncMock(
        side_effect=UniFiAuthenticationError("Denied", status_code=403)
    )
    await coordinator.async_refresh()

    assert not lock.available
    assert not button.available
    assert not cover.available
    with pytest.raises(HomeAssistantError):
        await lock.async_lock()
    with pytest.raises(HomeAssistantError):
        await lock.async_unlock()
    with pytest.raises(HomeAssistantError):
        await button.async_press()
    with pytest.raises(HomeAssistantError):
        await cover.async_open_cover()
    client.doors.set_lock_rule.assert_not_awaited()
    client.doors.unlock.assert_not_awaited()


async def test_door_base_entity_follows_door_presence(hass: Any) -> None:
    """The shared door base is available only while its door is in the data."""
    entry = _entry(hass)
    coordinator = await _ready(hass, _client(doors=DOORS, devices=[]), entry)
    entity = UnifiAccessDoorEntity(coordinator, "door-plain")

    assert entity.available
    coordinator.data["doors"].pop("door-plain")
    assert not entity.available
