# Copyright 2026 UniFi Insights contributors
"""
The Network platforms read the facade snapshot through its lookups (ADR-013).

A missing or malformed section, site or record reads as no record: discovery
adds no entity for it, and an entity reads it as it reads a record the
snapshot does not hold.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import DEFAULT, AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_insights import (
    binary_sensor,
    button,
    device_tracker,
    image,
    sensor,
    service_targets,
    switch,
    topology,
    update,
)
from custom_components.unifi_insights.client_wake import wake_unique_id
from custom_components.unifi_insights.const import CONF_CLIENT_CONTROL, DOMAIN
from tests.fixtures.entities import EntityCollector
from tests.fixtures.facade_data import (
    facade_data,
    with_client,
    with_latest_voucher,
    with_network_device,
    with_rule,
    with_sections,
    without_sections,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from homeassistant.core import HomeAssistant

    from custom_components.unifi_insights.coordinators.facade import FacadeData

SITE = "site1"
RECORD_ID = "rec1"
MAC = "02:00:00:00:00:0a"

type Variant = Callable[[FacadeData, str], FacadeData]


def _no_record(site_id: str = SITE, record_id: str = RECORD_ID) -> list[Any]:
    """Return snapshots that hold no usable ``record_id`` of a site's section."""
    return [
        pytest.param(without_sections, id="no-section"),
        pytest.param(
            lambda base, s: with_sections(base, **{s: []}), id="malformed-section"
        ),
        pytest.param(lambda base, s: with_sections(base, **{s: {}}), id="no-site"),
        pytest.param(
            lambda base, s: with_sections(base, **{s: {site_id: []}}),
            id="malformed-site",
        ),
        pytest.param(
            lambda base, s: with_sections(base, **{s: {site_id: {}}}), id="no-record"
        ),
        pytest.param(
            lambda base, s: with_sections(
                base, **{s: {site_id: {record_id: "invalid"}}}
            ),
            id="malformed-record",
        ),
    ]


# Snapshots that hold no usable record of a site-keyed section.
NO_RECORD = _no_record()


def _snapshot(
    section: str, record: dict[str, Any], base: FacadeData | None = None
) -> FacadeData:
    """Return ``base`` holding one record of a site-keyed section."""
    data = facade_data() if base is None else base
    if section == "devices":
        return with_network_device(data, SITE, RECORD_ID, record)
    if section == "stats":
        return with_network_device(data, SITE, RECORD_ID, {}, stats=record)
    if section == "clients":
        return with_client(data, SITE, RECORD_ID, record)
    return with_rule(data, section, SITE, RECORD_ID, record)


def _coordinator(data: Any) -> MagicMock:
    """Return a coordinator double that publishes ``data`` as its snapshot."""
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.protect_client = None
    return coordinator


# --- Discovery ---------------------------------------------------------------


def _gateway_base() -> FacadeData:
    """Return a snapshot with a device that reports a WAN link."""
    return with_network_device(
        facade_data(), SITE, "gw", {"name": "Gateway", "wans": [{"key": "wan1"}]}
    )


@dataclass(frozen=True)
class Discovery:
    """One platform discovery of one site-keyed section."""

    module: ModuleType
    section: str
    record: dict[str, Any]
    # The entity class (or builder) a record gets, and other names patched out.
    entity_class: str
    patched: tuple[str, ...]
    base: Callable[[], FacadeData] = field(default=facade_data)


