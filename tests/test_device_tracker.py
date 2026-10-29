# Copyright 2026 UniFi Insights contributors
"""Tests for UniFi Insights device tracker platform."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components.device_tracker import SourceType
from homeassistant.const import STATE_HOME, STATE_NOT_HOME
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api.exceptions import UniFiConnectionError

from custom_components.unifi_insights.const import (
    CONF_CLIENT_CONTROL,
    CONF_TRACK_WIFI_CLIENTS,
    CONF_TRACK_WIRED_CLIENTS,
    DOMAIN,
)
from custom_components.unifi_insights.device_tracker import (
    PARALLEL_UPDATES,
    UnifiClientTracker,
    _client_should_be_tracked,
    _get_client_type,
    _partition_connected_clients,
    async_setup_entry,
)
from tests.fixtures.assertions import patch_attribute, require_mock
from tests.fixtures.facade_data import (
    facade_data,
    frozen,
    set_availability,
    with_client,
    with_sections,
)
from tests.fixtures.library_responses import SAMPLE_WIRED_CLIENT

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import device_registry as dr

    from custom_components.unifi_insights.coordinators.facade import FacadeData


def _with_site_clients(
    data: Mapping[str, Any], site_id: str, clients: Any
) -> FacadeData:
    """Return ``data`` with one site's clients replaced."""
    return with_sections(
        data, clients=frozen({**data.get("clients", {}), site_id: clients})
    )


def _with_client_fields(
    data: Mapping[str, Any], site_id: str, client_id: str, **fields: Any
) -> FacadeData:
    """Return ``data`` with fields of one client's record replaced."""
    record = data["clients"][site_id][client_id]
    return with_client(data, site_id, client_id, {**record, **fields})


class TestParallelUpdates:
    """Test PARALLEL_UPDATES constant."""

    def test_parallel_updates_value(self) -> None:
        """Test that PARALLEL_UPDATES is set correctly."""
        assert PARALLEL_UPDATES == 0


