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
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, Literal

from .const import GATEWAY_MODEL_PREFIXES
from .innerspace_transforms import (
    _normalize_innerspace_mac,
    correlate_innerspace_devices,
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
    ``UnifiDeviceCoordinator._normalize_mac`` (exact legacy key matching),
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
    raw = _enum_value(get_field(device, "state", "status"))
    if not isinstance(raw, str):
        return "unknown"
    return _ONLINE_STATE_BY_DEVICE_STATE.get(raw.strip().upper(), "unknown")


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
    kinds = {key: device_kind(device) for key, _, device in records}
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


__all__ = [
    "CPU_STAT_KEYS",
    "MEMORY_STAT_KEYS",
    "NETWORK_DEVICE_STATES",
    "NETWORK_FACTS_KEY",
    "UPTIME_STAT_KEYS",
    "ClientConnectionType",
    "NetworkDeviceFacts",
    "NetworkDeviceKind",
    "NetworkPortFacts",
    "OnlineState",
    "SiteNetworkFacts",
    "_normalize_innerspace_mac",
    "as_bits_per_second",
    "build_site_network_facts",
    "bytes_to_bits_per_second",
    "client_connection_type",
    "correlate_innerspace_devices",
    "current_chime_ringtone_id",
    "device_facts",
    "device_kind",
    "device_online_state",
    "device_ports",
    "first_not_none",
    "get_field",
    "get_site_gateway_id",
    "is_volatile_storage_mount",
    "normalize_innerspace_snapshot",
    "normalize_legacy_storage",
    "normalize_legacy_wans",
    "normalize_mac",
    "parse_floor_plan_asset_path",
    "port_index",
    "site_device_facts",
    "site_network_facts",
    "storage_used_percent",
    "transform_innerspace_device",
    "transform_innerspace_floor_plan",
    "transform_innerspace_project",
    "uplink_rate_bps",
]
