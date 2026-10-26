# Copyright 2026 UniFi Insights contributors
"""
Snapshot builders for the bundled dashboard cards.

``websocket_api.py`` serves these snapshots. Each builder turns facade
coordinator data into one versioned card contract (ADR-005): site health,
device performance, the event timeline, Protect status and internet activity.
The shared contract helpers come first. The module does no I/O.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Set as AbstractSet
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

from .console_identity import (
    build_legacy_site_unique_id,
    build_site_unique_id,
    owned_site_entity_unique_id,
)
from .const import DOMAIN, TIMELINE_MAX_HOURS
from .data_transforms import (
    client_connection_type,
    network_device,
    normalize_nvr_storage,
    site_device_facts,
    site_network_facts,
    site_record,
)
from .topology import first_present, site_display_name

if TYPE_CHECKING:
    from collections.abc import Mapping

# --- Shared contract helpers -------------------------------------------------

MILLISECONDS_EPOCH_THRESHOLD = 10_000_000_000


def as_dict(value: Any) -> dict[str, Any]:
    """Return value if it is a dict, otherwise an empty dict."""
    return value if isinstance(value, dict) else {}


def enum_str(value: Any, default: str = "") -> str:
    """Return the underlying string value for str or Enum without class prefixes."""
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def is_protect_device_connected(device: dict[str, Any]) -> bool:
    """Return True if a Protect device dict represents a connected/online device."""
    state = enum_str(device.get("state") or device.get("status")).strip().upper()
    if state in {"CONNECTED", "ONLINE", "UP"}:
        return True
    if state in {"DISCONNECTED", "OFFLINE", "DOWN"}:
        return False
    is_conn = device.get("isConnected")
    if is_conn is None:
        is_conn = device.get("is_connected")
    return bool(is_conn)


def content_revision(
    payload: dict[str, Any], *, exclude: set[str] | None = None
) -> str:
    """Return a deterministic short revision hash for a payload."""
    body = {k: v for k, v in payload.items() if not exclude or k not in exclude}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def utc_iso(value: Any) -> str | None:
    """Convert epoch seconds/milliseconds or datetime to UTC ISO-8601."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, (int, float)):
        # Protect timestamps are commonly milliseconds.
        ts = value / 1000 if value > MILLISECONDS_EPOCH_THRESHOLD else value
        try:
            dt = datetime.fromtimestamp(ts, tz=UTC)
        except (ValueError, OSError, OverflowError):  # fmt: skip
            return None
        return dt.isoformat().replace("+00:00", "Z")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        dt = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return None


# --- Site health -------------------------------------------------------------

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_UNAVAILABLE = "unavailable"

LEVEL_HEALTHY = "healthy"
LEVEL_DEGRADED = "degraded"
LEVEL_CRITICAL = "critical"
LEVEL_UNKNOWN = "unknown"


def _wan_state(gateway: dict[str, Any] | None) -> tuple[str, list[dict[str, str]]]:
    if gateway is None:
        return "unknown", []
    wans = gateway.get("wans")
    if not isinstance(wans, list) or not wans:
        return "unknown", []

    states: list[str] = []
    links: list[dict[str, str]] = []
    for index, wan in enumerate(wans):
        if not isinstance(wan, dict):
            continue
        up = wan.get("connected")
        raw = wan.get("status")
        state = "unknown"
        if up is True or (isinstance(raw, str) and raw.upper() in {"CONNECTED", "UP"}):
            state = "up"
        elif up is False or (
            isinstance(raw, str) and raw.upper() in {"DISCONNECTED", "DOWN"}
        ):
            state = "down"
        states.append(state)
        links.append(
            {"name": str(wan.get("wan_key", f"WAN{index + 1}")), "state": state}
        )

    if not states or all(state == "unknown" for state in states):
        return "unknown", links
    if all(state == "down" for state in states):
        return "offline", links
    if any(state == "up" for state in states) and any(
        state == "down" for state in states
    ):
        return "partial", links
    if all(state == "up" for state in states):
        return "online", links
    return "unknown", links


