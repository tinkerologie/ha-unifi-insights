# Copyright (c) 2026 Ruaan Deysel

"""Synthetic conditional NVR storage compatibility and coordinator tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import EntityCategory, UnitOfInformation
from homeassistant.helpers.translation import async_get_translations
from pydantic import ValidationError
from unifi_official_api.protect.models.nvr import NVR, StorageInfo

from custom_components.unifi_insights.binary_sensor import (
    BINARY_SENSOR_TYPES,
    UnifiInsightsNVRStorageBinarySensor,
)
from custom_components.unifi_insights.binary_sensor import (
    async_setup_entry as setup_binary_sensors,
)
from custom_components.unifi_insights.coordinators.protect import (
    UnifiProtectCoordinator,
)
from custom_components.unifi_insights.data_transforms import normalize_legacy_storage
from custom_components.unifi_insights.protect_snapshot import build_protect_snapshot
from custom_components.unifi_insights.sensor import (
    UnifiProtectNVRSensor,
)
from custom_components.unifi_insights.sensor import (
    async_setup_entry as setup_sensors,
)
from custom_components.unifi_insights.storage_telemetry import normalize_nvr_storage

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_nvr_diagnostic_english_name_loads_at_runtime(
    hass: HomeAssistant,
    enable_custom_integrations: None,
) -> None:
    """Custom integration labels must resolve from HA's English translation file."""
    translations = await async_get_translations(
        hass, "en", "entity", {"unifi_insights"}
    )
    key = "component.unifi_insights.entity.binary_sensor.storage_degraded.name"
    assert translations[key] == "Storage degraded"


def test_console_arbitrary_size_integer_does_not_abort_normalization() -> None:
    """Malformed giant byte values stay unknown instead of failing a refresh."""
    mounts = normalize_legacy_storage(
        {"storage": [{"mount_point": "/data", "size": 10**500, "used": 0}]}
    )
    assert len(mounts) == 1
    assert mounts[0]["size"] is None
    assert mounts[0]["used"] == 0


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, (None, None, None, None, None)),
        ({"storageInfo": None}, (None, None, None, None, None)),
        ({"storageInfo": {"healthy": False}}, (None, None, None, None, False)),
        ({"storageInfo": {"healthy": True}}, (None, None, None, None, True)),
        ({"storage": {"used": 90}}, (None, None, None, 90.0, None)),
        ({"storageUsedBytes": 0, "storageTotalBytes": 100}, (0, 100, 100, 0.0, None)),
        (
            {"storage_used_bytes": 90, "storage_total_bytes": 100},
            (90, 100, 10, 90.0, None),
        ),
        (
            {
                "storage_info": {
                    "used_size": 90,
                    "total_size": 100,
                    "available_size": 10,
                }
            },
            (90, 100, 10, 90.0, None),
        ),
        (
            {"storageInfo": {"usedSize": 90, "totalSize": 100, "healthy": False}},
            (90, 100, 10, 90.0, False),
        ),
        (
            {"storageInfo": {"usedSpaceBytes": 0, "totalSpaceBytes": 100}},
            (0, 100, 100, 0.0, None),
        ),
        ({"storageAvailableBytes": 0}, (None, None, 0, None, None)),
        (
            {"storageUsedBytes": True, "storage_used_bytes": 0},
            (0, None, None, None, None),
        ),
        (
            {"storageUsedBytes": 101, "storageTotalBytes": 100},
            (None, None, None, None, None),
        ),
        (
            {"storageInfo": {"usedSize": 50, "totalSize": 100, "availableSize": 70}},
            (None, None, None, None, None),
        ),
        (
            {
                "storageUsedBytes": 5,
                "storageTotalBytes": 100,
                "storageInfo": {"usedSize": 90, "totalSize": 100},
                "storage": {"used": 99},
            },
            (5, 100, 95, 5.0, None),
        ),
        (
            {"storageInfo": {"healthy": False}, "storage": {"healthy": True}},
            (None, None, None, None, False),
        ),
        (
            {"storageInfo": {"healthy": None}, "storage": {"healthy": False}},
            (None, None, None, None, False),
        ),
        (
            {
                "storageTotalBytes": 100,
                "storageAvailableBytes": 101,
                "storageInfo": {"usedSize": 10, "totalSize": 100},
            },
            (10, 100, 90, 10.0, None),
        ),
        (
            {"storageUsedBytes": 10, "storageInfo": {"totalSize": 100}},
            (10, None, None, None, None),
        ),
        ({"storageUsedBytes": 0, "storageTotalBytes": 0}, (0, 0, 0, None, None)),
        (
            {"storageUsedBytes": 10**400, "storageTotalBytes": 10**401},
            (None, None, None, None, None),
        ),
    ],
)
def test_normalized_signals(payload: dict[str, Any], expected: tuple) -> None:
    """Alias/source precedence preserves zero, avoids contradictory byte tuples."""
    storage = normalize_nvr_storage(payload)
    assert (
        storage.used_bytes,
        storage.total_bytes,
        storage.available_bytes,
        storage.used_percent,
        storage.healthy,
    ) == expected
    assert storage.degraded is (None if expected[4] is None else not expected[4])