DISCOVERY: list[Any] = [
    pytest.param(
        Discovery(
            update, "devices", {"name": "Switch"}, "UnifiNetworkDeviceUpdate", ()
        ),
        id="update",
    ),
    pytest.param(
        Discovery(
            button,
            "devices",
            {"name": "Switch"},
            "UnifiInsightsButton",
            ("UnifiClientReconnectButton", "UnifiClientWakeButton"),
        ),
        id="button-device",
    ),
    pytest.param(
        Discovery(
            button,
            "clients",
            {"name": "Phone"},
            "UnifiClientReconnectButton",
            ("UnifiClientWakeButton",),
        ),
        id="button-client",
    ),
    pytest.param(
        Discovery(
            binary_sensor,
            "devices",
            {"name": "Switch"},
            "UnifiInsightsBinarySensor",
            (),
        ),
        id="binary-sensor-device",
    ),
    pytest.param(
        Discovery(
            binary_sensor,
            "site_vpns",
            {"name": "Office"},
            "UnifiInsightsSiteToSiteVpnBinarySensor",
            ("UnifiInsightsBinarySensor", "UnifiInsightsWanLinkBinarySensor"),
            base=_gateway_base,
        ),
        id="binary-sensor-site-vpn",
    ),
    pytest.param(
        Discovery(
            sensor,
            "devices",
            {"name": "Switch"},
            "_discover_device_sensors",
            (
                "_discover_port_sensors",
                "_discover_outlet_sensors",
                "discover_storage_sensors",
                "async_setup_mobility_sensors",
                "UnifiSiteClientSensor",
            ),
        ),
        id="sensor-device",
    ),
    pytest.param(
        Discovery(
            sensor,
            "wifi",
            {"name": "Home"},
            "UnifiWifiClientCountSensor",
            ("async_setup_mobility_sensors",),
        ),
        id="sensor-wifi",
    ),
    pytest.param(
        Discovery(switch, "clients", {"name": "Phone"}, "UnifiClientBlockSwitch", ()),
        id="switch-client",
    ),
    pytest.param(
        Discovery(switch, "wifi", {"name": "Home"}, "UnifiWifiSwitch", ()),
        id="switch-wifi",
    ),
    pytest.param(
        Discovery(
            switch,
            "devices",
            {"outlet_table": [{"index": 1}]},
            "UnifiOutletSwitch",
            (),
        ),
        id="switch-outlet",
    ),
    pytest.param(
        Discovery(
            switch,
            "firewall_rules",
            {"name": "Allow", "enabled": True},
            "UnifiInsightsNetworkRuleSwitch",
            (),
        ),
        id="switch-rule",
    ),
    pytest.param(
        Discovery(
            image, "wifi", {"qr_code": "WIFI:S:Home;;"}, "UnifiWifiQrCodeImage", ()
        ),
        id="image-wifi",
    ),
]


def _built_ids(factory: MagicMock, wanted: str = RECORD_ID) -> set[Any]:
    """Return every ``wanted`` ID an entity class double was built for."""
    return {
        value
        for call in factory.call_args_list
        for value in (*call.args, *call.kwargs.values())
        if value == wanted
    }


async def _discover(
    hass: HomeAssistant,
    module: ModuleType,
    names: tuple[str, ...],
    data: FacadeData,
) -> dict[str, Any]:
    """Run the platform's discovery once and return the patched doubles."""
    entry = MagicMock()
    entry.runtime_data.coordinator = _coordinator(data)
    # Background tasks (the button platform's wake history seed) are not run.
    entry.async_create_background_task.side_effect = lambda _hass, task, **_kwargs: (
        task.close()
    )
    with ExitStack() as stack:
        doubles = {
            name: stack.enter_context(patch.object(module, name)) for name in names
        }
        await module.async_setup_entry(hass, entry, MagicMock())
    return doubles


@pytest.mark.parametrize("case", DISCOVERY)
async def test_discovery_adds_an_entity_for_a_record(
    hass: HomeAssistant, case: Discovery
) -> None:
    """A record in the snapshot gets its entity."""
    data = _snapshot(case.section, case.record, case.base())

    doubles = await _discover(
        hass, case.module, (case.entity_class, *case.patched), data
    )

    assert _built_ids(doubles[case.entity_class]) == {RECORD_ID}


@pytest.mark.parametrize("variant", NO_RECORD)
@pytest.mark.parametrize("case", DISCOVERY)
async def test_discovery_skips_a_missing_or_malformed_record(
    hass: HomeAssistant, case: Discovery, variant: Variant
) -> None:
    """Without a usable record, discovery adds no entity for it."""
    data = variant(case.base(), case.section)

    doubles = await _discover(
        hass, case.module, (case.entity_class, *case.patched), data
    )

    assert _built_ids(doubles[case.entity_class]) == set()


@pytest.mark.parametrize(
    ("variant", "expected"),
    [
        *[pytest.param(param.values[0], set(), id=param.id) for param in NO_RECORD[:4]],
        pytest.param(NO_RECORD[4].values[0], {SITE}, id="no-record"),
        pytest.param(NO_RECORD[5].values[0], {SITE}, id="malformed-record"),
    ],
)
async def test_site_client_sensors_need_the_site_in_the_clients(
    hass: HomeAssistant, variant: Variant, expected: set[str]
) -> None:
    """A site whose clients are loaded gets its client count sensors."""
    names = ("UnifiSiteClientSensor", "async_setup_mobility_sensors")
    with patch.object(sensor, "async_migrate_site_entity", return_value="uid"):
        doubles = await _discover(
            hass, sensor, names, variant(facade_data(), "clients")
        )

    assert _built_ids(doubles["UnifiSiteClientSensor"], SITE) == expected


