# Copyright 2026 UniFi Insights contributors
"""Support for UniFi Protect number entities."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import UnitOfDataRate, UnitOfInformation, UnitOfTime
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .console_identity import resolve_console_namespace
from .const import (
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
from .coordinators import UnifiFacadeCoordinator
from .coordinators.voucher_state import (
    VOUCHER_INPUTS,
    cast_input,
    restore_input,
    voucher_site_ids,
)
from .entity import (
    UnifiProtectEntity,
    async_call_coordinator_action,
    build_site_device_info,
)
from .helpers import async_migrate_site_entity
from .led_level import led_level_to_percent, percent_to_led_level

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import UnifiInsightsConfigEntry

_LOGGER = logging.getLogger(__name__)

# Number entities are action-based, allow parallel execution
PARALLEL_UPDATES = 1

VOUCHER_NUMBER_DESCRIPTIONS: tuple[NumberEntityDescription, ...] = (
    NumberEntityDescription(
        key="voucher_duration",
        translation_key="voucher_duration",
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=VOUCHER_INPUTS["voucher_duration"].minimum,
        native_max_value=VOUCHER_INPUTS["voucher_duration"].maximum,
        native_step=VOUCHER_INPUTS["voucher_duration"].step,
        mode=NumberMode.BOX,
    ),
    NumberEntityDescription(
        key="voucher_guest_limit",
        translation_key="voucher_guest_limit",
        native_min_value=VOUCHER_INPUTS["voucher_guest_limit"].minimum,
        native_max_value=VOUCHER_INPUTS["voucher_guest_limit"].maximum,
        native_step=VOUCHER_INPUTS["voucher_guest_limit"].step,
        mode=NumberMode.BOX,
    ),
    NumberEntityDescription(
        key="voucher_download_limit",
        translation_key="voucher_download_limit",
        device_class=NumberDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        native_min_value=VOUCHER_INPUTS["voucher_download_limit"].minimum,
        native_max_value=VOUCHER_INPUTS["voucher_download_limit"].maximum,
        native_step=VOUCHER_INPUTS["voucher_download_limit"].step,
        mode=NumberMode.BOX,
    ),
    NumberEntityDescription(
        key="voucher_upload_limit",
        translation_key="voucher_upload_limit",
        device_class=NumberDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        native_min_value=VOUCHER_INPUTS["voucher_upload_limit"].minimum,
        native_max_value=VOUCHER_INPUTS["voucher_upload_limit"].maximum,
        native_step=VOUCHER_INPUTS["voucher_upload_limit"].step,
        mode=NumberMode.BOX,
    ),
    NumberEntityDescription(
        key="voucher_data_limit",
        translation_key="voucher_data_limit",
        device_class=NumberDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        native_min_value=VOUCHER_INPUTS["voucher_data_limit"].minimum,
        native_max_value=VOUCHER_INPUTS["voucher_data_limit"].maximum,
        native_step=VOUCHER_INPUTS["voucher_data_limit"].step,
        mode=NumberMode.BOX,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities for UniFi Protect integration."""
    coordinator: UnifiFacadeCoordinator = entry.runtime_data.coordinator
    _async_setup_voucher_numbers(hass, entry, coordinator, async_add_entities)

    # Skip if Protect API is not available
    if not coordinator.protect_client:
        _LOGGER.debug("Skipping number setup - Protect API not available")
        return

    known_number_keys: set[tuple[str, str]] = set()

    @callback
    def async_discover_numbers() -> None:
        """Discover and add new number entities."""
        if (
            not coordinator.data
            or not isinstance(coordinator.data, dict)
            or "protect" not in coordinator.data
            or not isinstance(coordinator.data["protect"], dict)
        ):
            return

        protect = coordinator.data["protect"]
        entities: list[NumberEntity] = []

        # Add camera microphone volume numbers
        cameras = protect.get("cameras", {})
        if isinstance(cameras, dict):
            for camera_id, camera_data in cameras.items():
                if not isinstance(camera_data, dict):
                    continue
                key = (camera_id, "mic_volume")
                if key in known_number_keys:
                    continue
                known_number_keys.add(key)
                _LOGGER.debug(
                    "Adding microphone volume number for camera %s",
                    camera_data.get("name", camera_id),
                )
                entities.append(
                    UnifiProtectMicrophoneVolumeNumber(
                        coordinator=coordinator,
                        camera_id=camera_id,
                    )
                )

        # Add light brightness level numbers
        lights = protect.get("lights", {})
        if isinstance(lights, dict):
            for light_id, light_data in lights.items():
                if not isinstance(light_data, dict):
                    continue
                key = (light_id, "light_level")
                if key in known_number_keys:
                    continue
                known_number_keys.add(key)
                _LOGGER.debug(
                    "Adding brightness level number for light %s",
                    light_data.get("name", light_id),
                )
                entities.append(
                    UnifiProtectLightLevelNumber(
                        coordinator=coordinator,
                        light_id=light_id,
                    )
                )

        # Add chime volume and repeat times numbers
        chimes = protect.get("chimes", {})
        if isinstance(chimes, dict):
            for chime_id, chime_data in chimes.items():
                if not isinstance(chime_data, dict):
                    continue
                vol_key = (chime_id, "chime_volume")
                if vol_key not in known_number_keys:
                    known_number_keys.add(vol_key)
                    _LOGGER.debug(
                        "Adding volume number for chime %s",
                        chime_data.get("name", chime_id),
                    )
                    entities.append(
                        UnifiProtectChimeVolumeNumber(
                            coordinator=coordinator,
                            chime_id=chime_id,
                        )
                    )

                rep_key = (chime_id, "repeat_times")
                if rep_key not in known_number_keys:
                    known_number_keys.add(rep_key)
                    _LOGGER.debug(
                        "Adding repeat times number for chime %s",
                        chime_data.get("name", chime_id),
                    )
                    entities.append(
                        UnifiProtectChimeRepeatTimesNumber(
                            coordinator=coordinator,
                            chime_id=chime_id,
                        )
                    )

        if entities:
            _LOGGER.info("Adding %d UniFi Protect number entities", len(entities))
            async_add_entities(entities)

    async_discover_numbers()
    entry.async_on_unload(coordinator.async_add_listener(async_discover_numbers))


