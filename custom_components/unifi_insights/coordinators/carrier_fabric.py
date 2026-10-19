# Copyright (c) 2026 Ruaan Deysel
"""UniFi Carrier Fabric coordinator."""

from __future__ import annotations

import logging
import uuid
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Final

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiError,
    UniFiRateLimitError,
    UniFiResponseError,
    UniFiTimeoutError,
)

from custom_components.unifi_insights.const import (
    CARRIER_FABRIC_SCAN_INTERVAL,
    CONF_CARRIER_ORG_ID,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from unifi_official_api.carrier_fabric.client import (
        UniFiCarrierFabricClient,
    )

_LOGGER = logging.getLogger(__name__)

SUBSCRIBER_ALLOWLIST: Final = frozenset(
    {
        "id",
        "orgId",
        "name",
        "subscriberNumber",
        "planId",
        "state",
        "suspended",
        "suspendedAt",
        "activatedAt",
        "createdAt",
        "updatedAt",
    }
)

SERVICE_PLAN_ALLOWLIST: Final = frozenset(
    {
        "id",
        "orgId",
        "name",
        "status",
        "downloadMbps",
        "uploadMbps",
        "archivedAt",
        "createdAt",
        "updatedAt",
    }
)

KNOWN_SUBSCRIBER_STATES: Final = (
    "pending_assignment",
    "provisioned",
    "installed",
    "suspended",
)


class InvalidSubscriberIdError(HomeAssistantError, ValueError):
    """Raised when a subscriber ID is not a valid UUID string."""


class UnifiCarrierFabricCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for UniFi Carrier Fabric API."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        client: UniFiCarrierFabricClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the Carrier Fabric coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_carrier_fabric",
            update_interval=CARRIER_FABRIC_SCAN_INTERVAL,
        )
        self.client = client
        self.data: dict[str, Any] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all data from Carrier Fabric API."""
        try:
            raw_plans = await self.client.service_plans.get_all()
            raw_subs = await self.client.subscribers.get_all()
        except (UniFiAuthenticationError, UniFiResponseError) as err:
            status = err.status_code
            if isinstance(err, UniFiAuthenticationError) or status in (
                HTTPStatus.UNAUTHORIZED,
                HTTPStatus.FORBIDDEN,
            ):
                if (
                    status == HTTPStatus.FORBIDDEN
                    or err.api_error_code == "insufficient_scope"
                ):
                    msg = (
                        "Carrier Fabric API key missing required scope "
                        f"(insufficient_scope): {err}"
                    )
                    raise ConfigEntryAuthFailed(msg) from err
                msg = f"Carrier Fabric authentication failed: {err}"
                raise ConfigEntryAuthFailed(msg) from err
            if (
                isinstance(err, UniFiRateLimitError)
                or status == HTTPStatus.TOO_MANY_REQUESTS
            ):
                msg = f"Carrier Fabric API rate limited: {err}"
                raise UpdateFailed(msg) from err
            msg = f"Carrier Fabric API error: {err}"
            raise UpdateFailed(msg) from err
        except UniFiConnectionError as err:
            msg = f"Error connecting to Carrier Fabric API: {err}"
            raise UpdateFailed(msg) from err
        except UniFiTimeoutError as err:
            msg = f"Timeout connecting to Carrier Fabric API: {err}"
            raise UpdateFailed(msg) from err
        except UniFiError as err:
            # Every transport failure reaches here as a UniFiError subclass, so
            # anything else is a bug and is left to surface.
            msg = f"Unexpected error communicating with Carrier Fabric API: {err}"
            raise UpdateFailed(msg) from err

        # Ingest and deduplicate service plans, filtering by allowlist
        service_plans: dict[str, dict[str, Any]] = {}
        for plan in raw_plans:
            plan_dict = plan.model_dump(by_alias=True)
            plan_id = plan_dict.get("id")
            if not isinstance(plan_id, str) or not plan_id or plan_id in service_plans:
                continue
            filtered_plan = {
                k: v for k, v in plan_dict.items() if k in SERVICE_PLAN_ALLOWLIST
            }
            service_plans[plan_id] = filtered_plan

        # Ingest and deduplicate subscribers, filtering by allowlist
        subscribers: dict[str, dict[str, Any]] = {}
        for sub in raw_subs:
            sub_dict = sub.model_dump(by_alias=True)
            sub_id = sub_dict.get("id")
            if not isinstance(sub_id, str) or not sub_id or sub_id in subscribers:
                continue
            filtered_sub = {
                k: v for k, v in sub_dict.items() if k in SUBSCRIBER_ALLOWLIST
            }
            subscribers[sub_id] = filtered_sub

        # Resolve org ID from entry data or discovered resources
        org_id = self.config_entry.data.get(CONF_CARRIER_ORG_ID)
        if not org_id:
            for plan_data in service_plans.values():
                if plan_data.get("orgId"):
                    org_id = plan_data["orgId"]
                    break
            if not org_id:
                for sub_data in subscribers.values():
                    if sub_data.get("orgId"):
                        org_id = sub_data["orgId"]
                        break

        # Compute summary
        subscribers_by_state: dict[str, int] = dict.fromkeys(KNOWN_SUBSCRIBER_STATES, 0)
        subscribers_by_state["unknown"] = 0
        subscribers_by_plan: dict[str, int] = {}
        suspended_count = 0
        unassigned_count = 0

        for sub_data in subscribers.values():
            if sub_data.get("suspended") is True:
                suspended_count += 1
            state = sub_data.get("state")
            if state in KNOWN_SUBSCRIBER_STATES:
                subscribers_by_state[state] += 1
            else:
                subscribers_by_state["unknown"] += 1

            plan_id = sub_data.get("planId")
            if plan_id is not None and str(plan_id).strip():
                plan_id_str = str(plan_id)
                subscribers_by_plan[plan_id_str] = (
                    subscribers_by_plan.get(plan_id_str, 0) + 1
                )
            else:
                unassigned_count += 1

        active_plans_count = sum(
            1
            for plan_data in service_plans.values()
            if plan_data.get("status") == "active"
        )

        summary = {
            "total_subscribers": len(subscribers),
            "suspended_subscribers": suspended_count,
            "subscribers_by_state": subscribers_by_state,
            "active_service_plans": active_plans_count,
            "subscribers_by_plan": subscribers_by_plan,
            "unassigned_subscribers": unassigned_count,
        }

        return {
            "org_id": org_id,
            "service_plans": service_plans,
            "subscribers": subscribers,
            "summary": summary,
        }

    @staticmethod
    def _validate_subscriber_uuid(subscriber_id: Any) -> None:
        """Validate that subscriber_id is a valid UUID."""
        if not isinstance(subscriber_id, str):
            msg = f"Invalid subscriber ID (UUID string required): {subscriber_id}"
            raise InvalidSubscriberIdError(msg)
        try:
            uuid.UUID(subscriber_id)
        except (ValueError, TypeError, AttributeError) as err:
            msg = f"Invalid subscriber ID format (valid UUID required): {subscriber_id}"
            raise InvalidSubscriberIdError(msg) from err

    async def async_suspend_subscriber(
        self, subscriber_id: str, reason: str | None = None
    ) -> None:
        """Suspend a subscriber with retry on write conflict."""
        self._validate_subscriber_uuid(subscriber_id)
        try:
            await self.client.subscribers.suspend(subscriber_id, reason=reason)
        except UniFiError as err:
            if getattr(err, "api_error_code", None) == "write_conflict_retryable":
                _LOGGER.debug(
                    "Write conflict suspending subscriber %s, retrying once",
                    subscriber_id,
                )
                await self.client.subscribers.suspend(subscriber_id, reason=reason)
            else:
                raise
        await self.async_refresh()

    async def async_resume_subscriber(self, subscriber_id: str) -> None:
        """Resume a subscriber with retry on write conflict."""
        self._validate_subscriber_uuid(subscriber_id)
        try:
            await self.client.subscribers.resume(subscriber_id)
        except UniFiError as err:
            if getattr(err, "api_error_code", None) == "write_conflict_retryable":
                _LOGGER.debug(
                    "Write conflict resuming subscriber %s, retrying once",
                    subscriber_id,
                )
                await self.client.subscribers.resume(subscriber_id)
            else:
                raise
        await self.async_refresh()
