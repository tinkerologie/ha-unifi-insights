# Copyright 2026 UniFi Insights contributors
"""Diagnostics support for UniFi Insights."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_VERIFY_SSL
from unifi_official_api import __version__ as api_version
from unifi_official_api.const import SENSITIVE_CREDENTIAL_KEYS

from .carrier_fabric_data import CarrierFabricData
from .const import (
    CONF_ACCESS_WEBHOOK_ENDPOINT_ID,
    CONF_ACCESS_WEBHOOK_ID,
    CONF_ACCESS_WEBHOOK_SECRET,
    CONF_CONNECTION_TYPE,
    CONF_CONSOLE_ID,
    CONNECTION_TYPE_REMOTE,
    ISP_WAN_NUMBERS,
    MOBILITY_MODELS,
    MOBILITY_ROUTER_STATES,
    MOBILITY_WORKSPACE_STATUSES,
    SITE_MANAGER_COLLECTIONS,
)
from .coordinators.carrier_fabric import (
    SERVICE_PLAN_ALLOWLIST,
    SUBSCRIBER_ALLOWLIST,
)
from .data_transforms import NETWORK_FACTS_KEY
from .innerspace_transforms import build_innerspace_diagnostics_summary

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.core import HomeAssistant

    from . import UnifiInsightsConfigEntry
    from .carrier_fabric_data import CarrierFabricConfigEntry
    from .coordinators.access import UnifiInsightsAccessCoordinator
    from .coordinators.mobility import UnifiInsightsMobilityCoordinator

_LOGGER = logging.getLogger(__name__)
_MAX_HOST_SITE_COUNTS = 20
_MAX_ISP_SAMPLES = 8
TO_REDACT = {
    # Credentials and secrets (shared with API transport logging, plus
    # HA-layer constants)
    *SENSITIVE_CREDENTIAL_KEYS,
    CONF_API_KEY,
    # Access webhook state: the receiver ID, endpoint ID and signing secret
    CONF_ACCESS_WEBHOOK_ID,
    CONF_ACCESS_WEBHOOK_ENDPOINT_ID,
    CONF_ACCESS_WEBHOOK_SECRET,
    # Network identifiers
    CONF_HOST,
    "ip",
    "ipAddress",
    "ip_address",
    "ipAddresses",
    "ip_addresses",
    "host",
    "hostname",
    "wan_ip",
    "wanIp",
    "lan_ip",
    "lanIp",
    "sourceAddress",
    "source_address",
    "destinationAddress",
    "destination_address",
    "domainName",
    "domain_name",
    # WiFi network identity: an SSID names a household and locates it in
    # public wardriving databases, so it is redacted like any other identifier.
    "ssid",
    "essid",
    # Device identifiers
    "serial",
    "serialNumber",
    "hardwareId",
    "hardware_id",
    # HA config entry fields
    "unique_id",
    CONF_CONSOLE_ID,
    CONF_VERIFY_SSL,
    # Common API response id fields that may contain sensitive info
    "id",
    "deviceId",
    "siteId",
    "site_id",
    "floor_plan_id",
    "floorPlanId",
    "matched_device_id",
    "matched_site_id",
    "image_url",
    "imageUrl",
    # Location data
    "latitude",
    "longitude",
    # Smart detections that name a person or their vehicle
    "licensePlate",
    "license_plate",
    # Protect alarm hub tamper events name the user involved
    "userName",
    "_lastTamperUser",
    # Thread network identity (the network key itself is never exposed)
    "networkName",
    "panId",
    "extendedPanId",
}

# Labels that name a person rather than a piece of hardware. These are only
# redacted inside the records where they carry personal data - a client is
# usually named after its owner ("Sarah's iPhone"), while the name of a switch,
# a site or a camera is what makes a diagnostics download readable at all.
PERSONAL_NAMES = {
    "name",
    "displayName",
    "display_name",
    "deviceName",
    "device_name",
    "note",
    "userId",
    "user_id",
}

# Client records: everything above, plus the owner-supplied names.
CLIENT_TO_REDACT = TO_REDACT | PERSONAL_NAMES

# WiFi records: the SSID is also carried in the record's "name" field.
WIFI_TO_REDACT = TO_REDACT | {"name"}

# Hotspot vouchers: voucher codes are guest credentials; the note ("name" or
# "note") can name a guest.
VOUCHER_TO_REDACT = TO_REDACT | {"code", "name", "note"}

# Port forwards: the forward target, the allowed source and the WAN-side
# destination locate hosts inside and outside the network.
PORT_FORWARD_TO_REDACT = TO_REDACT | {
    "fwd",
    "src",
    "destination_ip",
    "destination_ips",
}
# Traffic rules: the blocked or allowed domains and address ranges
# ("ip_addresses" is already in TO_REDACT; client MACs are anonymised below).
TRAFFIC_RULE_TO_REDACT = TO_REDACT | {"domains", "ip_ranges"}
# NAT rules: the translated address ("ip_address") is already in TO_REDACT; the
# source and destination filters carry their match address under "address".
NAT_RULE_TO_REDACT = TO_REDACT | {"address"}
# Static routes: the destination network and the next hop map the routed LAN.
# ("gateway_device" is a MAC and is anonymised below.)
STATIC_ROUTE_TO_REDACT = TO_REDACT | {"static-route_network", "static-route_nexthop"}
# Firewall policies (official API): the source and destination filters hold the
# matched addresses, domains and MACs in nested objects with generic keys
# ("items", "value", "start", "stop"), so the filter objects go as a whole.
FIREWALL_RULE_TO_REDACT = TO_REDACT | {
    "ipAddressFilter",
    "domainFilter",
    "macAddressFilter",
    "ipv6IidFilter",
}
# Policy-based routes: the routed domains, ranges and next hop ("ipAddresses"
# is already in TO_REDACT; client MACs are anonymised below).
POLICY_BASED_ROUTE_TO_REDACT = TO_REDACT | {
    "domains",
    "ipRanges",
    "ip_ranges",
    "ips",
    "nextHop",
    "next_hop",
}

# Redaction key set of each rule section of the coordinator data. The redacted
# rule export does not use these: it keeps an allow-list (RULE_EXPORT_SAFE_KEYS).
RULE_SECTION_TO_REDACT: dict[str, set[str]] = {
    "firewall_rules": FIREWALL_RULE_TO_REDACT,
    "policy_based_routes": POLICY_BASED_ROUTE_TO_REDACT,
    "port_forwards": PORT_FORWARD_TO_REDACT,
    "traffic_rules": TRAFFIC_RULE_TO_REDACT,
    "nat_rules": NAT_RULE_TO_REDACT,
    "static_routes": STATIC_ROUTE_TO_REDACT,
}

# Keys whose value is a MAC address even when it arrives unpunctuated. Values
# are replaced with a per-report placeholder rather than dropped; see
# _anonymize_macs.
MAC_KEYS = frozenset(
    {
        "mac",
        "macAddress",
        "mac_address",
        "macAddresses",
        "mac_addresses",
        "macFilterList",
        "mac_filter_list",
        "bssid",
        "apMac",
        "ap_mac",
        "swMac",
        "sw_mac",
        "uplinkMac",
        "uplink_mac",
        "gatewayMac",
        "gateway_mac",
        "wanMac",
        "wan_mac",
        "lanMac",
        "lan_mac",
        "client_mac",
    }
)

# A punctuated MAC anywhere in a string, whatever key it arrived under.
_MAC_PATTERN = re.compile(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b")
# A whole value that is a MAC. Unlike the pattern above this also accepts the
# unpunctuated form, which is only safe to assume when it is the entire value:
# twelve hex characters in the middle of a sentence are not an address.
_MAC_VALUE_PATTERN = re.compile(
    r"[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}|[0-9A-Fa-f]{12}"
)


def _mac_placeholder(mac: str, seen: dict[str, str]) -> str:
    """Return a stable placeholder for a MAC address within one report."""
    # Punctuation and case vary by endpoint, so real addresses canonicalize to
    # one key. Anything else keeps its own value as the key: stripping it down
    # to its hex characters would make "not-a-mac" and "aac" collide, and the
    # placeholders are only useful while distinct values stay distinct.
    lowered = mac.lower()
    normalized = (
        re.sub(r"[:-]", "", lowered)
        if _MAC_VALUE_PATTERN.fullmatch(lowered)
        else f"raw:{lowered}"
    )
    placeholder = seen.get(normalized)
    if placeholder is None:
        placeholder = f"**REDACTED-MAC-{len(seen) + 1}**"
        seen[normalized] = placeholder
    return placeholder


def _anonymize_macs(
    value: Any, seen: dict[str, str], *, is_mac_field: bool = False
) -> Any:
    """
    Replace every MAC address with a placeholder that is stable per report.

    Key-based redaction cannot keep up here: the API spells MAC addresses
    `macAddress`, `bssid`, `apMac`, `swMac` and more, and a client record
    accepts unknown extra fields, so any future MAC field would be published
    the moment the controller starts sending it. Every MAC-shaped value is
    therefore rewritten regardless of the key that carried it, punctuated or
    not, whether it appears as a value or as a mapping key.

    The same MAC always maps to the same placeholder, so a report still shows
    which access point or switch port a client sits behind, and devices keyed
    by MAC (those the API lists without an id) cannot collide into one entry.

    Args:
        value: The diagnostics payload, or any part of it.
        seen: Placeholders already handed out, keyed by normalized MAC.
        is_mac_field: Whether the value arrived under a known MAC key, in
            which case it is a MAC even if it is not punctuated like one.

    Returns:
        A copy of the value with MAC addresses replaced.

    """
    if isinstance(value, str):
        if not value or value == REDACTED:
            return value
        if is_mac_field or _MAC_VALUE_PATTERN.fullmatch(value):
            return _mac_placeholder(value, seen)
        return _MAC_PATTERN.sub(
            lambda match: _mac_placeholder(match.group(), seen), value
        )
    if isinstance(value, Mapping):
        return {
            (
                _mac_placeholder(key, seen)
                if isinstance(key, str) and _MAC_VALUE_PATTERN.fullmatch(key)
                else key
            ): _anonymize_macs(
                item, seen, is_mac_field=isinstance(key, str) and key in MAC_KEYS
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _anonymize_macs(item, seen, is_mac_field=is_mac_field) for item in value
        ]
    return value


def _redact_coordinator_data(data: Any) -> Any:
    """
    Redact a coordinator data snapshot.

    Client and WiFi records get the personal-name treatment on top of the
    shared key list; devices, sites and Protect records keep their names so
    the report stays readable.
    """
    redacted = async_redact_data(data, TO_REDACT)
    if not isinstance(data, Mapping) or not isinstance(redacted, dict):
        return redacted

    for section, to_redact in (
        ("clients", CLIENT_TO_REDACT),
        ("wifi", WIFI_TO_REDACT),
        ("vouchers", VOUCHER_TO_REDACT),
        ("latest_vouchers", VOUCHER_TO_REDACT),
        *RULE_SECTION_TO_REDACT.items(),
    ):
        records = data.get(section)
        if isinstance(records, Mapping):
            redacted[section] = {
                site_id: async_redact_data(site_records, to_redact)
                for site_id, site_records in records.items()
            }

    # Per-device statistics carry a copy of that device's client records.
    stats = redacted.get("stats")
    if isinstance(stats, dict):
        for site_stats in stats.values():
            if not isinstance(site_stats, dict):
                continue
            for device_stats in site_stats.values():
                if isinstance(device_stats, dict) and "clients" in device_stats:
                    device_stats["clients"] = async_redact_data(
                        device_stats["clients"], CLIENT_TO_REDACT
                    )

    return redacted


# The redacted rule export keeps only what is listed here and replaces the
# rest, so a key nobody has heard of cannot leak. Per rule type, the top-level
# keys that are kept; the keys are the console's own spellings, as
# ``model_dump(by_alias=True)`` emits them, and both spellings are listed where
# a model can emit either. A kept key's value must still pass
# _safe_export_value. Names, descriptions, addresses, domains, interfaces and
# network, client, group and zone references are deliberately not here.
RULE_EXPORT_SAFE_KEYS: dict[str, frozenset[str]] = {
    # Firewall policies (official API): identity, state, action, order, logging,
    # IP protocol scope and connection state. Source and destination filters
    # hold addresses, domains and zone references and are never kept.
    "firewall_rule": frozenset(
        {
            "id",
            "enabled",
            "action",
            "index",
            "logging",
            "loggingEnabled",
            "protocol",
            "ipProtocolScope",
            "connectionStateFilter",
        }
    ),
    # Policy-based routes: identity, state, kill switch and matching target.
    "policy_based_route": frozenset(
        {
            "_id",
            "enabled",
            "killSwitch",
            "kill_switch",
            "killSwitchEnabled",
            "kill_switch_enabled",
            "matchingTarget",
            "matching_target",
        }
    ),
    # Port forwards: identity, state and the port and protocol settings.
    "port_forward": frozenset(
        {
            "_id",
            "enabled",
            "proto",
            "dst_port",
            "fwd_port",
            "log",
            "src_limiting_enabled",
            "pfwd_interface",
        }
    ),
    # Traffic rules: identity, state, action and matching target.
    "traffic_rule": frozenset({"_id", "enabled", "action", "matching_target"}),
    # NAT rules: identity, state, type, protocol and order.
    "nat_rule": frozenset(
        {
            "_id",
            "enabled",
            "type",
            "protocol",
            "ip_version",
            "logging",
            "rule_index",
            "exclude",
            "is_predefined",
            "setting_preference",
            "port",
        }
    ),
    # Static routes: identity, state, route type, distance and gateway type.
    "static_route": frozenset(
        {
            "_id",
            "enabled",
            "type",
            "static-route_type",
            "static-route_distance",
            "gateway_type",
        }
    ),
}

# A string value that is kept: letters, digits, "_", "," and "-" only. An
# address, a CIDR, a domain, a URL and free text all contain something else.
_SAFE_EXPORT_STRING = re.compile(r"[A-Za-z0-9_,-]{0,64}")
# A mapping key that is kept; a mapping with any other key is replaced whole.
_SAFE_EXPORT_KEY = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _safe_export_value(value: Any) -> Any:
    """
    Return a value of a kept export key, or REDACTED when it is not safe.

    Numbers, booleans and None are kept; so are short strings made of letters,
    digits, "_", "," and "-". Lists and mappings are checked item by item, and
    a mapping with any other kind of key is replaced as a whole.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if _SAFE_EXPORT_STRING.fullmatch(value) else REDACTED
    if isinstance(value, list):
        return [_safe_export_value(item) for item in value]
    if isinstance(value, Mapping):
        if not all(
            isinstance(key, str) and _SAFE_EXPORT_KEY.fullmatch(key) for key in value
        ):
            return REDACTED
        return {key: _safe_export_value(item) for key, item in value.items()}
    return REDACTED


