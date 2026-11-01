# Copyright 2026 UniFi Insights contributors
"""Support for UniFi Insights sensors."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, TypeIs, cast

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    EntityCategory,
    UnitOfDataRate,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfInformation,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CarrierFabricData
from .console_identity import async_migrate_site_entity, resolve_console_namespace
from .const import (
    ATTR_ARM_PROFILE_ID,
    ATTR_NIGHT_PROFILE_ID,
    ATTR_NVR_ID,
    ATTR_NVR_NAME,
    ATTR_NVR_STORAGE_AVAILABLE,
    ATTR_NVR_STORAGE_TOTAL,
    ATTR_NVR_STORAGE_USED,
    ATTR_NVR_STORAGE_USED_PERCENT,
    ATTR_NVR_VERSION,
    ATTR_SENSOR_BATTERY,
    ATTR_SENSOR_BATTERY_LOW,
    ATTR_SENSOR_HUMIDITY_VALUE,
    ATTR_SENSOR_ID,
    ATTR_SENSOR_LIGHT_VALUE,
    ATTR_SENSOR_NAME,
    ATTR_SENSOR_TEMPERATURE_VALUE,
    ATTR_THREAD_CHANNEL,
    ATTR_THREAD_EXTENDED_PAN_ID,
    ATTR_THREAD_NETWORK_NAME,
    ATTR_THREAD_PAN_ID,
    CONF_TRACK_SUBSCRIBERS,
    DEFAULT_TRACK_SUBSCRIBERS,
    DEVICE_TYPE_FOB,
    DEVICE_TYPE_NVR,
    DEVICE_TYPE_SENSOR,
    DOMAIN,
    MANUFACTURER,
    MOBILITY_ROUTER_CONNECTED,
    MOBILITY_ROUTER_STATES,
    MOBILITY_WORKSPACE_PREFIX,
)
from .coordinators import UnifiInsightsFacadeCoordinator
from .coordinators.mobility import UnifiInsightsMobilityCoordinator
from .data_transforms import (
    THREAD_DEVICE_TYPES,
    NetworkDeviceFacts,
    _field,
    always_true,
    bytes_to_bits_per_second,
    count_active_vouchers,
    device_facts,
    find_storage_mount,
    get_storage_mounts,
    has_keypad,
    has_thread_network,
    is_volatile_storage_mount,
    latest_voucher,
    nested_dict,
    network_device,
    network_device_stats,
    normalize_nvr_storage,
    owner_available,
    parse_timestamp,
    present_attributes,
    protect_collection,
    rule_record,
    section_by_site,
    site_section,
    site_section_available,
    storage_mount_labels,
    storage_used_percent,
    thread_network,
    voucher_site_ids,
)
from .data_transforms import get_port_label as _get_port_label
from .entity import (
    UnifiCarrierFabricEntity,
    UnifiCarrierFabricSubscriberEntity,
    UnifiInnerSpaceEntity,
    UnifiInsightsEntity,
    UnifiMobilityRouterEntity,
    UnifiProtectEntity,
    UnifiSiteEntity,
    async_add_mobility_router_entities,
    carrier_organisation_device_fields,
    device_has_feature,
    first_not_none,
    get_field,
    mobility_workspace_device_info,
    protect_security_devices,
)
from .entity import (
    get_client_type as _get_client_type,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.typing import StateType

    from . import CarrierFabricConfigEntry, UnifiInsightsConfigEntry
    from .coordinators.carrier_fabric import UnifiInsightsCarrierFabricCoordinator


_LOGGER = logging.getLogger(__name__)


# Coordinator handles updates centrally (Gold/Platinum requirement)
PARALLEL_UPDATES = 0

# A port sensor's stats carry no reading for it (as opposed to a bad one).
_NO_STATS: Final = object()


@dataclass(frozen=True, kw_only=True)
class UnifiInsightsSensorEntityDescription(SensorEntityDescription):
    """Class describing UniFi Insights sensor entities."""

    value_fn: Callable[[dict[str, Any]], StateType] | None = None
    # Reads the device's published read-model facts instead of a raw record.
    facts_fn: Callable[[NetworkDeviceFacts], StateType] | None = None
    # Device feature required for this sensor (e.g., 'switching', 'accessPoint')
    # If None, sensor applies to all devices
    required_feature: str | None = None


@dataclass(frozen=True, kw_only=True)
class UnifiProtectSensorEntityDescription(SensorEntityDescription):
    """Class describing UniFi Protect sensor entities."""

    value_fn: Callable[[dict[str, Any]], StateType] | None = None
    device_type: str | None = None
    capability_fn: Callable[[dict[str, Any]], bool] | None = None
    # Attributes from the device record; None uses the built-in attributes.
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def format_uptime(seconds: int | None) -> str | None:
    """Format uptime into days, hours, minutes."""
    if seconds is None:
        return None

    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60

    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def bytes_to_megabits(bytes_per_sec: float | None) -> float | None:
    """Convert bytes per second to megabits per second."""
    if bytes_per_sec is None:
        return None
    return round(bytes_per_sec * 8 / 1_000_000, 2)


def bytes_to_bits(bytes_per_sec: float | None) -> float | None:
    """Convert bytes per second to bits per second."""
    return bytes_to_bits_per_second(bytes_per_sec)


def _get_temperature_entry_value(
    temperatures: list[dict[str, Any]],
    preferred_name: str,
) -> float | int | None:
    """Return a temperature value from a named legacy device temperature entry."""
    preferred_name_lower = preferred_name.lower()
    for temperature in temperatures:
        name = temperature.get("name")
        value = temperature.get("value")
        if (
            isinstance(name, str)
            and preferred_name_lower in name.lower()
            and isinstance(value, (int, float))
        ):
            return value

    return None


def get_network_device_temperature(device: dict[str, Any]) -> float | int | None:
    """Extract a network device temperature from merged device data."""
    direct_temperature = get_field(
        device,
        "generalTemperature",
        "general_temperature",
        "temperature",
    )
    if isinstance(direct_temperature, (int, float)):
        return direct_temperature

    temperatures = get_field(device, "temperatures", default=[])
    if not isinstance(temperatures, list):
        return None

    normalized_temperatures = [
        item
        for item in temperatures
        if isinstance(item, dict) and item.get("value") is not None
    ]
    if not normalized_temperatures:
        return None

    for preferred_name in ("local", "cpu", "phy"):
        value = _get_temperature_entry_value(normalized_temperatures, preferred_name)
        if value is not None:
            return value

    first_value = normalized_temperatures[0].get("value")
    return first_value if isinstance(first_value, (int, float)) else None


# Sensor descriptions for UniFi Protect sensors
def _has_protect_stat(
    stat_key: str, flat_key: str | None = None
) -> Callable[[dict[str, Any]], bool]:
    """Return a capability check for a Protect sensor measurement."""

    def check(data: dict[str, Any]) -> bool:
        stats = data.get("stats")
        if isinstance(stats, dict):
            entry = stats.get(stat_key)
            if isinstance(entry, dict) and entry.get("value") is not None:
                return True
        return bool(flat_key and data.get(flat_key) is not None)

    return check


PROTECT_SENSOR_TYPES: tuple[UnifiProtectSensorEntityDescription, ...] = (
    # Temperature sensor
    UnifiProtectSensorEntityDescription(
        key="temperature",
        translation_key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda sensor: (
            sensor.get("stats", {}).get("temperature", {}).get("value")
        ),
        device_type=DEVICE_TYPE_SENSOR,
        capability_fn=_has_protect_stat("temperature", "temperature"),
    ),
    # Humidity sensor
    UnifiProtectSensorEntityDescription(
        key="humidity",
        translation_key="humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda sensor: (
            sensor.get("stats", {}).get("humidity", {}).get("value")
        ),
        device_type=DEVICE_TYPE_SENSOR,
        capability_fn=_has_protect_stat("humidity", "humidity"),
    ),
    # Light sensor
    UnifiProtectSensorEntityDescription(
        key="light",
        translation_key="light",
        native_unit_of_measurement=LIGHT_LUX,
        device_class=SensorDeviceClass.ILLUMINANCE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda sensor: sensor.get("stats", {}).get("light", {}).get("value"),
        device_type=DEVICE_TYPE_SENSOR,
        capability_fn=_has_protect_stat("light", "lightValue"),
    ),
    # Battery sensor
    UnifiProtectSensorEntityDescription(
        key="battery",
        translation_key="battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda sensor: sensor.get("batteryStatus", {}).get("percentage"),
        device_type=DEVICE_TYPE_SENSOR,
    ),
)


def _bytes_to_gb(bytes_value: int | None) -> float | None:
    """Convert bytes to gigabytes."""
    if bytes_value is None:
        return None
    return round(bytes_value / (1024**3), 2)


def _has_storage_info(nvr_data: dict[str, Any]) -> bool:
    """Return True when at least one valid capacity signal exists."""
    storage = normalize_nvr_storage(nvr_data)
    return any(
        value is not None
        for value in (
            storage.used_bytes,
            storage.total_bytes,
            storage.available_bytes,
            storage.used_percent,
        )
    )


def _get_storage_bytes(nvr_data: dict[str, Any], field: str) -> int | None:
    """Read a validated byte signal from the shared normalizer."""
    storage = normalize_nvr_storage(nvr_data)
    return {
        "used": storage.used_bytes,
        "total": storage.total_bytes,
        "available": storage.available_bytes,
    }.get(field)


def _calculate_storage_percent(nvr_data: dict[str, Any]) -> float | None:
    """Read the normalized percentage, including percentage-only telemetry."""
    return normalize_nvr_storage(nvr_data).used_percent


def _calculate_storage_available(nvr_data: dict[str, Any]) -> float | None:
    """Read available storage using the existing GB/base-2 conversion."""
    return _bytes_to_gb(normalize_nvr_storage(nvr_data).available_bytes)


# Sensor descriptions for UniFi Protect NVR
NVR_SENSOR_TYPES: tuple[UnifiProtectSensorEntityDescription, ...] = (
    # Storage Used
    UnifiProtectSensorEntityDescription(
        key="storage_used",
        translation_key="storage_used",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda nvr: _bytes_to_gb(_get_storage_bytes(nvr, "used")),
        device_type=DEVICE_TYPE_NVR,
    ),
    # Storage Total
    UnifiProtectSensorEntityDescription(
        key="storage_total",
        translation_key="storage_total",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda nvr: _bytes_to_gb(_get_storage_bytes(nvr, "total")),
        device_type=DEVICE_TYPE_NVR,
    ),
    # Storage Available
    UnifiProtectSensorEntityDescription(
        key="storage_available",
        translation_key="storage_available",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_calculate_storage_available,
        device_type=DEVICE_TYPE_NVR,
    ),
    # Storage Used Percentage
    UnifiProtectSensorEntityDescription(
        key="storage_used_percent",
        translation_key="storage_used_percent",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_calculate_storage_percent,
        device_type=DEVICE_TYPE_NVR,
    ),
)

# Sensor descriptions for UniFi Insights sensors
SENSOR_TYPES: tuple[UnifiInsightsSensorEntityDescription, ...] = (
    UnifiInsightsSensorEntityDescription(
        key="cpu_usage",
        translation_key="cpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        facts_fn=lambda facts: facts.cpu_pct,
    ),
    UnifiInsightsSensorEntityDescription(
        key="memory_usage",
        translation_key="memory_usage",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        facts_fn=lambda facts: facts.memory_pct,
    ),
    UnifiInsightsSensorEntityDescription(
        key="uptime",
        translation_key="uptime",
        device_class=None,
        # Diagnostic - uptime is informational/troubleshooting data
        entity_category=EntityCategory.DIAGNOSTIC,
        # Gold: Disable diagnostic entities by default
        entity_registry_enabled_default=False,
        facts_fn=lambda facts: format_uptime(facts.uptime_s),
    ),
    UnifiInsightsSensorEntityDescription(
        key="tx_rate",
        translation_key="tx_rate",
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=1,
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        # Already bits/s; see NetworkDeviceFacts for the unit evidence (#337).
        facts_fn=lambda facts: facts.tx_bps,
    ),
    UnifiInsightsSensorEntityDescription(
        key="rx_rate",
        translation_key="rx_rate",
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=1,
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        facts_fn=lambda facts: facts.rx_bps,
    ),
    UnifiInsightsSensorEntityDescription(
        key="poe_total_power",
        translation_key="poe_total_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        # Only show on switch devices (devices with 'switching' feature)
        required_feature="switching",
        value_fn=lambda stats: get_field(
            stats,
            "poe_total_w",
            "poeTotalW",
            "total_used_power",
            "totalUsedPower",
        ),
    ),
    UnifiInsightsSensorEntityDescription(
        key="firmware_version",
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        # Gold: Disable diagnostic entities by default
        entity_registry_enabled_default=False,
        value_fn=lambda device: get_field(
            device, "firmwareVersion", "firmware_version", "version"
        ),
    ),
    UnifiInsightsSensorEntityDescription(
        key="general_temperature",
        translation_key="general_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=get_network_device_temperature,
    ),
    UnifiInsightsSensorEntityDescription(
        key="wired_clients",
        translation_key="wired_clients",
        state_class=SensorStateClass.MEASUREMENT,
        # Only show on switch devices (devices with 'switching' feature)
        required_feature="switching",
        value_fn=lambda stats: len(
            [c for c in stats.get("clients", []) if _get_client_type(c) == "WIRED"]
        ),
    ),
    UnifiInsightsSensorEntityDescription(
        key="wireless_clients",
        translation_key="wireless_clients",
        state_class=SensorStateClass.MEASUREMENT,
        # Only show on access point devices (devices with 'accessPoint' feature)
        required_feature="accessPoint",
        value_fn=lambda stats: len(
            [c for c in stats.get("clients", []) if _get_client_type(c) == "WIRELESS"]
        ),
    ),
    UnifiInsightsSensorEntityDescription(
        key="ac_power_consumption",
        translation_key="ac_power_consumption",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: get_field(
            device,
            "ac_power_consumption",
            "acPowerConsumption",
            "outlet_ac_power_consumption",
            "outletAcPowerConsumption",
        ),
    ),
    UnifiInsightsSensorEntityDescription(
        key="ac_power_budget",
        translation_key="ac_power_budget",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: get_field(
            device,
            "ac_power_budget",
            "acPowerBudget",
            "outlet_ac_power_budget",
            "outletAcPowerBudget",
        ),
    ),
)

# Outlet metering sensor descriptions for UniFi PDUs and smart power strips
OUTLET_SENSOR_TYPES: tuple[UnifiInsightsSensorEntityDescription, ...] = (
    # Outlet Power (W)
    UnifiInsightsSensorEntityDescription(
        key="power",
        translation_key="outlet_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda outlet: (
            outlet.get("outlet_power")
            if outlet.get("outlet_power") is not None
            else outlet.get("outletPower", outlet.get("power"))
        ),
    ),
    # Outlet Voltage (V)
    UnifiInsightsSensorEntityDescription(
        key="voltage",
        translation_key="outlet_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda outlet: (
            outlet.get("outlet_voltage")
            if outlet.get("outlet_voltage") is not None
            else outlet.get("outletVoltage", outlet.get("voltage"))
        ),
    ),
    # Outlet Current (A)
    UnifiInsightsSensorEntityDescription(
        key="current",
        translation_key="outlet_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda outlet: (
            outlet.get("outlet_current")
            if outlet.get("outlet_current") is not None
            else outlet.get("outletCurrent", outlet.get("current"))
        ),
    ),
    # Outlet Power Factor
    UnifiInsightsSensorEntityDescription(
        key="power_factor",
        translation_key="outlet_power_factor",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda outlet: (
            outlet.get("outlet_power_factor")
            if outlet.get("outlet_power_factor") is not None
            else outlet.get("outletPowerFactor", outlet.get("power_factor"))
        ),
    ),
)


def _outlet_has_metering(outlet: dict[str, Any]) -> bool:
    """Return True when an outlet has metering capability or telemetry."""
    caps = outlet.get("outlet_caps") or outlet.get("outletCaps")
    if caps is not None:
        try:
            if int(caps) & 2:
                return True
        except (TypeError, ValueError):  # fmt: skip
            pass
    for key in (
        "outlet_power",
        "outletPower",
        "power",
        "outlet_voltage",
        "outletVoltage",
        "voltage",
        "outlet_current",
        "outletCurrent",
        "current",
        "outlet_power_factor",
        "outletPowerFactor",
        "power_factor",
    ):
        if outlet.get(key) is not None:
            return True
    return False


# Port sensor descriptions for UniFi switches
# These are templates - actual sensors are created dynamically per port
PORT_SENSOR_TYPES: tuple[UnifiInsightsSensorEntityDescription, ...] = (
    # PoE Power Consumption
    UnifiInsightsSensorEntityDescription(
        key="port_poe_power",
        translation_key="port_poe_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,  # Changed from DIAGNOSTIC to make visible by default
        value_fn=lambda port: first_not_none(
            get_field(port, "poe_power_w"),
            get_field(port, "poe", default={}).get("power"),
            get_field(port, "poe", default={}).get("watts"),
        ),
    ),
    # Port Speed
    UnifiInsightsSensorEntityDescription(
        key="port_speed",
        translation_key="port_speed",
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        # Diagnostic - port speed is configuration/status, not monitored data
        entity_category=EntityCategory.DIAGNOSTIC,
        # Gold: Disable diagnostic entities by default
        entity_registry_enabled_default=False,
        value_fn=lambda port: (
            get_field(port, "speedMbps", "speed_mbps", "speed")
            if get_field(port, "state", "status", default="").upper() == "UP"
            else 0
        ),
    ),
    # Port TX
    UnifiInsightsSensorEntityDescription(
        key="port_tx_bytes",
        translation_key="port_tx_bytes",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=1,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=None,  # Changed from DIAGNOSTIC to make visible by default
        value_fn=lambda port: get_field(
            get_field(port, "stats", default={}), "txBytes", "tx_bytes", default=0
        ),
    ),
    # Port RX
    UnifiInsightsSensorEntityDescription(
        key="port_rx_bytes",
        translation_key="port_rx_bytes",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=1,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=None,  # Changed from DIAGNOSTIC to make visible by default
        value_fn=lambda port: get_field(
            get_field(port, "stats", default={}), "rxBytes", "rx_bytes", default=0
        ),
    ),
)

# Per-port throughput rate sensor descriptions (computed from byte count deltas)
PORT_RATE_SENSOR_TYPES: tuple[UnifiInsightsSensorEntityDescription, ...] = (
    # Port TX Rate
    UnifiInsightsSensorEntityDescription(
        key="port_tx_rate",
        translation_key="port_tx_rate",
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=1,
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        value_fn=lambda _port: None,  # Handled in entity native_value
    ),
    # Port RX Rate
    UnifiInsightsSensorEntityDescription(
        key="port_rx_rate",
        translation_key="port_rx_rate",
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=1,
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        value_fn=lambda _port: None,  # Handled in entity native_value
    ),
)

# SFP sensor descriptions for SFP/SFP+ ports
SFP_SENSOR_TYPES: tuple[UnifiInsightsSensorEntityDescription, ...] = (
    # SFP Module Model
    UnifiInsightsSensorEntityDescription(
        key="port_sfp_module",
        translation_key="port_sfp_module",
        device_class=None,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda port: port.get("sfp_part"),
    ),
    # SFP Vendor
    UnifiInsightsSensorEntityDescription(
        key="port_sfp_vendor",
        translation_key="port_sfp_vendor",
        device_class=None,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda port: port.get("sfp_vendor"),
    ),
    # SFP Compliance (DAC, SR, LR, etc.)
    UnifiInsightsSensorEntityDescription(
        key="port_sfp_compliance",
        translation_key="port_sfp_compliance",
        device_class=None,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda port: port.get("sfp_compliance"),
    ),
    # SFP Serial
    UnifiInsightsSensorEntityDescription(
        key="port_sfp_serial",
        translation_key="port_sfp_serial",
        device_class=None,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda port: port.get("sfp_serial"),
    ),
)


# WAN sensor descriptions for UniFi gateways
WAN_SENSOR_TYPES: tuple[UnifiInsightsSensorEntityDescription, ...] = (
    # WAN IP Address
    UnifiInsightsSensorEntityDescription(
        key="wan_ip",
        translation_key="wan_ip",
        device_class=None,
        entity_category=EntityCategory.DIAGNOSTIC,
        # Gold: Disable diagnostic entities by default
        entity_registry_enabled_default=False,
        value_fn=lambda device: get_field(device, "ipAddress", "ip_address", "ip"),
    ),
    # WAN Uptime
    UnifiInsightsSensorEntityDescription(
        key="wan_uptime",
        translation_key="wan_uptime",
        device_class=None,
        entity_category=EntityCategory.DIAGNOSTIC,
        # Gold: Disable diagnostic entities by default
        entity_registry_enabled_default=False,
        value_fn=lambda stats: format_uptime(
            get_field(stats, "uptimeSec", "uptime_sec", "uptime_seconds", "uptime")
        ),
    ),
)


# Site-level client count sensor descriptions
SITE_CLIENT_SENSOR_TYPES: tuple[UnifiInsightsSensorEntityDescription, ...] = (
    UnifiInsightsSensorEntityDescription(
        key="site_total_clients",
        translation_key="site_total_clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=len,
    ),
    UnifiInsightsSensorEntityDescription(
        key="site_wired_clients",
        translation_key="site_wired_clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda clients: len(
            [c for c in clients.values() if _get_client_type(c) == "WIRED"]
        ),
    ),
    UnifiInsightsSensorEntityDescription(
        key="site_wireless_clients",
        translation_key="site_wireless_clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda clients: len(
            [c for c in clients.values() if _get_client_type(c) == "WIRELESS"]
        ),
    ),
)


@callback
def _migrate_sensor_units(
    hass: HomeAssistant,
    config_entry: UnifiInsightsConfigEntry,
) -> None:
    """
    Migrate existing sensor entities to use updated suggested units.

    Sets the refresh_initial_entity_options flag on sensor entities whose
    stored suggested_unit_of_measurement differs from the current description.
    HA will then re-evaluate and apply the new suggested unit on next load.
    """
    registry = er.async_get(hass)

    # Build a lookup of current suggested units from sensor descriptions
    expected_units: dict[str, str | None] = {}
    for desc in (
        *SENSOR_TYPES,
        *PORT_SENSOR_TYPES,
        *PORT_RATE_SENSOR_TYPES,
        *OUTLET_SENSOR_TYPES,
        *SITE_INTERNET_ACTIVITY_SENSOR_TYPES,
    ):
        if desc.suggested_unit_of_measurement is not None:
            expected_units[desc.key] = str(desc.suggested_unit_of_measurement)

    for entity_entry in er.async_entries_for_config_entry(
        registry, config_entry.entry_id
    ):
        if entity_entry.domain != "sensor":
            continue

        # Match entity to a description key via unique_id suffix
        unique_id = entity_entry.unique_id or ""
        matched_key: str | None = None
        for key in expected_units:
            if unique_id.endswith(f"_{key}"):
                matched_key = key
                break

        if matched_key is None:
            continue

        # Check if stored suggested unit already matches
        private_opts: dict[str, Any] = entity_entry.options.get("sensor.private", {})
        stored_suggested = private_opts.get("suggested_unit_of_measurement")
        if stored_suggested == expected_units[matched_key]:
            continue

        # Flag for refresh so HA picks up the new suggested unit
        new_opts = dict(private_opts)
        new_opts["refresh_initial_entity_options"] = True
        registry.async_update_entity_options(
            entity_entry.entity_id,
            "sensor.private",
            new_opts,
        )
        _LOGGER.debug(
            "Migrated unit for %s: %s -> %s",
            entity_entry.entity_id,
            stored_suggested,
            expected_units[matched_key],
        )


def _discover_device_sensors(
    coordinator: UnifiInsightsFacadeCoordinator,
    site_id: str,
    device_id: str,
    device_data: dict[str, Any],
    known_sensor_keys: set[tuple[Any, ...]],
    *,
    entities: list[SensorEntity],
) -> None:
    """Discover standard network device sensors."""
    for description in SENSOR_TYPES:
        if description.required_feature is not None and not device_has_feature(
            device_data, description.required_feature
        ):
            continue

        if (
            description.key == "general_temperature"
            and get_network_device_temperature(device_data) is None
            and not get_field(
                device_data,
                "hasTemperature",
                "has_temperature",
                default=False,
            )
        ):
            continue

        # Only create uplink rate sensors if uplink data exists
        if description.key in ("tx_rate", "rx_rate"):
            stats = network_device_stats(coordinator.data, site_id, device_id)
            if not isinstance(stats, dict):
                continue
            uplink = get_field(stats, "uplink", "uplink_stats", default=None)
            has_uplink = isinstance(uplink, dict) and bool(uplink)
            has_top_level = any(
                stats.get(k) is not None
                for k in (
                    "txRateBps",
                    "tx_rate_bps",
                    "rxRateBps",
                    "rx_rate_bps",
                    "tx_bytes_per_sec",
                    "rx_bytes_per_sec",
                )
            )
            if not has_uplink and not has_top_level:
                continue

        # Only create Total PoE Power sensor if coordinator
        if description.key == "poe_total_power":
            stats = network_device_stats(coordinator.data, site_id, device_id)
            if not isinstance(stats, dict):
                continue
            poe_keys = (
                "poe_total_w",
                "poeTotalW",
                "total_used_power",
                "totalUsedPower",
            )
            has_any_total = any(stats.get(k) is not None for k in poe_keys)
            has_ports = isinstance(stats.get("poe_ports"), dict)
            if not has_any_total and not has_ports:
                continue

        # Only create AC power sensors if data is present on the device
        if description.key in (
            "ac_power_consumption",
            "ac_power_budget",
        ):
            val = description.value_fn(device_data) if description.value_fn else None
            if val is None:
                continue

        key = (site_id, device_id, description.key)
        if key in known_sensor_keys:
            continue
        known_sensor_keys.add(key)
        entities.append(
            UnifiInsightsSensor(
                coordinator=coordinator,
                description=description,
                site_id=site_id,
                device_id=device_id,
            )
        )

    # Add WAN sensors for gateway devices. The kind fact covers the
    # UCG/UXG/UDR/UDW consoles that issue #151 reported as missing WAN sensors.
    facts = device_facts(coordinator.data, site_id, device_id)
    if facts is not None and facts.kind == "gateway":
        for wan_desc in WAN_SENSOR_TYPES:
            wan_key = (site_id, device_id, wan_desc.key)
            if wan_key not in known_sensor_keys:
                known_sensor_keys.add(wan_key)
                entities.append(
                    UnifiInsightsSensor(
                        coordinator=coordinator,
                        description=wan_desc,
                        site_id=site_id,
                        device_id=device_id,
                    )
                )


def _discover_port_sensors(
    coordinator: UnifiInsightsFacadeCoordinator,
    site_id: str,
    device_id: str,
    device_data: dict[str, Any],
    known_sensor_keys: set[tuple[Any, ...]],
    *,
    entities: list[SensorEntity],
    restorable_unique_ids: frozenset[str] = frozenset(),
) -> None:
    """Discover port sensors for a network device."""
    # Ports need legacy data (see data_transforms): without port records no
    # port sensor is discovered, not even from the stats fallback.
    facts = device_facts(coordinator.data, site_id, device_id)
    if facts is None or not facts.ports:
        return

    active_port_indices: set[int] = set()
    seen_port_indices: set[int] = set()
    for port_facts in facts.ports:
        # The normalised index: the legacy merge can spell it "03" or " 3 ",
        # and _create_port_stats_fallback keys its ports on int; both feed the
        # same UnifiPortSensor unique ID. The first record of an index wins,
        # as it does for the runtime lookup.
        port_idx = port_facts.index
        if port_idx is None or port_idx in seen_port_indices:
            continue
        seen_port_indices.add(port_idx)
        port = port_facts.record

        port_state = get_field(port, "state", "status", default="DOWN")
        if str(port_state).upper() != "UP":
            if not restorable_unique_ids:
                continue
            port_label = _get_port_label(port, port_idx)
            for desc in (
                *PORT_SENSOR_TYPES,
                *PORT_RATE_SENSOR_TYPES,
                *SFP_SENSOR_TYPES,
            ):
                candidate = UnifiPortSensor(
                    coordinator=coordinator,
                    description=desc,
                    site_id=site_id,
                    device_id=device_id,
                    port_idx=port_idx,
                    port_label=port_label,
                )
                if candidate.unique_id in restorable_unique_ids:
                    p_key = (site_id, device_id, port_idx, desc.key)
                    known_sensor_keys.add(p_key)
                    entities.append(candidate)
            continue
        active_port_indices.add(port_idx)

        port_label = _get_port_label(port, port_idx)
        is_sfp = str(port.get("media", "")).startswith("SFP")

        poe_data = get_field(port, "poe", default={})
        poe_marker = False
        if isinstance(poe_data, dict):
            if poe_data.get("good"):
                poe_marker = True
            else:
                for pw_key in ("power", "watts"):
                    pw = poe_data.get(pw_key)
                    try:
                        if pw is not None and float(pw) > 0:
                            poe_marker = True
                            break
                    except (ValueError, TypeError):  # fmt: skip
                        pass

        if not poe_marker:
            norm = get_field(port, "poe_power_w")
            try:
                poe_marker = norm is not None and float(norm) > 0
            except (ValueError, TypeError):  # fmt: skip
                poe_marker = False

        if poe_marker:
            poe_desc = PORT_SENSOR_TYPES[0]
            poe_key = (site_id, device_id, port_idx, poe_desc.key)
            if poe_key not in known_sensor_keys:
                known_sensor_keys.add(poe_key)
                entities.append(
                    UnifiPortSensor(
                        coordinator=coordinator,
                        description=poe_desc,
                        site_id=site_id,
                        device_id=device_id,
                        port_idx=port_idx,
                        port_label=port_label,
                    )
                )

        for desc in (
            PORT_SENSOR_TYPES[1],  # speed
            PORT_SENSOR_TYPES[2],  # tx
            PORT_SENSOR_TYPES[3],  # rx
            PORT_RATE_SENSOR_TYPES[0],  # tx_rate
            PORT_RATE_SENSOR_TYPES[1],  # rx_rate
        ):
            p_key = (site_id, device_id, port_idx, desc.key)
            if p_key not in known_sensor_keys:
                known_sensor_keys.add(p_key)
                entities.append(
                    UnifiPortSensor(
                        coordinator=coordinator,
                        description=desc,
                        site_id=site_id,
                        device_id=device_id,
                        port_idx=port_idx,
                        port_label=port_label,
                    )
                )

        if is_sfp and port.get("sfp_found"):
            for sfp_desc in SFP_SENSOR_TYPES:
                sfp_key = (site_id, device_id, port_idx, sfp_desc.key)
                if sfp_key not in known_sensor_keys:
                    known_sensor_keys.add(sfp_key)
                    entities.append(
                        UnifiPortSensor(
                            coordinator=coordinator,
                            description=sfp_desc,
                            site_id=site_id,
                            device_id=device_id,
                            port_idx=port_idx,
                            port_label=port_label,
                        )
                    )

    # Fallback: create per-port sensors from stats
    if device_has_feature(device_data, "switching"):
        stats = network_device_stats(coordinator.data, site_id, device_id)
        if isinstance(stats, dict):
            _create_port_stats_fallback(
                coordinator,
                stats,
                site_id,
                device_id,
                active_port_indices,
                known_sensor_keys=known_sensor_keys,
                entities=entities,
            )


def _create_port_stats_fallback(
    coordinator: UnifiInsightsFacadeCoordinator,
    stats: dict[str, Any],
    site_id: str,
    device_id: str,
    active_ports: set[int],
    *,
    known_sensor_keys: set[tuple[Any, ...]],
    entities: list[SensorEntity],
) -> None:
    """Create port sensors from stats when interfaces.ports is missing."""
    poe_stats = stats.get("poe_ports")
    if isinstance(poe_stats, dict) and poe_stats:
        for k, val in poe_stats.items():
            if not isinstance(k, int) and not (isinstance(k, str) and k.isdigit()):
                continue
            port_idx = int(k)
            if active_ports and port_idx not in active_ports:
                continue
            try:
                if val is not None and float(val) <= 0:
                    continue
            except (ValueError, TypeError):  # fmt: skip
                continue
            desc = PORT_SENSOR_TYPES[0]
            key = (site_id, device_id, port_idx, desc.key)
            if key not in known_sensor_keys:
                known_sensor_keys.add(key)
                entities.append(
                    UnifiPortSensor(
                        coordinator=coordinator,
                        description=desc,
                        site_id=site_id,
                        device_id=device_id,
                        port_idx=port_idx,
                    )
                )

    byte_stats = stats.get("port_bytes")
    if isinstance(byte_stats, dict) and byte_stats:
        for k in byte_stats:
            if not isinstance(k, int) and not (isinstance(k, str) and k.isdigit()):
                continue
            port_idx = int(k)
            if active_ports and port_idx not in active_ports:
                continue
            for desc in (PORT_SENSOR_TYPES[2], PORT_SENSOR_TYPES[3]):
                key = (site_id, device_id, port_idx, desc.key)
                if key not in known_sensor_keys:
                    known_sensor_keys.add(key)
                    entities.append(
                        UnifiPortSensor(
                            coordinator=coordinator,
                            description=desc,
                            site_id=site_id,
                            device_id=device_id,
                            port_idx=port_idx,
                        )
                    )


def _discover_outlet_sensors(
    coordinator: UnifiInsightsFacadeCoordinator,
    site_id: str,
    device_id: str,
    device_data: dict[str, Any],
    known_sensor_keys: set[tuple[Any, ...]],
    *,
    entities: list[SensorEntity],
) -> None:
    """Discover outlet sensors for PDUs and smart power strips."""
    outlets = device_data.get("outlet_table", [])
    if not isinstance(outlets, list) or not outlets:
        return

    for outlet in outlets:
        if not isinstance(outlet, dict):
            continue
        idx = outlet.get("index")
        if idx is None:
            idx = outlet.get("outlet_idx") or outlet.get("outletIdx")
        if idx is None:
            continue
        try:
            outlet_idx = int(idx)
        except (TypeError, ValueError):  # fmt: skip
            continue

        if not _outlet_has_metering(outlet):
            continue

        outlet_label = outlet.get("name") or f"Outlet {outlet_idx}"
        for outlet_desc in OUTLET_SENSOR_TYPES:
            o_key = (site_id, device_id, outlet_idx, outlet_desc.key)
            if o_key not in known_sensor_keys:
                known_sensor_keys.add(o_key)
                entities.append(
                    UnifiOutletSensor(
                        coordinator=coordinator,
                        description=outlet_desc,
                        site_id=site_id,
                        device_id=device_id,
                        outlet_index=outlet_idx,
                        outlet_name=outlet_label,
                    )
                )


def _discover_protect_sensors(
    coordinator: UnifiInsightsFacadeCoordinator,
    known_sensor_keys: set[tuple[Any, ...]],
    entities: list[SensorEntity],
) -> None:
    """Discover sensors for UniFi Protect."""
    if not coordinator.protect_client:
        return

    sensors = protect_collection(coordinator.data, "sensors") or {}
    for sensor_id, sensor_data in sensors.items():
        for protect_desc in PROTECT_SENSOR_TYPES:
            if protect_desc.device_type == DEVICE_TYPE_SENSOR and (
                protect_desc.capability_fn is None
                or protect_desc.capability_fn(sensor_data)
            ):
                prot_key = (sensor_id, protect_desc.key)
                if prot_key not in known_sensor_keys:
                    known_sensor_keys.add(prot_key)
                    entities.append(
                        UnifiProtectSensor(
                            coordinator=coordinator,
                            description=protect_desc,
                            device_id=sensor_id,
                        )
                    )

    nvrs = protect_collection(coordinator.data, "nvrs") or {}
    for nvr_id, nvr_data in nvrs.items():
        for nvr_desc in NVR_SENSOR_TYPES:
            if nvr_desc.value_fn is None or nvr_desc.value_fn(nvr_data) is None:
                continue
            nvr_key = (nvr_id, nvr_desc.key)
            if nvr_key not in known_sensor_keys:
                known_sensor_keys.add(nvr_key)
                entities.append(
                    UnifiProtectSensor(
                        coordinator=coordinator,
                        description=nvr_desc,
                        device_id=nvr_id,
                    )
                )


def _parse_port_sensor_identity(
    entity: er.RegistryEntry,
    config_entry_id: str,
) -> tuple[str, UnifiInsightsSensorEntityDescription, int] | None:
    """Parse device ID, description, and port index from an entity row."""
    if (
        entity.domain != "sensor"
        or entity.platform != DOMAIN
        or entity.config_entry_id != config_entry_id
        or is_storage_unique_id(entity.unique_id)
    ):
        return None
    prefix, separator, port_idx_str = entity.unique_id.rpartition("_")
    try:
        if not separator or str(int(port_idx_str)) != port_idx_str:
            return None
        port_idx = int(port_idx_str)
    except ValueError:
        return None
    for description in (
        *PORT_SENSOR_TYPES,
        *PORT_RATE_SENSOR_TYPES,
        *SFP_SENSOR_TYPES,
    ):
        ending = f"_{description.key}"
        if not prefix.endswith(ending):
            continue
        device_id = prefix[: -len(ending)]
        if not device_id:
            return None
        return (device_id, description, port_idx)
    return None


def _is_owned_port_sensor(
    entity: er.RegistryEntry,
    config_entry_id: str,
    known_device_ids: set[str],
) -> bool:
    """
    Recognize physical port rows using the constructor's identity and ownership.

    Matching description metadata also identifies ports of removed devices.
    Without that metadata, require a device from the current API inventory;
    incompatible or otherwise ambiguous rows are retained. Namespace, site,
    and storage-path text containing ``_port_`` never establishes port identity.
    """
    parsed = _parse_port_sensor_identity(entity, config_entry_id)
    if parsed is None:
        return False
    device_id, description, _ = parsed
    return entity.translation_key == description.translation_key or (
        entity.translation_key in (None, "") and device_id in known_device_ids
    )


def _collect_device_port_indices(
    coordinator: UnifiInsightsFacadeCoordinator,
) -> dict[str, set[int]]:
    """
    Collect the union of valid port indices across sites for each device.

    Devices with no valid port indices (missing ports, empty ports, all-invalid indices,
    or facts is None) are omitted so their port evidence is treated as unknown.
    """
    device_ports: dict[str, set[int]] = {}
    devices_by_site = section_by_site(coordinator.data, "devices") or {}
    for site_id, devices in devices_by_site.items():
        for device_id in devices:
            facts = device_facts(coordinator.data, site_id, device_id)
            if facts is None or not facts.ports:
                continue
            for port in facts.ports:
                if port.index is not None:
                    device_ports.setdefault(device_id, set()).add(port.index)
    return device_ports


def _is_carrier_fabric_sensor_entry(
    entry: UnifiInsightsConfigEntry | CarrierFabricConfigEntry,
) -> TypeIs[CarrierFabricConfigEntry]:
    """Narrow the sensor entry through its existing runtime-data discriminator."""
    return isinstance(entry.runtime_data, CarrierFabricData)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: UnifiInsightsConfigEntry | CarrierFabricConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for UniFi Insights integration."""
    if _is_carrier_fabric_sensor_entry(config_entry):
        await async_setup_carrier_fabric_sensors(hass, config_entry, async_add_entities)
        return

    _LOGGER.debug("Setting up UniFi Insights sensors")

    # Migrate existing entities to pick up new suggested_unit_of_measurement
    _migrate_sensor_units(hass, config_entry)

    coordinator: UnifiInsightsFacadeCoordinator = config_entry.runtime_data.coordinator
    known_sensor_keys: set[tuple[Any, ...]] = set()
    initial_entities: list[SensorEntity] = []

    ent_reg = er.async_get(hass)
    restorable_unique_ids: frozenset[str] = frozenset(
        entry.unique_id
        for entry in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)
        if entry.domain == "sensor" and entry.platform == DOMAIN
    )

    # Only the first pass feeds the stale-entity sweep below. Without this the
    # list would keep every entity discovered for the lifetime of the entry,
    # long after the one sweep that reads it has run.
    setup_complete = False

    @callback
    def async_discover_sensors() -> None:
        """Discover and add new sensors."""
        snapshot: Mapping[str, Any] = coordinator.data
        if not snapshot or not isinstance(snapshot, dict):
            return

        entities: list[SensorEntity] = []

        # Add sensors for each device in each site
        devices_by_site = section_by_site(coordinator.data, "devices") or {}
        for site_id, devices in devices_by_site.items():
            for device_id, device_data in devices.items():
                _discover_device_sensors(
                    coordinator,
                    site_id,
                    device_id,
                    device_data,
                    known_sensor_keys,
                    entities=entities,
                )
                _discover_port_sensors(
                    coordinator,
                    site_id,
                    device_id,
                    device_data,
                    known_sensor_keys,
                    entities=entities,
                    restorable_unique_ids=restorable_unique_ids
                    if not setup_complete
                    else frozenset(),
                )
                _discover_outlet_sensors(
                    coordinator,
                    site_id,
                    device_id,
                    device_data,
                    known_sensor_keys,
                    entities=entities,
                )
                entities.extend(
                    discover_storage_sensors(
                        coordinator,
                        site_id,
                        device_id,
                        device_data,
                        known_sensor_keys,
                    )
                )

        # Add site-level client count sensors
        namespace = resolve_console_namespace(config_entry)
        clients_by_site = section_by_site(coordinator.data, "clients") or {}
        for site_id in clients_by_site:
            for site_desc in SITE_CLIENT_SENSOR_TYPES:
                site_key = (site_id, site_desc.key)
                if site_key not in known_sensor_keys:
                    unique_id = async_migrate_site_entity(
                        hass,
                        config_entry,
                        "sensor",
                        namespace,
                        site_id,
                        suffix=site_desc.key,
                    )
                    if unique_id is None:
                        continue
                    entity = UnifiSiteClientSensor(
                        coordinator=coordinator,
                        description=site_desc,
                        site_id=site_id,
                        unique_id=unique_id,
                    )
                    known_sensor_keys.add(site_key)
                    entities.append(entity)

        # Add site-level internet activity rolling-window sensors
        entities.extend(
            _discover_site_internet_activity_sensors(
                coordinator,
                known_sensor_keys,
                hass=hass,
                entry=config_entry,
                namespace=namespace,
            )
        )

        # Add hotspot voucher management sensors
        entities.extend(
            discover_voucher_sensors(
                coordinator,
                known_sensor_keys,
                hass=hass,
                entry=config_entry,
                namespace=namespace,
            )
        )

        # Add per-WiFi-network connected client count sensors
        wifi_by_site = section_by_site(coordinator.data, "wifi") or {}
        for site_id, wifi_networks in wifi_by_site.items():
            for wifi_id in wifi_networks:
                w_key = (site_id, wifi_id, "wifi_clients")
                if w_key not in known_sensor_keys:
                    known_sensor_keys.add(w_key)
                    entities.append(
                        UnifiWifiClientCountSensor(
                            coordinator=coordinator,
                            site_id=site_id,
                            wifi_id=wifi_id,
                        )
                    )

        # Add UniFi Protect sensors
        _discover_protect_sensors(coordinator, known_sensor_keys, entities)

        # Add UniFi InnerSpace placement sensors
        _discover_innerspace_sensors(coordinator, known_sensor_keys, entities)

        # Add Protect alarm hub, link station and fob sensors
        entities.extend(
            discover_protect_security_sensors(coordinator, known_sensor_keys)
        )

        if entities:
            _LOGGER.info("Adding %d UniFi Insights sensors", len(entities))
            async_add_entities(entities)
            if not setup_complete:
                initial_entities.extend(entities)

    async_discover_sensors()
    setup_complete = True
    config_entry.async_on_unload(coordinator.async_add_listener(async_discover_sensors))

    if mobility_coordinator := config_entry.runtime_data.mobility_coordinator:
        async_setup_mobility_sensors(
            hass, config_entry, mobility_coordinator, async_add_entities
        )

    # Clean up stale port entities from previous runs
    created_uids = {getattr(e, "unique_id", None) for e in initial_entities}
    known_device_ids = {
        device_id
        for devices in (section_by_site(coordinator.data, "devices") or {}).values()
        for device_id in devices
        if isinstance(device_id, str) and device_id
    }
    device_port_indices = _collect_device_port_indices(coordinator)
    stale: list[er.RegistryEntry] = []
    for entry in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id):
        if entry.unique_id in created_uids:
            continue
        if not _is_owned_port_sensor(entry, config_entry.entry_id, known_device_ids):
            continue
        parsed = _parse_port_sensor_identity(entry, config_entry.entry_id)
        if parsed is None:
            continue
        device_id, _, port_idx = parsed
        if device_id not in known_device_ids:
            stale.append(entry)
            continue
        valid_indices = device_port_indices.get(device_id)
        if not valid_indices:
            continue
        if port_idx not in valid_indices:
            stale.append(entry)
    for entry in stale:
        _LOGGER.debug("Removing stale port sensor entity: %s", entry.unique_id)
        ent_reg.async_remove(entry.entity_id)


