# Copyright (c) 2026 Ruaan Deysel
# SPDX-License-Identifier: Apache-2.0

"""Tests for UniFi Insights config flow error recovery and retry behavior.

Verifies that every error condition in the config flow (discovery, validation,
reauth, and reconfigure) presents the expected error to the user and can be
successfully recovered from by submitting valid credentials.
"""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_VERIFY_SSL
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiNotFoundError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from unifi_official_api.innerspace import InnerSpaceProject

from custom_components.unifi_insights.config_flow import UnifiInsightsConfigFlow
from custom_components.unifi_insights.const import (
    CONF_ACCESS_TOKEN,
    CONF_CONNECTION_TYPE,
    CONF_CONSOLE_ID,
    CONNECTION_TYPE_LOCAL,
    CONNECTION_TYPE_REMOTE,
    DOMAIN,
)
from tests.fixtures.innerspace import build_innerspace_project

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


# What unifi-official-api 3.0.0 raises for a successful answer whose record
# failed to parse (2.x raised pydantic's ValidationError).
_MALFORMED_SITES = UniFiResponseError(
    "Site response failed validation (id)", status_code=200
)


@pytest.fixture(autouse=True)
def mock_protect_client_flow():
    """Auto-mock Protect client for config flow tests unless overridden."""
    mock_protect_client = MagicMock()
    mock_protect_client.cameras = MagicMock()
    mock_protect_client.cameras.get_all = AsyncMock(return_value=[])
    mock_protect_client.nvr = MagicMock()
    mock_protect_client.nvr.get = AsyncMock(return_value=None)
    mock_protect_client.close = AsyncMock()

    protect_cm = MagicMock()
    protect_cm.__aenter__ = AsyncMock(return_value=mock_protect_client)
    protect_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient",
            return_value=protect_cm,
        ),
        patch(
            "custom_components.unifi_insights.async_setup_entry",
            return_value=True,
        ),
    ):
        yield mock_protect_client


def _make_client_context(
    *,
    get_hosts: object = None,
    get_hosts_side_effect: object = None,
    sites: object = None,
    sites_side_effect: object = None,
    devices: list[object] | None = None,
    devices_side_effect: Exception | None = None,
    enter_side_effect: object = None,
) -> MagicMock:
    """Create an async context manager mock for UniFiNetworkClient."""
    async_cm = MagicMock()

    if enter_side_effect is not None:
        async_cm.__aenter__ = AsyncMock(side_effect=enter_side_effect)
    else:
        client = MagicMock()
        client.get_hosts = AsyncMock(
            side_effect=get_hosts_side_effect,
            return_value=[] if get_hosts is None else get_hosts,
        )
        client.sites = MagicMock()
        client.sites.get_all = AsyncMock(
            side_effect=sites_side_effect,
            return_value=[] if sites is None else sites,
        )
        client.devices = MagicMock()
        client.devices.get_all = AsyncMock(
            side_effect=devices_side_effect,
            return_value=[] if devices is None else devices,
        )
        client.close = AsyncMock()
        async_cm.__aenter__ = AsyncMock(return_value=client)

    async_cm.__aexit__ = AsyncMock(return_value=None)
    return async_cm


def _remote_host(
    host_id: str = "console123",
    hostname: str = "Dream Router 7",
    host_type: str = "console",
) -> dict[str, object]:
    """Create a discovered remote host payload."""
    return {
        "id": host_id,
        "type": host_type,
        "reportedState": {"hostname": hostname},
    }


def test_extract_remote_console_options_skips_invalid_hosts() -> None:
    """Test _extract_remote_console_options skips invalid types or missing IDs."""
    hosts = [
        {"id": None, "type": "console"},
        {"id": "", "type": "console"},
        {"id": 123, "type": "console"},
        {"id": "valid_id_no_type", "type": None},
        {"id": "valid_id_other_type", "type": "gateway"},
        {
            "id": "console_1",
            "type": "console",
            "reportedState": "not-a-dict",
        },
        {
            "id": "console_2",
            "type": "console",
            "reportedState": {"hostname": None},
        },
    ]

    options = UnifiInsightsConfigFlow._extract_remote_console_options(hosts)
    assert options == {
        "console_1": "console_1 (console)",
        "console_2": "console_2 (console)",
    }


