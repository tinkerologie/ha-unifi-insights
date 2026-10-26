# Copyright (c) 2026 Ruaan Deysel

"""Tests for dashboard snapshot builder modules."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from enum import Enum
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api.protect.models.camera import (
    CameraState,
    CameraType,
    RecordingMode,
)

from custom_components.unifi_insights import data_transforms
from custom_components.unifi_insights.console_identity import (
    build_legacy_site_unique_id,
    build_site_unique_id,
)
from custom_components.unifi_insights.const import CONF_CONSOLE_ID, DOMAIN
from custom_components.unifi_insights.dashboard_snapshots import (
    WINDOWS,
    _wan_state,
    bits_per_second_to_int,
    build_internet_activity_snapshot,
    build_no_protect_snapshot,
    build_performance_snapshot,
    build_protect_snapshot,
    build_site_health_snapshot,
    build_timeline_snapshot,
    build_unavailable_internet_activity,
    build_unavailable_performance,
    build_unavailable_protect_snapshot,
    build_unavailable_site_health,
    build_unavailable_timeline,
    content_revision,
    enum_str,
    is_protect_device_connected,
    utc_iso,
)
from custom_components.unifi_insights.data_transforms import (
    NETWORK_FACTS_KEY,
    build_site_network_facts,
    get_site_gateway_id,
)
from custom_components.unifi_insights.topology import build_site_topology
from tests.fixtures.assertions import require_not_none

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
                    "uplink": {"txRateBps": 100, "rxRateBps": 50},
                },
                "ap-1": {
                    "cpuUtilizationPct": 30,
                    "memoryUtilizationPct": 20,
                    "uplink": {"txRateBps": 10, "rxRateBps": 20},
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
            require_not_none(entity_registry.async_get(entity_id)).config_entry_id
            == entry.entry_id
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
        require_not_none(entity_registry.async_get(entity_id)).config_entry_id
        == entries["own"].entry_id
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
    # The public uplink rates are already bits/s (#337): 100/50 stay 100/50.
    assert gateway["tx_bps"] == 100
    assert gateway["rx_bps"] == 50


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


@pytest.mark.parametrize("kind", ["smartDetect", "smartDetectZone", "motion"])
@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (
            {"smartDetectTypes": ["person", "animal", "vehicle"]},
            ["person", "animal", "vehicle"],
        ),
        (
            {"smart_detect_types": ["car", "unknown", "person"]},
            ["car", "unknown", "person"],
        ),
        ({"smartDetectTypes": [], "smart_detect_types": ["person"]}, []),
        ({"smartDetectTypes": None, "smart_detect_types": ["person"]}, []),
        ({"smartDetectTypes": "person"}, []),
        ({"smartDetectTypes": [None, 1, {}, " ", " animal "]}, ["animal"]),
        ({}, []),
    ],
)
def test_timeline_classifications_are_event_local(
    kind: str, metadata: dict[str, Any], expected: list[str]
) -> None:
    """Keep raw event kinds and classify only from that event's own metadata."""
    event = {"id": "e1", "camera": "cam", "start": "2100-01-01T00:00:00Z", **metadata}
    data = {
        "protect": {
            "events": {kind: {"e1": event}},
            "cameras": {
                "cam": {"name": "Driveway", "lastSmartDetectTypes": ["person"]}
            },
        }
    }
    before = deepcopy(data)
    result = build_timeline_snapshot(data, entry_id="entry", site_id="site")
    item = result["items"][0]
    assert result["version"] == 2
    assert item["kind"] == kind
    assert item["smart_detect_types"] == expected
    assert item["smart_detect_types"] is not metadata.get("smartDetectTypes")
    assert item["source"]["name"] == "Driveway"
    assert item["source"]["id"] == "e1"
    assert data == before


def test_timeline_classification_enum_values() -> None:
    """String enum values are serialized without enum class prefixes."""

    class Detection(Enum):
        PERSON = "person"
        INVALID = 1

    data = {
        "protect": {
            "events": {
                "smartDetectZone": {
                    "e": {
                        "start": "2100-01-01T00:00:00Z",
                        "smartDetectTypes": [Detection.PERSON, Detection.INVALID],
                    }
                }
            }
        }
    }
    result = build_timeline_snapshot(data, entry_id="e", site_id="s")
    assert result["items"][0]["smart_detect_types"] == ["person"]


