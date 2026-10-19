# Copyright 2026 UniFi Insights contributors
"""
Data transformation functions for `unifi-official-api` responses.

This module provides transformation functions to convert `unifi-official-api`
response formats to the internal data structures expected by entities,
maintaining backward compatibility.
"""

from __future__ import annotations

import ipaddress
import math
from typing import Any

from .innerspace_transforms import (
    _normalize_innerspace_mac,
    correlate_innerspace_devices,
    normalize_innerspace_snapshot,
    parse_floor_plan_asset_path,
    transform_innerspace_device,
    transform_innerspace_floor_plan,
    transform_innerspace_project,
)


def current_chime_ringtone_id(chime_data: Any) -> str | None:
    """Read one reported ringtone ID only when every ring setting agrees."""
    if not isinstance(chime_data, dict):
        return None
    settings = chime_data.get("ringSettings")
    if not isinstance(settings, list) or not settings:
        return None
    current: str | None = None
    for setting in settings:
        if not isinstance(setting, dict):
            return None
        value = setting.get("ringtoneId")
        if not isinstance(value, str) or not value.strip():
            return None
        if current is None:
            current = value
        elif value != current:
            return None
    return current


def _is_unspecified_address(value: str) -> bool:
    """Return True for 0.0.0.0 / ::, the placeholder a down link reports."""
    try:
        return ipaddress.ip_address(value).is_unspecified
    except ValueError:
        return False


def normalize_legacy_wans(legacy_device: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Return per-WAN connection state from a legacy gateway record.

    The gateway's ``wan1..wanN`` blocks describe the physical ports (type
    "ethernet", name "eth8"), not the internet connection, so they cannot
    tell a dropped PPPoE session from a connected one. The controller's own
    per-WAN verdict lives in ``last_wan_status`` ({"WAN": "online"}) and
    ``last_wan_interfaces`` ({"WAN": {"ip": ..., "alive": true}}), keyed by
    the same WAN names the UniFi UI shows. A WAN that is configured but not
    in use does not appear in either.
    """
    statuses = legacy_device.get("last_wan_status")
    interfaces = legacy_device.get("last_wan_interfaces")
    statuses = statuses if isinstance(statuses, dict) else {}
    interfaces = interfaces if isinstance(interfaces, dict) else {}

    wans: list[dict[str, Any]] = []
    for name in dict.fromkeys((*statuses, *interfaces)):
        if not isinstance(name, str) or not name:
            continue
        status = statuses.get(name)
        status = status if isinstance(status, str) else None
        interface = interfaces.get(name)
        interface = interface if isinstance(interface, dict) else {}
        alive = interface.get("alive")
        alive = alive if isinstance(alive, bool) else None
        ip = interface.get("ip")
        has_ip = isinstance(ip, str) and bool(ip) and not _is_unspecified_address(ip)
        wans.append(
            {
                "key": name.lower(),
                "name": name,
                "status": status,
                "alive": alive,
                "ip": ip if has_ip else None,
                # The controller's status is authoritative; "alive" (its
                # reachability probe) only decides when no status is given.
                "connected": (
                    status.lower() == "online" if status is not None else alive is True
                ),
            }
        )
    return wans


# Filesystems that live in RAM or are recreated at boot. They fill and empty
# constantly, so their entities are disabled by default and they never count
# towards "storage nearly full". Matched on the mount path (and the type when a
# console names one): the classic "other" type is NOT a signal, an HDD console
# may report a real array as "other".
_VOLATILE_MOUNT_PREFIXES: tuple[str, ...] = (
    "/" + "tmp",
    "/run",
    "/var/run",
    "/" + "var/tmp",
    "/" + "dev/shm",
)
_VOLATILE_MOUNT_TYPES: frozenset[str] = frozenset({"tmpfs", "ramfs"})


def _clean_text(value: Any) -> str | None:
    """Return a stripped non-empty string, or None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _storage_bytes(value: Any) -> int | None:
    """Return a byte count, or None for anything that is not a sane number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        finite = math.isfinite(value)
    except OverflowError:
        return None
    if not finite or value < 0:
        return None
    return int(value)


def normalize_legacy_storage(legacy_device: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Return the per-mount storage of a legacy (classic) device record.

    Consoles report ``storage`` as a list of ``{mount_point, name, type, size,
    used}`` (sizes in bytes). Entries without a usable ``mount_point`` are
    dropped and a repeated mount point keeps its first entry. Sizes that are
    not finite non-negative numbers become ``None`` so a bad reading reads as
    unknown rather than as 0 or full. ``name`` and ``type`` become ``None``
    when blank. The list is empty when the device reports no storage.
    """
    raw = legacy_device.get("storage")
    if not isinstance(raw, list):
        return []
    mounts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        mount_point = _clean_text(item.get("mount_point"))
        if mount_point is None or mount_point in seen:
            continue
        seen.add(mount_point)
        mounts.append(
            {
                "mount_point": mount_point,
                "name": _clean_text(item.get("name")),
                "type": _clean_text(item.get("type")),
                "size": _storage_bytes(item.get("size")),
                "used": _storage_bytes(item.get("used")),
            }
        )
    return mounts


def storage_used_percent(mount: dict[str, Any]) -> float | None:
    """
    Return how full a normalised mount is, in percent with one decimal.

    None when either size is unknown or the size is 0. A mount that reports
    more used than its size (a quirk of some filesystems) reads 100.0.
    """
    size = mount.get("size")
    used = mount.get("used")
    if size is None or used is None or size <= 0:
        return None
    return float(min(100.0, round(float(used) / float(size) * 100, 1)))


def is_volatile_storage_mount(mount: dict[str, Any]) -> bool:
    """Return True for RAM-backed or boot-recreated mounts such as ``/tmp``."""
    mount_point = str(mount.get("mount_point") or "").lower()
    if any(
        mount_point == prefix or mount_point.startswith(f"{prefix}/")
        for prefix in _VOLATILE_MOUNT_PREFIXES
    ):
        return True
    return str(mount.get("type") or "").lower() in _VOLATILE_MOUNT_TYPES


__all__ = [
    "_normalize_innerspace_mac",
    "correlate_innerspace_devices",
    "is_volatile_storage_mount",
    "normalize_innerspace_snapshot",
    "normalize_legacy_storage",
    "normalize_legacy_wans",
    "parse_floor_plan_asset_path",
    "storage_used_percent",
    "transform_innerspace_device",
    "transform_innerspace_floor_plan",
    "transform_innerspace_project",
]
