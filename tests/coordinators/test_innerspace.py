# Copyright 2026 UniFi Insights contributors
"""Tests for the InnerSpace coordinator."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
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

from custom_components.unifi_insights.console_identity import async_get_device_entry
from custom_components.unifi_insights.const import (
    DOMAIN,
)
from custom_components.unifi_insights.coordinators import (
    UnifiInsightsInnerSpaceCoordinator,
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
            InnerSpaceFloorPlan.model_validate(
                {
                    "id": "fp-1",
                    "name": "First Floor",
                    "floor_number": 1,
                    "site_id": "default",
                    "ppm": 20.0,
                }
            )
        ]
        mock_innerspace_client.list_access_points.return_value = [
            InnerSpaceAccessPoint.model_validate(
                {
                    "id": "ap-1",
                    "name": "Office AP",
                    "model": "U6-Pro",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "floor_plan_id": "fp-1",
                    "x": 10.0,
                    "y": 20.0,
                    "status": "online",
                }
            )
        ]
        mock_innerspace_client.list_switches.return_value = [
            InnerSpaceSwitch.model_validate(
                {
                    "id": "sw-1",
                    "name": "Office SW",
                    "model": "USW-24",
                    "floor_plan_id": "fp-1",
                    "x": 5.0,
                    "y": 15.0,
                    "status": "online",
                }
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
                InnerSpaceFloorPlan.model_validate(
                    {
                        "id": plan_id,
                        "name": plan_id,
                        "image_url": f"/v1/assets/{plan_id}/plan.png",
                    }
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
            InnerSpaceFloorPlan.model_validate(
                {"id": "fp-1", "name": "fp-1", "image_url": "/v1/assets/fp-1/plan.png"}
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
                InnerSpaceAccessPoint.model_validate(
                    {
                        "id": "ap-1",
                        "name": "Office AP",
                        "model": "U6-Pro",
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "floor_plan_id": "fp-1",
                        "x": 10.0,
                        "y": 20.0,
                        "status": "online",
                    }
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
