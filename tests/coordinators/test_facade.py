# Copyright 2026 UniFi Insights contributors
"""Tests for the facade's translated command errors and owner accessors (ADR-010)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.exceptions import HomeAssistantError
from unifi_official_api import UniFiConnectionError

from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.coordinators import UnifiInsightsFacadeCoordinator
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


async def test_voucher_failure_carries_command_failed(
    seeded_owners: SeededOwners,
) -> None:
    """A facade voucher command keeps its message and gains the generic key."""
    delete = seeded_owners.network_client.vouchers.delete
    cause = UniFiConnectionError("down")
    delete.side_effect = cause

    with pytest.raises(
        HomeAssistantError, match=r"^Unable to delete voucher"
    ) as caught:
        await seeded_owners.facade.async_delete_voucher(SITE_ID, "v1")

    assert caught.value.translation_key == "command_failed"
    assert caught.value.__cause__ is cause
    delete.assert_awaited_once_with(SITE_ID, "v1")


async def test_accessors_return_the_command_owners(
    seeded_owners: SeededOwners,
) -> None:
    """The facade hands callers the owners of the Network and Protect commands."""
    facade = seeded_owners.facade

    assert facade.config_coordinator is seeded_owners.config
    assert facade.device_coordinator is seeded_owners.device
    assert facade.require_protect_coordinator() is seeded_owners.protect


async def test_require_protect_coordinator_without_protect_is_translated(
    seeded_owners: SeededOwners,
) -> None:
    """Without Protect the accessor raises the translated prerequisite error."""
    facade = UnifiInsightsFacadeCoordinator(
        seeded_owners.facade.hass,
        seeded_owners.network_client,
        None,
        seeded_owners.entry,
        seeded_owners.config,
        device_coordinator=seeded_owners.device,
        protect_coordinator=None,
    )
    try:
        with pytest.raises(HomeAssistantError) as caught:
            facade.require_protect_coordinator()
    finally:
        await facade.async_shutdown()

    assert caught.value.translation_domain == DOMAIN
    assert caught.value.translation_key == "no_protect_coordinator"
