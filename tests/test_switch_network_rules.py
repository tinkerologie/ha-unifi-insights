# Copyright 2026 UniFi Insights contributors
"""Tests for port forward and traffic rule switches."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from unifi_official_api.network.models import NatRule, StaticRoute

from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.switch import (
    NAT_RULE_SWITCH,
    NETWORK_RULE_SWITCH_TYPES,
    PORT_FORWARD_SWITCH,
    STATIC_ROUTE_SWITCH,
    TRAFFIC_RULE_SWITCH,
    UnifiInsightsNetworkRuleSwitch,
    UnifiInsightsNetworkRuleSwitchEntityDescription,
    async_setup_entry,
    discover_network_rule_switches,
)
from tests.fixtures.assertions import require_instance
from tests.fixtures.command_owners import autospec_command_owners
from tests.fixtures.facade_data import (
    facade_data,
    set_availability,
    with_rule,
    with_sections,
)
from tests.fixtures.network_rule_responses import (
    nat_rule_record,
    port_forward_record,
    static_route_record,
    traffic_rule_record,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.fixture
def mock_coordinator() -> MagicMock:
    """Create mock coordinator with port forward and traffic rule data."""
    coordinator = MagicMock()
    coordinator.protect_client = None
    coordinator.network_client = MagicMock()
    coordinator.network_client.base_url = "https://192.168.1.1"
    coordinator.data = facade_data(
        sites={"site1": {"id": "site1", "name": "Default"}},
        port_forwards={
            "site1": {
                "pf1": port_forward_record(_id="pf1", name="Plex"),
            }
        },
        traffic_rules={
            "site1": {
                "tr1": traffic_rule_record(_id="tr1", description="Bedtime"),
            }
        },
        protect={
            "cameras": {},
            "lights": {},
            "sensors": {},
            "nvrs": {},
            "viewers": {},
            "chimes": {},
            "liveviews": {},
        },
    )
    return coordinator


@pytest.mark.asyncio
async def test_setup_entry_adds_port_forward_and_traffic_rule_switches(
    hass: HomeAssistant, mock_coordinator: MagicMock
) -> None:
    """Platform setup adds both port forward and traffic rule switches."""
    mock_entry = MagicMock()
    mock_entry.options = {}
    mock_entry.entry_id = "test_entry_id"
    mock_entry.runtime_data = MagicMock()
    mock_entry.runtime_data.coordinator = mock_coordinator

    async_add_entities = MagicMock()
    await async_setup_entry(hass, mock_entry, async_add_entities)

    entities = async_add_entities.call_args[0][0]
    rule_switches = [
        entity
        for entity in entities
        if isinstance(entity, UnifiInsightsNetworkRuleSwitch)
    ]
    assert len(rule_switches) == 2
    assert {e._rule_id for e in rule_switches} == {"pf1", "tr1"}


def test_discovery_skips_malformed_sites_records_and_non_bool_enabled(
    mock_coordinator: MagicMock,
) -> None:
    """Non-dict collections, non-dict records, and non-bool enabled are skipped."""
    mock_coordinator.data = with_sections(
        mock_coordinator.data,
        port_forwards={
            "site_not_dict": "not-a-dict",
            "site1": {
                "rule_not_dict": "not-a-dict",
                "rule_no_enabled": {"name": "No enabled"},
                "rule_str_enabled": {"name": "Str enabled", "enabled": "true"},
                "rule_int_enabled": {"name": "Int enabled", "enabled": 1},
                "rule_none_enabled": {"name": "None enabled", "enabled": None},
                "valid_rule": {"name": "Valid", "enabled": True},
            },
        },
    )
    known_keys: set[tuple[Any, ...]] = set()
    entities = discover_network_rule_switches(mock_coordinator, known_keys)
    pf_entities = [e for e in entities if e.entity_description.key == "port_forward"]
    assert len(pf_entities) == 1
    assert (
        require_instance(pf_entities[0], UnifiInsightsNetworkRuleSwitch)._rule_id
        == "valid_rule"
    )


@pytest.mark.asyncio
async def test_discovery_adds_new_rule_on_listener_tick_without_duplicates(
    hass: HomeAssistant, mock_coordinator: MagicMock
) -> None:
    """Coordinator update listener adds new entities without duplicates."""
    mock_entry = MagicMock()
    mock_entry.options = {}
    mock_entry.entry_id = "test_entry_id"
    mock_entry.runtime_data = MagicMock()
    mock_entry.runtime_data.coordinator = mock_coordinator

    async_add_entities = MagicMock()
    await async_setup_entry(hass, mock_entry, async_add_entities)

    first_added = async_add_entities.call_args[0][0]
    first_rules = [
        e for e in first_added if isinstance(e, UnifiInsightsNetworkRuleSwitch)
    ]
    assert len(first_rules) == 2

    listener = mock_coordinator.async_add_listener.call_args[0][0]

    call_count = async_add_entities.call_count
    listener()
    assert async_add_entities.call_count == call_count

    mock_coordinator.data = with_rule(
        mock_coordinator.data,
        "port_forwards",
        "site1",
        "pf2",
        {"_id": "pf2", "name": "SSH", "enabled": False},
    )
    listener()
    assert async_add_entities.call_count == call_count + 1
    new_added = async_add_entities.call_args[0][0]
    assert len(new_added) == 1
    assert new_added[0]._rule_id == "pf2"


@pytest.mark.parametrize(
    ("desc", "rule_id", "rule_name"),
    [
        (PORT_FORWARD_SWITCH, "pf1", " Plex "),
        (TRAFFIC_RULE_SWITCH, "tr1", " Bedtime "),
        (NAT_RULE_SWITCH, "nat1", " DNS "),
        (STATIC_ROUTE_SWITCH, "sr1", " Lab "),
    ],
)
def test_unique_id_and_named_translation(
    mock_coordinator: MagicMock,
    desc: UnifiInsightsNetworkRuleSwitchEntityDescription,
    rule_id: str,
    rule_name: str,
) -> None:
    """Named rule populates unique_id, translation_key and trimmed rule_name."""
    mock_coordinator.data = with_sections(
        mock_coordinator.data,
        **{
            desc.data_key: {
                "site1": {rule_id: {desc.name_fields[0]: rule_name, "enabled": True}}
            }
        },
    )
    switch = UnifiInsightsNetworkRuleSwitch(mock_coordinator, desc, "site1", rule_id)
    assert switch.unique_id == f"site1_{rule_id}_{desc.unique_id_suffix}"
    assert switch.translation_key == desc.translation_key
    assert switch.translation_placeholders == {"rule_name": rule_name.strip()}


@pytest.mark.parametrize(
    "desc",
    [PORT_FORWARD_SWITCH, TRAFFIC_RULE_SWITCH, NAT_RULE_SWITCH, STATIC_ROUTE_SWITCH],
)
@pytest.mark.parametrize("name_val", [None, "", "   "])
def test_unnamed_rule_uses_rule_id_placeholder(
    mock_coordinator: MagicMock,
    desc: UnifiInsightsNetworkRuleSwitchEntityDescription,
    name_val: str | None,
) -> None:
    """Unnamed rule falls back to unnamed translation and rule_id placeholder."""
    mock_coordinator.data = with_sections(
        mock_coordinator.data,
        **{
            desc.data_key: {
                "site1": {"r1": {desc.name_fields[0]: name_val, "enabled": True}}
            }
        },
    )
    switch = UnifiInsightsNetworkRuleSwitch(mock_coordinator, desc, "site1", "r1")
    assert switch.translation_key == desc.unnamed_translation_key
    assert switch.translation_placeholders == {"rule_id": "r1"}


def test_device_info_uses_site_gateway(mock_coordinator: MagicMock) -> None:
    """Switch attaches to site gateway device when present."""
    mock_coordinator.data = with_sections(
        mock_coordinator.data,
        devices={"site1": {"gw1": {"model": "UDM-Pro", "name": "Gateway"}}},
    )
    switch = UnifiInsightsNetworkRuleSwitch(
        mock_coordinator, PORT_FORWARD_SWITCH, "site1", "pf1"
    )
    assert switch.device_info is not None
    assert (DOMAIN, "site1_gw1") in switch.device_info["identifiers"]


def test_device_info_falls_back_to_site_device(mock_coordinator: MagicMock) -> None:
    """Switch falls back to site device grouping when no gateway is found."""
    mock_coordinator.data = with_sections(mock_coordinator.data, devices={"site1": {}})
    switch = UnifiInsightsNetworkRuleSwitch(
        mock_coordinator, PORT_FORWARD_SWITCH, "site1", "pf1"
    )
    assert switch.device_info is not None
    assert (DOMAIN, "site_site1") in switch.device_info["identifiers"]


@pytest.mark.parametrize(
    ("section_avail", "rule_data", "expected"),
    [
        (True, {"enabled": True}, True),
        (True, {"enabled": False}, True),
        (False, {"enabled": True}, False),
        (False, {"enabled": False}, False),
        (True, {"enabled": None}, False),
        (True, {"enabled": "true"}, False),
        (True, {"enabled": 1}, False),
        (True, {}, False),
        (True, None, False),
    ],
)
def test_available_requires_section_rule_and_bool_enabled(
    mock_coordinator: MagicMock,
    section_avail: bool,  # noqa: FBT001
    rule_data: dict[str, Any] | None,
    expected: bool,  # noqa: FBT001
) -> None:
    """Switch availability requires section, rule, and bool enabled."""
    set_availability(
        mock_coordinator,
        failed_sections=() if section_avail else {("port_forwards", "site1")},
    )
    site_rules = {} if rule_data is None else {"pf1": rule_data}
    mock_coordinator.data = with_sections(
        mock_coordinator.data, port_forwards={"site1": site_rules}
    )
    switch = UnifiInsightsNetworkRuleSwitch(
        mock_coordinator, PORT_FORWARD_SWITCH, "site1", "pf1"
    )
    assert switch.available is expected


@pytest.mark.parametrize(("enabled", "expected"), [(True, True), (False, False)])
def test_is_on_follows_enabled(
    mock_coordinator: MagicMock,
    enabled: bool,  # noqa: FBT001
    expected: bool,  # noqa: FBT001
) -> None:
    """is_on follows enabled bool state."""
    mock_coordinator.data = with_rule(
        mock_coordinator.data,
        "port_forwards",
        "site1",
        "pf1",
        {**mock_coordinator.data["port_forwards"]["site1"]["pf1"], "enabled": enabled},
    )
    switch = UnifiInsightsNetworkRuleSwitch(
        mock_coordinator, PORT_FORWARD_SWITCH, "site1", "pf1"
    )
    assert switch.is_on is expected


def test_port_forward_attributes_exclude_addresses(
    mock_coordinator: MagicMock,
) -> None:
    """Port forward extra state attributes contain metadata but no IP addresses."""
    mock_coordinator.data = with_rule(
        mock_coordinator.data,
        "port_forwards",
        "site1",
        "pf1",
        port_forward_record(
            _id="pf1",
            name="Plex",
            enabled=True,
            src_limiting_enabled=True,
            fwd="192.168.1.50",
            src="198.51.100.0/24",
            destination_ip="203.0.113.7",
            destination_ips=["203.0.113.8"],
        ),
    )
    switch = UnifiInsightsNetworkRuleSwitch(
        mock_coordinator, PORT_FORWARD_SWITCH, "site1", "pf1"
    )
    attrs = switch.extra_state_attributes
    expected_keys = {
        "rule_id",
        "protocol",
        "external_port",
        "forward_port",
        "interface",
        "logging",
        "source_limiting_enabled",
    }
    assert set(attrs.keys()) == expected_keys
    assert "192.168.1.50" not in attrs.values()
    assert "203.0.113.7" not in attrs.values()
    assert "203.0.113.8" not in attrs.values()
    assert "198.51.100.0/24" not in attrs.values()


def test_traffic_rule_attributes(mock_coordinator: MagicMock) -> None:
    """Traffic rule extra state attributes contain action and matching target."""
    switch = UnifiInsightsNetworkRuleSwitch(
        mock_coordinator, TRAFFIC_RULE_SWITCH, "site1", "tr1"
    )
    attrs = switch.extra_state_attributes
    assert attrs == {
        "rule_id": "tr1",
        "action": "BLOCK",
        "matching_target": "INTERNET",
    }


@pytest.mark.parametrize(
    ("desc", "rule_id", "method_name"),
    [
        (PORT_FORWARD_SWITCH, "pf1", "async_set_port_forward_enabled"),
        (TRAFFIC_RULE_SWITCH, "tr1", "async_set_traffic_rule_enabled"),
        (NAT_RULE_SWITCH, "nat1", "async_set_nat_rule_enabled"),
        (STATIC_ROUTE_SWITCH, "sr1", "async_set_static_route_enabled"),
    ],
)
@pytest.mark.parametrize("turn_on", [True, False])
@pytest.mark.asyncio
async def test_turn_on_off_calls_the_config_owner(
    mock_coordinator: MagicMock,
    desc: UnifiInsightsNetworkRuleSwitchEntityDescription,
    rule_id: str,
    method_name: str,
    turn_on: bool,  # noqa: FBT001
) -> None:
    """turn_on / turn_off await the typed config-owner command and nothing else."""
    autospec_command_owners(mock_coordinator)
    mock_coordinator.async_request_refresh = AsyncMock()
    record = mock_coordinator.data[desc.data_key].get("site1", {}).get(rule_id, {})
    mock_coordinator.data = with_rule(
        mock_coordinator.data,
        desc.data_key,
        "site1",
        rule_id,
        {**record, "enabled": not turn_on},
    )

    switch = UnifiInsightsNetworkRuleSwitch(mock_coordinator, desc, "site1", rule_id)

    if turn_on:
        await switch.async_turn_on()
    else:
        await switch.async_turn_off()

    handler = getattr(mock_coordinator.config_coordinator, method_name)
    handler.assert_awaited_once_with("site1", rule_id, enabled=turn_on)
    # The owner publishes the overlay and refreshes; the entity does neither.
    assert (
        mock_coordinator.data[desc.data_key]["site1"][rule_id]["enabled"] is not turn_on
    )
    mock_coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_turn_on_failure_propagates_the_owner_error(
    mock_coordinator: MagicMock,
) -> None:
    """The owner's translated error reaches the caller unchanged."""
    autospec_command_owners(mock_coordinator)
    error = HomeAssistantError(
        translation_domain=DOMAIN, translation_key="network_rule_update_failed"
    )
    mock_coordinator.config_coordinator.async_set_port_forward_enabled.side_effect = (
        error
    )
    switch = UnifiInsightsNetworkRuleSwitch(
        mock_coordinator, PORT_FORWARD_SWITCH, "site1", "pf1"
    )

    with pytest.raises(HomeAssistantError) as caught:
        await switch.async_turn_on()

    assert caught.value is error


