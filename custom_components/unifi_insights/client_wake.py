# Copyright (c) 2026 Ruaan Deysel

"""
Pure helpers for the Wake-on-LAN client buttons.

No I/O and no Home Assistant imports, so the coordinator facade and the
button platform can both use them. It must not import ``entity`` (which
imports the coordinators): that would be an import cycle with the facade.
"""

from __future__ import annotations

import ipaddress
import math
from typing import TYPE_CHECKING, Any, Final

from .const import DOMAIN
from .topology import first_present, normalize_mac

if TYPE_CHECKING:
    from collections.abc import Iterable

# Home Assistant core action that sends the magic packet.
WAKE_ON_LAN_DOMAIN: Final = "wake_on_lan"
WAKE_ON_LAN_SERVICE: Final = "send_magic_packet"

WAKE_UNIQUE_ID_SUFFIX: Final = "_wake"
# An offline wired client only gets a Wake button from the startup history seed
# if the console saw it this recently.
WAKE_HISTORY_MAX_AGE_SECONDS: Final = 30 * 24 * 60 * 60
# Only LAN-like networks can hold a wired client. WAN and VPN networks are skipped.
_WAKE_NETWORK_PURPOSES: Final = frozenset({"corporate", "guest"})
# A /31 or /32 has no directed broadcast address of its own.
_MAX_BROADCAST_PREFIXLEN: Final = 30


def wake_unique_id(mac: str) -> str:
    """Return the entity unique ID of the Wake button for a normalized MAC."""
    return f"{DOMAIN}_{mac}{WAKE_UNIQUE_ID_SUFFIX}"


def mac_from_wake_unique_id(unique_id: str) -> str | None:
    """Return the MAC a Wake button unique ID identifies, or None if it is not one."""
    prefix = f"{DOMAIN}_"
    if not (unique_id.startswith(prefix) and unique_id.endswith(WAKE_UNIQUE_ID_SUFFIX)):
        return None
    mac = unique_id[len(prefix) : -len(WAKE_UNIQUE_ID_SUFFIX)]
    return mac if normalize_mac(mac) == mac else None


def find_client_by_mac(
    clients_by_site: Any, mac: str
) -> tuple[str, dict[str, Any]] | None:
    """Return (site_id, client) for the first client record with this normalized MAC."""
    if not isinstance(clients_by_site, dict):
        return None
    for site_id, clients in clients_by_site.items():
        if not isinstance(clients, dict):
            continue
        for client in clients.values():
            if not isinstance(client, dict):
                continue
            candidate = first_present(client, "macAddress", "mac_address", "mac")
            if normalize_mac(candidate) == mac:
                return str(site_id), client
    return None


def first_non_blank_text(record: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-blank string value among ``keys``."""
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _seen_within(last_seen: Any, *, now: float) -> bool:
    """Return True if a classic ``last_seen`` epoch is inside the history window."""
    if isinstance(last_seen, bool) or not isinstance(last_seen, (int, float)):
        return False
    try:
        age = now - float(last_seen)
    except OverflowError:
        return False
    return math.isfinite(age) and age <= WAKE_HISTORY_MAX_AGE_SECONDS


def select_wake_history(
    records: Iterable[Any], *, now: float, enabled_macs: set[str] | None = None
) -> dict[str, str]:
    """
    Pick the historical clients that deserve a Wake button, as MAC -> name.

    A classic ``stat/alluser`` record qualifies when it is wired, has a valid
    MAC and a name or hostname, and was last seen within the history window.
    Enabled buttons may use names from any age, including wireless history.
    The first record wins when a MAC repeats.
    """
    selected: dict[str, str] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        mac = normalize_mac(record.get("mac"))
        name = first_non_blank_text(record, "name", "hostname")
        if mac is None or name is None or mac in selected:
            continue
        enabled = enabled_macs is not None and mac in enabled_macs
        if not enabled and (
            record.get("is_wired") is not True
            or not _seen_within(record.get("last_seen"), now=now)
        ):
            continue
        selected[mac] = name
    return selected


def history_network_hint(
    records: Iterable[Any], mac: str
) -> tuple[str | None, str | None]:
    """Return (last IP, last network id) from the alluser record for ``mac``."""
    for record in records:
        if not isinstance(record, dict) or normalize_mac(record.get("mac")) != mac:
            continue
        return (
            first_non_blank_text(record, "last_ip", "fixed_ip", "ip"),
            first_non_blank_text(record, "last_connection_network_id", "network_id"),
        )
    return None, None


def derive_directed_broadcast(
    networks: Iterable[Any],
    *,
    ip: str | None = None,
    network_id: str | None = None,
) -> str | None:
    """
    Return the directed broadcast address of the client's network, or None.

    ``networks`` are the dicts from ``NetworksEndpoint.get_legacy_all``. A
    matching ``network_id`` wins over ``ip``. An ``ip`` must fall in exactly
    one usable network; overlapping networks make the answer ambiguous, and an
    ambiguous or unknown answer is None so the caller falls back to
    255.255.255.255.
    """
    candidates: list[tuple[Any, ipaddress.IPv4Network]] = []
    for network in networks:
        if not isinstance(network, dict):
            continue
        if network.get("purpose") not in _WAKE_NETWORK_PURPOSES:
            continue
        if network.get("enabled") is False:
            continue
        subnet = network.get("ip_subnet")
        if not isinstance(subnet, str):
            continue
        try:
            interface = ipaddress.IPv4Interface(subnet.strip())
        except ValueError:
            continue
        if interface.network.prefixlen > _MAX_BROADCAST_PREFIXLEN:
            continue
        candidates.append((network.get("id"), interface.network))

    if network_id:
        for candidate_id, candidate_network in candidates:
            if candidate_id == network_id:
                return str(candidate_network.broadcast_address)

    if ip:
        try:
            address = ipaddress.IPv4Address(ip.strip())
        except ValueError:
            return None
        matches = [net for _, net in candidates if address in net]
        if len(matches) == 1:
            return str(matches[0].broadcast_address)
    return None
