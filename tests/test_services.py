# Copyright 2026 UniFi Insights contributors
"""Tests for UniFi Insights services."""

import ast
import inspect
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, cast
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest
import voluptuous as vol
import yaml
from homeassistant.core import Context
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api import ApiKeyAuth, ConnectionType
from unifi_official_api.protect import UniFiProtectClient
from yarl import URL

from custom_components.unifi_insights import services
from custom_components.unifi_insights.services import (
    SERVICE_AUTHORIZE_GUEST,
    SERVICE_PLAY_CHIME_RINGTONE,
    SERVICE_PTZ_MOVE,
    SERVICE_PTZ_PATROL,
    SERVICE_RECONNECT_CLIENT,
    SERVICE_REMOVE_CLIENTS,
    SERVICE_RESTART_DEVICE,
    SERVICE_SET_CHIME_REPEAT_TIMES,
    SERVICE_SET_CHIME_RINGTONE,
    SERVICE_SET_CHIME_VOLUME,
    SERVICE_SET_HDR_MODE,
    SERVICE_SET_LIGHT_LEVEL,
    SERVICE_SET_LIGHT_MODE,
    SERVICE_SET_LIVEVIEW,
    SERVICE_SET_MIC_VOLUME,
    SERVICE_SET_VIDEO_MODE,
    _extract_target_id,
    _filter_clients_for_removal,
    _get_coordinator_for_network_resource,
    _get_coordinator_for_protect_resource,
    _is_wireless_client,
    _resolve_network_client_id,
    _resolve_network_device_id,
    _resolve_protect_resource_id,
)
from tests.fixtures.command_owners import autospec_command_owners, protect_owner

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from unifi_official_api.network.client import UniFiNetworkClient
from unifi_official_api.network.models.client import ClientType

from custom_components.unifi_insights.const import DOMAIN, SERVICE_POWER_CYCLE_PORT
from custom_components.unifi_insights.coordinators.access import (
    UnifiInsightsAccessCoordinator,
)
from custom_components.unifi_insights.coordinators.carrier_fabric import (
    UnifiCarrierFabricCoordinator,
)
from custom_components.unifi_insights.coordinators.config import (
    UnifiConfigCoordinator,
)
from custom_components.unifi_insights.coordinators.device import (
    UnifiDeviceCoordinator,
)
from custom_components.unifi_insights.coordinators.facade import (
    UnifiFacadeCoordinator,
)
from custom_components.unifi_insights.coordinators.protect import (
    UnifiProtectCoordinator,
)
from custom_components.unifi_insights.services import (
    SERVICE_REFRESH_DATA,
    _client_records_match,
    _coord_data,
    _coord_section,
    _entry_has_client,
    _entry_has_device,
    _entry_has_site,
    _entry_owns_client,
    _entry_owns_device,
    _get_coordinators,
    _mac_key,
    _protect_entry_has_resource,
    _protect_owns_resource,
    _resolve_carrier_subscriber_target,
    _select_console,
    async_setup_services,
    async_unload_services,
)


def _protect(facade: Any) -> Any:
    """
    Return a mock facade's autospecced Protect owner, creating it once.

    Protect service actions call ``require_protect_coordinator()`` and then
    the owner's typed command (ADR-010), so tests assert on the owner.
    """
    owner = facade.require_protect_coordinator.return_value
    if getattr(owner, "_spec_class", None) is not UnifiProtectCoordinator:
        facade.require_protect_coordinator = MagicMock(
            return_value=create_autospec(UnifiProtectCoordinator, instance=True)
        )
    return protect_owner(facade)


def _real_protect_owner(
    hass: HomeAssistant, client: UniFiProtectClient
) -> UnifiProtectCoordinator:
    """Return a real Protect owner over ``client`` for end-to-end service tests."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    return UnifiProtectCoordinator(hass, MagicMock(), client, entry)


def _network_client() -> UniFiNetworkClient:
    """Build a local Network client for tests."""
    return UniFiNetworkClient(
        auth=ApiKeyAuth(api_key="test-key"),
        base_url="https://192.168.1.1",
        connection_type=ConnectionType.LOCAL,
    )


class TestGetCoordinators:
    """Tests for _get_coordinators helper."""

    def test_get_coordinators_with_entries(self, hass: HomeAssistant):
        """Test getting coordinators with valid entries."""
        mock_coordinator = MagicMock()
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            coordinators = _get_coordinators(hass)
            assert len(coordinators) == 1
            assert coordinators[0] == mock_coordinator

    def test_get_coordinators_no_entries(self, hass: HomeAssistant):
        """Test getting coordinators with no entries."""
        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[],
        ):
            coordinators = _get_coordinators(hass)
            assert len(coordinators) == 0

    def test_get_coordinators_entry_without_runtime_data(self, hass: HomeAssistant):
        """Test getting coordinators with entry missing runtime_data."""
        mock_entry = MagicMock()
        mock_entry.runtime_data = None

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            coordinators = _get_coordinators(hass)
            assert len(coordinators) == 0


class TestAsyncSetupServices:
    """Tests for async_setup_services."""

    async def test_setup_services_registers_services(self, hass: HomeAssistant):
        """Test that setup registers all services."""
        await async_setup_services(hass)

        # Check core services are registered
        assert hass.services.has_service(DOMAIN, SERVICE_REFRESH_DATA)
        assert hass.services.has_service(DOMAIN, SERVICE_RESTART_DEVICE)
        assert hass.services.has_service(DOMAIN, SERVICE_POWER_CYCLE_PORT)
        assert hass.services.has_service(DOMAIN, "set_hdr_mode")
        assert hass.services.has_service(DOMAIN, "set_video_mode")
        assert hass.services.has_service(DOMAIN, "set_mic_volume")
        assert hass.services.has_service(DOMAIN, "set_light_mode")
        assert hass.services.has_service(DOMAIN, "set_light_level")
        assert hass.services.has_service(DOMAIN, "ptz_move")
        assert hass.services.has_service(DOMAIN, "ptz_patrol")
        assert not hass.services.has_service(DOMAIN, "set_recording_mode")
        for action in (
            "set_chime_volume",
            "play_chime_ringtone",
            "set_chime_ringtone",
            "set_chime_repeat_times",
            "set_chime_paired_doorbells",
        ):
            assert hass.services.has_service(DOMAIN, action)

        # Clean up
        await async_unload_services(hass)


class TestAsyncUnloadServices:
    """Tests for async_unload_services."""

    async def test_unload_services_removes_services(self, hass: HomeAssistant):
        """Test that unload removes all services."""
        await async_setup_services(hass)
        assert hass.services.has_service(DOMAIN, SERVICE_REFRESH_DATA)
        assert hass.services.has_service(DOMAIN, SERVICE_RECONNECT_CLIENT)
        assert hass.services.has_service(DOMAIN, SERVICE_REMOVE_CLIENTS)

        await async_unload_services(hass)
        assert not hass.services.has_service(DOMAIN, SERVICE_REFRESH_DATA)
        assert not hass.services.has_service(DOMAIN, SERVICE_POWER_CYCLE_PORT)
        assert not hass.services.has_service(DOMAIN, SERVICE_RECONNECT_CLIENT)
        assert not hass.services.has_service(DOMAIN, SERVICE_REMOVE_CLIENTS)
        for action in (
            "set_chime_volume",
            "play_chime_ringtone",
            "set_chime_ringtone",
            "set_chime_repeat_times",
            "set_chime_paired_doorbells",
        ):
            assert not hass.services.has_service(DOMAIN, action)


class TestRefreshDataService:
    """Tests for refresh_data service handler."""

    async def test_refresh_data_no_coordinators(self, hass: HomeAssistant):
        """Test refresh data with no coordinators raises error."""
        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Insights coordinators"),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REFRESH_DATA,
                {},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_refresh_data_success(self, hass: HomeAssistant):
        """Test refresh data success."""
        mock_coordinator = MagicMock()
        mock_coordinator.async_refresh = AsyncMock()
        mock_coordinator.async_refresh_or_raise = AsyncMock()
        mock_coordinator.data = {"sites": {"site1": {}}}
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REFRESH_DATA,
                {},
                blocking=True,
            )

        # A console-wide call refreshes every child, Protect included.
        mock_coordinator.async_refresh_or_raise.assert_called_once_with(
            include_protect=True
        )
        # The facade's own async_refresh() only re-aggregates the cache.
        mock_coordinator.async_refresh.assert_not_called()

        await async_unload_services(hass)

    async def test_refresh_data_with_site_id(self, hass: HomeAssistant):
        """Test refresh data with specific site_id."""
        mock_coordinator = MagicMock()
        mock_coordinator.async_refresh = AsyncMock()
        mock_coordinator.async_refresh_or_raise = AsyncMock()
        mock_coordinator.data = {"sites": {"site1": {}}}
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REFRESH_DATA,
                {"site_id": "site1"},
                blocking=True,
            )

        # A site is a Network concept, so Protect is left alone.
        mock_coordinator.async_refresh_or_raise.assert_called_once_with(
            include_protect=False
        )

        await async_unload_services(hass)

    async def test_refresh_data_site_not_found_skips_coordinator(
        self, hass: HomeAssistant
    ):
        """Test refresh data skips coordinator when site_id not found."""
        mock_coordinator = MagicMock()
        mock_coordinator.async_refresh = AsyncMock()
        mock_coordinator.async_refresh_or_raise = AsyncMock()
        mock_coordinator.data = {"sites": {"site1": {}}}  # Only has site1
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            # A site_id no console owns is user error and must be reported,
            # not silently answered with "refreshed".
            pytest.raises(ServiceValidationError, match="site2"),
        ):
            # Request refresh for site2, which doesn't exist
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REFRESH_DATA,
                {"site_id": "site2"},  # Not in coordinator's sites
                blocking=True,
            )

        # Coordinator should NOT be refreshed since site2 wasn't found
        mock_coordinator.async_refresh_or_raise.assert_not_called()

        await async_unload_services(hass)

    async def test_refresh_data_skips_none_sites_and_refreshes_matching_console(
        self, hass: HomeAssistant
    ):
        """A console with no sites must not block a later matching console."""
        unavailable = MagicMock()
        unavailable.data = {"sites": None}
        unavailable.async_refresh_or_raise = AsyncMock()
        unavailable_entry = MagicMock()
        unavailable_entry.title = "Unavailable console"
        unavailable_entry.runtime_data = MagicMock()
        unavailable_entry.runtime_data.coordinator = unavailable

        matching = MagicMock()
        matching.data = {"sites": {"site2": {}}}
        matching.async_refresh_or_raise = AsyncMock()
        matching_entry = MagicMock()
        matching_entry.title = "Matching console"
        matching_entry.runtime_data = MagicMock()
        matching_entry.runtime_data.coordinator = matching

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[unavailable_entry, matching_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REFRESH_DATA,
                {"site_id": "site2"},
                blocking=True,
            )

        unavailable.async_refresh_or_raise.assert_not_called()
        matching.async_refresh_or_raise.assert_called_once_with(include_protect=False)

        await async_unload_services(hass)

    async def test_refresh_data_one_console_down_still_refreshes_the_others(
        self, hass: HomeAssistant
    ):
        """One unreachable console must not stop the others being refreshed."""
        broken = MagicMock()
        broken.data = {"sites": {"site1": {}}}
        broken.async_refresh_or_raise = AsyncMock(
            side_effect=HomeAssistantError(
                "Error refreshing UniFi Insights data: devices coordinator: timeout"
            )
        )
        broken_entry = MagicMock()
        broken_entry.title = "Upstairs console"
        broken_entry.runtime_data = MagicMock()
        broken_entry.runtime_data.coordinator = broken

        healthy = MagicMock()
        healthy.data = {"sites": {"site2": {}}}
        healthy.async_refresh_or_raise = AsyncMock()
        healthy_entry = MagicMock()
        healthy_entry.title = "Garage console"
        healthy_entry.runtime_data = MagicMock()
        healthy_entry.runtime_data.coordinator = healthy

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[broken_entry, healthy_entry],
            ),
            pytest.raises(HomeAssistantError) as raised,
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REFRESH_DATA,
                {},
                blocking=True,
            )

        # The healthy console was still attempted after the broken one failed.
        broken.async_refresh_or_raise.assert_called_once_with(include_protect=True)
        healthy.async_refresh_or_raise.assert_called_once_with(include_protect=True)

        message = str(raised.value)
        # The message names the console that failed, and does not double up
        # the "Error refreshing ..." prefix the facade already supplies.
        assert "Upstairs console" in message
        assert "timeout" in message
        assert "Garage console" not in message
        assert message.count("Error refreshing") == 1

        await async_unload_services(hass)


class TestRestartDeviceService:
    """Tests for restart_device service handler."""

    async def test_restart_device_no_coordinator(self, hass: HomeAssistant):
        """Test restart device with no coordinator raises error."""
        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Insights coordinator"),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RESTART_DEVICE,
                {"site_id": "site1", "device_id": "device1"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_restart_device_success(self, hass: HomeAssistant):
        """Test restart device success."""
        mock_coordinator = MagicMock()
        mock_coordinator.device_coordinator.async_restart_device = AsyncMock()
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RESTART_DEVICE,
                {"site_id": "site1", "device_id": "device1"},
                blocking=True,
            )

        mock_coordinator.device_coordinator.async_restart_device.assert_called_once_with(
            "site1", "device1"
        )

        await async_unload_services(hass)

    async def test_restart_device_failure(self, hass: HomeAssistant):
        """Test restart device failure raises error."""
        mock_coordinator = MagicMock()
        mock_coordinator.device_coordinator.async_restart_device = AsyncMock(
            side_effect=HomeAssistantError("Failed to restart device device1")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Failed to restart device"),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RESTART_DEVICE,
                {"site_id": "site1", "device_id": "device1"},
                blocking=True,
            )

        await async_unload_services(hass)


class _PowerCycleTestContext:
    """Helper managing mock coordinator and service lifecycle."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator_data: dict | None = None,
    ) -> None:
        self.hass = hass
        self.coordinator = MagicMock()
        self.coordinator.network_client = _network_client()
        self.coordinator.network_client._post = AsyncMock(return_value=None)
        self.coordinator.data = coordinator_data
        # The real device owner command, which runs the PoE eligibility check
        # against its own data before the HTTP request.
        device = MagicMock()
        device.network_client = self.coordinator.network_client
        device.data = coordinator_data
        device.async_power_cycle_port = (
            UnifiDeviceCoordinator.async_power_cycle_port.__get__(device)
        )
        self.coordinator.device_coordinator = device
        self.entry = MagicMock()
        self.entry.runtime_data = MagicMock()
        self.entry.runtime_data.coordinator = self.coordinator
        self._patch = None

    async def __aenter__(self) -> MagicMock:
        await async_setup_services(self.hass)
        self._patch = patch.object(
            self.hass.config_entries,
            "async_entries",
            return_value=[self.entry],
        )
        self._patch.start()
        return self.coordinator

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._patch:
            self._patch.stop()
        await async_unload_services(self.hass)


class TestPowerCyclePortService:
    """Tests for power_cycle_port service handler."""

    async def _call_service(
        self,
        hass: HomeAssistant,
        service_data: dict,
        coordinator_data: dict | None = None,
    ) -> MagicMock:
        """Call power_cycle_port service within managed test context."""
        async with _PowerCycleTestContext(hass, coordinator_data) as coord:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_POWER_CYCLE_PORT,
                service_data,
                blocking=True,
            )
            return coord

    async def test_power_cycle_port_success(self, hass: HomeAssistant) -> None:
        """Test power cycle port success path through real facade."""
        coord_data = {
            "sites": {"site1": {"name": "Site 1"}},
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "name": "Switch 1",
                        "interfaces": {
                            "ports": [
                                {
                                    "idx": 1,
                                    "name": "Port 1",
                                    "poe": {"enabled": True},
                                },
                                {
                                    "idx": 2,
                                    "name": "Port 2",
                                    "poe": {"enabled": False},
                                },
                            ]
                        },
                    }
                }
            },
        }
        coord = await self._call_service(
            hass,
            {"site_id": "site1", "device_id": "device1", "port_idx": 1},
            coord_data,
        )
        path = coord.network_client.build_api_path(
            "/sites/site1/devices/device1/interfaces/ports/1/actions"
        )
        coord.network_client._post.assert_awaited_once_with(
            path,
            json_data={"action": "POWER_CYCLE"},
        )

    async def test_power_cycle_port_legacy_port_format_success(
        self, hass: HomeAssistant
    ) -> None:
        """Test power cycle port with legacy ports array and omitted site_id."""
        coord_data = {
            "sites": {"site1": {"name": "Site 1"}},
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "ports": [
                            {"idx": 4, "poe": {"enabled": True}},
                        ],
                    }
                }
            },
        }
        coord = await self._call_service(
            hass,
            {"device_id": "device1", "port_idx": 4},
            coord_data,
        )
        path = coord.network_client.build_api_path(
            "/sites/site1/devices/device1/interfaces/ports/4/actions"
        )
        coord.network_client._post.assert_awaited_once_with(
            path,
            json_data={"action": "POWER_CYCLE"},
        )

    async def test_power_cycle_port_skips_non_dict_ports(
        self, hass: HomeAssistant
    ) -> None:
        """Test power cycle port handles non-dict items in port list."""
        coord_data = {
            "sites": {"site1": {"name": "Site 1"}},
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "interfaces": {
                            "ports": [
                                None,
                                {"idx": 5, "poe": {"enabled": True}},
                            ]
                        },
                    }
                }
            },
        }
        coord = await self._call_service(
            hass,
            {"site_id": "site1", "device_id": "device1", "port_idx": 5},
            coord_data,
        )
        path = coord.network_client.build_api_path(
            "/sites/site1/devices/device1/interfaces/ports/5/actions"
        )
        coord.network_client._post.assert_awaited_once_with(
            path,
            json_data={"action": "POWER_CYCLE"},
        )

    async def test_power_cycle_port_missing_site_id_unresolvable(
        self, hass: HomeAssistant
    ) -> None:
        """Test power cycle port raises when site_id cannot be resolved."""
        coord_data = {
            "sites": {"site1": {"name": "Site 1"}},
            "devices": None,
        }
        with pytest.raises(ServiceValidationError, match="Site ID is required"):
            await self._call_service(
                hass,
                {"device_id": "device1", "port_idx": 1},
                coord_data,
            )

    @pytest.mark.parametrize(
        ("device_data", "call_data", "expected_key"),
        [
            (
                {"interfaces": {"ports": [{"idx": 7, "name": "Normal Port"}]}},
                {"site_id": "site1", "device_id": "device1", "port_idx": 7},
                "port_not_poe",
            ),
            (
                {
                    "interfaces": {
                        "ports": [
                            {
                                "idx": 2,
                                "name": "Port 2",
                                "poe": {"enabled": False},
                            }
                        ]
                    }
                },
                {"site_id": "site1", "device_id": "device1", "port_idx": 2},
                "port_not_poe",
            ),
            (
                {
                    "interfaces": {
                        "ports": [
                            {
                                "idx": 1,
                                "name": "Port 1",
                                "poe": {"enabled": True},
                            }
                        ]
                    }
                },
                {"site_id": "site1", "device_id": "device1", "port_idx": 99},
                "port_not_found",
            ),
            (
                {"id": "device1", "name": "Switch"},
                {"site_id": "site1", "device_id": "device1", "port_idx": 1},
                "port_not_found",
            ),
            (
                None,
                {"site_id": "site1", "device_id": "unknown_dev", "port_idx": 1},
                None,
            ),
            (
                ...,
                {"site_id": "site1", "device_id": "device1", "port_idx": 1},
                "device_not_found",
            ),
        ],
    )
    async def test_power_cycle_port_validation_errors(
        self,
        hass: HomeAssistant,
        device_data,
        call_data,
        expected_key,
    ) -> None:
        """Test power cycle port validation errors raise ServiceValidationError."""
        if device_data is ...:
            devices = {"site1": {"device1": None}}
        elif device_data is None:
            devices = {"site1": {}}
        else:
            devices = {"site1": {"device1": {"id": "device1", **device_data}}}

        coord_data = {
            "sites": {"site1": {"name": "Site 1"}},
            "devices": devices,
        }

        async with _PowerCycleTestContext(hass, coord_data) as coord:
            with pytest.raises(ServiceValidationError) as exc_info:
                await hass.services.async_call(
                    DOMAIN,
                    SERVICE_POWER_CYCLE_PORT,
                    call_data,
                    blocking=True,
                )
            if expected_key is not None:
                assert exc_info.value.translation_key == expected_key
            else:
                assert call_data["device_id"] in str(exc_info.value)
            coord.network_client._post.assert_not_called()


