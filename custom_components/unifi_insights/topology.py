# Copyright 2026 UniFi Insights contributors
"""
Per-site network topology graph derived from coordinator data.

The module has three sections: the snapshot contract and node normalisers,
the per-entry node id keys, and the graph builder. The contract and graph
sections are pure: they do no I/O and use only ``data_transforms`` from the
integration, so the graph logic is unit-testable in isolation and cheap enough
to run on every coordinator update. Only the node id keys use Home Assistant
storage. Device kind and state come from the published network facts
(``site_network_facts``).

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
import hmac
import json
import logging
import re
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, NotRequired, TypedDict

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN
from .data_transforms import (
    client_connection_type,
    device_online_state,
    device_ports,
    get_field,
    normalize_mac,
    site_device_facts,
    site_network_facts,
)
from .data_transforms import device_kind as network_device_kind

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

    from .data_transforms import SiteNetworkFacts

_LOGGER = logging.getLogger(__name__)

# --- Snapshot contract and node normalisers ----------------------------------

# Frontend contract for the network topology snapshot (schema version 2).
#
# The TypedDicts here are the payload the topology WebSocket commands send, and
# the helpers are the per-field normalisers the graph builder runs every device,
# client and port through: MAC parsing, opaque node ids, device kind and state,
# display and site names, link medium, client connection and port details.
# The shared field and MAC primitives and the network device read model live
# in ``data_transforms``. The kind, state, client and port helpers here are
# adapters over the read-model rules for callers that hold a raw record; the
# builders read the published facts.

TOPOLOGY_SCHEMA_VERSION: Final = 2
# Hard server-side cap on client nodes per site; requests may ask for fewer.
MAX_CLIENTS_PER_SITE: Final = 500

# A "wan" node is one internet connection of a gateway. It is the gateway's
# uplink: its edge has the gateway as source and the WAN as target.
NodeKind = Literal["wan", "gateway", "switch", "access_point", "client", "other"]
NodeState = Literal["online", "offline", "unknown"]
LinkMedium = Literal["wired", "wireless", "unknown"]
ClientConnection = Literal["wired", "wireless"]
TopologyStatus = Literal["ok", "partial", "unavailable"]
IssueSeverity = Literal["info", "warning", "error"]
UnresolvedReason = Literal["parent_not_found", "no_uplink_data"]


class TopologyNode(TypedDict):
    """A device or client in the graph."""

    id: str
    kind: NodeKind
    name: str
    state: NodeState
    model: NotRequired[str]
    ha_device_id: NotRequired[str]
    connection: NotRequired[ClientConnection]
    vlan_id: NotRequired[int]
    network_name: NotRequired[str]


class TopologyEdge(TypedDict):
    """
    A child -> parent link.

    A ``gateway`` node is a ``source`` only of the edges to its own WANs.
    """

    source: str
    target: str
    medium: LinkMedium
    speed_mbps: NotRequired[int]
    parent_port: NotRequired[int]
    child_port: NotRequired[int]
    poe_power_w: NotRequired[float]


class TopologyIssue(TypedDict):
    """A machine-readable problem with the snapshot."""

    code: str
    severity: IssueSeverity


class TopologyUnresolved(TypedDict):
    """A node whose parent could not be placed in the graph."""

    node_id: str
    reason: UnresolvedReason


class TopologyTruncation(TypedDict):
    """How many clients were left out by the client cap."""

    clients_total: int
    clients_included: int


class SiteTopology(TypedDict):
    """
    One site's topology snapshot (contract version 2).

    ``status: "unavailable"`` comes in two shapes. With the
    ``devices_unavailable`` issue the nodes and edges are the last-known data
    (the device coordinator keeps its previous data when a poll fails). With
    ``entry_unloaded`` or ``site_unavailable`` they are empty.
    """

    schema_version: int
    entry_id: str
    site_id: str
    site_name: str
    revision: str
    status: TopologyStatus
    issues: list[TopologyIssue]
    nodes: list[TopologyNode]
    edges: list[TopologyEdge]
    unresolved: list[TopologyUnresolved]
    truncation: TopologyTruncation | None


def first_present(data: Mapping[str, Any], *keys: str) -> Any:
    """Return the first non-None value among ``keys`` (get_field semantics)."""
    return get_field(data, *keys)


def opaque_node_id(prefix: str, key: bytes, raw_id: str) -> str:
    """
    Return a node id that never exposes a MAC address.

    MAC-shaped ids are keyed with a per-entry secret (HMAC-SHA-256): the MAC
    space is small enough to enumerate, especially per vendor prefix, so an
    unkeyed hash of a public value plus the MAC could be reversed by anyone
    who receives the snapshot. Other ids (v1 UUIDs) pass through unchanged.
    """
    mac = normalize_mac(raw_id)
    if mac is None:
        return f"{prefix}:{raw_id}"
    digest = hmac.new(key, mac.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{prefix}:h{digest}"


def wan_node_id(key: bytes, device_id: str, wan_key: str) -> str:
    """
    Return the node id of one WAN connection of a gateway.

    The gateway part is opaque exactly like a device node id, so a WAN node
    never exposes the gateway's MAC; the WAN key ("wan", "wan2") is not
    sensitive and keeps the id stable between polls.
    """
    return f"{opaque_node_id('wan', key, device_id)}:{wan_key.lower()}"


def wan_state(wan: Mapping[str, Any]) -> NodeState:
    """Map a WAN record's ``connected`` flag onto online/offline/unknown."""
    connected = wan.get("connected")
    if connected is True:
        return "online"
    if connected is False:
        return "offline"
    return "unknown"


