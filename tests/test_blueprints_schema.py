# Copyright (c) 2026 UniFi Insights contributors
"""Schema and contract checks for the three UniFi Insights blueprints."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.blueprint.models import Blueprint
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.util import yaml as yaml_util

from custom_components.unifi_insights.const import DOMAIN

BLUEPRINT_SOURCE = Path(__file__).parent.parent / "blueprints" / "automation" / DOMAIN
AP_OVERLOAD = "ap_overload.yaml"
INFRASTRUCTURE_OFFLINE = "infrastructure_offline.yaml"
SECURE_SUBNET = "secure_subnet_new_client.yaml"
ALL_BLUEPRINTS = (AP_OVERLOAD, INFRASTRUCTURE_OFFLINE, SECURE_SUBNET)
MIN_HOME_ASSISTANT_VERSION = "2026.9.0"


EXPECTED_MODES = {
    AP_OVERLOAD: "queued",
    INFRASTRUCTURE_OFFLINE: "queued",
    SECURE_SUBNET: "parallel",
}
EXPECTED_INPUTS = {
    AP_OVERLOAD: {
        "client_sensors",
        "client_threshold",
        "utilisation_sensors",
        "utilisation_threshold",
        "duration",
        "alert_actions",
    },
    INFRASTRUCTURE_OFFLINE: {
        "connectivity_sensors",
        "offline_delay",
        "treat_unavailable_as_offline",
        "offline_actions",
        "recovery_actions",
    },
    SECURE_SUBNET: {"subnet_prefixes", "allowed_trackers", "alert_actions"},
}


def _load(name: str) -> dict[str, Any]:
    """Load a blueprint with Home Assistant's !input-aware YAML loader."""
    return yaml_util.load_yaml_dict(BLUEPRINT_SOURCE / name)


EXPECTED_ENTITY_SELECTORS: dict[str, dict[str, dict[str, Any]]] = {
    AP_OVERLOAD: {
        "client_sensors": {
            "entity": {"integration": DOMAIN, "domain": "sensor", "multiple": True}
        },
        # Utilisation can come from any integration: no integration filter.
        "utilisation_sensors": {"entity": {"domain": "sensor", "multiple": True}},
    },
    INFRASTRUCTURE_OFFLINE: {
        "connectivity_sensors": {
            "entity": {
                "integration": DOMAIN,
                "domain": "binary_sensor",
                "device_class": "connectivity",
                "multiple": True,
            }
        },
    },
    SECURE_SUBNET: {
        "allowed_trackers": {
            "entity": {
                "integration": DOMAIN,
                "domain": "device_tracker",
                "multiple": True,
            }
        },
    },
}


def test_blueprint_directory_holds_exactly_the_three_blueprints() -> None:
    """The distributed directory contains the three blueprints and nothing else."""
    assert sorted(path.name for path in BLUEPRINT_SOURCE.iterdir()) == sorted(
        ALL_BLUEPRINTS
    )


@pytest.mark.parametrize("name", ALL_BLUEPRINTS)
def test_blueprint_parses_and_matches_the_blueprint_schema(name: str) -> None:
    """Each file loads with ``!input`` support and passes the blueprint schema."""
    data = _load(name)
    validated = BLUEPRINT_SCHEMA(data)
    blueprint = Blueprint(
        data, expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA
    )
    assert blueprint.validate() is None
    assert set(blueprint.inputs) == EXPECTED_INPUTS[name]
    assert data["mode"] == EXPECTED_MODES[name]
    assert data["max"] == 100

    metadata = validated["blueprint"]
    assert metadata["domain"] == "automation"
    assert metadata["name"].startswith("UniFi Insights")
    assert metadata["description"].strip()
    assert metadata["homeassistant"] == {"min_version": MIN_HOME_ASSISTANT_VERSION}
    # Every input is described for the blueprint UI.
    for input_name, definition in metadata["input"].items():
        assert definition["name"], input_name
        assert definition["description"], input_name
        assert "selector" in definition, input_name


@pytest.mark.parametrize("name", ALL_BLUEPRINTS)
def test_blueprint_uses_the_modern_automation_syntax(name: str) -> None:
    """Plural ``triggers``/``conditions``/``actions`` keys, no legacy singular keys."""
    data = _load(name)

    assert "triggers" in data
    assert "actions" in data
    assert "conditions" in data
    assert not {"trigger", "condition", "action", "platform"} & set(data)
    for trigger in data["triggers"]:
        assert "trigger" in trigger
        assert "platform" not in trigger


@pytest.mark.parametrize("name", ALL_BLUEPRINTS)
def test_blueprint_selectors_that_filter_by_integration_use_unifi_insights(
    name: str,
) -> None:
    """No selector filters on any integration other than UniFi Insights."""
    inputs = _load(name)["blueprint"]["input"]

    for input_name, definition in inputs.items():
        entity = definition["selector"].get("entity", {})
        if "integration" in entity:
            assert entity["integration"] == DOMAIN, input_name


@pytest.mark.parametrize(
    ("name", "input_name", "selector"),
    [
        pytest.param(name, input_name, selector, id=f"{name}-{input_name}")
        for name, selectors in EXPECTED_ENTITY_SELECTORS.items()
        for input_name, selector in selectors.items()
    ],
)
def test_entity_selectors_are_exactly_the_planned_filters(
    name: str, input_name: str, selector: dict[str, Any]
) -> None:
    """The entity pickers show only the entities the blueprint can work with."""
    definition = _load(name)["blueprint"]["input"][input_name]

    assert definition["selector"] == selector


