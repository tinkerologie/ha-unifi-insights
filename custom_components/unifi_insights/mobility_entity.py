# Copyright 2026 UniFi Insights contributors
"""UniFi Mobility workspace and router entities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfInformation,
    UnitOfTime,
)
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MANUFACTURER,
    MOBILITY_DEVICE_PREFIX,
    MOBILITY_ROUTER_STATES,
    MOBILITY_WORKSPACE_PREFIX,
)
from .coordinators.mobility import UnifiInsightsMobilityCoordinator

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.typing import StateType

_DEFAULT_ROUTER_NAME = "UniFi Mobile Router"
_WORKSPACE_MODEL = "UniFi Mobility workspace"
_CONNECTED = "CONNECTED"
# Enum sensor options: the API's values, lower-cased. "NULL" and empty
# strings are not options; they mean the value is unknown.
_ROUTER_STATES = [state.lower() for state in MOBILITY_ROUTER_STATES if state != "NULL"]
_WAN_SOURCES = ["lte", "wan", "wifiwan"]
_LTE_SIGNALS = ["no_signal", "poor", "fair", "strong"]
_VPN_STATES = ["connecting", "connected", "disconnected", "failed"]
_SUBSCRIPTION_STATES = ["active", "inactive", "pending", "failed"]
_SUBSCRIPTION_PLANS = ["free_trial", "1gb", "2gb", "5gb", "20gb", "cloud"]


def _text(field: str) -> Callable[[dict[str, Any]], StateType]:
    """Return a value function for a free-text field; empty text is unknown."""

    def value(device: dict[str, Any]) -> StateType:
        raw = device.get(field)
        return raw if isinstance(raw, str) and raw else None

    return value


def _number(field: str) -> Callable[[dict[str, Any]], StateType]:
    """Return a value function for a numeric field."""

    def value(device: dict[str, Any]) -> StateType:
        raw = device.get(field)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        return raw

    return value


def _enum(field: str, options: list[str]) -> Callable[[dict[str, Any]], StateType]:
    """
    Return a value function for an enum field.

    The API sends upper-case values and an empty string for "not set". Values
    this integration does not know yet are unknown, never an invalid option.
    """

    def value(device: dict[str, Any]) -> StateType:
        raw = device.get(field)
        state = raw.lower() if isinstance(raw, str) else None
        return state if state in options else None

    return value


def _data_limit(device: dict[str, Any]) -> StateType:
    """Return the billing-cycle data cap; -1 (unlimited) has no number."""
    raw = _number("cellular_data_limit_bytes")(device)
    return raw if isinstance(raw, (int, float)) and raw > 0 else None


def _uptime(device: dict[str, Any]) -> StateType:
    """Return uptime; the API reports 0 for a router that is not connected."""
    if device.get("state") != _CONNECTED:
        return None
    return _number("uptime_seconds")(device)


@dataclass(frozen=True, kw_only=True)
class UnifiMobilitySensorEntityDescription(SensorEntityDescription):
    """Describes a UniFi Mobility router sensor."""

    value_fn: Callable[[dict[str, Any]], StateType]


ROUTER_SENSORS: tuple[UnifiMobilitySensorEntityDescription, ...] = (
    UnifiMobilitySensorEntityDescription(
        key="state",
        translation_key="mobility_state",
        device_class=SensorDeviceClass.ENUM,
        options=_ROUTER_STATES,
        value_fn=_enum("state", _ROUTER_STATES),
    ),
    UnifiMobilitySensorEntityDescription(
        key="clients",
        translation_key="mobility_clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_number("client_count"),
    ),
    UnifiMobilitySensorEntityDescription(
        key="wan_source",
        translation_key="mobility_wan_source",
        device_class=SensorDeviceClass.ENUM,
        options=_WAN_SOURCES,
        value_fn=_enum("wan_source", _WAN_SOURCES),
    ),
    UnifiMobilitySensorEntityDescription(
        key="lte_signal",
        translation_key="mobility_lte_signal",
        device_class=SensorDeviceClass.ENUM,
        options=_LTE_SIGNALS,
        value_fn=_enum("lte_signal_level", _LTE_SIGNALS),
    ),
    UnifiMobilitySensorEntityDescription(
        key="cellular_data_usage",
        translation_key="mobility_cellular_data_usage",
        device_class=SensorDeviceClass.DATA_SIZE,
        # Resets at the start of each billing cycle.
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        value_fn=_number("cellular_data_usage_bytes"),
    ),
    UnifiMobilitySensorEntityDescription(
        key="vpn_status",
        translation_key="mobility_vpn_status",
        device_class=SensorDeviceClass.ENUM,
        options=_VPN_STATES,
        value_fn=_enum("vpn_status", _VPN_STATES),
    ),
    UnifiMobilitySensorEntityDescription(
        key="subscription_status",
        translation_key="mobility_subscription_status",
        device_class=SensorDeviceClass.ENUM,
        options=_SUBSCRIPTION_STATES,
        value_fn=_enum("subscription_status", _SUBSCRIPTION_STATES),
    ),
    UnifiMobilitySensorEntityDescription(
        key="memory_usage",
        translation_key="mobility_memory_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_number("memory_usage_percent"),
    ),
    UnifiMobilitySensorEntityDescription(
        key="uptime",
        translation_key="mobility_uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.DAYS,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        # Changes on every poll, so it is opt-in like the console uptime.
        entity_registry_enabled_default=False,
        value_fn=_uptime,
    ),
    UnifiMobilitySensorEntityDescription(
        key="firmware",
        translation_key="mobility_firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_text("firmware_version"),
    ),
    UnifiMobilitySensorEntityDescription(
        key="cellular_data_limit",
        translation_key="mobility_cellular_data_limit",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_data_limit,
    ),
    UnifiMobilitySensorEntityDescription(
        key="subscription_plan",
        translation_key="mobility_subscription_plan",
        device_class=SensorDeviceClass.ENUM,
        options=_SUBSCRIPTION_PLANS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_enum("subscription_plan", _SUBSCRIPTION_PLANS),
    ),
    UnifiMobilitySensorEntityDescription(
        key="wan_ip",
        translation_key="mobility_wan_ip",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_text("wan_ip"),
    ),
    UnifiMobilitySensorEntityDescription(
        key="isp",
        translation_key="mobility_isp",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_text("isp"),
    ),
)


@dataclass(frozen=True, kw_only=True)
class UnifiMobilityWorkspaceSensorEntityDescription(SensorEntityDescription):
    """Describes a UniFi Mobility workspace sensor."""

    value_fn: Callable[[dict[str, Any], dict[str, dict[str, Any]]], StateType]


WORKSPACE_SENSORS: tuple[UnifiMobilityWorkspaceSensorEntityDescription, ...] = (
    UnifiMobilityWorkspaceSensorEntityDescription(
        key="devices",
        translation_key="mobility_workspace_devices",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda workspace, _devices: len(workspace["device_ids"]),
    ),
    UnifiMobilityWorkspaceSensorEntityDescription(
        key="online_devices",
        translation_key="mobility_workspace_online_devices",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda workspace, devices: sum(
            1
            for device_id in workspace["device_ids"]
            if devices.get(device_id, {}).get("state") == _CONNECTED
        ),
    ),
)

CONNECTIVITY_DESCRIPTION = BinarySensorEntityDescription(
    key="connectivity",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
)

LOCATION_DESCRIPTION = EntityDescription(
    key="location",
    translation_key="mobility_location",
)


def _workspace_device_info(workspace_id: str, name: str | None) -> DeviceInfo:
    """Return the service device that groups a workspace's routers."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{MOBILITY_WORKSPACE_PREFIX}{workspace_id}")},
        name=name or _WORKSPACE_MODEL,
        manufacturer=MANUFACTURER,
        model=_WORKSPACE_MODEL,
        entry_type=DeviceEntryType.SERVICE,
    )


