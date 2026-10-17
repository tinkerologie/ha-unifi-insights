# Copyright 2026 UniFi Insights contributors
"""
Per-site network topology graph derived from coordinator data.

This module is pure: it performs no I/O and imports nothing from Home
Assistant or the rest of the integration beyond ``topology_contract``, so the
graph logic is unit-testable in isolation and cheap enough to run on every
coordinator update. The snapshot types and node normalisers live in
``topology_contract``.

Relationship sources, verified against real hardware:

* Device -> parent device comes from the classic ``/stat/device`` ``uplink``
  block, which the device coordinator copies onto each device as
  ``device["topology"]``. The v1 API leaves ``uplink`` and ``type`` null on
  current firmware, so they are only fallbacks.
* Client -> device comes from the v1 client ``uplinkDeviceId``.
* Gateway -> WAN comes from the gateway's merged legacy ``wans`` list (the
  same records the "<WAN name> Connection" binary sensors read).

The coordinator only holds clients the controller currently reports, so
offline clients never appear in the graph.

The legacy ``/stat/device`` poll is best-effort: when one poll fails, devices
carry no ``topology`` block for that cycle and the snapshot reports
``legacy_uplink_missing`` (a flat graph); the next successful poll restores
the tree.

The snapshot is an allowlisted contract for the frontend: MAC addresses, IP
addresses, hostnames, firmware and traffic counters are never emitted, and
MAC-shaped identifiers are replaced by opaque hashes.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Final

from .topology_contract import (
    MAX_CLIENTS_PER_SITE,
    TOPOLOGY_SCHEMA_VERSION,
    UNKNOWN_CLIENT,
    UNKNOWN_DEVICE,
    as_int,
    client_connection,
    device_kind,
    device_state,
    display_name,
    first_present,
    link_medium,
    normalize_mac,
    opaque_node_id,
    port_poe_watts,
    site_display_name,
    uplink_port_index,
    wan_node_id,
    wan_state,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .topology_contract import (
        SiteTopology,
        TopologyEdge,
        TopologyIssue,
        TopologyNode,
        TopologyStatus,
        TopologyTruncation,
        TopologyUnresolved,
        UnresolvedReason,
    )

_KIND_ORDER: Final[dict[str, int]] = {
    "wan": 0,
    "gateway": 1,
    "switch": 2,
    "access_point": 3,
    "other": 4,
    "client": 5,
}


def _site_map(data: Mapping[str, Any], section: str, site_id: str) -> dict[str, Any]:
    """Return ``data[section][site_id]`` when it is a dict, else {}."""
    by_site = data.get(section)
    site_items = by_site.get(site_id) if isinstance(by_site, dict) else None
    return site_items if isinstance(site_items, dict) else {}


def _device_node(
    device: Mapping[str, Any],
    device_id: str,
    node_id: str,
    ha_device_ids: Mapping[str, str],
) -> TopologyNode:
    """Build the allowlisted node for one infrastructure device."""
    raw_model = device.get("model")
    model = raw_model if isinstance(raw_model, str) and raw_model else None
    node: TopologyNode = {
        "id": node_id,
        "kind": device_kind(device),
        "name": display_name(device.get("name"), model or UNKNOWN_DEVICE),
        "state": device_state(device),
    }
    if model is not None:
        node["model"] = model
    ha_device_id = ha_device_ids.get(device_id)
    if ha_device_id:
        node["ha_device_id"] = ha_device_id
    return node


def _wan_links(
    device: Mapping[str, Any],
    device_id: str,
    gateway_node_id: str,
    node_key: bytes,
    ha_device_ids: Mapping[str, str],
) -> tuple[list[TopologyNode], list[TopologyEdge]]:
    """
    Return one gateway's WAN nodes and their gateway -> WAN edges.

    Only the name and connected state are emitted: the WAN IP, the raw
    controller status and the reachability probe stay out of the snapshot.
    """
    wans = device.get("wans")
    nodes: list[TopologyNode] = []
    edges: list[TopologyEdge] = []
    seen: set[str] = set()
    for wan in wans if isinstance(wans, list) else ():
        wan_key = wan.get("key") if isinstance(wan, dict) else None
        if not isinstance(wan_key, str) or not wan_key:
            continue
        node_id = wan_node_id(node_key, device_id, wan_key)
        if node_id in seen:
            continue
        seen.add(node_id)
        node: TopologyNode = {
            "id": node_id,
            "kind": "wan",
            "name": display_name(wan.get("name"), wan_key.upper()),
            "state": wan_state(wan),
        }
        ha_device_id = ha_device_ids.get(device_id)
        if ha_device_id:
            node["ha_device_id"] = ha_device_id
        nodes.append(node)
        edges.append(
            {"source": gateway_node_id, "target": node_id, "medium": "unknown"}
        )
    return nodes, edges


def _client_node(
    client: Mapping[str, Any], node_id: str, link: Mapping[str, Any]
) -> TopologyNode:
    """Build the allowlisted node for one client."""
    node: TopologyNode = {
        "id": node_id,
        "kind": "client",
        "name": display_name(client.get("name"), UNKNOWN_CLIENT),
        "state": "offline" if client.get("connected") is False else "online",
    }
    connection = client_connection(client)
    if connection is not None:
        node["connection"] = connection
    vlan = as_int(link.get("vlan"))
    if vlan is not None:
        node["vlan_id"] = vlan
    network_name = link.get("network_name")
    if isinstance(network_name, str) and network_name:
        node["network_name"] = network_name
    return node


def _client_edge(
    client: Mapping[str, Any],
    node_id: str,
    link: Mapping[str, Any],
    device_node_ids: Mapping[str, str],
    mac_index: Mapping[str, str],
) -> TopologyEdge | UnresolvedReason:
    """Return a client's edge to its AP/switch, or why it has none."""
    connection = client_connection(client)
    link_parent_keys = (
        ("ap_mac", "sw_mac") if connection == "wireless" else ("sw_mac", "ap_mac")
    )
    link_parent: str | None = None
    link_macs = [normalize_mac(link.get(key)) for key in link_parent_keys]
    for mac in link_macs:
        if mac is not None and mac in mac_index:
            link_parent = mac_index[mac]
            break
    uplink = first_present(client, "uplinkDeviceId", "uplink_device_id")
    target = device_node_ids.get(uplink) if isinstance(uplink, str) else None
    if target is None:
        target = link_parent
    if target is None:
        has_reference = isinstance(uplink, str) or any(link_macs)
        return "parent_not_found" if has_reference else "no_uplink_data"

    edge: TopologyEdge = {
        "source": node_id,
        "target": target,
        "medium": connection or "unknown",
    }
    # /stat/sta is up to five minutes older than uplinkDeviceId, so its port
    # is only trusted when it names the same switch the edge points at.
    sw_parent = mac_index.get(normalize_mac(link.get("sw_mac")) or "")
    sw_port = as_int(link.get("sw_port"))
    if connection == "wired" and sw_port is not None and sw_parent == target:
        edge["parent_port"] = sw_port
    return edge


