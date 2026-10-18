# Copyright (c) 2026 Ruaan Deysel
"""Tests for UniFi Protect switch platform."""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_registry import (
    EntityRegistry,
    RegistryEntry,
)
from homeassistant.helpers.entity_registry import (
    async_get as async_get_entity_registry,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from custom_components.unifi_insights.api import ApiKeyAuth, ConnectionType
from custom_components.unifi_insights.api.network.models.routes import (
    PolicyBasedRoute,
)
from custom_components.unifi_insights.api.protect import UniFiProtectClient
from custom_components.unifi_insights.const import (
    ATTR_CAMERA_ID,
    ATTR_CAMERA_NAME,
    ATTR_HIGH_FPS_MODE,
    ATTR_STATUS_LIGHT,
    CONF_CLIENT_CONTROL,
    DEVICE_TYPE_CAMERA,
    DOMAIN,
    VIDEO_MODE_DEFAULT,
    VIDEO_MODE_HIGH_FPS,
)
from custom_components.unifi_insights.coordinators.base import UnifiBaseCoordinator
from custom_components.unifi_insights.switch import (
    PARALLEL_UPDATES,
    UnifiClientBlockSwitch,
    UnifiFirewallRuleSwitch,
    UnifiOutletCycleSwitch,
    UnifiOutletSwitch,
    UnifiPolicyBasedRouteSwitch,
    UnifiProtectHighFPSSwitch,
    UnifiProtectStatusLightSwitch,
    UnifiVpnClientSwitch,
    UnifiWifiSwitch,
    _find_gateway_device_id,
    _get_firewall_rule_action,
    _prune_orphaned_switch_entities,
    async_setup_entry,
)


class TestParallelUpdates:
    """Test PARALLEL_UPDATES constant."""

    def test_parallel_updates_value(self) -> None:
        """Test that PARALLEL_UPDATES is set correctly for action-based entities."""
        assert PARALLEL_UPDATES == 1


class TestAsyncSetupEntry:
    """Tests for async_setup_entry function."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.network_client.firewall = MagicMock()
        coordinator.network_client.firewall.update_rule = AsyncMock()
        coordinator.data = {
            "sites": {},
            "devices": {},
            "clients": {},
            "wifi": {},
            "firewall_rules": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    @pytest.mark.asyncio
    async def test_setup_entry_no_protect_client(self, hass, mock_coordinator) -> None:
        """Test setup when Protect API is not available.

        The switch platform now also handles PoE switches for network devices,
        so even without Protect, entities may be added (0 in this case since
        no devices have PoE ports configured).
        """
        mock_coordinator.protect_client = None

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        # Should add empty list (no PoE ports, no Protect cameras)
        async_add_entities.assert_called_once_with([])

    @pytest.mark.asyncio
    async def test_setup_entry_with_cameras(self, hass, mock_coordinator) -> None:
        """Test setup with cameras present."""
        mock_coordinator.data["protect"]["cameras"] = {
            "camera1": {
                "id": "camera1",
                "name": "Test Camera",
                "state": "CONNECTED",
                "isMicEnabled": True,
            }
        }

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        # Should add 1 switch entity per camera (status light)
        # High FPS only added if hasHighFpsCapability is True
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], UnifiProtectStatusLightSwitch)

    @pytest.mark.asyncio
    async def test_setup_entry_with_multiple_cameras(
        self, hass, mock_coordinator
    ) -> None:
        """Test setup with multiple cameras."""
        mock_coordinator.data["protect"]["cameras"] = {
            "camera1": {"id": "camera1", "name": "Front Camera", "state": "CONNECTED"},
            "camera2": {"id": "camera2", "name": "Back Camera", "state": "CONNECTED"},
            "camera3": {
                "id": "camera3",
                "name": "Side Camera",
                "state": "DISCONNECTED",
            },
        }

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        # 3 cameras x 1 switch each = 3 switches
        assert len(entities) == 3

    @pytest.mark.asyncio
    async def test_setup_entry_top_level_collections_not_dicts_are_skipped(
        self, hass, mock_coordinator
    ) -> None:
        """Malformed (non-dict) top-level collections are skipped without
        raising, producing no switches for any of them."""
        mock_coordinator.data["clients"] = "not-a-dict"
        mock_coordinator.data["wifi"] = "not-a-dict"
        mock_coordinator.data["firewall_rules"] = "not-a-dict"
        mock_coordinator.data["policy_based_routes"] = "not-a-dict"
        mock_coordinator.data["vpn_clients"] = "not-a-dict"

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.options = {CONF_CLIENT_CONTROL: True}

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        assert entities == []

    @pytest.mark.asyncio
    async def test_setup_entry_high_fps_dedupe_on_rediscovery(
        self, hass, mock_coordinator
    ) -> None:
        """Re-running discovery for a capable camera does not duplicate the
        high FPS switch."""
        mock_coordinator.data["protect"]["cameras"] = {
            "camera1": {
                "id": "camera1",
                "name": "Test Camera",
                "state": "CONNECTED",
                "featureFlags": {"hasHighFpsCapability": True},
            }
        }

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        entities = async_add_entities.call_args[0][0]
        high_fps_before = [
            e for e in entities if isinstance(e, UnifiProtectHighFPSSwitch)
        ]
        assert len(high_fps_before) == 1

        # Re-running discovery on unchanged data must not add a duplicate.
        listener()

        assert async_add_entities.call_count == 1

    @pytest.mark.asyncio
    async def test_setup_entry_client_block_switch_dedupe_on_rediscovery(
        self, hass, mock_coordinator
    ) -> None:
        """Re-running discovery for an unchanged client does not duplicate
        the block switch."""
        mock_coordinator.data["clients"]["site1"] = {
            "client1": {"id": "client1", "mac": "aa:bb:cc:dd:ee:ff", "name": "PC"}
        }

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.options = {CONF_CLIENT_CONTROL: True}

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        entities = async_add_entities.call_args[0][0]
        block_switches_before = [
            e for e in entities if isinstance(e, UnifiClientBlockSwitch)
        ]
        assert len(block_switches_before) == 1

        # Re-running discovery on unchanged data must not add a duplicate.
        listener()

        assert async_add_entities.call_count == 1

    @pytest.mark.asyncio
    async def test_setup_entry_removes_client_block_switches_when_disabled(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup removes client block switches when client_control is disabled."""
        mock_entry: MockConfigEntry = MockConfigEntry(
            domain=DOMAIN,
            entry_id="entry_123",
            options={CONF_CLIENT_CONTROL: False},
        )
        mock_entry.add_to_hass(hass)
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        registry: EntityRegistry = async_get_entity_registry(hass)
        reg_entry: RegistryEntry = registry.async_get_or_create(
            "switch",
            DOMAIN,
            "entry_123_client1_block_switch",
            config_entry=mock_entry,
        )
        reg_entry_other: RegistryEntry = registry.async_get_or_create(
            "switch",
            DOMAIN,
            "entry_123_device_other_switch",
            config_entry=mock_entry,
        )
        assert registry.async_get(reg_entry.entity_id) is not None
        assert registry.async_get(reg_entry_other.entity_id) is not None

        async_add_entities: MagicMock = MagicMock()
        await async_setup_entry(hass, mock_entry, async_add_entities)

        assert registry.async_get(reg_entry.entity_id) is None
        assert registry.async_get(reg_entry_other.entity_id) is not None


# (data_key, unique_id_suffix, availability_attr) -- availability_attr is
# either a plain bool attribute ("config_available") or a callable taking
# site_id ("firewall_available"). Table-driven to match the production
# helper's own (unique_id_suffix, data_key, availability_fn) spec.
_ORPHAN_TYPE_PARAMS = [
    pytest.param(
        "policy_based_routes", "_policy_based_route", "config_available", id="pbr"
    ),
    pytest.param(
        "firewall_rules", "_firewall_rule", "firewall_available", id="firewall_rule"
    ),
    pytest.param("vpn_clients", "_vpn_client", "config_available", id="vpn_client"),
    pytest.param(
        "port_forwards", "_port_forward", "port_forwards_available", id="port_forward"
    ),
    pytest.param(
        "traffic_rules", "_traffic_rule", "traffic_rules_available", id="traffic_rule"
    ),
    pytest.param("nat_rules", "_nat_rule", "nat_rules_available", id="nat_rule"),
    pytest.param(
        "static_routes", "_static_route", "static_routes_available", id="static_route"
    ),
]


class TestPruneOrphanedSwitchEntities:
    """Tests for `_prune_orphaned_switch_entities`.

    Deleting a policy-based route, firewall rule, or VPN client on the
    console leaves its switch permanently unavailable (see each class's
    `available` property) with nothing to remove it from the entity
    registry. These tests lock down the per-type availability guard: each
    of the three types must use its OWN availability predicate, never a
    shared one, or a firewall-specific outage would wipe every PBR/VPN
    entity too (and vice versa).
    """

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Coordinator with all three collections populated and available."""
        coordinator = MagicMock()
        coordinator.config_available = True
        coordinator.firewall_available = MagicMock(return_value=True)
        coordinator.port_forwards_available = MagicMock(return_value=True)
        coordinator.traffic_rules_available = MagicMock(return_value=True)
        coordinator.nat_rules_available = MagicMock(return_value=True)
        coordinator.static_routes_available = MagicMock(return_value=True)
        coordinator.data = {
            "policy_based_routes": {"site1": {"route1": {}}},
            "firewall_rules": {"site1": {"rule1": {}}},
            "vpn_clients": {"site1": {"vpn1": {}}},
            "port_forwards": {"site1": {"pf1": {}}},
            "traffic_rules": {"site1": {"tr1": {}}},
            "nat_rules": {"site1": {"nat1": {}}},
            "static_routes": {"site1": {"sr1": {}}},
        }
        return coordinator

    @staticmethod
    def _reg_entry(unique_id: str) -> MagicMock:
        """Build a fake `switch` domain, `unifi_insights` platform entry."""
        entry = MagicMock()
        entry.domain = "switch"
        entry.platform = DOMAIN
        entry.unique_id = unique_id
        entry.entity_id = f"switch.{unique_id}"
        return entry

    def _prune(self, hass, coordinator, reg_entries) -> MagicMock:
        """Run the prune against mocked registry lookups.

        Returns the mock registry so callers can assert on
        `async_remove` calls.
        """
        mock_entry = MagicMock()
        mock_entry.entry_id = "entry1"
        mock_registry = MagicMock()

        with (
            patch(
                "custom_components.unifi_insights.switch.er.async_get",
                return_value=mock_registry,
            ),
            patch(
                "custom_components.unifi_insights.switch.er."
                "async_entries_for_config_entry",
                return_value=reg_entries,
            ),
        ):
            _prune_orphaned_switch_entities(hass, mock_entry, coordinator)

        return mock_registry

    @pytest.mark.parametrize(
        ("data_key", "suffix", "availability_attr"), _ORPHAN_TYPE_PARAMS
    )
    def test_own_availability_false_prunes_nothing_even_if_others_true(
        self, hass, mock_coordinator, data_key, suffix, availability_attr
    ) -> None:
        """This type's own predicate False -> zero pruned, regardless of the
        other two types' predicates being True (the regression the per-type
        guard exists to prevent)."""
        if availability_attr != "config_available":
            setattr(mock_coordinator, availability_attr, MagicMock(return_value=False))
        else:
            setattr(mock_coordinator, availability_attr, False)

        stale_entry = self._reg_entry(f"site1_stale{suffix}")
        registry = self._prune(hass, mock_coordinator, [stale_entry])

        registry.async_remove.assert_not_called()

    @pytest.mark.parametrize(
        ("data_key", "suffix", "availability_attr"), _ORPHAN_TYPE_PARAMS
    )
    def test_missing_data_key_prunes_nothing(
        self, hass, mock_coordinator, data_key, suffix, availability_attr
    ) -> None:
        """Coordinator data missing the collection entirely -> zero pruned."""
        del mock_coordinator.data[data_key]

        stale_entry = self._reg_entry(f"site1_stale{suffix}")
        registry = self._prune(hass, mock_coordinator, [stale_entry])

        registry.async_remove.assert_not_called()

    @pytest.mark.parametrize(
        ("data_key", "suffix", "availability_attr"), _ORPHAN_TYPE_PARAMS
    )
    def test_missing_site_key_prunes_nothing(
        self, hass, mock_coordinator, data_key, suffix, availability_attr
    ) -> None:
        """Collection present but the entry's site_id key is absent from it
        -> zero pruned (looks identical to a transient fetch failure)."""
        mock_coordinator.data[data_key] = {}

        stale_entry = self._reg_entry(f"site1_stale{suffix}")
        registry = self._prune(hass, mock_coordinator, [stale_entry])

        registry.async_remove.assert_not_called()

    @pytest.mark.parametrize(
        ("data_key", "suffix", "availability_attr"), _ORPHAN_TYPE_PARAMS
    )
    def test_site_value_not_dict_prunes_nothing(
        self, hass, mock_coordinator, data_key, suffix, availability_attr
    ) -> None:
        """Site key present but its value is not a dict -> zero pruned."""
        mock_coordinator.data[data_key] = {"site1": "not-a-dict"}

        stale_entry = self._reg_entry(f"site1_stale{suffix}")
        registry = self._prune(hass, mock_coordinator, [stale_entry])

        registry.async_remove.assert_not_called()

    @pytest.mark.parametrize(
        ("data_key", "suffix", "availability_attr"), _ORPHAN_TYPE_PARAMS
    )
    def test_stale_id_pruned_siblings_untouched(
        self, hass, mock_coordinator, data_key, suffix, availability_attr
    ) -> None:
        """Site present as a dict with one stale id -> exactly that entity
        removed; a live sibling in the same site is untouched."""
        live_id = next(iter(mock_coordinator.data[data_key]["site1"]))
        live_entry = self._reg_entry(f"site1_{live_id}{suffix}")
        stale_entry = self._reg_entry(f"site1_stale{suffix}")

        registry = self._prune(hass, mock_coordinator, [live_entry, stale_entry])

        registry.async_remove.assert_called_once_with(stale_entry.entity_id)

    @pytest.mark.parametrize(
        ("data_key", "suffix", "availability_attr"), _ORPHAN_TYPE_PARAMS
    )
    def test_other_sites_unique_id_untouched_when_only_one_site_qualified(
        self, hass, mock_coordinator, data_key, suffix, availability_attr
    ) -> None:
        """An entry whose site prefix does not match any qualified site is
        left alone, even though a *different* site in the same collection
        is qualified and has stale ids of its own pruned."""
        mock_coordinator.data[data_key]["site2"] = "not-a-dict"

        stale_site1 = self._reg_entry(f"site1_stale{suffix}")
        site2_entry = self._reg_entry(f"site2_stale{suffix}")

        registry = self._prune(hass, mock_coordinator, [stale_site1, site2_entry])

        registry.async_remove.assert_called_once_with(stale_site1.entity_id)

    @pytest.mark.parametrize(
        ("data_key", "suffix", "availability_attr"), _ORPHAN_TYPE_PARAMS
    )
    def test_empty_site_collection_prunes_nothing(
        self, hass, mock_coordinator, data_key, suffix, availability_attr
    ) -> None:
        """An EMPTY per-site collection must never trigger a prune.

        Regression test. Policy-based routes and VPN clients have no
        per-site "fetched successfully" signal: on a transient per-site
        fetch failure the config coordinator stores `{}` for that site
        while the overall refresh still succeeds, leaving
        `config_available` True. Treating that `{}` as "every object was
        deleted" would irreversibly remove every row of this type for the
        site on a single startup timeout.
        """
        mock_coordinator.data[data_key]["site1"] = {}

        entry_a = self._reg_entry(f"site1_route_a{suffix}")
        entry_b = self._reg_entry(f"site1_route_b{suffix}")

        registry = self._prune(hass, mock_coordinator, [entry_a, entry_b])

        registry.async_remove.assert_not_called()

    @pytest.mark.parametrize(
        ("data_key", "suffix", "availability_attr"), _ORPHAN_TYPE_PARAMS
    )
    def test_overlapping_site_ids_are_not_pruned_by_prefix_collision(
        self, hass, mock_coordinator, data_key, suffix, availability_attr
    ) -> None:
        """A site id that is a prefix of another must not claim its rows.

        Regression test. With qualified site `site1` and an unfetched
        `site1_backup`, a plain `startswith("site1_")` also matches
        `site1_backup_<id>`, so the second site's entities would be
        deleted on evidence that says nothing about them. Ownership must
        be unambiguous before anything is removed.
        """
        mock_coordinator.data[data_key]["site1_backup"] = {}
        mock_coordinator.data.setdefault("sites", {})["site1_backup"] = {}

        backup_entry = self._reg_entry(f"site1_backup_route1{suffix}")
        stale_site1 = self._reg_entry(f"site1_stale{suffix}")

        registry = self._prune(hass, mock_coordinator, [backup_entry, stale_site1])

        registry.async_remove.assert_called_once_with(stale_site1.entity_id)

    def test_non_matching_unique_ids_untouched(self, hass, mock_coordinator) -> None:
        """A client-block switch (and any other unrelated unique_id) is never
        considered by this cleanup, even when every predicate is True and the
        stale ids for all three managed types are pruned."""
        block_entry = self._reg_entry("site1_client1_block_switch")
        stale_pbr = self._reg_entry("site1_stale_policy_based_route")
        stale_rule = self._reg_entry("site1_stale_firewall_rule")
        stale_vpn = self._reg_entry("site1_stale_vpn_client")

        registry = self._prune(
            hass, mock_coordinator, [block_entry, stale_pbr, stale_rule, stale_vpn]
        )

        removed_ids = {call.args[0] for call in registry.async_remove.call_args_list}
        assert removed_ids == {
            stale_pbr.entity_id,
            stale_rule.entity_id,
            stale_vpn.entity_id,
        }

    def test_prune_removes_nat_rule_switch_that_turned_predefined(
        self, hass, mock_coordinator
    ) -> None:
        """Registry row for NAT rule switch is pruned when rule turns predefined."""
        mock_coordinator.nat_rules_available = MagicMock(return_value=True)
        mock_coordinator.data["nat_rules"] = {
            "site1": {"nat1": {"is_predefined": True}}
        }
        reg_entry = self._reg_entry("site1_nat1_nat_rule")
        registry = self._prune(hass, mock_coordinator, [reg_entry])

        registry.async_remove.assert_called_once_with(reg_entry.entity_id)

    def test_prune_keeps_nat_rule_switch_when_section_unavailable(
        self, hass, mock_coordinator
    ) -> None:
        """Predefined NAT rule row is kept when section availability is False."""
        mock_coordinator.nat_rules_available = MagicMock(return_value=False)
        mock_coordinator.data["nat_rules"] = {
            "site1": {"nat1": {"is_predefined": True}}
        }
        reg_entry = self._reg_entry("site1_nat1_nat_rule")
        registry = self._prune(hass, mock_coordinator, [reg_entry])

        registry.async_remove.assert_not_called()

    def test_prune_keeps_nat_rule_switch_when_collection_empty(
        self, hass, mock_coordinator
    ) -> None:
        """NAT rule switch is kept when collection is empty."""
        mock_coordinator.nat_rules_available = MagicMock(return_value=True)
        mock_coordinator.data["nat_rules"] = {"site1": {}}
        reg_entry = self._reg_entry("site1_nat1_nat_rule")
        registry = self._prune(hass, mock_coordinator, [reg_entry])

        registry.async_remove.assert_not_called()

    async def test_setup_entry_prunes_once_not_on_every_listener_tick(
        self, hass
    ) -> None:
        """The prune runs once during setup, after the first discovery pass
        -- not on every coordinator update (dynamic discovery fires on every
        tick; re-running the prune there would be wasted work at best)."""
        coordinator = MagicMock()
        coordinator.protect_client = None
        coordinator.config_available = True
        coordinator.firewall_available = MagicMock(return_value=True)
        coordinator.data = {
            "sites": {},
            "devices": {},
            "clients": {},
            "wifi": {},
            "firewall_rules": {},
            "policy_based_routes": {},
            "vpn_clients": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = coordinator
        mock_entry.options = {CONF_CLIENT_CONTROL: True}

        async_add_entities = MagicMock()

        with patch(
            "custom_components.unifi_insights.switch._prune_orphaned_switch_entities"
        ) as mock_prune:
            await async_setup_entry(hass, mock_entry, async_add_entities)
            listener = coordinator.async_add_listener.call_args[0][0]

            assert mock_prune.call_count == 1

            listener()

            assert mock_prune.call_count == 1


class TestObsoleteMicrophoneSwitchCleanup:
    """Tests for microphone switch removal and entity registry cleanup."""

    @pytest.mark.asyncio
    async def test_camera_discovery_does_not_create_microphone_switch(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Assert that camera discovery does not create a microphone switch."""
        mock_coordinator.data["protect"]["cameras"] = {
            "camera1": {
                "id": "camera1",
                "name": "Front Camera",
                "state": "CONNECTED",
                "isMicEnabled": True,
            }
        }
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry"
        mock_entry.options = {CONF_CLIENT_CONTROL: False}
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()
        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        assert not any(e.unique_id.endswith("_microphone") for e in entities)
        assert not any(
            getattr(e, "_attr_translation_key", None) == "microphone" for e in entities
        )

    @pytest.mark.asyncio
    async def test_setup_removes_obsolete_microphone_switch_registry_entry(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Assert that setup removes obsolete microphone switch entries
        and preserves other switches.
        """
        mock_coordinator.data["protect"]["cameras"] = {}
        mock_entry = MockConfigEntry(
            domain=DOMAIN,
            title="UniFi Protect Test",
            data={},
            entry_id="test_cleanup_entry",
            options={CONF_CLIENT_CONTROL: True},
        )
        mock_entry.add_to_hass(hass)
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        registry: EntityRegistry = async_get_entity_registry(hass)
        stale_mic_entry = registry.async_get_or_create(
            "switch",
            DOMAIN,
            f"{DOMAIN}_camera_camera1_microphone",
            config_entry=mock_entry,
        )
        preserved_mic_sensor_entry = registry.async_get_or_create(
            "binary_sensor",
            DOMAIN,
            f"{DOMAIN}_camera_camera1_camera_microphone",
            config_entry=mock_entry,
        )
        preserved_light_entry = registry.async_get_or_create(
            "switch",
            DOMAIN,
            f"{DOMAIN}_camera_camera1_status_light",
            config_entry=mock_entry,
        )
        preserved_block_entry = registry.async_get_or_create(
            "switch",
            DOMAIN,
            "site1_client1_block_switch",
            config_entry=mock_entry,
        )

        assert registry.async_get(stale_mic_entry.entity_id) is not None
        assert registry.async_get(preserved_mic_sensor_entry.entity_id) is not None
        assert registry.async_get(preserved_light_entry.entity_id) is not None
        assert registry.async_get(preserved_block_entry.entity_id) is not None

        async_add_entities = MagicMock()
        await async_setup_entry(hass, mock_entry, async_add_entities)

        assert registry.async_get(stale_mic_entry.entity_id) is None
        assert registry.async_get(preserved_mic_sensor_entry.entity_id) is not None
        assert registry.async_get(preserved_light_entry.entity_id) is not None
        assert registry.async_get(preserved_block_entry.entity_id) is not None


class TestUnifiClientBlockSwitch:
    """Tests for client block switch."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.clients = MagicMock()
        coordinator.network_client.clients.block = AsyncMock()
        coordinator.network_client.clients.unblock = AsyncMock()
        coordinator.async_block_client = AsyncMock()
        coordinator.async_unblock_client = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "name": "Test Switch",
                        "model": "USW-24-POE",
                    },
                },
            },
            "clients": {
                "site1": {
                    "client1": {
                        "id": "client1",
                        "name": "Jukebox",
                        "hostname": "jukebox",
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "blocked": False,
                        "uplinkDeviceId": "device1",
                    },
                },
            },
            "stats": {},
            "wifi": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    def test_switch_grouped_under_device(self, mock_coordinator) -> None:
        """Test switch is grouped under connected device."""
        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        # Should use the uplink device's identifiers
        assert switch._attr_device_info["identifiers"] == {(DOMAIN, "site1_device1")}

    def test_switch_fallback_no_uplink(self, mock_coordinator) -> None:
        """Test switch creates own device when no uplink."""
        # Remove uplink device ID
        mock_coordinator.data["clients"]["site1"]["client1"]["uplinkDeviceId"] = None

        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        # Should create its own device
        assert switch._attr_device_info["identifiers"] == {(DOMAIN, "client_client1")}
        assert switch._attr_device_info["name"] == "Jukebox"

    def test_switch_available(self, mock_coordinator) -> None:
        """Test switch availability."""
        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        # Should be available when client exists
        assert switch.available is True

        # Should be unavailable when client doesn't exist
        mock_coordinator.data["clients"]["site1"]["client1"] = {}
        assert switch.available is False

    def test_switch_is_on_when_not_blocked(self, mock_coordinator) -> None:
        """Test switch is ON when client is not blocked (allowed)."""
        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        # Not blocked = ON (allowed)
        assert switch.is_on is True

    def test_switch_is_off_when_blocked(self, mock_coordinator) -> None:
        """Test switch is OFF when client is blocked."""
        mock_coordinator.data["clients"]["site1"]["client1"]["blocked"] = True

        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        # When client is blocked, switch should be OFF (OFF means blocked)
        assert switch.is_on is False

    @pytest.mark.asyncio
    async def test_turn_on_unblocks_client(self, mock_coordinator) -> None:
        """Test turning ON unblocks the client."""
        mock_coordinator.data["clients"]["site1"]["client1"]["blocked"] = True

        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        await switch.async_turn_on()

        mock_coordinator.async_unblock_client.assert_called_once_with(
            "site1", "client1"
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_off_blocks_client(self, mock_coordinator) -> None:
        """Test turning OFF blocks the client."""
        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        await switch.async_turn_off()

        mock_coordinator.async_block_client.assert_called_once_with("site1", "client1")
        mock_coordinator.async_request_refresh.assert_called_once()


class TestUnifiWifiSwitch:
    """Tests for WiFi network enable/disable switch."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.wifi = MagicMock()
        coordinator.network_client.wifi.update = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {},
            "clients": {},
            "stats": {},
            "wifi": {
                "site1": {
                    "wifi1": {
                        "id": "wifi1",
                        "name": "Home Network",
                        "ssid": "HomeWiFi",
                        "enabled": True,
                        "security": "wpa2",
                        "hidden": False,
                        "isGuest": False,
                    },
                },
            },
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    def test_switch_unique_id(self, mock_coordinator) -> None:
        """Test switch has correct unique ID."""
        wifi_data = mock_coordinator.data["wifi"]["site1"]["wifi1"]
        switch = UnifiWifiSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
            wifi_data=wifi_data,
        )

        assert switch._attr_unique_id == "site1_wifi1_wifi_switch"

    def test_switch_name(self, mock_coordinator) -> None:
        """Test switch has correct name."""
        wifi_data = mock_coordinator.data["wifi"]["site1"]["wifi1"]
        switch = UnifiWifiSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
            wifi_data=wifi_data,
        )

        assert switch._attr_translation_key == "wifi"
        assert switch._attr_translation_placeholders == {"wifi_name": "Home Network"}

    def test_switch_device_info(self, mock_coordinator) -> None:
        """Test switch device info is set correctly."""
        wifi_data = mock_coordinator.data["wifi"]["site1"]["wifi1"]
        switch = UnifiWifiSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
            wifi_data=wifi_data,
        )

        assert switch._attr_device_info["identifiers"] == {(DOMAIN, "wifi_wifi1")}
        assert switch._attr_device_info["name"] == "WiFi: Home Network"

    def test_switch_is_on_when_enabled(self, mock_coordinator) -> None:
        """Test switch is ON when WiFi is enabled."""
        wifi_data = mock_coordinator.data["wifi"]["site1"]["wifi1"]
        switch = UnifiWifiSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
            wifi_data=wifi_data,
        )

        assert switch.is_on is True

    def test_switch_is_off_when_disabled(self, mock_coordinator) -> None:
        """Test switch is OFF when WiFi is disabled."""
        mock_coordinator.data["wifi"]["site1"]["wifi1"]["enabled"] = False
        wifi_data = mock_coordinator.data["wifi"]["site1"]["wifi1"]

        switch = UnifiWifiSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
            wifi_data=wifi_data,
        )

        assert switch.is_on is False

    def test_extra_state_attributes(self, mock_coordinator) -> None:
        """Test extra state attributes are returned."""
        wifi_data = mock_coordinator.data["wifi"]["site1"]["wifi1"]
        switch = UnifiWifiSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
            wifi_data=wifi_data,
        )

        attrs = switch.extra_state_attributes
        assert attrs["wifi_id"] == "wifi1"
        assert attrs["ssid"] == "HomeWiFi"
        assert attrs["security"] == "wpa2"
        assert attrs["hidden"] is False
        assert attrs["is_guest"] is False

    @pytest.mark.asyncio
    async def test_turn_on_enables_wifi(self, mock_coordinator) -> None:
        """Test turning ON enables the WiFi network."""
        mock_coordinator.data["wifi"]["site1"]["wifi1"]["enabled"] = False
        wifi_data = mock_coordinator.data["wifi"]["site1"]["wifi1"]

        switch = UnifiWifiSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
            wifi_data=wifi_data,
        )

        await switch.async_turn_on()

        mock_coordinator.network_client.wifi.update.assert_called_once_with(
            "site1", "wifi1", enabled=True
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_off_disables_wifi(self, mock_coordinator) -> None:
        """Test turning OFF disables the WiFi network."""
        wifi_data = mock_coordinator.data["wifi"]["site1"]["wifi1"]

        switch = UnifiWifiSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
            wifi_data=wifi_data,
        )

        await switch.async_turn_off()

        mock_coordinator.network_client.wifi.update.assert_called_once_with(
            "site1", "wifi1", enabled=False
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    def test_available_when_wifi_data_exists(self, mock_coordinator) -> None:
        """Test switch is available when WiFi data exists."""
        wifi_data = mock_coordinator.data["wifi"]["site1"]["wifi1"]
        switch = UnifiWifiSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
            wifi_data=wifi_data,
        )

        assert switch.available is True

    def test_available_falls_back_to_initial_data(self, mock_coordinator) -> None:
        """Test switch uses initial data when coordinator data is empty."""
        wifi_data = mock_coordinator.data["wifi"]["site1"]["wifi1"].copy()
        mock_coordinator.data["wifi"]["site1"]["wifi1"] = {}

        switch = UnifiWifiSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
            wifi_data=wifi_data,
        )

        # Should fall back to initial wifi_data
        assert switch.available is True


class TestUnifiFirewallRuleSwitch:
    """Tests for firewall rule switches."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with firewall rules."""
        coordinator = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.firewall = MagicMock()
        coordinator.network_client.firewall.update_rule = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = {
            "sites": {"site1": {"id": "site1", "name": "Default"}},
            "devices": {
                "site1": {
                    "gateway1": {
                        "id": "gateway1",
                        "name": "Main Gateway",
                        "model": "UCG-Max",
                        "features": ["gateway"],
                        "state": "ONLINE",
                    }
                }
            },
            "clients": {},
            "stats": {},
            "wifi": {},
            "firewall_rules": {
                "site1": {
                    "rule1": {
                        "id": "rule1",
                        "name": "Block Instagram",
                        "enabled": True,
                        "action": {"type": "DENY"},
                        "protocol": "all",
                        "sourceZoneId": "internal",
                        "destinationZoneId": "external",
                        "logging": True,
                        "index": 2005,
                    },
                    "predefined_rule": {
                        "id": "predefined_rule",
                        "name": "System Rule",
                        "enabled": True,
                        "predefined": True,
                    },
                }
            },
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    def test_initialization(self, mock_coordinator) -> None:
        """Test firewall switch initialization."""
        switch = UnifiFirewallRuleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            rule_id="rule1",
        )

        assert switch._attr_unique_id == "site1_rule1_firewall_rule"

    def test_available_follows_site_firewall_fetch(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Firewall switch is unavailable when its site's firewall fetch failed."""
        switch = UnifiFirewallRuleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            rule_id="rule1",
        )
        mock_coordinator.firewall_available = MagicMock(return_value=True)
        assert switch.available is True

        mock_coordinator.firewall_available.return_value = False
        assert switch.available is False
        mock_coordinator.firewall_available.assert_called_with("site1")
        assert switch._attr_translation_key == "firewall_rule"
        assert switch._attr_translation_placeholders == {"rule_name": "Block Instagram"}
        assert switch._attr_entity_category == EntityCategory.CONFIG
        assert switch._attr_device_info["identifiers"] == {(DOMAIN, "site1_gateway1")}

    def test_is_on(self, mock_coordinator) -> None:
        """Test firewall switch state mirrors rule enabled state."""
        switch = UnifiFirewallRuleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            rule_id="rule1",
        )

        assert switch.is_on is True
        mock_coordinator.data["firewall_rules"]["site1"]["rule1"]["enabled"] = False
        assert switch.is_on is False

    def test_extra_state_attributes(self, mock_coordinator) -> None:
        """Test firewall switch metadata attributes."""
        switch = UnifiFirewallRuleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            rule_id="rule1",
        )

        attrs = switch.extra_state_attributes
        assert attrs["rule_id"] == "rule1"
        assert attrs["action"] == "DENY"
        assert attrs["protocol"] == "all"
        assert attrs["source_zone_id"] == "internal"
        assert attrs["destination_zone_id"] == "external"
        assert attrs["logging"] is True

    def test_icon_changes_with_state(self, mock_coordinator) -> None:
        """Test firewall switch icon reflects enabled state."""
        switch = UnifiFirewallRuleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            rule_id="rule1",
        )

        assert switch.icon == "mdi:shield-lock"
        mock_coordinator.data["firewall_rules"]["site1"]["rule1"]["enabled"] = False
        assert switch.icon == "mdi:shield-off"

    @pytest.mark.asyncio
    async def test_turn_on_updates_rule(self, mock_coordinator) -> None:
        """Test enabling a firewall rule."""
        mock_coordinator.data["firewall_rules"]["site1"]["rule1"]["enabled"] = False
        switch = UnifiFirewallRuleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            rule_id="rule1",
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()

        mock_coordinator.network_client.firewall.update_rule.assert_called_once_with(
            "site1", "rule1", enabled=True
        )
        assert (
            mock_coordinator.data["firewall_rules"]["site1"]["rule1"]["enabled"] is True
        )
        switch.async_write_ha_state.assert_called_once()
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_off_updates_rule(self, mock_coordinator) -> None:
        """Test disabling a firewall rule."""
        switch = UnifiFirewallRuleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            rule_id="rule1",
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_off()

        mock_coordinator.network_client.firewall.update_rule.assert_called_once_with(
            "site1", "rule1", enabled=False
        )
        assert (
            mock_coordinator.data["firewall_rules"]["site1"]["rule1"]["enabled"]
            is False
        )
        switch.async_write_ha_state.assert_called_once()
        mock_coordinator.async_request_refresh.assert_called_once()

    def test_fallback_device_info_without_gateway(self, mock_coordinator) -> None:
        """Test fallback device registry entry when no gateway device is found."""
        mock_coordinator.data["devices"]["site1"] = {}

        switch = UnifiFirewallRuleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            rule_id="rule1",
        )

        assert switch._attr_device_info["identifiers"] == {
            (DOMAIN, "firewall_policies_site1")
        }
        assert switch._attr_device_info["name"] == "Firewall Policies (Default)"

    @pytest.mark.asyncio
    async def test_turn_on_error_does_not_write_state(self, mock_coordinator) -> None:
        """Test firewall update failures do not write optimistic state."""
        mock_coordinator.network_client.firewall.update_rule.side_effect = Exception(
            "API error"
        )
        mock_coordinator.data["firewall_rules"]["site1"]["rule1"]["enabled"] = False

        switch = UnifiFirewallRuleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            rule_id="rule1",
        )
        switch.async_write_ha_state = MagicMock()

        with pytest.raises(HomeAssistantError, match="Unable to update firewall rule"):
            await switch.async_turn_on()

        switch.async_write_ha_state.assert_not_called()
        assert (
            mock_coordinator.data["firewall_rules"]["site1"]["rule1"]["enabled"]
            is False
        )

    def test_update_local_state_rule_not_dict(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test _update_local_state when rule data is absent or not a dict."""
        switch = UnifiFirewallRuleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            rule_id="nonexistent_rule",
        )
        original_data = deepcopy(mock_coordinator.data)
        switch._update_local_state(enabled=False)
        assert mock_coordinator.data == original_data


class TestAsyncSetupEntryFirewallRules:
    """Tests firewall rule discovery in switch platform setup."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with firewall rule data."""
        coordinator = MagicMock()
        coordinator.protect_client = None
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.network_client.firewall = MagicMock()
        coordinator.network_client.firewall.update_rule = AsyncMock()
        coordinator.data = {
            "sites": {"site1": {"id": "site1", "name": "Default"}},
            "devices": {
                "site1": {
                    "gateway1": {
                        "id": "gateway1",
                        "name": "Main Gateway",
                        "model": "UCG-Max",
                        "features": ["gateway"],
                        "state": "ONLINE",
                    }
                }
            },
            "clients": {},
            "wifi": {},
            "firewall_rules": {
                "site1": {
                    "rule1": {
                        "id": "rule1",
                        "name": "Block Instagram",
                        "enabled": True,
                    },
                    "rule2": {
                        "id": "rule2",
                        "name": "School Nights",
                        "enabled": False,
                    },
                    "system": {
                        "id": "system",
                        "name": "System",
                        "enabled": True,
                        "predefined": True,
                    },
                    "system_defined": {
                        "id": "system_defined",
                        "name": "Allow mDNS",
                        "enabled": True,
                        "metadata": {"origin": "SYSTEM_DEFINED"},
                    },
                }
            },
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    @pytest.mark.asyncio
    async def test_setup_entry_adds_only_user_firewall_rules(
        self, hass, mock_coordinator
    ) -> None:
        """Test setup adds switches only for user-defined firewall rules."""
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        firewall_switches = [
            entity for entity in entities if isinstance(entity, UnifiFirewallRuleSwitch)
        ]
        assert len(firewall_switches) == 2
        assert {entity._rule_id for entity in firewall_switches} == {"rule1", "rule2"}

    @pytest.mark.asyncio
    async def test_setup_entry_firewall_rules_malformed_record_and_dedupe(
        self, hass, mock_coordinator
    ) -> None:
        """A malformed rule record is skipped, and rediscovery on unchanged
        data does not duplicate the remaining switches."""
        mock_coordinator.data["firewall_rules"]["site1"]["bad_rule"] = "not-a-dict"

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        entities = async_add_entities.call_args[0][0]
        firewall_switches = [
            entity for entity in entities if isinstance(entity, UnifiFirewallRuleSwitch)
        ]
        assert len(firewall_switches) == 2
        assert all(e._rule_id != "bad_rule" for e in firewall_switches)

        # Re-running discovery on unchanged data must not add duplicates.
        listener()

        assert async_add_entities.call_count == 1


class TestUnifiPolicyBasedRouteSwitch:
    """Tests for policy-based route switches."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with policy-based routes."""
        coordinator = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.routes = MagicMock()
        coordinator.network_client.routes.update_route = AsyncMock()
        coordinator.resolve_legacy_site_name = MagicMock(return_value="default")
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = {
            "sites": {"site1": {"id": "site1", "name": "Default"}},
            "devices": {
                "site1": {
                    "gateway1": {
                        "id": "gateway1",
                        "name": "Main Gateway",
                        "model": "UCG-Max",
                        "features": ["gateway"],
                        "state": "ONLINE",
                    }
                }
            },
            "clients": {},
            "stats": {},
            "wifi": {},
            "firewall_rules": {},
            "policy_based_routes": {
                "site1": {
                    "route1": {
                        "id": "route1",
                        "description": "Route via Privado VPN",
                        "enabled": True,
                        "interface": "vpn",
                        "vpnClientId": "vpn123",
                        "matchingTarget": "DOMAIN",
                        "killSwitch": True,
                        "fallBackToDefaultWAN": False,
                        "domains": ["privado.com"],
                        "ipAddresses": ["1.2.3.4"],
                        "clientMacs": ["aa:bb:cc:dd:ee:ff"],
                        "networkIds": ["net1"],
                    },
                    "route2": {
                        "id": "route2",
                        "name": "Route WAN2",
                        "enabled": False,
                        "interface": "WAN2",
                    },
                }
            },
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    def test_initialization(self, mock_coordinator: MagicMock) -> None:
        """Test policy-based route switch initialization."""
        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route1",
        )

        assert switch._attr_unique_id == "site1_route1_policy_based_route"
        assert switch._attr_translation_key == "policy_based_route"
        assert switch._attr_translation_placeholders == {
            "route_name": "Route via Privado VPN"
        }
        assert switch._attr_entity_category == EntityCategory.CONFIG
        assert switch._attr_device_info["identifiers"] == {(DOMAIN, "site1_gateway1")}

    def test_available_follows_config_refresh(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Route switch is unavailable while the config refresh is failing."""
        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route1",
        )
        mock_coordinator.config_available = True
        assert switch.available is True

        mock_coordinator.config_available = False
        assert switch.available is False

    def test_is_on(self, mock_coordinator: MagicMock) -> None:
        """Test switch state mirrors route enabled state."""
        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route1",
        )

        assert switch.is_on is True
        mock_coordinator.data["policy_based_routes"]["site1"]["route1"]["enabled"] = (
            False
        )
        assert switch.is_on is False

    def test_extra_state_attributes(self, mock_coordinator: MagicMock) -> None:
        """Test route metadata attributes."""
        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route1",
        )

        attrs = switch.extra_state_attributes
        assert attrs["route_id"] == "route1"
        assert attrs["description"] == "Route via Privado VPN"
        assert attrs["interface"] == "vpn"
        assert attrs["vpn_client_id"] == "vpn123"
        assert attrs["matching_target"] == "DOMAIN"
        assert attrs["kill_switch_enabled"] is True
        assert attrs["fall_back_to_default_wan"] is False
        assert attrs["domains"] == ["privado.com"]
        assert attrs["ip_addresses"] == ["1.2.3.4"]
        assert attrs["client_macs"] == ["aa:bb:cc:dd:ee:ff"]
        assert attrs["network_ids"] == ["net1"]

    def test_extra_state_attributes_live_controller_payload(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test attributes for a route shaped like the live controller payload.

        Captured from a UniFi Dream Machine SE (UniFi OS 5.1.31, Network
        10.6.101): the controller uses ``kill_switch_enabled``, not
        ``killSwitch``/``kill_switch``, and also sends ``network_id``,
        ``next_hop``, ``regions``, ``ip_ranges``, and ``target_devices``.
        """
        mock_coordinator.data["policy_based_routes"]["site1"]["route3"] = {
            "id": "route3",
            "description": "Nord Route",
            "enabled": True,
            "matching_target": "INTERNET",
            "kill_switch_enabled": True,
            "domains": [],
            "ip_addresses": [],
            "ip_ranges": [],
            "next_hop": "",
            "regions": [],
            "network_id": "67678a746c1d8157c66f444e",
            "target_devices": [
                {"network_id": "67678b326c1d8157c66f4466", "type": "NETWORK"}
            ],
        }
        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route3",
        )

        attrs = switch.extra_state_attributes
        assert attrs["kill_switch_enabled"] is True
        assert attrs["network_id"] == "67678a746c1d8157c66f444e"
        assert attrs["next_hop"] == ""
        assert attrs["regions"] == []
        assert attrs["ip_ranges"] == []
        assert attrs["target_devices"] == [
            {"network_id": "67678b326c1d8157c66f4466", "type": "NETWORK"}
        ]

    @pytest.mark.asyncio
    async def test_turn_off_warns_when_kill_switch_enabled_live_payload(
        self, mock_coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test disable warning fires for the live ``kill_switch_enabled`` field."""
        mock_coordinator.data["policy_based_routes"]["site1"]["route3"] = {
            "id": "route3",
            "description": "Nord Route",
            "enabled": True,
            "kill_switch_enabled": True,
        }
        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route3",
        )
        switch.async_write_ha_state = MagicMock()

        with caplog.at_level(logging.WARNING):
            await switch.async_turn_off()

        assert any("kill switch" in record.message.lower() for record in caplog.records)

    @pytest.mark.parametrize(
        ("route_kwargs", "expected"),
        [
            ({"kill_switch_enabled": True}, True),
            ({"kill_switch_enabled": False}, False),
            ({"kill_switch": True}, True),
            ({"kill_switch": False}, False),
            ({}, None),
        ],
        ids=[
            "live-shape-on",
            "live-shape-off",
            "legacy-shape-on",
            "legacy-shape-off",
            "unset",
        ],
    )
    def test_kill_switch_resolves_through_coordinator_serialization(
        self,
        mock_coordinator: MagicMock,
        route_kwargs: dict[str, bool],
        *,
        expected: bool | None,
    ) -> None:
        """Test both payload shapes survive the coordinator's own serialization.

        The coordinator stores routes via ``_model_to_dict``, which dumps with
        ``by_alias=True, exclude_none=False`` -- so **every** alias key is
        present, ``None`` included. A membership test (``key in route_data``)
        would stop at ``killSwitchEnabled`` and return ``None`` for a
        legacy-shape route, so the resolver must skip ``None`` values rather
        than merely-absent keys. Building the dict from a real
        ``PolicyBasedRoute`` here is the point: hand-built dicts that omit the
        sibling alias key do not reproduce real coordinator data.
        """
        route = PolicyBasedRoute.model_validate(
            {
                "_id": "route4",
                "description": "Serialized Route",
                "enabled": True,
                **route_kwargs,
            }
        )
        route_data = UnifiBaseCoordinator._model_to_dict(mock_coordinator, route)

        # Guard the premise: both alias keys really are present in the dump.
        assert "killSwitchEnabled" in route_data
        assert "killSwitch" in route_data

        mock_coordinator.data["policy_based_routes"]["site1"]["route4"] = route_data
        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route4",
        )

        assert switch._kill_switch_enabled is expected
        assert switch.extra_state_attributes["kill_switch_enabled"] is expected

    @pytest.mark.asyncio
    async def test_turn_off_warns_for_legacy_shape_through_serialization(
        self, mock_coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test the disable warning fires for a serialized legacy-shape route."""
        route = PolicyBasedRoute.model_validate(
            {
                "_id": "route4",
                "description": "Serialized Route",
                "enabled": True,
                "kill_switch": True,
            }
        )
        mock_coordinator.data["policy_based_routes"]["site1"]["route4"] = (
            UnifiBaseCoordinator._model_to_dict(mock_coordinator, route)
        )
        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route4",
        )
        switch.async_write_ha_state = MagicMock()

        with caplog.at_level(logging.WARNING):
            await switch.async_turn_off()

        assert any("kill switch" in record.message.lower() for record in caplog.records)

    def test_icon_changes_with_state(self, mock_coordinator: MagicMock) -> None:
        """Test route switch icon reflects VPN and enabled state."""
        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route1",
        )
        assert switch.icon == "mdi:vpn"
        mock_coordinator.data["policy_based_routes"]["site1"]["route1"]["enabled"] = (
            False
        )
        assert switch.icon == "mdi:vpn-off"

        # Non-VPN route
        switch2 = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route2",
        )
        assert switch2.icon == "mdi:routes-clock"
        mock_coordinator.data["policy_based_routes"]["site1"]["route2"]["enabled"] = (
            True
        )
        assert switch2.icon == "mdi:routes"

    @pytest.mark.asyncio
    async def test_turn_on_updates_route(self, mock_coordinator: MagicMock) -> None:
        """Test enabling a policy-based route."""
        mock_coordinator.data["policy_based_routes"]["site1"]["route1"]["enabled"] = (
            False
        )
        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route1",
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()

        mock_coordinator.network_client.routes.update_route.assert_called_once_with(
            "default", "route1", enabled=True
        )
        assert (
            mock_coordinator.data["policy_based_routes"]["site1"]["route1"]["enabled"]
            is True
        )
        switch.async_write_ha_state.assert_called_once()
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_off_updates_route(self, mock_coordinator: MagicMock) -> None:
        """Test disabling a policy-based route."""
        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route1",
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_off()

        mock_coordinator.network_client.routes.update_route.assert_called_once_with(
            "default", "route1", enabled=False
        )
        assert (
            mock_coordinator.data["policy_based_routes"]["site1"]["route1"]["enabled"]
            is False
        )
        switch.async_write_ha_state.assert_called_once()
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_on_fallback_uses_resolved_site_name(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test the fallback path resolves the real site name on multi-site."""
        mock_coordinator.resolve_legacy_site_name = MagicMock(return_value="mysite")
        mock_coordinator.data["policy_based_routes"]["site1"]["route1"]["enabled"] = (
            False
        )
        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route1",
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()

        mock_coordinator.network_client.routes.update_route.assert_called_once_with(
            "mysite", "route1", enabled=True
        )

    @pytest.mark.asyncio
    async def test_turn_off_warns_when_kill_switch_enabled(
        self, mock_coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test disabling a route with an active kill switch logs a warning."""
        # route1 has killSwitch=True in the fixture data.
        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route1",
        )
        switch.async_write_ha_state = MagicMock()

        with caplog.at_level(logging.WARNING):
            await switch.async_turn_off()

        assert any("kill switch" in record.message.lower() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_turn_off_no_warning_when_kill_switch_disabled(
        self, mock_coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test disabling a route without a kill switch logs no warning."""
        # route2 has no killSwitch key in the fixture data.
        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route2",
        )
        switch.async_write_ha_state = MagicMock()

        with caplog.at_level(logging.WARNING):
            await switch.async_turn_off()

        assert not any(
            "kill switch" in record.message.lower() for record in caplog.records
        )

    def test_fallback_device_info_without_gateway(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test fallback device registry entry when no gateway device is found."""
        mock_coordinator.data["devices"]["site1"] = {}

        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route1",
        )

        assert switch._attr_device_info["identifiers"] == {
            (DOMAIN, "policy_based_routes_site1")
        }
        assert switch._attr_device_info["name"] == "Policy-Based Routes (Default)"

    @pytest.mark.asyncio
    async def test_turn_on_error_does_not_write_state(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test route update failures do not write optimistic state."""
        mock_coordinator.network_client.routes.update_route.side_effect = Exception(
            "API error"
        )
        mock_coordinator.data["policy_based_routes"]["site1"]["route1"]["enabled"] = (
            False
        )

        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route1",
        )
        switch.async_write_ha_state = MagicMock()

        with pytest.raises(
            HomeAssistantError, match="Unable to update policy-based route"
        ):
            await switch.async_turn_on()

        switch.async_write_ha_state.assert_not_called()
        assert (
            mock_coordinator.data["policy_based_routes"]["site1"]["route1"]["enabled"]
            is False
        )

    @pytest.mark.asyncio
    async def test_turn_off_error_does_not_write_state(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test route disable failures do not write optimistic state."""
        mock_coordinator.network_client.routes.update_route.side_effect = Exception(
            "API error"
        )
        mock_coordinator.data["policy_based_routes"]["site1"]["route1"]["enabled"] = (
            True
        )

        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route1",
        )
        switch.async_write_ha_state = MagicMock()

        with pytest.raises(
            HomeAssistantError, match="Unable to update policy-based route"
        ):
            await switch.async_turn_off()

        switch.async_write_ha_state.assert_not_called()
        assert (
            mock_coordinator.data["policy_based_routes"]["site1"]["route1"]["enabled"]
            is True
        )

    def test_unnamed_policy_based_route_switch(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test unnamed policy route switch falls back to unnamed key."""
        mock_coordinator.data.setdefault("policy_based_routes", {})["site1"] = {
            "route_unnamed": {"id": "route_unnamed", "name": ""}
        }
        entity = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="route_unnamed",
        )
        assert entity.translation_key == "policy_based_route_unnamed"
        assert entity.translation_placeholders == {"route_id": "route_unnamed"}

    def test_update_local_state_route_not_dict(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test _update_local_state when route data is absent or not a dict."""
        switch = UnifiPolicyBasedRouteSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            route_id="nonexistent_route",
        )
        original_data = deepcopy(mock_coordinator.data)
        switch._update_local_state(enabled=False)
        assert mock_coordinator.data == original_data


class TestAsyncSetupEntryPolicyBasedRoutes:
    """Tests policy-based route discovery in switch platform setup."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with policy-based route data."""
        coordinator = MagicMock()
        coordinator.protect_client = None
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.network_client.routes = MagicMock()
        coordinator.network_client.routes.update_route = AsyncMock()
        coordinator.resolve_legacy_site_name = MagicMock(return_value="default")
        coordinator.data = {
            "sites": {"site1": {"id": "site1", "name": "Default"}},
            "devices": {},
            "clients": {},
            "stats": {},
            "wifi": {},
            "firewall_rules": {},
            "policy_based_routes": {
                "site1": {
                    "route1": {
                        "id": "route1",
                        "description": "Route 1",
                        "enabled": True,
                    },
                    "route2": {
                        "id": "route2",
                        "name": "Route 2",
                        "enabled": False,
                    },
                }
            },
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    @pytest.mark.asyncio
    async def test_setup_entry_creates_route_switches(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test policy-based route switches created during platform setup."""
        mock_entry = MagicMock()
        mock_entry.options = {}
        mock_entry.entry_id = "test_entry_id"
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        route_switches = [
            entity
            for entity in entities
            if isinstance(entity, UnifiPolicyBasedRouteSwitch)
        ]
        assert len(route_switches) == 2
        assert {entity._route_id for entity in route_switches} == {"route1", "route2"}

    @pytest.mark.asyncio
    async def test_setup_entry_routes_malformed_site_and_record_and_dedupe(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Non-dict site collections/route records are skipped, and
        rediscovery on unchanged data does not duplicate switches."""
        mock_coordinator.data["policy_based_routes"]["site2"] = "not-a-dict"
        mock_coordinator.data["policy_based_routes"]["site1"]["bad_route"] = (
            "not-a-dict"
        )

        mock_entry = MagicMock()
        mock_entry.options = {}
        mock_entry.entry_id = "test_entry_id"
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        entities = async_add_entities.call_args[0][0]
        route_switches = [
            entity
            for entity in entities
            if isinstance(entity, UnifiPolicyBasedRouteSwitch)
        ]
        assert len(route_switches) == 2
        assert all(e._route_id != "bad_route" for e in route_switches)

        # Re-running discovery on unchanged data must not add duplicates.
        listener()

        assert async_add_entities.call_count == 1


class TestUnifiVpnClientSwitch:
    """Tests for UnifiVpnClientSwitch entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with VPN client data."""
        coordinator = MagicMock()
        coordinator.protect_client = None
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.network_client.vpn_clients = MagicMock()
        coordinator.network_client.vpn_clients.update_vpn_client = AsyncMock()
        coordinator.resolve_legacy_site_name = MagicMock(return_value="default")
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = {
            "sites": {"site1": {"id": "site1", "name": "Default"}},
            "devices": {
                "site1": {
                    "gateway1": {
                        "id": "gateway1",
                        "model": "UDM-PRO",
                        "features": {"gateway": True},
                    }
                }
            },
            "clients": {},
            "stats": {},
            "wifi": {},
            "firewall_rules": {},
            "policy_based_routes": {},
            "vpn_clients": {
                "site1": {
                    "vpn1": {
                        "id": "vpn1",
                        "name": "Privado VPN",
                        "purpose": "vpn-client",
                        "vpn_type": "openvpn-client",
                        "enabled": True,
                        "ip_subnet": "172.21.25.217/32",
                        "openvpn_id": 1,
                        "remote_host": "syd-012.vpn.privado.io",
                    },
                    "vpn2": {
                        "id": "vpn2",
                        "name": "WireGuard Client",
                        "purpose": "vpn-client",
                        "vpn_type": "wireguard-client",
                        "enabled": False,
                    },
                }
            },
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    def test_initialization(self, mock_coordinator: MagicMock) -> None:
        """Test VPN client switch initialization."""
        switch = UnifiVpnClientSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="vpn1",
        )

        assert switch._attr_unique_id == "site1_vpn1_vpn_client"
        assert switch._attr_translation_key == "vpn_client"
        assert switch._attr_translation_placeholders == {
            "vpn_client_name": "Privado VPN"
        }
        assert switch._attr_entity_category == EntityCategory.CONFIG
        assert switch._attr_device_info["identifiers"] == {(DOMAIN, "site1_gateway1")}

    def test_available(self, mock_coordinator: MagicMock) -> None:
        """Test switch availability depends on coordinator and data."""
        switch = UnifiVpnClientSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="vpn1",
        )
        assert switch.available is True
        mock_coordinator.config_available = False
        assert switch.available is False

        mock_coordinator.last_update_success = True
        mock_coordinator.data["vpn_clients"]["site1"] = {}
        assert switch.available is False

    def test_is_on(self, mock_coordinator: MagicMock) -> None:
        """Test switch state mirrors VPN client enabled state."""
        switch = UnifiVpnClientSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="vpn1",
        )

        assert switch.is_on is True
        mock_coordinator.data["vpn_clients"]["site1"]["vpn1"]["enabled"] = False
        assert switch.is_on is False

    def test_extra_state_attributes(self, mock_coordinator: MagicMock) -> None:
        """Test VPN client metadata attributes."""
        switch = UnifiVpnClientSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="vpn1",
        )

        attrs = switch.extra_state_attributes
        assert attrs["client_id"] == "vpn1"
        assert attrs["name"] == "Privado VPN"
        assert attrs["purpose"] == "vpn-client"
        assert attrs["vpn_type"] == "openvpn-client"
        assert attrs["ip_subnet"] == "172.21.25.217/32"
        assert attrs["openvpn_id"] == 1
        assert attrs["remote_host"] == "syd-012.vpn.privado.io"

    def test_icon_changes_with_state(self, mock_coordinator: MagicMock) -> None:
        """Test VPN client switch icon reflects enabled state."""
        switch = UnifiVpnClientSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="vpn1",
        )
        assert switch.icon == "mdi:vpn"
        mock_coordinator.data["vpn_clients"]["site1"]["vpn1"]["enabled"] = False
        assert switch.icon == "mdi:vpn-off"

    @pytest.mark.asyncio
    async def test_turn_on_updates_vpn_client(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test enabling a VPN client."""
        mock_coordinator.data["vpn_clients"]["site1"]["vpn1"]["enabled"] = False
        switch = UnifiVpnClientSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="vpn1",
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()

        mock_update = mock_coordinator.network_client.vpn_clients.update_vpn_client
        mock_update.assert_called_once_with("default", "vpn1", enabled=True)
        assert mock_coordinator.data["vpn_clients"]["site1"]["vpn1"]["enabled"] is True
        switch.async_write_ha_state.assert_called_once()
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_off_updates_vpn_client(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test disabling a VPN client."""
        switch = UnifiVpnClientSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="vpn1",
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_off()

        mock_update = mock_coordinator.network_client.vpn_clients.update_vpn_client
        mock_update.assert_called_once_with("default", "vpn1", enabled=False)
        assert mock_coordinator.data["vpn_clients"]["site1"]["vpn1"]["enabled"] is False
        switch.async_write_ha_state.assert_called_once()
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_on_fallback_uses_resolved_site_name(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test the fallback path resolves the real site name on multi-site."""
        mock_coordinator.resolve_legacy_site_name = MagicMock(return_value="mysite")
        mock_coordinator.data["vpn_clients"]["site1"]["vpn1"]["enabled"] = False
        switch = UnifiVpnClientSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="vpn1",
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()

        mock_update = mock_coordinator.network_client.vpn_clients.update_vpn_client
        mock_update.assert_called_once_with("mysite", "vpn1", enabled=True)
        mock_coordinator.async_request_refresh.assert_called_once()

    def test_fallback_device_info_without_gateway(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test fallback device registry entry when no gateway device is found."""
        mock_coordinator.data["devices"]["site1"] = {}

        switch = UnifiVpnClientSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="vpn1",
        )

        assert switch._attr_device_info["identifiers"] == {
            (DOMAIN, "vpn_clients_site1")
        }
        assert switch._attr_device_info["name"] == "VPN Clients (Default)"

    @pytest.mark.asyncio
    async def test_turn_on_error_does_not_write_state(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test VPN client update failures do not write optimistic state."""
        mock_coordinator.network_client.vpn_clients.update_vpn_client.side_effect = (
            Exception("API error")
        )
        mock_coordinator.data["vpn_clients"]["site1"]["vpn1"]["enabled"] = False

        switch = UnifiVpnClientSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="vpn1",
        )
        switch.async_write_ha_state = MagicMock()

        with pytest.raises(HomeAssistantError, match="Unable to update VPN client"):
            await switch.async_turn_on()

        switch.async_write_ha_state.assert_not_called()
        assert mock_coordinator.data["vpn_clients"]["site1"]["vpn1"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_turn_off_error_does_not_write_state(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test VPN client disable failures do not write optimistic state."""
        mock_coordinator.network_client.vpn_clients.update_vpn_client.side_effect = (
            Exception("API error")
        )
        mock_coordinator.data["vpn_clients"]["site1"]["vpn1"]["enabled"] = True

        switch = UnifiVpnClientSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="vpn1",
        )
        switch.async_write_ha_state = MagicMock()

        with pytest.raises(HomeAssistantError, match="Unable to update VPN client"):
            await switch.async_turn_off()

        switch.async_write_ha_state.assert_not_called()
        assert mock_coordinator.data["vpn_clients"]["site1"]["vpn1"]["enabled"] is True

    def test_unnamed_vpn_client_switch(self, mock_coordinator: MagicMock) -> None:
        """Test unnamed VPN client switch falls back to unnamed key."""
        mock_coordinator.data.setdefault("vpn_clients", {})["site1"] = {
            "vpn_unnamed": {"id": "vpn_unnamed"}
        }
        entity = UnifiVpnClientSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="vpn_unnamed",
        )
        assert entity.translation_key == "vpn_client_unnamed"

    def test_update_local_state_client_not_in_site(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test _update_local_state when client_id is not in site_vpn_clients."""
        switch = UnifiVpnClientSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="nonexistent_vpn",
        )
        original_data = deepcopy(mock_coordinator.data)
        switch._update_local_state(enabled=False)
        assert mock_coordinator.data == original_data


class TestAsyncSetupEntryVpnClients:
    """Tests VPN client discovery in switch platform setup."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with VPN client data."""
        coordinator = MagicMock()
        coordinator.protect_client = None
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.network_client.vpn_clients = MagicMock()
        coordinator.network_client.vpn_clients.update_vpn_client = AsyncMock()
        coordinator.resolve_legacy_site_name = MagicMock(return_value="default")
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = {
            "sites": {"site1": {"id": "site1", "name": "Default"}},
            "devices": {},
            "clients": {},
            "stats": {},
            "wifi": {},
            "firewall_rules": {},
            "policy_based_routes": {},
            "vpn_clients": {
                "site1": {
                    "vpn1": {
                        "id": "vpn1",
                        "name": "Privado VPN",
                        "purpose": "vpn-client",
                        "enabled": True,
                    },
                    "vpn2": {
                        "id": "vpn2",
                        "name": "WireGuard Client",
                        "purpose": "vpn-client",
                        "enabled": False,
                    },
                }
            },
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    @pytest.mark.asyncio
    async def test_setup_entry_creates_vpn_client_switches(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test VPN client switches created during platform setup."""
        mock_entry = MagicMock()
        mock_entry.options = {}
        mock_entry.entry_id = "test_entry_id"
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        vpn_switches = [
            entity for entity in entities if isinstance(entity, UnifiVpnClientSwitch)
        ]
        assert len(vpn_switches) == 2
        assert {entity._client_id for entity in vpn_switches} == {"vpn1", "vpn2"}

    @pytest.mark.asyncio
    async def test_setup_entry_vpn_clients_malformed_site_and_record_and_dedupe(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Non-dict site collections/VPN client records are skipped, and
        rediscovery on unchanged data does not duplicate switches."""
        mock_coordinator.data["vpn_clients"]["site2"] = "not-a-dict"
        mock_coordinator.data["vpn_clients"]["site1"]["bad_vpn"] = "not-a-dict"

        mock_entry = MagicMock()
        mock_entry.options = {}
        mock_entry.entry_id = "test_entry_id"
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        entities = async_add_entities.call_args[0][0]
        vpn_switches = [
            entity for entity in entities if isinstance(entity, UnifiVpnClientSwitch)
        ]
        assert len(vpn_switches) == 2
        assert all(e._client_id != "bad_vpn" for e in vpn_switches)

        # Re-running discovery on unchanged data must not add duplicates.
        listener()

        assert async_add_entities.call_count == 1


class TestObsoletePrivacySwitchCleanup:
    """Tests for privacy mode switch removal and entity registry cleanup."""

    @pytest.mark.asyncio
    async def test_camera_discovery_does_not_create_privacy_switch(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Assert that camera discovery does not create a privacy mode switch."""
        mock_coordinator.data["protect"]["cameras"] = {
            "camera1": {
                "id": "camera1",
                "name": "Front Camera",
                "state": "CONNECTED",
            }
        }
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry"
        mock_entry.options = {CONF_CLIENT_CONTROL: False}
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()
        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        assert not any(e.unique_id.endswith("_privacy_mode") for e in entities)
        assert not any(
            getattr(e, "_attr_translation_key", None) == "privacy_mode"
            for e in entities
        )

    @pytest.mark.asyncio
    async def test_setup_removes_obsolete_privacy_switch_registry_entry(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Assert that setup removes obsolete privacy mode switch entries
        and preserves other switches.
        """
        mock_coordinator.data["protect"]["cameras"] = {}
        mock_entry = MockConfigEntry(
            domain=DOMAIN,
            title="UniFi Protect Test",
            data={},
            entry_id="test_cleanup_entry",
            options={CONF_CLIENT_CONTROL: True},
        )
        mock_entry.add_to_hass(hass)
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        registry: EntityRegistry = async_get_entity_registry(hass)
        stale_privacy_mode_entry = registry.async_get_or_create(
            "switch",
            DOMAIN,
            f"{DOMAIN}_camera_camera1_privacy_mode",
            config_entry=mock_entry,
        )
        preserved_light_entry = registry.async_get_or_create(
            "switch",
            DOMAIN,
            f"{DOMAIN}_camera_camera1_status_light",
            config_entry=mock_entry,
        )

        assert registry.async_get(stale_privacy_mode_entry.entity_id) is not None
        assert registry.async_get(preserved_light_entry.entity_id) is not None

        async_add_entities = MagicMock()
        await async_setup_entry(hass, mock_entry, async_add_entities)

        assert registry.async_get(stale_privacy_mode_entry.entity_id) is None
        assert registry.async_get(preserved_light_entry.entity_id) is not None


class TestUnifiProtectStatusLightSwitch:
    """Tests for UnifiProtectStatusLightSwitch entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.protect_client.cameras = MagicMock()
        coordinator.protect_client.cameras.update = AsyncMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "Test Camera",
                        "state": "CONNECTED",
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "type": "UVC-G4-Pro",
                        "firmwareVersion": "1.0.0",
                        "ledSettings": {"isEnabled": True},
                    }
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    def test_initialization(self, mock_coordinator) -> None:
        """Test switch entity initialization."""
        switch = UnifiProtectStatusLightSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert switch._device_id == "camera1"
        assert switch._device_type == DEVICE_TYPE_CAMERA
        assert switch._attr_has_entity_name is True
        assert switch._attr_translation_key == "status_light"
        assert switch._attr_entity_category == EntityCategory.CONFIG
        assert getattr(switch, "_attr_icon", None) is None

    def test_update_from_data_led_enabled(self, mock_coordinator) -> None:
        """Test _update_from_data with LED enabled."""
        switch = UnifiProtectStatusLightSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert switch._attr_is_on is True

    def test_update_from_data_led_disabled(self, mock_coordinator) -> None:
        """Test _update_from_data with LED disabled."""
        mock_coordinator.data["protect"]["cameras"]["camera1"]["ledSettings"] = {
            "isEnabled": False
        }

        switch = UnifiProtectStatusLightSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert switch._attr_is_on is False

    def test_update_from_data_no_led_settings(self, mock_coordinator) -> None:
        """Test _update_from_data when ledSettings is missing (defaults to True)."""
        del mock_coordinator.data["protect"]["cameras"]["camera1"]["ledSettings"]

        switch = UnifiProtectStatusLightSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        # Default is True when ledSettings is missing
        assert switch._attr_is_on is True

    def test_extra_state_attributes(self, mock_coordinator) -> None:
        """Test extra state attributes."""
        switch = UnifiProtectStatusLightSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        attrs = switch._attr_extra_state_attributes
        assert attrs[ATTR_CAMERA_ID] == "camera1"
        assert attrs[ATTR_CAMERA_NAME] == "Test Camera"
        assert attrs[ATTR_STATUS_LIGHT] is True

    @pytest.mark.asyncio
    async def test_async_turn_on_success(self, mock_coordinator) -> None:
        """Test turning status light on successfully."""
        mock_coordinator.data["protect"]["cameras"]["camera1"]["ledSettings"] = {
            "isEnabled": False
        }

        switch = UnifiProtectStatusLightSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()

        mock_coordinator.protect_client.cameras.update.assert_called_once_with(
            "camera1",
            ledSettings={"isEnabled": True},
        )
        assert switch._attr_is_on is True
        switch.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_on_error(self, mock_coordinator) -> None:
        """Test turning status light on with error."""
        mock_coordinator.protect_client.cameras.update.side_effect = Exception(
            "API error"
        )
        mock_coordinator.data["protect"]["cameras"]["camera1"]["ledSettings"] = {
            "isEnabled": False
        }

        switch = UnifiProtectStatusLightSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        switch.async_write_ha_state = MagicMock()

        with pytest.raises(HomeAssistantError, match="Unable to turn on status light"):
            await switch.async_turn_on()

        switch.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_turn_off_success(self, mock_coordinator) -> None:
        """Test turning status light off successfully."""
        switch = UnifiProtectStatusLightSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_off()

        mock_coordinator.protect_client.cameras.update.assert_called_once_with(
            "camera1",
            ledSettings={"isEnabled": False},
        )
        assert switch._attr_is_on is False
        switch.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_off_error(self, mock_coordinator) -> None:
        """Test turning status light off with error."""
        mock_coordinator.protect_client.cameras.update.side_effect = Exception(
            "API error"
        )

        switch = UnifiProtectStatusLightSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        switch.async_write_ha_state = MagicMock()

        with pytest.raises(HomeAssistantError, match="Unable to turn off status light"):
            await switch.async_turn_off()

        switch.async_write_ha_state.assert_not_called()


class TestUnifiProtectHighFPSSwitch:
    """Tests for UnifiProtectHighFPSSwitch entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.protect_client.cameras = MagicMock()
        coordinator.protect_client.cameras.update = AsyncMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "Test Camera",
                        "state": "CONNECTED",
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "type": "UVC-G4-Pro",
                        "firmwareVersion": "1.0.0",
                        "videoMode": "default",
                        "featureFlags": {"hasHighFpsCapability": True},
                    }
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    def test_initialization(self, mock_coordinator) -> None:
        """Test switch entity initialization."""
        switch = UnifiProtectHighFPSSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert switch._device_id == "camera1"
        assert switch._device_type == DEVICE_TYPE_CAMERA
        assert switch._attr_has_entity_name is True
        assert switch._attr_translation_key == "high_fps_mode"
        assert switch._attr_entity_category == EntityCategory.CONFIG
        assert getattr(switch, "_attr_icon", None) is None

    def test_update_from_data_default_mode(self, mock_coordinator) -> None:
        """Test _update_from_data with default video mode."""
        switch = UnifiProtectHighFPSSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert switch._attr_is_on is False

    def test_update_from_data_high_fps_mode(self, mock_coordinator) -> None:
        """Test _update_from_data with high FPS video mode."""
        mock_coordinator.data["protect"]["cameras"]["camera1"]["videoMode"] = (
            VIDEO_MODE_HIGH_FPS
        )

        switch = UnifiProtectHighFPSSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert switch._attr_is_on is True

    def test_update_from_data_sport_mode(self, mock_coordinator) -> None:
        """Test _update_from_data with sport video mode (not high FPS)."""
        mock_coordinator.data["protect"]["cameras"]["camera1"]["videoMode"] = "sport"

        switch = UnifiProtectHighFPSSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert switch._attr_is_on is False

    def test_extra_state_attributes(self, mock_coordinator) -> None:
        """Test extra state attributes."""
        switch = UnifiProtectHighFPSSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        attrs = switch._attr_extra_state_attributes
        assert attrs[ATTR_CAMERA_ID] == "camera1"
        assert attrs[ATTR_CAMERA_NAME] == "Test Camera"
        assert attrs[ATTR_HIGH_FPS_MODE] is False

    @pytest.mark.asyncio
    async def test_async_turn_on_success(self, mock_coordinator) -> None:
        """Test enabling high FPS mode successfully."""
        switch = UnifiProtectHighFPSSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()

        mock_coordinator.protect_client.cameras.update.assert_called_once_with(
            "camera1",
            videoMode=VIDEO_MODE_HIGH_FPS,
        )
        assert switch._attr_is_on is True
        switch.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_on_error(self, mock_coordinator) -> None:
        """Test enabling high FPS mode with error."""
        mock_coordinator.protect_client.cameras.update.side_effect = Exception(
            "API error"
        )

        switch = UnifiProtectHighFPSSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        switch._attr_is_on = False
        switch.async_write_ha_state = MagicMock()

        with pytest.raises(HomeAssistantError, match="Unable to enable high FPS mode"):
            await switch.async_turn_on()

        switch.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_turn_off_success(self, mock_coordinator) -> None:
        """Test disabling high FPS mode successfully."""
        mock_coordinator.data["protect"]["cameras"]["camera1"]["videoMode"] = (
            VIDEO_MODE_HIGH_FPS
        )

        switch = UnifiProtectHighFPSSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_off()

        mock_coordinator.protect_client.cameras.update.assert_called_once_with(
            "camera1",
            videoMode=VIDEO_MODE_DEFAULT,
        )
        assert switch._attr_is_on is False
        switch.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_off_error(self, mock_coordinator) -> None:
        """Test disabling high FPS mode with error."""
        mock_coordinator.protect_client.cameras.update.side_effect = Exception(
            "API error"
        )
        mock_coordinator.data["protect"]["cameras"]["camera1"]["videoMode"] = (
            VIDEO_MODE_HIGH_FPS
        )

        switch = UnifiProtectHighFPSSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        switch.async_write_ha_state = MagicMock()

        with pytest.raises(HomeAssistantError, match="Unable to disable high FPS mode"):
            await switch.async_turn_off()

        switch.async_write_ha_state.assert_not_called()


class TestAsyncSetupEntryWithNewSwitches:
    """Test async_setup_entry with new camera switches."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {},
            "devices": {},
            "clients": {},
            "wifi": {},
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "Test Camera",
                        "state": "CONNECTED",
                        "featureFlags": {"hasHighFpsCapability": True},
                    },
                    "camera2": {
                        "id": "camera2",
                        "name": "Basic Camera",
                        "state": "CONNECTED",
                        "featureFlags": {"hasHighFpsCapability": False},
                    },
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    @pytest.mark.asyncio
    async def test_setup_creates_all_camera_switches(
        self, hass, mock_coordinator
    ) -> None:
        """Test setup creates camera switches (mic, privacy, status, high FPS)."""
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]

        # Camera 1 gets 2 switches (status light, high FPS)
        # Camera 2 gets 1 switch (status light - no high FPS)
        # Total: 3 switches
        assert len(entities) == 3

        # Check types
        entity_types = [type(e).__name__ for e in entities]
        assert "UnifiProtectMicrophoneSwitch" not in entity_types
        assert "UnifiProtectPrivacySwitch" not in entity_types
        assert entity_types.count("UnifiProtectStatusLightSwitch") == 2
        assert entity_types.count("UnifiProtectHighFPSSwitch") == 1

    @pytest.mark.asyncio
    async def test_high_fps_only_for_capable_cameras(
        self, hass, mock_coordinator
    ) -> None:
        """Test high FPS switch is only created for cameras with capability."""
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]

        # Find high FPS switches
        high_fps_switches = [
            e for e in entities if isinstance(e, UnifiProtectHighFPSSwitch)
        ]

        # Should only have one high FPS switch (for camera1)
        assert len(high_fps_switches) == 1
        assert high_fps_switches[0]._device_id == "camera1"


class TestAsyncSetupEntryEdgeCases:
    """Tests for async_setup_entry edge cases to improve coverage."""

    @pytest.mark.asyncio
    async def test_camera_with_high_fps_capability(self, hass) -> None:
        """Test High FPS switch created for cameras with hasHighFpsCapability."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {},
            "devices": {},
            "stats": {},
            "clients": {},
            "wifi": {},
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "High FPS Camera",
                        "state": "CONNECTED",
                        "featureFlags": {"hasHighFpsCapability": True},
                    }
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        high_fps_switches = [
            e for e in entities if isinstance(e, UnifiProtectHighFPSSwitch)
        ]

        # Should have High FPS switch
        assert len(high_fps_switches) == 1

    @pytest.mark.asyncio
    async def test_camera_without_high_fps_capability(self, hass) -> None:
        """Test no High FPS switch for cameras without hasHighFpsCapability."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {},
            "devices": {},
            "stats": {},
            "clients": {},
            "wifi": {},
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "Basic Camera",
                        "state": "CONNECTED",
                        "featureFlags": {"hasHighFpsCapability": False},
                    }
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        high_fps_switches = [
            e for e in entities if isinstance(e, UnifiProtectHighFPSSwitch)
        ]

        # Should NOT have High FPS switch
        assert len(high_fps_switches) == 0

    @pytest.mark.asyncio
    async def test_camera_with_feature_flags_not_dict(self, hass) -> None:
        """Test camera with featureFlags not being a dict."""
        coordinator = MagicMock()
        coordinator.protect_client = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {},
            "devices": {},
            "stats": {},
            "clients": {},
            "wifi": {},
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "Basic Camera",
                        "state": "CONNECTED",
                        "featureFlags": None,  # Not a dict
                    }
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        high_fps_switches = [
            e for e in entities if isinstance(e, UnifiProtectHighFPSSwitch)
        ]

        # Should NOT have High FPS switch (featureFlags is not dict)
        assert len(high_fps_switches) == 0

    @pytest.mark.asyncio
    async def test_client_name_fallback_to_hostname(self, hass) -> None:
        """Test client name fallback from name to hostname (line 163)."""
        coordinator = MagicMock()
        coordinator.protect_client = None
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {"site1": {}},
            "stats": {},
            "clients": {
                "site1": {
                    "client1": {
                        "id": "client1",
                        "hostname": "test-hostname",  # No name, fallback to hostname
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "blocked": False,
                    }
                }
            },
            "wifi": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        client_switches = [e for e in entities if isinstance(e, UnifiClientBlockSwitch)]

        assert len(client_switches) == 1
        # Verify switch was created (hostname used for naming)
        assert client_switches[0]._client_id == "client1"

    @pytest.mark.asyncio
    async def test_client_name_fallback_to_mac(self, hass) -> None:
        """Test client name fallback from name/hostname to mac (lines 163-166)."""
        coordinator = MagicMock()
        coordinator.protect_client = None
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {"site1": {}},
            "stats": {},
            "clients": {
                "site1": {
                    "client1": {
                        "id": "client1",
                        # No name, no hostname, fallback to mac
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "blocked": False,
                    }
                }
            },
            "wifi": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        client_switches = [e for e in entities if isinstance(e, UnifiClientBlockSwitch)]

        assert len(client_switches) == 1

    @pytest.mark.asyncio
    async def test_wifi_name_fallback_to_ssid(self, hass) -> None:
        """Test WiFi name fallback from name to ssid (lines 182-183)."""
        coordinator = MagicMock()
        coordinator.protect_client = None
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {"site1": {}},
            "stats": {},
            "clients": {},
            "wifi": {
                "site1": {
                    "wifi1": {
                        "id": "wifi1",
                        "ssid": "MyNetwork",  # No name, fallback to ssid
                        "enabled": True,
                    }
                }
            },
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        wifi_switches = [e for e in entities if isinstance(e, UnifiWifiSwitch)]

        assert len(wifi_switches) == 1
        # Verify switch was created with ssid in name
        assert wifi_switches[0]._wifi_id == "wifi1"


class TestUnifiClientBlockSwitchEdgeCases:
    """Tests for UnifiClientBlockSwitch edge cases."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with client."""
        coordinator = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.clients = MagicMock()
        coordinator.network_client.clients.block = AsyncMock()
        coordinator.network_client.clients.unblock = AsyncMock()
        coordinator.async_block_client = AsyncMock()
        coordinator.async_unblock_client = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {"site1": {}},
            "stats": {},
            "clients": {
                "site1": {
                    "client1": {
                        "id": "client1",
                        "name": "Test Client",
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "blocked": False,
                    }
                }
            },
            "wifi": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    @pytest.mark.asyncio
    async def test_turn_on_handles_error(self, mock_coordinator) -> None:
        """Test async_turn_on handles errors gracefully (lines 863-864)."""
        mock_coordinator.async_unblock_client = AsyncMock(
            side_effect=Exception("API Error")
        )

        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        with pytest.raises(HomeAssistantError, match="Unable to allow client"):
            await switch.async_turn_on()

        mock_coordinator.async_unblock_client.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_off_handles_error(self, mock_coordinator) -> None:
        """Test async_turn_off handles errors gracefully (lines 884-885)."""
        mock_coordinator.async_block_client = AsyncMock(
            side_effect=Exception("API Error")
        )

        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        with pytest.raises(HomeAssistantError, match="Unable to block client"):
            await switch.async_turn_off()

        mock_coordinator.async_block_client.assert_called_once()


class TestUnifiWifiSwitchEdgeCases:
    """Tests for UnifiWifiSwitch edge cases."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with WiFi."""
        coordinator = MagicMock()
        coordinator.network_client = MagicMock()
        coordinator.network_client.wifi = MagicMock()
        coordinator.network_client.wifi.update = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {},
            "stats": {},
            "clients": {},
            "wifi": {
                "site1": {
                    "wifi1": {
                        "id": "wifi1",
                        "name": "Test WiFi",
                        "ssid": "TestSSID",
                        "enabled": True,
                        "security": "wpa2",
                        "hidden": False,
                        "isGuest": False,
                    }
                }
            },
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    @pytest.mark.asyncio
    async def test_turn_on_handles_error(self, mock_coordinator) -> None:
        """Test async_turn_on handles errors gracefully (lines 975-976)."""
        mock_coordinator.network_client.wifi.update = AsyncMock(
            side_effect=Exception("API Error")
        )

        switch = UnifiWifiSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
            wifi_data=mock_coordinator.data["wifi"]["site1"]["wifi1"],
        )

        with pytest.raises(HomeAssistantError, match="Unable to enable WiFi"):
            await switch.async_turn_on()

        mock_coordinator.network_client.wifi.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_off_handles_error(self, mock_coordinator) -> None:
        """Test async_turn_off handles errors gracefully (lines 1000-1001)."""
        mock_coordinator.network_client.wifi.update = AsyncMock(
            side_effect=Exception("API Error")
        )

        switch = UnifiWifiSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
            wifi_data=mock_coordinator.data["wifi"]["site1"]["wifi1"],
        )

        with pytest.raises(HomeAssistantError, match="Unable to disable WiFi"):
            await switch.async_turn_off()

        mock_coordinator.network_client.wifi.update.assert_called_once()

    def test_get_wifi_data_fallback_to_initial_data(self, mock_coordinator) -> None:
        """Test _get_wifi_data falls back to initial wifi_data."""
        initial_wifi_data = {
            "id": "wifi1",
            "name": "Initial WiFi",
            "ssid": "InitialSSID",
            "enabled": True,
        }

        switch = UnifiWifiSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
            wifi_data=initial_wifi_data,
        )

        # Remove wifi from coordinator data
        mock_coordinator.data["wifi"]["site1"] = {}

        wifi_data = switch._get_wifi_data()
        assert wifi_data == initial_wifi_data


