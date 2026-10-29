# Copyright 2026 UniFi Insights contributors
"""Support for UniFi Insights device tracker."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.device_tracker import (
    ScannerEntity,
    SourceType,
    TrackerEntity,
)
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .console_identity import async_get_device_entry
from .const import (
    CONF_TRACK_CLIENTS,
    CONF_TRACK_WIFI_CLIENTS,
    CONF_TRACK_WIRED_CLIENTS,
    DEFAULT_TRACK_CLIENTS,
    DOMAIN,
    MANUFACTURER,
    MOBILITY_DEVICE_PREFIX,
)
from .coordinators import UnifiInsightsFacadeCoordinator
from .data_transforms import client_is_connected, section_by_site
from .entity import (
    UnifiMobilityRouterEntity,
    async_add_mobility_router_entities,
    get_field,
)
from .entity import get_client_type as _get_client_type

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import UnifiInsightsConfigEntry
    from .coordinators.mobility import UnifiInsightsMobilityCoordinator

_LOGGER = logging.getLogger(__name__)

# Coordinator handles updates centrally
PARALLEL_UPDATES = 0


def _client_should_be_tracked(
    client_data: dict[str, Any],
    *,
    track_wifi: bool,
    track_wired: bool,
) -> bool:
    """Return True if a connected client should be tracked per the options."""
    client_type = _get_client_type(client_data)
    if client_type == "WIRELESS":
        return track_wifi
    if client_type == "WIRED":
        return track_wired
    # Unknown type: track only if any tracking is enabled.
    return track_wifi or track_wired


def _partition_connected_clients(
    coordinator: UnifiInsightsFacadeCoordinator,
    *,
    track_wifi: bool,
    track_wired: bool,
) -> tuple[dict[str, str], set[str]]:
    """Split connected clients into tracked (MAC -> site_id) and untracked MACs."""
    wanted: dict[str, str] = {}
    untracked: set[str] = set()
    clients_by_site = section_by_site(coordinator.data, "clients") or {}
    for site_id, clients in clients_by_site.items():
        for client_data in clients.values():
            mac = get_field(client_data, "macAddress", "mac_address", "mac", default="")
            if not mac:
                continue
            if _client_should_be_tracked(
                client_data, track_wifi=track_wifi, track_wired=track_wired
            ):
                wanted[mac.lower()] = site_id
            else:
                untracked.add(mac.lower())
    return wanted, untracked


def _connected_clients_to_track(
    coordinator: UnifiInsightsFacadeCoordinator,
    *,
    track_wifi: bool,
    track_wired: bool,
) -> dict[str, str]:
    """Map MAC (lowercase) -> site_id for connected clients that should track."""
    wanted, _ = _partition_connected_clients(
        coordinator, track_wifi=track_wifi, track_wired=track_wired
    )
    return wanted


def _client_tracker_entries(
    registry: er.EntityRegistry, entry_id: str
) -> list[er.RegistryEntry]:
    """Return this platform's client tracker entries for a config entry."""
    # Mobility router GPS trackers share the platform but are not clients:
    # client-tracking options must never remove them.
    return [
        reg_entry
        for reg_entry in er.async_entries_for_config_entry(registry, entry_id)
        if reg_entry.domain == "device_tracker"
        and reg_entry.platform == DOMAIN
        and not reg_entry.unique_id.startswith(MOBILITY_DEVICE_PREFIX)
    ]


def _mac_from_unique_id(unique_id: str) -> str:
    """
    Return the lowercase MAC a client tracker's unique_id identifies.

    Entries written by this platform are prefixed; entries predating the
    prefix are the bare MAC as the API spelled it.
    """
    prefix = f"{DOMAIN}_"
    if unique_id.startswith(prefix):
        return unique_id[len(prefix) :].lower()
    return unique_id.lower()


