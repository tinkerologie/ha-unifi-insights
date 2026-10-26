# Copyright 2026 UniFi Insights contributors
"""Tests for UniFi Insights event platform."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.event import EventDeviceClass
from homeassistant.const import CONF_API_KEY, EVENT_STATE_CHANGED, EntityCategory
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
    async_capture_events,
)

from custom_components.unifi_insights import UnifiInsightsData
from custom_components.unifi_insights import event as event_platform
from custom_components.unifi_insights.const import (
    CONF_ACCESS_TOKEN,
    CONF_CONSOLE_ID,
    DOMAIN,
)
from custom_components.unifi_insights.coordinators.access import (
    access_doorbell_signal,
    access_result_signal,
    async_dispatch_access_result,
)
from custom_components.unifi_insights.event import (
    EVENT_ATTRIBUTE_KEYS,
    EVENT_TYPE_DOORBELL_RING,
    EVENT_TYPE_MOTION,
    EVENT_TYPE_SENSOR_CLOSE,
    EVENT_TYPE_SENSOR_OPEN,
    EVENT_TYPE_SMART_DETECT_ANIMAL,
    EVENT_TYPE_SMART_DETECT_PACKAGE,
    EVENT_TYPE_SMART_DETECT_PERSON,
    EVENT_TYPE_SMART_DETECT_VEHICLE,
    PARALLEL_UPDATES,
    UnifiInsightsNetworkRuleChangeEvent,
    UnifiProtectDoorbellEventEntity,
    UnifiProtectSensorEventEntity,
    UnifiProtectSmartDetectEventEntity,
    _is_doorbell_camera,
    async_setup_entry,
    discover_network_rule_change_events,
)
from tests.fixtures.access import DEVICES, DOORS, ENTRY_DATA, _client, _ready
from tests.fixtures.assertions import patch_attribute, require_mock, require_not_none
from tests.fixtures.entities import EntityCollector
from tests.fixtures.facade_data import (
    set_availability,
    with_protect_collection,
    with_protect_device,
    with_protect_fields,
    with_sections,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.unifi_insights.coordinators.facade import FacadeData


def _without_protect_device(
    data: Mapping[str, Any], collection: str, device_id: str
) -> FacadeData:
    """Return ``data`` without a Protect device of ``collection``."""
    records = {
        key: value
        for key, value in data["protect"][collection].items()
        if key != device_id
    }
    return with_protect_collection(data, collection, records)


class TestParallelUpdates:
    """Test PARALLEL_UPDATES constant."""

    def test_parallel_updates_value(self) -> None:
        """Test that PARALLEL_UPDATES is set correctly."""
        assert PARALLEL_UPDATES == 0


class TestEventTypes:
    """Test event type constants."""

    def test_doorbell_ring_event_type(self) -> None:
        """Test doorbell ring event type."""
        assert EVENT_TYPE_DOORBELL_RING == "ring"

    def test_motion_event_type(self) -> None:
        """Test motion event type."""
        assert EVENT_TYPE_MOTION == "motion"

    def test_smart_detect_event_types(self) -> None:
        """Test smart detect event types."""
        assert EVENT_TYPE_SMART_DETECT_PERSON == "person_detected"
        assert EVENT_TYPE_SMART_DETECT_VEHICLE == "vehicle_detected"
        assert EVENT_TYPE_SMART_DETECT_ANIMAL == "animal_detected"
        assert EVENT_TYPE_SMART_DETECT_PACKAGE == "package_detected"

    def test_sensor_event_types(self) -> None:
        """Test sensor event types."""
        assert EVENT_TYPE_SENSOR_OPEN == "opened"
        assert EVENT_TYPE_SENSOR_CLOSE == "closed"


class TestIsDoorbellCamera:
    """Tests for _is_doorbell_camera helper function."""

    def test_doorbell_in_camera_type(self) -> None:
        """Test detection via _camera_type field."""
        camera_data = {"_camera_type": "doorbell"}
        assert _is_doorbell_camera(camera_data) is True

    def test_doorbell_in_api_type(self) -> None:
        """Test detection via type field."""
        camera_data = {"type": "G4-Doorbell"}
        assert _is_doorbell_camera(camera_data) is True

    def test_doorbell_in_name(self) -> None:
        """Test detection via name field."""
        camera_data = {"name": "Front Doorbell"}
        assert _is_doorbell_camera(camera_data) is True

    def test_not_doorbell(self) -> None:
        """Test non-doorbell camera."""
        camera_data = {"name": "Backyard Camera", "type": "G4-Pro"}
        assert _is_doorbell_camera(camera_data) is False

    def test_empty_data(self) -> None:
        """Test with empty camera data."""
        camera_data = {}
        assert _is_doorbell_camera(camera_data) is False


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
            "clients": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
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
    async def test_setup_entry_with_doorbell_camera(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup with a doorbell camera."""
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "cameras",
            "camera1",
            {
                "id": "camera1",
                "name": "Front Doorbell",
                "state": "CONNECTED",
                "type": "G4-Doorbell",
                "smartDetectTypes": ["person"],
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        # Should have doorbell event + smart detect event
        assert len(entities) == 2
        entity_types = [type(e).__name__ for e in entities]
        assert "UnifiProtectDoorbellEventEntity" in entity_types
        assert "UnifiProtectSmartDetectEventEntity" in entity_types

    @pytest.mark.asyncio
    async def test_setup_entry_with_regular_camera(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup with a regular camera."""
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "cameras",
            "camera1",
            {
                "id": "camera1",
                "name": "Backyard Camera",
                "state": "CONNECTED",
                "type": "G4-Pro",
                "smartDetectTypes": ["person", "vehicle"],
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        # Regular camera only has smart detect event (no doorbell)
        assert len(entities) == 1
        assert isinstance(entities[0], UnifiProtectSmartDetectEventEntity)

    @pytest.mark.asyncio
    async def test_setup_entry_with_sensors(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup with sensors."""
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "sensors",
            "sensor1",
            {
                "id": "sensor1",
                "name": "Front Door Sensor",
                "state": "CONNECTED",
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], UnifiProtectSensorEventEntity)

    @pytest.mark.asyncio
    async def test_setup_entry_rediscovery_dedupes_doorbell_and_smart_detect(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Re-running discovery on the same doorbell camera adds no duplicates."""
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "cameras",
            "camera1",
            {
                "id": "camera1",
                "name": "Front Doorbell",
                "state": "CONNECTED",
                "type": "G4-Doorbell",
                "smartDetectTypes": ["person"],
            },
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        assert async_add_entities.call_count == 1
        assert len(async_add_entities.call_args[0][0]) == 2

        # Re-running discovery with the same camera data must not add anything.
        listener()

        assert async_add_entities.call_count == 1


class TestUnifiProtectDoorbellEventEntity:
    """Tests for UnifiProtectDoorbellEventEntity."""

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
            "clients": {},
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "Front Doorbell",
                        "state": "CONNECTED",
                        "type": "G4-Doorbell",
                    }
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }
        return coordinator

    def test_initialization(self, mock_coordinator) -> None:
        """Test entity initialization."""
        entity = UnifiProtectDoorbellEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        assert entity._device_id == "camera1"
        assert entity._attr_device_class == EventDeviceClass.DOORBELL

    def test_unique_id(self, mock_coordinator) -> None:
        """Test unique ID is set correctly."""
        entity = UnifiProtectDoorbellEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        assert "camera1" in require_not_none(entity._attr_unique_id)
        assert "doorbell" in require_not_none(entity._attr_unique_id)

    def test_event_types(self, mock_coordinator) -> None:
        """Test event types are set correctly."""
        entity = UnifiProtectDoorbellEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        assert EVENT_TYPE_DOORBELL_RING in entity._attr_event_types

    def test_available_when_connected(self, mock_coordinator) -> None:
        """Test availability when camera is connected."""
        entity = UnifiProtectDoorbellEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        assert entity.available is True

    def test_unavailable_when_disconnected(self, mock_coordinator: MagicMock) -> None:
        """Test availability when camera is disconnected."""
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "cameras",
            "camera1",
            state="DISCONNECTED",
        )

        entity = UnifiProtectDoorbellEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        assert entity.available is False

    def test_unavailable_when_protect_coordinator_fails(self, mock_coordinator) -> None:
        """Test entity unavailable when Protect coordinator fails."""
        set_availability(mock_coordinator, protect=False)
        entity = UnifiProtectDoorbellEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )
        assert entity.available is False

    def test_handle_update_skips_when_protect_coordinator_fails(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test update handler skips triggering event when Protect fails."""
        set_availability(mock_coordinator, protect=False)
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "cameras",
            "camera1",
            lastRingStart=12345,
        )
        entity = UnifiProtectDoorbellEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )
        # Test branch where hass is None
        with patch.object(entity, "_trigger_event") as mock_trigger:
            entity._handle_coordinator_update()
            mock_trigger.assert_not_called()

        # Test branch where hass is set
        entity.hass = hass
        with (
            patch.object(entity, "_trigger_event") as mock_trigger,
            patch.object(entity, "async_write_ha_state") as mock_write_state,
        ):
            entity._handle_coordinator_update()
            mock_trigger.assert_not_called()
            mock_write_state.assert_called_once()

    def test_device_info(self, mock_coordinator) -> None:
        """Test device info is set correctly."""
        entity = UnifiProtectDoorbellEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        device_info = entity._attr_device_info
        assert device_info is not None
        assert "manufacturer" in device_info
        assert device_info["manufacturer"] == "Ubiquiti Inc."


