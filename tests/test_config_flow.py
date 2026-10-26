# Copyright 2026 UniFi Insights contributors
"""Tests for the UniFi Insights config flow."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_VERIFY_SSL
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiNotFoundError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from unifi_official_api.network.models.site import Site

from custom_components.unifi_insights.config_flow import (
    UnifiInsightsConfigFlow,
    UnifiInsightsOptionsFlow,
)
from custom_components.unifi_insights.const import (
    CONF_CONNECTION_TYPE,
    CONF_CONSOLE_ID,
    CONF_SITE_IDS,
    CONNECTION_TYPE_LOCAL,
    CONNECTION_TYPE_REMOTE,
    DOMAIN,
)
from tests.fixtures.assertions import require_not_none

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# All tests require custom_integrations to be enabled
pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


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

    with patch(
        "custom_components.unifi_insights.config_flow.UniFiProtectClient",
        return_value=protect_cm,
    ):
        yield mock_protect_client


def _make_client_context(
    *,
    get_hosts: object = None,
    get_hosts_side_effect: object = None,
    sites: object = None,
    sites_side_effect: object = None,
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


async def test_user_flow_shows_connection_type_selection(hass: HomeAssistant) -> None:
    """Test user flow shows connection type selection."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_local_flow_success(hass: HomeAssistant) -> None:
    """Test successful local connection flow."""
    # Create a mock client
    mock_client = MagicMock()
    mock_client.sites = MagicMock()
    mock_client.sites.get_all = AsyncMock(
        return_value=[MagicMock(id="default", name="Default")]
    )
    mock_client.close = AsyncMock()

    # Create async context manager mock
    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(return_value=mock_client)
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
        # Prevent the real integration setup (which opens sockets) when the
        # entry is created/reloaded by the flow.
        patch(
            "custom_components.unifi_insights.async_setup_entry",
            return_value=True,
        ),
    ):
        # Start the flow
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        # Select local connection
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "local"

        # Enter local connection details
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_api_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "UniFi Insights (Local)"
        assert result["data"] == {
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.1",
            CONF_API_KEY: "test_api_key",
            CONF_VERIFY_SSL: False,
        }


async def test_local_flow_auth_error(hass: HomeAssistant) -> None:
    """Test local flow with authentication error."""
    # Create async context manager mock that raises auth error
    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(
        side_effect=UniFiAuthenticationError("Invalid credentials")
    )
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        # Select local connection
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL},
        )

        # Enter local connection details
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "bad_api_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_API_KEY: "invalid_auth"}


async def test_local_flow_connection_error(hass: HomeAssistant) -> None:
    """Test local flow with connection error."""
    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(side_effect=UniFiConnectionError("Cannot connect"))
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL},
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_api_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}


async def test_local_flow_timeout_error(hass: HomeAssistant) -> None:
    """Test local flow with timeout error."""
    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(side_effect=UniFiTimeoutError("Timeout"))
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL},
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_api_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}


async def test_local_flow_unknown_error(hass: HomeAssistant) -> None:
    """Test local flow with unknown error."""
    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(side_effect=Exception("Unknown error"))
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL},
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_api_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "unknown"}


async def test_local_flow_success_with_missing_site_id(
    hass: HomeAssistant,
) -> None:
    """Test local flow succeeds with Dream 7 site payload missing id (Issue 80)."""
    site = Site.model_validate({"internalReference": "default", "name": "Default"})
    mock_client = MagicMock()
    mock_client.sites = MagicMock()
    mock_client.sites.get_all = AsyncMock(return_value=[site])
    mock_client.close = AsyncMock()

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(return_value=mock_client)
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
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
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_api_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "UniFi Insights (Local)"


