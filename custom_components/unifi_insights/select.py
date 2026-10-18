# Copyright 2026 UniFi Insights contributors
"""Support for UniFi Protect select entities."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from .const import (
    ATTR_CAMERA_ID,
    ATTR_CAMERA_NAME,
    ATTR_CHIME_ID,
    ATTR_CHIME_NAME,
    ATTR_CHIME_RINGTONE_ID,
    ATTR_HDR_MODE,
    ATTR_VIDEO_MODE,
    ATTR_VIEWER_ID,
    ATTR_VIEWER_NAME,
    DEVICE_TYPE_CAMERA,
    DEVICE_TYPE_CHIME,
    DEVICE_TYPE_VIEWER,
    HDR_MODE_AUTO,
    HDR_MODE_OFF,
    HDR_MODE_ON,
    VIDEO_MODE_DEFAULT,
    VIDEO_MODE_HIGH_FPS,
    VIDEO_MODE_SLOW_SHUTTER,
    VIDEO_MODE_SPORT,
)
from .data_transforms import current_chime_ringtone_id
from .entity import (
    UnifiProtectEntity,
    async_call_coordinator_action,
    camera_supports_ptz,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import UnifiInsightsConfigEntry
    from .coordinators import UnifiFacadeCoordinator

_LOGGER = logging.getLogger(__name__)

# Select entities are action-based, allow parallel execution
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities for UniFi Protect integration."""
    _ = hass
    coordinator: UnifiFacadeCoordinator = entry.runtime_data.coordinator

    # Skip if Protect API is not available
    if not coordinator.protect_client:
        _LOGGER.debug("Skipping select setup - Protect API not available")
        return

    known_select_keys: set[tuple[str, str]] = set()

    @callback
    def async_discover_selects() -> None:
        """Discover and add new select entities."""
        if (
            not coordinator.data
            or not isinstance(coordinator.data, dict)
            or "protect" not in coordinator.data
            or not isinstance(coordinator.data["protect"], dict)
        ):
            return

        protect = coordinator.data["protect"]
        entities: list[SelectEntity] = []

        # Add camera HDR and video mode selects
        cameras = protect.get("cameras", {})
        if isinstance(cameras, dict):
            for camera_id, camera_data in cameras.items():
                if not isinstance(camera_data, dict):
                    continue
                hdr_key = (camera_id, "hdr_mode")
                if hdr_key not in known_select_keys:
                    known_select_keys.add(hdr_key)
                    _LOGGER.debug(
                        "Adding HDR mode select for camera %s",
                        camera_data.get("name", camera_id),
                    )
                    entities.append(
                        UnifiProtectHDRModeSelect(
                            coordinator=coordinator,
                            camera_id=camera_id,
                        )
                    )

                video_key = (camera_id, "video_mode")
                if video_key not in known_select_keys:
                    known_select_keys.add(video_key)
                    _LOGGER.debug(
                        "Adding video mode select for camera %s",
                        camera_data.get("name", camera_id),
                    )
                    entities.append(
                        UnifiProtectVideoModeSelect(
                            coordinator=coordinator,
                            camera_id=camera_id,
                        )
                    )

                # Add PTZ preset selects for cameras with PTZ support
                if camera_supports_ptz(camera_data):
                    ptz_key = (camera_id, "ptz_preset")
                    if ptz_key not in known_select_keys:
                        known_select_keys.add(ptz_key)
                        _LOGGER.debug(
                            "Adding PTZ preset select for camera %s",
                            camera_data.get("name", camera_id),
                        )
                        entities.append(
                            UnifiProtectPTZPresetSelect(
                                coordinator=coordinator,
                                camera_id=camera_id,
                            )
                        )

        # Add chime ringtone selects
        chimes = protect.get("chimes", {})
        if isinstance(chimes, dict):
            for chime_id, chime_data in chimes.items():
                if not isinstance(chime_data, dict):
                    continue
                ring_key = (chime_id, "ringtone")
                if ring_key not in known_select_keys:
                    known_select_keys.add(ring_key)
                    _LOGGER.debug(
                        "Adding ringtone select for chime %s",
                        chime_data.get("name", chime_id),
                    )
                    entities.append(
                        UnifiProtectChimeRingtoneSelect(
                            coordinator=coordinator,
                            chime_id=chime_id,
                        )
                    )

        # Add liveview selects for viewers
        viewers = protect.get("viewers", {})
        if isinstance(viewers, dict):
            for viewer_id, viewer_data in viewers.items():
                if not isinstance(viewer_data, dict):
                    continue
                lv_key = (viewer_id, "liveview")
                if lv_key not in known_select_keys:
                    known_select_keys.add(lv_key)
                    _LOGGER.debug(
                        "Adding liveview select for viewer %s",
                        viewer_data.get("name", viewer_id),
                    )
                    entities.append(
                        UnifiProtectViewerLiveviewSelect(
                            coordinator=coordinator,
                            viewer_id=viewer_id,
                        )
                    )

        if entities:
            _LOGGER.info("Adding %d UniFi Protect select entities", len(entities))
            async_add_entities(entities)

    async_discover_selects()
    entry.async_on_unload(coordinator.async_add_listener(async_discover_selects))