def _redact_export_rule(rule_type: str, record: Any) -> Any:
    """Keep the allow-listed keys of one exported rule and replace the rest."""
    if not isinstance(record, Mapping):
        return REDACTED
    # A key that is not an ordinary field name (an address, a host name, a
    # number) is data, not a field: the whole record goes, key names included.
    if not all(
        isinstance(key, str) and _SAFE_EXPORT_KEY.fullmatch(key) for key in record
    ):
        return REDACTED
    safe_keys = RULE_EXPORT_SAFE_KEYS.get(rule_type, frozenset())
    return {
        key: _safe_export_value(value) if key in safe_keys else REDACTED
        for key, value in record.items()
    }


def _redact_export_family(rule_type: str, exported: Any) -> Any:
    """Redact one rule family of one site; its status and count stay."""
    if not isinstance(exported, Mapping):
        return REDACTED
    family: dict[str, Any] = dict.fromkeys(exported, REDACTED)
    for key in ("status", "count"):
        if key in exported:
            family[key] = _safe_export_value(exported[key])
    rules = exported.get("rules")
    if isinstance(rules, list):
        family["rules"] = [_redact_export_rule(rule_type, rule) for rule in rules]
    return family


def _redact_export_site(site: Any) -> Any:
    """Redact one exported site: its name goes, its rule families are checked."""
    if not isinstance(site, Mapping):
        return REDACTED
    redacted: dict[str, Any] = dict.fromkeys(site, REDACTED)
    rule_types = site.get("rule_types")
    if isinstance(rule_types, Mapping):
        redacted["rule_types"] = {
            rule_type: _redact_export_family(rule_type, exported)
            for rule_type, exported in rule_types.items()
        }
    return redacted


