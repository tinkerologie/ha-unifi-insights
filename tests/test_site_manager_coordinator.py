# Copyright 2026 UniFi Insights contributors
"""Tests for optional, account-wide Site Manager polling."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_insights.api import (
    UniFiRateLimitError,
    UniFiResponseError,
)
from custom_components.unifi_insights.api.const import DEFAULT_RATE_LIMIT_RETRY_AFTER
from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.coordinators.site_manager import (
    _ACCOUNT_REGISTRY,
    UnifiInsightsSiteManagerCoordinator,
    _async_initial_refresh,
    _latest_isp_metrics,
    async_acquire_site_manager,
    async_release_site_manager,
)
from custom_components.unifi_insights.diagnostics import _site_manager_summary

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _client() -> MagicMock:
    """Build a client whose five collections can be varied independently."""
    client = MagicMock()
    client.list_hosts = AsyncMock(return_value=[{"id": "host-secret"}])
    client.list_sites = AsyncMock(
        return_value=[{"siteId": "site-secret", "hostId": "host-secret"}]
    )
    client.list_devices = AsyncMock(
        return_value=[{"hostId": "host-secret", "devices": [{"id": "device"}]}]
    )
    client.get_isp_metrics = AsyncMock(
        return_value=[
            {
                "hostId": "host-secret",
                "siteId": "site-secret",
                "periods": [
                    {
                        "metricTime": "2026-01-02T03:05:00Z",
                        "data": {"wan": {"avgLatency": 5, "ispName": "private ISP"}},
                    },
                    {
                        "metricTime": "2026-01-02T03:10:00Z",
                        "data": {"wan": {"avgLatency": 2, "ispName": "private ISP"}},
                    },
                ],
            }
        ]
    )
    client.list_sd_wan_configs = AsyncMock(
        return_value=[
            {"id": "config-secret", "name": "Private VPN", "type": "sdwan-hbsp"}
        ]
    )
    client.close = AsyncMock()
    return client


def test_latest_isp_metrics_ignores_invalid_and_older_periods() -> None:
    """Only the newest valid, numeric WAN sample is retained for a site."""
    rows = [
        {"hostId": None, "siteId": "site"},
        {"hostId": "host", "siteId": None},
        {
            "hostId": "host",
            "siteId": "site",
            "periods": [
                None,
                {"metricTime": None, "data": {"wan": {}}},
                {"metricTime": "2026-01-02T03:10:00Z", "data": {"wan": []}},
                {"metricTime": "invalid", "data": {"wan": {}}},
                {"metricTime": "2026-01-02T03:10:00", "data": {"wan": {}}},
                {
                    "metricTime": "2026-01-02T03:10:00Z",
                    "data": {
                        "wan": {
                            "avgLatency": 2,
                            "uptime": True,
                            "ispName": "private ISP",
                        }
                    },
                },
                {
                    "metricTime": "2026-01-02T03:05:00Z",
                    "data": {"wan": {"avgLatency": 9}},
                },
                {
                    "metricTime": "2026-01-02T03:10:00Z",
                    "data": {"wan": {"avgLatency": 10}},
                },
            ],
        },
    ]

    assert _latest_isp_metrics(rows) == {
        "host": {
            "site": {
                "metric_time": "2026-01-02T03:10:00+00:00",
                "wan": {"avgLatency": 2},
            }
        }
    }


async def test_partial_failure_keeps_last_good_collection(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed sites call must not erase inventory or newer ISP data."""
    client = _client()
    coordinator = UnifiInsightsSiteManagerCoordinator(hass, client)
    first = await coordinator._async_update_data()
    coordinator.data = first

    assert first["sites"]["site-secret"]["hostId"] == "host-secret"
    assert first["isp_metrics"]["host-secret"]["site-secret"]["wan"] == {
        "avgLatency": 2
    }
    assert first["collections"]["sites"]["available"]
    metric_query = client.get_isp_metrics.call_args.kwargs
    assert metric_query["end_timestamp"] - metric_query["begin_timestamp"] == (
        timedelta(hours=1)
    )

    client.list_sites.side_effect = UniFiResponseError("temporary", status_code=502)
    client.list_hosts.return_value = [{"id": "new-host"}]
    caplog.set_level(
        logging.INFO,
        logger="custom_components.unifi_insights.coordinators.site_manager",
    )
    second = await coordinator._async_update_data()

    assert second["sites"] == first["sites"]
    assert second["hosts"] == {"new-host": {"id": "new-host"}}
    assert second["collections"]["sites"]["available"] is False
    assert second["collections"]["sites"]["error"] == "UniFiResponseError"
    assert (
        caplog.messages.count("Site Manager sites unavailable (UniFiResponseError)")
        == 1
    )
    assert "temporary" not in caplog.text

    coordinator.data = second
    await coordinator._async_update_data()
    assert (
        caplog.messages.count("Site Manager sites unavailable (UniFiResponseError)")
        == 1
    )

    client.list_sites.side_effect = None
    recovered = await coordinator._async_update_data()
    assert recovered["collections"]["sites"]["available"] is True
    assert caplog.messages.count("Site Manager sites available again") == 1