class UnifiProtectHDRModeSelect(UnifiProtectEntity, SelectEntity):
    """Representation of a UniFi Protect Camera HDR Mode Select."""

    _attr_has_entity_name = True
    _attr_translation_key = "hdr_mode"
    _attr_options: ClassVar[list[str]] = [HDR_MODE_AUTO, HDR_MODE_ON, HDR_MODE_OFF]  # type: ignore[misc]

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        camera_id: str,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, DEVICE_TYPE_CAMERA, camera_id, "hdr_mode")

        # Set entity category
        self._attr_entity_category = EntityCategory.CONFIG

        # Set initial state
        self._update_from_data()

    def _update_from_data(self) -> None:
        """Update entity from data."""
        camera_data = self.coordinator.data["protect"]["cameras"].get(
            self._device_id, {}
        )

        # Set current option
        self._attr_current_option = camera_data.get("hdrType", HDR_MODE_AUTO)

        # Set attributes
        self._attr_extra_state_attributes = {
            ATTR_CAMERA_ID: self._device_id,
            ATTR_CAMERA_NAME: camera_data.get("name"),
            ATTR_HDR_MODE: self._attr_current_option,
        }

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        _LOGGER.debug("Setting HDR mode to %s for camera %s", option, self._device_id)

        await async_call_coordinator_action(
            self.coordinator,
            "async_set_hdr_mode",
            f"Unable to set HDR mode for camera {self._device_id}",
            self._device_id,
            option,
            fallback_factory=lambda: self.coordinator.protect_client.set_hdr_mode(  # type: ignore[union-attr]
                camera_id=self._device_id,
                mode=option,
            ),
        )
        self._attr_current_option = option
        self.async_write_ha_state()


class UnifiProtectVideoModeSelect(UnifiProtectEntity, SelectEntity):
    """Representation of a UniFi Protect Camera Video Mode Select."""

    _attr_has_entity_name = True
    _attr_translation_key = "video_mode"
    _attr_options: ClassVar[list[str]] = [  # type: ignore[misc]
        VIDEO_MODE_DEFAULT,
        VIDEO_MODE_HIGH_FPS,
        VIDEO_MODE_SPORT,
        VIDEO_MODE_SLOW_SHUTTER,
    ]

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        camera_id: str,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, DEVICE_TYPE_CAMERA, camera_id, "video_mode")

        # Set entity category
        self._attr_entity_category = EntityCategory.CONFIG

        # Set initial state
        self._update_from_data()

    def _update_from_data(self) -> None:
        """Update entity from data."""
        camera_data = self.coordinator.data["protect"]["cameras"].get(
            self._device_id, {}
        )

        # Set current option
        self._attr_current_option = camera_data.get("videoMode", VIDEO_MODE_DEFAULT)

        # Set attributes
        self._attr_extra_state_attributes = {
            ATTR_CAMERA_ID: self._device_id,
            ATTR_CAMERA_NAME: camera_data.get("name"),
            ATTR_VIDEO_MODE: self._attr_current_option,
        }

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        _LOGGER.debug("Setting video mode to %s for camera %s", option, self._device_id)

        await async_call_coordinator_action(
            self.coordinator,
            "async_set_video_mode",
            f"Unable to set video mode for camera {self._device_id}",
            self._device_id,
            option,
            fallback_factory=lambda: (
                self.coordinator.protect_client.cameras.set_video_mode(  # type: ignore[union-attr]
                    self._device_id,
                    option,
                )
            ),
        )
        self._attr_current_option = option
        self.async_write_ha_state()


class UnifiProtectChimeRingtoneSelect(UnifiProtectEntity, SelectEntity):
    """Representation of a UniFi Protect Chime Ringtone Select."""

    _attr_has_entity_name = True
    _attr_translation_key = "ringtone"

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        chime_id: str,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, DEVICE_TYPE_CHIME, chime_id, "ringtone")

        # Set entity category
        self._attr_entity_category = EntityCategory.CONFIG

        # Set initial state
        self._update_from_data()

    def _update_from_data(self) -> None:
        """Update entity from data."""
        data = self.coordinator.data
        protect = data.get("protect") if isinstance(data, dict) else None
        chimes = protect.get("chimes") if isinstance(protect, dict) else None
        if not isinstance(chimes, dict):
            chimes = {}
        chime_data = chimes.get(self._device_id, {})
        if not isinstance(chime_data, dict):
            chime_data = {}

        # The API has no ringtone catalogue; offer only IDs observed on this console.
        ringtone_ids: set[str] = set()
        for chime in chimes.values():
            if not isinstance(chime, dict):
                continue
            settings = chime.get("ringSettings")
            if not isinstance(settings, list):
                continue
            for setting in settings:
                if not isinstance(setting, dict):
                    continue
                value = setting.get("ringtoneId")
                if isinstance(value, str) and value.strip():
                    ringtone_ids.add(value)
        self._attr_options = sorted(ringtone_ids)

        ringtone_id = current_chime_ringtone_id(chime_data)

        # Set current option
        self._attr_current_option = ringtone_id

        # Set attributes
        self._attr_extra_state_attributes = {
            ATTR_CHIME_ID: self._device_id,
            ATTR_CHIME_NAME: chime_data.get("name"),
            ATTR_CHIME_RINGTONE_ID: ringtone_id,
        }

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if option not in self._attr_options:
            msg = f"Ringtone ID not found: {option}"
            raise HomeAssistantError(msg)
        _LOGGER.debug("Setting ringtone to %s for chime %s", option, self._device_id)

        await async_call_coordinator_action(
            self.coordinator,
            "async_set_chime_ringtone",
            f"Unable to set ringtone for chime {self._device_id}",
            self._device_id,
            option,
            fallback_factory=lambda: (
                self.coordinator.protect_client.chimes.set_ringtone(  # type: ignore[union-attr]
                    chime_id=self._device_id,
                    ringtone_id=option,
                )
            ),
        )
        self._attr_current_option = option
        self.async_write_ha_state()