def redact_network_rule_export(document: dict[str, Any]) -> dict[str, Any]:
    """
    Return a redacted copy of a rule export that is safe by construction.

    Only allow-listed rule keys keep their value (and only when it passes
    ``_safe_export_value``); every other value, and every key this function
    does not know, is replaced by REDACTED. Site ids become ``site-1``,
    ``site-2``, ... in the sorted order of the original ids, site names and the
    console's values are replaced, and every MAC address that still got through
    is anonymised. The input is not changed.
    """
    redacted: dict[str, Any] = dict.fromkeys(document, REDACTED)
    for key in ("format_version", "generated_at"):
        if key in document:
            redacted[key] = document[key]
    redacted["redacted"] = True
    console = document.get("console")
    if "console" in document:
        redacted["console"] = (
            dict.fromkeys(console, REDACTED)
            if isinstance(console, Mapping)
            else REDACTED
        )
    sites = document.get("sites")
    if isinstance(sites, Mapping):
        labels = {
            site_id: f"site-{number}"
            for number, site_id in enumerate(sorted(sites, key=str), start=1)
        }
        redacted["sites"] = {
            labels[site_id]: _redact_export_site(site)
            for site_id, site in sites.items()
        }
    result: dict[str, Any] = _anonymize_macs(redacted, {})
    return result


