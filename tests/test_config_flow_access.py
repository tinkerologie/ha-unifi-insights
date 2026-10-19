# Copyright 2026 UniFi Insights contributors
"""Config flow tests for the optional UniFi Access token, port and webhook state."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.components import webhook
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_VERIFY_SSL
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiNotFoundError,
    UniFiResponseError,
    UniFiValidationError,
)

from custom_components.unifi_insights.config_flow import (
    _access_server_moved,
    _clear_access_field,
    _host_key,
)
from custom_components.unifi_insights.console_identity import SiteEntityIdentityError
from custom_components.unifi_insights.const import (
    CONF_ACCESS_PORT,
    CONF_ACCESS_TOKEN,
    CONF_ACCESS_WEBHOOK,
    CONF_ACCESS_WEBHOOK_ENDPOINT_ID,
    CONF_ACCESS_WEBHOOK_ID,
    CONF_ACCESS_WEBHOOK_SECRET,
    CONF_CLEAR_ACCESS_TOKEN,
    CONF_CONNECTION_TYPE,
    CONF_CONSOLE_ID,
    CONF_CONSOLE_NAME,
    CONNECTION_TYPE_LOCAL,
    DEFAULT_ACCESS_PORT,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

_HOST = "https://192.168.1.1"
_NEW_HOST = "https://192.168.2.1"
_API_KEY = "network-api-key"
_CONSOLE_MAC = "aa:bb:cc:dd:ee:ff"
_OLD_TOKEN = "old-access-token"  # noqa: S105 - fixture token, not a credential
_NEW_TOKEN = "new-access-token"  # noqa: S105 - fixture token, not a credential
_WEBHOOK_STATE = {
    CONF_ACCESS_WEBHOOK_ID: "receiver-id",
    CONF_ACCESS_WEBHOOK_ENDPOINT_ID: "endpoint-id",
    CONF_ACCESS_WEBHOOK_SECRET: "endpoint-secret",
}


def _network_cm() -> MagicMock:
    """Network client context whose main validation succeeds with one site."""
    client = MagicMock()
    client.sites = MagicMock()
    client.sites.get_all = AsyncMock(return_value=[MagicMock(id="default")])
    client.devices = MagicMock()
    client.devices.get_all = AsyncMock(return_value=[])
    client.close = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


def _access_cm(get_all: AsyncMock) -> MagicMock:
    """Access client context whose doors listing is controlled by the test."""
    client = MagicMock()
    client.doors = MagicMock()
    client.doors.get_all = get_all
    client.close = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


def _failing_cm() -> MagicMock:
    """Application client context whose connection is rejected for the key."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(side_effect=UniFiAuthenticationError("bad key"))
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


def _doors_ok() -> AsyncMock:
    return AsyncMock(return_value=[MagicMock(id="door-1")])


@pytest.fixture(autouse=True)
def _no_entry_setup():
    """Keep entry setup and reload from opening sockets."""
    with patch(
        "custom_components.unifi_insights.async_setup_entry",
        return_value=True,
    ):
        yield


@pytest.fixture
def network_cm():
    """Patch the Network client used for main local validation."""
    cm = _network_cm()
    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        yield cm


@pytest.fixture
def access_client_cls():
    """Patch the Access client class; tests set the doors result per case."""
    with patch(
        "custom_components.unifi_insights.config_flow.UniFiAccessClient",
        return_value=_access_cm(_doors_ok()),
    ) as cls:
        yield cls


@pytest.fixture
def remove_webhook():
    """Patch the remote webhook cleanup and record the entry data it saw."""
    seen: list[dict[str, Any]] = []

    async def _record(
        hass: HomeAssistant,
        entry: MockConfigEntry,
        *,
        keep_receiver_on_failure: bool = False,
    ) -> bool:
        seen.append(dict(entry.data))
        return True

    with patch(
        "custom_components.unifi_insights.access_webhook.async_remove_access_webhook",
        side_effect=_record,
    ) as mock:
        mock.seen = seen
        yield mock


@pytest.fixture
def remote_console():
    """Patch the temporary Access client used by the real webhook cleanup."""
    client = MagicMock()
    client.webhooks.delete = AsyncMock(return_value=None)
    client.close = AsyncMock(return_value=None)
    with patch(
        "custom_components.unifi_insights.access_webhook.UniFiAccessClient",
        return_value=client,
    ):
        yield client


