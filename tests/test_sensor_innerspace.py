# Copyright 2026 UniFi Insights contributors
"""Tests for UniFi InnerSpace transforms, entities, discovery, and diagnostics."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.diagnostics import REDACTED
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api.auth import ApiKeyAuth, LocalAuth
from unifi_official_api.const import ConnectionType
from unifi_official_api.exceptions import UniFiValidationError
from unifi_official_api.innerspace import UniFiInnerSpaceClient
from unifi_official_api.innerspace import client as innerspace_client
from unifi_official_api.innerspace.models import InnerSpaceFloorPlan

from custom_components.unifi_insights import innerspace_transforms
from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.coordinators import UnifiInsightsFacadeCoordinator
from custom_components.unifi_insights.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.unifi_insights.innerspace_transforms import (
    _normalize_innerspace_mac,
    _to_mapping,
    build_innerspace_view,
    correlate_innerspace_devices,
    innerspace_correlation_key,
    innerspace_view_inputs,
    normalize_innerspace_snapshot,
    parse_floor_plan_asset_path,
    transform_innerspace_device,
    transform_innerspace_floor_plan,
    transform_innerspace_project,
)
from custom_components.unifi_insights.sensor import (
    INNERSPACE_PLACEMENT_DESCRIPTION,
    UnifiInnerSpaceEntity,
    UnifiInsightsInnerSpacePlacementSensor,
    _discover_innerspace_sensors,
)
from custom_components.unifi_insights.sensor import (
    async_setup_entry as async_setup_sensor,
)
from tests.fixtures.facade_data import (
    facade_data,
    set_availability,
    with_camera,
    with_network_device,
    with_sections,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _strings(value: Any) -> list[str]:
    """Recursively collect all string keys and values from a diagnostics dict."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for k, v in value.items():
            if isinstance(k, str):
                result.append(k)
            result.extend(_strings(v))
        return result
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_strings(item))
        return out
    return []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("aabbccddeeff", "aa:bb:cc:dd:ee:ff"),
        ("AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff"),
        ("aa-bb-cc-dd-ee-ff", "aa:bb:cc:dd:ee:ff"),
        (" aa.bb.cc.dd.ee.ff ", "aa:bb:cc:dd:ee:ff"),
        ("0x123456789a", None),
        ("+123456789ab", None),
        ("aa:bb:cc:dd:ee:fg", None),
        ("aabbccddeef", None),
        (None, None),
        (123, None),
    ],
)
def test_normalize_innerspace_mac(value: Any, expected: str | None) -> None:
    """Normalize supported MAC formats and reject malformed input."""
    assert _normalize_innerspace_mac(value) == expected


async def test_diagnostics_includes_redacted_innerspace_and_anonymizes_macs(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations: Any,
) -> None:
    """InnerSpace diagnostics redact IDs/serials and anonymize MACs."""
    coordinator = init_integration.runtime_data.coordinator
    coordinator.innerspace_client = MagicMock()
    coordinator.data = with_sections(
        coordinator.data,
        innerspace={
            "project": {"id": "secret-proj-id", "plan_count": 1, "product_count": 1},
            "floor_plans": {
                "fp-secret-1": {
                    "id": "fp-secret-1",
                    "name": "Level 1",
                    "floor_number": 1,
                    "site_id": "site-secret-1",
                    "ppm": 25.0,
                }
            },
            "access_points": {},
            "switches": {},
            "inventory": {},
            "devices": {
                "ap-rec-1": {
                    "id": "ap-rec-1",
                    "name": "Ceiling AP",
                    "model": "U6-Pro",
                    "device_type": "access_point",
                    "placement_state": "placed",
                    "mac": "de:ad:be:ef:12:34",
                    "serial": "SECRET-SERIAL-99",
                    "floor_plan_id": "fp-secret-1",
                    "floor_plan_name": "Level 1",
                    "site_id": "site-secret-1",
                    "matched_domain": "network",
                    "matched_site_id": "site-secret-1",
                    "matched_device_id": "net-dev-secret-1",
                }
            },
            "last_update": "2026-09-25T00:00:00+00:00",
        },
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)

    assert diagnostics["connection"]["innerspace_client_connected"] is True
    assert "innerspace" not in diagnostics["data"]
    assert "innerspace" in diagnostics
    innerspace_diag = diagnostics["innerspace"]
    assert innerspace_diag["available"] is True
    assert innerspace_diag["counts"]["devices"] == 1
    dev_diag = innerspace_diag["devices"][0]
    assert dev_diag["name"] == "Ceiling AP"
    assert dev_diag["serial"] == REDACTED
    assert dev_diag["matched_device_id"] == REDACTED
    assert dev_diag["mac"].startswith("**REDACTED-MAC-")
    all_strings = _strings(diagnostics)
    for raw_sensitive in (
        "secret-proj-id",
        "fp-secret-1",
        "site-secret-1",
        "ap-rec-1",
        "net-dev-secret-1",
        "SECRET-SERIAL-99",
        "de:ad:be:ef:12:34",
    ):
        assert raw_sensitive not in all_strings


