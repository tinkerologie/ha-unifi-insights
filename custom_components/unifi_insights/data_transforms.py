# Copyright 2026 UniFi Insights contributors
"""
Data transformation functions for `unifi-official-api` responses.

This module provides transformation functions to convert `unifi-official-api`
response formats to the internal data structures expected by entities,
maintaining backward compatibility.

It also holds the network device read model (#337): the shared field-alias,
number and MAC primitives, and the rules that decide each network device's
online state, kind, site gateway, ports, canonical metrics and a client's
connection type. The device coordinator builds the facts once per successful
update and publishes them under ``network_facts``; ``site_network_facts`` and
``device_facts`` are the one way to read them.

Ports need legacy data. The device coordinator only fetches the v1 device
overview, whose ``interfaces`` is a list of category names, never the v1
device details that carry ``interfaces.ports``. Port records therefore come
from the legacy ``/stat/device`` merge, and when that fetch fails a device's
``ports`` are empty.

Like ``topology_contract``, this module does no I/O and imports nothing from
Home Assistant, so the dashboard snapshot builders can use it.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, Literal

from homeassistant.util import dt as dt_util

from .const import (
    CAMERA_TYPE_DOORBELL,
    CAMERA_TYPE_DOORBELL_MAIN,
    CAMERA_TYPE_DOORBELL_PACKAGE,
    CAMERA_TYPE_DOORBELL_WITH_PACKAGE_DETECTION,
    DEVICE_TYPE_ALARM_HUB,
    DEVICE_TYPE_LINK_STATION,
    GATEWAY_MODEL_PREFIXES,
    STORAGE_NEARLY_FULL_PERCENT,
)
from .innerspace_transforms import (
    _normalize_innerspace_mac,
    build_innerspace_view,
    correlate_innerspace_devices,
    innerspace_correlation_key,
    innerspace_view_inputs,
    normalize_innerspace_snapshot,
    parse_floor_plan_asset_path,
    transform_innerspace_device,
    transform_innerspace_floor_plan,
    transform_innerspace_project,
)

_LOGGER = logging.getLogger(__name__)


# --- Shared primitives -------------------------------------------------------


def get_field(data: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    """
    Get a field from data using multiple possible key names.

    Handles both camelCase and snake_case field names from different API versions.
    Skips keys whose value is None so that fallback keys are still checked.
    """
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def first_not_none(*values: Any, default: Any = None) -> Any:
    """
    Return the first value that is not None.

    This is the value-level companion to `get_field`'s key-level None-skip:
    `get_field` already skips a candidate *key* whose value is None, but
    callers that then chain the resulting *values* together with `or` are
    falsy-based, not None-based, and `or` silently discards a legitimate
    `0` (or `False`/`""`) in favour of the next candidate. Use this helper
    wherever multiple candidate values for a numeric measurement or counter
    (rate, power, bytes, temperature, percentage, count) are combined, so a
    real `0` reading is preserved instead of being coerced into `None`.
    """
    for value in values:
        if value is not None:
            return value
    return default


def _finite_number(value: Any) -> float | None:
    """
    Return ``value`` as a finite float, or None when it is not a number.

    Booleans are rejected although Python treats them as ints: a flag is never
    a measurement. Numeric strings are accepted, as the dashboard converter
    always has; NaN and infinity are not.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):  # fmt: skip
        return None
    return number if math.isfinite(number) else None


def as_bits_per_second(value: Any) -> float | None:
    """
    Read a rate that is already in bits/s, keeping a real 0.

    None, booleans, non-numeric values, NaN and infinity read as None.
    """
    return _finite_number(value)


def bytes_to_bits_per_second(value: Any) -> float | None:
    """
    Convert a bytes/s rate to bits/s (x8), keeping a real 0.

    None, booleans, non-numeric values, NaN and infinity read as None.
    """
    number = _finite_number(value)
    if number is None:
        return None
    bits = number * 8
    return bits if math.isfinite(bits) else None


_MAC_RE: Final = re.compile(r"[0-9a-f]{2}(?:[:-]?[0-9a-f]{2}){5}")


