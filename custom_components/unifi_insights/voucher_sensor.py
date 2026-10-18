# Copyright (c) 2026 Ruaan Deysel
"""Voucher sensor platform for UniFi Insights."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import StateType

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinators import UnifiFacadeCoordinator
from .coordinators.voucher_state import (
    _field,
    count_active_vouchers,
    parse_timestamp,
    voucher_site_ids,
)
from .entity import build_site_device_info
from .helpers import async_migrate_site_entity


@dataclass(frozen=True, kw_only=True)
class UnifiVoucherSensorEntityDescription(SensorEntityDescription):
    """Class describing UniFi voucher sensor entities."""

    value_fn: Callable[[Any, str], StateType | datetime]
    attrs_fn: Callable[[Any, str], dict[str, Any]] | None = None


def _get_latest_record(data: object, site_id: str) -> dict[str, Any] | None:
    """Safely return latest voucher record for a site."""
    if not isinstance(data, Mapping):
        return None
    latest_vouchers = data.get("latest_vouchers")
    if not isinstance(latest_vouchers, Mapping):
        return None
    record = latest_vouchers.get(site_id)
    if not isinstance(record, Mapping):
        return None
    return dict(record)


def _latest_code_value(data: object, site_id: str) -> StateType:
    """Return latest voucher code as string, or None."""
    record = _get_latest_record(data, site_id)
    if record is None:
        return None
    code = record.get("code")
    return str(code) if code is not None else None


def _latest_expiration_value(data: object, site_id: str) -> datetime | None:
    """Return latest voucher expiration datetime, or None."""
    record = _get_latest_record(data, site_id)
    if record is None:
        return None
    exp = _field(record, "expiresAt", "expires_at")
    return parse_timestamp(exp)


def _active_vouchers_value(data: object, site_id: str) -> int:
    """Return active vouchers count for a site."""
    if not isinstance(data, Mapping):
        return 0
    vouchers = data.get("vouchers")
    if not isinstance(vouchers, Mapping):
        return 0
    site_vouchers = vouchers.get(site_id)
    return count_active_vouchers(site_vouchers)


def _latest_attributes(data: object, site_id: str) -> dict[str, Any]:
    """Return attributes of the latest generated voucher."""
    record = _get_latest_record(data, site_id)
    if record is None:
        return {}

    attrs: dict[str, Any] = {}

    mappings: list[tuple[str, tuple[str, ...], bool]] = [
        ("voucher_id", ("id",), False),
        ("created_at", ("createdAt", "created_at"), True),
        ("activated_at", ("activatedAt", "activated_at"), True),
        ("expires_at", ("expiresAt", "expires_at"), True),
        ("expired", ("expired",), False),
        (
            "duration_minutes",
            ("timeLimitMinutes", "time_limit_minutes", "duration_minutes"),
            False,
        ),
        ("guest_limit", ("authorizedGuestLimit", "authorized_guest_limit"), False),
        ("guest_count", ("authorizedGuestCount", "authorized_guest_count"), False),
        (
            "data_limit_mb",
            ("dataUsageLimitMBytes", "data_usage_limit_mbytes", "data_limit_mb"),
            False,
        ),
        (
            "download_limit_kbps",
            ("rxRateLimitKbps", "rx_rate_limit_kbps", "download_limit_kbps"),
            False,
        ),
        (
            "upload_limit_kbps",
            ("txRateLimitKbps", "tx_rate_limit_kbps", "upload_limit_kbps"),
            False,
        ),
    ]

    for attr_key, source_keys, is_ts in mappings:
        val = _field(record, *source_keys)
        if val is None:
            continue
        if is_ts:
            parsed = parse_timestamp(val)
            if parsed is not None:
                attrs[attr_key] = parsed.isoformat()
        else:
            attrs[attr_key] = val

    return attrs


VOUCHER_SENSOR_TYPES: tuple[UnifiVoucherSensorEntityDescription, ...] = (
    UnifiVoucherSensorEntityDescription(
        key="latest_voucher_code",
        translation_key="latest_voucher_code",
        value_fn=_latest_code_value,
        attrs_fn=_latest_attributes,
    ),
    UnifiVoucherSensorEntityDescription(
        key="latest_voucher_expiration",
        translation_key="latest_voucher_expiration",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_latest_expiration_value,
    ),
    UnifiVoucherSensorEntityDescription(
        key="active_vouchers",
        translation_key="active_vouchers",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_active_vouchers_value,
    ),
)


class UnifiVoucherSensor(CoordinatorEntity[UnifiFacadeCoordinator], SensorEntity):
    """Representation of a UniFi hotspot voucher sensor."""

    _attr_has_entity_name = True
    entity_description: UnifiVoucherSensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        description: UnifiVoucherSensorEntityDescription,
        site_id: str,
        *,
        unique_id: str,
    ) -> None:
        """Initialize the voucher sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._site_id = site_id
        self._attr_unique_id = unique_id
        self._attr_device_info = DeviceInfo(
            **build_site_device_info(coordinator.data, site_id)  # type: ignore[typeddict-item]
        )

    @property
    def available(self) -> bool:
        """Return True if vouchers are available for this site."""
        return bool(self.coordinator.vouchers_available(self._site_id))

    @property
    def native_value(self) -> StateType | datetime:
        """Return the value reported by the description value_fn."""
        return self.entity_description.value_fn(self.coordinator.data, self._site_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes if attrs_fn is defined."""
        if self.entity_description.attrs_fn is not None:
            attrs = self.entity_description.attrs_fn(
                self.coordinator.data, self._site_id
            )
            return attrs or None
        return None


def discover_voucher_sensors(
    coordinator: UnifiFacadeCoordinator,
    known_sensor_keys: set[tuple[str, ...]],
    *,
    hass: HomeAssistant,
    entry: ConfigEntry,
    namespace: str | None,
) -> list[SensorEntity]:
    """Discover and instantiate voucher sensors for each configured site."""
    entities: list[SensorEntity] = []
    for site_id in voucher_site_ids(coordinator.data):
        for description in VOUCHER_SENSOR_TYPES:
            key = (site_id, description.key)
            if key in known_sensor_keys:
                continue
            unique_id = async_migrate_site_entity(
                hass, entry, "sensor", namespace, site_id, suffix=description.key
            )
            if unique_id is None:
                continue
            entity = UnifiVoucherSensor(
                coordinator, description, site_id, unique_id=unique_id
            )
            known_sensor_keys.add(key)
            entities.append(entity)
    return entities
