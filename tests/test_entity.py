# Copyright (c) 2026 Ruaan Deysel
"""Tests for UniFi Insights entity base classes."""

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import EntityDescription

from custom_components.unifi_insights.const import (
    DEVICE_TYPE_CAMERA,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_SENSOR,
    DOMAIN,
    MANUFACTURER,
)
from custom_components.unifi_insights.entity import (
    UnifiInsightsEntity,
    UnifiProtectEntity,
    build_site_device_info,
    camera_supports_ptz,
    camera_supports_speaker,
    device_has_feature,
    first_not_none,
    get_field,
    is_device_online,
    is_gateway_device,
)
from tests.fixtures.assertions import (
    patch_attribute,
    require_instance,
    require_mock,
    require_not_none,
)
from tests.fixtures.facade_data import (
    facade_data,
    frozen,
    set_availability,
    with_camera,
    with_device_stats,
    with_network_device,
    with_protect_device,
    with_protect_fields,
    with_sections,
    without_sections,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

    from custom_components.unifi_insights.coordinators.facade import FacadeData


def _without_record(
    data: Mapping[str, Any], section: str, group: str, key: str
) -> FacadeData:
    """Return ``data`` without the record at ``[section][group][key]``."""
    by_group = data[section]
    records = {
        record_key: record
        for record_key, record in by_group[group].items()
        if record_key != key
    }
    return with_sections(data, **{section: {**by_group, group: records}})


class TestGetField:
    """Tests for get_field helper function."""

    def test_get_field_first_key_found(self):
        """Test get_field returns value for first matching key."""
        data = {"key1": "value1", "key2": "value2"}
        assert get_field(data, "key1", "key2") == "value1"

    def test_get_field_second_key_found(self):
        """Test get_field returns value for second key when first missing."""
        data = {"key2": "value2"}
        assert get_field(data, "key1", "key2") == "value2"

    def test_get_field_no_keys_found(self):
        """Test get_field returns default when no keys found."""
        data = {"other": "value"}
        assert get_field(data, "key1", "key2", default="default") == "default"

    def test_get_field_none_value(self):
        """Test get_field skips None values and falls back to default."""
        data = {"key1": None}
        assert get_field(data, "key1", default="default") == "default"

    def test_get_field_no_default(self):
        """Test get_field returns None when no default specified."""
        data = {"other": "value"}
        assert get_field(data, "key1") is None

    def test_get_field_camelcase_and_snake_case(self):
        """Test get_field handles both camelCase and snake_case."""
        data_camel = {"firmwareVersion": "1.0.0"}
        data_snake = {"firmware_version": "2.0.0"}

        assert get_field(data_camel, "firmwareVersion", "firmware_version") == "1.0.0"
        assert get_field(data_snake, "firmwareVersion", "firmware_version") == "2.0.0"


class TestFirstNotNone:
    """Tests for first_not_none helper function."""

    def test_first_not_none_zero_is_returned(self):
        """A leading 0 is a legitimate value and must not be skipped."""
        assert first_not_none(0, 5) == 0

    def test_first_not_none_zero_is_not_none(self):
        """The returned 0 must be the int 0, not None (the bug under test)."""
        result = first_not_none(0, 5)
        assert result == 0
        assert result is not None

    def test_first_not_none_skips_leading_nones(self):
        """Leading None values are skipped in favor of the first real value."""
        assert first_not_none(None, None, 7) == 7

    def test_first_not_none_all_none_returns_default(self):
        """Returns the default when every candidate is None."""
        assert first_not_none(None, None, default="fallback") == "fallback"

    def test_first_not_none_all_none_no_default_returns_none(self):
        """Returns None when every candidate is None and no default given."""
        assert first_not_none(None, None) is None

    def test_first_not_none_does_not_skip_false(self):
        """first_not_none is None-aware, not truthy-aware: False survives."""
        falsy_value = False
        assert first_not_none(falsy_value, "unreachable") is False

    def test_first_not_none_does_not_skip_empty_string(self):
        """first_not_none is None-aware, not truthy-aware: "" survives."""
        assert first_not_none("", "unreachable") == ""

    def test_first_not_none_does_not_skip_empty_dict(self):
        """first_not_none is None-aware, not truthy-aware: {} survives."""
        assert first_not_none({}, "unreachable") == {}

    def test_first_not_none_single_value(self):
        """A single non-None value is returned as-is."""
        assert first_not_none(3) == 3


class TestIsDeviceOnline:
    """Tests for is_device_online helper function."""

    def test_is_device_online_state_online(self):
        """Test device is online with state ONLINE."""
        data = {"state": "ONLINE"}
        assert is_device_online(data) is True

    def test_is_device_online_state_connected(self):
        """CONNECTED is not a network device state, so it is not online (#337)."""
        data = {"state": "CONNECTED"}
        assert is_device_online(data) is False

    def test_is_device_online_state_up(self):
        """UP is not a network device state, so it is not online (#337)."""
        data = {"state": "UP"}
        assert is_device_online(data) is False

    def test_is_device_online_status_online(self):
        """Test device is online with status online."""
        data = {"status": "online"}
        assert is_device_online(data) is True

    def test_is_device_online_state_offline(self):
        """Test device is offline with state OFFLINE."""
        data = {"state": "OFFLINE"}
        assert is_device_online(data) is False

    def test_is_device_online_state_disconnected(self):
        """Test device is offline with state DISCONNECTED."""
        data = {"state": "DISCONNECTED"}
        assert is_device_online(data) is False

    def test_is_device_online_empty(self):
        """Test device is offline with empty data."""
        data = {}
        assert is_device_online(data) is False

    def test_is_device_online_lowercase(self):
        """Test device is online with lowercase status."""
        data = {"state": "online"}
        assert is_device_online(data) is True

    def test_is_device_online_non_string_state(self):
        """Test device is offline when state is not a string (returns False)."""
        # Non-string state values should return False (covers line 48)
        data_int = {"state": 123}
        assert is_device_online(data_int) is False

        data_list = {"state": ["online"]}
        assert is_device_online(data_list) is False

        data_dict = {"state": {"value": "online"}}
        assert is_device_online(data_dict) is False

        data_none = {"state": None}
        assert is_device_online(data_none) is False


class TestIsGatewayDevice:
    """Tests for the shared gateway heuristic."""

    @pytest.mark.parametrize(
        ("device", "expected"),
        [
            ({"model": "UDM-PRO"}, True),
            ({"model": "UCG-Ultra"}, True),
            ({"model": "ucg ultra"}, True),
            ({"model": "UXG-Lite"}, True),
            ({"model": "USG-3P"}, True),
            ({"model": "UDR"}, True),
            ({"model": "Cloud Gateway Max"}, True),
            ({"model": "USW-24-POE", "features": ["switching"]}, False),
            ({"model": "U6-LR", "features": ["accessPoint"]}, False),
            ({"model": "Unknown", "features": ["switching", "gateway"]}, True),
            ({"model": "Unknown", "features": {"router": True}}, True),
            # WAN data alone must not change which device groups site entities
            ({"model": "UX", "wans": [{"key": "wan1"}]}, False),
            ({"model": None}, False),
            ({}, False),
        ],
    )
    def test_is_gateway_device(self, device, expected):
        """Model prefix or a gateway/router feature identify a gateway."""
        assert is_gateway_device(device) is expected


class TestDeviceHasFeature:
    """Tests for device_has_feature helper."""

    def test_device_has_feature_list(self) -> None:
        """Test device_has_feature with feature list."""
        assert device_has_feature({"features": ["poe", "switching"]}, "poe") is True
        assert device_has_feature({"features": ["switching"]}, "poe") is False

    def test_device_has_feature_dict(self) -> None:
        """Test device_has_feature with feature dict."""
        assert device_has_feature({"features": {"poe": True}}, "poe") is True
        assert device_has_feature({"features": {"poe": False}}, "poe") is False

    def test_device_has_feature_non_container(self) -> None:
        """Test device_has_feature with non-list non-dict."""
        assert device_has_feature({"features": "poe"}, "poe") is False
        assert device_has_feature({"features": None}, "poe") is False
        assert device_has_feature({}, "poe") is False


class TestCameraSupportsPtz:
    """Tests for camera_supports_ptz helper."""

    def test_camera_supports_ptz_boolean_flags(self) -> None:
        """Test camera_supports_ptz when direct boolean flag is present."""
        assert camera_supports_ptz({"isPtz": True}) is True
        assert camera_supports_ptz({"isPtz": False}) is False
        assert camera_supports_ptz({"is_ptz": True}) is True
        assert camera_supports_ptz({"hasPtz": True}) is True

    def test_camera_supports_ptz_fallback_feature_flags(self) -> None:
        """Test camera_supports_ptz when falling back to featureFlags dict."""
        assert (
            camera_supports_ptz({"isPtz": "invalid", "featureFlags": {"hasPtz": True}})
            is True
        )
        assert (
            camera_supports_ptz(
                {"isPtz": "invalid", "feature_flags": {"has_ptz": True}}
            )
            is True
        )
        assert (
            camera_supports_ptz({"isPtz": "invalid", "featureFlags": {"hasPtz": False}})
            is False
        )

    def test_camera_supports_ptz_fallback_invalid_feature_flags(self) -> None:
        """Test camera_supports_ptz when featureFlags is not a dict."""
        assert (
            camera_supports_ptz({"isPtz": "invalid", "featureFlags": "not_a_dict"})
            is False
        )
        assert camera_supports_ptz({"isPtz": "invalid", "featureFlags": None}) is False


class TestCameraSupportsSpeaker:
    """Tests for camera_supports_speaker helper."""

    def test_camera_supports_speaker_non_dict(self) -> None:
        """Test non-dict inputs return False."""
        assert camera_supports_speaker(None) is False
        assert camera_supports_speaker("invalid") is False
        assert camera_supports_speaker(123) is False
        assert camera_supports_speaker([]) is False

    def test_camera_supports_speaker_top_level_flag(self) -> None:
        """Test top-level hasSpeaker flags."""
        assert camera_supports_speaker({"hasSpeaker": True}) is True
        assert camera_supports_speaker({"has_speaker": True}) is True
        assert camera_supports_speaker({"hasSpeaker": False}) is False
        assert camera_supports_speaker({"has_speaker": False}) is False

    def test_camera_supports_speaker_nested_flags(self) -> None:
        """Test nested featureFlags flags."""
        assert (
            camera_supports_speaker(
                {"hasSpeaker": False, "featureFlags": {"hasSpeaker": True}}
            )
            is True
        )
        assert camera_supports_speaker({"feature_flags": {"has_speaker": True}}) is True
        assert (
            camera_supports_speaker({"featureFlags": {"hasRemovableSpeaker": True}})
            is True
        )
        assert (
            camera_supports_speaker({"feature_flags": {"has_removable_speaker": True}})
            is True
        )
        assert (
            camera_supports_speaker(
                {
                    "hasSpeaker": False,
                    "featureFlags": {
                        "hasSpeaker": False,
                        "hasRemovableSpeaker": False,
                    },
                }
            )
            is False
        )

    def test_camera_supports_speaker_invalid_feature_flags(self) -> None:
        """Test featureFlags with non-dict values or empty dict."""
        assert camera_supports_speaker({}) is False
        assert camera_supports_speaker({"featureFlags": "not_a_dict"}) is False
        assert camera_supports_speaker({"featureFlags": None}) is False
        assert camera_supports_speaker({"featureFlags": []}) is False
        assert camera_supports_speaker({"hasSpeaker": "truthy"}) is False
        assert camera_supports_speaker({"hasSpeaker": 1}) is False
        assert camera_supports_speaker({"featureFlags": {"hasSpeaker": "yes"}}) is False
        assert (
            camera_supports_speaker({"featureFlags": {"hasRemovableSpeaker": 1}})
            is False
        )


class TestUnifiInsightsEntity:
    """Tests for UnifiInsightsEntity base class."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant) -> MagicMock:
        """Create mock coordinator with test data."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.available = True
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = frozen(
            {
                "sites": {
                    "site1": {"id": "site1", "meta": {"name": "Default"}},
                },
                "devices": {
                    "site1": {
                        "device1": {
                            "id": "device1",
                            "name": "Test Switch",
                            "model": "USW-24-POE",
                            "firmwareVersion": "6.5.55",
                            "macAddress": "AA:BB:CC:DD:EE:FF",
                            "ipAddress": "192.168.1.10",
                            "state": "ONLINE",
                        },
                        "device2": {
                            "id": "device2",
                            "name": "Test AP",
                            "model": "UAP-AC-PRO",
                            "state": "OFFLINE",
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
        )
        return coordinator

    async def test_entity_initialization(self, hass: HomeAssistant, mock_coordinator):
        """Test entity initializes correctly."""
        description = EntityDescription(key="test_key", name="Test Sensor")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        assert entity._site_id == "site1"
        assert entity._device_id == "device1"
        assert entity.entity_description == description

    async def test_entity_unique_id(self, hass: HomeAssistant, mock_coordinator):
        """Test entity unique ID format."""
        description = EntityDescription(key="cpu_usage", name="CPU Usage")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        assert entity.unique_id == "site1_device1_cpu_usage"

    async def test_entity_device_info(self, hass: HomeAssistant, mock_coordinator):
        """Test entity device info."""
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        device_info = entity.device_info
        assert (DOMAIN, "site1_device1") in require_not_none(device_info)["identifiers"]
        assert "manufacturer" in require_not_none(device_info)
        assert dict(require_not_none(device_info))["manufacturer"] == MANUFACTURER
        assert "model" in require_not_none(device_info)
        assert dict(require_not_none(device_info))["model"] == "USW-24-POE"
        assert "sw_version" in require_not_none(device_info)
        assert dict(require_not_none(device_info))["sw_version"] == "6.5.55"

    async def test_entity_available_online(self, hass: HomeAssistant, mock_coordinator):
        """Test entity is available when device is online."""
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        assert entity.available is True

    @pytest.mark.parametrize(
        ("devices", "expected"),
        [
            pytest.param(None, False, id="devices-none"),
            pytest.param([], False, id="devices-list"),
            pytest.param("invalid", False, id="devices-string"),
            pytest.param(0, False, id="devices-number"),
            pytest.param({"site1": None}, False, id="site-none"),
            pytest.param({"site1": []}, False, id="site-list"),
            pytest.param({"site1": "invalid"}, False, id="site-string"),
            pytest.param({"site1": 0}, False, id="site-number"),
            pytest.param(
                {"site1": {"device1": {"state": "ONLINE"}}}, True, id="online"
            ),
        ],
    )
    async def test_entity_available_validates_device_containers(
        self, mock_coordinator: MagicMock, devices: Any, *, expected: bool
    ) -> None:
        """Malformed containers are unavailable while valid online data works."""
        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator,
            description=EntityDescription(key="test"),
            site_id="site1",
            device_id="device1",
        )
        assert entity.available is True

        mock_coordinator.data = with_sections(mock_coordinator.data, devices=devices)

        assert entity.available is expected

    async def test_entity_available_offline(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test entity is unavailable when device is offline."""
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device2",  # This device is OFFLINE
        )

        assert entity.available is False

    async def test_entity_available_coordinator_unavailable(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Entity unavailable when coordinator stale, even w/ cached ONLINE data."""
        set_availability(mock_coordinator, device=False)
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",  # cached state is ONLINE
        )

        assert entity.available is False

    async def test_entity_available_decoupled_from_protect_failure(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Network entity remains available when Protect coordinator fails."""
        set_availability(mock_coordinator, device=True, protect=False)
        mock_coordinator.available = False
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        assert entity.available is True

    async def test_entity_handle_coordinator_update_unavailable_then_recovers(
        self, hass: HomeAssistant, mock_coordinator, monkeypatch
    ):
        """Entity un-freezes on _handle_coordinator_update once coordinator recovers."""
        description = EntityDescription(key="test", name="Test")
        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",  # cached state is ONLINE
        )
        patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())

        set_availability(mock_coordinator, device=False)
        entity._handle_coordinator_update()
        assert entity.available is False

        set_availability(mock_coordinator, device=True)
        entity._handle_coordinator_update()
        assert entity.available is True

    async def test_entity_device_data(self, hass: HomeAssistant, mock_coordinator):
        """Test entity device_data property."""
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        device_data = entity.device_data
        assert require_not_none(device_data)["id"] == "device1"
        assert require_not_none(device_data)["name"] == "Test Switch"

    async def test_entity_device_stats(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test entity device_stats property."""
        mock_coordinator.data = with_device_stats(
            mock_coordinator.data, "site1", "device1", {"cpuUtilizationPct": 25.5}
        )
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        stats = entity.device_stats
        assert require_not_none(stats)["cpuUtilizationPct"] == 25.5

    async def test_entity_mac_connection(self, hass: HomeAssistant, mock_coordinator):
        """Test entity includes MAC address connection."""
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        device_info = entity.device_info
        assert "connections" in require_not_none(device_info)
        connections = require_instance(
            dict(require_not_none(device_info))["connections"], set
        )
        assert (CONNECTION_NETWORK_MAC, "AA:BB:CC:DD:EE:FF") in connections


class TestUnifiProtectEntity:
    """Tests for UnifiProtectEntity base class."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant) -> MagicMock:
        """Create mock coordinator with protect data."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.available = True
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.data = frozen(
            {
                "sites": {},
                "devices": {
                    "site1": {
                        "device1": {
                            "id": "device1",
                            "name": "UDM Pro",
                            "macAddress": "AA:BB:CC:DD:EE:FF",
                            "model": "UDM-PRO",
                            "firmwareVersion": "2.0.0",
                            "ipAddress": "192.168.1.1",
                            "state": "ONLINE",
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
                            "mac": "AA:BB:CC:DD:EE:FF",
                            "type": "UVC-G4-PRO",
                            "firmwareVersion": "4.50.0",
                        },
                        "camera2": {
                            "id": "camera2",
                            "name": "Backyard",
                            "state": "DISCONNECTED",
                            "type": "UVC-G3-FLEX",
                        },
                    },
                    "lights": {
                        "light1": {
                            "id": "light1",
                            "name": "Garage Light",
                            "state": "CONNECTED",
                            "type": "UP-FLOODLIGHT",
                        },
                    },
                    "sensors": {
                        "sensor1": {
                            "id": "sensor1",
                            "name": "Front Door Sensor",
                            "state": "CONNECTED",
                            "type": "UP-SENSE",
                        },
                    },
                    "nvrs": {},
                    "viewers": {},
                    "chimes": {},
                    "liveviews": {},
                },
            }
        )
        return coordinator

    async def test_protect_entity_initialization(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test protect entity initializes correctly."""
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera1",
        )

        assert entity._device_type == DEVICE_TYPE_CAMERA
        assert entity._device_id == "camera1"

    async def test_protect_entity_unique_id(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test protect entity unique ID format."""
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera1",
        )

        assert entity.unique_id == f"{DOMAIN}_camera_camera1"

    async def test_protect_entity_unique_id_with_type(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test protect entity unique ID with entity type."""
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera1",
            entity_type="motion",
        )

        assert entity.unique_id == f"{DOMAIN}_camera_camera1_motion"

    async def test_protect_entity_available_connected(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test protect entity is available when connected."""
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera1",
        )

        assert entity.available is True

    async def test_protect_entity_available_disconnected(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test protect entity is unavailable when disconnected."""
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera2",
        )

        assert entity.available is False

    async def test_protect_entity_available_coordinator_unavailable(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Protect entity unavailable when coordinator stale (mirrors 47h incident)."""
        set_availability(mock_coordinator, protect=False)

        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera1",  # cached state is CONNECTED
        )

        assert entity.available is False

    async def test_protect_entity_available_decoupled_from_network_failure(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Protect entity remains available when Network device coordinator fails."""
        set_availability(mock_coordinator, device=False, protect=True)
        mock_coordinator.available = False

        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera1",  # cached state is CONNECTED
        )

        assert entity.available is True

    async def test_protect_entity_handle_coordinator_update_unavailable_then_recovers(
        self, hass: HomeAssistant, mock_coordinator, monkeypatch
    ):
        """Protect entity un-freezes on coordinator update once coordinator recovers."""
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera1",  # cached state is CONNECTED
        )
        patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())
        entity._update_from_data = MagicMock()

        set_availability(mock_coordinator, protect=False)
        entity._handle_coordinator_update()
        assert entity.available is False

        set_availability(mock_coordinator, protect=True)
        entity._handle_coordinator_update()
        assert entity.available is True

    async def test_protect_entity_coordinator_update_refreshes_state(
        self, hass: HomeAssistant, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test coordinator updates refresh Protect entity state."""
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera1",
        )
        patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())
        entity._update_from_data = MagicMock()

        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "cameras", "camera1", state="DISCONNECTED"
        )

        entity._handle_coordinator_update()

        assert entity.available is False
        entity._update_from_data.assert_called_once()
        require_mock(entity.async_write_ha_state).assert_called_once()

    async def test_protect_entity_coordinator_update_handles_missing_device(
        self, hass: HomeAssistant, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test coordinator updates mark missing Protect devices unavailable."""
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera1",
        )
        patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())
        entity._update_from_data = MagicMock()

        mock_coordinator.data = _without_record(
            mock_coordinator.data, "protect", "cameras", "camera1"
        )

        entity._handle_coordinator_update()

        assert entity.available is False
        entity._update_from_data.assert_not_called()
        require_mock(entity.async_write_ha_state).assert_called_once()

    async def test_protect_entity_device_data(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test protect entity device_data property."""
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera1",
        )

        device_data = entity.device_data
        assert require_not_none(device_data)["id"] == "camera1"
        assert require_not_none(device_data)["name"] == "Front Door"

    async def test_protect_entity_device_info(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test protect entity device info."""
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera1",
        )

        device_info = entity.device_info
        assert "manufacturer" in require_not_none(device_info)
        assert dict(require_not_none(device_info))["manufacturer"] == MANUFACTURER

    async def test_protect_entity_light_type(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test protect entity for light type."""
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_LIGHT,
            device_id="light1",
        )

        assert entity._device_type == DEVICE_TYPE_LIGHT
        assert entity.available is True

    async def test_protect_entity_sensor_type(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test protect entity for sensor type."""
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_SENSOR,
            device_id="sensor1",
        )

        assert entity._device_type == DEVICE_TYPE_SENSOR
        assert entity.available is True

    async def test_protect_entity_network_device_matching(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test protect entity matches network device by MAC address."""
        # Camera with MAC that matches a network device
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera1",  # Has MAC AA:BB:CC:DD:EE:FF matching device1
        )

        # Should use network device identifiers
        device_info = entity.device_info
        identifiers = require_not_none(device_info).get("identifiers", set())
        # Should match network device site1_device1
        assert (DOMAIN, "site1_device1") in identifiers

    async def test_protect_entity_nvr_network_device_matching(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test protect NVR entity matches network device by MAC (UDM-Pro fix)."""
        # Add NVR with MAC that matches a network device (like UDM-Pro)
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "nvrs",
            "nvr1",
            {
                "id": "nvr1",
                "name": "UDM-Pro NVR",
                "state": "CONNECTED",
                "mac": "AA:BB:CC:DD:EE:FF",  # Same MAC as device1
            },
        )

        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type="nvr",
            device_id="nvr1",
        )

        # Should use network device identifiers - both NVR and network device
        # entities should appear under the same device in HA
        device_info = entity.device_info
        identifiers = require_not_none(device_info).get("identifiers", set())
        # Should match network device site1_device1
        assert (DOMAIN, "site1_device1") in identifiers

    async def test_protect_entity_no_network_match(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test protect entity without matching network device."""
        # Camera2 has no MAC address
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera2",
        )

        # Should create its own device entry
        device_info = entity.device_info
        identifiers = require_not_none(device_info).get("identifiers", set())
        assert (DOMAIN, "protect_camera_camera2") in identifiers

    async def test_protect_entity_dual_camera_parent(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test protect entity handles dual-camera parent ID."""
        # Add a camera with _parent_camera_id
        mock_coordinator.data = with_camera(
            mock_coordinator.data,
            "camera_pkg",
            {
                "id": "camera_pkg",
                "name": "Front Door Package Camera",
                "state": "CONNECTED",
                "type": "UVC-G4-DOORBELL",
                "_parent_camera_id": "camera_main",
                "_is_package_camera": True,
            },
        )
        mock_coordinator.data = with_camera(
            mock_coordinator.data,
            "camera_main",
            {
                "id": "camera_main",
                "name": "Front Door Main Camera",
                "state": "CONNECTED",
                "type": "UVC-G4-DOORBELL",
                "mac": "11:22:33:44:55:66",
            },
        )

        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera_pkg",
        )

        # Should use parent camera ID in device identifiers
        device_info = entity.device_info
        identifiers = require_not_none(device_info).get("identifiers", set())
        # Uses parent ID for grouping
        assert (DOMAIN, "protect_camera_camera_main") in identifiers

    async def test_protect_entity_dual_camera_package_name_stripping(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test protect entity strips Package Camera suffix (line 211)."""
        # Add a package camera with " Package Camera" suffix but NO " Main Camera"
        # This specifically tests the elif branch at line 211
        mock_coordinator.data = with_camera(
            mock_coordinator.data,
            "camera_pkg2",
            {
                "id": "camera_pkg2",
                # Has " Package Camera" but NOT " Main Camera"
                "name": "Garage Package Camera",
                "state": "CONNECTED",
                "type": "UVC-G4-PRO",
                # Has parent so stripping logic runs
                "_parent_camera_id": "camera_garage_main",
                "_is_package_camera": True,
            },
        )
        # Add the parent camera
        mock_coordinator.data = with_camera(
            mock_coordinator.data,
            "camera_garage_main",
            {
                "id": "camera_garage_main",
                "name": "Garage",
                "state": "CONNECTED",
                "type": "UVC-G4-PRO",
            },
        )

        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera_pkg2",
        )

        # The device name should be stripped of " Package Camera" suffix
        # device_info.name should be "Garage" not "Garage Package Camera"
        device_info = entity.device_info
        assert "Package Camera" not in require_not_none(
            require_not_none(device_info).get("name", "")
        )

    async def test_protect_entity_dual_camera_main_name_stripping(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test protect entity strips Main Camera suffix (line 209)."""
        # Add a main camera with " Main Camera" suffix AND _parent_camera_id
        # This specifically tests the if branch at line 209 (when the entity
        # itself has " Main Camera" in its name but is referencing a parent)
        mock_coordinator.data = with_camera(
            mock_coordinator.data,
            "camera_main_sub",
            {
                "id": "camera_main_sub",
                "name": "Backyard Main Camera",  # Has " Main Camera"
                "state": "CONNECTED",
                "type": "UVC-G4-PRO",
                "_parent_camera_id": "camera_backyard_parent",  # Has parent ID
            },
        )
        # Add the parent camera
        mock_coordinator.data = with_camera(
            mock_coordinator.data,
            "camera_backyard_parent",
            {
                "id": "camera_backyard_parent",
                "name": "Backyard",
                "state": "CONNECTED",
                "type": "UVC-G4-PRO",
            },
        )

        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera_main_sub",
        )

        # The device name should be stripped of " Main Camera" suffix
        # device_info.name should be "Backyard" not "Backyard Main Camera"
        device_info = entity.device_info
        assert "Main Camera" not in require_not_none(
            require_not_none(device_info).get("name", "")
        )
        assert require_not_none(device_info).get("name") == "Backyard"

    async def test_protect_entity_available_non_dict(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test protect entity available with non-dict device data."""
        # Create entity with valid data first
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera1",
        )

        # Now set device data to non-dict
        mock_coordinator.data = with_camera(
            mock_coordinator.data, "camera1", "not a dict"
        )

        assert entity.available is False

    async def test_protect_entity_device_data_non_dict(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test protect entity device_data with non-dict value."""
        # Set device data to non-dict
        mock_coordinator.data = with_camera(
            mock_coordinator.data, "camera_test", "not a dict"
        )

        # Need to create entity with valid data first
        mock_coordinator.data = with_camera(
            mock_coordinator.data,
            "camera_test",
            {
                "id": "camera_test",
                "name": "Test",
                "state": "CONNECTED",
                "type": "UVC-G4",
            },
        )
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera_test",
        )

        # Now change to non-dict
        mock_coordinator.data = with_camera(
            mock_coordinator.data, "camera_test", "not a dict"
        )

        assert entity.device_data is None

    async def test_protect_entity_suggested_area_camera(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test protect camera entity gets Security suggested area."""
        # Camera without network device match
        mock_coordinator.data = with_camera(
            mock_coordinator.data,
            "camera_new",
            {
                "id": "camera_new",
                "name": "New Camera",
                "state": "CONNECTED",
                "type": "UVC-G4-PRO",
            },
        )

        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="camera_new",
        )

        device_info = entity.device_info
        assert require_not_none(device_info).get("suggested_area") == "Security"

    async def test_protect_entity_suggested_area_light(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test protect light entity gets Exterior suggested area."""
        # Light without network device match
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_LIGHT,
            device_id="light1",
        )

        device_info = entity.device_info
        assert require_not_none(device_info).get("suggested_area") == "Exterior"

    async def test_protect_entity_suggested_area_sensor(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test protect sensor entity gets Security suggested area."""
        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_SENSOR,
            device_id="sensor1",
        )

        device_info = entity.device_info
        assert require_not_none(device_info).get("suggested_area") == "Security"

    async def test_protect_entity_empty_mac_in_lookup(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test protect entity with empty mac address does not match network device."""
        mock_coordinator.data = with_camera(
            mock_coordinator.data,
            "cam_empty_mac",
            {
                "id": "cam_empty_mac",
                "name": "No MAC Cam",
                "state": "CONNECTED",
                "mac": None,
            },
        )

        entity = UnifiProtectEntity(
            coordinator=mock_coordinator,
            device_type=DEVICE_TYPE_CAMERA,
            device_id="cam_empty_mac",
        )

        device_info = entity.device_info
        identifiers = require_not_none(device_info).get("identifiers", set())
        assert (DOMAIN, "site1_device1") not in identifiers


class TestUnifiInsightsEntityEdgeCases:
    """Tests for edge cases in UnifiInsightsEntity."""

    @pytest.fixture
    def mock_coordinator_with_ports(self, hass: HomeAssistant) -> MagicMock:
        """Create mock coordinator with port and radio data."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = frozen(
            {
                "sites": {"site1": {"id": "site1"}},
                "devices": {
                    "site1": {
                        "switch1": {
                            "id": "switch1",
                            "name": "Main Switch",
                            "model": "USW-24-POE",
                            "state": "ONLINE",
                            "port_table": [
                                {"port_idx": 1, "name": "Port 1"},
                                {"port_idx": 2, "name": "Port 2"},
                            ],
                        },
                        "ap1": {
                            "id": "ap1",
                            "name": "Access Point",
                            "model": "UAP-AC-PRO",
                            "state": "ONLINE",
                            "radio_table": [
                                {"name": "ra0", "radio": "ng"},
                                {"name": "rai0", "radio": "na"},
                            ],
                        },
                        "device_minimal": {
                            "id": "device_minimal",
                            "model": "Unknown",
                            "state": "ONLINE",
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
        )
        return coordinator

    async def test_entity_with_port_table(
        self, hass: HomeAssistant, mock_coordinator_with_ports
    ):
        """Test entity with port_table gets hw_version."""
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator_with_ports,
            description=description,
            site_id="site1",
            device_id="switch1",
        )

        device_info = entity.device_info
        assert "hw_version" in require_not_none(device_info)
        assert "2 Ports" in require_not_none(
            require_instance(dict(require_not_none(device_info))["hw_version"], str)
        )

    async def test_entity_with_radio_table(
        self, hass: HomeAssistant, mock_coordinator_with_ports
    ):
        """Test entity with radio_table gets hw_version."""
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator_with_ports,
            description=description,
            site_id="site1",
            device_id="ap1",
        )

        device_info = entity.device_info
        assert "hw_version" in require_not_none(device_info)
        assert "ra0 (ng)" in require_not_none(
            require_instance(dict(require_not_none(device_info))["hw_version"], str)
        )
        assert "rai0 (na)" in require_not_none(
            require_instance(dict(require_not_none(device_info))["hw_version"], str)
        )

    async def test_entity_without_name(
        self, hass: HomeAssistant, mock_coordinator_with_ports
    ):
        """Test entity without device name uses fallback."""
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator_with_ports,
            description=description,
            site_id="site1",
            device_id="device_minimal",
        )

        device_info = entity.device_info
        # Should have a name (either from device or fallback)
        assert "name" in require_not_none(device_info)

    async def test_entity_device_data_missing(
        self, hass: HomeAssistant, mock_coordinator_with_ports: MagicMock
    ) -> None:
        """Test entity device_data when device is missing."""
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator_with_ports,
            description=description,
            site_id="site1",
            device_id="switch1",
        )

        # Remove device from data
        mock_coordinator_with_ports.data = _without_record(
            mock_coordinator_with_ports.data, "devices", "site1", "switch1"
        )

        assert entity.device_data is None

    async def test_entity_device_stats_non_dict(
        self, hass: HomeAssistant, mock_coordinator_with_ports: MagicMock
    ) -> None:
        """Test entity device_stats returns None for non-dict."""
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator_with_ports,
            description=description,
            site_id="site1",
            device_id="switch1",
        )

        # Set stats to non-dict
        mock_coordinator_with_ports.data = with_device_stats(
            mock_coordinator_with_ports.data, "site1", "switch1", "not a dict"
        )

        assert entity.device_stats is None

    async def test_entity_available_missing_device(
        self, hass: HomeAssistant, mock_coordinator_with_ports: MagicMock
    ) -> None:
        """Test entity available when device is missing."""
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator_with_ports,
            description=description,
            site_id="site1",
            device_id="switch1",
        )

        # Remove device from data
        mock_coordinator_with_ports.data = _without_record(
            mock_coordinator_with_ports.data, "devices", "site1", "switch1"
        )

        assert entity.available is False

    async def test_entity_handle_coordinator_update(
        self, hass: HomeAssistant, mock_coordinator_with_ports, monkeypatch
    ):
        """Test entity _handle_coordinator_update."""
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator_with_ports,
            description=description,
            site_id="site1",
            device_id="switch1",
        )

        # Mock async_write_ha_state
        patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())

        # Call update handler
        entity._handle_coordinator_update()

        # Should have called async_write_ha_state
        require_mock(entity.async_write_ha_state).assert_called()

    async def test_entity_handle_coordinator_update_missing_device(
        self, hass: HomeAssistant, mock_coordinator_with_ports: MagicMock, monkeypatch
    ) -> None:
        """Test entity _handle_coordinator_update with missing device."""
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator_with_ports,
            description=description,
            site_id="site1",
            device_id="switch1",
        )

        # Mock async_write_ha_state
        patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())

        # Remove device from data
        mock_coordinator_with_ports.data = _without_record(
            mock_coordinator_with_ports.data, "devices", "site1", "switch1"
        )

        # Call update handler
        entity._handle_coordinator_update()

        # Should set available to False
        assert entity.available is False
        require_mock(entity.async_write_ha_state).assert_called()

    async def test_entity_suggested_area_switch(
        self, hass: HomeAssistant, mock_coordinator_with_ports
    ):
        """Test entity with switch model gets Network suggested area."""
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator_with_ports,
            description=description,
            site_id="site1",
            device_id="switch1",
        )

        device_info = entity.device_info
        assert require_not_none(device_info).get("suggested_area") == "Network"

    async def test_entity_suggested_area_ap(
        self, hass: HomeAssistant, mock_coordinator_with_ports
    ):
        """Test entity with AP model gets Network suggested area."""
        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator_with_ports,
            description=description,
            site_id="site1",
            device_id="ap1",
        )

        device_info = entity.device_info
        assert require_not_none(device_info).get("suggested_area") == "Network"

    async def test_entity_empty_port_table(
        self, hass: HomeAssistant, mock_coordinator_with_ports: MagicMock
    ) -> None:
        """Test entity with empty port_table."""
        # Add device with empty port_table
        mock_coordinator_with_ports.data = with_network_device(
            mock_coordinator_with_ports.data,
            "site1",
            "switch_empty",
            {
                "id": "switch_empty",
                "name": "Empty Switch",
                "model": "USW",
                "state": "ONLINE",
                "port_table": [],
            },
        )

        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator_with_ports,
            description=description,
            site_id="site1",
            device_id="switch_empty",
        )

        device_info = entity.device_info
        # hw_version should not include ports if empty
        hw_version = require_not_none(device_info).get("hw_version", "")
        assert "Ports" not in require_not_none(hw_version)

    async def test_entity_radio_table_non_dict_entry(
        self, hass: HomeAssistant, mock_coordinator_with_ports: MagicMock
    ) -> None:
        """Test entity handles non-dict entries in radio_table."""
        # Add device with mixed radio_table
        mock_coordinator_with_ports.data = with_network_device(
            mock_coordinator_with_ports.data,
            "site1",
            "ap_mixed",
            {
                "id": "ap_mixed",
                "name": "Mixed AP",
                "model": "UAP",
                "state": "ONLINE",
                "radio_table": [
                    {"name": "ra0", "radio": "ng"},
                    "not a dict",  # Invalid entry
                    None,  # Another invalid entry
                ],
            },
        )

        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator_with_ports,
            description=description,
            site_id="site1",
            device_id="ap_mixed",
        )

        device_info = entity.device_info
        hw_version = require_not_none(device_info).get("hw_version", "")
        # Should only include valid radio entry
        assert "ra0 (ng)" in require_not_none(hw_version)

    async def test_entity_radio_table_missing_name_or_type(
        self, hass: HomeAssistant, mock_coordinator_with_ports: MagicMock
    ) -> None:
        """Test entity handles radio table with missing name or type."""
        mock_coordinator_with_ports.data = with_network_device(
            mock_coordinator_with_ports.data,
            "site1",
            "ap_empty_radio",
            {
                "id": "ap_empty_radio",
                "name": "Empty Radio AP",
                "model": "UAP",
                "state": "ONLINE",
                "radio_table": [
                    {"name": "", "radio": "ng"},
                    {"name": "ra0", "radio": ""},
                    {"name": "ra1", "radio": "na"},
                ],
            },
        )

        description = EntityDescription(key="test", name="Test")

        entity = UnifiInsightsEntity(
            coordinator=mock_coordinator_with_ports,
            description=description,
            site_id="site1",
            device_id="ap_empty_radio",
        )

        device_info = entity.device_info
        hw_version = require_not_none(device_info).get("hw_version", "")
        assert hw_version == "ra1 (na)"


