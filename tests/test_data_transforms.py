# Copyright 2026 UniFi Insights contributors
"""Tests for data transformation functions."""

import ast
import copy
import dataclasses
import logging
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from unifi_official_api.network.models.client import ClientType
from unifi_official_api.network.models.device import DeviceState
from unifi_official_api.network.models.voucher import Voucher

from custom_components.unifi_insights import entity, topology
from custom_components.unifi_insights.console_identity import (
    normalize_mac as console_normalize_mac,
)
from custom_components.unifi_insights.coordinators import protect_events
from custom_components.unifi_insights.coordinators.device import (
    UnifiInsightsDeviceCoordinator,
)
from custom_components.unifi_insights.dashboard_snapshots import bits_per_second_to_int
from custom_components.unifi_insights.data_transforms import (
    DATA_LIMIT_MBYTES_RANGE,
    GUEST_LIMIT_UI_MAX,
    NETWORK_DEVICE_STATES,
    NETWORK_FACTS_KEY,
    RADIO_BAND_LABELS,
    RADIO_TX_POWER_MODE_OPTIONS,
    RADIO_WLAN_STANDARD_OPTIONS,
    TIME_LIMIT_MINUTES_RANGE,
    VOUCHER_INPUTS,
    NetworkDeviceFacts,
    NetworkPortFacts,
    SiteNetworkFacts,
    VoucherSettings,
    _field,
    as_bits_per_second,
    build_site_network_facts,
    bytes_to_bits_per_second,
    cast_input,
    client_connection_type,
    client_is_connected,
    count_active_vouchers,
    current_chime_ringtone_id,
    device_facts,
    device_kind,
    device_online_state,
    device_serviceable,
    first_not_none,
    get_field,
    is_volatile_storage_mount,
    is_voucher_active,
    latest_voucher_qr_payload,
    led_level_to_percent,
    network_radio_details,
    network_radio_stats,
    normalize_innerspace_snapshot,
    normalize_legacy_storage,
    normalize_legacy_wans,
    normalize_mac,
    parse_floor_plan_asset_path,
    parse_timestamp,
    percent_to_led_level,
    port_index,
    radio_band_map,
    radio_band_slug,
    radio_channel,
    radio_channel_width,
    radio_tx_power_mode,
    radio_tx_retries,
    radio_wlan_standard,
    refresh_latest_voucher,
    restore_input,
    site_device_facts,
    site_network_facts,
    storage_used_percent,
    tx_power_mode_slug,
    uplink_rate_bps,
    valid_led_level,
    voucher_request_kwargs,
    voucher_site_ids,
    voucher_to_record,
    wlan_standard_slug,
)
from custom_components.unifi_insights.innerspace_transforms import (
    _normalize_innerspace_mac,
    correlate_innerspace_devices,
    transform_innerspace_device,
)
from custom_components.unifi_insights.sensor import bytes_to_bits
from custom_components.unifi_insights.service_targets import _mac_key
from tests.fixtures.library_responses import (
    SAMPLE_NETWORK_DEVICE,
    SAMPLE_PDU_DEVICE,
    SAMPLE_PDU_LEGACY_DEVICE,
    SAMPLE_UDM_PRO_OVERVIEW,
    SAMPLE_UDM_PRO_STATS,
)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (None, None),
        ([], None),
        ({}, None),
        ({"ringSettings": None}, None),
        ({"ringSettings": {}}, None),
        ({"ringSettings": []}, None),
        ({"ringSettings": [None]}, None),
        ({"ringSettings": [{}]}, None),
        ({"ringSettings": [{"ringtoneId": 1}]}, None),
        ({"ringSettings": [{"ringtoneId": True}]}, None),
        ({"ringSettings": [{"ringtoneId": ""}]}, None),
        ({"ringSettings": [{"ringtoneId": " "}]}, None),
        (
            {"ringSettings": [{"ringtoneId": "tone-a"}, {"ringtoneId": "tone-b"}]},
            None,
        ),
        ({"ringSettings": [{"ringtoneId": "tone-a"}, {}]}, None),
        ({"ringSettings": [{"ringtoneId": "tone-a"}]}, "tone-a"),
        (
            {"ringSettings": [{"ringtoneId": "tone-a"}, {"ringtoneId": "tone-a"}]},
            "tone-a",
        ),
    ],
)
def test_current_chime_ringtone_id_is_safe_and_requires_agreement(data, expected):
    """Only one valid ID shared by all paired settings is a current value."""
    assert current_chime_ringtone_id(data) == expected


def test_normalize_legacy_wans_uses_controller_status():
    """Each WAN named in last_wan_status becomes one connection entry."""
    wans = normalize_legacy_wans(
        {
            # The physical port block is ignored: it cannot see a PPP session.
            "wan1": {"type": "ethernet", "name": "eth8", "up": True},
            "last_wan_status": {"WAN": "online", "WAN2": "offline"},
            "last_wan_interfaces": {
                "WAN": {"ip": "198.51.100.7", "alive": True},
                "WAN2": {"ip": "", "alive": False},
            },
        }
    )
    assert wans == [
        {
            "key": "wan",
            "name": "WAN",
            "status": "online",
            "alive": True,
            "ip": "198.51.100.7",
            "connected": True,
        },
        {
            "key": "wan2",
            "name": "WAN2",
            "status": "offline",
            "alive": False,
            "ip": None,
            "connected": False,
        },
    ]


def test_normalize_legacy_wans_status_wins_over_alive():
    """The controller's status decides even when the probe disagrees."""
    (wan,) = normalize_legacy_wans(
        {
            "last_wan_status": {"WAN": "Offline"},
            "last_wan_interfaces": {"WAN": {"ip": "198.51.100.7", "alive": True}},
        }
    )
    assert wan["connected"] is False


def test_normalize_legacy_wans_falls_back_to_alive():
    """Without a status string, the reachability probe decides."""
    wans = normalize_legacy_wans(
        {
            "last_wan_interfaces": {
                "WAN": {"ip": "198.51.100.7", "alive": True},
                "WAN2": {"alive": "yes"},
            }
        }
    )
    assert [(w["key"], w["connected"], w["alive"]) for w in wans] == [
        ("wan", True, True),
        ("wan2", False, None),
    ]


def test_normalize_legacy_wans_unspecified_address_is_none():
    """Placeholder addresses a down link reports are not exposed as its IP."""
    # 0.0.0.0 is a payload value compared against here, not an address bound.
    for missing_ip in ("", "0.0.0.0", "::", None):  # noqa: S104
        (wan,) = normalize_legacy_wans(
            {"last_wan_interfaces": {"WAN": {"ip": missing_ip, "alive": False}}}
        )
        assert wan["ip"] is None


def test_normalize_legacy_wans_without_wan_data():
    """Non-gateways and malformed payloads yield no WAN entries."""
    assert normalize_legacy_wans({"port_table": []}) == []
    assert normalize_legacy_wans({"last_wan_status": ["WAN"]}) == []
    assert (
        normalize_legacy_wans(
            {"last_wan_status": {"": "online", 3: "online"}, "last_wan_interfaces": 1}
        )
        == []
    )