class UnifiInsightsSensor(UnifiInsightsEntity, SensorEntity):
    """Representation of a UniFi Insights Sensor."""

    entity_description: UnifiInsightsSensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        description: UnifiInsightsSensorEntityDescription,
        site_id: str,
        device_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description, site_id, device_id)

        _LOGGER.debug(
            "Initializing %s sensor for device %s in site %s",
            description.key,
            device_id,
            site_id,
        )

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        if self.entity_description.facts_fn is not None:
            # CPU, memory, uptime and uplink rates: the published read model,
            # so these sensors and the dashboards report the same numbers.
            facts = self.device_facts
            return self.entity_description.facts_fn(facts) if facts else None
        # Special handling for sensors that come from device data (not stats)
        if self.entity_description.key in [
            "firmware_version",
            "wan_ip",
            "general_temperature",
            "ac_power_consumption",
            "ac_power_budget",
        ]:
            device = network_device(
                self.coordinator.data, self._site_id, self._device_id
            )
            if not device or not isinstance(device, dict):
                _LOGGER.debug(
                    "No device data available for %s sensor (device %s in site %s)",
                    self.entity_description.key,
                    self._device_id,
                    self._site_id,
                )
                return None
            value = self.entity_description.value_fn(device)  # type: ignore[misc]
        else:
            # For all other sensors, use stats data
            stats = network_device_stats(
                self.coordinator.data, self._site_id, self._device_id
            )
            if not stats or not isinstance(stats, dict):
                _LOGGER.debug(
                    "No stats available for sensor %s (device %s in site %s)",
                    self.entity_description.key,
                    self._device_id,
                    self._site_id,
                )
                return None

            value = self.entity_description.value_fn(stats)  # type: ignore[misc]

        _LOGGER.debug(
            "Sensor %s for device %s in site %s updated to %s %s",
            self.entity_description.key,
            self._device_id,
            self._site_id,
            value,
            self.native_unit_of_measurement or "",
        )

        return value


