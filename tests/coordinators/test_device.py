# Copyright 2026 UniFi Insights contributors
"""
Tests for the device owner's command foundations (ADR-010).

Covers PoE port eligibility, which lives next to the power-cycle command and
reads #337's port facts, and the device coordinator's reuse of the config
coordinator's legacy site-name rule.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from unifi_official_api import UniFiConnectionError

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
from tests.fixtures.command_owners import PDU_ID, SITE_ID

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
    """The device coordinator reads the config mapping instead of its own."""
    device = seeded_owners.device
    config = seeded_owners.config
    network_client = seeded_owners.network_client

    assert config.data["legacy_site_names"] == {SITE_ID: "default"}
    assert config.legacy_site_names == {SITE_ID: "default"}
    network_client.sites.get_legacy_all.reset_mock()
    network_client.devices.get_legacy_site_devices.reset_mock()
    config.data = {**config.data, "legacy_site_names": {SITE_ID: "renamed"}}

    await device.async_refresh()

    assert device.last_update_success
    network_client.sites.get_legacy_all.assert_not_called()
    network_client.devices.get_legacy_site_devices.assert_awaited_once_with("renamed")


MAC = "AA:BB:CC:DD:EE:FF"
LEGACY_PDU_ID = "60a1b2c3d4e5f67890123456"


def _with_client(owners: SeededOwners, record: dict[str, Any]) -> None:
    """Publish one client record in the device owner's data."""
    owners.device.data = {**owners.device.data, "clients": {SITE_ID: {"c1": record}}}


