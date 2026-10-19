# Copyright (c) 2026 Ruaan Deysel

"""Internet activity snapshot builder for dashboard cards."""

from __future__ import annotations

from typing import Any

from .console_identity import build_legacy_site_unique_id, build_site_unique_id
from .const import DOMAIN
from .dashboard_contract_utils import as_dict, content_revision
from .data_transforms import site_device_facts, site_network_facts
from .helpers import owned_site_entity_unique_id
from .performance import bits_per_second_to_int
from .topology_contract import site_display_name

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
    data: dict[str, Any],
    *,
    entry_id: str,
    site_id: str,
    entity_registry: Any,
    namespace: str | None = None,
) -> dict[str, Any]:
    """Build one site's internet-activity snapshot."""
    activity_by_site = as_dict(data.get("internet_activity"))
    unavailable_raw = data.get("internet_activity_unavailable")
    unavailable_sites = unavailable_raw if isinstance(unavailable_raw, set) else set()

    if site_id in unavailable_sites:
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
    devices = as_dict(as_dict(data.get("devices")).get(site_id))
    site_facts = site_network_facts(data, site_id)
    gateway_id = site_facts.gateway_id if site_facts is not None else None
    gateway = devices.get(gateway_id) if gateway_id is not None else None

    throughput = None
    wan_links: list[dict[str, Any]] = []
    gateway_online = False
    if gateway_id is not None and isinstance(gateway, dict):
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