class UnifiProtectMicrophoneVolumeNumber(UnifiProtectEntity, NumberEntity):
    """Representation of a UniFi Protect Camera Microphone Volume Number."""

    _attr_has_entity_name = True
    _attr_translation_key = "mic_volume"
    # The Protect camera PATCH accepts micVolume 1-100.
    _attr_native_min_value = 1
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        camera_id: str,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(
            coordinator, DEVICE_TYPE_CAMERA, camera_id, "microphone_volume"
        )

        # Set entity category
        self._attr_entity_category = EntityCategory.CONFIG

        # Set initial state
        self._update_from_data()

    def _update_from_data(self) -> None:
        """Update entity from data."""
        camera_data = self.coordinator.data["protect"]["cameras"].get(
            self._device_id, {}
        )

        # Set value; unknown when the camera doesn't report one (0 is below
        # the slider minimum, so it can't stand in for a missing value)
        self._attr_native_value = camera_data.get("micVolume")

        # Set attributes
        mic_enabled = camera_data.get("isMicEnabled")
        if mic_enabled is None:
            mic_enabled = camera_data.get("micEnabled", False)

        self._attr_extra_state_attributes = {
            ATTR_CAMERA_ID: self._device_id,
            ATTR_CAMERA_NAME: camera_data.get("name"),
            ATTR_MIC_ENABLED: bool(mic_enabled),
        }

    async def async_set_native_value(self, value: float) -> None:
        """Set the microphone volume."""
        _LOGGER.debug(
            "Setting microphone volume to %s for camera %s", value, self._device_id
        )

        await async_call_coordinator_action(
            self.coordinator,
            "async_set_microphone_volume",
            f"Unable to set microphone volume for camera {self._device_id}",
            self._device_id,
            int(value),
            fallback_factory=lambda: (
                self.coordinator.protect_client.set_microphone_volume(  # type: ignore[union-attr]
                    camera_id=self._device_id,
                    volume=int(value),
                )
            ),
        )
        self._attr_native_value = value
        self.async_write_ha_state()