def test_entity_category_is_config_and_has_entity_name(
    mock_coordinator: MagicMock,
) -> None:
    """Switches have EntityCategory.CONFIG and _attr_has_entity_name True."""
    for desc in NETWORK_RULE_SWITCH_TYPES:
        rule_id = "pf1" if desc.key == "port_forward" else "tr1"
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, desc, "site1", rule_id
        )
        assert switch.entity_category == EntityCategory.CONFIG
        assert switch.has_entity_name is True


def test_discover_is_noop_when_sections_missing(mock_coordinator: MagicMock) -> None:
    """discover_network_rule_switches returns empty list when sections are missing."""
    mock_coordinator.data = {}
    known_keys: set[tuple[Any, ...]] = set()
    assert discover_network_rule_switches(mock_coordinator, known_keys) == []
    assert known_keys == set()

    mock_coordinator.data = None
    assert discover_network_rule_switches(mock_coordinator, known_keys) == []


def test_get_rule_data_guards_against_non_dict_data(
    mock_coordinator: MagicMock,
) -> None:
    """_get_rule_data returns {} when any coordinator data level is not a dict."""
    switch = UnifiInsightsNetworkRuleSwitch(
        mock_coordinator, PORT_FORWARD_SWITCH, "site1", "pf1"
    )
    mock_coordinator.data = "not-a-dict"
    assert switch._get_rule_data() == {}

    mock_coordinator.data = {"port_forwards": "not-a-dict"}
    assert switch._get_rule_data() == {}

    mock_coordinator.data = {"port_forwards": {"site1": "not-a-dict"}}
    assert switch._get_rule_data() == {}


