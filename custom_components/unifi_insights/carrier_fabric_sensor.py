# Copyright (c) 2026 Ruaan Deysel
"""Sensor platform for UniFi Carrier Fabric integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)

from .carrier_fabric_entity import (
    UnifiCarrierFabricEntity,
    UnifiCarrierFabricSubscriberEntity,
)
from .const import (
    CONF_TRACK_SUBSCRIBERS,
    DEFAULT_TRACK_SUBSCRIBERS,
    DOMAIN,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .carrier_fabric_data import CarrierFabricConfigEntry
    from .coordinators.carrier_fabric import UnifiCarrierFabricCoordinator


@dataclass(frozen=True, kw_only=True)
class CarrierFabricAggregateSensorDescription(SensorEntityDescription):
    """Description for Carrier Fabric aggregate sensor."""

    value_fn: Callable[[dict[str, Any]], int]


AGGREGATE_SENSOR_DESCRIPTIONS: tuple[CarrierFabricAggregateSensorDescription, ...] = (
    CarrierFabricAggregateSensorDescription(
        key="total_subscribers",
        translation_key="total_subscribers",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda summary: summary.get("total_subscribers", 0),
    ),
    CarrierFabricAggregateSensorDescription(
        key="suspended_subscribers",
        translation_key="suspended_subscribers",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda summary: summary.get("suspended_subscribers", 0),
    ),
    CarrierFabricAggregateSensorDescription(
        key="subscribers_pending_assignment",
        translation_key="subscribers_pending_assignment",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda summary: summary.get("subscribers_by_state", {}).get(
            "pending_assignment", 0
        ),
    ),
    CarrierFabricAggregateSensorDescription(
        key="subscribers_provisioned",
        translation_key="subscribers_provisioned",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda summary: summary.get("subscribers_by_state", {}).get(
            "provisioned", 0
        ),
    ),
    CarrierFabricAggregateSensorDescription(
        key="subscribers_installed",
        translation_key="subscribers_installed",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda summary: summary.get("subscribers_by_state", {}).get(
            "installed", 0
        ),
    ),
    CarrierFabricAggregateSensorDescription(
        key="unassigned_subscribers",
        translation_key="unassigned_subscribers",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda summary: summary.get("unassigned_subscribers", 0),
    ),
    CarrierFabricAggregateSensorDescription(
        key="active_service_plans",
        translation_key="active_service_plans",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda summary: summary.get("active_service_plans", 0),
    ),
)


# State attribute name -> key in the coordinator data (always the API's camelCase).
_PLAN_ATTRIBUTES: Final = (
    ("status", "status"),
    ("download_mbps", "downloadMbps"),
    ("upload_mbps", "uploadMbps"),
    ("archived_at", "archivedAt"),
)
_PLAN_SPEED_ATTRIBUTES: Final = (
    ("download_mbps", "downloadMbps"),
    ("upload_mbps", "uploadMbps"),
)
_SUBSCRIBER_STATE_ATTRIBUTES: Final = (
    ("suspended_at", "suspendedAt"),
    ("activated_at", "activatedAt"),
)


def _attributes(
    data: dict[str, Any], mapping: tuple[tuple[str, str], ...]
) -> dict[str, Any]:
    """Return the set values of ``data`` under their state attribute names."""
    return {
        attribute: data[key] for attribute, key in mapping if data.get(key) is not None
    }


class UnifiCarrierFabricAggregateSensor(UnifiCarrierFabricEntity, SensorEntity):
    """Aggregate organisation-level sensor for UniFi Carrier Fabric."""

    entity_description: CarrierFabricAggregateSensorDescription

    def __init__(
        self,
        coordinator: UnifiCarrierFabricCoordinator,
        entry: CarrierFabricConfigEntry,
        description: CarrierFabricAggregateSensorDescription,
    ) -> None:
        """Initialize the aggregate sensor."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{self._entry_unique_id}_{description.key}"

    @property
    def native_value(self) -> int:
        """Return the sensor value."""
        summary = self.coordinator.data.get("summary", {})
        if isinstance(summary, dict):
            return self.entity_description.value_fn(summary)
        return 0


