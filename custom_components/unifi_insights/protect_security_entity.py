# Copyright 2026 UniFi Insights contributors
"""
Entities for UniFi Protect security devices: alarm hubs, link stations, fobs.

Covers the Protect 7.3.70 additions: Thread mesh state on link stations and
alarm hubs (both use the ``linkStation`` schema), the alarm hub's device
tamper status, and a keypad fob's keypad and arm-control settings. The public
API cannot write any of these (``PATCH`` accepts ``name`` only), so every
entity is read-only.

The `unifi-official-api` models accept any value so a firmware addition never drops a
device; the spec's enums and ranges are enforced here instead, and anything
outside them reads as unknown (``None``) rather than as a misleading state.
"""

from __future__ import annotations

import logging
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
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_ARM_PROFILE_ID,
    ATTR_LAST_TAMPER_AT,
    ATTR_LAST_TAMPER_USER,
    ATTR_NIGHT_PROFILE_ID,
    ATTR_THREAD_CHANNEL,
    ATTR_THREAD_ERROR_REASON,
    ATTR_THREAD_EXTENDED_PAN_ID,
    ATTR_THREAD_NETWORK_NAME,
    ATTR_THREAD_PAN_ID,
    DEVICE_TYPE_ALARM_HUB,
    DEVICE_TYPE_FOB,
    DEVICE_TYPE_LINK_STATION,
)
from .entity import UnifiProtectEntity

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.helpers.typing import StateType

    from .coordinators import UnifiInsightsFacadeCoordinator

_LOGGER = logging.getLogger(__name__)

# Spec enum for linkStationThreadNetwork.role.
THREAD_ROLES: list[str] = ["disabled", "detached", "child", "router", "leader"]
ARM_CONTROL_STATES: list[str] = ["enabled", "disabled"]
# fobKeypadSettings.beepVolume range per spec, in percent.
_MAX_BEEP_VOLUME = 100
_THREAD_DEVICE_TYPES = (DEVICE_TYPE_LINK_STATION, DEVICE_TYPE_ALARM_HUB)


def _nested(data: dict[str, Any], *keys: str) -> dict[str, Any] | None:
    """
    Follow ``keys`` through nested dicts, or return None.

    Coordinator dicts come from ``model_dump(exclude_none=False)``, so an
    absent object is an explicit ``None`` and ``data.get(key, {})`` would not
    fall back.
    """
    value: Any = data
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value if isinstance(value, dict) else None


def _thread_network(data: dict[str, Any]) -> dict[str, Any] | None:
    """Return ``threadState.network``; None on SKUs without Thread."""
    return _nested(data, "threadState", "network")


def _has_thread_network(data: dict[str, Any]) -> bool:
    """Return True if the gateway reports a Thread network."""
    return _thread_network(data) is not None


def _has_keypad(data: dict[str, Any]) -> bool:
    """Return True for a fob with a PIN keypad."""
    flags = data.get("featureFlags")
    return isinstance(flags, dict) and flags.get("hasKeypad") is True


def _has_arm_control(data: dict[str, Any]) -> bool:
    """Return True if the fob reports arm-control settings at all."""
    return _nested(data, "armControlSettings") is not None


def _always(_data: dict[str, Any]) -> bool:
    return True


def _tamper_state(data: dict[str, Any]) -> bool | None:
    """
    Map ``alarmHub.deviceTamperStatus`` to on/off.

    The field is optional. Missing or unrecognized reads as unknown: falling
    through to off would report "not tampered", a false all-clear.
    """
    status = (_nested(data, "alarmHub") or {}).get("deviceTamperStatus")
    if status == "tampered":
        return True
    if status == "restored":
        return False
    return None


def _thread_problem(data: dict[str, Any]) -> bool | None:
    """Map the Thread network status to a PROBLEM state (error is on)."""
    status = (_thread_network(data) or {}).get("status")
    if status == "error":
        return True
    if status == "ready":
        return False
    return None


def _keypad_beep(data: dict[str, Any]) -> bool | None:
    """Return whether a keypress beeps."""
    value = (_nested(data, "keypadSettings") or {}).get("beepEnabled")
    return value if isinstance(value, bool) else None


