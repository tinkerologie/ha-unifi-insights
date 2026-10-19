# Copyright 2026 UniFi Insights contributors
"""Site configuration and legacy section helpers for UnifiInsightsConfigCoordinator."""

from __future__ import annotations

import inspect
import logging
from functools import partial
from typing import TYPE_CHECKING, Any, Final

from unifi_official_api import UniFiAuthenticationError
from unifi_official_api.network.models import is_predefined_nat_rule
from unifi_official_api.network.models.report import SiteReportBucket

from custom_components.unifi_insights.topology import normalize_mac

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from unifi_official_api.network import UniFiNetworkClient

_LOGGER = logging.getLogger(__name__)

SECTION_OUTCOME_OK: Final = "ok"
SECTION_OUTCOME_UNSUPPORTED: Final = "unsupported"
SECTION_OUTCOME_FAILED: Final = "failed"

__all__ = [
    "SECTION_OUTCOME_FAILED",
    "SECTION_OUTCOME_OK",
    "SECTION_OUTCOME_UNSUPPORTED",
    "async_fetch_site_firewall",
    "async_fetch_site_routes",
    "async_fetch_site_rule_section",
    "async_fetch_site_vouchers",
    "async_fetch_site_vpn_clients",
    "async_fetch_site_vpns",
    "async_fetch_site_wifi_and_links",
    "client_links",
    "enrich_wifi",
    "is_predefined_firewall_rule",
    "is_predefined_nat_rule",
    "map_legacy_site_names",
    "resolve_report_site_name",
    "rule_display_name",
    "verified_legacy_site_name",
    "wifi_qr_payload",
]


