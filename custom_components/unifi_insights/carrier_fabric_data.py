# Copyright (c) 2026 Ruaan Deysel
"""Runtime data models and type aliases for UniFi Carrier Fabric."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from unifi_official_api.carrier_fabric.client import UniFiCarrierFabricClient

    from .coordinators.carrier_fabric import UnifiCarrierFabricCoordinator


@dataclass
class CarrierFabricData:
    """Runtime data for UniFi Carrier Fabric integration."""

    client: UniFiCarrierFabricClient
    coordinator: UnifiCarrierFabricCoordinator


# Use TypeAlias for proper mypy validation (Python 3.10+ style)
CarrierFabricConfigEntry: TypeAlias = ConfigEntry[CarrierFabricData]  # noqa: UP040
