# Copyright (c) 2026 Ruaan Deysel
"""Tests for UniFi Carrier Fabric sensors."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import CONF_API_KEY, EntityCategory
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_insights import CarrierFabricData
from custom_components.unifi_insights.const import (
    CONF_CARRIER_ORG_ID,
    CONF_CONNECTION_TYPE,
    CONF_TRACK_SUBSCRIBERS,
    CONNECTION_TYPE_CARRIER_FABRIC,
    DOMAIN,
)
from custom_components.unifi_insights.sensor import (
    AGGREGATE_SENSOR_DESCRIPTIONS,
    UnifiCarrierFabricAggregateSensor,
    UnifiCarrierFabricPlanSubscribersSensor,
    UnifiCarrierFabricSubscriberPlanSensor,
    UnifiCarrierFabricSubscriberStateSensor,
    async_setup_entry,
    reconcile_subscriber_registry,
)
from tests.conftest import device_config_entry_ids, get_device_by_identifier
from tests.fixtures.entities import EntityCallback, EntityCollector

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
def mock_carrier_coordinator():
    """Create a mock Carrier Fabric coordinator."""
    coord = MagicMock()
    coord.data = {
        "org_id": "org_test123",
        "service_plans": {
            "plan_1": {
                "id": "plan_1",
                "orgId": "org_test123",
                "name": "Fiber 100M",
                "status": "active",
                "downloadMbps": 100.0,
                "uploadMbps": 50.0,
                "archivedAt": None,
            },
            "plan_2": {
                "id": "plan_2",
                "orgId": "org_test123",
                "name": "Legacy DSL",
                "status": "archived",
                "downloadMbps": 10.0,
                "uploadMbps": 1.0,
                "archivedAt": "2026-01-01T00:00:00Z",
            },
        },
        "subscribers": {
            "sub_1": {
                "id": "sub_1",
                "orgId": "org_test123",
                "name": "Alice Resident",
                "subscriberNumber": "SUB-001",
                "planId": "plan_1",
                "state": "installed",
                "suspended": False,
                "suspendedAt": None,
                "activatedAt": "2026-01-15T10:00:00Z",
                # Private fields that must never appear in attributes
                "email": "alice@example.com",
                "serviceAddress": "123 Main St",
                "notes": "VIP customer",
                "metadata": {"router": "UDM-Pro"},
                "suspendReason": "non-payment",
                "hostId": "host-xyz",
            },
            "sub_2": {
                "id": "sub_2",
                "orgId": "org_test123",
                "name": None,
                "subscriberNumber": "SUB-002",
                "planId": None,
                "state": "provisioned",
                "suspended": True,
                "suspendedAt": "2026-02-01T12:00:00Z",
                "activatedAt": None,
            },
            "sub_3_12345678": {
                "id": "sub_3_12345678",
                "orgId": "org_test123",
                "name": None,
                "subscriberNumber": None,
                "planId": "unknown_plan_id",
                "state": "unknown_custom_state",
                "suspended": False,
            },
        },
        "summary": {
            "total_subscribers": 3,
            "suspended_subscribers": 1,
            "subscribers_by_state": {
                "pending_assignment": 0,
                "provisioned": 1,
                "installed": 1,
                "unknown": 1,
            },
            "active_service_plans": 1,
            "subscribers_by_plan": {
                "plan_1": 1,
            },
            "unassigned_subscribers": 1,
        },
    }
    listeners = []
    coord.async_add_listener = MagicMock(side_effect=listeners.append)
    coord._listeners = listeners
    return coord


@pytest.fixture
def carrier_entry(hass, mock_carrier_coordinator):
    """Create a mock Carrier Fabric entry attached to coordinator."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Carrier Fabric",
        unique_id="carrier_org_test123",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            CONF_API_KEY: "isp_secret_key",
            CONF_CARRIER_ORG_ID: "org_test123",
        },
        options={
            CONF_TRACK_SUBSCRIBERS: False,
        },
    )
    entry.add_to_hass(hass)
    mock_client = MagicMock()
    mock_client.close = AsyncMock()
    entry.runtime_data = CarrierFabricData(
        client=mock_client,
        coordinator=mock_carrier_coordinator,
    )
    return entry


