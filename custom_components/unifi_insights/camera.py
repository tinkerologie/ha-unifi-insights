# Copyright 2026 UniFi Insights contributors
"""Support for UniFi Protect cameras."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import callback

from .const import (
    ATTR_CAMERA_ID,
    ATTR_CAMERA_NAME,
    ATTR_CAMERA_STATE,
    ATTR_CAMERA_TYPE,
    ATTR_IS_PACKAGE_CAMERA,
    ATTR_PARENT_CAMERA_ID,
    DEVICE_TYPE_CAMERA,
)
from .data_transforms import protect_collection, protect_device
from .entity import UnifiProtectEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import UnifiInsightsConfigEntry
    from .coordinators import UnifiInsightsFacadeCoordinator
    from .coordinators.protect import StreamQuality

_LOGGER = logging.getLogger(__name__)

# Camera streaming/snapshots can be parallel
PARALLEL_UPDATES = 1

# Every camera gets one entity per channel. The medium and low entities are
# disabled by default, so only the high one shows up on a fresh install.
CAMERA_QUALITIES: tuple[StreamQuality, ...] = ("high", "medium", "low")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up cameras for UniFi Protect integration."""
    coordinator: UnifiInsightsFacadeCoordinator = entry.runtime_data.coordinator
    _ = hass  # hass is unused but kept for HA signature

    # Skip if Protect API is not available
    if not coordinator.protect_client:
        _LOGGER.debug("Skipping camera setup - Protect API not available")
        return

    known_camera_ids: set[str] = set()

    @callback
    def async_discover_cameras() -> None:
        """Discover and add new cameras."""
        cameras = protect_collection(coordinator.data, "cameras") or {}
        new_entities: list[Camera] = []
        for camera_id, camera_data in cameras.items():
            if camera_id in known_camera_ids:
                continue
            _LOGGER.debug(
                "Adding camera entities for %s", camera_data.get("name", camera_id)
            )
            known_camera_ids.add(camera_id)
            # One entity per channel. The medium and low ones are disabled by
            # default; all three attach to the camera's existing device.
            new_entities.extend(
                UnifiProtectCamera(
                    coordinator=coordinator,
                    camera_id=camera_id,
                    quality=quality,
                )
                for quality in CAMERA_QUALITIES
            )

        if new_entities:
            _LOGGER.info("Adding %d UniFi Protect cameras", len(new_entities))
            async_add_entities(new_entities)

    async_discover_cameras()
    entry.async_on_unload(coordinator.async_add_listener(async_discover_cameras))


class UnifiProtectCamera(UnifiProtectEntity, Camera):
    """Representation of a UniFi Protect Camera."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        camera_id: str,
        quality: StreamQuality = "high",
    ) -> None:
        """Initialize the camera."""
        self._quality: StreamQuality = quality
        # Only the extra channels carry an entity type suffix, so the high
        # channel keeps the unique ID it has always had.
        super().__init__(
            coordinator,
            DEVICE_TYPE_CAMERA,
            camera_id,
            None if quality == "high" else quality,
        )
        Camera.__init__(self)
        if quality == "high":
            self._attr_name = None
        elif quality == "medium":
            self._attr_translation_key = "medium_resolution"
            self._attr_entity_registry_enabled_default = False
        else:
            self._attr_translation_key = "low_resolution"
            self._attr_entity_registry_enabled_default = False

        # Set up camera features - use CameraEntityFeature flags for proper type
        # STREAM enables streaming support for the camera
        self._attr_supported_features = CameraEntityFeature.STREAM

        # Set entity category
        self._attr_entity_category = None

        # Set initial state
        self._update_from_data()

    def _update_from_data(self) -> None:
        """Update entity from data."""
        camera_data = (
            protect_device(self.coordinator.data, "cameras", self._device_id) or {}
        )

        # Set attributes
        self._attr_extra_state_attributes = {
            ATTR_CAMERA_ID: self._device_id,
            ATTR_CAMERA_NAME: camera_data.get("name"),
            ATTR_CAMERA_STATE: camera_data.get("state"),
            ATTR_CAMERA_TYPE: camera_data.get("_camera_type", "regular"),
            ATTR_IS_PACKAGE_CAMERA: camera_data.get("_is_package_camera", False),
            ATTR_PARENT_CAMERA_ID: camera_data.get("_parent_camera_id"),
        }

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the camera."""
        _LOGGER.debug("Getting camera image for %s", self._device_id)
        # The Protect snapshot endpoint does not accept width/height params
        # (they cause an HTTP 500). HA scales the returned image as needed.
        _ = (width, height)
        protect = self.coordinator.require_protect_coordinator()
        return await protect.async_get_camera_snapshot(self._device_id)

    async def stream_source(self) -> str | None:
        """
        Return the stream source.

        Returns the RTSPS URL of this entity's channel. The high channel falls
        back to a static URL when the API cannot create one; the medium and low
        channels return None instead, because that static URL is the high
        channel and serving it would be the stream the user turned away from.
        """
        _LOGGER.debug("Getting %s stream source for %s", self._quality, self._device_id)

        # Try to use the API to create a dynamic RTSPS stream (preferred method)
        protect = self.coordinator.require_protect_coordinator()
        dynamic_url = await protect.async_create_camera_stream_url(
            self._device_id, self._quality
        )
        if self._quality != "high":
            # The static URL below is the high channel, so it is no fallback
            # for the medium and low channels: they serve their own URL or none.
            if dynamic_url:
                _LOGGER.debug(
                    "Using dynamic %s stream URL for camera %s",
                    self._quality,
                    self._device_id,
                )
            return dynamic_url
        if dynamic_url:
            _LOGGER.debug("Using dynamic stream URL for camera %s", self._device_id)
            return dynamic_url

        # Fallback: Construct static RTSPS URL
        nvr_host = None

        # Try to get from NVR data
        nvrs = protect_collection(self.coordinator.data, "nvrs") or {}
        for nvr in nvrs.values():
            if nvr.get("host"):
                nvr_host = nvr["host"]
                break

        # Fall back to configured base URL host
        if not nvr_host and self.coordinator.protect_client:
            base_url = str(self.coordinator.protect_client.base_url)
            if "://" in base_url:
                nvr_host = (
                    base_url.split("://")[1]
                    .split("/", maxsplit=1)[0]
                    .split(":", maxsplit=1)[0]
                )

        if not nvr_host:
            _LOGGER.warning(
                "Could not determine NVR host for camera %s stream", self._device_id
            )
            return None

        # Static RTSPS URL format
        stream_url = f"rtsps://{nvr_host}:7441/{self._device_id}?enableSrtp"
        _LOGGER.debug(
            "Using static stream URL for camera %s: %s", self._device_id, stream_url
        )
        return stream_url
