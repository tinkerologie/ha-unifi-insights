# Copyright 2026 UniFi Insights contributors
"""Verify Access diagnostics never export tokens, PINs, secrets or identifiers."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components.diagnostics import REDACTED, async_redact_data
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api.access.models import (
    AccessDevice,
    AccessDoor,
    AccessUser,
)

from custom_components.unifi_insights.const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCESS_WEBHOOK_ENDPOINT_ID,
    CONF_ACCESS_WEBHOOK_ID,
    CONF_ACCESS_WEBHOOK_SECRET,
    DOMAIN,
)
from custom_components.unifi_insights.coordinators.access import (
    _device_record,
    _door_record,
)
from custom_components.unifi_insights.diagnostics import (
    ACCESS_TO_REDACT,
    TO_REDACT,
    _access_diagnostics,
    async_get_config_entry_diagnostics,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

TOKEN = "access-token-7c1e9a"  # noqa: S105
SECRET = "whsec-diagnostic-5d2b"  # noqa: S105
WEBHOOK_ID = "hook-diagnostic-93af"
ENDPOINT_ID = "endpoint-diagnostic-4e10"
PIN_HASH = "pinhash-diagnostic-88ab"
PIN = "27183141"
USER_ID = "user-diagnostic-0c9d"
EMAIL = "person.private@example.invalid"
MAC_ID = "e4388386be1d"


def _raw_door() -> dict[str, Any]:
    """Return a door payload with the verified field names."""
    return {
        "id": "door-diagnostic-1",
        "name": "Main Door",
        "full_name": "HQ Main Door",
        "floor_id": "floor-diagnostic-1",
        "type": "door",
        "is_bind_hub": True,
        "door_lock_relay_status": "lock",
        "door_position_status": "close",
    }


def _raw_device() -> dict[str, Any]:
    """Return a device payload with the verified field names."""
    return {
        "id": MAC_ID,
        "name": "Reader Lobby",
        "full_name": "Lobby Reader",
        "type": "UA-G2-PRO",
        "location_id": "door-diagnostic-1",
    }


def _raw_user() -> dict[str, Any]:
    """Return a user payload with PIN and identity fields filled."""
    return {
        "id": USER_ID,
        "first_name": "Pat",
        "last_name": "Private",
        "full_name": "Pat Private",
        "alias": "ppriv",
        "user_email": EMAIL,
        "status": "ACTIVE",
        "pin_code": {"token": PIN_HASH},
        "access_policy_ids": ["policy-diagnostic-1"],
    }


def _coordinator() -> MagicMock:
    """Return a coordinator stand-in carrying records built from real models."""
    coordinator = MagicMock()
    door = _door_record(AccessDoor.model_validate(_raw_door()))
    device = _device_record(AccessDevice.model_validate(_raw_device()))
    coordinator.data = {
        "doors": {door["id"]: door} if door else {},
        "devices": {device["id"]: device} if device else {},
        "emergency": {"lockdown": False, "evacuation": True},
    }
    coordinator.auth_failed = False
    coordinator.webhook_active = True
    coordinator.feature_available = MagicMock(return_value=True)
    return coordinator


def test_access_summary_reports_capability_and_auth_state() -> None:
    """Auth state, webhook state and feature capability are exported."""
    summary = _access_diagnostics(_coordinator())
    assert summary["auth_failed"] is False
    assert summary["webhook_active"] is True
    assert summary["features"] == {
        "doors": True,
        "devices": True,
        "emergency": True,
    }
    assert summary["snapshot"]["emergency"] == {"lockdown": False, "evacuation": True}


def test_access_summary_removes_identifiers_and_keeps_names() -> None:
    """IDs go; door and device names stay so the report stays readable."""
    summary = _access_diagnostics(_coordinator())
    text = json.dumps(summary)
    assert "door-diagnostic-1" not in text
    assert MAC_ID not in text
    assert "Main Door" in text
    assert "Reader Lobby" in text


def test_access_summary_handles_missing_data() -> None:
    """Before the first refresh there is no snapshot to export."""
    coordinator = _coordinator()
    coordinator.data = None
    summary = _access_diagnostics(coordinator)
    assert summary["snapshot"] == {"doors": [], "devices": [], "emergency": None}


def test_real_user_serialization_is_fully_redacted() -> None:
    """A user dumped from the real model leaks no PIN, identity or account ID."""
    user = AccessUser.model_validate(_raw_user()).model_dump(by_alias=True)
    redacted = async_redact_data({"users": [user]}, ACCESS_TO_REDACT)
    text = json.dumps(redacted, default=str)
    for value in (
        PIN_HASH,
        USER_ID,
        EMAIL,
        "Pat Private",
        "ppriv",
        "policy-diagnostic-1",
    ):
        assert value not in text
    assert PIN_HASH not in str(redacted)


def test_entry_export_hides_token_and_webhook_state() -> None:
    """Stored token, receiver ID, endpoint ID and signing secret are redacted."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Main",
        data={
            CONF_ACCESS_TOKEN: TOKEN,
            CONF_ACCESS_WEBHOOK_ID: WEBHOOK_ID,
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
            CONF_ACCESS_WEBHOOK_SECRET: SECRET,
        },
        options={"access_webhook": True},
    )
    text = json.dumps(async_redact_data(entry.as_dict(), TO_REDACT))
    for value in (TOKEN, WEBHOOK_ID, ENDPOINT_ID, SECRET):
        assert value not in text
    assert "access_webhook" in text


def _runtime(access_coordinator: MagicMock | None) -> SimpleNamespace:
    """Return an entry runtime whose only optional coordinator is Access."""
    facade = SimpleNamespace(data={}, network_client=None, protect_client=None)
    return SimpleNamespace(
        coordinator=facade,
        protect_coordinator=None,
        innerspace_coordinator=None,
        site_manager_coordinator=None,
        mobility_coordinator=None,
        access_coordinator=access_coordinator,
    )


@pytest.mark.parametrize("configured", [True, False])
async def test_public_export_includes_access_only_when_configured(
    hass: HomeAssistant, *, configured: bool
) -> None:
    """The whole download carries Access state, redacted, only when Access runs."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Main",
        data={
            CONF_ACCESS_TOKEN: TOKEN,
            CONF_ACCESS_WEBHOOK_ID: WEBHOOK_ID,
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
            CONF_ACCESS_WEBHOOK_SECRET: SECRET,
        },
        options={"access_webhook": True},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = _runtime(_coordinator() if configured else None)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert ("access" in diagnostics) is configured
    assert diagnostics["entry"]["title"] == "Main"
    assert diagnostics["entry"]["data"][CONF_ACCESS_TOKEN] == REDACTED
    if not configured:
        return
    access = diagnostics["access"]
    assert access["auth_failed"] is False
    assert access["webhook_active"] is True
    assert access["features"] == {"doors": True, "devices": True, "emergency": True}
    text = json.dumps(diagnostics, default=str)
    for value in (TOKEN, WEBHOOK_ID, ENDPOINT_ID, SECRET, "door-diagnostic-1", MAC_ID):
        assert value not in text
    assert "Main Door" in text
    assert "Reader Lobby" in text
    assert "access_webhook" in text