class UnifiPortSensor(UnifiInsightsEntity, SensorEntity):
    """Representation of a UniFi Port Sensor."""

    entity_description: UnifiInsightsSensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        description: UnifiInsightsSensorEntityDescription,
        site_id: str,
        device_id: str,
        port_idx: int,
        *,
        port_label: str | None = None,
    ) -> None:
        """Initialize the port sensor."""
        super().__init__(coordinator, description, site_id, device_id)
        self._port_idx = port_idx

        # Use port label (e.g. "SFP+ 1") or fall back to "Port {idx}"
        label = port_label or f"Port {port_idx}"

        # Provide the {port_label} placeholder used in the per-port translation
        # strings (e.g. "{port_label} TX"). This produces unique, descriptive
        # names per metric (e.g. "Port 1 TX", "Port 1 RX") instead of every
        # sensor for a port sharing the same name.
        self._attr_translation_placeholders = {"port_label": label}

        # Create unique ID with port index (stable, doesn't change with rename)
        self._attr_unique_id = f"{device_id}_{description.key}_{port_idx}"

        _LOGGER.debug(
            "Initializing %s sensor for port %d on device %s in site %s",
            description.key,
            port_idx,
            device_id,
            site_id,
        )

    def _find_port_data(self) -> Mapping[str, Any] | None:
        """
        Return this sensor's port record from the published read model.

        The lookup uses the normalised index, so a port the legacy merge
        spells "03" or 0 resolves like discovery found it. The first record
        of a duplicated index wins.
        """
        facts = self.device_facts
        port = facts.port_by_index(self._port_idx) if facts else None
        return port.record if port else None

    def _stats_section(self, section: str) -> dict[Any, Any] | None:
        """Return one per-port section of this device's stats, if a dict."""
        stats = network_device_stats(
            self.coordinator.data, self._site_id, self._device_id
        )
        value = stats.get(section) if stats is not None else None
        return value if isinstance(value, dict) else None

    def _stats_port_entry(self, section: str) -> Any:
        """Return this port's entry in a stats section (int key, then str)."""
        by_port = self._stats_section(section)
        if by_port is None:
            return None
        return by_port.get(self._port_idx) or by_port.get(str(self._port_idx))

    def _stats_poe_watts(self) -> Any:
        """Return stats PoE watts, None when invalid, _NO_STATS when absent."""
        poe_ports = self._stats_section("poe_ports")
        if poe_ports is None:
            return _NO_STATS
        # An int-keyed poe_ports (e.g. {3: 0}) must not fall through to the
        # string-key lookup: `or` would treat a real 0 W reading as absent.
        watts = poe_ports.get(self._port_idx)
        if watts is None:
            watts = poe_ports.get(str(self._port_idx))
        if isinstance(watts, bool):
            # bool is an int subclass: True must not read as 1 W.
            return None
        if isinstance(watts, (int, float)):
            return float(watts)
        if isinstance(watts, str):
            try:
                return float(watts)
            except ValueError:
                return None
        return _NO_STATS

    def _stats_counter(self, *sections: str) -> int | None:
        """Return this port's TX or RX byte counter from stats sections."""
        counter_key = (
            "tx_bytes" if self.entity_description.key == "port_tx_bytes" else "rx_bytes"
        )
        for section in sections:
            entry = self._stats_port_entry(section)
            if isinstance(entry, dict):
                value = entry.get(counter_key)
                # bool is an int subclass: True must not read as 1 byte, which
                # a total_increasing counter would record as a reset.
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return int(value)
        return None

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        key = self.entity_description.key
        if self.device_facts is None:
            _LOGGER.debug(
                "No device data available for port sensor %s (device %s in site %s)",
                key,
                self._device_id,
                self._site_id,
            )
            return None

        port_data = self._find_port_data()
        # Rates always come from computed stats; byte counters always come from
        # stats, and also from upstream port_data when the port record exists.
        if key in ("port_tx_rate", "port_rx_rate"):
            return self._get_port_rate_value()
        if key in ("port_tx_bytes", "port_rx_bytes"):
            if port_data is None:
                return self._stats_counter("port_bytes")
            counter = self._stats_counter("port_bytes", "port_data")
            if counter is None:
                # Last resort: the merged port record's own counters. Never
                # default to 0, which would reset a total_increasing counter.
                record_stats = port_data.get("stats")
                if isinstance(record_stats, dict):
                    value = get_field(
                        record_stats,
                        *(
                            ("txBytes", "tx_bytes")
                            if key == "port_tx_bytes"
                            else ("rxBytes", "rx_bytes")
                        ),
                    )
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        counter = int(value)
            return counter
        if key == "port_poe_power":
            # Prefer PoE watts from coordinator stats when available
            watts = self._stats_poe_watts()
            if watts is not _NO_STATS:
                return cast("StateType", watts)

        if port_data is None:
            _LOGGER.debug(
                "No port data available for port %d on device %s",
                self._port_idx,
                self._device_id,
            )
            return None

        value = self.entity_description.value_fn(dict(port_data))  # type: ignore[misc]

        _LOGGER.debug(
            "Port sensor %s for port %d on device %s updated to %s %s",
            key,
            self._port_idx,
            self._device_id,
            value,
            self.native_unit_of_measurement or "",
        )

        return value

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not owner_available(self.coordinator.data, "device"):
            return False
        key = self.entity_description.key
        # Stats-backed metrics stay available while stats carry this port,
        # even when the link is DOWN (PoE disabled or idle). If the device
        # provides poe_ports at all, keep PoE power sensors available.
        if key == "port_poe_power" and self._stats_section("poe_ports") is not None:
            return True
        if key in ("port_tx_bytes", "port_rx_bytes") and isinstance(
            self._stats_port_entry("port_bytes"), dict
        ):
            return True
        if key in ("port_tx_rate", "port_rx_rate") and isinstance(
            self._stats_port_entry("port_rates"), dict
        ):
            return True

        # Otherwise the port must exist and be UP
        port_data = self._find_port_data()
        if port_data is None:
            return False

        # SFP info sensors stay available regardless of port state
        if key.startswith("port_sfp_"):
            return True

        port_state = port_data.get("state", "DOWN")
        return isinstance(port_state, str) and port_state == "UP"

    def _get_port_rate_value(self) -> StateType:
        """Get the computed port byte rate as bits/sec."""
        stats = network_device_stats(
            self.coordinator.data, self._site_id, self._device_id
        )
        if stats is None:
            return None

        port_rates = stats.get("port_rates")
        if not isinstance(port_rates, dict):
            return None

        pr = port_rates.get(self._port_idx) or port_rates.get(str(self._port_idx))
        if not isinstance(pr, dict):
            return None

        rate_key = (
            "tx_bytes_rate"
            if self.entity_description.key == "port_tx_rate"
            else "rx_bytes_rate"
        )
        bytes_per_sec = pr.get(rate_key)
        if bytes_per_sec is None:
            return None

        # Convert bytes/sec to bits/sec (native unit)
        return round(float(bytes_per_sec) * 8)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return port type attributes."""
        port = self._find_port_data()
        if not port:
            return None

        attrs: dict[str, Any] = {}
        media = port.get("media")
        if media:
            attrs["media_type"] = media
        is_uplink = port.get("is_uplink")
        if is_uplink is not None:
            attrs["is_uplink"] = is_uplink
        network_name = port.get("network_name")
        if network_name:
            attrs["network"] = network_name
        port_name = port.get("name")
        if port_name:
            attrs["port_name"] = port_name
        sfp_found = port.get("sfp_found")
        if sfp_found is not None:
            attrs["sfp_module_present"] = sfp_found
        return attrs or None


class UnifiOutletSensor(UnifiInsightsEntity, SensorEntity):
    """Representation of a UniFi Outlet Metering Sensor."""

    entity_description: UnifiInsightsSensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        description: UnifiInsightsSensorEntityDescription,
        site_id: str,
        device_id: str,
        *,
        outlet_index: int,
        outlet_name: str | None = None,
    ) -> None:
        """Initialize the outlet sensor."""
        super().__init__(coordinator, description, site_id, device_id)
        self._outlet_index = outlet_index
        label = outlet_name or f"Outlet {outlet_index}"
        self._attr_translation_placeholders = {"outlet_name": label}
        self._attr_unique_id = (
            f"{site_id}_{device_id}_outlet_{outlet_index}_{description.key}"
        )

        _LOGGER.debug(
            "Initializing %s sensor for outlet %d on device %s in site %s",
            description.key,
            outlet_index,
            device_id,
            site_id,
        )

    def _find_outlet_data(self) -> dict[str, Any] | None:
        """Find outlet data for this sensor's outlet index."""
        device_data = network_device(
            self.coordinator.data, self._site_id, self._device_id
        )
        if device_data is None:
            return None
        outlets = device_data.get("outlet_table", [])
        if not isinstance(outlets, list):
            return None
        for outlet in outlets:
            if not isinstance(outlet, dict):
                continue
            idx = outlet.get("index")
            if idx is None:
                idx = outlet.get("outlet_idx") or outlet.get("outletIdx")
            if idx is not None:
                try:
                    if int(idx) == self._outlet_index:
                        return outlet
                except (TypeError, ValueError):  # fmt: skip
                    continue
        return None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not super().available:
            return False
        return self._find_outlet_data() is not None

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        outlet = self._find_outlet_data()
        if not outlet:
            return None
        if self.entity_description.value_fn:
            return self.entity_description.value_fn(outlet)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return outlet metadata attributes."""
        outlet = self._find_outlet_data()
        if not outlet:
            return None
        attrs: dict[str, Any] = {"index": self._outlet_index}
        if "name" in outlet and outlet["name"] is not None:
            attrs["name"] = outlet["name"]
        if "relay_state" in outlet and outlet["relay_state"] is not None:
            attrs["relay_state"] = outlet["relay_state"]
        return attrs


class UnifiProtectSensor(UnifiProtectEntity, SensorEntity):
    """Sensor of a UniFi Protect camera, sensor, NVR or security device."""

    entity_description: UnifiProtectSensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        description: UnifiProtectSensorEntityDescription,
        device_id: str,
        *,
        device_type: str | None = None,
    ) -> None:
        """Initialize the sensor; security devices pass their own device type."""
        super().__init__(
            coordinator,
            device_type or description.device_type or "",
            device_id,
            description.key,
        )
        self.entity_description = description

        # Set entity category for battery sensors
        if description.key == "battery":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

        self._update_from_data()

    @property
    def available(self) -> bool:
        """
        Return True if entity is available.

        An NVR has no 'state' field like cameras, so its sensors check for
        valid NVR data, and storage sensors also for storage info.
        """
        if self._device_type != DEVICE_TYPE_NVR:
            return super().available
        if not owner_available(self.coordinator.data, "protect"):
            return False

        nvr_data = self.device_data
        if not nvr_data:
            return False

        if not self.entity_description.key.startswith("storage_"):
            return True
        value_fn = self.entity_description.value_fn
        return (
            nvr_data.get("state") not in ("DISCONNECTED", "DISCONNECTING")
            and value_fn is not None
            and value_fn(nvr_data) is not None
        )

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        device_data = self.device_data
        value_fn = self.entity_description.value_fn
        if not device_data or value_fn is None:
            return None
        return value_fn(device_data)

    def _update_from_data(self) -> None:
        """Update the attributes from the device record."""
        device_data = self.device_data or {}
        attributes_fn = self.entity_description.attributes_fn
        if attributes_fn is not None:
            self._attr_extra_state_attributes = (
                attributes_fn(device_data) if device_data else {}
            )
        elif self._device_type == DEVICE_TYPE_NVR:
            storage = normalize_nvr_storage(device_data)
            self._attr_extra_state_attributes = {
                ATTR_NVR_ID: self._device_id,
                ATTR_NVR_NAME: device_data.get("name"),
                ATTR_NVR_VERSION: device_data.get("version"),
                ATTR_NVR_STORAGE_USED: _bytes_to_gb(storage.used_bytes),
                ATTR_NVR_STORAGE_TOTAL: _bytes_to_gb(storage.total_bytes),
                ATTR_NVR_STORAGE_AVAILABLE: _calculate_storage_available(device_data),
                ATTR_NVR_STORAGE_USED_PERCENT: _calculate_storage_percent(device_data),
            }
        elif self.entity_description.key == "temperature":
            self._attr_extra_state_attributes = {
                ATTR_SENSOR_ID: self._device_id,
                ATTR_SENSOR_NAME: device_data.get("name"),
                ATTR_SENSOR_TEMPERATURE_VALUE: device_data.get("stats", {})
                .get("temperature", {})
                .get("value"),
            }
        elif self.entity_description.key == "humidity":
            self._attr_extra_state_attributes = {
                ATTR_SENSOR_ID: self._device_id,
                ATTR_SENSOR_NAME: device_data.get("name"),
                ATTR_SENSOR_HUMIDITY_VALUE: device_data.get("stats", {})
                .get("humidity", {})
                .get("value"),
            }
        elif self.entity_description.key == "light":
            self._attr_extra_state_attributes = {
                ATTR_SENSOR_ID: self._device_id,
                ATTR_SENSOR_NAME: device_data.get("name"),
                ATTR_SENSOR_LIGHT_VALUE: device_data.get("stats", {})
                .get("light", {})
                .get("value"),
            }
        elif self.entity_description.key == "battery":
            self._attr_extra_state_attributes = {
                ATTR_SENSOR_ID: self._device_id,
                ATTR_SENSOR_NAME: device_data.get("name"),
                ATTR_SENSOR_BATTERY: device_data.get("batteryStatus", {}).get(
                    "percentage"
                ),
                ATTR_SENSOR_BATTERY_LOW: device_data.get("batteryStatus", {}).get(
                    "isLow", False
                ),
            }


class UnifiSiteClientSensor(UnifiSiteEntity, SensorEntity):
    """Representation of a site-level client count sensor."""

    entity_description: UnifiInsightsSensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        description: UnifiInsightsSensorEntityDescription,
        site_id: str,
        *,
        unique_id: str,
    ) -> None:
        """Initialize the site-level client sensor."""
        super().__init__(
            coordinator, site_id, unique_id=unique_id, description=description
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return owner_available(self.coordinator.data, "device")

    @property
    def native_value(self) -> StateType:
        """Return the client count."""
        clients = site_section(self.coordinator.data, "clients", self._site_id)
        if clients is None:
            return 0
        return self.entity_description.value_fn(clients)  # type: ignore[misc]


class UnifiWifiClientCountSensor(
    CoordinatorEntity[UnifiInsightsFacadeCoordinator], SensorEntity
):
    """Number of clients connected to a specific WiFi network (SSID)."""

    _attr_has_entity_name = True
    _attr_translation_key = "wifi_client_count"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        site_id: str,
        wifi_id: str,
    ) -> None:
        """Initialize the per-WiFi client count sensor."""
        super().__init__(coordinator)
        self._site_id = site_id
        self._wifi_id = wifi_id

        wifi_data = self._get_wifi_data()
        wifi_name = wifi_data.get("name") or wifi_data.get("ssid", wifi_id)

        self._attr_unique_id = f"{site_id}_{wifi_id}_client_count"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"wifi_{wifi_id}")},
            name=f"WiFi: {wifi_name}",
            manufacturer=MANUFACTURER,
            model="WiFi Network",
        )

    def _get_wifi_data(self) -> dict[str, Any]:
        """Get WiFi data for this network from the coordinator."""
        result = rule_record(
            self.coordinator.data, "wifi", self._site_id, self._wifi_id
        )
        return result if isinstance(result, dict) else {}

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return bool(
            site_section_available(self.coordinator.data, self._site_id, "wifi")
            and self._get_wifi_data()
        )

    @property
    def native_value(self) -> StateType:
        """Return the number of connected clients on this WiFi network."""
        count = self._get_wifi_data().get("num_connected_clients")
        return count if isinstance(count, int) else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return WiFi network details."""
        wifi_data = self._get_wifi_data()
        return {
            "ssid": wifi_data.get("ssid") or wifi_data.get("name"),
            "is_guest": wifi_data.get("is_guest", wifi_data.get("isGuest", False)),
            "enabled": wifi_data.get("enabled", True),
        }


