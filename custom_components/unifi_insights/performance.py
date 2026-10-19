# Copyright (c) 2026 Ruaan Deysel

"""Device performance snapshot builder for dashboard cards."""

from __future__ import annotations

from typing import Any

from .dashboard_contract_utils import as_dict, content_revision
from .data_transforms import site_device_facts, site_network_facts
from .topology_contract import first_present, site_display_name


def bits_per_second_to_int(value: float | None) -> int | None:
    """
    Project a canonical bits/s reading onto an integer payload field.

    The read model keeps throughput as float bits/s; the ADR-005 payloads
    carry integers. A real 0 stays 0.
    """
    return int(value) if value is not None else None


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
