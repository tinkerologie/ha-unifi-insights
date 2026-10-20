# Copyright 2026 UniFi Insights contributors
"""Verify the admin-only UniFi Access user services."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from homeassistant.core import Context
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api import UniFiResponseError

from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.services import (
    SERVICE_REMOVE_ACCESS_USER_PIN,
    SERVICE_SET_ACCESS_USER_PIN,
    SERVICE_SET_ACCESS_USER_STATUS,
    async_setup_services,
    async_unload_services,
)

PIN = "90817263"
USER_ID = "user-7f3a-42"


def _coordinator() -> MagicMock:
    """Return an Access coordinator stand-in with the user command methods."""
    coordinator = MagicMock()
    coordinator.async_set_user_pin = AsyncMock(return_value=None)
    coordinator.async_remove_user_pin = AsyncMock(return_value=None)
    coordinator.async_set_user_status = AsyncMock(return_value=None)
    return coordinator


def _add_entry(
    hass: Any,
    *,
    title: str,
    coordinator: MagicMock | None,
) -> MockConfigEntry:
    """Add a loaded entry; ``coordinator=None`` models a console without Access."""
    entry = MockConfigEntry(domain=DOMAIN, title=title, data={})
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(access_coordinator=coordinator)
    return entry


def _integration_messages(caplog: pytest.LogCaptureFixture) -> str:
    """Return the text of every record from this integration's loggers."""
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("custom_components.unifi_insights")
    )


@pytest.fixture
async def services(hass: Any) -> None:
    """Register the integration services once per test."""
    await async_setup_services(hass)


async def _call(
    hass: Any,
    service: str,
    data: dict[str, Any],
    context: Context | None = None,
) -> None:
    """Call a service in blocking mode."""
    await hass.services.async_call(
        DOMAIN, service, data, blocking=True, context=context
    )


async def test_admin_can_set_pin_on_the_only_access_console(
    hass: Any, services: None, hass_admin_user: Any
) -> None:
    """A single loaded Access console receives the PIN call."""
    coordinator = _coordinator()
    _add_entry(hass, title="Main", coordinator=coordinator)
    await _call(
        hass,
        SERVICE_SET_ACCESS_USER_PIN,
        {"user_id": USER_ID, "pin": PIN},
        Context(user_id=hass_admin_user.id),
    )
    coordinator.async_set_user_pin.assert_awaited_once_with(USER_ID, PIN)


@pytest.mark.parametrize("pin", [83, 1234, True, 12.5, None])
async def test_numeric_pin_is_rejected_before_any_console_call(
    hass: Any, services: None, pin: Any
) -> None:
    """A PIN must be a string so YAML/JSON coercion cannot change credentials."""
    coordinator = _coordinator()
    _add_entry(hass, title="Main", coordinator=coordinator)
    with pytest.raises(vol.Invalid):
        await _call(hass, SERVICE_SET_ACCESS_USER_PIN, {"user_id": USER_ID, "pin": pin})
    coordinator.async_set_user_pin.assert_not_awaited()


async def test_pin_string_keeps_leading_zeros(hass: Any, services: None) -> None:
    """Quoted PIN strings reach the console unchanged."""
    coordinator = _coordinator()
    _add_entry(hass, title="Main", coordinator=coordinator)
    await _call(hass, SERVICE_SET_ACCESS_USER_PIN, {"user_id": USER_ID, "pin": "0123"})
    coordinator.async_set_user_pin.assert_awaited_once_with(USER_ID, "0123")


async def test_admin_can_remove_pin_and_set_status(
    hass: Any, services: None, hass_admin_user: Any
) -> None:
    """Remove and status calls reach the matching coordinator methods."""
    coordinator = _coordinator()
    _add_entry(hass, title="Main", coordinator=coordinator)
    context = Context(user_id=hass_admin_user.id)
    await _call(hass, SERVICE_REMOVE_ACCESS_USER_PIN, {"user_id": USER_ID}, context)
    await _call(
        hass,
        SERVICE_SET_ACCESS_USER_STATUS,
        {"user_id": USER_ID, "status": "DEACTIVATED"},
        context,
    )
    coordinator.async_remove_user_pin.assert_awaited_once_with(USER_ID)
    coordinator.async_set_user_status.assert_awaited_once_with(USER_ID, "DEACTIVATED")