@pytest.mark.parametrize(
    "alias",
    [
        "device",
        "camera",
        "cameraId",
        "camera_id",
        "sensor",
        "sensorId",
        "sensor_id",
        "deviceId",
        "device_id",
    ],
)
@pytest.mark.parametrize("object_ref", [False, True])
def test_timeline_source_reference_aliases(alias: str, *, object_ref: bool) -> None:
    """Resolve both scalar and object references from the owning entry only."""
    collection = "sensors" if alias.startswith("sensor") else "cameras"
    event = {
        "start": "2100-01-01T00:00:00Z",
        alias: {"id": "dev"} if object_ref else "dev",
        "name": "Generic event",
    }
    data = {
        "protect": {
            collection: {"dev": {"name": "Friendly device"}},
            "events": {"motion": {"e": event}},
        }
    }
    result = build_timeline_snapshot(data, entry_id="entry", site_id="site")
    assert result["items"][0]["source"]["name"] == "Friendly device"


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"id": "cam"}, "Protect device"),
        ({"camera": {"not_id": "cam"}}, "Protect device"),
        ({"camera": ["cam"]}, "Protect device"),
        ({"camera": True}, "Protect device"),
        ({"camera": " "}, "Protect device"),
        ({"camera": 123}, "123"),
        ({"camera": "absent"}, "absent"),
        (
            {"device": "cam", "deviceName": "Event device", "name": "Event"},
            "Event device",
        ),
        ({"device": "cam", "device_name": "Snake device"}, "Snake device"),
        ({"device": "cam", "name": "Event"}, "Event"),
        ({"camera": "cam"}, "Camera"),
        ({"sensor": "cam"}, "Sensor"),
    ],
)
def test_timeline_source_fallbacks(event: dict[str, Any], expected: str) -> None:
    """Generic IDs must be unambiguous; an event ID is never a device ID."""
    data = {
        "protect": {
            "cameras": {"cam": {"name": "Camera"}, "bad": None, "blank": {"name": 1}},
            "sensors": {"cam": {"name": "Sensor"}},
            "events": {"motion": {"event": {"start": "2100-01-01T00:00:00Z", **event}}},
        }
    }
    result = build_timeline_snapshot(data, entry_id="entry", site_id="site")
    assert result["items"][0]["source"]["name"] == expected


def test_content_revision_and_utc_iso_helpers() -> None:
    payload = {"a": 1, "b": 2}
    rev1 = content_revision(payload)
    rev2 = content_revision({"b": 2, "a": 1})
    assert rev1 == rev2
    assert len(rev1) == 16

    now = datetime.now(tz=UTC)
    assert require_not_none(utc_iso(now)).endswith("Z")
    assert require_not_none(utc_iso(int(now.timestamp()))).endswith("Z")
    assert require_not_none(utc_iso(int(now.timestamp() * 1000))).endswith("Z")
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


@pytest.mark.parametrize(
    ("unavailable_sites", "expected_status"),
    [
        ({"site-1"}, "unavailable"),
        (frozenset({"site-1"}), "unavailable"),
        (["site-1"], "unavailable"),
        (("site-1",), "unavailable"),
        ({"site-2"}, "ok"),
        ("site-1", "ok"),
        ({"site-1": True}, "ok"),
        (None, "ok"),
    ],
)
def test_internet_activity_unavailable_sites_shapes(
    unavailable_sites: object, expected_status: str
) -> None:
    """Any set, list or tuple of site IDs marks the site; malformed is ignored."""
    data = {**_base_data(), "internet_activity_unavailable": unavailable_sites}
    snapshot = build_internet_activity_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        entity_registry=_FakeEntityRegistry(),
    )
    assert snapshot["status"] == expected_status


def test_internet_activity_gateway_rate_parse_failures() -> None:
    data = _base_data()
    data["stats"]["site-1"]["gw-1"]["uplink"] = {
        "txRateBps": "bad",
        "rxRateBps": object(),
    }
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
    data["stats"]["site-1"]["gw-1"]["uplink"] = {"txRateBps": 0, "rxRateBps": 0}
    data["stats"]["site-1"]["gw-1"]["tx_rate_bps"] = 5

    snapshot = build_internet_activity_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        entity_registry=_FakeEntityRegistry(),
    )
    assert snapshot["throughput"]["tx_bps"] == 0
    assert snapshot["throughput"]["rx_bps"] == 0