def _device_edge(
    device: Mapping[str, Any],
    node_id: str,
    mac_index: Mapping[str, str],
    devices_by_node: Mapping[str, Mapping[str, Any]],
) -> TopologyEdge | UnresolvedReason | None:
    """
    Return the device's edge to its parent, an unresolved reason, or None.

    None means "no edge and nothing wrong": a gateway or a self-reference.
    Gateways are always roots, so their uplink data is ignored for parentage.
    A gateway's legacy uplink can come from LLDP
    (``uplink_source: lldp_uplink``) and often names an upstream ISP router
    or a LAN-side switch; the switch case can even be mutual over one link
    (gateway names switch, switch names gateway), which would otherwise put
    the switch above the gateway.
    """
    if device_kind(device) == "gateway":
        return None
    topology = device.get("topology")
    block = topology if isinstance(topology, dict) else {}
    parent_mac = normalize_mac(block.get("uplink_mac"))
    if parent_mac is None:
        return "no_uplink_data"
    parent_id = mac_index.get(parent_mac)
    if parent_id is None:
        return "parent_not_found"
    if parent_id == node_id:
        return None

    edge: TopologyEdge = {
        "source": node_id,
        "target": parent_id,
        "medium": link_medium(block.get("uplink_type")),
    }
    speed = as_int(block.get("uplink_speed"))
    if speed is not None:
        edge["speed_mbps"] = speed
    parent_port = as_int(block.get("uplink_remote_port"))
    if parent_port is not None:
        edge["parent_port"] = parent_port
        poe_watts = port_poe_watts(devices_by_node[parent_id], parent_port)
        if poe_watts is not None:
            edge["poe_power_w"] = poe_watts
    child_port = as_int(block.get("uplink_port_idx"))
    if child_port is None:
        child_port = uplink_port_index(device)
    if child_port is not None:
        edge["child_port"] = child_port
    return edge


def _select_clients(
    clients: Mapping[str, Any], max_clients: int
) -> tuple[list[tuple[str, dict[str, Any]]], int]:
    """Apply the client cap: wired first, then by name, then by id."""
    valid = [
        (client_id, client)
        for client_id, client in clients.items()
        if isinstance(client, dict)
    ]
    limit = max(0, min(max_clients, MAX_CLIENTS_PER_SITE))
    valid.sort(
        key=lambda item: (
            client_connection(item[1]) != "wired",
            display_name(item[1].get("name"), UNKNOWN_CLIENT).casefold(),
            item[0],
        )
    )
    return valid[:limit], len(valid)


def _status(issues: list[TopologyIssue]) -> TopologyStatus:
    """Derive the snapshot status from its issues."""
    if any(issue["severity"] == "error" for issue in issues):
        return "unavailable"
    return "partial" if issues else "ok"


