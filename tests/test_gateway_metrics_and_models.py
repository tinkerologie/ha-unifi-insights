# Copyright 2026 UniFi Insights contributors
"""Tests for Issue #151 (legacy device metrics) and gateway model prefixes."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from unifi_official_api.network.endpoints.devices import (
    DevicesEndpoint,
)
from unifi_official_api.network.models.device import (
    LegacyPortMetrics,
)

from custom_components.unifi_insights.const import (
    GATEWAY_MODEL_PREFIXES,
)
from custom_components.unifi_insights.coordinators.device import (
    UnifiInsightsDeviceCoordinator,
    _legacy_system_stats,
    _merge_legacy_system_stats,
)
from custom_components.unifi_insights.data_transforms import get_site_gateway_id


class TestLegacyDeviceStatsExtraction:
    """Test CPU, memory, and uptime metrics extraction from legacy controller data."""

    @pytest.mark.asyncio
    async def test_get_port_metrics_sys_stats(self):
        """Test extraction of cpu, mem, uptime from sys_stats."""
        client = MagicMock()
        client._get = AsyncMock(
            return_value={
                "data": [
                    {
                        "mac": "aa:bb:cc:dd:ee:ff",
                        "sys_stats": {"cpu": "12.5", "mem": "78.2", "uptime": 123456},
                    }
                ]
            }
        )
        endpoint = DevicesEndpoint(client)
        metrics = await endpoint.get_port_metrics("default", "aa:bb:cc:dd:ee:ff")
        assert isinstance(metrics, LegacyPortMetrics)
        assert metrics.cpu_utilization_pct == 12.5
        assert metrics.memory_utilization_pct == 78.2
        assert metrics.uptime_sec == 123456

    @pytest.mark.asyncio
    async def test_get_port_metrics_prefers_system_stats_over_sys_stats(self):
        """Both stat objects present: percentages come from ``system-stats``.

        Real gateway consoles (UDM/UCG) report ``sys_stats`` and
        ``system-stats`` side by side. Only ``system-stats`` carries the
        cpu/mem percentages; ``sys_stats`` holds raw counters and no
        cpu/mem keys at all, so selecting a single object by truthiness
        picks the one that cannot answer.
        """
        client = MagicMock()
        client._get = AsyncMock(
            return_value={
                "data": [
                    {
                        "mac": "aa:bb:cc:dd:ee:ff",
                        "sys_stats": {
                            "loadavg_1": "1.58",
                            "loadavg_5": "1.32",
                            "loadavg_15": "1.19",
                            "mem_buffer": 0,
                            "mem_total": 4045352960,
                            "mem_used": 3119644672,
                        },
                        "system-stats": {
                            "cpu": "9.3",
                            "mem": "77.1",
                            "uptime": "1022432",
                        },
                        "uptime": 1022431,
                    }
                ]
            }
        )
        endpoint = DevicesEndpoint(client)
        metrics = await endpoint.get_port_metrics("default", "aa:bb:cc:dd:ee:ff")
        assert metrics.cpu_utilization_pct == 9.3
        assert metrics.memory_utilization_pct == 77.1
        assert metrics.uptime_sec == 1022432

    @pytest.mark.asyncio
    async def test_get_port_metrics_system_stats_fallback(self):
        """Test extraction from system-stats and top-level fields."""
        client = MagicMock()
        client._get = AsyncMock(
            return_value={
                "data": [
                    {
                        "mac": "aa:bb:cc:dd:ee:ff",
                        "system-stats": {"cpu": "25.0", "mem": "50.0"},
                        "uptime": 654321,
                    }
                ]
            }
        )
        endpoint = DevicesEndpoint(client)
        metrics = await endpoint.get_port_metrics("default", "aa:bb:cc:dd:ee:ff")
        assert metrics.cpu_utilization_pct == 25.0
        assert metrics.memory_utilization_pct == 50.0
        assert metrics.uptime_sec == 654321

    @pytest.mark.asyncio
    async def test_process_device_merges_legacy_stats_when_v1_stats_empty(self):
        """Test _process_device populates stats for MAC-keyed devices."""
        hass = MagicMock()
        network_client = MagicMock()
        protect_client = MagicMock()
        entry = MagicMock()
        entry.entry_id = "test_entry"
        config_coordinator = MagicMock()

        coordinator = UnifiInsightsDeviceCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=entry,
            config_coordinator=config_coordinator,
        )
        device_dict = {
            "id": "aa:bb:cc:dd:ee:ff",
            "name": "UCG-Max Gateway",
            "model": "UCG-Max",
            "mac": "aa:bb:cc:dd:ee:ff",
        }
        legacy_device = {
            "mac": "aa:bb:cc:dd:ee:ff",
            "sys_stats": {"cpu": "15.4", "mem": "82.1"},
            "uptime": 99999,
        }

        coordinator.network_client.devices.get_port_metrics = AsyncMock(
            return_value=LegacyPortMetrics(
                cpu_utilization_pct=15.4,
                memory_utilization_pct=82.1,
                uptime_sec=99999,
            )
        )

        dev_id, _dev_data, stats = await coordinator._process_device(
            site_id="default",
            device_dict=device_dict,
            clients=[],
            legacy_site_name="default",
            legacy_device=legacy_device,
        )

        assert dev_id == "aa:bb:cc:dd:ee:ff"
        assert stats["cpu_utilization_pct"] == 15.4
        assert stats["memory_utilization_pct"] == 82.1
        assert stats["uptime_sec"] == 99999


class TestGatewayModelPrefixes:
    """Test gateway recognition with GATEWAY_MODEL_PREFIXES."""

    def test_gateway_model_prefixes_content(self):
        """Ensure expected models are present in GATEWAY_MODEL_PREFIXES."""
        for prefix in ("UDM", "USG", "UXG", "UCG", "UDR", "UDW", "GATEWAY"):
            assert prefix in GATEWAY_MODEL_PREFIXES

    def test_switch_find_gateway_device_id_ucg(self):
        """Test get_site_gateway_id recognizes UCG."""
        coordinator = MagicMock()
        coordinator.data = {
            "devices": {
                "site1": {
                    "sw1": {"model": "USW-24-PoE"},
                    "gw1": {"model": "UCG-Max"},
                }
            }
        }
        assert get_site_gateway_id(coordinator.data, "site1") == "gw1"

    def test_switch_find_gateway_device_id_uxg(self):
        """Test get_site_gateway_id recognizes UXG."""
        coordinator = MagicMock()
        coordinator.data = {
            "devices": {
                "site1": {
                    "sw1": {"model": "USW-Pro-48"},
                    "gw1": {"model": "UXG-Lite"},
                }
            }
        }
        assert get_site_gateway_id(coordinator.data, "site1") == "gw1"

    def test_switch_find_gateway_device_id_udr(self):
        """Test get_site_gateway_id recognizes UDR."""
        coordinator = MagicMock()
        coordinator.data = {
            "devices": {
                "site1": {
                    "gw1": {"model": "UDR"},
                }
            }
        }
        assert get_site_gateway_id(coordinator.data, "site1") == "gw1"

    def test_switch_find_gateway_device_id_udw(self):
        """Test get_site_gateway_id recognizes UDW."""
        coordinator = MagicMock()
        coordinator.data = {
            "devices": {
                "site1": {
                    "gw1": {"model": "UDW-Pro"},
                }
            }
        }
        assert get_site_gateway_id(coordinator.data, "site1") == "gw1"

    def test_legacy_system_stats_fills_empty_stats(self):
        """Legacy sys_stats populate CPU, memory and uptime when v1 gave none."""
        stats: dict = {}
        legacy_device = {
            "sys_stats": {"cpu": "45.2", "mem": "60.1"},
            "uptime": 12345,
        }

        _merge_legacy_system_stats(stats, legacy_device)

        assert stats["cpuUtilizationPct"] == 45.2
        assert stats["memoryUtilizationPct"] == 60.1
        assert stats["uptimeSec"] == 12345

    def test_legacy_does_not_overwrite_v1_stats(self):
        """A v1 reading always wins over the legacy fallback."""
        stats = {
            "cpuUtilizationPct": 10.0,
            "memoryUtilizationPct": 20.0,
            "uptimeSec": 3000,
        }
        legacy_device = {
            "sys_stats": {"cpu": "99.0", "mem": "99.0"},
            "uptime": 99999,
        }

        _merge_legacy_system_stats(stats, legacy_device)

        assert stats["cpuUtilizationPct"] == 10.0
        assert stats["memoryUtilizationPct"] == 20.0
        assert stats["uptimeSec"] == 3000

    def test_legacy_system_stats_reads_system_stats_alias(self):
        """The ``system-stats`` spelling is accepted, falling back to the root."""
        cpu, mem, uptime = _legacy_system_stats(
            {"system-stats": {"cpu": "12.5"}, "mem": "33.0", "uptime": 60}
        )

        assert (cpu, mem, uptime) == ("12.5", "33.0", 60)

    def test_legacy_system_stats_prefers_system_stats_over_sys_stats(self):
        """Both objects present: cpu/mem come from ``system-stats``.

        ``sys_stats`` is truthy on real gateways but exposes only raw
        counters, so it must not shadow the ``system-stats`` percentages.
        """
        cpu, mem, uptime = _legacy_system_stats(
            {
                "sys_stats": {
                    "loadavg_1": "1.58",
                    "mem_total": 4045352960,
                    "mem_used": 3119644672,
                },
                "system-stats": {"cpu": "9.3", "mem": "77.1", "uptime": "1022432"},
                "uptime": 1022431,
            }
        )

        assert (cpu, mem, uptime) == ("9.3", "77.1", "1022432")

    def test_legacy_system_stats_falls_back_to_sys_stats_keys(self):
        """``sys_stats`` still answers when ``system-stats`` lacks the key."""
        cpu, mem, uptime = _legacy_system_stats(
            {
                "system-stats": {"cpu": "5.0"},
                "sys_stats": {"mem": "42.0"},
                "uptime": 1234,
            }
        )

        assert (cpu, mem, uptime) == ("5.0", "42.0", 1234)

    def test_legacy_system_stats_tolerates_non_dict_sys_stats(self):
        """A malformed sys_stats value falls back to the device root."""
        cpu, mem, uptime = _legacy_system_stats(
            {"sys_stats": ["unexpected"], "cpu": "7.5", "mem": "8.5", "uptime": 99}
        )

        assert (cpu, mem, uptime) == ("7.5", "8.5", 99)

    def test_legacy_system_stats_ignores_unparsable_values(self):
        """A non-numeric legacy reading is skipped rather than raising."""
        stats: dict = {}

        _merge_legacy_system_stats(stats, {"sys_stats": {"cpu": "n/a"}})

        assert "cpuUtilizationPct" not in stats
