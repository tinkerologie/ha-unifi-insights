# Copyright 2026 UniFi Insights contributors
"""Verify optional Access failures do not interrupt other console applications."""

import asyncio
import logging
import time
from datetime import timedelta
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_VERIFY_SSL
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiNotFoundError,
    UniFiResponseError,
    UniFiValidationError,
)
from unifi_official_api.access.models import AccessWebhookEndpoint

from custom_components.unifi_insights import (
    _async_setup_access,
    async_remove_entry,
    async_unload_entry,
)
from custom_components.unifi_insights.access_entity import access_unique_id
from custom_components.unifi_insights.console_identity import SiteEntityIdentityError
from custom_components.unifi_insights.const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCESS_WEBHOOK,
    CONF_ACCESS_WEBHOOK_ID,
    CONF_ACCESS_WEBHOOK_SECRET,
    CONF_CONSOLE_ID,
    DOMAIN,
    SCAN_INTERVAL_ACCESS,
)
from custom_components.unifi_insights.coordinators.access import (
    ACCESS_DEBOUNCE_SECONDS,
    UnifiInsightsAccessCoordinator,
)
from tests.test_access_coordinator import DEVICES, DOORS, NAMESPACE, _client
from tests.test_access_webhook import _sign, _unlock_event


@pytest.mark.parametrize(
    "error",
    [
        UniFiAuthenticationError("Rejected", status_code=401),
        UniFiAuthenticationError("Denied", status_code=403),
        UniFiNotFoundError("Absent", status_code=404),
        UniFiValidationError("Unexpected answer"),
    ],
)
async def test_access_permanent_probe_failure_preserves_existing_applications(
    *,
    hass,
    mock_config_entry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    enable_custom_integrations,
    error,
):
    """A rejected, unsupported or unknown Access application stays closed."""
    client = MagicMock()
    client.doors.get_all = AsyncMock(side_effect=error)
    client.close = AsyncMock()
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, CONF_ACCESS_TOKEN: "test-access-token"},
    )
    with patch(
        "custom_components.unifi_insights.UniFiAccessClient", return_value=client
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_config_entry.runtime_data.access_coordinator is None
    assert mock_config_entry.runtime_data.protect_coordinator is not None
    assert mock_config_entry.runtime_data.network_client is mock_network_client
    client.close.assert_awaited_once()


async def test_access_is_not_created_without_a_token(
    *,
    hass,
    mock_config_entry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    enable_custom_integrations,
):
    """Existing entries keep their behavior and do not open an Access connection."""
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.unifi_insights.UniFiAccessClient") as client:
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    client.assert_not_called()
    assert mock_config_entry.runtime_data.access_coordinator is None
    assert mock_config_entry.runtime_data.access_client is None


def _add_access_entry(hass, entry, *, console_id: bool = True) -> None:
    """Give the entry an Access token, and a console namespace unless told not to."""
    entry.add_to_hass(hass)
    data = {**entry.data, CONF_ACCESS_TOKEN: "test-access-token"}
    if console_id:
        data[CONF_CONSOLE_ID] = NAMESPACE
    hass.config_entries.async_update_entry(entry, data=data)


async def _setup_access_directly(hass, entry, client):
    """Run the Access setup step with a client double and no platform forwarding."""
    with patch(
        "custom_components.unifi_insights.UniFiAccessClient", return_value=client
    ):
        return await _async_setup_access(
            hass, entry, async_get_clientsession(hass), verify_ssl=False
        )


@pytest.mark.parametrize(
    "doors_side_effect",
    [
        # The probe itself is unreachable.
        UniFiConnectionError("Unavailable"),
        UniFiResponseError("Busy", status_code=503),
        # The probe answers, then the first refresh loses the console.
        [DOORS, UniFiConnectionError("Unavailable")],
    ],
    ids=["probe-unreachable", "probe-server-error", "first-fetch-unreachable"],
)
async def test_transient_access_outage_keeps_coordinator_for_recovery(
    hass, mock_config_entry, doors_side_effect
):
    """An outage at startup keeps the failed coordinator and its client open."""
    _add_access_entry(hass, mock_config_entry)
    client = _client(doors=DOORS, devices=DEVICES)
    client.doors.get_all.side_effect = doors_side_effect

    client_out, coordinator = await _setup_access_directly(
        hass, mock_config_entry, client
    )

    assert client_out is client
    assert coordinator is not None
    assert not coordinator.last_update_success
    assert coordinator.data == {"doors": {}, "devices": {}, "emergency": None}
    client.close.assert_not_awaited()


async def test_access_outage_without_console_namespace_closes_client(
    hass, mock_config_entry
):
    """Without a namespace no listener can poll, so an outage closes Access."""
    _add_access_entry(hass, mock_config_entry, console_id=False)
    client = _client(doors=DOORS, devices=DEVICES)
    client.doors.get_all.side_effect = UniFiConnectionError("Unavailable")

    assert await _setup_access_directly(hass, mock_config_entry, client) == (
        None,
        None,
    )
    client.close.assert_awaited_once()


async def test_access_token_rejected_after_probe_closes_client(hass, mock_config_entry):
    """A token rejected by the first read is permanent, so Access is closed."""
    _add_access_entry(hass, mock_config_entry)
    client = _client(doors=DOORS, devices=DEVICES)
    client.doors.get_all.side_effect = [
        DOORS,
        UniFiAuthenticationError("Rejected", status_code=401),
    ]

    assert await _setup_access_directly(hass, mock_config_entry, client) == (
        None,
        None,
    )
    client.close.assert_awaited_once()


async def test_empty_access_console_is_retained_without_entities(
    hass, mock_config_entry
):
    """An empty console is a healthy answer: the coordinator is kept, nothing errors."""
    _add_access_entry(hass, mock_config_entry)
    client = _client(doors=[], devices=[])

    client_out, coordinator = await _setup_access_directly(
        hass, mock_config_entry, client
    )

    assert client_out is client
    assert coordinator is not None
    assert coordinator.last_update_success
    assert coordinator.data["doors"] == {}
    client.close.assert_not_awaited()


async def test_unreachable_access_recovers_without_reload(
    *,
    hass,
    mock_config_entry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    enable_custom_integrations,
):
    """Real HA: an outage at startup, then a good poll, adds entities without reload."""
    _add_access_entry(hass, mock_config_entry)
    client = _client(doors=DOORS, devices=DEVICES)
    client.doors.get_all.side_effect = UniFiConnectionError("Unavailable")
    unique_id = access_unique_id(NAMESPACE, "door-plain", "lock")

    async def _run_socket(*_args, **_kwargs):
        await asyncio.Event().wait()

    with (
        patch(
            "custom_components.unifi_insights.UniFiAccessClient", return_value=client
        ),
        patch(
            "custom_components.unifi_insights.coordinators.access."
            "AccessNotificationSocket.run",
            side_effect=_run_socket,
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        data = mock_config_entry.runtime_data
        coordinator = data.access_coordinator
        assert mock_config_entry.state is ConfigEntryState.LOADED
        assert coordinator is not None
        assert not coordinator.last_update_success
        assert (
            async_get_entity_registry(hass).async_get_entity_id(
                "lock", DOMAIN, unique_id
            )
            is None
        )
        task = coordinator._ws_task
        assert task is not None
        client.close.assert_not_awaited()

        client.doors.get_all.side_effect = None
        async_fire_time_changed(
            hass, dt_util.utcnow() + SCAN_INTERVAL_ACCESS + timedelta(seconds=1)
        )
        await hass.async_block_till_done()

        assert coordinator.last_update_success
        assert mock_config_entry.runtime_data.access_coordinator is coordinator
        assert mock_config_entry.state is ConfigEntryState.LOADED
        assert mock_config_entry.runtime_data.network_client is mock_network_client
        assert hass.config_entries.flow.async_progress() == []
        entity_id = async_get_entity_registry(hass).async_get_entity_id(
            "lock", DOMAIN, unique_id
        )
        assert entity_id is not None
        assert hass.states.get(entity_id).state == "unlocked"

        client.doors.get_all.side_effect = UniFiConnectionError("Unavailable")
        async_fire_time_changed(
            hass,
            dt_util.utcnow() + SCAN_INTERVAL_ACCESS * 2 + timedelta(seconds=2),
        )
        await hass.async_block_till_done()

        assert not coordinator.last_update_success
        assert hass.states.get(entity_id).state == "unavailable"

        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert task.cancelled()
    client.close.assert_awaited()


async def test_unload_during_access_outage_releases_socket_and_client(
    *,
    hass,
    mock_config_entry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    enable_custom_integrations,
):
    """Unloading during an outage cancels the socket and closes the client."""
    _add_access_entry(hass, mock_config_entry)
    client = _client(doors=DOORS, devices=DEVICES)
    client.doors.get_all.side_effect = UniFiConnectionError("Unavailable")

    async def _run_socket(*_args, **_kwargs):
        await asyncio.Event().wait()

    with (
        patch(
            "custom_components.unifi_insights.UniFiAccessClient", return_value=client
        ),
        patch(
            "custom_components.unifi_insights.coordinators.access."
            "AccessNotificationSocket.run",
            side_effect=_run_socket,
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        task = mock_config_entry.runtime_data.access_coordinator._ws_task
        assert task is not None
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert task.cancelled()
    client.close.assert_awaited()


async def test_unreachable_access_with_webhook_requested_stays_inactive(
    *,
    hass,
    mock_config_entry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    enable_custom_integrations,
):
    """The one-shot webhook setup fails cleanly in an outage, with no receiver."""
    _add_access_entry(hass, mock_config_entry)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, CONF_ACCESS_WEBHOOK: True},
    )
    client = _client(doors=DOORS, devices=DEVICES)
    client.doors.get_all.side_effect = UniFiConnectionError("Unavailable")
    client.webhooks.get_all = AsyncMock(side_effect=UniFiConnectionError("Unavailable"))
    client.webhooks.create = AsyncMock(side_effect=UniFiConnectionError("Unavailable"))
    client.webhooks.delete = AsyncMock()

    async def _run_socket(*_args, **_kwargs):
        await asyncio.Event().wait()

    with (
        patch(
            "custom_components.unifi_insights.UniFiAccessClient", return_value=client
        ),
        patch(
            "custom_components.unifi_insights.access_webhook.webhook.async_generate_url",
            return_value="http://ha.test.local:8123/api/webhook/test-outage",
        ),
        patch(
            "custom_components.unifi_insights.coordinators.access."
            "AccessNotificationSocket.run",
            side_effect=_run_socket,
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = mock_config_entry.runtime_data.access_coordinator
        assert mock_config_entry.state is ConfigEntryState.LOADED
        assert coordinator is not None
        assert not coordinator.webhook_active
        assert mock_config_entry.data[CONF_ACCESS_WEBHOOK_ID] not in hass.data.get(
            webhook.DOMAIN, {}
        )
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()


async def test_access_close_failure_does_not_block_other_applications(
    *,
    hass,
    mock_config_entry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    enable_custom_integrations,
):
    """Even an Access teardown error cannot prevent Network and Protect setup."""
    client = MagicMock()
    client.doors.get_all = AsyncMock(side_effect=UniFiConnectionError("Unavailable"))
    client.close = AsyncMock(side_effect=RuntimeError("Close failed"))
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, CONF_ACCESS_TOKEN: "test-access-token"},
    )
    with patch(
        "custom_components.unifi_insights.UniFiAccessClient", return_value=client
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED


async def test_invalid_saved_access_settings_preserve_existing_applications(
    *,
    hass,
    mock_config_entry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    enable_custom_integrations,
):
    """Client validation errors remain isolated, including before a probe."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, CONF_ACCESS_TOKEN: "invalid-stored-token"},
    )
    with patch(
        "custom_components.unifi_insights.UniFiAccessClient",
        side_effect=UniFiValidationError("Invalid saved settings"),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    assert mock_config_entry.runtime_data.access_coordinator is None
    assert mock_config_entry.runtime_data.protect_coordinator is not None


@pytest.mark.parametrize("forward_failure", [False, True])
async def test_access_resources_follow_the_entry_lifecycle(
    *,
    hass,
    mock_config_entry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    enable_custom_integrations,
    forward_failure,
):
    """Real HA setup, unload and failed forwarding release optional resources."""
    client = _client(doors=DOORS, devices=DEVICES)
    client.webhooks.get_all = AsyncMock(return_value=[])
    client.webhooks.create = AsyncMock(
        return_value=AccessWebhookEndpoint.model_validate(
            {"id": "endpoint-lifecycle", "secret": "test-lifecycle-signing-secret"}
        )
    )
    client.webhooks.delete = AsyncMock()
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            CONF_ACCESS_TOKEN: "test-access-token",
            CONF_CONSOLE_ID: NAMESPACE,
        },
        options={**mock_config_entry.options, CONF_ACCESS_WEBHOOK: True},
    )

    async def _run_socket(*_args, **_kwargs):
        await asyncio.Event().wait()

    shutdowns: list[UnifiInsightsAccessCoordinator] = []
    original_shutdown = UnifiInsightsAccessCoordinator.async_shutdown

    async def _record_shutdown(self: UnifiInsightsAccessCoordinator) -> None:
        shutdowns.append(self)
        await original_shutdown(self)

    with (
        patch(
            "custom_components.unifi_insights.UniFiAccessClient", return_value=client
        ),
        patch.object(
            UnifiInsightsAccessCoordinator, "async_shutdown", _record_shutdown
        ),
        patch(
            "custom_components.unifi_insights.access_webhook.webhook.async_generate_url",
            return_value="http://ha.test.local:8123/api/webhook/test-lifecycle",
        ),
        patch(
            "custom_components.unifi_insights.coordinators.access."
            "AccessNotificationSocket.run",
            side_effect=_run_socket,
        ),
    ):
        if forward_failure:
            with patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                side_effect=RuntimeError("Platform forwarding failed"),
            ):
                assert not await hass.config_entries.async_setup(
                    mock_config_entry.entry_id
                )
            await hass.async_block_till_done()
            assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
            client.close.assert_awaited_once()
            # The ordered teardown and HA's base on_unload registration both call
            # async_shutdown, so a failed setup always reaches it with the flag set.
            assert len(shutdowns) == 2
            assert all(shutdown._shutdown_requested for shutdown in shutdowns)
        else:
            assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
            await hass.async_block_till_done()
            data = mock_config_entry.runtime_data
            assert data.access_client is client
            assert data.access_coordinator.webhook_active
            task = data.access_coordinator._ws_task
            assert task is not None
            assert not task.done()
            with patch.object(
                hass.config_entries, "async_unload_platforms", return_value=False
            ):
                assert not await async_unload_entry(hass, mock_config_entry)
            assert not task.done()
            assert data.access_coordinator.webhook_active
            client.close.assert_not_awaited()
            entity_id = async_get_entity_registry(hass).async_get_entity_id(
                "lock",
                DOMAIN,
                access_unique_id(NAMESPACE, "door-plain", "lock"),
            )
            assert entity_id is not None
            assert hass.states.get(entity_id).state == "unlocked"
            client.doors.unlock.side_effect = UniFiAuthenticationError(
                "Rejected", status_code=401
            )
            with pytest.raises(HomeAssistantError):
                await data.access_coordinator.async_unlock_door("door-plain")
            await hass.async_block_till_done()
            assert hass.states.get(entity_id).state == "unavailable"
            assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
            await hass.async_block_till_done()
            assert task.cancelled()
            assert not data.access_coordinator.webhook_active
            assert client.close.await_count >= 1

    assert (
        mock_config_entry.data[CONF_ACCESS_WEBHOOK_ID] not in hass.data[webhook.DOMAIN]
    )
    # Unloading keeps the remote endpoint, so a reload can reuse it.
    client.webhooks.delete.assert_not_awaited()


async def test_removing_access_entry_also_cleans_up_remote_webhook(
    hass, mock_config_entry
):
    """Entry removal invokes Access cleanup without changing other cleanup hooks."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, CONF_ACCESS_TOKEN: "test-access-token"},
    )
    with patch(
        "custom_components.unifi_insights.async_remove_access_webhook"
    ) as cleanup:
        await async_remove_entry(hass, mock_config_entry)
    cleanup.assert_awaited_once_with(hass, mock_config_entry)


@pytest.mark.parametrize(
    "record",
    [
        {"id": 123, "secret": "fixture-malformed-secret"},
        {"id": "broken-endpoint", "secret": {"value": "fixture-malformed-secret"}},
    ],
)
async def test_malformed_webhook_response_is_sanitized_and_isolated(
    *,
    hass,
    mock_config_entry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    enable_custom_integrations,
    caplog,
    record,
):
    """Actual endpoint parsing never leaks input or prevents other apps loading."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            CONF_ACCESS_TOKEN: "test-access-token",
            CONF_CONSOLE_ID: NAMESPACE,
        },
        options={CONF_ACCESS_WEBHOOK: True},
    )

    async def _get_response(path, **_kwargs):
        data = {
            "doors": [{"id": "door-native", "is_bind_hub": True}],
            "devices": [],
            "doors/settings/emergency": {"lockdown": False, "evacuation": False},
            "webhooks/endpoints": [record],
        }
        return {"code": "SUCCESS", "data": data[path]}

    async def _run_socket(*_args, **_kwargs):
        await asyncio.Event().wait()

    with (
        patch(
            "unifi_official_api.access.client.UniFiAccessClient._get",
            side_effect=_get_response,
        ),
        patch(
            "custom_components.unifi_insights.access_webhook.webhook.async_generate_url",
            return_value="http://ha.test.local:8123/api/webhook/test-malformed",
        ),
        patch(
            "custom_components.unifi_insights.coordinators.access."
            "AccessNotificationSocket.run",
            side_effect=_run_socket,
        ),
        caplog.at_level(logging.DEBUG),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert mock_config_entry.state is ConfigEntryState.LOADED
        data = mock_config_entry.runtime_data
        assert data.access_coordinator is not None
        assert not data.access_coordinator.webhook_active
        assert data.protect_coordinator is not None
        assert data.network_client is mock_network_client
        assert "fixture-malformed-secret" not in caplog.text
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


@pytest.mark.parametrize(
    "injection",
    ["probe", "coordinator-init"],
    ids=["probe-raises", "coordinator-init-raises"],
)
@pytest.mark.parametrize("close_fails", [False, True], ids=["close-ok", "close-fails"])
async def test_unexpected_access_setup_error_closes_client_and_keeps_other_apps(
    *,
    hass,
    mock_config_entry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    enable_custom_integrations,
    caplog,
    injection,
    close_fails,
):
    """
    Defensive cleanup: an injected UniFiError after the client is built closes it.

    Injected, not observed. A real door API error cannot reach this except: the
    probe classifies it as a status. The error is raised by a patched helper
    that runs after UniFiAccessClient is constructed. A failing close is logged
    at debug level and does not stop Network and Protect from loading.
    """
    injected_message = "Injected-unexpected-helper-failure"
    _add_access_entry(hass, mock_config_entry)
    client = _client(doors=DOORS, devices=DEVICES)
    if close_fails:
        client.close.side_effect = RuntimeError("Close failed")
    injected = UniFiConnectionError(injected_message)
    helper = (
        patch(
            "custom_components.unifi_insights.async_probe_access", side_effect=injected
        )
        if injection == "probe"
        else patch(
            "custom_components.unifi_insights.UnifiInsightsAccessCoordinator",
            side_effect=injected,
        )
    )

    with (
        patch(
            "custom_components.unifi_insights.UniFiAccessClient", return_value=client
        ),
        helper,
        caplog.at_level(logging.DEBUG, logger="custom_components.unifi_insights"),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    data = mock_config_entry.runtime_data
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert data.access_client is None
    assert data.access_coordinator is None
    assert data.network_client is mock_network_client
    assert data.protect_coordinator is not None
    client.close.assert_awaited_once()
    assert "UniFi Access setup failed (UniFiConnectionError)" in caplog.text
    assert injected_message not in caplog.text
    assert "test-access-token" not in caplog.text
    assert ("Error closing Access client (RuntimeError)" in caplog.text) is close_fails
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_identity_conflict_after_move_cleanup_reloads_and_restores_receiver(
    *,
    hass,
    hass_client,
    mock_config_entry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    enable_custom_integrations,
):
    """A move refused after remote cleanup reloads the entry and re-registers it.

    The identity conflict is injected: async_rekey_site_entities raises after
    the old endpoint was deleted and its receiver unregistered. Entry setup,
    the webhook registration, the scheduled reload and the receiver it registers
    are all the real Home Assistant paths.
    """
    client = _client(doors=DOORS, devices=DEVICES)
    client.webhooks.get_all = AsyncMock(return_value=[])
    client.webhooks.create = AsyncMock(
        side_effect=[
            AccessWebhookEndpoint.model_validate(
                {"id": "endpoint-old", "secret": "old-secret"}
            ),
            AccessWebhookEndpoint.model_validate(
                {"id": "endpoint-new", "secret": "new-secret"}
            ),
        ]
    )
    client.webhooks.delete = AsyncMock()
    client.close = AsyncMock()
    url = "http://ha.test.local:8123/api/webhook/test-recovery"

    async def _run_socket(*_args, **_kwargs):
        await asyncio.Event().wait()

    def _deliver(http, secret: str):
        body = _unlock_event()
        return http.post(
            webhook.async_generate_path(stored_id),
            data=body,
            headers={
                "Signature": _sign(body, timestamp=int(time.time()), secret=secret)
            },
        )

    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            CONF_ACCESS_TOKEN: "test-access-token",
            CONF_CONSOLE_ID: NAMESPACE,
        },
        options={**mock_config_entry.options, CONF_ACCESS_WEBHOOK: True},
    )
    with (
        patch(
            "custom_components.unifi_insights.UniFiAccessClient", return_value=client
        ),
        patch(
            "custom_components.unifi_insights.config_flow.UniFiAccessClient",
            return_value=client,
        ),
        patch(
            "custom_components.unifi_insights.access_webhook.UniFiAccessClient",
            return_value=client,
        ),
        patch(
            "custom_components.unifi_insights.access_webhook.webhook.async_generate_url",
            return_value=url,
        ),
        patch(
            "custom_components.unifi_insights.coordinators.access."
            "AccessNotificationSocket.run",
            side_effect=_run_socket,
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        stored_id = mock_config_entry.data[CONF_ACCESS_WEBHOOK_ID]
        http = await hass_client()
        assert (await _deliver(http, "old-secret")).status == HTTPStatus.OK

        with patch(
            "custom_components.unifi_insights.config_flow.async_rekey_site_entities",
            side_effect=SiteEntityIdentityError("forced identity conflict"),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={
                    "source": config_entries.SOURCE_RECONFIGURE,
                    "entry_id": mock_config_entry.entry_id,
                },
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={
                    CONF_HOST: "https://192.168.2.1",
                    CONF_API_KEY: "test_api_key",
                    CONF_VERIFY_SSL: False,
                },
            )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "site_entity_identity_conflict"}
        assert mock_config_entry.data[CONF_HOST] == "https://192.168.1.1"
        client.webhooks.delete.assert_awaited_once_with("endpoint-old")

        # The scheduled reload registers the receiver again, with a new endpoint.
        await hass.async_block_till_done()

        assert mock_config_entry.state is ConfigEntryState.LOADED
        assert mock_config_entry.data[CONF_ACCESS_WEBHOOK_ID] == stored_id
        assert mock_config_entry.data[CONF_ACCESS_WEBHOOK_SECRET] == "new-secret"
        assert client.webhooks.create.await_count == 2
        assert (await _deliver(http, "old-secret")).status != HTTPStatus.OK
        assert (await _deliver(http, "new-secret")).status == HTTPStatus.OK
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()


async def test_unload_settles_debounced_refresh_before_closing_the_client(
    *,
    hass,
    mock_config_entry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    enable_custom_integrations,
    caplog,
):
    """Real HA unload: a debounced refresh blocked on the console ends before close.

    The status frame arrives through the socket's own frame callback, so the
    refresh starts from the same path a console notification uses.
    """
    _add_access_entry(hass, mock_config_entry)
    client = _client(doors=DOORS, devices=DEVICES)
    frames = {}
    started = asyncio.Queue()
    cancelled = []
    seen_at_close = []

    async def _run_socket(on_frame, **_kwargs):
        frames["on_frame"] = on_frame
        await asyncio.Event().wait()

    async def _blocked_read() -> None:
        started.put_nowait(None)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    async def _record_close() -> None:
        seen_at_close.append(list(cancelled))

    with (
        patch(
            "custom_components.unifi_insights.UniFiAccessClient", return_value=client
        ),
        patch(
            "custom_components.unifi_insights.coordinators.access."
            "AccessNotificationSocket.run",
            side_effect=_run_socket,
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = mock_config_entry.runtime_data.access_coordinator
        snapshot = coordinator.data
        client.doors.get_all.side_effect = _blocked_read
        frames["on_frame"]({"event": "access.remote_view.change", "data": {}})
        async_fire_time_changed(
            hass,
            dt_util.utcnow() + timedelta(seconds=ACCESS_DEBOUNCE_SECONDS + 1),
        )
        await started.get()
        reads_before_unload = client.doors.get_all.await_count

        client.close.side_effect = _record_close
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert seen_at_close
    assert all(seen == [True] for seen in seen_at_close)
    assert client.doors.get_all.await_count == reads_before_unload
    assert coordinator.data is snapshot
    assert _unexpected_errors(caplog) == []


def _block_doors_read(
    client: MagicMock,
) -> tuple[asyncio.Queue[None], list[bool]]:
    """Make the doors read wait on the console until it is cancelled."""
    started = asyncio.Queue()
    cancelled = []

    async def _blocked_read() -> None:
        started.put_nowait(None)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    client.doors.get_all.side_effect = _blocked_read
    return started, cancelled


async def test_unload_settles_periodic_poll_before_closing_the_client(
    *,
    hass,
    mock_config_entry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    enable_custom_integrations,
    caplog,
):
    """Real HA poll timer: a periodic read blocked on the console ends before close."""
    _add_access_entry(hass, mock_config_entry)
    client = _client(doors=DOORS, devices=DEVICES)

    async def _run_socket(*_args, **_kwargs):
        await asyncio.Event().wait()

    seen_at_close = []

    async def _record_close() -> None:
        seen_at_close.append(list(cancelled))

    with (
        patch(
            "custom_components.unifi_insights.UniFiAccessClient", return_value=client
        ),
        patch(
            "custom_components.unifi_insights.coordinators.access."
            "AccessNotificationSocket.run",
            side_effect=_run_socket,
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = mock_config_entry.runtime_data.access_coordinator
        snapshot = coordinator.data
        started, cancelled = _block_doors_read(client)
        async_fire_time_changed(
            hass, dt_util.utcnow() + SCAN_INTERVAL_ACCESS + timedelta(seconds=1)
        )
        await started.get()
        reads_before_unload = client.doors.get_all.await_count

        client.close.side_effect = _record_close
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert seen_at_close
    assert all(seen == [True] for seen in seen_at_close)
    assert client.doors.get_all.await_count == reads_before_unload
    assert coordinator.data is snapshot
    assert _unexpected_errors(caplog) == []


async def test_queued_refresh_behind_blocked_poll_never_reads_after_unload(
    *,
    hass,
    mock_config_entry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    enable_custom_integrations,
    caplog,
):
    """A status refresh queued on HA's refresh lock does not read after unload."""
    _add_access_entry(hass, mock_config_entry)
    client = _client(doors=DOORS, devices=DEVICES)
    frames = {}

    async def _run_socket(on_frame, **_kwargs):
        frames["on_frame"] = on_frame
        await asyncio.Event().wait()

    seen_at_close = []

    async def _record_close() -> None:
        seen_at_close.append(list(cancelled))

    with (
        patch(
            "custom_components.unifi_insights.UniFiAccessClient", return_value=client
        ),
        patch(
            "custom_components.unifi_insights.coordinators.access."
            "AccessNotificationSocket.run",
            side_effect=_run_socket,
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        started, cancelled = _block_doors_read(client)
        async_fire_time_changed(
            hass, dt_util.utcnow() + SCAN_INTERVAL_ACCESS + timedelta(seconds=1)
        )
        await started.get()
        frames["on_frame"]({"event": "access.remote_view.change", "data": {}})
        async_fire_time_changed(
            hass,
            dt_util.utcnow() + timedelta(seconds=ACCESS_DEBOUNCE_SECONDS + 1),
        )
        await hass.async_block_till_done()
        reads_before_unload = client.doors.get_all.await_count

        client.close.side_effect = _record_close
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert seen_at_close
    assert all(seen == [True] for seen in seen_at_close)
    assert client.doors.get_all.await_count == reads_before_unload
    assert _unexpected_errors(caplog) == []


async def test_failed_setup_settles_reads_before_closing_the_client(
    *,
    hass,
    mock_config_entry,
    mock_network_client,
    mock_protect_client,
    mock_local_auth,
    enable_custom_integrations,
):
    """A failed forward with a read in flight settles the read before close.

    Home Assistant runs the entry's on_unload callbacks as concurrent tasks, so
    the client close must wait for the shutdown inside one ordered callback.
    """
    client = _client(doors=DOORS, devices=DEVICES)
    _add_access_entry(hass, mock_config_entry)
    coordinators: list[UnifiInsightsAccessCoordinator] = []
    blocked: list[tuple[asyncio.Queue[None], list[bool]]] = []
    reads_at_failure: list[int] = []
    close_seen: list[tuple[bool, list[bool]]] = []

    async def _run_socket(*_args, **_kwargs):
        await asyncio.Event().wait()

    async def _fail_with_reads_in_flight(*_args, **_kwargs) -> None:
        coordinator = mock_config_entry.runtime_data.access_coordinator
        coordinators.append(coordinator)
        blocked.append(_block_doors_read(client))
        started, _ = blocked[0]
        hass.async_create_task(coordinator.async_refresh())
        await started.get()
        hass.async_create_task(coordinator.async_refresh())
        await asyncio.sleep(0)
        reads_at_failure.append(client.doors.get_all.await_count)
        msg = "Platform forwarding failed"
        raise RuntimeError(msg)

    async def _record_close() -> None:
        _, cancelled = blocked[0]
        close_seen.append((coordinators[0]._shutdown_requested, list(cancelled)))

    with (
        patch(
            "custom_components.unifi_insights.UniFiAccessClient", return_value=client
        ),
        patch(
            "custom_components.unifi_insights.coordinators.access."
            "AccessNotificationSocket.run",
            side_effect=_run_socket,
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            side_effect=_fail_with_reads_in_flight,
        ),
    ):
        client.close.side_effect = _record_close
        async with asyncio.timeout(5):
            assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
            await hass.async_block_till_done(wait_background_tasks=True)

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert close_seen == [(True, [True])]
    client.close.assert_awaited_once()
    assert client.doors.get_all.await_count == reads_at_failure[0]


# The camera platform logs this once when Pillow lacks libturbojpeg. It is
# environment noise, not an Access failure, so it is the only ERROR allowed.
_KNOWN_ENVIRONMENT_ERRORS = frozenset(
    {
        (
            "homeassistant.components.camera.img_util",
            (
                "Error loading libturbojpeg; "
                "Camera snapshot performance will be sub-optimal"
            ),
        )
    }
)


def _unexpected_errors(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Return every ERROR record except the known camera libturbojpeg notice."""
    return [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR
        and (record.name, record.getMessage()) not in _KNOWN_ENVIRONMENT_ERRORS
    ]
