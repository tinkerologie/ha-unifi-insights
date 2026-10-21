# Copyright (c) 2026 Ruaan Deysel
"""Tests for legacy port-stats end-to-end path, rates, and fallbacks."""

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from custom_components.unifi_insights.coordinators.device import (
    UnifiInsightsDeviceCoordinator,
)
from custom_components.unifi_insights.sensor import (
    PORT_RATE_SENSOR_TYPES,
    PORT_SENSOR_TYPES,
    SFP_SENSOR_TYPES,
    UnifiPortSensor,
)
from tests.fixtures.facade_data import (
    facade_data,
    set_availability,
    with_device_fields,
    with_device_stats,
    with_device_stats_fields,
    with_network_device,
    with_sections,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.fixture
def mock_facade_coordinator() -> MagicMock:
    """Create a mock facade coordinator for port sensor tests."""
    coordinator = MagicMock()
    coordinator.data = facade_data(
        devices={
            "site1": {
                "device1": {
                    "id": "device1",
                    "mac": "00:11:22:33:44:55",
                    "name": "Switch 1",
                }
            }
        },
        stats={"site1": {"device1": {}}},
    )
    return coordinator


class TestEndToEndLegacyPortStatsPath:
    """End-to-end test: legacy /stat/device data -> merge/rate code -> port sensors."""

    def test_legacy_payload_to_port_sensors(self, hass: HomeAssistant):
        """Feed legacy port data through merge/rates and assert sensor values."""
        # Step 1: Realistic legacy device payload from /stat/device
        legacy_device = {
            "mac": "00:11:22:33:44:55",
            "port_table": [
                {
                    "port_idx": 1,
                    "up": True,
                    "enable": True,
                    "speed": 1000,
                    "name": "Port 1",
                    "media": "GE",
                    "is_uplink": False,
                    "port_poe": True,
                    "poe_enable": True,
                    "poe_power": "1.5",
                    "poe_good": True,
                    "tx_bytes": 100000,
                    "rx_bytes": 200000,
                },
                {
                    "port_idx": 2,
                    "up": True,
                    "enable": True,
                    "speed": 1000,
                    "name": "Port 2",
                    "port_poe": True,
                    "poe_enable": True,
                    "poe_power": 0,
                    "poe_good": False,
                    "tx_bytes": 0,
                    "rx_bytes": 0,
                },
                {
                    "port_idx": 3,
                    "up": True,
                    "enable": True,
                    "speed": 1000,
                    "name": "Port 3",
                    "port_poe": True,
                    "poe_enable": True,
                    "poe_power": "bad",
                    "poe_good": False,
                    "tx_bytes": 50000,
                    "rx_bytes": 50000,
                },
            ],
        }

        device_dict = {
            "id": "device1",
            "mac": "00:11:22:33:44:55",
            "name": "USW-Lite-8-PoE",
        }
        legacy_mapping = {"00:11:22:33:44:55": legacy_device}

        # Step 2: Merge legacy port data into device dict (no interfaces.ports)
        UnifiInsightsDeviceCoordinator._merge_legacy_port_data(
            device_dict, legacy_mapping
        )
        assert "ports" in device_dict
        assert len(device_dict["ports"]) == 3

        # Step 3: Run coordinator rate computation across two polls
        entry = MagicMock()
        entry.entry_id = "test_entry"
        device_coord = UnifiInsightsDeviceCoordinator(
            hass=hass,
            network_client=MagicMock(),
            protect_client=MagicMock(),
            entry=entry,
            config_coordinator=MagicMock(),
        )

        t0 = 1000.0
        # First sample: derive the stats the update pipeline would extract from
        # the same legacy port_table that was merged above, rather than
        # hand-building a second copy of the data.
        port_table = legacy_device["port_table"]
        device_coord.data = {
            "stats": {
                "site1": {
                    "device1": {
                        "port_bytes": {
                            p["port_idx"]: {
                                "tx_bytes": p["tx_bytes"],
                                "rx_bytes": p["rx_bytes"],
                            }
                            for p in port_table
                        },
                        "poe_ports": {
                            p["port_idx"]: p["poe_power"] for p in port_table
                        },
                    }
                }
            }
        }
        with patch("time.monotonic", return_value=t0):
            device_coord.data["stats"] = device_coord._compute_port_rates(
                device_coord.data["stats"]
            )

        # Second sample 10 seconds later:
        # Port 1: +10,000 bytes tx (1,000 B/s), +20,000 bytes rx (2,000 B/s)
        # Port 2: 0 bytes delta (0 B/s -> rate MUST stay 0)
        # Port 3: +10,000 bytes tx (1,000 B/s), +20,000 bytes rx (2,000 B/s)
        t1 = t0 + 10.0
        device_coord.data["stats"]["site1"]["device1"]["port_bytes"] = {
            1: {"tx_bytes": 110000, "rx_bytes": 220000},
            2: {"tx_bytes": 0, "rx_bytes": 0},
            3: {"tx_bytes": 60000, "rx_bytes": 70000},
        }
        with patch("time.monotonic", return_value=t1):
            device_coord.data["stats"] = device_coord._compute_port_rates(
                device_coord.data["stats"]
            )

        rates = device_coord.data["stats"]["site1"]["device1"]["port_rates"]
        assert rates[1]["tx_bytes_rate"] == 1000.0
        assert rates[1]["rx_bytes_rate"] == 2000.0
        assert rates[2]["tx_bytes_rate"] == 0.0
        assert rates[2]["rx_bytes_rate"] == 0.0
        assert rates[3]["tx_bytes_rate"] == 1000.0
        assert rates[3]["rx_bytes_rate"] == 2000.0

        # Step 4: Bind data to a facade coordinator and query port sensors
        facade = MagicMock()
        facade.data = {
            "devices": {"site1": {"device1": device_dict}},
            "stats": device_coord.data["stats"],
        }

        # Assert port 1 values (rate = bytes/sec * 8)
        s_tx_rate = UnifiPortSensor(
            facade, PORT_RATE_SENSOR_TYPES[0], "site1", "device1", 1
        )
        s_rx_rate = UnifiPortSensor(
            facade, PORT_RATE_SENSOR_TYPES[1], "site1", "device1", 1
        )
        s_tx_bytes = UnifiPortSensor(
            facade, PORT_SENSOR_TYPES[2], "site1", "device1", 1
        )
        s_rx_bytes = UnifiPortSensor(
            facade, PORT_SENSOR_TYPES[3], "site1", "device1", 1
        )
        s_poe = UnifiPortSensor(facade, PORT_SENSOR_TYPES[0], "site1", "device1", 1)

        assert s_tx_rate.native_value == 8000  # 1000.0 * 8
        assert s_rx_rate.native_value == 16000  # 2000.0 * 8
        assert s_tx_bytes.native_value == 110000
        assert s_rx_bytes.native_value == 220000
        assert s_poe.native_value == 1.5

        # Assert port 2 zero values: 0 MUST STAY 0, not None/unknown
        s2_tx_rate = UnifiPortSensor(
            facade, PORT_RATE_SENSOR_TYPES[0], "site1", "device1", 2
        )
        s2_rx_rate = UnifiPortSensor(
            facade, PORT_RATE_SENSOR_TYPES[1], "site1", "device1", 2
        )
        s2_tx_bytes = UnifiPortSensor(
            facade, PORT_SENSOR_TYPES[2], "site1", "device1", 2
        )
        s2_rx_bytes = UnifiPortSensor(
            facade, PORT_SENSOR_TYPES[3], "site1", "device1", 2
        )
        s2_poe = UnifiPortSensor(facade, PORT_SENSOR_TYPES[0], "site1", "device1", 2)

        assert s2_tx_rate.native_value == 0
        assert isinstance(s2_tx_rate.native_value, int)
        assert s2_rx_rate.native_value == 0
        assert isinstance(s2_rx_rate.native_value, int)
        assert s2_tx_bytes.native_value == 0
        assert isinstance(s2_tx_bytes.native_value, int)
        assert s2_rx_bytes.native_value == 0
        assert isinstance(s2_rx_bytes.native_value, int)
        assert s2_poe.native_value == 0.0

        # Assert port 3 bad PoE string -> None
        s3_poe = UnifiPortSensor(facade, PORT_SENSOR_TYPES[0], "site1", "device1", 3)
        assert s3_poe.native_value is None


class TestPortSensorStatsFallbackWithoutPortData:
    """Test UnifiPortSensor.native_value when _find_port_data returns None."""

    def test_poe_power_stats_fallback_int_and_str_keys(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """Test PoE power from stats fallback with int/str keys and strings."""
        poe_desc = PORT_SENSOR_TYPES[0]  # port_poe_power

        # Int key: 0 stays 0.0
        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data, "site1", "device1", poe_ports={1: 0}
        )
        sensor_int_zero = UnifiPortSensor(
            mock_facade_coordinator, poe_desc, "site1", "device1", 1
        )
        assert sensor_int_zero.native_value == 0.0

        # Str key: "1.5" -> 1.5
        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data, "site1", "device1", poe_ports={"2": "1.5"}
        )
        sensor_str = UnifiPortSensor(
            mock_facade_coordinator, poe_desc, "site1", "device1", 2
        )
        assert sensor_str.native_value == 1.5

        # Str key: "bad" -> None
        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data, "site1", "device1", poe_ports={"3": "bad"}
        )
        sensor_bad = UnifiPortSensor(
            mock_facade_coordinator, poe_desc, "site1", "device1", 3
        )
        assert sensor_bad.native_value is None

        # Stats is not a dict
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data, "site1", "device1", None
        )
        sensor_no_stats = UnifiPortSensor(
            mock_facade_coordinator, poe_desc, "site1", "device1", 1
        )
        assert sensor_no_stats.native_value is None

    def test_tx_rx_bytes_stats_fallback_int_and_str_keys(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """Test TX/RX bytes from stats fallback when device has no port data."""
        tx_desc = PORT_SENSOR_TYPES[2]  # port_tx_bytes
        rx_desc = PORT_SENSOR_TYPES[3]  # port_rx_bytes

        # Int key with zero values: 0 stays 0
        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            port_bytes={1: {"tx_bytes": 0, "rx_bytes": 0}},
        )
        sensor_tx = UnifiPortSensor(
            mock_facade_coordinator, tx_desc, "site1", "device1", 1
        )
        sensor_rx = UnifiPortSensor(
            mock_facade_coordinator, rx_desc, "site1", "device1", 1
        )
        assert sensor_tx.native_value == 0
        assert sensor_rx.native_value == 0

        # Str key with positive values
        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            port_bytes={"2": {"tx_bytes": 12345.0, "rx_bytes": 67890.0}},
        )
        sensor_tx_str = UnifiPortSensor(
            mock_facade_coordinator, tx_desc, "site1", "device1", 2
        )
        sensor_rx_str = UnifiPortSensor(
            mock_facade_coordinator, rx_desc, "site1", "device1", 2
        )
        assert sensor_tx_str.native_value == 12345
        assert sensor_rx_str.native_value == 67890

        # Non-numeric or missing stats
        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            port_bytes={3: {"tx_bytes": "not_numeric", "rx_bytes": None}},
        )
        sensor_invalid = UnifiPortSensor(
            mock_facade_coordinator, tx_desc, "site1", "device1", 3
        )
        assert sensor_invalid.native_value is None

    def test_tx_rx_rate_stats_fallback(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """Test TX/RX rate fallback calls _get_port_rate_value."""
        tx_rate_desc = PORT_RATE_SENSOR_TYPES[0]
        rx_rate_desc = PORT_RATE_SENSOR_TYPES[1]

        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            port_rates={1: {"tx_bytes_rate": 12.5, "rx_bytes_rate": 25.0}},
        )
        sensor_tx_rate = UnifiPortSensor(
            mock_facade_coordinator, tx_rate_desc, "site1", "device1", 1
        )
        sensor_rx_rate = UnifiPortSensor(
            mock_facade_coordinator, rx_rate_desc, "site1", "device1", 1
        )
        assert sensor_tx_rate.native_value == 100  # 12.5 * 8
        assert sensor_rx_rate.native_value == 200  # 25.0 * 8

    def test_unsupported_key_returns_none_when_no_port_data(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """Return None and log debug when sensor key has no stats fallback."""
        speed_desc = PORT_SENSOR_TYPES[1]  # port_speed
        sensor = UnifiPortSensor(
            mock_facade_coordinator, speed_desc, "site1", "device1", 1
        )
        assert sensor.native_value is None

    def test_poe_power_stats_fallback_port_absent_from_poe_ports(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """Test PoE power fallback when port is absent from poe_ports (watts None)."""
        poe_desc = PORT_SENSOR_TYPES[0]
        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data, "site1", "device1", poe_ports={99: 5.0}
        )
        sensor = UnifiPortSensor(
            mock_facade_coordinator, poe_desc, "site1", "device1", 1
        )
        assert sensor.native_value is None

    def test_tx_rx_bytes_stats_fallback_missing_or_non_dict_branches(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """Test TX/RX bytes fallback when stats/port_bytes/pb is non-dict."""
        tx_desc = PORT_SENSOR_TYPES[2]
        sensor = UnifiPortSensor(
            mock_facade_coordinator, tx_desc, "site1", "device1", 1
        )

        # 1) stats is not a dict
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data, "site1", "device1", None
        )
        assert sensor.native_value is None

        # 2) port_bytes is not a dict
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data, "site1", "device1", {"port_bytes": None}
        )
        assert sensor.native_value is None

        # 3) pb is not a dict (port absent or not a dict)
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            {"port_bytes": {99: {"tx_bytes": 100}}},
        )
        assert sensor.native_value is None