async def _register_receiver(hass: HomeAssistant) -> list[str]:
    """Register the stored receiver id; the returned list records each delivery."""
    calls: list[str] = []

    async def _handler(_hass: HomeAssistant, webhook_id: str, _request: Any) -> None:
        calls.append(webhook_id)

    await async_setup_component(hass, "webhook", {})
    webhook.async_register(hass, DOMAIN, "Access", "receiver-id", _handler)
    return calls


async def _receiver_answers(
    hass: HomeAssistant, hass_client: Any, calls: list[str]
) -> bool:
    """Post to the receiver path and report whether its handler ran."""
    before = len(calls)
    http = await hass_client()
    await http.post(webhook.async_generate_path("receiver-id"))
    await hass.async_block_till_done()
    return len(calls) > before


def _set_access_result(access_client_cls: MagicMock, get_all: AsyncMock) -> None:
    access_client_cls.return_value = _access_cm(get_all)


async def _start_local_flow(hass: HomeAssistant) -> ConfigFlowResult:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL}
    )


def _local_input(**extra: Any) -> dict[str, Any]:
    return {
        CONF_HOST: _HOST,
        CONF_API_KEY: _API_KEY,
        CONF_VERIFY_SSL: False,
        **extra,
    }


def _existing_entry(
    *,
    token: str | None = _OLD_TOKEN,
    webhook: bool = True,
    endpoint: bool = True,
    host: str = _HOST,
    port: int | None = None,
) -> MockConfigEntry:
    data: dict[str, Any] = {
        CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
        CONF_HOST: host,
        CONF_API_KEY: _API_KEY,
        CONF_VERIFY_SSL: False,
        CONF_CONSOLE_ID: _CONSOLE_MAC,
    }
    if token is not None:
        data[CONF_ACCESS_TOKEN] = token
    if port is not None:
        data[CONF_ACCESS_PORT] = port
    if webhook and token is not None:
        data.update(_WEBHOOK_STATE)
        if not endpoint:
            del data[CONF_ACCESS_WEBHOOK_ENDPOINT_ID]
            del data[CONF_ACCESS_WEBHOOK_SECRET]
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=_CONSOLE_MAC,
        title="UniFi Insights (Local)",
        data=data,
    )


async def _reconfigure(
    hass: HomeAssistant, entry: MockConfigEntry, user_input: dict[str, Any]
) -> ConfigFlowResult:
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    assert result["type"] == FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=user_input
    )


class TestHostComparison:
    """Host identity used to decide whether the Access server has moved."""

    @pytest.mark.parametrize("host", [None, "", "   ", 5])
    def test_unusable_host_has_no_key(self, host: object) -> None:
        assert _host_key(host) is None

    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("https://Console.Local:8443", "console.local"),
            ("192.168.1.1", "192.168.1.1"),
            ("[fd00::1]", "fd00::1"),
        ],
    )
    def test_host_key_is_lower_cased_name(self, host: str, expected: str) -> None:
        assert _host_key(host) == expected

    def test_same_host_different_case_is_not_a_move(self) -> None:
        entry = _existing_entry(host="https://Console.Local")
        assert (
            _access_server_moved(entry, "https://console.local", DEFAULT_ACCESS_PORT)
            is False
        )

    def test_different_port_is_a_move(self) -> None:
        entry = _existing_entry(port=8443)
        assert _access_server_moved(entry, _HOST, 9443)

    def test_clear_field_only_offered_with_stored_token(self) -> None:
        assert _clear_access_field(_existing_entry()) != {}
        assert _clear_access_field(_existing_entry(token=None)) == {}