async def test_stale_port_sweep_keeps_only_loaded_devices() -> None:
    """Only a device record of a loaded site counts as a known device."""
    with patch.object(sensor, "_is_owned_port_sensor") as owned:
        for param in NO_RECORD:
            (variant,) = param.values
            owned.reset_mock()
            await _sweep_known_device_ids(variant(facade_data(), "devices"))
            assert owned.call_args.args[2] == set()
        owned.reset_mock()
        await _sweep_known_device_ids(_snapshot("devices", {"name": "Switch"}))
        assert owned.call_args.args[2] == {RECORD_ID}


async def _sweep_known_device_ids(data: FacadeData) -> None:
    """Run the sensor platform's setup, and so its stale port sweep, once."""
    row = MagicMock(unique_id="stale")
    entry = MagicMock()
    entry.runtime_data.coordinator = _coordinator(data)
    with (
        patch.object(sensor.er, "async_get"),
        patch.object(sensor.er, "async_entries_for_config_entry", return_value=[row]),
        patch.multiple(
            sensor,
            async_setup_mobility_sensors=DEFAULT,
            _discover_device_sensors=DEFAULT,
            _discover_port_sensors=DEFAULT,
            _discover_outlet_sensors=DEFAULT,
            discover_storage_sensors=DEFAULT,
        ),
    ):
        await sensor.async_setup_entry(MagicMock(), entry, MagicMock())


@pytest.mark.parametrize("variant", NO_RECORD)
def test_connected_clients_need_a_client_record(variant: Variant) -> None:
    """Tracking and Wake discovery only see client records."""
    record = {"macAddress": MAC, "type": "WIRED"}
    found = _coordinator(_snapshot("clients", record))
    missing = _coordinator(variant(facade_data(), "clients"))

    assert device_tracker._partition_connected_clients(
        found, track_wifi=True, track_wired=True
    ) == ({MAC: SITE}, set())
    assert device_tracker._partition_connected_clients(
        missing, track_wifi=True, track_wired=True
    ) == ({}, set())
    assert button._connected_client_macs(found) == {MAC}
    assert button._connected_client_macs(missing) == set()


# --- Entity reads ------------------------------------------------------------


def _description(descriptions: Any, key: str) -> Any:
    """Return the entity description with ``key``."""
    return next(desc for desc in descriptions if desc.key == key)


