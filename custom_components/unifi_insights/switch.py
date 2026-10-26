# Copyright 2026 UniFi Insights contributors
"""Support for UniFi switches (Network and Protect)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_CAMERA_ID,
    ATTR_CAMERA_NAME,
    ATTR_HIGH_FPS_MODE,
    ATTR_STATUS_LIGHT,
    CONF_CLIENT_CONTROL,
    DEFAULT_CLIENT_CONTROL,
    DEVICE_TYPE_CAMERA,
    DOMAIN,
    MANUFACTURER,
    VIDEO_MODE_DEFAULT,
    VIDEO_MODE_HIGH_FPS,
)
from .coordinators.config_sections import (
    is_predefined_firewall_rule as _is_predefined_firewall_rule,
)
from .coordinators.config_sections import (
    is_predefined_nat_rule,
)
from .data_transforms import (
    get_site_gateway_id,
    network_client,
    network_device,
    owner_available,
    protect_collection,
    protect_device,
    rule_record,
    section_by_site,
    site_record,
    site_section_available,
)
from .entity import (
    UnifiAccessEntity,
    UnifiProtectEntity,
    UnifiSiteEntity,
    access_coordinator_for,
    access_unique_id,
    async_discover_access_entities,
    console_device_info,
    get_field,
    is_device_serviceable,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import UnifiInsightsConfigEntry
    from .coordinators import UnifiInsightsFacadeCoordinator
    from .coordinators.access import EmergencyMode, UnifiInsightsAccessCoordinator
    from .coordinators.config import UnifiInsightsConfigCoordinator

_LOGGER = logging.getLogger(__name__)

# Switch entities are action-based, allow parallel execution
PARALLEL_UPDATES = 1


def _get_firewall_rule_action(rule_data: dict[str, Any]) -> str | None:
    """Return the firewall rule action regardless of payload shape."""
    action = rule_data.get("action")
    if isinstance(action, dict):
        action_type = action.get("type")
        return str(action_type) if action_type is not None else None
    return str(action) if action is not None else None


def _resolve_site_name(
    coordinator: UnifiInsightsFacadeCoordinator, site_id: str
) -> str:
    """Resolve human-readable site name from coordinator data."""
    site_data = site_record(coordinator.data, site_id) or {}
    meta = site_data.get("meta", {})
    return str(
        (meta.get("name") if isinstance(meta, dict) else None)
        or site_data.get("name")
        or site_id
    )


def _stale_switch_unique_ids(
    coordinator: UnifiInsightsFacadeCoordinator,
    reg_entries: list[er.RegistryEntry],
    *,
    unique_id_suffix: str,
    data_key: str,
    availability_fn: Callable[[str], bool],
    is_hidden_fn: Callable[[Any], bool] | None = None,
) -> set[str]:
    """
    Return unique_ids of switches whose backing object no longer exists.

    Policy-based-route, firewall-rule, and VPN-client switches have no
    cleanup: deleting the underlying object on the console leaves ``available``
    (each type's own property) permanently False, but nothing ever removes the
    entity-registry row. This mirrors the client-block-switch cleanup
    (``async_setup_entry`` above) and the stale port-sensor cleanup
    (``sensor.py``), generalised to a table-driven
    ``(unique_id_suffix, data_key, availability_fn)`` spec.

    The three switch types do **not** share an availability predicate: the
    config owner's availability is global while a site's ``firewall_rules``
    availability is site-scoped, so each call site passes its own
    ``availability_fn``. Hard-coding one predicate for all three would prune,
    e.g., every firewall-rule entity during a firewall-only degradation while
    the config owner stays available.

    A site is only "genuinely fetched" -- and therefore eligible for pruning
    -- when all of these hold for it: ``availability_fn(site_id)`` is True,
    ``coordinator.data[data_key][site_id]`` is present and a ``dict``, and
    that dict is **non-empty**. A site that is missing, non-dict, empty, or
    whose availability predicate is False is left alone entirely.

    The non-empty requirement matters because only firewall rules have a
    per-site failure signal. On a failed firewall fetch the coordinator
    records the site in ``_failed_sections`` and restores the *previous*
    per-site data, so the site's ``firewall_rules`` availability gates it out.
    Policy-based routes and VPN clients have no equivalent: a transient
    per-site failure stores ``{}`` for that site while the overall refresh
    succeeds and the config owner stays available. Without the non-empty check, one
    timeout during startup would delete every route/VPN row for that site.
    """
    collections = coordinator.data.get(data_key)
    if not isinstance(collections, dict):
        return set()

    # Every site id the integration knows about, not just the qualified ones.
    # Needed to detect an ambiguous prefix match below.
    known_site_ids: set[str] = set(collections)
    sites = coordinator.data.get("sites")
    if isinstance(sites, dict):
        known_site_ids |= set(sites)

    live_unique_ids: set[str] = set()
    qualified_site_ids: set[str] = set()
    for site_id, site_objects in collections.items():
        if not isinstance(site_objects, dict):
            continue
        if not site_objects:
            # An EMPTY collection is never sufficient evidence to prune.
            # Policy-based routes and VPN clients have no per-site "fetched
            # successfully" signal: on a transient per-site fetch failure the
            # config coordinator stores `{}` for that site while the overall
            # refresh still succeeds, so the config owner stays available. An
            # empty dict is therefore indistinguishable from "every object
            # was deleted", and treating it as the latter would delete every
            # row of this type for the site. The cost of this guard is that
            # deleting the *last* object of a type on a site leaves its
            # entity behind; that is strictly better than the alternative.
            continue
        if not availability_fn(site_id):
            continue
        qualified_site_ids.add(site_id)
        for object_id, object_data in site_objects.items():
            if is_hidden_fn is not None and is_hidden_fn(object_data):
                continue
            live_unique_ids.add(f"{site_id}_{object_id}{unique_id_suffix}")

    if not qualified_site_ids:
        return set()

    stale: set[str] = set()
    for reg_entry in reg_entries:
        unique_id = reg_entry.unique_id
        if not unique_id.endswith(unique_id_suffix):
            continue
        # `startswith` alone is ambiguous when one site id is a prefix of
        # another (site `s` also matches `s_backup_<id>_<suffix>`). Require
        # exactly one known site to claim the row before trusting it.
        matching_site_ids = {
            site_id for site_id in known_site_ids if unique_id.startswith(f"{site_id}_")
        }
        if len(matching_site_ids) != 1:
            continue
        if matching_site_ids.pop() not in qualified_site_ids:
            # Belongs to a site we did not just fetch (or none at all) --
            # never prune based on data we cannot confirm is current.
            continue
        if unique_id not in live_unique_ids:
            stale.add(unique_id)

    return stale


def _prune_orphaned_switch_entities(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    coordinator: UnifiInsightsFacadeCoordinator,
) -> None:
    """
    Remove registry rows for rule switches whose rule no longer exists.

    Runs once, after the first discovery pass in ``async_setup_entry`` --
    NOT on every coordinator update -- following the ``sensor.py`` stale
    port-sensor precedent. Dynamic discovery already re-adds an entity if the
    same id reappears; this only removes rows whose object is confirmed gone.

    Deliberately excludes ``UnifiWifiSwitch`` (gated by the site's ``wifi``
    availability), which has the identical orphan gap. Left as
    a noted follow-up: deleting a WiFi network is rarer than deleting a route,
    rule, or VPN client, and keeping this first cleanup PR's blast radius
    small matters more than completeness.
    """
    registry = er.async_get(hass)
    reg_entries = [
        reg_entry
        for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
        if reg_entry.domain == "switch" and reg_entry.platform == DOMAIN
    ]

    data = coordinator.data
    specs: tuple[
        tuple[str, str, Callable[[str], bool], Callable[[Any], bool] | None], ...
    ] = (
        (
            "_policy_based_route",
            "policy_based_routes",
            lambda _site_id: owner_available(data, "config"),
            None,
        ),
        (
            "_firewall_rule",
            "firewall_rules",
            lambda site_id: site_section_available(data, site_id, "firewall_rules"),
            None,
        ),
        (
            "_vpn_client",
            "vpn_clients",
            lambda _site_id: owner_available(data, "config"),
            None,
        ),
        (
            "_port_forward",
            "port_forwards",
            lambda site_id: site_section_available(data, site_id, "port_forwards"),
            None,
        ),
        (
            "_traffic_rule",
            "traffic_rules",
            lambda site_id: site_section_available(data, site_id, "traffic_rules"),
            None,
        ),
        (
            "_nat_rule",
            "nat_rules",
            lambda site_id: site_section_available(data, site_id, "nat_rules"),
            is_predefined_nat_rule,
        ),
        (
            "_static_route",
            "static_routes",
            lambda site_id: site_section_available(data, site_id, "static_routes"),
            None,
        ),
    )

    stale_unique_ids: set[str] = set()
    for unique_id_suffix, data_key, availability_fn, is_hidden_fn in specs:
        stale_unique_ids |= _stale_switch_unique_ids(
            coordinator,
            reg_entries,
            unique_id_suffix=unique_id_suffix,
            data_key=data_key,
            availability_fn=availability_fn,
            is_hidden_fn=is_hidden_fn,
        )

    for reg_entry in reg_entries:
        if reg_entry.unique_id in stale_unique_ids:
            _LOGGER.debug("Removing orphaned switch entity: %s", reg_entry.unique_id)
            registry.async_remove(reg_entry.entity_id)


EMERGENCY_MODES = ("lockdown", "evacuation")


class UnifiAccessEmergencySwitch(UnifiAccessEntity, SwitchEntity):
    """One console-wide emergency mode. Lockdown and Evacuation exclude each other."""

    _feature = "emergency"

    def __init__(self, coordinator: UnifiInsightsAccessCoordinator, mode: str) -> None:
        """Create the switch for one emergency mode on the console device."""
        super().__init__(coordinator)
        # One of EMERGENCY_MODES, which the discovery passes in as its keys.
        self._mode: EmergencyMode = cast("EmergencyMode", mode)
        self._attr_translation_key = f"access_emergency_{mode}"
        self._attr_unique_id = access_unique_id(self._namespace, "emergency", mode)
        self._attr_device_info = console_device_info(self._namespace)

    def _is_present(self) -> bool:
        """Return True once the console has reported its emergency state."""
        return self.coordinator.data["emergency"] is not None

    @property
    def is_on(self) -> bool | None:
        """Return the mode as the console last reported it, or None when unknown."""
        emergency = self.coordinator.data["emergency"]
        if emergency is None:
            return None
        value: bool | None = emergency[self._mode]
        return value

    async def async_turn_on(self, **kwargs: object) -> None:  # noqa: ARG002
        """Turn this mode on; the coordinator clears the other mode."""
        await self._async_command(
            "Could not change the Access emergency mode",
            self.coordinator.async_set_emergency_mode,
            self._mode,
            enabled=True,
        )

    async def async_turn_off(self, **kwargs: object) -> None:  # noqa: ARG002
        """Turn this mode off and keep the other mode as the console reports it."""
        await self._async_command(
            "Could not change the Access emergency mode",
            self.coordinator.async_set_emergency_mode,
            self._mode,
            enabled=False,
        )


@callback
def async_setup_access_switches(
    entry: UnifiInsightsConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the Lockdown and Evacuation switches when the console supports them."""
    coordinator = access_coordinator_for(entry)
    if coordinator is None:
        return

    async_discover_access_entities(
        entry,
        coordinator,
        async_add_entities,
        known_ids=set(),
        keys=lambda: (
            EMERGENCY_MODES if coordinator.feature_available("emergency") else ()
        ),
        make=lambda mode: UnifiAccessEmergencySwitch(coordinator, mode),
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switches for UniFi Insights integration."""
    # Access is set up before the Protect-dependent switches below.
    async_setup_access_switches(entry, async_add_entities)
    coordinator: UnifiInsightsFacadeCoordinator = entry.runtime_data.coordinator
    client_control = entry.options.get(CONF_CLIENT_CONTROL, DEFAULT_CLIENT_CONTROL)

    # Remove orphaned client block switches when client control is disabled.
    # This mirrors the device_tracker cleanup pattern so toggling the option
    # in the UI immediately removes the stale entities on the next reload.
    registry = er.async_get(hass)
    if not client_control:
        for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
            if (
                reg_entry.domain == "switch"
                and reg_entry.platform == DOMAIN
                and reg_entry.unique_id.endswith("_block_switch")
            ):
                _LOGGER.debug(
                    "Removing client block switch %s (client control disabled)",
                    reg_entry.entity_id,
                )
                registry.async_remove(reg_entry.entity_id)

    # Remove obsolete camera microphone switches (#269).
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            reg_entry.domain == "switch"
            and reg_entry.platform == DOMAIN
            and reg_entry.unique_id.startswith(f"{DOMAIN}_{DEVICE_TYPE_CAMERA}_")
            and reg_entry.unique_id.endswith("_microphone")
        ):
            _LOGGER.debug(
                "Removing obsolete camera microphone switch %s",
                reg_entry.entity_id,
            )
            registry.async_remove(reg_entry.entity_id)

    # Remove obsolete camera privacy mode switches (#271).
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            reg_entry.domain == "switch"
            and reg_entry.platform == DOMAIN
            and reg_entry.unique_id.startswith(f"{DOMAIN}_{DEVICE_TYPE_CAMERA}_")
            and reg_entry.unique_id.endswith("_privacy_mode")
        ):
            _LOGGER.debug(
                "Removing obsolete camera privacy mode switch %s",
                reg_entry.entity_id,
            )
            registry.async_remove(reg_entry.entity_id)

    known_switch_keys: set[tuple[Any, ...]] = set()
    first_setup = True

    @callback
    def async_discover_switches() -> None:
        nonlocal first_setup
        """Discover and add new switches."""
        current_client_control = entry.options.get(
            CONF_CLIENT_CONTROL, DEFAULT_CLIENT_CONTROL
        )
        entities: list[SwitchEntity] = []

        # Add Protect switches if available
        if coordinator.protect_client:
            cameras = protect_collection(coordinator.data, "cameras") or {}
            for camera_id, camera_data in cameras.items():
                # Status light switch
                status_light_key = (camera_id, "status_light")
                if status_light_key not in known_switch_keys:
                    known_switch_keys.add(status_light_key)
                    entities.append(
                        UnifiProtectStatusLightSwitch(
                            coordinator=coordinator,
                            camera_id=camera_id,
                        )
                    )
                # High FPS mode switch (only for cameras that support it).
                high_fps_key = (camera_id, "high_fps")
                if high_fps_key not in known_switch_keys:
                    feature_flags = camera_data.get("featureFlags", {})
                    if isinstance(feature_flags, dict) and (
                        feature_flags.get("hasHighFpsCapability", False)
                        or "highFps" in feature_flags.get("videoModes", [])
                    ):
                        known_switch_keys.add(high_fps_key)
                        entities.append(
                            UnifiProtectHighFPSSwitch(
                                coordinator=coordinator,
                                camera_id=camera_id,
                            )
                        )

        # Add client block/allow switches for each connected client (when enabled)
        if current_client_control:
            clients_by_site = section_by_site(coordinator.data, "clients") or {}
            for site_id, clients in clients_by_site.items():
                for client_id in clients:
                    key = (site_id, client_id, "block_switch")
                    if key in known_switch_keys:
                        continue
                    known_switch_keys.add(key)
                    entities.append(
                        UnifiClientBlockSwitch(
                            coordinator=coordinator,
                            site_id=site_id,
                            client_id=client_id,
                        )
                    )

        # Add WiFi network enable/disable switches
        wifi_by_site = section_by_site(coordinator.data, "wifi") or {}
        for site_id, wifi_networks in wifi_by_site.items():
            for wifi_id, wifi_data in wifi_networks.items():
                key = (site_id, wifi_id, "wifi_switch")
                if key in known_switch_keys:
                    continue
                known_switch_keys.add(key)
                entities.append(
                    UnifiWifiSwitch(
                        coordinator=coordinator,
                        site_id=site_id,
                        wifi_id=wifi_id,
                        wifi_data=wifi_data,
                    )
                )

        # Add PDU outlet relay and power cycle switches
        devices_by_site = section_by_site(coordinator.data, "devices") or {}
        for site_id, devices in devices_by_site.items():
            for device_id, device_data in devices.items():
                outlet_table = device_data.get("outlet_table", [])
                if not isinstance(outlet_table, list):
                    continue
                for outlet in outlet_table:
                    if not isinstance(outlet, dict):
                        continue
                    idx = outlet.get("index")
                    if idx is None:
                        idx = outlet.get("outlet_idx") or outlet.get("outletIdx")
                    if idx is None:
                        continue
                    try:
                        outlet_idx = int(idx)
                    except TypeError, ValueError:
                        continue

                    for description in (OUTLET_RELAY_SWITCH, OUTLET_CYCLE_SWITCH):
                        # Only outlets that report cycling get a cycle switch.
                        if (
                            description is OUTLET_CYCLE_SWITCH
                            and outlet.get("cycle_enabled") is None
                        ):
                            continue
                        outlet_key = (
                            site_id,
                            device_id,
                            outlet_idx,
                            description.key,
                        )
                        if outlet_key in known_switch_keys:
                            continue
                        known_switch_keys.add(outlet_key)
                        entities.append(
                            UnifiOutletSwitch(
                                coordinator,
                                description,
                                site_id,
                                device_id,
                                outlet_index=outlet_idx,
                                outlet_data=outlet,
                            )
                        )

        entities.extend(discover_network_rule_switches(coordinator, known_switch_keys))

        if entities or first_setup:
            _LOGGER.info("Adding %d UniFi switches", len(entities))
            async_add_entities(entities)
        first_setup = False

    async_discover_switches()

    # One-time prune of orphaned PBR/firewall-rule/VPN-client switches -- see
    # docstring. Must run after the discovery pass above (so coordinator.data
    # reflects the first refresh) and only once, not on every listener tick.
    _prune_orphaned_switch_entities(hass, entry, coordinator)

    entry.async_on_unload(coordinator.async_add_listener(async_discover_switches))


class UnifiProtectStatusLightSwitch(UnifiProtectEntity, SwitchEntity):
    """Representation of a UniFi Protect Camera Status Light Switch."""

    _attr_has_entity_name = True
    _attr_translation_key = "status_light"

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        camera_id: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, DEVICE_TYPE_CAMERA, camera_id, "status_light")

        # Set entity category
        self._attr_entity_category = EntityCategory.CONFIG

        # Set initial state
        self._update_from_data()

    def _update_from_data(self) -> None:
        """Update entity from data."""
        camera_data = (
            protect_device(self.coordinator.data, "cameras", self._device_id) or {}
        )

        # LED settings are stored in ledSettings
        led_settings = camera_data.get("ledSettings", {})
        # isEnabled controls whether the status LED is on
        self._attr_is_on = led_settings.get("isEnabled", True)

        # Set attributes
        self._attr_extra_state_attributes = {
            ATTR_CAMERA_ID: self._device_id,
            ATTR_CAMERA_NAME: camera_data.get("name"),
            ATTR_STATUS_LIGHT: self._attr_is_on,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the status light on."""
        _ = kwargs
        _LOGGER.debug("Turning on status light for camera %s", self._device_id)

        # The owner publishes the written setting; state follows from it.
        protect = self.coordinator.require_protect_coordinator()
        await protect.async_update_camera_settings(
            self._device_id, ledSettings={"isEnabled": True}
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the status light off."""
        _ = kwargs
        _LOGGER.debug("Turning off status light for camera %s", self._device_id)

        # The owner publishes the written setting; state follows from it.
        protect = self.coordinator.require_protect_coordinator()
        await protect.async_update_camera_settings(
            self._device_id, ledSettings={"isEnabled": False}
        )


class UnifiProtectHighFPSSwitch(UnifiProtectEntity, SwitchEntity):
    """Representation of a UniFi Protect Camera High FPS Mode Switch."""

    _attr_has_entity_name = True
    _attr_translation_key = "high_fps_mode"

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        camera_id: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, DEVICE_TYPE_CAMERA, camera_id, "high_fps")

        # Set entity category
        self._attr_entity_category = EntityCategory.CONFIG

        # Set initial state
        self._update_from_data()

    def _update_from_data(self) -> None:
        """Update entity from data."""
        camera_data = (
            protect_device(self.coordinator.data, "cameras", self._device_id) or {}
        )

        # Video mode indicates high FPS when set to "highFps"
        video_mode = camera_data.get("videoMode", VIDEO_MODE_DEFAULT)
        self._attr_is_on = video_mode == VIDEO_MODE_HIGH_FPS

        # Set attributes
        self._attr_extra_state_attributes = {
            ATTR_CAMERA_ID: self._device_id,
            ATTR_CAMERA_NAME: camera_data.get("name"),
            ATTR_HIGH_FPS_MODE: self._attr_is_on,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable high FPS mode."""
        _ = kwargs
        _LOGGER.debug("Enabling high FPS mode for camera %s", self._device_id)

        # The owner publishes the written setting; state follows from it.
        protect = self.coordinator.require_protect_coordinator()
        await protect.async_update_camera_settings(
            self._device_id, videoMode=VIDEO_MODE_HIGH_FPS
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable high FPS mode (return to default)."""
        _ = kwargs
        _LOGGER.debug("Disabling high FPS mode for camera %s", self._device_id)

        # The owner publishes the written setting; state follows from it.
        protect = self.coordinator.require_protect_coordinator()
        await protect.async_update_camera_settings(
            self._device_id, videoMode=VIDEO_MODE_DEFAULT
        )


class UnifiClientBlockSwitch(
    CoordinatorEntity["UnifiInsightsFacadeCoordinator"], SwitchEntity
):
    """
    Switch to allow/block a network client.

    When ON (is_on=True): Client is allowed (not blocked)
    When OFF (is_on=False): Client is blocked
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        site_id: str,
        client_id: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._site_id = site_id
        self._client_id = client_id

        # Get client data for naming
        client_data = self._get_client_data()
        client_name = (
            client_data.get("name")
            or client_data.get("hostname")
            or client_data.get("mac", client_id)
        )

        self._attr_unique_id = f"{site_id}_{client_id}_block_switch"
        self._attr_translation_key = "client_allow"
        self._attr_translation_placeholders = {"client_name": str(client_name)}

        # Device info - associate with the connected network device (switch/AP)
        # This groups client entities under their uplink device for a cleaner UI
        uplink_device_id = client_data.get("uplinkDeviceId") or client_data.get(
            "uplink_device_id"
        )
        if uplink_device_id:
            # Use the network device's identifiers to group under it
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{site_id}_{uplink_device_id}")},
            )
        else:
            # Fallback: create a standalone client device if no uplink found
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"client_{client_id}")},
                name=client_name,
                manufacturer=MANUFACTURER,
                model="Network Client",
            )

    def _get_client_data(self) -> dict[str, Any]:
        """Get client data from coordinator."""
        result = network_client(self.coordinator.data, self._site_id, self._client_id)
        return result if isinstance(result, dict) else {}

    @property
    def available(self) -> bool:
        """Return if switch is available."""
        return bool(
            owner_available(self.coordinator.data, "device") and self._get_client_data()
        )

    @property
    def is_on(self) -> bool:
        """Return true if client is allowed (not blocked)."""
        client_data = self._get_client_data()
        # ON = allowed (not blocked), OFF = blocked
        is_blocked = client_data.get("blocked", False)
        return not is_blocked

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Allow the client (unblock)."""
        _ = kwargs
        _LOGGER.debug(
            "Allowing client %s in site %s (unblocking)", self._client_id, self._site_id
        )

        await self.coordinator.device_coordinator.async_unblock_client(
            self._site_id, self._client_id
        )
        _LOGGER.info(
            "Successfully allowed client %s in site %s",
            self._client_id,
            self._site_id,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Block the client."""
        _ = kwargs
        _LOGGER.debug("Blocking client %s in site %s", self._client_id, self._site_id)

        await self.coordinator.device_coordinator.async_block_client(
            self._site_id, self._client_id
        )
        _LOGGER.info(
            "Successfully blocked client %s in site %s",
            self._client_id,
            self._site_id,
        )


