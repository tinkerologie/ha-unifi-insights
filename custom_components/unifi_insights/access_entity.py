# Copyright 2026 UniFi Insights contributors
"""Shared base entity, identity and discovery for UniFi Access entities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinators.access import UnifiInsightsAccessCoordinator
from .coordinators.base import CommandFailure, async_execute_command

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinators.access import Feature


def access_unique_id(namespace: str, *parts: str) -> str:
    """Return an entity unique id scoped to one console."""
    return "_".join(("unifi_insights_access", namespace, *parts))


def door_device_info(namespace: str, door_id: str, door_name: str | None) -> DeviceInfo:
    """Return the device a door's entities belong to."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"access_door_{namespace}_{door_id}")},
        name=door_name or f"Access door {door_id}",
        manufacturer=MANUFACTURER,
        model="UniFi Access door",
    )


def console_device_info(namespace: str) -> DeviceInfo:
    """Return the console-level Access device."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"access_console_{namespace}")},
        name="UniFi Access",
        manufacturer=MANUFACTURER,
        model="UniFi Access",
    )


def doorbell_device_info(
    namespace: str, device_id: str, device_name: str | None
) -> DeviceInfo:
    """Return the Access hardware that reports doorbell rings."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"access_device_{namespace}_{device_id}")},
        name=device_name or f"Access device {device_id}",
        manufacturer=MANUFACTURER,
    )


class UnifiAccessEntity(CoordinatorEntity[UnifiInsightsAccessCoordinator]):
    """Base entity for Access. Available only with valid, undenied, present data."""

    _attr_has_entity_name = True
    _feature: Feature = "doors"

    def __init__(self, coordinator: UnifiInsightsAccessCoordinator) -> None:
        """Bind the entity to the coordinator that owns its console."""
        super().__init__(coordinator)
        namespace = coordinator.namespace
        if namespace is None:
            msg = "Access entities need a valid console namespace"
            raise ValueError(msg)
        self._namespace = namespace

    @property
    def available(self) -> bool:
        """Return False unless the last refresh worked and the resource is present."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.feature_available(self._feature)
            and self._is_present()
        )

    def _is_present(self) -> bool:
        """Return True when the entity's resource is in the current data."""
        return True

    @staticmethod
    async def _async_command[**P](
        message: str,
        command: Callable[P, Awaitable[None]],
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> None:
        """
        Call an Access coordinator command (ADR-010).

        The coordinator translates every UniFi API failure itself. Any other
        failure keeps ``message`` as a translated ``command_failed`` error,
        as before the command path was typed.
        """
        await async_execute_command(
            CommandFailure(translation_key="command_failed", message=message),
            command,
            *args,
            **kwargs,
        )


class UnifiAccessDoorEntity(UnifiAccessEntity):
    """Base entity for one Access door."""

    def __init__(
        self, coordinator: UnifiInsightsAccessCoordinator, door_id: str
    ) -> None:
        """Bind the entity to one door id."""
        super().__init__(coordinator)
        self._door_id = door_id

    @property
    def door(self) -> dict[str, object] | None:
        """Return the door record, or None when the door is gone."""
        door: dict[str, object] | None = self.coordinator.data["doors"].get(
            self._door_id
        )
        return door

    def _is_present(self) -> bool:
        """Return True while the door is in the current data."""
        return self.door is not None


def access_coordinator_for(
    entry: ConfigEntry,
) -> UnifiInsightsAccessCoordinator | None:
    """Return the entry's Access coordinator, or None for any other entry type."""
    # Imported here: the package __init__ imports the platforms, so a module-level
    # import would make a cycle.
    from . import UnifiInsightsData  # noqa: PLC0415

    data = getattr(entry, "runtime_data", None)
    if not isinstance(data, UnifiInsightsData):
        return None
    return data.access_coordinator


@callback
def async_discover_access_entities(
    entry: ConfigEntry,
    coordinator: UnifiInsightsAccessCoordinator,
    add_entities: AddEntitiesCallback,
    *,
    known_ids: set[str],
    keys: Callable[[], Iterable[str]],
    make: Callable[[str], Entity],
) -> None:
    """
    Add entities for new keys now and after every coordinator update.

    Nothing is added and no listener is registered without a valid console
    namespace, so entities never get an identity the console does not own.
    """
    if coordinator.namespace is None:
        return

    @callback
    def _discover() -> None:
        """Add the entities for keys that have not been seen before."""
        new_keys = [key for key in keys() if key not in known_ids]
        if not new_keys:
            return
        known_ids.update(new_keys)
        add_entities([make(key) for key in new_keys])

    _discover()
    entry.async_on_unload(coordinator.async_add_listener(_discover))