class TestLocalSetupAccess:
    """Adding a local entry, with and without an Access token."""

    async def test_omitted_token_creates_entry_without_access(
        self, hass: HomeAssistant, network_cm, access_client_cls
    ) -> None:
        result = await _start_local_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=_local_input()
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert CONF_ACCESS_TOKEN not in result["data"]
        assert CONF_ACCESS_PORT not in result["data"]
        access_client_cls.assert_not_called()

    async def test_blank_token_is_treated_as_omitted(
        self, hass: HomeAssistant, network_cm, access_client_cls
    ) -> None:
        result = await _start_local_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=_local_input(**{CONF_ACCESS_TOKEN: "   "})
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert CONF_ACCESS_TOKEN not in result["data"]
        access_client_cls.assert_not_called()

    async def test_valid_token_is_stripped_and_stored_with_default_port(
        self, hass: HomeAssistant, network_cm, access_client_cls
    ) -> None:
        result = await _start_local_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=_local_input(**{CONF_ACCESS_TOKEN: f"  {_NEW_TOKEN} "}),
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_ACCESS_TOKEN] == _NEW_TOKEN
        assert result["data"][CONF_ACCESS_PORT] == DEFAULT_ACCESS_PORT
        assert access_client_cls.call_args.kwargs["token"] == _NEW_TOKEN
        assert access_client_cls.call_args.kwargs["host"] == _HOST
        assert access_client_cls.call_args.kwargs["verify_ssl"] is False

    async def test_custom_port_is_probed_and_stored(
        self, hass: HomeAssistant, network_cm, access_client_cls
    ) -> None:
        result = await _start_local_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=_local_input(
                **{CONF_ACCESS_TOKEN: _NEW_TOKEN, CONF_ACCESS_PORT: 8443}
            ),
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_ACCESS_PORT] == 8443
        assert access_client_cls.call_args.kwargs["port"] == 8443

    async def test_rejected_token_shows_error_then_recovers(
        self, hass: HomeAssistant, network_cm, access_client_cls
    ) -> None:
        _set_access_result(
            access_client_cls,
            AsyncMock(side_effect=UniFiAuthenticationError("rejected")),
        )
        result = await _start_local_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=_local_input(**{CONF_ACCESS_TOKEN: _OLD_TOKEN}),
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_ACCESS_TOKEN: "access_invalid_auth"}

        _set_access_result(access_client_cls, _doors_ok())
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=_local_input(**{CONF_ACCESS_TOKEN: _NEW_TOKEN}),
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_ACCESS_TOKEN] == _NEW_TOKEN

    async def test_unreachable_access_shows_cannot_connect(
        self, hass: HomeAssistant, network_cm, access_client_cls
    ) -> None:
        _set_access_result(
            access_client_cls,
            AsyncMock(side_effect=UniFiConnectionError("refused")),
        )
        result = await _start_local_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=_local_input(**{CONF_ACCESS_TOKEN: _NEW_TOKEN}),
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_ACCESS_TOKEN: "access_cannot_connect"}

    async def test_missing_access_api_shows_unsupported(
        self, hass: HomeAssistant, network_cm, access_client_cls
    ) -> None:
        _set_access_result(
            access_client_cls,
            AsyncMock(side_effect=UniFiNotFoundError("missing", status_code=404)),
        )
        result = await _start_local_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=_local_input(**{CONF_ACCESS_TOKEN: _NEW_TOKEN}),
        )
        assert result["errors"] == {CONF_ACCESS_TOKEN: "access_unsupported"}

    async def test_empty_access_listing_is_accepted(
        self, hass: HomeAssistant, network_cm, access_client_cls
    ) -> None:
        _set_access_result(access_client_cls, AsyncMock(return_value=[]))
        result = await _start_local_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=_local_input(**{CONF_ACCESS_TOKEN: _NEW_TOKEN}),
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY

    async def test_rejected_client_construction_shows_unsupported(
        self, hass: HomeAssistant, network_cm, access_client_cls
    ) -> None:
        access_client_cls.side_effect = UniFiValidationError("bad port")
        result = await _start_local_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=_local_input(**{CONF_ACCESS_TOKEN: _NEW_TOKEN}),
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_ACCESS_TOKEN: "access_unsupported"}

    async def test_console_identity_is_stored_with_access_token(
        self, hass: HomeAssistant, network_cm, access_client_cls
    ) -> None:
        result = await _start_local_flow(hass)
        with patch(
            "custom_components.unifi_insights.config_flow.resolve_console_identity",
            return_value=(_CONSOLE_MAC, "Dream Router"),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input=_local_input(**{CONF_ACCESS_TOKEN: _NEW_TOKEN}),
            )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_CONSOLE_ID] == _CONSOLE_MAC
        assert result["data"][CONF_CONSOLE_NAME] == "Dream Router"
        assert result["data"][CONF_ACCESS_TOKEN] == _NEW_TOKEN

    async def test_failed_main_connection_skips_access_probe(
        self, hass: HomeAssistant, access_client_cls
    ) -> None:
        with (
            patch(
                "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
                return_value=_failing_cm(),
            ),
            patch("custom_components.unifi_insights.config_flow.LocalAuth"),
            patch(
                "custom_components.unifi_insights.config_flow.UniFiProtectClient",
                return_value=_failing_cm(),
            ),
        ):
            result = await _start_local_flow(hass)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input=_local_input(**{CONF_ACCESS_TOKEN: _NEW_TOKEN}),
            )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_API_KEY: "invalid_auth"}
        access_client_cls.assert_not_called()


