# Copyright 2026 UniFi Insights contributors
"""
Strict, read-only grouped device inventory for the device groups panel.

Home Assistant's own integration page nests devices only by
``parent_device_id``, which means "part of the same physical device", and has
no hook to nest them by network link (ADR-016). This module builds the model
an integration-owned admin panel shows instead: each device of one config
entry under the device it is directly connected to.

The module is pure: it reads the facade snapshot and the registry rows the
caller passes, and never creates, updates or removes a registry row. It never
sets ``parent_device_id`` or ``via_device_id`` either.

Grouping is strict. The topology card draws a best-effort graph; this list
only nests a row under a parent when the link is current and unambiguous:

* A Network device nests under the device its classic ``uplink`` names
  (``uplink_source == "current"``) when that MAC matches exactly one device in
  the same site, the link is not to itself, and the device coordinator is
  available. A ``last_uplink`` link is historical and is not used.
* A Protect camera that is not also a Network device nests under the v1
  ``uplinkDeviceId`` of the one client, across the selected sites, that has
  its MAC. The classic client link only corroborates that parent (and then
  supplies the port); naming another device makes the camera ambiguous.
* Edges that form a cycle are all dropped.

Anything that cannot be placed stays at the top level of its site with a
reason, or in the "unassigned" section when its site is unknown; every other
device row the entry owns is listed in the "other" section. No device is
hidden.

The payload is an allowlisted, versioned contract for the panel. It carries
no IP or MAC address: keys derived from records are opaque and address-shaped
text is removed from display labels.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, TypedDict

from .const import DOMAIN, UPLINK_SOURCE_CURRENT
from .data_transforms import (
    client_connection_type,
    get_field,
    nested_dict,
    normalize_mac,
    protect_collection,
    site_device_facts,
    site_network_facts,
    site_section,
)
from .topology import (
    UNKNOWN_DEVICE,
    as_int,
    client_link_parent_keys,
    device_mac,
    display_name,
    site_display_name,
    uplink_parent_port,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from homeassistant.helpers.device_registry import DeviceEntry
    from homeassistant.helpers.entity_registry import RegistryEntry

DEVICE_GROUPS_SCHEMA_VERSION: Final = 1

RowKind = Literal[
    "gateway", "switch", "access_point", "network_device", "camera", "other"
]
RowReason = Literal[
    "no_uplink", "historical", "ambiguous", "parent_not_found", "cycle", "stale"
]
SectionKind = Literal["site", "unassigned", "other"]
GroupsStatus = Literal["ok", "entry_unloaded", "grouping_disabled"]

STATUS_OK: Final = "ok"
STATUS_ENTRY_UNLOADED: Final = "entry_unloaded"
STATUS_GROUPING_DISABLED: Final = "grouping_disabled"

UNASSIGNED_SECTION: Final = "unassigned"
OTHER_SECTION: Final = "other"
UNKNOWN_CAMERA: Final = "Unknown camera"

_CAMERA_UNIQUE_ID_PREFIX: Final = f"{DOMAIN}_camera_"
# Dual-camera children are named "<device> Main Camera" / "<device> Package
# Camera"; their shared device row carries the plain device name.
_DUAL_CAMERA_SUFFIXES: Final = (" Main Camera", " Package Camera")

# Strip address-shaped text from display labels.
_MAC_RE: Final = re.compile(
    r"(?<![0-9a-f])(?:[0-9a-f]{2}(?:[:-][0-9a-f]{2}){5}"
    r"|[0-9a-f]{4}(?:\.[0-9a-f]{4}){2}|[0-9a-f]{12})(?![0-9a-f])",
    re.IGNORECASE,
)
# IPv6 candidates must stand alone (not inside a word such as "Office::Cam")
# and must parse as an address (so "10:30:15" is kept); see _strip_ipv6.
_IPV6_RE: Final = re.compile(
    r"(?<![\w:])(?:[0-9a-f]{0,4}:){2,7}"
    r"(?:\d{1,3}(?:\.\d{1,3}){3}|[0-9a-f]{0,4})(?:%[\w.-]+)?(?![\w:])",
    re.IGNORECASE,
)
_IPV4_RE: Final = re.compile(r"(?<!\d)\d{1,3}(?:\.\d{1,3}){3}(?!\d)")
_EMPTY_PARENS_RE: Final = re.compile(r"\(\s*\)|\[\s*\]")
_SPACES_RE: Final = re.compile(r"\s+")


class DeviceGroupRow(TypedDict):
    """One device in a section; ``parent_key`` names a row of the same section."""

    key: str
    name: str
    kind: RowKind
    ha_device_id: str | None
    parent_key: str | None
    port: int | None
    reason: RowReason | None


class DeviceGroupSection(TypedDict):
    """A site, or the "unassigned" or "other devices" section."""

    key: str
    kind: SectionKind
    title: str | None
    rows: list[DeviceGroupRow]


class DeviceGroups(TypedDict):
    """The full grouped inventory of one config entry (schema version 1)."""

    schema_version: int
    entry_id: str
    status: GroupsStatus
    revision: str
    stale: bool
    sections: list[DeviceGroupSection]


@dataclass
class _Row:
    """A row under construction."""

    key: str
    name: str
    kind: RowKind
    ha_device_id: str | None
    parent_key: str | None = None
    port: int | None = None
    reason: RowReason | None = None

    def as_payload(self) -> DeviceGroupRow:
        """Return the allowlisted row."""
        return {
            "key": self.key,
            "name": self.name,
            "kind": self.kind,
            "ha_device_id": self.ha_device_id,
            "parent_key": self.parent_key,
            "port": self.port,
            "reason": self.reason,
        }


@dataclass
class _Site:
    """One selected site's Network devices, MAC index and rows."""

    devices: dict[str, dict[str, Any]]
    mac_index: dict[str, list[str]]
    rows: dict[str, _Row]


