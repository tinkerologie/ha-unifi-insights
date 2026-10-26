# Copyright (c) 2026 Ruaan Deysel

"""
WebSocket API exposing the network topology graph to the frontend.

Commands are available to any authenticated user (dashboards are used by
non-admins); privacy comes from the topology builder's field allowlist and
from scoping every request to one loaded entry and one selected site.

The one exception is ``unifi_insights/topology/node_details``: it returns the
IP and MAC addresses the snapshot never carries, so it is admin-only, and it
serves a single node resolved on the server.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from functools import partial
from typing import TYPE_CHECKING, Any

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.hass_dict import HassKey

from .console_identity import async_get_device_entry, resolve_console_namespace
from .const import DOMAIN, TIMELINE_MAX_HOURS
from .dashboard_snapshots import (
    TIMELINE_DEVICE_COLLECTIONS,
    as_dict,
    build_internet_activity_snapshot,
    build_no_protect_snapshot,
    build_performance_snapshot,
    build_protect_snapshot,
    build_site_health_snapshot,
    build_timeline_snapshot,
    build_unavailable_internet_activity,
    build_unavailable_performance,
    build_unavailable_protect_snapshot,
    build_unavailable_site_health,
    build_unavailable_timeline,
)
from .data_transforms import NETWORK_FACTS_KEY, site_section
from .device_tracker import _client_tracker_entries, _mac_from_unique_id
from .entity import get_field
from .topology import (
    MAX_CLIENTS_PER_SITE,
    async_get_node_key,
    build_site_topology,
    build_unavailable_topology,
    device_kind,
    opaque_node_id,
    site_display_name,
    wan_node_id,
)

# Schemas are typed against probatio, the library HA 2026.10 annotates its schema
# arguments with. Runtime still imports voluptuous: HA 2026.10 aliases it to probatio,
# and the 2026.9 minimum ships real voluptuous, so only the type checker sees probatio.
# CarrierFabricData is imported inside functions: __init__.py imports this
# module while it loads, before the class exists.
if TYPE_CHECKING:
    import probatio as vol
else:
    import voluptuous as vol

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

    from . import UnifiInsightsConfigEntry
    from .topology import NodeKind, SiteTopology

_LOGGER = logging.getLogger(__name__)

ERR_ENTRY_NOT_FOUND = "entry_not_found"
ERR_ENTRY_NOT_LOADED = "entry_not_loaded"
ERR_SITE_NOT_SELECTED = "site_not_selected"
ERR_NODE_NOT_FOUND = "node_not_found"

_MAX_CLIENTS = vol.All(vol.Coerce(int), vol.Range(min=0, max=MAX_CLIENTS_PER_SITE))
_SNAPSHOT_SCHEMA: dict[str | vol.Marker, Any] = {
    vol.Required("entry_id"): str,
    vol.Required("site_id"): str,
    vol.Optional("max_clients", default=MAX_CLIENTS_PER_SITE): _MAX_CLIENTS,
}

# Node ids are "<prefix>:<id>" (see topology.build_site_topology), one prefix per
# coordinator section.
_NODE_SECTIONS = (("dev", "devices"), ("cli", "clients"))
# Key spellings the coordinators' device and client records use (see entity.py
# and device_tracker.py).
_IP_KEYS = ("ipAddress", "ip_address", "ip")
_MAC_KEYS = ("macAddress", "mac_address", "mac")


class _RequestError(Exception):
    """A request that cannot be served, carrying a WebSocket error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@callback
