# Copyright 2026 UniFi Insights contributors
"""
Tests for the Protect owner's command foundations (ADR-010).

Pins the second test adapter against the real ``unifi_official_api`` 2.0.0
classes: the seeded Protect owner refreshes through real coordinator code,
and the autospecced client rejects methods and arguments the library does
not have.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.exceptions import HomeAssistantError
from unifi_official_api.protect.endpoints.chimes import ChimesEndpoint

from custom_components.unifi_insights.const import DOMAIN
from tests.fixtures.command_owners import (
    CHIME_ID,
    DOORBELL_IDS,
    autospec_endpoint,
    build_seeded_owners,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from tests.fixtures.command_owners import SeededOwners


async def test_seeded_owners_refresh_through_real_code(
    seeded_owners: SeededOwners,
) -> None:
    """Every owner refreshes successfully from the seeded reads."""
    for owner in (seeded_owners.config, seeded_owners.device, seeded_owners.protect):
        assert owner.last_update_success is True
    protect = seeded_owners.facade.data["protect"]
    assert set(protect["cameras"]) == set(DOORBELL_IDS)
    assert protect["nvrs"]


async def test_seeded_chime_is_paired_with_two_doorbells(
    seeded_owners: SeededOwners,
) -> None:
    """The chime seed keeps one ring setting per paired doorbell."""
    chime = seeded_owners.protect.data["chimes"][CHIME_ID]

    assert chime["cameraIds"] == list(DOORBELL_IDS)
    assert [entry["cameraId"] for entry in chime["ringSettings"]] == list(DOORBELL_IDS)


async def test_client_rejects_methods_the_library_does_not_have(
    hass: HomeAssistant,
) -> None:
    """The autospec fails a call that a bare MagicMock would let pass."""
    owners = build_seeded_owners(hass)
    chimes = owners.protect_client.chimes

    # The shared conftest mock offers set_repeat; the real 2.0.0 method is
    # set_repeat_times.
    with pytest.raises(AttributeError):
        _ = chimes.set_repeat
    with pytest.raises(AttributeError):
        chimes.set_repeat = None
    with pytest.raises(AttributeError):
        _ = owners.protect_client.not_an_endpoint
    with pytest.raises(AttributeError):
        _ = owners.network_client.devices.reboot


async def test_client_enforces_the_library_signatures(hass: HomeAssistant) -> None:
    """A call that the real signature does not accept raises TypeError."""
    owners = build_seeded_owners(hass)

    with pytest.raises(TypeError):
        await owners.protect_client.chimes.set_volume(CHIME_ID)
    with pytest.raises(TypeError):
        await owners.network_client.devices.restart("default")

    await owners.protect_client.chimes.set_repeat_times(CHIME_ID, 2)
    owners.protect_client.chimes.set_repeat_times.assert_awaited_once_with(CHIME_ID, 2)


def test_endpoint_autospec_keeps_instance_attributes() -> None:
    """Instance attributes of the real endpoint are part of the spec."""
    chimes = autospec_endpoint(ChimesEndpoint)

    assert chimes.last_result_complete is True
    assert chimes.get_all.return_value == []


async def test_missing_protect_client_is_a_translated_error(
    seeded_owners: SeededOwners,
) -> None:
    """The facade's Protect prerequisite raises no_protect_coordinator."""
    facade = seeded_owners.facade
    facade.protect_client = None

    with pytest.raises(HomeAssistantError) as caught:
        facade._require_protect_client()

    assert caught.value.translation_domain == DOMAIN
    assert caught.value.translation_key == "no_protect_coordinator"
