# Copyright 2026 UniFi Insights contributors
"""Tests for UniFi network rule change event entities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from homeassistant.const import CONF_API_KEY, EVENT_STATE_CHANGED, EntityCategory
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
    async_capture_events,
)

from custom_components.unifi_insights.const import (
    CONF_CONSOLE_ID,
    DOMAIN,
)
from custom_components.unifi_insights.event import async_setup_entry
from custom_components.unifi_insights.network_rule_audit_event import (
    EVENT_ATTRIBUTE_KEYS,
    UnifiInsightsNetworkRuleChangeEvent,
    discover_network_rule_change_events,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_UNIQUE_ID = "console_a_site1_network_rule_changes"


def _change(
    sequence: int, change_type: str = "modified", **extra: Any
) -> dict[str, Any]:
    """Build one synthetic change record."""
    record: dict[str, Any] = {
        "sequence": sequence,
        "change_type": change_type,
        "site_id": "site1",
        "rule_type": "port_forward",
        "rule_id": f"pf{sequence}",
        "rule_name": f"Port Forward {sequence}",
        "enabled": True,
        "changed_fields": ["enabled"] if change_type == "modified" else [],
    }
    record.update(extra)
    return record


def _entry(hass: HomeAssistant, namespace: str | None = "console_a") -> MockConfigEntry:
    """Create and register a mock config entry."""
    data: dict[str, Any] = {CONF_API_KEY: "synthetic_credential"}
    if namespace is not None:
        data[CONF_CONSOLE_ID] = namespace
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        entry_id="entry_a",
        unique_id=namespace,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_coordinator() -> MagicMock:
    """Create a mock facade coordinator for rule audit testing."""
    coord = MagicMock()
    coord.protect_client = None
    coord.config_available = True
    coord.data = {
        "sites": {"site1": {"id": "site1", "name": "Default"}},
        "devices": {},
        "rule_changes": {},
    }
    return coord


def test_identity_and_metadata(mock_coordinator: MagicMock) -> None:
    """Event entity initializes with correct identity, metadata, and device info."""
    entity = UnifiInsightsNetworkRuleChangeEvent(
        mock_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    assert entity.unique_id == _UNIQUE_ID
    assert entity.translation_key == "network_rule_changes"
    assert entity.has_entity_name is True
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert entity.event_types == ["added", "modified", "deleted"]
    assert entity.device_info == {
        "identifiers": {(DOMAIN, "site_site1")},
        "name": "UniFi Site (Default)",
        "manufacturer": "Ubiquiti Inc.",
        "model": "UniFi Site",
    }


def test_existing_changes_are_not_replayed(
    mock_coordinator: MagicMock,
) -> None:
    """Changes present before entity creation are treated as history."""
    mock_coordinator.data["rule_changes"] = {
        "site1": [_change(1), _change(2)],
    }
    entity = UnifiInsightsNetworkRuleChangeEvent(
        mock_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    entity._trigger_event = MagicMock()
    entity.async_write_ha_state = MagicMock()
    entity._handle_coordinator_update()
    entity._trigger_event.assert_not_called()
    entity.async_write_ha_state.assert_not_called()


def test_each_new_change_fires_once_in_order(
    mock_coordinator: MagicMock,
) -> None:
    """New changes fire in sequence order; attributes contain allow-list keys only."""
    entity = UnifiInsightsNetworkRuleChangeEvent(
        mock_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    entity._trigger_event = MagicMock()
    entity.async_write_ha_state = MagicMock()

    mock_coordinator.data["rule_changes"] = {
        "site1": [
            _change(2, "deleted", leaked="10.0.0.1"),
            _change(1, "added"),
        ],
        "site2": [_change(3, "added", site_id="site2")],
    }
    entity._handle_coordinator_update()

    assert entity._trigger_event.call_count == 2
    calls = entity._trigger_event.call_args_list
    assert [c[0][0] for c in calls] == ["added", "deleted"]
    second_payload = calls[1][0][1]
    assert tuple(second_payload) == EVENT_ATTRIBUTE_KEYS
    assert "leaked" not in second_payload
    assert entity.async_write_ha_state.call_count == 2

    # Subsequent update does not refire already triggered events
    entity._handle_coordinator_update()
    assert entity._trigger_event.call_count == 2
    assert entity.async_write_ha_state.call_count == 2


@pytest.mark.parametrize(
    "rule_changes",
    [
        None,
        {"site1": None},
        {"site1": ["x", {"sequence": "1", "change_type": "added"}]},
        {"site1": [{"sequence": 5, "change_type": "renamed"}]},
    ],
)
def test_malformed_change_data_fires_nothing(
    mock_coordinator: MagicMock, rule_changes: Any
) -> None:
    """Malformed change structures are ignored and fire no events."""
    entity = UnifiInsightsNetworkRuleChangeEvent(
        mock_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    entity._trigger_event = MagicMock()
    entity.async_write_ha_state = MagicMock()
    mock_coordinator.data["rule_changes"] = rule_changes
    entity._handle_coordinator_update()
    entity._trigger_event.assert_not_called()


def test_non_dict_coordinator_data_is_tolerated(
    mock_coordinator: MagicMock,
) -> None:
    """Non-dict coordinator data yields empty changes and skips discovery safely."""
    entity = UnifiInsightsNetworkRuleChangeEvent(
        mock_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    mock_coordinator.data = None
    assert entity._site_changes() == []
    known: set[str] = set()
    entry = MagicMock()
    assert (
        discover_network_rule_change_events(
            mock_coordinator,
            known,
            hass=MagicMock(),
            entry=entry,
            namespace="console_a",
        )
        == []
    )


def test_available_follows_config_coordinator(
    mock_coordinator: MagicMock,
) -> None:
    """Entity availability tracks coordinator config_available property."""
    entity = UnifiInsightsNetworkRuleChangeEvent(
        mock_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    mock_coordinator.config_available = True
    assert entity.available is True
    mock_coordinator.config_available = False
    assert entity.available is False


async def test_discovery_adds_one_namespaced_entity_per_new_site(
    hass: HomeAssistant, mock_coordinator: MagicMock
) -> None:
    """Discovery creates namespaced entities for newly seen sites only."""
    entry = _entry(hass)
    known: set[str] = set()
    res1 = discover_network_rule_change_events(
        mock_coordinator, known, hass=hass, entry=entry, namespace="console_a"
    )
    assert [e.unique_id for e in res1] == ["console_a_site1_network_rule_changes"]

    mock_coordinator.data["sites"]["site2"] = {"id": "site2", "name": "Site 2"}
    mock_coordinator.data["sites"][7] = {"id": "7"}
    res2 = discover_network_rule_change_events(
        mock_coordinator, known, hass=hass, entry=entry, namespace="console_a"
    )
    assert [e.unique_id for e in res2] == ["console_a_site2_network_rule_changes"]

    res3 = discover_network_rule_change_events(
        mock_coordinator, known, hass=hass, entry=entry, namespace="console_a"
    )
    assert res3 == []


async def test_discovery_without_namespace_skips_and_retries(
    hass: HomeAssistant, mock_coordinator: MagicMock
) -> None:
    """Missing namespace skips entity discovery until resolved on entry update."""
    entry = _entry(hass, None)
    known: set[str] = set()
    res1 = discover_network_rule_change_events(
        mock_coordinator, known, hass=hass, entry=entry, namespace=None
    )
    assert res1 == []
    assert known == set()

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_CONSOLE_ID: "console_a"}
    )
    res2 = discover_network_rule_change_events(
        mock_coordinator, known, hass=hass, entry=entry, namespace="console_a"
    )
    assert len(res2) == 1
    assert res2[0].unique_id == "console_a_site1_network_rule_changes"
    assert "site1" in known


async def test_setup_entry_adds_rule_change_event_without_protect(
    hass: HomeAssistant, mock_coordinator: MagicMock
) -> None:
    """Event setup registers rule change entities even when Protect is absent."""
    entry = _entry(hass)
    entry.runtime_data = MagicMock(coordinator=mock_coordinator)
    listeners: list[Any] = []
    mock_coordinator.async_add_listener.side_effect = lambda cb: (
        listeners.append(cb) or (lambda: None)
    )
    added_entities: list[Any] = []

    def _async_add(ents: list[Any]) -> None:
        added_entities.extend(ents)

    await async_setup_entry(hass, entry, _async_add)
    assert len(added_entities) == 1
    assert isinstance(added_entities[0], UnifiInsightsNetworkRuleChangeEvent)
    assert mock_coordinator.async_add_listener.call_count == 1

    mock_coordinator.data["sites"]["site2"] = {"id": "site2", "name": "Site 2"}
    assert len(listeners) == 1
    listeners[0]()
    assert len(added_entities) == 2


async def test_two_changes_in_one_refresh_are_two_state_changes(
    hass: HomeAssistant, mock_coordinator: MagicMock, freezer: Any
) -> None:
    """Through the real state machine: every change is its own state write."""
    freezer.move_to("2026-01-01 00:00:00+00:00")
    entity = UnifiInsightsNetworkRuleChangeEvent(
        mock_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    platform = MockEntityPlatform(hass, domain="event", platform_name=DOMAIN)
    await platform.async_add_entities([entity])
    await hass.async_block_till_done()
    events = async_capture_events(hass, EVENT_STATE_CHANGED)

    mock_coordinator.data["rule_changes"] = {
        "site1": [
            _change(1, "added"),
            _change(2, "deleted", leaked="10.0.0.1"),
        ]
    }
    entity._handle_coordinator_update()
    await hass.async_block_till_done()

    states = [e.data["new_state"] for e in events]
    assert [s.attributes["event_type"] for s in states] == [
        "added",
        "deleted",
    ]
    assert [s.attributes["rule_id"] for s in states] == ["pf1", "pf2"]
    t0 = dt_util.parse_datetime(states[0].state)
    t1 = dt_util.parse_datetime(states[1].state)
    assert t0 is not None
    assert t1 is not None
    assert t0 <= t1
    assert "leaked" not in states[1].attributes


async def test_event_data_coerces_unexpected_value_types(
    hass: HomeAssistant, mock_coordinator: MagicMock
) -> None:
    """Unexpected value types are coerced before they reach the event data."""
    entity = UnifiInsightsNetworkRuleChangeEvent(
        mock_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    platform = MockEntityPlatform(hass, domain="event", platform_name=DOMAIN)
    await platform.async_add_entities([entity])
    await hass.async_block_till_done()

    mock_coordinator.data["rule_changes"] = {
        "site1": [
            _change(
                1,
                "modified",
                changed_fields=["enabled", {"fwd": "192.0.2.9"}, 5],
                enabled="true",
                rule_name={"x": 1},
            )
        ]
    }
    entity._handle_coordinator_update()
    await hass.async_block_till_done()

    state = hass.states.get(entity.entity_id)
    assert state is not None
    assert state.attributes["changed_fields"] == ["enabled"]
    assert state.attributes["enabled"] is None
    assert state.attributes["rule_name"] is None
    assert "192.0.2.9" not in str(state.attributes)


async def test_unavailable_round_trip_writes_no_new_event(
    hass: HomeAssistant, mock_coordinator: MagicMock, freezer: Any
) -> None:
    """An entity becoming unavailable and returning does not fire a new event."""
    freezer.move_to("2026-01-01 00:00:00+00:00")
    entity = UnifiInsightsNetworkRuleChangeEvent(
        mock_coordinator, "site1", unique_id=_UNIQUE_ID
    )
    entity._trigger_event = MagicMock(wraps=entity._trigger_event)
    platform = MockEntityPlatform(hass, domain="event", platform_name=DOMAIN)
    await platform.async_add_entities([entity])
    await hass.async_block_till_done()

    events = async_capture_events(hass, EVENT_STATE_CHANGED)

    # Fire one change
    mock_coordinator.data["rule_changes"] = {"site1": [_change(1)]}
    entity._handle_coordinator_update()
    await hass.async_block_till_done()

    # Make unavailable
    mock_coordinator.config_available = False
    entity._handle_coordinator_update()
    await hass.async_block_till_done()

    # Make available again
    mock_coordinator.config_available = True
    entity._handle_coordinator_update()
    await hass.async_block_till_done()

    states = [e.data["new_state"] for e in events]
    ts = states[0].state
    assert [s.state for s in states] == [ts, "unavailable", ts]
    entity._trigger_event.assert_called_once()