def _strip_ipv6(match: re.Match[str]) -> str:
    """Drop an IPv6-shaped match only when it really is an address."""
    try:
        ipaddress.ip_address(match.group(0))
    except ValueError:
        return match.group(0)
    return ""


def safe_name(value: Any, fallback: str) -> str:
    """Return ``value`` with address-shaped text removed, or ``fallback``."""
    if not isinstance(value, str):
        return fallback
    text = _MAC_RE.sub("", value)
    text = _IPV6_RE.sub(_strip_ipv6, text)
    text = _IPV4_RE.sub("", text)
    text = _EMPTY_PARENS_RE.sub("", text)
    text = _SPACES_RE.sub(" ", text).strip(" -")
    return text or fallback


def _group_key(kind: str, node_key: bytes, *identifiers: str) -> str:
    """Key a row or section with the entry's secret, including legacy IDs."""
    message = json.dumps((kind, *identifiers)).encode()
    digest = hmac.digest(node_key, message, "sha256").hex()[:16]
    return f"{kind}:{digest}"


def _network_kind(kind: str) -> RowKind:
    """Map a Network device kind onto a row kind."""
    if kind in ("gateway", "switch", "access_point"):
        return kind  # type: ignore[return-value]
    return "network_device"


def _registry_name(row: DeviceEntry | None) -> str | None:
    """Return the user's name for a registry row, when they set one."""
    if row is not None and isinstance(row.name_by_user, str) and row.name_by_user:
        return row.name_by_user
    return None


def _device_parent(
    device: Mapping[str, Any],
    device_id: str,
    kind: RowKind,
    mac_index: Mapping[str, list[str]],
    *,
    devices_available: bool,
) -> tuple[str | None, int | None, RowReason | None]:
    """
    Return a Network device's (parent device id, parent port, reason).

    A gateway is always a root and needs no reason. Any other device without
    a strict edge gets the reason it has none.
    """
    if kind == "gateway":
        return None, None, None
    if not devices_available:
        return None, None, "stale"
    topology = device.get("topology")
    block = topology if isinstance(topology, dict) else {}
    parent_mac = normalize_mac(block.get("uplink_mac"))
    if parent_mac is None:
        return None, None, "no_uplink"
    if block.get("uplink_source") != UPLINK_SOURCE_CURRENT:
        return None, None, "historical"
    matches = mac_index.get(parent_mac, [])
    if len(matches) > 1:
        return None, None, "ambiguous"
    if not matches or matches[0] == device_id:
        return None, None, "parent_not_found"
    return matches[0], uplink_parent_port(block), None


