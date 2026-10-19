# Copyright (c) 2026 Ruaan Deysel

"""Tests for dashboard snapshot builder modules."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api.protect.models.camera import (
    CameraState,
    CameraType,
    RecordingMode,
)

from custom_components.unifi_insights.console_identity import (
    build_legacy_site_unique_id,
    build_site_unique_id,
)
from custom_components.unifi_insights.const import CONF_CONSOLE_ID, DOMAIN
from custom_components.unifi_insights.dashboard_contract_utils import (
    content_revision,
    enum_str,
    is_protect_device_connected,
    utc_iso,
)
from custom_components.unifi_insights.internet_activity_snapshot import (
    WINDOWS,
    build_internet_activity_snapshot,
    build_unavailable_internet_activity,
)
from custom_components.unifi_insights.performance import (
    _metric_rate,
    build_performance_snapshot,
    build_unavailable_performance,
    bytes_per_second_to_bits_per_second,
)
from custom_components.unifi_insights.protect_snapshot import (
    build_no_protect_snapshot,
    build_protect_snapshot,
    build_unavailable_protect_snapshot,
)
from custom_components.unifi_insights.site_health import (
    _gateway_for_site,
    _wan_state,
    build_site_health_snapshot,
    build_unavailable_site_health,
)
from custom_components.unifi_insights.timeline import (
    build_timeline_snapshot,
    build_unavailable_timeline,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class _FakeEntityRegistry:
    def async_get(self, entity_id):
        return SimpleNamespace(
            config_entry_id="entry-1", translation_key="internet_download_1h"
        )

    def async_get_entity_id(
        self,
        domain: str,
        platform: str,
        unique_id: str,
    ) -> str | None:
        if unique_id == "site-1_internet_download_1h":
            return "sensor.internet_download_1h"
        return None


def _base_data() -> dict:
    return {
        "sites": {"site-1": {"name": "Home"}},
        "devices": {
            "site-1": {
                "gw-1": {
                    "id": "gw-1",
                    "name": "Gateway",
                    "type": "gateway",
                    "state": "ONLINE",
                    "topology": {"legacy_type": "udm"},
                    "wans": [{"wan_key": "WAN1", "connected": True}],
                    "macAddress": "AA:BB:CC:DD:EE:FF",
                },
                "ap-1": {
                    "id": "ap-1",
                    "name": "AP",
                    "type": "access_point",
                    "state": "ONLINE",
                    "topology": {"legacy_type": "uap"},
                },
            }
        },
        "clients": {
            "site-1": {
                "c1": {"type": "WIRED"},
                "c2": {"type": "WIRELESS"},
            }
        },
        "stats": {
            "site-1": {
                "gw-1": {
                    "cpuUtilizationPct": 12.5,
                    "memoryUtilizationPct": 42.0,
                    "tx_rate": 100,
                    "rx_rate": 50,
                },
                "ap-1": {
                    "cpuUtilizationPct": 30,
                    "memoryUtilizationPct": 20,
                    "tx_rate": 10,
                    "rx_rate": 20,
                    "num_sta": 2,
                },
            }
        },
        "internet_activity": {
            "site-1": {
                "1h": {"rx_bytes": 1000, "tx_bytes": 500},
                "1d": {"rx_bytes": 10_000, "tx_bytes": 5_000},
            }
        },
        "internet_activity_unavailable": set(),
        "protect": {
            "cameras": {
                "cam-1": {
                    "name": "Front Door",
                    "type": "UVC-G4-DOORBELL",
                    "state": "CONNECTED",
                    "lastRingStart": int(datetime.now(tz=UTC).timestamp() * 1000),
                    "lastSmartDetectTypes": ["person"],
                    "mac": "AA:BB:CC:DD:EE:FF",
                }
            },
            "chimes": {},
            "nvrs": {},
            "events": {
                "motion": {
                    "evt-1": {
                        "id": "evt-1",
                        "device_id": "cam-1",
                        "name": "Front Door",
                        "timestamp": int(datetime.now(tz=UTC).timestamp() * 1000),
                    }
                }
            },
        },
    }


def test_build_site_health_snapshot() -> None:
    data = _base_data()
    snapshot = build_site_health_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        ha_device_ids={"gw-1": "dev-1"},
        devices_available=True,
    )

    assert snapshot["status"] == "ok"
    assert snapshot["site_name"] == "Home"
    assert snapshot["health"]["level"] == "healthy"
    assert snapshot["gateway"]["ha_device_id"] == "dev-1"
    assert snapshot["clients"] == {"total": 2, "wired": 1, "wireless": 1}
    assert isinstance(snapshot["revision"], str)
    assert snapshot["revision"]


def test_build_internet_activity_snapshot() -> None:
    data = _base_data()
    snapshot = build_internet_activity_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        entity_registry=_FakeEntityRegistry(),
    )

    assert snapshot["status"] == "ok"
    assert snapshot["windows"]["1h"]["download_bytes"] == 1000
    assert snapshot["windows"]["1h"]["upload_bytes"] == 500
    assert snapshot["entity_ids"]["download_1h"] == "sensor.internet_download_1h"


async def test_internet_activity_snapshot_namespaces_shared_site_registry_rows(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Each console's complete activity snapshot resolves only its owned rows."""
    snapshots = []
    expected_ids = []
    for index, namespace in enumerate(("console_a", "console_b"), start=1):
        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id=f"entry_{namespace}",
            unique_id=namespace,
            data={CONF_CONSOLE_ID: namespace},
        )
        entry.add_to_hass(hass)
        owned_ids = {}
        for direction in ("download", "upload"):
            for window in WINDOWS:
                key = f"internet_{direction}_{window}"
                row = entity_registry.async_get_or_create(
                    "sensor",
                    DOMAIN,
                    build_site_unique_id(namespace, "site-1", key),
                    config_entry=entry,
                    translation_key=key,
                )
                owned_ids[f"{direction}_{window}"] = row.entity_id
        data = _base_data()
        data["internet_activity"]["site-1"] = {
            window: {"rx_bytes": index * 1000, "tx_bytes": index * 500}
            for window in WINDOWS
        }
        registry_before = dict(entity_registry.entities)

        snapshot = build_internet_activity_snapshot(
            data,
            entry_id=entry.entry_id,
            site_id="site-1",
            entity_registry=entity_registry,
            namespace=namespace,
        )

        assert snapshot["status"] == "ok"
        assert snapshot["entry_id"] == entry.entry_id
        assert snapshot["site_id"] == "site-1"
        assert snapshot["entity_ids"] == owned_ids
        assert snapshot["windows"]["1h"]["download_bytes"] == index * 1000
        assert all(
            entity_registry.async_get(entity_id).config_entry_id == entry.entry_id
            for entity_id in snapshot["entity_ids"].values()
        )
        assert dict(entity_registry.entities) == registry_before
        assert set(data["sites"]) == {"site-1"}
        snapshots.append(snapshot)
        expected_ids.append(set(owned_ids.values()))

    assert len(expected_ids[0]) == len(expected_ids[1]) == 8
    assert expected_ids[0].isdisjoint(expected_ids[1])
    assert snapshots[0]["revision"] != snapshots[1]["revision"]


