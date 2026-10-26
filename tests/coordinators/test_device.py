# Copyright 2026 UniFi Insights contributors
"""
Tests for the device owner's command foundations (ADR-010).

Covers PoE port eligibility, which lives next to the power-cycle command and
reads #337's port facts, and the device coordinator's reuse of the config
coordinator's legacy site-name rule.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from contextlib import nullcontext, suppress
from typing import TYPE_CHECKING, Any, Final
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiNotFoundError,
    UniFiRateLimitError,
    UniFiResponseError,
    UniFiTimeoutError,
    UniFiValidationError,
)
from unifi_official_api.network.endpoints.devices import DevicesEndpoint
from unifi_official_api.network.models import LegacyPortMetrics, PortBytesMetrics

from custom_components.unifi_insights.const import (
    DOMAIN,
    MAX_CONSECUTIVE_MISSING_POLLS,
    SCAN_INTERVAL_DEVICE,
)
from custom_components.unifi_insights.coordinators.config import (
    UnifiInsightsConfigCoordinator,
)
from custom_components.unifi_insights.coordinators.device import (
    MAX_STATS_REUSE_POLLS,
    OUTLET_WRITE_GUARD_SECONDS,
    OutletPayloadProvenance,
    PendingOutletWrite,
    UnifiInsightsDeviceCoordinator,
    _build_outlet_overrides,
    _is_table_outlet,
    evaluate_outlet_payload_provenance,
    get_device_port,
    outlet_payload_is_safe,
    port_can_be_power_cycled,
    power_cyclable_ports,
    reconcile_pending_outlet_writes,
    require_power_cyclable_port,
)
from custom_components.unifi_insights.data_transforms import (
    NETWORK_FACTS_KEY,
    SiteNetworkFacts,
    build_site_network_facts,
)
from custom_components.unifi_insights.entity import is_device_online
from tests.conftest import (
    device_config_entry_ids,
    get_device_by_identifier,
)
from tests.fixtures.assertions import (
    patch_attribute,
    require_instance,
    require_mock,
    require_not_none,
)
from tests.fixtures.command_owners import (
    PDU_ID,
    SITE_ID,
    async_shutdown_owners,
    build_seeded_owners,
)
from tests.fixtures.coordinator_clients import (
    _create_mock_model,
    _create_mock_network_client,
    _create_mock_protect_client,
)
from tests.fixtures.library_responses import (
    SAMPLE_PDU_DEVICE,
    SAMPLE_PDU_LEGACY_DEVICE,
    SAMPLE_PDU_LEGACY_DEVICE_COMPLETE,
    SAMPLE_PDU_LEGACY_DEVICE_SUPERSET,
    SAMPLE_UDM_PRO_OVERVIEW,
    SAMPLE_UDM_PRO_STATS,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

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


COMPLETE_OUTLET_DEVICE: Final = SAMPLE_PDU_LEGACY_DEVICE_COMPLETE


def _device_outlet_body(
    device: Mapping[str, Any],
    changes: Mapping[int, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return a device's override body with per-index field changes."""
    body = copy.deepcopy(device["outlet_overrides"])
    for row in body:
        row.update((changes or {}).get(row["index"], {}))
    return body


