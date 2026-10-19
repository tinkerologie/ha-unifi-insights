# Copyright 2026 UniFi Insights contributors
"""Switches for UniFi network rules (port forwards, traffic rules, NAT, routes)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from homeassistant.components.switch import (
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinators.config_sections import is_predefined_nat_rule
from .entity import build_site_device_info

if TYPE_CHECKING:
    from collections.abc import Callable

    from .coordinators.config import UnifiConfigCoordinator
    from .coordinators.facade import UnifiFacadeCoordinator


class RuleToggleCommand(Protocol):
    """A config-coordinator command that enables or disables one rule."""

    async def __call__(self, site_id: str, rule_id: str, *, enabled: bool) -> None:
        """Enable or disable the rule."""


@dataclass(frozen=True, kw_only=True)
class UnifiInsightsNetworkRuleSwitchEntityDescription(
    SwitchEntityDescription,
):
    """Static metadata for one family of console rule switches."""

    data_key: str
    unique_id_suffix: str
    name_field: str
    unnamed_translation_key: str
    available_fn: Callable[[UnifiFacadeCoordinator, str], bool]
    command: Callable[[UnifiConfigCoordinator], RuleToggleCommand]
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]]
    is_hidden_fn: Callable[[dict[str, Any]], bool] | None = None


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
    name_field="name",
    available_fn=lambda coordinator, site_id: coordinator.port_forwards_available(
        site_id
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
    name_field="description",
    available_fn=lambda coordinator, site_id: coordinator.traffic_rules_available(
        site_id
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
    name_field="description",
    available_fn=lambda coordinator, site_id: coordinator.nat_rules_available(site_id),
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
    name_field="name",
    available_fn=lambda coordinator, site_id: coordinator.static_routes_available(
        site_id
    ),
    command=lambda owner: owner.async_set_static_route_enabled,
    attributes_fn=_static_route_attributes,
)

NETWORK_RULE_SWITCH_TYPES = (
    PORT_FORWARD_SWITCH,
    TRAFFIC_RULE_SWITCH,
    NAT_RULE_SWITCH,
    STATIC_ROUTE_SWITCH,
)


class UnifiInsightsNetworkRuleSwitch(
    CoordinatorEntity["UnifiFacadeCoordinator"],
    SwitchEntity,
):
    """Switch to enable or disable one UniFi network rule."""

    entity_description: UnifiInsightsNetworkRuleSwitchEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: UnifiFacadeCoordinator,
        description: UnifiInsightsNetworkRuleSwitchEntityDescription,
        site_id: str,
        rule_id: str,
    ) -> None:
        """Initialize the network rule switch."""
        super().__init__(coordinator)
        self.entity_description = description
        self._site_id = site_id
        self._rule_id = rule_id
        self._attr_unique_id = f"{site_id}_{rule_id}_{description.unique_id_suffix}"

        rule_name = self._get_rule_data().get(description.name_field)
        if isinstance(rule_name, str) and rule_name.strip():
            self._attr_translation_key = description.translation_key
            self._attr_translation_placeholders = {"rule_name": rule_name.strip()}
        else:
            self._attr_translation_key = description.unnamed_translation_key
            self._attr_translation_placeholders = {"rule_id": rule_id}

        site_info = build_site_device_info(coordinator.data, site_id)
        self._attr_device_info = DeviceInfo(**site_info)  # type: ignore[typeddict-item]

    def _get_rule_data(self) -> dict[str, Any]:
        """Get rule data from coordinator."""
        data: Any = self.coordinator.data
        if not isinstance(data, dict):
            return {}
        section = data.get(self.entity_description.data_key)
        if not isinstance(section, dict):
            return {}
        site_rules = section.get(self._site_id)
        if not isinstance(site_rules, dict):
            return {}
        rule = site_rules.get(self._rule_id)
        return rule if isinstance(rule, dict) else {}

    @property
    def available(self) -> bool:
        """Return if the switch is available."""
        desc = self.entity_description
        if desc.is_hidden_fn is not None and desc.is_hidden_fn(self._get_rule_data()):
            return False
        return bool(desc.available_fn(self.coordinator, self._site_id)) and isinstance(
            self._get_rule_data().get("enabled"), bool
        )

    @property
    def is_on(self) -> bool:
        """Return True if the rule is enabled."""
        return self._get_rule_data().get("enabled") is True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return rule extra state attributes."""
        return {
            "rule_id": self._rule_id,
            **self.entity_description.attributes_fn(self._get_rule_data()),
        }

    async def _async_set_enabled(self, *, enabled: bool) -> None:
        """Enable or disable the network rule through its command owner."""
        command = self.entity_description.command(self.coordinator.config_coordinator)
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
    coordinator: UnifiFacadeCoordinator,
    known_keys: set[tuple[Any, ...]],
) -> list[SwitchEntity]:
    """Discover the switch entities of every network rule family."""
    entities: list[SwitchEntity] = []
    data: Any = coordinator.data
    if not isinstance(data, dict):
        return entities

    for desc in NETWORK_RULE_SWITCH_TYPES:
        section = data.get(desc.data_key)
        if not isinstance(section, dict):
            continue
        for site_id, site_rules in section.items():
            if not isinstance(site_rules, dict):
                continue
            for rule_id, rule in site_rules.items():
                if not isinstance(rule, dict) or not isinstance(
                    rule.get("enabled"), bool
                ):
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
