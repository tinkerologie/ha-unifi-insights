# Copyright (c) 2026 Ruaan Deysel
"""Tests for UniFi Protect switch platform."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
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
from unifi_official_api.network.models.routes import (
    PolicyBasedRoute,
)

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
from custom_components.unifi_insights.coordinators.base import (
    UnifiInsightsBaseCoordinator,
)
from custom_components.unifi_insights.data_transforms import get_site_gateway_id
from custom_components.unifi_insights.switch import (
    FIREWALL_RULE_SWITCH,
    OUTLET_CYCLE_SWITCH,
    OUTLET_RELAY_SWITCH,
    PARALLEL_UPDATES,
    POLICY_BASED_ROUTE_SWITCH,
    VPN_CLIENT_SWITCH,
    UnifiClientBlockSwitch,
    UnifiInsightsNetworkRuleSwitch,
    UnifiOutletSwitch,
    UnifiOutletSwitchEntityDescription,
    UnifiProtectHighFPSSwitch,
    UnifiProtectStatusLightSwitch,
    UnifiWifiSwitch,
    _get_firewall_rule_action,
    _prune_orphaned_switch_entities,
    _route_kill_switch,
    async_setup_entry,
)
from tests.fixtures.assertions import patch_attribute, require_mock, require_not_none
from tests.fixtures.command_owners import autospec_command_owners, protect_owner
from tests.fixtures.entities import EntityCollector
from tests.fixtures.facade_data import (
    set_availability,
    with_camera,
    with_client,
    with_device_fields,
    with_network_device,
    with_rule,
    with_sections,
    without_sections,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

    from custom_components.unifi_insights.coordinators.facade import FacadeData


def _with_cameras(data: Mapping[str, Any], cameras: Any) -> FacadeData:
    """Return ``data`` with its Protect cameras collection replaced."""
    return with_sections(data, protect={**data["protect"], "cameras": cameras})


def _with_camera_fields(
    data: Mapping[str, Any], camera_id: str, **fields: Any
) -> FacadeData:
    """Return ``data`` with fields of a Protect camera's record replaced."""
    record = data["protect"]["cameras"][camera_id]
    return with_camera(data, camera_id, {**record, **fields})


def _with_client_fields(
    data: Mapping[str, Any], site_id: str, client_id: str, **fields: Any
) -> FacadeData:
    """Return ``data`` with fields of a Network client's record replaced."""
    record = data["clients"][site_id][client_id]
    return with_client(data, site_id, client_id, {**record, **fields})


def _with_record_fields(
    data: Mapping[str, Any], section: str, site_id: str, record_id: str, **fields: Any
) -> FacadeData:
    """Return ``data`` with fields of a per-site record of ``section`` replaced."""
    record = data[section][site_id][record_id]
    return with_rule(data, section, site_id, record_id, {**record, **fields})


