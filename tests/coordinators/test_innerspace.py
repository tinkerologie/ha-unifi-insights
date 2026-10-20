# Copyright 2026 UniFi Insights contributors
"""Tests for the InnerSpace coordinator."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import UpdateFailed
from unifi_official_api import (
    ApiKeyAuth,
    ConnectionType,
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiNotFoundError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from unifi_official_api.innerspace import (
    InnerSpaceAccessPoint,
    InnerSpaceFloorPlan,
    InnerSpaceInventoryDevice,
    InnerSpaceProject,
    InnerSpaceProjectIdentity,
    InnerSpaceSwitch,
    UniFiInnerSpaceClient,
)
from unifi_official_api.network.models import (
    SiteReportBucket,
)

from custom_components.unifi_insights.console_identity import async_get_device_entry
from custom_components.unifi_insights.const import (
    DOMAIN,
)
from custom_components.unifi_insights.coordinators import (
    UnifiInsightsInnerSpaceCoordinator,
)
from custom_components.unifi_insights.coordinators.config import (
    WINDOW_1D_MS,
    WINDOW_1H_MS,
    WINDOW_1M_MS,
    WINDOW_1W_MS,
    UnifiInsightsConfigCoordinator,
    aggregate_internet_activity_windows,
)
from custom_components.unifi_insights.coordinators.config_sections import (
    async_fetch_site_routes,
    async_fetch_site_wifi_and_links,
    async_update_site_internet_activity,
    enrich_wifi,
    map_legacy_site_names,
    resolve_report_site_name,
    wifi_qr_payload,
)
from custom_components.unifi_insights.coordinators.facade import (
    UnifiInsightsFacadeCoordinator,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


# --- InnerSpace coordinator --------------------------------------------------


class TestUnifiInsightsInnerSpaceCoordinator:
    """Tests for UnifiInsightsInnerSpaceCoordinator and facade integration."""

    @pytest.mark.asyncio
    async def test_update_and_snapshot_preservation_on_failure(
        self,
        hass: HomeAssistant,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        mock_innerspace_client: MagicMock,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test InnerSpace refresh normalizes data and preserves snapshot on failure."""
        mock_innerspace_client.get_project.return_value = InnerSpaceProject(
            project=InnerSpaceProjectIdentity(id="proj-1"),
        )
        mock_innerspace_client.list_floor_plans.return_value = [
            InnerSpaceFloorPlan(
                id="fp-1",
                name="First Floor",
                floor_number=1,
                site_id="default",
                ppm=20.0,
            )
        ]
        mock_innerspace_client.list_access_points.return_value = [
            InnerSpaceAccessPoint(
                id="ap-1",
                name="Office AP",
                model="U6-Pro",
                mac="AA:BB:CC:DD:EE:FF",
                floor_plan_id="fp-1",
                x=10.0,
                y=20.0,
                status="online",
            )
        ]
        mock_innerspace_client.list_switches.return_value = [
            InnerSpaceSwitch(
                id="sw-1",
                name="Office SW",
                model="USW-24",
                floor_plan_id="fp-1",
                x=5.0,
                y=15.0,
                status="online",
            )
        ]
        mock_innerspace_client.list_inventory.return_value = [
            InnerSpaceInventoryDevice(
                id="inv-1",
                name="Spare AP",
                model="U6-LR",
                mac="AA:BB:CC:99:99:99",
            )
        ]

        coord = UnifiInsightsInnerSpaceCoordinator(
            hass=hass,
            network_client=mock_network_client,
            protect_client=mock_protect_client,
            innerspace_client=mock_innerspace_client,
            entry=mock_config_entry,
        )

        data = await coord._async_update_data()
        assert data["project"]["id"] == "proj-1"
        assert "fp-1" in data["floor_plans"]
        assert "ap-1" in data["access_points"]
        assert "sw-1" in data["switches"]
        assert "inv-1" in data["inventory"]
        assert coord.available is True

        # Transient failure raises UpdateFailed while keeping self.data
        mock_innerspace_client.get_project.side_effect = UniFiConnectionError("Down")
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()
        assert coord.available is False
        assert "ap-1" in coord.data["access_points"]

        # Auth error raises ConfigEntryAuthFailed
        mock_innerspace_client.get_project.side_effect = UniFiAuthenticationError(
            "Expired", status_code=401
        )
        with pytest.raises(ConfigEntryAuthFailed):
            await coord._async_update_data()

        # Timeout, response, and unexpected errors raise UpdateFailed
        for exc in (
            UniFiTimeoutError("Timeout"),
            UniFiResponseError("Bad gateway", status_code=502),
            RuntimeError("Unexpected"),
        ):
            mock_innerspace_client.get_project.side_effect = exc
            with pytest.raises(UpdateFailed):
                await coord._async_update_data()

    @pytest.mark.asyncio
    async def test_stale_floor_plan_device_removed_current_kept(
        self,
        hass: HomeAssistant,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        mock_innerspace_client: MagicMock,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """A floor plan that disappears loses its device; present ones keep it."""
        mock_config_entry.add_to_hass(hass)
        registry = dr.async_get(hass)
        for plan_id in ("fp-1", "fp-2"):
            registry.async_get_or_create(
                config_entry_id=mock_config_entry.entry_id,
                identifiers={(DOMAIN, f"innerspace_floor_plan_{plan_id}")},
            )

        def plans(*ids: str) -> list[InnerSpaceFloorPlan]:
            return [
                InnerSpaceFloorPlan(
                    id=plan_id,
                    name=plan_id,
                    image_url=f"/v1/assets/{plan_id}/plan.png",
                )
                for plan_id in ids
            ]

        mock_innerspace_client.get_project.return_value = InnerSpaceProject(
            project=InnerSpaceProjectIdentity(id="proj-1"),
        )
        coord = UnifiInsightsInnerSpaceCoordinator(
            hass=hass,
            network_client=mock_network_client,
            protect_client=mock_protect_client,
            innerspace_client=mock_innerspace_client,
            entry=mock_config_entry,
        )

        def device(plan_id: str) -> object:
            return async_get_device_entry(
                registry,
                (DOMAIN, f"innerspace_floor_plan_{plan_id}"),
                mock_config_entry.entry_id,
            )

        mock_innerspace_client.list_floor_plans.return_value = plans("fp-1", "fp-2")
        await coord._async_update_data()
        assert device("fp-1") is not None
        assert device("fp-2") is not None

        mock_innerspace_client.list_floor_plans.return_value = plans("fp-1")
        await coord._async_update_data()
        assert device("fp-1") is not None
        assert device("fp-2") is None

        # A plan that vanishes without ever having had a device is a no-op.
        registry.async_clear_config_entry(mock_config_entry.entry_id)
        mock_innerspace_client.list_floor_plans.return_value = []
        await coord._async_update_data()
        assert device("fp-1") is None

    @pytest.mark.asyncio
    async def test_failed_refresh_keeps_floor_plan_devices(
        self,
        hass: HomeAssistant,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        mock_innerspace_client: MagicMock,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """A failed poll keeps the last snapshot and must not prune devices."""
        mock_config_entry.add_to_hass(hass)
        registry = dr.async_get(hass)
        registry.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "innerspace_floor_plan_fp-1")},
        )
        mock_innerspace_client.get_project.return_value = InnerSpaceProject(
            project=InnerSpaceProjectIdentity(id="proj-1"),
        )
        mock_innerspace_client.list_floor_plans.return_value = [
            InnerSpaceFloorPlan(
                id="fp-1", name="fp-1", image_url="/v1/assets/fp-1/plan.png"
            )
        ]
        coord = UnifiInsightsInnerSpaceCoordinator(
            hass=hass,
            network_client=mock_network_client,
            protect_client=mock_protect_client,
            innerspace_client=mock_innerspace_client,
            entry=mock_config_entry,
        )
        await coord._async_update_data()

        mock_innerspace_client.get_project.side_effect = UniFiConnectionError("Down")
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()
        assert (
            async_get_device_entry(
                registry,
                (DOMAIN, "innerspace_floor_plan_fp-1"),
                mock_config_entry.entry_id,
            )
            is not None
        )

    @pytest.mark.asyncio
    async def test_refresh_retains_warning_on_200_html_and_preserves_snapshot(
        self,
        hass: HomeAssistant,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        mock_config_entry: MockConfigEntry,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """InnerSpace refresh logs WARNING on 200 HTML and keeps snapshot."""
        real_client = UniFiInnerSpaceClient(
            auth=ApiKeyAuth(api_key="test-key"),
            base_url="https://192.168.1.1",
            connection_type=ConnectionType.LOCAL,
        )
        real_client.get_project = AsyncMock(
            return_value=InnerSpaceProject(
                project=InnerSpaceProjectIdentity(id="proj-1"),
            )
        )
        real_client.list_floor_plans = AsyncMock(return_value=[])
        real_client.list_access_points = AsyncMock(
            return_value=[
                InnerSpaceAccessPoint(
                    id="ap-1",
                    name="Office AP",
                    model="U6-Pro",
                    mac="AA:BB:CC:DD:EE:FF",
                    floor_plan_id="fp-1",
                    x=10.0,
                    y=20.0,
                    status="online",
                )
            ]
        )
        real_client.list_switches = AsyncMock(return_value=[])
        real_client.list_inventory = AsyncMock(return_value=[])

        coord = UnifiInsightsInnerSpaceCoordinator(
            hass=hass,
            network_client=mock_network_client,
            protect_client=mock_protect_client,
            innerspace_client=real_client,
            entry=mock_config_entry,
        )
        await coord._async_update_data()
        real_client.get_project.assert_awaited_once_with()
        assert "ap-1" in coord.data["access_points"]

        # Restore real get_project method and simulate 200 HTML response on refresh
        real_client.get_project = UniFiInnerSpaceClient.get_project.__get__(
            real_client, UniFiInnerSpaceClient
        )
        html_response = MagicMock()
        html_response.status = 200
        html_response.text = AsyncMock(
            return_value="<!doctype html><html><body>UniFi OS</body></html>"
        )
        html_response.headers = {}
        html_response.method = "GET"
        html_response.history = ()
        html_response.url = MagicMock()
        html_response.url.path = "/proxy/innerspace/integration/v1/project"
        html_response.json = AsyncMock(
            side_effect=aiohttp.ContentTypeError(MagicMock(), MagicMock())
        )
        request_ctx = MagicMock()
        request_ctx.__aenter__ = AsyncMock(return_value=html_response)
        request_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=request_ctx)
        real_client._ensure_session = AsyncMock(return_value=mock_session)
        real_client._throttle = AsyncMock()

        with caplog.at_level(logging.WARNING), pytest.raises(UpdateFailed):
            await coord._async_update_data()

        assert any(
            "Response is not JSON for GET /proxy/innerspace/integration/v1/project"
            in r.getMessage()
            for r in caplog.records
        )
        assert coord.available is False
        assert "ap-1" in coord.data["access_points"]

    @pytest.mark.asyncio
    async def test_config_coordinator_internet_activity_windows_and_resilience(
        self,
        hass: HomeAssistant,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test rolling window aggregation, hourly reuse, and transient recovery."""
        now_ms = 1_800_000_000_000
        windows = aggregate_internet_activity_windows(
            five_minute_buckets=[
                {
                    "time": now_ms - WINDOW_1H_MS - 1,
                    "wan-rx_bytes": 9999,
                    "wan-tx_bytes": 9999,
                },
                {
                    "time": now_ms - WINDOW_1H_MS,
                    "wan-rx_bytes": 1000,
                    "wan-tx_bytes": 500,
                    "wan2-rx_bytes": 250,
                    "wan2-tx_bytes": 100,
                },
            ],
            hourly_buckets=[
                {
                    "time": now_ms - WINDOW_1W_MS - 1,
                    "wan-rx_bytes": 8888,
                    "wan-tx_bytes": 8888,
                },
                {
                    "time": now_ms - WINDOW_1D_MS - 3_600_000,
                    "wan-rx_bytes": 2000,
                    "wan-tx_bytes": 800,
                },
                {
                    "time": now_ms - WINDOW_1D_MS,
                    "wan-rx_bytes": 3000,
                    "wan-tx_bytes": 1200,
                },
            ],
            daily_buckets=[
                {
                    "time": now_ms - WINDOW_1M_MS,
                    "wan-rx_bytes": 10000,
                    "wan-tx_bytes": 4000,
                }
            ],
            now_ms=now_ms,
        )
        assert windows == {
            "1h": {"rx_bytes": 1250, "tx_bytes": 600},
            "1d": {"rx_bytes": 3000, "tx_bytes": 1200},
            "1w": {"rx_bytes": 5000, "tx_bytes": 2000},
            "1m": {"rx_bytes": 10000, "tx_bytes": 4000},
        }
        assert aggregate_internet_activity_windows([], [], [], now_ms=now_ms) == {}

        mock_network_client.sites.get_all = AsyncMock(
            return_value=[
                {
                    "id": "site1",
                    "name": "Default",
                    "internalReference": "default",
                }
            ]
        )
        coord = UnifiInsightsConfigCoordinator(
            hass=hass,
            network_client=mock_network_client,
            protect_client=mock_protect_client,
            entry=mock_config_entry,
        )

        # 1. Unsupported response stores no site data and keeps WiFi/firewall intact
        mock_network_client.reports.get_site_report = AsyncMock(
            side_effect=UniFiNotFoundError("Not found", status_code=404)
        )
        await coord._async_update_data()
        assert coord.data["internet_activity"] == {}
        assert coord.internet_activity_available("site1") is True
        assert coord.wifi_available("site1") is True
        assert coord.firewall_available("site1") is True

        # 2. Successful fetch populates windows
        async def _report_ok(
            site_name: str, interval: str, *, start_ms: int, end_ms: int
        ) -> list[dict[str, int]]:
            return [{"time": end_ms, "wan-rx_bytes": 4096, "wan-tx_bytes": 2048}]

        mock_network_client.reports.get_site_report = AsyncMock(side_effect=_report_ok)
        await coord._async_update_data()
        assert coord.data["internet_activity"]["site1"]["1h"] == {
            "rx_bytes": 4096,
            "tx_bytes": 2048,
        }
        assert coord.internet_activity_available("site1") is True

        # 3. Transient failure retains prior data and marks section unavailable
        mock_network_client.reports.get_site_report = AsyncMock(
            side_effect=UniFiTimeoutError("Timeout")
        )
        await coord._async_update_data()
        coord.last_update_success = True
        assert coord.internet_activity_available("site1") is False
        assert "site1" in coord.data["internet_activity_unavailable"]
        assert coord.data["internet_activity"]["site1"]["1h"] == {
            "rx_bytes": 4096,
            "tx_bytes": 2048,
        }
        assert coord.wifi_available("site1") is True
        assert coord.firewall_available("site1") is True

        # 4. Later success replaces data and clears the unavailable marker
        async def _report_recovered(
            site_name: str, interval: str, *, start_ms: int, end_ms: int
        ) -> list[dict[str, int]]:
            return [{"time": end_ms, "wan-rx_bytes": 8192, "wan-tx_bytes": 4096}]

        mock_network_client.reports.get_site_report = AsyncMock(
            side_effect=_report_recovered
        )
        await coord._async_update_data()
        assert coord.internet_activity_available("site1") is True
        assert "site1" not in coord.data["internet_activity_unavailable"]
        assert coord.data["internet_activity"]["site1"]["1h"] == {
            "rx_bytes": 8192,
            "tx_bytes": 4096,
        }

        # 5. Cover bucket variations (SiteReportBucket instance, invalid dict,
        # non-dict object, rx-only, tx-only), client fallback, hourly/daily
        # partial failure, HTTP 200 HTML response, empty windows, and facade
        mixed_buckets: list[Any] = [
            SiteReportBucket.model_validate({"time": now_ms, "wan-rx_bytes": 100}),
            {"time": now_ms, "wan-tx_bytes": 50},
            {"invalid": "no_time"},
            "not_a_dict",
        ]
        rx_only = aggregate_internet_activity_windows(
            [SiteReportBucket.model_validate({"time": now_ms, "wan-rx_bytes": 100})],
            [SiteReportBucket.model_validate({"time": now_ms, "wan-tx_bytes": 50})],
            mixed_buckets,
            now_ms=now_ms,
        )
        assert rx_only["1h"] == {"rx_bytes": 100}
        assert rx_only["1d"] == {"tx_bytes": 50}
        assert rx_only["1m"] == {"rx_bytes": 100, "tx_bytes": 50}

        # Fallback to network_client.get_site_report and empty fallback
        mock_network_client.reports = None
        mock_network_client.get_site_report = AsyncMock(return_value=[])
        assert (
            await coord._call_site_report("default", "hourly", start_ms=1, end_ms=2)
            == []
        )
        mock_network_client.get_site_report = None
        assert (
            await coord._call_site_report("default", "hourly", start_ms=1, end_ms=2)
            == []
        )

        # Hourly / daily transient failures and HTTP 200 HTML unsupported
        mock_network_client.reports = MagicMock()

        async def _fail_hourly(
            site_name: str, interval: str, *, start_ms: int, end_ms: int
        ) -> list[dict[str, int]]:
            if interval == "hourly":
                msg = "Hourly timeout"
                raise UniFiTimeoutError(msg)
            return []

        mock_network_client.reports.get_site_report = AsyncMock(
            side_effect=_fail_hourly
        )
        assert (
            await coord._fetch_site_internet_activity(
                site_id="site1", site_name="default", now_ms=now_ms
            )
            is None
        )

        async def _fail_daily(
            site_name: str, interval: str, *, start_ms: int, end_ms: int
        ) -> list[dict[str, int]]:
            if interval == "daily":
                msg = "Daily timeout"
                raise UniFiTimeoutError(msg)
            return []

        mock_network_client.reports.get_site_report = AsyncMock(side_effect=_fail_daily)
        assert (
            await coord._fetch_site_internet_activity(
                site_id="site1", site_name="default", now_ms=now_ms
            )
            is None
        )

        mock_network_client.reports.get_site_report = AsyncMock(
            side_effect=UniFiResponseError("HTML", status_code=200)
        )
        await coord._async_update_data()
        assert "site1" not in coord.data["internet_activity"]

        facade_with_cfg = MagicMock()
        facade_with_cfg.config_available = True
        facade_with_cfg._config_coordinator = coord
        assert (
            UnifiInsightsFacadeCoordinator.internet_activity_available(
                facade_with_cfg, "site1"
            )
            is True
        )
        facade_with_cfg._config_coordinator = None
        assert (
            UnifiInsightsFacadeCoordinator.internet_activity_available(
                facade_with_cfg, "site1"
            )
            is True
        )

    @pytest.mark.asyncio
    async def test_config_coordinator_innerspace_only_suppresses_network_404(
        self,
        hass: HomeAssistant,
        mock_network_client: MagicMock,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """The config coordinator suppresses a Network 404 when InnerSpace is used."""
        mock_network_client.sites.get_all.side_effect = UniFiNotFoundError(
            "Not found", status_code=404
        )
        config_coord = UnifiInsightsConfigCoordinator(
            hass=hass,
            network_client=mock_network_client,
            protect_client=None,
            entry=mock_config_entry,
            network_available=True,
            innerspace_available=True,
        )
        data = await config_coord._async_update_data()
        assert data["sites"] == {}
        assert config_coord.available is True

    @pytest.mark.asyncio
    async def test_config_sections_helper_branches(
        self,
        mock_network_client: MagicMock,
    ) -> None:
        """Test edge branches in config_sections helper functions."""
        # map_legacy_site_names: skip empty name and fall back to single legacy site
        mappings = map_legacy_site_names(
            {"unmatched_site": {"name": "Office"}},
            [{"name": ""}, {"name": "default", "desc": "Main"}],
        )
        assert mappings == {"unmatched_site": "default"}

        # wifi_qr_payload: WEP and hidden SSID
        qr = wifi_qr_payload("My;Net", "secret", "wep", hidden=True)
        assert "T:WEP" in qr
        assert "H:true" in qr

        # enrich_wifi: client without essid, wifi without ssid/matching config
        wifi_dict: dict[str, dict[str, Any]] = {
            "w1": {"id": "w1"},
            "w2": {"id": "w2", "name": "UnmatchedSSID"},
        }
        enrich_wifi(
            wifi_dict,
            [{"name": "OtherSSID", "x_passphrase": "pw", "security": "wpapsk"}],
            [{"is_wired": False}],
        )
        assert wifi_dict["w2"]["num_connected_clients"] == 0

        # resolve_report_site_name: non-dict site_data and internalReference fallback
        assert resolve_report_site_name("site1", "not_a_dict", None) == "site1"
        assert (
            resolve_report_site_name("site1", {"internalReference": "custom_ref"}, None)
            == "custom_ref"
        )

        # async_fetch_site_routes: route without id is skipped
        mock_network_client.routes.list_routes = AsyncMock(
            return_value=[{"name": "no-id"}, {"id": "r1", "name": "with-id"}]
        )
        routes = await async_fetch_site_routes(
            mock_network_client, dict, "site1", "default"
        )
        assert list(routes.keys()) == ["r1"]

        # async_update_site_internet_activity: failed fetch with no prior_activity
        failed_set: set[tuple[str, str]] = set()
        activity_map: dict[str, dict[str, dict[str, int]]] = {}
        await async_update_site_internet_activity(
            AsyncMock(return_value=None),
            site_id="site1",
            site_data={},
            legacy_name="default",
            now_ms=1000,
            prior_activity=None,
            internet_activity_by_site=activity_map,
            failed_sections=failed_set,
        )
        assert ("internet_activity", "site1") in failed_set
        assert "site1" not in activity_map

        # async_fetch_site_wifi_and_links: get_legacy_configs raising logs debug
        dummy_coord = MagicMock()
        dummy_coord._fetch_optional_section = AsyncMock(
            return_value=[{"id": "w1", "name": "Net"}]
        )
        dummy_coord._model_to_dict = dict
        dummy_coord._client_links = lambda _: {}
        dummy_coord.data = {"client_links": {}, "wifi": {}}
        dummy_coord.network_client.clients.get_active_legacy = AsyncMock(
            return_value=[]
        )
        dummy_coord.network_client.wifi.get_legacy_configs = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        wifi_res, _ = await async_fetch_site_wifi_and_links(
            dummy_coord, "site1", "default", set()
        )
        assert "w1" in wifi_res
