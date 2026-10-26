# Copyright 2026 UniFi Insights contributors
"""
The Protect platforms read the facade snapshot through its lookups (ADR-013).

A missing or malformed Protect section, collection or record reads as no
record: discovery adds no entity for it, and an entity reads it as it reads a
record the snapshot does not hold.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import DEFAULT, AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.unifi_insights import (
    alarm_control_panel,
    binary_sensor,
    button,
    camera,
    event,
    light,
    media_player,
    number,
    select,
    sensor,
    siren,
    switch,
)
from tests.fixtures.assertions import require_mock
from tests.fixtures.facade_data import (
    facade_data,
    with_protect_device,
    without_sections,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from homeassistant.core import HomeAssistant

    from custom_components.unifi_insights.coordinators.facade import FacadeData

DEVICE_ID = "dev1"
NVR = {
    "storageUsedBytes": 50_000_000_000,
    "storageTotalBytes": 100_000_000_000,
    "storage": {"healthy": True},
    "armMode": {"status": "disarmed"},
}

type Variant = Callable[[str], FacadeData]

# Snapshots that hold no usable record of a collection.
NO_RECORD: list[Any] = [
    pytest.param(lambda _: without_sections(facade_data(), "protect"), id="no-section"),
    pytest.param(lambda _: facade_data(protect=[]), id="malformed-section"),
    pytest.param(lambda _: facade_data(protect={}), id="no-collection"),
    pytest.param(lambda c: facade_data(protect={c: []}), id="malformed-collection"),
    pytest.param(
        lambda c: facade_data(protect={c: {DEVICE_ID: "invalid"}}),
        id="malformed-record",
    ),
]


def _snapshot(collection: str, record: dict[str, Any], **protect: Any) -> FacadeData:
    """Return a snapshot holding one Protect record."""
    return with_protect_device(
        facade_data(protect=protect), collection, DEVICE_ID, record
    )


def _coordinator(data: Any) -> MagicMock:
    """Return a coordinator double that publishes ``data`` as its snapshot."""
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.protect_client = None
    return coordinator


# --- Discovery ---------------------------------------------------------------


@dataclass(frozen=True)
class Discovery:
    """One platform discovery of one Protect collection."""

    module: ModuleType
    collection: str
    record: dict[str, Any]
    # The entity class a record gets, and other names patched out with it.
    entity_class: str
    patched: tuple[str, ...]


DISCOVERY: list[Any] = [
    pytest.param(
        Discovery(camera, "cameras", {"name": "Cam"}, "UnifiProtectCamera", ()),
        id="camera",
    ),
    pytest.param(
        Discovery(
            event,
            "cameras",
            {"name": "Cam", "smartDetectTypes": ["person"]},
            "UnifiProtectSmartDetectEventEntity",
            (
                "UnifiProtectDoorbellEventEntity",
                "UnifiProtectSensorEventEntity",
                "async_setup_access_events",
                "discover_network_rule_change_events",
                "resolve_console_namespace",
            ),
        ),
        id="event-camera",
    ),
    pytest.param(
        Discovery(
            event,
            "sensors",
            {"name": "Door"},
            "UnifiProtectSensorEventEntity",
            (
                "UnifiProtectDoorbellEventEntity",
                "UnifiProtectSmartDetectEventEntity",
                "async_setup_access_events",
                "discover_network_rule_change_events",
                "resolve_console_namespace",
            ),
        ),
        id="event-sensor",
    ),
    pytest.param(
        Discovery(light, "lights", {"id": DEVICE_ID}, "UnifiProtectLight", ()),
        id="light",
    ),
    pytest.param(
        Discovery(
            media_player,
            "cameras",
            {"hasSpeaker": True},
            "UnifiProtectCameraSpeaker",
            (),
        ),
        id="media-player",
    ),
    pytest.param(
        Discovery(
            number,
            "cameras",
            {},
            "UnifiProtectMicrophoneVolumeNumber",
            ("_async_setup_voucher_numbers",),
        ),
        id="number-camera",
    ),
    pytest.param(
        Discovery(
            number,
            "lights",
            {},
            "UnifiProtectLightLevelNumber",
            ("_async_setup_voucher_numbers",),
        ),
        id="number-light",
    ),
    pytest.param(
        Discovery(
            number,
            "chimes",
            {},
            "UnifiProtectChimeVolumeNumber",
            ("_async_setup_voucher_numbers", "UnifiProtectChimeRepeatTimesNumber"),
        ),
        id="number-chime",
    ),
    pytest.param(
        Discovery(
            select,
            "cameras",
            {"isPtz": True},
            "UnifiProtectPTZPresetSelect",
            ("UnifiProtectHDRModeSelect", "UnifiProtectVideoModeSelect"),
        ),
        id="select-camera",
    ),
    pytest.param(
        Discovery(
            select,
            "chimes",
            {},
            "UnifiProtectChimeRingtoneSelect",
            (),
        ),
        id="select-chime",
    ),
    pytest.param(
        Discovery(
            select,
            "viewers",
            {},
            "UnifiProtectViewerLiveviewSelect",
            (),
        ),
        id="select-viewer",
    ),
    pytest.param(Discovery(siren, "sirens", {}, "UnifiProtectSiren", ()), id="siren"),
    pytest.param(
        Discovery(
            alarm_control_panel,
            "nvrs",
            NVR,
            "UnifiProtectNvrAlarmControlPanel",
            (),
        ),
        id="alarm",
    ),
    pytest.param(
        Discovery(
            switch,
            "cameras",
            {"featureFlags": {"hasHighFpsCapability": True}},
            "UnifiProtectHighFPSSwitch",
            ("UnifiProtectStatusLightSwitch",),
        ),
        id="switch",
    ),
    pytest.param(
        Discovery(button, "chimes", {}, "UnifiProtectChimePlayButton", ()),
        id="button-chime",
    ),
    pytest.param(
        Discovery(
            button,
            "cameras",
            {"isPtz": True},
            "UnifiProtectPTZPatrolStartButton",
            ("UnifiProtectPTZPatrolStopButton",),
        ),
        id="button-camera",
    ),
    pytest.param(
        Discovery(
            binary_sensor,
            "cameras",
            {"name": "Cam"},
            "UnifiProtectBinarySensor",
            (),
        ),
        id="binary-sensor-camera",
    ),
    pytest.param(
        Discovery(
            binary_sensor,
            "sensors",
            {"name": "Door"},
            "UnifiProtectBinarySensor",
            (),
        ),
        id="binary-sensor-sensor",
    ),
    pytest.param(
        Discovery(
            binary_sensor,
            "nvrs",
            NVR,
            "UnifiProtectBinarySensor",
            (),
        ),
        id="binary-sensor-nvr",
    ),
]


def _built_ids(factory: MagicMock) -> set[Any]:
    """Return every device ID an entity class double was built for."""
    return {
        value
        for call in factory.call_args_list
        for value in (*call.args, *call.kwargs.values())
        if value == DEVICE_ID
    }


async def _discover(
    hass: HomeAssistant, case: Discovery, data: FacadeData
) -> dict[str, Any]:
    """Run the platform's discovery once and return the patched doubles."""
    entry = MagicMock()
    entry.runtime_data.coordinator.data = data
    # Background tasks (the button platform's wake history seed) are not run.
    entry.async_create_background_task.side_effect = lambda _hass, task, **_kwargs: (
        task.close()
    )
    names = dict.fromkeys((case.entity_class, *case.patched), DEFAULT)
    with ExitStack() as stack:
        doubles = {
            name: stack.enter_context(patch.object(case.module, name)) for name in names
        }
        if "discover_network_rule_change_events" in doubles:
            doubles["discover_network_rule_change_events"].return_value = []
        await case.module.async_setup_entry(hass, entry, MagicMock())
    return doubles


