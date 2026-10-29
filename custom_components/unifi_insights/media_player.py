# Copyright 2026 UniFi Insights contributors
"""Support for UniFi Protect camera speaker media players."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError

from .const import DEVICE_TYPE_CAMERA, DOMAIN
from .data_transforms import protect_collection
from .entity import (
    UnifiProtectEntity,
    async_delegate_entity_id_changed,
    camera_supports_speaker,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from unifi_official_api.protect.talkback import TalkbackStream

    from . import UnifiInsightsConfigEntry
    from .coordinators import UnifiInsightsFacadeCoordinator

_LOGGER = logging.getLogger(__name__)

# Media players are action-based: run one action at a time
PARALLEL_UPDATES: Final[int] = 1
_MAX_VOLUME: Final[int] = 100


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up media player entities for UniFi Protect integration."""
    _ = hass
    coordinator: UnifiInsightsFacadeCoordinator = entry.runtime_data.coordinator

    # Skip if Protect API is not available
    if not coordinator.protect_client:
        _LOGGER.debug("Skipping media player setup - Protect API not available")
        return

    known_speaker_keys: set[tuple[str, str]] = set()

    @callback
    def async_discover_speakers() -> None:
        """Discover and add speaker entities for speaker-capable cameras."""
        entities: list[UnifiProtectCameraSpeaker] = []
        cameras = protect_collection(coordinator.data, "cameras") or {}
        for camera_id, camera in cameras.items():
            if not camera_supports_speaker(camera):
                continue
            key = (camera_id, "speaker")
            if key in known_speaker_keys:
                continue
            known_speaker_keys.add(key)
            entities.append(UnifiProtectCameraSpeaker(coordinator, camera_id))

        if entities:
            async_add_entities(entities)

    async_discover_speakers()
    entry.async_on_unload(coordinator.async_add_listener(async_discover_speakers))


class UnifiProtectCameraSpeaker(UnifiProtectEntity, MediaPlayerEntity):
    """Media player entity representing a UniFi Protect camera speaker."""

    _attr_has_entity_name = True
    _attr_translation_key = "speaker"
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.BROWSE_MEDIA
    )
    _attr_media_content_type = MediaType.MUSIC

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        camera_id: str,
    ) -> None:
        """Initialize the camera speaker media player."""
        super().__init__(coordinator, DEVICE_TYPE_CAMERA, camera_id)
        self._attr_unique_id = f"{DOMAIN}_camera_{camera_id}_speaker"
        self._current_stream: TalkbackStream | None = None
        self._completion_task: asyncio.Task[None] | None = None
        self._play_lock = asyncio.Lock()
        self._removing = False

    @property
    def available(self) -> bool:
        """Return True if device is available and camera supports speaker."""
        try:
            return super().available and camera_supports_speaker(self.device_data)
        except Exception:
            return False

    @property
    def state(self) -> MediaPlayerState:
        """Return the state of the media player."""
        if self._current_stream is not None and self._current_stream.running:
            return MediaPlayerState.PLAYING
        return MediaPlayerState.IDLE

    @property
    def volume_level(self) -> float | None:
        """
        Return the current volume level (0.0 to 1.0).

        Reads speaker volume in order:
        1. speakerSettings.speakerVolume
        2. speakerSettings.volume
        3. speakerVolume
        """
        data = self.device_data
        if not data or not isinstance(data, dict):
            return None

        speaker_settings = data.get("speakerSettings")
        volume: Any = None
        if isinstance(speaker_settings, dict):
            volume = speaker_settings.get("speakerVolume")
            if volume is None:
                volume = speaker_settings.get("volume")

        if volume is None:
            volume = data.get("speakerVolume")

        if isinstance(volume, bool) or not isinstance(volume, int):
            return None

        if 0 <= volume <= _MAX_VOLUME:
            return float(volume / float(_MAX_VOLUME))

        return None

    async def async_play_media(
        self,
        media_type: MediaType | str,
        media_id: str,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        """Play media on the camera speaker via talkback."""
        if media_source.is_media_source_id(media_id):
            play_item = await media_source.async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            if not play_item.mime_type.lower().startswith("audio/"):
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="media_not_audio",
                )
            media_type = MediaType.MUSIC
            media_id = play_item.url

        if str(media_type).lower() != MediaType.MUSIC.lower():
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="media_not_audio",
            )

        media_url = async_process_play_media_url(self.hass, media_id)

        async with self._play_lock:
            if self._removing:
                return

            if self._current_stream is not None and self._current_stream.running:
                await self._current_stream.stop()
                self._current_stream = None
                self.async_write_ha_state()

            protect = self.coordinator.require_protect_coordinator()
            stream = await protect.async_start_camera_talkback(
                self._device_id, media_url
            )
            self._current_stream = stream
            self.async_write_ha_state()

            # Track completion in a background task
            self._completion_task = self.hass.async_create_background_task(
                self._async_wait_for_completion(stream),
                name=f"{self.entity_id}_talkback_completion",
            )

    async def _async_wait_for_completion(self, stream: TalkbackStream) -> None:
        """Wait for stream completion, then transition back to IDLE."""
        try:
            await stream.wait_until_complete()
        except Exception as err:
            _LOGGER.warning("Talkback stream error for %s: %s", self.entity_id, err)
        finally:
            if self._current_stream is stream:
                self._current_stream = None
                self.async_write_ha_state()

    async def async_media_stop(self) -> None:
        """Stop current talkback stream."""
        async with self._play_lock:
            stream = self._current_stream
            if stream is not None and stream.running:
                await stream.stop()
                if self._current_stream is stream:
                    self._current_stream = None
                self.async_write_ha_state()

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,  # noqa: ARG002
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Implement the websocket media browsing helper with audio MIME filter."""
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: (
                bool(item.media_content_type)
                and item.media_content_type.startswith("audio/")
            ),
        )

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep the entity on an entity ID change; nothing here is keyed on it."""
        async_delegate_entity_id_changed(super(), old_entity_id)

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is removed from Home Assistant."""
        self._removing = True
        async with self._play_lock:
            stream = self._current_stream
            if stream is not None:
                await stream.stop()
                if self._current_stream is stream:
                    self._current_stream = None
            if self._completion_task is not None and not self._completion_task.done():
                self._completion_task.cancel()
        await super().async_will_remove_from_hass()