def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register the topology WebSocket commands (once per HA instance)."""
    websocket_api.async_register_command(hass, ws_topology_sources)
    websocket_api.async_register_command(hass, ws_topology_get)
    websocket_api.async_register_command(hass, ws_topology_subscribe)
    # Extend HA's native schema so required fields work with both Voluptuous
    # (HA 2026.9) and Probatio (HA 2026.10+).
    websocket_api.async_register_command(
        hass,
        "unifi_insights/topology/node_details",
        ws_topology_node_details,
        websocket_api.BASE_COMMAND_MESSAGE_SCHEMA.extend(
            {
                "type": "unifi_insights/topology/node_details",
                "entry_id": str,
                "site_id": str,
                "node_id": str,
            },
            required=True,
        ),
    )
    websocket_api.async_register_command(hass, ws_site_health_get)
    websocket_api.async_register_command(hass, ws_site_health_subscribe)
    websocket_api.async_register_command(hass, ws_internet_activity_get)
    websocket_api.async_register_command(hass, ws_internet_activity_subscribe)
    websocket_api.async_register_command(hass, ws_performance_get)
    websocket_api.async_register_command(hass, ws_performance_subscribe)
    websocket_api.async_register_command(hass, ws_protect_sources)
    websocket_api.async_register_command(hass, ws_protect_get)
    websocket_api.async_register_command(hass, ws_protect_subscribe)
    websocket_api.async_register_command(hass, ws_timeline_get)
    websocket_api.async_register_command(hass, ws_timeline_subscribe)


def _resolve_entry(hass: HomeAssistant, entry_id: str) -> UnifiInsightsConfigEntry:
    """Return a loaded UniFi Insights entry or raise _RequestError."""
    from . import CarrierFabricData  # noqa: PLC0415

    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        msg = f"No UniFi Insights config entry {entry_id}"
        raise _RequestError(ERR_ENTRY_NOT_FOUND, msg)
    if (
        entry.state is not ConfigEntryState.LOADED
        or getattr(entry, "runtime_data", None) is None
        or isinstance(entry.runtime_data, CarrierFabricData)
    ):
        msg = f"UniFi Insights config entry {entry_id} is not loaded"
        raise _RequestError(ERR_ENTRY_NOT_LOADED, msg)
    return entry


def _require_selected_site(entry: UnifiInsightsConfigEntry, site_id: str) -> None:
    """Raise _RequestError unless the entry has the site selected."""
    if site_id not in entry.runtime_data.config_coordinator.get_site_ids():
        msg = f"Site {site_id} is not enabled for this UniFi Insights entry"
        raise _RequestError(ERR_SITE_NOT_SELECTED, msg)


def _device_in_entry(device: dr.DeviceEntry, entry_id: str) -> bool:
    """Return whether a registry device belongs to the config entry."""
    return device.config_entry_id == entry_id


def _ha_device_ids(
    hass: HomeAssistant, data: Mapping[str, Any], site_id: str, entry_id: str
) -> dict[str, str]:
    """
    Map the site's device ids to their Home Assistant device registry ids.

    Scoped to this config entry: device identifiers and connections are no
    longer unique across entries (HA 2026.9+), so an unscoped lookup could
    return another loaded UniFi Insights console's device with the same
    site/device id pair. async_get_device_entry also keeps this off the
    deprecated DeviceRegistry.async_get_device path (removed in 2027.8).
    """
    registry = dr.async_get(hass)
    result: dict[str, str] = {}
    for device_id in site_section(data, "devices", site_id) or ():
        device = async_get_device_entry(
            registry, (DOMAIN, f"{site_id}_{device_id}"), entry_id
        )
        if device is not None:
            result[device_id] = device.id
    return result


def _build_snapshot(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    site_id: str,
    max_clients: int,
) -> SiteTopology:
    """Build one site's snapshot, enforcing that the site is selected."""
    _require_selected_site(entry, site_id)
    facade = entry.runtime_data.coordinator
    return build_site_topology(
        facade.data,
        entry.entry_id,
        site_id,
        node_key=async_get_node_key(hass, entry.entry_id),
        ha_device_ids=_ha_device_ids(hass, facade.data, site_id, entry.entry_id),
        max_clients=max_clients,
        devices_available=facade.device_available,
    )


def _find_node(
    data: Mapping[str, Any], site_id: str, node_key: bytes, node_id: str
) -> tuple[NodeKind, str, dict[str, Any]]:
    """
    Return (kind, raw id, record) of the one device, client or WAN ``node_id`` names.

    The id is never decoded: each record of the validated site is given the id
    the snapshot would give it and compared. A node id that matches no record,
    or more than one (two ids that normalise to the same MAC share a hash),
    names no node. A WAN's raw id is its gateway's device id and its record is
    the WAN record.
    """
    by_section = {
        section: site_section(data, section, site_id) or {}
        for _, section in _NODE_SECTIONS
    }
    matches: list[tuple[NodeKind, str, dict[str, Any]]] = []
    for prefix, section in _NODE_SECTIONS:
        for raw_id, record in by_section[section].items():
            if opaque_node_id(prefix, node_key, raw_id) == node_id:
                kind = device_kind(record) if section == "devices" else "client"
                matches.append((kind, raw_id, record))
    for raw_id, record in by_section["devices"].items():
        if device_kind(record) != "gateway":
            continue
        wans = record.get("wans")
        for wan in wans if isinstance(wans, list) else ():
            wan_key = wan.get("key") if isinstance(wan, dict) else None
            if (
                isinstance(wan_key, str)
                and wan_key
                and wan_node_id(node_key, raw_id, wan_key) == node_id
            ):
                # The snapshot keeps the first record per WAN node id.
                matches.append(("wan", raw_id, wan))
                break
    if len(matches) != 1:
        msg = f"No node {node_id} in site {site_id}"
        raise _RequestError(ERR_NODE_NOT_FOUND, msg)
    return matches[0]