def _with_site_records(
    data: Mapping[str, Any], section: str, site_id: str, records: Any
) -> FacadeData:
    """Return ``data`` with one site's records of ``section`` replaced."""
    return with_sections(data, **{section: {**data[section], site_id: records}})


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
        coordinator = autospec_command_owners(MagicMock())
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
    async def test_setup_entry_with_cameras(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup with cameras present."""
        mock_coordinator.data = with_camera(
            mock_coordinator.data,
            "camera1",
            {
                "id": "camera1",
                "name": "Test Camera",
                "state": "CONNECTED",
                "isMicEnabled": True,
            },
        )

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
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test setup with multiple cameras."""
        mock_coordinator.data = _with_cameras(
            mock_coordinator.data,
            {
                "camera1": {
                    "id": "camera1",
                    "name": "Front Camera",
                    "state": "CONNECTED",
                },
                "camera2": {
                    "id": "camera2",
                    "name": "Back Camera",
                    "state": "CONNECTED",
                },
                "camera3": {
                    "id": "camera3",
                    "name": "Side Camera",
                    "state": "DISCONNECTED",
                },
            },
        )

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
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Malformed (non-dict) top-level collections are skipped without
        raising, producing no switches for any of them."""
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            clients="not-a-dict",
            wifi="not-a-dict",
            firewall_rules="not-a-dict",
            policy_based_routes="not-a-dict",
            vpn_clients="not-a-dict",
        )

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
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Re-running discovery for a capable camera does not duplicate the
        high FPS switch."""
        mock_coordinator.data = with_camera(
            mock_coordinator.data,
            "camera1",
            {
                "id": "camera1",
                "name": "Test Camera",
                "state": "CONNECTED",
                "featureFlags": {"hasHighFpsCapability": True},
            },
        )

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
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Re-running discovery for an unchanged client does not duplicate
        the block switch."""
        mock_coordinator.data = with_client(
            mock_coordinator.data,
            "site1",
            "client1",
            {"id": "client1", "mac": "aa:bb:cc:dd:ee:ff", "name": "PC"},
        )

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
# either the config owner ("config") or the per-site config section the
# snapshot captures ("firewall_rules"). Table-driven to match the production
# helper's own (unique_id_suffix, data_key, availability_fn) spec.
_ORPHAN_TYPE_PARAMS = [
    pytest.param("policy_based_routes", "_policy_based_route", "config", id="pbr"),
    pytest.param(
        "firewall_rules", "_firewall_rule", "firewall_rules", id="firewall_rule"
    ),
    pytest.param("vpn_clients", "_vpn_client", "config", id="vpn_client"),
    pytest.param("port_forwards", "_port_forward", "port_forwards", id="port_forward"),
    pytest.param("traffic_rules", "_traffic_rule", "traffic_rules", id="traffic_rule"),
    pytest.param("nat_rules", "_nat_rule", "nat_rules", id="nat_rule"),
    pytest.param("static_routes", "_static_route", "static_routes", id="static_route"),
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
        coordinator = autospec_command_owners(MagicMock())
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
        if availability_attr == "config":
            set_availability(mock_coordinator, config=False)
        else:
            set_availability(
                mock_coordinator, failed_sections={(availability_attr, "site1")}
            )

        stale_entry = self._reg_entry(f"site1_stale{suffix}")
        registry = self._prune(hass, mock_coordinator, [stale_entry])

        registry.async_remove.assert_not_called()

    @pytest.mark.parametrize(
        ("data_key", "suffix", "availability_attr"), _ORPHAN_TYPE_PARAMS
    )
    def test_missing_data_key_prunes_nothing(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        data_key: str,
        suffix: str,
        availability_attr: str,
    ) -> None:
        """Coordinator data missing the collection entirely -> zero pruned."""
        mock_coordinator.data = without_sections(mock_coordinator.data, data_key)

        stale_entry = self._reg_entry(f"site1_stale{suffix}")
        registry = self._prune(hass, mock_coordinator, [stale_entry])

        registry.async_remove.assert_not_called()

    @pytest.mark.parametrize(
        ("data_key", "suffix", "availability_attr"), _ORPHAN_TYPE_PARAMS
    )
    def test_missing_site_key_prunes_nothing(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        data_key: str,
        suffix: str,
        availability_attr: str,
    ) -> None:
        """Collection present but the entry's site_id key is absent from it
        -> zero pruned (looks identical to a transient fetch failure)."""
        mock_coordinator.data = with_sections(mock_coordinator.data, **{data_key: {}})

        stale_entry = self._reg_entry(f"site1_stale{suffix}")
        registry = self._prune(hass, mock_coordinator, [stale_entry])

        registry.async_remove.assert_not_called()

    @pytest.mark.parametrize(
        ("data_key", "suffix", "availability_attr"), _ORPHAN_TYPE_PARAMS
    )
    def test_site_value_not_dict_prunes_nothing(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        data_key: str,
        suffix: str,
        availability_attr: str,
    ) -> None:
        """Site key present but its value is not a dict -> zero pruned."""
        mock_coordinator.data = with_sections(
            mock_coordinator.data, **{data_key: {"site1": "not-a-dict"}}
        )

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
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        data_key: str,
        suffix: str,
        availability_attr: str,
    ) -> None:
        """An entry whose site prefix does not match any qualified site is
        left alone, even though a *different* site in the same collection
        is qualified and has stale ids of its own pruned."""
        mock_coordinator.data = _with_site_records(
            mock_coordinator.data, data_key, "site2", "not-a-dict"
        )

        stale_site1 = self._reg_entry(f"site1_stale{suffix}")
        site2_entry = self._reg_entry(f"site2_stale{suffix}")

        registry = self._prune(hass, mock_coordinator, [stale_site1, site2_entry])

        registry.async_remove.assert_called_once_with(stale_site1.entity_id)

    @pytest.mark.parametrize(
        ("data_key", "suffix", "availability_attr"), _ORPHAN_TYPE_PARAMS
    )
    def test_empty_site_collection_prunes_nothing(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        data_key: str,
        suffix: str,
        availability_attr: str,
    ) -> None:
        """An EMPTY per-site collection must never trigger a prune.

        Regression test. Policy-based routes and VPN clients have no
        per-site "fetched successfully" signal: on a transient per-site
        fetch failure the config coordinator stores `{}` for that site
        while the overall refresh still succeeds, leaving the config
        owner available. Treating that `{}` as "every object was
        deleted" would irreversibly remove every row of this type for the
        site on a single startup timeout.
        """
        mock_coordinator.data = _with_site_records(
            mock_coordinator.data, data_key, "site1", {}
        )

        entry_a = self._reg_entry(f"site1_route_a{suffix}")
        entry_b = self._reg_entry(f"site1_route_b{suffix}")

        registry = self._prune(hass, mock_coordinator, [entry_a, entry_b])

        registry.async_remove.assert_not_called()

    @pytest.mark.parametrize(
        ("data_key", "suffix", "availability_attr"), _ORPHAN_TYPE_PARAMS
    )
    def test_overlapping_site_ids_are_not_pruned_by_prefix_collision(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        data_key: str,
        suffix: str,
        availability_attr: str,
    ) -> None:
        """A site id that is a prefix of another must not claim its rows.

        Regression test. With qualified site `site1` and an unfetched
        `site1_backup`, a plain `startswith("site1_")` also matches
        `site1_backup_<id>`, so the second site's entities would be
        deleted on evidence that says nothing about them. Ownership must
        be unambiguous before anything is removed.
        """
        mock_coordinator.data = _with_site_records(
            mock_coordinator.data, data_key, "site1_backup", {}
        )
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            sites={**mock_coordinator.data.get("sites", {}), "site1_backup": {}},
        )

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
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Registry row for NAT rule switch is pruned when rule turns predefined."""
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            nat_rules={"site1": {"nat1": {"is_predefined": True}}},
        )
        reg_entry = self._reg_entry("site1_nat1_nat_rule")
        registry = self._prune(hass, mock_coordinator, [reg_entry])

        registry.async_remove.assert_called_once_with(reg_entry.entity_id)

    def test_prune_keeps_nat_rule_switch_when_section_unavailable(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Predefined NAT rule row is kept when section availability is False."""
        set_availability(mock_coordinator, failed_sections={("nat_rules", "site1")})
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            nat_rules={"site1": {"nat1": {"is_predefined": True}}},
        )
        reg_entry = self._reg_entry("site1_nat1_nat_rule")
        registry = self._prune(hass, mock_coordinator, [reg_entry])

        registry.async_remove.assert_not_called()

    def test_prune_keeps_nat_rule_switch_when_collection_empty(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """NAT rule switch is kept when collection is empty."""
        mock_coordinator.data = with_sections(
            mock_coordinator.data, nat_rules={"site1": {}}
        )

        reg_entry = self._reg_entry("site1_nat1_nat_rule")
        registry = self._prune(hass, mock_coordinator, [reg_entry])

        registry.async_remove.assert_not_called()

    async def test_setup_entry_prunes_once_not_on_every_listener_tick(
        self, hass
    ) -> None:
        """The prune runs once during setup, after the first discovery pass
        -- not on every coordinator update (dynamic discovery fires on every
        tick; re-running the prune there would be wasted work at best)."""
        coordinator = autospec_command_owners(MagicMock())
        coordinator.protect_client = None
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
        mock_coordinator.data = _with_cameras(
            mock_coordinator.data,
            {
                "camera1": {
                    "id": "camera1",
                    "name": "Front Camera",
                    "state": "CONNECTED",
                    "isMicEnabled": True,
                }
            },
        )
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
        mock_coordinator.data = _with_cameras(mock_coordinator.data, {})
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
        coordinator = autospec_command_owners(MagicMock())
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
        assert require_not_none(switch._attr_device_info)["identifiers"] == {
            (DOMAIN, "site1_device1")
        }

    def test_switch_fallback_no_uplink(self, mock_coordinator: MagicMock) -> None:
        """Test switch creates own device when no uplink."""
        # Remove uplink device ID
        mock_coordinator.data = _with_client_fields(
            mock_coordinator.data, "site1", "client1", uplinkDeviceId=None
        )

        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        # Should create its own device
        assert require_not_none(switch._attr_device_info)["identifiers"] == {
            (DOMAIN, "client_client1")
        }
        assert require_not_none(switch._attr_device_info)["name"] == "Jukebox"

    def test_switch_available(self, mock_coordinator: MagicMock) -> None:
        """Test switch availability."""
        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        # Should be available when client exists
        assert switch.available is True

        # Should be unavailable when client doesn't exist
        mock_coordinator.data = with_client(
            mock_coordinator.data, "site1", "client1", {}
        )
        assert switch.available is False

    def test_switch_available_follows_captured_device_availability(
        self, mock_coordinator: MagicMock
    ) -> None:
        """A cached client is unavailable while the device owner is failing."""
        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator, site_id="site1", client_id="client1"
        )

        set_availability(mock_coordinator, device=False)
        assert switch.available is False

        set_availability(mock_coordinator, device=True)
        assert switch.available is True

    def test_switch_is_on_when_not_blocked(self, mock_coordinator) -> None:
        """Test switch is ON when client is not blocked (allowed)."""
        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        # Not blocked = ON (allowed)
        assert switch.is_on is True

    def test_switch_is_off_when_blocked(self, mock_coordinator: MagicMock) -> None:
        """Test switch is OFF when client is blocked."""
        mock_coordinator.data = _with_client_fields(
            mock_coordinator.data, "site1", "client1", blocked=True
        )

        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        # When client is blocked, switch should be OFF (OFF means blocked)
        assert switch.is_on is False

    @pytest.mark.asyncio
    async def test_turn_on_unblocks_client(self, mock_coordinator: MagicMock) -> None:
        """Test turning ON unblocks the client."""
        mock_coordinator.data = _with_client_fields(
            mock_coordinator.data, "site1", "client1", blocked=True
        )

        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        await switch.async_turn_on()

        device = mock_coordinator.device_coordinator
        device.async_unblock_client.assert_awaited_once_with("site1", "client1")
        mock_coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_turn_off_blocks_client(self, mock_coordinator) -> None:
        """Test turning OFF blocks the client."""
        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        await switch.async_turn_off()

        device = mock_coordinator.device_coordinator
        device.async_block_client.assert_awaited_once_with("site1", "client1")
        mock_coordinator.async_request_refresh.assert_not_awaited()


class TestUnifiWifiSwitch:
    """Tests for WiFi network enable/disable switch."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = autospec_command_owners(MagicMock())
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

        assert require_not_none(switch._attr_device_info)["identifiers"] == {
            (DOMAIN, "wifi_wifi1")
        }
        assert (
            require_not_none(switch._attr_device_info)["name"] == "WiFi: Home Network"
        )

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

    def test_switch_is_off_when_disabled(self, mock_coordinator: MagicMock) -> None:
        """Test switch is OFF when WiFi is disabled."""
        mock_coordinator.data = _with_record_fields(
            mock_coordinator.data, "wifi", "site1", "wifi1", enabled=False
        )
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
    async def test_turn_on_enables_wifi(self, mock_coordinator: MagicMock) -> None:
        """Test turning ON enables the WiFi network."""
        mock_coordinator.data = _with_record_fields(
            mock_coordinator.data, "wifi", "site1", "wifi1", enabled=False
        )
        wifi_data = mock_coordinator.data["wifi"]["site1"]["wifi1"]

        switch = UnifiWifiSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
            wifi_data=wifi_data,
        )

        await switch.async_turn_on()

        config = mock_coordinator.config_coordinator
        config.async_update_wifi_network.assert_awaited_once_with(
            "site1", "wifi1", enabled=True
        )
        mock_coordinator.network_client.wifi.update.assert_not_called()
        mock_coordinator.async_request_refresh.assert_not_awaited()

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

        config = mock_coordinator.config_coordinator
        config.async_update_wifi_network.assert_awaited_once_with(
            "site1", "wifi1", enabled=False
        )
        mock_coordinator.network_client.wifi.update.assert_not_called()
        mock_coordinator.async_request_refresh.assert_not_awaited()

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

    def test_available_follows_captured_site_wifi_fetch(
        self, mock_coordinator: MagicMock
    ) -> None:
        """The switch is unavailable while its site's WiFi fetch is failing."""
        wifi_data = mock_coordinator.data["wifi"]["site1"]["wifi1"]
        switch = UnifiWifiSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
            wifi_data=wifi_data,
        )

        set_availability(mock_coordinator, failed_sections={("wifi", "site1")})
        assert switch.available is False

        set_availability(mock_coordinator, failed_sections={("wifi", "other")})
        assert switch.available is True

    def test_available_falls_back_to_initial_data(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test switch uses initial data when coordinator data is empty."""
        wifi_data = mock_coordinator.data["wifi"]["site1"]["wifi1"].copy()
        mock_coordinator.data = with_rule(
            mock_coordinator.data, "wifi", "site1", "wifi1", {}
        )

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
        coordinator = autospec_command_owners(MagicMock())
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
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, FIREWALL_RULE_SWITCH, "site1", "rule1"
        )

        assert switch._attr_unique_id == "site1_rule1_firewall_rule"

    def test_available_follows_site_firewall_fetch(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Firewall switch is unavailable when its site's firewall fetch failed."""
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, FIREWALL_RULE_SWITCH, "site1", "rule1"
        )
        assert switch.available is True

        set_availability(
            mock_coordinator, failed_sections={("firewall_rules", "site1")}
        )
        assert switch.available is False
        assert switch._attr_translation_key == "firewall_rule"
        assert switch._attr_translation_placeholders == {"rule_name": "Block Instagram"}
        assert switch.entity_category == EntityCategory.CONFIG
        assert require_not_none(switch._attr_device_info)["identifiers"] == {
            (DOMAIN, "site1_gateway1")
        }

    def test_is_on(self, mock_coordinator: MagicMock) -> None:
        """Test firewall switch state mirrors rule enabled state."""
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, FIREWALL_RULE_SWITCH, "site1", "rule1"
        )

        assert switch.is_on is True
        mock_coordinator.data = _with_record_fields(
            mock_coordinator.data, "firewall_rules", "site1", "rule1", enabled=False
        )
        assert switch.is_on is False

    def test_extra_state_attributes(self, mock_coordinator) -> None:
        """Test firewall switch metadata attributes."""
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, FIREWALL_RULE_SWITCH, "site1", "rule1"
        )

        attrs = switch.extra_state_attributes
        assert attrs["rule_id"] == "rule1"
        assert attrs["action"] == "DENY"
        assert attrs["protocol"] == "all"
        assert attrs["source_zone_id"] == "internal"
        assert attrs["destination_zone_id"] == "external"
        assert attrs["logging"] is True

    def test_icon_changes_with_state(self, mock_coordinator: MagicMock) -> None:
        """Test firewall switch icon reflects enabled state."""
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, FIREWALL_RULE_SWITCH, "site1", "rule1"
        )

        # The state icons come from icons.json.
        assert switch.icon is None
        mock_coordinator.data = _with_record_fields(
            mock_coordinator.data, "firewall_rules", "site1", "rule1", enabled=False
        )
        assert switch.icon is None

    @pytest.mark.asyncio
    async def test_turn_on_updates_rule(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test enabling a firewall rule."""
        mock_coordinator.data = _with_record_fields(
            mock_coordinator.data, "firewall_rules", "site1", "rule1", enabled=False
        )
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, FIREWALL_RULE_SWITCH, "site1", "rule1"
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        await switch.async_turn_on()

        config = mock_coordinator.config_coordinator
        config.async_set_firewall_rule_enabled.assert_awaited_once_with(
            "site1", "rule1", enabled=True
        )
        mock_coordinator.network_client.firewall.update_rule.assert_not_called()
        require_mock(switch.async_write_ha_state).assert_not_called()
        mock_coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_turn_off_updates_rule(self, mock_coordinator, monkeypatch) -> None:
        """Test disabling a firewall rule."""
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, FIREWALL_RULE_SWITCH, "site1", "rule1"
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        await switch.async_turn_off()

        config = mock_coordinator.config_coordinator
        config.async_set_firewall_rule_enabled.assert_awaited_once_with(
            "site1", "rule1", enabled=False
        )
        mock_coordinator.network_client.firewall.update_rule.assert_not_called()
        require_mock(switch.async_write_ha_state).assert_not_called()
        mock_coordinator.async_request_refresh.assert_not_awaited()

    def test_fallback_device_info_without_gateway(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test fallback device registry entry when no gateway device is found."""
        mock_coordinator.data = _with_site_records(
            mock_coordinator.data, "devices", "site1", {}
        )

        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, FIREWALL_RULE_SWITCH, "site1", "rule1"
        )

        assert require_not_none(switch._attr_device_info)["identifiers"] == {
            (DOMAIN, "firewall_policies_site1")
        }
        assert (
            require_not_none(switch._attr_device_info)["name"]
            == "Firewall Policies (Default)"
        )

    @pytest.mark.asyncio
    async def test_turn_on_error_does_not_write_state(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test firewall update failures propagate without writing state."""
        config = mock_coordinator.config_coordinator
        config.async_set_firewall_rule_enabled.side_effect = HomeAssistantError(
            "Unable to update firewall rule rule1"
        )
        mock_coordinator.data = _with_record_fields(
            mock_coordinator.data, "firewall_rules", "site1", "rule1", enabled=False
        )

        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, FIREWALL_RULE_SWITCH, "site1", "rule1"
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        with pytest.raises(HomeAssistantError, match="Unable to update firewall rule"):
            await switch.async_turn_on()

        require_mock(switch.async_write_ha_state).assert_not_called()
        assert (
            mock_coordinator.data["firewall_rules"]["site1"]["rule1"]["enabled"]
            is False
        )


class TestAsyncSetupEntryFirewallRules:
    """Tests firewall rule discovery in switch platform setup."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with firewall rule data."""
        coordinator = autospec_command_owners(MagicMock())
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
            entity
            for entity in entities
            if (
                isinstance(entity, UnifiInsightsNetworkRuleSwitch)
                and entity.entity_description is FIREWALL_RULE_SWITCH
            )
        ]
        assert len(firewall_switches) == 2
        assert {entity._rule_id for entity in firewall_switches} == {"rule1", "rule2"}

    @pytest.mark.asyncio
    async def test_setup_entry_firewall_rules_malformed_record_and_dedupe(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """A malformed rule record is skipped, and rediscovery on unchanged
        data does not duplicate the remaining switches."""
        mock_coordinator.data = with_rule(
            mock_coordinator.data, "firewall_rules", "site1", "bad_rule", "not-a-dict"
        )

        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_entry, async_add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        entities = async_add_entities.call_args[0][0]
        firewall_switches = [
            entity
            for entity in entities
            if (
                isinstance(entity, UnifiInsightsNetworkRuleSwitch)
                and entity.entity_description is FIREWALL_RULE_SWITCH
            )
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
        coordinator = autospec_command_owners(MagicMock())
        coordinator.network_client = MagicMock()
        coordinator.network_client.routes = MagicMock()
        coordinator.network_client.routes.update_route = AsyncMock()
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
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route1"
        )

        assert switch._attr_unique_id == "site1_route1_policy_based_route"
        assert switch._attr_translation_key == "policy_based_route"
        assert switch._attr_translation_placeholders == {
            "route_name": "Route via Privado VPN"
        }
        assert switch.entity_category == EntityCategory.CONFIG
        assert require_not_none(switch._attr_device_info)["identifiers"] == {
            (DOMAIN, "site1_gateway1")
        }

    def test_available_follows_config_refresh(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Route switch is unavailable while the config refresh is failing."""
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route1"
        )
        set_availability(mock_coordinator, config=True)
        assert switch.available is True

        set_availability(mock_coordinator, config=False)
        assert switch.available is False

    def test_is_on(self, mock_coordinator: MagicMock) -> None:
        """Test switch state mirrors route enabled state."""
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route1"
        )

        assert switch.is_on is True
        mock_coordinator.data = _with_record_fields(
            mock_coordinator.data,
            "policy_based_routes",
            "site1",
            "route1",
            enabled=False,
        )
        assert switch.is_on is False

    def test_extra_state_attributes(self, mock_coordinator: MagicMock) -> None:
        """Test route metadata attributes."""
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route1"
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
        mock_coordinator.data = with_rule(
            mock_coordinator.data,
            "policy_based_routes",
            "site1",
            "route3",
            {
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
            },
        )
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route3"
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
        self, mock_coordinator: MagicMock, caplog: pytest.LogCaptureFixture, monkeypatch
    ) -> None:
        """Test disable warning fires for the live ``kill_switch_enabled`` field."""
        mock_coordinator.data = with_rule(
            mock_coordinator.data,
            "policy_based_routes",
            "site1",
            "route3",
            {
                "id": "route3",
                "description": "Nord Route",
                "enabled": True,
                "kill_switch_enabled": True,
            },
        )
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route3"
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

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
        route_data = UnifiInsightsBaseCoordinator._model_to_dict(
            mock_coordinator, route
        )

        # Guard the premise: both alias keys really are present in the dump.
        assert "killSwitchEnabled" in route_data
        assert "killSwitch" in route_data

        mock_coordinator.data = with_rule(
            mock_coordinator.data, "policy_based_routes", "site1", "route4", route_data
        )
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route4"
        )

        assert _route_kill_switch(switch._get_rule_data()) is expected
        assert switch.extra_state_attributes["kill_switch_enabled"] is expected

    @pytest.mark.asyncio
    async def test_turn_off_warns_for_legacy_shape_through_serialization(
        self, mock_coordinator: MagicMock, caplog: pytest.LogCaptureFixture, monkeypatch
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
        mock_coordinator.data = with_rule(
            mock_coordinator.data,
            "policy_based_routes",
            "site1",
            "route4",
            UnifiInsightsBaseCoordinator._model_to_dict(mock_coordinator, route),
        )
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route4"
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        with caplog.at_level(logging.WARNING):
            await switch.async_turn_off()

        assert any("kill switch" in record.message.lower() for record in caplog.records)

    def test_icon_changes_with_state(self, mock_coordinator: MagicMock) -> None:
        """Test route switch icon reflects VPN and enabled state."""
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route1"
        )
        assert switch.icon == "mdi:vpn"
        mock_coordinator.data = _with_record_fields(
            mock_coordinator.data,
            "policy_based_routes",
            "site1",
            "route1",
            enabled=False,
        )
        assert switch.icon == "mdi:vpn-off"

        # Non-VPN route
        switch2 = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route2"
        )
        # A non-VPN route uses its icons.json state icons.
        assert switch2.icon is None
        mock_coordinator.data = _with_record_fields(
            mock_coordinator.data,
            "policy_based_routes",
            "site1",
            "route2",
            enabled=True,
        )
        assert switch2.icon is None

    @pytest.mark.asyncio
    async def test_turn_on_updates_route(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test enabling a policy-based route."""
        mock_coordinator.data = _with_record_fields(
            mock_coordinator.data,
            "policy_based_routes",
            "site1",
            "route1",
            enabled=False,
        )
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route1"
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        await switch.async_turn_on()

        config = mock_coordinator.config_coordinator
        config.async_set_policy_based_route_enabled.assert_awaited_once_with(
            "site1", "route1", enabled=True
        )
        mock_coordinator.network_client.routes.update_route.assert_not_called()
        require_mock(switch.async_write_ha_state).assert_not_called()
        mock_coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_turn_off_updates_route(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test disabling a policy-based route."""
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route1"
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        await switch.async_turn_off()

        config = mock_coordinator.config_coordinator
        config.async_set_policy_based_route_enabled.assert_awaited_once_with(
            "site1", "route1", enabled=False
        )
        mock_coordinator.network_client.routes.update_route.assert_not_called()
        require_mock(switch.async_write_ha_state).assert_not_called()
        mock_coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_turn_off_warns_when_kill_switch_enabled(
        self, mock_coordinator: MagicMock, caplog: pytest.LogCaptureFixture, monkeypatch
    ) -> None:
        """Test disabling a route with an active kill switch logs a warning."""
        # route1 has killSwitch=True in the fixture data.
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route1"
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        with caplog.at_level(logging.WARNING):
            await switch.async_turn_off()

        assert any("kill switch" in record.message.lower() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_turn_off_no_warning_when_kill_switch_disabled(
        self, mock_coordinator: MagicMock, caplog: pytest.LogCaptureFixture, monkeypatch
    ) -> None:
        """Test disabling a route without a kill switch logs no warning."""
        # route2 has no killSwitch key in the fixture data.
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route2"
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        with caplog.at_level(logging.WARNING):
            await switch.async_turn_off()

        assert not any(
            "kill switch" in record.message.lower() for record in caplog.records
        )

    def test_fallback_device_info_without_gateway(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test fallback device registry entry when no gateway device is found."""
        mock_coordinator.data = _with_site_records(
            mock_coordinator.data, "devices", "site1", {}
        )

        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route1"
        )

        assert require_not_none(switch._attr_device_info)["identifiers"] == {
            (DOMAIN, "policy_based_routes_site1")
        }
        assert (
            require_not_none(switch._attr_device_info)["name"]
            == "Policy-Based Routes (Default)"
        )

    @pytest.mark.asyncio
    async def test_turn_on_error_does_not_write_state(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test route update failures do not write optimistic state."""
        config = mock_coordinator.config_coordinator
        config.async_set_policy_based_route_enabled.side_effect = HomeAssistantError(
            "Unable to update policy-based route route1"
        )
        mock_coordinator.data = _with_record_fields(
            mock_coordinator.data,
            "policy_based_routes",
            "site1",
            "route1",
            enabled=False,
        )

        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route1"
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        with pytest.raises(
            HomeAssistantError, match="Unable to update policy-based route"
        ):
            await switch.async_turn_on()

        require_mock(switch.async_write_ha_state).assert_not_called()
        assert (
            mock_coordinator.data["policy_based_routes"]["site1"]["route1"]["enabled"]
            is False
        )

    @pytest.mark.asyncio
    async def test_turn_off_error_does_not_write_state(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test route disable failures do not write optimistic state."""
        config = mock_coordinator.config_coordinator
        config.async_set_policy_based_route_enabled.side_effect = HomeAssistantError(
            "Unable to update policy-based route route1"
        )
        mock_coordinator.data = _with_record_fields(
            mock_coordinator.data,
            "policy_based_routes",
            "site1",
            "route1",
            enabled=True,
        )

        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route1"
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        with pytest.raises(
            HomeAssistantError, match="Unable to update policy-based route"
        ):
            await switch.async_turn_off()

        require_mock(switch.async_write_ha_state).assert_not_called()
        assert (
            mock_coordinator.data["policy_based_routes"]["site1"]["route1"]["enabled"]
            is True
        )

    def test_unnamed_policy_based_route_switch(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test unnamed policy route switch falls back to unnamed key."""
        mock_coordinator.data = _with_site_records(
            mock_coordinator.data,
            "policy_based_routes",
            "site1",
            {"route_unnamed": {"id": "route_unnamed", "name": ""}},
        )
        entity = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, POLICY_BASED_ROUTE_SWITCH, "site1", "route_unnamed"
        )
        assert entity.translation_key == "policy_based_route_unnamed"
        assert entity.translation_placeholders == {"route_id": "route_unnamed"}


class TestAsyncSetupEntryPolicyBasedRoutes:
    """Tests policy-based route discovery in switch platform setup."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with policy-based route data."""
        coordinator = autospec_command_owners(MagicMock())
        coordinator.protect_client = None
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.network_client.routes = MagicMock()
        coordinator.network_client.routes.update_route = AsyncMock()
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
            if (
                isinstance(entity, UnifiInsightsNetworkRuleSwitch)
                and entity.entity_description is POLICY_BASED_ROUTE_SWITCH
            )
        ]
        assert len(route_switches) == 2
        assert {entity._rule_id for entity in route_switches} == {"route1", "route2"}

    @pytest.mark.asyncio
    async def test_setup_entry_routes_malformed_site_and_record_and_dedupe(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Non-dict site collections/route records are skipped, and
        rediscovery on unchanged data does not duplicate switches."""
        mock_coordinator.data = _with_site_records(
            mock_coordinator.data, "policy_based_routes", "site2", "not-a-dict"
        )
        mock_coordinator.data = with_rule(
            mock_coordinator.data,
            "policy_based_routes",
            "site1",
            "bad_route",
            "not-a-dict",
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
            if (
                isinstance(entity, UnifiInsightsNetworkRuleSwitch)
                and entity.entity_description is POLICY_BASED_ROUTE_SWITCH
            )
        ]
        assert len(route_switches) == 2
        assert all(e._rule_id != "bad_route" for e in route_switches)

        # Re-running discovery on unchanged data must not add duplicates.
        listener()

        assert async_add_entities.call_count == 1


class TestUnifiVpnClientSwitch:
    """Tests for UnifiVpnClientSwitch entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with VPN client data."""
        coordinator = autospec_command_owners(MagicMock())
        coordinator.protect_client = None
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.network_client.vpn_clients = MagicMock()
        coordinator.network_client.vpn_clients.update_vpn_client = AsyncMock()
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
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, VPN_CLIENT_SWITCH, "site1", "vpn1"
        )

        assert switch._attr_unique_id == "site1_vpn1_vpn_client"
        assert switch._attr_translation_key == "vpn_client"
        assert switch._attr_translation_placeholders == {
            "vpn_client_name": "Privado VPN"
        }
        assert switch.entity_category == EntityCategory.CONFIG
        assert require_not_none(switch._attr_device_info)["identifiers"] == {
            (DOMAIN, "site1_gateway1")
        }

    def test_available(self, mock_coordinator: MagicMock) -> None:
        """Test switch availability depends on coordinator and data."""
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, VPN_CLIENT_SWITCH, "site1", "vpn1"
        )
        assert switch.available is True
        set_availability(mock_coordinator, config=False)
        assert switch.available is False

        mock_coordinator.last_update_success = True
        mock_coordinator.data = _with_site_records(
            mock_coordinator.data, "vpn_clients", "site1", {}
        )
        assert switch.available is False

    def test_is_on(self, mock_coordinator: MagicMock) -> None:
        """Test switch state mirrors VPN client enabled state."""
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, VPN_CLIENT_SWITCH, "site1", "vpn1"
        )

        assert switch.is_on is True
        mock_coordinator.data = _with_record_fields(
            mock_coordinator.data,
            "vpn_clients",
            "site1",
            "vpn1",
            enabled=False,
        )
        assert switch.is_on is False

    def test_extra_state_attributes(self, mock_coordinator: MagicMock) -> None:
        """Test VPN client metadata attributes."""
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, VPN_CLIENT_SWITCH, "site1", "vpn1"
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
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, VPN_CLIENT_SWITCH, "site1", "vpn1"
        )
        # The state icons come from icons.json.
        assert switch.icon is None
        mock_coordinator.data = _with_record_fields(
            mock_coordinator.data,
            "vpn_clients",
            "site1",
            "vpn1",
            enabled=False,
        )
        assert switch.icon is None

    @pytest.mark.asyncio
    async def test_turn_on_updates_vpn_client(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test enabling a VPN client."""
        mock_coordinator.data = _with_record_fields(
            mock_coordinator.data,
            "vpn_clients",
            "site1",
            "vpn1",
            enabled=False,
        )
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, VPN_CLIENT_SWITCH, "site1", "vpn1"
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        await switch.async_turn_on()

        config = mock_coordinator.config_coordinator
        config.async_set_vpn_client_enabled.assert_awaited_once_with(
            "site1", "vpn1", enabled=True
        )
        mock_coordinator.network_client.vpn_clients.update_vpn_client.assert_not_called()
        require_mock(switch.async_write_ha_state).assert_not_called()
        mock_coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_turn_off_updates_vpn_client(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test disabling a VPN client."""
        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, VPN_CLIENT_SWITCH, "site1", "vpn1"
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        await switch.async_turn_off()

        config = mock_coordinator.config_coordinator
        config.async_set_vpn_client_enabled.assert_awaited_once_with(
            "site1", "vpn1", enabled=False
        )
        mock_coordinator.network_client.vpn_clients.update_vpn_client.assert_not_called()
        require_mock(switch.async_write_ha_state).assert_not_called()
        mock_coordinator.async_request_refresh.assert_not_awaited()

    def test_fallback_device_info_without_gateway(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test fallback device registry entry when no gateway device is found."""
        mock_coordinator.data = _with_site_records(
            mock_coordinator.data, "devices", "site1", {}
        )

        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, VPN_CLIENT_SWITCH, "site1", "vpn1"
        )

        assert require_not_none(switch._attr_device_info)["identifiers"] == {
            (DOMAIN, "vpn_clients_site1")
        }
        assert (
            require_not_none(switch._attr_device_info)["name"]
            == "VPN Clients (Default)"
        )

    @pytest.mark.asyncio
    async def test_turn_on_error_does_not_write_state(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test VPN client update failures do not write optimistic state."""
        config = mock_coordinator.config_coordinator
        config.async_set_vpn_client_enabled.side_effect = HomeAssistantError(
            "Unable to update VPN client vpn1"
        )
        mock_coordinator.data = _with_record_fields(
            mock_coordinator.data,
            "vpn_clients",
            "site1",
            "vpn1",
            enabled=False,
        )

        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, VPN_CLIENT_SWITCH, "site1", "vpn1"
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        with pytest.raises(HomeAssistantError, match="Unable to update VPN client"):
            await switch.async_turn_on()

        require_mock(switch.async_write_ha_state).assert_not_called()
        assert mock_coordinator.data["vpn_clients"]["site1"]["vpn1"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_turn_off_error_does_not_write_state(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test VPN client disable failures do not write optimistic state."""
        config = mock_coordinator.config_coordinator
        config.async_set_vpn_client_enabled.side_effect = HomeAssistantError(
            "Unable to update VPN client vpn1"
        )
        mock_coordinator.data = _with_record_fields(
            mock_coordinator.data,
            "vpn_clients",
            "site1",
            "vpn1",
            enabled=True,
        )

        switch = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, VPN_CLIENT_SWITCH, "site1", "vpn1"
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        with pytest.raises(HomeAssistantError, match="Unable to update VPN client"):
            await switch.async_turn_off()

        require_mock(switch.async_write_ha_state).assert_not_called()
        assert mock_coordinator.data["vpn_clients"]["site1"]["vpn1"]["enabled"] is True

    def test_unnamed_vpn_client_switch(self, mock_coordinator: MagicMock) -> None:
        """Test unnamed VPN client switch falls back to unnamed key."""
        mock_coordinator.data = _with_site_records(
            mock_coordinator.data,
            "vpn_clients",
            "site1",
            {"vpn_unnamed": {"id": "vpn_unnamed"}},
        )
        entity = UnifiInsightsNetworkRuleSwitch(
            mock_coordinator, VPN_CLIENT_SWITCH, "site1", "vpn_unnamed"
        )
        assert entity.translation_key == "vpn_client_unnamed"


class TestAsyncSetupEntryVpnClients:
    """Tests VPN client discovery in switch platform setup."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with VPN client data."""
        coordinator = autospec_command_owners(MagicMock())
        coordinator.protect_client = None
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.network_client.vpn_clients = MagicMock()
        coordinator.network_client.vpn_clients.update_vpn_client = AsyncMock()
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
            entity
            for entity in entities
            if (
                isinstance(entity, UnifiInsightsNetworkRuleSwitch)
                and entity.entity_description is VPN_CLIENT_SWITCH
            )
        ]
        assert len(vpn_switches) == 2
        assert {entity._rule_id for entity in vpn_switches} == {"vpn1", "vpn2"}

    @pytest.mark.asyncio
    async def test_setup_entry_vpn_clients_malformed_site_and_record_and_dedupe(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Non-dict site collections/VPN client records are skipped, and
        rediscovery on unchanged data does not duplicate switches."""
        mock_coordinator.data = _with_site_records(
            mock_coordinator.data, "vpn_clients", "site2", "not-a-dict"
        )
        mock_coordinator.data = with_rule(
            mock_coordinator.data, "vpn_clients", "site1", "bad_vpn", "not-a-dict"
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
        vpn_switches = [
            entity
            for entity in entities
            if (
                isinstance(entity, UnifiInsightsNetworkRuleSwitch)
                and entity.entity_description is VPN_CLIENT_SWITCH
            )
        ]
        assert len(vpn_switches) == 2
        assert all(e._rule_id != "bad_vpn" for e in vpn_switches)

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
        mock_coordinator.data = _with_cameras(
            mock_coordinator.data,
            {
                "camera1": {
                    "id": "camera1",
                    "name": "Front Camera",
                    "state": "CONNECTED",
                }
            },
        )
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
        mock_coordinator.data = _with_cameras(mock_coordinator.data, {})
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
        coordinator = autospec_command_owners(MagicMock())
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
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
        assert switch.entity_category == EntityCategory.CONFIG
        assert getattr(switch, "_attr_icon", None) is None

    def test_update_from_data_led_enabled(self, mock_coordinator) -> None:
        """Test _update_from_data with LED enabled."""
        switch = UnifiProtectStatusLightSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert switch._attr_is_on is True

    def test_update_from_data_led_disabled(self, mock_coordinator: MagicMock) -> None:
        """Test _update_from_data with LED disabled."""
        mock_coordinator.data = _with_camera_fields(
            mock_coordinator.data, "camera1", ledSettings={"isEnabled": False}
        )

        switch = UnifiProtectStatusLightSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert switch._attr_is_on is False

    def test_update_from_data_no_led_settings(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test _update_from_data when ledSettings is missing (defaults to True)."""
        camera = mock_coordinator.data["protect"]["cameras"]["camera1"]
        mock_coordinator.data = with_camera(
            mock_coordinator.data,
            "camera1",
            {key: value for key, value in camera.items() if key != "ledSettings"},
        )

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
    async def test_async_turn_on_success(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test turning status light on successfully."""
        mock_coordinator.data = _with_camera_fields(
            mock_coordinator.data, "camera1", ledSettings={"isEnabled": False}
        )

        switch = UnifiProtectStatusLightSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        await switch.async_turn_on()

        protect = protect_owner(mock_coordinator)
        protect.async_update_camera_settings.assert_awaited_once_with(
            "camera1", ledSettings={"isEnabled": True}
        )
        # The owner publishes the written setting; the state follows from it.
        mock_coordinator.data = _with_camera_fields(
            mock_coordinator.data, "camera1", ledSettings={"isEnabled": True}
        )
        switch._update_from_data()
        assert switch._attr_is_on is True

    @pytest.mark.asyncio
    async def test_async_turn_on_error(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test turning status light on with error."""
        protect_owner(
            mock_coordinator
        ).async_update_camera_settings.side_effect = HomeAssistantError(
            "Unable to update camera camera1"
        )
        mock_coordinator.data = _with_camera_fields(
            mock_coordinator.data, "camera1", ledSettings={"isEnabled": False}
        )

        switch = UnifiProtectStatusLightSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        with pytest.raises(HomeAssistantError, match="Unable to update camera"):
            await switch.async_turn_on()

        require_mock(switch.async_write_ha_state).assert_not_called()

    @pytest.mark.asyncio
    async def test_async_turn_off_success(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test turning status light off successfully."""
        switch = UnifiProtectStatusLightSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        await switch.async_turn_off()

        protect = protect_owner(mock_coordinator)
        protect.async_update_camera_settings.assert_awaited_once_with(
            "camera1", ledSettings={"isEnabled": False}
        )
        # The owner publishes the written setting; the state follows from it.
        mock_coordinator.data = _with_camera_fields(
            mock_coordinator.data, "camera1", ledSettings={"isEnabled": False}
        )
        switch._update_from_data()
        assert switch._attr_is_on is False

    @pytest.mark.asyncio
    async def test_async_turn_off_error(self, mock_coordinator, monkeypatch) -> None:
        """Test turning status light off with error."""
        protect_owner(
            mock_coordinator
        ).async_update_camera_settings.side_effect = HomeAssistantError(
            "Unable to update camera camera1"
        )

        switch = UnifiProtectStatusLightSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        with pytest.raises(HomeAssistantError, match="Unable to update camera"):
            await switch.async_turn_off()

        require_mock(switch.async_write_ha_state).assert_not_called()


class TestUnifiProtectHighFPSSwitch:
    """Tests for UnifiProtectHighFPSSwitch entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = autospec_command_owners(MagicMock())
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
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
        assert switch.entity_category == EntityCategory.CONFIG
        assert getattr(switch, "_attr_icon", None) is None

    def test_update_from_data_default_mode(self, mock_coordinator) -> None:
        """Test _update_from_data with default video mode."""
        switch = UnifiProtectHighFPSSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert switch._attr_is_on is False

    def test_update_from_data_high_fps_mode(self, mock_coordinator: MagicMock) -> None:
        """Test _update_from_data with high FPS video mode."""
        mock_coordinator.data = _with_camera_fields(
            mock_coordinator.data, "camera1", videoMode=VIDEO_MODE_HIGH_FPS
        )

        switch = UnifiProtectHighFPSSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert switch._attr_is_on is True

    def test_update_from_data_sport_mode(self, mock_coordinator: MagicMock) -> None:
        """Test _update_from_data with sport video mode (not high FPS)."""
        mock_coordinator.data = _with_camera_fields(
            mock_coordinator.data, "camera1", videoMode="sport"
        )

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
    async def test_async_turn_on_success(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test enabling high FPS mode successfully."""
        switch = UnifiProtectHighFPSSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        await switch.async_turn_on()

        protect = protect_owner(mock_coordinator)
        protect.async_update_camera_settings.assert_awaited_once_with(
            "camera1", videoMode=VIDEO_MODE_HIGH_FPS
        )
        # The owner publishes the written setting; the state follows from it.
        mock_coordinator.data = _with_camera_fields(
            mock_coordinator.data, "camera1", videoMode=VIDEO_MODE_HIGH_FPS
        )
        switch._update_from_data()
        assert switch._attr_is_on is True

    @pytest.mark.asyncio
    async def test_async_turn_on_error(self, mock_coordinator, monkeypatch) -> None:
        """Test enabling high FPS mode with error."""
        protect_owner(
            mock_coordinator
        ).async_update_camera_settings.side_effect = HomeAssistantError(
            "Unable to update camera camera1"
        )

        switch = UnifiProtectHighFPSSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        switch._attr_is_on = False
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        with pytest.raises(HomeAssistantError, match="Unable to update camera"):
            await switch.async_turn_on()

        require_mock(switch.async_write_ha_state).assert_not_called()

    @pytest.mark.asyncio
    async def test_async_turn_off_success(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test disabling high FPS mode successfully."""
        mock_coordinator.data = _with_camera_fields(
            mock_coordinator.data, "camera1", videoMode=VIDEO_MODE_HIGH_FPS
        )

        switch = UnifiProtectHighFPSSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        await switch.async_turn_off()

        protect = protect_owner(mock_coordinator)
        protect.async_update_camera_settings.assert_awaited_once_with(
            "camera1", videoMode=VIDEO_MODE_DEFAULT
        )
        # The owner publishes the written setting; the state follows from it.
        mock_coordinator.data = _with_camera_fields(
            mock_coordinator.data, "camera1", videoMode=VIDEO_MODE_DEFAULT
        )
        switch._update_from_data()
        assert switch._attr_is_on is False

    @pytest.mark.asyncio
    async def test_async_turn_off_error(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test disabling high FPS mode with error."""
        protect_owner(
            mock_coordinator
        ).async_update_camera_settings.side_effect = HomeAssistantError(
            "Unable to update camera camera1"
        )
        mock_coordinator.data = _with_camera_fields(
            mock_coordinator.data, "camera1", videoMode=VIDEO_MODE_HIGH_FPS
        )

        switch = UnifiProtectHighFPSSwitch(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        with pytest.raises(HomeAssistantError, match="Unable to update camera"):
            await switch.async_turn_off()

        require_mock(switch.async_write_ha_state).assert_not_called()


class TestAsyncSetupEntryWithNewSwitches:
    """Test async_setup_entry with new camera switches."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator."""
        coordinator = autospec_command_owners(MagicMock())
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
        coordinator = autospec_command_owners(MagicMock())
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
        coordinator = autospec_command_owners(MagicMock())
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
        coordinator = autospec_command_owners(MagicMock())
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
        coordinator = autospec_command_owners(MagicMock())
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
        coordinator = autospec_command_owners(MagicMock())
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
        coordinator = autospec_command_owners(MagicMock())
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
        coordinator = autospec_command_owners(MagicMock())
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
        device = mock_coordinator.device_coordinator
        device.async_unblock_client.side_effect = HomeAssistantError(
            "Unable to unblock client client1"
        )

        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        with pytest.raises(HomeAssistantError, match="Unable to unblock client"):
            await switch.async_turn_on()

        device.async_unblock_client.assert_awaited_once_with("site1", "client1")

    @pytest.mark.asyncio
    async def test_turn_off_handles_error(self, mock_coordinator) -> None:
        """Test async_turn_off handles errors gracefully (lines 884-885)."""
        device = mock_coordinator.device_coordinator
        device.async_block_client.side_effect = HomeAssistantError(
            "Unable to block client client1"
        )

        switch = UnifiClientBlockSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        with pytest.raises(HomeAssistantError, match="Unable to block client"):
            await switch.async_turn_off()

        device.async_block_client.assert_awaited_once_with("site1", "client1")


class TestUnifiWifiSwitchEdgeCases:
    """Tests for UnifiWifiSwitch edge cases."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with WiFi."""
        coordinator = autospec_command_owners(MagicMock())
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
        config = mock_coordinator.config_coordinator
        config.async_update_wifi_network.side_effect = HomeAssistantError(
            "Unable to update WiFi network wifi1"
        )

        switch = UnifiWifiSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
            wifi_data=mock_coordinator.data["wifi"]["site1"]["wifi1"],
        )

        with pytest.raises(HomeAssistantError, match="Unable to update WiFi"):
            await switch.async_turn_on()

        config.async_update_wifi_network.assert_awaited_once_with(
            "site1", "wifi1", enabled=True
        )

    @pytest.mark.asyncio
    async def test_turn_off_handles_error(self, mock_coordinator) -> None:
        """Test async_turn_off handles errors gracefully (lines 1000-1001)."""
        config = mock_coordinator.config_coordinator
        config.async_update_wifi_network.side_effect = HomeAssistantError(
            "Unable to update WiFi network wifi1"
        )

        switch = UnifiWifiSwitch(
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
            wifi_data=mock_coordinator.data["wifi"]["site1"]["wifi1"],
        )

        with pytest.raises(HomeAssistantError, match="Unable to update WiFi"):
            await switch.async_turn_off()

        config.async_update_wifi_network.assert_awaited_once_with(
            "site1", "wifi1", enabled=False
        )

    def test_get_wifi_data_fallback_to_initial_data(
        self, mock_coordinator: MagicMock
    ) -> None:
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
        mock_coordinator.data = _with_site_records(
            mock_coordinator.data, "wifi", "site1", {}
        )

        wifi_data = switch._get_wifi_data()
        assert wifi_data == initial_wifi_data


class TestUnifiOutletSwitch:
    """Tests for UnifiOutletSwitch platform entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with PDU device data."""
        coordinator = autospec_command_owners(MagicMock())
        coordinator.available = True
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
        coordinator.device_coordinator.async_set_outlet_state.return_value = True
        coordinator.async_request_refresh = AsyncMock()
        return coordinator

    @pytest.mark.asyncio
    async def test_async_setup_entry_creates_outlet_switches(
        self, mock_coordinator
    ) -> None:
        mock_entry = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        entities = []

        async_add_entities = EntityCollector(entities)

        await async_setup_entry(
            MagicMock(),
            mock_entry,
            async_add_entities,
        )

        outlet_switches = [
            e
            for e in entities
            if (
                isinstance(e, UnifiOutletSwitch)
                and e.entity_description is OUTLET_RELAY_SWITCH
            )
        ]
        cycle_switches = [
            e
            for e in entities
            if (
                isinstance(e, UnifiOutletSwitch)
                and e.entity_description is OUTLET_CYCLE_SWITCH
            )
        ]

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

        async_add_entities = EntityCollector(entities)

        await async_setup_entry(
            MagicMock(),
            mock_entry,
            async_add_entities,
        )
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        outlet_before = len(
            [
                e
                for e in entities
                if (
                    isinstance(e, UnifiOutletSwitch)
                    and e.entity_description is OUTLET_RELAY_SWITCH
                )
            ]
        )
        cycle_before = len(
            [
                e
                for e in entities
                if (
                    isinstance(e, UnifiOutletSwitch)
                    and e.entity_description is OUTLET_CYCLE_SWITCH
                )
            ]
        )
        assert outlet_before == 3
        assert cycle_before == 2

        listener()

        outlet_after = len(
            [
                e
                for e in entities
                if (
                    isinstance(e, UnifiOutletSwitch)
                    and e.entity_description is OUTLET_RELAY_SWITCH
                )
            ]
        )
        cycle_after = len(
            [
                e
                for e in entities
                if (
                    isinstance(e, UnifiOutletSwitch)
                    and e.entity_description is OUTLET_CYCLE_SWITCH
                )
            ]
        )
        assert outlet_after == outlet_before
        assert cycle_after == cycle_before

    def test_outlet_switch_properties(self, mock_coordinator) -> None:
        """Test UnifiOutletSwitch properties and unique_id literal."""
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            0
        ]
        switch = UnifiOutletSwitch(
            mock_coordinator,
            OUTLET_RELAY_SWITCH,
            "site1",
            "pdu1",
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
        assert require_not_none(attrs)["index"] == 1
        assert require_not_none(attrs)["name"] == "Main Server"
        assert require_not_none(attrs)["relay_state"] is True
        assert require_not_none(attrs)["cycle_enabled"] is True
        assert require_not_none(attrs)["outlet_power"] == 150.0

    def test_outlet_switch_unavailable_when_device_coordinator_unavailable(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test outlet switch unavailable when device coordinator is unavailable."""
        set_availability(mock_coordinator, device=False)
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            0
        ]
        switch = UnifiOutletSwitch(
            mock_coordinator,
            OUTLET_RELAY_SWITCH,
            "site1",
            "pdu1",
            outlet_index=1,
            outlet_data=outlet_data,
        )
        assert switch.available is False

    @pytest.mark.asyncio
    async def test_outlet_switch_turn_on(self, mock_coordinator, monkeypatch) -> None:
        """Test turning on the outlet switch."""
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            1
        ]
        switch = UnifiOutletSwitch(
            mock_coordinator,
            OUTLET_RELAY_SWITCH,
            "site1",
            "pdu1",
            outlet_index=2,
            outlet_data=outlet_data,
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        await switch.async_turn_on()

        mock_coordinator.device_coordinator.async_set_outlet_state.assert_awaited_once_with(
            "site1", "pdu1", 2, state=True
        )
        mock_coordinator.async_request_refresh.assert_not_awaited()
        require_mock(switch.async_write_ha_state).assert_not_called()

    @pytest.mark.asyncio
    async def test_outlet_switch_turn_off(self, mock_coordinator, monkeypatch) -> None:
        """Test turning off the outlet switch."""
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            0
        ]
        switch = UnifiOutletSwitch(
            mock_coordinator,
            OUTLET_RELAY_SWITCH,
            "site1",
            "pdu1",
            outlet_index=1,
            outlet_data=outlet_data,
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        await switch.async_turn_off()

        mock_coordinator.device_coordinator.async_set_outlet_state.assert_awaited_once_with(
            "site1", "pdu1", 1, state=False
        )
        mock_coordinator.async_request_refresh.assert_not_awaited()
        require_mock(switch.async_write_ha_state).assert_not_called()

    def test_outlet_switch_unavailable_when_device_offline(
        self, mock_coordinator: MagicMock
    ) -> None:
        """Test switch availability when device is offline."""
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data, "site1", "pdu1", state="OFFLINE", status="offline"
        )

        switch = UnifiOutletSwitch(
            mock_coordinator, OUTLET_RELAY_SWITCH, "site1", "pdu1", outlet_index=1
        )

        assert switch.available is False

    def test_outlet_switch_more_edge_cases(self, mock_coordinator: MagicMock) -> None:
        """Test outlet switch edge cases for non-list table and alternative keys."""
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data, "site1", "pdu1", outlet_table="not_a_list"
        )
        switch = UnifiOutletSwitch(
            mock_coordinator,
            OUTLET_RELAY_SWITCH,
            "site1",
            "pdu1",
            outlet_index=1,
            outlet_data={"relay_state": True},
        )
        assert switch._get_outlet_data() is None

        mock_coordinator.data = with_device_fields(
            mock_coordinator.data,
            "site1",
            "pdu1",
            outlet_table=[{"outlet_idx": 1, "relay_state": True, "name": "Outlet 1"}],
        )
        assert switch._get_outlet_data() == {
            "outlet_idx": 1,
            "relay_state": True,
            "name": "Outlet 1",
        }

        mock_coordinator.data = with_device_fields(
            mock_coordinator.data,
            "site1",
            "pdu1",
            outlet_table=[
                {
                    "index": 1,
                    "outlet_caps": 3,
                    "outlet_voltage": 120.0,
                    "outlet_current": 1.0,
                    "outlet_power": 120.0,
                    "outlet_power_factor": 0.99,
                }
            ],
        )
        attrs = switch.extra_state_attributes
        assert require_not_none(attrs)["outlet_voltage"] == 120.0
        assert require_not_none(attrs)["outlet_power_factor"] == 0.99

    @pytest.mark.asyncio
    async def test_outlet_switch_turn_on_failure_no_optimistic_update(
        self, mock_coordinator
    ) -> None:
        """Test outlet switch turn on failure asserts no optimistic state write."""

        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            1
        ]
        switch = UnifiOutletSwitch(
            mock_coordinator,
            OUTLET_RELAY_SWITCH,
            "site1",
            "pdu1",
            outlet_index=2,
            outlet_data=outlet_data,
        )
        mock_coordinator.device_coordinator.async_set_outlet_state.side_effect = (
            HomeAssistantError("API Error")
        )

        with (
            patch.object(switch, "async_write_ha_state") as write_state,
            pytest.raises(HomeAssistantError),
        ):
            await switch.async_turn_on()

        mock_coordinator.device_coordinator.async_set_outlet_state.assert_awaited_once_with(
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
            mock_coordinator,
            OUTLET_RELAY_SWITCH,
            "site1",
            "pdu1",
            outlet_index=1,
            outlet_data=outlet_data,
        )
        mock_coordinator.device_coordinator.async_set_outlet_state.side_effect = (
            HomeAssistantError("API Error")
        )

        with (
            patch.object(switch, "async_write_ha_state") as write_state,
            pytest.raises(HomeAssistantError),
        ):
            await switch.async_turn_off()

        mock_coordinator.device_coordinator.async_set_outlet_state.assert_awaited_once_with(
            "site1", "pdu1", 1, state=False
        )
        write_state.assert_not_called()
        mock_coordinator.async_request_refresh.assert_not_awaited()
        assert switch.is_on is True


class TestUnifiOutletCycleSwitch:
    """Tests for the outlet power-cycle switch."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create mock coordinator with PDU device data."""
        coordinator = autospec_command_owners(MagicMock())
        coordinator.available = True
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
        coordinator.device_coordinator.async_set_outlet_state.return_value = True
        coordinator.async_request_refresh = AsyncMock()
        return coordinator

    def test_cycle_switch_properties(self, mock_coordinator) -> None:
        """Test the power-cycle switch properties and unique_id literal."""
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            0
        ]
        switch = UnifiOutletSwitch(
            mock_coordinator,
            OUTLET_CYCLE_SWITCH,
            "site1",
            "pdu1",
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
        set_availability(mock_coordinator, device=False)
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            0
        ]
        switch = UnifiOutletSwitch(
            mock_coordinator,
            OUTLET_CYCLE_SWITCH,
            "site1",
            "pdu1",
            outlet_index=1,
            outlet_data=outlet_data,
        )
        assert switch.available is False

    @pytest.mark.asyncio
    async def test_cycle_switch_turn_on(self, mock_coordinator, monkeypatch) -> None:
        """Test turning on the power cycle switch."""
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            1
        ]
        switch = UnifiOutletSwitch(
            mock_coordinator,
            OUTLET_CYCLE_SWITCH,
            "site1",
            "pdu1",
            outlet_index=2,
            outlet_data=outlet_data,
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        await switch.async_turn_on()

        mock_coordinator.device_coordinator.async_set_outlet_state.assert_awaited_once_with(
            "site1", "pdu1", 2, state=False, cycle_enabled=True
        )
        mock_coordinator.async_request_refresh.assert_not_awaited()
        require_mock(switch.async_write_ha_state).assert_not_called()

    @pytest.mark.asyncio
    async def test_cycle_switch_turn_off(self, mock_coordinator, monkeypatch) -> None:
        """Test turning off the power cycle switch."""
        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            0
        ]
        switch = UnifiOutletSwitch(
            mock_coordinator,
            OUTLET_CYCLE_SWITCH,
            "site1",
            "pdu1",
            outlet_index=1,
            outlet_data=outlet_data,
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        await switch.async_turn_off()

        mock_coordinator.device_coordinator.async_set_outlet_state.assert_awaited_once_with(
            "site1", "pdu1", 1, state=True, cycle_enabled=False
        )
        mock_coordinator.async_request_refresh.assert_not_awaited()
        require_mock(switch.async_write_ha_state).assert_not_called()

    @pytest.mark.asyncio
    async def test_cycle_switch_does_not_default_relay_on(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test toggling power cycling never energizes an unknown relay.

        set_outlet_state always writes relay_state into the override, so an
        outlet whose reported data carries no relay_state must be treated as
        OFF. Defaulting to ON would switch on a load the user did not ask for.
        """
        mock_coordinator.data = with_device_fields(
            mock_coordinator.data,
            "site1",
            "pdu1",
            outlet_table=[{"index": 1, "name": "Main Server", "cycle_enabled": False}],
        )
        switch = UnifiOutletSwitch(
            mock_coordinator,
            OUTLET_CYCLE_SWITCH,
            "site1",
            "pdu1",
            outlet_index=1,
            outlet_data={"index": 1, "cycle_enabled": False},
        )
        patch_attribute(monkeypatch, switch, "async_write_ha_state", MagicMock())

        await switch.async_turn_on()

        mock_coordinator.device_coordinator.async_set_outlet_state.assert_awaited_once_with(
            "site1", "pdu1", 1, state=False, cycle_enabled=True
        )

    def test_outlet_switch_edge_cases(self, mock_coordinator: MagicMock) -> None:
        """Test outlet switch edge cases for complete branch coverage."""
        # 1. Device data missing or not dict
        mock_coordinator.data = with_sections(mock_coordinator.data, devices={})
        switch = UnifiOutletSwitch(
            mock_coordinator,
            OUTLET_RELAY_SWITCH,
            "site1",
            "pdu1",
            outlet_index=1,
            outlet_data={"relay_state": True},
        )
        assert switch._get_outlet_data() is None
        assert switch.is_on is True
        assert switch.available is False

        # 2. Outlet table with invalid entries
        mock_coordinator.data = with_network_device(
            mock_coordinator.data,
            "site1",
            "pdu1",
            {
                "outlet_table": [
                    "not_a_dict",
                    {"index": "invalid"},
                    {"outlet_idx": 2},
                ],
                "state": "ONLINE",
                "status": "online",
            },
        )
        assert switch._get_outlet_data() is None

        # 3. Cycle switch with missing/offline data
        cycle_switch = UnifiOutletSwitch(
            mock_coordinator,
            OUTLET_CYCLE_SWITCH,
            "site1",
            "pdu1",
            outlet_index=1,
            outlet_data={"cycle_enabled": True},
        )
        assert cycle_switch._get_outlet_data() is None
        assert cycle_switch.is_on is True
        assert cycle_switch.available is False

    def test_cycle_switch_more_edge_cases(self, mock_coordinator: MagicMock) -> None:
        """Test cycle switch edge cases for non-list table and offline state."""
        switch = UnifiOutletSwitch(
            mock_coordinator,
            OUTLET_CYCLE_SWITCH,
            "site1",
            "pdu1",
            outlet_index=1,
            outlet_data={"cycle_enabled": True},
        )

        site_devices = mock_coordinator.data["devices"]["site1"]
        mock_coordinator.data = _with_site_records(
            mock_coordinator.data,
            "devices",
            "site1",
            {key: value for key, value in site_devices.items() if key != "pdu1"},
        )
        assert switch._get_outlet_data() is None

        mock_coordinator.data = with_network_device(
            mock_coordinator.data, "site1", "pdu1", {"outlet_table": None}
        )
        assert switch._get_outlet_data() is None

        mock_coordinator.data = with_network_device(
            mock_coordinator.data,
            "site1",
            "pdu1",
            {
                "state": "ONLINE",
                "outlet_table": [
                    "not_a_dict",
                    {"index": "invalid_int", "cycle_enabled": False},
                    {"outlet_idx": 1, "cycle_enabled": True},
                ],
            },
        )
        assert require_not_none(switch._get_outlet_data())["outlet_idx"] == 1

        mock_coordinator.data = with_device_fields(
            mock_coordinator.data, "site1", "pdu1", state="OFFLINE"
        )
        assert switch.available is False

    @pytest.mark.asyncio
    async def test_cycle_switch_turn_on_failure_no_optimistic_update(
        self, mock_coordinator
    ) -> None:
        """Test power cycle turn on failure asserts no optimistic state write."""

        outlet_data = mock_coordinator.data["devices"]["site1"]["pdu1"]["outlet_table"][
            1
        ]
        switch = UnifiOutletSwitch(
            mock_coordinator,
            OUTLET_CYCLE_SWITCH,
            "site1",
            "pdu1",
            outlet_index=2,
            outlet_data=outlet_data,
        )
        mock_coordinator.device_coordinator.async_set_outlet_state.side_effect = (
            HomeAssistantError("API Error")
        )

        with (
            patch.object(switch, "async_write_ha_state") as write_state,
            pytest.raises(HomeAssistantError),
        ):
            await switch.async_turn_on()

        mock_coordinator.device_coordinator.async_set_outlet_state.assert_awaited_once_with(
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
        switch = UnifiOutletSwitch(
            mock_coordinator,
            OUTLET_CYCLE_SWITCH,
            "site1",
            "pdu1",
            outlet_index=1,
            outlet_data=outlet_data,
        )
        mock_coordinator.device_coordinator.async_set_outlet_state.side_effect = (
            HomeAssistantError("API Error")
        )

        with (
            patch.object(switch, "async_write_ha_state") as write_state,
            pytest.raises(HomeAssistantError),
        ):
            await switch.async_turn_off()

        mock_coordinator.device_coordinator.async_set_outlet_state.assert_awaited_once_with(
            "site1", "pdu1", 1, state=True, cycle_enabled=False
        )
        write_state.assert_not_called()
        mock_coordinator.async_request_refresh.assert_not_awaited()
        assert switch.is_on is True


class TestFindGatewayDeviceId:
    """Tests for grouping site-level switches under the site's gateway."""

    @staticmethod
    def _coordinator(devices: dict) -> MagicMock:
        coordinator = autospec_command_owners(MagicMock())
        coordinator.data = {"devices": {"site1": devices}}
        return coordinator

    def test_recognised_gateway(self):
        """A gateway model is found."""
        coordinator = self._coordinator(
            {"sw": {"model": "USW-24"}, "gw": {"model": "UCG-Ultra"}}
        )
        assert get_site_gateway_id(coordinator.data, "site1") == "gw"

    def test_wan_data_alone_does_not_regroup_entities(self):
        """Merged WAN data must not move entities onto an unrecognised device.

        It is only present when that poll's legacy fetch worked, so grouping
        on it would move entities between devices across restarts.
        """
        coordinator = self._coordinator(
            {"gw": {"model": "Unknown", "wans": [{"key": "wan1"}]}}
        )
        assert get_site_gateway_id(coordinator.data, "site1") is None

    def test_non_dict_site_devices(self) -> None:
        """Test when site devices is not a dict."""
        coordinator = autospec_command_owners(MagicMock())
        coordinator.data = {"devices": {"site1": None}}
        assert get_site_gateway_id(coordinator.data, "site1") is None

    def test_non_dict_device_entry(self) -> None:
        """Test when site devices has non-dict entry."""
        coordinator = autospec_command_owners(MagicMock())
        coordinator.data = {
            "devices": {
                "site1": {
                    "invalid_device": "not_a_dict",
                    "gw": {"model": "UCG-Ultra"},
                }
            }
        }
        assert get_site_gateway_id(coordinator.data, "site1") == "gw"


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


@pytest.mark.parametrize("description", [OUTLET_RELAY_SWITCH, OUTLET_CYCLE_SWITCH])
def test_outlet_lookup_skips_outlets_without_an_index(
    description: UnifiOutletSwitchEntityDescription,
) -> None:
    """An outlet without any index alias is skipped, not matched."""
    coordinator = autospec_command_owners(MagicMock())
    coordinator.data = {
        "devices": {
            "site1": {
                "pdu1": {
                    "outlet_table": [
                        {"name": "No index"},
                        {"outlet_idx": 2, "name": "Second"},
                    ]
                }
            }
        }
    }

    switch = UnifiOutletSwitch(
        coordinator, description, "site1", "pdu1", outlet_index=2
    )

    assert switch._get_outlet_data() == {"outlet_idx": 2, "name": "Second"}


def test_outlet_cycle_switch_reports_no_outlet_attributes() -> None:
    """Only the relay switch reports the outlet attributes."""
    coordinator = autospec_command_owners(MagicMock())
    coordinator.data = {"devices": {}}
    outlet = {"index": 1, "name": "Modem", "relay_state": True, "cycle_enabled": True}

    relay = UnifiOutletSwitch(
        coordinator,
        OUTLET_RELAY_SWITCH,
        "site1",
        "pdu1",
        outlet_index=1,
        outlet_data=outlet,
    )
    cycle = UnifiOutletSwitch(
        coordinator,
        OUTLET_CYCLE_SWITCH,
        "site1",
        "pdu1",
        outlet_index=1,
        outlet_data=outlet,
    )

    assert relay.extra_state_attributes == {
        "index": 1,
        "name": "Modem",
        "relay_state": True,
        "cycle_enabled": True,
    }
    assert cycle.extra_state_attributes is None


@pytest.mark.parametrize("description", [OUTLET_RELAY_SWITCH, OUTLET_CYCLE_SWITCH])
@pytest.mark.parametrize(
    ("state", "healthy", "expected"),
    [
        ("GETTING_READY", True, True),
        ("GETTING_READY", False, False),
        ("ONLINE", False, False),
        ("OFFLINE", True, False),
        ("UPDATING", True, False),
        ("ADOPTING", True, False),
    ],
)
def test_outlet_serviceable_availability(
    description: UnifiOutletSwitchEntityDescription,
    state: str,
    *,
    healthy: bool,
    expected: bool,
) -> None:
    """Both outlet controls need fresh Device data and a serviceable device."""
    coordinator = MagicMock()
    coordinator.data = {
        "devices": {"site": {"pdu": {"state": state, "outlet_table": [{"index": 1}]}}}
    }
    set_availability(coordinator, device=healthy)
    switch = UnifiOutletSwitch(coordinator, description, "site", "pdu", outlet_index=1)
    assert switch.available is expected
    coordinator.data = with_device_fields(
        coordinator.data, "site", "pdu", outlet_table=[]
    )

    assert switch.available is False