class TestPortSensorWithPortDataCountersAndRates:
    """Test UnifiPortSensor.native_value branches when port_data IS present."""

    def test_tx_rx_bytes_preferred_port_bytes_and_fallback_port_data(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """Test preferred stats['port_bytes'] and fallback stats['port_data']."""
        tx_desc = PORT_SENSOR_TYPES[2]
        rx_desc = PORT_SENSOR_TYPES[3]

        # Add ports to device_data so _find_port_data finds the port
        mock_facade_coordinator.data = with_device_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            ports=[{"idx": 1, "state": "UP"}],
        )

        # 1) Preferred: port_bytes present (with zero values)
        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            port_bytes={1: {"tx_bytes": 0, "rx_bytes": 0}},
        )
        sensor_tx = UnifiPortSensor(
            mock_facade_coordinator, tx_desc, "site1", "device1", 1
        )
        sensor_rx = UnifiPortSensor(
            mock_facade_coordinator, rx_desc, "site1", "device1", 1
        )
        assert sensor_tx.native_value == 0
        assert sensor_rx.native_value == 0

        # 2) Fallback: port_bytes absent, port_data present (int and str keys)
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            {"port_data": {"1": {"tx_bytes": 5000, "rx_bytes": 10000}}},
        )
        assert sensor_tx.native_value == 5000
        assert sensor_rx.native_value == 10000

        # 3) Neither present
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data, "site1", "device1", {}
        )
        assert sensor_tx.native_value is None

        # 4) Stats is not a dict
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data, "site1", "device1", "not_dict"
        )
        assert sensor_tx.native_value is None

    def test_poe_power_with_port_data_string_and_bad_values(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """Test PoE power parsing with port_data present for strings and bad input."""
        poe_desc = PORT_SENSOR_TYPES[0]
        mock_facade_coordinator.data = with_device_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            ports=[{"idx": 1, "state": "UP"}],
        )

        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data, "site1", "device1", poe_ports={"1": "3.3"}
        )
        sensor = UnifiPortSensor(
            mock_facade_coordinator, poe_desc, "site1", "device1", 1
        )
        assert sensor.native_value == 3.3

        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data, "site1", "device1", poe_ports={"1": "invalid"}
        )
        assert sensor.native_value is None

    def test_poe_power_with_port_data_stats_not_dict_or_port_absent(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """Test PoE power when stats is non-dict or port absent from poe_ports."""
        poe_desc = PORT_SENSOR_TYPES[0]
        mock_facade_coordinator.data = with_device_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            ports=[{"idx": 1, "state": "UP", "poe_power_w": 7.5}],
        )
        sensor = UnifiPortSensor(
            mock_facade_coordinator, poe_desc, "site1", "device1", 1
        )

        # stats is not dict
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data, "site1", "device1", None
        )
        assert sensor.native_value == 7.5

        # port absent from poe_ports (watts is None)
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data, "site1", "device1", {"poe_ports": {99: 5.0}}
        )
        assert sensor.native_value == 7.5

    def test_tx_rx_bytes_port_data_fallback_edges(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """Test TX/RX bytes with port data when port_bytes pb is None or non-numeric."""
        tx_desc = PORT_SENSOR_TYPES[2]
        mock_facade_coordinator.data = with_device_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            ports=[{"idx": 1, "state": "UP"}],
        )
        sensor = UnifiPortSensor(
            mock_facade_coordinator, tx_desc, "site1", "device1", 1
        )

        # 1) pb is not in port_bytes, falls through to port_data
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            {
                "port_bytes": {99: {"tx_bytes": 100}},
                "port_data": {1: {"tx_bytes": 500}},
            },
        )
        assert sensor.native_value == 500

        # 2) v is not numeric in port_bytes, falls through to port_data
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            {
                "port_bytes": {1: {"tx_bytes": "invalid"}},
                "port_data": {1: {"tx_bytes": 600}},
            },
        )
        assert sensor.native_value == 600

        # 3) port absent from fallback port_data -> returns None
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            {
                "port_data": {99: {"tx_bytes": 100}},
            },
        )
        assert sensor.native_value is None

        # 4) v is not numeric in fallback port_data -> returns None
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            {
                "port_data": {1: {"tx_bytes": "invalid"}},
            },
        )
        assert sensor.native_value is None