@pytest.mark.parametrize(
    ("legacy_owner", "target_owner", "expected_candidate"),
    [
        ("own", "foreign", "legacy"),
        ("own", "own", "legacy"),
        ("foreign", "own", "target"),
        ("foreign", "foreign", None),
        (None, "foreign", None),
        (None, "own", "target"),
    ],
)
async def test_internet_activity_snapshot_preserves_candidate_ownership(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    legacy_owner: str | None,
    target_owner: str,
    expected_candidate: str | None,
) -> None:
    """Occupied identities retain an owned legacy row or exclude foreign rows."""
    entries = {}
    for owner, namespace in (("own", "console_a"), ("foreign", "console_b")):
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=namespace,
            data={CONF_CONSOLE_ID: namespace},
        )
        entry.add_to_hass(hass)
        entries[owner] = entry
    key = "internet_download_1h"
    candidates = {}
    if legacy_owner is not None:
        candidates["legacy"] = entity_registry.async_get_or_create(
            "sensor",
            DOMAIN,
            build_legacy_site_unique_id("site-1", key),
            config_entry=entries[legacy_owner],
            translation_key=key,
        )
    candidates["target"] = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        build_site_unique_id("console_a", "site-1", key),
        config_entry=entries[target_owner],
        translation_key=key,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    candidates["target"] = entity_registry.async_update_entity(
        candidates["target"].entity_id, name="Customized activity sensor"
    )
    registry_before = dict(entity_registry.entities)

    snapshot = build_internet_activity_snapshot(
        _base_data(),
        entry_id=entries["own"].entry_id,
        site_id="site-1",
        entity_registry=entity_registry,
        namespace="console_a",
    )

    expected = (
        {"download_1h": candidates[expected_candidate].entity_id}
        if expected_candidate is not None
        else {}
    )
    assert snapshot["entity_ids"] == expected
    assert dict(entity_registry.entities) == registry_before
    assert all(
        entity_registry.async_get(entity_id).config_entry_id == entries["own"].entry_id
        for entity_id in snapshot["entity_ids"].values()
    )


