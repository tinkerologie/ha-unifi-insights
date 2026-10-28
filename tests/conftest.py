# Copyright 2026 UniFi Insights contributors
"""Test fixtures for ha-unifi-insights integration tests."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_VERIFY_SSL
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api.exceptions import UniFiUnsupportedError
from unifi_official_api.innerspace import InnerSpaceProject

from custom_components.unifi_insights.const import (
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_LOCAL,
    DOMAIN,
)

from .fixtures.command_owners import (
    SeededOwners,
    async_refresh_owners,
    async_shutdown_owners,
    build_seeded_owners,
)
from .fixtures.deprecations import HANDLER as DEPRECATION_REPORTS
from .fixtures.facade_data import facade_data

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator

    from homeassistant.core import HomeAssistant

pytest_plugins = "pytest_homeassistant_custom_component"


def _create_mock_network_client() -> MagicMock:
    """Create a mock network client with all required methods."""
    client = MagicMock()
    client.base_url = "https://192.168.1.1"

    # Setup sites namespace
    client.sites = MagicMock()
    client.sites.get_all = AsyncMock(
        return_value=[MagicMock(id="default", name="Default", desc="Default")]
    )
    client.sites.get_legacy_all = AsyncMock(
        return_value=[{"name": "default", "desc": "Default"}]
    )

    # Setup devices namespace
    client.devices = MagicMock()
    client.devices.get_all = AsyncMock(
        return_value=[
            MagicMock(
                id="device1",
                name="Test Device",
                model="USW-24",
                mac="AA:BB:CC:DD:EE:FF",
                state="ONLINE",
                ipAddress="192.168.1.10",
            )
        ]
    )
    client.devices.get = AsyncMock(
        return_value=MagicMock(id="device1", name="Test Device", model="USW-24")
    )
    client.devices.get_statistics = AsyncMock(
        return_value=MagicMock(cpu=10.5, mem=25.3)
    )
    client.devices.get_legacy_site_devices = AsyncMock(return_value=[])
    client.devices.restart = AsyncMock(return_value=True)

    # Setup clients namespace
    client.clients = MagicMock()
    client.clients.get_all = AsyncMock(return_value=[])
    client.clients.authorize_guest = AsyncMock(return_value=True)

    # Setup WiFi namespace
    client.wifi = MagicMock()
    client.wifi.get_all = AsyncMock(return_value=[])

    # Setup firewall namespace
    client.firewall = MagicMock()
    client.firewall.list_rules = AsyncMock(return_value=[])
    client.firewall.update_rule = AsyncMock()

    # Setup vouchers namespace
    client.vouchers = MagicMock()
    client.vouchers.get_all_pages = AsyncMock(return_value=[])
    client.vouchers.create = AsyncMock(return_value=[MagicMock(id="voucher1")])
    client.vouchers.delete = AsyncMock(return_value=True)

    client.reports = MagicMock()
    client.reports.get_site_report = AsyncMock(return_value=[])
    client.get_site_report = AsyncMock(return_value=[])

    # Legacy methods (keeping for backwards compatibility in tests)
    client.restart_device = AsyncMock(return_value=True)
    client.authorize_guest = AsyncMock(return_value=True)
    client.generate_voucher = AsyncMock(return_value=[{"id": "voucher1"}])
    client.delete_voucher = AsyncMock(return_value=True)
    client.power_cycle_port = AsyncMock(return_value=True)
    client.close = AsyncMock()

    return client


def _create_mock_protect_client() -> MagicMock:
    """Create a mock protect client with all required methods."""
    client = MagicMock()
    client.base_url = "https://192.168.1.1"

    # Setup cameras namespace
    client.cameras = MagicMock()
    client.cameras.get_all = AsyncMock(
        return_value=[
            MagicMock(
                id="camera1",
                name="Front Door",
                state="CONNECTED",
                type="UVC-G4-DOORBELL",
                mac="11:22:33:44:55:66",
                featureFlags=MagicMock(smartDetectTypes=["person", "vehicle"]),
            )
        ]
    )
    client.cameras.get_snapshot = AsyncMock(return_value=b"fake_image_data")
    client.cameras.create_rtsps_stream = AsyncMock(
        return_value=MagicMock(url="rtsps://192.168.1.1/stream")
    )
    client.cameras.set_hdr_mode = AsyncMock(return_value=True)
    client.cameras.set_video_mode = AsyncMock(return_value=True)
    client.cameras.set_microphone_volume = AsyncMock(return_value=True)

    # Setup lights namespace
    client.lights = MagicMock()
    client.lights.get_all = AsyncMock(return_value=[])
    client.lights.set_mode = AsyncMock(return_value=True)
    client.lights.set_brightness = AsyncMock(return_value=True)

    # Setup sensors namespace
    client.sensors = MagicMock()
    client.sensors.get_all = AsyncMock(return_value=[])

    # Setup chimes namespace
    client.chimes = MagicMock()
    client.chimes.get_all = AsyncMock(return_value=[])
    client.chimes.play = AsyncMock(
        side_effect=UniFiUnsupportedError("Synthetic unsupported playback")
    )
    client.chimes.set_volume = AsyncMock(return_value=True)
    client.chimes.set_ringtone = AsyncMock(return_value=True)
    client.chimes.set_repeat = AsyncMock(return_value=True)

    # Setup viewers namespace
    client.viewers = MagicMock()
    client.viewers.get_all = AsyncMock(return_value=[])
    client.viewers.update = AsyncMock(return_value=True)

    # Setup liveviews namespace
    client.liveviews = MagicMock()
    client.liveviews.get_all = AsyncMock(return_value=[])
    client.liveviews.create = AsyncMock(return_value=MagicMock(id="liveview1"))

    # Setup NVR namespace
    client.nvr = MagicMock()
    client.nvr.get = AsyncMock(
        return_value=MagicMock(id="nvr1", name="NVR", type="UNVR")
    )

    # Legacy methods (keeping for backwards compatibility)
    client.update_camera = AsyncMock(return_value=True)
    client.set_hdr_mode = AsyncMock(return_value=True)
    client.set_video_mode = AsyncMock(return_value=True)
    client.set_microphone_volume = AsyncMock(return_value=True)
    client.set_light_mode = AsyncMock(return_value=True)
    client.set_light_brightness = AsyncMock(return_value=True)
    client.ptz_move_to_preset = AsyncMock(return_value=True)
    client.ptz_start_patrol = AsyncMock(return_value=True)
    client.ptz_stop_patrol = AsyncMock(return_value=True)
    client.set_chime_volume = AsyncMock(return_value=True)
    client.set_chime_ringtone = AsyncMock(return_value=True)
    client.set_chime_repeat = AsyncMock(return_value=True)
    client.trigger_alarm = AsyncMock(return_value=True)
    client.create_liveview = AsyncMock(return_value={"id": "liveview1"})
    client.update_viewer = AsyncMock(return_value=True)

    # Protect 7.3.70 security device families (empty by default)
    for family in ("fobs", "link_stations", "alarm_hubs", "sirens"):
        endpoint = MagicMock()
        endpoint.get_all = AsyncMock(return_value=[])
        endpoint.last_result_complete = True
        setattr(client, family, endpoint)

    # WebSocket support (real-time device updates, additive to polling)
    client.get_host_id = AsyncMock(return_value="nvr1")
    client.websocket = MagicMock()
    client.websocket.subscribe_with_callback = AsyncMock()
    client.websocket.stop = MagicMock()

    client.close = AsyncMock()

    return client


def _create_mock_innerspace_client() -> MagicMock:
    """Create a mock InnerSpace client defaulting to empty/unconfigured console."""
    client = MagicMock()
    client.base_url = "https://192.168.1.1"
    client.get_project = AsyncMock(return_value=InnerSpaceProject())
    client.list_floor_plans = AsyncMock(return_value=[])
    client.list_access_points = AsyncMock(return_value=[])
    client.list_switches = AsyncMock(return_value=[])
    client.list_inventory = AsyncMock(return_value=[])
    client.validate_connection = AsyncMock(return_value=True)
    client.close = AsyncMock()
    return client


@pytest.fixture(autouse=True)
def fail_on_ha_deprecation_reports() -> Generator[None]:
    """Fail when Home Assistant logs deprecated use by this integration.

    Reports logged while modules were imported surface on the first test.
    """
    yield
    if reports := DEPRECATION_REPORTS.pop_reports():
        pytest.fail(
            "Home Assistant reported deprecated use by unifi_insights:\n"
            + "\n".join(reports),
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def mock_innerspace_client() -> Generator[MagicMock]:
    """Return a mocked UniFi InnerSpace client."""
    client = _create_mock_innerspace_client()

    with patch(
        "custom_components.unifi_insights.UniFiInnerSpaceClient",
        MagicMock(return_value=client),
    ):
        yield client


@pytest.fixture(autouse=True)
def mock_mobility_client() -> Generator[MagicMock]:
    """Keep remote entries off the Mobility cloud API: an account without routers."""
    client = MagicMock()
    client.list_workspaces = AsyncMock(return_value=[])
    client.list_devices = AsyncMock(return_value=[])
    client.get_device = AsyncMock(return_value={})
    client.close = AsyncMock()

    with patch(
        "custom_components.unifi_insights.coordinators.mobility.UniFiMobilityClient",
        MagicMock(return_value=client),
    ):
        yield client


@pytest.fixture
def mock_site_manager_client() -> Generator[MagicMock]:
    """Keep the account-wide Site Manager poller of remote entries off the network."""
    with patch(
        "custom_components.unifi_insights.coordinators.site_manager."
        "UniFiSiteManagerClient"
    ) as client_class:
        client = client_class.return_value
        for method in (
            "list_hosts",
            "list_sites",
            "list_devices",
            "get_isp_metrics",
            "list_sd_wan_configs",
        ):
            setattr(client, method, AsyncMock(return_value=[]))
        client.close = AsyncMock()
        yield client


@pytest.fixture
def mock_network_client() -> Generator[MagicMock]:
    """Return a mocked UniFi Network client."""
    client = _create_mock_network_client()

    # Create a mock class that works as both regular init and async context manager
    mock_class = MagicMock()
    mock_class.return_value = client

    # Support async context manager for config_flow.py
    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(return_value=client)
    async_cm.__aexit__ = AsyncMock(return_value=None)
    mock_class.return_value = async_cm

    # Also make the mock itself work like the client for direct usage
    for attr in dir(client):
        if not attr.startswith("_"):
            setattr(mock_class.return_value, attr, getattr(client, attr))

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            mock_class,
        ),
        patch(
            "custom_components.unifi_insights.UniFiNetworkClient",
            MagicMock(return_value=client),
        ),
    ):
        yield client


@pytest.fixture
def mock_protect_client() -> Generator[MagicMock]:
    """Return a mocked UniFi Protect client."""
    client = _create_mock_protect_client()

    # Create a mock class that works as both regular init and async context manager
    mock_class = MagicMock()
    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(return_value=client)
    async_cm.__aexit__ = AsyncMock(return_value=None)
    mock_class.return_value = async_cm

    for attr in dir(client):
        if not attr.startswith("_"):
            setattr(mock_class.return_value, attr, getattr(client, attr))

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient",
            mock_class,
        ),
        patch(
            "custom_components.unifi_insights.UniFiProtectClient",
            MagicMock(return_value=client),
        ),
    ):
        yield client


@pytest.fixture
def mock_local_auth() -> Generator[MagicMock]:
    """Return a mocked LocalAuth."""
    with (
        patch(
            "custom_components.unifi_insights.config_flow.LocalAuth", autospec=True
        ) as mock,
        patch("custom_components.unifi_insights.LocalAuth", autospec=True),
    ):
        yield mock.return_value


@pytest.fixture
def mock_api_key_auth() -> Generator[MagicMock]:
    """Return a mocked ApiKeyAuth."""
    with (
        patch(
            "custom_components.unifi_insights.config_flow.ApiKeyAuth", autospec=True
        ) as mock,
        patch("custom_components.unifi_insights.ApiKeyAuth", autospec=True),
    ):
        yield mock.return_value


@pytest.fixture
def mock_coordinator() -> MagicMock:
    """Return a mocked UniFi Insights coordinator with a frozen facade snapshot."""
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.available = True
    coordinator.network_client = _create_mock_network_client()
    coordinator.protect_client = _create_mock_protect_client()
    coordinator.innerspace_client = None
    coordinator.data = facade_data(
        sites={"default": {"id": "default", "name": "Default"}},
        site_ids=("default",),
        last_update=None,
    )
    coordinator.async_refresh = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


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


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    mock_local_auth: MagicMock,
    *,
    enable_custom_integrations,
) -> MockConfigEntry:
    """Set up the integration for testing."""
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    return mock_config_entry


def set_mock_device_lookup(registry: MagicMock, device: MagicMock | None) -> None:
    """Make every DeviceRegistry lookup path on ``registry`` resolve to ``device``.

    ``async_get_device_entry`` picks its lookup method from what the installed
    Home Assistant exposes: ``async_get_device_by_identifier`` (2026.8+),
    ``async_get_devices``, or the deprecated ``async_get_device``. A test that
    stubs only one of them silently stops simulating anything on the other
    versions, because an unstubbed ``MagicMock`` attribute returns a truthy
    mock rather than the intended miss.
    """
    registry.async_get_device.return_value = device
    registry.async_get_device_by_identifier.return_value = device
    registry.async_get_devices.return_value = [device] if device is not None else []


def mock_device_lookup_method(
    registry: MagicMock, config_entry_id: str | None = None
) -> MagicMock:
    """Return the registry attribute ``async_get_device_entry`` will actually call.

    Mirrors the branch selection in the helper under test so assertions follow
    the production code across the 2026.8 API addition.
    """
    if (
        hasattr(dr.DeviceRegistry, "async_get_device_by_identifier")
        and config_entry_id is not None
    ):
        return registry.async_get_device_by_identifier
    if hasattr(dr.DeviceRegistry, "async_get_devices"):
        return registry.async_get_devices
    return registry.async_get_device


def assert_mock_device_lookup(
    registry: MagicMock,
    identifier: tuple[str, str],
    config_entry_id: str | None = None,
) -> None:
    """Assert ``identifier`` was looked up through whichever API this HA exposes."""
    lookup = mock_device_lookup_method(registry, config_entry_id)
    if lookup is registry.async_get_device_by_identifier:
        lookup.assert_any_call(identifier, config_entry_id)
    else:
        lookup.assert_any_call(identifiers={identifier})


def get_device_by_identifier(
    device_registry: dr.DeviceRegistry,
    identifier: tuple[str, str],
    config_entry_id: str,
) -> dr.DeviceEntry | None:
    """Look up a device in a real registry across Home Assistant releases."""
    if hasattr(device_registry, "async_get_device_by_identifier"):
        return device_registry.async_get_device_by_identifier(
            identifier, config_entry_id
        )
    return device_registry.async_get_device(identifiers={identifier})


def device_config_entry_ids(device: dr.DeviceEntry) -> set[str]:
    """Return the set of config entry IDs owning this device.

    Normalizes ownership across Home Assistant releases where devices moved
    from config_entries: set[str] to a single config_entry_id: str | None.
    """
    if hasattr(device, "config_entry_id"):
        entry_id = device.config_entry_id
        return {entry_id} if entry_id else set()
    return set(device.config_entries)


@pytest.fixture
async def seeded_owners(hass: HomeAssistant) -> AsyncGenerator[SeededOwners]:
    """Real command owners over seeded autospecced clients, refreshed once."""
    owners = build_seeded_owners(hass)
    await async_refresh_owners(owners)
    yield owners
    await async_shutdown_owners(owners)