async def test_local_flow_success_protect_only_console(
    hass: HomeAssistant,
) -> None:
    """Test local flow succeeds for Protect-only consoles like UNVR (Issue 93)."""
    mock_net_client = MagicMock()
    mock_net_client.sites = MagicMock()
    mock_net_client.sites.get_all = AsyncMock(return_value=[])

    net_cm = MagicMock()
    net_cm.__aenter__ = AsyncMock(return_value=mock_net_client)
    net_cm.__aexit__ = AsyncMock(return_value=None)

    mock_protect_client = MagicMock()
    mock_protect_client.cameras = MagicMock()
    mock_protect_client.cameras.get_all = AsyncMock(
        return_value=[MagicMock(id="cam-1", name="Doorbell")]
    )

    protect_cm = MagicMock()
    protect_cm.__aenter__ = AsyncMock(return_value=mock_protect_client)
    protect_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm,
        ),
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient",
            return_value=protect_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
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
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_api_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "UniFi Insights (Local)"


async def test_local_flow_success_protect_only_nvr(
    hass: HomeAssistant,
) -> None:
    """Test local flow succeeds when Protect has 0 cameras but NVR is online."""
    mock_net_client = MagicMock()
    mock_net_client.sites = MagicMock()
    mock_net_client.sites.get_all = AsyncMock(return_value=[])

    net_cm = MagicMock()
    net_cm.__aenter__ = AsyncMock(return_value=mock_net_client)
    net_cm.__aexit__ = AsyncMock(return_value=None)

    mock_protect_client = MagicMock()
    mock_protect_client.cameras = MagicMock()
    mock_protect_client.cameras.get_all = AsyncMock(return_value=[])
    mock_protect_client.nvr = MagicMock()
    mock_protect_client.nvr.get = AsyncMock(
        return_value=MagicMock(id="nvr-1", name="UNVR Instant")
    )

    protect_cm = MagicMock()
    protect_cm.__aenter__ = AsyncMock(return_value=mock_protect_client)
    protect_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm,
        ),
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient",
            return_value=protect_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
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
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_api_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "UniFi Insights (Local)"


async def test_local_flow_api_unsupported_both_clients(
    hass: HomeAssistant,
) -> None:
    """Test local flow returns api_unsupported when both clients return 404."""
    net_cm = MagicMock()
    net_cm.__aenter__ = AsyncMock(
        side_effect=UniFiNotFoundError("Not found", status_code=404)
    )
    net_cm.__aexit__ = AsyncMock(return_value=None)

    protect_cm = MagicMock()
    protect_cm.__aenter__ = AsyncMock(
        side_effect=UniFiNotFoundError("Not found", status_code=404)
    )
    protect_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm,
        ),
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient",
            return_value=protect_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_api_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "api_unsupported"}


async def test_remote_flow_success(hass: HomeAssistant) -> None:
    """Test successful remote connection flow."""
    discovery_cm = _make_client_context(get_hosts=[_remote_host()])
    validation_cm = _make_client_context(
        sites=[MagicMock(id="default", name="Default")]
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[discovery_cm, validation_cm],
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        # Select remote connection
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "remote"

        # Enter remote API key
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "test_api_key"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "select_console"

        # Select discovered remote console
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONSOLE_ID: "console123"},
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "UniFi - Dream Router 7"
        assert result["data"] == {
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE,
            CONF_CONSOLE_ID: "console123",
            CONF_API_KEY: "test_api_key",
        }


async def test_remote_flow_success_protect_only_console(
    hass: HomeAssistant,
) -> None:
    """Test remote flow succeeds for Protect-only console."""
    discovery_cm = _make_client_context(get_hosts=[_remote_host()])
    validation_net_cm = _make_client_context(sites=[])

    protect_mock = MagicMock()
    protect_mock.cameras = MagicMock()
    protect_mock.cameras.get_all = AsyncMock(
        return_value=[MagicMock(id="cam1", name="Driveway")]
    )
    protect_cm = MagicMock()
    protect_cm.__aenter__ = AsyncMock(return_value=protect_mock)
    protect_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[discovery_cm, validation_net_cm],
        ),
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient",
            return_value=protect_cm,
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
            user_input={CONF_API_KEY: "test_api_key"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONSOLE_ID: "console123"},
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "UniFi - Dream Router 7"