class TestDeviceCommands:
    """Restart and PoE: one call, a translated error, no refresh."""

    async def test_restart_calls_the_device_action(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A restart is sent with the integration IDs and returns its result."""
        restart = seeded_owners.network_client.devices.restart
        restart.return_value = True
        refreshes = seeded_owners.network_client.devices.get_all.await_count

        assert await seeded_owners.device.async_restart_device(SITE_ID, PDU_ID) is True

        restart.assert_awaited_once_with(SITE_ID, PDU_ID)
        assert seeded_owners.network_client.devices.get_all.await_count == refreshes

    async def test_falsy_restart_raises_restart_rejected(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A restart the console does not accept raises a translated error."""
        seeded_owners.network_client.devices.restart.return_value = False

        with pytest.raises(HomeAssistantError) as caught:
            await seeded_owners.device.async_restart_device(SITE_ID, PDU_ID)

        assert caught.value.translation_domain == DOMAIN
        assert caught.value.translation_key == "restart_rejected"
        assert caught.value.translation_placeholders == {"device_id": PDU_ID}

    async def test_restart_failure_is_translated(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A failed restart keeps its message and the API error as cause."""
        cause = UniFiConnectionError("down")
        seeded_owners.network_client.devices.restart.side_effect = cause

        with pytest.raises(HomeAssistantError, match=r"^Unable to restart") as caught:
            await seeded_owners.device.async_restart_device(SITE_ID, PDU_ID)

        assert caught.value.translation_key == "command_failed"
        assert caught.value.__cause__ is cause

    async def test_power_cycle_checks_the_port_first(
        self, seeded_owners: SeededOwners
    ) -> None:
        """An eligible port is power cycled; an ineligible one is never sent."""
        device = seeded_owners.device
        device.data = {**device.data, **_data()}
        action = seeded_owners.network_client.devices.execute_port_action
        action.return_value = True

        assert await device.async_power_cycle_port(SITE_ID, SWITCH_ID, 1) is True
        action.assert_awaited_once_with(SITE_ID, SWITCH_ID, 1, "POWER_CYCLE")

        action.reset_mock()
        with pytest.raises(ServiceValidationError) as caught:
            await device.async_power_cycle_port(SITE_ID, SWITCH_ID, 3)
        assert caught.value.translation_key == "port_not_poe"
        action.assert_not_awaited()


class TestOutletCommand:
    """Outlet writes: classic call, list-aware overlay, deferred refresh."""

    async def test_outlet_write_publishes_a_copy_on_write_overlay(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Only the written outlet and its ancestors are replaced."""
        device = seeded_owners.device
        client = seeded_owners.network_client
        client.devices.set_outlet_state.return_value = True
        before_device = device.data["devices"][SITE_ID][PDU_ID]
        before_table = before_device["outlet_table"]
        seen: list[dict[str, Any]] = []
        device.async_add_listener(
            lambda: seen.append(device.data["devices"][SITE_ID][PDU_ID])
        )
        refreshes = client.devices.get_all.await_count

        result = await device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)

        assert result is True
        client.devices.set_outlet_state.assert_awaited_once_with(
            "default",
            LEGACY_PDU_ID,
            2,
            state=True,
            cycle_enabled=None,
            current_device=before_device,
        )
        overlay = seen[0]
        assert overlay is not before_device
        assert overlay["outlet_table"] is not before_table
        assert overlay["outlet_table"][1]["relay_state"] is True
        assert overlay["outlet_table"][1]["cycle_enabled"] is False
        # Unwritten outlets stay shared and the old snapshot is untouched.
        assert overlay["outlet_table"][0] is before_table[0]
        assert before_table[1]["relay_state"] is False
        assert client.devices.get_all.await_count == refreshes + 1

    async def test_cycle_write_overlays_cycle_enabled(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A cycle write overlays both written fields."""
        device = seeded_owners.device
        seen: list[dict[str, Any]] = []
        device.async_add_listener(
            lambda: seen.append(device.data["devices"][SITE_ID][PDU_ID])
        )

        await device.async_set_outlet_state(
            SITE_ID, PDU_ID, 1, state=True, cycle_enabled=False
        )

        assert seen[0]["outlet_table"][0]["cycle_enabled"] is False
        assert seen[0]["outlet_table"][0]["relay_state"] is True

    @pytest.mark.parametrize(
        ("outlet_table", "device_id"),
        [
            ([{"index": 9}, "junk", {"outlet_idx": "x"}], PDU_ID),
            ("not-a-list", PDU_ID),
            ([{"index": 2}], "missing"),
        ],
    )
    async def test_unknown_outlet_gets_no_overlay(
        self, seeded_owners: SeededOwners, outlet_table: Any, device_id: str
    ) -> None:
        """No matching outlet means the write is sent without an overlay."""
        device = seeded_owners.device
        pdu = device.data["devices"][SITE_ID][PDU_ID]
        pdu["outlet_table"] = outlet_table
        listener = MagicMock()
        device.async_add_listener(listener)
        seeded_owners.network_client.devices.get_all.side_effect = UniFiConnectionError(
            "down"
        )

        await device.async_set_outlet_state(SITE_ID, device_id, 2, state=True)

        seeded_owners.network_client.devices.set_outlet_state.assert_awaited_once()
        assert device.data["devices"][SITE_ID][PDU_ID]["outlet_table"] == outlet_table

    async def test_outlet_index_aliases_match(
        self, seeded_owners: SeededOwners
    ) -> None:
        """``outlet_idx`` and ``outletIdx`` identify an outlet too."""
        device = seeded_owners.device
        pdu = device.data["devices"][SITE_ID][PDU_ID]
        pdu["outlet_table"] = [{"outletIdx": "4", "relay_state": False}]
        seen: list[Any] = []
        device.async_add_listener(
            lambda: seen.append(device.data["devices"][SITE_ID][PDU_ID])
        )

        await device.async_set_outlet_state(SITE_ID, PDU_ID, 4, state=True)

        assert seen[0]["outlet_table"] == [{"outletIdx": "4", "relay_state": True}]

    async def test_failed_outlet_write_is_translated(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A failed write raises, publishes nothing and does not refresh."""
        device = seeded_owners.device
        client = seeded_owners.network_client
        client.devices.set_outlet_state.side_effect = UniFiConnectionError("down")
        before = device.data
        refreshes = client.devices.get_all.await_count

        with pytest.raises(HomeAssistantError, match=r"^Unable to set outlet 2") as err:
            await device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)

        assert err.value.translation_key == "command_failed"
        assert device.data is before
        assert client.devices.get_all.await_count == refreshes


class TestClientCommands:
    """Classic client commands resolve the MAC and the mapped site name."""

    @pytest.mark.parametrize(
        ("command", "method", "refreshed"),
        [
            ("async_block_client", "block", True),
            ("async_unblock_client", "unblock", True),
            ("async_reconnect_client", "reconnect", False),
            ("async_forget_client", "forget", False),
        ],
    )
    async def test_client_command_uses_mac_and_classic_site(
        self,
        seeded_owners: SeededOwners,
        command: str,
        method: str,
        *,
        refreshed: bool,
    ) -> None:
        """Each command targets the MAC; block and unblock refresh after."""
        _with_client(seeded_owners, {"id": "c1", "macAddress": MAC})
        client = seeded_owners.network_client
        refreshes = client.devices.get_all.await_count

        await getattr(seeded_owners.device, command)(SITE_ID, "c1")

        getattr(client.clients, method).assert_awaited_once_with("default", MAC)
        assert client.devices.get_all.await_count == refreshes + refreshed

    async def test_mapped_site_name_is_used_without_internal_reference(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Client commands accept the published (fuzzy) mapping."""
        config = seeded_owners.config
        del config.data["sites"][SITE_ID]["internalReference"]
        config.data["legacy_site_names"] = {SITE_ID: "branch"}
        _with_client(seeded_owners, {"id": "c1", "mac": MAC})

        await seeded_owners.device.async_reconnect_client(SITE_ID, "c1")

        seeded_owners.network_client.clients.reconnect.assert_awaited_once_with(
            "branch", MAC
        )

    async def test_client_without_mac_is_a_translated_error(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A client action without a resolvable MAC raises client_not_resolvable."""
        _with_client(seeded_owners, {"id": "c1"})

        with pytest.raises(HomeAssistantError) as caught:
            await seeded_owners.device.async_block_client(SITE_ID, "c1")

        assert caught.value.translation_key == "client_not_resolvable"
        assert caught.value.translation_placeholders == {"client_id": "c1"}
        seeded_owners.network_client.clients.block.assert_not_awaited()

    async def test_client_failure_is_translated(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A failed client command keeps its message and does not refresh."""
        _with_client(seeded_owners, {"id": "c1", "macAddress": MAC})
        client = seeded_owners.network_client
        client.clients.block.side_effect = UniFiConnectionError("down")
        refreshes = client.devices.get_all.await_count

        with pytest.raises(HomeAssistantError, match=r"^Unable to block client c1"):
            await seeded_owners.device.async_block_client(SITE_ID, "c1")

        assert client.devices.get_all.await_count == refreshes

    async def test_forget_batch_uses_the_verified_site_name(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Batch forget writes only to the verified classic site."""
        forget_batch = seeded_owners.network_client.clients.forget_batch
        forget_batch.return_value = True

        assert (
            await seeded_owners.device.async_forget_clients_batch(SITE_ID, [MAC])
            is True
        )
        forget_batch.assert_awaited_once_with("default", [MAC])

    async def test_forget_batch_never_uses_the_mapping(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Without internalReference batch forget raises and sends nothing."""
        config = seeded_owners.config
        del config.data["sites"][SITE_ID]["internalReference"]
        forget_batch = seeded_owners.network_client.clients.forget_batch

        with pytest.raises(HomeAssistantError) as caught:
            await seeded_owners.device.async_forget_clients_batch(SITE_ID, [MAC])

        assert caught.value.translation_key == "legacy_site_not_found"
        forget_batch.assert_not_awaited()

    async def test_forget_batch_failure_is_translated(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A failed batch forget raises command_failed with its message."""
        seeded_owners.network_client.clients.forget_batch.side_effect = RuntimeError(
            "dropped"
        )

        with pytest.raises(
            HomeAssistantError, match="Unable to forget clients for site"
        ) as caught:
            await seeded_owners.device.async_forget_clients_batch(SITE_ID, [MAC])

        assert caught.value.translation_key == "command_failed"
