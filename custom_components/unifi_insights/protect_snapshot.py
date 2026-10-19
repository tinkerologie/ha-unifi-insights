# Copyright (c) 2026 Ruaan Deysel

"""Protect status snapshot builder for dashboard cards."""

from __future__ import annotations

from typing import Any

from .dashboard_contract_utils import (
    as_dict,
    content_revision,
    enum_str,
    is_protect_device_connected,
    utc_iso,
)
from .data_transforms import normalize_nvr_storage

LOW_BATTERY_PCT = 20


def _site_from_mac(data: dict[str, Any], mac: str | None) -> str | None:
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
    data: dict[str, Any],
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