@pytest.mark.asyncio
async def test_setup_entry_adds_nat_rule_and_static_route_switches(
    hass: HomeAssistant, mock_coordinator: MagicMock
) -> None:
    """Platform setup adds NAT rule and static route switches."""
    mock_coordinator.data = with_sections(
        mock_coordinator.data,
        nat_rules={
            "site1": {
                "nat-dns": nat_rule_record(_id="nat-dns"),
            }
        },
        static_routes={
            "site1": {
                "route-lab": static_route_record(_id="route-lab"),
            }
        },
    )
    mock_entry = MagicMock()
    mock_entry.options = {}
    mock_entry.entry_id = "test_entry_id"
    mock_entry.runtime_data = MagicMock()
    mock_entry.runtime_data.coordinator = mock_coordinator

    async_add_entities = MagicMock()
    await async_setup_entry(hass, mock_entry, async_add_entities)

    entities = async_add_entities.call_args[0][0]
    rule_switches = [
        entity
        for entity in entities
        if isinstance(entity, UnifiInsightsNetworkRuleSwitch)
    ]
    assert len(rule_switches) == 4
    assert {e._rule_id for e in rule_switches} == {
        "pf1",
        "tr1",
        "nat-dns",
        "route-lab",
    }


def test_discovery_hides_predefined_nat_rules(
    mock_coordinator: MagicMock,
) -> None:
    """Discovery hides predefined/auto NAT rules; static routes never hidden."""
    r5 = nat_rule_record(_id="r5")
    del r5["is_predefined"]
    del r5["setting_preference"]
    mock_coordinator.data = with_sections(
        mock_coordinator.data,
        nat_rules={
            "site1": {
                "r1": nat_rule_record(_id="r1", is_predefined=True),
                "r2": nat_rule_record(_id="r2", setting_preference="auto"),
                "r3": nat_rule_record(_id="r3", setting_preference=None),
                "r4": nat_rule_record(_id="r4"),
                "r5": r5,
            }
        },
    )
    known_keys: set[tuple[Any, ...]] = set()
    entities = discover_network_rule_switches(mock_coordinator, known_keys)
    nat_entities = [e for e in entities if e.entity_description.key == "nat_rule"]
    assert len(nat_entities) == 2
    assert {
        require_instance(e, UnifiInsightsNetworkRuleSwitch)._rule_id
        for e in nat_entities
    } == {"r4", "r5"}
    assert STATIC_ROUTE_SWITCH.is_hidden_fn is None


