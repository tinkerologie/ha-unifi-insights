# Copyright (c) 2026 Ruaan Deysel
"""Tests for UniFi Carrier Fabric service actions."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiError,
    UniFiResponseError,
)

from custom_components.unifi_insights import CarrierFabricData
from custom_components.unifi_insights.const import (
    CONF_CARRIER_ACTIONS,
    CONF_CARRIER_ORG_ID,
    CONNECTION_TYPE_CARRIER_FABRIC,
    DOMAIN,
    SERVICE_CARRIER_RESUME_SUBSCRIBER,
    SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
)
from custom_components.unifi_insights.coordinators.carrier_fabric import (
    InvalidSubscriberIdError,
    UnifiInsightsCarrierFabricCoordinator,
)
from custom_components.unifi_insights.services import (
    async_setup_services,
    async_unload_services,
)
from tests.conftest import device_config_entry_ids
from tests.fixtures.assertions import require_not_none

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

VALID_SUB_ID = "11111111-2222-3333-4444-555555555555"
INVALID_SUB_ID = "not-a-valid-uuid"


@pytest.fixture
def mock_carrier_client():
    """Create a mock UniFiCarrierFabricClient."""
    client = MagicMock()
    client.service_plans = MagicMock()
    client.service_plans.get_all = AsyncMock(return_value=[])
    client.subscribers = MagicMock()
    client.subscribers.get_all = AsyncMock(return_value=[])
    client.subscribers.suspend = AsyncMock(return_value={})
    client.subscribers.resume = AsyncMock(return_value={})
    return client


@pytest.fixture
async def setup_carrier_services(hass, mock_carrier_client):
    """Set up services and registries with a Carrier Fabric entry."""
    await async_setup_services(hass)

    carrier_entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="carrier_entry_1",
        unique_id="carrier_org_test123",
        data={
            "connection_type": CONNECTION_TYPE_CARRIER_FABRIC,
            "api_key": "test_isp_key",
            CONF_CARRIER_ORG_ID: "org_test123",
        },
        options={
            CONF_CARRIER_ACTIONS: True,
        },
    )
    carrier_entry.add_to_hass(hass)
    carrier_entry.mock_state(hass, ConfigEntryState.LOADED)

    mock_coord = MagicMock(spec=UnifiInsightsCarrierFabricCoordinator)
    mock_coord.async_suspend_subscriber = AsyncMock()
    mock_coord.async_resume_subscriber = AsyncMock()
    carrier_entry.runtime_data = CarrierFabricData(
        client=mock_carrier_client,
        coordinator=mock_coord,
    )

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    # 1. Subscriber device
    sub_device = dev_reg.async_get_or_create(
        config_entry_id=carrier_entry.entry_id,
        identifiers={(DOMAIN, f"carrier_subscriber_{VALID_SUB_ID}")},
        name="Subscriber Test",
    )

    # 2. Subscriber entity
    sub_entity = ent_reg.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id=f"carrier_subscriber_{VALID_SUB_ID}_state",
        device_id=sub_device.id,
        config_entry=carrier_entry,
    )

    # 3. Organisation device (Carrier Fabric entry, not a subscriber)
    org_device = dev_reg.async_get_or_create(
        config_entry_id=carrier_entry.entry_id,
        identifiers={(DOMAIN, require_not_none(carrier_entry.unique_id))},
        name="Carrier Fabric Org",
    )

    # 4. Console entry & device & entity
    console_entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="console_entry_1",
        unique_id="console_123",
        data={"connection_type": "local", "api_key": "console_key"},
    )
    console_entry.add_to_hass(hass)
    console_entry.mock_state(hass, ConfigEntryState.LOADED)
    console_entry.runtime_data = MagicMock()  # Not CarrierFabricData

    console_device = dev_reg.async_get_or_create(
        config_entry_id=console_entry.entry_id,
        identifiers={(DOMAIN, "gateway_mac_1")},
        name="UDM Pro",
    )
    console_entity = ent_reg.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="console_sensor_1",
        device_id=console_device.id,
        config_entry=console_entry,
    )

    # 5. Invalid UUID subscriber device
    invalid_sub_device = dev_reg.async_get_or_create(
        config_entry_id=carrier_entry.entry_id,
        identifiers={(DOMAIN, f"carrier_subscriber_{INVALID_SUB_ID}")},
        name="Invalid Subscriber",
    )

    yield {
        "carrier_entry": carrier_entry,
        "coordinator": mock_coord,
        "client": mock_carrier_client,
        "sub_device": sub_device,
        "sub_entity": sub_entity,
        "org_device": org_device,
        "console_device": console_device,
        "console_entity": console_entity,
        "invalid_sub_device": invalid_sub_device,
    }

    await async_unload_services(hass)


async def test_carrier_suspend_subscriber_success_without_reason(
    hass, setup_carrier_services
):
    """Test suspend subscriber succeeds without reason."""
    coord = setup_carrier_services["coordinator"]
    sub_device = setup_carrier_services["sub_device"]

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
        {"device_id": sub_device.id},
        blocking=True,
    )
    coord.async_suspend_subscriber.assert_called_once_with(VALID_SUB_ID, reason=None)


async def test_carrier_suspend_subscriber_success_with_reason(
    hass, setup_carrier_services
):
    """Test suspend subscriber succeeds with optional reason."""
    coord = setup_carrier_services["coordinator"]
    sub_device = setup_carrier_services["sub_device"]

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
        {"device_id": sub_device.id, "reason": "Overdue payment"},
        blocking=True,
    )
    coord.async_suspend_subscriber.assert_called_once_with(
        VALID_SUB_ID, reason="Overdue payment"
    )


async def test_carrier_resume_subscriber_success(hass, setup_carrier_services):
    """Test resume subscriber succeeds."""
    coord = setup_carrier_services["coordinator"]
    sub_device = setup_carrier_services["sub_device"]

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CARRIER_RESUME_SUBSCRIBER,
        {"device_id": sub_device.id},
        blocking=True,
    )
    coord.async_resume_subscriber.assert_called_once_with(VALID_SUB_ID)


async def test_entity_target_resolves_to_subscriber(hass, setup_carrier_services):
    """Test an entity target resolves to its owning subscriber."""
    coord = setup_carrier_services["coordinator"]
    sub_entity = setup_carrier_services["sub_entity"]

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
        {"entity_id": sub_entity.entity_id},
        blocking=True,
    )
    coord.async_suspend_subscriber.assert_called_once_with(VALID_SUB_ID, reason=None)

    # Test target dict structure
    coord.async_suspend_subscriber.reset_mock()
    await hass.services.async_call(
        DOMAIN,
        SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
        {"target": {"entity_id": sub_entity.entity_id}},
        blocking=True,
    )
    coord.async_suspend_subscriber.assert_called_once_with(VALID_SUB_ID, reason=None)


async def test_rejection_when_carrier_actions_off(hass, setup_carrier_services):
    """Test rejection when carrier_actions option is disabled."""
    carrier_entry = setup_carrier_services["carrier_entry"]
    sub_device = setup_carrier_services["sub_device"]
    coord = setup_carrier_services["coordinator"]

    # Disable carrier actions
    hass.config_entries.async_update_entry(
        carrier_entry, options={CONF_CARRIER_ACTIONS: False}
    )

    with pytest.raises(
        HomeAssistantError,
        match=r"Carrier Fabric actions are disabled\. Enable 'Enable service actions'",
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"device_id": sub_device.id},
            blocking=True,
        )
    coord.async_suspend_subscriber.assert_not_called()


async def test_rejection_no_target(hass, setup_carrier_services):
    """Test rejection when no target is specified."""
    with pytest.raises(
        ServiceValidationError, match="At least one target must be specified"
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {},
            blocking=True,
        )


async def test_rejection_multiple_targets(hass, setup_carrier_services):
    """Test rejection when multiple targets are specified."""
    sub_device = setup_carrier_services["sub_device"]
    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"device_id": [sub_device.id, "extra_device_id"]},
            blocking=True,
        )
    assert raised.value.translation_key == "carrier_multiple_targets"


async def test_rejection_console_device_target(hass, setup_carrier_services):
    """Test rejection when target is a console device."""
    console_device = setup_carrier_services["console_device"]
    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"device_id": console_device.id},
            blocking=True,
        )
    assert raised.value.translation_key == "carrier_target_not_subscriber"


async def test_rejection_console_entity_target(hass, setup_carrier_services):
    """Test rejection when target is a console entity."""
    console_entity = setup_carrier_services["console_entity"]
    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"entity_id": console_entity.entity_id},
            blocking=True,
        )
    assert raised.value.translation_key == "carrier_target_not_subscriber"


async def test_rejection_organisation_device_target(hass, setup_carrier_services):
    """Test rejection when target is the organisation-level device."""
    org_device = setup_carrier_services["org_device"]
    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"device_id": org_device.id},
            blocking=True,
        )
    assert raised.value.translation_key == "carrier_target_not_subscriber"


async def test_rejection_unloaded_entry(hass, setup_carrier_services):
    """Test rejection when config entry is not loaded."""
    carrier_entry = setup_carrier_services["carrier_entry"]
    sub_device = setup_carrier_services["sub_device"]

    carrier_entry.mock_state(hass, ConfigEntryState.NOT_LOADED)
    carrier_entry.runtime_data = None

    with pytest.raises(
        ServiceValidationError, match="Carrier Fabric integration entry is not loaded"
    ) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"device_id": sub_device.id},
            blocking=True,
        )
    assert exc_info.value.translation_key == "carrier_entry_not_loaded"


async def test_rejection_invalid_subscriber_id(hass, setup_carrier_services):
    """Test rejection when subscriber ID is not a valid UUID."""
    invalid_sub_device = setup_carrier_services["invalid_sub_device"]
    coord = setup_carrier_services["coordinator"]

    with pytest.raises(
        ServiceValidationError, match="Invalid subscriber ID format"
    ) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"device_id": invalid_sub_device.id},
            blocking=True,
        )
    assert exc_info.value.translation_key == "carrier_invalid_subscriber_id"
    coord.async_suspend_subscriber.assert_not_called()


async def test_rejection_reason_too_long(hass, setup_carrier_services):
    """Test rejection when suspend reason exceeds 1024 characters."""
    sub_device = setup_carrier_services["sub_device"]
    coord = setup_carrier_services["coordinator"]
    with pytest.raises(vol.Invalid, match="1024"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"device_id": sub_device.id, "reason": "a" * 1025},
            blocking=True,
        )
    coord.async_suspend_subscriber.assert_not_called()

    # The limit is inclusive: exactly 1024 characters goes through untouched.
    await hass.services.async_call(
        DOMAIN,
        SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
        {"device_id": sub_device.id, "reason": "a" * 1024},
        blocking=True,
    )
    coord.async_suspend_subscriber.assert_called_once_with(
        VALID_SUB_ID, reason="a" * 1024
    )


async def test_non_string_reason_is_coerced_by_the_schema(hass, setup_carrier_services):
    """A numeric reason reaches the coordinator as text."""
    sub_device = setup_carrier_services["sub_device"]
    coord = setup_carrier_services["coordinator"]

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
        {"device_id": sub_device.id, "reason": 42},
        blocking=True,
    )
    coord.async_suspend_subscriber.assert_called_once_with(VALID_SUB_ID, reason="42")


async def test_suspend_403_maps_to_scope_message(hass, setup_carrier_services):
    """Test 403 / insufficient_scope maps to suspend:service scope message."""
    coord = setup_carrier_services["coordinator"]
    sub_device = setup_carrier_services["sub_device"]

    coord.async_suspend_subscriber.side_effect = UniFiAuthenticationError(
        "Access forbidden. Check your API key permissions.",
        status_code=403,
        api_error_code="insufficient_scope",
    )

    with pytest.raises(
        HomeAssistantError, match="missing required scope 'suspend:service'"
    ) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"device_id": sub_device.id},
            blocking=True,
        )
    assert exc_info.value.translation_key == "carrier_missing_scope"
    assert exc_info.value.translation_placeholders == {
        "scope": "suspend:service",
        "action": "suspend",
        "subscriber_id": VALID_SUB_ID,
    }


async def test_resume_403_maps_to_scope_message(hass, setup_carrier_services):
    """Test 403 / insufficient_scope maps to resume:service scope message."""
    coord = setup_carrier_services["coordinator"]
    sub_device = setup_carrier_services["sub_device"]

    coord.async_resume_subscriber.side_effect = UniFiAuthenticationError(
        "Access forbidden. Check your API key permissions.",
        status_code=403,
        api_error_code="insufficient_scope",
    )

    with pytest.raises(
        HomeAssistantError, match="missing required scope 'resume:service'"
    ) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_RESUME_SUBSCRIBER,
            {"device_id": sub_device.id},
            blocking=True,
        )
    assert exc_info.value.translation_key == "carrier_missing_scope"
    assert exc_info.value.translation_placeholders == {
        "scope": "resume:service",
        "action": "resume",
        "subscriber_id": VALID_SUB_ID,
    }


async def test_generic_unifi_error_maps_to_ha_error(hass, setup_carrier_services):
    """A generic UniFiError maps to a translated error with no raw text."""
    coord = setup_carrier_services["coordinator"]
    sub_device = setup_carrier_services["sub_device"]

    sensitive_detail = "API secret failure: user@isp.com db timeout"
    coord.async_suspend_subscriber.side_effect = UniFiError(sensitive_detail)

    with pytest.raises(
        HomeAssistantError, match=r"^Failed to suspend subscriber$"
    ) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"device_id": sub_device.id},
            blocking=True,
        )
    assert exc_info.value.translation_key == "carrier_action_failed"
    assert exc_info.value.translation_placeholders == {"action": "suspend"}
    assert sensitive_detail not in str(exc_info.value)


async def test_coordinator_invalid_subscriber_id_maps_to_validation_error(
    hass, setup_carrier_services
):
    """The coordinator's typed id error becomes a translated validation error."""
    coord = setup_carrier_services["coordinator"]
    sub_device = setup_carrier_services["sub_device"]

    coord.async_suspend_subscriber.side_effect = InvalidSubscriberIdError("bad id")

    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"device_id": sub_device.id},
            blocking=True,
        )
    assert exc_info.value.translation_key == "carrier_invalid_subscriber_id"
    assert exc_info.value.translation_placeholders == {"target": VALID_SUB_ID}