def test_build_performance_snapshot() -> None:
    data = _base_data()
    snapshot = build_performance_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        ha_device_ids={"gw-1": "ha-gw-1"},
        devices_available=True,
    )

    assert snapshot["status"] == "ok"
    assert len(snapshot["devices"]) == 2
    gateway = next(item for item in snapshot["devices"] if item["id"] == "site-1:gw-1")
    assert gateway["ha_device_id"] == "ha-gw-1"
    assert gateway["tx_bps"] == 800
    assert gateway["rx_bps"] == 400


def test_build_protect_snapshot() -> None:
    data = _base_data()
    snapshot = build_protect_snapshot(
        data,
        entry_id="entry-1",
        entry_title="Console",
        entity_registry=_FakeEntityRegistry(),
        ha_device_ids={"cam-1": "ha-cam-1"},
        protect_available=True,
    )

    assert snapshot["status"] == "ok"
    assert snapshot["entry_title"] == "Console"
    assert any(device["id"] == "cam-1" for device in snapshot["devices"])


def test_build_timeline_snapshot() -> None:
    data = _base_data()
    snapshot = build_timeline_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        categories=["security"],
        hours=6,
        max_items=5,
    )

    assert snapshot["status"] == "ok"
    assert snapshot["included"] >= 1
    assert snapshot["items"][0]["category"] == "security"


def test_content_revision_and_utc_iso_helpers() -> None:
    payload = {"a": 1, "b": 2}
    rev1 = content_revision(payload)
    rev2 = content_revision({"b": 2, "a": 1})
    assert rev1 == rev2
    assert len(rev1) == 16

    now = datetime.now(tz=UTC)
    assert utc_iso(now).endswith("Z")
    assert utc_iso(int(now.timestamp())).endswith("Z")
    assert utc_iso(int(now.timestamp() * 1000)).endswith("Z")
    assert utc_iso("2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"
    assert utc_iso("2026-01-01T00:00:00+00:00") == "2026-01-01T00:00:00Z"
    assert utc_iso("") is None
    assert utc_iso("invalid") is None
    assert utc_iso(None) is None


def test_site_health_unavailable_and_degraded_states() -> None:
    data = _base_data()

    unavailable_site = build_site_health_snapshot(
        data,
        entry_id="entry-1",
        site_id="missing",
        ha_device_ids={},
        devices_available=True,
    )
    assert unavailable_site["status"] == "unavailable"

    # Force a degraded/critical state by taking WAN down and gateway offline.
    data["devices"]["site-1"]["gw-1"]["state"] = "OFFLINE"
    data["devices"]["site-1"]["gw-1"]["wans"] = [{"connected": False}]
    degraded = build_site_health_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        ha_device_ids={},
        devices_available=False,
    )
    assert degraded["status"] == "partial"
    assert degraded["health"]["level"] == "critical"
    assert "gateway_offline" in degraded["health"]["reasons"]
    assert "wan_down" in degraded["health"]["reasons"]

    unavailable_payload = build_unavailable_site_health("entry-1", "site-1", "x")
    assert unavailable_payload["status"] == "unavailable"


def test_site_health_wan_state_variants() -> None:
    data = _base_data()
    gateway = data["devices"]["site-1"]["gw-1"]

    gateway["wans"] = [{"status": "UP"}, {"status": "DOWN"}]
    partial = build_site_health_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        ha_device_ids={},
        devices_available=True,
    )
    assert partial["gateway"]["internet"] == "partial"

    gateway["wans"] = [{"status": "UP"}, {"connected": True}]
    online = build_site_health_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        ha_device_ids={},
        devices_available=True,
    )
    assert online["gateway"]["internet"] == "online"

    gateway["wans"] = ["bad"]
    unknown = build_site_health_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        ha_device_ids={},
        devices_available=True,
    )
    assert unknown["gateway"]["internet"] == "unknown"