class TestUnifiOutletSwitch:
    """Tests for UnifiOutletSwitch platform entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with PDU device data."""
        coordinator = MagicMock()
        coordinator.available = True
        coordinator.device_available = True
        coordinator.data = {
            "devices": {
                "site1": {
                    "pdu1": {
                        "id": "pdu1",
                        "_id": "60a1b2c3d4e5f67890123456",
                        "name": "Smart PDU",
                        "status": "online",
                        "state": "ONLINE",
                        "outlet_table": [
                            {
                                "index": 1,
                                "name": "Main Server",
                                "relay_state": True,
                                "cycle_enabled": True,
                                "outlet_caps": 3,
                                "outlet_voltage": 120.2,
                                "outlet_current": 1.25,
                                "outlet_power": 150.0,
                                "outlet_power_factor": 0.98,
                            },
                            {
                                "index": 2,
                                "name": "Backup Server",
                                "relay_state": False,
                                "cycle_enabled": False,
                                "outlet_caps": 3,
                                "outlet_voltage": 120.1,
                                "outlet_current": 0.0,
                                "outlet_power": 0.0,
                                "outlet_power_factor": 0.0,
                            },
                            {
                                "index": 3,
                                "name": "Modem",
                                "relay_state": True,
                                "cycle_enabled": None,
                                "outlet_caps": 1,
                            },
                        ],
                    }
                }
            },
            "protect": {"cameras": {}, "lights": {}},
            "wifi": {},
            "firewall_rules": {},
            "clients": {},
        }
        coordinator.network_client = MagicMock()
        coordinator.network_client.devices = MagicMock()
        coordinator.network_client.devices.set_outlet_state = AsyncMock(
            return_value=True
        )
        coordinator.async_set_outlet_state = AsyncMock(return_value=True)
        coordinator.async_request_refresh = AsyncMock()
        coordinator.resolve_legacy_site_name = MagicMock(return_value="default")
        return coordinator

    @pytest.mark.asyncio
    async def test_async_setup_entry_creates_outlet_switches(
        self, mock_coordinator
    ) -> None:
        mock_entry = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        entities = []

        def async_add_entities(new_entities):
            entities.extend(new_entities)

        await async_setup_entry(
            MagicMock(),
            mock_entry,
            async_add_entities,
        )

        outlet_switches = [e for e in entities if isinstance(e, UnifiOutletSwitch)]
        cycle_switches = [e for e in entities if isinstance(e, UnifiOutletCycleSwitch)]

        assert len(outlet_switches) == 3
        # Outlets 1 and 2 have cycle_enabled not None; outlet 3 has cycle_enabled None
        assert len(cycle_switches) == 2

    @pytest.mark.asyncio
    async def test_async_setup_entry_dedupes_outlet_and_cycle_switches(
        self, mock_coordinator
    ) -> None:
        """Re-running discovery on unchanged PDU data does not duplicate
        outlet or outlet-cycle switches."""
        mock_entry = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        entities = []

        def async_add_entities(new_entities):
            entities.extend(new_entities)

        await async_setup_entry(
            MagicMock(),
            mock_entry,
            async_add_entities,
        )
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        outlet_before = len([e for e in entities if isinstance(e, UnifiOutletSwitch)])
        cycle_before = len(
            [e for e in entities if isinstance(e, UnifiOutletCycleSwitch)]
        )
        assert outlet_before == 3
        assert cycle_before == 2

        listener()

        outlet_after = len([e for e in entities if isinstance(e, UnifiOutletSwitch)])
        cycle_after = len(
            [e for e in entities if isinstance(e, UnifiOutletCycleSwitch)]
        )
        assert outlet_after == outlet_before
        assert cycle_after == cycle_before

    def test_outlet_switch_properties(self, mock_coordinator) -> None:
        """Test UnifiOutletSwitch properties and unique_id literal."""
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            0
        ]
        switch = UnifiOutletSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_data=outlet_data,
        )

        assert switch.unique_id == "site1_pdu1_outlet_1"
        assert switch.translation_key == "outlet"
        assert switch.translation_placeholders == {"outlet_name": "Main Server"}
        assert switch.device_class == SwitchDeviceClass.OUTLET
        assert switch.is_on is True
        assert switch.available is True
        assert switch.icon is None

        attrs = switch.extra_state_attributes
        assert attrs["index"] == 1
        assert attrs["name"] == "Main Server"
        assert attrs["relay_state"] is True
        assert attrs["cycle_enabled"] is True
        assert attrs["outlet_power"] == 150.0

    def test_outlet_switch_unavailable_when_device_coordinator_unavailable(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test outlet switch unavailable when device coordinator is unavailable."""
        mock_coordinator.device_available = False
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            0
        ]
        switch = UnifiOutletSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_data=outlet_data,
        )
        assert switch.available is False

    @pytest.mark.asyncio
    async def test_outlet_switch_turn_on(self, mock_coordinator) -> None:
        """Test turning on the outlet switch."""
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            1
        ]
        switch = UnifiOutletSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=2,
            outlet_data=outlet_data,
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()

        mock_coordinator.async_set_outlet_state.assert_awaited_once_with(
            "site1", "pdu1", 2, state=True
        )
        mock_coordinator.async_request_refresh.assert_awaited_once()
        assert switch.is_on is True

    @pytest.mark.asyncio
    async def test_outlet_switch_turn_off(self, mock_coordinator) -> None:
        """Test turning off the outlet switch."""
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            0
        ]
        switch = UnifiOutletSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_data=outlet_data,
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_off()

        mock_coordinator.async_set_outlet_state.assert_awaited_once_with(
            "site1", "pdu1", 1, state=False
        )
        mock_coordinator.async_request_refresh.assert_awaited_once()
        assert switch.is_on is False

    @pytest.mark.asyncio
    async def test_outlet_switch_turn_on_fallback(self, mock_coordinator) -> None:
        """Test turning on uses fallback when coordinator method is not present."""
        del mock_coordinator.async_set_outlet_state
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            1
        ]
        switch = UnifiOutletSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=2,
            outlet_data=outlet_data,
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()

        set_outlet = mock_coordinator.network_client.devices.set_outlet_state
        set_outlet.assert_awaited_once_with(
            "default",
            "pdu1",
            2,
            state=True,
            current_device=mock_coordinator.data["devices"]["site1"]["pdu1"],
        )

    def test_outlet_switch_unavailable_when_device_offline(
        self, mock_coordinator
    ) -> None:
        """Test switch availability when device is offline."""
        mock_coordinator.data["devices"]["site1"]["pdu1"]["state"] = "OFFLINE"
        mock_coordinator.data["devices"]["site1"]["pdu1"]["status"] = "offline"

        switch = UnifiOutletSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
        )

        assert switch.available is False

    def test_outlet_switch_more_edge_cases(self, mock_coordinator: MagicMock) -> None:
        """Test outlet switch edge cases for non-list table and alternative keys."""
        mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"] = "not_a_list"
        switch = UnifiOutletSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_data={"relay_state": True},
        )
        assert switch._get_outlet_data() is None

        mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"] = [
            {"outlet_idx": 1, "relay_state": True, "name": "Outlet 1"}
        ]
        assert switch._get_outlet_data() == {
            "outlet_idx": 1,
            "relay_state": True,
            "name": "Outlet 1",
        }

        switch._update_local_state(relay_state=True, cycle_enabled=False)
        assert (
            mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][0][
                "cycle_enabled"
            ]
            is False
        )

        mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"] = [
            {
                "index": 1,
                "outlet_caps": 3,
                "outlet_voltage": 120.0,
                "outlet_current": 1.0,
                "outlet_power": 120.0,
                "outlet_power_factor": 0.99,
            }
        ]
        attrs = switch.extra_state_attributes
        assert attrs["outlet_voltage"] == 120.0
        assert attrs["outlet_power_factor"] == 0.99

    @pytest.mark.asyncio
    async def test_outlet_switch_turn_on_failure_no_optimistic_update(
        self, mock_coordinator
    ) -> None:
        """Test outlet switch turn on failure asserts no optimistic state write."""

        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            1
        ]
        switch = UnifiOutletSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=2,
            outlet_data=outlet_data,
        )
        mock_coordinator.async_set_outlet_state.side_effect = HomeAssistantError(
            "API Error"
        )

        with (
            patch.object(switch, "async_write_ha_state") as write_state,
            pytest.raises(HomeAssistantError),
        ):
            await switch.async_turn_on()

        mock_coordinator.async_set_outlet_state.assert_awaited_once_with(
            "site1", "pdu1", 2, state=True
        )
        write_state.assert_not_called()
        mock_coordinator.async_request_refresh.assert_not_awaited()
        assert switch.is_on is False

    @pytest.mark.asyncio
    async def test_outlet_switch_turn_off_failure_no_optimistic_update(
        self, mock_coordinator
    ) -> None:
        """Test outlet switch turn off failure asserts no optimistic state write."""

        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            0
        ]
        switch = UnifiOutletSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_data=outlet_data,
        )
        mock_coordinator.async_set_outlet_state.side_effect = HomeAssistantError(
            "API Error"
        )

        with (
            patch.object(switch, "async_write_ha_state") as write_state,
            pytest.raises(HomeAssistantError),
        ):
            await switch.async_turn_off()

        mock_coordinator.async_set_outlet_state.assert_awaited_once_with(
            "site1", "pdu1", 1, state=False
        )
        write_state.assert_not_called()
        mock_coordinator.async_request_refresh.assert_not_awaited()
        assert switch.is_on is True

    def test_outlet_switch_find_outlet_and_update_local_state_edges(
        self, mock_coordinator
    ) -> None:
        """Test outlet switch branches for missing index, device, or table."""
        switch = UnifiOutletSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_data={"relay_state": True},
        )

        # 1) outlet with no index key in table -> line 1573->1567
        mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"] = [
            {"name": "No Index Outlet"}
        ]
        assert switch._get_outlet_data() is None

        # 2) device not in site_devices -> line 1591->exit
        mock_coordinator.data["devices"]["site1"].pop("pdu1", None)
        switch._update_local_state(relay_state=True)

        # 3) outlet_table not a list -> line 1593->exit
        mock_coordinator.data["devices"]["site1"]["pdu1"] = {"outlet_table": None}
        switch._update_local_state(relay_state=True)

        # 4) outlet in table has no index key (1600->1594)
        # and cycle_enabled updated without relay_state (1603->1605)
        mock_coordinator.data["devices"]["site1"]["pdu1"] = {
            "outlet_table": [
                {"name": "No Index"},
                {"index": 1, "relay_state": True, "cycle_enabled": False},
            ]
        }
        switch._update_local_state(cycle_enabled=True)
        assert (
            mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][1][
                "cycle_enabled"
            ]
            is True
        )


class TestUnifiOutletCycleSwitch:
    """Tests for UnifiOutletCycleSwitch entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with PDU device data."""
        coordinator = MagicMock()
        coordinator.available = True
        coordinator.device_available = True
        coordinator.data = {
            "devices": {
                "site1": {
                    "pdu1": {
                        "id": "pdu1",
                        "_id": "60a1b2c3d4e5f67890123456",
                        "name": "Smart PDU",
                        "status": "online",
                        "state": "ONLINE",
                        "outlet_table": [
                            {
                                "index": 1,
                                "name": "Main Server",
                                "relay_state": True,
                                "cycle_enabled": True,
                                "outlet_caps": 3,
                            },
                            {
                                "index": 2,
                                "name": "Backup Server",
                                "relay_state": False,
                                "cycle_enabled": False,
                                "outlet_caps": 3,
                            },
                        ],
                    }
                }
            }
        }
        coordinator.network_client = MagicMock()
        coordinator.network_client.devices = MagicMock()
        coordinator.network_client.devices.set_outlet_state = AsyncMock(
            return_value=True
        )
        coordinator.async_set_outlet_state = AsyncMock(return_value=True)
        coordinator.async_request_refresh = AsyncMock()
        coordinator.resolve_legacy_site_name = MagicMock(return_value="default")
        return coordinator

    def test_cycle_switch_properties(self, mock_coordinator) -> None:
        """Test UnifiOutletCycleSwitch properties and unique_id literal."""
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            0
        ]
        switch = UnifiOutletCycleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_data=outlet_data,
        )

        assert switch.unique_id == "site1_pdu1_outlet_1_cycle_enabled"
        assert switch.translation_key == "outlet_cycle_enabled"
        assert switch.translation_placeholders == {"outlet_name": "Main Server"}
        assert switch.entity_category == EntityCategory.CONFIG
        assert switch.entity_registry_enabled_default is False
        assert switch.is_on is True
        assert switch.available is True

    def test_cycle_switch_unavailable_when_device_coordinator_unavailable(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test cycle switch unavailable when device coordinator is unavailable."""
        mock_coordinator.device_available = False
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            0
        ]
        switch = UnifiOutletCycleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_data=outlet_data,
        )
        assert switch.available is False

    @pytest.mark.asyncio
    async def test_cycle_switch_turn_on(self, mock_coordinator) -> None:
        """Test turning on the power cycle switch."""
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            1
        ]
        switch = UnifiOutletCycleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=2,
            outlet_data=outlet_data,
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()

        mock_coordinator.async_set_outlet_state.assert_awaited_once_with(
            "site1", "pdu1", 2, state=False, cycle_enabled=True
        )
        mock_coordinator.async_request_refresh.assert_awaited_once()
        assert switch.is_on is True

    @pytest.mark.asyncio
    async def test_cycle_switch_turn_off(self, mock_coordinator) -> None:
        """Test turning off the power cycle switch."""
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            0
        ]
        switch = UnifiOutletCycleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_data=outlet_data,
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_off()

        mock_coordinator.async_set_outlet_state.assert_awaited_once_with(
            "site1", "pdu1", 1, state=True, cycle_enabled=False
        )
        mock_coordinator.async_request_refresh.assert_awaited_once()
        assert switch.is_on is False

    @pytest.mark.asyncio
    async def test_cycle_switch_fallback(self, mock_coordinator) -> None:
        """Test fallback when coordinator action is absent."""
        del mock_coordinator.async_set_outlet_state
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            0
        ]
        switch = UnifiOutletCycleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_data=outlet_data,
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_off()

        set_outlet = mock_coordinator.network_client.devices.set_outlet_state
        set_outlet.assert_awaited_once_with(
            "default",
            "pdu1",
            1,
            state=True,
            cycle_enabled=False,
            current_device=mock_coordinator.data["devices"]["site1"]["pdu1"],
        )

    @pytest.mark.asyncio
    async def test_cycle_switch_does_not_default_relay_on(
        self, mock_coordinator
    ) -> None:
        """Test toggling power cycling never energizes an unknown relay.

        set_outlet_state always writes relay_state into the override, so an
        outlet whose reported data carries no relay_state must be treated as
        OFF. Defaulting to ON would switch on a load the user did not ask for.
        """
        mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"] = [
            {"index": 1, "name": "Main Server", "cycle_enabled": False}
        ]
        switch = UnifiOutletCycleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_data={"index": 1, "cycle_enabled": False},
        )
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()

        mock_coordinator.async_set_outlet_state.assert_awaited_once_with(
            "site1", "pdu1", 1, state=False, cycle_enabled=True
        )

    def test_outlet_switch_edge_cases(self, mock_coordinator) -> None:
        """Test outlet switch edge cases for complete branch coverage."""
        # 1. Device data missing or not dict
        mock_coordinator.data["devices"] = {}
        switch = UnifiOutletSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_data={"relay_state": True},
        )
        assert switch._get_outlet_data() is None
        assert switch.is_on is True
        assert switch.available is False

        # 2. Outlet table with invalid entries
        mock_coordinator.data["devices"] = {
            "site1": {
                "pdu1": {
                    "outlet_table": [
                        "not_a_dict",
                        {"index": "invalid"},
                        {"outlet_idx": 2},
                    ],
                    "state": "ONLINE",
                    "status": "online",
                }
            }
        }
        assert switch._get_outlet_data() is None
        switch._update_local_state(relay_state=False)

        # 3. Cycle switch with missing/offline data
        cycle_switch = UnifiOutletCycleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_data={"cycle_enabled": True},
        )
        assert cycle_switch._get_outlet_data() is None
        assert cycle_switch.is_on is True
        assert cycle_switch.available is False

        # 4. Cycle switch update local state
        mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"] = [
            {"index": 1, "cycle_enabled": False}
        ]
        cycle_switch._update_local_state(cycle_enabled=True)
        assert (
            mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][0][
                "cycle_enabled"
            ]
            is True
        )

    def test_cycle_switch_more_edge_cases(self, mock_coordinator: MagicMock) -> None:
        """Test cycle switch edge cases for non-list table and offline state."""
        switch = UnifiOutletCycleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_data={"cycle_enabled": True},
        )

        mock_coordinator.data["devices"]["site1"].pop("pdu1", None)
        assert switch._get_outlet_data() is None

        mock_coordinator.data["devices"]["site1"]["pdu1"] = {"outlet_table": None}
        assert switch._get_outlet_data() is None

        mock_coordinator.data["devices"]["site1"]["pdu1"] = {
            "state": "ONLINE",
            "outlet_table": [
                "not_a_dict",
                {"index": "invalid_int", "cycle_enabled": False},
                {"outlet_idx": 1, "cycle_enabled": True},
            ],
        }
        assert switch._get_outlet_data()["outlet_idx"] == 1

        switch._update_local_state(cycle_enabled=False)
        assert (
            mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][2][
                "cycle_enabled"
            ]
            is False
        )

        mock_coordinator.data["devices"]["site1"]["pdu1"]["state"] = "OFFLINE"
        assert switch.available is False

    @pytest.mark.asyncio
    async def test_cycle_switch_turn_on_failure_no_optimistic_update(
        self, mock_coordinator
    ) -> None:
        """Test power cycle turn on failure asserts no optimistic state write."""

        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            1
        ]
        switch = UnifiOutletCycleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=2,
            outlet_data=outlet_data,
        )
        mock_coordinator.async_set_outlet_state.side_effect = HomeAssistantError(
            "API Error"
        )

        with (
            patch.object(switch, "async_write_ha_state") as write_state,
            pytest.raises(HomeAssistantError),
        ):
            await switch.async_turn_on()

        mock_coordinator.async_set_outlet_state.assert_awaited_once_with(
            "site1", "pdu1", 2, state=False, cycle_enabled=True
        )
        write_state.assert_not_called()
        mock_coordinator.async_request_refresh.assert_not_awaited()
        assert switch.is_on is False

    @pytest.mark.asyncio
    async def test_cycle_switch_turn_off_failure_no_optimistic_update(
        self, mock_coordinator
    ) -> None:
        """Test power cycle turn off failure asserts no optimistic state write."""

        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            0
        ]
        switch = UnifiOutletCycleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_data=outlet_data,
        )
        mock_coordinator.async_set_outlet_state.side_effect = HomeAssistantError(
            "API Error"
        )

        with (
            patch.object(switch, "async_write_ha_state") as write_state,
            pytest.raises(HomeAssistantError),
        ):
            await switch.async_turn_off()

        mock_coordinator.async_set_outlet_state.assert_awaited_once_with(
            "site1", "pdu1", 1, state=True, cycle_enabled=False
        )
        write_state.assert_not_called()
        mock_coordinator.async_request_refresh.assert_not_awaited()
        assert switch.is_on is True

    def test_cycle_switch_find_outlet_and_update_local_state_edges(
        self, mock_coordinator
    ) -> None:
        """Test cycle switch branches for missing index, device, or table."""
        switch = UnifiOutletCycleSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="pdu1",
            outlet_index=1,
            outlet_data={"cycle_enabled": True},
        )

        # 1) outlet with no index key in table -> line 1783->1777
        mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"] = [
            {"name": "No Index Outlet"}
        ]
        assert switch._get_outlet_data() is None

        # 2) device not in site_devices -> line 1800->exit
        mock_coordinator.data["devices"]["site1"].pop("pdu1", None)
        switch._update_local_state(cycle_enabled=True)

        # 3) outlet_table not a list -> line 1802->exit
        mock_coordinator.data["devices"]["site1"]["pdu1"] = {"outlet_table": "invalid"}
        switch._update_local_state(cycle_enabled=True)

        # 4) outlet in table has no index key (1809->1803)
        # and no outlet matches index 1 (1803->exit)
        mock_coordinator.data["devices"]["site1"]["pdu1"] = {
            "outlet_table": [
                {"name": "No Index"},
                {"index": 99, "cycle_enabled": False},
            ]
        }
        switch._update_local_state(cycle_enabled=True)
        assert (
            mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][1][
                "cycle_enabled"
            ]
            is False
        )