async def test_carrier_fabric_aggregate_sensors(
    hass, carrier_entry, mock_carrier_coordinator
):
    """Test creation, values, and attributes of organisation aggregate sensors."""
    added_entities = []

    mock_add_entities = EntityCollector(added_entities)

    await async_setup_entry(hass, carrier_entry, EntityCallback(mock_add_entities))

    # 7 aggregate sensors + 2 plan sensors = 9 entities (track_subscribers is False)
    assert len(added_entities) == 9

    agg_sensors = [
        e for e in added_entities if isinstance(e, UnifiCarrierFabricAggregateSensor)
    ]
    assert len(agg_sensors) == 7

    sensors_by_key = {s.entity_description.key: s for s in agg_sensors}

    # Verify primary sensors
    total = sensors_by_key["total_subscribers"]
    assert total.unique_id == "carrier_org_test123_total_subscribers"
    assert total.native_value == 3
    assert total.entity_description.entity_category is None
    assert total.state_class == SensorStateClass.MEASUREMENT
    assert total.device_info["identifiers"] == {(DOMAIN, "carrier_org_test123")}
    assert total.device_info["name"] == "UniFi Carrier Fabric"

    suspended = sensors_by_key["suspended_subscribers"]
    assert suspended.unique_id == "carrier_org_test123_suspended_subscribers"
    assert suspended.native_value == 1
    assert suspended.entity_description.entity_category is None
    assert suspended.state_class == SensorStateClass.MEASUREMENT

    # Verify diagnostic sensors
    prov = sensors_by_key["subscribers_provisioned"]
    assert prov.native_value == 1
    assert prov.entity_description.entity_category == EntityCategory.DIAGNOSTIC

    inst = sensors_by_key["subscribers_installed"]
    assert inst.native_value == 1
    assert inst.entity_description.entity_category == EntityCategory.DIAGNOSTIC

    unassigned = sensors_by_key["unassigned_subscribers"]
    assert unassigned.native_value == 1
    assert unassigned.entity_description.entity_category == EntityCategory.DIAGNOSTIC

    plans = sensors_by_key["active_service_plans"]
    assert plans.native_value == 1
    assert plans.entity_description.entity_category == EntityCategory.DIAGNOSTIC


async def test_carrier_fabric_plan_sensors(
    hass, carrier_entry, mock_carrier_coordinator
):
    """Test per-plan subscriber count sensors."""
    added_entities = []
    await async_setup_entry(hass, carrier_entry, EntityCollector(added_entities))

    plan_sensors = [
        e
        for e in added_entities
        if isinstance(e, UnifiCarrierFabricPlanSubscribersSensor)
    ]
    assert len(plan_sensors) == 2

    sensors_by_plan = {s._plan_id: s for s in plan_sensors}

    # Active plan
    s1 = sensors_by_plan["plan_1"]
    assert s1.unique_id == "carrier_org_test123_plan_plan_1_subscribers"
    assert s1.native_value == 1
    assert s1.available is True
    assert s1.entity_registry_enabled_default is True
    assert s1.translation_placeholders == {"plan_name": "Fiber 100M"}
    assert s1.extra_state_attributes == {
        "status": "active",
        "download_mbps": 100.0,
        "upload_mbps": 50.0,
    }

    # Archived plan
    s2 = sensors_by_plan["plan_2"]
    assert s2.unique_id == "carrier_org_test123_plan_plan_2_subscribers"
    assert s2.native_value == 0
    assert s2.available is True
    assert s2.entity_registry_enabled_default is False
    assert s2.extra_state_attributes["status"] == "archived"
    assert s2.extra_state_attributes["archived_at"] == "2026-01-01T00:00:00Z"

    # Test plan disappearance
    del mock_carrier_coordinator.data["service_plans"]["plan_1"]
    assert s1.available is False


