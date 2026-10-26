# Copyright 2026 UniFi Insights contributors
"""Tests for UniFi Insights console identity separation (PR D).

Verifies:
1. Changing host or re-authenticating preserves entity IDs,
   device links, and integration options.
2. Duplicate configuration entries for the same physical console
   are prevented even if different hosts or credentials are provided.
3. Multi-console cloud setup with the same API key succeeds.
4. Backward compatibility with v1/v2 schema entries without user intervention.
5. Migration preserves existing Home Assistant entity unique IDs and options.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_VERIFY_SSL
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api import UniFiResponseError

from custom_components.unifi_insights import (
    _first_site_id,
    _is_console_device,
    async_migrate_entry,
    async_setup_entry,
)
from custom_components.unifi_insights.config_flow import UnifiInsightsConfigFlow
from custom_components.unifi_insights.console_identity import first_non_default_site_id
from custom_components.unifi_insights.const import (
    CONF_CLIENT_CONTROL,
    CONF_CONNECTION_TYPE,
    CONF_CONSOLE_ID,
    CONF_CONSOLE_NAME,
    CONF_TRACK_CLIENTS,
    CONF_TRACK_WIFI_CLIENTS,
    CONF_TRACK_WIRED_CLIENTS,
    CONNECTION_TYPE_LOCAL,
    CONNECTION_TYPE_REMOTE,
    DOMAIN,
)
from custom_components.unifi_insights.coordinators.protect import (
    UnifiInsightsProtectCoordinator,
)
from custom_components.unifi_insights.probe import ProbeResult, ProbeStatus
from tests.conftest import device_config_entry_ids, get_device_by_identifier
from tests.fixtures.assertions import require_not_none

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigFlowContext
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import (
        device_registry as dr,
    )
    from homeassistant.helpers import (
        entity_registry as er,
    )

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _remote_host(
    host_id: str = "console123", hostname: str = "Dream Machine Pro"
) -> dict[str, object]:
    """Create a discovered remote host payload."""
    return {
        "id": host_id,
        "type": "console",
        "reportedState": {"hostname": hostname},
    }


def _make_mock_client(get_hosts=None, sites=None, devices=None, cameras=None, nvr=None):
    """Create a configured mock client."""
    client = MagicMock()
    if get_hosts is not None:
        client.get_hosts = AsyncMock(return_value=get_hosts)
    else:
        client.get_hosts = AsyncMock(return_value=[])

    client.sites = MagicMock()
    client.sites.get_all = AsyncMock(return_value=sites if sites is not None else [])
    client.devices = MagicMock()
    client.devices.get_all = AsyncMock(
        return_value=devices if devices is not None else []
    )
    client.cameras = MagicMock()
    client.cameras.get_all = AsyncMock(
        return_value=cameras if cameras is not None else []
    )
    client.nvr = MagicMock()
    client.nvr.get = AsyncMock(return_value=nvr)
    client.close = AsyncMock()

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm, client


async def test_duplicate_remote_console_prevented(
    hass: HomeAssistant,
) -> None:
    """Test duplicate remote configuration entries for the same console are prevented.

    Even if different credentials/API keys are provided, duplicate console entries
    must abort with already_configured.
    """
    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="console123",
        title="UniFi - Dream Machine Pro",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE,
            CONF_CONSOLE_ID: "console123",
            CONF_API_KEY: "api_key_1",
        },
    )
    existing_entry.add_to_hass(hass)

    discovery_cm, _ = _make_mock_client(
        get_hosts=[_remote_host("console123", "Dream Machine Pro")]
    )
    validation_net_cm, _ = _make_mock_client(
        sites=[MagicMock(id="site1", name="Default")]
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[discovery_cm, validation_net_cm],
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
            user_input={CONF_API_KEY: "different_api_key_2"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONSOLE_ID: "console123"},
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "already_configured"


async def test_multi_console_cloud_setup_same_api_key(
    hass: HomeAssistant,
) -> None:
    """Test that two different consoles under the same cloud API key succeed.

    Under the legacy schema, the unique_id was the API key, which blocked
    adding multiple consoles on the same UI account. With PR D, console_id
    is the unique_id, allowing multi-console cloud setups.
    """
    hosts = [
        _remote_host("console_home", "Home UDM"),
        _remote_host("console_office", "Office UDM"),
    ]

    discovery_cm1, _ = _make_mock_client(get_hosts=hosts)
    validation_cm1, _ = _make_mock_client(
        sites=[MagicMock(id="default", name="Default")]
    )
    discovery_cm2, _ = _make_mock_client(get_hosts=hosts)
    validation_cm2, _ = _make_mock_client(
        sites=[MagicMock(id="default", name="Default")]
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            side_effect=[discovery_cm1, validation_cm1, discovery_cm2, validation_cm2],
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
        patch(
            "custom_components.unifi_insights.async_setup_entry",
            return_value=True,
        ),
    ):
        # Configure first console
        r1 = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        r1 = await hass.config_entries.flow.async_configure(
            r1["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE},
        )
        r1 = await hass.config_entries.flow.async_configure(
            r1["flow_id"],
            user_input={CONF_API_KEY: "shared_account_key"},
        )
        assert r1["type"] == FlowResultType.FORM
        assert r1["step_id"] == "select_console"

        r1 = await hass.config_entries.flow.async_configure(
            r1["flow_id"],
            user_input={CONF_CONSOLE_ID: "console_home"},
        )
        assert r1["type"] == FlowResultType.CREATE_ENTRY
        assert r1["title"] == "UniFi - Home UDM"
        assert r1["result"].unique_id == "console_home"

        # Configure second console with the exact same API key
        r2 = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        r2 = await hass.config_entries.flow.async_configure(
            r2["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE},
        )
        r2 = await hass.config_entries.flow.async_configure(
            r2["flow_id"],
            user_input={CONF_API_KEY: "shared_account_key"},
        )
        assert r2["type"] == FlowResultType.FORM
        assert r2["step_id"] == "select_console"

        r2 = await hass.config_entries.flow.async_configure(
            r2["flow_id"],
            user_input={CONF_CONSOLE_ID: "console_office"},
        )
        assert r2["type"] == FlowResultType.CREATE_ENTRY
        assert r2["title"] == "UniFi - Office UDM"
        assert r2["result"].unique_id == "console_office"


async def test_reconfiguring_host_preserves_unique_id_and_options(
    hass: HomeAssistant,
) -> None:
    """Test reconfiguring host preserves entry unique ID, title, and options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="11:22:33:44:55:66",
        title="UniFi - UDM Pro",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.1",
            CONF_API_KEY: "test_key",
            CONF_VERIFY_SSL: False,
        },
        options={
            CONF_TRACK_WIFI_CLIENTS: True,
            CONF_TRACK_WIRED_CLIENTS: False,
            CONF_CLIENT_CONTROL: True,
        },
    )
    entry.add_to_hass(hass)

    gateway_dev = MagicMock(type="udm-pro", mac="11:22:33:44:55:66", name="UDM Pro")
    net_cm, _ = _make_mock_client(
        sites=[MagicMock(id="site1", name="Default")],
        devices=[gateway_dev],
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
        patch(
            "custom_components.unifi_insights.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.200",
                CONF_API_KEY: "test_key",
                CONF_VERIFY_SSL: True,
            },
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"

        assert entry.unique_id == "11:22:33:44:55:66"
        assert entry.title == "UniFi - UDM Pro"
        assert entry.data[CONF_HOST] == "https://192.168.1.200"
        assert entry.data[CONF_VERIFY_SSL] is True
        assert entry.options[CONF_TRACK_WIFI_CLIENTS] is True
        assert entry.options[CONF_TRACK_WIRED_CLIENTS] is False
        assert entry.options[CONF_CLIENT_CONTROL] is True


async def test_reauth_preserves_unique_id_and_options(
    hass: HomeAssistant,
) -> None:
    """Test re-authenticating preserves entry unique ID and options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="console123",
        title="UniFi - Dream Router",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE,
            CONF_CONSOLE_ID: "console123",
            CONF_API_KEY: "old_expired_key",
        },
        options={
            CONF_TRACK_WIFI_CLIENTS: False,
            CONF_TRACK_WIRED_CLIENTS: True,
            CONF_CLIENT_CONTROL: False,
        },
    )
    entry.add_to_hass(hass)

    discovery_cm, _ = _make_mock_client(
        get_hosts=[_remote_host("console123", "Dream Router")]
    )
    validation_cm, _ = _make_mock_client(
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
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "brand_new_api_key_456"},
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"

        assert entry.unique_id == "console123"
        assert entry.data[CONF_API_KEY] == "brand_new_api_key_456"
        assert entry.options[CONF_TRACK_WIFI_CLIENTS] is False
        assert entry.options[CONF_TRACK_WIRED_CLIENTS] is True
        assert entry.options[CONF_CLIENT_CONTROL] is False


async def test_migration_v1_to_v1_2(
    hass: HomeAssistant,
) -> None:
    """Test migration from v1 schema where unique_id was the API key."""
    legacy_entry = MockConfigEntry(
        version=1,
        minor_version=0,
        domain=DOMAIN,
        title="UniFi Insights (Cloud)",
        unique_id="legacy_api_key_123",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE,
            CONF_CONSOLE_ID: "cloud_console_456",
            CONF_API_KEY: "legacy_api_key_123",
        },
        options={
            CONF_TRACK_CLIENTS: True,
        },
    )
    legacy_entry.add_to_hass(hass)

    migrated = await async_migrate_entry(hass, legacy_entry)
    assert migrated is True
    assert legacy_entry.version == 1
    assert legacy_entry.minor_version == 2
    assert legacy_entry.unique_id == "cloud_console_456"
    assert legacy_entry.options[CONF_TRACK_CLIENTS] is True


async def test_migration_leaves_stable_unique_id_intact(
    hass: HomeAssistant,
) -> None:
    """Test migration does not alter unique_id if it was already stable."""
    stable_entry = MockConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="UniFi - UDM",
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.1",
            CONF_API_KEY: "my_api_key",
        },
        options={},
    )
    stable_entry.add_to_hass(hass)

    migrated = await async_migrate_entry(hass, stable_entry)
    assert migrated is True
    assert stable_entry.version == 1
    assert stable_entry.minor_version == 2
    assert stable_entry.unique_id == "aa:bb:cc:dd:ee:ff"


async def test_transport_change_preserves_device_and_entity_registry(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test that entities and device links survive a host reconfiguration."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="console_abc_123",
        title="UniFi - Console",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.1",
            CONF_API_KEY: "initial_key",
            CONF_VERIFY_SSL: False,
        },
    )
    entry.add_to_hass(hass)

    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "gateway_mac_123")},
        name="Gateway Router",
        manufacturer="Ubiquiti Inc.",
        model="UDM-Pro",
    )
    assert device is not None

    entity = entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="gateway_mac_123_cpu_utilization",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="gateway_router_cpu_utilization",
    )
    assert entity is not None
    original_entity_id = entity.entity_id

    gateway_dev = MagicMock(type="udm-pro", mac="123", name="Gateway Router")
    net_cm, _ = _make_mock_client(
        sites=[MagicMock(id="site1", name="Default")],
        devices=[gateway_dev],
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
        patch(
            "custom_components.unifi_insights.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://10.0.0.1",
                CONF_API_KEY: "replaced_api_key",
                CONF_VERIFY_SSL: True,
            },
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"

    registered_device = get_device_by_identifier(
        device_registry, (DOMAIN, "gateway_mac_123"), entry.entry_id
    )
    assert registered_device is not None
    assert registered_device.id == device.id
    assert device_config_entry_ids(registered_device) == {entry.entry_id}

    registered_entity = entity_registry.async_get(original_entity_id)
    assert registered_entity is not None
    assert registered_entity.unique_id == "gateway_mac_123_cpu_utilization"
    assert registered_entity.device_id == device.id
    assert registered_entity.config_entry_id == entry.entry_id


async def test_duplicate_local_console_prevented(
    hass: HomeAssistant,
) -> None:
    """Test duplicate local configuration entries for the same console are prevented."""
    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="11:22:33:44:55:66",
        title="UniFi - UDM Pro",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.1",
            CONF_API_KEY: "key_1",
            CONF_CONSOLE_ID: "11:22:33:44:55:66",
        },
    )
    existing_entry.add_to_hass(hass)

    gateway_dev = MagicMock(type="udm-pro", mac="11:22:33:44:55:66", name="UDM Pro")
    net_cm, _ = _make_mock_client(
        sites=[MagicMock(id="site1", name="Default")],
        devices=[gateway_dev],
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm,
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
                CONF_HOST: "https://192.168.1.50",
                CONF_API_KEY: "different_key_2",
            },
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "already_configured"


async def test_reconfigure_local_account_mismatch(
    hass: HomeAssistant,
) -> None:
    """Test reconfiguring local console to an IP on a different console aborts."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="11:22:33:44:55:66",
        title="UniFi - UDM Pro",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.1",
            CONF_API_KEY: "initial_key",
            CONF_CONSOLE_ID: "11:22:33:44:55:66",
        },
    )
    entry.add_to_hass(hass)

    different_gateway = MagicMock(
        type="udm-se", mac="99:88:77:66:55:44", name="Different Console"
    )
    net_cm, _ = _make_mock_client(
        sites=[MagicMock(id="site1", name="Default")],
        devices=[different_gateway],
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://10.0.0.1",
                CONF_API_KEY: "initial_key",
            },
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "account_mismatch"