@pytest.mark.parametrize(
    "invalid", [True, False, -1, float("nan"), float("inf"), "90", {}, []]
)
def test_invalid_signal_values(invalid: Any) -> None:
    """Invalid primary aliases fall through; health never coerces strings/numbers."""
    storage = normalize_nvr_storage(
        {
            "storageUsedBytes": invalid,
            "storage_used_bytes": 0,
            "storageTotalBytes": 100,
            "storage": {"used": invalid, "healthy": invalid},
        }
    )
    assert storage.used_bytes == 0
    assert storage.used_percent == 0
    if not isinstance(invalid, bool):
        assert storage.healthy is None
    assert normalize_nvr_storage({"storage": {"used": invalid}}).used_percent is None


@pytest.mark.parametrize(
    ("used", "full"),
    [
        (89.94, False),
        (89.96, True),
        (90, True),
        (90.04, True),
        (100, True),
        (101, None),
    ],
)
def test_threshold_matches_displayed_percentage(
    used: float, *, full: bool | None
) -> None:
    """Inclusive threshold uses one-decimal rounding, matching console mounts."""
    assert normalize_nvr_storage({"storage": {"used": used}}).nearly_full is full


@pytest.mark.parametrize("healthy", [None, False, True, "false", 0])
def test_model_pipeline_omitted_sizes_and_literal_health(healthy: Any) -> None:
    """Actual model serialization keeps supplied values, never synthesizes capacity."""
    nvr = NVR.model_validate({"id": "nvr1", "storageInfo": {"healthy": healthy}})
    serialized = nvr.model_dump(by_alias=True, exclude_none=False)
    assert serialized["storageInfo"] == {"healthy": healthy}
    storage = normalize_nvr_storage(serialized)
    assert storage.total_bytes is None
    assert storage.used_bytes is None
    assert storage.healthy is (healthy if isinstance(healthy, bool) else None)
    assert NVR(id="nvr1").model_dump()["storage_info"] is None


def test_explicit_storage_model_serialization() -> None:
    """Explicit models, extra aliases and Python/JSON alias modes preserve omissions."""
    model = StorageInfo.model_validate(
        {"healthy": False, "usedSpaceBytes": 0, "totalSpaceBytes": 100}
    )
    nvr = NVR.model_validate({"id": "nvr1", "storageInfo": model})
    assert nvr.model_dump()["storage_info"] == {
        "healthy": False,
        "usedSpaceBytes": 0,
        "totalSpaceBytes": 100,
    }
    storage = normalize_nvr_storage(nvr.model_dump(by_alias=True))
    assert storage.used_percent == 0
    assert storage.degraded is True
    assert nvr.model_dump(by_alias=True, exclude={"storage_info"}) == {
        key: value
        for key, value in nvr.model_dump(by_alias=True).items()
        if key != "storageInfo"
    }
    assert StorageInfo().healthy is None
    for invalid in ("false", 0):
        with pytest.raises(ValidationError):
            StorageInfo(healthy=invalid)
    for invalid in (True, "100"):
        nvr = NVR.model_validate({"id": "nvr1", "storageInfo": {"usedSize": invalid}})
        assert normalize_nvr_storage(nvr.model_dump(by_alias=True)).used_bytes is None