# --- Mobility ----------------------------------------------------------------

# UniFi Mobility workspace and router entities.

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
    if device.get("state") != MOBILITY_ROUTER_CONNECTED:
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
            if devices.get(device_id, {}).get("state") == MOBILITY_ROUTER_CONNECTED
        ),
    ),
)


class UnifiMobilitySensor(UnifiMobilityRouterEntity, SensorEntity):
    """A value reported for one Mobility router."""

    entity_description: UnifiMobilitySensorEntityDescription

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        router = self.router
        return self.entity_description.value_fn(router) if router else None


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
        self._attr_device_info = mobility_workspace_device_info(
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
    async_add_mobility_router_entities(
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


# --- Storage -----------------------------------------------------------------

# Per-mount storage entities for UniFi consoles.
#
# A console that runs the Network application lists its filesystems in the
# classic ``stat/device`` record. The device coordinator normalises that list
# into ``storage_mounts`` (see ``normalize_legacy_storage``) from the response it
# already fetches; nothing here makes a request.
#
# Capacity only: the classic record carries no disk health, SMART or RAID data,
# and the official Integration API has no storage fields at all.


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


# Every storage sensor unique ID contains this marker before the mount path.
STORAGE_UNIQUE_ID_MARKER = "_storage_"


def is_storage_unique_id(unique_id: str) -> bool:
    """
    Return True for a storage sensor unique ID.

    A mount path can contain "_port_" (for example "/mnt/usb_port_1"), so the
    stale port sweep in sensor.py uses this to leave storage rows alone.
    """
    return STORAGE_UNIQUE_ID_MARKER in unique_id


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


# --- Protect security devices ------------------------------------------------

# Entities for UniFi Protect security devices: alarm hubs, link stations, fobs.
#
# Covers the Protect 7.3.70 additions: Thread mesh state on link stations and
# alarm hubs (both use the ``linkStation`` schema), the alarm hub's device
# tamper status, and a keypad fob's keypad and arm-control settings. The public
# API cannot write any of these (``PATCH`` accepts ``name`` only), so every
# entity is read-only.
#
# The `unifi-official-api` models accept any value so a firmware addition never drops a
# device; the spec's enums and ranges are enforced here instead, and anything
# outside them reads as unknown (``None``) rather than as a misleading state.

# Spec enum for linkStationThreadNetwork.role.
THREAD_ROLES: list[str] = ["disabled", "detached", "child", "router", "leader"]
ARM_CONTROL_STATES: list[str] = ["enabled", "disabled"]
# fobKeypadSettings.beepVolume range per spec, in percent.
_MAX_BEEP_VOLUME = 100


def _has_arm_control(data: dict[str, Any]) -> bool:
    """Return True if the fob reports arm-control settings at all."""
    return nested_dict(data, "armControlSettings") is not None


def _keypad_volume(data: dict[str, Any]) -> int | None:
    """Return the keypress beep volume, 0-100 % per spec."""
    value = (nested_dict(data, "keypadSettings") or {}).get("beepVolume")
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= _MAX_BEEP_VOLUME
    ):
        return value
    return None


def _thread_role(data: dict[str, Any]) -> str | None:
    """
    Return the Thread role, limited to the ENUM options.

    HA raises if an ENUM sensor's state is not one of its options, so a role
    newer firmware adds reads as unknown until the integration learns it.
    """
    role = (thread_network(data) or {}).get("role")
    if isinstance(role, str) and role in THREAD_ROLES:
        return role
    if role is not None:
        _LOGGER.debug("Unrecognized Thread role %r; reporting unknown", role)
    return None


def _thread_joined_devices(data: dict[str, Any]) -> int | None:
    """Return how many devices have joined the Thread network."""
    value = (thread_network(data) or {}).get("joinedDeviceCount")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _arm_control(data: dict[str, Any]) -> str | None:
    """Return whether the fob may arm and disarm external arm profiles."""
    enabled = (nested_dict(data, "armControlSettings") or {}).get("enabled")
    if enabled is True:
        return "enabled"
    if enabled is False:
        return "disabled"
    return None


def _thread_network_attributes(data: dict[str, Any]) -> dict[str, Any]:
    # lastUpdatedAt is left out on purpose: it changes on every gateway
    # refresh, so each refresh would write a new recorder row.
    network = thread_network(data) or {}
    return present_attributes(
        **{
            ATTR_THREAD_CHANNEL: network.get("channel"),
            ATTR_THREAD_NETWORK_NAME: network.get("networkName"),
            ATTR_THREAD_PAN_ID: network.get("panId"),
            ATTR_THREAD_EXTENDED_PAN_ID: network.get("extendedPanId"),
        }
    )


def _arm_control_attributes(data: dict[str, Any]) -> dict[str, Any]:
    settings = nested_dict(data, "armControlSettings") or {}
    return present_attributes(
        **{
            ATTR_ARM_PROFILE_ID: settings.get("armProfileId"),
            ATTR_NIGHT_PROFILE_ID: settings.get("nightProfileId"),
        }
    )


@dataclass(frozen=True, kw_only=True)
class UnifiProtectSecuritySensorEntityDescription(UnifiProtectSensorEntityDescription):
    """Describes a sensor of a Protect security device."""

    device_types: tuple[str, ...]
    supported_fn: Callable[[dict[str, Any]], bool] = always_true


SECURITY_SENSOR_TYPES: tuple[UnifiProtectSecuritySensorEntityDescription, ...] = (
    UnifiProtectSecuritySensorEntityDescription(
        key="thread_role",
        translation_key="thread_role",
        device_class=SensorDeviceClass.ENUM,
        options=THREAD_ROLES,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=THREAD_DEVICE_TYPES,
        value_fn=_thread_role,
        supported_fn=has_thread_network,
        attributes_fn=_thread_network_attributes,
    ),
    UnifiProtectSecuritySensorEntityDescription(
        key="thread_joined_devices",
        translation_key="thread_joined_devices",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=THREAD_DEVICE_TYPES,
        value_fn=_thread_joined_devices,
        supported_fn=has_thread_network,
    ),
    UnifiProtectSecuritySensorEntityDescription(
        key="fob_keypad_beep_volume",
        translation_key="fob_keypad_beep_volume",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        device_types=(DEVICE_TYPE_FOB,),
        value_fn=_keypad_volume,
        supported_fn=has_keypad,
    ),
    UnifiProtectSecuritySensorEntityDescription(
        key="fob_arm_control",
        translation_key="fob_arm_control",
        device_class=SensorDeviceClass.ENUM,
        options=ARM_CONTROL_STATES,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        device_types=(DEVICE_TYPE_FOB,),
        value_fn=_arm_control,
        supported_fn=_has_arm_control,
        attributes_fn=_arm_control_attributes,
    ),
)


def discover_protect_security_sensors(
    coordinator: UnifiInsightsFacadeCoordinator,
    known_keys: set[tuple[Any, ...]],
) -> list[SensorEntity]:
    """Return sensors for security devices not yet in ``known_keys``."""
    entities: list[SensorEntity] = []
    for device_type, device_id, data in protect_security_devices(coordinator):
        for description in SECURITY_SENSOR_TYPES:
            if device_type not in description.device_types:
                continue
            if not description.supported_fn(data):
                continue
            key = (device_type, device_id, description.key)
            if key in known_keys:
                continue
            known_keys.add(key)
            entities.append(
                UnifiProtectSensor(
                    coordinator, description, device_id, device_type=device_type
                )
            )
    return entities


# --- Vouchers ----------------------------------------------------------------

# Voucher sensor platform for UniFi Insights.


@dataclass(frozen=True, kw_only=True)
class UnifiVoucherSensorEntityDescription(SensorEntityDescription):
    """Class describing UniFi voucher sensor entities."""

    value_fn: Callable[[Any, str], StateType | datetime]
    attrs_fn: Callable[[Any, str], dict[str, Any]] | None = None


def _get_latest_record(
    data: Mapping[str, Any] | None, site_id: str
) -> dict[str, Any] | None:
    """Safely return latest voucher record for a site."""
    if data is None:
        return None
    record = latest_voucher(data, site_id)
    return dict(record) if record is not None else None


def _latest_code_value(data: Mapping[str, Any] | None, site_id: str) -> StateType:
    """Return latest voucher code as string, or None."""
    record = _get_latest_record(data, site_id)
    if record is None:
        return None
    code = record.get("code")
    return str(code) if code is not None else None


def _latest_expiration_value(
    data: Mapping[str, Any] | None, site_id: str
) -> datetime | None:
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


def _latest_attributes(data: Mapping[str, Any] | None, site_id: str) -> dict[str, Any]:
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


class UnifiVoucherSensor(UnifiSiteEntity, SensorEntity):
    """Representation of a UniFi hotspot voucher sensor."""

    entity_description: UnifiVoucherSensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        description: UnifiVoucherSensorEntityDescription,
        site_id: str,
        *,
        unique_id: str,
    ) -> None:
        """Initialize the voucher sensor."""
        super().__init__(
            coordinator, site_id, unique_id=unique_id, description=description
        )

    @property
    def available(self) -> bool:
        """Return True if vouchers are available for this site."""
        return site_section_available(self.coordinator.data, self._site_id, "vouchers")

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
    coordinator: UnifiInsightsFacadeCoordinator,
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