class UnifiWifiSwitch(
    CoordinatorEntity["UnifiInsightsFacadeCoordinator"], SwitchEntity
):
    """Switch to enable/disable a WiFi network."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        site_id: str,
        wifi_id: str,
        wifi_data: dict[str, Any],
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._site_id = site_id
        self._wifi_id = wifi_id
        self._wifi_data = wifi_data

        wifi_name = wifi_data.get("name") or wifi_data.get("ssid", wifi_id)

        self._attr_unique_id = f"{site_id}_{wifi_id}_wifi_switch"
        self._attr_translation_key = "wifi"
        self._attr_translation_placeholders = {"wifi_name": str(wifi_name)}

        # Create device info for WiFi network
        # Note: We don't use via_device since site_id is not a registered device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"wifi_{wifi_id}")},
            name=f"WiFi: {wifi_name}",
            manufacturer=MANUFACTURER,
            model="WiFi Network",
        )

    def _get_wifi_data(self) -> dict[str, Any]:
        """Get WiFi data from coordinator."""
        result = rule_record(
            self.coordinator.data, "wifi", self._site_id, self._wifi_id
        )
        return result if isinstance(result, dict) and result else self._wifi_data

    @property
    def available(self) -> bool:
        """Return if switch is available."""
        return bool(
            site_section_available(self.coordinator.data, self._site_id, "wifi")
            and self._get_wifi_data()
        )

    @property
    def is_on(self) -> bool:
        """Return true if WiFi is enabled."""
        wifi_data = self._get_wifi_data()
        return bool(wifi_data.get("enabled", True))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        wifi_data = self._get_wifi_data()
        return {
            "wifi_id": self._wifi_id,
            "ssid": wifi_data.get("ssid"),
            "security": wifi_data.get("security"),
            "hidden": wifi_data.get("hidden", False),
            "is_guest": wifi_data.get("isGuest", False),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the WiFi network."""
        _ = kwargs
        _LOGGER.debug(
            "Enabling WiFi network %s in site %s", self._wifi_id, self._site_id
        )

        await self.coordinator.config_coordinator.async_update_wifi_network(
            self._site_id, self._wifi_id, enabled=True
        )
        _LOGGER.info(
            "Successfully enabled WiFi network %s in site %s",
            self._wifi_id,
            self._site_id,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the WiFi network."""
        _ = kwargs
        _LOGGER.debug(
            "Disabling WiFi network %s in site %s", self._wifi_id, self._site_id
        )

        await self.coordinator.config_coordinator.async_update_wifi_network(
            self._site_id, self._wifi_id, enabled=False
        )
        _LOGGER.info(
            "Successfully disabled WiFi network %s in site %s",
            self._wifi_id,
            self._site_id,
        )


@dataclass(frozen=True, kw_only=True)
class UnifiOutletSwitchEntityDescription(SwitchEntityDescription):
    """One kind of PDU outlet switch: the relay, or automatic power cycling."""

    unique_id_suffix: str
    # Outlet field the switch reports: "relay_state" or "cycle_enabled".
    state_field: str


OUTLET_RELAY_SWITCH = UnifiOutletSwitchEntityDescription(
    key="outlet_switch",
    translation_key="outlet",
    device_class=SwitchDeviceClass.OUTLET,
    unique_id_suffix="",
    state_field="relay_state",
)

OUTLET_CYCLE_SWITCH = UnifiOutletSwitchEntityDescription(
    key="outlet_cycle_switch",
    translation_key="outlet_cycle_enabled",
    entity_category=EntityCategory.CONFIG,
    entity_registry_enabled_default=False,
    unique_id_suffix="_cycle_enabled",
    state_field="cycle_enabled",
)

# Outlet fields the relay switch reports as attributes.
_OUTLET_ATTRIBUTES = (
    "name",
    "relay_state",
    "cycle_enabled",
    "outlet_caps",
    "outlet_voltage",
    "outlet_current",
    "outlet_power",
    "outlet_power_factor",
)


class UnifiOutletSwitch(
    CoordinatorEntity["UnifiInsightsFacadeCoordinator"], SwitchEntity
):
    """Relay or power-cycle switch of one UniFi PDU or smart power strip outlet."""

    _attr_has_entity_name = True
    entity_description: UnifiOutletSwitchEntityDescription

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        description: UnifiOutletSwitchEntityDescription,
        site_id: str,
        device_id: str,
        *,
        outlet_index: int,
        outlet_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the outlet switch."""
        super().__init__(coordinator)
        self.entity_description = description
        self._site_id = site_id
        self._device_id = device_id
        self._outlet_index = outlet_index
        self._initial_outlet_data = outlet_data or {}

        outlet = self._get_outlet_data() or self._initial_outlet_data
        outlet_name = outlet.get("name") or f"Outlet {outlet_index}"

        self._attr_unique_id = (
            f"{site_id}_{device_id}_outlet_{outlet_index}{description.unique_id_suffix}"
        )
        self._attr_translation_placeholders = {"outlet_name": str(outlet_name)}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{site_id}_{device_id}")},
        )

    def _get_device_data(self) -> dict[str, Any]:
        """Return the coordinator's cached snapshot for this PDU device."""
        device_data = network_device(
            self.coordinator.data, self._site_id, self._device_id
        )
        return device_data if isinstance(device_data, dict) else {}

    def _get_outlet_data(self) -> dict[str, Any] | None:
        """Get current outlet data from coordinator."""
        device_data = self._get_device_data()
        if not device_data:
            return None
        outlets = device_data.get("outlet_table", [])
        if not isinstance(outlets, list):
            return None
        for outlet in outlets:
            if not isinstance(outlet, dict):
                continue
            idx = outlet.get("index")
            if idx is None:
                idx = outlet.get("outlet_idx") or outlet.get("outletIdx")
            if idx is not None:
                try:
                    if int(idx) == self._outlet_index:
                        return outlet
                except TypeError, ValueError:
                    continue
        return None

    @property
    def available(self) -> bool:
        """Return True if switch is available."""
        if not owner_available(self.coordinator.data, "device"):
            return False
        device_data = self._get_device_data()
        if not device_data or not is_device_serviceable(device_data):
            return False
        return self._get_outlet_data() is not None

    @property
    def is_on(self) -> bool:
        """Return the outlet's relay or power-cycling state."""
        outlet = self._get_outlet_data() or self._initial_outlet_data
        return bool(outlet.get(self.entity_description.state_field, False))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return outlet attributes for the relay switch."""
        if self.entity_description.state_field != "relay_state":
            return None
        outlet = self._get_outlet_data() or self._initial_outlet_data
        attrs: dict[str, Any] = {"index": self._outlet_index}
        for key in _OUTLET_ATTRIBUTES:
            if key in outlet and outlet[key] is not None:
                attrs[key] = outlet[key]
        return attrs

    async def _async_set(self, *, on: bool) -> None:
        """Set the relay, or power cycling while keeping the relay as it is."""
        owner = self.coordinator.device_coordinator
        if self.entity_description.state_field == "relay_state":
            await owner.async_set_outlet_state(
                self._site_id, self._device_id, self._outlet_index, state=on
            )
            return
        outlet = self._get_outlet_data() or self._initial_outlet_data
        await owner.async_set_outlet_state(
            self._site_id,
            self._device_id,
            self._outlet_index,
            state=bool(outlet.get("relay_state", False)),
            cycle_enabled=on,
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the relay or enable power cycling."""
        _ = kwargs
        await self._async_set(on=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the relay or disable power cycling."""
        _ = kwargs
        await self._async_set(on=False)


# --- Network rules -----------------------------------------------------------

# Switches for UniFi network rules (port forwards, traffic rules, NAT, routes).


class RuleToggleCommand(Protocol):
    """A config-coordinator command that enables or disables one rule."""

    async def __call__(self, site_id: str, rule_id: str, /, *, enabled: bool) -> None:
        """Enable or disable the rule."""


@dataclass(frozen=True, kw_only=True)
class UnifiInsightsNetworkRuleSwitchEntityDescription(
    SwitchEntityDescription,
):
    """Static metadata for one family of console rule switches."""

    data_key: str
    unique_id_suffix: str
    name_fields: tuple[str, ...]
    unnamed_translation_key: str
    available_fn: Callable[[Mapping[str, Any], str], bool]
    command: Callable[[UnifiInsightsConfigCoordinator], RuleToggleCommand]
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]]
    is_hidden_fn: Callable[[dict[str, Any]], bool] | None = None
    # Placeholder that carries the rule name, and the one that carries the rule
    # id when the rule has no name (None: the unnamed translation has none).
    name_placeholder: str = "rule_name"
    id_placeholder: str | None = "rule_id"
    # Attribute that reports the rule id.
    id_attribute: str = "rule_id"
    # Strict: the rule needs a boolean ``enabled`` and is on only when it is
    # True. Lenient (firewall policies, policy-based routes, VPN clients): any
    # non-empty rule counts, and a missing ``enabled`` counts as on.
    strict_enabled: bool = True
    # (identifier prefix, device name, model) of the device the switches use
    # when the site has no gateway; None uses the site device.
    fallback_device: tuple[str, str, str] | None = None
    # True for a rule that should show the VPN icon instead of its icons.json icon.
    vpn_icon_fn: Callable[[dict[str, Any]], bool] | None = None
    before_disable_fn: Callable[[str, str, dict[str, Any]], None] | None = None


def _port_forward_attributes(rule: dict[str, Any]) -> dict[str, Any]:
    """Return extra state attributes for a port forward rule."""
    return {
        "protocol": rule.get("proto"),
        "external_port": rule.get("dst_port"),
        "forward_port": rule.get("fwd_port"),
        "interface": rule.get("pfwd_interface"),
        "logging": rule.get("log"),
        "source_limiting_enabled": rule.get("src_limiting_enabled"),
    }


def _traffic_rule_attributes(rule: dict[str, Any]) -> dict[str, Any]:
    """Return extra state attributes for a traffic rule."""
    return {
        "action": rule.get("action"),
        "matching_target": rule.get("matching_target"),
    }


def _nat_rule_attributes(rule: dict[str, Any]) -> dict[str, Any]:
    """Return extra state attributes for a NAT rule."""
    return {
        "nat_type": rule.get("type"),
        "protocol": rule.get("protocol"),
        "ip_version": rule.get("ip_version"),
        "logging": rule.get("logging"),
    }


def _static_route_attributes(rule: dict[str, Any]) -> dict[str, Any]:
    """Return extra state attributes for a static route."""
    return {
        "route_type": rule.get("static-route_type"),
        "distance": rule.get("static-route_distance"),
    }


PORT_FORWARD_SWITCH = UnifiInsightsNetworkRuleSwitchEntityDescription(
    key="port_forward",
    translation_key="port_forward",
    unnamed_translation_key="port_forward_unnamed",
    entity_category=EntityCategory.CONFIG,
    data_key="port_forwards",
    unique_id_suffix="port_forward",
    name_fields=("name",),
    available_fn=lambda data, site_id: site_section_available(
        data, site_id, "port_forwards"
    ),
    command=lambda owner: owner.async_set_port_forward_enabled,
    attributes_fn=_port_forward_attributes,
)

TRAFFIC_RULE_SWITCH = UnifiInsightsNetworkRuleSwitchEntityDescription(
    key="traffic_rule",
    translation_key="traffic_rule",
    unnamed_translation_key="traffic_rule_unnamed",
    entity_category=EntityCategory.CONFIG,
    data_key="traffic_rules",
    unique_id_suffix="traffic_rule",
    name_fields=("description",),
    available_fn=lambda data, site_id: site_section_available(
        data, site_id, "traffic_rules"
    ),
    command=lambda owner: owner.async_set_traffic_rule_enabled,
    attributes_fn=_traffic_rule_attributes,
)

NAT_RULE_SWITCH = UnifiInsightsNetworkRuleSwitchEntityDescription(
    key="nat_rule",
    translation_key="nat_rule",
    unnamed_translation_key="nat_rule_unnamed",
    entity_category=EntityCategory.CONFIG,
    data_key="nat_rules",
    unique_id_suffix="nat_rule",
    name_fields=("description",),
    available_fn=lambda data, site_id: site_section_available(
        data, site_id, "nat_rules"
    ),
    command=lambda owner: owner.async_set_nat_rule_enabled,
    attributes_fn=_nat_rule_attributes,
    is_hidden_fn=is_predefined_nat_rule,
)

# Disabled by default: turning off a static route can cut the path Home Assistant
# itself uses to reach the console, and then the switch cannot turn it back on.
STATIC_ROUTE_SWITCH = UnifiInsightsNetworkRuleSwitchEntityDescription(
    key="static_route",
    translation_key="static_route",
    unnamed_translation_key="static_route_unnamed",
    entity_category=EntityCategory.CONFIG,
    entity_registry_enabled_default=False,
    data_key="static_routes",
    unique_id_suffix="static_route",
    name_fields=("name",),
    available_fn=lambda data, site_id: site_section_available(
        data, site_id, "static_routes"
    ),
    command=lambda owner: owner.async_set_static_route_enabled,
    attributes_fn=_static_route_attributes,
)


def _firewall_rule_attributes(rule: dict[str, Any]) -> dict[str, Any]:
    """Return extra state attributes for a firewall policy."""
    return {
        "action": _get_firewall_rule_action(rule),
        "protocol": rule.get("protocol"),
        "source_zone_id": rule.get("sourceZoneId") or rule.get("source_zone_id"),
        "destination_zone_id": rule.get("destinationZoneId")
        or rule.get("destination_zone_id"),
        "logging": rule.get("logging", False),
        "index": rule.get("index"),
    }


def _route_kill_switch(route: dict[str, Any]) -> bool | None:
    """
    Return a policy-based route's kill switch state, if known.

    Prefers the controller's own field names (``kill_switch_enabled`` /
    ``killSwitchEnabled``), falling back to the maintainer's original
    ``kill_switch`` / ``killSwitch`` names so both payload shapes work.
    """
    for key in (
        "kill_switch_enabled",
        "killSwitchEnabled",
        "killSwitch",
        "kill_switch",
    ):
        val = route.get(key)
        if val is not None:
            return bool(val)
    return None


def _policy_based_route_attributes(route: dict[str, Any]) -> dict[str, Any]:
    """Return extra state attributes for a policy-based route."""
    return {
        "description": route.get("description"),
        "target": route.get("target"),
        "matching_target": route.get("matchingTarget") or route.get("matching_target"),
        "interface": route.get("interface"),
        "vpn_client_id": route.get("vpnClientId") or route.get("vpn_client_id"),
        "kill_switch_enabled": _route_kill_switch(route),
        "fall_back_to_default_wan": get_field(
            route, "fallBackToDefaultWAN", "fall_back_to_default_wan", default=None
        ),
        "domains": route.get("domains", []),
        "ip_addresses": route.get("ipAddresses")
        or route.get("ip_addresses")
        or route.get("ips", []),
        "client_macs": route.get("clientMacs") or route.get("client_macs", []),
        "network_ids": route.get("networkIds") or route.get("network_ids", []),
        "network_id": route.get("networkId") or route.get("network_id"),
        "next_hop": route.get("nextHop") or route.get("next_hop"),
        "regions": route.get("regions", []),
        "ip_ranges": route.get("ipRanges") or route.get("ip_ranges", []),
        "target_devices": route.get("targetDevices") or route.get("target_devices", []),
    }


def _routes_over_vpn(route: dict[str, Any]) -> bool:
    """Return True for a policy-based route that sends traffic over a VPN."""
    return bool(route.get("vpn_client_id") or route.get("vpnClientId"))


def _warn_kill_switch(site_id: str, route_id: str, route: dict[str, Any]) -> None:
    """Warn that disabling a kill-switched route keeps dropping its traffic."""
    if _route_kill_switch(route):
        _LOGGER.warning(
            "Disabling policy-based route %s in site %s while its kill switch "
            "is enabled; matched traffic will keep being dropped instead of "
            "falling back to the default route",
            route_id,
            site_id,
        )


def _vpn_client_attributes(client: dict[str, Any]) -> dict[str, Any]:
    """Return extra state attributes for a VPN client."""
    return {
        "name": client.get("name"),
        "purpose": client.get("purpose", "vpn-client"),
        "vpn_type": client.get("vpn_type"),
        "ip_subnet": client.get("ip_subnet"),
        "openvpn_id": client.get("openvpn_id"),
        "wireguard_id": client.get("wireguard_id"),
        "remote_host": client.get("remote_host"),
    }


FIREWALL_RULE_SWITCH = UnifiInsightsNetworkRuleSwitchEntityDescription(
    key="firewall_rule",
    translation_key="firewall_rule",
    # An unnamed policy shows its id under the same translation.
    unnamed_translation_key="firewall_rule",
    id_placeholder="rule_name",
    entity_category=EntityCategory.CONFIG,
    data_key="firewall_rules",
    unique_id_suffix="firewall_rule",
    name_fields=("name",),
    available_fn=lambda data, site_id: site_section_available(
        data, site_id, "firewall_rules"
    ),
    command=lambda owner: owner.async_set_firewall_rule_enabled,
    attributes_fn=_firewall_rule_attributes,
    is_hidden_fn=_is_predefined_firewall_rule,
    strict_enabled=False,
    fallback_device=(
        "firewall_policies",
        "Firewall Policies",
        "UniFi Firewall Policies",
    ),
)

POLICY_BASED_ROUTE_SWITCH = UnifiInsightsNetworkRuleSwitchEntityDescription(
    key="pbr_switch",
    translation_key="policy_based_route",
    unnamed_translation_key="policy_based_route_unnamed",
    name_placeholder="route_name",
    id_placeholder="route_id",
    id_attribute="route_id",
    entity_category=EntityCategory.CONFIG,
    data_key="policy_based_routes",
    unique_id_suffix="policy_based_route",
    name_fields=("description", "name"),
    available_fn=lambda data, _site_id: owner_available(data, "config"),
    command=lambda owner: owner.async_set_policy_based_route_enabled,
    attributes_fn=_policy_based_route_attributes,
    strict_enabled=False,
    fallback_device=(
        "policy_based_routes",
        "Policy-Based Routes",
        "UniFi Policy-Based Routes",
    ),
    vpn_icon_fn=_routes_over_vpn,
    before_disable_fn=_warn_kill_switch,
)

VPN_CLIENT_SWITCH = UnifiInsightsNetworkRuleSwitchEntityDescription(
    key="vpn_switch",
    translation_key="vpn_client",
    unnamed_translation_key="vpn_client_unnamed",
    name_placeholder="vpn_client_name",
    id_placeholder=None,
    id_attribute="client_id",
    entity_category=EntityCategory.CONFIG,
    data_key="vpn_clients",
    unique_id_suffix="vpn_client",
    name_fields=("name",),
    available_fn=lambda data, _site_id: owner_available(data, "config"),
    command=lambda owner: owner.async_set_vpn_client_enabled,
    attributes_fn=_vpn_client_attributes,
    strict_enabled=False,
    fallback_device=("vpn_clients", "VPN Clients", "UniFi VPN Clients"),
)

NETWORK_RULE_SWITCH_TYPES = (
    FIREWALL_RULE_SWITCH,
    POLICY_BASED_ROUTE_SWITCH,
    VPN_CLIENT_SWITCH,
    PORT_FORWARD_SWITCH,
    TRAFFIC_RULE_SWITCH,
    NAT_RULE_SWITCH,
    STATIC_ROUTE_SWITCH,
)


class UnifiInsightsNetworkRuleSwitch(
    UnifiSiteEntity,
    SwitchEntity,
):
    """Switch to enable or disable one UniFi network rule."""

    entity_description: UnifiInsightsNetworkRuleSwitchEntityDescription

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        description: UnifiInsightsNetworkRuleSwitchEntityDescription,
        site_id: str,
        rule_id: str,
    ) -> None:
        """Initialize the network rule switch."""
        super().__init__(
            coordinator,
            site_id,
            unique_id=f"{site_id}_{rule_id}_{description.unique_id_suffix}",
            description=description,
        )
        self._rule_id = rule_id

        rule = self._get_rule_data()
        rule_name = next(
            (
                name.strip()
                for field in description.name_fields
                if isinstance(name := rule.get(field), str) and name.strip()
            ),
            None,
        )
        if rule_name is not None:
            self._attr_translation_key = description.translation_key
            self._attr_translation_placeholders = {
                description.name_placeholder: rule_name
            }
        else:
            self._attr_translation_key = description.unnamed_translation_key
            self._attr_translation_placeholders = (
                {description.id_placeholder: rule_id}
                if description.id_placeholder
                else {}
            )
        if (
            description.fallback_device is not None
            and get_site_gateway_id(coordinator.data, site_id) is None
        ):
            prefix, name, model = description.fallback_device
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{prefix}_{site_id}")},
                name=f"{name} ({_resolve_site_name(coordinator, site_id)})",
                manufacturer=MANUFACTURER,
                model=model,
            )

    def _get_rule_data(self) -> dict[str, Any]:
        """Get rule data from coordinator."""
        rule = rule_record(
            self.coordinator.data,
            self.entity_description.data_key,
            self._site_id,
            self._rule_id,
        )
        return rule if isinstance(rule, dict) else {}

    @property
    def available(self) -> bool:
        """Return if the switch is available."""
        desc = self.entity_description
        rule = self._get_rule_data()
        if desc.is_hidden_fn is not None and desc.is_hidden_fn(rule):
            return False
        if not desc.available_fn(self.coordinator.data, self._site_id):
            return False
        if desc.strict_enabled:
            return isinstance(rule.get("enabled"), bool)
        return bool(rule)

    @property
    def is_on(self) -> bool:
        """Return True if the rule is enabled."""
        rule = self._get_rule_data()
        if self.entity_description.strict_enabled:
            return rule.get("enabled") is True
        return bool(rule.get("enabled", True))

    @property
    def icon(self) -> str | None:
        """Show the VPN icon for a VPN route; otherwise use icons.json."""
        vpn_icon_fn = self.entity_description.vpn_icon_fn
        if vpn_icon_fn is not None and vpn_icon_fn(self._get_rule_data()):
            return "mdi:vpn" if self.is_on else "mdi:vpn-off"
        return super().icon

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return rule extra state attributes."""
        return {
            self.entity_description.id_attribute: self._rule_id,
            **self.entity_description.attributes_fn(self._get_rule_data()),
        }

    async def _async_set_enabled(self, *, enabled: bool) -> None:
        """Enable or disable the network rule through its command owner."""
        desc = self.entity_description
        if not enabled and desc.before_disable_fn is not None:
            desc.before_disable_fn(self._site_id, self._rule_id, self._get_rule_data())
        command = desc.command(self.coordinator.config_coordinator)
        await command(self._site_id, self._rule_id, enabled=enabled)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the network rule."""
        _ = kwargs
        await self._async_set_enabled(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the network rule."""
        _ = kwargs
        await self._async_set_enabled(enabled=False)


def discover_network_rule_switches(
    coordinator: UnifiInsightsFacadeCoordinator,
    known_keys: set[tuple[Any, ...]],
) -> list[SwitchEntity]:
    """Discover the switch entities of every network rule family."""
    entities: list[SwitchEntity] = []
    for desc in NETWORK_RULE_SWITCH_TYPES:
        rules_by_site = section_by_site(coordinator.data, desc.data_key) or {}
        for site_id, site_rules in rules_by_site.items():
            for rule_id, rule in site_rules.items():
                if desc.strict_enabled and not isinstance(rule.get("enabled"), bool):
                    continue
                if desc.is_hidden_fn is not None and desc.is_hidden_fn(rule):
                    continue
                key = (site_id, rule_id, desc.key)
                if key in known_keys:
                    continue
                known_keys.add(key)
                entities.append(
                    UnifiInsightsNetworkRuleSwitch(coordinator, desc, site_id, rule_id)
                )

    return entities
