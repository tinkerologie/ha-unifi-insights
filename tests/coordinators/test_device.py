# Copyright 2026 UniFi Insights contributors
"""
Tests for the device owner's command foundations (ADR-010).

Covers PoE port eligibility, which lives next to the power-cycle command and
reads #337's port facts, and the device coordinator's reuse of the config
coordinator's legacy site-name rule.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.coordinators.device import (
    get_device_port,
    port_can_be_power_cycled,
    power_cyclable_ports,
    require_power_cyclable_port,
)
from custom_components.unifi_insights.data_transforms import (
    NETWORK_FACTS_KEY,
    build_site_network_facts,
)
from tests.fixtures.command_owners import SITE_ID

if TYPE_CHECKING:
    from tests.fixtures.command_owners import SeededOwners

SWITCH_ID = "sw1"
PORTS: list[dict[str, Any]] = [
    {"idx": 1, "poe": {"enabled": True}},
    {"idx": "2", "poe": {"enabled": True}},
    {"idx": 3, "poe": {"enabled": False}},
    {"idx": 4},
    {"idx": 5, "poe": {"enabled": "true"}},
]


def _data(ports: list[dict[str, Any]] | None = PORTS) -> dict[str, Any]:
    """Return coordinator data for one switch with its published facts."""
    switch: dict[str, Any] = {
        "id": SWITCH_ID,
        "name": "Switch",
        "macAddress": "aa:bb:cc:00:00:01",
        "features": ["switching"],
    }
    if ports is not None:
        switch["ports"] = ports
    data: dict[str, Any] = {"devices": {SITE_ID: {SWITCH_ID: switch}}, "stats": {}}
    data[NETWORK_FACTS_KEY] = {
        SITE_ID: build_site_network_facts(data["devices"][SITE_ID], None)
    }
    return data


class TestPoeEligibility:
    """PoE lookup and eligibility over the published port facts."""

    def test_only_integer_indexed_poe_ports_qualify(self) -> None:
        """Discovery keeps integer raw indexes with PoE enabled, in order."""
        ports = power_cyclable_ports(_data(), SITE_ID, SWITCH_ID)

        assert [port.raw_index for port in ports] == [1]
        assert ports[0].record == PORTS[0]

    @pytest.mark.parametrize(
        ("data", "device_id"),
        [({}, SWITCH_ID), (_data(), "missing"), (_data(None), SWITCH_ID)],
    )
    def test_no_facts_or_ports_means_no_targets(
        self, data: dict[str, Any], device_id: str
    ) -> None:
        """A device without facts or ports has nothing to power cycle."""
        assert power_cyclable_ports(data, SITE_ID, device_id) == ()

    def test_lookup_matches_the_raw_index_exactly(self) -> None:
        """``2`` never finds the port spelled ``"2"``."""
        data = _data()

        assert get_device_port(data, SITE_ID, SWITCH_ID, 1) == PORTS[0]
        assert get_device_port(data, SITE_ID, SWITCH_ID, 2) is None
        assert get_device_port(data, SITE_ID, SWITCH_ID, 9) is None
        assert get_device_port(data, None, SWITCH_ID, 1) is None
        assert get_device_port(None, SITE_ID, SWITCH_ID, 1) is None

    @pytest.mark.parametrize(
        ("port", "eligible"),
        [
            ({"poe": {"enabled": True}}, True),
            ({"poe": {"enabled": False}}, False),
            ({"poe": {"enabled": "true"}}, False),
            ({"poe": None}, False),
            ({}, False),
        ],
    )
    def test_power_cycle_needs_poe_enabled_true(
        self, port: dict[str, Any], *, eligible: bool
    ) -> None:
        """Only a literal ``True`` enables a power cycle."""
        assert port_can_be_power_cycled(port) is eligible

    def test_require_returns_the_eligible_port(self) -> None:
        """An eligible port is returned to the caller."""
        assert require_power_cyclable_port(_data(), SITE_ID, SWITCH_ID, 1) == PORTS[0]

    @pytest.mark.parametrize(
        ("target", "key", "placeholders"),
        [
            (
                (_data(), SITE_ID, "missing", 1),
                "device_not_found",
                {"device_id": "missing", "site_id": SITE_ID},
            ),
            (
                (None, None, None, 1),
                "device_not_found",
                {"device_id": "None", "site_id": "None"},
            ),
            (
                (_data(), SITE_ID, SWITCH_ID, 9),
                "port_not_found",
                {"port_idx": "9", "device_id": SWITCH_ID},
            ),
            (
                (_data(), SITE_ID, SWITCH_ID, 3),
                "port_not_poe",
                {"port_idx": "3", "device_id": SWITCH_ID},
            ),
        ],
    )
    def test_require_raises_translated_validation(
        self,
        target: tuple[dict[str, Any] | None, str | None, str | None, int],
        key: str,
        placeholders: dict[str, str],
    ) -> None:
        """Each ineligible target raises its ServiceValidationError."""
        with pytest.raises(ServiceValidationError) as caught:
            require_power_cyclable_port(*target)

        assert caught.value.translation_domain == DOMAIN
        assert caught.value.translation_key == key
        assert caught.value.translation_placeholders == placeholders


async def test_device_mapping_follows_the_config_rule(
    seeded_owners: SeededOwners,
) -> None:
    """The device coordinator maps legacy sites with the config rule."""
    device = seeded_owners.device
    config = seeded_owners.config

    assert config.data["legacy_site_names"] == {SITE_ID: "default"}
    assert device.get_legacy_site_name(SITE_ID) == "default"
    assert device._map_legacy_site_names(
        [SITE_ID], [{"name": "renamed", "desc": "Default Site"}]
    ) == {SITE_ID: "renamed"}