async def test_reconfigure_without_any_prior_identity_writes_no_console_id(
    hass: HomeAssistant,
) -> None:
    """An entry with nothing to preserve still must not store an empty id.

    Once the stored console id is used as a fallback, ``new_id`` is only
    falsy for a legacy entry that has neither CONF_CONSOLE_ID nor a
    unique_id, reconfigured to a host that yields no derivable id. The
    entry must then be left without a CONF_CONSOLE_ID key rather than
    gaining an empty one.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=None,
        title="UniFi - Legacy",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "192.168.1.70",
            CONF_API_KEY: "existing_key",
            CONF_VERIFY_SSL: False,
        },
    )
    entry.add_to_hass(hass)

    net_cm_err, net_client_err = _make_mock_client()
    net_client_err.sites.get_all = AsyncMock(
        side_effect=UniFiResponseError("Not Found", status_code=404)
    )
    protect_client = MagicMock()
    protect_client.cameras.get_all = AsyncMock(return_value=[MagicMock()])
    protect_client.nvr.get = AsyncMock(return_value=None)
    protect_cm = MagicMock()
    protect_cm.__aenter__ = AsyncMock(return_value=protect_client)
    protect_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm_err,
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
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "",
                CONF_API_KEY: "existing_key",
                CONF_VERIFY_SSL: False,
            },
        )
        await hass.async_block_till_done()

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert CONF_CONSOLE_ID not in entry.data
        assert entry.unique_id is None


async def test_reconfigure_transient_device_error_preserves_console_mac(
    hass: HomeAssistant,
) -> None:
    """A flaky device fetch during reconfigure must not erase the stored MAC.

    Device inspection swallows every exception, after which the console id
    falls back to a site id or the host. Letting that fallback overwrite a
    hardware MAC is permanent: setup only backfills when console_id is falsy,
    and the ":"-based mismatch guard stops firing once the id is not a MAC,
    so a later reconfigure aimed at a different console would be accepted.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="11:22:33:44:55:66",
        title="UniFi - UDM Pro",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.1",
            CONF_API_KEY: "initial_key",
            CONF_CONSOLE_ID: "11:22:33:44:55:66",
        },
    )
    entry.add_to_hass(hass)

    net_cm, net_client = _make_mock_client(
        sites=[MagicMock(id="site-a", name="Default")],
    )
    # The console is reachable, but this one call fails the way a busy or
    # rebooting controller fails mid-reconfigure.
    net_client.devices.get_all = AsyncMock(side_effect=TimeoutError())

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
        patch(
            "custom_components.unifi_insights.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://10.0.0.1",
                CONF_API_KEY: "initial_key",
            },
        )
        await hass.async_block_till_done()

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        # The new host is stored, but the hardware identity survives untouched.
        assert entry.data[CONF_HOST] == "https://10.0.0.1"
        assert entry.data[CONF_CONSOLE_ID] == "11:22:33:44:55:66"
        assert entry.unique_id == "11:22:33:44:55:66"


