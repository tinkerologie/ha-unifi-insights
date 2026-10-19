# Copyright 2026 UniFi Insights contributors
"""Config coordinator for UniFi Insights - handles slow-changing configuration data."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiNotFoundError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from unifi_official_api.validation import sanitized_validation_fields

from custom_components.unifi_insights.const import CONF_SITE_IDS, SCAN_INTERVAL_CONFIG

from .base import UnifiBaseCoordinator
from .config_sections import (
    SECTION_OUTCOME_FAILED,
    SECTION_OUTCOME_OK,
    SECTION_OUTCOME_UNSUPPORTED,
    async_fetch_site_firewall,
    async_fetch_site_routes,
    async_fetch_site_rule_section,
    async_fetch_site_vouchers,
    async_fetch_site_vpn_clients,
    async_fetch_site_vpns,
    async_fetch_site_wifi_and_links,
    client_links,
    enrich_wifi,
    map_legacy_site_names,
    verified_legacy_site_name,
    wifi_qr_payload,
)
from .internet_activity import (
    WINDOW_1D_MS,
    WINDOW_1H_MS,
    WINDOW_1M_MS,
    WINDOW_1W_MS,
    aggregate_internet_activity_windows,
    async_call_site_report,
    async_fetch_site_internet_activity,
    async_update_site_internet_activity,
)
from .rule_audit import RuleAuditTracker

__all__ = [
    "WINDOW_1D_MS",
    "WINDOW_1H_MS",
    "WINDOW_1M_MS",
    "WINDOW_1W_MS",
    "UnifiConfigCoordinator",
    "aggregate_internet_activity_windows",
]

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from unifi_official_api.network import UniFiNetworkClient
    from unifi_official_api.protect import UniFiProtectClient

_LOGGER = logging.getLogger(__name__)

# Rule sections fetched through async_fetch_site_rule_section, in fetch order.
_RULE_SECTIONS: tuple[str, ...] = (
    "port_forwards",
    "traffic_rules",
    "nat_rules",
    "static_routes",
)


class UnifiConfigCoordinator(UnifiBaseCoordinator):
    """Coordinator for slow-changing configuration data (5 minute updates)."""

    _map_legacy_site_names = staticmethod(map_legacy_site_names)
    _wifi_qr_payload = staticmethod(wifi_qr_payload)
    _enrich_wifi = staticmethod(enrich_wifi)
    _client_links = staticmethod(client_links)

    def __init__(
        self,
        hass: HomeAssistant,
        network_client: UniFiNetworkClient,
        protect_client: UniFiProtectClient | None,
        entry: ConfigEntry,
        *,
        network_available: bool = True,
        innerspace_available: bool = False,
    ) -> None:
        """Initialize the config coordinator."""
        super().__init__(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=entry,
            name="config",
            update_interval=SCAN_INTERVAL_CONFIG,
        )
        self._network_available = network_available
        self._innerspace_available = innerspace_available
        self.data: dict[str, Any] = {
            "sites": {},
            "wifi": {},
            "firewall_rules": {},
            "vouchers": {},
            "policy_based_routes": {},
            "vpn_clients": {},
            "port_forwards": {},
            "traffic_rules": {},
            "nat_rules": {},
            "static_routes": {},
            "site_vpns": {},
            "network_info": {},
            "client_links": {},
            "internet_activity": {},
            "internet_activity_unavailable": set(),
            "rule_changes": {},
        }
        self._rule_audit = RuleAuditTracker()
        self.available_sites: dict[str, str] = {}
        self._warned_no_selected_sites = False
        self._failed_sections: set[tuple[str, str]] = set()
        self._voucher_site_request_seq: dict[str, int] = {}
        self._voucher_site_committed_seq: dict[str, int] = {}

    def _section_available(self, section: str, site_id: str) -> bool:
        """Return True if the last refresh fetched the given section for a site."""
        return self.last_update_success and (section, site_id) not in (
            self._failed_sections
        )

    @property
    def network_available(self) -> bool:
        """Return True if the Network application is available on the console."""
        return self._network_available

    def wifi_available(self, site_id: str) -> bool:
        """Return True if the last refresh fetched WiFi networks for a site."""
        return self._section_available("wifi", site_id)

    def firewall_available(self, site_id: str) -> bool:
        """Return True if the last refresh fetched firewall rules for a site."""
        return self._section_available("firewall_rules", site_id)

    def vouchers_available(self, site_id: str) -> bool:
        """Return True if the last refresh fetched hotspot vouchers for a site."""
        return self._section_available("vouchers", site_id)

    def port_forwards_available(self, site_id: str) -> bool:
        """Return True if the last refresh fetched port forwards for a site."""
        return self._section_available("port_forwards", site_id)

    def traffic_rules_available(self, site_id: str) -> bool:
        """Return True if the last refresh fetched traffic rules for a site."""
        return self._section_available("traffic_rules", site_id)

    def nat_rules_available(self, site_id: str) -> bool:
        """Return True if the last refresh fetched NAT rules for a site."""
        return self._section_available("nat_rules", site_id)

    def static_routes_available(self, site_id: str) -> bool:
        """Return True if the last refresh fetched static routes for a site."""
        return self._section_available("static_routes", site_id)

    def internet_activity_available(self, site_id: str) -> bool:
        """Return True if the last refresh fetched internet activity for a site."""
        return self._section_available("internet_activity", site_id)

    async def _fetch_optional_section(
        self,
        section: str,
        site_id: str,
        fetch: Callable[[], Awaitable[list[Any]]],
        *,
        outcomes: dict[tuple[str, str], str] | None = None,
    ) -> list[Any] | None:
        """
        Fetch an optional per-site section (WiFi, firewall, or site reports).

        ``outcomes``, when given, records how the fetch ended (``ok``,
        ``unsupported`` or ``failed``) under ``(section, site_id)``. The return
        value alone cannot tell an empty list from an unsupported endpoint.
        """
        if outcomes is None:
            outcomes = {}
        try:
            result = await fetch()
        except UniFiAuthenticationError as err:
            if not self._is_unsupported_response(err):
                raise
            unsupported: Exception = err
        except Exception as err:
            if not self._is_unsupported_response(err):
                log = (
                    _LOGGER.debug
                    if (section, site_id) in self._failed_sections
                    else _LOGGER.warning
                )
                if isinstance(err, ValidationError):
                    err_msg: Any = (
                        f"ValidationError(fields: {sanitized_validation_fields(err)})"
                    )
                else:
                    err_msg = err
                log(
                    "Config coordinator: Unable to fetch %s for site %s, keeping "
                    "the last known data: %s",
                    section,
                    site_id,
                    err_msg,
                )
                outcomes[(section, site_id)] = SECTION_OUTCOME_FAILED
                return None
            unsupported = err
        else:
            outcomes[(section, site_id)] = SECTION_OUTCOME_OK
            return result
        _LOGGER.debug(
            "Config coordinator: %s not available for site %s: %s",
            section,
            site_id,
            unsupported,
        )
        outcomes[(section, site_id)] = SECTION_OUTCOME_UNSUPPORTED
        return []

    async def _call_site_report(
        self,
        site_name: str,
        interval: str,
        *,
        start_ms: int,
        end_ms: int,
    ) -> list[Any]:
        """Invoke the Network client site report method for one interval."""
        return await async_call_site_report(
            self.network_client, site_name, interval, start_ms=start_ms, end_ms=end_ms
        )

    async def _fetch_site_internet_activity(
        self,
        *,
        site_id: str,
        site_name: str,
        now_ms: int,
    ) -> dict[str, dict[str, int]] | None:
        """Fetch ``5minutes``, ``hourly``, and ``daily`` site reports and aggregate."""
        return await async_fetch_site_internet_activity(
            self._fetch_optional_section,
            self._call_site_report,
            site_id=site_id,
            site_name=site_name,
            now_ms=now_ms,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch configuration data from API."""
        try:
            sites_models = []
            if self._network_available:
                other_app_available = (
                    self.protect_client is not None or self._innerspace_available
                )
                try:
                    sites_models = await self.network_client.sites.get_all()
                except (UniFiNotFoundError, UniFiAuthenticationError) as err:
                    if other_app_available:
                        self._network_available = False
                        _LOGGER.debug(
                            "Config coordinator: Network API not available: %s", err
                        )
                    else:
                        raise
                except UniFiResponseError as err:
                    if not other_app_available or err.status_code != HTTPStatus.OK:
                        raise
                    self._network_available = False
                    _LOGGER.debug(
                        "Config coordinator: sites endpoint returned non-JSON "
                        "(status %s)",
                        err.status_code,
                    )

            site_list = [self._model_to_dict(s) for s in sites_models]
            all_sites: dict[str, dict[str, Any]] = {
                site["id"]: site for site in site_list if site.get("id")
            }
            self.available_sites = {
                site_id: str(
                    site.get("name") or site.get("internalReference") or site_id
                )
                for site_id, site in all_sites.items()
            }
            sites = self._filter_selected_sites(all_sites)
            self.data["sites"] = sites

            if not sites:
                self.data.update(
                    wifi={},
                    firewall_rules={},
                    vouchers={},
                    policy_based_routes={},
                    vpn_clients={},
                    port_forwards={},
                    traffic_rules={},
                    nat_rules={},
                    static_routes={},
                    site_vpns={},
                    network_info={},
                    client_links={},
                    internet_activity={},
                    internet_activity_unavailable=set(),
                    last_update=datetime.now(tz=UTC),
                )
                self._update_rule_changes({})
                self._available = True
                return self.data

            for key in (
                "wifi",
                "firewall_rules",
                "vouchers",
                "policy_based_routes",
                "vpn_clients",
                "port_forwards",
                "traffic_rules",
                "nat_rules",
                "static_routes",
                "site_vpns",
            ):
                self.data[key] = {
                    k: v for k, v in self.data[key].items() if k in self.data["sites"]
                }

            legacy_site_names: dict[str, str] = {}
            legacy_mapping_failed = False
            try:
                legacy_sites = await self.network_client.sites.get_legacy_all()
                legacy_site_names = self._map_legacy_site_names(sites, legacy_sites)
            except Exception as err:
                legacy_mapping_failed = True
                _LOGGER.debug(
                    "Config coordinator: Unable to fetch legacy site mapping: %s", err
                )

            wifi_by_site: dict[str, dict[str, Any]] = {}
            firewall_by_site: dict[str, dict[str, Any]] = {}
            vouchers_by_site: dict[str, tuple[int, dict[str, Any]]] = {}
            internet_activity_by_site: dict[str, dict[str, dict[str, int]]] = {}
            failed_sections: set[tuple[str, str]] = set()
            section_outcomes: dict[tuple[str, str], str] = {}
            routes_by_site: dict[str, dict[str, Any]] = {}
            vpn_clients_by_site: dict[str, dict[str, Any]] = {}
            rule_sections_by_site: dict[str, dict[str, dict[str, Any]]] = {
                section: {} for section in _RULE_SECTIONS
            }
            site_vpns_by_site: dict[str, dict[str, Any]] = {}
            client_links_by_site: dict[str, dict[str, Any]] = {}
            now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)

            for site_id in sites:
                legacy_name = legacy_site_names.get(site_id)
                (
                    wifi_by_site[site_id],
                    client_links_by_site[site_id],
                ) = await async_fetch_site_wifi_and_links(
                    self, site_id, legacy_name, failed_sections
                )
                firewall_by_site[site_id] = await async_fetch_site_firewall(
                    self, site_id, failed_sections, outcomes=section_outcomes
                )
                v_seq = self._voucher_site_request_seq.get(site_id, 0) + 1
                self._voucher_site_request_seq[site_id] = v_seq
                vouchers_by_site[site_id] = (
                    v_seq,
                    await async_fetch_site_vouchers(self, site_id, failed_sections),
                )
                await async_update_site_internet_activity(
                    self._fetch_site_internet_activity,
                    site_id=site_id,
                    site_data=sites.get(site_id),
                    legacy_name=legacy_name,
                    now_ms=now_ms,
                    prior_activity=self.data.get("internet_activity", {}).get(site_id),
                    internet_activity_by_site=internet_activity_by_site,
                    failed_sections=failed_sections,
                )
                routes_by_site[site_id] = await async_fetch_site_routes(
                    self.network_client,
                    self._model_to_dict,
                    site_id,
                    legacy_name,
                    outcomes=section_outcomes,
                )
                vpn_clients_by_site[site_id] = await async_fetch_site_vpn_clients(
                    self.network_client, self._model_to_dict, site_id, legacy_name
                )
                site_vpns_by_site[site_id] = await async_fetch_site_vpns(
                    self.network_client,
                    site_id,
                    legacy_name,
                    legacy_mapping_failed=legacy_mapping_failed,
                    prior_site_vpns=self.data.get("site_vpns", {}).get(site_id, {}),
                )
                rule_site_name = verified_legacy_site_name(sites.get(site_id))
                rule_fetchers: tuple[Callable[[str], Awaitable[list[Any]]], ...] = (
                    self.network_client.port_forwards.list_port_forwards,
                    self.network_client.traffic_rules.list_traffic_rules,
                    self.network_client.nat_rules.list_nat_rules,
                    self.network_client.static_routes.list_static_routes,
                )
                rule_results: list[
                    dict[str, Any] | BaseException
                ] = await asyncio.gather(
                    *(
                        async_fetch_site_rule_section(
                            self,
                            section,
                            site_id,
                            rule_site_name,
                            fetch,
                            failed_sections=failed_sections,
                            outcomes=section_outcomes,
                        )
                        for section, fetch in zip(
                            _RULE_SECTIONS, rule_fetchers, strict=True
                        )
                    ),
                    return_exceptions=True,
                )
                for section, rule_result in zip(
                    _RULE_SECTIONS, rule_results, strict=True
                ):
                    if isinstance(rule_result, BaseException):
                        raise rule_result
                    rule_sections_by_site[section][site_id] = rule_result

            committed_vouchers = dict(self.data.get("vouchers", {}))
            for site_id, (v_seq, inv) in vouchers_by_site.items():
                if v_seq >= self._voucher_site_committed_seq.get(site_id, 0):
                    self._voucher_site_committed_seq[site_id] = v_seq
                    committed_vouchers[site_id] = inv
                elif ("vouchers", site_id) in self._failed_sections:
                    failed_sections.add(("vouchers", site_id))
                else:
                    failed_sections.discard(("vouchers", site_id))

            self.data.update(
                sites=sites,
                wifi=wifi_by_site,
                firewall_rules=firewall_by_site,
                vouchers=committed_vouchers,
                internet_activity=internet_activity_by_site,
                internet_activity_unavailable={
                    s for sec, s in failed_sections if sec == "internet_activity"
                },
                policy_based_routes=routes_by_site,
                vpn_clients=vpn_clients_by_site,
                **rule_sections_by_site,
                site_vpns=site_vpns_by_site,
                client_links=client_links_by_site,
            )
            for section, site_id in self._failed_sections - failed_sections:
                _LOGGER.info(
                    "Config coordinator: %s fetch for site %s recovered",
                    section,
                    site_id,
                )
            self._failed_sections = failed_sections
            self._update_rule_changes(section_outcomes)
            self._available = True
            return self.data

        except UniFiAuthenticationError as err:
            self._handle_auth_error(err)
        except UniFiConnectionError as err:
            self._handle_connection_error(err)
        except UniFiTimeoutError as err:
            self._handle_timeout_error(err)
        except UniFiResponseError as err:
            self._handle_response_error(err)
        except Exception as err:
            self._handle_generic_error(err)

        return self.data  # pragma: no cover

    def _update_rule_changes(self, outcomes: dict[tuple[str, str], str]) -> None:
        """
        Diff the rule sections against their last authoritative snapshot.

        Runs only after a refresh has committed its data. A bug in the audit
        must never fail the refresh that every rule switch depends on, so any
        error is logged and the previous change list is kept.
        """
        try:
            self.data["rule_changes"] = self._rule_audit.process(self.data, outcomes)
        except Exception:
            _LOGGER.exception("Config coordinator: rule change detection failed")

    async def async_refresh_vouchers(self, site_id: str) -> None:
        """Re-fetch one site's voucher inventory without full config refresh."""
        if site_id not in self.data.get("sites", {}):
            return
        v_seq = self._voucher_site_request_seq.get(site_id, 0) + 1
        self._voucher_site_request_seq[site_id] = v_seq
        failed: set[tuple[str, str]] = set()
        inventory = await async_fetch_site_vouchers(self, site_id, failed)
        if v_seq >= self._voucher_site_committed_seq.get(site_id, 0):
            self._voucher_site_committed_seq[site_id] = v_seq
            self.data["vouchers"] = {
                **self.data.get("vouchers", {}),
                site_id: inventory,
            }
            self._failed_sections.discard(("vouchers", site_id))
            self._failed_sections |= failed
            self.async_update_listeners()

    def get_site(self, site_id: str) -> dict[str, Any] | None:
        """Get site data by site ID."""
        sites = self.data.get("sites", {})
        result = sites.get(site_id)
        return result if isinstance(result, dict) else None

    def _filter_selected_sites(
        self, sites: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Narrow sites to the ones selected in options."""
        selected = self.config_entry.options.get(CONF_SITE_IDS)
        if not selected or not sites:
            return sites
        filtered = {
            site_id: site for site_id, site in sites.items() if site_id in selected
        }
        if filtered:
            self._warned_no_selected_sites = False
        elif not self._warned_no_selected_sites:
            self._warned_no_selected_sites = True
            _LOGGER.warning(
                "Config coordinator: none of the selected sites (%s) exist on "
                "the console any more; re-select sites in the integration options",
                ", ".join(selected),
            )
        return filtered

    def get_site_ids(self) -> list[str]:
        """Get all site IDs."""
        return list(self.data.get("sites", {}).keys())

    def get_wifi_networks(self, site_id: str) -> dict[str, Any]:
        """Get WiFi networks for a site."""
        return dict(self.data.get("wifi", {}).get(site_id, {}))

    def get_firewall_rules(self, site_id: str) -> dict[str, Any]:
        """Get firewall rules for a site."""
        return dict(self.data.get("firewall_rules", {}).get(site_id, {}))

    def get_policy_based_routes(self, site_id: str) -> dict[str, Any]:
        """Get policy-based routes for a site."""
        return dict(self.data.get("policy_based_routes", {}).get(site_id, {}))

    def get_vpn_clients(self, site_id: str) -> dict[str, Any]:
        """Get VPN clients for a site."""
        return dict(self.data.get("vpn_clients", {}).get(site_id, {}))