def test_normalize_innerspace_snapshot_excludes_shapes_and_urls_and_correlates_macs():
    """Test InnerSpace normalization, placement states, and MAC correlation."""
    project = {
        "project": {"id": "proj-1", "model": None, "environment": None},
        "plans": [
            {
                "id": "fp-1",
                "name": "Office Floor",
                "ppm": 20.0,
                "ordering": 1,
                "siteId": "site-a",
            }
        ],
        "products": [{"id": "ap-1", "planId": "fp-1", "code": "U6-Pro"}],
        "shapes": [{"id": "shape-1", "type": "wall"}],
    }
    floor_plans = [
        {
            "id": "fp-1",
            "name": "Office Floor",
            "floor_number": 2,
            "image_url": "/proxy/innerspace/assets/fp-1.png",
            "ppm": 20.0,
            "width": 1000,
            "height": 800,
            "origin_x": 0.0,
            "origin_y": 0.0,
            "site_id": None,
        },
        {
            "id": "fp-unmapped",
            "name": "Warehouse Floor",
            "floor_number": 1,
            "site_id": None,
        },
    ]
    access_points = [
        {
            "id": "ap-1",
            "name": "Office AP",
            "model": "U6-Pro",
            "mac": "AA-BB-CC-11-22-33",
            "serial": "SN1",
            "floor_plan_id": "fp-1",
            "x": 120.5,
            "y": 340.0,
            "height": 2.8,
            "azimuth": 180.0,
            "mount": "ceiling",
            "status": "online",
        }
    ]
    switches = [
        {
            "id": "sw-1",
            "name": "Ambiguous Switch",
            "model": "USW-24",
            "mac": "AA:BB:CC:44:55:66",
            "floor_plan_id": "fp-unmapped",
            "x": 10.0,
            "y": 20.0,
            "status": "online",
        }
    ]
    inventory = [
        {
            "id": "inv-1",
            "name": "Spare Camera or AP",
            "model": "U6-Enterprise",
            "mac": "11:22:33:44:55:66",
            "serial": "SN-INV",
        }
    ]

    # Site-a and Site-b both have AA:BB:CC:11:22:33, narrowed by fp-1 to site-a.
    # AA:BB:CC:44:55:66 has no site_id across two sites -> ambiguous -> no match.
    network_devices = {
        "site-a": {
            "net-ap-a": {"id": "net-ap-a", "macAddress": "aa:bb:cc:11:22:33"},
            "net-sw-a": {"id": "net-sw-a", "macAddress": "aa:bb:cc:44:55:66"},
        },
        "site-b": {
            "net-ap-b": {"id": "net-ap-b", "macAddress": "aa:bb:cc:11:22:33"},
            "net-sw-b": {"id": "net-sw-b", "macAddress": "aa:bb:cc:44:55:66"},
        },
    }
    protect_devices = {
        "cameras": {
            "cam-1": {"id": "cam-1", "mac": "112233445566"},
        }
    }

    snapshot = normalize_innerspace_snapshot(
        project=project,
        floor_plans=floor_plans,
        access_points=access_points,
        switches=switches,
        inventory=inventory,
        network_devices=network_devices,
        protect_devices=protect_devices,
    )

    # Project shapes must be excluded, floor_plan image_url preserved for image entity
    assert "shapes" not in snapshot["project"]
    assert (
        snapshot["floor_plans"]["fp-1"]["image_url"]
        == "/proxy/innerspace/assets/fp-1.png"
    )
    assert snapshot["floor_plans"]["fp-1"]["site_id"] == "site-a"

    # Placed AP has placement_state='placed', site_id='site-a', and matches net-ap-a
    ap_rec = snapshot["access_points"]["ap-1"]
    assert ap_rec["placement_state"] == "placed"
    assert ap_rec["device_type"] == "access_point"
    assert ap_rec["matched_domain"] == "network"
    assert ap_rec["matched_site_id"] == "site-a"
    assert ap_rec["matched_device_id"] == "net-ap-a"

    # Ambiguous switch across two sites without site_id is NOT matched
    sw_rec = snapshot["switches"]["sw-1"]
    assert sw_rec["placement_state"] == "placed"
    assert sw_rec["matched_domain"] is None
    assert sw_rec["correlation"] is None

    # Unplaced inventory record stays distinct, never infers device_type from model,
    # and correlates with Protect camera by normalized MAC
    inv_rec = snapshot["inventory"]["inv-1"]
    assert inv_rec["placement_state"] == "unplaced"
    assert inv_rec["device_type"] is None
    assert inv_rec["matched_domain"] == "protect"
    assert inv_rec["matched_protect_type"] == "camera"
    assert inv_rec["matched_device_id"] == "cam-1"


def test_parse_floor_plan_asset_path_reexport() -> None:
    """Test parse_floor_plan_asset_path is exported from data_transforms."""
    url = "/proxy/innerspace/integration/v1/assets/fp-1/floor.png"
    assert parse_floor_plan_asset_path(url) == ("fp-1", "floor.png")


UDM_STORAGE = [
    {
        "mount_point": "/data",
        "name": "eMMC",
        "type": "eMMC",
        "size": 4143677440,
        "used": 1700257792,
    },
    {
        "mount_point": "/persistent",
        "name": "Backup",
        "type": "eMMC",
        "size": 2046640128,
        "used": 318767104,
    },
    {
        "mount_point": "/" + "tmp",
        "name": "Temporary",
        "type": "other",
        "size": 1073741824,
        "used": 1748992,
    },
]


def test_normalize_legacy_storage_live_shape() -> None:
    """Test normalizing storage from the live UDM record shape."""
    result = normalize_legacy_storage({"storage": UDM_STORAGE})
    assert len(result) == 3
    for entry, expected in zip(result, UDM_STORAGE, strict=True):
        assert set(entry.keys()) == {"mount_point", "name", "type", "size", "used"}
        assert entry == expected


@pytest.mark.parametrize(
    "raw_device",
    [
        {},
        {"storage": None},
        {"storage": {"mount_point": "/data"}},
        {"storage": "not a list"},
    ],
)
def test_normalize_legacy_storage_without_storage(raw_device: dict[str, Any]) -> None:
    """Test devices reporting no storage or non-list storage produce empty list."""
    assert normalize_legacy_storage(raw_device) == []


def test_normalize_legacy_storage_skips_unusable_entries() -> None:
    """Test skipping non-dicts, blank mount points, and duplicates."""
    raw = {
        "storage": [
            "not a dict",
            {"name": "no mount point"},
            {"mount_point": None, "name": "none mount point"},
            {"mount_point": "", "name": "empty mount point"},
            {"mount_point": "   ", "name": "whitespace mount point"},
            {"mount_point": 123, "name": "non-str mount point"},
            {"mount_point": " / ", "name": "root", "size": 100, "used": 50},
            {"mount_point": "/", "name": "duplicate root", "size": 200, "used": 100},
        ]
    }
    result = normalize_legacy_storage(raw)
    assert len(result) == 1
    assert result[0] == {
        "mount_point": "/",
        "name": "root",
        "type": None,
        "size": 100,
        "used": 50,
    }


@pytest.mark.parametrize(
    "bad_val",
    [
        True,
        "100",
        -1,
        float("nan"),
        float("inf"),
        None,
    ],
)
def test_normalize_legacy_storage_invalid_numbers_become_none(
    bad_val: Any,
) -> None:
    """Test invalid size/used values become None; 0 and floats convert properly."""
    raw = {
        "storage": [
            {"mount_point": "/bad", "size": bad_val, "used": bad_val},
            {"mount_point": "/missing"},
            {"mount_point": "/zero", "size": 0, "used": 0},
            {"mount_point": "/float", "size": 1.5e9, "used": 1.5e9},
        ]
    }
    result = normalize_legacy_storage(raw)
    assert result[0]["size"] is None
    assert result[0]["used"] is None
    assert result[1]["size"] is None
    assert result[1]["used"] is None
    assert result[2]["size"] == 0
    assert result[2]["used"] == 0
    assert result[3]["size"] == 1500000000
    assert result[3]["used"] == 1500000000