def test_optional_inputs_default_to_empty_or_on() -> None:
    """Optional inputs have defaults so a minimal setup validates."""
    ap = _load(AP_OVERLOAD)["blueprint"]["input"]
    offline = _load(INFRASTRUCTURE_OFFLINE)["blueprint"]["input"]
    vlan = _load(SECURE_SUBNET)["blueprint"]["input"]

    assert ap["utilisation_sensors"]["default"] == []
    assert offline["recovery_actions"]["default"] == []
    assert offline["treat_unavailable_as_offline"]["default"] is True
    assert offline["treat_unavailable_as_offline"]["selector"] == {"boolean": {}}
    assert vlan["allowed_trackers"]["default"] == []
    # The secure prefixes are required: without one the blueprint is meaningless.
    assert "default" not in vlan["subnet_prefixes"]
    assert vlan["subnet_prefixes"]["selector"] == {"text": {"multiple": True}}


def test_ap_overload_uses_numeric_state_triggers_with_ids_and_a_duration() -> None:
    """Client overload and utilisation are separate ``for:`` numeric triggers."""
    triggers = _load(AP_OVERLOAD)["triggers"]

    assert [(t["trigger"], t["id"]) for t in triggers] == [
        ("numeric_state", "client_overload"),
        ("numeric_state", "utilisation"),
    ]
    assert all("for" in trigger for trigger in triggers)


def test_ap_overload_description_says_there_is_no_utilisation_sensor() -> None:
    """Users must be told UniFi Insights has no channel utilisation sensor."""
    metadata = _load(AP_OVERLOAD)["blueprint"]
    text = " ".join(
        [metadata["description"]]
        + [
            definition.get("description", "")
            for definition in metadata["input"].values()
        ]
    ).lower()

    assert "does not currently" in text
    assert "channel utilisation sensor" in text
    assert "another" in text


def test_offline_description_states_the_unavailable_limits() -> None:
    """A confirmed offline device and a polling failure both read unavailable."""
    description = _load(INFRASTRUCTURE_OFFLINE)["blueprint"]["description"].lower()

    assert "confirmed offline device" in description
    assert "controller" in description
    assert description.count("unavailable") >= 2


def test_offline_blueprint_separates_offline_and_recovery_with_trigger_ids() -> None:
    """Offline goes to off/unavailable for a duration, recovery returns to on."""
    triggers = _load(INFRASTRUCTURE_OFFLINE)["triggers"]

    offline, unavailable, recovery = triggers
    assert offline["id"] == "offline"
    assert offline["to"] == "off"
    assert unavailable["id"] == "unavailable"
    assert unavailable["to"] == "unavailable"
    assert "for" in offline
    assert "for" in unavailable
    assert recovery["id"] == "recovery"
    assert recovery["to"] == "on"
    assert all("from" not in trigger for trigger in triggers)


def test_secure_subnet_triggers_on_new_entity_registry_entries() -> None:
    """A new client is a new tracker, so the trigger is a registry ``create``."""
    assert _load(SECURE_SUBNET)["triggers"] == [
        {
            "trigger": "event",
            "event_type": "entity_registry_updated",
            "event_data": {"action": "create"},
        }
    ]


def test_secure_subnet_description_states_the_prerequisites_and_limits() -> None:
    """Tracking is opt-in, the VLAN comes from the IP, and the burst has a limit."""
    data = _load(SECURE_SUBNET)
    description = " ".join(data["blueprint"]["description"].lower().split())

    assert "client tracking" in description
    assert "opt-in" in description or "opt in" in description
    assert "ip" in description
    assert "inferred" in description
    assert "first creation" in description
    assert "returning clients" in description
    assert "10-second wait is fixed" in description
    assert "missing ip address" in description
    assert "warning" in description
    assert "max_exceeded" not in data
    assert "many" in description

    # ``mode: parallel`` drops every run beyond ``max``: the text must say so
    # and name the limit that is really configured.
    sentences = re.split(r"(?<=[.!?])\s+", description)
    dropped = [sentence for sentence in sentences if "dropped" in sentence]
    assert dropped, "the description must say that runs beyond the limit are dropped"
    assert any(str(data["max"]) in sentence for sentence in dropped), (
        f"the sentence about dropped runs must name the limit ({data['max']})"
    )


def test_secure_subnet_action_variables_and_fixed_delay() -> None:
    """The fixed wait precedes router/IP checks and the named action variables."""
    data = _load(SECURE_SUBNET)
    assert set(data["variables"]) == {
        "tracker",
        "subnet_prefixes",
        "prefixes",
        "allowed_trackers",
    }
    assert len(data["conditions"]) == 4
    assert data["actions"][0] == {"delay": "00:00:10"}
    variable_steps = [step for step in data["actions"] if "variables" in step]
    assert set(variable_steps[0]["variables"]) == {"ip_address"}
    assert set(variable_steps[1]["variables"]) == {"matched_prefix"}
    everything = str({key: data[key] for key in data if key != "blueprint"})
    assert "state_attr(tracker, 'ip')" in everything
    assert "source_type" in everything
    assert not re.search(r"\bmac", everything, re.IGNORECASE)


@pytest.mark.parametrize("name", ALL_BLUEPRINTS)
def test_number_duration_and_action_selectors_match_the_plan(name: str) -> None:
    """Inputs use the UI selector types the plan calls for."""
    inputs = _load(name)["blueprint"]["input"]
    for input_name in EXPECTED_INPUTS[name]:
        selector = inputs[input_name]["selector"]
        if input_name.endswith("actions"):
            assert selector == {"action": {}}
        elif input_name in {"duration", "offline_delay"}:
            assert selector == {"duration": {}}
        elif input_name.endswith("threshold"):
            assert "number" in selector
    if name == AP_OVERLOAD:
        assert (
            inputs["utilisation_threshold"]["selector"]["number"]["unit_of_measurement"]
            == "%"
        )
        assert "wireless_clients" in inputs["client_sensors"]["description"]