@pytest.mark.asyncio
async def test_innerspace_placement_sensor_discovery_and_device_correlation(
    hass: HomeAssistant,
    mock_coordinator: MagicMock,
    mock_config_entry: MagicMock,
) -> None:
    """InnerSpace placement sensors discover records and attach via MAC."""
    mock_config_entry.runtime_data = MagicMock(coordinator=mock_coordinator)
    innerspace: dict[str, Any] = {
        "project": {"id": "proj-1"},
        "floor_plans": {"fp-1": {"id": "fp-1", "name": "Lobby"}},
        "access_points": {},
        "switches": {},
        "inventory": {},
        "devices": {
            "is-ap-1": {
                "id": "is-ap-1",
                "name": "Lobby AP",
                "model": "U6-Pro",
                "device_type": "access_point",
                "placement_state": "placed",
                "floor_plan_id": "fp-1",
                "floor_plan_name": "Lobby",
                "site_id": "site-1",
                "x": 12.5,
                "y": 34.0,
                "height": 2.7,
                "matched_domain": "network",
                "matched_site_id": "site-1",
                "matched_device_id": "net-ap-1",
            },
            "is-inv-1": {
                "id": "is-inv-1",
                "name": "Spare Switch",
                "model": "USW-Flex",
                "device_type": None,
                "placement_state": "unplaced",
                "mac": "aa:bb:cc:99:88:77",
                "serial": "SN-FLEX-1",
                "matched_domain": None,
                "matched_site_id": None,
                "matched_device_id": None,
            },
        },
    }
    mock_coordinator.data = with_sections(
        mock_coordinator.data,
        devices={
            "site-1": {
                "net-ap-1": {
                    "id": "net-ap-1",
                    "name": "Lobby AP",
                    "model": "U6-Pro",
                    "macAddress": "aa:bb:cc:11:22:33",
                }
            }
        },
        innerspace=innerspace,
    )

    add_entities = MagicMock()
    await async_setup_sensor(hass, mock_config_entry, add_entities)
    added = [e for call in add_entities.call_args_list for e in call[0][0]]
    placement_sensors = {
        e.unique_id: e
        for e in added
        if getattr(e, "unique_id", "").startswith("innerspace_")
    }

    assert "innerspace_is-ap-1_placement" in placement_sensors
    assert "innerspace_is-inv-1_placement" in placement_sensors

    ap_sensor = placement_sensors["innerspace_is-ap-1_placement"]
    assert ap_sensor.native_value == "placed"
    assert ap_sensor.available is True
    assert ap_sensor.device_info["identifiers"] == {(DOMAIN, "site-1_net-ap-1")}
    assert "suggested_area" not in ap_sensor.device_info
    assert ap_sensor.extra_state_attributes["floor_plan_name"] == "Lobby"
    assert ap_sensor.extra_state_attributes["x"] == 12.5

    inv_sensor = placement_sensors["innerspace_is-inv-1_placement"]
    assert inv_sensor.native_value == "unplaced"
    assert inv_sensor.device_info["identifiers"] == {(DOMAIN, "innerspace_is-inv-1")}
    assert "connections" not in inv_sensor.device_info
    assert "suggested_area" not in inv_sensor.device_info

    # Gated on innerspace_available
    set_availability(mock_coordinator, innerspace=False)
    assert ap_sensor.available is False

    # Test Protect correlation and entity registry device_id reconciliation
    set_availability(mock_coordinator, innerspace=True)
    real_entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=mock_config_entry.entry_id,
    )
    real_entry.add_to_hass(hass)
    mock_coordinator.config_entry = real_entry
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    initial_dev = dev_reg.async_get_or_create(
        config_entry_id=real_entry.entry_id,
        identifiers={(DOMAIN, "innerspace_is-inv-1")},
    )
    reg_entry = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        inv_sensor.unique_id,
        config_entry=real_entry,
        device_id=initial_dev.id,
    )
    inv_sensor.hass = hass
    inv_sensor.entity_id = reg_entry.entity_id

    # The Protect platform registered the camera already. Reconciliation moves
    # the entity to it and must leave the camera's own metadata alone.
    camera_dev = dev_reg.async_get_or_create(
        config_entry_id=real_entry.entry_id,
        identifiers={(DOMAIN, "protect_camera_cam-1")},
        name="Front Door",
        manufacturer="Ubiquiti",
        model="G5 Bullet",
        serial_number="CAM-SERIAL",
    )
    inv_record = {
        **innerspace["devices"]["is-inv-1"],
        "placement_state": "other",
        "matched_domain": "protect",
        "matched_protect_type": "camera",
        "matched_device_id": "cam-1",
    }
    mock_coordinator.data = with_sections(
        with_camera(mock_coordinator.data, "cam-1", {"id": "cam-1"}),
        innerspace={
            **innerspace,
            "devices": {**innerspace["devices"], "is-inv-1": inv_record},
        },
    )
    inv_sensor.async_write_ha_state = MagicMock()
    inv_sensor._handle_coordinator_update()
    inv_sensor.async_write_ha_state.assert_called_once()
    assert inv_sensor.native_value == "unknown"
    assert inv_sensor.device_info["identifiers"] == {(DOMAIN, "protect_camera_cam-1")}
    updated_entry = ent_reg.async_get(reg_entry.entity_id)
    assert updated_entry is not None
    protect_dev = dev_reg.async_get_or_create(
        config_entry_id=real_entry.entry_id,
        identifiers={(DOMAIN, "protect_camera_cam-1")},
    )
    assert updated_entry.device_id == protect_dev.id == camera_dev.id
    assert (
        protect_dev.name,
        protect_dev.manufacturer,
        protect_dev.model,
        protect_dev.serial_number,
    ) == ("Front Door", "Ubiquiti", "G5 Bullet", "CAM-SERIAL")

    # When record disappears from snapshot, fallback to innerspace_<id>
    mock_coordinator.data = with_sections(
        mock_coordinator.data,
        innerspace={**innerspace, "inventory": {}, "devices": {}},
    )
    inv_sensor._handle_coordinator_update()
    assert inv_sensor.native_value == "unknown"
    assert inv_sensor.device_info["identifiers"] == {(DOMAIN, "innerspace_is-inv-1")}
    reverted_entry = ent_reg.async_get(reg_entry.entity_id)
    assert reverted_entry is not None
    assert reverted_entry.device_id == initial_dev.id


