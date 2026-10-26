# Copyright 2026 UniFi Insights contributors
"""Tests verifying schema and flow backend compatibility across HA engines."""

from __future__ import annotations

import importlib.util
import sys
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_VERIFY_SSL
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv

from custom_components.unifi_insights import (
    config_flow,
    services,
    websocket_api,
)
from custom_components.unifi_insights.const import (
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_LOCAL,
    DEFAULT_API_HOST,
    DOMAIN,
    HDR_MODE_AUTO,
)
from tests.fixtures.assertions import require_not_none

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _get_ha_schema_backend() -> Any:
    """Resolve active Home Assistant config validation backend."""
    backend = getattr(cv, "probatio", getattr(cv, "vol", None))
    assert backend is not None, "No backend found in config_validation"
    return backend


def test_schema_backend_runtime_identity() -> None:
    """Verify runtime schema identity corresponds to HA config_validation."""
    backend = _get_ha_schema_backend()

    for mod in (config_flow, services, websocket_api):
        assert mod.vol.Schema is backend.Schema
        assert mod.vol.Invalid is backend.Invalid

    assert isinstance(services.POWER_CYCLE_PORT_SCHEMA, backend.Schema)
    assert isinstance(services.SET_HDR_MODE_SCHEMA, backend.Schema)
    assert isinstance(services.GENERATE_VOUCHER_SCHEMA, backend.Schema)
    assert isinstance(services.EXPORT_NETWORK_RULES_SCHEMA, backend.Schema)


async def test_config_entry_flow_user_to_local_returned_schema(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify actual flow manager returns local schema and validates data."""
    if importlib.util.find_spec("wakeonlan") is None:
        # Only the legacy test environment lacks this manifest dependency.
        monkeypatch.setitem(sys.modules, "wakeonlan", MagicMock())
    backend = _get_ha_schema_backend()

    # Step 1: Initialize user flow
    init_result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert init_result["type"] is FlowResultType.FORM
    assert init_result["step_id"] == "user"

    # Step 2: Select local connection to transition to local form
    local_result = await hass.config_entries.flow.async_configure(
        init_result["flow_id"],
        user_input={CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL},
    )
    assert local_result["type"] is FlowResultType.FORM
    assert local_result["step_id"] == "local"

    # Step 3: Validate the actual returned schema
    schema = local_result["data_schema"]
    assert isinstance(schema, backend.Schema)

    # Valid inputs with default host and ssl verification
    valid_data = require_not_none(schema)({CONF_API_KEY: "super-secret-key"})
    assert valid_data[CONF_API_KEY] == "super-secret-key"
    assert valid_data[CONF_HOST] == DEFAULT_API_HOST
    assert valid_data[CONF_VERIFY_SSL] is False

    # Valid explicit inputs
    custom_data = require_not_none(schema)(
        {
            CONF_API_KEY: "custom-key",
            CONF_HOST: "https://10.0.0.1",
            CONF_VERIFY_SSL: True,
        }
    )
    assert custom_data[CONF_API_KEY] == "custom-key"
    assert custom_data[CONF_HOST] == "https://10.0.0.1"
    assert custom_data[CONF_VERIFY_SSL] is True

    # Rejection of missing required key
    with pytest.raises(backend.Invalid):
        require_not_none(schema)({})

    # Rejection of invalid boolean type
    with pytest.raises(backend.Invalid):
        require_not_none(schema)({CONF_API_KEY: "key", CONF_VERIFY_SSL: "not-a-bool"})


def test_service_schemas_actual_validation() -> None:
    """Verify registered service schemas accept valid inputs and reject invalid."""
    backend = _get_ha_schema_backend()

    # POWER_CYCLE_PORT_SCHEMA validation
    valid_power = services.POWER_CYCLE_PORT_SCHEMA(
        {"port_idx": 1, "device_id": "switch-1"}
    )
    assert valid_power["port_idx"] == 1
    assert valid_power["device_id"] == "switch-1"

    with pytest.raises(backend.Invalid):
        services.POWER_CYCLE_PORT_SCHEMA({})

    with pytest.raises(backend.Invalid):
        services.POWER_CYCLE_PORT_SCHEMA({"port_idx": 0})

    # SET_HDR_MODE_SCHEMA validation
    valid_hdr = services.SET_HDR_MODE_SCHEMA(
        {"camera_id": "cam-1", "mode": HDR_MODE_AUTO}
    )
    assert valid_hdr["mode"] == HDR_MODE_AUTO

    with pytest.raises(backend.Invalid):
        services.SET_HDR_MODE_SCHEMA({"camera_id": "cam-1", "mode": "invalid_mode"})

    # GENERATE_VOUCHER_SCHEMA validation with defaults and bounds
    valid_voucher = services.GENERATE_VOUCHER_SCHEMA({"site_id": "default"})
    assert valid_voucher["site_id"] == "default"
    assert valid_voucher["count"] == 1
    assert valid_voucher["duration_minutes"] == 480

    with pytest.raises(backend.Invalid):
        services.GENERATE_VOUCHER_SCHEMA({})

    with pytest.raises(backend.Invalid):
        services.GENERATE_VOUCHER_SCHEMA({"site_id": "default", "count": 1000})

    # EXPORT_NETWORK_RULES_SCHEMA validation with boolean default
    valid_export = services.EXPORT_NETWORK_RULES_SCHEMA(
        {"site_id": "default", "console_id": "console-1"}
    )
    assert valid_export["site_id"] == "default"
    assert valid_export["redact"] is True

    with pytest.raises(backend.Invalid):
        services.EXPORT_NETWORK_RULES_SCHEMA({"redact": "not-a-bool"})

    with pytest.raises(backend.Invalid):
        services.EXPORT_NETWORK_RULES_SCHEMA({"unexpected_param": "foo"})
