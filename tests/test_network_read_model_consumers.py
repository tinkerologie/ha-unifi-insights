# Copyright 2026 UniFi Insights contributors
"""Entity platforms and services read the network device read model (#337)."""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from unifi_official_api.network.models.client import ClientType

from custom_components.unifi_insights import data_transforms
from custom_components.unifi_insights.binary_sensor import (
    BINARY_SENSOR_TYPES,
    UnifiInsightsBinarySensor,
    UnifiPortBinarySensor,
)
from custom_components.unifi_insights.binary_sensor import (
    async_setup_entry as binary_sensor_setup,
)
from custom_components.unifi_insights.button import (
    UnifiInsightsButton,
    UnifiInsightsPoePowerCycleButton,
    get_device_port,
)
from custom_components.unifi_insights.button import (
    async_setup_entry as button_setup,
)
from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.data_transforms import (
    NETWORK_FACTS_KEY,
    build_site_network_facts,
)
from custom_components.unifi_insights.entity import (
    build_site_device_info,
    get_client_type,
    is_device_online,
    is_gateway_device,
)
from custom_components.unifi_insights.sensor import (
    PORT_SENSOR_TYPES,
    SENSOR_TYPES,
    UnifiInsightsSensor,
    UnifiPortSensor,
    _discover_device_sensors,
    _discover_port_sensors,
)
from custom_components.unifi_insights.services import _is_wireless_client
from tests.fixtures.library_responses import (
    SAMPLE_UDM_PRO_OVERVIEW,
    SAMPLE_UDM_PRO_STATS,
)

SITE = "site1"