@pytest.mark.parametrize(
    ("status_code", "api_error_code", "expected_key"),
    [
        (403, None, "carrier_missing_scope"),
        (401, "insufficient_scope", "carrier_missing_scope"),
        (401, "unauthorized", "carrier_action_failed"),
        (401, None, "carrier_action_failed"),
    ],
)
async def test_auth_error_scope_detection_uses_typed_fields(
    hass, setup_carrier_services, status_code, api_error_code, expected_key
):
    """Only a 403 or the insufficient_scope code is reported as a missing scope."""
    coord = setup_carrier_services["coordinator"]
    sub_device = setup_carrier_services["sub_device"]

    coord.async_resume_subscriber.side_effect = UniFiAuthenticationError(
        "Rejected",
        status_code=status_code,
        api_error_code=api_error_code,
    )

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_RESUME_SUBSCRIBER,
            {"device_id": sub_device.id},
            blocking=True,
        )
    assert exc_info.value.translation_key == expected_key


@pytest.mark.parametrize(
    ("service", "method"),
    [
        (SERVICE_CARRIER_SUSPEND_SUBSCRIBER, "async_suspend_subscriber"),
        (SERVICE_CARRIER_RESUME_SUBSCRIBER, "async_resume_subscriber"),
    ],
)
async def test_unexpected_exceptions_are_not_mapped(
    hass, setup_carrier_services, service, method
):
    """A programming error must surface as itself, not as carrier_action_failed."""
    coord = setup_carrier_services["coordinator"]
    sub_device = setup_carrier_services["sub_device"]

    getattr(coord, method).side_effect = KeyError("boom")

    with pytest.raises(KeyError):
        await hass.services.async_call(
            DOMAIN,
            service,
            {"device_id": sub_device.id},
            blocking=True,
        )


