# Copyright 2026 UniFi Insights contributors
"""Tests for coordinator config_sections internet activity and mapping helpers."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from unifi_official_api import UniFiAuthenticationError
from unifi_official_api.network.models.report import SiteReportBucket
from unifi_official_api.network.models.stats import StatsSeries, StatsSeriesBucket

from custom_components.unifi_insights.coordinators.config_sections import (
    METHOD_MISSING,
    SECTION_OUTCOME_FAILED,
    SECTION_OUTCOME_OK,
    SECTION_OUTCOME_UNSUPPORTED,
    WINDOW_1H_MS,
    InternetActivityFetchResult,
    aggregate_modern_internet_activity_windows,
    async_call_site_report,
    async_fetch_site_internet_activity,
    async_fetch_site_modern_internet_activity,
    async_update_site_internet_activity,
    map_legacy_stats_site_ids,
)


@pytest.mark.asyncio
async def test_async_call_site_report_normal_list() -> None:
    """Test async_call_site_report when reports endpoint returns a list."""
    client = MagicMock()
    client.reports = MagicMock()
    client.reports.get_site_report = AsyncMock(return_value=[{"time": 1000}])

    res = await async_call_site_report(
        client, "default", "hourly", start_ms=0, end_ms=1000
    )
    assert res == [{"time": 1000}]


@pytest.mark.asyncio
async def test_async_call_site_report_method_missing() -> None:
    """Test async_call_site_report when get_site_report is not callable."""
    client = MagicMock(spec=[])
    res = await async_call_site_report(
        client, "default", "hourly", start_ms=0, end_ms=1000
    )
    assert res is METHOD_MISSING


@pytest.mark.asyncio
async def test_async_call_site_report_non_list_shape() -> None:
    """Test async_call_site_report preserves unexpected return shapes."""
    client = MagicMock()
    client.reports.get_site_report = AsyncMock(return_value={"unexpected": "dict"})

    res = await async_call_site_report(
        client, "default", "hourly", start_ms=0, end_ms=1000
    )
    assert res == {"unexpected": "dict"}


@pytest.mark.asyncio
async def test_async_fetch_site_internet_activity_ok() -> None:
    """Test async_fetch_site_internet_activity with valid buckets returning ok."""
    now_ms = 1_000_000_000
    b5 = SiteReportBucket.model_validate(
        {"time": now_ms - 1000, "wan-rx_bytes": 100, "wan-tx_bytes": 200}
    )
    bh = SiteReportBucket.model_validate(
        {"time": now_ms - 5000, "wan-rx_bytes": 300, "wan-tx_bytes": 400}
    )
    bd = SiteReportBucket.model_validate(
        {"time": now_ms - 10000, "wan-rx_bytes": 500, "wan-tx_bytes": 600}
    )

    async def mock_fetch(
        section: str,
        site_id: str,
        fn: Any,
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> list[Any]:
        if outcomes is not None:
            outcomes[(section, site_id)] = SECTION_OUTCOME_OK
        return await fn()

    async def mock_report(
        site_name: str, interval: str, *, start_ms: int, end_ms: int
    ) -> list[Any]:
        if interval == "5minutes":
            return [b5]
        if interval == "hourly":
            return [bh]
        return [bd]

    res = await async_fetch_site_internet_activity(
        mock_fetch, mock_report, "site1", "default", now_ms
    )
    assert res.data is not None
    assert res.status["source"] == "legacy"
    assert res.status["reason"] == "ok"
    assert res.status["counts"]["5minutes"] == {
        "received": 1,
        "usable": 1,
        "skipped": 0,
    }
    assert res.status["counts"]["hourly"] == {"received": 1, "usable": 1, "skipped": 0}
    assert res.status["counts"]["daily"] == {"received": 1, "usable": 1, "skipped": 0}
    assert "1h" in res.data
    assert "1d" in res.data
    assert "1w" in res.data
    assert "1m" in res.data


@pytest.mark.asyncio
async def test_async_fetch_site_internet_activity_empty_response() -> None:
    """Test async_fetch_site_internet_activity when all intervals return empty lists."""
    now_ms = 1_000_000_000

    async def mock_fetch(
        section: str,
        site_id: str,
        fn: Any,
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> list[Any]:
        if outcomes is not None:
            outcomes[(section, site_id)] = SECTION_OUTCOME_OK
        return []

    async def mock_report(*args: Any, **kwargs: Any) -> list[Any]:
        return []

    res = await async_fetch_site_internet_activity(
        mock_fetch, mock_report, "site1", "default", now_ms
    )
    assert res.data == {}
    assert res.status["source"] == "legacy"
    assert res.status["reason"] == "empty_response"
    assert res.status["counts"]["5minutes"]["received"] == 0


@pytest.mark.asyncio
async def test_async_fetch_site_internet_activity_unsupported() -> None:
    """Test async_fetch_site_internet_activity when endpoints are unsupported."""
    now_ms = 1_000_000_000

    async def mock_fetch(
        section: str,
        site_id: str,
        fn: Any,
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> list[Any]:
        if outcomes is not None:
            outcomes[(section, site_id)] = SECTION_OUTCOME_UNSUPPORTED
        return []

    async def mock_report(*args: Any, **kwargs: Any) -> list[Any]:
        return []

    res = await async_fetch_site_internet_activity(
        mock_fetch, mock_report, "site1", "default", now_ms
    )
    assert res.data == {}
    assert res.status["source"] == "legacy"
    assert res.status["reason"] == "unsupported"


@pytest.mark.asyncio
async def test_async_fetch_site_internet_activity_method_missing() -> None:
    """Test async_fetch_site_internet_activity when report method is missing."""
    now_ms = 1_000_000_000

    async def mock_fetch(
        section: str,
        site_id: str,
        fn: Any,
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> Any:
        return METHOD_MISSING

    res = await async_fetch_site_internet_activity(
        mock_fetch, AsyncMock(), "site1", "default", now_ms
    )
    assert res.data is None
    assert res.status["source"] == "legacy"
    assert res.status["reason"] == "method_missing"


@pytest.mark.asyncio
async def test_async_fetch_site_internet_activity_unrecognised_shape() -> None:
    """Test async_fetch_site_internet_activity with malformed non-list response."""
    now_ms = 1_000_000_000

    async def mock_fetch(
        section: str,
        site_id: str,
        fn: Any,
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> Any:
        if outcomes is not None:
            outcomes[(section, site_id)] = SECTION_OUTCOME_OK
        return {"non_list": "shape"}

    res = await async_fetch_site_internet_activity(
        mock_fetch, AsyncMock(), "site1", "default", now_ms
    )
    assert res.data is None
    assert res.status["source"] == "legacy"
    assert res.status["reason"] == "unrecognised_shape"


@pytest.mark.asyncio
async def test_async_fetch_site_internet_activity_failed() -> None:
    """Test async_fetch_site_internet_activity on fetch failure."""
    now_ms = 1_000_000_000

    async def mock_fetch(
        section: str,
        site_id: str,
        fn: Any,
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> Any:
        if outcomes is not None:
            outcomes[(section, site_id)] = SECTION_OUTCOME_FAILED
        return None

    res = await async_fetch_site_internet_activity(
        mock_fetch, AsyncMock(), "site1", "default", now_ms
    )
    assert res.data is None
    assert res.status["source"] == "legacy"
    assert res.status["reason"] == "failed"


@pytest.mark.asyncio
async def test_async_fetch_site_internet_activity_no_usable_buckets() -> None:
    """Test async_fetch_site_internet_activity when received buckets
    are invalid/outside window."""
    now_ms = 10_000_000_000
    old_bucket = SiteReportBucket.model_validate(
        {"time": 1000, "wan-rx_bytes": 100, "wan-tx_bytes": 200}
    )

    async def mock_fetch(
        section: str,
        site_id: str,
        fn: Any,
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> list[Any]:
        if outcomes is not None:
            outcomes[(section, site_id)] = SECTION_OUTCOME_OK
        return [old_bucket]

    res = await async_fetch_site_internet_activity(
        mock_fetch, AsyncMock(), "site1", "default", now_ms
    )
    assert res.data == {}
    assert res.status["source"] == "legacy"
    assert res.status["reason"] == "no_usable_buckets"
    assert res.status["counts"]["5minutes"]["received"] == 1
    assert res.status["counts"]["5minutes"]["usable"] == 1
    assert res.status["counts"]["5minutes"]["skipped"] == 0


def test_map_legacy_stats_site_ids_matrix() -> None:
    """Test mapping integration IDs to classic stats _ids across edge cases."""
    # 1. Exact external_id match
    int_sites = [{"id": "int1"}, {"id": "int2"}]
    legacy_sites = [
        {"_id": "stats1", "external_id": "int1"},
        {"_id": "stats2", "external_id": "int2"},
    ]
    assert map_legacy_stats_site_ids(int_sites, legacy_sites) == {
        "int1": "stats1",
        "int2": "stats2",
    }

    # 2. Absent external_id, internalReference match
    int_sites = [{"id": "int1", "internalReference": "default"}]
    legacy_sites = [
        {"_id": "stats1", "name": "default", "external_id": None},
        {"_id": "stats2", "name": "site2"},
    ]
    assert map_legacy_stats_site_ids(int_sites, legacy_sites) == {"int1": "stats1"}

    # 3. Single-site fallback: 1 integration, 1 legacy
    int_sites = [{"id": "int_single"}]
    legacy_sites = [{"_id": "stats-single"}]
    assert map_legacy_stats_site_ids(int_sites, legacy_sites) == {
        "int_single": "stats-single"
    }

    # 4. Fallback rejected when full integration inventory has multiple sites
    int_sites = [{"id": "int1"}, {"id": "int2"}]
    legacy_sites = [{"_id": "stats1"}]
    assert map_legacy_stats_site_ids(int_sites, legacy_sites) == {}

    # 5. Duplicate external_id matches in legacy -> unresolved
    int_sites = [{"id": "int1"}]
    legacy_sites = [
        {"_id": "stats1", "external_id": "int1"},
        {"_id": "stats2", "external_id": "int1"},
    ]
    assert map_legacy_stats_site_ids(int_sites, legacy_sites) == {}

    # 6. Duplicate target _id -> unresolved for both
    int_sites = [{"id": "int1"}, {"id": "int2"}]
    legacy_sites = [
        {"_id": "stats-dup", "external_id": "int1"},
        {"_id": "stats-dup", "external_id": "int2"},
    ]
    assert map_legacy_stats_site_ids(int_sites, legacy_sites) == {}

    # 7. Invalid target _id rejected: the library accepts only
    # [0-9A-Za-z-]{1,64}, so anything else stays unresolved here.
    int_sites = [{"id": "int1"}]
    for bad_id in ("invalid id with spaces", "under_score", "a" * 65, "quote'd"):
        legacy_sites = [{"_id": bad_id, "external_id": "int1"}]
        assert map_legacy_stats_site_ids(int_sites, legacy_sites) == {}
    legacy_sites = [{"_id": "a" * 64, "external_id": "int1"}]
    assert map_legacy_stats_site_ids(int_sites, legacy_sites) == {"int1": "a" * 64}


@pytest.mark.parametrize(
    ("integration_sites", "legacy_sites"),
    [
        # The single-site fallback must not override a conflicting external_id.
        (
            [{"id": "int1"}],
            [{"_id": "stats-conflict", "external_id": "int_other"}],
        ),
        # An invalid _id must not be filtered out to expose a competing record.
        (
            [{"id": "int1"}],
            [
                {"_id": "invalid id", "external_id": "int1"},
                {"_id": "stats-valid", "external_id": "int1"},
            ],
        ),
        # A duplicate target _id is ambiguous even when only one is a candidate.
        (
            [{"id": "int1"}, {"id": "int2"}],
            [
                {"_id": "stats-shared", "external_id": "int1"},
                {"_id": "stats-shared", "external_id": "int_other"},
            ],
        ),
        # A single legacy record with no usable _id cannot be resolved.
        ([{"id": "int1"}], [{"external_id": None, "name": "default"}]),
    ],
)
def test_map_legacy_stats_site_ids_rejects_conflicts(
    integration_sites: list[dict[str, Any]],
    legacy_sites: list[dict[str, Any]],
) -> None:
    """Conflicts, invalid ids and duplicate targets leave every site unresolved."""
    assert map_legacy_stats_site_ids(integration_sites, legacy_sites) == {}


def test_map_legacy_stats_site_ids_single_site_fallback_guards() -> None:
    """The fallback resolves only an unconflicted one-to-one inventory."""
    assert map_legacy_stats_site_ids(
        [{"id": "int1"}], [{"_id": "stats1", "external_id": ""}]
    ) == {"int1": "stats1"}
    assert map_legacy_stats_site_ids(
        [{"id": "int1"}], [{"_id": "stats1", "external_id": "int1"}]
    ) == {"int1": "stats1"}
    # Two legacy records, one without external_id: no fallback.
    assert (
        map_legacy_stats_site_ids(
            [{"id": "int1"}],
            [{"_id": "stats1"}, {"_id": "stats2", "external_id": "int_other"}],
        )
        == {}
    )


def test_aggregate_modern_internet_activity_windows_boundaries_and_values() -> None:
    """Test modern window aggregation cutoffs, finite values, and rounding."""
    now_ms = 1_000_000_000

    # Bucket exactly at 1h cutoff (now - WINDOW_1H_MS)
    b_exact_1h = StatsSeriesBucket(
        timestamp=now_ms - WINDOW_1H_MS,
        duration=300000,
        rxBytes=10.4,
        txBytes=20.4,
    )
    # Bucket 1ms before 1h cutoff (should be excluded from 1h, included in 1d)
    b_before_1h = StatsSeriesBucket(
        timestamp=now_ms - WINDOW_1H_MS - 1,
        duration=300000,
        rxBytes=5.0,
        txBytes=5.0,
    )
    # Bucket exactly at now_ms
    b_now = StatsSeriesBucket(
        timestamp=now_ms,
        duration=270216,
        rxBytes=10.2,
        txBytes=0.0,
    )
    # Future bucket (should be excluded)
    b_future = StatsSeriesBucket(
        timestamp=now_ms + 1,
        duration=300000,
        rxBytes=999.0,
        txBytes=999.0,
    )

    day_series = [b_exact_1h, b_before_1h, b_now, b_future]
    res = aggregate_modern_internet_activity_windows(day_series, [], [], now_ms=now_ms)

    assert res["1h"] == {"rx_bytes": 21, "tx_bytes": 20}
    assert res["1d"] == {"rx_bytes": 26, "tx_bytes": 25}
    assert "1w" not in res
    assert "1m" not in res


def test_aggregate_modern_internet_activity_windows_zero_and_missing() -> None:
    """Test modern aggregation zero preservation and missing direction omission."""
    now_ms = 1_000_000_000

    b_rx_zero = StatsSeriesBucket(
        timestamp=now_ms - 1000,
        duration=300000,
        rxBytes=0.0,
        txBytes=None,
    )
    res = aggregate_modern_internet_activity_windows([b_rx_zero], [], [], now_ms=now_ms)
    assert res["1h"] == {"rx_bytes": 0}

    b_tx_only = StatsSeriesBucket(
        timestamp=now_ms - 1000,
        duration=300000,
        rxBytes=None,
        txBytes=7.0,
    )
    res_tx = aggregate_modern_internet_activity_windows(
        [b_tx_only], [], [], now_ms=now_ms
    )
    assert res_tx["1h"] == {"tx_bytes": 7}


@pytest.mark.asyncio
async def test_async_fetch_site_modern_internet_activity_ok() -> None:
    """Test modern fetch sequence requesting DAY, WEEK, MONTH."""
    now_ms = 1_000_000_000
    stats_site_id = "stats-uuid-1"

    b_day = StatsSeriesBucket(
        timestamp=now_ms - 1000, duration=300000, rxBytes=100.0, txBytes=100.0
    )
    b_week = StatsSeriesBucket(
        timestamp=now_ms - 50000, duration=3600000, rxBytes=200.0, txBytes=200.0
    )
    b_month = StatsSeriesBucket(
        timestamp=now_ms - 100000, duration=28800000, rxBytes=300.0, txBytes=300.0
    )

    client = MagicMock()
    client.stats = MagicMock()

    async def mock_get_recent_series(
        *, schema: str, site_id: str, period: str, end_ms: int, values: Any
    ) -> list[StatsSeries]:
        assert schema == "wanBySite"
        assert site_id == stats_site_id
        assert end_ms == now_ms
        assert values == ("rxBytes", "txBytes")
        if period == "DAY":
            return [StatsSeries(siteId=stats_site_id, series=[b_day])]
        if period == "WEEK":
            return [StatsSeries(siteId=stats_site_id, series=[b_week])]
        return [StatsSeries(siteId=stats_site_id, series=[b_month])]

    client.stats.get_recent_series = AsyncMock(side_effect=mock_get_recent_series)

    async def mock_fetch(
        section: str,
        site_id: str,
        fn: Any,
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> list[Any]:
        if outcomes is not None:
            outcomes[(section, site_id)] = SECTION_OUTCOME_OK
        return await fn()

    res = await async_fetch_site_modern_internet_activity(
        mock_fetch, client, "int_site1", stats_site_id, now_ms
    )
    assert res.data is not None
    assert res.status["source"] == "modern"
    assert res.status["reason"] == "ok"
    assert res.status["counts"]["DAY"] == {"received": 1, "usable": 1, "skipped": 0}
    assert res.status["counts"]["WEEK"] == {"received": 1, "usable": 1, "skipped": 0}
    assert res.status["counts"]["MONTH"] == {"received": 1, "usable": 1, "skipped": 0}
    assert res.data["1h"] == {"rx_bytes": 100, "tx_bytes": 100}
    assert res.data["1w"] == {"rx_bytes": 200, "tx_bytes": 200}
    assert res.data["1m"] == {"rx_bytes": 300, "tx_bytes": 300}


@pytest.mark.asyncio
async def test_async_fetch_site_modern_internet_activity_isolation() -> None:
    """Test that another site's series in the response never enters sums."""
    now_ms = 1_000_000_000
    stats_site_id = "stats-uuid-1"

    b_site1 = StatsSeriesBucket(
        timestamp=now_ms - 1000, duration=300000, rxBytes=100.0, txBytes=100.0
    )
    b_other = StatsSeriesBucket(
        timestamp=now_ms - 1000, duration=300000, rxBytes=9999.0, txBytes=9999.0
    )

    client = MagicMock()
    client.stats = MagicMock()
    client.stats.get_recent_series = AsyncMock(
        return_value=[
            StatsSeries(siteId="other_site", series=[b_other]),
            StatsSeries(siteId=stats_site_id, series=[b_site1]),
        ]
    )

    async def mock_fetch(
        section: str,
        site_id: str,
        fn: Any,
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> list[Any]:
        if outcomes is not None:
            outcomes[(section, site_id)] = SECTION_OUTCOME_OK
        return await fn()

    res = await async_fetch_site_modern_internet_activity(
        mock_fetch, client, "int_site1", stats_site_id, now_ms
    )
    assert res.data is not None
    assert res.data["1h"] == {"rx_bytes": 100, "tx_bytes": 100}


@pytest.mark.asyncio
async def test_fetch_site_modern_activity_duplicate_series_rejected() -> None:
    """Test that duplicate matching series groups are rejected as unrecognised_shape."""
    now_ms = 1_000_000_000
    stats_site_id = "stats-uuid-1"
    b = StatsSeriesBucket(
        timestamp=now_ms - 1000, duration=300000, rxBytes=100.0, txBytes=100.0
    )

    client = MagicMock()
    client.stats = MagicMock()
    client.stats.get_recent_series = AsyncMock(
        return_value=[
            StatsSeries(siteId=stats_site_id, series=[b]),
            StatsSeries(siteId=stats_site_id, series=[b]),
        ]
    )

    async def mock_fetch(
        section: str,
        site_id: str,
        fn: Any,
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> list[Any]:
        if outcomes is not None:
            outcomes[(section, site_id)] = SECTION_OUTCOME_OK
        return await fn()

    res = await async_fetch_site_modern_internet_activity(
        mock_fetch, client, "int_site1", stats_site_id, now_ms
    )
    assert res.data is None
    assert res.status["reason"] == "unrecognised_shape"


@pytest.mark.asyncio
async def test_fetch_site_modern_activity_duplicate_timestamps_rejected() -> None:
    """Test that duplicate timestamps in a series are rejected as unrecognised_shape."""
    now_ms = 1_000_000_000
    stats_site_id = "stats-uuid-1"
    b1 = StatsSeriesBucket(
        timestamp=now_ms - 1000, duration=300000, rxBytes=100.0, txBytes=100.0
    )
    b2 = StatsSeriesBucket(
        timestamp=now_ms - 1000, duration=300000, rxBytes=200.0, txBytes=200.0
    )

    client = MagicMock()
    client.stats = MagicMock()
    client.stats.get_recent_series = AsyncMock(
        return_value=[StatsSeries(siteId=stats_site_id, series=[b1, b2])]
    )

    async def mock_fetch(
        section: str,
        site_id: str,
        fn: Any,
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> list[Any]:
        if outcomes is not None:
            outcomes[(section, site_id)] = SECTION_OUTCOME_OK
        return await fn()

    res = await async_fetch_site_modern_internet_activity(
        mock_fetch, client, "int_site1", stats_site_id, now_ms
    )
    assert res.data is None
    assert res.status["reason"] == "unrecognised_shape"


@pytest.mark.asyncio
async def test_async_fetch_site_modern_internet_activity_permission_denied() -> None:
    """Test that 403 on stats series returns permission_denied with data=None."""
    now_ms = 1_000_000_000
    stats_site_id = "stats-uuid-1"

    client = MagicMock()
    client.stats = MagicMock()
    client.stats.get_recent_series = AsyncMock(
        side_effect=UniFiAuthenticationError("Forbidden", status_code=403)
    )

    async def mock_fetch(
        section: str,
        site_id: str,
        fn: Any,
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> Any:
        # Mirror ``_fetch_optional_section``: an optional-feature 403 is
        # classified as unsupported, not re-raised.
        try:
            return await fn()
        except UniFiAuthenticationError:
            if outcomes is not None:
                outcomes[(section, site_id)] = SECTION_OUTCOME_UNSUPPORTED
            return []

    res = await async_fetch_site_modern_internet_activity(
        mock_fetch, client, "int_site1", stats_site_id, now_ms
    )
    assert res.data is None
    assert res.status["reason"] == "permission_denied"


@pytest.mark.asyncio
async def test_async_update_site_internet_activity_integration() -> None:
    """Test async_update_site_internet_activity updating data dicts and status."""
    now_ms = 1_000_000_000
    site_id = "site1"
    internet_activity_by_site: dict[str, dict[str, dict[str, int]]] = {}
    internet_activity_status: dict[str, dict[str, Any]] = {}
    failed_sections: set[tuple[str, str]] = set()

    # Success case
    async def mock_fetch_success(**kwargs: Any) -> Any:

        return InternetActivityFetchResult(
            data={"1h": {"rx_bytes": 100, "tx_bytes": 200}},
            status={"source": "modern", "reason": "ok", "counts": {}},
        )

    await async_update_site_internet_activity(
        mock_fetch_success,
        site_id=site_id,
        site_data={},
        legacy_name="default",
        now_ms=now_ms,
        prior_activity=None,
        internet_activity_by_site=internet_activity_by_site,
        failed_sections=failed_sections,
        internet_activity_status=internet_activity_status,
        stats_site_id="uuid-1",
        source="modern",
    )
    assert internet_activity_by_site[site_id] == {
        "1h": {"rx_bytes": 100, "tx_bytes": 200}
    }
    assert internet_activity_status[site_id]["reason"] == "ok"
    assert ("internet_activity", site_id) not in failed_sections

    # Failure case retains prior data
    async def mock_fetch_fail(**kwargs: Any) -> Any:

        return InternetActivityFetchResult(
            data=None,
            status={"source": "modern", "reason": "failed", "counts": {}},
        )

    await async_update_site_internet_activity(
        mock_fetch_fail,
        site_id=site_id,
        site_data={},
        legacy_name="default",
        now_ms=now_ms,
        prior_activity=internet_activity_by_site[site_id],
        internet_activity_by_site=internet_activity_by_site,
        failed_sections=failed_sections,
        internet_activity_status=internet_activity_status,
        stats_site_id="uuid-1",
        source="modern",
    )
    # Prior data retained
    assert internet_activity_by_site[site_id] == {
        "1h": {"rx_bytes": 100, "tx_bytes": 200}
    }
    assert internet_activity_status[site_id]["reason"] == "failed"
    assert ("internet_activity", site_id) in failed_sections


@pytest.mark.asyncio
async def test_legacy_invalid_bucket_log_hides_input_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unparseable report bucket is logged without its rejected value."""
    now_ms = 1_000_000_000
    sentinel = "sentinel-report-value"

    async def mock_fetch(
        section: str,
        site_id: str,
        fn: Any,
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> list[Any]:
        if outcomes is not None:
            outcomes[(section, site_id)] = SECTION_OUTCOME_OK
        return [{"time": sentinel}, "not-a-bucket"]

    with caplog.at_level(logging.DEBUG):
        res = await async_fetch_site_internet_activity(
            mock_fetch, AsyncMock(), "site1", "default", now_ms
        )

    assert sentinel not in caplog.text
    assert res.status["counts"]["5minutes"] == {
        "received": 2,
        "usable": 0,
        "skipped": 2,
    }


def _modern_ok_fetch(*, fail_at: int | None = None) -> tuple[Any, list[str]]:
    """Return a fake optional-section fetch over a real modern series, and its calls."""
    calls: list[str] = []

    async def mock_fetch(
        section: str,
        site_id: str,
        fn: Any,
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> Any:
        index = len(calls)
        calls.append(section)
        if outcomes is not None:
            outcomes[(section, site_id)] = (
                SECTION_OUTCOME_FAILED if fail_at == index else SECTION_OUTCOME_OK
            )
        if fail_at == index:
            return None
        return await fn()

    return mock_fetch, calls


@pytest.mark.asyncio
async def test_modern_unsupported_all_periods_clears_history() -> None:
    """Every period reporting unsupported returns empty data, not a failure."""
    now_ms = 1_000_000_000

    async def mock_fetch(
        section: str,
        site_id: str,
        fn: Any,
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> list[Any]:
        if outcomes is not None:
            outcomes[(section, site_id)] = SECTION_OUTCOME_UNSUPPORTED
        return []

    client = MagicMock()
    client.stats.get_recent_series = AsyncMock(return_value=[])

    res = await async_fetch_site_modern_internet_activity(
        mock_fetch, client, "int_site1", "stats-uuid-1", now_ms
    )
    assert res.data == {}
    assert res.status["source"] == "modern"
    assert res.status["reason"] == "unsupported"
    assert set(res.status["counts"]) == {"DAY", "WEEK", "MONTH"}


@pytest.mark.parametrize(
    ("fail_at", "failed_period"),
    [(0, "DAY"), (1, "WEEK"), (2, "MONTH")],
)
@pytest.mark.asyncio
async def test_modern_failure_at_each_period_retains_prior_windows(
    fail_at: int, failed_period: str
) -> None:
    """A transient failure stops the sequence and keeps the whole site's totals."""
    now_ms = 1_000_000_000
    stats_site_id = "stats-uuid-1"
    bucket = StatsSeriesBucket(
        timestamp=now_ms - 1000, duration=300000, rxBytes=10.0, txBytes=20.0
    )
    client = MagicMock()
    client.stats.get_recent_series = AsyncMock(
        return_value=[StatsSeries(siteId=stats_site_id, series=[bucket])]
    )
    mock_fetch, calls = _modern_ok_fetch(fail_at=fail_at)

    res = await async_fetch_site_modern_internet_activity(
        mock_fetch, client, "int_site1", stats_site_id, now_ms
    )

    assert res.data is None
    assert res.status["reason"] == "failed"
    assert res.status["counts"][failed_period] == {
        "received": 0,
        "usable": 0,
        "skipped": 0,
    }
    # No request after the failure is made.
    assert len(calls) == fail_at + 1

    prior = {"1h": {"rx_bytes": 7, "tx_bytes": 8}}
    activity: dict[str, dict[str, dict[str, int]]] = {}
    failed: set[tuple[str, str]] = set()
    await async_update_site_internet_activity(
        AsyncMock(return_value=res),
        site_id="int_site1",
        site_data={},
        legacy_name=None,
        now_ms=now_ms,
        prior_activity=prior,
        internet_activity_by_site=activity,
        failed_sections=failed,
        stats_site_id=stats_site_id,
        source="modern",
    )
    assert activity["int_site1"] == prior
    assert ("internet_activity", "int_site1") in failed


@pytest.mark.asyncio
async def test_modern_empty_history_is_a_healthy_empty_response() -> None:
    """An empty series list for every period is empty history, not a failure."""
    now_ms = 1_000_000_000
    client = MagicMock()
    client.stats.get_recent_series = AsyncMock(return_value=[])
    mock_fetch, _calls = _modern_ok_fetch()

    res = await async_fetch_site_modern_internet_activity(
        mock_fetch, client, "int_site1", "stats-uuid-1", now_ms
    )
    assert res.data == {}
    assert res.status["reason"] == "empty_response"
    assert res.status["counts"]["DAY"] == {"received": 0, "usable": 0, "skipped": 0}


@pytest.mark.asyncio
async def test_modern_no_usable_directions_omits_windows() -> None:
    """Buckets without a usable direction count as skipped and publish nothing."""
    now_ms = 1_000_000_000
    stats_site_id = "stats-uuid-1"
    bucket = StatsSeriesBucket(
        timestamp=now_ms - 1000, duration=300000, rxBytes=None, txBytes=None
    )
    client = MagicMock()
    client.stats.get_recent_series = AsyncMock(
        return_value=[StatsSeries(siteId=stats_site_id, series=[bucket])]
    )
    mock_fetch, _calls = _modern_ok_fetch()

    res = await async_fetch_site_modern_internet_activity(
        mock_fetch, client, "int_site1", stats_site_id, now_ms
    )
    assert res.data == {}
    assert res.status["reason"] == "no_usable_buckets"
    assert res.status["counts"]["DAY"] == {"received": 1, "usable": 0, "skipped": 1}


@pytest.mark.asyncio
async def test_async_call_site_report_sync_result() -> None:
    """A synchronous report method's list result is returned as a list."""
    client = MagicMock()
    client.reports.get_site_report = MagicMock(return_value=({"time": 1},))

    res = await async_call_site_report(
        client, "default", "hourly", start_ms=0, end_ms=1000
    )
    assert res == [{"time": 1}]


def test_map_legacy_stats_site_ids_inventory_shapes() -> None:
    """Object sites, id-less sites and non-mapping legacy records stay unresolved."""
    object_site = SimpleNamespace(id="int1", internalReference="default")
    assert map_legacy_stats_site_ids(
        [object_site], [{"_id": "stats1", "name": "default"}]
    ) == {"int1": "stats1"}
    assert map_legacy_stats_site_ids([{"name": "no-id"}], [{"_id": "stats1"}]) == {}
    assert map_legacy_stats_site_ids([{"id": "int1"}], ["not-a-record"]) == {}


def test_map_legacy_stats_site_ids_invalid_targets_do_not_hide_other_sites() -> None:
    """An invalid target for one site leaves the other site's match intact."""
    assert map_legacy_stats_site_ids(
        [{"id": "int1"}, {"id": "int2"}],
        [
            {"_id": "invalid id", "external_id": "int1"},
            {"_id": "stats2", "external_id": "int2"},
        ],
    ) == {"int2": "stats2"}
    assert map_legacy_stats_site_ids(
        [{"id": "int1", "internalReference": "default"}, {"id": "int2"}],
        [
            {"_id": "invalid id", "name": "default"},
            {"_id": "stats2", "external_id": "int2"},
        ],
    ) == {"int2": "stats2"}


@pytest.mark.asyncio
async def test_modern_401_is_not_swallowed() -> None:
    """A 401 from a series request propagates so the refresh can reauth."""
    client = MagicMock()
    client.stats.get_recent_series = AsyncMock(
        side_effect=UniFiAuthenticationError("Unauthorized", status_code=401)
    )

    async def mock_fetch(
        section: str,
        site_id: str,
        fn: Any,
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> Any:
        return await fn()

    with pytest.raises(UniFiAuthenticationError):
        await async_fetch_site_modern_internet_activity(
            mock_fetch, client, "int_site1", "stats-uuid-1", 1_000_000_000
        )


@pytest.mark.asyncio
async def test_legacy_401_is_not_swallowed() -> None:
    """A 401 from a legacy report request propagates so the refresh can reauth."""

    async def mock_fetch(
        section: str,
        site_id: str,
        fn: Any,
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> Any:
        msg = "Unauthorized"
        raise UniFiAuthenticationError(msg, status_code=401)

    with pytest.raises(UniFiAuthenticationError):
        await async_fetch_site_internet_activity(
            mock_fetch, AsyncMock(), "site1", "default", 1_000_000_000
        )