def build_site_health_snapshot(
    data: Mapping[str, Any],
    *,
    entry_id: str,
    site_id: str,
    ha_device_ids: dict[str, str],
    devices_available: bool,
) -> dict[str, Any]:
    """Build a compact site-health payload for one site."""
    if site_record(data, site_id) is None:
        return build_unavailable_site_health(entry_id, site_id, "site_unavailable")

    devices_by_site = as_dict(data.get("devices"))
    devices = devices_by_site.get(site_id)
    if not isinstance(devices, dict):
        return build_unavailable_site_health(entry_id, site_id, "site_unavailable")

    clients_by_site = as_dict(data.get("clients"))
    clients = as_dict(clients_by_site.get(site_id))

    # The site gateway, kind, state and uptime are the published read model,
    # so a null-type UDM Pro is the gateway here exactly as for its entities.
    site_facts = site_network_facts(data, site_id)
    gateway_id = site_facts.gateway_id if site_facts is not None else None
    raw_gateway = devices.get(gateway_id) if gateway_id is not None else None
    gateway = raw_gateway if isinstance(raw_gateway, dict) else None
    gateway_facts = (
        site_device_facts(site_facts, gateway_id, gateway)
        if gateway_id is not None and gateway is not None
        else None
    )
    internet, wan_links = _wan_state(gateway)

    by_kind: dict[str, dict[str, int]] = {
        "gateway": {"online": 0, "offline": 0, "unknown": 0},
        "switch": {"online": 0, "offline": 0, "unknown": 0},
        "access_point": {"online": 0, "offline": 0, "unknown": 0},
        "other": {"online": 0, "offline": 0, "unknown": 0},
    }

    attention_devices: list[dict[str, Any]] = []
    for device_id, device in devices.items():
        if not isinstance(device, dict):
            continue
        facts = site_device_facts(site_facts, device_id, device)
        kind = facts.kind
        state = facts.online_state
        by_kind[kind][state] += 1
        if state in {"offline", "unknown"}:
            attention_devices.append(
                {
                    "id": device_id,
                    "name": str(device.get("name") or device_id),
                    "kind": kind,
                    "state": state,
                    "ha_device_id": ha_device_ids.get(device_id),
                }
            )

    attention_devices.sort(
        key=lambda item: (
            item["state"] != "offline",
            item["kind"],
            item["name"],
        )
    )
    shown_attention = attention_devices[:5]
    omitted = max(0, len(attention_devices) - len(shown_attention))

    reasons: list[str] = []
    if gateway_facts is None:
        reasons.append("gateway_missing")
    elif gateway_facts.online_state == "offline":
        reasons.append("gateway_offline")
    if internet == "offline":
        reasons.append("wan_down")
    elif internet == "partial":
        reasons.append("wan_partial")
    if any(item["state"] == "offline" for item in attention_devices):
        reasons.append("devices_offline")
    if not devices_available:
        reasons.append("device_data_stale")

    if gateway_facts is None or internet == "offline":
        level = LEVEL_CRITICAL
    elif reasons:
        level = LEVEL_DEGRADED
    else:
        level = LEVEL_HEALTHY

    status = STATUS_OK if devices_available else STATUS_PARTIAL
    connections = [
        client_connection_type(client)
        for client in clients.values()
        if isinstance(client, dict)
    ]
    payload: dict[str, Any] = {
        "version": 1,
        "entry_id": entry_id,
        "site_id": site_id,
        "site_name": site_display_name(data, site_id),
        "status": status,
        "issues": []
        if devices_available
        else [{"code": "devices_unavailable", "severity": "warning"}],
        "health": {"level": level, "reasons": reasons},
        "gateway": {
            "present": gateway_facts is not None,
            "name": str(gateway.get("name") or "Gateway") if gateway else None,
            "state": gateway_facts.online_state if gateway_facts else "unknown",
            "uptime_s": gateway_facts.uptime_s if gateway_facts else None,
            "ha_device_id": ha_device_ids.get(gateway_id) if gateway_id else None,
            "internet": internet,
            "wan_links": wan_links,
        },
        "devices": by_kind,
        "attention_devices": shown_attention,
        "attention_omitted": omitted,
        "clients": {
            "total": len(clients),
            "wired": connections.count("wired"),
            "wireless": connections.count("wireless"),
        },
        "freshness": "fresh" if devices_available else "stale",
    }
    payload["revision"] = content_revision(payload)
    return payload