def test_normalize_legacy_storage_blank_name_and_type_become_none() -> None:
    """Test empty/whitespace name and type become None."""
    raw = {
        "storage": [
            {
                "mount_point": "/data",
                "name": "  ",
                "type": "",
                "size": 1000,
                "used": 500,
            }
        ]
    }
    result = normalize_legacy_storage(raw)
    assert result[0]["name"] is None
    assert result[0]["type"] is None


@pytest.mark.parametrize(
    ("mount", "expected_percent"),
    [
        (UDM_STORAGE[0], 41.0),
        (UDM_STORAGE[1], 15.6),
        (UDM_STORAGE[2], 0.2),
        ({"size": 0, "used": 0}, None),
        ({"size": None, "used": 100}, None),
        ({"size": 100, "used": None}, None),
        ({"size": 100, "used": 150}, 100.0),
        ({"size": 1000, "used": 899.6}, 90.0),
    ],
)
def test_storage_used_percent(
    mount: dict[str, Any], expected_percent: float | None
) -> None:
    """Test percentage calculation, rounding, capping and null handling."""
    assert storage_used_percent(mount) == expected_percent


@pytest.mark.parametrize(
    ("mount", "expected_volatile"),
    [
        ({"mount_point": "/" + "tmp"}, True),
        ({"mount_point": "/" + "tmp/x"}, True),
        ({"mount_point": "/RUN"}, True),
        ({"mount_point": "/var/run"}, True),
        ({"mount_point": "/" + "var/tmp"}, True),
        ({"mount_point": "/" + "dev/shm"}, True),
        ({"mount_point": "/custom", "type": "tmpfs"}, True),
        ({"mount_point": "/custom", "type": "RAMFS"}, True),
        ({"mount_point": "/data"}, False),
        ({"mount_point": "/persistent"}, False),
        ({"mount_point": "/" + "tmpfoo"}, False),
        ({"mount_point": "/volume1"}, False),
        ({"mount_point": "/", "type": "other"}, False),
        ({}, False),
    ],
)
def test_is_volatile_storage_mount(
    mount: dict[str, Any], *, expected_volatile: bool
) -> None:
    """Test detecting volatile (RAM or boot-recreated) filesystems."""
    assert is_volatile_storage_mount(mount) is expected_volatile


def test_normalize_legacy_wans_non_ip_string() -> None:
    """Non-IP string in WAN address triggers ValueError in IP parsing and is kept."""
    (wan,) = normalize_legacy_wans(
        {"last_wan_interfaces": {"WAN": {"ip": "not-an-ip", "alive": True}}}
    )
    assert wan["ip"] == "not-an-ip"


def test_transform_innerspace_device_site_id_preserved_from_raw() -> None:
    """Device raw site_id is preserved and not overwritten by floor plan site_id."""
    raw = {
        "id": "dev-1",
        "site_id": "original-site",
        "floor_plan_id": "fp-1",
    }
    floor_plans = {"fp-1": {"name": "First Floor", "site_id": "plan-site"}}
    transformed = transform_innerspace_device(raw, floor_plans=floor_plans)
    assert transformed["site_id"] == "original-site"


def test_correlate_innerspace_devices_non_dict_section() -> None:
    """Non-dict sections in snapshot are skipped during MAC correlation."""
    snapshot = {
        "access_points": "not-a-dict",
        "switches": None,
        "inventory": {},
        "devices": {},
    }
    correlations = correlate_innerspace_devices(snapshot)
    assert correlations == {}


def test_correlate_innerspace_devices_single_collection_without_inventory() -> None:
    """Single collection mode without inventory_devices mapping."""
    collection = {"d1": {"mac": "aa:bb:cc:dd:ee:ff"}}
    correlations = correlate_innerspace_devices(collection, inventory_devices=None)
    assert "d1" in correlations


def test_normalize_innerspace_snapshot_plans_item_missing_id() -> None:
    """Project plans list containing items without id or non-dicts are tolerated."""
    project = {
        "plans": ["not-a-dict", {"name": "No ID"}, {"id": "p1", "name": "Plan 1"}],
    }
    snapshot = normalize_innerspace_snapshot(project=project, floor_plans=[])
    assert "p1" in snapshot["floor_plans"]


# --- LED level ---------------------------------------------------------------

# Tests for the UniFi Protect LED level conversions.


@pytest.mark.parametrize(
    ("percent", "expected"),
    [(0, 1), (1, 1), (16, 1), (17, 1), (50, 3), (99, 6), (100, 6)],
)
def test_percent_to_led_level(percent: int, expected: int) -> None:
    """Test percent maps to the nearest LED level, clamped to 1-6."""
    assert percent_to_led_level(percent) == expected


@pytest.mark.parametrize(
    ("level", "expected"),
    [(1, 17), (2, 33), (3, 50), (4, 67), (5, 83), (6, 100), (3.0, 50)],
)
def test_led_level_to_percent(level: Any, expected: int) -> None:
    """Test LED levels 1-6 map to a percentage."""
    assert led_level_to_percent(level) == expected


@pytest.mark.parametrize("level", [0, 7, -1, None, "3", True, 3.5, float("nan")])
def test_led_level_to_percent_invalid(level: Any) -> None:
    """Test anything that is not a valid level maps to None."""
    assert led_level_to_percent(level) is None
    assert valid_led_level(level) is None


def test_percent_round_trip_is_stable() -> None:
    """Test a level read back as a percentage converts to the same level."""
    for level in range(1, 7):
        percent = led_level_to_percent(level)
        assert percent is not None
        assert percent_to_led_level(percent) == level


# --- Voucher state -----------------------------------------------------------

# Tests for voucher_state module.


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


# --- Network device read model -----------------------------------------------

# Table-driven tests for the network device read model (#337).

# Constructed legacy port_table rows (not a capture), normalised through the
# coordinator's own legacy port normaliser so the shape is the stored one.
_LEGACY_PORT_TABLE: list[dict[str, Any]] = [
    {"port_idx": 1, "up": True, "speed": 1000, "media": "GE", "tx_bytes": 10},
    {"port_idx": 9, "up": True, "speed": 10000, "media": "SFP+", "is_uplink": True},
    {"port_idx": 11, "up": False, "media": "SFP+", "sfp_found": False},
]
_LEGACY_PORTS = [
    port
    for row in _LEGACY_PORT_TABLE
    if (port := UnifiInsightsDeviceCoordinator._normalize_legacy_port(row)) is not None
]

# Constructed: the captured overview after a successful legacy merge
# (the live console reported legacy_type "udm" and port_table-backed ports).
UDM_PRO_LEGACY_ENRICHED: dict[str, Any] = {
    **SAMPLE_UDM_PRO_OVERVIEW,
    "ports": _LEGACY_PORTS,
    "topology": {"legacy_type": "udm"},
}

# Constructed: a legacy-only device, as the v1-5xx fallback maps it.
LEGACY_ONLY_SWITCH = UnifiInsightsDeviceCoordinator._legacy_device_to_v1_dict(
    {
        "_id": "60a1b2c3d4e5f67890123456",
        "mac": "AA:BB:CC:00:00:E1",
        "model": "US24P250",
        "name": "Legacy Switch",
        "type": "usw",
        "up": True,
        "port_table": [{"port_idx": 2, "up": True, "speed": 1000}],
    }
)

# Constructed: explicit zeros everywhere a metric is read.
ZERO_STATS: dict[str, Any] = {
    "cpuUtilizationPct": 0,
    "memoryUtilizationPct": 0,
    "uptimeSec": 0,
    "uplink": {"txRateBps": 0, "rxRateBps": 0},
}