async def test_remote_flow_connection_error_on_protect(
    hass: HomeAssistant,
) -> None:
    """Test remote console validation propagates connection error from Protect."""
    discovery_cm = _make_client_context(get_hosts=[_remote_host()])
    validation_net_cm = _make_client_context(
        sites_side_effect=UniFiNotFoundError("Not found", status_code=404)
    )

    protect_cm = MagicMock()
    protect_cm.__aenter__ = AsyncMock(
        side_effect=UniFiConnectionError("Remote Protect unreachable")
    )
    protect_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[discovery_cm, validation_net_cm],
        ),
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient",
            return_value=protect_cm,
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
            user_input={CONF_API_KEY: "test_api_key"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONSOLE_ID: "console123"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}


async def test_local_flow_protect_auth_error(
    hass: HomeAssistant,
) -> None:
    """Test local flow when Network returns empty and Protect returns auth error."""
    mock_net_client = MagicMock()
    mock_net_client.sites = MagicMock()
    mock_net_client.sites.get_all = AsyncMock(return_value=[])

    net_cm = MagicMock()
    net_cm.__aenter__ = AsyncMock(return_value=mock_net_client)
    net_cm.__aexit__ = AsyncMock(return_value=None)

    protect_cm = MagicMock()
    protect_cm.__aenter__ = AsyncMock(
        side_effect=UniFiAuthenticationError("Protect auth failed")
    )
    protect_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm,
        ),
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient",
            return_value=protect_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_api_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_API_KEY: "invalid_auth"}


async def test_local_flow_protect_timeout_error(
    hass: HomeAssistant,
) -> None:
    """Test local flow when Network returns empty and Protect times out."""
    mock_net_client = MagicMock()
    mock_net_client.sites = MagicMock()
    mock_net_client.sites.get_all = AsyncMock(return_value=[])

    net_cm = MagicMock()
    net_cm.__aenter__ = AsyncMock(return_value=mock_net_client)
    net_cm.__aexit__ = AsyncMock(return_value=None)

    protect_cm = MagicMock()
    protect_cm.__aenter__ = AsyncMock(
        side_effect=UniFiTimeoutError("Protect timed out")
    )
    protect_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm,
        ),
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient",
            return_value=protect_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_api_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}


async def test_remote_flow_success_protect_nvr(
    hass: HomeAssistant,
) -> None:
    """Test remote flow succeeds when Protect has 0 cameras but NVR is online."""
    discovery_cm = _make_client_context(get_hosts=[_remote_host()])
    validation_net_cm = _make_client_context(sites=[])

    protect_mock = MagicMock()
    protect_mock.cameras = MagicMock()
    protect_mock.cameras.get_all = AsyncMock(return_value=[])
    protect_mock.nvr = MagicMock()
    protect_mock.nvr.get = AsyncMock(return_value=MagicMock(id="nvr1", name="UNVR"))
    protect_cm = MagicMock()
    protect_cm.__aenter__ = AsyncMock(return_value=protect_mock)
    protect_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[discovery_cm, validation_net_cm],
        ),
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient",
            return_value=protect_cm,
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
            user_input={CONF_API_KEY: "test_api_key"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONSOLE_ID: "console123"},
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "UniFi - Dream Router 7"


async def test_remote_flow_protect_timeout_error(
    hass: HomeAssistant,
) -> None:
    """Test remote flow propagates timeout error when Protect times out."""
    discovery_cm = _make_client_context(get_hosts=[_remote_host()])
    validation_net_cm = _make_client_context(sites=[])

    protect_cm = MagicMock()
    protect_cm.__aenter__ = AsyncMock(
        side_effect=UniFiTimeoutError("Remote Protect timed out")
    )
    protect_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[discovery_cm, validation_net_cm],
        ),
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient",
            return_value=protect_cm,
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
            user_input={CONF_API_KEY: "test_api_key"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONSOLE_ID: "console123"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_flow_success(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test successful reauth flow."""
    mock_config_entry.add_to_hass(hass)

    mock_client = MagicMock()
    mock_client.sites = MagicMock()
    mock_client.sites.get_all = AsyncMock(
        return_value=[MagicMock(id="default", name="Default")]
    )
    mock_client.close = AsyncMock()

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(return_value=mock_client)
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
        # Prevent the real integration setup (which opens sockets) when the
        # entry is created/reloaded by the flow.
        patch(
            "custom_components.unifi_insights.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await mock_config_entry.start_reauth_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "new_api_key"},
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"


async def test_reauth_flow_auth_failed(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test reauth flow with authentication failure."""
    mock_config_entry.add_to_hass(hass)

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(
        side_effect=UniFiAuthenticationError("Invalid credentials")
    )
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await mock_config_entry.start_reauth_flow(hass)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "wrong_key"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_API_KEY: "invalid_auth"}


async def test_local_flow_no_sites_found(hass: HomeAssistant) -> None:
    """Test local flow when no sites are found."""
    mock_client = MagicMock()
    mock_client.sites = MagicMock()
    mock_client.sites.get_all = AsyncMock(return_value=[])  # No sites
    mock_client.close = AsyncMock()

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(return_value=mock_client)
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL},
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_api_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_API_KEY: "invalid_auth"}