async def test_non_admin_is_refused_for_every_user_service(
    hass: Any, services: None, hass_read_only_user: Any
) -> None:
    """Non-admin callers never reach the coordinator."""
    coordinator = _coordinator()
    _add_entry(hass, title="Main", coordinator=coordinator)
    context = Context(user_id=hass_read_only_user.id)
    calls = (
        (SERVICE_SET_ACCESS_USER_PIN, {"user_id": USER_ID, "pin": PIN}),
        (SERVICE_REMOVE_ACCESS_USER_PIN, {"user_id": USER_ID}),
        (SERVICE_SET_ACCESS_USER_STATUS, {"user_id": USER_ID, "status": "ACTIVE"}),
    )
    for service, data in calls:
        with pytest.raises(Unauthorized):
            await _call(hass, service, data, context)
    coordinator.async_set_user_pin.assert_not_called()
    coordinator.async_remove_user_pin.assert_not_called()
    coordinator.async_set_user_status.assert_not_called()


async def test_unknown_user_context_is_refused(hass: Any, services: None) -> None:
    """A context naming a user that does not exist is refused, not treated as system."""
    coordinator = _coordinator()
    _add_entry(hass, title="Main", coordinator=coordinator)
    with pytest.raises(Unauthorized):
        await _call(
            hass,
            SERVICE_REMOVE_ACCESS_USER_PIN,
            {"user_id": USER_ID},
            Context(user_id="no-such-user"),
        )
    coordinator.async_remove_user_pin.assert_not_called()


async def test_system_context_without_user_is_allowed(
    hass: Any, services: None
) -> None:
    """Automations and the system call the service without a user context."""
    coordinator = _coordinator()
    _add_entry(hass, title="Main", coordinator=coordinator)
    await _call(hass, SERVICE_REMOVE_ACCESS_USER_PIN, {"user_id": USER_ID})
    coordinator.async_remove_user_pin.assert_awaited_once_with(USER_ID)


@pytest.mark.parametrize(
    ("service", "data"),
    [
        (SERVICE_SET_ACCESS_USER_PIN, {"user_id": "../etc", "pin": PIN}),
        (SERVICE_SET_ACCESS_USER_PIN, {"user_id": "", "pin": PIN}),
        (SERVICE_REMOVE_ACCESS_USER_PIN, {"user_id": "x" * 65}),
        (SERVICE_SET_ACCESS_USER_STATUS, {"user_id": USER_ID, "status": "PENDING"}),
        (SERVICE_SET_ACCESS_USER_STATUS, {"user_id": USER_ID, "status": "active"}),
        (SERVICE_SET_ACCESS_USER_PIN, {"user_id": USER_ID}),
    ],
)
async def test_schema_rejects_bad_input_before_routing(
    hass: Any, services: None, service: str, data: dict[str, Any]
) -> None:
    """Path-unsafe IDs, read-only statuses and missing fields fail schema validation."""
    coordinator = _coordinator()
    _add_entry(hass, title="Main", coordinator=coordinator)
    with pytest.raises(vol.Invalid):
        await _call(hass, service, data)
    coordinator.async_set_user_pin.assert_not_called()
    coordinator.async_set_user_status.assert_not_called()


async def test_pin_validation_errors_pass_through_unchanged(
    hass: Any, services: None
) -> None:
    """Coordinator validation errors pass through with their own key."""
    coordinator = _coordinator()
    coordinator.async_set_user_pin.side_effect = ServiceValidationError(
        translation_domain=DOMAIN, translation_key="pin_rejected"
    )
    _add_entry(hass, title="Main", coordinator=coordinator)
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass, SERVICE_SET_ACCESS_USER_PIN, {"user_id": USER_ID, "pin": "12"}
        )
    assert err.value.translation_key == "pin_rejected"