def build_unavailable_site_health(
    entry_id: str, site_id: str, code: str
) -> dict[str, Any]:
    """Return an unavailable site-health payload."""
    payload: dict[str, Any] = {
        "version": 1,
        "entry_id": entry_id,
        "site_id": site_id,
        "site_name": site_id,
        "status": STATUS_UNAVAILABLE,
        "issues": [{"code": code, "severity": "error"}],
        "health": {"level": LEVEL_UNKNOWN, "reasons": [code]},
        "gateway": {
            "present": False,
            "name": None,
            "state": "unknown",
            "uptime_s": None,
            "ha_device_id": None,
            "internet": "unknown",
            "wan_links": [],
        },
        "devices": {
            "gateway": {"online": 0, "offline": 0, "unknown": 0},
            "switch": {"online": 0, "offline": 0, "unknown": 0},
            "access_point": {"online": 0, "offline": 0, "unknown": 0},
            "other": {"online": 0, "offline": 0, "unknown": 0},
        },
        "attention_devices": [],
        "attention_omitted": 0,
        "clients": {"total": 0, "wired": 0, "wireless": 0},
        "freshness": "unavailable",
    }
    payload["revision"] = ""
    return payload


# --- Device performance ------------------------------------------------------


def bits_per_second_to_int(value: float | None) -> int | None:
    """
    Project a canonical bits/s reading onto an integer payload field.

    The read model keeps throughput as float bits/s; the ADR-005 payloads
    carry integers. A real 0 stays 0.
    """
    return int(value) if value is not None else None


def build_performance_snapshot(
    data: Mapping[str, Any],
    *,
    entry_id: str,
    site_id: str,
    ha_device_ids: dict[str, str],
    devices_available: bool,
) -> dict[str, Any]:
    """Build a site-scoped infrastructure performance snapshot."""
    devices_by_site = as_dict(data.get("devices"))
    stats_by_site = as_dict(data.get("stats"))

    devices = devices_by_site.get(site_id)
    stats = stats_by_site.get(site_id)
    if not isinstance(devices, dict) or not isinstance(stats, dict):
        return build_unavailable_performance(entry_id, site_id, "site_unavailable")

    site_facts = site_network_facts(data, site_id)
    snapshot_devices: list[dict[str, Any]] = []
    for device_id, device in devices.items():
        if not isinstance(device, dict):
            continue
        metric = as_dict(stats.get(device_id))
        # Kind, state, CPU, memory, uptime and the uplink rates are the
        # published read model, the same values the device sensors show.
        facts = site_device_facts(site_facts, device_id, device, metric)
        poe = first_present(metric, "poe_total_w", "poeTotalW")
        clients_total = first_present(metric, "num_sta", "clientCount", "client_count")

        item = {
            "id": f"{site_id}:{device_id}",
            "ha_device_id": ha_device_ids.get(device_id),
            "name": str(device.get("name") or device_id),
            "kind": facts.kind,
            "state": facts.online_state,
            "cpu_pct": facts.cpu_pct,
            "memory_pct": facts.memory_pct,
            "uptime_s": facts.uptime_s,
            "tx_bps": bits_per_second_to_int(facts.tx_bps),
            "rx_bps": bits_per_second_to_int(facts.rx_bps),
            "poe_power_w": float(poe) if isinstance(poe, (int, float)) else None,
            "clients": int(clients_total)
            if isinstance(clients_total, (int, float))
            else None,
        }
        snapshot_devices.append(item)

    snapshot_devices.sort(key=lambda item: (item["kind"], item["name"]))
    payload: dict[str, Any] = {
        "version": 1,
        "entry_id": entry_id,
        "site_id": site_id,
        "site_name": site_display_name(data, site_id),
        "status": "ok" if devices_available else "partial",
        "issues": []
        if devices_available
        else [{"code": "devices_unavailable", "severity": "warning"}],
        "devices": snapshot_devices,
    }
    payload["revision"] = content_revision(payload)
    return payload


def build_unavailable_performance(
    entry_id: str, site_id: str, code: str
) -> dict[str, Any]:
    """Return an unavailable performance payload."""
    return {
        "version": 1,
        "entry_id": entry_id,
        "site_id": site_id,
        "site_name": site_id,
        "status": "unavailable",
        "issues": [{"code": code, "severity": "error"}],
        "devices": [],
        "revision": "",
    }


# --- Event timeline ----------------------------------------------------------

TIMELINE_VERSION = 2
TIMELINE_DEVICE_COLLECTIONS = (
    "cameras",
    "sensors",
    "lights",
    "doorlocks",
    "alarm_hubs",
    "chimes",
    "nvrs",
    "viewers",
    "sirens",
    "link_stations",
    "fobs",
    "viewports",
)
DEFAULT_HOURS = 6
DEFAULT_MAX_ITEMS = 10
MAX_ITEMS = 50


