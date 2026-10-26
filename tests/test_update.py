# Copyright 2026 UniFi Insights contributors
"""Tests for UniFi Insights update platform."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from custom_components.unifi_insights.update import (
    PARALLEL_UPDATES,
    UnifiNetworkDeviceUpdate,
    async_setup_entry,
)
from tests.fixtures.assertions import patch_attribute, require_mock, require_not_none
from tests.fixtures.facade_data import (
    set_availability,
    with_device_fields,
    with_network_device,
    with_protect_collection,
    with_sections,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


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
        coordinator.protect_client = MagicMock()
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
    async def test_setup_entry_no_devices(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup when no devices present."""
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 0

    @pytest.mark.asyncio
    async def test_setup_entry_with_network_devices(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup with network devices present."""
        mock_coordinator.data = with_network_device(
            mock_coordinator.data,
            "site1",
            "device1",
            {
                "id": "device1",
                "name": "Test Switch",
                "model": "USW-24-POE",
                "state": "ONLINE",
                "firmwareVersion": "6.5.55",
                "upgradeAvailable": True,
                "upgradableFirmwareVersion": "6.6.65",
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], UnifiNetworkDeviceUpdate)

    @pytest.mark.asyncio
    async def test_setup_entry_with_protect_devices(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup with Protect devices — no Protect update entities created."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "cameras",
            {
                "camera1": {
                    "id": "camera1",
                    "name": "Test Camera",
                    "state": "CONNECTED",
                }
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        # No entities — no network devices and Protect devices don't get update entities
        assert len(entities) == 0

    @pytest.mark.asyncio
    async def test_setup_entry_no_protect_client(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup without Protect API."""
        mock_coordinator.protect_client = None
        mock_coordinator.data = with_network_device(
            mock_coordinator.data,
            "site1",
            "device1",
            {
                "id": "device1",
                "name": "Test Switch",
                "model": "USW-24-POE",
                "state": "ONLINE",
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        # Should only have network device, no Protect devices
        assert len(entities) == 1
        assert isinstance(entities[0], UnifiNetworkDeviceUpdate)


class TestUnifiNetworkDeviceUpdate:
    """Tests for UnifiNetworkDeviceUpdate entity."""

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
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "name": "Test Switch",
                        "model": "USW-24-POE",
                        "state": "ONLINE",
                        "firmwareVersion": "6.5.55",
                        "firmwareUpdatable": True,
                        "macAddress": "AA:BB:CC:DD:EE:FF",
                    }
                }
            },
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

    def test_initialization(self, mock_coordinator: MagicMock) -> None:
        """Test entity initialization."""
        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )

        assert entity._site_id == "site1"
        assert entity._device_id == "device1"

    def test_unique_id(self, mock_coordinator: MagicMock) -> None:
        """Test unique ID is set correctly."""
        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )

        assert "device1" in require_not_none(entity._attr_unique_id)
        assert "update" in require_not_none(entity._attr_unique_id)

    def test_installed_version(self, mock_coordinator: MagicMock) -> None:
        """Test installed version property."""
        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )

        assert entity.installed_version == "6.5.55"

    def test_latest_version_available(self, mock_coordinator: MagicMock) -> None:
        """Test latest version when update available."""
        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )

        # When firmwareUpdatable=True but no upgrade version field,
        # returns current version with +update suffix
        assert entity.latest_version == "6.5.55+update"

    def test_latest_version_with_upgrade_firmware(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test latest version returns actual firmware version when available."""
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data, "site1", "device1", upgradeToFirmware="7.0.0"
        )

        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )

        assert entity.latest_version == "7.0.0"

    def test_latest_version_no_update(self, mock_coordinator: MagicMock) -> None:
        """Test latest version when no update available."""
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data, "site1", "device1", firmwareUpdatable=False
        )

        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )

        assert entity.latest_version == entity.installed_version

    def test_available_when_coordinator_success(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test availability when coordinator update succeeds."""
        mock_coordinator.last_update_success = True

        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )

        assert entity.available is True

    def test_unavailable_when_coordinator_fails(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test availability when coordinator update fails."""
        set_availability(mock_coordinator, device=False)

        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )

        assert entity.available is False

    def test_device_info(self, mock_coordinator: MagicMock) -> None:
        """Test device info is set correctly."""
        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )

        device_info = entity.device_info
        assert device_info is not None


class TestNetworkDeviceUpdateEdgeCases:
    """Test edge cases for UnifiNetworkDeviceUpdate."""

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
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "name": "Test Switch",
                        "model": "USW-24-POE",
                        "state": "ONLINE",
                        "firmwareVersion": "6.5.55",
                    }
                }
            },
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

    def test_device_without_mac(self, mock_coordinator: MagicMock) -> None:
        """Test device initialization without MAC address."""
        # Device has no MAC
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data, "site1", "device1", macAddress=None
        )

        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )

        # Should still initialize without connections
        assert entity._attr_device_info is not None

    def test_installed_version_no_device_data(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test installed version when device data is missing."""
        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )
        # Remove device data after entity creation
        mock_coordinator.data = with_sections(
            mock_coordinator.data, devices={"site1": {}}
        )

        assert entity.installed_version is None

    def test_latest_version_no_device_data(self, mock_coordinator: MagicMock) -> None:
        """Test latest version when device data is missing."""
        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )
        # Remove device data after entity creation
        mock_coordinator.data = with_sections(
            mock_coordinator.data, devices={"site1": {}}
        )

        assert entity.latest_version is None

    def test_in_progress_when_upgrading(self, mock_coordinator: MagicMock) -> None:
        """Test in_progress property when device is upgrading."""
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data, "site1", "device1", state="UPGRADING"
        )

        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )

        assert entity.in_progress is True

    def test_in_progress_when_not_upgrading(self, mock_coordinator: MagicMock) -> None:
        """Test in_progress property when device is not upgrading."""
        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )

        assert entity.in_progress is False

    def test_in_progress_no_device_data(self, mock_coordinator: MagicMock) -> None:
        """Test in_progress property when device data is missing."""
        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )
        # Remove device data after entity creation
        mock_coordinator.data = with_sections(
            mock_coordinator.data, devices={"site1": {}}
        )

        assert entity.in_progress is False

    @pytest.mark.asyncio
    async def test_async_added_to_hass(self, mock_coordinator: MagicMock) -> None:
        """Test async_added_to_hass method."""
        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )

        # Mock the HA methods
        entity.async_on_remove = MagicMock()
        mock_coordinator.async_add_listener = MagicMock(return_value="remove_callback")

        await entity.async_added_to_hass()

        mock_coordinator.async_add_listener.assert_called_once()
        entity.async_on_remove.assert_called_once_with("remove_callback")

    def test_handle_coordinator_update(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test _handle_coordinator_update method."""
        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )

        # Mock async_write_ha_state
        patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())

        entity._handle_coordinator_update()

        require_mock(entity.async_write_ha_state).assert_called_once()

    def test_latest_version_update_available_no_version_and_no_installed(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test latest version fallback when updatable but versions are missing."""
        mock_coordinator.data = with_network_device(
            mock_coordinator.data,
            "site1",
            "device1",
            {
                "id": "device1",
                "name": "Test Switch",
                "firmwareUpdatable": True,
            },
        )
        entity = UnifiNetworkDeviceUpdate(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
        )
        assert entity.latest_version == "update-available"