def test_extract_remote_console_options_uses_network_servers_when_no_consoles() -> None:
    """Test _extract_remote_console_options falls back to network servers."""
    hosts = [
        {
            "id": "server_1",
            "type": "network-server",
            "reportedState": {"hostname": "Cloud Key Gen2"},
        },
        {
            "id": "server_2",
            "type": "network-server",
            "reportedState": {},
        },
    ]

    options = UnifiInsightsConfigFlow._extract_remote_console_options(hosts)
    assert options == {
        "server_1": "Cloud Key Gen2 (network server)",
        "server_2": "server_2 (network server)",
    }


def test_normalize_remote_console_id_variants() -> None:
    """Test _normalize_remote_console_id for empty, prefix, and missing candidates."""
    discovered = {
        "console-123:site-a": "Console 123 (console)",
        "plain-console": "Plain (console)",
    }

    # Empty and whitespace candidates return None
    assert UnifiInsightsConfigFlow._normalize_remote_console_id("", discovered) is None
    assert (
        UnifiInsightsConfigFlow._normalize_remote_console_id("   ", discovered) is None
    )

    # Exact match (case insensitive)
    assert (
        UnifiInsightsConfigFlow._normalize_remote_console_id(
            "PLAIN-CONSOLE", discovered
        )
        == "plain-console"
    )

    # Prefix match before colon (case insensitive)
    assert (
        UnifiInsightsConfigFlow._normalize_remote_console_id("CONSOLE-123", discovered)
        == "console-123:site-a"
    )

    # Unknown candidate returns None
    assert (
        UnifiInsightsConfigFlow._normalize_remote_console_id("unknown-id", discovered)
        is None
    )


async def test_local_flow_unexpected_exception_recovery(hass: HomeAssistant) -> None:
    """Test local flow recovers from an unexpected Exception."""
    valid_cm = _make_client_context(
        sites=[MagicMock(id="default", name="Default")],
        devices=[],
    )
    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[RuntimeError("Unexpected boom"), valid_cm],
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth") as mock_auth,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "local"

        # First attempt triggers unexpected Exception
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_key",
                CONF_VERIFY_SSL: False,
            },
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "local"
        assert result["errors"] == {"base": "unknown"}

        # Retry with valid input succeeds
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_key",
                CONF_VERIFY_SSL: False,
            },
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "UniFi Insights (Local)"
        mock_auth.assert_called_with(api_key="test_key", verify_ssl=False)
        assert result["data"][CONF_HOST] == "https://192.168.1.1"
        assert result["data"][CONF_API_KEY] == "test_key"


async def test_remote_flow_not_found_error_recovery(hass: HomeAssistant) -> None:
    """Test remote flow recovers when discovery returns 404 (api_unsupported)."""
    discovery_err_cm = _make_client_context(
        get_hosts_side_effect=UniFiNotFoundError("404 Not Found", status_code=404)
    )
    discovery_ok_cm = _make_client_context(
        get_hosts=[_remote_host(host_id="console123", hostname="Console 123")]
    )
    validation_cm = _make_client_context(
        sites=[MagicMock(id="default", name="Default")]
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[discovery_err_cm, discovery_ok_cm, validation_cm],
        ),
        patch(
            "custom_components.unifi_insights.config_flow.ApiKeyAuth"
        ) as mock_api_key_auth,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "remote"

        # First attempt encounters UniFiNotFoundError
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "bad_key"},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "remote"
        assert result["errors"] == {"base": "api_unsupported"}

        # Retry with valid discovery advances to select_console
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: " good_key "},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "select_console"

        # Select console to finish flow
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONSOLE_ID: "console123"},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_API_KEY] == "good_key"
        assert [
            call.kwargs["api_key"] for call in mock_api_key_auth.call_args_list
        ] == ["bad_key", "good_key", "good_key"]


@pytest.mark.parametrize(
    ("remote_api_key", "discovered_consoles"),
    [
        (None, {"console123": "Console 123 (console)"}),
        ("valid_api_key", {}),
    ],
)
async def test_select_console_jumps_to_remote_when_state_missing(
    hass: HomeAssistant,
    remote_api_key: str | None,
    discovered_consoles: dict[str, str],
) -> None:
    """Test select_console falls back to remote step if state is missing."""
    flow = UnifiInsightsConfigFlow()
    flow.hass = hass
    flow._remote_api_key = remote_api_key
    flow._discovered_remote_consoles = discovered_consoles

    result = await flow.async_step_select_console()
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "remote"