def _outlet_body(
    changes: Mapping[int, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return the complete override body with per-index field changes."""
    return _device_outlet_body(COMPLETE_OUTLET_DEVICE, changes)


def _seeded_body(target: int, *, state: bool) -> list[dict[str, Any]]:
    """Return the payload the builder seeds from the complete table."""
    body: list[dict[str, Any]] = []
    for row in COMPLETE_OUTLET_DEVICE["outlet_table"]:
        seeded: dict[str, Any] = {
            "index": row["index"],
            "relay_state": state if row["index"] == target else row["relay_state"],
        }
        if "name" in row:
            seeded["name"] = row["name"]
        if "cycle_enabled" in row:
            seeded["cycle_enabled"] = bool(row["cycle_enabled"])
        body.append(seeded)
    return body


def _complete_without(*indexes: int) -> dict[str, Any]:
    """Return the complete PDU with the given outlet indexes removed."""
    device = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
    for key in ("outlet_table", "outlet_overrides"):
        device[key] = [row for row in device[key] if row["index"] not in indexes]
    return device


async def _seed_complete_outlet_poll(owners: SeededOwners) -> None:
    """Serve a complete PDU poll and refresh so the #376 gate has a baseline."""
    devices = owners.network_client.devices
    devices.get_legacy_site_devices.side_effect = None
    devices.get_legacy_site_devices.return_value = [
        copy.deepcopy(COMPLETE_OUTLET_DEVICE)
    ]
    await owners.device.async_refresh()


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

    @pytest.fixture(autouse=True)
    async def _complete_baseline(self, seeded_owners: SeededOwners) -> None:
        """Trust a complete poll before each command (the #376 gate)."""
        await _seed_complete_outlet_poll(seeded_owners)

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
        device.async_request_refresh = AsyncMock()

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
        assert len(seen) == 1
        overlay = seen[0]
        assert overlay is not before_device
        assert overlay["outlet_table"] is not before_table
        assert overlay["outlet_table"][1]["relay_state"] is True
        assert overlay["outlet_table"][1]["cycle_enabled"] is True
        # Unwritten outlets stay shared and the old snapshot is untouched.
        assert overlay["outlet_table"][0] is before_table[0]
        assert before_table[1]["relay_state"] is False
        # Replaced outlet_overrides is a new list with the sent overrides.
        assert overlay["outlet_overrides"] is not before_device["outlet_overrides"]
        assert overlay["outlet_overrides"] == _outlet_body({2: {"relay_state": True}})
        device.async_request_refresh.assert_awaited_once()

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
        assert seen[0]["outlet_overrides"] == _outlet_body(
            {1: {"cycle_enabled": False}}
        )

    @pytest.mark.parametrize(
        ("target", "outlet_index"),
        [("unknown-site", 2), ("missing-device", 2), ("pdu", 99)],
    )
    async def test_unknown_outlet_is_rejected_without_a_put(
        self,
        seeded_owners: SeededOwners,
        target: str,
        outlet_index: int,
    ) -> None:
        """An unknown target cannot build or authorise a write: fail closed."""
        device = seeded_owners.device
        client = seeded_owners.network_client
        pdu = device.data["devices"][SITE_ID][PDU_ID]
        before = copy.deepcopy(pdu)
        site_id = "unknown" if target == "unknown-site" else SITE_ID
        seeded_owners.config.data["legacy_site_names"][site_id] = "default"
        device_id = "missing" if target == "missing-device" else PDU_ID
        listener = MagicMock()
        device.async_add_listener(listener)
        device.async_request_refresh = AsyncMock()

        with pytest.raises(HomeAssistantError, match=r"^Unable to set outlet ") as err:
            await device.async_set_outlet_state(
                site_id, device_id, outlet_index, state=True
            )

        assert err.value.translation_key == "command_failed"
        client.devices.set_outlet_state.assert_not_called()
        listener.assert_not_called()
        device.async_request_refresh.assert_not_awaited()
        assert device.data["devices"][SITE_ID][PDU_ID] == before

    async def test_outlet_index_aliases_match(
        self, seeded_owners: SeededOwners
    ) -> None:
        """``outlet_idx`` rows carry a complete payload through the alias."""
        device = seeded_owners.device
        legacy = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        for row in legacy["outlet_table"]:
            row["outlet_idx"] = row.pop("index")
        for row in legacy["outlet_overrides"]:
            row["outlet_idx"] = row.pop("index")
        _serve(seeded_owners, legacy)
        await device.async_refresh()
        seen: list[dict[str, Any]] = []
        device.async_add_listener(
            lambda: seen.append(device.data["devices"][SITE_ID][PDU_ID])
        )

        await device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)

        published = seen[0]["outlet_table"][1]
        assert published["index"] == 2
        assert published["relay_state"] is True
        assert published["cycle_enabled"] is True
        assert seen[0]["outlet_overrides"][1]["outlet_idx"] == 2
        assert seen[0]["outlet_overrides"][1]["relay_state"] is True

    def test_table_outlet_outletidx_alias_matches(self) -> None:
        """The table lookup still recognises the legacy JSON ``outletIdx``."""
        assert _is_table_outlet({"outletIdx": "2", "relay_state": True}, 2)
        assert not _is_table_outlet({"outletIdx": "3", "relay_state": True}, 2)
        # Unconvertible aliases are simply not this outlet, never an error.
        assert not _is_table_outlet({"outletIdx": "bad", "relay_state": True}, 2)
        assert not _is_table_outlet({"index": None, "outlet_idx": None}, 2)

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


class _OutletPublishProbe:
    """Minimal owner surface for the copy-on-write publish helper contract."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.listener = MagicMock()

    def async_update_listeners(self) -> None:
        """Record one listener notification."""
        self.listener()


PUBLISH_DEVICE: Final = {
    "outlet_table": [{"index": 1, "relay_state": False, "name": "Outlet 1"}],
    "outlet_overrides": [{"index": 1, "relay_state": False, "name": "Outlet 1"}],
}


class TestOutletPublishContract:
    """Direct contract of the unchanged copy-on-write publish helper."""

    @staticmethod
    def _publish(
        data: dict[str, Any],
        *,
        outlet_index: int = 1,
        changes: dict[str, bool] | None = None,
        overrides: list[dict[str, Any]] | None = None,
    ) -> _OutletPublishProbe:
        probe = _OutletPublishProbe(data)
        UnifiInsightsDeviceCoordinator._publish_outlet(
            probe,  # type: ignore[arg-type]
            SITE_ID,
            PDU_ID,
            outlet_index,
            changes if changes is not None else {"relay_state": True},
            overrides,
        )
        return probe

    @pytest.mark.parametrize(
        "data",
        [
            {"devices": {SITE_ID: []}},
            {"devices": {SITE_ID: {PDU_ID: []}}},
            {"devices": {}},
            {},
        ],
    )
    def test_no_publish_without_site_or_device_record(
        self, data: dict[str, Any]
    ) -> None:
        """A missing or malformed cache path is a silent no-op."""
        probe = self._publish(data, overrides=[{"index": 1, "relay_state": True}])

        assert probe.data is data
        assert probe.listener.call_count == 0

    def test_no_publish_when_nothing_can_be_applied(self) -> None:
        """No overrides and no matching table row leave the root untouched."""
        data = {
            "devices": {
                SITE_ID: {
                    PDU_ID: {"outlet_table": [{"index": 2, "relay_state": False}]}
                }
            }
        }
        before = copy.deepcopy(data)

        probe = self._publish(data, overrides=None)

        assert probe.data is data
        assert data == before
        assert probe.listener.call_count == 0

    def test_publishes_overrides_without_a_table(self) -> None:
        """An override-only publish copies its ancestors and notifies once."""
        device = {"name": "PDU", "outlet_overrides": [{"index": 1, "name": "A"}]}
        data = {"devices": {SITE_ID: {PDU_ID: device}}}
        before = copy.deepcopy(data)
        overrides = [{"index": 1, "relay_state": True, "name": "A"}]

        probe = self._publish(data, overrides=overrides)

        published = probe.data["devices"][SITE_ID][PDU_ID]
        assert published is not device
        assert published["outlet_overrides"] == overrides
        assert "outlet_table" not in published
        assert data == before
        assert probe.listener.call_count == 1

    def test_publishes_a_matching_table_row_without_overrides(self) -> None:
        """A table-only publish copies the list and row, not the siblings."""
        device = copy.deepcopy(PUBLISH_DEVICE)
        device.pop("outlet_overrides")
        before = copy.deepcopy(device)
        data = {"devices": {SITE_ID: {PDU_ID: device}}}

        probe = self._publish(data, overrides=None)

        published = probe.data["devices"][SITE_ID][PDU_ID]
        assert published["outlet_table"][0] == {
            "index": 1,
            "relay_state": True,
            "name": "Outlet 1",
        }
        assert published["outlet_table"] is not device["outlet_table"]
        assert device == before
        assert "outlet_overrides" not in published
        assert probe.listener.call_count == 1


class _FakePduController:
    """Stateful classic-API stand-in: relay state follows the last PUT."""

    def __init__(self, device: dict[str, Any] | None = None) -> None:
        self.device = copy.deepcopy(
            device if device is not None else SAMPLE_PDU_LEGACY_DEVICE_COMPLETE
        )
        self.puts: list[list[dict[str, Any]]] = []

    def apply(self, overrides: list[dict[str, Any]]) -> None:
        """Apply overrides: record deep copy, update relay states, store overrides."""
        self.puts.append(copy.deepcopy(overrides))
        by_idx: dict[int, dict[str, Any]] = {}
        for o in overrides:
            idx = o.get("index")
            if idx is None:
                idx = o.get("outlet_idx")
            if idx is not None:
                with suppress(TypeError, ValueError):
                    by_idx[int(idx)] = o
        for outlet in self.device["outlet_table"]:
            idx = outlet.get("index")
            if idx is None:
                idx = outlet.get("outlet_idx")
            if idx is not None:
                with suppress(TypeError, ValueError):
                    ov = by_idx.get(int(idx))
                    outlet["relay_state"] = ov["relay_state"] if ov else True
        self.device["outlet_overrides"] = copy.deepcopy(overrides)

    def relay(self) -> dict[int, bool]:
        """Return a mapping of outlet index to relay state."""
        return {
            int(o["index"]): o["relay_state"]
            for o in self.device["outlet_table"]
            if "index" in o
        }


def _wire_pdu_controller(
    seeded_owners: SeededOwners, controller: _FakePduController
) -> DevicesEndpoint:
    """Wire a real DevicesEndpoint transport to a fake controller."""
    client = MagicMock()

    async def put(path: str, json_data: dict[str, Any]) -> None:
        controller.apply(json_data["outlet_overrides"])

    client._put = put
    client.build_legacy_api_path = lambda site, path: f"/{site}{path}"
    endpoint = DevicesEndpoint(client)

    seeded_owners.network_client.devices.set_outlet_state.side_effect = (
        endpoint.set_outlet_state
    )
    seeded_owners.network_client.devices.get_legacy_site_devices.side_effect = (
        lambda *a, **k: [copy.deepcopy(controller.device)]
    )
    return endpoint


FIRST_WRITE_OVERRIDE = {
    "cycle_enabled": True,
    "index": 1,
    "name": "Main Server",
    "relay_state": False,
}
OUTLET_1_ON = {"index": 1, "relay_state": True}
OUTLET_1_OFF = {"index": 1, "relay_state": False}
OUTLET_2_OFF = {"index": 2, "relay_state": False}
OUTLET_3_ON = {"index": 3, "relay_state": True}


class TestSequentialOutletWrites:
    """Sequential PDU outlet writes mirror library read-modify-write semantics."""

    @pytest.fixture(autouse=True)
    async def _complete_baseline(self, seeded_owners: SeededOwners) -> None:
        """Trust a complete poll before each sequential write."""
        await _seed_complete_outlet_poll(seeded_owners)

    async def test_poll_in_flight_cannot_overwrite_a_write(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A write waits for an in-flight poll, so its stale array is not kept."""
        controller = _FakePduController()
        _wire_pdu_controller(seeded_owners, controller)
        device = seeded_owners.device
        device.async_request_refresh = AsyncMock()
        devices = seeded_owners.network_client.devices
        stale = copy.deepcopy(controller.device)
        polling = asyncio.Event()
        release = asyncio.Event()

        async def slow_read(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            polling.set()
            await release.wait()
            return [copy.deepcopy(stale)]

        devices.get_legacy_site_devices.side_effect = slow_read
        poll = asyncio.create_task(device.async_refresh())
        await polling.wait()
        write = asyncio.create_task(
            device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)
        )
        for _ in range(20):
            await asyncio.sleep(0)
        assert controller.puts == []

        release.set()
        await poll
        await write
        devices.get_legacy_site_devices.side_effect = lambda *a, **k: [
            copy.deepcopy(controller.device)
        ]
        await device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=False)

        assert controller.puts[-1][:1] == [FIRST_WRITE_OVERRIDE]
        assert controller.relay()[1] is False
        assert controller.relay()[2] is False

    async def test_overlapping_writes_run_one_after_the_other(
        self, seeded_owners: SeededOwners, monkeypatch
    ) -> None:
        """A second command waits and is built from the first command's array."""
        controller = _FakePduController()
        endpoint = _wire_pdu_controller(seeded_owners, controller)
        device = seeded_owners.device
        device.async_request_refresh = AsyncMock()
        sending = asyncio.Event()
        release = asyncio.Event()

        async def slow_put(path: str, json_data: dict[str, Any]) -> None:
            sending.set()
            await release.wait()
            controller.apply(json_data["outlet_overrides"])

        patch_attribute(monkeypatch, endpoint._client, "_put", slow_put)
        first = asyncio.create_task(
            device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)
        )
        await sending.wait()
        second = asyncio.create_task(
            device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=False)
        )
        for _ in range(20):
            await asyncio.sleep(0)
        assert seeded_owners.network_client.devices.set_outlet_state.await_count == 1

        release.set()
        await first
        await second

        assert controller.puts == [
            _outlet_body({1: {"relay_state": False}}),
            _outlet_body({1: {"relay_state": False}, 2: {"relay_state": False}}),
        ]
        assert controller.relay()[1] is False

    async def test_two_writes_when_refresh_after_first_write_fails(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Sequential writes preserve earlier outlets even when refresh fails."""
        controller = _FakePduController()
        _wire_pdu_controller(seeded_owners, controller)
        device = seeded_owners.device
        seeded_owners.network_client.devices.get_all.side_effect = UniFiConnectionError(
            "down"
        )

        await device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)
        assert device.last_update_success is False
        await device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=False)

        assert controller.puts == [
            _outlet_body({1: {"relay_state": False}}),
            _outlet_body({1: {"relay_state": False}, 2: {"relay_state": False}}),
        ]
        assert controller.relay()[1] is False

    async def test_three_writes_inside_refresh_cooldown(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Three back-to-back writes keep all outlets off during refresh cooldown."""
        controller = _FakePduController()
        _wire_pdu_controller(seeded_owners, controller)
        device = seeded_owners.device
        device.async_request_refresh = AsyncMock()

        await device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)
        await device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=False)
        await device.async_set_outlet_state(SITE_ID, PDU_ID, 3, state=False)

        assert controller.puts == [
            _outlet_body({1: {"relay_state": False}}),
            _outlet_body({1: {"relay_state": False}, 2: {"relay_state": False}}),
            _outlet_body(
                {
                    1: {"relay_state": False},
                    2: {"relay_state": False},
                    3: {"relay_state": False},
                }
            ),
        ]
        assert controller.relay() == {1: False, 2: False, 3: False}

    async def test_relay_change_then_cycle_change_on_same_outlet(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Relay change then cycle change preserves values when cycle_enabled=None."""
        controller = _FakePduController()
        _wire_pdu_controller(seeded_owners, controller)
        device = seeded_owners.device
        device.async_request_refresh = AsyncMock()

        for state, cycle, expected_cycle in [
            (False, None, True),
            (False, False, False),
            (True, None, False),
        ]:
            await device.async_set_outlet_state(
                SITE_ID, PDU_ID, 1, state=state, cycle_enabled=cycle
            )
            assert controller.puts[-1][0]["relay_state"] is state
            assert controller.puts[-1][0]["cycle_enabled"] is expected_cycle

    async def test_endpoint_failure_does_not_publish_or_leak_into_later_write(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Endpoint failure raises HomeAssistantError, publishes nothing."""
        controller = _FakePduController()
        endpoint = _wire_pdu_controller(seeded_owners, controller)
        put = endpoint._client._put
        endpoint._client._put = AsyncMock(side_effect=UniFiConnectionError("timeout"))
        device = seeded_owners.device
        device.async_request_refresh = AsyncMock()
        before_device = copy.deepcopy(device.data["devices"][SITE_ID][PDU_ID])

        with pytest.raises(HomeAssistantError, match=r"^Unable to set outlet 2") as err:
            await device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=False)

        assert err.value.translation_key == "command_failed"
        current_device = device.data["devices"][SITE_ID][PDU_ID]
        assert current_device["outlet_overrides"] == before_device["outlet_overrides"]
        assert current_device["outlet_table"] == before_device["outlet_table"]

        endpoint._client._put = put
        await device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)

        assert controller.puts == [_outlet_body({1: {"relay_state": False}})]
        assert controller.puts[0][0]["index"] == 1
        assert controller.puts[0][0]["relay_state"] is False

    @pytest.mark.parametrize("seed_from_table", [False, True])
    async def test_copy_on_write_immutability(
        self, seeded_owners: SeededOwners, *, seed_from_table: bool
    ) -> None:
        """Preserve old snapshots and unknown fields while publishing sent payloads."""
        controller = _FakePduController()
        _wire_pdu_controller(seeded_owners, controller)
        device = seeded_owners.device
        device.async_request_refresh = AsyncMock()
        old_device = device.data["devices"][SITE_ID][PDU_ID]
        if seed_from_table:
            del old_device["outlet_overrides"]
        else:
            old_device["outlet_overrides"][0]["sentinel_custom_field"] = "xyz_123"
        old_snapshot = copy.deepcopy(old_device)
        old_overrides = old_device.get("outlet_overrides")
        index, state = (2, True) if seed_from_table else (1, False)

        await device.async_set_outlet_state(SITE_ID, PDU_ID, index, state=state)

        new_device = device.data["devices"][SITE_ID][PDU_ID]
        published = new_device["outlet_overrides"]
        assert new_device is not old_device
        assert old_device == old_snapshot
        assert published == controller.puts[0]
        if seed_from_table:
            assert len(published) == 3
            assert all(item["relay_state"] is True for item in published)
        else:
            assert old_device["outlet_overrides"] is old_overrides
            assert published is not old_overrides
            assert published[0] is not old_overrides[0]
            assert published[0]["sentinel_custom_field"] == "xyz_123"
            await device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=False)
            assert controller.puts[1][0]["sentinel_custom_field"] == "xyz_123"
            assert (
                device.data["devices"][SITE_ID][PDU_ID]["outlet_overrides"]
                == controller.puts[1]
            )

    async def test_poll_mid_write_publishes_sent_overrides(
        self, seeded_owners: SeededOwners, monkeypatch
    ) -> None:
        """A poll during PUT must not replace the payload used for publication."""
        controller = _FakePduController()
        endpoint = _wire_pdu_controller(seeded_owners, controller)
        device = seeded_owners.device
        device.async_request_refresh = AsyncMock()

        async def put(path: str, json_data: dict[str, Any]) -> None:
            controller.apply(json_data["outlet_overrides"])
            devices = device.data["devices"]
            pdu = devices[SITE_ID][PDU_ID]
            polled_pdu = {**pdu, "outlet_overrides": [OUTLET_3_ON]}
            polled_site = {**devices[SITE_ID], PDU_ID: polled_pdu}
            device.data = {**device.data, "devices": {**devices, SITE_ID: polled_site}}

        patch_attribute(monkeypatch, endpoint._client, "_put", put)
        await device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)

        assert (
            device.data["devices"][SITE_ID][PDU_ID]["outlet_overrides"]
            == controller.puts[-1]
        )

    @pytest.mark.parametrize(
        ("device_dict", "command", "expected"),
        [
            (
                {"outlet_overrides": ["junk", None, 123, OUTLET_1_ON]},
                (1, False, None),
                [OUTLET_1_OFF],
            ),
            (
                {"outlet_table": ["junk", None, 123, OUTLET_1_ON]},
                (1, False, None),
                [OUTLET_1_OFF],
            ),
            (
                {
                    "outlet_table": [
                        {"index": "bad_index", "relay_state": True},
                        {"index": None, "outlet_idx": "also_bad"},
                        OUTLET_1_ON,
                    ]
                },
                (1, False, None),
                [OUTLET_1_OFF],
            ),
            (
                {
                    "outlet_overrides": [
                        {"index": "invalid", "relay_state": True},
                        OUTLET_1_ON,
                    ]
                },
                (1, False, None),
                [{"index": "invalid", "relay_state": True}, OUTLET_1_OFF],
            ),
            (
                {"outlet_table": [{"outlet_idx": "2", "relay_state": True}]},
                (2, False, None),
                [OUTLET_2_OFF],
            ),
            (
                {"outlet_overrides": [{"outlet_idx": 2, "relay_state": True}]},
                (2, False, None),
                [{"outlet_idx": 2, "relay_state": False}],
            ),
            (
                {"outlet_table": [{"index": 1, "outlet_idx": 2, "relay_state": True}]},
                (1, False, None),
                [OUTLET_1_OFF],
            ),
            (
                {"outlet_overrides": [{"outletIdx": 1, "relay_state": True}]},
                (1, False, None),
                [{"outletIdx": 1, "relay_state": True}, OUTLET_1_OFF],
            ),
            (
                {
                    "outlet_overrides": [
                        {"index": 1, "outlet_idx": 2, "relay_state": True}
                    ]
                },
                (1, False, None),
                [{"index": 1, "outlet_idx": 2, "relay_state": False}],
            ),
            (
                {
                    "outlet_table": [
                        {"index": 1, "name": "Outlet 1"},
                        {"index": 2, "cycle_enabled": True},
                        {"index": 3},
                    ]
                },
                (3, True, None),
                [
                    {"index": 1, "relay_state": False, "name": "Outlet 1"},
                    {"index": 2, "relay_state": False, "cycle_enabled": True},
                    OUTLET_3_ON,
                ],
            ),
            (
                {
                    "outlet_overrides": [
                        {"index": 1, "relay_state": True, "first": True},
                        {"index": 1, "relay_state": True, "first": False},
                    ]
                },
                (1, False, None),
                [
                    {"index": 1, "relay_state": False, "first": True},
                    {"index": 1, "relay_state": True, "first": False},
                ],
            ),
            (
                {"outlet_overrides": [OUTLET_1_ON]},
                (2, False, True),
                [
                    OUTLET_1_ON,
                    {"index": 2, "relay_state": False, "cycle_enabled": True},
                ],
            ),
            ({}, (1, True, None), None),
            ({"outlet_overrides": []}, (1, True, None), None),
            (
                {"outlet_overrides": ["junk"], "outlet_table": ["junk"]},
                (1, True, None),
                None,
            ),
            ({"outlet_table": "not-a-list"}, (1, True, None), None),
            (
                {
                    "outlet_overrides": [
                        {"index": 1, "relay_state": True, "custom": "field"}
                    ]
                },
                (1, False, True),
                [
                    {
                        "index": 1,
                        "relay_state": False,
                        "custom": "field",
                        "cycle_enabled": True,
                    }
                ],
            ),
            (
                {"outlet_overrides": [OUTLET_1_ON], "outlet_table": [OUTLET_2_OFF]},
                (1, False, True),
                [{"index": 1, "relay_state": False, "cycle_enabled": True}],
            ),
            (
                {
                    "outlet_table": [
                        OUTLET_1_ON,
                        {"index": 2, "name": "PDU2", "cycle_enabled": False},
                    ]
                },
                (1, False, True),
                [
                    {"index": 1, "relay_state": False, "cycle_enabled": True},
                    {
                        "index": 2,
                        "relay_state": False,
                        "name": "PDU2",
                        "cycle_enabled": False,
                    },
                ],
            ),
            (
                {"outlet_overrides": ["junk", None, OUTLET_1_OFF]},
                (1, False, True),
                [{"index": 1, "relay_state": False, "cycle_enabled": True}],
            ),
            (
                {"outlet_table": [{"outlet_idx": "3", "relay_state": True}]},
                (1, False, True),
                [
                    OUTLET_3_ON,
                    {"index": 1, "relay_state": False, "cycle_enabled": True},
                ],
            ),
            ({}, (1, False, True), None),
            ({"outlet_table": []}, (1, False, True), None),
        ],
    )
    async def test_builder_matches_real_endpoint(
        self,
        device_dict: dict[str, Any],
        command: tuple[int, bool, bool | None],
        expected: list[dict[str, Any]] | None,
    ) -> None:
        """Builder and real endpoint match explicit payloads without mutating input."""
        outlet_index, state, cycle_enabled = command
        before = copy.deepcopy(device_dict)
        assert (
            _build_outlet_overrides(
                device_dict, outlet_index, state=state, cycle_enabled=cycle_enabled
            )
            == expected
        )
        client = MagicMock()
        client.build_legacy_api_path = lambda site, path: f"/{site}{path}"
        client._put = AsyncMock()
        endpoint = DevicesEndpoint(client)
        with pytest.raises(UniFiValidationError) if expected is None else nullcontext():
            await endpoint.set_outlet_state(
                "default",
                "dev1",
                outlet_index,
                state=state,
                cycle_enabled=cycle_enabled,
                current_device=device_dict,
            )
        if expected is None:
            client._put.assert_not_awaited()
        else:
            client._put.assert_awaited_once_with(
                "/default/rest/device/dev1", json_data={"outlet_overrides": expected}
            )
        assert device_dict == before


DEVICE_MONOTONIC = "custom_components.unifi_insights.coordinators.device.time.monotonic"


def _pdu(owners: SeededOwners) -> dict[str, Any]:
    """Return the PDU record the device owner published."""
    return owners.device.data["devices"][SITE_ID][PDU_ID]


def _serve(
    owners: SeededOwners, legacy: dict[str, Any], *, state: str = "ONLINE"
) -> None:
    """Make the next polls return this classic PDU record and v1 state."""
    v1 = copy.deepcopy(SAMPLE_PDU_DEVICE)
    v1["state"] = state
    devices = owners.network_client.devices
    devices.get_all.side_effect = None
    devices.get_all.return_value = [v1]
    devices.get_legacy_site_devices.side_effect = None
    devices.get_legacy_site_devices.return_value = [legacy]


def _outlet(owners: SeededOwners, index: int, field: str) -> tuple[Any, Any]:
    """Return an outlet field from (``outlet_overrides``, ``outlet_table``)."""
    pdu = _pdu(owners)
    override = next(
        (o for o in pdu.get("outlet_overrides", []) if o.get("index") == index), {}
    )
    outlet = next(o for o in pdu["outlet_table"] if o["index"] == index)
    return override.get(field), outlet.get(field)