def _finalize(snapshot: SiteTopology) -> SiteTopology:
    """Sort every list deterministically and stamp the content revision."""
    snapshot["nodes"].sort(key=lambda node: (_KIND_ORDER[node["kind"]], node["id"]))
    snapshot["edges"].sort(key=lambda edge: (edge["source"], edge["target"]))
    snapshot["unresolved"].sort(key=lambda entry: entry["node_id"])
    snapshot["issues"].sort(key=lambda issue: issue["code"])
    content = {key: value for key, value in snapshot.items() if key != "revision"}
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    snapshot["revision"] = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return snapshot


def build_unavailable_topology(
    entry_id: str, site_id: str, site_name: str, issue_code: str
) -> SiteTopology:
    """Return an empty snapshot that reports why no graph is available."""
    return _finalize(
        {
            "schema_version": TOPOLOGY_SCHEMA_VERSION,
            "entry_id": entry_id,
            "site_id": site_id,
            "site_name": site_name,
            "revision": "",
            "status": "unavailable",
            "issues": [{"code": issue_code, "severity": "error"}],
            "nodes": [],
            "edges": [],
            "unresolved": [],
            "truncation": None,
        }
    )


def build_site_topology(
    data: Mapping[str, Any],
    entry_id: str,
    site_id: str,
    *,
    node_key: bytes,
    ha_device_ids: Mapping[str, str],
    max_clients: int = MAX_CLIENTS_PER_SITE,
    devices_available: bool = True,
) -> SiteTopology:
    """
    Build the allowlisted topology snapshot for one site.

    ``node_key`` is the entry's secret for opaque MAC-derived node ids; it must
    stay the same across calls for node ids to be stable.
    """
    devices = _site_map(data, "devices", site_id)
    clients = _site_map(data, "clients", site_id)
    links = _site_map(data, "client_links", site_id)
    nodes: list[TopologyNode] = []
    edges: list[TopologyEdge] = []
    unresolved: list[TopologyUnresolved] = []

    device_node_ids: dict[str, str] = {}
    devices_by_node: dict[str, dict[str, Any]] = {}
    mac_index: dict[str, str] = {}
    for device_id, device in devices.items():
        if not isinstance(device, dict):
            continue
        node_id = opaque_node_id("dev", node_key, device_id)
        device_node_ids[device_id] = node_id
        devices_by_node[node_id] = device
        nodes.append(_device_node(device, device_id, node_id, ha_device_ids))
        mac = normalize_mac(
            first_present(device, "macAddress", "mac")
        ) or normalize_mac(device_id)
        if mac is not None:
            mac_index[mac] = node_id

    for device_id, node_id in device_node_ids.items():
        device = devices_by_node[node_id]
        if device_kind(device) != "gateway":
            continue
        wan_nodes, wan_edges = _wan_links(
            device, device_id, node_id, node_key, ha_device_ids
        )
        nodes.extend(wan_nodes)
        edges.extend(wan_edges)

    for node_id, device in devices_by_node.items():
        link = _device_edge(device, node_id, mac_index, devices_by_node)
        if isinstance(link, dict):
            edges.append(link)
        elif link is not None:
            unresolved.append({"node_id": node_id, "reason": link})

    included, clients_total = _select_clients(clients, max_clients)
    for client_id, client in included:
        node_id = opaque_node_id("cli", node_key, client_id)
        client_mac = normalize_mac(first_present(client, "macAddress", "mac"))
        raw_link = links.get(client_mac) if client_mac is not None else None
        client_link_data = raw_link if isinstance(raw_link, dict) else {}
        nodes.append(_client_node(client, node_id, client_link_data))
        client_link = _client_edge(
            client, node_id, client_link_data, device_node_ids, mac_index
        )
        if isinstance(client_link, dict):
            edges.append(client_link)
        else:
            unresolved.append({"node_id": node_id, "reason": client_link})

    issues: list[TopologyIssue] = []
    by_site = data.get("devices")
    if not devices_available or not isinstance(by_site, dict) or site_id not in by_site:
        issues.append({"code": "devices_unavailable", "severity": "error"})
    if devices_by_node and not any(
        isinstance(device.get("topology"), dict) for device in devices_by_node.values()
    ):
        issues.append({"code": "legacy_uplink_missing", "severity": "warning"})
    if unresolved:
        issues.append({"code": "parents_unresolved", "severity": "warning"})
    truncation: TopologyTruncation | None = None
    if len(included) < clients_total:
        truncation = {"clients_total": clients_total, "clients_included": len(included)}
        issues.append({"code": "clients_truncated", "severity": "info"})

    return _finalize(
        {
            "schema_version": TOPOLOGY_SCHEMA_VERSION,
            "entry_id": entry_id,
            "site_id": site_id,
            "site_name": site_display_name(data, site_id),
            "revision": "",
            "status": _status(issues),
            "issues": issues,
            "nodes": nodes,
            "edges": edges,
            "unresolved": unresolved,
            "truncation": truncation,
        }
    )