async def test_area_target_is_refused(hass, setup_carrier_services):
    """Areas are unsupported: a nested area target fails schema validation."""
    coord = setup_carrier_services["coordinator"]
    sub_device = setup_carrier_services["sub_device"]

    with pytest.raises(vol.Invalid, match="area_id is not supported"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"target": {"area_id": "living_room", "device_id": sub_device.id}},
            blocking=True,
        )
    coord.async_suspend_subscriber.assert_not_called()


async def test_repeated_target_is_one_target(hass, setup_carrier_services):
    """The same subscriber named twice (device_id and target) is not "multiple"."""
    coord = setup_carrier_services["coordinator"]
    sub_device = setup_carrier_services["sub_device"]

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
        {"device_id": sub_device.id, "target": {"device_id": sub_device.id}},
        blocking=True,
    )
    coord.async_suspend_subscriber.assert_called_once_with(VALID_SUB_ID, reason=None)


@pytest.mark.parametrize(
    "target", [f"carrier_subscriber_{VALID_SUB_ID}", VALID_SUB_ID], ids=["full", "bare"]
)
async def test_subscriber_targeted_by_identifier(hass, setup_carrier_services, target):
    """A subscriber can be targeted by its device identifier or bare id."""
    coord = setup_carrier_services["coordinator"]

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CARRIER_RESUME_SUBSCRIBER,
        {"device_id": target},
        blocking=True,
    )
    coord.async_resume_subscriber.assert_called_once_with(VALID_SUB_ID)