# Constructed: snake-case bits/s keys and the explicit bytes/s fallbacks.
SNAKE_AND_BYTES_STATS: dict[str, Any] = {
    "cpu_utilization_pct": 7.5,
    "memory_percent": 40,
    "uptime_seconds": 3600,
    "uplink": {"tx_rate_bps": 100},
    "rx_bytes_per_sec": 50,
}

# Constructed: malformed values.
MALFORMED_STATS: dict[str, Any] = {
    "cpuUtilizationPct": True,
    "memoryUtilizationPct": "bad",
    "uptimeSec": float("nan"),
    "uplink": {"txRateBps": "bad", "rxRateBps": float("nan")},
}


# --- Shared primitives ----------------------------------------------------


# ADR-011: data_transforms may import Home Assistant's pure utilities and the
# pure integration modules only, so coordinators and builders can use it
# without an import cycle.
_TRANSFORMS_ALLOWED_INTEGRATION_IMPORTS = frozenset({"const", "innerspace_transforms"})


def _transforms_imports() -> list[tuple[str, int]]:
    """Return (module, level) for every import in data_transforms.py."""
    path = (
        Path(__file__).parents[1]
        / "custom_components/unifi_insights/data_transforms.py"
    )
    found: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            found.append((node.module or "", node.level))
        elif isinstance(node, ast.Import):
            found.extend((alias.name, 0) for alias in node.names)
    return found


def test_data_transforms_imports_only_pure_modules() -> None:
    """Home Assistant imports are limited to homeassistant.util.*."""
    for module, level in _transforms_imports():
        if level:
            assert level == 1, module
            assert module in _TRANSFORMS_ALLOWED_INTEGRATION_IMPORTS, module
        elif module.startswith("homeassistant"):
            assert module.startswith("homeassistant.util"), module
        else:
            assert not module.startswith("custom_components"), module


@pytest.mark.parametrize(
    ("data", "keys", "kwargs", "expected"),
    [
        ({"a": 0, "b": 1}, ("a", "b"), {}, 0),
        ({"a": False, "b": True}, ("a", "b"), {}, False),
        ({"a": "", "b": "x"}, ("a", "b"), {}, ""),
        ({"a": None, "b": 2}, ("a", "b"), {}, 2),
        ({}, ("a", "b"), {}, None),
        ({}, ("a",), {"default": "fallback"}, "fallback"),
        ({"a": None}, ("a",), {"default": 5}, 5),
    ],
)
def test_get_field(
    data: dict[str, Any], keys: tuple[str, ...], kwargs: dict[str, Any], expected: Any
) -> None:
    """get_field returns the first non-None value and keeps falsy readings."""
    assert get_field(data, *keys, **kwargs) == expected


def test_first_not_none() -> None:
    """first_not_none keeps 0 and False and falls back to the default."""
    assert first_not_none(None, 0, 5) == 0
    flag = False
    assert first_not_none(None, flag) is False
    assert first_not_none(None, None) is None
    assert first_not_none(default="x") == "x"


def test_field_helpers_delegate_to_one_implementation() -> None:
    """The old helper names are thin wrappers over data_transforms.get_field."""
    assert entity.get_field is get_field
    assert entity.first_not_none is first_not_none
    assert topology.normalize_mac is normalize_mac
    record = {"a": None, "b": 0}
    assert topology.first_present(record, "a", "b") == 0
    assert _field(record, "a", "b") == 0
    assert _field(record, "a") is None
    # The Protect reconciler reads its timestamps through the same helper.
    assert protect_events.get_field is get_field


@pytest.mark.parametrize(
    ("value", "bits", "from_bytes"),
    [
        (0, 0.0, 0.0),
        (100, 100.0, 800.0),
        (12.5, 12.5, 100.0),
        ("100", 100.0, 800.0),
        (None, None, None),
        (True, None, None),
        (False, None, None),
        ("bad", None, None),
        ([], None, None),
        (float("nan"), None, None),
        (float("inf"), None, None),
        (float("-inf"), None, None),
        ("nan", None, None),
        (10**400, None, None),
        (1e308, 1e308, None),
    ],
)
def test_numeric_primitives(value: Any, bits: Any, from_bytes: Any) -> None:
    """Both rate primitives keep zero and reject flags, junk and non-finite."""
    assert as_bits_per_second(value) == bits
    assert bytes_to_bits_per_second(value) == from_bytes


def test_converters_delegate_to_the_shared_primitive() -> None:
    """The sensor and dashboard converters use the validated primitive."""
    assert bytes_to_bits(12) == 96
    assert bytes_to_bits(None) is None
    assert bits_per_second_to_int(bytes_to_bits_per_second(12)) == 96
    assert isinstance(bits_per_second_to_int(12.5), int)
    assert bits_per_second_to_int(bytes_to_bits_per_second(float("nan"))) is None


# Each column pins one MAC policy: canonical (topology HMAC input), console
# identity, legacy key match, InnerSpace correlation and service input.
_MAC_POLICIES = (
    normalize_mac,
    console_normalize_mac,
    UnifiInsightsDeviceCoordinator._normalize_mac,
    _normalize_innerspace_mac,
    _mac_key,
)
_CANONICAL = "aa:bb:cc:dd:ee:ff"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(
            "AABBCCDDEEFF",
            (_CANONICAL, "aabbccddeeff", "aabbccddeeff", _CANONICAL, "aabbccddeeff"),
            id="bare",
        ),
        pytest.param(
            " AA:BB:CC:DD:EE:FF ",
            (_CANONICAL, " aa:bb:cc:dd:ee:ff ", _CANONICAL, _CANONICAL, "aabbccddeeff"),
            id="whitespace",
        ),
        pytest.param(
            "aabb.ccdd.eeff",
            (None, "aabb.ccdd.eeff", "aabb.ccdd.eeff", _CANONICAL, "aabbccddeeff"),
            id="dotted",
        ),
        pytest.param(
            "aa-bb:cc-dd:ee-ff",
            (_CANONICAL, _CANONICAL, "aa-bb:cc-dd:ee-ff", _CANONICAL, "aabbccddeeff"),
            id="mixed-separators",
        ),
        pytest.param(
            ":aa:bb:cc:dd:ee:ff:",
            (
                None,
                ":aa:bb:cc:dd:ee:ff:",
                ":aa:bb:cc:dd:ee:ff:",
                _CANONICAL,
                "aabbccddeeff",
            ),
            id="leading-trailing-separators",
        ),
        pytest.param(
            "zz:bb:cc:dd:ee:ff",
            (None, "zz:bb:cc:dd:ee:ff", "zz:bb:cc:dd:ee:ff", None, "zzbbccddeeff"),
            id="malformed",
        ),
        pytest.param("   ", (None, "   ", "", None, None), id="whitespace-only"),
        pytest.param("", (None, None, None, None, None), id="empty"),
        pytest.param(None, (None, None, None, None, None), id="none"),
    ],
)
def test_mac_policies_are_pinned(value: Any, expected: tuple[Any, ...]) -> None:
    """The canonical parser and the four identity policies keep their outputs."""
    assert tuple(policy(value) for policy in _MAC_POLICIES) == expected


# --- Online state ---------------------------------------------------------


def test_spec_state_tuple_matches_the_network_api() -> None:
    """The local tuple maps exactly the spec's ten network device states."""
    assert dict(NETWORK_DEVICE_STATES) == {
        "ONLINE": "online",
        "OFFLINE": "offline",
        "CONNECTION_INTERRUPTED": "offline",
        "ISOLATED": "offline",
        "U5G_INCORRECT_TOPOLOGY": "offline",
        "PENDING_ADOPTION": "unknown",
        "UPDATING": "unknown",
        "GETTING_READY": "unknown",
        "ADOPTING": "unknown",
        "DELETING": "unknown",
    }
    assert len(NETWORK_DEVICE_STATES) == len(dict(NETWORK_DEVICE_STATES))


