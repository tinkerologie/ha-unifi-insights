# Copyright 2026 UniFi Insights contributors
"""Event entity that reports UniFi network rule changes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components.event import EventEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinators.rule_audit import RULE_CHANGE_TYPES
from .entity import build_site_device_info
from .helpers import async_migrate_site_entity

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinators.facade import UnifiFacadeCoordinator

# Translation key and unique-id suffix. It is listed in SITE_ENTITY_SUFFIXES so a
# console identity change re-keys this entity with the other site entities.
RULE_CHANGE_EVENT_KEY = "network_rule_changes"

# The only keys of a change record that reach the event. Field values, addresses
# and domains are never part of a change record in the first place.
EVENT_ATTRIBUTE_KEYS: tuple[str, ...] = (
    "site_id",
    "rule_type",
    "rule_id",
    "rule_name",
    "enabled",
    "changed_fields",
)


def _event_data(change: Mapping[str, Any]) -> dict[str, Any]:
    """Coerce event data from a change record to documented types."""
    data: dict[str, Any] = {}
    for key in EVENT_ATTRIBUTE_KEYS:
        val = change.get(key)
        if key == "changed_fields":
            data[key] = (
                [field for field in val if isinstance(field, str)]
                if isinstance(val, list)
                else []
            )
        elif key == "enabled":
            data[key] = val if isinstance(val, bool) else None
        elif key == "rule_name":
            data[key] = val if isinstance(val, str) else None
        else:
            data[key] = val
    return data


class UnifiInsightsNetworkRuleChangeEvent(
    CoordinatorEntity["UnifiFacadeCoordinator"],
    EventEntity,
):
    """Event entity that fires once for each added, modified or deleted rule."""

    _attr_has_entity_name = True
    _attr_translation_key = RULE_CHANGE_EVENT_KEY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_event_types: ClassVar[list[str]] = list(RULE_CHANGE_TYPES)  # type: ignore[misc]

    def __init__(
        self, coordinator: UnifiFacadeCoordinator, site_id: str, *, unique_id: str
    ) -> None:
        """Initialize the rule change event entity."""
        super().__init__(coordinator)
        self._site_id = site_id
        self._last_available = self.available
        self._attr_unique_id = unique_id
        # Changes detected before this entity existed are history, not news.
        self._last_sequence = max(
            (change["sequence"] for change in self._site_changes()), default=0
        )
        site_info = build_site_device_info(coordinator.data, site_id)
        self._attr_device_info = DeviceInfo(**site_info)  # type: ignore[typeddict-item]

    def _site_changes(self) -> list[dict[str, Any]]:
        """Return this site's well-formed change records, oldest first."""
        data: Any = self.coordinator.data
        by_site = data.get("rule_changes") if isinstance(data, dict) else None
        changes = by_site.get(self._site_id) if isinstance(by_site, dict) else None
        if not isinstance(changes, list):
            return []
        valid = [
            change
            for change in changes
            if isinstance(change, dict)
            and isinstance(change.get("sequence"), int)
            and change.get("change_type") in RULE_CHANGE_TYPES
        ]
        return sorted(valid, key=lambda change: change["sequence"])

    @property
    def available(self) -> bool:
        """Return True while the config coordinator is refreshing successfully."""
        return bool(self.coordinator.config_available)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Fire one event for every change not reported yet."""
        wrote_event = False
        for change in self._site_changes():
            if change["sequence"] <= self._last_sequence:
                continue
            self._last_sequence = change["sequence"]
            self._trigger_event(
                change["change_type"],
                _event_data(change),
            )
            self.async_write_ha_state()
            wrote_event = True
        available = self.available
        if not wrote_event and available != self._last_available:
            self.async_write_ha_state()
        self._last_available = available


def discover_network_rule_change_events(
    coordinator: UnifiFacadeCoordinator,
    known_site_ids: set[str],
    *,
    hass: HomeAssistant,
    entry: ConfigEntry,
    namespace: str | None,
) -> list[UnifiInsightsNetworkRuleChangeEvent]:
    """
    Return one rule change event entity for each site not seen before.

    The unique id is scoped by the console namespace, like every other logical
    site entity, because two consoles can report the same site id. A site whose
    identity cannot be claimed safely is skipped and retried on the next tick.
    """
    data: Any = coordinator.data
    sites = data.get("sites") if isinstance(data, dict) else None
    if not isinstance(sites, dict):
        return []
    entities: list[UnifiInsightsNetworkRuleChangeEvent] = []
    for site_id in sites:
        if not isinstance(site_id, str) or site_id in known_site_ids:
            continue
        unique_id = async_migrate_site_entity(
            hass, entry, "event", namespace, site_id, suffix=RULE_CHANGE_EVENT_KEY
        )
        if unique_id is None:
            continue
        known_site_ids.add(site_id)
        entities.append(
            UnifiInsightsNetworkRuleChangeEvent(
                coordinator, site_id, unique_id=unique_id
            )
        )
    return entities
