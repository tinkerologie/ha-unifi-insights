# Copyright 2026 UniFi Insights contributors
"""Tests for leak detection via featureFlags.waterLeak (USL-Environmental)."""

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from custom_components.unifi_insights.binary_sensor import (
    UnifiProtectBinarySensor,
    _is_external_leak_detected,
    _is_internal_leak_detected,
    _supports_external_leak,
    _supports_internal_leak,
    _water_leak_channel_count,
    async_setup_entry,
)
from tests.fixtures.assertions import require_not_none
from tests.fixtures.entities import EntityCollector
from tests.fixtures.facade_data import (
    with_protect_fields,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# Trimmed real-world public API payload of a USL-Environmental-EU sensor:
# no `isLeakDetected` field, mountType "none", leak support only advertised
# via featureFlags.waterLeak (2 channels: internal contacts + external probe).
USL_ENVIRONMENTAL = {
    "id": "env_sensor",
    "name": "USL-Wohnzimmer",
    "modelKey": "sensor",
    "type": "USL-Environmental-EU",
    "state": "CONNECTED",
    "mountType": "none",
    "isMotionDetected": False,
    "featureFlags": {
        "temperature": {"channelCount": 1},
        "humidity": {"channelCount": 1},
        "light": {"channelCount": 1},
        "waterLeak": {"channelCount": 2},
    },
    "leakDetectedAt": None,
    "externalLeakDetectedAt": None,
    "leakSettings": {"isInternalEnabled": True, "isExternalEnabled": True},
    "tamperingDetectedAt": None,
}


class TestWaterLeakHelpers:
    """Unit tests for the leak capability/state helpers."""

    def test_channel_count(self):
        """Channel count is read from featureFlags.waterLeak."""
        assert _water_leak_channel_count(USL_ENVIRONMENTAL) == 2
        assert (
            _water_leak_channel_count(
                {"feature_flags": {"water_leak": {"channel_count": "2"}}}
            )
            == 2
        )
        assert _water_leak_channel_count({}) == 0
        assert _water_leak_channel_count({"featureFlags": None}) == 0
        assert _water_leak_channel_count({"featureFlags": {"waterLeak": {}}}) == 0
        assert _water_leak_channel_count({"featureFlags": {"waterLeak": True}}) == 0
        assert (
            _water_leak_channel_count(
                {"featureFlags": {"waterLeak": {"channelCount": True}}}
            )
            == 0
        )
        assert (
            _water_leak_channel_count(
                {"featureFlags": {"waterLeak": {"channelCount": "bogus"}}}
            )
            == 0
        )

    def test_environmental_supports_both_channels(self):
        """USL-Environmental gets internal and external leak capability."""
        assert _supports_internal_leak(USL_ENVIRONMENTAL) is True
        assert _supports_external_leak(USL_ENVIRONMENTAL) is True
        assert _supports_external_leak({"isExternalLeakDetected": False}) is True

    def test_single_channel_has_no_external(self):
        """A single leak channel does not create an external probe entity."""
        data = {"featureFlags": {"waterLeak": {"channelCount": 1}}}
        assert _supports_internal_leak(data) is True
        assert _supports_external_leak(data) is False

    def test_legacy_detection_still_works(self):
        """MountType leak and explicit isLeakDetected still enable the entity."""
        assert _supports_internal_leak({"mountType": "leak"}) is True
        assert _supports_internal_leak({"isLeakDetected": False}) is True
        assert _supports_internal_leak({"mountType": "door"}) is False

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            ({"leakDetectedAt": None}, False),
            ({"leakDetectedAt": 1758600000000}, True),
            ({"isLeakDetected": True, "leakDetectedAt": None}, True),
            ({"isLeakDetected": False, "leakDetectedAt": 1758600000000}, False),
            ({}, False),
        ],
    )
    def test_internal_state(self, data, expected):
        """Internal state prefers explicit flag, else the timestamp."""
        assert _is_internal_leak_detected(data) is expected

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            ({"externalLeakDetectedAt": None}, False),
            ({"externalLeakDetectedAt": 1758600000000}, True),
            ({"leakDetectedAt": 1758600000000}, False),
            ({"isExternalLeakDetected": True}, True),
            (
                {"isExternalLeakDetected": False, "externalLeakDetectedAt": 1},
                False,
            ),
        ],
    )
    def test_external_state(self, data, expected):
        """External state follows externalLeakDetectedAt only."""
        assert _is_external_leak_detected(data) is expected


def _publish_sensor_fields(coordinator: MagicMock, **fields: Any) -> None:
    """Publish a snapshot with fields of the Environmental sensor replaced."""
    coordinator.data = with_protect_fields(
        coordinator.data, "sensors", "env_sensor", **fields
    )


class TestWaterLeakEntityCreation:
    """Integration-style test: entities are created for the Environmental sensor."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Coordinator exposing a single USL-Environmental sensor."""
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
                "sensors": {"env_sensor": dict(USL_ENVIRONMENTAL)},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
                "events": {},
            },
            "last_update": None,
        }
        return coordinator

    async def test_environmental_gets_internal_and_external_leak(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Both leak entities are created and report dry/wet independently."""
        config_entry = MagicMock()
        config_entry.runtime_data = MagicMock()
        config_entry.runtime_data.mobility_coordinator = None
        config_entry.runtime_data.coordinator = mock_coordinator

        added: list = []
        await async_setup_entry(hass, config_entry, EntityCollector(added))

        by_key = {
            e.entity_description.key: e
            for e in added
            if isinstance(e, UnifiProtectBinarySensor) and e._device_id == "env_sensor"
        }
        assert "sensor_leak" in by_key
        assert "sensor_leak_external" in by_key
        assert by_key["sensor_leak"].is_on is False
        assert by_key["sensor_leak_external"].is_on is False
        assert "sensor_door" not in by_key

        _publish_sensor_fields(mock_coordinator, leakDetectedAt=1758600000000)
        assert by_key["sensor_leak"].is_on is True
        assert by_key["sensor_leak_external"].is_on is False

        _publish_sensor_fields(
            mock_coordinator,
            leakDetectedAt=None,
            externalLeakDetectedAt=1758600000000,
        )
        assert by_key["sensor_leak"].is_on is False
        assert by_key["sensor_leak_external"].is_on is True

    async def test_leak_attributes_expose_both_channels(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Leak state attributes report internal and external channel."""
        _publish_sensor_fields(mock_coordinator, externalLeakDetectedAt=1758600000000)

        config_entry = MagicMock()
        config_entry.runtime_data = MagicMock()
        config_entry.runtime_data.mobility_coordinator = None
        config_entry.runtime_data.coordinator = mock_coordinator

        added: list = []
        await async_setup_entry(hass, config_entry, EntityCollector(added))
        external = next(
            e
            for e in added
            if isinstance(e, UnifiProtectBinarySensor)
            and e.entity_description.key == "sensor_leak_external"
        )

        assert external.is_on is True
        attrs = external.extra_state_attributes
        assert require_not_none(attrs)["leak_detected"] is False
        assert require_not_none(attrs)["external_leak_detected"] is True
        assert require_not_none(attrs)["external_leak_detected_at"] == 1758600000000