async def test_remote_flow_auth_error(hass: HomeAssistant) -> None:
    """Test remote flow with authentication error."""
    discovery_cm = _make_client_context(
        enter_side_effect=UniFiAuthenticationError("Invalid credentials")
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=discovery_cm,
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
            user_input={CONF_API_KEY: "bad_api_key"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_API_KEY: "invalid_auth"}


async def test_remote_flow_connection_error(hass: HomeAssistant) -> None:
    """Test remote flow with connection error."""
    discovery_cm = _make_client_context(
        enter_side_effect=UniFiConnectionError("Cannot connect")
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=discovery_cm,
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
            user_input={CONF_API_KEY: "test_api_key"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}


async def test_remote_flow_timeout_error(hass: HomeAssistant) -> None:
    """Test remote flow with timeout error."""
    discovery_cm = _make_client_context(enter_side_effect=UniFiTimeoutError("Timeout"))

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=discovery_cm,
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
            user_input={CONF_API_KEY: "test_api_key"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}


async def test_remote_flow_unknown_error(hass: HomeAssistant) -> None:
    """Test remote flow with unknown error."""
    discovery_cm = _make_client_context(enter_side_effect=Exception("Unknown error"))

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=discovery_cm,
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
            user_input={CONF_API_KEY: "test_api_key"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "unknown"}


async def test_remote_flow_no_sites_found(hass: HomeAssistant) -> None:
    """Test remote flow when no sites are found."""
    discovery_cm = _make_client_context(get_hosts=[_remote_host()])
    validation_cm = _make_client_context(sites=[])

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[discovery_cm, validation_cm],
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
            user_input={CONF_API_KEY: "test_api_key"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "select_console"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONSOLE_ID: "console123"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_CONSOLE_ID: "invalid_console_id"}


async def test_remote_flow_no_remote_consoles(hass: HomeAssistant) -> None:
    """Test remote flow when the API key has no accessible consoles."""
    discovery_cm = _make_client_context(get_hosts=[])

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=discovery_cm,
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
            user_input={CONF_API_KEY: "test_api_key"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "no_remote_consoles"}


async def test_reauth_flow_remote_success(hass: HomeAssistant) -> None:
    """Test successful reauth flow for remote connection."""
    # Create a remote config entry
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

    discovery_cm = _make_client_context(get_hosts=[_remote_host()])
    validation_cm = _make_client_context(
        sites=[MagicMock(id="default", name="Default")]
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[discovery_cm, validation_cm],
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
    ):
        result = await remote_entry.start_reauth_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "new_api_key"},
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"


async def test_reauth_flow_connection_error(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test reauth flow with connection error."""
    mock_config_entry.add_to_hass(hass)

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(side_effect=UniFiConnectionError("Cannot connect"))
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await mock_config_entry.start_reauth_flow(hass)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "test_api_key"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_flow_timeout_error(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test reauth flow with timeout error."""
    mock_config_entry.add_to_hass(hass)

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(side_effect=UniFiTimeoutError("Timeout"))
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await mock_config_entry.start_reauth_flow(hass)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "test_api_key"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_flow_unknown_error(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test reauth flow with unknown error."""
    mock_config_entry.add_to_hass(hass)

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(side_effect=Exception("Unknown error"))
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await mock_config_entry.start_reauth_flow(hass)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "test_api_key"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "unknown"}


async def test_reauth_flow_no_sites_found(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test reauth flow when no sites are found."""
    mock_config_entry.add_to_hass(hass)

    mock_client = MagicMock()
    mock_client.sites = MagicMock()
    mock_client.sites.get_all = AsyncMock(return_value=[])
    mock_client.close = AsyncMock()

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(return_value=mock_client)
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await mock_config_entry.start_reauth_flow(hass)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "test_api_key"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_API_KEY: "invalid_auth"}


async def test_reauth_flow_remote_no_sites_found(
    hass: HomeAssistant,
) -> None:
    """Test reauth flow for remote connection when no sites are found."""
    # Create remote config entry
    remote_entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Insights (Cloud)",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE,
            CONF_CONSOLE_ID: "test_console",
            CONF_API_KEY: "test_api_key",
        },
        unique_id="test_api_key",
    )
    remote_entry.add_to_hass(hass)

    discovery_cm = _make_client_context(get_hosts=[_remote_host("test_console")])
    validation_cm = _make_client_context(sites=[])

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[discovery_cm, validation_cm],
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
    ):
        result = await remote_entry.start_reauth_flow(hass)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "test_api_key"},
        )

        assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_console_id"}