def _device_targets(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    site_id: str,
    device_id: str,
    entity_key: str = "device_status",
) -> dict[str, str]:
    """
    Return the registry device and connectivity entity of an infrastructure node.

    ``entity_key`` picks the connectivity sensor: the device status, or a WAN
    node's ``wan_link_<key>`` connection sensor on its gateway.

    Each registry row must belong to this config entry: the device lookup's
    older-Home-Assistant fallbacks match on identifier alone, and an entity's
    unique id is unique across entries, so the row found may be another
    console's. A target that is absent or foreign is left out.
    """
    targets: dict[str, str] = {}
    device = async_get_device_entry(
        dr.async_get(hass), (DOMAIN, f"{site_id}_{device_id}"), entry.entry_id
    )
    if device is not None and _device_in_entry(device, entry.entry_id):
        targets["device_id"] = device.id
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{site_id}_{device_id}_{entity_key}"
    )
    entity = registry.async_get(entity_id) if entity_id is not None else None
    if entity is not None and entity.config_entry_id == entry.entry_id:
        targets["entity_id"] = entity.entity_id
    return targets


def _client_targets(
    hass: HomeAssistant, entry: UnifiInsightsConfigEntry, mac: str | None
) -> dict[str, str]:
    """
    Return the tracker entity and own device of a client node.

    Prefer the current tracker unique id, falling back to a legacy bare MAC.
    Each target must belong to this entry. The standalone client device is
    resolved independently: the tracker's own device may be its uplink.
    """
    if mac is None:
        return {}
    mac = mac.lower()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "device_tracker", DOMAIN, f"{DOMAIN}_{mac}"
    )
    tracker = registry.async_get(entity_id) if entity_id is not None else None
    if tracker is None or tracker.config_entry_id != entry.entry_id:
        # Older ScannerEntity rows used the MAC as the API spelled it, so
        # preserve the existing case-insensitive legacy lookup.
        legacy = [
            candidate
            for candidate in _client_tracker_entries(registry, entry.entry_id)
            if not candidate.unique_id.startswith(f"{DOMAIN}_")
            and _mac_from_unique_id(candidate.unique_id) == mac
        ]
        tracker = legacy[0] if len(legacy) == 1 else None
    targets: dict[str, str] = {}
    if tracker is not None and tracker.config_entry_id == entry.entry_id:
        targets["entity_id"] = tracker.entity_id
    device = async_get_device_entry(
        dr.async_get(hass), (DOMAIN, f"client_{mac}"), entry.entry_id
    )
    if device is not None and _device_in_entry(device, entry.entry_id):
        targets["device_id"] = device.id
    return targets


def _build_node_details(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    site_id: str,
    node_id: str,
) -> dict[str, str]:
    """
    Resolve one node of a selected site to its addresses and registry targets.

    Every key but ``kind`` is left out when its value is absent or ambiguous.
    """
    _require_selected_site(entry, site_id)
    kind, raw_id, record = _find_node(
        entry.runtime_data.coordinator.data,
        site_id,
        async_get_node_key(hass, entry.entry_id),
        node_id,
    )
    details: dict[str, str] = {"kind": kind}
    for key, spellings in (("ip_address", _IP_KEYS), ("mac_address", _MAC_KEYS)):
        value = get_field(record, *spellings)
        if isinstance(value, str) and value.strip():
            details[key] = value.lower() if key == "mac_address" else value
    if kind == "client":
        details.update(_client_targets(hass, entry, details.get("mac_address")))
    elif kind == "wan":
        details.update(
            _device_targets(hass, entry, site_id, raw_id, f"wan_link_{record['key']}")
        )
    else:
        details.update(_device_targets(hass, entry, site_id, raw_id))
    return details


def _snapshot_inputs(
    hass: HomeAssistant, entry: UnifiInsightsConfigEntry, site_id: str
) -> tuple[object, ...]:
    """
    Return what one site's snapshot is built from, for cheap change detection.

    The coordinators publish each poll by replacing the per-site dicts (the
    device coordinator builds fresh ``devices``/``clients`` dicts per site,
    the config coordinator a fresh ``sites`` dict), never by mutating them in
    place, so object identity says whether the data changed. The registry
    size stands in for the ``ha_device_id`` links: it changes when a device
    is first registered or removed. Any other registry-only change is picked
    up at the next data poll.
    """
    runtime = entry.runtime_data
    data = runtime.coordinator.data
    per_site = []
    for section in ("devices", "clients", "client_links", NETWORK_FACTS_KEY):
        by_site = data.get(section)
        per_site.append(by_site.get(site_id) if isinstance(by_site, dict) else None)
    return (
        data.get("sites"),
        *per_site,
        runtime.coordinator.device_available,
        site_id in runtime.config_coordinator.get_site_ids(),
        len(dr.async_get(hass).devices),
    )