@pytest.mark.parametrize(("state", "expected"), NETWORK_DEVICE_STATES)
def test_every_spec_state_is_mapped(state: str, expected: str) -> None:
    """Every spec state resolves through the tuple, in state and status."""
    assert device_online_state({"state": state}) == expected
    assert device_online_state({"status": state.lower()}) == expected


# The library's DeviceState values when this test was written. A library bump
# that adds or renames one fails here, so the new value gets a decision. The
# library spells the spec's ADOPTING in lower case.
_KNOWN_LIBRARY_STATES = frozenset(
    {
        *(state for state, _ in NETWORK_DEVICE_STATES if state != "ADOPTING"),
        "connected",
        "disconnected",
        "pending",
        "adopting",
        "provisioning",
        "upgrading",
        "unknown",
    }
)


def test_library_device_states_all_resolve() -> None:
    """Every library DeviceState member resolves, and none is new."""
    assert {member.value for member in DeviceState} == _KNOWN_LIBRARY_STATES
    spec = dict(NETWORK_DEVICE_STATES)
    for member in DeviceState:
        resolved = device_online_state({"state": member})
        assert resolved in {"online", "offline", "unknown"}
        assert resolved == spec.get(member.value.upper(), "unknown")


class _PlainState(Enum):
    """A non-str enum, as a decoded value that is not a StrEnum."""

    ONLINE = "ONLINE"
    BROKEN = 1


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        ({"state": "CONNECTED"}, "unknown"),
        ({"state": "UP"}, "unknown"),
        ({"state": "connected"}, "unknown"),
        ({"state": "up"}, "unknown"),
        ({"state": "DISCONNECTED"}, "unknown"),
        ({"state": "DOWN"}, "unknown"),
        ({}, "unknown"),
        ({"state": None}, "unknown"),
        ({"state": None, "status": "ONLINE"}, "online"),
        ({"state": "nonsense"}, "unknown"),
        ({"state": 1}, "unknown"),
        ({"state": ""}, "unknown"),
        ({"state": "online"}, "online"),
        ({"state": "  OFFLINE \n"}, "offline"),
        ({"state": DeviceState.ISOLATED}, "offline"),
        ({"state": _PlainState.ONLINE}, "online"),
        ({"state": _PlainState.BROKEN}, "unknown"),
        ({"status": "offline"}, "offline"),
        ({"state": "ONLINE", "status": "offline"}, "online"),
    ],
)
def test_online_state_edge_inputs(device: dict[str, Any], expected: str) -> None:
    """Non-spec, absent, invalid, cased, padded and enum inputs."""
    assert device_online_state(device) == expected


@pytest.mark.parametrize("field", ["state", "status"])
@pytest.mark.parametrize("state", [state for state, _ in NETWORK_DEVICE_STATES])
@pytest.mark.parametrize("as_enum", [False, True])
def test_serviceable_state_matrix(field: str, state: str, *, as_enum: bool) -> None:
    """Only online and GETTING_READY remain eligible through either alias/enum."""
    raw = Enum("RawState", {"VALUE": state}).VALUE if as_enum else state
    record = {field: raw}
    expected = state in {"ONLINE", "GETTING_READY"}
    assert device_serviceable(record) is expected
    published = build_site_network_facts({"device": record}, {})
    facts = published.devices["device"]
    assert facts.serviceable is expected
    assert facts.online_state == dict(NETWORK_DEVICE_STATES)[state]
    assert site_device_facts(published, "device", record) is facts
    assert site_device_facts(None, "device", record).serviceable is expected


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"state": " getting_ready ", "status": "OFFLINE"}, True),
        ({"state": " offline ", "status": "GETTING_READY"}, False),
        ({"state": None, "status": " getting_ready "}, True),
        ({"state": " online "}, True),
        ({"state": "", "status": "ONLINE"}, False),
        ({"state": 1, "status": "GETTING_READY"}, False),
        ({"state": [], "status": "ONLINE"}, False),
        ({"state": {"value": "GETTING_READY"}}, False),
        ({"state": "nonsense", "status": "ONLINE"}, False),
        ({"state": False}, False),
        ({"state": None}, False),
        ({}, False),
    ],
)
def test_serviceable_normalization(record: dict[str, Any], *, expected: bool) -> None:
    """Invalid non-None state masks status; normalization preserves precedence."""
    assert device_serviceable(record) is expected
    assert entity.is_device_serviceable(record) is expected


# --- Kind -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        pytest.param(SAMPLE_UDM_PRO_OVERVIEW, "gateway", id="null-type-udm-pro"),
        pytest.param(UDM_PRO_LEGACY_ENRICHED, "gateway", id="legacy-enriched"),
        pytest.param(LEGACY_ONLY_SWITCH, "switch", id="legacy-only-switch"),
        pytest.param(SAMPLE_NETWORK_DEVICE, "switch", id="compat-switch"),
        pytest.param(SAMPLE_PDU_DEVICE, "other", id="pdu"),
        pytest.param({"topology": {"legacy_type": "UDR"}}, "gateway", id="legacy-gw"),
        pytest.param({"topology": {"legacy_type": "uap"}}, "access_point", id="uap"),
        pytest.param({"topology": "bad", "type": "Gateway"}, "gateway", id="v1-gw"),
        pytest.param({"type": "switch"}, "switch", id="v1-switch"),
        pytest.param({"type": "Access Point"}, "access_point", id="v1-ap"),
        pytest.param({"type": "uap"}, "access_point", id="v1-uap"),
        pytest.param({"type": "pdu"}, "other", id="v1-other"),
        pytest.param({"model": "Cloud Gateway Max"}, "gateway", id="model-text"),
        pytest.param({"model": "UCG-Fiber"}, "gateway", id="model-prefix"),
        pytest.param(
            {"model": "USW Flex", "features": ["switching", "gateway"]},
            "gateway",
            id="gateway-feature-list",
        ),
        pytest.param(
            {"model": "USW Flex", "features": {"switching": {}, "gateway": {}}},
            "gateway",
            id="gateway-feature-object",
        ),
        pytest.param(
            {"model": "USW Flex", "features": ["router"]}, "gateway", id="router"
        ),
        pytest.param(
            {"features": {"switching": {"lags": []}, "accessPoint": None}},
            "switch",
            id="details-switch",
        ),
        pytest.param({"features": {"accessPoint": {}}}, "access_point", id="ap-obj"),
        pytest.param({"features": {"gateway": False}}, "other", id="false-feature"),
        pytest.param({"features": ["accessPoint"]}, "access_point", id="ap-list"),
        pytest.param({"features": "switching"}, "other", id="bad-features"),
        pytest.param(
            {"model": "USW Flex", "features": ("switching", "gateway")},
            "gateway",
            id="gateway-feature-tuple",
        ),
        pytest.param({"features": ("switching",)}, "switch", id="switch-tuple"),
        pytest.param({"model": 5}, "other", id="bad-model"),
    ],
)
def test_device_kind(device: dict[str, Any], expected: str) -> None:
    """Gateway checks run before access point and switching checks."""
    assert device_kind(device) == expected


# --- Ports ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (3, 3),
        (0, 0),
        ("03", 3),
        (" 3 ", 3),
        ("x", None),
        ("", None),
        (True, None),
        (3.0, None),
        (None, None),
    ],
)
def test_port_index(raw: Any, expected: int | None) -> None:
    """Ints and numeric strings normalise with int(); nothing else does."""
    assert port_index(raw) == expected