# --- Site internet activity --------------------------------------------------

# Site-level historical internet activity sensors for UniFi Insights.


@dataclass(frozen=True, kw_only=True)
class UnifiSiteInternetActivitySensorEntityDescription(SensorEntityDescription):
    """Class describing UniFi site-level internet activity sensor entities."""

    window: str = "1h"
    metric: str = "rx_bytes"
    period_label: str = "Last Hour"
    unifi_window: str = "1H"
    direction: str = "download"
    report_interval: str = "5minutes"


SITE_INTERNET_ACTIVITY_SENSOR_TYPES: tuple[
    UnifiSiteInternetActivitySensorEntityDescription, ...
] = (
    UnifiSiteInternetActivitySensorEntityDescription(
        key="internet_download_1h",
        translation_key="internet_download_1h",
        window="1h",
        metric="rx_bytes",
        period_label="Last Hour",
        unifi_window="1H",
        direction="download",
        report_interval="5minutes",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnifiSiteInternetActivitySensorEntityDescription(
        key="internet_upload_1h",
        translation_key="internet_upload_1h",
        window="1h",
        metric="tx_bytes",
        period_label="Last Hour",
        unifi_window="1H",
        direction="upload",
        report_interval="5minutes",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnifiSiteInternetActivitySensorEntityDescription(
        key="internet_download_1d",
        translation_key="internet_download_1d",
        window="1d",
        metric="rx_bytes",
        period_label="Last 24 Hours",
        unifi_window="1D",
        direction="download",
        report_interval="hourly",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnifiSiteInternetActivitySensorEntityDescription(
        key="internet_upload_1d",
        translation_key="internet_upload_1d",
        window="1d",
        metric="tx_bytes",
        period_label="Last 24 Hours",
        unifi_window="1D",
        direction="upload",
        report_interval="hourly",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnifiSiteInternetActivitySensorEntityDescription(
        key="internet_download_1w",
        translation_key="internet_download_1w",
        window="1w",
        metric="rx_bytes",
        period_label="Last 7 Days",
        unifi_window="1W",
        direction="download",
        report_interval="hourly",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnifiSiteInternetActivitySensorEntityDescription(
        key="internet_upload_1w",
        translation_key="internet_upload_1w",
        window="1w",
        metric="tx_bytes",
        period_label="Last 7 Days",
        unifi_window="1W",
        direction="upload",
        report_interval="hourly",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnifiSiteInternetActivitySensorEntityDescription(
        key="internet_download_1m",
        translation_key="internet_download_1m",
        window="1m",
        metric="rx_bytes",
        period_label="Last 30 Days",
        unifi_window="1M",
        direction="download",
        report_interval="daily",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnifiSiteInternetActivitySensorEntityDescription(
        key="internet_upload_1m",
        translation_key="internet_upload_1m",
        window="1m",
        metric="tx_bytes",
        period_label="Last 30 Days",
        unifi_window="1M",
        direction="upload",
        report_interval="daily",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)


class UnifiSiteInternetActivitySensor(UnifiSiteEntity, SensorEntity):
    """Sensor showing historical WAN download/upload bytes for a UniFi site."""

    entity_description: UnifiSiteInternetActivitySensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        description: UnifiSiteInternetActivitySensorEntityDescription,
        site_id: str,
        *,
        unique_id: str,
    ) -> None:
        """Initialize the site internet activity sensor."""
        super().__init__(
            coordinator, site_id, unique_id=unique_id, description=description
        )

        self._attr_extra_state_attributes = {
            "period": description.period_label,
            "unifi_window": description.unifi_window,
            "direction": description.direction,
            "report_interval": description.report_interval,
        }

    def _get_window_value(self) -> int | None:
        """Return the current byte total for this sensor's window and metric."""
        data: Any = self.coordinator.data
        if not isinstance(data, dict):
            return None
        internet_activity = data.get("internet_activity")
        if not isinstance(internet_activity, dict):
            return None
        site_windows = internet_activity.get(self._site_id)
        if not isinstance(site_windows, dict):
            return None
        window_data = site_windows.get(self.entity_description.window)
        if not isinstance(window_data, dict):
            return None
        raw_value = window_data.get(self.entity_description.metric)
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            return None
        return round(float(raw_value))

    @property
    def available(self) -> bool:
        """Return True if the config refresh and site report section succeeded."""
        if not self.coordinator.last_update_success:
            return False
        if not site_section_available(
            self.coordinator.data, self._site_id, "internet_activity"
        ):
            return False
        unavailable_sites = self.coordinator.data.get("internet_activity_unavailable")
        if (
            isinstance(unavailable_sites, (AbstractSet, list, tuple))
            and self._site_id in unavailable_sites
        ):
            return False
        return self._get_window_value() is not None

    @property
    def native_value(self) -> int | None:
        """Return the aggregated byte usage for the configured rolling window."""
        return self._get_window_value()


def _discover_site_internet_activity_sensors(
    coordinator: UnifiInsightsFacadeCoordinator,
    known_sensor_keys: set[tuple[str, ...]],
    *,
    hass: HomeAssistant,
    entry: ConfigEntry,
    namespace: str | None,
) -> list[SensorEntity]:
    """Discover site-level historical internet activity sensors."""
    entities: list[SensorEntity] = []
    internet_activity_by_site = coordinator.data.get("internet_activity", {})
    if not isinstance(internet_activity_by_site, dict):
        return entities

    for site_id, site_windows in internet_activity_by_site.items():
        if not isinstance(site_windows, dict) or not site_windows:
            continue
        for activity_desc in SITE_INTERNET_ACTIVITY_SENSOR_TYPES:
            window_data = site_windows.get(activity_desc.window)
            if (
                not isinstance(window_data, dict)
                or window_data.get(activity_desc.metric) is None
            ):
                continue
            activity_key = (site_id, activity_desc.key)
            if activity_key not in known_sensor_keys:
                unique_id = async_migrate_site_entity(
                    hass, entry, "sensor", namespace, site_id, suffix=activity_desc.key
                )
                if unique_id is None:
                    continue
                entity = UnifiSiteInternetActivitySensor(
                    coordinator=coordinator,
                    description=activity_desc,
                    site_id=site_id,
                    unique_id=unique_id,
                )
                known_sensor_keys.add(activity_key)
                entities.append(entity)

    return entities


# --- InnerSpace --------------------------------------------------------------

# InnerSpace entity base class and placement sensor for UniFi Insights.

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


# --- Carrier Fabric ----------------------------------------------------------

# Sensor platform for UniFi Carrier Fabric integration.


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
        coordinator: UnifiInsightsCarrierFabricCoordinator,
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
        coordinator: UnifiInsightsCarrierFabricCoordinator,
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
        coordinator: UnifiInsightsCarrierFabricCoordinator,
        entry: CarrierFabricConfigEntry,
        subscriber_id: str,
        organisation_device_id: str,
    ) -> None:
        """Initialize the subscriber state sensor."""
        super().__init__(coordinator, entry, subscriber_id, organisation_device_id)
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
        coordinator: UnifiInsightsCarrierFabricCoordinator,
        entry: CarrierFabricConfigEntry,
        subscriber_id: str,
        organisation_device_id: str,
    ) -> None:
        """Initialize the subscriber plan sensor."""
        super().__init__(coordinator, entry, subscriber_id, organisation_device_id)
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
    # Subscriber devices link to the organisation device by its registry ID,
    # so it is registered in this entry before any subscriber is created.
    organisation_device_id = (
        dr.async_get(hass)
        .async_get_or_create(
            config_entry_id=config_entry.entry_id,
            **carrier_organisation_device_fields(config_entry),
        )
        .id
    )

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
                            coordinator,
                            config_entry,
                            str(sub_id),
                            organisation_device_id,
                        )
                    )

                plan_uid = f"carrier_subscriber_{sub_id}_service_plan"
                if plan_uid not in known_keys:
                    known_keys.add(plan_uid)
                    new_entities.append(
                        UnifiCarrierFabricSubscriberPlanSensor(
                            coordinator,
                            config_entry,
                            str(sub_id),
                            organisation_device_id,
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
