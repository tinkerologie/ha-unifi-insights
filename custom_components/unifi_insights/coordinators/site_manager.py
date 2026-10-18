# Copyright 2026 UniFi Insights contributors
"""Account-wide, optional UniFi Site Manager polling."""

from __future__ import annotations

import asyncio
import hmac
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util.hass_dict import HassKey

from custom_components.unifi_insights.api import ApiKeyAuth, UniFiRateLimitError
from custom_components.unifi_insights.api.const import DEFAULT_RATE_LIMIT_RETRY_AFTER
from custom_components.unifi_insights.api.site_manager import UniFiSiteManagerClient
from custom_components.unifi_insights.const import (
    DOMAIN,
    ISP_WAN_NUMBERS,
    SCAN_INTERVAL_SITE_MANAGER,
    SITE_MANAGER_COLLECTIONS,
)

if TYPE_CHECKING:
    from aiohttp import ClientSession
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
_ACCOUNT_REGISTRY: HassKey[dict[str, UnifiInsightsSiteManagerAccount]] = HassKey(
    f"{DOMAIN}_site_manager_accounts"
)


def _empty_snapshot() -> dict[str, Any]:
    """Return independent empty sections for a newly added account."""
    return {
        "hosts": {},
        "sites": {},
        "devices": {},
        "isp_metrics": {},
        "sd_wan_configs": {},
        "collections": {
            name: {"available": False, "updated_at": None, "error": None}
            for name in SITE_MANAGER_COLLECTIONS
        },
        "last_attempt": None,
        "cooldown_until": None,
    }


def _by_id(rows: list[dict[str, Any]], field_name: str) -> dict[str, dict[str, Any]]:
    """Index records without inventing identifiers for malformed rows."""
    return {
        identifier: row
        for row in rows
        if isinstance(identifier := row.get(field_name), str) and identifier
    }


