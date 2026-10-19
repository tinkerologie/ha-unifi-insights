# Copyright 2026 UniFi Insights contributors
"""Tests for voucher_state module."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from unifi_official_api.network.models.voucher import Voucher

from custom_components.unifi_insights.coordinators.voucher_state import (
    DATA_LIMIT_MBYTES_RANGE,
    GUEST_LIMIT_UI_MAX,
    TIME_LIMIT_MINUTES_RANGE,
    VOUCHER_INPUTS,
    VoucherSettings,
    cast_input,
    count_active_vouchers,
    is_voucher_active,
    latest_voucher_qr_payload,
    parse_timestamp,
    refresh_latest_voucher,
    restore_input,
    voucher_request_kwargs,
    voucher_site_ids,
    voucher_to_record,
)


def test_voucher_settings_defaults() -> None:
    """VoucherSettings has expected default values."""
    settings = VoucherSettings()
    assert settings.duration_minutes == 480
    assert settings.guest_limit == 1
    assert settings.download_limit_mbps == 0.0
    assert settings.upload_limit_mbps == 0.0
    assert settings.data_limit_mb == 0


def test_voucher_inputs_match_spec_bounds() -> None:
    """VOUCHER_INPUTS constants match spec tables and UI caps."""
    assert VOUCHER_INPUTS["voucher_duration"].minimum == TIME_LIMIT_MINUTES_RANGE[0]
    assert VOUCHER_INPUTS["voucher_duration"].maximum == TIME_LIMIT_MINUTES_RANGE[1]
    assert VOUCHER_INPUTS["voucher_duration"].step == 1
    assert VOUCHER_INPUTS["voucher_duration"].default == 480
    assert VOUCHER_INPUTS["voucher_duration"].field == "duration_minutes"

    assert VOUCHER_INPUTS["voucher_guest_limit"].minimum == 0
    assert VOUCHER_INPUTS["voucher_guest_limit"].maximum == GUEST_LIMIT_UI_MAX
    assert VOUCHER_INPUTS["voucher_guest_limit"].step == 1
    assert VOUCHER_INPUTS["voucher_guest_limit"].default == 1
    assert VOUCHER_INPUTS["voucher_guest_limit"].field == "guest_limit"

    assert VOUCHER_INPUTS["voucher_download_limit"].minimum == 0
    assert VOUCHER_INPUTS["voucher_download_limit"].maximum == 100
    assert VOUCHER_INPUTS["voucher_download_limit"].step == 0.1
    assert VOUCHER_INPUTS["voucher_download_limit"].default == 0
    assert VOUCHER_INPUTS["voucher_download_limit"].field == "download_limit_mbps"

    assert VOUCHER_INPUTS["voucher_upload_limit"].minimum == 0
    assert VOUCHER_INPUTS["voucher_upload_limit"].maximum == 100
    assert VOUCHER_INPUTS["voucher_upload_limit"].step == 0.1
    assert VOUCHER_INPUTS["voucher_upload_limit"].default == 0
    assert VOUCHER_INPUTS["voucher_upload_limit"].field == "upload_limit_mbps"

    assert VOUCHER_INPUTS["voucher_data_limit"].minimum == 0
    assert VOUCHER_INPUTS["voucher_data_limit"].maximum == DATA_LIMIT_MBYTES_RANGE[1]
    assert VOUCHER_INPUTS["voucher_data_limit"].step == 1
    assert VOUCHER_INPUTS["voucher_data_limit"].default == 0
    assert VOUCHER_INPUTS["voucher_data_limit"].field == "data_limit_mb"


def test_cast_input_rounds_integer_and_mbps_fields() -> None:
    """cast_input rounds step 1 fields to int and mbps fields to float."""
    assert cast_input("voucher_duration", 480.4) == 480
    assert cast_input("voucher_guest_limit", 2.8) == 3
    assert cast_input("voucher_download_limit", 1.23456) == 1.235
    assert cast_input("voucher_upload_limit", 5.0) == 5.0


def test_restore_input_accepts_in_range_values() -> None:
    """restore_input accepts valid numeric values within range."""
    assert restore_input("voucher_duration", 60) == 60
    assert restore_input("voucher_guest_limit", 5.0) == 5
    assert restore_input("voucher_download_limit", 12.5) == 12.5


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("voucher_duration", None),
        ("voucher_duration", True),
        ("voucher_duration", "5"),
        ("voucher_duration", float("nan")),
        ("voucher_duration", float("inf")),
        ("voucher_duration", -1),
        ("voucher_duration", 0),  # min is 1
        ("voucher_duration", 1_000_001),  # max is 1_000_000
        ("voucher_download_limit", -0.1),
        ("voucher_download_limit", 101),  # max is 100
        ("unknown_key", 10),
    ],
)
def test_restore_input_rejects_unusable_values(key: str, value: object) -> None:
    """restore_input rejects invalid types, NaN, inf, and out of bounds values."""
    assert restore_input(key, value) is None


def test_voucher_request_kwargs_defaults() -> None:
    """Default VoucherSettings produces duration and guest limit only."""
    settings = VoucherSettings()
    assert voucher_request_kwargs(settings) == {
        "time_limit_minutes": 480,
        "authorized_guest_limit": 1,
    }


def test_voucher_request_kwargs_omits_zero_limits() -> None:
    """Zero limits are omitted from request kwargs."""
    settings = VoucherSettings(
        duration_minutes=120,
        guest_limit=0,
        download_limit_mbps=0,
        upload_limit_mbps=0,
        data_limit_mb=0,
    )
    assert voucher_request_kwargs(settings) == {"time_limit_minutes": 120}


def test_voucher_request_kwargs_converts_mbps_to_kbps() -> None:
    """Mbps values are converted to kbps with correct keys."""
    settings = VoucherSettings(
        duration_minutes=60,
        guest_limit=2,
        download_limit_mbps=1.5,
        upload_limit_mbps=0.1,
        data_limit_mb=500,
    )
    kwargs = voucher_request_kwargs(settings)
    assert kwargs["rx_rate_limit_kbps"] == 1500
    assert kwargs["tx_rate_limit_kbps"] == 100
    assert kwargs["data_usage_limit_mbytes"] == 500
    assert kwargs["authorized_guest_limit"] == 2


def test_voucher_request_kwargs_clamps_into_spec_range() -> None:
    """Values are clamped into official spec ranges."""
    settings = VoucherSettings(
        duration_minutes=2_000_000,  # max 1_000_000
        guest_limit=0.4,  # min 1
        download_limit_mbps=0.0001,  # min 2 kbps
        upload_limit_mbps=100.0,  # max 100_000 kbps
        data_limit_mb=2_000_000,  # max 1_048_576
    )
    kwargs = voucher_request_kwargs(settings)
    assert kwargs["time_limit_minutes"] == 1_000_000
    assert kwargs["authorized_guest_limit"] == 1
    assert kwargs["rx_rate_limit_kbps"] == 2
    assert kwargs["tx_rate_limit_kbps"] == 100_000
    assert kwargs["data_usage_limit_mbytes"] == 1_048_576


def test_voucher_to_record_variants() -> None:
    """voucher_to_record converts Voucher and dicts, rejecting invalid records."""
    voucher = Voucher.model_validate(
        {"id": "v-1", "code": "1234567890", "name": "Test"}
    )
    rec = voucher_to_record(voucher)
    assert isinstance(rec, dict)
    assert rec["id"] == "v-1"
    assert rec["code"] == "1234567890"

    # dict
    dict_voucher = {"id": "v-2", "code": "0987654321"}
    rec2 = voucher_to_record(dict_voucher)
    assert rec2 == dict_voucher

    # MagicMock -> None
    assert voucher_to_record(MagicMock()) is None

    # model_dump raising -> None
    failing = MagicMock()
    failing.model_dump.side_effect = ValueError("cannot dump")
    assert voucher_to_record(failing) is None

    # Missing id
    assert voucher_to_record({"code": "123"}) is None
    assert voucher_to_record({"id": ""}) is None

    # No model_dump
    assert voucher_to_record(123) is None
    assert voucher_to_record(None) is None


def test_parse_timestamp_variants() -> None:
    """parse_timestamp handles aware, naive, ISO strings, bad strings, None, int."""
    utc_dt = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    assert parse_timestamp(utc_dt) == utc_dt

    naive_dt = datetime(2026, 10, 9, 12, 0)  # noqa: DTZ001
    parsed_naive = parse_timestamp(naive_dt)
    assert parsed_naive is not None
    assert parsed_naive.tzinfo == UTC

    iso_str = "2026-10-09T12:00:00Z"
    assert parse_timestamp(iso_str) == utc_dt

    assert parse_timestamp("invalid-date") is None
    assert parse_timestamp(None) is None
    assert parse_timestamp(12345678) is None


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"id": "v1", "expired": True}, False),
        ({"id": "v1", "expiresAt": "2026-10-09T11:59:59Z"}, False),
        ({"id": "v1", "expiresAt": "2026-10-09T12:00:01Z"}, True),
        ({"id": "v1"}, True),
        ({"id": "v1", "authorizedGuestLimit": 2, "authorizedGuestCount": 2}, False),
        ({"id": "v1", "authorizedGuestLimit": 2, "authorizedGuestCount": 1}, True),
        ({"id": "v1", "authorizedGuestLimit": None, "authorizedGuestCount": 5}, True),
        ({"id": "v1", "authorizedGuestLimit": 1}, True),  # count missing defaults 0
        ({"id": "v1", "expires_at": "2026-10-09T11:59:59Z"}, False),
        ({"id": "v1", "authorized_guest_limit": 1, "authorized_guest_count": 1}, False),
    ],
)
def test_is_voucher_active_rules(record: dict, expected: bool) -> None:  # noqa: FBT001
    """is_voucher_active correctly checks expiration and guest limit bounds."""
    now = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)
    assert is_voucher_active(record, now) is expected


def test_count_active_vouchers_ignores_malformed_entries() -> None:
    """count_active_vouchers tolerates non-mapping and malformed records."""
    now = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)
    assert count_active_vouchers(None, now) == 0
    assert count_active_vouchers("string", now) == 0

    inventory = {
        "v1": {"id": "v1", "expired": False},
        "v2": {"id": "v2", "expired": True},
        "v3": "not-a-dict",
        "v4": None,
    }
    assert count_active_vouchers(inventory, now) == 1


def test_latest_voucher_qr_payload_variants() -> None:
    """latest_voucher_qr_payload extracts code when not expired."""
    assert latest_voucher_qr_payload({"code": "4861409510"}) == "4861409510"
    assert latest_voucher_qr_payload({"code": ""}) is None
    assert latest_voucher_qr_payload({"code": 12345}) is None
    assert latest_voucher_qr_payload({"code": "4861409510", "expired": True}) is None
    assert latest_voucher_qr_payload(None) is None
    assert latest_voucher_qr_payload("not-a-record") is None


def test_refresh_latest_voucher_prefers_inventory_copy() -> None:
    """refresh_latest_voucher updates record from inventory if present."""
    latest = {"id": "v1", "expired": False}
    inventory = {
        "v1": {"id": "v1", "expired": True, "activatedAt": "2026-10-09T12:00:00Z"}
    }
    refreshed = refresh_latest_voucher(latest, inventory)
    assert refreshed["expired"] is True
    assert refreshed["activatedAt"] == "2026-10-09T12:00:00Z"


def test_refresh_latest_voucher_keeps_record_when_absent() -> None:
    """refresh_latest_voucher keeps existing record if missing from inventory."""
    latest = {"id": "v1", "expired": False}
    inventory = {"v2": {"id": "v2"}}
    assert refresh_latest_voucher(latest, inventory) == latest
    assert refresh_latest_voucher(latest, {}) == latest


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"sites": {"s1": {}}, "vouchers": {"s1": {}}}, ["s1"]),
        ({"sites": {"s1": {}, "s2": {}}, "vouchers": {}}, ["s1", "s2"]),
        ({"sites": {"s1": {}}}, []),  # no vouchers
        ({"vouchers": {"s1": {}}}, []),  # no sites
        (None, []),
        ("data", []),
        ({"sites": None, "vouchers": {}}, []),
    ],
)
def test_voucher_site_ids_requires_both_sections(
    data: object, expected: list[str]
) -> None:
    """voucher_site_ids requires both sites and vouchers mapping sections."""
    assert voucher_site_ids(data) == expected


def test_voucher_request_kwargs_clamps_rate_limits_both_directions() -> None:
    """Rate limits clamp below minimum and above maximum in both directions."""
    # Both above maximum
    settings_high = VoucherSettings(
        download_limit_mbps=200.0,
        upload_limit_mbps=200.0,
    )
    kwargs_high = voucher_request_kwargs(settings_high)
    assert kwargs_high["rx_rate_limit_kbps"] == 100_000
    assert kwargs_high["tx_rate_limit_kbps"] == 100_000

    # Both below minimum
    settings_low = VoucherSettings(
        download_limit_mbps=0.0001,
        upload_limit_mbps=0.0001,
    )
    kwargs_low = voucher_request_kwargs(settings_low)
    assert kwargs_low["rx_rate_limit_kbps"] == 2
    assert kwargs_low["tx_rate_limit_kbps"] == 2


def test_latest_voucher_qr_payload_expires_at(freezer: Any) -> None:
    """latest_voucher_qr_payload rejects records at or past expiresAt deadline."""
    freezer.move_to("2026-10-09T12:00:00Z")
    # Expired by timestamp in past
    past_record = {
        "id": "v1",
        "code": "1234567890",
        "expired": False,
        "expiresAt": "2026-10-09T11:59:59Z",
    }
    assert latest_voucher_qr_payload(past_record) is None

    # Exactly at deadline
    at_record = {
        "id": "v1",
        "code": "1234567890",
        "expired": False,
        "expiresAt": "2026-10-09T12:00:00Z",
    }
    assert latest_voucher_qr_payload(at_record) is None

    # Unparseable deadline is ignored and the code is still returned
    invalid_record = {
        "id": "v1",
        "code": "1234567890",
        "expired": False,
        "expiresAt": "not-a-timestamp",
    }
    assert latest_voucher_qr_payload(invalid_record) == "1234567890"

    # Future deadline
    future_record = {
        "id": "v1",
        "code": "1234567890",
        "expired": False,
        "expiresAt": "2026-10-09T12:00:01Z",
    }
    assert latest_voucher_qr_payload(future_record) == "1234567890"


def test_parse_timestamp_branches() -> None:
    """Test parse_timestamp with naive and invalid dates."""
    assert parse_timestamp("not-a-date") is None
    naive = parse_timestamp("2026-10-09T12:00:00")
    assert naive is not None
    assert naive.tzinfo == UTC


def test_latest_voucher_qr_payload_now_none() -> None:
    """Test latest_voucher_qr_payload when now is None."""
    voucher = {
        "id": "v1",
        "code": "1234567890",
        "expired": False,
        "expiresAt": "2099-01-01T00:00:00Z",
    }
    assert latest_voucher_qr_payload(voucher, now=None) == "1234567890"


def test_refresh_latest_voucher_preserves_attributes() -> None:
    """Test refresh_latest_voucher preserves timestamps and monotonic count."""
    old_record = {
        "id": "v1",
        "code": "1234567890",
        "expired": False,
        "activatedAt": "2026-10-09T10:00:00Z",
        "expiresAt": "2026-10-09T18:00:00Z",
        "authorizedGuestCount": 5,
    }
    inventory = {
        "v1": {
            "id": "v1",
            "code": "1234567890",
            "expired": False,
            "authorizedGuestCount": 3,
        }
    }
    refreshed = refresh_latest_voucher(old_record, inventory)
    assert refreshed is not None
    assert refreshed["activatedAt"] == "2026-10-09T10:00:00Z"
    assert refreshed["expiresAt"] == "2026-10-09T18:00:00Z"
    assert refreshed["authorizedGuestCount"] == 5

    # Non-mapping inventory or empty voucher id
    assert refresh_latest_voucher({"code": "x"}, None) == {"code": "x"}


@pytest.mark.parametrize("shape", ["null", "missing", "snake"])
def test_real_model_omission_preserves_deadline(shape: str) -> None:
    """Real dumps retain known timestamps when a refresh supplies unknown ones."""
    known = Voucher.model_validate(
        {
            "id": "v1",
            "code": "1234567890",
            "activatedAt": "2026-10-09T11:00:00Z",
            "expiresAt": "2026-10-09T12:00:00Z",
        }
    )
    old = known.model_dump(by_alias=True, exclude_none=False)
    omitted = Voucher.model_validate({"id": "v1", "code": "1234567890"}).model_dump(
        by_alias=True, exclude_none=False
    )
    if shape == "missing":
        omitted.pop("activatedAt")
        omitted.pop("expiresAt")
    elif shape == "snake":
        old["activated_at"] = old.pop("activatedAt")
        old["expires_at"] = old.pop("expiresAt")
    refreshed = refresh_latest_voucher(old, {"v1": omitted})
    assert refreshed["activatedAt"] == known.activated_at
    assert refreshed["expiresAt"] == known.expires_at
    replacement = Voucher.model_validate(
        {
            "id": "v1",
            "code": "1234567890",
            "activatedAt": "2026-10-09T13:00:00Z",
            "expiresAt": "2026-10-09T14:00:00Z",
        }
    )
    incoming = replacement.model_dump(by_alias=True, exclude_none=False)
    assert (
        refresh_latest_voucher(refreshed, {"v1": incoming})["expiresAt"]
        == replacement.expires_at
    )
    assert (
        refresh_latest_voucher(refreshed, {"v1": incoming})["activatedAt"]
        == replacement.activated_at
    )
