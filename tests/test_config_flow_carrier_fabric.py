# Copyright (c) 2026 Ruaan Deysel
"""Tests for UniFi Carrier Fabric config flow."""

from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_insights.const import (
    CONF_CARRIER_ACTIONS,
    CONF_CARRIER_ORG_ID,
    CONF_CONNECTION_TYPE,
    CONF_TRACK_SUBSCRIBERS,
    CONNECTION_TYPE_CARRIER_FABRIC,
    DOMAIN,
)
from custom_components.unifi_insights.probe import ProbeResult, ProbeStatus

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_carrier_fabric_selection_in_user_step(hass):
    """Test Carrier Fabric option in user step routes to carrier_fabric step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "carrier_fabric"


async def test_carrier_fabric_flow_success_with_org_id(hass):
    """Test successful setup generates carrier_<orgId> unique_id."""
    with patch(
        "custom_components.unifi_insights.config_flow.async_probe_carrier_fabric",
        return_value=ProbeResult(
            status=ProbeStatus.AVAILABLE,
            org_id="org_12345",
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "secret_isp_key"},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "UniFi Carrier Fabric"
    assert result["result"].unique_id == "carrier_org_12345"
    assert result["data"] == {
        CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
        CONF_API_KEY: "secret_isp_key",
        CONF_CARRIER_ORG_ID: "org_12345",
    }


async def test_carrier_fabric_flow_success_fallback_unique_id(hass):
    """Test successful setup without org_id generates carrier_key_<hash> unique_id."""
    api_key = "secret_isp_key_no_org"
    # First 16 hex chars of SHA-256("secret_isp_key_no_org"), used as test fixture.
    expected_hash = "ea04d5e823c90f95"
    expected_unique_id = f"carrier_key_{expected_hash}"

    with patch(
        "custom_components.unifi_insights.config_flow.async_probe_carrier_fabric",
        return_value=ProbeResult(
            status=ProbeStatus.EMPTY,
            org_id=None,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: api_key},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == expected_unique_id
    assert result["data"] == {
        CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
        CONF_API_KEY: api_key,
        CONF_CARRIER_ORG_ID: None,
    }


async def test_carrier_fabric_flow_already_configured(hass):
    """Test already configured unique_id aborts the flow."""
    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="carrier_org_already_here",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            CONF_API_KEY: "existing_key",
            CONF_CARRIER_ORG_ID: "org_already_here",
        },
    )
    existing_entry.add_to_hass(hass)

    with patch(
        "custom_components.unifi_insights.config_flow.async_probe_carrier_fabric",
        return_value=ProbeResult(
            status=ProbeStatus.AVAILABLE,
            org_id="org_already_here",
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "different_key_same_org"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_carrier_fabric_flow_missing_scope_error(hass):
    """Test missing_scope maps to carrier_missing_scope error."""
    with patch(
        "custom_components.unifi_insights.config_flow.async_probe_carrier_fabric",
        return_value=ProbeResult(
            status=ProbeStatus.AUTH_FAILED,
            missing_scope=True,
            error=Exception("insufficient scope"),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "key_without_scope"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "carrier_missing_scope"}


async def test_carrier_fabric_flow_invalid_auth_error(hass):
    """Test 401 AUTH_FAILED maps to invalid_auth on api_key field."""
    with patch(
        "custom_components.unifi_insights.config_flow.async_probe_carrier_fabric",
        return_value=ProbeResult(
            status=ProbeStatus.AUTH_FAILED,
            missing_scope=False,
            error=Exception("401 unauthorized"),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "bad_key"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_API_KEY: "invalid_auth"}


async def test_carrier_fabric_flow_unreachable_error(hass):
    """Test UNREACHABLE maps to cannot_connect error."""
    with patch(
        "custom_components.unifi_insights.config_flow.async_probe_carrier_fabric",
        return_value=ProbeResult(
            status=ProbeStatus.UNREACHABLE,
            error=Exception("Connection refused"),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "some_key"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_carrier_fabric_reauth_success(hass):
    """Test successful reauth updates API key and reloads entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="carrier_org_999",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            CONF_API_KEY: "old_key",
            CONF_CARRIER_ORG_ID: "org_999",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.unifi_insights.config_flow.async_probe_carrier_fabric",
        return_value=ProbeResult(
            status=ProbeStatus.AVAILABLE,
            org_id="org_999",
        ),
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_carrier_fabric"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "new_working_key"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "new_working_key"


async def test_carrier_fabric_reauth_org_mismatch(hass):
    """Test reauth aborts if new key belongs to a different organisation."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="carrier_org_999",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            CONF_API_KEY: "old_key",
            CONF_CARRIER_ORG_ID: "org_999",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.unifi_insights.config_flow.async_probe_carrier_fabric",
        return_value=ProbeResult(
            status=ProbeStatus.AVAILABLE,
            org_id="org_DIFFERENT",
        ),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "key_for_different_org"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "carrier_org_mismatch"


async def test_carrier_fabric_reauth_backfills_org_id_if_none(hass):
    """Test reauth backfills carrier_org_id if it was previously None."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="carrier_key_1234567890abcdef",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            CONF_API_KEY: "old_key",
            CONF_CARRIER_ORG_ID: None,
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.unifi_insights.config_flow.async_probe_carrier_fabric",
        return_value=ProbeResult(
            status=ProbeStatus.AVAILABLE,
            org_id="org_now_discovered",
        ),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "new_key"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_CARRIER_ORG_ID] == "org_now_discovered"