@pytest.mark.parametrize(
    ("side_effect_item", "raise_at", "expected_errors"),
    [
        ([], "call", {CONF_CONSOLE_ID: "invalid_console_id"}),
        (
            UniFiAuthenticationError("denied", status_code=403),
            "call",
            {CONF_CONSOLE_ID: "console_access_denied"},
        ),
        (
            UniFiAuthenticationError("unauthorized", status_code=401),
            "call",
            {CONF_CONSOLE_ID: "invalid_console_id"},
        ),
        (
            UniFiAuthenticationError("auth error"),
            "construct",
            {CONF_CONSOLE_ID: "invalid_console_id"},
        ),
        (UniFiConnectionError("conn error"), "call", {"base": "cannot_connect"}),
        (UniFiTimeoutError("timeout"), "construct", {"base": "cannot_connect"}),
        # Raised by the API call, the probe classifies these as not usable,
        # so the step reports an invalid console.
        (
            UniFiNotFoundError("not found", status_code=404),
            "call",
            {CONF_CONSOLE_ID: "invalid_console_id"},
        ),
        (
            _MALFORMED_SITES,
            "call",
            {CONF_CONSOLE_ID: "invalid_console_id"},
        ),
        (RuntimeError("unexpected"), "call", {CONF_CONSOLE_ID: "invalid_console_id"}),
        # Raised while building the client, before the probe runs, these
        # reach the step's own defensive except branches.
        (
            UniFiNotFoundError("not found", status_code=404),
            "construct",
            {"base": "api_unsupported"},
        ),
        (
            _MALFORMED_SITES,
            "construct",
            {"base": "site_parse_error"},
        ),
        (RuntimeError("unexpected"), "construct", {"base": "unknown"}),
    ],
)
async def test_select_console_errors_and_recovery(
    hass: HomeAssistant,
    side_effect_item: object,
    raise_at: str,
    expected_errors: dict[str, str],
) -> None:
    """Test each error during console validation and subsequent recovery."""
    discovery_cm = _make_client_context(
        get_hosts=[_remote_host(host_id="console123", hostname="Dream Router 7")]
    )
    failing_validation_item: object
    if raise_at == "construct":
        failing_validation_item = side_effect_item
    elif isinstance(side_effect_item, Exception):
        failing_validation_item = _make_client_context(
            sites_side_effect=side_effect_item
        )
    else:
        failing_validation_item = _make_client_context(sites=side_effect_item)

    recovering_validation_cm = _make_client_context(
        sites=[MagicMock(id="default", name="Default")]
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[
                discovery_cm,
                failing_validation_item,
                recovering_validation_cm,
            ],
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "valid_api_key"},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "select_console"

        # Submit console selection triggering error
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONSOLE_ID: "console123"},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "select_console"
        assert result["errors"] == expected_errors

        # Resubmit console selection with successful validation
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONSOLE_ID: "console123"},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "UniFi - Dream Router 7"
        assert result["data"][CONF_CONSOLE_ID] == "console123"


@pytest.mark.parametrize(
    ("discovery_side_effect", "expected_errors"),
    [
        ([], {"base": "no_remote_consoles"}),
        ([_remote_host("other_console")], {"base": "invalid_console_id"}),
        (
            UniFiAuthenticationError("auth error"),
            {CONF_API_KEY: "invalid_auth"},
        ),
        (UniFiConnectionError("conn error"), {"base": "cannot_connect"}),
        (UniFiTimeoutError("timeout"), {"base": "cannot_connect"}),
        (
            UniFiNotFoundError("not found", status_code=404),
            {"base": "api_unsupported"},
        ),
        (
            _MALFORMED_SITES,
            {"base": "site_parse_error"},
        ),
        (RuntimeError("unexpected"), {"base": "unknown"}),
    ],
)
async def test_reauth_remote_discovery_errors_and_recovery(
    hass: HomeAssistant,
    discovery_side_effect: object,
    expected_errors: dict[str, str],
) -> None:
    """Test discovery errors in remote reauth and subsequent recovery."""
    remote_entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Insights (Cloud)",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE,
            CONF_CONSOLE_ID: "console123",
            CONF_API_KEY: "old_api_key",
        },
        unique_id="old_api_key",
    )
    remote_entry.add_to_hass(hass)

    if isinstance(discovery_side_effect, Exception):
        failing_discovery_cm = _make_client_context(
            get_hosts_side_effect=discovery_side_effect
        )
    else:
        failing_discovery_cm = _make_client_context(get_hosts=discovery_side_effect)

    recovering_discovery_cm = _make_client_context(
        get_hosts=[_remote_host("console123")]
    )
    validation_cm = _make_client_context(
        sites=[MagicMock(id="default", name="Default")]
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[
                failing_discovery_cm,
                recovering_discovery_cm,
                validation_cm,
            ],
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
    ):
        result = await remote_entry.start_reauth_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        # First attempt with failing discovery
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "bad_api_key"},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"] == expected_errors

        # Recovery attempt with valid discovery and validation
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "new_valid_key"},
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert remote_entry.data[CONF_API_KEY] == "new_valid_key"


