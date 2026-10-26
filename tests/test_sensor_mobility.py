# Copyright 2026 UniFi Insights contributors
"""Tests for UniFi Mobility workspace and router entities."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, EntityCategory
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api import UniFiConnectionError

from custom_components.unifi_insights.binary_sensor import (
    async_setup_mobility_binary_sensors,
)
from custom_components.unifi_insights.const import (
    CONF_TRACK_WIFI_CLIENTS,
    CONF_TRACK_WIRED_CLIENTS,
    DOMAIN,
)
from custom_components.unifi_insights.coordinators.mobility import (
    UnifiInsightsMobilityCoordinator,
)
from custom_components.unifi_insights.device_tracker import (
    LOCATION_DESCRIPTION,
    UnifiMobilityLocationTracker,
)
from custom_components.unifi_insights.sensor import (
    ROUTER_SENSORS,
    WORKSPACE_SENSORS,
    UnifiMobilitySensor,
    UnifiMobilityWorkspaceSensor,
)
from custom_components.unifi_insights.service_targets import (
    _resolve_network_client_id,
    _resolve_network_device_id,
)
from tests.fixtures.assertions import require_not_none
from tests.fixtures.mobility_responses import (
    BRANCH_ROUTER_ID,
    DEVICE_SUMMARIES,
    OFFICE_ROUTER_DETAIL,
    OFFICE_ROUTER_ID,
    PENDING_WORKSPACE_ID,
    WORKSPACE_ID,
    mobility_client_mock,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, State

pytestmark = pytest.mark.usefixtures(
    "mock_network_client",
    "mock_protect_client",
    "mock_site_manager_client",
    "enable_custom_integrations",
)

_NEW_ROUTER_ID = "772a0622-a4bd-63f6-c938-668877662222"


def _serve(client: MagicMock, examples: MagicMock | None = None) -> MagicMock:
    """Make the autouse Mobility client mock answer with the spec examples."""
    examples = examples or mobility_client_mock()
    client.list_workspaces = examples.list_workspaces
    client.list_devices = examples.list_devices
    client.get_device = examples.get_device
    return examples


async def _setup(
    hass: HomeAssistant, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    """Set up a remote entry and wait for its first Mobility poll."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": "remote",
            "console_id": "console",
            "api_key": "cloud-key",
        },
        options=options or {},
        unique_id="console",
        entry_id="01REMOTE",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def _entity_id(hass: HomeAssistant, platform: str, unique_id: str) -> str | None:
    """Return the entity id registered for a unique id."""
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)


def _state(hass: HomeAssistant, platform: str, unique_id: str) -> State:
    """Return the current state of a Mobility entity."""
    entity_id = _entity_id(hass, platform, unique_id)
    assert entity_id is not None, unique_id
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


def _router(key: str, device_id: str = OFFICE_ROUTER_ID) -> str:
    """Return a router entity's unique id."""
    return f"mobility_{device_id}_{key}"