def _same_inputs(old: tuple[object, ...], new: tuple[object, ...]) -> bool:
    """
    Compare inputs by identity (data dicts) or value (flags and counts).

    The previous inputs hold the dicts themselves rather than their id(), so
    a replaced dict stays alive and its address cannot be reused by a new
    one and mistaken for "unchanged".
    """
    return all(
        a is b or (isinstance(a, int) and a == b) for a, b in zip(old, new, strict=True)
    )


def _runtime_snapshot_inputs(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    site_id: str,
    payload_type: str,
) -> tuple[object, ...]:
    """Return generic per-site inputs for non-topology snapshots."""
    runtime = entry.runtime_data
    data = runtime.coordinator.data
    sites = data.get("sites")
    devices = data.get("devices")
    stats = data.get("stats")
    clients = data.get("clients")
    network_facts = data.get(NETWORK_FACTS_KEY)
    protect_events = (
        _protect_collections(
            data.get("protect"), keys=("events", *TIMELINE_DEVICE_COLLECTIONS)
        )
        if payload_type == "timeline"
        else (None,)
    )
    return (
        sites,
        devices.get(site_id) if isinstance(devices, dict) else None,
        stats.get(site_id) if isinstance(stats, dict) else None,
        clients.get(site_id) if isinstance(clients, dict) else None,
        network_facts.get(site_id) if isinstance(network_facts, dict) else None,
        *protect_events,
        data.get("internet_activity"),
        data.get("internet_activity_unavailable"),
        runtime.coordinator.device_available,
        runtime.coordinator.protect_available,
        site_id in runtime.config_coordinator.get_site_ids(),
        len(dr.async_get(hass).devices),
        len(er.async_get(hass).entities),
    )


def _protect_collections(
    value: object,
    *,
    keys: tuple[str, ...] = ("cameras", "chimes", "nvrs"),
) -> tuple[object, ...]:
    """
    Return the Protect collections a snapshot reads, for identity gating.

    The Protect cache is copy-on-write (ADR-012): a frame replaces every
    collection it changes and leaves the others as they were, so a collection
    that is the same object as last time is unchanged.
    """
    if not isinstance(value, Mapping):
        return (None,) * len(keys)
    return tuple(value.get(key) for key in keys)


def _runtime_protect_inputs(
    hass: HomeAssistant, entry: UnifiInsightsConfigEntry
) -> tuple[object, ...]:
    """Return inputs that drive Protect snapshot rebuilds."""
    runtime = entry.runtime_data
    data = runtime.coordinator.data
    return (
        *_protect_collections(data.get("protect")),
        runtime.coordinator.protect_available,
        len(dr.async_get(hass).devices),
        len(er.async_get(hass).entities),
    )