async def test_innerspace_coordinator_update_uses_captured_availability(
    hass: HomeAssistant, mock_coordinator: MagicMock
) -> None:
    """Updates write state once and never shadow coordinator availability."""
    mock_coordinator.hass = hass
    mock_coordinator.data = with_sections(
        mock_coordinator.data,
        innerspace={
            **mock_coordinator.data["innerspace"],
            "devices": {"rec-1": {"id": "rec-1", "placement_state": "placed"}},
        },
    )
    entity = UnifiInnerSpaceEntity(
        mock_coordinator, INNERSPACE_PLACEMENT_DESCRIPTION, "rec-1"
    )
    entity.hass = hass

    for available in (True, False, True):
        set_availability(mock_coordinator, innerspace=available)
        assert entity.available is available
        with patch.object(entity, "async_write_ha_state") as write_state:
            entity._handle_coordinator_update()
        write_state.assert_called_once_with()
        assert entity.available is available
        assert "_attr_available" not in vars(entity)
        # Home Assistant's descriptor stores _attr_available under this name.
        assert "__attr_available" not in vars(entity)


def test_innerspace_entity_and_discovery_edge_cases(
    mock_coordinator: MagicMock,
) -> None:
    """Cover non-dict coordinator branches and duplicate discovery keys."""
    known_keys: set[tuple[Any, ...]] = set()
    entities: list[Any] = []

    # Non-dict innerspace section
    mock_coordinator.data = with_sections(mock_coordinator.data, innerspace="invalid")
    _discover_innerspace_sensors(mock_coordinator, known_keys, entities)
    assert entities == []

    # Non-dict devices section
    mock_coordinator.data = with_sections(
        mock_coordinator.data, innerspace={"devices": "invalid"}
    )
    _discover_innerspace_sensors(mock_coordinator, known_keys, entities)
    assert entities == []

    # Invalid record entry + duplicate key guard
    mock_coordinator.data = with_sections(
        mock_coordinator.data,
        innerspace={
            "devices": {
                "bad": "not-a-dict",
                "rec-1": {"id": "rec-1", "placement_state": "placed"},
            }
        },
    )
    _discover_innerspace_sensors(mock_coordinator, known_keys, entities)
    assert len(entities) == 1
    _discover_innerspace_sensors(mock_coordinator, known_keys, entities)
    assert len(entities) == 1

    sensor = UnifiInsightsInnerSpacePlacementSensor(
        mock_coordinator, INNERSPACE_PLACEMENT_DESCRIPTION, "rec-1"
    )
    # Unregistered entity_id returns early in _reconcile_device_association
    sensor.hass = MagicMock()
    sensor.entity_id = "sensor.unregistered"
    with pytest.MonkeyPatch.context() as mp:
        mock_er = MagicMock()
        mock_er.async_get.return_value = None
        mp.setattr(er, "async_get", lambda _hass: mock_er)
        sensor._reconcile_device_association(sensor._build_device_fields())
        sensor._set_device_fields({"identifiers": {(DOMAIN, "old")}})
        sensor._reconcile_device_association({"identifiers": {(DOMAIN, "new")}})

    # Non-dict innerspace and non-dict devices in innerspace_record property
    mock_coordinator.data = with_sections(mock_coordinator.data, innerspace="invalid")
    assert sensor.innerspace_record is None
    mock_coordinator.data = with_sections(
        mock_coordinator.data, innerspace={"devices": "invalid"}
    )
    assert sensor.innerspace_record is None


