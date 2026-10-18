# Copyright 2026 UniFi Insights contributors
"""InnerSpace entity base class and placement sensor for UniFi Insights."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinators.facade import UnifiFacadeCoordinator

if TYPE_CHECKING:
    from homeassistant.helpers.device_registry import DeviceInfo
    from homeassistant.helpers.entity import EntityDescription
    from homeassistant.helpers.typing import StateType


class UnifiInnerSpaceEntity(CoordinatorEntity[UnifiFacadeCoordinator]):
    """Base class for UniFi InnerSpace entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        description: EntityDescription,
        record_id: str,
    ) -> None:
        """Initialize the InnerSpace entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._record_id = record_id
        self._attr_unique_id = f"innerspace_{record_id}_{description.key}"
        self._attr_device_info = self._build_device_info()

    @property
    def innerspace_record(self) -> dict[str, Any] | None:
        """Return the normalized InnerSpace record from coordinator data."""
        innerspace = self.coordinator.data.get("innerspace", {})
        if not isinstance(innerspace, dict):
            return None
        devices = innerspace.get("devices", {})
        if not isinstance(devices, dict):
            return None
        record = devices.get(self._record_id)
        return record if isinstance(record, dict) else None

    def _build_device_info(self) -> DeviceInfo:
        """
        Build device info for an InnerSpace record.

        Attaches to an existing Network or Protect device when MAC correlation
        is unambiguous, or creates a standalone InnerSpace device otherwise.
        Does not set ``suggested_area`` from floor-plan names.
        """
        record = self.innerspace_record or {}
        matched_domain = record.get("matched_domain")
        matched_site_id = record.get("matched_site_id")
        matched_device_id = record.get("matched_device_id")
        matched_protect_type = record.get("matched_protect_type")

        if (
            matched_domain == "network"
            and isinstance(matched_site_id, str)
            and isinstance(matched_device_id, str)
            and self.coordinator.get_device(matched_site_id, matched_device_id)
            is not None
        ):
            return {
                "identifiers": {(DOMAIN, f"{matched_site_id}_{matched_device_id}")},
            }

        if (
            matched_domain == "protect"
            and isinstance(matched_protect_type, str)
            and isinstance(matched_device_id, str)
        ):
            protect_section = self.coordinator.data.get("protect", {}).get(
                f"{matched_protect_type}s", {}
            )
            if (
                isinstance(protect_section, dict)
                and matched_device_id in protect_section
            ):
                return {
                    "identifiers": {
                        (
                            DOMAIN,
                            f"protect_{matched_protect_type}_{matched_device_id}",
                        )
                    },
                }

        name = (
            record.get("name")
            or record.get("model")
            or f"UniFi InnerSpace Device {self._record_id}"
        )
        device_info: DeviceInfo = {
            "identifiers": {(DOMAIN, f"innerspace_{self._record_id}")},
            "name": str(name),
            "manufacturer": MANUFACTURER,
            "model": record.get("model") or "UniFi InnerSpace Device",
        }
        if serial := record.get("serial"):
            device_info["serial_number"] = str(serial)
        return device_info

    @property
    def device_info(self) -> DeviceInfo:
        """Return current device info reflecting any updated MAC correlation."""
        device_info = self._build_device_info()
        self._attr_device_info = device_info
        return device_info

    def _reconcile_device_association(self, new_device_info: DeviceInfo) -> None:
        """Reconcile entity registry device_id when MAC correlation changes."""
        old_identifiers = (
            self._attr_device_info.get("identifiers")
            if self._attr_device_info
            else None
        )
        new_identifiers = new_device_info.get("identifiers")
        self._attr_device_info = new_device_info
        if (
            old_identifiers == new_identifiers
            or not new_identifiers
            or self.hass is None
            or not self.entity_id
            or self.coordinator.config_entry is None
        ):
            return
        ent_reg = er.async_get(self.hass)
        if ent_reg.async_get(self.entity_id) is None:
            return
        dev_reg = dr.async_get(self.hass)
        device = dev_reg.async_get_or_create(
            config_entry_id=self.coordinator.config_entry.entry_id,
            identifiers=new_identifiers,
            name=new_device_info.get("name"),
            manufacturer=new_device_info.get("manufacturer"),
            model=new_device_info.get("model"),
            serial_number=new_device_info.get("serial_number"),
        )
        ent_reg.async_update_entity(self.entity_id, device_id=device.id)

    @property
    def available(self) -> bool:
        """Return True if the InnerSpace coordinator and record are available."""
        return bool(
            self.coordinator.innerspace_available and self.innerspace_record is not None
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        new_device_info = self._build_device_info()
        self._reconcile_device_association(new_device_info)
        self.async_write_ha_state()


INNERSPACE_PLACEMENT_DESCRIPTION = SensorEntityDescription(
    key="placement",
    translation_key="innerspace_placement",
    device_class=SensorDeviceClass.ENUM,
    options=["placed", "unplaced", "unknown"],
    entity_category=EntityCategory.DIAGNOSTIC,
)


class UnifiInsightsInnerSpacePlacementSensor(UnifiInnerSpaceEntity, SensorEntity):
    """Diagnostic placement sensor for a UniFi InnerSpace record."""

    entity_description: SensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        description: SensorEntityDescription,
        record_id: str,
    ) -> None:
        """Initialize the InnerSpace placement sensor."""
        super().__init__(coordinator, description, record_id)

    @property
    def native_value(self) -> StateType:
        """Return the placement state ('placed', 'unplaced', or 'unknown')."""
        record = self.innerspace_record
        if not record:
            return "unknown"
        placement = record.get("placement_state")
        if placement in ("placed", "unplaced"):
            return str(placement)
        return "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return structured InnerSpace placement and correlation attributes."""
        record = self.innerspace_record or {}
        attrs: dict[str, Any] = {
            "innerspace_id": record.get("id") or self._record_id,
            "placement_state": record.get("placement_state"),
            "device_type": record.get("device_type"),
            "floor_plan_id": record.get("floor_plan_id"),
            "floor_plan_name": record.get("floor_plan_name"),
            "site_id": record.get("site_id"),
            "x": record.get("x"),
            "y": record.get("y"),
            "height": record.get("height"),
            "azimuth": record.get("azimuth"),
            "mount": record.get("mount"),
            "status": record.get("status"),
            "model": record.get("model"),
            "serial": record.get("serial"),
            "matched_domain": record.get("matched_domain"),
            "matched_site_id": record.get("matched_site_id"),
            "matched_device_id": record.get("matched_device_id"),
        }
        return {k: v for k, v in attrs.items() if v is not None}


def _discover_innerspace_sensors(
    coordinator: UnifiFacadeCoordinator,
    known_sensor_keys: set[tuple[Any, ...]],
    entities: list[SensorEntity],
) -> None:
    """Discover diagnostic placement sensors for UniFi InnerSpace devices."""
    innerspace = coordinator.data.get("innerspace", {})
    if not isinstance(innerspace, dict):
        return

    devices = innerspace.get("devices", {})
    if not isinstance(devices, dict):
        return

    for record_id, record in devices.items():
        if not isinstance(record_id, str) or not isinstance(record, dict):
            continue
        key = ("innerspace", record_id, INNERSPACE_PLACEMENT_DESCRIPTION.key)
        if key in known_sensor_keys:
            continue
        known_sensor_keys.add(key)
        entities.append(
            UnifiInsightsInnerSpacePlacementSensor(
                coordinator=coordinator,
                description=INNERSPACE_PLACEMENT_DESCRIPTION,
                record_id=record_id,
            )
        )