KIND_TO_SEVERITY: dict[str, str] = {
    "ring": "warning",
    "person": "warning",
    "package": "warning",
    "motion": "info",
    "vehicle": "info",
    "animal": "info",
    "sensor_opened": "info",
    "sensor_closed": "info",
}


def _timeline_detection_types(event: dict[str, Any]) -> list[str]:
    """Read this event's classifications, never a camera's latest detection."""
    raw = event.get("smartDetectTypes", event.get("smart_detect_types"))
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        value = item.value if isinstance(item, Enum) else item
        if isinstance(value, str) and value.strip():
            result.append(value.strip())
    return result


def _timeline_device_names(
    protect: dict[str, Any],
) -> dict[str, list[tuple[str, str | None]]]:
    """Index names once per snapshot, keeping ambiguous IDs distinguishable."""
    names: dict[str, list[tuple[str, str | None]]] = {}
    for collection in TIMELINE_DEVICE_COLLECTIONS:
        for device_id, record in as_dict(protect.get(collection)).items():
            if not isinstance(record, dict):
                continue
            raw_name = record.get("name")
            name = raw_name.strip() if isinstance(raw_name, str) else None
            names.setdefault(str(device_id), []).append((collection, name or None))
    return names


def _timeline_source_name(
    event: dict[str, Any], names: dict[str, list[tuple[str, str | None]]]
) -> str:
    """Resolve an entry-local device reference without confusing event IDs."""
    fallback_id: str | None = None
    for key, collection in (
        ("device", None),
        ("camera", "cameras"),
        ("cameraId", "cameras"),
        ("camera_id", "cameras"),
        ("sensor", "sensors"),
        ("sensorId", "sensors"),
        ("sensor_id", "sensors"),
        ("deviceId", None),
        ("device_id", None),
    ):
        value = event.get(key)
        if isinstance(value, dict):
            value = value.get("id")
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            continue
        device_id = str(value).strip()
        if not device_id:
            continue
        fallback_id = fallback_id or device_id
        matches = [
            name
            for group, name in names.get(device_id, [])
            if collection is None or group == collection
        ]
        if len(matches) == 1 and matches[0]:
            return matches[0]
    for key in ("deviceName", "device_name", "name"):
        name = event.get(key)
        if isinstance(name, str) and name.strip():
            return name.strip()
    return fallback_id or "Protect device"


