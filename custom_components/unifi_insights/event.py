# Copyright 2026 UniFi Insights contributors
"""Support for UniFi Protect, network rule change, and UniFi Access events."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components.event import (
    EventDeviceClass,
    EventEntity,
)
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .access_entity import (
    UnifiAccessDoorEntity,
    UnifiAccessEntity,
    access_coordinator_for,
    access_unique_id,
    async_discover_access_entities,
    door_device_info,
    doorbell_device_info,
)
from .console_identity import resolve_console_namespace
from .const import (
    DOMAIN,
    MANUFACTURER,
    SMART_DETECT_ANIMAL,
    SMART_DETECT_PACKAGE,
    SMART_DETECT_PERSON,
    SMART_DETECT_VEHICLE,
)
from .coordinators.access import access_doorbell_signal, access_result_signal
from .entity import get_field
from .network_rule_audit_event import discover_network_rule_change_events

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import UnifiInsightsConfigEntry
    from .coordinators import UnifiInsightsFacadeCoordinator
    from .coordinators.access import UnifiInsightsAccessCoordinator

_LOGGER = logging.getLogger(__name__)

# Coordinator handles updates
PARALLEL_UPDATES = 0

# Event types
EVENT_TYPE_DOORBELL_RING = "ring"
EVENT_TYPE_MOTION = "motion"
EVENT_TYPE_SMART_DETECT_PERSON = "person_detected"
EVENT_TYPE_SMART_DETECT_VEHICLE = "vehicle_detected"
EVENT_TYPE_SMART_DETECT_ANIMAL = "animal_detected"
EVENT_TYPE_SMART_DETECT_PACKAGE = "package_detected"
EVENT_TYPE_SENSOR_OPEN = "opened"
EVENT_TYPE_SENSOR_CLOSE = "closed"


EVENT_TYPE_ACCESS_GRANTED = "access_granted"
EVENT_TYPE_ACCESS_DENIED = "access_denied"
_ACCESS_RESULT_EVENT_TYPES = {
    "granted": EVENT_TYPE_ACCESS_GRANTED,
    "denied": EVENT_TYPE_ACCESS_DENIED,
}


class UnifiAccessDoorbellEvent(UnifiAccessEntity, EventEntity):
    """
    A doorbell on UniFi Access hardware, one ``ring`` per live remote-view frame.

    Rings are never stored. A ring that arrives before the entity is added is
    held once and fired on add, so a reload does not repeat an old ring.
    """

    _feature = "notifications"
    _attr_translation_key = "access_doorbell"
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types: ClassVar[list[str]] = [EVENT_TYPE_DOORBELL_RING]  # type: ignore[misc]

    def __init__(
        self,
        coordinator: UnifiInsightsAccessCoordinator,
        device_id: str,
        device_name: str | None,
        *,
        pending_ring: bool = False,
    ) -> None:
        """Create the doorbell entity for one Access device."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._pending_ring: bool = pending_ring
        self._added: bool = False
        self._attr_unique_id = access_unique_id(self._namespace, device_id, "doorbell")
        self._attr_device_info = doorbell_device_info(
            self._namespace, device_id, device_name
        )

    async def async_added_to_hass(self) -> None:
        """Fire the ring that created this entity, if any, once it is added."""
        await super().async_added_to_hass()
        self._added = True
        if self._pending_ring:
            self._pending_ring = False
            self._trigger_event(EVENT_TYPE_DOORBELL_RING)
            self.async_write_ha_state()

    @callback
    def async_ring(self) -> None:
        """Fire one ring. Before the entity is added, hold it for later."""
        if not self._added:
            self._pending_ring = True
            return
        self._trigger_event(EVENT_TYPE_DOORBELL_RING)
        self.async_write_ha_state()


class UnifiAccessResultEvent(UnifiAccessDoorEntity, EventEntity):
    """Verified access-granted and access-denied results for one door."""

    _attr_translation_key = "access_result"
    _attr_event_types: ClassVar[list[str]] = [  # type: ignore[misc]
        EVENT_TYPE_ACCESS_GRANTED,
        EVENT_TYPE_ACCESS_DENIED,
    ]

    def __init__(
        self, coordinator: UnifiInsightsAccessCoordinator, door_id: str
    ) -> None:
        """Create the result entity for one door."""
        super().__init__(coordinator, door_id)
        self._added: bool = False
        self._attr_unique_id = access_unique_id(
            self._namespace, door_id, "access_result"
        )
        door = self.door or {}
        name = door.get("name")
        self._attr_device_info = door_device_info(
            self._namespace, door_id, name if isinstance(name, str) else None
        )

    async def async_added_to_hass(self) -> None:
        """Mark the entity as added so results can be written."""
        await super().async_added_to_hass()
        self._added = True

    def _is_present(self) -> bool:
        """Return True only while the verified webhook is active."""
        return self.coordinator.webhook_active and self.door is not None

    @callback
    def async_result(self, result: str, authentication: str | None) -> None:
        """Fire the event type for a verified result. Attributes hold no secrets."""
        event_type = _ACCESS_RESULT_EVENT_TYPES[result]
        if not self._added:
            return
        self._trigger_event(
            event_type,
            {"door_id": self._door_id, "authentication": authentication},
        )
        self.async_write_ha_state()


