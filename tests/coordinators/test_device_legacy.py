# Copyright (c) 2026 Ruaan Deysel
"""Tests for legacy device coordinator methods and rate computation."""

import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unifi_official_api.network.models.device import (
    LegacyPortMetrics,
    PortBytesMetrics,
)

from custom_components.unifi_insights.coordinators.config_sections import (
    map_legacy_site_names,
)
from custom_components.unifi_insights.coordinators.device import (
    UnifiInsightsDeviceCoordinator,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.fixture
def mock_device_coordinator(hass: HomeAssistant) -> UnifiInsightsDeviceCoordinator:
    """Create a device coordinator for legacy tests."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    config_coordinator = MagicMock()
    config_coordinator.get_site = MagicMock(
        return_value={"id": "default", "internalReference": "default"}
    )
    network_client = MagicMock()
    network_client.devices.get_statistics = AsyncMock(return_value=None)
    protect_client = MagicMock()

    coordinator = UnifiInsightsDeviceCoordinator(
        hass=hass,
        network_client=network_client,
        protect_client=protect_client,
        entry=entry,
        config_coordinator=config_coordinator,
    )
    coordinator.data = {"devices": {}, "stats": {}, "clients": {}}
    return coordinator


class TestMergeLegacyTemperatureData:
    """Test _merge_legacy_temperature_data branches."""

    def test_mac_address_none(self):
        """Return early if MAC cannot be normalized."""
        device_dict: dict[str, Any] = {"name": "No MAC"}
        legacy_devices: dict[str, Any] = {
            "00:11:22:33:44:55": {"mac": "00:11:22:33:44:55"}
        }
        UnifiInsightsDeviceCoordinator._merge_legacy_temperature_data(
            device_dict, legacy_devices
        )
        assert "hasTemperature" not in device_dict

    def test_legacy_device_missing_or_no_temp_data(self):
        """Return early if legacy device is missing or has no temperature data."""
        device_dict: dict[str, Any] = {"mac": "00:11:22:33:44:55"}
        legacy_devices: dict[str, Any] = {
            "00:11:22:33:44:55": {"mac": "00:11:22:33:44:55"}
        }
        UnifiInsightsDeviceCoordinator._merge_legacy_temperature_data(
            device_dict, legacy_devices
        )
        assert "hasTemperature" not in device_dict

    def test_fallback_to_camel_case_general_temperature(self):
        """Fall back to generalTemperature when general_temperature is None."""
        device_dict: dict[str, Any] = {"mac": "00:11:22:33:44:55"}
        legacy_devices: dict[str, Any] = {
            "00:11:22:33:44:55": {
                "mac": "00:11:22:33:44:55",
                "generalTemperature": 45.5,
            }
        }
        UnifiInsightsDeviceCoordinator._merge_legacy_temperature_data(
            device_dict, legacy_devices
        )
        assert device_dict["generalTemperature"] == 45.5
        assert device_dict["hasTemperature"] is True

    def test_temperatures_list_and_general_temp(self):
        """Merge both general_temperature and temperatures list."""
        device_dict: dict[str, Any] = {"mac": "00:11:22:33:44:55"}
        legacy_devices: dict[str, Any] = {
            "00:11:22:33:44:55": {
                "mac": "00:11:22:33:44:55",
                "general_temperature": 52.0,
                "temperatures": [{"name": "CPU", "value": 52.0}],
            }
        }
        UnifiInsightsDeviceCoordinator._merge_legacy_temperature_data(
            device_dict, legacy_devices
        )
        assert device_dict["generalTemperature"] == 52.0
        assert device_dict["temperatures"] == [{"name": "CPU", "value": 52.0}]
        assert device_dict["hasTemperature"] is True

    def test_temperatures_not_a_list(self):
        """Ignore temperatures field if it is not a list."""
        device_dict: dict[str, Any] = {"mac": "00:11:22:33:44:55"}
        legacy_devices: dict[str, Any] = {
            "00:11:22:33:44:55": {
                "mac": "00:11:22:33:44:55",
                "general_temperature": 50.0,
                "temperatures": "invalid_string",
            }
        }
        UnifiInsightsDeviceCoordinator._merge_legacy_temperature_data(
            device_dict, legacy_devices
        )
        assert device_dict["generalTemperature"] == 50.0
        assert "temperatures" not in device_dict
        assert device_dict["hasTemperature"] is True

    def test_temperatures_present_without_general_temperature(self):
        """Merge temperatures list when general_temperature is None."""
        device_dict: dict[str, Any] = {"mac": "00:11:22:33:44:55"}
        legacy_devices: dict[str, Any] = {
            "00:11:22:33:44:55": {
                "mac": "00:11:22:33:44:55",
                "temperatures": [{"name": "CPU", "value": 52.0}],
            }
        }
        UnifiInsightsDeviceCoordinator._merge_legacy_temperature_data(
            device_dict, legacy_devices
        )
        assert "generalTemperature" not in device_dict
        assert device_dict["temperatures"] == [{"name": "CPU", "value": 52.0}]
        assert device_dict["hasTemperature"] is True


class TestMergeLegacyPortData:
    """Test _merge_legacy_port_data branches."""

    def test_mac_address_none(self):
        """Return early if device has no valid MAC."""
        device_dict: dict[str, Any] = {"name": "No MAC"}
        legacy_devices: dict[str, Any] = {
            "00:11:22:33:44:55": {"port_table": [{"port_idx": 1}]}
        }
        UnifiInsightsDeviceCoordinator._merge_legacy_port_data(
            device_dict, legacy_devices
        )
        assert "ports" not in device_dict

    def test_legacy_device_missing(self):
        """Return early if legacy device not in mapping."""
        device_dict: dict[str, Any] = {"mac": "00:11:22:33:44:55"}
        legacy_devices: dict[str, Any] = {}
        UnifiInsightsDeviceCoordinator._merge_legacy_port_data(
            device_dict, legacy_devices
        )
        assert "ports" not in device_dict

    def test_port_table_not_list_or_empty(self):
        """Return early if port_table is missing, non-list, or empty."""
        device_dict: dict[str, Any] = {"mac": "00:11:22:33:44:55"}
        legacy_devices: dict[str, Any] = {"00:11:22:33:44:55": {"port_table": []}}
        UnifiInsightsDeviceCoordinator._merge_legacy_port_data(
            device_dict, legacy_devices
        )
        assert "ports" not in device_dict

        legacy_devices["00:11:22:33:44:55"]["port_table"] = "not_a_list"
        UnifiInsightsDeviceCoordinator._merge_legacy_port_data(
            device_dict, legacy_devices
        )
        assert "ports" not in device_dict

    def test_port_table_non_dict_entry_and_none_normalizer(self):
        """Skip non-dict port entries or entries without port_idx."""
        device_dict: dict[str, Any] = {"mac": "00:11:22:33:44:55"}
        legacy_devices: dict[str, Any] = {
            "00:11:22:33:44:55": {
                "port_table": [
                    "not_a_dict",
                    {"name": "No port_idx"},  # normalizes to None
                    {"port_idx": 1, "name": "Port 1", "up": True},
                ]
            }
        }
        UnifiInsightsDeviceCoordinator._merge_legacy_port_data(
            device_dict, legacy_devices
        )
        assert "ports" in device_dict
        assert len(device_dict["ports"]) == 1
        assert device_dict["ports"][0]["idx"] == 1

    def test_all_ports_normalize_to_none(self):
        """If all port entries fail normalization, ports is not set."""
        device_dict: dict[str, Any] = {"mac": "00:11:22:33:44:55"}
        legacy_devices: dict[str, Any] = {
            "00:11:22:33:44:55": {
                "port_table": [
                    {"name": "No port_idx 1"},
                    {"name": "No port_idx 2"},
                ]
            }
        }
        UnifiInsightsDeviceCoordinator._merge_legacy_port_data(
            device_dict, legacy_devices
        )
        assert "ports" not in device_dict


class TestMergeLegacyOutletData:
    """Test _merge_legacy_outlet_data branches."""

    def test_mac_address_none(self):
        """Return early if MAC is None."""
        device_dict: dict[str, Any] = {"name": "No MAC"}
        legacy_devices: dict[str, Any] = {"00:11:22:33:44:55": {"outlet_table": []}}
        UnifiInsightsDeviceCoordinator._merge_legacy_outlet_data(
            device_dict, legacy_devices
        )
        assert "outlet_table" not in device_dict

    def test_legacy_device_missing(self):
        """Return early if legacy device is missing."""
        device_dict: dict[str, Any] = {"mac": "00:11:22:33:44:55"}
        UnifiInsightsDeviceCoordinator._merge_legacy_outlet_data(device_dict, {})
        assert "outlet_table" not in device_dict

    def test_ac_power_budget_only(self):
        """Merge when only ac_power_budget is present without ac_power_consumption."""
        device_dict: dict[str, Any] = {"mac": "00:11:22:33:44:55"}
        legacy_devices: dict[str, Any] = {
            "00:11:22:33:44:55": {
                "_id": "dev123",
                "mac": "00:11:22:33:44:55",
                "ac_power_budget": 100.0,
            }
        }
        UnifiInsightsDeviceCoordinator._merge_legacy_outlet_data(
            device_dict, legacy_devices
        )
        assert device_dict["_id"] == "dev123"
        assert device_dict["outlet_ac_power_budget"] == 100.0
        assert "outlet_ac_power_consumption" not in device_dict

    def test_ac_power_consumption_and_budget(self):
        """Merge both ac_power_consumption and ac_power_budget."""
        device_dict: dict[str, Any] = {"mac": "00:11:22:33:44:55"}
        legacy_devices: dict[str, Any] = {
            "00:11:22:33:44:55": {
                "_id": "dev123",
                "mac": "00:11:22:33:44:55",
                "ac_power_consumption": 42.5,
                "ac_power_budget": 150.0,
            }
        }
        UnifiInsightsDeviceCoordinator._merge_legacy_outlet_data(
            device_dict, legacy_devices
        )
        assert device_dict["outlet_ac_power_consumption"] == 42.5
        assert device_dict["ac_power_consumption"] == 42.5
        assert device_dict["outlet_ac_power_budget"] == 150.0
        assert device_dict["ac_power_budget"] == 150.0

    def test_outlets_present_without_outlet_overrides(self):
        """Merge outlet_table when outlet_overrides is absent."""
        device_dict: dict[str, Any] = {"mac": "00:11:22:33:44:55"}
        legacy_devices: dict[str, Any] = {
            "00:11:22:33:44:55": {
                "mac": "00:11:22:33:44:55",
                "outlet_table": [{"index": 1, "name": "Outlet 1", "relay_state": True}],
            }
        }
        UnifiInsightsDeviceCoordinator._merge_legacy_outlet_data(
            device_dict, legacy_devices
        )
        assert "outlet_table" in device_dict
        assert "outlet_overrides" not in device_dict

    def test_outlets_present_with_outlet_overrides(self):
        """Merge outlet_table and outlet_overrides when both are present."""
        device_dict: dict[str, Any] = {"mac": "00:11:22:33:44:55"}
        legacy_devices: dict[str, Any] = {
            "00:11:22:33:44:55": {
                "mac": "00:11:22:33:44:55",
                "outlet_table": [{"index": 1, "name": "Outlet 1", "relay_state": True}],
                "outlet_overrides": [{"index": 1, "name": "Override 1"}],
            }
        }
        UnifiInsightsDeviceCoordinator._merge_legacy_outlet_data(
            device_dict, legacy_devices
        )
        assert "outlet_table" in device_dict
        assert device_dict["outlet_overrides"] == [{"index": 1, "name": "Override 1"}]


class TestMapLegacySiteNames:
    """Test the single legacy site mapping rule's edge cases."""

    def test_legacy_site_missing_or_empty_name(self):
        """Skip legacy sites without a valid non-empty string name."""
        legacy_sites = [
            {"desc": "Site Without Name"},
            {"name": "", "desc": "Empty Name"},
            {"name": 123, "desc": "Integer Name"},
            {"name": "site_alpha", "desc": "Alpha Site"},
        ]
        mappings = map_legacy_site_names(
            {"alpha_id": {"name": "Alpha Site", "id": "alpha_id"}}, legacy_sites
        )
        assert mappings.get("alpha_id") == "site_alpha"

    def test_single_legacy_site_fallback(self):
        """Map unmatched site_id to the single legacy site if exactly one exists."""
        legacy_sites = [{"name": "default_legacy", "desc": "Some Name"}]
        mappings = map_legacy_site_names(
            {"unmatched_site": {"name": "Unrelated Name", "id": "unmatched_site"}},
            legacy_sites,
        )
        assert mappings.get("unmatched_site") == "default_legacy"


class TestProcessDeviceSiteResolutionAndStats:
    """Test _process_device fallback site reference resolution and poe metrics."""

    async def test_fallback_internal_reference_camel_case(
        self, mock_device_coordinator
    ):
        """Resolve site_name via internalReference when legacy_site_name is None."""
        device_dict: dict[str, Any] = {
            "id": "dev1",
            "mac": "00:11:22:33:44:55",
            "state": "ONLINE",
        }
        mock_device_coordinator.config_coordinator.get_site = MagicMock(
            return_value={"internalReference": "internal_site_ref"}
        )
        mock_device_coordinator.network_client.devices.get_port_metrics = AsyncMock(
            return_value=LegacyPortMetrics(
                poe_total_w=30.5,
                poe_ports={1: 15.0},
                port_bytes={1: PortBytesMetrics(rx_bytes=100, tx_bytes=200)},
            )
        )

        _, _, stats = await mock_device_coordinator._process_device(
            site_id="site1",
            device_dict=device_dict,
            clients=[],
            legacy_site_name=None,
        )
        mock_device_coordinator.network_client.devices.get_port_metrics.assert_called_with(
            "internal_site_ref", "00:11:22:33:44:55"
        )
        assert stats["poe_total_w"] == 30.5
        assert stats["poe_ports"] == {1: 15.0}

    async def test_fallback_internal_reference_snake_case(
        self, mock_device_coordinator
    ):
        """Resolve site_name via internal_reference when legacy_site_name is None."""
        device_dict: dict[str, Any] = {
            "id": "dev1",
            "mac": "00:11:22:33:44:55",
            "state": "ONLINE",
        }
        mock_device_coordinator.config_coordinator.get_site = MagicMock(
            return_value={"internal_reference": "internal_site_snake"}
        )
        mock_device_coordinator.network_client.devices.get_port_metrics = AsyncMock(
            return_value=LegacyPortMetrics()
        )

        await mock_device_coordinator._process_device(
            site_id="site1",
            device_dict=device_dict,
            clients=[],
            legacy_site_name=None,
        )
        mock_device_coordinator.network_client.devices.get_port_metrics.assert_called_with(
            "internal_site_snake", "00:11:22:33:44:55"
        )

    async def test_fallback_internal_reference_exception(self, mock_device_coordinator):
        """Catch exception when resolving internal site reference."""
        device_dict: dict[str, Any] = {
            "id": "dev1",
            "mac": "00:11:22:33:44:55",
            "state": "ONLINE",
        }
        mock_device_coordinator.config_coordinator.get_site = MagicMock(
            side_effect=RuntimeError("Site lookup failed")
        )
        mock_device_coordinator.network_client.devices.get_port_metrics = AsyncMock(
            return_value=LegacyPortMetrics()
        )

        await mock_device_coordinator._process_device(
            site_id="site1",
            device_dict=device_dict,
            clients=[],
            legacy_site_name=None,
        )
        mock_device_coordinator.network_client.devices.get_port_metrics.assert_called_with(
            "site1", "00:11:22:33:44:55"
        )
        assert mock_device_coordinator.config_coordinator.get_site.called

    async def test_fallback_when_site_obj_not_dict(self, mock_device_coordinator):
        """Resolve site_name via site_id when get_site returns non-dict."""
        device_dict: dict[str, Any] = {
            "id": "dev1",
            "mac": "00:11:22:33:44:55",
            "state": "ONLINE",
        }
        mock_device_coordinator.config_coordinator.get_site = MagicMock(
            return_value=None
        )
        mock_device_coordinator.network_client.devices.get_port_metrics = AsyncMock(
            return_value=LegacyPortMetrics()
        )

        await mock_device_coordinator._process_device(
            site_id="site1",
            device_dict=device_dict,
            clients=[],
            legacy_site_name=None,
        )
        mock_device_coordinator.network_client.devices.get_port_metrics.assert_called_with(
            "site1", "00:11:22:33:44:55"
        )


class TestComputePortRatesBranches:
    """Test _compute_port_rates edge cases and rate calculations."""

    def test_reused_stats_carried_forward(self, mock_device_coordinator):
        """Carries forward previous sample when device is in reused_stats."""
        now = time.monotonic()
        mock_device_coordinator._prev_port_bytes = {
            "dev1": (now - 10, {1: {"tx_bytes": 1000, "rx_bytes": 2000}})
        }
        mock_device_coordinator._reused_stats = {"dev1"}
        mock_device_coordinator.data = {
            "stats": {
                "site1": {
                    "dev1": {"port_bytes": {1: {"tx_bytes": 1000, "rx_bytes": 2000}}}
                }
            }
        }
        result = mock_device_coordinator._compute_port_rates(
            mock_device_coordinator.data["stats"]
        )
        assert "dev1" in mock_device_coordinator._prev_port_bytes
        assert (
            mock_device_coordinator._prev_port_bytes["dev1"][1][1]["tx_bytes"] == 1000
        )
        # The reused record is passed through as it is.
        stats = mock_device_coordinator.data["stats"]["site1"]["dev1"]
        assert result["site1"]["dev1"] is stats

    def test_reused_stats_not_in_previous(self, mock_device_coordinator):
        """Do not error when device in reused_stats has no previous entry."""
        mock_device_coordinator._prev_port_bytes = {}
        mock_device_coordinator._reused_stats = {"dev1"}
        mock_device_coordinator.data = {
            "stats": {
                "site1": {
                    "dev1": {"port_bytes": {1: {"tx_bytes": 1000, "rx_bytes": 2000}}}
                }
            }
        }
        result = mock_device_coordinator._compute_port_rates(
            mock_device_coordinator.data["stats"]
        )
        assert "dev1" not in mock_device_coordinator._prev_port_bytes
        stats = mock_device_coordinator.data["stats"]["site1"]["dev1"]
        assert result["site1"]["dev1"] is stats

    @pytest.mark.parametrize("offset", [0.0, 10.0], ids=["zero", "negative"])
    def test_elapsed_zero_or_negative(self, mock_device_coordinator, offset: float):
        """Skip rate computation if elapsed <= 0."""
        now = time.monotonic()
        mock_device_coordinator._prev_port_bytes = {
            "dev1": (now + offset, {1: {"tx_bytes": 1000, "rx_bytes": 2000}})
        }
        mock_device_coordinator.data = {
            "stats": {
                "site1": {
                    "dev1": {"port_bytes": {1: {"tx_bytes": 2000, "rx_bytes": 3000}}}
                }
            }
        }
        with patch("time.monotonic", return_value=now):
            result = mock_device_coordinator._compute_port_rates(
                mock_device_coordinator.data["stats"]
            )
        stats = result["site1"]["dev1"]
        assert "port_rates" not in stats

    def test_negative_deltas_counter_reset(self, mock_device_coordinator):
        """Skip ports where delta is negative due to counter reset."""
        now = time.monotonic()
        mock_device_coordinator._prev_port_bytes = {
            "dev1": (now - 10, {1: {"tx_bytes": 5000, "rx_bytes": 5000}})
        }
        mock_device_coordinator.data = {
            "stats": {
                "site1": {
                    "dev1": {"port_bytes": {1: {"tx_bytes": 1000, "rx_bytes": 1000}}}
                }
            }
        }
        with patch("time.monotonic", return_value=now):
            result = mock_device_coordinator._compute_port_rates(
                mock_device_coordinator.data["stats"]
            )
        stats = result["site1"]["dev1"]
        assert "port_rates" not in stats

    def test_compute_port_rates_port_churn_new_port_skipped(
        self, mock_device_coordinator
    ):
        """Port churn: newly appearing port with no prior sample is skipped."""
        now = time.monotonic()
        mock_device_coordinator._prev_port_bytes = {
            "dev1": (now - 10, {1: {"tx_bytes": 1000, "rx_bytes": 2000}})
        }
        mock_device_coordinator.data = {
            "stats": {
                "site1": {
                    "dev1": {
                        "port_bytes": {
                            1: {"tx_bytes": 2000, "rx_bytes": 3000},
                            2: {"tx_bytes": 500, "rx_bytes": 500},
                        }
                    }
                }
            }
        }
        with patch("time.monotonic", return_value=now):
            result = mock_device_coordinator._compute_port_rates(
                mock_device_coordinator.data["stats"]
            )
        stats = result["site1"]["dev1"]
        assert "port_rates" in stats
        assert 1 in stats["port_rates"]
        assert 2 not in stats["port_rates"]
        # The rates go on a copy; the input maps and record are not written.
        assert result is not mock_device_coordinator.data["stats"]
        assert result["site1"] is not mock_device_coordinator.data["stats"]["site1"]
        assert (
            "port_rates" not in mock_device_coordinator.data["stats"]["site1"]["dev1"]
        )
