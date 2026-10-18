# Copyright 2026 UniFi Insights contributors
"""Support for UniFi switches (Network and Protect)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .access_entity import (
    UnifiAccessEntity,
    access_coordinator_for,
    access_unique_id,
    async_discover_access_entities,
    console_device_info,
)
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
from .entity import (
    UnifiProtectEntity,
    async_call_coordinator_action,
    get_field,
    is_device_online,
    is_gateway_device,
)
from .network_rule_switch import discover_network_rule_switches

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import UnifiInsightsConfigEntry
    from .coordinators import UnifiFacadeCoordinator
    from .coordinators.access import UnifiInsightsAccessCoordinator

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


def _find_gateway_device_id(
    coordinator: UnifiFacadeCoordinator, site_id: str
) -> str | None:
    """Return the gateway-like device ID for the site if one exists."""
    site_devices = coordinator.data.get("devices", {}).get(site_id, {})
    if not isinstance(site_devices, dict):
        return None

    for device_id, device_data in site_devices.items():
        if not isinstance(device_data, dict):
            continue

        if is_gateway_device(device_data):
            return str(device_id)

    return None


def _resolve_site_name(coordinator: UnifiFacadeCoordinator, site_id: str) -> str:
    """Resolve human-readable site name from coordinator data."""
    site_data = coordinator.data.get("sites", {}).get(site_id, {})
    meta = site_data.get("meta", {})
    return str(
        (meta.get("name") if isinstance(meta, dict) else None)
        or site_data.get("name")
        or site_id
    )


def _stale_switch_unique_ids(
    coordinator: UnifiFacadeCoordinator,
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

    The three switch types do **not** share an availability predicate:
    ``config_available`` is global while ``firewall_available(site_id)`` is
    site-scoped, so each call site passes its own ``availability_fn``.
    Hard-coding one predicate for all three would prune, e.g., every
    firewall-rule entity during a firewall-only degradation while
    ``config_available`` stays True.

    A site is only "genuinely fetched" -- and therefore eligible for pruning
    -- when all of these hold for it: ``availability_fn(site_id)`` is True,
    ``coordinator.data[data_key][site_id]`` is present and a ``dict``, and
    that dict is **non-empty**. A site that is missing, non-dict, empty, or
    whose availability predicate is False is left alone entirely.

    The non-empty requirement matters because only firewall rules have a
    per-site failure signal. On a failed firewall fetch the coordinator
    records the site in ``_failed_sections`` and restores the *previous*
    per-site data, so ``firewall_available(site_id)`` gates it out. Policy-
    based routes and VPN clients have no equivalent: a transient per-site
    failure stores ``{}`` for that site while the overall refresh succeeds
    and ``config_available`` stays True. Without the non-empty check, one
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
            # refresh still succeeds, so `config_available` stays True. An
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
    coordinator: UnifiFacadeCoordinator,
) -> None:
    """
    Remove registry rows for rule switches whose rule no longer exists.

    Runs once, after the first discovery pass in ``async_setup_entry`` --
    NOT on every coordinator update -- following the ``sensor.py`` stale
    port-sensor precedent. Dynamic discovery already re-adds an entity if the
    same id reappears; this only removes rows whose object is confirmed gone.

    Deliberately excludes ``UnifiWifiSwitch`` (gated by
    ``wifi_available(site_id)``), which has the identical orphan gap. Left as
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

    specs: tuple[
        tuple[str, str, Callable[[str], bool], Callable[[Any], bool] | None], ...
    ] = (
        (
            "_policy_based_route",
            "policy_based_routes",
            lambda _site_id: coordinator.config_available,
            None,
        ),
        (
            "_firewall_rule",
            "firewall_rules",
            coordinator.firewall_available,
            None,
        ),
        (
            "_vpn_client",
            "vpn_clients",
            lambda _site_id: coordinator.config_available,
            None,
        ),
        (
            "_port_forward",
            "port_forwards",
            coordinator.port_forwards_available,
            None,
        ),
        (
            "_traffic_rule",
            "traffic_rules",
            coordinator.traffic_rules_available,
            None,
        ),
        (
            "_nat_rule",
            "nat_rules",
            coordinator.nat_rules_available,
            is_predefined_nat_rule,
        ),
        (
            "_static_route",
            "static_routes",
            coordinator.static_routes_available,
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
        self._mode = mode
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
        await async_call_coordinator_action(
            self.coordinator,
            "async_set_emergency_mode",
            "Could not change the Access emergency mode",
            self._mode,
            enabled=True,
        )

    async def async_turn_off(self, **kwargs: object) -> None:  # noqa: ARG002
        """Turn this mode off and keep the other mode as the console reports it."""
        await async_call_coordinator_action(
            self.coordinator,
            "async_set_emergency_mode",
            "Could not change the Access emergency mode",
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
    coordinator: UnifiFacadeCoordinator = entry.runtime_data.coordinator
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
        if not coordinator.data or not isinstance(coordinator.data, dict):
            return

        current_client_control = entry.options.get(
            CONF_CLIENT_CONTROL, DEFAULT_CLIENT_CONTROL
        )
        entities: list[SwitchEntity] = []

        # Add Protect switches if available
        if coordinator.protect_client:
            protect = coordinator.data.get("protect", {})
            if isinstance(protect, dict):
                cameras = protect.get("cameras", {})
                if isinstance(cameras, dict):
                    for camera_id, camera_data in cameras.items():
                        if not isinstance(camera_data, dict):
                            continue
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
            clients_by_site = coordinator.data.get("clients", {})
            if isinstance(clients_by_site, dict):
                for site_id, clients in clients_by_site.items():
                    if not isinstance(clients, dict):
                        continue
                    for client_id, client_data in clients.items():
                        if not isinstance(client_data, dict):
                            continue
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
        wifi_by_site = coordinator.data.get("wifi", {})
        if isinstance(wifi_by_site, dict):
            for site_id, wifi_networks in wifi_by_site.items():
                if not isinstance(wifi_networks, dict):
                    continue
                for wifi_id, wifi_data in wifi_networks.items():
                    if not isinstance(wifi_data, dict):
                        continue
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

        # Add firewall policy enable/disable switches for user-defined rules.
        firewall_rules_by_site = coordinator.data.get("firewall_rules", {})
        if isinstance(firewall_rules_by_site, dict):
            for site_id, firewall_rules in firewall_rules_by_site.items():
                if not isinstance(firewall_rules, dict):
                    continue
                for rule_id, rule_data in firewall_rules.items():
                    if not isinstance(rule_data, dict):
                        continue
                    if _is_predefined_firewall_rule(rule_data):
                        continue
                    key = (site_id, rule_id, "firewall_rule")
                    if key in known_switch_keys:
                        continue
                    known_switch_keys.add(key)
                    entities.append(
                        UnifiFirewallRuleSwitch(
                            coordinator=coordinator,
                            site_id=site_id,
                            rule_id=rule_id,
                        )
                    )

        # Add PDU outlet relay and power cycle switches
        devices_by_site = coordinator.data.get("devices", {})
        if isinstance(devices_by_site, dict):
            for site_id, devices in devices_by_site.items():
                if not isinstance(devices, dict):
                    continue
                for device_id, device_data in devices.items():
                    if not isinstance(device_data, dict):
                        continue
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

                        switch_key = (site_id, device_id, outlet_idx, "outlet_switch")
                        if switch_key not in known_switch_keys:
                            known_switch_keys.add(switch_key)
                            entities.append(
                                UnifiOutletSwitch(
                                    coordinator=coordinator,
                                    site_id=site_id,
                                    device_id=device_id,
                                    outlet_index=outlet_idx,
                                    outlet_data=outlet,
                                )
                            )

                        if outlet.get("cycle_enabled") is not None:
                            cycle_key = (
                                site_id,
                                device_id,
                                outlet_idx,
                                "outlet_cycle_switch",
                            )
                            if cycle_key not in known_switch_keys:
                                known_switch_keys.add(cycle_key)
                                entities.append(
                                    UnifiOutletCycleSwitch(
                                        coordinator=coordinator,
                                        site_id=site_id,
                                        device_id=device_id,
                                        outlet_index=outlet_idx,
                                        outlet_data=outlet,
                                    )
                                )

        # Add policy-based route enable/disable switches
        routes_by_site = coordinator.data.get("policy_based_routes", {})
        if isinstance(routes_by_site, dict):
            for site_id, routes in routes_by_site.items():
                if not isinstance(routes, dict):
                    continue
                for route_id, route_data in routes.items():
                    if not isinstance(route_data, dict):
                        continue
                    key = (site_id, route_id, "pbr_switch")
                    if key in known_switch_keys:
                        continue
                    known_switch_keys.add(key)
                    entities.append(
                        UnifiInsightsPolicyBasedRouteSwitch(
                            coordinator=coordinator,
                            site_id=site_id,
                            route_id=route_id,
                        )
                    )

        # Add VPN client enable/disable switches
        vpn_clients_by_site = coordinator.data.get("vpn_clients", {})
        if isinstance(vpn_clients_by_site, dict):
            for site_id, vpn_clients in vpn_clients_by_site.items():
                if not isinstance(vpn_clients, dict):
                    continue
                for client_id, vpn_client_data in vpn_clients.items():
                    if not isinstance(vpn_client_data, dict):
                        continue
                    key = (site_id, client_id, "vpn_switch")
                    if key in known_switch_keys:
                        continue
                    known_switch_keys.add(key)
                    entities.append(
                        UnifiInsightsVpnClientSwitch(
                            coordinator=coordinator,
                            site_id=site_id,
                            client_id=client_id,
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


class UnifiFirewallRuleSwitch(
    CoordinatorEntity["UnifiFacadeCoordinator"], SwitchEntity
):
    """Switch to enable or disable a user-defined firewall rule."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        site_id: str,
        rule_id: str,
    ) -> None:
        """Initialize the firewall rule switch."""
        super().__init__(coordinator)
        self._site_id = site_id
        self._rule_id = rule_id

        rule_data = self._get_rule_data()
        rule_name = rule_data.get("name") or rule_id

        self._attr_unique_id = f"{site_id}_{rule_id}_firewall_rule"
        self._attr_translation_key = "firewall_rule"
        self._attr_translation_placeholders = {"rule_name": str(rule_name)}
        self._attr_device_info = self._build_device_info()

    def _get_rule_data(self) -> dict[str, Any]:
        """Get firewall rule data from the coordinator."""
        result: dict[str, Any] = (
            self.coordinator.data.get("firewall_rules", {})
            .get(self._site_id, {})
            .get(self._rule_id, {})
        )
        return result

    def _build_device_info(self) -> DeviceInfo:
        """Build device info for firewall rule grouping."""
        gateway_device_id = _find_gateway_device_id(self.coordinator, self._site_id)
        if gateway_device_id is not None:
            return DeviceInfo(
                identifiers={(DOMAIN, f"{self._site_id}_{gateway_device_id}")}
            )

        site_name = _resolve_site_name(self.coordinator, self._site_id)
        return DeviceInfo(
            identifiers={(DOMAIN, f"firewall_policies_{self._site_id}")},
            name=f"Firewall Policies ({site_name})",
            manufacturer=MANUFACTURER,
            model="UniFi Firewall Policies",
        )

    def _update_local_state(self, *, enabled: bool) -> None:
        """Update the aggregated coordinator cache for immediate UI feedback."""
        firewall_rules = self.coordinator.data.setdefault("firewall_rules", {})
        site_rules = firewall_rules.setdefault(self._site_id, {})
        rule_data = site_rules.get(self._rule_id)
        if isinstance(rule_data, dict):
            rule_data["enabled"] = enabled

    @property
    def available(self) -> bool:
        """Return if the switch is available."""
        return bool(
            self.coordinator.firewall_available(self._site_id) and self._get_rule_data()
        )

    @property
    def is_on(self) -> bool:
        """Return True if the firewall rule is enabled."""
        rule_data = self._get_rule_data()
        return bool(rule_data.get("enabled", True))

    @property
    def icon(self) -> str:
        """Return a context-specific icon for the firewall rule state."""
        return "mdi:shield-lock" if self.is_on else "mdi:shield-off"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return firewall rule metadata useful in automations and debugging."""
        rule_data = self._get_rule_data()
        return {
            "rule_id": self._rule_id,
            "action": _get_firewall_rule_action(rule_data),
            "protocol": rule_data.get("protocol"),
            "source_zone_id": rule_data.get("sourceZoneId")
            or rule_data.get("source_zone_id"),
            "destination_zone_id": rule_data.get("destinationZoneId")
            or rule_data.get("destination_zone_id"),
            "logging": rule_data.get("logging", False),
            "index": rule_data.get("index"),
        }

    async def _async_set_enabled(self, *, enabled: bool) -> None:
        """Enable or disable the firewall rule."""
        action = "Enabling" if enabled else "Disabling"
        _LOGGER.debug(
            "%s firewall rule %s in site %s",
            action,
            self._rule_id,
            self._site_id,
        )

        await async_call_coordinator_action(
            self.coordinator,
            "async_set_firewall_rule_enabled",
            f"Unable to update firewall rule {self._rule_id}",
            self._site_id,
            self._rule_id,
            enabled=enabled,
            fallback_factory=lambda: (
                self.coordinator.network_client.firewall.update_rule(
                    self._site_id,
                    self._rule_id,
                    enabled=enabled,
                )
            ),
        )
        self._update_local_state(enabled=enabled)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the firewall rule."""
        _ = kwargs
        await self._async_set_enabled(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the firewall rule."""
        _ = kwargs
        await self._async_set_enabled(enabled=False)


class UnifiInsightsPolicyBasedRouteSwitch(
    CoordinatorEntity["UnifiFacadeCoordinator"], SwitchEntity
):
    """Switch to enable or disable a policy-based route (traffic route)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        site_id: str,
        route_id: str,
    ) -> None:
        """Initialize the policy-based route switch."""
        super().__init__(coordinator)
        self._site_id = site_id
        self._route_id = route_id

        route_data = self._get_route_data()
        route_name = route_data.get("description") or route_data.get("name")

        self._attr_unique_id = f"{site_id}_{route_id}_policy_based_route"
        if route_name:
            self._attr_translation_key = "policy_based_route"
            self._attr_translation_placeholders = {"route_name": str(route_name)}
        else:
            self._attr_translation_key = "policy_based_route_unnamed"
            self._attr_translation_placeholders = {"route_id": route_id}
        self._attr_device_info = self._build_device_info()

    def _get_route_data(self) -> dict[str, Any]:
        """Get policy-based route data from the coordinator."""
        result: dict[str, Any] = (
            self.coordinator.data.get("policy_based_routes", {})
            .get(self._site_id, {})
            .get(self._route_id, {})
        )
        return result

    def _build_device_info(self) -> DeviceInfo:
        """Build device info for policy-based route grouping."""
        gateway_device_id = _find_gateway_device_id(self.coordinator, self._site_id)
        if gateway_device_id is not None:
            return DeviceInfo(
                identifiers={(DOMAIN, f"{self._site_id}_{gateway_device_id}")}
            )

        site_name = _resolve_site_name(self.coordinator, self._site_id)
        return DeviceInfo(
            identifiers={(DOMAIN, f"policy_based_routes_{self._site_id}")},
            name=f"Policy-Based Routes ({site_name})",
            manufacturer=MANUFACTURER,
            model="UniFi Policy-Based Routes",
        )

    def _update_local_state(self, *, enabled: bool) -> None:
        """Update the aggregated coordinator cache for immediate UI feedback."""
        routes = self.coordinator.data.setdefault("policy_based_routes", {})
        site_routes = routes.setdefault(self._site_id, {})
        route_data = site_routes.get(self._route_id)
        if isinstance(route_data, dict):
            route_data["enabled"] = enabled

    @property
    def available(self) -> bool:
        """Return if the switch is available."""
        return bool(self.coordinator.config_available and self._get_route_data())

    @property
    def is_on(self) -> bool:
        """Return True if the policy-based route is enabled."""
        route_data = self._get_route_data()
        return bool(route_data.get("enabled", True))

    @property
    def icon(self) -> str:
        """Return a context-specific icon for the route state."""
        route_data = self._get_route_data()
        if route_data.get("vpn_client_id") or route_data.get("vpnClientId"):
            return "mdi:vpn" if self.is_on else "mdi:vpn-off"
        return "mdi:routes" if self.is_on else "mdi:routes-clock"

    @property
    def _kill_switch_enabled(self) -> bool | None:
        """
        Return the route's kill switch state, if known.

        Prefers the controller's own field names (``kill_switch_enabled`` /
        ``killSwitchEnabled``), falling back to the maintainer's original
        ``kill_switch`` / ``killSwitch`` names so both payload shapes work.
        """
        route_data = self._get_route_data()
        for key in (
            "kill_switch_enabled",
            "killSwitchEnabled",
            "killSwitch",
            "kill_switch",
        ):
            val = route_data.get(key)
            if val is not None:
                return bool(val)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return route metadata useful in automations and debugging."""
        route_data = self._get_route_data()
        kill_switch = self._kill_switch_enabled
        fall_back = get_field(
            route_data,
            "fallBackToDefaultWAN",
            "fall_back_to_default_wan",
            default=None,
        )
        return {
            "route_id": self._route_id,
            "description": route_data.get("description"),
            "target": route_data.get("target"),
            "matching_target": route_data.get("matchingTarget")
            or route_data.get("matching_target"),
            "interface": route_data.get("interface"),
            "vpn_client_id": route_data.get("vpnClientId")
            or route_data.get("vpn_client_id"),
            "kill_switch_enabled": kill_switch,
            "fall_back_to_default_wan": fall_back,
            "domains": route_data.get("domains", []),
            "ip_addresses": route_data.get("ipAddresses")
            or route_data.get("ip_addresses")
            or route_data.get("ips", []),
            "client_macs": route_data.get("clientMacs")
            or route_data.get("client_macs", []),
            "network_ids": route_data.get("networkIds")
            or route_data.get("network_ids", []),
            "network_id": route_data.get("networkId") or route_data.get("network_id"),
            "next_hop": route_data.get("nextHop") or route_data.get("next_hop"),
            "regions": route_data.get("regions", []),
            "ip_ranges": route_data.get("ipRanges") or route_data.get("ip_ranges", []),
            "target_devices": route_data.get("targetDevices")
            or route_data.get("target_devices", []),
        }

    async def _async_set_enabled(self, *, enabled: bool) -> None:
        """Enable or disable the policy-based route."""
        action = "Enabling" if enabled else "Disabling"
        _LOGGER.debug(
            "%s policy-based route %s in site %s",
            action,
            self._route_id,
            self._site_id,
        )

        if not enabled and self._kill_switch_enabled:
            _LOGGER.warning(
                "Disabling policy-based route %s in site %s while its kill switch "
                "is enabled; matched traffic will keep being dropped instead of "
                "falling back to the default route",
                self._route_id,
                self._site_id,
            )

        await async_call_coordinator_action(
            self.coordinator,
            "async_set_policy_based_route_enabled",
            f"Unable to update policy-based route {self._route_id}",
            self._site_id,
            self._route_id,
            enabled=enabled,
            fallback_factory=lambda: (
                self.coordinator.network_client.routes.update_route(
                    self.coordinator.resolve_legacy_site_name(self._site_id),
                    self._route_id,
                    enabled=enabled,
                )
            ),
        )
        self._update_local_state(enabled=enabled)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the policy-based route."""
        _ = kwargs
        await self._async_set_enabled(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the policy-based route."""
        _ = kwargs
        await self._async_set_enabled(enabled=False)


class UnifiInsightsVpnClientSwitch(
    CoordinatorEntity["UnifiFacadeCoordinator"], SwitchEntity
):
    """Switch to enable or disable a UniFi VPN Client network connection."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        site_id: str,
        client_id: str,
    ) -> None:
        """Initialize the VPN client switch."""
        super().__init__(coordinator)
        self._site_id = site_id
        self._client_id = client_id
        self._attr_unique_id = f"{site_id}_{client_id}_vpn_client"

        vpn_client_data = self._get_vpn_client_data()
        vpn_client_name = vpn_client_data.get("name")
        if vpn_client_name:
            self._attr_translation_key = "vpn_client"
            self._attr_translation_placeholders = {
                "vpn_client_name": str(vpn_client_name)
            }
        else:
            self._attr_translation_key = "vpn_client_unnamed"

        self._attr_device_info = self._build_device_info()

    def _build_device_info(self) -> DeviceInfo:
        """Build device info for VPN client grouping."""
        gateway_device_id = _find_gateway_device_id(self.coordinator, self._site_id)
        if gateway_device_id is not None:
            return DeviceInfo(
                identifiers={(DOMAIN, f"{self._site_id}_{gateway_device_id}")}
            )

        site_name = _resolve_site_name(self.coordinator, self._site_id)
        return DeviceInfo(
            identifiers={(DOMAIN, f"vpn_clients_{self._site_id}")},
            name=f"VPN Clients ({site_name})",
            manufacturer=MANUFACTURER,
            model="UniFi VPN Clients",
        )

    def _get_vpn_client_data(self) -> dict[str, Any]:
        """Get VPN client data from coordinator."""
        vpn_clients = self.coordinator.data.get("vpn_clients", {}).get(
            self._site_id, {}
        )
        data = vpn_clients.get(self._client_id, {})
        return data if isinstance(data, dict) else {}

    def _update_local_state(self, *, enabled: bool) -> None:
        """Optimistically update local coordinator state for immediate UI feedback."""
        vpn_clients = self.coordinator.data.setdefault("vpn_clients", {})
        site_vpn_clients = vpn_clients.setdefault(self._site_id, {})
        if self._client_id in site_vpn_clients:
            site_vpn_clients[self._client_id]["enabled"] = enabled

    @property
    def available(self) -> bool:
        """Return if the switch is available."""
        return bool(self.coordinator.config_available and self._get_vpn_client_data())

    @property
    def is_on(self) -> bool:
        """Return True if the VPN client is enabled."""
        vpn_client_data = self._get_vpn_client_data()
        return bool(vpn_client_data.get("enabled", True))

    @property
    def icon(self) -> str:
        """Return icon representing VPN client status."""
        return "mdi:vpn" if self.is_on else "mdi:vpn-off"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return VPN client extra state attributes."""
        vpn_client_data = self._get_vpn_client_data()
        return {
            "client_id": self._client_id,
            "name": vpn_client_data.get("name"),
            "purpose": vpn_client_data.get("purpose", "vpn-client"),
            "vpn_type": vpn_client_data.get("vpn_type"),
            "ip_subnet": vpn_client_data.get("ip_subnet"),
            "openvpn_id": vpn_client_data.get("openvpn_id"),
            "wireguard_id": vpn_client_data.get("wireguard_id"),
            "remote_host": vpn_client_data.get("remote_host"),
        }

    async def _async_set_enabled(self, *, enabled: bool) -> None:
        """Enable or disable the VPN client."""
        action = "Enabling" if enabled else "Disabling"
        _LOGGER.debug(
            "%s VPN client %s in site %s",
            action,
            self._client_id,
            self._site_id,
        )

        await async_call_coordinator_action(
            self.coordinator,
            "async_set_vpn_client_enabled",
            f"Unable to update VPN client {self._client_id}",
            self._site_id,
            self._client_id,
            enabled=enabled,
            fallback_factory=lambda: (
                self.coordinator.network_client.vpn_clients.update_vpn_client(
                    self.coordinator.resolve_legacy_site_name(self._site_id),
                    self._client_id,
                    enabled=enabled,
                )
            ),
        )
        self._update_local_state(enabled=enabled)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the VPN client."""
        _ = kwargs
        await self._async_set_enabled(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the VPN client."""
        _ = kwargs
        await self._async_set_enabled(enabled=False)


# Backward compatibility aliases for switch classes
UnifiPolicyBasedRouteSwitch = UnifiInsightsPolicyBasedRouteSwitch
UnifiVpnClientSwitch = UnifiInsightsVpnClientSwitch


class UnifiProtectStatusLightSwitch(UnifiProtectEntity, SwitchEntity):
    """Representation of a UniFi Protect Camera Status Light Switch."""

    _attr_has_entity_name = True
    _attr_translation_key = "status_light"

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
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
        camera_data = self.coordinator.data["protect"]["cameras"].get(
            self._device_id, {}
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

        await async_call_coordinator_action(
            self.coordinator,
            "async_update_camera_settings",
            f"Unable to turn on status light for camera {self._device_id}",
            self._device_id,
            fallback_factory=lambda: self.coordinator.protect_client.cameras.update(  # type: ignore[union-attr]
                self._device_id,
                ledSettings={"isEnabled": True},
            ),
            ledSettings={"isEnabled": True},
        )
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the status light off."""
        _ = kwargs
        _LOGGER.debug("Turning off status light for camera %s", self._device_id)

        await async_call_coordinator_action(
            self.coordinator,
            "async_update_camera_settings",
            f"Unable to turn off status light for camera {self._device_id}",
            self._device_id,
            fallback_factory=lambda: self.coordinator.protect_client.cameras.update(  # type: ignore[union-attr]
                self._device_id,
                ledSettings={"isEnabled": False},
            ),
            ledSettings={"isEnabled": False},
        )
        self._attr_is_on = False
        self.async_write_ha_state()


class UnifiProtectHighFPSSwitch(UnifiProtectEntity, SwitchEntity):
    """Representation of a UniFi Protect Camera High FPS Mode Switch."""

    _attr_has_entity_name = True
    _attr_translation_key = "high_fps_mode"

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
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
        camera_data = self.coordinator.data["protect"]["cameras"].get(
            self._device_id, {}
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

        await async_call_coordinator_action(
            self.coordinator,
            "async_update_camera_settings",
            f"Unable to enable high FPS mode for camera {self._device_id}",
            self._device_id,
            fallback_factory=lambda: self.coordinator.protect_client.cameras.update(  # type: ignore[union-attr]
                self._device_id,
                videoMode=VIDEO_MODE_HIGH_FPS,
            ),
            videoMode=VIDEO_MODE_HIGH_FPS,
        )
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable high FPS mode (return to default)."""
        _ = kwargs
        _LOGGER.debug("Disabling high FPS mode for camera %s", self._device_id)

        await async_call_coordinator_action(
            self.coordinator,
            "async_update_camera_settings",
            f"Unable to disable high FPS mode for camera {self._device_id}",
            self._device_id,
            fallback_factory=lambda: self.coordinator.protect_client.cameras.update(  # type: ignore[union-attr]
                self._device_id,
                videoMode=VIDEO_MODE_DEFAULT,
            ),
            videoMode=VIDEO_MODE_DEFAULT,
        )
        self._attr_is_on = False
        self.async_write_ha_state()


class UnifiClientBlockSwitch(CoordinatorEntity["UnifiFacadeCoordinator"], SwitchEntity):
    """
    Switch to allow/block a network client.

    When ON (is_on=True): Client is allowed (not blocked)
    When OFF (is_on=False): Client is blocked
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
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
        result: dict[str, Any] = (
            self.coordinator.data.get("clients", {})
            .get(self._site_id, {})
            .get(self._client_id, {})
        )
        return result

    @property
    def available(self) -> bool:
        """Return if switch is available."""
        return bool(self.coordinator.device_available and self._get_client_data())

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

        await async_call_coordinator_action(
            self.coordinator,
            "async_unblock_client",
            f"Unable to allow client {self._client_id}",
            self._site_id,
            self._client_id,
        )
        _LOGGER.info(
            "Successfully allowed client %s in site %s",
            self._client_id,
            self._site_id,
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Block the client."""
        _ = kwargs
        _LOGGER.debug("Blocking client %s in site %s", self._client_id, self._site_id)

        await async_call_coordinator_action(
            self.coordinator,
            "async_block_client",
            f"Unable to block client {self._client_id}",
            self._site_id,
            self._client_id,
        )
        _LOGGER.info(
            "Successfully blocked client %s in site %s",
            self._client_id,
            self._site_id,
        )
        await self.coordinator.async_request_refresh()


class UnifiWifiSwitch(CoordinatorEntity["UnifiFacadeCoordinator"], SwitchEntity):
    """Switch to enable/disable a WiFi network."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
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
        result: dict[str, Any] = (
            self.coordinator.data.get("wifi", {})
            .get(self._site_id, {})
            .get(self._wifi_id, {})
        )
        return result or self._wifi_data

    @property
    def available(self) -> bool:
        """Return if switch is available."""
        return bool(
            self.coordinator.wifi_available(self._site_id) and self._get_wifi_data()
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

        await async_call_coordinator_action(
            self.coordinator,
            "async_update_wifi_network",
            f"Unable to enable WiFi network {self._wifi_id}",
            self._site_id,
            self._wifi_id,
            fallback_factory=lambda: self.coordinator.network_client.wifi.update(
                self._site_id,
                self._wifi_id,
                enabled=True,
            ),
            enabled=True,
        )
        _LOGGER.info(
            "Successfully enabled WiFi network %s in site %s",
            self._wifi_id,
            self._site_id,
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the WiFi network."""
        _ = kwargs
        _LOGGER.debug(
            "Disabling WiFi network %s in site %s", self._wifi_id, self._site_id
        )

        await async_call_coordinator_action(
            self.coordinator,
            "async_update_wifi_network",
            f"Unable to disable WiFi network {self._wifi_id}",
            self._site_id,
            self._wifi_id,
            fallback_factory=lambda: self.coordinator.network_client.wifi.update(
                self._site_id,
                self._wifi_id,
                enabled=False,
            ),
            enabled=False,
        )
        _LOGGER.info(
            "Successfully disabled WiFi network %s in site %s",
            self._wifi_id,
            self._site_id,
        )
        await self.coordinator.async_request_refresh()


class UnifiOutletSwitch(CoordinatorEntity["UnifiFacadeCoordinator"], SwitchEntity):
    """Per-outlet relay switch for UniFi PDU and smart power strip devices."""

    _attr_has_entity_name = True
    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        site_id: str,
        device_id: str,
        outlet_index: int,
        outlet_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the outlet switch."""
        super().__init__(coordinator)
        self._site_id = site_id
        self._device_id = device_id
        self._outlet_index = outlet_index
        self._initial_outlet_data = outlet_data or {}

        outlet = self._get_outlet_data() or self._initial_outlet_data
        outlet_name = outlet.get("name") or f"Outlet {outlet_index}"

        self._attr_unique_id = f"{site_id}_{device_id}_outlet_{outlet_index}"
        self._attr_translation_key = "outlet"
        self._attr_translation_placeholders = {"outlet_name": str(outlet_name)}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{site_id}_{device_id}")},
        )

    def _get_device_data(self) -> dict[str, Any]:
        """Return the coordinator's cached snapshot for this PDU device."""
        device_data = (
            self.coordinator.data.get("devices", {})
            .get(self._site_id, {})
            .get(self._device_id, {})
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

    def _update_local_state(
        self,
        *,
        relay_state: bool | None = None,
        cycle_enabled: bool | None = None,
    ) -> None:
        """Update local coordinator cache for immediate UI feedback."""
        devices = self.coordinator.data.setdefault("devices", {})
        site_devices = devices.setdefault(self._site_id, {})
        device_data = site_devices.get(self._device_id)
        if isinstance(device_data, dict):
            outlet_table = device_data.get("outlet_table", [])
            if isinstance(outlet_table, list):
                for outlet in outlet_table:
                    if not isinstance(outlet, dict):
                        continue
                    idx = outlet.get("index")
                    if idx is None:
                        idx = outlet.get("outlet_idx") or outlet.get("outletIdx")
                    if idx is not None:
                        try:
                            if int(idx) == self._outlet_index:
                                if relay_state is not None:
                                    outlet["relay_state"] = relay_state
                                if cycle_enabled is not None:
                                    outlet["cycle_enabled"] = cycle_enabled
                                break
                        except TypeError, ValueError:
                            continue

    @property
    def available(self) -> bool:
        """Return True if switch is available."""
        if not self.coordinator.device_available:
            return False
        device_data = self._get_device_data()
        if not device_data or not is_device_online(device_data):
            return False
        return self._get_outlet_data() is not None

    @property
    def is_on(self) -> bool:
        """Return True if outlet relay is energized (ON)."""
        outlet = self._get_outlet_data()
        if not outlet:
            return bool(self._initial_outlet_data.get("relay_state", False))
        return bool(outlet.get("relay_state", False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return outlet extra state attributes."""
        outlet = self._get_outlet_data() or self._initial_outlet_data
        attrs: dict[str, Any] = {"index": self._outlet_index}
        for key in (
            "name",
            "relay_state",
            "cycle_enabled",
            "outlet_caps",
            "outlet_voltage",
            "outlet_current",
            "outlet_power",
            "outlet_power_factor",
        ):
            if key in outlet and outlet[key] is not None:
                attrs[key] = outlet[key]
        return attrs

    def _get_legacy_site_name(self) -> str:
        """Resolve the legacy site name via the facade coordinator."""
        return self.coordinator.resolve_legacy_site_name(self._site_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the outlet."""
        _ = kwargs
        _LOGGER.debug(
            "Turning on outlet %d for device %s in site %s",
            self._outlet_index,
            self._device_id,
            self._site_id,
        )
        site_name = self._get_legacy_site_name()

        await async_call_coordinator_action(
            self.coordinator,
            "async_set_outlet_state",
            (
                f"Unable to turn on outlet {self._outlet_index} "
                f"on device {self._device_id}"
            ),
            self._site_id,
            self._device_id,
            self._outlet_index,
            state=True,
            fallback_factory=lambda: (
                self.coordinator.network_client.devices.set_outlet_state(
                    site_name,
                    self._device_id,
                    self._outlet_index,
                    state=True,
                    current_device=self._get_device_data(),
                )
            ),
        )
        self._update_local_state(relay_state=True)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the outlet."""
        _ = kwargs
        _LOGGER.debug(
            "Turning off outlet %d for device %s in site %s",
            self._outlet_index,
            self._device_id,
            self._site_id,
        )
        site_name = self._get_legacy_site_name()

        await async_call_coordinator_action(
            self.coordinator,
            "async_set_outlet_state",
            (
                f"Unable to turn off outlet {self._outlet_index} "
                f"on device {self._device_id}"
            ),
            self._site_id,
            self._device_id,
            self._outlet_index,
            state=False,
            fallback_factory=lambda: (
                self.coordinator.network_client.devices.set_outlet_state(
                    site_name,
                    self._device_id,
                    self._outlet_index,
                    state=False,
                    current_device=self._get_device_data(),
                )
            ),
        )
        self._update_local_state(relay_state=False)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class UnifiOutletCycleSwitch(CoordinatorEntity["UnifiFacadeCoordinator"], SwitchEntity):
    """Switch to enable or disable automatic modem power cycling for a PDU outlet."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        site_id: str,
        device_id: str,
        outlet_index: int,
        outlet_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the outlet cycle switch."""
        super().__init__(coordinator)
        self._site_id = site_id
        self._device_id = device_id
        self._outlet_index = outlet_index
        self._initial_outlet_data = outlet_data or {}

        outlet = self._get_outlet_data() or self._initial_outlet_data
        outlet_name = outlet.get("name") or f"Outlet {outlet_index}"

        self._attr_unique_id = (
            f"{site_id}_{device_id}_outlet_{outlet_index}_cycle_enabled"
        )
        self._attr_translation_key = "outlet_cycle_enabled"
        self._attr_translation_placeholders = {"outlet_name": str(outlet_name)}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{site_id}_{device_id}")},
        )

    def _get_device_data(self) -> dict[str, Any]:
        """Return the coordinator's cached snapshot for this PDU device."""
        device_data = (
            self.coordinator.data.get("devices", {})
            .get(self._site_id, {})
            .get(self._device_id, {})
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

    def _update_local_state(
        self,
        *,
        cycle_enabled: bool,
    ) -> None:
        """Update local coordinator cache for immediate UI feedback."""
        devices = self.coordinator.data.setdefault("devices", {})
        site_devices = devices.setdefault(self._site_id, {})
        device_data = site_devices.get(self._device_id)
        if isinstance(device_data, dict):
            outlet_table = device_data.get("outlet_table", [])
            if isinstance(outlet_table, list):
                for outlet in outlet_table:
                    if not isinstance(outlet, dict):
                        continue
                    idx = outlet.get("index")
                    if idx is None:
                        idx = outlet.get("outlet_idx") or outlet.get("outletIdx")
                    if idx is not None:
                        try:
                            if int(idx) == self._outlet_index:
                                outlet["cycle_enabled"] = cycle_enabled
                                break
                        except TypeError, ValueError:
                            continue

    @property
    def available(self) -> bool:
        """Return True if switch is available."""
        if not self.coordinator.device_available:
            return False
        device_data = self._get_device_data()
        if not device_data or not is_device_online(device_data):
            return False
        return self._get_outlet_data() is not None

    def _get_current_relay_state(self) -> bool:
        """Return the outlet's reported relay state, defaulting to OFF."""
        outlet = self._get_outlet_data() or self._initial_outlet_data
        return bool(outlet.get("relay_state", False))

    @property
    def is_on(self) -> bool:
        """Return True if power cycling is enabled on this outlet."""
        outlet = self._get_outlet_data()
        if not outlet:
            return bool(self._initial_outlet_data.get("cycle_enabled", False))
        return bool(outlet.get("cycle_enabled", False))

    def _get_legacy_site_name(self) -> str:
        """Resolve the legacy site name via the facade coordinator."""
        return self.coordinator.resolve_legacy_site_name(self._site_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable power cycling on the outlet."""
        _ = kwargs
        _LOGGER.debug(
            "Enabling power cycle for outlet %d on device %s in site %s",
            self._outlet_index,
            self._device_id,
            self._site_id,
        )
        current_relay = self._get_current_relay_state()
        site_name = self._get_legacy_site_name()

        await async_call_coordinator_action(
            self.coordinator,
            "async_set_outlet_state",
            (
                f"Unable to enable power cycle on outlet {self._outlet_index} "
                f"on device {self._device_id}"
            ),
            self._site_id,
            self._device_id,
            self._outlet_index,
            state=current_relay,
            cycle_enabled=True,
            fallback_factory=lambda: (
                self.coordinator.network_client.devices.set_outlet_state(
                    site_name,
                    self._device_id,
                    self._outlet_index,
                    state=current_relay,
                    cycle_enabled=True,
                    current_device=self._get_device_data(),
                )
            ),
        )
        self._update_local_state(cycle_enabled=True)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable power cycling on the outlet."""
        _ = kwargs
        _LOGGER.debug(
            "Disabling power cycle for outlet %d on device %s in site %s",
            self._outlet_index,
            self._device_id,
            self._site_id,
        )
        current_relay = self._get_current_relay_state()
        site_name = self._get_legacy_site_name()

        await async_call_coordinator_action(
            self.coordinator,
            "async_set_outlet_state",
            (
                f"Unable to disable power cycle on outlet {self._outlet_index} "
                f"on device {self._device_id}"
            ),
            self._site_id,
            self._device_id,
            self._outlet_index,
            state=current_relay,
            cycle_enabled=False,
            fallback_factory=lambda: (
                self.coordinator.network_client.devices.set_outlet_state(
                    site_name,
                    self._device_id,
                    self._outlet_index,
                    state=current_relay,
                    cycle_enabled=False,
                    current_device=self._get_device_data(),
                )
            ),
        )
        self._update_local_state(cycle_enabled=False)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
