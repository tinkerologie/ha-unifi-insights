# Copyright 2026 UniFi Insights contributors
"""Support for UniFi Protect lights."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.core import callback

from .const import (
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
from .entity import UnifiProtectEntity, async_call_coordinator_action
from .led_level import MAX_LED_LEVEL, MIN_LED_LEVEL, valid_led_level

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import UnifiInsightsConfigEntry
    from .coordinators import UnifiFacadeCoordinator

_LOGGER = logging.getLogger(__name__)

# Lights are action-based, allow parallel execution
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up lights for UniFi Protect integration."""
    _ = hass
    coordinator: UnifiFacadeCoordinator = entry.runtime_data.coordinator

    # Skip if Protect API is not available
    if not coordinator.protect_client:
        _LOGGER.debug("Skipping light setup - Protect API not available")
        return

    known_light_ids: set[str] = set()

    @callback
    def async_discover_lights() -> None:
        """Discover and add new lights."""
        if (
            not coordinator.data
            or not isinstance(coordinator.data, dict)
            or "protect" not in coordinator.data
            or not isinstance(coordinator.data["protect"], dict)
        ):
            return

        lights = coordinator.data["protect"].get("lights", {})
        if not isinstance(lights, dict):
            return

        new_entities: list[LightEntity] = []
        for light_id, light_data in lights.items():
            # Skip malformed entries to avoid crashing
            if not isinstance(light_data, dict) or (
                "name" not in light_data and "id" not in light_data
            ):
                _LOGGER.warning("Skipping malformed light entry: %s", light_id)
                continue
            if light_id in known_light_ids:
                continue
            try:
                _LOGGER.debug(
                    "Adding light entity for %s", light_data.get("name", light_id)
                )
                light = UnifiProtectLight(
                    coordinator=coordinator,
                    light_id=light_id,
                )
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.warning("Skipping light %s due to error: %s", light_id, err)
                continue
            else:
                # Only after construction succeeds, so a light that failed on a
                # malformed payload is retried on the next coordinator update
                # instead of being hidden until the entry is reloaded.
                known_light_ids.add(light_id)
                new_entities.append(light)

        if new_entities:
            _LOGGER.info("Adding %d UniFi Protect lights", len(new_entities))
            async_add_entities(new_entities)

    async_discover_lights()
    entry.async_on_unload(coordinator.async_add_listener(async_discover_lights))


class UnifiProtectLight(UnifiProtectEntity, LightEntity):
    """Representation of a UniFi Protect Light."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        light_id: str,
    ) -> None:
        """Initialize the light."""
        super().__init__(coordinator, DEVICE_TYPE_LIGHT, light_id)

        # Set up light features - brightness is indicated via ColorMode, not features
        self._attr_color_mode = ColorMode.BRIGHTNESS
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}

        # Set entity category
        self._attr_entity_category = None

        # Set initial state
        self._update_from_data()

    def _update_from_data(self) -> None:
        """Update entity from data."""
        light_data = self.coordinator.data["protect"]["lights"].get(self._device_id, {})

        # Set state
        light_mode = light_data.get("lightModeSettings", {}).get("mode", LIGHT_MODE_OFF)
        self._attr_is_on = light_mode != LIGHT_MODE_OFF

        # Set brightness
        device_settings = light_data.get("lightDeviceSettings")
        led_level = (
            device_settings.get("ledLevel")
            if isinstance(device_settings, dict)
            else None
        )
        valid_level = valid_led_level(led_level)
        if valid_level is not None:
            self._attr_brightness = round(valid_level * 255 / MAX_LED_LEVEL)
        else:
            self._attr_brightness = None

        # Set attributes
        self._attr_extra_state_attributes = {
            ATTR_LIGHT_ID: self._device_id,
            ATTR_LIGHT_NAME: light_data.get("name"),
            ATTR_LIGHT_STATE: light_data.get("state"),
            ATTR_LIGHT_MODE: light_mode,
            ATTR_LIGHT_LEVEL: led_level,
            ATTR_LIGHT_MOTION: light_data.get("lastMotion"),
            ATTR_LIGHT_DARK: light_data.get("isDark"),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        _LOGGER.debug("Turning on light %s", self._device_id)

        # Set brightness if provided
        if ATTR_BRIGHTNESS in kwargs:
            brightness = kwargs[ATTR_BRIGHTNESS]
            led_level = max(
                MIN_LED_LEVEL,
                min(MAX_LED_LEVEL, round(brightness * MAX_LED_LEVEL / 255)),
            )
            _LOGGER.debug(
                "Setting light %s brightness to %s (led_level %s)",
                self._device_id,
                brightness,
                led_level,
            )
            await async_call_coordinator_action(
                self.coordinator,
                "async_set_light_brightness",
                f"Unable to set brightness for light {self._device_id}",
                self._device_id,
                led_level,
                fallback_factory=lambda: (
                    self.coordinator.protect_client.lights.set_brightness(  # type: ignore[union-attr]
                        self._device_id,
                        led_level,
                    )
                ),
            )
            self._attr_brightness = brightness

        # Set light mode to always on
        await async_call_coordinator_action(
            self.coordinator,
            "async_set_light_mode",
            f"Unable to turn on light {self._device_id}",
            self._device_id,
            LIGHT_MODE_ALWAYS,
            fallback_factory=lambda: self.coordinator.protect_client.lights.set_mode(  # type: ignore[union-attr]
                self._device_id,
                LIGHT_MODE_ALWAYS,
            ),
        )

        # Update state
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        _LOGGER.debug("Turning off light %s", self._device_id)
        _ = kwargs

        # Set light mode to off
        await async_call_coordinator_action(
            self.coordinator,
            "async_set_light_mode",
            f"Unable to turn off light {self._device_id}",
            self._device_id,
            LIGHT_MODE_OFF,
            fallback_factory=lambda: self.coordinator.protect_client.lights.set_mode(  # type: ignore[union-attr]
                self._device_id,
                LIGHT_MODE_OFF,
            ),
        )

        # Update state
        self._attr_is_on = False
        self.async_write_ha_state()
