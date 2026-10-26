# Copyright (c) 2026 Ruaan Deysel
"""Tests for UniFi Insights sensors."""

import dataclasses
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_VERIFY_SSL,
    PERCENTAGE,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTemperature,
)
from homeassistant.helpers.entity_registry import (
    RegistryEntryDisabler,
    RegistryEntryHider,
)
from homeassistant.helpers.typing import UNDEFINED
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api.protect.models.sensor import (
    Sensor as ProtectSensorModel,
)

from custom_components.unifi_insights.const import (
    CONF_CONNECTION_TYPE,
    CONF_CONSOLE_ID,
    CONNECTION_TYPE_LOCAL,
    DOMAIN,
)
from custom_components.unifi_insights.data_transforms import get_site_gateway_id
from custom_components.unifi_insights.sensor import (
    NVR_SENSOR_TYPES,
    OUTLET_SENSOR_TYPES,
    PARALLEL_UPDATES,
    PORT_RATE_SENSOR_TYPES,
    PORT_SENSOR_TYPES,
    PROTECT_SENSOR_TYPES,
    SENSOR_TYPES,
    SFP_SENSOR_TYPES,
    SITE_CLIENT_SENSOR_TYPES,
    SITE_INTERNET_ACTIVITY_SENSOR_TYPES,
    STORAGE_SENSOR_TYPES,
    UnifiInsightsSensor,
    UnifiOutletSensor,
    UnifiPortSensor,
    UnifiProtectSecuritySensorEntityDescription,
    UnifiProtectSensor,
    UnifiProtectSensorEntityDescription,
    UnifiSiteClientSensor,
    UnifiSiteInternetActivitySensor,
    UnifiStorageSensor,
    UnifiVoucherSensor,
    UnifiWifiClientCountSensor,
    _bytes_to_gb,
    _calculate_storage_available,
    _calculate_storage_percent,
    _discover_outlet_sensors,
    _discover_protect_sensors,
    _discover_site_internet_activity_sensors,
    _get_client_type,
    _get_port_label,
    _get_storage_bytes,
    _has_protect_stat,
    _has_storage_info,
    _migrate_sensor_units,
    _outlet_has_metering,
    async_setup_entry,
    bytes_to_bits,
    bytes_to_megabits,
    format_uptime,
    get_network_device_temperature,
)
from tests.fixtures.assertions import patch_attribute, require_not_none
from tests.fixtures.entities import EntityCollector
from tests.fixtures.facade_data import (
    facade_data,
    set_availability,
    with_device_fields,
    with_device_stats,
    with_device_stats_fields,
    with_network_device,
    with_protect_device,
    with_sections,
    without_sections,
)
from tests.fixtures.library_responses import (
    SAMPLE_KEYPAD_FOB,
    SAMPLE_THREAD_LINK_STATION,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.entity import Entity

    from custom_components.unifi_insights.sensor import (
        UnifiInsightsSensorEntityDescription,
        UnifiStorageSensorEntityDescription,
    )


def _with_value(data: Mapping[str, Any], path: tuple[str, ...], value: Any) -> Any:
    """Return ``data`` with ``value`` at ``path`` (a section, then keys)."""

    def replaced(node: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
        head, *rest = keys
        return {**node, head: replaced(node[head], tuple(rest)) if rest else value}

    section, *keys = path
    new = replaced(data[section], tuple(keys)) if keys else value
    return with_sections(data, **{section: new})


def _create_mock_model(**kwargs):
    """Create a mock model that properly handles model_dump."""
    mock = MagicMock()
    for key, value in kwargs.items():
        setattr(mock, key, value)
    mock.model_dump = MagicMock(return_value=kwargs)
    return mock


@pytest.fixture
def mock_coordinator() -> MagicMock:
    """Create a mock coordinator for sensor tests."""
    coordinator = MagicMock()
    coordinator.network_client = MagicMock()
    coordinator.network_client.base_url = "https://192.168.1.1"
    coordinator.protect_client = MagicMock()
    coordinator.protect_client.base_url = "https://192.168.1.1"

    # Setup coordinator data structure
    coordinator.data = facade_data(
        sites={"site1": {"id": "site1", "meta": {"name": "Default"}}},
        devices={
            "site1": {
                "device1": {
                    "id": "device1",
                    "name": "Test Switch",
                    "model": "USW-24",
                    "macAddress": "AA:BB:CC:DD:EE:FF",
                    "ipAddress": "192.168.1.10",
                    "state": "ONLINE",
                    "firmwareVersion": "6.5.55",
                    "interfaces": {
                        "ports": [
                            {
                                "idx": 1,
                                "state": "UP",
                                "speedMbps": 1000,
                                "poe": {"enabled": True, "power": 15.5},
                                "stats": {"txBytes": 1000000, "rxBytes": 2000000},
                            },
                            {
                                "idx": 2,
                                "state": "DOWN",
                                "speedMbps": 0,
                            },
                            {
                                "idx": 25,
                                "state": "UP",
                                "speedMbps": 10000,
                                "media": "SFP+",
                                "name": "SFP+ 1",
                                "is_uplink": True,
                                "sfp_found": True,
                                "sfp_part": "UC-DAC-SFP+",
                                "sfp_vendor": "Ubiquiti Inc.",
                                "sfp_serial": "SN12345",
                                "sfp_compliance": "DAC",
                            },
                            {
                                "idx": 26,
                                "state": "DOWN",
                                "speedMbps": 10000,
                                "media": "SFP+",
                                "name": "SFP+ 2",
                                "sfp_found": False,
                            },
                        ]
                    },
                },
                "device2": {
                    "id": "device2",
                    "name": "Test Gateway",
                    "model": "UDM-Pro",
                    "macAddress": "11:22:33:44:55:66",
                    "ipAddress": "192.168.1.1",
                    "state": "ONLINE",
                    "firmwareVersion": "3.0.0",
                    "hasTemperature": True,
                    "generalTemperature": 44.5,
                    "features": {"gateway": True, "switching": True},
                    "interfaces": {"ports": []},
                },
            }
        },
        clients={"site1": {"client1": {"id": "client1", "name": "Test Client"}}},
        stats={
            "site1": {
                "device1": {
                    "id": "device1",
                    "cpuUtilizationPct": 15.5,
                    "memoryUtilizationPct": 30.2,
                    "uptimeSec": 86400,
                    "uplink": {"txRateBps": 1000000, "rxRateBps": 500000},
                    "clients": [
                        {"id": "client1", "type": "WIRED", "uplinkDeviceId": "device1"},
                        {
                            "id": "client2",
                            "type": "WIRELESS",
                            "uplinkDeviceId": "device1",
                        },
                    ],
                },
                "device2": {
                    "id": "device2",
                    "cpuUtilizationPct": 25.0,
                    "memoryUtilizationPct": 45.0,
                    "uptimeSec": 172800,
                    "clients": [],
                },
            }
        },
        protect={
            "sensors": {
                "sensor1": {
                    "id": "sensor1",
                    "name": "Kitchen Sensor",
                    "state": "CONNECTED",
                    "stats": {
                        "temperature": {"value": 22.5},
                        "humidity": {"value": 45},
                        "light": {"value": 500},
                    },
                    "batteryStatus": {"percentage": 85, "isLow": False},
                }
            },
            "cameras": {},
            "lights": {},
            "nvrs": {
                "nvr1": {
                    "id": "nvr1",
                    "name": "Test NVR",
                    "state": "CONNECTED",
                    "version": "4.0.0",
                    "storageUsedBytes": 500000000000,  # 500 GB
                    "storageTotalBytes": 1000000000000,  # 1 TB
                }
            },
            "viewers": {},
            "chimes": {},
            "liveviews": {},
            "events": {},
        },
    )
    coordinator.get_site = MagicMock(return_value=coordinator.data["sites"]["site1"])
    coordinator.last_update_success = True
    return coordinator


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.runtime_data = MagicMock()
    entry.runtime_data.mobility_coordinator = None
    return entry


class TestFormatUptime:
    """Tests for format_uptime function."""

    def test_format_uptime_none(self):
        """Test format_uptime with None."""
        assert format_uptime(None) is None

    def test_format_uptime_minutes_only(self):
        """Test format_uptime with just minutes."""
        assert format_uptime(300) == "5m"

    def test_format_uptime_hours_and_minutes(self):
        """Test format_uptime with hours and minutes."""
        assert format_uptime(3660) == "1h 1m"

    def test_format_uptime_days(self):
        """Test format_uptime with days."""
        assert format_uptime(86400) == "1d 0h 0m"

    def test_format_uptime_all_components(self):
        """Test format_uptime with days, hours, and minutes."""
        assert format_uptime(90061) == "1d 1h 1m"

    def test_format_uptime_zero(self):
        """Test format_uptime with zero."""
        assert format_uptime(0) == "0m"


class TestBytesToMegabits:
    """Tests for bytes_to_megabits function."""

    def test_bytes_to_megabits_none(self):
        """Test bytes_to_megabits with None."""
        assert bytes_to_megabits(None) is None

    def test_bytes_to_megabits_zero(self):
        """Test bytes_to_megabits with zero."""
        assert bytes_to_megabits(0) == 0.0

    def test_bytes_to_megabits_calculation(self):
        """Test bytes_to_megabits calculation."""
        # 1,000,000 bytes/sec = 8 Mbps
        assert bytes_to_megabits(1000000) == 8.0

    def test_bytes_to_megabits_rounding(self):
        """Test bytes_to_megabits rounding."""
        # 125000 bytes/sec = 1 Mbps
        assert bytes_to_megabits(125000) == 1.0


class TestBytesToBits:
    """Tests for bytes_to_bits function."""

    def test_bytes_to_bits_none(self):
        """Test bytes_to_bits with None."""
        assert bytes_to_bits(None) is None

    def test_bytes_to_bits_zero(self):
        """Test bytes_to_bits with zero."""
        assert bytes_to_bits(0) == 0

    def test_bytes_to_bits_calculation(self):
        """Test bytes_to_bits calculation."""
        # 1000 bytes/sec = 8000 bits/sec
        assert bytes_to_bits(1000) == 8000

    def test_bytes_to_bits_large_value(self):
        """Test bytes_to_bits with large value."""
        # 1,000,000 bytes/sec = 8,000,000 bits/sec
        assert bytes_to_bits(1000000) == 8000000


class TestGetClientType:
    """Tests for _get_client_type helper function."""

    def test_get_client_type_wired(self):
        """Test _get_client_type returns WIRED for wired clients."""
        assert _get_client_type({"type": "WIRED"}) == "WIRED"
        assert _get_client_type({"type": "wired"}) == "WIRED"

    def test_get_client_type_wireless(self):
        """Test _get_client_type returns WIRELESS for wireless clients."""
        assert _get_client_type({"type": "WIRELESS"}) == "WIRELESS"
        assert _get_client_type({"type": "wireless"}) == "WIRELESS"

    def test_get_client_type_connection_type_fallback(self):
        """Test _get_client_type uses connection_type as fallback."""
        assert _get_client_type({"connection_type": "WIRED"}) == "WIRED"
        assert _get_client_type({"connection_type": "WIRELESS"}) == "WIRELESS"

    def test_get_client_type_unknown_returns_as_is(self):
        """Test _get_client_type returns unknown types as-is (line 73)."""
        # Unknown type should be returned as uppercase
        assert _get_client_type({"type": "UNKNOWN"}) == "UNKNOWN"
        assert _get_client_type({"type": "other"}) == "OTHER"

        # Empty type returns empty string
        assert _get_client_type({"type": ""}) == ""
        assert _get_client_type({}) == ""

        # None type returns empty string (via str(None) -> "NONE")
        assert _get_client_type({"type": None}) == ""


class TestSensorTypes:
    """Tests for sensor type definitions."""

    def test_parallel_updates(self):
        """Test PARALLEL_UPDATES is set to 0."""
        assert PARALLEL_UPDATES == 0

    def test_sensor_types_defined(self):
        """Test that sensor types are defined."""
        assert len(SENSOR_TYPES) > 0

    def test_port_sensor_types_defined(self):
        """Test that port sensor types are defined."""
        assert len(PORT_SENSOR_TYPES) > 0

    def test_protect_sensor_types_defined(self):
        """Test that protect sensor types are defined."""
        assert len(PROTECT_SENSOR_TYPES) > 0


class TestUnifiInsightsSensor:
    """Tests for UnifiInsightsSensor."""

    async def test_sensor_cpu_usage(self, hass: HomeAssistant, mock_coordinator):
        """Test CPU usage sensor."""
        description = next(s for s in SENSOR_TYPES if s.key == "cpu_usage")

        sensor = UnifiInsightsSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        # Reads the read-model facts built from the stats
        assert description.value_fn is None
        assert sensor.native_value == 15.5

    async def test_sensor_memory_usage(self, hass: HomeAssistant, mock_coordinator):
        """Test memory usage sensor."""
        description = next(s for s in SENSOR_TYPES if s.key == "memory_usage")

        sensor = UnifiInsightsSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        assert sensor.native_value == 30.2

    async def test_sensor_uptime(self, hass: HomeAssistant, mock_coordinator):
        """Test uptime sensor."""
        description = next(s for s in SENSOR_TYPES if s.key == "uptime")

        sensor = UnifiInsightsSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        # 86400 seconds = 1 day
        assert sensor.native_value == "1d 0h 0m"

    async def test_sensor_tx_rate(self, hass: HomeAssistant, mock_coordinator):
        """Test TX rate sensor returns bits per second."""
        description = next(s for s in SENSOR_TYPES if s.key == "tx_rate")

        sensor = UnifiInsightsSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        # The public txRateBps is already bits/s (#337): 1000000 stays 1000000,
        # it is no longer multiplied by 8.
        assert sensor.native_value == 1000000

    async def test_sensor_firmware_version(self, hass: HomeAssistant, mock_coordinator):
        """Test firmware version sensor."""
        description = next(s for s in SENSOR_TYPES if s.key == "firmware_version")

        sensor = UnifiInsightsSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        assert sensor.native_value == "6.5.55"

    async def test_sensor_general_temperature(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test network device temperature sensor from device data."""
        description = next(s for s in SENSOR_TYPES if s.key == "general_temperature")

        sensor = UnifiInsightsSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device2",
        )

        assert sensor.native_value == 44.5
        assert sensor.native_unit_of_measurement == UnitOfTemperature.CELSIUS

    async def test_sensor_wired_clients(self, hass: HomeAssistant, mock_coordinator):
        """Test wired clients sensor."""
        description = next(s for s in SENSOR_TYPES if s.key == "wired_clients")

        sensor = UnifiInsightsSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        # One wired client in the test data
        assert sensor.native_value == 1

    async def test_sensor_wireless_clients(self, hass: HomeAssistant, mock_coordinator):
        """Test wireless clients sensor."""
        description = next(s for s in SENSOR_TYPES if s.key == "wireless_clients")

        sensor = UnifiInsightsSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        # One wireless client in the test data
        assert sensor.native_value == 1

    async def test_sensor_no_stats(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test sensor when no stats are available."""
        description = next(s for s in SENSOR_TYPES if s.key == "cpu_usage")

        # Remove stats for device
        mock_coordinator.data = with_device_stats(
            mock_coordinator.data, "site1", "device1", None
        )

        sensor = UnifiInsightsSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        assert sensor.native_value is None

    async def test_sensor_unique_id(self, hass: HomeAssistant, mock_coordinator):
        """Test sensor unique ID format."""
        description = next(s for s in SENSOR_TYPES if s.key == "cpu_usage")

        sensor = UnifiInsightsSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        assert sensor.unique_id == "site1_device1_cpu_usage"


class TestUplinkRateZeroIsZeroNotNone:
    """
    Headline regression: a literal 0 rate must read 0, never unknown.

    `or` is falsy-based, so `0 or <fallback>` silently discards a real 0
    reading. These tests assert both `== 0` and `is not None` at the
    value_fn level, because `assert not x` would also pass for None and
    is exactly the mistake this bug class is guarding against.
    """

    @pytest.mark.parametrize(
        ("sensor_key", "api_key"),
        [
            ("tx_rate", "txRateBps"),
            ("rx_rate", "rxRateBps"),
        ],
    )
    async def test_uplink_rate_zero_is_zero_not_none(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        sensor_key: str,
        api_key: str,
    ) -> None:
        """A device reporting a literal 0 B/s rate must yield 0, not None."""
        description = next(s for s in SENSOR_TYPES if s.key == sensor_key)
        mock_coordinator.data = with_device_stats_fields(
            mock_coordinator.data,
            "site1",
            "device1",
            uplink={
                api_key: 0,
            },
        )

        sensor = UnifiInsightsSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        assert sensor.native_value == 0
        assert sensor.native_value is not None

    @pytest.mark.parametrize(
        ("sensor_key", "api_key"),
        [
            ("tx_rate", "txRateBps"),
            ("rx_rate", "rxRateBps"),
        ],
    )
    async def test_uplink_rate_absent_key_stays_unknown(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        sensor_key: str,
        api_key: str,
    ) -> None:
        """
        Negative test: an absent key still yields None (state `unknown`).

        This guards against "fixing" the bug by defaulting to 0, which
        would fabricate a reading for a device that never reported one.
        """
        description = next(s for s in SENSOR_TYPES if s.key == sensor_key)
        stats = dict(mock_coordinator.data["stats"]["site1"]["device1"])
        stats.pop("uplink", None)
        for absent_key in (
            "txRateBps",
            "tx_rate_bps",
            "tx_bytes_per_sec",
            "rxRateBps",
            "rx_rate_bps",
            "rx_bytes_per_sec",
        ):
            stats.pop(absent_key, None)
        mock_coordinator.data = with_device_stats(
            mock_coordinator.data, "site1", "device1", stats
        )

        sensor = UnifiInsightsSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        assert sensor.native_value is None

    async def test_non_numeric_lookup_without_fallback_still_returns_none(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """
        Sanity check that the falsy-zero fix was not applied too broadly.

        `firmware_version` is a non-numeric lookup with no fallback
        candidate, and this PR deliberately leaves it alone. An absent
        value must therefore still yield `None` (rendered as `Unknown`)
        rather than being coerced into a value, confirming the sweep did
        not turn every lookup into "return something".
        """
        description = next(s for s in SENSOR_TYPES if s.key == "firmware_version")
        device = dict(mock_coordinator.data["devices"]["site1"]["device1"])
        device.pop("firmwareVersion", None)
        mock_coordinator.data = with_network_device(
            mock_coordinator.data, "site1", "device1", device
        )

        sensor = UnifiInsightsSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        # No fallback candidate exists, so absence still yields None.
        assert sensor.native_value is None


class TestUplinkRateStateLevel:
    """
    State-level proof of the user-visible symptom, not just the value_fn.

    tx_rate/rx_rate carry suggested_unit_of_measurement=Mbit/s, so the
    stored HA state is the *converted* value, not the raw native value.
    Comparing against the string "0" would be a false negative for any
    non-integer conversion result, so this asserts on the float value.
    """

    @pytest.fixture
    def real_config_entry(self) -> MockConfigEntry:
        """
        Build a genuine, registerable MockConfigEntry.

        The module-level `mock_config_entry` fixture in this file is a bare
        MagicMock used by the async_setup_entry()-level tests above; it is
        never added to hass.config_entries, so
        `hass.config_entries.async_setup()` cannot find it. This test needs
        the real thing to drive a full integration setup.
        """
        return MockConfigEntry(
            version=1,
            minor_version=0,
            domain=DOMAIN,
            title="UniFi Insights (Local)",
            data={
                CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_api_key",
                CONF_VERIFY_SSL: False,
            },
            options={},
            source="user",
            unique_id="test_api_key",
            entry_id="zero_uplink_test_entry_id",
        )

    async def test_zero_uplink_rate_state_is_zero_not_unknown(
        self,
        *,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        real_config_entry: MockConfigEntry,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        mock_local_auth: MagicMock,
        enable_custom_integrations: None,
    ) -> None:
        """A device reporting txRateBps=0 must show sensor state 0, not unknown."""
        real_config_entry.add_to_hass(hass)

        mock_network_client.sites.get_all.return_value = [
            {"id": "default", "name": "Default", "desc": "Default"}
        ]
        mock_network_client.devices.get_all.return_value = [
            {
                "id": "zero_uplink_device",
                "name": "Zero Uplink Switch",
                "model": "USW-24",
                "mac": "AA:BB:CC:DD:EE:00",
                "macAddress": "AA:BB:CC:DD:EE:00",
                "state": "ONLINE",
                "ipAddress": "192.168.1.50",
            }
        ]
        mock_network_client.devices.get_statistics = AsyncMock(
            return_value={"uplink": {"txRateBps": 0, "rxRateBps": 0}}
        )

        await hass.config_entries.async_setup(real_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = entity_registry.async_get_entity_id(
            "sensor", DOMAIN, "default_zero_uplink_device_tx_rate"
        )
        assert entity_id is not None, "tx_rate sensor was not created"

        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state not in ("unknown", "unavailable")
        assert float(state.state) == 0.0


class TestUnifiPortSensor:
    """Tests for UnifiPortSensor."""

    async def test_port_poe_power(self, hass: HomeAssistant, mock_coordinator):
        """Test PoE power port sensor."""
        description = next(s for s in PORT_SENSOR_TYPES if s.key == "port_poe_power")

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        assert sensor.native_value == 15.5
        assert sensor.translation_placeholders == {"port_label": "Port 1"}

    def test_port_poe_power_value_fn_zero_watts_is_zero_not_none(self):
        """
        A port drawing 0 W (e.g. the flap case) must report 0, not unknown.

        `get_field(port, "poe_power_w") or get_field(port, "poe", ...).get(
        "power") or ...` would discard a real 0.0 in favour of the next
        candidate. Exercised directly at the value_fn level, bypassing the
        coordinator-stats poe_ports shortcut.
        """
        description = next(s for s in PORT_SENSOR_TYPES if s.key == "port_poe_power")
        port = {"poe": {"enabled": True, "power": 0.0, "good": False}}

        value = require_not_none(description.value_fn)(port)

        assert value == 0.0
        assert value is not None

    async def test_port_speed(self, hass: HomeAssistant, mock_coordinator):
        """Test port speed sensor."""
        description = next(s for s in PORT_SENSOR_TYPES if s.key == "port_speed")
        assert description.device_class == SensorDeviceClass.DATA_RATE
        assert (
            description.native_unit_of_measurement == UnitOfDataRate.MEGABITS_PER_SECOND
        )

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        assert sensor.native_value == 1000

    async def test_port_sensor_unique_id(self, hass: HomeAssistant, mock_coordinator):
        """Test port sensor unique ID includes port index."""
        description = next(s for s in PORT_SENSOR_TYPES if s.key == "port_speed")

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        assert sensor.unique_id == "device1_port_speed_1"

    async def test_port_sensor_available_when_port_up(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test port sensor availability when port is UP."""
        description = next(s for s in PORT_SENSOR_TYPES if s.key == "port_speed")

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        assert sensor.available is True

    async def test_port_sensor_unavailable_when_port_down(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test port sensor availability when port is DOWN."""
        description = next(s for s in PORT_SENSOR_TYPES if s.key == "port_speed")

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=2,  # Port 2 is DOWN in test data
        )

        assert sensor.available is False

    async def test_port_sensor_no_device_data(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test port sensor when device data is removed after initialization."""
        description = next(s for s in PORT_SENSOR_TYPES if s.key == "port_speed")

        # Create sensor with valid data
        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        # Now remove device data to simulate device going offline
        mock_coordinator.data = _with_value(
            mock_coordinator.data, ("devices", "site1", "device1"), None
        )

        # Value should be None when device data is missing
        assert sensor.native_value is None


class TestGetPortLabel:
    """Tests for _get_port_label helper."""

    def test_port_label_uses_api_name(self):
        """Test _get_port_label returns the API name when available."""
        port = {"name": "SFP+ 1", "media": "SFP+", "idx": 25}
        assert _get_port_label(port, 25) == "SFP+ 1"

    def test_port_label_sfp_fallback(self):
        """Test _get_port_label returns media-based label for SFP ports without name."""
        port = {"media": "SFP+", "idx": 10}
        assert _get_port_label(port, 10) == "SFP+ 10"

    def test_port_label_regular_port(self):
        """Test _get_port_label returns generic label for regular ports."""
        port = {"media": "GE", "idx": 1}
        assert _get_port_label(port, 1) == "Port 1"

    def test_port_label_no_media(self):
        """Test _get_port_label returns generic label when no media info."""
        port = {"idx": 5}
        assert _get_port_label(port, 5) == "Port 5"

    @pytest.mark.parametrize("media", [None, 1])
    def test_port_label_non_string_media(self, media):
        """Test _get_port_label falls back to the generic label for non-string media."""
        port = {"name": "Port 7", "media": media, "idx": 7}
        assert _get_port_label(port, 7) == "Port 7"

    def test_port_label_ignores_generic_name(self):
        """Test _get_port_label ignores default 'Port N' name and uses media."""
        port = {"name": "Port 25", "media": "SFP+", "idx": 25}
        assert _get_port_label(port, 25) == "SFP+ 25"


class TestSFPPortSensors:
    """Tests for SFP port sensor features."""

    async def test_sfp_sensor_types_defined(self):
        """Test that SFP sensor types are defined."""
        assert len(SFP_SENSOR_TYPES) == 4
        keys = {s.key for s in SFP_SENSOR_TYPES}
        assert keys == {
            "port_sfp_module",
            "port_sfp_vendor",
            "port_sfp_compliance",
            "port_sfp_serial",
        }

    async def test_sfp_module_sensor_value(self, hass: HomeAssistant, mock_coordinator):
        """Test SFP module sensor returns sfp_part."""
        description = next(s for s in SFP_SENSOR_TYPES if s.key == "port_sfp_module")
        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=25,
            port_label="SFP+ 1",
        )
        assert sensor.native_value == "UC-DAC-SFP+"

    async def test_sfp_vendor_sensor_value(self, hass: HomeAssistant, mock_coordinator):
        """Test SFP vendor sensor returns sfp_vendor."""
        description = next(s for s in SFP_SENSOR_TYPES if s.key == "port_sfp_vendor")
        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=25,
            port_label="SFP+ 1",
        )
        assert sensor.native_value == "Ubiquiti Inc."

    async def test_sfp_compliance_sensor_value(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test SFP compliance sensor returns sfp_compliance."""
        description = next(
            s for s in SFP_SENSOR_TYPES if s.key == "port_sfp_compliance"
        )
        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=25,
            port_label="SFP+ 1",
        )
        assert sensor.native_value == "DAC"

    async def test_sfp_serial_sensor_value(self, hass: HomeAssistant, mock_coordinator):
        """Test SFP serial sensor returns sfp_serial."""
        description = next(s for s in SFP_SENSOR_TYPES if s.key == "port_sfp_serial")
        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=25,
            port_label="SFP+ 1",
        )
        assert sensor.native_value == "SN12345"

    async def test_sfp_sensor_available_even_when_port_down(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test SFP sensors stay available even when port state is DOWN."""
        description = next(s for s in SFP_SENSOR_TYPES if s.key == "port_sfp_module")
        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=26,  # Port 26 is DOWN but has SFP media
        )
        # SFP info sensors stay available regardless of port state
        assert sensor.available is True

    async def test_port_label_in_sensor_name(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test port label is used in sensor name."""
        description = next(s for s in PORT_SENSOR_TYPES if s.key == "port_speed")
        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=25,
            port_label="SFP+ 1",
        )
        assert sensor.translation_placeholders == {"port_label": "SFP+ 1"}

    async def test_sfp_sensor_name_uses_port_label(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test SFP sensor name includes port label."""
        description = next(s for s in SFP_SENSOR_TYPES if s.key == "port_sfp_module")
        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=25,
            port_label="SFP+ 1",
        )
        assert sensor.translation_placeholders == {"port_label": "SFP+ 1"}

    async def test_port_extra_state_attributes(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test port sensor extra_state_attributes include port type info."""
        description = next(s for s in PORT_SENSOR_TYPES if s.key == "port_speed")
        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=25,
            port_label="SFP+ 1",
        )
        attrs = sensor.extra_state_attributes
        assert attrs is not None
        assert attrs["media_type"] == "SFP+"
        assert attrs["is_uplink"] is True
        assert attrs["port_name"] == "SFP+ 1"
        assert attrs["sfp_module_present"] is True

    async def test_port_extra_state_attributes_regular_port(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test regular port has no extra attributes if no type info."""
        description = next(s for s in PORT_SENSOR_TYPES if s.key == "port_speed")
        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )
        attrs = sensor.extra_state_attributes
        # Regular port without media/is_uplink/etc. has no attributes
        assert attrs is None

    async def test_setup_creates_sfp_sensors(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test async_setup_entry creates SFP sensors for SFP ports with modules."""
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        added_entities: list = []

        add_entities = EntityCollector(added_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        sfp_sensors = [
            e
            for e in added_entities
            if isinstance(e, UnifiPortSensor)
            and e.entity_description.key.startswith("port_sfp_")
        ]
        # Port 25 has sfp_found=True → 4 SFP sensors
        # Port 26 has sfp_found=False → 0 SFP sensors
        assert len(sfp_sensors) == 4

    async def test_setup_rediscovery_dedupes_sfp_sensors(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Re-running discovery on unchanged SFP port data adds no duplicates."""
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        added_entities: list = []

        add_entities = EntityCollector(added_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        sfp_sensors_before = [
            e
            for e in added_entities
            if isinstance(e, UnifiPortSensor)
            and e.entity_description.key.startswith("port_sfp_")
        ]
        assert len(sfp_sensors_before) == 4

        listener()

        sfp_sensors_after = [
            e
            for e in added_entities
            if isinstance(e, UnifiPortSensor)
            and e.entity_description.key.startswith("port_sfp_")
        ]
        assert len(sfp_sensors_after) == 4

    async def test_setup_port_labels_passed(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test async_setup_entry passes port labels to port sensors."""
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        added_entities: list = []

        add_entities = EntityCollector(added_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        # Find a speed sensor for port 25 (SFP+)
        sfp_speed = [
            e
            for e in added_entities
            if isinstance(e, UnifiPortSensor)
            and e.entity_description.key == "port_speed"
            and e._port_idx == 25
        ]
        assert len(sfp_speed) == 1
        # The label is exposed via the translation placeholder; resolving
        # entity.name requires a live entity platform, which this unit test
        # does not attach.
        assert sfp_speed[0].translation_placeholders["port_label"] == "SFP+ 1"


class TestUnifiProtectSensor:
    """Tests for UnifiProtectSensor."""

    async def test_temperature_sensor(self, hass: HomeAssistant, mock_coordinator):
        """Test temperature sensor."""
        description = next(s for s in PROTECT_SENSOR_TYPES if s.key == "temperature")

        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor1",
        )

        assert sensor.native_value == 22.5
        assert sensor.native_unit_of_measurement == UnitOfTemperature.CELSIUS

    async def test_humidity_sensor(self, hass: HomeAssistant, mock_coordinator):
        """Test humidity sensor."""
        description = next(s for s in PROTECT_SENSOR_TYPES if s.key == "humidity")

        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor1",
        )

        assert sensor.native_value == 45
        assert sensor.native_unit_of_measurement == PERCENTAGE

    async def test_light_sensor(self, hass: HomeAssistant, mock_coordinator):
        """Test light sensor."""
        description = next(s for s in PROTECT_SENSOR_TYPES if s.key == "light")

        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor1",
        )

        assert sensor.native_value == 500

    async def test_battery_sensor(self, hass: HomeAssistant, mock_coordinator):
        """Test battery sensor."""
        description = next(s for s in PROTECT_SENSOR_TYPES if s.key == "battery")

        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor1",
        )

        assert sensor.native_value == 85
        assert sensor.native_unit_of_measurement == PERCENTAGE

    async def test_protect_sensor_no_data(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test protect sensor when data is removed after initialization."""
        description = next(s for s in PROTECT_SENSOR_TYPES if s.key == "temperature")

        # Create sensor with valid data first
        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor1",
        )

        # Now remove sensor data to simulate device going offline
        mock_coordinator.data = _with_value(
            mock_coordinator.data, ("protect", "sensors", "sensor1"), None
        )

        # Value should be None when sensor data is missing
        assert sensor.native_value is None


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    async def test_setup_entry_creates_sensors(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test that setup entry creates sensors."""
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        added_entities: list = []

        add_entities = EntityCollector(added_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        # Should have created sensors for devices
        assert len(added_entities) > 0

    async def test_setup_entry_creates_port_sensors(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test that setup entry creates port sensors for active ports."""
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        added_entities: list = []

        add_entities = EntityCollector(added_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        # Check for port sensors (only for port 1 which is UP)
        port_sensors = [e for e in added_entities if isinstance(e, UnifiPortSensor)]
        assert len(port_sensors) > 0

    async def test_setup_entry_creates_network_temperature_sensor(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test that setup creates network temperature sensors when available."""
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        added_entities: list = []

        add_entities = EntityCollector(added_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        temperature_sensors = [
            entity
            for entity in added_entities
            if isinstance(entity, UnifiInsightsSensor)
            and entity.entity_description.key == "general_temperature"
        ]
        assert len(temperature_sensors) == 1
        assert temperature_sensors[0].native_value == 44.5

    async def test_setup_entry_creates_protect_sensors(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test that setup entry creates protect sensors."""
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        added_entities: list = []

        add_entities = EntityCollector(added_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        # Check for protect sensors
        protect_sensors = [
            e for e in added_entities if isinstance(e, UnifiProtectSensor)
        ]
        assert len(protect_sensors) > 0

    async def test_setup_entry_creates_protect_security_sensors(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Thread gateway and keypad fob sensors are discovered at setup."""
        mock_coordinator.data = _with_value(
            mock_coordinator.data,
            ("protect", "link_stations"),
            {"link_station_thread": SAMPLE_THREAD_LINK_STATION},
        )
        mock_coordinator.data = _with_value(
            mock_coordinator.data, ("protect", "fobs"), {"fob_1": SAMPLE_KEYPAD_FOB}
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        added_entities: list = []

        add_entities = EntityCollector(added_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        keys = {
            e.entity_description.key
            for e in added_entities
            if (
                isinstance(e, UnifiProtectSensor)
                and isinstance(
                    e.entity_description, UnifiProtectSecuritySensorEntityDescription
                )
            )
        }
        assert keys == {
            "thread_role",
            "thread_joined_devices",
            "fob_keypad_beep_volume",
            "fob_arm_control",
        }

    async def test_setup_entry_without_protect_client(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test setup entry without protect client."""
        mock_coordinator.protect_client = None
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        added_entities: list = []

        add_entities = EntityCollector(added_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        # Should still create network sensors
        assert len(added_entities) > 0

        # But no protect sensors
        protect_sensors = [
            e for e in added_entities if isinstance(e, UnifiProtectSensor)
        ]
        assert len(protect_sensors) == 0


class TestUnifiPortSensorEdgeCases:
    """Tests for UnifiPortSensor edge cases."""

    async def test_port_sensor_no_device_data(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test port sensor when device data is missing."""
        description = PORT_SENSOR_TYPES[0]  # PoE power

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        # Remove device data
        mock_coordinator.data = _with_value(
            mock_coordinator.data, ("devices", "site1"), {}
        )

        assert sensor.native_value is None

    async def test_port_sensor_no_port_data(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test port sensor when port data is missing."""
        description = PORT_SENSOR_TYPES[0]  # PoE power

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=99,  # Non-existent port
        )

        assert sensor.native_value is None

    async def test_port_sensor_tx_bytes_with_stats(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test port TX bytes sensor with stats data."""
        description = PORT_SENSOR_TYPES[2]  # TX bytes

        # Add port stats using port_bytes key (matches code fallback path)
        mock_coordinator.data = with_device_stats_fields(
            mock_coordinator.data,
            "site1",
            "device1",
            port_bytes={"1": {"tx_bytes": 5000000, "rx_bytes": 3000000}},
        )

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        value = sensor.native_value
        assert value is not None

    async def test_port_sensor_rx_bytes_with_stats(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test port RX bytes sensor with stats data."""
        description = PORT_SENSOR_TYPES[3]  # RX bytes

        # Add port stats using port_bytes key (matches code fallback path)
        mock_coordinator.data = with_device_stats_fields(
            mock_coordinator.data,
            "site1",
            "device1",
            port_bytes={"1": {"tx_bytes": 5000000, "rx_bytes": 3000000}},
        )

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        value = sensor.native_value
        assert value is not None

    async def test_port_sensor_available_not_connected(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test port sensor availability when coordinator not connected."""
        description = PORT_SENSOR_TYPES[0]  # PoE power

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        # Mark the device coordinator unavailable
        set_availability(mock_coordinator, device=False)

        assert sensor.available is False

    @pytest.mark.parametrize(
        ("key", "stat_key"),
        [("port_tx_bytes", "port_bytes"), ("port_tx_rate", "port_rates")],
    )
    async def test_port_sensor_available_from_stats(
        self, hass: HomeAssistant, mock_coordinator: MagicMock, key: str, stat_key: str
    ) -> None:
        """Byte and rate sensors are available when stats carry the port."""
        set_availability(mock_coordinator, device=True)
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            stats={
                "site1": {
                    "device1": {stat_key: {1: {"tx_bytes": 1, "tx_bytes_rate": 1.0}}}
                }
            },
        )
        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=next(
                s for s in (*PORT_SENSOR_TYPES, *PORT_RATE_SENSOR_TYPES) if s.key == key
            ),
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        assert sensor.available is True

    @pytest.mark.parametrize(
        ("key", "record_stats", "expected"),
        [
            ("port_tx_bytes", {"txBytes": 42, "rxBytes": 7}, 42),
            ("port_rx_bytes", {"rx_bytes": 7}, 7),
            ("port_tx_bytes", {"txBytes": True}, None),
            ("port_tx_bytes", {}, None),
            ("port_tx_bytes", None, None),
        ],
    )
    async def test_port_byte_counter_falls_back_to_port_record(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        key: str,
        record_stats: Any,
        expected: int | None,
    ) -> None:
        """Without stats counters, bytes come from the port record, never 0."""
        mock_coordinator.data = without_sections(mock_coordinator.data, "network_facts")
        port: dict[str, Any] = {"idx": 1}
        if record_stats is not None:
            port["stats"] = record_stats
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            devices={"site1": {"device1": {"id": "device1", "ports": [port]}}},
        )
        mock_coordinator.data = with_sections(
            mock_coordinator.data, stats={"site1": {"device1": {}}}
        )
        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=next(s for s in PORT_SENSOR_TYPES if s.key == key),
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        assert sensor.native_value == expected

    @pytest.mark.parametrize(
        ("watts", "expected"), [("4.5", 4.5), ("0", 0.0), ("n/a", None)]
    )
    async def test_port_poe_power_reads_string_watts_from_stats(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        watts: str,
        expected: float | None,
    ) -> None:
        """String PoE watts parse as numbers; an unparsable string is unknown."""
        mock_coordinator.data = without_sections(mock_coordinator.data, "network_facts")
        mock_coordinator.data = with_sections(
            mock_coordinator.data, devices={"site1": {"device1": {"id": "device1"}}}
        )
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            stats={"site1": {"device1": {"poe_ports": {"1": watts}}}},
        )
        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=next(s for s in PORT_SENSOR_TYPES if s.key == "port_poe_power"),
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        assert sensor.native_value == expected

    async def test_port_stats_reject_boolean_counters_and_watts(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """A boolean stats value is not a byte count or a PoE reading."""
        mock_coordinator.data = without_sections(mock_coordinator.data, "network_facts")
        mock_coordinator.data = with_sections(
            mock_coordinator.data, devices={"site1": {"device1": {"id": "device1"}}}
        )
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            stats={
                "site1": {
                    "device1": {
                        "port_bytes": {1: {"tx_bytes": True}},
                        "poe_ports": {1: True},
                    }
                }
            },
        )

        def _sensor(key: str) -> UnifiPortSensor:
            return UnifiPortSensor(
                coordinator=mock_coordinator,
                description=next(s for s in PORT_SENSOR_TYPES if s.key == key),
                site_id="site1",
                device_id="device1",
                port_idx=1,
            )

        assert _sensor("port_tx_bytes").native_value is None
        assert _sensor("port_poe_power").native_value is None

    @pytest.mark.parametrize("key", ["port_tx_bytes", "port_tx_rate", "port_speed"])
    async def test_port_sensor_unavailable_when_device_refresh_fails(
        self, hass: HomeAssistant, mock_coordinator, key: str
    ):
        """Byte, rate and state port sensors follow device_available."""
        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=next(
                s for s in (*PORT_SENSOR_TYPES, *PORT_RATE_SENSOR_TYPES) if s.key == key
            ),
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )
        set_availability(mock_coordinator, device=False)

        assert sensor.available is False

    async def test_port_sensor_available_devices_not_dict(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test port sensor availability when devices is not a dict."""
        description = PORT_SENSOR_TYPES[0]  # PoE power

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        # Set devices to non-dict
        mock_coordinator.data = with_sections(
            mock_coordinator.data, devices="not a dict"
        )

        assert sensor.available is False

    async def test_port_sensor_available_site_devices_not_dict(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test port sensor availability when site devices is not a dict."""
        description = PORT_SENSOR_TYPES[0]  # PoE power

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        # Set site devices to non-dict
        mock_coordinator.data = _with_value(
            mock_coordinator.data, ("devices", "site1"), "not a dict"
        )

        assert sensor.available is False

    async def test_port_sensor_available_device_data_not_dict(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test port sensor availability when device data is not a dict."""
        description = PORT_SENSOR_TYPES[0]  # PoE power

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        # Set device data to non-dict
        mock_coordinator.data = _with_value(
            mock_coordinator.data, ("devices", "site1", "device1"), "not a dict"
        )

        assert sensor.available is False

    async def test_port_sensor_available_interfaces_not_dict(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test port sensor availability when interfaces is not a dict."""
        description = PORT_SENSOR_TYPES[0]  # PoE power

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        # Set interfaces to non-dict
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data, "site1", "device1", interfaces="not a dict"
        )

        assert sensor.available is False

    async def test_port_sensor_available_ports_not_list(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test port sensor availability when ports is not a list."""
        description = PORT_SENSOR_TYPES[0]  # PoE power

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        # Set ports to non-list
        mock_coordinator.data = _with_value(
            mock_coordinator.data,
            ("devices", "site1", "device1", "interfaces", "ports"),
            "not a list",
        )

        assert sensor.available is False

    async def test_port_sensor_available_port_not_found(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test port sensor availability when port is not found."""
        description = PORT_SENSOR_TYPES[0]  # PoE power

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=99,  # Non-existent port
        )

        assert sensor.available is False

    async def test_port_sensor_available_port_down(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test port sensor availability when port is DOWN."""
        description = PORT_SENSOR_TYPES[0]  # PoE power

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=2,  # Port 2 is DOWN
        )

        assert sensor.available is False

    async def test_port_sensor_available_port_state_not_string(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test port sensor availability when port state is not a string."""
        description = PORT_SENSOR_TYPES[0]  # PoE power

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        # Set port state to non-string
        interfaces = mock_coordinator.data["devices"]["site1"]["device1"]["interfaces"]
        ports = [dict(port) for port in interfaces["ports"]]
        ports[0]["state"] = 123
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data,
            "site1",
            "device1",
            interfaces={**interfaces, "ports": ports},
        )

        assert sensor.available is False


class TestUnifiProtectSensorAttributes:
    """Tests for UnifiProtectSensor extra state attributes."""

    async def test_temperature_sensor_attributes(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test temperature sensor extra state attributes."""
        description = next(s for s in PROTECT_SENSOR_TYPES if s.key == "temperature")

        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor1",
        )

        attrs = sensor._attr_extra_state_attributes
        assert attrs["sensor_id"] == "sensor1"
        assert attrs["sensor_name"] == "Kitchen Sensor"
        assert attrs["temperature_value"] == 22.5

    async def test_humidity_sensor_attributes(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test humidity sensor extra state attributes."""
        description = next(s for s in PROTECT_SENSOR_TYPES if s.key == "humidity")

        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor1",
        )

        attrs = sensor._attr_extra_state_attributes
        assert attrs["sensor_id"] == "sensor1"
        assert attrs["sensor_name"] == "Kitchen Sensor"
        assert attrs["humidity_value"] == 45

    async def test_light_sensor_attributes(self, hass: HomeAssistant, mock_coordinator):
        """Test light sensor extra state attributes."""
        description = next(s for s in PROTECT_SENSOR_TYPES if s.key == "light")

        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor1",
        )

        attrs = sensor._attr_extra_state_attributes
        assert attrs["sensor_id"] == "sensor1"
        assert attrs["sensor_name"] == "Kitchen Sensor"
        assert attrs["light_value"] == 500

    async def test_battery_sensor_attributes(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test battery sensor extra state attributes."""
        description = next(s for s in PROTECT_SENSOR_TYPES if s.key == "battery")

        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor1",
        )

        attrs = sensor._attr_extra_state_attributes
        assert attrs["sensor_id"] == "sensor1"
        assert attrs["sensor_name"] == "Kitchen Sensor"
        assert attrs["battery_percentage"] == 85
        assert attrs["battery_low"] is False

    async def test_protect_sensor_unhandled_key_no_extra_attributes(
        self, hass: HomeAssistant, mock_coordinator
    ) -> None:
        """Test UnifiProtectSensor with description key not handling attributes."""
        description = UnifiProtectSensorEntityDescription(
            key="unhandled_type",
            name="Unhandled Sensor",
            device_type="sensor",
            value_fn=lambda _: None,
        )
        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor1",
        )
        assert getattr(sensor, "_attr_extra_state_attributes", None) is None
        assert sensor.extra_state_attributes is None

    async def test_battery_sensor_missing_battery_status_key(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test battery sensor attributes when batteryStatus key is absent."""
        description = next(s for s in PROTECT_SENSOR_TYPES if s.key == "battery")
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "sensors",
            "sensor_no_battery",
            {
                "name": "No Battery Sensor",
            },
        )
        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor_no_battery",
        )
        attrs = sensor._attr_extra_state_attributes
        assert attrs["sensor_id"] == "sensor_no_battery"
        assert attrs["sensor_name"] == "No Battery Sensor"
        assert attrs["battery_percentage"] is None
        assert attrs["battery_low"] is False

    async def test_battery_sensor_real_model_dump(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test battery sensor attributes with real Protect Sensor model dump."""
        model = ProtectSensorModel.model_validate(
            {
                "id": "sensor_real",
                "name": "Real Door Sensor",
                "type": "sensor",
                "modelKey": "sensor",
                "mac": "11:22:33:44:55:66",
                "batteryStatus": {"percentage": 92, "isLow": False},
            }
        )
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "sensors",
            "sensor_real",
            model.model_dump(by_alias=True),
        )

        description = next(s for s in PROTECT_SENSOR_TYPES if s.key == "battery")
        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor_real",
        )
        attrs = sensor._attr_extra_state_attributes
        assert attrs["sensor_id"] == "sensor_real"
        assert attrs["sensor_name"] == "Real Door Sensor"
        assert attrs["battery_percentage"] == 92
        assert attrs["battery_low"] is False


class TestHasStorageInfo:
    """Tests for _has_storage_info helper function."""

    def test_has_storage_info_with_direct_camel_case(self):
        """Test _has_storage_info with direct camelCase fields."""
        assert _has_storage_info({"storageUsedBytes": 100}) is True
        assert _has_storage_info({"storageTotalBytes": 100}) is True

    def test_has_storage_info_with_snake_case(self):
        """Test _has_storage_info with snake_case fields."""
        assert _has_storage_info({"storage_used_bytes": 100}) is True
        assert _has_storage_info({"storage_total_bytes": 100}) is True

    def test_has_storage_info_with_nested_used_size(self):
        """Test _has_storage_info with nested storageInfo.usedSize."""
        assert _has_storage_info({"storageInfo": {"usedSize": 100}}) is True

    def test_has_storage_info_with_nested_total_size(self):
        """Test _has_storage_info with nested storageInfo.totalSize."""
        assert _has_storage_info({"storageInfo": {"totalSize": 100}}) is True

    def test_has_storage_info_with_nested_used_size_snake(self):
        """Test _has_storage_info with nested storageInfo.used_size."""
        assert _has_storage_info({"storageInfo": {"used_size": 100}}) is True

    def test_has_storage_info_with_nested_total_size_snake(self):
        """Test _has_storage_info with nested storageInfo.total_size."""
        assert _has_storage_info({"storageInfo": {"total_size": 100}}) is True

    def test_has_storage_info_returns_false_for_empty(self):
        """Test _has_storage_info returns False when no storage info."""
        assert _has_storage_info({}) is False
        assert _has_storage_info({"other_field": 123}) is False

    def test_has_storage_info_with_non_dict_storage_info(self):
        """Test _has_storage_info with non-dict storageInfo."""
        assert _has_storage_info({"storageInfo": "not a dict"}) is False
        assert _has_storage_info({"storageInfo": None}) is False

    def test_has_storage_info_with_percentage_only(self):
        """Test _has_storage_info when the NVR reports only a used percentage."""
        assert _has_storage_info({"storage": {"used": 42}}) is True
        assert _calculate_storage_percent({"storage": {"used": 42}}) == 42.0

    def test_has_storage_info_with_available_only(self):
        """Test _has_storage_info when the NVR reports only available bytes."""
        assert _has_storage_info({"storageAvailableBytes": 500}) is True
        assert _has_storage_info({"storageInfo": {"availableSize": 500}}) is True


class TestGetStorageBytes:
    """Tests for _get_storage_bytes helper function."""

    def test_get_storage_bytes_available_explicit(self):
        """Test _get_storage_bytes prefers a reported available value."""
        data = {"storageInfo": {"usedSize": 50, "totalSize": 100, "availableSize": 30}}
        assert _get_storage_bytes(data, "available") == 30
        assert _get_storage_bytes({"storageAvailableBytes": 500}, "available") == 500
        assert _get_storage_bytes(data, "unknown") is None

    def test_get_storage_bytes_used_direct(self):
        """Test _get_storage_bytes for 'used' with direct fields."""
        assert _get_storage_bytes({"storageUsedBytes": 1000}, "used") == 1000
        assert _get_storage_bytes({"storage_used_bytes": 2000}, "used") == 2000

    def test_get_storage_bytes_total_direct(self):
        """Test _get_storage_bytes for 'total' with direct fields."""
        assert _get_storage_bytes({"storageTotalBytes": 1000}, "total") == 1000
        assert _get_storage_bytes({"storage_total_bytes": 2000}, "total") == 2000

    def test_get_storage_bytes_used_nested(self):
        """Test _get_storage_bytes for 'used' with nested storageInfo."""
        assert _get_storage_bytes({"storageInfo": {"usedSize": 100}}, "used") == 100
        assert _get_storage_bytes({"storageInfo": {"used_size": 200}}, "used") == 200
        data = {"storageInfo": {"usedSpaceBytes": 300}}
        assert _get_storage_bytes(data, "used") == 300
        data = {"storageInfo": {"used_space_bytes": 400}}
        assert _get_storage_bytes(data, "used") == 400

    def test_get_storage_bytes_total_nested(self):
        """Test _get_storage_bytes for 'total' with nested storageInfo."""
        assert _get_storage_bytes({"storageInfo": {"totalSize": 100}}, "total") == 100
        assert _get_storage_bytes({"storageInfo": {"total_size": 200}}, "total") == 200
        data = {"storageInfo": {"totalSpaceBytes": 300}}
        assert _get_storage_bytes(data, "total") == 300
        data = {"storageInfo": {"total_space_bytes": 400}}
        assert _get_storage_bytes(data, "total") == 400

    def test_get_storage_bytes_returns_none_for_invalid_field(self):
        """Test _get_storage_bytes returns None for unknown field."""
        assert _get_storage_bytes({"storageUsedBytes": 100}, "invalid") is None

    def test_get_storage_bytes_with_float_value(self):
        """Test _get_storage_bytes converts float to int."""
        assert _get_storage_bytes({"storageUsedBytes": 100.5}, "used") == 100
        assert _get_storage_bytes({"storageTotalBytes": 200.9}, "total") == 200

    def test_get_storage_bytes_with_non_numeric_value(self):
        """Test _get_storage_bytes returns None for non-numeric."""
        assert _get_storage_bytes({"storageUsedBytes": "not a number"}, "used") is None
        data = {"storageInfo": {"usedSize": "string"}}
        assert _get_storage_bytes(data, "used") is None

    def test_get_storage_bytes_zero_used_direct_is_zero_not_none(self):
        """
        A genuinely empty NVR reports usedSize 0; that must survive.

        `or` between the direct and snake_case candidates would treat a
        real 0 as absent and incorrectly fall through to the nested
        storageInfo lookup.
        """
        assert _get_storage_bytes({"storageUsedBytes": 0}, "used") == 0
        result = _get_storage_bytes({"storageUsedBytes": 0}, "used")
        assert result is not None

    def test_get_storage_bytes_zero_used_nested_is_zero_not_none(self):
        """A nested storageInfo.usedSize of 0 must also survive, not fall through."""
        data = {"storageInfo": {"usedSize": 0}}
        assert _get_storage_bytes(data, "used") == 0
        assert _get_storage_bytes(data, "used") is not None

    def test_get_storage_bytes_zero_total_direct_is_zero_not_none(self):
        """A totalBytes of 0 must survive rather than being treated as absent."""
        assert _get_storage_bytes({"storageTotalBytes": 0}, "total") == 0
        assert _get_storage_bytes({"storageTotalBytes": 0}, "total") is not None


class TestNVRStorageHelpers:
    """Tests for NVR storage helper functions."""

    def test_bytes_to_gb(self):
        """Test bytes to GB conversion."""
        # 1 GB
        assert _bytes_to_gb(1073741824) == 1.0
        # 500 GB
        assert _bytes_to_gb(536870912000) == 500.0
        # None input
        assert _bytes_to_gb(None) is None
        # Small value
        assert _bytes_to_gb(1000000) == 0.0

    def test_calculate_storage_percent(self):
        """Test storage percentage calculation."""
        # Test 50% used storage
        nvr_data = {
            "storageUsedBytes": 500000000000,
            "storageTotalBytes": 1000000000000,
        }
        assert _calculate_storage_percent(nvr_data) == 50.0

        # Snake case keys
        nvr_data_snake = {
            "storage_used_bytes": 250000000000,
            "storage_total_bytes": 1000000000000,
        }
        assert _calculate_storage_percent(nvr_data_snake) == 25.0

        # Missing data
        assert _calculate_storage_percent({}) is None
        assert _calculate_storage_percent({"storageUsedBytes": 100}) is None
        assert _calculate_storage_percent({"storageTotalBytes": 100}) is None

        # Zero total (division by zero protection)
        assert (
            _calculate_storage_percent({"storageUsedBytes": 0, "storageTotalBytes": 0})
            is None
        )

    def test_calculate_storage_available(self):
        """Test storage available calculation."""
        # 500 GB available
        nvr_data = {
            "storageUsedBytes": 500000000000,
            "storageTotalBytes": 1000000000000,
        }
        result = _calculate_storage_available(nvr_data)
        assert result is not None
        assert abs(result - 465.66) < 1  # ~465 GB (500 GB in decimal)

        # Snake case keys
        nvr_data_snake = {
            "storage_used_bytes": 250000000000,
            "storage_total_bytes": 1000000000000,
        }
        result = _calculate_storage_available(nvr_data_snake)
        assert result is not None

        # Missing data
        assert _calculate_storage_available({}) is None


class TestNVRSensorTypes:
    """Tests for NVR sensor type definitions."""

    def test_nvr_sensor_types_defined(self):
        """Test that NVR sensor types are properly defined."""
        assert len(NVR_SENSOR_TYPES) == 4

        keys = [s.key for s in NVR_SENSOR_TYPES]
        assert "storage_used" in keys
        assert "storage_total" in keys
        assert "storage_available" in keys
        assert "storage_used_percent" in keys

    def test_nvr_sensor_types_have_device_type(self):
        """Test that all NVR sensor types have device_type set."""
        for sensor_type in NVR_SENSOR_TYPES:
            assert sensor_type.device_type == "nvr"


class TestUnifiProtectNVRSensor:
    """Tests for UnifiProtectNVRSensor class."""

    async def test_storage_used_sensor(self, hass: HomeAssistant, mock_coordinator):
        """Test NVR storage used sensor."""
        description = next(s for s in NVR_SENSOR_TYPES if s.key == "storage_used")

        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="nvr1",
        )

        # Check native value (500 GB = ~465.66 GB in binary)
        value = sensor.native_value
        assert isinstance(value, (int, float))
        assert abs(value - 465.66) < 1

    async def test_storage_total_sensor(self, hass: HomeAssistant, mock_coordinator):
        """Test NVR storage total sensor."""
        description = next(s for s in NVR_SENSOR_TYPES if s.key == "storage_total")

        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="nvr1",
        )

        # Check native value (1 TB = ~931.32 GB in binary)
        value = sensor.native_value
        assert isinstance(value, (int, float))
        assert abs(value - 931.32) < 1

    async def test_storage_available_sensor(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test NVR storage available sensor."""
        description = next(s for s in NVR_SENSOR_TYPES if s.key == "storage_available")

        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="nvr1",
        )

        # Check native value (~465.66 GB available)
        value = sensor.native_value
        assert isinstance(value, (int, float))
        assert abs(value - 465.66) < 1

    async def test_storage_percent_sensor(self, hass: HomeAssistant, mock_coordinator):
        """Test NVR storage percentage sensor."""
        description = next(
            s for s in NVR_SENSOR_TYPES if s.key == "storage_used_percent"
        )

        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="nvr1",
        )

        # Check native value (50% used)
        value = sensor.native_value
        assert value == 50.0

    async def test_nvr_sensor_no_data(self, hass: HomeAssistant, mock_coordinator):
        """Test NVR sensor with missing data."""
        description = next(s for s in NVR_SENSOR_TYPES if s.key == "storage_used")

        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="nonexistent_nvr",
        )

        assert sensor.native_value is None

    async def test_nvr_sensor_unique_id(self, hass: HomeAssistant, mock_coordinator):
        """Test NVR sensor unique ID format."""
        description = next(s for s in NVR_SENSOR_TYPES if s.key == "storage_used")

        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="nvr1",
        )

        assert sensor._attr_unique_id == "unifi_insights_nvr_nvr1_storage_used"

    async def test_nvr_sensor_name(self, hass: HomeAssistant, mock_coordinator):
        """Test NVR sensor uses translation_key."""
        description = next(s for s in NVR_SENSOR_TYPES if s.key == "storage_used")

        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="nvr1",
        )

        assert sensor.entity_description.translation_key == "storage_used"


class TestUnifiProtectNVRSensorAttributes:
    """Tests for NVR sensor extra state attributes."""

    async def test_nvr_sensor_attributes(self, hass: HomeAssistant, mock_coordinator):
        """Test NVR sensor extra state attributes."""
        description = next(s for s in NVR_SENSOR_TYPES if s.key == "storage_used")

        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="nvr1",
        )

        attrs = sensor._attr_extra_state_attributes
        assert attrs["nvr_id"] == "nvr1"
        assert attrs["nvr_name"] == "Test NVR"
        assert attrs["nvr_version"] == "4.0.0"
        assert attrs["storage_used"] is not None
        assert attrs["storage_total"] is not None
        assert attrs["storage_available"] is not None
        assert attrs["storage_used_percent"] == 50.0


class TestAsyncSetupEntryWithNVRSensors:
    """Tests for async_setup_entry with NVR sensors."""

    async def test_setup_entry_creates_nvr_sensors(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test that setup creates NVR sensors."""
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        # Should have NVR sensors (4 sensors per NVR)
        nvr_sensors = [
            e
            for e in entities
            if (isinstance(e, UnifiProtectSensor) and e._device_type == "nvr")
        ]
        assert len(nvr_sensors) == 4

        # Check sensor keys
        nvr_sensor_keys = [s.entity_description.key for s in nvr_sensors]
        assert "storage_used" in nvr_sensor_keys
        assert "storage_total" in nvr_sensor_keys
        assert "storage_available" in nvr_sensor_keys
        assert "storage_used_percent" in nvr_sensor_keys

    async def test_setup_entry_without_nvrs(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test setup when there are no NVRs."""
        # Remove NVR data
        mock_coordinator.data = _with_value(
            mock_coordinator.data, ("protect", "nvrs"), {}
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        # Should have no NVR sensors
        nvr_sensors = [
            e
            for e in entities
            if (isinstance(e, UnifiProtectSensor) and e._device_type == "nvr")
        ]
        assert len(nvr_sensors) == 0

    async def test_setup_entry_nvr_without_storage_info(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test setup when NVR has no storage info (covers lines 743, 753)."""
        # Create NVR data without any storage fields
        mock_coordinator.data = _with_value(
            mock_coordinator.data,
            ("protect", "nvrs"),
            {
                "nvr_no_storage": {
                    "id": "nvr_no_storage",
                    "name": "NVR Without Storage",
                    "state": "CONNECTED",
                    "version": "4.0.0",
                    # No storage fields at all
                }
            },
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        # Should have 0 NVR sensors since storage info is not available
        # (all NVR sensor types start with "storage_")
        nvr_sensors = [
            e
            for e in entities
            if (isinstance(e, UnifiProtectSensor) and e._device_type == "nvr")
        ]
        assert len(nvr_sensors) == 0

    async def test_setup_entry_nvrs_not_a_dict_is_skipped(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """A malformed (non-dict) top-level nvrs collection is skipped."""
        mock_coordinator.data = _with_value(
            mock_coordinator.data, ("protect", "nvrs"), "not-a-dict"
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        nvr_sensors = [
            e
            for e in entities
            if (isinstance(e, UnifiProtectSensor) and e._device_type == "nvr")
        ]
        assert len(nvr_sensors) == 0

    async def test_setup_entry_malformed_nvr_record_is_skipped(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """A non-dict record inside the nvrs collection is skipped without
        raising, while a well-formed sibling NVR still gets sensors."""
        mock_coordinator.data = _with_value(
            mock_coordinator.data, ("protect", "nvrs", "nvr_bad"), "not-a-dict"
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        nvr_sensors = [
            e
            for e in entities
            if (isinstance(e, UnifiProtectSensor) and e._device_type == "nvr")
        ]
        # The original well-formed "nvr1" still produces its 4 sensors.
        assert len(nvr_sensors) == 4
        assert all(s._device_id != "nvr_bad" for s in nvr_sensors)


class TestAsyncSetupEntryEdgeCases:
    """Test setup entry edge cases for sensors."""

    async def test_setup_entry_interfaces_not_dict(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test setup when interfaces is not a dict (e.g., list)."""
        # Set interfaces to a list instead of dict
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data, "site1", "device1", interfaces=["ports"]
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        # Should still create device sensors but no port sensors
        port_sensors = [e for e in entities if isinstance(e, UnifiPortSensor)]
        assert len(port_sensors) == 0

    async def test_setup_entry_port_without_idx(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test setup when port has no idx field."""
        # Add port without idx
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data,
            "site1",
            "device1",
            interfaces={
                "ports": [{"state": "UP", "poe": {"enabled": True}}]  # No idx
            },
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        # Should have no port sensors since port has no idx
        port_sensors = [e for e in entities if isinstance(e, UnifiPortSensor)]
        assert len(port_sensors) == 0

    async def test_setup_entry_port_state_down(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test setup when port state is DOWN."""
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data,
            "site1",
            "device1",
            interfaces={
                "ports": [{"idx": 1, "state": "DOWN", "poe": {"enabled": True}}]
            },
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        # Should have no port sensors since port is DOWN
        port_sensors = [e for e in entities if isinstance(e, UnifiPortSensor)]
        assert len(port_sensors) == 0

    async def test_setup_entry_stats_fallback_respects_port_state(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test stats fallback does not create sensors for inactive ports."""
        # Device with switching feature and one UP port, one DOWN port
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data, "site1", "device1", features=["switching"]
        )
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data,
            "site1",
            "device1",
            interfaces={
                "ports": [
                    {"idx": 1, "state": "UP", "poe": {"enabled": True}},
                    {"idx": 2, "state": "DOWN"},
                ]
            },
        )
        # Stats have poe_ports and port_bytes for BOTH ports (including DOWN)
        mock_coordinator.data = with_device_stats_fields(
            mock_coordinator.data,
            "site1",
            "device1",
            poe_ports={
                1: {"power": 10.0},
                2: {"power": 0.0},
            },
        )
        mock_coordinator.data = with_device_stats_fields(
            mock_coordinator.data,
            "site1",
            "device1",
            port_bytes={
                "1": {"tx_bytes": 5000, "rx_bytes": 3000},
                "2": {"tx_bytes": 0, "rx_bytes": 0},
            },
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        port_sensors = [e for e in entities if isinstance(e, UnifiPortSensor)]
        port_indices = {s._port_idx for s in port_sensors}
        # Only port 1 (UP) should have sensors, not port 2 (DOWN)
        assert 1 in port_indices
        assert 2 not in port_indices

    async def test_setup_entry_no_poe_sensor_for_non_poe_device(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test PoE sensor is NOT created when a non-PoE device is on a PoE port."""
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data,
            "site1",
            "device1",
            interfaces={
                "ports": [
                    {
                        "idx": 1,
                        "state": "UP",
                        "speedMbps": 1000,
                        # PoE port enabled but power is 0 (non-PoE device connected)
                        "poe": {"enabled": True, "power": 0.0, "good": False},
                    },
                    {
                        "idx": 2,
                        "state": "UP",
                        "speedMbps": 1000,
                        # PoE port with a PoE device actually drawing power
                        "poe": {"enabled": True, "power": 8.5, "good": True},
                    },
                ]
            },
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        poe_sensors = [
            e
            for e in entities
            if isinstance(e, UnifiPortSensor)
            and e.entity_description.key == "port_poe_power"
        ]
        poe_port_indices = {s._port_idx for s in poe_sensors}

        # Port 1 should NOT have PoE sensor (non-PoE device, power=0)
        assert 1 not in poe_port_indices
        # Port 2 should have PoE sensor (PoE device, power=8.5)
        assert 2 in poe_port_indices

    async def test_setup_entry_poe_sensor_created_when_poe_good(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test PoE sensor IS created when poe_good is True even with 0 power."""
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data,
            "site1",
            "device1",
            interfaces={
                "ports": [
                    {
                        "idx": 1,
                        "state": "UP",
                        "speedMbps": 1000,
                        # PoE negotiation succeeded but no current draw (e.g. standby)
                        "poe": {"enabled": True, "power": 0.0, "good": True},
                    },
                ]
            },
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        poe_sensors = [
            e
            for e in entities
            if isinstance(e, UnifiPortSensor)
            and e.entity_description.key == "port_poe_power"
        ]
        # Sensor should be created because poe_good=True
        assert len(poe_sensors) == 1
        assert poe_sensors[0]._port_idx == 1

    async def test_setup_entry_poe_sensor_only_enabled_no_power(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test PoE sensor is NOT created when port has only enabled=True."""
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data,
            "site1",
            "device1",
            interfaces={
                "ports": [
                    {
                        "idx": 1,
                        "state": "UP",
                        "speedMbps": 1000,
                        # PoE enabled but no power or good flag
                        "poe": {"enabled": True},
                    },
                ]
            },
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        poe_sensors = [
            e
            for e in entities
            if isinstance(e, UnifiPortSensor)
            and e.entity_description.key == "port_poe_power"
        ]
        # No PoE sensor: enabled=True alone doesn't confirm a PoE device
        assert len(poe_sensors) == 0

    async def test_setup_entry_stats_fallback_skips_zero_poe(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test stats fallback skips PoE sensors for ports with zero power."""
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data, "site1", "device1", features=["switching"]
        )
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data,
            "site1",
            "device1",
            interfaces={
                "ports": [
                    {"idx": 1, "state": "UP"},
                    {"idx": 2, "state": "UP"},
                ]
            },
        )
        # Stats: port 1 draws power, port 2 draws zero
        mock_coordinator.data = with_device_stats_fields(
            mock_coordinator.data,
            "site1",
            "device1",
            poe_ports={
                1: 12.5,
                2: 0.0,
            },
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        poe_sensors = [
            e
            for e in entities
            if isinstance(e, UnifiPortSensor)
            and e.entity_description.key == "port_poe_power"
        ]
        poe_port_indices = {s._port_idx for s in poe_sensors}

        # Port 1 should have PoE sensor (power > 0)
        assert 1 in poe_port_indices
        # Port 2 should NOT have PoE sensor (power = 0, non-PoE device)
        assert 2 not in poe_port_indices

    async def test_setup_entry_wan_sensors_for_gateway(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test WAN sensors are created for gateway devices."""
        # Make device a gateway
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data, "site1", "device1", model="UDM-Pro"
        )
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data, "site1", "device1", features=["gateway"]
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        # Find WAN sensors
        wan_sensors = [
            e
            for e in entities
            if isinstance(e, UnifiInsightsSensor)
            and e.entity_description.key.startswith("wan_")
        ]
        assert len(wan_sensors) > 0

    async def test_setup_entry_wan_sensors_dedupe_on_rediscovery(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Re-running discovery for a gateway device adds no duplicate WAN sensors."""
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data, "site1", "device1", model="UDM-Pro"
        )
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data, "site1", "device1", features=["gateway"]
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        wan_sensors_before = [
            e
            for e in entities
            if isinstance(e, UnifiInsightsSensor)
            and e.entity_description.key.startswith("wan_")
        ]
        assert len(wan_sensors_before) > 0

        listener()

        wan_sensors_after = [
            e
            for e in entities
            if isinstance(e, UnifiInsightsSensor)
            and e.entity_description.key.startswith("wan_")
        ]
        assert len(wan_sensors_after) == len(wan_sensors_before)

    async def test_setup_entry_uplink_rate_sensors_created_with_uplink_data(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test uplink rate sensors are created when uplink data exists."""
        # device1 already has uplink stats in default mock data
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        rate_sensors = [
            e
            for e in entities
            if isinstance(e, UnifiInsightsSensor)
            and e.entity_description.key in ("tx_rate", "rx_rate")
        ]
        rate_keys = {s.entity_description.key for s in rate_sensors}
        assert "tx_rate" in rate_keys
        assert "rx_rate" in rate_keys

    async def test_setup_entry_uplink_rate_sensors_skipped_without_data(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test uplink rate sensors are NOT created when no uplink data exists."""
        # Remove uplink data from device1 stats
        stats = dict(mock_coordinator.data["stats"]["site1"]["device1"])
        stats.pop("uplink", None)
        mock_coordinator.data = with_device_stats(
            mock_coordinator.data, "site1", "device1", stats
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        rate_sensors = [
            e
            for e in entities
            if isinstance(e, UnifiInsightsSensor)
            and e.entity_description.key in ("tx_rate", "rx_rate")
        ]
        # device1 has no uplink stats; device2 never had any
        assert len(rate_sensors) == 0

    async def test_setup_entry_uplink_rate_name_includes_uplink(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test uplink rate sensors have 'Uplink' in name."""
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        tx_sensor = next(
            (
                e
                for e in entities
                if isinstance(e, UnifiInsightsSensor)
                and e.entity_description.key == "tx_rate"
            ),
            None,
        )
        assert tx_sensor is not None
        assert tx_sensor.entity_description.translation_key == "tx_rate"

    async def test_setup_entry_uses_top_level_ports_field(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """A top-level `ports` field on device data is used directly, taking
        priority over `interfaces.ports`."""
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data,
            "site1",
            "device1",
            ports=[
                {"idx": 40, "state": "UP", "speedMbps": 1000},
            ],
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        port_indices = {e._port_idx for e in entities if isinstance(e, UnifiPortSensor)}
        assert 40 in port_indices

    async def test_setup_entry_ignores_unparseable_poe_values(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Malformed PoE data (non-dict, non-numeric power) is ignored rather
        than raising, and no PoE sensor is created for those ports."""
        interfaces = mock_coordinator.data["devices"]["site1"]["device1"]["interfaces"]
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data,
            "site1",
            "device1",
            interfaces={
                **interfaces,
                "ports": [
                    *interfaces["ports"],
                    {"idx": 30, "state": "UP", "poe": "not-a-dict"},
                    {"idx": 31, "state": "UP", "poe": {"power": "invalid"}},
                    {"idx": 32, "state": "UP", "poe_power_w": "not-a-number"},
                ],
            },
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        poe_port_indices = {
            e._port_idx
            for e in entities
            if isinstance(e, UnifiPortSensor)
            and e.entity_description.key == "port_poe_power"
        }
        assert 30 not in poe_port_indices
        assert 31 not in poe_port_indices
        assert 32 not in poe_port_indices

    async def test_setup_entry_stats_not_a_dict_for_device_is_skipped(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """A malformed (non-dict) stats entry for a switching device is
        skipped without raising, and regular port sensors are unaffected."""
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data, "site1", "device1", features=["switching"]
        )
        mock_coordinator.data = with_device_stats(
            mock_coordinator.data, "site1", "device1", "not-a-dict"
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

        # Regular interface port sensors (e.g. port 1, which is UP) are still
        # created even though the stats-based fallback was skipped.
        port_indices = {e._port_idx for e in entities if isinstance(e, UnifiPortSensor)}
        assert 1 in port_indices

    async def test_setup_entry_stats_fallback_skips_invalid_keys_and_dedupes(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Non-numeric keys in poe_ports/port_bytes stats are skipped; valid
        new ports are added once and not duplicated on rediscovery."""
        mock_coordinator.data = with_network_device(
            mock_coordinator.data,
            "site1",
            "device3",
            {
                "id": "device3",
                "name": "Fallback Switch",
                "model": "USW-8",
                "state": "ONLINE",
                "features": ["switching"],
                "interfaces": {"ports": [{"idx": 1, "state": "DOWN"}]},
            },
        )
        mock_coordinator.data = with_device_stats(
            mock_coordinator.data,
            "site1",
            "device3",
            {
                "poe_ports": {
                    "abc": 5.0,
                    "5": 12.0,
                },
                "port_bytes": {
                    "xyz": {"tx_bytes": 100},
                    "5": {"tx_bytes": 200, "rx_bytes": 100},
                },
            },
        )
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        mock_add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_config_entry, mock_add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        device3_sensors = [
            e
            for e in entities
            if isinstance(e, UnifiPortSensor) and e._device_id == "device3"
        ]
        # Only port 5 (valid digit key) should have sensors; "abc"/"xyz" are
        # skipped as non-numeric keys.
        assert {e._port_idx for e in device3_sensors} == {5}
        keys = {e.entity_description.key for e in device3_sensors}
        assert "port_poe_power" in keys
        assert "port_tx_bytes" in keys
        assert "port_rx_bytes" in keys
        before_count = len(device3_sensors)

        # Re-running discovery with unchanged data must not add duplicates.
        listener()

        device3_sensors_after = [
            e
            for e in entities
            if isinstance(e, UnifiPortSensor) and e._device_id == "device3"
        ]
        assert len(device3_sensors_after) == before_count


class TestUnifiInsightsSensorEdgeCases:
    """Test edge cases for UnifiInsightsSensor."""

    async def test_native_value_firmware_version_no_device(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test native_value returns None when device data missing."""
        # Find firmware version description
        fw_desc = next(d for d in SENSOR_TYPES if d.key == "firmware_version")

        sensor = UnifiInsightsSensor(
            coordinator=mock_coordinator,
            description=fw_desc,
            site_id="site1",
            device_id="device1",
        )

        # Remove device data
        mock_coordinator.data = _with_value(
            mock_coordinator.data, ("devices", "site1"), {}
        )

        assert sensor.native_value is None

    async def test_async_update_calls_super(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test async_update calls parent implementation."""
        cpu_desc = next(d for d in SENSOR_TYPES if d.key == "cpu_usage")
        sensor = UnifiInsightsSensor(
            coordinator=mock_coordinator,
            description=cpu_desc,
            site_id="site1",
            device_id="device1",
        )

        # Mock async_request_refresh to be awaitable
        mock_coordinator.async_request_refresh = AsyncMock()

        # Should not raise
        await sensor.async_update()

    def test_get_network_device_temperature_from_temperatures_list(self):
        """Test fallback temperature extraction from legacy temperatures list."""
        device = {
            "temperatures": [
                {"name": "CPU", "value": 54.0},
                {"name": "Local", "value": 49.5},
            ]
        }

        assert get_network_device_temperature(device) == 49.5


class TestUnifiPortSensorNativeValueEdgeCases:
    """Test edge cases for UnifiPortSensor native_value."""

    async def test_native_value_no_device_data(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test native_value returns None when device data is missing."""
        speed_desc = PORT_SENSOR_TYPES[1]
        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=speed_desc,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        # Remove device data
        mock_coordinator.data = _with_value(
            mock_coordinator.data, ("devices", "site1"), {}
        )

        assert sensor.native_value is None

    async def test_native_value_no_port_data(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test native_value returns None when port not found."""
        speed_desc = PORT_SENSOR_TYPES[1]
        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=speed_desc,
            site_id="site1",
            device_id="device1",
            port_idx=99,  # Non-existent port
        )

        assert sensor.native_value is None

    async def test_native_value_tx_bytes_with_stats(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test native_value for TX bytes uses stats."""
        tx_desc = PORT_SENSOR_TYPES[2]  # TX bytes
        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=tx_desc,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        # Add stats data for port using port_bytes key (matches code fallback path)
        mock_coordinator.data = with_device_stats_fields(
            mock_coordinator.data,
            "site1",
            "device1",
            port_bytes={"1": {"tx_bytes": 12345}},
        )

        value = sensor.native_value
        # Value should be extracted from value_fn
        assert value is not None or value == 0

    async def test_native_value_poe_power_int_keyed_zero_with_port_data(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """
        An int-keyed poe_ports dict mapping to a real 0 W must yield 0.0.

        Port 1 exists in interfaces.ports (port_data is found), so this
        exercises the "prefer PoE watts from coordinator stats" branch.
        `poe_ports.get(1) or poe_ports.get("1")` would treat the int-keyed
        0 as falsy and wrongly fall through to the (absent) string key.
        """
        poe_desc = PORT_SENSOR_TYPES[0]  # port_poe_power
        mock_coordinator.data = with_device_stats_fields(
            mock_coordinator.data, "site1", "device1", poe_ports={1: 0}
        )

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=poe_desc,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        assert sensor.native_value == 0.0
        assert sensor.native_value is not None

    async def test_native_value_poe_power_int_keyed_zero_without_port_data(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """
        Same int-keyed-zero case, but for a port absent from interfaces.ports.

        This exercises the earlier "no port_data" fallback branch, which
        has its own separate `poe_ports.get(idx) or poe_ports.get(str(idx))`
        call site.
        """
        poe_desc = PORT_SENSOR_TYPES[0]  # port_poe_power
        mock_coordinator.data = with_device_stats_fields(
            mock_coordinator.data, "site1", "device1", poe_ports={5: 0}
        )

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=poe_desc,
            site_id="site1",
            device_id="device1",
            port_idx=5,  # Not present in interfaces.ports
        )

        assert sensor.native_value == 0.0
        assert sensor.native_value is not None

    async def test_native_value_poe_power_string_keyed_zero_with_port_data(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """
        A string-keyed poe_ports dict mapping to a real 0 W must still yield 0.0.

        Covers the fallback half of the lookup: the int-key probe misses, so
        the string-key probe must run and its `0` must survive. Together with
        the int-keyed test this pins both sides of the two-step lookup that
        replaced `poe_ports.get(idx) or poe_ports.get(str(idx))`.
        """
        poe_desc = PORT_SENSOR_TYPES[0]  # port_poe_power
        mock_coordinator.data = with_device_stats_fields(
            mock_coordinator.data, "site1", "device1", poe_ports={"1": 0}
        )

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=poe_desc,
            site_id="site1",
            device_id="device1",
            port_idx=1,
        )

        assert sensor.native_value == 0.0
        assert sensor.native_value is not None

    async def test_native_value_poe_power_string_keyed_zero_without_port_data(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """
        Same string-keyed-zero case for a port absent from interfaces.ports.

        This is the second, separate call site, which needs its own coverage
        of the string-key fallback.
        """
        poe_desc = PORT_SENSOR_TYPES[0]  # port_poe_power
        mock_coordinator.data = with_device_stats_fields(
            mock_coordinator.data, "site1", "device1", poe_ports={"5": 0}
        )

        sensor = UnifiPortSensor(
            coordinator=mock_coordinator,
            description=poe_desc,
            site_id="site1",
            device_id="device1",
            port_idx=5,
        )

        assert sensor.native_value == 0.0
        assert sensor.native_value is not None


class TestUnifiProtectNVRSensorEdgeCases:
    """Test edge cases for UnifiProtectNVRSensor."""

    async def test_available_no_nvr_data(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test available returns False when NVR data missing."""
        storage_desc = next(d for d in NVR_SENSOR_TYPES if d.key == "storage_used")
        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=storage_desc,
            device_id="nvr1",
        )

        # Remove NVR data
        mock_coordinator.data = _with_value(
            mock_coordinator.data, ("protect", "nvrs"), {}
        )

        assert sensor.available is False

    async def test_unavailable_when_protect_coordinator_fails(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test available returns False when Protect coordinator fails."""
        storage_desc = next(d for d in NVR_SENSOR_TYPES if d.key == "storage_used")
        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=storage_desc,
            device_id="nvr1",
        )
        set_availability(mock_coordinator, protect=False)
        assert sensor.available is False

    async def test_available_storage_sensor_with_nested_storage_info(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test available for storage sensor with nested storageInfo."""
        storage_desc = next(d for d in NVR_SENSOR_TYPES if d.key == "storage_used")
        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=storage_desc,
            device_id="nvr1",
        )

        # Set nested storageInfo
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "nvrs",
            "nvr1",
            {
                "id": "nvr1",
                "name": "Test NVR",
                "storageInfo": {"usedSize": 100, "totalSize": 1000},
            },
        )

        assert sensor.available is True

    async def test_available_non_storage_sensor(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test available for non-storage NVR sensor."""
        # Create a custom description that's not storage-related
        custom_desc = UnifiProtectSensorEntityDescription(
            key="custom_sensor",
            name="Custom",
            value_fn=lambda x: x.get("customValue"),
            device_type="nvr",
        )

        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=custom_desc,
            device_id="nvr1",
        )

        # Has NVR data
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "nvrs",
            "nvr1",
            {
                "id": "nvr1",
                "name": "Test NVR",
            },
        )

        assert sensor.available is True

    async def test_update_from_data_attributes(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test _update_from_data sets attributes correctly."""
        storage_desc = next(d for d in NVR_SENSOR_TYPES if d.key == "storage_used")
        sensor = UnifiProtectSensor(
            coordinator=mock_coordinator,
            description=storage_desc,
            device_id="nvr1",
        )

        # Set storage data
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "nvrs",
            "nvr1",
            {
                "id": "nvr1",
                "name": "Test NVR",
                "version": "3.0.0",
                "storageUsedBytes": 500000000000,
                "storageTotalBytes": 1000000000000,
            },
        )

        sensor._update_from_data()

        assert sensor._attr_extra_state_attributes is not None
        assert "nvr_id" in sensor._attr_extra_state_attributes


class TestMigrateSensorUnits:
    """Test _migrate_sensor_units for entity registry unit migration."""

    def test_migrate_sets_refresh_flag_for_port_rx_bytes(self, hass, mock_config_entry):
        """Test migration sets refresh_initial_entity_options for port_rx_bytes."""
        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.domain = "sensor"
        mock_entry.unique_id = "aa:bb:cc:dd:ee:ff_port_rx_bytes"
        mock_entry.entity_id = "sensor.device_port_rx_bytes"
        mock_entry.options = {}

        mock_registry = MagicMock()

        with (
            patch(
                "custom_components.unifi_insights.sensor.er.async_get",
                return_value=mock_registry,
            ),
            patch(
                "custom_components.unifi_insights.sensor.er.async_entries_for_config_entry",
                return_value=[mock_entry],
            ),
        ):
            _migrate_sensor_units(hass, mock_config_entry)

        mock_registry.async_update_entity_options.assert_called_once()
        call_args = mock_registry.async_update_entity_options.call_args
        assert call_args[0][0] == "sensor.device_port_rx_bytes"
        assert call_args[0][1] == "sensor.private"
        assert call_args[0][2]["refresh_initial_entity_options"] is True

    def test_migrate_skips_non_sensor_entities(self, hass, mock_config_entry):
        """Test migration skips non-sensor domain entities."""
        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.domain = "binary_sensor"
        mock_entry.unique_id = "aa:bb:cc:dd:ee:ff_port_rx_bytes"
        mock_entry.entity_id = "binary_sensor.something"
        mock_entry.options = {}

        mock_registry = MagicMock()

        with (
            patch(
                "custom_components.unifi_insights.sensor.er.async_get",
                return_value=mock_registry,
            ),
            patch(
                "custom_components.unifi_insights.sensor.er.async_entries_for_config_entry",
                return_value=[mock_entry],
            ),
        ):
            _migrate_sensor_units(hass, mock_config_entry)

        mock_registry.async_update_entity_options.assert_not_called()

    def test_migrate_skips_already_correct_units(self, hass, mock_config_entry):
        """Test migration skips entities whose suggested unit already matches."""
        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.domain = "sensor"
        mock_entry.unique_id = "aa:bb:cc:dd:ee:ff_port_tx_bytes"
        mock_entry.entity_id = "sensor.device_port_tx_bytes"
        mock_entry.options = {
            "sensor.private": {
                "suggested_unit_of_measurement": str(UnitOfInformation.GIGABYTES),
            }
        }

        mock_registry = MagicMock()

        with (
            patch(
                "custom_components.unifi_insights.sensor.er.async_get",
                return_value=mock_registry,
            ),
            patch(
                "custom_components.unifi_insights.sensor.er.async_entries_for_config_entry",
                return_value=[mock_entry],
            ),
        ):
            _migrate_sensor_units(hass, mock_config_entry)

        mock_registry.async_update_entity_options.assert_not_called()

    def test_migrate_skips_unrelated_sensor_keys(self, hass, mock_config_entry):
        """Test migration skips sensors whose key has no suggested unit."""
        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.domain = "sensor"
        mock_entry.unique_id = "aa:bb:cc:dd:ee:ff_cpu_utilization"
        mock_entry.entity_id = "sensor.device_cpu_utilization"
        mock_entry.options = {}

        mock_registry = MagicMock()

        with (
            patch(
                "custom_components.unifi_insights.sensor.er.async_get",
                return_value=mock_registry,
            ),
            patch(
                "custom_components.unifi_insights.sensor.er.async_entries_for_config_entry",
                return_value=[mock_entry],
            ),
        ):
            _migrate_sensor_units(hass, mock_config_entry)

        mock_registry.async_update_entity_options.assert_not_called()

    def test_migrate_handles_uplink_rate_sensors(self, hass, mock_config_entry):
        """Test migration sets refresh flag for uplink rate sensors."""
        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.domain = "sensor"
        mock_entry.unique_id = "aa:bb:cc:dd:ee:ff_tx_rate"
        mock_entry.entity_id = "sensor.device_tx_rate"
        mock_entry.options = {}

        mock_registry = MagicMock()

        with (
            patch(
                "custom_components.unifi_insights.sensor.er.async_get",
                return_value=mock_registry,
            ),
            patch(
                "custom_components.unifi_insights.sensor.er.async_entries_for_config_entry",
                return_value=[mock_entry],
            ),
        ):
            _migrate_sensor_units(hass, mock_config_entry)

        mock_registry.async_update_entity_options.assert_called_once()
        call_args = mock_registry.async_update_entity_options.call_args
        assert call_args[0][2]["refresh_initial_entity_options"] is True


class TestSiteClientSensorTypes:
    """Tests for site-level client sensor descriptions."""

    def test_site_client_sensor_types_defined(self):
        """Test that site client sensor types are defined."""
        assert len(SITE_CLIENT_SENSOR_TYPES) == 3

    def test_sensor_keys(self):
        """Test that sensor keys are correct."""
        keys = {desc.key for desc in SITE_CLIENT_SENSOR_TYPES}
        assert keys == {
            "site_total_clients",
            "site_wired_clients",
            "site_wireless_clients",
        }

    def test_total_clients_value_fn(self):
        """Test value_fn counts all clients."""
        clients = {
            "c1": {"type": "WIRED"},
            "c2": {"type": "WIRELESS"},
            "c3": {"type": "WIRED"},
        }
        desc = next(
            d for d in SITE_CLIENT_SENSOR_TYPES if d.key == "site_total_clients"
        )
        assert require_not_none(desc.value_fn)(clients) == 3

    def test_wired_clients_value_fn(self):
        """Test value_fn counts only wired clients."""
        clients = {
            "c1": {"type": "WIRED"},
            "c2": {"type": "WIRELESS"},
            "c3": {"type": "WIRED"},
        }
        desc = next(
            d for d in SITE_CLIENT_SENSOR_TYPES if d.key == "site_wired_clients"
        )
        assert require_not_none(desc.value_fn)(clients) == 2

    def test_wireless_clients_value_fn(self):
        """Test value_fn counts only wireless clients."""
        clients = {
            "c1": {"type": "WIRED"},
            "c2": {"type": "WIRELESS"},
            "c3": {"type": "WIRELESS"},
        }
        desc = next(
            d for d in SITE_CLIENT_SENSOR_TYPES if d.key == "site_wireless_clients"
        )
        assert require_not_none(desc.value_fn)(clients) == 2

    def test_empty_clients(self):
        """Test value_fn handles empty clients dict."""
        clients: dict = {}
        for desc in SITE_CLIENT_SENSOR_TYPES:
            assert require_not_none(desc.value_fn)(clients) == 0


class TestUnifiSiteClientSensor:
    """Tests for the UnifiSiteClientSensor entity class."""

    @pytest.fixture
    def mock_coordinator_with_clients(self, mock_coordinator: MagicMock) -> MagicMock:
        """Create a coordinator with client data for site-level tests."""
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            clients={
                "site1": {
                    "c1": {"id": "c1", "type": "WIRED", "name": "PC"},
                    "c2": {"id": "c2", "type": "WIRELESS", "name": "Phone"},
                    "c3": {"id": "c3", "type": "WIRED", "name": "Printer"},
                }
            },
        )
        mock_coordinator.last_update_success = True
        return mock_coordinator

    async def test_total_clients_value(
        self, hass: HomeAssistant, mock_coordinator_with_clients
    ):
        """Test total clients sensor returns correct count."""
        desc = next(
            d for d in SITE_CLIENT_SENSOR_TYPES if d.key == "site_total_clients"
        )
        sensor = UnifiSiteClientSensor(
            coordinator=mock_coordinator_with_clients,
            description=desc,
            site_id="site1",
            unique_id="console_site1_" + desc.key,
        )
        assert sensor.native_value == 3

    async def test_wired_clients_value(
        self, hass: HomeAssistant, mock_coordinator_with_clients
    ):
        """Test wired clients sensor returns correct count."""
        desc = next(
            d for d in SITE_CLIENT_SENSOR_TYPES if d.key == "site_wired_clients"
        )
        sensor = UnifiSiteClientSensor(
            coordinator=mock_coordinator_with_clients,
            description=desc,
            site_id="site1",
            unique_id="console_site1_" + desc.key,
        )
        assert sensor.native_value == 2

    async def test_wireless_clients_value(
        self, hass: HomeAssistant, mock_coordinator_with_clients
    ):
        """Test wireless clients sensor returns correct count."""
        desc = next(
            d for d in SITE_CLIENT_SENSOR_TYPES if d.key == "site_wireless_clients"
        )
        sensor = UnifiSiteClientSensor(
            coordinator=mock_coordinator_with_clients,
            description=desc,
            site_id="site1",
            unique_id="console_site1_" + desc.key,
        )
        assert sensor.native_value == 1

    async def test_unique_id(self, hass: HomeAssistant, mock_coordinator_with_clients):
        """Test unique ID format."""
        desc = next(
            d for d in SITE_CLIENT_SENSOR_TYPES if d.key == "site_total_clients"
        )
        sensor = UnifiSiteClientSensor(
            coordinator=mock_coordinator_with_clients,
            description=desc,
            site_id="site1",
            unique_id="console_site1_" + desc.key,
        )
        assert sensor.unique_id == "console_site1_site_total_clients"

    async def test_available(self, hass: HomeAssistant, mock_coordinator_with_clients):
        """Test availability follows coordinator update success."""
        desc = next(
            d for d in SITE_CLIENT_SENSOR_TYPES if d.key == "site_total_clients"
        )
        sensor = UnifiSiteClientSensor(
            coordinator=mock_coordinator_with_clients,
            description=desc,
            site_id="site1",
            unique_id="console_site1_" + desc.key,
        )
        assert sensor.available is True

        set_availability(mock_coordinator_with_clients, device=False)
        assert sensor.available is False

    async def test_device_info_attaches_to_gateway(
        self, hass: HomeAssistant, mock_coordinator_with_clients
    ):
        """Test device info attaches to existing gateway device."""
        desc = next(
            d for d in SITE_CLIENT_SENSOR_TYPES if d.key == "site_total_clients"
        )
        sensor = UnifiSiteClientSensor(
            coordinator=mock_coordinator_with_clients,
            description=desc,
            site_id="site1",
            unique_id="console_site1_" + desc.key,
        )
        # device2 is a UDM-Pro gateway, so the sensor should attach to it
        device_info = sensor.device_info
        assert device_info is not None
        idents = device_info.get("identifiers")
        assert idents is not None
        assert ("unifi_insights", "site1_device2") in idents

    async def test_device_info_creates_virtual_site_device(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test virtual site device created when no gateway exists."""
        # Remove the gateway device
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            devices={
                "site1": {
                    "device1": {
                        "id": "device1",
                        "name": "Test Switch",
                        "model": "USW-24",
                        "features": ["switching"],
                    }
                }
            },
        )
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            clients={"site1": {"c1": {"id": "c1", "type": "WIRED"}}},
        )
        mock_coordinator.last_update_success = True
        desc = next(
            d for d in SITE_CLIENT_SENSOR_TYPES if d.key == "site_total_clients"
        )
        sensor = UnifiSiteClientSensor(
            coordinator=mock_coordinator,
            description=desc,
            site_id="site1",
            unique_id="console_site1_" + desc.key,
        )
        device_info = sensor.device_info
        assert device_info is not None
        idents = device_info.get("identifiers")
        assert ("unifi_insights", "site_site1") in require_not_none(idents)

    async def test_native_value_no_clients_data(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test returns 0 when no client data for the site."""
        mock_coordinator.data = with_sections(mock_coordinator.data, clients={})
        mock_coordinator.last_update_success = True
        desc = next(
            d for d in SITE_CLIENT_SENSOR_TYPES if d.key == "site_total_clients"
        )
        sensor = UnifiSiteClientSensor(
            coordinator=mock_coordinator,
            description=desc,
            site_id="site1",
            unique_id="console_site1_" + desc.key,
        )
        assert sensor.native_value == 0


class TestAsyncSetupEntrySiteClientSensors:
    """Tests for site-level client sensor creation in async_setup_entry."""

    async def test_setup_entry_creates_site_client_sensors(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test async_setup_entry creates site-level client sensors."""
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            clients={
                "site1": {
                    "c1": {"id": "c1", "type": "WIRED"},
                    "c2": {"id": "c2", "type": "WIRELESS"},
                }
            },
        )

        config_entry = MagicMock()
        config_entry.data = {"console_id": "console"}
        config_entry.runtime_data = MagicMock()
        config_entry.runtime_data.mobility_coordinator = None
        config_entry.runtime_data.coordinator = mock_coordinator
        config_entry.entry_id = "test_entry"

        entities = []

        mock_add_entities = EntityCollector(entities)

        with (
            patch(
                "custom_components.unifi_insights.sensor.er.async_get",
                return_value=MagicMock(
                    async_entries_for_config_entry=MagicMock(return_value=[]),
                    async_get_entity_id=MagicMock(return_value=None),
                ),
            ),
            patch(
                "custom_components.unifi_insights.sensor.er.async_entries_for_config_entry",
                return_value=[],
            ),
        ):
            await async_setup_entry(hass, config_entry, mock_add_entities)

        site_sensors = [e for e in entities if isinstance(e, UnifiSiteClientSensor)]
        assert len(site_sensors) == 3
        keys = {s.entity_description.key for s in site_sensors}
        assert keys == {
            "site_total_clients",
            "site_wired_clients",
            "site_wireless_clients",
        }

    async def test_setup_entry_no_clients_no_site_sensors(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test no site sensors when clients data is empty."""
        mock_coordinator.data = with_sections(mock_coordinator.data, clients={})

        config_entry = MagicMock()
        config_entry.data = {"console_id": "console"}
        config_entry.runtime_data = MagicMock()
        config_entry.runtime_data.mobility_coordinator = None
        config_entry.runtime_data.coordinator = mock_coordinator
        config_entry.entry_id = "test_entry"

        entities = []

        mock_add_entities = EntityCollector(entities)

        with (
            patch(
                "custom_components.unifi_insights.sensor.er.async_get",
                return_value=MagicMock(
                    async_entries_for_config_entry=MagicMock(return_value=[]),
                    async_get_entity_id=MagicMock(return_value=None),
                ),
            ),
            patch(
                "custom_components.unifi_insights.sensor.er.async_entries_for_config_entry",
                return_value=[],
            ),
        ):
            await async_setup_entry(hass, config_entry, mock_add_entities)

        site_sensors = [e for e in entities if isinstance(e, UnifiSiteClientSensor)]
        assert len(site_sensors) == 0

    async def test_setup_entry_clients_and_wifi_not_dicts_are_skipped(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Malformed (non-dict) top-level clients/wifi collections are skipped
        without raising, and produce no site or WiFi sensors."""
        mock_coordinator.data = with_sections(
            mock_coordinator.data, clients="not-a-dict"
        )
        mock_coordinator.data = with_sections(mock_coordinator.data, wifi="not-a-dict")

        config_entry = MagicMock()
        config_entry.data = {"console_id": "console"}
        config_entry.runtime_data = MagicMock()
        config_entry.runtime_data.mobility_coordinator = None
        config_entry.runtime_data.coordinator = mock_coordinator
        config_entry.entry_id = "test_entry"

        entities = []

        mock_add_entities = EntityCollector(entities)

        with (
            patch(
                "custom_components.unifi_insights.sensor.er.async_get",
                return_value=MagicMock(
                    async_entries_for_config_entry=MagicMock(return_value=[]),
                    async_get_entity_id=MagicMock(return_value=None),
                ),
            ),
            patch(
                "custom_components.unifi_insights.sensor.er.async_entries_for_config_entry",
                return_value=[],
            ),
        ):
            await async_setup_entry(hass, config_entry, mock_add_entities)

        site_sensors = [e for e in entities if isinstance(e, UnifiSiteClientSensor)]
        wifi_sensors = [
            e for e in entities if isinstance(e, UnifiWifiClientCountSensor)
        ]
        assert len(site_sensors) == 0
        assert len(wifi_sensors) == 0


class TestHasProtectStat:
    """Tests for _has_protect_stat capability check."""

    def test_stat_present_in_stats(self):
        """Test capability returns True when stat exists in stats dict."""
        check = _has_protect_stat("temperature", "temperature")
        data = {"stats": {"temperature": {"value": 22.5}}}
        assert check(data) is True

    def test_stat_missing_from_stats(self):
        """Test capability returns False when stat is absent."""
        check = _has_protect_stat("temperature", "temperature")
        data = {"stats": {}, "temperature": None}
        assert check(data) is False

    def test_stat_none_in_stats(self):
        """Test capability returns False when stat value is None."""
        check = _has_protect_stat("temperature", "temperature")
        data = {"stats": {"temperature": {"value": None}}}
        assert check(data) is False

    def test_flat_field_fallback(self):
        """Test capability uses flat field when stats absent."""
        check = _has_protect_stat("temperature", "temperature")
        data = {"temperature": 22.5}
        assert check(data) is True

    def test_flat_field_none(self):
        """Test capability returns False when flat field is None."""
        check = _has_protect_stat("temperature", "temperature")
        data = {"temperature": None}
        assert check(data) is False

    def test_no_stats_no_flat(self):
        """Test capability returns False when no data at all."""
        check = _has_protect_stat("temperature", "temperature")
        assert check({}) is False

    def test_light_value_flat_key(self):
        """Test light capability with different flat key (lightValue)."""
        check = _has_protect_stat("light", "lightValue")
        data = {"lightValue": 500.0}
        assert check(data) is True

    def test_light_value_none(self):
        """Test light capability when lightValue is None."""
        check = _has_protect_stat("light", "lightValue")
        data = {"lightValue": None}
        assert check(data) is False


class TestProtectSensorCapabilityFiltering:
    """Tests for capability-based filtering of Protect sensor entities."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock coordinator with UP-SENSE and USL-Entry sensors."""
        coordinator = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.last_update_success = True
        coordinator.data = {
            "sites": {"site1": {"id": "site1", "name": "Default"}},
            "devices": {"site1": {}},
            "clients": {"site1": []},
            "stats": {"site1": {}},
            "network_info": {},
            "vouchers": {},
            "protect": {
                "sensors": {
                    "up_sense_1": {
                        "id": "up_sense_1",
                        "name": "Kitchen Sensor",
                        "model": "UP-SENSE",
                        "state": "CONNECTED",
                        "stats": {
                            "temperature": {"value": 22.5},
                            "humidity": {"value": 45},
                            "light": {"value": 500},
                        },
                        "batteryStatus": {"percentage": 85, "isLow": False},
                    },
                    "usl_entry_1": {
                        "id": "usl_entry_1",
                        "name": "Front Door Sensor",
                        "model": "USL-Entry",
                        "state": "CONNECTED",
                        "mountType": "door",
                        "isOpened": False,
                        "isTamperingDetected": False,
                        "temperature": None,
                        "humidity": None,
                        "lightValue": None,
                        "batteryStatus": {"percentage": 92, "isLow": False},
                    },
                },
                "cameras": {},
                "lights": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
                "events": {},
            },
            "last_update": None,
        }
        coordinator.get_site = MagicMock(
            return_value=coordinator.data["sites"]["site1"]
        )
        return coordinator

    async def test_up_sense_gets_all_sensors(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test UP-SENSE gets all four sensor types."""
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        added_entities: list = []

        add_entities = EntityCollector(added_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        up_sense_sensors = [
            e
            for e in added_entities
            if isinstance(e, UnifiProtectSensor) and e._device_id == "up_sense_1"
        ]
        sensor_keys = {e.entity_description.key for e in up_sense_sensors}
        assert sensor_keys == {"temperature", "humidity", "light", "battery"}

    async def test_usl_entry_gets_only_battery_sensor(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test USL-Entry gets only battery sensor."""
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        added_entities: list = []

        add_entities = EntityCollector(added_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        usl_entry_sensors = [
            e
            for e in added_entities
            if isinstance(e, UnifiProtectSensor) and e._device_id == "usl_entry_1"
        ]
        sensor_keys = {e.entity_description.key for e in usl_entry_sensors}
        assert sensor_keys == {"battery"}
        assert "temperature" not in sensor_keys
        assert "humidity" not in sensor_keys
        assert "light" not in sensor_keys


class TestUnifiOutletSensor:
    """Tests for UnifiOutletSensor platform entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with PDU device data."""
        coordinator = MagicMock()
        coordinator.available = True
        coordinator.last_update_success = True
        coordinator.protect_client = None
        coordinator.data = {
            "sites": {"site1": {"id": "site1", "name": "Default"}},
            "devices": {
                "site1": {
                    "pdu1": {
                        "id": "pdu1",
                        "_id": "60a1b2c3d4e5f67890123456",
                        "name": "Server Room PDU",
                        "status": "online",
                        "state": "ONLINE",
                        "ac_power_consumption": 245.5,
                        "ac_power_budget": 1800.0,
                        "features": [],
                        "outlet_table": [
                            {
                                "index": 1,
                                "name": "Main Server",
                                "relay_state": True,
                                "cycle_enabled": True,
                                "outlet_caps": 3,
                                "outlet_voltage": 120.2,
                                "outlet_current": 1.25,
                                "outlet_power": 150.0,
                                "outlet_power_factor": 0.98,
                            },
                            {
                                "index": 2,
                                "name": "Backup Server",
                                "relay_state": False,
                                "cycle_enabled": False,
                                "outlet_caps": 3,
                                "outlet_voltage": 120.1,
                                "outlet_current": 0.0,
                                "outlet_power": 0.0,
                                "outlet_power_factor": 0.0,
                            },
                            {
                                "index": 3,
                                "name": "Unmetered Outlet",
                                "relay_state": True,
                                "cycle_enabled": None,
                                "outlet_caps": 1,
                                "outlet_voltage": None,
                                "outlet_current": None,
                                "outlet_power": None,
                                "outlet_power_factor": None,
                            },
                        ],
                    }
                }
            },
            "stats": {"site1": {"pdu1": {}}},
            "clients": {},
            "wifi": {},
        }
        coordinator.get_site = MagicMock(
            return_value=coordinator.data["sites"]["site1"]
        )
        return coordinator

    async def test_setup_creates_outlet_metering_sensors(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ) -> None:
        """Test async_setup_entry creates UnifiOutletSensor instances only for
        metered outlets."""
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        added_entities: list = []

        add_entities = EntityCollector(added_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        outlet_sensors = [e for e in added_entities if isinstance(e, UnifiOutletSensor)]
        # Outlets 1 & 2 are metered (4 sensors each = 8);
        # outlet 3 is unmetered (0 sensors)
        assert len(outlet_sensors) == 8

        unique_ids = {e.unique_id for e in outlet_sensors}
        assert "site1_pdu1_outlet_1_power" in unique_ids
        assert "site1_pdu1_outlet_1_voltage" in unique_ids
        assert "site1_pdu1_outlet_1_current" in unique_ids
        assert "site1_pdu1_outlet_1_power_factor" in unique_ids

        assert "site1_pdu1_outlet_2_power" in unique_ids
        assert "site1_pdu1_outlet_2_voltage" in unique_ids
        assert "site1_pdu1_outlet_2_current" in unique_ids
        assert "site1_pdu1_outlet_2_power_factor" in unique_ids

        # Outlet 3 should not have any sensors
        assert "site1_pdu1_outlet_3_power" not in unique_ids

    def test_outlet_metering_sensor_properties_and_values(
        self, mock_coordinator
    ) -> None:
        """Test UnifiOutletSensor properties, unique_id, native values, and state."""
        desc_map = {d.key: d for d in OUTLET_SENSOR_TYPES}

        sensor_power = UnifiOutletSensor(
            coordinator=mock_coordinator,
            description=desc_map["power"],
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_name="Main Server",
        )
        assert sensor_power.unique_id == "site1_pdu1_outlet_1_power"
        assert sensor_power.native_value == 150.0
        assert sensor_power.available is True
        assert sensor_power.extra_state_attributes == {
            "index": 1,
            "name": "Main Server",
            "relay_state": True,
        }

        sensor_voltage = UnifiOutletSensor(
            coordinator=mock_coordinator,
            description=desc_map["voltage"],
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_name="Main Server",
        )
        assert sensor_voltage.unique_id == "site1_pdu1_outlet_1_voltage"
        assert sensor_voltage.native_value == 120.2

        sensor_current = UnifiOutletSensor(
            coordinator=mock_coordinator,
            description=desc_map["current"],
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_name="Main Server",
        )
        assert sensor_current.unique_id == "site1_pdu1_outlet_1_current"
        assert sensor_current.native_value == 1.25

        sensor_pf = UnifiOutletSensor(
            coordinator=mock_coordinator,
            description=desc_map["power_factor"],
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_name="Main Server",
        )
        assert sensor_pf.unique_id == "site1_pdu1_outlet_1_power_factor"
        assert sensor_pf.native_value == 0.98

    def test_outlet_sensor_unavailable_when_outlet_missing(
        self, mock_coordinator
    ) -> None:
        """Test UnifiOutletSensor unavailable when outlet is missing from data."""
        desc_map = {d.key: d for d in OUTLET_SENSOR_TYPES}
        sensor = UnifiOutletSensor(
            coordinator=mock_coordinator,
            description=desc_map["power"],
            site_id="site1",
            device_id="pdu1",
            outlet_index=99,
        )
        assert sensor.available is False
        assert sensor.native_value is None

    @pytest.mark.parametrize("description", OUTLET_SENSOR_TYPES)
    @pytest.mark.parametrize(
        ("state", "healthy", "expected"),
        [
            ("GETTING_READY", True, True),
            ("GETTING_READY", False, False),
            ("ONLINE", False, False),
            ("OFFLINE", True, False),
            ("UPDATING", True, False),
            ("ADOPTING", True, False),
        ],
    )
    def test_outlet_serviceable_availability(
        self,
        mock_coordinator: MagicMock,
        description: UnifiInsightsSensorEntityDescription,
        state: str,
        *,
        healthy: bool,
        expected: bool,
    ) -> None:
        """Electrical readings survive provisioning only while Device data is fresh."""
        set_availability(mock_coordinator, device=healthy)
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data, "site1", "pdu1", state=state
        )
        sensor = UnifiOutletSensor(
            mock_coordinator, description, "site1", "pdu1", outlet_index=1
        )
        assert sensor.available is expected

    def test_outlet_has_metering_helper(self) -> None:
        """Test _outlet_has_metering helper detects caps bitmask and values."""
        assert _outlet_has_metering({"outlet_caps": 3}) is True
        assert _outlet_has_metering({"outlet_caps": 2}) is True
        assert _outlet_has_metering({"outlet_caps": 1}) is False
        assert _outlet_has_metering({"outlet_power": 10.0}) is True
        assert _outlet_has_metering({"power": 10.0}) is True
        assert _outlet_has_metering({"outlet_voltage": 120.0}) is True
        assert _outlet_has_metering({"outlet_current": 1.0}) is True
        assert _outlet_has_metering({"outlet_power_factor": 0.9}) is True
        assert _outlet_has_metering({}) is False


class TestUnifiAcPowerSensors:
    """Tests for device-level AC power consumption and budget sensors."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with PDU device data."""
        coordinator = MagicMock()
        coordinator.available = True
        coordinator.last_update_success = True
        coordinator.protect_client = None
        coordinator.data = {
            "sites": {"site1": {"id": "site1", "name": "Default"}},
            "devices": {
                "site1": {
                    "pdu1": {
                        "id": "pdu1",
                        "name": "Server Room PDU",
                        "status": "online",
                        "state": "ONLINE",
                        "ac_power_consumption": 245.5,
                        "ac_power_budget": 1800.0,
                        "features": [],
                        "outlet_table": [],
                    },
                    "switch1": {
                        "id": "switch1",
                        "name": "Standard Switch",
                        "status": "online",
                        "state": "ONLINE",
                        "features": ["switching"],
                        "ports": [],
                    },
                }
            },
            "stats": {"site1": {"pdu1": {}, "switch1": {}}},
            "clients": {},
            "wifi": {},
        }
        coordinator.get_site = MagicMock(
            return_value=coordinator.data["sites"]["site1"]
        )
        return coordinator

    async def test_setup_creates_ac_power_sensors_only_when_data_present(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ) -> None:
        """Test AC power sensors created for PDU but skipped for switch."""
        mock_config_entry.runtime_data.coordinator = mock_coordinator
        added_entities: list = []

        add_entities = EntityCollector(added_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        pdu_sensors = [
            e
            for e in added_entities
            if isinstance(e, UnifiInsightsSensor) and e._device_id == "pdu1"
        ]
        pdu_keys = {e.entity_description.key for e in pdu_sensors}
        assert "ac_power_consumption" in pdu_keys
        assert "ac_power_budget" in pdu_keys

        switch_sensors = [
            e
            for e in added_entities
            if isinstance(e, UnifiInsightsSensor) and e._device_id == "switch1"
        ]
        switch_keys = {e.entity_description.key for e in switch_sensors}
        assert "ac_power_consumption" not in switch_keys
        assert "ac_power_budget" not in switch_keys

    def test_ac_power_sensor_unique_ids_and_values(self, mock_coordinator) -> None:
        """Test literal unique_ids and values for AC power sensors."""
        desc_map = {d.key: d for d in SENSOR_TYPES}

        sensor_consumption = UnifiInsightsSensor(
            coordinator=mock_coordinator,
            description=desc_map["ac_power_consumption"],
            site_id="site1",
            device_id="pdu1",
        )
        assert sensor_consumption.unique_id == "site1_pdu1_ac_power_consumption"
        assert sensor_consumption.native_value == 245.5

        sensor_budget = UnifiInsightsSensor(
            coordinator=mock_coordinator,
            description=desc_map["ac_power_budget"],
            site_id="site1",
            device_id="pdu1",
        )
        assert sensor_budget.unique_id == "site1_pdu1_ac_power_budget"
        assert sensor_budget.native_value == 1800.0

    def test_outlet_sensor_edge_cases(self, mock_coordinator: MagicMock) -> None:
        """Test UnifiOutletSensor _find_outlet_data edge cases."""
        desc_map = {d.key: d for d in OUTLET_SENSOR_TYPES}
        sensor = UnifiOutletSensor(
            coordinator=mock_coordinator,
            description=desc_map["power"],
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
        )

        # 1. Device missing
        mock_coordinator.data = with_sections(mock_coordinator.data, devices={})
        assert sensor._find_outlet_data() is None
        assert sensor.extra_state_attributes is None

        # 2. Outlet table with invalid entries and matching outlet_idx
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            devices={
                "site1": {
                    "pdu1": {
                        "outlet_table": [
                            "not_a_dict",
                            {"index": "invalid"},
                            {"outlet_idx": 1, "name": None, "relay_state": None},
                        ]
                    }
                }
            },
        )
        outlet = sensor._find_outlet_data()
        assert outlet is not None
        assert sensor.extra_state_attributes == {"index": 1}

    def test_outlet_sensor_device_not_dict_and_missing_index_and_no_value_fn(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test outlet sensor when device is non-dict, missing index, or no value_fn."""
        desc_map = {d.key: d for d in OUTLET_SENSOR_TYPES}
        desc = desc_map["power"]
        sensor = UnifiOutletSensor(
            coordinator=mock_coordinator,
            description=desc,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
        )

        # 1) device_data is not a dict (e.g. None or 'invalid') -> line 2086-2087
        mock_coordinator.data = with_sections(
            mock_coordinator.data, devices={"site1": {"pdu1": None}}
        )
        assert sensor._find_outlet_data() is None

        # 2) outlet in outlet_table has no index/outlet_idx/outletIdx -> line 2097->2091
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            devices={
                "site1": {
                    "pdu1": {
                        "outlet_table": [
                            {"name": "No Index Outlet"},
                        ]
                    }
                }
            },
        )
        assert sensor._find_outlet_data() is None

        # 3) value_fn is None -> line 2118-2120
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            devices={
                "site1": {
                    "pdu1": {
                        "outlet_table": [
                            {"index": 1, "power": 12.0},
                        ]
                    }
                }
            },
        )
        desc_no_fn = dataclasses.replace(desc, value_fn=None)
        sensor_no_fn = UnifiOutletSensor(
            coordinator=mock_coordinator,
            description=desc_no_fn,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
        )
        assert sensor_no_fn.native_value is None


class TestUnifiWifiClientCountSensor:
    """Tests for the per-SSID client count sensor."""

    def test_available_follows_site_wifi_fetch(self) -> None:
        """Unavailable when the WiFi fetch for its site failed."""
        coordinator = MagicMock()
        coordinator.data = {
            "wifi": {"site1": {"wifi1": {"name": "Home", "num_connected_clients": 3}}}
        }
        sensor = UnifiWifiClientCountSensor(
            coordinator=coordinator, site_id="site1", wifi_id="wifi1"
        )
        assert sensor.available is True
        assert sensor.translation_key == "wifi_client_count"
        assert sensor.has_entity_name is True
        assert getattr(sensor, "_attr_name", None) is None

        set_availability(coordinator, failed_sections={("wifi", "site1")})
        assert sensor.available is False


class TestUnifiSiteInternetActivitySensor:
    """Tests for site-level internet activity rolling-window sensors."""

    def test_eight_sensor_descriptions_and_values(self) -> None:
        """All 8 descriptions use DATA_SIZE, MEASUREMENT, BYTES, and suggested GB."""
        assert len(SITE_INTERNET_ACTIVITY_SENSOR_TYPES) == 8
        expected_keys = {
            "internet_download_1h": 1_000,
            "internet_upload_1h": 2_000,
            "internet_download_1d": 3_000,
            "internet_upload_1d": 4_000,
            "internet_download_1w": 5_000,
            "internet_upload_1w": 6_000,
            "internet_download_1m": 7_000,
            "internet_upload_1m": 8_000,
        }

        coordinator = MagicMock()
        coordinator.last_update_success = True
        coordinator.data = {
            "sites": {"site1": {"name": "Default"}},
            "devices": {},
            "internet_activity": {
                "site1": {
                    "1h": {"rx_bytes": 1_000, "tx_bytes": 2_000},
                    "1d": {"rx_bytes": 3_000, "tx_bytes": 4_000},
                    "1w": {"rx_bytes": 5_000, "tx_bytes": 6_000},
                    "1m": {"rx_bytes": 7_000, "tx_bytes": 8_000},
                }
            },
        }

        for desc in SITE_INTERNET_ACTIVITY_SENSOR_TYPES:
            assert desc.device_class == SensorDeviceClass.DATA_SIZE
            assert desc.state_class == SensorStateClass.MEASUREMENT
            assert desc.native_unit_of_measurement == UnitOfInformation.BYTES
            assert desc.suggested_unit_of_measurement == UnitOfInformation.GIGABYTES
            assert desc.translation_key == desc.key

            sensor = UnifiSiteInternetActivitySensor(
                coordinator=coordinator,
                description=desc,
                site_id="site1",
                unique_id="console_site1_" + desc.key,
            )
            assert "_attr_name" not in sensor.__dict__

            assert sensor.entity_description.name in (None, UNDEFINED)
            assert desc.name in (None, UNDEFINED)
            assert desc.key.startswith("internet_")
            assert sensor.unique_id == f"console_site1_{desc.key}"
            assert sensor.available is True
            assert sensor.native_value == expected_keys[desc.key]
            assert sensor.extra_state_attributes == {
                "period": desc.period_label,
                "unifi_window": desc.unifi_window,
                "direction": desc.direction,
                "report_interval": desc.report_interval,
            }

    def test_unavailable_when_section_unavailable_or_window_missing(
        self, hass: HomeAssistant, mock_config_entry: MagicMock
    ) -> None:
        """Sensor is unavailable on failed refresh or missing window."""
        desc = SITE_INTERNET_ACTIVITY_SENSOR_TYPES[0]
        coordinator = MagicMock()
        coordinator.last_update_success = True
        coordinator.data = {
            "sites": {"site1": {"name": "Default"}},
            "devices": {"site1": {"gw1": {"features": ["gateway"]}}},
            "internet_activity": {"site1": {"1h": {"rx_bytes": 1024}}},
        }

        sensor = UnifiSiteInternetActivitySensor(
            coordinator=coordinator,
            description=desc,
            site_id="site1",
            unique_id="console_site1_" + desc.key,
        )
        assert sensor.available is True

        # Section marked unavailable in the captured availability
        set_availability(coordinator, failed_sections={("internet_activity", "site1")})
        assert sensor.available is False

        # Section marked unavailable via data set
        set_availability(coordinator)
        coordinator.data = with_sections(
            coordinator.data, internet_activity_unavailable={"site1"}
        )
        assert sensor.available is False

        # Missing window value
        coordinator.data = with_sections(
            coordinator.data, internet_activity_unavailable=set()
        )
        coordinator.data = _with_value(
            coordinator.data, ("internet_activity", "site1"), {}
        )
        assert sensor.available is False
        assert sensor.native_value is None

        # Config refresh failed
        coordinator.data = _with_value(
            coordinator.data, ("internet_activity", "site1"), {"1h": {"rx_bytes": 1024}}
        )
        set_availability(coordinator, config=False)
        assert sensor.available is False

        # Coordinator last_update_success is False
        set_availability(coordinator, config=True)
        coordinator.last_update_success = False
        assert sensor.available is False
        coordinator.last_update_success = True

        # Non-dict site_devices, non-gateway device, non-dict data/site_windows,
        # and bool value
        coordinator.data = _with_value(
            coordinator.data, ("devices", "site1"), "not_a_dict"
        )
        assert get_site_gateway_id(coordinator.data, "site1") is None
        coordinator.data = _with_value(
            coordinator.data, ("devices", "site1"), {"sw1": {"features": ["switching"]}}
        )
        assert get_site_gateway_id(coordinator.data, "site1") is None
        coordinator.data = _with_value(
            coordinator.data, ("internet_activity", "site1"), {"1h": {"rx_bytes": True}}
        )
        assert sensor.native_value is None
        coordinator.data = _with_value(
            coordinator.data, ("internet_activity", "site1"), "not_a_dict"
        )
        assert sensor.native_value is None
        assert (
            _discover_site_internet_activity_sensors(
                coordinator,
                set(),
                hass=hass,
                entry=mock_config_entry,
                namespace="console",
            )
            == []
        )
        coordinator.data = with_sections(
            coordinator.data, internet_activity={"site1": {"1h": {"rx_bytes": 100}}}
        )
        known_keys: set[tuple[str, ...]] = set()
        assert (
            len(
                _discover_site_internet_activity_sensors(
                    coordinator,
                    known_keys,
                    hass=hass,
                    entry=mock_config_entry,
                    namespace="console",
                )
            )
            == 1
        )
        assert (
            _discover_site_internet_activity_sensors(
                coordinator,
                known_keys,
                hass=hass,
                entry=mock_config_entry,
                namespace="console",
            )
            == []
        )
        coordinator.data = with_sections(
            coordinator.data, internet_activity="not_a_dict"
        )
        assert (
            _discover_site_internet_activity_sensors(
                coordinator,
                set(),
                hass=hass,
                entry=mock_config_entry,
                namespace="console",
            )
            == []
        )
        assert sensor.native_value is None
        coordinator.data = None
        assert sensor.native_value is None

    @pytest.mark.parametrize(
        ("unavailable_sites", "expected_available"),
        [
            ({"site1"}, False),
            (frozenset({"site1"}), False),
            (["site1"], False),
            (("site1",), False),
            ({"site2"}, True),
            ("site1", True),
            ({"site1": True}, True),
            (None, True),
        ],
    )
    def test_unavailable_sites_shapes(
        self,
        unavailable_sites: object,
        expected_available: bool,  # noqa: FBT001
    ) -> None:
        """Any set, list or tuple of site IDs marks the site; malformed is ignored."""
        desc = SITE_INTERNET_ACTIVITY_SENSOR_TYPES[0]
        coordinator = MagicMock()
        coordinator.last_update_success = True
        coordinator.data = facade_data(
            sites={"site1": {"name": "Default"}},
            site_ids=("site1",),
            internet_activity={"site1": {"1h": {"rx_bytes": 1024}}},
            internet_activity_unavailable=unavailable_sites,
        )

        sensor = UnifiSiteInternetActivitySensor(
            coordinator=coordinator,
            description=desc,
            site_id="site1",
            unique_id="console_site1_" + desc.key,
        )
        assert sensor.available is expected_available


class TestSensorAdditionalCoverageGaps:
    """Targeted tests for remaining sensor.py coverage gaps."""

    def test_temperature_fallbacks(self) -> None:
        """Test get_network_device_temperature fallback branches."""
        assert get_network_device_temperature({"generalTemperature": 40.0}) == 40.0
        assert get_network_device_temperature({"temperatures": "invalid"}) is None
        assert get_network_device_temperature({"temperatures": [None, 123]}) is None
        assert (
            get_network_device_temperature(
                {"temperatures": [{"name": "ambient", "value": 35.5}]}
            )
            == 35.5
        )
        assert (
            get_network_device_temperature(
                {"temperatures": [{"name": "ambient", "value": "unknown"}]}
            )
            is None
        )

    def test_has_storage_info_total_size_fallback(self) -> None:
        """Test _has_storage_info when only total_size is present."""
        data = {"storageInfo": {"total_size": 1000}}
        assert _has_storage_info(data) is True

    def test_outlet_has_metering_invalid_caps(self) -> None:
        """Test _outlet_has_metering when outlet_caps cannot be parsed as int."""
        outlet = {"outlet_caps": "invalid_caps"}
        assert _outlet_has_metering(outlet) is False

    def test_migrate_sensor_units_skips_non_sensor_domain(
        self, hass: HomeAssistant, mock_config_entry: MagicMock
    ) -> None:
        """Test _migrate_sensor_units skips entities whose domain is not sensor."""
        mock_ent = MagicMock(domain="switch", unique_id="site1_dev1_switch")
        mock_reg = MagicMock()
        with (
            patch(
                "custom_components.unifi_insights.sensor.er.async_get",
                return_value=mock_reg,
            ),
            patch(
                "custom_components.unifi_insights.sensor.er.async_entries_for_config_entry",
                return_value=[mock_ent],
            ),
        ):
            _migrate_sensor_units(hass, mock_config_entry)
        mock_reg.async_update_entity_options.assert_not_called()

    def test_outlet_discovery_edge_cases(self) -> None:
        """Test _discover_outlet_sensors skips invalid outlets and dedupes."""
        coordinator = MagicMock()
        coordinator.data = {
            "devices": {
                "site1": {
                    "dev1": {
                        "id": "dev1",
                        "name": "PDU",
                        "state": "ONLINE",
                    }
                }
            }
        }
        known_keys: set[tuple[Any, ...]] = set()
        entities: list[Any] = []

        device_data = {
            "outlet_table": [
                "not_a_dict",
                {"name": "No index"},
                {"index": "unparseable_idx"},
                {"index": 1},  # no metering -> skipped
                {"index": 2, "outlet_power": 10.0},  # valid metering -> created
            ]
        }
        _discover_outlet_sensors(
            coordinator, "site1", "dev1", device_data, known_keys, entities=entities
        )
        expected_keys = {("site1", "dev1", 2, d.key) for d in OUTLET_SENSOR_TYPES}
        assert known_keys == expected_keys
        assert len(entities) == len(OUTLET_SENSOR_TYPES)

        entities_second: list[Any] = []
        _discover_outlet_sensors(
            coordinator,
            "site1",
            "dev1",
            device_data,
            known_keys,
            entities=entities_second,
        )
        assert len(entities_second) == 0

    def test_protect_sensors_discovery_guards(self) -> None:
        """Test _discover_protect_sensors returns early if no client or invalid data."""
        coordinator = MagicMock()
        coordinator.protect_client = None
        entities: list[Any] = []
        _discover_protect_sensors(coordinator, set(), entities)
        assert len(entities) == 0

        coordinator.protect_client = MagicMock()
        coordinator.data = {"protect": "not_a_dict"}
        _discover_protect_sensors(coordinator, set(), entities)
        assert len(entities) == 0

    def test_outlet_sensor_branches(self) -> None:
        """Test UnifiOutletSensor available and native_value branches."""
        coordinator = MagicMock()
        coordinator.data = {
            "devices": {
                "site1": {
                    "dev1": {
                        "id": "dev1",
                        "name": "PDU",
                        "state": "ONLINE",
                        "outlet_table": [
                            "not_a_dict",
                            {"outletIdx": "bad_idx"},
                            {
                                "outletIdx": "1",
                                "name": "Main Outlet",
                                "relay_state": True,
                                "power": 25.0,
                            },
                        ],
                    }
                }
            }
        }
        desc = OUTLET_SENSOR_TYPES[0]
        sensor = UnifiOutletSensor(coordinator, desc, "site1", "dev1", outlet_index=1)

        assert sensor.available is True
        assert sensor.native_value == 25.0
        assert sensor.extra_state_attributes == {
            "index": 1,
            "name": "Main Outlet",
            "relay_state": True,
        }

        set_availability(coordinator, device=False)
        assert sensor.available is False
        set_availability(coordinator, device=True)

        coordinator.data = with_device_fields(
            coordinator.data, "site1", "dev1", outlet_table="not_a_list"
        )
        assert sensor._find_outlet_data() is None
        assert sensor.native_value is None
        assert sensor.extra_state_attributes is None

    def test_site_client_sensor_clients_not_dict(self) -> None:
        """Test UnifiSiteClientSensor native_value with invalid clients."""
        coordinator = MagicMock()
        coordinator.data = {"clients": {"site1": None}}
        desc = SITE_CLIENT_SENSOR_TYPES[0]
        sensor = UnifiSiteClientSensor(
            coordinator,
            desc,
            "site1",
            unique_id="console_site1_" + desc.key,
        )
        assert sensor.native_value == 0

    def test_wifi_client_count_sensor_branches(self) -> None:
        """Test UnifiWifiClientCountSensor name fallbacks, values, and attributes."""
        coordinator = MagicMock()
        coordinator.data = {
            "wifi": {
                "site1": {
                    "w1": {
                        "ssid": "MyGuestSSID",
                        "isGuest": True,
                        "enabled": True,
                        "num_connected_clients": 5,
                    },
                    "w2": {
                        "num_connected_clients": "not_an_int",
                    },
                }
            }
        }
        sensor1 = UnifiWifiClientCountSensor(coordinator, "site1", "w1")
        assert sensor1.native_value == 5
        attrs = sensor1.extra_state_attributes
        assert attrs["ssid"] == "MyGuestSSID"
        assert attrs["is_guest"] is True
        assert attrs["enabled"] is True

        sensor2 = UnifiWifiClientCountSensor(coordinator, "site1", "w2")
        assert sensor2.native_value == 0

    async def test_async_setup_entry_stale_port_cleanup_and_branches(
        self, hass: HomeAssistant, mock_config_entry: MagicMock
    ) -> None:
        """Test stale port sensor cleanup and async_setup_entry branches."""
        coordinator = MagicMock()
        coordinator.data = {
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "name": "Switch",
                        "state": "ONLINE",
                        "interfaces": {"ports": [{"idx": 1, "state": "UP"}]},
                    },
                    "invalid_device": "not_a_dict",
                },
                "invalid_site": "not_a_dict",
            },
            "clients": {"site1": {}},
            "wifi": {
                "site1": {
                    "w1": {"name": "WiFi", "num_connected_clients": 2},
                    "invalid_wifi": "not_a_dict",
                },
                "invalid_wifi_site": "not_a_dict",
            },
        }
        coordinator.protect_client = None
        coordinator.async_add_listener = MagicMock()

        mock_config_entry.runtime_data = MagicMock(
            coordinator=coordinator, mobility_coordinator=None
        )
        async_add_entities = MagicMock()

        mock_stale = MagicMock(
            domain="sensor",
            platform=DOMAIN,
            config_entry_id=mock_config_entry.entry_id,
            translation_key="port_speed",
            unique_id="device1_port_speed_99",
            entity_id="sensor.stale_port",
        )
        mock_reg = MagicMock()

        with (
            patch(
                "custom_components.unifi_insights.sensor.er.async_get",
                return_value=mock_reg,
            ),
            patch(
                "custom_components.unifi_insights.sensor.er.async_entries_for_config_entry",
                return_value=[mock_stale],
            ),
        ):
            await async_setup_entry(hass, mock_config_entry, async_add_entities)

        mock_reg.async_remove.assert_called_with("sensor.stale_port")
        assert coordinator.async_add_listener.called

        discover_callback = coordinator.async_add_listener.call_args[0][0]
        discover_callback()

        coordinator.data = None
        discover_callback()

    async def test_voucher_sensor_discovery_and_deduplication(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry, monkeypatch
    ) -> None:
        """Test that voucher sensors are discovered and deduplicated."""
        coordinator = MagicMock()
        coordinator.data = {
            "sites": {"default": {"desc": "Default"}},
            "vouchers": {"default": {}},
            "latest_vouchers": {},
        }
        coordinator.protect_client = None
        listeners = []
        coordinator.async_add_listener = listeners.append

        patch_attribute(
            monkeypatch, mock_config_entry, "data", {"console_id": "console"}
        )
        mock_config_entry.runtime_data = MagicMock(
            coordinator=coordinator, mobility_coordinator=None
        )
        added_entities: list = []

        async_add_entities = EntityCollector(added_entities)

        with (
            patch(
                "custom_components.unifi_insights.sensor.er.async_get",
                return_value=MagicMock(
                    async_get_entity_id=MagicMock(return_value=None)
                ),
            ),
            patch(
                "custom_components.unifi_insights.sensor.er.async_entries_for_config_entry",
                return_value=[],
            ),
        ):
            await async_setup_entry(hass, mock_config_entry, async_add_entities)

        voucher_entities = [
            e for e in added_entities if isinstance(e, UnifiVoucherSensor)
        ]
        assert len(voucher_entities) == 3
        count_before = len(added_entities)

        # Trigger listener (deduplication check)
        for listener in listeners:
            listener()
        assert len(added_entities) == count_before

    @pytest.mark.asyncio
    async def test_stale_port_cleanup_keeps_storage_rows_with_port_in_path(
        self, hass: HomeAssistant, mock_config_entry: MagicMock
    ) -> None:
        """A storage row whose mount path contains "_port_" is not a stale port."""
        coordinator = MagicMock()
        coordinator.data = {"devices": {"site1": {}}, "clients": {"site1": {}}}
        coordinator.protect_client = None
        coordinator.async_add_listener = MagicMock()
        mock_config_entry.runtime_data = MagicMock(
            coordinator=coordinator, mobility_coordinator=None
        )

        stale_port = MagicMock(
            domain="sensor",
            platform=DOMAIN,
            config_entry_id=mock_config_entry.entry_id,
            translation_key="port_speed",
            unique_id="device1_port_speed_99",
            entity_id="sensor.stale_port",
        )
        storage_row = MagicMock(
            domain="sensor",
            unique_id="site1_device1_storage_/mnt/usb_port_1_used_percent",
            entity_id="sensor.usb_storage_used",
        )
        mock_reg = MagicMock()

        with (
            patch(
                "custom_components.unifi_insights.sensor.er.async_get",
                return_value=mock_reg,
            ),
            patch(
                "custom_components.unifi_insights.sensor.er.async_entries_for_config_entry",
                return_value=[stale_port, storage_row],
            ),
        ):
            await async_setup_entry(hass, mock_config_entry, MagicMock())

        removed = [call.args[0] for call in mock_reg.async_remove.call_args_list]
        assert removed == ["sensor.stale_port"]


@pytest.mark.usefixtures("enable_custom_integrations")
class TestStalePortCleanupNativeRegistry:
    """Preserve ownership and logical rows during native physical-port cleanup."""

    @pytest.fixture
    def mock_config_entry(self) -> MockConfigEntry:
        """Use a real synthetic config entry instead of this module's mock fixture."""
        return MockConfigEntry(
            domain=DOMAIN,
            entry_id="synthetic_cleanup_entry",
            unique_id="console_a",
            data={
                CONF_CONSOLE_ID: "console_a",
                CONF_API_KEY: "synthetic_credential",
            },
        )

    @pytest.fixture
    def cleanup_coordinator(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        mock_config_entry: MockConfigEntry,
    ) -> MagicMock:
        """Set up a registered entry with one device and no active port readings."""
        mock_config_entry.add_to_hass(hass)
        mock_coordinator.protect_client = None
        mock_coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {
                "site1": {
                    "device1": {
                        "name": "Synthetic switch",
                        "state": "ONLINE",
                        "ports": [],
                    }
                }
            },
            "clients": {},
            "vouchers": {},
            "internet_activity": {},
        }
        mock_coordinator.async_add_listener.return_value = lambda: None
        mock_config_entry.runtime_data = SimpleNamespace(
            coordinator=mock_coordinator, mobility_coordinator=None
        )
        return mock_coordinator

    @pytest.mark.parametrize(
        ("namespace", "site_id"),
        [
            ("console_port_a", "site_shared"),
            ("console_a", "site_port_shared"),
        ],
    )
    @pytest.mark.parametrize("translation_key", [None, "incompatible_key"])
    async def test_skipped_logical_rows_survive_port_cleanup(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_config_entry: MockConfigEntry,
        cleanup_coordinator: MagicMock,
        namespace: str,
        *,
        site_id: str,
        translation_key: str | None,
    ) -> None:
        """Keep skipped logical rows when namespace or site text contains _port_."""
        hass.config_entries.async_update_entry(
            mock_config_entry,
            data={**mock_config_entry.data, CONF_CONSOLE_ID: namespace},
        )
        cleanup_coordinator.data = with_sections(
            cleanup_coordinator.data, sites={site_id: {"id": site_id}}
        )
        cleanup_coordinator.data = with_sections(
            cleanup_coordinator.data, clients={site_id: {}}
        )
        row = entity_registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{namespace}_{site_id}_site_total_clients",
            config_entry=mock_config_entry,
            translation_key=translation_key,
        )
        row = entity_registry.async_update_entity(
            row.entity_id,
            name="Custom logical sensor",
            disabled_by=RegistryEntryDisabler.USER,
            hidden_by=RegistryEntryHider.USER,
        )
        added: list[Entity] = []
        await async_setup_entry(hass, mock_config_entry, EntityCollector(added))
        assert row.unique_id not in {entity.unique_id for entity in added}
        assert entity_registry.async_get(row.entity_id) == row

    @pytest.mark.parametrize(
        "description",
        [*PORT_SENSOR_TYPES, *PORT_RATE_SENSOR_TYPES, *SFP_SENSOR_TYPES],
        ids=lambda description: description.key,
    )
    async def test_removed_device_ports_still_clean_up(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_config_entry: MockConfigEntry,
        cleanup_coordinator: MagicMock,
        description: UnifiInsightsSensorEntityDescription,
    ) -> None:
        """Clean up every actual port family even after its device is removed."""
        port = UnifiPortSensor(
            cleanup_coordinator, description, "site1", "device1", port_idx=99
        )
        row = entity_registry.async_get_or_create(
            "sensor",
            DOMAIN,
            require_not_none(port.unique_id),
            config_entry=mock_config_entry,
            translation_key=port.translation_key,
        )
        cleanup_coordinator.data = with_sections(cleanup_coordinator.data, devices={})
        await async_setup_entry(hass, mock_config_entry, MagicMock())
        assert entity_registry.async_get(row.entity_id) is None

    @pytest.mark.parametrize("devices", [None, []], ids=["null", "list"])
    async def test_unknown_device_inventory_preserves_native_registry_rows(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_config_entry: MockConfigEntry,
        cleanup_coordinator: MagicMock,
        devices: Any,
    ) -> None:
        """Keep native rows when startup has no sites or usable device inventory."""
        other = MockConfigEntry(
            domain=DOMAIN,
            entry_id="synthetic_foreign_cleanup_entry",
            unique_id="console_b",
            data={
                CONF_CONSOLE_ID: "console_b",
                CONF_API_KEY: "synthetic_foreign_credential",
            },
        )
        other.add_to_hass(hass)
        own_port = UnifiPortSensor(
            cleanup_coordinator, PORT_SENSOR_TYPES[1], "site1", "device1", 99
        )
        cleanup_coordinator.data = with_network_device(
            cleanup_coordinator.data,
            "site1",
            "foreign_device",
            {
                **cleanup_coordinator.data["devices"]["site1"]["device1"],
                "name": "Synthetic foreign switch",
            },
        )
        foreign_port = UnifiPortSensor(
            cleanup_coordinator, PORT_SENSOR_TYPES[1], "site1", "foreign_device", 99
        )
        rows = [
            entity_registry.async_get_or_create(
                "sensor",
                DOMAIN,
                require_not_none(own_port.unique_id),
                config_entry=mock_config_entry,
            ),
            entity_registry.async_get_or_create(
                "sensor",
                DOMAIN,
                "console_a_site1_site_total_clients",
                config_entry=mock_config_entry,
                translation_key="site_total_clients",
            ),
            entity_registry.async_get_or_create(
                "sensor",
                DOMAIN,
                require_not_none(foreign_port.unique_id),
                config_entry=other,
                translation_key=foreign_port.translation_key,
            ),
        ]
        rows = [
            entity_registry.async_update_entity(
                row.entity_id,
                name=f"Custom retained sensor {index}",
                disabled_by=RegistryEntryDisabler.USER,
                hidden_by=RegistryEntryHider.USER,
            )
            for index, row in enumerate(rows)
        ]
        cleanup_coordinator.data = with_sections(cleanup_coordinator.data, sites={})
        cleanup_coordinator.data = with_sections(
            cleanup_coordinator.data, devices=devices
        )
        added: list[Entity] = []
        with patch.object(
            entity_registry, "async_remove", wraps=entity_registry.async_remove
        ) as remove:
            await async_setup_entry(hass, mock_config_entry, EntityCollector(added))

        assert not added
        remove.assert_not_called()
        cleanup_coordinator.async_add_listener.assert_called_once()
        for row in rows:
            assert entity_registry.async_get(row.entity_id) == row

    @pytest.mark.parametrize(
        ("translation_key", "device_present", "removed"),
        [
            (None, True, True),
            ("", True, True),
            (None, False, False),
            ("incompatible_key", True, False),
        ],
    )
    async def test_missing_port_metadata_requires_current_device_evidence(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_config_entry: MockConfigEntry,
        cleanup_coordinator: MagicMock,
        translation_key: str | None,
        *,
        device_present: bool,
        removed: bool,
    ) -> None:
        """Retain ambiguous rows while cleaning known-device ports without metadata."""
        port = UnifiPortSensor(
            cleanup_coordinator, PORT_SENSOR_TYPES[1], "site1", "device1", 99
        )
        row = entity_registry.async_get_or_create(
            "sensor",
            DOMAIN,
            require_not_none(port.unique_id),
            config_entry=mock_config_entry,
            translation_key=translation_key,
        )
        if not device_present:
            cleanup_coordinator.data = with_sections(
                cleanup_coordinator.data, devices={}
            )
        await async_setup_entry(hass, mock_config_entry, MagicMock())
        assert (entity_registry.async_get(row.entity_id) is None) is removed

    @pytest.mark.parametrize("ownership", ["foreign", "platform", "domain"])
    async def test_port_cleanup_respects_registry_ownership(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_config_entry: MockConfigEntry,
        cleanup_coordinator: MagicMock,
        ownership: str,
    ) -> None:
        """Retain port-shaped rows owned by another entry, platform, or domain."""
        other = MockConfigEntry(
            domain=DOMAIN,
            entry_id="synthetic_foreign_entry",
            data={CONF_CONSOLE_ID: "foreign_console"},
            unique_id="foreign_console",
        )
        other.add_to_hass(hass)
        port = UnifiPortSensor(
            cleanup_coordinator, PORT_SENSOR_TYPES[1], "site1", "device1", 99
        )
        row = entity_registry.async_get_or_create(
            "binary_sensor" if ownership == "domain" else "sensor",
            "other_platform" if ownership == "platform" else DOMAIN,
            require_not_none(port.unique_id),
            config_entry=other if ownership == "foreign" else mock_config_entry,
            translation_key=port.translation_key,
            disabled_by=RegistryEntryDisabler.USER,
        )
        await async_setup_entry(hass, mock_config_entry, MagicMock())
        assert entity_registry.async_get(row.entity_id) == row

    @pytest.mark.parametrize(
        ("unique_id", "removed"),
        [
            ("device1_port_speed_-1", True),
            ("device1_port_speed_0", True),
            ("device1_port_speed_99", True),
            ("device1_port_speed_099", False),
            ("device1_port_speed_bad", False),
            ("device1_port_speed_", False),
            ("_port_speed_99", False),
            ("device1_port_unknown_99", False),
            ("device1_port_speed", False),
        ],
    )
    async def test_port_cleanup_requires_constructor_identity_shape(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_config_entry: MockConfigEntry,
        cleanup_coordinator: MagicMock,
        unique_id: str,
        *,
        removed: bool,
    ) -> None:
        """Require a nonempty device, known key, and canonical integer port tail."""
        row = entity_registry.async_get_or_create(
            "sensor",
            DOMAIN,
            unique_id,
            config_entry=mock_config_entry,
            translation_key="port_speed",
        )
        await async_setup_entry(hass, mock_config_entry, MagicMock())
        assert (entity_registry.async_get(row.entity_id) is None) is removed

    async def test_active_port_retains_native_registry_identity(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_config_entry: MockConfigEntry,
        cleanup_coordinator: MagicMock,
    ) -> None:
        """Keep an active port's native row and user metadata across discovery."""
        cleanup_coordinator.data = with_device_fields(
            cleanup_coordinator.data,
            "site1",
            "device1",
            ports=[{"idx": 1, "state": "UP"}],
        )
        port = UnifiPortSensor(
            cleanup_coordinator, PORT_SENSOR_TYPES[1], "site1", "device1", 1
        )
        row = entity_registry.async_get_or_create(
            "sensor",
            DOMAIN,
            require_not_none(port.unique_id),
            config_entry=mock_config_entry,
            translation_key=port.translation_key,
            disabled_by=RegistryEntryDisabler.USER,
        )
        row = entity_registry.async_update_entity(
            row.entity_id, name="Custom active port"
        )
        added: list[Entity] = []
        await async_setup_entry(hass, mock_config_entry, EntityCollector(added))
        assert row.unique_id in {entity.unique_id for entity in added}
        assert entity_registry.async_get(row.entity_id) == row

    @pytest.mark.parametrize(
        "description", STORAGE_SENSOR_TYPES, ids=lambda description: description.key
    )
    async def test_storage_rows_with_port_text_survive_cleanup(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_config_entry: MockConfigEntry,
        cleanup_coordinator: MagicMock,
        description: UnifiStorageSensorEntityDescription,
    ) -> None:
        """Preserve native storage rows whose mount paths contain _port_."""
        storage = UnifiStorageSensor(
            cleanup_coordinator,
            description,
            "site1",
            "device1",
            mount_point="/mnt/usb_port_1",
            mount_label="Synthetic USB storage",
            volatile=False,
        )
        row = entity_registry.async_get_or_create(
            "sensor",
            DOMAIN,
            require_not_none(storage.unique_id),
            config_entry=mock_config_entry,
            translation_key=storage.translation_key,
            disabled_by=RegistryEntryDisabler.USER,
        )
        await async_setup_entry(hass, mock_config_entry, MagicMock())
        assert entity_registry.async_get(row.entity_id) == row


def test_port_speed_statistics_metadata(
    hass: HomeAssistant, mock_coordinator: MagicMock
) -> None:
    """Port speed exposes data-rate measurements in Mbit/s for statistics."""
    mock_coordinator.hass = hass
    mock_coordinator.data = with_sections(
        mock_coordinator.data, devices={"site1": {"device1": {"name": "Switch"}}}
    )
    description = next(desc for desc in PORT_SENSOR_TYPES if desc.key == "port_speed")
    sensor = UnifiPortSensor(mock_coordinator, description, "site1", "device1", 25)
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.device_class == SensorDeviceClass.DATA_RATE
    assert sensor.native_unit_of_measurement == UnitOfDataRate.MEGABITS_PER_SECOND