def _switch(ports: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    """Return a constructed switch with legacy-merged ports."""
    device: dict[str, Any] = {
        "id": "sw1",
        "name": "Switch",
        "model": "USW-24-POE",
        "state": "ONLINE",
        "macAddress": "aa:bb:cc:00:00:01",
        "features": ["switching"],
    }
    if ports is not None:
        device["ports"] = ports
    device.update(extra)
    return device


def _coordinator(
    hass: HomeAssistant,
    devices: dict[str, Any],
    stats: dict[str, Any] | None = None,
    *,
    publish: bool = False,
) -> MagicMock:
    """Return a mock facade coordinator holding one site's data."""
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.device_available = True
    coordinator.protect_client = None
    coordinator.get_site = MagicMock(return_value={"id": SITE})
    coordinator.async_get_wake_history = AsyncMock(return_value={})
    coordinator.data = {
        "sites": {SITE: {"id": SITE, "name": "Default"}},
        "devices": {SITE: devices},
        "stats": {SITE: stats or {}},
        "clients": {SITE: {}},
        "protect": {
            "cameras": {},
            "lights": {},
            "sensors": {},
            "nvrs": {},
            "viewers": {},
            "chimes": {},
            "liveviews": {},
        },
    }
    if publish:
        _publish(coordinator.data)
    return coordinator


def _publish(data: dict[str, Any]) -> None:
    """Publish the facts like the device coordinator does after a refresh."""
    data[NETWORK_FACTS_KEY] = {
        site_id: build_site_network_facts(devices, data["stats"].get(site_id))
        for site_id, devices in data["devices"].items()
    }


def _entry(coordinator: MagicMock) -> MagicMock:
    """Return a mock config entry whose runtime data holds ``coordinator``."""
    entry = MagicMock()
    entry.options = {}
    entry.runtime_data = MagicMock(coordinator=coordinator)
    entry.runtime_data.mobility_coordinator = None
    entry.runtime_data.access_coordinator = None
    entry.async_create_background_task.side_effect = lambda hass, target, name: (
        hass.async_create_background_task(target, name)
    )
    return entry


async def _setup(hass: HomeAssistant, setup: Any, coordinator: MagicMock) -> list:
    """Run a platform's async_setup_entry and return the entities it added."""
    added: list = []
    await setup(hass, _entry(coordinator), lambda entities, **_: added.extend(entities))
    return added


def _no_rebuild() -> Any:
    """Patch the builder so a published-facts read that rebuilds fails."""
    return patch.object(
        data_transforms,
        "build_site_network_facts",
        side_effect=AssertionError("published facts were rebuilt"),
    )


# --- entity.py helpers -----------------------------------------------------


@pytest.mark.parametrize(
    ("client", "expected"),
    [
        ({"type": "WIRED"}, "WIRED"),
        ({"type": " wireless "}, "WIRELESS"),
        ({"type": "ClientType.WIRED"}, "WIRED"),
        ({"type": ClientType.WIRELESS}, "WIRELESS"),
        ({"connection_type": "WIRELESS"}, "WIRELESS"),
        # Decorated values were substring matches before #337; now they are
        # unknown and come back as is.
        ({"type": "WIRED_GUEST"}, "WIRED_GUEST"),
        ({"type": "UNKNOWN_WIRELESS"}, "UNKNOWN_WIRELESS"),
        ({"type": None}, ""),
        ({}, ""),
    ],
)
def test_get_client_type_uses_the_exact_rule(
    client: dict[str, Any], expected: str
) -> None:
    """Recognition is exact; unknown values keep their upper-cased spelling."""
    assert get_client_type(client) == expected


@pytest.mark.parametrize(
    ("state", "online"),
    [
        ("ONLINE", True),
        ("online", True),
        ("CONNECTED", False),
        ("connected", False),
        ("up", False),
        ("CONNECTION_INTERRUPTED", False),
        ("UPDATING", False),
    ],
)
def test_is_device_online_follows_the_spec(state: str, *, online: bool) -> None:
    """Only ONLINE is online; lowercase connected/up no longer are."""
    assert is_device_online({"state": state}) is online


def test_null_type_udm_pro_is_a_gateway() -> None:
    """The observed UDM Pro (null type, only switching) is a gateway."""
    assert SAMPLE_UDM_PRO_OVERVIEW["type"] is None
    assert is_gateway_device(SAMPLE_UDM_PRO_OVERVIEW) is True
    # A legacy gateway type is now recognised too.
    assert is_gateway_device({"model": "X", "topology": {"legacy_type": "udm"}})


def test_site_grouping_uses_the_published_gateway() -> None:
    """Site entities group under the published gateway without a rebuild."""
    data: dict[str, Any] = {
        "sites": {SITE: {"name": "Home"}},
        "devices": {SITE: {"sw1": _switch(), "udm": dict(SAMPLE_UDM_PRO_OVERVIEW)}},
        "stats": {SITE: {}},
    }
    raw_info = build_site_device_info(data, SITE)
    _publish(data)
    with _no_rebuild():
        assert build_site_device_info(data, SITE) == raw_info
    assert raw_info == {"identifiers": {(DOMAIN, f"{SITE}_udm")}}

    data["devices"][SITE] = {"sw1": _switch()}
    data.pop(NETWORK_FACTS_KEY)
    assert build_site_device_info(data, SITE)["identifiers"] == {
        (DOMAIN, f"site_{SITE}")
    }


async def test_availability_and_status_read_published_facts(
    hass: HomeAssistant,
) -> None:
    """The base availability and status binaries read the online fact."""
    coordinator = _coordinator(hass, {"sw1": _switch()}, publish=True)
    status = next(d for d in BINARY_SENSOR_TYPES if d.key == "device_status")
    entity = UnifiInsightsBinarySensor(coordinator, status, SITE, "sw1")
    # The raw record changes, the published facts do not: facts win.
    coordinator.data["devices"][SITE]["sw1"]["state"] = "OFFLINE"
    with _no_rebuild():
        assert entity.device_facts is not None
        assert entity.available is True
        assert entity.is_on is True

    coordinator.data["devices"][SITE]["sw1"]["state"] = "CONNECTED"
    coordinator.data.pop(NETWORK_FACTS_KEY)
    assert entity.available is False
    assert entity.is_on is False

    coordinator.data["devices"][SITE] = {}
    assert entity.device_facts is None
    assert entity.available is False
    assert entity.is_on is None

    coordinator.data["devices"][SITE]["sw1"] = {}
    assert entity.is_on is None
    coordinator.data["devices"][SITE]["sw1"] = {"state": "ONLINE"}
    coordinator.device_available = False
    assert entity.available is False


# --- sensors -------------------------------------------------------------


async def test_system_and_uplink_sensors_read_published_facts(
    hass: HomeAssistant,
) -> None:
    """CPU, memory, uptime and the uplink rates come from the facts, in bit/s."""
    devices = {"udm": dict(SAMPLE_UDM_PRO_OVERVIEW)}
    stats = {"udm": dict(SAMPLE_UDM_PRO_STATS)}
    coordinator = _coordinator(hass, devices, stats, publish=True)
    values = {}
    with _no_rebuild():
        for key in ("cpu_usage", "memory_usage", "uptime", "tx_rate", "rx_rate"):
            description = next(d for d in SENSOR_TYPES if d.key == key)
            sensor = UnifiInsightsSensor(coordinator, description, SITE, "udm")
            values[key] = sensor.native_value
    assert values == {
        "cpu_usage": 12.5,
        "memory_usage": 63.2,
        "uptime": "10d 0h 0m",
        "tx_rate": 93688,
        "rx_rate": 144528,
    }

    # Sensors that still read raw stats have no value without them.
    wired = next(d for d in SENSOR_TYPES if d.key == "wired_clients")
    wired_sensor = UnifiInsightsSensor(coordinator, wired, SITE, "udm")
    coordinator.data["stats"][SITE] = {}
    assert wired_sensor.native_value is None

    coordinator.data["devices"][SITE] = {}
    _publish(coordinator.data)
    assert sensor.native_value is None


async def test_wan_sensors_follow_the_kind_fact(hass: HomeAssistant) -> None:
    """The null-type UDM Pro gets WAN sensors; a switch does not."""
    devices = {"udm": dict(SAMPLE_UDM_PRO_OVERVIEW), "sw1": _switch()}
    coordinator = _coordinator(hass, devices, publish=True)
    entities: list = []
    with _no_rebuild():
        for device_id, device in devices.items():
            _discover_device_sensors(
                coordinator, SITE, device_id, device, set(), entities=entities
            )
    wan = {e._device_id for e in entities if e.entity_description.key == "wan_ip"}
    assert wan == {"udm"}


async def test_port_sensors_resolve_string_and_zero_indexes(
    hass: HomeAssistant,
) -> None:
    """Discovery and runtime lookup both use the normalised index."""
    ports = [
        {"idx": "03", "state": "UP", "speedMbps": 1000},
        {"idx": 0, "state": "UP", "speedMbps": 100},
    ]
    coordinator = _coordinator(hass, {"sw1": _switch(ports)}, publish=True)
    entities: list = []
    with _no_rebuild():
        _discover_port_sensors(
            coordinator, SITE, "sw1", _switch(ports), set(), entities=entities
        )
    speed = {
        e.unique_id: e for e in entities if e.entity_description.key == "port_speed"
    }
    assert set(speed) == {"sw1_port_speed_3", "sw1_port_speed_0"}
    with _no_rebuild():
        # Before #337 the runtime lookup compared the raw "03" and missed.
        assert speed["sw1_port_speed_3"].native_value == 1000
        assert speed["sw1_port_speed_3"].available is True
        assert speed["sw1_port_speed_0"].native_value == 100
        assert speed["sw1_port_speed_0"].extra_state_attributes is None


async def test_port_sensors_have_no_ports_without_legacy_data(
    hass: HomeAssistant,
) -> None:
    """A v1-only device has no port records, so no port sensor at all."""
    stats = {"udm": {"poe_ports": {"1": 2.0}, "port_bytes": {"1": {"tx_bytes": 1}}}}
    device = dict(SAMPLE_UDM_PRO_OVERVIEW)
    coordinator = _coordinator(hass, {"udm": device}, stats, publish=True)
    entities: list = []
    _discover_port_sensors(coordinator, SITE, "udm", device, set(), entities=entities)
    assert entities == []

    sensor = UnifiPortSensor(coordinator, PORT_SENSOR_TYPES[1], SITE, "udm", 1)
    assert sensor.native_value is None
    assert sensor.available is False


# --- binary sensors --------------------------------------------------------


async def test_sfp_binaries_share_the_port_source(hass: HomeAssistant) -> None:
    """SFP presence discovers from interfaces.ports when no legacy ports exist."""
    device = _switch(
        interfaces={"ports": [{"idx": 25, "media": "SFP+", "sfp_found": True}]}
    )
    coordinator = _coordinator(hass, {"sw1": device}, publish=True)
    with _no_rebuild():
        entities = await _setup(hass, binary_sensor_setup, coordinator)
        sfp = [e for e in entities if isinstance(e, UnifiPortBinarySensor)]
        assert [e.unique_id for e in sfp] == ["sw1_port_sfp_present_25"]
        assert sfp[0].is_on is True

    coordinator.data["devices"][SITE]["sw1"] = _switch()
    _publish(coordinator.data)
    assert sfp[0].is_on is None


async def test_wan_status_follows_the_kind_fact(hass: HomeAssistant) -> None:
    """The null-type UDM Pro gets a WAN status sensor from the kind fact."""
    devices = {"udm": dict(SAMPLE_UDM_PRO_OVERVIEW), "sw1": _switch()}
    coordinator = _coordinator(hass, devices, publish=True)
    entities = await _setup(hass, binary_sensor_setup, coordinator)
    wan = [
        e._device_id
        for e in entities
        if isinstance(e, UnifiInsightsBinarySensor)
        and e.entity_description.key == "wan_status"
    ]
    assert wan == ["udm"]


# --- buttons and services --------------------------------------------------


async def test_poe_buttons_use_the_shared_port_tuple(hass: HomeAssistant) -> None:
    """PoE discovery and lookup read the facts and need an integer raw idx."""
    ports = [
        {"idx": 1, "poe": {"enabled": True}},
        {"idx": "2", "poe": {"enabled": True}},
        {"idx": 3, "poe": {"enabled": False}},
    ]
    coordinator = _coordinator(hass, {"sw1": _switch(ports)}, publish=True)
    with _no_rebuild():
        entities = await _setup(hass, button_setup, coordinator)
        poe = [e for e in entities if isinstance(e, UnifiInsightsPoePowerCycleButton)]
        assert [e.unique_id for e in poe] == [f"{SITE}_sw1_port1_poe_power_cycle"]
        assert poe[0].available is True
        assert get_device_port(coordinator.data, SITE, "sw1", 2) is None
        assert get_device_port(coordinator.data, SITE, "sw1", 3) == ports[2]
    restart = [e for e in entities if isinstance(e, UnifiInsightsButton)]
    assert restart
    assert restart[0].available is True
    coordinator.device_available = False
    assert restart[0].available is False


@pytest.mark.parametrize(
    ("client", "wireless"),
    [
        ({"type": ClientType.WIRELESS}, True),
        ({"type": "ClientType.WIRELESS"}, True),
        ({"type": " wireless "}, True),
        ({"type": "UNKNOWN_WIRELESS"}, False),
        ({"connection_type": "WIRELESS"}, True),
        ({"type": "WIRED"}, False),
    ],
)
def test_service_wireless_check_uses_the_shared_rule(
    client: dict[str, Any], *, wireless: bool
) -> None:
    """The reconnect action's wireless check is the shared client rule."""
    assert _is_wireless_client(copy.deepcopy(client)) is wireless
