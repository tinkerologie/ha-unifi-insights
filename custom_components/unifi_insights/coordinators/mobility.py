# Copyright 2026 UniFi Insights contributors
"""Optional UniFi Mobility polling for the entry that owns a cloud API key."""

from __future__ import annotations

import hmac
import logging
import math
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.hass_dict import HassKey

from custom_components.unifi_insights.api import (
    ApiKeyAuth,
    UniFiAuthenticationError,
    UniFiError,
    UniFiRateLimitError,
    UniFiResponseError,
)
from custom_components.unifi_insights.api.mobility import (
    UniFiMobilityClient,
    is_valid_mobility_id,
)
from custom_components.unifi_insights.const import (
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_REMOTE,
    DOMAIN,
    SCAN_INTERVAL_MOBILITY,
    SCAN_INTERVAL_MOBILITY_IDLE,
)

if TYPE_CHECKING:
    from aiohttp import ClientSession
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
# Polling uses at most half of the key's 100 requests per minute on average,
# leaving the rest for anything else that uses the same key.
_REQUESTS_PER_MINUTE_BUDGET = 50
# Entry id that currently polls Mobility, per API key fingerprint.
_OWNERS: HassKey[dict[str, str]] = HassKey(f"{DOMAIN}_mobility_owners")
ACCESS_UNKNOWN = "unknown"
ACCESS_GRANTED = "granted"
ACCESS_DENIED = "denied"


def _empty_snapshot(access: str = ACCESS_UNKNOWN) -> dict[str, Any]:
    """Return a snapshot without workspaces or devices."""
    return {"access": access, "workspaces": {}, "devices": {}, "updated_at": None}


def _means_no_access(err: UniFiError) -> bool:
    """
    Return True if a workspace-list error says the key has no Mobility.

    The documented answer is 403, but any other client error (400, 404) is
    just as permanent for an account that has never used Mobility. Rate
    limiting and server errors are outages, not an answer.
    """
    if isinstance(err, UniFiAuthenticationError):
        return True
    status = err.status_code if isinstance(err, UniFiResponseError) else None
    return (
        not isinstance(err, UniFiRateLimitError)
        and isinstance(status, int)
        and HTTPStatus.BAD_REQUEST <= status < HTTPStatus.INTERNAL_SERVER_ERROR
    )


class UnifiInsightsMobilityCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll Mobility workspaces and routers for one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: UniFiMobilityClient,
    ) -> None:
        """Initialize the coordinator for the entry that owns the API key."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_mobility",
            update_interval=SCAN_INTERVAL_MOBILITY,
        )
        self.client = client
        self.data = _empty_snapshot()
        self.last_error_type: str | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch Mobility data, keeping the last snapshot when a poll fails."""
        try:
            snapshot = await self._async_fetch()
        except UniFiError as err:
            self.last_error_type = type(err).__name__
            msg = f"Error fetching UniFi Mobility data ({type(err).__name__})"
            raise UpdateFailed(msg) from err
        self.last_error_type = None
        return snapshot

    async def _async_fetch(self) -> dict[str, Any]:
        """Read workspaces, then each active workspace's routers and their detail."""
        now = datetime.now(UTC).isoformat()
        try:
            workspace_rows = await self.client.list_workspaces()
        except UniFiError as err:
            if not _means_no_access(err):
                raise
            # Most cloud keys have no Mobility scope. That is not a broken
            # entry, so it never starts reauth; it is only re-checked hourly.
            if self.data.get("access") != ACCESS_DENIED:
                _LOGGER.info(
                    "UniFi Mobility is not available to this API key (status %s), "
                    "checking again in an hour",
                    getattr(err, "status_code", None),
                )
            self.update_interval = SCAN_INTERVAL_MOBILITY_IDLE
            return {**_empty_snapshot(ACCESS_DENIED), "updated_at": now}

        if self.data.get("access") == ACCESS_DENIED:
            _LOGGER.info("UniFi Mobility is available to this API key again")

        workspaces: dict[str, dict[str, Any]] = {}
        devices: dict[str, dict[str, Any]] = {}
        requests = 1
        for row in workspace_rows:
            workspace_id = row.get("workspace_id")
            if not is_valid_mobility_id(workspace_id):
                continue
            workspace: dict[str, Any] = {
                "name": str(row.get("workspace_name") or ""),
                "status": row.get("status"),
                "is_owner": row.get("is_owner") is True,
                "device_ids": [],
            }
            workspaces[workspace_id] = workspace
            # Pending, declined and inactive memberships cannot be read.
            if workspace["status"] != "ACTIVE":
                continue

            requests += 1
            try:
                summaries = await self.client.list_devices(workspace_id)
            except UniFiAuthenticationError:
                _LOGGER.debug("Skipping a Mobility workspace this key cannot read")
                continue

            for summary in summaries:
                device_id = summary.get("id")
                if not is_valid_mobility_id(device_id) or device_id in devices:
                    continue
                requests += 1
                workspace["device_ids"].append(device_id)
                devices[device_id] = await self._async_device(
                    workspace_id, device_id, summary
                )

        self.update_interval = (
            max(
                SCAN_INTERVAL_MOBILITY,
                timedelta(minutes=math.ceil(requests / _REQUESTS_PER_MINUTE_BUDGET)),
            )
            if devices
            else SCAN_INTERVAL_MOBILITY_IDLE
        )
        return {
            "access": ACCESS_GRANTED,
            "workspaces": workspaces,
            "devices": devices,
            "updated_at": now,
        }

    async def _async_device(
        self, workspace_id: str, device_id: str, summary: dict[str, Any]
    ) -> dict[str, Any]:
        """Return a router's detail, or its summary if the detail is unavailable."""
        try:
            detail = await self.client.get_device(workspace_id, device_id)
        except UniFiRateLimitError:
            raise
        except (UniFiAuthenticationError, UniFiResponseError) as err:
            # A router removed between the two calls answers 404; an upstream
            # error, or a router this key may no longer read, should not hide
            # every other router.
            _LOGGER.debug(
                "Mobility device detail unavailable (status %s)", err.status_code
            )
            return {**summary, "workspace_id": workspace_id, "detail_available": False}
        return {
            **summary,
            **detail,
            "id": device_id,
            "workspace_id": workspace_id,
            "detail_available": True,
        }