async def test_carrier_fabric_subscriber_sensors_opt_in(
    hass, carrier_entry, mock_carrier_coordinator
):
    """Test opt-in subscriber entities with privacy protections and device hierarchy."""
    hass.config_entries.async_update_entry(
        carrier_entry,
        options={CONF_TRACK_SUBSCRIBERS: True},
    )

    added_entities = []
    await async_setup_entry(hass, carrier_entry, EntityCollector(added_entities))

    state_sensors = [
        e
        for e in added_entities
        if isinstance(e, UnifiCarrierFabricSubscriberStateSensor)
    ]
    plan_sensors = [
        e
        for e in added_entities
        if isinstance(e, UnifiCarrierFabricSubscriberPlanSensor)
    ]

    assert len(state_sensors) == 3
    assert len(plan_sensors) == 3

    states_by_sub = {s._subscriber_id: s for s in state_sensors}
    plans_by_sub = {s._subscriber_id: s for s in plan_sensors}

    # Subscriber 1: named, active, on plan 1
    s1_state = states_by_sub["sub_1"]
    assert s1_state.unique_id == "carrier_subscriber_sub_1_state"
    assert s1_state.device_class == SensorDeviceClass.ENUM
    assert s1_state.native_value == "installed"
    assert s1_state.available is True
    assert s1_state.extra_state_attributes == {"activated_at": "2026-01-15T10:00:00Z"}
    # Verify strict privacy: ensure private fields are NOT present
    for private_field in (
        "email",
        "serviceAddress",
        "notes",
        "metadata",
        "suspendReason",
        "hostId",
        "subscriberNumber",
    ):
        assert private_field not in s1_state.extra_state_attributes

    # Device info checks
    dev_info1 = s1_state.device_info
    assert dev_info1["identifiers"] == {(DOMAIN, "carrier_subscriber_sub_1")}
    org_device = get_device_by_identifier(
        dr.async_get(hass), (DOMAIN, "carrier_org_test123"), carrier_entry.entry_id
    )
    assert org_device is not None
    assert dev_info1["via_device_id"] == org_device.id
    assert "via_device" not in dev_info1
    assert dev_info1["name"] == "Alice Resident"

    s1_plan = plans_by_sub["sub_1"]
    assert s1_plan.unique_id == "carrier_subscriber_sub_1_service_plan"
    assert s1_plan.native_value == "Fiber 100M"
    assert s1_plan.extra_state_attributes == {
        "download_mbps": 100.0,
        "upload_mbps": 50.0,
    }
    assert s1_plan.entity_category == EntityCategory.DIAGNOSTIC

    # Subscriber 2: suspended, unassigned, name falls back to subscriberNumber
    s2_state = states_by_sub["sub_2"]
    assert s2_state.native_value == "suspended"
    assert s2_state.extra_state_attributes == {"suspended_at": "2026-02-01T12:00:00Z"}
    assert s2_state.device_info["name"] == "SUB-002"

    s2_plan = plans_by_sub["sub_2"]
    assert s2_plan.native_value is None
    assert s2_plan.extra_state_attributes == {}

    # Subscriber 3: name falls back to Subscriber <id[:8]>
    # unknown state, unknown plan ID
    s3_state = states_by_sub["sub_3_12345678"]
    assert s3_state.device_info["name"] == "Subscriber sub_3_12"
    assert s3_state.native_value == "unknown"

    # A plan this key cannot see is unknown, not a raw plan UUID
    s3_plan = plans_by_sub["sub_3_12345678"]
    assert s3_plan.native_value is None
    assert s3_plan.extra_state_attributes == {}

    # Subscriber disappearance marks entity unavailable without deleting
    del mock_carrier_coordinator.data["subscribers"]["sub_1"]
    assert s1_state.available is False
    assert s1_plan.available is False