async def test_rejection_unknown_device_target(hass, setup_carrier_services):
    """A device id that matches no device, identifier or subscriber is refused."""
    coord = setup_carrier_services["coordinator"]

    with pytest.raises(
        ServiceValidationError, match="not found in device registry"
    ) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"device_id": "no-such-device"},
            blocking=True,
        )
    assert exc_info.value.translation_key == "carrier_target_not_found"
    assert exc_info.value.translation_placeholders == {"target": "no-such-device"}
    coord.async_suspend_subscriber.assert_not_called()


async def test_rejection_unknown_entity_target(hass, setup_carrier_services):
    """An entity id that is not in the entity registry is refused."""
    coord = setup_carrier_services["coordinator"]

    with pytest.raises(
        ServiceValidationError, match="not found in entity registry"
    ) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"entity_id": "sensor.not_registered"},
            blocking=True,
        )
    assert exc_info.value.translation_key == "carrier_target_not_found"
    assert exc_info.value.translation_placeholders == {
        "target": "sensor.not_registered"
    }
    coord.async_suspend_subscriber.assert_not_called()


async def test_rejection_entity_of_another_integration(hass, setup_carrier_services):
    """An entity that belongs to another integration is not a subscriber."""
    coord = setup_carrier_services["coordinator"]
    foreign_entity = er.async_get(hass).async_get_or_create(
        domain="light", platform="other_integration", unique_id="foreign-light-1"
    )

    with pytest.raises(
        ServiceValidationError, match="is not a UniFi Insights entity"
    ) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"entity_id": foreign_entity.entity_id},
            blocking=True,
        )
    assert exc_info.value.translation_key == "carrier_target_not_subscriber"
    coord.async_suspend_subscriber.assert_not_called()


