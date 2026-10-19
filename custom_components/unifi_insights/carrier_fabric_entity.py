# Copyright (c) 2026 Ruaan Deysel
"""Base entity classes for UniFi Carrier Fabric integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from . import CarrierFabricConfigEntry
    from .coordinators.carrier_fabric import UnifiInsightsCarrierFabricCoordinator


class UnifiCarrierFabricEntity(
    CoordinatorEntity["UnifiInsightsCarrierFabricCoordinator"]
):
    """Base entity for UniFi Carrier Fabric organisation-level entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: UnifiInsightsCarrierFabricCoordinator,
        entry: CarrierFabricConfigEntry,
    ) -> None:
        """Initialize the Carrier Fabric entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._entry_unique_id: str = entry.unique_id or entry.entry_id

    @property
    def device_info(self) -> DeviceInfo:
        """Return organisation device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_unique_id)},
            name="UniFi Carrier Fabric",
            manufacturer="Ubiquiti",
            model="Carrier Fabric",
            entry_type=DeviceEntryType.SERVICE,
        )


class UnifiCarrierFabricSubscriberEntity(UnifiCarrierFabricEntity):
    """Base entity for UniFi Carrier Fabric subscriber entities."""

    def __init__(
        self,
        coordinator: UnifiInsightsCarrierFabricCoordinator,
        entry: CarrierFabricConfigEntry,
        subscriber_id: str,
    ) -> None:
        """Initialize the subscriber entity."""
        super().__init__(coordinator, entry)
        self._subscriber_id = subscriber_id

    @property
    def _subscriber_data(self) -> dict[str, Any] | None:
        """Get subscriber data from coordinator."""
        subs = self.coordinator.data.get("subscribers", {})
        if isinstance(subs, dict):
            return subs.get(self._subscriber_id)
        return None

    @property
    def available(self) -> bool:
        """Return True if subscriber is present in coordinator data."""
        if not super().available:
            return False
        subs = self.coordinator.data.get("subscribers", {})
        return isinstance(subs, dict) and self._subscriber_id in subs

    @property
    def device_info(self) -> DeviceInfo:
        """Return subscriber device info."""
        sub = self._subscriber_data or {}
        name = (
            sub.get("name")
            or sub.get("subscriberNumber")
            or f"Subscriber {self._subscriber_id[:8]}"
        )
        info: dict[str, Any] = {
            "identifiers": {(DOMAIN, f"carrier_subscriber_{self._subscriber_id}")},
            "via_device": (DOMAIN, self._entry_unique_id),
            "name": str(name),
            "manufacturer": "Ubiquiti",
            "model": "Subscriber",
            "entry_type": DeviceEntryType.SERVICE,
        }
        return cast("DeviceInfo", info)