# (section, record, entity builder, the read compared across snapshots)
ENTITIES: list[Any] = [
    pytest.param(
        "devices",
        {
            "firmwareVersion": "1.0",
            "firmwareUpdatable": True,
            "upgradeToFirmware": "1.1",
        },
        lambda c: update.UnifiNetworkDeviceUpdate(c, SITE, RECORD_ID),
        lambda e: (e.installed_version, e.latest_version),
        id="update",
    ),
    pytest.param(
        "devices",
        {"state": "ONLINE"},
        lambda c: binary_sensor.UnifiInsightsBinarySensor(
            c,
            _description(binary_sensor.BINARY_SENSOR_TYPES, "device_status"),
            SITE,
            RECORD_ID,
        ),
        lambda e: e.is_on,
        id="binary-sensor-device",
    ),
    pytest.param(
        "devices",
        {"wans": [{"key": "wan1", "connected": True}]},
        lambda c: binary_sensor.UnifiInsightsWanLinkBinarySensor(
            c, SITE, RECORD_ID, "wan1", "WAN"
        ),
        lambda e: (e.is_on, e.extra_state_attributes),
        id="binary-sensor-wan-link",
    ),
    pytest.param(
        "site_vpns",
        {"name": "Office", "vpn_type": "ipsec"},
        lambda c: binary_sensor.UnifiInsightsSiteToSiteVpnBinarySensor(
            c, SITE, "gw", RECORD_ID, "Office"
        ),
        lambda e: (e._tunnel(), e.extra_state_attributes),
        id="binary-sensor-site-vpn",
    ),
    pytest.param(
        "clients",
        {"name": "Phone"},
        lambda c: button.UnifiClientReconnectButton(c, SITE, RECORD_ID),
        lambda e: (e._get_client_data(), e.available),
        id="button-reconnect",
    ),
    pytest.param(
        "clients",
        {"name": "Phone", "blocked": False},
        lambda c: switch.UnifiClientBlockSwitch(c, SITE, RECORD_ID),
        lambda e: (e._get_client_data(), e.available),
        id="switch-client",
    ),
    pytest.param(
        "wifi",
        {"name": "Home", "enabled": False},
        lambda c: switch.UnifiWifiSwitch(c, SITE, RECORD_ID, {"name": "Seen"}),
        lambda e: e._get_wifi_data(),
        id="switch-wifi",
    ),
    pytest.param(
        "devices",
        {"outlet_table": [{"index": 1, "relay_state": True}]},
        lambda c: switch.UnifiOutletSwitch(
            c, switch.OUTLET_RELAY_SWITCH, SITE, RECORD_ID, outlet_index=1
        ),
        lambda e: e._get_outlet_data(),
        id="switch-outlet",
    ),
    pytest.param(
        "firewall_rules",
        {"name": "Allow", "enabled": False},
        lambda c: switch.UnifiInsightsNetworkRuleSwitch(
            c, switch.FIREWALL_RULE_SWITCH, SITE, RECORD_ID
        ),
        lambda e: (e._get_rule_data(), e.is_on),
        id="switch-rule",
    ),
    pytest.param(
        "devices",
        {"firmwareVersion": "1.0"},
        lambda c: sensor.UnifiInsightsSensor(
            c, _description(sensor.SENSOR_TYPES, "firmware_version"), SITE, RECORD_ID
        ),
        lambda e: e.native_value,
        id="sensor-device",
    ),
    pytest.param(
        "stats",
        {"clients": [{"type": "WIRED"}]},
        lambda c: sensor.UnifiInsightsSensor(
            c, _description(sensor.SENSOR_TYPES, "wired_clients"), SITE, RECORD_ID
        ),
        lambda e: e.native_value,
        id="sensor-stats",
    ),
    pytest.param(
        "stats",
        {"poe_ports": {1: 5.0}, "port_rates": {1: {"tx_bytes_rate": 10}}},
        lambda c: sensor.UnifiPortSensor(
            c,
            _description(sensor.PORT_RATE_SENSOR_TYPES, "port_tx_rate"),
            SITE,
            RECORD_ID,
            1,
        ),
        lambda e: (e._stats_section("poe_ports"), e._get_port_rate_value()),
        id="sensor-port-stats",
    ),
    pytest.param(
        "devices",
        {"outlet_table": [{"index": 1, "outlet_power": "5"}]},
        lambda c: sensor.UnifiOutletSensor(
            c, sensor.OUTLET_SENSOR_TYPES[0], SITE, RECORD_ID, outlet_index=1
        ),
        lambda e: e._find_outlet_data(),
        id="sensor-outlet",
    ),
    pytest.param(
        "clients",
        {"name": "Phone"},
        lambda c: sensor.UnifiSiteClientSensor(
            c, sensor.SITE_CLIENT_SENSOR_TYPES[0], SITE, unique_id="uid"
        ),
        lambda e: e.native_value,
        id="sensor-site-clients",
    ),
    pytest.param(
        "wifi",
        {"ssid": "Home", "num_connected_clients": 3},
        lambda c: sensor.UnifiWifiClientCountSensor(c, SITE, RECORD_ID),
        lambda e: (e.native_value, e.extra_state_attributes),
        id="sensor-wifi",
    ),
]


@pytest.mark.parametrize("variant", NO_RECORD)
@pytest.mark.parametrize(("section", "record", "build", "read"), ENTITIES)
def test_entity_reads_a_missing_or_malformed_record_as_no_record(
    section: str,
    record: dict[str, Any],
    build: Callable[[MagicMock], Any],
    read: Callable[[Any], Any],
    variant: Variant,
) -> None:
    """A missing or malformed section, site or record reads as no record."""
    # A site-to-site VPN entity sits on a gateway device of its site.
    base = _gateway_base() if section == "site_vpns" else None
    coordinator = _coordinator(_snapshot(section, record, base))
    entity = build(coordinator)
    with_record = read(entity)
    coordinator.data = facade_data()
    expected = read(entity)

    coordinator.data = variant(facade_data(), section)

    assert read(entity) == expected
    # The record itself reads differently, so the comparison means something.
    assert with_record != expected


