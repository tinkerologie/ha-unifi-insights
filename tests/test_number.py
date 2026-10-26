# Copyright 2026 UniFi Insights contributors
"""Tests for UniFi Protect number platform."""

from __future__ import annotations

import contextlib
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.number import (
    NumberDeviceClass,
    NumberExtraStoredData,
    NumberMode,
)
from homeassistant.const import UnitOfDataRate, UnitOfInformation, UnitOfTime
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from custom_components.unifi_insights.const import (
    ATTR_CAMERA_ID,
    ATTR_CAMERA_NAME,
    ATTR_CHIME_ID,
    ATTR_CHIME_NAME,
    ATTR_CHIME_REPEAT_TIMES,
    ATTR_CHIME_VOLUME,
    ATTR_LIGHT_ID,
    ATTR_LIGHT_LEVEL,
    ATTR_LIGHT_NAME,
    ATTR_MIC_ENABLED,
    DEVICE_TYPE_CAMERA,
    DEVICE_TYPE_CHIME,
    DEVICE_TYPE_LIGHT,
)
from custom_components.unifi_insights.data_transforms import VoucherSettings
from custom_components.unifi_insights.number import (
    PARALLEL_UPDATES,
    VOUCHER_NUMBER_DESCRIPTIONS,
    UnifiProtectChimeRepeatTimesNumber,
    UnifiProtectChimeVolumeNumber,
    UnifiProtectLightLevelNumber,
    UnifiProtectMicrophoneVolumeNumber,
    UnifiVoucherInputNumber,
    async_setup_entry,
)
from tests.fixtures.assertions import patch_attribute, require_mock, require_not_none
from tests.fixtures.command_owners import autospec_command_owners, protect_owner
from tests.fixtures.entities import EntityCollector
from tests.fixtures.facade_data import (
    set_availability,
    with_protect_collection,
    with_protect_device,
    with_protect_fields,
    with_sections,
    without_sections,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

    from custom_components.unifi_insights.coordinators.facade import FacadeData


def _without_protect_fields(
    data: Mapping[str, Any], collection: str, device_id: str, *keys: str
) -> FacadeData:
    """Return ``data`` with fields of one Protect device's record removed."""
    record = data["protect"][collection][device_id]
    return with_protect_device(
        data,
        collection,
        device_id,
        {key: value for key, value in record.items() if key not in keys},
    )


class TestParallelUpdates:
    """Test PARALLEL_UPDATES constant."""

    def test_parallel_updates_value(self) -> None:
        """Test that PARALLEL_UPDATES is set correctly for action-based entities."""
        assert PARALLEL_UPDATES == 1


class TestAsyncSetupEntry:
    """Tests for async_setup_entry function."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = autospec_command_owners(MagicMock())
        coordinator.protect_client = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    @pytest.mark.asyncio
    async def test_setup_entry_no_protect_client(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup when Protect API is not available."""
        mock_coordinator.protect_client = None

        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        # Should not add any entities when Protect is not available
        async_add_entities.assert_not_called()

    @pytest.mark.asyncio
    async def test_setup_entry_with_cameras(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup with cameras present."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "cameras",
            {
                "camera1": {
                    "id": "camera1",
                    "name": "Test Camera",
                    "state": "CONNECTED",
                    "micVolume": 50,
                }
            },
        )

        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], UnifiProtectMicrophoneVolumeNumber)

    @pytest.mark.asyncio
    async def test_setup_entry_with_lights(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup with lights present."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "lights",
            {
                "light1": {
                    "id": "light1",
                    "name": "Test Light",
                    "state": "CONNECTED",
                }
            },
        )

        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], UnifiProtectLightLevelNumber)

    @pytest.mark.asyncio
    async def test_setup_entry_with_chimes(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup with chimes present."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "chimes",
            {
                "chime1": {
                    "id": "chime1",
                    "name": "Test Chime",
                    "state": "CONNECTED",
                }
            },
        )

        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        # Should create both volume and repeat times numbers
        assert len(entities) == 2
        entity_types = {type(e).__name__ for e in entities}
        assert "UnifiProtectChimeVolumeNumber" in entity_types
        assert "UnifiProtectChimeRepeatTimesNumber" in entity_types

    @pytest.mark.asyncio
    async def test_setup_entry_with_all_devices(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup with all device types."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "cameras",
            {"camera1": {"id": "camera1", "name": "Cam1", "state": "CONNECTED"}},
        )
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "lights",
            {"light1": {"id": "light1", "name": "Light1", "state": "CONNECTED"}},
        )
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "chimes",
            {"chime1": {"id": "chime1", "name": "Chime1", "state": "CONNECTED"}},
        )

        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        # 1 camera + 1 light + 2 chime (volume + repeat) = 4
        assert len(entities) == 4