def test_innerspace_transforms_edge_cases() -> None:
    """Cover remaining edge cases in innerspace_transforms."""
    assert _to_mapping(None) == {}
    assert _to_mapping(42) == {}
    assert _normalize_innerspace_mac("not-a-mac") is None

    # Wrapped data dict, invalid plan/product entries, and empty project returning None
    assert transform_innerspace_project({"project": {"title": "No ID"}}) is None
    wrapped = transform_innerspace_project(
        {
            "data": {
                "project": {"id": "proj-w"},
                "plans": [{"id": ""}, {"id": "plan-1", "name": "Floor 1"}],
                "products": [{"id": None}, {"id": "prod-1", "sku": "U6"}],
            }
        }
    )
    assert wrapped is not None
    assert wrapped["id"] == "proj-w"
    assert len(wrapped["plans"]) == 1
    assert len(wrapped["products"]) == 1

    # Placed record missing coordinates -> placement_state = unknown
    placed_missing_coords = transform_innerspace_device(
        {"id": "ap-1", "floorPlanId": "fp-1"},
        device_kind="access_point",
        placed=True,
        floor_plans={"fp-1": {"name": "Main", "site_id": "s1"}},
    )
    assert placed_missing_coords["placement_state"] == "unknown"
    assert placed_missing_coords["site_id"] == "s1"

    # Unplaced record with partial coordinates -> placement_state = unknown
    unplaced_with_x = transform_innerspace_device(
        {"id": "inv-1", "x": 1.0},
        placed=False,
    )
    assert unplaced_with_x["placement_state"] == "unknown"

    # Indexers and correlate_innerspace_devices with non-dict / invalid entries
    snapshot = {
        "access_points": {
            "ap-bad": "not-a-dict",
            "ap-no-mac": {"id": "ap-no-mac", "mac": None},
        },
        "switches": {},
        "inventory": {},
        "devices": {},
    }
    correlate_innerspace_devices(
        snapshot,
        network_devices_by_site={
            "bad-site": "not-a-dict",
            "s1": {
                "bad-dev": "not-a-dict",
                "no-mac": {"id": "no-mac"},
            },
        },
        protect_data={
            "cameras": {
                "bad-cam": "not-a-dict",
                "no-mac-cam": {"id": "no-mac-cam"},
            }
        },
    )
    assert snapshot["access_points"]["ap-no-mac"]["correlation"] is None

    # normalize_innerspace_snapshot with project-only floor plan and invalid IDs
    norm = normalize_innerspace_snapshot(
        project={
            "project": {"id": "p1"},
            "plans": [{"id": "fp-from-proj", "name": "P"}],
        },
        floor_plans=[{"id": ""}],
        access_points=[{"id": ""}],
        switches=[{"id": ""}],
        inventory=[{"id": ""}],
    )
    assert "fp-from-proj" in norm["floor_plans"]


def test_innerspace_floor_plan_model_image_url() -> None:
    """InnerSpaceFloorPlan parses snake_case and camelCase image_url."""
    plan_snake = InnerSpaceFloorPlan.model_validate(
        {
            "id": "fp-1",
            "name": "First Floor",
            "image_url": "/proxy/innerspace/integration/v1/assets/fp-1/floor.png",
        }
    )
    assert (
        plan_snake.image_url == "/proxy/innerspace/integration/v1/assets/fp-1/floor.png"
    )

    plan_camel = InnerSpaceFloorPlan.model_validate(
        {
            "id": "fp-2",
            "name": "Second Floor",
            "imageUrl": "/proxy/innerspace/integration/v1/assets/fp-2/plan.jpeg",
        }
    )
    assert (
        plan_camel.image_url == "/proxy/innerspace/integration/v1/assets/fp-2/plan.jpeg"
    )