async def test_reconfigure_rejects_a_unique_id_another_entry_owns(
    hass: HomeAssistant,
) -> None:
    """Reconfigure must not claim a unique_id that another entry already holds.

    The account_mismatch guard only fires when the stored id and the newly
    discovered one both look like MACs, so a legacy entry still keyed on a
    site id slips past it. async_update_reload_and_abort does not check for
    an existing owner and Home Assistant will let two entries share one
    unique_id, which is why setup and the migration both look first.
    """
    legacy = MockConfigEntry(
        domain=DOMAIN,
        unique_id="site-a",
        title="UniFi - Legacy",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.1",
            CONF_API_KEY: "initial_key",
            CONF_CONSOLE_ID: "site-a",
        },
    )
    legacy.add_to_hass(hass)

    # A second entry already owns the console this reconfigure would resolve to.
    owner = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        title="UniFi - Console",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.2",
            CONF_API_KEY: "other_key",
            CONF_CONSOLE_ID: "aa:bb:cc:dd:ee:ff",
        },
    )
    owner.add_to_hass(hass)

    net_cm, _ = _make_mock_client(
        sites=[MagicMock(id="site-a", name="Default")],
        devices=[
            MagicMock(
                type=None,
                model="UniFi Dream Machine PRO SE",
                mac="AA:BB:CC:DD:EE:FF",
                name="Crestwood",
            )
        ],
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": legacy.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.2",
                CONF_API_KEY: "initial_key",
            },
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    # Neither entry was mutated.
    assert legacy.unique_id == "site-a"
    assert owner.unique_id == "aa:bb:cc:dd:ee:ff"


async def test_reconfigure_detects_gateway_with_unknown_model(
    hass: HomeAssistant,
) -> None:
    """A device flagged is_gateway is the console even if its model is unknown.

    ``_is_console_device`` honours ``is_gateway``; the config flow's own
    matcher only looked at the type/model tokens, so a console whose model
    string is not in CONSOLE_DEVICE_TOKENS was invisible to the flow while
    setup adopted its MAC - which is how one console ends up with two entries.

    Reaching account_mismatch proves the MAC was discovered: had the gateway
    gone unrecognised, no MAC would be found and the reconfigure would have
    kept the stored id and succeeded.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="11:22:33:44:55:66",
        title="UniFi - Console",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.1",
            CONF_API_KEY: "initial_key",
            CONF_CONSOLE_ID: "11:22:33:44:55:66",
        },
    )
    entry.add_to_hass(hass)

    unknown_gateway = MagicMock(
        type=None,
        model="Mystery Box 9000",
        mac="99:88:77:66:55:44",
        name="Unreleased Console",
        is_gateway=True,
    )
    net_cm, _ = _make_mock_client(
        sites=[MagicMock(id="site-a", name="Default")],
        devices=[unknown_gateway],
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://10.0.0.1",
                CONF_API_KEY: "initial_key",
            },
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "account_mismatch"


async def test_flow_finds_console_in_a_later_site(hass: HomeAssistant) -> None:
    """The flow scans past the first site to find the gateway.

    Setup scans every site, so a gateway outside sites[0] left the flow with
    a site-or-host id while setup adopted the MAC. The two identities then
    disagreed and re-adding the console produced a second config entry.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="site-a",
        title="UniFi - Console",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.1",
            CONF_API_KEY: "initial_key",
            CONF_CONSOLE_ID: "site-a",
        },
    )
    entry.add_to_hass(hass)

    net_cm, net_client = _make_mock_client(
        sites=[MagicMock(id="site-a", name="A"), MagicMock(id="site-b", name="B")],
    )
    per_site: dict[str | None, list[MagicMock]] = {
        "site-a": [MagicMock(type=None, model="USW Pro Max 24", name="Switch")],
        "site-b": [
            MagicMock(
                type=None,
                model="UniFi Dream Machine PRO SE",
                mac="AA:BB:CC:DD:EE:FF",
                name="Crestwood",
            )
        ],
    }

    async def _devices(
        site_id: str | None = None, **_kwargs: object
    ) -> list[MagicMock]:
        return per_site.get(site_id, []) if site_id is not None else []

    net_client.devices.get_all = AsyncMock(side_effect=_devices)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
        patch(
            "custom_components.unifi_insights.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "initial_key",
            },
        )
        await hass.async_block_till_done()

        assert result["reason"] == "reconfigure_successful"
        # The MAC from the second site replaces the weaker site-based id.
        assert entry.data[CONF_CONSOLE_ID] == "aa:bb:cc:dd:ee:ff"
        assert entry.unique_id == "aa:bb:cc:dd:ee:ff"