async def test_reconfigure_local_success(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test successful reconfigure flow for local connection."""
    mock_config_entry.add_to_hass(hass)

    mock_client = MagicMock()
    mock_client.sites = MagicMock()
    mock_client.sites.get_all = AsyncMock(
        return_value=[MagicMock(id="default", name="Default")]
    )
    mock_client.close = AsyncMock()

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(return_value=mock_client)
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
        # Prevent the real integration setup (which opens sockets) when the
        # entry is created/reloaded by the flow.
        patch(
            "custom_components.unifi_insights.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await mock_config_entry.start_reconfigure_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.2",
                CONF_API_KEY: "test_api_key",
                CONF_VERIFY_SSL: True,
            },
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"


async def test_reconfigure_remote_success(hass: HomeAssistant) -> None:
    """Test successful reconfigure flow for remote connection."""
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

    discovery_cm = _make_client_context(get_hosts=[_remote_host("new_console")])
    validation_cm = _make_client_context(
        sites=[MagicMock(id="default", name="Default")]
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[discovery_cm, validation_cm],
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
    ):
        result = await remote_entry.start_reconfigure_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_CONSOLE_ID: "new_console",
                CONF_API_KEY: "old_api_key",
            },
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"


async def test_reconfigure_remote_adopts_the_console_as_unique_id(
    hass: HomeAssistant,
) -> None:
    """Test reconfigure moves a cloud entry's unique ID onto the console."""
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

    discovery_cm = _make_client_context(get_hosts=[_remote_host("new_console")])
    validation_cm = _make_client_context(
        sites=[MagicMock(id="default", name="Default")]
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[discovery_cm, validation_cm],
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
        patch(
            "custom_components.unifi_insights.async_setup_entry",
            return_value=True,
        ),
        # The reload runs async_migrate_entry, which also rewrites the
        # unique_id. Stub it out so only the flow can set it here.
        patch(
            "custom_components.unifi_insights.async_migrate_entry",
            return_value=True,
        ),
    ):
        result = await remote_entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_CONSOLE_ID: "new_console",
                CONF_API_KEY: "old_api_key",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert remote_entry.unique_id == "new_console"
    assert remote_entry.data[CONF_CONSOLE_ID] == "new_console"


async def test_reconfigure_remote_console_rejects_the_key(
    hass: HomeAssistant,
) -> None:
    """Test reconfigure re-shows the form when the console rejects the key."""
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

    discovery_cm = _make_client_context(get_hosts=[_remote_host("new_console")])

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=discovery_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
        patch(
            "custom_components.unifi_insights.config_flow"
            ".UnifiInsightsConfigFlow._async_validate_remote_console",
            side_effect=UniFiAuthenticationError("console rejected the key"),
        ),
    ):
        result = await remote_entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_CONSOLE_ID: "new_console",
                CONF_API_KEY: "old_api_key",
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_CONSOLE_ID: "invalid_console_id"}
    assert remote_entry.data[CONF_CONSOLE_ID] == "console123"
    assert remote_entry.unique_id == "old_api_key"


