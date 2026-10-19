# Copyright (c) 2026 Ruaan Deysel

"""
Frontend contract for the network topology snapshot (schema version 2).

The TypedDicts here are the payload the topology WebSocket commands send, and
the helpers are the per-field normalisers the graph builder runs every device,
client and port through: MAC parsing, opaque node ids, device kind and state,
display and site names, link medium, client connection and port details.
Like ``topology``, this module is pure (no I/O, no Home Assistant imports).
Its only integration import is the equally pure ``data_transforms``, which
owns the shared field and MAC primitives and the network device read model,
so ``coordinators/config.py`` can use ``normalize_mac`` without an import
cycle. The kind, state, client and port helpers here are adapters over the
read-model rules for callers that hold a raw record; the builders read the
published facts.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Any, Final, Literal, NotRequired, TypedDict

from .data_transforms import (
    client_connection_type,
    device_online_state,
    device_ports,
    get_field,
    normalize_mac,
)
from .data_transforms import device_kind as network_device_kind

if TYPE_CHECKING:
    from collections.abc import Mapping

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
