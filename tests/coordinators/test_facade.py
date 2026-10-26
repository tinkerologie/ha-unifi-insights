# Copyright 2026 UniFi Insights contributors
"""Tests for the facade's translated command errors and owner accessors (ADR-010)."""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util.read_only_dict import ReadOnlyDict
from unifi_official_api import UniFiConnectionError

from custom_components.unifi_insights.const import (
    DOMAIN,
)
from custom_components.unifi_insights.coordinators import UnifiInsightsFacadeCoordinator
from custom_components.unifi_insights.coordinators.config import (
    UnifiInsightsConfigCoordinator,
)
from custom_components.unifi_insights.coordinators.device import (
    UnifiInsightsDeviceCoordinator,
)
from custom_components.unifi_insights.coordinators.facade import (
    EMPTY_PROTECT,
    SITE_SECTIONS,
    FacadeData,
    build_facade_data,
    capture_availability,
)
from custom_components.unifi_insights.coordinators.protect import (
    UnifiInsightsProtectCoordinator,
)
from custom_components.unifi_insights.data_transforms import (
    NETWORK_FACTS_KEY,
    build_site_network_facts,
    device_facts,
    latest_voucher,
    network_client,
    network_device,
    network_device_stats,
    owner_available,
    protect_device,
    rule_record,
    site_record,
    site_section_available,
)
from tests.fixtures.assertions import (
    patch_attribute,
    require_instance,
    require_mock,
    require_not_none,
)
from tests.fixtures.command_owners import SITE_ID
from tests.fixtures.coordinator_clients import (
    _create_mock_network_client,
    _create_mock_protect_client,
)
from tests.fixtures.facade_data import (
    FACADE_DATA_KEYS,
    FrozenList,
    facade_data,
    frozen,
    with_camera,
    with_client,
    with_device_fields,
    with_device_stats,
    with_device_stats_fields,
    with_latest_voucher,
    with_network_device,
    with_protect_collection,
    with_protect_device,
    with_protect_fields,
    with_rule,
    with_sections,
)
from tests.fixtures.library_responses import (
    SAMPLE_UDM_PRO_OVERVIEW,
    SAMPLE_UDM_PRO_STATS,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

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


# --- Facade coordinator ------------------------------------------------------

# Tests for UniFi Insights facade coordinator.


@pytest.fixture
def mock_sub_coordinators() -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    """Create mock sub-coordinators for facade tests."""
    config_coord = MagicMock()
    config_coord.data = {
        "sites": {"site1": {"name": "Default"}},
        "vouchers": {},
    }
    config_coord.last_update_success = True
    config_coord.last_exception = None
    config_coord.async_refresh = AsyncMock()
    config_coord.async_refresh_vouchers = AsyncMock()

    device_coord = MagicMock()
    device_coord.data = {
        "devices": {
            "site1": {
                "dev1": {
                    "id": "dev1",
                    "macAddress": "00:11:22:33:44:55",
                    "name": "Switch 1",
                }
            }
        },
        "clients": {
            "site1": {
                "client1": {
                    "id": "client1",
                    "macAddress": "AA:BB:CC:DD:EE:01",
                    "name": "Phone",
                },
                "client_no_mac": {
                    "id": "client_no_mac",
                    "name": "Ghost Client",
                },
            }
        },
        "stats": {},
    }
    device_coord.last_update_success = True
    device_coord.last_exception = None
    device_coord.async_refresh = AsyncMock()

    protect_coord = MagicMock()
    protect_coord.data = {
        "cameras": {"cam1": {"mac": "66:77:88:99:AA:BB"}},
        "lights": {"light1": {"mac": "11:22:33:44:55:66"}},
        "sensors": {"sens1": {"macAddress": "22:33:44:55:66:77"}},
        "nvrs": {"nvr1": {"mac_address": "33:44:55:66:77:88"}},
        "doorlocks": {"lock1": {"mac": "44:55:66:77:88:99"}},
        "viewports": {"vp1": {"mac": "55:66:77:88:99:00"}},
    }
    protect_coord.last_update_success = True
    protect_coord.last_exception = None
    protect_coord.async_refresh = AsyncMock()

    innerspace_coord = MagicMock()
    innerspace_coord.data = {
        "access_points": {"ap1": {"name": "AP Hall"}},
        "switches": {"sw1": {"name": "Switch Rack"}},
        "inventory": {"inv1": {"name": "Item"}},
    }
    innerspace_coord.last_update_success = True
    innerspace_coord.last_exception = None
    innerspace_coord.async_refresh = AsyncMock()

    return config_coord, device_coord, protect_coord, innerspace_coord


@pytest.fixture
def facade(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
) -> UnifiInsightsFacadeCoordinator:
    """Create a test facade coordinator with mock children."""
    config_coord, device_coord, protect_coord, innerspace_coord = mock_sub_coordinators
    network_client = MagicMock()
    network_client.vouchers.create = AsyncMock()
    network_client.clients.unblock = AsyncMock()
    protect_client = MagicMock()

    return UnifiInsightsFacadeCoordinator(
        hass=hass,
        network_client=network_client,
        protect_client=protect_client,
        entry=mock_config_entry,
        config_coordinator=config_coord,
        device_coordinator=device_coord,
        protect_coordinator=protect_coord,
        innerspace_coordinator=innerspace_coord,
    )


class TestFacadeInnerSpaceMemo:
    """The InnerSpace view is rebuilt only when what it reads changed (ADR-013)."""

    @staticmethod
    def _rebuild(facade: UnifiInsightsFacadeCoordinator) -> Any:
        """Rebuild the snapshot and return its InnerSpace view."""
        facade._aggregate_data()
        return facade.data["innerspace"]

    def test_unchanged_inputs_reuse_the_view(
        self, facade: UnifiInsightsFacadeCoordinator
    ) -> None:
        """A rebuild with the same objects returns the same view, uncomputed."""
        before = facade.data["innerspace"]
        with patch(
            "custom_components.unifi_insights.coordinators.facade."
            "innerspace_correlation_key"
        ) as key:
            assert self._rebuild(facade) is before
        key.assert_not_called()

    def test_unrelated_protect_frame_reuses_the_view(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """A frame that replaces only events reads no device field."""
        protect_coord = mock_sub_coordinators[2]
        before = facade.data["innerspace"]
        protect_coord.data = {**protect_coord.data, "events": {"motion": {}}}

        with patch(
            "custom_components.unifi_insights.coordinators.facade."
            "innerspace_correlation_key"
        ) as key:
            assert self._rebuild(facade) is before
        key.assert_not_called()

    @pytest.mark.parametrize("collection", ["cameras", "nvrs"])
    def test_replaced_collection_with_the_same_devices_reuses_the_view(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
        collection: str,
    ) -> None:
        """A frame that replaces a collection with the same MACs rebuilds nothing."""
        protect_coord = mock_sub_coordinators[2]
        before = facade.data["innerspace"]
        protect_coord.data = {
            **protect_coord.data,
            collection: {**protect_coord.data[collection]},
        }

        with patch(
            "custom_components.unifi_insights.coordinators.facade.build_innerspace_view"
        ) as build:
            assert self._rebuild(facade) is before
        build.assert_not_called()

    def test_changed_protect_mac_rebuilds_the_view(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """A camera whose MAC now matches an InnerSpace record is correlated."""
        _, _, protect_coord, innerspace_coord = mock_sub_coordinators
        innerspace_coord.data = {
            **innerspace_coord.data,
            "access_points": {"ap1": {"name": "AP Hall", "mac": "AA:00:00:00:00:01"}},
        }
        before = self._rebuild(facade)
        assert before["correlations"]["ap1"] is None

        protect_coord.data = {
            **protect_coord.data,
            "cameras": {"cam1": {"id": "cam1", "mac": "AA:00:00:00:00:01"}},
        }
        after = self._rebuild(facade)

        assert after is not before
        assert after["correlations"]["ap1"]["device_id"] == "cam1"
        assert self._rebuild(facade) is after

    def test_new_device_poll_rebuilds_only_on_changed_fields(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """A poll with the same devices reuses the view; a changed MAC rebuilds it."""
        device_coord = mock_sub_coordinators[1]
        before = facade.data["innerspace"]

        device_coord.data = {
            **device_coord.data,
            "devices": {"site1": {**device_coord.data["devices"]["site1"]}},
        }
        assert self._rebuild(facade) is before

        device_coord.data = {
            **device_coord.data,
            "devices": {
                "site1": {"dev1": {"id": "dev1", "macAddress": "00:11:22:33:44:66"}}
            },
        }
        assert self._rebuild(facade) is not before

    def test_new_innerspace_data_rebuilds_the_view(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """New InnerSpace data is always correlated again."""
        innerspace_coord = mock_sub_coordinators[3]
        before = facade.data["innerspace"]
        innerspace_coord.data = {**innerspace_coord.data}

        after = self._rebuild(facade)

        assert after is not before
        assert after == before

    def test_without_innerspace_the_empty_view_is_reused(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Without InnerSpace data the empty view is built once."""
        innerspace_coord = mock_sub_coordinators[3]
        innerspace_coord.data = {}
        empty = self._rebuild(facade)

        assert empty["devices"] == {}
        assert empty["last_update"] is None
        assert self._rebuild(facade) is empty


class TestFacadeAsyncRefreshChildren:
    """Tests for _async_refresh_children."""

    async def test_refresh_children_includes_innerspace(
        self, facade: UnifiInsightsFacadeCoordinator, mock_sub_coordinators
    ) -> None:
        """Test _async_refresh_children with include_innerspace=True."""
        _, _, _, innerspace_coord = mock_sub_coordinators
        failures = await facade._async_refresh_children(
            include_protect=True, include_innerspace=True
        )
        assert failures == []
        innerspace_coord.async_refresh.assert_awaited_once()


class TestFacadeVoucherReads:
    """The facade's voucher reads pass the config coordinator's data through."""

    async def test_aggregate_exposes_config_vouchers(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Verify facade.data[vouchers] is sourced from config_coordinator."""
        config_coord, device_coord, _, _ = mock_sub_coordinators
        config_coord.data["vouchers"] = {"site1": {"v1": {"id": "v1"}}}
        device_coord.data["vouchers"] = {"site1": {"stale": {"id": "stale"}}}
        facade._aggregate_data()
        assert facade.data["vouchers"] == {"site1": {"v1": {"id": "v1"}}}

    def test_latest_vouchers_pass_through_by_reference(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """The facade publishes the config coordinator's latest vouchers as is."""
        config_coord = mock_sub_coordinators[0]
        latest = {"site1": {"id": "v1", "code": "1111111111"}}
        config_coord.data["latest_vouchers"] = latest
        facade._aggregate_data()
        assert facade.data["latest_vouchers"] is latest

    def test_facade_holds_no_voucher_state(
        self, facade: UnifiInsightsFacadeCoordinator
    ) -> None:
        """No facade attribute or command holds voucher state (ADR-013)."""
        assert not [name for name in vars(facade) if "voucher" in name.lower()]
        for name in (
            "get_voucher_settings",
            "async_generate_voucher",
            "async_delete_voucher",
            "_async_refresh_vouchers",
        ):
            assert not hasattr(facade, name), name


class TestFacadeNetworkFacts:
    """Tests for the read-model accessors (#337)."""

    def test_published_facts_pass_through_by_reference(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Published facts are returned by identity, without a rebuild."""
        device_coord = mock_sub_coordinators[1]
        site = build_site_network_facts(
            {"udm": SAMPLE_UDM_PRO_OVERVIEW, "sw": {"features": ["switching"]}},
            {"udm": SAMPLE_UDM_PRO_STATS},
        )
        facts_map = {"site1": site}
        device_coord.data = {**device_coord.data, NETWORK_FACTS_KEY: facts_map}
        facade._aggregate_data()
        builder = MagicMock(side_effect=AssertionError("rebuilt"))
        monkeypatch.setattr(
            "custom_components.unifi_insights.data_transforms.build_site_network_facts",
            builder,
        )

        assert facade.data[NETWORK_FACTS_KEY] is facts_map
        assert facade.get_device_facts("site1", "udm") is site.devices["udm"]
        assert facade.get_site_gateway_id("site1") == "udm"
        assert facade.get_device_facts("site1", "unknown") is None
        assert facade.get_device_facts("unknown", "udm") is None
        assert facade.get_site_gateway_id("unknown") is None
        builder.assert_not_called()

    def test_unpublished_facts_fall_back_to_raw_data(
        self, facade: UnifiInsightsFacadeCoordinator
    ) -> None:
        """A child that never published facts still answers from raw data."""
        facade._aggregate_data()

        assert facade.data[NETWORK_FACTS_KEY] is None
        facts = facade.get_device_facts("site1", "dev1")
        assert facts is not None
        assert facts.mac == "00:11:22:33:44:55"
        assert facade.get_site_gateway_id("site1") is None
        assert facade.get_device_facts("unknown", "dev1") is None


# --- Facade coordinator ------------------------------------------------------


class TestUnifiFacadeCoordinator:
    """Tests for UnifiInsightsFacadeCoordinator."""

    @pytest.fixture
    def config_coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> UnifiInsightsConfigCoordinator:
        """Create a config coordinator."""
        network_client = _create_mock_network_client()
        coord = UnifiInsightsConfigCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=None,
            entry=mock_config_entry,
        )
        coord.data = {
            "sites": {"default": {"id": "default", "name": "Default"}},
            "wifi": {"default": {"wifi1": {"id": "wifi1"}}},
            "firewall_rules": {
                "default": {"rule1": {"id": "rule1", "name": "Block Instagram"}}
            },
            "network_info": {},
        }
        return coord

    @pytest.fixture
    def device_coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        config_coordinator: UnifiInsightsConfigCoordinator,
    ) -> UnifiInsightsDeviceCoordinator:
        """Create a device coordinator."""
        network_client = _create_mock_network_client()
        coord = UnifiInsightsDeviceCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=None,
            entry=mock_config_entry,
            config_coordinator=config_coordinator,
        )
        coord.data = {
            "devices": {"default": {"device1": {"id": "device1"}}},
            "clients": {"default": {"client1": {"id": "client1"}}},
            "stats": {"default": {"device1": {"cpu": 10}}},
            "vouchers": {},
            "last_update": None,
        }
        return coord

    @pytest.fixture
    def protect_coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> UnifiInsightsProtectCoordinator:
        """Create a protect coordinator."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        coord = UnifiInsightsProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )
        coord.data = {
            "cameras": {"camera1": {"id": "camera1"}},
            "lights": {},
            "sensors": {},
            "nvrs": {},
            "viewers": {},
            "chimes": {},
            "liveviews": {},
            "protect_info": {},
            "events": {},
            "last_update": None,
        }
        return coord

    @pytest.fixture
    async def facade_coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        config_coordinator: UnifiInsightsConfigCoordinator,
        device_coordinator: UnifiInsightsDeviceCoordinator,
        protect_coordinator: UnifiInsightsProtectCoordinator,
    ) -> AsyncGenerator[UnifiInsightsFacadeCoordinator]:
        """Create a facade coordinator."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        coordinator = UnifiInsightsFacadeCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
            config_coordinator=config_coordinator,
            device_coordinator=device_coordinator,
            protect_coordinator=protect_coordinator,
        )
        yield coordinator
        # Shut down so refresh timers scheduled by the facade's listeners on
        # the sub-coordinators don't linger past the test.
        await coordinator.async_shutdown()
        await config_coordinator.async_shutdown()
        await device_coordinator.async_shutdown()
        await protect_coordinator.async_shutdown()

    @pytest.fixture
    async def facade_coordinator_no_protect(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        config_coordinator: UnifiInsightsConfigCoordinator,
        device_coordinator: UnifiInsightsDeviceCoordinator,
    ) -> AsyncGenerator[UnifiInsightsFacadeCoordinator]:
        """Create a facade coordinator without protect."""
        network_client = _create_mock_network_client()
        coordinator = UnifiInsightsFacadeCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=None,
            entry=mock_config_entry,
            config_coordinator=config_coordinator,
            device_coordinator=device_coordinator,
            protect_coordinator=None,
        )
        yield coordinator
        await coordinator.async_shutdown()
        await config_coordinator.async_shutdown()
        await device_coordinator.async_shutdown()

    def test_initialization(self, facade_coordinator: UnifiInsightsFacadeCoordinator):
        """Test facade coordinator initialization."""
        assert facade_coordinator.name == f"{DOMAIN}_facade"

    def test_aggregate_without_protect_has_security_device_keys(
        self, facade_coordinator_no_protect: UnifiInsightsFacadeCoordinator
    ):
        """The empty Protect fallback carries every collection entities index."""
        facade_coordinator_no_protect._aggregate_data()

        protect = facade_coordinator_no_protect.data["protect"]
        for collection in ("fobs", "link_stations", "alarm_hubs", "sirens"):
            assert protect[collection] == {}

    def test_aggregate_data(self, facade_coordinator: UnifiInsightsFacadeCoordinator):
        """Test data aggregation."""
        facade_coordinator._aggregate_data()

        # Check config coordinator data
        assert "sites" in facade_coordinator.data
        assert "default" in facade_coordinator.data["sites"]
        assert "wifi" in facade_coordinator.data
        assert "firewall_rules" in facade_coordinator.data
        assert "rule1" in facade_coordinator.data["firewall_rules"]["default"]

        # Check device coordinator data
        assert "devices" in facade_coordinator.data
        assert "default" in facade_coordinator.data["devices"]
        assert "clients" in facade_coordinator.data
        assert "stats" in facade_coordinator.data
        assert "vpn_connections" in facade_coordinator.data
        assert "site_vpns" in facade_coordinator.data

        # Check protect coordinator data
        assert "protect" in facade_coordinator.data
        assert "cameras" in facade_coordinator.data["protect"]
        assert "camera1" in facade_coordinator.data["protect"]["cameras"]

        # Check timestamp
        assert "last_update" in facade_coordinator.data

    def test_aggregate_data_no_protect(
        self, facade_coordinator_no_protect: UnifiInsightsFacadeCoordinator
    ):
        """Test data aggregation without protect."""
        facade_coordinator_no_protect._aggregate_data()

        # Protect data should have default empty structure
        assert facade_coordinator_no_protect.data["protect"]["cameras"] == {}
        assert facade_coordinator_no_protect.data["protect"]["lights"] == {}

    def test_empty_protect_stand_in_is_one_read_only_object(
        self, facade_coordinator_no_protect: UnifiInsightsFacadeCoordinator
    ) -> None:
        """Without Protect every rebuild publishes the same read-only stand-in."""
        before = facade_coordinator_no_protect.data["protect"]
        facade_coordinator_no_protect._aggregate_data()

        protect = facade_coordinator_no_protect.data["protect"]
        assert protect is before
        assert protect is EMPTY_PROTECT
        assert protect["cameras"] is before["cameras"]
        with pytest.raises(RuntimeError):
            protect["cameras"]["cam-1"] = {}  # type: ignore[index]

    def test_aggregate_data_includes_rule_changes(
        self,
        facade_coordinator: UnifiInsightsFacadeCoordinator,
        config_coordinator: UnifiInsightsConfigCoordinator,
    ) -> None:
        """Rule change records in config data are included in facade data."""
        config_coordinator.data["rule_changes"] = {"site1": [{"sequence": 1}]}
        facade_coordinator._aggregate_data()
        assert facade_coordinator.data["rule_changes"] == {"site1": [{"sequence": 1}]}

    def test_lookups_read_the_aggregated_snapshot(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """The snapshot lookups read the owners' records through the facade."""
        facade_coordinator._aggregate_data()
        data = facade_coordinator.data

        assert site_record(data, "default") == {"id": "default", "name": "Default"}
        assert network_device(data, "default", "device1") == {"id": "device1"}
        assert network_device(data, "default", "nonexistent") is None
        assert network_device_stats(data, "default", "device1") == {"cpu": 10}
        assert network_device_stats(data, "default", "nonexistent") is None

    def test_available_all_success(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """Test available property when all coordinators succeed."""
        # Replace coordinators with mocks that have last_update_success attribute
        mock_config = MagicMock()
        mock_config.last_update_success = True
        mock_device = MagicMock()
        mock_device.last_update_success = True
        mock_protect = MagicMock()
        mock_protect.last_update_success = True

        facade_coordinator._config_coordinator = mock_config
        facade_coordinator._device_coordinator = mock_device
        facade_coordinator._protect_coordinator = mock_protect

        assert facade_coordinator.available is True

    def test_available_config_fails_decoupled(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """Test available property when config coordinator fails."""
        mock_config = MagicMock()
        mock_config.last_update_success = False
        mock_device = MagicMock()
        mock_device.last_update_success = True
        mock_protect = MagicMock()
        mock_protect.last_update_success = True

        facade_coordinator._config_coordinator = mock_config
        facade_coordinator._device_coordinator = mock_device
        facade_coordinator._protect_coordinator = mock_protect

        assert facade_coordinator.available is False
        assert facade_coordinator.config_available is False
        assert facade_coordinator.device_available is True
        assert facade_coordinator.protect_available is True

    def test_available_device_fails_decoupled(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """Test available property when device coordinator fails."""
        mock_config = MagicMock()
        mock_config.last_update_success = True
        mock_device = MagicMock()
        mock_device.last_update_success = False
        mock_protect = MagicMock()
        mock_protect.last_update_success = True

        facade_coordinator._config_coordinator = mock_config
        facade_coordinator._device_coordinator = mock_device
        facade_coordinator._protect_coordinator = mock_protect

        assert facade_coordinator.available is False
        assert facade_coordinator.device_available is False
        assert facade_coordinator.config_available is True
        assert facade_coordinator.protect_available is True

    def test_available_protect_fails_decoupled(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """Test available property when protect coordinator fails."""
        mock_config = MagicMock()
        mock_config.last_update_success = True
        mock_device = MagicMock()
        mock_device.last_update_success = True
        mock_protect = MagicMock()
        mock_protect.last_update_success = False

        facade_coordinator._config_coordinator = mock_config
        facade_coordinator._device_coordinator = mock_device
        facade_coordinator._protect_coordinator = mock_protect

        assert facade_coordinator.available is False
        assert facade_coordinator.protect_available is False
        assert facade_coordinator.device_available is True
        assert facade_coordinator.config_available is True

    def test_available_all_coordinators_fail(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """Test available property when all coordinators fail."""
        mock_config = MagicMock()
        mock_config.last_update_success = False
        mock_device = MagicMock()
        mock_device.last_update_success = False
        mock_protect = MagicMock()
        mock_protect.last_update_success = False

        facade_coordinator._config_coordinator = mock_config
        facade_coordinator._device_coordinator = mock_device
        facade_coordinator._protect_coordinator = mock_protect

        assert facade_coordinator.available is False
        assert facade_coordinator.device_available is False
        assert facade_coordinator.config_available is False
        assert facade_coordinator.protect_available is False

    def test_available_no_protect(
        self, facade_coordinator_no_protect: UnifiInsightsFacadeCoordinator
    ):
        """Test available property without protect coordinator."""
        mock_config = MagicMock()
        mock_config.last_update_success = True
        mock_device = MagicMock()
        mock_device.last_update_success = True

        facade_coordinator_no_protect._config_coordinator = mock_config
        facade_coordinator_no_protect._device_coordinator = mock_device
        # protect_coordinator is None by default for this fixture

        assert facade_coordinator_no_protect.available is True
        assert facade_coordinator_no_protect.protect_available is True

    @pytest.mark.asyncio
    async def test_async_update_data(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """Test async update data."""
        result = await facade_coordinator._async_update_data()

        assert "sites" in result
        assert "devices" in result
        assert "protect" in result

    @pytest.mark.asyncio
    async def test_async_request_refresh(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """
        Test async_request_refresh forces a genuine refresh on each
        sub-coordinator (async_refresh), not the debounced
        async_request_refresh which can return before any fetch happens.
        """
        facade_coordinator._config_coordinator.async_refresh = AsyncMock()
        facade_coordinator._device_coordinator.async_refresh = AsyncMock()
        require_not_none(
            facade_coordinator._protect_coordinator
        ).async_refresh = AsyncMock()

        await facade_coordinator.async_request_refresh()

        facade_coordinator._config_coordinator.async_refresh.assert_called_once()
        facade_coordinator._device_coordinator.async_refresh.assert_called_once()
        require_mock(
            require_not_none(facade_coordinator._protect_coordinator).async_refresh
        ).assert_called_once()

    @pytest.mark.asyncio
    async def test_async_refresh_or_raise_is_quiet_when_children_succeed(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """A clean refresh raises nothing and still notifies listeners."""
        listener_calls = 0

        def listener() -> None:
            nonlocal listener_calls
            listener_calls += 1

        facade_coordinator.async_add_listener(listener)
        for child in (
            facade_coordinator._config_coordinator,
            facade_coordinator._device_coordinator,
            facade_coordinator._protect_coordinator,
        ):
            require_not_none(child).async_refresh = AsyncMock()
            require_not_none(child).last_update_success = True

        await facade_coordinator.async_refresh_or_raise()

        assert listener_calls >= 1

    @pytest.mark.asyncio
    async def test_async_refresh_or_raise_reports_recorded_failure(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """
        A child that failed is reported even though nothing was raised.

        ``DataUpdateCoordinator.async_refresh()`` swallows the error and only
        records ``last_update_success`` / ``last_exception``, so awaiting it
        tells the caller nothing. Without this the service reported success.
        """
        for child in (
            facade_coordinator._config_coordinator,
            facade_coordinator._device_coordinator,
            facade_coordinator._protect_coordinator,
        ):
            require_not_none(child).async_refresh = AsyncMock()
            require_not_none(child).last_update_success = True

        facade_coordinator._device_coordinator.last_update_success = False
        facade_coordinator._device_coordinator.last_exception = OSError("console down")

        with pytest.raises(HomeAssistantError) as err:
            await facade_coordinator.async_refresh_or_raise()

        assert "devices coordinator" in str(err.value)
        assert "console down" in str(err.value)

    @pytest.mark.asyncio
    async def test_async_refresh_or_raise_reports_escaped_exception(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """An exception that escapes a child refresh is reported, not lost."""
        for child in (
            facade_coordinator._config_coordinator,
            facade_coordinator._device_coordinator,
            facade_coordinator._protect_coordinator,
        ):
            require_not_none(child).async_refresh = AsyncMock()
            require_not_none(child).last_update_success = True

        require_not_none(
            facade_coordinator._protect_coordinator
        ).async_refresh = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(HomeAssistantError) as err:
            await facade_coordinator.async_refresh_or_raise()

        assert "protect coordinator" in str(err.value)
        assert "boom" in str(err.value)

    @pytest.mark.asyncio
    async def test_async_refresh_or_raise_propagates_cancellation(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """Cancellation is teardown, not a console failure - it must propagate.

        ``CancelledError`` is a ``BaseException``; an ``isinstance(x, Exception)``
        check would miss it and report the refresh as a success.
        """
        for child in (
            facade_coordinator._config_coordinator,
            facade_coordinator._device_coordinator,
            facade_coordinator._protect_coordinator,
        ):
            require_not_none(child).async_refresh = AsyncMock()
            require_not_none(child).last_update_success = True

        facade_coordinator._device_coordinator.async_refresh = AsyncMock(
            side_effect=asyncio.CancelledError
        )

        with pytest.raises(asyncio.CancelledError):
            await facade_coordinator.async_refresh_or_raise()

    @pytest.mark.asyncio
    async def test_async_refresh_or_raise_can_skip_protect(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """A site-scoped refresh is a Network concern and skips Protect."""
        for child in (
            facade_coordinator._config_coordinator,
            facade_coordinator._device_coordinator,
            facade_coordinator._protect_coordinator,
        ):
            require_not_none(child).async_refresh = AsyncMock()
            require_not_none(child).last_update_success = True

        await facade_coordinator.async_refresh_or_raise(include_protect=False)

        require_mock(
            facade_coordinator._config_coordinator.async_refresh
        ).assert_called_once()
        require_mock(
            facade_coordinator._device_coordinator.async_refresh
        ).assert_called_once()
        require_mock(
            require_not_none(facade_coordinator._protect_coordinator).async_refresh
        ).assert_not_called()

    @pytest.mark.asyncio
    async def test_async_refresh_or_raise_without_protect_console(
        self, facade_coordinator_no_protect: UnifiInsightsFacadeCoordinator
    ):
        """A Network-only console refreshes cleanly with no Protect child."""
        for child in (
            facade_coordinator_no_protect._config_coordinator,
            facade_coordinator_no_protect._device_coordinator,
        ):
            child.async_refresh = AsyncMock()
            child.last_update_success = True

        await facade_coordinator_no_protect.async_refresh_or_raise()

        require_mock(
            facade_coordinator_no_protect._config_coordinator.async_refresh
        ).assert_called_once()

    @pytest.mark.asyncio
    async def test_async_request_refresh_no_protect(
        self, facade_coordinator_no_protect: UnifiInsightsFacadeCoordinator
    ):
        """Test async request refresh without protect."""
        facade_coordinator_no_protect._config_coordinator.async_refresh = AsyncMock()
        facade_coordinator_no_protect._device_coordinator.async_refresh = AsyncMock()

        await facade_coordinator_no_protect.async_request_refresh()

        facade_coordinator_no_protect._config_coordinator.async_refresh.assert_called_once()
        facade_coordinator_no_protect._device_coordinator.async_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_request_refresh_waits_for_fresh_data_and_notifies(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """
        Regression test for the async_request_refresh contract violation.

        Callers (e.g. switch.py action handlers) await
        ``coordinator.async_request_refresh()`` expecting two things: (1)
        the aggregated data reflects a fetch that actually happened during
        the call, and (2) their own listener gets notified. The previous
        implementation awaited each sub-coordinator's *debounced*
        ``async_request_refresh()``, which can return before any fetch
        starts (see ``Debouncer`` cooldown), and never called
        ``async_update_listeners()`` itself - so both guarantees could be
        silently violated.

        This test drives a real (mocked) async_refresh that mutates the
        sub-coordinator's data, then asserts the facade's aggregated data
        reflects it and that a registered listener was actually called.
        """
        listener_calls = 0

        def listener() -> None:
            nonlocal listener_calls
            listener_calls += 1

        facade_coordinator.async_add_listener(listener)

        async def _fake_refresh() -> None:
            facade_coordinator._config_coordinator.data["sites"] = {
                "new-site": {"id": "new-site"}
            }

        facade_coordinator._config_coordinator.async_refresh = AsyncMock(
            side_effect=_fake_refresh
        )
        facade_coordinator._device_coordinator.async_refresh = AsyncMock()
        require_not_none(
            facade_coordinator._protect_coordinator
        ).async_refresh = AsyncMock()
        # The debounced path must not be used for this explicit,
        # user-triggered refresh - assert it's never touched.
        facade_coordinator._config_coordinator.async_request_refresh = AsyncMock()
        facade_coordinator._device_coordinator.async_request_refresh = AsyncMock()
        require_not_none(
            facade_coordinator._protect_coordinator
        ).async_request_refresh = AsyncMock()

        await facade_coordinator.async_request_refresh()

        facade_coordinator._config_coordinator.async_request_refresh.assert_not_called()
        facade_coordinator._device_coordinator.async_request_refresh.assert_not_called()
        require_mock(
            require_not_none(
                facade_coordinator._protect_coordinator
            ).async_request_refresh
        ).assert_not_called()

        # The aggregated data must reflect what the (mocked) genuine
        # refresh produced, proving the call actually waited for it.
        assert facade_coordinator.data["sites"] == {"new-site": {"id": "new-site"}}

        # Listeners (entities) must be notified so they can reflect the
        # refreshed state without waiting for the next natural poll.
        assert listener_calls >= 1

    def test_handle_coordinator_update(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """Test handling coordinator updates."""
        # Track if listeners were called
        listener_called = False

        def listener():
            nonlocal listener_called
            listener_called = True

        facade_coordinator.async_add_listener(listener)
        facade_coordinator._handle_coordinator_update()

        assert listener_called

    @pytest.mark.asyncio
    async def test_async_get_historical_clients_verified_site(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """Test async_get_historical_clients uses verified site mapping."""
        facade_coordinator._config_coordinator.data = {
            "sites": {"site1": {"id": "site1", "internalReference": "branch"}}
        }
        facade_coordinator.network_client.clients.get_historical_legacy = AsyncMock(
            return_value=[{"mac": "aa:bb"}]
        )
        result = await facade_coordinator.async_get_historical_clients("site1")
        assert result == [{"mac": "aa:bb"}]
        facade_coordinator.network_client.clients.get_historical_legacy.assert_called_once_with(
            "branch"
        )

    @pytest.mark.asyncio
    async def test_async_get_historical_clients_unmapped_site_raises(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """Unmapped site raises HomeAssistantError and does NOT fall back to default."""
        facade_coordinator._config_coordinator.data = {"sites": {}}
        facade_coordinator.network_client.clients.get_historical_legacy = AsyncMock()
        with pytest.raises(
            HomeAssistantError, match="No verified classic site name found"
        ) as excinfo:
            await facade_coordinator.async_get_historical_clients("site1")
        assert excinfo.value.translation_key == "legacy_site_not_found"
        assert excinfo.value.translation_placeholders == {"site_id": "site1"}
        facade_coordinator.network_client.clients.get_historical_legacy.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_get_historical_clients_api_error_wrapped(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """API error in async_get_historical_clients is wrapped."""
        facade_coordinator._config_coordinator.data = {
            "sites": {"site1": {"id": "site1", "internalReference": "branch"}}
        }
        facade_coordinator.network_client.clients.get_historical_legacy = AsyncMock(
            side_effect=RuntimeError("connection dropped")
        )
        with pytest.raises(
            HomeAssistantError,
            match="Unable to read historical clients for site site1",
        ):
            await facade_coordinator.async_get_historical_clients("site1")

    def test_facade_network_usability_accessors(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """Test network_available and is_network_usable accessors on facade."""
        assert facade_coordinator.network_available is True
        assert facade_coordinator.is_network_usable is True

        facade_coordinator._config_coordinator._network_available = False
        assert facade_coordinator._config_coordinator.network_available is False
        assert facade_coordinator.network_available is False
        assert facade_coordinator.is_network_usable is False

        facade_coordinator._config_coordinator._network_available = True
        facade_coordinator.network_client = None  # type: ignore[assignment]
        assert facade_coordinator.is_network_usable is False

    @pytest.mark.asyncio
    async def test_async_shutdown_releases_sub_coordinator_listeners(
        self,
        facade_coordinator: UnifiInsightsFacadeCoordinator,
        config_coordinator: UnifiInsightsConfigCoordinator,
        device_coordinator: UnifiInsightsDeviceCoordinator,
        protect_coordinator: UnifiInsightsProtectCoordinator,
    ):
        """
        Regression test for the sub-coordinator listener leak.

        ``_setup_listeners`` registers the facade as a listener on each
        sub-coordinator via ``async_add_listener``, which returns a
        remove-callback. Discarding that callback (the previous behavior)
        means nothing ever undoes the registration: on config-entry
        reload, a fresh facade is built and a fresh listener piles onto
        the sub-coordinators, while the outgoing facade instance and its
        listener registration are never released. Shutting the facade
        down must release exactly what it registered, returning each
        sub-coordinator's listener count to its pre-facade baseline.
        """
        # Baseline: __init__ -> _setup_listeners already registered
        # exactly one listener (this facade) on each sub-coordinator.
        assert len(config_coordinator._listeners) == 1
        assert len(device_coordinator._listeners) == 1
        assert len(protect_coordinator._listeners) == 1

        await facade_coordinator.async_shutdown()

        assert len(config_coordinator._listeners) == 0
        assert len(device_coordinator._listeners) == 0
        assert len(protect_coordinator._listeners) == 0

    async def test_async_shutdown_is_idempotent(
        self,
        facade_coordinator: UnifiInsightsFacadeCoordinator,
    ):
        """
        Shutting the facade down twice must be safe.

        ``DataUpdateCoordinator.__init__`` registers
        ``entry.async_on_unload(self.async_shutdown)``, so HA invokes
        shutdown on its own at unload -- which can coincide with an
        explicit call from our unload path. The second pass must not
        raise and must not re-invoke the already-spent remove-callbacks
        (which would unregister listeners belonging to a *newer* facade
        built by the reload that followed).
        """
        # Stand in for the real remove-callbacks so a second release is
        # observable. Listener-count behavior is covered by
        # test_async_shutdown_releases_sub_coordinator_listeners.
        unsubs = [MagicMock() for _ in range(3)]
        facade_coordinator._sub_coordinator_unsubs = list(unsubs)

        await facade_coordinator.async_shutdown()
        for unsub in unsubs:
            assert unsub.call_count == 1

        # Second shutdown: must not raise, and must not re-invoke the
        # already-spent callbacks.
        await facade_coordinator.async_shutdown()
        for unsub in unsubs:
            assert unsub.call_count == 1

        assert facade_coordinator._sub_coordinator_unsubs == []

    def test_aggregate_data_includes_rule_sections(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ) -> None:
        """Test aggregate data includes port_forwards and traffic_rules."""
        facade_coordinator._config_coordinator.data["port_forwards"] = {
            "site1": {"pf1": {}}
        }
        facade_coordinator._config_coordinator.data["traffic_rules"] = {
            "site1": {"tr1": {}}
        }
        facade_coordinator._aggregate_data()
        assert "port_forwards" in facade_coordinator.data
        assert "traffic_rules" in facade_coordinator.data
        assert facade_coordinator.data["port_forwards"] == {"site1": {"pf1": {}}}
        assert facade_coordinator.data["traffic_rules"] == {"site1": {"tr1": {}}}

    def test_aggregate_data_includes_nat_and_static_route_sections(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ) -> None:
        """Test aggregated data includes NAT and static route sections."""
        facade_coordinator._config_coordinator.data["nat_rules"] = {
            "site1": {"nat1": {}}
        }
        facade_coordinator._config_coordinator.data["static_routes"] = {
            "site1": {"sr1": {}}
        }
        facade_coordinator._aggregate_data()
        assert "nat_rules" in facade_coordinator.data
        assert "static_routes" in facade_coordinator.data
        assert facade_coordinator.data["nat_rules"] == {"site1": {"nat1": {}}}
        assert facade_coordinator.data["static_routes"] == {"site1": {"sr1": {}}}

    @pytest.mark.asyncio
    async def test_async_export_network_rules_passes_console_and_sites(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ) -> None:
        """The facade hands its client, sites and console identity to the builder."""
        sites = {"s": {"id": "s", "internalReference": "default"}}
        facade_coordinator._config_coordinator.data = {"sites": sites}
        document = {"format_version": 1, "sites": {}}

        with patch(
            "custom_components.unifi_insights.coordinators.facade."
            "async_build_rule_export",
            AsyncMock(return_value=document),
        ) as build:
            result = await facade_coordinator.async_export_network_rules(
                ["s"], ["nat_rule"]
            )

        assert result is document
        build.assert_awaited_once()
        assert build.await_args is not None
        assert build.await_args.args == (facade_coordinator.network_client, sites)
        entry = facade_coordinator.config_entry
        assert build.await_args.kwargs == {
            "console": {"entry_id": entry.entry_id, "title": entry.title},
            "site_ids": ["s"],
            "rule_types": ["nat_rule"],
            "is_unsupported": (
                facade_coordinator._config_coordinator._is_unsupported_response
            ),
        }

    @pytest.mark.asyncio
    async def test_async_export_network_rules_without_a_config_entry(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator, monkeypatch
    ) -> None:
        """Without a config entry the console identity is exported as unknown."""
        facade_coordinator._config_coordinator.data = {"sites": {}}
        patch_attribute(monkeypatch, facade_coordinator, "config_entry", None)

        with patch(
            "custom_components.unifi_insights.coordinators.facade."
            "async_build_rule_export",
            AsyncMock(return_value={}),
        ) as build:
            await facade_coordinator.async_export_network_rules()

        assert build.await_args is not None
        assert build.await_args.kwargs["console"] == {"entry_id": None, "title": None}


class TestCoordinatorDataFlow:
    """Integration tests for coordinator data flow."""

    @pytest.mark.asyncio
    async def test_full_data_flow(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ):
        """Test complete data flow from API to facade coordinator."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()

        # Create coordinators
        config_coord = UnifiInsightsConfigCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )

        device_coord = UnifiInsightsDeviceCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
            config_coordinator=config_coord,
        )

        protect_coord = UnifiInsightsProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )

        facade_coord = UnifiInsightsFacadeCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
            config_coordinator=config_coord,
            device_coordinator=device_coord,
            protect_coordinator=protect_coord,
        )

        # Fetch data through coordinators
        await config_coord._async_update_data()
        await device_coord._async_update_data()
        await protect_coord._async_update_data()
        facade_coord._aggregate_data()

        # Verify data is properly aggregated
        assert "default" in facade_coord.data["sites"]
        assert "default" in facade_coord.data["devices"]
        assert "camera1" in facade_coord.data["protect"]["cameras"]

        # Shut down so scheduled refresh timers don't linger past the test
        await facade_coord.async_shutdown()
        await config_coord.async_shutdown()
        await device_coord.async_shutdown()
        await protect_coord.async_shutdown()


# --- Pure snapshot builder (ADR-013) ------------------------------------------

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
AVAILABLE = capture_availability(
    device=True,
    config=True,
    protect=True,
    innerspace=True,
    network=True,
    failed_sections=frozenset(),
    site_ids=(),
)


def test_build_facade_data_passes_the_owners_sections_through() -> None:
    """The builder copies no section and publishes a read-only root."""
    config = {
        "sites": {"site1": {"id": "site1"}},
        "latest_vouchers": {"site1": {"id": "v1"}},
        "internet_activity_unavailable": {"site1"},
    }
    facts = {"site1": build_site_network_facts({}, {})}
    device = {
        "devices": {"site1": {}},
        "stats": {"site1": {}},
        NETWORK_FACTS_KEY: facts,
    }
    protect = {"cameras": {"cam1": {"id": "cam1"}}}
    innerspace = {"devices": {}}

    snapshot = build_facade_data(
        config,
        device,
        protect,
        innerspace,
        availability=AVAILABLE,
        site_manager=None,
        site_manager_host_id=None,
        now=NOW,
    )

    assert type(snapshot) is ReadOnlyDict
    for key in ("sites", "latest_vouchers", "internet_activity_unavailable"):
        assert snapshot[key] is config[key], key
    assert snapshot["devices"] is device["devices"]
    assert snapshot["stats"] is device["stats"]
    assert snapshot[NETWORK_FACTS_KEY] is facts
    assert snapshot["protect"] is protect
    assert snapshot["innerspace"] is innerspace
    assert snapshot["last_update"] == NOW
    assert snapshot["availability"] == AVAILABLE
    assert snapshot["firewall_rules"] == {}
    assert snapshot["clients"] == {}
    assert "site_manager" not in snapshot
    assert set(snapshot) == (
        FacadeData.__required_keys__ | FacadeData.__optional_keys__
    ) - {"site_manager"}
    with pytest.raises(RuntimeError):
        snapshot["sites"] = {}  # type: ignore[typeddict-readonly-mutated]


def test_build_facade_data_without_protect_uses_the_stand_in() -> None:
    """A console without Protect gets the shared empty Protect data."""
    snapshot = build_facade_data(
        {},
        {},
        None,
        {},
        availability=AVAILABLE,
        site_manager=None,
        site_manager_host_id=None,
        now=NOW,
    )

    assert snapshot["protect"] is EMPTY_PROTECT
    assert snapshot["internet_activity_unavailable"] == set()
    assert snapshot[NETWORK_FACTS_KEY] is None


@pytest.mark.parametrize(
    ("host_id", "selected"),
    [("host-1", "host-1"), ("host-9", None), (None, None)],
)
def test_build_facade_data_selects_the_entry_site_manager_host(
    host_id: str | None, selected: str | None
) -> None:
    """The Site Manager section names this entry's console only when listed."""
    account = {"hosts": {"host-1": {"id": "host-1"}}}

    snapshot = build_facade_data(
        {},
        {},
        None,
        {},
        availability=AVAILABLE,
        site_manager=account,
        site_manager_host_id=host_id,
        now=NOW,
    )

    assert snapshot["site_manager"] == {**account, "selected_host_id": selected}
    assert "selected_host_id" not in account


# --- Captured availability (ADR-013) ------------------------------------------

# The config coordinator method that answers each site section live.
SECTION_METHODS = {
    "wifi": "wifi_available",
    "firewall_rules": "firewall_available",
    "vouchers": "vouchers_available",
    "port_forwards": "port_forwards_available",
    "traffic_rules": "traffic_rules_available",
    "nat_rules": "nat_rules_available",
    "static_routes": "static_routes_available",
    "internet_activity": "internet_activity_available",
}


def test_section_methods_cover_every_site_section() -> None:
    """Every captured site section has a live config coordinator method."""
    assert set(SECTION_METHODS) == set(SITE_SECTIONS)


@pytest.mark.parametrize("config", [True, False])
def test_capture_availability_per_site(*, config: bool) -> None:
    """A failed section is unavailable for its site only, and all with config."""
    availability = capture_availability(
        device=True,
        config=config,
        protect=False,
        innerspace=True,
        network=True,
        failed_sections=frozenset({("wifi", "site1"), ("vouchers", "gone")}),
        site_ids=("site1", "site2"),
    )

    assert availability["config"] is config
    assert availability["protect"] is False
    assert set(availability["sites"]) == {"site1", "site2", "gone"}
    for site_id, failed in (("site1", "wifi"), ("site2", None), ("gone", "vouchers")):
        for section in SITE_SECTIONS:
            expected = config and section != failed
            assert availability["sites"][site_id][section] is expected, (
                site_id,
                section,
            )


def _assert_captured_matches_live(facade: UnifiInsightsFacadeCoordinator) -> None:
    """Assert the snapshot's availability lookups answer what the owners do."""
    data = facade.data
    assert owner_available(data, "device") is facade.device_available
    assert owner_available(data, "config") is facade.config_available
    assert owner_available(data, "protect") is facade.protect_available
    assert owner_available(data, "innerspace") is facade.innerspace_available
    assert owner_available(data, "network") is facade.network_available
    for site_id in (*data["sites"], "unknown-site"):
        for section, method in SECTION_METHODS.items():
            live = getattr(facade.config_coordinator, method)(site_id)
            assert site_section_available(data, site_id, section) is live, (
                site_id,
                section,
            )


async def _fail_wifi(owners: SeededOwners) -> None:
    """Refresh the config with the site's WiFi fetch failing."""
    owners.network_client.wifi.get_all.side_effect = UniFiConnectionError("down")
    await owners.config.async_refresh()


async def _fail_config(owners: SeededOwners) -> None:
    """Fail the whole config refresh."""
    owners.network_client.sites.get_all.side_effect = UniFiConnectionError("down")
    await owners.config.async_refresh()


async def _fail_vouchers(owners: SeededOwners) -> None:
    """Fail the targeted voucher refresh of one site."""
    owners.network_client.vouchers.get_all_pages.side_effect = UniFiConnectionError(
        "down"
    )
    await owners.config.async_refresh_vouchers(SITE_ID)


async def _recover_vouchers(owners: SeededOwners) -> None:
    """Fail the targeted voucher refresh, then refresh it successfully."""
    await _fail_vouchers(owners)
    owners.network_client.vouchers.get_all_pages.side_effect = None
    await owners.config.async_refresh_vouchers(SITE_ID)


async def _fail_device(owners: SeededOwners) -> None:
    """Fail the device refresh."""
    owners.network_client.devices.get_all.side_effect = UniFiConnectionError("down")
    await owners.device.async_refresh()


async def _fail_protect(owners: SeededOwners) -> None:
    """Record a failed Protect update, as a lost Protect session does."""
    owners.protect.async_set_update_error(UpdateFailed("down"))


async def _refresh_all(owners: SeededOwners) -> None:
    """Refresh every owner successfully."""
    await owners.facade.async_request_refresh()


@pytest.mark.parametrize(
    ("update", "changed"),
    [
        pytest.param(_refresh_all, None, id="refresh_all"),
        pytest.param(_fail_wifi, ("sites", SITE_ID, "wifi"), id="wifi_failed"),
        pytest.param(_fail_config, ("config",), id="config_failed"),
        pytest.param(_fail_vouchers, ("sites", SITE_ID, "vouchers"), id="vouchers"),
        pytest.param(_recover_vouchers, None, id="vouchers_recovered"),
        pytest.param(_fail_device, ("device",), id="device_failed"),
        pytest.param(_fail_protect, ("protect",), id="protect_failed"),
    ],
)
async def test_captured_availability_follows_each_owner_update(
    seeded_owners: SeededOwners,
    update: Callable[[SeededOwners], Awaitable[None]],
    changed: tuple[str, ...] | None,
) -> None:
    """After each owner update the snapshot carries the live availability."""
    facade = seeded_owners.facade
    _assert_captured_matches_live(facade)

    await update(seeded_owners)

    _assert_captured_matches_live(facade)
    if changed is not None:
        value: Any = facade.data["availability"]
        for key in changed:
            value = value[key]
        assert value is False


async def test_unchanged_availability_is_one_object(
    seeded_owners: SeededOwners,
) -> None:
    """A rebuild with unchanged availability publishes the same capture."""
    facade = seeded_owners.facade
    before = facade.data["availability"]

    seeded_owners.protect.async_update_listeners()
    assert facade.data["availability"] is before

    await _fail_device(seeded_owners)
    assert facade.data["availability"] is not before
    assert facade.data["availability"]["device"] is False


async def test_captured_availability_without_protect_or_innerspace(
    seeded_owners: SeededOwners,
) -> None:
    """A console without Protect or InnerSpace captures both as available."""
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
        _assert_captured_matches_live(facade)
        assert facade.data["availability"]["protect"] is True
        assert facade.data["availability"]["innerspace"] is True
    finally:
        await facade.async_shutdown()


# --- Test snapshot builders (tests/fixtures/facade_data.py) -------------------


def test_facade_data_builds_every_snapshot_key() -> None:
    """The builder's snapshot has exactly the keys FacadeData declares."""
    assert (
        FacadeData.__required_keys__ | FacadeData.__optional_keys__ == FACADE_DATA_KEYS
    )
    assert set(facade_data()) == FACADE_DATA_KEYS - {"site_manager"}
    assert set(facade_data(site_manager={"hosts": {}})) == FACADE_DATA_KEYS


def test_facade_data_defaults_are_empty_and_available() -> None:
    """Every section defaults to empty and every domain to available."""
    data = facade_data(site_ids=["site1"], failed_sections=[("wifi", "site1")])

    assert data["devices"] == {}
    assert data["protect"] == EMPTY_PROTECT
    assert data[NETWORK_FACTS_KEY] is None
    assert data["innerspace"]["devices"] == {}
    assert data["availability"]["device"] is True
    assert data["availability"]["sites"]["site1"]["wifi"] is False
    assert data["availability"]["sites"]["site1"]["vouchers"] is True


def test_facade_data_rejects_unknown_sections() -> None:
    """A misspelled section fails instead of being silently added."""
    with pytest.raises(TypeError, match="device_list"):
        facade_data(device_list={})


def test_facade_data_is_frozen_all_the_way_down() -> None:
    """Writes into the root, a section, a record or a list all raise."""
    data = facade_data(devices={"site1": {"dev1": {"id": "dev1", "ports": [1]}}})
    record = data["devices"]["site1"]["dev1"]

    for write in (
        lambda: require_instance(data, ReadOnlyDict).__setitem__("devices", {}),
        lambda: require_instance(data["devices"], ReadOnlyDict).__setitem__(
            "site2", {}
        ),
        lambda: record.__setitem__("name", "x"),
        lambda: record["ports"].append(2),
        lambda: record["ports"].__setitem__(0, 2),
    ):
        with pytest.raises(RuntimeError):
            write()


def test_frozen_data_serializes_and_redacts_like_the_original() -> None:
    """Frozen data still serializes to JSON, redacts and copies."""
    original = {
        "clients": {"site1": {"c1": {"name": "Phone", "tags": ["a", {"k": "v"}]}}},
        "unavailable": {"site1"},
        "pair": ("x", ["y"]),
    }
    value = frozen(original)

    assert value == {
        **original,
        "unavailable": frozenset({"site1"}),
        "pair": ("x", ["y"]),
    }
    assert isinstance(value["clients"]["site1"]["c1"]["tags"], FrozenList)
    assert isinstance(value["pair"][1], FrozenList)
    json_safe = {"clients": value["clients"]}
    assert json.loads(json.dumps(json_safe)) == {"clients": original["clients"]}
    assert async_redact_data(json_safe, {"name"}) == async_redact_data(
        {"clients": original["clients"]}, {"name"}
    )
    tags = value["clients"]["site1"]["c1"]["tags"]
    assert copy.copy(tags) == tags
    assert isinstance(copy.deepcopy(tags), FrozenList)


def test_with_builders_publish_one_record_copy_on_write() -> None:
    """Each builder returns a new snapshot; the rest keep their identity."""
    base = facade_data()
    device = {"id": "dev1", "macAddress": "02:00:00:00:00:01", "state": "ONLINE"}

    cases = [
        (
            with_network_device(base, "site1", "dev1", device, stats={"cpu": 1}),
            lambda d: network_device(d, "site1", "dev1"),
            {"devices", "stats"},
        ),
        (
            with_client(base, "site1", "c1", {"id": "c1"}),
            lambda d: network_client(d, "site1", "c1"),
            {"clients"},
        ),
        (
            with_rule(base, "firewall_rules", "site1", "r1", {"id": "r1"}),
            lambda d: rule_record(d, "firewall_rules", "site1", "r1"),
            {"firewall_rules"},
        ),
        (
            with_protect_device(base, "chimes", "ch1", {"id": "ch1"}),
            lambda d: protect_device(d, "chimes", "ch1"),
            {"protect"},
        ),
        (
            with_camera(base, "cam1", {"id": "cam1"}),
            lambda d: protect_device(d, "cameras", "cam1"),
            {"protect"},
        ),
        (
            with_latest_voucher(base, "site1", {"id": "v1"}),
            lambda d: latest_voucher(d, "site1"),
            {"latest_vouchers"},
        ),
    ]
    for data, lookup, changed in cases:
        assert type(data) is ReadOnlyDict
        assert lookup(data) is not None
        assert lookup(base) is None
        for key in FACADE_DATA_KEYS - {"site_manager"} - changed:
            assert data[key] is base[key], key
        with pytest.raises(RuntimeError):
            lookup(data)["id"] = "x"  # type: ignore[index]
    assert network_device_stats(cases[0][0], "site1", "dev1") == {"cpu": 1}


def test_with_builders_keep_the_other_records() -> None:
    """Adding a record keeps the collection's and the site's other records."""
    data = with_camera(with_camera(facade_data(), "cam1", {"id": "cam1"}), "cam2", {})
    data = with_client(with_client(data, "s1", "c1", {}), "s2", "c2", {})

    assert set(data["protect"]["cameras"]) == {"cam1", "cam2"}
    assert set(data["protect"]["chimes"]) == set()
    assert set(data["clients"]) == {"s1", "s2"}


def test_with_network_device_republishes_published_facts() -> None:
    """Published network facts follow the device; unpublished stay None."""
    device = {"id": "gw", "macAddress": "02:00:00:00:00:01", "state": "ONLINE"}
    unpublished = with_network_device(facade_data(), "site1", "gw", device)
    assert unpublished[NETWORK_FACTS_KEY] is None

    published = facade_data(network_facts={"site0": build_site_network_facts({}, {})})
    data = with_network_device(published, "site1", "gw", device)

    assert set(require_not_none(data[NETWORK_FACTS_KEY])) == {"site0", "site1"}
    facts = device_facts(data, "site1", "gw")
    assert facts is not None
    assert facts.online_state == "online"


def test_with_device_stats_publishes_any_stats_value() -> None:
    """Stats may be replaced by a record or a malformed value, copy-on-write."""
    device = {"id": "gw", "macAddress": "02:00:00:00:00:01", "state": "ONLINE"}
    base = with_network_device(
        facade_data(), "site1", "gw", device, stats={"cpu": 1, "uptime": 5}
    )

    for stats in ({"cpu": 2}, None, "not-a-dict"):
        data = with_device_stats(base, "site1", "gw", stats)
        assert data["stats"]["site1"]["gw"] == stats
        assert data["devices"] is base["devices"]
    assert base["stats"]["site1"]["gw"] == {"cpu": 1, "uptime": 5}
    with pytest.raises(RuntimeError):
        with_device_stats(base, "site1", "gw", {"cpu": 2})["stats"]["site1"]["gw"][
            "cpu"
        ] = 3


def test_with_device_fields_replace_only_the_given_fields() -> None:
    """The field builders merge into the current record, or start from empty."""
    device = {"id": "gw", "macAddress": "02:00:00:00:00:01", "state": "ONLINE"}
    base = with_network_device(facade_data(), "site1", "gw", device, stats={"cpu": 1})

    data = with_device_stats_fields(
        with_device_fields(base, "site1", "gw", state="OFFLINE", ports=[]),
        "site1",
        "gw",
        uptime=5,
    )

    assert data["devices"]["site1"]["gw"] == {**device, "state": "OFFLINE", "ports": []}
    assert data["stats"]["site1"]["gw"] == {"cpu": 1, "uptime": 5}
    assert base["devices"]["site1"]["gw"] == device
    added = with_device_stats_fields(
        with_device_fields(base, "site2", "sw", name="Switch"), "site2", "sw", cpu=3
    )
    assert added["devices"]["site2"]["sw"] == {"name": "Switch"}
    assert added["stats"]["site2"]["sw"] == {"cpu": 3}


def test_with_device_stats_republishes_published_facts() -> None:
    """Published network facts follow a stats change; unpublished stay None."""
    device = {"id": "gw", "macAddress": "02:00:00:00:00:01", "state": "ONLINE"}
    unpublished = with_device_stats(facade_data(), "site1", "gw", {"cpu": 5})
    assert unpublished[NETWORK_FACTS_KEY] is None

    published = with_network_device(
        facade_data(**{NETWORK_FACTS_KEY: {}}), "site1", "gw", device
    )
    data = with_device_stats_fields(published, "site1", "gw", cpu=42.0)

    facts = device_facts(data, "site1", "gw")
    assert facts is not None
    assert facts.cpu_pct == 42.0


def test_with_protect_fields_replace_only_the_given_fields() -> None:
    """The Protect field builder merges into the current record, or starts empty."""
    camera = {"id": "cam1", "name": "Front", "isMicEnabled": True}
    base = with_camera(facade_data(), "cam1", camera)

    data = with_protect_fields(base, "cameras", "cam1", isMicEnabled=False)

    assert data["protect"]["cameras"]["cam1"] == {**camera, "isMicEnabled": False}
    assert base["protect"]["cameras"]["cam1"] == camera
    added = with_protect_fields(base, "lights", "light1", ledLevel=3)
    assert added["protect"]["lights"]["light1"] == {"ledLevel": 3}
    with pytest.raises(RuntimeError):
        data["protect"]["cameras"]["cam1"]["name"] = "Back"


def test_with_protect_collection_replaces_one_collection() -> None:
    """A whole Protect collection is replaced, frozen, leaving the others."""
    base = with_camera(facade_data(), "cam1", {"id": "cam1"})

    data = with_protect_collection(base, "chimes", {"chime1": {"id": "chime1"}})

    assert data["protect"]["chimes"] == {"chime1": {"id": "chime1"}}
    assert data["protect"]["cameras"] is base["protect"]["cameras"]
    assert base["protect"]["chimes"] == {}
    with pytest.raises(RuntimeError):
        data["protect"]["chimes"]["chime1"]["id"] = "other"
    assert with_protect_collection(base, "cameras", None)["protect"]["cameras"] is None
    bare = with_protect_collection({}, "sensors", {})
    assert bare["protect"] == {"sensors": {}}


def test_frozen_keeps_frozen_values_and_copies_the_rest() -> None:
    """A value frozen all the way down is kept; a writable part is copied."""
    value = frozen({"a": {"b": [1, {"c": 2}]}, "s": {"x"}, "t": ("y", [3])})

    assert frozen(value) is value
    assert frozen(value["s"]) is value["s"]
    assert frozen(value["t"]) is value["t"]

    mixed = ReadOnlyDict({"kept": value["a"], "plain": {"d": [4]}})
    refrozen = frozen(mixed)
    assert refrozen is not mixed
    assert refrozen["kept"] is value["a"]
    assert refrozen == {"kept": value["a"], "plain": {"d": [4]}}
    with pytest.raises(RuntimeError):
        refrozen["plain"]["d"].append(5)
    partly = FrozenList([value["a"], {"e": 5}])
    assert frozen(partly) is not partly
    assert frozen(partly)[0] is value["a"]
    assert frozen(("z", {"f": 6}))[1] == {"f": 6}


def test_with_sections_freezes_the_sections_it_replaces() -> None:
    """Replaced sections are frozen; frozen ones keep their identity."""
    base = facade_data()
    clients = frozen({"site1": {"c1": {"id": "c1"}}})

    data = with_sections(
        base, devices={"site1": {"d1": {"ports": [1]}}}, clients=clients
    )

    assert data["clients"] is clients
    assert data["stats"] is base["stats"]
    with pytest.raises(RuntimeError):
        data["devices"]["site1"]["d1"]["ports"].append(2)
    with pytest.raises(RuntimeError):
        require_instance(data["devices"]["site1"], ReadOnlyDict)["d2"] = {}