class TestFindGatewayDeviceId:
    """Tests for grouping site-level switches under the site's gateway."""

    @staticmethod
    def _coordinator(devices: dict) -> MagicMock:
        coordinator = MagicMock()
        coordinator.data = {"devices": {"site1": devices}}
        return coordinator

    def test_recognised_gateway(self):
        """A gateway model is found."""
        coordinator = self._coordinator(
            {"sw": {"model": "USW-24"}, "gw": {"model": "UCG-Ultra"}}
        )
        assert _find_gateway_device_id(coordinator, "site1") == "gw"

    def test_wan_data_alone_does_not_regroup_entities(self):
        """Merged WAN data must not move entities onto an unrecognised device.

        It is only present when that poll's legacy fetch worked, so grouping
        on it would move entities between devices across restarts.
        """
        coordinator = self._coordinator(
            {"gw": {"model": "Unknown", "wans": [{"key": "wan1"}]}}
        )
        assert _find_gateway_device_id(coordinator, "site1") is None

    def test_non_dict_site_devices(self) -> None:
        """Test when site devices is not a dict."""
        coordinator = MagicMock()
        coordinator.data = {"devices": {"site1": None}}
        assert _find_gateway_device_id(coordinator, "site1") is None

    def test_non_dict_device_entry(self) -> None:
        """Test when site devices has non-dict entry."""
        coordinator = MagicMock()
        coordinator.data = {
            "devices": {
                "site1": {
                    "invalid_device": "not_a_dict",
                    "gw": {"model": "UCG-Ultra"},
                }
            }
        }
        assert _find_gateway_device_id(coordinator, "site1") == "gw"