class TestAsyncSetupEntry:
    """Tests for async_setup_entry function."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.protect_client = None
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {"site1": {"id": "site1", "meta": {"name": "Test Site"}}},
            "devices": {"site1": {}},
            "clients": {"site1": {}},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }
        return coordinator

    @pytest.mark.asyncio
    async def test_setup_entry_tracking_disabled(
        self, hass: HomeAssistant, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test setup when client tracking is disabled (default)."""
        mock_coordinator.data = _with_site_clients(
            mock_coordinator.data,
            "site1",
            {
                "client1": {
                    "id": "client1",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "name": "Test Client",
                    "connected": True,
                }
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.mobility_coordinator = None
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        # Tracking disabled by default (options is empty dict)
        mock_entry.options = {}

        # Initialize tracked clients set
        patch_attribute(monkeypatch, hass, "data", {})

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        # No entities should be added when tracking is disabled
        async_add_entities.assert_not_called()

    @pytest.mark.asyncio
    async def test_setup_entry_no_clients(self, hass, mock_coordinator) -> None:
        """Test setup when no clients present but tracking is enabled."""
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.mobility_coordinator = None
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        # Enable WiFi client tracking with new option
        mock_entry.options = {"track_wifi_clients": True}

        # Initialize tracked clients set
        hass.data = {}

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        # When no entities, async_add_entities is not called (only called if entities)
        async_add_entities.assert_not_called()

    @pytest.mark.asyncio
    async def test_setup_entry_with_clients(
        self, hass: HomeAssistant, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test setup with clients present and tracking enabled (wired client)."""
        mock_coordinator.data = _with_site_clients(
            mock_coordinator.data,
            "site1",
            {
                "client1": {
                    "id": "client1",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "name": "Test Client",
                    "connected": True,
                    "ipAddress": "192.168.1.100",
                    "hostname": "test-client",
                    "type": "WIRED",
                }
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.mobility_coordinator = None
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        # Enable wired client tracking with new option
        mock_entry.options = {"track_wired_clients": True}

        # Initialize tracked clients set
        patch_attribute(monkeypatch, hass, "data", {})

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], UnifiClientTracker)

    @pytest.mark.asyncio
    async def test_setup_entry_wifi_only_skips_wired(
        self, hass: HomeAssistant, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test setup with WiFi tracking only skips wired clients."""
        mock_coordinator.data = _with_site_clients(
            mock_coordinator.data,
            "site1",
            {
                "client1": {
                    "id": "client1",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "name": "Wired Client",
                    "connected": True,
                    "type": "WIRED",
                },
                "client2": {
                    "id": "client2",
                    "mac": "11:22:33:44:55:66",
                    "name": "WiFi Client",
                    "connected": True,
                    "type": "WIRELESS",
                },
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.mobility_coordinator = None
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        # Enable WiFi only tracking
        mock_entry.options = {"track_wifi_clients": True, "track_wired_clients": False}

        # Initialize tracked clients set
        patch_attribute(monkeypatch, hass, "data", {})

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        # Only WiFi client should be tracked
        assert len(entities) == 1
        assert entities[0]._mac == "11:22:33:44:55:66"

    @pytest.mark.asyncio
    async def test_setup_entry_wired_only_skips_wifi(
        self, hass: HomeAssistant, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test setup with wired tracking only skips WiFi clients."""
        mock_coordinator.data = _with_site_clients(
            mock_coordinator.data,
            "site1",
            {
                "client1": {
                    "id": "client1",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "name": "Wired Client",
                    "connected": True,
                    "type": "WIRED",
                },
                "client2": {
                    "id": "client2",
                    "mac": "11:22:33:44:55:66",
                    "name": "WiFi Client",
                    "connected": True,
                    "type": "WIRELESS",
                },
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.mobility_coordinator = None
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        # Enable wired only tracking
        mock_entry.options = {"track_wifi_clients": False, "track_wired_clients": True}

        # Initialize tracked clients set
        patch_attribute(monkeypatch, hass, "data", {})

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        # Only wired client should be tracked
        assert len(entities) == 1
        assert entities[0]._mac == "aa:bb:cc:dd:ee:ff"

    @pytest.mark.asyncio
    async def test_setup_entry_both_client_types(
        self, hass: HomeAssistant, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test setup with both client types tracking enabled."""
        mock_coordinator.data = _with_site_clients(
            mock_coordinator.data,
            "site1",
            {
                "client1": {
                    "id": "client1",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "name": "Wired Client",
                    "connected": True,
                    "type": "WIRED",
                },
                "client2": {
                    "id": "client2",
                    "mac": "11:22:33:44:55:66",
                    "name": "WiFi Client",
                    "connected": True,
                    "type": "WIRELESS",
                },
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.mobility_coordinator = None
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        # Enable both tracking options
        mock_entry.options = {"track_wifi_clients": True, "track_wired_clients": True}

        # Initialize tracked clients set
        patch_attribute(monkeypatch, hass, "data", {})

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        # Both clients should be tracked
        assert len(entities) == 2

    @pytest.mark.asyncio
    async def test_setup_entry_old_option_migration(
        self, hass: HomeAssistant, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test setup migrates old track_clients option to new options."""
        mock_coordinator.data = _with_site_clients(
            mock_coordinator.data,
            "site1",
            {
                "client1": {
                    "id": "client1",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "name": "Wired Client",
                    "connected": True,
                    "type": "WIRED",
                },
                "client2": {
                    "id": "client2",
                    "mac": "11:22:33:44:55:66",
                    "name": "WiFi Client",
                    "connected": True,
                    "type": "WIRELESS",
                },
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.mobility_coordinator = None
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        # Use old option - should track all clients as fallback
        mock_entry.options = {"track_clients": True}

        # Initialize tracked clients set
        patch_attribute(monkeypatch, hass, "data", {})

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        # Both clients should be tracked with old option migration
        assert len(entities) == 2

    @pytest.mark.asyncio
    async def test_setup_entry_multiple_sites(
        self, hass: HomeAssistant, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test setup with clients from multiple sites and tracking enabled."""
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            sites={
                **mock_coordinator.data["sites"],
                "site2": {"id": "site2", "meta": {"name": "Site 2"}},
            },
        )
        mock_coordinator.data = _with_site_clients(
            mock_coordinator.data,
            "site1",
            {
                "client1": {
                    "id": "client1",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "name": "Client 1",
                    "connected": True,
                    "type": "WIRED",
                }
            },
        )
        mock_coordinator.data = _with_site_clients(
            mock_coordinator.data,
            "site2",
            {
                "client2": {
                    "id": "client2",
                    "mac": "11:22:33:44:55:66",
                    "name": "Client 2",
                    "connected": False,
                    "type": "WIRELESS",
                }
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.mobility_coordinator = None
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        # Enable both client tracking options
        mock_entry.options = {"track_wifi_clients": True, "track_wired_clients": True}

        # Initialize tracked clients set
        patch_attribute(monkeypatch, hass, "data", {})

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 2

    @pytest.mark.asyncio
    async def test_setup_entry_skips_already_tracked_clients(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test the per-setup dedup set prevents duplicate tracker entities."""
        mock_coordinator.data = _with_site_clients(
            mock_coordinator.data,
            "site1",
            {
                "client1": {
                    "id": "client1",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "name": "First Client",
                    "connected": True,
                    "type": "WIRED",
                },
                "client2": {
                    "id": "client2",
                    "mac": "11:22:33:44:55:66",
                    "name": "Second Client",
                    "connected": True,
                    "type": "WIRED",
                },
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.mobility_coordinator = None
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        # Enable wired tracking
        mock_entry.options = {"track_wifi_clients": False, "track_wired_clients": True}

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        # Fresh setup adds trackers for every connected client (the dedup set
        # is local to the setup call, so a reload re-adds entities).
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 2

        # Re-running the registered coordinator listener must not create
        # duplicates for clients that are already tracked in this setup.
        listener = mock_coordinator.async_add_listener.call_args[0][0]
        listener()
        assert async_add_entities.call_count == 1


class TestUnifiClientTracker:
    """Tests for UnifiClientTracker entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.protect_client = None
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.last_update_success = True
        coordinator.data = {
            "sites": {"site1": {"id": "site1", "meta": {"name": "Test Site"}}},
            "devices": {"site1": {}},
            "clients": {
                "site1": {
                    "client1": {
                        "id": "client1",
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "name": "Test Client",
                        "connected": True,
                        "ipAddress": "192.168.1.100",
                        "hostname": "test-client",
                        "type": "WIRED",
                    }
                }
            },
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }
        return coordinator

    def test_initialization(self, mock_coordinator) -> None:
        """Test tracker initialization."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        assert tracker._site_id == "site1"
        assert tracker._mac == "aa:bb:cc:dd:ee:ff"

    def test_unique_id(self, mock_coordinator) -> None:
        """Test unique ID is set correctly."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        # Unique ID uses the normalized (lowercase) MAC address
        assert tracker._attr_unique_id is not None
        assert "aa:bb:cc:dd:ee:ff" in tracker._attr_unique_id

    def test_source_type_wired(self, mock_coordinator) -> None:
        """Test source type for wired client."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        assert tracker.source_type == SourceType.ROUTER

    def test_source_type_wireless(self, mock_coordinator: MagicMock) -> None:
        """Test source type for wireless client."""
        mock_coordinator.data = _with_client_fields(
            mock_coordinator.data, "site1", "client1", type="WIRELESS"
        )

        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        assert tracker.source_type == SourceType.ROUTER

    def test_is_connected_online(self, mock_coordinator: MagicMock) -> None:
        """Test is_connected for online client."""
        # Ensure connected field is True
        mock_coordinator.data = _with_client_fields(
            mock_coordinator.data, "site1", "client1", connected=True
        )

        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        assert tracker.is_connected is True

    def test_is_connected_missing_connected_key(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test is_connected for a present client record without a connected key."""
        record = dict(mock_coordinator.data["clients"]["site1"]["client1"])
        record.pop("connected", None)
        mock_coordinator.data = with_client(
            mock_coordinator.data, "site1", "client1", record
        )

        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        assert tracker.is_connected is True

    def test_is_connected_none_connected_value(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test is_connected for a present client record with connected=None."""
        mock_coordinator.data = _with_client_fields(
            mock_coordinator.data, "site1", "client1", connected=None
        )

        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        assert tracker.is_connected is True

    def test_is_connected_offline(self, mock_coordinator: MagicMock) -> None:
        """Test is_connected for offline client."""
        mock_coordinator.data = _with_client_fields(
            mock_coordinator.data, "site1", "client1", connected=False
        )

        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        assert tracker.is_connected is False

    def test_is_connected_missing_client(self, mock_coordinator: MagicMock) -> None:
        """Test is_connected when client data is missing."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        # Remove client data
        mock_coordinator.data = _with_site_clients(mock_coordinator.data, "site1", {})

        assert tracker.is_connected is False

    def test_available(self, mock_coordinator) -> None:
        """Test entity availability based on coordinator update success."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        # Default: coordinator last_update_success is True
        assert tracker.available is True

        # Coordinator fails update
        mock_coordinator.last_update_success = False
        assert tracker.available is False

    def test_available_while_device_refresh_fails(self, mock_coordinator) -> None:
        """A failing device refresh keeps the tracker's last home/not_home.

        person ignores unavailable trackers and goes unknown when it has no
        other tracker (#116), so the tracker must not follow device_available.
        """
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )
        mock_coordinator.last_update_success = True
        set_availability(mock_coordinator, device=False)

        assert tracker.available is True

    def test_ip_address(self, mock_coordinator) -> None:
        """Test IP address property."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        assert tracker.ip_address == "192.168.1.100"

    def test_mac_address(self, mock_coordinator) -> None:
        """Test MAC address property is the normalised MAC the tracker holds."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        assert tracker.mac_address == "aa:bb:cc:dd:ee:ff"

    def test_hostname(self, mock_coordinator) -> None:
        """Test hostname property."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        assert tracker.hostname == "test-client"

    def test_extra_state_attributes(self, mock_coordinator: MagicMock) -> None:
        """Test extra state attributes."""
        mock_coordinator.data = _with_client_fields(
            mock_coordinator.data,
            "site1",
            "client1",
            type="WIRELESS",
            uplinkDeviceId="device123",
            essid="TestWiFi",
            channel=36,
            rssi=-45,
        )

        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        attrs = tracker.extra_state_attributes
        assert attrs is not None
        assert attrs["connection_type"] == "WIRELESS"

    def test_device_info(self, mock_coordinator) -> None:
        """Test device info is set correctly."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        device_info = tracker.device_info
        assert device_info is not None
        assert device_info.get("manufacturer") == "Ubiquiti Inc."

    def test_trackers_are_enabled_by_default(self, mock_coordinator: MagicMock) -> None:
        """Opted-in client trackers stay enabled on Home Assistant 2026.11+."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator, site_id="site1", mac="AA:BB:CC:DD:EE:FF"
        )
        assert tracker._attr_entity_registry_enabled_default is True

    def test_grouped_tracker_follows_client_rename(
        self, mock_coordinator: MagicMock
    ) -> None:
        """A tracker under its uplink takes the new client name itself."""
        mock_coordinator.data = _with_client_fields(
            mock_coordinator.data, "site1", "client1", uplinkDeviceId="ap1"
        )
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator, site_id="site1", mac="AA:BB:CC:DD:EE:FF"
        )
        assert tracker._attr_name == "Test Client"
        mock_coordinator.data = _with_client_fields(
            mock_coordinator.data, "site1", "client1", name="Renamed"
        )

        tracker._async_follow_client_rename()

        assert tracker._attr_name == "Renamed"

    def test_standalone_rename_updates_only_the_client_device(
        self, mock_coordinator: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A standalone tracker renames its own client_ device, nothing else."""
        registry = MagicMock()
        monkeypatch.setattr(
            "custom_components.unifi_insights.device_tracker.dr.async_get",
            lambda _hass: registry,
        )
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator, site_id="site1", mac="AA:BB:CC:DD:EE:FF"
        )
        client_device = MagicMock(
            id="device1", identifiers={(DOMAIN, "client_aa:bb:cc:dd:ee:ff")}
        )
        client_device.name = "Old name"
        other_device = MagicMock(id="device2", identifiers={(DOMAIN, "site1_ap1")})
        other_device.name = "Old name"
        same_name = MagicMock(
            id="device3", identifiers={(DOMAIN, "client_aa:bb:cc:dd:ee:ff")}
        )
        same_name.name = "Test Client"

        # Not added to Home Assistant yet, no device, another device, same name.
        tracker._async_follow_client_rename()
        tracker.hass = MagicMock()
        for device in (None, other_device, same_name):
            tracker.device_entry = device
            tracker._async_follow_client_rename()
        registry.async_update_device.assert_not_called()

        tracker.device_entry = client_device
        tracker._async_follow_client_rename()

        registry.async_update_device.assert_called_once_with(
            "device1", name="Test Client"
        )
        assert tracker._attr_name is None

    def test_absent_client_keeps_its_name(self, mock_coordinator: MagicMock) -> None:
        """A client missing from the snapshot is not renamed."""
        mock_coordinator.data = _with_client_fields(
            mock_coordinator.data, "site1", "client1", uplinkDeviceId="ap1"
        )
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator, site_id="site1", mac="AA:BB:CC:DD:EE:FF"
        )
        mock_coordinator.data = _with_site_clients(mock_coordinator.data, "site1", {})

        tracker._async_follow_client_rename()

        assert tracker._attr_name == "Test Client"

    def test_coordinator_update_follows_rename_then_writes_state(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Each coordinator update applies the rename before writing state."""
        mock_coordinator.data = _with_client_fields(
            mock_coordinator.data, "site1", "client1", uplinkDeviceId="ap1"
        )
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator, site_id="site1", mac="AA:BB:CC:DD:EE:FF"
        )
        mock_coordinator.data = _with_client_fields(
            mock_coordinator.data, "site1", "client1", name="Renamed"
        )
        written: list[str | None] = []
        tracker.async_write_ha_state = lambda: written.append(tracker._attr_name)  # type: ignore[method-assign]

        tracker._handle_coordinator_update()

        assert written == ["Renamed"]


class TestGetClientType:
    """Tests for _get_client_type helper function."""

    def test_get_client_type_wired(self) -> None:
        """Test _get_client_type returns WIRED for wired clients."""
        result = _get_client_type({"type": "WIRED"})
        assert result == "WIRED"

    def test_get_client_type_wireless(self) -> None:
        """Test _get_client_type returns WIRELESS for wireless clients."""
        result = _get_client_type({"type": "WIRELESS"})
        assert result == "WIRELESS"

    def test_get_client_type_unknown(self) -> None:
        """Test _get_client_type returns original type for unknown types."""
        result = _get_client_type({"type": "UNKNOWN_TYPE"})
        assert result == "UNKNOWN_TYPE"

    def test_get_client_type_empty_string(self) -> None:
        """Test _get_client_type returns empty string when no type."""
        result = _get_client_type({"type": ""})
        assert result == ""

    def test_get_client_type_connection_type_field(self) -> None:
        """Test _get_client_type uses connection_type field as fallback."""
        result = _get_client_type({"connection_type": "WIRED"})
        assert result == "WIRED"

    def test_get_client_type_no_type_field(self) -> None:
        """Test _get_client_type returns empty string when no type field."""
        result = _get_client_type({})
        assert result == ""

    def test_client_should_be_tracked_unknown_client_type(self) -> None:
        """Test _client_should_be_tracked for unknown client type."""
        client = {"type": "UNKNOWN"}
        assert (
            _client_should_be_tracked(client, track_wifi=True, track_wired=False)
            is True
        )
        assert (
            _client_should_be_tracked(client, track_wifi=False, track_wired=True)
            is True
        )
        assert (
            _client_should_be_tracked(client, track_wifi=False, track_wired=False)
            is False
        )

    def test_partition_connected_clients_skips_non_dict_site_clients(self) -> None:
        """Test _partition_connected_clients skips non-dict site entries."""
        coordinator = MagicMock()
        coordinator.data = {
            "clients": {
                "site1": None,
                "site2": "invalid",
                "site3": [1, 2, 3],
                "site4": {
                    "client1": {
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "type": "WIRELESS",
                    }
                },
            }
        }
        wanted, untracked = _partition_connected_clients(
            coordinator, track_wifi=True, track_wired=True
        )
        assert wanted == {"aa:bb:cc:dd:ee:ff": "site4"}
        assert untracked == set()


class TestUnifiClientTrackerEdgeCases:
    """Test edge cases for UnifiClientTracker."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.protect_client = None
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.last_update_success = True
        coordinator.data = {
            "sites": {"site1": {"id": "site1", "meta": {"name": "Test Site"}}},
            "devices": {"site1": {"device1": {"id": "device1", "name": "Device"}}},
            "clients": {
                "site1": {
                    "client1": {
                        "id": "client1",
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "macAddress": "AA:BB:CC:DD:EE:FF",
                        "name": "Test Client",
                        "hostname": "test-client",
                        "connected": True,
                        "type": "WIRELESS",
                    }
                }
            },
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }
        return coordinator

    def test_source_type_no_client_data(self, mock_coordinator: MagicMock) -> None:
        """Test source_type returns ROUTER when client data is missing."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        # Remove client data
        mock_coordinator.data = _with_site_clients(mock_coordinator.data, "site1", {})

        assert tracker.source_type == SourceType.ROUTER

    def test_ip_address_no_client_data(self, mock_coordinator: MagicMock) -> None:
        """Test ip_address returns None when client data is missing."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        # Remove client data
        mock_coordinator.data = _with_site_clients(mock_coordinator.data, "site1", {})

        assert tracker.ip_address is None

    def test_mac_address_no_client_data(self, mock_coordinator: MagicMock) -> None:
        """Test mac_address survives the client vanishing from the snapshot."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        # Remove client data
        mock_coordinator.data = _with_site_clients(mock_coordinator.data, "site1", {})

        # The MAC identifies the client; it is not a live reading. An absent
        # client is exactly the case a restored tracker exists to report on,
        # so dropping its identity here would defeat the purpose.
        assert tracker.mac_address == "aa:bb:cc:dd:ee:ff"

    def test_hostname_no_client_data(self, mock_coordinator: MagicMock) -> None:
        """Test hostname returns None when client data is missing."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        # Remove client data
        mock_coordinator.data = _with_site_clients(mock_coordinator.data, "site1", {})

        assert tracker.hostname is None

    def test_extra_state_attributes_no_client_data(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test extra_state_attributes returns empty dict when missing."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        # Remove client data
        mock_coordinator.data = _with_site_clients(mock_coordinator.data, "site1", {})

        assert tracker.extra_state_attributes == {}

    async def test_setup_skips_clients_without_mac(
        self, hass: HomeAssistant, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Clients without a MAC cannot be identified and are not tracked."""
        mock_coordinator.data = _with_site_clients(
            mock_coordinator.data,
            "site1",
            {
                "client_no_mac": {
                    "id": "client_no_mac",
                    "name": "Client Without MAC",
                    "connected": True,
                    "type": "WIRED",
                }
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.mobility_coordinator = None
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.async_on_unload = MagicMock()
        mock_entry.options = {"track_wired_clients": True}

        patch_attribute(monkeypatch, hass, "data", {})
        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        # No MAC means no stable identity, so no tracker is created.
        async_add_entities.assert_not_called()

    async def test_async_added_to_hass(
        self, mock_coordinator, hass, monkeypatch
    ) -> None:
        """Test async_added_to_hass registers listener."""
        mock_coordinator.async_add_listener = MagicMock(return_value=MagicMock())

        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )
        tracker.hass = hass

        # Create a mock async_on_remove
        remove_callbacks = []
        patch_attribute(
            monkeypatch, tracker, "async_on_remove", remove_callbacks.append
        )

        await tracker.async_added_to_hass()

        # Verify listener was added
        mock_coordinator.async_add_listener.assert_called_once()

    def test_handle_coordinator_update(
        self, mock_coordinator, hass, monkeypatch
    ) -> None:
        """Test _handle_coordinator_update writes state."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )
        tracker.hass = hass

        # Mock async_write_ha_state
        patch_attribute(monkeypatch, tracker, "async_write_ha_state", MagicMock())

        tracker._handle_coordinator_update()

        require_mock(tracker.async_write_ha_state).assert_called_once()

    def test_get_client_data_with_invalid_non_dict_entry(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test _get_client_data skips non-dictionary entries gracefully."""
        mock_coordinator.data = _with_site_clients(
            mock_coordinator.data,
            "site1",
            {
                "invalid_client": None,
                "invalid_client_str": "not-a-dict",
                "valid_client": {
                    "id": "valid_client",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "name": "Valid Client",
                    "connected": True,
                },
            },
        )

        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="AA:BB:CC:DD:EE:FF",
        )

        client_data = tracker._get_client_data()
        assert client_data is not None

        assert client_data["id"] == "valid_client"
        assert tracker.is_connected is True

    @pytest.mark.parametrize(
        "site_clients",
        [None, "invalid", [1, 2, 3], {"bad": "not-a-record"}],
        ids=["none", "string", "list", "malformed-record"],
    )
    def test_get_client_data_skips_a_malformed_site(
        self, mock_coordinator, site_clients: Any
    ) -> None:
        """A malformed hinted site is skipped and the client found in another."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="aa:bb:cc:dd:ee:ff",
        )
        mock_coordinator.data = with_client(
            facade_data(clients={"site1": site_clients}),
            "site2",
            "c1",
            {"id": "c1", "macAddress": "AA:BB:CC:DD:EE:FF"},
        )

        client_data = tracker._get_client_data()

        assert client_data is not None
        assert client_data["id"] == "c1"
        assert tracker._site_id == "site2"

    def test_get_client_data_all_clients_not_dict(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test _get_client_data returns None when clients in data is not a dict."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            site_id="site1",
            mac="aa:bb:cc:dd:ee:ff",
        )
        mock_coordinator.data = with_sections(mock_coordinator.data, clients=None)
        assert tracker._get_client_data() is None

        mock_coordinator.data = with_sections(
            mock_coordinator.data, clients="not_a_dict"
        )
        assert tracker._get_client_data() is None


class TestRegistryReconciliation:
    """Registry reconciliation on setup (upstream issue #116).

    A tracker may only be deleted because the *configuration* no longer wants it,
    never because the client is missing from the current snapshot. Deletion is
    permanent and takes the user's name/area/entity_id customisation with it.
    """

    OFFLINE_MAC: str = "aa:bb:cc:dd:ee:01"
    WIRED_MAC: str = "aa:bb:cc:dd:ee:02"
    WIFI_MAC: str = "aa:bb:cc:dd:ee:03"

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with an empty client snapshot."""
        coordinator = MagicMock()
        coordinator.data = {"clients": {"site1": {}}}
        return coordinator

    @staticmethod
    def _entry(
        hass: HomeAssistant, coordinator: MagicMock, options: dict[str, Any]
    ) -> MockConfigEntry:
        """Build a config entry wired to the coordinator."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"connection_type": "remote", "console_id": "c", "api_key": "k"},
            options=options,
            entry_id="tracker_reconcile_entry",
        )
        entry.add_to_hass(hass)
        entry.runtime_data = MagicMock()
        entry.runtime_data.mobility_coordinator = None
        entry.runtime_data.coordinator = coordinator
        return entry

    @staticmethod
    def _register(
        entity_registry: er.EntityRegistry, entry: MockConfigEntry, mac: str
    ) -> str:
        """Register an existing client tracker and return its entity_id."""
        return entity_registry.async_get_or_create(
            "device_tracker",
            DOMAIN,
            f"{DOMAIN}_{mac}",
            config_entry=entry,
            suggested_object_id=f"client_{mac.replace(':', '')}",
        ).entity_id

    @staticmethod
    def _client(mac: str, client_type: str) -> dict[str, Any]:
        """Build a connected-client payload."""
        return {"id": mac, "mac": mac, "connected": True, "type": client_type}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "data",
        [
            # Site polled fine, this client is simply not connected right now.
            pytest.param({"clients": {"site1": {}}}, id="client_offline"),
            # No sites at all: a poll that failed or has not run yet. This is the
            # case that wiped every client tracker in issue #116.
            pytest.param({"clients": {}}, id="poll_returned_nothing"),
            pytest.param({}, id="coordinator_has_no_data"),
        ],
    )
    async def test_absent_client_keeps_its_registry_entry(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_coordinator: MagicMock,
        data: dict[str, Any],
    ) -> None:
        """A client missing from the snapshot must not lose its registry entry."""
        mock_coordinator.data = data

        entry = self._entry(hass, mock_coordinator, {"track_wifi_clients": True})
        entity_id = self._register(entity_registry, entry, self.OFFLINE_MAC)

        await async_setup_entry(hass, entry, MagicMock())

        assert entity_registry.async_get(entity_id) is not None

    @pytest.mark.asyncio
    async def test_connected_client_of_untracked_type_is_removed(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_coordinator: MagicMock,
    ) -> None:
        """A connected client whose type is no longer tracked is still removed."""
        mock_coordinator.data = _with_site_clients(
            mock_coordinator.data,
            "site1",
            {"c1": self._client(self.WIRED_MAC, "WIRED")},
        )

        entry = self._entry(hass, mock_coordinator, {"track_wifi_clients": True})
        entity_id = self._register(entity_registry, entry, self.WIRED_MAC)

        await async_setup_entry(hass, entry, MagicMock())

        assert entity_registry.async_get(entity_id) is None

    @pytest.mark.asyncio
    async def test_connected_tracked_client_is_kept(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_coordinator: MagicMock,
    ) -> None:
        """A connected client of a tracked type keeps its registry entry."""
        mock_coordinator.data = _with_site_clients(
            mock_coordinator.data,
            "site1",
            {"c1": self._client(self.WIFI_MAC, "WIRELESS")},
        )

        entry = self._entry(hass, mock_coordinator, {"track_wifi_clients": True})
        entity_id = self._register(entity_registry, entry, self.WIFI_MAC)

        await async_setup_entry(hass, entry, MagicMock())

        assert entity_registry.async_get(entity_id) is not None

    @pytest.mark.asyncio
    async def test_legacy_raw_mac_entry_is_rekeyed(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_coordinator: MagicMock,
    ) -> None:
        """Trackers registered under the bare MAC are renamed, not orphaned."""
        entry = self._entry(hass, mock_coordinator, {"track_wifi_clients": True})
        entity_id = entity_registry.async_get_or_create(
            "device_tracker",
            DOMAIN,
            self.OFFLINE_MAC.upper(),
            config_entry=entry,
        ).entity_id

        await async_setup_entry(hass, entry, MagicMock())

        migrated = entity_registry.async_get(entity_id)
        assert migrated is not None
        assert migrated.unique_id == f"{DOMAIN}_{self.OFFLINE_MAC}"

    @pytest.mark.asyncio
    async def test_rekey_is_skipped_when_target_is_taken(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_coordinator: MagicMock,
    ) -> None:
        """A legacy entry is left alone if the migrated id already exists."""
        entry = self._entry(hass, mock_coordinator, {"track_wifi_clients": True})
        self._register(entity_registry, entry, self.OFFLINE_MAC)
        legacy_id = entity_registry.async_get_or_create(
            "device_tracker",
            DOMAIN,
            self.OFFLINE_MAC.upper(),
            config_entry=entry,
        ).entity_id

        async_add_entities = MagicMock()
        await async_setup_entry(hass, entry, async_add_entities)

        legacy = entity_registry.async_get(legacy_id)
        assert legacy is not None
        assert legacy.unique_id == self.OFFLINE_MAC.upper()

        # The unmigrated legacy entry must still receive a live, functional tracker
        # rather than being skipped and left permanently unavailable.
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 2
        legacy_tracker = next(
            t for t in entities if t.unique_id == self.OFFLINE_MAC.upper()
        )
        assert legacy_tracker.is_connected is False
        assert legacy_tracker.available is True

    @pytest.mark.asyncio
    async def test_rekey_collision_across_config_entries_is_skipped(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_coordinator: MagicMock,
    ) -> None:
        """Cross-entry collision avoids ValueError and preserves legacy tracker."""
        other_entry = MockConfigEntry(
            version=1,
            minor_version=0,
            domain=DOMAIN,
            entry_id="other_entry",
        )
        other_entry.add_to_hass(hass)
        entity_registry.async_get_or_create(
            "device_tracker",
            DOMAIN,
            f"{DOMAIN}_{self.OFFLINE_MAC}",
            config_entry=other_entry,
        )

        entry = self._entry(hass, mock_coordinator, {"track_wifi_clients": True})
        legacy_id = entity_registry.async_get_or_create(
            "device_tracker",
            DOMAIN,
            self.OFFLINE_MAC.upper(),
            config_entry=entry,
        ).entity_id

        async_add_entities = MagicMock()
        # Must not raise ValueError: Unique id ... already in use
        await async_setup_entry(hass, entry, async_add_entities)

        legacy = entity_registry.async_get(legacy_id)
        assert legacy is not None
        assert legacy.unique_id == self.OFFLINE_MAC.upper()

        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        tracker = entities[0]
        assert tracker.unique_id == self.OFFLINE_MAC.upper()
        assert tracker.is_connected is False
        assert tracker.available is True

    @pytest.mark.asyncio
    async def test_retained_entry_gets_a_live_tracker(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_coordinator: MagicMock,
    ) -> None:
        """A surviving registry entry is restored as a real, available entity."""
        entry = self._entry(hass, mock_coordinator, {"track_wifi_clients": True})
        self._register(entity_registry, entry, self.OFFLINE_MAC)

        async_add_entities = MagicMock()
        await async_setup_entry(hass, entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        tracker = entities[0]
        assert isinstance(tracker, UnifiClientTracker)
        assert tracker.unique_id == f"{DOMAIN}_{self.OFFLINE_MAC}"
        # not_home (absent but reporting), not unavailable (no entity at all).
        assert tracker.is_connected is False
        assert tracker.available is True

    def test_retained_tracker_finds_client_on_any_site(
        self, mock_coordinator: MagicMock
    ) -> None:
        """With no site hint, the MAC is resolved by scanning every site."""
        tracker = UnifiClientTracker(
            coordinator=mock_coordinator,
            mac=self.WIFI_MAC,
            site_id=None,
        )

        assert tracker.is_connected is False

        # The client turns up on a site the tracker was never pointed at.
        mock_coordinator.data = _with_site_clients(
            mock_coordinator.data,
            "site2",
            {"c1": self._client(self.WIFI_MAC, "WIRELESS")},
        )

        assert tracker.is_connected is True
        assert tracker._site_id == "site2"

    @pytest.mark.asyncio
    async def test_reconnecting_retained_client_is_not_added_twice(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_coordinator: MagicMock,
    ) -> None:
        """A retained MAC reconnecting must not create a duplicate unique_id."""
        entry = self._entry(hass, mock_coordinator, {"track_wifi_clients": True})
        self._register(entity_registry, entry, self.WIFI_MAC)

        async_add_entities = MagicMock()
        await async_setup_entry(hass, entry, async_add_entities)

        added = [
            entity
            for call in async_add_entities.call_args_list
            for entity in call[0][0]
        ]
        assert len(added) == 1

        # The retained client comes back; the coordinator listener re-runs.
        mock_coordinator.data = _with_site_clients(
            mock_coordinator.data,
            "site1",
            {"c1": self._client(self.WIFI_MAC, "WIRELESS")},
        )
        listener = mock_coordinator.async_add_listener.call_args[0][0]
        listener()

        added = [
            entity
            for call in async_add_entities.call_args_list
            for entity in call[0][0]
        ]
        assert [entity.unique_id for entity in added] == [f"{DOMAIN}_{self.WIFI_MAC}"]

    @pytest.mark.asyncio
    async def test_retained_tracker_keeps_its_name(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_coordinator: MagicMock,
    ) -> None:
        """Restoring an absent client keeps its name, not "Client <mac>"."""
        entry = self._entry(hass, mock_coordinator, {"track_wifi_clients": True})
        entity_registry.async_get_or_create(
            "device_tracker",
            DOMAIN,
            f"{DOMAIN}_{self.OFFLINE_MAC}",
            config_entry=entry,
            original_name="Kitchen Tablet",
        )

        async_add_entities = MagicMock()
        await async_setup_entry(hass, entry, async_add_entities)

        tracker = async_add_entities.call_args[0][0][0]
        # An absent client has no uplink to group under, so it gets a standalone
        # device that represents the client itself. The device carries the name
        # and the entity has none -- see test_offline_tracker_name_is_not_doubled.
        assert tracker.device_info["name"] == "Kitchen Tablet"
        assert tracker.device_info["name"] != f"Client {self.OFFLINE_MAC}"
        assert tracker.name is None

    @pytest.mark.asyncio
    async def test_offline_tracker_name_is_not_doubled(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_coordinator: MagicMock,
    ) -> None:
        """An absent client renders its name once, not "Tablet Tablet"."""
        entry = self._entry(hass, mock_coordinator, {"track_wifi_clients": True})
        entity_registry.async_get_or_create(
            "device_tracker",
            DOMAIN,
            f"{DOMAIN}_{self.OFFLINE_MAC}",
            config_entry=entry,
            original_name="Kitchen Tablet",
        )

        async_add_entities = MagicMock()
        await async_setup_entry(hass, entry, async_add_entities)

        tracker = async_add_entities.call_args[0][0][0]
        # `has_entity_name` composes "<device name> <entity name>". Setting both
        # to the client name is what produced "Kitchen Tablet Kitchen Tablet",
        # and an offline client takes this path on every start.
        assert tracker.has_entity_name is True
        assert tracker.name is None, (
            "entity must not repeat the name its standalone device already has"
        )

    @pytest.mark.asyncio
    async def test_offline_tracker_keeps_name_when_registry_name_is_gone(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        device_registry: dr.DeviceRegistry,
        mock_coordinator: MagicMock,
    ) -> None:
        """A second offline start reads the name back off the client device."""
        entry = self._entry(hass, mock_coordinator, {"track_wifi_clients": True})
        # State after one offline start: the entity has no name of its own, the
        # standalone client device holds it.
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"client_{self.OFFLINE_MAC}")},
            name="Kitchen Tablet",
        )
        entity_registry.async_get_or_create(
            "device_tracker",
            DOMAIN,
            f"{DOMAIN}_{self.OFFLINE_MAC}",
            config_entry=entry,
            original_name=None,
        )

        async_add_entities = MagicMock()
        await async_setup_entry(hass, entry, async_add_entities)

        tracker = async_add_entities.call_args[0][0][0]
        # Without the device-registry fallback this degrades to "Client <mac>".
        assert tracker.device_info["name"] == "Kitchen Tablet"

    @pytest.mark.asyncio
    async def test_tracking_disabled_removes_all_trackers(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_coordinator: MagicMock,
    ) -> None:
        """Turning tracking off is a config decision, so it may remove everything."""
        entry = self._entry(hass, mock_coordinator, {})
        offline = self._register(entity_registry, entry, self.OFFLINE_MAC)
        wifi = self._register(entity_registry, entry, self.WIFI_MAC)

        await async_setup_entry(hass, entry, MagicMock())

        assert entity_registry.async_get(offline) is None
        assert entity_registry.async_get(wifi) is None


class TestEntityPlatformRestoration:
    """End-to-end tests using Home Assistant's actual entity platform."""

    @pytest.mark.asyncio
    async def test_entity_platform_reports_not_home_then_home(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_config_entry: MockConfigEntry,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        *,
        mock_local_auth: MagicMock,
        enable_custom_integrations: None,
    ) -> None:
        """Original entity_id reports not_home offline, then home after reconnect."""
        mac = "aa:bb:cc:dd:ee:22"
        mock_config_entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(
            mock_config_entry, options={CONF_TRACK_WIFI_CLIENTS: True}
        )

        reg_entry = entity_registry.async_get_or_create(
            "device_tracker",
            DOMAIN,
            f"{DOMAIN}_{mac}",
            config_entry=mock_config_entry,
            suggested_object_id="test_phone",
        )
        entity_id = reg_entry.entity_id

        # Setup sites so coordinator has a valid site
        mock_network_client.sites.get_all.return_value = [
            {"id": "default", "name": "Default"}
        ]
        # Client is initially absent from the network snapshot
        mock_network_client.clients.get_all.return_value = []

        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # HA entity platform reports not_home, NOT unavailable
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_NOT_HOME

        # Client reconnects to the network
        client_data = {
            "id": "client_22",
            "macAddress": mac,
            "name": "Test Phone",
            "hostname": "test-phone",
            "type": "WIRELESS",
            "connected": True,
            "uplinkDeviceId": None,
            "ipAddress": "192.168.1.122",
        }
        mock_network_client.clients.get_all.return_value = [client_data]

        data = mock_config_entry.runtime_data
        await data.device_coordinator.async_refresh()
        await hass.async_block_till_done()

        # HA entity platform reports home
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_HOME

        # Simulate restart/reload with client absent again (live restart test)
        mock_network_client.clients.get_all.return_value = []
        await hass.config_entries.async_reload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_NOT_HOME

    @pytest.mark.asyncio
    async def test_entity_platform_wired_client_lifecycle(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_config_entry: MockConfigEntry,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        *,
        mock_local_auth: MagicMock,
        enable_custom_integrations: None,
    ) -> None:
        """Wired client reports home from connected listing, not_home when absent."""
        mac = SAMPLE_WIRED_CLIENT["macAddress"].lower()
        mock_config_entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(
            mock_config_entry,
            options={
                CONF_TRACK_WIRED_CLIENTS: True,
                CONF_CLIENT_CONTROL: True,
            },
        )

        reg_entry = entity_registry.async_get_or_create(
            "device_tracker",
            DOMAIN,
            f"{DOMAIN}_{mac}",
            config_entry=mock_config_entry,
            suggested_object_id="test_wired",
        )
        entity_id = reg_entry.entity_id

        mock_network_client.sites.get_all.return_value = [
            {"id": "default", "name": "Default"}
        ]
        mock_network_client.clients.get_all.return_value = [SAMPLE_WIRED_CLIENT]

        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # STATE_HOME after setup
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_HOME

        # STATE_NOT_HOME after refresh with [] (same unique ID stays registered)
        mock_network_client.clients.get_all.return_value = []
        data = mock_config_entry.runtime_data
        await data.device_coordinator.async_refresh()
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_NOT_HOME
        reg_after_empty = entity_registry.async_get(entity_id)
        assert reg_after_empty is not None
        assert reg_after_empty.unique_id == f"{DOMAIN}_{mac}"

        # STATE_HOME after the client returns
        mock_network_client.clients.get_all.return_value = [SAMPLE_WIRED_CLIENT]
        await data.device_coordinator.async_refresh()
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_HOME

        # Connection error: runtime_data retains previous client record
        mock_network_client.clients.get_all.side_effect = UniFiConnectionError(
            "Connection lost"
        )
        await data.device_coordinator.async_refresh()
        await hass.async_block_till_done()

        # Check client record retention and architectural availability behavior:
        # device_coordinator records failure, retains previous client record.
        # Per #116 and UnifiClientTracker.available docstring, the tracker retains
        # its last home/not_home state (STATE_HOME) and does not drop to unavailable.
        assert data.device_coordinator.last_update_success is False
        client_data = (
            data.device_coordinator.data.get("clients", {})
            .get("default", {})
            .get(SAMPLE_WIRED_CLIENT["id"])
        )
        assert client_data is not None

        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_HOME

        # Recovery: refresh succeeds and client reports home
        mock_network_client.clients.get_all.side_effect = None
        mock_network_client.clients.get_all.return_value = [SAMPLE_WIRED_CLIENT]
        await data.device_coordinator.async_refresh()
        await hass.async_block_till_done()

        assert data.device_coordinator.last_update_success is True
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_HOME

    @pytest.mark.asyncio
    async def test_entity_platform_legacy_collision_retained_lifecycle(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_config_entry: MockConfigEntry,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        *,
        mock_local_auth: MagicMock,
        enable_custom_integrations: None,
    ) -> None:
        """Verify unmigrated legacy entry functions properly in HA entity platform."""
        mac = "aa:bb:cc:dd:ee:33"
        mock_config_entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(
            mock_config_entry, options={CONF_TRACK_WIFI_CLIENTS: True}
        )

        # Another entry already occupies the migrated unique_id
        other_entry = MockConfigEntry(
            version=1,
            minor_version=0,
            domain=DOMAIN,
            entry_id="colliding_entry",
            data=dict(mock_config_entry.data),
        )
        other_entry.add_to_hass(hass)
        entity_registry.async_get_or_create(
            "device_tracker",
            DOMAIN,
            f"{DOMAIN}_{mac}",
            config_entry=other_entry,
        )

        # Legacy entry keyed by bare MAC
        legacy_entry = entity_registry.async_get_or_create(
            "device_tracker",
            DOMAIN,
            mac.upper(),
            config_entry=mock_config_entry,
            suggested_object_id="legacy_collided_phone",
        )
        entity_id = legacy_entry.entity_id

        mock_network_client.sites.get_all.return_value = [
            {"id": "default", "name": "Default"}
        ]
        mock_network_client.clients.get_all.return_value = []

        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Check legacy entry unique_id is preserved
        entry_after = entity_registry.async_get(entity_id)
        assert entry_after is not None
        assert entry_after.unique_id == mac.upper()

        # Entity reports not_home (functional, not unavailable)
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_NOT_HOME

        # Client reconnects
        client_data = {
            "id": "client_33",
            "macAddress": mac,
            "name": "Legacy Collided Phone",
            "hostname": "legacy-collided-phone",
            "type": "WIRELESS",
            "connected": True,
            "uplinkDeviceId": None,
            "ipAddress": "192.168.1.133",
        }
        mock_network_client.clients.get_all.return_value = [client_data]

        data = mock_config_entry.runtime_data
        await data.device_coordinator.async_refresh()
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_HOME

    @pytest.mark.asyncio
    async def test_new_tracker_is_enabled_and_user_disabled_stays_disabled(
        self,
        hass: HomeAssistant,
        entity_registry: er.EntityRegistry,
        mock_config_entry: MockConfigEntry,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        *,
        mock_local_auth: MagicMock,
        enable_custom_integrations: None,
    ) -> None:
        """A newly discovered tracker is enabled; a user's disable is kept."""
        new_mac = "aa:bb:cc:dd:ee:31"
        disabled_mac = "aa:bb:cc:dd:ee:32"
        mock_config_entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(
            mock_config_entry, options={CONF_TRACK_WIFI_CLIENTS: True}
        )
        disabled = entity_registry.async_get_or_create(
            "device_tracker",
            DOMAIN,
            f"{DOMAIN}_{disabled_mac}",
            config_entry=mock_config_entry,
            disabled_by=er.RegistryEntryDisabler.USER,
        )
        mock_network_client.sites.get_all.return_value = [
            {"id": "default", "name": "Default"}
        ]
        mock_network_client.clients.get_all.return_value = [
            {
                "id": f"client_{mac[-2:]}",
                "macAddress": mac,
                "name": f"Phone {mac[-2:]}",
                "type": "WIRELESS",
                "connected": True,
            }
            for mac in (new_mac, disabled_mac)
        ]

        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = entity_registry.async_get_entity_id(
            "device_tracker", DOMAIN, f"{DOMAIN}_{new_mac}"
        )
        assert entity_id is not None
        entry = entity_registry.async_get(entity_id)
        assert entry is not None
        assert entry.disabled_by is None
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_HOME

        kept = entity_registry.async_get(disabled.entity_id)
        assert kept is not None
        assert kept.disabled_by is er.RegistryEntryDisabler.USER
        assert hass.states.get(disabled.entity_id) is None

    @pytest.mark.asyncio
    async def test_controller_rename_updates_the_client_device(
        self,
        hass: HomeAssistant,
        device_registry: dr.DeviceRegistry,
        mock_config_entry: MockConfigEntry,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        *,
        mock_local_auth: MagicMock,
        enable_custom_integrations: None,
    ) -> None:
        """A rename on the controller renames the device; the user's name wins."""
        mac = "aa:bb:cc:dd:ee:33"
        mock_config_entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(
            mock_config_entry, options={CONF_TRACK_WIFI_CLIENTS: True}
        )
        client = {
            "id": "client_33",
            "macAddress": mac,
            "name": "Old Phone",
            "type": "WIRELESS",
            "connected": True,
        }
        mock_network_client.sites.get_all.return_value = [
            {"id": "default", "name": "Default"}
        ]
        mock_network_client.clients.get_all.return_value = [client]
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        identifier = (DOMAIN, f"client_{mac}")
        device = device_registry.async_get_device_by_identifier(
            identifier=identifier, config_entry_id=mock_config_entry.entry_id
        )
        assert device is not None
        assert device.name == "Old Phone"
        device_registry.async_update_device(device.id, name_by_user="My Phone")

        mock_network_client.clients.get_all.return_value = [
            {**client, "name": "New Phone"}
        ]
        await mock_config_entry.runtime_data.device_coordinator.async_refresh()
        await hass.async_block_till_done()

        renamed = device_registry.async_get(device.id)
        assert renamed is not None
        assert renamed.name == "New Phone"
        assert renamed.name_by_user == "My Phone"