@pytest.mark.parametrize(
    ("validation_side_effect", "expected_errors", "raise_at"),
    [
        (
            UniFiAuthenticationError("denied", status_code=403),
            {"base": "console_access_denied"},
            "call",
        ),
        (
            UniFiAuthenticationError("unauthorized", status_code=401),
            {"base": "invalid_console_id"},
            "call",
        ),
        ([], {"base": "invalid_console_id"}, "call"),
        (
            UniFiAuthenticationError("auth error", status_code=401),
            {"base": "invalid_console_id"},
            "construct",
        ),
        (UniFiConnectionError("conn error"), {"base": "cannot_connect"}, "call"),
        (UniFiTimeoutError("timeout"), {"base": "cannot_connect"}, "call"),
        (
            UniFiNotFoundError("not found", status_code=404),
            {"base": "invalid_console_id"},
            "call",
        ),
        (
            _MALFORMED_SITES,
            {"base": "invalid_console_id"},
            "call",
        ),
        (RuntimeError("unexpected"), {"base": "invalid_console_id"}, "call"),
    ],
)
async def test_reauth_remote_validation_errors_and_recovery(
    hass: HomeAssistant,
    validation_side_effect: object,
    expected_errors: dict[str, str],
    raise_at: str,
) -> None:
    """Test validation errors in remote reauth and subsequent recovery."""
    remote_entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Insights (Cloud)",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE,
            CONF_CONSOLE_ID: "console123",
            CONF_API_KEY: "old_api_key",
        },
        unique_id="old_api_key",
    )
    remote_entry.add_to_hass(hass)
    original_data = dict(remote_entry.data)
    original_unique_id = remote_entry.unique_id

    discovery_cm_1 = _make_client_context(get_hosts=[_remote_host("console123")])
    if raise_at == "construct":
        failing_validation_cm = validation_side_effect
    elif isinstance(validation_side_effect, Exception):
        failing_validation_cm = _make_client_context(
            sites_side_effect=validation_side_effect
        )
    else:
        failing_validation_cm = _make_client_context(sites=validation_side_effect)

    discovery_cm_2 = _make_client_context(get_hosts=[_remote_host("console123")])
    recovering_validation_cm = _make_client_context(
        sites=[MagicMock(id="default", name="Default")]
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[
                discovery_cm_1,
                failing_validation_cm,
                discovery_cm_2,
                recovering_validation_cm,
            ],
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
    ):
        result = await remote_entry.start_reauth_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        # First attempt with failing validation
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "key_with_validation_failure"},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"] == expected_errors
        assert dict(remote_entry.data) == original_data
        assert remote_entry.unique_id == original_unique_id

        # Recovery attempt
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "new_valid_key"},
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert remote_entry.data[CONF_API_KEY] == "new_valid_key"


