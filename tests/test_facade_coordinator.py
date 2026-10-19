# Copyright (c) 2026 Ruaan Deysel
"""Tests for UniFi Insights facade coordinator."""

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from pydantic import ValidationError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
)
from unifi_official_api.network.endpoints.vouchers import (
    VouchersEndpoint,
)
from unifi_official_api.network.models.voucher import Voucher

from custom_components.unifi_insights.coordinators.facade import (
    UnifiInsightsFacadeCoordinator,
)
from custom_components.unifi_insights.data_transforms import (
    NETWORK_FACTS_KEY,
    VoucherSettings,
    build_site_network_facts,
)
from tests.fixtures.library_responses import (
    SAMPLE_UDM_PRO_OVERVIEW,
    SAMPLE_UDM_PRO_STATS,
)


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


class TestFacadeGenerateVoucher:
    """Tests for async_generate_voucher."""

    async def test_generate_voucher_with_rate_limits(
        self, facade: UnifiInsightsFacadeCoordinator
    ) -> None:
        """Test async_generate_voucher with rate limits and usage limits."""
        await facade.async_generate_voucher(
            site_id="site1",
            name="VIP Guest",
            time_limit_minutes=120,
            count=2,
            tx_rate_limit_kbps=1000,
            rx_rate_limit_kbps=2000,
            data_usage_limit_mbytes=500,
        )
        facade.network_client.vouchers.create.assert_awaited_once_with(
            "site1",
            name="VIP Guest",
            time_limit_minutes=120,
            count=2,
            tx_rate_limit_kbps=1000,
            rx_rate_limit_kbps=2000,
            data_usage_limit_mbytes=500,
        )

    async def test_generate_voucher_without_limits(
        self, facade: UnifiInsightsFacadeCoordinator
    ) -> None:
        """Test async_generate_voucher without optional limits."""
        await facade.async_generate_voucher(
            site_id="site1",
            name="Standard Guest",
            time_limit_minutes=60,
            count=1,
        )
        facade.network_client.vouchers.create.assert_awaited_once_with(
            "site1",
            name="Standard Guest",
            time_limit_minutes=60,
            count=1,
        )

    async def test_generate_voucher_passes_authorized_guest_limit(
        self, facade: UnifiInsightsFacadeCoordinator
    ) -> None:
        """Test async_generate_voucher passes authorized_guest_limit to create."""
        await facade.async_generate_voucher(
            site_id="site1",
            name="Guest",
            time_limit_minutes=60,
            count=1,
            authorized_guest_limit=2,
        )
        facade.network_client.vouchers.create.assert_awaited_once_with(
            "site1",
            name="Guest",
            time_limit_minutes=60,
            count=1,
            authorized_guest_limit=2,
        )

    async def test_generate_voucher_returns_created_vouchers_and_records_latest(
        self, facade: UnifiInsightsFacadeCoordinator
    ) -> None:
        """Test async_generate_voucher returns created vouchers and records latest."""
        v1 = Voucher.model_validate({"id": "v1", "code": "1111111111", "name": "G1"})
        facade.network_client.vouchers.create = AsyncMock(return_value=[v1])
        result1 = await facade.async_generate_voucher(
            site_id="site1", name="G1", time_limit_minutes=60
        )
        assert result1 == [v1]
        assert facade.data["latest_vouchers"]["site1"]["id"] == "v1"

        v2 = Voucher.model_validate({"id": "v2", "code": "2222222222", "name": "G2"})
        facade.network_client.vouchers.create = AsyncMock(return_value=[v2])
        result2 = await facade.async_generate_voucher(
            site_id="site1", name="G2", time_limit_minutes=60
        )
        assert result2 == [v2]
        assert facade.data["latest_vouchers"]["site1"]["id"] == "v2"

    async def test_generate_voucher_records_last_voucher_of_a_batch(
        self, facade: UnifiInsightsFacadeCoordinator
    ) -> None:
        """When multiple vouchers are created, the last one is recorded as latest."""
        v1 = Voucher.model_validate({"id": "v1", "code": "1111111111", "name": "Batch"})
        v2 = Voucher.model_validate({"id": "v2", "code": "2222222222", "name": "Batch"})
        facade.network_client.vouchers.create = AsyncMock(return_value=[v1, v2])
        await facade.async_generate_voucher(
            site_id="site1", name="Batch", time_limit_minutes=60, count=2
        )
        assert facade.data["latest_vouchers"]["site1"]["id"] == "v2"

    async def test_generate_voucher_with_unusable_result_records_nothing(
        self, facade: UnifiInsightsFacadeCoordinator
    ) -> None:
        """Unusable result (MagicMock) does not record latest voucher."""
        facade.network_client.vouchers.create = AsyncMock(return_value=MagicMock())
        result = await facade.async_generate_voucher(
            site_id="site1", name="Unusable", time_limit_minutes=60
        )
        assert result == []
        assert facade.data["latest_vouchers"] == {}

    async def test_generate_voucher_refreshes_that_sites_inventory(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Creating a voucher triggers targeted refresh for that site."""
        config_coord = mock_sub_coordinators[0]
        v = Voucher.model_validate({"id": "v1", "code": "1111111111", "name": "G1"})
        facade.network_client.vouchers.create = AsyncMock(return_value=[v])
        await facade.async_generate_voucher(
            site_id="site1", name="G1", time_limit_minutes=60
        )
        config_coord.async_refresh_vouchers.assert_awaited_once_with("site1")

    async def test_generate_voucher_survives_refresh_failure(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Targeted refresh failure still succeeds without voucher code in log."""
        config_coord = mock_sub_coordinators[0]
        config_coord.async_refresh_vouchers = AsyncMock(
            side_effect=RuntimeError("Refresh error")
        )
        v = Voucher.model_validate({"id": "v1", "code": "1111111111", "name": "G1"})
        facade.network_client.vouchers.create = AsyncMock(return_value=[v])
        with caplog.at_level("WARNING"):
            result = await facade.async_generate_voucher(
                site_id="site1", name="G1", time_limit_minutes=60
            )
        assert result == [v]
        assert facade.data["latest_vouchers"]["site1"]["id"] == "v1"
        assert "Unable to refresh the vouchers of site site1" in caplog.text
        assert "1111111111" not in caplog.text

    async def test_generate_voucher_validation_error_does_not_leak_code_in_logs(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Pydantic validation error does not leak code in logs."""
        synthetic_code = "1234567890"
        try:
            Voucher.model_validate({"code": synthetic_code})
        except ValidationError as validation_error:
            real_validation_error = validation_error

        facade.network_client.vouchers.create = AsyncMock(
            side_effect=real_validation_error
        )
        with (
            caplog.at_level("DEBUG"),
            pytest.raises(HomeAssistantError),
        ):
            await facade.async_generate_voucher(
                site_id="site1", name="G1", time_limit_minutes=60
            )

        assert synthetic_code not in caplog.text

    async def test_generate_voucher_real_endpoint_validation_error_does_not_leak_code(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Real endpoint validation failure does not leak code in logs."""
        synthetic_code = "1234567890"

        class _TestClient:
            def build_api_path(self, path: str) -> str:
                return path

            async def _post(
                self,
                path: str,
                json_data: Any = None,
                *,
                log_body: bool = True,
            ) -> Any:
                assert path.endswith("/hotspot/vouchers")
                assert json_data == {
                    "count": 1,
                    "name": "G1",
                    "timeLimitMinutes": 60,
                }
                assert log_body is False
                return {"vouchers": [{"code": synthetic_code}]}

        facade.network_client.vouchers = VouchersEndpoint(_TestClient())  # type: ignore[assignment]
        with (
            caplog.at_level("DEBUG"),
            pytest.raises(HomeAssistantError),
        ):
            await facade.async_generate_voucher(
                site_id="site1", name="G1", time_limit_minutes=60
            )

        assert synthetic_code not in caplog.text
        assert "Invalid voucher data (fields: id)" in caplog.text

    async def test_refresh_vouchers_auth_failure_starts_reauth_and_notifies(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """A targeted voucher refresh auth failure starts reauth and notifies."""
        config_coord = mock_sub_coordinators[0]
        config_coord.async_refresh_vouchers.side_effect = UniFiAuthenticationError(
            "Revoked", status_code=401
        )
        listener = MagicMock()
        facade.async_add_listener(listener)

        with patch.object(facade.config_entry, "async_start_reauth") as start_reauth:
            await facade._async_refresh_vouchers("site1")

        start_reauth.assert_called_once_with(facade.hass)
        listener.assert_called_once()

    async def test_refresh_vouchers_expected_failure_notifies(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Expected targeted voucher refresh errors keep the action successful."""
        config_coord = mock_sub_coordinators[0]
        config_coord.async_refresh_vouchers.side_effect = UniFiConnectionError("Down")
        listener = MagicMock()
        facade.async_add_listener(listener)

        await facade._async_refresh_vouchers("site1")

        listener.assert_called_once()

    async def test_refresh_vouchers_unexpected_failure_propagates(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Unexpected targeted voucher refresh errors are not hidden."""
        config_coord = mock_sub_coordinators[0]
        error = TypeError("broken implementation")
        config_coord.async_refresh_vouchers.side_effect = error

        with pytest.raises(TypeError) as exc_info:
            await facade._async_refresh_vouchers("site1")

        assert exc_info.value is error

    async def test_generate_voucher_api_error_raises_and_skips_refresh(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """API failure raises HomeAssistantError and does not call refresh."""
        config_coord = mock_sub_coordinators[0]
        facade.network_client.vouchers.create = AsyncMock(
            side_effect=RuntimeError("Network failure")
        )
        with pytest.raises(HomeAssistantError, match="Unable to generate voucher"):
            await facade.async_generate_voucher(
                site_id="site1", name="G1", time_limit_minutes=60
            )
        config_coord.async_refresh_vouchers.assert_not_called()

    async def test_delete_voucher_refreshes_and_clears_matching_latest(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Deleting matching voucher clears latest and refreshes inventory."""
        config_coord = mock_sub_coordinators[0]
        facade._latest_vouchers["site1"] = {"id": "v1", "code": "1111111111"}
        facade.network_client.vouchers.delete = AsyncMock(return_value=True)
        await facade.async_delete_voucher("site1", "v1")
        assert "site1" not in facade._latest_vouchers
        config_coord.async_refresh_vouchers.assert_awaited_once_with("site1")

    async def test_delete_voucher_keeps_latest_of_other_voucher(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Deleting another voucher leaves latest intact and refreshes inventory."""
        config_coord = mock_sub_coordinators[0]
        facade._latest_vouchers["site1"] = {"id": "v2", "code": "2222222222"}
        facade.network_client.vouchers.delete = AsyncMock(return_value=True)
        await facade.async_delete_voucher("site1", "v1")
        assert facade._latest_vouchers["site1"]["id"] == "v2"
        config_coord.async_refresh_vouchers.assert_awaited_once_with("site1")

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

    async def test_latest_voucher_is_refreshed_from_inventory(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Latest voucher updates attributes when refreshed from inventory."""
        config_coord = mock_sub_coordinators[0]
        facade._latest_vouchers["site1"] = {"id": "v1", "code": "1111111111"}
        config_coord.data["vouchers"] = {
            "site1": {
                "v1": {
                    "id": "v1",
                    "code": "1111111111",
                    "expired": True,
                    "activatedAt": "2026-10-09T12:00:00Z",
                }
            }
        }
        facade._aggregate_data()
        assert facade.data["latest_vouchers"]["site1"]["expired"] is True
        assert (
            facade.data["latest_vouchers"]["site1"]["activatedAt"]
            == "2026-10-09T12:00:00Z"
        )

    async def test_latest_voucher_is_kept_when_missing_from_inventory(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """Latest voucher is kept in memory even if missing from polled inventory."""
        config_coord = mock_sub_coordinators[0]
        facade._latest_vouchers["site1"] = {"id": "v1", "code": "1111111111"}
        config_coord.data["vouchers"] = {"site1": {}}
        facade._aggregate_data()
        assert facade.data["latest_vouchers"]["site1"]["id"] == "v1"

    async def test_latest_voucher_is_kept_when_inventory_is_not_a_mapping(
        self,
        facade: UnifiInsightsFacadeCoordinator,
        mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    ) -> None:
        """A malformed voucher inventory leaves the latest voucher untouched."""
        config_coord = mock_sub_coordinators[0]
        latest = {"id": "v1", "code": "1111111111"}
        facade._latest_vouchers["site1"] = dict(latest)
        config_coord.data["vouchers"] = None
        facade._aggregate_data()
        assert facade.data["latest_vouchers"]["site1"] == latest

    def test_get_voucher_settings_creates_defaults_once_per_site(
        self, facade: UnifiInsightsFacadeCoordinator
    ) -> None:
        """get_voucher_settings returns existing or initializes default settings."""
        s1 = facade.get_voucher_settings("site1")
        assert isinstance(s1, VoucherSettings)
        assert s1.duration_minutes == 480
        s2 = facade.get_voucher_settings("site1")
        assert s1 is s2

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