class TestUnifiProtectSmartDetectEventEntity:
    """Tests for UnifiProtectSmartDetectEventEntity."""

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
            "clients": {},
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "Backyard Camera",
                        "state": "CONNECTED",
                        "type": "G4-Pro",
                        "smartDetectTypes": ["person", "vehicle", "animal"],
                    }
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }
        return coordinator

    def test_initialization(self, mock_coordinator) -> None:
        """Test entity initialization."""
        entity = UnifiProtectSmartDetectEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        assert entity._device_id == "camera1"
        assert entity._attr_device_class == EventDeviceClass.MOTION

    def test_unique_id(self, mock_coordinator) -> None:
        """Test unique ID is set correctly."""
        entity = UnifiProtectSmartDetectEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        assert "camera1" in require_not_none(entity._attr_unique_id)
        assert "smart_detect" in require_not_none(entity._attr_unique_id)

    def test_event_types(self, mock_coordinator) -> None:
        """Test event types include all smart detect types."""
        entity = UnifiProtectSmartDetectEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        assert EVENT_TYPE_MOTION in entity._attr_event_types
        assert EVENT_TYPE_SMART_DETECT_PERSON in entity._attr_event_types
        assert EVENT_TYPE_SMART_DETECT_VEHICLE in entity._attr_event_types
        assert EVENT_TYPE_SMART_DETECT_ANIMAL in entity._attr_event_types
        assert EVENT_TYPE_SMART_DETECT_PACKAGE in entity._attr_event_types

    def test_available_when_connected(self, mock_coordinator) -> None:
        """Test availability when camera is connected."""
        entity = UnifiProtectSmartDetectEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        assert entity.available is True

    def test_unavailable_when_disconnected(self, mock_coordinator: MagicMock) -> None:
        """Test availability when camera is disconnected."""
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "cameras",
            "camera1",
            state="DISCONNECTED",
        )

        entity = UnifiProtectSmartDetectEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        assert entity.available is False

    def test_unavailable_when_protect_coordinator_fails(self, mock_coordinator) -> None:
        """Test entity unavailable when Protect coordinator fails."""
        set_availability(mock_coordinator, protect=False)
        entity = UnifiProtectSmartDetectEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )
        assert entity.available is False

    def test_handle_update_skips_when_protect_coordinator_fails(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test update handler skips triggering event when Protect fails."""
        set_availability(mock_coordinator, protect=False)
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "cameras",
            "camera1",
            lastMotionStart=12345,
        )
        entity = UnifiProtectSmartDetectEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )
        # Test branch where hass is None
        with patch.object(entity, "_trigger_event") as mock_trigger:
            entity._handle_coordinator_update()
            mock_trigger.assert_not_called()

        # Test branch where hass is set
        entity.hass = hass
        with (
            patch.object(entity, "_trigger_event") as mock_trigger,
            patch.object(entity, "async_write_ha_state") as mock_write_state,
        ):
            entity._handle_coordinator_update()
            mock_trigger.assert_not_called()
            mock_write_state.assert_called_once()

    def test_device_info(self, mock_coordinator) -> None:
        """Test device info is set correctly."""
        entity = UnifiProtectSmartDetectEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        device_info = entity._attr_device_info
        assert device_info is not None
        assert "manufacturer" in device_info
        assert device_info["manufacturer"] == "Ubiquiti Inc."


class TestUnifiProtectSensorEventEntity:
    """Tests for UnifiProtectSensorEventEntity."""

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
            "clients": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {
                    "sensor1": {
                        "id": "sensor1",
                        "name": "Front Door Sensor",
                        "state": "CONNECTED",
                        "type": "UP-Sense",
                        "isOpened": False,
                    }
                },
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }
        return coordinator

    def test_initialization(self, mock_coordinator) -> None:
        """Test entity initialization."""
        entity = UnifiProtectSensorEventEntity(
            coordinator=mock_coordinator,
            device_id="sensor1",
        )

        assert entity._device_id == "sensor1"

    def test_unique_id(self, mock_coordinator) -> None:
        """Test unique ID is set correctly."""
        entity = UnifiProtectSensorEventEntity(
            coordinator=mock_coordinator,
            device_id="sensor1",
        )

        assert "sensor1" in require_not_none(entity._attr_unique_id)
        assert "event" in require_not_none(entity._attr_unique_id)

    def test_event_types(self, mock_coordinator) -> None:
        """Test event types include open and close."""
        entity = UnifiProtectSensorEventEntity(
            coordinator=mock_coordinator,
            device_id="sensor1",
        )

        assert EVENT_TYPE_SENSOR_OPEN in entity._attr_event_types
        assert EVENT_TYPE_SENSOR_CLOSE in entity._attr_event_types

    def test_available_when_connected(self, mock_coordinator) -> None:
        """Test availability when sensor is connected."""
        entity = UnifiProtectSensorEventEntity(
            coordinator=mock_coordinator,
            device_id="sensor1",
        )

        assert entity.available is True

    def test_unavailable_when_disconnected(self, mock_coordinator: MagicMock) -> None:
        """Test availability when sensor is disconnected."""
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "sensors",
            "sensor1",
            state="DISCONNECTED",
        )

        entity = UnifiProtectSensorEventEntity(
            coordinator=mock_coordinator,
            device_id="sensor1",
        )

        assert entity.available is False

    def test_unavailable_when_protect_coordinator_fails(self, mock_coordinator) -> None:
        """Test entity unavailable when Protect coordinator fails."""
        set_availability(mock_coordinator, protect=False)
        entity = UnifiProtectSensorEventEntity(
            coordinator=mock_coordinator,
            device_id="sensor1",
        )
        assert entity.available is False

    def test_handle_update_skips_when_protect_coordinator_fails(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test update handler skips triggering event when Protect fails."""
        set_availability(mock_coordinator, protect=False)
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "sensors",
            "sensor1",
            openStatusChangedAt=12345,
        )
        entity = UnifiProtectSensorEventEntity(
            coordinator=mock_coordinator,
            device_id="sensor1",
        )
        # Test branch where hass is None
        with patch.object(entity, "_trigger_event") as mock_trigger:
            entity._handle_coordinator_update()
            mock_trigger.assert_not_called()

        # Test branch where hass is set
        entity.hass = hass
        with (
            patch.object(entity, "_trigger_event") as mock_trigger,
            patch.object(entity, "async_write_ha_state") as mock_write_state,
        ):
            entity._handle_coordinator_update()
            mock_trigger.assert_not_called()
            mock_write_state.assert_called_once()

    def test_device_info(self, mock_coordinator) -> None:
        """Test device info is set correctly."""
        entity = UnifiProtectSensorEventEntity(
            coordinator=mock_coordinator,
            device_id="sensor1",
        )

        device_info = entity._attr_device_info
        assert device_info is not None
        assert "manufacturer" in device_info
        assert device_info["manufacturer"] == "Ubiquiti Inc."