async def test_malformed_collection_keeps_last_good_data(hass: HomeAssistant) -> None:
    """A successful HTTP response with malformed rows does not erase inventory."""
    client = _client()
    coordinator = UnifiInsightsSiteManagerCoordinator(hass, client)
    first = await coordinator._async_update_data()
    coordinator.data = first
    client.list_sites.return_value = [None]

    second = await coordinator._async_update_data()

    assert second["sites"] == first["sites"]
    assert second["collections"]["sites"]["available"] is False
    assert second["collections"]["sites"]["error"] == "AttributeError"

    coordinator.data = second
    repeated = await coordinator._async_update_data()
    assert repeated["sites"] == first["sites"]
    assert repeated["collections"]["sites"]["error"] == "AttributeError"


async def test_cancelled_collection_update_propagates(hass: HomeAssistant) -> None:
    """Cancellation of a cloud request must stop the coordinator update."""
    client = _client()
    client.list_hosts.side_effect = asyncio.CancelledError()
    coordinator = UnifiInsightsSiteManagerCoordinator(hass, client)

    with pytest.raises(asyncio.CancelledError):
        await coordinator._async_update_data()


async def test_rate_limit_skips_requests_until_retry_after(hass: HomeAssistant) -> None:
    """A cloud 429 applies a shared cooldown to the account."""
    client = _client()
    client.list_hosts.side_effect = UniFiRateLimitError(
        "limited", status_code=429, retry_after=3600
    )
    coordinator = UnifiInsightsSiteManagerCoordinator(hass, client)
    coordinator.data = await coordinator._async_update_data()
    before = client.list_sites.await_count

    await coordinator._async_update_data()

    assert client.list_sites.await_count == before
    assert coordinator.data["cooldown_until"] is not None
    assert coordinator.data["collections"]["hosts"]["available"] is False

    coordinator._cooldown_until = datetime.now(UTC) - timedelta(seconds=1)
    client.list_hosts.side_effect = None
    resumed = await coordinator._async_update_data()
    assert resumed["cooldown_until"] is None
    assert client.list_sites.await_count == before + 1


async def test_longest_rate_limit_deadline_wins(hass: HomeAssistant) -> None:
    """Concurrent 429 responses share the longest requested cooldown."""
    client = _client()
    client.list_hosts.side_effect = UniFiRateLimitError(
        "limited", status_code=429, retry_after=60
    )
    client.list_sites.side_effect = UniFiRateLimitError(
        "limited", status_code=429, retry_after=120
    )
    client.list_devices.side_effect = UniFiRateLimitError(
        "limited", status_code=429, retry_after=30
    )
    coordinator = UnifiInsightsSiteManagerCoordinator(hass, client)

    snapshot = await coordinator._async_update_data()

    deadline = datetime.fromisoformat(snapshot["cooldown_until"])
    assert deadline - datetime.now(UTC) > timedelta(seconds=110)