def test_static_route_switch_is_disabled_by_default(
    mock_coordinator: MagicMock,
) -> None:
    """Static route switch is disabled by default in registry; others enabled."""
    assert STATIC_ROUTE_SWITCH.entity_registry_enabled_default is False
    assert PORT_FORWARD_SWITCH.entity_registry_enabled_default is True
    assert TRAFFIC_RULE_SWITCH.entity_registry_enabled_default is True
    assert NAT_RULE_SWITCH.entity_registry_enabled_default is True

    sr_entity = UnifiInsightsNetworkRuleSwitch(
        mock_coordinator, STATIC_ROUTE_SWITCH, "site1", "sr1"
    )
    assert sr_entity.entity_registry_enabled_default is False

    for desc in (PORT_FORWARD_SWITCH, TRAFFIC_RULE_SWITCH, NAT_RULE_SWITCH):
        entity = UnifiInsightsNetworkRuleSwitch(mock_coordinator, desc, "site1", "r1")
        assert entity.entity_registry_enabled_default is True


def test_nat_rule_attributes_exclude_addresses(
    mock_coordinator: MagicMock,
) -> None:
    """NAT rule extra state attributes contain metadata but no IP addresses."""
    mock_coordinator.data = with_sections(
        mock_coordinator.data,
        nat_rules={
            "site1": {
                "nat-dns": nat_rule_record(),
            }
        },
    )
    switch = UnifiInsightsNetworkRuleSwitch(
        mock_coordinator, NAT_RULE_SWITCH, "site1", "nat-dns"
    )
    attrs = switch.extra_state_attributes
    expected_keys = {
        "rule_id",
        "nat_type",
        "protocol",
        "ip_version",
        "logging",
    }
    assert set(attrs.keys()) == expected_keys
    dumped = str(attrs)
    assert "192.0.2.53" not in dumped
    assert "192.0.2.54" not in dumped