@pytest.mark.parametrize(
    ("discovery_side_effect", "input_console_id", "expected_errors"),
    [
        ([], "console123", {"base": "no_remote_consoles"}),
        (
            [_remote_host("console123")],
            "unmatched_console",
            {CONF_CONSOLE_ID: "invalid_console_id"},
        ),
        (
            UniFiAuthenticationError("auth error"),
            "console123",
            {CONF_API_KEY: "invalid_auth"},
        ),
        (UniFiConnectionError("conn error"), "console123", {"base": "cannot_connect"}),
        (UniFiTimeoutError("timeout"), "console123", {"base": "cannot_connect"}),
        (
            UniFiNotFoundError("not found", status_code=404),
            "console123",
            {"base": "api_unsupported"},
        ),
        (
            _MALFORMED_SITES,
            "console123",
            {"base": "site_parse_error"},
        ),
        (RuntimeError("unexpected"), "console123", {"base": "unknown"}),
    ],
)
async def test_reconfigure_remote_discovery_errors_and_recovery(
    hass: HomeAssistant,
    discovery_side_effect: object,
    input_console_id: str,
    expected_errors: dict[str, str],
) -> None:
    """Test discovery errors in remote reconfigure and subsequent recovery."""
    remote_entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Insights (Cloud)",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE,
            CONF_CONSOLE_ID: "console123",
            CONF_API_KEY: "old_api_key",
        },
        unique_id="old_api_key",
    )
    remote_entry.add_to_hass(hass)

    if isinstance(discovery_side_effect, Exception):
        failing_discovery_cm = _make_client_context(
            get_hosts_side_effect=discovery_side_effect
        )
    else:
        failing_discovery_cm = _make_client_context(get_hosts=discovery_side_effect)

    recovering_discovery_cm = _make_client_context(
        get_hosts=[_remote_host("console123")]
    )
    validation_cm = _make_client_context(
        sites=[MagicMock(id="default", name="Default")]
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[
                failing_discovery_cm,
                recovering_discovery_cm,
                validation_cm,
            ],
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
    ):
        result = await remote_entry.start_reconfigure_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

        # First attempt with failing discovery
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_CONSOLE_ID: input_console_id,
                CONF_API_KEY: "bad_api_key",
            },
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure"
        assert result["errors"] == expected_errors

        # Recovery attempt
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_CONSOLE_ID: "console123",
                CONF_API_KEY: "new_valid_key",
            },
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert remote_entry.data[CONF_API_KEY] == "new_valid_key"
        assert remote_entry.data[CONF_CONSOLE_ID] == "console123"


@pytest.mark.parametrize(
    ("validation_side_effect", "expected_errors"),
    [
        (
            UniFiAuthenticationError("denied", status_code=403),
            {CONF_CONSOLE_ID: "console_access_denied"},
        ),
        (
            UniFiAuthenticationError("unauthorized", status_code=401),
            {CONF_CONSOLE_ID: "invalid_console_id"},
        ),
        ([], {CONF_CONSOLE_ID: "invalid_console_id"}),
        (
            UniFiAuthenticationError("auth error"),
            {CONF_CONSOLE_ID: "invalid_console_id"},
        ),
        (UniFiConnectionError("conn error"), {"base": "cannot_connect"}),
        (UniFiTimeoutError("timeout"), {"base": "cannot_connect"}),
        (
            UniFiNotFoundError("not found", status_code=404),
            {CONF_CONSOLE_ID: "invalid_console_id"},
        ),
        (
            _MALFORMED_SITES,
            {CONF_CONSOLE_ID: "invalid_console_id"},
        ),
        (RuntimeError("unexpected"), {CONF_CONSOLE_ID: "invalid_console_id"}),
    ],
)
async def test_reconfigure_remote_validation_errors_and_recovery(
    hass: HomeAssistant,
    validation_side_effect: object,
    expected_errors: dict[str, str],
) -> None:
    """Test validation errors in remote reconfigure and subsequent recovery."""
    remote_entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Insights (Cloud)",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE,
            CONF_CONSOLE_ID: "console123",
            CONF_API_KEY: "old_api_key",
        },
        unique_id="old_api_key",
    )
    remote_entry.add_to_hass(hass)
    original_data = dict(remote_entry.data)
    original_unique_id = remote_entry.unique_id

    discovery_cm_1 = _make_client_context(get_hosts=[_remote_host("console123")])
    if isinstance(validation_side_effect, Exception):
        failing_validation_cm = _make_client_context(
            sites_side_effect=validation_side_effect
        )
    else:
        failing_validation_cm = _make_client_context(sites=validation_side_effect)

    discovery_cm_2 = _make_client_context(get_hosts=[_remote_host("console123")])
    recovering_validation_cm = _make_client_context(
        sites=[MagicMock(id="default", name="Default")]
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[
                discovery_cm_1,
                failing_validation_cm,
                discovery_cm_2,
                recovering_validation_cm,
            ],
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
    ):
        result = await remote_entry.start_reconfigure_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

        # First attempt with failing validation
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_CONSOLE_ID: "console123",
                CONF_API_KEY: "key_with_validation_failure",
            },
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure"
        assert result["errors"] == expected_errors
        assert dict(remote_entry.data) == original_data
        assert remote_entry.unique_id == original_unique_id

        # Recovery attempt
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_CONSOLE_ID: "console123",
                CONF_API_KEY: "new_valid_key",
            },
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert remote_entry.data[CONF_API_KEY] == "new_valid_key"
        assert remote_entry.data[CONF_CONSOLE_ID] == "console123"


