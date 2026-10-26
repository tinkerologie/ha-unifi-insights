# Copyright 2026 UniFi Insights contributors
"""Tests for the set_chime_paired_doorbells action.

Most of these set the integration up through real config entries and call the
real service, so a call travels through the schema, the routing and validation
in services.py, the real facade and the Protect coordinator, and only the
Protect API client is mocked. They assert the mocked ``chimes.update`` is
awaited with exact arguments. A MagicMock coordinator would accept any method
name and hide a facade method that does not exist.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
import yaml
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_VERIFY_SSL
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api import UniFiResponseError
from unifi_official_api.protect.endpoints.chimes import (
    ChimesEndpoint,
)
from unifi_official_api.protect.models import Camera, Chime

from custom_components.unifi_insights.const import (
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_LOCAL,
    DOMAIN,
    SERVICE_SET_CHIME_PAIRED_DOORBELLS,
)
from custom_components.unifi_insights.data_transforms import is_doorbell_camera_model
from custom_components.unifi_insights.service_targets import _doorbell_camera_id
from custom_components.unifi_insights.services import (
    async_setup_services,
    async_unload_services,
)
from tests.conftest import _create_mock_protect_client
from tests.fixtures.assertions import require_not_none
from tests.fixtures.facade_data import with_protect_collection, without_sections
from tests.fixtures.library_responses import SAMPLE_DOORBELL_CAMERA, SAMPLE_PUBLIC_CHIME

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / DOMAIN
RING_SUFFIX = "camera_doorbell_ring"
ERROR_KEYS = (
    "chime_doorbell_not_found",
    "chime_doorbell_not_doorbell",
    "chime_doorbell_other_console",
    "chime_doorbells_none_selected",
)


@pytest.fixture
def protect_client(
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    mock_local_auth: MagicMock,
    enable_custom_integrations: None,
) -> MagicMock:
    """Patch every client the integration builds and return the Protect one."""
    return mock_protect_client


def _camera(camera_id: str = "doorbell_cam_1", **overrides: Any) -> dict[str, Any]:
    """Return a doorbell camera as the Integration API reports it."""
    record = copy.deepcopy(SAMPLE_DOORBELL_CAMERA)
    record["id"] = camera_id
    record.update(overrides)
    return record


def _prepare(
    client: MagicMock,
    *,
    cameras: list[dict[str, Any]],
    chimes: list[dict[str, Any]],
) -> None:
    """Give a mocked Protect client its cameras and chimes, and an update call."""
    client.cameras.get_all = AsyncMock(
        return_value=[Camera.model_validate(camera) for camera in cameras]
    )
    client.chimes.get_all = AsyncMock(
        return_value=[Chime.model_validate(chime) for chime in chimes]
    )
    client.chimes.update = AsyncMock(
        return_value=Chime.model_validate(SAMPLE_PUBLIC_CHIME)
    )


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    client: MagicMock,
    *,
    cameras: list[dict[str, Any]] | None = None,
    chimes: list[dict[str, Any]] | None = None,
) -> None:
    """Set the integration up with a doorbell camera and a chime by default."""
    _prepare(
        client,
        cameras=[_camera()] if cameras is None else cameras,
        chimes=[copy.deepcopy(SAMPLE_PUBLIC_CHIME)] if chimes is None else chimes,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant, camera_id: str, key: str) -> str:
    """Look a camera binary sensor up by unique_id, never by a guessed entity_id."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{DOMAIN}_camera_{camera_id}_{key}"
    )
    assert entity_id is not None, f"no {key} sensor for camera {camera_id}"
    return entity_id


def _ring(hass: HomeAssistant, camera_id: str = "doorbell_cam_1") -> str:
    return _entity_id(hass, camera_id, RING_SUFFIX)


def _motion(hass: HomeAssistant, camera_id: str = "doorbell_cam_1") -> str:
    return _entity_id(hass, camera_id, "camera_motion")


async def _call(hass: HomeAssistant, **data: Any) -> None:
    """Call the real action."""
    await hass.services.async_call(
        DOMAIN, SERVICE_SET_CHIME_PAIRED_DOORBELLS, data, blocking=True
    )


def _updated(client: MagicMock, camera_ids: list[str]) -> None:
    """Assert the one PATCH that was sent, with its exact arguments."""
    client.chimes.update.assert_awaited_once_with("chime_1", cameraIds=camera_ids)


def _error_key(err: pytest.ExceptionInfo[ServiceValidationError]) -> str | None:
    return err.value.translation_key