@pytest.mark.parametrize(
    ("alias", "field"),
    [
        ("totalSize", "total_size"),
        ("usedSize", "used_size"),
        ("availableSize", "available_size"),
    ],
)
@pytest.mark.parametrize("invalid", ["100", True, 1.5, None])
def test_storage_info_rejects_non_integer_sizes(
    alias: str, field: str, invalid: object
) -> None:
    """Strict size fields reject coercible values instead of guessing a number."""
    with pytest.raises(ValidationError) as excinfo:
        StorageInfo.model_validate({alias: invalid})
    assert [error["loc"] for error in excinfo.value.errors()] == [(alias,)]
    assert getattr(StorageInfo.model_validate({alias: 100}), field) == 100


def test_targeted_serializer_preserves_caller_filters() -> None:
    """Stripping defaults must not restore excluded/null nested fields."""
    nvr = NVR.model_validate(
        {
            "id": "nvr1",
            "storageInfo": StorageInfo.model_validate(
                {"healthy": None, "usedSize": 0, "extra": {"keep": 1, "drop": 2}}
            ),
        }
    )
    assert nvr.model_dump(by_alias=True, exclude_none=True)["storageInfo"] == {
        "usedSize": 0,
        "extra": {"keep": 1, "drop": 2},
    }
    assert nvr.model_dump(
        by_alias=True,
        exclude={"storage_info": {"healthy": True, "extra": {"drop"}}},
    )["storageInfo"] == {"usedSize": 0, "extra": {"keep": 1}}
    assert nvr.model_dump(include={"storage_info": {"used_size"}}) == {
        "storage_info": {"used_size": 0}
    }


async def test_poll_model_wss_delta_null_and_generation(
    hass: HomeAssistant,
    mock_config_entry,
    mock_network_client,
    mock_protect_client,
    mock_coordinator,
) -> None:
    """Poll feeds entities/snapshot; WSS deltas preserve siblings and win races."""
    coordinator = UnifiProtectCoordinator(
        hass, mock_network_client, mock_protect_client, mock_config_entry
    )
    try:
        mock_protect_client.nvr.get.return_value = NVR.model_validate(
            {
                "id": "nvr1",
                "storageInfo": {"usedSize": 90, "totalSize": 100, "healthy": False},
            }
        )
        await coordinator._fetch_nvr()
        mock_protect_client.nvr.get.assert_awaited_once_with()
        mock_coordinator.data["protect"]["nvrs"] = coordinator.data["nvrs"]
        _assert_entity_snapshot_parity(mock_coordinator, degraded=True, full=True)
        coordinator._handle_device_update(
            "nvr", {"id": "nvr1", "storageInfo": {"usedSize": 10}}
        )
        _assert_entity_snapshot_parity(mock_coordinator, degraded=True, full=False)
        coordinator._handle_device_update(
            "nvr", {"id": "nvr1", "storageInfo": {"healthy": None}}
        )
        _assert_entity_snapshot_parity(mock_coordinator, degraded=None, full=False)

        async def stale_poll() -> NVR:
            coordinator._handle_device_update(
                "nvr", {"id": "nvr1", "storageInfo": {"usedSize": 95, "healthy": False}}
            )
            return NVR.model_validate(
                {
                    "id": "nvr1",
                    "storageInfo": {"usedSize": 0, "totalSize": 100, "healthy": True},
                }
            )

        mock_protect_client.nvr.get.side_effect = stale_poll
        await coordinator._fetch_nvr()
        _assert_entity_snapshot_parity(mock_coordinator, degraded=True, full=True)
        coordinator._handle_device_update("nvr", {"id": "nvr1", "storageInfo": None})
        _assert_entity_snapshot_parity(mock_coordinator, degraded=None, full=None)
    finally:
        await coordinator.async_shutdown()


