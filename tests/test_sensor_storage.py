# Copyright (c) 2026 Ruaan Deysel

"""Tests for console per-mount storage entities and nearly-full binary sensor."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation

from custom_components.unifi_insights import binary_sensor, sensor
from custom_components.unifi_insights.binary_sensor import (
    STORAGE_NEARLY_FULL_DESCRIPTION,
    UnifiStorageNearlyFullBinarySensor,
    discover_storage_binary_sensors,
)
from custom_components.unifi_insights.const import STORAGE_NEARLY_FULL_PERCENT
from custom_components.unifi_insights.data_transforms import (
    find_storage_mount,
    fullest_persistent_storage_mount,
    get_storage_mounts,
    persistent_storage_mounts,
    storage_mount_labels,
)
from custom_components.unifi_insights.sensor import (
    STORAGE_SENSOR_TYPES,
    UnifiStorageSensor,
    discover_storage_sensors,
)
from tests.fixtures.assertions import require_instance
from tests.fixtures.entities import EntityCollector
from tests.fixtures.facade_data import (
    facade_data,
    set_availability,
    with_network_device,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant


UDM_STORAGE: list[dict[str, Any]] = [
    {
        "mount_point": "/data",
        "name": "eMMC",
        "type": "eMMC",
        "size": 4143677440,
        "used": 1700257792,
    },
    {
        "mount_point": "/persistent",
        "name": "Backup",
        "type": "eMMC",
        "size": 2046640128,
        "used": 318767104,
    },
    {
        "mount_point": "/" + "tmp",
        "name": "Temporary",
        "type": "other",
        "size": 1073741824,
        "used": 1748992,
    },
]


UDM_DEVICE: dict[str, Any] = {
    "id": "udm",
    "_id": "60a1b2c3d4e5f67890123456",
    "name": "Dream Machine Pro SE",
    "status": "online",
    "state": "ONLINE",
    "model": "UDMPROSE",
    "macAddress": "AA:BB:CC:DD:EE:FF",
    "storage_mounts": UDM_STORAGE,
}


def _udm_record() -> dict[str, Any]:
    """Return a mutable copy of the UDM record, to build the next snapshot from."""
    return copy.deepcopy(UDM_DEVICE)


def _publish_udm(coordinator: MagicMock, record: dict[str, Any]) -> Mapping[str, Any]:
    """Publish ``record`` as the UDM in a new snapshot and return it as published."""
    coordinator.data = with_network_device(coordinator.data, "site1", "udm", record)
    published: Mapping[str, Any] = coordinator.data["devices"]["site1"]["udm"]
    return published


@pytest.fixture
def mock_coordinator() -> MagicMock:
    """Create mock coordinator with UDM device holding storage mounts."""
    coordinator = MagicMock()
    coordinator.available = True
    coordinator.last_update_success = True
    coordinator.protect_client = None
    coordinator.data = facade_data(
        sites={"site1": {"id": "site1", "name": "Default"}},
        devices={"site1": {"udm": UDM_DEVICE}},
        stats={"site1": {"udm": {}}},
    )
    coordinator.get_site = MagicMock(return_value=coordinator.data["sites"]["site1"])
    return coordinator


def test_descriptions() -> None:
    """Test entity descriptions for storage sensors and binary sensor."""
    desc_pct, desc_used, desc_total = STORAGE_SENSOR_TYPES

    assert desc_pct.key == "used_percent"
    assert desc_pct.translation_key == "storage_mount_used_percent"
    assert desc_pct.native_unit_of_measurement == PERCENTAGE
    assert desc_pct.suggested_unit_of_measurement is None
    assert desc_pct.suggested_display_precision == 1
    assert desc_pct.device_class is None
    assert desc_pct.state_class == SensorStateClass.MEASUREMENT
    assert desc_pct.entity_category is None
    assert desc_pct.entity_registry_enabled_default is True

    assert desc_used.key == "used"
    assert desc_used.translation_key == "storage_mount_used"
    assert desc_used.native_unit_of_measurement == UnitOfInformation.BYTES
    assert desc_used.suggested_unit_of_measurement == UnitOfInformation.GIBIBYTES
    assert desc_used.suggested_display_precision == 2
    assert desc_used.device_class == SensorDeviceClass.DATA_SIZE
    assert desc_used.state_class == SensorStateClass.MEASUREMENT
    assert desc_used.entity_category == EntityCategory.DIAGNOSTIC
    assert desc_used.entity_registry_enabled_default is False

    assert desc_total.key == "total"
    assert desc_total.translation_key == "storage_mount_total"
    assert desc_total.native_unit_of_measurement == UnitOfInformation.BYTES
    assert desc_total.suggested_unit_of_measurement == UnitOfInformation.GIBIBYTES
    assert desc_total.suggested_display_precision == 2
    assert desc_total.device_class == SensorDeviceClass.DATA_SIZE
    assert desc_total.state_class == SensorStateClass.MEASUREMENT
    assert desc_total.entity_category == EntityCategory.DIAGNOSTIC
    assert desc_total.entity_registry_enabled_default is False

    assert STORAGE_NEARLY_FULL_DESCRIPTION.key == "storage_nearly_full"
    assert STORAGE_NEARLY_FULL_DESCRIPTION.translation_key == "storage_nearly_full"
    assert (
        STORAGE_NEARLY_FULL_DESCRIPTION.device_class == BinarySensorDeviceClass.PROBLEM
    )
    assert STORAGE_NEARLY_FULL_DESCRIPTION.entity_category == EntityCategory.DIAGNOSTIC
    assert STORAGE_NEARLY_FULL_DESCRIPTION.entity_registry_enabled_default is True


def test_discover_storage_sensors_creates_three_per_mount(
    mock_coordinator: MagicMock,
) -> None:
    """Test discover_storage_sensors creates 3 sensors per mount."""
    device_data = mock_coordinator.data["devices"]["site1"]["udm"]
    known_keys: set[tuple[Any, ...]] = set()

    entities = discover_storage_sensors(
        mock_coordinator, "site1", "udm", dict(device_data), known_keys
    )
    assert len(entities) == 9

    expected_ids = {
        "site1_udm_storage_/data_used_percent",
        "site1_udm_storage_/data_used",
        "site1_udm_storage_/data_total",
        "site1_udm_storage_/persistent_used_percent",
        "site1_udm_storage_/persistent_used",
        "site1_udm_storage_/persistent_total",
        "site1_udm_storage_/tmp_used_percent",
        "site1_udm_storage_/tmp_used",
        "site1_udm_storage_/tmp_total",
    }
    assert {e.unique_id for e in entities} == expected_ids

    # Calling again adds none
    assert (
        discover_storage_sensors(
            mock_coordinator, "site1", "udm", dict(device_data), known_keys
        )
        == []
    )

    # Adding a 4th mount adds exactly 3 entities
    udm = _udm_record()
    udm["storage_mounts"].append(
        {
            "mount_point": "/srv",
            "name": "Extra",
            "type": "ext4",
            "size": 1000,
            "used": 100,
        }
    )
    device_data = _publish_udm(mock_coordinator, udm)
    new_entities = discover_storage_sensors(
        mock_coordinator, "site1", "udm", dict(device_data), known_keys
    )
    assert len(new_entities) == 3
    assert {e.unique_id for e in new_entities} == {
        "site1_udm_storage_/srv_used_percent",
        "site1_udm_storage_/srv_used",
        "site1_udm_storage_/srv_total",
    }


@pytest.mark.parametrize(
    "device_data",
    [
        {},
        {"storage_mounts": None},
        {"storage_mounts": "not a list"},
        {"storage_mounts": []},
        {"storage_mounts": [123, "abc", {}, {"mount_point": ""}]},
    ],
)
def test_discover_storage_sensors_ignores_devices_without_mounts(
    mock_coordinator: MagicMock, device_data: dict[str, Any]
) -> None:
    """Test devices with missing or invalid storage_mounts yield zero sensors."""
    known_keys: set[tuple[Any, ...]] = set()
    assert (
        discover_storage_sensors(
            mock_coordinator, "site1", "udm", dict(device_data), known_keys
        )
        == []
    )


def test_sensor_names_use_console_label_with_path_fallback(
    mock_coordinator: MagicMock,
) -> None:
    """Test translation placeholders use label, fallback, and deduplication."""
    device_data = mock_coordinator.data["devices"]["site1"]["udm"]
    known_keys: set[tuple[Any, ...]] = set()
    entities = discover_storage_sensors(
        mock_coordinator, "site1", "udm", dict(device_data), known_keys
    )
    by_mount = {
        require_instance(e, UnifiStorageSensor)._mount_point: e for e in entities
    }
    assert by_mount["/data"].translation_placeholders == {"mount_name": "eMMC"}
    assert by_mount["/persistent"].translation_placeholders == {"mount_name": "Backup"}
    assert by_mount["/" + "tmp"].translation_placeholders == {"mount_name": "Temporary"}

    # Direct tests of storage_mount_labels
    mounts = [
        {"mount_point": "/data", "name": None},
        {"mount_point": "/other", "name": ""},
    ]
    labels = storage_mount_labels(mounts)
    assert labels["/data"] == "/data"
    assert labels["/other"] == "/other"

    # Case-insensitive duplicate labels get mount path appended
    mounts_dup = [
        {"mount_point": "/data", "name": "eMMC"},
        {"mount_point": "/other", "name": "EMMC"},
        {"mount_point": "/single", "name": "Unique"},
    ]
    labels_dup = storage_mount_labels(mounts_dup)
    assert labels_dup["/data"] == "eMMC (/data)"
    assert labels_dup["/other"] == "EMMC (/other)"
    assert labels_dup["/single"] == "Unique"


def test_unique_id_does_not_change_when_name_changes(
    mock_coordinator: MagicMock,
) -> None:
    """Test unique ID depends on mount point and description key, not display label."""
    sensor1 = UnifiStorageSensor(
        mock_coordinator,
        STORAGE_SENSOR_TYPES[0],
        "site1",
        "udm",
        mount_point="/data",
        mount_label="eMMC",
        volatile=False,
    )
    sensor2 = UnifiStorageSensor(
        mock_coordinator,
        STORAGE_SENSOR_TYPES[0],
        "site1",
        "udm",
        mount_point="/data",
        mount_label="Renamed Volume",
        volatile=False,
    )
    assert (
        sensor1.unique_id == sensor2.unique_id == "site1_udm_storage_/data_used_percent"
    )


def test_registry_enabled_defaults(mock_coordinator: MagicMock) -> None:
    """Test enabled defaults: percentage enabled for persistent, all others disabled."""
    device_data = mock_coordinator.data["devices"]["site1"]["udm"]
    entities = discover_storage_sensors(
        mock_coordinator, "site1", "udm", device_data, set()
    )
    by_key = {
        (
            require_instance(e, UnifiStorageSensor)._mount_point,
            e.entity_description.key,
        ): e
        for e in entities
    }

    # /data (persistent): percentage True, used False, total False
    assert by_key[("/data", "used_percent")].entity_registry_enabled_default is True
    assert by_key[("/data", "used")].entity_registry_enabled_default is False
    assert by_key[("/data", "total")].entity_registry_enabled_default is False

    # /persistent (persistent): percentage True, used False, total False
    assert (
        by_key[("/persistent", "used_percent")].entity_registry_enabled_default is True
    )
    assert by_key[("/persistent", "used")].entity_registry_enabled_default is False
    assert by_key[("/persistent", "total")].entity_registry_enabled_default is False

    # /tmp (volatile): all three disabled
    assert (
        by_key[("/" + "tmp", "used_percent")].entity_registry_enabled_default is False
    )
    assert by_key[("/" + "tmp", "used")].entity_registry_enabled_default is False
    assert by_key[("/" + "tmp", "total")].entity_registry_enabled_default is False


def test_sensor_values_match_live_shape(mock_coordinator: MagicMock) -> None:
    """Test sensors report expected percentage and raw byte values from live shape."""
    device_data = mock_coordinator.data["devices"]["site1"]["udm"]
    entities = discover_storage_sensors(
        mock_coordinator, "site1", "udm", device_data, set()
    )
    by_key = {
        (
            require_instance(e, UnifiStorageSensor)._mount_point,
            e.entity_description.key,
        ): e
        for e in entities
    }

    assert by_key[("/data", "used_percent")].native_value == 41.0
    assert by_key[("/data", "used")].native_value == 1700257792
    assert by_key[("/data", "total")].native_value == 4143677440

    assert by_key[("/persistent", "used_percent")].native_value == 15.6
    assert by_key[("/persistent", "used")].native_value == 318767104
    assert by_key[("/persistent", "total")].native_value == 2046640128

    assert by_key[("/" + "tmp", "used_percent")].native_value == 0.2
    assert by_key[("/" + "tmp", "used")].native_value == 1748992
    assert by_key[("/" + "tmp", "total")].native_value == 1073741824


def test_sensor_unknown_vs_unavailable(mock_coordinator: MagicMock) -> None:
    """Test distinction between unknown value (None) and entity unavailability."""
    device_data = mock_coordinator.data["devices"]["site1"]["udm"]
    entities = discover_storage_sensors(
        mock_coordinator, "site1", "udm", device_data, set()
    )
    used_sensor = next(
        e
        for e in entities
        if require_instance(e, UnifiStorageSensor)._mount_point == "/data"
        and e.entity_description.key == "used"
    )
    pct_sensor = next(
        e
        for e in entities
        if require_instance(e, UnifiStorageSensor)._mount_point == "/data"
        and e.entity_description.key == "used_percent"
    )
    total_sensor = next(
        e
        for e in entities
        if require_instance(e, UnifiStorageSensor)._mount_point == "/data"
        and e.entity_description.key == "total"
    )

    udm = _udm_record()

    # 1. used is None -> native_value is None, available is True
    udm["storage_mounts"][0]["used"] = None
    _publish_udm(mock_coordinator, udm)
    assert used_sensor.available is True
    assert used_sensor.native_value is None

    # 2. size 0 -> percentage None, total 0
    udm["storage_mounts"][0]["size"] = 0
    udm["storage_mounts"][0]["used"] = 0
    _publish_udm(mock_coordinator, udm)
    assert pct_sensor.native_value is None
    assert total_sensor.native_value == 0

    # 3. mount removed from storage_mounts -> available is False, values None
    udm["storage_mounts"] = [
        m for m in udm["storage_mounts"] if m["mount_point"] != "/data"
    ]
    _publish_udm(mock_coordinator, udm)
    assert used_sensor.available is False
    assert used_sensor.native_value is None
    assert used_sensor.extra_state_attributes is None

    # 4. storage_mounts key deleted -> available is False
    del udm["storage_mounts"]
    _publish_udm(mock_coordinator, udm)
    assert used_sensor.available is False

    # 5. device offline -> available is False
    udm["state"] = "OFFLINE"
    _publish_udm(mock_coordinator, udm)
    assert used_sensor.available is False


def test_sensor_attributes(mock_coordinator: MagicMock) -> None:
    """Test extra_state_attributes exposes mount_point and storage_type."""
    device_data = mock_coordinator.data["devices"]["site1"]["udm"]
    entities = discover_storage_sensors(
        mock_coordinator, "site1", "udm", device_data, set()
    )
    data_sensor = next(
        e
        for e in entities
        if require_instance(e, UnifiStorageSensor)._mount_point == "/data"
    )
    assert data_sensor.extra_state_attributes == {
        "mount_point": "/data",
        "storage_type": "eMMC",
    }

    # type is None -> drops storage_type
    udm = _udm_record()
    udm["storage_mounts"][0]["type"] = None
    _publish_udm(mock_coordinator, udm)
    assert data_sensor.extra_state_attributes == {"mount_point": "/data"}


def test_binary_sensor_states(mock_coordinator: MagicMock) -> None:
    """Test binary sensor thresholding, attributes, volatile filtering, and ties."""
    binary = UnifiStorageNearlyFullBinarySensor(mock_coordinator, "site1", "udm")
    udm = _udm_record()

    # Off at 41.0%
    assert binary.is_on is False
    assert binary.extra_state_attributes == {
        "threshold_percent": STORAGE_NEARLY_FULL_PERCENT,
        "fullest_mount": "/data",
        "fullest_used_percent": 41.0,
    }

    # On at exactly 90.0% (size 1000, used 900)
    udm["storage_mounts"][0]["size"] = 1000
    udm["storage_mounts"][0]["used"] = 900
    _publish_udm(mock_coordinator, udm)
    assert binary.is_on is True
    assert binary.extra_state_attributes["fullest_used_percent"] == 90.0

    # On above 90%
    udm["storage_mounts"][0]["used"] = 950
    _publish_udm(mock_coordinator, udm)
    assert binary.is_on is True
    assert binary.extra_state_attributes["fullest_used_percent"] == 95.0

    # Volatile /tmp at 100% does NOT turn it on when persistent mounts are below 90%
    udm["storage_mounts"][0]["used"] = 400  # /data = 40.0%
    udm["storage_mounts"][1]["used"] = 300000000  # /persistent ~14.7%
    tmp_mount = next(
        m for m in udm["storage_mounts"] if m["mount_point"] == "/" + "tmp"
    )
    tmp_mount["size"] = 1000
    tmp_mount["used"] = 1000  # 100%
    _publish_udm(mock_coordinator, udm)
    assert binary.is_on is False
    assert binary.extra_state_attributes["fullest_mount"] == "/data"

    # Unknown when every persistent percentage is invalid
    udm["storage_mounts"][0]["size"] = 0
    udm["storage_mounts"][1]["size"] = None
    _publish_udm(mock_coordinator, udm)
    assert binary.is_on is None
    assert binary.extra_state_attributes == {
        "threshold_percent": STORAGE_NEARLY_FULL_PERCENT
    }

    # Tie keeps the first persistent mount
    udm["storage_mounts"][0]["size"] = 1000
    udm["storage_mounts"][0]["used"] = 500  # 50.0%
    udm["storage_mounts"][1]["size"] = 1000
    udm["storage_mounts"][1]["used"] = 500  # 50.0%
    _publish_udm(mock_coordinator, udm)
    assert binary.extra_state_attributes["fullest_mount"] == "/data"


def test_binary_sensor_availability_and_discovery(
    mock_coordinator: MagicMock,
) -> None:
    """Test binary sensor discovery, uniqueness, and availability."""
    device_data = mock_coordinator.data["devices"]["site1"]["udm"]
    known_keys: set[tuple[Any, ...]] = set()

    # Discovered for UDM
    sensors = discover_storage_binary_sensors(
        mock_coordinator, "site1", "udm", dict(device_data), known_keys
    )
    assert len(sensors) == 1
    binary = sensors[0]
    assert binary.unique_id == "site1_udm_storage_nearly_full"
    assert binary.available is True

    # Not created twice
    assert (
        discover_storage_binary_sensors(
            mock_coordinator, "site1", "udm", dict(device_data), known_keys
        )
        == []
    )

    # Not created for device with only volatile mounts
    volatile_only = {
        "storage_mounts": [{"mount_point": "/" + "tmp", "size": 1000, "used": 100}]
    }
    assert (
        discover_storage_binary_sensors(
            mock_coordinator, "site1", "tmp_dev", volatile_only, set()
        )
        == []
    )

    # Becomes unavailable when device reports no persistent storage
    udm = _udm_record()
    udm["storage_mounts"] = [{"mount_point": "/" + "tmp", "size": 1000, "used": 100}]
    _publish_udm(mock_coordinator, udm)
    assert binary.available is False


def test_storage_entities_unavailable_when_device_coordinator_fails(
    mock_coordinator: MagicMock,
) -> None:
    """A failed device coordinator overrides mounts that are still cached."""
    device_data = mock_coordinator.data["devices"]["site1"]["udm"]
    storage_sensors = discover_storage_sensors(
        mock_coordinator, "site1", "udm", device_data, set()
    )
    nearly_full = discover_storage_binary_sensors(
        mock_coordinator, "site1", "udm", device_data, set()
    )[0]
    assert all(entity.available for entity in storage_sensors)
    assert nearly_full.available is True

    set_availability(mock_coordinator, device=False)

    # The cached mounts are untouched, so only the coordinator gate applies.
    assert device_data["storage_mounts"] == UDM_STORAGE
    assert [entity.available for entity in storage_sensors] == [False] * len(
        storage_sensors
    )
    assert nearly_full.available is False


def test_helpers_tolerate_bad_shapes() -> None:
    """Test helper robustness with None, non-dicts, and malformed mounts."""
    assert get_storage_mounts(None) == []
    assert find_storage_mount(None, "/data") is None

    bad_device = {
        "storage_mounts": [
            "not a dict",
            None,
            {},
            {"mount_point": None},
            {"mount_point": ""},
            {"mount_point": 123},
            {"mount_point": "/valid"},
        ]
    }
    assert get_storage_mounts(bad_device) == [{"mount_point": "/valid"}]
    assert find_storage_mount(bad_device, "/valid") == {"mount_point": "/valid"}
    assert find_storage_mount(bad_device, "/missing") is None
    assert persistent_storage_mounts(bad_device) == [{"mount_point": "/valid"}]
    assert fullest_persistent_storage_mount(bad_device) is None


@pytest.fixture
def mock_config_entry(mock_coordinator: MagicMock) -> MagicMock:
    """Create a mock config entry with runtime_data pointing to coordinator."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.runtime_data = MagicMock()
    entry.runtime_data.mobility_coordinator = None
    entry.runtime_data.coordinator = mock_coordinator
    return entry