def test_static_route_attributes_exclude_addresses(
    mock_coordinator: MagicMock,
) -> None:
    """Static route extra state attributes contain type and distance but no IPs/MACs."""
    mock_coordinator.data = with_sections(
        mock_coordinator.data,
        static_routes={
            "site1": {
                "route-lab": static_route_record(),
            }
        },
    )
    switch = UnifiInsightsNetworkRuleSwitch(
        mock_coordinator, STATIC_ROUTE_SWITCH, "site1", "route-lab"
    )
    attrs = switch.extra_state_attributes
    expected_keys = {"rule_id", "route_type", "distance"}
    assert set(attrs.keys()) == expected_keys
    dumped = str(attrs)
    assert "192.168.2.0/24" not in dumped
    assert "aa:bb:cc:dd:ee:ff" not in dumped


def test_nat_rule_switch_unavailable_after_rule_turns_predefined(
    mock_coordinator: MagicMock,
) -> None:
    """NAT rule switch becomes unavailable when rule turns predefined."""
    nat_rule = {"description": "DNS", "enabled": True, "is_predefined": False}
    mock_coordinator.data = with_sections(
        mock_coordinator.data, nat_rules={"site1": {"nat1": nat_rule}}
    )
    switch = UnifiInsightsNetworkRuleSwitch(
        mock_coordinator, NAT_RULE_SWITCH, "site1", "nat1"
    )
    assert switch.available is True

    # Rule transitions to predefined on next update
    mock_coordinator.data = with_rule(
        mock_coordinator.data,
        "nat_rules",
        "site1",
        "nat1",
        {**nat_rule, "is_predefined": True},
    )
    assert switch.available is False


