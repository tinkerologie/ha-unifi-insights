# Copyright 2026 UniFi Insights contributors
"""Support for UniFi Insights binary sensors."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import callback
from homeassistant.helpers.entity import EntityCategory

from .const import (
    ATTR_CAMERA_ID,
    ATTR_CAMERA_NAME,
    ATTR_LAST_MOTION,
    ATTR_SENSOR_EXTERNAL_LEAK_DETECTED,
    ATTR_SENSOR_EXTERNAL_LEAK_DETECTED_AT,
    ATTR_SENSOR_ID,
    ATTR_SENSOR_IS_OPENED,
    ATTR_SENSOR_LEAK_DETECTED,
    ATTR_SENSOR_LEAK_DETECTED_AT,
    ATTR_SENSOR_MOTION_DETECTED,
    ATTR_SENSOR_MOTION_DETECTED_AT,
    ATTR_SENSOR_NAME,
    ATTR_SENSOR_OPEN_STATUS_CHANGED_AT,
    ATTR_SENSOR_TAMPER_DETECTED,
    ATTR_SENSOR_TAMPER_DETECTED_AT,
    DEVICE_TYPE_CAMERA,
    DEVICE_TYPE_NVR,
    DEVICE_TYPE_SENSOR,
    SMART_DETECT_ANIMAL,
    SMART_DETECT_PACKAGE,
    SMART_DETECT_PERSON,
    SMART_DETECT_VEHICLE,
)
from .entity import (
    UnifiInsightsEntity,
    UnifiProtectEntity,
    get_field,
    is_device_online,
    is_gateway_device,
)
from .helpers import get_port_label, is_doorbell_camera_model
from .mobility_entity import async_setup_mobility_binary_sensors
from .protect_security_entity import discover_protect_security_binary_sensors
from .storage_entity import discover_storage_binary_sensors
from .storage_telemetry import normalize_nvr_storage

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import UnifiInsightsConfigEntry
    from .coordinators import UnifiFacadeCoordinator

_LOGGER = logging.getLogger(__name__)

# Coordinator handles updates centrally (Gold/Platinum requirement)
PARALLEL_UPDATES = 0


def _get_supported_smart_detect_types(camera_data: dict[str, Any]) -> list[str]:
    """
    Get supported smart detection types for a camera.

    The supported types are in featureFlags.smartDetectTypes, not smartDetectTypes.
    """
    feature_flags = camera_data.get("featureFlags", {})
    if isinstance(feature_flags, dict):
        result: list[str] = feature_flags.get("smartDetectTypes", [])
        return result
    return []


def _is_smart_detect_active(camera_data: dict[str, Any], detect_type: str) -> bool:
    """
    Check if a specific smart detection type is currently active.

    For smart detection to be active:
    1. The camera must support this smart detect type (in featureFlags)
    2. Motion must be currently detected (isMotionDetected or via lastMotionStart/End)
    3. Smart detection must be active (isSmartDetected or via lastSmartDetectTypes)
    4. The specific type must be in the last detected types
    """
    # Check if camera supports this detect type
    supported_types = _get_supported_smart_detect_types(camera_data)
    if detect_type not in supported_types:
        return False

    # Check if motion is currently detected
    is_motion = camera_data.get("isMotionDetected", False)
    if not is_motion:
        # Fallback to event-based detection
        motion_start = camera_data.get("lastMotionStart")
        motion_end = camera_data.get("lastMotionEnd")
        is_motion = motion_start is not None and motion_end is None

    if not is_motion:
        return False

    # Check if smart detection is active and matches the type
    is_smart = camera_data.get("isSmartDetected", False)
    if is_smart:
        # When isSmartDetected is True, check lastSmartDetectTypes for specific type
        last_types = camera_data.get("lastSmartDetectTypes", [])
        return detect_type in last_types

    # Fallback to just checking lastSmartDetectTypes
    last_types = camera_data.get("lastSmartDetectTypes", [])
    return detect_type in last_types


def _is_doorbell_camera(camera_data: dict[str, Any]) -> bool:
    """Check if a camera is a doorbell camera."""
    # The camera's type metadata and the API type field
    if is_doorbell_camera_model(camera_data):
        return True

    # Fallback: Check camera name for doorbell indicators
    camera_name = (camera_data.get("name") or "").lower()
    return bool(
        any(
            doorbell_name in camera_name
            for doorbell_name in ["doorbell", "door bell", "front door", "entrance"]
        )
    )


def _water_leak_channel_count(sensor_data: dict[str, Any]) -> int:
    """
    Return the number of water leak channels a Protect sensor reports.

    The public Protect API advertises leak support via
    ``featureFlags.waterLeak.channelCount`` (e.g. USL-Environmental reports
    2: the internal contacts plus the external probe). Sensors that do not
    support leak detection omit the ``waterLeak`` flag entirely.
    """
    feature_flags = get_field(sensor_data, "featureFlags", "feature_flags")
    if not isinstance(feature_flags, dict):
        return 0
    water_leak = get_field(feature_flags, "waterLeak", "water_leak")
    if not isinstance(water_leak, dict):
        return 0
    raw_count = get_field(water_leak, "channelCount", "channel_count")
    if isinstance(raw_count, bool):
        return 0
    if isinstance(raw_count, int):
        return raw_count
    if isinstance(raw_count, str) and raw_count.isdigit():
        return int(raw_count)
    return 0


def _camera_has_mic(camera_data: dict[str, Any]) -> bool:
    """Return True if the camera has a microphone, defaulting to True when missing."""
    feature_flags = get_field(camera_data, "featureFlags", "feature_flags")
    if not isinstance(feature_flags, dict):
        return True
    has_mic = get_field(feature_flags, "hasMic", "has_mic")
    if has_mic is None:
        return True
    return bool(has_mic)


def _supports_internal_leak(sensor_data: dict[str, Any]) -> bool:
    """Return True if the sensor supports (internal) leak detection."""
    return (
        get_field(sensor_data, "mountType", "mount_type", default="")
        in ("leak", "water")
        or get_field(sensor_data, "isLeakDetected", "is_leak_detected") is not None
        or _water_leak_channel_count(sensor_data) >= 1
    )


def _supports_external_leak(sensor_data: dict[str, Any]) -> bool:
    """Return True if the sensor has a second (external probe) leak channel."""
    return (
        get_field(sensor_data, "isExternalLeakDetected", "is_external_leak_detected")
        is not None
        or _water_leak_channel_count(sensor_data) >= 2  # noqa: PLR2004
    )


def _is_internal_leak_detected(sensor_data: dict[str, Any]) -> bool:
    """
    Return the internal leak state.

    Prefers an explicit ``isLeakDetected`` flag when the API provides one.
    Otherwise the state is derived from ``leakDetectedAt``, which the API
    sets while a leak is active and clears (null) once the sensor is dry.
    """
    explicit = get_field(
        sensor_data, "isLeakDetected", "is_leak_detected", "leak_detected"
    )
    if explicit is not None:
        return bool(explicit)
    return bool(get_field(sensor_data, "leakDetectedAt", "leak_detected_at"))


def _is_external_leak_detected(sensor_data: dict[str, Any]) -> bool:
    """Return the external probe leak state (derived like the internal one)."""
    explicit = get_field(
        sensor_data, "isExternalLeakDetected", "is_external_leak_detected"
    )
    if explicit is not None:
        return bool(explicit)
    return bool(
        get_field(sensor_data, "externalLeakDetectedAt", "external_leak_detected_at")
    )


@dataclass
class UnifiInsightsBinarySensorEntityDescription(BinarySensorEntityDescription):  # type: ignore[misc]
    """Class describing UniFi Insights binary sensor entities."""

    value_fn: Callable[[dict[str, Any]], bool | None] | None = None
    device_type: str | None = None
    entity_type: str = "device"  # "device" or "protect"
    capability_fn: Callable[[dict[str, Any]], bool] | None = None


BINARY_SENSOR_TYPES: tuple[UnifiInsightsBinarySensorEntityDescription, ...] = (
    UnifiInsightsBinarySensorEntityDescription(
        key="storage_degraded",
        translation_key="storage_degraded",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: normalize_nvr_storage(data).degraded,
        device_type=DEVICE_TYPE_NVR,
        entity_type="protect",
        capability_fn=lambda data: normalize_nvr_storage(data).degraded is not None,
    ),
    UnifiInsightsBinarySensorEntityDescription(
        key="storage_nearly_full",
        translation_key="storage_nearly_full",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: normalize_nvr_storage(data).nearly_full,
        device_type=DEVICE_TYPE_NVR,
        entity_type="protect",
        capability_fn=lambda data: normalize_nvr_storage(data).nearly_full is not None,
    ),
    # Network device status
    UnifiInsightsBinarySensorEntityDescription(
        key="device_status",
        translation_key="device_status",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=is_device_online,
        entity_type="device",
    ),
    # WAN status (gateway devices only)
    UnifiInsightsBinarySensorEntityDescription(
        key="wan_status",
        translation_key="wan_status",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=is_device_online,
        entity_type="device",
    ),
    # Camera motion detection - uses isMotionDetected from API
    UnifiInsightsBinarySensorEntityDescription(
        key="camera_motion",
        translation_key="camera_motion",
        device_class=BinarySensorDeviceClass.MOTION,
        value_fn=lambda device: (
            device.get("isMotionDetected", False)
            or (
                device.get("lastMotionStart") is not None
                and device.get("lastMotionEnd") is None
            )
        ),
        device_type=DEVICE_TYPE_CAMERA,
        entity_type="protect",
    ),
    # Camera person detection - uses helper to check feature flags and active detection
    UnifiInsightsBinarySensorEntityDescription(
        key="camera_person_detection",
        translation_key="camera_person_detection",
        device_class=BinarySensorDeviceClass.MOTION,
        value_fn=lambda device: _is_smart_detect_active(device, SMART_DETECT_PERSON),
        device_type=DEVICE_TYPE_CAMERA,
        entity_type="protect",
    ),
    # Camera vehicle detection
    UnifiInsightsBinarySensorEntityDescription(
        key="camera_vehicle_detection",
        translation_key="camera_vehicle_detection",
        device_class=BinarySensorDeviceClass.MOTION,
        value_fn=lambda device: _is_smart_detect_active(device, SMART_DETECT_VEHICLE),
        device_type=DEVICE_TYPE_CAMERA,
        entity_type="protect",
    ),
    # Camera animal detection
    UnifiInsightsBinarySensorEntityDescription(
        key="camera_animal_detection",
        translation_key="camera_animal_detection",
        device_class=BinarySensorDeviceClass.MOTION,
        value_fn=lambda device: _is_smart_detect_active(device, SMART_DETECT_ANIMAL),
        device_type=DEVICE_TYPE_CAMERA,
        entity_type="protect",
    ),
    # Camera package detection
    UnifiInsightsBinarySensorEntityDescription(
        key="camera_package_detection",
        translation_key="camera_package_detection",
        device_class=BinarySensorDeviceClass.MOTION,
        value_fn=lambda device: _is_smart_detect_active(device, SMART_DETECT_PACKAGE),
        device_type=DEVICE_TYPE_CAMERA,
        entity_type="protect",
    ),
    # Camera doorbell ring
    UnifiInsightsBinarySensorEntityDescription(
        key="camera_doorbell_ring",
        translation_key="camera_doorbell_ring",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        value_fn=lambda device: (
            device.get("lastRingStart") is not None
            and device.get("lastRingEnd") is None
        ),
        device_type=DEVICE_TYPE_CAMERA,
        entity_type="protect",
    ),
    # Camera microphone status (read-only)
    UnifiInsightsBinarySensorEntityDescription(
        key="camera_microphone",
        translation_key="camera_microphone",
        device_type=DEVICE_TYPE_CAMERA,
        entity_type="protect",
        entity_category=EntityCategory.DIAGNOSTIC,
        capability_fn=_camera_has_mic,
        value_fn=lambda device: bool(
            device.get("isMicEnabled")
            if device.get("isMicEnabled") is not None
            else device.get("micEnabled", False)
        ),
    ),
    # Sensor motion detection
    UnifiInsightsBinarySensorEntityDescription(
        key="sensor_motion",
        translation_key="sensor_motion",
        device_class=BinarySensorDeviceClass.MOTION,
        value_fn=lambda device: get_field(
            device,
            "isMotionDetected",
            "is_motion_detected",
            "motion_detected",
            default=False,
        ),
        device_type=DEVICE_TYPE_SENSOR,
        entity_type="protect",
        capability_fn=lambda data: (
            get_field(data, "mountType", "mount_type", default="") == "motion"
            or get_field(data, "isMotionDetected", "is_motion_detected") is not None
        ),
    ),
    # Sensor door/window status
    UnifiInsightsBinarySensorEntityDescription(
        key="sensor_door",
        translation_key="sensor_door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda device: get_field(
            device, "isOpened", "is_opened", "opened", default=False
        ),
        device_type=DEVICE_TYPE_SENSOR,
        entity_type="protect",
        capability_fn=lambda data: (
            get_field(data, "mountType", "mount_type", default="") in ("door", "window")
            or get_field(data, "isOpened", "is_opened") is not None
        ),
    ),
    # Sensor tamper detection
    UnifiInsightsBinarySensorEntityDescription(
        key="sensor_tamper",
        translation_key="sensor_tamper",
        device_class=BinarySensorDeviceClass.TAMPER,
        value_fn=lambda device: get_field(
            device,
            "isTamperingDetected",
            "is_tampering_detected",
            "tampering_detected",
            default=False,
        ),
        device_type=DEVICE_TYPE_SENSOR,
        entity_type="protect",
    ),
    # Sensor leak detection - internal contacts (USL-Leak, USL-Environmental)
    UnifiInsightsBinarySensorEntityDescription(
        key="sensor_leak",
        translation_key="sensor_leak",
        device_class=BinarySensorDeviceClass.MOISTURE,
        value_fn=_is_internal_leak_detected,
        device_type=DEVICE_TYPE_SENSOR,
        entity_type="protect",
        capability_fn=_supports_internal_leak,
    ),
    # Sensor leak detection - external probe (second waterLeak channel)
    UnifiInsightsBinarySensorEntityDescription(
        key="sensor_leak_external",
        translation_key="sensor_leak_external",
        device_class=BinarySensorDeviceClass.MOISTURE,
        value_fn=_is_external_leak_detected,
        device_type=DEVICE_TYPE_SENSOR,
        entity_type="protect",
        capability_fn=_supports_external_leak,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors for UniFi Insights integration."""
    _ = hass
    coordinator: UnifiFacadeCoordinator = config_entry.runtime_data.coordinator
    known_sensor_keys: set[tuple[Any, ...]] = set()

    @callback
    def async_discover_binary_sensors() -> None:
        """Discover and add new binary sensors."""
        if not coordinator.data or not isinstance(coordinator.data, dict):
            return

        entities: list[BinarySensorEntity] = []

        # Add binary sensors for each device in each site
        devices_by_site = coordinator.data.get("devices", {})
        if isinstance(devices_by_site, dict):
            for site_id, devices in devices_by_site.items():
                if not isinstance(devices, dict):
                    continue
                site_data = coordinator.get_site(site_id)
                site_name = (
                    (site_data.get("meta") or {}).get("name", site_id)
                    if site_data
                    else site_id
                )

                _LOGGER.debug(
                    "Processing site %s (%s) with %d devices",
                    site_id,
                    site_name,
                    len(devices),
                )

                for device_id, device_data in devices.items():
                    if not isinstance(device_data, dict):
                        continue
                    device_name = device_data.get("name", device_id)

                    _LOGGER.debug(
                        "Creating binary sensors for device %s (%s) in site %s (%s)",
                        device_id,
                        device_name,
                        site_id,
                        site_name,
                    )

                    for description in BINARY_SENSOR_TYPES:
                        if description.entity_type == "device":
                            # Skip WAN status sensor for non-gateway devices
                            if description.key == "wan_status" and not (
                                is_gateway_device(device_data)
                            ):
                                _LOGGER.debug(
                                    "Skipping WAN status sensor for non-gateway device "
                                    "%s (%s)",
                                    device_id,
                                    device_name,
                                )
                                continue

                            key = (site_id, device_id, description.key)
                            if key in known_sensor_keys:
                                continue
                            known_sensor_keys.add(key)
                            entities.append(
                                UnifiInsightsBinarySensor(
                                    coordinator=coordinator,
                                    description=description,
                                    site_id=site_id,
                                    device_id=device_id,
                                )
                            )

                    # Add SFP module binary sensors for ports with SFP media type
                    ports = device_data.get("ports", [])
                    if isinstance(ports, list):
                        for port in ports:
                            if not isinstance(port, dict):
                                continue
                            media = port.get("media", "")
                            if not isinstance(media, str) or not media.startswith(
                                "SFP"
                            ):
                                continue
                            port_idx = port.get("idx") or port.get("port_idx")
                            if port_idx is None:
                                continue
                            sfp_key = (site_id, device_id, port_idx, "sfp_present")
                            if sfp_key in known_sensor_keys:
                                continue
                            known_sensor_keys.add(sfp_key)
                            port_name = get_port_label(port, port_idx)
                            entities.append(
                                UnifiPortBinarySensor(
                                    coordinator=coordinator,
                                    site_id=site_id,
                                    device_id=device_id,
                                    port_idx=port_idx,
                                    port_label=port_name,
                                )
                            )

                    # Per-WAN link connectivity (merged legacy WAN data)
                    wans = device_data.get("wans", [])
                    if isinstance(wans, list):
                        for wan in wans:
                            if not isinstance(wan, dict) or not wan.get("key"):
                                continue
                            wan_key = (site_id, device_id, wan["key"], "wan_link")
                            if wan_key in known_sensor_keys:
                                continue
                            known_sensor_keys.add(wan_key)
                            entities.append(
                                UnifiInsightsWanLinkBinarySensor(
                                    coordinator=coordinator,
                                    site_id=site_id,
                                    device_id=device_id,
                                    wan_key=wan["key"],
                                    wan_name=wan.get("name") or wan["key"].upper(),
                                )
                            )

                    entities.extend(
                        discover_storage_binary_sensors(
                            coordinator,
                            site_id,
                            device_id,
                            device_data,
                            known_sensor_keys,
                        )
                    )

                    # Site-to-site VPN tunnels, one sensor each, on the gateway.
                    # Devices reporting WAN links route traffic even when their
                    # model/features are not recognised.
                    site_vpns = coordinator.data.get("site_vpns", {})
                    tunnels = (
                        site_vpns.get(site_id) if isinstance(site_vpns, dict) else None
                    )
                    if isinstance(tunnels, dict) and (
                        is_gateway_device(device_data) or wans
                    ):
                        for tunnel_id, tunnel in tunnels.items():
                            if not isinstance(tunnel, dict) or not tunnel.get(
                                "enabled", True
                            ):
                                continue
                            vpn_key = (site_id, "site_to_site_vpn", tunnel_id)
                            if vpn_key in known_sensor_keys:
                                continue
                            known_sensor_keys.add(vpn_key)
                            entities.append(
                                UnifiInsightsSiteToSiteVpnBinarySensor(
                                    coordinator=coordinator,
                                    site_id=site_id,
                                    device_id=device_id,
                                    tunnel_id=tunnel_id,
                                    tunnel_name=tunnel.get("name") or tunnel_id,
                                )
                            )

        # Add binary sensors for Protect devices
        if coordinator.protect_client:
            protect = coordinator.data.get("protect", {})
            if isinstance(protect, dict):
                # Add camera binary sensors
                cameras = protect.get("cameras", {})
                if isinstance(cameras, dict):
                    for camera_id, camera_data in cameras.items():
                        if not isinstance(camera_data, dict):
                            continue
                        camera_name = camera_data.get("name", camera_id)

                        _LOGGER.debug(
                            "Creating binary sensors for camera %s (%s)",
                            camera_id,
                            camera_name,
                        )

                        for description in BINARY_SENSOR_TYPES:
                            if (
                                description.entity_type == "protect"
                                and description.device_type == DEVICE_TYPE_CAMERA
                                and (
                                    description.capability_fn is None
                                    or description.capability_fn(camera_data)
                                )
                            ):
                                # Skip doorbell sensors for non-doorbell cameras
                                if description.key in [
                                    "camera_package_detection",
                                    "camera_doorbell_ring",
                                ] and not _is_doorbell_camera(camera_data):
                                    _LOGGER.debug(
                                        "Skipping %s sensor for non-doorbell "
                                        "camera %s (%s)",
                                        description.key,
                                        camera_id,
                                        camera_name,
                                    )
                                    continue

                                cam_key = (camera_id, description.key)
                                if cam_key in known_sensor_keys:
                                    continue
                                known_sensor_keys.add(cam_key)
                                entities.append(
                                    UnifiProtectBinarySensor(
                                        coordinator=coordinator,
                                        description=description,
                                        device_id=camera_id,
                                    )
                                )

                # Add sensor binary sensors
                sensors = protect.get("sensors", {})
                if isinstance(sensors, dict):
                    for sensor_id, sensor_data in sensors.items():
                        if not isinstance(sensor_data, dict):
                            continue
                        sensor_name = sensor_data.get("name", sensor_id)

                        _LOGGER.debug(
                            "Creating binary sensors for sensor %s (%s)",
                            sensor_id,
                            sensor_name,
                        )

                        for description in BINARY_SENSOR_TYPES:
                            if (
                                description.entity_type == "protect"
                                and description.device_type == DEVICE_TYPE_SENSOR
                                and (
                                    description.capability_fn is None
                                    or description.capability_fn(sensor_data)
                                )
                            ):
                                s_key = (sensor_id, description.key)
                                if s_key in known_sensor_keys:
                                    continue
                                known_sensor_keys.add(s_key)
                                entities.append(
                                    UnifiProtectBinarySensor(
                                        coordinator=coordinator,
                                        description=description,
                                        device_id=sensor_id,
                                    )
                                )

        if coordinator.protect_client and isinstance(coordinator.data, dict):
            protect = coordinator.data.get("protect", {})
            nvrs = protect.get("nvrs", {}) if isinstance(protect, dict) else {}
            if isinstance(nvrs, dict):
                for nvr_id, nvr_data in nvrs.items():
                    if not isinstance(nvr_data, dict):
                        continue
                    for description in BINARY_SENSOR_TYPES:
                        if (
                            description.device_type != DEVICE_TYPE_NVR
                            or description.capability_fn is None
                            or not description.capability_fn(nvr_data)
                        ):
                            continue
                        nvr_key = (nvr_id, description.key)
                        if nvr_key in known_sensor_keys:
                            continue
                        known_sensor_keys.add(nvr_key)
                        entities.append(
                            UnifiInsightsNVRStorageBinarySensor(
                                coordinator, description, nvr_id
                            )
                        )

        # Add Protect alarm hub, link station and fob binary sensors
        entities.extend(
            discover_protect_security_binary_sensors(coordinator, known_sensor_keys)
        )

        if entities:
            _LOGGER.info("Adding %d UniFi Insights binary sensors", len(entities))
            async_add_entities(entities)

    async_discover_binary_sensors()
    config_entry.async_on_unload(
        coordinator.async_add_listener(async_discover_binary_sensors)
    )

    if mobility_coordinator := config_entry.runtime_data.mobility_coordinator:
        async_setup_mobility_binary_sensors(
            hass, config_entry, mobility_coordinator, async_add_entities
        )