def test_internet_activity_unavailable_paths() -> None:
    data = _base_data()
    data["internet_activity_unavailable"] = {"site-1"}
    unavailable = build_internet_activity_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        entity_registry=_FakeEntityRegistry(),
    )
    assert unavailable["status"] == "unavailable"

    no_window = build_internet_activity_snapshot(
        {**data, "internet_activity_unavailable": set(), "internet_activity": {}},
        entry_id="entry-1",
        site_id="site-1",
        entity_registry=_FakeEntityRegistry(),
    )
    assert no_window["status"] == "unavailable"

    manual_unavailable = build_unavailable_internet_activity("entry-1", "site-1", "x")
    assert manual_unavailable["status"] == "unavailable"


def test_internet_activity_gateway_rate_parse_failures() -> None:
    data = _base_data()
    data["stats"]["site-1"]["gw-1"]["tx_rate"] = "bad"
    data["stats"]["site-1"]["gw-1"]["rx_rate"] = object()
    snapshot = build_internet_activity_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        entity_registry=_FakeEntityRegistry(),
    )
    assert snapshot["throughput"]["tx_bps"] is None
    assert snapshot["throughput"]["rx_bps"] is None


def test_internet_activity_preserves_zero_rates() -> None:
    data = _base_data()
    data["stats"]["site-1"]["gw-1"]["tx_rate"] = 0
    data["stats"]["site-1"]["gw-1"]["txRate"] = None
    data["stats"]["site-1"]["gw-1"]["rx_rate"] = 0
    data["stats"]["site-1"]["gw-1"]["rxRate"] = None

    snapshot = build_internet_activity_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        entity_registry=_FakeEntityRegistry(),
    )
    assert snapshot["throughput"]["tx_bps"] == 0
    assert snapshot["throughput"]["rx_bps"] == 0


def test_performance_helpers_and_unavailable() -> None:
    assert bytes_per_second_to_bits_per_second(10) == 80
    assert bytes_per_second_to_bits_per_second(None) is None
    assert bytes_per_second_to_bits_per_second("bad") is None

    unavailable = build_performance_snapshot(
        {"devices": {}, "stats": {}},
        entry_id="entry-1",
        site_id="site-1",
        ha_device_ids={},
        devices_available=True,
    )
    assert unavailable["status"] == "unavailable"

    manual_unavailable = build_unavailable_performance("entry-1", "site-1", "x")
    assert manual_unavailable["status"] == "unavailable"


def test_performance_metric_rate_helper() -> None:
    assert _metric_rate({"tx_rate": 2}, "tx") == 16
    assert _metric_rate({"rxRate": 3}, "rx") == 24
    assert _metric_rate({"tx_rate": "invalid"}, "tx") is None
    assert _metric_rate({}, "tx") is None


def test_performance_skips_non_device_and_client_rows() -> None:
    data = _base_data()
    data["devices"]["site-1"]["not-dict"] = "bad"
    snapshot = build_performance_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        ha_device_ids={},
        devices_available=True,
    )
    ids = {device["id"] for device in snapshot["devices"]}
    assert "site-1:not-dict" not in ids


def test_performance_rate_fallbacks() -> None:
    data = _base_data()
    data["stats"]["site-1"]["gw-1"] = {
        "uplinkTxRate": "20",
        "uplinkRxRate": "10",
        "cpu": "bad",
    }
    snapshot = build_performance_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        ha_device_ids={},
        devices_available=True,
    )
    gateway = next(item for item in snapshot["devices"] if item["id"] == "site-1:gw-1")
    assert gateway["tx_bps"] == 160
    assert gateway["rx_bps"] == 80
    assert gateway["cpu_pct"] is None


def test_protect_snapshot_status_paths() -> None:
    data = _base_data()
    # Add chime and unhealthy NVR storage branches.
    data["protect"]["chimes"] = {
        "chime-1": {"name": "Chime", "state": "DISCONNECTED", "type": "UP-Chime"}
    }
    data["protect"]["nvrs"] = {"nvr-1": {"storage": {"healthy": False, "used": 95}}}
    data["protect"]["cameras"]["cam-1"]["batteryLow"] = True
    data["protect"]["cameras"]["cam-1"]["batteryPercentage"] = 10

    degraded = build_protect_snapshot(
        data,
        entry_id="entry-1",
        entry_title="Console",
        entity_registry=_FakeEntityRegistry(),
        ha_device_ids={"cam-1": "ha-cam-1", "chime-1": "ha-chime-1"},
        protect_available=False,
    )
    assert degraded["status"] in {"degraded", "unavailable"}
    assert "storage_unhealthy" in degraded["warnings"]
    assert "storage_nearly_full" in degraded["warnings"]

    no_protect = build_no_protect_snapshot("entry-1", "Console")
    assert no_protect["status"] == "no_protect"

    unavailable = build_unavailable_protect_snapshot("entry-1", "Console", "x")
    assert unavailable["status"] == "unavailable"