async def test_carrier_fabric_track_subscribers_disabled_reconciliation(
    hass, carrier_entry, mock_carrier_coordinator
):
    """Test subscriber cleanup when track_subscribers is disabled."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    # Pre-register organisation device and aggregate entity
    org_device = dev_reg.async_get_or_create(
        config_entry_id=carrier_entry.entry_id,
        identifiers={(DOMAIN, carrier_entry.unique_id)},
    )
    ent_reg.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id=f"{carrier_entry.unique_id}_total_subscribers",
        config_entry=carrier_entry,
        device_id=org_device.id,
    )

    # Pre-register subscriber device and entity
    sub_device = dev_reg.async_get_or_create(
        config_entry_id=carrier_entry.entry_id,
        identifiers={(DOMAIN, "carrier_subscriber_sub_old")},
    )
    ent_reg.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="carrier_subscriber_sub_old_state",
        config_entry=carrier_entry,
        device_id=sub_device.id,
    )

    # Run setup with track_subscribers = False
    hass.config_entries.async_update_entry(
        carrier_entry,
        options={CONF_TRACK_SUBSCRIBERS: False},
    )

    added_entities = []
    await async_setup_entry(hass, carrier_entry, EntityCollector(added_entities))

    # Verify subscriber entity was removed
    assert (
        ent_reg.async_get_entity_id(
            "sensor", DOMAIN, "carrier_subscriber_sub_old_state"
        )
        is None
    )

    # Verify subscriber device was removed
    sub_devices = [
        d
        for d in dr.async_entries_for_config_entry(dev_reg, carrier_entry.entry_id)
        if (DOMAIN, "carrier_subscriber_sub_old") in d.identifiers
    ]
    assert len(sub_devices) == 0

    # Verify organisation device and entity were NOT removed
    org_devices = [
        d
        for d in dr.async_entries_for_config_entry(dev_reg, carrier_entry.entry_id)
        if (DOMAIN, carrier_entry.unique_id) in d.identifiers
    ]
    assert len(org_devices) == 1
    assert (
        ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"{carrier_entry.unique_id}_total_subscribers"
        )
        is not None
    )


class ModernDeviceEntryDouble:
    """DeviceEntry double matching Home Assistant 2026.10.1 tagged shape."""

    def __init__(
        self,
        *,
        device_id: str,
        identifiers: set[tuple[str, str]],
        config_entry_id: str,
    ) -> None:
        self.id = device_id
        self.identifiers = identifiers
        self.config_entry_id = config_entry_id


async def test_reconcile_does_not_touch_another_entrys_subscriber_device(
    hass, carrier_entry, mock_carrier_coordinator
):
    """Two entries keep separate devices or shared ownership portably.

    On modern single-owner registries, each entry gets a distinct device. On older
    registries, the device is shared across entries. Cleaning up one entry must
    detach that entry without removing the other entry's device or entities.
    """
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    other_entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="other_carrier_entry",
        unique_id="carrier_key_other",
        data={CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC},
    )
    other_entry.add_to_hass(hass)

    identifier = (DOMAIN, "carrier_subscriber_shared")
    ours = dev_reg.async_get_or_create(
        config_entry_id=carrier_entry.entry_id, identifiers={identifier}
    )
    theirs = dev_reg.async_get_or_create(
        config_entry_id=other_entry.entry_id, identifiers={identifier}
    )
    assert carrier_entry.entry_id in device_config_entry_ids(ours)
    assert other_entry.entry_id in device_config_entry_ids(theirs)
    if hasattr(theirs, "config_entry_id"):
        assert ours.id != theirs.id

    foreign_entity = ent_reg.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="carrier_subscriber_foreign_state",
        config_entry=other_entry,
        device_id=theirs.id,
    )
    foreign_device_id = theirs.id
    foreign_identifiers = set(theirs.identifiers)
    foreign_entity_id = foreign_entity.entity_id
    foreign_unique_id = foreign_entity.unique_id

    own_sub_entity = ent_reg.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="carrier_subscriber_own_state",
        config_entry=carrier_entry,
        device_id=ours.id,
    )
    own_other_entity = ent_reg.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="carrier_other_own_attached_to_device",
        config_entry=carrier_entry,
        device_id=ours.id,
    )

    hass.config_entries.async_update_entry(
        carrier_entry, options={CONF_TRACK_SUBSCRIBERS: False}
    )
    await async_setup_entry(hass, carrier_entry, EntityCollector([]))
    await hass.async_block_till_done()

    our_device = get_device_by_identifier(dev_reg, identifier, carrier_entry.entry_id)
    if our_device is not None:
        assert carrier_entry.entry_id not in device_config_entry_ids(our_device)
    if hasattr(ours, "config_entry_id"):
        assert dev_reg.async_get(ours.id) is None

    survivor = get_device_by_identifier(dev_reg, identifier, other_entry.entry_id)
    assert survivor is not None
    assert survivor.id == foreign_device_id
    assert survivor.identifiers == foreign_identifiers
    assert other_entry.entry_id in device_config_entry_ids(survivor)

    surviving_entity = ent_reg.async_get(foreign_entity_id)
    assert surviving_entity is not None
    assert surviving_entity.unique_id == foreign_unique_id
    assert surviving_entity.device_id == foreign_device_id
    assert surviving_entity.config_entry_id == other_entry.entry_id

    assert ent_reg.async_get(own_sub_entity.entity_id) is None
    assert ent_reg.async_get(own_other_entity.entity_id) is None


async def test_reconcile_removes_last_owner_subscriber_device(
    hass, carrier_entry, mock_carrier_coordinator
):
    """Last-owner own subscriber device and entity removed on both HA versions."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    identifier = (DOMAIN, "carrier_subscriber_last_owner")
    sub_device = dev_reg.async_get_or_create(
        config_entry_id=carrier_entry.entry_id, identifiers={identifier}
    )
    sub_entity = ent_reg.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="carrier_subscriber_last_owner_state",
        config_entry=carrier_entry,
        device_id=sub_device.id,
    )

    hass.config_entries.async_update_entry(
        carrier_entry, options={CONF_TRACK_SUBSCRIBERS: False}
    )
    await async_setup_entry(hass, carrier_entry, MagicMock())
    await hass.async_block_till_done()

    assert dev_reg.async_get(sub_device.id) is None
    assert ent_reg.async_get(sub_entity.entity_id) is None
    assert get_device_by_identifier(dev_reg, identifier, carrier_entry.entry_id) is None