class TestDoorbellCoordinatorUpdate:
    """Tests for doorbell _handle_coordinator_update method."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator for doorbell tests."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "Doorbell",
                        "state": "CONNECTED",
                        "type": "doorbell",
                        "lastRingStart": None,
                        "lastRingEnd": None,
                    }
                },
                "sensors": {},
            }
        }
        return coordinator

    def test_handle_ring_event(self, mock_coordinator: MagicMock) -> None:
        """Test handling of ring event triggers event."""
        entity = UnifiProtectDoorbellEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        # Simulate ring event
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "cameras",
            "camera1",
            lastRingStart=1234,
            lastRingEnd=None,
        )

        # Patch async_write_ha_state to avoid HA state machine issues
        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        assert entity._last_ring_start == 1234

    def test_no_ring_when_ring_ended(self, mock_coordinator: MagicMock) -> None:
        """Test no event when ring has ended."""
        entity = UnifiProtectDoorbellEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        # Ring has ended
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "cameras",
            "camera1",
            lastRingStart=1234,
            lastRingEnd=1235,
        )

        # Patch async_write_ha_state to avoid HA state machine issues
        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        # _last_ring_start should not be updated since ring_end is not None
        assert entity._last_ring_start is None


class TestSmartDetectCoordinatorUpdate:
    """Tests for smart detect _handle_coordinator_update method."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator for smart detect tests."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "Front Camera",
                        "state": "CONNECTED",
                        "type": "camera",
                        "lastMotionStart": None,
                        "lastMotionEnd": None,
                        "lastSmartDetectTypes": [],
                    }
                },
                "sensors": {},
            }
        }
        return coordinator

    def test_handle_motion_event(self, mock_coordinator: MagicMock) -> None:
        """Test handling of motion event."""
        entity = UnifiProtectSmartDetectEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        # Simulate motion event
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "cameras",
            "camera1",
            lastMotionStart=5678,
            lastMotionEnd=None,
            lastSmartDetectTypes=[],
        )

        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        assert entity._last_motion_start == 5678

    def test_handle_person_detection(self, mock_coordinator: MagicMock) -> None:
        """Test handling of person detection event."""
        entity = UnifiProtectSmartDetectEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        # Simulate person detection
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "cameras",
            "camera1",
            lastMotionStart=9999,
            lastMotionEnd=None,
            lastSmartDetectTypes=["person"],
        )

        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        assert entity._last_motion_start == 9999
        assert "person" in entity._last_smart_detect_types

    def test_no_event_when_motion_ended(self, mock_coordinator: MagicMock) -> None:
        """Test no event when motion has ended."""
        entity = UnifiProtectSmartDetectEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        # Motion has ended
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "cameras",
            "camera1",
            lastMotionStart=1111,
            lastMotionEnd=1112,
        )

        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        # _last_motion_start should not be updated since motion_end is not None
        assert entity._last_motion_start is None


