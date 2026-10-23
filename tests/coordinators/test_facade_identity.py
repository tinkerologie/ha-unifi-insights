# Copyright 2026 UniFi Insights contributors
"""
Characterization tests for the identity of the facade's shared sections.

The facade passes each owner's published sections through by reference, and
the WebSocket streams skip a rebuild when every input is the same object as
last time (``websocket_api._same_inputs``). These pin that contract through
#342: a rebuilt snapshot holds the owners' own objects, and an owner update
leaves the sections it does not touch identical. They also record what a
snapshot taken while a device or config refresh is in flight sees once that
refresh completes.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.unifi_insights.data_transforms import NETWORK_FACTS_KEY
from tests.fixtures.command_owners import PDU_ID, SITE_ID
from tests.fixtures.library_responses import (
    SAMPLE_PDU_DEVICE,
    SAMPLE_PDU_LEGACY_DEVICE_COMPLETE,
    SAMPLE_SITE,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.fixtures.command_owners import SeededOwners

CONFIG_SECTIONS: tuple[str, ...] = (
    "sites",
    "wifi",
    "vouchers",
    "firewall_rules",
    "policy_based_routes",
    "vpn_clients",
    "port_forwards",
    "traffic_rules",
    "nat_rules",
    "static_routes",
    "site_vpns",
    "rule_changes",
    "network_info",
    "client_links",
    "internet_activity",
    "internet_activity_unavailable",
)
DEVICE_SECTIONS: tuple[str, ...] = (
    "devices",
    "clients",
    "stats",
    "vpn_connections",
    NETWORK_FACTS_KEY,
)


class _Gate:
    """An API answer that waits until the test releases it."""

    def __init__(self, answer: Any) -> None:
        """Hold the answer until the test releases it."""
        self.answer = answer
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        """Signal that the call is out, then wait for the release."""
        self.started.set()
        await self.release.wait()
        return self.answer


async def _while_in_flight(
    gate: _Gate, request: Awaitable[Any], during: Callable[[], None]
) -> None:
    """
    Run ``request``; call ``during`` once its gated call is out; then answer.

    Fails, instead of hanging, when the request never reaches the gated call,
    and never leaves the request task pending.
    """
    task = asyncio.ensure_future(request)
    started = asyncio.ensure_future(gate.started.wait())
    try:
        done, _ = await asyncio.wait(
            {task, started}, timeout=5, return_when=asyncio.FIRST_COMPLETED
        )
        if started not in done:
            if task.done():
                await task  # surface the request's own error
            msg = "the request never reached the gated call"
            raise AssertionError(msg)
        during()
    except BaseException:
        gate.release.set()
        started.cancel()
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        raise
    gate.release.set()
    await task


def test_snapshot_sections_are_the_owners_objects(
    seeded_owners: SeededOwners,
) -> None:
    """Each shared section is the object its owner published, not a copy."""
    snapshot = seeded_owners.facade.data

    for key in CONFIG_SECTIONS:
        assert snapshot[key] is seeded_owners.config.data[key], key
    for key in DEVICE_SECTIONS:
        assert snapshot[key] is seeded_owners.device.data[key], key
    assert snapshot["protect"] is seeded_owners.protect.data


async def _refresh_config(owners: SeededOwners) -> None:
    """Run a full config refresh."""
    await owners.config.async_refresh()


async def _refresh_vouchers(owners: SeededOwners) -> None:
    """Refresh one site's voucher inventory."""
    await owners.config.async_refresh_vouchers(SITE_ID)


async def _refresh_device(owners: SeededOwners) -> None:
    """Run a full device refresh."""
    await owners.device.async_refresh()


@pytest.fixture
async def complete_outlet_baseline(seeded_owners: SeededOwners) -> None:
    """Trust a complete outlet poll before the test snapshots the facade."""
    devices = seeded_owners.network_client.devices
    devices.get_legacy_site_devices.side_effect = None
    devices.get_legacy_site_devices.return_value = [
        copy.deepcopy(SAMPLE_PDU_LEGACY_DEVICE_COMPLETE)
    ]
    await seeded_owners.device.async_refresh()


async def _write_outlet(owners: SeededOwners) -> None:
    """Turn an outlet off through the device owner's overlay."""
    owners.network_client.devices.set_outlet_state.return_value = True
    await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)


async def _refresh_protect(owners: SeededOwners) -> None:
    """Run a full Protect refresh."""
    await owners.protect.async_refresh()