def _assert_entity_snapshot_parity(
    coordinator: MagicMock, *, degraded: bool | None, full: bool | None
) -> None:
    """Validate shared signals through entities and snapshot warnings."""
    for key, expected in (
        ("storage_degraded", degraded),
        ("storage_nearly_full", full),
    ):
        description = next(
            d for d in BINARY_SENSOR_TYPES if d.key == key and d.device_type == "nvr"
        )
        entity = UnifiInsightsNVRStorageBinarySensor(coordinator, description, "nvr1")
        assert entity.is_on is expected
        assert entity.available is (expected is not None)
        assert entity.unique_id == f"unifi_insights_nvr_nvr1_{key}"
        assert entity.entity_description.device_class == BinarySensorDeviceClass.PROBLEM
        assert entity.entity_description.entity_category == EntityCategory.DIAGNOSTIC
        assert entity.device_info["identifiers"] == {
            ("unifi_insights", "protect_nvr_nvr1")
        }
    snapshot = build_protect_snapshot(
        coordinator.data,
        entry_id="entry",
        entry_title="Console",
        entity_registry=MagicMock(),
        ha_device_ids={},
        protect_available=True,
    )
    assert ("storage_unhealthy" in snapshot["warnings"]) is (degraded is True)
    assert ("storage_nearly_full" in snapshot["warnings"]) is (full is True)


async def test_conditional_discovery_and_availability(
    hass: HomeAssistant, mock_coordinator, mock_config_entry
) -> None:
    """Late signals discover once; disappearing signals retain entities."""
    mock_coordinator.data["protect"]["nvrs"] = {
        "nvr1": {"id": "nvr1", "name": "Recorder"},
        "bad": None,
    }
    mock_config_entry.runtime_data = MagicMock(coordinator=mock_coordinator)
    binaries: list = []
    capacities: list = []
    await setup_binary_sensors(hass, mock_config_entry, binaries.extend)
    binary_listener = mock_coordinator.async_add_listener.call_args[0][0]
    await setup_sensors(
        hass,
        mock_config_entry,
        lambda entities: capacities.extend(
            e for e in entities if isinstance(e, UnifiProtectNVRSensor)
        ),
    )
    sensor_listener = mock_coordinator.async_add_listener.call_args[0][0]
    assert not binaries
    assert not capacities
    nvr = mock_coordinator.data["protect"]["nvrs"]["nvr1"]
    nvr["storageInfo"] = {"healthy": False}
    binary_listener()
    sensor_listener()
    assert len(binaries) == 1
    assert not capacities
    nvr["storageInfo"].update(usedSize=0, totalSize=100)
    binary_listener()
    sensor_listener()
    assert len(binaries) == 2
    assert len(capacities) == 4
    binary_listener()
    sensor_listener()
    assert len(binaries) == 2
    assert len(capacities) == 4
    used = next(
        entity
        for entity in capacities
        if entity.entity_description.key == "storage_used"
    )
    assert isinstance(used, UnifiProtectNVRSensor)
    assert used.unique_id == "unifi_insights_nvr_nvr1_storage_used"
    assert used.native_value == 0
    assert used.native_unit_of_measurement == UnitOfInformation.GIGABYTES
    assert used.extra_state_attributes["storage_used"] == 0
    del nvr["storageInfo"]
    assert all(not entity.available for entity in binaries + capacities)
    assert all(entity.is_on is None for entity in binaries)
    nvr["storage"] = {"used": 90, "healthy": True}
    assert all(entity.available for entity in binaries)
    assert binaries[0].is_on is False
    assert binaries[1].is_on is True
    assert not used.available
    mock_coordinator.protect_available = False
    assert all(not entity.available for entity in binaries + capacities)
    mock_coordinator.protect_available = True
    nvr["state"] = "DISCONNECTED"
    assert all(not entity.available for entity in binaries + capacities)
    remove_listener = mock_coordinator.async_add_listener.return_value
    await mock_config_entry._async_process_on_unload(hass)
    assert remove_listener.call_count == 2
    assert not mock_coordinator.protect_client.nvr.get.called