def _device(
    hass: HomeAssistant, entry_id: str, identifier: str
) -> dr.DeviceEntry | None:
    """Return an entry's device that carries a Mobility identifier."""
    return next(
        (
            device
            for device in dr.async_entries_for_config_entry(
                dr.async_get(hass), entry_id
            )
            if (DOMAIN, identifier) in device.identifiers
        ),
        None,
    )


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Run one Mobility poll and let entities react."""
    await entry.runtime_data.mobility_coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_router_entities_report_the_spec_values(
    hass: HomeAssistant, mock_mobility_client: MagicMock
) -> None:
    """Every router field the issue asks for is exposed with its native value."""
    _serve(mock_mobility_client)
    await _setup(hass)

    expected = {
        "state": "connected",
        "clients": "5",
        "wan_source": "lte",
        "lte_signal": "fair",
        "vpn_status": "connected",
        "subscription_status": "active",
        "memory_usage": "42",
        "firmware": "3.1.14",
        "subscription_plan": "5gb",
    }
    for key, value in expected.items():
        assert _state(hass, "sensor", _router(key)).state == value, key

    usage = _state(hass, "sensor", _router("cellular_data_usage"))
    assert float(usage.state) == pytest.approx(0.524288)
    assert usage.attributes["unit_of_measurement"] == "GB"
    assert usage.attributes["state_class"] == "total_increasing"
    limit = _state(hass, "sensor", _router("cellular_data_limit"))
    assert float(limit.state) == pytest.approx(5.36870912)

    assert _state(hass, "binary_sensor", _router("connectivity")).state == "on"
    assert (
        _state(hass, "binary_sensor", _router("connectivity", BRANCH_ROUTER_ID)).state
        == "off"
    )

    names = {
        ("sensor", _router("clients")): "Office Router Clients",
        ("sensor", _router("lte_signal")): "Office Router LTE signal",
        ("binary_sensor", _router("connectivity")): "Office Router Connectivity",
        ("device_tracker", _router("location")): "Office Router Location",
        (
            "sensor",
            f"mobility_workspace_{WORKSPACE_ID}_online_devices",
        ): "Headquarters Online routers",
    }
    for (platform, unique_id), name in names.items():
        assert _state(hass, platform, unique_id).attributes["friendly_name"] == name

    tracker = _state(hass, "device_tracker", _router("location"))
    assert tracker.attributes["latitude"] == 37.7749
    assert tracker.attributes["longitude"] == -122.4194
    assert tracker.attributes["source_type"] == "gps"
    assert tracker.state == "not_home"


async def test_empty_or_unknown_values_are_unknown(
    hass: HomeAssistant, mock_mobility_client: MagicMock
) -> None:
    """Empty strings, -1 and enum values the integration does not know are unknown."""
    _serve(mock_mobility_client)
    entry = await _setup(hass)

    for key in (
        "wan_source",
        "lte_signal",
        "vpn_status",
        "subscription_plan",
        "cellular_data_limit",
    ):
        state = _state(hass, "sensor", _router(key, BRANCH_ROUTER_ID)).state
        assert state == STATE_UNKNOWN, key
    assert _state(hass, "sensor", _router("state", BRANCH_ROUTER_ID)).state == (
        "disconnected"
    )

    detail = copy.deepcopy(OFFICE_ROUTER_DETAIL)
    detail.update(state="NULL", wan_source="SATELLITE", lte_signal_level="EXCELLENT")
    mock_mobility_client.get_device.side_effect = None
    mock_mobility_client.get_device.return_value = detail
    await _refresh(hass, entry)

    for key in ("state", "wan_source", "lte_signal"):
        assert _state(hass, "sensor", _router(key)).state == STATE_UNKNOWN, key
    assert _state(hass, "binary_sensor", _router("connectivity")).state == "off"


async def test_router_without_gps_fix_has_unknown_location(
    hass: HomeAssistant, mock_mobility_client: MagicMock
) -> None:
    """A router that has no GPS fix reports no position rather than a stale one."""
    _serve(mock_mobility_client)
    await _setup(hass)

    tracker = _state(hass, "device_tracker", _router("location", BRANCH_ROUTER_ID))
    assert tracker.state == STATE_UNKNOWN
    assert "latitude" not in tracker.attributes


async def test_workspace_device_groups_its_routers(
    hass: HomeAssistant, mock_mobility_client: MagicMock
) -> None:
    """Routers hang under their workspace and never merge with Network devices."""
    _serve(mock_mobility_client)
    entry = await _setup(hass)

    workspace = _device(hass, entry.entry_id, f"mobility_workspace_{WORKSPACE_ID}")
    assert workspace is not None
    assert workspace.name == "Headquarters"
    assert workspace.entry_type is dr.DeviceEntryType.SERVICE
    router = _device(hass, entry.entry_id, f"mobility_{OFFICE_ROUTER_ID}")
    assert router is not None
    assert router.name == "Office Router"
    assert router.model == "UMR"
    assert router.sw_version == "3.1.14"
    assert router.via_device_id == workspace.id
    assert router.connections == set()

    workspace_devices = f"mobility_workspace_{WORKSPACE_ID}_devices"
    online = f"mobility_workspace_{WORKSPACE_ID}_online_devices"
    assert _state(hass, "sensor", workspace_devices).state == "2"
    assert _state(hass, "sensor", online).state == "1"
    # A pending invitation is not a workspace the user can see yet.
    assert (
        _device(hass, entry.entry_id, f"mobility_workspace_{PENDING_WORKSPACE_ID}")
        is None
    )


async def test_routers_appear_and_disappear_with_later_polls(
    hass: HomeAssistant, mock_mobility_client: MagicMock
) -> None:
    """New routers get entities on the next poll; removed ones go unavailable."""
    examples = _serve(mock_mobility_client)
    entry = await _setup(hass)
    spec_detail = examples.get_device.side_effect
    new_router = {
        **DEVICE_SUMMARIES[0],
        "id": _NEW_ROUTER_ID,
        "name": "Van Router",
    }

    async def list_devices(workspace_id: str) -> list[dict[str, Any]]:
        return [DEVICE_SUMMARIES[0], new_router] if workspace_id == WORKSPACE_ID else []

    async def get_device(workspace_id: str, device_id: str) -> dict[str, Any]:
        if device_id == _NEW_ROUTER_ID:
            return {**OFFICE_ROUTER_DETAIL, **new_router}
        return await spec_detail(workspace_id, device_id)

    mock_mobility_client.list_devices.side_effect = list_devices
    mock_mobility_client.get_device.side_effect = get_device
    await _refresh(hass, entry)

    assert _state(hass, "sensor", _router("clients", _NEW_ROUTER_ID)).state == "5"
    assert (
        _state(hass, "sensor", _router("state", BRANCH_ROUTER_ID)).state
        == STATE_UNAVAILABLE
    )
    assert (
        _state(hass, "device_tracker", _router("location", BRANCH_ROUTER_ID)).state
        == STATE_UNAVAILABLE
    )
    assert _state(hass, "sensor", _router("state")).state == "connected"


async def test_failed_poll_makes_entities_unavailable(
    hass: HomeAssistant, mock_mobility_client: MagicMock
) -> None:
    """A cloud outage shows as unavailable, then recovers on the next good poll."""
    examples = _serve(mock_mobility_client)
    entry = await _setup(hass)

    mock_mobility_client.list_workspaces.side_effect = UniFiConnectionError("offline")
    await _refresh(hass, entry)
    assert _state(hass, "sensor", _router("clients")).state == STATE_UNAVAILABLE

    mock_mobility_client.list_workspaces.side_effect = None
    mock_mobility_client.list_workspaces.return_value = (
        examples.list_workspaces.return_value
    )
    await _refresh(hass, entry)
    assert _state(hass, "sensor", _router("clients")).state == "5"


async def test_noisy_or_sensitive_entities_are_diagnostic_or_disabled(
    hass: HomeAssistant, mock_mobility_client: MagicMock
) -> None:
    """IP, ISP and uptime are opt-in; housekeeping values are diagnostic."""
    _serve(mock_mobility_client)
    await _setup(hass)
    registry = er.async_get(hass)

    for key in ("wan_ip", "isp", "uptime"):
        entity_id = _entity_id(hass, "sensor", _router(key))
        assert entity_id is not None, key
        entity = registry.async_get(entity_id)
        assert entity is not None
        assert entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION, key
        assert entity.entity_category is EntityCategory.DIAGNOSTIC, key
    for key in ("memory_usage", "firmware", "cellular_data_limit", "subscription_plan"):
        entity = registry.async_get(_entity_id(hass, "sensor", _router(key)) or "")
        assert entity is not None, key
        assert entity.entity_category is EntityCategory.DIAGNOSTIC, key
        assert entity.disabled_by is None, key


async def test_disabling_client_tracking_keeps_the_router_tracker(
    hass: HomeAssistant, mock_mobility_client: MagicMock
) -> None:
    """Client-tracker cleanup must not delete a router's GPS tracker."""
    _serve(mock_mobility_client)
    entry = await _setup(
        hass, {CONF_TRACK_WIFI_CLIENTS: False, CONF_TRACK_WIRED_CLIENTS: False}
    )
    registry = er.async_get(hass)
    entity_id = _entity_id(hass, "device_tracker", _router("location"))
    assert entity_id is not None
    registry.async_update_entity(entity_id, name="Fleet van")

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    entity = registry.async_get(entity_id)
    assert entity is not None
    assert entity.name == "Fleet van"
    assert (
        require_not_none(hass.states.get(entity_id)).attributes["latitude"] == 37.7749
    )


