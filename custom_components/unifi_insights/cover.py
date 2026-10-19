# Copyright 2026 UniFi Insights contributors
"""Gate covers for UniFi Access UA Hub Gate motors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)

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

# Motor commands take a moment and must not overlap for one gate.
PARALLEL_UPDATES = 1

_GATE_FEATURES = (
    CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
)


class UnifiAccessGateCover(UnifiAccessDoorEntity, CoverEntity):
    """
    A UA Hub Gate motor driven through the door it is linked to.

    Open, close and stop are sent as the gate control command in three-button
    mode (UniFi Access 4.2.16+). The query parameter and device type come from
    two community SDKs (hjdhjd/unifi-access and imhotep/py-unifi-access). Neither
    is in the vendor's published reference, and this integration has not
    verified the behaviour on gate hardware. The console's answer to a command is
    the only capability check. Nothing here sets the position locally: the state
    comes from the door position sensor, and opening or closing is never inferred.
    """

    _attr_device_class = CoverDeviceClass.GATE
    _attr_supported_features = _GATE_FEATURES
    _attr_translation_key = "access_gate"

    def __init__(
        self, coordinator: UnifiInsightsAccessCoordinator, door_id: str
    ) -> None:
        """Create the cover for one door a UA Hub Gate is linked to."""
        super().__init__(coordinator, door_id)
        self._attr_unique_id = access_unique_id(self._namespace, door_id, "gate")
        door = self.door or {}
        name = door.get("name")
        self._attr_device_info = door_device_info(
            self._namespace, door_id, name if isinstance(name, str) else None
        )

    def _is_present(self) -> bool:
        """Return True only while the door is linked to a gate hub."""
        return self._door_id in self.coordinator.gate_door_ids

    @property
    def is_closed(self) -> bool | None:
        """Return the door position, or None without a position sensor."""
        door = self.door
        if door is None:
            return None
        position = door["position"]
        if position == "close":
            return True
        if position == "open":
            return False
        return None

    async def async_open_cover(self, **kwargs: object) -> None:  # noqa: ARG002
        """Send the open command to the gate motor."""
        await self._async_command(
            "Could not open the Access gate",
            self.coordinator.async_gate_command,
            self._door_id,
            "open",
        )

    async def async_close_cover(self, **kwargs: object) -> None:  # noqa: ARG002
        """Send the close command to the gate motor."""
        await self._async_command(
            "Could not close the Access gate",
            self.coordinator.async_gate_command,
            self._door_id,
            "close",
        )

    async def async_stop_cover(self, **kwargs: object) -> None:  # noqa: ARG002
        """Halt the gate mid-transition."""
        await self._async_command(
            "Could not stop the Access gate",
            self.coordinator.async_gate_command,
            self._door_id,
            "stop",
        )


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a cover for every door a UA Hub Gate is linked to."""
    coordinator = access_coordinator_for(entry)
    if coordinator is None:
        return

    async_discover_access_entities(
        entry,
        coordinator,
        async_add_entities,
        known_ids=set(),
        keys=lambda: sorted(coordinator.gate_door_ids),
        make=lambda door_id: UnifiAccessGateCover(coordinator, door_id),
    )
