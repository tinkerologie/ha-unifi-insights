# Copyright 2026 UniFi Insights contributors
"""Tests for UniFi Protect media player platform."""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from unifi_official_api.protect.models.files import TalkbackSession
from unifi_official_api.protect.talkback import TalkbackStream

from custom_components.unifi_insights.const import (
    CAMERA_STATE_CONNECTED,
    DOMAIN,
)
from custom_components.unifi_insights.media_player import (
    PARALLEL_UPDATES,
    UnifiProtectCameraSpeaker,
    async_setup_entry,
)
from tests.fixtures.assertions import require_instance, require_not_none
from tests.fixtures.command_owners import autospec_command_owners, protect_owner
from tests.fixtures.entities import EntityCollector
from tests.fixtures.facade_data import (
    set_availability,
    with_protect_collection,
    with_protect_device,
    with_protect_fields,
)

if TYPE_CHECKING:
    from homeassistant.helpers.entity import Entity


class TestParallelUpdates:
    """Test PARALLEL_UPDATES constant."""

    def test_parallel_updates_value(self) -> None:
        """Test that PARALLEL_UPDATES is set correctly."""
        assert PARALLEL_UPDATES == 1


class TestAsyncSetupEntry:
    """Tests for async_setup_entry function and dynamic discovery."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
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

    @pytest.fixture
    def mock_entry(self, mock_coordinator: MagicMock) -> MagicMock:
        """Create mock config entry."""
        entry = MagicMock()
        entry.runtime_data = MagicMock()
        entry.runtime_data.coordinator = mock_coordinator
        return entry

    @pytest.mark.asyncio
    async def test_setup_entry_no_protect_client(
        self, hass: Any, mock_entry: MagicMock, mock_coordinator: MagicMock
    ) -> None:
        """Test setup when Protect API is not available."""
        mock_coordinator.protect_client = None
        add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, add_entities)

        add_entities.assert_not_called()

    @pytest.mark.asyncio
    async def test_setup_entry_with_speaker_flag_cases(
        self, hass: Any, mock_entry: MagicMock, mock_coordinator: MagicMock
    ) -> None:
        """Test setup creates one entity for each supported flag case."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "cameras",
            {
                "cam_top": {
                    "id": "cam_top",
                    "name": "Top Level Speaker Camera",
                    "state": CAMERA_STATE_CONNECTED,
                    "hasSpeaker": True,
                    "speakerVolume": 80,
                },
                "cam_nested": {
                    "id": "cam_nested",
                    "name": "Nested Flag Speaker Camera",
                    "state": CAMERA_STATE_CONNECTED,
                    "hasSpeaker": False,
                    "featureFlags": {"hasSpeaker": True},
                    "speakerVolume": 60,
                },
                "cam_removable": {
                    "id": "cam_removable",
                    "name": "Removable Speaker Camera",
                    "state": CAMERA_STATE_CONNECTED,
                    "featureFlags": {"hasRemovableSpeaker": True},
                    "volume": 40,
                },
                "cam_no_speaker": {
                    "id": "cam_no_speaker",
                    "name": "No Speaker Camera",
                    "state": CAMERA_STATE_CONNECTED,
                    "hasSpeaker": False,
                    "featureFlags": {"hasSpeaker": False, "hasRemovableSpeaker": False},
                },
                "cam_non_bool": {
                    "id": "cam_non_bool",
                    "name": "Non Boolean Flag Camera",
                    "state": CAMERA_STATE_CONNECTED,
                    "hasSpeaker": "truthy_string",
                    "featureFlags": {"hasSpeaker": 1},
                },
                "non_dict_record": "invalid_camera_data",
            },
        )

        entities: list[Entity] = []

        add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_entry, add_entities)

        # Should only create entities for the 3 supported cameras
        assert len(entities) == 3
        camera_ids = {
            require_instance(entity, UnifiProtectCameraSpeaker)._device_id
            for entity in entities
        }
        assert camera_ids == {"cam_top", "cam_nested", "cam_removable"}

    @pytest.mark.asyncio
    async def test_setup_entry_skips_unsupported_and_empty(
        self, hass: Any, mock_entry: MagicMock, mock_coordinator: MagicMock
    ) -> None:
        """Test setup with only unsupported cameras creates no entities."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "cameras",
            {
                "cam_none": {
                    "id": "cam_none",
                    "name": "No Speaker",
                    "state": CAMERA_STATE_CONNECTED,
                },
                "cam_false": {
                    "id": "cam_false",
                    "hasSpeaker": False,
                    "state": CAMERA_STATE_CONNECTED,
                },
            },
        )

        add_entities = MagicMock()
        await async_setup_entry(hass, mock_entry, add_entities)
        add_entities.assert_not_called()

    @pytest.mark.asyncio
    async def test_setup_entry_graceful_missing_protect_data(
        self, hass: Any, mock_entry: MagicMock, mock_coordinator: MagicMock
    ) -> None:
        """Test listener handles missing or non-dict protect data gracefully."""
        add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        # Test various malformed data shapes
        mock_coordinator.data = None
        listener()

        mock_coordinator.data = "not_a_dict"
        listener()

        mock_coordinator.data = {}
        listener()

        mock_coordinator.data = {"protect": "not_a_dict"}
        listener()

        mock_coordinator.data = {"protect": {"cameras": "not_a_dict"}}
        listener()

        add_entities.assert_not_called()

    @pytest.mark.asyncio
    async def test_dynamic_discovery_and_deduplication(
        self, hass: Any, mock_entry: MagicMock, mock_coordinator: MagicMock
    ) -> None:
        """Test dynamic discovery adds new cameras without duplicating existing ones."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "cameras",
            {
                "cam1": {
                    "id": "cam1",
                    "name": "Initial Camera",
                    "state": CAMERA_STATE_CONNECTED,
                    "hasSpeaker": True,
                }
            },
        )

        entities: list[Entity] = []

        add_entities = EntityCollector(entities)

        await async_setup_entry(hass, mock_entry, add_entities)
        assert len(entities) == 1
        assert (
            require_instance(entities[0], UnifiProtectCameraSpeaker)._device_id
            == "cam1"
        )

        # Listener was registered with async_on_unload
        mock_coordinator.async_add_listener.assert_called_once()
        mock_entry.async_on_unload.assert_called_once()
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        # Add a new camera to coordinator data and call listener
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "cameras",
            "cam2",
            {
                "id": "cam2",
                "name": "New Camera",
                "state": CAMERA_STATE_CONNECTED,
                "hasSpeaker": True,
            },
        )
        listener()

        assert len(entities) == 2
        assert {
            require_instance(e, UnifiProtectCameraSpeaker)._device_id for e in entities
        } == {"cam1", "cam2"}

        # Running listener again with same data must not add duplicates
        listener()
        assert len(entities) == 2