@pytest.mark.asyncio
async def test_sensor_setup_entry_creates_storage_sensors(
    hass: HomeAssistant, mock_coordinator: MagicMock, mock_config_entry: MagicMock
) -> None:
    """Test sensor async_setup_entry creates 9 UnifiStorageSensor instances."""
    added_entities: list[Any] = []

    add_entities = EntityCollector(added_entities)

    await sensor.async_setup_entry(hass, mock_config_entry, add_entities)

    storage_sensors = [e for e in added_entities if isinstance(e, UnifiStorageSensor)]
    assert len(storage_sensors) == 9

    # Running the coordinator listener again without changes adds no new entities
    listener = mock_coordinator.async_add_listener.call_args[0][0]
    count_before = len(added_entities)
    listener()
    assert len(added_entities) == count_before


@pytest.mark.asyncio
async def test_sensor_setup_entry_adds_late_mounts(
    hass: HomeAssistant, mock_coordinator: MagicMock, mock_config_entry: MagicMock
) -> None:
    """Test coordinator listener discovers late mounts that appear in later polls."""
    added_entities: list[Any] = []

    add_entities = EntityCollector(added_entities)

    await sensor.async_setup_entry(hass, mock_config_entry, add_entities)

    listener = mock_coordinator.async_add_listener.call_args[0][0]
    udm = _udm_record()
    udm["storage_mounts"].append(
        {
            "mount_point": "/mnt/external",
            "name": "External",
            "type": "ext4",
            "size": 5000000000,
            "used": 1000000000,
        }
    )
    _publish_udm(mock_coordinator, udm)

    count_before = len(added_entities)
    listener()
    newly_added = added_entities[count_before:]
    assert len(newly_added) == 3
    assert all(isinstance(e, UnifiStorageSensor) for e in newly_added)
    assert {e.unique_id for e in newly_added} == {
        "site1_udm_storage_/mnt/external_used_percent",
        "site1_udm_storage_/mnt/external_used",
        "site1_udm_storage_/mnt/external_total",
    }