async def test_flow_stops_scanning_once_the_console_is_found(
    hass: HomeAssistant,
) -> None:
    """A console in the first site still costs exactly one device request.

    Widening the scan is only worth it if the ordinary case does not pay for
    it, so the loop must stop at the first site that yields a console.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        title="UniFi - Console",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.1",
            CONF_API_KEY: "initial_key",
            CONF_CONSOLE_ID: "aa:bb:cc:dd:ee:ff",
        },
    )
    entry.add_to_hass(hass)

    gateway = MagicMock(
        type=None,
        model="UniFi Dream Machine PRO SE",
        mac="AA:BB:CC:DD:EE:FF",
        name="Crestwood",
    )
    net_cm, net_client = _make_mock_client(
        sites=[
            MagicMock(id="site-a", name="A"),
            MagicMock(id="site-b", name="B"),
            MagicMock(id="site-c", name="C"),
        ],
        devices=[gateway],
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
        patch(
            "custom_components.unifi_insights.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "initial_key",
            },
        )
        await hass.async_block_till_done()

    # Three sites configured, but the console is in the first one.
    assert net_client.devices.get_all.await_count == 1


async def test_no_mac_flow_uses_host_when_default_site_comes_first(
    hass: HomeAssistant,
) -> None:
    """Keep console identity independent of default and non-default site IDs.

    The site-order utility still returns the first non-default site, while
    the configuration flow uses the actual host when no console MAC is found.
    """
    # No stored identity: a stored one would rightly take precedence over a
    # freshly derived host, which would hide what the flow derived.
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=None,
        title="UniFi - Console",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.1",
            CONF_API_KEY: "initial_key",
        },
    )
    entry.add_to_hass(hass)

    # No console among the devices, so identity falls back to the actual host.
    net_cm, _ = _make_mock_client(
        sites=[
            MagicMock(id="default", name="Default"),
            MagicMock(id="site-b", name="B"),
        ],
        devices=[MagicMock(type=None, model="USW Pro Max 24", name="Switch")],
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
        patch(
            "custom_components.unifi_insights.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "https://192.168.1.1",
                CONF_API_KEY: "initial_key",
            },
        )
        await hass.async_block_till_done()

    assert entry.data[CONF_CONSOLE_ID] == "https://192.168.1.1"
    # Inventory ordering remains available without establishing identity.
    coordinator = MagicMock()
    coordinator.data = {"sites": {"default": {}, "site-b": {}}}
    assert _first_site_id(coordinator) == "site-b"


class TestConsoleDeviceDetection:
    """Identify the console among the Network devices.

    Pinned to the shape the API actually returns: every device carries a
    `model` and leaves `type` unset, so matching on `type` alone never fires
    and the console identity silently degrades to the host address - the
    exact coupling this separation exists to remove.
    """

    # Verbatim from a live console's device list.
    LIVE_DEVICES: ClassVar[list[dict[str, Any]]] = [
        {"name": "USP PDU Pro", "model": "USP PDU Pro", "type": None},
        {"name": "Switch Pro Max 24", "model": "USW Pro Max 24", "type": None},
        {"name": "USW Flex 2.5G 5", "model": "USW Flex 2.5G 5", "type": None},
        {"name": "USW-Lite-8-PoE", "model": "USW-Lite-8-PoE", "type": None},
        {"name": "U7 Pro XGS", "model": "U7 Pro XGS", "type": None},
        {
            "name": "Crestwood",
            "model": "UniFi Dream Machine PRO SE",
            "type": None,
            "macAddress": "AA:BB:CC:DD:EE:FF",
        },
    ]

    def test_console_found_by_model_when_type_is_unset(self):
        """The Dream Machine is the console; the switches and AP are not."""
        consoles = [d for d in self.LIVE_DEVICES if _is_console_device(d)]

        assert [d["name"] for d in consoles] == ["Crestwood"]

    def test_is_gateway_flag_still_wins(self):
        """A device that declares itself a gateway needs no model match."""
        assert _is_console_device({"model": "Mystery Box", "is_gateway": True})

    def test_plain_switch_is_not_a_console(self):
        assert not _is_console_device({"model": "USW Pro Max 24", "type": None})

    def test_first_site_id_skips_the_default_placeholder(self):
        """A real site id is a stable identity; "default" is not."""
        coordinator = MagicMock()
        coordinator.data = {"sites": {"default": {}, "88f7af54-98f8": {}}}

        assert _first_site_id(coordinator) == "88f7af54-98f8"

    def test_first_site_id_handles_unloaded_coordinator(self):
        coordinator = MagicMock()
        coordinator.data = None

        assert _first_site_id(coordinator) is None


async def test_migration_version_guard_and_unique_id_collision(
    hass: HomeAssistant,
) -> None:
    """Abort newer-schema migration and avoid duplicate unique_id collisions."""
    # Higher major version returns False
    entry_future = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=0,
        data={},
    )
    entry_future.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry_future) is False

    # Unique id collision during migration keeps original unique_id
    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="target_console_123",
        version=1,
        minor_version=2,
        data={CONF_CONSOLE_ID: "target_console_123"},
    )
    existing_entry.add_to_hass(hass)

    v1_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="old_unique_id",
        version=1,
        minor_version=1,
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE,
            CONF_CONSOLE_ID: "target_console_123",
        },
    )
    v1_entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, v1_entry) is True
    # Should not overwrite existing entry's unique_id
    assert v1_entry.unique_id == "old_unique_id"


async def test_async_setup_entry_discovers_console_identity_from_nvr(
    hass: HomeAssistant,
    enable_custom_integrations,
) -> None:
    """Update the local entry with the NVR's console MAC, name, and title."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Insights (Local)",
        unique_id="local_api_key_123",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "192.168.1.1",
            CONF_API_KEY: "local_api_key_123",
            CONF_VERIFY_SSL: False,
        },
    )
    entry.add_to_hass(hass)

    async def fake_protect_refresh(coord_self):
        coord_self.data = {
            "nvrs": {
                "nvr_1": {
                    # Deliberately unnormalised: setup must store this the
                    # same way the config flow would, or the two identities
                    # disagree and the console gains a second entry.
                    "mac": "AA-BB-CC-DD-EE-11",
                    "name": "Home UDM",
                }
            }
        }

    with (
        patch(
            "custom_components.unifi_insights.async_probe_network",
            new_callable=AsyncMock,
        ) as mock_probe_net,
        patch(
            "custom_components.unifi_insights.async_probe_protect",
            new_callable=AsyncMock,
        ) as mock_probe_prot,
        patch(
            "custom_components.unifi_insights.UnifiInsightsConfigCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.unifi_insights.UnifiInsightsDeviceCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.unifi_insights.UnifiInsightsProtectCoordinator.async_config_entry_first_refresh",
            autospec=True,
            side_effect=fake_protect_refresh,
        ),
        patch(
            "custom_components.unifi_insights.UnifiInsightsProtectCoordinator.async_start_websocket",
            new_callable=AsyncMock,
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
    ):
        mock_probe_net.return_value = ProbeResult(
            ProbeStatus.AVAILABLE, sites=[MagicMock(id="default")]
        )
        mock_probe_prot.return_value = ProbeResult(ProbeStatus.AVAILABLE)

        entry.mock_state(hass, config_entries.ConfigEntryState.LOADED)
        res = await async_setup_entry(hass, entry)
        assert res is True
        assert entry.data.get(CONF_CONSOLE_ID) == "aa:bb:cc:dd:ee:11"
        assert entry.data.get(CONF_CONSOLE_NAME) == "Home UDM"
        assert entry.unique_id == "aa:bb:cc:dd:ee:11"
        assert entry.title == "UniFi - Home UDM"
        await hass.config_entries.async_unload(entry.entry_id)


async def test_async_setup_entry_discovers_console_identity_from_device_gateway(
    hass: HomeAssistant,
    enable_custom_integrations,
) -> None:
    """Update the local entry with the gateway's console MAC and name."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Insights (Local)",
        unique_id="local_api_key_456",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "192.168.1.1",
            CONF_API_KEY: "local_api_key_456",
            CONF_VERIFY_SSL: False,
        },
    )
    entry.add_to_hass(hass)

    async def fake_device_refresh(coord_self):
        coord_self.data = {
            "devices": {
                "site_alpha": {
                    "gw_1": {
                        "is_gateway": True,
                        "macAddress": "22-33-44-55-66-77",
                        "name": "Dream Router",
                    }
                }
            }
        }

    with (
        patch(
            "custom_components.unifi_insights.async_probe_network",
            new_callable=AsyncMock,
        ) as mock_probe_net,
        patch(
            "custom_components.unifi_insights.async_probe_protect",
            new_callable=AsyncMock,
        ) as mock_probe_prot,
        patch(
            "custom_components.unifi_insights.UnifiInsightsConfigCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.unifi_insights.UnifiInsightsDeviceCoordinator.async_config_entry_first_refresh",
            autospec=True,
            side_effect=fake_device_refresh,
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
    ):
        mock_probe_net.return_value = ProbeResult(
            ProbeStatus.AVAILABLE, sites=[MagicMock(id="site_alpha")]
        )
        mock_probe_prot.return_value = ProbeResult(ProbeStatus.UNSUPPORTED)

        entry.mock_state(hass, config_entries.ConfigEntryState.LOADED)
        res = await async_setup_entry(hass, entry)
        assert res is True
        assert entry.data.get(CONF_CONSOLE_ID) == "22:33:44:55:66:77"
        assert entry.data.get(CONF_CONSOLE_NAME) == "Dream Router"
        assert entry.unique_id == "22:33:44:55:66:77"
        assert entry.title == "UniFi - Dream Router"
        await hass.config_entries.async_unload(entry.entry_id)


async def test_console_identity_extra_coverage_branches(
    hass: HomeAssistant,
    enable_custom_integrations,
) -> None:
    """Cover site lookup, device parsing, setup fallback, and Protect edge cases."""

    # 1. _first_site_id when sites is not a dict
    coord = MagicMock()
    coord.data = {"sites": "not_a_dict"}
    assert _first_site_id(coord) is None

    # 2. Local setup uses the host, with non-dict site devices handled defensively.
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Insights (Local)",
        unique_id="local_api_key_fallback",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "192.168.1.100",
            CONF_API_KEY: "local_api_key_fallback",
            CONF_VERIFY_SSL: False,
        },
    )
    entry.add_to_hass(hass)

    async def fake_device_data_with_non_dicts(coord_self):
        coord_self.data = {
            "devices": {
                "invalid_site": "not_a_dict",
                "valid_site": {
                    "invalid_dev": "not_a_dict",
                    "plain_switch": {
                        "is_gateway": False,
                        "model": "USW-Flex",
                        "mac": "00:11:22:33:44:55",
                    },
                },
            }
        }

    async def fake_config_sites(coord_self):
        coord_self.data = {
            "sites": {
                "my_custom_site": {"id": "my_custom_site"},
            }
        }

    with (
        patch(
            "custom_components.unifi_insights.async_probe_network",
            new_callable=AsyncMock,
        ) as mock_probe_net,
        patch(
            "custom_components.unifi_insights.async_probe_protect",
            new_callable=AsyncMock,
        ) as mock_probe_prot,
        patch(
            "custom_components.unifi_insights.UnifiInsightsConfigCoordinator.async_config_entry_first_refresh",
            autospec=True,
            side_effect=fake_config_sites,
        ),
        patch(
            "custom_components.unifi_insights.UnifiInsightsDeviceCoordinator.async_config_entry_first_refresh",
            autospec=True,
            side_effect=fake_device_data_with_non_dicts,
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
    ):
        mock_probe_net.return_value = ProbeResult(
            ProbeStatus.AVAILABLE, sites=[MagicMock(id="my_custom_site")]
        )
        mock_probe_prot.return_value = ProbeResult(ProbeStatus.UNSUPPORTED)

        entry.mock_state(hass, config_entries.ConfigEntryState.LOADED)
        res = await async_setup_entry(hass, entry)
        assert res is True
        assert entry.data.get(CONF_CONSOLE_ID) == "192.168.1.100"
        assert entry.unique_id == "192.168.1.100"
        await hass.config_entries.async_unload(entry.entry_id)

    # 3. Protect coordinator methods when client is None or timers already stopped
    protect_coord = UnifiInsightsProtectCoordinator(
        hass,
        network_client=MagicMock(),
        protect_client=None,
        entry=entry,
    )
    protect_coord._unsub_sensor_reconcile = None
    protect_coord._stop_sensor_reconcile_timer()
    await protect_coord.async_refresh_sensors()


async def test_config_flow_local_probe_protect_nvr_and_reconfigure_mismatch(
    hass: HomeAssistant,
) -> None:
    """Test NVR discovery and reject reconfiguration to a different console."""
    # 1. Local flow with Protect probe returning NVR
    net_cm, net_client = _make_mock_client()
    net_client.sites.get_all = AsyncMock(
        side_effect=UniFiResponseError("Not Found", status_code=404)
    )

    nvr_mock = MagicMock()
    nvr_mock.mac = "aa-bb-cc-dd-ee-99"
    nvr_mock.name = "Test NVR Console"
    nvr_mock.display_name = None

    protect_client = MagicMock()
    protect_client.nvr.get = AsyncMock(return_value=nvr_mock)
    protect_client.cameras.get_all = AsyncMock(return_value=[])
    protect_cm = MagicMock()
    protect_cm.__aenter__ = AsyncMock(return_value=protect_client)
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
        patch("custom_components.unifi_insights.async_setup_entry", return_value=True),
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
                CONF_HOST: "https://192.168.1.10",
                CONF_API_KEY: "protect_nvr_key",
                CONF_VERIFY_SSL: False,
            },
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"].get(CONF_CONSOLE_ID) == "aa:bb:cc:dd:ee:99"
        assert result["data"].get(CONF_CONSOLE_NAME) == "Test NVR Console"

    # 2. Remote reconfigure aborts on mismatched console_id
    remote_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="existing_console_111",
        title="UniFi - Remote Console",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE,
            CONF_API_KEY: "remote_api_key",
            CONF_CONSOLE_ID: "existing_console_111",
        },
    )
    remote_entry.add_to_hass(hass)

    hosts = [
        _remote_host("existing_console_111", "Console 1"),
        _remote_host("different_console_222", "Console 2"),
    ]
    cloud_cm, _ = _make_mock_client(
        get_hosts=hosts,
        sites=[MagicMock(id="default", name="Default")],
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=cloud_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": remote_entry.entry_id,
            },
        )
        assert result["step_id"] == "reconfigure"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_API_KEY: "new_api_key",
                CONF_CONSOLE_ID: "different_console_222",
            },
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "account_mismatch"


async def test_console_identity_final_branch_coverage(
    hass: HomeAssistant,
) -> None:
    """Cover remaining edge cases in config flow and protect coordinator."""
    # 1. Protect coordinator _start_sensor_reconcile_timer when timer already active
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    p_coord = UnifiInsightsProtectCoordinator(
        hass, network_client=MagicMock(), protect_client=MagicMock(), entry=entry
    )
    p_coord._unsub_sensor_reconcile = MagicMock()
    p_coord._start_sensor_reconcile_timer()
    assert p_coord._unsub_sensor_reconcile is not None

    # 2. Local flow retains the site name but uses the host as its fallback ID.
    net_cm, _ = _make_mock_client(
        sites=[MagicMock(id="custom_site_beta", name="Beta Site")],
        devices=[],
    )
    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
        patch("custom_components.unifi_insights.async_setup_entry", return_value=True),
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
                CONF_HOST: "https://192.168.1.15",
                CONF_API_KEY: "key_custom_site",
                CONF_VERIFY_SSL: False,
            },
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["result"].unique_id == "https://192.168.1.15"

    # 3. Local flow probe protect NVR inspect exception handling
    net_cm_err, net_client_err = _make_mock_client()
    net_client_err.sites.get_all = AsyncMock(
        side_effect=UniFiResponseError("Not Found", status_code=404)
    )

    protect_client = MagicMock()
    protect_client.nvr.get = AsyncMock(side_effect=RuntimeError("NVR inspect boom"))
    protect_client.cameras.get_all = AsyncMock(return_value=[MagicMock()])
    protect_cm = MagicMock()
    protect_cm.__aenter__ = AsyncMock(return_value=protect_client)
    protect_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm_err,
        ),
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient",
            return_value=protect_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
        patch("custom_components.unifi_insights.async_setup_entry", return_value=True),
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
                CONF_HOST: "https://192.168.1.25",
                CONF_API_KEY: "key_nvr_err",
                CONF_VERIFY_SSL: False,
            },
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY

    # 4. Remote reconfigure console validation raises UniFiAuthenticationError
    remote_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="auth_fail_console",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE,
            CONF_API_KEY: "auth_key",
            CONF_CONSOLE_ID: "auth_fail_console",
        },
    )
    remote_entry.add_to_hass(hass)

    hosts = [_remote_host("auth_fail_console", "Fail Console")]
    cloud_client = MagicMock()
    cloud_client.get_hosts = AsyncMock(return_value=hosts)
    cloud_client.sites.get_all = AsyncMock(return_value=[])
    cloud_cm = MagicMock()
    cloud_cm.__aenter__ = AsyncMock(return_value=cloud_client)
    cloud_cm.__aexit__ = AsyncMock(return_value=None)

    protect_client_empty = MagicMock()
    protect_client_empty.cameras.get_all = AsyncMock(return_value=[])
    protect_client_empty.nvr.get = AsyncMock(return_value=None)
    protect_cm_empty = MagicMock()
    protect_cm_empty.__aenter__ = AsyncMock(return_value=protect_client_empty)
    protect_cm_empty.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=cloud_cm,
        ),
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient",
            return_value=protect_cm_empty,
        ),
        patch("custom_components.unifi_insights.config_flow.ApiKeyAuth"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": remote_entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_API_KEY: "new_auth_key",
                CONF_CONSOLE_ID: "auth_fail_console",
            },
        )
        assert result["type"] == FlowResultType.FORM
        assert (
            require_not_none(result["errors"]).get(CONF_CONSOLE_ID)
            == "invalid_console_id"
        )


async def test_async_setup_entry_handles_malformed_coordinator_data(
    hass: HomeAssistant,
    enable_custom_integrations,
) -> None:
    """Malformed nvrs/devices payloads fall back to the site id, not a crash.

    Covers the defensive isinstance() guards around the NVR and device walk:
    a non-empty ``nvrs`` dict whose first value isn't itself a dict, and a
    ``devices`` value that isn't a dict at all.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Insights (Local)",
        unique_id="local_api_key_malformed",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "192.168.1.50",
            CONF_API_KEY: "local_api_key_malformed",
            CONF_VERIFY_SSL: False,
        },
    )
    entry.add_to_hass(hass)

    async def fake_protect_refresh(coord_self):
        coord_self.data = {"nvrs": {"nvr_1": "not-a-dict"}}

    async def fake_device_refresh(coord_self):
        coord_self.data = {"devices": "not-a-dict"}

    async def fake_config_sites(coord_self):
        coord_self.data = {"sites": {"site_xyz": {"id": "site_xyz"}}}

    with (
        patch(
            "custom_components.unifi_insights.async_probe_network",
            new_callable=AsyncMock,
        ) as mock_probe_net,
        patch(
            "custom_components.unifi_insights.async_probe_protect",
            new_callable=AsyncMock,
        ) as mock_probe_prot,
        patch(
            "custom_components.unifi_insights.UnifiInsightsConfigCoordinator.async_config_entry_first_refresh",
            autospec=True,
            side_effect=fake_config_sites,
        ),
        patch(
            "custom_components.unifi_insights.UnifiInsightsDeviceCoordinator.async_config_entry_first_refresh",
            autospec=True,
            side_effect=fake_device_refresh,
        ),
        patch(
            "custom_components.unifi_insights.UnifiInsightsProtectCoordinator.async_config_entry_first_refresh",
            autospec=True,
            side_effect=fake_protect_refresh,
        ),
        patch(
            "custom_components.unifi_insights.UnifiInsightsProtectCoordinator.async_start_websocket",
            new_callable=AsyncMock,
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
    ):
        mock_probe_net.return_value = ProbeResult(
            ProbeStatus.AVAILABLE, sites=[MagicMock(id="site_xyz")]
        )
        mock_probe_prot.return_value = ProbeResult(ProbeStatus.AVAILABLE)

        entry.mock_state(hass, config_entries.ConfigEntryState.LOADED)
        res = await async_setup_entry(hass, entry)
        assert res is True
        # Neither the NVR nor the device payload yielded a usable mac, so
        # identity falls back to the explicit console host.
        assert entry.data.get(CONF_CONSOLE_ID) == "192.168.1.50"
        assert entry.unique_id == "192.168.1.50"
        await hass.config_entries.async_unload(entry.entry_id)


async def test_async_setup_entry_keeps_preset_console_name_from_nvr(
    hass: HomeAssistant,
    enable_custom_integrations,
) -> None:
    """An already-known console name is not clobbered by the NVR's name."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi - Preset Name",
        unique_id="local_api_key_preset_name",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "192.168.1.51",
            CONF_API_KEY: "local_api_key_preset_name",
            CONF_VERIFY_SSL: False,
            CONF_CONSOLE_NAME: "Preset Name",
        },
    )
    entry.add_to_hass(hass)

    async def fake_protect_refresh(coord_self):
        coord_self.data = {
            "nvrs": {
                "nvr_1": {
                    "mac": "aa:bb:cc:dd:ee:22",
                    "name": "NVR Reported Name",
                }
            }
        }

    with (
        patch(
            "custom_components.unifi_insights.async_probe_network",
            new_callable=AsyncMock,
        ) as mock_probe_net,
        patch(
            "custom_components.unifi_insights.async_probe_protect",
            new_callable=AsyncMock,
        ) as mock_probe_prot,
        patch(
            "custom_components.unifi_insights.UnifiInsightsConfigCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.unifi_insights.UnifiInsightsDeviceCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.unifi_insights.UnifiInsightsProtectCoordinator.async_config_entry_first_refresh",
            autospec=True,
            side_effect=fake_protect_refresh,
        ),
        patch(
            "custom_components.unifi_insights.UnifiInsightsProtectCoordinator.async_start_websocket",
            new_callable=AsyncMock,
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
    ):
        mock_probe_net.return_value = ProbeResult(
            ProbeStatus.AVAILABLE, sites=[MagicMock(id="default")]
        )
        mock_probe_prot.return_value = ProbeResult(ProbeStatus.AVAILABLE)

        entry.mock_state(hass, config_entries.ConfigEntryState.LOADED)
        res = await async_setup_entry(hass, entry)
        assert res is True
        # The mac is still adopted for identity...
        assert entry.data.get(CONF_CONSOLE_ID) == "aa:bb:cc:dd:ee:22"
        # ...but the pre-existing console name is left alone.
        assert entry.data.get(CONF_CONSOLE_NAME) == "Preset Name"
        await hass.config_entries.async_unload(entry.entry_id)


async def test_async_setup_entry_keeps_preset_console_name_from_device(
    hass: HomeAssistant,
    enable_custom_integrations,
) -> None:
    """An already-known console name is not clobbered by a discovered gateway's name."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi - Preset Device Name",
        unique_id="local_api_key_preset_device",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "192.168.1.52",
            CONF_API_KEY: "local_api_key_preset_device",
            CONF_VERIFY_SSL: False,
            CONF_CONSOLE_NAME: "Preset Device Name",
        },
    )
    entry.add_to_hass(hass)

    async def fake_device_refresh(coord_self):
        coord_self.data = {
            "devices": {
                "site_alpha": {
                    "gw_1": {
                        "is_gateway": True,
                        "macAddress": "33-44-55-66-77-88",
                        "name": "Reported Gateway Name",
                    }
                }
            }
        }

    with (
        patch(
            "custom_components.unifi_insights.async_probe_network",
            new_callable=AsyncMock,
        ) as mock_probe_net,
        patch(
            "custom_components.unifi_insights.async_probe_protect",
            new_callable=AsyncMock,
        ) as mock_probe_prot,
        patch(
            "custom_components.unifi_insights.UnifiInsightsConfigCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.unifi_insights.UnifiInsightsDeviceCoordinator.async_config_entry_first_refresh",
            autospec=True,
            side_effect=fake_device_refresh,
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
    ):
        mock_probe_net.return_value = ProbeResult(
            ProbeStatus.AVAILABLE, sites=[MagicMock(id="site_alpha")]
        )
        mock_probe_prot.return_value = ProbeResult(ProbeStatus.UNSUPPORTED)

        entry.mock_state(hass, config_entries.ConfigEntryState.LOADED)
        res = await async_setup_entry(hass, entry)
        assert res is True
        assert entry.data.get(CONF_CONSOLE_ID) == "33:44:55:66:77:88"
        assert entry.data.get(CONF_CONSOLE_NAME) == "Preset Device Name"
        await hass.config_entries.async_unload(entry.entry_id)


async def test_async_setup_entry_backfills_credential_identity_when_no_mac_found(
    hass: HomeAssistant,
    enable_custom_integrations,
) -> None:
    """Backfill a legacy credential identity with the host when no MAC is found."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="local_api_key_unstable",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://Console.Example.Invalid",
            CONF_API_KEY: "local_api_key_unstable",
            CONF_VERIFY_SSL: False,
            CONF_CONSOLE_ID: "local_api_key_unstable",
        },
    )
    entry.add_to_hass(hass)

    async def fake_device_refresh(coord_self):
        """Refresh a console without discovered devices or a usable MAC."""
        coord_self.data = {"devices": {}}

    with (
        patch(
            "custom_components.unifi_insights.async_probe_network",
            new_callable=AsyncMock,
        ) as mock_probe_net,
        patch(
            "custom_components.unifi_insights.async_probe_protect",
            new_callable=AsyncMock,
        ) as mock_probe_prot,
        patch(
            "custom_components.unifi_insights.UnifiInsightsConfigCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.unifi_insights.UnifiInsightsDeviceCoordinator.async_config_entry_first_refresh",
            autospec=True,
            side_effect=fake_device_refresh,
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
    ):
        mock_probe_net.return_value = ProbeResult(
            ProbeStatus.AVAILABLE, sites=[MagicMock(id="default")]
        )
        mock_probe_prot.return_value = ProbeResult(ProbeStatus.UNSUPPORTED)

        entry.mock_state(hass, config_entries.ConfigEntryState.LOADED)
        res = await async_setup_entry(hass, entry)
        assert res is True
        assert entry.data.get(CONF_CONSOLE_ID) == "https://console.example.invalid"
        assert entry.unique_id == "https://console.example.invalid"
        await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("source", ["user", "reconfigure"])
async def test_local_no_mac_flow_keeps_shared_site_consoles_distinct(
    hass: HomeAssistant, source: str
) -> None:
    """Use distinct actual hosts for initial and legacy reconfiguration flows."""
    identities = []
    for index in ("a", "b"):
        host = f"https://Console{index.upper()}.Example.Invalid"
        context: ConfigFlowContext = {"source": source}
        if source == "reconfigure":
            stored_key = f"synthetic_old_key_{index}"
            entry = MockConfigEntry(
                domain=DOMAIN,
                data={
                    CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
                    CONF_CONSOLE_ID: stored_key,
                    CONF_API_KEY: stored_key,
                    CONF_HOST: host,
                },
                unique_id=stored_key,
            )
            entry.add_to_hass(hass)
            context["entry_id"] = entry.entry_id
        network, _ = _make_mock_client(
            sites=[MagicMock(id="site_shared", name="Synthetic site")],
            devices=[],
        )
        with (
            patch(
                "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
                return_value=network,
            ),
            patch("custom_components.unifi_insights.config_flow.LocalAuth"),
            patch(
                "custom_components.unifi_insights.async_setup_entry", return_value=True
            ),
            patch(
                "custom_components.unifi_insights.async_migrate_entry",
                return_value=True,
            ),
        ):
            result = await hass.config_entries.flow.async_init(DOMAIN, context=context)
            if source == "user":
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"],
                    user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL},
                )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={
                    CONF_HOST: host,
                    CONF_API_KEY: f"synthetic_new_key_{index}",
                    CONF_VERIFY_SSL: False,
                },
            )
            await hass.async_block_till_done()
        if source == "user":
            assert result["type"] == FlowResultType.CREATE_ENTRY
            identities.append(result["result"].unique_id)
        else:
            assert result["type"] == FlowResultType.ABORT
            assert result["reason"] == "reconfigure_successful"
            assert entry.data[CONF_CONSOLE_ID] == entry.unique_id
            identities.append(entry.unique_id)
    assert identities == [
        "https://consolea.example.invalid",
        "https://consoleb.example.invalid",
    ]


async def test_migration_skips_v1_block_below_version_one(
    hass: HomeAssistant,
) -> None:
    """Leave a config entry below version 1 untouched and report success."""
    entry_v0 = MockConfigEntry(
        domain=DOMAIN,
        version=0,
        minor_version=0,
        unique_id="some_unique_id",
        data={CONF_API_KEY: "some_key"},
    )
    entry_v0.add_to_hass(hass)

    migrated = await async_migrate_entry(hass, entry_v0)
    assert migrated is True
    # The version==1 migration block never ran: nothing was touched.
    assert entry_v0.version == 0
    assert entry_v0.unique_id == "some_unique_id"
    assert dict(entry_v0.data) == {CONF_API_KEY: "some_key"}


async def test_local_flow_device_scan_skips_non_console_and_captures_name(
    hass: HomeAssistant,
) -> None:
    """The device scan skips non-console hardware and records a console's name.

    Exercises the loop-continue branch when a device's type/model doesn't
    match any console token, and the case where a matched console exposes
    a name but no usable MAC - identity then falls back to the normalized
    configured host, keeping consoles sharing a site ID separate.
    """
    dev_switch = MagicMock()
    dev_switch.type = None
    dev_switch.model = "USW Pro Max 24"
    dev_switch.mac = None
    dev_switch.macAddress = None
    dev_switch.name = "Switch 1"

    dev_console = MagicMock()
    dev_console.type = None
    dev_console.model = "UniFi Dream Machine PRO SE"
    dev_console.mac = None
    dev_console.macAddress = None
    dev_console.name = "Crestwood"

    net_cm, _ = _make_mock_client(
        sites=[MagicMock(id="site_gamma", name="Gamma Site")],
        devices=[dev_switch, dev_console],
    )

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm,
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
                CONF_HOST: "https://192.168.1.60",
                CONF_API_KEY: "key_device_scan",
                CONF_VERIFY_SSL: False,
            },
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        # The switch was skipped; the Dream Machine matched by model but
        # exposed no mac, so identity falls back to the explicit console host.
        assert result["result"].unique_id == "https://192.168.1.60"
        assert result["data"].get(CONF_CONSOLE_ID) is None


async def test_local_flow_protect_probe_without_nvr_attribute(
    hass: HomeAssistant,
) -> None:
    """Protect clients that expose no nvr sub-client skip NVR inspection cleanly."""
    net_cm_err, net_client_err = _make_mock_client()
    net_client_err.sites.get_all = AsyncMock(
        side_effect=UniFiResponseError("Not Found", status_code=404)
    )

    class _NvrlessProtectClient:
        """A Protect client shape that exposes no nvr sub-client at all."""

        def __init__(self) -> None:
            self.cameras = MagicMock()
            self.cameras.get_all = AsyncMock(return_value=[MagicMock()])
            self.close = AsyncMock()

    protect_client = _NvrlessProtectClient()
    protect_cm = MagicMock()
    protect_cm.__aenter__ = AsyncMock(return_value=protect_client)
    protect_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm_err,
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
                CONF_HOST: "https://192.168.1.61",
                CONF_API_KEY: "key_nvrless",
                CONF_VERIFY_SSL: False,
            },
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        # Falls back to the host-based id since there's no nvr sub-client
        # to inspect for a mac.
        assert result["result"].unique_id == "https://192.168.1.61"
        assert result["data"].get(CONF_CONSOLE_ID) is None


async def test_local_flow_protect_probe_nvr_returns_none(
    hass: HomeAssistant,
) -> None:
    """Keep the host-based id when an available Protect API has no NVR record."""

    net_cm_err, net_client_err = _make_mock_client()
    net_client_err.sites.get_all = AsyncMock(
        side_effect=UniFiResponseError("Not Found", status_code=404)
    )

    protect_client = MagicMock()
    protect_client.cameras.get_all = AsyncMock(return_value=[MagicMock()])
    protect_client.nvr.get = AsyncMock(return_value=None)
    protect_cm = MagicMock()
    protect_cm.__aenter__ = AsyncMock(return_value=protect_client)
    protect_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm_err,
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
                CONF_HOST: "https://192.168.1.62",
                CONF_API_KEY: "key_nvr_none",
                CONF_VERIFY_SSL: False,
            },
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["result"].unique_id == "https://192.168.1.62"
        assert result["data"].get(CONF_CONSOLE_ID) is None


@pytest.mark.parametrize(
    "host",
    ["", " \t ", "bad host", "synthetic-probe-key", "SYNTHETIC:PROBE:KEY"],
    ids=["empty", "whitespace", "malformed", "credential", "normalized_credential"],
)
async def test_available_network_probe_rejects_unsafe_host_identity(
    hass: HomeAssistant, host: str
) -> None:
    """Keep connectivity usable without adopting a malformed or credential host."""
    network_context, network_client = _make_mock_client(
        sites=[SimpleNamespace(id="site_shared", name="Synthetic site")],
        devices=[
            SimpleNamespace(
                model="UDM-Pro",
                name="Synthetic gateway without a MAC",
                is_gateway=True,
                mac=None,
            )
        ],
    )
    flow = UnifiInsightsConfigFlow()
    flow.hass = hass
    entries_before = hass.config_entries.async_entries(DOMAIN)
    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=network_context,
        ),
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient"
        ) as protect_client,
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        available, error, console_info = await flow._async_validate_local_connection(
            host, "synthetic-probe-key"
        )

    assert available
    assert error is None
    assert console_info == {"name": "Synthetic site"}
    assert hass.config_entries.async_entries(DOMAIN) == entries_before
    network_client.sites.get_all.assert_awaited_once_with(expected_unsupported=True)
    network_client.devices.get_all.assert_awaited_once_with(site_id="site_shared")
    network_context.__aexit__.assert_awaited_once()
    protect_client.assert_not_called()


async def test_local_reconfigure_with_blank_host_preserves_console_id(
    hass: HomeAssistant,
) -> None:
    """Reconfiguring to a host that normalizes to "" keeps the stored console_id.

    A host that resolves to a falsy id means `_async_validate_local_connection`
    never populated `console_info["id"]` with anything usable, so there is
    nothing better to store than what the entry already had.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="existing_console_id_777",
        title="UniFi - Existing Console",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "192.168.1.70",
            CONF_API_KEY: "existing_key",
            CONF_CONSOLE_ID: "existing_console_id_777",
            CONF_VERIFY_SSL: False,
        },
    )
    entry.add_to_hass(hass)

    net_cm_err, net_client_err = _make_mock_client()
    net_client_err.sites.get_all = AsyncMock(
        side_effect=UniFiResponseError("Not Found", status_code=404)
    )
    protect_client = MagicMock()
    protect_client.cameras.get_all = AsyncMock(return_value=[MagicMock()])
    protect_client.nvr.get = AsyncMock(return_value=None)
    protect_cm = MagicMock()
    protect_cm.__aenter__ = AsyncMock(return_value=protect_client)
    protect_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=net_cm_err,
        ),
        patch(
            "custom_components.unifi_insights.config_flow.UniFiProtectClient",
            return_value=protect_cm,
        ),
        patch("custom_components.unifi_insights.config_flow.LocalAuth"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert result["step_id"] == "reconfigure"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_HOST: "",
                CONF_API_KEY: "existing_key",
                CONF_VERIFY_SSL: False,
            },
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        # The unique_id (the real dedup identity) is preserved even though
        # the id could not be re-derived from this host, and so is the stored
        # console id: a host that yields no usable id must not erase it.
        assert entry.unique_id == "existing_console_id_777"
        assert entry.data[CONF_CONSOLE_ID] == "existing_console_id_777"


def test_first_non_default_site_id_default_only_and_empty() -> None:
    """first_non_default_site_id returns None when only default site or empty."""
    assert first_non_default_site_id(["default"]) is None
    assert first_non_default_site_id([]) is None
    assert first_non_default_site_id(["default", "site2"]) == "site2"