def _keypad_volume(data: dict[str, Any]) -> int | None:
    """Return the keypress beep volume, 0-100 % per spec."""
    value = (_nested(data, "keypadSettings") or {}).get("beepVolume")
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
    role = (_thread_network(data) or {}).get("role")
    if isinstance(role, str) and role in THREAD_ROLES:
        return role
    if role is not None:
        _LOGGER.debug("Unrecognized Thread role %r; reporting unknown", role)
    return None


def _thread_joined_devices(data: dict[str, Any]) -> int | None:
    """Return how many devices have joined the Thread network."""
    value = (_thread_network(data) or {}).get("joinedDeviceCount")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _arm_control(data: dict[str, Any]) -> str | None:
    """Return whether the fob may arm and disarm external arm profiles."""
    enabled = (_nested(data, "armControlSettings") or {}).get("enabled")
    if enabled is True:
        return "enabled"
    if enabled is False:
        return "disabled"
    return None


def _present(**attributes: Any) -> dict[str, Any]:
    """Drop attributes that are None rather than reporting null values."""
    return {key: value for key, value in attributes.items() if value is not None}


def _tamper_attributes(data: dict[str, Any]) -> dict[str, Any]:
    """Return the latest tamper event's user and time (ISO 8601, UTC)."""
    user = data.get("_lastTamperUser")
    at = data.get("_lastTamperAt")
    when = None
    if isinstance(at, (int, float)) and not isinstance(at, bool):
        when = dt_util.utc_from_timestamp(at / 1000).isoformat()
    return _present(
        **{
            ATTR_LAST_TAMPER_USER: user if isinstance(user, str) else None,
            ATTR_LAST_TAMPER_AT: when,
        }
    )


def _thread_problem_attributes(data: dict[str, Any]) -> dict[str, Any]:
    network = _thread_network(data) or {}
    return _present(**{ATTR_THREAD_ERROR_REASON: network.get("errorReason")})


def _thread_network_attributes(data: dict[str, Any]) -> dict[str, Any]:
    # lastUpdatedAt is left out on purpose: it changes on every gateway
    # refresh, so each refresh would write a new recorder row.
    network = _thread_network(data) or {}
    return _present(
        **{
            ATTR_THREAD_CHANNEL: network.get("channel"),
            ATTR_THREAD_NETWORK_NAME: network.get("networkName"),
            ATTR_THREAD_PAN_ID: network.get("panId"),
            ATTR_THREAD_EXTENDED_PAN_ID: network.get("extendedPanId"),
        }
    )


def _arm_control_attributes(data: dict[str, Any]) -> dict[str, Any]:
    settings = _nested(data, "armControlSettings") or {}
    return _present(
        **{
            ATTR_ARM_PROFILE_ID: settings.get("armProfileId"),
            ATTR_NIGHT_PROFILE_ID: settings.get("nightProfileId"),
        }
    )