def _facts(device: dict[str, Any], stats: dict[str, Any] | None = None) -> Any:
    """Build one device's facts through the site builder."""
    site = build_site_network_facts({"dev": device}, {"dev": stats or {}})
    return site.devices["dev"]


def test_ports_come_from_the_legacy_merge() -> None:
    """Legacy-enriched ports keep source order, raw spelling and the uplink."""
    facts = _facts(UDM_PRO_LEGACY_ENRICHED)

    assert [port.raw_index for port in facts.ports] == [1, 9, 11]
    assert [port.is_uplink for port in facts.ports] == [False, True, False]
    assert facts.port_by_index(9) is facts.ports[1]
    assert facts.port_by_index(2) is None
    assert facts.ports[2].record["sfp_found"] is False


def test_v1_overview_has_no_ports() -> None:
    """A list-shaped interfaces and an empty ports list yield no ports."""
    assert _facts(SAMPLE_UDM_PRO_OVERVIEW).ports == ()


def test_port_index_spellings_zero_and_duplicates() -> None:
    """Raw spellings are kept, zero is a port, and the first record wins."""
    device = {
        "ports": [
            {"idx": 0, "name": "zero"},
            {"port_idx": "03", "name": "padded"},
            {"portIdx": " 3 ", "name": "spaced"},
            {"index": 4, "isUplink": True},
            {"idx": True},
            {"idx": None, "port_idx": 5, "name": "first-5"},
            {"idx": 5, "name": "second-5"},
            "not-a-port",
        ]
    }
    facts = _facts(device)

    assert [port.raw_index for port in facts.ports] == [0, "03", " 3 ", 4, True, 5, 5]
    assert [port.index for port in facts.ports] == [0, 3, 3, 4, None, 5, 5]
    assert facts.port_by_index(0).record["name"] == "zero"
    assert facts.port_by_index(3).record["name"] == "padded"
    assert facts.port_by_index(5).record["name"] == "first-5"
    assert facts.port_by_raw_index(" 3 ").record["name"] == "spaced"
    assert facts.port_by_raw_index("03").record["name"] == "padded"
    assert facts.port_by_raw_index(3) is None
    assert facts.port_by_raw_index(1) is None
    flag = True
    assert facts.port_by_raw_index(flag).raw_index is True
    assert facts.ports[3].is_uplink is True


@pytest.mark.parametrize(
    ("device", "names"),
    [
        pytest.param(
            {"ports": [], "interfaces": {"ports": [{"idx": 1, "name": "v1"}]}},
            ["v1"],
            id="details-interfaces",
        ),
        pytest.param(
            {
                "ports": [{"idx": 1, "name": "legacy"}],
                "interfaces": {"ports": [{"idx": 2, "name": "v1"}]},
            },
            ["legacy"],
            id="no-merge",
        ),
        pytest.param({"interfaces": ["ports", "radios"]}, [], id="overview-list"),
        pytest.param({"interfaces": {"ports": "bad"}}, [], id="bad-interfaces"),
        pytest.param({"ports": "bad"}, [], id="bad-ports"),
    ],
)
def test_port_source(device: dict[str, Any], names: list[str]) -> None:
    """Top-level ports win; mapping interfaces.ports is the only fallback."""
    assert [port.record["name"] for port in _facts(device).ports] == names


def test_port_records_are_read_only_copies() -> None:
    """A port record cannot be changed and does not follow its source."""
    source = {"idx": 1, "name": "a"}
    facts = _facts({"ports": [source]})
    record = facts.ports[0].record

    with pytest.raises(TypeError):
        record["name"] = "b"  # type: ignore[index]
    source["name"] = "changed"
    assert record["name"] == "a"


# --- Metrics --------------------------------------------------------------


@pytest.mark.parametrize(
    ("stats", "expected"),
    [
        pytest.param(
            {"uplink": {"txRateBps": 100, "rxRateBps": 50}}, (100, 50), id="public"
        ),
        pytest.param(
            {"uplink": {"tx_rate_bps": 100, "rx_rate_bps": 50}}, (100, 50), id="snake"
        ),
        pytest.param(
            {"uplink": {"tx_bytes_per_sec": 100, "rx_bytes_per_sec": 50}},
            (800, 400),
            id="bytes",
        ),
        pytest.param({"txRateBps": 100, "rxRateBps": 50}, (100, 50), id="top-level"),
        pytest.param(
            {"uplink": {"txRateBps": 1}, "txRateBps": 2, "rx_bytes_per_sec": 3},
            (1, 24),
            id="uplink-first",
        ),
        pytest.param(
            {"uplink": {"txRateBps": None, "tx_bytes_per_sec": 2}}, (16, None), id="n"
        ),
        pytest.param(
            {"uplink_stats": {"txRateBps": 7, "rxRateBps": 8}}, (7, 8), id="alias"
        ),
        pytest.param({"uplink": "bad", "txRateBps": 9}, (9, None), id="bad-uplink"),
        pytest.param(ZERO_STATS, (0, 0), id="zeros"),
        pytest.param(MALFORMED_STATS, (None, None), id="malformed"),
        pytest.param(
            {"uplink": {"txRateBps": "bad"}, "txRateBps": 5}, (None, None), id="no-fall"
        ),
        pytest.param({"tx_rate": 1, "txRate": 2, "uplinkTxRate": 3}, (None, None)),
        pytest.param({}, (None, None), id="empty"),
    ],
)
def test_uplink_rate_bps(stats: dict[str, Any], expected: tuple[Any, Any]) -> None:
    """Public rates are bits/s; only the explicit bytes/s keys are x8."""
    assert (uplink_rate_bps(stats, "tx"), uplink_rate_bps(stats, "rx")) == expected


@pytest.mark.parametrize(
    ("device", "stats", "expected"),
    [
        pytest.param(
            SAMPLE_UDM_PRO_OVERVIEW,
            SAMPLE_UDM_PRO_STATS,
            (14.6, 62.5, 3333412, 35072.0, 74016.0),
            id="captured",
        ),
        pytest.param({}, ZERO_STATS, (0.0, 0.0, 0, 0.0, 0.0), id="zeros"),
        pytest.param(
            {}, SNAKE_AND_BYTES_STATS, (7.5, 40.0, 3600, 100.0, 400.0), id="snake"
        ),
        pytest.param(
            {}, MALFORMED_STATS, (None, None, None, None, None), id="malformed"
        ),
        pytest.param(
            {"cpu": 1, "memory": 2, "uptime": 3},
            {},
            (1.0, 2.0, 3, None, None),
            id="device-record-fallback",
        ),
        pytest.param(
            {"cpu": 1},
            {"cpuUtilizationPct": 9},
            (9.0, None, None, None, None),
            id="stats-first",
        ),
    ],
)
def test_metrics(
    device: dict[str, Any], stats: dict[str, Any], expected: tuple[Any, ...]
) -> None:
    """Metrics read stats then device, keep zeros and reject junk."""
    facts = _facts(device, stats)
    assert (
        facts.cpu_pct,
        facts.memory_pct,
        facts.uptime_s,
        facts.tx_bps,
        facts.rx_bps,
    ) == expected
    assert facts.uptime_s is None or isinstance(facts.uptime_s, int)


# --- Client connection type -----------------------------------------------