def _site_manager_summary(
    snapshot: Mapping[str, Any], console_id: str
) -> dict[str, Any]:
    """Build a bounded diagnostic view without cloud identifiers or raw data."""
    hosts = snapshot.get("hosts") or {}
    sites = snapshot.get("sites") or {}
    devices = snapshot.get("devices") or {}
    metrics = snapshot.get("isp_metrics") or {}
    configs = snapshot.get("sd_wan_configs") or {}
    collections = snapshot.get("collections") or {}

    site_counts: dict[str, int] = {}
    for site in sites.values():
        host_id = site.get("hostId") if isinstance(site, Mapping) else None
        if isinstance(host_id, str):
            site_counts[host_id] = site_counts.get(host_id, 0) + 1

    selected_devices = devices.get(console_id) or {}
    selected_device_count = len(selected_devices.get("devices") or [])
    selected_metrics = metrics.get(console_id) or {}
    samples: list[dict[str, Any]] = []
    for item in selected_metrics.values():
        if not isinstance(item, Mapping):
            continue
        metric_time = item.get("metric_time")
        wan = item.get("wan")
        if not isinstance(metric_time, str) or not isinstance(wan, Mapping):
            continue
        try:
            safe_time = datetime.fromisoformat(metric_time).isoformat()
        except ValueError:
            continue
        samples.append(
            {
                "metric_time": safe_time,
                "wan": {
                    key: value
                    for key in ISP_WAN_NUMBERS
                    if isinstance(value := wan.get(key), (int, float))
                    and not isinstance(value, bool)
                },
            }
        )
    samples.sort(key=lambda item: str(item["metric_time"]), reverse=True)

    sd_wan_types: dict[str, int] = {}
    for config in configs.values():
        config_type = config.get("type") if isinstance(config, Mapping) else None
        safe_type = "sdwan-hbsp" if config_type == "sdwan-hbsp" else "other"
        sd_wan_types[safe_type] = sd_wan_types.get(safe_type, 0) + 1

    return {
        "inventory": {
            "hosts": len(hosts),
            "sites": len(sites),
            "device_groups": len(devices),
            "devices": sum(
                len(group.get("devices") or [])
                for group in devices.values()
                if isinstance(group, Mapping)
            ),
            "sd_wan_configs": len(configs),
        },
        "host_site_counts": sorted(site_counts.values(), reverse=True)[
            :_MAX_HOST_SITE_COUNTS
        ],
        "host_site_counts_truncated": len(site_counts) > _MAX_HOST_SITE_COUNTS,
        "selected_host": {
            "found": console_id in hosts,
            "site_count": site_counts.get(console_id, 0),
            "device_count": selected_device_count,
            "isp_samples": samples[:_MAX_ISP_SAMPLES],
            "isp_samples_truncated": len(samples) > _MAX_ISP_SAMPLES,
        },
        "sd_wan_types": sd_wan_types,
        "collections": {
            name: {
                "available": bool(state.get("available")),
                "updated_at": state.get("updated_at"),
                "error": state.get("error"),
            }
            for name in SITE_MANAGER_COLLECTIONS
            if isinstance(state := collections.get(name), Mapping)
        },
        "last_attempt": snapshot.get("last_attempt"),
        "cooldown_until": snapshot.get("cooldown_until"),
    }