def test_discovery_skips_nat_rule_and_static_route_with_non_bool_enabled(
    mock_coordinator: MagicMock,
) -> None:
    """Records with non-bool enabled produce zero switches in discovery."""

    nat_raw = [
        nat_rule_record(_id=f"nat_{val}", enabled=val)
        for val in ("true", "false", 1, 0)
    ]
    route_raw = [
        static_route_record(_id=f"sr_{val}", enabled=val)
        for val in ("true", "false", 1, 0)
    ]

    mock_coordinator.data = with_sections(
        mock_coordinator.data,
        nat_rules={
            "site1": {
                rec["_id"]: NatRule.model_validate(rec).model_dump(by_alias=True)
                for rec in nat_raw
            }
        },
        static_routes={
            "site1": {
                rec["_id"]: StaticRoute.model_validate(rec).model_dump(by_alias=True)
                for rec in route_raw
            }
        },
    )
    known_keys: set[tuple[Any, ...]] = set()
    entities = discover_network_rule_switches(mock_coordinator, known_keys)
    target_entities = [
        e for e in entities if e.entity_description.key in ("nat_rule", "static_route")
    ]
    assert len(target_entities) == 0


_STRICT_RULE_SWITCH_TYPES = [d for d in NETWORK_RULE_SWITCH_TYPES if d.strict_enabled]


@pytest.mark.parametrize("desc", _STRICT_RULE_SWITCH_TYPES)
def test_rule_switch_availability_follows_its_own_section(
    mock_coordinator: MagicMock,
    desc: UnifiInsightsNetworkRuleSwitchEntityDescription,
) -> None:
    """Switch availability follows its own section availability function."""
    mock_coordinator.data = with_sections(
        mock_coordinator.data,
        port_forwards={"site1": {"pf1": {"enabled": True}}},
        traffic_rules={"site1": {"tr1": {"enabled": True}}},
        nat_rules={"site1": {"nat1": {"enabled": True}}},
        static_routes={"site1": {"sr1": {"enabled": True}}},
    )

    rule_ids = {
        PORT_FORWARD_SWITCH.key: "pf1",
        TRAFFIC_RULE_SWITCH.key: "tr1",
        NAT_RULE_SWITCH.key: "nat1",
        STATIC_ROUTE_SWITCH.key: "sr1",
    }
    sections = {
        PORT_FORWARD_SWITCH.key: "port_forwards",
        TRAFFIC_RULE_SWITCH.key: "traffic_rules",
        NAT_RULE_SWITCH.key: "nat_rules",
        STATIC_ROUTE_SWITCH.key: "static_routes",
    }

    # Only descriptor's section failed, other three fetched -> unavailable
    set_availability(
        mock_coordinator,
        failed_sections={
            (sections[d.key], "site1")
            for d in _STRICT_RULE_SWITCH_TYPES
            if d.key == desc.key
        },
    )
    switch = UnifiInsightsNetworkRuleSwitch(
        mock_coordinator, desc, "site1", rule_ids[desc.key]
    )
    assert switch.available is False

    # Reverse: only its own fetched, other three failed -> available
    set_availability(
        mock_coordinator,
        failed_sections={
            (sections[d.key], "site1")
            for d in _STRICT_RULE_SWITCH_TYPES
            if d.key != desc.key
        },
    )
    assert switch.available is True
