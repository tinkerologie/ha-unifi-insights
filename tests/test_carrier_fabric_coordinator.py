# Copyright (c) 2026 Ruaan Deysel
"""Tests for UniFi Carrier Fabric coordinator."""

from typing import NamedTuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from unifi_official_api.carrier_fabric.models import (
    ServicePlan,
    Subscriber,
)

from custom_components.unifi_insights.const import (
    CARRIER_FABRIC_SCAN_INTERVAL,
    CONF_CARRIER_ORG_ID,
    CONNECTION_TYPE_CARRIER_FABRIC,
    DOMAIN,
)
from custom_components.unifi_insights.coordinators.carrier_fabric import (
    UnifiInsightsCarrierFabricCoordinator,
)


@pytest.fixture
def mock_carrier_entry(hass):
    """Create a mock Carrier Fabric config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="carrier_org_123",
        data={
            "connection_type": CONNECTION_TYPE_CARRIER_FABRIC,
            "api_key": "isp_secret_test_key",
            CONF_CARRIER_ORG_ID: "org_123",
        },
    )
    entry.add_to_hass(hass)
    return entry


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


async def test_coordinator_data_ingestion_and_allowlist(
    hass, mock_carrier_entry, mock_carrier_client
):
    """Test data ingestion with allowlisting, deduplication, and summary aggregation."""
    # Real ServicePlan model instances from API containing extra/sensitive fields
    raw_plans = [
        ServicePlan.model_validate(
            {
                "id": "plan_gold",
                "orgId": "org_123",
                "name": "Gigabit Gold",
                "status": "active",
                "downloadMbps": 1000,
                "uploadMbps": 1000,
                "archivedAt": None,
                "createdAt": "2024-01-01T00:00:00Z",
                "updatedAt": "2024-01-02T00:00:00Z",
                # Extra fields that MUST NOT leak into coordinator data:
                "internalBillingCode": "BILL_999",
                "adminNotes": "Confidential margin details",
            }
        ),
        # Duplicate plan
        ServicePlan.model_validate(
            {
                "id": "plan_gold",
                "orgId": "org_123",
                "name": "Gigabit Gold Duplicate",
                "status": "active",
                "downloadMbps": 1000,
                "uploadMbps": 1000,
            }
        ),
        # Archived plan
        ServicePlan.model_validate(
            {
                "id": "plan_archived",
                "orgId": "org_123",
                "name": "Legacy 100M",
                "status": "archived",
                "downloadMbps": 100,
                "uploadMbps": 20,
            }
        ),
        # Empty id - cannot be keyed, must be skipped
        ServicePlan.model_validate({"id": "", "name": "No ID Plan"}),
    ]

    # Real Subscriber model instances containing sensitive fields
    raw_subscribers = [
        Subscriber.model_validate(
            {
                "id": "sub_1",
                "orgId": "org_123",
                "name": "Alice Smith",
                "subscriberNumber": "SUB-001",
                "planId": "plan_gold",
                "state": "installed",
                "suspended": False,
                "suspendedAt": None,
                "activatedAt": "2024-01-05T00:00:00Z",
                "createdAt": "2024-01-05T00:00:00Z",
                "updatedAt": "2024-01-05T00:00:00Z",
                # Sensitive fields that MUST NEVER leak:
                "email": "alice@example.com",
                "phone": "+15551234567",
                "serviceAddress": "123 Main St, Anytown",
                "notes": "Gate code is 1234",
                "hostId": "host_xyz",
                "metadata": {"creditCardLast4": "4242"},
                "suspendReason": None,
            }
        ),
        Subscriber.model_validate(
            {
                "id": "sub_2",
                "orgId": "org_123",
                "name": "Bob Jones",
                "subscriberNumber": "SUB-002",
                "planId": "plan_gold",
                "state": "suspended",
                "suspended": True,
                "suspendedAt": "2024-02-01T00:00:00Z",
                "activatedAt": "2024-01-06T00:00:00Z",
                "createdAt": "2024-01-06T00:00:00Z",
                "updatedAt": "2024-02-01T00:00:00Z",
                "email": "bob@example.com",
                "suspendReason": "Non-payment",
            }
        ),
        Subscriber.model_validate(
            {
                "id": "sub_3",
                "orgId": "org_123",
                "name": "Charlie Brown",
                "subscriberNumber": "SUB-003",
                "planId": None,  # unassigned
                "state": "pending_assignment",
                "suspended": False,
                "suspendedAt": None,
                "activatedAt": None,
                "createdAt": "2024-02-01T00:00:00Z",
                "updatedAt": "2024-02-01T00:00:00Z",
                "email": "charlie@example.com",
            }
        ),
        # Subscriber with unrecognized state:
        Subscriber.model_validate(
            {
                "id": "sub_4",
                "orgId": "org_123",
                "name": "Dana White",
                "state": "unrecognized_future_state",
                "planId": "plan_gold",
            }
        ),
        # Subscriber with state=None:
        Subscriber.model_validate(
            {
                "id": "sub_5",
                "orgId": "org_123",
                "name": "Eve Adams",
                "state": None,
            }
        ),
        # Duplicate subscriber sub_1
        Subscriber.model_validate(
            {
                "id": "sub_1",
                "name": "Alice Duplicate",
            }
        ),
        # Empty id - cannot be keyed, must be skipped
        Subscriber.model_validate({"id": "", "name": "No ID Subscriber"}),
    ]

    mock_carrier_client.service_plans.get_all.return_value = raw_plans
    mock_carrier_client.subscribers.get_all.return_value = raw_subscribers

    coord = UnifiInsightsCarrierFabricCoordinator(
        hass, mock_carrier_client, mock_carrier_entry
    )
    data = await coord._async_update_data()

    assert data["org_id"] == "org_123"

    # Verify plans allowlisting & deduplication
    plans = data["service_plans"]
    assert len(plans) == 2
    assert "plan_gold" in plans
    assert "plan_archived" in plans
    assert "internalBillingCode" not in plans["plan_gold"]
    assert "adminNotes" not in plans["plan_gold"]

    # Verify subscribers allowlisting & deduplication
    subs = data["subscribers"]
    assert len(subs) == 5
    assert "sub_1" in subs
    assert "sub_2" in subs
    assert "sub_3" in subs
    assert "sub_4" in subs
    assert "sub_5" in subs

    sub_1 = subs["sub_1"]
    assert sub_1["id"] == "sub_1"
    assert sub_1["name"] == "Alice Smith"
    # Ensure sensitive fields are purged
    for forbidden in (
        "email",
        "phone",
        "serviceAddress",
        "notes",
        "hostId",
        "metadata",
        "suspendReason",
    ):
        assert forbidden not in sub_1

    # Verify summary aggregation
    summary = data["summary"]
    assert summary["total_subscribers"] == 5
    assert summary["suspended_subscribers"] == 1
    assert summary["subscribers_by_state"] == {
        "installed": 1,
        "suspended": 1,
        "pending_assignment": 1,
        "provisioned": 0,
        "unknown": 2,
    }
    assert summary["active_service_plans"] == 1  # Only plan_gold is active
    assert summary["subscribers_by_plan"] == {
        "plan_gold": 3,
    }
    assert summary["unassigned_subscribers"] == 2


async def test_coordinator_refresh_auth_failed_401(
    hass, mock_carrier_entry, mock_carrier_client
):
    """Test 401 raises ConfigEntryAuthFailed."""
    mock_carrier_client.service_plans.get_all.side_effect = UniFiResponseError(
        status_code=401, message="Unauthorized"
    )
    coord = UnifiInsightsCarrierFabricCoordinator(
        hass, mock_carrier_client, mock_carrier_entry
    )
    with pytest.raises(
        ConfigEntryAuthFailed, match="Carrier Fabric authentication failed"
    ):
        await coord._async_update_data()


async def test_coordinator_refresh_auth_failed_403(
    hass, mock_carrier_entry, mock_carrier_client
):
    """Test 403 raises ConfigEntryAuthFailed naming insufficient_scope."""
    mock_carrier_client.service_plans.get_all.side_effect = UniFiResponseError(
        status_code=403,
        message="Forbidden",
        api_error_code="insufficient_scope",
    )
    coord = UnifiInsightsCarrierFabricCoordinator(
        hass, mock_carrier_client, mock_carrier_entry
    )
    with pytest.raises(ConfigEntryAuthFailed, match="insufficient_scope"):
        await coord._async_update_data()


async def test_coordinator_refresh_rate_limited_429(
    hass, mock_carrier_entry, mock_carrier_client
):
    """Test 429 raises UpdateFailed."""
    mock_carrier_client.service_plans.get_all.side_effect = UniFiResponseError(
        status_code=429, message="Too Many Requests"
    )
    coord = UnifiInsightsCarrierFabricCoordinator(
        hass, mock_carrier_client, mock_carrier_entry
    )
    with pytest.raises(UpdateFailed, match="Carrier Fabric API rate limited"):
        await coord._async_update_data()


async def test_coordinator_refresh_server_error_500(
    hass, mock_carrier_entry, mock_carrier_client
):
    """Test 500 raises UpdateFailed."""
    mock_carrier_client.service_plans.get_all.side_effect = UniFiResponseError(
        status_code=500, message="Internal Server Error"
    )
    coord = UnifiInsightsCarrierFabricCoordinator(
        hass, mock_carrier_client, mock_carrier_entry
    )
    with pytest.raises(UpdateFailed, match="Carrier Fabric API error"):
        await coord._async_update_data()


async def test_coordinator_refresh_connection_error(
    hass, mock_carrier_entry, mock_carrier_client
):
    """Test connection error raises UpdateFailed."""
    mock_carrier_client.service_plans.get_all.side_effect = UniFiConnectionError(
        "Connection refused"
    )
    coord = UnifiInsightsCarrierFabricCoordinator(
        hass, mock_carrier_client, mock_carrier_entry
    )
    with pytest.raises(UpdateFailed, match="Error connecting to Carrier Fabric API"):
        await coord._async_update_data()


async def test_coordinator_refresh_timeout_error(
    hass, mock_carrier_entry, mock_carrier_client
):
    """Test timeout error raises UpdateFailed."""
    mock_carrier_client.service_plans.get_all.side_effect = UniFiTimeoutError(
        "Request timed out"
    )
    coord = UnifiInsightsCarrierFabricCoordinator(
        hass, mock_carrier_client, mock_carrier_entry
    )
    with pytest.raises(UpdateFailed, match="Timeout connecting to Carrier Fabric API"):
        await coord._async_update_data()


async def test_coordinator_suspend_invalid_uuid(
    hass, mock_carrier_entry, mock_carrier_client
):
    """Test suspend validates UUID before any HTTP call."""
    coord = UnifiInsightsCarrierFabricCoordinator(
        hass, mock_carrier_client, mock_carrier_entry
    )
    with pytest.raises(HomeAssistantError, match="Invalid subscriber ID format"):
        await coord.async_suspend_subscriber("invalid-uuid-format")
    mock_carrier_client.subscribers.suspend.assert_not_called()


async def test_coordinator_suspend_success(
    hass, mock_carrier_entry, mock_carrier_client
):
    """Test successful suspend calls client and refreshes coordinator."""
    coord = UnifiInsightsCarrierFabricCoordinator(
        hass, mock_carrier_client, mock_carrier_entry
    )
    coord.async_refresh = AsyncMock()

    valid_id = "11111111-2222-3333-4444-555555555555"
    await coord.async_suspend_subscriber(valid_id)

    mock_carrier_client.subscribers.suspend.assert_called_once_with(
        valid_id, reason=None
    )
    coord.async_refresh.assert_called_once()


async def test_coordinator_suspend_retry_on_write_conflict(
    hass, mock_carrier_entry, mock_carrier_client
):
    """Test suspend retries exactly once on write_conflict_retryable."""
    coord = UnifiInsightsCarrierFabricCoordinator(
        hass, mock_carrier_client, mock_carrier_entry
    )
    coord.async_refresh = AsyncMock()

    mock_carrier_client.subscribers.suspend.side_effect = [
        UniFiResponseError(
            status_code=409,
            message="Conflict",
            api_error_code="write_conflict_retryable",
        ),
        {"status": "ok"},
    ]

    valid_id = "11111111-2222-3333-4444-555555555555"
    await coord.async_suspend_subscriber(valid_id)

    assert mock_carrier_client.subscribers.suspend.call_count == 2
    coord.async_refresh.assert_called_once()


class ErrorCase(NamedTuple):
    """Error case specification for typed error tests."""

    error_to_raise: Exception
    expected_exc_type: type[Exception]
    expected_status: int | None
    expected_api_code: str | None


ERROR_CASES = [
    ErrorCase(
        error_to_raise=UniFiAuthenticationError(
            status_code=403,
            message="Forbidden",
            api_error_code="insufficient_scope",
        ),
        expected_exc_type=UniFiAuthenticationError,
        expected_status=403,
        expected_api_code="insufficient_scope",
    ),
    ErrorCase(
        error_to_raise=UniFiAuthenticationError(
            status_code=401,
            message="Unauthorized",
        ),
        expected_exc_type=UniFiAuthenticationError,
        expected_status=401,
        expected_api_code=None,
    ),
    ErrorCase(
        error_to_raise=UniFiResponseError(
            status_code=500,
            message="Internal Server Error",
        ),
        expected_exc_type=UniFiResponseError,
        expected_status=500,
        expected_api_code=None,
    ),
]


@pytest.mark.parametrize("case", ERROR_CASES)
async def test_coordinator_suspend_typed_errors_propagate(
    hass, mock_carrier_entry, mock_carrier_client, case: ErrorCase
):
    """Test suspend lets typed client errors propagate without wrapping."""
    coord = UnifiInsightsCarrierFabricCoordinator(
        hass, mock_carrier_client, mock_carrier_entry
    )
    mock_carrier_client.subscribers.suspend.side_effect = case.error_to_raise

    valid_id = "11111111-2222-3333-4444-555555555555"
    with pytest.raises(case.expected_exc_type) as exc_info:
        await coord.async_suspend_subscriber(valid_id)

    assert getattr(exc_info.value, "status_code", None) == case.expected_status
    assert getattr(exc_info.value, "api_error_code", None) == case.expected_api_code


async def test_coordinator_suspend_write_conflict_retry_exhausted_propagates(
    hass, mock_carrier_entry, mock_carrier_client
):
    """Test suspend retries 503 write conflict once, then propagates typed error."""
    coord = UnifiInsightsCarrierFabricCoordinator(
        hass, mock_carrier_client, mock_carrier_entry
    )
    conflict = UniFiResponseError(
        status_code=503,
        message="Service Unavailable",
        api_error_code="write_conflict_retryable",
    )
    mock_carrier_client.subscribers.suspend.side_effect = [conflict, conflict]

    valid_id = "11111111-2222-3333-4444-555555555555"
    with pytest.raises(UniFiResponseError) as exc_info:
        await coord.async_suspend_subscriber(valid_id)

    assert mock_carrier_client.subscribers.suspend.call_count == 2
    assert exc_info.value.status_code == 503
    assert exc_info.value.api_error_code == "write_conflict_retryable"


async def test_coordinator_resume_invalid_uuid(
    hass, mock_carrier_entry, mock_carrier_client
):
    """Test resume validates UUID before any HTTP call."""
    coord = UnifiInsightsCarrierFabricCoordinator(
        hass, mock_carrier_client, mock_carrier_entry
    )
    with pytest.raises(HomeAssistantError, match="Invalid subscriber ID format"):
        await coord.async_resume_subscriber("invalid-uuid-format")
    mock_carrier_client.subscribers.resume.assert_not_called()


async def test_coordinator_resume_success(
    hass, mock_carrier_entry, mock_carrier_client
):
    """Test successful resume calls client and refreshes coordinator."""
    coord = UnifiInsightsCarrierFabricCoordinator(
        hass, mock_carrier_client, mock_carrier_entry
    )
    coord.async_refresh = AsyncMock()

    valid_id = "11111111-2222-3333-4444-555555555555"
    await coord.async_resume_subscriber(valid_id)

    mock_carrier_client.subscribers.resume.assert_called_once_with(valid_id)
    coord.async_refresh.assert_called_once()


@pytest.mark.parametrize("case", ERROR_CASES)
async def test_coordinator_resume_typed_errors_propagate(
    hass, mock_carrier_entry, mock_carrier_client, case: ErrorCase
):
    """Test resume lets typed client errors propagate without wrapping."""
    coord = UnifiInsightsCarrierFabricCoordinator(
        hass, mock_carrier_client, mock_carrier_entry
    )
    mock_carrier_client.subscribers.resume.side_effect = case.error_to_raise

    valid_id = "11111111-2222-3333-4444-555555555555"
    with pytest.raises(case.expected_exc_type) as exc_info:
        await coord.async_resume_subscriber(valid_id)

    assert getattr(exc_info.value, "status_code", None) == case.expected_status
    assert getattr(exc_info.value, "api_error_code", None) == case.expected_api_code


async def test_coordinator_resume_write_conflict_retry_exhausted_propagates(
    hass, mock_carrier_entry, mock_carrier_client
):
    """Test resume retries 503 write conflict once, then propagates typed error."""
    coord = UnifiInsightsCarrierFabricCoordinator(
        hass, mock_carrier_client, mock_carrier_entry
    )
    conflict = UniFiResponseError(
        status_code=503,
        message="Service Unavailable",
        api_error_code="write_conflict_retryable",
    )
    mock_carrier_client.subscribers.resume.side_effect = [conflict, conflict]

    valid_id = "11111111-2222-3333-4444-555555555555"
    with pytest.raises(UniFiResponseError) as exc_info:
        await coord.async_resume_subscriber(valid_id)

    assert mock_carrier_client.subscribers.resume.call_count == 2
    assert exc_info.value.status_code == 503
    assert exc_info.value.api_error_code == "write_conflict_retryable"


async def test_coordinator_refresh_unexpected_unifi_error(hass, mock_carrier_client):
    """A library error without a more specific type raises UpdateFailed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CARRIER_ORG_ID: "org_1"},
    )
    coord = UnifiInsightsCarrierFabricCoordinator(hass, mock_carrier_client, entry)
    mock_carrier_client.service_plans.get_all.side_effect = UniFiError("odd failure")

    with pytest.raises(
        UpdateFailed, match="Unexpected error communicating with Carrier Fabric API"
    ):
        await coord._async_update_data()