# Subscriber name and number identify a customer; they are the only allowlisted
# Carrier Fabric fields that are personal data.
CARRIER_REDACT_KEYS = frozenset({"name", "subscriberNumber"})


def _count_known(values: Iterable[Any], known: tuple[str, ...]) -> dict[str, int]:
    """Count enum values, folding anything unpublished into "other"."""
    counts: dict[str, int] = {}
    for value in values:
        bucket = value if value in known else "other"
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def _mobility_summary(coordinator: UnifiInsightsMobilityCoordinator) -> dict[str, Any]:
    """
    Describe Mobility health with counts only.

    Workspace and router names, ids, MACs, IP addresses, the carrier, Wi-Fi
    and VPN names and GPS positions all identify the account or its owner,
    so none of the snapshot's values leave except published enum values.
    """
    snapshot = coordinator.data
    workspaces = snapshot["workspaces"].values()
    devices = snapshot["devices"].values()
    interval = coordinator.update_interval
    return {
        "owner": True,
        "access": snapshot["access"],
        "last_update_success": coordinator.last_update_success,
        "last_error_type": coordinator.last_error_type,
        "update_interval_seconds": (
            int(interval.total_seconds()) if interval is not None else None
        ),
        "updated_at": snapshot["updated_at"],
        "workspaces": {
            "total": len(workspaces),
            "by_status": _count_known(
                (workspace.get("status") for workspace in workspaces),
                MOBILITY_WORKSPACE_STATUSES,
            ),
        },
        "devices": {
            "total": len(devices),
            "by_state": _count_known(
                (device.get("state") for device in devices), MOBILITY_ROUTER_STATES
            ),
            "by_model": _count_known(
                (device.get("model") for device in devices), MOBILITY_MODELS
            ),
            "with_location": sum(
                1 for device in devices if isinstance(device.get("location"), dict)
            ),
            "detail_unavailable": sum(
                1 for device in devices if not device.get("detail_available")
            ),
        },
    }


