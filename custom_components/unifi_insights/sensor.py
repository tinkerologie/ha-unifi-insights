# Copyright 2026 UniFi Insights contributors
"""Support for UniFi Insights sensors."""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    EntityCategory,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfDataRate,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfInformation,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import site_internet_activity_sensor
from .console_identity import resolve_console_namespace
from .const import (
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
    DEVICE_TYPE_NVR,
    DEVICE_TYPE_SENSOR,
    DOMAIN,
    MANUFACTURER,
)
from .coordinators import UnifiFacadeCoordinator
from .entity import (
    UnifiInsightsEntity,
    UnifiProtectEntity,
    build_site_device_info,
    device_has_feature,
    first_not_none,
    get_field,
    is_gateway_device,
)
from .entity import (
    get_client_type as _get_client_type,
)
from .helpers import async_migrate_site_entity
from .helpers import get_port_label as _get_port_label
from .innerspace_entity import (
    _discover_innerspace_sensors,
)
from .mobility_entity import async_setup_mobility_sensors
from .protect_security_entity import discover_protect_security_sensors
from .site_internet_activity_sensor import (
    _discover_site_internet_activity_sensors,
)
from .storage_entity import discover_storage_sensors, is_storage_unique_id
from .storage_telemetry import normalize_nvr_storage
from .voucher_sensor import discover_voucher_sensors

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.typing import StateType

    from . import UnifiInsightsConfigEntry
    from .carrier_fabric_data import CarrierFabricConfigEntry

from homeassistant.helpers.entity import DeviceInfo

from .carrier_fabric_data import CarrierFabricData
from .carrier_fabric_sensor import async_setup_carrier_fabric_sensors

# Preserve these public sensor-module exports without redundant import aliases.
SITE_INTERNET_ACTIVITY_SENSOR_TYPES = (
    site_internet_activity_sensor.SITE_INTERNET_ACTIVITY_SENSOR_TYPES
)
UnifiSiteInternetActivitySensor = (
    site_internet_activity_sensor.UnifiSiteInternetActivitySensor
)
UnifiSiteInternetActivitySensorEntityDescription = (
    site_internet_activity_sensor.UnifiSiteInternetActivitySensorEntityDescription
)

_LOGGER = logging.getLogger(__name__)


# Coordinator handles updates centrally (Gold/Platinum requirement)
PARALLEL_UPDATES = 0


@dataclass
class UnifiInsightsSensorEntityDescription(SensorEntityDescription):  # type: ignore[misc]
    """Class describing UniFi Insights sensor entities."""

    value_fn: Callable[[dict[str, Any]], StateType] | None = None
    # Device feature required for this sensor (e.g., 'switching', 'accessPoint')
    # If None, sensor applies to all devices
    required_feature: str | None = None


@dataclass
class UnifiProtectSensorEntityDescription(SensorEntityDescription):  # type: ignore[misc]
    """Class describing UniFi Protect sensor entities."""

    value_fn: Callable[[dict[str, Any]], StateType] | None = None
    device_type: str | None = None
    capability_fn: Callable[[dict[str, Any]], bool] | None = None


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


def get_stats_field(stats: dict, *keys: str, default: Any = None) -> Any:
    """Get a field from stats handling both camelCase and snake_case."""
    return get_field(stats, *keys, default=default)


def bytes_to_megabits(bytes_per_sec: float | None) -> float | None:
    """Convert bytes per second to megabits per second."""
    if bytes_per_sec is None:
        return None
    return round(bytes_per_sec * 8 / 1_000_000, 2)


def bytes_to_bits(bytes_per_sec: float | None) -> float | None:
    """Convert bytes per second to bits per second."""
    if bytes_per_sec is None:
        return None
    return bytes_per_sec * 8


