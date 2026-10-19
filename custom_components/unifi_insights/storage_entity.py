# Copyright (c) 2026 Ruaan Deysel

"""
Per-mount storage entities for UniFi consoles.

A console that runs the Network application lists its filesystems in the
classic ``stat/device`` record. The device coordinator normalises that list
into ``storage_mounts`` (see ``normalize_legacy_storage``) from the response it
already fetches; nothing here makes a request.

Capacity only: the classic record carries no disk health, SMART or RAID data,
and the official Integration API has no storage fields at all.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation

from .const import STORAGE_NEARLY_FULL_PERCENT
from .data_transforms import is_volatile_storage_mount, storage_used_percent
from .entity import UnifiInsightsEntity

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.helpers.typing import StateType

    from .coordinators import UnifiInsightsFacadeCoordinator


@dataclass(frozen=True, kw_only=True)
class UnifiStorageSensorEntityDescription(SensorEntityDescription):
    """Describes a per-mount storage sensor."""

    value_fn: Callable[[dict[str, Any]], StateType]


STORAGE_SENSOR_TYPES: tuple[UnifiStorageSensorEntityDescription, ...] = (
    UnifiStorageSensorEntityDescription(
        key="used_percent",
        translation_key="storage_mount_used_percent",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=storage_used_percent,
    ),
    UnifiStorageSensorEntityDescription(
        key="used",
        translation_key="storage_mount_used",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda mount: mount.get("used"),
    ),
    UnifiStorageSensorEntityDescription(
        key="total",
        translation_key="storage_mount_total",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda mount: mount.get("size"),
    ),
)

STORAGE_NEARLY_FULL_DESCRIPTION = BinarySensorEntityDescription(
    key="storage_nearly_full",
    translation_key="storage_nearly_full",
    device_class=BinarySensorDeviceClass.PROBLEM,
    entity_category=EntityCategory.DIAGNOSTIC,
)

# Every storage sensor unique ID contains this marker before the mount path.
STORAGE_UNIQUE_ID_MARKER = "_storage_"


def is_storage_unique_id(unique_id: str) -> bool:
    """
    Return True for a storage sensor unique ID.

    A mount path can contain "_port_" (for example "/mnt/usb_port_1"), so the
    stale port sweep in sensor.py uses this to leave storage rows alone.
    """
    return STORAGE_UNIQUE_ID_MARKER in unique_id


def get_storage_mounts(device_data: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the usable normalised mounts of a device (empty when none)."""
    mounts = device_data.get("storage_mounts") if device_data else None
    if not isinstance(mounts, list):
        return []
    return [
        mount
        for mount in mounts
        if isinstance(mount, dict)
        and isinstance(mount.get("mount_point"), str)
        and mount["mount_point"]
    ]


def find_storage_mount(
    device_data: dict[str, Any] | None, mount_point: str
) -> dict[str, Any] | None:
    """Return the mount with this mount point, or None when it is gone."""
    for mount in get_storage_mounts(device_data):
        if mount["mount_point"] == mount_point:
            return mount
    return None


def storage_mount_labels(mounts: list[dict[str, Any]]) -> dict[str, str]:
    """
    Return a display label per mount point, unique within the device.

    The label is the console's ``name`` ("eMMC", "Backup") or the mount path
    when it has none. Labels that two mounts share (compared case-insensitively)
    get the mount path appended so entity names never repeat.
    """
    bases = {
        mount["mount_point"]: mount.get("name") or mount["mount_point"]
        for mount in mounts
    }
    counts = Counter(base.casefold() for base in bases.values())
    return {
        mount_point: f"{base} ({mount_point})" if counts[base.casefold()] > 1 else base
        for mount_point, base in bases.items()
    }


