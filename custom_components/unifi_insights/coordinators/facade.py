# Copyright 2026 UniFi Insights contributors
"""Facade coordinator providing backward-compatible unified data view."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from unifi_official_api.innerspace import UniFiInnerSpaceClient
    from unifi_official_api.network import UniFiNetworkClient
    from unifi_official_api.network.models.voucher import Voucher
    from unifi_official_api.protect import UniFiProtectClient

    from .config import UnifiConfigCoordinator
    from .device import UnifiDeviceCoordinator
    from .innerspace import UnifiInsightsInnerSpaceCoordinator
    from .protect import UnifiProtectCoordinator
    from .site_manager import UnifiInsightsSiteManagerCoordinator

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiError,
)

from custom_components.unifi_insights.client_wake import select_wake_history
from custom_components.unifi_insights.const import CONF_CONSOLE_ID, DOMAIN
from custom_components.unifi_insights.data_transforms import (
    NETWORK_FACTS_KEY,
    NetworkDeviceFacts,
    correlate_innerspace_devices,
    device_facts,
    get_site_gateway_id,
    normalize_innerspace_snapshot,
)

from .base import CommandFailure, async_execute_command
from .rule_export import async_build_rule_export
from .voucher_state import VoucherSettings, refresh_latest_voucher, voucher_to_record

_LOGGER = logging.getLogger(__name__)


class UnifiFacadeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """
    Facade coordinator providing unified data view for backward compatibility.

    This coordinator aggregates data from the specialized coordinators
    (config, device, protect) and presents a unified interface that matches
    the original single-coordinator structure. This allows existing entity
    classes to work without modifications.

    Data structure (matches original coordinator):
    - sites: from config_coordinator
    - devices: from device_coordinator
    - clients: from device_coordinator
    - stats: from device_coordinator
    - wifi: from config_coordinator
    - vouchers: from config_coordinator
    - latest_vouchers: generated in Home Assistant
    - protect: from protect_coordinator (cameras, lights, sensors, etc.)
    - innerspace: from innerspace_coordinator (floor_plans, access_points,
      switches, inventory)
    - last_update: combined from all coordinators
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        network_client: UniFiNetworkClient,
        protect_client: UniFiProtectClient | None,
        entry: ConfigEntry,
        config_coordinator: UnifiConfigCoordinator,
        *,
        device_coordinator: UnifiDeviceCoordinator,
        protect_coordinator: UnifiProtectCoordinator | None,
        innerspace_client: UniFiInnerSpaceClient | None = None,
        innerspace_coordinator: UnifiInsightsInnerSpaceCoordinator | None = None,
        site_manager_coordinator: UnifiInsightsSiteManagerCoordinator | None = None,
    ) -> None:
        """Initialize the facade coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_facade",
            # Facade doesn't poll - it aggregates from other coordinators
            update_interval=None,
        )
        self.network_client = network_client
        self.protect_client = protect_client
        self.innerspace_client = innerspace_client
        self._config_coordinator = config_coordinator
        self._device_coordinator = device_coordinator
        self._protect_coordinator = protect_coordinator
        self._innerspace_coordinator = innerspace_coordinator
        self._site_manager_coordinator = site_manager_coordinator
        self._site_manager_host_id = entry.data.get(CONF_CONSOLE_ID)

        self._voucher_settings: dict[str, VoucherSettings] = {}
        self._latest_vouchers: dict[str, dict[str, Any]] = {}

        # Remove-callbacks returned by async_add_listener() below, released
        # in async_shutdown() so this facade's forwarding listener doesn't
        # outlive it on the sub-coordinators (see _setup_listeners).
        self._sub_coordinator_unsubs: list[Callable[[], None]] = []
        self._innerspace_corr_key: tuple[Any, ...] | None = None
        self._cached_innerspace_data: dict[str, Any] | None = None

        # Register listeners to update when any coordinator updates
        self._setup_listeners()

        # Aggregate the sub-coordinators' current data immediately so that
        # self.data is never None for entities created right after init.
        self._aggregate_data()

    def _setup_listeners(self) -> None:
        """Set up listeners to aggregate data when coordinators update."""
        # async_add_listener() returns a remove-callback; it must be kept
        # and invoked on shutdown (see async_shutdown) or this facade's
        # listener registration outlives the facade itself - each
        # config-entry reload builds a fresh facade + fresh sub-coordinators,
        # but without releasing this, the outgoing facade stays reachable
        # (and un-collectable) via the very listener slot it never freed.
        self._sub_coordinator_unsubs.append(
            self._device_coordinator.async_add_listener(self._handle_coordinator_update)
        )
        self._sub_coordinator_unsubs.append(
            self._config_coordinator.async_add_listener(self._handle_coordinator_update)
        )
        if self._protect_coordinator:
            self._sub_coordinator_unsubs.append(
                self._protect_coordinator.async_add_listener(
                    self._handle_coordinator_update
                )
            )
        if self._innerspace_coordinator:
            self._sub_coordinator_unsubs.append(
                self._innerspace_coordinator.async_add_listener(
                    self._handle_coordinator_update
                )
            )
        if self._site_manager_coordinator:
            self._sub_coordinator_unsubs.append(
                self._site_manager_coordinator.async_add_listener(
                    self._handle_coordinator_update
                )
            )

    async def async_shutdown(self) -> None:
        """
        Shut down the facade and release its listeners on the sub-coordinators.

        ``DataUpdateCoordinator.__init__`` auto-registers
        ``config_entry.async_on_unload(self.async_shutdown)`` for every
        coordinator constructed with a ``config_entry`` (all four of ours
        qualify), so this already runs automatically on config-entry
        unload/reload - no extra wiring is needed in
        ``__init__.py::async_unload_entry``. That auto-registration is also
        why a leaked facade doesn't keep the sub-coordinators *polling*:
        each sub-coordinator's own auto-registered ``async_shutdown()``
        cancels its scheduled refresh and sets ``_shutdown_requested``,
        which ``_async_refresh()`` checks before it would ever fetch or
        reschedule. What that auto-registration does *not* do is undo the
        side effect *this* facade caused on *other* objects: the base
        ``async_shutdown()`` never touches ``_listeners``, so the
        remove-callbacks from ``_setup_listeners`` must be released here
        explicitly, or the sub-coordinators keep a dead reference back to
        this facade indefinitely.
        """
        for unsub in self._sub_coordinator_unsubs:
            unsub()
        self._sub_coordinator_unsubs.clear()
        await super().async_shutdown()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle update from any coordinator by refreshing aggregated data."""
        self._aggregate_data()
        self.async_update_listeners()

    @staticmethod
    def _build_innerspace_corr_key(
        raw_innerspace: dict[str, Any],
        devices: Any,
        protect_data: Any,
    ) -> tuple[Any, ...]:
        """Build a lightweight cache key for InnerSpace MAC correlation."""
        net_pairs: list[tuple[str, str, Any]] = []
        if isinstance(devices, dict):
            for site_id, site_devs in sorted(devices.items()):
                if isinstance(site_devs, dict):
                    for dev_id, dev in sorted(site_devs.items()):
                        if isinstance(dev, dict):
                            net_pairs.append(
                                (
                                    str(site_id),
                                    str(dev_id),
                                    dev.get("macAddress")
                                    or dev.get("mac_address")
                                    or dev.get("mac"),
                                )
                            )
        prot_pairs: list[tuple[str, str, Any]] = []
        if isinstance(protect_data, dict):
            for col_name in (
                "cameras",
                "lights",
                "sensors",
                "nvrs",
                "viewers",
                "chimes",
                "doorlocks",
                "viewports",
            ):
                col = protect_data.get(col_name)
                if isinstance(col, dict):
                    for dev_id, dev in sorted(col.items()):
                        if isinstance(dev, dict):
                            prot_pairs.append(
                                (
                                    col_name,
                                    str(dev_id),
                                    dev.get("mac")
                                    or dev.get("macAddress")
                                    or dev.get("mac_address"),
                                )
                            )
        return (id(raw_innerspace), tuple(net_pairs), tuple(prot_pairs))

    def _refresh_latest_vouchers(self) -> None:
        """Pick up activation / expiry changes of latest vouchers from inventory."""
        inventory = self._config_coordinator.data.get("vouchers", {})
        if not isinstance(inventory, Mapping):
            return
        for site_id, record in list(self._latest_vouchers.items()):
            site_inventory = inventory.get(site_id)
            if isinstance(site_inventory, Mapping):
                self._latest_vouchers[site_id] = refresh_latest_voucher(
                    record, site_inventory
                )

    def _aggregate_data(self) -> None:
        """Aggregate data from all coordinators into unified structure."""
        self._refresh_latest_vouchers()
        devices = self._device_coordinator.data.get("devices", {})
        protect_data = (
            self._protect_coordinator.data
            if self._protect_coordinator
            else {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "fobs": {},
                "link_stations": {},
                "alarm_hubs": {},
                "sirens": {},
                "doorlocks": {},
                "viewports": {},
                "liveviews": {},
                "protect_info": {},
                "events": {},
            }
        )
        if self._innerspace_coordinator and self._innerspace_coordinator.data:
            raw_innerspace = self._innerspace_coordinator.data
            corr_key = self._build_innerspace_corr_key(
                raw_innerspace, devices, protect_data
            )
            if (
                self._cached_innerspace_data is not None
                and self._innerspace_corr_key == corr_key
            ):
                innerspace_data = self._cached_innerspace_data
            else:
                devices_map: dict[str, dict[str, Any]] = {}
                access_points_map = {
                    str(rec_id): dict(rec)
                    for rec_id, rec in raw_innerspace.get("access_points", {}).items()
                    if isinstance(rec, dict)
                }
                switches_map = {
                    str(rec_id): dict(rec)
                    for rec_id, rec in raw_innerspace.get("switches", {}).items()
                    if isinstance(rec, dict)
                }
                inventory_map = {
                    str(rec_id): dict(rec)
                    for rec_id, rec in raw_innerspace.get("inventory", {}).items()
                    if isinstance(rec, dict)
                }
                for section_map in (access_points_map, switches_map, inventory_map):
                    devices_map.update(section_map)
                innerspace_data = {
                    "project": raw_innerspace.get("project"),
                    "floor_plans": dict(raw_innerspace.get("floor_plans", {})),
                    "access_points": access_points_map,
                    "switches": switches_map,
                    "inventory": inventory_map,
                    "placed_devices": {**access_points_map, **switches_map},
                    "devices": devices_map,
                    "last_update": raw_innerspace.get("last_update"),
                }
                innerspace_data["correlations"] = correlate_innerspace_devices(
                    innerspace_data,
                    network_devices=devices if isinstance(devices, dict) else None,
                    protect_devices=protect_data
                    if isinstance(protect_data, dict)
                    else None,
                )
                self._innerspace_corr_key = corr_key
                self._cached_innerspace_data = innerspace_data
        else:
            self._innerspace_corr_key = None
            self._cached_innerspace_data = None
            innerspace_data = {**normalize_innerspace_snapshot(), "last_update": None}

        self.data = {
            # From config coordinator
            "sites": self._config_coordinator.data.get("sites", {}),
            "wifi": self._config_coordinator.data.get("wifi", {}),
            "vouchers": self._config_coordinator.data.get("vouchers", {}),
            "latest_vouchers": {
                site_id: dict(record)
                for site_id, record in self._latest_vouchers.items()
            },
            "firewall_rules": self._config_coordinator.data.get("firewall_rules", {}),
            "policy_based_routes": self._config_coordinator.data.get(
                "policy_based_routes", {}
            ),
            "vpn_clients": self._config_coordinator.data.get("vpn_clients", {}),
            "port_forwards": self._config_coordinator.data.get("port_forwards", {}),
            "traffic_rules": self._config_coordinator.data.get("traffic_rules", {}),
            "nat_rules": self._config_coordinator.data.get("nat_rules", {}),
            "static_routes": self._config_coordinator.data.get("static_routes", {}),
            "site_vpns": self._config_coordinator.data.get("site_vpns", {}),
            "rule_changes": self._config_coordinator.data.get("rule_changes", {}),
            "network_info": self._config_coordinator.data.get("network_info", {}),
            "client_links": self._config_coordinator.data.get("client_links", {}),
            "internet_activity": self._config_coordinator.data.get(
                "internet_activity", {}
            ),
            "internet_activity_unavailable": self._config_coordinator.data.get(
                "internet_activity_unavailable", set()
            ),
            # From device coordinator
            "devices": devices,
            "clients": self._device_coordinator.data.get("clients", {}),
            "stats": self._device_coordinator.data.get("stats", {}),
            "vpn_connections": self._device_coordinator.data.get("vpn_connections", {}),
            # Passed by reference like devices and stats. None (never
            # published) makes the accessors build the facts from raw data.
            NETWORK_FACTS_KEY: self._device_coordinator.data.get(NETWORK_FACTS_KEY),
            # From protect coordinator
            "protect": protect_data,
            # From innerspace coordinator
            "innerspace": innerspace_data,
            # Combined timestamp
            "last_update": datetime.now(tz=UTC),
        }
        if self._site_manager_coordinator:
            account_data = self._site_manager_coordinator.data
            self.data["site_manager"] = {
                **account_data,
                "selected_host_id": (
                    self._site_manager_host_id
                    if self._site_manager_host_id in account_data["hosts"]
                    else None
                ),
            }

    def get_site(self, site_id: str) -> dict[str, Any] | None:
        """Get site data by site ID (delegates to config coordinator)."""
        return self._config_coordinator.get_site(site_id)

    def get_device(self, site_id: str, device_id: str) -> dict[str, Any] | None:
        """Get device data by site ID and device ID."""
        devices = self.data.get("devices", {}).get(site_id, {})
        result = devices.get(device_id)
        return result if isinstance(result, dict) else None

    def get_device_stats(self, site_id: str, device_id: str) -> dict[str, Any] | None:
        """Get device statistics by site ID and device ID."""
        stats = self.data.get("stats", {}).get(site_id, {})
        result = stats.get(device_id)
        return result if isinstance(result, dict) else None

    def get_device_facts(
        self, site_id: str, device_id: str
    ) -> NetworkDeviceFacts | None:
        """Return the read-model facts of one network device, or None."""
        return device_facts(self.data, site_id, device_id)

    def get_site_gateway_id(self, site_id: str) -> str | None:
        """Return the ID of a site's gateway device, or None."""
        return get_site_gateway_id(self.data, site_id)

    @property
    def config_coordinator(self) -> UnifiConfigCoordinator:
        """Return the owner of the rule, Wi-Fi and guest commands (ADR-010)."""
        return self._config_coordinator

    @property
    def device_coordinator(self) -> UnifiDeviceCoordinator:
        """Return the owner of the device, outlet and client commands (ADR-010)."""
        return self._device_coordinator

    def require_protect_coordinator(self) -> UnifiProtectCoordinator:
        """Return the owner of the Protect commands, or raise if not configured."""
        if self._protect_coordinator is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="no_protect_coordinator",
            )
        return self._protect_coordinator

    @property
    def available(self) -> bool:
        """Return combined availability from all sub-coordinators."""
        return (
            self.device_available
            and self.config_available
            and self.protect_available
            and self.innerspace_available
        )

    @property
    def device_available(self) -> bool:
        """Return True if the device coordinator is available."""
        return self._device_coordinator.last_update_success

    @property
    def config_available(self) -> bool:
        """Return True if the config coordinator is available."""
        return self._config_coordinator.last_update_success

    def wifi_available(self, site_id: str) -> bool:
        """Return True if a site's WiFi networks were fetched on the last refresh."""
        return self._config_coordinator.wifi_available(site_id)

    def vouchers_available(self, site_id: str) -> bool:
        """Return True if a site's hotspot vouchers were fetched on the last refresh."""
        return self._config_coordinator.vouchers_available(site_id)

    def get_voucher_settings(self, site_id: str) -> VoucherSettings:
        """Get or initialize the voucher settings for a site."""
        return self._voucher_settings.setdefault(site_id, VoucherSettings())

    def firewall_available(self, site_id: str) -> bool:
        """Return True if a site's firewall rules were fetched on the last refresh."""
        return self._config_coordinator.firewall_available(site_id)

    def port_forwards_available(self, site_id: str) -> bool:
        """Return True if a site's port forwards were fetched on the last refresh."""
        return bool(self._config_coordinator.port_forwards_available(site_id))

    def traffic_rules_available(self, site_id: str) -> bool:
        """Return True if a site's traffic rules were fetched on the last refresh."""
        return bool(self._config_coordinator.traffic_rules_available(site_id))

    def nat_rules_available(self, site_id: str) -> bool:
        """Return True if a site's NAT rules were fetched on the last refresh."""
        return bool(self._config_coordinator.nat_rules_available(site_id))

    def static_routes_available(self, site_id: str) -> bool:
        """Return True if a site's static routes were fetched on the last refresh."""
        return bool(self._config_coordinator.static_routes_available(site_id))

    def internet_activity_available(self, site_id: str) -> bool:
        """Return True if a site's internet activity was fetched on the last refresh."""
        if hasattr(self._config_coordinator, "internet_activity_available"):
            return self._config_coordinator.internet_activity_available(site_id)
        return self.config_available

    @property
    def protect_available(self) -> bool:
        """Return True if the protect coordinator is available or not configured."""
        if self._protect_coordinator is None:
            return True
        return self._protect_coordinator.last_update_success

    @property
    def innerspace_available(self) -> bool:
        """Return True if the InnerSpace coordinator is available or not configured."""
        if self._innerspace_coordinator is None:
            return True
        return self._innerspace_coordinator.last_update_success

    @property
    def network_available(self) -> bool:
        """Return True if the Network application is available on the console."""
        return getattr(self._config_coordinator, "network_available", True)

    @property
    def is_network_usable(self) -> bool:
        """Return True if Network is configured and usable."""
        return self.network_available and self.network_client is not None

    async def _async_update_data(self) -> dict[str, Any]:
        """
        Update aggregated data.

        The facade doesn't fetch data itself - it aggregates from coordinators.
        This method is called periodically and ensures data is fresh.
        """
        self._aggregate_data()
        return self.data

    async def async_request_refresh(self) -> None:
        """
        Force a genuine refresh of all underlying coordinators and notify listeners.

        Home Assistant's ``homeassistant.update_entity`` action reaches this
        through ``CoordinatorEntity.async_update`` and expects fresh data by
        the time the await returns (writes reconcile through their owner
        instead, ADR-010). Two deliberate departures from the obvious
        implementation make that true:

        1. Each sub-coordinator's own ``async_refresh()`` is used instead
           of its ``async_request_refresh()``. The latter goes through
           that coordinator's ``Debouncer`` - fine for coalescing
           coordinator-internal refresh triggers, but wrong here: when a
           debounce cooldown from unrelated recent activity is already
           armed, ``async_request_refresh()`` returns immediately without
           fetching anything, so aggregating right after it would silently
           serve stale data. ``async_refresh()`` always performs (and
           awaits) a real fetch. The trade-off is that this path no longer
           benefits from that debounce - acceptable because it only runs
           once per explicit, user-triggered action, not on a hot loop.
        2. The three refreshes run concurrently via ``asyncio.gather``
           rather than sequentially - three real HTTP round trips awaited
           one after another would make every caller noticeably slower for
           no benefit, since the coordinators are independent.

        Each sub-coordinator's own refresh already calls
        ``async_update_listeners()`` when its data changes, which cascades
        into this facade's ``_handle_coordinator_update`` via the listener
        chain from ``_setup_listeners`` - re-aggregating and notifying
        this facade's listeners already in the common case. The explicit
        ``_aggregate_data()`` + ``async_update_listeners()`` below is kept
        anyway so a caller of this method is *always* notified even in the
        edge case where none of the three sub-refreshes produced a change
        (so that cascade stays silent) - the base
        ``DataUpdateCoordinator.async_request_refresh()`` this replaces
        never called ``async_update_listeners()`` at all, which was the
        second half of the original defect.
        """
        await self._async_refresh_children()

    async def _async_refresh_children(
        self,
        *,
        include_protect: bool = True,
        include_innerspace: bool = False,
    ) -> list[str]:
        """
        Refresh the sub-coordinators concurrently and report what failed.

        ``DataUpdateCoordinator.async_refresh()`` does not raise: it records
        the problem as ``last_update_success = False`` plus ``last_exception``
        and returns normally. A caller that only awaits it therefore cannot
        tell a successful refresh from a failed one, so both that recorded
        state and any exception that escaped ``gather`` are collected here and
        returned as human-readable strings, newest data aggregated either way.
        """
        targets: list[tuple[str, DataUpdateCoordinator[Any]]] = [
            ("config", self._config_coordinator),
            ("devices", self._device_coordinator),
        ]
        if include_protect and self._protect_coordinator:
            targets.append(("protect", self._protect_coordinator))
        if include_innerspace and self._innerspace_coordinator:
            targets.append(("innerspace", self._innerspace_coordinator))

        results = await asyncio.gather(
            *(coordinator.async_refresh() for _, coordinator in targets),
            return_exceptions=True,
        )

        failures: list[str] = []
        for (name, coordinator), result in zip(targets, results, strict=True):
            if isinstance(result, BaseException):
                # CancelledError is a BaseException and means this task is
                # being torn down, not that the console misbehaved.
                if isinstance(result, asyncio.CancelledError):
                    raise result
                failures.append(f"{name} coordinator: {result}")
            elif not coordinator.last_update_success:
                error = coordinator.last_exception
                failures.append(
                    f"{name} coordinator: {error}" if error else f"{name} coordinator"
                )

        # Aggregate the updated data and notify this facade's own listeners.
        self._aggregate_data()
        self.async_update_listeners()
        return failures

    async def async_refresh_or_raise(self, *, include_protect: bool = True) -> None:
        """Refresh the sub-coordinators, raising if any of them failed."""
        failures = await self._async_refresh_children(include_protect=include_protect)
        if failures:
            joined = ", ".join(failures)
            msg = f"Error refreshing UniFi Insights data: {joined}"
            # The placeholder names the coordinators only; the raw errors stay
            # in the English message and the log.
            targets = ", ".join(failure.split(":", 1)[0] for failure in failures)
            raise HomeAssistantError(
                msg,
                translation_domain=DOMAIN,
                translation_key="refresh_failed",
                translation_placeholders={"targets": targets},
            )

    async def async_export_network_rules(
        self,
        site_ids: list[str] | None = None,
        rule_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch the current network rules and return a JSON-safe export."""
        entry = self.config_entry
        return await async_build_rule_export(
            self.network_client,
            self._config_coordinator.data.get("sites", {}),
            console={
                "entry_id": entry.entry_id if entry else None,
                "title": entry.title if entry else None,
            },
            site_ids=site_ids,
            rule_types=rule_types,
            is_unsupported=self._config_coordinator._is_unsupported_response,
        )

    async def async_get_historical_clients(self, site_id: str) -> list[dict[str, Any]]:
        """Read historical client records for a site from the classic API."""
        site_name = self._config_coordinator.resolve_legacy_site_name(
            site_id, verified_only=True
        )
        result = await async_execute_command(
            CommandFailure(
                translation_key="command_failed",
                message=f"Unable to read historical clients for site {site_id}",
            ),
            self.network_client.clients.get_historical_legacy,
            site_name,
        )
        return result if isinstance(result, list) else []

    async def async_get_wake_history(
        self, *, enabled_macs: set[str] | None = None
    ) -> dict[str, tuple[str, str]]:
        """
        Return recent wired clients and any-age names for enabled Wake buttons.

        Returns MAC -> (name, site_id). Best effort: a site whose history
        cannot be read is skipped, so this never raises. Costs one request
        per site, and is called once at setup.
        """
        now = dt_util.utcnow().timestamp()
        history: dict[str, tuple[str, str]] = {}
        for site_id in self._config_coordinator.get_site_ids():
            try:
                site_name = self._config_coordinator.resolve_legacy_site_name(
                    site_id, verified_only=True
                )
                records = await self.network_client.clients.get_historical_legacy(
                    site_name
                )
            except Exception as err:
                _LOGGER.debug(
                    "Wake-on-LAN: no client history for site %s: %s",
                    site_id,
                    err,
                )
                continue
            for mac, name in select_wake_history(
                records, now=now, enabled_macs=enabled_macs
            ).items():
                history.setdefault(mac, (name, str(site_id)))
        return history

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
        """Generate voucher(s) for a site."""
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
            self._latest_vouchers[site_id] = record
        await self._async_refresh_vouchers(site_id)
        return vouchers

    async def async_delete_voucher(self, site_id: str, voucher_id: str) -> None:
        """Delete a voucher."""
        await async_execute_command(
            CommandFailure(
                translation_key="command_failed",
                message=f"Unable to delete voucher {voucher_id}",
            ),
            self.network_client.vouchers.delete,
            site_id,
            voucher_id,
        )
        latest = self._latest_vouchers.get(site_id)
        if latest is not None and latest.get("id") == voucher_id:
            del self._latest_vouchers[site_id]
        await self._async_refresh_vouchers(site_id)

    async def _async_refresh_vouchers(self, site_id: str) -> None:
        """Refresh a site's vouchers after change; never fail change itself."""
        try:
            await self._config_coordinator.async_refresh_vouchers(site_id)
        except UniFiAuthenticationError:
            self.config_entry.async_start_reauth(self.hass)
        except (UniFiError, RuntimeError) as err:
            _LOGGER.warning(
                "Unable to refresh the vouchers of site %s after a change: %s",
                site_id,
                err,
            )
        self._aggregate_data()
        self.async_update_listeners()

    async def async_get_floor_plan_image(
        self,
        plan_id: str,
        filename: str,
    ) -> tuple[bytes, str] | None:
        """Download floor plan image binary data and content type."""
        if not self.innerspace_client:
            return None
        return await self.innerspace_client.download_floor_plan_image(
            plan_id,
            filename,
        )
