# Copyright 2026 UniFi Insights contributors
"""
Tests for the legacy cross-check in Network stale-device cleanup (#395).

A device that a successful v1 poll omits only counts toward removal when the
same poll's legacy device list (matched by normalized MAC) agrees that it is
gone. The tests run full refreshes on the real coordinators against the real
device registry.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from unifi_official_api import UniFiResponseError

from custom_components.unifi_insights.const import (
    DOMAIN,
    MAX_CONSECUTIVE_MISSING_POLLS,
)
from tests.conftest import device_config_entry_ids, get_device_by_identifier
from tests.fixtures.command_owners import (
    SITE_ID,
    async_shutdown_owners,
    build_seeded_owners,
)
from tests.fixtures.library_responses import (
    SAMPLE_PDU_DEVICE,
    SAMPLE_PDU_LEGACY_DEVICE,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from tests.fixtures.command_owners import SeededOwners

SWITCH_ID = "switch_v1_id"
SWITCH_MAC = "aa:aa:aa:aa:aa:01"
SWITCH_V1 = {
    "id": SWITCH_ID,
    "mac": SWITCH_MAC,
    "model": "USW-24",
    "name": "Switch",
    "state": "ONLINE",
}
SWITCH_LEGACY = {
    "_id": "switch_legacy_id",
    # Upper case: the cross-check matches on the normalized MAC.
    "mac": SWITCH_MAC.upper(),
    "model": "USW-24",
    "name": "Switch",
    "type": "usw",
    "up": True,
}

SITE_B = "site_b"
SITE_B_LEGACY_NAME = "site_b_legacy"
SITE_B_SWITCH_ID = "switch_b_id"
SITE_B_SWITCH_MAC = "aa:aa:aa:aa:aa:02"
SITE_B_SWITCH_V1 = {**SWITCH_V1, "id": SITE_B_SWITCH_ID, "mac": SITE_B_SWITCH_MAC}
SITE_B_SWITCH_LEGACY = {
    **SWITCH_LEGACY,
    "_id": "switch_b_legacy_id",
    "mac": SITE_B_SWITCH_MAC,
}


def _v1(*extra: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a v1 device list: the PDU plus ``extra``."""
    return [copy.deepcopy(SAMPLE_PDU_DEVICE), *map(copy.deepcopy, extra)]


