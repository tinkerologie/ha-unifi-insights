# Copyright 2026 UniFi Insights contributors
"""Tests for the facade's translated command errors (ADR-010)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.exceptions import HomeAssistantError
from unifi_official_api import UniFiConnectionError, UniFiNotFoundError

from custom_components.unifi_insights.const import DOMAIN
from tests.fixtures.command_owners import SITE_ID

if TYPE_CHECKING:
    from tests.fixtures.command_owners import SeededOwners


async def test_refresh_or_raise_names_the_failed_coordinators(
    seeded_owners: SeededOwners,
) -> None:
    """A failed child refresh raises refresh_failed naming only coordinators."""
    seeded_owners.network_client.devices.get_all.side_effect = UniFiConnectionError(
        "secret detail"
    )

    with pytest.raises(HomeAssistantError) as caught:
        await seeded_owners.facade.async_refresh_or_raise(include_protect=False)

    assert caught.value.translation_domain == DOMAIN
    assert caught.value.translation_key == "refresh_failed"
    assert caught.value.translation_placeholders == {"targets": "devices coordinator"}
    assert str(caught.value).startswith("Error refreshing UniFi Insights data: ")


async def test_pass_through_failure_carries_command_failed(
    seeded_owners: SeededOwners,
) -> None:
    """A facade pass-through keeps its message and gains the generic key."""
    restart = seeded_owners.network_client.devices.restart
    cause = UniFiConnectionError("down")
    restart.side_effect = cause

    with pytest.raises(
        HomeAssistantError, match=r"^Unable to restart device"
    ) as caught:
        await seeded_owners.facade.async_restart_device(SITE_ID, "pdu_device_1")

    assert caught.value.translation_key == "command_failed"
    assert caught.value.__cause__ is cause
    restart.assert_awaited_once_with(SITE_ID, "pdu_device_1")


async def test_client_without_mac_is_a_translated_error(
    seeded_owners: SeededOwners,
) -> None:
    """A client action without a resolvable MAC raises client_not_resolvable."""
    facade = seeded_owners.facade
    facade.data["clients"] = {SITE_ID: {"client1": {"id": "client1"}}}

    with pytest.raises(HomeAssistantError) as caught:
        await facade.async_block_client(SITE_ID, "client1")

    assert caught.value.translation_key == "client_not_resolvable"
    assert caught.value.translation_placeholders == {"client_id": "client1"}
    seeded_owners.network_client.clients.block.assert_not_awaited()


_RULE_COMMANDS = {
    "port forward": (
        "async_set_port_forward_enabled",
        "port_forwards",
        "update_port_forward",
    ),
    "traffic rule": (
        "async_set_traffic_rule_enabled",
        "traffic_rules",
        "update_traffic_rule",
    ),
}


@pytest.mark.parametrize("rule_type", sorted(_RULE_COMMANDS))
@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        (UniFiNotFoundError("gone"), "network_rule_not_found"),
        (RuntimeError("boom"), "network_rule_update_failed"),
    ],
)
async def test_rule_failures_are_translated(
    seeded_owners: SeededOwners,
    rule_type: str,
    error: Exception,
    translation_key: str,
) -> None:
    """Port-forward and traffic-rule failures carry the rule translations."""
    command, endpoint, method = _RULE_COMMANDS[rule_type]
    update = getattr(getattr(seeded_owners.network_client, endpoint), method)
    update.side_effect = error

    with pytest.raises(HomeAssistantError) as caught:
        await getattr(seeded_owners.facade, command)(SITE_ID, "rule1", enabled=True)

    assert caught.value.translation_domain == DOMAIN
    assert caught.value.translation_key == translation_key
    assert caught.value.translation_placeholders == {
        "rule_type": rule_type,
        "rule_name": "rule1",
    }
    assert caught.value.__cause__ is error
    update.assert_awaited_once_with("default", "rule1", enabled=True)