def _break_cycles(rows: Mapping[str, _Row]) -> None:
    """Drop every edge that is part of a cycle and mark those rows ``cycle``."""
    done: set[str] = set()
    for start in rows:
        path: list[str] = []
        on_path: set[str] = set()
        key: str | None = start
        while key is not None and key not in done and key not in on_path:
            path.append(key)
            on_path.add(key)
            key = rows[key].parent_key
        if key is not None and key in on_path:
            for member in path[path.index(key) :]:
                row = rows[member]
                row.parent_key = None
                row.port = None
                row.reason = "cycle"
        done.update(path)


def _build_sites(
    data: Mapping[str, Any],
    site_ids: Iterable[str],
    registry_row: Callable[[tuple[str, str]], DeviceEntry | None],
    *,
    node_key: bytes,
    devices_available: bool,
) -> dict[str, _Site]:
    """Build every selected site's Network device rows and strict edges."""
    sites: dict[str, _Site] = {}
    for site_id in site_ids:
        devices = site_section(data, "devices", site_id) or {}
        facts = site_network_facts(data, site_id)
        mac_index: dict[str, list[str]] = {}
        for device_id, device in devices.items():
            mac = device_mac(device, device_id)
            if mac is not None:
                mac_index.setdefault(mac, []).append(device_id)
        rows: dict[str, _Row] = {}
        for device_id, device in devices.items():
            kind = _network_kind(site_device_facts(facts, device_id, device).kind)
            row = registry_row((DOMAIN, f"{site_id}_{device_id}"))
            raw_model = device.get("model")
            model = raw_model if isinstance(raw_model, str) and raw_model else None
            name = _registry_name(row) or display_name(
                device.get("name"), model or UNKNOWN_DEVICE
            )
            parent, port, reason = _device_parent(
                device,
                device_id,
                kind,
                mac_index,
                devices_available=devices_available,
            )
            key = _group_key("net", node_key, site_id, device_id)
            rows[key] = _Row(
                key=key,
                name=safe_name(name, UNKNOWN_DEVICE),
                kind=kind,
                ha_device_id=row.id if row is not None else None,
                parent_key=_group_key("net", node_key, site_id, parent)
                if parent
                else None,
                port=port,
                reason=reason,
            )
        _break_cycles(rows)
        sites[site_id] = _Site(devices=devices, mac_index=mac_index, rows=rows)
    return sites