async def test_router_entities_never_reference_a_missing_workspace(
    hass: HomeAssistant,
) -> None:
    """Whichever platform sets up first, the workspace device already exists."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": "remote", "console_id": "c", "api_key": "k"},
        entry_id="01REMOTE",
    )
    entry.add_to_hass(hass)
    coordinator = UnifiInsightsMobilityCoordinator(hass, entry, mobility_client_mock())
    coordinator.data = await coordinator._async_update_data()
    add_entities = MagicMock()

    async_setup_mobility_binary_sensors(hass, entry, coordinator, add_entities)

    await coordinator.async_shutdown()

    assert add_entities.call_count == 1
    assert len(add_entities.call_args.args[0]) == 2
    assert _device(hass, entry.entry_id, f"mobility_workspace_{WORKSPACE_ID}")


async def test_router_links_to_its_workspace(hass: HomeAssistant) -> None:
    """A router links to its workspace device by registry id."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": "remote", "console_id": "c", "api_key": "k"},
        entry_id="01REMOTE",
    )
    entry.add_to_hass(hass)
    coordinator = UnifiInsightsMobilityCoordinator(hass, entry, mobility_client_mock())
    coordinator.data = await coordinator._async_update_data()

    sensor = UnifiMobilitySensor(
        coordinator, ROUTER_SENSORS[0], OFFICE_ROUTER_ID, "workspace-device-id"
    )

    device_info: dict[str, Any] = dict(sensor.device_info or {})
    assert device_info["via_device_id"] == "workspace-device-id"
    assert "via_device" not in device_info


