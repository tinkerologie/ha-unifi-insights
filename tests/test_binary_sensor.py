# Copyright 2026 UniFi Insights contributors
"""Tests for UniFi Insights binary sensors."""

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.helpers.typing import UNDEFINED

from custom_components.unifi_insights.binary_sensor import (
    BINARY_SENSOR_TYPES,
    UnifiInsightsBinarySensor,
    UnifiInsightsBinarySensorEntityDescription,
    UnifiInsightsSiteToSiteVpnBinarySensor,
    UnifiInsightsWanLinkBinarySensor,
    UnifiPortBinarySensor,
    UnifiProtectBinarySensor,
    _get_supported_smart_detect_types,
    _is_doorbell_camera,
    _is_smart_detect_active,
    async_setup_entry,
)
from custom_components.unifi_insights.const import (
    CAMERA_TYPE_DOORBELL,
    CAMERA_TYPE_DOORBELL_MAIN,
    DEVICE_TYPE_CAMERA,
    DEVICE_TYPE_SENSOR,
)
from custom_components.unifi_insights.protect_security_entity import (
    UnifiProtectSecurityBinarySensor,
)
from tests.fixtures.library_responses import (
    SAMPLE_ALARM_HUB,
    SAMPLE_THREAD_LINK_STATION,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class TestIsDoorbellCamera:
    """Tests for _is_doorbell_camera helper function."""

    def test_doorbell_type_metadata(self):
        """Test detection via _camera_type metadata."""
        camera_data = {"_camera_type": CAMERA_TYPE_DOORBELL}
        assert _is_doorbell_camera(camera_data) is True

    def test_doorbell_main_type(self):
        """Test detection via doorbell main type."""
        camera_data = {"_camera_type": CAMERA_TYPE_DOORBELL_MAIN}
        assert _is_doorbell_camera(camera_data) is True

    def test_api_type_doorbell(self):
        """Test detection via API type field."""
        camera_data = {"type": "G4-Doorbell"}
        assert _is_doorbell_camera(camera_data) is True

    def test_api_type_ai_doorbell(self):
        """Test detection via AI doorbell type."""
        camera_data = {"type": "AI-Doorbell-Pro"}
        assert _is_doorbell_camera(camera_data) is True

    def test_name_doorbell(self):
        """Test detection via name."""
        camera_data = {"name": "Front Door Doorbell"}
        assert _is_doorbell_camera(camera_data) is True

    def test_name_front_door(self):
        """Test detection via front door name."""
        camera_data = {"name": "Front Door Camera"}
        assert _is_doorbell_camera(camera_data) is True

    def test_regular_camera(self):
        """Test regular camera is not doorbell."""
        camera_data = {"name": "Garage Camera", "type": "UVC-G4-PRO"}
        assert _is_doorbell_camera(camera_data) is False

    def test_empty_data(self):
        """Test empty data returns False."""
        camera_data = {}
        assert _is_doorbell_camera(camera_data) is False

    def test_none_values(self):
        """Test None values are handled."""
        camera_data = {"name": None, "type": None, "_camera_type": None}
        assert _is_doorbell_camera(camera_data) is False


class TestBinarySensorTypes:
    """Tests for binary sensor type definitions."""

    def test_binary_sensor_types_defined(self):
        """Test that binary sensor types are defined."""
        assert len(BINARY_SENSOR_TYPES) > 0

    def test_device_status_sensor(self):
        """Test device status sensor is defined."""
        device_status = next(
            (s for s in BINARY_SENSOR_TYPES if s.key == "device_status"), None
        )
        assert device_status is not None
        assert device_status.device_class == BinarySensorDeviceClass.CONNECTIVITY

    def test_camera_motion_sensor(self):
        """Test camera motion sensor is defined."""
        motion = next(
            (s for s in BINARY_SENSOR_TYPES if s.key == "camera_motion"), None
        )
        assert motion is not None
        assert motion.device_class == BinarySensorDeviceClass.MOTION
        assert motion.device_type == DEVICE_TYPE_CAMERA

    def test_camera_person_detection(self):
        """Test camera person detection sensor."""
        person = next(
            (s for s in BINARY_SENSOR_TYPES if s.key == "camera_person_detection"),
            None,
        )
        assert person is not None
        assert person.device_class == BinarySensorDeviceClass.MOTION

    def test_sensor_door_sensor(self):
        """Test sensor door sensor is defined."""
        door = next((s for s in BINARY_SENSOR_TYPES if s.key == "sensor_door"), None)
        assert door is not None
        assert door.device_class == BinarySensorDeviceClass.DOOR
        assert door.device_type == DEVICE_TYPE_SENSOR

    def test_sensor_tamper_sensor(self):
        """Test sensor tamper sensor is defined."""
        tamper = next(
            (s for s in BINARY_SENSOR_TYPES if s.key == "sensor_tamper"), None
        )
        assert tamper is not None
        assert tamper.device_class == BinarySensorDeviceClass.TAMPER
        assert tamper.device_type == DEVICE_TYPE_SENSOR

    def test_sensor_leak_sensor(self):
        """Test sensor leak sensor is defined."""
        leak = next((s for s in BINARY_SENSOR_TYPES if s.key == "sensor_leak"), None)
        assert leak is not None
        assert leak.device_class == BinarySensorDeviceClass.MOISTURE
        assert leak.device_type == DEVICE_TYPE_SENSOR

    def test_device_status_category(self):
        """Test device_status binary sensor has diagnostic category."""
        status = next(
            (s for s in BINARY_SENSOR_TYPES if s.key == "device_status"), None
        )
        assert status is not None
        assert status.entity_category == EntityCategory.DIAGNOSTIC


class TestUnifiInsightsBinarySensor:
    """Tests for UnifiInsightsBinarySensor."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.protect_client = MagicMock()
        coordinator.data = {
            "sites": {"site1": {"id": "site1", "meta": {"name": "Default"}}},
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "name": "Test Switch",
                        "model": "USW-24-POE",
                        "state": "ONLINE",
                        "macAddress": "AA:BB:CC:DD:EE:FF",
                        "ipAddress": "192.168.1.10",
                        "firmwareVersion": "6.5.55",
                    },
                    "device2": {
                        "id": "device2",
                        "name": "UDM Pro",
                        "model": "UDM-PRO",
                        "state": "OFFLINE",
                        "macAddress": "11:22:33:44:55:66",
                        "ipAddress": "192.168.1.1",
                    },
                },
            },
            "stats": {},
            "clients": {},
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "Front Door",
                        "state": "CONNECTED",
                        "smartDetectTypes": ["person", "vehicle"],
                        "lastSmartDetectTypes": [],
                        "lastMotionStart": None,
                        "lastMotionEnd": None,
                        "lastRingStart": None,
                        "lastRingEnd": None,
                    },
                    "camera2": {
                        "id": "camera2",
                        "name": "Doorbell",
                        "state": "CONNECTED",
                        "_camera_type": "doorbell",
                        "smartDetectTypes": ["person", "package"],
                        "lastSmartDetectTypes": ["person"],
                        "lastMotionStart": 1234567890,
                        "lastMotionEnd": None,
                        "lastRingStart": None,
                        "lastRingEnd": None,
                    },
                },
                "lights": {},
                "sensors": {
                    "sensor1": {
                        "id": "sensor1",
                        "name": "Front Door Sensor",
                        "state": "CONNECTED",
                        "isMotionDetected": False,
                        "isOpened": True,
                    },
                },
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    async def test_device_status_sensor_online(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test device status sensor when online."""
        description = next(s for s in BINARY_SENSOR_TYPES if s.key == "device_status")

        sensor = UnifiInsightsBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        assert sensor.is_on is True

    async def test_device_status_sensor_offline(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test device status sensor when offline."""
        description = next(s for s in BINARY_SENSOR_TYPES if s.key == "device_status")

        sensor = UnifiInsightsBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device2",
        )

        assert sensor.is_on is False

    async def test_device_status_no_data(self, hass: HomeAssistant, mock_coordinator):
        """Test device status when data removed."""
        description = next(s for s in BINARY_SENSOR_TYPES if s.key == "device_status")

        sensor = UnifiInsightsBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        # Remove device data
        mock_coordinator.data["devices"]["site1"]["device1"] = None

        assert sensor.is_on is None

    async def test_insights_binary_sensor_none_value_fn(
        self, hass: HomeAssistant, mock_coordinator
    ) -> None:
        """Test is_on returns None when entity_description value_fn is None."""
        description = UnifiInsightsBinarySensorEntityDescription(
            key="test_no_fn",
            value_fn=None,
        )
        sensor = UnifiInsightsBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )
        assert sensor.is_on is None

    async def test_entity_category_inherited_from_description(
        self, hass: HomeAssistant, mock_coordinator
    ) -> None:
        """Test entity_category is inherited from entity_description and not forced."""
        desc_diag = UnifiInsightsBinarySensorEntityDescription(
            key="test_diag",
            entity_category=EntityCategory.DIAGNOSTIC,
        )
        sensor_diag = UnifiInsightsBinarySensor(
            coordinator=mock_coordinator,
            description=desc_diag,
            site_id="site1",
            device_id="device1",
        )
        assert sensor_diag.entity_category == EntityCategory.DIAGNOSTIC

        desc_none = UnifiInsightsBinarySensorEntityDescription(
            key="test_none",
            entity_category=None,
        )
        sensor_none = UnifiInsightsBinarySensor(
            coordinator=mock_coordinator,
            description=desc_none,
            site_id="site1",
            device_id="device1",
        )
        assert sensor_none.entity_category is None


class TestUnifiProtectBinarySensor:
    """Tests for UnifiProtectBinarySensor."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {},
            "devices": {},
            "stats": {},
            "clients": {},
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "Front Camera",
                        "state": "CONNECTED",
                        "type": "UVC-G4-PRO",
                        # Supported smart detect types in featureFlags (like real API)
                        "featureFlags": {
                            "smartDetectTypes": ["person", "vehicle"],
                        },
                        "isMotionDetected": False,
                        "isSmartDetected": False,
                        "lastSmartDetectTypes": [],
                        "lastMotionStart": None,
                        "lastMotionEnd": None,
                        "lastRingStart": None,
                        "lastRingEnd": None,
                    },
                    "camera2": {
                        "id": "camera2",
                        "name": "Doorbell",
                        "state": "CONNECTED",
                        "_camera_type": "doorbell",
                        "type": "G4-Doorbell",
                        # Supported smart detect types in featureFlags (like real API)
                        "featureFlags": {
                            "smartDetectTypes": ["person", "package"],
                        },
                        # Active detection state
                        "isMotionDetected": True,
                        "isSmartDetected": True,
                        "lastSmartDetectTypes": ["person"],
                        "lastMotionStart": 1234567890,
                        "lastMotionEnd": None,
                        "lastRingStart": 1234567800,
                        "lastRingEnd": None,
                    },
                },
                "lights": {},
                "sensors": {
                    "sensor1": {
                        "id": "sensor1",
                        "name": "Door Sensor",
                        "state": "CONNECTED",
                        "isMotionDetected": True,
                        "motionDetectedAt": 1234567890,
                        "isOpened": True,
                        "openStatusChangedAt": 1234567800,
                        "isTamperingDetected": True,
                        "tamperingDetectedAt": 1234567850,
                        "isLeakDetected": False,
                        "leakDetectedAt": 0,
                    },
                    "sensor2": {
                        "id": "sensor2",
                        "name": "Water Leak Sensor",
                        "state": "CONNECTED",
                        "isMotionDetected": False,
                        "motionDetectedAt": 0,
                        "isOpened": False,
                        "openStatusChangedAt": 0,
                        "isTamperingDetected": False,
                        "tamperingDetectedAt": 0,
                        "isLeakDetected": True,
                        "leakDetectedAt": 1234567900,
                    },
                },
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    async def test_camera_motion_no_motion(self, hass: HomeAssistant, mock_coordinator):
        """Test camera motion sensor when no motion."""
        description = next(s for s in BINARY_SENSOR_TYPES if s.key == "camera_motion")

        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="camera1",
        )

        assert sensor.is_on is False

    async def test_camera_motion_with_motion(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test camera motion sensor with active motion."""
        description = next(s for s in BINARY_SENSOR_TYPES if s.key == "camera_motion")

        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="camera2",
        )

        assert sensor.is_on is True

    async def test_camera_person_detection_no_person(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test person detection when no person."""
        description = next(
            s for s in BINARY_SENSOR_TYPES if s.key == "camera_person_detection"
        )

        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="camera1",
        )

        assert sensor.is_on is False

    async def test_camera_person_detection_with_person(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test person detection with active person detection."""
        description = next(
            s for s in BINARY_SENSOR_TYPES if s.key == "camera_person_detection"
        )

        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="camera2",
        )

        assert sensor.is_on is True

    async def test_camera_doorbell_ring(self, hass: HomeAssistant, mock_coordinator):
        """Test doorbell ring detection."""
        description = next(
            s for s in BINARY_SENSOR_TYPES if s.key == "camera_doorbell_ring"
        )

        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="camera2",
        )

        assert sensor.is_on is True

    async def test_camera_microphone_is_mic_enabled_true(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test camera microphone binary sensor when isMicEnabled is True."""
        mock_coordinator.data["protect"]["cameras"]["camera1"]["isMicEnabled"] = True
        description = next(
            s for s in BINARY_SENSOR_TYPES if s.key == "camera_microphone"
        )
        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="camera1",
        )
        assert sensor.is_on is True
        assert sensor.unique_id == "unifi_insights_camera_camera1_camera_microphone"
        assert sensor.translation_key == "camera_microphone"
        assert sensor.entity_description.entity_category == EntityCategory.DIAGNOSTIC

    async def test_camera_microphone_precedence_over_legacy(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test isMicEnabled False takes precedence over legacy micEnabled True."""
        mock_coordinator.data["protect"]["cameras"]["camera1"]["isMicEnabled"] = False
        mock_coordinator.data["protect"]["cameras"]["camera1"]["micEnabled"] = True
        description = next(
            s for s in BINARY_SENSOR_TYPES if s.key == "camera_microphone"
        )
        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="camera1",
        )
        assert sensor.is_on is False

    async def test_camera_microphone_legacy_fallback(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test legacy micEnabled fallback when isMicEnabled is None."""
        mock_coordinator.data["protect"]["cameras"]["camera1"]["isMicEnabled"] = None
        mock_coordinator.data["protect"]["cameras"]["camera1"]["micEnabled"] = True
        description = next(
            s for s in BINARY_SENSOR_TYPES if s.key == "camera_microphone"
        )
        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="camera1",
        )
        assert sensor.is_on is True

        mock_coordinator.data["protect"]["cameras"]["camera1"]["micEnabled"] = False
        sensor_off = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="camera1",
        )
        assert sensor_off.is_on is False

    async def test_camera_microphone_both_missing_defaults_off(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test camera microphone defaults to False when both fields are missing."""
        mock_coordinator.data["protect"]["cameras"]["camera1"].pop("isMicEnabled", None)
        mock_coordinator.data["protect"]["cameras"]["camera1"].pop("micEnabled", None)
        description = next(
            s for s in BINARY_SENSOR_TYPES if s.key == "camera_microphone"
        )
        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="camera1",
        )
        assert sensor.is_on is False

    async def test_camera_microphone_missing_camera_data(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test is_on returns None when camera data is missing."""
        description = next(
            s for s in BINARY_SENSOR_TYPES if s.key == "camera_microphone"
        )
        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="nonexistent_camera",
        )
        assert sensor.is_on is None

    async def test_camera_microphone_no_extra_state_attributes(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test camera microphone binary sensor exposes no extra attributes."""
        description = next(
            s for s in BINARY_SENSOR_TYPES if s.key == "camera_microphone"
        )
        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="camera1",
        )
        assert sensor.extra_state_attributes == {}
        assert "last_motion" not in (sensor.extra_state_attributes or {})

    async def test_sensor_motion_detected(self, hass: HomeAssistant, mock_coordinator):
        """Test sensor motion detection."""
        description = next(s for s in BINARY_SENSOR_TYPES if s.key == "sensor_motion")

        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor1",
        )

        assert sensor.is_on is True

    async def test_sensor_door_opened(self, hass: HomeAssistant, mock_coordinator):
        """Test sensor door status."""
        description = next(s for s in BINARY_SENSOR_TYPES if s.key == "sensor_door")

        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor1",
        )

        assert sensor.is_on is True

    async def test_sensor_tamper_detected(self, hass: HomeAssistant, mock_coordinator):
        """Test sensor tamper detection."""
        description = next(s for s in BINARY_SENSOR_TYPES if s.key == "sensor_tamper")

        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor1",
        )

        assert sensor.is_on is True

    async def test_sensor_tamper_not_detected(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test sensor tamper when not detected."""
        description = next(s for s in BINARY_SENSOR_TYPES if s.key == "sensor_tamper")

        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor2",
        )

        assert sensor.is_on is False

    async def test_sensor_leak_detected(self, hass: HomeAssistant, mock_coordinator):
        """Test leak sensor detection."""
        description = next(s for s in BINARY_SENSOR_TYPES if s.key == "sensor_leak")

        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor2",
        )

        assert sensor.is_on is True

    async def test_sensor_leak_not_detected(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test leak sensor when no leak detected."""
        description = next(s for s in BINARY_SENSOR_TYPES if s.key == "sensor_leak")

        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="sensor1",
        )

        assert sensor.is_on is False

    async def test_protect_sensor_no_data(self, hass: HomeAssistant, mock_coordinator):
        """Test protect sensor when data removed."""
        description = next(s for s in BINARY_SENSOR_TYPES if s.key == "camera_motion")

        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="camera1",
        )

        # Remove camera data
        mock_coordinator.data["protect"]["cameras"]["camera1"] = None

        assert sensor.is_on is None

    async def test_protect_binary_sensor_none_value_fn(
        self, hass: HomeAssistant, mock_coordinator
    ) -> None:
        """Test is_on returns None when entity_description value_fn is None."""
        description = UnifiInsightsBinarySensorEntityDescription(
            key="test_protect_no_fn",
            name="Test Protect Sensor",
            device_type=DEVICE_TYPE_CAMERA,
            value_fn=None,
        )
        sensor = UnifiProtectBinarySensor(
            coordinator=mock_coordinator,
            description=description,
            device_id="camera1",
        )
        assert sensor.is_on is None


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "name": "Test Device",
                        "model": "USW-24-POE",
                        "state": "ONLINE",
                        "macAddress": "AA:BB:CC:DD:EE:FF",
                        "ipAddress": "192.168.1.10",
                        "firmwareVersion": "6.5.55",
                    },
                },
            },
            "stats": {},
            "clients": {},
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "Front Door",
                        "state": "CONNECTED",
                        "type": "UVC-G4-PRO",
                        "smartDetectTypes": ["person"],
                        "lastSmartDetectTypes": [],
                        "lastMotionStart": None,
                        "lastMotionEnd": None,
                    },
                },
                "lights": {},
                "sensors": {
                    "sensor1": {
                        "id": "sensor1",
                        "name": "Door Sensor",
                        "state": "CONNECTED",
                    },
                },
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    @pytest.fixture
    def mock_config_entry(self, mock_coordinator):
        """Create mock config entry."""
        entry = MagicMock()
        entry.runtime_data = MagicMock()
        entry.runtime_data.mobility_coordinator = None
        entry.runtime_data.coordinator = mock_coordinator
        return entry

    async def test_setup_entry_creates_protect_security_binary_sensors(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Alarm hub tamper is discovered at setup, and on later updates."""
        mock_coordinator.data["protect"]["alarm_hubs"] = {
            "alarm_hub_1": SAMPLE_ALARM_HUB
        }
        listeners: list = []
        mock_coordinator.async_add_listener = listeners.append
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)
        mock_coordinator.data["protect"]["link_stations"] = {
            "link_station_thread": SAMPLE_THREAD_LINK_STATION
        }
        for listener in listeners:
            listener()

        keys = {
            (e._device_type, e.entity_description.key)
            for e in added_entities
            if isinstance(e, UnifiProtectSecurityBinarySensor)
        }
        assert keys == {
            ("alarm_hub", "alarm_hub_device_tamper"),
            ("link_station", "thread_network_problem"),
        }

    async def test_setup_entry_creates_sensors(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test that setup entry creates binary sensors."""
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        assert len(added_entities) > 0

    async def test_setup_entry_creates_device_sensors(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test that setup creates device binary sensors."""
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        device_sensors = [
            e for e in added_entities if isinstance(e, UnifiInsightsBinarySensor)
        ]
        assert len(device_sensors) > 0

    async def test_setup_entry_wan_status_for_feature_only_gateway(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """A gateway recognised only by its features still gets WAN status."""
        mock_coordinator.data["devices"]["site1"]["device2"] = {
            "id": "device2",
            "name": "Gateway",
            "model": "Unknown",
            "features": ["gateway"],
            "state": "ONLINE",
            "macAddress": "11:22:33:44:55:66",
        }
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        wan_status_devices = {
            e._device_id
            for e in added_entities
            if isinstance(e, UnifiInsightsBinarySensor)
            and e.entity_description.key == "wan_status"
        }
        assert wan_status_devices == {"device2"}

    async def test_setup_entry_creates_protect_sensors(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test that setup creates protect binary sensors."""
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        protect_sensors = [
            e for e in added_entities if isinstance(e, UnifiProtectBinarySensor)
        ]
        assert len(protect_sensors) > 0

    async def test_setup_entry_without_protect_client(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test setup without protect client."""
        mock_coordinator.protect_client = None

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        # Should still create device sensors
        device_sensors = [
            e for e in added_entities if isinstance(e, UnifiInsightsBinarySensor)
        ]
        assert len(device_sensors) > 0

        # But no protect sensors
        protect_sensors = [
            e for e in added_entities if isinstance(e, UnifiProtectBinarySensor)
        ]
        assert len(protect_sensors) == 0


class TestWanLinkBinarySensor:
    """Tests for the per-WAN link connectivity binary sensor."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create a coordinator holding one gateway with two WAN links."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.protect_client = None
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {
                "site1": {
                    "gw": {
                        "id": "gw",
                        "name": "Gateway",
                        "model": "UCG-Ultra",
                        "state": "ONLINE",
                        "macAddress": "11:22:33:44:55:66",
                        "wans": [
                            {
                                "key": "wan",
                                "name": "WAN",
                                "status": "online",
                                "alive": True,
                                "ip": "198.51.100.7",
                                "connected": True,
                            },
                            {
                                "key": "wan2",
                                "name": "WAN2",
                                "status": "offline",
                                "alive": False,
                                "ip": None,
                                "connected": False,
                            },
                        ],
                    },
                },
            },
            "stats": {},
            "clients": {},
        }
        return coordinator

    @pytest.fixture
    def mock_config_entry(self, mock_coordinator):
        """Create mock config entry."""
        entry = MagicMock()
        entry.runtime_data = MagicMock()
        entry.runtime_data.mobility_coordinator = None
        entry.runtime_data.coordinator = mock_coordinator
        return entry

    async def test_setup_entry_creates_one_sensor_per_wan(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Each merged WAN link gets its own connectivity sensor."""
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        wan = {
            e._wan_key: e
            for e in added_entities
            if isinstance(e, UnifiInsightsWanLinkBinarySensor)
        }
        assert set(wan) == {"wan", "wan2"}
        assert wan["wan"].unique_id == "site1_gw_wan_link_wan"
        assert wan["wan"].device_class == BinarySensorDeviceClass.CONNECTIVITY
        assert wan["wan"].translation_key == "wan_link"
        assert wan["wan"].translation_placeholders == {"wan_name": "WAN"}
        assert wan["wan2"].translation_placeholders == {"wan_name": "WAN2"}
        assert wan["wan"].is_on is True
        assert wan["wan2"].is_on is False
        assert wan["wan"].extra_state_attributes == {
            "status": "online",
            "alive": True,
            "ip": "198.51.100.7",
        }

    async def test_setup_entry_does_not_duplicate_on_refresh(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Rediscovery on later coordinator updates adds no duplicates."""
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]
        listener()

        wan_sensors = [
            e for e in added_entities if isinstance(e, UnifiInsightsWanLinkBinarySensor)
        ]
        assert len(wan_sensors) == 2

    async def test_state_unknown_when_wan_disappears(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """A WAN missing from the latest data reports unknown, not off."""
        sensor = UnifiInsightsWanLinkBinarySensor(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="gw",
            wan_key="wan",
            wan_name="WAN",
        )
        del mock_coordinator.data["devices"]["site1"]["gw"]["wans"]

        assert sensor.is_on is None
        assert sensor.extra_state_attributes is None

        mock_coordinator.data["devices"]["site1"]["gw"]["wans"] = None
        assert sensor.is_on is None

        mock_coordinator.data["devices"]["site1"]["gw"]["wans"] = [{"key": "wan2"}]
        assert sensor.is_on is None

    async def test_setup_entry_skips_malformed_wans(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Malformed WAN entries or a non-list WAN field create no sensors."""
        gw = mock_coordinator.data["devices"]["site1"]["gw"]
        gw["wans"] = ["wan", {"name": "No key"}, {"key": "wan", "name": "WAN"}]
        mock_coordinator.data["devices"]["site1"]["gw_other"] = {
            **gw,
            "id": "gw_other",
            "wans": {"wan": {"status": "online"}},
        }
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        wan_sensors = [
            e for e in added_entities if isinstance(e, UnifiInsightsWanLinkBinarySensor)
        ]
        assert [(e._device_id, e._wan_key) for e in wan_sensors] == [("gw", "wan")]


class TestSiteToSiteVpnBinarySensor:
    """Tests for the per-tunnel site-to-site VPN binary sensor."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create a coordinator with a switch, a gateway and two tunnels."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.protect_client = None
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {
                "site1": {
                    "sw": {
                        "id": "sw",
                        "name": "Switch",
                        "model": "USW-24-POE",
                        "state": "ONLINE",
                        "macAddress": "AA:BB:CC:DD:EE:FF",
                    },
                    "gw": {
                        "id": "gw",
                        "name": "Gateway",
                        "model": "UCG-Ultra",
                        "state": "ONLINE",
                        "macAddress": "11:22:33:44:55:66",
                    },
                },
            },
            "site_vpns": {
                "site1": {
                    "tun1": {
                        "id": "tun1",
                        "name": "Office",
                        "vpn_type": "ipsec-vpn",
                        "enabled": True,
                    },
                    "tun2": {
                        "id": "tun2",
                        "name": "Old Branch",
                        "vpn_type": "ipsec-vpn",
                        "enabled": False,
                    },
                }
            },
            "vpn_connections": {
                "site1": {
                    "tun1": {
                        "network_id": "tun1",
                        "type": "ipsec-vpn",
                        "status": "CONNECTED",
                    }
                }
            },
            "stats": {},
            "clients": {},
        }
        return coordinator

    @pytest.fixture
    def mock_config_entry(self, mock_coordinator):
        """Create mock config entry."""
        entry = MagicMock()
        entry.runtime_data = MagicMock()
        entry.runtime_data.mobility_coordinator = None
        entry.runtime_data.coordinator = mock_coordinator
        return entry

    async def _setup(self, hass, mock_config_entry) -> list:
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)
        return [
            e
            for e in added_entities
            if isinstance(e, UnifiInsightsSiteToSiteVpnBinarySensor)
        ]

    async def test_one_sensor_per_enabled_tunnel_on_the_gateway(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Each enabled tunnel gets one sensor on the gateway, never re-added."""
        sensors = await self._setup(hass, mock_config_entry)
        mock_coordinator.async_add_listener.call_args[0][0]()

        (sensor,) = sensors
        assert sensor._device_id == "gw"
        assert sensor.unique_id == "site1_gw_site_to_site_vpn_tun1"
        assert sensor.translation_key == "site_to_site_vpn"
        assert sensor.translation_placeholders == {"tunnel_name": "Office"}
        assert sensor.device_class == BinarySensorDeviceClass.CONNECTIVITY

    async def test_connected_tunnel_is_on(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """A tunnel listed as CONNECTED is on."""
        (sensor,) = await self._setup(hass, mock_config_entry)

        assert sensor.is_on is True
        assert sensor.extra_state_attributes == {
            "vpn_type": "ipsec-vpn",
            "status": "CONNECTED",
        }

    @pytest.mark.parametrize(
        "connections",
        [
            {},
            {"tun1": {"network_id": "tun1", "status": "DISCONNECTED"}},
            {"tun1": {"network_id": "tun1", "status": None}},
            {"tun1": "junk"},
        ],
    )
    async def test_missing_or_not_connected_tunnel_is_off(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry, connections
    ):
        """A tunnel absent from the list, or not CONNECTED, is off."""
        mock_coordinator.data["vpn_connections"]["site1"] = connections
        (sensor,) = await self._setup(hass, mock_config_entry)

        assert sensor.is_on is False

    @pytest.mark.parametrize("vpn_connections", [{}, {"site1": None}, None])
    async def test_unknown_when_connection_state_is_unknown(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry, vpn_connections
    ):
        """Unreadable connection state reads unknown rather than disconnected."""
        (sensor,) = await self._setup(hass, mock_config_entry)
        mock_coordinator.data["vpn_connections"] = vpn_connections

        assert sensor.is_on is None
        assert sensor.extra_state_attributes == {
            "vpn_type": "ipsec-vpn",
            "status": None,
        }

    async def test_unavailable_once_tunnel_is_removed(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """A tunnel deleted on the console makes its sensor unavailable."""
        (sensor,) = await self._setup(hass, mock_config_entry)
        assert sensor.available is True

        del mock_coordinator.data["site_vpns"]["site1"]["tun1"]
        assert sensor.available is False
        mock_coordinator.data["site_vpns"] = None
        assert sensor.available is False

    async def test_created_on_device_reporting_wans(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """An unrecognised model that reports WAN links still gets the sensors."""
        gw = mock_coordinator.data["devices"]["site1"]["gw"]
        gw["model"] = "Unknown"
        gw["wans"] = [{"key": "wan", "name": "WAN", "connected": True}]

        (sensor,) = await self._setup(hass, mock_config_entry)

        assert sensor._device_id == "gw"

    async def test_not_created_without_gateway_or_tunnels(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """No gateway, or no configured tunnels, means no sensors."""
        mock_coordinator.data["site_vpns"] = {"site1": {}}
        assert await self._setup(hass, mock_config_entry) == []

        mock_coordinator.data["site_vpns"] = {"site1": {"tun1": {"name": "Office"}}}
        del mock_coordinator.data["devices"]["site1"]["gw"]
        assert await self._setup(hass, mock_config_entry) == []

    async def test_name_falls_back_to_tunnel_id(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """A tunnel without a name is labelled by its id."""
        mock_coordinator.data["site_vpns"]["site1"]["tun1"]["name"] = None

        (sensor,) = await self._setup(hass, mock_config_entry)

        assert sensor.translation_placeholders == {"tunnel_name": "tun1"}


class TestGetSupportedSmartDetectTypes:
    """Tests for _get_supported_smart_detect_types helper."""

    def test_returns_empty_list_for_non_dict_feature_flags(self):
        """Test returns empty list when featureFlags is not a dict."""
        # featureFlags is a list instead of dict
        camera_data = {"featureFlags": ["some", "values"]}
        assert _get_supported_smart_detect_types(camera_data) == []

        # featureFlags is None
        camera_data = {"featureFlags": None}
        assert _get_supported_smart_detect_types(camera_data) == []

        # featureFlags is a string
        camera_data = {"featureFlags": "not_a_dict"}
        assert _get_supported_smart_detect_types(camera_data) == []


class TestIsSmartDetectActive:
    """Tests for _is_smart_detect_active helper edge cases."""

    def test_motion_detected_via_is_motion_detected_flag(self):
        """Test motion detection via isMotionDetected flag."""
        # Motion detected via isMotionDetected=True, person in lastSmartDetectTypes
        camera_data = {
            "featureFlags": {"smartDetectTypes": ["person"]},
            "isMotionDetected": True,  # Using flag instead of timestamps
            "isSmartDetected": False,
            "lastSmartDetectTypes": ["person"],
        }
        assert _is_smart_detect_active(camera_data, "person") is True

    def test_smart_detect_active_via_is_smart_detected_flag(self):
        """Test smart detect via isSmartDetected=True flag."""
        # isSmartDetected=True should check lastSmartDetectTypes
        camera_data = {
            "featureFlags": {"smartDetectTypes": ["person", "vehicle"]},
            "isMotionDetected": True,
            "isSmartDetected": True,  # Smart detection active
            "lastSmartDetectTypes": ["person"],
        }
        assert _is_smart_detect_active(camera_data, "person") is True
        assert _is_smart_detect_active(camera_data, "vehicle") is False

    def test_no_motion_with_is_motion_detected_false(self):
        """Test returns False when isMotionDetected is False."""
        # Motion via timestamps but isMotionDetected is False and no valid timestamps
        camera_data = {
            "featureFlags": {"smartDetectTypes": ["person"]},
            "isMotionDetected": False,
            "lastMotionStart": None,
            "lastMotionEnd": None,
            "lastSmartDetectTypes": ["person"],
        }
        assert _is_smart_detect_active(camera_data, "person") is False

    def test_detect_type_not_supported(self):
        """Test returns False when detect_type is not in supported types (line 85)."""
        # Camera only supports "person", but we're asking about "vehicle"
        camera_data = {
            "featureFlags": {"smartDetectTypes": ["person"]},  # Only person supported
            "isMotionDetected": True,
            "isSmartDetected": True,
            "lastSmartDetectTypes": ["vehicle"],
        }
        # "vehicle" is not in supported types, should return False immediately
        assert _is_smart_detect_active(camera_data, "vehicle") is False

        # Empty supported types should also return False
        camera_data_empty = {
            "featureFlags": {"smartDetectTypes": []},
            "isMotionDetected": True,
            "isSmartDetected": True,
            "lastSmartDetectTypes": ["person"],
        }
        assert _is_smart_detect_active(camera_data_empty, "person") is False


class TestProtectBinarySensorUpdateFromDataNoData:
    """Tests for _update_from_data when device_data is None."""

    def test_update_from_data_with_no_device_data(self):
        """Test _update_from_data returns early when device_data is None."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {},
            "devices": {},
            "clients": {},
            "protect": {
                "cameras": {},  # Empty - no camera data
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }

        # Create sensor with a camera that doesn't exist
        description = next(d for d in BINARY_SENSOR_TYPES if d.key == "camera_motion")

        # First add camera data to create entity
        coordinator.data["protect"]["cameras"]["cam1"] = {
            "id": "cam1",
            "name": "Test Camera",
            "state": "CONNECTED",
        }

        entity = UnifiProtectBinarySensor(
            coordinator=coordinator,
            description=description,
            device_id="cam1",
        )

        # Now remove camera data
        del coordinator.data["protect"]["cameras"]["cam1"]

        # _update_from_data should return early without error
        entity._update_from_data()

        # Verify no attributes set (early return)
        # The entity should still function without error


class TestSetupSkipsNonDoorbellCameraSensors:
    """Test that setup skips doorbell-specific sensors for regular cameras."""

    @pytest.mark.asyncio
    async def test_skips_package_detection_for_non_doorbell(self, hass: HomeAssistant):
        """Test package detection sensor is skipped for non-doorbell cameras."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {"site1": {}},  # No network devices
            "clients": {},
            "stats": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "Backyard Camera",  # Not a doorbell name
                        "type": "G4-Pro",  # Not a doorbell type
                        "state": "CONNECTED",
                        "featureFlags": {"smartDetectTypes": ["person", "package"]},
                    }
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }

        config_entry = MagicMock()
        config_entry.runtime_data = MagicMock()
        config_entry.runtime_data.mobility_coordinator = None
        config_entry.runtime_data.coordinator = coordinator

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, config_entry, add_entities)

        # Check that package detection sensor was NOT created
        package_sensors = [
            e
            for e in added_entities
            if isinstance(e, UnifiProtectBinarySensor)
            and e.entity_description.key == "camera_package_detection"
        ]
        assert len(package_sensors) == 0

        # Check that doorbell ring sensor was NOT created
        ring_sensors = [
            e
            for e in added_entities
            if isinstance(e, UnifiProtectBinarySensor)
            and e.entity_description.key == "camera_doorbell_ring"
        ]
        assert len(ring_sensors) == 0

        # But motion sensor should still be created
        motion_sensors = [
            e
            for e in added_entities
            if isinstance(e, UnifiProtectBinarySensor)
            and e.entity_description.key == "camera_motion"
        ]
        assert len(motion_sensors) == 1

        # Camera microphone sensor should also be created for non-doorbell cameras
        mic_sensors = [
            e
            for e in added_entities
            if isinstance(e, UnifiProtectBinarySensor)
            and e.entity_description.key == "camera_microphone"
        ]
        assert len(mic_sensors) == 1

    @pytest.mark.asyncio
    async def test_camera_microphone_skipped_when_has_mic_false(
        self, hass: HomeAssistant
    ):
        """Test no camera microphone sensor is created when hasMic is False."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {"site1": {}},
            "clients": {},
            "stats": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "Camera Without Mic",
                        "type": "G4-Dome",
                        "state": "CONNECTED",
                        "isMicEnabled": True,
                        "featureFlags": {"hasMic": False},
                    }
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }

        config_entry = MagicMock()
        config_entry.runtime_data = MagicMock()
        config_entry.runtime_data.mobility_coordinator = None
        config_entry.runtime_data.coordinator = coordinator

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, config_entry, add_entities)

        mic_sensors = [
            e
            for e in added_entities
            if isinstance(e, UnifiProtectBinarySensor)
            and e.entity_description.key == "camera_microphone"
        ]
        assert len(mic_sensors) == 0

    @pytest.mark.asyncio
    async def test_camera_microphone_created_when_has_mic_true(
        self, hass: HomeAssistant
    ):
        """Test camera microphone sensor is created when featureFlags.hasMic is True."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {"site1": {}},
            "clients": {},
            "stats": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "Camera With Mic",
                        "type": "G4-Pro",
                        "state": "CONNECTED",
                        "isMicEnabled": True,
                        "featureFlags": {"hasMic": True},
                    }
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }

        config_entry = MagicMock()
        config_entry.runtime_data = MagicMock()
        config_entry.runtime_data.mobility_coordinator = None
        config_entry.runtime_data.coordinator = coordinator

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, config_entry, add_entities)

        mic_sensors = [
            e
            for e in added_entities
            if isinstance(e, UnifiProtectBinarySensor)
            and e.entity_description.key == "camera_microphone"
        ]
        assert len(mic_sensors) == 1

    @pytest.mark.asyncio
    async def test_camera_microphone_created_when_has_mic_missing(
        self, hass: HomeAssistant
    ):
        """Test the camera microphone sensor is created when hasMic is missing."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {"site1": {}},
            "clients": {},
            "stats": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "Camera Missing FeatureFlags",
                        "type": "G3-Flex",
                        "state": "CONNECTED",
                        "isMicEnabled": True,
                    },
                    "cam2": {
                        "id": "cam2",
                        "name": "Camera Missing hasMic",
                        "type": "G3-Flex",
                        "state": "CONNECTED",
                        "isMicEnabled": True,
                        "featureFlags": {},
                    },
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }

        config_entry = MagicMock()
        config_entry.runtime_data = MagicMock()
        config_entry.runtime_data.mobility_coordinator = None
        config_entry.runtime_data.coordinator = coordinator

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, config_entry, add_entities)

        mic_sensors = [
            e
            for e in added_entities
            if isinstance(e, UnifiProtectBinarySensor)
            and e.entity_description.key == "camera_microphone"
        ]
        assert len(mic_sensors) == 2


class TestUnifiPortBinarySensor:
    """Tests for SFP module presence binary sensor."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create mock coordinator with SFP port data."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.protect_client = MagicMock()
        coordinator.data = {
            "sites": {"site1": {"id": "site1", "meta": {"name": "Default"}}},
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "name": "Test Switch",
                        "model": "USW-24-POE",
                        "state": "ONLINE",
                        "macAddress": "AA:BB:CC:DD:EE:FF",
                        "ipAddress": "192.168.1.10",
                        "ports": [
                            {
                                "idx": 25,
                                "state": "UP",
                                "media": "SFP+",
                                "name": "SFP+ 1",
                                "sfp_found": True,
                                "sfp_part": "UC-DAC-SFP+",
                                "sfp_vendor": "Ubiquiti Inc.",
                                "sfp_serial": "SN12345",
                                "sfp_compliance": "DAC",
                            },
                            {
                                "idx": 26,
                                "state": "DOWN",
                                "media": "SFP+",
                                "name": "SFP+ 2",
                                "sfp_found": False,
                            },
                        ],
                    },
                },
            },
            "stats": {},
            "clients": {},
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

    async def test_sfp_present_is_on(self, hass: HomeAssistant, mock_coordinator):
        """Test SFP binary sensor is on when module present."""
        sensor = UnifiPortBinarySensor(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
            port_idx=25,
            port_label="SFP+ 1",
        )
        assert sensor.is_on is True

    async def test_sfp_not_present_is_off(self, hass: HomeAssistant, mock_coordinator):
        """Test SFP binary sensor is off when no module."""
        sensor = UnifiPortBinarySensor(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
            port_idx=26,
            port_label="SFP+ 2",
        )
        assert sensor.is_on is False

    async def test_sfp_extra_attributes_when_present(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test extra state attributes for inserted SFP module."""
        sensor = UnifiPortBinarySensor(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
            port_idx=25,
            port_label="SFP+ 1",
        )
        attrs = sensor.extra_state_attributes
        assert attrs is not None
        assert attrs["module"] == "UC-DAC-SFP+"
        assert attrs["vendor"] == "Ubiquiti Inc."
        assert attrs["serial"] == "SN12345"
        assert attrs["type"] == "DAC"

    async def test_sfp_extra_attributes_when_not_present(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test no extra attributes when SFP module not present."""
        sensor = UnifiPortBinarySensor(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
            port_idx=26,
            port_label="SFP+ 2",
        )
        attrs = sensor.extra_state_attributes
        assert attrs is None

    async def test_sfp_sensor_unique_id(self, hass: HomeAssistant, mock_coordinator):
        """Test SFP binary sensor unique ID."""
        sensor = UnifiPortBinarySensor(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
            port_idx=25,
            port_label="SFP+ 1",
        )
        assert sensor.unique_id == "device1_port_sfp_present_25"

    async def test_sfp_sensor_name(self, hass: HomeAssistant, mock_coordinator):
        """Test SFP binary sensor name."""
        sensor = UnifiPortBinarySensor(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
            port_idx=25,
            port_label="SFP+ 1",
        )
        assert sensor.translation_key == "port_sfp_present"
        assert sensor.translation_placeholders == {"port_label": "SFP+ 1"}
        assert getattr(sensor, "_attr_name", None) is None

        assert sensor.entity_description.name in (None, UNDEFINED)

        sensor_fallback = UnifiPortBinarySensor(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
            port_idx=25,
            port_label="",
        )
        assert sensor_fallback.translation_placeholders == {"port_label": "Port 25"}

    async def test_sfp_no_port_data(self, hass: HomeAssistant, mock_coordinator):
        """Test SFP binary sensor with missing port data."""
        sensor = UnifiPortBinarySensor(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
            port_idx=99,
            port_label="SFP+ 99",
        )
        assert sensor.is_on is None

    async def test_sfp_no_device_data(
        self, hass: HomeAssistant, mock_coordinator
    ) -> None:
        """Test SFP binary sensor when device data is absent."""
        sensor = UnifiPortBinarySensor(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
            port_idx=25,
            port_label="SFP+ 1",
        )
        mock_coordinator.data["devices"]["site1"].pop("device1", None)
        assert sensor._find_port_data() is None
        assert sensor.is_on is None

    async def test_sfp_extra_attributes_partial_and_empty_fields(
        self, hass: HomeAssistant, mock_coordinator
    ) -> None:
        """Test extra_state_attributes when SFP fields are partially absent or falsy."""
        mock_coordinator.data["devices"]["site1"]["device1"]["ports"].append(
            {
                "idx": 27,
                "sfp_found": True,
                "sfp_part": "UF-RJ45-1G",
                "sfp_vendor": None,
                "sfp_serial": "",
            }
        )
        sensor = UnifiPortBinarySensor(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
            port_idx=27,
            port_label="SFP+ 3",
        )
        attrs = sensor.extra_state_attributes
        assert attrs == {"module": "UF-RJ45-1G"}

    async def test_setup_creates_sfp_binary_sensors(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test async_setup_entry creates SFP binary sensors for SFP ports."""
        config_entry = MagicMock()
        config_entry.runtime_data = MagicMock()
        config_entry.runtime_data.mobility_coordinator = None
        config_entry.runtime_data.coordinator = mock_coordinator

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, config_entry, add_entities)

        sfp_sensors = [
            e for e in added_entities if isinstance(e, UnifiPortBinarySensor)
        ]
        # 2 SFP ports (25 and 26) → 2 binary sensors
        assert len(sfp_sensors) == 2

    async def test_setup_skips_malformed_ports_and_dedupes(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Malformed/non-SFP/duplicate ports are skipped; only valid ones are added."""
        mock_coordinator.data["devices"]["site1"]["device1"]["ports"] = [
            "not-a-dict",  # skipped: not a dict
            {"idx": 30, "media": 123},  # skipped: media not a string
            {"idx": 31, "media": "Copper"},  # skipped: media doesn't start with SFP
            {"media": "SFP+"},  # skipped: no idx/port_idx
            {"idx": 25, "media": "SFP+", "name": "SFP+ 1"},  # added
            {"idx": 25, "media": "SFP+", "name": "SFP+ 1 dup"},  # skipped: dup key
        ]
        mock_coordinator.data["devices"]["site2"] = {
            "device2": {
                "id": "device2",
                "name": "Non-list Ports Device",
                "model": "USW-Lite-8-PoE",
                "state": "ONLINE",
                "ports": "not-a-list",
            },
        }

        config_entry = MagicMock()
        config_entry.runtime_data = MagicMock()
        config_entry.runtime_data.mobility_coordinator = None
        config_entry.runtime_data.coordinator = mock_coordinator

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, config_entry, add_entities)

        sfp_sensors = [
            e for e in added_entities if isinstance(e, UnifiPortBinarySensor)
        ]
        assert len(sfp_sensors) == 1
        assert sfp_sensors[0].unique_id == "device1_port_sfp_present_25"


class TestProtectBinarySensorCapabilityFiltering:
    """Tests for capability-based filtering of Protect binary sensor entities."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create a coordinator with USL-Entry door sensor and water leak sensor."""
        coordinator = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.last_update_success = True
        coordinator.data = {
            "sites": {"site1": {"id": "site1", "name": "Default"}},
            "devices": {"site1": {}},
            "clients": {"site1": []},
            "stats": {"site1": {}},
            "network_info": {},
            "vouchers": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {
                    "door_sensor": {
                        "id": "door_sensor",
                        "name": "Front Door",
                        "state": "CONNECTED",
                        "mountType": "door",
                        "isOpened": False,
                        "isTamperingDetected": False,
                    },
                    "leak_sensor": {
                        "id": "leak_sensor",
                        "name": "Basement Leak",
                        "state": "CONNECTED",
                        "mountType": "leak",
                        "isLeakDetected": False,
                        "isTamperingDetected": False,
                    },
                },
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
                "events": {},
            },
            "last_update": None,
        }
        return coordinator

    async def test_door_sensor_gets_door_and_tamper(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test door sensor gets door and tamper binary sensors."""
        config_entry = MagicMock()
        config_entry.runtime_data = MagicMock()
        config_entry.runtime_data.mobility_coordinator = None
        config_entry.runtime_data.coordinator = mock_coordinator

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, config_entry, add_entities)

        door_sensor_entities = [
            e
            for e in added_entities
            if isinstance(e, UnifiProtectBinarySensor) and e._device_id == "door_sensor"
        ]
        entity_keys = {e.entity_description.key for e in door_sensor_entities}
        assert "sensor_door" in entity_keys
        assert "sensor_tamper" in entity_keys

    async def test_door_sensor_no_leak_or_motion(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test door sensor does NOT get leak or motion binary sensors."""
        config_entry = MagicMock()
        config_entry.runtime_data = MagicMock()
        config_entry.runtime_data.mobility_coordinator = None
        config_entry.runtime_data.coordinator = mock_coordinator

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, config_entry, add_entities)

        door_sensor_entities = [
            e
            for e in added_entities
            if isinstance(e, UnifiProtectBinarySensor) and e._device_id == "door_sensor"
        ]
        entity_keys = {e.entity_description.key for e in door_sensor_entities}
        assert "sensor_leak" not in entity_keys
        assert "sensor_motion" not in entity_keys

    async def test_leak_sensor_gets_leak_and_tamper(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test leak sensor gets leak and tamper binary sensors."""
        config_entry = MagicMock()
        config_entry.runtime_data = MagicMock()
        config_entry.runtime_data.mobility_coordinator = None
        config_entry.runtime_data.coordinator = mock_coordinator

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, config_entry, add_entities)

        leak_sensor_entities = [
            e
            for e in added_entities
            if isinstance(e, UnifiProtectBinarySensor) and e._device_id == "leak_sensor"
        ]
        entity_keys = {e.entity_description.key for e in leak_sensor_entities}
        assert "sensor_leak" in entity_keys
        assert "sensor_tamper" in entity_keys

    async def test_leak_sensor_no_door_or_motion(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test leak sensor does NOT get door or motion binary sensors."""
        config_entry = MagicMock()
        config_entry.runtime_data = MagicMock()
        config_entry.runtime_data.mobility_coordinator = None
        config_entry.runtime_data.coordinator = mock_coordinator

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, config_entry, add_entities)

        leak_sensor_entities = [
            e
            for e in added_entities
            if isinstance(e, UnifiProtectBinarySensor) and e._device_id == "leak_sensor"
        ]
        entity_keys = {e.entity_description.key for e in leak_sensor_entities}
        assert "sensor_door" not in entity_keys
        assert "sensor_motion" not in entity_keys


@pytest.mark.parametrize("description", BINARY_SENSOR_TYPES, ids=lambda desc: desc.key)
def test_every_binary_sensor_description_preserves_effective_category(
    hass: HomeAssistant, mock_coordinator, description
):
    """Description categories replace the old forced category without overrides."""
    mock_coordinator.hass = hass
    mock_coordinator.data["devices"] = {"site1": {"device1": {"name": "Switch"}}}
    sensor = UnifiInsightsBinarySensor(
        mock_coordinator, description, "site1", "device1"
    )
    expected = (
        EntityCategory.DIAGNOSTIC
        if description.key
        in {
            "storage_degraded",
            "storage_nearly_full",
            "device_status",
            "wan_status",
            "camera_microphone",
        }
        else None
    )
    assert description.entity_category == expected
    assert sensor.entity_category == expected


@pytest.mark.parametrize(
    ("sensor_class", "args", "expected"),
    [
        (UnifiPortBinarySensor, (25, "SFP+ 25"), EntityCategory.DIAGNOSTIC),
        (UnifiInsightsWanLinkBinarySensor, ("wan1", "WAN 1"), None),
        (UnifiInsightsSiteToSiteVpnBinarySensor, ("tunnel1", "Office"), None),
    ],
)
def test_dynamic_binary_sensor_description_categories(
    hass: HomeAssistant, mock_coordinator, sensor_class, args, expected
):
    """Also lock categories for descriptions constructed per port/WAN/tunnel."""
    mock_coordinator.hass = hass
    mock_coordinator.data["devices"] = {"site1": {"device1": {"name": "Switch"}}}
    sensor = sensor_class(mock_coordinator, "site1", "device1", *args)
    assert sensor.entity_description.entity_category == expected
    assert sensor.entity_category == expected