def map_legacy_site_names(
    integration_sites: dict[str, dict[str, Any]],
    legacy_sites: list[dict[str, Any]],
) -> dict[str, str]:
    """Map integration site IDs to classic ("legacy") site names."""

    def _norm(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip().lower()
        return stripped or None

    legacy: list[tuple[str, set[str]]] = []
    for site in legacy_sites:
        name = site.get("name")
        if not isinstance(name, str) or not name:
            continue
        candidates = {
            c
            for c in (
                _norm(name),
                _norm(site.get("desc")),
                _norm(site.get("description")),
            )
            if c is not None
        }
        legacy.append((name, candidates))

    mappings: dict[str, str] = {}
    for site_id, site_data in integration_sites.items():
        candidates = {
            c
            for c in (
                _norm(site_id),
                _norm(site_data.get("name")),
                _norm(site_data.get("description")),
                _norm(site_data.get("desc")),
            )
            if c is not None
        }
        for legacy_name, legacy_candidates in legacy:
            if candidates & legacy_candidates:
                mappings[site_id] = legacy_name
                break
        if site_id not in mappings and len(legacy) == 1:
            mappings[site_id] = legacy[0][0]
    return mappings


def wifi_qr_payload(
    ssid: str,
    passphrase: str | None,
    security: str | None,
    *,
    hidden: bool,
) -> str:
    """
    Build a standard ``WIFI:`` QR payload string.

    Follows the de-facto WiFi network config QR format consumed by phone
    cameras: ``WIFI:T:<auth>;S:<ssid>;P:<password>;H:<hidden>;;``.
    """

    def _escape(value: str) -> str:
        for char in ("\\", ";", ",", ":", '"'):
            value = value.replace(char, f"\\{char}")
        return value

    security_lower = (security or "").lower()
    if not passphrase or security_lower in ("", "open", "none"):
        auth = "nopass"
    elif "wep" in security_lower:
        auth = "WEP"
    else:
        auth = "WPA"

    parts = [f"T:{auth}", f"S:{_escape(ssid)}"]
    if auth != "nopass" and passphrase:
        parts.append(f"P:{_escape(passphrase)}")
    if hidden:
        parts.append("H:true")
    return "WIFI:" + ";".join(parts) + ";;"


def enrich_wifi(
    wifi_dict: dict[str, dict[str, Any]],
    legacy_configs: list[dict[str, Any]],
    active_clients: list[dict[str, Any]],
) -> None:
    """
    Add secrets, per-SSID client counts, and QR payloads to WiFi data.

    Secrets come from the classic ``/rest/wlanconf`` data (the official API
    redacts them); per-SSID counts are derived from active clients' essid.
    """
    configs_by_name = {
        config.get("name"): config for config in legacy_configs if config.get("name")
    }

    counts: dict[str, int] = {}
    for client in active_clients:
        if client.get("is_wired"):
            continue
        essid = client.get("essid")
        if essid:
            counts[essid] = counts.get(essid, 0) + 1

    for wifi in wifi_dict.values():
        ssid = wifi.get("name") or wifi.get("ssid")
        if not ssid:
            continue

        wifi["num_connected_clients"] = counts.get(ssid, 0)

        config = configs_by_name.get(ssid)
        if config is None:
            continue

        passphrase = config.get("x_passphrase")
        security = config.get("security")
        hidden = bool(config.get("hide_ssid"))
        wifi["ssid"] = ssid
        wifi["passphrase"] = passphrase
        wifi["security"] = security
        wifi["wpa_mode"] = config.get("wpa_mode")
        wifi["hide_ssid"] = hidden
        wifi["is_guest"] = config.get("is_guest", wifi.get("isGuest", False))
        wifi["qr_code"] = wifi_qr_payload(ssid, passphrase, security, hidden=hidden)


def client_links(active_clients: list[Any]) -> dict[str, dict[str, Any]]:
    """
    Extract each active client's switch port, AP, VLAN and network name.

    The v1 clients endpoint leaves swMac/swPort/apMac/vlan/networkId null
    on current firmware; the classic /stat/sta response (already fetched
    for per-SSID counts) carries them. Keyed by normalised client MAC so
    the topology builder can join it to v1 clients.
    """
    links: dict[str, dict[str, Any]] = {}
    for client in active_clients:
        if not isinstance(client, dict):
            continue
        client_mac = normalize_mac(client.get("mac"))
        if client_mac is None:
            continue
        link: dict[str, Any] = {}
        for key in ("sw_mac", "ap_mac"):
            mac = normalize_mac(client.get(key))
            if mac is not None:
                link[key] = mac
        sw_port = client.get("sw_port")
        if isinstance(sw_port, int) and not isinstance(sw_port, bool):
            link["sw_port"] = sw_port
        vlan = client.get("vlan")
        if isinstance(vlan, int) and not isinstance(vlan, bool) and vlan >= 1:
            link["vlan"] = vlan
        network_name = client.get("network")
        if isinstance(network_name, str) and network_name:
            link["network_name"] = network_name
        links[client_mac] = link
    return links


async def async_fetch_site_wifi_and_links(
    coordinator: Any,
    site_id: str,
    legacy_name: str | None,
    failed_sections: set[tuple[str, str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch WiFi networks, client links, and legacy WiFi enrichment for one site."""
    wifi_models = await coordinator._fetch_optional_section(
        "wifi", site_id, partial(coordinator.network_client.wifi.get_all, site_id)
    )
    wifi_dict: dict[str, Any] = {}
    for wifi_model in wifi_models or []:
        wifi = coordinator._model_to_dict(wifi_model)
        wifi_id = wifi.get("id")
        if wifi_id:
            wifi_dict[wifi_id] = wifi

    active_clients: list[Any] | None = None
    if legacy_name:
        try:
            active_clients = await coordinator.network_client.clients.get_active_legacy(
                legacy_name
            )
        except Exception as err:
            _LOGGER.debug(
                "Config coordinator: Unable to fetch active clients for site %s: %s",
                site_id,
                err,
            )
    site_links = (
        coordinator._client_links(active_clients)
        if active_clients is not None
        else coordinator.data["client_links"].get(site_id, {})
    )
    if wifi_models is not None and legacy_name and active_clients is not None:
        try:
            legacy_configs = await coordinator.network_client.wifi.get_legacy_configs(
                legacy_name
            )
            coordinator._enrich_wifi(wifi_dict, legacy_configs, active_clients)
        except Exception as err:
            _LOGGER.debug(
                "Config coordinator: Unable to enrich WiFi data for site %s: %s",
                site_id,
                err,
            )
    if wifi_models is None:
        failed_sections.add(("wifi", site_id))
        return coordinator.data["wifi"].get(site_id, {}), site_links
    return wifi_dict, site_links


async def async_fetch_site_firewall(
    coordinator: Any,
    site_id: str,
    failed_sections: set[tuple[str, str]],
    *,
    outcomes: dict[tuple[str, str], str] | None = None,
) -> dict[str, Any]:
    """Fetch firewall rules for one site."""
    firewall_models = await coordinator._fetch_optional_section(
        "firewall_rules",
        site_id,
        partial(coordinator.network_client.firewall.list_rules, site_id),
        outcomes=outcomes,
    )
    if firewall_models is None:
        failed_sections.add(("firewall_rules", site_id))
        return dict(coordinator.data["firewall_rules"].get(site_id, {}))
    firewall_rules_dict: dict[str, Any] = {}
    for firewall_model in firewall_models:
        firewall_rule = coordinator._model_to_dict(firewall_model)
        firewall_rule_id = firewall_rule.get("id")
        if firewall_rule_id:
            firewall_rules_dict[firewall_rule_id] = firewall_rule
    return firewall_rules_dict


async def async_fetch_site_vouchers(
    coordinator: Any,
    site_id: str,
    failed_sections: set[tuple[str, str]],
) -> dict[str, Any]:
    """Fetch the voucher inventory of one site, keyed by voucher id."""
    models = await coordinator._fetch_optional_section(
        "vouchers",
        site_id,
        partial(coordinator.network_client.vouchers.get_all_pages, site_id),
    )
    if models is None:
        failed_sections.add(("vouchers", site_id))
        return dict(coordinator.data.get("vouchers", {}).get(site_id, {}))
    inventory: dict[str, Any] = {}
    for model in models:
        record = coordinator._model_to_dict(model)
        if voucher_id := record.get("id"):
            inventory[voucher_id] = record
    return inventory


def verified_legacy_site_name(site_data: Any) -> str | None:
    """
    Return the verified classic site name (internalReference) for older APIs.

    Spec: internal unique name of the site used in older APIs; never fuzzy.
    """
    if isinstance(site_data, dict):
        val = site_data.get("internalReference")
        if isinstance(val, str) and (stripped := val.strip()):
            return stripped
    return None


def rule_display_name(
    rule_data: Any,
    rule_id: str,
    name_field: str = "name",
) -> str:
    """Return stripped display name for a rule or fall back to rule_id."""
    if isinstance(rule_data, dict):
        rule_name = rule_data.get(name_field)
        if isinstance(rule_name, str) and (stripped := rule_name.strip()):
            return stripped
    return rule_id


def is_predefined_firewall_rule(rule_data: dict[str, Any]) -> bool:
    """Return True when a firewall rule appears to be system-defined."""
    metadata = rule_data.get("metadata")
    if isinstance(metadata, dict) and metadata.get("origin") == "SYSTEM_DEFINED":
        return True
    return bool(
        rule_data.get("predefined")
        or rule_data.get("isPredefined")
        or rule_data.get("isSystem")
    )


async def async_fetch_site_rule_section(
    coordinator: Any,
    section: str,
    site_id: str,
    site_name: str | None,
    fetch: Callable[[str], Awaitable[list[Any]]],
    *,
    failed_sections: set[tuple[str, str]],
    outcomes: dict[tuple[str, str], str] | None = None,
) -> dict[str, Any]:
    """Fetch one rule section (port forwards, traffic rules, NAT, routes)."""
    if site_name is None:
        return {}
    models = await coordinator._fetch_optional_section(
        section, site_id, partial(fetch, site_name), outcomes=outcomes
    )
    if models is None:
        failed_sections.add((section, site_id))
        return dict(coordinator.data.get(section, {}).get(site_id, {}))
    rules_dict: dict[str, Any] = {}
    for model in models:
        record = coordinator._model_to_dict(model)
        rule_id = record.get("_id") or record.get("id")
        if isinstance(rule_id, str) and rule_id:
            rules_dict[rule_id] = record
    return rules_dict


async def async_fetch_site_routes(
    network_client: UniFiNetworkClient,
    model_to_dict: Callable[[Any], dict[str, Any]],
    site_id: str,
    legacy_name: str | None,
    *,
    outcomes: dict[tuple[str, str], str] | None = None,
) -> dict[str, Any]:
    """
    Fetch policy-based routes (traffic routes) for a site.

    ``outcomes`` records whether this fetch was authoritative. The returned
    data is unchanged by it: a failed fetch still yields ``{}``.
    """
    if not legacy_name:
        return {}
    try:
        _LOGGER.debug(
            "Config coordinator: Fetching policy-based routes for site %s (%s)",
            site_id,
            legacy_name,
        )
        route_models = await network_client.routes.list_routes(legacy_name)
        routes_dict: dict[str, Any] = {}
        for route_model in route_models:
            route = model_to_dict(route_model)
            route_id = route.get("id") or route.get("_id")
            if route_id:
                routes_dict[route_id] = route
        _LOGGER.debug(
            "Config coordinator: Successfully fetched %d policy-based routes "
            "for site %s",
            len(routes_dict),
            site_id,
        )
    except UniFiAuthenticationError:
        raise
    except Exception as err:
        _LOGGER.debug(
            "Config coordinator: Policy-based routes unavailable for site %s: %s",
            site_id,
            err,
        )
        if outcomes is not None:
            outcomes[("policy_based_routes", site_id)] = SECTION_OUTCOME_FAILED
        return {}
    if outcomes is not None:
        outcomes[("policy_based_routes", site_id)] = SECTION_OUTCOME_OK
    return routes_dict


async def async_fetch_site_vpn_clients(
    network_client: UniFiNetworkClient,
    model_to_dict: Callable[[Any], dict[str, Any]],
    site_id: str,
    legacy_name: str | None,
) -> dict[str, Any]:
    """Fetch VPN clients via the classic networkconf endpoint for a site."""
    if not legacy_name:
        return {}
    try:
        _LOGGER.debug(
            "Config coordinator: Fetching VPN clients for site %s (%s)",
            site_id,
            legacy_name,
        )
        vpn_client_models = await network_client.vpn_clients.list_vpn_clients(
            legacy_name
        )
        vpn_clients_dict: dict[str, Any] = {}
        for vpn_client_model in vpn_client_models:
            vpn_client = model_to_dict(vpn_client_model)
            vpn_client_id = vpn_client.get("id") or vpn_client.get("_id")
            if vpn_client_id:
                vpn_clients_dict[vpn_client_id] = vpn_client
        _LOGGER.debug(
            "Config coordinator: Successfully fetched %d VPN clients for site %s",
            len(vpn_clients_dict),
            site_id,
        )
    except UniFiAuthenticationError:
        raise
    except Exception as err:
        _LOGGER.debug(
            "Config coordinator: VPN clients unavailable for site %s: %s",
            site_id,
            err,
        )
        return {}
    return vpn_clients_dict


async def async_fetch_site_vpns(
    network_client: UniFiNetworkClient,
    site_id: str,
    legacy_name: str | None,
    *,
    legacy_mapping_failed: bool,
    prior_site_vpns: dict[str, Any],
) -> dict[str, Any]:
    """Fetch site-to-site VPN tunnels for a site."""
    if legacy_name:
        try:
            vpn_endpoint = network_client.vpn_clients
            tunnels = await vpn_endpoint.list_site_to_site_vpns(legacy_name)
        except UniFiAuthenticationError:
            raise
        except Exception as err:
            _LOGGER.debug(
                "Config coordinator: Site-to-site VPNs unavailable for site %s: %s",
                site_id,
                err,
            )
            return prior_site_vpns
        return {tunnel["id"]: tunnel for tunnel in tunnels}
    if legacy_mapping_failed:
        return prior_site_vpns
    return {}


# --- Internet activity reports -----------------------------------------------

# Historical site-level internet activity aggregation and fetching helpers.

# Historical Internet Activity time windows (milliseconds)
WINDOW_1H_MS = 60 * 60 * 1000
WINDOW_1D_MS = 24 * WINDOW_1H_MS
WINDOW_1W_MS = 7 * WINDOW_1D_MS
WINDOW_1M_MS = 30 * WINDOW_1D_MS


def _sum_report_window(
    buckets: Sequence[SiteReportBucket | dict[str, Any] | object],
    cutoff_ms: int,
) -> dict[str, int] | None:
    """Sum download (RX) and upload (TX) bytes for buckets at or after ``cutoff_ms``."""
    rx_values: list[int] = []
    tx_values: list[int] = []

    for item in buckets:
        if isinstance(item, SiteReportBucket):
            bucket = item
        elif isinstance(item, dict):
            try:
                bucket = SiteReportBucket.model_validate(item)
            except Exception as err:
                _LOGGER.debug("Skipping invalid site report bucket: %s", err)
                continue
        else:
            continue

        if bucket.time < cutoff_ms:
            continue
        if bucket.rx_bytes is not None:
            rx_values.append(bucket.rx_bytes)
        if bucket.tx_bytes is not None:
            tx_values.append(bucket.tx_bytes)

    if not rx_values and not tx_values:
        return None

    window_totals: dict[str, int] = {}
    if rx_values:
        window_totals["rx_bytes"] = sum(rx_values)
    if tx_values:
        window_totals["tx_bytes"] = sum(tx_values)
    return window_totals


def aggregate_internet_activity_windows(
    five_minute_buckets: Sequence[SiteReportBucket | dict[str, Any]],
    hourly_buckets: Sequence[SiteReportBucket | dict[str, Any]],
    daily_buckets: Sequence[SiteReportBucket | dict[str, Any]],
    *,
    now_ms: int,
) -> dict[str, dict[str, int]]:
    """
    Aggregate site report buckets into ``1h``, ``1d``, ``1w``, and ``1m`` totals.

    - ``1h`` uses ``5minutes`` buckets with ``time >= now_ms - 1h``.
    - ``1d`` and ``1w`` reuse ``hourly`` buckets with ``1d`` and ``7d`` cutoffs.
    - ``1m`` uses ``daily`` buckets with ``time >= now_ms - 30d``.
    Windows without usable buckets are omitted rather than publishing zero.
    """
    windows: dict[str, dict[str, int]] = {}
    for window_key, source_buckets, duration_ms in (
        ("1h", five_minute_buckets, WINDOW_1H_MS),
        ("1d", hourly_buckets, WINDOW_1D_MS),
        ("1w", hourly_buckets, WINDOW_1W_MS),
        ("1m", daily_buckets, WINDOW_1M_MS),
    ):
        totals = _sum_report_window(source_buckets, now_ms - duration_ms)
        if totals is not None:
            windows[window_key] = totals
    return windows


async def async_call_site_report(
    network_client: Any,
    site_name: str,
    interval: str,
    *,
    start_ms: int,
    end_ms: int,
) -> list[Any]:
    """Invoke the Network client's legacy site report endpoint for one interval."""
    reports_obj = getattr(network_client, "reports", None)
    target_fn = getattr(reports_obj, "get_site_report", None)
    if not callable(target_fn):
        target_fn = getattr(network_client, "get_site_report", None)
    if not callable(target_fn):
        return []

    result = target_fn(
        site_name,
        interval,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    if inspect.isawaitable(result):
        result = await result
    return list(result) if isinstance(result, (list, tuple)) else []


async def async_fetch_site_internet_activity(
    fetch_optional_section: Callable[
        [str, str, Callable[[], Awaitable[list[Any]]]],
        Awaitable[list[Any] | None],
    ],
    call_site_report: Callable[..., Awaitable[list[Any]]],
    site_id: str,
    site_name: str,
    now_ms: int,
) -> dict[str, dict[str, int]] | None:
    """Fetch ``5minutes``, ``hourly``, and ``daily`` site reports and aggregate."""
    five_min_buckets = await fetch_optional_section(
        "internet_activity",
        site_id,
        partial(
            call_site_report,
            site_name,
            "5minutes",
            start_ms=now_ms - WINDOW_1H_MS,
            end_ms=now_ms,
        ),
    )
    if five_min_buckets is None:
        return None

    hourly_buckets = await fetch_optional_section(
        "internet_activity",
        site_id,
        partial(
            call_site_report,
            site_name,
            "hourly",
            start_ms=now_ms - WINDOW_1W_MS,
            end_ms=now_ms,
        ),
    )
    if hourly_buckets is None:
        return None

    daily_buckets = await fetch_optional_section(
        "internet_activity",
        site_id,
        partial(
            call_site_report,
            site_name,
            "daily",
            start_ms=now_ms - WINDOW_1M_MS,
            end_ms=now_ms,
        ),
    )
    if daily_buckets is None:
        return None

    return aggregate_internet_activity_windows(
        five_min_buckets,
        hourly_buckets,
        daily_buckets,
        now_ms=now_ms,
    )


def resolve_report_site_name(
    site_id: str,
    site_data: Any,
    legacy_name: str | None,
) -> str:
    """Resolve the classic site name used for ``/stat/report/*.site`` requests."""
    if legacy_name:
        return str(legacy_name)
    if isinstance(site_data, dict):
        internal_ref = site_data.get("internal_reference") or site_data.get(
            "internalReference"
        )
        if internal_ref:
            return str(internal_ref)
    return str(site_id)


async def async_update_site_internet_activity(
    fetch_site_internet_activity: Callable[
        ..., Awaitable[dict[str, dict[str, int]] | None]
    ],
    *,
    site_id: str,
    site_data: Any,
    legacy_name: str | None,
    now_ms: int,
    prior_activity: Any,
    internet_activity_by_site: dict[str, dict[str, dict[str, int]]],
    failed_sections: set[tuple[str, str]],
) -> None:
    """Refresh internet activity for one site and update coordinator state dicts."""
    report_site_name = resolve_report_site_name(site_id, site_data, legacy_name)
    site_activity = await fetch_site_internet_activity(
        site_id=site_id,
        site_name=report_site_name,
        now_ms=now_ms,
    )
    if site_activity is None:
        failed_sections.add(("internet_activity", site_id))
        if isinstance(prior_activity, dict) and prior_activity:
            internet_activity_by_site[site_id] = prior_activity
    elif site_activity:
        internet_activity_by_site[site_id] = site_activity