class TestUnifiProtectCameraSpeaker:
    """Tests for UnifiProtectCameraSpeaker entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with camera speaker data."""
        coordinator = autospec_command_owners(MagicMock())
        coordinator.protect_client = MagicMock()
        coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "Front Door Camera",
                        "state": CAMERA_STATE_CONNECTED,
                        "hasSpeaker": True,
                        "speakerVolume": 75,
                        "type": "UVC G4 Doorbell Pro",
                        "modelKey": "camera",
                    }
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
            },
        }
        return coordinator

    def test_entity_initialization_and_attributes(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test entity initialization, features, and identity attributes."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")

        assert entity.unique_id == f"{DOMAIN}_camera_cam1_speaker"
        assert entity.translation_key == "speaker"
        assert entity.device_class == MediaPlayerDeviceClass.SPEAKER
        assert entity.state == MediaPlayerState.IDLE
        assert (
            entity.supported_features
            == MediaPlayerEntityFeature.PLAY_MEDIA
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.BROWSE_MEDIA
        )
        assert entity.media_content_type == MediaType.MUSIC
        assert entity.volume_level == 0.75
        assert entity.available is True
        assert entity.device_info is not None

    def test_volume_fallback_and_missing(self, mock_coordinator: MagicMock) -> None:
        """Test volume resolution order: speakerSettings, volume, top-level."""
        # speakerSettings.speakerVolume takes highest priority
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "cameras",
            "cam1",
            {
                "id": "cam1",
                "state": CAMERA_STATE_CONNECTED,
                "hasSpeaker": True,
                "speakerSettings": {"speakerVolume": 85, "volume": 50},
                "speakerVolume": 70,
            },
        )
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        assert entity.volume_level == 0.85

        # Fallback to speakerSettings.volume
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "cameras",
            "cam1",
            {
                "id": "cam1",
                "state": CAMERA_STATE_CONNECTED,
                "hasSpeaker": True,
                "speakerSettings": {"volume": 55},
                "speakerVolume": 70,
            },
        )
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        assert entity.volume_level == 0.55

        # Fallback to top-level speakerVolume
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "cameras",
            "cam1",
            {
                "id": "cam1",
                "state": CAMERA_STATE_CONNECTED,
                "hasSpeaker": True,
                "speakerVolume": 65,
            },
        )
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        assert entity.volume_level == 0.65

        # Out of bounds volume (negative or >100) returns None
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "cameras", "cam1", speakerVolume=105
        )
        assert entity.volume_level is None
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "cameras", "cam1", speakerVolume=-5
        )
        assert entity.volume_level is None

        # Boolean volume (bool is subclass of int in Python) returns None
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "cameras", "cam1", speakerVolume=True
        )
        assert entity.volume_level is None

        # Missing both volumes returns None
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "cameras",
            "cam1",
            {
                "id": "cam1",
                "state": CAMERA_STATE_CONNECTED,
                "hasSpeaker": True,
            },
        )
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        assert entity.volume_level is None

        # Non-dict camera data
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data, "cameras", {"cam1": "invalid"}
        )
        assert entity.volume_level is None

    def test_state_idle_and_playing(self, mock_coordinator: MagicMock) -> None:
        """Test state reporting PLAYING when stream is active, otherwise IDLE."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        assert entity.state == MediaPlayerState.IDLE

        mock_stream = MagicMock()
        mock_stream.running = True
        entity._current_stream = mock_stream
        assert entity.state == MediaPlayerState.PLAYING

        mock_stream.running = False
        assert entity.state == MediaPlayerState.IDLE

    def test_availability_conditions(self, mock_coordinator: MagicMock) -> None:
        """Test entity availability under various conditions."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        assert entity.available is True

        # Camera state not CONNECTED
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "cameras", "cam1", state="DISCONNECTED"
        )
        assert entity.available is False

        # Protect coordinator unavailable
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "cameras", "cam1", state=CAMERA_STATE_CONNECTED
        )
        set_availability(mock_coordinator, protect=False)
        assert entity.available is False
        set_availability(mock_coordinator, protect=True)

        # Speaker capability removed
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "cameras", "cam1", hasSpeaker=False
        )
        assert entity.available is False

        # Missing camera device data
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data, "cameras", {}
        )
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_play_media_success_and_completion(
        self, hass: Any, mock_coordinator: MagicMock
    ) -> None:
        """Test async_play_media initiates talkback stream and tracks completion."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]

        completion_event = asyncio.Event()
        mock_stream = MagicMock()
        mock_stream.running = True
        mock_stream.wait_until_complete = AsyncMock(side_effect=completion_event.wait)
        protect_owner(
            mock_coordinator
        ).async_start_camera_talkback.return_value = mock_stream

        with patch(
            "custom_components.unifi_insights.media_player.async_process_play_media_url",
            return_value="http://processed.local/test.mp3",
        ):
            await entity.async_play_media(
                media_type=MediaType.MUSIC,
                media_id="http://media.local/test.mp3",
            )

        protect_owner(
            mock_coordinator
        ).async_start_camera_talkback.assert_called_once_with(
            "cam1", "http://processed.local/test.mp3"
        )
        assert entity._current_stream is mock_stream
        assert entity.state == MediaPlayerState.PLAYING
        assert entity.async_write_ha_state.call_count == 1

        # Release completion task
        completion_event.set()
        assert entity._completion_task is not None
        await entity._completion_task

        # After completion, stream is cleared and state returns to IDLE
        assert entity._current_stream is None
        assert entity.state == MediaPlayerState.IDLE
        assert entity.async_write_ha_state.call_count == 2

    @pytest.mark.asyncio
    async def test_play_media_source_resolution(
        self, hass: Any, mock_coordinator: MagicMock
    ) -> None:
        """Test async_play_media resolves media-source identifiers."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]

        mock_stream = MagicMock()
        mock_stream.running = True
        mock_stream.wait_until_complete = AsyncMock()
        protect_owner(
            mock_coordinator
        ).async_start_camera_talkback.return_value = mock_stream

        resolved_item = MagicMock(url="/local/audio.mp3", mime_type="audio/mpeg")

        with (
            patch(
                "homeassistant.components.media_source.is_media_source_id",
                return_value=True,
            ),
            patch(
                "homeassistant.components.media_source.async_resolve_media",
                AsyncMock(return_value=resolved_item),
            ),
            patch(
                "custom_components.unifi_insights.media_player.async_process_play_media_url",
                return_value="http://127.0.0.1:8123/local/audio.mp3",
            ),
        ):
            await entity.async_play_media(
                media_type="audio/mp3",
                media_id="media-source://radio_browser/123",
            )

        protect_owner(
            mock_coordinator
        ).async_start_camera_talkback.assert_called_once_with(
            "cam1", "http://127.0.0.1:8123/local/audio.mp3"
        )
        await require_not_none(entity._completion_task)

    @pytest.mark.asyncio
    async def test_play_media_source_non_audio_rejected(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test media_source resolving to non-audio MIME type is rejected."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        resolved_item = MagicMock(url="/local/video.mp4", mime_type="video/mp4")

        with (
            patch(
                "homeassistant.components.media_source.is_media_source_id",
                return_value=True,
            ),
            patch(
                "homeassistant.components.media_source.async_resolve_media",
                AsyncMock(return_value=resolved_item),
            ),
            pytest.raises(ServiceValidationError) as exc_info,
        ):
            await entity.async_play_media(
                media_type=MediaType.MUSIC,
                media_id="media-source://camera/front_door",
            )

        assert exc_info.value.translation_key == "media_not_audio"
        protect_owner(mock_coordinator).async_start_camera_talkback.assert_not_called()

    @pytest.mark.asyncio
    async def test_play_media_non_music_rejected(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test non-music media type is rejected before request."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")

        with pytest.raises(ServiceValidationError) as exc_info:
            await entity.async_play_media(
                media_type=MediaType.VIDEO,
                media_id="http://media.local/video.mp4",
            )

        assert exc_info.value.translation_key == "media_not_audio"
        protect_owner(mock_coordinator).async_start_camera_talkback.assert_not_called()

    @pytest.mark.asyncio
    async def test_play_media_stops_previous_stream(
        self, hass: Any, mock_coordinator: MagicMock
    ) -> None:
        """Test starting a new stream stops an already running stream first."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]

        old_stream = MagicMock()
        old_stream.running = True
        old_stream.stop = AsyncMock()
        entity._current_stream = old_stream

        completion_event = asyncio.Event()
        new_stream = MagicMock()
        new_stream.running = True
        new_stream.wait_until_complete = AsyncMock(side_effect=completion_event.wait)
        protect_owner(
            mock_coordinator
        ).async_start_camera_talkback.return_value = new_stream

        with patch(
            "custom_components.unifi_insights.media_player.async_process_play_media_url",
            return_value="http://media.local/new.mp3",
        ):
            await entity.async_play_media(
                media_type=MediaType.MUSIC,
                media_id="http://media.local/new.mp3",
            )

        old_stream.stop.assert_called_once()
        assert entity._current_stream is new_stream
        completion_event.set()
        await require_not_none(entity._completion_task)

    @pytest.mark.asyncio
    async def test_play_media_start_failure(
        self, hass: Any, mock_coordinator: MagicMock
    ) -> None:
        """Test talkback start failure leaves entity idle and propagates exception."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]

        protect_owner(
            mock_coordinator
        ).async_start_camera_talkback.side_effect = HomeAssistantError("Start failed")

        with (
            patch(
                "custom_components.unifi_insights.media_player.async_process_play_media_url",
                return_value="http://media.local/fail.mp3",
            ),
            pytest.raises(HomeAssistantError, match="Start failed"),
        ):
            await entity.async_play_media(
                media_type=MediaType.MUSIC,
                media_id="http://media.local/fail.mp3",
            )

        assert entity._current_stream is None
        assert entity.state == MediaPlayerState.IDLE
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_play_media_start_failure_after_stopping_previous_stream(
        self, hass: Any, mock_coordinator: MagicMock
    ) -> None:
        """Test state is written when old stream is stopped even if new stream fails."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]

        old_stream = MagicMock()
        old_stream.running = True
        old_stream.stop = AsyncMock()
        entity._current_stream = old_stream

        protect_owner(
            mock_coordinator
        ).async_start_camera_talkback.side_effect = HomeAssistantError("Start failed")

        with (
            patch(
                "custom_components.unifi_insights.media_player.async_process_play_media_url",
                return_value="http://media.local/fail.mp3",
            ),
            pytest.raises(HomeAssistantError, match="Start failed"),
        ):
            await entity.async_play_media(
                media_type=MediaType.MUSIC,
                media_id="http://media.local/fail.mp3",
            )

        old_stream.stop.assert_called_once()
        assert entity._current_stream is None
        assert entity.state == MediaPlayerState.IDLE
        # State was written when old stream was stopped
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_play_media_worker_error_handled(
        self, hass: Any, mock_coordinator: MagicMock
    ) -> None:
        """Test stream worker error in background task is logged."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]

        mock_stream = MagicMock()
        mock_stream.running = True
        mock_stream.wait_until_complete = AsyncMock(
            side_effect=RuntimeError("Stream network error")
        )
        protect_owner(
            mock_coordinator
        ).async_start_camera_talkback.return_value = mock_stream

        with patch(
            "custom_components.unifi_insights.media_player.async_process_play_media_url",
            return_value="http://media.local/error.mp3",
        ):
            await entity.async_play_media(
                media_type=MediaType.MUSIC,
                media_id="http://media.local/error.mp3",
            )

        await require_not_none(entity._completion_task)
        assert entity._current_stream is None
        assert entity.state == MediaPlayerState.IDLE

    @pytest.mark.asyncio
    async def test_media_stop_when_playing(self, mock_coordinator: MagicMock) -> None:
        """Test async_media_stop stops running stream and clears reference."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]

        mock_stream = MagicMock()
        mock_stream.running = True
        mock_stream.stop = AsyncMock()
        entity._current_stream = mock_stream

        await entity.async_media_stop()

        mock_stream.stop.assert_called_once()
        assert entity._current_stream is None
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_media_stop_when_idle(self, mock_coordinator: MagicMock) -> None:
        """Test async_media_stop does nothing when idle."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]

        await entity.async_media_stop()
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_browse_media(
        self, hass: Any, mock_coordinator: MagicMock
    ) -> None:
        """Test async_browse_media applies audio MIME content filter."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        entity.hass = hass

        captured_filter: Any = None

        async def _mock_browse(
            hass: Any, media_content_id: Any, content_filter: Any = None
        ) -> BrowseMedia:
            nonlocal captured_filter
            captured_filter = content_filter
            return MagicMock()

        with patch(
            "homeassistant.components.media_source.async_browse_media",
            side_effect=_mock_browse,
        ):
            await entity.async_browse_media(media_content_id="media-source://")

        assert captured_filter is not None
        # Test content_filter lambda
        audio_item = MagicMock(media_content_type="audio/mpeg")
        video_item = MagicMock(media_content_type="video/mp4")
        none_item = MagicMock(media_content_type=None)

        assert captured_filter(audio_item) is True
        assert captured_filter(video_item) is False
        assert captured_filter(none_item) is False

    @pytest.mark.asyncio
    async def test_async_will_remove_from_hass(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test removal from Home Assistant stops stream before cancelling task."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")

        mock_task = MagicMock()
        mock_task.done.return_value = False
        entity._completion_task = mock_task

        async def _stop_assert_not_cancelled() -> None:
            """Assert that the completion task is not cancelled when stopping."""
            mock_task.cancel.assert_not_called()

        mock_stream = MagicMock()
        mock_stream.running = True
        mock_stream.stop = AsyncMock(side_effect=_stop_assert_not_cancelled)
        entity._current_stream = mock_stream

        await entity.async_will_remove_from_hass()

        mock_stream.stop.assert_called_once()
        mock_task.cancel.assert_called_once()
        assert entity._current_stream is None

    @pytest.mark.asyncio
    async def test_async_will_remove_from_hass_stream_running_false(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test removal awaits stop() even if stream.running is False."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")

        mock_task = MagicMock()
        mock_task.done.return_value = False
        entity._completion_task = mock_task

        async def _stop_assert_not_cancelled() -> None:
            """Assert that the completion task is not cancelled when stopping."""
            mock_task.cancel.assert_not_called()

        mock_stream = MagicMock()
        mock_stream.running = False
        mock_stream.stop = AsyncMock(side_effect=_stop_assert_not_cancelled)
        entity._current_stream = mock_stream

        await entity.async_will_remove_from_hass()

        mock_stream.stop.assert_called_once()
        mock_task.cancel.assert_called_once()
        assert entity._current_stream is None

    @pytest.mark.asyncio
    async def test_async_will_remove_from_hass_mismatched_stream(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test removal does not clear reference if stream changed during stop."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        stream1 = MagicMock()
        stream2 = MagicMock()

        async def _mock_stop() -> None:
            """Simulate stream replacement during stop."""
            entity._current_stream = stream2

        stream1.stop = AsyncMock(side_effect=_mock_stop)
        entity._current_stream = stream1

        await entity.async_will_remove_from_hass()

        stream1.stop.assert_called_once()
        assert entity._current_stream is stream2

    @pytest.mark.asyncio
    async def test_async_will_remove_from_hass_end_to_end_real_stream(
        self, hass: Any, mock_coordinator: MagicMock
    ) -> None:
        """Test entity removal stops a live real TalkbackStream end-to-end."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        entity.hass = hass

        session = TalkbackSession(
            url="rtp://192.168.1.10:7004",
            codec="opus",
            samplingRate=24000,
            bitsPerSample=16,
        )
        stream = TalkbackStream(
            session, "http://processed.local/test.mp3", loop=hass.loop
        )

        transcoder_started = threading.Event()

        def _mock_transcode(
            sess: Any,
            media_url: str,
            stop_event: threading.Event,
        ) -> None:
            """Simulate transcoding worker blocking on the stop event."""
            transcoder_started.set()
            assert stop_event.wait(timeout=5)

        async def _mock_start_camera_talkback(cam_id: str, url: str) -> TalkbackStream:
            """Start and return the mock talkback stream."""
            await stream.start()
            return stream

        protect_owner(
            mock_coordinator
        ).async_start_camera_talkback.side_effect = _mock_start_camera_talkback

        with (
            patch.object(entity, "async_write_ha_state"),
            patch(
                (
                    "custom_components.unifi_insights.media_player"
                    ".async_process_play_media_url"
                ),
                return_value="http://processed.local/test.mp3",
            ),
            patch(
                ("unifi_official_api.protect.talkback._transcode_and_stream"),
                side_effect=_mock_transcode,
            ),
        ):
            try:
                await entity.async_play_media(
                    media_type=MediaType.MUSIC,
                    media_id="http://media.local/test.mp3",
                )
                assert await asyncio.to_thread(transcoder_started.wait, 5)
                assert entity._current_stream is stream
                assert stream.running is True

                await entity.async_will_remove_from_hass()

                assert stream._stop_event.is_set()
                assert stream._thread is not None
                await asyncio.to_thread(stream._thread.join, 5)
                assert not stream._thread.is_alive()
                assert entity._current_stream is None
            finally:
                stream._stop_event.set()
                if stream._thread is not None:
                    stream._thread.join(timeout=5)

    @pytest.mark.asyncio
    async def test_async_will_remove_from_hass_no_task_no_stream(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test removal from Home Assistant when no task or stream is running."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        entity._completion_task = None
        entity._current_stream = None
        await entity.async_will_remove_from_hass()

    def test_availability_exception_handled(self, mock_coordinator: MagicMock) -> None:
        """Test entity availability returns False if exception occurs."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        with patch(
            "custom_components.unifi_insights.media_player.camera_supports_speaker",
            side_effect=RuntimeError("unexpected"),
        ):
            assert entity.available is False

    @pytest.mark.asyncio
    async def test_wait_for_completion_mismatched_stream(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test _async_wait_for_completion preserves replaced stream."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]
        stream1 = MagicMock()
        stream1.wait_until_complete = AsyncMock()
        stream2 = MagicMock()
        entity._current_stream = stream2

        await entity._async_wait_for_completion(stream1)
        assert entity._current_stream is stream2
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_play_media_when_removing(
        self, hass: Any, mock_coordinator: MagicMock
    ) -> None:
        """Test async_play_media rejects playback when entity removal has begun."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        entity.hass = hass
        entity._removing = True

        with patch(
            "custom_components.unifi_insights.media_player.async_process_play_media_url",
            return_value="http://media.local/test.mp3",
        ):
            await entity.async_play_media(
                media_type=MediaType.MUSIC,
                media_id="http://media.local/test.mp3",
            )

        protect_owner(mock_coordinator).async_start_camera_talkback.assert_not_called()
        assert entity._current_stream is None

    @pytest.mark.asyncio
    async def test_media_stop_mismatched_stream(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test async_media_stop does not clear reference if stream changed."""
        entity = UnifiProtectCameraSpeaker(mock_coordinator, "cam1")
        entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]
        stream1 = MagicMock()
        stream1.running = True

        async def _mock_stop() -> None:
            # Simulate a concurrent play setting a new stream during await stop()
            stream2 = MagicMock()
            entity._current_stream = stream2

        stream1.stop = AsyncMock(side_effect=_mock_stop)
        entity._current_stream = stream1

        await entity.async_media_stop()
        assert entity._current_stream is not None
        assert entity._current_stream is not stream1
