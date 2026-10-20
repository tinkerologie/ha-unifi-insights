# Copyright (c) 2026 Ruaan Deysel
"""Tests for UniFi Insights voucher sensors."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from custom_components.unifi_insights.sensor import (
    VOUCHER_SENSOR_TYPES,
    UnifiVoucherSensor,
    _active_vouchers_value,
    _get_latest_record,
    _latest_attributes,
    _latest_code_value,
    _latest_expiration_value,
    discover_voucher_sensors,
)


@pytest.fixture
def mock_coordinator():
    coord = MagicMock()
    coord.data = {
        "sites": {"default": {"desc": "Default"}},
        "vouchers": {
            "default": {
                "v1": {
                    "id": "v1",
                    "code": "1111111111",
                    "createdAt": "2026-10-09T00:00:00Z",
                    "expiresAt": "2026-10-10T00:00:00Z",
                    "expired": False,
                    "authorizedGuestLimit": 5,
                    "authorizedGuestCount": 2,
                },
                "v2": {
                    "id": "v2",
                    "code": "2222222222",
                    "createdAt": "2026-10-08T00:00:00Z",
                    "expiresAt": "2026-10-08T12:00:00Z",
                    "expired": True,
                },
            }
        },
        "latest_vouchers": {
            "default": {
                "id": "v1",
                "code": "1111111111",
                "createdAt": "2026-10-09T00:00:00Z",
                "activatedAt": "2026-10-09T01:00:00Z",
                "expiresAt": "2026-10-10T00:00:00Z",
                "expired": False,
                "timeLimitMinutes": 1440,
                "authorizedGuestLimit": 5,
                "authorizedGuestCount": 2,
                "dataUsageLimitMBytes": 500,
                "rxRateLimitKbps": 10000,
                "txRateLimitKbps": 5000,
            }
        },
    }
    coord.vouchers_available = MagicMock(return_value=True)
    return coord


def test_discover_voucher_sensors(hass, mock_coordinator, mock_config_entry):
    known_keys = set()
    entities = discover_voucher_sensors(
        mock_coordinator,
        known_keys,
        hass=hass,
        entry=mock_config_entry,
        namespace="console",
    )
    assert len(entities) == 3
    assert {e.unique_id for e in entities} == {
        "console_default_latest_voucher_code",
        "console_default_latest_voucher_expiration",
        "console_default_active_vouchers",
    }
    # Deduplication
    entities2 = discover_voucher_sensors(
        mock_coordinator,
        known_keys,
        hass=hass,
        entry=mock_config_entry,
        namespace="console",
    )
    assert len(entities2) == 0


def test_sensor_availability(mock_coordinator):
    desc = VOUCHER_SENSOR_TYPES[0]
    sensor = UnifiVoucherSensor(
        mock_coordinator,
        desc,
        "default",
        unique_id="console_default_" + desc.key,
    )
    assert sensor.available is True
    mock_coordinator.vouchers_available.return_value = False
    assert sensor.available is False


def test_sensor_device_info(mock_coordinator):
    desc = VOUCHER_SENSOR_TYPES[0]
    sensor = UnifiVoucherSensor(
        mock_coordinator,
        desc,
        "default",
        unique_id="console_default_" + desc.key,
    )
    assert sensor.device_info is not None
    assert ("unifi_insights", "site_default") in sensor.device_info.get(
        "identifiers", set()
    )


def test_latest_voucher_code_sensor(mock_coordinator):
    desc = next(d for d in VOUCHER_SENSOR_TYPES if d.key == "latest_voucher_code")
    sensor = UnifiVoucherSensor(
        mock_coordinator,
        desc,
        "default",
        unique_id="console_default_" + desc.key,
    )
    assert sensor.native_value == "1111111111"
    attrs = sensor.extra_state_attributes
    assert attrs is not None
    assert attrs["voucher_id"] == "v1"
    assert attrs["duration_minutes"] == 1440
    assert attrs["guest_limit"] == 5
    assert attrs["guest_count"] == 2
    assert attrs["data_limit_mb"] == 500
    assert attrs["download_limit_kbps"] == 10000
    assert attrs["upload_limit_kbps"] == 5000
    assert "2026-10-09" in attrs["created_at"]
    assert "2026-10-10" in attrs["expires_at"]


def test_latest_voucher_code_empty(mock_coordinator):
    mock_coordinator.data["latest_vouchers"] = {}
    desc = next(d for d in VOUCHER_SENSOR_TYPES if d.key == "latest_voucher_code")
    sensor = UnifiVoucherSensor(
        mock_coordinator,
        desc,
        "default",
        unique_id="console_default_" + desc.key,
    )
    assert sensor.native_value is None
    assert sensor.extra_state_attributes is None


def test_latest_voucher_expiration_sensor(mock_coordinator):
    desc = next(d for d in VOUCHER_SENSOR_TYPES if d.key == "latest_voucher_expiration")
    sensor = UnifiVoucherSensor(
        mock_coordinator,
        desc,
        "default",
        unique_id="console_default_" + desc.key,
    )
    val = sensor.native_value
    assert isinstance(val, datetime)
    assert val == datetime(2026, 10, 10, 0, 0, tzinfo=UTC)


def test_latest_voucher_expiration_empty(mock_coordinator):
    mock_coordinator.data["latest_vouchers"] = {}
    desc = next(d for d in VOUCHER_SENSOR_TYPES if d.key == "latest_voucher_expiration")
    sensor = UnifiVoucherSensor(
        mock_coordinator,
        desc,
        "default",
        unique_id="console_default_" + desc.key,
    )
    assert sensor.native_value is None


def test_active_vouchers_sensor(mock_coordinator, freezer):
    freezer.move_to("2026-10-09T10:00:00Z")
    desc = next(d for d in VOUCHER_SENSOR_TYPES if d.key == "active_vouchers")
    sensor = UnifiVoucherSensor(
        mock_coordinator,
        desc,
        "default",
        unique_id="console_default_" + desc.key,
    )
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes is None

    # Advance time past expiration
    freezer.move_to("2026-10-10T01:00:00Z")
    assert sensor.native_value == 0


def test_helper_edge_cases():
    assert _get_latest_record(None, "default") is None
    assert _get_latest_record({}, "default") is None
    assert _get_latest_record({"latest_vouchers": "bad"}, "default") is None
    assert (
        _get_latest_record({"latest_vouchers": {"default": "bad"}}, "default") is None
    )
    assert _latest_code_value(None, "default") is None
    assert (
        _latest_code_value({"latest_vouchers": {"default": {"code": None}}}, "default")
        is None
    )
    assert _latest_expiration_value(None, "default") is None
    assert _active_vouchers_value(None, "default") == 0
    assert _active_vouchers_value({"vouchers": "bad"}, "default") == 0
    assert _latest_attributes(None, "default") == {}


def test_latest_attributes_skip_missing_and_invalid_values():
    """Absent fields and unparseable timestamps are omitted from attributes."""
    data = {
        "latest_vouchers": {
            "default": {
                "id": "v1",
                "expiresAt": "not-a-timestamp",
                "timeLimitMinutes": 60,
            }
        }
    }
    assert _latest_attributes(data, "default") == {
        "voucher_id": "v1",
        "duration_minutes": 60,
    }