def _generic_site_payload(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    site_id: str,
    payload_type: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one of the site-scoped dashboard payloads."""
    runtime = entry.runtime_data
    if site_id not in runtime.config_coordinator.get_site_ids():
        msg = f"Site {site_id} is not enabled for this UniFi Insights entry"
        raise _RequestError(ERR_SITE_NOT_SELECTED, msg)

    facade = runtime.coordinator
    data = facade.data
    if payload_type == "site_health":
        return build_site_health_snapshot(
            data,
            entry_id=entry.entry_id,
            site_id=site_id,
            ha_device_ids=_ha_device_ids(hass, data, site_id, entry.entry_id),
            devices_available=facade.device_available,
        )
    if payload_type == "internet_activity":
        return build_internet_activity_snapshot(
            data,
            entry_id=entry.entry_id,
            site_id=site_id,
            entity_registry=er.async_get(hass),
            namespace=resolve_console_namespace(entry),
        )
    if payload_type == "performance":
        return build_performance_snapshot(
            data,
            entry_id=entry.entry_id,
            site_id=site_id,
            ha_device_ids=_ha_device_ids(hass, data, site_id, entry.entry_id),
            devices_available=facade.device_available,
        )
    if payload_type == "timeline":
        opts = options or {}
        return build_timeline_snapshot(
            data,
            entry_id=entry.entry_id,
            site_id=site_id,
            categories=opts.get("categories"),
            hours=opts.get("hours", 6),
            max_items=opts.get("max_items", 10),
        )
    msg = f"Unsupported payload type: {payload_type}"
    code = "invalid_format"
    raise _RequestError(code, msg)


def _generic_site_unavailable(
    entry: UnifiInsightsConfigEntry,
    site_id: str,
    payload_type: str,
    code: str,
) -> dict[str, Any]:
    """Build an unavailable payload for one type."""
    if payload_type == "site_health":
        return build_unavailable_site_health(entry.entry_id, site_id, code)
    if payload_type == "internet_activity":
        return build_unavailable_internet_activity(entry.entry_id, site_id, code)
    if payload_type == "performance":
        return build_unavailable_performance(entry.entry_id, site_id, code)
    if payload_type == "timeline":
        return build_unavailable_timeline(entry.entry_id, site_id, code)
    return {
        "entry_id": entry.entry_id,
        "site_id": site_id,
        "status": "unavailable",
        "issues": [{"code": code, "severity": "error"}],
        "revision": "",
    }


def _protect_payload(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
) -> dict[str, Any]:
    """Build protect payload for one entry."""
    runtime = entry.runtime_data
    data = runtime.coordinator.data
    protect_data = data.get("protect")
    if not isinstance(protect_data, dict) or runtime.protect_coordinator is None:
        return build_no_protect_snapshot(entry.entry_id, entry.title)

    ha_device_ids: dict[str, str] = {}
    registry = dr.async_get(hass)
    cameras = as_dict(protect_data.get("cameras"))
    chimes = as_dict(protect_data.get("chimes"))
    for device_id in cameras:
        device = async_get_device_entry(
            registry, (DOMAIN, f"protect_camera_{device_id}"), entry.entry_id
        )
        if device is not None:
            ha_device_ids[f"protect_camera_{device_id}"] = device.id
            ha_device_ids[str(device_id)] = device.id
    for device_id in chimes:
        device = async_get_device_entry(
            registry, (DOMAIN, f"protect_chime_{device_id}"), entry.entry_id
        )
        if device is not None:
            ha_device_ids[f"protect_chime_{device_id}"] = device.id
            ha_device_ids[str(device_id)] = device.id

    return build_protect_snapshot(
        data,
        entry_id=entry.entry_id,
        entry_title=entry.title,
        entity_registry=er.async_get(hass),
        ha_device_ids=ha_device_ids,
        protect_available=runtime.coordinator.protect_available,
    )


@websocket_api.websocket_command(
    {vol.Required("type"): "unifi_insights/topology/sources"}
)
@callback
def ws_topology_sources(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List loaded entries and their selected sites for the card editor."""
    from . import CarrierFabricData  # noqa: PLC0415

    sources = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if runtime is None or isinstance(runtime, CarrierFabricData):
            continue
        data = runtime.coordinator.data
        sources.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "sites": [
                    {"id": site_id, "name": site_display_name(data, site_id)}
                    for site_id in runtime.config_coordinator.get_site_ids()
                ],
            }
        )
    connection.send_result(msg["id"], sources)


@websocket_api.websocket_command(
    {vol.Required("type"): "unifi_insights/topology/get", **_SNAPSHOT_SCHEMA}
)
@callback
def ws_topology_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return one site's topology snapshot."""
    try:
        entry = _resolve_entry(hass, msg["entry_id"])
        snapshot = _build_snapshot(hass, entry, msg["site_id"], msg["max_clients"])
    except _RequestError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    connection.send_result(msg["id"], snapshot)