@pytest.mark.parametrize(
    ("update", "touched", "untouched"),
    [
        pytest.param(
            _refresh_config,
            tuple(key for key in CONFIG_SECTIONS if key != "network_info"),
            # The full refresh keeps the network info it already has.
            ("network_info", *DEVICE_SECTIONS, "protect"),
            id="config_refresh",
        ),
        pytest.param(
            _refresh_vouchers,
            ("vouchers",),
            (
                *(key for key in CONFIG_SECTIONS if key != "vouchers"),
                *DEVICE_SECTIONS,
                "protect",
            ),
            id="voucher_refresh",
        ),
        pytest.param(
            _refresh_device,
            DEVICE_SECTIONS,
            (*CONFIG_SECTIONS, "protect"),
            id="device_refresh",
        ),
        pytest.param(
            _write_outlet,
            ("devices",),
            (*CONFIG_SECTIONS, "protect"),
            id="outlet_overlay",
        ),
        pytest.param(
            _refresh_protect,
            ("protect",),
            (*CONFIG_SECTIONS, *DEVICE_SECTIONS),
            id="protect_refresh",
        ),
    ],
)
async def test_owner_update_keeps_untouched_sections(
    seeded_owners: SeededOwners,
    complete_outlet_baseline: None,
    update: Callable[[SeededOwners], Awaitable[None]],
    touched: tuple[str, ...],
    untouched: tuple[str, ...],
) -> None:
    """An owner update replaces the sections it writes; the rest keep identity."""
    facade = seeded_owners.facade
    before = facade.data

    await update(seeded_owners)

    after = facade.data
    assert after is not before
    for key in untouched:
        assert after[key] is before[key], key
    for key in touched:
        assert after[key] is not before[key], key


async def test_mid_refresh_snapshot_keeps_its_contents(
    seeded_owners: SeededOwners,
) -> None:
    """
    A snapshot taken during a device refresh is unchanged when the refresh ends.

    The refresh builds its sections locally and publishes them once, as a new
    root, after its last API call. A facade rebuild in between keeps the
    previous maps, and the previous root is left as it was.
    """
    device = seeded_owners.device
    facade = seeded_owners.facade
    root_before = device.data
    site_devices_before = device.data["devices"][SITE_ID]
    site_stats_before = device.data["stats"][SITE_ID]
    gate = _Gate([copy.deepcopy(SAMPLE_PDU_DEVICE)])
    seeded_owners.network_client.devices.get_all.side_effect = gate.__call__
    mid: dict[str, Any] = {}
    seen_then: dict[str, Any] = {}

    def rebuild() -> None:
        """Rebuild the facade snapshot while the refresh is in flight."""
        seeded_owners.config.async_update_listeners()
        mid.update(facade.data)
        seen_then.update(mid["devices"])

    await _while_in_flight(gate, device.async_refresh(), rebuild)

    assert device.last_update_success
    assert seen_then[SITE_ID] is site_devices_before
    assert mid["devices"][SITE_ID] is site_devices_before
    assert mid["devices"][SITE_ID] is not device.data["devices"][SITE_ID]
    assert mid["stats"][SITE_ID] is site_stats_before
    assert mid["stats"][SITE_ID] is not device.data["stats"][SITE_ID]
    assert device.data is not root_before
    assert root_before["devices"][SITE_ID] is site_devices_before


async def test_mid_config_refresh_snapshot_keeps_previous_sites(
    seeded_owners: SeededOwners,
) -> None:
    """
    A snapshot taken during a config refresh keeps the previous sites.

    The refresh keeps its new sites and sections local and publishes them
    once, as a new root, after its last API call. A facade rebuild in between
    sees the previous, consistent set, and the previous root is left as it
    was.
    """
    config = seeded_owners.config
    facade = seeded_owners.facade
    root_before = config.data
    sites_before = config.data["sites"]
    renamed = {**copy.deepcopy(SAMPLE_SITE), "internalReference": "default"}
    renamed["name"] = "Renamed"
    seeded_owners.network_client.sites.get_all.return_value = [renamed]
    gate = _Gate([{"name": "default", "desc": SAMPLE_SITE["desc"]}])
    seeded_owners.network_client.sites.get_legacy_all.side_effect = gate.__call__
    mid: dict[str, Any] = {}

    def rebuild() -> None:
        """Rebuild the facade snapshot while the refresh is in flight."""
        seeded_owners.device.async_update_listeners()
        mid.update(facade.data)

    await _while_in_flight(gate, config.async_refresh(), rebuild)

    assert config.last_update_success
    assert mid["sites"] is sites_before
    assert mid["sites"][SITE_ID]["name"] == "Default"
    assert config.data["sites"][SITE_ID]["name"] == "Renamed"
    assert config.data is not root_before
    assert root_before["sites"] is sites_before