class UnifiMobilityRouterEntity(CoordinatorEntity[UnifiInsightsMobilityCoordinator]):
    """Base class for entities of one Mobility router."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: UnifiInsightsMobilityCoordinator,
        description: EntityDescription,
        device_id: str,
        workspace_device_id: str,
    ) -> None:
        """Initialize the router entity under its workspace's device."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_id = device_id
        # Keyed by the router alone, so moving it to another workspace keeps
        # its entities and history.
        self._attr_unique_id = f"{MOBILITY_DEVICE_PREFIX}{device_id}_{description.key}"
        device = self.router or {}
        name = device.get("name") or device.get("model") or _DEFAULT_ROUTER_NAME
        # No MAC connection: a Mobility router is its own device, and must not
        # merge into a Network device that happens to share the address.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{MOBILITY_DEVICE_PREFIX}{device_id}")},
            name=str(name),
            manufacturer=MANUFACTURER,
            model=str(device.get("model") or _DEFAULT_ROUTER_NAME),
            sw_version=device.get("firmware_version") or None,
            via_device_id=workspace_device_id,
        )

    @property
    def router(self) -> dict[str, Any] | None:
        """Return the router's latest record, if Mobility still reports it."""
        device = self.coordinator.data["devices"].get(self._device_id)
        return device if isinstance(device, dict) else None

    @property
    def available(self) -> bool:
        """Return True if the last poll succeeded and still lists the router."""
        return super().available and self.router is not None


class UnifiMobilitySensor(UnifiMobilityRouterEntity, SensorEntity):
    """A value reported for one Mobility router."""

    entity_description: UnifiMobilitySensorEntityDescription

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        router = self.router
        return self.entity_description.value_fn(router) if router else None


class UnifiMobilityConnectivitySensor(UnifiMobilityRouterEntity, BinarySensorEntity):
    """Whether a Mobility router is connected to the cloud."""

    @property
    def is_on(self) -> bool | None:
        """Return True if the router is connected."""
        router = self.router
        return router.get("state") == _CONNECTED if router else None