@callback
def async_setup_access_events(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Wire doorbell rings and access results to their entities."""
    coordinator = access_coordinator_for(entry)
    if coordinator is None or coordinator.namespace is None:
        return

    doorbells: dict[str, UnifiAccessDoorbellEvent] = {}
    doorbell_ids: set[str] = set()

    def _make_doorbell(device_id: str) -> UnifiAccessDoorbellEvent:
        """Create a doorbell entity for a device of a known doorbell type."""
        device = coordinator.data["devices"].get(device_id, {})
        name = device.get("name")
        entity = UnifiAccessDoorbellEvent(
            coordinator, device_id, name if isinstance(name, str) else None
        )
        doorbells[device_id] = entity
        return entity

    async_discover_access_entities(
        entry,
        coordinator,
        async_add_entities,
        known_ids=doorbell_ids,
        keys=lambda: sorted(coordinator.doorbell_device_ids),
        make=_make_doorbell,
    )

    @callback
    def _on_ring(payload: dict[str, Any]) -> None:
        """Ring a known doorbell, or create the entity that is ringing now."""
        device_id = payload["device_id"]
        existing = doorbells.get(device_id)
        if existing is not None:
            existing.async_ring()
            return
        doorbell_ids.add(device_id)
        entity = UnifiAccessDoorbellEvent(
            coordinator,
            device_id,
            payload.get("device_name"),
            pending_ring=True,
        )
        doorbells[device_id] = entity
        async_add_entities([entity])

    entry.async_on_unload(
        async_dispatcher_connect(hass, access_doorbell_signal(entry.entry_id), _on_ring)
    )

    result_doors: dict[str, UnifiAccessResultEvent] = {}
    result_ids: set[str] = set()

    def _make_result(door_id: str) -> UnifiAccessResultEvent:
        """Create the result entity for one door."""
        entity = UnifiAccessResultEvent(coordinator, door_id)
        result_doors[door_id] = entity
        return entity

    async_discover_access_entities(
        entry,
        coordinator,
        async_add_entities,
        known_ids=result_ids,
        keys=lambda: (
            sorted(coordinator.data["doors"]) if coordinator.webhook_active else ()
        ),
        make=_make_result,
    )

    @callback
    def _on_result(payload: dict[str, Any]) -> None:
        """Route one verified result to its door's entity."""
        entity = result_doors.get(payload["door_id"])
        result = payload["result"]
        if entity is None or result not in _ACCESS_RESULT_EVENT_TYPES:
            return
        entity.async_result(result, payload.get("authentication"))

    entry.async_on_unload(
        async_dispatcher_connect(hass, access_result_signal(entry.entry_id), _on_result)
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up event entities for UniFi Insights integration."""
    # Access events are set up before the Protect-dependent events below.
    async_setup_access_events(hass, entry, async_add_entities)
    coordinator = entry.runtime_data.coordinator

    known_event_keys: set[tuple[str, str]] = set()
    known_rule_audit_sites: set[str] = set()
    first_setup = True

    @callback
    def async_discover_events() -> None:
        """Discover and add new event entities."""
        nonlocal first_setup
        # Rule change events belong to the Network application, so they are
        # discovered whether or not this console runs Protect.
        rule_events = discover_network_rule_change_events(
            coordinator,
            known_rule_audit_sites,
            hass=hass,
            entry=entry,
            namespace=resolve_console_namespace(entry),
        )
        if rule_events:
            async_add_entities(rule_events)
        if not coordinator.protect_client:
            return
        if (
            not coordinator.data
            or not isinstance(coordinator.data, dict)
            or "protect" not in coordinator.data
            or not isinstance(coordinator.data["protect"], dict)
        ):
            return

        protect = coordinator.data["protect"]
        entities: list[
            UnifiProtectDoorbellEventEntity
            | UnifiProtectSmartDetectEventEntity
            | UnifiProtectSensorEventEntity
        ] = []

        # Get cameras data
        cameras = protect.get("cameras", {})
        if isinstance(cameras, dict):
            for camera_id, camera_data in cameras.items():
                if not isinstance(camera_data, dict):
                    continue

                # Add doorbell event entities for cameras with doorbell feature
                if _is_doorbell_camera(camera_data):
                    doorbell_key = (camera_id, "doorbell")
                    if doorbell_key not in known_event_keys:
                        known_event_keys.add(doorbell_key)
                        entities.append(
                            UnifiProtectDoorbellEventEntity(
                                coordinator=coordinator,
                                device_id=camera_id,
                            )
                        )

                # Add smart detection event entity for all cameras
                smart_detect_types = camera_data.get("smartDetectTypes", [])
                if smart_detect_types:
                    smart_key = (camera_id, "smart_detect")
                    if smart_key not in known_event_keys:
                        known_event_keys.add(smart_key)
                        entities.append(
                            UnifiProtectSmartDetectEventEntity(
                                coordinator=coordinator,
                                device_id=camera_id,
                            )
                        )

        # Add sensor open/close event entities
        sensors = protect.get("sensors", {})
        if isinstance(sensors, dict):
            for sensor_id, sensor_data in sensors.items():
                if not isinstance(sensor_data, dict):
                    continue
                sensor_key = (sensor_id, "sensor")
                if sensor_key not in known_event_keys:
                    known_event_keys.add(sensor_key)
                    entities.append(
                        UnifiProtectSensorEventEntity(
                            coordinator=coordinator,
                            device_id=sensor_id,
                        )
                    )

        if entities or first_setup:
            _LOGGER.info("Adding %d UniFi Protect event entities", len(entities))
            async_add_entities(entities)
        first_setup = False

    async_discover_events()
    entry.async_on_unload(coordinator.async_add_listener(async_discover_events))


def _is_doorbell_camera(camera_data: dict[str, Any]) -> bool:
    """Check if a camera is a doorbell camera."""
    # Check camera type metadata
    camera_type = camera_data.get("_camera_type") or ""
    if "doorbell" in camera_type.lower():
        return True

    # Check API type field
    api_camera_type = (camera_data.get("type") or "").lower()
    if "doorbell" in api_camera_type:
        return True

    # Check name for doorbell indicators
    camera_name = (camera_data.get("name") or "").lower()
    return "doorbell" in camera_name


class UnifiProtectDoorbellEventEntity(
    CoordinatorEntity["UnifiInsightsFacadeCoordinator"],
    EventEntity,
):
    """Event entity for UniFi Protect doorbell ring events."""

    _attr_has_entity_name = True
    _attr_translation_key = "doorbell_ring"
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types: ClassVar[list[str]] = [EVENT_TYPE_DOORBELL_RING]  # type: ignore[misc]

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._last_ring_start: int | None = None

        camera_data = coordinator.data["protect"]["cameras"].get(device_id, {})
        camera_name = camera_data.get("name", f"Camera {device_id}")

        self._attr_unique_id = f"{DOMAIN}_camera_{device_id}_doorbell_event"

        # Set device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"protect_camera_{device_id}")},
            "name": camera_name,
            "manufacturer": MANUFACTURER,
            "model": camera_data.get("type", "UniFi Camera"),
        }

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.coordinator.protect_available:
            return False
        camera_data = self.coordinator.data["protect"]["cameras"].get(self._device_id)
        if not camera_data:
            return False
        return bool(camera_data.get("state") == "CONNECTED")

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator.protect_available:
            if self.hass is not None:
                self.async_write_ha_state()
            return
        camera_data = self.coordinator.data["protect"]["cameras"].get(
            self._device_id, {}
        )

        # Check for new ring event
        ring_start = camera_data.get("lastRingStart")
        ring_end = camera_data.get("lastRingEnd")

        # Fire event if new ring detected (ring_start changed and ring_end is None)
        if (
            ring_start is not None
            and ring_start != self._last_ring_start
            and ring_end is None
        ):
            self._last_ring_start = ring_start
            self._trigger_event(
                EVENT_TYPE_DOORBELL_RING,
                {"camera_id": self._device_id},
            )

        self.async_write_ha_state()


class UnifiProtectSmartDetectEventEntity(
    CoordinatorEntity["UnifiInsightsFacadeCoordinator"],
    EventEntity,
):
    """Event entity for UniFi Protect smart detection events."""

    _attr_has_entity_name = True
    _attr_translation_key = "smart_detection"
    _attr_device_class = EventDeviceClass.MOTION
    _attr_event_types: ClassVar[list[str]] = [  # type: ignore[misc]
        EVENT_TYPE_MOTION,
        EVENT_TYPE_SMART_DETECT_PERSON,
        EVENT_TYPE_SMART_DETECT_VEHICLE,
        EVENT_TYPE_SMART_DETECT_ANIMAL,
        EVENT_TYPE_SMART_DETECT_PACKAGE,
    ]

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._last_motion_start: int | None = None
        self._last_smart_detect_types: list[str] = []

        camera_data = coordinator.data["protect"]["cameras"].get(device_id, {})
        camera_name = camera_data.get("name", f"Camera {device_id}")

        self._attr_unique_id = f"{DOMAIN}_camera_{device_id}_smart_detect_event"

        # Set device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"protect_camera_{device_id}")},
            "name": camera_name,
            "manufacturer": MANUFACTURER,
            "model": camera_data.get("type", "UniFi Camera"),
        }

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.coordinator.protect_available:
            return False
        camera_data = self.coordinator.data["protect"]["cameras"].get(self._device_id)
        if not camera_data:
            return False
        return bool(camera_data.get("state") == "CONNECTED")

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator.protect_available:
            if self.hass is not None:
                self.async_write_ha_state()
            return
        camera_data = self.coordinator.data["protect"]["cameras"].get(
            self._device_id, {}
        )

        # Check for new motion/smart detect event
        motion_start = camera_data.get("lastMotionStart")
        motion_end = camera_data.get("lastMotionEnd")
        smart_detect_types = camera_data.get("lastSmartDetectTypes", [])

        # Fire event if new motion detected
        if (
            motion_start is not None
            and motion_start != self._last_motion_start
            and motion_end is None
        ):
            self._last_motion_start = motion_start
            self._last_smart_detect_types = smart_detect_types

            # Determine event type based on smart detect types
            event_data: dict[str, Any] = {"camera_id": self._device_id}

            if SMART_DETECT_PERSON in smart_detect_types:
                self._trigger_event(EVENT_TYPE_SMART_DETECT_PERSON, event_data)
            elif SMART_DETECT_VEHICLE in smart_detect_types:
                self._trigger_event(EVENT_TYPE_SMART_DETECT_VEHICLE, event_data)
            elif SMART_DETECT_ANIMAL in smart_detect_types:
                self._trigger_event(EVENT_TYPE_SMART_DETECT_ANIMAL, event_data)
            elif SMART_DETECT_PACKAGE in smart_detect_types:
                self._trigger_event(EVENT_TYPE_SMART_DETECT_PACKAGE, event_data)
            else:
                # Generic motion
                self._trigger_event(EVENT_TYPE_MOTION, event_data)

        self.async_write_ha_state()


class UnifiProtectSensorEventEntity(
    CoordinatorEntity["UnifiInsightsFacadeCoordinator"],
    EventEntity,
):
    """Event entity for UniFi Protect sensor open/close events."""

    _attr_has_entity_name = True
    _attr_translation_key = "sensor_event"
    _attr_event_types: ClassVar[list[str]] = [  # type: ignore[misc]
        EVENT_TYPE_SENSOR_OPEN,
        EVENT_TYPE_SENSOR_CLOSE,
    ]

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._last_open_status_changed_at: int | None = None
        self._last_is_opened: bool | None = None

        sensor_data = coordinator.data["protect"]["sensors"].get(device_id, {})
        sensor_name = sensor_data.get("name", f"Sensor {device_id}")

        self._attr_unique_id = f"{DOMAIN}_sensor_{device_id}_event"

        # Set device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"protect_sensor_{device_id}")},
            "name": sensor_name,
            "manufacturer": MANUFACTURER,
            "model": sensor_data.get("type", "UniFi Sensor"),
        }

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.coordinator.protect_available:
            return False
        sensor_data = self.coordinator.data["protect"]["sensors"].get(self._device_id)
        if not sensor_data:
            return False
        return bool(sensor_data.get("state") == "CONNECTED")

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator.protect_available:
            if self.hass is not None:
                self.async_write_ha_state()
            return
        sensor_data = self.coordinator.data["protect"]["sensors"].get(
            self._device_id, {}
        )

        # Check for open/close status change
        open_status_changed_at = get_field(
            sensor_data, "openStatusChangedAt", "open_status_changed_at"
        )
        is_opened = get_field(
            sensor_data, "isOpened", "is_opened", "opened", default=False
        )

        # Fire event if status changed
        if (
            open_status_changed_at is not None
            and open_status_changed_at != self._last_open_status_changed_at
        ):
            self._last_open_status_changed_at = open_status_changed_at
            self._last_is_opened = is_opened

            if is_opened:
                event_type = EVENT_TYPE_SENSOR_OPEN
            else:
                event_type = EVENT_TYPE_SENSOR_CLOSE

            self._trigger_event(
                event_type,
                {"sensor_id": self._device_id},
            )

        self.async_write_ha_state()