async def test_reconcile_modern_no_deprecation_reported(
    hass, carrier_entry, mock_carrier_coordinator, caplog
):
    """Modern HA reports no deprecation or usage warning during reconciliation."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    identifier = (DOMAIN, "carrier_subscriber_dep_check")
    sub_device = dev_reg.async_get_or_create(
        config_entry_id=carrier_entry.entry_id, identifiers={identifier}
    )
    ent_reg.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="carrier_subscriber_dep_check_state",
        config_entry=carrier_entry,
        device_id=sub_device.id,
    )

    hass.config_entries.async_update_entry(
        carrier_entry, options={CONF_TRACK_SUBSCRIBERS: False}
    )

    report_usage_mock = MagicMock()
    with (
        patch(
            "homeassistant.helpers.device_registry.report_usage",
            report_usage_mock,
        ),
        patch.object(
            dev_reg,
            "async_update_device",
            wraps=dev_reg.async_update_device,
        ) as update_spy,
    ):
        caplog.clear()
        reconcile_subscriber_registry(hass, carrier_entry.entry_id)
        await hass.async_block_till_done()

    if (
        hasattr(sub_device, "config_entry_id")
        and getattr(sub_device, "config_entry_id", None) is not None
    ):
        report_usage_mock.assert_not_called()
        for call in update_spy.call_args_list:
            assert "remove_config_entry_id" not in call.kwargs

        deprecation_warnings = [
            rec.message
            for rec in caplog.records
            if rec.levelname in ("WARNING", "ERROR")
            and any(
                term in rec.message.lower()
                for term in (
                    "deprecated",
                    "remove_config_entry_id",
                    "config_entries",
                    "report_usage",
                )
            )
        ]
        assert deprecation_warnings == []


async def test_reconcile_subscriber_registry_modern_double(hass, carrier_entry):
    """Modern 2026.10.1 double removes whole device without reading config_entries."""
    dev_reg = dr.async_get(hass)
    modern_device = ModernDeviceEntryDouble(
        device_id="dev_modern_sole",
        identifiers={(DOMAIN, "carrier_subscriber_mod_sole")},
        config_entry_id=carrier_entry.entry_id,
    )
    with (
        patch(
            "homeassistant.helpers.device_registry.async_entries_for_config_entry",
            return_value=[modern_device],
        ),
        patch.object(dev_reg, "async_remove_device") as mock_remove,
        patch.object(dev_reg, "async_update_device") as mock_update,
    ):
        reconcile_subscriber_registry(hass, carrier_entry.entry_id)

    mock_remove.assert_called_once_with("dev_modern_sole")
    mock_update.assert_not_called()


async def test_carrier_fabric_dynamic_discovery(
    hass, carrier_entry, mock_carrier_coordinator
):
    """Test dynamic discovery of new plans and subscribers on coordinator refresh."""
    hass.config_entries.async_update_entry(
        carrier_entry,
        options={CONF_TRACK_SUBSCRIBERS: True},
    )

    added_batches = []

    def mock_add_entities(entities):
        added_batches.append(entities)

    await async_setup_entry(hass, carrier_entry, EntityCallback(mock_add_entities))
    assert len(added_batches) == 1  # Initial batch

    # Simulate coordinator update with new plan and new subscriber
    mock_carrier_coordinator.data["service_plans"]["plan_new"] = {
        "id": "plan_new",
        "name": "Gigabit Ultra",
        "status": "active",
        "downloadMbps": 1000.0,
        "uploadMbps": 1000.0,
    }
    mock_carrier_coordinator.data["subscribers"]["sub_new"] = {
        "id": "sub_new",
        "name": "Bob New",
        "state": "installed",
        "suspended": False,
        "planId": "plan_new",
    }

    # Trigger listeners
    for listener in mock_carrier_coordinator._listeners:
        listener()

    assert len(added_batches) == 2  # New batch discovered
    new_entities = added_batches[1]
    new_uids = {e.unique_id for e in new_entities}

    assert f"{carrier_entry.unique_id}_plan_plan_new_subscribers" in new_uids
    assert "carrier_subscriber_sub_new_state" in new_uids
    assert "carrier_subscriber_sub_new_service_plan" in new_uids


async def test_carrier_fabric_refresh_without_new_resources_adds_nothing(
    hass, carrier_entry, mock_carrier_coordinator
):
    """A refresh that finds nothing new must not add entities, nor re-add old ones."""
    hass.config_entries.async_update_entry(
        carrier_entry,
        options={CONF_TRACK_SUBSCRIBERS: True},
    )

    added_batches = []
    await async_setup_entry(hass, carrier_entry, EntityCallback(added_batches.append))
    assert len(added_batches) == 1

    # Same plans and subscribers as at setup.
    for listener in mock_carrier_coordinator._listeners:
        listener()
    assert len(added_batches) == 1

    # A genuinely new plan is added once; the refresh after it adds nothing again.
    mock_carrier_coordinator.data["service_plans"]["plan_new"] = {
        "id": "plan_new",
        "name": "Gigabit Ultra",
        "status": "active",
    }
    for listener in mock_carrier_coordinator._listeners:
        listener()
    assert len(added_batches) == 2
    assert {e.unique_id for e in added_batches[1]} == {
        f"{carrier_entry.unique_id}_plan_plan_new_subscribers"
    }

    for listener in mock_carrier_coordinator._listeners:
        listener()
    assert len(added_batches) == 2


async def test_carrier_fabric_plan_sensor_unavailable_when_update_fails(
    hass, carrier_entry, mock_carrier_coordinator
):
    """A plan present in the last data is still unavailable while refreshes fail."""
    plan_sensor = UnifiCarrierFabricPlanSubscribersSensor(
        mock_carrier_coordinator, carrier_entry, "plan_1"
    )
    assert plan_sensor.available is True

    mock_carrier_coordinator.last_update_success = False
    assert plan_sensor.available is False

    mock_carrier_coordinator.last_update_success = True
    assert plan_sensor.available is True


async def test_carrier_fabric_sensor_defensive_fallbacks(
    hass, carrier_entry, mock_carrier_coordinator
):
    """Test defensive fallbacks for missing data and coordinator updates."""
    # Test plan sensor when plan is absent from data
    ghost_plan_sensor = UnifiCarrierFabricPlanSubscribersSensor(
        mock_carrier_coordinator, carrier_entry, "ghost_plan"
    )
    assert ghost_plan_sensor.available is False
    assert ghost_plan_sensor.native_value == 0
    assert ghost_plan_sensor.extra_state_attributes == {}
    assert ghost_plan_sensor.entity_registry_enabled_default is True

    # Test plan sensor coordinator update
    mock_carrier_coordinator.data["service_plans"]["plan_1"]["name"] = "Renamed Fiber"
    ghost_plan_sensor._plan_id = "plan_1"
    with patch.object(ghost_plan_sensor, "async_write_ha_state"):
        ghost_plan_sensor._handle_coordinator_update()
    assert ghost_plan_sensor.translation_placeholders == {"plan_name": "Renamed Fiber"}

    # Test subscriber state and plan sensors when subscriber is absent
    ghost_sub_state = UnifiCarrierFabricSubscriberStateSensor(
        mock_carrier_coordinator, carrier_entry, "ghost_sub", "org-device"
    )
    assert ghost_sub_state.available is False
    assert ghost_sub_state.native_value is None
    assert ghost_sub_state.extra_state_attributes == {}

    ghost_sub_plan = UnifiCarrierFabricSubscriberPlanSensor(
        mock_carrier_coordinator, carrier_entry, "ghost_sub", "org-device"
    )
    assert ghost_sub_plan.available is False
    assert ghost_sub_plan.native_value is None
    assert ghost_sub_plan.extra_state_attributes == {}

    # Coordinator data is always the API's camelCase: snake_case keys are ignored
    mock_carrier_coordinator.data["subscribers"]["sub_snake"] = {
        "id": "sub_snake",
        "state": "provisioned",
        "suspended": False,
        "suspended_at": "2026-03-01T00:00:00Z",
        "activated_at": "2026-03-02T00:00:00Z",
        "plan_id": "plan_snake",
    }
    mock_carrier_coordinator.data["subscribers"]["sub_snake_plan"] = {
        "id": "sub_snake_plan",
        "planId": "plan_snake",
    }
    mock_carrier_coordinator.data["service_plans"]["plan_snake"] = {
        "id": "plan_snake",
        "name": "Snake Plan",
        "download_mbps": 500.0,
        "upload_mbps": 250.0,
        "archived_at": "2026-03-03T00:00:00Z",
        "status": "active",
    }
    snake_state = UnifiCarrierFabricSubscriberStateSensor(
        mock_carrier_coordinator, carrier_entry, "sub_snake", "org-device"
    )
    assert snake_state.extra_state_attributes == {}

    snake_unassigned = UnifiCarrierFabricSubscriberPlanSensor(
        mock_carrier_coordinator, carrier_entry, "sub_snake", "org-device"
    )
    assert snake_unassigned.native_value is None
    assert snake_unassigned.extra_state_attributes == {}

    snake_plan = UnifiCarrierFabricSubscriberPlanSensor(
        mock_carrier_coordinator, carrier_entry, "sub_snake_plan", "org-device"
    )
    assert snake_plan.native_value == "Snake Plan"
    assert snake_plan.extra_state_attributes == {}

    snake_plan_sensor = UnifiCarrierFabricPlanSubscribersSensor(
        mock_carrier_coordinator, carrier_entry, "plan_snake"
    )
    assert snake_plan_sensor.extra_state_attributes == {"status": "active"}
    assert snake_plan_sensor.entity_registry_enabled_default is True

    # Test aggregate sensor when summary is malformed
    mock_carrier_coordinator.data["summary"] = None
    agg = UnifiCarrierFabricAggregateSensor(
        mock_carrier_coordinator,
        carrier_entry,
        AGGREGATE_SENSOR_DESCRIPTIONS[0],
    )
    assert agg.native_value == 0

    # Test coordinator unavailable
    mock_carrier_coordinator.last_update_success = False
    assert snake_state.available is False

    # Test subscribers not a dict
    mock_carrier_coordinator.last_update_success = True
    mock_carrier_coordinator.data["subscribers"] = None
    assert snake_state.available is False
    assert snake_state._subscriber_data is None


async def _setup_and_register(hass, entry) -> list:
    """Set up the sensors and register their devices as the platform would."""
    added: list = []
    await async_setup_entry(hass, entry, EntityCollector(added))
    _register_devices(hass, entry, added)
    return added


def _register_devices(hass, entry, entities) -> None:
    """Create or update each entity's device in the entry, like entity setup."""
    dev_reg = dr.async_get(hass)
    for entity in entities:
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id, **entity.device_info
        )


