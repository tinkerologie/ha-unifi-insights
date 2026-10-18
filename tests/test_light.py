# Copyright 2026 UniFi Insights contributors
"""Tests for UniFi Protect light platform."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode
from homeassistant.exceptions import HomeAssistantError

from custom_components.unifi_insights.api import ApiKeyAuth, ConnectionType
from custom_components.unifi_insights.api.protect import UniFiProtectClient
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
    async def test_setup_entry_no_protect_client(self, hass, mock_coordinator) -> None:
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
    async def test_setup_entry_with_lights(self, hass, mock_coordinator) -> None:
        """Test setup with lights present."""
        mock_coordinator.data["protect"]["lights"] = {
            "light1": {
                "id": "light1",
                "name": "Test Light",
                "state": "CONNECTED",
                "lightModeSettings": {"mode": "motion"},
                "lightDeviceSettings": {"ledLevel": 75},
            }
        }

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
        self, hass, mock_coordinator
    ) -> None:
        """Test setup with multiple lights."""
        mock_coordinator.data["protect"]["lights"] = {
            "light1": {"id": "light1", "name": "Front Light", "state": "CONNECTED"},
            "light2": {"id": "light2", "name": "Back Light", "state": "CONNECTED"},
            "light3": {"id": "light3", "name": "Side Light", "state": "DISCONNECTED"},
        }

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 3

    @pytest.mark.asyncio
    async def test_setup_entry_skips_light_that_fails_to_initialize(
        self, hass, mock_coordinator
    ) -> None:
        """A light whose data raises during initialization is skipped, not fatal."""
        mock_coordinator.data["protect"]["lights"] = {
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
        }

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        orig_init = UnifiProtectLight.__init__

        def bad_init(self, *args, **kwargs):
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
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.protect_client.lights = MagicMock()
        coordinator.protect_client.lights.set_mode = AsyncMock()
        coordinator.protect_client.lights.set_brightness = AsyncMock()
        coordinator.async_set_light_mode = AsyncMock()
        coordinator.async_set_light_brightness = AsyncMock()
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

    def test_initialization(self, mock_coordinator) -> None:
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
        assert ColorMode.BRIGHTNESS in light._attr_supported_color_modes
        assert light._attr_entity_category is None

    def test_update_from_data_connected(self, mock_coordinator) -> None:
        """Test _update_from_data with connected light."""
        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        assert light.available is True
        assert light._attr_is_on is True  # mode is "motion", not OFF
        assert light._attr_brightness == 255

    def test_update_from_data_off_mode(self, mock_coordinator) -> None:
        """Test _update_from_data with light in OFF mode."""
        mock_coordinator.data["protect"]["lights"]["light1"]["lightModeSettings"] = {
            "mode": LIGHT_MODE_OFF
        }

        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        assert light._attr_is_on is False

    def test_update_from_data_always_on_mode(self, mock_coordinator) -> None:
        """Test _update_from_data with light in always-on mode."""
        mock_coordinator.data["protect"]["lights"]["light1"]["lightModeSettings"] = {
            "mode": LIGHT_MODE_ALWAYS
        }

        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        assert light._attr_is_on is True

    def test_update_from_data_disconnected(self, mock_coordinator) -> None:
        """Test _update_from_data with disconnected light."""
        mock_coordinator.data["protect"]["lights"]["light1"]["state"] = "DISCONNECTED"

        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        assert light.available is False

    def test_extra_state_attributes(self, mock_coordinator) -> None:
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

    def test_brightness_calculation(self, mock_coordinator) -> None:
        """Test brightness value calculation from LED level."""
        # Test 100% brightness (ledLevel 6 -> 255)
        mock_coordinator.data["protect"]["lights"]["light1"]["lightDeviceSettings"][
            "ledLevel"
        ] = 6
        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        assert light._attr_brightness == 255

        # Test out of range (0 -> None)
        mock_coordinator.data["protect"]["lights"]["light1"]["lightDeviceSettings"][
            "ledLevel"
        ] = 0
        light._update_from_data()
        assert light._attr_brightness is None

        # Test out of range (7 -> None)
        mock_coordinator.data["protect"]["lights"]["light1"]["lightDeviceSettings"][
            "ledLevel"
        ] = 7
        light._update_from_data()
        assert light._attr_brightness is None

        # Test 50% brightness (ledLevel 3 -> 128)
        mock_coordinator.data["protect"]["lights"]["light1"]["lightDeviceSettings"][
            "ledLevel"
        ] = 3
        light._update_from_data()
        assert light._attr_brightness == 128

    def test_default_led_level(self, mock_coordinator) -> None:
        """Test default LED level when not provided."""
        mock_coordinator.data["protect"]["lights"]["light1"]["lightDeviceSettings"] = {}

        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        # Missing ledLevel defaults to None
        assert light._attr_brightness is None

    @pytest.mark.asyncio
    async def test_async_turn_on(self, mock_coordinator) -> None:
        """Test turning light on."""
        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        light.async_write_ha_state = MagicMock()

        await light.async_turn_on()

        mock_coordinator.async_set_light_mode.assert_called_once_with(
            "light1",
            LIGHT_MODE_ALWAYS,
        )
        assert light._attr_is_on is True
        light.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_on_with_brightness(self, mock_coordinator) -> None:
        """Test turning light on with specific brightness."""
        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        light.async_write_ha_state = MagicMock()

        await light.async_turn_on(**{ATTR_BRIGHTNESS: 128})

        # Should set brightness first (128 -> ledLevel 3)
        mock_coordinator.async_set_light_brightness.assert_called_once_with(
            "light1",
            3,
        )
        # Then set mode
        mock_coordinator.async_set_light_mode.assert_called_once_with(
            "light1",
            LIGHT_MODE_ALWAYS,
        )

    @pytest.mark.asyncio
    async def test_async_turn_on_with_full_brightness(self, mock_coordinator) -> None:
        """Test turning light on with full brightness."""
        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        light.async_write_ha_state = MagicMock()

        await light.async_turn_on(**{ATTR_BRIGHTNESS: 255})

        mock_coordinator.async_set_light_brightness.assert_called_once_with(
            "light1",
            6,
        )

    @pytest.mark.asyncio
    async def test_async_turn_off(self, mock_coordinator) -> None:
        """Test turning light off."""
        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        light.async_write_ha_state = MagicMock()

        await light.async_turn_off()

        mock_coordinator.async_set_light_mode.assert_called_once_with(
            "light1",
            LIGHT_MODE_OFF,
        )
        assert light._attr_is_on is False
        light.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_off_with_kwargs(self, mock_coordinator) -> None:
        """Test turning light off ignores extra kwargs."""
        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        light.async_write_ha_state = MagicMock()

        await light.async_turn_off(some_extra_kwarg="value")

        mock_coordinator.async_set_light_mode.assert_called_once_with(
            "light1",
            LIGHT_MODE_OFF,
        )

    @pytest.mark.asyncio
    async def test_async_turn_on_error(self, mock_coordinator) -> None:
        """Test turning light on surfaces Home Assistant errors."""
        mock_coordinator.async_set_light_mode.side_effect = Exception("API error")

        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        light.async_write_ha_state = MagicMock()

        with pytest.raises(HomeAssistantError, match="Unable to turn on light"):
            await light.async_turn_on()

        light.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_turn_off_error(self, mock_coordinator) -> None:
        """Test turning light off surfaces Home Assistant errors."""
        mock_coordinator.async_set_light_mode.side_effect = Exception("API error")

        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )
        light.async_write_ha_state = MagicMock()

        with pytest.raises(HomeAssistantError, match="Unable to turn off light"):
            await light.async_turn_off()

        light.async_write_ha_state.assert_not_called()

    def test_missing_light_data(self, mock_coordinator) -> None:
        """Test handling missing light data."""
        mock_coordinator.data["protect"]["lights"]["light1"] = {}

        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        # Should use defaults
        assert light.available is False
        assert light._attr_is_on is False
        assert light._attr_brightness is None

    def test_missing_mode_settings(self, mock_coordinator) -> None:
        """Test handling missing mode settings."""
        del mock_coordinator.data["protect"]["lights"]["light1"]["lightModeSettings"]

        light = UnifiProtectLight(
            coordinator=mock_coordinator,
            light_id="light1",
        )

        # Should default to OFF mode
        assert light._attr_is_on is False


class TestProtectLightPatchBodies:
    """Test light entity actions call through facade down to protect_client._patch."""

    @pytest.mark.asyncio
    async def test_light_turn_on_and_off_sends_spec_patch_body(self) -> None:
        """Test light turn on/off sends lightModeSettings via facade."""
        coordinator = MagicMock()
        client = UniFiProtectClient(
            auth=ApiKeyAuth(api_key="test-key"),
            base_url="https://192.168.1.1",
            connection_type=ConnectionType.LOCAL,
        )
        client._patch = AsyncMock(
            return_value={"id": "light1", "mac": "00:11:22:33:44:66"}
        )
        coordinator.protect_client = client
        coordinator.async_set_light_mode = AsyncMock(side_effect=client.lights.set_mode)
        coordinator.async_set_light_brightness = AsyncMock(
            side_effect=client.lights.set_brightness
        )
        coordinator.data = {
            "protect": {
                "lights": {
                    "light1": {
                        "name": "Test Light",
                        "lightModeSettings": {"mode": "off"},
                        "lightDeviceSettings": {"ledLevel": 3},
                    }
                }
            }
        }
        light = UnifiProtectLight(coordinator, "light1")
        light.async_write_ha_state = MagicMock()

        # Turn on without brightness
        await light.async_turn_on()
        client._patch.assert_awaited_once_with(
            client.build_api_path("/lights/light1"),
            json_data={"lightModeSettings": {"mode": "always"}},
        )

        # Turn off
        client._patch.reset_mock()
        await light.async_turn_off()
        client._patch.assert_awaited_once_with(
            client.build_api_path("/lights/light1"),
            json_data={"lightModeSettings": {"mode": "off"}},
        )

        # Turn on with brightness 255 (100% -> ledLevel 6)
        client._patch.reset_mock()
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 255})
        assert client._patch.await_count == 2
        client._patch.assert_has_awaits(
            [
                call(
                    client.build_api_path("/lights/light1"),
                    json_data={"lightDeviceSettings": {"ledLevel": 6}},
                ),
                call(
                    client.build_api_path("/lights/light1"),
                    json_data={"lightModeSettings": {"mode": "always"}},
                ),
            ]
        )

    def _setup_light(
        self, led_level: float | None = 3
    ) -> tuple[MagicMock, UniFiProtectClient, UnifiProtectLight]:
        coordinator = MagicMock()
        client = UniFiProtectClient(
            auth=ApiKeyAuth(api_key="test-key"),
            base_url="https://192.168.1.1",
            connection_type=ConnectionType.LOCAL,
        )
        client._patch = AsyncMock(
            return_value={"id": "light1", "mac": "00:11:22:33:44:66"}
        )
        coordinator.protect_client = client
        coordinator.async_set_light_mode = AsyncMock(side_effect=client.lights.set_mode)
        coordinator.async_set_light_brightness = AsyncMock(
            side_effect=client.lights.set_brightness
        )
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
        light.async_write_ha_state = MagicMock()
        return coordinator, client, light

    @pytest.mark.asyncio
    async def test_light_write_brightness_mapping_255_maps_to_6(self) -> None:
        """Test write path maps HA brightness 255 to ledLevel 6."""
        _, client, light = self._setup_light()
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 255})
        client._patch.assert_has_awaits(
            [
                call(
                    client.build_api_path("/lights/light1"),
                    json_data={"lightDeviceSettings": {"ledLevel": 6}},
                ),
                call(
                    client.build_api_path("/lights/light1"),
                    json_data={"lightModeSettings": {"mode": "always"}},
                ),
            ]
        )
        assert light._attr_brightness == 255

    @pytest.mark.asyncio
    async def test_light_write_brightness_mapping_128_maps_to_3(self) -> None:
        """Test write path maps HA brightness 128 to ledLevel 3."""
        _, client, light = self._setup_light()
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 128})
        client._patch.assert_has_awaits(
            [
                call(
                    client.build_api_path("/lights/light1"),
                    json_data={"lightDeviceSettings": {"ledLevel": 3}},
                ),
                call(
                    client.build_api_path("/lights/light1"),
                    json_data={"lightModeSettings": {"mode": "always"}},
                ),
            ]
        )
        assert light._attr_brightness == 128

    @pytest.mark.asyncio
    async def test_light_write_brightness_mapping_1_maps_to_1(self) -> None:
        """Test write path maps HA brightness 1 to ledLevel 1."""
        _, client, light = self._setup_light()
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 1})
        client._patch.assert_has_awaits(
            [
                call(
                    client.build_api_path("/lights/light1"),
                    json_data={"lightDeviceSettings": {"ledLevel": 1}},
                ),
                call(
                    client.build_api_path("/lights/light1"),
                    json_data={"lightModeSettings": {"mode": "always"}},
                ),
            ]
        )
        assert light._attr_brightness == 1

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
        """Test round trip: set 255, data ledLevel 6, read 255."""
        coordinator, client, light = self._setup_light(led_level=3)
        await light.async_turn_on(**{ATTR_BRIGHTNESS: 255})
        assert light._attr_brightness == 255
        client._patch.assert_any_await(
            client.build_api_path("/lights/light1"),
            json_data={"lightDeviceSettings": {"ledLevel": 6}},
        )
        coordinator.data["protect"]["lights"]["light1"]["lightDeviceSettings"][
            "ledLevel"
        ] = 6
        light._update_from_data()
        assert light._attr_brightness == 255
        assert light._attr_extra_state_attributes[ATTR_LIGHT_LEVEL] == 6

    @pytest.mark.asyncio
    async def test_light_turn_on_mode_fallback(self) -> None:
        """Test turn on takes fallback when coordinator action is non-coroutine."""
        coordinator, client, light = self._setup_light()
        coordinator.async_set_light_mode = MagicMock()
        client.lights.set_mode = AsyncMock()

        await light.async_turn_on()

        client.lights.set_mode.assert_awaited_once_with(
            "light1",
            LIGHT_MODE_ALWAYS,
        )

    @pytest.mark.asyncio
    async def test_light_turn_off_mode_fallback(self) -> None:
        """Test turn off takes fallback when coordinator action is non-coroutine."""
        coordinator, client, light = self._setup_light()
        coordinator.async_set_light_mode = MagicMock()
        client.lights.set_mode = AsyncMock()

        await light.async_turn_off()

        client.lights.set_mode.assert_awaited_once_with(
            "light1",
            LIGHT_MODE_OFF,
        )

    @pytest.mark.asyncio
    async def test_light_brightness_fallback(self) -> None:
        """Test brightness takes fallback when coordinator action is non-coroutine."""
        coordinator, client, light = self._setup_light()
        coordinator.async_set_light_brightness = MagicMock()
        client.lights.set_brightness = AsyncMock()

        await light.async_turn_on(**{ATTR_BRIGHTNESS: 128})

        client.lights.set_brightness.assert_awaited_once_with(
            "light1",
            3,
        )