def _migrate_tracker_unique_ids(
    registry: er.EntityRegistry, client_trackers: list[er.RegistryEntry]
) -> None:
    """
    Re-key trackers registered under the bare MAC to the MAC-derived id.

    Before this platform declared its own `unique_id`, `ScannerEntity` supplied
    it from the live `mac_address`, so existing entries are keyed by the raw MAC
    as the API spelled it. Rename them in place rather than letting them be
    orphaned, which would lose the user's name, area and entity_id.
    """
    prefix = f"{DOMAIN}_"
    for reg_entry in client_trackers:
        if reg_entry.unique_id.startswith(prefix):
            continue
        new_unique_id = f"{prefix}{reg_entry.unique_id.lower()}"
        existing_entity_id = registry.async_get_entity_id(
            "device_tracker", DOMAIN, new_unique_id
        )
        if existing_entity_id is not None:
            _LOGGER.warning(
                "Cannot migrate client tracker %s to unique_id %s: "
                "already in use by %s",
                reg_entry.entity_id,
                new_unique_id,
                existing_entity_id,
            )
            continue
        _LOGGER.debug(
            "Migrating client tracker %s unique_id %s -> %s",
            reg_entry.entity_id,
            reg_entry.unique_id,
            new_unique_id,
        )
        registry.async_update_entity(reg_entry.entity_id, new_unique_id=new_unique_id)