def _uplink_rate_bps(stats: dict[str, Any], *keys: str) -> Any:
    """
    Get an uplink tx/rx rate candidate, preserving a legitimate 0.

    Reads the uplink stats container once, then tries the same key names
    against it and against the top-level stats dict, keeping the first
    candidate that is not None. A plain `or` chain here would discard a
    real `0 B/s` reading (traffic momentarily idle) in favour of a
    non-existent fallback, flipping the sensor state to `unknown`.
    """
    uplink = get_stats_field(stats, "uplink", "uplink_stats", default={})
    return first_not_none(
        get_field(uplink, *keys),
        get_field(stats, *keys),
    )


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
        value_fn=lambda stats: get_stats_field(
            stats, "cpuUtilizationPct", "cpu_utilization_pct", "cpu_percent", "cpu"
        ),
    ),
    UnifiInsightsSensorEntityDescription(
        key="memory_usage",
        translation_key="memory_usage",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda stats: get_stats_field(
            stats,
            "memoryUtilizationPct",
            "memory_utilization_pct",
            "memory_percent",
            "memory",
        ),
    ),
    UnifiInsightsSensorEntityDescription(
        key="uptime",
        translation_key="uptime",
        device_class=None,
        # Diagnostic - uptime is informational/troubleshooting data
        entity_category=EntityCategory.DIAGNOSTIC,
        # Gold: Disable diagnostic entities by default
        entity_registry_enabled_default=False,
        value_fn=lambda stats: format_uptime(
            get_stats_field(
                stats, "uptimeSec", "uptime_sec", "uptime_seconds", "uptime"
            )
        ),
    ),
    UnifiInsightsSensorEntityDescription(
        key="tx_rate",
        translation_key="tx_rate",
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=1,
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda stats: bytes_to_bits(
            _uplink_rate_bps(stats, "txRateBps", "tx_rate_bps", "tx_bytes_per_sec")
        ),
    ),
    UnifiInsightsSensorEntityDescription(
        key="rx_rate",
        translation_key="rx_rate",
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=1,
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda stats: bytes_to_bits(
            _uplink_rate_bps(stats, "rxRateBps", "rx_rate_bps", "rx_bytes_per_sec")
        ),
    ),
    UnifiInsightsSensorEntityDescription(
        key="poe_total_power",
        translation_key="poe_total_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        # Only show on switch devices (devices with 'switching' feature)
        required_feature="switching",
        value_fn=lambda stats: get_stats_field(
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
        value_fn=lambda port: (
            get_field(port, "stats", default={}).get("txBytes")
            or get_field(port, "stats", default={}).get("tx_bytes", 0)
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
        value_fn=lambda port: (
            get_field(port, "stats", default={}).get("rxBytes")
            or get_field(port, "stats", default={}).get("rx_bytes", 0)
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
            get_stats_field(
                stats, "uptimeSec", "uptime_sec", "uptime_seconds", "uptime"
            )
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
    coordinator: UnifiFacadeCoordinator,
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
            stats = ((coordinator.data.get("stats") or {}).get(site_id) or {}).get(
                device_id, {}
            )
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
            stats = ((coordinator.data.get("stats") or {}).get(site_id) or {}).get(
                device_id, {}
            )
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

    # Add WAN sensors for gateway devices
    # See is_gateway_device: covers the UCG/UXG/UDR/UDW consoles that issue
    # #151 reported as missing WAN sensors.
    if is_gateway_device(device_data):
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
    coordinator: UnifiFacadeCoordinator,
    site_id: str,
    device_id: str,
    device_data: dict[str, Any],
    known_sensor_keys: set[tuple[Any, ...]],
    *,
    entities: list[SensorEntity],
) -> None:
    """Discover port sensors for a network device."""
    ports = device_data.get("ports", [])
    if not ports:
        interfaces = get_field(device_data, "interfaces", default={})
        if isinstance(interfaces, dict):
            ports = get_field(interfaces, "ports", default=[])
    if not ports or not isinstance(ports, list):
        return

    active_port_indices: set[int] = set()
    for port in ports:
        if not isinstance(port, dict):
            continue
        port_idx = get_field(port, "idx", "index", "port_idx")
        if port_idx is None:
            continue
        # The legacy device path copies the raw index straight from the
        # payload, so it can still be a string here. _create_port_stats_fallback
        # keys its ports on int, and both paths feed the same UnifiPortSensor
        # unique ID, so normalise before the dedupe keys are built.
        with contextlib.suppress(ValueError, TypeError):
            port_idx = int(port_idx)

        port_state = get_field(port, "state", "status", default="DOWN")
        if str(port_state).upper() != "UP":
            continue

        with contextlib.suppress(ValueError, TypeError):
            active_port_indices.add(int(port_idx))

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
        stats = ((coordinator.data.get("stats") or {}).get(site_id) or {}).get(
            device_id, {}
        )
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
    coordinator: UnifiFacadeCoordinator,
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
    coordinator: UnifiFacadeCoordinator,
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
    coordinator: UnifiFacadeCoordinator,
    known_sensor_keys: set[tuple[Any, ...]],
    entities: list[SensorEntity],
) -> None:
    """Discover sensors for UniFi Protect."""
    if not coordinator.protect_client:
        return

    protect = coordinator.data.get("protect", {})
    if not isinstance(protect, dict):
        return

    sensors = protect.get("sensors", {})
    if isinstance(sensors, dict):
        for sensor_id, sensor_data in sensors.items():
            if not isinstance(sensor_data, dict):
                continue
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

    nvrs = protect.get("nvrs", {})
    if isinstance(nvrs, dict):
        for nvr_id, nvr_data in nvrs.items():
            if not isinstance(nvr_data, dict):
                continue
            for nvr_desc in NVR_SENSOR_TYPES:
                if nvr_desc.value_fn is None or nvr_desc.value_fn(nvr_data) is None:
                    continue
                nvr_key = (nvr_id, nvr_desc.key)
                if nvr_key not in known_sensor_keys:
                    known_sensor_keys.add(nvr_key)
                    entities.append(
                        UnifiProtectNVRSensor(
                            coordinator=coordinator,
                            description=nvr_desc,
                            device_id=nvr_id,
                        )
                    )


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
    if (
        entity.domain != "sensor"
        or entity.platform != DOMAIN
        or entity.config_entry_id != config_entry_id
        or is_storage_unique_id(entity.unique_id)
    ):
        return False
    prefix, separator, port_idx = entity.unique_id.rpartition("_")
    try:
        if not separator or str(int(port_idx)) != port_idx:
            return False
    except ValueError:
        return False
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
            return False
        return entity.translation_key == description.translation_key or (
            entity.translation_key in (None, "") and device_id in known_device_ids
        )
    return False


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: UnifiInsightsConfigEntry | CarrierFabricConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for UniFi Insights integration."""
    if isinstance(config_entry.runtime_data, CarrierFabricData):
        await async_setup_carrier_fabric_sensors(hass, config_entry, async_add_entities)
        return

    _LOGGER.debug("Setting up UniFi Insights sensors")

    # Migrate existing entities to pick up new suggested_unit_of_measurement
    _migrate_sensor_units(hass, config_entry)

    coordinator: UnifiFacadeCoordinator = config_entry.runtime_data.coordinator
    known_sensor_keys: set[tuple[Any, ...]] = set()
    initial_entities: list[SensorEntity] = []
    # Only the first pass feeds the stale-entity sweep below. Without this the
    # list would keep every entity discovered for the lifetime of the entry,
    # long after the one sweep that reads it has run.
    setup_complete = False

    @callback
    def async_discover_sensors() -> None:
        """Discover and add new sensors."""
        if not coordinator.data or not isinstance(coordinator.data, dict):
            return

        entities: list[SensorEntity] = []

        # Add sensors for each device in each site
        devices_by_site = coordinator.data.get("devices", {})
        if isinstance(devices_by_site, dict):
            for site_id, devices in devices_by_site.items():
                if not isinstance(devices, dict):
                    continue
                for device_id, device_data in devices.items():
                    if not isinstance(device_data, dict):
                        continue
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
        clients_by_site = coordinator.data.get("clients", {})
        if isinstance(clients_by_site, dict):
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
        wifi_by_site = coordinator.data.get("wifi", {})
        if isinstance(wifi_by_site, dict):
            for site_id, wifi_networks in wifi_by_site.items():
                if not isinstance(wifi_networks, dict):
                    continue
                for wifi_id, wifi_data in wifi_networks.items():
                    if not isinstance(wifi_data, dict):
                        continue
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
    devices_by_site = (
        coordinator.data.get("devices", {})
        if isinstance(coordinator.data, dict)
        else {}
    )
    known_device_ids = (
        {
            device_id
            for devices in devices_by_site.values()
            if isinstance(devices, dict)
            for device_id, device_data in devices.items()
            if isinstance(device_id, str)
            and device_id
            and isinstance(device_data, dict)
        }
        if isinstance(devices_by_site, dict)
        else set()
    )
    ent_reg = er.async_get(hass)
    stale = [
        entry
        for entry in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)
        if entry.unique_id not in created_uids
        and _is_owned_port_sensor(entry, config_entry.entry_id, known_device_ids)
    ]
    for entry in stale:
        _LOGGER.debug("Removing stale port sensor entity: %s", entry.unique_id)
        ent_reg.async_remove(entry.entity_id)


class UnifiInsightsSensor(UnifiInsightsEntity, SensorEntity):
    """Representation of a UniFi Insights Sensor."""

    entity_description: UnifiInsightsSensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
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
        # Special handling for sensors that come from device data (not stats)
        if self.entity_description.key in [
            "firmware_version",
            "wan_ip",
            "general_temperature",
            "ac_power_consumption",
            "ac_power_budget",
        ]:
            device = (
                self.coordinator.data["devices"]
                .get(self._site_id, {})
                .get(self._device_id)
            )
            if not device:
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
            if (
                not self.coordinator.data["stats"]
                .get(self._site_id, {})
                .get(self._device_id)
            ):
                _LOGGER.debug(
                    "No stats available for sensor %s (device %s in site %s)",
                    self.entity_description.key,
                    self._device_id,
                    self._site_id,
                )
                return None

            stats = self.coordinator.data["stats"][self._site_id][self._device_id]
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
        coordinator: UnifiFacadeCoordinator,
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

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        # Get device data to access port information
        device_data = (
            self.coordinator.data.get("devices", {})
            .get(self._site_id, {})
            .get(self._device_id, {})
        )

        if not device_data:
            _LOGGER.debug(
                "No device data available for port sensor %s (device %s in site %s)",
                self.entity_description.key,
                self._device_id,
                self._site_id,
            )
            return None

        # Find the port data
        # Port data can come from:
        # 1. device_data["ports"] - merged from legacy port_table by coordinator
        # 2. device_data["interfaces"]["ports"] - new API format (dict)
        ports = device_data.get("ports", [])
        if not ports:
            interfaces = device_data.get("interfaces", {})
            ports = interfaces.get("ports", []) if isinstance(interfaces, dict) else []
        port_data = None
        for port in ports:
            idx = port.get("idx") or port.get("port_idx")
            if idx == self._port_idx:
                port_data = port
                break

        if not port_data:
            # PoE power can be sourced from stats when interfaces.ports is unavailable
            if self.entity_description.key == "port_poe_power":
                stats = (
                    self.coordinator.data.get("stats", {})
                    .get(self._site_id, {})
                    .get(self._device_id, {})
                )
                if isinstance(stats, dict):
                    poe_ports = stats.get("poe_ports")
                    if isinstance(poe_ports, dict):
                        # An int-keyed poe_ports (e.g. {3: 0}) must not fall
                        # through to the string-key lookup: `or` would treat
                        # a real 0 W reading as absent.
                        watts = poe_ports.get(self._port_idx)
                        if watts is None:
                            watts = poe_ports.get(str(self._port_idx))
                        if isinstance(watts, (int, float)):
                            return float(watts)
                        if isinstance(watts, str):
                            try:
                                return float(watts)
                            except ValueError:
                                return None

            # TX/RX bytes can be sourced from stats when interfaces.ports is unavailable
            if self.entity_description.key in ("port_tx_bytes", "port_rx_bytes"):
                stats = (
                    self.coordinator.data.get("stats", {})
                    .get(self._site_id, {})
                    .get(self._device_id, {})
                )
                if isinstance(stats, dict):
                    port_bytes = stats.get("port_bytes")
                    if isinstance(port_bytes, dict):
                        pb = port_bytes.get(self._port_idx) or port_bytes.get(
                            str(self._port_idx)
                        )
                        if isinstance(pb, dict):
                            v = (
                                pb.get("tx_bytes")
                                if self.entity_description.key == "port_tx_bytes"
                                else pb.get("rx_bytes")
                            )
                            if isinstance(v, (int, float)):
                                return int(v)

            # TX/RX rate sourced from stats when interfaces.ports is unavailable
            if self.entity_description.key in ("port_tx_rate", "port_rx_rate"):
                return self._get_port_rate_value()

            _LOGGER.debug(
                "No port data available for port %d on device %s",
                self._port_idx,
                self._device_id,
            )
            return None

        # Port TX/RX rate always comes from computed stats, not port data
        if self.entity_description.key in ("port_tx_rate", "port_rx_rate"):
            return self._get_port_rate_value()

        # Prefer PoE watts from coordinator stats when available
        if self.entity_description.key == "port_poe_power":
            stats = (
                self.coordinator.data.get("stats", {})
                .get(self._site_id, {})
                .get(self._device_id, {})
            )
            if isinstance(stats, dict):
                poe_ports = stats.get("poe_ports")
                if isinstance(poe_ports, dict):
                    # An int-keyed poe_ports (e.g. {3: 0}) must not fall
                    # through to the string-key lookup: `or` would treat
                    # a real 0 W reading as absent.
                    watts = poe_ports.get(self._port_idx)
                    if watts is None:
                        watts = poe_ports.get(str(self._port_idx))
                    if isinstance(watts, (int, float)):
                        return float(watts)
                    if isinstance(watts, str):
                        try:
                            return float(watts)
                        except ValueError:
                            return None

        # TX/RX bytes from coordinator stats
        if self.entity_description.key in ["port_tx_bytes", "port_rx_bytes"]:
            stats = (
                self.coordinator.data.get("stats", {})
                .get(self._site_id, {})
                .get(self._device_id, {})
            )
            if not isinstance(stats, dict):
                return None

            counter_key = (
                "tx_bytes"
                if self.entity_description.key == "port_tx_bytes"
                else "rx_bytes"
            )

            # 1) Preferred: legacy-mapped per-port counters
            port_bytes = stats.get("port_bytes")
            if isinstance(port_bytes, dict):
                pb = port_bytes.get(self._port_idx) or port_bytes.get(
                    str(self._port_idx)
                )
                if isinstance(pb, dict):
                    v = pb.get(counter_key)
                    if isinstance(v, (int, float)):
                        return int(v)

            # 2) Optional fallback: upstream per-port counters dict
            port_data = stats.get("port_data")
            if isinstance(port_data, dict):
                pb = port_data.get(self._port_idx) or port_data.get(str(self._port_idx))
                if isinstance(pb, dict):
                    v = pb.get(counter_key)
                    if isinstance(v, (int, float)):
                        return int(v)

            return None

        # Use value_fn to extract the value
        value = self.entity_description.value_fn(port_data)  # type: ignore[misc]

        _LOGGER.debug(
            "Port sensor %s for port %d on device %s updated to %s %s",
            self.entity_description.key,
            self._port_idx,
            self._device_id,
            value,
            self.native_unit_of_measurement or "",
        )

        return value

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # PoE power availability based on coordinator stats
        if self.entity_description.key == "port_poe_power":
            if not self.coordinator.device_available:
                return False
            stats = (
                self.coordinator.data.get("stats", {})
                .get(self._site_id, {})
                .get(self._device_id, {})
            )
            if isinstance(stats, dict):
                poe_ports = stats.get("poe_ports")
                if isinstance(poe_ports, dict):
                    # If the device provides poe_ports at all,
                    # keep PoE power sensors available
                    # (link state may be DOWN while PoE is
                    # disabled or idle).
                    return True
            # Fall through to standard port-state availability logic

        # TX/RX availability based on coordinator stats
        if self.entity_description.key in ("port_tx_bytes", "port_rx_bytes"):
            if not self.coordinator.device_available:
                return False
            stats = (
                self.coordinator.data.get("stats", {})
                .get(self._site_id, {})
                .get(self._device_id, {})
            )
            if isinstance(stats, dict):
                port_bytes = stats.get("port_bytes")
                if isinstance(port_bytes, dict):
                    pb = port_bytes.get(self._port_idx) or port_bytes.get(
                        str(self._port_idx)
                    )
                    if isinstance(pb, dict):
                        return True
            # Fall through to standard port-state availability logic

        # TX/RX rate availability based on computed port_rates
        if self.entity_description.key in ("port_tx_rate", "port_rx_rate"):
            if not self.coordinator.device_available:
                return False
            stats = (
                self.coordinator.data.get("stats", {})
                .get(self._site_id, {})
                .get(self._device_id, {})
            )
            if isinstance(stats, dict):
                port_rates = stats.get("port_rates")
                if isinstance(port_rates, dict):
                    pr = port_rates.get(self._port_idx) or port_rates.get(
                        str(self._port_idx)
                    )
                    if isinstance(pr, dict):
                        return True
            # Fall through to standard port-state availability logic

        # Port sensors are available if the device is available AND the port is UP
        if not self.coordinator.device_available:
            return False

        # Get device data
        devices = self.coordinator.data.get("devices", {})
        if not isinstance(devices, dict):
            return False
        site_devices = devices.get(self._site_id, {})
        if not isinstance(site_devices, dict):
            return False
        device_data = site_devices.get(self._device_id, {})
        if not device_data or not isinstance(device_data, dict):
            return False

        # Find the port data
        # Check device_data["ports"] first (from legacy merge),
        # fall back to interfaces["ports"]
        port_list = device_data.get("ports", [])
        if not port_list:
            interfaces = device_data.get("interfaces", {})
            if isinstance(interfaces, dict):
                port_list = interfaces.get("ports", [])
        if not isinstance(port_list, list):
            return False

        port_data = None
        for port in port_list:
            idx = port.get("idx") or port.get("port_idx")
            if idx == self._port_idx:
                port_data = port
                break

        if not port_data:
            return False

        # SFP info sensors stay available regardless of port state
        if self.entity_description.key.startswith("port_sfp_"):
            return True

        # Port sensor is only available if port state is UP
        port_state = port_data.get("state", "DOWN")
        return isinstance(port_state, str) and port_state == "UP"

    def _get_port_rate_value(self) -> StateType:
        """Get the computed port byte rate as bits/sec."""
        stats = (
            self.coordinator.data.get("stats", {})
            .get(self._site_id, {})
            .get(self._device_id, {})
        )
        if not isinstance(stats, dict):
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

    def _find_port_data(self) -> dict[str, Any] | None:
        """Find port data for this sensor's port index."""
        device_data = (
            self.coordinator.data.get("devices", {})
            .get(self._site_id, {})
            .get(self._device_id, {})
        )
        if not device_data:
            return None
        ports = device_data.get("ports", [])
        if not ports:
            interfaces = device_data.get("interfaces", {})
            if isinstance(interfaces, dict):
                ports = interfaces.get("ports", [])
        for port in ports:
            idx = port.get("idx") or port.get("port_idx")
            if idx == self._port_idx:
                return dict(port)
        return None

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
        coordinator: UnifiFacadeCoordinator,
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
        device_data = (
            self.coordinator.data.get("devices", {})
            .get(self._site_id, {})
            .get(self._device_id, {})
        )
        if not isinstance(device_data, dict):
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
    """Representation of a UniFi Protect Sensor."""

    entity_description: UnifiProtectSensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        description: UnifiProtectSensorEntityDescription,
        device_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator, description.device_type or "", device_id, description.key
        )
        self.entity_description = description

        # Set entity category for battery sensors
        if description.key == "battery":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

        # Update initial state
        self._update_from_data()

        _LOGGER.debug(
            "Initializing %s sensor for %s device %s",
            description.key,
            description.device_type,
            device_id,
        )

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        device_data = self.coordinator.data["protect"][
            f"{self.entity_description.device_type}s"
        ].get(self._device_id)
        if not device_data:
            return None

        value = self.entity_description.value_fn(device_data)  # type: ignore[misc]

        _LOGGER.debug(
            "Sensor %s for %s device %s updated to %s %s",
            self.entity_description.key,
            self.entity_description.device_type,
            self._device_id,
            value,
            self.native_unit_of_measurement or "",
        )

        return value

    def _update_from_data(self) -> None:
        """Update entity from data."""
        device_data = self.coordinator.data["protect"][
            f"{self.entity_description.device_type}s"
        ].get(self._device_id, {})

        # Set attributes based on sensor type
        if self.entity_description.key == "temperature":
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


class UnifiProtectNVRSensor(UnifiProtectEntity, SensorEntity):
    """Representation of a UniFi Protect NVR Sensor."""

    entity_description: UnifiProtectSensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        description: UnifiProtectSensorEntityDescription,
        device_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator, description.device_type or "", device_id, description.key
        )
        self.entity_description = description

        # Update initial state
        self._update_from_data()

        _LOGGER.debug(
            "Initializing %s sensor for NVR %s",
            description.key,
            device_id,
        )

    @property
    def available(self) -> bool:
        """
        Return True if entity is available.

        NVR doesn't have a 'state' field like cameras, so we check if we have
        valid NVR data and if storage info is available for storage sensors.
        """
        if not self.coordinator.protect_available:
            return False

        nvr_data = self.coordinator.data["protect"]["nvrs"].get(self._device_id)
        if not nvr_data or not isinstance(nvr_data, dict):
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
        nvr_data = self.coordinator.data["protect"]["nvrs"].get(self._device_id)
        if not nvr_data:
            return None

        value = self.entity_description.value_fn(nvr_data)  # type: ignore[misc]

        _LOGGER.debug(
            "NVR sensor %s for device %s updated to %s %s",
            self.entity_description.key,
            self._device_id,
            value,
            self.native_unit_of_measurement or "",
        )

        return value

    def _update_from_data(self) -> None:
        """Update entity from data."""
        nvr_data = self.coordinator.data["protect"]["nvrs"].get(self._device_id, {})

        storage = normalize_nvr_storage(nvr_data)
        storage_used = storage.used_bytes
        storage_total = storage.total_bytes

        # Set attributes
        self._attr_extra_state_attributes = {
            ATTR_NVR_ID: self._device_id,
            ATTR_NVR_NAME: nvr_data.get("name"),
            ATTR_NVR_VERSION: nvr_data.get("version"),
            ATTR_NVR_STORAGE_USED: _bytes_to_gb(storage_used),
            ATTR_NVR_STORAGE_TOTAL: _bytes_to_gb(storage_total),
            ATTR_NVR_STORAGE_AVAILABLE: _calculate_storage_available(nvr_data),
            ATTR_NVR_STORAGE_USED_PERCENT: _calculate_storage_percent(nvr_data),
        }