class UnifiMobilityLocationTracker(UnifiMobilityRouterEntity, TrackerEntity):
    """GPS position of a Mobility router."""

    _attr_source_type = SourceType.GPS

    def _coordinate(self, key: str) -> float | None:
        """Return a coordinate of the last GPS fix, if there is one."""
        router = self.router or {}
        location = router.get("location")
        value = location.get(key) if isinstance(location, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    @property
    def latitude(self) -> float | None:
        """Return the router's latitude."""
        return self._coordinate("latitude")

    @property
    def longitude(self) -> float | None:
        """Return the router's longitude."""
        return self._coordinate("longitude")


class UnifiMobilityWorkspaceSensor(
    CoordinatorEntity[UnifiInsightsMobilityCoordinator], SensorEntity
):
    """A router count for one Mobility workspace."""

    _attr_has_entity_name = True
    entity_description: UnifiMobilityWorkspaceSensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiInsightsMobilityCoordinator,
        description: UnifiMobilityWorkspaceSensorEntityDescription,
        workspace_id: str,
    ) -> None:
        """Initialize the workspace sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._workspace_id = workspace_id
        self._attr_unique_id = (
            f"{MOBILITY_WORKSPACE_PREFIX}{workspace_id}_{description.key}"
        )
        workspace = self.workspace or {}
        self._attr_device_info = _workspace_device_info(
            workspace_id, workspace.get("name")
        )

    @property
    def workspace(self) -> dict[str, Any] | None:
        """Return the workspace while it is active."""
        workspace = self.coordinator.data["workspaces"].get(self._workspace_id)
        if isinstance(workspace, dict) and workspace.get("status") == "ACTIVE":
            return workspace
        return None

    @property
    def available(self) -> bool:
        """Return True if the last poll succeeded and the workspace is active."""
        return super().available and self.workspace is not None

    @property
    def native_value(self) -> StateType:
        """Return the router count."""
        workspace = self.workspace
        if workspace is None:
            return None
        return self.entity_description.value_fn(
            workspace, self.coordinator.data["devices"]
        )


@callback
def _async_add_router_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: UnifiInsightsMobilityCoordinator,
    async_add_entities: AddEntitiesCallback,
    build: Callable[[str, str], list[Entity]],
) -> None:
    """Add a platform's router entities now and for routers found later."""
    known: set[str] = set()
    device_registry = dr.async_get(hass)

    @callback
    def async_discover() -> None:
        entities: list[Entity] = []
        for device_id, device in coordinator.data["devices"].items():
            if device_id in known:
                continue
            known.add(device_id)
            # A router device links to its workspace's device, which must be
            # registered first. Platforms set up in any order, so whichever
            # adds a router first registers the workspace.
            workspace_id = device.get("workspace_id")
            workspace = coordinator.data["workspaces"].get(workspace_id) or {}
            workspace_device = device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                **_workspace_device_info(workspace_id, workspace.get("name")),
            )
            entities.extend(build(device_id, workspace_device.id))
        if entities:
            async_add_entities(entities)

    async_discover()
    entry.async_on_unload(coordinator.async_add_listener(async_discover))


@callback
def async_setup_mobility_sensors(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: UnifiInsightsMobilityCoordinator,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Mobility workspace and router sensors."""
    known_workspaces: set[str] = set()

    @callback
    def async_discover_workspaces() -> None:
        entities: list[Entity] = []
        for workspace_id, workspace in coordinator.data["workspaces"].items():
            if workspace_id in known_workspaces or workspace.get("status") != "ACTIVE":
                continue
            known_workspaces.add(workspace_id)
            entities.extend(
                UnifiMobilityWorkspaceSensor(coordinator, description, workspace_id)
                for description in WORKSPACE_SENSORS
            )
        if entities:
            async_add_entities(entities)

    async_discover_workspaces()
    entry.async_on_unload(coordinator.async_add_listener(async_discover_workspaces))
    _async_add_router_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        lambda device_id, workspace_device_id: [
            UnifiMobilitySensor(
                coordinator, description, device_id, workspace_device_id
            )
            for description in ROUTER_SENSORS
        ],
    )


@callback
def async_setup_mobility_binary_sensors(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: UnifiInsightsMobilityCoordinator,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Mobility router connectivity sensors."""
    _async_add_router_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        lambda device_id, workspace_device_id: [
            UnifiMobilityConnectivitySensor(
                coordinator, CONNECTIVITY_DESCRIPTION, device_id, workspace_device_id
            )
        ],
    )


@callback
def async_setup_mobility_trackers(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: UnifiInsightsMobilityCoordinator,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Mobility router GPS trackers."""
    _async_add_router_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        lambda device_id, workspace_device_id: [
            UnifiMobilityLocationTracker(
                coordinator, LOCATION_DESCRIPTION, device_id, workspace_device_id
            )
        ],
    )