class UnifiProtectLightLevelNumber(UnifiProtectEntity, NumberEntity):
    """Representation of a UniFi Protect Light Brightness Level Number."""

    _attr_has_entity_name = True
    _attr_translation_key = "brightness_level"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        light_id: str,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, DEVICE_TYPE_LIGHT, light_id, "brightness_level")

        # Set entity category
        self._attr_entity_category = EntityCategory.CONFIG

        # Set initial state
        self._update_from_data()

    def _update_from_data(self) -> None:
        """Update entity from data."""
        light_data = self.coordinator.data["protect"]["lights"].get(self._device_id, {})

        # The slider is a 0-100 percentage; Protect stores a 1-6 ledLevel.
        # lightDeviceSettings can be present but None, as in light.py.
        device_settings = light_data.get("lightDeviceSettings")
        led_level = (
            device_settings.get("ledLevel")
            if isinstance(device_settings, dict)
            else None
        )
        self._attr_native_value = led_level_to_percent(led_level)

        # Set attributes
        self._attr_extra_state_attributes = {
            ATTR_LIGHT_ID: self._device_id,
            ATTR_LIGHT_NAME: light_data.get("name"),
            ATTR_LIGHT_LEVEL: led_level,
        }

    async def async_set_native_value(self, value: float) -> None:
        """Set the light brightness level."""
        led_level = percent_to_led_level(value)
        _LOGGER.debug(
            "Setting light level to %s%% (led_level %s) for light %s",
            value,
            led_level,
            self._device_id,
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
        # Show the percentage Protect will report for the level it was given
        self._attr_native_value = led_level_to_percent(led_level)
        self.async_write_ha_state()


class UnifiProtectChimeVolumeNumber(UnifiProtectEntity, NumberEntity):
    """Representation of a UniFi Protect Chime Volume Number."""

    _attr_has_entity_name = True
    _attr_translation_key = "chime_volume"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        chime_id: str,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, DEVICE_TYPE_CHIME, chime_id, "volume")

        # Set entity category
        self._attr_entity_category = EntityCategory.CONFIG

        # Set initial state
        self._update_from_data()

    def _update_from_data(self) -> None:
        """Update entity from data."""
        chime_data = self.coordinator.data["protect"]["chimes"].get(self._device_id, {})

        # Get ring settings - use the first camera's settings or default to 80
        ring_settings = chime_data.get("ringSettings", [])
        volume = 80  # Default volume

        if ring_settings:
            # Use the first camera's settings
            volume = ring_settings[0].get("volume", 80)

        # Set value
        self._attr_native_value = volume

        # Set attributes
        self._attr_extra_state_attributes = {
            ATTR_CHIME_ID: self._device_id,
            ATTR_CHIME_NAME: chime_data.get("name"),
            ATTR_CHIME_VOLUME: volume,
        }

    async def async_set_native_value(self, value: float) -> None:
        """Set the chime volume level."""
        _LOGGER.debug("Setting chime volume to %s for chime %s", value, self._device_id)

        await async_call_coordinator_action(
            self.coordinator,
            "async_set_chime_volume",
            f"Unable to set volume for chime {self._device_id}",
            self._device_id,
            int(value),
            fallback_factory=lambda: self.coordinator.protect_client.set_chime_volume(  # type: ignore[union-attr]
                chime_id=self._device_id,
                volume=int(value),
            ),
        )
        self._attr_native_value = int(value)
        self.async_write_ha_state()