def normalize_mac(value: Any) -> str | None:
    """
    Return ``value`` as a lower-case colon MAC, or None if it is not one.

    This is the canonical MAC rule of the read model, and the input of the
    topology node-id HMAC. Four identity-specific policies deliberately stay
    separate because they feed stored or user-facing identities:
    ``console_identity.normalize_mac`` (stored console ids),
    ``UnifiInsightsDeviceCoordinator._normalize_mac`` (exact legacy key matching),
    ``innerspace_transforms._normalize_innerspace_mac`` (dotted InnerSpace
    MACs) and ``services._mac_key`` (MACs typed into action calls).
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    if not _MAC_RE.fullmatch(candidate):
        return None
    digits = candidate.replace(":", "").replace("-", "")
    return ":".join(digits[index : index + 2] for index in range(0, 12, 2))


def _enum_value(value: Any) -> Any:
    """Unwrap an enum member to its value; pass anything else through."""
    return value.value if isinstance(value, Enum) else value


# --- Network device read model -----------------------------------------------

NETWORK_FACTS_KEY: Final = "network_facts"

OnlineState = Literal["online", "offline", "unknown"]
NetworkDeviceKind = Literal["gateway", "switch", "access_point", "other"]
ClientConnectionType = Literal["wired", "wireless"]

# Every network device ``state`` the Network API spec defines, with what it
# means for "is this device online". This tuple is the source of truth; a
# test checks that every library ``DeviceState`` member resolves through it.
# ``CONNECTED``, ``UP``, ``DISCONNECTED`` and ``DOWN`` are not network device
# states (they belong to Protect devices and ports), so like an absent or
# unrecognised value they read as unknown. The library's ``DeviceState`` also
# carries lowercase non-spec values (``connected``, ``disconnected``, ...);
# they stay unknown on purpose until a console is seen sending them (#337
# design review, 2026-10-19: live consoles report ``ONLINE``).
NETWORK_DEVICE_STATES: Final[tuple[tuple[str, OnlineState], ...]] = (
    ("ONLINE", "online"),
    ("OFFLINE", "offline"),
    ("CONNECTION_INTERRUPTED", "offline"),
    ("ISOLATED", "offline"),
    ("U5G_INCORRECT_TOPOLOGY", "offline"),
    ("PENDING_ADOPTION", "unknown"),
    ("UPDATING", "unknown"),
    ("GETTING_READY", "unknown"),
    ("ADOPTING", "unknown"),
    ("DELETING", "unknown"),
)
_ONLINE_STATE_BY_DEVICE_STATE: Final[dict[str, OnlineState]] = dict(
    NETWORK_DEVICE_STATES
)

# Only GETTING_READY is a reachable managed device applying configuration.
# UPDATING may reboot; ADOPTING, PENDING_ADOPTION and DELETING are not managed.
TRANSIENT_SERVICEABLE_STATES: Final = frozenset({"GETTING_READY"})

_GATEWAY_LEGACY_TYPES: Final = frozenset({"udm", "uxg", "ugw", "udr", "ucg"})

# Stats keys for the system metrics. The v1 statistics endpoint uses the
# camelCase names; the others are the legacy merge's and older aliases.
CPU_STAT_KEYS: Final = (
    "cpuUtilizationPct",
    "cpu_utilization_pct",
    "cpu_percent",
    "cpu",
)
MEMORY_STAT_KEYS: Final = (
    "memoryUtilizationPct",
    "memory_utilization_pct",
    "memory_percent",
    "memory",
)
UPTIME_STAT_KEYS: Final = ("uptimeSec", "uptime_sec", "uptime_seconds", "uptime")

# Uplink rate keys per direction, each with whether the value is bytes/s.
_TX_RATE_KEYS: Final = (
    ("txRateBps", False),
    ("tx_rate_bps", False),
    ("tx_bytes_per_sec", True),
)
_RX_RATE_KEYS: Final = (
    ("rxRateBps", False),
    ("rx_rate_bps", False),
    ("rx_bytes_per_sec", True),
)


def device_online_state(device: Mapping[str, Any]) -> OnlineState:
    """
    Return whether a network device is online, offline or unknown.

    Reads ``state``, then ``status``, unwraps enums and ignores case and
    surrounding whitespace, then maps the value through
    ``NETWORK_DEVICE_STATES``. Anything not listed there reads as unknown.
    """
    return _ONLINE_STATE_BY_DEVICE_STATE.get(
        _normalized_device_state(device), "unknown"
    )


def _normalized_device_state(device: Mapping[str, Any]) -> str:
    """Normalize state, falling back to status only when state is None."""
    raw = _enum_value(get_field(device, "state", "status"))
    return raw.strip().upper() if isinstance(raw, str) else ""


def device_serviceable(device: Mapping[str, Any]) -> bool:
    """Return service eligibility, including configuration in GETTING_READY."""
    return (
        device_online_state(device) == "online"
        or _normalized_device_state(device) in TRANSIENT_SERVICEABLE_STATES
    )


def _has_feature(features: Any, *names: str) -> bool:
    """
    Return True when ``features`` advertises any of ``names``.

    The device list returns ``features`` as a list of names; the device
    details return an object of name to detail, which arrives here as a
    mapping. A detail can be an empty object (the spec's access point
    overview is ``{}``), so a feature counts when its key holds anything but
    None or False.
    """
    if isinstance(features, Mapping):
        return any(features.get(name) not in (None, False) for name in names)
    if isinstance(features, list | tuple):
        return any(name in features for name in names)
    return False


def _lower_text(value: Any) -> str | None:
    """Return an enum-unwrapped string in lower case, or None."""
    value = _enum_value(value)
    return value.lower() if isinstance(value, str) else None


def device_kind(device: Mapping[str, Any]) -> NetworkDeviceKind:
    """
    Classify a network device as gateway, switch, access point or other.

    Every gateway check runs before the access point and switching checks,
    because UDM-class gateways also advertise switching. A device is a
    gateway when its legacy type is a gateway type, its v1 ``type`` contains
    "gateway", its model starts with a gateway prefix or contains "GATEWAY",
    or it advertises a ``gateway`` or ``router`` feature. The Network API has
    no v1 ``type`` (it arrives as None) and a UDM Pro advertises only
    ``["switching"]``, so without legacy data the model is what identifies
    it. The spec's device details feature object has no ``gateway`` key;
    accepting one there only supports such an extension.
    """
    topology = device.get("topology")
    legacy_type = (
        _lower_text(topology.get("legacy_type"))
        if isinstance(topology, Mapping)
        else None
    )
    v1_type = _lower_text(device.get("type"))
    model = _enum_value(device.get("model"))
    model_upper = model.upper() if isinstance(model, str) else ""
    features = device.get("features")

    if (
        legacy_type in _GATEWAY_LEGACY_TYPES
        or (v1_type is not None and "gateway" in v1_type)
        or model_upper.startswith(GATEWAY_MODEL_PREFIXES)
        or "GATEWAY" in model_upper
        or _has_feature(features, "gateway", "router")
    ):
        return "gateway"
    if legacy_type == "usw":
        return "switch"
    if legacy_type == "uap":
        return "access_point"
    if v1_type is not None:
        if "switch" in v1_type:
            return "switch"
        if "access" in v1_type or v1_type in {"ap", "uap"}:
            return "access_point"
    if _has_feature(features, "accessPoint"):
        return "access_point"
    if _has_feature(features, "switching"):
        return "switch"
    return "other"


def port_index(raw_index: Any) -> int | None:
    """
    Normalise a raw port index to an int, or None.

    An int (not a bool) is kept as is, including 0; a numeric string goes
    through ``int()``, so ``"03"`` and ``" 3 "`` become 3. No zero- or
    one-based shift is applied.
    """
    if isinstance(raw_index, bool):
        return None
    if isinstance(raw_index, int):
        return raw_index
    if isinstance(raw_index, str):
        try:
            return int(raw_index)
        except ValueError:
            return None
    return None


def _port_records(device: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """
    Return a device's port records in source order.

    The non-empty top-level ``ports`` list (the legacy merge) wins; otherwise
    a mapping-shaped ``interfaces`` supplies ``interfaces.ports``. The two
    sources are never merged. A list-shaped ``interfaces`` (the v1 overview's
    category names) has no ports.
    """
    ports = device.get("ports")
    if not isinstance(ports, list) or not ports:
        interfaces = device.get("interfaces")
        ports = interfaces.get("ports") if isinstance(interfaces, Mapping) else None
    if not isinstance(ports, list):
        return []
    return [port for port in ports if isinstance(port, Mapping)]


@dataclass(frozen=True, kw_only=True, slots=True)
class NetworkPortFacts:
    """
    One port of a network device.

    ``raw_index`` is the index exactly as the record spelled it (it feeds
    unique IDs that must not change); ``index`` is its normalised int for
    lookups. ``record`` is a read-only copy of the port record.
    """

    raw_index: Any
    index: int | None
    record: Mapping[str, Any]
    is_uplink: bool


def _port_facts(port: Mapping[str, Any]) -> NetworkPortFacts:
    """Build the facts for one port record."""
    raw_index = get_field(port, "idx", "port_idx", "portIdx", "index")
    return NetworkPortFacts(
        raw_index=raw_index,
        index=port_index(raw_index),
        record=MappingProxyType(dict(port)),
        is_uplink=get_field(port, "is_uplink", "isUplink") is True,
    )


def device_ports(device: Mapping[str, Any]) -> tuple[NetworkPortFacts, ...]:
    """
    Return the port facts of one raw device record, in source order.

    This is the one port source: the published facts use it, and so do the
    pure helpers that only hold a raw device record (topology port numbers).
    """
    return tuple(_port_facts(port) for port in _port_records(device))


@dataclass(frozen=True, kw_only=True, slots=True)
class NetworkDeviceFacts:
    """
    What the integration has decided about one network device.

    ``online_state`` is strict connectivity; ``serviceable`` also permits
    GETTING_READY while a managed device applies configuration. Coordinator
    health must still gate entity availability.

    ``ports`` keeps source order; when two records share an index the first
    one wins both lookups. Ports need legacy data: the v1 device overview
    carries no port records, so ``ports`` is empty when the legacy fetch
    fails.

    ``tx_bps`` and ``rx_bps`` are the device's uplink throughput in bits/s.
    The public ``uplink.txRateBps``/``rxRateBps`` (and snake-case
    ``tx_rate_bps``/``rx_rate_bps``) are already bits/s and are not
    converted; only the explicit ``tx_bytes_per_sec``/``rx_bytes_per_sec``
    fallbacks are multiplied by 8. The spec leaves the unit undefined. The
    #337 evidence: four same-moment rounds on a UDM Pro SE and a USW Pro Max
    24 (Network 11.0.89) read the v1 rates at 8x (7.994-8.000) the legacy
    uplink bytes/s, and an earlier, not simultaneous, UCG-Fiber sample
    (Network 10.6.106) agreed. A third console (UDM Pro, Network 10.6.106,
    2026-10-19) read 8.00x across seven devices and four same-moment rounds
    against the legacy ``stat/device`` uplink ``tx_bytes-r``/``rx_bytes-r``,
    the bytes/s figures the UniFi UI shows rates from. No UI screenshot
    was taken.
    """

    device_id: str
    mac: str | None
    online_state: OnlineState
    serviceable: bool
    kind: NetworkDeviceKind
    is_site_gateway: bool
    ports: tuple[NetworkPortFacts, ...]
    cpu_pct: float | None
    memory_pct: float | None
    uptime_s: int | None
    tx_bps: float | None
    rx_bps: float | None

    def port_by_index(self, index: int) -> NetworkPortFacts | None:
        """Return the first port whose normalised index is ``index``."""
        for port in self.ports:
            if port.index is not None and port.index == index:
                return port
        return None

    def port_by_raw_index(self, raw_index: Any) -> NetworkPortFacts | None:
        """Return the first port whose raw index has this exact spelling."""
        for port in self.ports:
            if type(port.raw_index) is type(raw_index) and (
                port.raw_index == raw_index
            ):
                return port
        return None


@dataclass(frozen=True, kw_only=True, slots=True)
class SiteNetworkFacts:
    """The facts of every network device of one site, and its gateway."""

    gateway_id: str | None
    devices: Mapping[str, NetworkDeviceFacts]


def _metric(
    stats: Mapping[str, Any], device: Mapping[str, Any], keys: tuple[str, ...]
) -> float | None:
    """Read a metric from the stats record, then the device record."""
    return _finite_number(
        first_not_none(get_field(stats, *keys), get_field(device, *keys))
    )


def uplink_rate_bps(
    stats: Mapping[str, Any], direction: Literal["tx", "rx"]
) -> float | None:
    """
    Return a device's uplink rate in bits/s, keeping a real 0.

    The ``uplink`` container is read before the top level of the stats
    record. In each, the bits/s keys come before the bytes/s fallback, and
    the first key that is present decides: a malformed value reads as None
    rather than falling through to another key. See ``NetworkDeviceFacts``
    for the unit evidence.
    """
    keys = _TX_RATE_KEYS if direction == "tx" else _RX_RATE_KEYS
    uplink = get_field(stats, "uplink", "uplink_stats")
    containers = (uplink, stats) if isinstance(uplink, Mapping) else (stats,)
    for container in containers:
        for key, is_bytes in keys:
            value = container.get(key)
            if value is None:
                continue
            if is_bytes:
                return bytes_to_bits_per_second(value)
            return as_bits_per_second(value)
    return None


def client_connection_type(client: Mapping[str, Any]) -> ClientConnectionType | None:
    """
    Return whether a client is wired or wireless, or None when unknown.

    Reads ``type``, then ``connection_type``, unwraps enums, strips
    whitespace and a ``CLIENTTYPE.`` prefix, and then requires an exact
    ``WIRED`` or ``WIRELESS`` (in any case).
    """
    raw = _enum_value(get_field(client, "type", "connection_type"))
    if not isinstance(raw, str):
        return None
    value = raw.strip().upper().removeprefix("CLIENTTYPE.")
    if value == "WIRED":
        return "wired"
    if value == "WIRELESS":
        return "wireless"
    return None


_CLIENT_CONNECTED_SENTINEL = object()


def client_is_connected(client: Mapping[str, Any] | None) -> bool:
    """
    Return True if a client record represents a connected client.

    Official UniFi Network API endpoints return connected clients only and omit
    the connected field entirely. A present client record is therefore
    considered connected by default (membership in the snapshot). An explicit
    non-None connected field overrides this membership rule with its own
    truthiness (for legacy or offline records with connected=False).
    An empty or missing client record returns False.
    """
    if not client:
        return False
    raw = get_field(client, "connected", default=_CLIENT_CONNECTED_SENTINEL)
    if raw is _CLIENT_CONNECTED_SENTINEL:
        return True
    return bool(raw)


def _device_facts(
    device_id: str,
    device: Mapping[str, Any],
    stats: Mapping[str, Any],
    *,
    kind: NetworkDeviceKind,
    is_site_gateway: bool,
) -> NetworkDeviceFacts:
    """Build the facts for one device."""
    uptime = _metric(stats, device, UPTIME_STAT_KEYS)
    return NetworkDeviceFacts(
        device_id=device_id,
        mac=normalize_mac(get_field(device, "macAddress", "mac")),
        online_state=device_online_state(device),
        serviceable=device_serviceable(device),
        kind=kind,
        is_site_gateway=is_site_gateway,
        ports=device_ports(device),
        cpu_pct=_metric(stats, device, CPU_STAT_KEYS),
        memory_pct=_metric(stats, device, MEMORY_STAT_KEYS),
        uptime_s=int(uptime) if uptime is not None else None,
        tx_bps=uplink_rate_bps(stats, "tx"),
        rx_bps=uplink_rate_bps(stats, "rx"),
    )


def build_site_network_facts(devices: Any, stats: Any) -> SiteNetworkFacts:
    """
    Build one site's facts from its ``devices`` and ``stats`` maps.

    The site gateway is the first device, in map order, whose kind is
    gateway. The inputs are not changed; the result holds read-only copies.
    """
    device_map = devices if isinstance(devices, Mapping) else {}
    stats_map = stats if isinstance(stats, Mapping) else {}
    records = [
        (str(device_id), device_id, device)
        for device_id, device in device_map.items()
        if isinstance(device, Mapping)
    ]
    kinds: dict[str, NetworkDeviceKind] = {
        key: device_kind(device) for key, _, device in records
    }
    gateway_id = next((key for key in kinds if kinds[key] == "gateway"), None)
    facts: dict[str, NetworkDeviceFacts] = {}
    for key, device_id, device in records:
        device_stats = stats_map.get(device_id)
        facts[key] = _device_facts(
            key,
            device,
            device_stats if isinstance(device_stats, Mapping) else {},
            kind=kinds[key],
            is_site_gateway=key == gateway_id,
        )
    return SiteNetworkFacts(gateway_id=gateway_id, devices=MappingProxyType(facts))


def site_network_facts(
    data: Mapping[str, Any], site_id: str
) -> SiteNetworkFacts | None:
    """
    Return one site's network facts from coordinator data.

    Returns the published facts when ``data`` carries the ``network_facts``
    key (None for a site it does not hold). Without the key, builds them from
    the raw ``devices`` and ``stats`` with the same builder, so data that was
    never published (pure builders, test data) follows the same rules.
    """
    published = data.get(NETWORK_FACTS_KEY)
    if isinstance(published, Mapping):
        site_facts = published.get(site_id)
        return site_facts if isinstance(site_facts, SiteNetworkFacts) else None
    devices = data.get("devices")
    site_devices = devices.get(site_id) if isinstance(devices, Mapping) else None
    if not isinstance(site_devices, Mapping):
        return None
    stats = data.get("stats")
    _LOGGER.debug("Building unpublished network facts for site %s", site_id)
    return build_site_network_facts(
        site_devices, stats.get(site_id) if isinstance(stats, Mapping) else None
    )


def device_facts(
    data: Mapping[str, Any], site_id: str, device_id: str
) -> NetworkDeviceFacts | None:
    """Return one device's network facts from coordinator data, or None."""
    site_facts = site_network_facts(data, site_id)
    if site_facts is None:
        return None
    return site_facts.devices.get(device_id)