def test_protect_snapshot_edge_inputs() -> None:
    data = _base_data()
    data["devices"] = {"site-1": ["invalid"]}
    data["protect"]["cameras"] = {
        "cam-1": {
            "name": "Camera",
            "state": "DISCONNECTED",
            "type": "camera",
            "recordingSettings": "invalid",
            "batteryPercentage": "bad",
            "lastSmartDetectTypes": "invalid",
        },
        "cam-2": "invalid",
    }
    data["protect"]["chimes"] = {"chime-1": "invalid"}
    data["protect"]["nvrs"] = {
        "nvr-1": {"storage": {"used": "invalid"}},
        "nvr-2": "invalid",
        "nvr-3": {"storage": "invalid"},
    }

    snapshot = build_protect_snapshot(
        data,
        entry_id="entry-1",
        entry_title="Console",
        entity_registry=_FakeEntityRegistry(),
        ha_device_ids={},
        protect_available=True,
    )
    assert snapshot["status"] in {"ok", "degraded"}
    assert any(device["id"] == "cam-1" for device in snapshot["devices"])


def test_timeline_truncation_and_unavailable() -> None:
    data = _base_data()
    # Create many events to exercise truncation branch.
    motion = data["protect"]["events"]["motion"]
    for idx in range(20):
        motion[f"evt-{idx}"] = {
            "id": f"evt-{idx}",
            "device_id": "cam-1",
            "name": "Front Door",
            "timestamp": 1_893_456_000_000 + idx,
        }

    snapshot = build_timeline_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        categories=["security"],
        hours=24,
        max_items=5,
    )
    assert snapshot["included"] == 5
    assert any(issue["code"] == "events_truncated" for issue in snapshot["issues"])

    empty = build_timeline_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        categories=["other"],
        hours=24,
        max_items=5,
    )
    assert empty["items"] == []

    unavailable = build_unavailable_timeline("entry-1", "site-1", "x")
    assert unavailable["status"] == "unavailable"


def test_timeline_ignores_invalid_events() -> None:
    data = _base_data()
    data["protect"]["events"] = {
        "motion": {
            "evt-1": {"timestamp": "invalid", "device_id": "cam-1"},
            "evt-2": "invalid",
        },
        "ring": "invalid",
    }
    snapshot = build_timeline_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        categories=["security"],
        hours=1,
        max_items=5,
    )
    assert snapshot["items"] == []


def test_site_health_helper_paths() -> None:
    assert _gateway_for_site({"a": {"type": "gateway"}}) == (
        "a",
        {"type": "gateway"},
    )
    assert _gateway_for_site({"a": {"type": "switch"}}) is None

    assert _wan_state(None) == ("unknown", [])
    assert _wan_state({"wans": []}) == ("unknown", [])
    assert _wan_state({"wans": "invalid"}) == ("unknown", [])
    assert _wan_state({"wans": [{"status": "down"}]})[0] == "offline"
    assert (
        _wan_state({"wans": [{"connected": True}, {"status": "standby"}]})[0]
        == "unknown"
    )