class TestPendingOutletWrites:
    """A poll that still reports older outlet values keeps the write (#376)."""

    @pytest.fixture(autouse=True)
    async def _manual_refresh(self, seeded_owners: SeededOwners) -> None:
        """Run polls only when a test asks, and let outlet writes succeed."""
        seeded_owners.device.async_request_refresh = AsyncMock()
        seeded_owners.network_client.devices.set_outlet_state.return_value = True
        await _seed_complete_outlet_poll(seeded_owners)

    @pytest.mark.parametrize(
        ("index", "state", "overrides"),
        [
            (1, False, _outlet_body({1: {"relay_state": False}})),
            (2, True, _outlet_body({2: {"relay_state": True}})),
        ],
    )
    async def test_stale_poll_keeps_written_entries(
        self,
        seeded_owners: SeededOwners,
        index: int,
        *,
        state: bool,
        overrides: list[dict[str, Any]],
    ) -> None:
        """Both arrays keep the write; every other polled field updates."""
        device = seeded_owners.device
        await device.async_set_outlet_state(SITE_ID, PDU_ID, index, state=state)
        stale = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        stale["outlet_table"][0]["outlet_current"] = "2.50"
        stale["outlet_table"][2]["relay_state"] = False
        stale["outlet_ac_power_consumption"] = "300.00"
        polled = copy.deepcopy(stale)
        _serve(seeded_owners, stale, state="GETTING_READY")
        before = device.data
        before_pdu = copy.deepcopy(_pdu(seeded_owners))

        await device.async_refresh()

        pdu = _pdu(seeded_owners)
        assert pdu["outlet_overrides"] == overrides
        assert _outlet(seeded_owners, index, "relay_state")[1] is state
        assert pdu["state"] == "GETTING_READY"
        assert pdu["outlet_table"][0]["outlet_current"] == 2.5
        assert _outlet(seeded_owners, 3, "relay_state")[1] is False
        assert pdu["outlet_ac_power_consumption"] == 300.0
        assert stale == polled
        assert before["devices"][SITE_ID][PDU_ID] == before_pdu

    async def test_released_once_every_array_agrees(
        self, seeded_owners: SeededOwners
    ) -> None:
        """One array agreeing is not enough; both agreeing releases the write."""
        device = seeded_owners.device
        await device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)

        table_only = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        table_only["outlet_table"][0]["relay_state"] = False
        _serve(seeded_owners, table_only)
        await device.async_refresh()
        assert _outlet(seeded_owners, 1, "relay_state") == (False, False)

        agreed = copy.deepcopy(table_only)
        agreed["outlet_overrides"][0]["relay_state"] = False
        _serve(seeded_owners, agreed)
        await device.async_refresh()
        assert _outlet(seeded_owners, 1, "relay_state") == (False, False)

        # Switched back on elsewhere: the poll wins once the write is released.
        _serve(seeded_owners, copy.deepcopy(COMPLETE_OUTLET_DEVICE))
        await device.async_refresh()
        assert _outlet(seeded_owners, 1, "relay_state") == (True, True)

    @pytest.mark.parametrize(
        ("elapsed", "held"),
        [
            (OUTLET_WRITE_GUARD_SECONDS - 0.1, True),
            (OUTLET_WRITE_GUARD_SECONDS, False),
        ],
    )
    async def test_released_after_the_timeout(
        self,
        seeded_owners: SeededOwners,
        caplog: pytest.LogCaptureFixture,
        elapsed: float,
        *,
        held: bool,
    ) -> None:
        """A write the controller never reports gives way to the poll."""
        device = seeded_owners.device
        with patch(DEVICE_MONOTONIC, return_value=1000.0):
            await device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)

        with (
            patch(DEVICE_MONOTONIC, return_value=1000.0 + elapsed),
            caplog.at_level(logging.DEBUG),
        ):
            await device.async_refresh()

        assert _outlet(seeded_owners, 1, "relay_state") == (
            (False, False) if held else (True, True)
        )
        assert ("still reports old ['relay_state']" in caplog.text) is not held

    async def test_second_write_in_the_lag_window_keeps_the_first(
        self, seeded_owners: SeededOwners
    ) -> None:
        """The next PUT carries the earlier outlet as written, not as polled."""
        controller = _FakePduController()
        _wire_pdu_controller(seeded_owners, controller)
        stale = copy.deepcopy(controller.device)
        seeded_owners.network_client.devices.get_legacy_site_devices.side_effect = (
            lambda *a, **k: [copy.deepcopy(stale)]
        )
        device = seeded_owners.device

        await device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)
        await device.async_refresh()
        await device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=False)

        assert controller.puts[-1] == _outlet_body(
            {1: {"relay_state": False}, 2: {"relay_state": False}}
        )
        assert controller.relay() == {1: False, 2: False, 3: True}

    async def test_failed_write_records_nothing(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A refused write leaves the next poll's values as they are."""
        device = seeded_owners.device
        devices = seeded_owners.network_client.devices
        devices.set_outlet_state.side_effect = UniFiConnectionError("down")

        with pytest.raises(HomeAssistantError):
            await device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)
        await device.async_refresh()

        assert _outlet(seeded_owners, 1, "relay_state") == (True, True)

    @pytest.mark.parametrize("gone", ["device", "site"])
    async def test_removed_device_or_site_releases_the_write(
        self, seeded_owners: SeededOwners, gone: str
    ) -> None:
        """A PDU that comes back shows what the controller reports."""
        device = seeded_owners.device
        devices = seeded_owners.network_client.devices
        await device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)

        if gone == "device":
            devices.get_all.return_value = []
            devices.get_legacy_site_devices.return_value = []
            await device.async_refresh()
            assert PDU_ID not in device.data["devices"][SITE_ID]
            _serve(seeded_owners, copy.deepcopy(COMPLETE_OUTLET_DEVICE))
        else:
            with patch.object(seeded_owners.config, "get_site_ids", return_value=[]):
                await device.async_refresh()
            assert SITE_ID not in device.data["devices"]
        await device.async_refresh()

        assert _outlet(seeded_owners, 1, "relay_state") == (True, True)

    async def test_failed_refresh_keeps_the_write_pending(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A failed poll neither publishes nor releases the pending write."""
        device = seeded_owners.device
        devices = seeded_owners.network_client.devices
        await device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)
        devices.get_all.side_effect = UniFiConnectionError("down")
        await device.async_refresh()
        assert device.last_update_success is False

        devices.get_all.side_effect = None
        await device.async_refresh()

        assert device.last_update_success is True
        assert _outlet(seeded_owners, 1, "relay_state") == (False, False)

    async def test_cycle_write_after_a_relay_write_holds_both(
        self, seeded_owners: SeededOwners
    ) -> None:
        """The cycle switch echoes the held relay, and both fields stay held."""
        controller = _FakePduController()
        _wire_pdu_controller(seeded_owners, controller)
        stale = copy.deepcopy(controller.device)
        seeded_owners.network_client.devices.get_legacy_site_devices.side_effect = (
            lambda *a, **k: [copy.deepcopy(stale)]
        )
        device = seeded_owners.device
        await device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)
        await device.async_refresh()

        relay = _outlet(seeded_owners, 1, "relay_state")[1]
        await device.async_set_outlet_state(
            SITE_ID, PDU_ID, 1, state=relay, cycle_enabled=False
        )
        await device.async_refresh()

        assert controller.puts[-1] == _outlet_body(
            {1: {"relay_state": False, "cycle_enabled": False}}
        )
        assert _outlet(seeded_owners, 1, "relay_state") == (False, False)
        assert _outlet(seeded_owners, 1, "cycle_enabled") == (False, False)

    async def test_cycle_write_does_not_hold_the_relay_echo(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A cycle write holds cycle_enabled; the polled relay still updates."""
        device = seeded_owners.device
        await device.async_set_outlet_state(
            SITE_ID, PDU_ID, 1, state=True, cycle_enabled=False
        )
        relay_off = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        relay_off["outlet_table"][0]["relay_state"] = False
        relay_off["outlet_overrides"][0]["relay_state"] = False
        _serve(seeded_owners, relay_off)

        await device.async_refresh()

        assert _outlet(seeded_owners, 1, "relay_state") == (False, False)
        assert _outlet(seeded_owners, 1, "cycle_enabled") == (False, False)


RELAY_ON: Final = PendingOutletWrite({"relay_state": True}, None, 0.0)


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (
            {"outlet_table": [{"index": 1, "relay_state": False}]},
            {"outlet_table": [{"index": 1, "relay_state": True}]},
        ),
        (
            {
                "outlet_overrides": "not-a-list",
                "outlet_table": [{"index": 1, "relay_state": False}],
            },
            {
                "outlet_overrides": "not-a-list",
                "outlet_table": [{"index": 1, "relay_state": True}],
            },
        ),
        (
            {
                "outlet_overrides": ["junk"],
                "outlet_table": ["junk", {"outlet_idx": "1", "relay_state": False}],
            },
            {
                "outlet_overrides": ["junk"],
                "outlet_table": ["junk", {"outlet_idx": "1", "relay_state": True}],
            },
        ),
        (
            {"outlet_overrides": ["junk", {"index": 2, "relay_state": False}]},
            {
                "outlet_overrides": [
                    "junk",
                    {"index": 2, "relay_state": False},
                    {"index": 1, "relay_state": True},
                ]
            },
        ),
        ({"outlet_overrides": [], "outlet_table": [{"index": 2}]}, None),
        # The outlet is in the table without the field: the write is patched in.
        (
            {"outlet_table": [{"index": 1}]},
            {"outlet_table": [{"index": 1, "relay_state": True}]},
        ),
        # The controller reports the written value: confirmed and released.
        (
            {
                "outlet_overrides": [{"outlet_idx": 1, "relay_state": True}],
                "outlet_table": "not-a-list",
            },
            "released",
        ),
        ("not-a-device", "released"),
    ],
)
def test_reconcile_pending_outlet_writes_shapes(
    record: Any, expected: dict[str, Any] | str | None
) -> None:
    """
    Patch copies of the arrays that exist; hold what nothing reports yet.

    ``expected`` is the patched device (held), None when there is nothing to
    patch but the field is not confirmed (held), or "released" when the
    controller confirms the value or the device record is malformed.
    """
    pending = {(SITE_ID, "pdu", 1): RELAY_ON, ("other", "pdu", 1): RELAY_ON}
    devices = {"pdu": record}
    before = copy.deepcopy(record)

    result, held = reconcile_pending_outlet_writes(SITE_ID, devices, pending, 1.0)

    assert record == before
    if expected == "released":
        assert result is devices
        assert held == {}
    elif expected is None:
        # Nothing to patch, but no report confirms the field: it stays held
        # until a report, the timeout or the device's disappearance.
        assert result is devices
        assert held == {(SITE_ID, "pdu", 1): RELAY_ON}
    else:
        assert result == {"pdu": expected}
        assert held == {(SITE_ID, "pdu", 1): RELAY_ON}


def test_reconcile_holds_two_stale_outlets_of_one_device() -> None:
    """Two stale outlets of the same device are both patched and both held."""
    write_1 = PendingOutletWrite({"relay_state": True}, {"index": 1}, 0.0)
    write_2 = PendingOutletWrite({"relay_state": False}, {"index": 2}, 0.0)
    record = {
        "outlet_overrides": [
            {"index": 1, "relay_state": False},
            {"index": 2, "relay_state": True},
        ],
        "outlet_table": [
            {"index": 1, "relay_state": False},
            {"index": 2, "relay_state": True},
        ],
    }
    before = copy.deepcopy(record)
    devices = {"pdu": record}
    pending = {(SITE_ID, "pdu", 1): write_1, (SITE_ID, "pdu", 2): write_2}

    result, held = reconcile_pending_outlet_writes(SITE_ID, devices, pending, 1.0)

    assert record == before
    assert result is not devices
    assert result["pdu"]["outlet_overrides"] == [
        {"index": 1, "relay_state": True},
        {"index": 2, "relay_state": False},
    ]
    assert result["pdu"]["outlet_table"] == [
        {"index": 1, "relay_state": True},
        {"index": 2, "relay_state": False},
    ]
    assert held == pending


def test_reconcile_holds_a_field_no_array_reports() -> None:
    """A device without a usable outlet report keeps the write held, unpatched."""
    write = PendingOutletWrite({"relay_state": True}, {"index": 1}, 0.0)
    devices = {"pdu": {"state": "GETTING_READY"}}

    result, held = reconcile_pending_outlet_writes(
        SITE_ID, devices, {(SITE_ID, "pdu", 1): write}, 1.0
    )

    assert result is devices
    assert held == {(SITE_ID, "pdu", 1): write}

    # The timeout still releases it.
    result, held = reconcile_pending_outlet_writes(
        SITE_ID, devices, {(SITE_ID, "pdu", 1): write}, OUTLET_WRITE_GUARD_SECONDS
    )
    assert result is devices
    assert held == {}


def test_reconcile_releases_confirmed_fields_only() -> None:
    """A confirmed field is dropped while the other written field stays held."""
    write = PendingOutletWrite(
        {"relay_state": True, "cycle_enabled": False}, {"index": 1}, 0.0
    )
    record = {"outlet_overrides": [{"index": 1, "relay_state": True}]}

    result, held = reconcile_pending_outlet_writes(
        SITE_ID, {"pdu": record}, {(SITE_ID, "pdu", 1): write}, 1.0
    )

    assert result["pdu"]["outlet_overrides"] == [
        {"index": 1, "relay_state": True, "cycle_enabled": False}
    ]
    assert held[SITE_ID, "pdu", 1].fields == {"cycle_enabled": False}


