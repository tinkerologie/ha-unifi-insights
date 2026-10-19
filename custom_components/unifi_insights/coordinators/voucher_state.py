# Copyright (c) 2026 Ruaan Deysel
"""Pure helpers and data structures for hotspot voucher entities."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from homeassistant.util import dt as dt_util

from custom_components.unifi_insights.data_transforms import get_field

TIME_LIMIT_MINUTES_RANGE: Final = (1, 1_000_000)  # spec timeLimitMinutes
GUEST_LIMIT_MIN: Final = 1  # spec authorizedGuestLimit (no spec max)
DATA_LIMIT_MBYTES_RANGE: Final = (1, 1_048_576)  # spec dataUsageLimitMBytes
RATE_LIMIT_KBPS_RANGE: Final = (2, 100_000)  # spec rx/txRateLimitKbps
GUEST_LIMIT_UI_MAX: Final = 1000  # our cap, NOT from the spec
KBPS_PER_MBPS: Final = 1000


@dataclass(frozen=True, slots=True)
class VoucherInputSpec:
    """Specification of an input number entity for voucher generation."""

    field: str
    minimum: float
    maximum: float
    step: float
    default: float


VOUCHER_INPUTS: Final[dict[str, VoucherInputSpec]] = {
    "voucher_duration": VoucherInputSpec(
        "duration_minutes",
        float(TIME_LIMIT_MINUTES_RANGE[0]),
        float(TIME_LIMIT_MINUTES_RANGE[1]),
        1.0,
        480.0,
    ),
    "voucher_guest_limit": VoucherInputSpec(
        "guest_limit",
        0.0,
        float(GUEST_LIMIT_UI_MAX),
        1.0,
        1.0,
    ),
    "voucher_download_limit": VoucherInputSpec(
        "download_limit_mbps",
        0.0,
        float(RATE_LIMIT_KBPS_RANGE[1] / KBPS_PER_MBPS),
        0.1,
        0.0,
    ),
    "voucher_upload_limit": VoucherInputSpec(
        "upload_limit_mbps",
        0.0,
        float(RATE_LIMIT_KBPS_RANGE[1] / KBPS_PER_MBPS),
        0.1,
        0.0,
    ),
    "voucher_data_limit": VoucherInputSpec(
        "data_limit_mb",
        0.0,
        float(DATA_LIMIT_MBYTES_RANGE[1]),
        1.0,
        0.0,
    ),
}


@dataclass(slots=True)
class VoucherSettings:
    """Per-site settings held in Home Assistant for voucher generation."""

    duration_minutes: int = 480
    guest_limit: int = 1
    download_limit_mbps: float = 0.0
    upload_limit_mbps: float = 0.0
    data_limit_mb: int = 0


def cast_input(key: str, value: float) -> int | float:
    """Cast an input value to its target type and precision."""
    spec = VOUCHER_INPUTS[key]
    if spec.step == 1:
        return round(value)
    return round(float(value), 3)


def restore_input(key: str, value: object) -> int | float | None:
    """Restore an input value if valid and within bounds, else None."""
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    val = float(value)
    if math.isnan(val) or math.isinf(val):
        return None
    spec = VOUCHER_INPUTS.get(key)
    if spec is None:
        return None
    if val < spec.minimum or val > spec.maximum:
        return None
    return cast_input(key, val)


def voucher_request_kwargs(settings: VoucherSettings) -> dict[str, int]:
    """Convert settings into UniFi voucher creation kwargs with clamped bounds."""
    duration = round(settings.duration_minutes)
    duration = max(
        TIME_LIMIT_MINUTES_RANGE[0], min(duration, TIME_LIMIT_MINUTES_RANGE[1])
    )
    kwargs: dict[str, int] = {
        "time_limit_minutes": duration,
    }
    if settings.guest_limit > 0:
        kwargs["authorized_guest_limit"] = max(
            GUEST_LIMIT_MIN, round(settings.guest_limit)
        )
    if settings.data_limit_mb > 0:
        data_mb = round(settings.data_limit_mb)
        data_mb = max(
            DATA_LIMIT_MBYTES_RANGE[0], min(data_mb, DATA_LIMIT_MBYTES_RANGE[1])
        )
        kwargs["data_usage_limit_mbytes"] = data_mb
    if settings.download_limit_mbps > 0:
        rx_kbps = round(settings.download_limit_mbps * KBPS_PER_MBPS)
        rx_kbps = max(RATE_LIMIT_KBPS_RANGE[0], min(rx_kbps, RATE_LIMIT_KBPS_RANGE[1]))
        kwargs["rx_rate_limit_kbps"] = rx_kbps
    if settings.upload_limit_mbps > 0:
        tx_kbps = round(settings.upload_limit_mbps * KBPS_PER_MBPS)
        tx_kbps = max(RATE_LIMIT_KBPS_RANGE[0], min(tx_kbps, RATE_LIMIT_KBPS_RANGE[1]))
        kwargs["tx_rate_limit_kbps"] = tx_kbps
    return kwargs


def voucher_to_record(voucher: object) -> dict[str, Any] | None:
    """Convert a voucher model or dict to an internal record dict."""
    if isinstance(voucher, Mapping):
        rec = dict(voucher)
    elif hasattr(voucher, "model_dump") and callable(voucher.model_dump):
        try:
            dumped = voucher.model_dump(by_alias=True)
        except Exception:
            return None
        if isinstance(dumped, dict):
            rec = dumped
        else:
            return None
    else:
        return None

    if not rec.get("id"):
        return None
    return rec


def parse_timestamp(value: object) -> datetime | None:
    """Parse a timestamp into a UTC-aware datetime or None."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=dt_util.UTC)
        return value
    if isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        if parsed is not None:
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=dt_util.UTC)
            return parsed
        return None
    return None


