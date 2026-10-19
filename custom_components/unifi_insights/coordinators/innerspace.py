# Copyright 2026 UniFi Insights contributors
"""InnerSpace coordinator for UniFi Insights floor-plan and inventory data."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiResponseError,
    UniFiTimeoutError,
)

from custom_components.unifi_insights.const import SCAN_INTERVAL_INNERSPACE
from custom_components.unifi_insights.data_transforms import (
    normalize_innerspace_snapshot,
)

from .base import UnifiInsightsBaseCoordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from unifi_official_api.innerspace import UniFiInnerSpaceClient
    from unifi_official_api.network import UniFiNetworkClient
    from unifi_official_api.protect import UniFiProtectClient

    from .device import UnifiInsightsDeviceCoordinator
    from .protect import UnifiInsightsProtectCoordinator

_LOGGER = logging.getLogger(__name__)


class UnifiInsightsInnerSpaceCoordinator(UnifiInsightsBaseCoordinator):
    """
    Coordinator for UniFi InnerSpace floor plans, placed devices, and inventory.

    Polls InnerSpace independently (5-minute interval) and correlates
    InnerSpace records with Network and Protect devices by normalized MAC.
    Preserves the last valid snapshot when a transient refresh failure occurs.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        network_client: UniFiNetworkClient,
        protect_client: UniFiProtectClient | None,
        innerspace_client: UniFiInnerSpaceClient,
        entry: ConfigEntry,
        *,
        device_coordinator: UnifiInsightsDeviceCoordinator | None = None,
        protect_coordinator: UnifiInsightsProtectCoordinator | None = None,
    ) -> None:
        """Initialize the InnerSpace coordinator."""
        super().__init__(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=entry,
            name="innerspace",
            update_interval=SCAN_INTERVAL_INNERSPACE,
        )
        self.innerspace_client = innerspace_client
        self._device_coordinator = device_coordinator
        self._protect_coordinator = protect_coordinator
        # Floor plans that currently have a device (those with an image_url),
        # for stale device cleanup.
        self._previous_floor_plan_ids: set[str] = set()
        self.data: dict[str, Any] = {
            **normalize_innerspace_snapshot(),
            "last_update": None,
        }

    def _cleanup_stale_floor_plan_devices(self) -> None:
        """
        Remove devices of floor plans that are no longer reported.

        Only plans with an image_url get an image entity and device, so only
        those are tracked. Runs on successful polls only: a failed poll keeps
        the previous snapshot and never reaches here.
        """
        current_ids = {
            plan_id
            for plan_id, plan in self.data.get("floor_plans", {}).items()
            if isinstance(plan, dict) and plan.get("image_url")
        }
        for plan_id in self._previous_floor_plan_ids - current_ids:
            self._remove_stale_device(f"innerspace_floor_plan_{plan_id}")
        self._previous_floor_plan_ids = current_ids

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch InnerSpace floor plans, placed devices, and inventory."""
        try:
            _LOGGER.debug("InnerSpace coordinator: Fetching InnerSpace data")
            (
                project,
                floor_plans,
                access_points,
                switches,
                inventory,
            ) = await asyncio.gather(
                self.innerspace_client.get_project(),
                self.innerspace_client.list_floor_plans(),
                self.innerspace_client.list_access_points(),
                self.innerspace_client.list_switches(),
                self.innerspace_client.list_inventory(),
            )

            network_devices = (
                self._device_coordinator.data.get("devices", {})
                if self._device_coordinator and self._device_coordinator.data
                else None
            )
            protect_devices = (
                self._protect_coordinator.data
                if self._protect_coordinator and self._protect_coordinator.data
                else None
            )

            snapshot = normalize_innerspace_snapshot(
                project=project,
                floor_plans=floor_plans,
                access_points=access_points,
                switches=switches,
                inventory=inventory,
                network_devices=network_devices,
                protect_devices=protect_devices,
            )
            snapshot["last_update"] = datetime.now(tz=UTC)
            self.data = snapshot
            self._available = True
            self._cleanup_stale_floor_plan_devices()
            _LOGGER.debug(
                "InnerSpace coordinator: Update complete - %d floor plans, "
                "%d access points, %d switches, %d inventory records",
                len(snapshot["floor_plans"]),
                len(snapshot["access_points"]),
                len(snapshot["switches"]),
                len(snapshot["inventory"]),
            )
            return self.data

        except UniFiAuthenticationError as err:
            self._handle_auth_error(err)
        except UniFiConnectionError as err:
            self._handle_connection_error(err)
        except UniFiTimeoutError as err:
            self._handle_timeout_error(err)
        except UniFiResponseError as err:
            self._handle_response_error(err)
        except Exception as err:
            self._handle_generic_error(err)

        return self.data  # pragma: no cover
