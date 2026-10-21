# Copyright 2026 UniFi Insights contributors
"""Tests for the Protect 7.3.70 security device entities."""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory

from custom_components.unifi_insights.binary_sensor import (
    UnifiProtectBinarySensor,
    discover_protect_security_binary_sensors,
)
from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.sensor import (
    THREAD_ROLES,
    UnifiProtectSensor,
    discover_protect_security_sensors,
)
from tests.fixtures.library_responses import (
    SAMPLE_ALARM_HUB,
    SAMPLE_KEYPAD_FOB,
    SAMPLE_LINK_STATION,
    SAMPLE_THREAD_LINK_STATION,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _coordinator(hass: HomeAssistant, **protect: dict[str, Any]) -> MagicMock:
    """Build a facade-coordinator mock whose protect data holds ``protect``."""
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.network_client.base_url = "https://192.168.1.1"
    coordinator.protect_client.base_url = "https://192.168.1.1"
    data: dict[str, Any] = {"cameras": {}, "sensors": {}}
    for collection in ("fobs", "link_stations", "alarm_hubs"):
        data[collection] = copy.deepcopy(protect.get(collection, {}))
    coordinator.data = {"devices": {}, "protect": data}
    return coordinator


def _full_coordinator(hass: HomeAssistant) -> MagicMock:
    plain_fob = copy.deepcopy(SAMPLE_KEYPAD_FOB)
    plain_fob.update(
        id="fob_plain",
        featureFlags={"buttons": ["arm"], "hasKeypad": False},
        keypadSettings=None,
        armControlSettings=None,
    )
    return _coordinator(
        hass,
        fobs={"fob_1": SAMPLE_KEYPAD_FOB, "fob_plain": plain_fob},
        link_stations={
            "link_station_1": SAMPLE_LINK_STATION,
            "link_station_thread": SAMPLE_THREAD_LINK_STATION,
        },
        alarm_hubs={"alarm_hub_1": SAMPLE_ALARM_HUB},
    )


def _keys(entities: list[Any]) -> set[tuple[str, str, str]]:
    return {(e._device_type, e._device_id, e.entity_description.key) for e in entities}


def _find(entities: list[Any], device_type: str, device_id: str, key: str) -> Any:
    for entity in entities:
        if _keys([entity]) == {(device_type, device_id, key)}:
            return entity
    pytest.fail(f"no {key} entity for {device_type} {device_id}")


def _binary(
    coordinator: MagicMock, device_type: str, device_id: str, key: str
) -> UnifiProtectBinarySensor:
    entities = discover_protect_security_binary_sensors(coordinator, set())
    return _find(entities, device_type, device_id, key)


def _sensor(
    coordinator: MagicMock, device_type: str, device_id: str, key: str
) -> UnifiProtectSensor:
    entities = discover_protect_security_sensors(coordinator, set())
    return _find(entities, device_type, device_id, key)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscovery:
    """Which devices get which entities."""

    def test_binary_sensors_created_only_where_supported(
        self, hass: HomeAssistant
    ) -> None:
        """Tamper on every hub, Thread only with a network, beep only on keypads.

        The real SuperLink capture (no threadState) and the alarm hub (null
        network) get no Thread entities that would sit at unknown forever.
        """
        entities = discover_protect_security_binary_sensors(
            _full_coordinator(hass), set()
        )

        assert _keys(entities) == {
            ("alarm_hub", "alarm_hub_1", "alarm_hub_device_tamper"),
            ("link_station", "link_station_thread", "thread_network_problem"),
            ("fob", "fob_1", "fob_keypad_beep"),
        }

    def test_sensors_created_only_where_supported(self, hass: HomeAssistant) -> None:
        """Thread sensors need a network; keypad volume needs a keypad."""
        entities = discover_protect_security_sensors(_full_coordinator(hass), set())

        assert _keys(entities) == {
            ("link_station", "link_station_thread", "thread_role"),
            ("link_station", "link_station_thread", "thread_joined_devices"),
            ("fob", "fob_1", "fob_keypad_beep_volume"),
            ("fob", "fob_1", "fob_arm_control"),
        }

    def test_thread_entities_also_apply_to_alarm_hubs(
        self, hass: HomeAssistant
    ) -> None:
        """Alarm hubs share the linkStation schema, threadState included."""
        hub = copy.deepcopy(SAMPLE_ALARM_HUB)
        hub["threadState"] = copy.deepcopy(SAMPLE_THREAD_LINK_STATION["threadState"])
        coordinator = _coordinator(hass, alarm_hubs={"alarm_hub_1": hub})

        binary = discover_protect_security_binary_sensors(coordinator, set())
        sensors = discover_protect_security_sensors(coordinator, set())

        assert ("alarm_hub", "alarm_hub_1", "thread_network_problem") in _keys(binary)
        assert ("alarm_hub", "alarm_hub_1", "thread_role") in _keys(sensors)

    def test_discovery_is_incremental(self, hass: HomeAssistant) -> None:
        """Known entities are not re-added; a network appearing later is."""
        coordinator = _coordinator(hass, alarm_hubs={"alarm_hub_1": SAMPLE_ALARM_HUB})
        known: set[tuple[Any, ...]] = set()

        first = discover_protect_security_binary_sensors(coordinator, known)
        again = discover_protect_security_binary_sensors(coordinator, known)
        coordinator.data["protect"]["alarm_hubs"]["alarm_hub_1"]["threadState"] = (
            copy.deepcopy(SAMPLE_THREAD_LINK_STATION["threadState"])
        )
        later = discover_protect_security_binary_sensors(coordinator, known)

        assert len(first) == 1
        assert again == []
        assert _keys(later) == {("alarm_hub", "alarm_hub_1", "thread_network_problem")}

    def test_sensor_discovery_is_incremental(self, hass: HomeAssistant) -> None:
        """Sensors already added are not added again on the next update."""
        coordinator = _full_coordinator(hass)
        known: set[tuple[Any, ...]] = set()

        first = discover_protect_security_sensors(coordinator, known)
        again = discover_protect_security_sensors(coordinator, known)

        assert len(first) == 4
        assert again == []

    def test_nothing_without_protect_client_or_collections(
        self, hass: HomeAssistant
    ) -> None:
        """No Protect client, or protect data without these keys, is fine."""
        coordinator = _full_coordinator(hass)
        coordinator.protect_client = None
        assert discover_protect_security_binary_sensors(coordinator, set()) == []
        assert discover_protect_security_sensors(coordinator, set()) == []

        bare = MagicMock()
        bare.data = {"devices": {}, "protect": {"cameras": {}}}
        assert discover_protect_security_binary_sensors(bare, set()) == []
        assert discover_protect_security_sensors(bare, set()) == []

    def test_non_dict_records_are_skipped(self, hass: HomeAssistant) -> None:
        """A malformed record never raises during discovery."""
        coordinator = _coordinator(hass)
        coordinator.data["protect"]["fobs"] = {"bad": None}
        coordinator.data["protect"]["alarm_hubs"] = ["not", "a", "dict"]

        assert discover_protect_security_binary_sensors(coordinator, set()) == []
        assert discover_protect_security_sensors(coordinator, set()) == []


# ---------------------------------------------------------------------------
# Binary sensors
# ---------------------------------------------------------------------------


class TestAlarmHubTamper:
    """alarm_hub_device_tamper."""

    @pytest.mark.parametrize(
        ("status", "expected"),
        [("tampered", True), ("restored", False), ("unknown", None), ({}, None)],
    )
    def test_state(self, hass: HomeAssistant, status: Any, expected: Any) -> None:
        """Only the two spec values map; anything else is unknown, not off."""
        hub = copy.deepcopy(SAMPLE_ALARM_HUB)
        hub["alarmHub"]["deviceTamperStatus"] = status
        coordinator = _coordinator(hass, alarm_hubs={"alarm_hub_1": hub})

        entity = _binary(
            coordinator, "alarm_hub", "alarm_hub_1", "alarm_hub_device_tamper"
        )

        assert entity.is_on is expected

    @pytest.mark.parametrize("alarm_hub", [None, {}, {"armed": "on"}])
    def test_missing_status_is_unknown_not_all_clear(
        self, hass: HomeAssistant, alarm_hub: Any
    ) -> None:
        """deviceTamperStatus is optional; missing must never read as off."""
        hub = copy.deepcopy(SAMPLE_ALARM_HUB)
        hub["alarmHub"] = alarm_hub
        coordinator = _coordinator(hass, alarm_hubs={"alarm_hub_1": hub})

        entity = _binary(
            coordinator, "alarm_hub", "alarm_hub_1", "alarm_hub_device_tamper"
        )

        assert entity.is_on is None

    def test_metadata(self, hass: HomeAssistant) -> None:
        """TAMPER class, primary (not diagnostic), stable ids and device."""
        coordinator = _coordinator(hass, alarm_hubs={"alarm_hub_1": SAMPLE_ALARM_HUB})

        entity = _binary(
            coordinator, "alarm_hub", "alarm_hub_1", "alarm_hub_device_tamper"
        )

        assert entity.device_class == BinarySensorDeviceClass.TAMPER
        assert entity.entity_category is None
        assert entity.entity_registry_enabled_default is True
        assert entity.translation_key == "alarm_hub_device_tamper"
        assert entity.unique_id == (
            f"{DOMAIN}_alarm_hub_alarm_hub_1_alarm_hub_device_tamper"
        )
        assert entity.device_info is not None
        assert (DOMAIN, "protect_alarm_hub_alarm_hub_1") in entity.device_info[
            "identifiers"
        ]
        assert entity.available is True

    def test_attributes_from_last_event(self, hass: HomeAssistant) -> None:
        """Event user and time become attributes; time as ISO 8601 UTC."""
        hub = copy.deepcopy(SAMPLE_ALARM_HUB)
        hub["_lastTamperUser"] = "Installer"
        hub["_lastTamperAt"] = 1759628100000
        coordinator = _coordinator(hass, alarm_hubs={"alarm_hub_1": hub})

        entity = _binary(
            coordinator, "alarm_hub", "alarm_hub_1", "alarm_hub_device_tamper"
        )

        assert entity.extra_state_attributes == {
            "last_tamper_user": "Installer",
            "last_tamper_at": "2025-10-05T01:35:00+00:00",
        }

    def test_attributes_omitted_without_event(self, hass: HomeAssistant) -> None:
        """No event yet: no attributes rather than null-valued ones."""
        hub = copy.deepcopy(SAMPLE_ALARM_HUB)
        hub["_lastTamperAt"] = "not-a-time"
        coordinator = _coordinator(hass, alarm_hubs={"alarm_hub_1": hub})

        entity = _binary(
            coordinator, "alarm_hub", "alarm_hub_1", "alarm_hub_device_tamper"
        )

        assert entity.extra_state_attributes == {}

    def test_unavailable_when_disconnected_or_gone(self, hass: HomeAssistant) -> None:
        """Availability follows the device's own state, like other Protect devices."""
        coordinator = _coordinator(hass, alarm_hubs={"alarm_hub_1": SAMPLE_ALARM_HUB})
        entity = _binary(
            coordinator, "alarm_hub", "alarm_hub_1", "alarm_hub_device_tamper"
        )

        coordinator.data["protect"]["alarm_hubs"]["alarm_hub_1"]["state"] = (
            "DISCONNECTED"
        )
        assert entity.available is False

        coordinator.data["protect"]["alarm_hubs"] = {}
        assert entity.available is False
        assert entity.is_on is None
        assert entity.extra_state_attributes == {}


class TestThreadNetworkProblem:
    """thread_network_problem."""

    @pytest.mark.parametrize(
        ("status", "expected"),
        [("error", True), ("ready", False), (None, None), ("booting", None)],
    )
    def test_state(self, hass: HomeAssistant, status: Any, expected: Any) -> None:
        """PROBLEM semantics: error is on, ready is off, anything else unknown."""
        station = copy.deepcopy(SAMPLE_THREAD_LINK_STATION)
        station["threadState"]["network"]["status"] = status
        coordinator = _coordinator(hass, link_stations={"link_station_thread": station})

        entity = _binary(
            coordinator, "link_station", "link_station_thread", "thread_network_problem"
        )

        assert entity.is_on is expected
        assert entity.device_class == BinarySensorDeviceClass.PROBLEM
        assert entity.entity_category == EntityCategory.DIAGNOSTIC

    def test_error_reason_attribute(self, hass: HomeAssistant) -> None:
        """errorReason is surfaced when set and omitted when null."""
        station = copy.deepcopy(SAMPLE_THREAD_LINK_STATION)
        coordinator = _coordinator(hass, link_stations={"link_station_thread": station})
        entity = _binary(
            coordinator, "link_station", "link_station_thread", "thread_network_problem"
        )
        assert entity.extra_state_attributes == {}

        network = coordinator.data["protect"]["link_stations"]["link_station_thread"][
            "threadState"
        ]["network"]
        network["status"] = "error"
        network["errorReason"] = "radio fault"
        # Attributes refresh on the coordinator update, as for every Protect entity.
        entity._update_from_data()
        assert entity.extra_state_attributes == {"error_reason": "radio fault"}

    def test_network_disappearing_reads_unknown(self, hass: HomeAssistant) -> None:
        """An existing entity whose network goes null shows unknown."""
        coordinator = _coordinator(
            hass, link_stations={"link_station_thread": SAMPLE_THREAD_LINK_STATION}
        )
        entity = _binary(
            coordinator, "link_station", "link_station_thread", "thread_network_problem"
        )

        coordinator.data["protect"]["link_stations"]["link_station_thread"][
            "threadState"
        ] = {"network": None}

        assert entity.is_on is None
        assert entity.extra_state_attributes == {}


class TestFobKeypadBeep:
    """fob_keypad_beep."""

    @pytest.mark.parametrize(
        ("beep", "expected"), [(True, True), (False, False), (None, None), (1, None)]
    )
    def test_state(self, hass: HomeAssistant, beep: Any, expected: Any) -> None:
        """beepEnabled as reported; non-bools are unknown."""
        fob = copy.deepcopy(SAMPLE_KEYPAD_FOB)
        fob["keypadSettings"]["beepEnabled"] = beep
        coordinator = _coordinator(hass, fobs={"fob_1": fob})

        entity = _binary(coordinator, "fob", "fob_1", "fob_keypad_beep")

        assert entity.is_on is expected

    def test_disabled_by_default_diagnostic(self, hass: HomeAssistant) -> None:
        """Read-only setting: diagnostic and off by default."""
        coordinator = _coordinator(hass, fobs={"fob_1": SAMPLE_KEYPAD_FOB})

        entity = _binary(coordinator, "fob", "fob_1", "fob_keypad_beep")

        assert entity.entity_registry_enabled_default is False
        assert entity.entity_category == EntityCategory.DIAGNOSTIC
        assert entity.device_class is None


# ---------------------------------------------------------------------------
# Sensors
# ---------------------------------------------------------------------------


class TestThreadRole:
    """thread_role."""

    def test_enum_options_match_spec(self, hass: HomeAssistant) -> None:
        """ENUM sensor with exactly the spec's role values."""
        coordinator = _coordinator(
            hass, link_stations={"link_station_thread": SAMPLE_THREAD_LINK_STATION}
        )

        entity = _sensor(
            coordinator, "link_station", "link_station_thread", "thread_role"
        )

        assert THREAD_ROLES == ["disabled", "detached", "child", "router", "leader"]
        assert entity.device_class == SensorDeviceClass.ENUM
        assert entity.options == THREAD_ROLES
        assert entity.native_value == "leader"
        assert entity.entity_category == EntityCategory.DIAGNOSTIC

    def test_unknown_role_reads_none_and_logs(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        """HA rejects an ENUM state outside its options, so map it to None."""
        station = copy.deepcopy(SAMPLE_THREAD_LINK_STATION)
        station["threadState"]["network"]["role"] = "commissioner"
        coordinator = _coordinator(hass, link_stations={"link_station_thread": station})
        entity = _sensor(
            coordinator, "link_station", "link_station_thread", "thread_role"
        )

        with caplog.at_level(logging.DEBUG):
            assert entity.native_value is None

        assert "commissioner" in caplog.text

    def test_null_role_reads_none(self, hass: HomeAssistant) -> None:
        """A null role (spec allows it) is unknown."""
        station = copy.deepcopy(SAMPLE_THREAD_LINK_STATION)
        station["threadState"]["network"]["role"] = None
        coordinator = _coordinator(hass, link_stations={"link_station_thread": station})

        entity = _sensor(
            coordinator, "link_station", "link_station_thread", "thread_role"
        )

        assert entity.native_value is None

    def test_network_attributes(self, hass: HomeAssistant) -> None:
        """Channel and network ids as attributes; lastUpdatedAt never.

        lastUpdatedAt changes on every gateway refresh and would write a
        recorder row each time.
        """
        coordinator = _coordinator(
            hass, link_stations={"link_station_thread": SAMPLE_THREAD_LINK_STATION}
        )

        entity = _sensor(
            coordinator, "link_station", "link_station_thread", "thread_role"
        )

        assert entity.extra_state_attributes == {
            "channel": 15,
            "network_name": "Home Thread",
            "pan_id": "1a2b",
            "extended_pan_id": "0011223344556677",
        }

    def test_null_network_attributes_are_omitted(self, hass: HomeAssistant) -> None:
        """A detached gateway reports null ids; they are left out."""
        station = copy.deepcopy(SAMPLE_THREAD_LINK_STATION)
        station["threadState"]["network"].update(
            networkName=None, channel=None, panId=None, extendedPanId=None
        )
        coordinator = _coordinator(hass, link_stations={"link_station_thread": station})

        entity = _sensor(
            coordinator, "link_station", "link_station_thread", "thread_role"
        )

        assert entity.extra_state_attributes == {}


class TestThreadJoinedDevices:
    """thread_joined_devices."""

    @pytest.mark.parametrize(
        ("count", "expected"), [(4, 4), (0, 0), (-1, None), (True, None), ("4", None)]
    )
    def test_state(self, hass: HomeAssistant, count: Any, expected: Any) -> None:
        """A non-negative integer, else unknown."""
        station = copy.deepcopy(SAMPLE_THREAD_LINK_STATION)
        station["threadState"]["network"]["joinedDeviceCount"] = count
        coordinator = _coordinator(hass, link_stations={"link_station_thread": station})

        entity = _sensor(
            coordinator, "link_station", "link_station_thread", "thread_joined_devices"
        )

        assert entity.native_value == expected
        assert entity.state_class == SensorStateClass.MEASUREMENT
        assert entity.native_unit_of_measurement is None
        assert not entity.extra_state_attributes


class TestFobSensors:
    """fob_keypad_beep_volume and fob_arm_control."""

    @pytest.mark.parametrize(
        ("volume", "expected"),
        [(60, 60), (0, 0), (100, 100), (150, None), (-5, None), (None, None)],
    )
    def test_volume(self, hass: HomeAssistant, volume: Any, expected: Any) -> None:
        """0-100 % per spec; outside that range is unknown."""
        fob = copy.deepcopy(SAMPLE_KEYPAD_FOB)
        fob["keypadSettings"]["beepVolume"] = volume
        coordinator = _coordinator(hass, fobs={"fob_1": fob})

        entity = _sensor(coordinator, "fob", "fob_1", "fob_keypad_beep_volume")

        assert entity.native_value == expected
        assert entity.native_unit_of_measurement == PERCENTAGE
        assert entity.entity_registry_enabled_default is False

    @pytest.mark.parametrize(
        ("enabled", "expected"),
        [(True, "enabled"), (False, "disabled"), (None, None)],
    )
    def test_arm_control(
        self, hass: HomeAssistant, enabled: Any, expected: Any
    ) -> None:
        """ENUM enabled/disabled, unknown when not reported."""
        fob = copy.deepcopy(SAMPLE_KEYPAD_FOB)
        fob["armControlSettings"]["enabled"] = enabled
        coordinator = _coordinator(hass, fobs={"fob_1": fob})

        entity = _sensor(coordinator, "fob", "fob_1", "fob_arm_control")

        assert entity.native_value == expected
        assert entity.options == ["enabled", "disabled"]
        assert entity.entity_registry_enabled_default is False

    def test_arm_control_attributes(self, hass: HomeAssistant) -> None:
        """Profile ids as attributes, null ones omitted."""
        coordinator = _coordinator(hass, fobs={"fob_1": SAMPLE_KEYPAD_FOB})

        entity = _sensor(coordinator, "fob", "fob_1", "fob_arm_control")

        assert entity.extra_state_attributes == {"arm_profile_id": "arm_profile_away"}

    def test_fob_without_arm_control_gets_no_arm_control_sensor(
        self, hass: HomeAssistant
    ) -> None:
        """No armControlSettings at all means nothing to show."""
        fob = copy.deepcopy(SAMPLE_KEYPAD_FOB)
        fob["armControlSettings"] = None
        coordinator = _coordinator(hass, fobs={"fob_1": fob})

        keys = _keys(discover_protect_security_sensors(coordinator, set()))

        assert ("fob", "fob_1", "fob_arm_control") not in keys
        assert ("fob", "fob_1", "fob_keypad_beep_volume") in keys