async def test_coordinator_refresh_programming_error_surfaces(
    hass, mock_carrier_client
):
    """A non-library exception is a bug: it propagates and fails the refresh."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CARRIER_ORG_ID: "org_1"},
    )
    coord = UnifiInsightsCarrierFabricCoordinator(hass, mock_carrier_client, entry)
    mock_carrier_client.service_plans.get_all.side_effect = RuntimeError("Disk full")

    with pytest.raises(RuntimeError, match="Disk full"):
        await coord._async_update_data()

    # Home Assistant's own refresh wrapper still marks the update as failed.
    await coord.async_refresh()
    assert coord.last_update_success is False
    assert isinstance(coord.last_exception, RuntimeError)


async def test_coordinator_matches_data_update_coordinator_contract(
    hass, mock_carrier_entry, mock_carrier_client
):
    """Entities and diagnostics rely on config_entry, interval and last_update_*."""
    coord = UnifiInsightsCarrierFabricCoordinator(
        hass, mock_carrier_client, mock_carrier_entry
    )

    assert coord.config_entry is mock_carrier_entry
    assert coord.update_interval == CARRIER_FABRIC_SCAN_INTERVAL
    assert coord.name == f"{DOMAIN}_carrier_fabric"
    assert coord.data == {}

    await coord.async_refresh()
    assert coord.last_update_success is True
    assert coord.data["org_id"] == "org_123"

    mock_carrier_client.service_plans.get_all.side_effect = UniFiConnectionError("down")
    await coord.async_refresh()
    assert coord.last_update_success is False
    # The last good snapshot is kept while the update is failing.
    assert coord.data["org_id"] == "org_123"


async def test_coordinator_org_id_fallback_discovery(hass, mock_carrier_client):
    """Test org_id fallback discovery when entry data is None."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CARRIER_ORG_ID: None},
    )
    coord = UnifiInsightsCarrierFabricCoordinator(hass, mock_carrier_client, entry)

    # 1. From service plan
    mock_carrier_client.service_plans.get_all.return_value = [
        ServicePlan.model_validate({"id": "plan_1", "orgId": "org_from_plan"})
    ]
    mock_carrier_client.subscribers.get_all.return_value = []
    data = await coord._async_update_data()
    assert data["org_id"] == "org_from_plan"

    # 2. From subscriber when plans have no orgId
    mock_carrier_client.service_plans.get_all.return_value = [
        ServicePlan.model_validate({"id": "plan_2"})
    ]
    mock_carrier_client.subscribers.get_all.return_value = [
        Subscriber.model_validate(
            {"id": "sub_1", "orgId": "org_from_sub", "state": "unknown_status"}
        )
    ]
    data = await coord._async_update_data()
    assert data["org_id"] == "org_from_sub"
    assert data["summary"]["subscribers_by_state"]["unknown"] == 1