async def test_unknown_user_404_becomes_validation_error(
    hass: Any, services: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A 404 is translated without exposing the PIN in integration logs."""

    async def _not_found(user_id: str, pin: str) -> None:
        try:
            msg = "absent"
            raise UniFiResponseError(msg, status_code=404)
        except UniFiResponseError as cause:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="pin_assign_failed"
            ) from cause

    coordinator = _coordinator()
    coordinator.async_set_user_pin.side_effect = _not_found
    _add_entry(hass, title="Main", coordinator=coordinator)
    with caplog.at_level(logging.DEBUG), pytest.raises(ServiceValidationError) as err:
        await _call(hass, SERVICE_SET_ACCESS_USER_PIN, {"user_id": USER_ID, "pin": PIN})
    assert err.value.translation_key == "access_user_not_found"
    assert PIN not in str(err.value)
    # The integration's own records must never carry the PIN. Home Assistant
    # core's handling of service data is outside this integration's logs.
    assert PIN not in _integration_messages(caplog)


async def test_other_console_failures_are_not_remapped(
    hass: Any, services: None
) -> None:
    """Only a 404 is treated as an unknown user; other failures keep their key."""

    async def _forbidden(user_id: str) -> None:
        try:
            msg = "denied"
            raise UniFiResponseError(msg, status_code=403)
        except UniFiResponseError as cause:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="permission_denied"
            ) from cause

    coordinator = _coordinator()
    coordinator.async_remove_user_pin.side_effect = _forbidden
    _add_entry(hass, title="Main", coordinator=coordinator)
    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, SERVICE_REMOVE_ACCESS_USER_PIN, {"user_id": USER_ID})
    assert not isinstance(err.value, ServiceValidationError)
    assert err.value.translation_key == "permission_denied"


async def test_console_id_routes_by_title_and_entry_id(
    hass: Any, services: None, hass_admin_user: Any
) -> None:
    """With two consoles, the named one receives the call, by title or entry ID."""
    home = _coordinator()
    lab = _coordinator()
    home_entry = _add_entry(hass, title="HQ", coordinator=home)
    _add_entry(hass, title="Lab", coordinator=lab)
    context = Context(user_id=hass_admin_user.id)
    await _call(
        hass,
        SERVICE_REMOVE_ACCESS_USER_PIN,
        {"user_id": USER_ID, "console_id": "lab"},
        context,
    )
    await _call(
        hass,
        SERVICE_REMOVE_ACCESS_USER_PIN,
        {"user_id": USER_ID, "console_id": home_entry.entry_id},
        context,
    )
    lab.async_remove_user_pin.assert_awaited_once_with(USER_ID)
    home.async_remove_user_pin.assert_awaited_once_with(USER_ID)


async def test_several_consoles_without_console_id_are_ambiguous(
    hass: Any, services: None
) -> None:
    """Routing never guesses between consoles."""
    _add_entry(hass, title="HQ", coordinator=_coordinator())
    _add_entry(hass, title="Lab", coordinator=_coordinator())
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, SERVICE_REMOVE_ACCESS_USER_PIN, {"user_id": USER_ID})
    assert err.value.translation_key == "access_console_required"


async def test_unknown_console_id_is_refused(hass: Any, services: None) -> None:
    """A console name that matches nothing is a validation error."""
    _add_entry(hass, title="HQ", coordinator=_coordinator())
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            SERVICE_REMOVE_ACCESS_USER_PIN,
            {"user_id": USER_ID, "console_id": "Nowhere"},
        )


async def test_consoles_without_access_are_not_routed(
    hass: Any, services: None
) -> None:
    """Entries with no Access coordinator never receive an Access call."""
    access = _coordinator()
    _add_entry(hass, title="Network Only", coordinator=None)
    _add_entry(hass, title="Access", coordinator=access)
    await _call(hass, SERVICE_REMOVE_ACCESS_USER_PIN, {"user_id": USER_ID})
    access.async_remove_user_pin.assert_awaited_once_with(USER_ID)


async def test_no_access_console_reports_not_configured(
    hass: Any, services: None
) -> None:
    """With no Access console loaded the service says so."""
    _add_entry(hass, title="Network Only", coordinator=None)
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, SERVICE_REMOVE_ACCESS_USER_PIN, {"user_id": USER_ID})
    assert err.value.translation_key == "access_not_configured"


async def test_unload_removes_access_services(hass: Any, services: None) -> None:
    """Unloading removes the three Access services and nothing else is required."""
    for service in (
        SERVICE_SET_ACCESS_USER_PIN,
        SERVICE_REMOVE_ACCESS_USER_PIN,
        SERVICE_SET_ACCESS_USER_STATUS,
    ):
        assert hass.services.has_service(DOMAIN, service)
    await async_unload_services(hass)
    for service in (
        SERVICE_SET_ACCESS_USER_PIN,
        SERVICE_REMOVE_ACCESS_USER_PIN,
        SERVICE_SET_ACCESS_USER_STATUS,
    ):
        assert not hass.services.has_service(DOMAIN, service)