@dataclass(frozen=True, kw_only=True)
class UnifiProtectSecurityBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a binary sensor of a Protect security device."""

    device_types: tuple[str, ...]
    value_fn: Callable[[dict[str, Any]], bool | None]
    supported_fn: Callable[[dict[str, Any]], bool] = _always
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class UnifiProtectSecuritySensorEntityDescription(SensorEntityDescription):
    """Describes a sensor of a Protect security device."""

    device_types: tuple[str, ...]
    value_fn: Callable[[dict[str, Any]], StateType]
    supported_fn: Callable[[dict[str, Any]], bool] = _always
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


SECURITY_BINARY_SENSOR_TYPES: tuple[
    UnifiProtectSecurityBinarySensorEntityDescription, ...
] = (
    UnifiProtectSecurityBinarySensorEntityDescription(
        key="alarm_hub_device_tamper",
        translation_key="alarm_hub_device_tamper",
        device_class=BinarySensorDeviceClass.TAMPER,
        device_types=(DEVICE_TYPE_ALARM_HUB,),
        value_fn=_tamper_state,
        attributes_fn=_tamper_attributes,
    ),
    UnifiProtectSecurityBinarySensorEntityDescription(
        key="thread_network_problem",
        translation_key="thread_network_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=_THREAD_DEVICE_TYPES,
        value_fn=_thread_problem,
        supported_fn=_has_thread_network,
        attributes_fn=_thread_problem_attributes,
    ),
    UnifiProtectSecurityBinarySensorEntityDescription(
        key="fob_keypad_beep",
        translation_key="fob_keypad_beep",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        device_types=(DEVICE_TYPE_FOB,),
        value_fn=_keypad_beep,
        supported_fn=_has_keypad,
    ),
)

SECURITY_SENSOR_TYPES: tuple[UnifiProtectSecuritySensorEntityDescription, ...] = (
    UnifiProtectSecuritySensorEntityDescription(
        key="thread_role",
        translation_key="thread_role",
        device_class=SensorDeviceClass.ENUM,
        options=THREAD_ROLES,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=_THREAD_DEVICE_TYPES,
        value_fn=_thread_role,
        supported_fn=_has_thread_network,
        attributes_fn=_thread_network_attributes,
    ),
    UnifiProtectSecuritySensorEntityDescription(
        key="thread_joined_devices",
        translation_key="thread_joined_devices",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=_THREAD_DEVICE_TYPES,
        value_fn=_thread_joined_devices,
        supported_fn=_has_thread_network,
    ),
    UnifiProtectSecuritySensorEntityDescription(
        key="fob_keypad_beep_volume",
        translation_key="fob_keypad_beep_volume",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        device_types=(DEVICE_TYPE_FOB,),
        value_fn=_keypad_volume,
        supported_fn=_has_keypad,
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


class UnifiProtectSecurityBinarySensor(UnifiProtectEntity, BinarySensorEntity):
    """Binary sensor of an alarm hub, link station or fob."""

    entity_description: UnifiProtectSecurityBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        description: UnifiProtectSecurityBinarySensorEntityDescription,
        device_type: str,
        device_id: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, device_type, device_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the state, or None when it is unknown."""
        data = self.device_data
        return self.entity_description.value_fn(data) if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return attributes that have a value."""
        data = self.device_data
        if not data or self.entity_description.attributes_fn is None:
            return {}
        return self.entity_description.attributes_fn(data)


class UnifiProtectSecuritySensor(UnifiProtectEntity, SensorEntity):
    """Sensor of an alarm hub, link station or fob."""

    entity_description: UnifiProtectSecuritySensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        description: UnifiProtectSecuritySensorEntityDescription,
        device_type: str,
        device_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_type, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the state, or None when it is unknown."""
        data = self.device_data
        return self.entity_description.value_fn(data) if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return attributes that have a value."""
        data = self.device_data
        if not data or self.entity_description.attributes_fn is None:
            return {}
        return self.entity_description.attributes_fn(data)


def _security_devices(
    coordinator: UnifiInsightsFacadeCoordinator,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Return (device_type, device_id, data) for every security device."""
    if not coordinator.protect_client:
        return []
    protect = coordinator.data.get("protect")
    if not isinstance(protect, dict):
        return []
    devices: list[tuple[str, str, dict[str, Any]]] = []
    for device_type in (
        DEVICE_TYPE_ALARM_HUB,
        DEVICE_TYPE_LINK_STATION,
        DEVICE_TYPE_FOB,
    ):
        collection = protect.get(f"{device_type}s")
        if not isinstance(collection, dict):
            continue
        devices.extend(
            (device_type, device_id, data)
            for device_id, data in collection.items()
            if isinstance(data, dict)
        )
    return devices


def discover_protect_security_binary_sensors(
    coordinator: UnifiInsightsFacadeCoordinator,
    known_keys: set[tuple[Any, ...]],
) -> list[BinarySensorEntity]:
    """
    Return binary sensors for security devices not yet in ``known_keys``.

    Called on every coordinator update, so an entity whose capability shows up
    later (a Thread network forming, a fob newly adopted) is added then.
    """
    entities: list[BinarySensorEntity] = []
    for device_type, device_id, data in _security_devices(coordinator):
        for description in SECURITY_BINARY_SENSOR_TYPES:
            if device_type not in description.device_types:
                continue
            if not description.supported_fn(data):
                continue
            key = (device_type, device_id, description.key)
            if key in known_keys:
                continue
            known_keys.add(key)
            entities.append(
                UnifiProtectSecurityBinarySensor(
                    coordinator, description, device_type, device_id
                )
            )
    return entities


def discover_protect_security_sensors(
    coordinator: UnifiInsightsFacadeCoordinator,
    known_keys: set[tuple[Any, ...]],
) -> list[SensorEntity]:
    """Return sensors for security devices not yet in ``known_keys``."""
    entities: list[SensorEntity] = []
    for device_type, device_id, data in _security_devices(coordinator):
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
                UnifiProtectSecuritySensor(
                    coordinator, description, device_type, device_id
                )
            )
    return entities
