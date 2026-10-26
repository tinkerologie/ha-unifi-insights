# Copyright 2026 UniFi Insights contributors
"""Tests for UniFi Protect light platform."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, call, patch

import pytest
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode
from homeassistant.exceptions import HomeAssistantError

from custom_components.unifi_insights.const import (
    ATTR_LIGHT_DARK,
    ATTR_LIGHT_ID,
    ATTR_LIGHT_LEVEL,
    ATTR_LIGHT_MODE,
    ATTR_LIGHT_MOTION,
    ATTR_LIGHT_NAME,
    ATTR_LIGHT_STATE,
    DEVICE_TYPE_LIGHT,
    LIGHT_MODE_ALWAYS,
    LIGHT_MODE_OFF,
)
from custom_components.unifi_insights.light import (
    PARALLEL_UPDATES,
    UnifiProtectLight,
    async_setup_entry,
)
from tests.fixtures.assertions import patch_attribute, require_mock, require_not_none
from tests.fixtures.command_owners import autospec_command_owners, protect_owner
from tests.fixtures.facade_data import (
    with_protect_collection,
    with_protect_device,
    with_protect_fields,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

    from custom_components.unifi_insights.coordinators.facade import FacadeData


def _with_led_level(data: Mapping[str, Any], led_level: Any) -> FacadeData:
    """Return ``data`` with ``light1``'s ledLevel replaced."""
    light1 = data["protect"]["lights"]["light1"]
    return with_protect_fields(
        data,
        "lights",
        "light1",
        lightDeviceSettings={**light1["lightDeviceSettings"], "ledLevel": led_level},
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
        coordinator = MagicMock()
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
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        # Should not add any entities when Protect is not available
        async_add_entities.assert_not_called()

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
                    "lightModeSettings": {"mode": "motion"},
                    "lightDeviceSettings": {"ledLevel": 75},
                }
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        # Should add one light entity
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], UnifiProtectLight)

    @pytest.mark.asyncio
    async def test_setup_entry_with_multiple_lights(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup with multiple lights."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "lights",
            {
                "light1": {"id": "light1", "name": "Front Light", "state": "CONNECTED"},
                "light2": {"id": "light2", "name": "Back Light", "state": "CONNECTED"},
                "light3": {
                    "id": "light3",
                    "name": "Side Light",
                    "state": "DISCONNECTED",
                },
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 3

    @pytest.mark.asyncio
    async def test_setup_entry_skips_light_that_fails_to_initialize(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """A light whose data raises during initialization is skipped, not fatal."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "lights",
            {
                "light_bad": {
                    "id": "light_bad",
                    "name": "Bad Light",
                    "state": "CONNECTED",
                    "lightDeviceSettings": {"ledLevel": "not-a-number"},
                },
                "light_good": {
                    "id": "light_good",
                    "name": "Good Light",
                    "state": "CONNECTED",
                    "lightDeviceSettings": {"ledLevel": 6},
                },
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        orig_init = UnifiProtectLight.__init__

        def bad_init(self: UnifiProtectLight, *args: Any, **kwargs: Any) -> None:
            if kwargs.get("light_id") == "light_bad":
                raise TypeError
            return orig_init(self, *args, **kwargs)

        with patch.object(UnifiProtectLight, "__init__", bad_init):
            await async_setup_entry(hass, mock_entry, async_add_entities)

        # Only the light that initialized successfully should be added.
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert entities[0]._device_id == "light_good"


class TestUnifiProtectLight:
    """Tests for UnifiProtectLight entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = autospec_command_owners(MagicMock())
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
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
                        "lightModeSettings": {"mode": "motion"},
                        "lightDeviceSettings": {"ledLevel": 6},
                        "lastMotion": 1234567890,
                        "isDark": True,
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
        """Test light entity initialization."""
        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        assert light._device_id == "light1"
        assert light._device_type == DEVICE_TYPE_LIGHT
        assert light._attr_has_entity_name is True
        assert light._attr_name is None
        assert light._attr_color_mode == ColorMode.BRIGHTNESS
        assert require_not_none(ColorMode.BRIGHTNESS) in require_not_none(
            light._attr_supported_color_modes
        )
        assert light._attr_entity_category is None

    def test_update_from_data_connected(self, mock_coordinator: MagicMock) -> None:
        """Test _update_from_data with connected light."""
        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        assert light.available is True
        assert light._attr_is_on is True  # mode is "motion", not OFF
        assert light._attr_brightness == 255

    def test_update_from_data_off_mode(self, mock_coordinator: MagicMock) -> None:
        """Test _update_from_data with light in OFF mode."""
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "lights",
            "light1",
            lightModeSettings={"mode": LIGHT_MODE_OFF},
        )

        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        assert light._attr_is_on is False

    def test_update_from_data_always_on_mode(self, mock_coordinator: MagicMock) -> None:
        """Test _update_from_data with light in always-on mode."""
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "lights",
            "light1",
            lightModeSettings={"mode": LIGHT_MODE_ALWAYS},
        )

        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        assert light._attr_is_on is True

    def test_update_from_data_disconnected(self, mock_coordinator: MagicMock) -> None:
        """Test _update_from_data with disconnected light."""
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "lights", "light1", state="DISCONNECTED"
        )

        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        assert light.available is False

    def test_extra_state_attributes(self, mock_coordinator: MagicMock) -> None:
        """Test extra state attributes."""
        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        attrs = light._attr_extra_state_attributes
        assert attrs[ATTR_LIGHT_ID] == "light1"
        assert attrs[ATTR_LIGHT_NAME] == "Test Light"
        assert attrs[ATTR_LIGHT_STATE] == "CONNECTED"
        assert attrs[ATTR_LIGHT_MODE] == "motion"
        assert attrs[ATTR_LIGHT_LEVEL] == 6
        assert attrs[ATTR_LIGHT_MOTION] == 1234567890
        assert attrs[ATTR_LIGHT_DARK] is True

    def test_brightness_calculation(self, mock_coordinator: MagicMock) -> None:
        """Test brightness value calculation from LED level."""
        # Test 100% brightness (ledLevel 6 -> 255)
        mock_coordinator.data = _with_led_level(mock_coordinator.data, 6)
        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        assert light._attr_brightness == 255

        # Test out of range (0 -> None)
        mock_coordinator.data = _with_led_level(mock_coordinator.data, 0)
        light._update_from_data()
        assert light._attr_brightness is None

        # Test out of range (7 -> None)
        mock_coordinator.data = _with_led_level(mock_coordinator.data, 7)
        light._update_from_data()
        assert light._attr_brightness is None

        # Test 50% brightness (ledLevel 3 -> 128)
        mock_coordinator.data = _with_led_level(mock_coordinator.data, 3)
        light._update_from_data()
        assert light._attr_brightness == 128

    def test_default_led_level(self, mock_coordinator: MagicMock) -> None:
        """Test default LED level when not provided."""
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "lights", "light1", lightDeviceSettings={}
        )

        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        # Missing ledLevel defaults to None
        assert light._attr_brightness is None

    @pytest.mark.asyncio
    async def test_async_turn_on(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test turning light on."""
        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        patch_attribute(monkeypatch, light, "async_write_ha_state", MagicMock())

        await light.async_turn_on()

        protect_owner(mock_coordinator).async_set_light_mode.assert_called_once_with(
            "light1",
            LIGHT_MODE_ALWAYS,
        )
        # The owner publishes the mode; the state follows from the data.
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "lights",
            "light1",
            lightModeSettings={"mode": LIGHT_MODE_ALWAYS},
        )
        light._update_from_data()
        assert light._attr_is_on is True

    @pytest.mark.asyncio
    async def test_async_turn_on_with_brightness(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test turning light on with specific brightness."""
        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        patch_attribute(monkeypatch, light, "async_write_ha_state", MagicMock())

        await light.async_turn_on(**{ATTR_BRIGHTNESS: 128})

        # Should set brightness first (128 -> ledLevel 3)
        protect_owner(
            mock_coordinator
        ).async_set_light_brightness.assert_called_once_with(
            "light1",
            3,
        )
        # Then set mode
        protect_owner(mock_coordinator).async_set_light_mode.assert_called_once_with(
            "light1",
            LIGHT_MODE_ALWAYS,
        )

    @pytest.mark.asyncio
    async def test_async_turn_on_with_full_brightness(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test turning light on with full brightness."""
        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        patch_attribute(monkeypatch, light, "async_write_ha_state", MagicMock())

        await light.async_turn_on(**{ATTR_BRIGHTNESS: 255})

        protect_owner(
            mock_coordinator
        ).async_set_light_brightness.assert_called_once_with(
            "light1",
            6,
        )

    @pytest.mark.asyncio
    async def test_async_turn_off(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test turning light off."""
        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        patch_attribute(monkeypatch, light, "async_write_ha_state", MagicMock())

        await light.async_turn_off()

        protect_owner(mock_coordinator).async_set_light_mode.assert_called_once_with(
            "light1",
            LIGHT_MODE_OFF,
        )
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "lights",
            "light1",
            lightModeSettings={"mode": LIGHT_MODE_OFF},
        )
        light._update_from_data()
        assert light._attr_is_on is False

    @pytest.mark.asyncio
    async def test_async_turn_off_with_kwargs(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test turning light off ignores extra kwargs."""
        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        patch_attribute(monkeypatch, light, "async_write_ha_state", MagicMock())

        await light.async_turn_off(some_extra_kwarg="value")

        protect_owner(mock_coordinator).async_set_light_mode.assert_called_once_with(
            "light1",
            LIGHT_MODE_OFF,
        )

    @pytest.mark.asyncio
    async def test_async_turn_on_error(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test turning light on surfaces Home Assistant errors."""
        protect_owner(
            mock_coordinator
        ).async_set_light_mode.side_effect = HomeAssistantError(
            "Unable to set mode for light light1"
        )

        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        patch_attribute(monkeypatch, light, "async_write_ha_state", MagicMock())

        with pytest.raises(HomeAssistantError, match="Unable to set mode for light"):
            await light.async_turn_on()

        require_mock(light.async_write_ha_state).assert_not_called()

    @pytest.mark.asyncio
    async def test_async_turn_off_error(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test turning light off surfaces Home Assistant errors."""
        protect_owner(
            mock_coordinator
        ).async_set_light_mode.side_effect = HomeAssistantError(
            "Unable to set mode for light light1"
        )

        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        patch_attribute(monkeypatch, light, "async_write_ha_state", MagicMock())

        with pytest.raises(HomeAssistantError, match="Unable to set mode for light"):
            await light.async_turn_off()

        require_mock(light.async_write_ha_state).assert_not_called()

    def test_missing_light_data(self, mock_coordinator: MagicMock) -> None:
        """Test handling missing light data."""
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data, "lights", "light1", {}
        )

        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        # Should use defaults
        assert light.available is False
        assert light._attr_is_on is False
        assert light._attr_brightness is None

    def test_missing_mode_settings(self, mock_coordinator: MagicMock) -> None:
        """Test handling missing mode settings."""
        light1 = mock_coordinator.data["protect"]["lights"]["light1"]
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "lights",
            "light1",
            {key: value for key, value in light1.items() if key != "lightModeSettings"},
        )

        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        # Should default to OFF mode
        assert light._attr_is_on is False


class TestProtectLightWrites:
    """Light writes call the Protect owner; brightness reads map from ledLevel."""

    @pytest.fixture(autouse=True)
    def state_writer_patcher(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._state_patcher = monkeypatch

    @pytest.mark.asyncio
    async def test_light_turn_on_and_off_call_the_owner(self) -> None:
        """Turn on/off set the mode; a brightness is written first."""
        _, protect, light = self._setup_light()

        await light.async_turn_on()
        protect.async_set_light_mode.assert_awaited_once_with(
            "light1", LIGHT_MODE_ALWAYS
        )
        protect.async_set_light_brightness.assert_not_awaited()

        protect.async_set_light_mode.reset_mock()
        await light.async_turn_off()
        protect.async_set_light_mode.assert_awaited_once_with("light1", LIGHT_MODE_OFF)

        manager = MagicMock()
        manager.attach_mock(protect.async_set_light_brightness, "brightness")
        manager.attach_mock(protect.async_set_light_mode, "mode")
        protect.async_set_light_mode.reset_mock()
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 255})
        assert manager.mock_calls == [
            call.brightness("light1", 6),
            call.mode("light1", LIGHT_MODE_ALWAYS),
        ]

    def _setup_light(
        self, led_level: float | None = 3
    ) -> tuple[MagicMock, Any, UnifiProtectLight]:
        coordinator = autospec_command_owners(MagicMock())
        light_settings: dict[str, Any] = {}
        if led_level is not None:
            light_settings["ledLevel"] = led_level
        coordinator.data = {
            "protect": {
                "lights": {
                    "light1": {
                        "name": "Test Light",
                        "lightModeSettings": {"mode": "off"},
                        "lightDeviceSettings": light_settings,
                    }
                }
            }
        }
        light = UnifiProtectLight(coordinator, "light1")
        patch_attribute(self._state_patcher, light, "async_write_ha_state", MagicMock())
        return coordinator, protect_owner(coordinator), light

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("brightness", "led_level"), [(255, 6), (128, 3), (1, 1)])
    async def test_light_write_brightness_mapping(
        self, brightness: int, led_level: int
    ) -> None:
        """The write path maps HA brightness to the Protect ledLevel."""
        _, protect, light = self._setup_light()
        await light.async_turn_on(**{ATTR_BRIGHTNESS: brightness})
        protect.async_set_light_brightness.assert_awaited_once_with("light1", led_level)

    def test_light_read_brightness_mapping_6_maps_to_255(self) -> None:
        """Test read path maps Protect ledLevel 6 to HA brightness 255."""
        _, _, light = self._setup_light(led_level=6)
        assert light._attr_brightness == 255
        assert light._attr_extra_state_attributes[ATTR_LIGHT_LEVEL] == 6

    def test_light_read_brightness_mapping_3_maps_to_128(self) -> None:
        """Test read path maps Protect ledLevel 3 to HA brightness 128."""
        _, _, light = self._setup_light(led_level=3)
        assert light._attr_brightness == 128
        assert light._attr_extra_state_attributes[ATTR_LIGHT_LEVEL] == 3

    def test_light_read_brightness_mapping_missing_maps_to_none(self) -> None:
        """Test read path maps missing ledLevel to None."""
        _, _, light = self._setup_light(led_level=None)
        assert light._attr_brightness is None
        assert light._attr_extra_state_attributes[ATTR_LIGHT_LEVEL] is None

    def test_light_read_brightness_accepts_whole_number_float(self) -> None:
        """Test read path accepts a whole-number float ledLevel (spec: number)."""
        _, _, light = self._setup_light(led_level=3.0)
        assert light._attr_brightness == 128

    @pytest.mark.parametrize("led_level", [3.5, True, 0.0, 7.0, float("nan")])
    def test_light_read_brightness_rejects_invalid_led_level(
        self, led_level: Any
    ) -> None:
        """Test read path maps fractions, bools and out-of-range to None."""
        _, _, light = self._setup_light(led_level=led_level)
        assert light._attr_brightness is None

    @pytest.mark.asyncio
    async def test_light_brightness_round_trip(self) -> None:
        """Round trip: set 255, the owner overlays ledLevel 6, read 255."""
        coordinator, protect, light = self._setup_light(led_level=3)
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 255})
        protect.async_set_light_brightness.assert_awaited_once_with("light1", 6)
        coordinator.data = _with_led_level(coordinator.data, 6)
        light._update_from_data()
        assert light._attr_brightness == 255
        assert light._attr_extra_state_attributes[ATTR_LIGHT_LEVEL] == 6