def site_device_facts(
    site_facts: SiteNetworkFacts | None,
    device_id: str,
    device: Mapping[str, Any],
    stats: Any = None,
) -> NetworkDeviceFacts:
    """
    Return one device's facts from a site's published facts.

    The snapshot builders iterate the raw ``devices`` map for names and
    models; this gives them the published facts of each device. A device the
    facts do not hold is built from its raw records with the same rules (it
    is never the site gateway then).
    """
    facts = site_facts.devices.get(device_id) if site_facts is not None else None
    if facts is not None:
        return facts
    return _device_facts(
        device_id,
        device,
        stats if isinstance(stats, Mapping) else {},
        kind=device_kind(device),
        is_site_gateway=False,
    )


def get_site_gateway_id(data: Mapping[str, Any], site_id: str) -> str | None:
    """
    Return the ID of a site's gateway device from coordinator data, or None.

    The facade's ``get_site_gateway_id`` is this function on its own data;
    code that holds only coordinator data (entity setup, tests with mock
    coordinators) calls it directly, so the fact has one name.
    """
    site_facts = site_network_facts(data, site_id)
    return site_facts.gateway_id if site_facts is not None else None


# --- Facade snapshot lookups (ADR-013) ---------------------------------------
#
# Pure reads of one record from the facade snapshot (or any mapping shaped
# like it). Each returns None for a missing or malformed section or record
# and never raises, so readers need no guards of their own.


