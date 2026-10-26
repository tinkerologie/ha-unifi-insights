# Copyright 2026 UniFi Insights contributors
"""
Consumers of the network device read model (#337).

Entity platforms and services read the published facts; the three drift
regressions show entities and dashboards agree; the unique ID locks pin every
port ID spelling and the base, tracker and Wake families.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.setup import async_setup_component
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
    UnifiClientWakeButton,
    UnifiInsightsButton,
    UnifiInsightsPoePowerCycleButton,
    _wired_client_mac,
    get_device_port,
)
from custom_components.unifi_insights.button import (
    async_setup_entry as button_setup,
)
from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.dashboard_snapshots import (
    build_internet_activity_snapshot,
    build_performance_snapshot,
    build_site_health_snapshot,
)
from custom_components.unifi_insights.data_transforms import (
    NETWORK_FACTS_KEY,
    build_site_network_facts,
)
from custom_components.unifi_insights.device_tracker import UnifiClientTracker
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
from custom_components.unifi_insights.topology import build_site_topology
from custom_components.unifi_insights.topology import (
    device_kind as topology_device_kind,
)
from tests.fixtures.facade_data import (
    frozen,
    set_availability,
    with_network_device,
    with_sections,
    without_sections,
)
from tests.fixtures.library_responses import (
    SAMPLE_UDM_PRO_OVERVIEW,
    SAMPLE_UDM_PRO_STATS,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

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
    coordinator.protect_client = None
    coordinator.get_site = MagicMock(return_value={"id": SITE})
    coordinator.async_get_wake_history = AsyncMock(return_value={})
    data: dict[str, Any] = {
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
    coordinator.data = _published(data) if publish else data
    return coordinator


def _published(data: Mapping[str, Any]) -> Any:
    """Return ``data`` with the facts published like the device coordinator does."""
    return frozen(
        {
            **data,
            NETWORK_FACTS_KEY: {
                site_id: build_site_network_facts(devices, data["stats"].get(site_id))
                for site_id, devices in data["devices"].items()
            },
        }
    )


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
    snapshot = _published(data)
    with _no_rebuild():
        assert build_site_device_info(snapshot, SITE) == raw_info
    assert raw_info == {"identifiers": {(DOMAIN, f"{SITE}_udm")}}

    snapshot = without_sections(
        with_sections(snapshot, devices={SITE: {"sw1": _switch()}}), NETWORK_FACTS_KEY
    )
    assert build_site_device_info(snapshot, SITE)["identifiers"] == {
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
    coordinator.data = with_sections(
        coordinator.data, devices={SITE: {"sw1": _switch(state="OFFLINE")}}
    )
    with _no_rebuild():
        assert entity.device_facts is not None
        assert entity.available is True
        assert entity.is_on is True

    coordinator.data = without_sections(
        with_sections(
            coordinator.data, devices={SITE: {"sw1": _switch(state="CONNECTED")}}
        ),
        NETWORK_FACTS_KEY,
    )
    assert entity.available is False
    assert entity.is_on is None

    coordinator.data = with_sections(coordinator.data, devices={SITE: {}})
    assert entity.device_facts is None
    assert entity.available is False
    assert entity.is_on is None

    coordinator.data = with_sections(coordinator.data, devices={SITE: {"sw1": {}}})
    assert entity.is_on is None
    coordinator.data = with_sections(
        coordinator.data, devices={SITE: {"sw1": {"state": "ONLINE"}}}
    )
    set_availability(coordinator, device=False)
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
        "cpu_usage": 14.6,
        "memory_usage": 62.5,
        "uptime": "38d 13h 56m",
        "tx_rate": 35072,
        "rx_rate": 74016,
    }

    # Sensors that still read raw stats have no value without them.
    wired = next(d for d in SENSOR_TYPES if d.key == "wired_clients")
    wired_sensor = UnifiInsightsSensor(coordinator, wired, SITE, "udm")
    coordinator.data = with_sections(coordinator.data, stats={SITE: {}})
    assert wired_sensor.native_value is None

    coordinator.data = _published(with_sections(coordinator.data, devices={SITE: {}}))
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

    coordinator.data = _published(
        with_network_device(coordinator.data, SITE, "sw1", _switch())
    )
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
    set_availability(coordinator, device=False)
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


# --- Drift regressions (#337) --------------------------------------------

_WITH_WANS = {"wans": [{"key": "wan", "connected": True}]}


def _gateway_cases() -> list[Any]:
    """The observed UDM Pro, plus non-gateway models advertising a gateway."""
    captured = dict(SAMPLE_UDM_PRO_OVERVIEW)
    features_list = {
        "id": "gw",
        "name": "Router",
        "model": "Unknown",
        "state": "ONLINE",
        "features": ["switching", "gateway"],
    }
    features_object = {**features_list, "features": {"switching": {}, "gateway": {}}}
    return [
        pytest.param(captured, id="captured-udm-pro"),
        pytest.param(features_list, id="gateway-feature-list"),
        pytest.param(features_object, id="gateway-feature-object"),
    ]


def _site_data(devices: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    """Return facade-shaped data for one site, with published facts."""
    data: dict[str, Any] = {
        "sites": {SITE: {"id": SITE, "name": "Home"}},
        "devices": {SITE: devices},
        "stats": {SITE: stats},
        "clients": {SITE: {}},
        "internet_activity": {SITE: {"1h": {"rx_bytes": 1, "tx_bytes": 1}}},
        "internet_activity_unavailable": set(),
    }
    return _published(data)


def _snapshots(data: dict[str, Any]) -> dict[str, Any]:
    """Build the site-health, performance, activity and topology snapshots."""
    registry = MagicMock()
    registry.async_get_entity_id.return_value = None
    common = {"entry_id": "entry", "site_id": SITE}
    return {
        "health": build_site_health_snapshot(
            data, ha_device_ids={}, devices_available=True, **common
        ),
        "performance": build_performance_snapshot(
            data, ha_device_ids={}, devices_available=True, **common
        ),
        "activity": build_internet_activity_snapshot(
            data, entity_registry=registry, **common
        ),
        "topology": build_site_topology(
            data, "entry", SITE, node_key=b"k" * 32, ha_device_ids={}
        ),
    }


@pytest.mark.parametrize("gateway", _gateway_cases())
def test_drift_1_gateway_agrees_everywhere(gateway: dict[str, Any]) -> None:
    """Entities and every dashboard call the same device the gateway."""
    devices = {"gw": copy.deepcopy(gateway), "sw1": _switch()}
    snapshots = _snapshots(_site_data(devices, {}))

    assert is_gateway_device(gateway) is True
    assert topology_device_kind(gateway) == "gateway"
    health = snapshots["health"]
    assert health["gateway"]["present"] is True
    assert "gateway_missing" not in health["health"]["reasons"]
    assert health["devices"]["gateway"]["online"] == 1
    # Not counted as a switch: only sw1 is one.
    assert health["devices"]["switch"]["online"] == 1
    # No WAN data: gateway recognition alone cannot establish internet.
    assert health["gateway"]["internet"] == "unknown"
    kinds = {d["id"]: d["kind"] for d in snapshots["performance"]["devices"]}
    assert kinds[f"{SITE}:gw"] == "gateway"
    assert snapshots["activity"]["throughput"] is not None
    assert snapshots["activity"]["wan_health"]["gateway_online"] is True
    node_kinds = sorted(node["kind"] for node in snapshots["topology"]["nodes"])
    assert node_kinds == ["gateway", "switch"]

    # With WAN data the internet state comes from it.
    devices["gw"].update(_WITH_WANS)
    health = _snapshots(_site_data(devices, {}))["health"]
    assert health["gateway"]["internet"] == "online"


@pytest.mark.parametrize(
    ("stats", "expected"),
    [
        pytest.param(dict(SAMPLE_UDM_PRO_STATS), (35072, 74016), id="captured"),
        pytest.param(
            {"uplink": {"txRateBps": 100, "rxRateBps": 50}}, (100, 50), id="public"
        ),
        pytest.param({"tx_rate_bps": 100, "rx_rate_bps": 50}, (100, 50), id="snake"),
        pytest.param(
            {"tx_bytes_per_sec": 100, "rx_bytes_per_sec": 50}, (800, 400), id="bytes"
        ),
        pytest.param({"uplink": {"txRateBps": 0, "rxRateBps": 0}}, (0, 0), id="zero"),
    ],
)
async def test_drift_2_throughput_agrees_everywhere(
    hass: HomeAssistant, stats: dict[str, Any], expected: tuple[int, int]
) -> None:
    """Uplink sensors, performance and internet activity report the same bit/s."""
    devices = {"udm": dict(SAMPLE_UDM_PRO_OVERVIEW)}
    data = _site_data(devices, {"udm": stats})
    coordinator = _coordinator(hass, devices, {"udm": stats})
    coordinator.data = data
    sensors = tuple(
        UnifiInsightsSensor(
            coordinator, next(d for d in SENSOR_TYPES if d.key == key), SITE, "udm"
        ).native_value
        for key in ("tx_rate", "rx_rate")
    )
    snapshots = _snapshots(data)
    (row,) = snapshots["performance"]["devices"]
    throughput = snapshots["activity"]["throughput"]

    assert sensors == expected
    assert (row["tx_bps"], row["rx_bps"]) == expected
    assert (throughput["tx_bps"], throughput["rx_bps"]) == expected


_PORTS = [
    {"idx": 1, "state": "UP", "speedMbps": 1000, "media": "GE"},
    {"idx": 25, "state": "UP", "speedMbps": 10000, "media": "SFP+", "sfp_found": True},
]


async def test_drift_3_ports_share_one_source(hass: HomeAssistant) -> None:
    """Port sensors and SFP binaries come and go with the same port records."""
    coordinator = _coordinator(hass, {"sw1": _switch(_PORTS)}, publish=True)
    sensors: list = []
    _discover_port_sensors(
        coordinator, SITE, "sw1", _switch(_PORTS), set(), entities=sensors
    )
    binaries = [
        e
        for e in await _setup(hass, binary_sensor_setup, coordinator)
        if isinstance(e, UnifiPortBinarySensor)
    ]
    assert {e._port_idx for e in sensors} == {1, 25}
    assert [e._port_idx for e in binaries] == [25]

    # The legacy fetch fails: the overview has list-shaped interfaces only.
    v1_only = {**_switch(), "interfaces": ["ports"], "ports": []}
    coordinator.data = _published(
        with_network_device(coordinator.data, SITE, "sw1", v1_only)
    )
    rediscovered: list = []
    _discover_port_sensors(
        coordinator, SITE, "sw1", v1_only, set(), entities=rediscovered
    )
    assert rediscovered == []
    assert not [
        e
        for e in await _setup(hass, binary_sensor_setup, coordinator)
        if isinstance(e, UnifiPortBinarySensor)
    ]
    # Existing port entities follow their missing-port behaviour together.
    speed = next(e for e in sensors if e.entity_description.key == "port_speed")
    assert speed.native_value is None
    assert speed.available is False
    assert binaries[0].is_on is None


# --- Unique ID and lookup locks (#337) -------------------------------------


async def _port_entities(
    hass: HomeAssistant, ports: list[dict[str, Any]]
) -> tuple[list, list, list]:
    """Discover the ordinary port sensors, SFP binaries and PoE buttons."""
    coordinator = _coordinator(hass, {"sw1": _switch(ports)}, publish=True)
    sensors: list = []
    _discover_port_sensors(
        coordinator, SITE, "sw1", _switch(ports), set(), entities=sensors
    )
    speed = [e for e in sensors if e.entity_description.key == "port_speed"]
    binaries = [
        e
        for e in await _setup(hass, binary_sensor_setup, coordinator)
        if isinstance(e, UnifiPortBinarySensor)
    ]
    buttons = [
        e
        for e in await _setup(hass, button_setup, coordinator)
        if isinstance(e, UnifiInsightsPoePowerCycleButton)
    ]
    return speed, binaries, buttons


def _port(raw_index: Any, **extra: Any) -> dict[str, Any]:
    """Return an UP SFP port with PoE enabled at ``raw_index``."""
    return {
        "idx": raw_index,
        "state": "UP",
        "speedMbps": 1000,
        "media": "SFP+",
        "sfp_found": True,
        "poe": {"enabled": True},
        **extra,
    }


@pytest.mark.parametrize(
    ("raw_index", "sensor_id", "sfp_id", "poe_id"),
    [
        (3, "sw1_port_speed_3", "sw1_port_sfp_present_3", "site1_sw1_port3"),
        ("03", "sw1_port_speed_3", "sw1_port_sfp_present_03", None),
        (" 3 ", "sw1_port_speed_3", "sw1_port_sfp_present_ 3 ", None),
        (0, "sw1_port_speed_0", None, "site1_sw1_port0"),
    ],
)
async def test_port_unique_id_spelling_matrix(
    hass: HomeAssistant,
    raw_index: Any,
    sensor_id: str,
    sfp_id: str | None,
    poe_id: str | None,
) -> None:
    """Each consumer keeps its unique ID spelling, and its lookup resolves."""
    speed, binaries, buttons = await _port_entities(hass, [_port(raw_index)])

    assert [e.unique_id for e in speed] == [sensor_id]
    assert speed[0].native_value == 1000
    assert [e.unique_id for e in binaries] == ([sfp_id] if sfp_id else [])
    for binary in binaries:
        assert binary.is_on is True
    expected_poe = [f"{poe_id}_poe_power_cycle"] if poe_id else []
    assert [e.unique_id for e in buttons] == expected_poe
    for button in buttons:
        assert button.available is True


async def test_duplicate_raw_index_first_record_wins(hass: HomeAssistant) -> None:
    """For a duplicated index the first record wins discovery and lookup."""
    ports = [
        _port(3, name="First", speedMbps=100),
        _port(3, name="Second", speedMbps=1000, poe={"enabled": False}),
    ]
    speed, binaries, buttons = await _port_entities(hass, ports)

    assert [e.unique_id for e in speed] == ["sw1_port_speed_3"]
    assert speed[0].native_value == 100
    assert speed[0].translation_placeholders == {"port_label": "First"}
    assert [e.unique_id for e in binaries] == ["sw1_port_sfp_present_3"]
    assert [e.unique_id for e in buttons] == ["site1_sw1_port3_poe_power_cycle"]
    assert buttons[0].available is True


async def test_spellings_of_one_index_collapse_only_for_sensors(
    hass: HomeAssistant,
) -> None:
    """3 and "03" share one ordinary sensor but stay two SFP binaries."""
    speed, binaries, _ = await _port_entities(hass, [_port(3), _port("03")])

    assert [e.unique_id for e in speed] == ["sw1_port_speed_3"]
    assert [e.unique_id for e in binaries] == [
        "sw1_port_sfp_present_3",
        "sw1_port_sfp_present_03",
    ]


async def test_sfp_raw_int_and_str_index_collide(hass: HomeAssistant) -> None:
    """
    Pin the unresolved collision: raw 3 and "3" format the same SFP ID.

    Discovery keys them apart, so two entities claim one unique ID and Home
    Assistant keeps only the first. This is today's behaviour, unchanged.
    """
    _, binaries, _ = await _port_entities(hass, [_port(3), _port("3")])

    assert [e.unique_id for e in binaries] == [
        "sw1_port_sfp_present_3",
        "sw1_port_sfp_present_3",
    ]
    assert [e._port_idx for e in binaries] == [3, "3"]


async def test_base_tracker_and_wake_unique_ids(hass: HomeAssistant) -> None:
    """The base, tracker and Wake unique ID families keep their formats."""
    devices = {"udm": dict(SAMPLE_UDM_PRO_OVERVIEW)}
    coordinator = _coordinator(hass, devices, publish=True)
    cpu = next(d for d in SENSOR_TYPES if d.key == "cpu_usage")
    assert UnifiInsightsSensor(coordinator, cpu, SITE, "udm").unique_id == (
        "site1_udm_cpu_usage"
    )
    status = next(d for d in BINARY_SENSOR_TYPES if d.key == "device_status")
    assert UnifiInsightsBinarySensor(coordinator, status, SITE, "udm").unique_id == (
        "site1_udm_device_status"
    )

    client = {"type": "ClientType.WIRED", "macAddress": "AA-BB-CC-00-00-02"}
    tracker = UnifiClientTracker(coordinator, "AA:BB:CC:00:00:02", SITE)
    assert tracker.unique_id == "unifi_insights_aa:bb:cc:00:00:02"
    wake_mac = _wired_client_mac(client)
    assert wake_mac == "aa:bb:cc:00:00:02"
    wake = UnifiClientWakeButton(coordinator, wake_mac, site_id=SITE)
    assert wake.unique_id == "unifi_insights_aa:bb:cc:00:00:02_wake"
    # A decorated, unrecognised type is not a wired client any more.
    assert _wired_client_mac({**client, "type": "WIRED_GUEST"}) is None


@pytest.mark.parametrize("key", ["device_status", "wan_status"])
@pytest.mark.parametrize("publish", [False, True])
async def test_transient_connectivity_is_unknown(
    hass: HomeAssistant, key: str, *, publish: bool
) -> None:
    """Status/WAN stay available but never assert offline during provisioning."""
    coordinator = _coordinator(
        hass, {"sw1": _switch(state="GETTING_READY")}, publish=publish
    )
    description = next(d for d in BINARY_SENSOR_TYPES if d.key == key)
    sensor = UnifiInsightsBinarySensor(coordinator, description, SITE, "sw1")
    assert sensor.available is True
    assert sensor.is_on is None
    assert await async_setup_component(hass, "binary_sensor", {})
    sensor.entity_id = f"binary_sensor.{key}"
    await hass.data["binary_sensor"].async_add_entities([sensor])
    state = hass.states.get(sensor.entity_id)
    assert state is not None
    assert state.state == "unknown"
    set_availability(coordinator, device=False)
    assert sensor.available is False


@pytest.mark.parametrize("key", ["device_status", "wan_status"])
@pytest.mark.parametrize(("state", "expected"), [("ONLINE", True), ("OFFLINE", False)])
def test_connectivity_follows_known_online_state(
    hass: HomeAssistant, key: str, state: str, *, expected: bool
) -> None:
    """Status/WAN report on for ONLINE and off for OFFLINE, as before #372."""
    coordinator = _coordinator(hass, {"sw1": _switch(state=state)}, publish=True)
    description = next(d for d in BINARY_SENSOR_TYPES if d.key == key)
    sensor = UnifiInsightsBinarySensor(coordinator, description, SITE, "sw1")
    assert sensor.is_on is expected