# Access collections carry user and actor identifiers, PIN material and IDs
# that link a door to a hub. Names stay, because they make a report readable.
ACCESS_TO_REDACT = TO_REDACT | {
    "id",
    "location_id",
    "user_id",
    "actor",
    "actor_id",
    "user_email",
    "first_name",
    "last_name",
    "full_name",
    "alias",
    "employee_number",
    "access_policy_ids",
    "access_policies",
    "pin",
    "pin_code",
    "pin_codes",
    "signature",
    "authentication_value",
    "token",
}


def _access_diagnostics(coordinator: UnifiInsightsAccessCoordinator) -> dict[str, Any]:
    """Summarize Access auth state, feature capability and redacted snapshot."""
    data = coordinator.data or {}
    snapshot = {
        "doors": [dict(door) for door in data.get("doors", {}).values()],
        "devices": [dict(device) for device in data.get("devices", {}).values()],
        "emergency": data.get("emergency"),
    }
    return {
        "auth_failed": coordinator.auth_failed,
        "webhook_active": coordinator.webhook_active,
        "features": {
            feature: coordinator.feature_available(feature)
            for feature in ("doors", "devices", "emergency")
        },
        "snapshot": async_redact_data(snapshot, ACCESS_TO_REDACT),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UnifiInsightsConfigEntry | CarrierFabricConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    _ = hass
    _LOGGER.debug("Gathering diagnostics data for UniFi Insights")

    if isinstance(entry.runtime_data, CarrierFabricData):
        carrier_data = entry.runtime_data
        carrier_coordinator = carrier_data.coordinator
        coord_data = carrier_coordinator.data

        summary = (
            coord_data.get("summary", {})
            if isinstance(coord_data.get("summary"), dict)
            else {}
        )

        last_exc = getattr(carrier_coordinator, "last_exception", None)
        last_exc_type = last_exc.__class__.__name__ if last_exc is not None else None

        # Only allowlisted keys are exported: a field the API adds later never
        # reaches a diagnostics file until it has been reviewed for privacy.
        exported_plans: dict[str, Any] = {
            plan_id: {
                key: value
                for key, value in plan_data.items()
                if key in SERVICE_PLAN_ALLOWLIST
            }
            for plan_id, plan_data in coord_data.get("service_plans", {}).items()
        }
        exported_subs: dict[str, Any] = {
            sub_id: async_redact_data(
                {
                    key: value
                    for key, value in sub_data.items()
                    if key in SUBSCRIBER_ALLOWLIST
                },
                CARRIER_REDACT_KEYS,
            )
            for sub_id, sub_data in coord_data.get("subscribers", {}).items()
        }

        return {
            "entry": async_redact_data(entry.as_dict(), TO_REDACT),
            "coordinator": {
                "last_update_success": getattr(
                    carrier_coordinator, "last_update_success", True
                ),
                "last_exception_type": last_exc_type,
            },
            "summary": summary,
            "data": {
                "org_id": coord_data.get("org_id"),
                "summary": summary,
                "service_plans": exported_plans,
                "subscribers": exported_subs,
            },
        }

    data = entry.runtime_data
    coordinator = data.coordinator

    # Get library version
    library_version = api_version

    # Get sanitized connection info
    connection_info = {
        "host": REDACTED,
        "network_client_connected": coordinator.network_client is not None,
        "protect_client_connected": coordinator.protect_client is not None,
        "innerspace_client_connected": (
            getattr(coordinator, "innerspace_client", None) is not None
            or getattr(data, "innerspace_client", None) is not None
        ),
    }

    # WS health signal (task 5): previously no way to tell "connected and
    # delivering" from "connected but silent" from "reconnect-looping" -
    # this is the first place an operator would look for that.
    protect_coordinator = data.protect_coordinator
    websocket_info = (
        protect_coordinator.websocket_health if protect_coordinator else None
    )

    # The Site Manager and InnerSpace snapshots contain identifiers and variable
    # nested fields. Build their summaries separately and exclude raw sections.
    facade_data = dict(coordinator.data)
    facade_data.pop("site_manager", None)
    # The read model repeats devices and stats as frozen records, which the
    # MAC pass cannot walk into; the raw sections already carry it all.
    facade_data.pop(NETWORK_FACTS_KEY, None)
    innerspace_snapshot = facade_data.pop("innerspace", None)
    innerspace_coord = getattr(data, "innerspace_coordinator", None)
    if not isinstance(innerspace_snapshot, Mapping) and innerspace_coord is not None:
        innerspace_snapshot = innerspace_coord.data
    diagnostics_data: dict[str, Any] = {
        "library_version": library_version,
        "connection": connection_info,
        "websocket": websocket_info,
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "data": _redact_coordinator_data(facade_data),
    }
    if isinstance(innerspace_snapshot, Mapping):
        diagnostics_data["innerspace"] = build_innerspace_diagnostics_summary(
            innerspace_snapshot,
            available=bool(getattr(coordinator, "innerspace_available", True)),
            redact_fn=async_redact_data,
            to_redact=TO_REDACT,
        )
    if data.site_manager_coordinator:
        diagnostics_data["site_manager"] = _site_manager_summary(
            data.site_manager_coordinator.data,
            entry.data.get(CONF_CONSOLE_ID, ""),
        )
    if data.mobility_coordinator:
        diagnostics_data["mobility"] = _mobility_summary(data.mobility_coordinator)
    elif entry.data.get(CONF_CONNECTION_TYPE) == CONNECTION_TYPE_REMOTE:
        # Another remote entry with the same cloud key polls Mobility.
        diagnostics_data["mobility"] = {"owner": False}
    access_coordinator = getattr(data, "access_coordinator", None)
    if access_coordinator is not None:
        diagnostics_data["access"] = _access_diagnostics(access_coordinator)

    # Last pass over the assembled payload: no MAC address leaves this
    # integration, whatever key the controller sent it under.
    anonymized: dict[str, Any] = _anonymize_macs(diagnostics_data, {})

    _LOGGER.debug("Diagnostics data collected successfully")
    return anonymized