async def test_wifi_qr_image_reads_its_wifi_record(hass: HomeAssistant) -> None:
    """The WiFi QR image has no payload without a usable WiFi record."""
    coordinator = _coordinator(_snapshot("wifi", {"qr_code": "WIFI:S:Home;;"}))
    entity = image.UnifiWifiQrCodeImage(hass, coordinator, SITE, RECORD_ID)

    assert entity._current_payload() == "WIFI:S:Home;;"
    for param in NO_RECORD:
        (variant,) = param.values
        coordinator.data = variant(facade_data(), "wifi")
        assert entity._current_payload() is None


# --- Latest vouchers -----------------------------------------------------------

VOUCHER = {"code": "1234512345", "expiresAt": "2099-01-01T00:00:00Z"}

NO_VOUCHER: list[Any] = [
    pytest.param(without_sections(facade_data(), "latest_vouchers"), id="no-section"),
    pytest.param(facade_data(latest_vouchers=[]), id="malformed-section"),
    pytest.param(facade_data(latest_vouchers={}), id="no-site"),
    pytest.param(with_latest_voucher(facade_data(), SITE, None), id="cleared"),
    pytest.param(facade_data(latest_vouchers={SITE: "invalid"}), id="malformed-record"),
]


@pytest.mark.parametrize("data", NO_VOUCHER)
async def test_voucher_readers_need_the_latest_voucher(
    hass: HomeAssistant, data: FacadeData
) -> None:
    """The voucher QR image and sensors read no voucher without a record."""
    coordinator = _coordinator(with_latest_voucher(facade_data(), SITE, VOUCHER))
    entity = image.UnifiVoucherQrCodeImage(hass, coordinator, SITE, unique_id="uid")
    entity.hass = hass
    assert entity._current_payload() == VOUCHER["code"]
    assert sensor._latest_code_value(coordinator.data, SITE) == VOUCHER["code"]

    coordinator.data = data

    assert entity._current_payload() is None
    with patch.object(image, "async_track_point_in_time") as track:
        entity._reschedule_expiration_timer()
    track.assert_not_called()
    assert sensor._get_latest_record(data, SITE) is None
    assert sensor._latest_code_value(data, SITE) is None


async def test_voucher_image_schedules_the_voucher_expiration(
    hass: HomeAssistant,
) -> None:
    """A live voucher record schedules its expiration."""
    coordinator = _coordinator(with_latest_voucher(facade_data(), SITE, VOUCHER))
    entity = image.UnifiVoucherQrCodeImage(hass, coordinator, SITE, unique_id="uid")
    entity.hass = hass

    with patch.object(image, "async_track_point_in_time") as track:
        entity._reschedule_expiration_timer()

    track.assert_called_once()


# --- Topology ----------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(without_sections(facade_data(), "sites"), id="no-section"),
        pytest.param(facade_data(sites=[]), id="malformed-section"),
        pytest.param(facade_data(sites={}), id="no-site"),
        pytest.param(facade_data(sites={SITE: "invalid"}), id="malformed-record"),
    ],
)
def test_site_display_name_falls_back_to_the_site_id(data: FacadeData) -> None:
    """Without a usable site record, a site is named by its ID."""
    assert topology.site_display_name(data, SITE) == SITE
    named = with_sections(data, sites={SITE: {"name": "Home"}})
    assert topology.site_display_name(named, SITE) == "Home"


# --- Sensor discovery of device statistics ------------------------------------

STATS_GATED = {"tx_rate", "rx_rate", "poe_total_power"}
SWITCH = {"name": "Switch", "features": ["switching"]}


def _device_sensor_keys(data: FacadeData) -> set[str]:
    """Return the stats-gated sensor keys discovered for the switch."""
    entities: list[Any] = []
    sensor._discover_device_sensors(
        _coordinator(data), SITE, RECORD_ID, SWITCH, set(), entities=entities
    )
    return {entity.entity_description.key for entity in entities} & STATS_GATED