def test_transform_innerspace_floor_plan_image_url() -> None:
    """transform_innerspace_floor_plan preserves image_url from the plan itself."""
    transformed = transform_innerspace_floor_plan(
        {
            "id": "fp-1",
            "name": "Main Floor",
            "image_url": "/proxy/innerspace/integration/v1/assets/fp-1/floor.png",
        }
    )
    assert (
        transformed["image_url"]
        == "/proxy/innerspace/integration/v1/assets/fp-1/floor.png"
    )

    transformed_camel = transform_innerspace_floor_plan(
        {
            "id": "fp-2",
            "name": "Upper Floor",
            "imageUrl": "/proxy/innerspace/integration/v1/assets/fp-2/plan.png",
        }
    )
    assert (
        transformed_camel["image_url"]
        == "/proxy/innerspace/integration/v1/assets/fp-2/plan.png"
    )

    # Project plans never carry an image, so the project plan cannot supply one.
    transformed_from_proj = transform_innerspace_floor_plan(
        {"id": "fp-3", "name": "Basement"},
        project_plan={"id": "fp-3", "name": "Basement"},
    )
    assert transformed_from_proj["image_url"] is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "/proxy/innerspace/integration/v1/assets/3fa85f64/floor.png",
            ("3fa85f64", "floor.png"),
        ),
        (
            (
                "/v1/connector/consoles/c1/innerspace/integration/v1"
                "/assets/3fa85f64/plan.jpeg"
            ),
            ("3fa85f64", "plan.jpeg"),
        ),
        (
            "/assets/fp-1/ground.png",
            ("fp-1", "ground.png"),
        ),
        (
            (
                "https://192.168.1.1/proxy/innerspace/integration/v1"
                "/assets/3fa85f64/img.png?token=xyz#frag"
            ),
            ("3fa85f64", "img.png"),
        ),
        (
            "/proxy/innerspace/integration/v1/assets/3fa85f64/..",
            None,
        ),
        (
            "/assets/3fa85f64/../secret.png",
            None,
        ),
        (
            "/assets/../img.png",
            None,
        ),
        (
            "/proxy/innerspace/integration/v1/assets/3fa85f64/sub/img.png",
            None,
        ),
        (
            r"/proxy/innerspace/integration/v1/assets/3fa85f64/sub\img.png",
            None,
        ),
        (
            "/proxy/innerspace/integration/v1/notassets/3fa85f64/floor.png",
            None,
        ),
        (
            "/proxy/innerspace/integration/v1/assets/floor.png",
            None,
        ),
        (
            "/proxy/innerspace/integration/v1/assets/3fa85f64/floor.png/",
            None,
        ),
        (
            "/proxy/innerspace/integration/v1/assets/3fa85f64/floor%20plan%20%231.png",
            ("3fa85f64", "floor plan #1.png"),
        ),
        (
            "/proxy/innerspace/integration/v1/assets/3fa85f64/pl%C3%A4n.png",
            ("3fa85f64", "plän.png"),
        ),
        (
            "/proxy/innerspace/integration/v1/assets/3fa85f64/" + "a" * 255,
            ("3fa85f64", "a" * 255),
        ),
        (
            "/proxy/innerspace/integration/v1/assets/3fa85f64/" + "a" * 256,
            None,
        ),
        (
            "/proxy/innerspace/integration/v1/assets/3fa85f64/.hidden.png",
            ("3fa85f64", ".hidden.png"),
        ),
        (
            "/proxy/innerspace/integration/v1/assets/3fa85f64/%2e%2e",
            None,
        ),
        (
            "/proxy/innerspace/integration/v1/assets/3fa85f64/%2E",
            None,
        ),
        (
            "/proxy/innerspace/integration/v1/assets/3fa85f64/sub%2Fimg.png",
            None,
        ),
        (
            "/proxy/innerspace/integration/v1/assets/3fa85f64/sub%5Cimg.png",
            None,
        ),
        (
            "/proxy/innerspace/integration/v1/assets/3fa85f64/img%00.png",
            None,
        ),
        (
            "/proxy/innerspace/integration/v1/assets/3fa85f64/img%0A.png",
            None,
        ),
        (
            "/proxy/innerspace/integration/v1/assets/3fa85f64/%20%20",
            None,
        ),
        (
            "/proxy/innerspace/integration/v1/assets/%2E%2E/floor.png",
            None,
        ),
        ("", None),
        ("   ", None),
        ("?a=1", None),
        ("#frag", None),
        ("https://host", None),
        (None, None),
        (123, None),
    ],
)
def test_parse_floor_plan_asset_path(
    url: Any, expected: tuple[str, str] | None
) -> None:
    """Extract plan_id and filename correctly or return None for invalid URLs."""
    assert parse_floor_plan_asset_path(url) == expected