def build_timeline_snapshot(
    data: Mapping[str, Any],
    *,
    entry_id: str,
    site_id: str,
    categories: list[str] | None = None,
    hours: int = DEFAULT_HOURS,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> dict[str, Any]:
    """Build a compact recent-activity timeline from cached Protect events."""
    categories = categories or ["security"]
    max_items = max(1, min(MAX_ITEMS, max_items))
    hours = max(1, min(TIMELINE_MAX_HOURS, hours))

    protect = as_dict(data.get("protect"))
    events = as_dict(protect.get("events"))
    device_names = _timeline_device_names(protect)

    cutoff = datetime.now(tz=UTC) - timedelta(hours=hours)
    items: list[dict[str, Any]] = []
    if "security" in categories:
        for event_type, by_id in events.items():
            if not isinstance(by_id, dict):
                continue
            for event_id, event_data in by_id.items():
                if not isinstance(event_data, dict):
                    continue
                ts = utc_iso(
                    event_data.get("start")
                    or event_data.get("time")
                    or event_data.get("timestamp")
                )
                if ts is None:
                    continue
                if datetime.fromisoformat(ts) < cutoff:
                    continue

                kind = str(event_type)
                source_name = _timeline_source_name(event_data, device_names)
                item = {
                    "id": f"evt:{event_id}",
                    "kind": kind,
                    "smart_detect_types": _timeline_detection_types(event_data),
                    "category": "security",
                    "severity": KIND_TO_SEVERITY.get(kind, "info"),
                    "timestamp": ts,
                    "time_source": "device",
                    "count": 1,
                    "scope": "entry",
                    "site_id": None,
                    "source": {
                        "id": str(
                            event_data.get("device_id")
                            or event_data.get("id")
                            or event_id
                        ),
                        "name": source_name,
                        "kind": "camera"
                        if kind
                        in {"motion", "ring", "person", "vehicle", "animal", "package"}
                        else "sensor",
                    },
                    "ha_device_id": None,
                    "camera_entity_id": None,
                }
                items.append(item)

    items.sort(
        key=lambda item: datetime.fromisoformat(item["timestamp"]),
        reverse=True,
    )
    total = len(items)
    included_items = items[:max_items]
    issues: list[dict[str, str]] = []
    if total > len(included_items):
        issues.append({"code": "events_truncated", "severity": "info"})

    payload: dict[str, Any] = {
        "version": TIMELINE_VERSION,
        "entry_id": entry_id,
        "site_id": site_id,
        "status": "ok",
        "issues": issues,
        "recording_since": included_items[-1]["timestamp"] if included_items else None,
        "total": total,
        "included": len(included_items),
        "items": included_items,
    }
    payload["revision"] = content_revision(payload)
    return payload


def build_unavailable_timeline(
    entry_id: str, site_id: str, code: str
) -> dict[str, Any]:
    """Return unavailable timeline snapshot."""
    return {
        "version": TIMELINE_VERSION,
        "entry_id": entry_id,
        "site_id": site_id,
        "status": "unavailable",
        "issues": [{"code": code, "severity": "error"}],
        "recording_since": None,
        "total": 0,
        "included": 0,
        "items": [],
        "revision": "",
    }


# --- Protect status ----------------------------------------------------------

LOW_BATTERY_PCT = 20


def _site_from_mac(data: Mapping[str, Any], mac: str | None) -> str | None:
    if not mac:
        return None
    devices_by_site = as_dict(data.get("devices"))
    for site_id, devices in devices_by_site.items():
        if not isinstance(devices, dict):
            continue
        for device in devices.values():
            if isinstance(device, dict) and device.get("macAddress") == mac:
                return str(site_id)
    return None


def _is_doorbell_camera(camera_data: dict[str, Any]) -> bool:
    """Check if a camera record represents a doorbell."""
    camera_type = enum_str(camera_data.get("_camera_type")).lower()
    if "doorbell" in camera_type:
        return True

    api_type = enum_str(camera_data.get("type") or camera_data.get("model")).lower()
    if "doorbell" in api_type:
        return True

    name = enum_str(camera_data.get("name")).lower()
    return "doorbell" in name


def _camera_entity_id(registry: Any, camera_id: str) -> str | None:
    unique_id = f"unifi_insights_camera_{camera_id}"
    entity_id = registry.async_get_entity_id("camera", "unifi_insights", unique_id)
    return str(entity_id) if isinstance(entity_id, str) else None


def build_protect_snapshot(
    data: Mapping[str, Any],
    *,
    entry_id: str,
    entry_title: str,
    entity_registry: Any,
    ha_device_ids: dict[str, str],
    protect_available: bool,
) -> dict[str, Any]:
    """Build a protect status snapshot for cameras/doorbells/chimes."""
    protect = as_dict(data.get("protect"))
    cameras = as_dict(protect.get("cameras"))
    chimes = as_dict(protect.get("chimes"))
    nvrs = as_dict(protect.get("nvrs"))

    if not protect:
        return build_no_protect_snapshot(entry_id, entry_title)

    devices: list[dict[str, Any]] = []
    warnings: list[str] = []

    for camera_id, camera in cameras.items():
        if not isinstance(camera, dict):
            continue
        model = enum_str(camera.get("type") or camera.get("model"), "camera")
        is_doorbell = _is_doorbell_camera(camera)
        kind = "doorbell" if is_doorbell else "camera"
        connected = is_protect_device_connected(camera)
        device_warnings: list[str] = []
        if not connected:
            device_warnings.append("offline")

        battery_pct = camera.get("batteryPercentage")
        battery_low = camera.get("batteryLow")
        battery_value = (
            int(battery_pct) if isinstance(battery_pct, (int, float)) else None
        )
        low_battery = bool(battery_low) or (
            battery_value is not None and battery_value <= LOW_BATTERY_PCT
        )
        if low_battery:
            device_warnings.append("low_battery")

        smart_types = camera.get("lastSmartDetectTypes")
        event_type = (
            enum_str(smart_types[0])
            if isinstance(smart_types, list) and smart_types
            else None
        )
        last_event = utc_iso(
            camera.get("lastSmartDetect")
            or camera.get("lastMotionStart")
            or camera.get("lastRingStart")
        )
        recording_settings = as_dict(camera.get("recordingSettings"))
        raw_recording_mode = (
            recording_settings.get("mode")
            or camera.get("recordingMode")
            or camera.get("recording_mode")
        )
        recording_mode = (
            enum_str(raw_recording_mode).lower()
            if raw_recording_mode is not None
            else None
        )

        camera_item: dict[str, Any] = {
            "id": str(camera_id),
            "kind": kind,
            "name": enum_str(camera.get("name"), str(camera_id)),
            "model": model,
            "connected": connected,
            "last_seen_at": utc_iso(camera.get("lastSeen")),
            "site_id": _site_from_mac(data, camera.get("mac")),
            "ha_device_id": ha_device_ids.get(str(camera_id))
            or ha_device_ids.get(f"protect_camera_{camera_id}"),
            "camera_entity_id": _camera_entity_id(entity_registry, str(camera_id)),
            "warnings": device_warnings,
            "motion_active": bool(
                camera.get("isMotionDetected") or camera.get("isMotionDetectedByPir")
            ),
            "is_recording": bool(
                camera.get("isRecording") or recording_mode not in {None, "never"}
            ),
            "recording_mode": recording_mode,
            "last_event_at": last_event,
            "last_event_type": event_type,
            "last_ring_at": (
                utc_iso(camera.get("lastRingStart")) if is_doorbell else None
            ),
        }
        if battery_value is not None or battery_low is not None:
            camera_item["battery"] = {"percentage": battery_value, "low": low_battery}

        devices.append(camera_item)

    for chime_id, chime in chimes.items():
        if not isinstance(chime, dict):
            continue
        connected = is_protect_device_connected(chime)
        chime_warnings = ["offline"] if not connected else []
        chime_item: dict[str, Any] = {
            "id": str(chime_id),
            "kind": "chime",
            "name": enum_str(chime.get("name"), str(chime_id)),
            "model": enum_str(chime.get("type") or chime.get("model"), "chime"),
            "connected": connected,
            "last_seen_at": utc_iso(chime.get("lastSeen")),
            "site_id": None,
            "ha_device_id": ha_device_ids.get(str(chime_id))
            or ha_device_ids.get(f"protect_chime_{chime_id}"),
            "camera_entity_id": None,
            "warnings": chime_warnings,
            "last_ring_at": None,
        }
        devices.append(chime_item)

    storage_unhealthy = False
    storage_nearly_full = False
    for nvr in nvrs.values():
        if not isinstance(nvr, dict):
            continue
        storage = normalize_nvr_storage(nvr)
        if storage.degraded is True:
            storage_unhealthy = True
        if storage.nearly_full is True:
            storage_nearly_full = True

    if storage_unhealthy:
        warnings.append("storage_unhealthy")
    if storage_nearly_full:
        warnings.append("storage_nearly_full")

    stale = not protect_available
    status = (
        "degraded"
        if (warnings or any(d.get("warnings") for d in devices) or stale)
        else "ok"
    )

    payload: dict[str, Any] = {
        "schema_version": 1,
        "entry_id": entry_id,
        "entry_title": entry_title,
        "status": "unavailable" if not devices and stale else status,
        "warnings": warnings,
        "devices": devices,
        "issues": (
            [{"code": "protect_unavailable", "severity": "warning"}] if stale else []
        ),
    }
    payload["revision"] = content_revision(payload)
    return payload


def build_no_protect_snapshot(entry_id: str, entry_title: str) -> dict[str, Any]:
    """Return snapshot for entries that do not have Protect configured."""
    return {
        "schema_version": 1,
        "entry_id": entry_id,
        "entry_title": entry_title,
        "status": "no_protect",
        "warnings": [],
        "issues": [],
        "devices": [],
        "revision": "",
    }


def build_unavailable_protect_snapshot(
    entry_id: str, entry_title: str, code: str
) -> dict[str, Any]:
    """Return unavailable protect snapshot."""
    return {
        "schema_version": 1,
        "entry_id": entry_id,
        "entry_title": entry_title,
        "status": "unavailable",
        "warnings": [],
        "issues": [{"code": code, "severity": "error"}],
        "devices": [],
        "revision": "",
    }


# --- Internet activity -------------------------------------------------------

WINDOWS = ("1h", "1d", "1w", "1m")


def _resolve_entity_ids(
    registry: Any,
    *,
    site_id: str,
    entry_id: str,
    namespace: str | None,
) -> dict[str, str]:
    """Resolve activity references using the shared ownership and legacy policy."""
    ids: dict[str, str] = {}
    # Sensor keys implemented by site_internet_activity_sensor.py
    for direction in ("download", "upload"):
        for window in WINDOWS:
            key = f"internet_{direction}_{window}"
            legacy = build_legacy_site_unique_id(site_id, key)
            target = (
                build_site_unique_id(namespace, site_id, key) if namespace else None
            )
            uid = owned_site_entity_unique_id(
                registry, entry_id, "sensor", legacy, target, translation_key=key
            )
            entity_id = (
                registry.async_get_entity_id("sensor", DOMAIN, uid) if uid else None
            )
            if entity_id:
                ids[f"{direction}_{window}"] = entity_id
    return ids


def build_internet_activity_snapshot(
    data: Mapping[str, Any],
    *,
    entry_id: str,
    site_id: str,
    entity_registry: Any,
    namespace: str | None = None,
) -> dict[str, Any]:
    """Build one site's internet-activity snapshot."""
    activity_by_site = as_dict(data.get("internet_activity"))
    unavailable_raw = data.get("internet_activity_unavailable")
    if isinstance(unavailable_raw, (AbstractSet, list, tuple)) and (
        site_id in unavailable_raw
    ):
        return build_unavailable_internet_activity(
            entry_id, site_id, "site_unavailable"
        )

    windows_data = activity_by_site.get(site_id)
    if not isinstance(windows_data, dict):
        return build_unavailable_internet_activity(
            entry_id, site_id, "site_unavailable"
        )

    # Gateway WAN health/rates from the site gateway of the published read
    # model: its online state and canonical uplink bits/s, the same values
    # the gateway's own sensors show.
    site_facts = site_network_facts(data, site_id)
    gateway_id = site_facts.gateway_id if site_facts is not None else None
    gateway = (
        network_device(data, site_id, gateway_id) if gateway_id is not None else None
    )

    throughput = None
    wan_links: list[dict[str, Any]] = []
    gateway_online = False
    if gateway_id is not None and gateway is not None:
        facts = site_device_facts(site_facts, gateway_id, gateway)
        throughput = {
            "tx_bps": bits_per_second_to_int(facts.tx_bps),
            "rx_bps": bits_per_second_to_int(facts.rx_bps),
            "source": "gateway_uplink",
        }
        gateway_online = facts.online_state == "online"

        wans = gateway.get("wans")
        if isinstance(wans, list):
            for idx, wan in enumerate(wans):
                if not isinstance(wan, dict):
                    continue
                connected = wan.get("connected")
                status = wan.get("status")
                wan_links.append(
                    {
                        "wan_key": str(wan.get("wan_key", f"WAN{idx + 1}")),
                        "connected": bool(connected)
                        if isinstance(connected, bool)
                        else None,
                        "status": str(status) if isinstance(status, str) else None,
                    }
                )

    windows: dict[str, Any] = {}
    for window in WINDOWS:
        totals = windows_data.get(window)
        if not isinstance(totals, dict):
            continue
        rx = totals.get("rx_bytes")
        tx = totals.get("tx_bytes")
        windows[window] = {
            "download_bytes": int(rx) if isinstance(rx, (int, float)) else None,
            "upload_bytes": int(tx) if isinstance(tx, (int, float)) else None,
            "report_interval": window,
            "available": True,
            "series": [],
            "coverage": None,
            "partial": False,
            "peak": None,
        }

    payload: dict[str, Any] = {
        "version": 1,
        "entry_id": entry_id,
        "site_id": site_id,
        "site_name": site_display_name(data, site_id),
        "status": "ok",
        "issues": [],
        "windows": windows,
        "entity_ids": _resolve_entity_ids(
            entity_registry,
            site_id=site_id,
            entry_id=entry_id,
            namespace=namespace,
        ),
        "throughput": throughput,
        "wan_health": {
            "gateway_online": gateway_online,
            "links": wan_links,
        },
    }
    payload["revision"] = content_revision(payload)
    return payload


def build_unavailable_internet_activity(
    entry_id: str, site_id: str, code: str
) -> dict[str, Any]:
    """Return an unavailable internet-activity payload."""
    return {
        "version": 1,
        "entry_id": entry_id,
        "site_id": site_id,
        "site_name": site_id,
        "status": "unavailable",
        "issues": [{"code": code, "severity": "error"}],
        "windows": {},
        "entity_ids": {},
        "throughput": None,
        "wan_health": {"gateway_online": False, "links": []},
        "revision": "",
    }