class TestGetPortRateValueBranches:
    """Test all branches of UnifiPortSensor._get_port_rate_value."""

    def test_stats_not_dict_or_port_rates_not_dict(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """Return None when stats or port_rates is not a dict."""
        rate_desc = PORT_RATE_SENSOR_TYPES[0]
        sensor = UnifiPortSensor(
            mock_facade_coordinator, rate_desc, "site1", "device1", 1
        )

        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data, "site1", "device1", "not_dict"
        )
        assert sensor._get_port_rate_value() is None

        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data, "site1", "device1", {"port_rates": "not_dict"}
        )
        assert sensor._get_port_rate_value() is None

    def test_pr_not_dict_or_bytes_per_sec_none(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """Return None when port record is not dict or rate key is None."""
        rate_desc = PORT_RATE_SENSOR_TYPES[0]
        sensor = UnifiPortSensor(
            mock_facade_coordinator, rate_desc, "site1", "device1", 1
        )

        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data, "site1", "device1", port_rates={1: "not_dict"}
        )
        assert sensor._get_port_rate_value() is None

        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            port_rates={1: {"tx_bytes_rate": None}},
        )
        assert sensor._get_port_rate_value() is None

    def test_string_key_and_zero_rate(self, mock_facade_coordinator: MagicMock) -> None:
        """Look up rate with str key and preserve 0.0 -> 0."""
        rate_desc = PORT_RATE_SENSOR_TYPES[0]
        sensor = UnifiPortSensor(
            mock_facade_coordinator, rate_desc, "site1", "device1", 1
        )

        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            port_rates={"1": {"tx_bytes_rate": 0.0}},
        )
        val = sensor._get_port_rate_value()
        assert val == 0
        assert isinstance(val, int)