@pytest.mark.parametrize("value", [True, "37.7749", None])
async def test_tracker_rejects_non_numeric_coordinates(
    hass: HomeAssistant, value: Any
) -> None:
    """A boolean or text coordinate is not a position."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": "remote", "console_id": "c", "api_key": "k"},
        entry_id="01REMOTE",
    )
    entry.add_to_hass(hass)
    coordinator = UnifiInsightsMobilityCoordinator(hass, entry, mobility_client_mock())
    coordinator.data = await coordinator._async_update_data()
    router = coordinator.data["devices"][OFFICE_ROUTER_ID]
    router["location"] = {"latitude": value, "longitude": value}

    tracker = UnifiMobilityLocationTracker(
        coordinator, LOCATION_DESCRIPTION, OFFICE_ROUTER_ID, "workspace-device-id"
    )

    assert tracker.latitude is None
    assert tracker.longitude is None


def _value(key: str, device: dict[str, Any]) -> Any:
    """Evaluate one router sensor's value function."""
    description = next(desc for desc in ROUTER_SENSORS if desc.key == key)
    return description.value_fn(device)


def test_router_values_reject_misleading_inputs() -> None:
    """Uptime only counts while connected; non-numbers never become numbers."""
    assert _value("uptime", {"state": "CONNECTED", "uptime_seconds": 86400}) == 86400
    assert _value("uptime", {"state": "DISCONNECTED", "uptime_seconds": 0}) is None
    assert _value("clients", {"client_count": True}) is None
    assert _value("clients", {"client_count": "5"}) is None
    assert _value("clients", {"client_count": 0}) == 0
    assert _value("cellular_data_limit", {"cellular_data_limit_bytes": 0}) is None
    assert _value("firmware", {"firmware_version": ""}) is None


async def test_inactive_workspace_makes_its_sensors_unavailable(
    hass: HomeAssistant, mock_mobility_client: MagicMock
) -> None:
    """A workspace the account leaves stops reporting router counts."""
    examples = _serve(mock_mobility_client)
    entry = await _setup(hass)
    workspaces = copy.deepcopy(examples.list_workspaces.return_value)
    workspaces[0]["status"] = "INACTIVE"
    mock_mobility_client.list_workspaces.return_value = workspaces

    await _refresh(hass, entry)

    online = f"mobility_workspace_{WORKSPACE_ID}_online_devices"
    assert _state(hass, "sensor", online).state == STATE_UNAVAILABLE


async def test_workspace_sensor_has_no_value_once_the_workspace_is_inactive(
    hass: HomeAssistant,
) -> None:
    """A counter never reports routers for a workspace the account has left."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": "remote", "console_id": "c", "api_key": "k"},
        entry_id="01REMOTE",
    )
    entry.add_to_hass(hass)
    coordinator = UnifiInsightsMobilityCoordinator(hass, entry, mobility_client_mock())
    coordinator.data = await coordinator._async_update_data()
    online = next(desc for desc in WORKSPACE_SENSORS if desc.key == "online_devices")
    sensor = UnifiMobilityWorkspaceSensor(coordinator, online, WORKSPACE_ID)
    assert sensor.native_value == 1

    coordinator.data["workspaces"][WORKSPACE_ID]["status"] = "INACTIVE"

    assert sensor.native_value is None
    assert sensor.available is False


async def test_network_actions_reject_mobility_targets(
    hass: HomeAssistant, mock_mobility_client: MagicMock
) -> None:
    """A router entity or device is never mistaken for a Network device or client."""
    _serve(mock_mobility_client)
    entry = await _setup(hass)
    router = _device(hass, entry.entry_id, f"mobility_{OFFICE_ROUTER_ID}")
    assert router is not None
    sensor = _entity_id(hass, "sensor", _router("state"))
    tracker = _entity_id(hass, "device_tracker", _router("location"))
    assert sensor is not None
    assert tracker is not None

    for target in (sensor, router.id):
        with pytest.raises(ServiceValidationError) as err:
            _resolve_network_device_id(hass, target, None, [entry])
        assert err.value.translation_key == "target_mobility_unsupported"
    for target in (tracker, router.id):
        with pytest.raises(ServiceValidationError) as err:
            _resolve_network_client_id(hass, target, [entry])
        assert err.value.translation_key == "target_mobility_unsupported"