@pytest.mark.parametrize(
    ("network_error", "protect_error", "expected_error", "innerspace_error"),
    [
        (
            UniFiAuthenticationError("denied", status_code=403),
            UniFiConnectionError("unreachable"),
            "cannot_connect",
            None,
        ),
        (
            UniFiAuthenticationError("denied", status_code=403),
            UniFiNotFoundError("absent", status_code=404),
            "console_access_denied",
            None,
        ),
        (
            UniFiAuthenticationError("denied", status_code=403),
            None,
            "console_access_denied",
            None,
        ),
        (
            UniFiAuthenticationError("unauthorized", status_code=401),
            None,
            "invalid_console_id",
            None,
        ),
        (
            None,
            UniFiAuthenticationError("denied", status_code=403),
            "console_access_denied",
            None,
        ),
        (
            UniFiConnectionError("unreachable"),
            UniFiAuthenticationError("denied", status_code=403),
            "cannot_connect",
            None,
        ),
        (
            UniFiAuthenticationError("denied", status_code=403),
            UniFiAuthenticationError("denied", status_code=403),
            "console_access_denied",
            None,
        ),
        (
            UniFiAuthenticationError("unauthorized", status_code=401),
            None,
            "console_access_denied",
            UniFiAuthenticationError("denied", status_code=403),
        ),
        (
            UniFiAuthenticationError("denied", status_code=403),
            UniFiAuthenticationError("denied", status_code=403),
            "console_access_denied",
            UniFiAuthenticationError("denied", status_code=403),
        ),
        (
            UniFiAuthenticationError("denied", status_code=403),
            None,
            "cannot_connect",
            UniFiTimeoutError("InnerSpace timeout"),
        ),
    ],
)
async def test_remote_validator_permission_precedence(
    *,
    hass: HomeAssistant,
    mock_protect_client_flow: MagicMock,
    mock_innerspace_client: MagicMock,
    network_error: Exception | None,
    protect_error: Exception | None,
    expected_error: str,
    innerspace_error: Exception | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """API outcomes preserve transient-error precedence over confirmed denials."""
    flow = UnifiInsightsConfigFlow()
    flow.hass = hass
    network_cm = _make_client_context(sites_side_effect=network_error)
    mock_protect_client_flow.cameras.get_all.side_effect = protect_error
    if innerspace_error is not None:
        mock_innerspace_client.get_project.side_effect = innerspace_error
    with patch(
        "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
        return_value=network_cm,
    ):
        if expected_error == "cannot_connect":
            with pytest.raises(UniFiConnectionError):
                await flow._async_validate_remote_console("synthetic-key", "console123")
        else:
            assert (
                await flow._async_validate_remote_console("synthetic-key", "console123")
                == expected_error
            )
    denied_logs = [
        record.message
        for record in caplog.records
        if record.name == "custom_components.unifi_insights.config_flow"
    ]
    if expected_error == "console_access_denied":
        applications = ", ".join(
            name
            for name, error in (
                ("Network", network_error),
                ("Protect", protect_error),
                ("InnerSpace", innerspace_error),
            )
            if isinstance(error, UniFiAuthenticationError) and error.status_code == 403
        )
        expected = (
            f"Remote console validation: {applications} refused the API key "
            "with HTTP 403"
        )
        assert denied_logs == [expected]
    else:
        assert denied_logs == []


@pytest.mark.parametrize(
    ("innerspace_side_effect", "expected_errors"),
    [
        (UniFiTimeoutError("timeout"), {"base": "cannot_connect"}),
        (
            UniFiAuthenticationError("denied", status_code=403),
            {CONF_CONSOLE_ID: "console_access_denied"},
        ),
        (
            [],  # empty
            {CONF_CONSOLE_ID: "invalid_console_id"},
        ),
    ],
    ids=["timeout", "403", "empty"],
)
async def test_select_console_innerspace_errors_and_recovery(
    hass: HomeAssistant,
    mock_innerspace_client: MagicMock,
    innerspace_side_effect: object,
    expected_errors: dict[str, str],
) -> None:
    """Test console validation errors from InnerSpace and subsequent recovery."""
    discovery_cm = _make_client_context(
        get_hosts=[_remote_host(host_id="console123", hostname="Dream Router 7")]
    )
    if isinstance(innerspace_side_effect, Exception):
        mock_innerspace_client.get_project.side_effect = innerspace_side_effect
    else:
        mock_innerspace_client.get_project.return_value = InnerSpaceProject()

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[
                discovery_cm,
                _make_client_context(sites=[]),
                _make_client_context(sites=[]),
            ],
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
        patch(
            "custom_components.unifi_insights.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "valid_api_key"},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "select_console"

        # Submit console selection triggering InnerSpace error
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONSOLE_ID: "console123"},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "select_console"
        assert result["errors"] == expected_errors

        # Recovery attempt: InnerSpace now succeeds
        mock_innerspace_client.get_project.side_effect = None
        mock_innerspace_client.get_project.return_value = build_innerspace_project()

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONSOLE_ID: "console123"},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "UniFi - Dream Router 7"
        assert result["data"][CONF_CONSOLE_ID] == "console123"