@pytest.mark.parametrize(
    ("stats", "expected"),
    [
        pytest.param(
            {"uplink": {"txRateBps": 1}, "poe_total_w": 10.0},
            STATS_GATED,
            id="uplink-and-poe",
        ),
        pytest.param({"tx_bytes_per_sec": 1}, {"tx_rate", "rx_rate"}, id="rates"),
        pytest.param({"poe_ports": {}}, {"poe_total_power"}, id="poe-ports"),
        pytest.param({"cpu": 5}, set(), id="neither"),
    ],
)
def test_device_sensors_need_their_stats(
    stats: dict[str, Any], expected: set[str]
) -> None:
    """Uplink rate and total PoE sensors come only from the device's stats."""
    data = with_network_device(facade_data(), SITE, RECORD_ID, SWITCH, stats=stats)

    assert _device_sensor_keys(data) == expected


@pytest.mark.parametrize("variant", NO_RECORD)
def test_device_sensors_skip_missing_or_malformed_stats(variant: Variant) -> None:
    """Without a usable stats record, no stats-gated sensor is discovered."""
    base = with_network_device(facade_data(), SITE, RECORD_ID, SWITCH)

    assert _device_sensor_keys(variant(base, "stats")) == set()


PORTED_SWITCH = {**SWITCH, "ports": [{"idx": 1, "state": "UP"}]}
PORT_STATS = {"poe_ports": {"1": 5.0}, "port_bytes": {"1": {}}}
PORT_KEYS = {
    (1, key)
    for key in (
        "port_speed",
        "port_tx_bytes",
        "port_rx_bytes",
        "port_tx_rate",
        "port_rx_rate",
    )
}


def _port_sensor_keys(data: FacadeData) -> set[tuple[int, str]]:
    """Return (port, key) of every port sensor discovered for the switch."""
    entities: list[Any] = []
    sensor._discover_port_sensors(
        _coordinator(data),
        SITE,
        RECORD_ID,
        PORTED_SWITCH,
        set(),
        entities=entities,
    )
    return {(entity._port_idx, entity.entity_description.key) for entity in entities}


def test_port_sensors_add_the_poe_sensor_from_the_stats() -> None:
    """The stats fallback adds a port's PoE sensor beside its port sensors."""
    data = with_network_device(
        facade_data(), SITE, RECORD_ID, PORTED_SWITCH, stats=PORT_STATS
    )

    assert _port_sensor_keys(data) == {*PORT_KEYS, (1, "port_poe_power")}


@pytest.mark.parametrize("variant", NO_RECORD)
def test_port_sensors_skip_missing_or_malformed_stats(variant: Variant) -> None:
    """Without a usable stats record, only the port records' sensors are found."""
    base = with_network_device(facade_data(), SITE, RECORD_ID, PORTED_SWITCH)

    assert _port_sensor_keys(variant(base, "stats")) == PORT_KEYS


# --- Wake buttons ------------------------------------------------------------

WAKE_MAC = "02:00:00:00:00:0b"
LIVE_CLIENT = {"name": "Live PC", "macAddress": WAKE_MAC}
# The first site, which a restored button falls back to without a live client.
SITES = {"home": {"name": "Home"}, SITE: {"name": "Office"}}


async def _restored_wake_button(
    hass: HomeAssistant, data: FacadeData
) -> button.UnifiClientWakeButton:
    """Set up the button platform over a registered Wake row; return its button."""
    coordinator = _coordinator(data)
    coordinator.hass = hass
    coordinator.async_get_wake_history = AsyncMock(return_value={})
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_CLIENT_CONTROL: True})
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(coordinator=coordinator)
    er.async_get(hass).async_get_or_create(
        "button",
        DOMAIN,
        wake_unique_id(WAKE_MAC),
        config_entry=entry,
        original_name="Wake Saved PC",
    )
    added: list[Any] = []

    await button.async_setup_entry(hass, entry, EntityCollector(added))
    await hass.async_block_till_done(wait_background_tasks=True)

    (wake,) = [e for e in added if isinstance(e, button.UnifiClientWakeButton)]
    return wake


async def test_restored_wake_button_follows_its_live_client(
    hass: HomeAssistant,
) -> None:
    """A registered Wake button sits on its live client's site, with its name."""
    data = with_client(facade_data(sites=SITES), SITE, RECORD_ID, LIVE_CLIENT)

    wake = await _restored_wake_button(hass, data)

    assert wake.device_info is not None
    assert wake.device_info["identifiers"] == {(DOMAIN, f"site_{SITE}")}
    assert wake.translation_placeholders == {"client_name": "Live PC"}