class TestProtectServices:
    """Tests for UniFi Protect service handlers."""

    async def test_set_hdr_mode_success(self, hass: HomeAssistant):
        """Test set_hdr_mode success."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_hdr_mode",
                {"camera_id": "cam1", "mode": "auto"},
                blocking=True,
            )

        _protect(mock_coordinator).async_set_hdr_mode.assert_called_once_with(
            "cam1", "auto"
        )

        await async_unload_services(hass)

    async def test_set_video_mode_success(self, hass: HomeAssistant):
        """Test set_video_mode success."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_video_mode",
                {"camera_id": "cam1", "mode": "default"},
                blocking=True,
            )

        _protect(mock_coordinator).async_set_video_mode.assert_called_once_with(
            "cam1", "default"
        )

        await async_unload_services(hass)

    async def test_set_mic_volume_success(self, hass: HomeAssistant):
        """Test set_mic_volume success."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_mic_volume",
                {"camera_id": "cam1", "volume": 50},
                blocking=True,
            )

        _protect(mock_coordinator).async_set_microphone_volume.assert_called_once_with(
            "cam1", 50
        )

        await async_unload_services(hass)

    @pytest.mark.parametrize("volume", [1, 100])
    async def test_set_mic_volume_accepts_range_limits(
        self, hass: HomeAssistant, volume: int
    ):
        """Test set_mic_volume passes 1 and 100 to the coordinator unchanged."""
        mock_coordinator = MagicMock()
        _protect(mock_coordinator)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_mic_volume",
                {"camera_id": "cam1", "volume": volume},
                blocking=True,
            )

        _protect(mock_coordinator).async_set_microphone_volume.assert_called_once_with(
            "cam1", volume
        )

        await async_unload_services(hass)

    @pytest.mark.parametrize("volume", [0, 101])
    async def test_set_mic_volume_rejects_out_of_range(
        self, hass: HomeAssistant, volume: int
    ):
        """Test set_mic_volume rejects 0 and 101; the Protect API range is 1-100."""
        mock_coordinator = MagicMock()
        _protect(mock_coordinator)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(vol.Invalid),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_mic_volume",
                {"camera_id": "cam1", "volume": volume},
                blocking=True,
            )

        _protect(mock_coordinator).async_set_microphone_volume.assert_not_called()

        await async_unload_services(hass)


class TestLightServices:
    """Tests for light service handlers."""

    async def test_set_light_mode_success(self, hass: HomeAssistant):
        """Test set_light_mode success."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_light_mode",
                {"light_id": "light1", "mode": "always"},
                blocking=True,
            )

        _protect(mock_coordinator).async_set_light_mode.assert_called_once_with(
            "light1", "always"
        )

        await async_unload_services(hass)

    async def test_set_light_level_success(self, hass: HomeAssistant):
        """Test set_light_level success."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_light_level",
                {"light_id": "light1", "level": 60},
                blocking=True,
            )

        # 60% maps to LED level 4 (the Protect API takes 1-6)
        _protect(mock_coordinator).async_set_light_brightness.assert_called_once_with(
            "light1", 4
        )

        await async_unload_services(hass)

    @pytest.mark.parametrize(
        ("level", "led_level"),
        [(0, 1), (50, 3), (100, 6)],
    )
    async def test_set_light_level_sends_spec_patch_body(
        self, hass: HomeAssistant, level: int, led_level: int
    ):
        """Test set_light_level reaches PATCH /lights/{id} as a 1-6 ledLevel."""
        client = UniFiProtectClient(
            auth=ApiKeyAuth(api_key="test-key"),
            base_url="https://192.168.1.1",
            connection_type=ConnectionType.LOCAL,
        )
        client._patch = AsyncMock(return_value={"id": "light1", "mac": "00:11:22"})
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = client
        # The real Protect owner, so the whole path down to the client runs
        mock_coordinator.require_protect_coordinator.return_value = _real_protect_owner(
            hass, client
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_light_level",
                {"light_id": "light1", "level": level},
                blocking=True,
            )

        client._patch.assert_awaited_once_with(
            client.build_api_path("/lights/light1"),
            json_data={"lightDeviceSettings": {"ledLevel": led_level}},
        )

        await async_unload_services(hass)


class TestPTZServices:
    """Tests for PTZ service handlers."""

    async def test_ptz_move_success(self, hass: HomeAssistant):
        """Test ptz_move success."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "ptz_move",
                {"camera_id": "cam1", "preset": 2},
                blocking=True,
            )

        _protect(mock_coordinator).async_move_ptz_to_preset.assert_called_once_with(
            "cam1", 2
        )

        await async_unload_services(hass)

    async def test_ptz_patrol_start_success(self, hass: HomeAssistant):
        """Test ptz_patrol start success."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "ptz_patrol",
                {"camera_id": "cam1", "action": "start", "slot": 1},
                blocking=True,
            )

        _protect(mock_coordinator).async_start_ptz_patrol.assert_called_once_with(
            "cam1", 1
        )

        await async_unload_services(hass)

    async def test_ptz_patrol_stop_success(self, hass: HomeAssistant):
        """Test ptz_patrol stop success."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "ptz_patrol",
                {"camera_id": "cam1", "action": "stop"},
                blocking=True,
            )

        _protect(mock_coordinator).async_stop_ptz_patrol.assert_called_once_with("cam1")

        await async_unload_services(hass)


