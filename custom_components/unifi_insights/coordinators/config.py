# Copyright 2026 UniFi Insights contributors
"""Config coordinator for UniFi Insights - handles slow-changing configuration data."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Final

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
from pydantic import ValidationError
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiError,
    UniFiNotFoundError,
    UniFiResponseError,
    UniFiRuleNotEditableError,
    UniFiTimeoutError,
)
from unifi_official_api.validation import sanitized_validation_fields

from custom_components.unifi_insights.const import (
    CONF_SITE_IDS,
    DOMAIN,
    SCAN_INTERVAL_CONFIG,
)
from custom_components.unifi_insights.data_transforms import (
    VoucherSettings,
    refresh_latest_voucher,
    voucher_to_record,
)

from .base import CommandFailure, UnifiInsightsBaseCoordinator, async_execute_command
from .config_sections import (
    SECTION_OUTCOME_FAILED,
    SECTION_OUTCOME_OK,
    SECTION_OUTCOME_UNSUPPORTED,
    WINDOW_1D_MS,
    WINDOW_1H_MS,
    WINDOW_1M_MS,
    WINDOW_1W_MS,
    aggregate_internet_activity_windows,
    async_call_site_report,
    async_fetch_site_firewall,
    async_fetch_site_internet_activity,
    async_fetch_site_routes,
    async_fetch_site_rule_section,
    async_fetch_site_vouchers,
    async_fetch_site_vpn_clients,
    async_fetch_site_vpns,
    async_fetch_site_wifi_and_links,
    async_update_site_internet_activity,
    client_links,
    enrich_wifi,
    map_legacy_site_names,
    rule_display_name,
    verified_legacy_site_name,
    wifi_qr_payload,
)
from .rule_audit import RuleAuditTracker

__all__ = [
    "WINDOW_1D_MS",
    "WINDOW_1H_MS",
    "WINDOW_1M_MS",
    "WINDOW_1W_MS",
    "UnifiInsightsConfigCoordinator",
    "aggregate_internet_activity_windows",
]

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import CALLBACK_TYPE, HomeAssistant
    from unifi_official_api.network import UniFiNetworkClient
    from unifi_official_api.network.models.voucher import Voucher
    from unifi_official_api.protect import UniFiProtectClient

_LOGGER = logging.getLogger(__name__)

# Rule sections fetched through async_fetch_site_rule_section, in fetch order.
_RULE_SECTIONS: tuple[str, ...] = (
    "port_forwards",
    "traffic_rules",
    "nat_rules",
    "static_routes",
)

# A console that is still applying a rule change can reject the list request
# for a few seconds (seen right after static route writes on a UDM). Seconds
# to wait before reading the rules one more time.
RULE_REFRESH_RETRY_DELAY: Final = 15

# Minimum seconds between two on-demand legacy site lookups, so a classic API
# that is down is not asked again on every write.
LEGACY_REMAP_COOLDOWN: Final = 60


def refreshed_latest_vouchers(
    latest: Mapping[str, dict[str, Any]],
    inventory: object,
    site_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """
    Return the latest vouchers with the given sites refreshed from inventory.

    A site whose inventory is missing or malformed keeps its record, so a
    voucher generated in Home Assistant outlives a deselected site or a
    failed fetch. Records of other sites are shared, not copied.
    """
    refreshed = dict(latest)
    if not isinstance(inventory, Mapping):
        return refreshed
    for site_id in site_ids:
        record = refreshed.get(site_id)
        site_inventory = inventory.get(site_id)
        if record is not None and isinstance(site_inventory, Mapping):
            refreshed[site_id] = refresh_latest_voucher(record, site_inventory)
    return refreshed


class UnifiInsightsConfigCoordinator(UnifiInsightsBaseCoordinator):
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
            # Site ID -> the last voucher generated in Home Assistant,
            # refreshed from each committed inventory (ADR-013).
            "latest_vouchers": {},
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
            # Integration site ID -> classic site name, published for every
            # owner that needs it (ADR-010). Last-good on lookup failure.
            "legacy_site_names": {},
            "legacy_mapping_failed": False,
        }
        self._rule_audit = RuleAuditTracker()
        self.available_sites: dict[str, str] = {}
        self._warned_no_selected_sites = False
        self._failed_sections: set[tuple[str, str]] = set()
        self._voucher_site_request_seq: dict[str, int] = {}
        self._voucher_site_committed_seq: dict[str, int] = {}
        # Per-site settings for the next generated voucher, replaced, never
        # changed in place.
        self._voucher_settings: dict[str, VoucherSettings] = {}
        # Pending one-shot rule refresh retries, keyed by (section, site_id).
        self._rule_refresh_retries: dict[tuple[str, str], CALLBACK_TYPE] = {}
        # One on-demand legacy site lookup at a time, at most once a cooldown.
        self._legacy_remap_lock = asyncio.Lock()
        self._legacy_remap_at: float | None = None

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

            if not sites:
                cleared: dict[str, Any] = {
                    **self.data,
                    "sites": sites,
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
                    "legacy_site_names": {},
                    "legacy_mapping_failed": False,
                    "last_update": datetime.now(tz=UTC),
                }
                cleared["rule_changes"] = self._rule_changes(cleared, {})
                self.data = cleared
                self._available = True
                return self.data

            # Nothing is published until every section is fetched: the new
            # sites and sections are kept local and replace the data root
            # once, after the last await (see below). The facade passes the
            # published sections on by reference, so a rebuild during this
            # refresh keeps seeing the previous, consistent set.
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
            # The section fetches below use only this cycle's mapping (empty
            # after a failed lookup); the published mapping keeps the last
            # good names of the still-selected sites for command owners and
            # device telemetry.
            published_legacy_site_names = (
                {
                    site_id: name
                    for site_id, name in self.data.get("legacy_site_names", {}).items()
                    if site_id in sites
                }
                if legacy_mapping_failed
                else dict(legacy_site_names)
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

            # Read after the last await, so an inventory a targeted voucher
            # refresh published meanwhile is kept for its site; deselected
            # sites are dropped.
            committed_vouchers = {
                site_id: inventory
                for site_id, inventory in self.data.get("vouchers", {}).items()
                if site_id in sites
            }
            for site_id, (v_seq, inv) in vouchers_by_site.items():
                if v_seq >= self._voucher_site_committed_seq.get(site_id, 0):
                    self._voucher_site_committed_seq[site_id] = v_seq
                    committed_vouchers[site_id] = inv
                elif ("vouchers", site_id) in self._failed_sections:
                    failed_sections.add(("vouchers", site_id))
                else:
                    failed_sections.discard(("vouchers", site_id))

            committed: dict[str, Any] = {
                **self.data,
                "sites": sites,
                "wifi": wifi_by_site,
                "firewall_rules": firewall_by_site,
                "vouchers": committed_vouchers,
                "latest_vouchers": refreshed_latest_vouchers(
                    self.data.get("latest_vouchers", {}),
                    committed_vouchers,
                    committed_vouchers,
                ),
                "internet_activity": internet_activity_by_site,
                "internet_activity_unavailable": {
                    s for sec, s in failed_sections if sec == "internet_activity"
                },
                "policy_based_routes": routes_by_site,
                "vpn_clients": vpn_clients_by_site,
                **rule_sections_by_site,
                "site_vpns": site_vpns_by_site,
                "client_links": client_links_by_site,
                "legacy_site_names": published_legacy_site_names,
                "legacy_mapping_failed": legacy_mapping_failed,
            }
            for section, site_id in self._failed_sections - failed_sections:
                _LOGGER.info(
                    "Config coordinator: %s fetch for site %s recovered",
                    section,
                    site_id,
                )
            self._failed_sections = failed_sections
            committed["rule_changes"] = self._rule_changes(committed, section_outcomes)
            self.data = committed
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

    def _rule_changes(
        self, data: Mapping[str, Any], outcomes: dict[tuple[str, str], str]
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Diff the rule sections against their last authoritative snapshot.

        Runs on the data a refresh is about to commit. A bug in the audit
        must never fail the refresh that every rule switch depends on, so any
        error is logged and the previous change list is kept.
        """
        try:
            return self._rule_audit.process(data, outcomes)
        except Exception:
            _LOGGER.exception("Config coordinator: rule change detection failed")
            previous: dict[str, list[dict[str, Any]]] = self.data.get(
                "rule_changes", {}
            )
            return previous

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
            vouchers = {**self.data.get("vouchers", {}), site_id: inventory}
            self.data = {
                **self.data,
                "vouchers": vouchers,
                "latest_vouchers": refreshed_latest_vouchers(
                    self.data.get("latest_vouchers", {}), vouchers, (site_id,)
                ),
            }
            self._failed_sections.discard(("vouchers", site_id))
            self._failed_sections |= failed
            self.async_update_listeners()

    def get_voucher_settings(self, site_id: str) -> VoucherSettings:
        """Return a site's settings for the next voucher, the defaults at first."""
        return self._voucher_settings.setdefault(site_id, VoucherSettings())

    def set_voucher_setting(self, site_id: str, field: str, value: float) -> None:
        """
        Replace a site's voucher settings with one field changed.

        The settings are kept in Home Assistant only and are read when a
        voucher is generated, so listeners are not notified; the number
        entity that set the value writes its own state.
        """
        # Any: the field picks the type; cast_input already rounds an
        # integer field to an int.
        changes: dict[str, Any] = {field: value}
        self._voucher_settings[site_id] = replace(
            self.get_voucher_settings(site_id), **changes
        )

    def _publish_latest_voucher(
        self, site_id: str, record: dict[str, Any] | None
    ) -> None:
        """
        Publish a site's latest voucher copy-on-write, or drop it for None.

        Listeners are notified by the targeted voucher refresh that follows.
        """
        latest = dict(self.data.get("latest_vouchers", {}))
        if record is None:
            latest.pop(site_id, None)
        else:
            latest[site_id] = record
            latest = refreshed_latest_vouchers(
                latest, self.data.get("vouchers", {}), (site_id,)
            )
        self.data = {**self.data, "latest_vouchers": latest}

    async def async_generate_voucher(
        self,
        site_id: str,
        *,
        name: str,
        time_limit_minutes: int,
        count: int = 1,
        tx_rate_limit_kbps: int | None = None,
        rx_rate_limit_kbps: int | None = None,
        data_usage_limit_mbytes: int | None = None,
        authorized_guest_limit: int | None = None,
    ) -> list[Voucher]:
        """Generate voucher(s) for a site and publish the last one as latest."""
        kwargs: dict[str, Any] = {
            "name": name,
            "time_limit_minutes": time_limit_minutes,
            "count": count,
        }
        if tx_rate_limit_kbps is not None:
            kwargs["tx_rate_limit_kbps"] = tx_rate_limit_kbps
        if rx_rate_limit_kbps is not None:
            kwargs["rx_rate_limit_kbps"] = rx_rate_limit_kbps
        if data_usage_limit_mbytes is not None:
            kwargs["data_usage_limit_mbytes"] = data_usage_limit_mbytes
        if authorized_guest_limit is not None:
            kwargs["authorized_guest_limit"] = authorized_guest_limit
        created = await async_execute_command(
            CommandFailure(
                translation_key="command_failed",
                message=f"Unable to generate voucher in site {site_id}",
            ),
            self.network_client.vouchers.create,
            site_id,
            **kwargs,
        )
        vouchers = list(created) if isinstance(created, list) else []
        record = voucher_to_record(vouchers[-1]) if vouchers else None
        if record is not None:
            self._publish_latest_voucher(site_id, record)
        await self._async_refresh_vouchers_after_change(site_id)
        return vouchers

    async def async_delete_voucher(self, site_id: str, voucher_id: str) -> None:
        """Delete a voucher, and drop it as the site's latest voucher."""
        await async_execute_command(
            CommandFailure(
                translation_key="command_failed",
                message=f"Unable to delete voucher {voucher_id}",
            ),
            self.network_client.vouchers.delete,
            site_id,
            voucher_id,
        )
        latest = self.data.get("latest_vouchers", {}).get(site_id)
        if latest is not None and latest.get("id") == voucher_id:
            self._publish_latest_voucher(site_id, None)
        await self._async_refresh_vouchers_after_change(site_id)

    async def _async_refresh_vouchers_after_change(self, site_id: str) -> None:
        """Refresh a site's vouchers after a change; never fail the change."""
        try:
            await self.async_refresh_vouchers(site_id)
        except UniFiAuthenticationError:
            self.config_entry.async_start_reauth(self.hass)
        except (UniFiError, RuntimeError) as err:
            _LOGGER.warning(
                "Unable to refresh the vouchers of site %s after a change: %s",
                site_id,
                err,
            )
        self.async_update_listeners()

    def get_site(self, site_id: str) -> dict[str, Any] | None:
        """Get site data by site ID."""
        sites = self.data.get("sites", {})
        result = sites.get(site_id)
        return result if isinstance(result, dict) else None

    @property
    def legacy_site_names(self) -> Mapping[str, str]:
        """
        Return the published integration site ID -> classic site name mapping.

        The device coordinator reads it on each poll for legacy telemetry
        instead of looking the classic sites up itself (ADR-010).
        """
        mapping: Mapping[str, str] = self.data.get("legacy_site_names", {})
        return mapping

    def resolve_legacy_site_name(self, site_id: str, *, verified_only: bool) -> str:
        """
        Return the classic site name for an integration site ID.

        The single legacy site-name rule (ADR-010), in a fixed order: the
        site's verified ``internalReference``, then (unless ``verified_only``)
        the published ``legacy_site_names`` mapping, then a translated
        ``legacy_site_not_found`` error. It never guesses ``"default"``; a
        real ``"default"`` from either source is returned as is.

        ``verified_only`` is for writes that must not land on a fuzzy match,
        such as the rule families, historical clients and batch forget.
        """
        site_name = verified_legacy_site_name(self.get_site(site_id))
        if site_name is None and not verified_only:
            mapped = self.data.get("legacy_site_names", {}).get(site_id)
            if isinstance(mapped, str) and mapped.strip():
                site_name = mapped
        if site_name is None:
            msg = f"No verified classic site name found for site {site_id}"
            raise HomeAssistantError(
                msg,
                translation_domain=DOMAIN,
                translation_key="legacy_site_not_found",
                translation_placeholders={"site_id": site_id},
            )
        return site_name

    async def async_resolve_legacy_site_name(
        self, site_id: str, *, verified_only: bool
    ) -> str:
        """
        Return the classic site name for a write, remapping once if needed.

        ``resolve_legacy_site_name`` with one recovery step for mapped
        writes: when it fails while the published mapping is flagged as
        failed or lacks the site (for example after a failed lookup in the
        first refresh), the legacy mapping alone is looked up again and the
        resolution retried, so the write does not wait for the next config
        poll. Verified-only writes never read the mapping, so they never
        remap.
        """
        try:
            return self.resolve_legacy_site_name(site_id, verified_only=verified_only)
        except HomeAssistantError:
            if (
                verified_only
                or site_id not in self.data.get("sites", {})
                or not (
                    self.data.get("legacy_mapping_failed")
                    or site_id not in self.data.get("legacy_site_names", {})
                )
            ):
                raise
        await self._async_remap_legacy_sites()
        return self.resolve_legacy_site_name(site_id, verified_only=verified_only)

    async def _async_remap_legacy_sites(self) -> None:
        """
        Look up and publish the legacy site mapping outside the config poll.

        Concurrent callers share one lookup: they queue on the lock and find
        the attempt already made. At most one lookup runs per
        ``LEGACY_REMAP_COOLDOWN`` seconds, so a classic API that is down is
        not asked again on every write. A failed lookup leaves the published
        mapping as it is. The mapping is no entity state, so listeners are
        not notified.
        """
        async with self._legacy_remap_lock:
            now = time.monotonic()
            if (
                self._legacy_remap_at is not None
                and now - self._legacy_remap_at < LEGACY_REMAP_COOLDOWN
            ):
                return
            self._legacy_remap_at = now
            try:
                legacy_sites = await self.network_client.sites.get_legacy_all()
            except (UniFiError, ValidationError) as err:
                _LOGGER.debug(
                    "Config coordinator: On-demand legacy site lookup failed: %s",
                    type(err).__name__,
                )
                return
            self.data = {
                **self.data,
                "legacy_site_names": self._map_legacy_site_names(
                    self.data.get("sites", {}), legacy_sites
                ),
                "legacy_mapping_failed": False,
            }

    async def async_set_firewall_rule_enabled(
        self,
        site_id: str,
        rule_id: str,
        *,
        enabled: bool,
    ) -> None:
        """Enable or disable a firewall rule."""
        await async_execute_command(
            CommandFailure(
                translation_key="command_failed",
                message=f"Unable to update firewall rule {rule_id}",
            ),
            self.network_client.firewall.update_rule,
            site_id,
            rule_id,
            enabled=enabled,
        )
        await self._async_after_rule_write(
            "firewall_rules", site_id, rule_id, enabled=enabled
        )

    async def async_set_policy_based_route_enabled(
        self,
        site_id: str,
        route_id: str,
        *,
        enabled: bool,
    ) -> None:
        """Enable or disable a policy-based route (traffic route)."""
        site_name = await self.async_resolve_legacy_site_name(
            site_id, verified_only=False
        )
        await async_execute_command(
            CommandFailure(
                translation_key="command_failed",
                message=f"Unable to update policy-based route {route_id}",
            ),
            self.network_client.routes.update_route,
            site_name,
            route_id,
            enabled=enabled,
        )
        await self._async_after_rule_write(
            "policy_based_routes", site_id, route_id, enabled=enabled
        )

    async def async_set_vpn_client_enabled(
        self,
        site_id: str,
        client_id: str,
        *,
        enabled: bool,
    ) -> None:
        """Enable or disable a VPN client configuration."""
        site_name = await self.async_resolve_legacy_site_name(
            site_id, verified_only=False
        )
        await async_execute_command(
            CommandFailure(
                translation_key="command_failed",
                message=f"Unable to update VPN client {client_id}",
            ),
            self.network_client.vpn_clients.update_vpn_client,
            site_name,
            client_id,
            enabled=enabled,
        )
        await self._async_after_rule_write(
            "vpn_clients", site_id, client_id, enabled=enabled
        )

    async def async_set_port_forward_enabled(
        self, site_id: str, rule_id: str, *, enabled: bool
    ) -> None:
        """Enable or disable a port forwarding rule."""
        await self._async_set_legacy_rule_enabled(
            site_id,
            rule_id,
            enabled=enabled,
            data_key="port_forwards",
            name_field="name",
            label="port forward",
            update=self.network_client.port_forwards.update_port_forward,
            keep_message=True,
        )

    async def async_set_traffic_rule_enabled(
        self, site_id: str, rule_id: str, *, enabled: bool
    ) -> None:
        """Enable or disable a traffic rule."""
        await self._async_set_legacy_rule_enabled(
            site_id,
            rule_id,
            enabled=enabled,
            data_key="traffic_rules",
            name_field="description",
            label="traffic rule",
            update=self.network_client.traffic_rules.update_traffic_rule,
            keep_message=True,
        )

    async def async_set_nat_rule_enabled(
        self, site_id: str, rule_id: str, *, enabled: bool
    ) -> None:
        """Enable or disable a NAT rule."""
        await self._async_set_legacy_rule_enabled(
            site_id,
            rule_id,
            enabled=enabled,
            data_key="nat_rules",
            name_field="description",
            label="NAT rule",
            update=self.network_client.nat_rules.update_nat_rule,
        )

    async def async_set_static_route_enabled(
        self, site_id: str, rule_id: str, *, enabled: bool
    ) -> None:
        """Enable or disable a static route."""
        await self._async_set_legacy_rule_enabled(
            site_id,
            rule_id,
            enabled=enabled,
            data_key="static_routes",
            name_field="name",
            label="static route",
            update=self.network_client.static_routes.update_static_route,
        )

    async def _async_set_legacy_rule_enabled(
        self,
        site_id: str,
        rule_id: str,
        *,
        enabled: bool,
        data_key: str,
        name_field: str,
        label: str,
        update: Callable[..., Awaitable[Any]],
        keep_message: bool = False,
    ) -> None:
        """
        Toggle one rule of a classic or v2 rule family by full-record PUT.

        A rule that no longer exists or that the console manages gets one
        reconciliation refresh, so its switch drops out, and then raises.
        Port forwards and traffic rules keep their established English
        message (``keep_message``).
        """
        rule_data = self.data.get(data_key, {}).get(site_id, {}).get(rule_id, {})
        display_name = rule_display_name(rule_data, rule_id, name_field)
        site_name = self.resolve_legacy_site_name(site_id, verified_only=True)
        msg = f"Unable to update {label} {display_name}"
        placeholders = {"rule_type": label, "rule_name": display_name}
        args = (msg,) if keep_message else ()
        try:
            await update(site_name, rule_id, enabled=enabled)
        except (UniFiNotFoundError, UniFiRuleNotEditableError) as err:
            not_found = isinstance(err, UniFiNotFoundError)
            _LOGGER.warning(
                "%s: %s",
                msg,
                "it no longer exists on the console"
                if not_found
                else "it is managed by the console",
            )
            await self.async_refresh()
            raise HomeAssistantError(
                *args,
                translation_domain=DOMAIN,
                translation_key="network_rule_not_found"
                if not_found
                else "network_rule_console_managed",
                translation_placeholders=placeholders,
            ) from err
        except HomeAssistantError:
            raise
        except Exception as err:
            _LOGGER.exception("%s", msg)
            raise HomeAssistantError(
                *args,
                translation_domain=DOMAIN,
                translation_key="network_rule_update_failed",
                translation_placeholders=placeholders,
            ) from err
        await self._async_after_rule_write(data_key, site_id, rule_id, enabled=enabled)

    async def _async_after_rule_write(
        self, section: str, site_id: str, rule_id: str, *, enabled: bool
    ) -> None:
        """
        Publish a successful rule toggle, then reconcile it (ADR-010).

        The ``enabled`` overlay is copy-on-write: the data root, the section,
        the site map and the rule record are replaced, never mutated, so
        older snapshots keep their contents. Listeners are notified without
        ``async_set_updated_data()``. A poll that started before the write
        may briefly restore the old value until the next refresh.

        The awaited refresh that follows does not fail the write: a recorded
        failure is logged, and an authentication failure starts reauth in
        ``DataUpdateCoordinator``. When the family is still unavailable
        afterwards, one more refresh is scheduled.
        """
        section_data = self.data.get(section)
        site_rules = (
            section_data.get(site_id) if isinstance(section_data, dict) else None
        )
        record = site_rules.get(rule_id) if isinstance(site_rules, dict) else None
        if (
            isinstance(section_data, dict)
            and isinstance(site_rules, dict)
            and isinstance(record, dict)
        ):
            self.data = {
                **self.data,
                section: {
                    **section_data,
                    site_id: {**site_rules, rule_id: {**record, "enabled": enabled}},
                },
            }
            self.async_update_listeners()

        await self.async_refresh()
        if not self.last_update_success:
            _LOGGER.debug(
                "Config coordinator: refresh after a %s write failed: %s",
                section,
                self.last_exception,
            )
        # Some families (policy-based routes, VPN clients) report a failed
        # fetch as an empty map rather than a failed section, so the written
        # record going missing is also a reason to retry.
        refreshed = self.data.get(section)
        refreshed_site = refreshed.get(site_id) if isinstance(refreshed, dict) else None
        if not self._section_available(section, site_id) or not (
            isinstance(refreshed_site, dict) and rule_id in refreshed_site
        ):
            self._schedule_rule_refresh_retry(section, site_id)

    def _schedule_rule_refresh_retry(self, section: str, site_id: str) -> None:
        """
        Refresh once more shortly after a refresh that could not read a family.

        Without the second attempt every switch of that family in the site
        stays unavailable until the next scheduled poll, although the change
        itself was applied. A newer retry for the same family and site
        replaces a pending one, and the retry never reschedules itself.
        """
        key = (section, site_id)
        if (cancel := self._rule_refresh_retries.pop(key, None)) is not None:
            cancel()

        async def _async_retry(_now: datetime) -> None:
            self._rule_refresh_retries.pop(key, None)
            await self.async_refresh()

        self._rule_refresh_retries[key] = async_call_later(
            self.hass, RULE_REFRESH_RETRY_DELAY, _async_retry
        )

    async def async_update_wifi_network(
        self,
        site_id: str,
        wifi_id: str,
        *,
        enabled: bool,
    ) -> None:
        """Enable or disable a WiFi network, then request a deferred refresh."""
        await async_execute_command(
            CommandFailure(
                translation_key="command_failed",
                message=f"Unable to update WiFi network {wifi_id}",
            ),
            self.network_client.wifi.update,
            site_id,
            wifi_id,
            enabled=enabled,
        )
        await self.async_request_refresh()

    async def async_authorize_guest(self, site_id: str, client_id: str) -> None:
        """Authorize guest access for a network client."""
        await async_execute_command(
            CommandFailure(
                translation_key="command_failed",
                message=f"Unable to authorize guest client {client_id}",
            ),
            self.network_client.clients.authorize_guest,
            site_id,
            client_id,
        )

    async def async_unauthorize_guest(self, site_id: str, client_id: str) -> None:
        """Remove guest authorization for a network client."""
        await async_execute_command(
            CommandFailure(
                translation_key="command_failed",
                message=f"Unable to unauthorize guest client {client_id}",
            ),
            self.network_client.clients.unauthorize_guest,
            site_id,
            client_id,
        )

    async def async_shutdown(self) -> None:
        """Cancel pending rule refresh retries, then shut down."""
        for cancel in self._rule_refresh_retries.values():
            cancel()
        self._rule_refresh_retries.clear()
        await super().async_shutdown()

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
