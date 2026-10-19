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

from .entity import UnifiInnerSpaceEntity

if TYPE_CHECKING:
    from homeassistant.helpers.typing import StateType

    from .coordinators.facade import UnifiInsightsFacadeCoordinator


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
        coordinator: UnifiInsightsFacadeCoordinator,
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
    coordinator: UnifiInsightsFacadeCoordinator,
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
