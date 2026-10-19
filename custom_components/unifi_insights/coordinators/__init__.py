# Copyright 2026 UniFi Insights contributors
"""Multi-coordinator architecture for UniFi Insights (Platinum compliance)."""

from __future__ import annotations

from .access import UnifiInsightsAccessCoordinator
from .base import UnifiInsightsBaseCoordinator
from .carrier_fabric import UnifiInsightsCarrierFabricCoordinator
from .config import UnifiInsightsConfigCoordinator
from .device import UnifiInsightsDeviceCoordinator
from .facade import UnifiInsightsFacadeCoordinator
from .innerspace import UnifiInsightsInnerSpaceCoordinator
from .mobility import UnifiInsightsMobilityCoordinator
from .protect import UnifiInsightsProtectCoordinator
from .site_manager import UnifiInsightsSiteManagerCoordinator

__all__ = [
    "UnifiInsightsAccessCoordinator",
    "UnifiInsightsBaseCoordinator",
    "UnifiInsightsCarrierFabricCoordinator",
    "UnifiInsightsConfigCoordinator",
    "UnifiInsightsDeviceCoordinator",
    "UnifiInsightsFacadeCoordinator",
    "UnifiInsightsInnerSpaceCoordinator",
    "UnifiInsightsMobilityCoordinator",
    "UnifiInsightsProtectCoordinator",
    "UnifiInsightsSiteManagerCoordinator",
]