def _camera_groups(
    data: Mapping[str, Any],
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """Group camera records by physical camera: dual-camera children by parent."""
    groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for camera_id, record in (protect_collection(data, "cameras") or {}).items():
        parent = record.get("_parent_camera_id")
        group_id = parent if isinstance(parent, str) and parent else camera_id
        groups.setdefault(group_id, []).append((camera_id, record))
    return groups


def _camera_name(
    group_id: str, members: list[tuple[str, dict[str, Any]]]
) -> str | None:
    """Return the physical camera's name from its records, parent first."""
    for camera_id, record in sorted(members, key=lambda item: item[0] != group_id):
        name = record.get("name")
        if isinstance(name, str) and name.strip():
            if camera_id != group_id:
                for suffix in _DUAL_CAMERA_SUFFIXES:
                    name = name.removesuffix(suffix)
            return name
    return None


def _camera_mac(members: list[tuple[str, dict[str, Any]]]) -> str | None:
    """Return the first MAC among the camera's records."""
    for _camera_id, record in members:
        mac = normalize_mac(get_field(record, "mac", "macAddress", "mac_address"))
        if mac is not None:
            return mac
    return None


def _camera_client_index(
    data: Mapping[str, Any], site_ids: Iterable[str]
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """Index every selected site's clients by normalized MAC once per build."""
    index: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for site_id in site_ids:
        for client in (site_section(data, "clients", site_id) or {}).values():
            mac = normalize_mac(get_field(client, "macAddress", "mac"))
            if mac is not None:
                index.setdefault(mac, []).append((site_id, client))
    return index


def _place_camera(
    data: Mapping[str, Any],
    sites: Mapping[str, _Site],
    client_index: Mapping[str, list[tuple[str, dict[str, Any]]]],
    mac: str | None,
    *,
    devices_available: bool,
) -> tuple[str | None, str | None, int | None, RowReason | None]:
    """
    Return a client-only camera's (site, parent device id, port, reason).

    The site is the one selected site whose clients hold the camera's MAC
    exactly once. The parent is that client's v1 ``uplinkDeviceId`` in the
    same site; the classic client link only corroborates it.
    """
    if mac is None or not (matches := client_index.get(mac)):
        return None, None, None, "no_uplink"
    if len(matches) > 1:
        return None, None, None, "ambiguous"
    site_id, client = matches[0]
    if not devices_available:
        return site_id, None, None, "stale"
    site = sites[site_id]
    uplink = get_field(client, "uplinkDeviceId", "uplink_device_id")
    if not isinstance(uplink, str) or not uplink:
        return site_id, None, None, "no_uplink"
    if uplink not in site.devices:
        return site_id, None, None, "parent_not_found"
    link = nested_dict(data, "client_links", site_id, mac) or {}
    connection = client_connection_type(client)
    classic_key = client_link_parent_keys(connection)[0]
    classic_mac = normalize_mac(link.get(classic_key))
    port: int | None = None
    if classic_mac is not None:
        if site.mac_index.get(classic_mac) != [uplink]:
            return site_id, None, None, "ambiguous"
        if classic_key == "sw_mac":
            port = as_int(link.get("sw_port"))
    return site_id, uplink, port, None


class _OwnedRows:
    """The registry rows one config entry owns, indexed for lookups."""

    def __init__(
        self,
        entry_id: str,
        device_rows: Iterable[DeviceEntry],
        entity_rows: Iterable[RegistryEntry],
    ) -> None:
        """Keep only the rows owned by ``entry_id``."""
        self.devices = {
            row.id: row for row in device_rows if row.config_entry_id == entry_id
        }
        # Identifiers are unique within one config entry (the registry
        # rejects a collision), so each maps to exactly one row.
        self._by_identifier = {
            identifier: row
            for row in self.devices.values()
            for identifier in row.identifiers
        }
        self.cameras = {
            entity.unique_id: entity
            for entity in entity_rows
            if entity.config_entry_id == entry_id
            and entity.domain == "camera"
            and entity.platform == DOMAIN
        }

    def device(self, identifier: tuple[str, str]) -> DeviceEntry | None:
        """Return the owned device row with ``identifier``, else None."""
        return self._by_identifier.get(identifier)

    def camera_device(self, camera_ids: Iterable[str]) -> DeviceEntry | None:
        """
        Return the device row of the first camera id with an owned entity.

        The camera entity (unique id ``unifi_insights_camera_<id>``) is the
        camera's identity; its device row must be owned by the entry too.
        """
        for camera_id in camera_ids:
            entity = self.cameras.get(f"{_CAMERA_UNIQUE_ID_PREFIX}{camera_id}")
            if entity is not None and entity.device_id in self.devices:
                return self.devices[entity.device_id]
        return None


def _place_cameras(
    data: Mapping[str, Any],
    sites: Mapping[str, _Site],
    registry: _OwnedRows,
    represented: set[str],
    *,
    node_key: bytes,
    devices_available: bool,
) -> list[_Row]:
    """
    Add a row per physical camera to its site; return the unplaced ones.

    A camera whose device row is represented already appears once through
    that row and is skipped.
    ``represented`` gains each camera's device row and the standalone client
    device of its MAC, so neither is listed again under "other devices".
    """
    groups = _camera_groups(data)
    if not groups:
        return []
    client_index = _camera_client_index(data, sites)
    unassigned: list[_Row] = []
    for group_id, members in groups.items():
        device_row = registry.camera_device(
            [group_id, *(cid for cid, _ in members if cid != group_id)]
        )
        if device_row is not None and device_row.id in represented:
            continue
        mac = _camera_mac(members)
        site_id, parent, port, reason = _place_camera(
            data,
            sites,
            client_index,
            mac,
            devices_available=devices_available,
        )
        camera = _Row(
            key=_group_key("cam", node_key, group_id),
            name=safe_name(
                _registry_name(device_row) or _camera_name(group_id, members),
                UNKNOWN_CAMERA,
            ),
            kind="camera",
            ha_device_id=device_row.id if device_row is not None else None,
            parent_key=_group_key("net", node_key, site_id, parent)
            if site_id is not None and parent is not None
            else None,
            port=port,
            reason=reason,
        )
        if site_id is None:
            unassigned.append(camera)
        else:
            sites[site_id].rows[camera.key] = camera
        if device_row is not None:
            represented.add(device_row.id)
        client_row = registry.device((DOMAIN, f"client_{mac}")) if mac else None
        if client_row is not None:
            represented.add(client_row.id)
    return unassigned


def _sorted_rows(rows: Iterable[_Row]) -> list[DeviceGroupRow]:
    """Order rows: gateways first, then by name (case-insensitive), then key."""
    return [
        row.as_payload()
        for row in sorted(
            rows, key=lambda row: (row.kind != "gateway", row.name.casefold(), row.key)
        )
    ]


def finalize_device_groups(payload: DeviceGroups) -> DeviceGroups:
    """Stamp the content revision: a hash of everything but the revision."""
    content = {key: value for key, value in payload.items() if key != "revision"}
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    payload["revision"] = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return payload


def build_terminal_device_groups(entry_id: str, status: GroupsStatus) -> DeviceGroups:
    """Return the empty, final payload of a stream that ends with ``status``."""
    return {
        "schema_version": DEVICE_GROUPS_SCHEMA_VERSION,
        "entry_id": entry_id,
        "status": status,
        "revision": "",
        "stale": False,
        "sections": [],
    }


def build_device_groups(
    data: Mapping[str, Any],
    *,
    entry_id: str,
    node_key: bytes,
    site_ids: Iterable[str],
    devices_available: bool,
    device_rows: Iterable[DeviceEntry],
    entity_rows: Iterable[RegistryEntry],
) -> DeviceGroups:
    """
    Build one config entry's grouped device inventory.

    ``device_rows`` and ``entity_rows`` are the entry's registry rows (from
    ``async_entries_for_config_entry``); a row owned by another entry is
    ignored all the same. ``site_ids`` are the entry's selected sites.
    ``node_key`` is the entry's persistent topology secret for opaque keys.
    """
    registry = _OwnedRows(entry_id, device_rows, entity_rows)
    sites = _build_sites(
        data,
        site_ids,
        registry.device,
        node_key=node_key,
        devices_available=devices_available,
    )
    represented = {
        row.ha_device_id
        for site in sites.values()
        for row in site.rows.values()
        if row.ha_device_id is not None
    }
    unassigned = _place_cameras(
        data,
        sites,
        registry,
        represented,
        node_key=node_key,
        devices_available=devices_available,
    )

    other = [
        _Row(
            key=f"dev:{row.id}",
            name=safe_name(_registry_name(row) or row.name, UNKNOWN_DEVICE),
            kind="other",
            ha_device_id=row.id,
        )
        for row in registry.devices.values()
        if row.id not in represented
    ]

    site_sections: list[DeviceGroupSection] = [
        {
            "key": _group_key("site", node_key, site_id),
            "kind": "site",
            "title": safe_name(site_display_name(data, site_id), "Unknown site"),
            "rows": _sorted_rows(site.rows.values()),
        }
        for site_id, site in sites.items()
    ]
    site_sections.sort(
        key=lambda section: (str(section["title"]).casefold(), section["key"])
    )
    return finalize_device_groups(
        {
            "schema_version": DEVICE_GROUPS_SCHEMA_VERSION,
            "entry_id": entry_id,
            "status": STATUS_OK,
            "revision": "",
            "stale": not devices_available,
            "sections": [
                *site_sections,
                {
                    "key": UNASSIGNED_SECTION,
                    "kind": "unassigned",
                    "title": None,
                    "rows": _sorted_rows(unassigned),
                },
                {
                    "key": OTHER_SECTION,
                    "kind": "other",
                    "title": None,
                    "rows": _sorted_rows(other),
                },
            ],
        }
    )