class TestOutletPayloadSafety:
    """The #376 gate sends a complete body or no PUT, never a partial one."""

    @staticmethod
    def _wire(owners: SeededOwners) -> _FakePduController:
        """Wire the real pinned endpoint to a fake complete PDU."""
        controller = _FakePduController()
        _wire_pdu_controller(owners, controller)
        owners.device.async_request_refresh = AsyncMock()
        return controller

    @staticmethod
    async def _poll(
        owners: SeededOwners, device: dict[str, Any], *, state: str = "ONLINE"
    ) -> None:
        _serve(owners, device, state=state)
        await owners.device.async_refresh()

    @staticmethod
    async def _assert_rejected(
        owners: SeededOwners, controller: _FakePduController, outlet_index: int
    ) -> None:
        """Assert the gate raised before transport, publish or refresh."""
        devices = owners.network_client.devices
        calls = devices.set_outlet_state.await_count
        puts = len(controller.puts)
        cache = owners.device.data
        with pytest.raises(HomeAssistantError) as err:
            await owners.device.async_set_outlet_state(
                SITE_ID, PDU_ID, outlet_index, state=True
            )
        assert err.value.translation_key == "command_failed"
        assert devices.set_outlet_state.await_count == calls
        assert len(controller.puts) == puts
        assert owners.device.data is cache

    async def test_s1_ticket_timeline_keeps_the_earlier_outlet(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A stale GETTING_READY poll cannot revert the earlier outlet."""
        owners = seeded_owners
        await _seed_complete_outlet_poll(owners)
        controller = self._wire(owners)

        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)
        assert controller.puts == [_outlet_body({2: {"relay_state": True}})]

        stale = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        for row in stale["outlet_table"] + stale["outlet_overrides"]:
            if row["index"] == 2:
                row["relay_state"] = False
        await self._poll(owners, stale, state="GETTING_READY")
        assert _outlet(owners, 2, "relay_state") == (True, True)

        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)
        assert controller.puts[-1] == _outlet_body(
            {1: {"relay_state": False}, 2: {"relay_state": True}}
        )
        assert COMPLETE_OUTLET_DEVICE == SAMPLE_PDU_LEGACY_DEVICE_COMPLETE

    async def test_s2_partial_poll_blocks_until_a_complete_poll(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A poll missing one sibling rejects the next write; a full poll recovers."""
        owners = seeded_owners
        await _seed_complete_outlet_poll(owners)
        controller = self._wire(owners)
        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)

        partial = _complete_without(3)
        for row in partial["outlet_table"] + partial["outlet_overrides"]:
            if row["index"] == 2:
                row["relay_state"] = False
        await self._poll(owners, partial)
        assert _outlet(owners, 2, "relay_state") == (True, True)

        await self._assert_rejected(owners, controller, 3)
        assert (SITE_ID, PDU_ID, 3) not in owners.device._pending_outlet_writes

        fresh = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        for row in fresh["outlet_table"] + fresh["outlet_overrides"]:
            if row["index"] == 3:
                row.update(name="Fresh Switch", relay_state=False, cycle_enabled=True)
        await self._poll(owners, fresh)

        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 3, state=True)
        assert controller.puts[-1] == _outlet_body(
            {
                2: {"relay_state": True},
                3: {
                    "name": "Fresh Switch",
                    "relay_state": True,
                    "cycle_enabled": True,
                },
            }
        )

    async def test_s3_expiry_releases_the_overlay_not_write_eligibility(
        self, seeded_owners: SeededOwners
    ) -> None:
        """The 60 s timeout releases the field; only a complete poll unblocks writes."""
        owners = seeded_owners
        await _seed_complete_outlet_poll(owners)
        controller = self._wire(owners)
        with patch(DEVICE_MONOTONIC, return_value=1000.0):
            await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)

        partial = _complete_without(3)
        for row in partial["outlet_table"] + partial["outlet_overrides"]:
            if row["index"] == 2:
                row["relay_state"] = False
        with patch(DEVICE_MONOTONIC, return_value=1000.0):
            await self._poll(owners, partial)
        assert _outlet(owners, 2, "relay_state") == (True, True)

        with patch(DEVICE_MONOTONIC, return_value=1000.0 + OUTLET_WRITE_GUARD_SECONDS):
            await self._poll(owners, partial)
        assert _outlet(owners, 2, "relay_state") == (False, False)

        await self._assert_rejected(owners, controller, 3)

        with patch(DEVICE_MONOTONIC, return_value=1000.0 + OUTLET_WRITE_GUARD_SECONDS):
            await self._poll(owners, copy.deepcopy(COMPLETE_OUTLET_DEVICE))
            await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 3, state=True)
        assert controller.puts[-1] == _outlet_body({3: {"relay_state": True}})
        assert controller.puts[-1][1]["relay_state"] is False

    async def test_s4_confirmation_in_a_partial_poll_does_not_authorise(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Confirming a field in a partial poll releases it but keeps the gate shut."""
        owners = seeded_owners
        await _seed_complete_outlet_poll(owners)
        controller = self._wire(owners)
        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)

        partial = _complete_without(3)
        for row in partial["outlet_table"] + partial["outlet_overrides"]:
            if row["index"] == 2:
                row["relay_state"] = True
        await self._poll(owners, partial)
        assert _outlet(owners, 2, "relay_state") == (True, True)
        assert (SITE_ID, PDU_ID, 2) not in owners.device._pending_outlet_writes

        await self._assert_rejected(owners, controller, 3)

        await self._poll(owners, copy.deepcopy(COMPLETE_OUTLET_DEVICE))
        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 3, state=True)
        assert controller.puts[-1] == _outlet_body({3: {"relay_state": True}})

    async def test_s5_shrinking_set_blocks_writes_until_restored(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A poll that drops a required outlet cannot become the baseline."""
        owners = seeded_owners
        await _seed_complete_outlet_poll(owners)
        controller = self._wire(owners)

        four = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        four["outlet_table"].append(
            {
                "index": 4,
                "name": "Outlet 4",
                "relay_state": False,
                "cycle_enabled": True,
            }
        )
        four["outlet_overrides"].append(
            {
                "index": 4,
                "name": "Outlet 4",
                "relay_state": False,
                "cycle_enabled": True,
            }
        )
        await self._poll(owners, four)
        await self._poll(owners, _complete_without(4))
        await self._assert_rejected(owners, controller, 2)

        await self._poll(owners, four)
        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)
        assert controller.puts[-1] == _device_outlet_body(
            four, {2: {"relay_state": True}}
        )

    async def test_s6_second_write_before_a_poll_is_complete(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Two writes without a poll are both complete; the second keeps the first."""
        owners = seeded_owners
        await _seed_complete_outlet_poll(owners)
        controller = self._wire(owners)

        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)
        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)

        assert controller.puts == [
            _outlet_body({2: {"relay_state": True}}),
            _outlet_body({1: {"relay_state": False}, 2: {"relay_state": True}}),
        ]

    async def test_cold_start_sparse_overrides_is_rejected(
        self, seeded_owners: SeededOwners
    ) -> None:
        """The seeded sparse fixture is not a complete baseline."""
        controller = self._wire(seeded_owners)

        await self._assert_rejected(seeded_owners, controller, 1)
        assert owners_pending(seeded_owners) == {}

    async def test_cold_start_empty_overrides_seeds_from_the_table(
        self, seeded_owners: SeededOwners
    ) -> None:
        """An empty override array with a complete table sends the seeded payload."""
        owners = seeded_owners
        device = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        device["outlet_overrides"] = []
        await self._poll(owners, device)
        controller = self._wire(owners)

        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)

        assert controller.puts == [_seeded_body(2, state=True)]
        assert _outlet(owners, 2, "relay_state") == (True, True)

    async def test_cold_start_absent_overrides_seeds_from_the_table(
        self, seeded_owners: SeededOwners
    ) -> None:
        """An absent override array is the same valid table-seeded cold start."""
        owners = seeded_owners
        device = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        device.pop("outlet_overrides")
        await self._poll(owners, device)
        controller = self._wire(owners)

        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)

        assert controller.puts == [_seeded_body(2, state=True)]

    async def test_override_missing_a_table_carried_field_is_rejected(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A baseline row must keep ``name``/``cycle_enabled`` the table carries."""
        owners = seeded_owners
        device = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        device["outlet_overrides"][1].pop("name")
        await self._poll(owners, device)
        controller = self._wire(owners)

        await self._assert_rejected(owners, controller, 2)

    async def test_cold_start_override_without_relay_state_is_rejected(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A row without a real relay state cannot become a baseline."""
        owners = seeded_owners
        device = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        for row in device["outlet_overrides"]:
            row.pop("relay_state")
        await self._poll(owners, device)
        controller = self._wire(owners)

        await self._assert_rejected(owners, controller, 2)

    @pytest.mark.parametrize("missing", [True, False])
    async def test_cold_start_missing_or_non_list_arrays_are_rejected(
        self, seeded_owners: SeededOwners, *, missing: bool
    ) -> None:
        """No array at all, or a non-list, can never authorise a write."""
        owners = seeded_owners
        device = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        if missing:
            device.pop("outlet_table")
            device.pop("outlet_overrides")
        else:
            device["outlet_table"] = None
            device["outlet_overrides"] = "not-a-list"
        await self._poll(owners, device)
        controller = self._wire(owners)

        await self._assert_rejected(owners, controller, 1)
        assert owners_pending(owners) == {}

    async def test_cold_start_21_overrides_over_20_table_sends_all_21(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A valid superset is trusted and sends every override row."""
        owners = seeded_owners
        superset = copy.deepcopy(SAMPLE_PDU_LEGACY_DEVICE_SUPERSET)
        await self._poll(owners, superset)
        controller = _FakePduController(superset)
        _wire_pdu_controller(owners, controller)
        owners.device.async_request_refresh = AsyncMock()

        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)

        assert len(controller.puts) == 1
        assert len(controller.puts[0]) == 21
        assert controller.puts[0] == _device_outlet_body(
            superset, {2: {"relay_state": True}}
        )

    async def test_malformed_poll_after_trust_blocks_then_recovers(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Duplicate, non-list, alias-only and bad indexes all fail closed."""
        owners = seeded_owners
        await _seed_complete_outlet_poll(owners)
        controller = self._wire(owners)

        duplicate = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        duplicate["outlet_overrides"].append(
            copy.deepcopy(duplicate["outlet_overrides"][1])
        )
        non_list = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        non_list["outlet_overrides"] = "not-a-list"
        alias_only = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        for row in alias_only["outlet_overrides"]:
            row["outletIdx"] = row.pop("index")
        bad_index = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        bad_index["outlet_overrides"][0]["index"] = "bad_index"
        junk_row = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        junk_row["outlet_overrides"].insert(0, "junk")

        for device in (duplicate, non_list, alias_only, bad_index, junk_row):
            await self._poll(owners, device)
            await self._assert_rejected(owners, controller, 2)

        await self._poll(owners, copy.deepcopy(COMPLETE_OUTLET_DEVICE))
        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)
        assert controller.puts[-1] == _outlet_body({2: {"relay_state": True}})
        assert len(controller.puts) == 1

    async def test_appended_saved_entry_never_authorises_a_write(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Display may append the pending entry; provenance still blocks the PUT."""
        owners = seeded_owners
        await _seed_complete_outlet_poll(owners)
        controller = self._wire(owners)
        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)

        await self._poll(owners, _complete_without(2, 3))

        appended = _pdu(owners)["outlet_overrides"]
        assert [row["index"] for row in appended] == [1, 2]
        assert appended[1]["relay_state"] is True
        await self._assert_rejected(owners, controller, 3)

    async def test_unknown_target_index_is_rejected(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A target outside the required identity set cannot widen the payload."""
        owners = seeded_owners
        await _seed_complete_outlet_poll(owners)
        controller = self._wire(owners)
        before = copy.deepcopy(owners.device.data["devices"][SITE_ID][PDU_ID])

        await self._assert_rejected(owners, controller, 99)

        assert owners.device.data["devices"][SITE_ID][PDU_ID] == before

    async def test_changed_classic_id_rebuilds_and_sparse_new_identity_blocks(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A new classic ``_id`` starts cold; only a complete new poll is trusted."""
        owners = seeded_owners
        await _seed_complete_outlet_poll(owners)
        controller = self._wire(owners)

        changed = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        changed["_id"] = "60a1b2c3d4e5f67890123457"
        await self._poll(owners, changed)
        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)
        assert controller.puts[-1] == _outlet_body({2: {"relay_state": True}})

        sparse = copy.deepcopy(SAMPLE_PDU_LEGACY_DEVICE)
        sparse["_id"] = changed["_id"]
        await self._poll(owners, sparse)
        await self._assert_rejected(owners, controller, 2)

    async def test_source_switch_resets_provenance(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A v1-to-legacy-primary switch is a new identity and needs its own poll."""
        owners = seeded_owners
        await _seed_complete_outlet_poll(owners)
        controller = self._wire(owners)
        devices = owners.network_client.devices
        devices.get_all.side_effect = UniFiResponseError("offline", status_code=502)
        devices.get_legacy_site_devices.side_effect = None
        devices.get_legacy_site_devices.return_value = [
            copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        ]
        await owners.device.async_refresh()

        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)
        assert controller.puts[-1] == _outlet_body({2: {"relay_state": True}})

        devices.get_legacy_site_devices.return_value = [
            copy.deepcopy(SAMPLE_PDU_LEGACY_DEVICE)
        ]
        await owners.device.async_refresh()
        await self._assert_rejected(owners, controller, 2)

    async def test_removed_and_readded_device_starts_cold(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Removal deletes provenance; the reappearing device needs a complete poll."""
        owners = seeded_owners
        await _seed_complete_outlet_poll(owners)
        controller = self._wire(owners)
        devices = owners.network_client.devices

        devices.get_all.return_value = []
        devices.get_legacy_site_devices.side_effect = None
        devices.get_legacy_site_devices.return_value = []
        await owners.device.async_refresh()
        assert owners.device._outlet_payload_provenance == {}

        await self._poll(owners, copy.deepcopy(SAMPLE_PDU_LEGACY_DEVICE))
        await self._assert_rejected(owners, controller, 2)

    async def test_legacy_primary_without_arrays_creates_none(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Legacy-primary raw data is judged as polled; no array is invented."""
        owners = seeded_owners
        devices = owners.network_client.devices
        no_arrays = copy.deepcopy(SAMPLE_PDU_LEGACY_DEVICE)
        no_arrays.pop("outlet_table")
        no_arrays.pop("outlet_overrides")
        devices.get_all.side_effect = UniFiResponseError("offline", status_code=502)
        devices.get_legacy_site_devices.side_effect = None
        devices.get_legacy_site_devices.return_value = [no_arrays]
        await owners.device.async_refresh()
        controller = self._wire(owners)

        published = owners.device.data["devices"][SITE_ID][PDU_ID]
        assert "outlet_table" not in published
        assert "outlet_overrides" not in published
        await self._assert_rejected(owners, controller, 1)

    async def test_fresh_polled_fields_stay_authoritative(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Non-pending fields update; the next complete body carries them."""
        owners = seeded_owners
        await _seed_complete_outlet_poll(owners)
        controller = self._wire(owners)
        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)

        polled = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        polled["outlet_ac_power_consumption"] = "333.00"
        for row in polled["outlet_table"] + polled["outlet_overrides"]:
            if row["index"] == 2:
                row["relay_state"] = False
            if row["index"] == 3:
                row.update(name="Fresh Switch", relay_state=False, cycle_enabled=True)
        await self._poll(owners, polled, state="GETTING_READY")

        pdu = _pdu(owners)
        assert pdu["state"] == "GETTING_READY"
        assert pdu["outlet_ac_power_consumption"] == 333.0
        assert _outlet(owners, 2, "relay_state") == (True, True)
        assert _outlet(owners, 3, "relay_state") == (False, False)
        assert _outlet(owners, 3, "name") == ("Fresh Switch", "Fresh Switch")

        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)
        assert controller.puts[-1] == _outlet_body(
            {
                1: {"relay_state": False},
                2: {"relay_state": True},
                3: {
                    "name": "Fresh Switch",
                    "relay_state": False,
                    "cycle_enabled": True,
                },
            }
        )

    async def test_failed_refresh_keeps_pending_and_provenance(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A failed refresh commits neither map; the next write stays complete."""
        owners = seeded_owners
        await _seed_complete_outlet_poll(owners)
        controller = self._wire(owners)
        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)
        provenance = owners.device._outlet_payload_provenance

        owners.network_client.devices.get_all.side_effect = UniFiConnectionError("down")
        await owners.device.async_refresh()
        assert owners.device.last_update_success is False
        assert owners.device._outlet_payload_provenance is provenance
        assert (SITE_ID, PDU_ID, 2) in owners.device._pending_outlet_writes

        owners.network_client.devices.get_all.side_effect = None
        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)
        assert controller.puts[-1] == _outlet_body(
            {1: {"relay_state": False}, 2: {"relay_state": True}}
        )

    async def test_failed_write_records_nothing_and_keeps_provenance(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A transport failure leaves both maps alone."""
        owners = seeded_owners
        await _seed_complete_outlet_poll(owners)
        controller = self._wire(owners)
        devices = owners.network_client.devices
        send = devices.set_outlet_state.side_effect
        devices.set_outlet_state.side_effect = UniFiConnectionError("down")
        provenance = owners.device._outlet_payload_provenance

        with pytest.raises(HomeAssistantError):
            await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)

        assert (SITE_ID, PDU_ID, 2) not in owners.device._pending_outlet_writes
        assert owners.device._outlet_payload_provenance is provenance
        assert controller.puts == []

        devices.set_outlet_state.side_effect = send
        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)
        assert controller.puts == [_outlet_body({2: {"relay_state": True}})]


