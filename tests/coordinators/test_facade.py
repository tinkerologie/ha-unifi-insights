# Copyright 2026 UniFi Insights contributors
"""Tests for the facade's translated command errors and owner accessors (ADR-010)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
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
from custom_components.unifi_insights.coordinators.protect import (
    UnifiInsightsProtectCoordinator,
)
from custom_components.unifi_insights.data_transforms import (
    NETWORK_FACTS_KEY,
    build_site_network_facts,
)
from tests.fixtures.coordinator_clients import (
    RULE_SECTION_PARAMS,
    _create_mock_network_client,
    _create_mock_protect_client,
)
from tests.fixtures.library_responses import (
    SAMPLE_UDM_PRO_OVERVIEW,
    SAMPLE_UDM_PRO_STATS,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

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
    config_coord.vouchers_available = MagicMock(return_value=True)

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


class TestFacadeBuildInnerspaceCorrKey:
    """Tests for _build_innerspace_corr_key."""

    def test_build_innerspace_corr_key_full(self) -> None:
        """Test building correlation key with devices and protect data."""
        raw_innerspace = {"raw": 1}
        devices = {
            "site1": {
                "dev1": {"macAddress": "00:11:22:33:44:55"},
                "dev2": {"mac_address": "00:11:22:33:44:66"},
                "dev3": {"mac": "00:11:22:33:44:77"},
                "dev_invalid": "not_a_dict",
            },
            "site2": "not_a_dict",
        }
        protect_data = {
            "cameras": {"cam1": {"mac": "11:22:33:44:55:66"}},
            "lights": {"light1": {"macAddress": "22:33:44:55:66:77"}},
            "sensors": {"sens1": {"mac_address": "33:44:55:66:77:88"}},
            "nvrs": {"nvr1": {"mac": "44:55:66:77:88:99"}},
            "doorlocks": {"lock1": {"mac": "55:66:77:88:99:AA"}},
            "viewports": {"vp1": {"mac": "66:77:88:99:AA:BB"}},
        }
        key = UnifiInsightsFacadeCoordinator._build_innerspace_corr_key(
            raw_innerspace, devices, protect_data
        )
        assert isinstance(key, tuple)
        assert len(key) == 3
        assert key[0] == id(raw_innerspace)
        assert ("site1", "dev1", "00:11:22:33:44:55") in key[1]
        assert ("cameras", "cam1", "11:22:33:44:55:66") in key[2]

    def test_build_innerspace_corr_key_non_dict_inputs(self) -> None:
        """Test building correlation key with non-dict devices and protect data."""
        raw_innerspace = {"raw": 2}
        key = UnifiInsightsFacadeCoordinator._build_innerspace_corr_key(
            raw_innerspace, "not_dict", None
        )
        assert key == (id(raw_innerspace), (), ())


class TestFacadeInnerSpaceCaching:
    """Tests for InnerSpace data aggregation caching."""

    def test_aggregate_data_innerspace_cache_hit(
        self, facade: UnifiInsightsFacadeCoordinator
    ) -> None:
        """Test that repeated _aggregate_data calls reuse cached innerspace data."""
        facade._aggregate_data()
        assert facade._cached_innerspace_data is not None
        cached_before = facade._cached_innerspace_data

        facade._aggregate_data()
        assert facade.data["innerspace"] is cached_before


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

    def test_vouchers_available_delegates_to_config_coordinator(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """vouchers_available delegates to config_coordinator.vouchers_available."""
        config_coord = mock_sub_coordinators[0]
        config_coord.vouchers_available.return_value = True
        assert facade.vouchers_available("site1") is True
        config_coord.vouchers_available.assert_called_with("site1")

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


def test_build_innerspace_corr_key_skips_non_dict_devices() -> None:
    """Test that _build_innerspace_corr_key handles non-dict entries defensively."""
    protect_data = {
        "cameras": {
            "cam_bad": None,
            "cam_good": {"mac": "11:22:33:44:55:66"},
        }
    }
    key = UnifiInsightsFacadeCoordinator._build_innerspace_corr_key(
        {}, {}, protect_data
    )
    assert len(key[2]) == 1
    assert key[2][0] == ("cameras", "cam_good", "11:22:33:44:55:66")


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

    def test_aggregate_data_includes_rule_changes(
        self,
        facade_coordinator: UnifiInsightsFacadeCoordinator,
        config_coordinator: UnifiInsightsConfigCoordinator,
    ) -> None:
        """Rule change records in config data are included in facade data."""
        config_coordinator.data["rule_changes"] = {"site1": [{"sequence": 1}]}
        facade_coordinator._aggregate_data()
        assert facade_coordinator.data["rule_changes"] == {"site1": [{"sequence": 1}]}

    def test_get_site(self, facade_coordinator: UnifiInsightsFacadeCoordinator):
        """Test get_site delegation to config coordinator."""
        facade_coordinator._aggregate_data()
        result = facade_coordinator.get_site("default")
        assert result == {"id": "default", "name": "Default"}

    def test_get_device(self, facade_coordinator: UnifiInsightsFacadeCoordinator):
        """Test get_device."""
        facade_coordinator._aggregate_data()
        result = facade_coordinator.get_device("default", "device1")
        assert result == {"id": "device1"}

    def test_get_device_missing(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """Test get_device for missing device."""
        facade_coordinator._aggregate_data()
        result = facade_coordinator.get_device("default", "nonexistent")
        assert result is None

    def test_get_device_stats(self, facade_coordinator: UnifiInsightsFacadeCoordinator):
        """Test get_device_stats."""
        facade_coordinator._aggregate_data()
        result = facade_coordinator.get_device_stats("default", "device1")
        assert result == {"cpu": 10}

    def test_get_device_stats_missing(
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ):
        """Test get_device_stats for missing device."""
        facade_coordinator._aggregate_data()
        result = facade_coordinator.get_device_stats("default", "nonexistent")
        assert result is None

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
        facade_coordinator._protect_coordinator.async_refresh = AsyncMock()

        await facade_coordinator.async_request_refresh()

        facade_coordinator._config_coordinator.async_refresh.assert_called_once()
        facade_coordinator._device_coordinator.async_refresh.assert_called_once()
        facade_coordinator._protect_coordinator.async_refresh.assert_called_once()

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
            child.async_refresh = AsyncMock()
            child.last_update_success = True

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
            child.async_refresh = AsyncMock()
            child.last_update_success = True

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
            child.async_refresh = AsyncMock()
            child.last_update_success = True

        facade_coordinator._protect_coordinator.async_refresh = AsyncMock(
            side_effect=RuntimeError("boom")
        )

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
            child.async_refresh = AsyncMock()
            child.last_update_success = True

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
            child.async_refresh = AsyncMock()
            child.last_update_success = True

        await facade_coordinator.async_refresh_or_raise(include_protect=False)

        facade_coordinator._config_coordinator.async_refresh.assert_called_once()
        facade_coordinator._device_coordinator.async_refresh.assert_called_once()
        facade_coordinator._protect_coordinator.async_refresh.assert_not_called()

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

        facade_coordinator_no_protect._config_coordinator.async_refresh.assert_called_once()

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
        facade_coordinator._protect_coordinator.async_refresh = AsyncMock()
        # The debounced path must not be used for this explicit,
        # user-triggered refresh - assert it's never touched.
        facade_coordinator._config_coordinator.async_request_refresh = AsyncMock()
        facade_coordinator._device_coordinator.async_request_refresh = AsyncMock()
        facade_coordinator._protect_coordinator.async_request_refresh = AsyncMock()

        await facade_coordinator.async_request_refresh()

        facade_coordinator._config_coordinator.async_request_refresh.assert_not_called()
        facade_coordinator._device_coordinator.async_request_refresh.assert_not_called()
        facade_coordinator._protect_coordinator.async_request_refresh.assert_not_called()

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

    @pytest.mark.parametrize(
        ("data_key", "endpoint_attr", "list_method", "avail_method"),
        RULE_SECTION_PARAMS,
    )
    def test_rule_section_availability_passthrough(
        self,
        facade_coordinator: UnifiInsightsFacadeCoordinator,
        data_key: str,
        endpoint_attr: str,
        list_method: str,
        avail_method: str,
    ) -> None:
        """Test facade availability methods pass through to config coordinator."""
        setattr(
            facade_coordinator._config_coordinator,
            avail_method,
            MagicMock(return_value=True),
        )
        avail_fn = getattr(facade_coordinator, avail_method)
        assert avail_fn("site1") is True
        getattr(
            facade_coordinator._config_coordinator, avail_method
        ).assert_called_once_with("site1")

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
        self, facade_coordinator: UnifiInsightsFacadeCoordinator
    ) -> None:
        """Without a config entry the console identity is exported as unknown."""
        facade_coordinator._config_coordinator.data = {"sites": {}}
        facade_coordinator.config_entry = None

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