async def test_reconfigure_auth_error(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test reconfigure flow with authentication error."""
    mock_config_entry.add_to_hass(hass)

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(
        side_effect=UniFiAuthenticationError("Invalid credentials")
    )
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await mock_config_entry.start_reconfigure_flow(hass)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "bad_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_API_KEY: "invalid_auth"}


async def test_reconfigure_connection_error(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test reconfigure flow with connection error."""
    mock_config_entry.add_to_hass(hass)

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(side_effect=UniFiConnectionError("Cannot connect"))
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await mock_config_entry.start_reconfigure_flow(hass)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_timeout_error(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test reconfigure flow with timeout error."""
    mock_config_entry.add_to_hass(hass)

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(side_effect=UniFiTimeoutError("Timeout"))
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await mock_config_entry.start_reconfigure_flow(hass)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_unknown_error(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test reconfigure flow with unknown error."""
    mock_config_entry.add_to_hass(hass)

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(side_effect=Exception("Unknown error"))
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await mock_config_entry.start_reconfigure_flow(hass)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "unknown"}


async def test_reconfigure_no_sites_found(
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Test reconfigure flow when no sites are found."""
    mock_config_entry.add_to_hass(hass)

    mock_client = MagicMock()
    mock_client.sites = MagicMock()
    mock_client.sites.get_all = AsyncMock(return_value=[])
    mock_client.close = AsyncMock()

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(return_value=mock_client)
    async_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=async_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await mock_config_entry.start_reconfigure_flow(hass)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "test_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_API_KEY: "invalid_auth"}


async def test_reconfigure_remote_no_sites_found(
    hass: HomeAssistant,
) -> None:
    """Test reconfigure flow for remote connection when no sites are found."""
    # Create remote config entry
    remote_entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Insights (Cloud)",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE,
            CONF_CONSOLE_ID: "test_console",
            CONF_API_KEY: "test_api_key",
        },
        unique_id="test_api_key",
    )
    remote_entry.add_to_hass(hass)

    discovery_cm = _make_client_context(get_hosts=[_remote_host("test_console")])
    validation_cm = _make_client_context(sites=[])

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[discovery_cm, validation_cm],
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
    ):
        result = await remote_entry.start_reconfigure_flow(hass)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_CONSOLE_ID: "test_console",
                CONF_API_KEY: "test_api_key",
            },
        )

        assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_CONSOLE_ID: "invalid_console_id"}


# ============================================================================
# Options Flow Tests
# ============================================================================


async def test_options_flow_shows_form(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test options flow shows form with current values."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_options_flow_submit(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test options flow submission creates entry."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "track_wifi_clients": True,
            "track_wired_clients": False,
            "client_control": True,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "track_wifi_clients": True,
        "track_wired_clients": False,
        "client_control": True,
    }


def _attach_sites(entry: MockConfigEntry, sites: dict[str, str]) -> None:
    """Give a config entry runtime data exposing the console's sites."""
    entry.runtime_data = MagicMock()
    entry.runtime_data.config_coordinator.available_sites = sites


async def test_options_flow_hides_site_picker_for_single_site(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A single-site console has nothing to filter, so no picker is shown."""
    mock_config_entry.add_to_hass(hass)
    _attach_sites(mock_config_entry, {"default": "Default"})

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    assert CONF_SITE_IDS not in require_not_none(result["data_schema"]).schema


async def test_options_flow_filters_sites(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A multi-site console offers a site picker and stores the pick (Issue #128)."""
    mock_config_entry.add_to_hass(hass)
    _attach_sites(mock_config_entry, {"default": "Default", "site2": "Branch"})

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert CONF_SITE_IDS in require_not_none(result["data_schema"]).schema

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "track_wifi_clients": False,
            "track_wired_clients": False,
            "client_control": True,
            CONF_SITE_IDS: ["site2"],
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SITE_IDS] == ["site2"]


async def test_options_flow_empty_site_selection_means_all_sites(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Clearing the picker stores no filter rather than an empty one."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_SITE_IDS: ["site2"]}
    )
    _attach_sites(mock_config_entry, {"default": "Default", "site2": "Branch"})

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "track_wifi_clients": False,
            "track_wired_clients": False,
            "client_control": True,
            CONF_SITE_IDS: [],
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert CONF_SITE_IDS not in result["data"]


async def test_options_flow_omitted_site_selection_clears_filter(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A submit without the site field clears the filter instead of restoring it."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_SITE_IDS: ["site2"]}
    )
    _attach_sites(mock_config_entry, {"default": "Default", "site2": "Branch"})

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "track_wifi_clients": False,
            "track_wired_clients": False,
            "client_control": True,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert CONF_SITE_IDS not in result["data"]


async def test_options_flow_lists_saved_site_that_vanished(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A saved site no longer on the console stays listed so it can be cleared."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_SITE_IDS: ["gone"]}
    )
    _attach_sites(mock_config_entry, {"default": "Default"})

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    selector = require_not_none(result["data_schema"]).schema[CONF_SITE_IDS]
    values = [option["value"] for option in selector.config["options"]]
    assert values == ["default", "gone"]


async def test_options_flow_keeps_site_filter_when_sites_unknown(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Saving options while the entry is not loaded must not drop the filter."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_SITE_IDS: ["site2"]}
    )

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert CONF_SITE_IDS not in require_not_none(result["data_schema"]).schema

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "track_wifi_clients": True,
            "track_wired_clients": False,
            "client_control": True,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SITE_IDS] == ["site2"]


async def test_options_flow_migrates_old_track_clients(
    hass: HomeAssistant,
) -> None:
    """Test options flow migrates from old track_clients option."""
    # Create config entry with old track_clients option
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Insights (Local)",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.1",
            CONF_API_KEY: "test_api_key",
            CONF_VERIFY_SSL: False,
        },
        options={"track_clients": True},  # Old option
        unique_id="test_api_key",
    )
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    assert result["type"] == FlowResultType.FORM
    # Default values should come from old track_clients
    assert result["data_schema"] is not None