def _key_fingerprint(api_key: str) -> str:
    """Return a stable, non-reversible key for the ownership registry."""
    return hmac.new(
        b"unifi_insights_mobility_owner", api_key.encode(), sha256
    ).hexdigest()


def _owners(hass: HomeAssistant) -> dict[str, str]:
    """Return the registry of entries currently polling Mobility."""
    owners: dict[str, str] = hass.data.setdefault(_OWNERS, {})
    return owners


def _owner_candidates(
    hass: HomeAssistant, api_key: Any, *, exclude: str | None = None
) -> list[ConfigEntry]:
    """Return enabled remote entries using the key, oldest first."""
    return sorted(
        (
            candidate
            for candidate in hass.config_entries.async_entries(
                DOMAIN, include_ignore=False, include_disabled=False
            )
            if candidate.entry_id != exclude
            and candidate.data.get(CONF_CONNECTION_TYPE) == CONNECTION_TYPE_REMOTE
            and candidate.data.get(CONF_API_KEY) == api_key
        ),
        key=lambda candidate: candidate.entry_id,
    )


def is_mobility_owner(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Return True if this entry should poll Mobility for its API key.

    Mobility is account-wide, so remote entries sharing a key would create the
    same entities. An entry already polling keeps Mobility until it unloads;
    otherwise the oldest enabled remote entry with the key takes it, which
    keeps the choice the same across restarts.
    """
    if entry.data.get(CONF_CONNECTION_TYPE) != CONNECTION_TYPE_REMOTE:
        return False
    api_key = entry.data.get(CONF_API_KEY)
    holder = _owners(hass).get(_key_fingerprint(str(api_key)))
    if holder is not None and holder != entry.entry_id:
        return False
    candidates = _owner_candidates(hass, api_key)
    return bool(candidates) and candidates[0].entry_id == entry.entry_id


@callback
def _async_release_ownership(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Let another entry take Mobility once this one stops polling it."""
    fingerprint = _key_fingerprint(str(entry.data.get(CONF_API_KEY)))
    owners = _owners(hass)
    if owners.get(fingerprint) == entry.entry_id:
        del owners[fingerprint]


@callback
def async_hand_over_mobility(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Start Mobility on the next entry when its owner is disabled or deleted.

    A reload or Home Assistant stopping is not a hand-over: the owner comes
    back and keeps Mobility, so no other entry is reloaded for it.
    """
    if hass.is_stopping or entry.data.get(CONF_CONNECTION_TYPE) != (
        CONNECTION_TYPE_REMOTE
    ):
        return
    _async_release_ownership(hass, entry)
    if _key_fingerprint(str(entry.data.get(CONF_API_KEY))) in _owners(hass):
        # Another entry already polls Mobility for this key.
        return
    candidates = _owner_candidates(
        hass, entry.data.get(CONF_API_KEY), exclude=entry.entry_id
    )
    if not candidates:
        return
    successor = candidates[0]
    if (
        successor.state is ConfigEntryState.LOADED
        and successor.runtime_data.mobility_coordinator is None
    ):
        _LOGGER.debug("Handing UniFi Mobility over to another entry")
        hass.config_entries.async_schedule_reload(successor.entry_id)


@callback
def async_setup_mobility(
    hass: HomeAssistant, entry: ConfigEntry, session: ClientSession
) -> UnifiInsightsMobilityCoordinator | None:
    """Start Mobility polling if this entry owns its API key's Mobility data."""
    if not is_mobility_owner(hass, entry):
        _LOGGER.debug("UniFi Mobility is polled by another entry with this API key")
        return None

    _owners(hass)[_key_fingerprint(str(entry.data[CONF_API_KEY]))] = entry.entry_id
    entry.async_on_unload(lambda: _async_release_ownership(hass, entry))
    client = UniFiMobilityClient(
        auth=ApiKeyAuth(api_key=entry.data[CONF_API_KEY]), session=session, timeout=30
    )
    entry.async_on_unload(client.close)
    coordinator = UnifiInsightsMobilityCoordinator(hass, entry, client)
    # A background task keeps optional cloud requests from holding up
    # Home Assistant startup.
    entry.async_create_background_task(
        hass, coordinator.async_refresh(), name=f"{DOMAIN}_mobility_initial_refresh"
    )
    return coordinator