class UnifiSiteClientSensor(CoordinatorEntity[UnifiFacadeCoordinator], SensorEntity):
    """Representation of a site-level client count sensor."""

    _attr_has_entity_name = True
    entity_description: UnifiInsightsSensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        description: UnifiInsightsSensorEntityDescription,
        site_id: str,
        *,
        unique_id: str,
    ) -> None:
        """Initialize the site-level client sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._site_id = site_id

        self._attr_unique_id = unique_id
        self._attr_device_info = DeviceInfo(**self._build_device_info())  # type: ignore[typeddict-item]

    def _build_device_info(self) -> dict[str, Any]:
        """Build device info for site-level entity grouping."""
        return build_site_device_info(self.coordinator.data, self._site_id)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return bool(self.coordinator.device_available)

    @property
    def native_value(self) -> StateType:
        """Return the client count."""
        clients = self.coordinator.data.get("clients", {}).get(self._site_id, {})
        if not isinstance(clients, dict):
            return 0
        return self.entity_description.value_fn(clients)  # type: ignore[misc]


class UnifiWifiClientCountSensor(
    CoordinatorEntity[UnifiFacadeCoordinator], SensorEntity
):
    """Number of clients connected to a specific WiFi network (SSID)."""

    _attr_has_entity_name = True
    _attr_translation_key = "wifi_client_count"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
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
        result: dict[str, Any] = (
            self.coordinator.data.get("wifi", {})
            .get(self._site_id, {})
            .get(self._wifi_id, {})
        )
        return result

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return bool(
            self.coordinator.wifi_available(self._site_id) and self._get_wifi_data()
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