class TestSensorCoordinatorUpdate:
    """Tests for sensor _handle_coordinator_update method."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator for sensor tests."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "protect": {
                "cameras": {},
                "sensors": {
                    "sensor1": {
                        "id": "sensor1",
                        "name": "Door Sensor",
                        "state": "CONNECTED",
                        "isOpened": False,
                        "openStatusChangedAt": None,
                    }
                },
            }
        }
        return coordinator

    def test_handle_sensor_opened(self, mock_coordinator: MagicMock) -> None:
        """Test handling of sensor opened event."""
        entity = UnifiProtectSensorEventEntity(
            coordinator=mock_coordinator,
            device_id="sensor1",
        )

        # Simulate sensor opened
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "sensors",
            "sensor1",
            isOpened=True,
            openStatusChangedAt=2222,
        )

        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        assert entity._last_open_status_changed_at == 2222
        assert entity._last_is_opened is True

    def test_handle_sensor_closed(self, mock_coordinator: MagicMock) -> None:
        """Test handling of sensor closed event."""
        # Start with sensor opened
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "sensors",
            "sensor1",
            isOpened=True,
            openStatusChangedAt=1111,
        )

        entity = UnifiProtectSensorEventEntity(
            coordinator=mock_coordinator,
            device_id="sensor1",
        )

        # Record initial state
        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        assert entity._last_is_opened is True

        # Now close sensor
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "sensors",
            "sensor1",
            isOpened=False,
            openStatusChangedAt=3333,
        )

        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        assert entity._last_open_status_changed_at == 3333
        assert entity._last_is_opened is False


class TestEventSetupEmptyCameras:
    """Tests for setup when cameras dict is empty."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with empty cameras."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {},
            "devices": {},
            "clients": {},
            "protect": {
                "cameras": {},  # Empty cameras dict
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }
        return coordinator

    @pytest.mark.asyncio
    async def test_setup_entry_empty_cameras(self, hass, mock_coordinator) -> None:
        """Test setup with empty cameras dict (no iterations)."""
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        # Should add empty list since no cameras and no sensors
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 0


class TestDoorbellAvailableNoCameraData:
    """Tests for doorbell entity available when camera data is missing."""

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
            "clients": {},
            "protect": {
                "cameras": {},  # Will be missing the camera
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }
        return coordinator

    def test_available_returns_false_when_camera_missing(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test available returns False when camera data is missing."""
        # First create entity with camera data present
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "cameras",
            "camera1",
            {
                "id": "camera1",
                "name": "Front Doorbell",
                "state": "CONNECTED",
                "type": "G4-Doorbell",
            },
        )

        entity = UnifiProtectDoorbellEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        # Now remove the camera from data
        mock_coordinator.data = _without_protect_device(
            mock_coordinator.data, "cameras", "camera1"
        )

        # available should return False
        assert entity.available is False


class TestSmartDetectAvailableNoCameraData:
    """Tests for smart detect entity available when camera data is missing."""

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
            "clients": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }
        return coordinator

    def test_available_returns_false_when_camera_missing(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test available returns False when camera data is missing."""
        # First create entity with camera data present
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "cameras",
            "camera1",
            {
                "id": "camera1",
                "name": "Backyard Camera",
                "state": "CONNECTED",
                "type": "G4-Pro",
                "smartDetectTypes": ["person", "vehicle"],
            },
        )

        entity = UnifiProtectSmartDetectEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        # Now remove the camera from data
        mock_coordinator.data = _without_protect_device(
            mock_coordinator.data, "cameras", "camera1"
        )

        # available should return False
        assert entity.available is False


class TestSensorAvailableNoSensorData:
    """Tests for sensor entity available when sensor data is missing."""

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
            "clients": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }
        return coordinator

    def test_available_returns_false_when_sensor_missing(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test available returns False when sensor data is missing."""
        # First create entity with sensor data present
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "sensors",
            "sensor1",
            {
                "id": "sensor1",
                "name": "Front Door Sensor",
                "state": "CONNECTED",
            },
        )

        entity = UnifiProtectSensorEventEntity(
            coordinator=mock_coordinator,
            device_id="sensor1",
        )

        # Now remove the sensor from data
        mock_coordinator.data = _without_protect_device(
            mock_coordinator.data, "sensors", "sensor1"
        )

        # available should return False
        assert entity.available is False


class TestSmartDetectVehicleAnimalPackage:
    """Tests for vehicle, animal, and package detection events."""

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
            "clients": {},
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "Driveway Camera",
                        "state": "CONNECTED",
                        "type": "G4-Pro",
                        "smartDetectTypes": ["person", "vehicle", "animal", "package"],
                        "lastMotionStart": None,
                        "lastMotionEnd": None,
                        "lastSmartDetectTypes": [],
                    }
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }
        return coordinator

    def test_handle_vehicle_detection(self, mock_coordinator: MagicMock) -> None:
        """Test handling of vehicle detection event."""
        entity = UnifiProtectSmartDetectEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        # Trigger vehicle detection
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "cameras",
            "camera1",
            lastMotionStart=1234,
            lastMotionEnd=None,
            lastSmartDetectTypes=["vehicle"],
        )

        with (
            patch.object(entity, "async_write_ha_state"),
            patch.object(entity, "_trigger_event") as mock_trigger,
        ):
            entity._handle_coordinator_update()
            mock_trigger.assert_called_once_with(
                EVENT_TYPE_SMART_DETECT_VEHICLE,
                {"camera_id": "camera1"},
            )

    def test_handle_animal_detection(self, mock_coordinator: MagicMock) -> None:
        """Test handling of animal detection event."""
        entity = UnifiProtectSmartDetectEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        # Trigger animal detection
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "cameras",
            "camera1",
            lastMotionStart=2345,
            lastMotionEnd=None,
            lastSmartDetectTypes=["animal"],
        )

        with (
            patch.object(entity, "async_write_ha_state"),
            patch.object(entity, "_trigger_event") as mock_trigger,
        ):
            entity._handle_coordinator_update()
            mock_trigger.assert_called_once_with(
                EVENT_TYPE_SMART_DETECT_ANIMAL,
                {"camera_id": "camera1"},
            )

    def test_handle_package_detection(self, mock_coordinator: MagicMock) -> None:
        """Test handling of package detection event."""
        entity = UnifiProtectSmartDetectEventEntity(
            coordinator=mock_coordinator,
            device_id="camera1",
        )

        # Trigger package detection
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data,
            "cameras",
            "camera1",
            lastMotionStart=3456,
            lastMotionEnd=None,
            lastSmartDetectTypes=["package"],
        )

        with (
            patch.object(entity, "async_write_ha_state"),
            patch.object(entity, "_trigger_event") as mock_trigger,
        ):
            entity._handle_coordinator_update()
            mock_trigger.assert_called_once_with(
                EVENT_TYPE_SMART_DETECT_PACKAGE,
                {"camera_id": "camera1"},
            )


class TestSensorUpdateNoStatusChange:
    """Tests for sensor update when status hasn't changed."""

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
            "clients": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {
                    "sensor1": {
                        "id": "sensor1",
                        "name": "Front Door Sensor",
                        "state": "CONNECTED",
                        "isOpened": False,
                        "openStatusChangedAt": None,  # No status change timestamp
                    }
                },
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }
        return coordinator

    def test_no_event_when_status_unchanged_at_is_none(self, mock_coordinator) -> None:
        """Test no event fired when openStatusChangedAt is None."""
        entity = UnifiProtectSensorEventEntity(
            coordinator=mock_coordinator,
            device_id="sensor1",
        )

        with (
            patch.object(entity, "async_write_ha_state"),
            patch.object(entity, "_trigger_event") as mock_trigger,
        ):
            entity._handle_coordinator_update()
            # Should not trigger any event when openStatusChangedAt is None
            mock_trigger.assert_not_called()