def _field(record: Mapping[str, Any], *keys: str) -> Any:
    """Return the first non-None value among keys in record, or None."""
    return get_field(record, *keys)


def is_voucher_active(record: Mapping[str, Any], now: datetime) -> bool:
    """Return True if voucher is not expired and can still admit a guest."""
    if _field(record, "expired") is True:
        return False

    expires_at = parse_timestamp(_field(record, "expiresAt", "expires_at"))
    if expires_at is not None and expires_at <= now:
        return False

    limit = _field(record, "authorizedGuestLimit", "authorized_guest_limit")
    if (
        limit is not None
        and not isinstance(limit, bool)
        and isinstance(limit, (int, float))
    ):
        count = _field(record, "authorizedGuestCount", "authorized_guest_count")
        guest_count = (
            count
            if (
                count is not None
                and not isinstance(count, bool)
                and isinstance(count, (int, float))
            )
            else 0
        )
        if guest_count >= limit:
            return False

    return True


def count_active_vouchers(inventory: object, now: datetime | None = None) -> int:
    """Count redeemable vouchers in a site inventory."""
    if not isinstance(inventory, Mapping):
        return 0
    current_time = now if now is not None else dt_util.utcnow()
    count = 0
    for item in inventory.values():
        if isinstance(item, Mapping) and is_voucher_active(item, current_time):
            count += 1
    return count


def latest_voucher_qr_payload(
    record: object, now: datetime | None = None
) -> str | None:
    """Extract QR code payload from latest voucher record."""
    if not isinstance(record, Mapping):
        return None
    if record.get("expired") is True:
        return None
    expires_at_raw = _field(record, "expiresAt", "expires_at")
    if expires_at_raw is not None:
        expires_at = parse_timestamp(expires_at_raw)
        if expires_at is not None:
            current_time = now if now is not None else dt_util.utcnow()
            if expires_at <= current_time:
                return None
    code = record.get("code")
    if isinstance(code, str) and code:
        return code
    return None


def refresh_latest_voucher(
    record: Mapping[str, Any], inventory: Mapping[str, Any]
) -> dict[str, Any]:
    """Refresh latest voucher record from polled inventory if present."""
    voucher_id = record.get("id")
    if voucher_id and isinstance(inventory, Mapping):
        item = inventory.get(voucher_id)
        if isinstance(item, Mapping):
            new_record = dict(item)
            if record.get("expired") is True:
                new_record["expired"] = True
            for camel, snake in (
                ("activatedAt", "activated_at"),
                ("expiresAt", "expires_at"),
            ):
                if _field(new_record, camel, snake) is None:
                    known_value = _field(record, camel, snake)
                    if known_value is not None:
                        new_record[camel] = known_value
            old_count = record.get("authorizedGuestCount")
            new_count = new_record.get("authorizedGuestCount")
            if (
                isinstance(old_count, int)
                and not isinstance(old_count, bool)
                and isinstance(new_count, int)
                and not isinstance(new_count, bool)
                and old_count > new_count
            ):
                new_record["authorizedGuestCount"] = old_count
            return new_record
    return dict(record)


def voucher_site_ids(data: object) -> list[str]:
    """Return site IDs if data contains both sites and vouchers mapping sections."""
    if not isinstance(data, Mapping):
        return []
    sites = data.get("sites")
    vouchers = data.get("vouchers")
    if not isinstance(sites, Mapping) or not isinstance(vouchers, Mapping):
        return []
    return [str(site_id) for site_id in sites]
