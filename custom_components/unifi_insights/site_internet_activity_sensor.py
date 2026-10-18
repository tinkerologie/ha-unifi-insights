# Copyright 2026 UniFi Insights contributors
"""Site-level historical internet activity sensors for UniFi Insights."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfInformation
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinators import UnifiFacadeCoordinator
from .entity import build_site_device_info, find_site_gateway_device_id
from .helpers import async_migrate_site_entity


@dataclass
class UnifiSiteInternetActivitySensorEntityDescription(  # type: ignore[misc]
    SensorEntityDescription
):
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


class UnifiSiteInternetActivitySensor(
    CoordinatorEntity[UnifiFacadeCoordinator], SensorEntity
):
    """Sensor showing historical WAN download/upload bytes for a UniFi site."""

    _attr_has_entity_name = True
    entity_description: UnifiSiteInternetActivitySensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        description: UnifiSiteInternetActivitySensorEntityDescription,
        site_id: str,
        *,
        unique_id: str,
    ) -> None:
        """Initialize the site internet activity sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._site_id = site_id

        self._attr_unique_id = unique_id
        self._attr_device_info = DeviceInfo(**self._build_device_info())  # type: ignore[typeddict-item]
        self._attr_extra_state_attributes = {
            "period": description.period_label,
            "unifi_window": description.unifi_window,
            "direction": description.direction,
            "report_interval": description.report_interval,
        }

    def _find_gateway_device_id(self) -> str | None:
        """Find the gateway device ID for this site."""
        return find_site_gateway_device_id(self.coordinator.data, self._site_id)

    def _build_device_info(self) -> dict[str, Any]:
        """Build device info for site-level entity grouping."""
        return build_site_device_info(self.coordinator.data, self._site_id)

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
        if not bool(getattr(self.coordinator, "config_available", True)):
            return False
        section_available_fn = getattr(
            self.coordinator, "internet_activity_available", None
        )
        if callable(section_available_fn) and not bool(
            section_available_fn(self._site_id)
        ):
            return False
        unavailable_sites = self.coordinator.data.get("internet_activity_unavailable")
        if (
            isinstance(unavailable_sites, (set, list, tuple))
            and self._site_id in unavailable_sites
        ):
            return False
        return self._get_window_value() is not None

    @property
    def native_value(self) -> int | None:
        """Return the aggregated byte usage for the configured rolling window."""
        return self._get_window_value()


def _discover_site_internet_activity_sensors(
    coordinator: UnifiFacadeCoordinator,
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