@pytest.mark.parametrize(
    ("state", "healthy", "expected"),
    [
        ("GETTING_READY", True, True),
        ("GETTING_READY", False, False),
        ("ONLINE", False, False),
        ("OFFLINE", True, False),
        ("UPDATING", True, False),
        ("ADOPTING", True, False),
    ],
)
def test_network_serviceable_availability(
    state: str, *, healthy: bool, expected: bool
) -> None:
    """Base entities need fresh Device data and a serviceable device."""
    coordinator = MagicMock()
    coordinator.network_client.base_url = "https://example.test"
    coordinator.data = frozen(
        {
            "devices": {"site": {"pdu": {"state": state, "model": "USP-PDU-Pro"}}},
            "stats": {},
        }
    )
    set_availability(coordinator, device=healthy)
    entity = UnifiInsightsEntity(
        coordinator, EntityDescription(key="test"), "site", "pdu"
    )
    assert entity.available is expected


# --- Snapshot lookups --------------------------------------------------------

_SWITCH = {"id": "sw1", "name": "Switch", "model": "USW-24", "state": "ONLINE"}
_CAMERA = {"id": "cam1", "name": "Door", "type": "UVC-G4", "state": "CONNECTED"}


def _snapshot_coordinator(data: Any) -> MagicMock:
    """Return a coordinator double that publishes ``data`` as its snapshot."""
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.network_client.base_url = "https://192.168.1.1"
    coordinator.protect_client = None
    return coordinator


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(
            lambda data: without_sections(data, "devices", "stats"), id="no-section"
        ),
        pytest.param(
            lambda data: with_sections(data, devices={}, stats={}), id="no-site"
        ),
        pytest.param(
            lambda data: with_sections(
                data, devices={"site1": {}}, stats={"site1": {}}
            ),
            id="no-record",
        ),
        pytest.param(
            lambda data: with_sections(data, devices=[], stats="invalid"),
            id="malformed-section",
        ),
    ],
)
def test_network_entity_reads_missing_records_as_none(change: Any) -> None:
    """A missing or malformed device or stats record reads as None."""
    data = with_network_device(
        facade_data(), "site1", "sw1", _SWITCH, stats={"cpuUtilizationPct": 5}
    )
    coordinator = _snapshot_coordinator(data)
    entity = UnifiInsightsEntity(
        coordinator, EntityDescription(key="t"), "site1", "sw1"
    )
    assert entity.device_data == _SWITCH
    assert entity.device_stats == {"cpuUtilizationPct": 5}
    assert entity.available is True

    coordinator.data = change(data)

    assert entity.device_data is None
    assert entity.device_stats is None
    assert entity.available is False