class TestPortSensorAvailabilityAndAttributes:
    """Test UnifiPortSensor.available, extra_state_attributes, and _find_port_data."""

    def test_available_poe_power_coordinator_stats(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """PoE power sensor availability follows coordinator stats."""
        poe_desc = PORT_SENSOR_TYPES[0]
        sensor = UnifiPortSensor(
            mock_facade_coordinator, poe_desc, "site1", "device1", 1
        )

        # Device offline -> False
        set_availability(mock_facade_coordinator, device=False)
        assert sensor.available is False

        # Device online, poe_ports dict -> True
        set_availability(mock_facade_coordinator, device=True)
        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data, "site1", "device1", poe_ports={1: 5.0}
        )
        assert sensor.available is True

    def test_available_tx_rx_bytes_and_rate_coordinator_stats(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """TX/RX bytes and rates availability follows stats."""
        bytes_desc = PORT_SENSOR_TYPES[2]
        rate_desc = PORT_RATE_SENSOR_TYPES[0]
        s_bytes = UnifiPortSensor(
            mock_facade_coordinator, bytes_desc, "site1", "device1", 1
        )
        s_rate = UnifiPortSensor(
            mock_facade_coordinator, rate_desc, "site1", "device1", 1
        )

        # Device offline -> False
        set_availability(mock_facade_coordinator, device=False)
        assert s_bytes.available is False
        assert s_rate.available is False

        set_availability(mock_facade_coordinator, device=True)
        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            port_bytes={1: {"tx_bytes": 100}},
        )
        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            port_rates={1: {"tx_bytes_rate": 10.0}},
        )
        assert s_bytes.available is True
        assert s_rate.available is True

        # String port index lookup
        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            port_bytes={"1": {"tx_bytes": 100}},
        )
        mock_facade_coordinator.data = with_device_stats_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            port_rates={"1": {"tx_bytes_rate": 10.0}},
        )
        assert s_bytes.available is True
        assert s_rate.available is True

    def test_available_sfp_info_sensors_always_available(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """SFP sensors stay available regardless of port UP/DOWN state."""
        sfp_desc = SFP_SENSOR_TYPES[0]  # port_sfp_temperature
        mock_facade_coordinator.data = with_device_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            ports=[{"idx": 1, "state": "DOWN"}],
        )
        sensor = UnifiPortSensor(
            mock_facade_coordinator, sfp_desc, "site1", "device1", 1
        )
        assert sensor.available is True

    def test_extra_state_attributes(self, mock_facade_coordinator: MagicMock) -> None:
        """Test extra_state_attributes extraction from port data."""
        desc = PORT_SENSOR_TYPES[1]
        mock_facade_coordinator.data = with_device_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            ports=[
                {
                    "idx": 1,
                    "state": "UP",
                    "media": "RJ45",
                    "is_uplink": True,
                    "network_name": "Corporate",
                    "name": "Uplink Port",
                    "sfp_found": False,
                }
            ],
        )
        sensor = UnifiPortSensor(mock_facade_coordinator, desc, "site1", "device1", 1)
        attrs = sensor.extra_state_attributes
        assert attrs == {
            "media_type": "RJ45",
            "is_uplink": True,
            "network": "Corporate",
            "port_name": "Uplink Port",
            "sfp_module_present": False,
        }

        # Port with no matching attributes returns None
        mock_facade_coordinator.data = with_device_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            ports=[{"idx": 1, "state": "UP"}],
        )
        assert sensor.extra_state_attributes is None

    def test_find_port_data_from_interfaces_ports(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """Find port data in interfaces.ports when device_data['ports'] is absent."""
        desc = PORT_SENSOR_TYPES[1]
        mock_facade_coordinator.data = with_device_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            interfaces={"ports": [{"port_idx": 1, "state": "UP", "speedMbps": 1000}]},
        )
        sensor = UnifiPortSensor(mock_facade_coordinator, desc, "site1", "device1", 1)
        port_data = sensor._find_port_data()
        assert port_data is not None
        assert port_data["port_idx"] == 1

    def test_available_poe_power_stats_not_dict(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """PoE power availability falls through to port state when stats is non-dict."""
        poe_desc = PORT_SENSOR_TYPES[0]
        set_availability(mock_facade_coordinator, device=True)
        mock_facade_coordinator.data = with_device_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            ports=[{"idx": 1, "state": "UP"}],
        )
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data, "site1", "device1", None
        )
        sensor = UnifiPortSensor(
            mock_facade_coordinator, poe_desc, "site1", "device1", 1
        )
        assert sensor.available is True

    def test_available_tx_rx_bytes_stats_edges(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """TX/RX bytes availability falls through when stats or pb is invalid."""
        bytes_desc = PORT_SENSOR_TYPES[2]
        set_availability(mock_facade_coordinator, device=True)
        mock_facade_coordinator.data = with_device_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            ports=[{"idx": 1, "state": "UP"}],
        )
        sensor = UnifiPortSensor(
            mock_facade_coordinator, bytes_desc, "site1", "device1", 1
        )

        # stats is not dict
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data, "site1", "device1", None
        )
        assert sensor.available is True

        # port_bytes is not dict
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data, "site1", "device1", {"port_bytes": None}
        )
        assert sensor.available is True

        # pb is not dict (port absent)
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            {"port_bytes": {99: {"tx_bytes": 100}}},
        )
        assert sensor.available is True

    def test_available_tx_rx_rate_stats_edges(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """TX/RX rate availability falls through when stats/port_rates/pr is invalid."""
        rate_desc = PORT_RATE_SENSOR_TYPES[0]
        set_availability(mock_facade_coordinator, device=True)
        mock_facade_coordinator.data = with_device_fields(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            ports=[{"idx": 1, "state": "UP"}],
        )
        sensor = UnifiPortSensor(
            mock_facade_coordinator, rate_desc, "site1", "device1", 1
        )

        # stats is not dict
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data, "site1", "device1", None
        )
        assert sensor.available is True

        # port_rates is not dict
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data, "site1", "device1", {"port_rates": None}
        )
        assert sensor.available is True

        # pr is not dict (port absent)
        mock_facade_coordinator.data = with_device_stats(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            {"port_rates": {99: {"tx_bytes_rate": 10.0}}},
        )
        assert sensor.available is True

    def test_find_port_data_device_missing_or_port_missing(
        self, mock_facade_coordinator: MagicMock
    ) -> None:
        """Test _find_port_data when device is missing or interfaces non-dict."""
        desc = PORT_SENSOR_TYPES[1]
        sensor = UnifiPortSensor(mock_facade_coordinator, desc, "site1", "device1", 1)

        # 1) Device missing in coordinator data
        mock_facade_coordinator.data = with_sections(
            mock_facade_coordinator.data, devices={"site1": {}}
        )
        assert sensor._find_port_data() is None
        assert sensor.extra_state_attributes is None

        # 2) ports empty and interfaces not dict
        mock_facade_coordinator.data = with_network_device(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            {
                "ports": [],
                "interfaces": "not_a_dict",
            },
        )
        assert sensor._find_port_data() is None

        # 3) ports present but port 1 is absent
        mock_facade_coordinator.data = with_network_device(
            mock_facade_coordinator.data,
            "site1",
            "device1",
            {
                "ports": [{"idx": 99, "state": "UP"}],
            },
        )
        assert sensor._find_port_data() is None