class UnifiCarrierFabricPlanSubscribersSensor(UnifiCarrierFabricEntity, SensorEntity):
    """Sensor for subscriber count on a service plan."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "plan_subscribers"

    def __init__(
        self,
        coordinator: UnifiCarrierFabricCoordinator,
        entry: CarrierFabricConfigEntry,
        plan_id: str,
    ) -> None:
        """Initialize the plan subscribers sensor."""
        super().__init__(coordinator, entry)
        self._plan_id = plan_id
        self._attr_unique_id = f"{self._entry_unique_id}_plan_{plan_id}_subscribers"
        self._update_placeholders()

    def _update_placeholders(self) -> None:
        """Update translation placeholders with plan name."""
        plan = self._plan_data or {}
        plan_name = plan.get("name") or self._plan_id
        self._attr_translation_placeholders = {"plan_name": str(plan_name)}

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        self._update_placeholders()
        super()._handle_coordinator_update()

    @property
    def _plan_data(self) -> dict[str, Any] | None:
        """Get service plan data from coordinator."""
        plans: dict[str, dict[str, Any]] = self.coordinator.data.get(
            "service_plans", {}
        )
        return plans.get(self._plan_id)

    @property
    def available(self) -> bool:
        """Return True if plan is present in coordinator data."""
        if not super().available:
            return False
        plans = self.coordinator.data.get("service_plans", {})
        return isinstance(plans, dict) and self._plan_id in plans

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Default disabled if archived."""
        plan = self._plan_data
        return not (
            plan and (plan.get("status") == "archived" or plan.get("archivedAt"))
        )

    @property
    def native_value(self) -> int:
        """Return the number of subscribers on this plan."""
        summary = self.coordinator.data.get("summary", {})
        return int(summary.get("subscribers_by_plan", {}).get(self._plan_id, 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return plan attributes."""
        plan = self._plan_data
        if not plan:
            return {}
        return _attributes(plan, _PLAN_ATTRIBUTES)


SUBSCRIBER_STATES: tuple[str, ...] = (
    "pending_assignment",
    "provisioned",
    "installed",
    "suspended",
    "unknown",
)


class UnifiCarrierFabricSubscriberStateSensor(
    UnifiCarrierFabricSubscriberEntity, SensorEntity
):
    """Sensor for subscriber service state."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "subscriber_state"

    def __init__(
        self,
        coordinator: UnifiCarrierFabricCoordinator,
        entry: CarrierFabricConfigEntry,
        subscriber_id: str,
    ) -> None:
        """Initialize the subscriber state sensor."""
        super().__init__(coordinator, entry, subscriber_id)
        self._attr_options = list(SUBSCRIBER_STATES)
        self._attr_unique_id = f"carrier_subscriber_{subscriber_id}_state"

    @property
    def native_value(self) -> str | None:
        """Return the subscriber state."""
        sub = self._subscriber_data
        if not sub:
            return None
        if sub.get("suspended") is True:
            return "suspended"
        state = sub.get("state")
        if state in SUBSCRIBER_STATES:
            return str(state)
        return "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return attributes excluding private fields."""
        sub = self._subscriber_data
        if not sub:
            return {}
        return _attributes(sub, _SUBSCRIBER_STATE_ATTRIBUTES)


class UnifiCarrierFabricSubscriberPlanSensor(
    UnifiCarrierFabricSubscriberEntity, SensorEntity
):
    """Sensor for subscriber service plan."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "subscriber_service_plan"

    def __init__(
        self,
        coordinator: UnifiCarrierFabricCoordinator,
        entry: CarrierFabricConfigEntry,
        subscriber_id: str,
    ) -> None:
        """Initialize the subscriber plan sensor."""
        super().__init__(coordinator, entry, subscriber_id)
        self._attr_unique_id = f"carrier_subscriber_{subscriber_id}_service_plan"

    @property
    def _plan(self) -> dict[str, Any] | None:
        """Return the subscriber's plan, or None if unassigned or not visible."""
        sub = self._subscriber_data
        plan_id = sub.get("planId") if sub else None
        if not plan_id:
            return None
        plans: dict[str, dict[str, Any]] = self.coordinator.data.get(
            "service_plans", {}
        )
        return plans.get(str(plan_id))

    @property
    def native_value(self) -> str | None:
        """Return the plan name, or None when the plan is unknown."""
        plan = self._plan
        if plan and plan.get("name"):
            return str(plan["name"])
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return plan speed attributes."""
        plan = self._plan
        if not plan:
            return {}
        return _attributes(plan, _PLAN_SPEED_ATTRIBUTES)


def reconcile_subscriber_registry(hass: HomeAssistant, entry_id: str) -> None:
    """Remove subscriber entities and devices when subscriber tracking is disabled."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    for entity in er.async_entries_for_config_entry(ent_reg, entry_id):
        if entity.unique_id and entity.unique_id.startswith("carrier_subscriber_"):
            ent_reg.async_remove(entity.entity_id)

    for device in dr.async_entries_for_config_entry(dev_reg, entry_id):
        for domain, identifier in device.identifiers:
            if domain == DOMAIN and identifier.startswith("carrier_subscriber_"):
                # Each device has one owning entry, so removing it is safe.
                dev_reg.async_remove_device(device.id)
                break


async def async_setup_carrier_fabric_sensors(
    hass: HomeAssistant,
    config_entry: CarrierFabricConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Carrier Fabric sensors."""
    carrier_data = config_entry.runtime_data
    coordinator = carrier_data.coordinator

    track_subscribers = config_entry.options.get(
        CONF_TRACK_SUBSCRIBERS, DEFAULT_TRACK_SUBSCRIBERS
    )

    if not track_subscribers:
        reconcile_subscriber_registry(hass, config_entry.entry_id)

    known_keys: set[str] = set()
    initial_entities: list[SensorEntity] = []

    # Ensure organization device is registered first by creating aggregate sensors
    for desc in AGGREGATE_SENSOR_DESCRIPTIONS:
        uid = f"{config_entry.unique_id}_{desc.key}"
        known_keys.add(uid)
        initial_entities.append(
            UnifiCarrierFabricAggregateSensor(coordinator, config_entry, desc)
        )

    setup_complete = False

    @callback
    def async_discover_sensors() -> None:
        """Discover and add new plan and subscriber sensors."""
        new_entities: list[SensorEntity] = []
        for plan_id in coordinator.data.get("service_plans", {}):
            plan_uid = f"{config_entry.unique_id}_plan_{plan_id}_subscribers"
            if plan_uid not in known_keys:
                known_keys.add(plan_uid)
                new_entities.append(
                    UnifiCarrierFabricPlanSubscribersSensor(
                        coordinator, config_entry, str(plan_id)
                    )
                )

        if track_subscribers:
            for sub_id in coordinator.data.get("subscribers", {}):
                state_uid = f"carrier_subscriber_{sub_id}_state"
                if state_uid not in known_keys:
                    known_keys.add(state_uid)
                    new_entities.append(
                        UnifiCarrierFabricSubscriberStateSensor(
                            coordinator, config_entry, str(sub_id)
                        )
                    )

                plan_uid = f"carrier_subscriber_{sub_id}_service_plan"
                if plan_uid not in known_keys:
                    known_keys.add(plan_uid)
                    new_entities.append(
                        UnifiCarrierFabricSubscriberPlanSensor(
                            coordinator, config_entry, str(sub_id)
                        )
                    )

        if not setup_complete:
            initial_entities.extend(new_entities)
        elif new_entities:
            async_add_entities(new_entities)

    async_discover_sensors()
    setup_complete = True
    async_add_entities(initial_entities)
    config_entry.async_on_unload(coordinator.async_add_listener(async_discover_sensors))