class TestUnifiProtectMicrophoneVolumeNumber:
    """Tests for UnifiProtectMicrophoneVolumeNumber entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = autospec_command_owners(MagicMock())
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.protect_client.set_microphone_volume = AsyncMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "Test Camera",
                        "state": "CONNECTED",
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "type": "UVC-G4-Pro",
                        "firmwareVersion": "1.0.0",
                        "micVolume": 75,
                        "micEnabled": True,
                    }
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    def test_initialization(self, mock_coordinator: MagicMock) -> None:
        """Test number entity initialization."""
        number = UnifiProtectMicrophoneVolumeNumber(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert number._device_id == "camera1"
        assert number._device_type == DEVICE_TYPE_CAMERA
        assert number._attr_has_entity_name is True
        assert number._attr_translation_key == "mic_volume"
        assert number._attr_entity_category == EntityCategory.CONFIG
        assert number._attr_native_min_value == 1
        assert number._attr_native_max_value == 100
        assert number._attr_native_step == 1
        assert number._attr_mode == NumberMode.SLIDER

    def test_update_from_data(self, mock_coordinator: MagicMock) -> None:
        """Test _update_from_data."""
        number = UnifiProtectMicrophoneVolumeNumber(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert number._attr_native_value == 75

    def test_extra_state_attributes(self, mock_coordinator: MagicMock) -> None:
        """Test extra state attributes."""
        number = UnifiProtectMicrophoneVolumeNumber(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        attrs = number._attr_extra_state_attributes
        assert attrs[ATTR_CAMERA_ID] == "camera1"
        assert attrs[ATTR_CAMERA_NAME] == "Test Camera"
        assert attrs[ATTR_MIC_ENABLED] is True

    def test_extra_state_attributes_mic_enabled_precedence(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test mic_enabled reads isMicEnabled first, then micEnabled."""
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "cameras",
            "camera1",
            isMicEnabled=True,
            micEnabled=False,
        )
        number = UnifiProtectMicrophoneVolumeNumber(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        assert require_not_none(number.extra_state_attributes)[ATTR_MIC_ENABLED] is True

        # isMicEnabled False takes precedence over micEnabled True
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "cameras",
            "camera1",
            isMicEnabled=False,
            micEnabled=True,
        )
        number._update_from_data()
        assert (
            require_not_none(number.extra_state_attributes)[ATTR_MIC_ENABLED] is False
        )

        # Fallback to micEnabled when isMicEnabled is None
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "cameras",
            "camera1",
            isMicEnabled=None,
            micEnabled=True,
        )
        number._update_from_data()
        assert require_not_none(number.extra_state_attributes)[ATTR_MIC_ENABLED] is True

        # Default to False when both missing
        mock_coordinator.data = _without_protect_fields(
            mock_coordinator.data, "cameras", "camera1", "isMicEnabled", "micEnabled"
        )
        number._update_from_data()
        assert (
            require_not_none(number.extra_state_attributes)[ATTR_MIC_ENABLED] is False
        )

    @pytest.mark.asyncio
    async def test_async_set_native_value_success(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test setting volume successfully."""
        number = UnifiProtectMicrophoneVolumeNumber(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        patch_attribute(monkeypatch, number, "async_write_ha_state", MagicMock())

        await number.async_set_native_value(50.0)

        protect = protect_owner(mock_coordinator)
        protect.async_set_microphone_volume.assert_awaited_once_with("camera1", 50)
        # The owner publishes micVolume; the value follows from the data.
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "cameras", "camera1", micVolume=50
        )
        number._update_from_data()
        assert number._attr_native_value == 50

    @pytest.mark.asyncio
    async def test_async_set_native_value_error(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test setting volume with error."""
        protect_owner(
            mock_coordinator
        ).async_set_microphone_volume.side_effect = HomeAssistantError(
            "Unable to set microphone volume for camera camera1"
        )

        number = UnifiProtectMicrophoneVolumeNumber(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        patch_attribute(monkeypatch, number, "async_write_ha_state", MagicMock())

        with pytest.raises(HomeAssistantError, match="Unable to set microphone volume"):
            await number.async_set_native_value(50.0)

        require_mock(number.async_write_ha_state).assert_not_called()

    def test_missing_mic_volume(self, mock_coordinator: MagicMock) -> None:
        """A missing micVolume shows as unknown, not 0 (below the minimum of 1)."""
        mock_coordinator.data = _without_protect_fields(
            mock_coordinator.data, "cameras", "camera1", "micVolume"
        )

        number = UnifiProtectMicrophoneVolumeNumber(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert number._attr_native_value is None


class TestUnifiProtectLightLevelNumber:
    """Tests for UnifiProtectLightLevelNumber entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = autospec_command_owners(MagicMock())
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.protect_client.lights = MagicMock()
        coordinator.protect_client.lights.set_brightness = AsyncMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {},
                "lights": {
                    "light1": {
                        "id": "light1",
                        "name": "Test Light",
                        "state": "CONNECTED",
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "type": "UP-Floodlight",
                        "firmwareVersion": "1.0.0",
                        "lightDeviceSettings": {"ledLevel": 3},
                    }
                },
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    def test_initialization(self, mock_coordinator: MagicMock) -> None:
        """Test number entity initialization."""
        number = UnifiProtectLightLevelNumber(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        assert number._device_id == "light1"
        assert number._device_type == DEVICE_TYPE_LIGHT
        assert number._attr_has_entity_name is True
        assert number._attr_translation_key == "brightness_level"
        assert number._attr_entity_category == EntityCategory.CONFIG
        assert number._attr_mode == NumberMode.SLIDER

    def test_update_from_data(self, mock_coordinator: MagicMock) -> None:
        """Test _update_from_data."""
        number = UnifiProtectLightLevelNumber(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        # ledLevel 3 of 6 is shown on the 0-100 slider as 50
        assert number._attr_native_value == 50

    def test_slider_range_is_unchanged(self, mock_coordinator: MagicMock) -> None:
        """Test the user-facing slider stays 0-100 in whole percent steps."""
        number = UnifiProtectLightLevelNumber(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        assert number.native_min_value == 0
        assert number.native_max_value == 100
        assert number.native_step == 1

    def test_extra_state_attributes(self, mock_coordinator: MagicMock) -> None:
        """Test extra state attributes."""
        number = UnifiProtectLightLevelNumber(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        attrs = number._attr_extra_state_attributes
        assert attrs[ATTR_LIGHT_ID] == "light1"
        assert attrs[ATTR_LIGHT_NAME] == "Test Light"
        assert attrs[ATTR_LIGHT_LEVEL] == 3

    @pytest.mark.asyncio
    async def test_async_set_native_value_success(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test setting light level successfully."""
        number = UnifiProtectLightLevelNumber(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        patch_attribute(monkeypatch, number, "async_write_ha_state", MagicMock())

        await number.async_set_native_value(60.0)

        # 60% maps to LED level 4, which reads back as 67%
        protect = protect_owner(mock_coordinator)
        protect.async_set_light_brightness.assert_awaited_once_with("light1", 4)
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "lights",
            "light1",
            lightDeviceSettings={"ledLevel": 4},
        )
        number._update_from_data()
        assert number._attr_native_value == 67

    @pytest.mark.asyncio
    async def test_async_set_native_value_error(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test setting light level with error."""
        protect_owner(
            mock_coordinator
        ).async_set_light_brightness.side_effect = HomeAssistantError(
            "Unable to set brightness for light light1"
        )

        number = UnifiProtectLightLevelNumber(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        patch_attribute(monkeypatch, number, "async_write_ha_state", MagicMock())

        with pytest.raises(HomeAssistantError, match="Unable to set brightness"):
            await number.async_set_native_value(60.0)

        require_mock(number.async_write_ha_state).assert_not_called()

    def test_missing_light_device_settings(self, mock_coordinator: MagicMock) -> None:
        """Test handling missing lightDeviceSettings."""
        mock_coordinator.data = _without_protect_fields(
            mock_coordinator.data, "lights", "light1", "lightDeviceSettings"
        )

        number = UnifiProtectLightLevelNumber(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        assert number._attr_native_value is None
        assert number._attr_extra_state_attributes[ATTR_LIGHT_LEVEL] is None

    def test_null_light_device_settings(self, mock_coordinator: MagicMock) -> None:
        """Test lightDeviceSettings present but None reads as no value."""
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "lights", "light1", lightDeviceSettings=None
        )

        number = UnifiProtectLightLevelNumber(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        assert number._attr_native_value is None
        assert number._attr_extra_state_attributes[ATTR_LIGHT_LEVEL] is None

    @pytest.mark.parametrize("led_level", [0, 7, "3", True, 3.5])
    def test_invalid_led_level_reads_as_none(
        self, mock_coordinator: MagicMock, led_level: object
    ) -> None:
        """Test an invalid ledLevel gives no value instead of a bogus percentage."""
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "lights",
            "light1",
            lightDeviceSettings={"ledLevel": led_level},
        )

        number = UnifiProtectLightLevelNumber(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        assert number._attr_native_value is None

    def test_whole_number_float_led_level_reads_as_percent(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test a whole-number float ledLevel (spec: number) is accepted."""
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "lights",
            "light1",
            lightDeviceSettings={"ledLevel": 6.0},
        )

        number = UnifiProtectLightLevelNumber(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        assert number._attr_native_value == 100

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("percent", "led_level", "shown"),
        [(0, 1, 17), (50, 3, 50), (100, 6, 100)],
    )
    async def test_light_level_number_writes_the_led_level(
        self,
        mock_coordinator: MagicMock,
        percent: float,
        led_level: int,
        shown: int,
        monkeypatch,
    ) -> None:
        """The slider percentage reaches the owner as a 1-6 ledLevel."""
        number = UnifiProtectLightLevelNumber(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        patch_attribute(monkeypatch, number, "async_write_ha_state", MagicMock())

        await number.async_set_native_value(percent)

        protect = protect_owner(mock_coordinator)
        protect.async_set_light_brightness.assert_awaited_once_with("light1", led_level)
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "lights",
            "light1",
            lightDeviceSettings={"ledLevel": led_level},
        )
        number._update_from_data()
        assert number._attr_native_value == shown


class TestUnifiProtectChimeVolumeNumber:
    """Tests for UnifiProtectChimeVolumeNumber entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = autospec_command_owners(MagicMock())
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.protect_client.set_chime_volume = AsyncMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {
                    "chime1": {
                        "id": "chime1",
                        "name": "Test Chime",
                        "state": "CONNECTED",
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "type": "UP-Chime",
                        "firmwareVersion": "1.0.0",
                        "ringSettings": [
                            {"cameraId": "cam1", "volume": 65, "repeatTimes": 2}
                        ],
                    }
                },
                "liveviews": {},
            },
        }
        return coordinator

    def test_initialization(self, mock_coordinator: MagicMock) -> None:
        """Test number entity initialization."""
        number = UnifiProtectChimeVolumeNumber(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )

        assert number._device_id == "chime1"
        assert number._device_type == DEVICE_TYPE_CHIME
        assert number._attr_has_entity_name is True
        assert number._attr_translation_key == "chime_volume"
        assert number._attr_entity_category == EntityCategory.CONFIG
        assert number._attr_mode == NumberMode.SLIDER
        assert getattr(number, "_attr_icon", None) is None

    def test_update_from_data(self, mock_coordinator: MagicMock) -> None:
        """Test _update_from_data."""
        number = UnifiProtectChimeVolumeNumber(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )

        assert number._attr_native_value == 65

    def test_update_from_data_no_ring_settings(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test _update_from_data with no ring settings."""
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "chimes", "chime1", ringSettings=[]
        )

        number = UnifiProtectChimeVolumeNumber(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )

        # Default is 80
        assert number._attr_native_value == 80

    def test_extra_state_attributes(self, mock_coordinator: MagicMock) -> None:
        """Test extra state attributes."""
        number = UnifiProtectChimeVolumeNumber(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )

        attrs = number._attr_extra_state_attributes
        assert attrs[ATTR_CHIME_ID] == "chime1"
        assert attrs[ATTR_CHIME_NAME] == "Test Chime"
        assert attrs[ATTR_CHIME_VOLUME] == 65

    @pytest.mark.asyncio
    async def test_async_set_native_value_success(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test setting chime volume successfully."""
        number = UnifiProtectChimeVolumeNumber(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )
        patch_attribute(monkeypatch, number, "async_write_ha_state", MagicMock())

        await number.async_set_native_value(70.0)

        protect = protect_owner(mock_coordinator)
        protect.async_set_chime_volume.assert_awaited_once_with("chime1", 70)
        chime = mock_coordinator.data["protect"]["chimes"]["chime1"]
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "chimes",
            "chime1",
            ringSettings=[{**chime["ringSettings"][0], "volume": 70}],
        )
        number._update_from_data()
        assert number._attr_native_value == 70

    @pytest.mark.asyncio
    async def test_async_set_native_value_error(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test setting chime volume with error."""
        protect_owner(
            mock_coordinator
        ).async_set_chime_volume.side_effect = HomeAssistantError(
            "Unable to set volume for chime chime1"
        )

        number = UnifiProtectChimeVolumeNumber(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )
        patch_attribute(monkeypatch, number, "async_write_ha_state", MagicMock())

        with pytest.raises(HomeAssistantError, match="Unable to set volume"):
            await number.async_set_native_value(70.0)

        require_mock(number.async_write_ha_state).assert_not_called()


class TestUnifiProtectChimeRepeatTimesNumber:
    """Tests for UnifiProtectChimeRepeatTimesNumber entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = autospec_command_owners(MagicMock())
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.protect_client.set_chime_repeat = AsyncMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {
                    "chime1": {
                        "id": "chime1",
                        "name": "Test Chime",
                        "state": "CONNECTED",
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "type": "UP-Chime",
                        "firmwareVersion": "1.0.0",
                        "ringSettings": [
                            {"cameraId": "cam1", "volume": 65, "repeatTimes": 5}
                        ],
                    }
                },
                "liveviews": {},
            },
        }
        return coordinator

    def test_initialization(self, mock_coordinator: MagicMock) -> None:
        """Test number entity initialization."""
        number = UnifiProtectChimeRepeatTimesNumber(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )

        assert number._device_id == "chime1"
        assert number._device_type == DEVICE_TYPE_CHIME
        assert number._attr_has_entity_name is True
        assert number._attr_translation_key == "repeat_times"
        assert number._attr_entity_category == EntityCategory.CONFIG
        assert number._attr_native_min_value == 1
        assert number._attr_native_max_value == 10
        assert number._attr_native_step == 1
        assert number._attr_mode == NumberMode.BOX
        assert getattr(number, "_attr_icon", None) is None

    def test_update_from_data(self, mock_coordinator: MagicMock) -> None:
        """Test _update_from_data."""
        number = UnifiProtectChimeRepeatTimesNumber(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )

        assert number._attr_native_value == 5

    def test_update_from_data_no_ring_settings(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test _update_from_data with no ring settings."""
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "chimes", "chime1", ringSettings=[]
        )

        number = UnifiProtectChimeRepeatTimesNumber(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )

        # Default is 3
        assert number._attr_native_value == 3

    def test_extra_state_attributes(self, mock_coordinator: MagicMock) -> None:
        """Test extra state attributes."""
        number = UnifiProtectChimeRepeatTimesNumber(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )

        attrs = number._attr_extra_state_attributes
        assert attrs[ATTR_CHIME_ID] == "chime1"
        assert attrs[ATTR_CHIME_NAME] == "Test Chime"
        assert attrs[ATTR_CHIME_REPEAT_TIMES] == 5

    @pytest.mark.asyncio
    async def test_async_set_native_value_success(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test setting repeat times successfully."""
        number = UnifiProtectChimeRepeatTimesNumber(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )
        patch_attribute(monkeypatch, number, "async_write_ha_state", MagicMock())

        await number.async_set_native_value(3.0)

        protect = protect_owner(mock_coordinator)
        protect.async_set_chime_repeat.assert_awaited_once_with("chime1", 3)
        chime = mock_coordinator.data["protect"]["chimes"]["chime1"]
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "chimes",
            "chime1",
            ringSettings=[{**chime["ringSettings"][0], "repeatTimes": 3}],
        )
        number._update_from_data()
        assert number._attr_native_value == 3

    @pytest.mark.asyncio
    async def test_async_set_native_value_error(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test setting repeat times with error."""
        protect_owner(
            mock_coordinator
        ).async_set_chime_repeat.side_effect = HomeAssistantError(
            "Unable to set repeat count for chime chime1"
        )

        number = UnifiProtectChimeRepeatTimesNumber(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )
        patch_attribute(monkeypatch, number, "async_write_ha_state", MagicMock())

        with pytest.raises(HomeAssistantError, match="Unable to set repeat count"):
            await number.async_set_native_value(3.0)

        require_mock(number.async_write_ha_state).assert_not_called()


class TestVoucherNumbers:
    """Tests for voucher number entities."""

    @pytest.fixture
    def voucher_coordinator(self) -> MagicMock:
        """Create mock coordinator configured for voucher entities."""
        coord = MagicMock()
        coord.protect_client = None
        coord.data = {
            "sites": {"site1": {"id": "site1", "name": "Main Site"}},
            "vouchers": {"site1": {}},
        }
        autospec_command_owners(coord)
        settings_map: dict[str, VoucherSettings] = {}

        def get_settings(site_id: str) -> VoucherSettings:
            return settings_map.setdefault(site_id, VoucherSettings())

        def set_setting(site_id: str, field: str, value: float) -> None:
            settings_map[site_id] = replace(get_settings(site_id), **{field: value})

        owner = coord.config_coordinator
        owner.get_voucher_settings.side_effect = get_settings
        owner.set_voucher_setting.side_effect = set_setting
        return coord

    @pytest.mark.asyncio
    async def test_voucher_numbers_created_without_protect_client(
        self, hass: HomeAssistant, voucher_coordinator: MagicMock
    ) -> None:
        """Voucher numbers are created even when protect_client is None."""
        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data.coordinator = voucher_coordinator
        added_entities = []

        await async_setup_entry(hass, mock_entry, EntityCollector(added_entities))
        assert len(added_entities) == 5
        expected_ids = {
            f"console_site1_{desc.key}" for desc in VOUCHER_NUMBER_DESCRIPTIONS
        }
        assert {e.unique_id for e in added_entities} == expected_ids

    @pytest.mark.asyncio
    async def test_voucher_numbers_one_set_per_selected_site_with_empty_inventory(
        self, hass: HomeAssistant, voucher_coordinator: MagicMock
    ) -> None:
        """One set of 5 numbers per selected site with empty voucher inventory."""
        voucher_coordinator.data = with_sections(
            voucher_coordinator.data,
            sites={
                **voucher_coordinator.data["sites"],
                "site2": {"id": "site2", "name": "Branch"},
            },
            vouchers={**voucher_coordinator.data["vouchers"], "site2": {}},
        )
        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data.coordinator = voucher_coordinator
        added_entities = []

        await async_setup_entry(hass, mock_entry, EntityCollector(added_entities))
        assert len(added_entities) == 10

    @pytest.mark.asyncio
    async def test_voucher_numbers_skipped_without_vouchers_section(
        self, hass: HomeAssistant, voucher_coordinator: MagicMock
    ) -> None:
        """Voucher numbers are skipped if data has sites but lacks vouchers section."""
        voucher_coordinator.data = without_sections(
            voucher_coordinator.data, "vouchers"
        )
        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data.coordinator = voucher_coordinator
        add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, add_entities)
        add_entities.assert_not_called()

    @pytest.mark.asyncio
    async def test_voucher_numbers_late_site_added_once(
        self, hass: HomeAssistant, voucher_coordinator: MagicMock
    ) -> None:
        """Adding a site dynamically triggers discovery exactly once."""
        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data.coordinator = voucher_coordinator
        added_entities = []

        await async_setup_entry(hass, mock_entry, EntityCollector(added_entities))
        assert len(added_entities) == 5

        # Voucher listener is the first listener registered
        discover_callback = voucher_coordinator.async_add_listener.call_args_list[0][0][
            0
        ]

        # Add new site
        voucher_coordinator.data = with_sections(
            voucher_coordinator.data,
            sites={**voucher_coordinator.data["sites"], "site2": {"id": "site2"}},
            vouchers={**voucher_coordinator.data["vouchers"], "site2": {}},
        )

        discover_callback()
        assert len(added_entities) == 10

        # Call again without new site -> no new entities
        discover_callback()
        assert len(added_entities) == 10

    @pytest.mark.asyncio
    async def test_voucher_number_construction_failure_is_retried(
        self, hass: HomeAssistant, voucher_coordinator: MagicMock
    ) -> None:
        """A failed entity construction is not recorded in known and is retried."""
        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data.coordinator = voucher_coordinator
        added_entities = []

        call_count = 0
        original_init = UnifiVoucherInputNumber.__init__

        def flaky_init(self, coord, desc, site_id, *, unique_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                err_msg = "Failed once"
                raise RuntimeError(err_msg)
            original_init(self, coord, desc, site_id, unique_id=unique_id)

        with (
            patch.object(UnifiVoucherInputNumber, "__init__", flaky_init),
            contextlib.suppress(RuntimeError),
        ):
            await async_setup_entry(hass, mock_entry, EntityCollector(added_entities))

        # Discover again -> failed one is constructed and added
        discover_callback = voucher_coordinator.async_add_listener.call_args_list[0][0][
            0
        ]
        discover_callback()
        assert len(added_entities) == 5

    @pytest.mark.parametrize(
        ("key", "unit", "dev_class", "min_v", "max_v", "step"),
        [
            (
                "voucher_duration",
                UnitOfTime.MINUTES,
                NumberDeviceClass.DURATION,
                1,
                1_000_000,
                1,
            ),
            ("voucher_guest_limit", None, None, 0, 1000, 1),
            (
                "voucher_download_limit",
                UnitOfDataRate.MEGABITS_PER_SECOND,
                NumberDeviceClass.DATA_RATE,
                0,
                100,
                0.1,
            ),
            (
                "voucher_upload_limit",
                UnitOfDataRate.MEGABITS_PER_SECOND,
                NumberDeviceClass.DATA_RATE,
                0,
                100,
                0.1,
            ),
            (
                "voucher_data_limit",
                UnitOfInformation.MEGABYTES,
                NumberDeviceClass.DATA_SIZE,
                0,
                1_048_576,
                1,
            ),
        ],
    )
    def test_voucher_number_metadata(
        self, voucher_coordinator, key: str, unit, dev_class, *, min_v, max_v, step
    ) -> None:
        """Test metadata and bounds for each voucher number entity."""
        desc = next(d for d in VOUCHER_NUMBER_DESCRIPTIONS if d.key == key)
        entity = UnifiVoucherInputNumber(
            voucher_coordinator,
            desc,
            "site1",
            unique_id="console_site1_" + desc.key,
        )
        assert entity.entity_description.native_unit_of_measurement == unit
        assert entity.entity_description.device_class == dev_class
        assert entity.entity_description.native_min_value == min_v
        assert entity.entity_description.native_max_value == max_v
        assert entity.entity_description.native_step == step
        assert entity.entity_description.mode == NumberMode.BOX
        assert entity.entity_category == EntityCategory.CONFIG
        assert entity.translation_key == key

    @pytest.mark.asyncio
    async def test_voucher_number_reads_value_from_settings(
        self, voucher_coordinator
    ) -> None:
        """native_value reflects settings held in HA."""
        desc = next(
            d for d in VOUCHER_NUMBER_DESCRIPTIONS if d.key == "voucher_duration"
        )
        entity = UnifiVoucherInputNumber(
            voucher_coordinator,
            desc,
            "site1",
            unique_id="console_site1_" + desc.key,
        )
        assert entity.native_value == 480

    @pytest.mark.asyncio
    async def test_voucher_number_set_updates_settings_without_api_call(
        self, voucher_coordinator, monkeypatch
    ) -> None:
        """Setting value updates settings in HA without calling external API."""
        desc_int = next(
            d for d in VOUCHER_NUMBER_DESCRIPTIONS if d.key == "voucher_duration"
        )
        entity_int = UnifiVoucherInputNumber(
            voucher_coordinator,
            desc_int,
            "site1",
            unique_id="console_site1_" + desc_int.key,
        )
        patch_attribute(monkeypatch, entity_int, "async_write_ha_state", MagicMock())

        owner = voucher_coordinator.config_coordinator
        await entity_int.async_set_native_value(60)
        owner.set_voucher_setting.assert_called_once_with(
            "site1", "duration_minutes", 60
        )
        settings = owner.get_voucher_settings("site1")
        assert settings.duration_minutes == 60
        assert isinstance(settings.duration_minutes, int)
        require_mock(entity_int.async_write_ha_state).assert_called_once()

        desc_float = next(
            d for d in VOUCHER_NUMBER_DESCRIPTIONS if d.key == "voucher_download_limit"
        )
        entity_float = UnifiVoucherInputNumber(
            voucher_coordinator,
            desc_float,
            "site1",
            unique_id="console_site1_" + desc_float.key,
        )
        patch_attribute(monkeypatch, entity_float, "async_write_ha_state", MagicMock())

        await entity_float.async_set_native_value(12.5)
        replaced = owner.get_voucher_settings("site1")
        assert replaced.download_limit_mbps == 12.5
        assert isinstance(replaced.download_limit_mbps, float)
        assert replaced.duration_minutes == 60
        # The earlier settings object is replaced, not changed in place.
        assert settings.download_limit_mbps == 0.0

        # No network client call
        assert voucher_coordinator.network_client.mock_calls == []

    @pytest.mark.asyncio
    async def test_voucher_number_restores_valid_value(
        self, hass: HomeAssistant, voucher_coordinator: MagicMock
    ) -> None:
        """Valid restored value updates settings."""
        desc = next(
            d for d in VOUCHER_NUMBER_DESCRIPTIONS if d.key == "voucher_duration"
        )
        entity = UnifiVoucherInputNumber(
            voucher_coordinator,
            desc,
            "site1",
            unique_id="console_site1_" + desc.key,
        )
        entity.hass = hass

        with patch.object(
            entity,
            "async_get_last_number_data",
            AsyncMock(return_value=NumberExtraStoredData(None, None, None, None, 30)),
        ):
            await entity.async_added_to_hass()

        settings = voucher_coordinator.config_coordinator.get_voucher_settings("site1")
        assert settings.duration_minutes == 30

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stored_val",
        [None, 99999999, float("nan")],
    )
    async def test_voucher_number_ignores_invalid_or_missing_restore(
        self, hass, voucher_coordinator, stored_val
    ) -> None:
        """Invalid or None restored values are ignored, keeping default."""
        desc = next(
            d for d in VOUCHER_NUMBER_DESCRIPTIONS if d.key == "voucher_duration"
        )
        entity = UnifiVoucherInputNumber(
            voucher_coordinator,
            desc,
            "site1",
            unique_id="console_site1_" + desc.key,
        )
        entity.hass = hass

        stored_data = (
            NumberExtraStoredData(None, None, None, None, stored_val)
            if stored_val is not None
            else None
        )
        with patch.object(
            entity,
            "async_get_last_number_data",
            AsyncMock(return_value=stored_data),
        ):
            await entity.async_added_to_hass()

        settings = voucher_coordinator.config_coordinator.get_voucher_settings("site1")
        assert settings.duration_minutes == 480

    def test_voucher_number_availability_follows_captured_vouchers(
        self, voucher_coordinator: MagicMock
    ) -> None:
        """Availability follows the site's captured voucher availability."""
        desc = VOUCHER_NUMBER_DESCRIPTIONS[0]
        entity = UnifiVoucherInputNumber(
            voucher_coordinator,
            desc,
            "site1",
            unique_id="console_site1_" + desc.key,
        )
        set_availability(voucher_coordinator)
        assert entity.available is True
        set_availability(voucher_coordinator, failed_sections={("vouchers", "site1")})
        assert entity.available is False

    def test_voucher_number_device_info_uses_site_device(
        self, voucher_coordinator
    ) -> None:
        """Device info groups under the site gateway device."""
        desc = VOUCHER_NUMBER_DESCRIPTIONS[0]
        entity = UnifiVoucherInputNumber(
            voucher_coordinator,
            desc,
            "site1",
            unique_id="console_site1_" + desc.key,
        )
        assert entity.device_info is not None

    @pytest.mark.asyncio
    async def test_protect_numbers_still_created_next_to_voucher_numbers(
        self, hass: HomeAssistant, voucher_coordinator: MagicMock
    ) -> None:
        """Verify voucher and Protect numbers when protect_client is present."""
        voucher_coordinator.protect_client = MagicMock()
        voucher_coordinator.data = with_sections(
            voucher_coordinator.data,
            protect={
                "lights": {"l1": {"id": "l1", "name": "Light 1"}},
                "cameras": {},
                "chimes": {},
            },
        )
        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data.coordinator = voucher_coordinator
        added_entities = []

        await async_setup_entry(hass, mock_entry, EntityCollector(added_entities))
        # 5 voucher numbers + 1 light number
        assert len(added_entities) == 6