def _subscriber_device(hass, entry, sub_id: str) -> dr.DeviceEntry:
    """Return a subscriber's device in the entry."""
    device = get_device_by_identifier(
        dr.async_get(hass), (DOMAIN, f"carrier_subscriber_{sub_id}"), entry.entry_id
    )
    assert device is not None
    return device


def _org_devices(hass, entry) -> list[dr.DeviceEntry]:
    """Return the entry's devices that carry the organisation identifier."""
    return [
        device
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        if (DOMAIN, "carrier_org_test123") in device.identifiers
    ]


async def test_subscriber_devices_link_to_the_organisation_device(hass, carrier_entry):
    """Setup registers the organisation device before any subscriber links to it."""
    hass.config_entries.async_update_entry(
        carrier_entry, options={CONF_TRACK_SUBSCRIBERS: True}
    )
    assert _org_devices(hass, carrier_entry) == []

    added: list = []
    await async_setup_entry(hass, carrier_entry, EntityCollector(added))

    # Registered by setup itself, before any entity's device is created.
    (org_device,) = _org_devices(hass, carrier_entry)
    assert org_device.name == "UniFi Carrier Fabric"
    assert org_device.entry_type is dr.DeviceEntryType.SERVICE

    _register_devices(hass, carrier_entry, added)
    for sub_id in ("sub_1", "sub_2", "sub_3_12345678"):
        device = _subscriber_device(hass, carrier_entry, sub_id)
        assert device.via_device_id == org_device.id
    assert [device.id for device in _org_devices(hass, carrier_entry)] == [
        org_device.id
    ]