async def test_coordinator_org_id_unknown_when_no_resource_carries_one(
    hass, mock_carrier_client
):
    """Without a stored or discoverable organisation the id stays unknown."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CARRIER_ORG_ID: None},
    )
    coord = UnifiInsightsCarrierFabricCoordinator(hass, mock_carrier_client, entry)

    # Neither the plan nor the subscribers name an organisation.
    mock_carrier_client.service_plans.get_all.return_value = [
        ServicePlan.model_validate({"id": "plan_1"})
    ]
    mock_carrier_client.subscribers.get_all.return_value = [
        Subscriber.model_validate({"id": "sub_1"}),
        Subscriber.model_validate({"id": "sub_2"}),
    ]
    data = await coord._async_update_data()
    assert data["org_id"] is None
    assert set(data["subscribers"]) == {"sub_1", "sub_2"}

    # An empty account has nothing to discover from either.
    mock_carrier_client.service_plans.get_all.return_value = []
    mock_carrier_client.subscribers.get_all.return_value = []
    data = await coord._async_update_data()
    assert data["org_id"] is None

    # A later subscriber that does carry the id is still found past those that
    # do not.
    mock_carrier_client.subscribers.get_all.return_value = [
        Subscriber.model_validate({"id": "sub_1"}),
        Subscriber.model_validate({"id": "sub_2", "orgId": "org_late"}),
    ]
    data = await coord._async_update_data()
    assert data["org_id"] == "org_late"


async def test_coordinator_validate_subscriber_uuid_non_string(
    hass, mock_carrier_client
):
    """Test non-string subscriber ID raises HomeAssistantError."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CARRIER_ORG_ID: "org_1"},
    )
    coord = UnifiInsightsCarrierFabricCoordinator(hass, mock_carrier_client, entry)

    with pytest.raises(
        HomeAssistantError, match=r"Invalid subscriber ID \(UUID string required\)"
    ):
        await coord.async_suspend_subscriber(12345)  # type: ignore[arg-type]


async def test_coordinator_resume_retry_on_write_conflict(hass, mock_carrier_client):
    """Test resume retries once on write_conflict_retryable and succeeds."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CARRIER_ORG_ID: "org_1"},
    )
    coord = UnifiInsightsCarrierFabricCoordinator(hass, mock_carrier_client, entry)

    valid_id = "11111111-2222-3333-4444-555555555555"
    conflict_err = UniFiResponseError(
        "Write conflict",
        status_code=503,
        api_error_code="write_conflict_retryable",
    )

    mock_carrier_client.subscribers.resume.side_effect = [
        conflict_err,
        {"id": valid_id, "suspended": False},
    ]

    with patch.object(coord, "async_refresh", new_callable=AsyncMock) as mock_refresh:
        await coord.async_resume_subscriber(valid_id)
        assert mock_carrier_client.subscribers.resume.call_count == 2
        mock_refresh.assert_awaited_once()
