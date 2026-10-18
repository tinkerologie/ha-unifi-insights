# Copyright 2026 UniFi Insights contributors
"""Tests for multi-coordinator architecture (Platinum compliance)."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import aiohttp
import pytest
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_VERIFY_SSL
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import UpdateFailed
from pydantic import ValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_insights.api import (
    ApiKeyAuth,
    ConnectionType,
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiGlobalAlarmManagerError,
    UniFiNotFoundError,
    UniFiRateLimitError,
    UniFiResponseError,
    UniFiRuleNotEditableError,
    UniFiTimeoutError,
)
from custom_components.unifi_insights.api.innerspace import (
    InnerSpaceAccessPoint,
    InnerSpaceFloorPlan,
    InnerSpaceInventoryDevice,
    InnerSpaceProject,
    InnerSpaceProjectIdentity,
    InnerSpaceSwitch,
    UniFiInnerSpaceClient,
)
from custom_components.unifi_insights.api.network.endpoints.vouchers import (
    VouchersEndpoint,
)
from custom_components.unifi_insights.api.network.models import (
    LegacyPortMetrics,
    PortBytesMetrics,
    SiteReportBucket,
)
from custom_components.unifi_insights.api.protect import UniFiProtectClient
from custom_components.unifi_insights.api.protect.endpoints.chimes import ChimesEndpoint
from custom_components.unifi_insights.api.protect.models import (
    Camera,
    Chime,
    Fob,
    Light,
    LinkStation,
    LiveView,
    Sensor,
    Siren,
    TalkbackSession,
    Viewer,
)
from custom_components.unifi_insights.api.protect.talkback import (
    TalkbackSchemeError,
    TalkbackStreamError,
)
from custom_components.unifi_insights.const import (
    CONF_CONNECTION_TYPE,
    CONF_SITE_IDS,
    CONNECTION_TYPE_LOCAL,
    DOMAIN,
    SCAN_INTERVAL_CONFIG,
    SCAN_INTERVAL_DEVICE,
    SCAN_INTERVAL_PROTECT,
)
from custom_components.unifi_insights.coordinators import (
    UnifiInsightsInnerSpaceCoordinator,
)
from custom_components.unifi_insights.coordinators.base import UnifiBaseCoordinator
from custom_components.unifi_insights.coordinators.config import (
    WINDOW_1D_MS,
    WINDOW_1H_MS,
    WINDOW_1M_MS,
    WINDOW_1W_MS,
    UnifiConfigCoordinator,
    aggregate_internet_activity_windows,
)
from custom_components.unifi_insights.coordinators.config_sections import (
    async_fetch_site_routes,
    async_fetch_site_wifi_and_links,
    enrich_wifi,
    is_predefined_nat_rule,
    map_legacy_site_names,
    resolve_report_site_name,
    rule_display_name,
    verified_legacy_site_name,
    wifi_qr_payload,
)
from custom_components.unifi_insights.coordinators.device import (
    MAX_STATS_REUSE_POLLS,
    UnifiDeviceCoordinator,
)
from custom_components.unifi_insights.coordinators.facade import UnifiFacadeCoordinator
from custom_components.unifi_insights.coordinators.internet_activity import (
    async_update_site_internet_activity,
)
from custom_components.unifi_insights.coordinators.protect import (
    MAX_CONSECUTIVE_EMPTY_FETCHES,
    MAX_CONSECUTIVE_MISSING_POLLS,
    SIREN_STOP_GUARD_TTL,
    STALE_EVENT_TIMEOUT,
    UNSUPPORTED_RESOURCE_RETRY,
    UnifiProtectCoordinator,
)
from custom_components.unifi_insights.entity import is_device_online
from custom_components.unifi_insights.helpers import async_get_device_entry
from tests.conftest import mock_device_lookup_method, set_mock_device_lookup
from tests.fixtures.library_responses import (
    SAMPLE_ALARM_HUB,
    SAMPLE_ALARM_HUB_TAMPER_EVENT,
    SAMPLE_KEYPAD_FOB,
    SAMPLE_SIREN,
    SAMPLE_THREAD_LINK_STATION,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from homeassistant.core import HomeAssistant


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return the default mocked config entry."""
    return MockConfigEntry(
        version=1,
        minor_version=0,
        domain=DOMAIN,
        title="UniFi Insights (Local)",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.1",
            CONF_API_KEY: "test_api_key",
            CONF_VERIFY_SSL: False,
        },
        options={},
        source="user",
        unique_id="test_api_key",
        entry_id="test_entry_id",
    )


def _create_mock_model(data: dict) -> MagicMock:
    """Create a mock pydantic model that returns proper dict from model_dump."""
    mock = MagicMock()
    mock.model_dump = MagicMock(return_value=data)
    # Also set attributes for direct access
    for key, value in data.items():
        setattr(mock, key, value)
    return mock


def _create_mock_network_client() -> MagicMock:
    """Create a mock network client."""
    client = MagicMock()
    client.base_url = "https://192.168.1.1"

    # Sites namespace
    client.sites = MagicMock()
    client.sites.get_all = AsyncMock(
        return_value=[
            _create_mock_model({"id": "default", "name": "Default"}),
            _create_mock_model({"id": "site2", "name": "Site 2"}),
        ]
    )
    client.sites.get_legacy_all = AsyncMock(
        return_value=[
            {"name": "default", "desc": "Default"},
            {"name": "site2", "desc": "Site 2"},
        ]
    )

    # Port forwards namespace
    client.port_forwards = MagicMock()
    client.port_forwards.list_port_forwards = AsyncMock(return_value=[])
    client.port_forwards.update_port_forward = AsyncMock()

    # Traffic rules namespace
    client.traffic_rules = MagicMock()
    client.traffic_rules.list_traffic_rules = AsyncMock(return_value=[])
    client.traffic_rules.update_traffic_rule = AsyncMock()

    # NAT rules namespace
    client.nat_rules = MagicMock()
    client.nat_rules.list_nat_rules = AsyncMock(return_value=[])
    client.nat_rules.update_nat_rule = AsyncMock()

    # Static routes namespace
    client.static_routes = MagicMock()
    client.static_routes.list_static_routes = AsyncMock(return_value=[])
    client.static_routes.update_static_route = AsyncMock()

    # WiFi namespace
    client.wifi = MagicMock()
    client.wifi.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {"id": "wifi1", "name": "MainWiFi", "ssid": "MyNetwork"}
            ),
        ]
    )

    # Vouchers namespace
    client.vouchers = MagicMock()
    client.vouchers.get_all_pages = AsyncMock(return_value=[])

    # Firewall namespace
    client.firewall = MagicMock()
    client.firewall.list_rules = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "rule1",
                    "name": "Block Instagram",
                    "enabled": True,
                    "action": "drop",
                    "protocol": "all",
                }
            )
        ]
    )
    client.firewall.update_rule = AsyncMock()

    # Routes namespace
    client.routes = MagicMock()
    client.routes.list_routes = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "route1",
                    "description": "Route via VPN",
                    "enabled": True,
                    "interface": "vpn",
                }
            )
        ]
    )
    client.routes.update_route = AsyncMock()

    # Devices namespace
    client.devices = MagicMock()
    client.devices.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "device1",
                    "name": "Test Switch",
                    "model": "USW-24",
                    "mac": "AA:BB:CC:DD:EE:FF",
                }
            )
        ]
    )
    client.devices.get_statistics = AsyncMock(
        return_value=_create_mock_model(
            {
                "cpuUtilizationPct": 15.2,
                "memoryUtilizationPct": 42.8,
                "uptimeSec": 864000,
            }
        )
    )
    client.devices.get_legacy_site_devices = AsyncMock(return_value=[])
    client.devices.execute_port_action = AsyncMock(return_value=True)

    # Clients namespace
    client.clients = MagicMock()
    client.clients.get_all = AsyncMock(
        return_value=[
            _create_mock_model({"id": "client1", "name": "iPhone", "type": "WIRELESS"}),
        ]
    )

    client.close = AsyncMock()
    return client


def _create_mock_protect_client() -> MagicMock:
    """Create a mock protect client."""
    client = MagicMock()
    client.base_url = "https://192.168.1.1"

    # Cameras namespace
    client.cameras = MagicMock()
    client.cameras.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "camera1",
                    "name": "Front Door",
                    "state": "CONNECTED",
                    "type": "UVC-G4-DOORBELL",
                    "mac": "11:22:33:44:55:66",
                    "featureFlags": {"smartDetectTypes": ["person", "vehicle"]},
                }
            )
        ]
    )

    # Lights namespace
    client.lights = MagicMock()
    client.lights.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "light1",
                    "name": "Garage Light",
                    "state": "CONNECTED",
                }
            )
        ]
    )

    # Sensors namespace
    client.sensors = MagicMock()
    client.sensors.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "sensor1",
                    "name": "Door Sensor",
                    "state": "CONNECTED",
                }
            )
        ]
    )

    # NVR namespace
    client.nvr = MagicMock()
    client.nvr.get = AsyncMock(
        return_value=_create_mock_model({"id": "nvr1", "name": "NVR", "type": "UNVR"})
    )

    # Chimes namespace
    client.chimes = MagicMock()
    client.chimes.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "chime1",
                    "name": "Door Chime",
                    "state": "CONNECTED",
                }
            )
        ]
    )

    # Viewers namespace
    client.viewers = MagicMock()
    client.viewers.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "viewer1",
                    "name": "Viewport",
                    "state": "CONNECTED",
                }
            )
        ]
    )

    # Liveviews namespace
    client.liveviews = MagicMock()
    client.liveviews.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "liveview1",
                    "name": "Main View",
                    "isDefault": True,
                }
            )
        ]
    )

    # Protect 7.3.70 security device families
    for family in ("fobs", "link_stations", "alarm_hubs", "sirens"):
        endpoint = MagicMock()
        endpoint.get_all = AsyncMock(return_value=[])
        endpoint.last_result_complete = True
        setattr(client, family, endpoint)
    # WebSocket support
    client.get_host_id = AsyncMock(return_value="nvr1")
    client.websocket = MagicMock()
    client.websocket.subscribe_with_callback = AsyncMock()
    client.websocket.stop = MagicMock()

    client.close = AsyncMock()
    return client


# ============================================================================
# UnifiBaseCoordinator Tests
# ============================================================================


class TestUnifiBaseCoordinator:
    """Tests for UnifiBaseCoordinator."""

    @pytest.fixture
    def coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> UnifiBaseCoordinator:
        """Create a base coordinator for testing."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        return UnifiBaseCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
            name="test",
            update_interval=timedelta(seconds=30),
        )

    def test_initialization(self, coordinator: UnifiBaseCoordinator):
        """Test coordinator initialization."""
        assert coordinator.name == f"{DOMAIN}_test"
        assert coordinator.update_interval == timedelta(seconds=30)
        assert coordinator._available is True
        assert coordinator.data == {}

    def test_available_property(self, coordinator: UnifiBaseCoordinator):
        """Test available property."""
        assert coordinator.available is True
        coordinator._available = False
        assert coordinator.available is False

    def test_model_to_dict_with_none(self, coordinator: UnifiBaseCoordinator):
        """Test model_to_dict with None input."""
        result = coordinator._model_to_dict(None)
        assert result == {}

    def test_model_to_dict_with_dict(self, coordinator: UnifiBaseCoordinator):
        """Test model_to_dict with dict input."""
        input_dict = {"key": "value"}
        result = coordinator._model_to_dict(input_dict)
        assert result == input_dict

    def test_model_to_dict_with_pydantic_model(self, coordinator: UnifiBaseCoordinator):
        """Test model_to_dict with pydantic model."""
        mock_model = MagicMock()
        mock_model.model_dump = MagicMock(return_value={"id": "test", "name": "Test"})
        result = coordinator._model_to_dict(mock_model)
        assert result == {"id": "test", "name": "Test"}
        mock_model.model_dump.assert_called_once_with(by_alias=True, exclude_none=False)

    def test_model_to_dict_with_pydantic_model_type_error(
        self, coordinator: UnifiBaseCoordinator
    ):
        """Test model_to_dict fallback when by_alias raises TypeError."""
        mock_model = MagicMock()
        # First call raises TypeError, second call succeeds
        mock_model.model_dump = MagicMock(
            side_effect=[TypeError("by_alias not supported"), {"id": "fallback"}]
        )
        result = coordinator._model_to_dict(mock_model)
        assert result == {"id": "fallback"}

    @pytest.mark.parametrize(
        ("side_effects", "expected_calls"),
        [
            (["not_a_dict"], 1),
            ([TypeError("by_alias not supported"), "not_a_dict"], 2),
        ],
        ids=["direct_non_dict", "type_error_fallback_non_dict"],
    )
    def test_model_to_dict_non_dict_results(
        self,
        coordinator: UnifiBaseCoordinator,
        side_effects: list[Any],
        expected_calls: int,
    ) -> None:
        """Test model_to_dict returns {} when model_dump or
        TypeError fallback returns non-dict.
        """
        mock_model = MagicMock()
        mock_model.model_dump = MagicMock(side_effect=side_effects)
        result = coordinator._model_to_dict(mock_model)
        assert result == {}
        assert mock_model.model_dump.call_count == expected_calls

    def test_model_to_dict_with_object_dict(self, coordinator: UnifiBaseCoordinator):
        """Test model_to_dict with object having __dict__."""

        class SimpleObject:
            def __init__(self):
                self.id = "test"
                self.name = "Test Object"
                self._private = "hidden"

        obj = SimpleObject()
        result = coordinator._model_to_dict(obj)
        assert result == {"id": "test", "name": "Test Object"}
        assert "_private" not in result

    def test_model_to_dict_with_primitive(self, coordinator: UnifiBaseCoordinator):
        """Test model_to_dict with primitive value."""
        result = coordinator._model_to_dict("string")
        assert result == {}

    def test_handle_auth_error(self, coordinator: UnifiBaseCoordinator):
        """Test handling authentication error."""
        err = UniFiAuthenticationError("Invalid credentials")
        with pytest.raises(ConfigEntryAuthFailed, match="Authentication failed"):
            coordinator._handle_auth_error(err)
        assert coordinator._available is False

    def test_handle_connection_error(self, coordinator: UnifiBaseCoordinator):
        """Test handling connection error."""
        err = UniFiConnectionError("Connection refused")
        with pytest.raises(UpdateFailed, match="Error communicating with API"):
            coordinator._handle_connection_error(err)
        assert coordinator._available is False

    def test_handle_timeout_error(self, coordinator: UnifiBaseCoordinator):
        """Test handling timeout error."""
        err = UniFiTimeoutError("Request timed out")
        with pytest.raises(UpdateFailed, match="Timeout"):
            coordinator._handle_timeout_error(err)
        assert coordinator._available is False

    def test_handle_response_error(self, coordinator: UnifiBaseCoordinator):
        """Test handling API response error."""
        err = UniFiResponseError("Bad response", status_code=400)
        with pytest.raises(UpdateFailed, match="API error"):
            coordinator._handle_response_error(err)
        assert coordinator._available is False

    @pytest.mark.parametrize(
        ("handler", "err", "detail"),
        [
            ("_handle_timeout_error", UniFiTimeoutError("timed out"), "timed out"),
            (
                "_handle_response_error",
                UniFiResponseError("gateway", status_code=502),
                "502",
            ),
            (
                "_handle_response_error",
                UniFiResponseError("bad request", status_code=400),
                "400",
            ),
            (
                "_handle_generic_error",
                ValueError("unexpected"),
                "ValueError: unexpected",
            ),
        ],
    )
    def test_handlers_leave_outage_logging_to_coordinator(
        self,
        coordinator: UnifiBaseCoordinator,
        caplog: pytest.LogCaptureFixture,
        handler: str,
        err: Exception,
        detail: str,
    ) -> None:
        """Failures preserve diagnostics without emitting per-poll warnings."""
        caplog.set_level(logging.DEBUG)
        with pytest.raises(UpdateFailed, match=detail) as raised:
            getattr(coordinator, handler)(err)
        assert raised.value.__cause__ is err
        assert coordinator.available is False
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ============================================================================
# UnifiConfigCoordinator Tests
# ============================================================================


_RULE_SECTION_PARAMS = [
    pytest.param(
        "port_forwards",
        "port_forwards",
        "list_port_forwards",
        "port_forwards_available",
        id="port_forward",
    ),
    pytest.param(
        "traffic_rules",
        "traffic_rules",
        "list_traffic_rules",
        "traffic_rules_available",
        id="traffic_rule",
    ),
    pytest.param(
        "nat_rules",
        "nat_rules",
        "list_nat_rules",
        "nat_rules_available",
        id="nat_rule",
    ),
    pytest.param(
        "static_routes",
        "static_routes",
        "list_static_routes",
        "static_routes_available",
        id="static_route",
    ),
]


class TestUnifiConfigCoordinator:
    """Tests for UnifiConfigCoordinator."""

    @pytest.fixture
    def coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> UnifiConfigCoordinator:
        """Create a config coordinator for testing."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        return UnifiConfigCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )

    def test_initialization(self, coordinator: UnifiConfigCoordinator):
        """Test coordinator initialization."""
        assert coordinator.name == f"{DOMAIN}_config"
        assert coordinator.update_interval == SCAN_INTERVAL_CONFIG
        assert "sites" in coordinator.data
        assert "wifi" in coordinator.data
        assert "firewall_rules" in coordinator.data
        assert "policy_based_routes" in coordinator.data
        assert "network_info" in coordinator.data

    @pytest.mark.asyncio
    async def test_async_update_data_success(self, coordinator: UnifiConfigCoordinator):
        """Test successful data fetch."""
        result = await coordinator._async_update_data()

        assert "sites" in result
        assert "default" in result["sites"]
        assert "site2" in result["sites"]
        assert "wifi" in result
        assert "firewall_rules" in result
        assert "rule1" in result["firewall_rules"]["default"]
        assert "policy_based_routes" in result
        assert "route1" in result["policy_based_routes"]["default"]
        assert coordinator._available is True
        assert coordinator.available_sites == {
            "default": "Default",
            "site2": "Site 2",
        }

    @pytest.mark.asyncio
    async def test_async_update_data_filters_selected_sites(self, hass: HomeAssistant):
        """Only the sites picked in options are polled (Issue #128)."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_API_KEY: "test_api_key"},
            options={CONF_SITE_IDS: ["site2"]},
        )
        network_client = _create_mock_network_client()
        coordinator = UnifiConfigCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=None,
            entry=entry,
        )

        result = await coordinator._async_update_data()

        assert list(result["sites"]) == ["site2"]
        assert coordinator.get_site_ids() == ["site2"]
        # Every site stays selectable in the options flow.
        assert coordinator.available_sites == {
            "default": "Default",
            "site2": "Site 2",
        }
        polled = [call.args[0] for call in network_client.wifi.get_all.await_args_list]
        assert polled == ["site2"]

    @pytest.mark.asyncio
    async def test_async_update_data_prunes_per_site_maps(
        self, coordinator: UnifiConfigCoordinator
    ):
        """Per-site config data for a site no longer polled is dropped."""
        for key in ("wifi", "firewall_rules", "policy_based_routes", "vpn_clients"):
            coordinator.data[key]["gone"] = {"stale": {"id": "stale"}}

        result = await coordinator._async_update_data()

        for key in ("wifi", "firewall_rules", "policy_based_routes", "vpn_clients"):
            assert "gone" not in result[key]
            assert "default" in result[key]

    @pytest.mark.asyncio
    async def test_async_update_data_selected_sites_all_gone(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ):
        """A selection matching no current site polls nothing and says why."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_API_KEY: "test_api_key"},
            options={CONF_SITE_IDS: ["removed-site"]},
        )
        coordinator = UnifiConfigCoordinator(
            hass=hass,
            network_client=_create_mock_network_client(),
            protect_client=None,
            entry=entry,
        )

        with caplog.at_level(logging.WARNING):
            result = await coordinator._async_update_data()
            await coordinator._async_update_data()

        assert result["sites"] == {}
        # Warned once, not on every 5-minute poll.
        assert caplog.text.count("none of the selected sites") == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", ["wifi.get_all", "firewall.list_rules"])
    @pytest.mark.parametrize(
        "error",
        [
            UniFiNotFoundError("Not found", status_code=404),
            UniFiResponseError("Bad request", status_code=400),
            UniFiAuthenticationError("Forbidden", status_code=403),
            UniFiResponseError("HTML instead of JSON", status_code=200),
        ],
        ids=["404", "400", "403", "200-non-json"],
    )
    async def test_async_update_data_optional_endpoint_unsupported(
        self, coordinator: UnifiConfigCoordinator, endpoint: str, error: Exception
    ):
        """A 4xx from WiFi/firewall means the feature is absent, not a failure."""
        namespace, method = endpoint.split(".")
        setattr(
            getattr(coordinator.network_client, namespace),
            method,
            AsyncMock(side_effect=error),
        )

        result = await coordinator._async_update_data()

        section = "wifi" if namespace == "wifi" else "firewall_rules"
        assert result[section]["default"] == {}
        assert "default" in result["sites"]
        assert coordinator._available is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("endpoint", "section", "other_section"),
        [
            ("wifi.get_all", "wifi", "firewall_rules"),
            ("firewall.list_rules", "firewall_rules", "wifi"),
        ],
        ids=["wifi", "firewall"],
    )
    @pytest.mark.parametrize(
        "error",
        [
            UniFiResponseError("Server error", status_code=500),
            UniFiRateLimitError("Slow down", status_code=429),
            UniFiConnectionError("Connection refused"),
            UniFiTimeoutError("Timed out"),
            ValueError("Unparseable payload"),
        ],
        ids=["500", "429", "connection", "timeout", "unexpected"],
    )
    async def test_async_update_data_optional_section_failure(
        self,
        coordinator: UnifiConfigCoordinator,
        endpoint: str,
        section: str,
        other_section: str,
        error: Exception,
    ):
        """A failed WiFi/firewall fetch keeps that section's data and flags it."""
        await coordinator.async_refresh()
        assert coordinator.wifi_available("default") is True
        assert coordinator.firewall_available("default") is True
        previous = copy.deepcopy(coordinator.data[section])
        assert previous["default"]

        namespace, method = endpoint.split(".")
        working = getattr(getattr(coordinator.network_client, namespace), method)
        setattr(
            getattr(coordinator.network_client, namespace),
            method,
            AsyncMock(side_effect=error),
        )

        await coordinator.async_refresh()

        # The refresh itself succeeds so other sections and platforms keep
        # working, but the failed section is not reported as fresh.
        assert coordinator.last_update_success is True
        assert coordinator.data[section] == previous
        section_flag = {
            "wifi": coordinator.wifi_available,
            "firewall_rules": coordinator.firewall_available,
        }
        assert section_flag[section]("default") is False
        assert section_flag[other_section]("default") is True

        setattr(getattr(coordinator.network_client, namespace), method, working)
        await coordinator.async_refresh()
        assert section_flag[section]("default") is True

    @pytest.mark.asyncio
    async def test_optional_section_failure_is_per_site(
        self, coordinator: UnifiConfigCoordinator
    ):
        """A WiFi failure on one site leaves other sites' WiFi available."""
        working = coordinator.network_client.wifi.get_all

        async def get_all(site_id: str):
            if site_id == "site2":
                msg = "site2 WiFi down"
                raise UniFiConnectionError(msg)
            return await working(site_id)

        coordinator.network_client.wifi.get_all = AsyncMock(side_effect=get_all)
        await coordinator.async_refresh()

        assert coordinator.wifi_available("default") is True
        assert coordinator.wifi_available("site2") is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", ["wifi.get_all", "firewall.list_rules"])
    async def test_async_update_data_optional_endpoint_auth_failure_reauths(
        self, coordinator: UnifiConfigCoordinator, endpoint: str
    ):
        """A 401 from WiFi/firewall means revoked credentials and starts reauth."""
        namespace, method = endpoint.split(".")
        setattr(
            getattr(coordinator.network_client, namespace),
            method,
            AsyncMock(side_effect=UniFiAuthenticationError("Revoked", status_code=401)),
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

        assert coordinator._available is False

    @pytest.mark.asyncio
    async def test_async_update_data_enriches_wifi_and_collects_sections(
        self, coordinator: UnifiConfigCoordinator
    ):
        """Legacy WiFi enrichment, VPN clients and id-less rules on a good poll."""
        client = coordinator.network_client
        client.wifi.get_legacy_configs = AsyncMock(
            return_value=[
                {"name": "MainWiFi", "x_passphrase": "secret", "security": "wpapsk"}
            ]
        )
        client.clients.get_active_legacy = AsyncMock(
            return_value=[
                {"essid": "MainWiFi"},
                {"essid": "MainWiFi", "is_wired": True},
            ]
        )
        client.vpn_clients.list_vpn_clients = AsyncMock(
            return_value=[{"_id": "vpn1", "name": "Office"}, {"name": "no id"}]
        )
        client.firewall.list_rules = AsyncMock(
            return_value=[_create_mock_model({"name": "rule without id"})]
        )

        result = await coordinator._async_update_data()

        wifi = result["wifi"]["default"]["wifi1"]
        assert wifi["num_connected_clients"] == 1
        assert wifi["qr_code"].startswith("WIFI:T:WPA;S:MainWiFi;P:")
        assert list(result["vpn_clients"]["default"]) == ["vpn1"]
        assert result["firewall_rules"]["default"] == {}
        assert coordinator.firewall_available("default") is True

    @pytest.mark.asyncio
    async def test_async_update_data_drops_sites_that_disappeared(
        self, coordinator: UnifiConfigCoordinator
    ):
        """Per-site sections are rebuilt, so a removed site's data is not kept."""
        coordinator.data["wifi"]["gone"] = {"wifi-x": {"id": "wifi-x"}}
        coordinator.data["firewall_rules"]["gone"] = {"rule-x": {"id": "rule-x"}}

        result = await coordinator._async_update_data()

        assert "gone" not in result["wifi"]
        assert "gone" not in result["firewall_rules"]

    @pytest.mark.asyncio
    async def test_async_update_data_routes_error(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """Test policy-based routes are optional when the endpoint is unavailable."""
        coordinator.network_client.routes.list_routes = AsyncMock(
            side_effect=Exception("Routes endpoint unavailable")
        )

        result = await coordinator._async_update_data()

        assert "sites" in result
        assert "default" in result["sites"]
        assert result["policy_based_routes"]["default"] == {}
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_routes_auth_error(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """Test auth error during routes fetch triggers reauth."""
        coordinator.protect_client = None
        coordinator.network_client.routes.list_routes = AsyncMock(
            side_effect=UniFiAuthenticationError("Invalid API key")
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_vpn_clients_error(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """Test VPN clients are optional when the endpoint is unavailable."""
        coordinator.network_client.vpn_clients.list_vpn_clients = AsyncMock(
            side_effect=Exception("VPN clients endpoint unavailable")
        )

        result = await coordinator._async_update_data()

        assert "sites" in result
        assert "default" in result["sites"]
        assert result["vpn_clients"]["default"] == {}
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_stores_site_to_site_vpns(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """Configured site-to-site tunnels are stored per site by id."""
        tunnel = {
            "id": "tun1",
            "name": "Office",
            "vpn_type": "ipsec-vpn",
            "enabled": True,
        }
        coordinator.network_client.vpn_clients.list_site_to_site_vpns = AsyncMock(
            return_value=[tunnel]
        )

        result = await coordinator._async_update_data()

        assert result["site_vpns"]["default"] == {"tun1": tunnel}

    @pytest.mark.asyncio
    async def test_async_update_data_site_to_site_vpns_error_keeps_last_known(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """A failed tunnel fetch keeps the last known tunnels, not an empty set."""
        tunnel = {
            "id": "tun1",
            "name": "Office",
            "vpn_type": "ipsec-vpn",
            "enabled": True,
        }
        coordinator.network_client.vpn_clients.list_site_to_site_vpns = AsyncMock(
            return_value=[tunnel]
        )
        await coordinator._async_update_data()

        coordinator.network_client.vpn_clients.list_site_to_site_vpns = AsyncMock(
            side_effect=Exception("networkconf unavailable")
        )
        result = await coordinator._async_update_data()

        assert result["site_vpns"]["default"] == {"tun1": tunnel}
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_site_mapping_error_keeps_site_vpns(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """A failed legacy site mapping keeps the last known tunnels."""
        tunnel = {
            "id": "tun1",
            "name": "Office",
            "vpn_type": "ipsec-vpn",
            "enabled": True,
        }
        coordinator.network_client.vpn_clients.list_site_to_site_vpns = AsyncMock(
            return_value=[tunnel]
        )
        await coordinator._async_update_data()

        coordinator.network_client.sites.get_legacy_all = AsyncMock(
            side_effect=Exception("legacy sites unavailable")
        )
        result = await coordinator._async_update_data()

        assert result["site_vpns"]["default"] == {"tun1": tunnel}

    @pytest.mark.asyncio
    async def test_async_update_data_site_to_site_vpns_auth_error(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """An auth error on the tunnel fetch triggers reauth, not last-known."""
        coordinator.network_client.vpn_clients.list_site_to_site_vpns = AsyncMock(
            side_effect=UniFiAuthenticationError("Invalid API key")
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_vpn_clients_auth_error(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """Test auth error during VPN clients fetch triggers reauth."""
        coordinator.protect_client = None
        coordinator.network_client.vpn_clients.list_vpn_clients = AsyncMock(
            side_effect=UniFiAuthenticationError("Invalid API key")
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_auth_error(
        self, coordinator: UnifiConfigCoordinator
    ):
        """Test data fetch with auth error on network-only setup."""
        coordinator.protect_client = None
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiAuthenticationError("Invalid API key")
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_protect_only_console(
        self, coordinator: UnifiConfigCoordinator
    ):
        """Test fetch succeeds on Protect-only console with empty sites (Issue 93)."""
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiNotFoundError("Not found", status_code=404)
        )

        result = await coordinator._async_update_data()
        assert result["sites"] == {}
        assert result["wifi"] == {}
        assert result["firewall_rules"] == {}
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_protect_only_console_non_json_sites(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """Test fetch succeeds when the sites endpoint returns 200 and no JSON."""
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiResponseError(
                "API returned non-JSON response (status 200)", status_code=200
            )
        )

        result = await coordinator._async_update_data()
        assert result["sites"] == {}
        assert result["wifi"] == {}
        assert result["firewall_rules"] == {}
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_non_json_sites_without_protect(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """Test a non-JSON sites response still fails when Protect is absent."""
        coordinator.protect_client = None
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiResponseError(
                "API returned non-JSON response (status 200)", status_code=200
            )
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_redirect_sites_still_fails(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """Test only a 200 is tolerated, so a 3xx is not swallowed."""
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiResponseError("Redirected", status_code=302)
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_server_error_sites_still_fails(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """Test a >=400 response is not swallowed by the non-JSON tolerance."""
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiResponseError("Server error", status_code=500)
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_connection_error(
        self, coordinator: UnifiConfigCoordinator
    ):
        """Test data fetch with connection error."""
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiConnectionError("Connection refused")
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_timeout_error(
        self, coordinator: UnifiConfigCoordinator
    ):
        """Test data fetch with timeout error."""
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiTimeoutError("Request timed out")
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_response_error(
        self, coordinator: UnifiConfigCoordinator
    ):
        """Test data fetch with response error."""
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiResponseError("Bad response", status_code=400)
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_generic_error(
        self, coordinator: UnifiConfigCoordinator
    ):
        """Test data fetch with generic error."""
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=Exception("Something broke")
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    def test_get_site_existing(self, coordinator: UnifiConfigCoordinator):
        """Test getting existing site."""
        coordinator.data["sites"] = {"default": {"id": "default", "name": "Default"}}
        result = coordinator.get_site("default")
        assert result == {"id": "default", "name": "Default"}

    def test_get_site_missing(self, coordinator: UnifiConfigCoordinator):
        """Test getting missing site."""
        result = coordinator.get_site("nonexistent")
        assert result is None

    def test_get_site_non_dict_entry(self, coordinator: UnifiConfigCoordinator) -> None:
        """Test getting site when site entry in data is not a dict."""
        coordinator.data["sites"] = {"site1": "not a dict"}
        assert coordinator.get_site("site1") is None

    def test_get_site_ids(self, coordinator: UnifiConfigCoordinator):
        """Test getting all site IDs."""
        coordinator.data["sites"] = {
            "default": {"id": "default"},
            "site2": {"id": "site2"},
        }
        result = coordinator.get_site_ids()
        assert set(result) == {"default", "site2"}

    def test_get_wifi_networks(self, coordinator: UnifiConfigCoordinator):
        """Test getting WiFi networks for a site."""
        coordinator.data["wifi"] = {
            "default": {"wifi1": {"id": "wifi1", "name": "MainWiFi"}}
        }
        result = coordinator.get_wifi_networks("default")
        assert "wifi1" in result

    def test_get_wifi_networks_missing_site(self, coordinator: UnifiConfigCoordinator):
        """Test getting WiFi networks for missing site."""
        result = coordinator.get_wifi_networks("nonexistent")
        assert result == {}

    def test_get_firewall_rules(self, coordinator: UnifiConfigCoordinator):
        """Test getting firewall rules for a site."""
        coordinator.data["firewall_rules"] = {
            "default": {"rule1": {"id": "rule1", "name": "Block Instagram"}}
        }
        result = coordinator.get_firewall_rules("default")
        assert "rule1" in result

    def test_get_firewall_rules_missing_site(self, coordinator: UnifiConfigCoordinator):
        """Test getting firewall rules for missing site."""
        result = coordinator.get_firewall_rules("nonexistent")
        assert result == {}

    def test_get_policy_based_routes(self, coordinator: UnifiConfigCoordinator):
        """Test getting policy-based routes for a site."""
        coordinator.data["policy_based_routes"] = {
            "default": {"route1": {"id": "route1", "description": "Route to VPN"}}
        }
        result = coordinator.get_policy_based_routes("default")
        assert "route1" in result

    def test_get_policy_based_routes_missing_site(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """Test getting policy-based routes for missing site."""
        result = coordinator.get_policy_based_routes("nonexistent")
        assert result == {}

    def test_get_vpn_clients(self, coordinator: UnifiConfigCoordinator) -> None:
        """Test getting VPN clients for a site."""
        coordinator.data["vpn_clients"] = {
            "default": {"client1": {"_id": "client1", "name": "Privado VPN"}}
        }
        result = coordinator.get_vpn_clients("default")
        assert "client1" in result

    def test_get_vpn_clients_missing_site(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """Test getting VPN clients for missing site."""
        result = coordinator.get_vpn_clients("nonexistent")
        assert result == {}

    @pytest.mark.asyncio
    async def test_async_update_data_skips_none_site_id(
        self, coordinator: UnifiConfigCoordinator
    ):
        """Test that WiFi fetch skips None site IDs."""
        # Create a site model that returns None for id
        site_with_none_id = MagicMock()
        site_with_none_id.model_dump.return_value = {"id": None, "name": "BadSite"}

        site_valid = MagicMock()
        site_valid.model_dump.return_value = {"id": "valid_site", "name": "ValidSite"}

        coordinator.network_client.sites.get_all = AsyncMock(
            return_value=[site_with_none_id, site_valid]
        )

        result = await coordinator._async_update_data()

        # Should only have the valid site (None id is filtered out)
        assert "valid_site" in result["sites"]
        assert None not in result["sites"]

    @pytest.mark.asyncio
    async def test_async_update_data_skips_wifi_without_id(
        self, coordinator: UnifiConfigCoordinator
    ):
        """Test that WiFi networks without an ID are skipped."""
        # Create WiFi models - one with ID, one without
        wifi_with_id = MagicMock()
        wifi_with_id.model_dump.return_value = {"id": "wifi1", "name": "ValidWiFi"}

        wifi_without_id = MagicMock()
        wifi_without_id.model_dump.return_value = {"id": None, "name": "BadWiFi"}

        coordinator.network_client.wifi.get_all = AsyncMock(
            return_value=[wifi_with_id, wifi_without_id]
        )

        result = await coordinator._async_update_data()

        # Should only have the WiFi with valid ID
        assert "wifi1" in result["wifi"].get("default", {})
        # WiFi without ID should not be in the dict
        assert None not in result["wifi"].get("default", {})

    def test_client_links_from_stat_sta(self):
        """Only link fields survive; MACs normalise; untagged has no vlan."""
        links = UnifiConfigCoordinator._client_links(
            [
                {
                    "mac": "8C:ED:E1:00:00:01",
                    "hostname": "cam",
                    "ip": "10.2.0.77",
                    "is_wired": True,
                    "sw_mac": "28:70:4E:00:00:01",
                    "sw_port": 14,
                    "vlan": 3,
                    "network": "Cameras",
                    "network_id": "legacy-net-1",
                },
                {
                    "mac": "12:00:00:00:00:02",
                    "is_wired": False,
                    "ap_mac": "02:00:00:00:00:08",
                    "network": "Default",
                },
                {
                    "mac": "aa:bb:cc:00:00:03",
                    "is_wired": True,
                    "sw_mac": "28:70:4e:00:00:01",
                    "sw_port": 5,
                    "vlan": 0,
                    "network": "Default",
                },
                {"mac": "not-a-mac"},
                "garbage",
            ]
        )

        assert links == {
            "8c:ed:e1:00:00:01": {
                "sw_mac": "28:70:4e:00:00:01",
                "sw_port": 14,
                "vlan": 3,
                "network_name": "Cameras",
            },
            "12:00:00:00:00:02": {
                "ap_mac": "02:00:00:00:00:08",
                "network_name": "Default",
            },
            "aa:bb:cc:00:00:03": {
                "sw_mac": "28:70:4e:00:00:01",
                "sw_port": 5,
                "network_name": "Default",
            },
        }

    @pytest.mark.asyncio
    async def test_async_update_data_collects_client_links_without_wifi(
        self, coordinator: UnifiConfigCoordinator
    ):
        """Links are built even when the Wi-Fi section is unavailable."""
        client = coordinator.network_client
        client.wifi.get_all = AsyncMock(side_effect=RuntimeError("wifi down"))
        client.clients.get_active_legacy = AsyncMock(
            return_value=[
                {
                    "mac": "8c:ed:e1:00:00:01",
                    "sw_mac": "28:70:4e:00:00:01",
                    "sw_port": 3,
                }
            ]
        )

        result = await coordinator._async_update_data()

        assert result["client_links"]["default"] == {
            "8c:ed:e1:00:00:01": {"sw_mac": "28:70:4e:00:00:01", "sw_port": 3}
        }
        # The fixture polls two sites, so this call also runs for "site2";
        # count the "default" calls specifically to pin the spec's "one
        # /stat/sta call per site" invariant without over-specifying the
        # total call count across sites, which is incidental here.
        assert (
            client.clients.get_active_legacy.await_args_list.count(call("default")) == 1
        )

    @pytest.mark.asyncio
    async def test_async_update_data_keeps_client_links_on_failure(
        self, coordinator: UnifiConfigCoordinator
    ):
        """A failed /stat/sta keeps the previous links and does not fail."""
        coordinator.data["client_links"] = {
            "default": {"aa:aa:aa:aa:aa:aa": {"vlan": 2}}
        }
        coordinator.network_client.clients.get_active_legacy = AsyncMock(
            side_effect=RuntimeError("sta down")
        )

        result = await coordinator._async_update_data()

        assert result["client_links"]["default"] == {"aa:aa:aa:aa:aa:aa": {"vlan": 2}}

    @pytest.mark.asyncio
    async def test_async_update_data_wifi_enrichment_unchanged(
        self, coordinator: UnifiConfigCoordinator
    ):
        """The single /stat/sta call still feeds per-SSID client counts."""
        client = coordinator.network_client
        client.wifi.get_legacy_configs = AsyncMock(
            return_value=[{"name": "MainWiFi", "security": "wpapsk"}]
        )
        client.clients.get_active_legacy = AsyncMock(
            return_value=[{"essid": "MainWiFi", "mac": "12:00:00:00:00:02"}]
        )

        result = await coordinator._async_update_data()

        assert result["wifi"]["default"]["wifi1"]["num_connected_clients"] == 1
        # The fixture polls two sites, so this call also runs for "site2";
        # count the "default" calls specifically to pin the spec's "one
        # /stat/sta call per site" invariant without over-specifying the
        # total call count across sites, which is incidental here.
        assert (
            client.clients.get_active_legacy.await_args_list.count(call("default")) == 1
        )

    @pytest.mark.asyncio
    async def test_async_update_data_clears_client_links_with_no_sites(
        self, coordinator: UnifiConfigCoordinator
    ):
        """The no-sites early return blanks client_links like its siblings."""
        coordinator.data["client_links"] = {
            "default": {"aa:aa:aa:aa:aa:aa": {"vlan": 2}}
        }
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiNotFoundError("Not found", status_code=404)
        )

        result = await coordinator._async_update_data()

        assert result["client_links"] == {}

    def test_initialization_has_rule_sections(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """Test coordinator initialization includes rule section dictionaries."""
        assert "port_forwards" in coordinator.data
        assert "traffic_rules" in coordinator.data
        assert coordinator.data["port_forwards"] == {}
        assert coordinator.data["traffic_rules"] == {}

    def test_initialization_has_nat_and_static_route_sections(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """Test coordinator initialization includes NAT and static route sections."""
        assert "nat_rules" in coordinator.data
        assert "static_routes" in coordinator.data
        assert coordinator.data["nat_rules"] == {}
        assert coordinator.data["static_routes"] == {}

    @pytest.mark.parametrize(
        ("site_data", "expected"),
        [
            ({"internalReference": " branch "}, "branch"),
            ({"internalReference": ""}, None),
            ({"internalReference": "   "}, None),
            ({"name": "default"}, None),
            ({"internalReference": 123}, None),
            ("not-a-dict", None),
            (None, None),
        ],
    )
    def test_verified_legacy_site_name(
        self, site_data: Any, expected: str | None
    ) -> None:
        """Test verified_legacy_site_name extracts valid non-blank internalReference."""
        assert verified_legacy_site_name(site_data) == expected

    @pytest.mark.parametrize(
        ("rule_data", "name_field", "expected"),
        [
            ({"name": "  Plex  "}, "name", "Plex"),
            ({"description": " Kids bedtime "}, "description", "Kids bedtime"),
            ({"name": "   "}, "name", "pf1"),
            ({"name": 42}, "name", "pf1"),
            ({"description": "Plex"}, "name", "pf1"),
            ("not-a-dict", "name", "pf1"),
        ],
    )
    def test_rule_display_name(
        self, rule_data: Any, name_field: str, expected: str
    ) -> None:
        """Test rule_display_name strips the name and falls back to the rule id."""
        assert rule_display_name(rule_data, "pf1", name_field) == expected

    @pytest.mark.parametrize(
        ("rule_data", "expected"),
        [
            ({"is_predefined": True}, True),
            ({"is_predefined": True, "setting_preference": "manual"}, True),
            ({"setting_preference": "auto"}, True),
            ({"setting_preference": None}, True),
            ({"is_predefined": False, "setting_preference": "manual"}, False),
            ({}, False),
            ({"is_predefined": "true"}, False),
            ("not a dict", False),
        ],
    )
    def test_is_predefined_nat_rule(self, rule_data: Any, *, expected: bool) -> None:
        """Test is_predefined_nat_rule identifies controller-managed NAT rules."""
        assert is_predefined_nat_rule(rule_data) is expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("data_key", "endpoint_attr", "list_method", "avail_method"),
        _RULE_SECTION_PARAMS,
    )
    async def test_rule_section_fetched_by_internal_reference(
        self,
        coordinator: UnifiConfigCoordinator,
        data_key: str,
        endpoint_attr: str,
        list_method: str,
        avail_method: str,
    ) -> None:
        """Rule sections are fetched with internalReference and keyed by id."""
        site1 = _create_mock_model(
            {"id": "default", "name": "Default", "internalReference": "default"}
        )
        site2 = _create_mock_model(
            {"id": "site2", "name": "Site 2", "internalReference": "branch"}
        )
        coordinator.network_client.sites.get_all = AsyncMock(
            return_value=[site1, site2]
        )

        record1 = {"_id": "rule-1", "name": "Rule 1", "enabled": True}
        record2 = {"id": "rule-2", "name": "Rule 2", "enabled": False}
        endpoint = getattr(coordinator.network_client, endpoint_attr)

        def _fetch_side_effect(ref: str) -> list[Any]:
            return (
                [_create_mock_model(record1), _create_mock_model({"name": "No id"})]
                if ref == "default"
                else [_create_mock_model(record2)]
            )

        setattr(endpoint, list_method, AsyncMock(side_effect=_fetch_side_effect))

        result = await coordinator._async_update_data()
        mock_fn = getattr(endpoint, list_method)
        assert [c.args[0] for c in mock_fn.await_args_list] == ["default", "branch"]
        assert result[data_key]["default"] == {"rule-1": record1}
        assert result[data_key]["site2"] == {"rule-2": record2}
        avail_fn = getattr(coordinator, avail_method)
        assert avail_fn("default") is True
        assert avail_fn("site2") is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("data_key", "endpoint_attr", "list_method", "avail_method"),
        _RULE_SECTION_PARAMS,
    )
    async def test_rule_section_ignores_fuzzy_legacy_mapping(
        self,
        coordinator: UnifiConfigCoordinator,
        data_key: str,
        endpoint_attr: str,
        list_method: str,
        avail_method: str,
    ) -> None:
        """Rule sections ignore fuzzy legacy mapping from get_legacy_all."""
        site1 = _create_mock_model(
            {"id": "default", "name": "Default", "internalReference": "default"}
        )
        site2 = _create_mock_model(
            {"id": "site2", "name": "Site 2", "internalReference": "branch"}
        )
        coordinator.network_client.sites.get_all = AsyncMock(
            return_value=[site1, site2]
        )
        coordinator.network_client.sites.get_legacy_all = AsyncMock(
            return_value=[{"name": "default", "desc": "Site 2"}]
        )
        endpoint = getattr(coordinator.network_client, endpoint_attr)
        setattr(endpoint, list_method, AsyncMock(return_value=[]))

        await coordinator._async_update_data()
        mock_fn = getattr(endpoint, list_method)
        assert [c.args[0] for c in mock_fn.await_args_list] == ["default", "branch"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("data_key", "endpoint_attr", "list_method", "avail_method"),
        _RULE_SECTION_PARAMS,
    )
    async def test_rule_section_without_internal_reference_is_empty_and_available(
        self,
        coordinator: UnifiConfigCoordinator,
        data_key: str,
        endpoint_attr: str,
        list_method: str,
        avail_method: str,
    ) -> None:
        """Without internalReference, fetch is skipped and data is empty."""
        endpoint = getattr(coordinator.network_client, endpoint_attr)
        mock_fn = getattr(endpoint, list_method)

        result = await coordinator._async_update_data()
        mock_fn.assert_not_awaited()
        assert result[data_key]["default"] == {}
        assert result[data_key]["site2"] == {}
        avail_fn = getattr(coordinator, avail_method)
        assert avail_fn("default") is True
        assert avail_fn("site2") is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("data_key", "endpoint_attr", "list_method", "avail_method"),
        _RULE_SECTION_PARAMS,
    )
    @pytest.mark.parametrize(
        "error",
        [
            UniFiAuthenticationError("Forbidden", status_code=403),
            UniFiNotFoundError("Not found", status_code=404),
            UniFiResponseError("Non-JSON body", status_code=200),
        ],
    )
    async def test_rule_section_unsupported_response_stores_empty(
        self,
        coordinator: UnifiConfigCoordinator,
        data_key: str,
        endpoint_attr: str,
        list_method: str,
        avail_method: str,
        *,
        error: Exception,
    ) -> None:
        """Unsupported responses (403/404/200 non-JSON) store {} and keep available."""
        site = _create_mock_model(
            {"id": "default", "name": "Default", "internalReference": "default"}
        )
        coordinator.network_client.sites.get_all = AsyncMock(return_value=[site])
        endpoint = getattr(coordinator.network_client, endpoint_attr)
        setattr(endpoint, list_method, AsyncMock(side_effect=error))

        result = await coordinator._async_update_data()
        assert result[data_key]["default"] == {}
        avail_fn = getattr(coordinator, avail_method)
        assert avail_fn("default") is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("data_key", "endpoint_attr", "list_method", "avail_method"),
        _RULE_SECTION_PARAMS,
    )
    async def test_rule_section_transient_failure_keeps_previous_data(
        self,
        coordinator: UnifiConfigCoordinator,
        data_key: str,
        endpoint_attr: str,
        list_method: str,
        avail_method: str,
    ) -> None:
        """Transient failure preserves data and toggles available until recovery."""
        site = _create_mock_model(
            {"id": "default", "name": "Default", "internalReference": "default"}
        )
        coordinator.network_client.sites.get_all = AsyncMock(return_value=[site])
        rec = {"_id": "r1", "name": "R1", "enabled": True}
        endpoint = getattr(coordinator.network_client, endpoint_attr)
        setattr(
            endpoint, list_method, AsyncMock(return_value=[_create_mock_model(rec)])
        )

        res1 = await coordinator._async_update_data()
        assert res1[data_key]["default"] == {"r1": rec}
        avail_fn = getattr(coordinator, avail_method)
        assert avail_fn("default") is True

        setattr(
            endpoint,
            list_method,
            AsyncMock(side_effect=UniFiConnectionError("Connection lost")),
        )
        res2 = await coordinator._async_update_data()
        assert res2[data_key]["default"] == {"r1": rec}
        assert avail_fn("default") is False

        setattr(
            endpoint, list_method, AsyncMock(return_value=[_create_mock_model(rec)])
        )
        res3 = await coordinator._async_update_data()
        assert res3[data_key]["default"] == {"r1": rec}
        assert avail_fn("default") is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("data_key", "endpoint_attr", "list_method", "avail_method"),
        _RULE_SECTION_PARAMS,
    )
    async def test_rule_section_failure_is_per_site(
        self,
        coordinator: UnifiConfigCoordinator,
        data_key: str,
        endpoint_attr: str,
        list_method: str,
        avail_method: str,
    ) -> None:
        """Failure on site A does not affect availability on site B."""
        site_a = _create_mock_model(
            {"id": "site_a", "name": "Site A", "internalReference": "ref_a"}
        )
        site_b = _create_mock_model(
            {"id": "site_b", "name": "Site B", "internalReference": "ref_b"}
        )
        coordinator.network_client.sites.get_all = AsyncMock(
            return_value=[site_a, site_b]
        )

        rec_b = {"_id": "rb", "name": "RB", "enabled": True}

        def _side_effect(ref: str) -> list[Any]:
            if ref == "ref_a":
                msg = "Failure on A"
                raise UniFiConnectionError(msg)
            return [_create_mock_model(rec_b)]

        endpoint = getattr(coordinator.network_client, endpoint_attr)
        setattr(endpoint, list_method, AsyncMock(side_effect=_side_effect))

        result = await coordinator._async_update_data()
        avail_fn = getattr(coordinator, avail_method)
        assert avail_fn("site_a") is False
        assert avail_fn("site_b") is True
        assert result[data_key]["site_b"] == {"rb": rec_b}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("data_key", "endpoint_attr", "list_method", "avail_method"),
        _RULE_SECTION_PARAMS,
    )
    async def test_rule_section_401_triggers_reauth(
        self,
        coordinator: UnifiConfigCoordinator,
        data_key: str,
        endpoint_attr: str,
        list_method: str,
        avail_method: str,
    ) -> None:
        """401 error during rule fetch triggers reauthentication."""
        site = _create_mock_model(
            {"id": "default", "name": "Default", "internalReference": "default"}
        )
        coordinator.network_client.sites.get_all = AsyncMock(return_value=[site])
        endpoint = getattr(coordinator.network_client, endpoint_attr)
        setattr(
            endpoint,
            list_method,
            AsyncMock(
                side_effect=UniFiAuthenticationError("Unauthorized", status_code=401)
            ),
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("data_key", "endpoint_attr", "list_method", "avail_method"),
        _RULE_SECTION_PARAMS,
    )
    async def test_rule_sections_pruned_with_sites(
        self,
        coordinator: UnifiConfigCoordinator,
        data_key: str,
        endpoint_attr: str,
        list_method: str,
        avail_method: str,
    ) -> None:
        """Removed sites are pruned from rule sections, leaving empty dict."""
        site1 = _create_mock_model(
            {"id": "s1", "name": "S1", "internalReference": "r1"}
        )
        site2 = _create_mock_model(
            {"id": "s2", "name": "S2", "internalReference": "r2"}
        )
        coordinator.network_client.sites.get_all = AsyncMock(
            return_value=[site1, site2]
        )
        endpoint = getattr(coordinator.network_client, endpoint_attr)
        setattr(endpoint, list_method, AsyncMock(return_value=[]))

        res1 = await coordinator._async_update_data()
        assert "s1" in res1[data_key]
        assert "s2" in res1[data_key]

        coordinator.network_client.sites.get_all = AsyncMock(return_value=[site1])
        res2 = await coordinator._async_update_data()
        assert "s1" in res2[data_key]
        assert "s2" not in res2[data_key]

        coordinator.network_client.sites.get_all = AsyncMock(return_value=[])
        res3 = await coordinator._async_update_data()
        assert res3[data_key] == {}

    @pytest.mark.asyncio
    async def test_rule_sections_fetched_concurrently(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """Port forwards and traffic rules are fetched concurrently with gather."""
        site = _create_mock_model(
            {"id": "default", "name": "Default", "internalReference": "default"}
        )
        coordinator.network_client.sites.get_all = AsyncMock(return_value=[site])

        pf_started = asyncio.Event()
        tr_started = asyncio.Event()

        async def slow_list_pf(site_name: str) -> list[Any]:
            pf_started.set()
            await tr_started.wait()
            return []

        async def slow_list_tr(site_name: str) -> list[Any]:
            tr_started.set()
            await pf_started.wait()
            return []

        coordinator.network_client.port_forwards.list_port_forwards = AsyncMock(
            side_effect=slow_list_pf
        )
        coordinator.network_client.traffic_rules.list_traffic_rules = AsyncMock(
            side_effect=slow_list_tr
        )

        result = await asyncio.wait_for(coordinator._async_update_data(), timeout=2)
        assert result["port_forwards"]["default"] == {}
        assert result["traffic_rules"]["default"] == {}
        assert pf_started.is_set()
        assert tr_started.is_set()


# ============================================================================
# UnifiDeviceCoordinator Tests
# ============================================================================


class TestUnifiDeviceCoordinator:
    """Tests for UnifiDeviceCoordinator."""

    @pytest.fixture
    def config_coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> UnifiConfigCoordinator:
        """Create a config coordinator."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        coord = UnifiConfigCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )
        # Pre-populate with site data
        coord.data["sites"] = {"default": {"id": "default", "name": "Default"}}
        return coord

    @pytest.fixture
    def coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        config_coordinator: UnifiConfigCoordinator,
    ) -> UnifiDeviceCoordinator:
        """Create a device coordinator for testing."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        return UnifiDeviceCoordinator(
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
        coordinator: UnifiDeviceCoordinator,
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
        coordinator: UnifiDeviceCoordinator,
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

    def test_initialization(self, coordinator: UnifiDeviceCoordinator):
        """Test coordinator initialization."""
        assert coordinator.name == f"{DOMAIN}_device"
        assert coordinator.update_interval == SCAN_INTERVAL_DEVICE
        assert "devices" in coordinator.data
        assert "clients" in coordinator.data
        assert "stats" in coordinator.data

    @pytest.mark.asyncio
    async def test_async_update_data_success(self, coordinator: UnifiDeviceCoordinator):
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        assert UnifiDeviceCoordinator._normalize_legacy_port({"name": "no idx"}) is None

    def test_normalize_legacy_port_maps_all_optional_fields(self):
        """Optional identification/SFP fields are copied through when present."""
        normalized = UnifiDeviceCoordinator._normalize_legacy_port(
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
    ):
        """A v1 5xx without a legacy site name cannot fall back and raises."""
        coordinator.network_client.devices.get_all = AsyncMock(
            side_effect=UniFiResponseError("Internal Server Error", status_code=500)
        )

        with pytest.raises(UniFiResponseError):
            await coordinator._process_site("default")

    @pytest.mark.asyncio
    async def test_process_site_4xx_does_not_fall_back(
        self, coordinator: UnifiDeviceCoordinator
    ):
        """Only 5xx triggers the legacy fallback; a 4xx error propagates."""
        coordinator.network_client.devices.get_all = AsyncMock(
            side_effect=UniFiResponseError("Forbidden", status_code=403)
        )

        with pytest.raises(UniFiResponseError):
            await coordinator._process_site("default", legacy_site_name="default")

        coordinator.network_client.devices.get_legacy_site_devices.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_site_clients_4xx_raises_without_fallback(
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
    ):
        """Test data fetch with no sites available."""
        coordinator.config_coordinator.data["sites"] = {}

        result = await coordinator._async_update_data()

        # Should return existing data without changes
        assert result == coordinator.data
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_prunes_sites_no_longer_listed(
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
    @pytest.mark.asyncio
    async def test_mac_keyed_device_skips_statistics_keeps_legacy_metrics(
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator, endpoint: str, error: Exception
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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

    @pytest.mark.asyncio
    async def test_auth_failure_wins_over_other_site_failures(
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator, error: Exception
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
        self, coordinator: UnifiDeviceCoordinator
    ):
        """Sites still refresh when the classic site mapping is unavailable."""
        coordinator.network_client.sites.get_legacy_all = AsyncMock(
            side_effect=UniFiConnectionError("classic API down")
        )
        coordinator.network_client.devices.get_legacy_site_devices = AsyncMock()

        result = await coordinator._async_update_data()

        assert "device1" in result["devices"]["default"]
        coordinator.network_client.devices.get_legacy_site_devices.assert_not_called()

    @pytest.mark.asyncio
    async def test_stats_reuse_is_capped(self, coordinator: UnifiDeviceCoordinator):
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        coordinator: UnifiDeviceCoordinator,
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
        self, coordinator: UnifiDeviceCoordinator, error: Exception
    ):
        """A per-device stats error leaves that device without stats only."""
        coordinator.network_client.devices.get_statistics = AsyncMock(side_effect=error)

        result = await coordinator._async_update_data()

        assert "device1" in result["devices"]["default"]
        assert result["stats"]["default"]["device1"] == {}
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_refresh_recovers_after_failure(
        self, coordinator: UnifiDeviceCoordinator
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

    def test_get_device_existing(self, coordinator: UnifiDeviceCoordinator):
        """Test getting existing device."""
        coordinator.data["devices"] = {
            "default": {"device1": {"id": "device1", "name": "Switch"}}
        }
        result = coordinator.get_device("default", "device1")
        assert result == {"id": "device1", "name": "Switch"}

    def test_get_device_missing(self, coordinator: UnifiDeviceCoordinator):
        """Test getting missing device."""
        result = coordinator.get_device("default", "nonexistent")
        assert result is None

    def test_get_device_stats_existing(self, coordinator: UnifiDeviceCoordinator):
        """Test getting existing device stats."""
        coordinator.data["stats"] = {"default": {"device1": {"cpu": 15.2, "mem": 42.8}}}
        result = coordinator.get_device_stats("default", "device1")
        assert result == {"cpu": 15.2, "mem": 42.8}

    def test_get_device_stats_missing(self, coordinator: UnifiDeviceCoordinator):
        """Test getting missing device stats."""
        result = coordinator.get_device_stats("default", "nonexistent")
        assert result is None

    def test_get_clients(self, coordinator: UnifiDeviceCoordinator):
        """Test getting clients for a site."""
        coordinator.data["clients"] = {
            "default": {"client1": {"id": "client1", "name": "iPhone"}}
        }
        result = coordinator.get_clients("default")
        assert "client1" in result

    def test_get_clients_missing_site(self, coordinator: UnifiDeviceCoordinator):
        """Test getting clients for missing site."""
        result = coordinator.get_clients("nonexistent")
        assert result == {}

    @pytest.mark.asyncio
    async def test_cleanup_stale_devices(
        self, hass: HomeAssistant, coordinator: UnifiDeviceCoordinator
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
            "custom_components.unifi_insights.coordinators.device.dr.async_get"
        ) as mock_registry:
            mock_device = MagicMock()
            mock_device.id = "device_entry_id"
            set_mock_device_lookup(mock_registry.return_value, mock_device)

            coordinator._cleanup_stale_devices()

            # device2 should be marked for removal
            mock_registry.return_value.async_update_device.assert_called()

    @pytest.mark.asyncio
    async def test_process_site_empty_devices(
        self, coordinator: UnifiDeviceCoordinator
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
        self, hass: HomeAssistant, coordinator: UnifiDeviceCoordinator
    ):
        """Test cleanup when stale device is not found in registry."""
        # Set up previous device IDs
        coordinator._previous_network_device_ids = {"default_stale_device"}

        # Current devices are empty
        coordinator.data["devices"] = {"default": {}}

        # Mock device registry to return None for the device lookup
        with patch(
            "custom_components.unifi_insights.coordinators.device.dr.async_get"
        ) as mock_registry:
            set_mock_device_lookup(mock_registry.return_value, None)

            # Should not raise even when device not in registry
            coordinator._cleanup_stale_devices()

            # async_update_device should not be called since device wasn't found
            mock_registry.return_value.async_update_device.assert_not_called()

    def test_get_device_non_dict_value(self, coordinator: UnifiDeviceCoordinator):
        """Test get_device returns None for non-dict values."""
        coordinator.data["devices"] = {"default": {"device1": "not_a_dict"}}

        result = coordinator.get_device("default", "device1")
        assert result is None

    def test_get_device_stats_non_dict_value(self, coordinator: UnifiDeviceCoordinator):
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
    ):
        """VPN state of a site that is no longer polled is purged."""
        coordinator.data["vpn_connections"]["gone"] = {"tun1": {"status": "CONNECTED"}}
        coordinator._vpn_connection_failures["gone"] = 1

        result = await coordinator._async_update_data()

        assert "gone" not in result["vpn_connections"]
        assert "gone" not in coordinator._vpn_connection_failures

    @pytest.mark.asyncio
    async def test_legacy_primary_devices_still_get_wans(
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
    ):
        """Without a legacy site name the state is unknown, not "no VPNs"."""
        coordinator.network_client.vpn_clients.list_vpn_connections = AsyncMock()

        assert await coordinator._fetch_vpn_connections("default", None) is None
        coordinator.network_client.vpn_clients.list_vpn_connections.assert_not_awaited()

    def test_merge_legacy_wan_data(self, coordinator: UnifiDeviceCoordinator):
        """The gateway's per-WAN connection state is merged by MAC."""
        device_dict: dict[str, Any] = {"macAddress": "AA:BB:CC:DD:EE:FF"}
        legacy_devices_by_mac: dict[str, dict[str, Any]] = {
            "aa:bb:cc:dd:ee:ff": {
                "wan1": {"type": "ethernet", "name": "eth8", "up": True},
                "last_wan_status": {"WAN": "online"},
                "last_wan_interfaces": {"WAN": {"ip": "198.51.100.7", "alive": True}},
            }
        }

        UnifiDeviceCoordinator._merge_legacy_wan_data(
            device_dict, legacy_devices_by_mac
        )

        assert [wan["key"] for wan in device_dict["wans"]] == ["wan"]
        assert device_dict["wans"][0]["connected"] is True
        assert device_dict["wans"][0]["name"] == "WAN"

    def test_merge_legacy_wan_data_without_wan_blocks(
        self, coordinator: UnifiDeviceCoordinator
    ):
        """Devices without WAN blocks, or without legacy data, are untouched."""
        switch: dict[str, Any] = {"macAddress": "AA:BB:CC:DD:EE:FF"}
        unmatched: dict[str, Any] = {"macAddress": "11:22:33:44:55:66"}
        no_mac: dict[str, Any] = {}
        legacy_devices_by_mac: dict[str, dict[str, Any]] = {
            "aa:bb:cc:dd:ee:ff": {"port_table": []}
        }

        for device_dict in (switch, unmatched, no_mac):
            UnifiDeviceCoordinator._merge_legacy_wan_data(
                device_dict, legacy_devices_by_mac
            )
            assert "wans" not in device_dict

    @pytest.mark.asyncio
    async def test_async_update_data_merges_legacy_wans(
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
    ):
        """The WAN failure count of a site that is no longer polled is purged."""
        coordinator._legacy_wan_failures["gone"] = 1

        await coordinator._async_update_data()

        assert "gone" not in coordinator._legacy_wan_failures

    def test_merge_legacy_storage_data(
        self, coordinator: UnifiDeviceCoordinator
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

        UnifiDeviceCoordinator._merge_legacy_storage_data(
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
        self, coordinator: UnifiDeviceCoordinator
    ) -> None:
        """Devices without storage, or without legacy data, are untouched."""
        switch: dict[str, Any] = {"macAddress": "AA:BB:CC:DD:EE:FF"}
        empty_storage: dict[str, Any] = {"macAddress": "AA:BB:CC:DD:EE:FF"}
        unmatched: dict[str, Any] = {"macAddress": "11:22:33:44:55:66"}
        no_mac: dict[str, Any] = {}
        legacy_devices_by_mac: dict[str, dict[str, Any]] = {
            "aa:bb:cc:dd:ee:ff": {"port_table": []}
        }

        UnifiDeviceCoordinator._merge_legacy_storage_data(switch, legacy_devices_by_mac)
        assert "storage_mounts" not in switch

        legacy_devices_by_mac_empty: dict[str, dict[str, Any]] = {
            "aa:bb:cc:dd:ee:ff": {"storage": []}
        }
        UnifiDeviceCoordinator._merge_legacy_storage_data(
            empty_storage, legacy_devices_by_mac_empty
        )
        assert "storage_mounts" not in empty_storage

        for device_dict in (unmatched, no_mac):
            UnifiDeviceCoordinator._merge_legacy_storage_data(
                device_dict, legacy_devices_by_mac
            )
            assert "storage_mounts" not in device_dict

    @pytest.mark.asyncio
    async def test_async_update_data_merges_legacy_storage(
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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

        UnifiDeviceCoordinator._merge_legacy_port_data(
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
        self, coordinator: UnifiDeviceCoordinator
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

        UnifiDeviceCoordinator._merge_legacy_port_data(
            device_dict, legacy_devices_by_mac
        )

        ports = device_dict.get("ports", [])
        assert len(ports) == 1
        assert ports[0]["port_idx"] == 1

    def test_legacy_topology_block_reads_uplink(self):
        """The parent link and legacy type are copied from /stat/device."""
        block = UnifiDeviceCoordinator._legacy_topology_block(
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
        block = UnifiDeviceCoordinator._legacy_topology_block(
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
        assert UnifiDeviceCoordinator._legacy_topology_block(
            {"type": "udm", "uplink": {"type": "wire", "speed": 2500}}
        ) == {"legacy_type": "udm", "uplink_type": "wire", "uplink_speed": 2500}
        assert (
            UnifiDeviceCoordinator._legacy_topology_block(
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

        UnifiDeviceCoordinator._merge_legacy_uplink_data(
            device, {"58:d6:1f:00:00:02": legacy}
        )

        assert device["topology"]["uplink_mac"] == "28:70:4e:00:00:01"
        # The v1 field is left alone.
        assert device["uplink"] is None

    def test_merge_legacy_uplink_data_without_match_is_noop(self):
        """No legacy record, or an empty block, adds nothing."""
        device = {"id": "uuid-x", "macAddress": "aa:bb:cc:dd:ee:ff"}
        UnifiDeviceCoordinator._merge_legacy_uplink_data(device, {})
        UnifiDeviceCoordinator._merge_legacy_uplink_data(
            device, {"aa:bb:cc:dd:ee:ff": {"mac": "aa:bb:cc:dd:ee:ff"}}
        )
        assert "topology" not in device

    def test_legacy_device_to_v1_dict_carries_topology(
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        self, coordinator: UnifiDeviceCoordinator
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
        coordinator: UnifiDeviceCoordinator,
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
        self, coordinator: UnifiDeviceCoordinator
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


# ============================================================================
# UnifiProtectCoordinator Tests
# ============================================================================


class TestUnifiProtectCoordinator:
    """Tests for UnifiProtectCoordinator."""

    @pytest.mark.parametrize(
        "family", ["chimes", "fobs", "link_stations", "alarm_hubs", "sirens"]
    )
    @pytest.mark.parametrize(
        "error",
        [UniFiTimeoutError("offline"), UniFiResponseError("offline", status_code=502)],
    )
    async def test_family_poll_outage_logs_only_transitions(
        self,
        coordinator: UnifiProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
        family: str,
        error: Exception,
    ) -> None:
        """Swallowed family failures log once per streak while polls succeed."""
        caplog.set_level(logging.DEBUG)
        endpoint = getattr(coordinator.protect_client, family)
        endpoint.get_all.side_effect = error
        failure = f"Protect coordinator: Error fetching {family}: {error}"
        recovery = f"Protect coordinator: Fetching {family} recovered"
        for _ in range(4):
            await coordinator.async_refresh()
            assert coordinator.last_update_success
        assert [r.levelno for r in caplog.records if r.getMessage() == failure] == [
            logging.WARNING,
            logging.DEBUG,
            logging.DEBUG,
            logging.DEBUG,
        ]

        # Recovery of this family must not reset another family's outage guard.
        other = "fobs" if family == "chimes" else "chimes"
        other_endpoint = getattr(coordinator.protect_client, other)
        other_endpoint.get_all.side_effect = error
        await coordinator.async_refresh()
        endpoint.get_all.side_effect = None
        for _ in range(2):
            await coordinator.async_refresh()
            assert coordinator.last_update_success
        assert caplog.messages.count(recovery) == 1
        assert (
            next(r.levelno for r in caplog.records if r.getMessage() == recovery)
            == logging.INFO
        )
        assert (
            sum(
                r.levelno == logging.WARNING
                and r.getMessage()
                == f"Protect coordinator: Error fetching {other}: {error}"
                for r in caplog.records
            )
            == 1
        )

        endpoint.get_all.side_effect = error
        for _ in range(2):
            await coordinator.async_refresh()
        assert (
            sum(
                r.levelno == logging.WARNING and r.getMessage() == failure
                for r in caplog.records
            )
            == 2
        )

    @pytest.fixture
    async def coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> AsyncGenerator[UnifiProtectCoordinator]:
        """Create a protect coordinator for testing, shut down on teardown.

        Was a plain (non-yield) fixture with no teardown at all - this
        class constructs `UnifiProtectCoordinator` directly rather than
        going through config-entry setup/unload, so nothing ever cancelled
        the sensor-reconcile timer, the three tracked sensor background
        tasks (`_sensor_refresh_task`/`_sensor_reconcile_task`/
        `_sensor_reconnect_task`), or shut down the
        `_sensor_reconnect_debouncer`'s trailing-edge timer that a test
        left pending. `async_shutdown()` (see coordinators/protect.py)
        cancels exactly those per-test - it is hygiene for this test file's
        own per-test coordinator instances, not a fix for a leak or a
        regression guard: measured via a real
        `entry._async_process_on_unload(hass)` reload-cycle probe, the
        `EVENT_HOMEASSISTANT_STOP` listener wrapped in
        `entry.async_on_unload` releases its coordinator on unload
        regardless of whether this fixture calls `async_shutdown()`, and
        the full 286-test file passes unmodified even with this fixture
        reverted to a plain non-yield fixture.
        """
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        coord = UnifiProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )
        yield coord
        await coord.async_shutdown()

    async def test_protect_poll_outage_logs_only_transitions(
        self,
        coordinator: UnifiProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Absorption and escalation do not add warnings before UpdateFailed."""
        caplog.set_level(logging.INFO)
        coordinator.protect_client.cameras.get_all.side_effect = UniFiTimeoutError(
            "offline"
        )
        for poll in range(MAX_CONSECUTIVE_EMPTY_FETCHES + 4):
            await coordinator.async_refresh()
            assert sum(r.levelno >= logging.WARNING for r in caplog.records) == int(
                poll >= MAX_CONSECUTIVE_EMPTY_FETCHES
            )
        coordinator.protect_client.cameras.get_all.side_effect = None
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        assert sum("recovered" in r.getMessage().lower() for r in caplog.records) == 1
        coordinator.protect_client.cameras.get_all.side_effect = UniFiTimeoutError(
            "offline"
        )
        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES + 1):
            await coordinator.async_refresh()
        assert sum(r.levelno >= logging.WARNING for r in caplog.records) == 2

    @pytest.fixture
    async def coordinator_no_protect(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> AsyncGenerator[UnifiProtectCoordinator]:
        """Create a protect coordinator without protect client, shut down on teardown.

        See `coordinator` above - same per-test cleanup, this fixture just
        omits the `protect_client`.
        """
        network_client = _create_mock_network_client()
        coord = UnifiProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=None,
            entry=mock_config_entry,
        )
        yield coord
        await coord.async_shutdown()

    def test_initialization(self, coordinator: UnifiProtectCoordinator):
        """Test coordinator initialization."""
        assert coordinator.name == f"{DOMAIN}_protect"
        assert coordinator.update_interval == SCAN_INTERVAL_PROTECT
        assert "cameras" in coordinator.data
        assert "lights" in coordinator.data
        assert "sensors" in coordinator.data
        assert "nvrs" in coordinator.data
        assert "chimes" in coordinator.data
        assert "viewers" in coordinator.data
        assert "liveviews" in coordinator.data
        assert "events" in coordinator.data

    def test_websocket_state_initialized(self, coordinator: UnifiProtectCoordinator):
        """Test WebSocket state is initialized but not started at construction."""
        assert coordinator.websocket_task is None
        assert coordinator.events_websocket_task is None
        assert coordinator._protect_websocket is coordinator.protect_client.websocket

    def test_websocket_state_without_client(
        self, coordinator_no_protect: UnifiProtectCoordinator
    ):
        """Test WebSocket state without a protect client."""
        assert coordinator_no_protect.protect_client is None
        assert coordinator_no_protect._protect_websocket is None
        assert coordinator_no_protect.websocket_task is None
        assert coordinator_no_protect.events_websocket_task is None

    @pytest.mark.asyncio
    async def test_async_start_websocket_without_client(
        self, coordinator_no_protect: UnifiProtectCoordinator
    ):
        """Test async_start_websocket returns early without a protect client."""
        await coordinator_no_protect.async_start_websocket()
        assert coordinator_no_protect.websocket_task is None

    @pytest.mark.asyncio
    async def test_async_start_websocket_success(
        self, hass: HomeAssistant, coordinator: UnifiProtectCoordinator
    ):
        """Test async_start_websocket resolves host_id and starts both the
        devices and events subscriptions (see task 1: the missing events
        subscription is the root cause of motion detection never firing).

        Each subscription is registered with its OWN connection-state
        callback (review finding 1: a single shared callback couldn't tell
        the caller which subscription actually transitioned, which is what
        made a devices-only reconnect look identical to a full recovery).
        """
        coordinator.protect_client.get_host_id = AsyncMock(return_value="nvr1")
        coordinator._protect_websocket.subscribe_with_callback = AsyncMock()

        await coordinator.async_start_websocket()

        assert coordinator.websocket_task is not None
        assert coordinator.events_websocket_task is not None
        coordinator.protect_client.get_host_id.assert_awaited_once()
        await coordinator.websocket_task
        await coordinator.events_websocket_task

        coordinator._protect_websocket.subscribe_with_callback.assert_any_await(
            "nvr1",
            coordinator._site_id,
            "devices",
            coordinator._on_websocket_message,
            reconnect=True,
            on_connection_state_change=coordinator._on_devices_connection_state_change,
        )
        coordinator._protect_websocket.subscribe_with_callback.assert_any_await(
            "nvr1",
            coordinator._site_id,
            "events",
            coordinator._on_websocket_event_message,
            reconnect=True,
            on_connection_state_change=coordinator._on_events_connection_state_change,
        )
        assert coordinator._protect_websocket.subscribe_with_callback.await_count == 2

    @pytest.mark.asyncio
    async def test_async_start_websocket_already_running(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test async_start_websocket is a no-op if already running."""
        existing_task = asyncio.get_event_loop().create_future()
        coordinator.websocket_task = existing_task  # type: ignore[assignment]
        coordinator.protect_client.get_host_id = AsyncMock(return_value="nvr1")

        await coordinator.async_start_websocket()

        coordinator.protect_client.get_host_id.assert_not_called()
        assert coordinator.websocket_task is existing_task
        existing_task.set_result(None)

    @pytest.mark.asyncio
    async def test_async_start_websocket_host_id_failure(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test async_start_websocket swallows host_id resolution failures."""
        coordinator.protect_client.get_host_id = AsyncMock(
            side_effect=Exception("no NVR")
        )

        # Should not raise - WebSocket is additive, polling stays the fallback.
        await coordinator.async_start_websocket()

        assert coordinator.websocket_task is None
        assert coordinator.events_websocket_task is None

    def test_on_websocket_message_top_level(self, coordinator: UnifiProtectCoordinator):
        """Test the WS message adapter with fields at the top level."""
        coordinator._on_websocket_message(
            {"modelKey": "sensor", "id": "sensor3", "isOpened": True}
        )

        assert coordinator.data["sensors"]["sensor3"]["isOpened"] is True

    def test_on_websocket_message_nested_payload(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test the WS message adapter with fields nested under 'payload'."""
        coordinator._on_websocket_message(
            {"payload": {"modelKey": "sensor", "id": "sensor4", "isOpened": False}}
        )

        assert coordinator.data["sensors"]["sensor4"]["isOpened"] is False

    def test_on_websocket_message_item_wrapper(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test the WS message adapter with fields nested under 'item'.

        Confirmed live against hardware 2026-08-12: local-console update
        frames use {"type": "update", "item": {...}}, not top-level fields.
        """
        coordinator._on_websocket_message(
            {
                "type": "update",
                "item": {"modelKey": "sensor", "id": "sensor9", "isOpened": True},
            }
        )

        assert coordinator.data["sensors"]["sensor9"]["isOpened"] is True

    def test_on_websocket_message_snake_case_model_key(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test the WS message adapter accepts snake_case model_key."""
        coordinator._on_websocket_message({"model_key": "light", "id": "light3"})

        assert "light3" in coordinator.data["lights"]

    def test_on_websocket_message_missing_model_key(
        self, coordinator: UnifiProtectCoordinator, caplog: pytest.LogCaptureFixture
    ):
        """Test the WS message adapter warns and drops unparseable messages."""
        with caplog.at_level(logging.WARNING):
            coordinator._on_websocket_message({"id": "sensor5"})

        assert "missing modelKey" in caplog.text
        assert "sensor5" not in coordinator.data["sensors"]

    def test_on_websocket_message_not_a_dict(
        self, coordinator: UnifiProtectCoordinator, caplog: pytest.LogCaptureFixture
    ):
        """Test the WS message adapter warns on non-dict payloads."""
        with caplog.at_level(logging.WARNING):
            coordinator._on_websocket_message("not-a-dict")  # type: ignore[arg-type]

        assert "not a JSON object" in caplog.text

    def test_on_websocket_message_action_payload_split(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test the adapter merges a header/payload-split message.

        Some UniFi WebSocket surfaces split the model identity into an
        "action" header and the changed fields into "payload" (rather than
        a single flat object). The adapter must resolve modelKey/id and the
        field dict independently instead of picking one container and
        giving up, or a message shaped this way would silently drop every
        real frame.
        """
        coordinator._on_websocket_message(
            {
                "action": {"action": "update", "modelKey": "sensor", "id": "sensor6"},
                "payload": {"isOpened": True},
            }
        )

        assert coordinator.data["sensors"]["sensor6"]["id"] == "sensor6"
        assert coordinator.data["sensors"]["sensor6"]["isOpened"] is True

    def test_on_websocket_message_model_key_without_id(
        self, coordinator: UnifiProtectCoordinator, caplog: pytest.LogCaptureFixture
    ):
        """Test a resolvable modelKey with no id logs at debug, not silently."""
        with caplog.at_level(logging.DEBUG):
            coordinator._on_websocket_message({"modelKey": "sensor", "isOpened": True})

        assert "missing device id" in caplog.text
        assert coordinator.data["sensors"] == {}

    def test_on_websocket_message_warning_capped_after_first(
        self, coordinator: UnifiProtectCoordinator, caplog: pytest.LogCaptureFixture
    ):
        """Test repeated unparseable messages only WARNING once, then DEBUG."""
        with caplog.at_level(logging.DEBUG):
            coordinator._on_websocket_message({"id": "sensor7"})
            caplog.clear()
            coordinator._on_websocket_message({"id": "sensor8"})

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert warning_records == []
        assert any("sensor8" in r.getMessage() for r in debug_records)

    def test_on_websocket_message_does_not_clobber_type_with_envelope_verb(
        self, coordinator: UnifiProtectCoordinator
    ):
        """The envelope's top-level "type" (the action verb) must not
        overwrite a real device "type" field on a partial update frame.

        Regression test: `containers = [payload, action, item, message]`
        included the raw envelope unconditionally, so its "type": "update"
        leaked into the merged device dict whenever `item` was a partial
        delta that didn't re-send the unchanged "type" field - clobbering
        the previously-correct hardware model string. Measured in
        production flipping back and forth 49 times in 10 minutes for
        UFP-SENSE/USL-Entry-US/USL-Environmental-US sensors.
        """
        coordinator.data["sensors"]["sensor10"] = {
            "id": "sensor10",
            "modelKey": "sensor",
            "type": "UFP-SENSE",
            "isOpened": False,
        }

        # A partial WS update: the envelope says "update", and the item
        # only carries the field that actually changed (isOpened) - real
        # Protect delta frames are not guaranteed to re-send "type".
        coordinator._on_websocket_message(
            {
                "type": "update",
                "item": {"modelKey": "sensor", "id": "sensor10", "isOpened": True},
            }
        )

        assert coordinator.data["sensors"]["sensor10"]["type"] == "UFP-SENSE"
        assert coordinator.data["sensors"]["sensor10"]["isOpened"] is True

    def test_on_websocket_message_replaces_device_dict_not_mutates_in_place(
        self, coordinator: UnifiProtectCoordinator
    ):
        """A WS update must produce a new per-device dict, not mutate the
        old one in place - in-place mutation can make HA listeners that
        hold a stale reference miss the transition (see task 4).
        """
        original = {"id": "sensor11", "modelKey": "sensor", "isOpened": False}
        coordinator.data["sensors"]["sensor11"] = original

        coordinator._on_websocket_message(
            {"modelKey": "sensor", "id": "sensor11", "isOpened": True}
        )

        assert coordinator.data["sensors"]["sensor11"] is not original
        assert original["isOpened"] is False
        assert coordinator.data["sensors"]["sensor11"]["isOpened"] is True

    @pytest.mark.asyncio
    async def test_async_stop_websocket_noop_when_never_started(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test async_stop_websocket is safe when no task was ever started.

        It still calls ProtectWebSocket.stop() unconditionally (harmless -
        it just sets a flag), but must not touch websocket_task since it's
        None.
        """
        await coordinator.async_stop_websocket()

        assert coordinator.websocket_task is None

    @pytest.mark.asyncio
    async def test_async_stop_websocket_stops_and_awaits_real_task(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test async_stop_websocket stops the socket and awaits BOTH real
        tasks (devices and events - task 1 requires both to be stopped and
        awaited, mirroring the existing devices-only teardown ordering).
        """
        coordinator.protect_client.get_host_id = AsyncMock(return_value="nvr1")

        async def _run_forever(*_args, **_kwargs):
            await asyncio.sleep(3600)

        coordinator._protect_websocket.subscribe_with_callback = AsyncMock(
            side_effect=_run_forever
        )
        await coordinator.async_start_websocket()
        devices_task = coordinator.websocket_task
        events_task = coordinator.events_websocket_task
        assert devices_task is not None
        assert events_task is not None
        assert not devices_task.done()
        assert not events_task.done()

        await coordinator.async_stop_websocket()

        coordinator._protect_websocket.stop.assert_called_once()
        assert devices_task.done()
        assert events_task.done()

    @pytest.mark.asyncio
    async def test_async_update_data_success(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test successful data fetch."""
        result = await coordinator._async_update_data()

        assert "cameras" in result
        assert "camera1" in result["cameras"]
        assert "lights" in result
        assert "light1" in result["lights"]
        assert "sensors" in result
        assert "sensor1" in result["sensors"]
        assert "nvrs" in result
        assert "nvr1" in result["nvrs"]
        assert "chimes" in result
        assert "chime1" in result["chimes"]
        assert "viewers" in result
        assert "viewer1" in result["viewers"]
        assert "liveviews" in result
        assert "liveview1" in result["liveviews"]
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_no_protect_client(
        self, coordinator_no_protect: UnifiProtectCoordinator
    ):
        """Test data fetch without protect client."""
        result = await coordinator_no_protect._async_update_data()

        # Should return empty data
        assert result["cameras"] == {}
        assert result["lights"] == {}

    @pytest.mark.asyncio
    async def test_async_update_data_sensors_error(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test data fetch with sensors error (should not fail)."""
        coordinator.protect_client.sensors.get_all = AsyncMock(
            side_effect=Exception("Sensors failed")
        )

        result = await coordinator._async_update_data()

        # Other devices should still be fetched
        assert "camera1" in result["cameras"]
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_nvr_error(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test data fetch with NVR error (should not fail)."""
        coordinator.protect_client.nvr.get = AsyncMock(
            side_effect=Exception("NVR failed")
        )

        result = await coordinator._async_update_data()

        # Other devices should still be fetched
        assert "camera1" in result["cameras"]
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_chimes_error(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test data fetch with chimes error (should not fail)."""
        coordinator.protect_client.chimes.get_all = AsyncMock(
            side_effect=Exception("Chimes failed")
        )

        result = await coordinator._async_update_data()

        # Other devices should still be fetched
        assert "camera1" in result["cameras"]

    @pytest.mark.asyncio
    async def test_async_update_data_viewers_error(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test data fetch with viewers error (should not fail)."""
        coordinator.protect_client.viewers.get_all = AsyncMock(
            side_effect=Exception("Viewers failed")
        )

        result = await coordinator._async_update_data()

        # Other devices should still be fetched
        assert "camera1" in result["cameras"]

    @pytest.mark.asyncio
    async def test_async_update_data_liveviews_error(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test data fetch with liveviews error (should not fail)."""
        coordinator.protect_client.liveviews.get_all = AsyncMock(
            side_effect=Exception("Liveviews failed")
        )

        result = await coordinator._async_update_data()

        # Other devices should still be fetched
        assert "camera1" in result["cameras"]

    @pytest.mark.asyncio
    async def test_async_update_data_auth_error(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test data fetch with auth error."""
        coordinator.protect_client.cameras.get_all = AsyncMock(
            side_effect=UniFiAuthenticationError("Invalid API key")
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_connection_error(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test a sustained connection error eventually fails the poll.

        The first MAX_CONSECUTIVE_EMPTY_FETCHES polls are absorbed so a single
        blipped endpoint does not take every Protect entity unavailable; past
        that window the error propagates and the coordinator reports failure.
        """
        coordinator.protect_client.cameras.get_all = AsyncMock(
            side_effect=UniFiConnectionError("Connection refused")
        )

        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._async_update_data()

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    def test_handle_device_update_camera(self, coordinator: UnifiProtectCoordinator):
        """Test handling camera device update."""
        coordinator._handle_device_update(
            "camera", {"id": "camera2", "name": "Back Camera"}
        )

        assert "camera2" in coordinator.data["cameras"]
        assert coordinator.data["cameras"]["camera2"]["name"] == "Back Camera"

    def test_handle_device_update_light(self, coordinator: UnifiProtectCoordinator):
        """Test handling light device update."""
        coordinator._handle_device_update(
            "light", {"id": "light2", "name": "Porch Light"}
        )

        assert "light2" in coordinator.data["lights"]

    def test_handle_device_update_sensor(self, coordinator: UnifiProtectCoordinator):
        """Test handling sensor device update."""
        coordinator._handle_device_update(
            "sensor", {"id": "sensor2", "name": "Window Sensor"}
        )

        assert "sensor2" in coordinator.data["sensors"]

    def test_handle_device_update_nvr(self, coordinator: UnifiProtectCoordinator):
        """Test handling NVR device update."""
        coordinator._handle_device_update("nvr", {"id": "nvr2", "name": "NVR 2"})

        assert "nvr2" in coordinator.data["nvrs"]

    def test_handle_device_update_viewer(self, coordinator: UnifiProtectCoordinator):
        """Test handling viewer device update."""
        coordinator._handle_device_update(
            "viewer", {"id": "viewer2", "name": "Viewport 2"}
        )

        assert "viewer2" in coordinator.data["viewers"]

    def test_handle_device_update_chime(self, coordinator: UnifiProtectCoordinator):
        """Test handling chime device update."""
        coordinator._handle_device_update("chime", {"id": "chime2", "name": "Chime 2"})

        assert "chime2" in coordinator.data["chimes"]

    def test_handle_device_update_no_id(self, coordinator: UnifiProtectCoordinator):
        """Test handling device update without ID."""
        coordinator._handle_device_update("camera", {"name": "No ID Camera"})

        # Should not add device without ID
        assert len(coordinator.data["cameras"]) == 0

    def test_handle_device_update_unknown_model_type(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """Test handling unknown device model type notifies listeners and
        preserves collections.
        """
        coordinator.data["cameras"] = {"camera1": {"id": "camera1", "name": "Cam 1"}}
        coordinator.data["lights"] = {"light1": {"id": "light1", "name": "Light 1"}}
        snapshot_cameras = dict(coordinator.data["cameras"])
        snapshot_lights = dict(coordinator.data["lights"])
        snapshot_sensors = dict(coordinator.data["sensors"])
        snapshot_nvrs = dict(coordinator.data["nvrs"])
        snapshot_viewers = dict(coordinator.data["viewers"])
        snapshot_chimes = dict(coordinator.data["chimes"])

        listener = MagicMock()
        coordinator.async_add_listener(listener)

        with patch.object(
            coordinator,
            "async_set_updated_data",
            wraps=coordinator.async_set_updated_data,
        ) as mock_set_data:
            # 1. Update without id returns early and does not notify
            coordinator._handle_device_update("unknown_type", {"name": "No ID"})
            coordinator._handle_device_update(
                "unknown_type", {"id": "", "name": "Empty ID"}
            )
            listener.assert_not_called()
            mock_set_data.assert_not_called()

            # 2. Update with id notifies through async_set_updated_data fall-through
            coordinator._handle_device_update(
                "unknown_type", {"id": "unk1", "name": "Unknown"}
            )
            listener.assert_called_once()
            mock_set_data.assert_called_once_with(coordinator.data)
            assert coordinator.last_update_success is True

        # 3. Known collections are untouched
        assert coordinator.data["cameras"] == snapshot_cameras
        assert coordinator.data["lights"] == snapshot_lights
        assert coordinator.data["sensors"] == snapshot_sensors
        assert coordinator.data["nvrs"] == snapshot_nvrs
        assert coordinator.data["viewers"] == snapshot_viewers
        assert coordinator.data["chimes"] == snapshot_chimes

    def test_handle_event_update_motion(self, coordinator: UnifiProtectCoordinator):
        """Test handling motion event."""
        # First add a camera
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}

        coordinator._handle_event_update(
            "motion",
            {
                "id": "event1",
                "device": "camera1",
                "start": 1234567890,
                "end": None,
            },
        )

        assert "motion" in coordinator.data["events"]
        assert coordinator.data["cameras"]["camera1"]["lastMotionStart"] == 1234567890
        assert coordinator.data["cameras"]["camera1"]["lastMotionEnd"] is None

    def test_handle_event_update_smart_detect(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test handling smart detection event."""
        # First add a camera
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}

        coordinator._handle_event_update(
            "smartDetectZone",
            {
                "id": "event2",
                "device": "camera1",
                "smartDetectTypes": ["person", "vehicle"],
                "start": 1234567890,
                "end": None,
            },
        )

        assert coordinator.data["cameras"]["camera1"]["lastSmartDetectTypes"] == [
            "person",
            "vehicle",
        ]

    def test_handle_event_update_doorbell_ring(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test handling doorbell ring event."""
        # First add a camera
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Doorbell"}

        coordinator._handle_event_update(
            "ring",
            {
                "id": "event3",
                "device": "camera1",
                "start": 1234567890,
                "end": None,
            },
        )

        assert coordinator.data["cameras"]["camera1"]["lastRingStart"] == 1234567890
        assert coordinator.data["cameras"]["camera1"]["lastRingEnd"] is None

    def test_handle_event_update_light_motion(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test handling light motion event."""
        # First add a light
        coordinator.data["lights"]["light1"] = {"id": "light1", "name": "Test"}

        coordinator._handle_event_update(
            "motion",
            {
                "id": "event4",
                "device": "light1",
                "start": 1234567890,
                "end": None,
            },
        )

        assert coordinator.data["lights"]["light1"]["lastMotionStart"] == 1234567890

    def test_handle_event_update_no_id(self, coordinator: UnifiProtectCoordinator):
        """Test handling event without ID."""
        initial_events = dict(coordinator.data["events"])
        coordinator._handle_event_update("motion", {"device": "camera1"})

        # Events should not change without event ID
        assert coordinator.data["events"] == initial_events

    def test_handle_event_update_event_id_semantics(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """Test event ID semantics: missing/empty IDs dropped, distinct IDs accumulate,
        repeated IDs replace, notification count matches accepted events."""
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        with patch.object(
            coordinator,
            "async_update_listeners",
            wraps=coordinator.async_update_listeners,
        ) as notify:
            # 1. Missing or empty event ID is dropped and produces no notification
            coordinator._handle_event_update("motion", {"device": "camera1"})
            coordinator._handle_event_update("motion", {"id": "", "device": "camera1"})
            assert coordinator.data["events"].get("motion", {}) == {}
            notify.assert_not_called()
            listener.assert_not_called()

            # 2. Distinct IDs accumulate and notify once per accepted event
            coordinator._handle_event_update(
                "motion", {"id": "ev1", "device": "camera1", "start": 100}
            )
            assert notify.call_count == 1
            assert listener.call_count == 1
            assert coordinator.data["events"]["motion"]["ev1"] == {
                "id": "ev1",
                "device": "camera1",
                "start": 100,
            }

            coordinator._handle_event_update(
                "motion", {"id": "ev2", "device": "camera1", "start": 200}
            )
            assert notify.call_count == 2
            assert listener.call_count == 2
            assert set(coordinator.data["events"]["motion"].keys()) == {"ev1", "ev2"}
            assert coordinator.data["events"]["motion"]["ev2"] == {
                "id": "ev2",
                "device": "camera1",
                "start": 200,
            }

            # 3. Repeated ID replaces previous event and notifies
            coordinator._handle_event_update(
                "motion", {"id": "ev1", "device": "camera1", "end": 400}
            )
            assert notify.call_count == 3
            assert listener.call_count == 3
            assert set(coordinator.data["events"]["motion"].keys()) == {"ev1", "ev2"}
            assert coordinator.data["events"]["motion"]["ev1"] == {
                "id": "ev1",
                "device": "camera1",
                "end": 400,
            }

    def test_get_camera_existing(self, coordinator: UnifiProtectCoordinator):
        """Test getting existing camera."""
        coordinator.data["cameras"] = {"camera1": {"id": "camera1", "name": "Test"}}
        result = coordinator.get_camera("camera1")
        assert result == {"id": "camera1", "name": "Test"}

    def test_get_camera_missing(self, coordinator: UnifiProtectCoordinator):
        """Test getting missing camera."""
        result = coordinator.get_camera("nonexistent")
        assert result is None

    def test_get_light_existing(self, coordinator: UnifiProtectCoordinator):
        """Test getting existing light."""
        coordinator.data["lights"] = {"light1": {"id": "light1", "name": "Test"}}
        result = coordinator.get_light("light1")
        assert result == {"id": "light1", "name": "Test"}

    def test_get_light_missing(self, coordinator: UnifiProtectCoordinator):
        """Test getting missing light."""
        result = coordinator.get_light("nonexistent")
        assert result is None

    def test_get_sensor_existing(self, coordinator: UnifiProtectCoordinator):
        """Test getting existing sensor."""
        coordinator.data["sensors"] = {"sensor1": {"id": "sensor1", "name": "Test"}}
        result = coordinator.get_sensor("sensor1")
        assert result == {"id": "sensor1", "name": "Test"}

    def test_get_sensor_missing(self, coordinator: UnifiProtectCoordinator):
        """Test getting missing sensor."""
        result = coordinator.get_sensor("nonexistent")
        assert result is None

    def test_get_nvr_existing(self, coordinator: UnifiProtectCoordinator):
        """Test getting existing NVR."""
        coordinator.data["nvrs"] = {"nvr1": {"id": "nvr1", "name": "Test"}}
        result = coordinator.get_nvr("nvr1")
        assert result == {"id": "nvr1", "name": "Test"}

    def test_get_nvr_missing(self, coordinator: UnifiProtectCoordinator):
        """Test getting missing NVR."""
        result = coordinator.get_nvr("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_cleanup_stale_devices(
        self, hass: HomeAssistant, coordinator: UnifiProtectCoordinator
    ):
        """Test stale device cleanup."""
        # Set up previous device IDs
        coordinator._previous_protect_device_ids = {
            "cameras": {"camera1", "camera2"},
            "lights": set(),
            "sensors": set(),
            "nvrs": set(),
            "viewers": set(),
            "chimes": set(),
        }

        # Current cameras only have camera1
        coordinator.data["cameras"] = {"camera1": {"id": "camera1"}}

        # Mock device registry
        with patch(
            "custom_components.unifi_insights.coordinators.protect.dr.async_get"
        ) as mock_registry:
            mock_device = MagicMock()
            mock_device.id = "device_entry_id"
            set_mock_device_lookup(mock_registry.return_value, mock_device)

            # Removal waits out the MAX_CONSECUTIVE_MISSING_POLLS grace window.
            for _ in range(MAX_CONSECUTIVE_MISSING_POLLS + 1):
                coordinator._cleanup_stale_devices()

            # camera2 should be marked for removal
            mock_registry.return_value.async_update_device.assert_called()

    def test_websocket_construction_does_not_start_task(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ):
        """Test constructing the coordinator never starts the WebSocket task.

        Starting requires an await (to resolve host_id), so it must happen via
        async_start_websocket(), not as a side effect of __init__.
        """
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()

        coordinator = UnifiProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )
        assert coordinator.websocket_task is None

    def test_handle_event_update_unknown_device(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test handling event for unknown device - no device_id match."""
        # Don't add any cameras/lights/sensors
        coordinator.data["cameras"] = {}
        coordinator.data["lights"] = {}

        # Event with device_id that doesn't match any known device type
        coordinator._handle_event_update(
            "motion",
            {
                "id": "event_unknown",
                "device": "unknown_device",
                "start": 1234567890,
                "end": None,
            },
        )

        # Events should still be stored
        assert "motion" in coordinator.data["events"]
        assert "event_unknown" in coordinator.data["events"]["motion"]

    def test_handle_event_update_no_device_id(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test handling event without device field."""
        coordinator._handle_event_update(
            "motion",
            {
                "id": "event_no_device",
                "start": 1234567890,
                "end": None,
                # No "device" field
            },
        )

        # Events should still be stored
        assert "motion" in coordinator.data["events"]
        assert "event_no_device" in coordinator.data["events"]["motion"]

    def test_handle_event_update_resolves_camera_id_field(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test device id resolution tolerates "cameraId" (models/event.py's
        actual field name), not just the invented "device" key.

        UNVALIDATED (task 3): the real `Event` model
        (api/protect/models/event.py) has no generic "device" field - only
        `camera`/`cameraId` or `sensor`/`sensorId`. Before this fix,
        `_process_event_for_device` was unreachable dead code in practice
        because it only ever looked for "device".
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}

        coordinator._handle_event_update(
            "motion",
            {"id": "event20", "cameraId": "camera1", "start": 111, "end": None},
        )

        assert coordinator.data["cameras"]["camera1"]["lastMotionStart"] == 111

    def test_handle_event_update_smart_detect_type_alias(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test the "smartDetect" event type (models/event.py's
        EventType.SMART_DETECT) is accepted alongside the original
        "smartDetectZone" comparison - UNVALIDATED (task 3) which of the
        two (or both) a real frame actually uses.
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}

        coordinator._handle_event_update(
            "smartDetect",
            {
                "id": "event21",
                "device": "camera1",
                "smartDetectTypes": ["person"],
                "start": 111,
                "end": None,
            },
        )

        assert coordinator.data["cameras"]["camera1"]["lastSmartDetectTypes"] == [
            "person"
        ]

    # -- Task 1 / 3: the "events" WebSocket subscription adapter ------------

    def test_on_websocket_event_message_item_wrapper(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test the events adapter with fields nested under "item", mirroring
        the confirmed "devices" envelope shape (see _on_websocket_message).
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}

        coordinator._on_websocket_event_message(
            {
                "type": "add",
                "item": {
                    "type": "motion",
                    "id": "event30",
                    "camera": "camera1",
                    "start": 555,
                    "end": None,
                },
            }
        )

        assert "event30" in coordinator.data["events"]["motion"]
        assert coordinator.data["cameras"]["camera1"]["lastMotionStart"] == 555
        assert coordinator.data["cameras"]["camera1"]["lastMotionEnd"] is None

    def test_on_websocket_event_message_flat_frame(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test the events adapter with no item/payload/action wrapper - a
        flat frame's own top-level "type" is the real event type (there is
        no separate envelope to strip it from in this shape).
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}

        coordinator._on_websocket_event_message(
            {"type": "motion", "id": "event31", "camera": "camera1", "start": 777}
        )

        assert coordinator.data["cameras"]["camera1"]["lastMotionStart"] == 777

    def test_on_websocket_event_message_not_a_dict(
        self, coordinator: UnifiProtectCoordinator, caplog: pytest.LogCaptureFixture
    ):
        """Test the events adapter warns and drops non-dict payloads instead
        of raising (task 3: never raise into the WS callback).
        """
        with caplog.at_level(logging.WARNING):
            coordinator._on_websocket_event_message("not-a-dict")  # type: ignore[arg-type]

        assert "not a JSON object" in caplog.text

    def test_on_websocket_event_message_missing_type_warns_once_then_debug(
        self, coordinator: UnifiProtectCoordinator, caplog: pytest.LogCaptureFixture
    ):
        """Test an unparseable (missing event type) frame logs WARNING once,
        then DEBUG on repeat - mirrors the devices adapter's existing cap.
        """
        with caplog.at_level(logging.DEBUG):
            coordinator._on_websocket_event_message({"id": "event32"})
            caplog.clear()
            coordinator._on_websocket_event_message({"id": "event33"})

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert warning_records == []
        assert any("event33" in r.getMessage() for r in debug_records)

    @pytest.mark.parametrize(
        ("window_ms", "expected_delay"),
        [
            (1000, 1.0),
            (2500, 2.5),
            (None, 1.0),
            ("invalid", 1.0),
            (True, 1.0),
            (-1, 1.0),
            (float("inf"), 1.0),
            (10**1000, 1.0),
        ],
    )
    def test_on_websocket_event_rate_limit_defers_without_warning(
        self,
        coordinator: UnifiProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
        window_ms: Any,
        expected_delay: float,
    ) -> None:
        """A rate-limit frame defers requests without spending warning flags."""
        frame: dict[str, Any] = {
            "error": "Too many requests",
            "name": "TOO_MANY_REQUESTS_ERROR",
            "windowMs": window_ms,
            "limit": 10,
        }

        with caplog.at_level(logging.DEBUG):
            coordinator._on_websocket_event_message(frame)

        assert coordinator.protect_client is not None
        coordinator.protect_client._defer_after_rate_limit.assert_called_once_with(
            expected_delay
        )
        assert "requests deferred" in caplog.text
        assert "limit=10" in caplog.text
        assert not any(r.levelno == logging.WARNING for r in caplog.records)
        assert coordinator._ws_event_error_warned is False

    def test_on_websocket_event_rate_limit_in_nested_error(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """An error object can carry its own rate-limit fields."""
        coordinator._on_websocket_event_message(
            {
                "payload": {
                    "error": {"name": "TOO_MANY_REQUESTS_ERROR", "windowMs": 1500}
                }
            }
        )

        assert coordinator.protect_client is not None
        coordinator.protect_client._defer_after_rate_limit.assert_called_once_with(1.5)
        assert coordinator._ws_event_error_warned is False

    def test_on_websocket_event_rate_limit_without_protect_client(
        self,
        coordinator_no_protect: UnifiProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A late rate-limit frame is harmless after the Protect client is gone."""
        with caplog.at_level(logging.DEBUG):
            coordinator_no_protect._on_websocket_event_message(
                {
                    "error": "Too many requests",
                    "name": "TOO_MANY_REQUESTS_ERROR",
                    "windowMs": 1000,
                }
            )

        assert coordinator_no_protect._ws_event_error_warned is False
        assert coordinator_no_protect._ws_stream_health["events"]["last_message_at"]
        assert not any(r.levelno == logging.WARNING for r in caplog.records)

    def test_on_websocket_event_message_error_frame_warns_once_then_debug(
        self, coordinator: UnifiProtectCoordinator, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Other console errors still warn once, then log at DEBUG."""
        frame: dict[str, Any] = {
            "error": "Unexpected stream fault",
            "name": "UNEXPECTED_ERROR",
        }

        with caplog.at_level(logging.DEBUG):
            coordinator._on_websocket_event_message(dict(frame))
            first: list[logging.LogRecord] = [
                r for r in caplog.records if r.levelno == logging.WARNING
            ]
            assert any("reported an error" in r.getMessage() for r in first)
            assert not any("missing event type" in r.getMessage() for r in first)
            caplog.clear()
            coordinator._on_websocket_event_message(dict(frame))

        assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
        assert any(
            "reported an error" in r.getMessage()
            for r in caplog.records
            if r.levelno == logging.DEBUG
        )
        assert coordinator.protect_client is not None
        coordinator.protect_client._defer_after_rate_limit.assert_not_called()

    def test_on_websocket_event_message_error_frame_keeps_parse_warning(
        self, coordinator: UnifiProtectCoordinator, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test an error frame does not spend the unparseable-frame warning.

        The two share a stream but not a cause, so a genuinely malformed
        frame must still reach WARNING after an error frame has arrived.
        """
        with caplog.at_level(logging.DEBUG):
            coordinator._on_websocket_event_message(
                {
                    "error": "Too many requests",
                    "name": "TOO_MANY_REQUESTS_ERROR",
                    "windowMs": 1000,
                    "limit": 10,
                }
            )
            caplog.clear()
            coordinator._on_websocket_event_message({"id": "event41"})

        warnings: list[logging.LogRecord] = [
            r for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("missing event type" in r.getMessage() for r in warnings)

    def test_on_websocket_event_message_missing_id_is_dropped(
        self, coordinator: UnifiProtectCoordinator, caplog: pytest.LogCaptureFixture
    ):
        """Test a resolvable type with no id logs at debug and is dropped."""
        with caplog.at_level(logging.DEBUG):
            coordinator._on_websocket_event_message({"type": "motion"})

        assert "missing event id" in caplog.text
        assert coordinator.data["events"] == {}

    def test_on_websocket_event_message_never_raises_on_handler_error(
        self, coordinator: UnifiProtectCoordinator, caplog: pytest.LogCaptureFixture
    ):
        """Test an exception from `_handle_event_update` is logged and
        swallowed, not propagated into the WS callback (task 3: the events
        path has never run in production, so its schema assumptions must
        degrade safely rather than kill the reconnect loop).
        """
        with (
            patch.object(
                coordinator,
                "_handle_event_update",
                side_effect=RuntimeError("boom"),
            ),
            caplog.at_level(logging.ERROR),
        ):
            coordinator._on_websocket_event_message(
                {"type": "motion", "id": "event34", "device": "camera1"}
            )

        assert "unexpected error processing" in caplog.text.lower()

    # -- Task 2: bounded auto-off for the motion/smart-detect/ring latch ----

    def test_motion_latch_survives_within_timeout(
        self, coordinator: UnifiProtectCoordinator, freezer
    ):
        """Test reconciliation does not clear a latch that is still young."""
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        coordinator._handle_event_update(
            "motion",
            {"id": "event40", "device": "camera1", "start": 1, "end": None},
        )

        freezer.tick(STALE_EVENT_TIMEOUT - timedelta(seconds=1))
        coordinator._reconcile_stale_events()

        assert coordinator.data["cameras"]["camera1"]["lastMotionEnd"] is None

    def test_motion_latch_auto_clears_after_stale_timeout(
        self, coordinator: UnifiProtectCoordinator, freezer
    ):
        """CRITICAL regression test: a dropped "end" frame must not latch
        `camera_motion` ON forever - it must self-clear after
        STALE_EVENT_TIMEOUT rather than trusting event pairing alone. A
        permanently-ON motion sensor in a home security system is worse
        than one that never fires (see coordinator module docstring).
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        coordinator._handle_event_update(
            "motion",
            {"id": "event41", "device": "camera1", "start": 1, "end": None},
        )
        assert coordinator.data["cameras"]["camera1"]["lastMotionEnd"] is None

        freezer.tick(STALE_EVENT_TIMEOUT + timedelta(seconds=1))
        coordinator._reconcile_stale_events()

        assert coordinator.data["cameras"]["camera1"]["lastMotionEnd"] is not None

    def test_smart_detect_latch_auto_clears_and_resets_types(
        self, coordinator: UnifiProtectCoordinator, freezer
    ):
        """Test a stale smart-detect latch clears lastMotionEnd AND resets
        lastSmartDetectTypes, so person/vehicle/animal detection sensors
        also turn back off rather than staying latched on a stale type.
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        coordinator._handle_event_update(
            "smartDetectZone",
            {
                "id": "event42",
                "device": "camera1",
                "smartDetectTypes": ["person"],
                "start": 1,
                "end": None,
            },
        )

        freezer.tick(STALE_EVENT_TIMEOUT + timedelta(seconds=1))
        coordinator._reconcile_stale_events()

        assert coordinator.data["cameras"]["camera1"]["lastMotionEnd"] is not None
        assert coordinator.data["cameras"]["camera1"]["lastSmartDetectTypes"] == []

    def test_smart_detect_event_tolerates_non_list_smart_detect_types(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test a non-list smartDetectTypes value (unconfirmed real shape -
        see class docstring) degrades to an empty list instead of storing
        garbage or raising.
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}

        coordinator._handle_event_update(
            "smartDetectZone",
            {
                "id": "event46",
                "device": "camera1",
                "smartDetectTypes": "person",  # not a list
                "start": 1,
                "end": 2,
            },
        )

        assert coordinator.data["cameras"]["camera1"]["lastSmartDetectTypes"] == []

    def test_light_motion_latch_auto_clears_after_stale_timeout(
        self, coordinator: UnifiProtectCoordinator, freezer
    ):
        """Test the same bounded auto-off applies to light motion latches."""
        coordinator.data["lights"]["light1"] = {"id": "light1", "name": "Test"}
        coordinator._handle_event_update(
            "motion",
            {"id": "event43", "device": "light1", "start": 1, "end": None},
        )

        freezer.tick(STALE_EVENT_TIMEOUT + timedelta(seconds=1))
        coordinator._reconcile_stale_events()

        assert coordinator.data["lights"]["light1"]["lastMotionEnd"] is not None

    def test_ring_latch_auto_clears_after_stale_timeout(
        self, coordinator: UnifiProtectCoordinator, freezer
    ):
        """Test the doorbell ring latch is bounded the same way as motion -
        it is exposed to the identical dropped-end-frame risk once the
        events stream is live.
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        coordinator._handle_event_update(
            "ring",
            {"id": "event44", "device": "camera1", "start": 1, "end": None},
        )

        freezer.tick(STALE_EVENT_TIMEOUT + timedelta(seconds=1))
        coordinator._reconcile_stale_events()

        assert coordinator.data["cameras"]["camera1"]["lastRingEnd"] is not None

    def test_ring_latch_normal_end_event_clears_tracker(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test a normal paired ring "end" event pops the ring tracker
        immediately, mirroring the motion latch's normal-clear path.
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        coordinator._handle_event_update(
            "ring",
            {"id": "event47", "device": "camera1", "start": 1, "end": None},
        )
        assert "camera1" in coordinator._camera_ring_started

        coordinator._handle_event_update(
            "ring",
            {"id": "event47", "device": "camera1", "start": 1, "end": 2},
        )

        assert coordinator.data["cameras"]["camera1"]["lastRingEnd"] == 2
        assert "camera1" not in coordinator._camera_ring_started

    def test_reconcile_stale_events_skips_tracker_for_removed_device(
        self, coordinator: UnifiProtectCoordinator, freezer
    ):
        """Test reconciliation for a stale tracker entry whose device has
        since disappeared (e.g. removed by a REST poll rebuild) just drops
        the tracker entry instead of raising or recreating the device.
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        coordinator._handle_event_update(
            "motion",
            {"id": "event48", "device": "camera1", "start": 1, "end": None},
        )
        assert "camera1" in coordinator._camera_motion_started

        # Simulate the camera vanishing (e.g. a REST poll rebuild that no
        # longer includes it) before the timeout elapses.
        del coordinator.data["cameras"]["camera1"]

        freezer.tick(STALE_EVENT_TIMEOUT + timedelta(seconds=1))
        coordinator._reconcile_stale_events()

        assert "camera1" not in coordinator._camera_motion_started
        assert "camera1" not in coordinator.data["cameras"]

    def test_motion_latch_normal_end_event_clears_without_reconciliation(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test a normal paired "end" event clears the latch immediately -
        reconciliation is a safety net, not the primary clearing path.
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        coordinator._handle_event_update(
            "motion",
            {"id": "event45", "device": "camera1", "start": 1, "end": None},
        )
        assert "camera1" in coordinator._camera_motion_started

        coordinator._handle_event_update(
            "motion",
            {"id": "event45", "device": "camera1", "start": 1, "end": 2},
        )

        assert coordinator.data["cameras"]["camera1"]["lastMotionEnd"] == 2
        assert "camera1" not in coordinator._camera_motion_started

    @pytest.mark.asyncio
    async def test_reconcile_stale_events_runs_on_periodic_poll(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test the periodic REST poll also reconciles stale latches,
        rather than relying solely on event pairing.
        """
        with patch.object(coordinator, "_reconcile_stale_events") as mock_reconcile:
            await coordinator._async_update_data()

        mock_reconcile.assert_called_once()

    def test_reconcile_stale_events_runs_on_websocket_reconnect(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test a WS (re)connect triggers reconciliation - a missed "end"
        frame during the outage would otherwise wait for the next poll.

        Applies per-stream (the callback now needs a `stream` argument -
        see review finding 1), but a reconnect of EITHER subscription
        still triggers reconciliation, matching the original behavior.
        """
        with patch.object(coordinator, "_reconcile_stale_events") as mock_reconcile:
            coordinator._on_websocket_connection_state_change("devices", connected=True)

        mock_reconcile.assert_called_once()

        with patch.object(
            coordinator, "_reconcile_stale_events"
        ) as mock_reconcile_disconnect:
            coordinator._on_websocket_connection_state_change(
                "devices", connected=False
            )

        mock_reconcile_disconnect.assert_not_called()

    # -- Task 5: WebSocket health signal -------------------------------------

    def test_websocket_health_initial_state(self, coordinator: UnifiProtectCoordinator):
        """Test the WS health signal starts unconnected/unknown, per stream
        and in the top-level roll-up.
        """
        assert coordinator._ws_connected is False
        assert coordinator._last_ws_message is None
        assert coordinator.websocket_health == {
            "connected": False,
            "last_message_at": None,
            "devices": {"connected": False, "last_message_at": None},
            "events": {"connected": False, "last_message_at": None},
        }

    def test_websocket_health_updates_on_devices_frame(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test an inbound devices frame marks only the devices stream
        connected - even an unparseable one, since receiving anything is
        evidence that stream's wire is alive. The top-level roll-up must
        NOT report overall-healthy off of one stream alone (review finding
        1): the events subscription is still unknown/disconnected here.
        """
        coordinator._on_websocket_message({"id": "sensor50"})

        assert coordinator.websocket_health["devices"]["connected"] is True
        assert coordinator.websocket_health["devices"]["last_message_at"] is not None
        assert coordinator.websocket_health["events"]["connected"] is False
        assert coordinator.websocket_health["events"]["last_message_at"] is None

        assert coordinator._ws_connected is False
        assert coordinator.websocket_health["connected"] is False
        # The top-level "any wire alive" timestamp still advances.
        assert coordinator._last_ws_message is not None
        assert coordinator.websocket_health["last_message_at"] is not None

    def test_websocket_health_updates_on_events_frame(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test an inbound events frame marks only the events stream
        connected, mirroring the devices case above.
        """
        coordinator._on_websocket_event_message({"id": "event51"})

        assert coordinator.websocket_health["events"]["connected"] is True
        assert coordinator.websocket_health["events"]["last_message_at"] is not None
        assert coordinator.websocket_health["devices"]["connected"] is False

        assert coordinator._ws_connected is False
        assert coordinator._last_ws_message is not None

    def test_websocket_health_reflects_connection_state_changes(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test the connection-state callback drives per-stream state, and
        the top-level roll-up only reports connected once BOTH streams are.
        """
        coordinator._on_websocket_connection_state_change("devices", connected=True)
        assert coordinator.websocket_health["devices"]["connected"] is True
        assert coordinator._ws_connected is False  # events not connected yet

        coordinator._on_websocket_connection_state_change("events", connected=True)
        assert coordinator.websocket_health["events"]["connected"] is True
        assert coordinator._ws_connected is True  # both streams now connected

        coordinator._on_websocket_connection_state_change("devices", connected=False)
        assert coordinator.websocket_health["devices"]["connected"] is False
        assert coordinator.websocket_health["events"]["connected"] is True
        assert coordinator._ws_connected is False

    def test_on_devices_connection_state_change_updates_devices_stream_only(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test the bound wrapper registered as the devices subscription's
        `on_connection_state_change` callback (see `async_start_websocket`)
        updates only the devices stream's health entry.
        """
        coordinator._on_devices_connection_state_change(connected=True)

        assert coordinator.websocket_health["devices"]["connected"] is True
        assert coordinator.websocket_health["events"]["connected"] is False

    def test_on_events_connection_state_change_updates_events_stream_only(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test the bound wrapper registered as the events subscription's
        `on_connection_state_change` callback updates only the events
        stream's health entry.
        """
        coordinator._on_events_connection_state_change(connected=True)

        assert coordinator.websocket_health["events"]["connected"] is True
        assert coordinator.websocket_health["devices"]["connected"] is False

    def test_websocket_health_detects_half_dead_pair(
        self, coordinator: UnifiProtectCoordinator
    ):
        """CRITICAL regression test (review finding 1, Major): a chatty
        devices subscription must never mask a hung/disconnected events
        subscription in `websocket_health`.

        This health signal exists specifically to catch "motion silently
        stopped working for days and nobody noticed" - that failure mode is
        exactly an NVR restart where the devices stream reconnects cleanly
        and keeps delivering frames while the events stream hangs half-open
        (no error, no close frame, just blocked forever). Before this fix,
        both subscriptions wrote the same shared `_ws_connected`/
        `_last_ws_message` fields, so devices traffic alone kept
        `websocket_health` reporting `connected: True` with a fresh
        `last_message_at` while motion detection was silently dead.
        """
        # Devices subscription connects and stays chatty.
        coordinator._on_websocket_connection_state_change("devices", connected=True)
        coordinator._on_websocket_message({"id": "sensor52", "modelKey": "sensor"})
        coordinator._on_websocket_message({"id": "sensor52", "modelKey": "sensor"})

        # Events subscription connected once, then hung - no more frames,
        # no disconnect callback (that's exactly what a half-open hang
        # looks like: nothing fires, `async for msg in ws` just blocks).
        coordinator._on_websocket_connection_state_change("events", connected=True)
        coordinator._on_websocket_event_message({"id": "event52", "type": "motion"})

        health = coordinator.websocket_health

        # Per-stream detail must show the events stream as connected but
        # its own traffic is what a human would inspect for staleness -
        # the critical assertion is that the top-level summary does not
        # paper over an events-side outage with devices-side traffic.
        assert health["devices"]["connected"] is True
        assert health["events"]["connected"] is True

        # Now the events subscription actually drops (half-open hang
        # eventually surfaces as a connection-state transition once the
        # heartbeat/reconnect logic in websocket.py notices) while devices
        # keeps flowing.
        coordinator._on_websocket_connection_state_change("events", connected=False)
        coordinator._on_websocket_message({"id": "sensor52", "modelKey": "sensor"})

        health = coordinator.websocket_health
        assert health["devices"]["connected"] is True
        assert health["events"]["connected"] is False
        # The overall roll-up must reflect the outage, not the chatty
        # devices stream alone.
        assert health["connected"] is False
        assert coordinator._ws_connected is False

    def test_cleanup_stale_devices_no_match(
        self, hass: HomeAssistant, coordinator: UnifiProtectCoordinator
    ):
        """Test stale device cleanup when device not found in registry."""
        # Set up previous device IDs
        coordinator._previous_protect_device_ids = {
            "cameras": {"stale_camera"},
            "lights": {"stale_light"},
            "sensors": set(),
            "nvrs": set(),
            "viewers": {"stale_viewer"},
            "chimes": {"stale_chime"},
        }

        # Current data has nothing
        coordinator.data["cameras"] = {}
        coordinator.data["lights"] = {}
        coordinator.data["viewers"] = {}
        coordinator.data["chimes"] = {}

        # Mock device registry to return None (device not found)
        with patch(
            "custom_components.unifi_insights.coordinators.protect.dr.async_get"
        ) as mock_registry:
            set_mock_device_lookup(mock_registry.return_value, None)

            # Poll past the grace window so eviction is actually attempted,
            # then fall through both identifier patterns without a match.
            for _ in range(MAX_CONSECUTIVE_MISSING_POLLS + 1):
                coordinator._cleanup_stale_devices()

            # No device updates should happen (nothing found)
            mock_registry.return_value.async_update_device.assert_not_called()
            assert mock_device_lookup_method(
                mock_registry.return_value, coordinator.config_entry.entry_id
            ).called

    @pytest.mark.asyncio
    async def test_fetch_sensors_error(self, coordinator: UnifiProtectCoordinator):
        """Test sensor fetch handles errors gracefully."""
        coordinator.protect_client.sensors.get_all = AsyncMock(
            side_effect=Exception("Sensors error")
        )

        # Call the internal method directly
        await coordinator._fetch_sensors()

        # Should not raise, sensors should remain empty
        assert coordinator.data["sensors"] == {}

    @pytest.mark.asyncio
    async def test_fetch_sensors_preserves_cache_on_empty(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test sensor fetch preserves cached sensors across transient empty polls."""
        cached = {"sensor1": {"id": "sensor1", "state": "CONNECTED"}}
        coordinator.data["sensors"] = cached
        coordinator.protect_client.sensors.get_all = AsyncMock(return_value=[])

        # Polls 1, 2, 3 should preserve cache
        for _ in range(3):
            await coordinator._fetch_sensors()
            assert "sensor1" in coordinator.data["sensors"]

    @pytest.mark.asyncio
    async def test_fetch_sensors_clears_cache_after_max_consecutive_empty(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test sensor cache is cleared after MAX_CONSECUTIVE_EMPTY_FETCHES polls."""
        cached = {"sensor1": {"id": "sensor1", "state": "CONNECTED"}}
        coordinator.data["sensors"] = cached
        coordinator.protect_client.sensors.get_all = AsyncMock(return_value=[])

        # 4 consecutive empty polls exceeds threshold of 3
        for _ in range(4):
            await coordinator._fetch_sensors()

        assert coordinator.data["sensors"] == {}

    @pytest.mark.asyncio
    async def test_fetch_sensors_preserves_cache_on_404(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test sensor fetch preserves cached sensors across transient 404s."""
        cached = {"sensor1": {"id": "sensor1", "state": "CONNECTED"}}
        coordinator.data["sensors"] = cached
        coordinator.protect_client.sensors.get_all = AsyncMock(
            side_effect=UniFiNotFoundError("Not Found", 404)
        )

        for _ in range(3):
            await coordinator._fetch_sensors()
            assert "sensor1" in coordinator.data["sensors"]

    @pytest.mark.asyncio
    async def test_fetch_sensors_clears_cache_after_max_consecutive_404(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test sensor cache is cleared after consecutive 404s exceed threshold."""
        cached = {"sensor1": {"id": "sensor1", "state": "CONNECTED"}}
        coordinator.data["sensors"] = cached
        coordinator.protect_client.sensors.get_all = AsyncMock(
            side_effect=UniFiNotFoundError("Not Found", 404)
        )

        for _ in range(4):
            await coordinator._fetch_sensors()

        assert coordinator.data["sensors"] == {}

    @pytest.mark.asyncio
    async def test_fetch_sensors_404_when_no_sensors_configured(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test sensor fetch 404 leaves dict empty without error."""
        coordinator.data["sensors"] = {}
        coordinator.protect_client.sensors.get_all = AsyncMock(
            side_effect=UniFiNotFoundError("Not Found", 404)
        )

        await coordinator._fetch_sensors()
        assert coordinator.data["sensors"] == {}

    def test_update_device_collection_handles_non_dict_existing(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test _update_device_collection handles existing data being non-dict."""
        coordinator.data["cameras"] = None  # type: ignore[assignment]
        coordinator._update_device_collection("cameras", {})
        assert coordinator.data["cameras"] == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("collection", "client_attr", "client_method", "fetch_method"),
        [
            ("cameras", "cameras", "get_all", "_fetch_cameras"),
            ("lights", "lights", "get_all", "_fetch_lights"),
            ("nvrs", "nvr", "get", "_fetch_nvr"),
            ("chimes", "chimes", "get_all", "_fetch_chimes"),
            ("viewers", "viewers", "get_all", "_fetch_viewers"),
        ],
    )
    async def test_fetch_endpoints_preserve_cache_on_404(
        self,
        coordinator: UnifiProtectCoordinator,
        collection: str,
        client_attr: str,
        client_method: str,
        fetch_method: str,
    ):
        """Test fetch methods handle UniFiNotFoundError (404) by preserving cache."""
        device_id = f"{collection}_1"
        cached = {device_id: {"id": device_id}}
        coordinator.data[collection] = cached
        setattr(
            getattr(coordinator.protect_client, client_attr),
            client_method,
            AsyncMock(side_effect=UniFiNotFoundError("Not Found", 404)),
        )
        await getattr(coordinator, fetch_method)()
        assert device_id in coordinator.data[collection]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("collection", ["sensors", "nvrs", "chimes", "viewers"])
    async def test_fetch_preserves_cache_across_persistent_errors(
        self,
        hass: HomeAssistant,
        coordinator: UnifiProtectCoordinator,
        collection: str,
    ):
        """Test a persistent fetch error never evicts devices or registry entries.

        An error means "we could not ask", not "the server says they are gone",
        so it must not feed the empty-response eviction counter. When it did,
        roughly two minutes of HTTP 500s cleared the collection and
        _cleanup_stale_devices then removed every device in it from the device
        registry, losing area assignments and any automation keyed on device_id.
        """
        # collection -> (protect_client attribute, its fetch method, coordinator fetch)
        endpoints = {
            "sensors": ("sensors", "get_all", "_fetch_sensors"),
            "nvrs": ("nvr", "get", "_fetch_nvr"),
            "chimes": ("chimes", "get_all", "_fetch_chimes"),
            "viewers": ("viewers", "get_all", "_fetch_viewers"),
        }
        client_attr, client_method, fetch_method = endpoints[collection]

        device_id = f"{collection}_device1"
        coordinator.data[collection] = {device_id: {"id": device_id}}
        coordinator._previous_protect_device_ids = {
            key: set()
            for key in ("cameras", "lights", "sensors", "nvrs", "viewers", "chimes")
        }
        coordinator._previous_protect_device_ids[collection] = {device_id}
        setattr(
            getattr(coordinator.protect_client, client_attr),
            client_method,
            AsyncMock(side_effect=Exception("500 Internal Server Error")),
        )

        # Well past MAX_CONSECUTIVE_EMPTY_FETCHES: errors have no threshold.
        for _ in range(6):
            await getattr(coordinator, fetch_method)()
            assert device_id in coordinator.data[collection]

        assert coordinator._consecutive_empty_fetches.get(collection, 0) == 0

        with patch(
            "custom_components.unifi_insights.coordinators.protect.dr.async_get"
        ) as mock_registry:
            set_mock_device_lookup(mock_registry.return_value, MagicMock())
            coordinator._cleanup_stale_devices()
            mock_registry.return_value.async_update_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_nvr_missing_id_clears_cache_after_max_consecutive(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test NVR response without id is treated as empty."""
        cached = {"nvr1": {"id": "nvr1", "name": "NVR"}}
        coordinator.data["nvrs"] = cached
        mock_model = MagicMock()
        coordinator._model_to_dict = MagicMock(return_value={"name": "No ID"})
        coordinator.protect_client.nvr.get = AsyncMock(return_value=mock_model)

        for _ in range(3):
            await coordinator._fetch_nvr()
            assert "nvr1" in coordinator.data["nvrs"]

        await coordinator._fetch_nvr()
        assert coordinator.data["nvrs"] == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "empty_response",
        [
            None,
            {},
            {"name": "No ID NVR"},
        ],
        ids=["none", "empty_dict", "id_less_record"],
    )
    async def test_fetch_nvr_empty_responses_preserve_then_clear_cache(
        self, coordinator: UnifiProtectCoordinator, empty_response: Any
    ) -> None:
        """Test NVR responses with None, empty dict, or ID-less record preserve
        cache for MAX_CONSECUTIVE_EMPTY_FETCHES polls and then clear it,
        without mocking _model_to_dict.
        """
        cached = {"nvr1": {"id": "nvr1", "name": "NVR"}}
        coordinator.data["nvrs"] = dict(cached)
        coordinator._consecutive_empty_fetches["nvrs"] = 0
        coordinator.protect_client.nvr.get = AsyncMock(return_value=empty_response)

        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._fetch_nvr()
            assert "nvr1" in coordinator.data["nvrs"]

        await coordinator._fetch_nvr()
        assert coordinator.data["nvrs"] == {}

    @pytest.mark.asyncio
    async def test_fetch_cameras_preserves_cache_on_transient_empty(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test camera fetch preserves cache across transient empty polls."""
        cached = {"camera1": {"id": "camera1", "state": "CONNECTED"}}
        coordinator.data["cameras"] = cached
        coordinator.protect_client.cameras.get_all = AsyncMock(return_value=[])

        await coordinator._fetch_cameras()
        assert "camera1" in coordinator.data["cameras"]

    @pytest.mark.asyncio
    async def test_fetch_cameras_clears_cache_after_max_consecutive_empty(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test camera cache is cleared after consecutive empty polls."""
        cached = {"camera1": {"id": "camera1", "state": "CONNECTED"}}
        coordinator.data["cameras"] = cached
        coordinator.protect_client.cameras.get_all = AsyncMock(return_value=[])

        for _ in range(4):
            await coordinator._fetch_cameras()

        assert coordinator.data["cameras"] == {}

    @pytest.mark.asyncio
    async def test_fetch_lights_preserves_cache_on_transient_empty(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test light fetch preserves cached lights across transient empty responses."""
        cached = {"light1": {"id": "light1", "state": "CONNECTED"}}
        coordinator.data["lights"] = cached
        coordinator.protect_client.lights.get_all = AsyncMock(return_value=[])

        await coordinator._fetch_lights()
        assert "light1" in coordinator.data["lights"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "err",
        [
            # The session-drop the controller serves as an HTML login page.
            pytest.param(
                UniFiResponseError("Response is not JSON", status_code=200),
                id="response_error",
            ),
            pytest.param(UniFiConnectionError("Connection reset"), id="connection"),
            pytest.param(UniFiTimeoutError("Timed out"), id="timeout"),
        ],
    )
    async def test_fetch_cameras_preserves_cache_on_transient_error(
        self, coordinator: UnifiProtectCoordinator, err: Exception
    ):
        """A transient camera fetch error must not fail the whole Protect poll.

        `_fetch_cameras` runs first, so letting the error escape aborts every
        later fetcher and raises `UpdateFailed`, marking all Protect entities
        unavailable for that cycle.
        """
        cached = {"camera1": {"id": "camera1", "state": "CONNECTED"}}
        coordinator.data["cameras"] = cached
        coordinator.protect_client.cameras.get_all = AsyncMock(side_effect=err)

        await coordinator._fetch_cameras()

        assert "camera1" in coordinator.data["cameras"]

    @pytest.mark.asyncio
    async def test_fetch_lights_preserves_cache_on_transient_error(
        self, coordinator: UnifiProtectCoordinator
    ):
        """A transient light fetch error must not fail the whole Protect poll."""
        cached = {"light1": {"id": "light1", "state": "CONNECTED"}}
        coordinator.data["lights"] = cached
        coordinator.protect_client.lights.get_all = AsyncMock(
            side_effect=UniFiResponseError("Response is not JSON", status_code=200)
        )

        await coordinator._fetch_lights()

        assert "light1" in coordinator.data["lights"]

    @pytest.mark.asyncio
    async def test_fetch_cameras_transient_error_does_not_advance_eviction(
        self, coordinator: UnifiProtectCoordinator
    ):
        """A fetch error is not evidence a camera was removed, unlike an empty poll.

        An empty response clears the cache after MAX_CONSECUTIVE_EMPTY_FETCHES so
        genuinely removed devices are cleaned up. An error says nothing about the
        device set, so it must not advance that counter: errored polls followed by
        a single empty one must still preserve the cache.
        """
        cached = {"camera1": {"id": "camera1", "state": "CONNECTED"}}
        coordinator.data["cameras"] = cached
        coordinator.protect_client.cameras.get_all = AsyncMock(
            side_effect=UniFiResponseError("Bad gateway", status_code=502)
        )
        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._fetch_cameras()

        # First clean poll after the outage genuinely returns no cameras.
        coordinator.protect_client.cameras.get_all = AsyncMock(return_value=[])
        await coordinator._fetch_cameras()

        assert "camera1" in coordinator.data["cameras"]

    @pytest.mark.asyncio
    async def test_sustained_camera_fetch_errors_fail_the_poll(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Preservation is bounded: a lasting outage must mark Protect unavailable.

        Serving a warm cache forever would leave every Protect entity reporting
        stale state through a controller outage, which is worse than the flapping
        the bounded window prevents.
        """
        coordinator.data["cameras"] = {"camera1": {"id": "camera1"}}
        coordinator.protect_client.cameras.get_all = AsyncMock(
            side_effect=UniFiResponseError("Response is not JSON", status_code=200)
        )

        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._fetch_cameras()

        with pytest.raises(UniFiResponseError):
            await coordinator._fetch_cameras()

    @pytest.mark.asyncio
    async def test_camera_fetch_error_counter_resets_on_handled_404(
        self, coordinator: UnifiProtectCoordinator
    ):
        """A 404 is a healthy answer, so it breaks the transient-error streak.

        The endpoint replied, which is evidence the session is alive. Without
        the reset, three absorbed errors plus a 404 plus one more error would
        fail the poll on errors that were never consecutive.
        """
        coordinator.data["cameras"] = {"camera1": {"id": "camera1"}}
        coordinator.protect_client.cameras.get_all = AsyncMock(
            side_effect=UniFiResponseError("Blip", status_code=502)
        )
        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._fetch_cameras()

        coordinator.protect_client.cameras.get_all = AsyncMock(
            side_effect=UniFiNotFoundError("No cameras configured", 404)
        )
        await coordinator._fetch_cameras()

        # Streak was broken, so this error is the first of a new window.
        coordinator.protect_client.cameras.get_all = AsyncMock(
            side_effect=UniFiResponseError("Blip", status_code=502)
        )
        await coordinator._fetch_cameras()

    @pytest.mark.asyncio
    async def test_light_fetch_error_counter_resets_on_handled_404(
        self, coordinator: UnifiProtectCoordinator
    ):
        """See the camera case: a handled 404 resets the light error streak."""
        coordinator.data["lights"] = {"light1": {"id": "light1"}}
        coordinator.protect_client.lights.get_all = AsyncMock(
            side_effect=UniFiResponseError("Blip", status_code=502)
        )
        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._fetch_lights()

        coordinator.protect_client.lights.get_all = AsyncMock(
            side_effect=UniFiNotFoundError("No lights configured", 404)
        )
        await coordinator._fetch_lights()

        coordinator.protect_client.lights.get_all = AsyncMock(
            side_effect=UniFiResponseError("Blip", status_code=502)
        )
        await coordinator._fetch_lights()

    @pytest.mark.asyncio
    async def test_camera_fetch_error_counter_resets_on_success(
        self, coordinator: UnifiProtectCoordinator
    ):
        """A recovered poll re-arms the full preservation window."""
        coordinator.data["cameras"] = {"camera1": {"id": "camera1"}}
        coordinator.protect_client.cameras.get_all = AsyncMock(
            side_effect=UniFiResponseError("Blip", status_code=502)
        )
        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._fetch_cameras()

        camera = MagicMock()
        camera.model_dump = MagicMock(return_value={"id": "camera1", "name": "Cam"})
        coordinator.protect_client.cameras.get_all = AsyncMock(return_value=[camera])
        await coordinator._fetch_cameras()

        # Window is re-armed, so the next error is absorbed rather than raised.
        coordinator.protect_client.cameras.get_all = AsyncMock(
            side_effect=UniFiResponseError("Blip", status_code=502)
        )
        await coordinator._fetch_cameras()

        assert "camera1" in coordinator.data["cameras"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["_fetch_cameras", "_fetch_lights"])
    async def test_fetch_propagates_auth_error(
        self, coordinator: UnifiProtectCoordinator, method: str
    ):
        """An expired key must still reach the reauth flow, not be swallowed."""
        collection = "cameras" if method == "_fetch_cameras" else "lights"
        getattr(coordinator.protect_client, collection).get_all = AsyncMock(
            side_effect=UniFiAuthenticationError("API key expired")
        )

        with pytest.raises(UniFiAuthenticationError):
            await getattr(coordinator, method)()

    @pytest.mark.asyncio
    async def test_cleanup_stale_devices_preserves_devices_on_transient_empty(
        self, hass: HomeAssistant, coordinator: UnifiProtectCoordinator
    ):
        """Test stale device cleanup preserves devices during transient empty poll."""
        coordinator._previous_protect_device_ids = {
            "cameras": set(),
            "lights": set(),
            "sensors": {"sensor1"},
            "nvrs": set(),
            "viewers": set(),
            "chimes": set(),
        }
        coordinator.data["sensors"] = {"sensor1": {"id": "sensor1"}}
        coordinator.protect_client.sensors.get_all = AsyncMock(return_value=[])

        # 1 transient empty fetch
        await coordinator._fetch_sensors()
        assert "sensor1" in coordinator.data["sensors"]

        with patch(
            "custom_components.unifi_insights.coordinators.protect.dr.async_get"
        ) as mock_registry:
            mock_device = MagicMock()
            set_mock_device_lookup(mock_registry.return_value, mock_device)
            coordinator._cleanup_stale_devices()
            mock_registry.return_value.async_update_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_stale_devices_removes_device_after_max_consecutive_empty(
        self, hass: HomeAssistant, coordinator: UnifiProtectCoordinator
    ):
        """Test stale device cleanup removes devices after max empty polls."""
        coordinator._previous_protect_device_ids = {
            "cameras": set(),
            "lights": set(),
            "sensors": {"sensor1"},
            "nvrs": set(),
            "viewers": set(),
            "chimes": set(),
        }
        coordinator.data["sensors"] = {"sensor1": {"id": "sensor1"}}
        coordinator.protect_client.sensors.get_all = AsyncMock(return_value=[])

        # 4 consecutive empty polls
        for _ in range(4):
            await coordinator._fetch_sensors()

        assert coordinator.data["sensors"] == {}

        with patch(
            "custom_components.unifi_insights.coordinators.protect.dr.async_get"
        ) as mock_registry:
            mock_device = MagicMock()
            mock_device.id = "sensor1_entry_id"
            set_mock_device_lookup(mock_registry.return_value, mock_device)
            # Removal waits out the MAX_CONSECUTIVE_MISSING_POLLS grace window.
            for _ in range(MAX_CONSECUTIVE_MISSING_POLLS + 1):
                coordinator._cleanup_stale_devices()
            mock_registry.return_value.async_update_device.assert_called()

    @pytest.mark.asyncio
    async def test_fetch_cameras_preserves_camera_skipped_by_validation(
        self, hass: HomeAssistant, coordinator: UnifiProtectCoordinator
    ):
        """Test a camera dropped on a ValidationError keeps its cached entry.

        `get_all()` skips an item whose payload will not parse - typically a
        field reshaped by a Protect release - and returns the rest. That short
        list is non-empty, so the bounded empty-response guard never engages
        and the grace window only delays the loss: the skip is deterministic
        and repeats on every poll. The collection must merge instead of
        replace while the endpoint reports the result incomplete.
        """
        coordinator.data["cameras"] = {
            "camera1": {"id": "camera1", "name": "Front"},
            "camera2": {"id": "camera2", "name": "Back"},
        }
        coordinator._previous_protect_device_ids["cameras"] = {"camera1", "camera2"}

        mock_camera = MagicMock()
        mock_camera.model_dump = MagicMock(
            return_value={"id": "camera1", "name": "Front"}
        )
        coordinator.protect_client.cameras.get_all = AsyncMock(
            return_value=[mock_camera]
        )
        coordinator.protect_client.cameras.last_result_complete = False

        with patch(
            "custom_components.unifi_insights.coordinators.protect.dr.async_get"
        ) as mock_registry:
            set_mock_device_lookup(mock_registry.return_value, MagicMock())
            for _ in range(MAX_CONSECUTIVE_MISSING_POLLS + 2):
                await coordinator._fetch_cameras()
                coordinator._cleanup_stale_devices()

            assert "camera2" in coordinator.data["cameras"]
            mock_registry.return_value.async_update_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_cameras_still_refreshes_data_when_result_incomplete(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test preserving a skipped camera does not stall the others' data.

        Merging must layer the fresh response over the cache, not fall back to
        it, or one unparseable camera would freeze every sibling's state.
        """
        coordinator.data["cameras"] = {
            "camera1": {"id": "camera1", "state": "DISCONNECTED"},
            "camera2": {"id": "camera2", "state": "CONNECTED"},
        }

        mock_camera = MagicMock()
        mock_camera.model_dump = MagicMock(
            return_value={"id": "camera1", "state": "CONNECTED"}
        )
        coordinator.protect_client.cameras.get_all = AsyncMock(
            return_value=[mock_camera]
        )
        coordinator.protect_client.cameras.last_result_complete = False

        await coordinator._fetch_cameras()

        assert coordinator.data["cameras"]["camera1"]["state"] == "CONNECTED"
        assert coordinator.data["cameras"]["camera2"]["state"] == "CONNECTED"

    @pytest.mark.asyncio
    async def test_fetch_cameras_replaces_collection_when_result_complete(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test a complete response still drops cameras removed from Protect.

        The merge is scoped to incomplete results; a clean listing remains
        authoritative so genuinely unadopted cameras are cleaned up.
        """
        coordinator.data["cameras"] = {
            "camera1": {"id": "camera1"},
            "camera2": {"id": "camera2"},
        }

        mock_camera = MagicMock()
        mock_camera.model_dump = MagicMock(return_value={"id": "camera1"})
        coordinator.protect_client.cameras.get_all = AsyncMock(
            return_value=[mock_camera]
        )
        coordinator.protect_client.cameras.last_result_complete = True

        await coordinator._fetch_cameras()

        assert set(coordinator.data["cameras"]) == {"camera1"}

    @pytest.mark.asyncio
    async def test_fetch_cameras_preserves_cache_when_every_camera_fails_parsing(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test a schema break that drops every camera does not purge the cache.

        A Protect release reshaping a shared field fails validation for all
        cameras at once, so `get_all()` returns an empty list that looks
        exactly like "no cameras adopted". Left there, the bounded
        empty-response guard clears the collection a few polls later and hands
        every camera to `_cleanup_stale_devices`. An endpoint that reports the
        result incomplete must not advance that counter.
        """
        coordinator.data["cameras"] = {"camera1": {"id": "camera1"}}
        coordinator.protect_client.cameras.get_all = AsyncMock(return_value=[])
        coordinator.protect_client.cameras.last_result_complete = False

        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES + 3):
            await coordinator._fetch_cameras()

        assert "camera1" in coordinator.data["cameras"]
        assert coordinator._consecutive_empty_fetches["cameras"] == 0

    @pytest.mark.asyncio
    async def test_cleanup_stale_devices_defers_removal_on_brief_absence(
        self, hass: HomeAssistant, coordinator: UnifiProtectCoordinator
    ):
        """Test a device missing from one poll is not purged from the registry.

        A partial controller response - or an item that `get_all()` skipped on
        a ValidationError - drops a still-adopted device out of the collection.
        Acting on that single poll is irreversible: the registry entry, its
        area assignment and every automation keyed on device_id are lost.
        """
        coordinator._previous_protect_device_ids["cameras"] = {"cam1", "cam2"}
        coordinator.data["cameras"] = {"cam1": {"id": "cam1"}}

        with patch(
            "custom_components.unifi_insights.coordinators.protect.dr.async_get"
        ) as mock_registry:
            set_mock_device_lookup(mock_registry.return_value, MagicMock())
            for _ in range(MAX_CONSECUTIVE_MISSING_POLLS):
                coordinator._cleanup_stale_devices()

            mock_registry.return_value.async_update_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_stale_devices_removes_device_after_sustained_absence(
        self, hass: HomeAssistant, coordinator: UnifiProtectCoordinator
    ):
        """Test a device absent past the grace window is still evicted.

        Guards the other half of the deferral: a device genuinely unadopted
        from Protect must not be tracked forever just because removal is no
        longer immediate (Gold stale-device requirement).
        """
        coordinator._previous_protect_device_ids["cameras"] = {"cam1", "cam2"}
        coordinator.data["cameras"] = {"cam1": {"id": "cam1"}}
        mock_device = MagicMock()
        mock_device.id = "registry_id_cam2"

        with patch(
            "custom_components.unifi_insights.coordinators.protect.dr.async_get"
        ) as mock_registry:
            set_mock_device_lookup(mock_registry.return_value, mock_device)
            for _ in range(MAX_CONSECUTIVE_MISSING_POLLS + 1):
                coordinator._cleanup_stale_devices()

            mock_registry.return_value.async_update_device.assert_called_once_with(
                device_id="registry_id_cam2",
                remove_config_entry_id=coordinator.config_entry.entry_id,
            )

    @pytest.mark.asyncio
    async def test_cleanup_stale_devices_resets_absence_counter_on_return(
        self, hass: HomeAssistant, coordinator: UnifiProtectCoordinator
    ):
        """Test a device that reappears restarts its grace window.

        Without a reset, intermittent partial responses would accumulate
        across unrelated polls and eventually evict a healthy device.
        """
        coordinator._previous_protect_device_ids["cameras"] = {"cam1", "cam2"}

        with patch(
            "custom_components.unifi_insights.coordinators.protect.dr.async_get"
        ) as mock_registry:
            set_mock_device_lookup(mock_registry.return_value, MagicMock())
            # Absent for one poll short of the threshold.
            coordinator.data["cameras"] = {"cam1": {"id": "cam1"}}
            for _ in range(MAX_CONSECUTIVE_MISSING_POLLS):
                coordinator._cleanup_stale_devices()

            # cam2 comes back, then goes missing again for the same span.
            coordinator.data["cameras"] = {
                "cam1": {"id": "cam1"},
                "cam2": {"id": "cam2"},
            }
            coordinator._cleanup_stale_devices()

            coordinator.data["cameras"] = {"cam1": {"id": "cam1"}}
            for _ in range(MAX_CONSECUTIVE_MISSING_POLLS):
                coordinator._cleanup_stale_devices()

            mock_registry.return_value.async_update_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_nvr_error(self, coordinator: UnifiProtectCoordinator):
        """Test NVR fetch handles errors gracefully."""
        coordinator.protect_client.nvr.get = AsyncMock(
            side_effect=Exception("NVR error")
        )

        # Call the internal method directly
        await coordinator._fetch_nvr()

        # Should not raise, nvrs should remain empty
        assert coordinator.data["nvrs"] == {}

    @pytest.mark.asyncio
    async def test_fetch_cameras_processes_smart_detect_types(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test camera fetch extracts smartDetectTypes from featureFlags."""
        mock_camera = MagicMock()
        mock_camera.model_dump = MagicMock(
            return_value={
                "id": "camera1",
                "name": "Front Camera",
                "featureFlags": {
                    "smartDetectTypes": ["person", "vehicle"],
                },
                "isPtz": True,
            }
        )
        coordinator.protect_client.cameras.get_all = AsyncMock(
            return_value=[mock_camera]
        )

        await coordinator._fetch_cameras()

        assert "camera1" in coordinator.data["cameras"]
        assert coordinator.data["cameras"]["camera1"]["smartDetectTypes"] == [
            "person",
            "vehicle",
        ]
        assert coordinator.data["cameras"]["camera1"]["hasPtz"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            {"featureFlags": "not_a_dict"},
            {"feature_flags": "x"},
            {"featureFlags": "not_a_dict", "feature_flags": "x"},
            {"feature_flags": None},
            {"featureFlags": "not_a_dict", "feature_flags": None},
            {"feature_flags": []},
            {"featureFlags": "not_a_dict", "feature_flags": []},
            {"feature_flags": 5},
            {"featureFlags": "not_a_dict", "feature_flags": 5},
        ],
        ids=[
            "camel_non_dict",
            "snake_str_absent_camel",
            "snake_str_non_dict_camel",
            "snake_none_absent_camel",
            "snake_none_non_dict_camel",
            "snake_list_absent_camel",
            "snake_list_non_dict_camel",
            "snake_int_absent_camel",
            "snake_int_non_dict_camel",
        ],
    )
    async def test_fetch_cameras_non_dict_feature_flags(
        self, coordinator: UnifiProtectCoordinator, payload: dict[str, Any]
    ) -> None:
        """Test camera fetch handles non-dict featureFlags and
        snake-case feature_flags.
        """
        mock_camera = MagicMock()
        mock_camera.model_dump = MagicMock(
            return_value={
                "id": "camera2",
                "name": "Camera 2",
                **payload,
            }
        )
        coordinator.protect_client.cameras.get_all = AsyncMock(
            return_value=[mock_camera]
        )

        await coordinator._fetch_cameras()

        assert "camera2" in coordinator.data["cameras"]
        # Should default to empty list when featureFlags is not a dict
        assert coordinator.data["cameras"]["camera2"]["smartDetectTypes"] == []
        assert coordinator.data["cameras"]["camera2"]["featureFlags"] == {}

    @pytest.mark.asyncio
    async def test_fetch_cameras_valid_snake_case_feature_flags(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """Test camera fetch normalises valid snake-case feature_flags into
        featureFlags.
        """
        mock_camera = MagicMock()
        mock_camera.model_dump = MagicMock(
            return_value={
                "id": "camera2",
                "name": "Camera 2",
                "feature_flags": {"smartDetectTypes": ["person"]},
            }
        )
        coordinator.protect_client.cameras.get_all = AsyncMock(
            return_value=[mock_camera]
        )

        await coordinator._fetch_cameras()

        assert "camera2" in coordinator.data["cameras"]
        assert coordinator.data["cameras"]["camera2"]["featureFlags"] == {
            "smartDetectTypes": ["person"]
        }
        assert coordinator.data["cameras"]["camera2"]["smartDetectTypes"] == ["person"]

    @pytest.mark.asyncio
    async def test_fetch_cameras_no_feature_flags(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test camera fetch handles missing featureFlags."""
        mock_camera = MagicMock()
        mock_camera.model_dump = MagicMock(
            return_value={
                "id": "camera3",
                "name": "Camera 3",
                # No featureFlags key
            }
        )
        coordinator.protect_client.cameras.get_all = AsyncMock(
            return_value=[mock_camera]
        )

        await coordinator._fetch_cameras()

        assert "camera3" in coordinator.data["cameras"]
        assert coordinator.data["cameras"]["camera3"]["smartDetectTypes"] == []

    @pytest.mark.asyncio
    async def test_fetch_cameras_rebuild_drops_motion_tracker_for_fieldless_camera(
        self,
        coordinator: UnifiProtectCoordinator,
        freezer,
        caplog: pytest.LogCaptureFixture,
    ):
        """Test a `_fetch_cameras()` rebuild pops the in-progress motion
        latch tracker for a camera whose rebuilt dict carries no
        `lastMotionStart` field, instead of leaving it to be "discovered"
        stale 5 minutes later.

        Regression test (review finding 2): the REST camera model never
        carries `lastMotionStart`/`lastMotionEnd` (those are only ever
        written by a paired WebSocket "start"/"end" event - see
        `_apply_motion_event`). Before this fix, a `_fetch_cameras()`
        rebuild silently cleared the latch's own fields without popping the
        tracker entry, so on virtually every real motion event
        `_reconcile_stale_events` would later "discover" the orphaned
        tracker and log a false "missed 'end' event?" warning for motion
        detection that was already correctly cleared ~4m30s earlier by the
        REST poll - flooding INFO logs during exactly the window someone is
        watching them to confirm a deploy worked.
        """
        # A live event-derived latch: WebSocket saw a "start" with no "end"
        # yet, so the tracker is armed - mirrors real motion detection.
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        coordinator._handle_event_update(
            "motion",
            {"id": "event49", "device": "camera1", "start": 1, "end": None},
        )
        assert "camera1" in coordinator._camera_motion_started
        assert coordinator.data["cameras"]["camera1"]["lastMotionStart"] == 1

        # The mock protect client's default camera fixture returns
        # "camera1" but with no lastMotionStart/lastMotionEnd fields at all
        # (matching the real REST model - see api/protect/models/camera.py).
        await coordinator._fetch_cameras()

        assert "lastMotionStart" not in coordinator.data["cameras"]["camera1"]
        assert "camera1" not in coordinator._camera_motion_started

        # 5+ minutes later, the periodic reconciliation safety net must not
        # find (and log about) a tracker that no longer exists.
        freezer.tick(STALE_EVENT_TIMEOUT + timedelta(seconds=1))
        with caplog.at_level(logging.INFO):
            coordinator._reconcile_stale_events()

        assert "missed 'end' event" not in caplog.text

    @pytest.mark.asyncio
    async def test_fetch_cameras_rebuild_drops_ring_tracker_for_fieldless_camera(
        self,
        coordinator: UnifiProtectCoordinator,
        freezer,
        caplog: pytest.LogCaptureFixture,
    ):
        """Test the identical fix also applies to the doorbell ring latch.

        The ring latch is stored on the same per-camera dict that
        `_fetch_cameras()` wholesale-replaces, so it is exposed to the
        identical dropped-tracker risk as the motion latch above.
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        coordinator._handle_event_update(
            "ring",
            {"id": "event50", "device": "camera1", "start": 1, "end": None},
        )
        assert "camera1" in coordinator._camera_ring_started

        await coordinator._fetch_cameras()

        assert "lastRingStart" not in coordinator.data["cameras"]["camera1"]
        assert "camera1" not in coordinator._camera_ring_started

        freezer.tick(STALE_EVENT_TIMEOUT + timedelta(seconds=1))
        with caplog.at_level(logging.INFO):
            coordinator._reconcile_stale_events()

        assert "missed 'end' event" not in caplog.text

    @pytest.mark.asyncio
    async def test_async_update_data_response_error(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test a sustained response error eventually fails the poll.

        The first MAX_CONSECUTIVE_EMPTY_FETCHES polls are absorbed so a single
        blipped endpoint does not take every Protect entity unavailable; past
        that window the error propagates and the coordinator reports failure.
        """
        coordinator.protect_client.cameras.get_all = AsyncMock(
            side_effect=UniFiResponseError("Invalid response", status_code=400)
        )

        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._async_update_data()

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_timeout_error(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test a sustained timeout error eventually fails the poll.

        The first MAX_CONSECUTIVE_EMPTY_FETCHES polls are absorbed so a single
        blipped endpoint does not take every Protect entity unavailable; past
        that window the error propagates and the coordinator reports failure.
        """
        coordinator.protect_client.cameras.get_all = AsyncMock(
            side_effect=UniFiTimeoutError("Request timed out")
        )

        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._async_update_data()

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_generic_error(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test data fetch with generic error."""
        coordinator.protect_client.cameras.get_all = AsyncMock(
            side_effect=Exception("Unknown error")
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_fetch_lights_no_protect_client(
        self, coordinator_no_protect: UnifiProtectCoordinator
    ):
        """Test light fetch returns early without protect client."""
        await coordinator_no_protect._fetch_lights()
        # Should not raise, lights remain empty
        assert coordinator_no_protect.data["lights"] == {}

    @pytest.mark.asyncio
    async def test_fetch_sensors_no_protect_client(
        self, coordinator_no_protect: UnifiProtectCoordinator
    ):
        """Test sensor fetch returns early without protect client."""
        await coordinator_no_protect._fetch_sensors()
        assert coordinator_no_protect.data["sensors"] == {}

    @pytest.mark.asyncio
    async def test_fetch_nvr_no_protect_client(
        self, coordinator_no_protect: UnifiProtectCoordinator
    ):
        """Test NVR fetch returns early without protect client."""
        await coordinator_no_protect._fetch_nvr()
        assert coordinator_no_protect.data["nvrs"] == {}

    @pytest.mark.asyncio
    async def test_fetch_chimes_no_protect_client(
        self, coordinator_no_protect: UnifiProtectCoordinator
    ):
        """Test chime fetch returns early without protect client."""
        await coordinator_no_protect._fetch_chimes()
        assert coordinator_no_protect.data["chimes"] == {}

    @pytest.mark.asyncio
    async def test_fetch_viewers_no_protect_client(
        self, coordinator_no_protect: UnifiProtectCoordinator
    ):
        """Test viewer fetch returns early without protect client."""
        await coordinator_no_protect._fetch_viewers()
        assert coordinator_no_protect.data["viewers"] == {}

    @pytest.mark.asyncio
    async def test_fetch_liveviews_no_protect_client(
        self, coordinator_no_protect: UnifiProtectCoordinator
    ):
        """Test liveview fetch returns early without protect client."""
        await coordinator_no_protect._fetch_liveviews()
        assert coordinator_no_protect.data["liveviews"] == {}

    @pytest.mark.asyncio
    async def test_fetch_cameras_no_protect_client(
        self, coordinator_no_protect: UnifiProtectCoordinator
    ):
        """Test camera fetch returns early without protect client."""
        await coordinator_no_protect._fetch_cameras()
        assert coordinator_no_protect.data["cameras"] == {}

    @pytest.mark.asyncio
    async def test_async_stop_websocket_no_protect_websocket(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test async_stop_websocket when _protect_websocket is None."""
        coordinator._protect_websocket = None
        await coordinator.async_stop_websocket()
        assert coordinator.websocket_task is None
        assert coordinator.events_websocket_task is None
        assert coordinator._sensor_refresh_task is None
        assert coordinator._sensor_reconcile_task is None
        assert coordinator._sensor_reconnect_task is None

    def test_handle_device_update_doorlock(self, coordinator: UnifiProtectCoordinator):
        """Test handling doorlock device raw dictionary merge."""
        coordinator.data["doorlocks"] = {
            "lock1": {"id": "lock1", "name": "Front Door", "state": "LOCKED"}
        }
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        coordinator._handle_device_update(
            "doorlock", {"id": "lock1", "state": "UNLOCKED", "battery": 90}
        )

        assert coordinator.data["doorlocks"]["lock1"] == {
            "id": "lock1",
            "name": "Front Door",
            "state": "UNLOCKED",
            "battery": 90,
        }
        listener.assert_called_once()

    def test_handle_device_update_viewport(self, coordinator: UnifiProtectCoordinator):
        """Test handling viewport device raw dictionary merge."""
        coordinator.data["viewports"] = {
            "vp1": {"id": "vp1", "name": "Viewport 1", "state": "ONLINE"}
        }
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        coordinator._handle_device_update(
            "viewport", {"id": "vp1", "name": "Renamed Viewport"}
        )

        assert coordinator.data["viewports"]["vp1"] == {
            "id": "vp1",
            "name": "Renamed Viewport",
            "state": "ONLINE",
        }
        listener.assert_called_once()

    def test_normalize_camera_data_sparse_defensive_fallbacks(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Cover smart-detect and PTZ fallback arms with sparse dictionaries."""
        # 1. Sparse camera dictionary with legacy snake_case fields (defensive fallback)
        sparse_cam = {
            "id": "cam_sparse",
            "name": "Sparse Camera",
            "smart_detect_types": ["person", "vehicle"],
            "is_ptz": True,
        }
        normalized = coordinator._normalize_camera_data(sparse_cam)
        assert normalized["smartDetectTypes"] == ["person", "vehicle"]
        assert normalized["isPtz"] is True

        # Sparse camera with legacy is_ptz=False fallback
        sparse_cam_no_ptz = {
            "id": "cam_sparse_no_ptz",
            "name": "Sparse Camera No PTZ",
            "is_ptz": False,
        }
        normalized_no_ptz = coordinator._normalize_camera_data(sparse_cam_no_ptz)
        assert normalized_no_ptz["isPtz"] is False

        # 2. Real Camera model dump showing default fields suppress the fallbacks
        real_cam = Camera(id="cam_real", name="Real Camera", mac="AA:BB:CC:DD:EE:FF")
        real_dump = real_cam.model_dump(by_alias=True)
        real_dump["smart_detect_types"] = ["ignored_legacy"]
        real_dump["is_ptz"] = True
        normalized_real = coordinator._normalize_camera_data(real_dump)
        assert normalized_real["smartDetectTypes"] == []
        assert normalized_real["isPtz"] is False

    def test_prune_rebuilt_trackers_motion_and_ring(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Cover REST rebuild tracker pruning branches for motion and ring."""
        now = datetime.now(tz=UTC)
        coordinator._camera_motion_started = {
            "cam_survives": now,
            "cam_pruned": now,
            "cam_missing": now,
        }
        coordinator._camera_ring_started = {
            "cam_survives": now,
            "cam_pruned": now,
            "cam_missing": now,
        }

        cameras = {
            "cam_survives": {
                "id": "cam_survives",
                "lastMotionStart": 1000,
                "lastRingStart": 2000,
            },
            "cam_pruned": {
                "id": "cam_pruned",
            },
        }

        coordinator._drop_rebuilt_latch_trackers(cameras)

        assert "cam_survives" in coordinator._camera_motion_started
        assert "cam_missing" in coordinator._camera_motion_started
        assert "cam_pruned" not in coordinator._camera_motion_started

        assert "cam_survives" in coordinator._camera_ring_started
        assert "cam_missing" in coordinator._camera_ring_started
        assert "cam_pruned" not in coordinator._camera_ring_started

    @pytest.mark.asyncio
    async def test_fetch_lights_unabsorbed_error_raises(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test _fetch_lights raises when errors exceed threshold."""
        protect_client = coordinator.protect_client
        assert protect_client is not None
        get_lights = AsyncMock(side_effect=UniFiConnectionError("Connection failure"))
        protect_client.lights.get_all = get_lights
        for expected_count in range(1, MAX_CONSECUTIVE_EMPTY_FETCHES + 1):
            await coordinator._fetch_lights()
            assert coordinator._consecutive_fetch_errors["lights"] == expected_count
        with pytest.raises(UniFiConnectionError, match="Connection failure"):
            await coordinator._fetch_lights()
        assert (
            coordinator._consecutive_fetch_errors["lights"]
            == MAX_CONSECUTIVE_EMPTY_FETCHES + 1
        )
        assert get_lights.await_count == MAX_CONSECUTIVE_EMPTY_FETCHES + 1

    @pytest.mark.asyncio
    async def test_fetch_collections_skip_records_without_id(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test Protect collection fetches skip ID-less records."""
        protect_client = coordinator.protect_client
        assert protect_client is not None
        protect_client.lights.get_all = AsyncMock(
            return_value=[
                Light.model_validate(
                    {"id": "light1", "mac": "00:11:22:33:44:01", "name": "Light 1"}
                ),
                Light.model_validate(
                    {"id": "", "mac": "00:11:22:33:44:02", "name": "NoIDLight"}
                ),
            ]
        )
        protect_client.lights.last_result_complete = True
        await coordinator._fetch_lights()
        assert list(coordinator.data["lights"].keys()) == ["light1"]
        protect_client.cameras.get_all = AsyncMock(
            return_value=[
                Camera.model_validate(
                    {"id": "camera1", "mac": "00:11:22:33:44:00", "name": "Camera 1"}
                ),
                Camera.model_validate(
                    {"id": "", "mac": "00:11:22:33:44:01", "name": "NoIDCamera"}
                ),
            ]
        )
        protect_client.cameras.last_result_complete = True
        await coordinator._fetch_cameras()
        assert list(coordinator.data["cameras"].keys()) == ["camera1"]
        protect_client.sensors.get_all = AsyncMock(
            return_value=[
                Sensor.model_validate(
                    {"id": "sensor1", "mac": "00:11:22:33:44:03", "name": "Sensor 1"}
                ),
                Sensor.model_validate(
                    {"id": "", "mac": "00:11:22:33:44:04", "name": "NoIDSensor"}
                ),
            ]
        )
        await coordinator._fetch_sensors()
        assert list(coordinator.data["sensors"].keys()) == ["sensor1"]
        protect_client.chimes.get_all = AsyncMock(
            return_value=[
                Chime.model_validate(
                    {"id": "chime1", "mac": "00:11:22:33:44:05", "name": "Chime 1"}
                ),
                Chime.model_validate(
                    {"id": "", "mac": "00:11:22:33:44:06", "name": "NoIDChime"}
                ),
            ]
        )
        protect_client.chimes.last_result_complete = True
        await coordinator._fetch_chimes()
        assert list(coordinator.data["chimes"].keys()) == ["chime1"]
        protect_client.viewers.get_all = AsyncMock(
            return_value=[
                Viewer.model_validate(
                    {
                        "id": "viewer1",
                        "mac": "00:11:22:33:44:07",
                        "state": "ONLINE",
                        "name": "Viewer 1",
                    }
                ),
                Viewer.model_validate(
                    {
                        "id": "",
                        "mac": "00:11:22:33:44:08",
                        "state": "ONLINE",
                        "name": "NoIDViewer",
                    }
                ),
            ]
        )
        protect_client.viewers.last_result_complete = True
        await coordinator._fetch_viewers()
        assert list(coordinator.data["viewers"].keys()) == ["viewer1"]
        protect_client.liveviews.get_all = AsyncMock(
            return_value=[
                LiveView.model_validate({"id": "liveview1", "name": "Liveview 1"}),
                LiveView.model_validate({"id": "", "name": "NoIDLiveview"}),
            ]
        )
        await coordinator._fetch_liveviews()
        assert list(coordinator.data["liveviews"].keys()) == ["liveview1"]

    @pytest.mark.asyncio
    async def test_async_refresh_sensors_task_cleared_mid_execution(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test _sensor_refresh_task cleared by production helper mid-execution."""
        entered_fetch = asyncio.Event()
        continue_fetch = asyncio.Event()

        async def mock_fetch():
            entered_fetch.set()
            await continue_fetch.wait()

        coordinator._fetch_sensors = mock_fetch

        refresh_task = asyncio.create_task(
            coordinator.async_refresh_sensors(notify=False)
        )
        await entered_fetch.wait()

        # Task was launched and assigned through production helper
        tracked_task = coordinator._sensor_refresh_task
        assert tracked_task is not None
        assert not tracked_task.done()

        # Cancellation clears slot before await resumes
        coordinator._cancel_sensor_background_task("_sensor_refresh_task")
        assert coordinator._sensor_refresh_task is None

        # Resume fetch and await completion (suppressing task cancellation)
        continue_fetch.set()
        with contextlib.suppress(asyncio.CancelledError):
            await refresh_task
        assert tracked_task.cancelled()
        assert coordinator._sensor_refresh_task is None

    @pytest.mark.asyncio
    async def test_async_refresh_sensors_completed_fetch_shutdown_race(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """Test completed-fetch shutdown race preserves newer fetch owner.

        Covers Protect coordinator 1162->1165: when _fetch_sensors finishes,
        the fetch Task transitions to done. Before the awaiting driver task
        (async_refresh_sensors) resumes on the event loop ready queue, a
        concurrent public async_stop_websocket task executes, sees that
        _sensor_refresh_task is already done, avoids cancelling it, and nulls
        the attribute reference via the production helper. Immediately following
        the stop task, a second async_refresh_sensors task executes, finds the
        cleared slot, and launches a new inner fetch Task through the production
        helper, becoming the legitimate new owner. When the original awaiting
        driver task then resumes, its finally block observes
        `self._sensor_refresh_task is task` as False (the newer fetch Task is
        not the original task) and bypasses nulling without error, preserving
        the newer owner's task in the slot.
        """
        entered_first_fetch = asyncio.Event()
        release_first_fetch = asyncio.Event()
        entered_second_fetch = asyncio.Event()
        release_second_fetch = asyncio.Event()
        fetch_tasks: list[asyncio.Task[Any]] = []
        stop_tasks: list[asyncio.Task[None]] = []
        second_refresh_tasks: list[asyncio.Task[None]] = []
        listener_calls: list[int] = []
        coordinator.async_add_listener(lambda: listener_calls.append(1))

        fetch_count = 0

        async def mock_fetch() -> None:
            nonlocal fetch_count
            fetch_count += 1
            current = asyncio.current_task()
            assert current is not None
            fetch_tasks.append(current)

            if fetch_count == 1:
                entered_first_fetch.set()
                await release_first_fetch.wait()
                # Queue public async_stop_websocket Task and second refresh Task
                # with eager_start=False before fetch returns without another await,
                # so stop_task and second_refresh_task are queued before inner fetch
                # completion queues driver resumption.
                stop_task = asyncio.create_task(
                    coordinator.async_stop_websocket(),
                    eager_start=False,
                )
                stop_tasks.append(stop_task)
                second_refresh_task = asyncio.create_task(
                    coordinator.async_refresh_sensors(notify=False),
                    eager_start=False,
                )
                second_refresh_tasks.append(second_refresh_task)
            else:
                entered_second_fetch.set()
                await release_second_fetch.wait()

        coordinator._fetch_sensors = mock_fetch

        driver_task: asyncio.Task[None] = asyncio.create_task(
            coordinator.async_refresh_sensors(notify=True)
        )
        await entered_first_fetch.wait()

        # Task was launched and assigned through production helper
        assert len(fetch_tasks) == 1
        inner_fetch_task = fetch_tasks[0]
        assert coordinator._sensor_refresh_task is inner_fetch_task
        assert not inner_fetch_task.done()

        # Unblock first fetch; fetch queues stop_task then second_refresh_task,
        # and returns without another await.
        release_first_fetch.set()

        await driver_task
        assert len(stop_tasks) == 1
        stop_task = stop_tasks[0]
        await stop_task
        assert len(second_refresh_tasks) == 1
        second_refresh_task = second_refresh_tasks[0]

        second_fetch_task: asyncio.Task[Any] | None = None
        try:
            # Assert second fetch has started and coordinator owns it
            assert entered_second_fetch.is_set()
            assert len(fetch_tasks) == 2
            second_fetch_task = fetch_tasks[1]
            assert coordinator._sensor_refresh_task is second_fetch_task
            assert not second_fetch_task.done()
            assert not second_fetch_task.cancelled()
            assert inner_fetch_task.done()
            assert not inner_fetch_task.cancelled()
            assert stop_task.done()
            assert not stop_task.cancelled()
            assert driver_task.done()
            assert not driver_task.cancelled()
            assert listener_calls == [1]
            assert coordinator.protect_client is not None
            assert coordinator.protect_client.websocket is not None
            stop_websocket = coordinator.protect_client.websocket.stop
            assert isinstance(stop_websocket, MagicMock)
            stop_websocket.assert_called_once()
        finally:
            # Robustness: release and await second refresh even if identity assertion
            # fails under guard mutation, preventing hung teardown or leaked tasks.
            release_second_fetch.set()
            await second_refresh_task

        # Assert second fetch + driver DONE/not cancelled, slot None,
        # listener still once
        assert second_fetch_task is not None
        assert second_fetch_task.done()
        assert not second_fetch_task.cancelled()
        assert second_refresh_task.done()
        assert not second_refresh_task.cancelled()
        assert coordinator._sensor_refresh_task is None
        assert listener_calls == [1]


# ============================================================================
# UnifiFacadeCoordinator Tests
# ============================================================================


class TestUnifiFacadeCoordinator:
    """Tests for UnifiFacadeCoordinator."""

    @pytest.fixture
    def config_coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> UnifiConfigCoordinator:
        """Create a config coordinator."""
        network_client = _create_mock_network_client()
        coord = UnifiConfigCoordinator(
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
        config_coordinator: UnifiConfigCoordinator,
    ) -> UnifiDeviceCoordinator:
        """Create a device coordinator."""
        network_client = _create_mock_network_client()
        coord = UnifiDeviceCoordinator(
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
    ) -> UnifiProtectCoordinator:
        """Create a protect coordinator."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        coord = UnifiProtectCoordinator(
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
        config_coordinator: UnifiConfigCoordinator,
        device_coordinator: UnifiDeviceCoordinator,
        protect_coordinator: UnifiProtectCoordinator,
    ) -> AsyncGenerator[UnifiFacadeCoordinator]:
        """Create a facade coordinator."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        coordinator = UnifiFacadeCoordinator(
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
        config_coordinator: UnifiConfigCoordinator,
        device_coordinator: UnifiDeviceCoordinator,
    ) -> AsyncGenerator[UnifiFacadeCoordinator]:
        """Create a facade coordinator without protect."""
        network_client = _create_mock_network_client()
        coordinator = UnifiFacadeCoordinator(
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

    def test_initialization(self, facade_coordinator: UnifiFacadeCoordinator):
        """Test facade coordinator initialization."""
        assert facade_coordinator.name == f"{DOMAIN}_facade"

    def test_aggregate_without_protect_has_security_device_keys(
        self, facade_coordinator_no_protect: UnifiFacadeCoordinator
    ):
        """The empty Protect fallback carries every collection entities index."""
        facade_coordinator_no_protect._aggregate_data()

        protect = facade_coordinator_no_protect.data["protect"]
        for collection in ("fobs", "link_stations", "alarm_hubs", "sirens"):
            assert protect[collection] == {}

    def test_aggregate_data(self, facade_coordinator: UnifiFacadeCoordinator):
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
        self, facade_coordinator_no_protect: UnifiFacadeCoordinator
    ):
        """Test data aggregation without protect."""
        facade_coordinator_no_protect._aggregate_data()

        # Protect data should have default empty structure
        assert facade_coordinator_no_protect.data["protect"]["cameras"] == {}
        assert facade_coordinator_no_protect.data["protect"]["lights"] == {}

    def test_aggregate_data_includes_rule_changes(
        self,
        facade_coordinator: UnifiFacadeCoordinator,
        config_coordinator: UnifiConfigCoordinator,
    ) -> None:
        """Rule change records in config data are included in facade data."""
        config_coordinator.data["rule_changes"] = {"site1": [{"sequence": 1}]}
        facade_coordinator._aggregate_data()
        assert facade_coordinator.data["rule_changes"] == {"site1": [{"sequence": 1}]}

    def test_get_site(self, facade_coordinator: UnifiFacadeCoordinator):
        """Test get_site delegation to config coordinator."""
        facade_coordinator._aggregate_data()
        result = facade_coordinator.get_site("default")
        assert result == {"id": "default", "name": "Default"}

    def test_get_device(self, facade_coordinator: UnifiFacadeCoordinator):
        """Test get_device."""
        facade_coordinator._aggregate_data()
        result = facade_coordinator.get_device("default", "device1")
        assert result == {"id": "device1"}

    def test_get_device_missing(self, facade_coordinator: UnifiFacadeCoordinator):
        """Test get_device for missing device."""
        facade_coordinator._aggregate_data()
        result = facade_coordinator.get_device("default", "nonexistent")
        assert result is None

    def test_get_device_stats(self, facade_coordinator: UnifiFacadeCoordinator):
        """Test get_device_stats."""
        facade_coordinator._aggregate_data()
        result = facade_coordinator.get_device_stats("default", "device1")
        assert result == {"cpu": 10}

    def test_get_device_stats_missing(self, facade_coordinator: UnifiFacadeCoordinator):
        """Test get_device_stats for missing device."""
        facade_coordinator._aggregate_data()
        result = facade_coordinator.get_device_stats("default", "nonexistent")
        assert result is None

    def test_available_all_success(self, facade_coordinator: UnifiFacadeCoordinator):
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
        self, facade_coordinator: UnifiFacadeCoordinator
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
        self, facade_coordinator: UnifiFacadeCoordinator
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
        self, facade_coordinator: UnifiFacadeCoordinator
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
        self, facade_coordinator: UnifiFacadeCoordinator
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
        self, facade_coordinator_no_protect: UnifiFacadeCoordinator
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
    async def test_async_update_data(self, facade_coordinator: UnifiFacadeCoordinator):
        """Test async update data."""
        result = await facade_coordinator._async_update_data()

        assert "sites" in result
        assert "devices" in result
        assert "protect" in result

    @pytest.mark.asyncio
    async def test_async_request_refresh(
        self, facade_coordinator: UnifiFacadeCoordinator
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
        self, facade_coordinator: UnifiFacadeCoordinator
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
        self, facade_coordinator: UnifiFacadeCoordinator
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
        self, facade_coordinator: UnifiFacadeCoordinator
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
        self, facade_coordinator: UnifiFacadeCoordinator
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
        self, facade_coordinator: UnifiFacadeCoordinator
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
        self, facade_coordinator_no_protect: UnifiFacadeCoordinator
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
        self, facade_coordinator_no_protect: UnifiFacadeCoordinator
    ):
        """Test async request refresh without protect."""
        facade_coordinator_no_protect._config_coordinator.async_refresh = AsyncMock()
        facade_coordinator_no_protect._device_coordinator.async_refresh = AsyncMock()

        await facade_coordinator_no_protect.async_request_refresh()

        facade_coordinator_no_protect._config_coordinator.async_refresh.assert_called_once()
        facade_coordinator_no_protect._device_coordinator.async_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_request_refresh_waits_for_fresh_data_and_notifies(
        self, facade_coordinator: UnifiFacadeCoordinator
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
        self, facade_coordinator: UnifiFacadeCoordinator
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
    async def test_require_protect_client_raises(
        self, facade_coordinator_no_protect: UnifiFacadeCoordinator
    ):
        """Test _require_protect_client raises when protect is None."""
        with pytest.raises(HomeAssistantError, match="Protect is not available"):
            facade_coordinator_no_protect._require_protect_client()

    @pytest.mark.asyncio
    async def test_require_protect_client_returns_client(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test _require_protect_client returns client when available."""
        client = facade_coordinator._require_protect_client()
        assert client is facade_coordinator.protect_client

    @pytest.mark.asyncio
    async def test_async_execute_api_action_success(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test _async_execute_api_action on success."""
        action = AsyncMock(return_value="ok")
        result = await facade_coordinator._async_execute_api_action(
            "test error", action, "arg1"
        )
        assert result == "ok"
        action.assert_called_once_with("arg1")

    @pytest.mark.asyncio
    async def test_async_execute_api_action_ha_error(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test _async_execute_api_action re-raises HomeAssistantError."""
        action = AsyncMock(side_effect=HomeAssistantError("ha error"))
        with pytest.raises(HomeAssistantError, match="ha error"):
            await facade_coordinator._async_execute_api_action("test error", action)

    @pytest.mark.asyncio
    async def test_async_execute_api_action_generic_error(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test _async_execute_api_action wraps generic errors."""
        cause = RuntimeError("boom")
        action = AsyncMock(side_effect=cause)
        with pytest.raises(HomeAssistantError, match="test error") as exc_info:
            await facade_coordinator._async_execute_api_action("test error", action)
        assert exc_info.value.__cause__ is cause

    @pytest.mark.asyncio
    async def test_async_restart_device(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_restart_device delegates to network client."""
        facade_coordinator.network_client.devices.restart = AsyncMock(return_value=True)
        result = await facade_coordinator.async_restart_device("site1", "dev1")
        assert result is True
        facade_coordinator.network_client.devices.restart.assert_called_once_with(
            "site1", "dev1"
        )

    @pytest.mark.asyncio
    async def test_async_power_cycle_port(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_power_cycle_port delegates to network client."""
        facade_coordinator.network_client.devices.execute_port_action = AsyncMock(
            return_value=True
        )
        result = await facade_coordinator.async_power_cycle_port("site1", "dev1", 3)
        assert result is True
        devices_api = facade_coordinator.network_client.devices
        devices_api.execute_port_action.assert_called_once_with(
            "site1", "dev1", 3, "POWER_CYCLE"
        )

    @pytest.mark.asyncio
    async def test_async_set_firewall_rule_enabled(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_set_firewall_rule_enabled delegates correctly."""
        facade_coordinator.network_client.firewall.update_rule = AsyncMock()
        await facade_coordinator.async_set_firewall_rule_enabled(
            "site1", "rule1", enabled=True
        )
        facade_coordinator.network_client.firewall.update_rule.assert_called_once_with(
            "site1", "rule1", enabled=True
        )

    @pytest.mark.asyncio
    async def test_async_set_policy_based_route_enabled(
        self, facade_coordinator: UnifiFacadeCoordinator
    ) -> None:
        """Test async_set_policy_based_route_enabled delegates correctly."""
        facade_coordinator.network_client.routes.update_route = AsyncMock()
        facade_coordinator._device_coordinator.get_legacy_site_name = MagicMock(
            return_value="default"
        )
        await facade_coordinator.async_set_policy_based_route_enabled(
            "site1", "route1", enabled=True
        )
        facade_coordinator.network_client.routes.update_route.assert_called_once_with(
            "default", "route1", enabled=True
        )

    @pytest.mark.asyncio
    async def test_async_set_policy_based_route_enabled_fallback(
        self, facade_coordinator: UnifiFacadeCoordinator
    ) -> None:
        """Test async_set_policy_based_route_enabled falls back to default."""
        facade_coordinator.network_client.routes.update_route = AsyncMock()
        facade_coordinator._device_coordinator.get_legacy_site_name = MagicMock(
            return_value=None
        )
        await facade_coordinator.async_set_policy_based_route_enabled(
            "unknown_site", "route1", enabled=True
        )
        facade_coordinator.network_client.routes.update_route.assert_called_once_with(
            "default", "route1", enabled=True
        )

    @pytest.mark.asyncio
    async def test_async_set_vpn_client_enabled(
        self, facade_coordinator: UnifiFacadeCoordinator
    ) -> None:
        """Test async_set_vpn_client_enabled delegates correctly."""
        facade_coordinator.network_client.vpn_clients.update_vpn_client = AsyncMock()
        facade_coordinator._device_coordinator.get_legacy_site_name = MagicMock(
            return_value="default"
        )
        await facade_coordinator.async_set_vpn_client_enabled(
            "site1", "vpn1", enabled=True
        )
        mock_update = facade_coordinator.network_client.vpn_clients.update_vpn_client
        mock_update.assert_called_once_with("default", "vpn1", enabled=True)

    @pytest.mark.asyncio
    async def test_async_set_vpn_client_enabled_fallback(
        self, facade_coordinator: UnifiFacadeCoordinator
    ) -> None:
        """Test async_set_vpn_client_enabled falls back to default site."""
        facade_coordinator.network_client.vpn_clients.update_vpn_client = AsyncMock()
        facade_coordinator._device_coordinator.get_legacy_site_name = MagicMock(
            return_value=None
        )
        await facade_coordinator.async_set_vpn_client_enabled(
            "unknown_site", "vpn1", enabled=True
        )
        mock_update = facade_coordinator.network_client.vpn_clients.update_vpn_client
        mock_update.assert_called_once_with("default", "vpn1", enabled=True)

    @pytest.mark.asyncio
    async def test_async_update_camera(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_update_camera delegates to protect client."""
        facade_coordinator.protect_client.cameras.update = AsyncMock()
        await facade_coordinator.async_update_camera("cam1", hdrMode="on")
        facade_coordinator.protect_client.cameras.update.assert_called_once_with(
            "cam1", hdrMode="on"
        )

    @pytest.mark.asyncio
    async def test_async_update_camera_settings(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_update_camera_settings is alias for async_update_camera."""
        facade_coordinator.protect_client.cameras.update = AsyncMock()
        await facade_coordinator.async_update_camera_settings("cam1", videoMode="hd")
        facade_coordinator.protect_client.cameras.update.assert_called_once_with(
            "cam1", videoMode="hd"
        )

    @pytest.mark.asyncio
    async def test_async_unblock_client(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_unblock_client resolves the MAC and classic site name."""
        facade_coordinator.data["clients"] = {
            "site1": {"client1": {"id": "client1", "macAddress": "AA:BB:CC:DD:EE:FF"}}
        }
        facade_coordinator.network_client.clients.unblock = AsyncMock()
        await facade_coordinator.async_unblock_client("site1", "client1")
        facade_coordinator.network_client.clients.unblock.assert_called_once_with(
            "default", "AA:BB:CC:DD:EE:FF"
        )

    @pytest.mark.asyncio
    async def test_async_block_client(self, facade_coordinator: UnifiFacadeCoordinator):
        """Test async_block_client resolves the MAC and classic site name."""
        facade_coordinator.data["clients"] = {
            "site1": {"client1": {"id": "client1", "macAddress": "AA:BB:CC:DD:EE:FF"}}
        }
        facade_coordinator.network_client.clients.block = AsyncMock()
        await facade_coordinator.async_block_client("site1", "client1")
        facade_coordinator.network_client.clients.block.assert_called_once_with(
            "default", "AA:BB:CC:DD:EE:FF"
        )

    @pytest.mark.asyncio
    async def test_async_reconnect_client(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_reconnect_client resolves the MAC and classic site name."""
        facade_coordinator.data["clients"] = {
            "site1": {"client1": {"id": "client1", "macAddress": "AA:BB:CC:DD:EE:FF"}}
        }
        facade_coordinator.network_client.clients.reconnect = AsyncMock()
        await facade_coordinator.async_reconnect_client("site1", "client1")
        facade_coordinator.network_client.clients.reconnect.assert_called_once_with(
            "default", "AA:BB:CC:DD:EE:FF"
        )

    @pytest.mark.asyncio
    async def test_async_forget_client(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_forget_client resolves the MAC and classic site name."""
        facade_coordinator.data["clients"] = {
            "site1": {"client1": {"id": "client1", "macAddress": "AA:BB:CC:DD:EE:FF"}}
        }
        facade_coordinator.network_client.clients.forget = AsyncMock()
        await facade_coordinator.async_forget_client("site1", "client1")
        facade_coordinator.network_client.clients.forget.assert_called_once_with(
            "default", "AA:BB:CC:DD:EE:FF"
        )

    @pytest.mark.asyncio
    async def test_client_action_uses_mapped_site_name(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """A non-default classic site name from the device coordinator is used."""
        facade_coordinator.data["clients"] = {
            "site1": {"client1": {"id": "client1", "macAddress": "AA:BB:CC:DD:EE:FF"}}
        }
        facade_coordinator._device_coordinator._legacy_site_names = {"site1": "branch"}
        facade_coordinator.network_client.clients.block = AsyncMock()
        await facade_coordinator.async_block_client("site1", "client1")
        facade_coordinator.network_client.clients.block.assert_called_once_with(
            "branch", "AA:BB:CC:DD:EE:FF"
        )

    @pytest.mark.asyncio
    async def test_async_get_historical_clients_verified_site(
        self, facade_coordinator: UnifiFacadeCoordinator
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
        self, facade_coordinator: UnifiFacadeCoordinator
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
        self, facade_coordinator: UnifiFacadeCoordinator
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

    @pytest.mark.asyncio
    async def test_async_forget_clients_batch_verified_site(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_forget_clients_batch uses verified site mapping."""
        facade_coordinator._config_coordinator.data = {
            "sites": {"site1": {"id": "site1", "internalReference": "branch"}}
        }
        facade_coordinator.network_client.clients.forget_batch = AsyncMock(
            return_value=True
        )
        result = await facade_coordinator.async_forget_clients_batch(
            "site1", ["aa:bb:cc:dd:ee:ff"]
        )
        assert result is True
        facade_coordinator.network_client.clients.forget_batch.assert_called_once_with(
            "branch", ["aa:bb:cc:dd:ee:ff"]
        )

    @pytest.mark.asyncio
    async def test_async_forget_clients_batch_unmapped_site_raises(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Unmapped site in batch forget raises error without fallback."""
        facade_coordinator._config_coordinator.data = {"sites": {}}
        facade_coordinator.network_client.clients.forget_batch = AsyncMock()
        with pytest.raises(
            HomeAssistantError, match="No verified classic site name found"
        ) as excinfo:
            await facade_coordinator.async_forget_clients_batch(
                "site1", ["aa:bb:cc:dd:ee:ff"]
            )
        assert excinfo.value.translation_key == "legacy_site_not_found"
        facade_coordinator.network_client.clients.forget_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_forget_clients_batch_api_error_wrapped(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """API error in async_forget_clients_batch is wrapped in HomeAssistantError."""
        facade_coordinator._config_coordinator.data = {
            "sites": {"site1": {"id": "site1", "internalReference": "branch"}}
        }
        facade_coordinator.network_client.clients.forget_batch = AsyncMock(
            side_effect=RuntimeError("connection dropped")
        )
        with pytest.raises(
            HomeAssistantError, match="Unable to forget clients for site site1"
        ):
            await facade_coordinator.async_forget_clients_batch(
                "site1", ["aa:bb:cc:dd:ee:ff"]
            )

    @pytest.mark.asyncio
    async def test_classic_site_two_sites_same_description_uses_internal_reference(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Resolve requests ONLY via internalReference for two sites named Office."""
        facade_coordinator._config_coordinator.data = {
            "sites": {
                "site_alpha": {
                    "id": "site_alpha",
                    "name": "Office",
                    "internalReference": "alpha",
                },
                "site_beta": {
                    "id": "site_beta",
                    "name": "Office",
                    "internalReference": "beta",
                },
            }
        }
        # Simulate device coordinator fuzzy mapping returning beta for both
        facade_coordinator._device_coordinator._legacy_site_names = {
            "site_alpha": "beta",
            "site_beta": "beta",
        }
        facade_coordinator.network_client.clients.get_historical_legacy = AsyncMock(
            return_value=[{"mac": "aa:bb:cc:dd:ee:ff"}]
        )
        facade_coordinator.network_client.clients.forget_batch = AsyncMock(
            return_value=True
        )

        clients = await facade_coordinator.async_get_historical_clients("site_alpha")
        assert clients == [{"mac": "aa:bb:cc:dd:ee:ff"}]
        clients_api = facade_coordinator.network_client.clients
        clients_api.get_historical_legacy.assert_called_once_with("alpha")

        result = await facade_coordinator.async_forget_clients_batch(
            "site_alpha", ["aa:bb:cc:dd:ee:ff"]
        )
        assert result is True
        facade_coordinator.network_client.clients.forget_batch.assert_called_once_with(
            "alpha", ["aa:bb:cc:dd:ee:ff"]
        )

    @pytest.mark.asyncio
    async def test_classic_site_blank_internal_reference_sends_nothing(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Missing or blank internalReference raises and sends no requests."""
        facade_coordinator.network_client.clients.get_historical_legacy = AsyncMock()
        facade_coordinator.network_client.clients.forget_batch = AsyncMock()

        for blank_ref in [None, "", "   "]:
            facade_coordinator._config_coordinator.data = {
                "sites": {"site1": {"id": "site1", "internalReference": blank_ref}}
            }
            with pytest.raises(HomeAssistantError) as exc_info:
                await facade_coordinator.async_get_historical_clients("site1")
            assert exc_info.value.translation_key == "legacy_site_not_found"

            with pytest.raises(HomeAssistantError) as exc_info:
                await facade_coordinator.async_forget_clients_batch(
                    "site1", ["aa:bb:cc:dd:ee:ff"]
                )
            assert exc_info.value.translation_key == "legacy_site_not_found"

        facade_coordinator.network_client.clients.get_historical_legacy.assert_not_called()
        facade_coordinator.network_client.clients.forget_batch.assert_not_called()

    def test_facade_network_usability_accessors(
        self, facade_coordinator: UnifiFacadeCoordinator
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
    async def test_async_set_outlet_state(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_set_outlet_state resolves site and targets legacy _id."""
        facade_coordinator.data["devices"] = {
            "site1": {
                "device1": {
                    "id": "device1",
                    "_id": "60a1b2c3d4e5f67890123456",
                    "macAddress": "AA:BB:CC:DD:EE:FF",
                }
            }
        }
        facade_coordinator._device_coordinator._legacy_site_names = {"site1": "branch"}
        facade_coordinator.network_client.devices.set_outlet_state = AsyncMock(
            return_value=True
        )

        result = await facade_coordinator.async_set_outlet_state(
            "site1", "device1", 1, state=True, cycle_enabled=False
        )
        assert result is True
        set_outlet = facade_coordinator.network_client.devices.set_outlet_state
        set_outlet.assert_called_once_with(
            "branch",
            "60a1b2c3d4e5f67890123456",
            1,
            state=True,
            cycle_enabled=False,
            current_device={
                "id": "device1",
                "_id": "60a1b2c3d4e5f67890123456",
                "macAddress": "AA:BB:CC:DD:EE:FF",
            },
        )

    @pytest.mark.asyncio
    async def test_async_authorize_guest(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_authorize_guest delegates to the official actions endpoint."""
        facade_coordinator.network_client.clients.authorize_guest = AsyncMock()
        await facade_coordinator.async_authorize_guest("site1", "client1")
        facade_coordinator.network_client.clients.authorize_guest.assert_called_once_with(
            "site1", "client1"
        )

    @pytest.mark.asyncio
    async def test_async_unauthorize_guest(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_unauthorize_guest delegates to the official actions endpoint."""
        facade_coordinator.network_client.clients.unauthorize_guest = AsyncMock()
        await facade_coordinator.async_unauthorize_guest("site1", "client1")
        mock = facade_coordinator.network_client.clients.unauthorize_guest
        mock.assert_called_once_with("site1", "client1")

    @pytest.mark.asyncio
    async def test_async_update_wifi_network(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_update_wifi_network delegates correctly."""
        facade_coordinator.network_client.wifi.update = AsyncMock()
        await facade_coordinator.async_update_wifi_network(
            "site1", "wifi1", enabled=False
        )
        facade_coordinator.network_client.wifi.update.assert_called_once_with(
            "site1", "wifi1", enabled=False
        )

    @pytest.mark.asyncio
    async def test_async_play_chime(self, facade_coordinator: UnifiFacadeCoordinator):
        """The vendored no-I/O stub surfaces a translated unsupported error."""
        client = facade_coordinator.protect_client
        assert client is not None
        with (
            patch.object(client, "chimes", ChimesEndpoint(client)),
            patch.object(client, "build_api_path") as build_api_path,
            patch.object(client, "_post") as post,
            pytest.raises(HomeAssistantError) as raised,
        ):
            await facade_coordinator.async_play_chime("chime1")
        assert raised.value.translation_key == "chime_playback_unsupported"
        assert raised.value.translation_domain == DOMAIN
        build_api_path.assert_not_called()
        post.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_play_chime_generic_failure(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Unexpected play failures are wrapped with the chime-specific message."""
        facade_coordinator.protect_client.chimes.play = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        with pytest.raises(
            HomeAssistantError, match="Unable to play chime chime1"
        ) as raised:
            await facade_coordinator.async_play_chime("chime1")
        assert isinstance(raised.value.__cause__, RuntimeError)
        assert str(raised.value.__cause__) == "boom"

    @pytest.mark.asyncio
    async def test_async_start_ptz_patrol(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_start_ptz_patrol delegates correctly."""
        facade_coordinator.protect_client.cameras.ptz_patrol_start = AsyncMock()
        await facade_coordinator.async_start_ptz_patrol("cam1", 1)
        facade_coordinator.protect_client.cameras.ptz_patrol_start.assert_called_once_with(
            "cam1", 1
        )

    @pytest.mark.asyncio
    async def test_async_stop_ptz_patrol(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_stop_ptz_patrol delegates correctly."""
        facade_coordinator.protect_client.cameras.ptz_patrol_stop = AsyncMock()
        await facade_coordinator.async_stop_ptz_patrol("cam1")
        facade_coordinator.protect_client.cameras.ptz_patrol_stop.assert_called_once_with(
            "cam1"
        )

    @pytest.mark.asyncio
    async def test_async_set_hdr_mode(self, facade_coordinator: UnifiFacadeCoordinator):
        """Test async_set_hdr_mode delegates correctly."""
        facade_coordinator.protect_client.cameras.set_hdr_mode = AsyncMock()
        await facade_coordinator.async_set_hdr_mode("cam1", "auto")
        facade_coordinator.protect_client.cameras.set_hdr_mode.assert_called_once_with(
            "cam1", "auto"
        )

    @pytest.mark.asyncio
    async def test_async_set_video_mode(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_set_video_mode delegates correctly."""
        facade_coordinator.protect_client.cameras.set_video_mode = AsyncMock()
        await facade_coordinator.async_set_video_mode("cam1", "highFps")
        facade_coordinator.protect_client.cameras.set_video_mode.assert_called_once_with(
            "cam1", "highFps"
        )

    @pytest.mark.asyncio
    async def test_async_set_chime_ringtone(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Use the ringtone setter and refresh only after a successful write."""
        assert facade_coordinator.protect_client is not None
        facade_coordinator.protect_client.chimes.set_ringtone = AsyncMock()
        facade_coordinator.protect_client.chimes.update = AsyncMock()
        with patch.object(
            facade_coordinator._protect_coordinator,
            "async_request_refresh",
            new_callable=AsyncMock,
        ) as refresh:
            await facade_coordinator.async_set_chime_ringtone("chime1", "tone-2")
        facade_coordinator.protect_client.chimes.set_ringtone.assert_awaited_once_with(
            "chime1", "tone-2"
        )
        refresh.assert_awaited_once()
        facade_coordinator.protect_client.chimes.update.assert_not_called()

    @pytest.mark.parametrize(
        "error",
        [
            ValueError("Incomplete ring settings"),
            UniFiResponseError("Ringtone rejected", status_code=400),
        ],
    )
    async def test_async_set_chime_ringtone_error_skips_refresh(
        self, facade_coordinator: UnifiFacadeCoordinator, error: Exception
    ) -> None:
        """Validation and API failures become a user-facing error without refresh."""
        assert facade_coordinator.protect_client is not None
        facade_coordinator.protect_client.chimes.set_ringtone = AsyncMock(
            side_effect=error
        )
        with (
            patch.object(
                facade_coordinator._protect_coordinator,
                "async_request_refresh",
                new_callable=AsyncMock,
            ) as refresh,
            pytest.raises(
                HomeAssistantError, match="Unable to set ringtone for chime chime1"
            ),
        ):
            await facade_coordinator.async_set_chime_ringtone("chime1", "tone-2")
        refresh.assert_not_awaited()

    async def test_async_set_chime_ringtone_without_protect_coordinator(
        self, facade_coordinator: UnifiFacadeCoordinator
    ) -> None:
        """The setter also works when no child coordinator needs a refresh."""
        assert facade_coordinator.protect_client is not None
        facade_coordinator.protect_client.chimes.set_ringtone = AsyncMock()
        with patch.object(facade_coordinator, "_protect_coordinator", None):
            await facade_coordinator.async_set_chime_ringtone("chime1", "tone-2")
        facade_coordinator.protect_client.chimes.set_ringtone.assert_awaited_once_with(
            "chime1", "tone-2"
        )

    @pytest.mark.parametrize("raises", [False, True])
    async def test_chime_ringtone_success_survives_refresh_failure(
        self, facade_coordinator: UnifiFacadeCoordinator, raises, caplog
    ) -> None:
        """A recorded or raised refresh error cannot undo a successful PATCH."""
        client = facade_coordinator.protect_client
        assert client is not None
        client.chimes.set_ringtone = AsyncMock()
        child = facade_coordinator._protect_coordinator

        async def refresh() -> None:
            assert child is not None
            child.last_update_success = False
            if raises:
                msg = "Synthetic refresh failure"
                raise RuntimeError(msg)

        with patch.object(child, "async_request_refresh", side_effect=refresh):
            await facade_coordinator.async_set_chime_ringtone("chime1", "tone-new")
        assert client is not None
        assert child is not None
        client.chimes.set_ringtone.assert_awaited_once_with("chime1", "tone-new")
        assert child.last_update_success is False
        assert ("ringtone was updated" in caplog.text) == raises

    @pytest.mark.asyncio
    async def test_async_move_ptz_to_preset(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_move_ptz_to_preset delegates correctly."""
        facade_coordinator.protect_client.cameras.ptz_goto_preset = AsyncMock()
        await facade_coordinator.async_move_ptz_to_preset("cam1", 3)
        facade_coordinator.protect_client.cameras.ptz_goto_preset.assert_called_once_with(
            "cam1", "3"
        )

    @pytest.mark.asyncio
    async def test_async_update_viewer(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_update_viewer delegates correctly."""
        facade_coordinator.protect_client.viewers.update = AsyncMock()
        await facade_coordinator.async_update_viewer("viewer1", liveview="lv1")
        facade_coordinator.protect_client.viewers.update.assert_called_once_with(
            "viewer1", liveview="lv1"
        )

    @pytest.mark.asyncio
    async def test_async_set_microphone_volume(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_set_microphone_volume delegates correctly."""
        facade_coordinator.protect_client.cameras.set_microphone_volume = AsyncMock()
        await facade_coordinator.async_set_microphone_volume("cam1", 50)
        facade_coordinator.protect_client.cameras.set_microphone_volume.assert_called_once_with(
            "cam1", 50
        )

    @pytest.mark.asyncio
    async def test_async_start_camera_talkback_success(
        self, facade_coordinator: UnifiFacadeCoordinator
    ) -> None:
        """Test async_start_camera_talkback creates session and starts stream."""
        session = TalkbackSession(
            url="rtp://192.168.1.10:7004",
            codec="opus",
            samplingRate=24000,
            bitsPerSample=16,
        )
        facade_coordinator.protect_client.cameras.create_talkback_session = AsyncMock(
            return_value=session
        )
        with patch(
            "custom_components.unifi_insights.coordinators.facade.TalkbackStream"
        ) as mock_stream_cls:
            mock_stream = mock_stream_cls.return_value
            mock_stream.start = AsyncMock()
            result = await facade_coordinator.async_start_camera_talkback(
                "cam1", "http://media.local/audio.mp3"
            )
            assert result is mock_stream
            facade_coordinator.protect_client.cameras.create_talkback_session.assert_called_once_with(
                "cam1"
            )
            mock_stream_cls.assert_called_once_with(
                session, "http://media.local/audio.mp3", facade_coordinator.hass
            )
            mock_stream.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_start_camera_talkback_no_protect(
        self, facade_coordinator: UnifiFacadeCoordinator
    ) -> None:
        """Test async_start_camera_talkback raises when protect client is None."""
        facade_coordinator.protect_client = None
        with pytest.raises(HomeAssistantError) as exc_info:
            await facade_coordinator.async_start_camera_talkback(
                "cam1", "http://media.local/audio.mp3"
            )
        assert exc_info.value.translation_key == "protect_not_available_for_talkback"

    @pytest.mark.asyncio
    async def test_async_start_camera_talkback_session_failure(
        self,
        facade_coordinator: UnifiFacadeCoordinator,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test async_start_camera_talkback raises when session creation fails."""
        facade_coordinator.protect_client.cameras.create_talkback_session = AsyncMock(
            side_effect=ValueError("Session failed")
        )
        with pytest.raises(HomeAssistantError) as exc_info:
            await facade_coordinator.async_start_camera_talkback(
                "cam1", "https://media.local/audio.mp3"
            )
        assert exc_info.value.translation_key == "talkback_session_invalid"

        # Also verify ValidationError handling logs sanitized fields without raw URL
        distinctive_url = "rtp://custom-token@192.168.1.10:7004"
        validation_err = ValidationError.from_exception_data(
            "TalkbackSession",
            [
                {
                    "type": "string_type",
                    "loc": ("url",),
                    "msg": "Input should be a valid string",
                    "input": distinctive_url,
                }
            ],
        )
        facade_coordinator.protect_client.cameras.create_talkback_session = AsyncMock(
            side_effect=validation_err
        )
        with (
            caplog.at_level(logging.WARNING),
            pytest.raises(HomeAssistantError) as exc_info,
        ):
            await facade_coordinator.async_start_camera_talkback(
                "cam1", "https://media.local/audio.mp3"
            )
        assert exc_info.value.translation_key == "talkback_session_invalid"
        assert "invalid fields: url" in caplog.text
        assert distinctive_url not in caplog.text

    @pytest.mark.asyncio
    async def test_async_start_camera_talkback_unsupported_params(
        self, facade_coordinator: UnifiFacadeCoordinator
    ) -> None:
        """Test async_start_camera_talkback raises for unsupported stream parameters."""
        session = TalkbackSession(
            url="rtp://192.168.1.10:7004",
            codec="opus",
            samplingRate=24000,
            bitsPerSample=16,
        )
        facade_coordinator.protect_client.cameras.create_talkback_session = AsyncMock(
            return_value=session
        )

        with patch(
            "custom_components.unifi_insights.coordinators.facade.TalkbackStream.start",
            side_effect=TalkbackSchemeError("Bad scheme"),
        ):
            with pytest.raises(HomeAssistantError) as exc_info:
                await facade_coordinator.async_start_camera_talkback(
                    "cam1", "http://media.local/audio.mp3"
                )
            assert (
                exc_info.value.translation_key == "talkback_unsupported_codec_or_scheme"
            )

    @pytest.mark.asyncio
    async def test_async_start_camera_talkback_stream_failure(
        self, facade_coordinator: UnifiFacadeCoordinator
    ) -> None:
        """Test async_start_camera_talkback raises for stream start failure."""
        session = TalkbackSession(
            url="rtp://192.168.1.10:7004",
            codec="opus",
            samplingRate=24000,
            bitsPerSample=16,
        )
        facade_coordinator.protect_client.cameras.create_talkback_session = AsyncMock(
            return_value=session
        )

        with patch(
            "custom_components.unifi_insights.coordinators.facade.TalkbackStream.start",
            side_effect=TalkbackStreamError("Stream failed"),
        ):
            with pytest.raises(HomeAssistantError) as exc_info:
                await facade_coordinator.async_start_camera_talkback(
                    "cam1", "http://media.local/audio.mp3"
                )
            assert exc_info.value.translation_key == "talkback_stream_failure"

    @pytest.mark.asyncio
    async def test_async_set_light_brightness(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_set_light_brightness delegates correctly."""
        facade_coordinator.protect_client.lights.set_brightness = AsyncMock()
        await facade_coordinator.async_set_light_brightness("light1", 5)
        facade_coordinator.protect_client.lights.set_brightness.assert_called_once_with(
            "light1", 5
        )

    @pytest.mark.asyncio
    async def test_async_set_light_mode(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_set_light_mode delegates correctly."""
        facade_coordinator.protect_client.lights.update = AsyncMock()
        await facade_coordinator.async_set_light_mode("light1", "motion")
        facade_coordinator.protect_client.lights.update.assert_called_once_with(
            "light1", lightModeSettings={"mode": "motion"}
        )

    @pytest.mark.asyncio
    async def test_async_set_light_mode_sends_spec_patch_body(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_set_light_mode sends spec lightModeSettings PATCH body."""
        client = UniFiProtectClient(
            auth=ApiKeyAuth(api_key="test-key"),
            base_url="https://192.168.1.1",
            connection_type=ConnectionType.LOCAL,
        )
        client._patch = AsyncMock(
            return_value={"id": "light1", "mac": "00:11:22:33:44:66"}
        )
        facade_coordinator.protect_client = client
        facade_coordinator._protect_coordinator.protect_client = client
        await facade_coordinator.async_set_light_mode("light1", "motion")
        client._patch.assert_awaited_once_with(
            client.build_api_path("/lights/light1"),
            json_data={"lightModeSettings": {"mode": "motion"}},
        )

    @pytest.mark.asyncio
    async def test_async_set_light_brightness_sends_spec_patch_body(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_set_light_brightness sends spec lightDeviceSettings PATCH body."""
        client = UniFiProtectClient(
            auth=ApiKeyAuth(api_key="test-key"),
            base_url="https://192.168.1.1",
            connection_type=ConnectionType.LOCAL,
        )
        client._patch = AsyncMock(
            return_value={"id": "light1", "mac": "00:11:22:33:44:66"}
        )
        facade_coordinator.protect_client = client
        facade_coordinator._protect_coordinator.protect_client = client
        await facade_coordinator.async_set_light_brightness("light1", 6)
        client._patch.assert_awaited_once_with(
            client.build_api_path("/lights/light1"),
            json_data={"lightDeviceSettings": {"ledLevel": 6}},
        )

    @pytest.mark.asyncio
    async def test_async_set_chime_volume(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_set_chime_volume delegates correctly."""
        facade_coordinator.protect_client.chimes.set_volume = AsyncMock()
        await facade_coordinator.async_set_chime_volume("chime1", 80)
        facade_coordinator.protect_client.chimes.set_volume.assert_called_once_with(
            "chime1", 80
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("camera_ids", [["cam_a", "cam_b"], []])
    async def test_async_set_chime_paired_cameras_patches_and_requests_refresh(
        self,
        facade_coordinator: UnifiFacadeCoordinator,
        protect_coordinator: UnifiProtectCoordinator,
        camera_ids: list[str],
    ):
        """The PATCH carries only cameraIds, then Protect is asked to refresh."""
        facade_coordinator.protect_client.chimes.update = AsyncMock()

        with patch.object(
            protect_coordinator, "async_request_refresh", AsyncMock()
        ) as refresh:
            await facade_coordinator.async_set_chime_paired_cameras(
                "chime1", camera_ids
            )

        facade_coordinator.protect_client.chimes.update.assert_awaited_once_with(
            "chime1", cameraIds=camera_ids
        )
        refresh.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_async_set_chime_paired_cameras_api_error_skips_the_refresh(
        self,
        facade_coordinator: UnifiFacadeCoordinator,
        protect_coordinator: UnifiProtectCoordinator,
    ):
        """A rejected PATCH is a HomeAssistantError and refreshes nothing."""
        facade_coordinator.protect_client.chimes.update = AsyncMock(
            side_effect=UniFiResponseError("x", 400)
        )

        with (
            patch.object(
                protect_coordinator, "async_request_refresh", AsyncMock()
            ) as refresh,
            pytest.raises(
                HomeAssistantError,
                match="Unable to set the paired doorbells of chime chime1",
            ),
        ):
            await facade_coordinator.async_set_chime_paired_cameras("chime1", [])

        refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_async_set_chime_paired_cameras_without_protect_coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        config_coordinator: UnifiConfigCoordinator,
        device_coordinator: UnifiDeviceCoordinator,
    ):
        """A facade with a client but no Protect coordinator still sends the PATCH."""
        protect_client = _create_mock_protect_client()
        protect_client.chimes.update = AsyncMock()
        facade = UnifiFacadeCoordinator(
            hass=hass,
            network_client=_create_mock_network_client(),
            protect_client=protect_client,
            entry=mock_config_entry,
            config_coordinator=config_coordinator,
            device_coordinator=device_coordinator,
            protect_coordinator=None,
        )
        try:
            await facade.async_set_chime_paired_cameras("chime1", ["cam_a"])
        finally:
            await facade.async_shutdown()
            await config_coordinator.async_shutdown()
            await device_coordinator.async_shutdown()

        protect_client.chimes.update.assert_awaited_once_with(
            "chime1", cameraIds=["cam_a"]
        )

    @pytest.mark.asyncio
    async def test_async_set_chime_paired_cameras_without_protect_client_raises(
        self, facade_coordinator_no_protect: UnifiFacadeCoordinator
    ):
        """Pairing without Protect fails with the shared "not available" error."""
        with pytest.raises(HomeAssistantError, match="Protect is not available"):
            await facade_coordinator_no_protect.async_set_chime_paired_cameras(
                "chime1", []
            )

    @pytest.mark.asyncio
    async def test_async_set_chime_repeat(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_set_chime_repeat delegates correctly."""
        facade_coordinator.protect_client.chimes.set_repeat_times = AsyncMock()
        await facade_coordinator.async_set_chime_repeat("chime1", 3)
        facade_coordinator.protect_client.chimes.set_repeat_times.assert_called_once_with(
            "chime1", 3
        )

    @pytest.mark.asyncio
    async def test_async_generate_voucher(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_generate_voucher delegates correctly."""
        facade_coordinator.network_client.vouchers.create = AsyncMock()
        await facade_coordinator.async_generate_voucher(
            "site1",
            count=5,
            time_limit_minutes=60,
            tx_rate_limit_kbps=1024,
            rx_rate_limit_kbps=2048,
            data_usage_limit_mbytes=500,
            name="test",
        )
        facade_coordinator.network_client.vouchers.create.assert_called_once_with(
            "site1",
            count=5,
            time_limit_minutes=60,
            tx_rate_limit_kbps=1024,
            rx_rate_limit_kbps=2048,
            data_usage_limit_mbytes=500,
            name="test",
        )

    @pytest.mark.asyncio
    async def test_async_delete_voucher(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_delete_voucher delegates correctly."""
        facade_coordinator.network_client.vouchers.delete = AsyncMock()
        await facade_coordinator.async_delete_voucher("site1", "voucher1")
        facade_coordinator.network_client.vouchers.delete.assert_called_once_with(
            "site1", "voucher1"
        )

    @pytest.mark.asyncio
    async def test_async_trigger_alarm(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_trigger_alarm delegates correctly."""
        facade_coordinator.protect_client.application.trigger_alarm_webhook = (
            AsyncMock()
        )
        await facade_coordinator.async_trigger_alarm("alarm1")
        facade_coordinator.protect_client.application.trigger_alarm_webhook.assert_called_once_with(
            "alarm1"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "endpoint"),
        [("async_arm_alarm", "enable"), ("async_disarm_alarm", "disable")],
    )
    async def test_async_arm_and_disarm_call_endpoint_and_request_refresh(
        self,
        facade_coordinator: UnifiFacadeCoordinator,
        protect_coordinator: UnifiProtectCoordinator,
        method: str,
        endpoint: str,
    ):
        """Arm and disarm hit the matching endpoint with no arguments, then refresh.

        The Protect coordinator's own debounced refresh is the fallback for the
        NVR's WebSocket frame; there is no optimistic state.
        """
        arm_profiles = facade_coordinator.protect_client.arm_profiles
        arm_profiles.enable = AsyncMock(return_value=True)
        arm_profiles.disable = AsyncMock(return_value=True)

        with patch.object(
            protect_coordinator, "async_request_refresh", AsyncMock()
        ) as refresh:
            await getattr(facade_coordinator, method)()

        called = getattr(arm_profiles, endpoint)
        called.assert_awaited_once_with()
        other = arm_profiles.disable if endpoint == "enable" else arm_profiles.enable
        other.assert_not_awaited()
        refresh.assert_awaited_once_with()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "endpoint"),
        [("async_arm_alarm", "enable"), ("async_disarm_alarm", "disable")],
    )
    async def test_async_arm_global_alarm_manager_raises_translated_error(
        self,
        facade_coordinator: UnifiFacadeCoordinator,
        protect_coordinator: UnifiProtectCoordinator,
        method: str,
        endpoint: str,
    ):
        """Protect's global alarm manager refusal becomes a translated error.

        Nothing is refreshed, because nothing changed.
        """
        arm_profiles = facade_coordinator.protect_client.arm_profiles
        setattr(
            arm_profiles,
            endpoint,
            AsyncMock(side_effect=UniFiGlobalAlarmManagerError("x", 400, "body")),
        )
        protect_coordinator.data["global_alarm_manager"] = False

        with (
            patch.object(
                protect_coordinator, "async_request_refresh", AsyncMock()
            ) as refresh,
            pytest.raises(HomeAssistantError) as err,
        ):
            await getattr(facade_coordinator, method)()

        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "global_alarm_manager"
        refresh.assert_not_awaited()
        # The refusal is also the answer to whether the manager is on.
        assert protect_coordinator.data["global_alarm_manager"] is True

    @pytest.mark.asyncio
    async def test_async_arm_alarm_other_error_wraps_message(
        self,
        facade_coordinator: UnifiFacadeCoordinator,
        protect_coordinator: UnifiProtectCoordinator,
    ):
        """Any other API failure keeps the house "Unable to ..." wording."""
        facade_coordinator.protect_client.arm_profiles.enable = AsyncMock(
            side_effect=UniFiResponseError("x", 500)
        )

        with (
            patch.object(
                protect_coordinator, "async_request_refresh", AsyncMock()
            ) as refresh,
            pytest.raises(
                HomeAssistantError, match="Unable to arm the UniFi Protect alarm"
            ),
        ):
            await facade_coordinator.async_arm_alarm()

        refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_async_disarm_alarm_without_protect_coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        config_coordinator: UnifiConfigCoordinator,
        device_coordinator: UnifiDeviceCoordinator,
    ):
        """A facade with a client but no Protect coordinator still sends the request."""
        protect_client = _create_mock_protect_client()
        protect_client.arm_profiles.disable = AsyncMock(return_value=True)
        facade = UnifiFacadeCoordinator(
            hass=hass,
            network_client=_create_mock_network_client(),
            protect_client=protect_client,
            entry=mock_config_entry,
            config_coordinator=config_coordinator,
            device_coordinator=device_coordinator,
            protect_coordinator=None,
        )
        try:
            await facade.async_disarm_alarm()
        finally:
            await facade.async_shutdown()
            await config_coordinator.async_shutdown()
            await device_coordinator.async_shutdown()

        protect_client.arm_profiles.disable.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_async_arm_global_alarm_manager_without_protect_coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        config_coordinator: UnifiConfigCoordinator,
        device_coordinator: UnifiDeviceCoordinator,
    ):
        """The refusal is still translated when there is no coordinator to tell."""
        protect_client = _create_mock_protect_client()
        protect_client.arm_profiles.enable = AsyncMock(
            side_effect=UniFiGlobalAlarmManagerError("x", 400, "body")
        )
        facade = UnifiFacadeCoordinator(
            hass=hass,
            network_client=_create_mock_network_client(),
            protect_client=protect_client,
            entry=mock_config_entry,
            config_coordinator=config_coordinator,
            device_coordinator=device_coordinator,
            protect_coordinator=None,
        )
        try:
            with pytest.raises(HomeAssistantError) as err:
                await facade.async_arm_alarm()
        finally:
            await facade.async_shutdown()
            await config_coordinator.async_shutdown()
            await device_coordinator.async_shutdown()

        assert err.value.translation_key == "global_alarm_manager"

    @pytest.mark.asyncio
    async def test_async_arm_alarm_without_protect_client_raises(
        self, facade_coordinator_no_protect: UnifiFacadeCoordinator
    ):
        """Arming without Protect fails with the shared "not available" error."""
        with pytest.raises(HomeAssistantError, match="Protect is not available"):
            await facade_coordinator_no_protect.async_arm_alarm()

    @pytest.mark.asyncio
    async def test_async_create_liveview(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """Test async_create_liveview delegates correctly."""
        facade_coordinator.protect_client.liveviews.create = AsyncMock()
        await facade_coordinator.async_create_liveview(
            name="Main View", layout=2, is_default=True
        )
        facade_coordinator.protect_client.liveviews.create.assert_called_once_with(
            name="Main View", layout=2, isDefault=True
        )

    @pytest.mark.asyncio
    async def test_protect_methods_raise_without_protect(
        self, facade_coordinator_no_protect: UnifiFacadeCoordinator
    ):
        """Test protect methods raise when protect is unavailable."""
        coord = facade_coordinator_no_protect
        with pytest.raises(HomeAssistantError, match="Protect is not available"):
            await coord.async_update_camera("cam1")
        with pytest.raises(HomeAssistantError, match="Protect is not available"):
            await coord.async_play_chime("chime1")
        with pytest.raises(HomeAssistantError, match="Protect is not available"):
            await coord.async_trigger_alarm("alarm1")

    @pytest.mark.asyncio
    async def test_async_shutdown_releases_sub_coordinator_listeners(
        self,
        facade_coordinator: UnifiFacadeCoordinator,
        config_coordinator: UnifiConfigCoordinator,
        device_coordinator: UnifiDeviceCoordinator,
        protect_coordinator: UnifiProtectCoordinator,
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
        facade_coordinator: UnifiFacadeCoordinator,
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

    @pytest.mark.asyncio
    @pytest.mark.parametrize("duration", [10, None])
    async def test_async_play_siren_passes_duration(
        self, facade_coordinator: UnifiFacadeCoordinator, duration: int | None
    ):
        """async_play_siren awaits the endpoint with the duration as a keyword."""
        sirens = facade_coordinator.protect_client.sirens
        sirens.play = AsyncMock(return_value=True)

        await facade_coordinator.async_play_siren("siren_1", duration=duration)

        sirens.play.assert_awaited_once_with("siren_1", duration=duration)

    @pytest.mark.asyncio
    async def test_async_set_siren_volume(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """async_set_siren_volume awaits the endpoint's set_volume."""
        sirens = facade_coordinator.protect_client.sirens
        sirens.set_volume = AsyncMock()

        await facade_coordinator.async_set_siren_volume("siren_1", 50)

        sirens.set_volume.assert_awaited_once_with("siren_1", 50)

    @pytest.mark.asyncio
    async def test_async_stop_siren_marks_stopped(
        self,
        facade_coordinator: UnifiFacadeCoordinator,
        protect_coordinator: UnifiProtectCoordinator,
    ):
        """A stop is sent, then the cache shows the siren off in the facade data."""
        sirens = facade_coordinator.protect_client.sirens
        sirens.stop = AsyncMock(return_value=True)
        active = copy.deepcopy(SAMPLE_SIREN)
        active["sirenStatus"] = {"isActive": True}
        protect_coordinator.data["sirens"] = {"siren_1": active}

        await facade_coordinator.async_stop_siren("siren_1")

        sirens.stop.assert_awaited_once_with("siren_1")
        siren = facade_coordinator.data["protect"]["sirens"]["siren_1"]
        assert siren["sirenStatus"]["isActive"] is False

    @pytest.mark.asyncio
    async def test_async_stop_siren_without_protect_coordinator(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        config_coordinator: UnifiConfigCoordinator,
        device_coordinator: UnifiDeviceCoordinator,
    ):
        """A facade with a client but no Protect coordinator still sends the stop."""
        protect_client = _create_mock_protect_client()
        protect_client.sirens.stop = AsyncMock(return_value=True)
        facade = UnifiFacadeCoordinator(
            hass=hass,
            network_client=_create_mock_network_client(),
            protect_client=protect_client,
            entry=mock_config_entry,
            config_coordinator=config_coordinator,
            device_coordinator=device_coordinator,
            protect_coordinator=None,
        )
        try:
            await facade.async_stop_siren("siren_1")
        finally:
            await facade.async_shutdown()
            await config_coordinator.async_shutdown()
            await device_coordinator.async_shutdown()

        protect_client.sirens.stop.assert_awaited_once_with("siren_1")

    @pytest.mark.asyncio
    async def test_async_play_siren_api_error_raises_home_assistant_error(
        self, facade_coordinator: UnifiFacadeCoordinator
    ):
        """An API failure surfaces as a HomeAssistantError naming the siren."""
        facade_coordinator.protect_client.sirens.play = AsyncMock(
            side_effect=UniFiResponseError("x", 500)
        )

        with pytest.raises(HomeAssistantError, match="Unable to sound siren siren_1"):
            await facade_coordinator.async_play_siren("siren_1")

    @pytest.mark.asyncio
    async def test_async_stop_siren_api_error_leaves_cache_untouched(
        self,
        facade_coordinator: UnifiFacadeCoordinator,
        protect_coordinator: UnifiProtectCoordinator,
    ):
        """A failed stop must not pretend the siren is off."""
        facade_coordinator.protect_client.sirens.stop = AsyncMock(
            side_effect=UniFiResponseError("x", 500)
        )
        active = copy.deepcopy(SAMPLE_SIREN)
        active["sirenStatus"] = {"isActive": True}
        protect_coordinator.data["sirens"] = {"siren_1": active}

        with pytest.raises(HomeAssistantError, match="Unable to stop siren siren_1"):
            await facade_coordinator.async_stop_siren("siren_1")

        assert protect_coordinator.data["sirens"]["siren_1"]["sirenStatus"] == {
            "isActive": True
        }

    def test_aggregate_data_includes_rule_sections(
        self, facade_coordinator: UnifiFacadeCoordinator
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
        self, facade_coordinator: UnifiFacadeCoordinator
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
        _RULE_SECTION_PARAMS,
    )
    def test_rule_section_availability_passthrough(
        self,
        facade_coordinator: UnifiFacadeCoordinator,
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
    async def test_async_set_port_forward_enabled_uses_internal_reference(
        self, facade_coordinator: UnifiFacadeCoordinator
    ) -> None:
        """Port forward toggle resolves site ONLY via internalReference."""
        facade_coordinator._config_coordinator.data = {
            "sites": {
                "site_alpha": {
                    "id": "site_alpha",
                    "name": "Office",
                    "internalReference": "alpha",
                },
                "site_beta": {
                    "id": "site_beta",
                    "name": "Office",
                    "internalReference": "beta",
                },
            }
        }
        facade_coordinator._device_coordinator._legacy_site_names = {
            "site_alpha": "beta",
            "site_beta": "beta",
        }
        facade_coordinator.network_client.port_forwards.update_port_forward = (
            AsyncMock()
        )

        await facade_coordinator.async_set_port_forward_enabled(
            "site_alpha", "pf1", enabled=False
        )
        facade_coordinator.network_client.port_forwards.update_port_forward.assert_called_once_with(
            "alpha", "pf1", enabled=False
        )

    @pytest.mark.asyncio
    async def test_async_set_traffic_rule_enabled_uses_internal_reference(
        self, facade_coordinator: UnifiFacadeCoordinator
    ) -> None:
        """Traffic rule toggle resolves site ONLY via internalReference."""
        facade_coordinator._config_coordinator.data = {
            "sites": {
                "site_alpha": {
                    "id": "site_alpha",
                    "name": "Office",
                    "internalReference": "alpha",
                },
                "site_beta": {
                    "id": "site_beta",
                    "name": "Office",
                    "internalReference": "beta",
                },
            }
        }
        facade_coordinator._device_coordinator._legacy_site_names = {
            "site_alpha": "beta",
            "site_beta": "beta",
        }
        facade_coordinator.network_client.traffic_rules.update_traffic_rule = (
            AsyncMock()
        )

        await facade_coordinator.async_set_traffic_rule_enabled(
            "site_alpha", "tr1", enabled=False
        )
        facade_coordinator.network_client.traffic_rules.update_traffic_rule.assert_called_once_with(
            "alpha", "tr1", enabled=False
        )

    @pytest.mark.asyncio
    async def test_async_set_nat_rule_enabled_uses_internal_reference(
        self, facade_coordinator: UnifiFacadeCoordinator
    ) -> None:
        """NAT rule toggle resolves site ONLY via internalReference."""
        facade_coordinator._config_coordinator.data = {
            "sites": {
                "site_alpha": {
                    "id": "site_alpha",
                    "name": "Office",
                    "internalReference": "alpha",
                },
                "site_beta": {
                    "id": "site_beta",
                    "name": "Office",
                    "internalReference": "beta",
                },
            }
        }
        facade_coordinator._device_coordinator._legacy_site_names = {
            "site_alpha": "beta",
            "site_beta": "beta",
        }
        facade_coordinator.network_client.nat_rules.update_nat_rule = AsyncMock()

        await facade_coordinator.async_set_nat_rule_enabled(
            "site_alpha", "nat1", enabled=False
        )
        facade_coordinator.network_client.nat_rules.update_nat_rule.assert_called_once_with(
            "alpha", "nat1", enabled=False
        )

    @pytest.mark.asyncio
    async def test_async_export_network_rules_passes_console_and_sites(
        self, facade_coordinator: UnifiFacadeCoordinator
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
        self, facade_coordinator: UnifiFacadeCoordinator
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

    @pytest.mark.asyncio
    async def test_async_set_nat_rule_enabled_console_managed_refreshes_and_raises(
        self, facade_coordinator: UnifiFacadeCoordinator
    ) -> None:
        """UniFiRuleNotEditableError requests refresh and raises translated error."""
        facade_coordinator._config_coordinator.data = {
            "sites": {"site1": {"id": "site1", "internalReference": "default"}},
            "nat_rules": {"site1": {"nat1": {"description": "DNS"}}},
        }
        facade_coordinator.network_client.nat_rules.update_nat_rule = AsyncMock(
            side_effect=UniFiRuleNotEditableError(
                "NAT rule nat1 is managed by the console"
            )
        )
        facade_coordinator.async_request_refresh = AsyncMock()

        with pytest.raises(HomeAssistantError) as exc_info:
            await facade_coordinator.async_set_nat_rule_enabled(
                "site1", "nat1", enabled=False
            )

        facade_coordinator.async_request_refresh.assert_awaited_once()
        assert exc_info.value.translation_key == "network_rule_console_managed"

    @pytest.mark.parametrize(
        ("setter_name", "endpoint_mock_path", "rule_type_placeholder"),
        [
            ("async_set_nat_rule_enabled", "nat_rules.update_nat_rule", "NAT rule"),
            (
                "async_set_static_route_enabled",
                "static_routes.update_static_route",
                "static route",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_async_set_new_rule_enabled_not_found_is_translated(
        self,
        facade_coordinator: UnifiFacadeCoordinator,
        setter_name: str,
        endpoint_mock_path: str,
        rule_type_placeholder: str,
    ) -> None:
        """A vanished rule raises the translated error and requests a refresh."""
        if "nat" in setter_name:
            section_key = "nat_rules"
            rule_id = "nat1"
            rule_record = {"description": "DNS"}
            expected_name = "DNS"
        else:
            section_key = "static_routes"
            rule_id = "sr1"
            rule_record = {"name": "Lab"}
            expected_name = "Lab"

        facade_coordinator._config_coordinator.data = {
            "sites": {"site1": {"id": "site1", "internalReference": "default"}},
            section_key: {"site1": {rule_id: rule_record}},
        }
        ep_name, meth_name = endpoint_mock_path.split(".")
        setattr(
            getattr(facade_coordinator.network_client, ep_name),
            meth_name,
            AsyncMock(
                side_effect=UniFiNotFoundError(
                    f"Rule {rule_id} not found", status_code=404
                )
            ),
        )
        facade_coordinator.async_request_refresh = AsyncMock()

        setter = getattr(facade_coordinator, setter_name)
        with pytest.raises(HomeAssistantError) as exc_info:
            await setter("site1", rule_id, enabled=True)

        assert exc_info.value.translation_domain == DOMAIN
        assert exc_info.value.translation_key == "network_rule_not_found"
        assert exc_info.value.translation_placeholders == {
            "rule_type": rule_type_placeholder,
            "rule_name": expected_name,
        }
        facade_coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.parametrize(
        ("setter_name", "endpoint_mock_path", "rule_type_placeholder"),
        [
            ("async_set_nat_rule_enabled", "nat_rules.update_nat_rule", "NAT rule"),
            (
                "async_set_static_route_enabled",
                "static_routes.update_static_route",
                "static route",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_async_set_new_rule_enabled_api_error_is_translated(
        self,
        facade_coordinator: UnifiFacadeCoordinator,
        setter_name: str,
        endpoint_mock_path: str,
        rule_type_placeholder: str,
    ) -> None:
        """API error during rule toggle raises translated error."""
        if "nat" in setter_name:
            section_key = "nat_rules"
            rule_id = "nat1"
            rule_record = {"description": "DNS"}
            expected_name = "DNS"
        else:
            section_key = "static_routes"
            rule_id = "sr1"
            rule_record = {"name": "Lab"}
            expected_name = "Lab"

        facade_coordinator._config_coordinator.data = {
            "sites": {"site1": {"id": "site1", "internalReference": "default"}},
            section_key: {"site1": {rule_id: rule_record}},
        }
        ep_name, meth_name = endpoint_mock_path.split(".")
        setattr(
            getattr(facade_coordinator.network_client, ep_name),
            meth_name,
            AsyncMock(side_effect=UniFiConnectionError("Connection failed")),
        )

        setter = getattr(facade_coordinator, setter_name)
        with pytest.raises(HomeAssistantError) as exc_info:
            await setter("site1", rule_id, enabled=True)

        assert exc_info.value.translation_domain == DOMAIN
        assert exc_info.value.translation_key == "network_rule_update_failed"
        assert exc_info.value.translation_placeholders == {
            "rule_type": rule_type_placeholder,
            "rule_name": expected_name,
        }

    @pytest.mark.asyncio
    async def test_async_set_static_route_enabled_uses_internal_reference(
        self, facade_coordinator: UnifiFacadeCoordinator
    ) -> None:
        """Static route toggle resolves site ONLY via internalReference."""
        facade_coordinator._config_coordinator.data = {
            "sites": {
                "site_alpha": {
                    "id": "site_alpha",
                    "name": "Office",
                    "internalReference": "alpha",
                },
                "site_beta": {
                    "id": "site_beta",
                    "name": "Office",
                    "internalReference": "beta",
                },
            }
        }
        facade_coordinator._device_coordinator._legacy_site_names = {
            "site_alpha": "beta",
            "site_beta": "beta",
        }
        facade_coordinator.network_client.static_routes.update_static_route = (
            AsyncMock()
        )

        await facade_coordinator.async_set_static_route_enabled(
            "site_alpha", "sr1", enabled=False
        )
        facade_coordinator.network_client.static_routes.update_static_route.assert_called_once_with(
            "alpha", "sr1", enabled=False
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("action_method", "endpoint_attr", "update_method"),
        [
            (
                "async_set_port_forward_enabled",
                "port_forwards",
                "update_port_forward",
            ),
            (
                "async_set_traffic_rule_enabled",
                "traffic_rules",
                "update_traffic_rule",
            ),
            (
                "async_set_nat_rule_enabled",
                "nat_rules",
                "update_nat_rule",
            ),
            (
                "async_set_static_route_enabled",
                "static_routes",
                "update_static_route",
            ),
        ],
    )
    async def test_async_set_rule_enabled_without_internal_reference_raises(
        self,
        facade_coordinator: UnifiFacadeCoordinator,
        action_method: str,
        endpoint_attr: str,
        update_method: str,
    ) -> None:
        """Toggling a rule without internalReference raises HomeAssistantError."""
        facade_coordinator._config_coordinator.data = {
            "sites": {"site1": {"id": "site1"}}
        }
        action_fn = getattr(facade_coordinator, action_method)
        endpoint = getattr(facade_coordinator.network_client, endpoint_attr)
        setattr(endpoint, update_method, AsyncMock())

        with pytest.raises(HomeAssistantError) as exc_info:
            await action_fn("site1", "rule1", enabled=True)

        assert exc_info.value.translation_key == "legacy_site_not_found"
        getattr(endpoint, update_method).assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        (
            "action_method",
            "endpoint_attr",
            "update_method",
            "rule_id",
            "expected_msg",
        ),
        [
            (
                "async_set_port_forward_enabled",
                "port_forwards",
                "update_port_forward",
                "pf1",
                "Unable to update port forward pf1",
            ),
            (
                "async_set_traffic_rule_enabled",
                "traffic_rules",
                "update_traffic_rule",
                "tr1",
                "Unable to update traffic rule tr1",
            ),
        ],
    )
    async def test_async_set_rule_enabled_wraps_api_errors(
        self,
        facade_coordinator: UnifiFacadeCoordinator,
        action_method: str,
        endpoint_attr: str,
        update_method: str,
        rule_id: str,
        *,
        expected_msg: str,
    ) -> None:
        """API errors during rule toggles are wrapped in HomeAssistantError."""
        facade_coordinator._config_coordinator.data = {
            "sites": {"site1": {"id": "site1", "internalReference": "default"}}
        }
        endpoint = getattr(facade_coordinator.network_client, endpoint_attr)
        setattr(
            endpoint,
            update_method,
            AsyncMock(side_effect=UniFiConnectionError("Connection failed")),
        )
        action_fn = getattr(facade_coordinator, action_method)

        with pytest.raises(HomeAssistantError) as exc_info:
            await action_fn("site1", rule_id, enabled=True)

        assert expected_msg in str(exc_info.value)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("action_method", "endpoint_attr", "update_method"),
        [
            (
                "async_set_port_forward_enabled",
                "port_forwards",
                "update_port_forward",
            ),
            (
                "async_set_traffic_rule_enabled",
                "traffic_rules",
                "update_traffic_rule",
            ),
            (
                "async_set_nat_rule_enabled",
                "nat_rules",
                "update_nat_rule",
            ),
            (
                "async_set_static_route_enabled",
                "static_routes",
                "update_static_route",
            ),
        ],
    )
    async def test_async_set_rule_enabled_reraises_home_assistant_error(
        self,
        facade_coordinator: UnifiFacadeCoordinator,
        action_method: str,
        endpoint_attr: str,
        update_method: str,
    ) -> None:
        """A HomeAssistantError from a rule toggle propagates unwrapped."""
        facade_coordinator._config_coordinator.data = {
            "sites": {"site1": {"id": "site1", "internalReference": "default"}}
        }
        error = HomeAssistantError("already translated")
        endpoint = getattr(facade_coordinator.network_client, endpoint_attr)
        setattr(endpoint, update_method, AsyncMock(side_effect=error))
        action_fn = getattr(facade_coordinator, action_method)

        with pytest.raises(HomeAssistantError) as exc_info:
            await action_fn("site1", "rule1", enabled=True)

        assert exc_info.value is error

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        (
            "action_method",
            "endpoint_attr",
            "update_method",
            "rule_id",
            "rule_key",
            "name_key",
            "rule_name",
            "error_label",
        ),
        [
            (
                "async_set_port_forward_enabled",
                "port_forwards",
                "update_port_forward",
                "pf1",
                "port_forwards",
                "name",
                "Plex",
                "port forward",
            ),
            (
                "async_set_traffic_rule_enabled",
                "traffic_rules",
                "update_traffic_rule",
                "tr1",
                "traffic_rules",
                "description",
                "Bedtime",
                "traffic rule",
            ),
        ],
    )
    async def test_async_set_rule_enabled_not_found_refreshes_and_raises_rule_name(
        self,
        facade_coordinator: UnifiFacadeCoordinator,
        action_method: str,
        endpoint_attr: str,
        update_method: str,
        rule_id: str,
        *,
        rule_key: str,
        name_key: str,
        rule_name: str,
        error_label: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When a toggled rule is vanished, request refresh and raise.

        Verifies that HomeAssistantError is raised without traceback.
        """
        facade_coordinator._config_coordinator.data = {
            "sites": {"site1": {"id": "site1", "internalReference": "default"}},
            rule_key: {"site1": {rule_id: {name_key: rule_name}}},
        }
        endpoint = getattr(facade_coordinator.network_client, endpoint_attr)
        setattr(
            endpoint,
            update_method,
            AsyncMock(
                side_effect=UniFiNotFoundError(
                    f"Rule {rule_id} not found", status_code=404
                )
            ),
        )
        facade_coordinator.async_request_refresh = AsyncMock()
        action_fn = getattr(facade_coordinator, action_method)

        with pytest.raises(HomeAssistantError) as exc_info:
            await action_fn("site1", rule_id, enabled=True)

        assert f"Unable to update {error_label} {rule_name}" in str(exc_info.value)
        facade_coordinator.async_request_refresh.assert_awaited_once()
        assert not any(record.levelno >= logging.ERROR for record in caplog.records)


# ============================================================================
# Integration Tests for Coordinator Data Flow
# ============================================================================


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
        config_coord = UnifiConfigCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )

        device_coord = UnifiDeviceCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
            config_coordinator=config_coord,
        )

        protect_coord = UnifiProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )

        facade_coord = UnifiFacadeCoordinator(
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


class TestProtectCoordinatorEdgeCases:
    """Tests for edge cases in UnifiProtectCoordinator."""

    @pytest.fixture
    def coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> UnifiProtectCoordinator:
        """Create a protect coordinator for testing."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        return UnifiProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )

    def test_site_id_default(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ):
        """Test the coordinator defaults site_id to 'default' when unset."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()

        coordinator = UnifiProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )

        assert coordinator._site_id == "default"

    def test_site_id_custom(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ):
        """Test the coordinator accepts a custom site_id (REMOTE routing)."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()

        coordinator = UnifiProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
            site_id="site2",
        )

        assert coordinator._site_id == "site2"

    @pytest.mark.asyncio
    async def test_fetch_viewers_no_viewers_attribute(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ):
        """Test fetch viewers when protect_client lacks viewers attribute."""
        network_client = _create_mock_network_client()
        protect_client = MagicMock()
        protect_client.register_device_update_callback = MagicMock()
        protect_client.register_event_update_callback = MagicMock()
        protect_client.cameras = MagicMock()
        protect_client.cameras.get_all = AsyncMock(return_value=[])
        protect_client.lights = MagicMock()
        protect_client.lights.get_all = AsyncMock(return_value=[])
        protect_client.sensors = MagicMock()
        protect_client.sensors.get_all = AsyncMock(return_value=[])
        protect_client.nvr = MagicMock()
        protect_client.nvr.get = AsyncMock(return_value=MagicMock(id="nvr1"))
        protect_client.chimes = MagicMock()
        protect_client.chimes.get_all = AsyncMock(return_value=[])
        # Remove viewers attribute
        del protect_client.viewers
        protect_client.liveviews = MagicMock()
        protect_client.liveviews.get_all = AsyncMock(return_value=[])

        coordinator = UnifiProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )

        result = await coordinator._async_update_data()

        # Should not raise and viewers should be empty
        assert result["viewers"] == {}

    @pytest.mark.asyncio
    async def test_fetch_liveviews_no_liveviews_attribute(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ):
        """Test fetch liveviews when protect_client lacks liveviews attribute."""
        network_client = _create_mock_network_client()
        protect_client = MagicMock()
        protect_client.register_device_update_callback = MagicMock()
        protect_client.register_event_update_callback = MagicMock()
        protect_client.cameras = MagicMock()
        protect_client.cameras.get_all = AsyncMock(return_value=[])
        protect_client.lights = MagicMock()
        protect_client.lights.get_all = AsyncMock(return_value=[])
        protect_client.sensors = MagicMock()
        protect_client.sensors.get_all = AsyncMock(return_value=[])
        protect_client.nvr = MagicMock()
        protect_client.nvr.get = AsyncMock(return_value=MagicMock(id="nvr1"))
        protect_client.chimes = MagicMock()
        protect_client.chimes.get_all = AsyncMock(return_value=[])
        protect_client.viewers = MagicMock()
        protect_client.viewers.get_all = AsyncMock(return_value=[])
        # Remove liveviews attribute
        del protect_client.liveviews

        coordinator = UnifiProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )

        result = await coordinator._async_update_data()

        # Should not raise and liveviews should be empty
        assert result["liveviews"] == {}

    @pytest.mark.asyncio
    async def test_fetch_cameras_feature_flags_not_dict(
        self, coordinator: UnifiProtectCoordinator
    ):
        """Test camera fetch when featureFlags is not a dict."""
        camera_mock = MagicMock()
        camera_mock.id = "camera1"
        camera_mock.name = "Test Camera"
        camera_mock.feature_flags = "not_a_dict"  # Invalid type
        # Make model_dump return the non-dict featureFlags
        camera_mock.model_dump = MagicMock(
            return_value={
                "id": "camera1",
                "name": "Test Camera",
                "featureFlags": "not_a_dict",
            }
        )
        coordinator.protect_client.cameras.get_all = AsyncMock(
            return_value=[camera_mock]
        )

        result = await coordinator._async_update_data()

        # Should still work, smartDetectTypes should be empty list
        assert result["cameras"]["camera1"]["smartDetectTypes"] == []

    def test_handle_event_unknown_type(self, coordinator: UnifiProtectCoordinator):
        """Test handling event with unknown type."""
        # Add camera first
        coordinator.data["cameras"]["camera1"] = {"id": "camera1"}

        # Process unknown event type
        coordinator._handle_event_update(
            "unknown_event",
            {"id": "event1", "device": "camera1", "start": 123},
        )

        # Event should be stored but no device update
        assert "unknown_event" in coordinator.data["events"]

    def test_handle_event_device_not_found(self, coordinator: UnifiProtectCoordinator):
        """Test handling event when device not in data."""
        coordinator._handle_event_update(
            "motion",
            {"id": "event1", "device": "nonexistent_device", "start": 123},
        )

        # Event should be stored but no error
        assert "motion" in coordinator.data["events"]


class TestUnifiInsightsInnerSpaceCoordinator:
    """Tests for UnifiInsightsInnerSpaceCoordinator and facade integration."""

    @pytest.mark.asyncio
    async def test_update_and_snapshot_preservation_on_failure(
        self,
        hass: HomeAssistant,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        mock_innerspace_client: MagicMock,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test InnerSpace refresh normalizes data and preserves snapshot on failure."""
        mock_innerspace_client.get_project.return_value = InnerSpaceProject(
            project=InnerSpaceProjectIdentity(id="proj-1"),
        )
        mock_innerspace_client.list_floor_plans.return_value = [
            InnerSpaceFloorPlan(
                id="fp-1",
                name="First Floor",
                floor_number=1,
                site_id="default",
                ppm=20.0,
            )
        ]
        mock_innerspace_client.list_access_points.return_value = [
            InnerSpaceAccessPoint(
                id="ap-1",
                name="Office AP",
                model="U6-Pro",
                mac="AA:BB:CC:DD:EE:FF",
                floor_plan_id="fp-1",
                x=10.0,
                y=20.0,
                status="online",
            )
        ]
        mock_innerspace_client.list_switches.return_value = [
            InnerSpaceSwitch(
                id="sw-1",
                name="Office SW",
                model="USW-24",
                floor_plan_id="fp-1",
                x=5.0,
                y=15.0,
                status="online",
            )
        ]
        mock_innerspace_client.list_inventory.return_value = [
            InnerSpaceInventoryDevice(
                id="inv-1",
                name="Spare AP",
                model="U6-LR",
                mac="AA:BB:CC:99:99:99",
            )
        ]

        coord = UnifiInsightsInnerSpaceCoordinator(
            hass=hass,
            network_client=mock_network_client,
            protect_client=mock_protect_client,
            innerspace_client=mock_innerspace_client,
            entry=mock_config_entry,
        )

        data = await coord._async_update_data()
        assert data["project"]["id"] == "proj-1"
        assert "fp-1" in data["floor_plans"]
        assert "ap-1" in data["access_points"]
        assert "sw-1" in data["switches"]
        assert "inv-1" in data["inventory"]
        assert coord.available is True

        # Transient failure raises UpdateFailed while keeping self.data
        mock_innerspace_client.get_project.side_effect = UniFiConnectionError("Down")
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()
        assert coord.available is False
        assert "ap-1" in coord.data["access_points"]

        # Auth error raises ConfigEntryAuthFailed
        mock_innerspace_client.get_project.side_effect = UniFiAuthenticationError(
            "Expired", status_code=401
        )
        with pytest.raises(ConfigEntryAuthFailed):
            await coord._async_update_data()

        # Timeout, response, and unexpected errors raise UpdateFailed
        for exc in (
            UniFiTimeoutError("Timeout"),
            UniFiResponseError("Bad gateway", status_code=502),
            RuntimeError("Unexpected"),
        ):
            mock_innerspace_client.get_project.side_effect = exc
            with pytest.raises(UpdateFailed):
                await coord._async_update_data()

    @pytest.mark.asyncio
    async def test_stale_floor_plan_device_removed_current_kept(
        self,
        hass: HomeAssistant,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        mock_innerspace_client: MagicMock,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """A floor plan that disappears loses its device; present ones keep it."""
        mock_config_entry.add_to_hass(hass)
        registry = dr.async_get(hass)
        for plan_id in ("fp-1", "fp-2"):
            registry.async_get_or_create(
                config_entry_id=mock_config_entry.entry_id,
                identifiers={(DOMAIN, f"innerspace_floor_plan_{plan_id}")},
            )

        def plans(*ids: str) -> list[InnerSpaceFloorPlan]:
            return [
                InnerSpaceFloorPlan(
                    id=plan_id,
                    name=plan_id,
                    image_url=f"/v1/assets/{plan_id}/plan.png",
                )
                for plan_id in ids
            ]

        mock_innerspace_client.get_project.return_value = InnerSpaceProject(
            project=InnerSpaceProjectIdentity(id="proj-1"),
        )
        coord = UnifiInsightsInnerSpaceCoordinator(
            hass=hass,
            network_client=mock_network_client,
            protect_client=mock_protect_client,
            innerspace_client=mock_innerspace_client,
            entry=mock_config_entry,
        )

        def device(plan_id: str) -> object:
            return async_get_device_entry(
                registry,
                (DOMAIN, f"innerspace_floor_plan_{plan_id}"),
                mock_config_entry.entry_id,
            )

        mock_innerspace_client.list_floor_plans.return_value = plans("fp-1", "fp-2")
        await coord._async_update_data()
        assert device("fp-1") is not None
        assert device("fp-2") is not None

        mock_innerspace_client.list_floor_plans.return_value = plans("fp-1")
        await coord._async_update_data()
        assert device("fp-1") is not None
        assert device("fp-2") is None

        # A plan that vanishes without ever having had a device is a no-op.
        registry.async_clear_config_entry(mock_config_entry.entry_id)
        mock_innerspace_client.list_floor_plans.return_value = []
        await coord._async_update_data()
        assert device("fp-1") is None

    @pytest.mark.asyncio
    async def test_failed_refresh_keeps_floor_plan_devices(
        self,
        hass: HomeAssistant,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        mock_innerspace_client: MagicMock,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """A failed poll keeps the last snapshot and must not prune devices."""
        mock_config_entry.add_to_hass(hass)
        registry = dr.async_get(hass)
        registry.async_get_or_create(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "innerspace_floor_plan_fp-1")},
        )
        mock_innerspace_client.get_project.return_value = InnerSpaceProject(
            project=InnerSpaceProjectIdentity(id="proj-1"),
        )
        mock_innerspace_client.list_floor_plans.return_value = [
            InnerSpaceFloorPlan(
                id="fp-1", name="fp-1", image_url="/v1/assets/fp-1/plan.png"
            )
        ]
        coord = UnifiInsightsInnerSpaceCoordinator(
            hass=hass,
            network_client=mock_network_client,
            protect_client=mock_protect_client,
            innerspace_client=mock_innerspace_client,
            entry=mock_config_entry,
        )
        await coord._async_update_data()

        mock_innerspace_client.get_project.side_effect = UniFiConnectionError("Down")
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()
        assert (
            async_get_device_entry(
                registry,
                (DOMAIN, "innerspace_floor_plan_fp-1"),
                mock_config_entry.entry_id,
            )
            is not None
        )

    @pytest.mark.asyncio
    async def test_refresh_retains_warning_on_200_html_and_preserves_snapshot(
        self,
        hass: HomeAssistant,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        mock_config_entry: MockConfigEntry,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """InnerSpace refresh logs WARNING on 200 HTML and keeps snapshot."""
        real_client = UniFiInnerSpaceClient(
            auth=ApiKeyAuth(api_key="test-key"),
            base_url="https://192.168.1.1",
            connection_type=ConnectionType.LOCAL,
        )
        real_client.get_project = AsyncMock(
            return_value=InnerSpaceProject(
                project=InnerSpaceProjectIdentity(id="proj-1"),
            )
        )
        real_client.list_floor_plans = AsyncMock(return_value=[])
        real_client.list_access_points = AsyncMock(
            return_value=[
                InnerSpaceAccessPoint(
                    id="ap-1",
                    name="Office AP",
                    model="U6-Pro",
                    mac="AA:BB:CC:DD:EE:FF",
                    floor_plan_id="fp-1",
                    x=10.0,
                    y=20.0,
                    status="online",
                )
            ]
        )
        real_client.list_switches = AsyncMock(return_value=[])
        real_client.list_inventory = AsyncMock(return_value=[])

        coord = UnifiInsightsInnerSpaceCoordinator(
            hass=hass,
            network_client=mock_network_client,
            protect_client=mock_protect_client,
            innerspace_client=real_client,
            entry=mock_config_entry,
        )
        await coord._async_update_data()
        real_client.get_project.assert_awaited_once_with()
        assert "ap-1" in coord.data["access_points"]

        # Restore real get_project method and simulate 200 HTML response on refresh
        real_client.get_project = UniFiInnerSpaceClient.get_project.__get__(
            real_client, UniFiInnerSpaceClient
        )
        html_response = MagicMock()
        html_response.status = 200
        html_response.text = AsyncMock(
            return_value="<!doctype html><html><body>UniFi OS</body></html>"
        )
        html_response.headers = {}
        html_response.method = "GET"
        html_response.history = ()
        html_response.url = MagicMock()
        html_response.url.path = "/proxy/innerspace/integration/v1/project"
        html_response.json = AsyncMock(
            side_effect=aiohttp.ContentTypeError(MagicMock(), MagicMock())
        )
        request_ctx = MagicMock()
        request_ctx.__aenter__ = AsyncMock(return_value=html_response)
        request_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=request_ctx)
        real_client._ensure_session = AsyncMock(return_value=mock_session)
        real_client._throttle = AsyncMock()

        with caplog.at_level(logging.WARNING), pytest.raises(UpdateFailed):
            await coord._async_update_data()

        assert any(
            "Response is not JSON for GET /proxy/innerspace/integration/v1/project"
            in r.getMessage()
            for r in caplog.records
        )
        assert coord.available is False
        assert "ap-1" in coord.data["access_points"]

    @pytest.mark.asyncio
    async def test_config_coordinator_internet_activity_windows_and_resilience(
        self,
        hass: HomeAssistant,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test rolling window aggregation, hourly reuse, and transient recovery."""
        now_ms = 1_800_000_000_000
        windows = aggregate_internet_activity_windows(
            five_minute_buckets=[
                {
                    "time": now_ms - WINDOW_1H_MS - 1,
                    "wan-rx_bytes": 9999,
                    "wan-tx_bytes": 9999,
                },
                {
                    "time": now_ms - WINDOW_1H_MS,
                    "wan-rx_bytes": 1000,
                    "wan-tx_bytes": 500,
                    "wan2-rx_bytes": 250,
                    "wan2-tx_bytes": 100,
                },
            ],
            hourly_buckets=[
                {
                    "time": now_ms - WINDOW_1W_MS - 1,
                    "wan-rx_bytes": 8888,
                    "wan-tx_bytes": 8888,
                },
                {
                    "time": now_ms - WINDOW_1D_MS - 3_600_000,
                    "wan-rx_bytes": 2000,
                    "wan-tx_bytes": 800,
                },
                {
                    "time": now_ms - WINDOW_1D_MS,
                    "wan-rx_bytes": 3000,
                    "wan-tx_bytes": 1200,
                },
            ],
            daily_buckets=[
                {
                    "time": now_ms - WINDOW_1M_MS,
                    "wan-rx_bytes": 10000,
                    "wan-tx_bytes": 4000,
                }
            ],
            now_ms=now_ms,
        )
        assert windows == {
            "1h": {"rx_bytes": 1250, "tx_bytes": 600},
            "1d": {"rx_bytes": 3000, "tx_bytes": 1200},
            "1w": {"rx_bytes": 5000, "tx_bytes": 2000},
            "1m": {"rx_bytes": 10000, "tx_bytes": 4000},
        }
        assert aggregate_internet_activity_windows([], [], [], now_ms=now_ms) == {}

        mock_network_client.sites.get_all = AsyncMock(
            return_value=[
                {
                    "id": "site1",
                    "name": "Default",
                    "internalReference": "default",
                }
            ]
        )
        coord = UnifiConfigCoordinator(
            hass=hass,
            network_client=mock_network_client,
            protect_client=mock_protect_client,
            entry=mock_config_entry,
        )

        # 1. Unsupported response stores no site data and keeps WiFi/firewall intact
        mock_network_client.reports.get_site_report = AsyncMock(
            side_effect=UniFiNotFoundError("Not found", status_code=404)
        )
        await coord._async_update_data()
        assert coord.data["internet_activity"] == {}
        assert coord.internet_activity_available("site1") is True
        assert coord.wifi_available("site1") is True
        assert coord.firewall_available("site1") is True

        # 2. Successful fetch populates windows
        async def _report_ok(
            site_name: str, interval: str, *, start_ms: int, end_ms: int
        ) -> list[dict[str, int]]:
            return [{"time": end_ms, "wan-rx_bytes": 4096, "wan-tx_bytes": 2048}]

        mock_network_client.reports.get_site_report = AsyncMock(side_effect=_report_ok)
        await coord._async_update_data()
        assert coord.data["internet_activity"]["site1"]["1h"] == {
            "rx_bytes": 4096,
            "tx_bytes": 2048,
        }
        assert coord.internet_activity_available("site1") is True

        # 3. Transient failure retains prior data and marks section unavailable
        mock_network_client.reports.get_site_report = AsyncMock(
            side_effect=UniFiTimeoutError("Timeout")
        )
        await coord._async_update_data()
        coord.last_update_success = True
        assert coord.internet_activity_available("site1") is False
        assert "site1" in coord.data["internet_activity_unavailable"]
        assert coord.data["internet_activity"]["site1"]["1h"] == {
            "rx_bytes": 4096,
            "tx_bytes": 2048,
        }
        assert coord.wifi_available("site1") is True
        assert coord.firewall_available("site1") is True

        # 4. Later success replaces data and clears the unavailable marker
        async def _report_recovered(
            site_name: str, interval: str, *, start_ms: int, end_ms: int
        ) -> list[dict[str, int]]:
            return [{"time": end_ms, "wan-rx_bytes": 8192, "wan-tx_bytes": 4096}]

        mock_network_client.reports.get_site_report = AsyncMock(
            side_effect=_report_recovered
        )
        await coord._async_update_data()
        assert coord.internet_activity_available("site1") is True
        assert "site1" not in coord.data["internet_activity_unavailable"]
        assert coord.data["internet_activity"]["site1"]["1h"] == {
            "rx_bytes": 8192,
            "tx_bytes": 4096,
        }

        # 5. Cover bucket variations (SiteReportBucket instance, invalid dict,
        # non-dict object, rx-only, tx-only), client fallback, hourly/daily
        # partial failure, HTTP 200 HTML response, empty windows, and facade
        mixed_buckets: list[Any] = [
            SiteReportBucket.model_validate({"time": now_ms, "wan-rx_bytes": 100}),
            {"time": now_ms, "wan-tx_bytes": 50},
            {"invalid": "no_time"},
            "not_a_dict",
        ]
        rx_only = aggregate_internet_activity_windows(
            [SiteReportBucket.model_validate({"time": now_ms, "wan-rx_bytes": 100})],
            [SiteReportBucket.model_validate({"time": now_ms, "wan-tx_bytes": 50})],
            mixed_buckets,
            now_ms=now_ms,
        )
        assert rx_only["1h"] == {"rx_bytes": 100}
        assert rx_only["1d"] == {"tx_bytes": 50}
        assert rx_only["1m"] == {"rx_bytes": 100, "tx_bytes": 50}

        # Fallback to network_client.get_site_report and empty fallback
        mock_network_client.reports = None
        mock_network_client.get_site_report = AsyncMock(return_value=[])
        assert (
            await coord._call_site_report("default", "hourly", start_ms=1, end_ms=2)
            == []
        )
        mock_network_client.get_site_report = None
        assert (
            await coord._call_site_report("default", "hourly", start_ms=1, end_ms=2)
            == []
        )

        # Hourly / daily transient failures and HTTP 200 HTML unsupported
        mock_network_client.reports = MagicMock()

        async def _fail_hourly(
            site_name: str, interval: str, *, start_ms: int, end_ms: int
        ) -> list[dict[str, int]]:
            if interval == "hourly":
                msg = "Hourly timeout"
                raise UniFiTimeoutError(msg)
            return []

        mock_network_client.reports.get_site_report = AsyncMock(
            side_effect=_fail_hourly
        )
        assert (
            await coord._fetch_site_internet_activity(
                site_id="site1", site_name="default", now_ms=now_ms
            )
            is None
        )

        async def _fail_daily(
            site_name: str, interval: str, *, start_ms: int, end_ms: int
        ) -> list[dict[str, int]]:
            if interval == "daily":
                msg = "Daily timeout"
                raise UniFiTimeoutError(msg)
            return []

        mock_network_client.reports.get_site_report = AsyncMock(side_effect=_fail_daily)
        assert (
            await coord._fetch_site_internet_activity(
                site_id="site1", site_name="default", now_ms=now_ms
            )
            is None
        )

        mock_network_client.reports.get_site_report = AsyncMock(
            side_effect=UniFiResponseError("HTML", status_code=200)
        )
        await coord._async_update_data()
        assert "site1" not in coord.data["internet_activity"]

        facade_with_cfg = MagicMock()
        facade_with_cfg.config_available = True
        facade_with_cfg._config_coordinator = coord
        assert (
            UnifiFacadeCoordinator.internet_activity_available(facade_with_cfg, "site1")
            is True
        )
        facade_with_cfg._config_coordinator = None
        assert (
            UnifiFacadeCoordinator.internet_activity_available(facade_with_cfg, "site1")
            is True
        )

    @pytest.mark.asyncio
    async def test_config_coordinator_innerspace_only_suppresses_network_404(
        self,
        hass: HomeAssistant,
        mock_network_client: MagicMock,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test UnifiConfigCoordinator suppresses Network 404 when InnerSpace used."""
        mock_network_client.sites.get_all.side_effect = UniFiNotFoundError(
            "Not found", status_code=404
        )
        config_coord = UnifiConfigCoordinator(
            hass=hass,
            network_client=mock_network_client,
            protect_client=None,
            entry=mock_config_entry,
            network_available=True,
            innerspace_available=True,
        )
        data = await config_coord._async_update_data()
        assert data["sites"] == {}
        assert config_coord.available is True

    @pytest.mark.asyncio
    async def test_config_sections_helper_branches(
        self,
        mock_network_client: MagicMock,
    ) -> None:
        """Test edge branches in config_sections helper functions."""
        # map_legacy_site_names: skip empty name and fall back to single legacy site
        mappings = map_legacy_site_names(
            {"unmatched_site": {"name": "Office"}},
            [{"name": ""}, {"name": "default", "desc": "Main"}],
        )
        assert mappings == {"unmatched_site": "default"}

        # wifi_qr_payload: WEP and hidden SSID
        qr = wifi_qr_payload("My;Net", "secret", "wep", hidden=True)
        assert "T:WEP" in qr
        assert "H:true" in qr

        # enrich_wifi: client without essid, wifi without ssid/matching config
        wifi_dict: dict[str, dict[str, Any]] = {
            "w1": {"id": "w1"},
            "w2": {"id": "w2", "name": "UnmatchedSSID"},
        }
        enrich_wifi(
            wifi_dict,
            [{"name": "OtherSSID", "x_passphrase": "pw", "security": "wpapsk"}],
            [{"is_wired": False}],
        )
        assert wifi_dict["w2"]["num_connected_clients"] == 0

        # resolve_report_site_name: non-dict site_data and internalReference fallback
        assert resolve_report_site_name("site1", "not_a_dict", None) == "site1"
        assert (
            resolve_report_site_name("site1", {"internalReference": "custom_ref"}, None)
            == "custom_ref"
        )

        # async_fetch_site_routes: route without id is skipped
        mock_network_client.routes.list_routes = AsyncMock(
            return_value=[{"name": "no-id"}, {"id": "r1", "name": "with-id"}]
        )
        routes = await async_fetch_site_routes(
            mock_network_client, dict, "site1", "default"
        )
        assert list(routes.keys()) == ["r1"]

        # async_update_site_internet_activity: failed fetch with no prior_activity
        failed_set: set[tuple[str, str]] = set()
        activity_map: dict[str, dict[str, dict[str, int]]] = {}
        await async_update_site_internet_activity(
            AsyncMock(return_value=None),
            site_id="site1",
            site_data={},
            legacy_name="default",
            now_ms=1000,
            prior_activity=None,
            internet_activity_by_site=activity_map,
            failed_sections=failed_set,
        )
        assert ("internet_activity", "site1") in failed_set
        assert "site1" not in activity_map

        # async_fetch_site_wifi_and_links: get_legacy_configs raising logs debug
        dummy_coord = MagicMock()
        dummy_coord._fetch_optional_section = AsyncMock(
            return_value=[{"id": "w1", "name": "Net"}]
        )
        dummy_coord._model_to_dict = dict
        dummy_coord._client_links = lambda _: {}
        dummy_coord.data = {"client_links": {}, "wifi": {}}
        dummy_coord.network_client.clients.get_active_legacy = AsyncMock(
            return_value=[]
        )
        dummy_coord.network_client.wifi.get_legacy_configs = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        wifi_res, _ = await async_fetch_site_wifi_and_links(
            dummy_coord, "site1", "default", set()
        )
        assert "w1" in wifi_res


# ============================================================================
# Protect 7.3.70 security device families (fobs, link stations, alarm hubs)
# ============================================================================

_SECURITY_FAMILIES = [
    ("fobs", "_fetch_fobs", Fob, SAMPLE_KEYPAD_FOB),
    ("link_stations", "_fetch_link_stations", LinkStation, SAMPLE_THREAD_LINK_STATION),
    ("alarm_hubs", "_fetch_alarm_hubs", LinkStation, SAMPLE_ALARM_HUB),
    ("sirens", "_fetch_sirens", Siren, SAMPLE_SIREN),
]


class TestProtectSecurityDeviceFamilies:
    """Polling, WebSocket and event handling for fobs/link stations/alarm hubs."""

    @pytest.fixture
    async def coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> AsyncGenerator[UnifiProtectCoordinator]:
        """Create a protect coordinator, shut down on teardown."""
        coord = UnifiProtectCoordinator(
            hass=hass,
            network_client=_create_mock_network_client(),
            protect_client=_create_mock_protect_client(),
            entry=mock_config_entry,
        )
        yield coord
        await coord.async_shutdown()

    def test_collections_are_initialised(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """Each family has a data key named device_type + "s" from the start.

        `UnifiProtectEntity` indexes `data["protect"][f"{device_type}s"]`
        directly, so a missing key is a KeyError in every entity.
        """
        for collection in ("fobs", "link_stations", "alarm_hubs", "sirens"):
            assert coordinator.data[collection] == {}
            assert coordinator._previous_protect_device_ids[collection] == set()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("collection", "fetch_method", "model", "sample"), _SECURITY_FAMILIES
    )
    async def test_fetch_populates_collection_with_camelcase_dicts(
        self,
        coordinator: UnifiProtectCoordinator,
        collection: str,
        fetch_method: str,
        model: type,
        sample: dict[str, Any],
    ) -> None:
        """A fetch stores the alias-keyed dump of each real model by id."""
        getattr(coordinator.protect_client, collection).get_all = AsyncMock(
            return_value=[model.model_validate(sample)]
        )

        await getattr(coordinator, fetch_method)()

        stored = coordinator.data[collection][sample["id"]]
        assert stored["id"] == sample["id"]
        assert stored["state"] == "CONNECTED"
        if collection == "fobs":
            assert stored["keypadSettings"]["beepVolume"] == 60
        elif collection == "sirens":
            assert stored["sirenStatus"]["isActive"] is False
            assert stored["volume"] == 50
        else:
            assert "threadState" in stored

    @pytest.mark.asyncio
    async def test_scheduled_poll_fetches_every_family(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """The 30s poll includes the three new families."""
        client = coordinator.protect_client
        client.fobs.get_all = AsyncMock(
            return_value=[Fob.model_validate(SAMPLE_KEYPAD_FOB)]
        )
        client.link_stations.get_all = AsyncMock(
            return_value=[LinkStation.model_validate(SAMPLE_THREAD_LINK_STATION)]
        )
        client.alarm_hubs.get_all = AsyncMock(
            return_value=[LinkStation.model_validate(SAMPLE_ALARM_HUB)]
        )
        client.sirens.get_all = AsyncMock(
            return_value=[Siren.model_validate(SAMPLE_SIREN)]
        )

        data = await coordinator._async_update_data()

        assert set(data["fobs"]) == {"fob_1"}
        assert set(data["link_stations"]) == {"link_station_thread"}
        assert set(data["alarm_hubs"]) == {"alarm_hub_1"}
        assert set(data["sirens"]) == {"siren_1"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("collection", "fetch_method", "model", "sample"), _SECURITY_FAMILIES
    )
    @pytest.mark.parametrize(
        "error",
        [
            UniFiNotFoundError("Not Found", 404),
            UniFiResponseError("API returned non-JSON response (status 200)", 200),
        ],
        ids=["404", "non-json-200"],
    )
    async def test_unsupported_answer_marks_family_unsupported_and_logs_once(
        self,
        coordinator: UnifiProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
        *,
        collection: str,
        fetch_method: str,
        model: type,
        sample: dict[str, Any],
        error: Exception,
    ) -> None:
        """An older Protect without the endpoint is polled hourly, logged once.

        It may answer 404 or serve its HTML page with a 200. Without this
        every 30s poll spends a request (and a warning) on an endpoint the
        console does not have.
        """
        get_all = AsyncMock(side_effect=error)
        getattr(coordinator.protect_client, collection).get_all = get_all
        clock = 1000.0

        with patch(
            "custom_components.unifi_insights.coordinators.protect.time.monotonic",
            side_effect=lambda: clock,
        ):
            with caplog.at_level(logging.INFO):
                await getattr(coordinator, fetch_method)()
                await getattr(coordinator, fetch_method)()
            assert get_all.await_count == 1
            assert coordinator.data[collection] == {}

            clock += UNSUPPORTED_RESOURCE_RETRY.total_seconds() + 1
            with caplog.at_level(logging.INFO):
                await getattr(coordinator, fetch_method)()
            assert get_all.await_count == 2

        unsupported = [r for r in caplog.records if "does not expose" in r.getMessage()]
        assert len(unsupported) == 1
        assert unsupported[0].levelno == logging.INFO
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        # Never answered, so the HTML page is expected and logged at DEBUG.
        get_all.assert_awaited_with(expected_unsupported=True)

    @pytest.mark.asyncio
    async def test_non_json_after_success_warns_and_keeps_polling(
        self, coordinator: UnifiProtectCoordinator, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A family that answered before is not written off on an HTML page.

        It stopped being an "unsupported endpoint" signal once the family
        answered, so the call opts out and the error warns like any other.
        """
        endpoint = coordinator.protect_client.link_stations
        endpoint.get_all = AsyncMock(
            return_value=[LinkStation.model_validate(SAMPLE_THREAD_LINK_STATION)]
        )
        await coordinator._fetch_link_stations()
        endpoint.get_all.assert_awaited_once_with(expected_unsupported=True)

        endpoint.get_all = AsyncMock(
            side_effect=UniFiResponseError(
                "API returned non-JSON response (status 200)", 200
            )
        )
        await coordinator._fetch_link_stations()
        await coordinator._fetch_link_stations()

        assert "link_station_thread" in coordinator.data["link_stations"]
        assert endpoint.get_all.await_count == 2
        endpoint.get_all.assert_awaited_with(expected_unsupported=False)
        assert "Error fetching link_stations" in caplog.text
        assert "does not expose" not in caplog.text

    @pytest.mark.asyncio
    async def test_http_400_before_first_answer_is_not_unsupported(
        self, coordinator: UnifiProtectCoordinator, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A 400 is a request problem, not a missing endpoint: warn, keep polling."""
        endpoint = coordinator.protect_client.fobs
        endpoint.get_all = AsyncMock(
            side_effect=UniFiResponseError("API error (status 400)", 400)
        )

        await coordinator._fetch_fobs()
        await coordinator._fetch_fobs()

        assert endpoint.get_all.await_count == 2
        assert "Error fetching fobs" in caplog.text
        assert "does not expose" not in caplog.text

    @pytest.mark.asyncio
    async def test_unsupported_family_recovers_once_endpoint_answers(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """A Protect upgrade is picked up at the next hourly re-probe."""
        endpoint = coordinator.protect_client.fobs
        endpoint.get_all = AsyncMock(
            side_effect=[
                UniFiNotFoundError("Not Found", 404),
                [Fob.model_validate(SAMPLE_KEYPAD_FOB)],
            ]
        )
        clock = 1000.0
        with patch(
            "custom_components.unifi_insights.coordinators.protect.time.monotonic",
            side_effect=lambda: clock,
        ):
            await coordinator._fetch_fobs()
            clock += UNSUPPORTED_RESOURCE_RETRY.total_seconds() + 1
            await coordinator._fetch_fobs()
            await coordinator._fetch_fobs()

        assert "fob_1" in coordinator.data["fobs"]
        # Not skipped after recovering: the third call went to the API.
        assert endpoint.get_all.await_count == 3

    @pytest.mark.asyncio
    async def test_404_after_success_keeps_cache_and_keeps_polling(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """A family that answered before is not written off on one 404.

        It goes through the bounded 404 cache path instead, like cameras.
        """
        endpoint = coordinator.protect_client.alarm_hubs
        endpoint.get_all = AsyncMock(
            return_value=[LinkStation.model_validate(SAMPLE_ALARM_HUB)]
        )
        await coordinator._fetch_alarm_hubs()

        endpoint.get_all = AsyncMock(side_effect=UniFiNotFoundError("Not Found", 404))
        await coordinator._fetch_alarm_hubs()
        await coordinator._fetch_alarm_hubs()

        assert "alarm_hub_1" in coordinator.data["alarm_hubs"]
        assert endpoint.get_all.await_count == 2

    @pytest.mark.asyncio
    async def test_fetch_error_keeps_cached_devices(
        self, coordinator: UnifiProtectCoordinator, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Any other error preserves the cache and is logged."""
        coordinator.data["link_stations"] = {"ls1": {"id": "ls1"}}
        coordinator.protect_client.link_stations.get_all = AsyncMock(
            side_effect=Exception("500 Internal Server Error")
        )

        await coordinator._fetch_link_stations()

        assert coordinator.data["link_stations"] == {"ls1": {"id": "ls1"}}
        assert "Error fetching link_stations" in caplog.text

    @pytest.mark.asyncio
    async def test_incomplete_response_is_merged_over_cache(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """A device the endpoint dropped on a parse error is not treated as gone."""
        coordinator.data["fobs"] = {"fob_old": {"id": "fob_old"}}
        endpoint = coordinator.protect_client.fobs
        endpoint.get_all = AsyncMock(
            return_value=[Fob.model_validate(SAMPLE_KEYPAD_FOB)]
        )
        endpoint.last_result_complete = False

        await coordinator._fetch_fobs()

        assert set(coordinator.data["fobs"]) == {"fob_old", "fob_1"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("collection", "identifier"),
        [
            ("fobs", "protect_fob_dev1"),
            ("link_stations", "protect_link_station_dev1"),
            ("alarm_hubs", "protect_alarm_hub_dev1"),
            ("sirens", "protect_siren_dev1"),
        ],
    )
    async def test_stale_cleanup_removes_vanished_device(
        self,
        hass: HomeAssistant,
        coordinator: UnifiProtectCoordinator,
        collection: str,
        identifier: str,
    ) -> None:
        """A device gone past the grace window leaves the registry.

        The identifier must match the entity's `protect_{device_type}_{id}`.
        """
        coordinator._previous_protect_device_ids[collection] = {"dev1"}
        coordinator.data[collection] = {}
        mock_device = MagicMock()
        mock_device.id = "registry_dev1"

        with (
            patch(
                "custom_components.unifi_insights.coordinators.protect.dr.async_get"
            ) as mock_registry,
            patch(
                "custom_components.unifi_insights.coordinators.protect.async_get_device_entry",
                side_effect=lambda _reg, ident, _entry: (
                    mock_device if ident == (DOMAIN, identifier) else None
                ),
            ),
        ):
            for _ in range(MAX_CONSECUTIVE_MISSING_POLLS + 1):
                coordinator._cleanup_stale_devices()

            mock_registry.return_value.async_update_device.assert_called_once_with(
                device_id="registry_dev1",
                remove_config_entry_id=coordinator.config_entry.entry_id,
            )

    # -- WebSocket device frames ------------------------------------------

    def _seed_hub(self, coordinator: UnifiProtectCoordinator) -> dict[str, Any]:
        hub = copy.deepcopy(SAMPLE_ALARM_HUB)
        hub["alarmHub"]["armed"] = "on"
        coordinator.data["alarm_hubs"] = {"alarm_hub_1": hub}
        return hub

    def test_linkstation_frame_for_known_alarm_hub_updates_alarm_hubs(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """Alarm hubs and link stations share modelKey "linkstation".

        The frame is routed by which collection already holds the id.
        """
        self._seed_hub(coordinator)
        coordinator.data["link_stations"] = {
            "link_station_thread": copy.deepcopy(SAMPLE_THREAD_LINK_STATION)
        }

        coordinator._handle_device_update(
            "linkstation", {"id": "alarm_hub_1", "state": "DISCONNECTED"}
        )
        coordinator._handle_device_update(
            "linkstation", {"id": "link_station_thread", "name": "Renamed"}
        )

        assert coordinator.data["alarm_hubs"]["alarm_hub_1"]["state"] == "DISCONNECTED"
        assert "alarm_hub_1" not in coordinator.data["link_stations"]
        assert coordinator.data["link_stations"]["link_station_thread"]["name"] == (
            "Renamed"
        )
        assert "link_station_thread" not in coordinator.data["alarm_hubs"]

    @pytest.mark.parametrize(
        ("is_alarm_hub", "collection"),
        [(True, "alarm_hubs"), (False, "link_stations")],
    )
    def test_linkstation_frame_for_new_device_uses_is_alarm_hub(
        self,
        coordinator: UnifiProtectCoordinator,
        *,
        is_alarm_hub: bool,
        collection: str,
    ) -> None:
        """An id no poll has seen yet is routed by the frame's isAlarmHub."""
        coordinator._handle_device_update(
            "linkstation", {"id": "new1", "isAlarmHub": is_alarm_hub}
        )

        assert "new1" in coordinator.data[collection]

    def test_linkstation_frame_for_unknown_device_is_dropped(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """Without an id match or isAlarmHub there is no safe target."""
        coordinator._handle_device_update(
            "linkstation", {"id": "mystery", "state": "CONNECTED"}
        )

        assert "mystery" not in coordinator.data["alarm_hubs"]
        assert "mystery" not in coordinator.data["link_stations"]

    def test_partial_nested_frame_keeps_sibling_fields(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """A partial alarmHub frame must not wipe armed/battery until next poll."""
        self._seed_hub(coordinator)

        coordinator._handle_device_update(
            "linkstation",
            {"id": "alarm_hub_1", "alarmHub": {"deviceTamperStatus": "tampered"}},
        )

        alarm_hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]["alarmHub"]
        assert alarm_hub["deviceTamperStatus"] == "tampered"
        assert alarm_hub["armed"] == "on"
        assert alarm_hub["battery"] == {
            "charging": "off",
            "batteryStatus": "ok",
            "voltage": 13.1,
        }

    def test_partial_thread_network_frame_keeps_sibling_fields(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """Merging recurses through threadState.network."""
        coordinator.data["link_stations"] = {
            "link_station_thread": copy.deepcopy(SAMPLE_THREAD_LINK_STATION)
        }

        coordinator._handle_device_update(
            "linkstation",
            {
                "id": "link_station_thread",
                "threadState": {"network": {"joinedDeviceCount": 5}},
            },
        )

        network = coordinator.data["link_stations"]["link_station_thread"][
            "threadState"
        ]["network"]
        assert network["joinedDeviceCount"] == 5
        assert network["role"] == "leader"
        assert network["channel"] == 15

    def test_explicit_null_in_frame_replaces_nested_value(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """Only dict-over-dict merges; a null network is a real state change."""
        coordinator.data["link_stations"] = {
            "link_station_thread": copy.deepcopy(SAMPLE_THREAD_LINK_STATION)
        }

        coordinator._handle_device_update(
            "linkstation",
            {"id": "link_station_thread", "threadState": {"network": None}},
        )

        state = coordinator.data["link_stations"]["link_station_thread"]["threadState"]
        assert state["network"] is None

    def test_merge_never_mutates_the_previous_dicts(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """Listeners holding the old dict must see it unchanged."""
        hub = self._seed_hub(coordinator)
        old_alarm_hub = hub["alarmHub"]
        snapshot = copy.deepcopy(hub)

        coordinator._handle_device_update(
            "linkstation",
            {"id": "alarm_hub_1", "alarmHub": {"deviceTamperStatus": "tampered"}},
        )

        assert hub == snapshot
        assert old_alarm_hub["deviceTamperStatus"] == "restored"
        new_hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert new_hub is not hub
        assert new_hub["alarmHub"] is not old_alarm_hub

    def test_fob_frame_deep_merges_keypad_settings(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """Fob frames ("fob" modelKey) deep-merge into fobs."""
        coordinator.data["fobs"] = {"fob_1": copy.deepcopy(SAMPLE_KEYPAD_FOB)}

        coordinator._handle_device_update(
            "fob", {"id": "fob_1", "keypadSettings": {"beepVolume": 20}}
        )

        keypad = coordinator.data["fobs"]["fob_1"]["keypadSettings"]
        assert keypad == {"beepEnabled": True, "beepVolume": 20}

    def test_siren_frame_deep_merges_siren_status(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """A partial siren frame keeps the nested siblings it does not carry.

        Goes through the confirmed {"type", "item"} envelope. The siren starts
        out sounding, and the frame carries only the changed leaves.
        """
        sounding = copy.deepcopy(SAMPLE_SIREN)
        sounding["sirenStatus"] = {
            "isActive": True,
            "activatedAt": 1759700000000,
            "duration": 10000,
        }
        coordinator.data["sirens"] = {"siren_1": sounding}
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        coordinator._on_websocket_message(
            {
                "type": "update",
                "item": {
                    "id": "siren_1",
                    "modelKey": "siren",
                    "sirenStatus": {"isActive": False},
                    "wirelessConnectionState": {"batteryStatus": {"percentage": 80}},
                },
            }
        )

        siren = coordinator.data["sirens"]["siren_1"]
        assert siren["sirenStatus"] == {
            "isActive": False,
            "activatedAt": 1759700000000,
            "duration": 10000,
        }
        wireless = siren["wirelessConnectionState"]
        assert wireless["batteryStatus"] == {"percentage": 80, "isLow": False}
        assert wireless["signalState"] == {
            "signalQuality": None,
            "signalStrength": None,
        }
        assert siren["volume"] == 50
        listener.assert_called()

    def test_siren_frame_for_unpolled_siren_creates_entry(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """A frame for a siren the poll has not seen yet creates its entry."""
        coordinator._handle_device_update(
            "siren", {"id": "siren_new", "sirenStatus": {"isActive": True}}
        )

        assert coordinator.data["sirens"]["siren_new"] == {
            "id": "siren_new",
            "sirenStatus": {"isActive": True},
        }

    def test_mark_siren_stopped_clears_is_active_and_notifies(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """A manual stop shows off in the cache and notifies listeners once."""
        active = copy.deepcopy(SAMPLE_SIREN)
        active["sirenStatus"] = {
            "isActive": True,
            "activatedAt": 1759700000000,
            "duration": 10000,
        }
        coordinator.data["sirens"] = {"siren_1": active}
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        coordinator.mark_siren_stopped("siren_1")

        status = coordinator.data["sirens"]["siren_1"]["sirenStatus"]
        assert status == {
            "isActive": False,
            "activatedAt": 1759700000000,
            "duration": 10000,
        }
        listener.assert_called_once()

    def test_mark_siren_stopped_with_null_status_sets_status(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """A siren whose status was null still ends up with an explicit off."""
        siren = copy.deepcopy(SAMPLE_SIREN)
        siren["sirenStatus"] = None
        coordinator.data["sirens"] = {"siren_1": siren}

        coordinator.mark_siren_stopped("siren_1")

        status = coordinator.data["sirens"]["siren_1"]["sirenStatus"]
        assert status == {"isActive": False}

    @pytest.mark.parametrize("record", [None, "not-a-dict"], ids=["absent", "garbage"])
    def test_mark_siren_stopped_ignores_unknown_siren(
        self, coordinator: UnifiProtectCoordinator, record: Any
    ) -> None:
        """A stop for a siren the cache has no usable record of changes nothing."""
        if record is not None:
            coordinator.data["sirens"] = {"siren_1": record}
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        coordinator.mark_siren_stopped("siren_1")

        assert coordinator.data["sirens"] == (
            {} if record is None else {"siren_1": record}
        )
        listener.assert_not_called()

    def test_mark_siren_stopped_leaves_poll_state_alone(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """Nothing was polled, so the poll is neither marked good nor pushed back."""
        active = copy.deepcopy(SAMPLE_SIREN)
        active["sirenStatus"] = {"isActive": True}
        coordinator.data["sirens"] = {"siren_1": active}
        coordinator.last_update_success = False
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        with patch.object(coordinator, "_schedule_refresh") as reschedule:
            coordinator.mark_siren_stopped("siren_1")

        assert coordinator.last_update_success is False
        reschedule.assert_not_called()
        listener.assert_called_once()

    @staticmethod
    def _siren_with_status(**status: Any) -> dict[str, Any]:
        siren = copy.deepcopy(SAMPLE_SIREN)
        siren["sirenStatus"] = {"isActive": True, "duration": 10000, **status}
        return siren

    async def _poll_sirens(
        self, coordinator: UnifiProtectCoordinator, siren: dict[str, Any]
    ) -> dict[str, Any]:
        """Run one siren poll that answers with ``siren`` and return its record."""
        coordinator.protect_client.sirens.get_all = AsyncMock(
            return_value=[Siren.model_validate(siren)]
        )
        await coordinator._fetch_sirens()
        return coordinator.data["sirens"]["siren_1"]["sirenStatus"]

    @pytest.mark.asyncio
    async def test_poll_in_flight_during_a_stop_cannot_switch_the_siren_back_on(
        self, coordinator: UnifiProtectCoordinator, freezer: Any
    ) -> None:
        """A reading of the run that was just stopped does not resurrect it."""
        freezer.move_to("2026-10-06 12:00:00+00:00")
        now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        running = self._siren_with_status(activatedAt=now_ms - 3000)
        coordinator.data["sirens"] = {"siren_1": copy.deepcopy(running)}

        coordinator.mark_siren_stopped("siren_1")
        status = await self._poll_sirens(coordinator, running)

        assert status["isActive"] is False
        # The reading is applied but for the flag, so nothing else is lost.
        assert status["activatedAt"] == now_ms - 3000

        # The console keeps saying so until it reports the siren idle.
        status = await self._poll_sirens(coordinator, running)
        assert status["isActive"] is False
        assert "siren_1" in coordinator._siren_stopped_at

        idle = self._siren_with_status(isActive=False, activatedAt=now_ms - 3000)
        status = await self._poll_sirens(coordinator, idle)
        assert status["isActive"] is False
        assert coordinator._siren_stopped_at == {}

    @pytest.mark.asyncio
    async def test_run_started_after_the_stop_is_believed(
        self, coordinator: UnifiProtectCoordinator, freezer: Any
    ) -> None:
        """A poll showing a run that began after the stop is a new run."""
        freezer.move_to("2026-10-06 12:00:00+00:00")
        now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        coordinator.data["sirens"] = {
            "siren_1": self._siren_with_status(activatedAt=now_ms - 3000)
        }
        coordinator.mark_siren_stopped("siren_1")

        freezer.tick(2)
        status = await self._poll_sirens(
            coordinator, self._siren_with_status(activatedAt=now_ms + 1500)
        )

        assert status["isActive"] is True
        assert coordinator._siren_stopped_at == {}

    @pytest.mark.asyncio
    async def test_active_siren_without_a_start_time_is_believed(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """With no start time to compare, an active reading is taken as it is."""
        coordinator.data["sirens"] = {"siren_1": self._siren_with_status()}
        coordinator.mark_siren_stopped("siren_1")

        status = await self._poll_sirens(
            coordinator, self._siren_with_status(activatedAt=None)
        )

        assert status["isActive"] is True
        assert coordinator._siren_stopped_at == {}

    @pytest.mark.asyncio
    async def test_stop_guard_is_dropped_for_a_siren_that_is_gone(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """A siren the console no longer lists leaves no guard behind."""
        coordinator.data["sirens"] = {"siren_1": self._siren_with_status()}
        coordinator.mark_siren_stopped("siren_1")

        coordinator.protect_client.sirens.get_all = AsyncMock(return_value=[])
        coordinator.data["sirens"] = {}
        await coordinator._fetch_sirens()

        assert coordinator._siren_stopped_at == {}

    @pytest.mark.asyncio
    async def test_console_clock_ahead_cannot_resurrect_the_stopped_run(
        self, coordinator: UnifiProtectCoordinator, freezer: Any
    ) -> None:
        """The run is recognised by its own start time, not Home Assistant's clock.

        A console 5 s ahead stamps the run with a time later than the moment
        Home Assistant stopped it, so comparing the two clocks would believe
        the stale reading.
        """
        freezer.move_to("2026-10-06 12:00:00+00:00")
        now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        running = self._siren_with_status(activatedAt=now_ms + 5000)
        coordinator.data["sirens"] = {"siren_1": copy.deepcopy(running)}
        coordinator.mark_siren_stopped("siren_1")

        status = await self._poll_sirens(coordinator, running)

        assert status["isActive"] is False
        assert "siren_1" in coordinator._siren_stopped_at

    @pytest.mark.asyncio
    async def test_home_assistant_clock_ahead_does_not_hide_a_new_run(
        self, coordinator: UnifiProtectCoordinator, freezer: Any
    ) -> None:
        """A new run is later than the stopped one on the console's own clock.

        With Home Assistant 5 s ahead of the console, a run started 2 s after
        the stop is stamped earlier than the moment of the stop, so comparing
        the two clocks would hide it for its whole run.
        """
        freezer.move_to("2026-10-06 12:00:00+00:00")
        now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        coordinator.data["sirens"] = {
            "siren_1": self._siren_with_status(activatedAt=now_ms - 8000)
        }
        coordinator.mark_siren_stopped("siren_1")

        freezer.tick(2)
        status = await self._poll_sirens(
            coordinator, self._siren_with_status(activatedAt=now_ms - 3000)
        )

        assert status["isActive"] is True
        assert coordinator._siren_stopped_at == {}

    @pytest.mark.asyncio
    async def test_siren_without_a_cached_start_falls_back_to_the_wall_clock(
        self, coordinator: UnifiProtectCoordinator, freezer: Any
    ) -> None:
        """With no start time cached at the stop, the clocks are all there is."""
        freezer.move_to("2026-10-06 12:00:00+00:00")
        now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        siren = copy.deepcopy(SAMPLE_SIREN)
        siren["sirenStatus"] = None
        coordinator.data["sirens"] = {"siren_1": siren}
        coordinator.mark_siren_stopped("siren_1")

        stale = await self._poll_sirens(
            coordinator, self._siren_with_status(activatedAt=now_ms - 3000)
        )
        assert stale["isActive"] is False

        fresh = await self._poll_sirens(
            coordinator, self._siren_with_status(activatedAt=now_ms + 1500)
        )
        assert fresh["isActive"] is True
        assert coordinator._siren_stopped_at == {}

    @pytest.mark.asyncio
    async def test_stop_guard_expires_after_two_poll_intervals(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """The guard cannot linger: after its TTL a reading is taken as it is."""
        clock = [5000.0]
        running = self._siren_with_status(activatedAt=1_000_000)
        coordinator.data["sirens"] = {"siren_1": copy.deepcopy(running)}

        with patch(
            "custom_components.unifi_insights.coordinators.protect.time.monotonic",
            side_effect=lambda: clock[0],
        ):
            coordinator.mark_siren_stopped("siren_1")

            clock[0] += SIREN_STOP_GUARD_TTL - 1
            held = await self._poll_sirens(coordinator, running)
            assert held["isActive"] is False

            clock[0] += 2
            expired = await self._poll_sirens(coordinator, running)

        assert expired["isActive"] is True
        assert coordinator._siren_stopped_at == {}

    def test_late_frame_of_the_stopped_run_does_not_switch_the_siren_back_on(
        self, coordinator: UnifiProtectCoordinator, freezer: Any
    ) -> None:
        """The guard covers WebSocket frames as well as polls."""
        freezer.move_to("2026-10-06 12:00:00+00:00")
        now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        coordinator.data["sirens"] = {
            "siren_1": self._siren_with_status(activatedAt=now_ms - 3000)
        }
        coordinator.mark_siren_stopped("siren_1")

        coordinator._handle_device_update(
            "siren",
            {
                "id": "siren_1",
                "sirenStatus": {"isActive": True, "activatedAt": now_ms - 3000},
            },
        )

        assert coordinator.data["sirens"]["siren_1"]["sirenStatus"]["isActive"] is False

    async def _read_in_flight_while_a_frame_lands(
        self,
        coordinator: UnifiProtectCoordinator,
        *,
        collection: str,
        fetch: Any,
        frame_model_key: str,
        frame: dict[str, Any],
        older_reading: Any,
        frame_before_request: bool = False,
    ) -> None:
        """Start ``fetch``, land ``frame`` while its request is out, then answer.

        The answer is ``older_reading``: what the console reported before the
        frame, handed back late.
        """
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_get_all(*_args: Any, **_kwargs: Any) -> list[Any]:
            started.set()
            await release.wait()
            return [older_reading]

        getattr(coordinator.protect_client, collection).get_all = slow_get_all
        if frame_before_request:
            coordinator._handle_device_update(frame_model_key, copy.deepcopy(frame))
        task = asyncio.create_task(fetch())
        await started.wait()
        if not frame_before_request:
            coordinator._handle_device_update(frame_model_key, copy.deepcopy(frame))
        release.set()
        await task

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["poll", "reconcile"])
    async def test_older_read_does_not_overwrite_a_siren_frame_that_landed_meanwhile(
        self, coordinator: UnifiProtectCoordinator, path: str
    ) -> None:
        """A WebSocket frame that lands while a read is out is newer than the read.

        Applying the read would replace the siren family and switch the run
        the frame reported back off, until the next frame or poll.
        """
        coordinator.data["sirens"] = {
            "siren_1": self._siren_with_status(isActive=False)
        }
        fetch = (
            coordinator._fetch_sirens
            if path == "poll"
            else coordinator._reconcile_sirens
        )

        await self._read_in_flight_while_a_frame_lands(
            coordinator,
            collection="sirens",
            fetch=fetch,
            frame_model_key="siren",
            frame={
                "id": "siren_1",
                "sirenStatus": {"isActive": True, "activatedAt": 1_000_000},
            },
            older_reading=Siren.model_validate(self._siren_with_status(isActive=False)),
        )

        status = coordinator.data["sirens"]["siren_1"]["sirenStatus"]
        assert status["isActive"] is True
        assert status["activatedAt"] == 1_000_000

    @pytest.mark.asyncio
    async def test_read_started_after_a_siren_frame_is_applied(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """Only a frame that lands after the request started makes it stale."""
        coordinator.data["sirens"] = {"siren_1": self._siren_with_status(isActive=True)}

        await self._read_in_flight_while_a_frame_lands(
            coordinator,
            collection="sirens",
            fetch=coordinator._fetch_sirens,
            frame_model_key="siren",
            frame={"id": "siren_1", "sirenStatus": {"isActive": True}},
            older_reading=Siren.model_validate(self._siren_with_status(isActive=False)),
            frame_before_request=True,
        )

        assert coordinator.data["sirens"]["siren_1"]["sirenStatus"]["isActive"] is False

    @pytest.mark.asyncio
    async def test_families_without_a_counter_are_fetched_as_before(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """The check applies to the families that have a generation, no others."""
        coordinator.data["fobs"] = {"fob_1": {"id": "fob_1"}}

        await self._read_in_flight_while_a_frame_lands(
            coordinator,
            collection="fobs",
            fetch=coordinator._fetch_fobs,
            frame_model_key="fob",
            frame={"id": "fob_1", "keypadSettings": {"beepVolume": 20}},
            older_reading=Fob.model_validate(SAMPLE_KEYPAD_FOB),
        )

        assert coordinator.data["fobs"]["fob_1"]["keypadSettings"]["beepVolume"] == 60

    @pytest.mark.asyncio
    async def test_reconcile_refreshes_sirens_and_notifies_only_on_change(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """The independent timer re-reads sirens, and stays quiet when nothing moved."""
        coordinator.data["sirens"] = {"siren_1": self._siren_with_status()}
        listener = MagicMock()
        coordinator.async_add_listener(listener)
        idle = self._siren_with_status(isActive=False)
        coordinator.protect_client.sirens.get_all = AsyncMock(
            return_value=[Siren.model_validate(idle)]
        )

        await coordinator._reconcile_sirens()

        assert coordinator.data["sirens"]["siren_1"]["sirenStatus"]["isActive"] is False
        listener.assert_called_once()

        await coordinator._reconcile_sirens()

        listener.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconcile_timer_refresh_includes_sirens(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """The sensor reconcile tick also reconciles the sirens."""
        with (
            patch.object(coordinator, "async_refresh_sensors", AsyncMock()) as sensors,
            patch.object(coordinator, "_reconcile_sirens", AsyncMock()) as sirens,
        ):
            await coordinator._reconcile_sensor_refresh()

        sensors.assert_awaited_once()
        sirens.assert_awaited_once()

    def test_real_envelope_linkstation_frame_reaches_alarm_hubs(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """End to end through the confirmed {"type", "item"} envelope."""
        self._seed_hub(coordinator)
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        coordinator._on_websocket_message(
            {
                "type": "update",
                "item": {
                    "id": "alarm_hub_1",
                    "modelKey": "linkstation",
                    "alarmHub": {"armed": "off"},
                },
            }
        )

        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["alarmHub"]["armed"] == "off"
        assert hub["alarmHub"]["deviceTamperStatus"] == "restored"
        assert hub["type"] == "UP-AlarmHub"
        listener.assert_called()

    # -- alarmHubDeviceTamper events --------------------------------------

    def test_tamper_event_sets_status_user_and_time(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """status is a {text} wrapper; userName is a plain string."""
        hub = self._seed_hub(coordinator)
        snapshot = copy.deepcopy(hub)

        coordinator._handle_event_update(
            "alarmHubDeviceTamper", copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        )

        updated = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert updated["alarmHub"]["deviceTamperStatus"] == "tampered"
        assert updated["alarmHub"]["armed"] == "on"
        assert updated["_lastTamperUser"] == "Installer"
        assert updated["_lastTamperAt"] == 1759628100000
        assert hub == snapshot

    @pytest.mark.parametrize(
        ("user_name", "expected"),
        [({"text": "Installer"}, "Installer"), (None, None), (42, None)],
    )
    def test_tamper_event_user_name_shapes(
        self,
        coordinator: UnifiProtectCoordinator,
        user_name: Any,
        expected: str | None,
    ) -> None:
        """A {text} wrapper is tolerated; anything else is no user."""
        self._seed_hub(coordinator)
        event = copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        event["metadata"]["userName"] = user_name

        coordinator._handle_event_update("alarmHubDeviceTamper", event)

        assert coordinator.data["alarm_hubs"]["alarm_hub_1"]["_lastTamperUser"] == (
            expected
        )

    def test_tamper_event_without_status_keeps_reported_status(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """An unreadable status never overwrites the hub's reported one."""
        self._seed_hub(coordinator)
        event = copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        event["metadata"]["status"] = {"value": "tampered"}  # no "text"

        coordinator._handle_event_update("alarmHubDeviceTamper", event)

        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["alarmHub"]["deviceTamperStatus"] == "restored"
        assert hub["_lastTamperAt"] == 1759628100000

    def test_tamper_event_without_metadata_records_time_only(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """A frame missing metadata changes no status and names no user."""
        self._seed_hub(coordinator)
        event = copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        event["metadata"] = None

        coordinator._handle_event_update("alarmHubDeviceTamper", event)

        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["alarmHub"]["deviceTamperStatus"] == "restored"
        assert hub["_lastTamperUser"] is None
        assert hub["_lastTamperAt"] == 1759628100000

    def test_tamper_event_for_unknown_device_is_ignored(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """An event for a hub no poll has seen changes nothing."""
        event = copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        event["device"] = "not_a_hub"

        coordinator._handle_event_update("alarmHubDeviceTamper", event)

        assert coordinator.data["alarm_hubs"] == {}
        assert coordinator._alarm_hub_last_tamper == {}

    @pytest.mark.asyncio
    async def test_tamper_details_survive_the_next_poll(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """The REST poll rebuilds each hub dict; the event details persist.

        Status stays authoritative from REST.
        """
        self._seed_hub(coordinator)
        coordinator._handle_event_update(
            "alarmHubDeviceTamper", copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        )
        coordinator.protect_client.alarm_hubs.get_all = AsyncMock(
            return_value=[LinkStation.model_validate(SAMPLE_ALARM_HUB)]
        )

        await coordinator._fetch_alarm_hubs()

        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["alarmHub"]["deviceTamperStatus"] == "restored"
        assert hub["_lastTamperUser"] == "Installer"
        assert hub["_lastTamperAt"] == 1759628100000

    @pytest.mark.asyncio
    async def test_event_status_survives_poll_without_rest_status(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """Protect 7.2.x has the tamper event but no REST deviceTamperStatus.

        The event's status must outlive the next poll, or the sensor blinks
        on for one poll and then sits at unknown.
        """
        rest_hub = copy.deepcopy(SAMPLE_ALARM_HUB)
        del rest_hub["alarmHub"]["deviceTamperStatus"]
        coordinator.data["alarm_hubs"] = {"alarm_hub_1": copy.deepcopy(rest_hub)}
        coordinator.protect_client.alarm_hubs.get_all = AsyncMock(
            return_value=[LinkStation.model_validate(rest_hub)]
        )

        coordinator._handle_event_update(
            "alarmHubDeviceTamper", copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        )
        await coordinator._fetch_alarm_hubs()
        await coordinator._fetch_alarm_hubs()

        alarm_hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]["alarmHub"]
        assert alarm_hub["deviceTamperStatus"] == "tampered"
        assert alarm_hub["armed"] == "off"

    @pytest.mark.asyncio
    async def test_event_during_in_flight_poll_beats_the_stale_snapshot(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """A REST snapshot taken before the tamper must not read as clear.

        The next poll, requested after the event, is authoritative again.
        """
        self._seed_hub(coordinator)

        async def get_all_with_tamper_mid_flight(
            *, expected_unsupported: bool
        ) -> list[LinkStation]:
            coordinator._handle_event_update(
                "alarmHubDeviceTamper", copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
            )
            return [LinkStation.model_validate(SAMPLE_ALARM_HUB)]  # "restored"

        endpoint = coordinator.protect_client.alarm_hubs
        endpoint.get_all = get_all_with_tamper_mid_flight
        await coordinator._fetch_alarm_hubs()
        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["alarmHub"]["deviceTamperStatus"] == "tampered"

        endpoint.get_all = AsyncMock(
            return_value=[LinkStation.model_validate(SAMPLE_ALARM_HUB)]
        )
        await coordinator._fetch_alarm_hubs()
        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["alarmHub"]["deviceTamperStatus"] == "restored"

    def test_later_frame_of_same_event_keeps_user_and_time(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """An update frame of the same event (e.g. its end) fills gaps only."""
        self._seed_hub(coordinator)
        coordinator._handle_event_update(
            "alarmHubDeviceTamper", copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        )

        coordinator._handle_event_update(
            "alarmHubDeviceTamper",
            {"id": "event_tamper_1", "device": "alarm_hub_1", "end": 1759628200000},
        )

        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["_lastTamperUser"] == "Installer"
        assert hub["_lastTamperAt"] == 1759628100000
        assert hub["alarmHub"]["deviceTamperStatus"] == "tampered"

    def test_new_event_without_user_does_not_inherit_previous_user(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """A different event naming nobody must not show the last event's user."""
        self._seed_hub(coordinator)
        coordinator._handle_event_update(
            "alarmHubDeviceTamper", copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        )
        restored = copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        restored["id"] = "event_tamper_2"
        restored["start"] = 1759628300000
        restored["metadata"]["status"] = {"text": "restored"}
        del restored["metadata"]["userName"]

        coordinator._handle_event_update("alarmHubDeviceTamper", restored)

        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["_lastTamperUser"] is None
        assert hub["_lastTamperAt"] == 1759628300000
        assert hub["alarmHub"]["deviceTamperStatus"] == "restored"

    def test_tamper_event_through_events_stream_envelope(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """End to end: the event's top-level "device" names the hub."""
        self._seed_hub(coordinator)

        coordinator._on_websocket_event_message(
            {"type": "add", "item": copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)}
        )

        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["alarmHub"]["deviceTamperStatus"] == "tampered"

    def test_stale_cleanup_forgets_tamper_details(
        self, hass: HomeAssistant, coordinator: UnifiProtectCoordinator
    ) -> None:
        """An evicted hub's event details must not leak onto a reused id."""
        coordinator._alarm_hub_last_tamper["gone"] = {"_lastTamperUser": "x"}
        coordinator._previous_protect_device_ids["alarm_hubs"] = {"gone"}
        coordinator.data["alarm_hubs"] = {}

        with patch(
            "custom_components.unifi_insights.coordinators.protect.dr.async_get"
        ) as mock_registry:
            set_mock_device_lookup(mock_registry.return_value, None)
            for _ in range(MAX_CONSECUTIVE_MISSING_POLLS + 1):
                coordinator._cleanup_stale_devices()

        assert "gone" not in coordinator._alarm_hub_last_tamper

    # -- remaining branches ------------------------------------------------

    def test_unrelated_model_key_leaves_security_collections_alone(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """A frame for a family this coordinator doesn't track changes nothing."""
        self._seed_hub(coordinator)
        before = copy.deepcopy(coordinator.data["alarm_hubs"])
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        coordinator._handle_device_update("relay", {"id": "alarm_hub_1"})

        assert coordinator.data["alarm_hubs"] == before
        assert coordinator.data["fobs"] == {}
        assert coordinator.data["link_stations"] == {}
        listener.assert_called()

    def test_later_frame_of_same_event_with_status_replaces_it(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """A same-event frame that carries its own values wins over the first."""
        self._seed_hub(coordinator)
        coordinator._handle_event_update(
            "alarmHubDeviceTamper", copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        )
        first_received = coordinator._alarm_hub_last_tamper["alarm_hub_1"]["received"]
        update = copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        update["metadata"]["status"] = {"text": "restored"}
        update["metadata"]["userName"] = "Owner"

        coordinator._handle_event_update("alarmHubDeviceTamper", update)

        record = coordinator._alarm_hub_last_tamper["alarm_hub_1"]
        assert record["status"] == "restored"
        assert record["_lastTamperUser"] == "Owner"
        assert record["received"] >= first_received
        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["alarmHub"]["deviceTamperStatus"] == "restored"

    @pytest.mark.asyncio
    async def test_poll_skips_tamper_record_of_hub_no_longer_listed(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """A record whose hub is not in the collection is not re-added."""
        self._seed_hub(coordinator)
        coordinator._alarm_hub_last_tamper["gone"] = {
            "event_id": "e0",
            "status": "tampered",
            "received": 0.0,
            "_lastTamperUser": None,
            "_lastTamperAt": None,
        }
        coordinator.protect_client.alarm_hubs.get_all = AsyncMock(
            return_value=[LinkStation.model_validate(SAMPLE_ALARM_HUB)]
        )

        await coordinator._fetch_alarm_hubs()

        assert set(coordinator.data["alarm_hubs"]) == {"alarm_hub_1"}

    @pytest.mark.asyncio
    async def test_fetch_without_protect_client_is_a_no_op(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """A coordinator without a Protect client fetches nothing."""
        coord = UnifiProtectCoordinator(
            hass=hass,
            network_client=_create_mock_network_client(),
            protect_client=None,
            entry=mock_config_entry,
        )
        try:
            await coord._fetch_fobs()
            await coord._fetch_link_stations()
            await coord._fetch_alarm_hubs()
            await coord._fetch_sirens()
            assert coord.data["fobs"] == {}
            assert coord.data["link_stations"] == {}
            assert coord.data["alarm_hubs"] == {}
            assert coord.data["sirens"] == {}
        finally:
            await coord.async_shutdown()

    @pytest.mark.asyncio
    async def test_device_without_id_is_skipped(
        self, coordinator: UnifiProtectCoordinator
    ) -> None:
        """An item the API returns without an id can't be keyed, so it's left out."""
        coordinator.protect_client.fobs.get_all = AsyncMock(
            return_value=[{"name": "no id"}, Fob.model_validate(SAMPLE_KEYPAD_FOB)]
        )

        await coordinator._fetch_fobs()

        assert set(coordinator.data["fobs"]) == {"fob_1"}


class TestConfigCoordinatorVouchers:
    """Tests for voucher polling in UnifiConfigCoordinator."""

    @pytest.fixture
    async def coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> AsyncGenerator[UnifiConfigCoordinator]:
        """Create a config coordinator for testing."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        coordinator = UnifiConfigCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )
        try:
            yield coordinator
        finally:
            await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_vouchers_polled_for_each_selected_site(
        self, hass: HomeAssistant
    ) -> None:
        """Vouchers are polled for each selected site and stored keyed by voucher ID."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_API_KEY: "test_api_key"},
            options={CONF_SITE_IDS: ["default", "site2"]},
        )
        network_client = _create_mock_network_client()
        network_client.vouchers.get_all_pages = AsyncMock(
            side_effect=lambda site_id: [
                _create_mock_model(
                    {
                        "id": f"{site_id}_v1",
                        "code": "1234567890",
                        "timeLimitMinutes": 480,
                    }
                )
            ]
        )
        coordinator = UnifiConfigCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=None,
            entry=entry,
        )
        result = await coordinator._async_update_data()
        assert coordinator.network_client.vouchers.get_all_pages.await_count == 2
        polled_sites = [
            c.args[0]
            for c in coordinator.network_client.vouchers.get_all_pages.await_args_list
        ]
        assert polled_sites == ["default", "site2"]
        assert "vouchers" in result
        assert result["vouchers"]["default"]["default_v1"]["code"] == "1234567890"
        assert result["vouchers"]["default"]["default_v1"]["timeLimitMinutes"] == 480
        assert result["vouchers"]["site2"]["site2_v1"]["code"] == "1234567890"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            UniFiNotFoundError("Not found", status_code=404),
            UniFiResponseError("Bad request", status_code=400),
            UniFiAuthenticationError("Forbidden", status_code=403),
            UniFiResponseError("HTML instead of JSON", status_code=200),
        ],
        ids=["404", "400", "403", "200-non-json"],
    )
    async def test_vouchers_unsupported_endpoint_gives_empty_inventory(
        self, coordinator: UnifiConfigCoordinator, error: Exception
    ) -> None:
        """A 4xx / non-JSON error gives empty inventory and stays available."""
        coordinator.network_client.vouchers.get_all_pages = AsyncMock(side_effect=error)
        result = await coordinator._async_update_data()
        assert result["vouchers"]["default"] == {}
        assert coordinator.vouchers_available("default") is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            UniFiResponseError("Server error", status_code=500),
            UniFiRateLimitError("Slow down", status_code=429),
            UniFiConnectionError("Connection refused"),
            UniFiTimeoutError("Timed out"),
            ValueError("Unparseable payload"),
        ],
        ids=["500", "429", "connection", "timeout", "unexpected"],
    )
    async def test_vouchers_transient_failure_keeps_inventory_and_flags_section(
        self, coordinator: UnifiConfigCoordinator, error: Exception
    ) -> None:
        """Transient error keeps inventory and marks section unavailable."""
        coordinator.network_client.vouchers.get_all_pages = AsyncMock(
            return_value=[_create_mock_model({"id": "v1", "code": "1111111111"})]
        )
        await coordinator.async_refresh()
        assert coordinator.vouchers_available("default") is True
        assert coordinator.wifi_available("default") is True
        assert coordinator.firewall_available("default") is True
        assert "v1" in coordinator.data["vouchers"]["default"]

        coordinator.network_client.vouchers.get_all_pages = AsyncMock(side_effect=error)
        await coordinator.async_refresh()

        assert coordinator.last_update_success is True
        assert "v1" in coordinator.data["vouchers"]["default"]
        assert coordinator.vouchers_available("default") is False
        assert coordinator.wifi_available("default") is True
        assert coordinator.firewall_available("default") is True

    @pytest.mark.asyncio
    async def test_vouchers_recover_after_failure(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """Section flags as recovered and available after transient failure resolves."""
        coordinator.network_client.vouchers.get_all_pages = AsyncMock(
            side_effect=UniFiConnectionError("Connection lost")
        )
        await coordinator.async_refresh()
        assert coordinator.vouchers_available("default") is False

        coordinator.network_client.vouchers.get_all_pages = AsyncMock(
            return_value=[_create_mock_model({"id": "v1", "code": "1111111111"})]
        )
        await coordinator.async_refresh()
        assert coordinator.vouchers_available("default") is True
        assert "v1" in coordinator.data["vouchers"]["default"]

    @pytest.mark.asyncio
    async def test_vouchers_authentication_failure_raises_auth_failed(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """A 401 error from vouchers endpoint raises ConfigEntryAuthFailed."""
        coordinator.network_client.vouchers.get_all_pages = AsyncMock(
            side_effect=UniFiAuthenticationError("Revoked", status_code=401)
        )
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()
        assert coordinator._available is False

    @pytest.mark.asyncio
    async def test_vouchers_pruned_when_site_deselected(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """Vouchers for deselected sites are pruned from coordinator data."""
        coordinator.data["vouchers"]["stale_site"] = {"v1": {"id": "v1"}}
        result = await coordinator._async_update_data()
        assert "stale_site" not in result["vouchers"]
        assert "default" in result["vouchers"]

    @pytest.mark.asyncio
    async def test_vouchers_cleared_when_no_site_selected(
        self, hass: HomeAssistant
    ) -> None:
        """Vouchers dict is cleared when no sites exist / match selection."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_API_KEY: "test_api_key"},
            options={CONF_SITE_IDS: ["nonexistent"]},
        )
        coordinator = UnifiConfigCoordinator(
            hass=hass,
            network_client=_create_mock_network_client(),
            protect_client=None,
            entry=entry,
        )
        result = await coordinator._async_update_data()
        assert result["vouchers"] == {}

    @pytest.mark.asyncio
    async def test_async_refresh_vouchers_updates_only_that_site(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """async_refresh_vouchers refreshes only target site and notifies listeners."""
        await coordinator.async_refresh()
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        coordinator.network_client.vouchers.get_all_pages = AsyncMock(
            return_value=[_create_mock_model({"id": "v-new", "code": "2222222222"})]
        )
        await coordinator.async_refresh_vouchers("default")

        assert coordinator.network_client.vouchers.get_all_pages.await_count == 1
        assert coordinator.network_client.vouchers.get_all_pages.await_args[0] == (
            "default",
        )
        assert "v-new" in coordinator.data["vouchers"]["default"]
        listener.assert_called_once()

        # Target error sets failed section without throwing
        listener.reset_mock()
        coordinator.network_client.vouchers.get_all_pages = AsyncMock(
            side_effect=UniFiConnectionError("Refused")
        )
        await coordinator.async_refresh_vouchers("default")
        assert coordinator.vouchers_available("default") is False
        assert "v-new" in coordinator.data["vouchers"]["default"]
        listener.assert_called_once()

        # Target success clears failed section
        coordinator.network_client.vouchers.get_all_pages = AsyncMock(return_value=[])
        await coordinator.async_refresh_vouchers("default")
        assert coordinator.vouchers_available("default") is True

        # Unselected site returns without call
        coordinator.network_client.vouchers.get_all_pages.reset_mock()
        await coordinator.async_refresh_vouchers("unknown_site")
        coordinator.network_client.vouchers.get_all_pages.assert_not_called()

    @pytest.mark.asyncio
    async def test_vouchers_available_follows_last_update_success(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """vouchers_available returns False when last_update_success is False."""
        await coordinator.async_refresh()
        assert coordinator.vouchers_available("default") is True
        coordinator.last_update_success = False
        assert coordinator.vouchers_available("default") is False

    @pytest.mark.asyncio
    async def test_vouchers_incomplete_listing_keeps_inventory_and_flags_section(
        self, coordinator: UnifiConfigCoordinator
    ) -> None:
        """Incomplete voucher listing keeps inventory and marks unavailable."""
        coordinator.network_client.vouchers.get_all_pages = AsyncMock(
            return_value=[_create_mock_model({"id": "v1", "code": "1111111111"})]
        )
        await coordinator.async_refresh()
        assert coordinator.vouchers_available("default") is True
        assert "v1" in coordinator.data["vouchers"]["default"]

        coordinator.network_client.vouchers.get_all_pages = AsyncMock(
            side_effect=RuntimeError(
                "Incomplete voucher listing for site default: reached page limit 50"
            )
        )
        await coordinator.async_refresh()
        assert coordinator.vouchers_available("default") is False
        assert "v1" in coordinator.data["vouchers"]["default"]

    async def test_vouchers_validation_error_does_not_leak_code_in_logs(
        self,
        coordinator: UnifiConfigCoordinator,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Pydantic validation error during polling does not log the voucher code."""
        synthetic_code = "1234567890"

        class _TestClient:
            def build_api_path(self, path: str) -> str:
                return path

            async def _get(
                self,
                path: str,
                params: Any = None,
                *,
                expected_unsupported: bool = False,
                log_body: bool = True,
            ) -> Any:
                assert path.endswith("/hotspot/vouchers")
                assert params == {"offset": 0, "limit": 1000}
                assert expected_unsupported is True
                assert log_body is False
                return {"data": [{"code": synthetic_code}], "totalCount": 1}

        coordinator.network_client.vouchers = VouchersEndpoint(_TestClient())  # type: ignore[assignment]
        with caplog.at_level("DEBUG"):
            await coordinator.async_refresh()

        assert coordinator.vouchers_available("default") is False
        assert synthetic_code not in caplog.text
        assert "Invalid voucher data (fields: id)" in caplog.text