def test_additional_snapshot_branch_coverage() -> None:
    # dashboard_contract_utils: overflow epoch and non-string/number input
    assert utc_iso(1e200) is None
    assert utc_iso([]) is None

    # performance: non-finite and boolean values
    flag = True
    assert bytes_per_second_to_bits_per_second(float("inf")) is None
    assert bytes_per_second_to_bits_per_second(flag) is None

    # internet_activity_snapshot: non-dict device, no gateway, and non-dict wan item
    no_gw_data = _base_data()
    no_gw_data["devices"]["site-1"] = {
        "bad": "not-a-dict",
        "sw-1": {"type": "switch", "state": "ONLINE"},
    }
    no_gw_snap = build_internet_activity_snapshot(
        no_gw_data,
        entry_id="entry-1",
        site_id="site-1",
        entity_registry=_FakeEntityRegistry(),
    )
    assert no_gw_snap["throughput"] is None

    gw_wan_data = _base_data()
    gw_wan_data["devices"]["site-1"]["gw-1"]["wans"] = ["not-a-dict"]
    gw_wan_snap = build_internet_activity_snapshot(
        gw_wan_data,
        entry_id="entry-1",
        site_id="site-1",
        entity_registry=_FakeEntityRegistry(),
    )
    assert gw_wan_snap["wan_health"]["links"] == []

    # protect_snapshot: empty protect dict, doorbell _camera_type, mac miss, used=None
    empty_protect = build_protect_snapshot(
        {"protect": {}},
        entry_id="entry-1",
        entry_title="Home",
        entity_registry=_FakeEntityRegistry(),
        ha_device_ids={},
        protect_available=True,
    )
    assert empty_protect["status"] == "no_protect"

    doorbell_data = _base_data()
    doorbell_data["devices"]["bad-site"] = "not-a-dict"
    doorbell_data["protect"]["cameras"]["cam-1"]["_camera_type"] = "doorbell"
    doorbell_data["protect"]["cameras"]["cam-1"]["mac"] = "FF:EE:DD:CC:BB:AA"
    doorbell_data["protect"]["nvrs"] = {"nvr-1": {"storage": {"used": None}}}
    doorbell_snap = build_protect_snapshot(
        doorbell_data,
        entry_id="entry-1",
        entry_title="Home",
        entity_registry=_FakeEntityRegistry(),
        ha_device_ids={},
        protect_available=True,
    )
    assert doorbell_snap["devices"][0]["kind"] == "doorbell"

    # site_health: non-dict devices, non-dict device item, and missing gateway
    bad_devices_snap = build_site_health_snapshot(
        {"sites": {"site-1": {}}, "devices": {"site-1": "invalid"}},
        entry_id="entry-1",
        site_id="site-1",
        ha_device_ids={},
        devices_available=True,
    )
    assert bad_devices_snap["status"] == "unavailable"

    missing_gw_snap = build_site_health_snapshot(
        {
            "sites": {"site-1": {"name": "Home"}},
            "devices": {
                "site-1": {
                    "bad": "invalid",
                    "sw-1": {"type": "switch", "state": "ONLINE"},
                }
            },
        },
        entry_id="entry-1",
        site_id="site-1",
        ha_device_ids={},
        devices_available=True,
    )
    assert missing_gw_snap["health"]["level"] == "critical"

    # timeline: event older than cutoff window
    old_evt_data = _base_data()
    old_evt_data["protect"]["events"] = {
        "motion": {
            "evt-old": {"timestamp": 1_000_000_000_000, "device_id": "cam-1"},
        }
    }
    old_evt_snap = build_timeline_snapshot(
        old_evt_data,
        entry_id="entry-1",
        site_id="site-1",
        categories=["security"],
        hours=1,
        max_items=5,
    )
    assert old_evt_snap["items"] == []


def test_protect_snapshot_handles_str_enums_and_is_connected_fallback() -> None:
    data = _base_data()
    data["protect"]["cameras"]["cam-1"]["state"] = CameraState.CONNECTED
    data["protect"]["cameras"]["cam-1"]["type"] = CameraType.UVC_G4_DOORBELL_PRO
    data["protect"]["cameras"]["cam-1"]["recordingMode"] = RecordingMode.ALWAYS
    data["protect"]["cameras"]["cam-2"] = {
        "id": "cam-2",
        "name": "Driveway",
        "state": CameraState.UNKNOWN,
        "isConnected": True,
    }

    snap = build_protect_snapshot(
        data,
        entry_id="entry-1",
        entry_title="Home",
        entity_registry=_FakeEntityRegistry(),
        ha_device_ids={},
        protect_available=True,
    )
    cam1 = next(d for d in snap["devices"] if d["id"] == "cam-1")
    cam2 = next(d for d in snap["devices"] if d["id"] == "cam-2")
    assert cam1["connected"] is True
    assert cam1["kind"] == "doorbell"
    assert cam1["model"] == "UVC G4 Doorbell Pro"
    assert cam1["recording_mode"] == "always"
    assert cam1["is_recording"] is True
    assert cam2["connected"] is True
    assert enum_str(42) == "42"
    assert is_protect_device_connected({"is_connected": True}) is True
    assert is_protect_device_connected({"isConnected": False}) is False