# --- Access events -----------------------------------------------------------

# Verify Access doorbell rings and verified access-result events.


def _collector(added: list[Any]) -> AddEntitiesCallback:
    """Return a platform add callback that appends entities to ``added``."""

    return EntityCollector(added)


def _entry(hass: Any, data: dict[str, Any] | None = None) -> MockConfigEntry:
    """Return an entry with the runtime data the event platform reads."""
    entry = MockConfigEntry(domain=DOMAIN, data=data or dict(ENTRY_DATA))
    entry.add_to_hass(hass)
    entry.runtime_data = UnifiInsightsData(
        config_coordinator=MagicMock(name="config"),
        device_coordinator=MagicMock(name="device"),
        protect_coordinator=None,
        network_client=MagicMock(name="network"),
        protect_client=None,
    )
    return entry


async def _wired(hass: Any, *, devices: list[Any] | None = None) -> tuple[Any, ...]:
    """Attach an Access coordinator and run the event setup, collecting entities."""
    entry = _entry(hass)
    coordinator = await _ready(
        hass,
        _client(doors=DOORS, devices=devices if devices is not None else DEVICES),
        entry,
    )
    entry.runtime_data.access_coordinator = coordinator
    added: list[Any] = []
    event_platform.async_setup_access_events(hass, entry, _collector(added))
    return entry, coordinator, added


async def _added(entity: Any) -> None:
    """Add an entity the way Home Assistant does, without writing to a real platform."""
    with patch.object(entity, "async_write_ha_state"):
        await entity.async_added_to_hass()


def _doorbells(added: list[Any]) -> dict[str, Any]:
    return {
        e._device_id: e
        for e in added
        if isinstance(e, event_platform.UnifiAccessDoorbellEvent)
    }


def _results(added: list[Any]) -> dict[str, Any]:
    return {
        e._door_id: e
        for e in added
        if isinstance(e, event_platform.UnifiAccessResultEvent)
    }