def _persistent_mounts(device_data: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the mounts that count towards "storage nearly full"."""
    return [
        mount
        for mount in get_storage_mounts(device_data)
        if not is_volatile_storage_mount(mount)
    ]


def _fullest_persistent_mount(
    device_data: dict[str, Any] | None,
) -> tuple[dict[str, Any], float] | None:
    """Return the persistent mount with the highest valid fill level."""
    scored = [
        (mount, percent)
        for mount in _persistent_mounts(device_data)
        if (percent := storage_used_percent(mount)) is not None
    ]
    return max(scored, key=lambda item: item[1]) if scored else None


class UnifiStorageSensor(UnifiInsightsEntity, SensorEntity):
    """One capacity reading of one filesystem of a UniFi device."""

    entity_description: UnifiStorageSensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        description: UnifiStorageSensorEntityDescription,
        site_id: str,
        device_id: str,
        *,
        mount_point: str,
        mount_label: str,
        volatile: bool,
    ) -> None:
        """Initialize the storage sensor."""
        super().__init__(coordinator, description, site_id, device_id)
        self._mount_point = mount_point
        self._attr_translation_placeholders = {"mount_name": mount_label}
        # The mount path is the stable key; the label can change with the
        # console's volume name without moving the entity.
        self._attr_unique_id = (
            f"{site_id}_{device_id}{STORAGE_UNIQUE_ID_MARKER}"
            f"{mount_point}_{description.key}"
        )
        if volatile:
            self._attr_entity_registry_enabled_default = False

    def _mount(self) -> dict[str, Any] | None:
        """Return this sensor's mount from coordinator data, if it still exists."""
        return find_storage_mount(self.device_data, self._mount_point)

    @property
    def available(self) -> bool:
        """Return False once the mount is gone from the console's report."""
        return bool(super().available and self._mount() is not None)

    @property
    def native_value(self) -> StateType:
        """Return the reading; None (unknown) when the console gave no number."""
        mount = self._mount()
        return None if mount is None else self.entity_description.value_fn(mount)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return which filesystem this is."""
        mount = self._mount()
        if mount is None:
            return None
        attributes = {
            "mount_point": mount.get("mount_point"),
            "storage_type": mount.get("type"),
        }
        return {key: value for key, value in attributes.items() if value is not None}


class UnifiStorageNearlyFullBinarySensor(UnifiInsightsEntity, BinarySensorEntity):
    """On when a persistent filesystem of a UniFi device is nearly full."""

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        site_id: str,
        device_id: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(
            coordinator, STORAGE_NEARLY_FULL_DESCRIPTION, site_id, device_id
        )

    @property
    def available(self) -> bool:
        """Return False when the device reports no persistent storage."""
        return bool(super().available and _persistent_mounts(self.device_data))

    @property
    def is_on(self) -> bool | None:
        """Return True when the fullest persistent mount reaches the threshold."""
        fullest = _fullest_persistent_mount(self.device_data)
        if fullest is None:
            return None
        return fullest[1] >= STORAGE_NEARLY_FULL_PERCENT

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the threshold and the fullest persistent mount."""
        attributes: dict[str, Any] = {"threshold_percent": STORAGE_NEARLY_FULL_PERCENT}
        fullest = _fullest_persistent_mount(self.device_data)
        if fullest is not None:
            attributes["fullest_mount"] = fullest[0]["mount_point"]
            attributes["fullest_used_percent"] = fullest[1]
        return attributes


def discover_storage_sensors(
    coordinator: UnifiInsightsFacadeCoordinator,
    site_id: str,
    device_id: str,
    device_data: dict[str, Any],
    known_keys: set[tuple[Any, ...]],
) -> list[SensorEntity]:
    """Return storage sensors for mounts of this device not yet in ``known_keys``."""
    mounts = get_storage_mounts(device_data)
    labels = storage_mount_labels(mounts)
    entities: list[SensorEntity] = []
    for mount in mounts:
        mount_point = mount["mount_point"]
        volatile = is_volatile_storage_mount(mount)
        for description in STORAGE_SENSOR_TYPES:
            key = (site_id, device_id, "storage", mount_point, description.key)
            if key in known_keys:
                continue
            known_keys.add(key)
            entities.append(
                UnifiStorageSensor(
                    coordinator,
                    description,
                    site_id,
                    device_id,
                    mount_point=mount_point,
                    mount_label=labels[mount_point],
                    volatile=volatile,
                )
            )
    return entities


def discover_storage_binary_sensors(
    coordinator: UnifiInsightsFacadeCoordinator,
    site_id: str,
    device_id: str,
    device_data: dict[str, Any],
    known_keys: set[tuple[Any, ...]],
) -> list[BinarySensorEntity]:
    """Return the nearly-full sensor for a device with persistent storage."""
    key = (site_id, device_id, STORAGE_NEARLY_FULL_DESCRIPTION.key)
    if key in known_keys or not _persistent_mounts(device_data):
        return []
    known_keys.add(key)
    return [UnifiStorageNearlyFullBinarySensor(coordinator, site_id, device_id)]