@pytest.mark.parametrize("case", DISCOVERY)
async def test_discovery_adds_an_entity_for_a_record(
    hass: HomeAssistant, case: Discovery
) -> None:
    """A Protect record in the snapshot gets its entity."""
    data = _snapshot(case.collection, case.record, global_alarm_manager=False)

    doubles = await _discover(hass, case, data)

    assert _built_ids(doubles[case.entity_class]) == {DEVICE_ID}


@pytest.mark.parametrize("variant", NO_RECORD)
@pytest.mark.parametrize("case", DISCOVERY)
async def test_discovery_skips_a_missing_or_malformed_record(
    hass: HomeAssistant, case: Discovery, variant: Variant
) -> None:
    """Without a usable record, discovery adds no Protect entity."""
    doubles = await _discover(hass, case, variant(case.collection))

    for name, double in doubles.items():
        if name.startswith("UnifiProtect"):
            double.assert_not_called()


async def test_light_discovery_skips_a_light_without_name_or_id(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A light record with neither a name nor an ID is skipped with a warning."""
    case = Discovery(light, "lights", {}, "UnifiProtectLight", ())

    doubles = await _discover(hass, case, _snapshot("lights", {"state": "CONNECTED"}))

    doubles["UnifiProtectLight"].assert_not_called()
    assert f"Skipping malformed light entry: {DEVICE_ID}" in caplog.text


@pytest.mark.parametrize(
    ("collection", "record", "description"),
    [
        pytest.param(
            "sensors", {"temperature": 21}, sensor.PROTECT_SENSOR_TYPES, id="sensor"
        ),
        pytest.param("nvrs", NVR, sensor.NVR_SENSOR_TYPES, id="nvr"),
    ],
)
def test_sensor_discovery_reads_protect_collections(
    collection: str, record: dict[str, Any], description: Any
) -> None:
    """Protect sensors are discovered for a record, never for a malformed one."""
    found: list[Any] = []
    with patch.object(sensor, "UnifiProtectSensor") as factory:
        coordinator = _coordinator(_snapshot(collection, record))
        coordinator.protect_client = object()
        sensor._discover_protect_sensors(coordinator, set(), found)
        assert _built_ids(factory) == {DEVICE_ID}
        assert all(
            call.kwargs["description"] in description for call in factory.call_args_list
        )

        for param in NO_RECORD:
            (variant,) = param.values
            factory.reset_mock()
            coordinator.data = variant(collection)
            sensor._discover_protect_sensors(coordinator, set(), found)
            factory.assert_not_called()


# --- Entity reads ------------------------------------------------------------

# (entity class, collection, record, the read that refreshes it)
ENTITIES: list[Any] = [
    pytest.param(
        camera.UnifiProtectCamera,
        "cameras",
        {"name": "Cam", "state": "CONNECTED"},
        "_update_from_data",
        id="camera",
    ),
    pytest.param(
        light.UnifiProtectLight,
        "lights",
        {
            "name": "Porch",
            "lightModeSettings": {"mode": "always"},
            "lightDeviceSettings": {"ledLevel": 3},
        },
        "_update_from_data",
        id="light",
    ),
    pytest.param(
        number.UnifiProtectMicrophoneVolumeNumber,
        "cameras",
        {"micVolume": 50, "isMicEnabled": True},
        "_update_from_data",
        id="number-mic-volume",
    ),
    pytest.param(
        number.UnifiProtectLightLevelNumber,
        "lights",
        {"lightDeviceSettings": {"ledLevel": 3}},
        "_update_from_data",
        id="number-light-level",
    ),
    pytest.param(
        number.UnifiProtectChimeVolumeNumber,
        "chimes",
        {"ringSettings": [{"volume": 40, "repeatTimes": 2}]},
        "_update_from_data",
        id="number-chime-volume",
    ),
    pytest.param(
        number.UnifiProtectChimeRepeatTimesNumber,
        "chimes",
        {"ringSettings": [{"volume": 40, "repeatTimes": 2}]},
        "_update_from_data",
        id="number-chime-repeat",
    ),
    pytest.param(
        select.UnifiProtectHDRModeSelect,
        "cameras",
        {"name": "Cam", "hdrType": "on"},
        "_update_from_data",
        id="select-hdr",
    ),
    pytest.param(
        select.UnifiProtectVideoModeSelect,
        "cameras",
        {"name": "Cam", "videoMode": "highFps"},
        "_update_from_data",
        id="select-video-mode",
    ),
    pytest.param(
        select.UnifiProtectPTZPresetSelect,
        "cameras",
        {"name": "Cam", "currentPtzPreset": 2},
        "_update_from_data",
        id="select-ptz",
    ),
    pytest.param(
        select.UnifiProtectChimeRingtoneSelect,
        "chimes",
        {"name": "Chime", "ringSettings": [{"ringtoneId": "r1"}]},
        "_update_from_data",
        id="select-ringtone",
    ),
    pytest.param(
        select.UnifiProtectViewerLiveviewSelect,
        "viewers",
        {"name": "Viewer", "liveview": "lv1"},
        "_update_from_data",
        id="select-liveview",
    ),
    pytest.param(
        switch.UnifiProtectStatusLightSwitch,
        "cameras",
        {"name": "Cam", "ledSettings": {"isEnabled": False}},
        "_update_from_data",
        id="switch-status-light",
    ),
    pytest.param(
        switch.UnifiProtectHighFPSSwitch,
        "cameras",
        {"name": "Cam", "videoMode": "highFps"},
        "_update_from_data",
        id="switch-high-fps",
    ),
    pytest.param(
        button.UnifiProtectChimePlayButton,
        "chimes",
        {"name": "Chime", "ringSettings": [{"ringtoneId": "r1"}]},
        "_update_attributes",
        id="button-chime",
    ),
]


def _read(
    entity_class: Any, collection: str, record: dict[str, Any], refresh: str, data: Any
) -> dict[str, Any]:
    """Build the entity from ``record``, then read ``data`` and return its state."""
    coordinator = _coordinator(_snapshot(collection, record))
    entity = entity_class(coordinator, DEVICE_ID)
    coordinator.data = data
    getattr(entity, refresh)()
    return {key: value for key, value in vars(entity).items() if "_attr_" in key}


@pytest.mark.parametrize("variant", NO_RECORD)
@pytest.mark.parametrize(("entity_class", "collection", "record", "refresh"), ENTITIES)
def test_entity_reads_a_missing_or_malformed_record_as_no_record(
    entity_class: Any,
    collection: str,
    record: dict[str, Any],
    refresh: str,
    variant: Variant,
) -> None:
    """A missing or malformed section, collection or record reads as no record."""
    no_record = facade_data(protect={collection: {}})
    expected = _read(entity_class, collection, record, refresh, no_record)

    assert _read(entity_class, collection, record, refresh, variant(collection)) == (
        expected
    )
    # The record itself reads differently, so the comparison means something.
    assert (
        _read(entity_class, collection, record, refresh, _snapshot(collection, record))
        != expected
    )


@pytest.mark.parametrize(
    ("liveviews", "options"),
    [
        pytest.param([], [], id="malformed-collection"),
        pytest.param(
            {"lv1": {"name": "Main"}, "lv2": "invalid"}, ["Main"], id="malformed-record"
        ),
    ],
)
def test_liveview_select_skips_malformed_liveviews(
    liveviews: Any, options: list[str]
) -> None:
    """Only liveview records are offered; a malformed one is not the current one."""
    data = _snapshot("viewers", {"liveview": "lv2"}, liveviews=liveviews)
    entity = select.UnifiProtectViewerLiveviewSelect(_coordinator(data), DEVICE_ID)

    assert entity.options == options
    assert entity.current_option is None


@pytest.mark.parametrize("variant", NO_RECORD)
async def test_select_options_need_their_protect_collection(variant: Variant) -> None:
    """Without the chimes or liveviews, no ringtone or liveview can be selected."""
    chime = select.UnifiProtectChimeRingtoneSelect(
        _coordinator(_snapshot("chimes", {"ringSettings": [{"ringtoneId": "r1"}]})),
        DEVICE_ID,
    )
    viewer = select.UnifiProtectViewerLiveviewSelect(
        _coordinator(_snapshot("viewers", {}, liveviews={"lv1": {"name": "Main"}})),
        DEVICE_ID,
    )
    chime.coordinator.data = variant("chimes")
    viewer.coordinator.data = variant("liveviews")

    with pytest.raises(ServiceValidationError) as ringtone_err:
        await chime.async_select_option("r1")
    with pytest.raises(ServiceValidationError) as liveview_err:
        await viewer.async_select_option("Main")

    assert ringtone_err.value.translation_key == "ringtone_not_found"
    assert liveview_err.value.translation_key == "liveview_not_found"
    require_mock(chime.coordinator.require_protect_coordinator).assert_not_called()
    require_mock(viewer.coordinator.require_protect_coordinator).assert_not_called()


@pytest.mark.parametrize(
    ("data", "host"),
    [
        pytest.param(
            _snapshot("nvrs", {"host": "10.0.0.1"}), "10.0.0.1", id="nvr-host"
        ),
        *[
            pytest.param(param.values[0]("nvrs"), "10.0.0.9", id=param.id)
            for param in NO_RECORD
        ],
    ],
)
async def test_camera_stream_falls_back_without_an_nvr_record(
    data: FacadeData, host: str
) -> None:
    """The static stream URL uses an NVR's host, else the configured host."""
    coordinator = _coordinator(_snapshot("cameras", {"name": "Cam"}))
    entity = camera.UnifiProtectCamera(coordinator, DEVICE_ID)
    coordinator.data = data
    coordinator.protect_client = MagicMock(base_url="https://10.0.0.9:443")
    protect = coordinator.require_protect_coordinator.return_value
    protect.async_create_camera_stream_url = AsyncMock(return_value=None)

    assert await entity.stream_source() == (
        f"rtsps://{host}:7441/{DEVICE_ID}?enableSrtp"
    )


# --- Event entities ----------------------------------------------------------

EVENTS: list[Any] = [
    pytest.param(
        event.UnifiProtectDoorbellEventEntity,
        "cameras",
        {"name": "Front", "type": "UVC G4 Doorbell", "state": "CONNECTED"},
        {"lastRingStart": 1},
        id="doorbell",
    ),
    pytest.param(
        event.UnifiProtectSmartDetectEventEntity,
        "cameras",
        {"name": "Yard", "type": "UVC G4 Bullet", "state": "CONNECTED"},
        {"lastMotionStart": 1, "lastSmartDetectTypes": ["person"]},
        id="smart-detect",
    ),
    pytest.param(
        event.UnifiProtectSensorEventEntity,
        "sensors",
        {"name": "Door", "type": "UFP-SENSE", "state": "CONNECTED"},
        {"openStatusChangedAt": 1, "isOpened": True},
        id="sensor",
    ),
]


@pytest.mark.parametrize("variant", NO_RECORD)
@pytest.mark.parametrize(("entity_class", "collection", "record", "change"), EVENTS)
def test_event_entity_reads_a_missing_or_malformed_record_as_no_record(
    entity_class: Any,
    collection: str,
    record: dict[str, Any],
    change: dict[str, Any],
    variant: Variant,
) -> None:
    """Without a usable record an event entity is unnamed, unavailable and silent."""
    unnamed = entity_class(_coordinator(facade_data()), DEVICE_ID)
    entity = entity_class(_coordinator(variant(collection)), DEVICE_ID)
    assert entity.device_info == unnamed.device_info

    coordinator = _coordinator(_snapshot(collection, record))
    entity = entity_class(coordinator, DEVICE_ID)
    assert entity.device_info != unnamed.device_info
    assert entity.available is True

    coordinator.data = variant(collection)
    with (
        patch.object(entity, "async_write_ha_state"),
        patch.object(entity, "_trigger_event") as trigger,
    ):
        entity._handle_coordinator_update()
        trigger.assert_not_called()

        # The same change on a usable record fires the event.
        coordinator.data = _snapshot(collection, {**record, **change})
        entity._handle_coordinator_update()
        trigger.assert_called_once()

    coordinator.data = variant(collection)
    assert entity.available is False