async def test_setup_is_a_no_op_without_namespace(hass: Any) -> None:
    """Without a valid console namespace no event entities and no listeners exist."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_API_KEY: "key", CONF_ACCESS_TOKEN: "token"}
    )
    entry.add_to_hass(hass)
    entry.runtime_data = UnifiInsightsData(
        config_coordinator=MagicMock(),
        device_coordinator=MagicMock(),
        protect_coordinator=None,
        network_client=MagicMock(),
        protect_client=None,
    )
    entry.runtime_data.access_coordinator = await _ready(
        hass, _client(doors=DOORS, devices=DEVICES), entry
    )
    added: list[Any] = []

    event_platform.async_setup_access_events(hass, entry, _collector(added))
    async_dispatcher_send(
        hass, access_doorbell_signal(entry.entry_id), {"device_id": "reader"}
    )
    await hass.async_block_till_done()

    assert added == []


async def test_known_doorbell_device_is_created_without_ringing(hass: Any) -> None:
    """Discovery from the device list creates a doorbell without firing a ring."""
    _, _, added = await _wired(hass)
    doorbells = _doorbells(added)

    assert set(doorbells) == {"reader"}
    entity = doorbells["reader"]
    assert entity.unique_id.endswith("_reader_doorbell")
    with patch.object(entity, "_trigger_event") as trigger:
        await _added(entity)
    trigger.assert_not_called()


async def test_ring_for_a_known_device_fires_one_ring_event(hass: Any) -> None:
    """A live ring frame for a known doorbell fires one ring event on that entity."""
    entry, _, added = await _wired(hass)
    entity = _doorbells(added)["reader"]
    await _added(entity)

    with (
        patch.object(entity, "_trigger_event") as trigger,
        patch.object(entity, "async_write_ha_state"),
    ):
        async_dispatcher_send(
            hass,
            access_doorbell_signal(entry.entry_id),
            {
                "device_id": "reader",
                "device_name": "Lobby",
                "door_name": "Front",
                "device_type": "UA-G2-PRO",
            },
        )
        await hass.async_block_till_done()

    trigger.assert_called_once_with("ring")


async def test_ring_from_an_unknown_device_creates_and_fires_once(hass: Any) -> None:
    """A ring from hardware the device list did not name creates that entity."""
    entry, _, added = await _wired(hass, devices=[])
    assert added == []

    async_dispatcher_send(
        hass,
        access_doorbell_signal(entry.entry_id),
        {
            "device_id": "new-reader",
            "device_name": "Side",
            "door_name": None,
            "device_type": "UA-G2-PRO",
        },
    )
    await hass.async_block_till_done()

    assert set(_doorbells(added)) == {"new-reader"}
    entity = _doorbells(added)["new-reader"]
    assert entity._pending_ring is True
    with (
        patch.object(entity, "_trigger_event") as trigger,
        patch.object(entity, "async_write_ha_state"),
    ):
        await entity.async_added_to_hass()
    trigger.assert_called_once_with("ring")
    assert entity._pending_ring is False


async def test_ring_before_add_is_held_then_fired_on_add(hass: Any) -> None:
    """A ring that arrives before Home Assistant adds the entity is fired on add."""
    _, coordinator, _ = await _wired(hass)
    entity = event_platform.UnifiAccessDoorbellEvent(coordinator, "reader", None)

    entity.async_ring()
    assert entity._pending_ring is True

    with (
        patch.object(entity, "_trigger_event") as trigger,
        patch.object(entity, "async_write_ha_state"),
    ):
        await entity.async_added_to_hass()
    trigger.assert_called_once_with("ring")


async def test_reload_does_not_repeat_an_old_ring(hass: Any) -> None:
    """An entity created without a live ring never fires one, so reloads stay quiet."""
    _, coordinator, _ = await _wired(hass)
    entity = event_platform.UnifiAccessDoorbellEvent(coordinator, "reader", None)

    with patch.object(entity, "_trigger_event") as trigger:
        await _added(entity)

    trigger.assert_not_called()
    assert entity._pending_ring is False


async def test_doorbell_is_unavailable_when_notifications_are_denied(hass: Any) -> None:
    """Once the notification endpoint refuses, doorbell entities report unavailable."""
    _, coordinator, added = await _wired(hass)
    entity = _doorbells(added)["reader"]
    assert entity.available

    coordinator._deny("notifications", 403)

    assert not entity.available


async def test_result_entities_exist_only_while_webhook_is_active(hass: Any) -> None:
    """Result entities are created only once the verified webhook is active."""
    _, coordinator, added = await _wired(hass)
    assert _results(added) == {}

    coordinator.async_set_webhook_active(active=True)

    assert set(_results(added)) == {"door-gate", "door-plain", "door-unbound"}


async def test_granted_and_denied_results_fire_the_matching_event(hass: Any) -> None:
    """A verified result fires the event type for its door, with no secrets."""
    entry, coordinator, added = await _wired(hass)
    coordinator.async_set_webhook_active(active=True)
    entity = _results(added)["door-plain"]
    await _added(entity)

    with (
        patch.object(entity, "_trigger_event") as trigger,
        patch.object(entity, "async_write_ha_state"),
    ):
        async_dispatch_access_result(
            hass,
            entry.entry_id,
            {"door_id": "door-plain", "result": "granted", "authentication": "nfc"},
        )
        async_dispatch_access_result(
            hass,
            entry.entry_id,
            {"door_id": "door-plain", "result": "denied", "authentication": None},
        )
        await hass.async_block_till_done()

    assert trigger.call_args_list[0].args == (
        "access_granted",
        {"door_id": "door-plain", "authentication": "nfc"},
    )
    assert trigger.call_args_list[1].args == (
        "access_denied",
        {"door_id": "door-plain", "authentication": None},
    )


async def test_result_for_unknown_door_or_result_is_ignored(hass: Any) -> None:
    """Results for doors without an entity, or with unknown results, fire nothing."""
    entry, coordinator, added = await _wired(hass)
    coordinator.async_set_webhook_active(active=True)
    entity = _results(added)["door-plain"]
    await _added(entity)

    with patch.object(entity, "_trigger_event") as trigger:
        async_dispatch_access_result(
            hass,
            entry.entry_id,
            {"door_id": "missing-door", "result": "granted", "authentication": None},
        )
        async_dispatch_access_result(
            hass,
            entry.entry_id,
            {"door_id": "door-plain", "result": "maybe", "authentication": None},
        )
        await hass.async_block_till_done()

    trigger.assert_not_called()


async def test_result_before_add_is_dropped(hass: Any) -> None:
    """A result for an entity that Home Assistant has not added is not written."""
    _, coordinator, _ = await _wired(hass)
    entity = event_platform.UnifiAccessResultEvent(coordinator, "door-plain")

    with patch.object(entity, "_trigger_event") as trigger:
        entity.async_result("granted", None)

    trigger.assert_not_called()


async def test_result_entity_is_unavailable_when_webhook_turns_off(hass: Any) -> None:
    """Turning the webhook off makes its result entities unavailable."""
    _, coordinator, added = await _wired(hass)
    coordinator.async_set_webhook_active(active=True)
    entity = _results(added)["door-plain"]
    assert entity.available

    coordinator.async_set_webhook_active(active=False)

    assert not entity.available


async def test_result_setup_listens_on_the_entry_scoped_signal(hass: Any) -> None:
    """Results sent on another entry's signal never reach this entry's entities."""
    _, coordinator, added = await _wired(hass)
    coordinator.async_set_webhook_active(active=True)
    entity = _results(added)["door-plain"]
    await _added(entity)

    with patch.object(entity, "_trigger_event") as trigger:
        async_dispatcher_send(
            hass,
            access_result_signal("another-entry"),
            {"door_id": "door-plain", "result": "granted", "authentication": None},
        )
        await hass.async_block_till_done()

    trigger.assert_not_called()


async def test_doorbell_signal_is_entry_scoped(hass: Any) -> None:
    """Rings sent on another entry's signal do not create entities here."""
    _, _, added = await _wired(hass, devices=[])

    async_dispatcher_send(
        hass,
        access_doorbell_signal("another-entry"),
        {
            "device_id": "stranger",
            "device_name": None,
            "door_name": None,
            "device_type": None,
        },
    )
    await hass.async_block_till_done()

    assert added == []


# --- Network rule change events ----------------------------------------------

# Tests for UniFi network rule change event entities.

_UNIQUE_ID = "console_a_site1_network_rule_changes"


def _change(
    sequence: int, change_type: str = "modified", **extra: Any
) -> dict[str, Any]:
    """Build one synthetic change record."""
    record: dict[str, Any] = {
        "sequence": sequence,
        "change_type": change_type,
        "site_id": "site1",
        "rule_type": "port_forward",
        "rule_id": f"pf{sequence}",
        "rule_name": f"Port Forward {sequence}",
        "enabled": True,
        "changed_fields": ["enabled"] if change_type == "modified" else [],
    }
    record.update(extra)
    return record


