# Copyright 2026 UniFi Insights contributors
"""Support for UniFi Network status LEDs and Protect lights."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
    LightEntityDescription,
)
from homeassistant.const import EntityCategory
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
from .data_transforms import (
    MAX_LED_LEVEL,
    MIN_LED_LEVEL,
    is_legacy_led_device_id,
    is_network_led_override,
    protect_collection,
    protect_device,
    valid_led_level,
)
from .entity import UnifiInsightsEntity, UnifiProtectEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import UnifiInsightsConfigEntry
    from .coordinators import UnifiInsightsFacadeCoordinator

_LOGGER = logging.getLogger(__name__)

# Lights are action-based, allow parallel execution
PARALLEL_UPDATES = 1

NETWORK_LED = LightEntityDescription(
    key="led", translation_key="led", entity_category=EntityCategory.CONFIG
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Network status LEDs and Protect lights."""
    _ = hass
    coordinator: UnifiInsightsFacadeCoordinator = entry.runtime_data.coordinator

    known_light_ids: set[str] = set()
    known_led_keys: set[tuple[str, str, str]] = set()

    @callback
    def async_discover_network_leds() -> None:
        """Discover status LEDs from the current device snapshot."""
        data: object = coordinator.data
        devices_by_site = data.get("devices") if isinstance(data, Mapping) else None
        if not isinstance(devices_by_site, Mapping):
            return
        new_entities: list[LightEntity] = []
        for site_id, devices in devices_by_site.items():
            if not isinstance(devices, Mapping):
                continue
            for device_id, device in devices.items():
                key = (site_id, device_id, NETWORK_LED.key)
                if (
                    key not in known_led_keys
                    and isinstance(device, dict)
                    and is_network_led_override(device.get("led_override"))
                ):
                    new_entities.append(
                        UnifiNetworkLedLight(coordinator, site_id, device_id)
                    )
                    known_led_keys.add(key)
        if new_entities:
            async_add_entities(new_entities)

    @callback
    def async_discover_protect_lights() -> None:
        """Discover and add new lights."""
        lights = protect_collection(coordinator.data, "lights") or {}
        new_entities: list[LightEntity] = []
        for light_id, light_data in lights.items():
            # Skip malformed entries to avoid crashing
            if "name" not in light_data and "id" not in light_data:
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

    @callback
    def async_discover_lights() -> None:
        """Discover Network and Protect lights from the same snapshot."""
        async_discover_network_leds()
        if coordinator.protect_client:
            async_discover_protect_lights()

    async_discover_lights()
    entry.async_on_unload(coordinator.async_add_listener(async_discover_lights))


class UnifiNetworkLedLight(UnifiInsightsEntity, LightEntity):
    """An on/off control for a Network device's status LED."""

    _attr_color_mode = ColorMode.ONOFF

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        site_id: str,
        device_id: str,
    ) -> None:
        """Set up an on/off light on the existing Network device."""
        super().__init__(coordinator, NETWORK_LED, site_id, device_id)
        self._attr_supported_color_modes = {ColorMode.ONOFF}

    @property
    def available(self) -> bool:
        """Return whether the device reports a supported mode and write ID."""
        record = self.device_data or {}
        return (
            super().available
            and is_network_led_override(record.get("led_override"))
            and is_legacy_led_device_id(record.get("led_device_id"))
        )

    @property
    def is_on(self) -> bool | None:
        """Return the override state, or unknown when the LED follows its site."""
        mode = (self.device_data or {}).get("led_override")
        if mode == "on":
            return True
        if mode == "off":
            return False
        return None

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Turn the status LED on."""
        await self.coordinator.device_coordinator.async_set_led_override(
            self._site_id, self._device_id, mode="on"
        )

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Turn the status LED off."""
        await self.coordinator.device_coordinator.async_set_led_override(
            self._site_id, self._device_id, mode="off"
        )


class UnifiProtectLight(UnifiProtectEntity, LightEntity):
    """Representation of a UniFi Protect Light."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
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
        light_data = (
            protect_device(self.coordinator.data, "lights", self._device_id) or {}
        )

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
        """
        Turn the light on, setting the brightness first when one is given.

        The two writes are not one transaction: a failed mode write leaves the
        new brightness. The owner publishes each written field and the state
        follows from it.
        """
        _LOGGER.debug("Turning on light %s", self._device_id)
        protect = self.coordinator.require_protect_coordinator()

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
            await protect.async_set_light_brightness(self._device_id, led_level)

        # Set light mode to always on
        await protect.async_set_light_mode(self._device_id, LIGHT_MODE_ALWAYS)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        _LOGGER.debug("Turning off light %s", self._device_id)
        _ = kwargs

        protect = self.coordinator.require_protect_coordinator()
        await protect.async_set_light_mode(self._device_id, LIGHT_MODE_OFF)