def device_kind(device: Mapping[str, Any]) -> NodeKind:
    """
    Classify a raw device record with the read model's kind rule.

    The builders read the published facts; this adapter is for callers that
    hold only a raw record.
    """
    return network_device_kind(device)


def device_state(device: Mapping[str, Any]) -> NodeState:
    """Map a raw device record's state with the read model's spec rule."""
    return device_online_state(device)


def site_display_name(data: Mapping[str, Any], site_id: str) -> str:
    """Return a site's display name, falling back to its id."""
    sites = data.get("sites")
    site = sites.get(site_id) if isinstance(sites, dict) else None
    if isinstance(site, dict):
        for key in ("name", "desc"):
            value = site.get(key)
            if isinstance(value, str) and value:
                return value
    return site_id


UNKNOWN_CLIENT: Final = "Unknown client"
UNKNOWN_DEVICE: Final = "Unknown device"


def as_int(value: Any) -> int | None:
    """Return value when it is a real int (not a bool), else None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def uplink_port_index(device: Mapping[str, Any]) -> int | None:
    """Return the index of the port flagged as the device's uplink."""
    for port in device_ports(device):
        if port.is_uplink:
            return as_int(port.raw_index)
    return None


def port_poe_watts(device: Mapping[str, Any], port_idx: int) -> float | None:
    """Return the PoE draw on one port, or None when PoE is off or unknown."""
    for port_facts in device_ports(device):
        if as_int(port_facts.raw_index) != port_idx:
            continue
        port = port_facts.record
        poe = port.get("poe")
        if isinstance(poe, dict):
            if poe.get("enabled") is not True:
                return None
            power = poe.get("power")
        else:
            if first_present(port, "poeEnabled", "poe_enabled") is False:
                return None
            power = first_present(port, "poePower", "poe_power")
        try:
            return round(float(power), 1) if power is not None else None
        except TypeError, ValueError:
            return None
    return None


def link_medium(value: Any) -> LinkMedium:
    """Map a legacy uplink type onto the contract medium."""
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"wire", "wired"}:
            return "wired"
        if lowered in {"wireless", "mesh"}:
            return "wireless"
    return "unknown"


def client_connection(client: Mapping[str, Any]) -> ClientConnection | None:
    """Return a client's wired/wireless connection with the shared client rule."""
    return client_connection_type(client)


def display_name(value: Any, fallback: str) -> str:
    """Return a usable name; blank or MAC-shaped names use the fallback."""
    if isinstance(value, str) and value.strip() and normalize_mac(value) is None:
        return value.strip()
    return fallback


# --- Per-entry node id keys --------------------------------------------------

# Per-entry secret keys for opaque topology node ids.
#
# A MAC-derived node id is an HMAC of the MAC under its entry's key, so a
# snapshot recipient cannot enumerate candidate MACs to reverse it. Keys are
# random, stored in Home Assistant's private storage (never in the config entry,
# so diagnostics cannot leak them) and kept across restarts so node ids stay
# stable. The store is loaded once at component setup, before the topology
# commands are registered, which lets the synchronous command handlers read a
# key without an await (and without a window in which the entry can unload).

_STORAGE_KEY: Final = f"{DOMAIN}.topology_keys"
_STORAGE_VERSION: Final = 1
_SAVE_DELAY: Final = 1
_KEY_BYTES: Final = 32
_KEY_RE: Final = re.compile(rf"[0-9a-f]{{{_KEY_BYTES * 2}}}")


@dataclass
class _NodeKeys:
    """Loaded keys (entry_id -> hex) and the store that persists them."""

    store: Store[dict[str, Any]]
    keys: dict[str, str]

    def data(self) -> dict[str, Any]:
        """Return the payload to persist."""
        return {"keys": dict(self.keys)}


