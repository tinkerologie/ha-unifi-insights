# Copyright (c) 2026 Ruaan Deysel

"""Device performance snapshot builder for dashboard cards."""

from __future__ import annotations

from typing import Any

from .dashboard_contract_utils import as_dict, content_revision
from .data_transforms import bytes_to_bits_per_second
from .topology_contract import (
    device_kind,
    device_state,
    first_present,
    site_display_name,
)


# Keep conversion in one place and reuse in snapshots.
def bytes_per_second_to_bits_per_second(value: Any) -> int | None:
    """Convert bytes/s to bits/s while preserving explicit zero values."""
    bits = bytes_to_bits_per_second(value)
    return int(bits) if bits is not None else None


def _metric_rate(stats: dict[str, Any], direction: str) -> int | None:
    """Resolve uplink rates from known fields."""
    if direction == "tx":
        raw = first_present(stats, "tx_rate", "txRate", "uplinkTxRate")
    else:
        raw = first_present(stats, "rx_rate", "rxRate", "uplinkRxRate")
    return bytes_per_second_to_bits_per_second(raw)


def build_performance_snapshot(
    data: dict[str, Any],
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

    snapshot_devices: list[dict[str, Any]] = []
    for device_id, device in devices.items():
        if not isinstance(device, dict):
            continue
        kind = device_kind(device)
        metric = as_dict(stats.get(device_id))
        cpu = first_present(
            metric, "cpuUtilizationPct", "cpu_utilization_pct", "cpu_percent", "cpu"
        )
        memory = first_present(
            metric,
            "memoryUtilizationPct",
            "memory_utilization_pct",
            "memory_percent",
            "memory",
        )
        uptime = first_present(
            metric, "uptimeSec", "uptime_sec", "uptime_seconds", "uptime"
        )
        poe = first_present(metric, "poe_total_w", "poeTotalW")
        clients_total = first_present(metric, "num_sta", "clientCount", "client_count")

        item = {
            "id": f"{site_id}:{device_id}",
            "ha_device_id": ha_device_ids.get(device_id),
            "name": str(device.get("name") or device_id),
            "kind": kind,
            "state": device_state(device),
            "cpu_pct": float(cpu) if isinstance(cpu, (int, float)) else None,
            "memory_pct": float(memory) if isinstance(memory, (int, float)) else None,
            "uptime_s": int(uptime) if isinstance(uptime, (int, float)) else None,
            "tx_bps": _metric_rate(metric, "tx"),
            "rx_bps": _metric_rate(metric, "rx"),
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