def test_doorbell_camera_id_reads_only_this_integrations_ring_sensors() -> None:
    """The unique_id is the only doorbell marker the integration has."""

    def entry(unique_id: str, *, platform: str = DOMAIN, domain: str = "binary_sensor"):
        return MagicMock(unique_id=unique_id, platform=platform, domain=domain)

    ring = f"{DOMAIN}_camera_doorbell_cam_1_{RING_SUFFIX}"
    assert _doorbell_camera_id(entry(ring)) == "doorbell_cam_1"
    assert _doorbell_camera_id(entry(ring, platform="other")) is None
    assert _doorbell_camera_id(entry(ring, domain="sensor")) is None
    assert (
        _doorbell_camera_id(entry(f"{DOMAIN}_camera_doorbell_cam_1_camera_motion"))
        is None
    )
    assert (
        _doorbell_camera_id(entry(f"{DOMAIN}_light_doorbell_cam_1_{RING_SUFFIX}"))
        is None
    )
    # Nothing between the prefix and the suffix means no camera id.
    assert _doorbell_camera_id(entry(f"{DOMAIN}_camera_{RING_SUFFIX}")) is None


async def test_pairs_the_selected_doorbell_with_exact_arguments(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """The action reaches the endpoint with the camera id and nothing else."""
    await _setup(hass, mock_config_entry, protect_client)

    await _call(hass, chime_id="chime_1", doorbells={"entity_id": [_ring(hass)]})

    _updated(protect_client, ["doorbell_cam_1"])


async def test_request_body_is_exactly_camera_ids(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """Through the real endpoint the PATCH body has one key: the spec forbids more."""
    await _setup(hass, mock_config_entry, protect_client)
    http = MagicMock()
    http.build_api_path = lambda path, site_id=None: f"/proxy/protect/v1{path}"
    http._patch = AsyncMock(return_value=copy.deepcopy(SAMPLE_PUBLIC_CHIME))
    protect_client.chimes = ChimesEndpoint(http)

    await _call(hass, chime_id="chime_1", doorbells={"entity_id": _ring(hass)})

    http._patch.assert_awaited_once_with(
        "/proxy/protect/v1/chimes/chime_1", json_data={"cameraIds": ["doorbell_cam_1"]}
    )


async def test_ids_are_sorted_and_deduplicated(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """Two doorbells, one of them also reached through its device, pair once each."""
    await _setup(
        hass,
        mock_config_entry,
        protect_client,
        cameras=[_camera("doorbell_cam_b", mac="AABBCC0000D2"), _camera()],
    )
    ring_a = _ring(hass)
    ring_b = _ring(hass, "doorbell_cam_b")
    device_b = require_not_none(er.async_get(hass).async_get(ring_b)).device_id

    await _call(
        hass,
        chime_id="chime_1",
        doorbells={"entity_id": [ring_b, ring_a], "device_id": [device_b]},
    )

    _updated(protect_client, ["doorbell_cam_1", "doorbell_cam_b"])


async def test_doorbell_device_expands_to_its_ring_sensor(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A doorbell's device pairs it. Its other entities are skipped quietly."""
    await _setup(hass, mock_config_entry, protect_client)
    registry = er.async_get(hass)
    device_id = require_not_none(registry.async_get(_ring(hass))).device_id
    siblings = [
        entry
        for entry in er.async_entries_for_device(registry, require_not_none(device_id))
        if entry.entity_id != _ring(hass)
    ]
    assert siblings, "the device must hold entities other than the ring sensor"

    await _call(hass, chime_id="chime_1", doorbells={"device_id": device_id})

    _updated(protect_client, ["doorbell_cam_1"])


async def test_area_expands_and_skips_what_is_not_a_doorbell(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """An area holding a ring sensor and a motion sensor pairs the doorbell only."""
    await _setup(hass, mock_config_entry, protect_client)
    area = ar.async_get(hass).async_create("Porch")
    registry = er.async_get(hass)
    registry.async_update_entity(_ring(hass), area_id=area.id)
    registry.async_update_entity(_motion(hass), area_id=area.id)

    await _call(hass, chime_id="chime_1", doorbells={"area_id": area.id})

    _updated(protect_client, ["doorbell_cam_1"])


@pytest.mark.parametrize(
    "doorbells", [None, {}, {"entity_id": []}, {"entity_id": "none"}]
)
async def test_empty_selection_unpairs_every_doorbell(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    doorbells: Any,
) -> None:
    """An empty selection is core's "unpair all": the camera list is emptied."""
    await _setup(hass, mock_config_entry, protect_client)

    await _call(hass, chime_id="chime_1", doorbells=doorbells)

    _updated(protect_client, [])


async def test_absent_doorbells_field_unpairs_every_doorbell(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """Leaving the field out is the same as leaving it empty."""
    await _setup(hass, mock_config_entry, protect_client)

    await _call(hass, chime_id="chime_1")

    _updated(protect_client, [])


async def test_selection_without_a_doorbell_raises_and_changes_nothing(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A non-empty selection that resolves to no doorbell never unpairs anything."""
    await _setup(hass, mock_config_entry, protect_client)
    area = ar.async_get(hass).async_create("Driveway")
    er.async_get(hass).async_update_entity(_motion(hass), area_id=area.id)

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, chime_id="chime_1", doorbells={"area_id": area.id})

    assert _error_key(err) == "chime_doorbells_none_selected"
    assert err.value.translation_domain == DOMAIN
    protect_client.chimes.update.assert_not_awaited()


async def test_directly_named_non_doorbell_entity_raises(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A motion sensor named on purpose is the user's mistake, not noise."""
    await _setup(hass, mock_config_entry, protect_client)
    motion = _motion(hass)

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, chime_id="chime_1", doorbells={"entity_id": [motion]})

    assert _error_key(err) == "chime_doorbell_not_doorbell"
    assert err.value.translation_placeholders == {"target": motion}
    protect_client.chimes.update.assert_not_awaited()


async def test_directly_named_entity_of_another_integration_raises(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A ring sensor lookalike from another integration is not a doorbell here."""
    await _setup(hass, mock_config_entry, protect_client)
    foreign = er.async_get(hass).async_get_or_create(
        "binary_sensor",
        "other",
        f"{DOMAIN}_camera_doorbell_cam_1_{RING_SUFFIX}",
        suggested_object_id="foreign_ring",
    )

    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass, chime_id="chime_1", doorbells={"entity_id": foreign.entity_id}
        )

    assert _error_key(err) == "chime_doorbell_not_doorbell"
    protect_client.chimes.update.assert_not_awaited()


async def test_unknown_doorbell_entity_raises(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """An entity id the registry does not know is reported as not found."""
    await _setup(hass, mock_config_entry, protect_client)

    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass, chime_id="chime_1", doorbells={"entity_id": "binary_sensor.nope"}
        )

    assert _error_key(err) == "chime_doorbell_not_found"
    assert err.value.translation_placeholders == {"target": "binary_sensor.nope"}
    protect_client.chimes.update.assert_not_awaited()


@pytest.mark.parametrize("field", ["device_id", "area_id", "floor_id", "label_id"])
async def test_unknown_device_area_floor_or_label_raises(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    field: str,
) -> None:
    """A selection naming something Home Assistant does not have is not found."""
    await _setup(hass, mock_config_entry, protect_client)

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, chime_id="chime_1", doorbells={field: "nope"})

    assert _error_key(err) == "chime_doorbell_not_found"
    assert err.value.translation_placeholders == {"target": "nope"}
    protect_client.chimes.update.assert_not_awaited()


async def test_doorbell_of_a_removed_camera_raises(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A ring sensor left behind by a camera the console dropped is not pairable."""
    await _setup(hass, mock_config_entry, protect_client)
    ring = _ring(hass)
    cameras = mock_config_entry.runtime_data.protect_coordinator.data["cameras"]
    del cameras["doorbell_cam_1"]

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, chime_id="chime_1", doorbells={"entity_id": ring})

    assert _error_key(err) == "chime_doorbell_not_found"
    assert err.value.translation_placeholders == {"target": ring}
    protect_client.chimes.update.assert_not_awaited()


@pytest.mark.parametrize("unloaded", ["data", "protect", "cameras"])
async def test_console_without_loaded_cameras_raises_translated_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    unloaded: str,
) -> None:
    """A console still loading its Protect data gets a translated error, not a crash.

    Routing keeps such a console as a candidate for the chime, so the cached
    cameras can be missing at any level when the doorbells are resolved.
    """
    await _setup(hass, mock_config_entry, protect_client)
    ring = _ring(hass)
    coordinator = mock_config_entry.runtime_data.coordinator
    if unloaded == "data":
        coordinator.data = None
    elif unloaded == "protect":
        coordinator.data = without_sections(coordinator.data, "protect")
    else:
        coordinator.data = with_protect_collection(coordinator.data, "cameras", None)

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, chime_id="chime_1", doorbells={"entity_id": ring})

    assert not isinstance(err.value, ServiceValidationError)
    assert err.value.translation_key == "chime_cameras_unavailable"
    protect_client.chimes.update.assert_not_awaited()


async def test_missing_chime_raises(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A chime no console has is refused before any doorbell is looked at."""
    await _setup(hass, mock_config_entry, protect_client)

    with pytest.raises(ServiceValidationError) as raised:
        await _call(
            hass, chime_id="no_such_chime", doorbells={"entity_id": _ring(hass)}
        )
    assert raised.value.translation_key == "protect_resource_not_found"

    protect_client.chimes.update.assert_not_awaited()


async def test_chime_target_is_required(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """Without a chime id or a target there is nothing to pair."""
    await _setup(hass, mock_config_entry, protect_client)

    with pytest.raises(ServiceValidationError) as raised:
        await _call(hass, doorbells={"entity_id": _ring(hass)})
    assert raised.value.translation_key == "protect_target_required"

    protect_client.chimes.update.assert_not_awaited()


async def test_chime_can_be_picked_by_device_like_core(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """The chime's device routes to its chime id, as in Home Assistant's action."""
    await _setup(hass, mock_config_entry, protect_client)
    registry = er.async_get(hass)
    chime_button = registry.async_get_entity_id(
        "button", DOMAIN, f"{DOMAIN}_chime_chime_1_play"
    )
    assert chime_button is not None
    device_id = require_not_none(registry.async_get(chime_button)).device_id
    device = dr.async_get(hass).async_get(require_not_none(device_id))
    assert device is not None
    assert device.identifiers == {(DOMAIN, "protect_chime_chime_1")}

    await _call(hass, device_id=device_id, doorbells={"entity_id": _ring(hass)})

    _updated(protect_client, ["doorbell_cam_1"])


async def test_chime_can_be_picked_as_the_action_target(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """One of the chime's own entities works as the action target."""
    await _setup(hass, mock_config_entry, protect_client)
    chime_button = er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{DOMAIN}_chime_chime_1_play"
    )
    assert chime_button is not None

    await _call(hass, entity_id=chime_button, doorbells={"entity_id": _ring(hass)})

    _updated(protect_client, ["doorbell_cam_1"])


async def test_api_error_raises_home_assistant_error_and_skips_the_refresh(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A rejected PATCH is a HomeAssistantError, and nothing is refreshed."""
    await _setup(hass, mock_config_entry, protect_client)
    protect_client.chimes.update = AsyncMock(side_effect=UniFiResponseError("x", 400))
    protect = mock_config_entry.runtime_data.protect_coordinator

    with (
        patch.object(protect, "async_request_refresh", AsyncMock()) as refresh,
        pytest.raises(HomeAssistantError, match="Unable to set the paired doorbells"),
    ):
        await _call(hass, chime_id="chime_1", doorbells={"entity_id": _ring(hass)})

    refresh.assert_not_awaited()


async def test_refresh_is_requested_after_a_successful_patch(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """The cached chimes are refreshed once the pairing is made."""
    await _setup(hass, mock_config_entry, protect_client)
    protect = mock_config_entry.runtime_data.protect_coordinator

    with patch.object(protect, "async_request_refresh", AsyncMock()) as refresh:
        await _call(hass, chime_id="chime_1", doorbells={"entity_id": _ring(hass)})

    _updated(protect_client, ["doorbell_cam_1"])
    refresh.assert_awaited_once_with()


async def test_failed_refresh_does_not_fail_the_action(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """The pairing is made; a refresh that fails is recorded, not raised.

    The real coordinator is used: its refresh catches the failure itself, so
    there is nothing for the action to guard against.
    """
    await _setup(hass, mock_config_entry, protect_client)
    protect = mock_config_entry.runtime_data.protect_coordinator

    with patch.object(
        protect, "_async_update_data", AsyncMock(side_effect=UpdateFailed("boom"))
    ) as poll:
        await _call(hass, chime_id="chime_1", doorbells={"entity_id": _ring(hass)})

    _updated(protect_client, ["doorbell_cam_1"])
    poll.assert_awaited_once()
    assert protect.last_update_success is False


@pytest.mark.parametrize(
    ("camera", "expected"),
    [
        ({"type": "UVC G4 Doorbell Pro"}, True),
        ({"type": "UP-Doorbell"}, True),
        ({"type": "UVC AI Doorbell"}, True),
        ({"_camera_type": "doorbell"}, True),
        ({"_camera_type": "doorbell_main"}, True),
        ({"type": "UVC G4 Pro", "name": "Front Door"}, False),
        ({"type": "UVC G4 Pro", "name": "Doorbell Cam"}, False),
        ({"type": None, "name": "Entrance"}, False),
        ({"type": None, "name": "Doorbell Cam"}, False),
        ({}, False),
    ],
    ids=[
        "g4-doorbell",
        "up-doorbell",
        "ai-doorbell",
        "camera-type-doorbell",
        "camera-type-main",
        "name-front-door",
        "name-says-doorbell",
        "no-type",
        "no-type-name-says-doorbell",
        "empty",
    ],
)
def test_doorbell_model_evidence_ignores_the_name(
    *, camera: dict[str, Any], expected: bool
) -> None:
    """The type says it is a doorbell. A name that looks like one does not."""
    assert is_doorbell_camera_model(camera) is expected


async def _setup_with_a_lookalike(
    hass: HomeAssistant, entry: MockConfigEntry, client: MagicMock
) -> str:
    """Set up a real doorbell and a camera that only has a doorbell-like name.

    The lookalike gets a ring sensor too, because the binary sensor platform
    also decides by name. Returns the lookalike's ring sensor.
    """
    await _setup(
        hass,
        entry,
        client,
        cameras=[
            _camera(),
            _camera(
                "front_door_cam",
                mac="AABBCC0000D3",
                name="Front Door Cam",
                type="UVC G4 Pro",
            ),
        ],
    )
    return _ring(hass, "front_door_cam")


async def test_lookalike_camera_named_directly_raises(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A ring sensor created only by the camera's name is not a doorbell."""
    lookalike = await _setup_with_a_lookalike(hass, mock_config_entry, protect_client)

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, chime_id="chime_1", doorbells={"entity_id": lookalike})

    assert _error_key(err) == "chime_doorbell_not_doorbell"
    assert err.value.translation_placeholders == {"target": lookalike}
    protect_client.chimes.update.assert_not_awaited()


async def test_lookalike_camera_reached_through_its_device_is_skipped(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """Picked only by device, it leaves the selection with no doorbell."""
    lookalike = await _setup_with_a_lookalike(hass, mock_config_entry, protect_client)
    device_id = require_not_none(er.async_get(hass).async_get(lookalike)).device_id

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, chime_id="chime_1", doorbells={"device_id": device_id})

    assert _error_key(err) == "chime_doorbells_none_selected"
    protect_client.chimes.update.assert_not_awaited()


async def test_area_with_a_real_doorbell_and_a_lookalike_pairs_only_the_doorbell(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """The lookalike is skipped quietly beside a real doorbell."""
    lookalike = await _setup_with_a_lookalike(hass, mock_config_entry, protect_client)
    area = ar.async_get(hass).async_create("Entrance")
    registry = er.async_get(hass)
    registry.async_update_entity(_ring(hass), area_id=area.id)
    registry.async_update_entity(lookalike, area_id=area.id)

    await _call(hass, chime_id="chime_1", doorbells={"area_id": area.id})

    _updated(protect_client, ["doorbell_cam_1"])


@pytest.fixture
async def two_consoles(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
) -> MockConfigEntry:
    """Set up console A (a chime and a doorbell) and console B (another doorbell)."""
    client_b = _create_mock_protect_client()
    client_b.base_url = "https://192.168.1.2"
    _prepare(
        client_b, cameras=[_camera("doorbell_cam_2", mac="AABBCC0000D2")], chimes=[]
    )
    _prepare(
        protect_client, cameras=[_camera()], chimes=[copy.deepcopy(SAMPLE_PUBLIC_CHIME)]
    )
    entry_b = MockConfigEntry(
        version=1,
        minor_version=0,
        domain=DOMAIN,
        title="UniFi Insights (second console)",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.2",
            CONF_API_KEY: "api_key_b",
            CONF_VERIFY_SSL: False,
        },
        options={},
        source="user",
        unique_id="api_key_b",
        entry_id="entry_b",
    )
    clients = {"https://192.168.1.1": protect_client, "https://192.168.1.2": client_b}

    with patch(
        "custom_components.unifi_insights.UniFiProtectClient",
        side_effect=lambda **kwargs: clients[kwargs["base_url"]],
    ):
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        entry_b.add_to_hass(hass)
        await hass.config_entries.async_setup(entry_b.entry_id)
        await hass.async_block_till_done()
    return entry_b


async def test_doorbell_on_another_console_raises(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    two_consoles: MockConfigEntry,
) -> None:
    """A doorbell of console B cannot ring a chime of console A."""
    assert two_consoles.runtime_data is not mock_config_entry.runtime_data
    other_ring = _ring(hass, "doorbell_cam_2")

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, chime_id="chime_1", doorbells={"entity_id": [other_ring]})

    assert _error_key(err) == "chime_doorbell_other_console"
    assert err.value.translation_placeholders == {"target": other_ring}
    protect_client.chimes.update.assert_not_awaited()


async def test_a_selection_mixing_consoles_is_refused_whole(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    two_consoles: MockConfigEntry,
) -> None:
    """One foreign doorbell in an area refuses the whole pairing, visibly."""
    area = ar.async_get(hass).async_create("Whole house")
    registry = er.async_get(hass)
    registry.async_update_entity(_ring(hass), area_id=area.id)
    registry.async_update_entity(_ring(hass, "doorbell_cam_2"), area_id=area.id)

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, chime_id="chime_1", doorbells={"area_id": area.id})

    assert _error_key(err) == "chime_doorbell_other_console"
    protect_client.chimes.update.assert_not_awaited()


async def test_same_console_still_pairs_with_two_consoles_set_up(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    two_consoles: MockConfigEntry,
) -> None:
    """The console check lets the chime's own doorbell through."""
    await _call(hass, chime_id="chime_1", doorbells={"entity_id": [_ring(hass)]})

    _updated(protect_client, ["doorbell_cam_1"])


async def test_unknown_field_in_doorbells_is_rejected_by_the_schema(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """Only target keys are accepted in the doorbells selection."""
    await _setup(hass, mock_config_entry, protect_client)

    with pytest.raises(vol.Invalid):
        await _call(hass, chime_id="chime_1", doorbells={"camera_ids": ["x"]})

    protect_client.chimes.update.assert_not_awaited()


async def test_setup_registers_and_unload_removes_the_action(
    hass: HomeAssistant,
) -> None:
    """The action is registered with the other services and removed with them."""
    await async_setup_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_CHIME_PAIRED_DOORBELLS)

    await async_unload_services(hass)
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_CHIME_PAIRED_DOORBELLS)


def test_services_yaml_defines_the_action_with_hassfest_safe_targets() -> None:
    """The chime is one entity or device; doorbells filter on ring sensors."""
    definitions = yaml.safe_load((COMPONENT_DIR / "services.yaml").read_text())
    service = definitions[SERVICE_SET_CHIME_PAIRED_DOORBELLS]

    # The chime is a single target, so no area-offering action target.
    assert "target" not in service
    assert set(service["fields"]) == {"entity_id", "device_id", "chime_id", "doorbells"}
    assert service["fields"]["entity_id"]["selector"] == {
        "entity": {"integration": DOMAIN}
    }
    assert service["fields"]["device_id"]["selector"] == {
        "device": {"integration": DOMAIN}
    }
    selector = service["fields"]["doorbells"]["selector"]["target"]
    assert selector == {
        "entity": {
            "integration": DOMAIN,
            "domain": "binary_sensor",
            "device_class": "occupancy",
        }
    }
    assert service["fields"]["doorbells"]["required"] is False


def test_icon_and_translations_cover_the_action_in_both_files() -> None:
    """strings.json and en.json agree, with no placeholder inside quotes."""
    icons = json.loads((COMPONENT_DIR / "icons.json").read_text(encoding="utf-8"))
    assert icons["services"][SERVICE_SET_CHIME_PAIRED_DOORBELLS] == "mdi:bell-ring"

    loaded = [
        json.loads((COMPONENT_DIR / path).read_text(encoding="utf-8"))
        for path in ("strings.json", "translations/en.json")
    ]
    assert (
        loaded[0]["services"][SERVICE_SET_CHIME_PAIRED_DOORBELLS]
        == loaded[1]["services"][SERVICE_SET_CHIME_PAIRED_DOORBELLS]
    )
    for key in ERROR_KEYS:
        messages = [data["exceptions"][key]["message"] for data in loaded]
        assert messages[0] == messages[1]
        assert "'{" not in messages[0]
    for key in ERROR_KEYS[:3]:
        assert "{target}" in loaded[0]["exceptions"][key]["message"]