def _restored_name(
    hass: HomeAssistant, reg_entry: er.RegistryEntry, mac: str
) -> str | None:
    """
    Return the display name a retained tracker should keep.

    A tracker that last came up standalone has no entity name of its own -- the
    `client_<mac>` device carries it -- so `original_name` is None and reading
    only that would rename the client to a "Client <mac>" placeholder on its
    second consecutive offline start. Fall back to the device registry, which
    is where that name actually lives.
    """
    if reg_entry.original_name is not None:
        return reg_entry.original_name
    # Resolved lazily: the device registry is only consulted for the minority of
    # entries that have no entity name of their own.
    device = async_get_device_entry(
        dr.async_get(hass),
        (DOMAIN, f"client_{mac}"),
        reg_entry.config_entry_id,
    )
    if device is None:
        return None
    return device.name_by_user or device.name


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up device tracker for UniFi Insights integration."""
    coordinator = entry.runtime_data.coordinator

    # Router trackers do not depend on the client-tracking options below.
    if mobility_coordinator := entry.runtime_data.mobility_coordinator:
        async_setup_mobility_trackers(
            hass, entry, mobility_coordinator, async_add_entities
        )

    # Check which client types to track (support both old and new options).
    # Migrate from the old single option if the new options are not set.
    old_track_clients = entry.options.get(CONF_TRACK_CLIENTS, DEFAULT_TRACK_CLIENTS)
    track_wifi = entry.options.get(CONF_TRACK_WIFI_CLIENTS, old_track_clients)
    track_wired = entry.options.get(CONF_TRACK_WIRED_CLIENTS, old_track_clients)

    _LOGGER.debug("Client tracking - WiFi: %s, Wired: %s", track_wifi, track_wired)

    # Reconcile the entity registry with the current options. This runs on every
    # setup, including option-change reloads.
    #
    # Only remove a tracker when the configuration says it is unwanted: either
    # tracking is off entirely, or the client is currently connected with a type
    # that is no longer tracked.
    #
    # A client merely missing from the connected-client snapshot is NOT evidence
    # that its tracker should go -- it may be powered off or roaming, or the poll
    # behind `coordinator.data` may have failed or not run yet. Removing the
    # registry entry is permanent and destroys the user's name, area, and
    # entity_id customisation, and reporting `not_home` for an absent device is
    # the entire purpose of a device tracker.
    registry = er.async_get(hass)
    client_trackers = _client_tracker_entries(registry, entry.entry_id)

    if not track_wifi and not track_wired:
        for reg_entry in client_trackers:
            _LOGGER.debug(
                "Removing client tracker %s (client tracking disabled)",
                reg_entry.entity_id,
            )
            registry.async_remove(reg_entry.entity_id)
        _LOGGER.debug("Client tracking disabled - no client trackers created")
        return

    # Re-key legacy entries before anything compares unique_ids, so the
    # reconciliation below and the retained trackers all speak the same
    # identifier. Deliberately after the early return above: entries that are
    # about to be removed are not worth renaming first.
    _migrate_tracker_unique_ids(registry, client_trackers)
    client_trackers = _client_tracker_entries(registry, entry.entry_id)

    connected_macs, untracked_macs = _partition_connected_clients(
        coordinator, track_wifi=track_wifi, track_wired=track_wired
    )
    surviving: list[er.RegistryEntry] = []
    for reg_entry in client_trackers:
        if _mac_from_unique_id(reg_entry.unique_id) in untracked_macs:
            _LOGGER.debug(
                "Removing client tracker %s (client type no longer tracked)",
                reg_entry.entity_id,
            )
            registry.async_remove(reg_entry.entity_id)
            continue
        surviving.append(reg_entry)

    # Per-setup dedup set (recreated on every reload so re-enabling re-adds
    # entities); MAC is globally unique so it is used as the key.
    tracked: set[str] = set()

    # Every surviving registry entry gets a live entity, even when its client is
    # absent from the current snapshot. A registry entry with no entity behind it
    # is restored as "unavailable" and stays that way until the client happens to
    # reconnect; adding the entity now makes it report `not_home` instead, which
    # is what a device tracker is for. Seeding `tracked` here is what stops
    # `async_add_clients` adding a second entity with the same unique_id when a
    # retained client comes back.
    retained: list[UnifiClientTracker] = []
    for reg_entry in surviving:
        mac = _mac_from_unique_id(reg_entry.unique_id)
        retained.append(
            UnifiClientTracker(
                coordinator=coordinator,
                mac=mac,
                site_id=connected_macs.get(mac),
                restored_name=_restored_name(hass, reg_entry, mac),
                unique_id=reg_entry.unique_id,
            )
        )
        tracked.add(mac)
    if retained:
        _LOGGER.debug("Restoring %d client tracker(s) from the registry", len(retained))
        async_add_entities(retained)

    @callback
    def async_add_clients() -> None:
        """Add trackers for currently connected clients of the enabled types."""
        current = _connected_clients_to_track(
            coordinator, track_wifi=track_wifi, track_wired=track_wired
        )
        entities = [
            UnifiClientTracker(coordinator=coordinator, mac=mac, site_id=site_id)
            for mac, site_id in current.items()
            if mac not in tracked
        ]
        tracked.update(current)
        if entities:
            async_add_entities(entities)

    # Initial setup
    async_add_clients()

    # Listen for new clients connecting later
    entry.async_on_unload(coordinator.async_add_listener(async_add_clients))


class UnifiClientTracker(
    CoordinatorEntity[UnifiInsightsFacadeCoordinator], ScannerEntity
):
    """Representation of a UniFi network client."""

    _attr_has_entity_name = True
    # Home Assistant 2026.11 creates every ScannerEntity disabled; before, a
    # tracker with device info was created enabled. Client tracking is off
    # until the user turns it on for a connection type, so the trackers it
    # creates stay enabled. A tracker the user disabled stays disabled.
    _attr_entity_registry_enabled_default = True

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        mac: str,
        site_id: str | None = None,
        restored_name: str | None = None,
        unique_id: str | None = None,
    ) -> None:
        """
        Initialize the tracker.

        `site_id` is only a starting hint and may be None for a tracker restored
        from the registry, which knows the MAC but not where it last connected.
        `restored_name` is the name the registry kept for such a tracker.
        `unique_id` overrides the default DOMAIN_mac id format, used when
        restoring legacy entries that could not be migrated due to collision.
        """
        super().__init__(coordinator)
        self._site_id = site_id
        self._mac = mac.lower()

        # The MAC identifies the client and is known for every tracker, whether
        # or not the client is currently connected. Reading it back out of the
        # live payload -- as this platform used to -- returns None for exactly
        # the absent clients a restored tracker exists to represent, which drops
        # the `mac` state attribute while the device is away and skips the
        # MAC registration `ScannerEntity.add_to_platform_start` performs.
        self._attr_mac_address = self._mac

        # Get initial client data
        client_data = self._get_client_data() or {}

        # Set unique ID based on MAC address for stability
        self._attr_unique_id = unique_id or f"{DOMAIN}_{self._mac}"

        # The best display name available: the live payload, else the name the
        # registry retained for a restored tracker, else a MAC placeholder. A
        # restored offline client must not be rewritten to "Client <mac>".
        display_name = (
            get_field(client_data, "name", "hostname", default=restored_name)
            or f"Client {self._mac}"
        )

        # Device info - associate with connected network device (switch/AP)
        # This groups client trackers under their uplink device for cleaner UI.
        # It is built once, from whatever data exists at construction time: a
        # client that is offline at setup has no uplink to group under, so it
        # lands on the standalone-client device below and only settles under its
        # uplink on the next reload after the client reconnects.
        uplink_device_id = get_field(client_data, "uplinkDeviceId", "uplink_device_id")
        self._standalone = not uplink_device_id
        if uplink_device_id:
            # Grouped under the uplink AP/switch. That device is not this
            # client, so the entity carries the client's own name and
            # `has_entity_name` renders "<uplink device> <client>".
            self._device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{self._site_id}_{uplink_device_id}")},
            )
            self._attr_name = display_name
        else:
            # Fallback: a standalone device representing the client itself.
            # Here the tracker is that device's primary entity, so the device
            # carries the name and the entity has none -- setting both makes
            # `has_entity_name` compose "<device> <entity>" and render the name
            # twice ("Kitchen Tablet Kitchen Tablet"). This path is the normal
            # one for a client that is absent at setup, so the duplication would
            # be the common case rather than an edge case.
            model = get_field(
                client_data, "deviceName", "osName", default="Network Client"
            )
            self._device_info = DeviceInfo(
                identifiers={(DOMAIN, f"client_{self._mac}")},
                name=display_name,
                manufacturer=MANUFACTURER,
                model=model,
            )
            self._attr_name = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Follow a client renamed on the controller, then write the state."""
        self._async_follow_client_rename()
        super()._handle_coordinator_update()

    @callback
    def _async_follow_client_rename(self) -> None:
        """
        Apply the controller's current client name without a reload.

        A grouped tracker carries the client name itself; the uplink device
        is not the client and is never renamed. A standalone tracker has no
        name of its own, so its `client_` device is renamed instead. Only the
        device's `name` changes: a name the user gave it (`name_by_user`) wins.
        An absent client keeps its last name.
        """
        name = get_field(self._get_client_data() or {}, "name", "hostname")
        if not name:
            return
        if not self._standalone:
            self._attr_name = name
            return
        device = self.device_entry
        if (
            self.hass is None
            or device is None
            or device.name == name
            or (DOMAIN, f"client_{self._mac}") not in device.identifiers
        ):
            return
        dr.async_get(self.hass).async_update_device(device.id, name=name)

    @property
    def unique_id(self) -> str | None:
        """
        Return the unique ID of the entity.

        `ScannerEntity.unique_id` returns the bare `mac_address`, which is not
        namespaced to this integration. Declaring the id here keeps it
        domain-prefixed and lets a tracker restored from the registry carry the
        id it was registered under, which a legacy entry that could not be
        re-keyed still needs.
        """
        return self._attr_unique_id

    @property  # type: ignore[misc, unused-ignore]
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return self._device_info

    def _find_in_site(self, clients: Any) -> dict[str, Any] | None:
        """Return this MAC's entry within one site's client snapshot."""
        if not isinstance(clients, dict):
            return None
        for client_data in clients.values():
            if not isinstance(client_data, dict):
                continue
            mac = get_field(client_data, "macAddress", "mac_address", "mac", default="")
            if mac and mac.lower() == self._mac:
                return client_data
        return None

    def _get_client_data(self) -> dict[str, Any] | None:
        """
        Get connected-client data for this MAC, if currently connected.

        The MAC, not the site, identifies the client. `self._site_id` is only a
        hint: check it first (the common case), then fall back to scanning every
        site so a roamed client -- or one restored from the registry with no hint
        at all -- is still found. Remember where it turned up for next time.
        """
        all_clients = self.coordinator.data.get("clients", {})
        if not isinstance(all_clients, dict):
            return None
        if self._site_id is not None:
            client_data = self._find_in_site(all_clients.get(self._site_id, {}))
            if client_data is not None:
                return client_data
        for site_id, clients in all_clients.items():
            if site_id == self._site_id:
                continue
            client_data = self._find_in_site(clients)
            if client_data is not None:
                self._site_id = site_id
                return client_data
        return None

    @property
    def is_connected(self) -> bool:
        """
        Return true if the client is connected.

        Uses the snapshot membership rule: clients returned by the official
        connected-clients API lack a connected field and are considered
        connected by presence in the snapshot. An explicit connected=False
        overrides membership and reports disconnected. A missing client
        record returns False.
        """
        return client_is_connected(self._get_client_data())

    @property
    def source_type(self) -> SourceType:
        """Return the source type."""
        return SourceType.ROUTER

    @property
    def ip_address(self) -> str | None:
        """Return the IP address of the client."""
        client_data = self._get_client_data()
        if not client_data:
            return None
        return get_field(client_data, "ipAddress", "ip_address", "ip")  # type: ignore[no-any-return]

    @property
    def hostname(self) -> str | None:
        """Return the hostname of the client."""
        client_data = self._get_client_data()
        if not client_data:
            return None
        return get_field(client_data, "hostname", "name")  # type: ignore[no-any-return]

    @property
    def available(self) -> bool:
        """
        Return True if entity is available.

        Deliberately not gated on ``device_available``: a tracker that goes
        ``unavailable`` during a controller outage is ignored by ``person``,
        which then drops to ``unknown`` when it is the person's only tracker -
        the failure #116 was about. The tracker keeps its last home/not_home
        until the device refresh recovers.
        """
        return bool(self.coordinator.last_update_success)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        client_data = self._get_client_data()
        if not client_data:
            return {}

        return {
            "connection_type": get_field(client_data, "type", "connection_type"),
            "connected_at": get_field(client_data, "connectedAt", "connected_at"),
            "uplink_device_id": get_field(
                client_data, "uplinkDeviceId", "uplink_device_id"
            ),
            "authorized": get_field(client_data, "authorized", default=True),
            "blocked": get_field(client_data, "blocked", default=False),
        }


# --- Mobility ----------------------------------------------------------------

LOCATION_DESCRIPTION = EntityDescription(
    key="location",
    translation_key="mobility_location",
)


class UnifiMobilityLocationTracker(UnifiMobilityRouterEntity, TrackerEntity):
    """GPS position of a Mobility router."""

    _attr_source_type = SourceType.GPS

    def _coordinate(self, key: str) -> float | None:
        """Return a coordinate of the last GPS fix, if there is one."""
        router = self.router or {}
        location = router.get("location")
        value = location.get(key) if isinstance(location, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    @property
    def latitude(self) -> float | None:
        """Return the router's latitude."""
        return self._coordinate("latitude")

    @property
    def longitude(self) -> float | None:
        """Return the router's longitude."""
        return self._coordinate("longitude")


@callback
def async_setup_mobility_trackers(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: UnifiInsightsMobilityCoordinator,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Mobility router GPS trackers."""
    async_add_mobility_router_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        lambda device_id, workspace_device_id: [
            UnifiMobilityLocationTracker(
                coordinator, LOCATION_DESCRIPTION, device_id, workspace_device_id
            )
        ],
    )
