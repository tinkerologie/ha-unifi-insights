# Copyright 2026 UniFi Insights contributors
"""Facade coordinator providing backward-compatible unified data view."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, NotRequired, ReadOnly, TypedDict, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from collections.abc import Set as AbstractSet

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from unifi_official_api.innerspace import UniFiInnerSpaceClient
    from unifi_official_api.network import UniFiNetworkClient
    from unifi_official_api.protect import UniFiProtectClient

    from .config import UnifiInsightsConfigCoordinator
    from .device import UnifiInsightsDeviceCoordinator
    from .innerspace import UnifiInsightsInnerSpaceCoordinator
    from .protect import UnifiInsightsProtectCoordinator
    from .site_manager import UnifiInsightsSiteManagerCoordinator

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.util.read_only_dict import ReadOnlyDict

from custom_components.unifi_insights.client_wake import select_wake_history
from custom_components.unifi_insights.const import CONF_CONSOLE_ID, DOMAIN
from custom_components.unifi_insights.data_transforms import (
    NETWORK_FACTS_KEY,
    NetworkDeviceFacts,
    SiteNetworkFacts,
    build_innerspace_view,
    device_facts,
    get_site_gateway_id,
    innerspace_correlation_key,
    innerspace_view_inputs,
)

from .base import CommandFailure, async_execute_command
from .rule_audit import async_build_rule_export

_LOGGER = logging.getLogger(__name__)

# Sections keyed by site ID whose records are mappings.
type SiteSection = Mapping[str, Mapping[str, Any]]

# The per-site config sections whose last fetch the config coordinator tracks.
SITE_SECTIONS: Final = (
    "wifi",
    "firewall_rules",
    "vouchers",
    "port_forwards",
    "traffic_rules",
    "nat_rules",
    "static_routes",
    "internet_activity",
)


class FacadeAvailability(TypedDict):
    """
    The availability one snapshot was built with.

    ``sites`` maps each site ID to whether each of ``SITE_SECTIONS`` was
    fetched on the last config refresh. A site it does not list has the
    config coordinator's availability.
    """

    device: ReadOnly[bool]
    config: ReadOnly[bool]
    protect: ReadOnly[bool]
    innerspace: ReadOnly[bool]
    network: ReadOnly[bool]
    sites: ReadOnly[Mapping[str, Mapping[str, bool]]]


class FacadeData(TypedDict):
    """
    The facade's published snapshot (ADR-013).

    Every key is read-only and every section is a ``Mapping``, so a write into
    the snapshot is a strict-mypy error. At runtime the root is a
    ``ReadOnlyDict``; the sections are the owners' own objects, passed through
    by reference and never copied (the WebSocket streams compare them by
    identity).
    """

    # From the config coordinator
    sites: ReadOnly[SiteSection]
    wifi: ReadOnly[SiteSection]
    vouchers: ReadOnly[SiteSection]
    latest_vouchers: ReadOnly[SiteSection]
    firewall_rules: ReadOnly[SiteSection]
    policy_based_routes: ReadOnly[SiteSection]
    vpn_clients: ReadOnly[SiteSection]
    port_forwards: ReadOnly[SiteSection]
    traffic_rules: ReadOnly[SiteSection]
    nat_rules: ReadOnly[SiteSection]
    static_routes: ReadOnly[SiteSection]
    site_vpns: ReadOnly[SiteSection]
    rule_changes: ReadOnly[Mapping[str, Any]]
    network_info: ReadOnly[Mapping[str, Any]]
    client_links: ReadOnly[SiteSection]
    internet_activity: ReadOnly[SiteSection]
    internet_activity_unavailable: ReadOnly[AbstractSet[str]]
    # From the device coordinator
    devices: ReadOnly[SiteSection]
    clients: ReadOnly[SiteSection]
    stats: ReadOnly[SiteSection]
    vpn_connections: ReadOnly[Mapping[str, Any]]
    # Access point radio settings: site ID -> device ID -> {radios, available}
    radio_details: ReadOnly[SiteSection]
    # None until the device coordinator publishes it; readers then build the
    # facts from the raw devices and stats.
    network_facts: ReadOnly[Mapping[str, SiteNetworkFacts] | None]
    # From the Protect coordinator, or an empty stand-in without one
    protect: ReadOnly[Mapping[str, Any]]
    # The InnerSpace view, derived from the InnerSpace, device and Protect data
    innerspace: ReadOnly[Mapping[str, Any]]
    last_update: ReadOnly[datetime]
    availability: ReadOnly[FacadeAvailability]
    # Only with a Site Manager coordinator
    site_manager: ReadOnly[NotRequired[Mapping[str, Any]]]


# The config coordinator's sections, passed through by reference.
_CONFIG_SECTIONS: Final = (
    "sites",
    "wifi",
    "vouchers",
    "latest_vouchers",
    "firewall_rules",
    "policy_based_routes",
    "vpn_clients",
    "port_forwards",
    "traffic_rules",
    "nat_rules",
    "static_routes",
    "site_vpns",
    "rule_changes",
    "network_info",
    "client_links",
    "internet_activity",
)
# The device coordinator's sections, passed through by reference.
_DEVICE_SECTIONS: Final = (
    "devices",
    "clients",
    "stats",
    "vpn_connections",
    "radio_details",
)

# The Protect data of a console without a Protect coordinator. Built once, so
# its identity is stable across rebuilds and the Protect streams never see a
# change.
EMPTY_PROTECT: Final[Mapping[str, Mapping[str, Any]]] = ReadOnlyDict(
    {
        collection: ReadOnlyDict()
        for collection in (
            "cameras",
            "lights",
            "sensors",
            "nvrs",
            "viewers",
            "chimes",
            "fobs",
            "link_stations",
            "alarm_hubs",
            "sirens",
            "doorlocks",
            "viewports",
            "liveviews",
            "protect_info",
            "events",
        )
    }
)


def capture_availability(
    *,
    device: bool,
    config: bool,
    protect: bool,
    innerspace: bool,
    network: bool,
    failed_sections: AbstractSet[tuple[str, str]],
    site_ids: Iterable[str],
) -> FacadeAvailability:
    """
    Capture the owners' availability for one snapshot.

    A section of a site is available when the config coordinator is and the
    section was not among ``failed_sections`` on its last refresh, as the
    config coordinator's ``*_available(site_id)`` methods answer.
    """
    sites = {*site_ids, *(site_id for _, site_id in failed_sections)}
    availability = {
        "device": device,
        "config": config,
        "protect": protect,
        "innerspace": innerspace,
        "network": network,
        "sites": ReadOnlyDict(
            {
                site_id: ReadOnlyDict(
                    {
                        section: config and (section, site_id) not in failed_sections
                        for section in SITE_SECTIONS
                    }
                )
                for site_id in sites
            }
        ),
    }
    return cast("FacadeAvailability", ReadOnlyDict(availability))


def build_facade_data(
    config: Mapping[str, Any],
    device: Mapping[str, Any],
    protect: Mapping[str, Any] | None,
    innerspace: Mapping[str, Any],
    *,
    availability: FacadeAvailability,
    site_manager: Mapping[str, Any] | None,
    site_manager_host_id: str | None,
    now: datetime,
) -> FacadeData:
    """
    Build one facade snapshot from the owners' published data.

    Pure: it reads only its arguments and copies no section. ``protect`` is
    None without a Protect coordinator, ``innerspace`` is the derived
    InnerSpace view, ``availability`` is what ``capture_availability``
    captured, and ``site_manager`` is the Site Manager account data, or None
    without a Site Manager coordinator.
    """
    data: dict[str, Any] = {
        **{section: config.get(section, {}) for section in _CONFIG_SECTIONS},
        "internet_activity_unavailable": config.get(
            "internet_activity_unavailable", set()
        ),
        **{section: device.get(section, {}) for section in _DEVICE_SECTIONS},
        # None (never published) makes the readers build the facts from the
        # raw devices and stats.
        NETWORK_FACTS_KEY: device.get(NETWORK_FACTS_KEY),
        "protect": EMPTY_PROTECT if protect is None else protect,
        "innerspace": innerspace,
        "last_update": now,
        "availability": availability,
    }
    if site_manager is not None:
        data["site_manager"] = {
            **site_manager,
            "selected_host_id": (
                site_manager_host_id
                if site_manager_host_id in site_manager["hosts"]
                else None
            ),
        }
    return cast("FacadeData", ReadOnlyDict(data))


class UnifiInsightsFacadeCoordinator(DataUpdateCoordinator[FacadeData]):
    """
    Facade coordinator providing unified data view for backward compatibility.

    This coordinator aggregates data from the specialized coordinators
    (config, device, protect) and presents a unified interface that matches
    the original single-coordinator structure. This allows existing entity
    classes to work without modifications.

    ``data`` is a read-only ``FacadeData`` snapshot that ``build_facade_data``
    rebuilds on every owner update (ADR-013).
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        network_client: UniFiNetworkClient,
        protect_client: UniFiProtectClient | None,
        entry: ConfigEntry,
        config_coordinator: UnifiInsightsConfigCoordinator,
        *,
        device_coordinator: UnifiInsightsDeviceCoordinator,
        protect_coordinator: UnifiInsightsProtectCoordinator | None,
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

        # Remove-callbacks returned by async_add_listener() below, released
        # in async_shutdown() so this facade's forwarding listener doesn't
        # outlive it on the sub-coordinators (see _setup_listeners).
        self._sub_coordinator_unsubs: list[Callable[[], None]] = []
        # The InnerSpace view memo: the objects the view was built from, held
        # (not their id()) so a freed object's address cannot match a new one.
        self._innerspace_inputs: tuple[object, ...] | None = None
        self._innerspace_key: tuple[Any, ...] | None = None
        # The last captured availability and what it was captured from.
        self._availability_key: tuple[Any, ...] | None = None
        self._availability: FacadeAvailability | None = None
        self._innerspace_view_memo: Mapping[str, Any] = {}

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

    def _innerspace_view(
        self, devices: Any, protect_data: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """
        Return the InnerSpace view, rebuilt only when what it reads changed.

        A derived memo, not domain state (ADR-013), checked in two steps:

        1. The identity of the InnerSpace data, the ``devices`` section and
           each correlated Protect collection, all published copy-on-write.
           A Protect frame that replaces only ``events``, or a collection the
           view does not read, costs nothing.
        2. When one of them was replaced, the device fields the correlation
           reads (``innerspace_correlation_key``). A Protect frame replaces
           ``cameras`` on every camera update and a device poll replaces
           ``devices``, but the MACs rarely change, and comparing them costs
           what the previous MAC key did on every rebuild, where correlating
           costs about ten times more.
        """
        raw = (
            self._innerspace_coordinator.data if self._innerspace_coordinator else None
        )
        inputs = innerspace_view_inputs(raw, devices, protect_data)
        memo = self._innerspace_inputs
        if (
            memo is not None
            and len(memo) == len(inputs)
            and all(old is new for old, new in zip(memo, inputs, strict=True))
        ):
            return self._innerspace_view_memo
        key = innerspace_correlation_key(devices, protect_data) if raw else None
        if memo is None or memo[0] is not raw or key != self._innerspace_key:
            self._innerspace_view_memo = build_innerspace_view(
                raw, devices, protect_data
            )
            self._innerspace_key = key
        self._innerspace_inputs = inputs
        return self._innerspace_view_memo

    def _capture_availability(self) -> FacadeAvailability:
        """
        Capture the availability the delegating properties report now.

        The last capture is reused while every input is equal, so most
        rebuilds (a Protect frame, a device poll) publish the same object.
        """
        key = (
            bool(self.device_available),
            bool(self.config_available),
            bool(self.protect_available),
            bool(self.innerspace_available),
            bool(self.network_available),
            self._config_coordinator.failed_sections,
            tuple(self._config_coordinator.data.get("sites", {})),
        )
        if self._availability is None or key != self._availability_key:
            device, config, protect, innerspace, network, failed, site_ids = key
            self._availability = capture_availability(
                device=device,
                config=config,
                protect=protect,
                innerspace=innerspace,
                network=network,
                failed_sections=failed,
                site_ids=site_ids,
            )
            self._availability_key = key
        return self._availability

    def _aggregate_data(self) -> None:
        """Publish a new snapshot built from the owners' current data."""
        device = self._device_coordinator.data
        protect = self._protect_coordinator.data if self._protect_coordinator else None
        self.data = build_facade_data(
            self._config_coordinator.data,
            device,
            protect,
            self._innerspace_view(
                device.get("devices", {}),
                EMPTY_PROTECT if protect is None else protect,
            ),
            availability=self._capture_availability(),
            site_manager=(
                self._site_manager_coordinator.data
                if self._site_manager_coordinator
                else None
            ),
            site_manager_host_id=self._site_manager_host_id,
            now=datetime.now(tz=UTC),
        )

    def get_device_facts(
        self, site_id: str, device_id: str
    ) -> NetworkDeviceFacts | None:
        """Return the read-model facts of one network device, or None."""
        return device_facts(self.data, site_id, device_id)

    def get_site_gateway_id(self, site_id: str) -> str | None:
        """Return the ID of a site's gateway device, or None."""
        return get_site_gateway_id(self.data, site_id)

    @property
    def config_coordinator(self) -> UnifiInsightsConfigCoordinator:
        """Return the owner of the rule, Wi-Fi, guest and voucher commands (ADR-010)."""
        return self._config_coordinator

    @property
    def device_coordinator(self) -> UnifiInsightsDeviceCoordinator:
        """Return the owner of the device, outlet and client commands (ADR-010)."""
        return self._device_coordinator

    def require_protect_coordinator(self) -> UnifiInsightsProtectCoordinator:
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

    async def _async_update_data(self) -> FacadeData:
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
