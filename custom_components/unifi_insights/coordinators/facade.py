# Copyright 2026 UniFi Insights contributors
"""Facade coordinator providing backward-compatible unified data view."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

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
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from pydantic import ValidationError
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiError,
    UniFiGlobalAlarmManagerError,
    UniFiNotFoundError,
    UniFiRuleNotEditableError,
)
from unifi_official_api.exceptions import UniFiUnsupportedError
from unifi_official_api.protect.talkback import (
    TalkbackCodecError,
    TalkbackSamplingRateError,
    TalkbackSchemeError,
    TalkbackStream,
)
from unifi_official_api.validation import sanitized_validation_fields

from custom_components.unifi_insights.client_wake import (
    WAKE_ON_LAN_DOMAIN,
    WAKE_ON_LAN_SERVICE,
    derive_directed_broadcast,
    find_client_by_mac,
    first_non_blank_text,
    history_network_hint,
    select_wake_history,
)
from custom_components.unifi_insights.const import CONF_CONSOLE_ID, DOMAIN
from custom_components.unifi_insights.data_transforms import (
    correlate_innerspace_devices,
    normalize_innerspace_snapshot,
)
from custom_components.unifi_insights.topology_contract import (
    first_present,
    normalize_mac,
)

from .config_sections import rule_display_name, verified_legacy_site_name
from .rule_export import async_build_rule_export
from .voucher_state import VoucherSettings, refresh_latest_voucher, voucher_to_record

_LOGGER = logging.getLogger(__name__)

WAKE_BROADCAST_TIMEOUT_SECONDS: Final = 5


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

        Entity action handlers (see switch.py) call this right after a
        mutating API call and expect the coordinator to reflect the change
        by the time the await returns. Two deliberate departures from the
        obvious implementation make that true:

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
           one after another would make every action handler noticeably
           slower for no benefit, since the coordinators are independent.

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
            raise HomeAssistantError(msg)

    def _require_protect_client(self) -> UniFiProtectClient:
        """Return the Protect client or raise a user-facing error."""
        if self.protect_client is None:
            msg = "Protect is not available for this config"
            raise HomeAssistantError(msg)
        return self.protect_client

    async def _async_execute_api_action[ActionResult](
        self,
        error_message: str,
        action: Callable[..., Awaitable[ActionResult]],
        *args: Any,
        **kwargs: Any,
    ) -> ActionResult:
        """Execute an API action and convert failures to Home Assistant errors."""
        try:
            return await action(*args, **kwargs)
        except HomeAssistantError:
            raise
        except Exception as err:
            if isinstance(err, ValidationError):
                msg = (
                    f"{error_message}: Invalid API response data "
                    f"(fields: {sanitized_validation_fields(err)})"
                )
                _LOGGER.error("%s", msg)  # noqa: TRY400
                raise HomeAssistantError(msg) from None
            _LOGGER.exception("%s", error_message)
            raise HomeAssistantError(error_message) from err

    async def async_restart_device(self, site_id: str, device_id: str) -> bool:
        """Restart a network device."""
        return await self._async_execute_api_action(
            f"Unable to restart device {device_id}",
            self.network_client.devices.restart,
            site_id,
            device_id,
        )

    async def async_power_cycle_port(
        self, site_id: str, device_id: str, port_idx: int
    ) -> bool:
        """Power cycle a PoE port on a network device."""
        return await self._async_execute_api_action(
            f"Unable to power-cycle port {port_idx} on device {device_id}",
            self.network_client.devices.execute_port_action,
            site_id,
            device_id,
            port_idx,
            "POWER_CYCLE",
        )

    async def async_set_outlet_state(
        self,
        site_id: str,
        device_id: str,
        outlet_index: int,
        *,
        state: bool,
        cycle_enabled: bool | None = None,
    ) -> bool:
        """Set outlet relay state and/or cycle_enabled on a PDU device."""
        site_name = self._device_coordinator.get_legacy_site_name(site_id) or "default"
        device_data = self.get_device(site_id, device_id) or {}
        target_id = device_data.get("_id") or device_id

        return await self._async_execute_api_action(
            f"Unable to set outlet {outlet_index} state on device {device_id}",
            self.network_client.devices.set_outlet_state,
            site_name,
            target_id,
            outlet_index,
            state=state,
            cycle_enabled=cycle_enabled,
            current_device=device_data,
        )

    async def async_set_firewall_rule_enabled(
        self,
        site_id: str,
        rule_id: str,
        *,
        enabled: bool,
    ) -> None:
        """Enable or disable a firewall rule."""
        await self._async_execute_api_action(
            f"Unable to update firewall rule {rule_id}",
            self.network_client.firewall.update_rule,
            site_id,
            rule_id,
            enabled=enabled,
        )

    def resolve_legacy_site_name(self, site_id: str) -> str:
        """
        Resolve the classic site name for a facade (integration API) site id.

        Falls back to ``"default"`` (the standard single-site name) when the
        legacy site mapping has not resolved yet.
        """
        return self._device_coordinator.get_legacy_site_name(site_id) or "default"

    async def async_set_policy_based_route_enabled(
        self,
        site_id: str,
        route_id: str,
        *,
        enabled: bool,
    ) -> None:
        """Enable or disable a policy-based route (traffic route)."""
        site_name = self.resolve_legacy_site_name(site_id)
        await self._async_execute_api_action(
            f"Unable to update policy-based route {route_id}",
            self.network_client.routes.update_route,
            site_name,
            route_id,
            enabled=enabled,
        )

    async def async_set_vpn_client_enabled(
        self,
        site_id: str,
        client_id: str,
        *,
        enabled: bool,
    ) -> None:
        """Enable or disable a VPN client configuration."""
        site_name = self.resolve_legacy_site_name(site_id)
        await self._async_execute_api_action(
            f"Unable to update VPN client {client_id}",
            self.network_client.vpn_clients.update_vpn_client,
            site_name,
            client_id,
            enabled=enabled,
        )

    async def async_set_port_forward_enabled(
        self, site_id: str, rule_id: str, *, enabled: bool
    ) -> None:
        """Enable or disable a port forwarding rule."""
        rule_data = (
            self._config_coordinator.data.get("port_forwards", {})
            .get(site_id, {})
            .get(rule_id, {})
        )
        display_name = rule_display_name(rule_data, rule_id, "name")
        site_name = self._require_verified_legacy_site_name(site_id)
        try:
            await self.network_client.port_forwards.update_port_forward(
                site_name, rule_id, enabled=enabled
            )
        except UniFiNotFoundError as err:
            _LOGGER.warning(
                "Unable to update port forward %s: it no longer exists on the console",
                display_name,
            )
            await self.async_request_refresh()
            msg = f"Unable to update port forward {display_name}"
            raise HomeAssistantError(msg) from err
        except HomeAssistantError:
            raise
        except Exception as err:
            _LOGGER.exception("Unable to update port forward %s", display_name)
            msg = f"Unable to update port forward {display_name}"
            raise HomeAssistantError(msg) from err

    async def async_set_traffic_rule_enabled(
        self, site_id: str, rule_id: str, *, enabled: bool
    ) -> None:
        """Enable or disable a traffic rule."""
        rule_data = (
            self._config_coordinator.data.get("traffic_rules", {})
            .get(site_id, {})
            .get(rule_id, {})
        )
        display_name = rule_display_name(rule_data, rule_id, "description")
        site_name = self._require_verified_legacy_site_name(site_id)
        try:
            await self.network_client.traffic_rules.update_traffic_rule(
                site_name, rule_id, enabled=enabled
            )
        except UniFiNotFoundError as err:
            _LOGGER.warning(
                "Unable to update traffic rule %s: it no longer exists on the console",
                display_name,
            )
            await self.async_request_refresh()
            msg = f"Unable to update traffic rule {display_name}"
            raise HomeAssistantError(msg) from err
        except HomeAssistantError:
            raise
        except Exception as err:
            _LOGGER.exception("Unable to update traffic rule %s", display_name)
            msg = f"Unable to update traffic rule {display_name}"
            raise HomeAssistantError(msg) from err

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
    ) -> None:
        """Toggle one rule of a classic or v2 rule family by full-record PUT."""
        rule_data = (
            self._config_coordinator.data.get(data_key, {})
            .get(site_id, {})
            .get(rule_id, {})
        )
        display_name = rule_display_name(rule_data, rule_id, name_field)
        site_name = self._require_verified_legacy_site_name(site_id)
        msg = f"Unable to update {label} {display_name}"
        try:
            await update(site_name, rule_id, enabled=enabled)
        except UniFiNotFoundError as err:
            _LOGGER.warning("%s: it no longer exists on the console", msg)
            await self.async_request_refresh()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="network_rule_not_found",
                translation_placeholders={
                    "rule_type": label,
                    "rule_name": display_name,
                },
            ) from err
        except UniFiRuleNotEditableError as err:
            _LOGGER.warning("%s: it is managed by the console", msg)
            await self.async_request_refresh()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="network_rule_console_managed",
                translation_placeholders={
                    "rule_type": label,
                    "rule_name": display_name,
                },
            ) from err
        except HomeAssistantError:
            raise
        except Exception as err:
            _LOGGER.exception("%s", msg)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="network_rule_update_failed",
                translation_placeholders={
                    "rule_type": label,
                    "rule_name": display_name,
                },
            ) from err

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

    async def async_update_camera(self, camera_id: str, **kwargs: Any) -> None:
        """Update a camera via the Protect cameras endpoint."""
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to update camera {camera_id}",
            protect_client.cameras.update,
            camera_id,
            **kwargs,
        )

    async def async_update_camera_settings(
        self,
        camera_id: str,
        **kwargs: Any,
    ) -> None:
        """Update camera settings (alias for async_update_camera)."""
        await self.async_update_camera(camera_id, **kwargs)

    def _require_verified_legacy_site_name(self, site_id: str) -> str:
        """
        Resolve the verified classic site name for an integration site ID.

        Must resolve ONLY from the integration site's internalReference
        in the config coordinator. Raises HomeAssistantError if missing or blank.
        Never falls back to "default" or single-site mapping.
        """
        site = self._config_coordinator.get_site(site_id)
        site_name = verified_legacy_site_name(site)
        if not site_name:
            msg = f"No verified classic site name found for site {site_id}"
            raise HomeAssistantError(
                msg,
                translation_domain=DOMAIN,
                translation_key="legacy_site_not_found",
                translation_placeholders={"site_id": site_id},
            )
        return site_name

    def _resolve_client_action_target(
        self, site_id: str, client_id: str
    ) -> tuple[str, str]:
        """
        Resolve the classic site name and client MAC for a client action.

        The official Integration API has no block/unblock/reconnect/forget
        operations, so these are issued against the classic ``cmd/stamgr``
        endpoint, which is scoped by the classic site name and identifies the
        client by MAC address rather than the integration client UUID.
        """
        clients = self.data.get("clients", {}).get(site_id, {})
        client_data = clients.get(client_id, {}) if isinstance(clients, dict) else {}
        mac = (
            client_data.get("macAddress")
            or client_data.get("mac_address")
            or client_data.get("mac")
        )
        if not mac:
            msg = f"Unable to determine MAC address for client {client_id}"
            raise HomeAssistantError(msg)

        # Fall back to "default" (the standard single-site name) when the
        # legacy site mapping has not been resolved yet.
        site_name = self._device_coordinator.get_legacy_site_name(site_id) or "default"
        return site_name, mac

    async def async_unblock_client(self, site_id: str, client_id: str) -> None:
        """Unblock a network client."""
        site_name, mac = self._resolve_client_action_target(site_id, client_id)
        await self._async_execute_api_action(
            f"Unable to unblock client {client_id}",
            self.network_client.clients.unblock,
            site_name,
            mac,
        )

    async def async_block_client(self, site_id: str, client_id: str) -> None:
        """Block a network client."""
        site_name, mac = self._resolve_client_action_target(site_id, client_id)
        await self._async_execute_api_action(
            f"Unable to block client {client_id}",
            self.network_client.clients.block,
            site_name,
            mac,
        )

    async def async_reconnect_client(self, site_id: str, client_id: str) -> None:
        """Reconnect a network client."""
        site_name, mac = self._resolve_client_action_target(site_id, client_id)
        await self._async_execute_api_action(
            f"Unable to reconnect client {client_id}",
            self.network_client.clients.reconnect,
            site_name,
            mac,
        )

    async def async_forget_client(self, site_id: str, client_id: str) -> None:
        """Forget/remove a network client."""
        site_name, mac = self._resolve_client_action_target(site_id, client_id)
        await self._async_execute_api_action(
            f"Unable to forget client {client_id}",
            self.network_client.clients.forget,
            site_name,
            mac,
        )

    async def async_get_historical_clients(self, site_id: str) -> list[dict[str, Any]]:
        """Read historical client records for a site from the classic API."""
        site_name = self._require_verified_legacy_site_name(site_id)
        result = await self._async_execute_api_action(
            f"Unable to read historical clients for site {site_id}",
            self.network_client.clients.get_historical_legacy,
            site_name,
        )
        return result if isinstance(result, list) else []

    async def async_forget_clients_batch(self, site_id: str, macs: list[str]) -> bool:
        """Forget/remove a list of client MACs for a site."""
        site_name = self._require_verified_legacy_site_name(site_id)
        return await self._async_execute_api_action(
            f"Unable to forget clients for site {site_id}",
            self.network_client.clients.forget_batch,
            site_name,
            macs,
        )

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
                site_name = self._require_verified_legacy_site_name(site_id)
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

    def _resolve_wake_client_name(self, mac: str | None) -> str | None:
        """Resolve a friendly client name for error messages without exposing MAC."""
        if not mac:
            return None
        live = find_client_by_mac((self.data or {}).get("clients"), mac)
        if live:
            return first_non_blank_text(live[1], "name", "hostname")
        return None

    async def _async_derive_wake_broadcast(self, mac: str) -> str | None:
        """
        Return the client's subnet directed broadcast address, or None if unknown.

        Never raises: any failure means "unknown", so the packet goes to the
        default 255.255.255.255. A connected client with an IP needs one request
        (the site's networks). Any other client also reads the classic history for
        its last network, which makes it two.
        """
        live = find_client_by_mac((self.data or {}).get("clients"), mac)
        live_site_id = live[0] if live else None
        live_ip = (
            first_present(live[1], "ipAddress", "ip_address", "ip") if live else None
        )

        async def _query_site(site_id: str) -> str | None:
            try:
                site_name = self._require_verified_legacy_site_name(site_id)
                ip: str | None
                network_id: str | None
                if isinstance(live_ip, str) and live_ip:
                    networks = await self.network_client.networks.get_legacy_all(
                        site_name
                    )
                    ip, network_id = live_ip, None
                else:
                    networks, records = await asyncio.gather(
                        self.network_client.networks.get_legacy_all(site_name),
                        self.network_client.clients.get_historical_legacy(site_name),
                    )
                    ip, network_id = history_network_hint(records, mac)
                return derive_directed_broadcast(networks, ip=ip, network_id=network_id)
            except Exception:
                _LOGGER.debug(
                    "Wake-on-LAN: no broadcast address from site %s",
                    site_id,
                )
                return None

        if live_site_id:
            return await _query_site(live_site_id)

        site_ids = self._config_coordinator.get_site_ids()
        results = await asyncio.gather(*(_query_site(s) for s in site_ids))
        for broadcast in results:
            if broadcast is not None:
                return broadcast
        return None

    async def async_wake_client(
        self, mac: str, *, client_name: str | None = None
    ) -> None:
        """
        Send a Wake-on-LAN magic packet to a client through Home Assistant core.

        UniFi has no wake action, so this calls ``wake_on_lan.send_magic_packet``
        (loaded through this integration's manifest dependencies). The packet goes
        to the client's subnet directed broadcast when UniFi data allows deriving
        it, otherwise to the default 255.255.255.255.
        """
        normalized = normalize_mac(mac)
        display_name = (
            client_name
            or self._resolve_wake_client_name(normalized)
            or (f"Client {normalized}" if normalized else str(mac))
        )
        if normalized is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="wake_failed",
                translation_placeholders={
                    "client_name": display_name,
                    "error": "invalid MAC address",
                },
            )

        broadcast: str | None = None
        if self.config_available and self.device_available:
            try:
                async with asyncio.timeout(WAKE_BROADCAST_TIMEOUT_SECONDS):
                    broadcast = await self._async_derive_wake_broadcast(normalized)
            except TimeoutError:
                _LOGGER.debug("Wake-on-LAN: broadcast address derivation timed out")
            except Exception:
                _LOGGER.debug("Wake-on-LAN: broadcast address derivation failed")

        service_data: dict[str, str] = {"mac": normalized}
        if broadcast is not None:
            service_data["broadcast_address"] = broadcast
        try:
            await self.hass.services.async_call(
                WAKE_ON_LAN_DOMAIN,
                WAKE_ON_LAN_SERVICE,
                service_data,
                blocking=True,
            )
        except ServiceNotFound as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="wake_on_lan_unavailable",
            ) from err
        except HomeAssistantError:
            raise
        except (OSError, ValueError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="wake_failed",
                translation_placeholders={
                    "client_name": display_name,
                    "error": str(err),
                },
            ) from err
        _LOGGER.debug(
            "Sent Wake-on-LAN packet (directed broadcast: %s)", broadcast is not None
        )

    async def async_authorize_guest(self, site_id: str, client_id: str) -> None:
        """Authorize guest access for a network client."""
        await self._async_execute_api_action(
            f"Unable to authorize guest client {client_id}",
            self.network_client.clients.authorize_guest,
            site_id,
            client_id,
        )

    async def async_unauthorize_guest(self, site_id: str, client_id: str) -> None:
        """Remove guest authorization for a network client."""
        await self._async_execute_api_action(
            f"Unable to unauthorize guest client {client_id}",
            self.network_client.clients.unauthorize_guest,
            site_id,
            client_id,
        )

    async def async_update_wifi_network(
        self,
        site_id: str,
        wifi_id: str,
        *,
        enabled: bool,
    ) -> None:
        """Enable or disable a WiFi network."""
        await self._async_execute_api_action(
            f"Unable to update WiFi network {wifi_id}",
            self.network_client.wifi.update,
            site_id,
            wifi_id,
            enabled=enabled,
        )

    async def async_play_chime(self, chime_id: str) -> None:
        """Report the official API's chime playback limitation."""
        protect_client = self._require_protect_client()

        async def play() -> None:
            try:
                await protect_client.chimes.play(chime_id)
            except UniFiUnsupportedError as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="chime_playback_unsupported",
                ) from err

        await self._async_execute_api_action(
            f"Unable to play chime {chime_id}",
            play,
        )

    async def async_start_ptz_patrol(self, camera_id: str, slot: int) -> None:
        """Start PTZ patrol for a camera."""
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to start PTZ patrol for camera {camera_id}",
            protect_client.cameras.ptz_patrol_start,
            camera_id,
            slot,
        )

    async def async_stop_ptz_patrol(self, camera_id: str) -> None:
        """Stop PTZ patrol for a camera."""
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to stop PTZ patrol for camera {camera_id}",
            protect_client.cameras.ptz_patrol_stop,
            camera_id,
        )

    async def async_set_hdr_mode(self, camera_id: str, mode: str) -> None:
        """Set HDR mode for a camera."""
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to set HDR mode for camera {camera_id}",
            protect_client.cameras.set_hdr_mode,
            camera_id,
            mode,
        )

    async def async_set_video_mode(self, camera_id: str, mode: str) -> None:
        """Set video mode for a camera."""
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to set video mode for camera {camera_id}",
            protect_client.cameras.set_video_mode,
            camera_id,
            mode,
        )

    async def async_set_chime_ringtone(self, chime_id: str, ringtone_id: str) -> None:
        """Set ringtone for a chime."""
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to set ringtone for chime {chime_id}",
            protect_client.chimes.set_ringtone,
            chime_id,
            ringtone_id,
        )
        if self._protect_coordinator is not None:
            try:
                await self._protect_coordinator.async_request_refresh()
            except Exception:
                _LOGGER.exception(
                    "Chime ringtone was updated, but the Protect refresh failed"
                )

    async def async_move_ptz_to_preset(self, camera_id: str, preset: int) -> None:
        """Move a PTZ camera to a preset."""
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to move camera {camera_id} to PTZ preset {preset}",
            protect_client.cameras.ptz_goto_preset,
            camera_id,
            str(preset),
        )

    async def async_update_viewer(self, viewer_id: str, **kwargs: Any) -> None:
        """Update viewer settings."""
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to update viewer {viewer_id}",
            protect_client.viewers.update,
            viewer_id,
            **kwargs,
        )

    async def async_set_microphone_volume(self, camera_id: str, volume: int) -> None:
        """Set microphone volume for a camera."""
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to set microphone volume for camera {camera_id}",
            protect_client.cameras.set_microphone_volume,
            camera_id,
            volume,
        )

    async def async_start_camera_talkback(
        self, camera_id: str, media_url: str
    ) -> TalkbackStream:
        """
        Start talkback audio streaming to a camera.

        Args:
            camera_id: Camera identifier.
            media_url: Resolved media URL or local path.

        Returns:
            Running TalkbackStream instance.

        Raises:
            HomeAssistantError: If Protect is unavailable, session creation fails,
                or parameters/streaming fail.

        """
        if self.protect_client is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="protect_not_available_for_talkback",
            )

        try:
            session = await self.protect_client.cameras.create_talkback_session(
                camera_id
            )
        except Exception as err:
            detail = (
                f"invalid fields: {sanitized_validation_fields(err)}"
                if isinstance(err, ValidationError)
                else type(err).__name__
            )
            _LOGGER.warning(
                "Failed to create talkback session for camera %s: %s",
                camera_id,
                detail,
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="talkback_session_invalid",
            ) from err

        try:
            stream = TalkbackStream(session, media_url, loop=self.hass.loop)
            await stream.start()
            return stream
        except (
            TalkbackSchemeError,
            TalkbackCodecError,
            TalkbackSamplingRateError,
        ) as err:
            _LOGGER.warning(
                "Unsupported talkback parameters for camera %s: %s",
                camera_id,
                err,
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="talkback_unsupported_codec_or_scheme",
            ) from err
        except Exception as err:
            _LOGGER.warning(
                "Failed to start talkback stream for camera %s: %s",
                camera_id,
                err,
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="talkback_stream_failure",
            ) from err

    async def async_set_light_brightness(self, light_id: str, led_level: int) -> None:
        """Set light brightness for a Protect light."""
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to set brightness for light {light_id}",
            protect_client.lights.set_brightness,
            light_id,
            led_level,
        )

    async def async_set_light_mode(self, light_id: str, mode: str) -> None:
        """Set mode for a Protect light."""
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to set mode for light {light_id}",
            protect_client.lights.update,
            light_id,
            lightModeSettings={"mode": mode},
        )

    async def async_set_chime_volume(self, chime_id: str, volume: int) -> None:
        """Set volume for a chime."""
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to set volume for chime {chime_id}",
            protect_client.chimes.set_volume,
            chime_id,
            volume,
        )

    async def async_set_chime_repeat(
        self,
        chime_id: str,
        repeat_times: int,
    ) -> None:
        """Set repeat count for a chime."""
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to set repeat count for chime {chime_id}",
            protect_client.chimes.set_repeat_times,
            chime_id,
            repeat_times,
        )

    async def async_set_chime_paired_cameras(
        self,
        chime_id: str,
        camera_ids: list[str],
    ) -> None:
        """
        Replace the doorbell cameras paired to a chime.

        An empty list unpairs every doorbell. The request body is exactly
        {"cameraIds": [...]}: the spec's chime PATCH sets
        additionalProperties to false, so nothing else may ride along. The
        refresh afterwards keeps the cached chimes current. A refresh that
        fails does not fail the action: the coordinator records the failure
        itself instead of raising it, and the pairing has been made.
        """
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to set the paired doorbells of chime {chime_id}",
            protect_client.chimes.update,
            chime_id,
            cameraIds=list(camera_ids),
        )
        if self._protect_coordinator is not None:
            await self._protect_coordinator.async_request_refresh()

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
        created = await self._async_execute_api_action(
            f"Unable to generate voucher in site {site_id}",
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
        await self._async_execute_api_action(
            f"Unable to delete voucher {voucher_id}",
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

    async def async_trigger_alarm(self, alarm_id: str) -> None:
        """Trigger an alarm."""
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to trigger alarm {alarm_id}",
            protect_client.application.trigger_alarm_webhook,
            alarm_id,
        )

    async def async_arm_alarm(self) -> None:
        """Arm the console's alarm with its currently selected arm profile."""
        await self._async_send_arm_command(enable=True)

    async def async_disarm_alarm(self) -> None:
        """Disarm the console's alarm."""
        await self._async_send_arm_command(enable=False)

    async def _async_send_arm_command(self, *, enable: bool) -> None:
        """
        Send an arm or disarm request, then ask Protect for fresh state.

        While the UniFi global alarm manager owns the alarm, Protect refuses
        both commands with a 400. That is reported as a translated error
        rather than the generic "Unable to ..." message. There is no
        optimistic state: the NVR's WebSocket frame normally arrives first,
        and the debounced refresh is the fallback.
        """
        protect_client = self._require_protect_client()
        command = (
            protect_client.arm_profiles.enable
            if enable
            else protect_client.arm_profiles.disable
        )

        async def _send() -> bool:
            try:
                return await command()
            except UniFiGlobalAlarmManagerError as err:
                # The refusal is also the answer to whether the manager is on,
                # e.g. when it was switched on since it was last asked.
                if self._protect_coordinator is not None:
                    self._protect_coordinator.mark_global_alarm_manager_enabled()
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="global_alarm_manager",
                ) from err

        await self._async_execute_api_action(
            f"Unable to {'arm' if enable else 'disarm'} the UniFi Protect alarm",
            _send,
        )
        if self._protect_coordinator is not None:
            await self._protect_coordinator.async_request_refresh()

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

    async def async_create_liveview(
        self,
        *,
        name: str,
        layout: int,
        is_default: bool = False,
    ) -> None:
        """Create a liveview."""
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to create liveview {name}",
            protect_client.liveviews.create,
            name=name,
            layout=layout,
            isDefault=is_default,
        )

    async def async_play_siren(
        self, siren_id: str, *, duration: int | None = None
    ) -> None:
        """Sound a siren, optionally for 5, 10, 20 or 30 seconds."""
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to sound siren {siren_id}",
            protect_client.sirens.play,
            siren_id,
            duration=duration,
        )

    async def async_set_siren_volume(self, siren_id: str, volume: int) -> None:
        """Set a siren's volume (1-100)."""
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to set volume for siren {siren_id}",
            protect_client.sirens.set_volume,
            siren_id,
            volume,
        )

    async def async_stop_siren(self, siren_id: str) -> None:
        """
        Stop a siren and show it off right away.

        Protect sends no WebSocket frame after a manual stop, so the Protect
        coordinator's cache is updated here instead of waiting for the poll.
        """
        protect_client = self._require_protect_client()
        await self._async_execute_api_action(
            f"Unable to stop siren {siren_id}",
            protect_client.sirens.stop,
            siren_id,
        )
        if self._protect_coordinator is not None:
            self._protect_coordinator.mark_siren_stopped(siren_id)