@pytest.mark.parametrize(
    ("client", "expected"),
    [
        ({"type": "WIRED"}, "wired"),
        ({"type": "wireless"}, "wireless"),
        ({"type": " Wired "}, "wired"),
        ({"type": "ClientType.WIRELESS"}, "wireless"),
        ({"type": ClientType.WIRED}, "wired"),
        ({"type": None, "connection_type": "WIRELESS"}, "wireless"),
        ({"connection_type": "wired"}, "wired"),
        ({"type": "VPN"}, None),
        ({"type": "WIRED_GUEST"}, None),
        ({"type": "UNKNOWN_WIRELESS"}, None),
        ({"type": ""}, None),
        ({"type": 1}, None),
        ({}, None),
    ],
)
def test_client_connection_type(client: dict[str, Any], expected: Any) -> None:
    """Recognition is exact after enum unwrap and prefix removal."""
    assert client_connection_type(client) == expected


@pytest.mark.parametrize(
    ("client", "expected"),
    [
        ({"id": "client_1", "mac": "aa:bb:cc:dd:ee:ff"}, True),
        ({"connected": None}, True),
        ({"connected": True}, True),
        ({"connected": False}, False),
        ({"connected": 1}, True),
        ({"connected": 0}, False),
        ({"connected": "true"}, True),
        ({"connected": ""}, False),
        ({}, False),
        (None, False),
    ],
)
def test_client_is_connected(client: dict[str, Any] | None, expected: Any) -> None:
    """Helper recognizes snapshot membership and explicit connected states."""
    assert client_is_connected(client) is expected


def test_client_is_connected_does_not_mutate() -> None:
    """Helper does not mutate input record."""
    original = {"id": "c1", "mac": "aa:bb:cc:dd:ee:ff", "name": "device"}
    snapshot = copy.deepcopy(original)
    assert client_is_connected(original) is True
    assert original == snapshot


# --- Builder and table rows -----------------------------------------------


@pytest.mark.parametrize(
    ("device", "stats", "expected"),
    [
        pytest.param(
            LEGACY_ONLY_SWITCH,
            {},
            ("online", "switch", False, [2], "aa:bb:cc:00:00:e1"),
            id="legacy-only",
        ),
        pytest.param(
            SAMPLE_UDM_PRO_OVERVIEW,
            SAMPLE_UDM_PRO_STATS,
            ("online", "gateway", True, [], "aa:bb:cc:00:00:d1"),
            id="v1-only-null-type",
        ),
        pytest.param(
            UDM_PRO_LEGACY_ENRICHED,
            SAMPLE_UDM_PRO_STATS,
            ("online", "gateway", True, [1, 9, 11], "aa:bb:cc:00:00:d1"),
            id="both",
        ),
        pytest.param(
            SAMPLE_NETWORK_DEVICE,
            ZERO_STATS,
            ("online", "switch", False, [], "00:11:22:33:44:66"),
            id="compat-explicit-zeros",
        ),
        pytest.param(
            {**SAMPLE_PDU_DEVICE, "state": "bad", "mac": "junk"},
            MALFORMED_STATS,
            ("unknown", "other", False, [], None),
            id="malformed",
        ),
    ],
)
def test_read_model_rows(
    device: dict[str, Any], stats: dict[str, Any], expected: tuple[Any, ...]
) -> None:
    """One row per source mix: legacy only, v1 only, both, zeros, malformed."""
    facts = _facts(device, stats)
    assert (
        facts.online_state,
        facts.kind,
        facts.is_site_gateway,
        [port.raw_index for port in facts.ports],
        facts.mac,
    ) == expected


def test_legacy_pdu_fixture_has_no_ports_or_gateway() -> None:
    """The legacy PDU record is not a gateway and carries no port table."""
    facts = _facts(SAMPLE_PDU_LEGACY_DEVICE)
    assert facts.kind == "other"
    assert facts.ports == ()
    assert facts.mac == "00:11:22:33:44:55"


def test_site_gateway_is_first_gateway_in_map_order() -> None:
    """The first gateway in map order is the site gateway; others are not."""
    devices = {
        "switch": SAMPLE_NETWORK_DEVICE,
        "udm": SAMPLE_UDM_PRO_OVERVIEW,
        "second": {"model": "UXG-Lite"},
        7: {"model": "U6 Pro", "features": ["accessPoint"]},
        "junk": "not-a-device",
    }
    site = build_site_network_facts(devices, {"udm": SAMPLE_UDM_PRO_STATS, 7: "x"})

    assert site.gateway_id == "udm"
    assert list(site.devices) == ["switch", "udm", "second", "7"]
    assert site.devices["udm"].is_site_gateway is True
    assert site.devices["second"].kind == "gateway"
    assert site.devices["second"].is_site_gateway is False
    assert site.devices["udm"].tx_bps == 35072.0
    assert site.devices["7"].device_id == "7"
    assert site.devices["7"].cpu_pct is None


def test_builder_without_gateway_or_inputs() -> None:
    """No gateway gives None; non-mapping inputs give an empty site."""
    assert build_site_network_facts({"a": SAMPLE_NETWORK_DEVICE}, {}).gateway_id is None
    empty = build_site_network_facts(None, None)
    assert empty.gateway_id is None
    assert dict(empty.devices) == {}


def test_builder_does_not_mutate_inputs_and_is_read_only() -> None:
    """The builder leaves its inputs alone and returns frozen records."""
    devices = {"udm": copy.deepcopy(UDM_PRO_LEGACY_ENRICHED)}
    stats = {"udm": copy.deepcopy(SAMPLE_UDM_PRO_STATS)}
    before = copy.deepcopy((devices, stats))

    site = build_site_network_facts(devices, stats)

    assert (devices, stats) == before
    with pytest.raises(TypeError):
        site.devices["other"] = site.devices["udm"]  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        site.gateway_id = "x"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        site.devices["udm"].kind = "switch"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        site.devices["udm"].ports[0].index = 5  # type: ignore[misc]
    assert isinstance(site.devices["udm"].ports[0], NetworkPortFacts)


# --- Accessors ------------------------------------------------------------


def _raw_data() -> dict[str, Any]:
    """Coordinator-shaped data without published facts."""
    return {
        "devices": {"site": {"udm": SAMPLE_UDM_PRO_OVERVIEW}},
        "stats": {"site": {"udm": SAMPLE_UDM_PRO_STATS}},
    }


