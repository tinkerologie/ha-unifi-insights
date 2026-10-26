# Copyright 2026 UniFi Insights contributors
"""Tests for the UniFi Insights integration initialization."""

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.const import CONF_API_KEY, CONF_HOST, Platform
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiNotFoundError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from unifi_official_api.innerspace import (
    InnerSpaceFloorPlan,
    InnerSpaceInventoryDevice,
    InnerSpaceProject,
    InnerSpaceProjectIdentity,
)

from custom_components.unifi_insights import (
    _SETUP_PROBE_ATTEMPTS,
    CARRIER_FABRIC_PLATFORMS,
    PLATFORMS,
    SETUP_PROBE_RETRIES,
    CarrierFabricData,
    UnifiInsightsData,
    _raise_for_setup_probes,
    async_remove_config_entry_device,
    async_unload_entry,
)
from custom_components.unifi_insights.const import (
    CONF_CARRIER_ORG_ID,
    CONF_CONNECTION_TYPE,
    CONF_CONSOLE_ID,
    CONF_SITE_IDS,
    CONF_TRACK_SUBSCRIBERS,
    CONNECTION_TYPE_CARRIER_FABRIC,
    DOMAIN,
)
from custom_components.unifi_insights.data_transforms import (
    owner_available,
    site_section_available,
)
from custom_components.unifi_insights.probe import ProbeResult, ProbeStatus
from tests.fixtures.assertions import require_not_none
from tests.fixtures.mobility_responses import (
    OFFICE_ROUTER_ID,
    WORKSPACE_ID,
    serve_spec_examples,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_registry import EntityRegistry
    from pytest_homeassistant_custom_component.typing import WebSocketGenerator


async def test_setup_entry_success(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    *,
    enable_custom_integrations,
) -> None:
    """Test successful setup of config entry."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.LOADED


@pytest.mark.parametrize(
    ("site_id", "expected_identity"),
    [
        ("synthetic_site", "https://console.example.invalid"),
        ("default", "https://console.example.invalid"),
    ],
)
async def test_setup_legacy_credential_identity_without_console_mac(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    mock_local_auth: MagicMock,
    *,
    enable_custom_integrations: None,
    entity_registry: EntityRegistry,
    site_id: str,
    expected_identity: str,
) -> None:
    """Backfill a legacy credential identity before new site sensors are discovered."""
    credential = "synthetic_setup_credential"
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            CONF_API_KEY: credential,
            CONF_CONSOLE_ID: credential,
            CONF_HOST: "https://Console.Example.Invalid",
        },
        unique_id=credential,
    )
    mock_network_client.sites.get_all.return_value = [
        {"id": site_id, "name": "Synthetic site"}
    ]
    mock_network_client.devices.get_all.return_value = []
    mock_protect_client.cameras.get_all.return_value = []
    mock_protect_client.nvr.get.return_value = None

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.LOADED
    assert mock_config_entry.data[CONF_CONSOLE_ID] == expected_identity
    assert mock_config_entry.unique_id == expected_identity
    expected_keys = {
        "site_total_clients",
        "site_wired_clients",
        "site_wireless_clients",
    }
    rows = [
        row
        for row in entity_registry.entities.values()
        if row.config_entry_id == mock_config_entry.entry_id
        and row.translation_key in expected_keys
    ]
    assert len(rows) == 3
    assert {row.translation_key for row in rows} == expected_keys
    assert all(
        row.unique_id.startswith(f"{expected_identity}_{site_id}_") for row in rows
    )
    assert all(credential not in row.unique_id for row in rows)


async def test_setup_entry_auth_failed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    *,
    enable_custom_integrations,
) -> None:
    """Test setup fails with authentication error."""
    mock_network_client.sites.get_all.side_effect = UniFiAuthenticationError(
        "Invalid API key"
    )
    mock_protect_client.cameras.get_all.side_effect = UniFiAuthenticationError(
        "Invalid API key"
    )

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.SETUP_ERROR


async def test_setup_entry_connection_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    *,
    enable_custom_integrations,
) -> None:
    """Test setup fails with connection error."""
    mock_network_client.sites.get_all.side_effect = UniFiConnectionError(
        "Cannot connect"
    )
    mock_protect_client.cameras.get_all.side_effect = UniFiConnectionError(
        "Cannot connect"
    )

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.SETUP_RETRY


async def test_setup_entry_timeout_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    *,
    enable_custom_integrations,
) -> None:
    """Test setup fails with timeout error."""
    mock_network_client.sites.get_all.side_effect = UniFiTimeoutError(
        "Connection timeout"
    )
    mock_protect_client.cameras.get_all.side_effect = UniFiTimeoutError(
        "Connection timeout"
    )

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.SETUP_RETRY


async def test_setup_entry_protect_unavailable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    *,
    enable_custom_integrations,
) -> None:
    """Test setup succeeds even if Protect is unavailable."""
    mock_protect_client.cameras.get_all.side_effect = Exception("Protect unavailable")

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.LOADED


async def test_setup_entry_protect_only_console(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    *,
    enable_custom_integrations,
) -> None:
    """Test setup succeeds on Protect-only console like UNVR (Issue 93)."""
    mock_network_client.sites.get_all.return_value = []

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.LOADED


async def test_unload_entry(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test successful unload of a config entry."""
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state == ConfigEntryState.NOT_LOADED


async def test_setup_entry_starts_protect_websocket(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test setup resolves host_id and starts the real-time WebSocket.

    Regression test for the dead `hasattr(..., "register_device_update_callback")`
    stub: the coordinator must actually invoke `get_host_id()` and hand a real
    background task to `ProtectWebSocket.subscribe_with_callback`, not just
    construct without error. Also confirms the second, independent "events"
    subscription (task 1 - without it, motion detection is permanently
    non-functional) starts alongside "devices".
    """
    runtime_data = init_integration.runtime_data
    protect_coordinator = runtime_data.protect_coordinator
    assert protect_coordinator is not None

    runtime_data.protect_client.get_host_id.assert_awaited_once()
    assert protect_coordinator.websocket_task is not None
    assert protect_coordinator.events_websocket_task is not None
    await protect_coordinator.websocket_task
    await protect_coordinator.events_websocket_task
    assert (
        protect_coordinator._protect_websocket.subscribe_with_callback.await_count == 2
    )


async def test_unload_entry_cancels_real_websocket_task(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test unload stops the ProtectWebSocket and cancels/awaits the real task.

    Regression test: `websocket_task.cancel()` alone does not stop
    `subscribe_with_callback`'s reconnect loop (it swallowed
    `CancelledError` and slept/reconnected instead), which would leave an
    orphaned WebSocket loop running after a config entry reload.
    """
    runtime_data = init_integration.runtime_data
    protect_coordinator = runtime_data.protect_coordinator
    assert protect_coordinator is not None
    websocket_task = protect_coordinator.websocket_task
    events_websocket_task = protect_coordinator.events_websocket_task
    assert websocket_task is not None
    assert events_websocket_task is not None

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state == ConfigEntryState.NOT_LOADED
    # Called twice by design: once from async_unload_entry's explicit call
    # (correct ordering - stop before closing the Protect client) and once
    # more via the entry.async_on_unload safety net registered in
    # async_setup_entry (covers a setup failure that happens after the
    # WebSocket starts but before async_unload_entry ever runs).
    # async_stop_websocket() is idempotent, so this is expected, not a bug.
    assert protect_coordinator._protect_websocket.stop.call_count == 2
    assert websocket_task.done()
    assert events_websocket_task.done()


async def test_reload_entry(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test successful reload of a config entry."""
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state == ConfigEntryState.LOADED


async def test_reload_entry_via_options_update(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test reload triggered by options update (update listener)."""
    # Update options to trigger the update listener (async_reload_entry)
    hass.config_entries.async_update_entry(
        init_integration,
        options={"track_wifi_clients": True},
    )
    await hass.async_block_till_done()

    # Entry should be reloaded and in loaded state
    assert init_integration.state == ConfigEntryState.LOADED


async def test_setup_entry_no_sites_found(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    *,
    enable_custom_integrations,
) -> None:
    """Test setup fails when no sites and no protect NVR are found."""
    # Return empty list - no sites found
    mock_network_client.sites.get_all.return_value = []
    mock_protect_client.cameras.get_all.return_value = []
    mock_protect_client.nvr.get.return_value = None

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # A console that is still starting answers this way, so setup retries
    # first; once the retries are used up it asks for reauth as before.
    assert mock_config_entry.state == ConfigEntryState.SETUP_RETRY

    hass.data[_SETUP_PROBE_ATTEMPTS][mock_config_entry.entry_id] = {
        "inconclusive": SETUP_PROBE_RETRIES
    }
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


@pytest.mark.parametrize("site_manager_outage", [False, True])
@pytest.mark.usefixtures("enable_custom_integrations")
async def test_setup_entry_remote_connection(
    hass: HomeAssistant,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    site_manager_outage: bool,  # noqa: FBT001
) -> None:
    """Site Manager data is optional for a working remote console."""
    # Create remote config entry
    remote_entry = MockConfigEntry(
        domain="unifi_insights",
        data={
            "connection_type": "remote",
            "console_id": "test_console",
            "api_key": "test_api_key",
        },
        entry_id="remote_entry",
    )

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
            setattr(
                client,
                method,
                AsyncMock(
                    side_effect=(
                        UniFiResponseError("cloud unavailable", status_code=502)
                        if site_manager_outage
                        else None
                    ),
                    return_value=[],
                ),
            )
        if not site_manager_outage:
            client.list_hosts.return_value = [{"id": "test_console"}]
        client.close = AsyncMock()

        remote_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(remote_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

        assert remote_entry.state == ConfigEntryState.LOADED
        assert remote_entry.runtime_data.site_manager_coordinator is not None
        assert "site_manager" in remote_entry.runtime_data.coordinator.data
        assert remote_entry.runtime_data.coordinator.data["site_manager"][
            "selected_host_id"
        ] == (None if site_manager_outage else "test_console")
        assert (
            remote_entry.runtime_data.site_manager_coordinator.data["collections"][
                "hosts"
            ]["available"]
            is not site_manager_outage
        )
        assert await hass.config_entries.async_unload(remote_entry.entry_id)
        client.close.assert_awaited_once()


@pytest.mark.usefixtures(
    "mock_network_client",
    "mock_protect_client",
    "mock_local_auth",
    "enable_custom_integrations",
)
@pytest.mark.parametrize("connection_type", ["remote", "local"])
async def test_platform_failure_releases_optional_site_manager(
    hass: HomeAssistant,
    connection_type: str,
) -> None:
    """Platform setup errors release cloud accounts only when one was acquired."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": connection_type,
            "console_id": "test_console",
            "api_key": "test_api_key",
            "host": "https://test.local",
        },
        entry_id=f"{connection_type}_platform_error",
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.unifi_insights.coordinators.site_manager."
            "UniFiSiteManagerClient"
        ) as client_class,
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new_callable=AsyncMock,
            side_effect=RuntimeError("platform setup failed"),
        ),
    ):
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

        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.SETUP_ERROR
        if connection_type == "remote":
            client.close.assert_awaited_once()
        else:
            client_class.assert_not_called()


async def test_unload_entry_with_websocket_task(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test unload entry cancels websocket task."""

    # Add a mock websocket task to the protect coordinator
    runtime_data = init_integration.runtime_data
    if runtime_data.protect_coordinator:
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        runtime_data.protect_coordinator.websocket_task = mock_task

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state == ConfigEntryState.NOT_LOADED


async def test_unload_entry_protect_close_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test unload entry handles protect client close error gracefully."""
    # Make protect client close raise an error
    runtime_data = init_integration.runtime_data
    if runtime_data.protect_client:
        runtime_data.protect_client.close = AsyncMock(
            side_effect=Exception("Close error")
        )

    # Should still unload successfully
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state == ConfigEntryState.NOT_LOADED


async def test_unload_entry_network_close_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test unload entry handles network client close error gracefully."""
    # Make network client close raise an error
    runtime_data = init_integration.runtime_data
    runtime_data.network_client.close = AsyncMock(side_effect=Exception("Close error"))

    # Should still unload successfully
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state == ConfigEntryState.NOT_LOADED


async def test_unload_entry_network_only_skips_protect_stop_and_close(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    mock_local_auth: MagicMock,
    *,
    enable_custom_integrations,
) -> None:
    """Test unload of a real Network-only setup skips Protect stop and close."""
    mock_protect_client.cameras.get_all.side_effect = Exception("Protect unavailable")

    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.LOADED
    runtime_data = mock_config_entry.runtime_data
    assert runtime_data.protect_coordinator is None
    assert runtime_data.protect_client is None
    assert runtime_data.network_client is mock_network_client

    mock_network_client.close.reset_mock()
    mock_protect_client.close.reset_mock()

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.NOT_LOADED
    mock_protect_client.close.assert_not_called()
    mock_network_client.close.assert_awaited_once()


async def test_unifi_insights_data_coordinator_not_initialized(
    hass: HomeAssistant,
) -> None:
    """Test UnifiInsightsData raises error when facade coordinator not initialized."""
    # Create data object with None facade coordinator
    data = UnifiInsightsData(
        config_coordinator=MagicMock(),
        device_coordinator=MagicMock(),
        protect_coordinator=None,
        network_client=MagicMock(),
        protect_client=None,
        _facade_coordinator=None,
    )

    # Accessing coordinator property should raise RuntimeError
    with pytest.raises(RuntimeError, match="Facade coordinator not initialized"):
        _ = data.coordinator


def _device_entry(*identifiers: str) -> MagicMock:
    """Build a device registry entry stub with unifi_insights identifiers."""
    device = MagicMock()
    device.identifiers = {("unifi_insights", identifier) for identifier in identifiers}
    return device


def _entry_with_sites(
    available: dict[str, str],
    polled: list[str],
    wifi: dict[str, dict[str, dict]] | None = None,
    wifi_failed: frozenset[str] | set[str] = frozenset(),
    *,
    last_update_success: bool = True,
) -> MagicMock:
    """Build a config entry whose config coordinator polls only some sites."""
    entry = MagicMock()
    coordinator = entry.runtime_data.config_coordinator
    coordinator.available_sites = available
    coordinator.get_site_ids.return_value = polled
    coordinator.last_update_success = last_update_success
    coordinator.data = {"wifi": wifi if wifi is not None else {}}
    coordinator.wifi_available.side_effect = lambda site_id: (
        last_update_success and site_id not in wifi_failed
    )
    return entry


@pytest.mark.parametrize(
    ("identifiers", "expected"),
    [
        (("site2_device-1",), True),
        (("policy_based_routes_site2",), True),
        (("firewall_policies_site2",), True),
        (("vpn_clients_site2",), True),
        (("site_site2",), True),
        (("site_default",), False),
        # A Protect id that happens to end in a site id is not site-scoped.
        (("protect_camera_site2",), False),
        (("default_device-1",), False),
        (("protect_camera_cam-1",), False),
        (("client_aa:bb:cc:dd:ee:ff",), False),
    ],
)
async def test_remove_config_entry_device_only_for_deselected_sites(
    hass: HomeAssistant,
    identifiers: tuple[str, ...],
    expected: bool,  # noqa: FBT001
) -> None:
    """Devices of a site dropped from the filter can be removed; nothing else (#128)."""
    entry = _entry_with_sites({"default": "Default", "site2": "Branch"}, ["default"])

    assert (
        await async_remove_config_entry_device(hass, entry, _device_entry(*identifiers))
        is expected
    )


@pytest.mark.parametrize(
    ("polled", "wifi_failed", "last_update_success", "expected"),
    [
        # Ticket scenario: the network only exists on the deselected site.
        (["default"], set(), True, True),
        # Every site polled, the network was deleted on the controller.
        (["default", "site2"], set(), True, True),
        # A failed WiFi fetch keeps old data, so absence proves nothing.
        (["default"], {"default"}, True, False),
        # No site is polled, so none can serve the network.
        ([], set(), True, True),
        # A failed refresh proves nothing either, even with no polled sites.
        ([], set(), False, False),
    ],
)
async def test_remove_config_entry_device_stale_wifi(
    hass: HomeAssistant,
    polled: list[str],
    wifi_failed: set[str],
    last_update_success: bool,  # noqa: FBT001
    expected: bool,  # noqa: FBT001
) -> None:
    """A WiFi device is removable only once no polled site provably serves it."""
    entry = _entry_with_sites(
        {"default": "Default", "site2": "Branch"},
        polled,
        wifi={site_id: {"wifi-main": {}} for site_id in polled},
        wifi_failed=wifi_failed,
        last_update_success=last_update_success,
    )

    assert (
        await async_remove_config_entry_device(hass, entry, _device_entry("wifi_gone"))
        is expected
    )


async def test_remove_config_entry_device_refuses_wifi_a_polled_site_serves(
    hass: HomeAssistant,
) -> None:
    """A WiFi network still served by any polled site is live."""
    entry = _entry_with_sites(
        {"default": "Default", "site2": "Branch"},
        ["default", "site2"],
        wifi={"default": {}, "site2": {"wifi-main": {}}},
    )

    assert (
        await async_remove_config_entry_device(
            hass, entry, _device_entry("wifi_wifi-main")
        )
        is False
    )


@pytest.mark.parametrize(
    ("identifiers", "expected"),
    [
        # A site deleted on the console is in no site list at all.
        (("gone_device-1",), True),
        (("gone_site_device_1",), True),
        (("site_gone",), True),
        (("firewall_policies_gone",), True),
        (("policy_based_routes_gone",), True),
        (("vpn_clients_gone",), True),
        # Deselected sites stay removable, polled sites stay live.
        (("site2_device-1",), True),
        (("default_device-1",), False),
        (("site_default",), False),
        # Other identifier families are not site-scoped.
        (("protect_camera_cam-1",), False),
        (("client_aa:bb:cc:dd:ee:ff",), False),
        (("access_console_ns",), False),
        (("innerspace_r1",), False),
        (("mobility_router-1",), False),
        (("carrier_subscriber_x",), False),
        # The console device and identifiers without a site part.
        (("controller-id",), False),
        (("console-mac",), False),
        (("gone_",), False),
        # A merged device with a live or non-site identifier is kept.
        (("gone_dev", "default_dev2"), False),
        (("gone_dev", "protect_camera_x"), False),
        # Identifiers of other integrations neither allow nor block.
        (("gone_dev", ("other", "default_dev")), True),
        ((("other", "gone_dev"),), False),
    ],
)
async def test_remove_config_entry_device_of_a_deleted_site(
    hass: HomeAssistant,
    identifiers: tuple[str | tuple[str, str], ...],
    expected: bool,  # noqa: FBT001
) -> None:
    """Devices of a site the console no longer lists can be removed (#394)."""
    entry = _entry_with_sites({"default": "Default", "site2": "Branch"}, ["default"])
    entry.unique_id = "controller-id"
    entry.entry_id = "entry-1"
    entry.runtime_data.mobility_coordinator.last_update_success = False
    device = MagicMock()
    device.identifiers = {
        item if isinstance(item, tuple) else ("unifi_insights", item)
        for item in identifiers
    }

    assert await async_remove_config_entry_device(hass, entry, device) is expected


@pytest.mark.parametrize(
    ("protect_data", "expected"),
    [
        # Protect still reports the camera or NVR merged into the device.
        ({"cameras": {"cam-1": {"mac": "AA-BB-CC-DD-EE-FF"}}, "nvrs": {}}, False),
        ({"cameras": {}, "nvrs": {"nvr-1": {"mac": "aa:bb:cc:dd:ee:ff"}}}, False),
        # Protect no longer reports it.
        ({"cameras": {"cam-2": {"mac": "11:22:33:44:55:66"}}, "nvrs": {}}, True),
        ({"cameras": {"cam-1": "not-a-record"}, "nvrs": None}, True),
        ({}, True),
    ],
)
async def test_remove_config_entry_device_of_a_deleted_site_shared_with_protect(
    hass: HomeAssistant,
    protect_data: dict[str, Any],
    expected: bool,  # noqa: FBT001
) -> None:
    """A deleted site's device that Protect still reports is kept (#394)."""
    entry = _entry_with_sites({"default": "Default"}, ["default"])
    entry.runtime_data.protect_coordinator.data = protect_data
    entry.runtime_data.mobility_coordinator.last_update_success = False
    device = _device_entry("gone_device-1")
    device.connections = {
        (dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff"),
        ("zigbee", "00:11"),
    }

    assert await async_remove_config_entry_device(hass, entry, device) is expected


@pytest.mark.parametrize(
    ("connections", "protect_coordinator"),
    [
        # A device without a MAC connection holds no Protect camera or NVR.
        (set(), MagicMock(data={"cameras": {"c": {"mac": "aa:bb:cc:dd:ee:ff"}}})),
        # An entry without Protect has nothing to keep alive.
        ({(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff")}, None),
    ],
    ids=["no-mac", "no-protect"],
)
async def test_remove_config_entry_device_of_a_deleted_site_without_protect_link(
    hass: HomeAssistant,
    connections: set[tuple[str, str]],
    protect_coordinator: MagicMock | None,
) -> None:
    """Without a MAC or a Protect coordinator the deleted site rule applies."""
    entry = _entry_with_sites({"default": "Default"}, ["default"])
    entry.runtime_data.mobility_coordinator.last_update_success = False
    entry.runtime_data.protect_coordinator = protect_coordinator
    device = _device_entry("gone_device-1")
    device.connections = connections

    assert await async_remove_config_entry_device(hass, entry, device) is True


@pytest.mark.parametrize(
    ("unique_id", "entry_id", "identifier"),
    [
        ("console_mac", "entry-1", "console_mac"),
        ("console-mac", "entry_1", "entry_1"),
    ],
)
async def test_remove_config_entry_device_refuses_the_console_of_a_deleted_site(
    hass: HomeAssistant,
    unique_id: str,
    entry_id: str,
    identifier: str,
) -> None:
    """A console id shaped like ``{site}_{device}`` is not a deleted site's (#394)."""
    entry = _entry_with_sites({"default": "Default"}, ["default"])
    entry.unique_id = unique_id
    entry.entry_id = entry_id
    entry.runtime_data.mobility_coordinator.last_update_success = False

    assert (
        await async_remove_config_entry_device(hass, entry, _device_entry(identifier))
        is False
    )


@pytest.mark.parametrize(
    ("available", "last_update_success"),
    [({}, True), ({"default": "Default"}, False)],
    ids=["no-site-listed", "refresh-failed"],
)
async def test_remove_config_entry_device_needs_a_site_list_to_trust(
    hass: HomeAssistant,
    available: dict[str, str],
    last_update_success: bool,  # noqa: FBT001
) -> None:
    """An empty site list or a failed refresh proves no site was deleted."""
    entry = _entry_with_sites(
        available, ["default"], last_update_success=last_update_success
    )

    assert (
        await async_remove_config_entry_device(
            hass, entry, _device_entry("gone_device-1")
        )
        is False
    )


async def test_remove_config_entry_device_refuses_a_prefix_of_a_polled_site(
    hass: HomeAssistant,
) -> None:
    """A device of polled site ``a_b`` is not taken for one of a site ``a``."""
    entry = _entry_with_sites({"a_b": "A B"}, ["a_b"])

    assert (
        await async_remove_config_entry_device(hass, entry, _device_entry("a_b_dev"))
        is False
    )


async def test_remove_config_entry_device_refused_when_not_loaded(
    hass: HomeAssistant,
) -> None:
    """Without runtime data nothing is known about sites, so refuse."""
    entry = MagicMock(spec=["entry_id"])

    assert (
        await async_remove_config_entry_device(hass, entry, _device_entry("site2_dev"))
        is False
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the integration for an entry and wait for it to load."""
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.LOADED


async def _remove_device_via_ui(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    entry: MockConfigEntry,
    identifier: str,
) -> dict[str, Any]:
    """Register a device for the entry and remove it the way the UI does."""
    assert await async_setup_component(hass, "config", {})
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, identifier)}
    )
    device_id = device.id
    client = await hass_ws_client(hass)
    ws_commands = hass.data.get(websocket_api.DOMAIN, {})
    if "config/device_registry/remove" in ws_commands:
        payload: dict[str, Any] = {
            "type": "config/device_registry/remove",
            "device_id": device_id,
        }
    else:
        assert "config/device_registry/remove_config_entry" in ws_commands
        payload = {
            "type": "config/device_registry/remove_config_entry",
            "config_entry_id": entry.entry_id,
            "device_id": device_id,
        }
    await client.send_json_auto_id(payload)
    response: dict[str, Any] = await client.receive_json()
    return response


@pytest.mark.usefixtures(
    "mock_protect_client", "mock_local_auth", "enable_custom_integrations"
)
async def test_remove_wifi_device_after_its_site_is_deselected(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client: MagicMock,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """A site's WiFi device can be removed once the site is deselected (#213)."""
    mock_network_client.sites.get_all.return_value = [
        {"id": "default", "name": "Default"},
        {"id": "branch", "name": "Branch"},
    ]
    wifi_by_site = {
        "default": [{"id": "wifi-main", "name": "Main"}],
        "branch": [{"id": "wifi-branch", "name": "Branch"}],
    }
    mock_network_client.wifi.get_all.side_effect = lambda site_id: wifi_by_site[site_id]
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.config_coordinator
    assert "wifi-branch" in coordinator.get_wifi_networks("branch")

    # While the branch site is polled its network is live.
    response = await _remove_device_via_ui(
        hass, hass_ws_client, mock_config_entry, "wifi_wifi-branch"
    )
    assert not response["success"]

    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_SITE_IDS: ["default"]}
    )
    await hass.async_block_till_done()
    coordinator = mock_config_entry.runtime_data.config_coordinator
    assert coordinator.get_site_ids() == ["default"]

    response = await _remove_device_via_ui(
        hass, hass_ws_client, mock_config_entry, "wifi_wifi-branch"
    )

    assert response["success"], response
    assert not any(
        (DOMAIN, "wifi_wifi-branch") in device.identifiers
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), mock_config_entry.entry_id
        )
    )


@pytest.mark.usefixtures(
    "mock_protect_client", "mock_local_auth", "enable_custom_integrations"
)
async def test_remove_wifi_device_refused_while_polled_site_provides_it(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client: MagicMock,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """A WiFi network a polled site still serves is live, so removal is refused."""
    mock_network_client.sites.get_all.return_value = [
        {"id": "default", "name": "Default"}
    ]
    mock_network_client.wifi.get_all.return_value = [
        {"id": "wifi-main", "name": "Main"}
    ]
    await _setup(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.config_coordinator
    assert "wifi-main" in coordinator.get_wifi_networks("default")

    response = await _remove_device_via_ui(
        hass, hass_ws_client, mock_config_entry, "wifi_wifi-main"
    )

    assert not response["success"]
    assert response["error"]["code"] == "home_assistant_error"


async def test_revoked_key_after_setup_starts_reauth(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_network_client: MagicMock,
) -> None:
    """A key revoked while running starts reauth instead of failing silently."""
    assert init_integration.state == ConfigEntryState.LOADED
    config_coordinator = init_integration.runtime_data.config_coordinator
    device_coordinator = init_integration.runtime_data.device_coordinator
    mock_network_client.sites.get_all.return_value = [{"id": "default"}]
    await config_coordinator.async_refresh()
    await device_coordinator.async_refresh()
    assert device_coordinator.last_update_success is True

    mock_network_client.devices.get_all = AsyncMock(
        side_effect=UniFiAuthenticationError("Revoked", status_code=401)
    )
    await device_coordinator.async_refresh()
    await hass.async_block_till_done()

    assert device_coordinator.last_update_success is False
    assert init_integration.runtime_data.coordinator.device_available is False
    flows = hass.config_entries.flow.async_progress_by_handler("unifi_insights")
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_failed_wifi_refresh_marks_wifi_unavailable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_network_client: MagicMock,
) -> None:
    """A failed WiFi refresh is reported, then recovery restores availability."""
    config_coordinator = init_integration.runtime_data.config_coordinator
    facade = init_integration.runtime_data.coordinator
    mock_network_client.sites.get_all.return_value = [{"id": "default"}]
    await config_coordinator.async_refresh()
    assert facade.config_available is True

    mock_network_client.wifi.get_all = AsyncMock(
        side_effect=UniFiConnectionError("Console rebooting")
    )
    await config_coordinator.async_refresh()
    await hass.async_block_till_done()
    assert site_section_available(facade.data, "default", "wifi") is False
    assert site_section_available(facade.data, "default", "firewall_rules") is True
    assert owner_available(facade.data, "config") is True
    assert owner_available(facade.data, "device") is True

    mock_network_client.wifi.get_all = AsyncMock(return_value=[])
    await config_coordinator.async_refresh()
    await hass.async_block_till_done()
    assert site_section_available(facade.data, "default", "wifi") is True


@pytest.mark.parametrize(
    ("namespace", "method", "error", "section"),
    [
        (
            "firewall",
            "list_rules",
            UniFiResponseError("Bad gateway", status_code=502),
            "firewall_rules",
        ),
        (
            "wifi",
            "get_all",
            UniFiConnectionError("Connection reset"),
            "wifi",
        ),
    ],
    ids=["firewall-502", "wifi-connection"],
)
async def test_optional_section_failure_does_not_block_setup(
    hass: HomeAssistant,
    *,
    mock_config_entry: MockConfigEntry,
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    mock_local_auth: MagicMock,
    enable_custom_integrations: None,
    namespace: str,
    method: str,
    error: Exception,
    section: str,
) -> None:
    """A WiFi/firewall outage at startup loads the entry, flagging only it."""
    mock_network_client.sites.get_all.return_value = [{"id": "default"}]
    setattr(
        getattr(mock_network_client, namespace), method, AsyncMock(side_effect=error)
    )
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.LOADED
    facade = mock_config_entry.runtime_data.coordinator
    assert site_section_available(facade.data, "default", section) is False
    assert owner_available(facade.data, "config") is True


async def test_site_forbidden_at_setup_does_not_start_reauth(
    hass: HomeAssistant,
    *,
    mock_config_entry: MockConfigEntry,
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    mock_local_auth: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A 403 on the devices endpoint retries setup instead of looping reauth."""
    mock_network_client.sites.get_all.return_value = [{"id": "default"}]
    mock_network_client.devices.get_all = AsyncMock(
        side_effect=UniFiAuthenticationError("Forbidden", status_code=403)
    )
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress_by_handler("unifi_insights")


def _set_network(client: MagicMock, behaviour: object) -> None:
    if isinstance(behaviour, Exception):
        client.sites.get_all = AsyncMock(side_effect=behaviour)
    else:
        client.sites.get_all = AsyncMock(return_value=behaviour)


def _set_protect(client: MagicMock, cameras: object, nvr: object = None) -> None:
    if isinstance(cameras, Exception):
        client.cameras.get_all = AsyncMock(side_effect=cameras)
    else:
        client.cameras.get_all = AsyncMock(return_value=cameras)
    if isinstance(nvr, Exception):
        client.nvr.get = AsyncMock(side_effect=nvr)
    else:
        client.nvr.get = AsyncMock(return_value=nvr)


_SITES = [{"id": "default", "name": "Default"}]


@pytest.mark.parametrize(
    ("network", "cameras", "nvr", "expected_state", "protect_loaded", "reauth"),
    [
        # Network works, Protect temporarily failing: retry, don't drop Protect.
        (_SITES, UniFiTimeoutError("t"), None, "setup_retry", None, False),
        (
            _SITES,
            UniFiResponseError("Bad gateway", status_code=502),
            None,
            "setup_retry",
            None,
            False,
        ),
        (
            _SITES,
            [],
            UniFiResponseError("Unavailable", status_code=503),
            "setup_retry",
            None,
            False,
        ),
        # Network-only console: Protect answers without an NVR.
        (
            _SITES,
            [],
            UniFiNotFoundError("NVR not found", status_code=200),
            "loaded",
            False,
            False,
        ),
        # Protect-only console with an NVR and no cameras yet.
        (
            UniFiResponseError("HTML page", status_code=200),
            [],
            MagicMock(id="nvr1"),
            "loaded",
            True,
            False,
        ),
        # Console still starting (seen live on HAOS: Network 5xx while Protect
        # answered 404 used to end in reauth): retry, not reauth.
        (
            UniFiResponseError("Bad gateway", status_code=502),
            UniFiNotFoundError("Not found", status_code=404),
            None,
            "setup_retry",
            None,
            False,
        ),
        (
            UniFiConnectionError("Cannot connect"),
            UniFiNotFoundError("Not found", status_code=404),
            None,
            "setup_retry",
            None,
            False,
        ),
        (
            UniFiAuthenticationError("Unauthorized", status_code=401),
            UniFiTimeoutError("t"),
            None,
            "setup_retry",
            None,
            False,
        ),
        # Nothing usable and nothing rejected (e.g. apps still starting):
        # retry before falling back to reauth.
        (
            UniFiNotFoundError("Not found", status_code=404),
            UniFiNotFoundError("Not found", status_code=404),
            None,
            "setup_retry",
            None,
            False,
        ),
        (
            UniFiResponseError("HTML page", status_code=200),
            UniFiNotFoundError("Not found", status_code=404),
            None,
            "setup_retry",
            None,
            False,
        ),
        # Rejected key everywhere: reauth.
        (
            UniFiAuthenticationError("Unauthorized", status_code=401),
            UniFiAuthenticationError("Unauthorized", status_code=401),
            None,
            "setup_error",
            None,
            True,
        ),
    ],
    ids=[
        "protect-timeout",
        "protect-502",
        "camera-free-nvr-503",
        "network-only",
        "protect-only-camera-free-nvr",
        "console-starting-502",
        "console-starting-connection",
        "network-401-protect-timeout",
        "both-404",
        "html-and-404",
        "both-401",
    ],
)
async def test_setup_classifies_probe_results(
    hass: HomeAssistant,
    *,
    mock_config_entry: MockConfigEntry,
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    mock_local_auth: MagicMock,
    enable_custom_integrations: None,
    network: object,
    cameras: object,
    nvr: object,
    expected_state: str,
    protect_loaded: bool | None,
    reauth: bool,
) -> None:
    """Setup retries temporary failures, reauths rejected keys, loads the rest."""
    _set_network(mock_network_client, network)
    _set_protect(mock_protect_client, cameras, nvr)
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState(expected_state)
    if protect_loaded is not None:
        runtime = mock_config_entry.runtime_data
        assert (runtime.protect_client is not None) is protect_loaded
    flows = hass.config_entries.flow.async_progress_by_handler("unifi_insights")
    assert bool(flows) is reauth


def _probe(status: ProbeStatus, error: Exception | None = None) -> ProbeResult:
    return ProbeResult(status, error)


async def test_setup_probe_retries_then_loads_available_application(
    hass: HomeAssistant,
) -> None:
    """A working app is held back only for a few retries, then loads alone."""
    network = _probe(ProbeStatus.AVAILABLE)
    protect = _probe(ProbeStatus.UNREACHABLE, UniFiTimeoutError("Timed out"))

    for _ in range(SETUP_PROBE_RETRIES):
        with pytest.raises(ConfigEntryNotReady):
            _raise_for_setup_probes(hass, "entry", network, protect)

    _raise_for_setup_probes(hass, "entry", network, protect)
    assert "entry" not in hass.data[_SETUP_PROBE_ATTEMPTS]


async def test_setup_probe_unreachable_console_always_retries(
    hass: HomeAssistant,
) -> None:
    """With nothing usable, a temporary failure keeps retrying."""
    network = _probe(ProbeStatus.UNREACHABLE, UniFiConnectionError("Refused"))
    protect = _probe(ProbeStatus.UNSUPPORTED)

    for _ in range(SETUP_PROBE_RETRIES + 2):
        with pytest.raises(ConfigEntryNotReady):
            _raise_for_setup_probes(hass, "entry", network, protect)


async def test_setup_probe_inconclusive_retries_then_reauths(
    hass: HomeAssistant,
) -> None:
    """Nothing usable and nothing rejected: retry, then reauth, then reset."""
    network = _probe(ProbeStatus.EMPTY)
    protect = _probe(ProbeStatus.UNSUPPORTED, UniFiNotFoundError("x", 404))

    for _ in range(SETUP_PROBE_RETRIES):
        with pytest.raises(ConfigEntryNotReady):
            _raise_for_setup_probes(hass, "entry", network, protect)
    with pytest.raises(ConfigEntryAuthFailed):
        _raise_for_setup_probes(hass, "entry", network, protect)

    # The count starts over, so a later reload retries again.
    with pytest.raises(ConfigEntryNotReady):
        _raise_for_setup_probes(hass, "entry", network, protect)


async def test_setup_probe_rejected_key_reauths_immediately(
    hass: HomeAssistant,
) -> None:
    """A 401 with nothing usable goes straight to reauth and clears the count."""
    hass.data[_SETUP_PROBE_ATTEMPTS] = {"entry": {"inconclusive": 2, "partial": 1}}
    network = _probe(
        ProbeStatus.AUTH_FAILED, UniFiAuthenticationError("x", status_code=401)
    )

    with pytest.raises(ConfigEntryAuthFailed):
        _raise_for_setup_probes(hass, "entry", network, _probe(ProbeStatus.UNSUPPORTED))

    assert "entry" not in hass.data[_SETUP_PROBE_ATTEMPTS]


@pytest.mark.parametrize(
    "then",
    [
        (ProbeStatus.AVAILABLE, ProbeStatus.UNREACHABLE),
        (ProbeStatus.UNSUPPORTED, ProbeStatus.UNSUPPORTED),
    ],
    ids=["then-protect-502", "then-both-404"],
)
async def test_unreachable_console_does_not_use_up_retry_budget(
    hass: HomeAssistant, then: tuple[ProbeStatus, ProbeStatus]
) -> None:
    """Reboot sequence seen live: many 'cannot connect', then partial answers.

    The unlimited retries while nothing answers must not spend the budgets,
    so the first conclusive-but-incomplete answer still gets its retries.
    """
    unreachable = _probe(ProbeStatus.UNREACHABLE, UniFiConnectionError("Refused"))
    for _ in range(SETUP_PROBE_RETRIES + 2):
        with pytest.raises(ConfigEntryNotReady):
            _raise_for_setup_probes(hass, "entry", unreachable, unreachable)

    network = _probe(then[0])
    protect = _probe(then[1], UniFiResponseError("x", status_code=502))
    for _ in range(SETUP_PROBE_RETRIES):
        with pytest.raises(ConfigEntryNotReady):
            _raise_for_setup_probes(hass, "entry", network, protect)


async def test_flapping_console_keeps_partial_budget(hass: HomeAssistant) -> None:
    """Flapping between unreachable and 502 neither resets nor extends the budget."""
    down = _probe(ProbeStatus.UNREACHABLE, UniFiConnectionError("Refused"))
    network = _probe(ProbeStatus.AVAILABLE)
    protect = _probe(
        ProbeStatus.UNREACHABLE, UniFiResponseError("Bad gateway", status_code=502)
    )

    for _ in range(SETUP_PROBE_RETRIES):
        with pytest.raises(ConfigEntryNotReady):
            _raise_for_setup_probes(hass, "entry", network, protect)
        with pytest.raises(ConfigEntryNotReady):
            _raise_for_setup_probes(hass, "entry", down, down)

    # Budget spent by the partial answers only: now it loads with Network.
    _raise_for_setup_probes(hass, "entry", network, protect)


async def test_unload_and_remove_clear_retry_budget(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Unloading or deleting an entry forgets its setup retry budget."""
    attempts = hass.data.setdefault(_SETUP_PROBE_ATTEMPTS, {})
    entry_id = init_integration.entry_id

    attempts[entry_id] = {"inconclusive": 2}
    await hass.config_entries.async_unload(entry_id)
    assert entry_id not in attempts

    attempts[entry_id] = {"partial": 1}
    await hass.config_entries.async_remove(entry_id)
    assert entry_id not in attempts


async def test_setup_entry_innerspace_only_console(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    mock_innerspace_client: MagicMock,
    *,
    mock_local_auth: MagicMock,
    enable_custom_integrations,
) -> None:
    """An InnerSpace-only console loads and closes cleanly."""
    mock_network_client.sites.get_all.return_value = []
    mock_protect_client.cameras.get_all.return_value = []
    mock_protect_client.nvr.get.return_value = None

    mock_innerspace_client.get_project.return_value = InnerSpaceProject(
        project=InnerSpaceProjectIdentity(id="proj-only"),
    )
    mock_innerspace_client.list_floor_plans.return_value = [
        InnerSpaceFloorPlan(id="fp-1", name="Main Floor")
    ]
    mock_innerspace_client.list_inventory.return_value = [
        InnerSpaceInventoryDevice(
            id="inv-only-1",
            name="Unplaced AP",
            model="U6-Pro",
            mac="AA:BB:CC:00:11:22",
        )
    ]

    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.runtime_data.innerspace_coordinator is not None
    assert mock_config_entry.runtime_data.innerspace_client is mock_innerspace_client
    innerspace_data = mock_config_entry.runtime_data.coordinator.data["innerspace"]
    assert "inv-only-1" in innerspace_data["inventory"]

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    mock_innerspace_client.close.assert_awaited()


def test_platforms_include_media_player() -> None:
    """Test that PLATFORMS includes Platform.MEDIA_PLAYER."""
    assert Platform.MEDIA_PLAYER in PLATFORMS


async def test_setup_entry_unused_innerspace_close_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    mock_innerspace_client: MagicMock,
    *,
    mock_local_auth: MagicMock,
    enable_custom_integrations,
) -> None:
    """Test that error closing unused InnerSpace client does not block setup."""
    mock_innerspace_client.close = AsyncMock(
        side_effect=Exception("InnerSpace close failed")
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state == ConfigEntryState.LOADED
    mock_innerspace_client.close.assert_awaited_once()


async def test_unload_entry_innerspace_close_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    mock_innerspace_client: MagicMock,
    *,
    mock_local_auth: MagicMock,
    enable_custom_integrations,
) -> None:
    """Test unload entry handles innerspace client close error gracefully."""
    mock_innerspace_client.get_project.return_value = InnerSpaceProject(
        project=InnerSpaceProjectIdentity(id="proj-1"),
    )
    mock_innerspace_client.list_floor_plans.return_value = [
        InnerSpaceFloorPlan(id="fp-1", name="Main")
    ]
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state == ConfigEntryState.LOADED

    mock_network_client.close.reset_mock()
    mock_innerspace_client.close = AsyncMock(
        side_effect=Exception("InnerSpace close error")
    )
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state == ConfigEntryState.NOT_LOADED
    mock_innerspace_client.close.assert_awaited_once()
    mock_network_client.close.assert_awaited_once()


async def test_unload_entry_platform_unload_fails_keeps_clients_open(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """When platform unload returns false, assert that clients stay open."""
    runtime_data = init_integration.runtime_data
    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=False
    ):
        result = await async_unload_entry(hass, init_integration)
        assert result is False
        assert not runtime_data.network_client.close.called
        assert not runtime_data.protect_client.close.called

    # Clean up entry cleanly
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()


# --- Carrier Fabric lifecycle ------------------------------------------------

# Tests for UniFi Carrier Fabric integration setup, unload, and lifecycle.


@pytest.fixture
def carrier_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Create a mock Carrier Fabric config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Carrier Fabric",
        unique_id="carrier_org_test123",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            CONF_API_KEY: "isp_secret_key",
            CONF_CARRIER_ORG_ID: "org_test123",
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_carrier_fabric_setup_and_unload_success(
    hass: HomeAssistant, carrier_entry: MockConfigEntry
) -> None:
    """Test full setup, platform forwarding, and unload sequence."""
    mock_client = MagicMock()
    mock_client.close = AsyncMock()
    mock_client.service_plans = MagicMock()
    mock_client.service_plans.get_all = AsyncMock(return_value=[])
    mock_client.subscribers = MagicMock()
    mock_client.subscribers.get_all = AsyncMock(return_value=[])

    with (
        patch(
            "custom_components.unifi_insights.UniFiCarrierFabricClient",
            return_value=mock_client,
        ),
        patch(
            "custom_components.unifi_insights.async_probe_carrier_fabric",
            return_value=ProbeResult(
                status=ProbeStatus.AVAILABLE,
                org_id="org_test123",
            ),
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new_callable=AsyncMock,
        ) as mock_forward,
    ):
        assert await hass.config_entries.async_setup(carrier_entry.entry_id)
        await hass.async_block_till_done()

        assert carrier_entry.state == ConfigEntryState.LOADED
        assert isinstance(carrier_entry.runtime_data, CarrierFabricData)
        assert carrier_entry.runtime_data.client is mock_client

        # Verify only Platform.SENSOR was forwarded
        mock_forward.assert_called_once_with(carrier_entry, [Platform.SENSOR])

        # Test unload
        assert await hass.config_entries.async_unload(carrier_entry.entry_id)
        await hass.async_block_till_done()

        assert carrier_entry.state == ConfigEntryState.NOT_LOADED
        mock_client.close.assert_awaited_once()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_carrier_fabric_setup_probe_auth_failed(
    hass: HomeAssistant, carrier_entry: MockConfigEntry
) -> None:
    """Test setup raises ConfigEntryAuthFailed when probe reports AUTH_FAILED."""
    mock_client = MagicMock()
    mock_client.close = AsyncMock()

    with (
        patch(
            "custom_components.unifi_insights.UniFiCarrierFabricClient",
            return_value=mock_client,
        ),
        patch(
            "custom_components.unifi_insights.async_probe_carrier_fabric",
            return_value=ProbeResult(
                status=ProbeStatus.AUTH_FAILED,
                error=Exception("401 unauthorized"),
            ),
        ),
    ):
        await hass.config_entries.async_setup(carrier_entry.entry_id)
        await hass.async_block_till_done()

        assert carrier_entry.state == ConfigEntryState.SETUP_ERROR
        # ConfigEntryAuthFailed, not any setup error: reauthentication starts.
        assert any(
            flow["context"].get("source") == "reauth"
            and flow["context"].get("entry_id") == carrier_entry.entry_id
            for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_carrier_fabric_setup_probe_unreachable(
    hass: HomeAssistant, carrier_entry: MockConfigEntry
) -> None:
    """Test setup raises ConfigEntryNotReady when probe reports UNREACHABLE."""
    mock_client = MagicMock()
    mock_client.close = AsyncMock()

    with (
        patch(
            "custom_components.unifi_insights.UniFiCarrierFabricClient",
            return_value=mock_client,
        ),
        patch(
            "custom_components.unifi_insights.async_probe_carrier_fabric",
            return_value=ProbeResult(
                status=ProbeStatus.UNREACHABLE,
                error=Exception("Connection refused"),
            ),
        ),
    ):
        await hass.config_entries.async_setup(carrier_entry.entry_id)
        await hass.async_block_till_done()

        assert carrier_entry.state == ConfigEntryState.SETUP_RETRY


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_carrier_fabric_setup_first_refresh_failure_closes_client(
    hass, carrier_entry
):
    """Test client is closed when coordinator first refresh fails during setup."""
    mock_client = MagicMock()
    mock_client.close = AsyncMock()
    mock_client.service_plans = MagicMock()
    mock_client.service_plans.get_all = AsyncMock(
        side_effect=Exception("Failed to load plans")
    )
    mock_client.subscribers = MagicMock()
    mock_client.subscribers.get_all = AsyncMock(return_value=[])

    with (
        patch(
            "custom_components.unifi_insights.UniFiCarrierFabricClient",
            return_value=mock_client,
        ),
        patch(
            "custom_components.unifi_insights.async_probe_carrier_fabric",
            return_value=ProbeResult(
                status=ProbeStatus.AVAILABLE,
                org_id="org_test123",
            ),
        ),
    ):
        await hass.config_entries.async_setup(carrier_entry.entry_id)
        await hass.async_block_till_done()

        assert carrier_entry.state == ConfigEntryState.SETUP_RETRY
        mock_client.close.assert_awaited()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_carrier_fabric_options_update_listener(
    hass: HomeAssistant, carrier_entry: MockConfigEntry
) -> None:
    """Test updating options triggers reload of entry."""
    mock_client = MagicMock()
    mock_client.close = AsyncMock()
    mock_client.service_plans = MagicMock()
    mock_client.service_plans.get_all = AsyncMock(return_value=[])
    mock_client.subscribers = MagicMock()
    mock_client.subscribers.get_all = AsyncMock(return_value=[])

    with (
        patch(
            "custom_components.unifi_insights.UniFiCarrierFabricClient",
            return_value=mock_client,
        ),
        patch(
            "custom_components.unifi_insights.async_probe_carrier_fabric",
            return_value=ProbeResult(
                status=ProbeStatus.AVAILABLE,
                org_id="org_test123",
            ),
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
    ):
        assert await hass.config_entries.async_setup(carrier_entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            hass.config_entries, "async_reload", new_callable=AsyncMock
        ) as mock_reload:
            hass.config_entries.async_update_entry(
                carrier_entry,
                options={CONF_TRACK_SUBSCRIBERS: True},
            )
            await hass.async_block_till_done()
            mock_reload.assert_called_once_with(carrier_entry.entry_id)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_carrier_fabric_device_removal(
    hass: HomeAssistant, carrier_entry: MockConfigEntry
) -> None:
    """Test device removal rules for Carrier Fabric."""
    mock_coordinator = MagicMock()
    mock_coordinator.data = {
        "subscribers": {
            "sub_active_1": {"id": "sub_active_1", "name": "Active 1"},
        }
    }
    mock_client = MagicMock()
    mock_client.close = AsyncMock()

    carrier_entry.runtime_data = CarrierFabricData(
        client=mock_client,
        coordinator=mock_coordinator,
    )

    # 1. Organization device (refuse removal)
    dev_reg = dr.async_get(hass)
    org_device = dev_reg.async_get_or_create(
        config_entry_id=carrier_entry.entry_id,
        identifiers={(DOMAIN, require_not_none(carrier_entry.unique_id))},
    )
    assert not await async_remove_config_entry_device(hass, carrier_entry, org_device)

    # 2. Subscriber device still in coordinator data (refuse removal)
    active_sub_device = dev_reg.async_get_or_create(
        config_entry_id=carrier_entry.entry_id,
        identifiers={(DOMAIN, "carrier_subscriber_sub_active_1")},
    )
    assert not await async_remove_config_entry_device(
        hass, carrier_entry, active_sub_device
    )

    # 3. Subscriber device no longer in coordinator data (allow removal)
    stale_sub_device = dev_reg.async_get_or_create(
        config_entry_id=carrier_entry.entry_id,
        identifiers={(DOMAIN, "carrier_subscriber_sub_removed_2")},
    )
    assert await async_remove_config_entry_device(hass, carrier_entry, stale_sub_device)

    # 4. Unknown device identifier format (refuse removal)
    other_device = dev_reg.async_get_or_create(
        config_entry_id=carrier_entry.entry_id,
        identifiers={(DOMAIN, "some_unknown_device_id")},
    )
    assert not await async_remove_config_entry_device(hass, carrier_entry, other_device)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_carrier_fabric_device_removal_ignores_other_integrations_identifiers(
    hass, carrier_entry
):
    """Identifiers of other integrations on a shared device are never ours to judge."""
    mock_coordinator = MagicMock()
    mock_coordinator.data = {"subscribers": {}}
    carrier_entry.runtime_data = CarrierFabricData(
        client=MagicMock(), coordinator=mock_coordinator
    )
    dev_reg = dr.async_get(hass)

    # A device only another integration identifies is refused, not removed.
    foreign_only = dev_reg.async_get_or_create(
        config_entry_id=carrier_entry.entry_id,
        identifiers={("other_integration", "carrier_subscriber_sub_gone")},
    )
    assert not await async_remove_config_entry_device(hass, carrier_entry, foreign_only)

    # A stale subscriber device that another integration also identifies is still
    # removable, whichever identifier is looked at first.
    shared_stale = dev_reg.async_get_or_create(
        config_entry_id=carrier_entry.entry_id,
        identifiers={
            ("other_integration", "some_serial"),
            (DOMAIN, "carrier_subscriber_sub_gone"),
        },
    )
    assert await async_remove_config_entry_device(hass, carrier_entry, shared_stale)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_carrier_fabric_setup_backfills_missing_org_id_from_probe(
    hass,
):
    """An entry stored without an organisation learns it from the setup probe."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Carrier Fabric",
        unique_id="carrier_key_abc123",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            CONF_API_KEY: "isp_secret_key",
            CONF_CARRIER_ORG_ID: None,
        },
    )
    entry.add_to_hass(hass)

    mock_client = MagicMock()
    mock_client.close = AsyncMock()
    mock_client.service_plans = MagicMock()
    mock_client.service_plans.get_all = AsyncMock(return_value=[])
    mock_client.subscribers = MagicMock()
    mock_client.subscribers.get_all = AsyncMock(return_value=[])

    with (
        patch(
            "custom_components.unifi_insights.UniFiCarrierFabricClient",
            return_value=mock_client,
        ),
        patch(
            "custom_components.unifi_insights.async_probe_carrier_fabric",
            return_value=ProbeResult(
                status=ProbeStatus.AVAILABLE,
                org_id="org_discovered",
            ),
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state == ConfigEntryState.LOADED
    assert entry.data[CONF_CARRIER_ORG_ID] == "org_discovered"
    assert entry.data[CONF_API_KEY] == "isp_secret_key"
    assert entry.data[CONF_CONNECTION_TYPE] == CONNECTION_TYPE_CARRIER_FABRIC
    # The coordinator resolves the same organisation from the updated entry.
    assert entry.runtime_data.coordinator.data["org_id"] == "org_discovered"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_carrier_fabric_setup_probe_unsupported(
    hass: HomeAssistant, carrier_entry: MockConfigEntry
) -> None:
    """Test setup raises ConfigEntryNotReady when probe reports UNSUPPORTED."""
    mock_client = MagicMock()
    mock_client.close = AsyncMock()

    with (
        patch(
            "custom_components.unifi_insights.UniFiCarrierFabricClient",
            return_value=mock_client,
        ),
        patch(
            "custom_components.unifi_insights.async_probe_carrier_fabric",
            return_value=ProbeResult(
                status=ProbeStatus.UNSUPPORTED,
                error=Exception("Unsupported API endpoint"),
            ),
        ),
    ):
        await hass.config_entries.async_setup(carrier_entry.entry_id)
        await hass.async_block_till_done()

        assert carrier_entry.state == ConfigEntryState.SETUP_RETRY
        # A 4xx is not an outage: the retry reason names the real probe status.
        assert carrier_entry.reason is not None
        assert "probe failed (unsupported)" in carrier_entry.reason
        assert "unreachable" not in carrier_entry.reason


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_carrier_fabric_unload_with_connection_type_in_entry_data(
    hass, carrier_entry
):
    """Test unload uses CARRIER_FABRIC_PLATFORMS even when runtime_data is None (B3)."""
    carrier_entry.runtime_data = None
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_unload:
        assert await async_unload_entry(hass, carrier_entry)
        mock_unload.assert_called_once_with(carrier_entry, CARRIER_FABRIC_PLATFORMS)


# --- Mobility setup ----------------------------------------------------------

# Tests for setting up UniFi Mobility on remote config entries.


def _remote_entry(entry_id: str, console_id: str = "console") -> MockConfigEntry:
    """Return a remote (cloud) entry using the shared test API key."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": "remote",
            "console_id": console_id,
            "api_key": "cloud-key",
        },
        unique_id=console_id,
        entry_id=entry_id,
    )


async def _setup_mobility(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up an entry and wait for its background Mobility refresh."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.usefixtures(
    "mock_network_client",
    "mock_protect_client",
    "enable_custom_integrations",
    "mock_site_manager_client",
)
async def test_key_without_mobility_scope_loads_without_reauth(
    hass: HomeAssistant, mock_mobility_client: MagicMock
) -> None:
    """A 403 from Mobility leaves the remote entry working and asks nothing."""
    mock_mobility_client.list_workspaces.side_effect = UniFiAuthenticationError(
        "Access forbidden", status_code=403
    )
    entry = _remote_entry("01REMOTE")

    await _setup_mobility(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data.mobility_coordinator
    assert coordinator is not None
    assert coordinator.data["access"] == "denied"
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    mock_mobility_client.close.assert_awaited_once()


@pytest.mark.usefixtures(
    "mock_network_client",
    "mock_protect_client",
    "enable_custom_integrations",
    "mock_site_manager_client",
)
async def test_local_entry_never_uses_mobility(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_mobility_client: MagicMock,
) -> None:
    """Console-local keys cannot reach the cloud Mobility API."""
    await hass.async_block_till_done(wait_background_tasks=True)

    assert init_integration.runtime_data.mobility_coordinator is None
    mock_mobility_client.list_workspaces.assert_not_awaited()


@pytest.mark.usefixtures(
    "mock_network_client",
    "mock_protect_client",
    "enable_custom_integrations",
    "mock_site_manager_client",
)
async def test_only_one_remote_entry_per_key_polls_mobility(
    hass: HomeAssistant, mock_mobility_client: MagicMock
) -> None:
    """Two consoles behind one cloud key share a single Mobility poller."""
    serve_spec_examples(mock_mobility_client)
    owner = _remote_entry("01A", "console-a")
    second = _remote_entry("01B", "console-b")
    second.add_to_hass(hass)

    # Setting up the integration loads every entry of the domain.
    await _setup_mobility(hass, owner)

    assert second.state is ConfigEntryState.LOADED
    assert owner.runtime_data.mobility_coordinator is not None
    assert second.runtime_data.mobility_coordinator is None
    mock_mobility_client.list_workspaces.assert_awaited_once()


def _device(identifier: str) -> Any:
    """Return a device registry entry stand-in with one identifier."""
    return MagicMock(identifiers={(DOMAIN, identifier)})


@pytest.mark.usefixtures(
    "mock_network_client",
    "mock_protect_client",
    "enable_custom_integrations",
    "mock_site_manager_client",
)
async def test_mobility_devices_are_removable_only_once_confirmed_gone(
    hass: HomeAssistant, mock_mobility_client: MagicMock
) -> None:
    """A router or workspace can be deleted once Mobility no longer reports it."""
    serve_spec_examples(mock_mobility_client)
    entry = _remote_entry("01REMOTE")
    await _setup_mobility(hass, entry)
    coordinator = entry.runtime_data.mobility_coordinator
    gone_router = _device("mobility_00000000-0000-0000-0000-000000000000")
    gone_workspace = _device("mobility_workspace_00000000-0000-0000-0000-00000000")

    assert not await async_remove_config_entry_device(
        hass, entry, _device(f"mobility_{OFFICE_ROUTER_ID}")
    )
    assert not await async_remove_config_entry_device(
        hass, entry, _device(f"mobility_workspace_{WORKSPACE_ID}")
    )
    assert await async_remove_config_entry_device(hass, entry, gone_router)
    assert await async_remove_config_entry_device(hass, entry, gone_workspace)

    # A failed poll proves nothing about what Mobility still has.
    coordinator.last_update_success = False
    assert not await async_remove_config_entry_device(hass, entry, gone_router)

    # Nor does a coordinator that has not answered yet.
    coordinator.last_update_success = True
    coordinator.data = {**coordinator.data, "access": "unknown", "devices": {}}
    assert not await async_remove_config_entry_device(hass, entry, gone_router)


@pytest.mark.usefixtures(
    "mock_network_client",
    "mock_protect_client",
    "enable_custom_integrations",
    "mock_site_manager_client",
)
async def test_mobility_devices_are_removable_from_a_non_owner_entry(
    hass: HomeAssistant,
) -> None:
    """Devices left behind on an entry that no longer owns Mobility can go."""
    owner = _remote_entry("01A", "console-a")
    second = _remote_entry("01B", "console-b")
    second.add_to_hass(hass)
    await _setup_mobility(hass, owner)

    assert second.runtime_data.mobility_coordinator is None
    assert await async_remove_config_entry_device(
        hass, second, _device(f"mobility_{OFFICE_ROUTER_ID}")
    )


def _mobility_owners(hass: HomeAssistant) -> dict[str, list[str]]:
    """Map each Mobility unique id to the entries its registry rows belong to."""
    owners: dict[str, list[str]] = {}
    for entity in er.async_get(hass).entities.values():
        if entity.platform == DOMAIN and entity.unique_id.startswith("mobility_"):
            owners.setdefault(entity.unique_id, []).append(entity.config_entry_id or "")
    return owners


@pytest.mark.usefixtures(
    "mock_network_client",
    "mock_protect_client",
    "enable_custom_integrations",
    "mock_site_manager_client",
)
async def test_re_enabled_older_entry_does_not_duplicate_mobility(
    hass: HomeAssistant,
    mock_mobility_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The entry already polling Mobility keeps it when an older one comes back."""
    serve_spec_examples(mock_mobility_client)
    older = _remote_entry("01A", "console-a")
    older.disabled_by = ConfigEntryDisabler.USER
    newer = _remote_entry("01B", "console-b")
    older.add_to_hass(hass)
    await _setup_mobility(hass, newer)
    assert newer.runtime_data.mobility_coordinator is not None

    await hass.config_entries.async_set_disabled_by(older.entry_id, None)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert older.state is ConfigEntryState.LOADED
    assert older.runtime_data.mobility_coordinator is None
    assert newer.runtime_data.mobility_coordinator is not None
    assert "does not generate unique IDs" not in caplog.text
    assert mock_mobility_client.list_workspaces.await_count == 1


@pytest.mark.usefixtures(
    "mock_network_client",
    "mock_protect_client",
    "enable_custom_integrations",
    "mock_site_manager_client",
)
@pytest.mark.parametrize("leave", ["disable", "remove"])
async def test_mobility_moves_to_the_next_entry_when_the_owner_leaves(
    hass: HomeAssistant,
    mock_mobility_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
    leave: str,
) -> None:
    """Disabling or deleting the owner hands Mobility to the next entry."""
    serve_spec_examples(mock_mobility_client)
    owner = _remote_entry("01A", "console-a")
    successor = _remote_entry("01B", "console-b")
    successor.add_to_hass(hass)
    await _setup_mobility(hass, owner)
    owned = _mobility_owners(hass)
    assert owned
    assert {tuple(entries) for entries in owned.values()} == {(owner.entry_id,)}

    if leave == "disable":
        await hass.config_entries.async_set_disabled_by(
            owner.entry_id, ConfigEntryDisabler.USER
        )
    else:
        await hass.config_entries.async_remove(owner.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert successor.state is ConfigEntryState.LOADED
    assert successor.runtime_data.mobility_coordinator is not None
    moved = _mobility_owners(hass)
    assert set(moved) == set(owned)
    assert {tuple(entries) for entries in moved.values()} == {(successor.entry_id,)}
    registry = er.async_get(hass)
    router_clients = registry.async_get_entity_id(
        "sensor", DOMAIN, f"mobility_{OFFICE_ROUTER_ID}_clients"
    )
    assert router_clients is not None
    assert require_not_none(registry.async_get(router_clients)).disabled_by is None
    assert require_not_none(hass.states.get(router_clients)).state == "5"
    assert "does not generate unique IDs" not in caplog.text


@pytest.mark.usefixtures(
    "mock_network_client",
    "mock_protect_client",
    "enable_custom_integrations",
    "mock_site_manager_client",
)
async def test_owner_reload_keeps_mobility_and_leaves_siblings_alone(
    hass: HomeAssistant, mock_mobility_client: MagicMock
) -> None:
    """Reloading the owner (an options change) does not reload other entries."""
    serve_spec_examples(mock_mobility_client)
    owner = _remote_entry("01A", "console-a")
    sibling = _remote_entry("01B", "console-b")
    sibling.add_to_hass(hass)
    await _setup_mobility(hass, owner)
    sibling_data = sibling.runtime_data

    assert await hass.config_entries.async_reload(owner.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert owner.runtime_data.mobility_coordinator is not None
    assert sibling.runtime_data is sibling_data
    assert sibling.runtime_data.mobility_coordinator is None