@pytest.mark.asyncio
async def test_download_floor_plan_image_local_and_remote() -> None:
    """Test download_floor_plan_image constructs correct path and passes parameters."""
    local_client = UniFiInnerSpaceClient(
        auth=LocalAuth(api_key="k", verify_ssl=False),
        base_url="https://192.168.1.1",
        connection_type=ConnectionType.LOCAL,
    )
    local_client._get_binary_with_content_type = AsyncMock(
        return_value=(b"local_png_bytes", "image/png")
    )
    res = await local_client.download_floor_plan_image("fp-123", "floor.png")
    assert res == (b"local_png_bytes", "image/png")
    local_client._get_binary_with_content_type.assert_awaited_once_with(
        "/proxy/innerspace/integration/v1/assets/fp-123/floor.png"
    )

    remote_client = UniFiInnerSpaceClient(
        auth=ApiKeyAuth(api_key="k"),
        connection_type=ConnectionType.REMOTE,
        console_id="console-abc",
    )
    remote_client._get_binary_with_content_type = AsyncMock(
        return_value=(b"remote_jpeg_bytes", "image/jpeg")
    )
    res_remote = await remote_client.download_floor_plan_image("fp-456", "plan.jpeg")
    assert res_remote == (b"remote_jpeg_bytes", "image/jpeg")
    remote_client._get_binary_with_content_type.assert_awaited_once_with(
        "/v1/connector/consoles/console-abc/innerspace/integration/v1"
        "/assets/fp-456/plan.jpeg"
    )


@pytest.mark.asyncio
async def test_download_floor_plan_image_quotes_path_segments() -> None:
    """Decoded names (spaces, unicode, 255 chars) are percent-encoded in the path."""
    client = UniFiInnerSpaceClient(
        auth=LocalAuth(api_key="k", verify_ssl=False),
        base_url="https://192.168.1.1",
        connection_type=ConnectionType.LOCAL,
    )
    client._get_binary_with_content_type = AsyncMock(return_value=(b"x", "image/png"))

    await client.download_floor_plan_image("fp-1", "floor plan #1.png")
    client._get_binary_with_content_type.assert_awaited_with(
        "/proxy/innerspace/integration/v1/assets/fp-1/floor%20plan%20%231.png"
    )

    await client.download_floor_plan_image("fp-1", ".hidden")
    client._get_binary_with_content_type.assert_awaited_with(
        "/proxy/innerspace/integration/v1/assets/fp-1/.hidden"
    )

    long_name = "b" * 255
    await client.download_floor_plan_image("fp-1", long_name)
    client._get_binary_with_content_type.assert_awaited_with(
        f"/proxy/innerspace/integration/v1/assets/fp-1/{long_name}"
    )


def test_asset_segment_check_is_shared() -> None:
    """The client and the transforms use one asset segment definition."""
    assert (
        innerspace_transforms.is_valid_asset_segment
        is innerspace_client.is_valid_asset_segment
    )


@pytest.mark.parametrize(
    ("plan_id", "filename"),
    [
        ("..", "image.png"),
        ("fp-1", ".."),
        ("fp-1", "dir/image.png"),
        ("fp-1", "dir\\image.png"),
        ("dir/fp-1", "image.png"),
        ("", "image.png"),
        ("fp-1", ""),
        ("   ", "image.png"),
        ("fp-1", "   "),
        ("fp-1", "a\x00b.png"),
        ("fp-1", "a\nb.png"),
        ("fp-1", "a\x7fb.png"),
        ("fp-1", "a\x85b.png"),
        ("fp\x00", "image.png"),
        ("fp-1", "a" * 256),
        ("a" * 256, "image.png"),
        (".", "image.png"),
        ("fp-1", "."),
    ],
)
@pytest.mark.asyncio
async def test_download_floor_plan_image_rejects_invalid_segments_before_request(
    plan_id: str,
    filename: str,
) -> None:
    """Client validates plan_id and filename and rejects bad segments."""
    client = UniFiInnerSpaceClient(
        auth=LocalAuth(api_key="k", verify_ssl=False),
        base_url="https://192.168.1.1",
        connection_type=ConnectionType.LOCAL,
    )
    client._get_binary_with_content_type = AsyncMock()

    with pytest.raises(UniFiValidationError):
        await client.download_floor_plan_image(plan_id, filename)

    client._get_binary_with_content_type.assert_not_called()