def owners_pending(owners: SeededOwners) -> dict[Any, Any]:
    """Return the device owner's committed pending writes."""
    return owners.device._pending_outlet_writes


class TestOutletPayloadRawBoundary:
    """The gate judges untouched controller arrays, not the display model."""

    @pytest.mark.parametrize(
        "shape",
        [
            "missing_relay",
            "junk_row",
            "float_index",
            "bool_index",
            "outlet_alias_only",
            "zero_alias",
        ],
    )
    async def test_raw_defects_fail_closed(
        self, seeded_owners: SeededOwners, shape: str
    ) -> None:
        """Raw shapes the display normalizer would hide cannot authorise a PUT."""
        owners = seeded_owners
        raw = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        raw["outlet_overrides"] = []
        row = raw["outlet_table"][0]
        if shape == "missing_relay":
            raw["outlet_table"][2].pop("relay_state")
        elif shape == "junk_row":
            raw["outlet_table"].append("junk")
        elif shape == "float_index":
            row["index"] = 1.5
        elif shape == "bool_index":
            row["index"] = True
        elif shape == "outlet_alias_only":
            for outlet in raw["outlet_table"]:
                outlet["outletIdx"] = outlet.pop("index")
        elif shape == "zero_alias":
            row.pop("index")
            row["outlet_idx"] = 0
        _serve(owners, raw)
        await owners.device.async_refresh()
        provider = owners.device._outlet_payload_provenance[SITE_ID, PDU_ID]
        if shape == "zero_alias":
            # Index 0 is structurally valid in the raw table, but the display
            # normalizer drops it, so the transport payload cannot cover it:
            # the gate fails closed below instead of silently omitting it.
            assert provider.trusted is True
            assert 0 in provider.required_indexes
        else:
            assert provider.trusted is False
        controller = _FakePduController()
        _wire_pdu_controller(owners, controller)
        owners.device.async_request_refresh = AsyncMock()

        await TestOutletPayloadSafety._assert_rejected(owners, controller, 2)

    async def test_raw_optional_absence_is_not_a_defect(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Raw rows without optional keys are complete, not normalized defaults."""
        owners = seeded_owners
        raw = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        for outlet in raw["outlet_table"]:
            outlet.pop("cycle_enabled")
            outlet.pop("name")
        raw["outlet_overrides"] = [
            {"index": outlet["index"], "relay_state": outlet["relay_state"]}
            for outlet in raw["outlet_table"]
        ]
        _serve(owners, raw)
        await owners.device.async_refresh()
        provider = owners.device._outlet_payload_provenance[SITE_ID, PDU_ID]
        assert provider.trusted is True
        assert provider.required_row_fields == frozenset()
        controller = _FakePduController()
        _wire_pdu_controller(owners, controller)
        owners.device.async_request_refresh = AsyncMock()

        await owners.device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)

        assert controller.puts == [
            [
                {"index": 1, "relay_state": True},
                {"index": 2, "relay_state": True},
                {"index": 3, "relay_state": True},
            ]
        ]


class TestOutletPayloadGate:
    """Pure contract of the pre-PUT gate, including every rejection path."""

    @staticmethod
    def _provenance(**overrides: Any) -> OutletPayloadProvenance:
        values: dict[str, Any] = {
            "site_id": SITE_ID,
            "device_id": PDU_ID,
            "classic_id": COMPLETE_OUTLET_DEVICE["_id"],
            "mac": "00:11:22:33:44:55",
            "source": "v1",
            "required_indexes": frozenset({1, 2, 3}),
            "required_row_fields": frozenset(
                {
                    (1, "name"),
                    (1, "cycle_enabled"),
                    (2, "name"),
                    (2, "cycle_enabled"),
                    (3, "name"),
                    (3, "cycle_enabled"),
                }
            ),
            "trusted": True,
        }
        values.update(overrides)
        return OutletPayloadProvenance(**values)

    @staticmethod
    def _safe(
        provenance: OutletPayloadProvenance | None,
        *,
        device: Mapping[str, Any] = COMPLETE_OUTLET_DEVICE,
        sent: list[dict[str, Any]] | None = None,
        outlet_index: int = 2,
    ) -> bool:
        return outlet_payload_is_safe(
            provenance,
            site_id=SITE_ID,
            device_id=PDU_ID,
            device=device,
            sent=_outlet_body() if sent is None else sent,
            outlet_index=outlet_index,
        )

    def test_accepts_a_complete_trusted_payload(self) -> None:
        """The happy path is a complete body for the trusted raw set."""
        assert self._safe(self._provenance()) is True

    @pytest.mark.parametrize(
        ("overrides", "device", "sent", "outlet_index"),
        [
            ({"trusted": False}, COMPLETE_OUTLET_DEVICE, None, 2),
            ({"site_id": "other"}, COMPLETE_OUTLET_DEVICE, None, 2),
            ({"device_id": "other"}, COMPLETE_OUTLET_DEVICE, None, 2),
            ({"classic_id": "other"}, COMPLETE_OUTLET_DEVICE, None, 2),
            ({"mac": "aa:bb:cc:dd:ee:ff"}, COMPLETE_OUTLET_DEVICE, None, 2),
            ({}, COMPLETE_OUTLET_DEVICE, "malformed", 2),
            ({}, COMPLETE_OUTLET_DEVICE, [], 2),
            ({}, COMPLETE_OUTLET_DEVICE, None, 9),
            (
                {},
                COMPLETE_OUTLET_DEVICE,
                [{"index": 1, "relay_state": True, "name": "A"}],
                1,
            ),
            (
                {},
                COMPLETE_OUTLET_DEVICE,
                [{"index": index, "relay_state": None} for index in (1, 2, 3)],
                2,
            ),
            (
                {},
                COMPLETE_OUTLET_DEVICE,
                [
                    {
                        "index": 1,
                        "relay_state": True,
                        "name": "A",
                        "cycle_enabled": True,
                    },
                    {"index": 2, "relay_state": True, "cycle_enabled": True},
                    {
                        "index": 3,
                        "relay_state": True,
                        "name": "C",
                        "cycle_enabled": True,
                    },
                ],
                2,
            ),
        ],
    )
    def test_rejects_every_unsafe_shape(
        self,
        overrides: dict[str, Any],
        device: Mapping[str, Any],
        sent: Any,
        outlet_index: int,
    ) -> None:
        """Each guard fails closed on an unsafe contract value."""
        assert (
            self._safe(
                self._provenance(**overrides),
                device=device,
                sent=sent,
                outlet_index=outlet_index,
            )
            is False
        )

    def test_rejects_without_provenance(self) -> None:
        """A device with no provenance record cannot write."""
        assert self._safe(None) is False

    def test_rejects_when_no_payload_can_be_built(self) -> None:
        """A trusted device whose builder returns ``None`` cannot write."""
        assert (
            outlet_payload_is_safe(
                self._provenance(),
                site_id=SITE_ID,
                device_id=PDU_ID,
                device=COMPLETE_OUTLET_DEVICE,
                sent=None,
                outlet_index=2,
            )
            is False
        )


class TestOutletPayloadProvenance:
    """Pure provenance branches reached by real controller shapes."""

    @pytest.mark.parametrize(
        "rows",
        [
            ["junk"],
            [{"index": True, "relay_state": True}],
            [{"index": 1.5, "relay_state": True}],
            [{"outletIdx": 1, "relay_state": True}],
            [{"relay_state": True}],
            [{"index": 1}, {"index": 1}],
        ],
    )
    def test_malformed_rows_are_untrusted(self, rows: list[Any]) -> None:
        """Non-dicts, bools, floats, alias-only, missing and duplicate indexes fail."""
        record = evaluate_outlet_payload_provenance(
            None,
            SITE_ID,
            PDU_ID,
            "v1",
            device={"outlet_table": rows, "outlet_overrides": []},
            raw_outlet_arrays={"outlet_table": rows, "outlet_overrides": []},
        )
        assert record.trusted is False
        assert record.required_indexes == frozenset()

    def test_override_without_relay_state_is_untrusted(self) -> None:
        """A baseline row without a real relay state fails the structural rule."""
        raw = {
            "outlet_table": [{"index": 1, "relay_state": True}],
            "outlet_overrides": [{"index": 1, "name": "Outlet 1"}],
        }
        record = evaluate_outlet_payload_provenance(
            None, SITE_ID, PDU_ID, "v1", device=raw, raw_outlet_arrays=raw
        )
        assert record.trusted is False

    def test_override_missing_a_table_carried_field_is_untrusted(self) -> None:
        """A table-carried ``name``/``cycle_enabled`` must survive in the baseline."""
        raw = {
            "outlet_table": [{"index": 1, "name": "Outlet 1", "relay_state": True}],
            "outlet_overrides": [{"index": 1, "relay_state": True}],
        }
        record = evaluate_outlet_payload_provenance(
            None, SITE_ID, PDU_ID, "v1", device=raw, raw_outlet_arrays=raw
        )
        assert record.trusted is False

    def test_table_seed_needs_a_real_relay_state(self) -> None:
        """A table row without a relay state cannot seed a payload."""
        raw = {
            "outlet_table": [{"index": 1, "name": "Outlet 1"}],
            "outlet_overrides": [],
        }
        record = evaluate_outlet_payload_provenance(
            None, SITE_ID, PDU_ID, "v1", device=raw, raw_outlet_arrays=raw
        )
        assert record.trusted is False

    def test_untrusted_poll_keeps_required_set_and_identity_change_discards_it(
        self,
    ) -> None:
        """A malformed poll cannot grow or shrink the set; a new identity resets it."""
        trusted = evaluate_outlet_payload_provenance(
            None,
            SITE_ID,
            PDU_ID,
            "v1",
            device=COMPLETE_OUTLET_DEVICE,
            raw_outlet_arrays=COMPLETE_OUTLET_DEVICE,
        )
        assert trusted.trusted is True
        assert trusted.required_indexes == frozenset({1, 2, 3})

        malformed = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        malformed["outlet_overrides"][0]["index"] = "bad"
        after = evaluate_outlet_payload_provenance(
            trusted,
            SITE_ID,
            PDU_ID,
            "v1",
            device=malformed,
            raw_outlet_arrays=malformed,
        )
        assert after.trusted is False
        assert after.required_indexes == trusted.required_indexes

        new_identity = copy.deepcopy(malformed)
        new_identity["_id"] = "other"
        cold = evaluate_outlet_payload_provenance(
            trusted,
            SITE_ID,
            PDU_ID,
            "v1",
            device=new_identity,
            raw_outlet_arrays=new_identity,
        )
        assert cold.required_indexes == frozenset()
        assert cold.classic_id == "other"

        class_unchanged = OutletPayloadProvenance(
            site_id=SITE_ID,
            device_id=PDU_ID,
            classic_id=COMPLETE_OUTLET_DEVICE["_id"],
            mac="00:11:22:33:44:55",
            source="v1",
            required_indexes=frozenset({1, 2, 3}),
            required_row_fields=frozenset(),
            trusted=True,
        )
        assert class_unchanged.trusted is True


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
        require_mock(coordinator.network_client.devices.get_all).side_effect = error
        for _ in range(4):
            await coordinator.async_refresh()
            assert not coordinator.last_update_success
        outage = f"Error fetching {DOMAIN}_device data: {detail}"
        assert [
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        ] == [outage]
        require_mock(coordinator.network_client.devices.get_all).side_effect = None
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        recovery = f"Fetching {DOMAIN}_device data recovered"
        assert caplog.messages.count(recovery) == 1
        require_mock(coordinator.network_client.devices.get_all).side_effect = error
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
        require_mock(endpoint.get_all).side_effect = UniFiResponseError(
            "offline", status_code=502
        )
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
        require_mock(endpoint.get_all).side_effect = None
        for _ in range(2):
            await coordinator._process_site("default", legacy_site_name="default")
        assert caplog.messages.count(recovery) == 1
        assert (
            next(r.levelno for r in caplog.records if r.getMessage() == recovery)
            == logging.INFO
        )

        require_mock(endpoint.get_all).side_effect = UniFiResponseError(
            "offline", status_code=502
        )
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

        devices_dict, stats_dict, clients_dict, *_ = await coordinator._process_site(
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

        require_mock(
            coordinator.network_client.devices.get_legacy_site_devices
        ).assert_not_awaited()

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
        coordinator._site_sources = {"gone": "v1"}
        coordinator._site_tracked_devices = {"gone": {"dev-gone": "aa:bb:cc:dd:ee:ff"}}

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
        coordinator._site_sources = {"default": "v1"}
        coordinator._site_tracked_devices = {
            "default": {"device1": "aa:bb:cc:dd:ee:01"}
        }
        coordinator.config_coordinator.data["sites"] = {}

        with patch.object(coordinator, "_cleanup_stale_devices") as cleanup:
            await coordinator._async_update_data()

        for key in ("devices", "stats", "clients"):
            assert coordinator.data[key] == {}
        # An empty list is also what a transient API failure looks like.
        cleanup.assert_not_called()
        assert coordinator._site_tracked_devices == {
            "default": {"device1": "aa:bb:cc:dd:ee:01"}
        }

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

        require_mock(
            coordinator.network_client.devices.get_statistics
        ).assert_not_awaited()
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
        require_mock(
            coordinator.network_client.sites.get_legacy_all
        ).assert_not_called()

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

        require_mock(
            coordinator.network_client.sites.get_legacy_all
        ).assert_not_called()
        require_mock(
            coordinator.network_client.devices.get_legacy_site_devices
        ).assert_awaited_with("renamed")

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
        """A genuinely removed device is removed only after N consecutive polls."""
        dev_reg = dr.async_get(hass)
        require_instance(coordinator.config_entry, MockConfigEntry).add_to_hass(hass)
        entry_id = coordinator.config_entry.entry_id
        id1 = (DOMAIN, "default_device1")
        id2 = (DOMAIN, "default_device2")
        dev1 = dev_reg.async_get_or_create(
            config_entry_id=entry_id,
            identifiers={id1},
            connections={(CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:01")},
        )
        dev2 = dev_reg.async_get_or_create(
            config_entry_id=entry_id,
            identifiers={id2},
            connections={(CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:02")},
        )
        assert entry_id in device_config_entry_ids(dev1)
        assert entry_id in device_config_entry_ids(dev2)

        coordinator._site_sources = {"default": "v1"}
        coordinator._site_tracked_devices = {
            "default": {
                "device1": "aa:bb:cc:dd:ee:01",
                "device2": "aa:bb:cc:dd:ee:02",
            }
        }
        coordinator._site_aliases = {
            "default": {
                "aa:bb:cc:dd:ee:01": "device1",
                "aa:bb:cc:dd:ee:02": "device2",
            }
        }
        coordinator.data["devices"] = {
            "default": {"device1": {"id": "device1", "mac": "aa:bb:cc:dd:ee:01"}}
        }
        site_sources = {"default": "v1"}
        current_macs = {"default": {"device1": "aa:bb:cc:dd:ee:01"}}

        # Polls 1 and 2: device2 is absent but threshold (3) is not yet reached
        coordinator._cleanup_stale_devices(site_sources, current_macs)
        assert get_device_by_identifier(dev_reg, id2, entry_id) is not None
        assert coordinator._site_absence_counts.get("default", {}).get("device2") == 1

        coordinator._cleanup_stale_devices(site_sources, current_macs)
        assert get_device_by_identifier(dev_reg, id2, entry_id) is not None
        assert coordinator._site_absence_counts.get("default", {}).get("device2") == 2

        # Poll 3: absence threshold reached; device2 is removed and its alias pruned
        coordinator._cleanup_stale_devices(site_sources, current_macs)
        reg_dev2 = get_device_by_identifier(dev_reg, id2, entry_id)
        assert reg_dev2 is None or entry_id not in device_config_entry_ids(reg_dev2)
        assert "device2" not in coordinator._site_tracked_devices.get("default", {})
        assert (
            coordinator._site_absence_counts.get("default", {}).get("device2") is None
        )
        assert "aa:bb:cc:dd:ee:02" not in coordinator._site_aliases.get("default", {})
        # device1 is retained
        reg_dev1 = get_device_by_identifier(dev_reg, id1, entry_id)
        assert reg_dev1 is not None
        assert entry_id in device_config_entry_ids(reg_dev1)
        assert (
            coordinator._site_aliases.get("default", {}).get("aa:bb:cc:dd:ee:01")
            == "device1"
        )

    @pytest.mark.asyncio
    async def test_cleanup_stale_devices_prunes_disappeared_site_state(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ) -> None:
        """Drop a disappeared site's state while continuing active-site tracking."""
        coordinator._site_sources = {"default": "v1", "gone": "legacy"}
        coordinator._site_aliases = {
            "default": {
                "aa:bb:cc:dd:ee:01": "device1",
                "aa:bb:cc:dd:ee:02": "device2",
            },
            "gone": {"aa:bb:cc:dd:ee:99": "gone-device"},
        }
        coordinator._site_tracked_devices = {
            "default": {
                "device1": "aa:bb:cc:dd:ee:01",
                "device2": "aa:bb:cc:dd:ee:02",
            },
            "gone": {"gone-device": "aa:bb:cc:dd:ee:99"},
        }
        coordinator._site_absence_counts = {
            "default": {"device1": 1, "device2": 1},
            "gone": {"gone-device": 2},
        }
        coordinator.data["devices"] = {
            "default": {"device1": {"id": "device1", "mac": "aa:bb:cc:dd:ee:01"}}
        }

        coordinator._cleanup_stale_devices(
            site_sources={"default": "v1"},
            site_device_macs={"default": {"device1": "aa:bb:cc:dd:ee:01"}},
        )

        assert coordinator._site_sources == {"default": "v1"}
        assert coordinator._site_aliases == {
            "default": {
                "aa:bb:cc:dd:ee:01": "device1",
                "aa:bb:cc:dd:ee:02": "device2",
            }
        }
        assert coordinator._site_tracked_devices == {
            "default": {
                "device1": "aa:bb:cc:dd:ee:01",
                "device2": "aa:bb:cc:dd:ee:02",
            }
        }
        assert coordinator._site_absence_counts == {"default": {"device2": 2}}

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
        """Cleanup handles missing registry device gracefully after N polls."""
        coordinator._site_sources = {"default": "v1"}
        coordinator._site_tracked_devices = {
            "default": {
                "device1": "aa:bb:cc:dd:ee:01",
                "stale_device": "aa:bb:cc:dd:ee:99",
            }
        }
        coordinator.data["devices"] = {
            "default": {"device1": {"id": "device1", "mac": "aa:bb:cc:dd:ee:01"}}
        }
        site_sources = {"default": "v1"}
        current_macs = {"default": {"device1": "aa:bb:cc:dd:ee:01"}}

        # Run 3 polls to hit the absence threshold
        for _ in range(3):
            coordinator._cleanup_stale_devices(site_sources, current_macs)

        assert "stale_device" not in coordinator._site_tracked_devices.get(
            "default", {}
        )

    @pytest.mark.asyncio
    async def test_cleanup_v1_to_legacy_flip_removes_nothing(
        self, hass: HomeAssistant, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """A flip from v1 to legacy fallback removes no devices
        even if identifiers differ."""
        dev_reg = dr.async_get(hass)
        require_instance(coordinator.config_entry, MockConfigEntry).add_to_hass(hass)
        entry_id = coordinator.config_entry.entry_id
        dev_id = (DOMAIN, "default_old_dev")
        dev = dev_reg.async_get_or_create(
            config_entry_id=entry_id,
            identifiers={dev_id},
            connections={(CONNECTION_NETWORK_MAC, "00:11:22:33:44:55")},
        )
        assert entry_id in device_config_entry_ids(dev)

        coordinator._site_sources = {"default": "v1"}
        coordinator._site_tracked_devices = {
            "default": {"old_dev": "00:11:22:33:44:55"}
        }
        coordinator._site_aliases = {"default": {"00:11:22:33:44:55": "old_dev"}}

        coordinator.data["devices"] = {
            "default": {
                "other_dev": {
                    "id": "other_dev",
                    "_id": "legacy_obj_id",
                    "mac": "aa:bb:cc:dd:ee:01",
                }
            }
        }

        # Alternating sources on each poll past the 3-poll threshold (6 polls)
        # to prove continuous flips prevent removal indefinitely.
        for i in range(6):
            source = "legacy" if i % 2 == 0 else "v1"
            coordinator._cleanup_stale_devices(
                site_sources={"default": source},
                site_device_macs={"default": {"other_dev": "aa:bb:cc:dd:ee:01"}},
            )

        reg_dev = get_device_by_identifier(dev_reg, dev_id, entry_id)
        assert reg_dev is not None
        assert entry_id in device_config_entry_ids(reg_dev)
        assert "old_dev" in coordinator._site_tracked_devices.get("default", {})

    @pytest.mark.asyncio
    async def test_cleanup_legacy_to_v1_flip_removes_nothing(
        self, hass: HomeAssistant, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """A flip from legacy fallback back to v1 removes no devices
        even if identifiers differ."""
        dev_reg = dr.async_get(hass)
        require_instance(coordinator.config_entry, MockConfigEntry).add_to_hass(hass)
        entry_id = coordinator.config_entry.entry_id
        dev_id = (DOMAIN, "default_old_dev")
        dev = dev_reg.async_get_or_create(
            config_entry_id=entry_id,
            identifiers={dev_id},
            connections={(CONNECTION_NETWORK_MAC, "00:11:22:33:44:55")},
        )
        assert entry_id in device_config_entry_ids(dev)

        coordinator._site_sources = {"default": "legacy"}
        coordinator._site_tracked_devices = {
            "default": {"old_dev": "00:11:22:33:44:55"}
        }

        coordinator.data["devices"] = {
            "default": {
                "other_dev": {
                    "id": "other_dev",
                    "mac": "aa:bb:cc:dd:ee:01",
                }
            }
        }

        # Alternating sources on each poll past the 3-poll threshold (6 polls)
        # to prove continuous flips prevent removal indefinitely.
        for i in range(6):
            source = "v1" if i % 2 == 0 else "legacy"
            coordinator._cleanup_stale_devices(
                site_sources={"default": source},
                site_device_macs={"default": {"other_dev": "aa:bb:cc:dd:ee:01"}},
            )

        reg_dev = get_device_by_identifier(dev_reg, dev_id, entry_id)
        assert reg_dev is not None
        assert entry_id in device_config_entry_ids(reg_dev)
        assert "old_dev" in coordinator._site_tracked_devices.get("default", {})

    @pytest.mark.asyncio
    async def test_cleanup_empty_site_polls_remove_nothing(
        self, hass: HomeAssistant, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """Empty site response and missing site data remove no devices."""
        coordinator._site_sources = {"default": "v1"}
        coordinator._site_tracked_devices = {
            "default": {
                "dev1": "aa:bb:cc:dd:ee:01",
                "dev2": "aa:bb:cc:dd:ee:02",
            }
        }

        with patch.object(coordinator, "_remove_stale_device") as mock_remove:
            # 1. Empty site poll
            coordinator.data["devices"] = {"default": {}}
            for _ in range(3):
                coordinator._cleanup_stale_devices(
                    site_sources={"default": "v1"},
                    site_device_macs={"default": {}},
                )

            mock_remove.assert_not_called()
            assert "dev1" in coordinator._site_tracked_devices.get("default", {})
            assert "dev2" in coordinator._site_tracked_devices.get("default", {})

            # 2. Empty devices dictionary (site omitted from data)
            coordinator.data["devices"] = {}
            for _ in range(3):
                coordinator._cleanup_stale_devices(
                    site_sources={"default": "v1"},
                    site_device_macs={},
                )

            mock_remove.assert_not_called()
            assert "dev1" in coordinator._site_tracked_devices.get("default", {})
            assert "dev2" in coordinator._site_tracked_devices.get("default", {})

    @pytest.mark.asyncio
    async def test_cleanup_same_mac_different_id_removes_nothing(
        self, hass: HomeAssistant, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """A device appearing with a new ID but the same MAC is not removed."""
        coordinator._site_sources = {"default": "v1"}
        coordinator._site_tracked_devices = {"default": {"old_id": "aa:bb:cc:dd:ee:01"}}

        coordinator.data["devices"] = {
            "default": {
                "new_id": {
                    "id": "new_id",
                    "mac": "aa:bb:cc:dd:ee:01",
                }
            }
        }

        with patch.object(coordinator, "_remove_stale_device") as mock_remove:
            for _ in range(3):
                coordinator._cleanup_stale_devices(
                    site_sources={"default": "v1"},
                    site_device_macs={"default": {"new_id": "aa:bb:cc:dd:ee:01"}},
                )

            mock_remove.assert_not_called()
            assert "new_id" in coordinator._site_tracked_devices.get("default", {})

    @pytest.mark.asyncio
    async def test_cleanup_device_reappears_resets_absence_count(
        self, hass: HomeAssistant, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """A missing device that reappears before threshold resets absence count."""
        coordinator._site_sources = {"default": "v1"}
        coordinator._site_tracked_devices = {
            "default": {
                "dev1": "aa:bb:cc:dd:ee:01",
                "dev2": "aa:bb:cc:dd:ee:02",
            }
        }
        coordinator.data["devices"] = {
            "default": {"dev1": {"id": "dev1", "mac": "aa:bb:cc:dd:ee:01"}}
        }
        site_sources = {"default": "v1"}
        poll_macs = {"default": {"dev1": "aa:bb:cc:dd:ee:01"}}

        with patch.object(coordinator, "_remove_stale_device") as mock_remove:
            # Polls 1 and 2: dev2 is missing
            coordinator._cleanup_stale_devices(site_sources, poll_macs)
            assert coordinator._site_absence_counts["default"].get("dev2") == 1
            coordinator._cleanup_stale_devices(site_sources, poll_macs)
            assert coordinator._site_absence_counts["default"].get("dev2") == 2

            # Poll 3: dev2 reappears!
            coordinator.data["devices"] = {
                "default": {
                    "dev1": {"id": "dev1", "mac": "aa:bb:cc:dd:ee:01"},
                    "dev2": {"id": "dev2", "mac": "aa:bb:cc:dd:ee:02"},
                }
            }
            coordinator._cleanup_stale_devices(
                site_sources,
                {
                    "default": {
                        "dev1": "aa:bb:cc:dd:ee:01",
                        "dev2": "aa:bb:cc:dd:ee:02",
                    }
                },
            )
            assert coordinator._site_absence_counts["default"].get("dev2") is None
            mock_remove.assert_not_called()

    def test_legacy_device_to_v1_dict_fallback_contract(self) -> None:
        """Test _legacy_device_to_v1_dict contract: maps _id to id,
        sets macAddress, state."""
        legacy = {
            "_id": "64legacy123456789012345678",
            "mac": "aa:bb:cc:dd:ee:01",
            "name": "Switch",
            "up": True,
        }
        mapped = UnifiInsightsDeviceCoordinator._legacy_device_to_v1_dict(legacy)
        assert mapped["id"] == "64legacy123456789012345678"
        assert mapped["_id"] == "64legacy123456789012345678"
        assert mapped["macAddress"] == "aa:bb:cc:dd:ee:01"
        assert mapped["state"] == "ONLINE"

        # Without _id, falls back to MAC
        legacy_no_id = {"mac": "aa:bb:cc:dd:ee:02", "up": False}
        mapped_no_id = UnifiInsightsDeviceCoordinator._legacy_device_to_v1_dict(
            legacy_no_id
        )
        assert mapped_no_id["id"] == "aa:bb:cc:dd:ee:02"
        assert mapped_no_id["state"] == "OFFLINE"

    @pytest.mark.asyncio
    async def test_refresh_sequence_v1_fallback_v1(self, hass: HomeAssistant) -> None:
        """v1 → fallback → v1: keys stay v1, get_device resolves,
        no duplicate or deletion."""
        owners = build_seeded_owners(hass)
        try:
            entry_id = owners.entry.entry_id
            dev_reg = dr.async_get(hass)
            mac = SAMPLE_PDU_DEVICE["mac"]
            pdu_reg = dev_reg.async_get_or_create(
                config_entry_id=entry_id,
                identifiers={(DOMAIN, f"{SITE_ID}_{PDU_ID}")},
                connections={(CONNECTION_NETWORK_MAC, mac)},
            )
            assert entry_id in device_config_entry_ids(pdu_reg)

            # Poll 1: initial v1 poll
            owners.network_client.devices.get_all.return_value = [
                copy.deepcopy(SAMPLE_PDU_DEVICE)
            ]
            await owners.config.async_refresh()
            await owners.device.async_refresh()

            assert list(owners.device.data["devices"][SITE_ID].keys()) == [PDU_ID]
            assert owners.device.get_device(SITE_ID, PDU_ID) is not None
            assert owners.device._site_sources[SITE_ID] == "v1"
            assert owners.device._site_aliases[SITE_ID] == {mac: PDU_ID}
            assert owners.device._site_absence_counts.get(SITE_ID, {}) == {}

            # Spy on _remove_stale_device
            with patch.object(
                owners.device,
                "_remove_stale_device",
                wraps=owners.device._remove_stale_device,
            ) as mock_remove:
                # Poll 2: fallback to legacy /stat/device
                owners.network_client.devices.get_all.side_effect = UniFiResponseError(
                    "down", status_code=503
                )
                owners.network_client.devices.get_legacy_site_devices.return_value = [
                    copy.deepcopy(SAMPLE_PDU_LEGACY_DEVICE)
                ]
                await owners.device.async_refresh()

                assert owners.device._site_sources[SITE_ID] == "legacy"
                assert owners.device._site_aliases[SITE_ID] == {mac: PDU_ID}
                assert owners.device._site_absence_counts.get(SITE_ID, {}) == {}

                # Cache keys stay v1 due to committed alias lookup
                assert list(owners.device.data["devices"][SITE_ID].keys()) == [PDU_ID]
                pdu_fallback = owners.device.get_device(SITE_ID, PDU_ID)
                assert pdu_fallback is not None
                assert pdu_fallback["id"] == PDU_ID
                assert pdu_fallback["_id"] == SAMPLE_PDU_LEGACY_DEVICE["_id"]

                # Registry device keeps Network entry, no duplicate legacy ID device
                reg_pdu = get_device_by_identifier(
                    dev_reg, (DOMAIN, f"{SITE_ID}_{PDU_ID}"), entry_id
                )
                assert reg_pdu is not None
                assert entry_id in device_config_entry_ids(reg_pdu)
                assert (
                    get_device_by_identifier(
                        dev_reg,
                        (DOMAIN, f"{SITE_ID}_{SAMPLE_PDU_LEGACY_DEVICE['_id']}"),
                        entry_id,
                    )
                    is None
                )
                mock_remove.assert_not_called()

                # Poll 2b: second consecutive fallback poll (eligible fallback)
                await owners.device.async_refresh()

                assert owners.device._site_sources[SITE_ID] == "legacy"
                assert owners.device._site_aliases[SITE_ID] == {mac: PDU_ID}
                assert owners.device._site_absence_counts.get(SITE_ID, {}) == {}
                assert list(owners.device.data["devices"][SITE_ID].keys()) == [PDU_ID]
                pdu_fallback2 = owners.device.get_device(SITE_ID, PDU_ID)
                assert pdu_fallback2 is not None
                assert pdu_fallback2["id"] == PDU_ID
                assert pdu_fallback2["_id"] == SAMPLE_PDU_LEGACY_DEVICE["_id"]
                mock_remove.assert_not_called()

                # Poll 3: v1 recovers
                owners.network_client.devices.get_all.side_effect = None
                owners.network_client.devices.get_all.return_value = [
                    copy.deepcopy(SAMPLE_PDU_DEVICE)
                ]
                await owners.device.async_refresh()

                assert owners.device._site_sources[SITE_ID] == "v1"
                assert owners.device._site_aliases[SITE_ID] == {mac: PDU_ID}
                assert owners.device._site_absence_counts.get(SITE_ID, {}) == {}
                assert list(owners.device.data["devices"][SITE_ID].keys()) == [PDU_ID]
                assert owners.device.get_device(SITE_ID, PDU_ID) is not None
                reg_pdu = get_device_by_identifier(
                    dev_reg, (DOMAIN, f"{SITE_ID}_{PDU_ID}"), entry_id
                )
                assert reg_pdu is not None
                assert entry_id in device_config_entry_ids(reg_pdu)
                mock_remove.assert_not_called()
        finally:
            await async_shutdown_owners(owners)

    @pytest.mark.asyncio
    async def test_refresh_sequence_v1_empty_fallback_preserves_aliases_and_counters(
        self, hass: HomeAssistant
    ) -> None:
        """Ineligible empty v1 result preserves aliases and counters across fallback."""
        owners = build_seeded_owners(hass)
        try:
            entry_id = owners.entry.entry_id
            dev_reg = dr.async_get(hass)
            pdu_mac = SAMPLE_PDU_DEVICE["mac"]
            sw_v1 = {
                "id": "switch_v1_id",
                "mac": "aa:aa:aa:aa:aa:01",
                "model": "USW-24",
                "name": "Switch",
                "state": "ONLINE",
            }
            sw_legacy = {
                "_id": "64switch_legacy_id12345678",
                "mac": "aa:aa:aa:aa:aa:01",
                "model": "USW-24",
                "name": "Switch",
                "up": True,
            }
            dev_reg.async_get_or_create(
                config_entry_id=entry_id,
                identifiers={(DOMAIN, f"{SITE_ID}_{PDU_ID}")},
                connections={(CONNECTION_NETWORK_MAC, pdu_mac)},
            )
            dev_reg.async_get_or_create(
                config_entry_id=entry_id,
                identifiers={(DOMAIN, f"{SITE_ID}_{sw_v1['id']}")},
                connections={(CONNECTION_NETWORK_MAC, sw_v1["mac"])},
            )

            # Poll 1: initial v1 poll with PDU and Switch
            owners.network_client.devices.get_all.return_value = [
                copy.deepcopy(SAMPLE_PDU_DEVICE),
                dict(sw_v1),
            ]
            await owners.config.async_refresh()
            await owners.device.async_refresh()

            assert owners.device._site_aliases[SITE_ID][pdu_mac] == PDU_ID
            assert owners.device._site_aliases[SITE_ID][sw_v1["mac"]] == sw_v1["id"]

            # Poll 2: eligible v1 poll omitting PDU (advances PDU absence counter to 1)
            # while the legacy list agrees that the PDU is gone
            owners.network_client.devices.get_all.return_value = [dict(sw_v1)]
            owners.network_client.devices.get_legacy_site_devices.return_value = [
                dict(sw_legacy)
            ]
            await owners.device.async_refresh()

            assert owners.device._site_absence_counts[SITE_ID][PDU_ID] == 1
            assert owners.device._site_aliases[SITE_ID][pdu_mac] == PDU_ID

            # Poll 3: v1 returns empty list [] (ineligible: empty after non-empty)
            owners.network_client.devices.get_all.return_value = []
            await owners.device.async_refresh()

            # Aliases must NOT be replaced with empty dict; counters must NOT reset
            assert owners.device._site_aliases[SITE_ID][pdu_mac] == PDU_ID
            assert owners.device._site_absence_counts[SITE_ID][PDU_ID] == 1

            # Poll 4: fallback to legacy /stat/device with both devices
            owners.network_client.devices.get_all.side_effect = UniFiResponseError(
                "down", status_code=503
            )
            owners.network_client.devices.get_legacy_site_devices.return_value = [
                copy.deepcopy(SAMPLE_PDU_LEGACY_DEVICE),
                dict(sw_legacy),
            ]
            await owners.device.async_refresh()

            # PDU must be resolved to its v1 ID, not legacy _id
            assert PDU_ID in owners.device.data["devices"][SITE_ID]
            pdu_dev = owners.device.get_device(SITE_ID, PDU_ID)
            assert pdu_dev is not None
            assert pdu_dev["id"] == PDU_ID
            assert pdu_dev["_id"] == SAMPLE_PDU_LEGACY_DEVICE["_id"]
        finally:
            await async_shutdown_owners(owners)

    @pytest.mark.asyncio
    async def test_refresh_sequence_v1_omits_device_then_fallback(
        self, hass: HomeAssistant
    ) -> None:
        """A v1 poll omitting a device merges aliases so fallback retains v1 ID."""
        owners = build_seeded_owners(hass)
        try:
            entry_id = owners.entry.entry_id
            dev_reg = dr.async_get(hass)
            pdu_mac = SAMPLE_PDU_DEVICE["mac"]
            sw_v1 = {
                "id": "switch_v1_id",
                "mac": "aa:aa:aa:aa:aa:01",
                "model": "USW-24",
                "name": "Switch",
                "state": "ONLINE",
            }
            dev_reg.async_get_or_create(
                config_entry_id=entry_id,
                identifiers={(DOMAIN, f"{SITE_ID}_{PDU_ID}")},
                connections={(CONNECTION_NETWORK_MAC, pdu_mac)},
            )

            # Poll 1: initial v1 poll with Switch only
            owners.network_client.devices.get_all.return_value = [
                dict(sw_v1),
            ]
            await owners.config.async_refresh()
            await owners.device.async_refresh()

            assert pdu_mac not in owners.device._site_aliases.get(SITE_ID, {})

            # Poll 2: eligible v1 poll discovers PDU (merges new PDU alias)
            owners.network_client.devices.get_all.return_value = [
                dict(sw_v1),
                copy.deepcopy(SAMPLE_PDU_DEVICE),
            ]
            await owners.device.async_refresh()

            assert owners.device._site_aliases[SITE_ID][pdu_mac] == PDU_ID

            # Poll 3: eligible v1 poll omits PDU (omitted for one poll), and so
            # does the legacy list
            owners.network_client.devices.get_all.return_value = [dict(sw_v1)]
            owners.network_client.devices.get_legacy_site_devices.return_value = [
                {"_id": "64switch_legacy_id12345678", "mac": sw_v1["mac"], "up": True}
            ]
            await owners.device.async_refresh()

            # Merging aliases ensures PDU alias is preserved even when omitted
            assert owners.device._site_aliases[SITE_ID][pdu_mac] == PDU_ID
            assert owners.device._site_absence_counts[SITE_ID][PDU_ID] == 1

            # Poll 4: fallback to legacy /stat/device returning PDU
            owners.network_client.devices.get_all.side_effect = UniFiResponseError(
                "down", status_code=503
            )
            owners.network_client.devices.get_legacy_site_devices.return_value = [
                copy.deepcopy(SAMPLE_PDU_LEGACY_DEVICE),
            ]
            await owners.device.async_refresh()

            # PDU is keyed by its v1 ID despite being omitted in previous v1 poll
            assert PDU_ID in owners.device.data["devices"][SITE_ID]
            pdu_dev = owners.device.get_device(SITE_ID, PDU_ID)
            assert pdu_dev is not None
            assert pdu_dev["id"] == PDU_ID
        finally:
            await async_shutdown_owners(owners)

    @pytest.mark.asyncio
    async def test_refresh_restart_during_fallback_to_v1_removes_nothing(
        self, hass: HomeAssistant
    ) -> None:
        """Restart while in fallback (no prior alias in coordinator)
        → v1: nothing detached."""
        owners = build_seeded_owners(hass)
        try:
            entry_id = owners.entry.entry_id
            dev_reg = dr.async_get(hass)
            mac = SAMPLE_PDU_DEVICE["mac"]
            legacy_id = SAMPLE_PDU_LEGACY_DEVICE["_id"]

            # Prior flip or restart: device in registry has legacy identifier
            reg_dev = dev_reg.async_get_or_create(
                config_entry_id=entry_id,
                identifiers={(DOMAIN, f"{SITE_ID}_{legacy_id}")},
                connections={(CONNECTION_NETWORK_MAC, mac)},
            )
            assert entry_id in device_config_entry_ids(reg_dev)

            # Poll 1: coordinator starts in fallback (v1 503), no aliases committed
            owners.network_client.devices.get_all.side_effect = UniFiResponseError(
                "down", status_code=503
            )
            owners.network_client.devices.get_legacy_site_devices.return_value = [
                copy.deepcopy(SAMPLE_PDU_LEGACY_DEVICE)
            ]
            await owners.config.async_refresh()
            await owners.device.async_refresh()

            # Poll 2: v1 recovers (source changed from legacy -> v1: ineligible)
            owners.network_client.devices.get_all.side_effect = None
            owners.network_client.devices.get_all.return_value = [
                copy.deepcopy(SAMPLE_PDU_DEVICE)
            ]
            await owners.device.async_refresh()

            # Poll 3: v1 again (eligible, but MAC guard matches hardware MAC)
            await owners.device.async_refresh()

            # The registry device is still attached
            check_dev = get_device_by_identifier(
                dev_reg, (DOMAIN, f"{SITE_ID}_{legacy_id}"), entry_id
            )
            assert check_dev is not None
            assert entry_id in device_config_entry_ids(check_dev)
        finally:
            await async_shutdown_owners(owners)

    @pytest.mark.asyncio
    async def test_refresh_restart_during_fallback_flapping_v1_retains_v1_keys(
        self, hass: HomeAssistant
    ) -> None:
        """Restart in fallback -> v1 (ineligible, source change) -> fallback:
        fallback poll keys devices by the v1 ID without legacy-key churn."""
        owners = build_seeded_owners(hass)
        try:
            mac = SAMPLE_PDU_DEVICE["mac"]
            v1_id = SAMPLE_PDU_DEVICE["id"]
            legacy_id = SAMPLE_PDU_LEGACY_DEVICE["_id"]

            # Poll 1: restart while in fallback (v1 down with 503)
            # No aliases committed yet, so the device is keyed by its legacy ID.
            owners.network_client.devices.get_all.side_effect = UniFiResponseError(
                "down", status_code=503
            )
            owners.network_client.devices.get_legacy_site_devices.return_value = [
                copy.deepcopy(SAMPLE_PDU_LEGACY_DEVICE)
            ]
            await owners.config.async_refresh()
            await owners.device.async_refresh()

            assert legacy_id in owners.device.data["devices"][SITE_ID]
            assert owners.device._site_sources[SITE_ID] == "legacy"
            assert owners.device._site_aliases.get(SITE_ID) == {}

            # Poll 2: v1 responds (source changed legacy -> v1: ineligible for cleanup,
            # but aliases must be merged on every committed v1 poll).
            owners.network_client.devices.get_all.side_effect = None
            owners.network_client.devices.get_all.return_value = [
                copy.deepcopy(SAMPLE_PDU_DEVICE)
            ]
            await owners.device.async_refresh()

            assert v1_id in owners.device.data["devices"][SITE_ID]
            assert owners.device._site_sources[SITE_ID] == "v1"
            assert owners.device._site_aliases.get(SITE_ID, {}).get(mac) == v1_id

            # Poll 3: v1 drops again to fallback.
            # Because the v1 alias was recorded in poll 2, fallback must reuse
            # the v1 ID rather than churning back to the legacy ID.
            owners.network_client.devices.get_all.side_effect = UniFiResponseError(
                "down", status_code=503
            )
            await owners.device.async_refresh()

            assert v1_id in owners.device.data["devices"][SITE_ID]
            assert legacy_id not in owners.device.data["devices"][SITE_ID]
            assert owners.device._site_sources[SITE_ID] == "legacy"
        finally:
            await async_shutdown_owners(owners)

    @pytest.mark.asyncio
    async def test_refresh_site_empty_after_non_empty_removes_nothing(
        self, hass: HomeAssistant
    ) -> None:
        """Site non-empty → zero devices: nothing detached, tracking unchanged."""
        owners = build_seeded_owners(hass)
        try:
            entry_id = owners.entry.entry_id
            dev_reg = dr.async_get(hass)
            dev_reg.async_get_or_create(
                config_entry_id=entry_id,
                identifiers={(DOMAIN, f"{SITE_ID}_{PDU_ID}")},
                connections={(CONNECTION_NETWORK_MAC, SAMPLE_PDU_DEVICE["mac"])},
            )

            # Poll 1: normal snapshot with device
            owners.network_client.devices.get_all.return_value = [
                copy.deepcopy(SAMPLE_PDU_DEVICE)
            ]
            await owners.config.async_refresh()
            await owners.device.async_refresh()
            assert PDU_ID in owners.device._site_tracked_devices[SITE_ID]

            # Polls 2, 3, 4: site returns zero devices
            owners.network_client.devices.get_all.return_value = []
            for _ in range(MAX_CONSECUTIVE_MISSING_POLLS + 1):
                await owners.device.async_refresh()

            # Device not detached, tracking unchanged
            reg_dev = get_device_by_identifier(
                dev_reg, (DOMAIN, f"{SITE_ID}_{PDU_ID}"), entry_id
            )
            assert reg_dev is not None
            assert entry_id in device_config_entry_ids(reg_dev)
            assert PDU_ID in owners.device._site_tracked_devices[SITE_ID]
        finally:
            await async_shutdown_owners(owners)

    @pytest.mark.asyncio
    async def test_refresh_failed_site_fetch_after_success_leaves_state_unchanged(
        self, hass: HomeAssistant
    ) -> None:
        """Failed site fetch after success leaves all aliases, source,
        and tracking unchanged."""
        owners = build_seeded_owners(hass)
        try:
            # Poll 1: success on v1
            owners.network_client.devices.get_all.return_value = [
                copy.deepcopy(SAMPLE_PDU_DEVICE)
            ]
            await owners.config.async_refresh()
            await owners.device.async_refresh()

            before_aliases = copy.deepcopy(owners.device._site_aliases)
            before_sources = copy.deepcopy(owners.device._site_sources)
            before_tracking = copy.deepcopy(owners.device._site_tracked_devices)
            before_absence = copy.deepcopy(owners.device._site_absence_counts)

            # Poll 2: multi-site poll where site 1 succeeds with new alias candidate
            # but site 2 fails -> whole refresh fails; no aliases or tracking committed
            owners.config.get_site_ids = MagicMock(
                return_value=["default", "failing_site"]
            )
            new_candidate = {
                "id": "candidate_v1_id",
                "mac": "aa:bb:cc:dd:ee:77",
                "model": "USW-24",
                "name": "Candidate",
            }

            async def mock_get_all(site=None):
                if site == "default":
                    return [copy.deepcopy(SAMPLE_PDU_DEVICE), new_candidate]
                msg = "server error"
                raise UniFiResponseError(msg, status_code=500)

            owners.network_client.devices.get_all.side_effect = mock_get_all
            owners.network_client.devices.get_legacy_site_devices.side_effect = (
                UniFiResponseError("server error", status_code=500)
            )
            await owners.device.async_refresh()
            assert not owners.device.last_update_success

            assert owners.device._site_aliases == before_aliases
            assert owners.device._site_sources == before_sources
            assert owners.device._site_tracked_devices == before_tracking
            assert owners.device._site_absence_counts == before_absence
        finally:
            await async_shutdown_owners(owners)

    @pytest.mark.asyncio
    async def test_refresh_genuine_removal_on_persistent_fallback_site(
        self, hass: HomeAssistant
    ) -> None:
        """Genuine removal on a site that stays on fallback is removed
        exactly at threshold."""
        owners = build_seeded_owners(hass)
        try:
            entry_id = owners.entry.entry_id
            dev_reg = dr.async_get(hass)
            sw_legacy = {
                "_id": "switch_legacy_id",
                "mac": "aa:aa:aa:aa:aa:01",
                "name": "Switch",
            }
            owners.network_client.devices.get_all.side_effect = UniFiResponseError(
                "down", status_code=503
            )
            owners.network_client.devices.get_legacy_site_devices.return_value = [
                copy.deepcopy(SAMPLE_PDU_LEGACY_DEVICE),
                sw_legacy,
            ]
            await owners.config.async_refresh()
            await owners.device.async_refresh()

            dev_reg.async_get_or_create(
                config_entry_id=entry_id,
                identifiers={(DOMAIN, f"{SITE_ID}_switch_legacy_id")},
                connections={(CONNECTION_NETWORK_MAC, "aa:aa:aa:aa:aa:01")},
            )
            dev_reg.async_get_or_create(
                config_entry_id=entry_id,
                identifiers={(DOMAIN, f"{SITE_ID}_{SAMPLE_PDU_LEGACY_DEVICE['_id']}")},
                connections={(CONNECTION_NETWORK_MAC, SAMPLE_PDU_DEVICE["mac"])},
            )

            # Switch genuinely removed from controller while site stays on fallback
            owners.network_client.devices.get_legacy_site_devices.return_value = [
                copy.deepcopy(SAMPLE_PDU_LEGACY_DEVICE)
            ]

            # Polls 1 and 2: missing, not yet detached
            for _ in range(MAX_CONSECUTIVE_MISSING_POLLS - 1):
                await owners.device.async_refresh()
                check_sw = get_device_by_identifier(
                    dev_reg, (DOMAIN, f"{SITE_ID}_switch_legacy_id"), entry_id
                )
                assert check_sw is not None
                assert entry_id in device_config_entry_ids(check_sw)

            # Poll 3: reaches threshold -> detached
            await owners.device.async_refresh()
            check_sw = get_device_by_identifier(
                dev_reg, (DOMAIN, f"{SITE_ID}_switch_legacy_id"), entry_id
            )
            assert check_sw is None or entry_id not in device_config_entry_ids(check_sw)

            # PDU was not removed
            check_pdu = get_device_by_identifier(
                dev_reg,
                (DOMAIN, f"{SITE_ID}_{SAMPLE_PDU_LEGACY_DEVICE['_id']}"),
                entry_id,
            )
            assert check_pdu is not None
            assert entry_id in device_config_entry_ids(check_pdu)
        finally:
            await async_shutdown_owners(owners)

    @pytest.mark.asyncio
    async def test_refresh_genuine_removal_on_v1_site_with_reset_and_exact_removal(
        self, hass: HomeAssistant
    ) -> None:
        """Genuine removal on v1: none before threshold; exact call at threshold;
        reset on reappear."""
        owners = build_seeded_owners(hass)
        try:
            entry_id = owners.entry.entry_id
            dev_reg = dr.async_get(hass)
            sw_v1 = {
                "id": "switch_v1_id",
                "mac": "aa:aa:aa:aa:aa:01",
                "model": "USW-24",
                "name": "Switch",
                "state": "ONLINE",
            }
            owners.network_client.devices.get_all.return_value = [
                copy.deepcopy(SAMPLE_PDU_DEVICE),
                sw_v1,
            ]
            await owners.config.async_refresh()
            await owners.device.async_refresh()

            dev_reg.async_get_or_create(
                config_entry_id=entry_id,
                identifiers={(DOMAIN, f"{SITE_ID}_switch_v1_id")},
                connections={(CONNECTION_NETWORK_MAC, "aa:aa:aa:aa:aa:01")},
            )

            # Disappears from v1
            owners.network_client.devices.get_all.return_value = [
                copy.deepcopy(SAMPLE_PDU_DEVICE)
            ]
            # Poll 1 & 2: absent, not detached
            await owners.device.async_refresh()
            assert (
                owners.device._site_absence_counts.get(SITE_ID, {}).get("switch_v1_id")
                == 1
            )
            await owners.device.async_refresh()
            assert (
                owners.device._site_absence_counts.get(SITE_ID, {}).get("switch_v1_id")
                == 2
            )

            check_sw = get_device_by_identifier(
                dev_reg, (DOMAIN, f"{SITE_ID}_switch_v1_id"), entry_id
            )
            assert check_sw is not None

            # Reappears on poll 3 -> counter resets
            owners.network_client.devices.get_all.return_value = [
                copy.deepcopy(SAMPLE_PDU_DEVICE),
                sw_v1,
            ]
            await owners.device.async_refresh()
            assert (
                owners.device._site_absence_counts.get(SITE_ID, {}).get("switch_v1_id")
                is None
            )

            # Disappears again: 1, 2, 3 -> removed
            owners.network_client.devices.get_all.return_value = [
                copy.deepcopy(SAMPLE_PDU_DEVICE)
            ]
            await owners.device.async_refresh()
            await owners.device.async_refresh()

            with patch.object(
                dev_reg, "async_update_device", wraps=dev_reg.async_update_device
            ) as mock_update:
                await owners.device.async_refresh()
                mock_update.assert_called_once()
                call_kwargs = mock_update.call_args.kwargs
                assert call_kwargs["remove_config_entry_id"] == entry_id

            check_sw = get_device_by_identifier(
                dev_reg, (DOMAIN, f"{SITE_ID}_switch_v1_id"), entry_id
            )
            assert check_sw is None or entry_id not in device_config_entry_ids(check_sw)
        finally:
            await async_shutdown_owners(owners)

    @pytest.mark.asyncio
    async def test_refresh_stale_removal_leaves_other_entry_device_intact(
        self, hass: HomeAssistant
    ) -> None:
        """Entry isolation: removing a stale Network device leaves a separate
        device with the same identifier under another config entry intact."""
        owners = build_seeded_owners(hass)
        try:
            entry_id = owners.entry.entry_id
            dev_reg = dr.async_get(hass)
            other_entry = MockConfigEntry(domain=DOMAIN, title="Protect Entry")
            other_entry.add_to_hass(hass)

            multi_dev = {
                "id": "multi_device_id",
                "mac": "aa:aa:aa:aa:aa:99",
                "model": "USW-24",
                "name": "Multi",
                "state": "ONLINE",
            }
            owners.network_client.devices.get_all.return_value = [
                copy.deepcopy(SAMPLE_PDU_DEVICE),
                multi_dev,
            ]
            await owners.config.async_refresh()
            await owners.device.async_refresh()

            identifier = (DOMAIN, f"{SITE_ID}_multi_device_id")
            dev_entry = dev_reg.async_get_or_create(
                config_entry_id=entry_id,
                identifiers={identifier},
                connections={(CONNECTION_NETWORK_MAC, "aa:aa:aa:aa:aa:99")},
            )
            other_dev_entry = dev_reg.async_get_or_create(
                config_entry_id=other_entry.entry_id,
                identifiers={identifier},
                connections={(CONNECTION_NETWORK_MAC, "aa:aa:aa:aa:aa:99")},
            )
            assert entry_id in device_config_entry_ids(dev_entry)
            assert other_entry.entry_id in device_config_entry_ids(other_dev_entry)

            # Multi device disappears from Network
            owners.network_client.devices.get_all.return_value = [
                copy.deepcopy(SAMPLE_PDU_DEVICE)
            ]
            for _ in range(MAX_CONSECUTIVE_MISSING_POLLS):
                await owners.device.async_refresh()

            our_dev = get_device_by_identifier(dev_reg, identifier, entry_id)
            assert our_dev is None or entry_id not in device_config_entry_ids(our_dev)

            survivor = get_device_by_identifier(
                dev_reg, identifier, other_entry.entry_id
            )
            assert survivor is not None
            assert other_entry.entry_id in device_config_entry_ids(survivor)
        finally:
            await async_shutdown_owners(owners)

    @pytest.mark.asyncio
    async def test_pdu_outlet_command_sends_legacy_id_with_v1_identity(
        self, coordinator: UnifiInsightsDeviceCoordinator
    ):
        """PDU outlet command resolves legacy _id from a real v1-keyed poll."""
        v1 = copy.deepcopy(SAMPLE_PDU_DEVICE)
        v1["id"] = "pdu-v1-uuid"
        devices = coordinator.network_client.devices
        require_mock(devices.get_all).return_value = [v1]
        require_mock(devices.get_legacy_site_devices).return_value = [
            copy.deepcopy(COMPLETE_OUTLET_DEVICE)
        ]
        await coordinator.async_refresh()
        assert "pdu-v1-uuid" in coordinator.data["devices"]["default"]

        devices.set_outlet_state = AsyncMock(return_value=True)
        coordinator.async_request_refresh = AsyncMock()

        result = await coordinator.async_set_outlet_state(
            "default", "pdu-v1-uuid", 1, state=False
        )
        assert result is True

        devices.set_outlet_state.assert_awaited_once()
        call_args = devices.set_outlet_state.await_args
        assert require_not_none(call_args).args[1] == COMPLETE_OUTLET_DEVICE["_id"]
        assert require_not_none(call_args).args[2] == 1
        assert require_not_none(call_args).kwargs["state"] is False

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
        for model in require_mock(
            coordinator.network_client.devices.get_all
        ).return_value:
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
        for model in require_mock(
            coordinator.network_client.devices.get_all
        ).return_value:
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

        devices_dict, _stats, _clients, *_ = await coordinator._process_site(
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

        devices_dict, stats_dict, clients_dict, *_ = await coordinator._process_site(
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


async def test_failed_refresh_publishes_nothing(seeded_owners: SeededOwners) -> None:
    """A failed refresh leaves the data root and its section maps as they were."""
    device = seeded_owners.device
    root_before = device.data
    devices_before = device.data["devices"]
    seeded_owners.network_client.devices.get_all.side_effect = UniFiConnectionError(
        "down"
    )

    await device.async_refresh()

    assert not device.last_update_success
    assert device.data is root_before
    assert root_before["devices"] is devices_before


async def test_two_outlet_writes_across_getting_ready_refresh(
    seeded_owners: SeededOwners,
) -> None:
    """A provisioning refresh keeps facts serviceable and supplies the next write."""
    device = seeded_owners.device
    endpoint = seeded_owners.network_client.devices
    refreshed = copy.deepcopy(SAMPLE_PDU_DEVICE)
    refreshed["state"] = "GETTING_READY"
    legacy = copy.deepcopy(COMPLETE_OUTLET_DEVICE)
    legacy["outlet_table"][0]["relay_state"] = False
    legacy["outlet_overrides"][0]["relay_state"] = False
    endpoint.get_all.return_value = [refreshed]
    endpoint.get_legacy_site_devices.return_value = [legacy]
    endpoint.set_outlet_state.return_value = True
    await device.async_refresh()

    await device.async_set_outlet_state(SITE_ID, PDU_ID, 1, state=False)
    await device.async_refresh()
    current = device.data["devices"][SITE_ID][PDU_ID]
    facts = seeded_owners.facade.get_device_facts(SITE_ID, PDU_ID)
    assert current["state"] == "GETTING_READY"
    assert facts is not None
    assert facts.serviceable is True
    assert facts.online_state == "unknown"
    assert seeded_owners.facade.device_available is True

    await device.async_set_outlet_state(SITE_ID, PDU_ID, 2, state=True)

    assert endpoint.set_outlet_state.await_count == 2
    first, second = endpoint.set_outlet_state.await_args_list
    assert first.args == ("default", legacy["_id"], 1)
    assert first.kwargs["state"] is False
    assert second.args == ("default", legacy["_id"], 2)
    assert second.kwargs["state"] is True
    assert second.kwargs["current_device"] is current
    assert (
        second.kwargs["current_device"]["outlet_overrides"]
        == legacy["outlet_overrides"]
    )