async def test_carrier_fabric_reconfigure_success(hass):
    """Test successful reconfigure updates key."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="carrier_org_777",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            CONF_API_KEY: "old_key",
            CONF_CARRIER_ORG_ID: "org_777",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.unifi_insights.config_flow.async_probe_carrier_fabric",
        return_value=ProbeResult(
            status=ProbeStatus.AVAILABLE,
            org_id="org_777",
        ),
    ):
        result = await entry.start_reconfigure_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure_carrier_fabric"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "reconfigured_key"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_API_KEY] == "reconfigured_key"


async def test_carrier_fabric_reconfigure_org_mismatch(hass):
    """Test reconfigure aborts if org IDs differ."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="carrier_org_777",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            CONF_API_KEY: "old_key",
            CONF_CARRIER_ORG_ID: "org_777",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.unifi_insights.config_flow.async_probe_carrier_fabric",
        return_value=ProbeResult(
            status=ProbeStatus.AVAILABLE,
            org_id="org_SOME_OTHER_ORG",
        ),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "key_with_wrong_org"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "carrier_org_mismatch"


async def test_carrier_fabric_options_flow(hass):
    """Test options flow shows only track_subscribers and carrier_actions."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="carrier_org_opts",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            CONF_API_KEY: "some_key",
            CONF_CARRIER_ORG_ID: "org_opts",
        },
        options={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    # Schema contains track_subscribers and carrier_actions booleans
    schema_keys = [vol_key.schema for vol_key in result["data_schema"].schema]
    assert CONF_TRACK_SUBSCRIBERS in schema_keys
    assert CONF_CARRIER_ACTIONS in schema_keys
    assert len(schema_keys) == 2

    # Submit options
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_TRACK_SUBSCRIBERS: True,
            CONF_CARRIER_ACTIONS: True,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_TRACK_SUBSCRIBERS: True,
        CONF_CARRIER_ACTIONS: True,
    }


@pytest.mark.parametrize(
    ("probe_status", "missing_scope", "expected_errors"),
    [
        (ProbeStatus.AUTH_FAILED, False, {CONF_API_KEY: "invalid_auth"}),
        (ProbeStatus.AUTH_FAILED, True, {"base": "carrier_missing_scope"}),
        (ProbeStatus.UNREACHABLE, False, {"base": "cannot_connect"}),
        (ProbeStatus.ERROR, False, {"base": "unknown"}),
    ],
)
async def test_carrier_fabric_reauth_error_branches(
    hass, probe_status, missing_scope, expected_errors
):
    """Test Carrier Fabric reauth error branches redisplay form with proper errors."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="carrier_org_reauth_err",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            CONF_API_KEY: "old_key",
            CONF_CARRIER_ORG_ID: "org_reauth_err",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.unifi_insights.config_flow.async_probe_carrier_fabric",
        return_value=ProbeResult(
            status=probe_status,
            missing_scope=missing_scope,
            error=Exception("Probe failed"),
        ),
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_carrier_fabric"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "failed_key"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_carrier_fabric"
    assert result["errors"] == expected_errors


async def test_carrier_fabric_reauth_unexpected_exception(hass):
    """Test unexpected exception in Carrier Fabric reauth shows unknown error."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="carrier_org_reauth_err2",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            CONF_API_KEY: "old_key",
            CONF_CARRIER_ORG_ID: "org_reauth_err2",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.unifi_insights.config_flow.async_probe_carrier_fabric",
        side_effect=RuntimeError("Unexpected probe crash"),
    ):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "crashing_key"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_carrier_fabric"
    assert result["errors"] == {"base": "unknown"}


@pytest.mark.parametrize(
    ("probe_status", "missing_scope", "expected_errors"),
    [
        (ProbeStatus.AUTH_FAILED, False, {CONF_API_KEY: "invalid_auth"}),
        (ProbeStatus.AUTH_FAILED, True, {"base": "carrier_missing_scope"}),
        (ProbeStatus.UNREACHABLE, False, {"base": "cannot_connect"}),
        (ProbeStatus.ERROR, False, {"base": "unknown"}),
    ],
)
async def test_carrier_fabric_reconfigure_error_branches(
    hass, probe_status, missing_scope, expected_errors
):
    """Test Carrier Fabric reconfigure error branches redisplay form."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="carrier_org_reconfig_err",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            CONF_API_KEY: "old_key",
            CONF_CARRIER_ORG_ID: "org_reconfig_err",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.unifi_insights.config_flow.async_probe_carrier_fabric",
        return_value=ProbeResult(
            status=probe_status,
            missing_scope=missing_scope,
            error=Exception("Probe failed"),
        ),
    ):
        result = await entry.start_reconfigure_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure_carrier_fabric"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "failed_key"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_carrier_fabric"
    assert result["errors"] == expected_errors


async def test_carrier_fabric_reconfigure_unexpected_exception(hass):
    """Test unexpected exception in Carrier Fabric reconfigure shows unknown error."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="carrier_org_reconfig_err2",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            CONF_API_KEY: "old_key",
            CONF_CARRIER_ORG_ID: "org_reconfig_err2",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.unifi_insights.config_flow.async_probe_carrier_fabric",
        side_effect=RuntimeError("Unexpected probe crash"),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_API_KEY: "crashing_key"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_carrier_fabric"
    assert result["errors"] == {"base": "unknown"}