@pytest.mark.asyncio
async def test_binary_sensor_setup_entry_creates_nearly_full(
    hass: HomeAssistant, mock_coordinator: MagicMock, mock_config_entry: MagicMock
) -> None:
    """Test binary_sensor async_setup_entry creates nearly full sensor for UDM."""
    # Add a second device without storage mounts to coordinator data
    mock_coordinator.data = with_network_device(
        mock_coordinator.data,
        "site1",
        "gw2",
        {
            "id": "gw2",
            "name": "Gateway Without Storage",
            "model": "UCG-Ultra",
            "state": "ONLINE",
            "macAddress": "22:33:44:55:66:77",
        },
    )
    added_entities: list[Any] = []

    add_entities = EntityCollector(added_entities)

    await binary_sensor.async_setup_entry(hass, mock_config_entry, add_entities)

    storage_binary_sensors = [
        e for e in added_entities if isinstance(e, UnifiStorageNearlyFullBinarySensor)
    ]
    assert len(storage_binary_sensors) == 1
    assert storage_binary_sensors[0].unique_id == "site1_udm_storage_nearly_full"


@pytest.mark.asyncio
async def test_existing_fixtures_unchanged(
    hass: HomeAssistant, mock_coordinator: MagicMock, mock_config_entry: MagicMock
) -> None:
    """Test a device with no storage_mounts yields zero storage entities."""
    udm = _udm_record()
    udm["storage_mounts"] = []
    _publish_udm(mock_coordinator, udm)
    added_sensors: list[Any] = []
    added_binary_sensors: list[Any] = []

    await sensor.async_setup_entry(
        hass, mock_config_entry, EntityCollector(added_sensors)
    )
    await binary_sensor.async_setup_entry(
        hass,
        mock_config_entry,
        EntityCollector(added_binary_sensors),
    )

    assert not any(isinstance(e, UnifiStorageSensor) for e in added_sensors)
    assert not any(
        isinstance(e, UnifiStorageNearlyFullBinarySensor) for e in added_binary_sensors
    )