@pytest.mark.asyncio
async def test_facade_coordinator_async_get_floor_plan_image() -> None:
    """Test async_get_floor_plan_image with and without client."""
    coordinator = MagicMock(spec=UnifiInsightsFacadeCoordinator)
    coordinator.innerspace_client = None
    res_none = await UnifiInsightsFacadeCoordinator.async_get_floor_plan_image(
        coordinator, "fp-1", "plan.png"
    )
    assert res_none is None

    mock_client = AsyncMock()
    mock_client.download_floor_plan_image.return_value = (b"bytes", "image/png")
    coordinator.innerspace_client = mock_client

    res = await UnifiInsightsFacadeCoordinator.async_get_floor_plan_image(
        coordinator, "fp-1", "plan.png"
    )
    assert res == (b"bytes", "image/png")
    mock_client.download_floor_plan_image.assert_awaited_once_with("fp-1", "plan.png")


def test_innerspace_entity_matched_protect_device_missing_from_protect_section(
    mock_coordinator: MagicMock,
) -> None:
    """Fall back to innerspace ID when matched Protect device is missing."""
    mock_coordinator.data = with_sections(
        mock_coordinator.data,
        protect={"cameras": {}},
        innerspace={
            "devices": {
                "is-inv-1": {
                    "id": "is-inv-1",
                    "name": "Camera Stand",
                    "matched_domain": "protect",
                    "matched_protect_type": "camera",
                    "matched_device_id": "missing-cam",
                }
            }
        },
    )
    sensor = UnifiInsightsInnerSpacePlacementSensor(
        mock_coordinator, INNERSPACE_PLACEMENT_DESCRIPTION, "is-inv-1"
    )
    assert sensor.device_info["identifiers"] == {(DOMAIN, "innerspace_is-inv-1")}


# --- InnerSpace view (ADR-013) ----------------------------------------------

VIEW_RAW = {
    "project": {"id": "proj-1"},
    "floor_plans": {"fp-1": {"id": "fp-1", "site_id": "s1"}},
    "access_points": {
        "ap-1": {"id": "ap-1", "mac": "02:00:00:00:00:01", "site_id": "s1"},
        "ap-bad": "not-a-dict",
    },
    "switches": {"sw-1": {"id": "sw-1", "mac": "02:00:00:00:00:02"}},
    "inventory": {"inv-1": {"id": "inv-1", "mac": "02:00:00:00:00:03"}},
    "last_update": "2026-10-20T00:00:00+00:00",
}
VIEW_DEVICES = {
    "s1": {
        "dev-ap": {"id": "dev-ap", "macAddress": "02:00:00:00:00:01", "model": "U7"},
        "dev-sw": {"id": "dev-sw", "mac": "02:00:00:00:00:02", "type": "usw"},
        "dev-bad": "not-a-dict",
    },
    "bad-site": "not-a-dict",
}
VIEW_PROTECT = {
    "cameras": {"cam-1": {"id": "cam-1", "macAddress": "02:00:00:00:00:03"}},
    "lights": {"bad": None},
    "events": {"motion": {}},
}


def test_build_innerspace_view_correlates_without_changing_its_inputs() -> None:
    """The view correlates copies; the coordinators' data is left as it was."""
    raw, devices, protect = (
        copy.deepcopy(VIEW_RAW),
        copy.deepcopy(VIEW_DEVICES),
        copy.deepcopy(VIEW_PROTECT),
    )

    view = build_innerspace_view(raw, devices, protect)

    assert (raw, devices, protect) == (VIEW_RAW, VIEW_DEVICES, VIEW_PROTECT)
    assert view["project"] == {"id": "proj-1"}
    assert view["floor_plans"] == VIEW_RAW["floor_plans"]
    assert view["floor_plans"] is not raw["floor_plans"]
    assert set(view["placed_devices"]) == {"ap-1", "sw-1"}
    assert set(view["devices"]) == {"ap-1", "sw-1", "inv-1"}
    assert view["devices"]["ap-1"] is view["access_points"]["ap-1"]
    assert view["access_points"]["ap-1"] is not raw["access_points"]["ap-1"]
    assert view["last_update"] == VIEW_RAW["last_update"]
    assert view["correlations"]["ap-1"]["device_id"] == "dev-ap"
    assert view["correlations"]["sw-1"]["device_id"] == "dev-sw"
    assert view["correlations"]["inv-1"]["device_id"] == "cam-1"
    assert view["access_points"]["ap-1"]["matched_domain"] == "network"
    assert view["inventory"]["inv-1"]["matched_protect_type"] == "camera"