async def test_reauth_local_innerspace_only_success_and_failure(
    hass: HomeAssistant,
    mock_innerspace_client: MagicMock,
) -> None:
    """Test local reauth preserves safe identity on success.

    Retains data on failure.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Insights (Local)",
        unique_id="https://192.168.1.1",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.1",
            CONF_API_KEY: "old_key",
            CONF_VERIFY_SSL: False,
            CONF_CONSOLE_ID: "https://192.168.1.1",
        },
    )
    entry.add_to_hass(hass)
    original_data = dict(entry.data)
    original_unique_id = entry.unique_id

    # 1. Failure attempt: InnerSpace 401
    mock_innerspace_client.get_project.side_effect = UniFiAuthenticationError(
        "denied", status_code=401
    )
    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=_make_client_context(sites=[]),
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
        patch("custom_components.unifi_insights.async_setup_entry", return_value=True),
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "bad_key"},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_API_KEY: "invalid_auth"}
        assert dict(entry.data) == original_data
        assert entry.unique_id == original_unique_id

        # 2. Failure attempt: InnerSpace timeout
        mock_innerspace_client.get_project.side_effect = UniFiTimeoutError("timeout")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "timeout_key"},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}
        assert dict(entry.data) == original_data

        # 3. Success attempt: InnerSpace available
        mock_innerspace_client.get_project.side_effect = None
        mock_innerspace_client.get_project.return_value = build_innerspace_project()
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "new_good_key"},
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert entry.data[CONF_API_KEY] == "new_good_key"
        assert entry.data[CONF_CONSOLE_ID] == "https://192.168.1.1"
        assert entry.unique_id == original_unique_id


async def test_reauth_remote_innerspace_only_success_and_failure(
    hass: HomeAssistant,
    mock_innerspace_client: MagicMock,
) -> None:
    """Test remote reauth with InnerSpace-only console."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Insights (Remote)",
        unique_id="console123",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE,
            CONF_CONSOLE_ID: "console123",
            CONF_API_KEY: "old_key",
        },
    )
    entry.add_to_hass(hass)
    original_data = dict(entry.data)

    def _network_side_effect(*args, **kwargs):
        client = MagicMock()
        client.get_hosts = AsyncMock(return_value=[_remote_host("console123")])
        client.sites = MagicMock()
        client.sites.get_all = AsyncMock(return_value=[])
        client.close = AsyncMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=_network_side_effect,
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
        patch("custom_components.unifi_insights.async_setup_entry", return_value=True),
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        # 1. 403 failure
        mock_innerspace_client.get_project.side_effect = UniFiAuthenticationError(
            "denied", status_code=403
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "denied_key"},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "console_access_denied"}
        assert dict(entry.data) == original_data

        # 2. Timeout failure
        mock_innerspace_client.get_project.side_effect = UniFiTimeoutError("timeout")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "timeout_key"},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}
        assert dict(entry.data) == original_data

        # 3. Success
        mock_innerspace_client.get_project.side_effect = None
        mock_innerspace_client.get_project.return_value = build_innerspace_project()
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "new_good_key"},
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert entry.data[CONF_API_KEY] == "new_good_key"