@websocket_api.require_admin
@callback
def ws_topology_node_details(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the addresses and registry targets of one topology node."""
    try:
        entry = _resolve_entry(hass, msg["entry_id"])
        details = _build_node_details(hass, entry, msg["site_id"], msg["node_id"])
    except _RequestError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    connection.send_result(msg["id"], details)


ISSUE_ENTRY_UNLOADED = "entry_unloaded"
ISSUE_SITE_UNAVAILABLE = "site_unavailable"

# hass.data key: entry_id -> callbacks to run when that entry unloads.
_UNLOAD_WATCHERS: HassKey[dict[str, set[Callable[[], None]]]] = HassKey(
    f"{DOMAIN}_topology_unload_watchers"
)


@callback
def _async_watch_unload(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    watcher: Callable[[], None],
) -> Callable[[], None]:
    """
    Call watcher when the entry unloads and return a callback that cancels it.

    ConfigEntry.async_on_unload has no remover, so registering one hook per
    subscription would leave a dead closure on the entry for every
    subscribe/unsubscribe cycle. Instead each loaded entry gets a single
    hook that drains a watcher set; subscriptions add and discard themselves.
    Unloading pops the set, so a reloaded entry gets a fresh hook.
    """
    watchers = hass.data.setdefault(_UNLOAD_WATCHERS, {})
    entry_watchers = watchers.get(entry.entry_id)
    if entry_watchers is None:
        entry_watchers = watchers[entry.entry_id] = set()

        @callback
        def _async_notify() -> None:
            for pending in list(watchers.pop(entry.entry_id, ())):
                pending()

        entry.async_on_unload(_async_notify)
    entry_watchers.add(watcher)
    return partial(entry_watchers.discard, watcher)


@websocket_api.websocket_command(
    {vol.Required("type"): "unifi_insights/topology/subscribe", **_SNAPSHOT_SCHEMA}
)
@callback
def ws_topology_subscribe(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """
    Stream one site's snapshot, pushing only when its revision changes.

    The listener rides on the facade coordinator, which never polls on its
    own, so a subscription adds no API traffic. It is removed when the client
    unsubscribes, when the connection closes, or when the entry unloads -
    whichever comes first, and only once (Home Assistant's remove-listener
    callback raises if called twice). The paths cannot overlap: unload pops
    the subscription from connection.subscriptions before the connection
    could call it, and unsubscribing stops watching the entry, so the unload
    hook never reaches it afterwards. The ``removed`` flag is belt and
    braces on top of that. Unload is observed via one shared hook per entry
    (_async_watch_unload), so repeated subscriptions leave nothing behind on
    the entry.
    """
    msg_id: int = msg["id"]
    site_id: str = msg["site_id"]
    max_clients: int = msg["max_clients"]
    try:
        entry = _resolve_entry(hass, msg["entry_id"])
        snapshot = _build_snapshot(hass, entry, site_id, max_clients)
    except _RequestError as err:
        connection.send_error(msg_id, err.code, str(err))
        return

    last_revision = snapshot["revision"]
    last_inputs = _snapshot_inputs(hass, entry, site_id)
    site_name = snapshot["site_name"]
    removed = False

    @callback
    def _async_forward() -> None:
        nonlocal last_inputs, last_revision, site_name
        # The facade notifies on every sub-coordinator update, including each
        # Protect WebSocket message, so skip the rebuild (registry lookups,
        # JSON and a hash) unless something the snapshot reads has changed.
        inputs = _snapshot_inputs(hass, entry, site_id)
        if _same_inputs(inputs, last_inputs):
            return
        last_inputs = inputs
        try:
            update = _build_snapshot(hass, entry, site_id, max_clients)
        except _RequestError:
            # The site left the selection without an entry reload: it was
            # deleted on the console, or the Network API went away and the
            # config coordinator dropped its sites. Report that once (the
            # revision dedupes repeats) and keep listening, so the stream
            # recovers on its own when the site comes back. Deselecting the
            # site in the options reloads the entry instead, which ends the
            # stream through _async_entry_unloaded.
            update = build_unavailable_topology(
                entry.entry_id, site_id, site_name, ISSUE_SITE_UNAVAILABLE
            )
        except Exception:
            # Home Assistant already isolates listener failures, but its log
            # line names neither the feature nor the site. Log a clear
            # per-site error instead; last_revision is untouched, so the
            # next good build is compared against what the card last got.
            _LOGGER.exception("Failed to rebuild the topology snapshot for %s", site_id)
            return
        if update["revision"] == last_revision:
            return
        last_revision = update["revision"]
        site_name = update["site_name"]
        connection.send_message(websocket_api.event_message(msg_id, update))

    remove_listener = entry.runtime_data.coordinator.async_add_listener(_async_forward)

    @callback
    def _async_unsubscribe() -> None:
        nonlocal removed
        if removed:
            return
        removed = True
        remove_listener()
        stop_watching()

    @callback
    def _async_entry_unloaded() -> None:
        if removed:
            return
        _async_unsubscribe()
        connection.subscriptions.pop(msg_id, None)
        connection.send_message(
            websocket_api.event_message(
                msg_id,
                build_unavailable_topology(
                    entry.entry_id, site_id, site_name, ISSUE_ENTRY_UNLOADED
                ),
            )
        )

    stop_watching = _async_watch_unload(hass, entry, _async_entry_unloaded)
    connection.subscriptions[msg_id] = _async_unsubscribe
    connection.send_result(msg_id)
    connection.send_message(websocket_api.event_message(msg_id, snapshot))


def _site_message_schema(message_type: str) -> dict[str | vol.Marker, Any]:
    """Return common entry/site schema for site-scoped websocket commands."""
    return {
        vol.Required("type"): message_type,
        vol.Required("entry_id"): str,
        vol.Required("site_id"): str,
    }


@websocket_api.websocket_command(_site_message_schema("unifi_insights/site_health/get"))
@callback
def ws_site_health_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return one site's health snapshot."""
    try:
        entry = _resolve_entry(hass, msg["entry_id"])
        snapshot = _generic_site_payload(
            hass, entry, msg["site_id"], "site_health", options=None
        )
    except _RequestError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    connection.send_result(msg["id"], snapshot)


@websocket_api.websocket_command(
    _site_message_schema("unifi_insights/site_health/subscribe")
)
@callback
def ws_site_health_subscribe(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Stream one site's health snapshot."""
    _subscribe_site_payload(hass, connection, msg, payload_type="site_health")


@websocket_api.websocket_command(
    _site_message_schema("unifi_insights/internet_activity/get")
)
@callback
def ws_internet_activity_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return one site's internet activity snapshot."""
    try:
        entry = _resolve_entry(hass, msg["entry_id"])
        snapshot = _generic_site_payload(
            hass, entry, msg["site_id"], "internet_activity", options=None
        )
    except _RequestError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    connection.send_result(msg["id"], snapshot)


@websocket_api.websocket_command(
    _site_message_schema("unifi_insights/internet_activity/subscribe")
)
@callback
def ws_internet_activity_subscribe(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Stream one site's internet activity snapshot."""
    _subscribe_site_payload(hass, connection, msg, payload_type="internet_activity")


@websocket_api.websocket_command(_site_message_schema("unifi_insights/performance/get"))
@callback
def ws_performance_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return one site's performance snapshot."""
    try:
        entry = _resolve_entry(hass, msg["entry_id"])
        snapshot = _generic_site_payload(
            hass, entry, msg["site_id"], "performance", options=None
        )
    except _RequestError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    connection.send_result(msg["id"], snapshot)


@websocket_api.websocket_command(
    _site_message_schema("unifi_insights/performance/subscribe")
)
@callback
def ws_performance_subscribe(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Stream one site's performance snapshot."""
    _subscribe_site_payload(hass, connection, msg, payload_type="performance")


_TIMELINE_OPTIONS_SCHEMA: dict[str | vol.Marker, Any] = {
    vol.Optional("max_items", default=10): vol.All(
        vol.Coerce(int), vol.Range(min=1, max=50)
    ),
    vol.Optional("hours", default=6): vol.All(
        vol.Coerce(int), vol.Range(min=1, max=TIMELINE_MAX_HOURS)
    ),
    vol.Optional("categories", default=["security"]): [str],
}

_TIMELINE_SCHEMA = {
    **_site_message_schema("unifi_insights/timeline/get"),
    **_TIMELINE_OPTIONS_SCHEMA,
}


@websocket_api.websocket_command(_TIMELINE_SCHEMA)
@callback
def ws_timeline_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return one site's timeline snapshot."""
    try:
        entry = _resolve_entry(hass, msg["entry_id"])
        snapshot = _generic_site_payload(
            hass,
            entry,
            msg["site_id"],
            "timeline",
            options={
                "max_items": msg["max_items"],
                "hours": msg["hours"],
                "categories": msg["categories"],
            },
        )
    except _RequestError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    connection.send_result(msg["id"], snapshot)


@websocket_api.websocket_command(
    {
        **_site_message_schema("unifi_insights/timeline/subscribe"),
        **_TIMELINE_OPTIONS_SCHEMA,
    }
)
@callback
def ws_timeline_subscribe(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Stream one site's timeline snapshot."""
    _subscribe_site_payload(
        hass,
        connection,
        msg,
        payload_type="timeline",
        options={
            "max_items": msg["max_items"],
            "hours": msg["hours"],
            "categories": msg["categories"],
        },
    )


@websocket_api.websocket_command(
    {vol.Required("type"): "unifi_insights/protect/sources"}
)
@callback
def ws_protect_sources(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List loaded entries that can provide Protect snapshots."""
    from . import CarrierFabricData  # noqa: PLC0415

    sources = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if runtime is None or isinstance(runtime, CarrierFabricData):
            continue
        data = runtime.coordinator.data
        protect = as_dict(data.get("protect"))
        cameras = as_dict(protect.get("cameras"))
        sites = [
            {"id": site_id, "name": site_display_name(data, site_id)}
            for site_id in runtime.config_coordinator.get_site_ids()
        ]
        sources.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "camera_count": len(cameras),
                "sites": sites,
            }
        )
    connection.send_result(msg["id"], sources)


@websocket_api.websocket_command(
    {vol.Required("type"): "unifi_insights/protect/get", vol.Required("entry_id"): str}
)
@callback
def ws_protect_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return one entry's protect status snapshot."""
    try:
        entry = _resolve_entry(hass, msg["entry_id"])
        snapshot = _protect_payload(hass, entry)
    except _RequestError as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    connection.send_result(msg["id"], snapshot)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "unifi_insights/protect/subscribe",
        vol.Required("entry_id"): str,
    }
)
@callback
def ws_protect_subscribe(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Stream one entry's protect status snapshot."""
    msg_id = msg["id"]
    try:
        entry = _resolve_entry(hass, msg["entry_id"])
        snapshot = _protect_payload(hass, entry)
    except _RequestError as err:
        connection.send_error(msg_id, err.code, str(err))
        return

    last_revision = snapshot.get("revision", "")
    last_inputs = _runtime_protect_inputs(hass, entry)
    removed = False

    @callback
    def _async_forward() -> None:
        nonlocal last_inputs, last_revision
        inputs = _runtime_protect_inputs(hass, entry)
        if _same_inputs(inputs, last_inputs):
            return
        last_inputs = inputs
        try:
            update = _protect_payload(hass, entry)
        except Exception:
            _LOGGER.exception("Failed to rebuild protect snapshot")
            return
        if update.get("revision", "") == last_revision:
            return
        last_revision = update.get("revision", "")
        connection.send_message(websocket_api.event_message(msg_id, update))

    remove_listener = entry.runtime_data.coordinator.async_add_listener(_async_forward)

    @callback
    def _async_unsubscribe() -> None:
        nonlocal removed
        if removed:
            return
        removed = True
        remove_listener()
        stop_watching()

    @callback
    def _async_entry_unloaded() -> None:
        if removed:
            return
        _async_unsubscribe()
        connection.subscriptions.pop(msg_id, None)
        connection.send_message(
            websocket_api.event_message(
                msg_id,
                build_unavailable_protect_snapshot(
                    entry.entry_id, entry.title, ISSUE_ENTRY_UNLOADED
                ),
            )
        )

    stop_watching = _async_watch_unload(hass, entry, _async_entry_unloaded)
    connection.subscriptions[msg_id] = _async_unsubscribe
    connection.send_result(msg_id)
    connection.send_message(websocket_api.event_message(msg_id, snapshot))


def _subscribe_site_payload(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    *,
    payload_type: str,
    options: dict[str, Any] | None = None,
) -> None:
    """Subscribe to a site-scoped payload stream with revision dedupe."""
    msg_id: int = msg["id"]
    site_id: str = msg["site_id"]
    try:
        entry = _resolve_entry(hass, msg["entry_id"])
        snapshot = _generic_site_payload(
            hass, entry, site_id, payload_type, options=options
        )
    except _RequestError as err:
        connection.send_error(msg_id, err.code, str(err))
        return

    last_revision = str(snapshot.get("revision", ""))
    last_inputs = _runtime_snapshot_inputs(hass, entry, site_id, payload_type)
    removed = False

    @callback
    def _async_forward() -> None:
        nonlocal last_inputs, last_revision
        inputs = _runtime_snapshot_inputs(hass, entry, site_id, payload_type)
        if _same_inputs(inputs, last_inputs):
            return
        last_inputs = inputs
        try:
            update = _generic_site_payload(
                hass, entry, site_id, payload_type, options=options
            )
        except _RequestError:
            update = _generic_site_unavailable(
                entry, site_id, payload_type, ISSUE_SITE_UNAVAILABLE
            )
        except Exception:
            _LOGGER.exception("Failed to rebuild %s snapshot", payload_type)
            return
        revision = str(update.get("revision", ""))
        if revision == last_revision:
            return
        last_revision = revision
        connection.send_message(websocket_api.event_message(msg_id, update))

    remove_listener = entry.runtime_data.coordinator.async_add_listener(_async_forward)

    @callback
    def _async_unsubscribe() -> None:
        nonlocal removed
        if removed:
            return
        removed = True
        remove_listener()
        stop_watching()

    @callback
    def _async_entry_unloaded() -> None:
        if removed:
            return
        _async_unsubscribe()
        connection.subscriptions.pop(msg_id, None)
        connection.send_message(
            websocket_api.event_message(
                msg_id,
                _generic_site_unavailable(
                    entry, site_id, payload_type, ISSUE_ENTRY_UNLOADED
                ),
            )
        )

    stop_watching = _async_watch_unload(hass, entry, _async_entry_unloaded)
    connection.subscriptions[msg_id] = _async_unsubscribe
    connection.send_result(msg_id)
    connection.send_message(websocket_api.event_message(msg_id, snapshot))