class UnifiInsightsBinarySensor(UnifiInsightsEntity, BinarySensorEntity):
    """Representation of a UniFi Insights Binary Sensor."""

    entity_description: UnifiInsightsBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        description: UnifiInsightsBinarySensorEntityDescription,
        site_id: str,
        device_id: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, description, site_id, device_id)

        _LOGGER.debug(
            "Initializing binary sensor %s for device %s in site %s",
            description.key,
            device_id,
            site_id,
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if (
            not self.coordinator.data["devices"]
            .get(self._site_id, {})
            .get(self._device_id)
        ):
            _LOGGER.debug(
                "No device data for binary sensor %s (device %s in site %s)",
                self.entity_description.key,
                self._device_id,
                self._site_id,
            )
            return None

        device = self.coordinator.data["devices"][self._site_id][self._device_id]
        if self.entity_description.value_fn is not None:
            return self.entity_description.value_fn(device)
        return None


class UnifiProtectBinarySensor(UnifiProtectEntity, BinarySensorEntity):
    """Representation of a UniFi Protect Binary Sensor."""

    entity_description: UnifiInsightsBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        description: UnifiInsightsBinarySensorEntityDescription,
        device_id: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(
            coordinator, description.device_type or "", device_id, description.key
        )
        self.entity_description = description

        _LOGGER.debug(
            "Initializing %s binary sensor %s for device %s",
            description.device_type,
            description.key,
            device_id,
        )

        # Update initial state
        self._update_from_data()

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        device_data = self.device_data
        if not device_data:
            return None

        if self.entity_description.value_fn is not None:
            return self.entity_description.value_fn(device_data)
        return None

    def _update_from_data(self) -> None:
        """Update entity from data."""
        device_data = self.device_data
        if not device_data:
            return

        # Get device name for attributes
        device_name = device_data.get(
            "name",
            (
                f"UniFi {self.entity_description.device_type.capitalize()} "  # type: ignore[union-attr]
                f"{self._device_id}"
            ),
        )

        if self.entity_description.key == "camera_microphone":
            self._attr_extra_state_attributes = {}
        elif self.entity_description.device_type == DEVICE_TYPE_CAMERA:
            self._attr_extra_state_attributes = {
                ATTR_CAMERA_ID: self._device_id,
                ATTR_CAMERA_NAME: device_name,
                ATTR_LAST_MOTION: device_data.get("lastMotion", 0),
            }
        elif self.entity_description.device_type == DEVICE_TYPE_SENSOR:
            self._attr_extra_state_attributes = {
                ATTR_SENSOR_ID: self._device_id,
                ATTR_SENSOR_NAME: device_name,
                ATTR_SENSOR_MOTION_DETECTED: device_data.get("isMotionDetected", False),
                ATTR_SENSOR_MOTION_DETECTED_AT: device_data.get("motionDetectedAt", 0),
                ATTR_SENSOR_IS_OPENED: device_data.get("isOpened", False),
                ATTR_SENSOR_OPEN_STATUS_CHANGED_AT: device_data.get(
                    "openStatusChangedAt", 0
                ),
                ATTR_SENSOR_TAMPER_DETECTED: device_data.get(
                    "isTamperingDetected", False
                ),
                ATTR_SENSOR_TAMPER_DETECTED_AT: device_data.get(
                    "tamperingDetectedAt", 0
                ),
                ATTR_SENSOR_LEAK_DETECTED: _is_internal_leak_detected(device_data),
                ATTR_SENSOR_LEAK_DETECTED_AT: device_data.get("leakDetectedAt", 0),
                ATTR_SENSOR_EXTERNAL_LEAK_DETECTED: _is_external_leak_detected(
                    device_data
                ),
                ATTR_SENSOR_EXTERNAL_LEAK_DETECTED_AT: device_data.get(
                    "externalLeakDetectedAt", 0
                ),
            }


class UnifiInsightsNVRStorageBinarySensor(UnifiProtectBinarySensor):
    """Conditional NVR problems; missing telemetry stays unavailable."""

    @property
    def available(self) -> bool:
        """NVRs have no required state field; gate on each explicit signal."""
        data = self.device_data
        return (
            self.coordinator.protect_available
            and data is not None
            and bool(data)
            and data.get("state") not in ("DISCONNECTED", "DISCONNECTING")
            and self.is_on is not None
        )


class UnifiPortBinarySensor(UnifiInsightsEntity, BinarySensorEntity):
    """Binary sensor indicating whether an SFP module is inserted."""

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        site_id: str,
        device_id: str,
        port_idx: int,
        port_label: str,
    ) -> None:
        """Initialize the SFP module binary sensor."""
        desc = UnifiInsightsBinarySensorEntityDescription(
            key=f"port_sfp_present_{port_idx}",
            translation_key="port_sfp_present",
            device_class=BinarySensorDeviceClass.CONNECTIVITY,
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_type="device",
        )
        super().__init__(coordinator, desc, site_id, device_id)
        self.entity_description = desc
        self._port_idx = port_idx
        label = port_label or f"Port {port_idx}"
        self._attr_translation_placeholders = {"port_label": label}
        self._attr_unique_id = f"{device_id}_port_sfp_present_{port_idx}"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    def _find_port_data(self) -> dict[str, Any] | None:
        """Find port data for this sensor's port index."""
        device_data = (
            self.coordinator.data.get("devices", {})
            .get(self._site_id, {})
            .get(self._device_id, {})
        )
        if not device_data:
            return None
        for port in device_data.get("ports", []):
            idx = port.get("idx") or port.get("port_idx")
            if idx == self._port_idx:
                return dict(port)
        return None

    @property
    def is_on(self) -> bool | None:
        """Return True if SFP module is inserted."""
        port = self._find_port_data()
        if port is None:
            return None
        return bool(port.get("sfp_found", False))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return SFP module details."""
        port = self._find_port_data()
        if not port or not port.get("sfp_found"):
            return None
        attrs: dict[str, Any] = {}
        for key, label in (
            ("sfp_part", "module"),
            ("sfp_vendor", "vendor"),
            ("sfp_serial", "serial"),
            ("sfp_compliance", "type"),
        ):
            val = port.get(key)
            if val:
                attrs[label] = val
        return attrs or None


class UnifiInsightsWanLinkBinarySensor(UnifiInsightsEntity, BinarySensorEntity):
    """Connectivity of one gateway WAN connection (DHCP, static or PPPoE)."""

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        site_id: str,
        device_id: str,
        wan_key: str,
        wan_name: str,
    ) -> None:
        """Initialize the WAN link binary sensor."""
        desc = UnifiInsightsBinarySensorEntityDescription(
            key=f"wan_link_{wan_key}",
            translation_key="wan_link",
            device_class=BinarySensorDeviceClass.CONNECTIVITY,
            entity_type="device",
        )
        super().__init__(coordinator, desc, site_id, device_id)
        self._wan_key = wan_key
        self._attr_translation_placeholders = {"wan_name": wan_name}

    def _find_wan(self) -> dict[str, Any] | None:
        """Return this sensor's WAN link from coordinator data."""
        device_data = (
            self.coordinator.data.get("devices", {})
            .get(self._site_id, {})
            .get(self._device_id, {})
        )
        wans = device_data.get("wans")
        if not isinstance(wans, list):
            return None
        for wan in wans:
            if isinstance(wan, dict) and wan.get("key") == self._wan_key:
                return wan
        return None

    @property
    def is_on(self) -> bool | None:
        """Return True when the WAN link is connected."""
        wan = self._find_wan()
        return None if wan is None else bool(wan.get("connected"))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return WAN link details."""
        wan = self._find_wan()
        if wan is None:
            return None
        return {key: wan.get(key) for key in ("status", "alive", "ip")}


class UnifiInsightsSiteToSiteVpnBinarySensor(UnifiInsightsEntity, BinarySensorEntity):
    """
    Connection state of one site-to-site VPN tunnel, shown on the gateway.

    State comes from the v2 ``vpn/connections`` list, which carries one entry
    per live VPN keyed by the tunnel's networkconf id. A tunnel that is not in
    the list, or is listed with a status other than CONNECTED, is off.
    """

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        site_id: str,
        device_id: str,
        tunnel_id: str,
        tunnel_name: str,
    ) -> None:
        """Initialize the site-to-site VPN tunnel binary sensor."""
        desc = UnifiInsightsBinarySensorEntityDescription(
            key=f"site_to_site_vpn_{tunnel_id}",
            translation_key="site_to_site_vpn",
            device_class=BinarySensorDeviceClass.CONNECTIVITY,
            entity_type="device",
        )
        super().__init__(coordinator, desc, site_id, device_id)
        self._tunnel_id = tunnel_id
        self._attr_translation_placeholders = {"tunnel_name": tunnel_name}

    def _tunnel(self) -> dict[str, Any] | None:
        """Return this tunnel's configuration, if it still exists."""
        site_vpns = self.coordinator.data.get("site_vpns")
        site = site_vpns.get(self._site_id) if isinstance(site_vpns, dict) else None
        tunnel = site.get(self._tunnel_id) if isinstance(site, dict) else None
        return tunnel if isinstance(tunnel, dict) else None

    def _connections(self) -> dict[str, Any] | None:
        """Return the site's live VPN connections, or None when unknown."""
        connections = self.coordinator.data.get("vpn_connections")
        site = connections.get(self._site_id) if isinstance(connections, dict) else None
        return site if isinstance(site, dict) else None

    @property
    def available(self) -> bool:
        """Return False once the tunnel is removed from the console."""
        return bool(super().available and self._tunnel() is not None)

    @property
    def is_on(self) -> bool | None:
        """Return True while the tunnel is connected."""
        connections = self._connections()
        if connections is None:
            return None
        connection = connections.get(self._tunnel_id)
        return (
            isinstance(connection, dict)
            and str(connection.get("status") or "").upper() == "CONNECTED"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the tunnel's type and raw connection status."""
        tunnel = self._tunnel() or {}
        connections = self._connections()
        connection = connections.get(self._tunnel_id) if connections else None
        return {
            "vpn_type": tunnel.get("vpn_type"),
            "status": connection.get("status")
            if isinstance(connection, dict)
            else None,
        }
