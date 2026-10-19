# Copyright 2026 UniFi Insights contributors
"""Historical site-level internet activity aggregation and fetching helpers."""

from __future__ import annotations

import inspect
import logging
from functools import partial
from typing import TYPE_CHECKING, Any

from unifi_official_api.network.models.report import SiteReportBucket

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

_LOGGER = logging.getLogger(__name__)

# Historical Internet Activity time windows (milliseconds)
WINDOW_1H_MS = 60 * 60 * 1000
WINDOW_1D_MS = 24 * WINDOW_1H_MS
WINDOW_1W_MS = 7 * WINDOW_1D_MS
WINDOW_1M_MS = 30 * WINDOW_1D_MS


def _sum_report_window(
    buckets: Sequence[SiteReportBucket | dict[str, Any] | object],
    cutoff_ms: int,
) -> dict[str, int] | None:
    """Sum download (RX) and upload (TX) bytes for buckets at or after ``cutoff_ms``."""
    rx_values: list[int] = []
    tx_values: list[int] = []

    for item in buckets:
        if isinstance(item, SiteReportBucket):
            bucket = item
        elif isinstance(item, dict):
            try:
                bucket = SiteReportBucket.model_validate(item)
            except Exception as err:
                _LOGGER.debug("Skipping invalid site report bucket: %s", err)
                continue
        else:
            continue

        if bucket.time < cutoff_ms:
            continue
        if bucket.rx_bytes is not None:
            rx_values.append(bucket.rx_bytes)
        if bucket.tx_bytes is not None:
            tx_values.append(bucket.tx_bytes)

    if not rx_values and not tx_values:
        return None

    window_totals: dict[str, int] = {}
    if rx_values:
        window_totals["rx_bytes"] = sum(rx_values)
    if tx_values:
        window_totals["tx_bytes"] = sum(tx_values)
    return window_totals


def aggregate_internet_activity_windows(
    five_minute_buckets: Sequence[SiteReportBucket | dict[str, Any]],
    hourly_buckets: Sequence[SiteReportBucket | dict[str, Any]],
    daily_buckets: Sequence[SiteReportBucket | dict[str, Any]],
    *,
    now_ms: int,
) -> dict[str, dict[str, int]]:
    """
    Aggregate site report buckets into ``1h``, ``1d``, ``1w``, and ``1m`` totals.

    - ``1h`` uses ``5minutes`` buckets with ``time >= now_ms - 1h``.
    - ``1d`` and ``1w`` reuse ``hourly`` buckets with ``1d`` and ``7d`` cutoffs.
    - ``1m`` uses ``daily`` buckets with ``time >= now_ms - 30d``.
    Windows without usable buckets are omitted rather than publishing zero.
    """
    windows: dict[str, dict[str, int]] = {}
    for window_key, source_buckets, duration_ms in (
        ("1h", five_minute_buckets, WINDOW_1H_MS),
        ("1d", hourly_buckets, WINDOW_1D_MS),
        ("1w", hourly_buckets, WINDOW_1W_MS),
        ("1m", daily_buckets, WINDOW_1M_MS),
    ):
        totals = _sum_report_window(source_buckets, now_ms - duration_ms)
        if totals is not None:
            windows[window_key] = totals
    return windows


async def async_call_site_report(
    network_client: Any,
    site_name: str,
    interval: str,
    *,
    start_ms: int,
    end_ms: int,
) -> list[Any]:
    """Invoke the Network client's legacy site report endpoint for one interval."""
    reports_obj = getattr(network_client, "reports", None)
    target_fn = getattr(reports_obj, "get_site_report", None)
    if not callable(target_fn):
        target_fn = getattr(network_client, "get_site_report", None)
    if not callable(target_fn):
        return []

    result = target_fn(
        site_name,
        interval,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    if inspect.isawaitable(result):
        result = await result
    return list(result) if isinstance(result, (list, tuple)) else []


async def async_fetch_site_internet_activity(
    fetch_optional_section: Callable[
        [str, str, Callable[[], Awaitable[list[Any]]]],
        Awaitable[list[Any] | None],
    ],
    call_site_report: Callable[..., Awaitable[list[Any]]],
    site_id: str,
    site_name: str,
    now_ms: int,
) -> dict[str, dict[str, int]] | None:
    """Fetch ``5minutes``, ``hourly``, and ``daily`` site reports and aggregate."""
    five_min_buckets = await fetch_optional_section(
        "internet_activity",
        site_id,
        partial(
            call_site_report,
            site_name,
            "5minutes",
            start_ms=now_ms - WINDOW_1H_MS,
            end_ms=now_ms,
        ),
    )
    if five_min_buckets is None:
        return None

    hourly_buckets = await fetch_optional_section(
        "internet_activity",
        site_id,
        partial(
            call_site_report,
            site_name,
            "hourly",
            start_ms=now_ms - WINDOW_1W_MS,
            end_ms=now_ms,
        ),
    )
    if hourly_buckets is None:
        return None

    daily_buckets = await fetch_optional_section(
        "internet_activity",
        site_id,
        partial(
            call_site_report,
            site_name,
            "daily",
            start_ms=now_ms - WINDOW_1M_MS,
            end_ms=now_ms,
        ),
    )
    if daily_buckets is None:
        return None

    return aggregate_internet_activity_windows(
        five_min_buckets,
        hourly_buckets,
        daily_buckets,
        now_ms=now_ms,
    )


def resolve_report_site_name(
    site_id: str,
    site_data: Any,
    legacy_name: str | None,
) -> str:
    """Resolve the classic site name used for ``/stat/report/*.site`` requests."""
    if legacy_name:
        return str(legacy_name)
    if isinstance(site_data, dict):
        internal_ref = site_data.get("internal_reference") or site_data.get(
            "internalReference"
        )
        if internal_ref:
            return str(internal_ref)
    return str(site_id)


async def async_update_site_internet_activity(
    fetch_site_internet_activity: Callable[
        ..., Awaitable[dict[str, dict[str, int]] | None]
    ],
    *,
    site_id: str,
    site_data: Any,
    legacy_name: str | None,
    now_ms: int,
    prior_activity: Any,
    internet_activity_by_site: dict[str, dict[str, dict[str, int]]],
    failed_sections: set[tuple[str, str]],
) -> None:
    """Refresh internet activity for one site and update coordinator state dicts."""
    report_site_name = resolve_report_site_name(site_id, site_data, legacy_name)
    site_activity = await fetch_site_internet_activity(
        site_id=site_id,
        site_name=report_site_name,
        now_ms=now_ms,
    )
    if site_activity is None:
        failed_sections.add(("internet_activity", site_id))
        if isinstance(prior_activity, dict) and prior_activity:
            internet_activity_by_site[site_id] = prior_activity
    elif site_activity:
        internet_activity_by_site[site_id] = site_activity