@pytest.mark.parametrize(
    "retry_after",
    [10**12, 10**100],
    ids=["datetime-addition", "timedelta-construction"],
)
async def test_unrepresentable_rate_limit_deadline_uses_default(
    hass: HomeAssistant, retry_after: int
) -> None:
    """An unrepresentable Retry-After value does not abort the refresh."""
    client = _client()
    client.list_hosts.side_effect = UniFiRateLimitError(
        "limited", status_code=429, retry_after=retry_after
    )
    coordinator = UnifiInsightsSiteManagerCoordinator(hass, client)

    snapshot = await coordinator._async_update_data()

    deadline = datetime.fromisoformat(snapshot["cooldown_until"])
    remaining = deadline - datetime.now(UTC)
    assert timedelta(seconds=DEFAULT_RATE_LIMIT_RETRY_AFTER - 1) <= remaining
    assert remaining <= timedelta(seconds=DEFAULT_RATE_LIMIT_RETRY_AFTER)
    assert snapshot["collections"]["hosts"]["error"] == "UniFiRateLimitError"
    assert snapshot["collections"]["sites"]["available"] is True


async def test_initial_refresh_failure_does_not_leak_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An optional background refresh failure only logs its exception type."""
    coordinator = MagicMock()
    coordinator.async_refresh = AsyncMock(side_effect=RuntimeError("private body"))
    caplog.set_level(
        logging.WARNING,
        logger="custom_components.unifi_insights.coordinators.site_manager",
    )

    await _async_initial_refresh(coordinator)

    assert "Initial Site Manager refresh failed (RuntimeError)" in caplog.messages
    assert "private body" not in caplog.text


async def test_same_key_shares_coordinator_until_last_unload(
    hass: HomeAssistant,
) -> None:
    """Multiple console entries reuse one account poller and close it once."""
    client = _client()
    coordinator = MagicMock()
    coordinator.async_refresh = AsyncMock()
    coordinator.async_shutdown = AsyncMock()
    assert _ACCOUNT_REGISTRY not in hass.data
    assert DOMAIN not in hass.data
    with (
        patch(
            "custom_components.unifi_insights.coordinators.site_manager."
            "UniFiSiteManagerClient",
            return_value=client,
        ) as client_class,
        patch(
            "custom_components.unifi_insights.coordinators.site_manager."
            "UnifiInsightsSiteManagerCoordinator",
            return_value=coordinator,
        ),
    ):
        key1, account1 = await async_acquire_site_manager(
            hass, "same-key", "entry-1", MagicMock()
        )
        registry = hass.data[_ACCOUNT_REGISTRY]
        assert registry == {key1: account1}
        assert account1.users == {"entry-1"}
        assert DOMAIN not in hass.data
        key2, account2 = await async_acquire_site_manager(
            hass, "same-key", "entry-2", MagicMock()
        )
        assert key1 == key2
        assert account1 is account2
        assert hass.data[_ACCOUNT_REGISTRY] is registry
        assert registry == {key1: account1}
        assert account1.users == {"entry-1", "entry-2"}
        assert DOMAIN not in hass.data
        client_class.assert_called_once()
        assert account1.initial_refresh is not None
        await account1.initial_refresh
        coordinator.async_refresh.assert_awaited_once()

        await async_release_site_manager(hass, key1, "entry-1")
        assert account1.users == {"entry-2"}
        assert registry == {key1: account1}
        coordinator.async_shutdown.assert_not_awaited()
        client.close.assert_not_awaited()
        await async_release_site_manager(hass, key2, "entry-2")
        assert account1.users == set()
        assert hass.data[_ACCOUNT_REGISTRY] is registry
        assert registry == {}
        assert DOMAIN not in hass.data
        coordinator.async_shutdown.assert_awaited_once()
        client.close.assert_awaited_once()
        await async_release_site_manager(hass, key2, "entry-2")
        client.close.assert_awaited_once()


async def test_shared_coordinator_is_not_bound_to_the_first_entry(
    hass: HomeAssistant,
) -> None:
    """
    Unloading the entry that created the poller must not stop it for others.

    Without an explicit config_entry, DataUpdateCoordinator adopts the entry
    currently being set up and registers its own shutdown on that entry's
    unload, which would silently stop polling for every other entry sharing
    the account.
    """
    first_entry = MockConfigEntry(domain="unifi_insights", entry_id="entry-1")
    first_entry.add_to_hass(hass)
    client = _client()
    token = config_entries.current_entry.set(first_entry)
    try:
        with patch(
            "custom_components.unifi_insights.coordinators.site_manager."
            "UniFiSiteManagerClient",
            return_value=client,
        ):
            fingerprint, account = await async_acquire_site_manager(
                hass, "same-key", "entry-1", MagicMock()
            )
            await async_acquire_site_manager(hass, "same-key", "entry-2", MagicMock())
    finally:
        config_entries.current_entry.reset(token)

    assert account.coordinator.config_entry is None
    assert account.initial_refresh is not None
    await account.initial_refresh

    await async_release_site_manager(hass, fingerprint, "entry-1")
    await first_entry._async_process_on_unload(hass)

    assert not account.coordinator._shutdown_requested
    await async_release_site_manager(hass, fingerprint, "entry-2")
    assert account.coordinator._shutdown_requested


async def test_last_unload_cancels_pending_initial_refresh(
    hass: HomeAssistant,
) -> None:
    """Unloading the last entry cancels in-flight optional cloud polling."""
    started = asyncio.Event()

    async def wait_for_cancellation() -> None:
        started.set()
        await asyncio.Event().wait()

    client = _client()
    coordinator = MagicMock()
    coordinator.async_refresh = AsyncMock(side_effect=wait_for_cancellation)
    coordinator.async_shutdown = AsyncMock()
    with (
        patch(
            "custom_components.unifi_insights.coordinators.site_manager."
            "UniFiSiteManagerClient",
            return_value=client,
        ),
        patch(
            "custom_components.unifi_insights.coordinators.site_manager."
            "UnifiInsightsSiteManagerCoordinator",
            return_value=coordinator,
        ),
    ):
        fingerprint, account = await async_acquire_site_manager(
            hass, "key", "entry", MagicMock()
        )
        await started.wait()

        await async_release_site_manager(hass, fingerprint, "entry")

    assert account.initial_refresh is not None
    assert account.initial_refresh.cancelled()
    coordinator.async_shutdown.assert_awaited_once()
    client.close.assert_awaited_once()


def test_diagnostics_omit_cloud_identity_and_raw_metadata() -> None:
    """The exported Site Manager section contains only bounded, safe fields."""
    sample = {
        "hosts": {"host-secret": {"id": "host-secret", "ipAddress": "203.0.113.9"}},
        "sites": {"site-secret": {"siteId": "site-secret", "hostId": "host-secret"}},
        "devices": {"host-secret": {"devices": [{"id": "device-secret"}]}},
        "isp_metrics": {
            "host-secret": {
                "site-secret": {
                    "metric_time": datetime(2026, 1, 2, tzinfo=UTC).isoformat(),
                    "wan": {"avgLatency": 2, "ispName": "private ISP"},
                }
            }
        },
        "sd_wan_configs": {
            "config-secret": {
                "id": "config-secret",
                "name": "Private VPN",
                "type": "sdwan-hbsp",
            }
        },
        "collections": {
            "hosts": {"available": True, "updated_at": None, "error": None}
        },
        "last_attempt": None,
        "cooldown_until": None,
    }

    summary = _site_manager_summary(sample, "host-secret")
    rendered = repr(summary)
    assert summary["selected_host"]["site_count"] == 1
    assert summary["selected_host"]["isp_samples"][0]["wan"] == {"avgLatency": 2}
    for secret in (
        "host-secret",
        "site-secret",
        "device-secret",
        "config-secret",
        "Private VPN",
        "private ISP",
        "203.0.113.9",
    ):
        assert secret not in rendered


def test_diagnostics_skips_malformed_site_and_isp_records() -> None:
    """Unexpected cloud record shapes cannot leak or break diagnostics."""
    snapshot = {
        "hosts": {},
        "sites": {
            "not-a-site": None,
            "missing-host": {"hostId": None},
        },
        "devices": {},
        "isp_metrics": {
            "host": {
                "not-a-metric": None,
                "missing-time": {"metric_time": None, "wan": {}},
                "missing-wan": {
                    "metric_time": "2026-01-02T03:10:00Z",
                    "wan": None,
                },
                "bad-time": {"metric_time": "private invalid date", "wan": {}},
            }
        },
        "sd_wan_configs": {},
        "collections": {},
    }

    summary = _site_manager_summary(snapshot, "host")

    assert summary["inventory"]["sites"] == 2
    assert summary["selected_host"]["isp_samples"] == []
    assert "private invalid date" not in repr(summary)