class TestProtectSwitchPatchBodies:
    """Test camera switches call through facade down to protect_client._patch."""

    @pytest.mark.asyncio
    async def test_status_light_switch_sends_spec_patch_body(self) -> None:
        """Test status light switch turn on/off sends ledSettings via facade."""
        coordinator = MagicMock()
        client = UniFiProtectClient(
            auth=ApiKeyAuth(api_key="test-key"),
            base_url="https://192.168.1.1",
            connection_type=ConnectionType.LOCAL,
        )
        client._patch = AsyncMock(
            return_value={"id": "camera1", "mac": "00:11:22:33:44:55"}
        )
        coordinator.protect_client = client
        coordinator.async_update_camera_settings = AsyncMock(
            side_effect=client.cameras.update
        )
        coordinator.data = {
            "protect": {
                "cameras": {
                    "camera1": {
                        "name": "Test Camera",
                        "ledSettings": {"isEnabled": False},
                    }
                }
            }
        }
        switch = UnifiProtectStatusLightSwitch(coordinator, "camera1")
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()
        client._patch.assert_awaited_once_with(
            client.build_api_path("/cameras/camera1"),
            json_data={"ledSettings": {"isEnabled": True}},
        )

        client._patch.reset_mock()
        await switch.async_turn_off()
        client._patch.assert_awaited_once_with(
            client.build_api_path("/cameras/camera1"),
            json_data={"ledSettings": {"isEnabled": False}},
        )

    @pytest.mark.asyncio
    async def test_high_fps_switch_sends_spec_patch_body(self) -> None:
        """Test high FPS switch turn on/off sends videoMode via facade."""
        coordinator = MagicMock()
        client = UniFiProtectClient(
            auth=ApiKeyAuth(api_key="test-key"),
            base_url="https://192.168.1.1",
            connection_type=ConnectionType.LOCAL,
        )
        client._patch = AsyncMock(
            return_value={"id": "camera1", "mac": "00:11:22:33:44:55"}
        )
        coordinator.protect_client = client
        coordinator.async_update_camera_settings = AsyncMock(
            side_effect=client.cameras.update
        )
        coordinator.data = {
            "protect": {
                "cameras": {
                    "camera1": {
                        "name": "Test Camera",
                        "videoMode": "default",
                    }
                }
            }
        }
        switch = UnifiProtectHighFPSSwitch(coordinator, "camera1")
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_on()
        client._patch.assert_awaited_once_with(
            client.build_api_path("/cameras/camera1"),
            json_data={"videoMode": "highFps"},
        )

        client._patch.reset_mock()
        await switch.async_turn_off()
        client._patch.assert_awaited_once_with(
            client.build_api_path("/cameras/camera1"),
            json_data={"videoMode": "default"},
        )


class TestGetFirewallRuleAction:
    """Tests for _get_firewall_rule_action helper."""

    def test_dict_action_with_type(self) -> None:
        """Test action as dict with type returns type as string."""
        assert _get_firewall_rule_action({"action": {"type": "DROP"}}) == "DROP"
        assert _get_firewall_rule_action({"action": {"type": None}}) is None

    def test_non_dict_action(self) -> None:
        """Test action as string or None."""
        assert _get_firewall_rule_action({"action": "ACCEPT"}) == "ACCEPT"
        assert _get_firewall_rule_action({"action": None}) is None
        assert _get_firewall_rule_action({}) is None