@pytest.mark.parametrize(
    "inventory",
    [
        None,
        [],
        [{"id": "nvr1", "storageInfo": {"healthy": False}}],
        "invalid",
        42,
    ],
)
async def test_malformed_nvr_inventory_waits_for_valid_discovery(
    hass: HomeAssistant, mock_coordinator, mock_config_entry, inventory: object
) -> None:
    """Malformed inventories cannot invent diagnostics or prevent later discovery."""
    protect = mock_coordinator.data["protect"]
    protect["nvrs"] = inventory
    mock_config_entry.runtime_data = MagicMock(coordinator=mock_coordinator)
    binaries: list = []

    await setup_binary_sensors(hass, mock_config_entry, binaries.extend)
    discover = mock_coordinator.async_add_listener.call_args[0][0]
    discover()
    assert not binaries

    valid_inventory = {
        "nvr1": {
            "id": "nvr1",
            "name": "Recorder",
            "storageInfo": {"healthy": False, "usedSize": 90, "totalSize": 100},
        }
    }
    protect["nvrs"] = valid_inventory
    discover()
    assert {entity.unique_id for entity in binaries} == {
        "unifi_insights_nvr_nvr1_storage_degraded",
        "unifi_insights_nvr_nvr1_storage_nearly_full",
    }
    assert all(entity.available and entity.is_on for entity in binaries)

    protect["nvrs"] = inventory
    discover()
    protect["nvrs"] = valid_inventory
    discover()
    assert len(binaries) == 2
    assert not mock_coordinator.protect_client.nvr.get.called

    remove_listener = mock_coordinator.async_add_listener.return_value
    await mock_config_entry._async_process_on_unload(hass)
    remove_listener.assert_called_once_with()


@pytest.mark.parametrize("protect", [None, [], "invalid"])
async def test_malformed_protect_data_waits_for_valid_discovery(
    hass: HomeAssistant, mock_coordinator, mock_config_entry, protect: object
) -> None:
    """A non-dict Protect payload is skipped; later valid data is discovered."""
    valid_protect = mock_coordinator.data["protect"]
    valid_protect["nvrs"] = {
        "nvr1": {
            "id": "nvr1",
            "name": "Recorder",
            "storageInfo": {"healthy": False, "usedSize": 90, "totalSize": 100},
        }
    }
    mock_coordinator.data["protect"] = protect
    mock_config_entry.runtime_data = MagicMock(coordinator=mock_coordinator)
    binaries: list = []

    await setup_binary_sensors(hass, mock_config_entry, binaries.extend)
    discover = mock_coordinator.async_add_listener.call_args[0][0]
    discover()
    assert not [
        e for e in binaries if isinstance(e, UnifiInsightsNVRStorageBinarySensor)
    ]

    mock_coordinator.data["protect"] = valid_protect
    discover()
    nvr_binaries = [
        e for e in binaries if isinstance(e, UnifiInsightsNVRStorageBinarySensor)
    ]
    assert len(nvr_binaries) == 2
    assert all(entity.available and entity.is_on for entity in nvr_binaries)

    # The NVR vanishing, or arriving empty, makes the diagnostics unavailable.
    for missing in ({}, {"nvr1": {}}):
        valid_protect["nvrs"] = missing
        assert [entity.available for entity in nvr_binaries] == [False, False]