class TestChimeServices:
    """Tests for chime service handlers."""

    def test_ringtone_schema_accepts_trimmed_opaque_id(
        self,
    ) -> None:
        """IDs need not match an invented enum or a fixed hex pattern."""
        validated = cast(
            "dict[str, Any]",
            services.SET_CHIME_RINGTONE_SCHEMA({"ringtone_id": "  opaque-tone-id  "}),
        )
        assert validated["ringtone_id"] == "opaque-tone-id"

    @pytest.mark.parametrize("ringtone_id", ["", " ", None, 12, False])
    def test_ringtone_schema_rejects_invalid_id(self, ringtone_id: Any) -> None:
        """Blank and non-string IDs fail schema validation."""
        with pytest.raises(vol.Invalid):
            services.SET_CHIME_RINGTONE_SCHEMA({"ringtone_id": ringtone_id})

    @pytest.mark.parametrize("ringtone_id", ["", " ", "tone-new", "default"])
    def test_playback_compatibility_field_is_a_plain_string(self, ringtone_id):
        """Compatibility values are ignored and are not ringtone selections."""
        assert services.PLAY_CHIME_RINGTONE_SCHEMA({"ringtone_id": ringtone_id}) == {
            "ringtone_id": ringtone_id
        }

    @pytest.mark.parametrize("ringtone_id", [None, 12, False])
    def test_playback_compatibility_field_rejects_non_strings(self, ringtone_id):
        """The optional compatibility field retains plain-string validation."""
        with pytest.raises(vol.Invalid):
            services.PLAY_CHIME_RINGTONE_SCHEMA({"ringtone_id": ringtone_id})

    @pytest.mark.parametrize("service", ["set_chime_ringtone", "play_chime_ringtone"])
    def test_ringtone_ui_uses_text_selector(self, service: str) -> None:
        """Action UI accepts actual IDs and does not invent ringtone options."""
        definitions = yaml.safe_load(
            Path(services.__file__).with_suffix(".yaml").read_text(encoding="utf-8")
        )
        field = definitions[service]["fields"]["ringtone_id"]
        assert field["selector"] == {"text": None}
        schema = getattr(services, f"{service.upper()}_SCHEMA")
        assert (
            schema({"ringtone_id": field["example"]})["ringtone_id"]
            == (field["example"])
        )

    @pytest.mark.parametrize(
        "settings",
        [
            None,
            [],
            [{}],
            [
                {
                    "cameraId": "doorbell-a",
                    "repeatTimes": 2,
                    "ringtoneId": "tone-old",
                    "volume": 25,
                },
                {
                    "cameraId": "doorbell-b",
                    "repeatTimes": 4,
                    "ringtoneId": "tone-other",
                    "volume": 75,
                },
            ],
        ],
    )
    async def test_set_ringtone_service_reaches_real_facade_and_client(
        self, hass: HomeAssistant, settings: Any
    ) -> None:
        """The service uses the spec body or exposes malformed settings safely."""
        client = UniFiProtectClient(
            auth=ApiKeyAuth(api_key="test-key"),
            base_url="https://192.168.1.1",
            connection_type=ConnectionType.LOCAL,
        )
        chime = {"id": "chime1", "mac": "AA:BB:CC:DD:EE:FF", "ringSettings": settings}
        client._get = AsyncMock(return_value=chime)
        client._patch = AsyncMock(return_value=chime)
        coordinator = MagicMock(protect_client=client)
        coordinator.data = {"protect": {"chimes": {"chime1": chime}}}
        owner = _real_protect_owner(hass, client)
        owner.async_request_refresh = AsyncMock()  # type: ignore[method-assign]
        coordinator.require_protect_coordinator.return_value = owner
        entry = MagicMock(
            entry_id="entry-chime", runtime_data=MagicMock(coordinator=coordinator)
        )
        await async_setup_services(hass)
        with patch.object(hass.config_entries, "async_entries", return_value=[entry]):
            if settings and settings[0]:
                await hass.services.async_call(
                    DOMAIN,
                    SERVICE_SET_CHIME_RINGTONE,
                    {"chime_id": "chime1", "ringtone_id": "  tone-new  "},
                    blocking=True,
                )
                client._patch.assert_awaited_once_with(
                    client.build_api_path("/chimes/chime1"),
                    json_data={
                        "ringSettings": [
                            {**setting, "ringtoneId": "tone-new"}
                            for setting in settings
                        ]
                    },
                )
                owner.async_request_refresh.assert_awaited_once()
            else:
                with pytest.raises(HomeAssistantError, match="Unable to set ringtone"):
                    await hass.services.async_call(
                        DOMAIN,
                        SERVICE_SET_CHIME_RINGTONE,
                        {"chime_id": "chime1", "ringtone_id": "tone-new"},
                        blocking=True,
                    )
                client._patch.assert_not_awaited()
                owner.async_request_refresh.assert_not_awaited()
        await async_unload_services(hass)

    async def test_set_chime_volume_success(self, hass: HomeAssistant):
        """Test set_chime_volume success."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_chime_volume",
                {"chime_id": "chime1", "volume": 80},
                blocking=True,
            )

        _protect(mock_coordinator).async_set_chime_volume.assert_called_once_with(
            "chime1", 80
        )

        await async_unload_services(hass)

    @pytest.mark.parametrize(
        "optional",
        [
            {},
            {"ringtone_id": "synthetic-opaque-id"},
            {"ringtone_id": "default"},
            {"ringtone_id": ""},
        ],
    )
    async def test_play_chime_ringtone_unsupported(self, hass: HomeAssistant, optional):
        """Valid targets receive the same error with or without the ignored field."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        error = HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="chime_playback_unsupported",
        )
        _protect(mock_coordinator).async_play_chime.configure_mock(side_effect=error)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries, "async_entries", return_value=[mock_entry]
            ),
            pytest.raises(HomeAssistantError) as raised,
        ):
            await hass.services.async_call(
                DOMAIN,
                "play_chime_ringtone",
                {"chime_id": "chime1", **optional},
                blocking=True,
            )

        assert raised.value is error
        _protect(mock_coordinator).async_play_chime.assert_awaited_once_with("chime1")
        mock_coordinator.protect_client.chimes.play.assert_not_called()
        mock_coordinator.protect_client._post.assert_not_called()

        await async_unload_services(hass)

    @pytest.mark.parametrize("target", [{}, {"chime_id": "missing-chime"}])
    async def test_play_chime_invalid_target_precedes_unsupported_error(
        self, hass: HomeAssistant, target
    ):
        """Missing and unknown targets fail validation before facade dispatch."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        _protect(coordinator)
        coordinator.data = {"protect": {"chimes": {"chime1": {"id": "chime1"}}}}
        entry = MagicMock()
        entry.runtime_data.coordinator = coordinator
        await async_setup_services(hass)
        with (
            patch.object(hass.config_entries, "async_entries", return_value=[entry]),
            pytest.raises(ServiceValidationError),
        ):
            await hass.services.async_call(
                DOMAIN, "play_chime_ringtone", target, blocking=True
            )
        _protect(coordinator).async_play_chime.assert_not_called()
        coordinator.protect_client.chimes.play.assert_not_called()
        await async_unload_services(hass)


class TestNetworkServices:
    """Tests for network service handlers."""

    async def test_authorize_guest_success(self, hass: HomeAssistant):
        """Test authorize_guest authorizes the client via the coordinator."""
        mock_coordinator = MagicMock()
        mock_coordinator.config_coordinator.async_authorize_guest = AsyncMock()
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "authorize_guest",
                {"site_id": "site1", "client_id": "client1"},
                blocking=True,
            )

        mock_coordinator.config_coordinator.async_authorize_guest.assert_called_once_with(
            "site1", "client1"
        )

        await async_unload_services(hass)

    async def test_generate_voucher_success(self, hass: HomeAssistant):
        """Test generate_voucher success."""
        mock_coordinator = MagicMock()
        mock_coordinator.async_generate_voucher = AsyncMock()
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "generate_voucher",
                {"site_id": "site1"},
                blocking=True,
            )

        mock_coordinator.async_generate_voucher.assert_called_once()

        await async_unload_services(hass)

    async def test_generate_voucher_defaults_name_and_duration(
        self, hass: HomeAssistant
    ) -> None:
        """Test generate_voucher defaults name to Home Assistant and duration to 480."""
        mock_coordinator = MagicMock()
        mock_coordinator.async_generate_voucher = AsyncMock()
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "generate_voucher",
                {"site_id": "site1"},
                blocking=True,
            )

        mock_coordinator.async_generate_voucher.assert_called_once_with(
            "site1",
            count=1,
            time_limit_minutes=480,
            tx_rate_limit_kbps=None,
            rx_rate_limit_kbps=None,
            data_usage_limit_mbytes=None,
            name="Home Assistant",
        )

        await async_unload_services(hass)

    async def test_generate_voucher_zero_limits_are_still_forwarded(
        self, hass: HomeAssistant
    ) -> None:
        """Test generate_voucher forwards 0 limits to coordinator unchanged."""
        mock_coordinator = MagicMock()
        mock_coordinator.async_generate_voucher = AsyncMock()
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "generate_voucher",
                {
                    "site_id": "site1",
                    "upload_limit_kbps": 0,
                    "download_limit_kbps": 0,
                    "data_limit_mb": 0,
                },
                blocking=True,
            )

        mock_coordinator.async_generate_voucher.assert_called_once_with(
            "site1",
            count=1,
            time_limit_minutes=480,
            tx_rate_limit_kbps=0,
            rx_rate_limit_kbps=0,
            data_usage_limit_mbytes=0,
            name="Home Assistant",
        )

        await async_unload_services(hass)

    async def test_generate_voucher_with_note(self, hass: HomeAssistant) -> None:
        """Test generate_voucher uses provided note and schema duration default."""
        mock_coordinator = MagicMock()
        mock_coordinator.async_generate_voucher = AsyncMock()
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "generate_voucher",
                {"site_id": "site1", "note": "Event Guest"},
                blocking=True,
            )

        mock_coordinator.async_generate_voucher.assert_called_once_with(
            "site1",
            count=1,
            time_limit_minutes=480,
            tx_rate_limit_kbps=None,
            rx_rate_limit_kbps=None,
            data_usage_limit_mbytes=None,
            name="Event Guest",
        )

        await async_unload_services(hass)

    async def test_generate_voucher_custom_note_and_duration(
        self, hass: HomeAssistant
    ) -> None:
        """Test generate_voucher passes custom note as name and duration_minutes."""
        mock_coordinator = MagicMock()
        mock_coordinator.async_generate_voucher = AsyncMock()
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "generate_voucher",
                {
                    "site_id": "site1",
                    "note": "VIP Guest",
                    "duration_minutes": 120,
                    "count": 2,
                },
                blocking=True,
            )

        mock_coordinator.async_generate_voucher.assert_called_once_with(
            "site1",
            count=2,
            time_limit_minutes=120,
            tx_rate_limit_kbps=None,
            rx_rate_limit_kbps=None,
            data_usage_limit_mbytes=None,
            name="VIP Guest",
        )

        await async_unload_services(hass)

    async def test_delete_voucher_success(self, hass: HomeAssistant):
        """Test delete_voucher success."""
        mock_coordinator = MagicMock()
        mock_coordinator.async_delete_voucher = AsyncMock()
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "delete_voucher",
                {"site_id": "site1", "voucher_id": "voucher1"},
                blocking=True,
            )

        mock_coordinator.async_delete_voucher.assert_called_once()

        await async_unload_services(hass)


class TestServiceErrorHandling:
    """Tests for service error handling."""

    async def test_refresh_data_no_coordinator(self, hass: HomeAssistant):
        """Test refresh_data when no coordinators are found."""
        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Insights"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "refresh_data",
                {},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_refresh_data_error(self, hass: HomeAssistant):
        """Test refresh_data with coordinator error."""
        mock_coordinator = MagicMock()
        mock_coordinator.data = {"sites": {"default": {}}}
        # The facade always supplies its own "Error refreshing ..." sentence,
        # so mirror that here rather than a bare message the code never sees.
        mock_coordinator.async_refresh_or_raise = AsyncMock(
            side_effect=HomeAssistantError(
                "Error refreshing UniFi Insights data: devices coordinator: boom"
            )
        )
        mock_entry = MagicMock()
        mock_entry.title = "UniFi Insights"
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="UniFi Insights: Error refreshing"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "refresh_data",
                {},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_restart_device_no_coordinator(self, hass: HomeAssistant):
        """Test restart_device when no coordinator is found."""
        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Insights"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "restart_device",
                {"site_id": "site1", "device_id": "device1"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_restart_device_failed(self, hass: HomeAssistant):
        """Test restart_device when restart fails."""
        mock_coordinator = MagicMock()
        mock_coordinator.device_coordinator.async_restart_device = AsyncMock(
            side_effect=HomeAssistantError("Failed to restart device device1")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Failed to restart"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "restart_device",
                {"site_id": "site1", "device_id": "device1"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_restart_device_error(self, hass: HomeAssistant):
        """Test restart_device with exception."""
        mock_coordinator = MagicMock()
        mock_coordinator.device_coordinator.async_restart_device = AsyncMock(
            side_effect=HomeAssistantError("Error restarting device")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Error restarting"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "restart_device",
                {"site_id": "site1", "device_id": "device1"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_hdr_mode_no_protect(self, hass: HomeAssistant):
        """Test set_hdr_mode when no Protect coordinator is found."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = None
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Protect"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_hdr_mode",
                {"camera_id": "cam1", "mode": "on"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_hdr_mode_error(self, hass: HomeAssistant):
        """Test set_hdr_mode with exception."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator).async_set_hdr_mode.configure_mock(
            side_effect=HomeAssistantError("Error setting HDR mode")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Error setting HDR"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_hdr_mode",
                {"camera_id": "cam1", "mode": "on"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_video_mode_no_protect(self, hass: HomeAssistant):
        """Test set_video_mode when no Protect coordinator is found."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = None
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Protect"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_video_mode",
                {"camera_id": "cam1", "mode": "default"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_video_mode_error(self, hass: HomeAssistant):
        """Test set_video_mode with exception."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator).async_set_video_mode.configure_mock(
            side_effect=HomeAssistantError("Error setting video mode")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Error setting video"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_video_mode",
                {"camera_id": "cam1", "mode": "default"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_mic_volume_no_protect(self, hass: HomeAssistant):
        """Test set_mic_volume when no Protect coordinator is found."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = None
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Protect"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_mic_volume",
                {"camera_id": "cam1", "volume": 50},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_mic_volume_error(self, hass: HomeAssistant):
        """Test set_mic_volume with exception."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator).async_set_microphone_volume.configure_mock(
            side_effect=HomeAssistantError("Error setting mic volume")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Error setting mic"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_mic_volume",
                {"camera_id": "cam1", "volume": 50},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_light_mode_no_protect(self, hass: HomeAssistant):
        """Test set_light_mode when no Protect coordinator is found."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = None
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Protect"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_light_mode",
                {"light_id": "light1", "mode": "always"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_light_mode_error(self, hass: HomeAssistant):
        """Test set_light_mode with exception."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator).async_set_light_mode.configure_mock(
            side_effect=HomeAssistantError("Error setting light mode")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Error setting light mode"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_light_mode",
                {"light_id": "light1", "mode": "always"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_light_level_no_protect(self, hass: HomeAssistant):
        """Test set_light_level when no Protect coordinator is found."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = None
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Protect"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_light_level",
                {"light_id": "light1", "level": 50},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_light_level_error(self, hass: HomeAssistant):
        """Test set_light_level with exception."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator).async_set_light_brightness.configure_mock(
            side_effect=HomeAssistantError("Error setting light level")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Error setting light level"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_light_level",
                {"light_id": "light1", "level": 50},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_ptz_move_no_protect(self, hass: HomeAssistant):
        """Test ptz_move when no Protect coordinator is found."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = None
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Protect"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "ptz_move",
                {"camera_id": "cam1", "preset": 1},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_ptz_move_error(self, hass: HomeAssistant):
        """Test ptz_move with exception."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator).async_move_ptz_to_preset.configure_mock(
            side_effect=HomeAssistantError("Error moving PTZ")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Error moving PTZ"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "ptz_move",
                {"camera_id": "cam1", "preset": 1},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_ptz_patrol_start_no_protect(self, hass: HomeAssistant):
        """Test ptz_patrol start when no Protect coordinator is found."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = None
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Protect"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "ptz_patrol",
                {"camera_id": "cam1", "action": "start"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_ptz_patrol_stop_success(self, hass: HomeAssistant):
        """Test ptz_patrol stop success."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "ptz_patrol",
                {"camera_id": "cam1", "action": "stop"},
                blocking=True,
            )

        _protect(mock_coordinator).async_stop_ptz_patrol.assert_called_once_with("cam1")

        await async_unload_services(hass)

    async def test_ptz_patrol_error(self, hass: HomeAssistant):
        """Test ptz_patrol with exception."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator).async_start_ptz_patrol.configure_mock(
            side_effect=HomeAssistantError("Error controlling PTZ patrol")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Error controlling PTZ"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "ptz_patrol",
                {"camera_id": "cam1", "action": "start"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_chime_volume_no_protect(self, hass: HomeAssistant):
        """Test set_chime_volume when no Protect coordinator is found."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = None
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Protect"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_chime_volume",
                {"chime_id": "chime1", "volume": 50},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_chime_volume_error(self, hass: HomeAssistant):
        """Test set_chime_volume with exception."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator).async_set_chime_volume.configure_mock(
            side_effect=HomeAssistantError("Error setting chime volume")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Error setting chime volume"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_chime_volume",
                {"chime_id": "chime1", "volume": 50},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_play_chime_ringtone_no_protect(self, hass: HomeAssistant):
        """Test play_chime_ringtone when no Protect coordinator is found."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = None
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Protect"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "play_chime_ringtone",
                {"chime_id": "chime1"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_play_chime_ringtone_error(self, hass: HomeAssistant):
        """Test play_chime_ringtone with exception."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator).async_play_chime.configure_mock(
            side_effect=HomeAssistantError("Error playing chime")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Error playing chime"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "play_chime_ringtone",
                {"chime_id": "chime1"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_chime_ringtone_no_protect(self, hass: HomeAssistant):
        """Test set_chime_ringtone when no Protect coordinator is found."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = None
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Protect"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_chime_ringtone",
                {"chime_id": "chime1", "ringtone_id": "tone-1"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_chime_ringtone_error(self, hass: HomeAssistant):
        """Test set_chime_ringtone with exception."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator).async_set_chime_ringtone.configure_mock(
            side_effect=HomeAssistantError("Error setting chime ringtone")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Error setting chime ringtone"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_chime_ringtone",
                {"chime_id": "chime1", "ringtone_id": "tone-1"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_chime_repeat_times_no_protect(self, hass: HomeAssistant):
        """Test set_chime_repeat_times when no Protect coordinator is found."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = None
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Protect"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_chime_repeat_times",
                {"chime_id": "chime1", "repeat_times": 3},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_chime_repeat_times_error(self, hass: HomeAssistant):
        """Test set_chime_repeat_times with exception."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator).async_set_chime_repeat.configure_mock(
            side_effect=HomeAssistantError("Error setting chime repeat times")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Error setting chime repeat times"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_chime_repeat_times",
                {"chime_id": "chime1", "repeat_times": 3},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_chime_ringtone_success(self, hass: HomeAssistant):
        """Test set_chime_ringtone success (covers line 784)."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_chime_ringtone",
                {"chime_id": "chime1", "ringtone_id": "tone-1"},
                blocking=True,
            )

        _protect(mock_coordinator).async_set_chime_ringtone.assert_called_once_with(
            "chime1", "tone-1"
        )

        await async_unload_services(hass)

    async def test_set_chime_repeat_times_success(self, hass: HomeAssistant):
        """Test set_chime_repeat_times success (covers line 816)."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_chime_repeat_times",
                {"chime_id": "chime1", "repeat_times": 3},
                blocking=True,
            )

        _protect(mock_coordinator).async_set_chime_repeat.assert_called_once_with(
            "chime1", 3
        )

        await async_unload_services(hass)

    async def test_authorize_guest_no_coordinator(self, hass: HomeAssistant):
        """Test authorize_guest raises when no coordinator is found."""
        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Insights"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "authorize_guest",
                {"site_id": "site1", "client_id": "client1"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_authorize_guest_error(self, hass: HomeAssistant):
        """Test authorize_guest propagates coordinator errors."""
        mock_coordinator = MagicMock()
        mock_coordinator.config_coordinator.async_authorize_guest = AsyncMock(
            side_effect=HomeAssistantError("Unable to authorize guest client client1")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Unable to authorize guest"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "authorize_guest",
                {"site_id": "site1", "client_id": "client1"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_generate_voucher_no_coordinator(self, hass: HomeAssistant):
        """Test generate_voucher when no coordinator is found."""
        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Insights"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "generate_voucher",
                {"site_id": "site1"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_generate_voucher_error(self, hass: HomeAssistant):
        """Test generate_voucher with exception."""
        mock_coordinator = MagicMock()
        mock_coordinator.async_generate_voucher = AsyncMock(
            side_effect=HomeAssistantError("Error generating voucher")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Error generating voucher"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "generate_voucher",
                {"site_id": "site1"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_delete_voucher_no_coordinator(self, hass: HomeAssistant):
        """Test delete_voucher when no coordinator is found."""
        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Insights"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "delete_voucher",
                {"site_id": "site1", "voucher_id": "voucher1"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_delete_voucher_error(self, hass: HomeAssistant):
        """Test delete_voucher with exception."""
        mock_coordinator = MagicMock()
        mock_coordinator.async_delete_voucher = AsyncMock(
            side_effect=HomeAssistantError("Error deleting voucher")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Error deleting voucher"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "delete_voucher",
                {"site_id": "site1", "voucher_id": "voucher1"},
                blocking=True,
            )

        await async_unload_services(hass)


class TestTriggerAlarmService:
    """Tests for trigger_alarm service."""

    async def test_trigger_alarm_success(self, hass: HomeAssistant):
        """Test trigger_alarm service success."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "trigger_alarm",
                {"alarm_id": "alarm1"},
                blocking=True,
            )

        _protect(mock_coordinator).async_trigger_alarm.assert_called_once_with("alarm1")

        await async_unload_services(hass)

    async def test_trigger_alarm_no_coordinator(self, hass: HomeAssistant):
        """Test trigger_alarm when no coordinator is found."""
        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Protect"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "trigger_alarm",
                {"alarm_id": "alarm1"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_trigger_alarm_no_protect_client(self, hass: HomeAssistant):
        """Test trigger_alarm when coordinator has no protect_client."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = None
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Protect"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "trigger_alarm",
                {"alarm_id": "alarm1"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_trigger_alarm_error(self, hass: HomeAssistant):
        """Test trigger_alarm with exception."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator).async_trigger_alarm.configure_mock(
            side_effect=HomeAssistantError("Error triggering alarm")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Error triggering alarm"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "trigger_alarm",
                {"alarm_id": "alarm1"},
                blocking=True,
            )

        await async_unload_services(hass)


class TestCreateLiveviewService:
    """Tests for create_liveview service."""

    async def test_create_liveview_success(self, hass: HomeAssistant):
        """Test create_liveview service success."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "create_liveview",
                {"name": "Test Liveview", "layout": 2, "is_default": True},
                blocking=True,
            )

        _protect(mock_coordinator).async_create_liveview.assert_called_once_with(
            name="Test Liveview", layout=2, is_default=True
        )

        await async_unload_services(hass)

    async def test_create_liveview_no_coordinator(self, hass: HomeAssistant):
        """Test create_liveview when no coordinator is found."""
        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Protect"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "create_liveview",
                {"name": "Test Liveview", "layout": 2},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_create_liveview_no_protect_client(self, hass: HomeAssistant):
        """Test create_liveview when coordinator has no protect_client."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = None
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Protect"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "create_liveview",
                {"name": "Test Liveview", "layout": 2},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_create_liveview_error(self, hass: HomeAssistant):
        """Test create_liveview with exception."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator).async_create_liveview.configure_mock(
            side_effect=HomeAssistantError("Error creating liveview")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Error creating liveview"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "create_liveview",
                {"name": "Test Liveview", "layout": 2},
                blocking=True,
            )

        await async_unload_services(hass)


class TestSetLiveviewService:
    """Tests for set_liveview service."""

    async def test_set_liveview_success(self, hass: HomeAssistant):
        """Test set_liveview service success."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator)
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_liveview",
                {"viewer_id": "viewer1", "liveview_id": "liveview1"},
                blocking=True,
            )

        _protect(mock_coordinator).async_update_viewer.assert_called_once_with(
            "viewer1", liveview="liveview1"
        )

        await async_unload_services(hass)

    async def test_set_liveview_no_coordinator(self, hass: HomeAssistant):
        """Test set_liveview when no coordinator is found."""
        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Protect"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_liveview",
                {"viewer_id": "viewer1", "liveview_id": "liveview1"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_liveview_no_protect_client(self, hass: HomeAssistant):
        """Test set_liveview when coordinator has no protect_client."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = None
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="No UniFi Protect"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_liveview",
                {"viewer_id": "viewer1", "liveview_id": "liveview1"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_set_liveview_error(self, hass: HomeAssistant):
        """Test set_liveview with exception."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        _protect(mock_coordinator).async_update_viewer.configure_mock(
            side_effect=HomeAssistantError("Error setting liveview")
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[mock_entry],
            ),
            pytest.raises(HomeAssistantError, match="Error setting liveview"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_liveview",
                {"viewer_id": "viewer1", "liveview_id": "liveview1"},
                blocking=True,
            )

        await async_unload_services(hass)


class TestConsoleOwnershipRouting:
    """Tests for multi-console routing and ownership validation."""

    @pytest.fixture
    def multi_console_setup(self):
        """Create mock setup with two network consoles and two Protect consoles."""
        coord1 = MagicMock()
        coord1.protect_client = MagicMock()
        coord1.data = {
            "sites": {"site1": {"id": "site1", "name": "Site 1"}},
            "devices": {"site1": {"dev1": {"id": "dev1"}}},
            "clients": {"site1": {"client1": {"id": "client1"}}},
            "protect": {
                "cameras": {"cam1": {"id": "cam1"}},
                "lights": {"light1": {"id": "light1"}},
                "chimes": {"chime1": {"id": "chime1"}},
                "viewers": {"viewer1": {"id": "viewer1"}},
                "liveviews": {"lv1": {"id": "lv1"}},
            },
        }
        coord1.device_coordinator.async_restart_device = AsyncMock()
        coord1.config_coordinator.async_authorize_guest = AsyncMock()
        coord1.async_generate_voucher = AsyncMock()
        coord1.async_delete_voucher = AsyncMock()
        _protect(coord1)

        entry1 = MagicMock()
        entry1.entry_id = "entry_1"
        entry1.runtime_data = MagicMock()
        entry1.runtime_data.coordinator = coord1

        coord2 = MagicMock()
        coord2.protect_client = MagicMock()
        coord2.data = {
            "sites": {"site2": {"id": "site2", "name": "Site 2"}},
            "devices": {"site2": {"dev2": {"id": "dev2"}}},
            "clients": {"site2": {"client2": {"id": "client2"}}},
            "protect": {
                "cameras": {"cam2": {"id": "cam2"}},
                "lights": {"light2": {"id": "light2"}},
                "chimes": {"chime2": {"id": "chime2"}},
                "viewers": {"viewer2": {"id": "viewer2"}},
                "liveviews": {"lv2": {"id": "lv2"}},
            },
        }
        coord2.device_coordinator.async_restart_device = AsyncMock()
        coord2.config_coordinator.async_authorize_guest = AsyncMock()
        coord2.async_generate_voucher = AsyncMock()
        coord2.async_delete_voucher = AsyncMock()
        _protect(coord2)

        entry2 = MagicMock()
        entry2.entry_id = "entry_2"
        entry2.runtime_data = MagicMock()
        entry2.runtime_data.coordinator = coord2

        return entry1, coord1, entry2, coord2

    async def test_network_service_routes_to_owning_console(
        self, hass: HomeAssistant, multi_console_setup
    ):
        """Test network service calls route to console owning site and device."""
        entry1, coord1, entry2, coord2 = multi_console_setup
        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[entry1, entry2],
        ):
            # Target device on console 2
            await hass.services.async_call(
                DOMAIN,
                "restart_device",
                {"site_id": "site2", "device_id": "dev2"},
                blocking=True,
            )
            coord2.device_coordinator.async_restart_device.assert_called_once_with(
                "site2", "dev2"
            )
            coord1.device_coordinator.async_restart_device.assert_not_called()

            # Target device on console 1
            await hass.services.async_call(
                DOMAIN,
                "restart_device",
                {"site_id": "site1", "device_id": "dev1"},
                blocking=True,
            )
            coord1.device_coordinator.async_restart_device.assert_called_once_with(
                "site1", "dev1"
            )

        await async_unload_services(hass)

    async def test_protect_services_route_to_owning_console(
        self, hass: HomeAssistant, multi_console_setup
    ):
        """Test Protect services route to the console owning the target resource."""
        entry1, coord1, entry2, coord2 = multi_console_setup
        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[entry1, entry2],
        ):
            # Camera services on console 2
            await hass.services.async_call(
                DOMAIN,
                "set_hdr_mode",
                {"camera_id": "cam2", "mode": "auto"},
                blocking=True,
            )
            _protect(coord2).async_set_hdr_mode.assert_called_once_with("cam2", "auto")

            await hass.services.async_call(
                DOMAIN,
                "set_video_mode",
                {"camera_id": "cam2", "mode": "sport"},
                blocking=True,
            )
            _protect(coord2).async_set_video_mode.assert_called_once_with(
                "cam2", "sport"
            )

            await hass.services.async_call(
                DOMAIN,
                "set_mic_volume",
                {"camera_id": "cam2", "volume": 80},
                blocking=True,
            )
            _protect(coord2).async_set_microphone_volume.assert_called_once_with(
                "cam2", 80
            )

            await hass.services.async_call(
                DOMAIN,
                "ptz_move",
                {"camera_id": "cam2", "preset": 2},
                blocking=True,
            )
            _protect(coord2).async_move_ptz_to_preset.assert_called_once_with("cam2", 2)

            await hass.services.async_call(
                DOMAIN,
                "ptz_patrol",
                {"camera_id": "cam2", "action": "start", "slot": 1},
                blocking=True,
            )
            _protect(coord2).async_start_ptz_patrol.assert_called_once_with("cam2", 1)

            # Light services on console 1
            await hass.services.async_call(
                DOMAIN,
                "set_light_mode",
                {"light_id": "light1", "mode": "motion"},
                blocking=True,
            )
            _protect(coord1).async_set_light_mode.assert_called_once_with(
                "light1", "motion"
            )
            _protect(coord2).async_set_light_mode.assert_not_called()

            await hass.services.async_call(
                DOMAIN,
                "set_light_level",
                {"light_id": "light1", "level": 60},
                blocking=True,
            )
            # 60% maps to LED level 4 (Protect takes 1-6)
            _protect(coord1).async_set_light_brightness.assert_called_once_with(
                "light1", 4
            )

            # Chime services on console 2
            await hass.services.async_call(
                DOMAIN,
                "set_chime_volume",
                {"chime_id": "chime2", "volume": 70},
                blocking=True,
            )
            _protect(coord2).async_set_chime_volume.assert_called_once_with(
                "chime2", 70
            )

            await hass.services.async_call(
                DOMAIN,
                "set_chime_volume",
                {"chime_id": "chime2", "camera_id": "cam2", "volume": 40},
                blocking=True,
            )
            # camera_id only selects the owning console; the Protect API call
            # itself stays chime-wide.
            assert _protect(coord2).async_set_chime_volume.call_count == 2
            _protect(coord2).async_set_chime_volume.assert_called_with("chime2", 40)
            _protect(coord1).async_set_chime_volume.assert_not_called()

            _protect(coord2).async_play_chime.side_effect = HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="chime_playback_unsupported",
            )
            with pytest.raises(HomeAssistantError) as raised:
                await hass.services.async_call(
                    DOMAIN,
                    "play_chime_ringtone",
                    {"chime_id": "chime2", "ringtone_id": "synthetic-opaque-id"},
                    blocking=True,
                )
            assert raised.value.translation_key == "chime_playback_unsupported"
            _protect(coord2).async_play_chime.assert_called_once_with("chime2")
            _protect(coord1).async_play_chime.assert_not_called()
            coord1.protect_client._post.assert_not_called()
            coord2.protect_client._post.assert_not_called()

            await hass.services.async_call(
                DOMAIN,
                "set_chime_ringtone",
                {"chime_id": "chime2", "ringtone_id": "tone-1"},
                blocking=True,
            )
            _protect(coord2).async_set_chime_ringtone.assert_called_once_with(
                "chime2", "tone-1"
            )

            await hass.services.async_call(
                DOMAIN,
                "set_chime_ringtone",
                {"chime_id": "chime2", "camera_id": "cam2", "ringtone_id": "tone-2"},
                blocking=True,
            )
            # camera_id only selects the owning console; the Protect API call
            # itself stays chime-wide.
            assert _protect(coord2).async_set_chime_ringtone.call_count == 2
            _protect(coord2).async_set_chime_ringtone.assert_called_with(
                "chime2", "tone-2"
            )
            _protect(coord1).async_set_chime_ringtone.assert_not_called()

            await hass.services.async_call(
                DOMAIN,
                "set_chime_repeat_times",
                {"chime_id": "chime2", "repeat_times": 3},
                blocking=True,
            )
            _protect(coord2).async_set_chime_repeat.assert_called_once_with("chime2", 3)

            # trigger_alarm takes an alarm-manager webhook id that appears in
            # no coordinator collection, so the console must be named.
            await hass.services.async_call(
                DOMAIN,
                "trigger_alarm",
                {"alarm_id": "alarm1", "console_id": entry1.entry_id},
                blocking=True,
            )
            _protect(coord1).async_trigger_alarm.assert_called_once_with("alarm1")
            _protect(coord2).async_trigger_alarm.assert_not_called()

            await hass.services.async_call(
                DOMAIN,
                "set_liveview",
                {"viewer_id": "viewer2", "liveview_id": "lv2"},
                blocking=True,
            )
            _protect(coord2).async_update_viewer.assert_called_once_with(
                "viewer2", liveview="lv2"
            )

        await async_unload_services(hass)

    async def test_cross_console_network_targeting_prevented(
        self, hass: HomeAssistant, multi_console_setup
    ):
        """Test error when device belongs to a different console."""
        entry1, _coord1, entry2, _coord2 = multi_console_setup
        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[entry1, entry2],
            ),
            pytest.raises(
                ServiceValidationError, match="belongs to a different console"
            ),
        ):
            await hass.services.async_call(
                DOMAIN,
                "restart_device",
                {"site_id": "site1", "device_id": "dev2"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_cross_console_protect_targeting_prevented(
        self, hass: HomeAssistant, multi_console_setup
    ):
        """Test error when secondary Protect resource is on another console."""
        entry1, _coord1, entry2, _coord2 = multi_console_setup
        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[entry1, entry2],
            ),
            pytest.raises(
                ServiceValidationError, match="belongs to a different Protect console"
            ),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_chime_volume",
                {"chime_id": "chime1", "camera_id": "cam2", "volume": 50},
                blocking=True,
            )

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[entry1, entry2],
            ),
            pytest.raises(
                ServiceValidationError, match="belongs to a different Protect console"
            ),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_liveview",
                {"viewer_id": "viewer1", "liveview_id": "lv2"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_legacy_site_id_only_calls_route_correctly(
        self, hass: HomeAssistant, multi_console_setup
    ):
        """Test legacy site_id-only calls route to the console owning the site."""
        entry1, coord1, entry2, coord2 = multi_console_setup
        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[entry1, entry2],
        ):
            await hass.services.async_call(
                DOMAIN,
                "generate_voucher",
                {"site_id": "site2"},
                blocking=True,
            )
            coord2.async_generate_voucher.assert_called_once()
            coord1.async_generate_voucher.assert_not_called()

            await hass.services.async_call(
                DOMAIN,
                "delete_voucher",
                {"site_id": "site1", "voucher_id": "v1"},
                blocking=True,
            )
            coord1.async_delete_voucher.assert_called_once_with("site1", "v1")
            coord2.async_delete_voucher.assert_not_called()

            await hass.services.async_call(
                DOMAIN,
                "authorize_guest",
                {"site_id": "site2", "client_id": "client2"},
                blocking=True,
            )
            coord2.config_coordinator.async_authorize_guest.assert_called_once_with(
                "site2", "client2"
            )
            coord1.config_coordinator.async_authorize_guest.assert_not_called()

        await async_unload_services(hass)

    async def test_ambiguous_network_target_fails_informatively(
        self, hass: HomeAssistant, multi_console_setup
    ):
        """Test ambiguous network target across consoles fails with clear error."""
        entry1, coord1, entry2, coord2 = multi_console_setup
        # Both consoles contain the same site "site_shared"
        coord1.data["sites"]["site_shared"] = {"id": "site_shared"}
        coord2.data["sites"]["site_shared"] = {"id": "site_shared"}

        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[entry1, entry2],
            ),
            pytest.raises(
                ServiceValidationError,
                match=r"Multiple consoles contain site 'site_shared'",
            ),
        ):
            await hass.services.async_call(
                DOMAIN,
                "generate_voucher",
                {"site_id": "site_shared"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_missing_network_target_fails_informatively(
        self, hass: HomeAssistant, multi_console_setup
    ):
        """Test missing site or device fails informatively."""
        entry1, _coord1, entry2, _coord2 = multi_console_setup
        await async_setup_services(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[entry1, entry2],
            ),
            pytest.raises(
                ServiceValidationError, match=r"Site 'unknown_site' not found"
            ),
        ):
            await hass.services.async_call(
                DOMAIN,
                "restart_device",
                {"site_id": "unknown_site", "device_id": "dev1"},
                blocking=True,
            )

        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[entry1, entry2],
            ),
            pytest.raises(
                ServiceValidationError, match=r"Device 'missing_dev' not found"
            ),
        ):
            await hass.services.async_call(
                DOMAIN,
                "restart_device",
                {"site_id": "site1", "device_id": "missing_dev"},
                blocking=True,
            )

        await async_unload_services(hass)

    async def test_ambiguous_and_missing_protect_target_fails(
        self, hass: HomeAssistant, multi_console_setup
    ):
        """Test ambiguous and missing Protect targets fail."""
        entry1, _coord1, entry2, _coord2 = multi_console_setup
        await async_setup_services(hass)

        # Ambiguous when multiple Protect consoles exist and no target is provided
        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[entry1, entry2],
            ),
            pytest.raises(
                ServiceValidationError, match="Multiple UniFi Protect consoles"
            ),
        ):
            await hass.services.async_call(
                DOMAIN,
                "create_liveview",
                {"name": "MyView", "layout": 2},
                blocking=True,
            )

        # Missing Protect resource
        with (
            patch.object(
                hass.config_entries,
                "async_entries",
                return_value=[entry1, entry2],
            ),
            pytest.raises(
                ServiceValidationError, match=r"Camera 'cam_nonexistent' not found"
            ),
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_hdr_mode",
                {"camera_id": "cam_nonexistent", "mode": "auto"},
                blocking=True,
            )

        # A secondary resource no console claims must NOT fail the call: it is
        # usually just newer than the last refresh. It proceeds on the console
        # that owns the primary resource.
        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[entry1, entry2],
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_chime_volume",
                {"chime_id": "chime1", "camera_id": "cam_unknown", "volume": 50},
                blocking=True,
            )
            _protect(_coord1).async_set_chime_volume.assert_called_once_with(
                "chime1", 50
            )
            _protect(_coord2).async_set_chime_volume.assert_not_called()

        await async_unload_services(hass)

    def test_direct_resolver_unit_tests(self, hass: HomeAssistant, multi_console_setup):
        """Direct branch coverage tests for network and protect resolvers."""
        entry1, coord1, entry2, coord2 = multi_console_setup

        # Test no entries found
        with patch.object(hass.config_entries, "async_entries", return_value=[]):
            with pytest.raises(
                ServiceValidationError, match="No UniFi Insights coordinator"
            ):
                _get_coordinator_for_network_resource(hass)
            with pytest.raises(
                ServiceValidationError, match="No UniFi Protect coordinator"
            ):
                _get_coordinator_for_protect_resource(hass)

        # Test single protect console with create_liveview
        with patch.object(hass.config_entries, "async_entries", return_value=[entry1]):
            c, _ = _get_coordinator_for_protect_resource(hass)
            assert c == coord1

        # Test helper functions directly
        assert _coord_data(None) is None
        assert _coord_data(MagicMock(runtime_data=None)) is None
        mock_non_dict = MagicMock()
        mock_non_dict.runtime_data.coordinator.data = "not-a-dict"
        assert _coord_data(mock_non_dict) is None

        # Test _entry_has_site fallback
        assert _entry_has_site(MagicMock(runtime_data=None), "any") is True
        # Test _entry_has_device fallback
        assert _entry_has_device(MagicMock(runtime_data=None), "site1", "dev1") is True
        # Test _entry_has_client fallback
        assert _entry_has_client(MagicMock(runtime_data=None), "site1", "c1") is True
        # Test _protect_entry_has_resource fallback
        assert (
            _protect_entry_has_resource(MagicMock(runtime_data=None), "cameras", "c1")
            is True
        )

        # Test ambiguous device across multiple consoles
        coord1.data["sites"]["site_ambig"] = {}
        coord2.data["sites"]["site_ambig"] = {}
        coord1.data["devices"]["site_ambig"] = {"dev_dup": {}}
        coord2.data["devices"]["site_ambig"] = {"dev_dup": {}}
        with (
            patch.object(
                hass.config_entries, "async_entries", return_value=[entry1, entry2]
            ),
            pytest.raises(ServiceValidationError, match="Multiple consoles found for"),
        ):
            _get_coordinator_for_network_resource(
                hass, site_id="site_ambig", device_id="dev_dup"
            )

        # Test ambiguous protect resource across consoles
        coord1.data["protect"]["cameras"]["cam_dup"] = {}
        coord2.data["protect"]["cameras"]["cam_dup"] = {}
        with (
            patch.object(
                hass.config_entries, "async_entries", return_value=[entry1, entry2]
            ),
            pytest.raises(
                ServiceValidationError, match="Multiple Protect consoles contain"
            ),
        ):
            _get_coordinator_for_protect_resource(
                hass, resource_type="camera", resource_id="cam_dup"
            )

    async def test_reconnect_client_ambiguous_across_consoles(
        self, hass: HomeAssistant, multi_console_setup
    ):
        """Test reconnect_client rejects ambiguous client across consoles."""
        entry1, coord1, entry2, coord2 = multi_console_setup
        coord1.data["sites"]["site_shared"] = {"id": "site_shared"}
        coord2.data["sites"]["site_shared"] = {"id": "site_shared"}
        coord1.data["clients"]["site_shared"] = {
            "client_shared": {
                "id": "client_shared",
                "connected": True,
                "type": "WIRELESS",
            }
        }
        coord2.data["clients"]["site_shared"] = {
            "client_shared": {
                "id": "client_shared",
                "connected": True,
                "type": "WIRELESS",
            }
        }
        await async_setup_services(hass)
        with (
            patch.object(
                hass.config_entries, "async_entries", return_value=[entry1, entry2]
            ),
            pytest.raises(
                ServiceValidationError,
                match=(
                    r"Multiple consoles contain client 'client_shared'; target is"
                    r" ambiguous"
                ),
            ),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RECONNECT_CLIENT,
                {"site_id": "site_shared", "client_id": "client_shared"},
                blocking=True,
            )
        coord1.device_coordinator.async_reconnect_client.assert_not_called()
        coord2.device_coordinator.async_reconnect_client.assert_not_called()
        await async_unload_services(hass)

    async def test_reconnect_client_cross_console_ownership_violation(
        self,
        hass: HomeAssistant,
        multi_console_setup,
        device_registry: dr.DeviceRegistry,
    ):
        """Test reconnect_client rejects cross-console client ownership."""
        entry1, _coord1, entry2, _coord2 = multi_console_setup
        mock_entry2 = MockConfigEntry(
            domain=DOMAIN, entry_id=entry2.entry_id, title="Console 2"
        )
        mock_entry2.add_to_hass(hass)
        dev2 = device_registry.async_get_or_create(
            config_entry_id=entry2.entry_id,
            identifiers={(DOMAIN, "site2_client2")},
        )
        await async_setup_services(hass)
        with (
            patch.object(
                hass.config_entries, "async_entries", return_value=[entry1, entry2]
            ),
            pytest.raises(ServiceValidationError, match="belongs to a different"),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RECONNECT_CLIENT,
                {"site_id": "site1", "client_id": dev2.id},
                blocking=True,
            )
        await async_unload_services(hass)


class TestServiceCoordinatorContract:
    """Guard the service layer against calling methods a coordinator lacks."""

    @pytest.mark.parametrize(
        ("variable", "coordinator_class"),
        [
            ("coordinator", UnifiFacadeCoordinator),
            ("carrier_coordinator", UnifiCarrierFabricCoordinator),
            ("access_coordinator", UnifiInsightsAccessCoordinator),
        ],
    )
    def test_every_coordinator_method_called_by_services_exists(
        self, variable, coordinator_class
    ):
        """Every ``<variable>.<method>()`` in services.py must exist on its class.

        ``coordinator`` is the console facade, ``carrier_coordinator`` the
        Carrier Fabric coordinator, and ``access_coordinator`` the UniFi Access
        coordinator used by the admin user services.

        The service tests drive MagicMock coordinators, which happily accept any
        attribute name. That is how ``async_set_camera_chime_volume`` - a method
        no coordinator ever defined - shipped green. This test reads the real
        source instead of a mock.
        """
        services_py = (
            Path(__file__).parent.parent
            / "custom_components"
            / "unifi_insights"
            / "services.py"
        )
        tree = ast.parse(services_py.read_text(encoding="utf-8"))

        calls: list[tuple[str, int, list[str], bool]] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == variable
            ):
                starred = any(isinstance(a, ast.Starred) for a in node.args) or any(
                    kw.arg is None for kw in node.keywords
                )
                calls.append(
                    (
                        node.func.attr,
                        len(node.args),
                        [kw.arg for kw in node.keywords if kw.arg],
                        starred,
                    )
                )

        assert calls, f"no {variable}.<method>() calls found - parser broke"

        missing = sorted(
            {name for name, _, _, _ in calls if not hasattr(coordinator_class, name)}
        )
        assert not missing, (
            f"services.py calls {variable} methods that do not exist on "
            f"{coordinator_class.__name__}: {missing}"
        )

        # hasattr alone would still accept a 3-arg call to a 2-arg method, which
        # is exactly how the per-camera chime bug could come back. Bind instead.
        bad_arity: list[str] = []
        for name, positional, keywords, starred in calls:
            if starred:
                continue
            signature = inspect.signature(getattr(coordinator_class, name))
            try:
                signature.bind(
                    object(),  # self
                    *[object()] * positional,
                    **dict.fromkeys(keywords, object()),
                )
            except TypeError as err:
                bad_arity.append(f"{name}: {err}")
        assert not bad_arity, (
            f"services.py calls {variable} methods with arguments they do not "
            f"accept: {bad_arity}"
        )


class TestAlarmRouting:
    """`alarm_id` is an alarm-manager webhook trigger, not a cached resource."""

    @staticmethod
    def _console(entry_id, title):
        """Build a console whose Protect data has every real collection."""
        coord = MagicMock()
        coord.protect_client = MagicMock()
        coord.data = {
            "sites": {},
            "devices": {},
            "clients": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "doorlocks": {},
                "viewports": {},
                "liveviews": {},
                "protect_info": {},
                "events": {},
            },
        }
        _protect(coord)
        entry = MagicMock()
        entry.entry_id = entry_id
        entry.title = title
        entry.runtime_data = MagicMock()
        entry.runtime_data.coordinator = coord
        return entry, coord

    async def test_single_console_routes_despite_unknown_id(self, hass: HomeAssistant):
        """One console takes the call even though the id is in no collection."""
        entry, coord = self._console("entry_1", "Dream Machine")

        await async_setup_services(hass)
        with patch.object(hass.config_entries, "async_entries", return_value=[entry]):
            await hass.services.async_call(
                DOMAIN,
                "trigger_alarm",
                {"alarm_id": "webhook-trigger-1"},
                blocking=True,
            )

        _protect(coord).async_trigger_alarm.assert_called_once_with("webhook-trigger-1")
        await async_unload_services(hass)

    async def test_two_consoles_without_console_id_is_refused(
        self, hass: HomeAssistant
    ):
        """With two consoles and nothing to route on, ask for console_id."""
        entry1, coord1 = self._console("entry_1", "Dream Machine")
        entry2, coord2 = self._console("entry_2", "Cloud Key")

        await async_setup_services(hass)
        with (
            patch.object(
                hass.config_entries, "async_entries", return_value=[entry1, entry2]
            ),
            pytest.raises(ServiceValidationError, match="console_id"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "trigger_alarm",
                {"alarm_id": "webhook-trigger-1"},
                blocking=True,
            )

        _protect(coord1).async_trigger_alarm.assert_not_called()
        _protect(coord2).async_trigger_alarm.assert_not_called()
        await async_unload_services(hass)

    async def test_console_id_selects_by_title_or_entry_id(self, hass: HomeAssistant):
        """console_id accepts the entry ID or the console's title."""
        entry1, coord1 = self._console("entry_1", "Dream Machine")
        entry2, coord2 = self._console("entry_2", "Cloud Key")

        await async_setup_services(hass)
        with patch.object(
            hass.config_entries, "async_entries", return_value=[entry1, entry2]
        ):
            await hass.services.async_call(
                DOMAIN,
                "trigger_alarm",
                {"alarm_id": "t1", "console_id": "Cloud Key"},
                blocking=True,
            )
            _protect(coord2).async_trigger_alarm.assert_called_once_with("t1")
            _protect(coord1).async_trigger_alarm.assert_not_called()

            await hass.services.async_call(
                DOMAIN,
                "trigger_alarm",
                {"alarm_id": "t2", "console_id": "entry_1"},
                blocking=True,
            )
            _protect(coord1).async_trigger_alarm.assert_called_once_with("t2")

        await async_unload_services(hass)

    async def test_unknown_console_id_lists_the_configured_ones(
        self, hass: HomeAssistant
    ):
        """A console_id that matches nothing names what is configured."""
        entry1, _ = self._console("entry_1", "Dream Machine")
        entry2, _ = self._console("entry_2", "Cloud Key")

        await async_setup_services(hass)
        with (
            patch.object(
                hass.config_entries, "async_entries", return_value=[entry1, entry2]
            ),
            pytest.raises(ServiceValidationError, match="Cloud Key"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "trigger_alarm",
                {"alarm_id": "t1", "console_id": "Nonexistent"},
                blocking=True,
            )

        await async_unload_services(hass)


class TestClientMacRouting:
    """Guest authorisation is commonly targeted by MAC, not by client id."""

    def test_entry_matches_client_by_mac_address(self):
        """A MAC belonging to a site's client record counts as ownership."""
        entry = MagicMock()
        entry.runtime_data = MagicMock()
        entry.runtime_data.coordinator.data = {
            "clients": {
                "site1": {"abc123": {"id": "abc123", "macAddress": "AA:BB:CC:DD:EE:FF"}}
            }
        }

        assert _entry_has_client(entry, "site1", "aa:bb:cc:dd:ee:ff") is True
        assert _entry_has_client(entry, "site1", "AA-BB-CC-DD-EE-FF") is True
        assert _entry_has_client(entry, "site1", "abc123") is True
        assert _entry_has_client(entry, "site1", "11:22:33:44:55:66") is False

    @staticmethod
    def _console(entry_id, site_id, clients):
        coord = MagicMock()
        coord.protect_client = None
        # No "sites" collection: site filtering stays permissive and keeps
        # both consoles, so the client branch is what has to decide.
        coord.data = {
            "devices": {site_id: {}},
            "clients": {site_id: clients},
        }
        coord.config_coordinator.async_authorize_guest = AsyncMock()
        entry = MagicMock()
        entry.entry_id = entry_id
        entry.title = entry_id
        entry.runtime_data = MagicMock()
        entry.runtime_data.coordinator = coord
        return entry, coord

    async def test_client_on_two_consoles_is_ambiguous(self, hass: HomeAssistant):
        """A MAC seen on both consoles must raise, not silently pick console 1."""
        mac = "AA:BB:CC:DD:EE:FF"
        entry1, coord1 = self._console(
            "entry_1", "siteX", {"c1": {"id": "c1", "macAddress": mac}}
        )
        entry2, coord2 = self._console(
            "entry_2", "siteX", {"c2": {"id": "c2", "macAddress": mac}}
        )

        await async_setup_services(hass)
        with (
            patch.object(
                hass.config_entries, "async_entries", return_value=[entry1, entry2]
            ),
            pytest.raises(ServiceValidationError, match="ambiguous"),
        ):
            await hass.services.async_call(
                DOMAIN,
                "authorize_guest",
                {"site_id": "siteX", "client_id": "aabbccddeeff"},
                blocking=True,
            )

        coord1.config_coordinator.async_authorize_guest.assert_not_called()
        coord2.config_coordinator.async_authorize_guest.assert_not_called()
        await async_unload_services(hass)

    async def test_client_on_one_console_routes_there(self, hass: HomeAssistant):
        """The same MAC on only one console routes to that console."""
        entry1, coord1 = self._console(
            "entry_1", "siteX", {"c1": {"id": "c1", "macAddress": "11:22:33:44:55:66"}}
        )
        entry2, coord2 = self._console(
            "entry_2", "siteX", {"c2": {"id": "c2", "macAddress": "AA:BB:CC:DD:EE:FF"}}
        )

        await async_setup_services(hass)
        with patch.object(
            hass.config_entries, "async_entries", return_value=[entry1, entry2]
        ):
            await hass.services.async_call(
                DOMAIN,
                "authorize_guest",
                {"site_id": "siteX", "client_id": "aa-bb-cc-dd-ee-ff"},
                blocking=True,
            )

        coord2.config_coordinator.async_authorize_guest.assert_called_once()
        coord1.config_coordinator.async_authorize_guest.assert_not_called()
        await async_unload_services(hass)


class TestConsoleRoutingEdgeCases:
    """Additional unit tests covering edge cases in console routing helpers."""

    def test_coord_data_and_section_edge_cases(self):
        """Test _coord_data and _coord_section edge cases."""
        assert _coord_data(None) is None

        entry = MagicMock()
        entry.runtime_data = None
        assert _coord_data(entry) is None
        assert _coord_section(entry, "devices") is None

        entry.runtime_data = MagicMock(coordinator=None)
        assert _coord_data(entry) is None

        entry.runtime_data.coordinator = MagicMock(data="not-a-dict")
        assert _coord_data(entry) is None

        entry.runtime_data.coordinator.data = {
            "devices": "not-a-dict",
            "sites": {"s1": {}},
        }
        assert _coord_section(entry, "devices") is None
        assert _coord_section(entry, "sites") == {"s1": {}}

    def test_entry_has_and_owns_device_edge_cases(self):
        """Test device ownership checks under abnormal or non-dict coordinator data."""
        entry = MagicMock()
        entry.runtime_data = None
        assert _entry_has_device(entry, "s1", "d1") is True
        assert _entry_owns_device(entry, "s1", "d1") is False

        entry.runtime_data = MagicMock(
            coordinator=MagicMock(data={"devices": "invalid"})
        )
        assert _entry_has_device(entry, "s1", "d1") is True
        assert _entry_owns_device(entry, "s1", "d1") is False

        entry.runtime_data.coordinator.data = {"devices": {"s1": "invalid"}}
        assert _entry_has_device(entry, "s1", "d1") is True

        entry.runtime_data.coordinator.data = {"devices": {"s1": {"d1": {}}}}
        assert _entry_owns_device(entry, None, "d1") is True
        assert _entry_owns_device(entry, None, "d2") is False

    def test_entry_has_and_owns_client_edge_cases(self):
        """Test client ownership checks under abnormal coordinator data."""
        entry = MagicMock()
        entry.runtime_data = None
        assert _entry_has_client(entry, "s1", "c1") is True
        assert _entry_owns_client(entry, "s1", "c1") is False

        entry.runtime_data = MagicMock(
            coordinator=MagicMock(data={"clients": "invalid"})
        )
        assert _entry_has_client(entry, "s1", "c1") is True
        assert _entry_owns_client(entry, "s1", "c1") is False

        entry.runtime_data.coordinator.data = {"clients": {"s1": "invalid"}}
        assert _entry_has_client(entry, "s1", "c1") is True

        entry.runtime_data.coordinator.data = {"clients": {"s1": {"c1": {"id": "c1"}}}}
        assert _entry_has_client(entry, None, "c1") is True
        assert _entry_owns_client(entry, None, "c1") is True
        assert _entry_owns_client(entry, None, "nonexistent") is False

    def test_mac_key_and_client_records_match_edge_cases(self):
        """Test _mac_key and _client_records_match edge cases."""
        assert _mac_key(123) is None
        assert _mac_key("") is None
        assert _mac_key("   ") is None
        assert _mac_key("AA:BB:CC:DD:EE:FF") == "aabbccddeeff"

        records = {"c1": "not-a-dict", "c2": {"mac": "11:22:33:44:55:66"}}
        assert _client_records_match(records, "c1") is True
        assert _client_records_match(records, "unknown_client") is False
        assert _client_records_match(records, "11-22-33-44-55-66") is True

    def test_protect_owns_resource_edge_cases(self):
        """Test _protect_owns_resource edge cases."""
        entry = MagicMock()
        entry.runtime_data = None
        assert _protect_owns_resource(entry, "cameras", "cam1") is None

        entry.runtime_data = MagicMock(
            coordinator=MagicMock(data={"protect": "invalid"})
        )
        assert _protect_owns_resource(entry, "cameras", "cam1") is None

        entry.runtime_data.coordinator.data = {"protect": {"cameras": "invalid"}}
        assert _protect_owns_resource(entry, "cameras", "cam1") is None

    def test_select_console_duplicate_matches(self):
        """Reject a console selector matching multiple entries."""
        e1 = MagicMock(entry_id="id1", title="Console")
        e2 = MagicMock(entry_id="id2", title="Console")
        with pytest.raises(ServiceValidationError, match="matches more than one"):
            _select_console([e1, e2], "Console", "Protect console")

    async def test_network_resource_device_id_cross_console_mismatch(
        self, hass: HomeAssistant
    ):
        """A device native ID that belongs to a different console than the
        requested site_id must raise instead of being silently routed."""
        c1 = MagicMock()
        e1 = MagicMock(
            entry_id="e1", title="E1", runtime_data=MagicMock(coordinator=c1)
        )
        e1.runtime_data.coordinator.data = {
            "sites": {"s1": {}},
            "devices": {"s1": {"d1": {}}},
        }

        c2 = MagicMock()
        e2 = MagicMock(
            entry_id="e2", title="E2", runtime_data=MagicMock(coordinator=c2)
        )
        e2.runtime_data.coordinator.data = {
            "sites": {"s2": {}},
            "devices": {"s2": {"d2": {}}},
        }

        with (
            patch.object(hass.config_entries, "async_entries", return_value=[e1, e2]),
            pytest.raises(
                ServiceValidationError, match="belongs to a different console than site"
            ),
        ):
            _get_coordinator_for_network_resource(hass, site_id="s2", device_id="d1")

    async def test_network_resource_unloaded_site_and_device(self, hass: HomeAssistant):
        """Test warm-up state where consoles haven't loaded site or device data."""
        c1 = MagicMock()
        e1 = MagicMock(
            entry_id="e1", title="E1", runtime_data=MagicMock(coordinator=c1)
        )
        e1.runtime_data.coordinator.data = {}

        with patch.object(hass.config_entries, "async_entries", return_value=[e1]):
            coord, _ = _get_coordinator_for_network_resource(
                hass, site_id="unknown_site"
            )
            assert coord is c1

            coord2, _ = _get_coordinator_for_network_resource(
                hass, device_id="unknown_dev"
            )
            assert coord2 is c1

    async def test_network_resource_explicit_device_and_client_ownership(
        self, hass: HomeAssistant
    ):
        """Select the explicit device or client owner among candidate entries."""
        c1 = MagicMock()
        e1 = MagicMock(
            entry_id="e1", title="E1", runtime_data=MagicMock(coordinator=c1)
        )
        e1.runtime_data.coordinator.data = {
            "sites": {"s1": {}},
            "devices": {"s1": {"d_shared": {}}},
            "clients": {"s1": {"c_shared": {"id": "c_shared"}}},
        }

        c2 = MagicMock()
        e2 = MagicMock(
            entry_id="e2", title="E2", runtime_data=MagicMock(coordinator=c2)
        )
        e2.runtime_data.coordinator.data = {"sites": {"s1": {}}}

        with patch.object(hass.config_entries, "async_entries", return_value=[e1, e2]):
            coord, _ = _get_coordinator_for_network_resource(
                hass, site_id="s1", device_id="d_shared"
            )
            assert coord is c1

            coord_client, _ = _get_coordinator_for_network_resource(
                hass, site_id="s1", client_id="c_shared"
            )
            assert coord_client is c1

        # One console has site s1 explicitly, while e2 does not have s1 in sites
        e2.runtime_data.coordinator.data = {"sites": {"s2": {}}}
        with patch.object(hass.config_entries, "async_entries", return_value=[e1, e2]):
            coord_site, _ = _get_coordinator_for_network_resource(hass, site_id="s1")
            assert coord_site is c1

    async def test_protect_resource_ownership_resolution_and_no_target_ambiguous(
        self, hass: HomeAssistant
    ):
        """Protect resolver picks the console that owns the resource, and
        raises when no resource_type is given and multiple consoles are
        configured (no positive target to route on)."""
        c1 = MagicMock(protect_client=MagicMock())
        e1 = MagicMock(
            entry_id="e1", title="E1", runtime_data=MagicMock(coordinator=c1)
        )
        e1.runtime_data.coordinator.data = {"protect": {"cameras": {"cam1": {}}}}

        c2 = MagicMock(protect_client=MagicMock())
        e2 = MagicMock(
            entry_id="e2", title="E2", runtime_data=MagicMock(coordinator=c2)
        )
        e2.runtime_data.coordinator.data = {"protect": {"cameras": {"cam2": {}}}}

        with patch.object(hass.config_entries, "async_entries", return_value=[e1, e2]):
            coord, _ = _get_coordinator_for_protect_resource(
                hass, resource_type="camera", resource_id="cam1"
            )
            assert coord is c1

            with pytest.raises(
                ServiceValidationError, match="carries no target to route on"
            ):
                _get_coordinator_for_protect_resource(hass, resource_id="cam1")

    async def test_protect_resource_unloaded_data_and_unresolved_candidates(
        self, hass: HomeAssistant
    ):
        """Handle unloaded Protect data and unresolved candidates."""
        c1 = MagicMock(protect_client=MagicMock())
        e1 = MagicMock(
            entry_id="e1", title="E1", runtime_data=MagicMock(coordinator=c1)
        )
        e1.runtime_data.coordinator.data = {}

        with patch.object(hass.config_entries, "async_entries", return_value=[e1]):
            coord, _ = _get_coordinator_for_protect_resource(
                hass, resource_type="camera", resource_id="cam_new"
            )
            assert coord is c1

        c2 = MagicMock(protect_client=MagicMock())
        e2 = MagicMock(
            entry_id="e2", title="E2", runtime_data=MagicMock(coordinator=c2)
        )
        e2.runtime_data.coordinator.data = {}

        with (
            patch.object(hass.config_entries, "async_entries", return_value=[e1, e2]),
            pytest.raises(
                ServiceValidationError, match="Cannot tell which Protect console owns"
            ),
        ):
            _get_coordinator_for_protect_resource(
                hass, resource_type="camera", resource_id="cam_unknown"
            )

    async def test_protect_secondary_resource_unloaded_fallback(
        self, hass: HomeAssistant
    ):
        """Allow secondary resources absent from the Protect cache."""
        c1 = MagicMock(protect_client=MagicMock())
        e1 = MagicMock(
            entry_id="e1", title="E1", runtime_data=MagicMock(coordinator=c1)
        )
        e1.runtime_data.coordinator.data = {
            "protect": {"chimes": {"chime1": {}}, "cameras": {}}
        }

        with patch.object(hass.config_entries, "async_entries", return_value=[e1]):
            coord, _ = _get_coordinator_for_protect_resource(
                hass,
                resource_type="chime",
                resource_id="chime1",
                secondary_resource_type="camera",
                secondary_resource_id="cam_brand_new",
            )
            assert coord is c1

    async def test_service_handlers_with_camera_id_and_ignored_guest_options(
        self, hass: HomeAssistant
    ):
        """Test service calls passing optional camera_id and ignored guest options."""
        coord = MagicMock(protect_client=MagicMock())
        _protect(coord)
        coord.config_coordinator.async_authorize_guest = AsyncMock()
        coord.data = {
            "sites": {"s1": {}},
            "clients": {"s1": {"c1": {"id": "c1"}}},
            "protect": {"chimes": {"chime1": {}}, "cameras": {"cam1": {}}},
        }
        entry = MagicMock(
            entry_id="e1", title="E1", runtime_data=MagicMock(coordinator=coord)
        )

        await async_setup_services(hass)
        with patch.object(hass.config_entries, "async_entries", return_value=[entry]):
            await hass.services.async_call(
                DOMAIN,
                "set_chime_volume",
                {"chime_id": "chime1", "volume": 50, "camera_id": "cam1"},
                blocking=True,
            )
            _protect(coord).async_set_chime_volume.assert_called_once_with("chime1", 50)

            await hass.services.async_call(
                DOMAIN,
                "set_chime_ringtone",
                {"chime_id": "chime1", "ringtone_id": "tone-1", "camera_id": "cam1"},
                blocking=True,
            )
            _protect(coord).async_set_chime_ringtone.assert_called_once_with(
                "chime1", "tone-1"
            )

            await hass.services.async_call(
                DOMAIN,
                "set_chime_repeat_times",
                {"chime_id": "chime1", "repeat_times": 3, "camera_id": "cam1"},
                blocking=True,
            )
            _protect(coord).async_set_chime_repeat.assert_called_once_with("chime1", 3)

            await hass.services.async_call(
                DOMAIN,
                "authorize_guest",
                {"site_id": "s1", "client_id": "c1", "duration_minutes": 60},
                blocking=True,
            )
            coord.config_coordinator.async_authorize_guest.assert_called_once_with(
                "s1", "c1"
            )

        await async_unload_services(hass)

    def test_client_records_match_non_mac_chars(self):
        """Test _client_records_match when client_id has no hex characters."""
        records = {"c1": {"mac": "11:22:33:44:55:66"}}
        assert _client_records_match(records, ":::---") is False

    async def test_protect_resource_single_explicit_match(self, hass: HomeAssistant):
        """Select the console explicitly owning the Protect resource."""
        c1 = MagicMock(protect_client=MagicMock())
        e1 = MagicMock(
            entry_id="e1", title="E1", runtime_data=MagicMock(coordinator=c1)
        )
        e1.runtime_data.coordinator.data = {"protect": {"cameras": {"cam1": {}}}}

        c2 = MagicMock(protect_client=MagicMock())
        e2 = MagicMock(
            entry_id="e2", title="E2", runtime_data=MagicMock(coordinator=c2)
        )
        e2.runtime_data.coordinator.data = {}  # Unloaded Protect data

        with patch.object(hass.config_entries, "async_entries", return_value=[e1, e2]):
            coord, _ = _get_coordinator_for_protect_resource(
                hass, resource_type="camera", resource_id="cam1"
            )
            assert coord is c1

    async def test_network_resource_multiple_candidate_single_explicit_site(
        self, hass: HomeAssistant
    ):
        """Select the explicit site owner among candidate consoles."""
        c1 = MagicMock()
        e1 = MagicMock(
            entry_id="e1", title="E1", runtime_data=MagicMock(coordinator=c1)
        )
        e1.runtime_data.coordinator.data = {"sites": {"s1": {}}}

        c2 = MagicMock()
        e2 = MagicMock(
            entry_id="e2", title="E2", runtime_data=MagicMock(coordinator=c2)
        )
        e2.runtime_data.coordinator.data = {}  # Unloaded site data

        with patch.object(hass.config_entries, "async_entries", return_value=[e1, e2]):
            coord, _ = _get_coordinator_for_network_resource(hass, site_id="s1")
            assert coord is c1

    def test_entry_owns_device_site_scoped_and_cross_site_fallback(self):
        """A device recorded under a different site than requested is still
        found by the full-collection ownership scan, not just the site-scoped
        lookup."""
        entry = MagicMock()
        entry.runtime_data = MagicMock(
            coordinator=MagicMock(
                data={"devices": {"s1": {"d1": {}}, "s2": {"d2": {}}}}
            )
        )
        # site_devices is a dict and contains the id: short-circuit True.
        assert _entry_owns_device(entry, "s1", "d1") is True
        # site_devices is a dict but lacks the id: falls through to the
        # cross-site scan, which finds it under "s2".
        assert _entry_owns_device(entry, "s1", "d2") is True

    def test_entry_owns_client_site_scoped_and_cross_site_fallback(self):
        """A client recorded under a different site than requested is still
        found by the full-collection ownership scan, not just the site-scoped
        lookup."""
        entry = MagicMock()
        entry.runtime_data = MagicMock(
            coordinator=MagicMock(
                data={
                    "clients": {
                        "s1": {"c1": {"id": "c1"}},
                        "s2": {"c2": {"id": "c2"}},
                    }
                }
            )
        )
        # site_clients is a dict and matches: short-circuit True.
        assert _entry_owns_client(entry, "s1", "c1") is True
        # site_clients is a dict but doesn't match: falls through to the
        # cross-site scan, which finds it under "s2".
        assert _entry_owns_client(entry, "s1", "c2") is True

    async def test_network_resource_device_id_resolves_via_ownership(
        self, hass: HomeAssistant
    ):
        """A native device_id with no site_id given resolves via
        device-ownership filtering alone."""
        c1 = MagicMock()
        e1 = MagicMock(
            entry_id="e1", title="E1", runtime_data=MagicMock(coordinator=c1)
        )
        e1.runtime_data.coordinator.data = {"devices": {"s1": {"d1": {}}}}

        c2 = MagicMock()
        e2 = MagicMock(
            entry_id="e2", title="E2", runtime_data=MagicMock(coordinator=c2)
        )
        e2.runtime_data.coordinator.data = {"devices": {"s2": {"d2": {}}}}

        with patch.object(hass.config_entries, "async_entries", return_value=[e1, e2]):
            coord, _ = _get_coordinator_for_network_resource(hass, device_id="d1")
            assert coord is c1

    async def test_network_resource_no_ownership_signal_raises_ambiguous(
        self, hass: HomeAssistant
    ):
        """When neither client nor site data can positively pick a console,
        the resolver raises instead of silently guessing the first entry."""
        c1 = MagicMock()
        e1 = MagicMock(
            entry_id="e1", title="E1", runtime_data=MagicMock(coordinator=c1)
        )
        e1.runtime_data.coordinator.data = {}

        c2 = MagicMock()
        e2 = MagicMock(
            entry_id="e2", title="E2", runtime_data=MagicMock(coordinator=c2)
        )
        e2.runtime_data.coordinator.data = {}

        with (
            patch.object(hass.config_entries, "async_entries", return_value=[e1, e2]),
            pytest.raises(ServiceValidationError, match="target is ambiguous"),
        ):
            _get_coordinator_for_network_resource(hass, client_id="mystery_client")

    async def test_protect_resource_resolves_via_ownership_with_multiple_candidates(
        self, hass: HomeAssistant
    ):
        """Protect resolver picks the console that owns the resource when
        multiple Protect consoles are configured and neither uniquely-owned
        camera collides with the other."""
        c1 = MagicMock(protect_client=MagicMock())
        e1 = MagicMock(
            entry_id="e1", title="E1", runtime_data=MagicMock(coordinator=c1)
        )
        e1.runtime_data.coordinator.data = {"protect": {"cameras": {"cam1": {}}}}

        c2 = MagicMock(protect_client=MagicMock())
        e2 = MagicMock(
            entry_id="e2", title="E2", runtime_data=MagicMock(coordinator=c2)
        )
        e2.runtime_data.coordinator.data = {"protect": {"cameras": {"cam2": {}}}}

        with patch.object(hass.config_entries, "async_entries", return_value=[e1, e2]):
            coord, _ = _get_coordinator_for_protect_resource(
                hass, resource_type="camera", resource_id="cam1"
            )
            assert coord is c1


class TestServiceComprehensiveFullCoverage:
    """Cover target resolution, errors, and coordinator routing."""

    def test_extract_target_id_variations(self):
        """Test _extract_target_id with various payloads."""

        # Empty dict
        call = MagicMock(service="restart_device", data={})
        assert _extract_target_id(call, "device_id") is None

        # Whitespace
        call = MagicMock(service="restart_device", data={"device_id": "   "})
        assert _extract_target_id(call, "device_id") is None

        # Valid stripped string
        call = MagicMock(service="restart_device", data={"device_id": "  dev1  "})
        assert _extract_target_id(call, "device_id") == "dev1"

        # Target dict with primary field
        call = MagicMock(
            service="restart_device", data={"target": {"device_id": "dev_target"}}
        )
        assert _extract_target_id(call, "device_id") == "dev_target"

        # Target dict with entity_id
        call = MagicMock(
            service="restart_device", data={"target": {"entity_id": "switch.dev"}}
        )
        assert _extract_target_id(call, "device_id") == "switch.dev"

        # Target dict with device_id
        call = MagicMock(
            service="restart_device", data={"target": {"device_id": "d123"}}
        )
        assert _extract_target_id(call, "camera_id") == "d123"

        # Top-level entity_id
        call = MagicMock(service="restart_device", data={"entity_id": "switch.dev2"})
        assert _extract_target_id(call, "device_id") == "switch.dev2"

        # Top-level device_id when looking for camera_id
        call = MagicMock(service="set_hdr_mode", data={"device_id": "cam_dev"})
        assert _extract_target_id(call, "camera_id") == "cam_dev"

        # List with single item
        call = MagicMock(service="restart_device", data={"device_id": ["dev_single"]})
        assert _extract_target_id(call, "device_id") == "dev_single"

        # Empty list raises
        call = MagicMock(service="restart_device", data={"device_id": []})
        with pytest.raises(
            ServiceValidationError, match="At least one target must be specified"
        ):
            _extract_target_id(call, "device_id")

        # Multiple items list raises
        call = MagicMock(service="restart_device", data={"device_id": ["dev1", "dev2"]})
        with pytest.raises(
            ServiceValidationError,
            match="Multiple targets specified for restart_device",
        ):
            _extract_target_id(call, "device_id")

        # Non-string, non-list
        call = MagicMock(service="restart_device", data={"device_id": 12345})
        assert _extract_target_id(call, "device_id") is None

    async def test_resolve_network_device_id_branches(self, hass: HomeAssistant):
        """Test _resolve_network_device_id helper branches."""

        dev_reg = dr.async_get(hass)
        ent_reg = er.async_get(hass)

        entry1 = MockConfigEntry(domain=DOMAIN, entry_id="entry1", title="Console 1")
        entry1.add_to_hass(hass)
        entry1.runtime_data = MagicMock(
            coordinator=MagicMock(
                data={"devices": {"site1": {"d1": {}}, "site_ident": {"dev_ident": {}}}}
            )
        )

        # 1. Target entity not found in registry (has ".")
        with pytest.raises(
            ServiceValidationError,
            match=r"Target entity 'switch\.missing' not found in entity registry",
        ):
            _resolve_network_device_id(hass, "switch.missing", None, [entry1])

        # 2. Entity found but platform != DOMAIN
        other_ent = ent_reg.async_get_or_create("switch", "other_domain", "other_uid")
        with pytest.raises(
            ServiceValidationError, match="is not a UniFi Insights entity"
        ):
            _resolve_network_device_id(hass, other_ent.entity_id, None, [entry1])

        # 3. Entity found with DOMAIN platform and device_id matching dev_reg, unique_id
        # matches devices
        ha_dev = dev_reg.async_get_or_create(
            config_entry_id=entry1.entry_id,
            identifiers={(DOMAIN, "site1_d1")},
        )
        ent_with_dev = ent_reg.async_get_or_create(
            "switch",
            DOMAIN,
            "site1_d1_status",
            config_entry=entry1,
            device_id=ha_dev.id,
        )
        d, s, ent = _resolve_network_device_id(
            hass, ent_with_dev.entity_id, None, [entry1]
        )
        assert d == "d1"
        assert s == "site1"
        assert ent == entry1

        # 4. Entity with unique_id split len >= 2 fallback
        ent_parts = ent_reg.async_get_or_create(
            "switch",
            DOMAIN,
            "site2_d2",
            config_entry=entry1,
        )
        d, s, ent = _resolve_network_device_id(
            hass, ent_parts.entity_id, None, [entry1]
        )
        assert d == "d2"
        assert s == "site2"

        # 5. Device target: not in matching config entries and no domain identifier
        other_entry = MockConfigEntry(domain="other_domain", entry_id="other_entry")
        other_entry.add_to_hass(hass)
        other_dev = dev_reg.async_get_or_create(
            config_entry_id="other_entry",
            identifiers={("other_domain", "other_id")},
        )
        with pytest.raises(
            ServiceValidationError, match="is not a UniFi Insights device"
        ):
            _resolve_network_device_id(hass, other_dev.id, None, [entry1])

        # 6. Device target with protect_ and client_ prefixes and other domain (tests
        # lines 351 and 354 continue)
        skipped_dev = dev_reg.async_get_or_create(
            config_entry_id=entry1.entry_id,
            identifiers={
                ("other_domain", "other_1"),
                (DOMAIN, "protect_camera_1"),
                (DOMAIN, "client_mac_1"),
            },
        )
        with pytest.raises(ServiceValidationError, match="Could not resolve target"):
            _resolve_network_device_id(hass, skipped_dev.id, None, [entry1])

        # 7. Device target with site identifier matching devices_by_site (tests line
        # 354: return d, s, entry)
        ident_dev = dev_reg.async_get_or_create(
            config_entry_id=entry1.entry_id,
            identifiers={(DOMAIN, "site_ident_dev_ident")},
        )
        d, s, ent = _resolve_network_device_id(hass, ident_dev.id, None, [entry1])
        assert d == "dev_ident"
        assert s == "site_ident"
        assert ent == entry1

        # 8. Device target with site_id prefix matching site_id
        site_dev = dev_reg.async_get_or_create(
            config_entry_id=entry1.entry_id,
            identifiers={(DOMAIN, "customsite_customdev")},
        )
        d, s, ent = _resolve_network_device_id(
            hass, site_dev.id, "customsite", [entry1]
        )
        assert d == "customdev"
        assert s == "customsite"

        # 9. Device target with rpartition fallback
        d, s, ent = _resolve_network_device_id(hass, site_dev.id, None, [entry1])
        assert d == "customdev"
        assert s == "customsite"

        # 10. Raw native ID without registry entry
        d, s, ent = _resolve_network_device_id(hass, "native_d", "s1", [entry1])
        assert d == "native_d"
        assert s == "s1"
        assert ent is None

    async def test_resolve_protect_resource_id_branches(self, hass: HomeAssistant):
        """Test _resolve_protect_resource_id helper branches."""

        dev_reg = dr.async_get(hass)
        ent_reg = er.async_get(hass)

        entry1 = MockConfigEntry(
            domain=DOMAIN, entry_id="pentry1", title="Protect Console 1"
        )
        entry1.add_to_hass(hass)
        entry1.runtime_data = MagicMock(
            coordinator=MagicMock(
                data={"protect": {"cameras": {"cam1": {}}, "lights": {"light1": {}}}}
            )
        )

        # 1. Target entity not found in registry (has ".")
        with pytest.raises(
            ServiceValidationError,
            match=r"Target entity 'camera\.missing' not found in entity registry",
        ):
            _resolve_protect_resource_id(hass, "camera", "camera.missing", [entry1])

        # 2. Entity found but platform != DOMAIN
        other_ent = ent_reg.async_get_or_create(
            "camera", "other_domain", "other_cam_uid"
        )
        with pytest.raises(
            ServiceValidationError, match="is not a UniFi Protect camera"
        ):
            _resolve_protect_resource_id(hass, "camera", other_ent.entity_id, [entry1])

        # 3. Entity found with DOMAIN platform and device_id matching dev_reg, unique_id
        # matches protect data
        ha_dev = dev_reg.async_get_or_create(
            config_entry_id=entry1.entry_id,
            identifiers={(DOMAIN, "protect_camera_cam1")},
        )
        cam_ent = ent_reg.async_get_or_create(
            "camera",
            DOMAIN,
            f"{DOMAIN}_camera_cam1",
            config_entry=entry1,
            device_id=ha_dev.id,
        )
        res_id, ent = _resolve_protect_resource_id(
            hass, "camera", cam_ent.entity_id, [entry1]
        )
        assert res_id == "cam1"
        assert ent == entry1

        # 4. Entity with unique_id split fallback (collection not matching)
        cam_ent_fallback = ent_reg.async_get_or_create(
            "camera",
            DOMAIN,
            f"{DOMAIN}_camera_camfallback_extra",
            config_entry=entry1,
        )
        res_id, ent = _resolve_protect_resource_id(
            hass, "camera", cam_ent_fallback.entity_id, [entry1]
        )
        assert res_id == "camfallback"

        # 5. Device target: not in matching config entries and no domain identifier
        other_entry = MockConfigEntry(domain="other_domain", entry_id="pother_entry")
        other_entry.add_to_hass(hass)
        other_dev = dev_reg.async_get_or_create(
            config_entry_id="pother_entry",
            identifiers={("other_domain", "other_protect_dev")},
        )
        with pytest.raises(
            ServiceValidationError, match="is not a UniFi Protect camera"
        ):
            _resolve_protect_resource_id(hass, "camera", other_dev.id, [entry1])

        # 6. Device target with identifier from other domain (tests line 476: if domain
        # != DOMAIN: continue)
        other_domain_dev = dev_reg.async_get_or_create(
            config_entry_id=entry1.entry_id,
            identifiers={
                ("other_domain", "other_id"),
                (DOMAIN, "unifi_device_without_prefix"),
            },
        )
        ent_reg.async_get_or_create(
            "camera",
            DOMAIN,
            f"{DOMAIN}_camera_camfromdevice_sub",
            config_entry=entry1,
            device_id=other_domain_dev.id,
        )
        res_id, ent = _resolve_protect_resource_id(
            hass, "camera", other_domain_dev.id, [entry1]
        )
        assert res_id == "camfromdevice"

        # 7. Device target with protect_ prefix of wrong resource type
        wrong_type_dev = dev_reg.async_get_or_create(
            config_entry_id=entry1.entry_id,
            identifiers={(DOMAIN, "protect_light_l1")},
        )
        with pytest.raises(
            ServiceValidationError, match=r"Target '.*' is a light, not a camera"
        ):
            _resolve_protect_resource_id(hass, "camera", wrong_type_dev.id, [entry1])

        # 8. Camera fallback: device has no protect_camera identifier but entity in
        # registry does
        dev_no_ident = dev_reg.async_get_or_create(
            config_entry_id=entry1.entry_id,
            identifiers={(DOMAIN, "unifi_device_without_prefix")},
        )
        ent_reg.async_get_or_create(
            "camera",
            DOMAIN,
            f"{DOMAIN}_camera_camfromdevice_sub",
            config_entry=entry1,
            device_id=dev_no_ident.id,
        )
        res_id, ent = _resolve_protect_resource_id(
            hass, "camera", dev_no_ident.id, [entry1]
        )
        assert res_id == "camfromdevice"

        # 9. Device target cannot be resolved
        unresolvable_dev = dev_reg.async_get_or_create(
            config_entry_id=entry1.entry_id,
            identifiers={(DOMAIN, "custom_unknown_identifier")},
        )
        with pytest.raises(
            ServiceValidationError,
            match=r"Could not resolve target '.*' to a UniFi Protect light",
        ):
            _resolve_protect_resource_id(hass, "light", unresolvable_dev.id, [entry1])

        # 10. Raw native ID without registry entry
        res_id, ent = _resolve_protect_resource_id(
            hass, "camera", "native_cam_id", [entry1]
        )
        assert res_id == "native_cam_id"
        assert ent is None

    async def test_resolve_network_client_id_branches(self, hass: HomeAssistant):
        """Test _resolve_network_client_id helper branches."""

        dev_reg = dr.async_get(hass)
        ent_reg = er.async_get(hass)

        entry1 = MockConfigEntry(domain=DOMAIN, entry_id="centry1", title="Console 1")
        entry1.add_to_hass(hass)
        entry1.runtime_data = MagicMock(coordinator=MagicMock(data={}))

        # 1. Target entity not found in registry (has ".")
        with pytest.raises(
            ServiceValidationError,
            match=r"device_tracker\.missing.*not found in entity registry",
        ):
            _resolve_network_client_id(hass, "device_tracker.missing", [entry1])

        # 2. Entity found but platform != DOMAIN
        other_ent = ent_reg.async_get_or_create(
            "device_tracker", "other_domain", "other_tracker"
        )
        with pytest.raises(
            ServiceValidationError, match="is not a UniFi Insights entity"
        ):
            _resolve_network_client_id(hass, other_ent.entity_id, [entry1])

        # 3. Entity found with MAC in unique_id and device_id linked to dev_reg
        ha_dev = dev_reg.async_get_or_create(
            config_entry_id=entry1.entry_id,
            identifiers={(DOMAIN, "client_00:11:22:33:44:55")},
        )
        tracker_ent = ent_reg.async_get_or_create(
            "device_tracker",
            DOMAIN,
            f"{DOMAIN}_00-11-22-33-44-55",
            config_entry=entry1,
            device_id=ha_dev.id,
        )
        c_id, ent = _resolve_network_client_id(hass, tracker_ent.entity_id, [entry1])
        assert c_id == "00:11:22:33:44:55"
        assert ent == entry1

        # 4. Entity without MAC but split("_") >= 2
        tracker_nonmac = ent_reg.async_get_or_create(
            "device_tracker",
            DOMAIN,
            "prefix_client123",
            config_entry=entry1,
        )
        c_id, ent = _resolve_network_client_id(hass, tracker_nonmac.entity_id, [entry1])
        assert c_id == "client123"

        # 5. Device target: not in matching config entries and no domain identifier
        other_entry = MockConfigEntry(domain="other_domain", entry_id="cother_entry")
        other_entry.add_to_hass(hass)
        other_dev = dev_reg.async_get_or_create(
            config_entry_id="cother_entry",
            identifiers={("other_domain", "other_client_dev")},
        )
        with pytest.raises(
            ServiceValidationError, match="is not a UniFi Insights device"
        ):
            _resolve_network_client_id(hass, other_dev.id, [entry1])

        # 6. Device target with client_ prefix
        c_id, ent = _resolve_network_client_id(hass, ha_dev.id, [entry1])
        assert c_id == "00:11:22:33:44:55"
        assert ent == entry1

        # 7. Raw client ID without registry entry
        c_id, ent = _resolve_network_client_id(hass, "raw_client_mac", [entry1])
        assert c_id == "raw_client_mac"
        assert ent is None

    async def test_coordinator_routing_edge_branches(self, hass: HomeAssistant):
        """Test _get_coordinator_for_network_resource and Protect edge branches."""

        dev_reg = dr.async_get(hass)

        coord1 = MagicMock()
        coord1.data = {
            "sites": {"site1": {}},
            "devices": {"site1": {"d1": {}}},
            "clients": {"site1": {"00:11:22:33:44:55": {"mac": "00:11:22:33:44:55"}}},
        }
        entry1 = MockConfigEntry(domain=DOMAIN, entry_id="rentry1", title="Console 1")
        entry1.add_to_hass(hass)
        entry1.runtime_data = MagicMock(coordinator=coord1)

        coord2 = MagicMock()
        coord2.data = {"sites": {"site2": {}}, "devices": {"site2": {"d2": {}}}}
        entry2 = MockConfigEntry(domain=DOMAIN, entry_id="rentry2", title="Console 2")
        entry2.add_to_hass(hass)
        entry2.runtime_data = MagicMock(coordinator=coord2)

        # Device belongs to different console than site_id
        dev1 = dev_reg.async_get_or_create(
            config_entry_id=entry1.entry_id,
            identifiers={(DOMAIN, "site1_d1")},
        )
        with patch.object(
            hass.config_entries, "async_entries", return_value=[entry1, entry2]
        ):
            with pytest.raises(
                ServiceValidationError,
                match="belongs to a different console than site 'site2'",
            ):
                _get_coordinator_for_network_resource(
                    hass, site_id="site2", device_id=dev1.id
                )

            # Native device in cross_console_entries with site_id mismatch
            with pytest.raises(
                ServiceValidationError,
                match="Device 'd1' belongs to a different console than site 'site2'",
            ):
                _get_coordinator_for_network_resource(
                    hass, site_id="site2", device_id="d1"
                )

            # Line 611: site_id is None and resolved_site is not None
            # Line 668: resolved_reg_entry is not None and resolved_reg_entry in
            # entries_with_device
            c_res, dev_res = _get_coordinator_for_network_resource(
                hass, site_id=None, device_id=dev1.id
            )
            assert c_res == coord1
            assert dev_res == "d1"

            # Line 694: Multiple candidate entries matching site, resolved_reg_entry
            # picks coordinator
            entry1_dup = MockConfigEntry(
                domain=DOMAIN, entry_id="entry1_dup", title="Console 1 Dup"
            )
            entry1_dup.add_to_hass(hass)
            entry1_dup.runtime_data = MagicMock(
                coordinator=MagicMock(data={"sites": {"site1": {}}})
            )

            client_dev = dev_reg.async_get_or_create(
                config_entry_id=entry1.entry_id,
                identifiers={(DOMAIN, "client_00:11:22:33:44:55")},
            )
            with patch.object(
                hass.config_entries, "async_entries", return_value=[entry1, entry1_dup]
            ):
                c, cid = _get_coordinator_for_network_resource(
                    hass, site_id="site1", client_id=client_dev.id
                )
                assert c == coord1
                assert cid == "00:11:22:33:44:55"

        # Protect cross-console mismatch (lines 826-830)
        coord_p1 = MagicMock(protect_client=MagicMock())
        coord_p1.data = {"protect": {"cameras": {}}}  # cam1 not loaded in p1
        entry_p1 = MockConfigEntry(domain=DOMAIN, entry_id="rp1", title="Protect 1")
        entry_p1.add_to_hass(hass)
        entry_p1.runtime_data = MagicMock(coordinator=coord_p1)

        coord_p2 = MagicMock(protect_client=MagicMock())
        coord_p2.data = {"protect": {"cameras": {"cam1": {}}}}  # cam1 loaded in p2
        entry_p2 = MockConfigEntry(domain=DOMAIN, entry_id="rp2", title="Protect 2")
        entry_p2.add_to_hass(hass)
        entry_p2.runtime_data = MagicMock(coordinator=coord_p2)

        cam1_dev = dev_reg.async_get_or_create(
            config_entry_id=entry_p1.entry_id,
            identifiers={(DOMAIN, "protect_camera_cam1")},
        )
        with (
            patch.object(
                hass.config_entries, "async_entries", return_value=[entry_p1, entry_p2]
            ),
            pytest.raises(
                ServiceValidationError,
                match="belongs to a different console than the target console",
            ),
        ):
            _get_coordinator_for_protect_resource(
                hass,
                resource_type="camera",
                resource_id=cam1_dev.id,
            )

        # Protect multiple matching entries resolved via resolved_reg_entry (line 834)
        coord_p1.data = {"protect": {"cameras": {"cam1": {}}}}
        coord_p_dup = MagicMock(protect_client=MagicMock())
        coord_p_dup.data = {"protect": {"cameras": {"cam1": {}}}}
        entry_p_dup = MockConfigEntry(
            domain=DOMAIN, entry_id="rp_dup", title="Protect Dup"
        )
        entry_p_dup.add_to_hass(hass)
        entry_p_dup.runtime_data = MagicMock(coordinator=coord_p_dup)
        with patch.object(
            hass.config_entries, "async_entries", return_value=[entry_p1, entry_p_dup]
        ):
            c, cid = _get_coordinator_for_protect_resource(
                hass, resource_type="camera", resource_id=cam1_dev.id
            )
            assert c == coord_p1
            assert cid == "cam1"

    async def test_service_handlers_missing_target_validations(
        self, hass: HomeAssistant
    ):
        """Test that each service handler raises when target/id is missing."""

        await async_setup_services(hass)

        # 1. restart_device missing device_id
        with pytest.raises(
            ServiceValidationError, match="Device ID or target is required"
        ):
            await hass.services.async_call(
                DOMAIN, SERVICE_RESTART_DEVICE, {}, blocking=True
            )

        # 1b. power_cycle_port missing device_id
        with pytest.raises(
            ServiceValidationError, match="Device ID or target is required"
        ):
            await hass.services.async_call(
                DOMAIN, SERVICE_POWER_CYCLE_PORT, {"port_idx": 1}, blocking=True
            )

        # 3. set_hdr_mode missing camera_id (translated, like every Protect
        # action that routes through _get_protect_owner)
        with pytest.raises(
            ServiceValidationError, match="Camera ID or target is required"
        ) as caught:
            await hass.services.async_call(
                DOMAIN, SERVICE_SET_HDR_MODE, {"mode": "auto"}, blocking=True
            )
        assert caught.value.translation_domain == DOMAIN
        assert caught.value.translation_key == "protect_target_required"
        assert caught.value.translation_placeholders == {"resource_type": "camera"}

        # 4. set_video_mode missing camera_id
        with pytest.raises(
            ServiceValidationError, match="Camera ID or target is required"
        ):
            await hass.services.async_call(
                DOMAIN, SERVICE_SET_VIDEO_MODE, {"mode": "high_fps"}, blocking=True
            )

        # 5. set_mic_volume missing camera_id
        with pytest.raises(
            ServiceValidationError, match="Camera ID or target is required"
        ):
            await hass.services.async_call(
                DOMAIN, SERVICE_SET_MIC_VOLUME, {"volume": 50}, blocking=True
            )

        # 6. set_light_mode missing light_id
        with pytest.raises(
            ServiceValidationError, match="Light ID or target is required"
        ):
            await hass.services.async_call(
                DOMAIN, SERVICE_SET_LIGHT_MODE, {"mode": "motion"}, blocking=True
            )

        # 7. set_light_level missing light_id
        with pytest.raises(
            ServiceValidationError, match="Light ID or target is required"
        ):
            await hass.services.async_call(
                DOMAIN, SERVICE_SET_LIGHT_LEVEL, {"level": 3}, blocking=True
            )

        # 8. ptz_move missing camera_id
        with pytest.raises(
            ServiceValidationError, match="Camera ID or target is required"
        ):
            await hass.services.async_call(
                DOMAIN, SERVICE_PTZ_MOVE, {"preset": 1}, blocking=True
            )

        # 9. ptz_patrol missing camera_id
        with pytest.raises(
            ServiceValidationError, match="Camera ID or target is required"
        ):
            await hass.services.async_call(
                DOMAIN, SERVICE_PTZ_PATROL, {"action": "start"}, blocking=True
            )

        # 10. set_chime_volume missing chime_id
        with pytest.raises(
            ServiceValidationError, match="Chime ID or target is required"
        ):
            await hass.services.async_call(
                DOMAIN, SERVICE_SET_CHIME_VOLUME, {"volume": 80}, blocking=True
            )

        # 11. play_chime_ringtone missing chime_id
        with pytest.raises(
            ServiceValidationError, match="Chime ID or target is required"
        ):
            await hass.services.async_call(
                DOMAIN, SERVICE_PLAY_CHIME_RINGTONE, {}, blocking=True
            )

        # 12. set_chime_ringtone missing chime_id
        with pytest.raises(
            ServiceValidationError, match="Chime ID or target is required"
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CHIME_RINGTONE,
                {"ringtone_id": "tone-2"},
                blocking=True,
            )

        # 13. set_chime_repeat_times missing chime_id
        with pytest.raises(
            ServiceValidationError, match="Chime ID or target is required"
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CHIME_REPEAT_TIMES,
                {"repeat_times": 3},
                blocking=True,
            )

        # 14. authorize_guest missing client_id
        with pytest.raises(
            ServiceValidationError, match="Client ID or target is required"
        ):
            await hass.services.async_call(
                DOMAIN, SERVICE_AUTHORIZE_GUEST, {"site_id": "site1"}, blocking=True
            )

        # 15. set_liveview missing viewer_id
        with pytest.raises(
            ServiceValidationError, match="Viewer ID or target is required"
        ):
            await hass.services.async_call(
                DOMAIN, SERVICE_SET_LIVEVIEW, {"liveview_id": "lv1"}, blocking=True
            )

        # Chime services with camera_id as list
        coord = MagicMock(protect_client=MagicMock())
        _protect(coord)
        entry = MagicMock(
            entry_id="entry_p",
            title="Protect",
            runtime_data=MagicMock(coordinator=coord),
        )
        coord.data = {"protect": {"chimes": {"chime1": {}}, "cameras": {"cam1": {}}}}

        with patch.object(hass.config_entries, "async_entries", return_value=[entry]):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CHIME_VOLUME,
                {"chime_id": "chime1", "volume": 50, "camera_id": ["cam1"]},
                blocking=True,
            )
            assert _protect(coord).async_set_chime_volume.called

            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CHIME_RINGTONE,
                {"chime_id": "chime1", "ringtone_id": "tone-2", "camera_id": ["cam1"]},
                blocking=True,
            )
            assert _protect(coord).async_set_chime_ringtone.called

            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_CHIME_REPEAT_TIMES,
                {"chime_id": "chime1", "repeat_times": 2, "camera_id": ["cam1"]},
                blocking=True,
            )
            assert _protect(coord).async_set_chime_repeat.called

        # Restart device site search (multiple sites in coordinator data)
        coord_net = MagicMock()
        coord_net.device_coordinator.async_restart_device = AsyncMock()
        coord_net.data = {
            "devices": {"other_site": {"other_d": {}}, "site_target": {"target_d": {}}}
        }
        entry_net = MagicMock(
            entry_id="entry_net",
            title="Network",
            runtime_data=MagicMock(coordinator=coord_net),
        )

        with patch.object(
            hass.config_entries, "async_entries", return_value=[entry_net]
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RESTART_DEVICE,
                {"device_id": "target_d"},
                blocking=True,
            )
            coord_net.device_coordinator.async_restart_device.assert_called_once_with(
                "site_target", "target_d"
            )

        # When coordinator.data has no devices section (warming up), device is accepted
        # by resolver
        # but site_id cannot be inferred, hitting lines 1241-1242
        coord_warming = MagicMock(data={})
        entry_warming = MagicMock(
            entry_id="entry_warm",
            title="Warming",
            runtime_data=MagicMock(coordinator=coord_warming),
        )
        with (
            patch.object(
                hass.config_entries, "async_entries", return_value=[entry_warming]
            ),
            pytest.raises(
                ServiceValidationError,
                match="Site ID is required to restart device 'd_unloaded'",
            ),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RESTART_DEVICE,
                {"device_id": "d_unloaded"},
                blocking=True,
            )

        await async_unload_services(hass)
        # Call unload a second time when no services are registered to cover false
        # branches
        await async_unload_services(hass)

    async def test_resolver_fine_grained_branches(self, hass: HomeAssistant):
        """Test fine-grained branch conditions for resolvers and handlers."""

        dev_reg = dr.async_get(hass)
        ent_reg = er.async_get(hass)

        entry1 = MockConfigEntry(
            domain=DOMAIN, entry_id="fg_entry1", title="Console FG"
        )
        entry1.add_to_hass(hass)
        entry1.runtime_data = MagicMock(
            coordinator=MagicMock(
                data={
                    "devices": {"s1": {"d_exist": {}}, "s_empty": "not_a_dict"},
                    "clients": {"s1": {"00:11:22:33:44:55": {}}},
                    "protect": {"cameras": {"cam_exist": {}}, "lights": "not_a_dict"},
                }
            )
        )

        # --- Network Device branches ---
        # 1. Entity without config_entry_id, without device_id, unique_id="single"
        # (len(parts) < 2), no dev_entry
        ent_no_cfg = ent_reg.async_get_or_create(
            "switch",
            DOMAIN,
            "single",
        )
        with pytest.raises(ServiceValidationError, match="Could not resolve target"):
            _resolve_network_device_id(hass, ent_no_cfg.entity_id, None, [entry1])

        # 2. Entity with non-matching config_entry_id
        mismatch_entry = MockConfigEntry(domain=DOMAIN, entry_id="mismatch_entry")
        mismatch_entry.add_to_hass(hass)
        ent_mismatch_cfg = ent_reg.async_get_or_create(
            "switch",
            DOMAIN,
            "s1_otherdev",
            config_entry=mismatch_entry,
        )
        with pytest.raises(ServiceValidationError, match="console is not loaded"):
            _resolve_network_device_id(hass, ent_mismatch_cfg.entity_id, None, [entry1])

        # 3. Device where identifier starts with s1_ but device is not in s_devs or
        # s_devs not dict
        dev_not_in_sdevs = dev_reg.async_get_or_create(
            config_entry_id=entry1.entry_id,
            identifiers={
                (DOMAIN, "s1_notthere"),
                (DOMAIN, "s_empty_ignored"),
                (DOMAIN, "otherprefix_dev"),
            },
        )
        d, _s, e = _resolve_network_device_id(hass, dev_not_in_sdevs.id, None, [entry1])
        assert d in {"notthere", "ignored", "dev"}

        # 4. Device where resolved_entry is already set, checking resolved_entry is None
        # branch
        # Also testing restart_device with site_id provided (1231->1236)
        coord = MagicMock()
        coord.device_coordinator.async_restart_device = AsyncMock()
        coord.data = {"devices": {"s1": {"d_exist": {}}}}
        entry_with_coord = MockConfigEntry(
            domain=DOMAIN, entry_id="fg_restart", title="Restart FG"
        )
        entry_with_coord.add_to_hass(hass)
        entry_with_coord.runtime_data = MagicMock(coordinator=coord)

        await async_setup_services(hass)
        with patch.object(
            hass.config_entries, "async_entries", return_value=[entry_with_coord]
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RESTART_DEVICE,
                {"device_id": "d_exist", "site_id": "s1"},
                blocking=True,
            )
            assert coord.device_coordinator.async_restart_device.called

        # --- Protect Resource branches ---
        # 1. Entity without config_entry_id, without device_id, unique_id has no match
        ent_p_nocfg = ent_reg.async_get_or_create(
            "camera",
            DOMAIN,
            f"{DOMAIN}_camera_notmatching_camnone",
        )
        res_id, e = _resolve_protect_resource_id(
            hass, "camera", ent_p_nocfg.entity_id, [entry1]
        )
        assert res_id == "notmatching"
        assert e is None

        # 2. Entity with non-matching config_entry_id
        ent_p_mismatch = ent_reg.async_get_or_create(
            "camera",
            DOMAIN,
            f"{DOMAIN}_camera_cam_exist",
            config_entry=mismatch_entry,
        )
        with pytest.raises(ServiceValidationError, match="console is not loaded"):
            _resolve_protect_resource_id(
                hass, "camera", ent_p_mismatch.entity_id, [entry1]
            )

        # 3. Protect resource_type != camera (e.g. light) with entity that cannot be
        # resolved and no dev_entry
        ent_light_single = ent_reg.async_get_or_create(
            "light",
            DOMAIN,
            "singlelight",
        )
        with pytest.raises(ServiceValidationError, match="Could not resolve target"):
            _resolve_protect_resource_id(
                hass, "light", ent_light_single.entity_id, [entry1]
            )

        # 4. Camera device fallback loop with unrelated entity on the device
        dev_cam_unrelated = dev_reg.async_get_or_create(
            config_entry_id=entry1.entry_id,
            identifiers={(DOMAIN, "other_device_id")},
        )
        ent_reg.async_get_or_create(
            "sensor",
            DOMAIN,
            "sensor_temperature",
            config_entry=entry1,
            device_id=dev_cam_unrelated.id,
        )
        with pytest.raises(ServiceValidationError, match="Could not resolve target"):
            _resolve_protect_resource_id(hass, "camera", dev_cam_unrelated.id, [entry1])

        # --- Network Client branches ---
        # 1. Entity without config_entry_id, without device_id, unique_id has no match
        ent_c_nocfg = ent_reg.async_get_or_create(
            "device_tracker",
            DOMAIN,
            "singletracker",
        )
        c_id, e = _resolve_network_client_id(hass, ent_c_nocfg.entity_id, [entry1])
        assert c_id == ent_c_nocfg.entity_id
        assert e is None

        # 2. Entity with non-matching config_entry_id
        ent_c_mismatch = ent_reg.async_get_or_create(
            "device_tracker",
            DOMAIN,
            "prefix_clienttest",
            config_entry=mismatch_entry,
        )
        with pytest.raises(ServiceValidationError, match="console is not loaded"):
            _resolve_network_client_id(hass, ent_c_mismatch.entity_id, [entry1])

        # 3. Client device target with non-client identifier fallback
        dev_client_other = dev_reg.async_get_or_create(
            config_entry_id=entry1.entry_id,
            identifiers={(DOMAIN, "other_prefix_00:11:22:33:44:55")},
        )
        c_id, e = _resolve_network_client_id(hass, dev_client_other.id, [entry1])
        assert c_id == dev_client_other.id
        assert e == entry1


@pytest.mark.parametrize(
    ("service", "entity_domain", "unique_id", "data", "method"),
    [
        (
            "restart_device",
            "sensor",
            "site_device_uptime",
            {},
            "device_coordinator.async_restart_device",
        ),
        (
            "set_hdr_mode",
            "camera",
            "unifi_insights_camera_cam",
            {"mode": "auto"},
            "require_protect_coordinator.return_value.async_set_hdr_mode",
        ),
        (
            "authorize_guest",
            "device_tracker",
            "unifi_insights_aa:bb:cc:dd:ee:ff",
            {"site_id": "site"},
            "config_coordinator.async_authorize_guest",
        ),
    ],
)
@pytest.mark.parametrize("target_kind", ["entity", "device"])
async def test_unloaded_target_console_does_not_fall_back(
    hass: HomeAssistant,
    *,
    service: str,
    entity_domain: str,
    unique_id: str,
    data: dict[str, str],
    method: str,
    target_kind: str,
) -> None:
    """A registry target must never be sent to a different loaded console."""
    owner = MockConfigEntry(domain=DOMAIN)
    owner.add_to_hass(hass)
    other = MockConfigEntry(domain=DOMAIN)
    other.add_to_hass(hass)
    coordinator = autospec_command_owners(MagicMock(data={}))
    action = AsyncMock()
    *owner_path, name = method.split(".")
    owner_obj = coordinator
    for part in owner_path:
        owner_obj = getattr(owner_obj, part)
    setattr(owner_obj, name, action)
    other.runtime_data = MagicMock(coordinator=coordinator)
    entity = er.async_get(hass).async_get_or_create(
        entity_domain,
        DOMAIN,
        unique_id,
        config_entry=owner,
    )
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id,
        identifiers={
            (
                DOMAIN,
                "protect_camera_cam"
                if entity_domain == "camera"
                else "client_aa:bb:cc:dd:ee:ff"
                if entity_domain == "device_tracker"
                else "site_device",
            )
        },
    )
    target = (
        {"entity_id": entity.entity_id}
        if target_kind == "entity"
        else {
            "device_id": device.id,
        }
    )
    await async_setup_services(hass)
    with pytest.raises(ServiceValidationError, match="console is not loaded"):
        await hass.services.async_call(
            DOMAIN,
            service,
            {**data, **target},
            blocking=True,
        )
    action.assert_not_awaited()


async def test_secondary_camera_registry_owner_is_enforced(
    hass: HomeAssistant,
) -> None:
    """An uncached camera collection must not override known registry ownership."""

    chime_entry = MockConfigEntry(domain=DOMAIN)
    chime_entry.add_to_hass(hass)
    camera_entry = MockConfigEntry(domain=DOMAIN)
    camera_entry.add_to_hass(hass)
    chime_coord = MagicMock(data={"protect": {"chimes": {"chime": {}}}})
    _protect(chime_coord)
    chime_entry.runtime_data = MagicMock(coordinator=chime_coord)
    camera_entry.runtime_data = MagicMock(
        coordinator=MagicMock(data={"protect": {"cameras": {"cam": {}}}}),
    )
    camera = er.async_get(hass).async_get_or_create(
        "camera",
        DOMAIN,
        "unifi_insights_camera_cam",
        config_entry=camera_entry,
    )
    await async_setup_services(hass)
    with pytest.raises(ServiceValidationError, match="different Protect console"):
        await hass.services.async_call(
            DOMAIN,
            "set_chime_volume",
            {"chime_id": "chime", "camera_id": camera.entity_id, "volume": 50},
            blocking=True,
        )
    _protect(chime_coord).async_set_chime_volume.assert_not_awaited()


@pytest.mark.parametrize("target_kind", ["dotted_mac", "entity", "device"])
async def test_authorize_guest_accepts_dotted_mac(
    hass: HomeAssistant,
    target_kind: str,
) -> None:
    """Dotted MAC addresses remain valid raw client targets."""

    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    coordinator = MagicMock(
        data={
            "sites": {"site": {}},
            "clients": {
                "site": {"native-client-id": {"macAddress": "aa:bb:cc:dd:ee:ff"}}
            },
        }
    )
    coordinator.config_coordinator.async_authorize_guest = AsyncMock()
    entry.runtime_data = MagicMock(coordinator=coordinator)
    entity = er.async_get(hass).async_get_or_create(
        "device_tracker",
        DOMAIN,
        "unifi_insights_aa:bb:cc:dd:ee:ff",
        config_entry=entry,
    )
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "client_aa:bb:cc:dd:ee:ff")},
    )
    target = {
        "dotted_mac": {"client_id": "aabb.ccdd.eeff"},
        "entity": {"entity_id": entity.entity_id},
        "device": {"device_id": device.id},
    }[target_kind]
    await async_setup_services(hass)
    await hass.services.async_call(
        DOMAIN,
        "authorize_guest",
        {"site_id": "site", **target},
        blocking=True,
    )
    coordinator.config_coordinator.async_authorize_guest.assert_awaited_once_with(
        "site",
        "native-client-id",
    )


@pytest.mark.parametrize("resource", ["network", "camera", "client"])
async def test_raw_targets_without_entity_registry(
    hass: HomeAssistant,
    resource: str,
) -> None:
    """Raw IDs remain usable before the entity registry has been loaded."""
    with patch.dict(hass.data):
        hass.data.pop(er.DATA_REGISTRY, None)
        if resource == "network":
            assert _resolve_network_device_id(hass, "native", "site", []) == (
                "native",
                "site",
                None,
            )
        elif resource == "camera":
            assert _resolve_protect_resource_id(hass, "camera", "native", []) == (
                "native",
                None,
            )
        else:
            assert _resolve_network_client_id(hass, "native", []) == ("native", None)


@pytest.mark.parametrize(
    ("resource", "identifier", "expected"),
    [
        ("network", "site_native", ("native", "site")),
        ("camera", "protect_camera_native", ("native",)),
        ("client", "client_aa:bb:cc:dd:ee:ff", ("aa:bb:cc:dd:ee:ff",)),
    ],
)
async def test_legacy_entity_resolves_through_its_device(
    hass: HomeAssistant,
    resource: str,
    identifier: str,
    expected: tuple[str, ...],
) -> None:
    """A legacy entity ID can fall back to its device without losing its owner."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(coordinator=MagicMock(data={}))
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, identifier)},
    )
    entity = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        "legacy",
        config_entry=entry,
        device_id=device.id,
    )
    if resource == "network":
        result = _resolve_network_device_id(hass, entity.entity_id, None, [entry])
    elif resource == "camera":
        result = _resolve_protect_resource_id(hass, "camera", entity.entity_id, [entry])
    else:
        result = _resolve_network_client_id(hass, entity.entity_id, [entry])
    assert result == (*expected, entry)


@pytest.mark.parametrize("data", [{}, {"protect": {}}])
async def test_camera_entity_resolves_before_cache_is_populated(
    hass: HomeAssistant,
    data: dict,
) -> None:
    """Registry identity remains usable while Protect collections are warming up."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(coordinator=MagicMock(data=data))
    entity = er.async_get(hass).async_get_or_create(
        "camera",
        DOMAIN,
        "unifi_insights_camera_native",
        config_entry=entry,
    )
    assert _resolve_protect_resource_id(
        hass,
        "camera",
        entity.entity_id,
        [entry],
    ) == ("native", entry)


async def test_malformed_network_device_identifier_is_rejected(
    hass: HomeAssistant,
) -> None:
    """A device without a site/resource identifier cannot become an API target."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(coordinator=MagicMock(data={}))
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "malformed")},
    )
    with pytest.raises(ServiceValidationError, match="Could not resolve target"):
        _resolve_network_device_id(hass, device.id, None, [entry])


async def test_restart_registry_device_without_cached_site_is_rejected(
    hass: HomeAssistant,
) -> None:
    """Restart must not guess a site when the registry device is absent from cache."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    coordinator = MagicMock(data={"devices": {"other": {}}})
    coordinator.device_coordinator.async_restart_device = AsyncMock()
    entry.runtime_data = MagicMock(coordinator=coordinator)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "site_native")},
    )
    await async_setup_services(hass)
    with pytest.raises(ServiceValidationError, match="Site ID is required"):
        await hass.services.async_call(
            DOMAIN,
            "restart_device",
            {"device_id": device.id},
            blocking=True,
        )
    coordinator.device_coordinator.async_restart_device.assert_not_awaited()


@pytest.mark.parametrize("matched", [True, False])
async def test_guest_client_lookup_skips_other_clients(
    hass: HomeAssistant,
    *,
    matched: bool,
) -> None:
    """Translate a matching MAC without selecting an unrelated cached client."""
    clients = {"other": {"mac": "11:22:33:44:55:66"}}
    if matched:
        clients["native"] = {"mac": "aa:bb:cc:dd:ee:ff"}
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    coordinator = MagicMock(data={"clients": {"site": clients}})
    coordinator.config_coordinator.async_authorize_guest = AsyncMock()
    entry.runtime_data = MagicMock(coordinator=coordinator)
    await async_setup_services(hass)
    await hass.services.async_call(
        DOMAIN,
        "authorize_guest",
        {"site_id": "site", "client_id": "aa:bb:cc:dd:ee:ff"},
        blocking=True,
    )
    coordinator.config_coordinator.async_authorize_guest.assert_awaited_once_with(
        "site",
        "native" if matched else "aa:bb:cc:dd:ee:ff",
    )


async def test_network_device_registry_fallback_for_empty_site(
    hass: HomeAssistant,
) -> None:
    """A known site's empty device cache does not corrupt a registry identifier."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(
        coordinator=MagicMock(data={"devices": {"site": {}}}),
    )
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "site_native")},
    )
    assert _resolve_network_device_id(hass, device.id, None, [entry]) == (
        "native",
        "site",
        entry,
    )


@pytest.mark.parametrize(
    ("service", "field", "expected_values", "other_fields"),
    [
        ("set_hdr_mode", "mode", ["auto", "on", "off"], {}),
        (
            "set_video_mode",
            "mode",
            ["default", "highFps", "sport", "slowShutter"],
            {},
        ),
        ("set_light_mode", "mode", ["always", "motion", "off"], {}),
        ("set_light_level", "level", list(range(101)), {}),
        ("set_mic_volume", "volume", list(range(1, 101)), {}),
        ("set_chime_volume", "volume", list(range(101)), {}),
        ("ptz_move", "preset", list(range(16)), {}),
        ("ptz_patrol", "slot", list(range(16)), {"action": "start"}),
    ],
)
def test_protect_ui_selectors_match_service_schemas(
    service: str,
    field: str,
    expected_values: list[str | int],
    other_fields: dict[str, str],
) -> None:
    """Every selectable UI value must generate an accepted service payload."""
    definitions = yaml.safe_load(
        Path(services.__file__).with_suffix(".yaml").read_text(encoding="utf-8"),
    )
    fields = definitions[service]["fields"]
    selector = fields[field]["selector"]
    if "select" in selector:
        values = selector["select"]["options"]
    else:
        number = selector["number"]
        values = list(range(number["min"], number["max"] + 1, number["step"]))
    assert values == expected_values
    schema = getattr(services, f"{service.upper()}_SCHEMA")
    for value in values:
        payload = {**other_fields, field: value}
        assert schema(payload)[field] == value
    if service == "ptz_move":
        assert fields["preset"]["required"] is True
        assert "direction" not in fields


class TestReconnectClientService:
    """Tests for reconnect_client service."""

    async def test_reconnect_client_success(self, hass: HomeAssistant):
        """Test reconnect_client succeeds for a connected wireless client."""
        mock_coordinator = MagicMock()
        mock_coordinator.device_coordinator.async_reconnect_client = AsyncMock()
        mock_coordinator.data = {
            "sites": {"site1": {}},
            "clients": {
                "site1": {
                    "client1": {
                        "id": "client1",
                        "mac": "aa:bb:cc:dd:ee:ff",
                        "connected": True,
                        "type": "WIRELESS",
                    }
                }
            },
        }
        mock_entry = MagicMock(
            entry_id="entry1",
            title="Console 1",
            runtime_data=MagicMock(coordinator=mock_coordinator),
        )

        await async_setup_services(hass)
        with patch.object(
            hass.config_entries, "async_entries", return_value=[mock_entry]
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RECONNECT_CLIENT,
                {"site_id": "site1", "client_id": "client1"},
                blocking=True,
            )

        mock_coordinator.device_coordinator.async_reconnect_client.assert_awaited_once_with(
            "site1", "client1"
        )
        await async_unload_services(hass)

    async def test_reconnect_client_by_mac_and_dotted_mac(self, hass: HomeAssistant):
        """Test reconnect_client resolves standard and dotted MACs."""
        mock_coordinator = MagicMock()
        mock_coordinator.device_coordinator.async_reconnect_client = AsyncMock()
        mock_coordinator.data = {
            "sites": {"site1": {}},
            "clients": {
                "site1": {
                    "native1": {
                        "id": "native1",
                        "macAddress": "aa:bb:cc:dd:ee:ff",
                        "connected": True,
                        "type": "WIRELESS",
                    }
                }
            },
        }
        mock_entry = MagicMock(
            entry_id="entry1",
            title="Console 1",
            runtime_data=MagicMock(coordinator=mock_coordinator),
        )

        await async_setup_services(hass)
        with patch.object(
            hass.config_entries, "async_entries", return_value=[mock_entry]
        ):
            # Standard MAC
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RECONNECT_CLIENT,
                {"site_id": "site1", "client_id": "aa:bb:cc:dd:ee:ff"},
                blocking=True,
            )
            mock_coordinator.device_coordinator.async_reconnect_client.assert_awaited_with(
                "site1", "native1"
            )

            # Dotted MAC
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RECONNECT_CLIENT,
                {"site_id": "site1", "client_id": "aabb.ccdd.eeff"},
                blocking=True,
            )
            assert (
                mock_coordinator.device_coordinator.async_reconnect_client.call_count
                == 2
            )
        await async_unload_services(hass)

    async def test_reconnect_client_by_entity_and_device(self, hass: HomeAssistant):
        """Test reconnect_client targeting entity and device."""
        entry = MockConfigEntry(domain=DOMAIN)
        entry.add_to_hass(hass)
        coordinator = MagicMock()
        coordinator.device_coordinator.async_reconnect_client = AsyncMock()
        coordinator.data = {
            "sites": {"site1": {}},
            "clients": {
                "site1": {
                    "native1": {
                        "id": "native1",
                        "macAddress": "aa:bb:cc:dd:ee:ff",
                        "connected": True,
                        "type": "WIRELESS",
                    }
                }
            },
        }
        entry.runtime_data = MagicMock(coordinator=coordinator)
        entity = er.async_get(hass).async_get_or_create(
            "device_tracker",
            DOMAIN,
            "unifi_insights_aa:bb:cc:dd:ee:ff",
            config_entry=entry,
        )
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, "client_aa:bb:cc:dd:ee:ff")},
        )

        await async_setup_services(hass)
        # Entity target
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RECONNECT_CLIENT,
            {"site_id": "site1", "entity_id": entity.entity_id},
            blocking=True,
        )
        coordinator.device_coordinator.async_reconnect_client.assert_awaited_with(
            "site1", "native1"
        )

        # Device target
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RECONNECT_CLIENT,
            {"site_id": "site1", "device_id": device.id},
            blocking=True,
        )
        assert coordinator.device_coordinator.async_reconnect_client.call_count == 2
        await async_unload_services(hass)

    async def test_reconnect_client_missing_client_id(self, hass: HomeAssistant):
        """Test reconnect_client raises when client_id / target is missing."""
        mock_coordinator = MagicMock()
        mock_entry = MagicMock(
            entry_id="entry1",
            runtime_data=MagicMock(coordinator=mock_coordinator),
        )
        await async_setup_services(hass)
        with (
            patch.object(
                hass.config_entries, "async_entries", return_value=[mock_entry]
            ),
            pytest.raises(
                ServiceValidationError, match="Client ID or target is required"
            ) as exc_info,
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RECONNECT_CLIENT,
                {"site_id": "site1"},
                blocking=True,
            )
        assert exc_info.value.translation_key == "client_target_required"
        await async_unload_services(hass)

    async def test_reconnect_client_not_connected(self, hass: HomeAssistant):
        """Test reconnect_client raises when client is disconnected or absent."""
        mock_coordinator = MagicMock()
        mock_coordinator.data = {
            "sites": {"site1": {}},
            "clients": {
                "site1": {
                    "client1": {
                        "id": "client1",
                        "connected": False,
                        "type": "WIRELESS",
                    }
                }
            },
        }
        mock_entry = MagicMock(
            entry_id="entry1",
            runtime_data=MagicMock(coordinator=mock_coordinator),
        )
        await async_setup_services(hass)
        with patch.object(
            hass.config_entries, "async_entries", return_value=[mock_entry]
        ):
            # Connected is False
            with pytest.raises(
                ServiceValidationError, match="client is not currently connected"
            ) as exc_info:
                await hass.services.async_call(
                    DOMAIN,
                    SERVICE_RECONNECT_CLIENT,
                    {"site_id": "site1", "client_id": "client1"},
                    blocking=True,
                )
            assert exc_info.value.translation_key == "client_not_connected"

            # Absent client
            with pytest.raises(
                ServiceValidationError, match="client is not currently connected"
            ) as exc_info:
                await hass.services.async_call(
                    DOMAIN,
                    SERVICE_RECONNECT_CLIENT,
                    {"site_id": "site1", "client_id": "client_missing"},
                    blocking=True,
                )
            assert exc_info.value.translation_key == "client_not_connected"
        await async_unload_services(hass)

    async def test_reconnect_client_not_wireless(self, hass: HomeAssistant):
        """Test reconnect_client raises when client is wired, vpn, or unknown type."""
        mock_coordinator = MagicMock()
        mock_coordinator.data = {
            "sites": {"site1": {}},
            "clients": {
                "site1": {
                    "wired_client": {
                        "id": "wired_client",
                        "connected": True,
                        "type": "WIRED",
                    },
                    "vpn_client": {
                        "id": "vpn_client",
                        "connected": True,
                        "type": "VPN",
                    },
                    "unknown_wireless_client": {
                        "id": "unknown_wireless_client",
                        "connected": True,
                        "type": "UNKNOWN_WIRELESS",
                    },
                }
            },
        }
        mock_entry = MagicMock(
            entry_id="entry1",
            runtime_data=MagicMock(coordinator=mock_coordinator),
        )
        await async_setup_services(hass)
        with patch.object(
            hass.config_entries, "async_entries", return_value=[mock_entry]
        ):
            for client_id in ["wired_client", "vpn_client", "unknown_wireless_client"]:
                with pytest.raises(
                    ServiceValidationError, match="not a wireless client"
                ) as exc_info:
                    await hass.services.async_call(
                        DOMAIN,
                        SERVICE_RECONNECT_CLIENT,
                        {"site_id": "site1", "client_id": client_id},
                        blocking=True,
                    )
                assert exc_info.value.translation_key == "client_not_wireless"
                assert exc_info.value.translation_placeholders == (
                    {"client_id": client_id}
                )
        await async_unload_services(hass)

    async def test_reconnect_client_no_coordinator(self, hass: HomeAssistant):
        """Test reconnect_client raises when no coordinator is loaded."""
        await async_setup_services(hass)
        with (
            patch.object(hass.config_entries, "async_entries", return_value=[]),
            pytest.raises(
                ServiceValidationError, match="No UniFi Insights coordinator found"
            ),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RECONNECT_CLIENT,
                {"site_id": "site1", "client_id": "c1"},
                blocking=True,
            )
        await async_unload_services(hass)

    async def test_reconnect_client_api_failure_propagates(self, hass: HomeAssistant):
        """Test reconnect_client propagates coordinator HomeAssistantError."""
        mock_coordinator = MagicMock()
        mock_coordinator.device_coordinator.async_reconnect_client = AsyncMock(
            side_effect=HomeAssistantError("Failed to kick client")
        )
        mock_coordinator.data = {
            "sites": {"site1": {}},
            "clients": {
                "site1": {
                    "c1": {
                        "id": "c1",
                        "connected": True,
                        "type": "WIRELESS",
                    }
                }
            },
        }
        mock_entry = MagicMock(
            entry_id="entry1",
            runtime_data=MagicMock(coordinator=mock_coordinator),
        )
        await async_setup_services(hass)
        with (
            patch.object(
                hass.config_entries, "async_entries", return_value=[mock_entry]
            ),
            pytest.raises(HomeAssistantError, match="Failed to kick client"),
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RECONNECT_CLIENT,
                {"site_id": "site1", "client_id": "c1"},
                blocking=True,
            )
        await async_unload_services(hass)


class _Response:
    """Minimal aiohttp response replacement for transport tests."""

    def __init__(
        self,
        body: Any = None,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}
        self.url = URL("https://192.168.1.1")
        self.method = "GET"
        self.history = ()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def text(self) -> str:
        if isinstance(self._body, (dict, list)):
            return json.dumps(self._body)
        if self._body is None:
            return ""
        return str(self._body)

    async def json(self) -> Any:
        if self._body is None or isinstance(self._body, str):
            msg = "No JSON body"
            raise ValueError(msg)
        return self._body


class _Session:
    """Mock session recording requests and returning queued responses."""

    closed = False

    def __init__(
        self, responses: list[_Response | dict[str, Any] | list[Any] | None]
    ) -> None:
        self._responses = iter(
            [r if isinstance(r, _Response) else _Response(r) for r in responses]
        )
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: object, **kwargs: Any) -> _Response:
        self.requests.append({"method": method, "url": str(url), **kwargs})
        return next(self._responses)

    async def close(self) -> None:
        self.closed = True


class TestRemoveClientsService:
    """Tests for remove_clients service."""

    def test_filter_clients_for_removal_matrix(self):
        """Test pure selection helper _filter_clients_for_removal against core rules."""
        records = [
            # Eligible client: short-lived, unnamed, no fixed IP
            {"mac": "AA:BB:CC:DD:EE:01", "first_seen": 100, "last_seen": 200},
            # Eligible: exactly 900 seconds
            {"mac": "AA:BB:CC:DD:EE:02", "first_seen": 100, "last_seen": 1000},
            # Excluded: > 900 seconds (901s)
            {"mac": "AA:BB:CC:DD:EE:03", "first_seen": 100, "last_seen": 1001},
            # Eligible: missing first_seen
            {"mac": "AA:BB:CC:DD:EE:04", "first_seen": None, "last_seen": 500},
            # Eligible: missing last_seen
            {"mac": "AA:BB:CC:DD:EE:05", "first_seen": 100, "last_seen": None},
            # Excluded: has name
            {
                "mac": "AA:BB:CC:DD:EE:06",
                "name": "Alice iPhone",
                "first_seen": 100,
                "last_seen": 200,
            },
            # Excluded: has hostname
            {
                "mac": "AA:BB:CC:DD:EE:07",
                "hostname": "alices-phone",
                "first_seen": 100,
                "last_seen": 200,
            },
            # Excluded: has fixed_ip
            {
                "mac": "AA:BB:CC:DD:EE:08",
                "fixed_ip": "192.168.1.50",
                "first_seen": 100,
                "last_seen": 200,
            },
            # Excluded: missing MAC
            {"first_seen": 100, "last_seen": 200},
            # Excluded: empty string MAC
            {"mac": "   ", "first_seen": 100, "last_seen": 200},
            # Duplicate of EE:01 with different casing
            {"mac": "aa:bb:cc:dd:ee:01", "first_seen": 100, "last_seen": 200},
            # Ineligible: invalid timestamp (malformed data kept)
            {"mac": "AA:BB:CC:DD:EE:09", "first_seen": "invalid", "last_seen": 200},
            # Ineligible: boolean timestamp (not a real number, kept)
            {"mac": "AA:BB:CC:DD:EE:0A", "first_seen": True, "last_seen": 200},
            # Ineligible: boolean last_seen (not a real number, kept)
            {"mac": "AA:BB:CC:DD:EE:0B", "first_seen": 100, "last_seen": False},
            # Ineligible: invalid first_seen with no last_seen (kept)
            {"mac": "AA:BB:CC:DD:EE:0C", "first_seen": "invalid", "last_seen": None},
            # Ineligible: falsy non-number first_seen (kept)
            {"mac": "AA:BB:CC:DD:EE:0D", "first_seen": [], "last_seen": 200},
            # Eligible: numeric zero first_seen skips the age check, as in core
            {"mac": "AA:BB:CC:DD:EE:0E", "first_seen": 0, "last_seen": 5000},
            # Excluded: unhashable name is still a name
            {
                "mac": "AA:BB:CC:DD:EE:0F",
                "name": {"value": "protected"},
                "first_seen": 100,
                "last_seen": 200,
            },
            # Ineligible: NaN and infinite timestamps (kept)
            {"mac": "AA:BB:CC:DD:EE:10", "first_seen": float("nan"), "last_seen": 200},
            {"mac": "AA:BB:CC:DD:EE:11", "first_seen": 100, "last_seen": float("inf")},
            # Ineligible: int too large to compare with a float time (kept)
            {"mac": "AA:BB:CC:DD:EE:12", "first_seen": 10**309, "last_seen": 200.0},
        ]

        result = _filter_clients_for_removal(records)
        assert result == [
            "aa:bb:cc:dd:ee:01",
            "aa:bb:cc:dd:ee:02",
            "aa:bb:cc:dd:ee:04",
            "aa:bb:cc:dd:ee:05",
            "aa:bb:cc:dd:ee:0e",
        ]

    async def test_remove_clients_unreadable_timestamp_keeps_client_and_continues(
        self, hass: HomeAssistant
    ):
        """Test an unreadable timestamp keeps the client and the next site runs."""
        coordinator = MagicMock()
        coordinator.is_network_usable = True
        coordinator.data = {"sites": {"site1": {}, "site2": {}}}
        coordinator.async_get_historical_clients = AsyncMock(
            side_effect=[
                [
                    {
                        "mac": "11:11:11:11:11:11",
                        "first_seen": 10**309,
                        "last_seen": 200.0,
                    }
                ],
                [{"mac": "22:22:22:22:22:22", "first_seen": 100, "last_seen": 200}],
            ]
        )
        coordinator.device_coordinator.async_forget_clients_batch = AsyncMock(
            return_value=True
        )
        entry = MagicMock(
            entry_id="entry1",
            title="Console 1",
            runtime_data=MagicMock(coordinator=coordinator),
        )

        await async_setup_services(hass)
        with patch.object(hass.config_entries, "async_entries", return_value=[entry]):
            await hass.services.async_call(
                DOMAIN, SERVICE_REMOVE_CLIENTS, {}, blocking=True
            )
        await async_unload_services(hass)

        coordinator.device_coordinator.async_forget_clients_batch.assert_awaited_once_with(
            "site2", ["22:22:22:22:22:22"]
        )

    async def test_remove_clients_non_admin_raises_unauthorized(
        self, hass: HomeAssistant
    ):
        """Test non-admin user call to remove_clients raises Unauthorized."""
        await async_setup_services(hass)
        user = await hass.auth.async_create_user("non_admin_user")
        user.is_owner = False
        user.groups = []  # Non-admin
        user.invalidate_cache()
        context = Context(user_id=user.id)

        with pytest.raises(Unauthorized):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REMOVE_CLIENTS,
                {},
                context=context,
                blocking=True,
            )
        await async_unload_services(hass)

    async def test_remove_clients_success_multi_console(self, hass: HomeAssistant):
        """Test remove_clients removes eligible clients across multiple consoles."""
        coord1 = MagicMock()
        coord1.is_network_usable = True
        coord1.data = {"sites": {"site1": {}}}
        coord1.async_get_historical_clients = AsyncMock(
            return_value=[
                {"mac": "11:22:33:44:55:66", "first_seen": 100, "last_seen": 200},
                {"mac": "AA:BB:CC:DD:EE:FF", "name": "Named Device"},
            ]
        )
        coord1.device_coordinator.async_forget_clients_batch = AsyncMock(
            return_value=True
        )
        entry1 = MagicMock(
            entry_id="entry1",
            title="Console 1",
            runtime_data=MagicMock(coordinator=coord1),
        )

        coord2 = MagicMock()
        coord2.is_network_usable = True
        coord2.data = {"sites": {"site2": {}}}
        coord2.async_get_historical_clients = AsyncMock(
            return_value=[
                {"mac": "99:88:77:66:55:44", "first_seen": 100, "last_seen": 300},
            ]
        )
        coord2.device_coordinator.async_forget_clients_batch = AsyncMock(
            return_value=True
        )
        entry2 = MagicMock(
            entry_id="entry2",
            title="Console 2",
            runtime_data=MagicMock(coordinator=coord2),
        )

        await async_setup_services(hass)
        with patch.object(
            hass.config_entries, "async_entries", return_value=[entry1, entry2]
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REMOVE_CLIENTS,
                {},
                blocking=True,
            )

        coord1.device_coordinator.async_forget_clients_batch.assert_awaited_once_with(
            "site1", ["11:22:33:44:55:66"]
        )
        coord2.device_coordinator.async_forget_clients_batch.assert_awaited_once_with(
            "site2", ["99:88:77:66:55:44"]
        )
        await async_unload_services(hass)

    async def test_remove_clients_skips_console_without_network(
        self, hass: HomeAssistant
    ):
        """Test remove_clients skips consoles where Network is not usable."""
        coord_no_network = MagicMock()
        coord_no_network.is_network_usable = False
        coord_no_network.data = {"sites": {"site1": {}}}
        coord_no_network.async_get_historical_clients = AsyncMock()
        entry_no_net = MagicMock(
            entry_id="entry1",
            title="Protect Only",
            runtime_data=MagicMock(coordinator=coord_no_network),
        )

        coord_with_net = MagicMock()
        coord_with_net.is_network_usable = True
        coord_with_net.data = {"sites": {"site2": {}}}
        coord_with_net.async_get_historical_clients = AsyncMock(
            return_value=[
                {"mac": "11:22:33:44:55:66", "first_seen": 100, "last_seen": 200}
            ]
        )
        coord_with_net.device_coordinator.async_forget_clients_batch = AsyncMock(
            return_value=True
        )
        entry_with_net = MagicMock(
            entry_id="entry2",
            title="Console Network",
            runtime_data=MagicMock(coordinator=coord_with_net),
        )

        await async_setup_services(hass)
        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[entry_no_net, entry_with_net],
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REMOVE_CLIENTS,
                {},
                blocking=True,
            )

        coord_no_network.async_get_historical_clients.assert_not_called()
        coord_with_net.device_coordinator.async_forget_clients_batch.assert_awaited_once_with(
            "site2", ["11:22:33:44:55:66"]
        )
        await async_unload_services(hass)

    async def test_remove_clients_no_eligible_consoles_raises(
        self, hass: HomeAssistant
    ):
        """Test remove_clients raises when no console has Network."""
        coord_no_net = MagicMock()
        coord_no_net.is_network_usable = False
        entry = MagicMock(
            entry_id="entry1",
            title="Protect Only",
            runtime_data=MagicMock(coordinator=coord_no_net),
        )

        await async_setup_services(hass)
        with (
            patch.object(hass.config_entries, "async_entries", return_value=[entry]),
            pytest.raises(
                ServiceValidationError,
                match="No UniFi Insights console has Network available",
            ) as exc_info,
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REMOVE_CLIENTS,
                {},
                blocking=True,
            )
        assert exc_info.value.translation_key == "no_network_console"
        await async_unload_services(hass)

    async def test_remove_clients_continues_and_aggregates_failures(
        self, hass: HomeAssistant
    ):
        """Test remove_clients continues past per-site failure and aggregates errors."""
        coord1 = MagicMock()
        coord1.is_network_usable = True
        coord1.data = {"sites": {"site1": {}}}
        coord1.async_get_historical_clients = AsyncMock(
            side_effect=HomeAssistantError("Connection refused")
        )
        entry1 = MagicMock(
            entry_id="entry1",
            title="Console 1",
            runtime_data=MagicMock(coordinator=coord1),
        )

        coord2 = MagicMock()
        coord2.is_network_usable = True
        coord2.data = {"sites": {"site2": {}}}
        coord2.async_get_historical_clients = AsyncMock(
            return_value=[
                {"mac": "11:22:33:44:55:66", "first_seen": 100, "last_seen": 200}
            ]
        )
        coord2.device_coordinator.async_forget_clients_batch = AsyncMock(
            return_value=True
        )
        entry2 = MagicMock(
            entry_id="entry2",
            title="Console 2",
            runtime_data=MagicMock(coordinator=coord2),
        )

        await async_setup_services(hass)
        with (
            patch.object(
                hass.config_entries, "async_entries", return_value=[entry1, entry2]
            ),
            pytest.raises(
                HomeAssistantError,
                match=r"Console 1 \(site site1\): Connection refused",
            ) as exc_info,
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REMOVE_CLIENTS,
                {},
                blocking=True,
            )
        assert exc_info.value.translation_key == "remove_clients_failed"
        assert exc_info.value.translation_placeholders == {
            "failures": "Console 1 (site site1): Connection refused"
        }

        # Confirm console 2 was still processed
        coord2.device_coordinator.async_forget_clients_batch.assert_awaited_once_with(
            "site2", ["11:22:33:44:55:66"]
        )
        await async_unload_services(hass)

    async def test_remove_clients_no_eligible_macs_sends_no_post(
        self, hass: HomeAssistant
    ):
        """Test remove_clients sends no forget request when no MACs are eligible."""
        coord = MagicMock()
        coord.is_network_usable = True
        coord.data = {"sites": {"site1": {}}}
        coord.async_get_historical_clients = AsyncMock(
            return_value=[
                {"mac": "11:22:33:44:55:66", "name": "Named"},
                {"mac": "77:88:99:00:11:22", "first_seen": 100, "last_seen": 2000},
            ]
        )
        coord.device_coordinator.async_forget_clients_batch = AsyncMock()
        entry = MagicMock(
            entry_id="entry1",
            title="Console 1",
            runtime_data=MagicMock(coordinator=coord),
        )

        await async_setup_services(hass)
        with patch.object(hass.config_entries, "async_entries", return_value=[entry]):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REMOVE_CLIENTS,
                {},
                blocking=True,
            )

        coord.device_coordinator.async_forget_clients_batch.assert_not_called()
        await async_unload_services(hass)

    async def test_remove_clients_rejects_extra_fields(self, hass: HomeAssistant):
        """Test remove_clients rejects unexpected arguments via schema."""
        await async_setup_services(hass)
        with pytest.raises(vol.MultipleInvalid):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REMOVE_CLIENTS,
                {"unexpected_param": "value"},
                blocking=True,
            )
        await async_unload_services(hass)

    async def test_remove_clients_classic_error_envelope_failure(
        self, hass: HomeAssistant
    ):
        """Test classic error response reports failure and forgets nothing."""
        for error_type in ["error", "failed", "missing_meta"]:
            coord = MagicMock()
            coord.is_network_usable = True
            coord.data = {"sites": {"site1": {}}}
            coord.async_get_historical_clients = AsyncMock(
                side_effect=HomeAssistantError(f"Classic API call failed: {error_type}")
            )
            coord.device_coordinator.async_forget_clients_batch = AsyncMock()
            entry = MagicMock(
                entry_id="entry1",
                title="Console 1",
                runtime_data=MagicMock(coordinator=coord),
            )

            await async_setup_services(hass)
            with (
                patch.object(
                    hass.config_entries, "async_entries", return_value=[entry]
                ),
                pytest.raises(HomeAssistantError) as exc_info,
            ):
                await hass.services.async_call(
                    DOMAIN,
                    SERVICE_REMOVE_CLIENTS,
                    {},
                    blocking=True,
                )

            assert exc_info.value.translation_key == "remove_clients_failed"
            assert f"Classic API call failed: {error_type}" in str(exc_info.value)
            coord.device_coordinator.async_forget_clients_batch.assert_not_called()
            await async_unload_services(hass)

    async def test_remove_clients_end_to_end_http_boundary(self, hass: HomeAssistant):
        """End-to-end service test mocking only the HTTP boundary."""
        session = _Session(
            [
                # 1. GET /api/s/alpha/stat/alluser
                {
                    "meta": {"rc": "ok"},
                    "data": [
                        # Candidate 1: anonymous seen for 300s
                        {
                            "mac": "11:22:33:44:55:01",
                            "first_seen": 100,
                            "last_seen": 400,
                        },
                        # Candidate 2: missing first_seen
                        {
                            "mac": "11:22:33:44:55:02",
                            "first_seen": None,
                            "last_seen": 400,
                        },
                        # Excluded: named client
                        {
                            "mac": "11:22:33:44:55:03",
                            "name": "Alice iPhone",
                            "first_seen": 100,
                            "last_seen": 400,
                        },
                        # Excluded: client with hostname
                        {
                            "mac": "11:22:33:44:55:04",
                            "hostname": "printer",
                            "first_seen": 100,
                            "last_seen": 400,
                        },
                        # Excluded: client with fixed IP
                        {
                            "mac": "11:22:33:44:55:05",
                            "fixed_ip": "10.0.0.1",
                            "first_seen": 100,
                            "last_seen": 400,
                        },
                        # Excluded: client seen for more than 900s
                        {
                            "mac": "11:22:33:44:55:06",
                            "first_seen": 100,
                            "last_seen": 1200,
                        },
                        # Excluded: malformed timestamp kept
                        {
                            "mac": "11:22:33:44:55:07",
                            "first_seen": "invalid",
                            "last_seen": 400,
                        },
                    ],
                },
                # 2. POST /api/s/alpha/cmd/stamgr
                {"meta": {"rc": "ok"}},
            ]
        )

        network_client = UniFiNetworkClient(
            auth=ApiKeyAuth(api_key="test-key"),
            base_url="https://192.168.1.1",
            connection_type=ConnectionType.LOCAL,
            session=session,  # type: ignore[arg-type]
        )

        mock_config_coordinator = MagicMock()
        mock_config_coordinator.network_available = True
        mock_config_coordinator.data = {
            "sites": {
                "site1": {
                    "id": "site1",
                    "name": "Office",
                    "internalReference": "alpha",
                }
            },
            # The published fuzzy mapping points elsewhere and must be ignored.
            "legacy_site_names": {"site1": "beta"},
        }
        mock_config_coordinator.get_site = mock_config_coordinator.data["sites"].get
        mock_config_coordinator.resolve_legacy_site_name = (
            UnifiConfigCoordinator.resolve_legacy_site_name.__get__(
                mock_config_coordinator
            )
        )

        mock_device_coordinator = MagicMock()
        mock_device_coordinator.data = {}
        # The real batch-forget owner command, so the request stays end to end.
        mock_device_coordinator.network_client = network_client
        mock_device_coordinator.config_coordinator = mock_config_coordinator
        mock_device_coordinator.async_forget_clients_batch = (
            UnifiDeviceCoordinator.async_forget_clients_batch.__get__(
                mock_device_coordinator
            )
        )

        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.title = "UDM Pro"
        entry.data = {}

        facade = UnifiFacadeCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=None,
            entry=entry,
            config_coordinator=mock_config_coordinator,
            device_coordinator=mock_device_coordinator,
            protect_coordinator=None,
        )

        entry.runtime_data = MagicMock(coordinator=facade)

        await async_setup_services(hass)
        with patch.object(hass.config_entries, "async_entries", return_value=[entry]):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REMOVE_CLIENTS,
                {},
                blocking=True,
            )

        assert len(session.requests) == 2
        req1 = session.requests[0]
        assert req1["method"] == "GET"
        assert (
            req1["url"] == "https://192.168.1.1/proxy/network/api/s/alpha/stat/alluser"
        )

        req2 = session.requests[1]
        assert req2["method"] == "POST"
        assert req2["url"] == "https://192.168.1.1/proxy/network/api/s/alpha/cmd/stamgr"
        assert req2["json"] == {
            "cmd": "forget-sta",
            "macs": ["11:22:33:44:55:01", "11:22:33:44:55:02"],
        }
        await async_unload_services(hass)


def test_is_wireless_client_helper():
    """Test all branches of _is_wireless_client helper."""
    assert _is_wireless_client({"type": ClientType.WIRELESS}) is True
    assert _is_wireless_client({"type": ClientType.WIRED}) is False
    assert _is_wireless_client({"type": "WIRELESS"}) is True
    assert _is_wireless_client({"type": "CLIENTTYPE.WIRELESS"}) is True
    assert _is_wireless_client({"type": " wireless "}) is True
    assert _is_wireless_client({"type": "UNKNOWN_WIRELESS"}) is False
    assert _is_wireless_client({"type": "WIRED"}) is False
    assert _is_wireless_client({"connection_type": "WIRELESS"}) is True
    assert _is_wireless_client({"connection_type": "WIRED"}) is False
    assert _is_wireless_client({"type": 123}) is False
    assert _is_wireless_client({}) is False


async def test_resolve_carrier_subscriber_target_foreign_registry_owner(
    hass: HomeAssistant,
) -> None:
    """Reject a target owned only by another integration registry entry."""
    other_entry = MockConfigEntry(domain="other_domain", entry_id="other_entry")
    other_entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    dev_entry = dev_reg.async_get_or_create(
        config_entry_id="other_entry",
        identifiers={(DOMAIN, "carrier_subscriber_sub_1")},
    )
    call = MagicMock()
    call.data = {"device_id": dev_entry.id}
    call.service = "carrier_suspend_subscriber"

    with pytest.raises(
        ServiceValidationError, match="Target config entry is not loaded"
    ) as exc_info:
        _resolve_carrier_subscriber_target(hass, call)

    assert exc_info.value.translation_key == "carrier_entry_not_loaded"


async def test_refresh_data_failure_is_translated(hass: HomeAssistant) -> None:
    """A failed refresh_data names the failed consoles in refresh_failed."""
    broken = MagicMock()
    broken.data = {"sites": {"site1": {}}}
    broken.async_refresh_or_raise = AsyncMock(
        side_effect=HomeAssistantError("Error refreshing UniFi Insights data: x")
    )
    broken_entry = MagicMock()
    broken_entry.title = "Upstairs console"
    broken_entry.runtime_data = MagicMock()
    broken_entry.runtime_data.coordinator = broken

    await async_setup_services(hass)
    with (
        patch.object(hass.config_entries, "async_entries", return_value=[broken_entry]),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await hass.services.async_call(DOMAIN, SERVICE_REFRESH_DATA, {}, blocking=True)

    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "refresh_failed"
    assert raised.value.translation_placeholders == {"targets": "Upstairs console"}
    await async_unload_services(hass)