class TestReconfigureAccess:
    """Local reconfigure: preservation, rotation, clear and server moves."""

    async def test_missing_token_field_keeps_access_fields(
        self, hass: HomeAssistant, network_cm, access_client_cls, remove_webhook
    ) -> None:
        entry = _existing_entry(port=8443)
        result = await _reconfigure(hass, entry, _local_input())
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert entry.data[CONF_ACCESS_TOKEN] == _OLD_TOKEN
        assert entry.data[CONF_ACCESS_PORT] == 8443
        assert entry.data[CONF_ACCESS_WEBHOOK_ID] == "receiver-id"
        assert entry.data[CONF_ACCESS_WEBHOOK_SECRET] == "endpoint-secret"
        access_client_cls.assert_not_called()
        remove_webhook.assert_not_called()

    async def test_reconfigure_rejects_different_hardware_identity(
        self, hass: HomeAssistant, network_cm, access_client_cls, remove_webhook
    ) -> None:
        entry = _existing_entry()
        before = dict(entry.data)
        with patch(
            "custom_components.unifi_insights.config_flow.resolve_console_identity",
            return_value=("11:22:33:44:55:66", "Other Router"),
        ):
            result = await _reconfigure(hass, entry, _local_input())
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "account_mismatch"
        assert dict(entry.data) == before
        remove_webhook.assert_not_called()

    async def test_reconfigure_adopting_a_mac_owned_by_another_entry_aborts(
        self, hass: HomeAssistant, network_cm, access_client_cls, remove_webhook
    ) -> None:
        legacy = MockConfigEntry(
            domain=DOMAIN,
            unique_id="192.168.1.1",
            title="UniFi Insights (Local)",
            data={
                CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
                CONF_HOST: _HOST,
                CONF_API_KEY: _API_KEY,
                CONF_VERIFY_SSL: False,
                CONF_ACCESS_TOKEN: _OLD_TOKEN,
            },
        )
        owner = MockConfigEntry(
            domain=DOMAIN,
            unique_id="11:22:33:44:55:66",
            title="Other site",
            data={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL},
        )
        owner.add_to_hass(hass)
        with patch(
            "custom_components.unifi_insights.config_flow.resolve_console_identity",
            return_value=("11:22:33:44:55:66", "Other Router"),
        ):
            result = await _reconfigure(hass, legacy, _local_input())
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "already_configured"
        assert legacy.data[CONF_ACCESS_TOKEN] == _OLD_TOKEN

    async def test_token_rotation_on_same_server_keeps_webhook_state(
        self, hass: HomeAssistant, network_cm, access_client_cls, remove_webhook
    ) -> None:
        entry = _existing_entry()
        result = await _reconfigure(
            hass,
            entry,
            _local_input(**{CONF_ACCESS_TOKEN: _NEW_TOKEN}),
        )
        assert result["type"] == FlowResultType.ABORT
        assert entry.data[CONF_ACCESS_TOKEN] == _NEW_TOKEN
        assert entry.data[CONF_ACCESS_PORT] == DEFAULT_ACCESS_PORT
        assert entry.data[CONF_ACCESS_WEBHOOK_ENDPOINT_ID] == "endpoint-id"
        assert entry.data[CONF_ACCESS_WEBHOOK_SECRET] == "endpoint-secret"
        remove_webhook.assert_not_called()

    async def test_explicit_clear_removes_remote_webhook_first(
        self, hass: HomeAssistant, network_cm, access_client_cls, remove_webhook
    ) -> None:
        entry = _existing_entry(port=8443)
        result = await _reconfigure(
            hass,
            entry,
            _local_input(**{CONF_CLEAR_ACCESS_TOKEN: True}),
        )
        assert result["type"] == FlowResultType.ABORT
        remove_webhook.assert_awaited_once()
        assert remove_webhook.call_args.kwargs == {"keep_receiver_on_failure": False}
        # The cleanup saw the original entry, so it could reach the old server.
        assert remove_webhook.seen[0][CONF_ACCESS_TOKEN] == _OLD_TOKEN
        assert remove_webhook.seen[0][CONF_ACCESS_WEBHOOK_ENDPOINT_ID] == "endpoint-id"
        for key in (
            CONF_ACCESS_TOKEN,
            CONF_ACCESS_PORT,
            CONF_ACCESS_WEBHOOK_ID,
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID,
            CONF_ACCESS_WEBHOOK_SECRET,
        ):
            assert key not in entry.data
        access_client_cls.assert_not_called()

    async def test_clear_without_webhook_state_drops_token_without_remote_call(
        self, hass: HomeAssistant, network_cm, access_client_cls, remove_webhook
    ) -> None:
        entry = _existing_entry(webhook=False)
        result = await _reconfigure(
            hass,
            entry,
            _local_input(**{CONF_CLEAR_ACCESS_TOKEN: True}),
        )
        assert result["type"] == FlowResultType.ABORT
        remove_webhook.assert_not_called()
        assert CONF_ACCESS_TOKEN not in entry.data

    async def test_typed_token_wins_over_clear_flag(
        self, hass: HomeAssistant, network_cm, access_client_cls, remove_webhook
    ) -> None:
        entry = _existing_entry()
        result = await _reconfigure(
            hass,
            entry,
            _local_input(
                **{CONF_ACCESS_TOKEN: _NEW_TOKEN, CONF_CLEAR_ACCESS_TOKEN: True}
            ),
        )
        assert result["type"] == FlowResultType.ABORT
        assert entry.data[CONF_ACCESS_TOKEN] == _NEW_TOKEN
        remove_webhook.assert_not_called()

    async def test_host_move_revalidates_stored_token_then_cleans_up(
        self, hass: HomeAssistant, network_cm, access_client_cls, remove_webhook
    ) -> None:
        entry = _existing_entry()
        result = await _reconfigure(hass, entry, _local_input(**{CONF_HOST: _NEW_HOST}))
        assert result["type"] == FlowResultType.ABORT
        # The stored token was probed against the new server.
        assert access_client_cls.call_args.kwargs["host"] == _NEW_HOST
        assert access_client_cls.call_args.kwargs["token"] == _OLD_TOKEN
        remove_webhook.assert_awaited_once()
        assert remove_webhook.call_args.kwargs == {"keep_receiver_on_failure": True}
        assert remove_webhook.seen[0][CONF_HOST] == _HOST
        assert entry.data[CONF_HOST] == _NEW_HOST
        assert entry.data[CONF_ACCESS_TOKEN] == _OLD_TOKEN
        assert entry.data[CONF_ACCESS_PORT] == DEFAULT_ACCESS_PORT
        assert CONF_ACCESS_WEBHOOK_ENDPOINT_ID not in entry.data
        assert CONF_ACCESS_WEBHOOK_SECRET not in entry.data

    async def test_rotation_without_webhook_keys_leaves_no_webhook_state(
        self, hass: HomeAssistant, network_cm, access_client_cls, remove_webhook
    ) -> None:
        entry = _existing_entry(webhook=False)
        result = await _reconfigure(
            hass,
            entry,
            _local_input(**{CONF_ACCESS_TOKEN: _NEW_TOKEN}),
        )
        assert result["type"] == FlowResultType.ABORT
        assert entry.data[CONF_ACCESS_TOKEN] == _NEW_TOKEN
        assert CONF_ACCESS_WEBHOOK_ID not in entry.data
        remove_webhook.assert_not_called()

    async def test_port_move_on_same_host_cleans_up(
        self, hass: HomeAssistant, network_cm, access_client_cls, remove_webhook
    ) -> None:
        entry = _existing_entry(port=12445)
        result = await _reconfigure(
            hass,
            entry,
            _local_input(**{CONF_ACCESS_PORT: 9443}),
        )
        assert result["type"] == FlowResultType.ABORT
        remove_webhook.assert_awaited_once()
        assert entry.data[CONF_ACCESS_PORT] == 9443
        assert CONF_ACCESS_WEBHOOK_ID not in entry.data

    async def test_rejected_stored_token_after_move_keeps_registration(
        self, hass: HomeAssistant, network_cm, access_client_cls, remove_webhook
    ) -> None:
        _set_access_result(
            access_client_cls,
            AsyncMock(side_effect=UniFiAuthenticationError("rejected")),
        )
        entry = _existing_entry()
        before = dict(entry.data)
        result = await _reconfigure(hass, entry, _local_input(**{CONF_HOST: _NEW_HOST}))
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_ACCESS_TOKEN: "access_invalid_auth"}
        remove_webhook.assert_not_called()
        assert dict(entry.data) == before

    async def test_rejected_new_token_after_move_keeps_registration(
        self, hass: HomeAssistant, network_cm, access_client_cls, remove_webhook
    ) -> None:
        _set_access_result(
            access_client_cls,
            AsyncMock(side_effect=UniFiConnectionError("refused")),
        )
        entry = _existing_entry()
        before = dict(entry.data)
        result = await _reconfigure(
            hass,
            entry,
            _local_input(**{CONF_HOST: _NEW_HOST, CONF_ACCESS_TOKEN: _NEW_TOKEN}),
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_ACCESS_TOKEN: "access_cannot_connect"}
        remove_webhook.assert_not_called()
        assert dict(entry.data) == before

    async def test_rejected_main_connection_keeps_registration(
        self, hass: HomeAssistant, access_client_cls, remove_webhook
    ) -> None:
        entry = _existing_entry()
        before = dict(entry.data)
        with (
            patch(
                "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
                return_value=_failing_cm(),
            ),
            patch("custom_components.unifi_insights.config_flow.LocalAuth"),
            patch(
                "custom_components.unifi_insights.config_flow.UniFiProtectClient",
                return_value=_failing_cm(),
            ),
        ):
            result = await _reconfigure(
                hass,
                entry,
                _local_input(**{CONF_HOST: _NEW_HOST, CONF_CLEAR_ACCESS_TOKEN: True}),
            )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_API_KEY: "invalid_auth"}
        remove_webhook.assert_not_called()
        access_client_cls.assert_not_called()
        assert dict(entry.data) == before

    async def test_no_stored_token_host_move_updates_only_host(
        self, hass: HomeAssistant, network_cm, access_client_cls, remove_webhook
    ) -> None:
        entry = _existing_entry(token=None)
        result = await _reconfigure(hass, entry, _local_input(**{CONF_HOST: _NEW_HOST}))
        assert result["type"] == FlowResultType.ABORT
        access_client_cls.assert_not_called()
        remove_webhook.assert_not_called()
        assert CONF_ACCESS_TOKEN not in entry.data