@pytest.mark.parametrize("unique_id", [None, ""])
async def test_organisation_device_falls_back_to_the_entry_id(
    hass, carrier_entry, unique_id
):
    """An entry without a unique ID keys its organisation device by entry ID."""
    hass.config_entries.async_update_entry(
        carrier_entry, unique_id=unique_id, options={CONF_TRACK_SUBSCRIBERS: True}
    )

    added: list = []
    await async_setup_entry(hass, carrier_entry, EntityCollector(added))
    _register_devices(hass, carrier_entry, added)

    org_device = get_device_by_identifier(
        dr.async_get(hass), (DOMAIN, carrier_entry.entry_id), carrier_entry.entry_id
    )
    assert org_device is not None
    assert org_device.identifiers == {(DOMAIN, carrier_entry.entry_id)}
    assert _subscriber_device(hass, carrier_entry, "sub_1").via_device_id == (
        org_device.id
    )


async def test_existing_devices_are_kept_and_not_duplicated(hass, carrier_entry):
    """Existing organisation and subscriber devices keep their IDs."""
    hass.config_entries.async_update_entry(
        carrier_entry, options={CONF_TRACK_SUBSCRIBERS: True}
    )
    dev_reg = dr.async_get(hass)
    org = dev_reg.async_get_or_create(
        config_entry_id=carrier_entry.entry_id,
        identifiers={(DOMAIN, "carrier_org_test123")},
    )
    sub = dev_reg.async_get_or_create(
        config_entry_id=carrier_entry.entry_id,
        identifiers={(DOMAIN, "carrier_subscriber_sub_1")},
    )
    before = len(dr.async_entries_for_config_entry(dev_reg, carrier_entry.entry_id))

    await _setup_and_register(hass, carrier_entry)
    # A later coordinator update finds nothing new and adds no device.
    for listener in carrier_entry.runtime_data.coordinator._listeners:
        listener()

    assert [device.id for device in _org_devices(hass, carrier_entry)] == [org.id]
    sub_device = _subscriber_device(hass, carrier_entry, "sub_1")
    assert sub_device.id == sub.id
    assert sub_device.via_device_id == org.id
    # Only the two other subscribers are new.
    entries = dr.async_entries_for_config_entry(dev_reg, carrier_entry.entry_id)
    assert len(entries) == before + 2