def _latest_isp_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep only the newest numeric WAN period for each host and site."""
    latest: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        host_id = row.get("hostId")
        site_id = row.get("siteId")
        if not isinstance(host_id, str) or not isinstance(site_id, str):
            continue
        for period in row.get("periods") or []:
            if not isinstance(period, dict):
                continue
            metric_time = period.get("metricTime")
            period_data = period.get("data")
            wan = period_data.get("wan") if isinstance(period_data, dict) else None
            if not isinstance(metric_time, str) or not isinstance(wan, dict):
                continue
            try:
                timestamp = datetime.fromisoformat(metric_time)
            except ValueError:
                continue
            if timestamp.tzinfo is None:
                continue
            current = latest.setdefault(host_id, {}).get(site_id)
            if current and timestamp <= datetime.fromisoformat(current["metric_time"]):
                continue
            latest[host_id][site_id] = {
                "metric_time": timestamp.isoformat(),
                "wan": {
                    key: value
                    for key in ISP_WAN_NUMBERS
                    if isinstance(value := wan.get(key), (int, float))
                    and not isinstance(value, bool)
                },
            }
    return latest


class UnifiInsightsSiteManagerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll Site Manager independently of console-scoped coordinators."""

    def __init__(self, hass: HomeAssistant, client: UniFiSiteManagerClient) -> None:
        """Initialize the shared coordinator without binding it to one entry."""
        # An omitted config_entry falls back to the entry being set up, which
        # would register this shared poller's shutdown on that one entry's
        # unload and stop polling for every other entry using the account.
        super().__init__(
            hass,
            _LOGGER,
            config_entry=None,
            name=f"{DOMAIN}_site_manager",
            update_interval=SCAN_INTERVAL_SITE_MANAGER,
        )
        self.client = client
        self.data = _empty_snapshot()
        self._cooldown_until: datetime | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Refresh independent collections while keeping each last good value."""
        now = datetime.now(UTC)
        snapshot = {
            **self.data,
            "collections": {
                name: dict(state) for name, state in self.data["collections"].items()
            },
            "last_attempt": now.isoformat(),
        }
        if self._cooldown_until and now < self._cooldown_until:
            return snapshot
        self._cooldown_until = None

        # A one-hour lookback allows ingestion lag while bounding the response.
        requests = (
            self.client.list_hosts(),
            self.client.list_sites(),
            self.client.list_devices(),
            self.client.get_isp_metrics(
                begin_timestamp=now - timedelta(hours=1), end_timestamp=now
            ),
            self.client.list_sd_wan_configs(),
        )
        results = await asyncio.gather(*requests, return_exceptions=True)
        for name, result in zip(SITE_MANAGER_COLLECTIONS, results, strict=True):
            state = snapshot["collections"][name]
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                if state["error"] is None:
                    _LOGGER.warning(
                        "Site Manager %s unavailable (%s)",
                        name,
                        type(result).__name__,
                    )
                state["available"] = False
                state["error"] = type(result).__name__
                if isinstance(result, UniFiRateLimitError):
                    retry_after = max(result.retry_after or 0, 0)
                    try:
                        deadline = now + timedelta(seconds=retry_after)
                    except OverflowError:
                        deadline = now + timedelta(
                            seconds=DEFAULT_RATE_LIMIT_RETRY_AFTER
                        )
                    if self._cooldown_until is None or deadline > self._cooldown_until:
                        self._cooldown_until = deadline
                continue

            try:
                if name == "hosts":
                    snapshot[name] = _by_id(result, "id")
                elif name == "sites":
                    snapshot[name] = _by_id(result, "siteId")
                elif name == "devices":
                    snapshot[name] = _by_id(result, "hostId")
                elif name == "isp_metrics":
                    snapshot[name] = _latest_isp_metrics(result)
                else:
                    snapshot[name] = _by_id(result, "id")
            except (AttributeError, TypeError, ValueError) as err:
                if state["error"] is None:
                    _LOGGER.warning(
                        "Site Manager %s unavailable (%s)",
                        name,
                        type(err).__name__,
                    )
                state["available"] = False
                state["error"] = type(err).__name__
                continue
            if state["error"] is not None:
                _LOGGER.info("Site Manager %s available again", name)
            state.update(available=True, updated_at=now.isoformat(), error=None)

        snapshot["cooldown_until"] = (
            self._cooldown_until.isoformat() if self._cooldown_until else None
        )
        return snapshot


@dataclass
class UnifiInsightsSiteManagerAccount:
    """A shared client/coordinator and the entries currently using it."""

    client: UniFiSiteManagerClient
    coordinator: UnifiInsightsSiteManagerCoordinator
    users: set[str] = field(default_factory=set)
    initial_refresh: asyncio.Task[None] | None = None


async def _async_initial_refresh(
    coordinator: UnifiInsightsSiteManagerCoordinator,
) -> None:
    """Load optional cloud data without delaying a working console entry."""
    try:
        await coordinator.async_refresh()
    except Exception as err:
        _LOGGER.warning("Initial Site Manager refresh failed (%s)", type(err).__name__)


async def async_acquire_site_manager(
    hass: HomeAssistant, api_key: str, entry_id: str, session: ClientSession
) -> tuple[str, UnifiInsightsSiteManagerAccount]:
    """Share account polling among remote entries with the same API key."""
    fingerprint = hmac.new(
        b"unifi_insights_account_registry", api_key.encode(), sha256
    ).hexdigest()
    registry: dict[str, UnifiInsightsSiteManagerAccount] = hass.data.setdefault(
        _ACCOUNT_REGISTRY, {}
    )
    account = registry.get(fingerprint)
    if account is None:
        client = UniFiSiteManagerClient(
            auth=ApiKeyAuth(api_key=api_key), session=session, timeout=30
        )
        account = UnifiInsightsSiteManagerAccount(
            client=client,
            coordinator=UnifiInsightsSiteManagerCoordinator(hass, client),
        )
        registry[fingerprint] = account
        # A background task keeps optional cloud requests from holding up
        # Home Assistant startup.
        account.initial_refresh = hass.async_create_background_task(
            _async_initial_refresh(account.coordinator),
            name=f"{DOMAIN}_site_manager_initial_refresh",
        )
    account.users.add(entry_id)
    return fingerprint, account


async def async_release_site_manager(
    hass: HomeAssistant, fingerprint: str, entry_id: str
) -> None:
    """Stop shared polling after the last remote entry unloads."""
    registry: dict[str, UnifiInsightsSiteManagerAccount] = hass.data.get(
        _ACCOUNT_REGISTRY, {}
    )
    account = registry.get(fingerprint)
    if account is None:
        return
    account.users.discard(entry_id)
    if account.users:
        return
    del registry[fingerprint]
    if account.initial_refresh and not account.initial_refresh.done():
        account.initial_refresh.cancel()
        await asyncio.gather(account.initial_refresh, return_exceptions=True)
    await account.coordinator.async_shutdown()
    await account.client.close()