def _legacy(*extra: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a legacy device list: the PDU plus ``extra``."""
    return [copy.deepcopy(SAMPLE_PDU_LEGACY_DEVICE), *map(copy.deepcopy, extra)]


def _count(owners: SeededOwners, site_id: str, device_id: str) -> int | None:
    """Return a device's committed absence counter."""
    return owners.device._site_absence_counts.get(site_id, {}).get(device_id)


def _registered(owners: SeededOwners, site_id: str, device_id: str) -> bool:
    """Return True while the Network entry still owns the registry device."""
    entry_id = owners.entry.entry_id
    device = get_device_by_identifier(
        dr.async_get(owners.device.hass),
        (DOMAIN, f"{site_id}_{device_id}"),
        entry_id,
    )
    return device is not None and entry_id in device_config_entry_ids(device)


def _register(owners: SeededOwners, site_id: str, device_id: str, mac: str) -> None:
    """Create the registry device that the switch entities would belong to."""
    dr.async_get(owners.device.hass).async_get_or_create(
        config_entry_id=owners.entry.entry_id,
        identifiers={(DOMAIN, f"{site_id}_{device_id}")},
        connections={(CONNECTION_NETWORK_MAC, mac)},
    )


async def _seed_switch(owners: SeededOwners) -> None:
    """Commit two v1 polls in which v1 and legacy both report the switch."""
    devices = owners.network_client.devices
    devices.get_all.return_value = _v1(SWITCH_V1)
    devices.get_legacy_site_devices.return_value = _legacy(SWITCH_LEGACY)
    await owners.config.async_refresh()
    await owners.device.async_refresh()
    await owners.device.async_refresh()
    _register(owners, SITE_ID, SWITCH_ID, SWITCH_MAC)
    assert owners.device._site_sources[SITE_ID] == "v1"
    assert SWITCH_ID in owners.device._site_tracked_devices[SITE_ID]


async def test_v1_omits_device_legacy_keeps_it_holds_the_counter(
    hass: HomeAssistant,
) -> None:
    """A device legacy still lists is held: never removed, counter kept."""
    owners = build_seeded_owners(hass)
    try:
        await _seed_switch(owners)
        devices = owners.network_client.devices
        devices.get_all.return_value = _v1()

        # v1 omits the switch, legacy still has it: no removal, no counter.
        for _ in range(MAX_CONSECUTIVE_MISSING_POLLS + 2):
            await owners.device.async_refresh()
            assert owners.device.last_update_success
            assert _count(owners, SITE_ID, SWITCH_ID) is None
            assert SWITCH_ID in owners.device._site_tracked_devices[SITE_ID]
            assert _registered(owners, SITE_ID, SWITCH_ID)

        # Both omit it once: the counter advances.
        devices.get_legacy_site_devices.return_value = _legacy()
        await owners.device.async_refresh()
        assert _count(owners, SITE_ID, SWITCH_ID) == 1

        # Legacy has it again: the counter is held, not reset.
        devices.get_legacy_site_devices.return_value = _legacy(SWITCH_LEGACY)
        await owners.device.async_refresh()
        assert _count(owners, SITE_ID, SWITCH_ID) == 1
        assert SWITCH_ID in owners.device._site_tracked_devices[SITE_ID]
        assert _registered(owners, SITE_ID, SWITCH_ID)

        # Both omit it on the next eligible polls: removed on the Nth.
        devices.get_legacy_site_devices.return_value = _legacy()
        for expected in range(2, MAX_CONSECUTIVE_MISSING_POLLS):
            await owners.device.async_refresh()
            assert _count(owners, SITE_ID, SWITCH_ID) == expected
            assert _registered(owners, SITE_ID, SWITCH_ID)
        await owners.device.async_refresh()
        assert not _registered(owners, SITE_ID, SWITCH_ID)
        assert _count(owners, SITE_ID, SWITCH_ID) is None
        assert SWITCH_ID not in owners.device._site_tracked_devices[SITE_ID]
        assert SWITCH_MAC not in owners.device._site_aliases[SITE_ID]
    finally:
        await async_shutdown_owners(owners)


async def test_v1_and_legacy_omit_device_removes_it_on_the_nth_poll(
    hass: HomeAssistant,
) -> None:
    """A device both lists omit is removed on the Nth eligible poll."""
    owners = build_seeded_owners(hass)
    try:
        await _seed_switch(owners)
        devices = owners.network_client.devices
        devices.get_all.return_value = _v1()
        devices.get_legacy_site_devices.return_value = _legacy()

        for expected in range(1, MAX_CONSECUTIVE_MISSING_POLLS):
            await owners.device.async_refresh()
            assert _count(owners, SITE_ID, SWITCH_ID) == expected
            assert _registered(owners, SITE_ID, SWITCH_ID)

        await owners.device.async_refresh()
        assert not _registered(owners, SITE_ID, SWITCH_ID)
    finally:
        await async_shutdown_owners(owners)


@pytest.mark.parametrize(
    ("side_effect", "return_value"),
    [
        pytest.param(
            UniFiResponseError("server error", status_code=500), None, id="fails"
        ),
        pytest.param(None, [], id="empty"),
    ],
)
async def test_unverified_legacy_list_removes_nothing_and_holds_counters(
    hass: HomeAssistant,
    side_effect: Exception | None,
    return_value: list[dict[str, Any]] | None,
) -> None:
    """A failed or empty legacy answer makes a pending removal ineligible."""
    owners = build_seeded_owners(hass)
    try:
        await _seed_switch(owners)
        devices = owners.network_client.devices
        devices.get_all.return_value = _v1()
        devices.get_legacy_site_devices.return_value = _legacy()
        await owners.device.async_refresh()
        assert _count(owners, SITE_ID, SWITCH_ID) == 1
        tracked = dict(owners.device._site_tracked_devices[SITE_ID])

        devices.get_legacy_site_devices.side_effect = side_effect
        devices.get_legacy_site_devices.return_value = return_value
        for _ in range(MAX_CONSECUTIVE_MISSING_POLLS + 1):
            await owners.device.async_refresh()
            assert owners.device.last_update_success
            assert owners.device._site_sources[SITE_ID] == "v1"
            assert _count(owners, SITE_ID, SWITCH_ID) == 1
            assert owners.device._site_tracked_devices[SITE_ID] == tracked
            assert _registered(owners, SITE_ID, SWITCH_ID)

        # Legacy answers again without the switch: counting resumes.
        devices.get_legacy_site_devices.side_effect = None
        devices.get_legacy_site_devices.return_value = _legacy()
        await owners.device.async_refresh()
        assert _count(owners, SITE_ID, SWITCH_ID) == 2
        await owners.device.async_refresh()
        assert not _registered(owners, SITE_ID, SWITCH_ID)
    finally:
        await async_shutdown_owners(owners)


async def test_cross_check_makes_no_extra_legacy_call(hass: HomeAssistant) -> None:
    """The cross-check reuses each poll's legacy list: one call per refresh."""
    owners = build_seeded_owners(hass)
    try:
        await _seed_switch(owners)
        legacy_call = owners.network_client.devices.get_legacy_site_devices

        # Normal polls: nothing pending.
        legacy_call.reset_mock()
        for _ in range(MAX_CONSECUTIVE_MISSING_POLLS):
            await owners.device.async_refresh()
        assert legacy_call.await_count == MAX_CONSECUTIVE_MISSING_POLLS
        assert {call.args for call in legacy_call.await_args_list} == {("default",)}

        # Pending removal held by legacy: still one call per refresh.
        owners.network_client.devices.get_all.return_value = _v1()
        legacy_call.reset_mock()
        for _ in range(MAX_CONSECUTIVE_MISSING_POLLS):
            await owners.device.async_refresh()
        assert legacy_call.await_count == MAX_CONSECUTIVE_MISSING_POLLS
        assert _registered(owners, SITE_ID, SWITCH_ID)

        # Pending removal that legacy confirms: still one call per refresh.
        legacy_call.return_value = _legacy()
        legacy_call.reset_mock()
        for _ in range(MAX_CONSECUTIVE_MISSING_POLLS):
            await owners.device.async_refresh()
        assert legacy_call.await_count == MAX_CONSECUTIVE_MISSING_POLLS
        assert not _registered(owners, SITE_ID, SWITCH_ID)
    finally:
        await async_shutdown_owners(owners)


async def test_site_without_classic_name_uses_v1_alone(hass: HomeAssistant) -> None:
    """With no classic site name there is no legacy list; v1 decides alone."""
    owners = build_seeded_owners(hass)
    try:
        await _seed_switch(owners)
        owners.config.data = {**owners.config.data, "legacy_site_names": {}}
        devices = owners.network_client.devices
        devices.get_all.return_value = _v1()
        devices.get_legacy_site_devices.reset_mock()

        for _ in range(MAX_CONSECUTIVE_MISSING_POLLS):
            await owners.device.async_refresh()

        devices.get_legacy_site_devices.assert_not_awaited()
        assert not _registered(owners, SITE_ID, SWITCH_ID)
    finally:
        await async_shutdown_owners(owners)


async def test_multi_site_cross_check_is_per_site(hass: HomeAssistant) -> None:
    """Only the site with a pending removal is affected by its legacy answer."""
    owners = build_seeded_owners(hass)
    try:
        devices = owners.network_client.devices
        v1_by_site: dict[str, list[dict[str, Any]]] = {
            SITE_ID: _v1(SWITCH_V1),
            SITE_B: _v1(SITE_B_SWITCH_V1),
        }
        legacy_by_name: dict[str, list[dict[str, Any]] | Exception] = {
            "default": _legacy(SWITCH_LEGACY),
            SITE_B_LEGACY_NAME: _legacy(SITE_B_SWITCH_LEGACY),
        }

        async def get_all(site_id: str) -> list[dict[str, Any]]:
            return copy.deepcopy(v1_by_site[site_id])

        async def get_legacy_site_devices(name: str) -> list[dict[str, Any]]:
            answer = legacy_by_name[name]
            if isinstance(answer, Exception):
                raise answer
            return copy.deepcopy(answer)

        devices.get_all.side_effect = get_all
        devices.get_legacy_site_devices.side_effect = get_legacy_site_devices
        await owners.config.async_refresh()
        owners.config.get_site_ids = MagicMock(return_value=[SITE_ID, SITE_B])
        owners.config.data = {
            **owners.config.data,
            "legacy_site_names": {
                SITE_ID: "default",
                SITE_B: SITE_B_LEGACY_NAME,
            },
        }
        await owners.device.async_refresh()
        await owners.device.async_refresh()
        _register(owners, SITE_ID, SWITCH_ID, SWITCH_MAC)
        _register(owners, SITE_B, SITE_B_SWITCH_ID, SITE_B_SWITCH_MAC)

        # Site B's v1 list omits its switch while its legacy list keeps it;
        # site A has nothing pending. One legacy call per site per refresh.
        v1_by_site[SITE_B] = _v1()
        devices.get_legacy_site_devices.reset_mock()
        for _ in range(MAX_CONSECUTIVE_MISSING_POLLS):
            await owners.device.async_refresh()
        assert devices.get_legacy_site_devices.await_count == (
            2 * MAX_CONSECUTIVE_MISSING_POLLS
        )
        assert _count(owners, SITE_B, SITE_B_SWITCH_ID) is None
        assert _registered(owners, SITE_B, SITE_B_SWITCH_ID)

        # Site A's legacy call fails, which does not matter with nothing
        # pending there; site B's legacy list now agrees, so it counts down.
        legacy_by_name["default"] = UniFiResponseError("server error", status_code=500)
        legacy_by_name[SITE_B_LEGACY_NAME] = _legacy()
        for expected in range(1, MAX_CONSECUTIVE_MISSING_POLLS):
            await owners.device.async_refresh()
            assert owners.device.last_update_success
            assert _count(owners, SITE_B, SITE_B_SWITCH_ID) == expected
        await owners.device.async_refresh()
        assert not _registered(owners, SITE_B, SITE_B_SWITCH_ID)
        assert _registered(owners, SITE_ID, SWITCH_ID)
        assert owners.device._site_absence_counts[SITE_ID] == {}
    finally:
        await async_shutdown_owners(owners)


async def test_unverified_legacy_list_restarts_the_count_of_a_device_that_is_back(
    hass: HomeAssistant,
) -> None:
    """While only the legacy check is unverifiable, a device v1 lists again restarts."""
    other_id = "switch2_v1_id"
    other_mac = "aa:aa:aa:aa:aa:03"
    other_v1 = {**SWITCH_V1, "id": other_id, "mac": other_mac, "name": "Switch 2"}
    other_legacy = {**SWITCH_LEGACY, "_id": "switch2_legacy_id", "mac": other_mac}
    owners = build_seeded_owners(hass)
    try:
        devices = owners.network_client.devices
        devices.get_all.return_value = _v1(SWITCH_V1, other_v1)
        devices.get_legacy_site_devices.return_value = _legacy(
            SWITCH_LEGACY, other_legacy
        )
        await owners.config.async_refresh()
        await owners.device.async_refresh()
        await owners.device.async_refresh()
        _register(owners, SITE_ID, SWITCH_ID, SWITCH_MAC)
        _register(owners, SITE_ID, other_id, other_mac)

        # Both switches drop out of v1 and legacy: both counts advance.
        devices.get_all.return_value = _v1()
        devices.get_legacy_site_devices.return_value = _legacy()
        await owners.device.async_refresh()
        assert _count(owners, SITE_ID, SWITCH_ID) == 1
        assert _count(owners, SITE_ID, other_id) == 1

        # Switch 2 is back in v1, the legacy check fails: switch 2's count
        # restarts, the still-missing switch keeps its count.
        devices.get_all.return_value = _v1(other_v1)
        devices.get_legacy_site_devices.side_effect = UniFiResponseError(
            "server error", status_code=500
        )
        await owners.device.async_refresh()
        assert owners.device.last_update_success
        assert _count(owners, SITE_ID, other_id) is None
        assert _count(owners, SITE_ID, SWITCH_ID) == 1
        assert _registered(owners, SITE_ID, SWITCH_ID)
        assert _registered(owners, SITE_ID, other_id)
    finally:
        await async_shutdown_owners(owners)


async def test_unverified_legacy_list_tracks_a_new_v1_device(
    hass: HomeAssistant,
) -> None:
    """A device v1 first reports while legacy is unverifiable is tracked."""
    new_id = "switch_new_v1_id"
    new_mac = "aa:aa:aa:aa:aa:04"
    new_v1 = {**SWITCH_V1, "id": new_id, "mac": new_mac, "name": "New switch"}
    owners = build_seeded_owners(hass)
    try:
        await _seed_switch(owners)
        devices = owners.network_client.devices
        # The switch goes missing (a removal is pending) while a new switch
        # appears and the legacy check fails.
        devices.get_all.return_value = _v1(new_v1)
        devices.get_legacy_site_devices.side_effect = UniFiResponseError(
            "server error", status_code=500
        )
        await owners.device.async_refresh()
        assert owners.device.last_update_success
        tracked = owners.device._site_tracked_devices[SITE_ID]
        assert new_id in tracked
        assert SWITCH_ID in tracked
        _register(owners, SITE_ID, new_id, new_mac)

        # Legacy answers again and both switches are gone from both lists:
        # each is counted and removed on the Nth eligible poll.
        devices.get_legacy_site_devices.side_effect = None
        devices.get_legacy_site_devices.return_value = _legacy()
        devices.get_all.return_value = _v1()
        for _ in range(MAX_CONSECUTIVE_MISSING_POLLS):
            await owners.device.async_refresh()
        assert not _registered(owners, SITE_ID, new_id)
        assert not _registered(owners, SITE_ID, SWITCH_ID)
    finally:
        await async_shutdown_owners(owners)