def _rule_event_entry(
    hass: HomeAssistant, namespace: str | None = "console_a"
) -> MockConfigEntry:
    """Create and register a mock config entry."""
    data: dict[str, Any] = {CONF_API_KEY: "synthetic_credential"}
    if namespace is not None:
        data[CONF_CONSOLE_ID] = namespace
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        entry_id="entry_a",
        unique_id=namespace,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def rule_audit_coordinator() -> MagicMock:
    """Create a mock facade coordinator for rule audit testing."""
    coord = MagicMock()
    coord.protect_client = None
    coord.data = {
        "sites": {"site1": {"id": "site1", "name": "Default"}},
        "devices": {},
        "rule_changes": {},
    }
    return coord


def test_identity_and_metadata(rule_audit_coordinator: MagicMock) -> None:
    """Event entity initializes with correct identity, metadata, and device info."""
    entity = UnifiInsightsNetworkRuleChangeEvent(
        rule_audit_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    assert entity.unique_id == _UNIQUE_ID
    assert entity.translation_key == "network_rule_changes"
    assert entity.has_entity_name is True
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert entity.event_types == ["added", "modified", "deleted"]
    assert entity.device_info == {
        "identifiers": {(DOMAIN, "site_site1")},
        "name": "UniFi Site (Default)",
        "manufacturer": "Ubiquiti Inc.",
        "model": "UniFi Site",
    }


def test_existing_changes_are_not_replayed(
    rule_audit_coordinator: MagicMock, monkeypatch
) -> None:
    """Changes present before entity creation are treated as history."""
    rule_audit_coordinator.data = with_sections(
        rule_audit_coordinator.data,
        rule_changes={
            "site1": [_change(1), _change(2)],
        },
    )
    entity = UnifiInsightsNetworkRuleChangeEvent(
        rule_audit_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    patch_attribute(monkeypatch, entity, "_trigger_event", MagicMock())
    patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())
    entity._handle_coordinator_update()
    require_mock(entity._trigger_event).assert_not_called()
    require_mock(entity.async_write_ha_state).assert_not_called()


def test_each_new_change_fires_once_in_order(
    rule_audit_coordinator: MagicMock, monkeypatch
) -> None:
    """New changes fire in sequence order; attributes contain allow-list keys only."""
    entity = UnifiInsightsNetworkRuleChangeEvent(
        rule_audit_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    patch_attribute(monkeypatch, entity, "_trigger_event", MagicMock())
    patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())

    rule_audit_coordinator.data = with_sections(
        rule_audit_coordinator.data,
        rule_changes={
            "site1": [
                _change(2, "deleted", leaked="10.0.0.1"),
                _change(1, "added"),
            ],
            "site2": [_change(3, "added", site_id="site2")],
        },
    )
    entity._handle_coordinator_update()

    assert require_mock(entity._trigger_event).call_count == 2
    calls = require_mock(entity._trigger_event).call_args_list
    assert [c[0][0] for c in calls] == ["added", "deleted"]
    second_payload = calls[1][0][1]
    assert tuple(second_payload) == EVENT_ATTRIBUTE_KEYS
    assert "leaked" not in second_payload
    assert require_mock(entity.async_write_ha_state).call_count == 2

    # Subsequent update does not refire already triggered events
    entity._handle_coordinator_update()
    assert require_mock(entity._trigger_event).call_count == 2
    assert require_mock(entity.async_write_ha_state).call_count == 2


@pytest.mark.parametrize(
    "rule_changes",
    [
        None,
        {"site1": None},
        {"site1": ["x", {"sequence": "1", "change_type": "added"}]},
        {"site1": [{"sequence": 5, "change_type": "renamed"}]},
    ],
)
def test_malformed_change_data_fires_nothing(
    rule_audit_coordinator: MagicMock, rule_changes: Any, monkeypatch
) -> None:
    """Malformed change structures are ignored and fire no events."""
    entity = UnifiInsightsNetworkRuleChangeEvent(
        rule_audit_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    patch_attribute(monkeypatch, entity, "_trigger_event", MagicMock())
    patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())
    rule_audit_coordinator.data = with_sections(
        rule_audit_coordinator.data, rule_changes=rule_changes
    )
    entity._handle_coordinator_update()
    require_mock(entity._trigger_event).assert_not_called()


def test_non_dict_coordinator_data_is_tolerated(
    rule_audit_coordinator: MagicMock,
) -> None:
    """Non-dict coordinator data yields empty changes and skips discovery safely."""
    entity = UnifiInsightsNetworkRuleChangeEvent(
        rule_audit_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    rule_audit_coordinator.data = None
    assert entity._site_changes() == []
    known: set[str] = set()
    entry = MagicMock()
    assert (
        discover_network_rule_change_events(
            rule_audit_coordinator,
            known,
            hass=MagicMock(),
            entry=entry,
            namespace="console_a",
        )
        == []
    )


def test_available_follows_config_coordinator(
    rule_audit_coordinator: MagicMock,
) -> None:
    """Entity availability tracks coordinator config_available property."""
    entity = UnifiInsightsNetworkRuleChangeEvent(
        rule_audit_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    set_availability(rule_audit_coordinator, config=True)
    assert entity.available is True
    set_availability(rule_audit_coordinator, config=False)
    assert entity.available is False


async def test_discovery_adds_one_namespaced_entity_per_new_site(
    hass: HomeAssistant, rule_audit_coordinator: MagicMock
) -> None:
    """Discovery creates namespaced entities for newly seen sites only."""
    entry = _rule_event_entry(hass)
    known: set[str] = set()
    res1 = discover_network_rule_change_events(
        rule_audit_coordinator, known, hass=hass, entry=entry, namespace="console_a"
    )
    assert [e.unique_id for e in res1] == ["console_a_site1_network_rule_changes"]

    rule_audit_coordinator.data = with_sections(
        rule_audit_coordinator.data,
        sites={
            **rule_audit_coordinator.data["sites"],
            "site2": {"id": "site2", "name": "Site 2"},
            7: {"id": "7"},
        },
    )
    res2 = discover_network_rule_change_events(
        rule_audit_coordinator, known, hass=hass, entry=entry, namespace="console_a"
    )
    assert [e.unique_id for e in res2] == ["console_a_site2_network_rule_changes"]

    res3 = discover_network_rule_change_events(
        rule_audit_coordinator, known, hass=hass, entry=entry, namespace="console_a"
    )
    assert res3 == []


async def test_discovery_without_namespace_skips_and_retries(
    hass: HomeAssistant, rule_audit_coordinator: MagicMock
) -> None:
    """Missing namespace skips entity discovery until resolved on entry update."""
    entry = _rule_event_entry(hass, None)
    known: set[str] = set()
    res1 = discover_network_rule_change_events(
        rule_audit_coordinator, known, hass=hass, entry=entry, namespace=None
    )
    assert res1 == []
    assert known == set()

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_CONSOLE_ID: "console_a"}
    )
    res2 = discover_network_rule_change_events(
        rule_audit_coordinator, known, hass=hass, entry=entry, namespace="console_a"
    )
    assert len(res2) == 1
    assert res2[0].unique_id == "console_a_site1_network_rule_changes"
    assert "site1" in known


async def test_setup_entry_adds_rule_change_event_without_protect(
    hass: HomeAssistant, rule_audit_coordinator: MagicMock
) -> None:
    """Event setup registers rule change entities even when Protect is absent."""
    entry = _rule_event_entry(hass)
    entry.runtime_data = MagicMock(coordinator=rule_audit_coordinator)
    listeners: list[Any] = []
    rule_audit_coordinator.async_add_listener.side_effect = lambda cb: (
        listeners.append(cb) or (lambda: None)
    )
    added_entities: list[Any] = []

    _async_add = EntityCollector(added_entities)

    await async_setup_entry(hass, entry, _async_add)
    assert len(added_entities) == 1
    assert isinstance(added_entities[0], UnifiInsightsNetworkRuleChangeEvent)
    assert rule_audit_coordinator.async_add_listener.call_count == 1

    rule_audit_coordinator.data = with_sections(
        rule_audit_coordinator.data,
        sites={
            **rule_audit_coordinator.data["sites"],
            "site2": {"id": "site2", "name": "Site 2"},
        },
    )
    assert len(listeners) == 1
    listeners[0]()
    assert len(added_entities) == 2


async def test_two_changes_in_one_refresh_are_two_state_changes(
    hass: HomeAssistant, rule_audit_coordinator: MagicMock, freezer: Any
) -> None:
    """Through the real state machine: every change is its own state write."""
    freezer.move_to("2026-01-01 00:00:00+00:00")
    entity = UnifiInsightsNetworkRuleChangeEvent(
        rule_audit_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    platform = MockEntityPlatform(hass, domain="event", platform_name=DOMAIN)
    await platform.async_add_entities([entity])
    await hass.async_block_till_done()
    events = async_capture_events(hass, EVENT_STATE_CHANGED)

    rule_audit_coordinator.data = with_sections(
        rule_audit_coordinator.data,
        rule_changes={
            "site1": [
                _change(1, "added"),
                _change(2, "deleted", leaked="10.0.0.1"),
            ]
        },
    )
    entity._handle_coordinator_update()
    await hass.async_block_till_done()

    states = [e.data["new_state"] for e in events]
    assert [require_not_none(s).attributes["event_type"] for s in states] == [
        "added",
        "deleted",
    ]
    assert [require_not_none(s).attributes["rule_id"] for s in states] == ["pf1", "pf2"]
    t0 = dt_util.parse_datetime(require_not_none(states[0]).state)
    t1 = dt_util.parse_datetime(require_not_none(states[1]).state)
    assert t0 is not None
    assert t1 is not None
    assert t0 <= t1
    assert "leaked" not in require_not_none(states[1]).attributes


async def test_event_data_coerces_unexpected_value_types(
    hass: HomeAssistant, rule_audit_coordinator: MagicMock
) -> None:
    """Unexpected value types are coerced before they reach the event data."""
    entity = UnifiInsightsNetworkRuleChangeEvent(
        rule_audit_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    platform = MockEntityPlatform(hass, domain="event", platform_name=DOMAIN)
    await platform.async_add_entities([entity])
    await hass.async_block_till_done()

    rule_audit_coordinator.data = with_sections(
        rule_audit_coordinator.data,
        rule_changes={
            "site1": [
                _change(
                    1,
                    "modified",
                    changed_fields=["enabled", {"fwd": "192.0.2.9"}, 5],
                    enabled="true",
                    rule_name={"x": 1},
                )
            ]
        },
    )
    entity._handle_coordinator_update()
    await hass.async_block_till_done()

    state = hass.states.get(entity.entity_id)
    assert state is not None
    assert state.attributes["changed_fields"] == ["enabled"]
    assert state.attributes["enabled"] is None
    assert state.attributes["rule_name"] is None
    assert "192.0.2.9" not in str(state.attributes)


async def test_unavailable_round_trip_writes_no_new_event(
    hass: HomeAssistant, rule_audit_coordinator: MagicMock, freezer: Any, monkeypatch
) -> None:
    """An entity becoming unavailable and returning does not fire a new event."""
    freezer.move_to("2026-01-01 00:00:00+00:00")
    entity = UnifiInsightsNetworkRuleChangeEvent(
        rule_audit_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    patch_attribute(
        monkeypatch, entity, "_trigger_event", MagicMock(wraps=entity._trigger_event)
    )
    platform = MockEntityPlatform(hass, domain="event", platform_name=DOMAIN)
    await platform.async_add_entities([entity])
    await hass.async_block_till_done()

    events = async_capture_events(hass, EVENT_STATE_CHANGED)

    # Fire one change
    rule_audit_coordinator.data = with_sections(
        rule_audit_coordinator.data, rule_changes={"site1": [_change(1)]}
    )
    entity._handle_coordinator_update()
    await hass.async_block_till_done()

    # Make unavailable
    set_availability(rule_audit_coordinator, config=False)
    entity._handle_coordinator_update()
    await hass.async_block_till_done()

    # Make available again
    set_availability(rule_audit_coordinator, config=True)
    entity._handle_coordinator_update()
    await hass.async_block_till_done()

    states = [e.data["new_state"] for e in events]
    ts = require_not_none(states[0]).state
    assert [require_not_none(s).state for s in states] == [ts, "unavailable", ts]
    require_mock(entity._trigger_event).assert_called_once()