@pytest.mark.usefixtures("network_cm", "access_client_cls")
class TestReconfigureReceiverCleanup:
    """Host/port moves and clears drive the real remote cleanup and receiver."""

    async def test_move_refused_cleanup_aborts_and_keeps_everything(
        self,
        hass: HomeAssistant,
        hass_client: Any,
        remote_console,
    ) -> None:
        entry = _existing_entry()
        calls = await _register_receiver(hass)
        before = dict(entry.data)
        remote_console.webhooks.delete.side_effect = UniFiResponseError(
            "refused", status_code=500
        )
        result = await _reconfigure(hass, entry, _local_input(**{CONF_HOST: _NEW_HOST}))
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "cannot_connect"
        assert dict(entry.data) == before
        remote_console.webhooks.delete.assert_awaited_once_with("endpoint-id")
        remote_console.close.assert_awaited_once()
        assert await _receiver_answers(hass, hass_client, calls)

    @pytest.mark.parametrize(
        "failure", ["refused", "rejected", "permission", "timeout"]
    )
    async def test_move_failed_or_timed_out_cleanup_keeps_receiver_and_identity(
        self,
        hass: HomeAssistant,
        hass_client: Any,
        remote_console,
        failure: str,
    ) -> None:
        entry = _existing_entry()
        calls = await _register_receiver(hass)
        before = dict(entry.data)

        async def hang(_endpoint_id: str) -> None:
            await asyncio.sleep(10)

        if failure == "refused":
            delete_failure = UniFiResponseError("refused", status_code=500)
        elif failure == "rejected":
            delete_failure = UniFiAuthenticationError("rejected", status_code=401)
        elif failure == "permission":
            delete_failure = UniFiAuthenticationError("denied", status_code=403)
        else:
            delete_failure = hang
        remote_console.webhooks.delete.side_effect = delete_failure
        with patch(
            "custom_components.unifi_insights.access_webhook.REMOVE_DELETE_TIMEOUT_SECONDS",
            0.05,
        ):
            result = await _reconfigure(
                hass, entry, _local_input(**{CONF_HOST: _NEW_HOST})
            )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "cannot_connect"
        assert dict(entry.data) == before
        assert await _receiver_answers(hass, hass_client, calls)

    @pytest.mark.parametrize(
        "delete_error",
        [
            None,
            UniFiNotFoundError("gone", status_code=404),
            UniFiResponseError("gone", status_code=404),
        ],
    )
    async def test_move_confirmed_gone_retires_receiver_then_saves(
        self,
        hass: HomeAssistant,
        hass_client: Any,
        remote_console,
        delete_error: Exception | None,
    ) -> None:
        entry = _existing_entry()
        calls = await _register_receiver(hass)
        remote_console.webhooks.delete.side_effect = delete_error
        result = await _reconfigure(hass, entry, _local_input(**{CONF_HOST: _NEW_HOST}))
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        remote_console.webhooks.delete.assert_awaited_once_with("endpoint-id")
        assert entry.data[CONF_HOST] == _NEW_HOST
        assert entry.data[CONF_ACCESS_TOKEN] == _OLD_TOKEN
        assert CONF_ACCESS_WEBHOOK_ID not in entry.data
        assert CONF_ACCESS_WEBHOOK_ENDPOINT_ID not in entry.data
        assert CONF_ACCESS_WEBHOOK_SECRET not in entry.data
        assert not await _receiver_answers(hass, hass_client, calls)

    async def test_move_without_stored_endpoint_saves_without_remote_call(
        self,
        hass: HomeAssistant,
        hass_client: Any,
        remote_console,
    ) -> None:
        entry = _existing_entry(endpoint=False)
        calls = await _register_receiver(hass)
        result = await _reconfigure(hass, entry, _local_input(**{CONF_HOST: _NEW_HOST}))
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        remote_console.webhooks.delete.assert_not_called()
        assert entry.data[CONF_HOST] == _NEW_HOST
        assert CONF_ACCESS_WEBHOOK_ID not in entry.data
        assert not await _receiver_answers(hass, hass_client, calls)

    async def test_clear_refused_cleanup_still_drops_token(
        self,
        hass: HomeAssistant,
        hass_client: Any,
        remote_console,
    ) -> None:
        entry = _existing_entry(port=8443)
        calls = await _register_receiver(hass)
        remote_console.webhooks.delete.side_effect = UniFiResponseError(
            "refused", status_code=500
        )
        result = await _reconfigure(
            hass, entry, _local_input(**{CONF_CLEAR_ACCESS_TOKEN: True})
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert CONF_ACCESS_TOKEN not in entry.data
        assert CONF_ACCESS_WEBHOOK_ID not in entry.data
        assert not await _receiver_answers(hass, hass_client, calls)


@pytest.mark.usefixtures("network_cm", "access_client_cls")
class TestReconfigureRecoveryReload:
    """A save that fails after receiver cleanup asks Home Assistant to reload."""

    async def test_identity_conflict_after_move_cleanup_schedules_reload(
        self, hass: HomeAssistant, remote_console
    ) -> None:
        entry = _existing_entry()
        with (
            patch.object(hass.config_entries, "async_schedule_reload") as schedule,
            patch(
                "custom_components.unifi_insights.config_flow.async_rekey_site_entities",
                side_effect=SiteEntityIdentityError("forced identity conflict"),
            ),
        ):
            result = await _reconfigure(
                hass, entry, _local_input(**{CONF_HOST: _NEW_HOST})
            )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "site_entity_identity_conflict"}
        remote_console.webhooks.delete.assert_awaited_once_with("endpoint-id")
        assert entry.data[CONF_HOST] == _HOST
        schedule.assert_called_once_with(entry.entry_id)

    async def test_identity_conflict_skips_reload_when_entry_is_gone(
        self, hass: HomeAssistant, remote_console
    ) -> None:
        entry = _existing_entry()
        conflict = False
        real_get_entry = hass.config_entries.async_get_entry

        def _get_entry(entry_id: str) -> ConfigEntry | None:
            # The entry disappears once the identity conflict has been raised.
            return None if conflict else real_get_entry(entry_id)

        def _conflict(*_args: Any, **_kwargs: Any) -> None:
            nonlocal conflict
            conflict = True
            msg = "forced identity conflict"
            raise SiteEntityIdentityError(msg)

        with (
            patch.object(hass.config_entries, "async_schedule_reload") as schedule,
            patch.object(
                hass.config_entries, "async_get_entry", side_effect=_get_entry
            ),
            patch(
                "custom_components.unifi_insights.config_flow.async_rekey_site_entities",
                side_effect=_conflict,
            ),
        ):
            result = await _reconfigure(
                hass, entry, _local_input(**{CONF_HOST: _NEW_HOST})
            )
        assert result["errors"] == {"base": "site_entity_identity_conflict"}
        remote_console.webhooks.delete.assert_awaited_once_with("endpoint-id")
        schedule.assert_not_called()

    async def test_save_failure_after_refused_clear_cleanup_schedules_reload(
        self, hass: HomeAssistant, remote_console
    ) -> None:
        entry = _existing_entry(port=8443)
        remote_console.webhooks.delete.side_effect = UniFiResponseError(
            "refused", status_code=500
        )
        with (
            patch.object(hass.config_entries, "async_schedule_reload") as schedule,
            patch(
                "homeassistant.config_entries.ConfigFlow.async_update_reload_and_abort",
                side_effect=RuntimeError("storage unavailable"),
            ),
        ):
            result = await _reconfigure(
                hass, entry, _local_input(**{CONF_CLEAR_ACCESS_TOKEN: True})
            )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "unknown"}
        assert entry.data[CONF_ACCESS_TOKEN] == _OLD_TOKEN
        schedule.assert_called_once_with(entry.entry_id)

    async def test_refused_move_cleanup_does_not_schedule_reload(
        self, hass: HomeAssistant, remote_console
    ) -> None:
        entry = _existing_entry()
        remote_console.webhooks.delete.side_effect = UniFiResponseError(
            "refused", status_code=500
        )
        with patch.object(hass.config_entries, "async_schedule_reload") as schedule:
            result = await _reconfigure(
                hass, entry, _local_input(**{CONF_HOST: _NEW_HOST})
            )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "cannot_connect"
        schedule.assert_not_called()

    async def test_cleanup_exception_does_not_schedule_reload(
        self, hass: HomeAssistant, remove_webhook
    ) -> None:
        entry = _existing_entry()
        remove_webhook.side_effect = RuntimeError("cleanup broke")
        with patch.object(hass.config_entries, "async_schedule_reload") as schedule:
            result = await _reconfigure(
                hass, entry, _local_input(**{CONF_HOST: _NEW_HOST})
            )
        assert result["errors"] == {"base": "unknown"}
        schedule.assert_not_called()

    async def test_save_failure_without_cleanup_does_not_schedule_reload(
        self, hass: HomeAssistant
    ) -> None:
        entry = _existing_entry(token=None)
        with (
            patch.object(hass.config_entries, "async_schedule_reload") as schedule,
            patch(
                "homeassistant.config_entries.ConfigFlow.async_update_reload_and_abort",
                side_effect=RuntimeError("storage unavailable"),
            ),
        ):
            result = await _reconfigure(
                hass, entry, _local_input(**{CONF_HOST: _NEW_HOST})
            )
        assert result["errors"] == {"base": "unknown"}
        schedule.assert_not_called()


