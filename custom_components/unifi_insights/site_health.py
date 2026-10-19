# Copyright (c) 2026 Ruaan Deysel

"""Site health snapshot builder for dashboard cards."""

from __future__ import annotations

from typing import Any

from .dashboard_contract_utils import as_dict, content_revision
from .data_transforms import (
    client_connection_type,
    site_device_facts,
    site_network_facts,
)
from .topology_contract import site_display_name

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
    data: dict[str, Any],
    *,
    entry_id: str,
    site_id: str,
    ha_device_ids: dict[str, str],
    devices_available: bool,
) -> dict[str, Any]:
    """Build a compact site-health payload for one site."""
    sites = as_dict(data.get("sites"))
    site = sites.get(site_id)
    if not isinstance(site, dict):
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