@pytest.mark.parametrize("variant", NO_RECORD)
async def test_restored_wake_button_without_a_live_client(
    hass: HomeAssistant, variant: Variant
) -> None:
    """Without a usable client record, the first site and saved name are used."""
    data = variant(facade_data(sites=SITES), "clients")

    wake = await _restored_wake_button(hass, data)

    assert wake.device_info is not None
    assert wake.device_info["identifiers"] == {(DOMAIN, "site_home")}
    assert wake.translation_placeholders == {"client_name": "Saved PC"}


@pytest.mark.parametrize(
    ("fallback", "expected"),
    [("Old PC", "Old PC"), ("  ", WAKE_MAC), (None, WAKE_MAC)],
)
@pytest.mark.parametrize("variant", NO_RECORD)
def test_wake_history_name_without_a_live_client(
    variant: Variant, fallback: str | None, expected: str
) -> None:
    """The live name wins; without a usable client the history name, then MAC."""
    coordinator = _coordinator(with_client(facade_data(), SITE, RECORD_ID, LIVE_CLIENT))
    wake = button.UnifiClientWakeButton(coordinator, WAKE_MAC, site_id=SITE)
    wake.async_update_history_name(fallback)
    assert wake.translation_placeholders == {"client_name": "Live PC"}

    coordinator.data = variant(facade_data(), "clients")
    wake.async_update_history_name(fallback)

    assert wake.translation_placeholders == {"client_name": expected}


# --- Service targets ---------------------------------------------------------

# IDs with underscores, so a cached match and the unique ID fallback differ.
TARGET_SITE = "my_site"
TARGET_DEVICE = "dev_1"
TARGET_NO_RECORD = _no_record(TARGET_SITE, TARGET_DEVICE)


def _network_console(hass: HomeAssistant, data: FacadeData) -> MockConfigEntry:
    """Add a loaded console entry whose facade publishes ``data``."""
    entry = MockConfigEntry(domain=DOMAIN, title="Office")
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = MagicMock(coordinator=_coordinator(data))
    return entry


def _target_snapshot() -> FacadeData:
    """Return a snapshot that caches the target device."""
    return with_network_device(facade_data(), TARGET_SITE, TARGET_DEVICE, {})


def _register_target(hass: HomeAssistant, entry: MockConfigEntry, kind: str) -> str:
    """Register the target device or one of its entities; return its ID."""
    if kind == "device":
        return (
            dr.async_get(hass)
            .async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, f"{TARGET_SITE}_{TARGET_DEVICE}")},
            )
            .id
        )
    return (
        er.async_get(hass)
        .async_get_or_create(
            "sensor",
            DOMAIN,
            f"{TARGET_SITE}_{TARGET_DEVICE}_cpu",
            config_entry=entry,
        )
        .entity_id
    )


# What a registry target resolves to when its device is not cached: the
# device identifier splits at its last "_", an entity unique ID at each "_".
UNCACHED_TARGET = {"device": ("1", "my_site_dev"), "entity": ("site", "my")}


@pytest.mark.parametrize("kind", ["device", "entity"])
async def test_registry_target_resolves_to_a_cached_device(
    hass: HomeAssistant, kind: str
) -> None:
    """A cached device splits a registry target into its own site and device."""
    entry = _network_console(hass, _target_snapshot())
    target = _register_target(hass, entry, kind)

    assert service_targets._resolve_network_device_id(hass, target, None, [entry]) == (
        TARGET_DEVICE,
        TARGET_SITE,
        entry,
    )


@pytest.mark.parametrize("variant", TARGET_NO_RECORD)
@pytest.mark.parametrize("kind", ["device", "entity"])
async def test_registry_target_without_a_usable_device_record(
    hass: HomeAssistant, kind: str, variant: Variant
) -> None:
    """Without a usable cached device, the registry ID is split as written."""
    entry = _network_console(hass, variant(_target_snapshot(), "devices"))
    target = _register_target(hass, entry, kind)

    assert service_targets._resolve_network_device_id(hass, target, None, [entry]) == (
        *UNCACHED_TARGET[kind],
        entry,
    )


def _resolve_device_call(hass: HomeAssistant, action: Any) -> Any:
    """Resolve a call for the target device without a site."""
    call = ServiceCall(hass, DOMAIN, "restart_device", {"device_id": TARGET_DEVICE})
    return service_targets.resolve_network_device_call(hass, call, action=action)