_NODE_KEYS: HassKey[_NodeKeys] = HassKey(f"{DOMAIN}_topology_node_keys")


async def async_load_node_keys(hass: HomeAssistant) -> None:
    """Load the stored keys into memory (once per Home Assistant instance)."""
    if _NODE_KEYS in hass.data:
        return
    store: Store[dict[str, Any]] = Store(
        hass, _STORAGE_VERSION, _STORAGE_KEY, private=True
    )
    try:
        stored = await store.async_load()
    except HomeAssistantError:
        # Losing the keys only changes MAC-derived node ids once; failing
        # component setup over it would take every entity down with it.
        _LOGGER.warning("Could not read topology node keys; generating new ones")
        stored = None
    raw = stored.get("keys") if isinstance(stored, dict) else None
    keys = {
        entry_id: key
        for entry_id, key in (raw.items() if isinstance(raw, dict) else ())
        if isinstance(key, str) and _KEY_RE.fullmatch(key)
    }
    hass.data.setdefault(_NODE_KEYS, _NodeKeys(store, keys))


@callback
def async_get_node_key(hass: HomeAssistant, entry_id: str) -> bytes:
    """Return the entry's node-id key, creating and persisting it on first use."""
    node_keys = hass.data[_NODE_KEYS]
    key = node_keys.keys.get(entry_id)
    if key is None:
        key = node_keys.keys[entry_id] = secrets.token_hex(_KEY_BYTES)
        node_keys.store.async_delay_save(node_keys.data, _SAVE_DELAY)
    return bytes.fromhex(key)


async def async_remove_node_key(hass: HomeAssistant, entry_id: str) -> None:
    """Forget a deleted entry's key."""
    await async_load_node_keys(hass)
    node_keys = hass.data[_NODE_KEYS]
    if node_keys.keys.pop(entry_id, None) is not None:
        node_keys.store.async_delay_save(node_keys.data, _SAVE_DELAY)


# --- Topology graph ----------------------------------------------------------

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


def _kind_and_state(
    site_facts: SiteNetworkFacts | None, device_id: str, device: Mapping[str, Any]
) -> tuple[NodeKind, NodeState]:
    """Return a device's kind and state from the published facts."""
    facts = site_device_facts(site_facts, device_id, device)
    return facts.kind, facts.online_state


def _device_node(
    device: Mapping[str, Any],
    node_id: str,
    kind_state: tuple[NodeKind, NodeState],
    ha_device_id: str | None,
) -> TopologyNode:
    """Build the allowlisted node for one infrastructure device."""
    raw_model = device.get("model")
    model = raw_model if isinstance(raw_model, str) and raw_model else None
    node: TopologyNode = {
        "id": node_id,
        "kind": kind_state[0],
        "name": display_name(device.get("name"), model or UNKNOWN_DEVICE),
        "state": kind_state[1],
    }
    if model is not None:
        node["model"] = model
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
    kind: NodeKind,
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
    if kind == "gateway":
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
    site_facts = site_network_facts(data, site_id)
    clients = _site_map(data, "clients", site_id)
    links = _site_map(data, "client_links", site_id)
    nodes: list[TopologyNode] = []
    edges: list[TopologyEdge] = []
    unresolved: list[TopologyUnresolved] = []

    device_node_ids: dict[str, str] = {}
    devices_by_node: dict[str, dict[str, Any]] = {}
    kinds_by_node: dict[str, NodeKind] = {}
    mac_index: dict[str, str] = {}
    for device_id, device in devices.items():
        if not isinstance(device, dict):
            continue
        node_id = opaque_node_id("dev", node_key, device_id)
        device_node_ids[device_id] = node_id
        devices_by_node[node_id] = device
        kind_state = _kind_and_state(site_facts, device_id, device)
        kinds_by_node[node_id] = kind_state[0]
        nodes.append(
            _device_node(device, node_id, kind_state, ha_device_ids.get(device_id))
        )
        mac = normalize_mac(
            first_present(device, "macAddress", "mac")
        ) or normalize_mac(device_id)
        if mac is not None:
            mac_index[mac] = node_id

    for device_id, node_id in device_node_ids.items():
        if kinds_by_node[node_id] != "gateway":
            continue
        device = devices_by_node[node_id]
        wan_nodes, wan_edges = _wan_links(
            device, device_id, node_id, node_key, ha_device_ids
        )
        nodes.extend(wan_nodes)
        edges.extend(wan_edges)

    for node_id, device in devices_by_node.items():
        link = _device_edge(
            device, kinds_by_node[node_id], node_id, mac_index, devices_by_node
        )
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
