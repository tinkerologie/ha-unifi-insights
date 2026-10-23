# Copyright 2026 UniFi Insights contributors
"""Real HA outlet service dispatch across a provisioning refresh (#372)."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.unifi_insights import UnifiInsightsData
from custom_components.unifi_insights.const import DOMAIN
from tests.fixtures.command_owners import PDU_ID, SITE_ID
from tests.fixtures.library_responses import (
    SAMPLE_PDU_DEVICE,
    SAMPLE_PDU_LEGACY_DEVICE_COMPLETE,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from tests.fixtures.command_owners import SeededOwners


@pytest.mark.parametrize("state", ["GETTING_READY", "OFFLINE"])
async def test_outlet_service_dispatch_after_refresh(
    hass: HomeAssistant,
    seeded_owners: SeededOwners,
    state: str,
    enable_custom_integrations,
) -> None:
    """HA dispatches both commands during provisioning, but skips offline outlets."""
    owners = seeded_owners
    owners.entry.runtime_data = UnifiInsightsData(
        config_coordinator=owners.config,
        device_coordinator=owners.device,
        protect_coordinator=owners.protect,
        network_client=owners.network_client,
        protect_client=owners.protect_client,
        _facade_coordinator=owners.facade,
    )
    # Coordinator fixtures represent an already loaded integration; set up only
    # its real switch platform, without unrelated HTTP/stream dependencies.
    hass.config.components.add(DOMAIN)
    owners.entry.mock_state(hass, ConfigEntryState.LOADED)
    assert await async_setup_component(hass, "switch", {})
    await hass.config_entries.async_forward_entry_setups(
        owners.entry, [Platform.SWITCH]
    )
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_ids: list[str] = []
    for index in (1, 2):
        entity_id = registry.async_get_entity_id(
            "switch", DOMAIN, f"{SITE_ID}_{PDU_ID}_outlet_{index}"
        )
        assert entity_id is not None
        entity_ids.append(entity_id)
    endpoint = owners.network_client.devices
    endpoint.set_outlet_state.return_value = True

    # Explicit read responses: a successful mocked write does not change them.
    refreshed = copy.deepcopy(SAMPLE_PDU_DEVICE)
    refreshed["state"] = state
    legacy = copy.deepcopy(SAMPLE_PDU_LEGACY_DEVICE_COMPLETE)
    legacy["outlet_table"][0]["relay_state"] = True
    legacy["outlet_overrides"][0]["relay_state"] = True
    endpoint.get_all.return_value = [copy.deepcopy(SAMPLE_PDU_DEVICE)]
    endpoint.get_legacy_site_devices.return_value = [legacy]
    await owners.device.async_refresh()
    endpoint.get_all.return_value = [refreshed]

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_ids[0]}, blocking=True
    )
    await owners.device.async_refresh()
    await hass.async_block_till_done()
    first_call = endpoint.set_outlet_state.await_args_list[0]
    assert first_call.args == ("default", legacy["_id"], 1)
    assert first_call.kwargs["state"] is True
    states: list[str] = []
    for entity_id in entity_ids:
        entity_state = hass.states.get(entity_id)
        assert entity_state is not None
        states.append(entity_state.state)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_ids[1]}, blocking=True
    )
    await hass.async_block_till_done()
    assert endpoint.set_outlet_state.await_count == (
        2 if state == "GETTING_READY" else 1
    )
    if state == "GETTING_READY":
        assert all(item != "unavailable" for item in states)
    else:
        assert all(item == "unavailable" for item in states)

    if state == "GETTING_READY":
        second_call = endpoint.set_outlet_state.await_args_list[1]
        assert second_call.args == ("default", legacy["_id"], 2)
        assert second_call.kwargs["state"] is True
        assert second_call.kwargs["current_device"]["state"] == "GETTING_READY"
        for entity_id in entity_ids:
            entity_state = hass.states.get(entity_id)
            assert entity_state is not None
            assert entity_state.state != "unavailable"
    await hass.config_entries.async_unload_platforms(owners.entry, [Platform.SWITCH])