def _record(data: object, *path: str) -> Mapping[str, Any] | None:
    """Return the mapping at ``path`` under ``data``, or None."""
    value = data
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value if isinstance(value, Mapping) else None


def network_device(
    data: Mapping[str, Any], site_id: str, device_id: str
) -> Mapping[str, Any] | None:
    """Return one Network device's record, or None."""
    return _record(data, "devices", site_id, device_id)


def network_device_stats(
    data: Mapping[str, Any], site_id: str, device_id: str
) -> Mapping[str, Any] | None:
    """Return one Network device's statistics, or None."""
    return _record(data, "stats", site_id, device_id)


def network_client(
    data: Mapping[str, Any], site_id: str, client_id: str
) -> Mapping[str, Any] | None:
    """Return one Network client's record by its client ID, or None."""
    return _record(data, "clients", site_id, client_id)


def site_record(data: Mapping[str, Any], site_id: str) -> Mapping[str, Any] | None:
    """Return one site's record, or None."""
    return _record(data, "sites", site_id)


def rule_record(
    data: Mapping[str, Any], kind: str, site_id: str, rule_id: str
) -> Mapping[str, Any] | None:
    """
    Return one network rule's record, or None.

    ``kind`` is the rule section the record lives in, such as
    ``"firewall_rules"``, ``"port_forwards"`` or ``"vpn_clients"``.
    """
    return _record(data, kind, site_id, rule_id)


def protect_device(
    data: Mapping[str, Any], collection: str, device_id: str
) -> Mapping[str, Any] | None:
    """
    Return one Protect device's record, or None.

    ``collection`` is the Protect collection, such as ``"cameras"`` or
    ``"chimes"``.
    """
    return _record(data, "protect", collection, device_id)


def protect_collection(
    data: Mapping[str, Any], collection: str
) -> dict[str, dict[str, Any]] | None:
    """
    Return a Protect collection's records by device ID, or None.

    None means the section or collection is missing or malformed, so callers
    can tell "not loaded" from "empty". Only the records published as dicts
    are returned, as the platforms that iterate a collection expect; any other
    value is skipped.
    """
    records = _record(data, "protect", collection)
    if records is None:
        return None
    return {
        device_id: record
        for device_id, record in records.items()
        if isinstance(record, dict)
    }


def _dict_records(records: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return only the records published as dicts."""
    return {
        record_id: record
        for record_id, record in records.items()
        if isinstance(record, dict)
    }


def site_section(
    data: Mapping[str, Any], section: str, site_id: str
) -> dict[str, dict[str, Any]] | None:
    """
    Return one site's records of a site-keyed section by record ID, or None.

    ``section`` is a section keyed by site, then record ID, such as
    ``"devices"``, ``"clients"`` or ``"wifi"``. None means the section or the
    site's records are missing or malformed. Only the records published as
    dicts are returned; any other value is skipped.
    """
    records = _record(data, section, site_id)
    return None if records is None else _dict_records(records)


def section_by_site(
    data: Mapping[str, Any], section: str
) -> dict[str, dict[str, dict[str, Any]]] | None:
    """
    Return a site-keyed section's records by site ID, then record ID, or None.

    None means the section is missing or malformed. A site whose records are
    not a mapping is skipped, and so is any record not published as a dict.
    """
    by_site = _record(data, section)
    if by_site is None:
        return None
    return {
        site_id: _dict_records(records)
        for site_id, records in by_site.items()
        if isinstance(records, Mapping)
    }


def latest_voucher(data: Mapping[str, Any], site_id: str) -> Mapping[str, Any] | None:
    """Return the latest voucher generated for a site, or None."""
    return _record(data, "latest_vouchers", site_id)


# Reads of the availability captured in the snapshot (ADR-013). Entities read
# them on every state write, so they only read dicts and allocate nothing. A
# missing or malformed value reads as available, as the facade reports an
# owner it does not have.

type AvailabilityOwner = Literal["device", "config", "protect", "innerspace", "network"]


def _availability(data: object) -> Mapping[str, Any] | None:
    """Return the snapshot's availability section, or None."""
    value = data.get("availability") if isinstance(data, Mapping) else None
    return value if isinstance(value, Mapping) else None


def owner_available(data: Mapping[str, Any], owner: AvailabilityOwner) -> bool:
    """Return whether an owner coordinator was available for this snapshot."""
    availability = _availability(data)
    value = None if availability is None else availability.get(owner)
    return value if isinstance(value, bool) else True


def site_section_available(data: Mapping[str, Any], site_id: str, section: str) -> bool:
    """
    Return whether a site's config section was fetched for this snapshot.

    ``section`` is a per-site config section, such as ``"wifi"`` or
    ``"vouchers"``. A site or section the snapshot does not list has the
    config coordinator's availability.
    """
    availability = _availability(data)
    if availability is not None:
        sites = availability.get("sites")
        sections = sites.get(site_id) if isinstance(sites, Mapping) else None
        value = sections.get(section) if isinstance(sections, Mapping) else None
        if isinstance(value, bool):
            return value
    return owner_available(data, "config")


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


# --- Access point radios -----------------------------------------------------
#
# The Network API (11.0.97) reports each access point radio by frequency in two
# places: the latest statistics (``interfaces.radios[]`` with ``frequencyGHz``
# and ``txRetriesPct``) and the device details (``frequencyGHz``, ``channel``,
# ``channelWidthMHz``, ``txPowerMode`` and ``wlanStandard``). Radios are keyed
# by their normalised frequency so a radio's entities keep their identity, and
# the spec enums become Home Assistant translation state keys, which allow only
# ``[a-z0-9_-]`` (the spec's ``802.11ax`` contains a dot).

# Band slug -> display label, which fills the ``{band}`` name placeholder.
RADIO_BAND_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "900mhz": "900 MHz",
        "2_4ghz": "2.4 GHz",
        "5ghz": "5 GHz",
        "6ghz": "6 GHz",
        "60ghz": "60 GHz",
    }
)