@pytest.mark.parametrize("raw", [None, {}])
def test_build_innerspace_view_without_data_is_empty(raw: Any) -> None:
    """Without InnerSpace data the view is the empty normalized snapshot."""
    view = build_innerspace_view(raw, VIEW_DEVICES, VIEW_PROTECT)

    assert view == {**normalize_innerspace_snapshot(), "last_update": None}


def test_build_innerspace_view_ignores_malformed_device_sections() -> None:
    """Non-mapping Network or Protect data correlates nothing."""
    view = build_innerspace_view(VIEW_RAW, "not-a-dict", None)

    assert set(view["correlations"].values()) == {None}


def test_innerspace_view_inputs_name_the_objects_read() -> None:
    """The memo inputs are the raw data, devices and correlated collections."""
    inputs = innerspace_view_inputs(VIEW_RAW, VIEW_DEVICES, VIEW_PROTECT)

    assert inputs[0] is VIEW_RAW
    assert inputs[1] is VIEW_DEVICES
    assert VIEW_PROTECT["cameras"] in inputs
    assert all(item is not VIEW_PROTECT["events"] for item in inputs)
    assert innerspace_view_inputs(None, VIEW_DEVICES, VIEW_PROTECT) == (None,)
    assert innerspace_view_inputs(VIEW_RAW, VIEW_DEVICES, None)[2:] == (None,)


def test_innerspace_correlation_key_holds_what_correlation_reads() -> None:
    """The key lists each device's MAC, id and type, skipping malformed entries."""
    network, protect = innerspace_correlation_key(VIEW_DEVICES, VIEW_PROTECT)

    assert network == (
        ("s1", "dev-ap", "02:00:00:00:00:01", "dev-ap", "U7"),
        ("s1", "dev-sw", "02:00:00:00:00:02", "dev-sw", "usw"),
    )
    assert protect == (("cameras", "cam-1", "02:00:00:00:00:03", "cam-1"),)
    assert innerspace_correlation_key("not-a-dict", None) == ((), ())
    assert innerspace_correlation_key(
        copy.deepcopy(VIEW_DEVICES), copy.deepcopy(VIEW_PROTECT)
    ) == (network, protect)


_NETWORK_MATCH = {
    "id": "rec-1",
    "matched_domain": "network",
    "matched_site_id": "site-1",
    "matched_device_id": "net-1",
}
_PROTECT_MATCH = {
    "id": "rec-1",
    "matched_domain": "protect",
    "matched_protect_type": "camera",
    "matched_device_id": "cam-1",
}


def _innerspace_snapshot(record: dict[str, Any], **sections: Any) -> Any:
    """Return a snapshot holding one InnerSpace record and ``sections``."""
    return facade_data(innerspace={"devices": {"rec-1": record}}, **sections)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        pytest.param(
            with_network_device(
                _innerspace_snapshot(_NETWORK_MATCH), "site-1", "net-1", {"id": "net-1"}
            ),
            "site-1_net-1",
            id="network-match",
        ),
        pytest.param(
            _innerspace_snapshot(_NETWORK_MATCH), "innerspace_rec-1", id="network-gone"
        ),
        pytest.param(
            _innerspace_snapshot(_NETWORK_MATCH, devices={"site-1": {"net-1": "bad"}}),
            "innerspace_rec-1",
            id="network-record-malformed",
        ),
        pytest.param(
            _innerspace_snapshot(_NETWORK_MATCH, devices={"site-1": "bad"}),
            "innerspace_rec-1",
            id="network-site-malformed",
        ),
        pytest.param(
            with_camera(_innerspace_snapshot(_PROTECT_MATCH), "cam-1", {"id": "cam-1"}),
            "protect_camera_cam-1",
            id="protect-match",
        ),
        pytest.param(
            _innerspace_snapshot(_PROTECT_MATCH), "innerspace_rec-1", id="protect-gone"
        ),
        pytest.param(
            _innerspace_snapshot(_PROTECT_MATCH, protect={"cameras": {"cam-1": "bad"}}),
            "innerspace_rec-1",
            id="protect-record-malformed",
        ),
        pytest.param(
            _innerspace_snapshot(_PROTECT_MATCH, protect="bad"),
            "innerspace_rec-1",
            id="protect-section-malformed",
        ),
    ],
)
def test_innerspace_device_info_needs_the_matched_record(
    data: Any, expected: str
) -> None:
    """A match attaches only while the matched Network or Protect record exists."""
    coordinator = MagicMock()
    coordinator.data = data
    entity = UnifiInnerSpaceEntity(
        coordinator, INNERSPACE_PLACEMENT_DESCRIPTION, "rec-1"
    )

    assert entity.device_info["identifiers"] == {(DOMAIN, expected)}