async def test_rejection_entity_without_device(hass, setup_carrier_services):
    """An entity of ours that is attached to no device has no subscriber behind it."""
    carrier_entry = setup_carrier_services["carrier_entry"]
    coord = setup_carrier_services["coordinator"]
    deviceless = er.async_get(hass).async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="carrier_subscriber_deviceless_state",
        config_entry=carrier_entry,
    )
    assert deviceless.device_id is None

    with pytest.raises(
        ServiceValidationError, match="not found in device registry"
    ) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"entity_id": deviceless.entity_id},
            blocking=True,
        )
    assert exc_info.value.translation_key == "carrier_target_not_found"
    coord.async_suspend_subscriber.assert_not_called()


async def test_rejection_device_of_another_integration(hass, setup_carrier_services):
    """A device that carries no UniFi Insights identifier is not a subscriber."""
    coord = setup_carrier_services["coordinator"]
    other_entry = MockConfigEntry(domain="other_integration", entry_id="other_entry_1")
    other_entry.add_to_hass(hass)
    foreign_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other_entry.entry_id,
        identifiers={("other_integration", "serial-123")},
    )

    with pytest.raises(
        ServiceValidationError, match="is not a UniFi Insights device"
    ) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"device_id": foreign_device.id},
            blocking=True,
        )
    assert exc_info.value.translation_key == "carrier_target_not_subscriber"
    coord.async_suspend_subscriber.assert_not_called()