# The spec's ``frequencyGHz`` enum, in GHz, to its band slug.
_RADIO_BAND_SLUGS: Final[Mapping[float, str]] = MappingProxyType(
    {
        0.9: "900mhz",
        2.4: "2_4ghz",
        5.0: "5ghz",
        6.0: "6ghz",
        60.0: "60ghz",
    }
)

# Every ``txPowerMode`` and ``wlanStandard`` state key, shared by the sensor
# descriptions and the translation tests so there is one source of truth.
RADIO_TX_POWER_MODE_OPTIONS: Final = (
    "auto",
    "low",
    "medium",
    "high",
    "custom",
    "disabled",
)
RADIO_WLAN_STANDARD_OPTIONS: Final = (
    "802_11a",
    "802_11b",
    "802_11g",
    "802_11n",
    "802_11ac",
    "802_11ax",
    "802_11be",
    "802_11ah",
)

# The spec value (upper case, as sent) -> state key.
_RADIO_TX_POWER_MODE_KEYS: Final[Mapping[str, str]] = MappingProxyType(
    {option.upper(): option for option in RADIO_TX_POWER_MODE_OPTIONS}
)
_RADIO_WLAN_STANDARD_KEYS: Final[Mapping[str, str]] = MappingProxyType(
    {
        f"802.11{suffix.upper()}": f"802_11{suffix}"
        for suffix in ("a", "b", "g", "n", "ac", "ax", "be", "ah")
    }
)


def _spec_token(value: Any) -> str:
    """Return an enum value as an upper-case spec token, or an empty string."""
    value = _enum_value(value)
    return value.strip().upper() if isinstance(value, str) else ""


def radio_band_slug(frequency: Any) -> str | None:
    """
    Return the band slug of a ``frequencyGHz`` value, or None when unknown.

    The spec types the field as a number, so ints, floats and numeric strings
    are accepted (``5``, ``5.0`` and ``"5"`` are the same band). Booleans,
    missing values, non-numeric values and frequencies outside the enum are
    not.
    """
    number = _finite_number(frequency)
    return None if number is None else _RADIO_BAND_SLUGS.get(number)


def radio_band_map(payload: Any) -> Mapping[str, Mapping[str, Any]]:
    """
    Return radios keyed by band slug, as read-only records.

    ``payload`` is either a device's ``interfaces`` mapping (the statistics and
    the details payloads both nest ``radios`` under it) or the ``radios`` list
    itself. Anything else, including the overview's list-shaped
    ``interfaces`` (``["ports", "radios"]``), has no radios and gives an empty
    mapping. A radio that is not a mapping, has no known ``frequencyGHz``, or
    repeats a band already seen is skipped with a debug log and never raises;
    the first radio of a band wins.
    """
    radios = payload.get("radios") if isinstance(payload, Mapping) else payload
    if not isinstance(radios, list):
        return MappingProxyType({})
    bands: dict[str, Mapping[str, Any]] = {}
    for radio in radios:
        if not isinstance(radio, Mapping):
            _LOGGER.debug("Skipping radio entry that is not a mapping: %r", radio)
            continue
        frequency = get_field(radio, "frequencyGHz", "frequency_ghz")
        band = radio_band_slug(frequency)
        if band is None:
            _LOGGER.debug("Skipping radio with unknown frequency: %r", frequency)
            continue
        if band in bands:
            _LOGGER.debug("Skipping duplicate %s radio", band)
            continue
        bands[band] = MappingProxyType(dict(radio))
    return MappingProxyType(bands)


def tx_power_mode_slug(value: Any) -> str | None:
    """Return the state key of a ``txPowerMode`` value, or None when unknown."""
    return _RADIO_TX_POWER_MODE_KEYS.get(_spec_token(value))


def wlan_standard_slug(value: Any) -> str | None:
    """Return the state key of a ``wlanStandard`` value, or None when unknown."""
    return _RADIO_WLAN_STANDARD_KEYS.get(_spec_token(value))


def radio_tx_retries(radio: Mapping[str, Any]) -> float | None:
    """Return a radio's retry rate in percent, keeping a real 0."""
    return _finite_number(get_field(radio, "txRetriesPct", "tx_retries_pct"))


def radio_tx_power_mode(radio: Mapping[str, Any]) -> str | None:
    """Return a radio's TX power mode state key."""
    return tx_power_mode_slug(get_field(radio, "txPowerMode", "tx_power_mode"))


def radio_wlan_standard(radio: Mapping[str, Any]) -> str | None:
    """Return a radio's Wi-Fi standard state key."""
    return wlan_standard_slug(get_field(radio, "wlanStandard", "wlan_standard"))


def _radio_number(radio: Mapping[str, Any], *keys: str) -> int | None:
    """Return a whole-number radio field, or None when it is not a number."""
    number = _finite_number(get_field(radio, *keys))
    return None if number is None else int(number)


def radio_channel(radio: Mapping[str, Any]) -> int | None:
    """Return a radio's channel number, or None."""
    return _radio_number(radio, "channel")


def radio_channel_width(radio: Mapping[str, Any]) -> int | None:
    """Return a radio's channel width in MHz, or None."""
    return _radio_number(radio, "channelWidthMHz", "channel_width_mhz")


def network_radio_stats(
    data: Mapping[str, Any], site_id: str, device_id: str
) -> Mapping[str, Mapping[str, Any]] | None:
    """
    Return one Network device's radios from its latest statistics, or None.

    The statistics endpoint already carries these radios, so step-1 sensors
    cost no request of their own. None means the device has no published
    statistics; an empty mapping means it reported no usable radio.
    """
    stats = network_device_stats(data, site_id, device_id)
    return None if stats is None else radio_band_map(stats.get("interfaces"))


