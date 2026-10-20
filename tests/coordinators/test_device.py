# Copyright 2026 UniFi Insights contributors
"""
Tests for the device owner's command foundations (ADR-010).

Covers PoE port eligibility, which lives next to the power-cycle command and
reads #337's port facts, and the device coordinator's reuse of the config
coordinator's legacy site-name rule.
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import UpdateFailed
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiNotFoundError,
    UniFiRateLimitError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from unifi_official_api.network.models import LegacyPortMetrics, PortBytesMetrics

from custom_components.unifi_insights.const import DOMAIN, SCAN_INTERVAL_DEVICE
from custom_components.unifi_insights.coordinators.config import (
    UnifiInsightsConfigCoordinator,
)
from custom_components.unifi_insights.coordinators.device import (
    MAX_STATS_REUSE_POLLS,
    UnifiInsightsDeviceCoordinator,
    get_device_port,
    port_can_be_power_cycled,
    power_cyclable_ports,
    require_power_cyclable_port,
)
from custom_components.unifi_insights.data_transforms import (
    NETWORK_FACTS_KEY,
    SiteNetworkFacts,
    build_site_network_facts,
)
from custom_components.unifi_insights.entity import is_device_online
from tests.conftest import set_mock_device_lookup
from tests.fixtures.command_owners import PDU_ID, SITE_ID
from tests.fixtures.coordinator_clients import (
    _create_mock_model,
    _create_mock_network_client,
    _create_mock_protect_client,
)
from tests.fixtures.library_responses import (
    SAMPLE_UDM_PRO_OVERVIEW,
    SAMPLE_UDM_PRO_STATS,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

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


# --- Device coordinator ------------------------------------------------------


class TestUnifiDeviceCoordinator:
    """Tests for UnifiInsightsDeviceCoordinator."""

    @pytest.fixture
    def config_coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> UnifiInsightsConfigCoordinator:
        """Create a config coordinator."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        coord = UnifiInsightsConfigCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )
        # Pre-populate with site data and the legacy site mapping the config
        # coordinator publishes for the device coordinator (ADR-010).
        coord.data["sites"] = {"default": {"id": "default", "name": "Default"}}
        coord.data["legacy_site_names"] = {"default": "default"}
        return coord

    @pytest.fixture
    def coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        config_coordinator: UnifiInsightsConfigCoordinator,
    ) -> UnifiInsightsDeviceCoordinator:
        """Create a device coordinator for testing."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        return UnifiInsightsDeviceCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
            config_coordinator=config_coordinator,
        )

    @pytest.mark.parametrize(
        ("error", "detail"),
        [
            (UniFiTimeoutError("offline"), "Timeout: offline"),
            (
                UniFiResponseError("offline", status_code=502),
                "API error (502): UniFiResponseError(status=502)",
            ),
            (
                UniFiResponseError("offline", status_code=400),
                "API error (400): UniFiResponseError(status=400)",
            ),
            (ValueError("offline"), "Error updating data: ValueError: offline"),
        ],
    )
    async def test_poll_outage_logs_only_transitions(
        self,
        coordinator: UnifiInsightsDeviceCoordinator,
        caplog: pytest.LogCaptureFixture,
        error: Exception,
        detail: str,
    ) -> None:
        """Real refreshes log one outage, one recovery, then re-arm."""
        caplog.set_level(logging.INFO)
        coordinator.network_client.devices.get_all.side_effect = error
        for _ in range(4):
            await coordinator.async_refresh()
            assert not coordinator.last_update_success
        outage = f"Error fetching {DOMAIN}_device data: {detail}"
        assert [
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        ] == [outage]
        coordinator.network_client.devices.get_all.side_effect = None
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        recovery = f"Fetching {DOMAIN}_device data recovered"
        assert caplog.messages.count(recovery) == 1
        coordinator.network_client.devices.get_all.side_effect = error
        await coordinator.async_refresh()
        assert [
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        ] == [outage, outage]

    async def test_legacy_fallback_logs_only_site_transitions(
        self,
        coordinator: UnifiInsightsDeviceCoordinator,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Each site reports fallback once, recovery once, and a new outage."""
        caplog.set_level(logging.DEBUG)
        endpoint = coordinator.network_client.devices
        endpoint.get_all.side_effect = UniFiResponseError("offline", status_code=502)
        endpoint.get_legacy_site_devices = AsyncMock(
            return_value=[{"_id": "legacy1", "mac": "AA:BB:CC:DD:EE:FF"}]
        )
        fallback = (
            "Device coordinator: Using 1 legacy devices as primary source "
            "for site default (v1 endpoint unavailable)"
        )
        recovery = "Device coordinator: v1 devices endpoint recovered for site default"
        for _ in range(4):
            await coordinator._process_site("default", legacy_site_name="default")
        assert [r.levelno for r in caplog.records if r.getMessage() == fallback] == [
            logging.INFO,
            logging.DEBUG,
            logging.DEBUG,
            logging.DEBUG,
        ]

        # A second site's transition is independent of the first site's streak.
        await coordinator._process_site("site2", legacy_site_name="site2")
        assert any(
            r.levelno == logging.INFO
            and "for site site2 (v1 endpoint unavailable)" in r.getMessage()
            for r in caplog.records
        )
        endpoint.get_all.side_effect = None
        for _ in range(2):
            await coordinator._process_site("default", legacy_site_name="default")
        assert caplog.messages.count(recovery) == 1
        assert (
            next(r.levelno for r in caplog.records if r.getMessage() == recovery)
            == logging.INFO
        )

        endpoint.get_all.side_effect = UniFiResponseError("offline", status_code=502)
        for _ in range(2):
            await coordinator._process_site("default", legacy_site_name="default")
        assert (
            sum(
                r.levelno == logging.INFO and r.getMessage() == fallback
                for r in caplog.records
            )
            == 2
        )

    def test_initialization(self, coordinator: UnifiInsightsDeviceCoordinator):
        """Test coordinator initialization."""
        assert coordinator.name == f"{DOMAIN}_device"
        assert coordinator.update_interval == SCAN_INTERVAL_DEVICE
        assert "devices" in coordinator.data
        assert "clients" in coordinator.data
        assert "stats" in coordinator.data

    @pytest.mark.asyncio
    async def test_async_update_data_success(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test successful data fetch."""
        result = await coordinator._async_update_data()

        assert "devices" in result
        assert "default" in result["devices"]
        assert "device1" in result["devices"]["default"]
        assert "clients" in result
        assert "stats" in result
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_process_site_5xx_fallback_with_client_retry(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """v1 devices 5xx falls back to legacy devices and retries clients.

        Regression test: the v1 devices endpoint returns 500 (USW Pro XG
        family), the site has a legacy name, and the first clients.get_all()
        call fails. The legacy device becomes the primary device, the retried
        client appears, and clients.get_all is awaited twice.
        """
        coordinator.network_client.devices.get_all = AsyncMock(
            side_effect=UniFiResponseError("Internal Server Error", status_code=500)
        )
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            return_value=[
                {
                    "_id": "60a1b2c3d4e5f67890123456",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "name": "Legacy Switch",
                    "model": "USW-24",
                    "type": "usw",
                    "ip": "192.168.1.10",
                    "up": True,
                    "port_table": [
                        {
                            "port_idx": 1,
                            "up": True,
                            "speed": 1000,
                            "name": "Port 1",
                        }
                    ],
                }
            ]
        )

        clients_call_count = 0

        async def clients_side_effect(site_id):
            nonlocal clients_call_count
            clients_call_count += 1
            if clients_call_count == 1:
                msg = "Service Unavailable"
                raise UniFiResponseError(msg, status_code=503)
            return [
                _create_mock_model(
                    {"id": "client1", "name": "Retried Client", "type": "WIRED"}
                )
            ]

        coordinator.network_client.clients.get_all = AsyncMock(
            side_effect=clients_side_effect
        )

        devices_dict, stats_dict, clients_dict = await coordinator._process_site(
            "default", legacy_site_name="default"
        )

        # The legacy device is keyed by its controller id (mapped from _id),
        # consistent with the v1 keying: devices without an id fall back to
        # their MAC, but this legacy device has an _id.
        assert "60a1b2c3d4e5f67890123456" in devices_dict
        assert "AA:BB:CC:DD:EE:FF" not in devices_dict
        legacy_device = devices_dict["60a1b2c3d4e5f67890123456"]
        assert legacy_device["name"] == "Legacy Switch"
        assert legacy_device["macAddress"] == "AA:BB:CC:DD:EE:FF"
        # Legacy "up": True is mapped to a v1-style string state that
        # is_device_online() can read.
        assert legacy_device["state"] == "ONLINE"
        assert is_device_online(legacy_device)
        # port_table is normalized into the v1-shaped ports list
        assert legacy_device["ports"][0]["idx"] == 1

        # The retried client appears, keyed by client id
        assert "client1" in clients_dict
        assert clients_dict["client1"]["name"] == "Retried Client"

        assert clients_call_count == 2
        assert coordinator.network_client.clients.get_all.await_count == 2

        # The per-device statistics endpoint still works in fallback mode:
        # the 5xx was on the v1 devices list endpoint only.
        assert stats_dict["60a1b2c3d4e5f67890123456"]["id"] == (
            "60a1b2c3d4e5f67890123456"
        )

    @pytest.mark.asyncio
    async def test_legacy_device_to_v1_dict_up_state_mapping(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Legacy bool ``up`` maps to a string ``state`` is_device_online reads."""
        online = coordinator._legacy_device_to_v1_dict(
            {"_id": "abc123", "mac": "AA:BB:CC:DD:EE:FF", "up": True}
        )
        offline = coordinator._legacy_device_to_v1_dict(
            {"_id": "abc123", "mac": "AA:BB:CC:DD:EE:FF", "up": False}
        )
        assert online["state"] == "ONLINE"
        assert is_device_online(online)
        assert offline["state"] == "OFFLINE"
        assert not is_device_online(offline)

        # An existing usable string state is preserved, and a non-string
        # legacy ``state`` (0/1) does not block the conversion.
        preserved = coordinator._legacy_device_to_v1_dict(
            {"_id": "abc123", "up": True, "state": "ONLINE"}
        )
        assert preserved["state"] == "ONLINE"
        int_state = coordinator._legacy_device_to_v1_dict(
            {"_id": "abc123", "up": True, "state": 1}
        )
        assert int_state["state"] == "ONLINE"
        assert is_device_online(int_state)

    def test_legacy_device_to_v1_dict_falls_back_to_mac_id(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """No ``_id`` maps id from mac; field_map keys are copied when absent."""
        mapped = coordinator._legacy_device_to_v1_dict(
            {
                "mac": "AA:BB:CC:DD:EE:FF",
                "fw_version": "6.6.55",
                "port_table": [
                    {"port_idx": 1, "up": True},
                    {"name": "no port_idx, dropped"},
                    "not-a-dict-entry",
                ],
            }
        )

        # No "_id" present, so id falls back to the device's mac.
        assert mapped["id"] == "AA:BB:CC:DD:EE:FF"
        # A field_map key present in legacy and absent from mapped is copied
        # to its v1 camelCase alias.
        assert mapped["firmwareVersion"] == "6.6.55"
        # port_table entries are filtered: the dict without port_idx and the
        # non-dict entry are both dropped, leaving only the valid port.
        assert len(mapped["ports"]) == 1
        assert mapped["ports"][0]["port_idx"] == 1

    def test_legacy_device_to_v1_dict_preserves_existing_id_and_mapped_field(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """An existing ``id``/mapped alias is not clobbered by the fallbacks."""
        mapped = coordinator._legacy_device_to_v1_dict(
            {
                "_id": "60a1b2c3d4e5f67890123456",
                "mac": "AA:BB:CC:DD:EE:FF",
                "id": "already-set",
                "fw_version": "6.6.55",
                "firmwareVersion": "already-mapped",
            }
        )
        # legacy_id is truthy but "id" is already present, so it is untouched.
        assert mapped["id"] == "already-set"
        # firmwareVersion is already present, so fw_version is not copied over.
        assert mapped["firmwareVersion"] == "already-mapped"

    def test_legacy_device_to_v1_dict_no_valid_ports_omits_ports_key(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """A port_table with no valid entries leaves ``ports`` unset."""
        mapped = coordinator._legacy_device_to_v1_dict(
            {
                "mac": "AA:BB:CC:DD:EE:FF",
                "port_table": [
                    {"name": "missing port_idx"},
                    "not-a-dict-entry",
                ],
            }
        )
        assert "ports" not in mapped

    def test_normalize_legacy_port_missing_port_idx_returns_none(self):
        """A port_table entry without port_idx cannot be normalized."""
        assert (
            UnifiInsightsDeviceCoordinator._normalize_legacy_port({"name": "no idx"})
            is None
        )

    def test_normalize_legacy_port_maps_all_optional_fields(self):
        """Optional identification/SFP fields are copied through when present."""
        normalized = UnifiInsightsDeviceCoordinator._normalize_legacy_port(
            {
                "port_idx": 5,
                "up": True,
                "media": "GE",
                "is_uplink": True,
                "name": "Uplink",
                "ifname": "eth5",
                "network_name": "Corporate LAN",
                "sfp_found": True,
                "sfp_part": "SFP-10G-SR",
                "sfp_vendor": "Ubiquiti",
                "sfp_serial": "SN12345",
                "sfp_compliance": "10GBASE-SR",
            }
        )
        assert normalized is not None
        assert normalized["media"] == "GE"
        assert normalized["is_uplink"] is True
        assert normalized["ifname"] == "eth5"
        assert normalized["network_name"] == "Corporate LAN"
        assert normalized["sfp_found"] is True
        assert normalized["sfp_part"] == "SFP-10G-SR"
        assert normalized["sfp_vendor"] == "Ubiquiti"
        assert normalized["sfp_serial"] == "SN12345"
        assert normalized["sfp_compliance"] == "10GBASE-SR"

    @pytest.mark.asyncio
    async def test_process_site_5xx_fallback_legacy_failure_raises(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """When v1 5xx and the legacy fetch both fail, the v1 error propagates."""
        coordinator.network_client.devices.get_all = AsyncMock(
            side_effect=UniFiResponseError("Internal Server Error", status_code=500)
        )
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            side_effect=UniFiConnectionError("Connection refused")
        )

        with pytest.raises(UniFiResponseError):
            await coordinator._process_site("default", legacy_site_name="default")

    @pytest.mark.asyncio
    async def test_process_site_5xx_with_empty_legacy_raises(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """A v1 5xx with an empty legacy response fails instead of wiping devices."""
        coordinator.network_client.devices.get_all = AsyncMock(
            side_effect=UniFiResponseError("Internal Server Error", status_code=500)
        )
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            return_value=[]
        )

        with pytest.raises(UniFiResponseError):
            await coordinator._process_site("default", legacy_site_name="default")

        # The refresh keeps the previous snapshot instead of writing an
        # empty device map (same contract as any other site failure).
        previous = copy.deepcopy(coordinator.data["devices"])
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
        assert coordinator.data["devices"] == previous

    @pytest.mark.asyncio
    async def test_process_site_5xx_without_legacy_site_raises(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """A v1 5xx without a legacy site name cannot fall back and raises."""
        coordinator.network_client.devices.get_all = AsyncMock(
            side_effect=UniFiResponseError("Internal Server Error", status_code=500)
        )

        with pytest.raises(UniFiResponseError):
            await coordinator._process_site("default")

    @pytest.mark.asyncio
    async def test_process_site_4xx_does_not_fall_back(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Only 5xx triggers the legacy fallback; a 4xx error propagates."""
        coordinator.network_client.devices.get_all = AsyncMock(
            side_effect=UniFiResponseError("Forbidden", status_code=403)
        )

        with pytest.raises(UniFiResponseError):
            await coordinator._process_site("default", legacy_site_name="default")

        coordinator.network_client.devices.get_legacy_site_devices.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_site_clients_5xx_raises_without_fallback(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """A clients failure with healthy v1 devices propagates (no fallback)."""
        coordinator.network_client.clients.get_all = AsyncMock(
            side_effect=UniFiResponseError("Service Unavailable", status_code=503)
        )

        with pytest.raises(UniFiResponseError):
            await coordinator._process_site("default", legacy_site_name="default")

        coordinator.network_client.clients.get_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_update_data_uses_legacy_devices_on_v1_5xx(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """End-to-end: a v1 devices 5xx refresh succeeds via legacy devices."""
        coordinator.network_client.devices.get_all = AsyncMock(
            side_effect=UniFiResponseError("Internal Server Error", status_code=500)
        )
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            return_value=[
                {
                    "_id": "60a1b2c3d4e5f67890123456",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "name": "Legacy Switch",
                    "model": "USW-24",
                    "type": "usw",
                    "up": True,
                }
            ]
        )

        result = await coordinator._async_update_data()

        assert "60a1b2c3d4e5f67890123456" in result["devices"]["default"]
        assert result["devices"]["default"]["60a1b2c3d4e5f67890123456"]["name"] == (
            "Legacy Switch"
        )
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_merges_legacy_temperature(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test successful merge of legacy temperature fields into device data."""
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            return_value=[
                {
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "general_temperature": 47.5,
                    "temperatures": [
                        {"name": "CPU", "value": 51.0},
                        {"name": "Local", "value": 47.5},
                    ],
                    "has_temperature": False,
                }
            ]
        )

        result = await coordinator._async_update_data()

        device_data = result["devices"]["default"]["device1"]
        assert device_data["generalTemperature"] == 47.5
        assert device_data["hasTemperature"] is True
        assert device_data["temperatures"][0]["name"] == "CPU"

    @pytest.mark.asyncio
    async def test_async_update_data_merges_legacy_outlets(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test merge of legacy outlet table and power totals into device data."""
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            return_value=[
                {
                    "_id": "60a1b2c3d4e5f67890123456",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "outlet_ac_power_consumption": "245.50",
                    "outlet_ac_power_budget": "1800.00",
                    "outlet_table": [
                        {
                            "index": 1,
                            "name": "Outlet 1",
                            "relay_state": True,
                            "cycle_enabled": True,
                            "outlet_caps": 3,
                            "outlet_voltage": "120.2",
                            "outlet_current": "1.25",
                            "outlet_power": "150.0",
                            "outlet_power_factor": "0.98",
                        }
                    ],
                    "outlet_overrides": [
                        {
                            "index": 1,
                            "relay_state": True,
                            "cycle_enabled": True,
                        }
                    ],
                }
            ]
        )

        result = await coordinator._async_update_data()

        device_data = result["devices"]["default"]["device1"]
        assert device_data["_id"] == "60a1b2c3d4e5f67890123456"
        assert device_data["ac_power_consumption"] == 245.5
        assert device_data["ac_power_budget"] == 1800.0
        assert len(device_data["outlet_table"]) == 1
        assert device_data["outlet_table"][0]["name"] == "Outlet 1"
        assert device_data["outlet_table"][0]["relay_state"] is True
        assert device_data["outlet_table"][0]["cycle_enabled"] is True
        assert device_data["outlet_table"][0]["outlet_power"] == 150.0
        assert len(device_data["outlet_overrides"]) == 1

    def test_merge_legacy_outlet_data_edge_cases(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test _merge_legacy_outlet_data edge cases."""
        # 1. Missing MAC in device_dict
        dev: dict[str, Any] = {"name": "No MAC"}
        coordinator._merge_legacy_outlet_data(
            dev, {"aa:bb:cc:dd:ee:ff": {"outlet_table": [{"index": 1}]}}
        )
        assert "outlet_table" not in dev

        # 2. Legacy device not in legacy_devices_by_mac
        dev = {"mac": "AA:BB:CC:DD:EE:FF"}
        coordinator._merge_legacy_outlet_data(dev, {})
        assert "outlet_table" not in dev

        # 3. Legacy device has no outlets and no power totals
        dev = {"mac": "AA:BB:CC:DD:EE:FF"}
        coordinator._merge_legacy_outlet_data(
            dev, {"aa:bb:cc:dd:ee:ff": {"name": "Empty"}}
        )
        assert "outlet_table" not in dev

        # 4. Legacy device has only ac_power_consumption
        dev = {"mac": "AA:BB:CC:DD:EE:FF"}
        coordinator._merge_legacy_outlet_data(
            dev, {"aa:bb:cc:dd:ee:ff": {"outlet_ac_power_consumption": "50.5"}}
        )
        assert dev["ac_power_consumption"] == 50.5
        assert "outlet_table" not in dev

        # 5. Legacy device has only ac_power_budget
        dev = {"mac": "AA:BB:CC:DD:EE:FF"}
        coordinator._merge_legacy_outlet_data(
            dev, {"aa:bb:cc:dd:ee:ff": {"outlet_ac_power_budget": "100.0"}}
        )
        assert dev["ac_power_budget"] == 100.0

    @pytest.mark.asyncio
    async def test_async_update_data_legacy_temperature_failure_is_ignored(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test legacy temperature fetch failure does not drop device data."""
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            side_effect=Exception("Legacy endpoint unavailable")
        )

        result = await coordinator._async_update_data()

        assert "device1" in result["devices"]["default"]
        assert "generalTemperature" not in result["devices"]["default"]["device1"]

    @pytest.mark.asyncio
    async def test_async_update_data_no_sites(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test data fetch with no sites available."""
        coordinator.config_coordinator.data["sites"] = {}

        result = await coordinator._async_update_data()

        # Should return existing data without changes
        assert result == coordinator.data
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_prunes_sites_no_longer_listed(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Data for a site no longer listed is dropped, and its devices go stale."""
        for key in ("devices", "stats", "clients"):
            coordinator.data[key]["gone"] = {"dev-gone": {"id": "dev-gone"}}
        coordinator._previous_network_device_ids = {"gone_dev-gone"}

        with patch.object(coordinator, "_cleanup_stale_devices") as cleanup:
            await coordinator._async_update_data()

        for key in ("devices", "stats", "clients"):
            assert "gone" not in coordinator.data[key]
            assert "default" in coordinator.data[key]
        cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_update_data_no_sites_clears_data_keeps_registry(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """An empty site list clears stale data but never purges registry devices."""
        for key in ("devices", "stats", "clients"):
            coordinator.data[key]["default"] = {"device1": {"id": "device1"}}
        coordinator._previous_network_device_ids = {"default_device1"}
        coordinator.config_coordinator.data["sites"] = {}

        with patch.object(coordinator, "_cleanup_stale_devices") as cleanup:
            await coordinator._async_update_data()

        for key in ("devices", "stats", "clients"):
            assert coordinator.data[key] == {}
        # An empty list is also what a transient API failure looks like.
        cleanup.assert_not_called()
        assert coordinator._previous_network_device_ids == {"default_device1"}

    @pytest.mark.asyncio
    async def test_process_device_stats_error(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test device processing with stats error."""
        coordinator.network_client.devices.get_statistics = AsyncMock(
            side_effect=Exception("Stats failed")
        )

        result = await coordinator._async_update_data()

        # Devices should still be fetched, just without stats
        assert "devices" in result
        assert "default" in result["devices"]

    @pytest.mark.asyncio
    async def test_mac_keyed_device_skips_statistics_keeps_legacy_metrics(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """A device keyed on its MAC has no controller id to fetch stats by (#128)."""
        mac = "e0:63:da:00:00:01"
        coordinator.network_client.devices.get_all = AsyncMock(
            return_value=[
                _create_mock_model(
                    {"id": mac, "macAddress": mac, "name": "AC Mesh", "state": "ONLINE"}
                )
            ]
        )
        coordinator.network_client.devices.get_port_metrics = AsyncMock(
            return_value=LegacyPortMetrics(
                port_bytes={1: PortBytesMetrics(rx_bytes=10, tx_bytes=20)}
            )
        )

        result = await coordinator._async_update_data()

        coordinator.network_client.devices.get_statistics.assert_not_awaited()
        coordinator.network_client.devices.get_port_metrics.assert_awaited_once()
        assert mac in result["devices"]["default"]
        stats = result["stats"]["default"][mac]
        assert stats["port_bytes"] == {1: {"rx_bytes": 10, "tx_bytes": 20}}
        assert stats["id"] == mac

    @pytest.mark.parametrize("endpoint", ["devices.get_all", "clients.get_all"])
    @pytest.mark.parametrize(
        "error",
        [
            UniFiConnectionError("Connection refused"),
            UniFiTimeoutError("Request timed out"),
            UniFiResponseError("Server error", status_code=502),
            UniFiResponseError("Bad response", status_code=400),
            Exception("Something broke"),
        ],
        ids=["connection", "timeout", "502", "400", "unexpected"],
    )
    async def test_site_failure_fails_refresh_and_keeps_snapshot(
        self,
        coordinator: UnifiInsightsDeviceCoordinator,
        endpoint: str,
        error: Exception,
    ):
        """A site that cannot be fetched fails the refresh; old data is kept."""
        await coordinator._async_update_data()
        previous_devices = copy.deepcopy(coordinator.data["devices"]["default"])
        previous_stats = copy.deepcopy(coordinator.data["stats"]["default"])
        assert previous_devices

        namespace, method = endpoint.split(".")
        setattr(
            getattr(coordinator.network_client, namespace),
            method,
            AsyncMock(side_effect=error),
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

        assert coordinator._available is False
        assert coordinator.data["devices"]["default"] == previous_devices
        assert coordinator.data["stats"]["default"] == previous_stats

    @pytest.mark.asyncio
    async def test_site_auth_failure_starts_reauth(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """A revoked key on the devices endpoint raises ConfigEntryAuthFailed."""
        coordinator.network_client.devices.get_all = AsyncMock(
            side_effect=UniFiAuthenticationError("Invalid API key", status_code=401)
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

        assert coordinator._available is False

    @pytest.mark.asyncio
    async def test_one_failed_site_fails_refresh_without_partial_write(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """With several sites, one failure fails the refresh and writes nothing."""
        coordinator.config_coordinator.data["sites"]["site2"] = {"id": "site2"}
        original_get_all = coordinator.network_client.devices.get_all

        async def get_all(site_id: str):
            if site_id == "site2":
                msg = "site2 unreachable"
                raise UniFiConnectionError(msg)
            return await original_get_all(site_id)

        coordinator.network_client.devices.get_all = AsyncMock(side_effect=get_all)

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

        assert coordinator.data["devices"] == {}
        assert coordinator.data["stats"] == {}

    def test_network_facts_start_empty(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """The facts map exists before the first refresh, so the facade finds it."""
        assert coordinator.data[NETWORK_FACTS_KEY] == {}

    @pytest.mark.asyncio
    async def test_network_facts_published_and_replaced_each_refresh(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Each successful refresh publishes a new facts map from final data."""
        await coordinator._async_update_data()
        first = coordinator.data[NETWORK_FACTS_KEY]
        site = first["default"]

        assert isinstance(site, SiteNetworkFacts)
        assert site.devices["device1"].cpu_pct == 15.2
        assert site.devices["device1"].uptime_s == 864000

        await coordinator._async_update_data()
        second = coordinator.data[NETWORK_FACTS_KEY]

        assert second is not first
        assert second["default"] is not site
        assert second["default"] == site

    @pytest.mark.asyncio
    async def test_network_facts_kept_on_site_failure(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """A failed refresh keeps the last committed facts of retained sites."""
        await coordinator._async_update_data()
        committed = coordinator.data[NETWORK_FACTS_KEY]["default"]
        coordinator.network_client.devices.get_all = AsyncMock(
            side_effect=UniFiConnectionError("offline")
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

        assert coordinator.data[NETWORK_FACTS_KEY]["default"] is committed

    @pytest.mark.asyncio
    async def test_network_facts_pruned_with_their_site(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Facts of a site no longer polled are dropped by replacement."""
        stale = SiteNetworkFacts(gateway_id=None, devices={})
        facts_map = {"gone": stale}
        coordinator.data[NETWORK_FACTS_KEY] = facts_map

        await coordinator._async_update_data()

        assert "gone" not in coordinator.data[NETWORK_FACTS_KEY]
        assert "default" in coordinator.data[NETWORK_FACTS_KEY]
        assert facts_map == {"gone": stale}

    @pytest.mark.asyncio
    async def test_network_facts_cleared_without_sites(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """An empty site list leaves an empty (new) facts map."""
        await coordinator._async_update_data()
        previous = coordinator.data[NETWORK_FACTS_KEY]
        coordinator.config_coordinator.data["sites"] = {}

        await coordinator._async_update_data()

        assert coordinator.data[NETWORK_FACTS_KEY] == {}
        assert coordinator.data[NETWORK_FACTS_KEY] is not previous
        assert "default" in previous

    @pytest.mark.asyncio
    async def test_network_facts_reflect_legacy_primary_ports(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """With the v1 list failing, facts carry the legacy device's ports."""
        coordinator.network_client.devices.get_all = AsyncMock(
            side_effect=UniFiResponseError("Internal Server Error", status_code=500)
        )
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            return_value=[
                {
                    "_id": "60a1b2c3d4e5f67890123456",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "name": "Legacy Gateway",
                    "model": "UDMPRO",
                    "type": "udm",
                    "up": True,
                    "port_table": [
                        {"port_idx": 1, "up": True, "speed": 1000},
                        {"port_idx": 9, "up": True, "media": "SFP+"},
                    ],
                }
            ]
        )

        await coordinator._async_update_data()

        site = coordinator.data[NETWORK_FACTS_KEY]["default"]
        facts = site.devices["60a1b2c3d4e5f67890123456"]
        assert site.gateway_id == "60a1b2c3d4e5f67890123456"
        assert facts.online_state == "online"
        assert [port.index for port in facts.ports] == [1, 9]

    @pytest.mark.asyncio
    async def test_network_facts_have_no_ports_when_legacy_fails(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Ports need legacy data: the v1 overview alone yields none."""
        coordinator.network_client.devices.get_all = AsyncMock(
            return_value=[_create_mock_model(dict(SAMPLE_UDM_PRO_OVERVIEW))]
        )
        coordinator.network_client.devices.get_statistics = AsyncMock(
            return_value=_create_mock_model(dict(SAMPLE_UDM_PRO_STATS))
        )
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            side_effect=UniFiConnectionError("classic API down")
        )

        await coordinator._async_update_data()

        site = coordinator.data[NETWORK_FACTS_KEY]["default"]
        facts = site.devices[SAMPLE_UDM_PRO_OVERVIEW["id"]]
        assert isinstance(SAMPLE_UDM_PRO_OVERVIEW["interfaces"], list)
        assert facts.ports == ()
        assert facts.kind == "gateway"
        assert site.gateway_id == SAMPLE_UDM_PRO_OVERVIEW["id"]
        assert (facts.tx_bps, facts.rx_bps) == (35072.0, 74016.0)

    @pytest.mark.asyncio
    async def test_auth_failure_wins_over_other_site_failures(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """If any site reports revoked credentials, reauth is triggered."""
        coordinator.config_coordinator.data["sites"]["site2"] = {"id": "site2"}

        async def get_all(site_id: str):
            if site_id == "site2":
                msg = "Revoked"
                raise UniFiAuthenticationError(msg, status_code=401)
            msg = "default unreachable"
            raise UniFiConnectionError(msg)

        coordinator.network_client.devices.get_all = AsyncMock(side_effect=get_all)

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_stats_auth_failure_starts_reauth(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Revoked credentials surfacing on the stats endpoint start reauth."""
        coordinator.network_client.devices.get_statistics = AsyncMock(
            side_effect=UniFiAuthenticationError("Revoked", status_code=401)
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            UniFiConnectionError("Connection lost"),
            UniFiTimeoutError("Timed out"),
            UniFiResponseError("Server error", status_code=500),
            UniFiRateLimitError("Slow down", status_code=429),
        ],
        ids=["connection", "timeout", "500", "429"],
    )
    async def test_stats_transient_failure_keeps_previous_stats(
        self, coordinator: UnifiInsightsDeviceCoordinator, error: Exception
    ):
        """One device's stats failing keeps its last stats, not the refresh."""
        await coordinator._async_update_data()
        previous = copy.deepcopy(coordinator.data["stats"]["default"]["device1"])
        assert previous

        coordinator.network_client.devices.get_statistics = AsyncMock(side_effect=error)
        result = await coordinator._async_update_data()

        assert coordinator._available is True
        assert result["stats"]["default"]["device1"] == previous

    @pytest.mark.asyncio
    async def test_refresh_without_legacy_site_mapping(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Sites still refresh when no classic site mapping is published."""
        # What the config coordinator publishes after a first failed lookup.
        coordinator.config_coordinator.data["legacy_site_names"] = {}
        coordinator.config_coordinator.data["legacy_mapping_failed"] = True
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock()

        result = await coordinator._async_update_data()

        assert "device1" in result["devices"]["default"]
        coordinator.network_client.devices.get_legacy_site_devices.assert_not_called()
        coordinator.network_client.sites.get_legacy_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_reads_the_published_legacy_site_mapping(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """The device poll uses the config mapping and never looks it up itself."""
        coordinator.config_coordinator.data["legacy_site_names"] = {
            "default": "renamed"
        }
        coordinator.config_coordinator.data["legacy_mapping_failed"] = True

        await coordinator._async_update_data()

        coordinator.network_client.sites.get_legacy_all.assert_not_called()
        coordinator.network_client.devices.get_legacy_site_devices.assert_awaited_with(
            "renamed"
        )

    @pytest.mark.asyncio
    async def test_stats_reuse_is_capped(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """A device whose stats keep failing stops reusing them after the cap."""
        await coordinator._async_update_data()
        assert coordinator.data["stats"]["default"]["device1"]
        coordinator.network_client.devices.get_statistics = AsyncMock(
            side_effect=UniFiTimeoutError("Timed out")
        )

        for _ in range(MAX_STATS_REUSE_POLLS):
            result = await coordinator._async_update_data()
            assert result["stats"]["default"]["device1"]

        result = await coordinator._async_update_data()
        assert result["stats"]["default"]["device1"] == {}

    @pytest.mark.asyncio
    async def test_stats_failure_count_resets(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Unsupported responses and vanished devices clear the failure count."""
        await coordinator._async_update_data()
        client = coordinator.network_client
        client.devices.get_statistics = AsyncMock(
            side_effect=UniFiTimeoutError("Timed out")
        )
        await coordinator._async_update_data()
        assert coordinator._stats_failures == {"device1": 1}

        client.devices.get_statistics = AsyncMock(
            side_effect=UniFiNotFoundError("Not found", status_code=404)
        )
        await coordinator._async_update_data()
        assert coordinator._stats_failures == {}

        coordinator._stats_failures["gone-device"] = 2
        await coordinator._async_update_data()
        assert "gone-device" not in coordinator._stats_failures

    @pytest.mark.asyncio
    async def test_port_rates_skip_reused_stats(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Reused stats don't produce a 0 rate or a doubled rate on recovery."""
        counters = {"bytes": 0}

        async def port_metrics(*_args, **_kwargs):
            return MagicMock(
                port_bytes={
                    1: MagicMock(rx_bytes=counters["bytes"], tx_bytes=counters["bytes"])
                },
                poe_ports={},
                poe_total_w=None,
            )

        coordinator.network_client.devices.get_port_metrics = AsyncMock(
            side_effect=port_metrics
        )
        coordinator.data["devices"] = {}
        working_stats = coordinator.network_client.devices.get_statistics
        clock = {"now": 1000.0}

        def rate() -> float:
            return coordinator.data["stats"]["default"]["device1"]["port_rates"][1][
                "rx_bytes_rate"
            ]

        with patch(
            "custom_components.unifi_insights.coordinators.device.time.monotonic",
            side_effect=lambda: clock["now"],
        ):
            await coordinator._async_update_data()  # first sample
            await coordinator._async_update_data()  # no time elapsed: no rate
            assert "port_rates" not in coordinator.data["stats"]["default"]["device1"]

            clock["now"] += 10
            counters["bytes"] += 1000
            await coordinator._async_update_data()
            assert rate() == 100.0

            clock["now"] += 10
            counters["bytes"] += 1000
            coordinator.network_client.devices.get_statistics = AsyncMock(
                side_effect=UniFiTimeoutError("Timed out")
            )
            await coordinator._async_update_data()
            assert rate() == 100.0  # last real rate kept, not 0

            clock["now"] += 10
            counters["bytes"] += 1000
            coordinator.network_client.devices.get_statistics = working_stats
            await coordinator._async_update_data()
            assert rate() == 100.0  # 2000 bytes over 20 s, not 200

    @pytest.mark.asyncio
    async def test_site_forbidden_fails_refresh_without_reauth(
        self,
        coordinator: UnifiInsightsDeviceCoordinator,
        caplog: pytest.LogCaptureFixture,
    ):
        """A 403 on devices fails the refresh but does not start a reauth loop."""
        coordinator.network_client.devices.get_all = AsyncMock(
            side_effect=UniFiAuthenticationError("Forbidden", status_code=403)
        )

        with pytest.raises(UpdateFailed, match=r"^Access forbidden for site"):
            await coordinator._async_update_data()

        assert coordinator._available is False
        # Not re-wrapped and logged as an unexpected error on every poll.
        assert "Unexpected error" not in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            UniFiAuthenticationError("Forbidden", status_code=403),
            UniFiNotFoundError("Not found", status_code=404),
        ],
        ids=["403", "404"],
    )
    async def test_stats_unavailable_for_one_device_is_tolerated(
        self, coordinator: UnifiInsightsDeviceCoordinator, error: Exception
    ):
        """A per-device stats error leaves that device without stats only."""
        coordinator.network_client.devices.get_statistics = AsyncMock(side_effect=error)

        result = await coordinator._async_update_data()

        assert "device1" in result["devices"]["default"]
        assert result["stats"]["default"]["device1"] == {}
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_refresh_recovers_after_failure(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """last_update_success goes False on failure and True again on recovery."""
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True

        working = coordinator.network_client.devices.get_all
        coordinator.network_client.devices.get_all = AsyncMock(
            side_effect=UniFiConnectionError("Console rebooting")
        )
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False
        assert coordinator.available is False
        assert "device1" in coordinator.data["devices"]["default"]

        coordinator.network_client.devices.get_all = working
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.available is True

    def test_get_device_existing(self, coordinator: UnifiInsightsDeviceCoordinator):
        """Test getting existing device."""
        coordinator.data["devices"] = {
            "default": {"device1": {"id": "device1", "name": "Switch"}}
        }
        result = coordinator.get_device("default", "device1")
        assert result == {"id": "device1", "name": "Switch"}

    def test_get_device_missing(self, coordinator: UnifiInsightsDeviceCoordinator):
        """Test getting missing device."""
        result = coordinator.get_device("default", "nonexistent")
        assert result is None

    def test_get_device_stats_existing(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test getting existing device stats."""
        coordinator.data["stats"] = {"default": {"device1": {"cpu": 15.2, "mem": 42.8}}}
        result = coordinator.get_device_stats("default", "device1")
        assert result == {"cpu": 15.2, "mem": 42.8}

    def test_get_device_stats_missing(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test getting missing device stats."""
        result = coordinator.get_device_stats("default", "nonexistent")
        assert result is None

    def test_get_clients(self, coordinator: UnifiInsightsDeviceCoordinator):
        """Test getting clients for a site."""
        coordinator.data["clients"] = {
            "default": {"client1": {"id": "client1", "name": "iPhone"}}
        }
        result = coordinator.get_clients("default")
        assert "client1" in result

    def test_get_clients_missing_site(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test getting clients for missing site."""
        result = coordinator.get_clients("nonexistent")
        assert result == {}

    @pytest.mark.asyncio
    async def test_cleanup_stale_devices(
        self, hass: HomeAssistant, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test stale device cleanup."""
        # Set up previous device IDs
        coordinator._previous_network_device_ids = {
            "default_device1",
            "default_device2",
        }

        # Current devices only have device1
        coordinator.data["devices"] = {"default": {"device1": {"id": "device1"}}}

        # Mock device registry
        with patch(
            "custom_components.unifi_insights.coordinators.base.dr.async_get"
        ) as mock_registry:
            mock_device = MagicMock()
            mock_device.id = "device_entry_id"
            set_mock_device_lookup(mock_registry.return_value, mock_device)

            coordinator._cleanup_stale_devices()

            # device2 should be marked for removal
            mock_registry.return_value.async_update_device.assert_called()

    @pytest.mark.asyncio
    async def test_process_site_empty_devices(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test site processing with empty devices list."""
        # Return empty list of devices
        coordinator.network_client.devices.get_all = AsyncMock(return_value=[])

        result = await coordinator._async_update_data()

        # Should handle empty devices gracefully
        assert result is not None
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_cleanup_stale_devices_no_registry_match(
        self, hass: HomeAssistant, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test cleanup when stale device is not found in registry."""
        # Set up previous device IDs
        coordinator._previous_network_device_ids = {"default_stale_device"}

        # Current devices are empty
        coordinator.data["devices"] = {"default": {}}

        # Mock device registry to return None for the device lookup
        with patch(
            "custom_components.unifi_insights.coordinators.base.dr.async_get"
        ) as mock_registry:
            set_mock_device_lookup(mock_registry.return_value, None)

            # Should not raise even when device not in registry
            coordinator._cleanup_stale_devices()

            # async_update_device should not be called since device wasn't found
            mock_registry.return_value.async_update_device.assert_not_called()

    def test_get_device_non_dict_value(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test get_device returns None for non-dict values."""
        coordinator.data["devices"] = {"default": {"device1": "not_a_dict"}}

        result = coordinator.get_device("default", "device1")
        assert result is None

    def test_get_device_stats_non_dict_value(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test get_device_stats returns None for non-dict values."""
        coordinator.data["stats"] = {"default": {"device1": "not_a_dict"}}

        result = coordinator.get_device_stats("default", "device1")
        assert result is None

    # -------------------------------------------------------------------
    # Top-level error handler tests
    # These test the defensive error handlers in _async_update_data (lines 214-223).
    # In normal operation, these can't be reached because _process_site catches
    # all exceptions. We test them by making config_coordinator.get_site_ids raise.
    # -------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_async_update_top_level_auth_error(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test top-level auth error handling in _async_update_data."""
        # Make get_site_ids raise to trigger top-level handler
        coordinator.config_coordinator.get_site_ids = MagicMock(
            side_effect=UniFiAuthenticationError("Token expired")
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

        assert coordinator._available is False

    @pytest.mark.asyncio
    async def test_async_update_top_level_connection_error(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test top-level connection error handling in _async_update_data."""
        coordinator.config_coordinator.get_site_ids = MagicMock(
            side_effect=UniFiConnectionError("Network unreachable")
        )

        with pytest.raises(UpdateFailed) as exc_info:
            await coordinator._async_update_data()

        assert "Error communicating" in str(exc_info.value)
        assert coordinator._available is False

    @pytest.mark.asyncio
    async def test_async_update_top_level_timeout_error(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test top-level timeout error handling in _async_update_data."""
        coordinator.config_coordinator.get_site_ids = MagicMock(
            side_effect=UniFiTimeoutError("Request timeout")
        )

        with pytest.raises(UpdateFailed) as exc_info:
            await coordinator._async_update_data()

        assert "Timeout" in str(exc_info.value)
        assert coordinator._available is False

    @pytest.mark.asyncio
    async def test_async_update_top_level_response_error(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test top-level response error handling in _async_update_data."""
        coordinator.config_coordinator.get_site_ids = MagicMock(
            side_effect=UniFiResponseError("Bad gateway", status_code=502)
        )

        with pytest.raises(UpdateFailed) as exc_info:
            await coordinator._async_update_data()

        assert "API error" in str(exc_info.value)
        assert coordinator._available is False

    @pytest.mark.asyncio
    async def test_async_update_top_level_generic_error(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test top-level generic error handling in _async_update_data."""
        coordinator.config_coordinator.get_site_ids = MagicMock(
            side_effect=RuntimeError("Unexpected failure")
        )

        with pytest.raises(UpdateFailed) as exc_info:
            await coordinator._async_update_data()

        assert "Error updating data" in str(exc_info.value)
        assert coordinator._available is False

    @pytest.mark.asyncio
    async def test_async_update_data_stores_vpn_connections(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Live VPN connections are stored per site, keyed by network_id."""
        coordinator.network_client.vpn_clients.list_vpn_connections = AsyncMock(
            return_value=[
                {"network_id": "tun1", "type": "ipsec-vpn", "status": "CONNECTED"},
                {"network_id": "cli1", "type": "openvpn-client", "status": "CONNECTED"},
            ]
        )

        result = await coordinator._async_update_data()

        assert result["vpn_connections"]["default"] == {
            "tun1": {"network_id": "tun1", "type": "ipsec-vpn", "status": "CONNECTED"},
            "cli1": {
                "network_id": "cli1",
                "type": "openvpn-client",
                "status": "CONNECTED",
            },
        }
        coordinator.network_client.vpn_clients.list_vpn_connections.assert_awaited_once_with(
            "default"
        )

    @pytest.mark.asyncio
    async def test_async_update_data_vpn_connections_failure_is_optional(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """A failing connections call never fails the device refresh."""
        coordinator.network_client.vpn_clients.list_vpn_connections = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        result = await coordinator._async_update_data()

        # No previous value to reuse, so the site's state is unknown.
        assert "default" not in result["vpn_connections"]
        assert result["devices"]["default"]

    @pytest.mark.asyncio
    async def test_vpn_connections_reused_for_bounded_polls_then_dropped(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """A failing call reuses the last good connections for a few polls."""
        good = {"network_id": "tun1", "type": "ipsec-vpn", "status": "CONNECTED"}
        coordinator.network_client.vpn_clients.list_vpn_connections = AsyncMock(
            return_value=[good]
        )
        await coordinator._async_update_data()

        coordinator.network_client.vpn_clients.list_vpn_connections = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        for _ in range(MAX_STATS_REUSE_POLLS):
            result = await coordinator._async_update_data()
            assert result["vpn_connections"]["default"] == {"tun1": good}

        result = await coordinator._async_update_data()
        assert "default" not in result["vpn_connections"]

        # A success resets the failure count.
        coordinator.network_client.vpn_clients.list_vpn_connections = AsyncMock(
            return_value=[]
        )
        result = await coordinator._async_update_data()
        assert result["vpn_connections"]["default"] == {}
        assert coordinator._vpn_connection_failures == {}

    @pytest.mark.asyncio
    async def test_vpn_connections_dropped_for_sites_no_longer_polled(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """VPN state of a site that is no longer polled is purged."""
        coordinator.data["vpn_connections"]["gone"] = {"tun1": {"status": "CONNECTED"}}
        coordinator._vpn_connection_failures["gone"] = 1

        result = await coordinator._async_update_data()

        assert "gone" not in result["vpn_connections"]
        assert "gone" not in coordinator._vpn_connection_failures

    @pytest.mark.asyncio
    async def test_legacy_primary_devices_still_get_wans(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """WAN links are merged when legacy devices replace a failing v1 list."""
        coordinator.network_client.devices.get_all = AsyncMock(
            side_effect=UniFiResponseError("Internal Server Error", status_code=500)
        )
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            return_value=[
                {
                    "_id": "60a1b2c3d4e5f67890123456",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "name": "Legacy Gateway",
                    "model": "UCG-Ultra",
                    "type": "ugw",
                    "up": True,
                    "last_wan_status": {"WAN": "online"},
                }
            ]
        )

        result = await coordinator._async_update_data()

        (device,) = result["devices"]["default"].values()
        assert device["wans"][0]["key"] == "wan"
        assert device["wans"][0]["connected"] is True

    @pytest.mark.asyncio
    async def test_fetch_vpn_connections_without_legacy_site(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Without a legacy site name the state is unknown, not "no VPNs"."""
        coordinator.network_client.vpn_clients.list_vpn_connections = AsyncMock()

        assert await coordinator._fetch_vpn_connections("default", None) is None
        coordinator.network_client.vpn_clients.list_vpn_connections.assert_not_awaited()

    def test_merge_legacy_wan_data(self, coordinator: UnifiInsightsDeviceCoordinator):
        """The gateway's per-WAN connection state is merged by MAC."""
        device_dict: dict[str, Any] = {"macAddress": "AA:BB:CC:DD:EE:FF"}
        legacy_devices_by_mac: dict[str, dict[str, Any]] = {
            "aa:bb:cc:dd:ee:ff": {
                "wan1": {"type": "ethernet", "name": "eth8", "up": True},
                "last_wan_status": {"WAN": "online"},
                "last_wan_interfaces": {"WAN": {"ip": "198.51.100.7", "alive": True}},
            }
        }

        UnifiInsightsDeviceCoordinator._merge_legacy_wan_data(
            device_dict, legacy_devices_by_mac
        )

        assert [wan["key"] for wan in device_dict["wans"]] == ["wan"]
        assert device_dict["wans"][0]["connected"] is True
        assert device_dict["wans"][0]["name"] == "WAN"

    def test_merge_legacy_wan_data_without_wan_blocks(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Devices without WAN blocks, or without legacy data, are untouched."""
        switch: dict[str, Any] = {"macAddress": "AA:BB:CC:DD:EE:FF"}
        unmatched: dict[str, Any] = {"macAddress": "11:22:33:44:55:66"}
        no_mac: dict[str, Any] = {}
        legacy_devices_by_mac: dict[str, dict[str, Any]] = {
            "aa:bb:cc:dd:ee:ff": {"port_table": []}
        }

        for device_dict in (switch, unmatched, no_mac):
            UnifiInsightsDeviceCoordinator._merge_legacy_wan_data(
                device_dict, legacy_devices_by_mac
            )
            assert "wans" not in device_dict

    @pytest.mark.asyncio
    async def test_async_update_data_merges_legacy_wans(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Legacy WAN link state reaches the gateway device through a refresh."""
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            return_value=[
                {
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "last_wan_status": {"WAN": "online"},
                }
            ]
        )

        result = await coordinator._async_update_data()

        wans = result["devices"]["default"]["device1"]["wans"]
        assert wans[0]["key"] == "wan"
        assert wans[0]["connected"] is True

    @pytest.mark.asyncio
    async def test_wans_reused_for_bounded_polls_then_dropped(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """A failing legacy call keeps the last WAN links for a few polls."""
        # Real models dump a fresh dict per poll; the shared fixture dict
        # would otherwise carry reused WAN links into later polls.
        for model in coordinator.network_client.devices.get_all.return_value:
            dumped = model.model_dump.return_value
            model.model_dump = MagicMock(
                side_effect=lambda *_a, d=dumped, **_k: dict(d)
            )
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            return_value=[
                {
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "last_wan_status": {"WAN": "online"},
                }
            ]
        )
        result = await coordinator._async_update_data()
        good = result["devices"]["default"]["device1"]["wans"]

        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        for _ in range(MAX_STATS_REUSE_POLLS):
            result = await coordinator._async_update_data()
            assert result["devices"]["default"]["device1"]["wans"] == good

        result = await coordinator._async_update_data()
        assert "wans" not in result["devices"]["default"]["device1"]

        # A success resets the failure count.
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            return_value=[]
        )
        await coordinator._async_update_data()
        assert coordinator._legacy_wan_failures == {}

    @pytest.mark.asyncio
    async def test_legacy_wan_failures_dropped_for_sites_no_longer_polled(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """The WAN failure count of a site that is no longer polled is purged."""
        coordinator._legacy_wan_failures["gone"] = 1

        await coordinator._async_update_data()

        assert "gone" not in coordinator._legacy_wan_failures

    def test_merge_legacy_storage_data(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ) -> None:
        """The console's per-mount storage data is merged by MAC."""
        device_dict: dict[str, Any] = {"macAddress": "AA:BB:CC:DD:EE:FF"}
        legacy_devices_by_mac: dict[str, dict[str, Any]] = {
            "aa:bb:cc:dd:ee:ff": {
                "storage": [
                    {
                        "mount_point": "/data",
                        "name": "eMMC",
                        "type": "eMMC",
                        "size": 4143677440,
                        "used": 1700257792,
                    }
                ]
            }
        }

        UnifiInsightsDeviceCoordinator._merge_legacy_storage_data(
            device_dict, legacy_devices_by_mac
        )

        assert device_dict["storage_mounts"] == [
            {
                "mount_point": "/data",
                "name": "eMMC",
                "type": "eMMC",
                "size": 4143677440,
                "used": 1700257792,
            }
        ]

    def test_merge_legacy_storage_data_leaves_other_devices_untouched(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ) -> None:
        """Devices without storage, or without legacy data, are untouched."""
        switch: dict[str, Any] = {"macAddress": "AA:BB:CC:DD:EE:FF"}
        empty_storage: dict[str, Any] = {"macAddress": "AA:BB:CC:DD:EE:FF"}
        unmatched: dict[str, Any] = {"macAddress": "11:22:33:44:55:66"}
        no_mac: dict[str, Any] = {}
        legacy_devices_by_mac: dict[str, dict[str, Any]] = {
            "aa:bb:cc:dd:ee:ff": {"port_table": []}
        }

        UnifiInsightsDeviceCoordinator._merge_legacy_storage_data(
            switch, legacy_devices_by_mac
        )
        assert "storage_mounts" not in switch

        legacy_devices_by_mac_empty: dict[str, dict[str, Any]] = {
            "aa:bb:cc:dd:ee:ff": {"storage": []}
        }
        UnifiInsightsDeviceCoordinator._merge_legacy_storage_data(
            empty_storage, legacy_devices_by_mac_empty
        )
        assert "storage_mounts" not in empty_storage

        for device_dict in (unmatched, no_mac):
            UnifiInsightsDeviceCoordinator._merge_legacy_storage_data(
                device_dict, legacy_devices_by_mac
            )
            assert "storage_mounts" not in device_dict

    @pytest.mark.asyncio
    async def test_async_update_data_merges_legacy_storage(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ) -> None:
        """Legacy storage reaches console through refresh with zero extra requests."""
        udm_storage = [
            {
                "mount_point": "/data",
                "name": "eMMC",
                "type": "eMMC",
                "size": 4143677440,
                "used": 1700257792,
            },
            {
                "mount_point": "/persistent",
                "name": "Backup",
                "type": "eMMC",
                "size": 2046640128,
                "used": 318767104,
            },
            {
                "mount_point": "/" + "tmp",
                "name": "Temporary",
                "type": "other",
                "size": 1073741824,
                "used": 1748992,
            },
        ]
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            return_value=[
                {
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "storage": udm_storage,
                }
            ]
        )

        result = await coordinator._async_update_data()

        mounts = result["devices"]["default"]["device1"]["storage_mounts"]
        assert mounts == udm_storage
        assert (
            coordinator.network_client.devices.get_legacy_site_devices.await_count == 1
        )

    @pytest.mark.asyncio
    async def test_legacy_primary_devices_still_get_storage_mounts(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ) -> None:
        """Storage mounts are merged when legacy devices replace a failing v1 list."""
        coordinator.network_client.devices.get_all = AsyncMock(
            side_effect=UniFiResponseError("Internal Server Error", status_code=500)
        )
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            return_value=[
                {
                    "_id": "60a1b2c3d4e5f67890123456",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "name": "Legacy Gateway",
                    "model": "UDMPROSE",
                    "type": "udm",
                    "up": True,
                    "storage": [
                        {
                            "mount_point": "/data",
                            "name": "eMMC",
                            "type": "eMMC",
                            "size": 4143677440,
                            "used": 1700257792,
                        }
                    ],
                }
            ]
        )

        result = await coordinator._async_update_data()

        (device,) = result["devices"]["default"].values()
        assert "storage_mounts" in device
        assert device["storage_mounts"][0]["mount_point"] == "/data"

    @pytest.mark.asyncio
    async def test_storage_mounts_reused_for_bounded_polls_then_dropped(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ) -> None:
        """A failing legacy call keeps the last storage mounts for a few polls."""
        for model in coordinator.network_client.devices.get_all.return_value:
            dumped = model.model_dump.return_value
            model.model_dump = MagicMock(
                side_effect=lambda *_a, d=dumped, **_k: dict(d)
            )
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            return_value=[
                {
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "storage": [
                        {
                            "mount_point": "/data",
                            "name": "eMMC",
                            "type": "eMMC",
                            "size": 4143677440,
                            "used": 1700257792,
                        }
                    ],
                }
            ]
        )
        result = await coordinator._async_update_data()
        good = result["devices"]["default"]["device1"]["storage_mounts"]

        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        for _ in range(MAX_STATS_REUSE_POLLS):
            result = await coordinator._async_update_data()
            assert result["devices"]["default"]["device1"]["storage_mounts"] == good

        result = await coordinator._async_update_data()
        assert "storage_mounts" not in result["devices"]["default"]["device1"]

        # A success resets the failure count.
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            return_value=[]
        )
        await coordinator._async_update_data()
        assert coordinator._legacy_wan_failures == {}

    def test_merge_legacy_port_data_includes_poe_good(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Test _merge_legacy_port_data passes through poe_good flag."""
        device_dict: dict[str, Any] = {"macAddress": "AA:BB:CC:DD:EE:FF"}
        legacy_devices_by_mac: dict[str, dict[str, Any]] = {
            "aa:bb:cc:dd:ee:ff": {
                "port_table": [
                    {
                        "port_idx": 1,
                        "up": True,
                        "port_poe": True,
                        "poe_enable": True,
                        "poe_power": "8.50",
                        "poe_good": True,
                    },
                    {
                        "port_idx": 2,
                        "up": True,
                        "port_poe": True,
                        "poe_enable": True,
                        "poe_power": "0.00",
                        "poe_good": False,
                    },
                    {
                        "port_idx": 3,
                        "up": True,
                        "port_poe": False,
                    },
                ]
            }
        }

        UnifiInsightsDeviceCoordinator._merge_legacy_port_data(
            device_dict, legacy_devices_by_mac
        )

        ports = device_dict.get("ports", [])
        assert len(ports) == 3

        port1 = next(p for p in ports if p["port_idx"] == 1)
        assert port1["poe"]["good"] is True
        assert port1["poe"]["enabled"] is True
        assert port1["poe"]["power"] == "8.50"

        port2 = next(p for p in ports if p["port_idx"] == 2)
        assert port2["poe"]["good"] is False
        assert port2["poe"]["enabled"] is True

        port3 = next(p for p in ports if p["port_idx"] == 3)
        assert "poe" not in port3

    def test_merge_legacy_port_data_skips_port_without_port_idx(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """A port_table entry without port_idx is dropped, not appended."""
        device_dict: dict[str, Any] = {"macAddress": "AA:BB:CC:DD:EE:FF"}
        legacy_devices_by_mac: dict[str, dict[str, Any]] = {
            "aa:bb:cc:dd:ee:ff": {
                "port_table": [
                    {"port_idx": 1, "up": True},
                    {"up": True, "name": "no port_idx"},
                ]
            }
        }

        UnifiInsightsDeviceCoordinator._merge_legacy_port_data(
            device_dict, legacy_devices_by_mac
        )

        ports = device_dict.get("ports", [])
        assert len(ports) == 1
        assert ports[0]["port_idx"] == 1

    def test_legacy_topology_block_reads_uplink(self):
        """The parent link and legacy type are copied from /stat/device."""
        block = UnifiInsightsDeviceCoordinator._legacy_topology_block(
            {
                "mac": "58:d6:1f:00:00:02",
                "type": "USW",
                "uplink": {
                    "uplink_mac": "28:70:4E:00:00:01",
                    "uplink_remote_port": 6,
                    "port_idx": 8,
                    "type": "wire",
                    "speed": 1000,
                    "ip": "10.1.0.105",
                    "mac": "58:d6:1f:00:00:02",
                },
            }
        )

        assert block == {
            "legacy_type": "usw",
            "uplink_mac": "28:70:4e:00:00:01",
            "uplink_remote_port": 6,
            "uplink_port_idx": 8,
            "uplink_type": "wire",
            "uplink_speed": 1000,
        }

    def test_legacy_topology_block_falls_back_to_last_uplink(self):
        """An offline device keeps its parent through last_uplink."""
        block = UnifiInsightsDeviceCoordinator._legacy_topology_block(
            {
                "type": "uap",
                "uplink": {"type": "wire"},
                "last_uplink": {
                    "uplink_mac": "28:70:4e:00:00:01",
                    "uplink_remote_port": 20,
                    "port_idx": 1,
                    "type": "wire",
                },
            }
        )

        assert block["uplink_mac"] == "28:70:4e:00:00:01"
        assert block["uplink_remote_port"] == 20
        assert block["uplink_port_idx"] == 1

    def test_legacy_topology_block_gateway_and_garbage(self):
        """A gateway has a type but no parent; malformed values are dropped."""
        assert UnifiInsightsDeviceCoordinator._legacy_topology_block(
            {"type": "udm", "uplink": {"type": "wire", "speed": 2500}}
        ) == {"legacy_type": "udm", "uplink_type": "wire", "uplink_speed": 2500}
        assert (
            UnifiInsightsDeviceCoordinator._legacy_topology_block(
                {
                    "type": "",
                    "uplink": {
                        "uplink_mac": 5,
                        "uplink_remote_port": True,
                        "speed": "fast",
                    },
                }
            )
            == {}
        )

    def test_merge_legacy_uplink_data_matches_by_mac(self):
        """The block lands on the v1 device whose MAC matches, case-insensitively."""
        device = {"id": "uuid-child", "macAddress": "58:D6:1F:00:00:02", "uplink": None}
        legacy = {
            "mac": "58:d6:1f:00:00:02",
            "type": "usw",
            "uplink": {"uplink_mac": "28:70:4e:00:00:01", "uplink_remote_port": 6},
        }

        UnifiInsightsDeviceCoordinator._merge_legacy_uplink_data(
            device, {"58:d6:1f:00:00:02": legacy}
        )

        assert device["topology"]["uplink_mac"] == "28:70:4e:00:00:01"
        # The v1 field is left alone.
        assert device["uplink"] is None

    def test_merge_legacy_uplink_data_without_match_is_noop(self):
        """No legacy record, or an empty block, adds nothing."""
        device = {"id": "uuid-x", "macAddress": "aa:bb:cc:dd:ee:ff"}
        UnifiInsightsDeviceCoordinator._merge_legacy_uplink_data(device, {})
        UnifiInsightsDeviceCoordinator._merge_legacy_uplink_data(
            device, {"aa:bb:cc:dd:ee:ff": {"mac": "aa:bb:cc:dd:ee:ff"}}
        )
        assert "topology" not in device

    def test_legacy_device_to_v1_dict_carries_topology(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """The legacy-only fallback produces the same block as the merge path."""
        mapped = coordinator._legacy_device_to_v1_dict(
            {
                "_id": "60a1b2c3d4e5f67890123456",
                "mac": "58:d6:1f:00:00:02",
                "type": "usw",
                "uplink": {"uplink_mac": "28:70:4e:00:00:01", "port_idx": 8},
            }
        )

        assert mapped["topology"] == {
            "legacy_type": "usw",
            "uplink_mac": "28:70:4e:00:00:01",
            "uplink_port_idx": 8,
        }

    @pytest.mark.asyncio
    async def test_process_site_merges_legacy_uplink(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """The real refresh path attaches the block to v1 devices."""
        coordinator.network_client.devices.get_all = AsyncMock(
            return_value=[
                {
                    "id": "uuid-child",
                    "name": "Ultra",
                    "model": "USW Ultra",
                    "macAddress": "58:d6:1f:00:00:02",
                    "state": "ONLINE",
                    "uplink": None,
                }
            ]
        )
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock(
            return_value=[
                {
                    "_id": "legacy-child",
                    "mac": "58:d6:1f:00:00:02",
                    "type": "usw",
                    "uplink": {
                        "uplink_mac": "28:70:4e:00:00:01",
                        "uplink_remote_port": 6,
                    },
                }
            ]
        )

        devices_dict, _stats, _clients = await coordinator._process_site(
            "default", legacy_site_name="default"
        )

        assert devices_dict["uuid-child"]["topology"] == {
            "legacy_type": "usw",
            "uplink_mac": "28:70:4e:00:00:01",
            "uplink_remote_port": 6,
        }

    @pytest.mark.asyncio
    async def test_process_device_filters_clients_without_index(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ) -> None:
        """Direct device callers retain filtering, order, and client identity."""
        coordinator.network_client.devices.get_statistics = AsyncMock(
            return_value=_create_mock_model({"uptimeSec": 60})
        )
        camel = {"id": "camel", "uplinkDeviceId": "device1"}
        snake = {"id": "snake", "uplink_device_id": "device1"}
        other = {"id": "other", "uplinkDeviceId": "device2"}
        fallback = {
            "id": "fallback",
            "uplinkDeviceId": "",
            "uplink_device_id": "device1",
        }
        camel_priority = {
            "id": "priority",
            "uplinkDeviceId": "device2",
            "uplink_device_id": "device1",
        }
        clients = [snake, other, camel, fallback, camel_priority, {"id": "unlinked"}]

        _, _, stats = await coordinator._process_device(
            "default", {"id": "device1"}, clients
        )

        assert stats["clients"] == [snake, camel, fallback]
        assert stats["clients"][0] is snake
        assert stats["clients"][1] is camel
        assert stats["clients"][2] is fallback

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stats_response", [None, {}], ids=["stats_none", "stats_empty"]
    )
    @pytest.mark.parametrize(
        "use_index", [False, True], ids=["scanning_path", "indexed_path"]
    )
    async def test_process_device_empty_stats_skips_client_assignment(
        self,
        coordinator: UnifiInsightsDeviceCoordinator,
        stats_response: Any,
        *,
        use_index: bool,
    ) -> None:
        """When device statistics are empty or None, no clients or id keys are added."""
        coordinator.network_client.devices.get_statistics = AsyncMock(
            return_value=stats_response
        )
        device = {"id": "device1", "name": "Device 1"}
        clients = [{"id": "client1", "uplinkDeviceId": "device1"}]
        clients_by_uplink = {"device1": clients} if use_index else None

        result = await coordinator._process_device(
            "default",
            device,
            clients,
            clients_by_uplink=clients_by_uplink,
        )

        assert result == ("device1", device, {})

    @pytest.mark.asyncio
    async def test_process_site_groups_clients_by_uplink(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ) -> None:
        """Site processing groups clients by uplink device in a single pass."""
        dev_a = {"id": "a", "name": "Device A"}
        dev_b = {"id": "b", "name": "Device B"}
        dev_empty = {"id": "empty", "name": "Device Empty"}

        coordinator.network_client.devices.get_all = AsyncMock(
            return_value=[
                _create_mock_model(dev_a),
                _create_mock_model(dev_b),
                _create_mock_model(dev_empty),
            ]
        )
        coordinator.network_client.devices.get_statistics = AsyncMock(
            side_effect=lambda site_id, device_id: _create_mock_model(
                {
                    "cpuUtilizationPct": 15.2,
                    "memoryUtilizationPct": 42.8,
                    "uptimeSec": 864000,
                }
            )
        )

        client_a1 = {"id": "a1", "name": "Client A1", "uplinkDeviceId": "a"}
        client_b1 = {"id": "b1", "name": "Client B1", "uplink_device_id": "b"}
        client_a2 = {"id": "a2", "name": "Client A2", "uplink_device_id": "a"}
        client_both = {
            "id": "both",
            "name": "Client Both",
            "uplinkDeviceId": "b",
            "uplink_device_id": "a",
        }
        client_fallback = {
            "id": "fallback",
            "name": "Client Fallback",
            "uplinkDeviceId": "",
            "uplink_device_id": "a",
        }
        client_missing = {
            "id": "missing",
            "name": "Client Missing",
            "uplinkDeviceId": None,
            "uplink_device_id": "",
        }
        client_unlinked = {"id": "unlinked", "name": "Client Unlinked"}
        client_unknown = {
            "id": "unknown",
            "name": "Client Unknown",
            "uplinkDeviceId": "unknown-uplink",
        }
        client_bad_list = {
            "id": "bad_list",
            "name": "Client Bad List",
            "uplinkDeviceId": ["bad", "list"],
        }
        client_bad_dict = {
            "id": "bad_dict",
            "name": "Client Bad Dict",
            "uplinkDeviceId": {"nested": "dict"},
        }
        client_bad_int = {
            "id": "bad_int",
            "name": "Client Bad Int",
            "uplinkDeviceId": 123,
        }
        client_bad_bool = {
            "id": "bad_bool",
            "name": "Client Bad Bool",
            "uplinkDeviceId": True,
        }
        client_bad_float = {
            "id": "bad_float",
            "name": "Client Bad Float",
            "uplinkDeviceId": 45.67,
        }
        client_camel_priority = {
            "id": "camel_priority",
            "name": "Client Camel Priority",
            "uplinkDeviceId": ["bad"],
            "uplink_device_id": "a",
        }

        coordinator.network_client.clients.get_all = AsyncMock(
            return_value=[
                _create_mock_model(client_a1),
                _create_mock_model(client_b1),
                _create_mock_model(client_a2),
                _create_mock_model(client_both),
                _create_mock_model(client_fallback),
                _create_mock_model(client_missing),
                _create_mock_model(client_unlinked),
                _create_mock_model(client_unknown),
                _create_mock_model(client_bad_list),
                _create_mock_model(client_bad_dict),
                _create_mock_model(client_bad_int),
                _create_mock_model(client_bad_bool),
                _create_mock_model(client_bad_float),
                _create_mock_model(client_camel_priority),
            ]
        )

        devices_dict, stats_dict, clients_dict = await coordinator._process_site(
            "default"
        )

        # Device dictionary retains all devices
        assert set(devices_dict.keys()) == {"a", "b", "empty"}

        # Client id ordering in device stats
        assert [c["id"] for c in stats_dict["a"]["clients"]] == [
            "a1",
            "a2",
            "fallback",
        ]
        assert [c["id"] for c in stats_dict["b"]["clients"]] == ["b1", "both"]
        assert stats_dict["empty"]["clients"] == []

        # Concrete independent expected values
        assert stats_dict["a"]["clients"] == [
            client_a1,
            client_a2,
            client_fallback,
        ]
        assert stats_dict["b"]["clients"] == [client_b1, client_both]

        # Empty device retains its statistics
        assert stats_dict["empty"]["id"] == "empty"
        assert "cpuUtilizationPct" in stats_dict["empty"]

        # Object identity is shared between group list and sitewide clients dictionary
        assert stats_dict["a"]["clients"][0] is clients_dict["a1"]
        assert stats_dict["a"]["clients"][1] is clients_dict["a2"]
        assert stats_dict["a"]["clients"][2] is clients_dict["fallback"]
        assert stats_dict["b"]["clients"][0] is clients_dict["b1"]
        assert stats_dict["b"]["clients"][1] is clients_dict["both"]

        # Sitewide dictionary retains all clients including unlinked and malformed
        assert set(clients_dict.keys()) == {
            "a1",
            "b1",
            "a2",
            "both",
            "fallback",
            "missing",
            "unlinked",
            "unknown",
            "bad_list",
            "bad_dict",
            "bad_int",
            "bad_bool",
            "bad_float",
            "camel_priority",
        }
        assert clients_dict["missing"] == client_missing
        assert clients_dict["unlinked"] == client_unlinked
        assert clients_dict["unknown"] == client_unknown
        assert clients_dict["bad_list"] == client_bad_list
        assert clients_dict["bad_dict"] == client_bad_dict
        assert clients_dict["bad_int"] == client_bad_int
        assert clients_dict["bad_bool"] == client_bad_bool
        assert clients_dict["bad_float"] == client_bad_float
        assert clients_dict["camel_priority"] == client_camel_priority

        # Object identity is preserved for sitewide unassigned clients
        assert clients_dict["bad_list"] is client_bad_list
        assert clients_dict["camel_priority"] is client_camel_priority
