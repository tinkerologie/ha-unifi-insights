# Copyright 2026 UniFi Insights contributors
"""Tests for UniFi Protect camera platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.camera import CameraEntityFeature

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
)


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
    async def test_setup_entry_with_cameras(self, hass, mock_coordinator) -> None:
        """Test setup with cameras present."""
        mock_coordinator.data["protect"]["cameras"] = {
            "camera1": {
                "id": "camera1",
                "name": "Test Camera",
                "state": CAMERA_STATE_CONNECTED,
            }
        }

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        # Should add one camera entity
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], UnifiProtectCamera)

    @pytest.mark.asyncio
    async def test_setup_entry_with_multiple_cameras(
        self, hass, mock_coordinator
    ) -> None:
        """Test setup with multiple cameras."""
        mock_coordinator.data["protect"]["cameras"] = {
            "camera1": {"id": "camera1", "name": "Front", "state": "CONNECTED"},
            "camera2": {"id": "camera2", "name": "Back", "state": "CONNECTED"},
            "camera3": {"id": "camera3", "name": "Side", "state": "DISCONNECTED"},
        }

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 3


class TestUnifiProtectCamera:
    """Tests for UnifiProtectCamera entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.protect_client.cameras = MagicMock()
        coordinator.protect_client.cameras.get_snapshot = AsyncMock(
            return_value=b"image_data"
        )
        coordinator.protect_client.cameras.create_rtsps_stream = AsyncMock()
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

    def test_initialization(self, mock_coordinator) -> None:
        """Test camera entity initialization."""
        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert camera._device_id == "camera1"
        assert camera._device_type == DEVICE_TYPE_CAMERA
        assert camera._attr_has_entity_name is True
        assert camera._attr_name is None
        assert camera._attr_supported_features == CameraEntityFeature.STREAM
        assert camera._attr_entity_category is None

    def test_update_from_data_connected(self, mock_coordinator) -> None:
        """Test _update_from_data with connected camera."""
        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert camera.available is True

    def test_update_from_data_disconnected(self, mock_coordinator) -> None:
        """Test _update_from_data with disconnected camera."""
        mock_coordinator.data["protect"]["cameras"]["camera1"]["state"] = "DISCONNECTED"

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert camera.available is False

    def test_extra_state_attributes(self, mock_coordinator) -> None:
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

    def test_extra_state_attributes_package_camera(self, mock_coordinator) -> None:
        """Test extra state attributes for package camera."""
        mock_coordinator.data["protect"]["cameras"]["camera1"]["_camera_type"] = (
            "doorbell_package"
        )
        mock_coordinator.data["protect"]["cameras"]["camera1"]["_is_package_camera"] = (
            True
        )
        mock_coordinator.data["protect"]["cameras"]["camera1"]["_parent_camera_id"] = (
            "doorbell_main"
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
    async def test_async_camera_image_success(self, mock_coordinator) -> None:
        """Test getting camera image successfully."""
        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.async_camera_image()

        assert result == b"image_data"
        # The Protect snapshot endpoint rejects width/height query params
        # (HTTP 500), so the call must not include them.
        mock_coordinator.protect_client.cameras.get_snapshot.assert_called_once_with(
            "camera1",
        )

    @pytest.mark.asyncio
    async def test_async_camera_image_with_dimensions(self, mock_coordinator) -> None:
        """Test getting camera image with specific dimensions."""
        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.async_camera_image(width=640, height=480)

        assert result == b"image_data"
        # Requested dimensions are ignored; HA scales the returned JPEG.
        mock_coordinator.protect_client.cameras.get_snapshot.assert_called_once_with(
            "camera1",
        )

    @pytest.mark.asyncio
    async def test_async_camera_image_error(self, mock_coordinator) -> None:
        """Test getting camera image with error."""
        mock_coordinator.protect_client.cameras.get_snapshot.side_effect = Exception(
            "API error"
        )

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.async_camera_image()

        assert result is None

    @pytest.mark.asyncio
    async def test_async_camera_image_non_bytes(self, mock_coordinator) -> None:
        """Test getting camera image when response is not bytes."""
        mock_coordinator.protect_client.cameras.get_snapshot.return_value = "not bytes"

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.async_camera_image()

        assert result is None

    @pytest.mark.asyncio
    async def test_stream_source_success(self, mock_coordinator) -> None:
        """Test getting stream source successfully using the API.

        The vendored API package returns a dynamic stream URL from
        create_rtsps_stream.
        """
        # Mock the create_rtsps_stream to return a proper stream object
        mock_stream = MagicMock()
        mock_stream.high = "rtsps://192.168.1.1:7441/camera1_abc123?enableSrtp"
        mock_coordinator.protect_client.cameras.create_rtsps_stream = AsyncMock(
            return_value=mock_stream
        )

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.stream_source()

        # URL comes from the API's create_rtsps_stream response
        assert result == "rtsps://192.168.1.1:7441/camera1_abc123?enableSrtp"
        mock_coordinator.protect_client.cameras.create_rtsps_stream.assert_called_once_with(
            "camera1", qualities=["high"]
        )

    @pytest.mark.asyncio
    async def test_stream_source_api_failure_fallback(self, mock_coordinator) -> None:
        """Test fallback to static URL when API fails."""
        # Mock the API to fail
        mock_coordinator.protect_client.cameras.create_rtsps_stream = AsyncMock(
            side_effect=Exception("API error")
        )

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.stream_source()

        # Falls back to static URL construction
        assert result == "rtsps://192.168.1.1:7441/camera1?enableSrtp"

    @pytest.mark.asyncio
    async def test_stream_source_no_host(self, mock_coordinator) -> None:
        """Test getting stream source when NVR host cannot be determined."""
        # Mock the API to fail
        mock_coordinator.protect_client.cameras.create_rtsps_stream = AsyncMock(
            side_effect=Exception("API error")
        )
        # Clear NVR data and set base_url to invalid
        mock_coordinator.data["protect"]["nvrs"] = {}
        mock_coordinator.protect_client.base_url = None

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.stream_source()

        assert result is None

    @pytest.mark.asyncio
    async def test_stream_source_from_nvr_data(self, mock_coordinator) -> None:
        """Test getting stream source from NVR data when API fails."""
        # Mock the API to fail so it falls back to static URL
        mock_coordinator.protect_client.cameras.create_rtsps_stream = AsyncMock(
            side_effect=Exception("API error")
        )
        # Add NVR data with a host
        mock_coordinator.data["protect"]["nvrs"] = {
            "nvr1": {"host": "10.0.0.1"},
        }

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.stream_source()

        # URL should use the NVR host
        assert result == "rtsps://10.0.0.1:7441/camera1?enableSrtp"

    @pytest.mark.asyncio
    async def test_stream_source_protect_client_none_fallback(
        self, mock_coordinator
    ) -> None:
        """Test fallback to static URL when protect_client is None."""
        mock_coordinator.protect_client = None
        mock_coordinator.data["protect"]["nvrs"] = {"nvr1": {"host": "10.0.0.1"}}

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.stream_source()
        assert result == "rtsps://10.0.0.1:7441/camera1?enableSrtp"

    @pytest.mark.asyncio
    async def test_stream_source_create_stream_missing_high_quality(
        self, mock_coordinator
    ) -> None:
        """Test fallback to static URL when stream high URL is missing."""
        mock_stream = MagicMock()
        mock_stream.high = None
        mock_coordinator.protect_client.cameras.create_rtsps_stream = AsyncMock(
            return_value=mock_stream
        )
        mock_coordinator.data["protect"]["nvrs"] = {"nvr1": {"host": "10.0.0.1"}}

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.stream_source()
        assert result == "rtsps://10.0.0.1:7441/camera1?enableSrtp"

    @pytest.mark.asyncio
    async def test_stream_source_nvr_data_without_host(self, mock_coordinator) -> None:
        """Test NVR loop continues when NVR data has no host."""
        mock_coordinator.protect_client.cameras.create_rtsps_stream = AsyncMock(
            side_effect=Exception("API error")
        )
        mock_coordinator.data["protect"]["nvrs"] = {
            "nvr1": {"name": "No Host"},
            "nvr2": {"host": "10.0.0.2"},
        }

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        result = await camera.stream_source()
        assert result == "rtsps://10.0.0.2:7441/camera1?enableSrtp"

    def test_missing_camera_data(self, mock_coordinator) -> None:
        """Test handling missing camera data."""
        mock_coordinator.data["protect"]["cameras"]["camera1"] = {}

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert camera.available is False
        attrs = camera._attr_extra_state_attributes
        assert attrs[ATTR_CAMERA_TYPE] == "regular"  # Default
        assert attrs[ATTR_IS_PACKAGE_CAMERA] is False  # Default

    def test_connecting_state(self, mock_coordinator) -> None:
        """Test camera in CONNECTING state."""
        mock_coordinator.data["protect"]["cameras"]["camera1"]["state"] = "CONNECTING"

        camera = UnifiProtectCamera(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert camera.available is False