@pytest.mark.parametrize("action", ["restart", "power_cycle"])
async def test_device_call_takes_the_site_that_caches_the_device(
    hass: HomeAssistant, action: Any
) -> None:
    """Without a site, the call takes the site whose devices hold the device."""
    data = with_sections(
        _target_snapshot(), devices={"other": [], **_target_snapshot()["devices"]}
    )
    entry = _network_console(hass, data)

    target = _resolve_device_call(hass, action)

    assert (target.entry, target.device_id, target.site_id) == (
        entry,
        TARGET_DEVICE,
        TARGET_SITE,
    )


@pytest.mark.parametrize(
    ("variant", "key"),
    [
        *[
            pytest.param(param.values[0], "{action}_site_required", id=param.id)
            for param in TARGET_NO_RECORD[:2]
        ],
        *[
            pytest.param(param.values[0], "device_not_on_any_console", id=param.id)
            for param in TARGET_NO_RECORD[2:]
        ],
    ],
)
@pytest.mark.parametrize("action", ["restart", "power_cycle"])
async def test_device_call_without_a_usable_device_record(
    hass: HomeAssistant, action: Any, variant: Variant, key: str
) -> None:
    """
    Without a usable cached device, the call is refused.

    A missing or malformed section may still hold the device, so only the site
    is missing; a loaded section without the device proves it is elsewhere.
    """
    _network_console(hass, variant(_target_snapshot(), "devices"))

    with pytest.raises(ServiceValidationError) as err:
        _resolve_device_call(hass, action)

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == key.format(action=action)
    assert err.value.translation_placeholders == {"device_id": TARGET_DEVICE}


# --- Topology sections -------------------------------------------------------

SWITCH_MAC = "02:00:00:00:00:01"
CLIENT_MAC = "02:00:00:00:00:02"
TOPOLOGY_RECORDS = {
    "devices": (RECORD_ID, {"name": "Switch", "macAddress": SWITCH_MAC}),
    "clients": (
        RECORD_ID,
        {"name": "TV", "type": "WIRED", "macAddress": CLIENT_MAC},
    ),
    "client_links": (CLIENT_MAC, {"sw_mac": SWITCH_MAC, "sw_port": 4, "vlan": 10}),
}


def _topology_snapshot() -> FacadeData:
    """Return a snapshot with a switch, a wired client and the client's link."""
    sections: dict[str, Any] = {
        section: {SITE: {record_id: record}}
        for section, (record_id, record) in TOPOLOGY_RECORDS.items()
    }
    return facade_data(**sections)


def _topology_shape(data: FacadeData) -> dict[str, Any]:
    """Build the site's topology and return its shape."""
    snapshot = topology.build_site_topology(
        data, "entry", SITE, node_key=b"k" * 32, ha_device_ids={}
    )
    nodes = snapshot["nodes"]
    return {
        "devices": sum(node["kind"] != "client" for node in nodes),
        "clients": [node.get("vlan_id") for node in nodes if node["kind"] == "client"],
        "edges": [
            (edge["medium"], edge.get("parent_port")) for edge in snapshot["edges"]
        ],
        "unresolved": sorted(entry["reason"] for entry in snapshot["unresolved"]),
    }


def test_topology_joins_devices_clients_and_client_links() -> None:
    """A wired client hangs off its switch port, on its link's VLAN."""
    assert _topology_shape(_topology_snapshot()) == {
        "devices": 1,
        "clients": [10],
        "edges": [("wired", 4)],
        "unresolved": ["no_uplink_data"],
    }


# What a site's topology is without a usable record of one section.
TOPOLOGY_WITHOUT = {
    "devices": {
        "devices": 0,
        "clients": [10],
        "edges": [],
        "unresolved": ["parent_not_found"],
    },
    "clients": {
        "devices": 1,
        "clients": [],
        "edges": [],
        "unresolved": ["no_uplink_data"],
    },
    "client_links": {
        "devices": 1,
        "clients": [None],
        "edges": [],
        "unresolved": ["no_uplink_data", "no_uplink_data"],
    },
}


@pytest.mark.parametrize(
    ("section", "variant"),
    [
        pytest.param(section, param.values[0], id=f"{section}-{param.id}")
        for section, (record_id, _record) in TOPOLOGY_RECORDS.items()
        for param in _no_record(SITE, record_id)
    ],
)
def test_topology_without_a_usable_section_record(
    section: str, variant: Variant
) -> None:
    """A missing or malformed section, site or record reads as no record."""
    data = variant(_topology_snapshot(), section)

    assert _topology_shape(data) == TOPOLOGY_WITHOUT[section]