def network_radio_details(
    data: Mapping[str, Any], site_id: str, device_id: str
) -> Mapping[str, Any] | None:
    """
    Return one Network device's published radio settings record, or None.

    The record is ``{"radios": [...], "available": bool}``. ``available`` is
    False while the last details request failed, so the step-2 sensors keep
    their radios but report unavailable instead of a frozen value.
    """
    return _record(data, "radio_details", site_id, device_id)


__all__ = [
    "CPU_STAT_KEYS",
    "MEMORY_STAT_KEYS",
    "NETWORK_DEVICE_STATES",
    "NETWORK_FACTS_KEY",
    "RADIO_BAND_LABELS",
    "RADIO_TX_POWER_MODE_OPTIONS",
    "RADIO_WLAN_STANDARD_OPTIONS",
    "TRANSIENT_SERVICEABLE_STATES",
    "UPTIME_STAT_KEYS",
    "AvailabilityOwner",
    "ClientConnectionType",
    "NetworkDeviceFacts",
    "NetworkDeviceKind",
    "NetworkPortFacts",
    "OnlineState",
    "SiteNetworkFacts",
    "_normalize_innerspace_mac",
    "as_bits_per_second",
    "build_innerspace_view",
    "build_site_network_facts",
    "bytes_to_bits_per_second",
    "client_connection_type",
    "client_is_connected",
    "correlate_innerspace_devices",
    "current_chime_ringtone_id",
    "device_facts",
    "device_kind",
    "device_online_state",
    "device_ports",
    "device_serviceable",
    "first_not_none",
    "get_field",
    "get_site_gateway_id",
    "innerspace_correlation_key",
    "innerspace_view_inputs",
    "is_volatile_storage_mount",
    "latest_voucher",
    "network_client",
    "network_device",
    "network_device_stats",
    "network_radio_details",
    "network_radio_stats",
    "normalize_innerspace_snapshot",
    "normalize_legacy_storage",
    "normalize_legacy_wans",
    "normalize_mac",
    "owner_available",
    "parse_floor_plan_asset_path",
    "port_index",
    "protect_collection",
    "protect_device",
    "radio_band_map",
    "radio_band_slug",
    "radio_channel",
    "radio_channel_width",
    "radio_tx_power_mode",
    "radio_tx_retries",
    "radio_wlan_standard",
    "rule_record",
    "section_by_site",
    "site_device_facts",
    "site_network_facts",
    "site_record",
    "site_section",
    "site_section_available",
    "storage_used_percent",
    "transform_innerspace_device",
    "transform_innerspace_floor_plan",
    "transform_innerspace_project",
    "tx_power_mode_slug",
    "uplink_rate_bps",
    "wlan_standard_slug",
]


# --- LED level ---------------------------------------------------------------

# Conversions for the UniFi Protect floodlight LED level (1-6).

MIN_LED_LEVEL = 1
MAX_LED_LEVEL = 6


def valid_led_level(value: Any) -> int | None:
    """
    Return a Protect ``ledLevel`` as an int, or None if it is not 1-6.

    The API spec types ``ledLevel`` as a number, so a whole-number float such
    as ``3.0`` is accepted. Booleans, fractions and out-of-range values are not.

    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    level = int(value)
    if MIN_LED_LEVEL <= level <= MAX_LED_LEVEL:
        return level
    return None


def percent_to_led_level(percent: float) -> int:
    """Convert a 0-100 brightness percentage to a Protect LED level (1-6)."""
    return max(MIN_LED_LEVEL, min(MAX_LED_LEVEL, round(percent * MAX_LED_LEVEL / 100)))


def led_level_to_percent(level: Any) -> int | None:
    """Convert a Protect LED level (1-6) to a 0-100 percentage, or None."""
    led_level = valid_led_level(level)
    if led_level is None:
        return None
    return round(led_level * 100 / MAX_LED_LEVEL)


# --- Storage telemetry -------------------------------------------------------

# Normalize aggregate NVR storage already delivered by the coordinator.
#
# Direct byte fields precede storageInfo, then storage_info. CamelCase precedes
# snake_case within each source; invalid aliases fall through. Capacity tuples
# stay within one source: contradictory tuples are rejected, never spliced.
# Coherent byte counts precede storage.used (percentage only). Health prefers
# storageInfo/storage_info over storage. Only literal booleans convey health.
# Percentages round to one decimal before the inclusive 90% comparison, matching
# console mounts. No telemetry is inferred from omitted model defaults.

_FULL_PERCENT = 100


@dataclass(frozen=True, slots=True)
class UnifiInsightsStorageTelemetry:
    """Validated storage signals; None means unknown, including health."""

    used_bytes: int | None = None
    total_bytes: int | None = None
    available_bytes: int | None = None
    used_percent: float | None = None
    healthy: bool | None = None

    @property
    def degraded(self) -> bool | None:
        """Return a problem only when health was explicitly supplied."""
        return None if self.healthy is None else not self.healthy

    @property
    def nearly_full(self) -> bool | None:
        """Use the same displayed percentage boundary as console mounts."""
        if self.used_percent is None:
            return None
        return self.used_percent >= STORAGE_NEARLY_FULL_PERCENT


def _number(value: object) -> float | int | None:
    """Reject malformed, negative, non-finite and boolean readings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        if not math.isfinite(value):
            return None
    except OverflowError:
        # The existing GB conversion cannot represent arbitrary-size integers.
        return None
    return value if value >= 0 else None


def _bytes(source: Mapping[str, Any], *aliases: str) -> int | None:
    """Pick the first valid alias, preserving zero."""
    for alias in aliases:
        if (value := _number(source.get(alias))) is not None:
            return int(value)
    return None


def normalize_nvr_storage(nvr: Mapping[str, Any]) -> UnifiInsightsStorageTelemetry:
    """Return optional capacities, percentage and health without mutating data."""
    nested = [
        value
        for key in ("storageInfo", "storage_info")
        if isinstance(value := nvr.get(key), Mapping)
    ]
    sources = [
        (
            _bytes(nvr, "storageUsedBytes", "storage_used_bytes"),
            _bytes(nvr, "storageTotalBytes", "storage_total_bytes"),
            _bytes(nvr, "storageAvailableBytes", "storage_available_bytes"),
        ),
        *[
            (
                _bytes(
                    info, "usedSize", "used_size", "usedSpaceBytes", "used_space_bytes"
                ),
                _bytes(
                    info,
                    "totalSize",
                    "total_size",
                    "totalSpaceBytes",
                    "total_space_bytes",
                ),
                _bytes(info, "availableSize", "available_size"),
            )
            for info in nested
        ],
    ]
    used = total = available = None
    for candidate_used, candidate_total, candidate_available in sources:
        if all(
            value is None
            for value in (candidate_used, candidate_total, candidate_available)
        ):
            continue
        if candidate_total is not None and (
            (candidate_used is not None and candidate_used > candidate_total)
            or (
                candidate_available is not None
                and candidate_available > candidate_total
            )
            or (
                candidate_used is not None
                and candidate_available is not None
                and candidate_used + candidate_available > candidate_total
            )
        ):
            continue
        used, total, available = candidate_used, candidate_total, candidate_available
        break

    percent = None
    if used is not None and total is not None:
        if available is None:
            available = total - used
        if total > 0:
            percent = round(used / total * 100, 1)
    storage = nvr.get("storage")
    if not isinstance(storage, Mapping):
        storage = {}
    if (
        percent is None
        and (value := _number(storage.get("used"))) is not None
        and value <= _FULL_PERCENT
    ):
        percent = round(float(value), 1)
    healthy = next(
        (
            health_value
            for info in [*nested, storage]
            if isinstance(health_value := info.get("healthy"), bool)
        ),
        None,
    )
    return UnifiInsightsStorageTelemetry(used, total, available, percent, healthy)