async def test_subscribers_link_to_their_own_entrys_organisation_device(
    hass, carrier_entry
):
    """An organisation device with the same identifier in another entry is ignored."""
    hass.config_entries.async_update_entry(
        carrier_entry, options={CONF_TRACK_SUBSCRIBERS: True}
    )
    other_entry = MockConfigEntry(domain=DOMAIN, title="Other organisation")
    other_entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    foreign = dev_reg.async_get_or_create(
        config_entry_id=other_entry.entry_id,
        identifiers={(DOMAIN, "carrier_org_test123")},
    )

    await _setup_and_register(hass, carrier_entry)

    (org_device,) = _org_devices(hass, carrier_entry)
    assert org_device.id != foreign.id
    sub_device = _subscriber_device(hass, carrier_entry, "sub_1")
    assert sub_device.via_device_id == org_device.id
    assert dev_reg.async_get(foreign.id) == foreign


async def test_subscriber_found_later_links_to_the_organisation_device(
    hass, carrier_entry, mock_carrier_coordinator
):
    """A subscriber added by a later update links to the same parent."""
    hass.config_entries.async_update_entry(
        carrier_entry, options={CONF_TRACK_SUBSCRIBERS: True}
    )

    def add(entities) -> None:
        _register_devices(hass, carrier_entry, entities)

    await async_setup_entry(hass, carrier_entry, EntityCallback(add))
    mock_carrier_coordinator.data["subscribers"]["sub_4"] = {
        "id": "sub_4",
        "name": "Late Subscriber",
    }
    for listener in mock_carrier_coordinator._listeners:
        listener()

    (org_device,) = _org_devices(hass, carrier_entry)
    sub_device = _subscriber_device(hass, carrier_entry, "sub_4")
    assert sub_device.via_device_id == org_device.id
