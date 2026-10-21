# Copyright 2026 UniFi Insights contributors
"""Support for UniFi Protect select entities."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
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
    DOMAIN,
    HDR_MODE_AUTO,
    HDR_MODE_OFF,
    HDR_MODE_ON,
    VIDEO_MODE_DEFAULT,
    VIDEO_MODE_HIGH_FPS,
    VIDEO_MODE_SLOW_SHUTTER,
    VIDEO_MODE_SPORT,
)
from .data_transforms import (
    current_chime_ringtone_id,
    protect_collection,
    protect_device,
)
from .entity import UnifiProtectEntity, camera_supports_ptz

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import UnifiInsightsConfigEntry
    from .coordinators import UnifiInsightsFacadeCoordinator

_LOGGER = logging.getLogger(__name__)

# Select entities are action-based, allow parallel execution
PARALLEL_UPDATES = 1


def observed_ringtone_ids(chimes: Any) -> list[str]:
    """
    Return the ringtone IDs any chime on this console uses, sorted.

    The API has no ringtone catalogue, so the ringtone select offers only
    the IDs already observed.
    """
    ringtone_ids: set[str] = set()
    for chime in chimes.values() if isinstance(chimes, dict) else ():
        settings = chime.get("ringSettings") if isinstance(chime, dict) else None
        for setting in settings if isinstance(settings, list) else ():
            value = setting.get("ringtoneId") if isinstance(setting, dict) else None
            if isinstance(value, str) and value.strip():
                ringtone_ids.add(value)
    return sorted(ringtone_ids)


def require_observed_ringtone(chimes: Any, chime_id: str, ringtone_id: str) -> None:
    """Raise ``ringtone_not_found`` unless a chime already uses ``ringtone_id``."""
    if ringtone_id not in observed_ringtone_ids(chimes):
        msg = f"Ringtone ID not found: {ringtone_id}"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="ringtone_not_found",
            translation_placeholders={"ringtone_id": ringtone_id, "chime_id": chime_id},
        )


def resolve_liveview_id(liveviews: Any, name: str) -> str:
    """
    Return the native ID of the liveview shown as ``name``.

    The select offers liveview names, while the Protect owner takes the
    native ID (ADR-010), so the name is translated here.
    """
    items = liveviews.items() if isinstance(liveviews, dict) else ()
    for liveview_id, liveview in items:
        # Same fallback as the options: an unnamed liveview is offered by ID.
        if isinstance(liveview, dict) and liveview.get("name", liveview_id) == name:
            return str(liveview_id)
    msg = f"Liveview not found: {name}"
    raise ServiceValidationError(
        msg,
        translation_domain=DOMAIN,
        translation_key="liveview_not_found",
        translation_placeholders={"liveview": name},
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities for UniFi Protect integration."""
    _ = hass
    coordinator: UnifiInsightsFacadeCoordinator = entry.runtime_data.coordinator

    # Skip if Protect API is not available
    if not coordinator.protect_client:
        _LOGGER.debug("Skipping select setup - Protect API not available")
        return

    known_select_keys: set[tuple[str, str]] = set()

    @callback
    def async_discover_selects() -> None:
        """Discover and add new select entities."""
        entities: list[SelectEntity] = []

        # Add camera HDR and video mode selects
        cameras = protect_collection(coordinator.data, "cameras") or {}
        for camera_id, camera_data in cameras.items():
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
        chimes = protect_collection(coordinator.data, "chimes") or {}
        for chime_id, chime_data in chimes.items():
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
        viewers = protect_collection(coordinator.data, "viewers") or {}
        for viewer_id, viewer_data in viewers.items():
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
        coordinator: UnifiInsightsFacadeCoordinator,
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
        camera_data = (
            protect_device(self.coordinator.data, "cameras", self._device_id) or {}
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

        # The owner publishes the written mode; the option follows from it.
        protect = self.coordinator.require_protect_coordinator()
        await protect.async_set_hdr_mode(self._device_id, option)


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
        coordinator: UnifiInsightsFacadeCoordinator,
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
        camera_data = (
            protect_device(self.coordinator.data, "cameras", self._device_id) or {}
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

        protect = self.coordinator.require_protect_coordinator()
        await protect.async_set_video_mode(self._device_id, option)


class UnifiProtectChimeRingtoneSelect(UnifiProtectEntity, SelectEntity):
    """Representation of a UniFi Protect Chime Ringtone Select."""

    _attr_has_entity_name = True
    _attr_translation_key = "ringtone"

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
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
        chimes = protect_collection(self.coordinator.data, "chimes") or {}
        chime_data = chimes.get(self._device_id, {})

        # The API has no ringtone catalogue; offer only IDs observed on this console.
        self._attr_options = observed_ringtone_ids(chimes)

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
        require_observed_ringtone(
            protect_collection(self.coordinator.data, "chimes"), self._device_id, option
        )
        _LOGGER.debug("Setting ringtone to %s for chime %s", option, self._device_id)

        protect = self.coordinator.require_protect_coordinator()
        await protect.async_set_chime_ringtone(self._device_id, option)


class UnifiProtectPTZPresetSelect(UnifiProtectEntity, SelectEntity):
    """Representation of a UniFi Protect Camera PTZ Preset Select."""

    _attr_has_entity_name = True
    _attr_translation_key = "ptz_preset"
    _attr_options: ClassVar[list[str]] = ["0", "1", "2", "3", "4"]  # type: ignore[misc]

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
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
        camera_data = (
            protect_device(self.coordinator.data, "cameras", self._device_id) or {}
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

        protect = self.coordinator.require_protect_coordinator()
        await protect.async_move_ptz_to_preset(self._device_id, int(option))
        # Protect reports no current preset, so the selection is kept here.
        self._attr_current_option = option
        self.async_write_ha_state()


class UnifiProtectViewerLiveviewSelect(UnifiProtectEntity, SelectEntity):
    """Representation of a UniFi Protect Viewer Liveview Select."""

    _attr_has_entity_name = True
    _attr_translation_key = "liveview"

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
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
        viewer_data = (
            protect_device(self.coordinator.data, "viewers", self._device_id) or {}
        )
        liveviews = protect_collection(self.coordinator.data, "liveviews") or {}

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

        # The owner takes the native liveview ID, not the display name.
        liveview_id = resolve_liveview_id(
            protect_collection(self.coordinator.data, "liveviews"), option
        )
        protect = self.coordinator.require_protect_coordinator()
        await protect.async_update_viewer(self._device_id, liveview=liveview_id)
