# Copyright 2026 UniFi Insights contributors
"""Door locks for UniFi Access doors that are not gate hubs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.lock import LockEntity

from .access_entity import (
    UnifiAccessDoorEntity,
    access_coordinator_for,
    access_unique_id,
    async_discover_access_entities,
    door_device_info,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import UnifiInsightsConfigEntry
    from .coordinators.access import UnifiInsightsAccessCoordinator

# Each command is a remote console call, so Home Assistant runs them one at a time.
PARALLEL_UPDATES = 1


class UnifiAccessDoorLock(UnifiAccessDoorEntity, LockEntity):
    """Lock state from the console's door relay. Commands never set state locally."""

    _attr_translation_key = "access_door_lock"

    def __init__(
        self, coordinator: UnifiInsightsAccessCoordinator, door_id: str
    ) -> None:
        """Create the lock for one non-gate door."""
        super().__init__(coordinator, door_id)
        self._attr_unique_id = access_unique_id(self._namespace, door_id, "lock")
        door = self.door or {}
        name = door.get("name")
        self._attr_device_info = door_device_info(
            self._namespace, door_id, name if isinstance(name, str) else None
        )

    def _is_present(self) -> bool:
        """Return True only for doors that are not gate hubs and devices are known."""
        return self._door_id in self.coordinator.lock_door_ids

    @property
    def is_locked(self) -> bool | None:
        """Return the relay state the console reports, or None when unknown."""
        door = self.door
        if door is None:
            return None
        relay = door["lock_relay"]
        if relay == "lock":
            return True
        if relay == "unlock":
            return False
        return None

    async def async_lock(self, **kwargs: object) -> None:  # noqa: ARG002
        """Send the documented reset rule; the relay state comes from the read-back."""
        await self._async_command(
            "Could not lock the Access door",
            self.coordinator.async_lock_door,
            self._door_id,
        )

    async def async_unlock(self, **kwargs: object) -> None:  # noqa: ARG002
        """Remotely unlock the door; the relay state comes from the read-back."""
        await self._async_command(
            "Could not unlock the Access door",
            self.coordinator.async_unlock_door,
            self._door_id,
        )


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a lock for every door that is not a gate hub."""
    coordinator = access_coordinator_for(entry)
    if coordinator is None:
        return

    async_discover_access_entities(
        entry,
        coordinator,
        async_add_entities,
        known_ids=set(),
        keys=lambda: sorted(coordinator.lock_door_ids),
        make=lambda door_id: UnifiAccessDoorLock(coordinator, door_id),
    )