# --- Voucher state -----------------------------------------------------------

# Pure helpers and data structures for hotspot voucher entities.

TIME_LIMIT_MINUTES_RANGE: Final = (1, 1_000_000)  # spec timeLimitMinutes
GUEST_LIMIT_MIN: Final = 1  # spec authorizedGuestLimit (no spec max)
DATA_LIMIT_MBYTES_RANGE: Final = (1, 1_048_576)  # spec dataUsageLimitMBytes
RATE_LIMIT_KBPS_RANGE: Final = (2, 100_000)  # spec rx/txRateLimitKbps
GUEST_LIMIT_UI_MAX: Final = 1000  # our cap, NOT from the spec
KBPS_PER_MBPS: Final = 1000


@dataclass(frozen=True, slots=True)
class VoucherInputSpec:
    """Specification of an input number entity for voucher generation."""

    field: str
    minimum: float
    maximum: float
    step: float
    default: float


VOUCHER_INPUTS: Final[dict[str, VoucherInputSpec]] = {
    "voucher_duration": VoucherInputSpec(
        "duration_minutes",
        float(TIME_LIMIT_MINUTES_RANGE[0]),
        float(TIME_LIMIT_MINUTES_RANGE[1]),
        1.0,
        480.0,
    ),
    "voucher_guest_limit": VoucherInputSpec(
        "guest_limit",
        0.0,
        float(GUEST_LIMIT_UI_MAX),
        1.0,
        1.0,
    ),
    "voucher_download_limit": VoucherInputSpec(
        "download_limit_mbps",
        0.0,
        float(RATE_LIMIT_KBPS_RANGE[1] / KBPS_PER_MBPS),
        0.1,
        0.0,
    ),
    "voucher_upload_limit": VoucherInputSpec(
        "upload_limit_mbps",
        0.0,
        float(RATE_LIMIT_KBPS_RANGE[1] / KBPS_PER_MBPS),
        0.1,
        0.0,
    ),
    "voucher_data_limit": VoucherInputSpec(
        "data_limit_mb",
        0.0,
        float(DATA_LIMIT_MBYTES_RANGE[1]),
        1.0,
        0.0,
    ),
}


@dataclass(frozen=True, slots=True)
class VoucherSettings:
    """Per-site settings held in Home Assistant for voucher generation."""

    duration_minutes: int = 480
    guest_limit: int | float = 1
    download_limit_mbps: float = 0.0
    upload_limit_mbps: float = 0.0
    data_limit_mb: int = 0


def cast_input(key: str, value: float) -> int | float:
    """Cast an input value to its target type and precision."""
    spec = VOUCHER_INPUTS[key]
    if spec.step == 1:
        return round(value)
    return round(float(value), 3)


def restore_input(key: str, value: object) -> int | float | None:
    """Restore an input value if valid and within bounds, else None."""
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    val = float(value)
    if math.isnan(val) or math.isinf(val):
        return None
    spec = VOUCHER_INPUTS.get(key)
    if spec is None:
        return None
    if val < spec.minimum or val > spec.maximum:
        return None
    return cast_input(key, val)


def voucher_request_kwargs(settings: VoucherSettings) -> dict[str, int]:
    """Convert settings into UniFi voucher creation kwargs with clamped bounds."""
    duration = round(settings.duration_minutes)
    duration = max(
        TIME_LIMIT_MINUTES_RANGE[0], min(duration, TIME_LIMIT_MINUTES_RANGE[1])
    )
    kwargs: dict[str, int] = {
        "time_limit_minutes": duration,
    }
    if settings.guest_limit > 0:
        kwargs["authorized_guest_limit"] = max(
            GUEST_LIMIT_MIN, round(settings.guest_limit)
        )
    if settings.data_limit_mb > 0:
        data_mb = round(settings.data_limit_mb)
        data_mb = max(
            DATA_LIMIT_MBYTES_RANGE[0], min(data_mb, DATA_LIMIT_MBYTES_RANGE[1])
        )
        kwargs["data_usage_limit_mbytes"] = data_mb
    if settings.download_limit_mbps > 0:
        rx_kbps = round(settings.download_limit_mbps * KBPS_PER_MBPS)
        rx_kbps = max(RATE_LIMIT_KBPS_RANGE[0], min(rx_kbps, RATE_LIMIT_KBPS_RANGE[1]))
        kwargs["rx_rate_limit_kbps"] = rx_kbps
    if settings.upload_limit_mbps > 0:
        tx_kbps = round(settings.upload_limit_mbps * KBPS_PER_MBPS)
        tx_kbps = max(RATE_LIMIT_KBPS_RANGE[0], min(tx_kbps, RATE_LIMIT_KBPS_RANGE[1]))
        kwargs["tx_rate_limit_kbps"] = tx_kbps
    return kwargs


def voucher_to_record(voucher: object) -> dict[str, Any] | None:
    """Convert a voucher model or dict to an internal record dict."""
    if isinstance(voucher, Mapping):
        rec = dict(voucher)
    elif callable(dump := getattr(voucher, "model_dump", None)):
        try:
            dumped = dump(by_alias=True)
        except Exception:
            return None
        if isinstance(dumped, dict):
            rec = dumped
        else:
            return None
    else:
        return None

    if not rec.get("id"):
        return None
    return rec