async def test_rejection_subscriber_device_owned_by_another_integrations_entry(
    hass, setup_carrier_services
):
    """A device carrying our identifier but owned by a foreign entry is refused."""
    coord = setup_carrier_services["coordinator"]

    # A device belongs to one config entry. This one keeps a UniFi Insights
    # identifier but is owned by another integration's entry, so there is no
    # UniFi Insights entry behind it to act through.
    other_entry = MockConfigEntry(domain="other_integration", entry_id="other_entry_2")
    other_entry.add_to_hass(hass)
    foreign_owned = dr.async_get(hass).async_get_or_create(
        config_entry_id=other_entry.entry_id,
        identifiers={
            (DOMAIN, "carrier_subscriber_99999999-2222-3333-4444-555555555555")
        },
    )
    assert device_config_entry_ids(foreign_owned) == {other_entry.entry_id}

    with pytest.raises(
        ServiceValidationError, match="Target config entry is not loaded"
    ) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
            {"device_id": foreign_owned.id},
            blocking=True,
        )
    assert exc_info.value.translation_key == "carrier_entry_not_loaded"
    coord.async_suspend_subscriber.assert_not_called()


async def test_write_conflict_retry_end_to_end(hass, setup_carrier_services):
    """Test write_conflict retry path exercised end-to-end through coordinator."""
    carrier_entry = setup_carrier_services["carrier_entry"]
    mock_client = setup_carrier_services["client"]
    sub_device = setup_carrier_services["sub_device"]

    # Use a real UnifiInsightsCarrierFabricCoordinator
    real_coord = UnifiInsightsCarrierFabricCoordinator(hass, mock_client, carrier_entry)
    carrier_entry.runtime_data = CarrierFabricData(
        client=mock_client,
        coordinator=real_coord,
    )

    # First call fails with write conflict, second call succeeds
    mock_client.subscribers.suspend.side_effect = [
        UniFiResponseError(
            status_code=409,
            message="Write conflict",
            api_error_code="write_conflict_retryable",
        ),
        {},
    ]

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
        {"device_id": sub_device.id, "reason": "Retry test"},
        blocking=True,
    )

    assert mock_client.subscribers.suspend.call_count == 2