def test_network_entity_needs_its_device_record() -> None:
    """A Network entity is only built for a device the snapshot holds."""
    coordinator = _snapshot_coordinator(facade_data())

    with pytest.raises(KeyError, match="sw1"):
        UnifiInsightsEntity(coordinator, EntityDescription(key="t"), "site1", "sw1")


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(without_sections(facade_data(), "protect"), id="no-section"),
        pytest.param(facade_data(protect={}), id="no-collection"),
        pytest.param(facade_data(), id="no-record"),
        pytest.param(facade_data(protect={"cameras": []}), id="malformed-section"),
    ],
)
def test_protect_entity_reads_missing_records_as_none(data: Any) -> None:
    """A missing or malformed Protect record reads as None and unavailable."""
    coordinator = _snapshot_coordinator(with_camera(facade_data(), "cam1", _CAMERA))
    entity = UnifiProtectEntity(coordinator, DEVICE_TYPE_CAMERA, "cam1")
    assert entity.device_data == _CAMERA
    assert entity.available is True

    coordinator.data = data

    assert entity.device_data is None
    assert entity.available is False


def test_protect_entity_without_its_record_uses_defaults() -> None:
    """A Protect entity built without its record gets the default device."""
    coordinator = _snapshot_coordinator(facade_data())

    entity = UnifiProtectEntity(coordinator, DEVICE_TYPE_CAMERA, "cam1")

    assert entity.device_info is not None
    assert entity.device_info["identifiers"] == {(DOMAIN, "protect_camera_cam1")}
    assert entity.device_info["name"] == "UniFi Camera cam1"
    assert "model" in entity.device_info
    assert entity.device_info["model"] == "UniFi Camera"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        pytest.param(
            without_sections(facade_data(), "sites"), "site1", id="no-section"
        ),
        pytest.param(facade_data(), "site1", id="no-site"),
        pytest.param(facade_data(sites=[]), "site1", id="malformed-section"),
        pytest.param(
            facade_data(sites={"site1": "invalid"}), "site1", id="malformed-site"
        ),
        pytest.param(
            facade_data(sites={"site1": {"name": "Home", "meta": "invalid"}}),
            "Home",
            id="malformed-meta",
        ),
        pytest.param(
            facade_data(sites={"site1": {"name": "Home", "meta": {"name": "Main"}}}),
            "Main",
            id="meta-name",
        ),
    ],
)
def test_site_device_info_reads_the_site_record(data: Any, expected: str) -> None:
    """The site device is named from its site record, else from the site ID."""
    info = build_site_device_info(data, "site1")

    assert info["identifiers"] == {(DOMAIN, "site_site1")}
    assert info["name"] == f"UniFi Site ({expected})"