def parse_timestamp(value: object) -> datetime | None:
    """Parse a timestamp into a UTC-aware datetime or None."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=dt_util.UTC)
        return value
    if isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        if parsed is not None:
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=dt_util.UTC)
            return parsed
        return None
    return None


def _field(record: Mapping[str, Any], *keys: str) -> Any:
    """Return the first non-None value among keys in record, or None."""
    return get_field(record, *keys)


def is_voucher_active(record: Mapping[str, Any], now: datetime) -> bool:
    """Return True if voucher is not expired and can still admit a guest."""
    if _field(record, "expired") is True:
        return False

    expires_at = parse_timestamp(_field(record, "expiresAt", "expires_at"))
    if expires_at is not None and expires_at <= now:
        return False

    limit = _field(record, "authorizedGuestLimit", "authorized_guest_limit")
    if (
        limit is not None
        and not isinstance(limit, bool)
        and isinstance(limit, (int, float))
    ):
        count = _field(record, "authorizedGuestCount", "authorized_guest_count")
        guest_count = (
            count
            if (
                count is not None
                and not isinstance(count, bool)
                and isinstance(count, (int, float))
            )
            else 0
        )
        if guest_count >= limit:
            return False

    return True


def count_active_vouchers(inventory: object, now: datetime | None = None) -> int:
    """Count redeemable vouchers in a site inventory."""
    if not isinstance(inventory, Mapping):
        return 0
    current_time = now if now is not None else dt_util.utcnow()
    count = 0
    for item in inventory.values():
        if isinstance(item, Mapping) and is_voucher_active(item, current_time):
            count += 1
    return count


def latest_voucher_qr_payload(
    record: object, now: datetime | None = None
) -> str | None:
    """Extract QR code payload from latest voucher record."""
    if not isinstance(record, Mapping):
        return None
    if record.get("expired") is True:
        return None
    expires_at_raw = _field(record, "expiresAt", "expires_at")
    if expires_at_raw is not None:
        expires_at = parse_timestamp(expires_at_raw)
        if expires_at is not None:
            current_time = now if now is not None else dt_util.utcnow()
            if expires_at <= current_time:
                return None
    code = record.get("code")
    if isinstance(code, str) and code:
        return code
    return None


def refresh_latest_voucher(
    record: Mapping[str, Any], inventory: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Refresh latest voucher record from polled inventory if present."""
    voucher_id = record.get("id")
    if voucher_id and isinstance(inventory, Mapping):
        item = inventory.get(voucher_id)
        if isinstance(item, Mapping):
            new_record = dict(item)
            if record.get("expired") is True:
                new_record["expired"] = True
            for camel, snake in (
                ("activatedAt", "activated_at"),
                ("expiresAt", "expires_at"),
            ):
                if _field(new_record, camel, snake) is None:
                    known_value = _field(record, camel, snake)
                    if known_value is not None:
                        new_record[camel] = known_value
            old_count = record.get("authorizedGuestCount")
            new_count = new_record.get("authorizedGuestCount")
            if (
                isinstance(old_count, int)
                and not isinstance(old_count, bool)
                and isinstance(new_count, int)
                and not isinstance(new_count, bool)
                and old_count > new_count
            ):
                new_record["authorizedGuestCount"] = old_count
            return new_record
    return dict(record)


def voucher_site_ids(data: object) -> list[str]:
    """Return site IDs if data contains both sites and vouchers mapping sections."""
    if not isinstance(data, Mapping):
        return []
    sites = data.get("sites")
    vouchers = data.get("vouchers")
    if not isinstance(sites, Mapping) or not isinstance(vouchers, Mapping):
        return []
    return [str(site_id) for site_id in sites]


# --- Port labels and camera models -------------------------------------------


def get_port_label(port: Mapping[str, Any], port_idx: Any) -> str:
    """Return user-friendly port label based on port type."""
    name = port.get("name")
    if name and name != f"Port {port_idx}":
        return str(name)
    media = port.get("media")
    if isinstance(media, str) and media.startswith("SFP"):
        return f"{media} {port_idx}"
    return f"Port {port_idx}"


def is_doorbell_camera_model(camera_data: dict[str, Any]) -> bool:
    """
    Return whether a cached camera record says, by its model, that it is a doorbell.

    This is the evidence a camera's type gives, as opposed to its name: a
    camera called "Front Door" is not a doorbell because of that.
    """
    camera_type = camera_data.get("_camera_type") or ""
    if camera_type in (
        CAMERA_TYPE_DOORBELL,
        CAMERA_TYPE_DOORBELL_WITH_PACKAGE_DETECTION,
        CAMERA_TYPE_DOORBELL_MAIN,
        CAMERA_TYPE_DOORBELL_PACKAGE,
    ):
        return True

    # The camera type field from the API (handle None safely)
    api_camera_type = (camera_data.get("type") or "").lower()
    return any(
        doorbell_type in api_camera_type
        for doorbell_type in (
            "doorbell",
            "g4-doorbell",
            "ai-doorbell",
            "g4doorbell",
            "aidoorbell",
        )
    )


# --- Storage mounts ----------------------------------------------------------


def get_storage_mounts(device_data: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the usable normalised mounts of a device (empty when none)."""
    mounts = device_data.get("storage_mounts") if device_data else None
    if not isinstance(mounts, list):
        return []
    return [
        mount
        for mount in mounts
        if isinstance(mount, dict)
        and isinstance(mount.get("mount_point"), str)
        and mount["mount_point"]
    ]


def find_storage_mount(
    device_data: dict[str, Any] | None, mount_point: str
) -> dict[str, Any] | None:
    """Return the mount with this mount point, or None when it is gone."""
    for mount in get_storage_mounts(device_data):
        if mount["mount_point"] == mount_point:
            return mount
    return None


def storage_mount_labels(mounts: list[dict[str, Any]]) -> dict[str, str]:
    """
    Return a display label per mount point, unique within the device.

    The label is the console's ``name`` ("eMMC", "Backup") or the mount path
    when it has none. Labels that two mounts share (compared case-insensitively)
    get the mount path appended so entity names never repeat.
    """
    bases = {
        mount["mount_point"]: mount.get("name") or mount["mount_point"]
        for mount in mounts
    }
    counts = Counter(base.casefold() for base in bases.values())
    return {
        mount_point: f"{base} ({mount_point})" if counts[base.casefold()] > 1 else base
        for mount_point, base in bases.items()
    }


def persistent_storage_mounts(
    device_data: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return the mounts that count towards "storage nearly full"."""
    return [
        mount
        for mount in get_storage_mounts(device_data)
        if not is_volatile_storage_mount(mount)
    ]


def fullest_persistent_storage_mount(
    device_data: dict[str, Any] | None,
) -> tuple[dict[str, Any], float] | None:
    """Return the persistent mount with the highest valid fill level."""
    scored = [
        (mount, percent)
        for mount in persistent_storage_mounts(device_data)
        if (percent := storage_used_percent(mount)) is not None
    ]
    return max(scored, key=lambda item: item[1]) if scored else None


# --- Protect security devices ------------------------------------------------

THREAD_DEVICE_TYPES = (DEVICE_TYPE_LINK_STATION, DEVICE_TYPE_ALARM_HUB)


def nested_dict(data: dict[str, Any], *keys: str) -> dict[str, Any] | None:
    """
    Follow ``keys`` through nested dicts, or return None.

    Coordinator dicts come from ``model_dump(exclude_none=False)``, so an
    absent object is an explicit ``None`` and ``data.get(key, {})`` would not
    fall back.
    """
    value: Any = data
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value if isinstance(value, dict) else None


def thread_network(data: dict[str, Any]) -> dict[str, Any] | None:
    """Return ``threadState.network``; None on SKUs without Thread."""
    return nested_dict(data, "threadState", "network")


def has_thread_network(data: dict[str, Any]) -> bool:
    """Return True if the gateway reports a Thread network."""
    return thread_network(data) is not None


def has_keypad(data: dict[str, Any]) -> bool:
    """Return True for a fob with a PIN keypad."""
    flags = data.get("featureFlags")
    return isinstance(flags, dict) and flags.get("hasKeypad") is True


def always_true(_data: dict[str, Any]) -> bool:
    return True


def present_attributes(**attributes: Any) -> dict[str, Any]:
    """Drop attributes that are None rather than reporting null values."""
    return {key: value for key, value in attributes.items() if value is not None}
