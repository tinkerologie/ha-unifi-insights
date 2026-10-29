# Copyright 2026 UniFi Insights contributors
"""Support for UniFi Protect, network rule change, and UniFi Access events."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components.event import (
    EventDeviceClass,
    EventEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .console_identity import async_migrate_site_entity, resolve_console_namespace
from .const import (
    DOMAIN,
    MANUFACTURER,
    SMART_DETECT_ANIMAL,
    SMART_DETECT_PACKAGE,
    SMART_DETECT_PERSON,
    SMART_DETECT_VEHICLE,
)
from .coordinators.access import access_doorbell_signal, access_result_signal
from .coordinators.rule_audit import RULE_CHANGE_TYPES
from .data_transforms import owner_available, protect_collection, protect_device
from .entity import (
    UnifiAccessDoorEntity,
    UnifiAccessEntity,
    UnifiSiteEntity,
    access_coordinator_for,
    access_unique_id,
    async_delegate_entity_id_changed,
    async_discover_access_entities,
    door_device_info,
    doorbell_device_info,
    get_field,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.config_entries import ConfigEntry
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

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep the entity on an entity ID change; nothing here is keyed on it."""
        async_delegate_entity_id_changed(super(), old_entity_id)

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

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep the entity on an entity ID change; nothing here is keyed on it."""
        async_delegate_entity_id_changed(super(), old_entity_id)

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
        entities: list[
            UnifiProtectDoorbellEventEntity
            | UnifiProtectSmartDetectEventEntity
            | UnifiProtectSensorEventEntity
        ] = []

        cameras = protect_collection(coordinator.data, "cameras") or {}
        for camera_id, camera_data in cameras.items():
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
        sensors = protect_collection(coordinator.data, "sensors") or {}
        for sensor_id in sensors:
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

        camera_data = protect_device(coordinator.data, "cameras", device_id) or {}
        camera_name = camera_data.get("name", f"Camera {device_id}")

        self._attr_unique_id = f"{DOMAIN}_camera_{device_id}_doorbell_event"

        # Set device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"protect_camera_{device_id}")},
            name=camera_name,
            manufacturer=MANUFACTURER,
            model=camera_data.get("type", "UniFi Camera"),
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not owner_available(self.coordinator.data, "protect"):
            return False
        camera_data = protect_device(self.coordinator.data, "cameras", self._device_id)
        if not camera_data:
            return False
        return bool(camera_data.get("state") == "CONNECTED")

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not owner_available(self.coordinator.data, "protect"):
            if self.hass is not None:
                self.async_write_ha_state()
            return
        camera_data = (
            protect_device(self.coordinator.data, "cameras", self._device_id) or {}
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

        camera_data = protect_device(coordinator.data, "cameras", device_id) or {}
        camera_name = camera_data.get("name", f"Camera {device_id}")

        self._attr_unique_id = f"{DOMAIN}_camera_{device_id}_smart_detect_event"

        # Set device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"protect_camera_{device_id}")},
            name=camera_name,
            manufacturer=MANUFACTURER,
            model=camera_data.get("type", "UniFi Camera"),
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not owner_available(self.coordinator.data, "protect"):
            return False
        camera_data = protect_device(self.coordinator.data, "cameras", self._device_id)
        if not camera_data:
            return False
        return bool(camera_data.get("state") == "CONNECTED")

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not owner_available(self.coordinator.data, "protect"):
            if self.hass is not None:
                self.async_write_ha_state()
            return
        camera_data = (
            protect_device(self.coordinator.data, "cameras", self._device_id) or {}
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

        sensor_data = protect_device(coordinator.data, "sensors", device_id) or {}
        sensor_name = sensor_data.get("name", f"Sensor {device_id}")

        self._attr_unique_id = f"{DOMAIN}_sensor_{device_id}_event"

        # Set device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"protect_sensor_{device_id}")},
            name=sensor_name,
            manufacturer=MANUFACTURER,
            model=sensor_data.get("type", "UniFi Sensor"),
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not owner_available(self.coordinator.data, "protect"):
            return False
        sensor_data = protect_device(self.coordinator.data, "sensors", self._device_id)
        if not sensor_data:
            return False
        return bool(sensor_data.get("state") == "CONNECTED")

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not owner_available(self.coordinator.data, "protect"):
            if self.hass is not None:
                self.async_write_ha_state()
            return
        sensor_data = (
            protect_device(self.coordinator.data, "sensors", self._device_id) or {}
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


# --- Network rule changes ----------------------------------------------------

# Event entity that reports UniFi network rule changes.

# Translation key and unique-id suffix. It is listed in SITE_ENTITY_SUFFIXES so a
# console identity change re-keys this entity with the other site entities.
RULE_CHANGE_EVENT_KEY = "network_rule_changes"

# The only keys of a change record that reach the event. Field values, addresses
# and domains are never part of a change record in the first place.
EVENT_ATTRIBUTE_KEYS: tuple[str, ...] = (
    "site_id",
    "rule_type",
    "rule_id",
    "rule_name",
    "enabled",
    "changed_fields",
)


def _event_data(change: Mapping[str, Any]) -> dict[str, Any]:
    """Coerce event data from a change record to documented types."""
    data: dict[str, Any] = {}
    for key in EVENT_ATTRIBUTE_KEYS:
        val = change.get(key)
        if key == "changed_fields":
            data[key] = (
                [field for field in val if isinstance(field, str)]
                if isinstance(val, list)
                else []
            )
        elif key == "enabled":
            data[key] = val if isinstance(val, bool) else None
        elif key == "rule_name":
            data[key] = val if isinstance(val, str) else None
        else:
            data[key] = val
    return data


class UnifiInsightsNetworkRuleChangeEvent(
    UnifiSiteEntity,
    EventEntity,
):
    """Event entity that fires once for each added, modified or deleted rule."""

    _attr_translation_key = RULE_CHANGE_EVENT_KEY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_event_types: ClassVar[list[str]] = list(RULE_CHANGE_TYPES)  # type: ignore[misc]

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        site_id: str,
        *,
        unique_id: str,
    ) -> None:
        """Initialize the rule change event entity."""
        super().__init__(coordinator, site_id, unique_id=unique_id)
        self._last_available = self.available
        # Changes detected before this entity existed are history, not news.
        self._last_sequence = max(
            (change["sequence"] for change in self._site_changes()), default=0
        )

    def _site_changes(self) -> list[dict[str, Any]]:
        """Return this site's well-formed change records, oldest first."""
        data: Any = self.coordinator.data
        by_site = data.get("rule_changes") if isinstance(data, dict) else None
        changes = by_site.get(self._site_id) if isinstance(by_site, dict) else None
        if not isinstance(changes, list):
            return []
        valid = [
            change
            for change in changes
            if isinstance(change, dict)
            and isinstance(change.get("sequence"), int)
            and change.get("change_type") in RULE_CHANGE_TYPES
        ]
        return sorted(valid, key=lambda change: change["sequence"])

    @property
    def available(self) -> bool:
        """Return True while the config coordinator is refreshing successfully."""
        return owner_available(self.coordinator.data, "config")

    @callback
    def _handle_coordinator_update(self) -> None:
        """Fire one event for every change not reported yet."""
        wrote_event = False
        for change in self._site_changes():
            if change["sequence"] <= self._last_sequence:
                continue
            self._last_sequence = change["sequence"]
            self._trigger_event(
                change["change_type"],
                _event_data(change),
            )
            self.async_write_ha_state()
            wrote_event = True
        available = self.available
        if not wrote_event and available != self._last_available:
            self.async_write_ha_state()
        self._last_available = available


def discover_network_rule_change_events(
    coordinator: UnifiInsightsFacadeCoordinator,
    known_site_ids: set[str],
    *,
    hass: HomeAssistant,
    entry: ConfigEntry,
    namespace: str | None,
) -> list[UnifiInsightsNetworkRuleChangeEvent]:
    """
    Return one rule change event entity for each site not seen before.

    The unique id is scoped by the console namespace, like every other logical
    site entity, because two consoles can report the same site id. A site whose
    identity cannot be claimed safely is skipped and retried on the next tick.
    """
    data: Any = coordinator.data
    sites = data.get("sites") if isinstance(data, dict) else None
    if not isinstance(sites, dict):
        return []
    entities: list[UnifiInsightsNetworkRuleChangeEvent] = []
    for site_id in sites:
        if not isinstance(site_id, str) or site_id in known_site_ids:
            continue
        unique_id = async_migrate_site_entity(
            hass, entry, "event", namespace, site_id, suffix=RULE_CHANGE_EVENT_KEY
        )
        if unique_id is None:
            continue
        known_site_ids.add(site_id)
        entities.append(
            UnifiInsightsNetworkRuleChangeEvent(
                coordinator, site_id, unique_id=unique_id
            )
        )
    return entities