def test_performance_helpers_and_unavailable() -> None:
    assert bits_per_second_to_int(100.9) == 100
    assert bits_per_second_to_int(0.0) == 0
    assert bits_per_second_to_int(None) is None

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
    # Coordinator-written keys: snake-case bits/s, then the explicit bytes/s
    # fallback (x8). The never-written tx_rate/txRate/uplinkTxRate are ignored.
    data["stats"]["site-1"]["gw-1"] = {
        "tx_rate_bps": "20",
        "rx_bytes_per_sec": "10",
        "uplinkTxRate": 999,
        "tx_rate": 999,
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
    assert gateway["tx_bps"] == 20
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
    gateway_data = {"devices": {"s": {"a": {"type": "gateway"}}}}
    assert get_site_gateway_id(gateway_data, "s") == "a"
    assert (
        get_site_gateway_id({"devices": {"s": {"a": {"type": "switch"}}}}, "s") is None
    )

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


# --- Network device read model (#337) ------------------------------------


def _published(data: dict[str, Any]) -> dict[str, Any]:
    """Publish the facts like the device coordinator does after a refresh."""
    data[NETWORK_FACTS_KEY] = {
        site_id: build_site_network_facts(devices, data["stats"].get(site_id))
        for site_id, devices in data["devices"].items()
    }
    return data


def _all_snapshots(data: dict[str, Any]) -> dict[str, Any]:
    """Build every snapshot that reads network devices for site-1."""
    common = {"entry_id": "entry-1", "site_id": "site-1"}
    return {
        "health": build_site_health_snapshot(
            data, ha_device_ids={}, devices_available=True, **common
        ),
        "performance": build_performance_snapshot(
            data, ha_device_ids={}, devices_available=True, **common
        ),
        "activity": build_internet_activity_snapshot(
            data, entity_registry=_FakeEntityRegistry(), **common
        ),
        "topology": build_site_topology(
            data, "entry-1", "site-1", node_key=b"k" * 32, ha_device_ids={}
        ),
    }


def test_dashboards_read_published_facts_without_rebuilding() -> None:
    """Every builder reads the published facts; none rebuilds them."""
    data = _published(_base_data())
    # Only the raw records change: the published facts still win.
    data["devices"]["site-1"]["gw-1"]["state"] = "OFFLINE"
    data["stats"]["site-1"]["gw-1"]["uplink"] = {"txRateBps": 1, "rxRateBps": 1}
    with patch.object(
        data_transforms,
        "build_site_network_facts",
        side_effect=AssertionError("published facts were rebuilt"),
    ):
        snapshots = _all_snapshots(data)

    assert snapshots["health"]["gateway"]["state"] == "online"
    gateway = next(
        d for d in snapshots["performance"]["devices"] if d["id"] == "site-1:gw-1"
    )
    assert (gateway["state"], gateway["tx_bps"], gateway["rx_bps"]) == (
        "online",
        100,
        50,
    )
    assert snapshots["activity"]["throughput"] == {
        "tx_bps": 100,
        "rx_bps": 50,
        "source": "gateway_uplink",
    }
    states = {node["kind"]: node["state"] for node in snapshots["topology"]["nodes"]}
    assert states["gateway"] == "online"


def test_dashboards_classify_devices_missing_from_published_facts() -> None:
    """A device the published facts lack is classified from its raw record."""
    data = _published(_base_data())
    data["devices"]["site-1"]["sw-1"] = {
        "name": "New switch",
        "state": "ONLINE",
        "features": ["switching"],
    }
    data["stats"]["site-1"]["sw-1"] = {"cpuUtilizationPct": 5}
    snapshots = _all_snapshots(data)

    assert snapshots["health"]["devices"]["switch"]["online"] == 1
    switch = next(
        d for d in snapshots["performance"]["devices"] if d["id"] == "site-1:sw-1"
    )
    assert (switch["kind"], switch["state"], switch["cpu_pct"]) == (
        "switch",
        "online",
        5.0,
    )
    kinds = sorted(node["kind"] for node in snapshots["topology"]["nodes"])
    assert "switch" in kinds


def test_site_health_gateway_uptime_and_client_rule() -> None:
    """Gateway uptime comes from the stats; clients use the exact rule."""
    data = _base_data()
    data["stats"]["site-1"]["gw-1"]["uptimeSec"] = 3600
    data["clients"]["site-1"] = {
        "c1": {"type": "ClientType.WIRED"},
        "c2": {"connection_type": " wireless "},
        "c3": {"type": "WIRED_GUEST"},
        "c4": "not-a-dict",
    }
    snapshot = build_site_health_snapshot(
        data,
        entry_id="entry-1",
        site_id="site-1",
        ha_device_ids={},
        devices_available=True,
    )
    assert snapshot["gateway"]["uptime_s"] == 3600
    assert snapshot["clients"] == {"total": 4, "wired": 1, "wireless": 1}


@pytest.mark.parametrize("publish", [False, True])
def test_getting_ready_is_still_unknown_on_site_health(*, publish: bool) -> None:
    """Service eligibility never inflates the dashboard's online counts."""
    data = _base_data()
    data["devices"]["site-1"]["gw-1"]["state"] = "GETTING_READY"
    if publish:
        data[NETWORK_FACTS_KEY] = {
            "site-1": build_site_network_facts(
                data["devices"]["site-1"], data["stats"]["site-1"]
            )
        }
    snapshot = build_site_health_snapshot(
        data,
        entry_id="entry",
        site_id="site-1",
        ha_device_ids={},
        devices_available=True,
    )
    assert snapshot["devices"]["gateway"] == {"online": 0, "offline": 0, "unknown": 1}
    assert snapshot["gateway"]["state"] == "unknown"