class UnifiProtectPTZPresetSelect(UnifiProtectEntity, SelectEntity):
    """Representation of a UniFi Protect Camera PTZ Preset Select."""

    _attr_has_entity_name = True
    _attr_translation_key = "ptz_preset"
    _attr_options: ClassVar[list[str]] = ["0", "1", "2", "3", "4"]  # type: ignore[misc]

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        camera_id: str,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, DEVICE_TYPE_CAMERA, camera_id, "ptz_preset")

        # Set entity category
        self._attr_entity_category = EntityCategory.CONFIG

        # Set initial state
        self._update_from_data()

    def _update_from_data(self) -> None:
        """Update entity from data."""
        camera_data = self.coordinator.data["protect"]["cameras"].get(
            self._device_id, {}
        )

        # Set current option
        current_preset = camera_data.get("currentPtzPreset", 0)
        self._attr_current_option = str(current_preset)

        # Set attributes
        self._attr_extra_state_attributes = {
            ATTR_CAMERA_ID: self._device_id,
            ATTR_CAMERA_NAME: camera_data.get("name"),
            "current_preset": current_preset,
        }

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        _LOGGER.debug("Setting PTZ preset to %s for camera %s", option, self._device_id)

        slot = int(option)
        await async_call_coordinator_action(
            self.coordinator,
            "async_move_ptz_to_preset",
            f"Unable to move camera {self._device_id} to PTZ preset {slot}",
            self._device_id,
            slot,
            fallback_factory=lambda: self.coordinator.protect_client.ptz_move_to_preset(  # type: ignore[union-attr]
                camera_id=self._device_id,
                slot=slot,
            ),
        )
        self._attr_current_option = option
        self.async_write_ha_state()


class UnifiProtectViewerLiveviewSelect(UnifiProtectEntity, SelectEntity):
    """Representation of a UniFi Protect Viewer Liveview Select."""

    _attr_has_entity_name = True
    _attr_translation_key = "liveview"

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        viewer_id: str,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, DEVICE_TYPE_VIEWER, viewer_id, "liveview")

        # Set entity category
        self._attr_entity_category = EntityCategory.CONFIG

        # Set initial state
        self._update_from_data()

    def _update_from_data(self) -> None:
        """Update entity from data."""
        viewer_data = self.coordinator.data["protect"]["viewers"].get(
            self._device_id, {}
        )
        liveviews = self.coordinator.data["protect"].get("liveviews", {})

        # Set options from available liveviews
        self._attr_options = [lv.get("name", lv_id) for lv_id, lv in liveviews.items()]

        # Set current option
        liveview_id = viewer_data.get("liveview")
        if liveview_id and liveview_id in liveviews:
            self._attr_current_option = liveviews[liveview_id].get("name", liveview_id)
        else:
            self._attr_current_option = None

        # Set attributes
        self._attr_extra_state_attributes = {
            ATTR_VIEWER_ID: self._device_id,
            ATTR_VIEWER_NAME: viewer_data.get("name"),
            "liveview_id": liveview_id,
        }

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        _LOGGER.debug("Setting liveview to %s for viewer %s", option, self._device_id)

        liveviews = self.coordinator.data["protect"].get("liveviews", {})
        liveview_id = None
        for lv_id, lv in liveviews.items():
            if lv.get("name") == option:
                liveview_id = lv_id
                break

        if liveview_id is None:
            msg = f"Liveview not found: {option}"
            raise HomeAssistantError(msg)

        await async_call_coordinator_action(
            self.coordinator,
            "async_update_viewer",
            f"Unable to set liveview for viewer {self._device_id}",
            self._device_id,
            fallback_factory=lambda: self.coordinator.protect_client.update_viewer(  # type: ignore[union-attr]
                viewer_id=self._device_id,
                data={"liveview": liveview_id},
            ),
            liveview=liveview_id,
        )
        self._attr_current_option = option
        self.async_write_ha_state()