class TestReauthAccess:
    """Reauth replaces the API key and keeps Access fields and webhook state."""

    async def test_reauth_preserves_access_fields(
        self, hass: HomeAssistant, network_cm, access_client_cls, remove_webhook
    ) -> None:
        entry = _existing_entry(port=8443)
        entry.add_to_hass(hass)
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )
        assert result["step_id"] == "reauth_confirm"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_API_KEY: "rotated-key"}
        )
        assert result["type"] == FlowResultType.ABORT
        assert entry.data[CONF_API_KEY] == "rotated-key"
        assert entry.data[CONF_ACCESS_TOKEN] == _OLD_TOKEN
        assert entry.data[CONF_ACCESS_PORT] == 8443
        assert entry.data[CONF_ACCESS_WEBHOOK_ENDPOINT_ID] == "endpoint-id"
        access_client_cls.assert_not_called()
        remove_webhook.assert_not_called()


class TestOptionsAccessWebhook:
    """The Access webhook toggle is offered only for local entries with a token."""

    async def test_toggle_shown_and_saved_for_token_entry(
        self, hass: HomeAssistant
    ) -> None:
        entry = _existing_entry()
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == FlowResultType.FORM
        assert result["data_schema"] is not None
        assert CONF_ACCESS_WEBHOOK in {
            key.schema for key in result["data_schema"].schema
        }
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={CONF_ACCESS_WEBHOOK: True}
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_ACCESS_WEBHOOK] is True

    async def test_toggle_hidden_without_token(self, hass: HomeAssistant) -> None:
        entry = _existing_entry(token=None)
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == FlowResultType.FORM
        assert result["data_schema"] is not None
        assert CONF_ACCESS_WEBHOOK not in {
            key.schema for key in result["data_schema"].schema
        }