async def test_reconfigure_local_innerspace_only(
    hass: HomeAssistant,
    mock_innerspace_client: MagicMock,
) -> None:
    """Test local reconfigure for InnerSpace-only console.

    Covers adoption, collision, unsafe host and failure.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Insights (Local)",
        unique_id="192.168.1.10",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "192.168.1.10",
            CONF_API_KEY: "old_key",
            CONF_VERIFY_SSL: False,
        },
    )
    entry.add_to_hass(hass)
    original_data = dict(entry.data)
    original_unique_id = entry.unique_id

    mock_innerspace_client.get_project.return_value = build_innerspace_project()

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=_make_client_context(sites=[]),
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
        patch("custom_components.unifi_insights.async_setup_entry", return_value=True),
    ):
        # 1. Unsafe host (host == api_key)
        result = await entry.start_reconfigure_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "new_key",
                CONF_API_KEY: "new_key",
                CONF_VERIFY_SSL: False,
            },
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "console_identity_unavailable"}
        assert dict(entry.data) == original_data
        assert entry.unique_id == original_unique_id

        # 1b. Unsafe host (host == a newly typed Access token)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "typed-access-token",
                CONF_API_KEY: "new_key",
                CONF_ACCESS_TOKEN: "typed-access-token",
                CONF_VERIFY_SSL: False,
            },
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "console_identity_unavailable"}
        assert dict(entry.data) == original_data
        assert entry.unique_id == original_unique_id

        # 2. Failure: timeout
        mock_innerspace_client.get_project.side_effect = UniFiTimeoutError("timeout")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "192.168.1.20",
                CONF_API_KEY: "new_key",
                CONF_VERIFY_SSL: False,
            },
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}
        assert dict(entry.data) == original_data
        assert entry.unique_id == original_unique_id

    # 3. Collision and adoption when entry has no current ID
    no_id_entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Insights (Local)",
        unique_id="old_credential_key",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "192.168.1.30",
            CONF_API_KEY: "old_credential_key",
            CONF_VERIFY_SSL: False,
        },
    )
    no_id_entry.add_to_hass(hass)

    other_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="192.168.1.50",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "192.168.1.50",
            CONF_API_KEY: "other_key",
        },
    )
    other_entry.add_to_hass(hass)

    mock_innerspace_client.get_project.side_effect = None
    mock_innerspace_client.get_project.return_value = build_innerspace_project()

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=_make_client_context(sites=[]),
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
        patch("custom_components.unifi_insights.async_setup_entry", return_value=True),
    ):
        # Collision with other_entry's unique_id
        result = await no_id_entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "192.168.1.50",
                CONF_API_KEY: "new_key",
                CONF_VERIFY_SSL: False,
            },
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "already_configured"

        # Adoption when no current ID
        result = await no_id_entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "192.168.1.40",
                CONF_API_KEY: "new_key",
                CONF_VERIFY_SSL: True,
            },
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert no_id_entry.data[CONF_HOST] == "192.168.1.40"
        assert no_id_entry.data[CONF_API_KEY] == "new_key"
        assert no_id_entry.data[CONF_CONSOLE_ID] == "192.168.1.40"


async def test_reconfigure_remote_innerspace_only(
    hass: HomeAssistant,
    mock_innerspace_client: MagicMock,
) -> None:
    """Test remote reconfigure with InnerSpace errors and success."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Insights (Remote)",
        unique_id="console123",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE,
            CONF_CONSOLE_ID: "console123",
            CONF_API_KEY: "old_key",
        },
    )
    entry.add_to_hass(hass)
    original_data = dict(entry.data)

    def _network_side_effect(*args, **kwargs):
        client = MagicMock()
        client.get_hosts = AsyncMock(return_value=[_remote_host("console123")])
        client.sites = MagicMock()
        client.sites.get_all = AsyncMock(return_value=[])
        client.close = AsyncMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=_network_side_effect,
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
        patch("custom_components.unifi_insights.async_setup_entry", return_value=True),
    ):
        result = await entry.start_reconfigure_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

        # 1. 403 error placed on CONF_CONSOLE_ID
        mock_innerspace_client.get_project.side_effect = UniFiAuthenticationError(
            "denied", status_code=403
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_CONSOLE_ID: "console123",
                CONF_API_KEY: "key_with_403",
            },
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_CONSOLE_ID: "console_access_denied"}
        assert dict(entry.data) == original_data

        # 2. Timeout error placed on base
        mock_innerspace_client.get_project.side_effect = UniFiTimeoutError("timeout")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_CONSOLE_ID: "console123",
                CONF_API_KEY: "key_with_timeout",
            },
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}
        assert dict(entry.data) == original_data

        # 3. Success
        mock_innerspace_client.get_project.side_effect = None
        mock_innerspace_client.get_project.return_value = build_innerspace_project()
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_CONSOLE_ID: "console123",
                CONF_API_KEY: "new_good_key",
            },
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert entry.data[CONF_API_KEY] == "new_good_key"