def test_accessor_returns_published_facts_without_rebuilding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Published facts come back by identity and the builder is not called."""
    published = build_site_network_facts(
        {"udm": SAMPLE_UDM_PRO_OVERVIEW}, {"udm": SAMPLE_UDM_PRO_STATS}
    )
    data = {**_raw_data(), NETWORK_FACTS_KEY: {"site": published}}

    def _fail(*_args: Any) -> SiteNetworkFacts:
        raise AssertionError

    monkeypatch.setattr(
        "custom_components.unifi_insights.data_transforms.build_site_network_facts",
        _fail,
    )

    assert site_network_facts(data, "site") is published
    assert device_facts(data, "site", "udm") is published.devices["udm"]
    assert site_network_facts(data, "other") is None
    assert device_facts(data, "other", "udm") is None
    assert site_network_facts({NETWORK_FACTS_KEY: {"site": "bad"}}, "site") is None


def test_accessor_falls_back_to_the_builder(caplog: pytest.LogCaptureFixture) -> None:
    """Without published facts the same builder runs and says so at debug."""
    with caplog.at_level(
        logging.DEBUG, logger="custom_components.unifi_insights.data_transforms"
    ):
        site = site_network_facts(_raw_data(), "site")

    assert site is not None
    assert site.gateway_id == "udm"
    assert "Building unpublished network facts for site site" in caplog.text
    facts = device_facts({**_raw_data(), NETWORK_FACTS_KEY: None}, "site", "udm")
    assert isinstance(facts, NetworkDeviceFacts)
    assert device_facts(_raw_data(), "site", "missing") is None
    assert site_network_facts({"devices": {"site": "bad"}}, "site") is None
    assert site_network_facts({}, "site") is None
    no_stats = site_network_facts({"devices": _raw_data()["devices"]}, "site")
    assert no_stats is not None
    assert no_stats.devices["udm"].cpu_pct is None


# --- Access point radios (#454) -------------------------------------------

RADIO_2_4 = {
    "frequencyGHz": 2.4,
    "channel": 6,
    "channelWidthMHz": 40,
    "txPowerMode": "AUTO",
    "wlanStandard": "802.11ax",
    "txRetriesPct": 3.5,
}
RADIO_5 = {
    "frequencyGHz": 5,
    "channel": 44,
    "channelWidthMHz": 80,
    "txPowerMode": "HIGH",
    "wlanStandard": "802.11ac",
    "txRetriesPct": 0,
}


@pytest.mark.parametrize(
    ("frequency", "expected"),
    [
        (0.9, "900mhz"),
        (2.4, "2_4ghz"),
        ("5", "5ghz"),
        (6.0, "6ghz"),
        (60, "60ghz"),
        (True, None),
        (None, None),
        (3, None),
        ("fast", None),
        (float("nan"), None),
    ],
)
def test_radio_band_slug_normalises_spec_frequencies(
    frequency: Any, expected: str | None
) -> None:
    """Numeric freqs and numeric strings map; booleans and strangers do not."""
    assert radio_band_slug(frequency) == expected


def test_radio_band_labels_cover_every_band() -> None:
    """Every band slug has a display label, including the 900 MHz band."""
    assert RADIO_BAND_LABELS == {
        "900mhz": "900 MHz",
        "2_4ghz": "2.4 GHz",
        "5ghz": "5 GHz",
        "6ghz": "6 GHz",
        "60ghz": "60 GHz",
    }


def test_radio_band_map_reads_the_interfaces_mapping() -> None:
    """The statistics/details shape nests radios under interfaces."""
    radios = radio_band_map({"radios": [RADIO_2_4, RADIO_5]})

    assert list(radios) == ["2_4ghz", "5ghz"]
    assert radios["2_4ghz"]["channel"] == 6
    assert radios["5ghz"]["wlanStandard"] == "802.11ac"
    with pytest.raises(TypeError):
        radios["6ghz"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        radios["2_4ghz"]["channel"] = 1  # type: ignore[index]


def test_radio_band_map_accepts_a_bare_radios_list() -> None:
    """The details record stores the bare list, not the interfaces wrapper."""
    assert set(radio_band_map([RADIO_5])) == {"5ghz"}


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"radios": None},
        {"radios": {}},
        {"radios": "radios"},
        ["ports", "radios"],
        42,
    ],
)
def test_radio_band_map_returns_empty_for_other_shapes(payload: Any) -> None:
    """The overview's list-shaped interfaces has no radios and never raises."""
    assert dict(radio_band_map(payload)) == {}


def test_radio_band_map_skips_bad_and_duplicate_radios(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Bad entries and repeated bands are skipped at debug, first band wins."""
    with caplog.at_level(
        logging.DEBUG, logger="custom_components.unifi_insights.data_transforms"
    ):
        radios = radio_band_map(
            [
                "not a radio",
                {"frequencyGHz": None},
                {"frequencyGHz": 3},
                RADIO_2_4,
                {**RADIO_2_4, "channel": 11},
            ]
        )

    assert list(radios) == ["2_4ghz"]
    assert radios["2_4ghz"]["channel"] == 6
    assert "Skipping radio entry that is not a mapping" in caplog.text
    assert "Skipping radio with unknown frequency: None" in caplog.text
    assert "Skipping radio with unknown frequency: 3" in caplog.text
    assert "Skipping duplicate 2_4ghz radio" in caplog.text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("AUTO", "auto"),
        ("low", "low"),
        (" Medium ", "medium"),
        ("HIGH", "high"),
        ("CUSTOM", "custom"),
        ("DISABLED", "disabled"),
        ("TURBO", None),
        (None, None),
        (1, None),
    ],
)
def test_tx_power_mode_slug(value: Any, expected: str | None) -> None:
    """Every spec TX power mode becomes a valid translation state key."""
    assert tx_power_mode_slug(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("802.11a", "802_11a"),
        ("802.11b", "802_11b"),
        ("802.11g", "802_11g"),
        ("802.11n", "802_11n"),
        ("802.11ac", "802_11ac"),
        (" 802.11AX ", "802_11ax"),
        ("802.11be", "802_11be"),
        ("802.11ah", "802_11ah"),
        ("802.11ad", None),
        (None, None),
    ],
)
def test_wlan_standard_slug(value: Any, expected: str | None) -> None:
    """Every spec Wi-Fi standard becomes a dot-free translation state key."""
    assert wlan_standard_slug(value) == expected


def test_radio_option_tuples_are_the_translation_keys() -> None:
    """The shared option tuples carry no dots, as HA state keys require."""
    assert RADIO_TX_POWER_MODE_OPTIONS == (
        "auto",
        "low",
        "medium",
        "high",
        "custom",
        "disabled",
    )
    assert RADIO_WLAN_STANDARD_OPTIONS == (
        "802_11a",
        "802_11b",
        "802_11g",
        "802_11n",
        "802_11ac",
        "802_11ax",
        "802_11be",
        "802_11ah",
    )
    for option in (*RADIO_TX_POWER_MODE_OPTIONS, *RADIO_WLAN_STANDARD_OPTIONS):
        assert option.replace("_", "").replace("80211", "").isalnum()


def test_radio_field_readers() -> None:
    """Readers unwrap the record fields and keep a real zero."""
    assert radio_tx_retries(RADIO_5) == 0
    assert radio_tx_retries(RADIO_2_4) == 3.5
    assert radio_tx_retries({}) is None
    assert radio_tx_retries({"txRetriesPct": "nope"}) is None
    assert radio_tx_power_mode(RADIO_2_4) == "auto"
    assert radio_tx_power_mode({}) is None
    assert radio_wlan_standard(RADIO_5) == "802_11ac"
    assert radio_wlan_standard({}) is None
    assert radio_channel(RADIO_5) == 44
    assert radio_channel({}) is None
    assert radio_channel({"channel": 6.0}) == 6
    assert radio_channel_width(RADIO_5) == 80
    assert radio_channel_width({"channel_width_mhz": 160}) == 160
    assert radio_channel_width({"channelWidthMHz": True}) is None


def test_network_radio_stats_reads_published_statistics() -> None:
    """The statistics accessor returns the device's radios by band."""
    data = {"stats": {"site": {"dev": {"interfaces": {"radios": [RADIO_2_4]}}}}}

    radios = network_radio_stats(data, "site", "dev")

    assert radios is not None
    assert radios["2_4ghz"]["txRetriesPct"] == 3.5
    assert network_radio_stats(data, "site", "missing") is None
    assert (
        network_radio_stats(
            {"stats": {"site": {"dev": {"interfaces": ["ports", "radios"]}}}},
            "site",
            "dev",
        )
        == {}
    )


def test_network_radio_details_returns_the_record_or_none() -> None:
    """The details accessor returns the per-device record, or None."""
    record = {"radios": [RADIO_5], "available": True}
    data: dict[str, Any] = {"radio_details": {"site": {"dev": record}}}

    assert network_radio_details(data, "site", "dev") is record
    assert network_radio_details(data, "site", "missing") is None
    assert network_radio_details({}, "site", "dev") is None
    assert network_radio_details({"radio_details": {"site": []}}, "site", "dev") is None