class UnifiProtectChimeRepeatTimesNumber(UnifiProtectEntity, NumberEntity):
    """Representation of a UniFi Protect Chime Repeat Times Number."""

    _attr_has_entity_name = True
    _attr_translation_key = "repeat_times"
    _attr_native_min_value = 1
    _attr_native_max_value = 10
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        chime_id: str,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, DEVICE_TYPE_CHIME, chime_id, "repeat_times")

        # Set entity category
        self._attr_entity_category = EntityCategory.CONFIG

        # Set initial state
        self._update_from_data()

    def _update_from_data(self) -> None:
        """Update entity from data."""
        chime_data = self.coordinator.data["protect"]["chimes"].get(self._device_id, {})

        # Get ring settings - use the first camera's settings or default to 3
        ring_settings = chime_data.get("ringSettings", [])
        repeat_times = 3  # Default repeat times

        if ring_settings:
            # Use the first camera's settings
            repeat_times = ring_settings[0].get("repeatTimes", 3)

        # Set value
        self._attr_native_value = repeat_times

        # Set attributes
        self._attr_extra_state_attributes = {
            ATTR_CHIME_ID: self._device_id,
            ATTR_CHIME_NAME: chime_data.get("name"),
            ATTR_CHIME_REPEAT_TIMES: repeat_times,
        }

    async def async_set_native_value(self, value: float) -> None:
        """Set the chime repeat times."""
        _LOGGER.debug(
            "Setting chime repeat times to %s for chime %s", value, self._device_id
        )

        await async_call_coordinator_action(
            self.coordinator,
            "async_set_chime_repeat",
            f"Unable to set repeat count for chime {self._device_id}",
            self._device_id,
            int(value),
            fallback_factory=lambda: self.coordinator.protect_client.set_chime_repeat(  # type: ignore[union-attr]
                chime_id=self._device_id,
                repeat_times=int(value),
            ),
        )
        self._attr_native_value = int(value)
        self.async_write_ha_state()


def _async_setup_voucher_numbers(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    coordinator: UnifiFacadeCoordinator,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up hotspot voucher input number entities."""
    if not isinstance(coordinator.data, dict) or "vouchers" not in coordinator.data:
        return

    known: set[tuple[str, str]] = set()

    @callback
    def discover() -> None:
        """Discover eligible owned voucher inputs without duplicating registry rows."""
        new_entities: list[UnifiVoucherInputNumber] = []
        namespace = resolve_console_namespace(entry)
        for site_id in voucher_site_ids(coordinator.data):
            for description in VOUCHER_NUMBER_DESCRIPTIONS:
                key = (site_id, description.key)
                if key in known:
                    continue
                unique_id = async_migrate_site_entity(
                    hass, entry, "number", namespace, site_id, suffix=description.key
                )
                if unique_id is None:
                    continue
                entity = UnifiVoucherInputNumber(
                    coordinator, description, site_id, unique_id=unique_id
                )
                known.add(key)
                new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(discover))
    discover()


class UnifiVoucherInputNumber(CoordinatorEntity[UnifiFacadeCoordinator], RestoreNumber):
    """Input number entity holding parameters for next generated voucher."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    entity_description: NumberEntityDescription

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        description: NumberEntityDescription,
        site_id: str,
        *,
        unique_id: str,
    ) -> None:
        """Initialize the voucher input number."""
        super().__init__(coordinator)
        self.entity_description = description
        self._site_id = site_id
        self._attr_unique_id = unique_id
        self._attr_device_info = DeviceInfo(
            **build_site_device_info(coordinator.data, site_id)  # type: ignore[typeddict-item]
        )

    @property
    def available(self) -> bool:
        """Return True if vouchers are available for this site."""
        return bool(self.coordinator.vouchers_available(self._site_id))

    @property
    def native_value(self) -> float | None:
        """Return current native value from settings."""
        settings = self.coordinator.get_voucher_settings(self._site_id)
        val = getattr(settings, VOUCHER_INPUTS[self.entity_description.key].field)
        return float(val) if val is not None else None

    async def async_added_to_hass(self) -> None:
        """Restore previous state on startup."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            restored = restore_input(self.entity_description.key, last.native_value)
            if restored is not None:
                settings = self.coordinator.get_voucher_settings(self._site_id)
                field = VOUCHER_INPUTS[self.entity_description.key].field
                setattr(settings, field, restored)

    async def async_set_native_value(self, value: float) -> None:
        """Update settings value held in HA without calling external API."""
        settings = self.coordinator.get_voucher_settings(self._site_id)
        field = VOUCHER_INPUTS[self.entity_description.key].field
        setattr(settings, field, cast_input(self.entity_description.key, value))
        self.async_write_ha_state()