async def test_async_get_options_flow(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test async_get_options_flow returns options handler."""
    options_flow = UnifiInsightsConfigFlow.async_get_options_flow(mock_config_entry)
    assert isinstance(options_flow, UnifiInsightsOptionsFlow)


def _protect_context(*, cameras: object = None, nvr: object = None) -> MagicMock:
    """Create an async context manager mock for UniFiProtectClient."""
    client = MagicMock()
    if isinstance(cameras, Exception):
        client.cameras.get_all = AsyncMock(side_effect=cameras)
    else:
        client.cameras.get_all = AsyncMock(return_value=cameras or [])
    if isinstance(nvr, Exception):
        client.nvr.get = AsyncMock(side_effect=nvr)
    else:
        client.nvr.get = AsyncMock(return_value=nvr)
    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(return_value=client)
    async_cm.__aexit__ = AsyncMock(return_value=None)
    return async_cm


async def _submit_local_flow(hass: HomeAssistant) -> config_entries.ConfigFlowResult:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL},
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_HOST: "https://192.168.1.1",
            CONF_API_KEY: "test_api_key",
            CONF_VERIFY_SSL: False,
        },
    )


@pytest.mark.parametrize(
    ("network", "protect", "expected_errors"),
    [
        # A 5xx is temporary, not an unknown error.
        (
            {"sites_side_effect": UniFiResponseError("Bad gateway", status_code=502)},
            {"cameras": UniFiNotFoundError("Not found", status_code=404)},
            {"base": "cannot_connect"},
        ),
        # A temporary Protect failure outranks a Network 401: the key may be
        # fine on a Protect-only console.
        (
            {"sites_side_effect": UniFiAuthenticationError("No", status_code=401)},
            {"cameras": UniFiTimeoutError("Timed out")},
            {"base": "cannot_connect"},
        ),
        # The empty-camera NVR probe's own server error.
        (
            {"sites": []},
            {"nvr": UniFiResponseError("Unavailable", status_code=503)},
            {"base": "cannot_connect"},
        ),
        # No sites and a successful answer without an NVR record:
        # api_unsupported, as before.
        (
            {"sites": []},
            {"nvr": UniFiNotFoundError("NVR not found", status_code=200)},
            {"base": "api_unsupported"},
        ),
        # No sites and an NVR record that failed to parse.
        (
            {"sites": []},
            {
                "nvr": UniFiResponseError(
                    "NVR response failed validation (id)", status_code=200
                )
            },
            {"base": "site_parse_error"},
        ),
        # An unexpected NVR probe error.
        (
            {"sites": []},
            {"nvr": RuntimeError("unexpected")},
            {"base": "unknown"},
        ),
        # No sites and no NVR record at all: the invalid_auth fallback.
        (
            {"sites": []},
            {"nvr": None},
            {CONF_API_KEY: "invalid_auth"},
        ),
    ],
    ids=[
        "network-502",
        "network-401-protect-timeout",
        "nvr-503",
        "nvr-not-found",
        "nvr-malformed",
        "nvr-unexpected",
        "no-nvr-record",
    ],
)
async def test_local_flow_classifies_probe_errors(
    hass: HomeAssistant,
    network: dict[str, object],
    protect: dict[str, object],
    expected_errors: dict[str, str],
) -> None:
    """Local validation maps probe results to the right form error."""
    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=_make_client_context(**network),
        ),
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient",
            return_value=_protect_context(**protect),
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await _submit_local_flow(hass)

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == expected_errors


async def test_local_flow_camera_free_nvr_creates_entry(hass: HomeAssistant) -> None:
    """A Protect-only console with an NVR and no cameras can be added."""
    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=_make_client_context(
                sites_side_effect=UniFiResponseError("HTML page", status_code=200)
            ),
        ),
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient",
            return_value=_protect_context(nvr=MagicMock(id="nvr1")),
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
        patch(
            "custom_components.unifi_insights.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await _submit_local_flow(hass)

    assert result["type"] == FlowResultType.CREATE_ENTRY


async def _submit_remote_flow(hass: HomeAssistant) -> config_entries.ConfigFlowResult:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE},
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_API_KEY: "test_api_key"},
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (UniFiResponseError("Bad gateway", status_code=502), "cannot_connect"),
        (UniFiResponseError("Bad request", status_code=400), "unknown"),
    ],
    ids=["502", "400"],
)
async def test_remote_discovery_response_error(
    hass: HomeAssistant, error: Exception, expected: str
) -> None:
    """Console discovery reports 5xx as temporary and other errors as unknown."""
    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=_make_client_context(get_hosts_side_effect=error),
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
    ):
        result = await _submit_remote_flow(hass)

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_remote_console_server_error_is_temporary(hass: HomeAssistant) -> None:
    """A 5xx while validating the selected console is cannot_connect."""
    discovery_cm = _make_client_context(get_hosts=[_remote_host()])
    validation_cm = _make_client_context(
        sites_side_effect=UniFiResponseError("Unavailable", status_code=503)
    )
    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[discovery_cm, validation_cm],
        ),
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient",
            return_value=_protect_context(
                cameras=UniFiNotFoundError("Not found", status_code=404)
            ),
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
    ):
        result = await _submit_remote_flow(hass)
        assert result["step_id"] == "select_console"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONSOLE_ID: "console123"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.parametrize("step", ["reauth", "reconfigure"])
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (UniFiResponseError("Bad gateway", status_code=502), "cannot_connect"),
        (UniFiResponseError("Bad request", status_code=400), "unknown"),
    ],
    ids=["502", "400"],
)
async def test_remote_entry_discovery_response_error(
    hass: HomeAssistant, step: str, error: Exception, expected: str
) -> None:
    """Reauth and reconfigure report discovery 5xx as temporary."""
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

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=_make_client_context(get_hosts_side_effect=error),
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
    ):
        if step == "reauth":
            result = await remote_entry.start_reauth_flow(hass)
            user_input = {CONF_API_KEY: "new_api_key"}
        else:
            result = await remote_entry.start_reconfigure_flow(hass)
            user_input = {CONF_API_KEY: "old_api_key", CONF_CONSOLE_ID: "console123"}
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=user_input
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": expected}
