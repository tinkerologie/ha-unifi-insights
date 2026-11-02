# Copyright 2026 UniFi Insights contributors
"""Tests for UniFi Protect camera platform."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from homeassistant.components.camera import CameraEntityFeature
from homeassistant.helpers import entity_registry as er

from custom_components.unifi_insights.camera import (
    PARALLEL_UPDATES,
    UnifiProtectCamera,
    async_setup_entry,
)
from custom_components.unifi_insights.const import (
    ATTR_CAMERA_ID,
    ATTR_CAMERA_NAME,
    ATTR_CAMERA_STATE,
    ATTR_CAMERA_TYPE,
    ATTR_IS_PACKAGE_CAMERA,
    ATTR_PARENT_CAMERA_ID,
    CAMERA_STATE_CONNECTED,
    DEVICE_TYPE_CAMERA,
    DOMAIN,
)
from tests.fixtures.assertions import require_not_none
from tests.fixtures.command_owners import autospec_command_owners, protect_owner
from tests.fixtures.facade_data import (
    with_camera,
    with_protect_collection,
    with_protect_fields,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.unifi_insights.coordinators.protect import StreamQuality


class TestParallelUpdates:
    """Test PARALLEL_UPDATES constant."""

    def test_parallel_updates_value(self) -> None:
        """Test that PARALLEL_UPDATES is set correctly for parallel operations."""
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
    async def test_setup_entry_with_cameras(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup with cameras present."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "cameras",
            {
                "camera1": {
                    "id": "camera1",
                    "name": "Test Camera",
                    "state": CAMERA_STATE_CONNECTED,
                }
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        # Should add one camera entity per channel
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 3
        assert all(isinstance(entity, UnifiProtectCamera) for entity in entities)
        assert [entity._quality for entity in entities] == ["high", "medium", "low"]

    @pytest.mark.asyncio
    async def test_setup_entry_with_multiple_cameras(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup with multiple cameras."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "cameras",
            {
                "camera1": {"id": "camera1", "name": "Front", "state": "CONNECTED"},
                "camera2": {"id": "camera2", "name": "Back", "state": "CONNECTED"},
                "camera3": {"id": "camera3", "name": "Side", "state": "DISCONNECTED"},
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 9
        assert {entity._device_id for entity in entities} == {
            "camera1",
            "camera2",
            "camera3",
        }


class TestUnifiProtectCamera:
    """Tests for UnifiProtectCamera entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = autospec_command_owners(MagicMock())
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        protect = protect_owner(coordinator)
        protect.async_get_camera_snapshot.return_value = b"image_data"
        protect.async_create_camera_stream_url.return_value = None
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "Test Camera",
                        "state": CAMERA_STATE_CONNECTED,
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "type": "UVC-G4-Pro",
                        "firmwareVersion": "1.0.0",
                        "_camera_type": "regular",
                        "_is_package_camera": False,
                        "_parent_camera_id": None,
                    }
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    @pytest.mark.parametrize(
        ("quality", "suffix", "translation_key", "enabled_default"),
        [
            pytest.param("high", "", None, True, id="high"),
            pytest.param("medium", "_medium", "medium_resolution", False, id="medium"),
            pytest.param("low", "_low", "low_resolution", False, id="low"),
        ],
    )
    def test_initialization(
        self,
        mock_coordinator: MagicMock,
        quality: StreamQuality,
        suffix: str,
        translation_key: str | None,
        *,
        enabled_default: bool,
    ) -> None:
        """Each channel keeps its own identity, name and registry default."""
        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
            quality=quality,
        )

        assert camera._quality == quality
        assert camera._device_id == "camera1"
        assert camera._device_type == DEVICE_TYPE_CAMERA
        assert camera._attr_has_entity_name is True
        assert camera._attr_unique_id == f"unifi_insights_camera_camera1{suffix}"
        assert camera.translation_key == translation_key
        assert camera.entity_registry_enabled_default is enabled_default
        if quality == "high":
            # The device name is the entity name, as it has always been.
            assert camera._attr_name is None
        else:
            # No name override, so the translation key names the entity.
            assert not hasattr(camera, "_attr_name")
        assert camera._attr_supported_features == CameraEntityFeature.STREAM
        assert camera._attr_entity_category is None

    def test_update_from_data_connected(self, mock_coordinator: MagicMock) -> None:
        """Test _update_from_data with connected camera."""
        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert camera.available is True

    def test_update_from_data_disconnected(self, mock_coordinator: MagicMock) -> None:
        """Test _update_from_data with disconnected camera."""
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "cameras", "camera1", state="DISCONNECTED"
        )

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert camera.available is False

    def test_extra_state_attributes(self, mock_coordinator: MagicMock) -> None:
        """Test extra state attributes."""
        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        attrs = camera._attr_extra_state_attributes
        assert attrs[ATTR_CAMERA_ID] == "camera1"
        assert attrs[ATTR_CAMERA_NAME] == "Test Camera"
        assert attrs[ATTR_CAMERA_STATE] == CAMERA_STATE_CONNECTED
        assert attrs[ATTR_CAMERA_TYPE] == "regular"
        assert attrs[ATTR_IS_PACKAGE_CAMERA] is False
        assert attrs[ATTR_PARENT_CAMERA_ID] is None

    def test_extra_state_attributes_package_camera(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test extra state attributes for package camera."""
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "cameras",
            "camera1",
            _camera_type="doorbell_package",
            _is_package_camera=True,
            _parent_camera_id="doorbell_main",
        )

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        attrs = camera._attr_extra_state_attributes
        assert attrs[ATTR_CAMERA_TYPE] == "doorbell_package"
        assert attrs[ATTR_IS_PACKAGE_CAMERA] is True
        assert attrs[ATTR_PARENT_CAMERA_ID] == "doorbell_main"

    @pytest.mark.asyncio
    async def test_async_camera_image_success(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test getting camera image successfully."""
        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.async_camera_image()

        assert result == b"image_data"
        # The Protect snapshot endpoint rejects width/height query params
        # (HTTP 500), so the call must not include them.
        protect_owner(
            mock_coordinator
        ).async_get_camera_snapshot.assert_awaited_once_with("camera1")

    @pytest.mark.asyncio
    async def test_async_camera_image_with_dimensions(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test getting camera image with specific dimensions."""
        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.async_camera_image(width=640, height=480)

        assert result == b"image_data"
        # Requested dimensions are ignored; HA scales the returned JPEG.
        protect_owner(
            mock_coordinator
        ).async_get_camera_snapshot.assert_awaited_once_with("camera1")

    @pytest.mark.asyncio
    async def test_async_camera_image_unreadable(
        self, mock_coordinator: MagicMock
    ) -> None:
        """A snapshot the owner cannot read is no image."""
        protect_owner(mock_coordinator).async_get_camera_snapshot.return_value = None

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert await camera.async_camera_image() is None

    @pytest.mark.asyncio
    async def test_stream_source_success(self, mock_coordinator: MagicMock) -> None:
        """Test getting stream source successfully using the API.

        The Protect owner returns the dynamic URL it created.
        """
        protect = protect_owner(mock_coordinator)
        protect.async_create_camera_stream_url.return_value = (
            "rtsps://192.168.1.1:7441/camera1_abc123?enableSrtp"
        )

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.stream_source()

        # URL comes from the owner's dynamic stream
        assert result == "rtsps://192.168.1.1:7441/camera1_abc123?enableSrtp"
        protect.async_create_camera_stream_url.assert_awaited_once_with(
            "camera1", "high"
        )

    @pytest.mark.parametrize("quality", ["medium", "low"])
    async def test_stream_source_extra_channel(
        self, mock_coordinator: MagicMock, quality: StreamQuality
    ) -> None:
        """A medium or low entity streams the channel it asked for."""
        protect = protect_owner(mock_coordinator)
        protect.async_create_camera_stream_url.return_value = (
            f"rtsps://192.168.1.1:7441/camera1_{quality}?enableSrtp"
        )

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
            quality=quality,
        )

        assert await camera.stream_source() == (
            f"rtsps://192.168.1.1:7441/camera1_{quality}?enableSrtp"
        )
        protect.async_create_camera_stream_url.assert_awaited_once_with(
            "camera1", quality
        )

    @pytest.mark.parametrize("quality", ["medium", "low"])
    async def test_stream_source_extra_channel_never_falls_back(
        self, mock_coordinator: MagicMock, quality: StreamQuality
    ) -> None:
        """A failed medium or low stream is None, never the high static URL."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data, "nvrs", {"nvr1": {"host": "10.0.0.1"}}
        )
        protect = protect_owner(mock_coordinator)

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
            quality=quality,
        )

        assert await camera.stream_source() is None
        protect.async_create_camera_stream_url.assert_awaited_once_with(
            "camera1", quality
        )

    @pytest.mark.asyncio
    async def test_stream_source_api_failure_fallback(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test fallback to static URL when API fails."""
        # The owner could not create a dynamic stream (its fixture default)

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.stream_source()

        # Falls back to static URL construction
        assert result == "rtsps://192.168.1.1:7441/camera1?enableSrtp"

    @pytest.mark.asyncio
    async def test_stream_source_no_host(self, mock_coordinator: MagicMock) -> None:
        """Test getting stream source when NVR host cannot be determined."""
        # The owner could not create a dynamic stream (its fixture default)
        # Clear NVR data and set base_url to invalid
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data, "nvrs", {}
        )
        mock_coordinator.protect_client.base_url = None

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.stream_source()

        assert result is None

    @pytest.mark.asyncio
    async def test_stream_source_from_nvr_data(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test getting stream source from NVR data when API fails."""
        # Add NVR data with a host
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data, "nvrs", {"nvr1": {"host": "10.0.0.1"}}
        )

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.stream_source()

        # URL should use the NVR host
        assert result == "rtsps://10.0.0.1:7441/camera1?enableSrtp"

    @pytest.mark.asyncio
    async def test_stream_source_protect_client_none_fallback(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Without a Protect client the NVR host still builds the static URL."""
        mock_coordinator.protect_client = None
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data, "nvrs", {"nvr1": {"host": "10.0.0.1"}}
        )

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.stream_source()
        assert result == "rtsps://10.0.0.1:7441/camera1?enableSrtp"

    @pytest.mark.asyncio
    async def test_stream_source_no_dynamic_url_uses_nvr_host(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test fallback to static URL when the owner returns no dynamic URL."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data, "nvrs", {"nvr1": {"host": "10.0.0.1"}}
        )

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.stream_source()
        assert result == "rtsps://10.0.0.1:7441/camera1?enableSrtp"

    @pytest.mark.asyncio
    async def test_stream_source_nvr_data_without_host(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test NVR loop continues when NVR data has no host."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "nvrs",
            {"nvr1": {"name": "No Host"}, "nvr2": {"host": "10.0.0.2"}},
        )

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.stream_source()
        assert result == "rtsps://10.0.0.2:7441/camera1?enableSrtp"

    def test_missing_camera_data(self, mock_coordinator: MagicMock) -> None:
        """Test handling missing camera data."""
        mock_coordinator.data = with_camera(mock_coordinator.data, "camera1", {})

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert camera.available is False
        attrs = camera._attr_extra_state_attributes
        assert attrs[ATTR_CAMERA_TYPE] == "regular"  # Default
        assert attrs[ATTR_IS_PACKAGE_CAMERA] is False  # Default

    def test_connecting_state(self, mock_coordinator: MagicMock) -> None:
        """Test camera in CONNECTING state."""
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "cameras", "camera1", state="CONNECTING"
        )

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert camera.available is False


class TestCameraQualityRegistry:
    """The three channel entities as the real entity registry sees them."""

    @pytest.fixture
    def mock_protect_client(self, mock_protect_client: MagicMock) -> MagicMock:
        """Seed one camera before the integration builds its snapshot."""
        mock_protect_client.cameras.get_all.return_value = [
            {
                "id": "camera1",
                "name": "Front Door",
                "state": "CONNECTED",
                "model": "UVC-G6-Turret",
                "mac": "11:22:33:44:55:66",
                "featureFlags": {},
            }
        ]
        return mock_protect_client

    async def test_extra_channels_are_disabled_and_share_the_device(
        self,
        hass: HomeAssistant,
        init_integration: MockConfigEntry,
        entity_registry: er.EntityRegistry,
    ) -> None:
        """Medium and low rows exist disabled on the high entity's device."""
        unique_ids = {
            "high": "unifi_insights_camera_camera1",
            "medium": "unifi_insights_camera_camera1_medium",
            "low": "unifi_insights_camera_camera1_low",
        }
        entity_ids = {
            quality: require_not_none(
                entity_registry.async_get_entity_id("camera", DOMAIN, unique_id)
            )
            for quality, unique_id in unique_ids.items()
        }
        rows = {
            quality: require_not_none(entity_registry.async_get(entity_id))
            for quality, entity_id in entity_ids.items()
        }

        # The high channel keeps the identity it has always had.
        assert rows["high"].unique_id == unique_ids["high"]
        assert entity_ids["high"] == "camera.security_front_door"
        assert rows["high"].disabled_by is None
        assert rows["high"].original_name is None
        assert hass.states.get(entity_ids["high"]) is not None

        # The extra channels are opt-in and named by their translation.
        for quality, name in (
            ("medium", "Medium resolution"),
            ("low", "Low resolution"),
        ):
            row = rows[quality]
            assert row.unique_id == unique_ids[quality]
            assert row.disabled_by is er.RegistryEntryDisabler.INTEGRATION
            assert row.original_name == name
            assert hass.states.get(entity_ids[quality]) is None

        assert len(set(entity_ids.values())) == 3
        device_ids = {row.device_id for row in rows.values()}
        assert len(device_ids) == 1
        device_id = require_not_none(rows["high"].device_id)

        # Enabling a channel keeps its entity ID and device across a reload.
        entity_registry.async_update_entity(entity_ids["medium"], disabled_by=None)
        await hass.config_entries.async_reload(init_integration.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

        enabled = require_not_none(entity_registry.async_get(entity_ids["medium"]))
        assert enabled.entity_id == entity_ids["medium"]
        assert enabled.device_id == device_id
        assert hass.states.get(entity_ids["medium"]) is not None
