# Copyright 2026 UniFi Insights contributors
"""Tests for the UniFi Protect alarm control panel platform.

Most of these set the integration up through the real config entry and call the
real ``alarm_control_panel`` services, so a service call travels through the
entity, the real facade, the real Protect command owner and the real library
endpoint or its mock, and only the Protect API client is mocked.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.alarm_control_panel import AlarmControlPanelEntityFeature
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from unifi_official_api import (
    UniFiGlobalAlarmManagerError,
    UniFiNotFoundError,
    UniFiResponseError,
)
from unifi_official_api.protect.endpoints.arm_profiles import (
    ArmProfilesEndpoint,
)
from unifi_official_api.protect.models import NVR

from custom_components.unifi_insights import alarm_control_panel as panel_platform
from custom_components.unifi_insights.alarm_control_panel import (
    ARM_MODE_TO_STATE,
    PARALLEL_UPDATES,
    UnifiProtectNvrAlarmControlPanel,
    _arm_mode,
    _epoch_ms_to_iso,
    async_setup_entry,
)
from custom_components.unifi_insights.const import DOMAIN
from tests.fixtures.assertions import require_instance, require_not_none
from tests.fixtures.facade_data import (
    facade_data,
    set_availability,
    with_protect_device,
)
from tests.fixtures.library_responses import SAMPLE_NVR, SAMPLE_NVR_WITH_ARM_MODE
from tests.fixtures.protect_ws import FakeProtectWebSocket

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / DOMAIN
UNIQUE_ID = f"{DOMAIN}_nvr_nvr_1_alarm"
# 2025-08-23T02:41:48.659Z, the armedAt the live console reports.
ARMED_AT_MS = 1755916908659
ARMED_AT_ISO = "2025-08-23T02:41:48.659000+00:00"
GLOBAL_ALARM_MANAGER_BODY = (
    '{"name":"BAD_REQUEST","error":"This operation is not available when '
    'global alarm manager is enabled"}'
)


def _nvr(**arm_mode: Any) -> dict[str, Any]:
    """Return the live NVR capture, with ``arm_mode`` overriding armMode keys."""
    nvr = copy.deepcopy(SAMPLE_NVR_WITH_ARM_MODE)
    nvr["armMode"].update(arm_mode)
    return nvr


def _nvr_with_arm_mode(arm_mode: dict[str, Any]) -> dict[str, Any]:
    """Return the live NVR capture with armMode replaced outright."""
    return {**copy.deepcopy(SAMPLE_NVR_WITH_ARM_MODE), "armMode": arm_mode}


def _nvr_without_arm_mode() -> dict[str, Any]:
    """Return the NVR as firmware without the Alarm Manager reports it."""
    nvr = copy.deepcopy(SAMPLE_NVR_WITH_ARM_MODE)
    del nvr["armMode"]
    return nvr


@pytest.fixture
def protect_client(
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    mock_local_auth: MagicMock,
    enable_custom_integrations: None,
) -> MagicMock:
    """Patch every client the integration builds and return the Protect one."""
    mock_protect_client.websocket = FakeProtectWebSocket()
    return mock_protect_client


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    protect_client: MagicMock,
    *,
    nvr: dict[str, Any],
    gam_error: Exception | None = None,
) -> None:
    """Set the integration up with ``nvr`` as the Protect console's NVR.

    The global alarm manager is off unless ``gam_error`` is given, which is what
    listing the arm profiles then raises.
    """
    protect_client.nvr.get = AsyncMock(return_value=NVR.model_validate(nvr))
    protect_client.arm_profiles.get_all = (
        AsyncMock(side_effect=gam_error)
        if gam_error is not None
        else AsyncMock(return_value=[])
    )
    protect_client.arm_profiles.enable = AsyncMock(return_value=True)
    protect_client.arm_profiles.disable = AsyncMock(return_value=True)

    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant) -> str:
    """Look the panel up by unique_id, never by a guessed entity_id."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "alarm_control_panel", DOMAIN, UNIQUE_ID
    )
    assert entity_id is not None, "no alarm control panel entity"
    return entity_id


def _state(hass: HomeAssistant) -> str:
    state = hass.states.get(_entity_id(hass))
    assert state is not None
    return state.state


def _attributes(hass: HomeAssistant) -> dict[str, Any]:
    state = hass.states.get(_entity_id(hass))
    assert state is not None
    return dict(state.attributes)


async def _call(hass: HomeAssistant, service: str) -> None:
    """Call a real ``alarm_control_panel`` service on the panel."""
    await hass.services.async_call(
        "alarm_control_panel",
        service,
        {ATTR_ENTITY_ID: _entity_id(hass)},
        blocking=True,
    )


def _ws_frame(arm_mode: dict[str, Any], **item: Any) -> dict[str, Any]:
    """Build a devices-stream NVR frame in the confirmed {"type", "item"} envelope."""
    return {
        "type": "update",
        "item": {"id": "nvr_1", "modelKey": "nvr", "armMode": arm_mode, **item},
    }


async def _push(
    hass: HomeAssistant, entry: MockConfigEntry, frame: dict[str, Any]
) -> None:
    """Deliver a WebSocket frame through the Protect devices subscription."""
    feed = entry.runtime_data.protect_coordinator.protect_client.websocket
    await feed.wait_subscribed("devices")
    feed.raw("devices", frame)
    await hass.async_block_till_done()


def _unit_coordinator(nvr: dict[str, Any], **extra: Any) -> MagicMock:
    """Build a facade-coordinator mock for entities constructed without HA."""
    coordinator = MagicMock()
    coordinator.network_client.base_url = "https://192.168.1.1"
    coordinator.protect_client.base_url = "https://192.168.1.1"
    coordinator.data = {
        "devices": extra.get("devices", {}),
        "protect": {
            "nvrs": {"nvr_1": nvr},
            "global_alarm_manager": extra.get("global_alarm_manager", False),
        },
    }
    return coordinator


def test_parallel_updates_value() -> None:
    """Arming and disarming are actions, so updates may run one at a time."""
    assert PARALLEL_UPDATES == 1


def test_nvr_model_keeps_arm_mode() -> None:
    """The `unifi-official-api` NVR model types armMode and round-trips it by alias."""
    nvr = NVR.model_validate(SAMPLE_NVR_WITH_ARM_MODE)

    assert nvr.arm_mode is not None
    assert nvr.arm_mode["status"] == "disabled"
    assert (
        nvr.model_dump(by_alias=True)["armMode"] == SAMPLE_NVR_WITH_ARM_MODE["armMode"]
    )


def test_nvr_model_without_arm_mode_reads_none() -> None:
    """Firmware that predates the Alarm Manager has no armMode."""
    assert NVR.model_validate(SAMPLE_NVR).arm_mode is None


@pytest.mark.parametrize(
    "nvr",
    [None, "garbage", [], {}, {"armMode": None}, {"armMode": "garbage"}],
    ids=["none", "string", "list", "empty", "null-mode", "string-mode"],
)
def test_arm_mode_helper_rejects_anything_but_a_dict(nvr: Any) -> None:
    """Only an armMode dict counts as the NVR reporting an arm mode."""
    assert _arm_mode(nvr) is None


def test_arm_mode_helper_returns_the_dict() -> None:
    """An armMode dict comes back as it is, even an empty one."""
    assert _arm_mode({"armMode": {}}) == {}
    assert _arm_mode(SAMPLE_NVR_WITH_ARM_MODE) == SAMPLE_NVR_WITH_ARM_MODE["armMode"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (ARMED_AT_MS, ARMED_AT_ISO),
        (float(ARMED_AT_MS), ARMED_AT_ISO),
        (0, "1970-01-01T00:00:00+00:00"),
        (True, None),
        ("1755916908659", None),
        (None, None),
        (1e20, None),
        (float("nan"), None),
        (float("inf"), None),
    ],
    ids=["int", "float", "epoch", "bool", "string", "none", "absurd", "nan", "inf"],
)
def test_epoch_ms_to_iso(value: Any, expected: str | None) -> None:
    """A millisecond epoch becomes ISO UTC; nothing else raises or passes."""
    assert _epoch_ms_to_iso(value) == expected


def test_state_mapping_covers_exactly_the_spec_enum() -> None:
    """The spec enum is arming/armed/breach/disabled; nothing else is mapped."""
    assert set(ARM_MODE_TO_STATE) == {"arming", "armed", "breach", "disabled"}


async def test_setup_without_protect_client_adds_nothing(hass: HomeAssistant) -> None:
    """A console with no Protect app creates no alarm control panel."""
    entry = MagicMock()
    entry.runtime_data.coordinator.protect_client = None
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    add_entities.assert_not_called()
    entry.async_on_unload.assert_not_called()


async def test_discovery_adds_each_panel_once(hass: HomeAssistant) -> None:
    """A panel is added when armMode first shows up and never twice."""
    entry = MagicMock()
    coordinator = entry.runtime_data.coordinator
    coordinator.data = facade_data(
        protect={
            "nvrs": {"nvr_old": {"id": "nvr_old"}},
            "global_alarm_manager": False,
        }
    )
    add_entities = MagicMock()
    built = MagicMock()

    with patch.object(
        panel_platform, "UnifiProtectNvrAlarmControlPanel", return_value=built
    ) as factory:
        await async_setup_entry(hass, entry, add_entities)
        add_entities.assert_not_called()

        # The NVR gains an armMode (a firmware upgrade) on a later update.
        coordinator.data = with_protect_device(
            coordinator.data, "nvrs", "nvr_old", SAMPLE_NVR_WITH_ARM_MODE
        )
        (listener,) = coordinator.async_add_listener.call_args.args
        listener()
        add_entities.assert_called_once_with([built])

        # Once added it is known, so a further update adds nothing.
        listener()
        add_entities.assert_called_once()
        factory.assert_called_once_with(coordinator, "nvr_old")


async def test_no_entity_when_nvr_has_no_arm_mode(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """Older firmware has no armMode, so there is no entity to show."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr_without_arm_mode())

    assert hass.states.async_entity_ids("alarm_control_panel") == []


async def test_entity_added_when_arm_mode_appears_later(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """An NVR that gains an armMode after setup gets its panel without a reload."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr_without_arm_mode())
    assert hass.states.async_entity_ids("alarm_control_panel") == []

    protect_client.nvr.get = AsyncMock(return_value=NVR.model_validate(_nvr()))
    await mock_config_entry.runtime_data.protect_coordinator.async_refresh()
    await hass.async_block_till_done()

    assert _state(hass) == "disarmed"
    assert len(hass.states.async_entity_ids("alarm_control_panel")) == 1


async def test_one_panel_per_nvr_unique_id_and_device(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """The panel shares the NVR's device with the NVR sensors."""
    # The public API reports no storage, so these sensors only exist when the
    # NVR record has some; give it some to have a sensor to compare against.
    nvr = _nvr()
    nvr["storageInfo"] = {"usedSize": 1_000_000_000, "totalSize": 4_000_000_000}
    await _setup(hass, mock_config_entry, protect_client, nvr=nvr)

    registry = er.async_get(hass)
    entity = registry.async_get(_entity_id(hass))
    assert entity is not None
    assert entity.unique_id == UNIQUE_ID
    assert entity.translation_key == "nvr_alarm"
    device = dr.async_get(hass).async_get(require_not_none(entity.device_id))
    assert device is not None
    assert device.identifiers == {(DOMAIN, "protect_nvr_nvr_1")}
    assert (dr.CONNECTION_NETWORK_MAC, dr.format_mac("AABBCC0000AA")) in (
        require_instance(device, dr.DeviceEntry).connections
    )
    nvr_sensors = [
        sensor
        for sensor in er.async_entries_for_config_entry(
            registry, mock_config_entry.entry_id
        )
        if sensor.domain == "sensor"
        and sensor.unique_id.startswith("unifi_insights_nvr_nvr_1_")
    ]
    assert nvr_sensors, "expected NVR sensors to compare the device against"
    assert {sensor.device_id for sensor in nvr_sensors} == {entity.device_id}
    attributes = _attributes(hass)
    assert attributes["friendly_name"] == "UniFi Protect Alarm"


def test_panel_joins_network_device_when_mac_matches() -> None:
    """An NVR that is also a Network device shows as that one device."""
    network_device = {
        "macAddress": SAMPLE_NVR_WITH_ARM_MODE["mac"],
        "name": "Dream Machine",
        "model": "UDM-Pro",
    }
    coordinator = _unit_coordinator(
        copy.deepcopy(SAMPLE_NVR_WITH_ARM_MODE),
        devices={"site1": {"dev1": network_device}},
    )

    panel = UnifiProtectNvrAlarmControlPanel(coordinator, "nvr_1")

    assert panel.device_info is not None
    assert panel.device_info["identifiers"] == {(DOMAIN, "site1_dev1")}


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("disabled", "disarmed"),
        ("arming", "arming"),
        ("armed", "armed_away"),
        ("breach", "triggered"),
        ("ARMED", "armed_away"),
        ("armed_night", STATE_UNKNOWN),
        ("", STATE_UNKNOWN),
        (None, STATE_UNKNOWN),
        (7, STATE_UNKNOWN),
    ],
    ids=[
        "disabled",
        "arming",
        "armed",
        "breach",
        "case-insensitive",
        "unrecognised",
        "empty",
        "null",
        "not-a-string",
    ],
)
async def test_state_mapping(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    status: Any,
    expected: str,
) -> None:
    """Every spec status maps; anything else is unknown, never disarmed."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr(status=status))

    assert _state(hass) == expected


async def test_missing_status_is_unknown(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """An armMode with no status at all reads unknown, not disarmed."""
    await _setup(
        hass,
        mock_config_entry,
        protect_client,
        nvr=_nvr_with_arm_mode({"armProfileId": "arm_profile_away"}),
    )

    assert _state(hass) == STATE_UNKNOWN


async def test_unrecognised_status_is_logged_once_per_value(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A status this integration does not know is reported once, at debug."""
    caplog.set_level(logging.DEBUG, logger=panel_platform.__name__)
    await _setup(
        hass, mock_config_entry, protect_client, nvr=_nvr(status="armed_night")
    )
    await _push(hass, mock_config_entry, _ws_frame({"status": "armed_night"}))
    await _push(hass, mock_config_entry, _ws_frame({"status": "armed_vacation"}))

    assert caplog.text.count("Unrecognised armMode status 'armed_night'") == 1
    assert caplog.text.count("Unrecognised armMode status 'armed_vacation'") == 1


async def test_attributes_profile_times_and_count(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """The profile, the arming and breach times (ISO UTC) and the count show."""
    await _setup(
        hass,
        mock_config_entry,
        protect_client,
        nvr=_nvr(
            status="breach",
            armedAt=ARMED_AT_MS,
            willBeArmedAt=ARMED_AT_MS + 1000,
            breachDetectedAt=ARMED_AT_MS + 2000,
            breachEventCount=3,
            breachEventId="opaque",
            breachTriggerEventId="opaque",
        ),
    )

    attributes = _attributes(hass)
    assert attributes["arm_profile_id"] == "arm_profile_away"
    assert attributes["armed_at"] == ARMED_AT_ISO
    assert attributes["will_be_armed_at"] == "2025-08-23T02:41:49.659000+00:00"
    assert attributes["breach_detected_at"] == "2025-08-23T02:41:50.659000+00:00"
    assert attributes["breach_event_count"] == 3
    # Opaque ids are left out.
    assert "opaque" not in str(attributes)


async def test_attributes_drop_none_values(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """Values Protect reports as null are left out, but a count of zero stays."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())

    attributes = _attributes(hass)
    assert attributes["arm_profile_id"] == "arm_profile_away"
    assert attributes["armed_at"] == ARMED_AT_ISO
    assert attributes["breach_event_count"] == 0
    assert "will_be_armed_at" not in attributes
    assert "breach_detected_at" not in attributes


async def test_attributes_drop_values_of_the_wrong_type(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A bool count, an absurd or NaN timestamp and a non-string id are dropped."""
    await _setup(
        hass,
        mock_config_entry,
        protect_client,
        nvr=_nvr(
            armProfileId=12,
            armedAt=1e20,
            willBeArmedAt=math.nan,
            breachDetectedAt=True,
            breachEventCount=True,
        ),
    )

    attributes = _attributes(hass)
    for key in (
        "arm_profile_id",
        "armed_at",
        "will_be_armed_at",
        "breach_detected_at",
        "breach_event_count",
    ):
        assert key not in attributes


async def test_supported_features_and_no_code(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """Arm away only, and no code is asked for."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())

    attributes = _attributes(hass)
    assert attributes["supported_features"] == int(
        AlarmControlPanelEntityFeature.ARM_AWAY
    )
    assert attributes["code_arm_required"] is False
    assert attributes["code_format"] is None


async def test_arm_away_enables_arm_profiles(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """The service reaches the real endpoint, then a refresh is requested."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())
    polls_before = protect_client.nvr.get.await_count

    await _call(hass, "alarm_arm_away")
    await hass.async_block_till_done()

    protect_client.arm_profiles.enable.assert_awaited_once_with()
    protect_client.arm_profiles.disable.assert_not_awaited()
    # No optimistic state: the NVR still reports disabled, so the panel does too.
    assert _state(hass) == "disarmed"
    # The refresh that follows is the fallback for the NVR's WebSocket frame.
    assert protect_client.nvr.get.await_count > polls_before


async def test_disarm_disables_arm_profiles(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """Disarm reaches the real endpoint and leaves the state to Protect."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr(status="armed"))
    assert _state(hass) == "armed_away"

    await _call(hass, "alarm_disarm")
    await hass.async_block_till_done()

    protect_client.arm_profiles.disable.assert_awaited_once_with()
    protect_client.arm_profiles.enable.assert_not_awaited()
    assert _state(hass) == "armed_away"


@pytest.mark.parametrize(
    ("service", "endpoint"),
    [("alarm_arm_away", "enable"), ("alarm_disarm", "disable")],
)
async def test_global_alarm_manager_refusal_raises_translated_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    service: str,
    endpoint: str,
) -> None:
    """The refusal reaches the user as a translated HomeAssistantError."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())
    setattr(
        protect_client.arm_profiles,
        endpoint,
        AsyncMock(side_effect=UniFiGlobalAlarmManagerError("x", 400, "body")),
    )
    polls_before = protect_client.nvr.get.await_count

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, service)

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "global_alarm_manager"
    # The refusal says the manager is on, so the panel is hidden from now on.
    assert _state(hass) == STATE_UNAVAILABLE
    # Nothing changed, so nothing is refreshed.
    assert protect_client.nvr.get.await_count == polls_before


@pytest.mark.parametrize(
    ("service", "action"),
    [("alarm_arm_away", "enable"), ("alarm_disarm", "disable")],
)
async def test_real_endpoint_turns_the_400_into_the_translated_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    service: str,
    action: str,
) -> None:
    """The live 400 body, through the real library endpoint and the facade."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())
    http = MagicMock()
    http.build_api_path = lambda path, site_id=None: f"/proxy/protect/v1{path}"
    http._post = AsyncMock(
        side_effect=UniFiResponseError(
            "API error (status 400)", 400, response_body=GLOBAL_ALARM_MANAGER_BODY
        )
    )
    protect_client.arm_profiles = ArmProfilesEndpoint(http)

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, service)

    assert err.value.translation_key == "global_alarm_manager"
    http._post.assert_awaited_once_with(
        f"/proxy/protect/v1/arm-profiles/{action}", json_data=None
    )


async def test_other_api_error_raises_home_assistant_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """Any other failure is a HomeAssistantError that names the action."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())
    protect_client.arm_profiles.enable = AsyncMock(
        side_effect=UniFiResponseError("x", 500)
    )

    with pytest.raises(HomeAssistantError, match="Unable to arm the UniFi Protect"):
        await _call(hass, "alarm_arm_away")


async def test_fetch_nvr_stores_arm_mode(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """The poll keeps armMode in the NVR record, under its API name."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())

    stored = mock_config_entry.runtime_data.protect_coordinator.data["nvrs"]["nvr_1"]
    assert stored["armMode"] == SAMPLE_NVR_WITH_ARM_MODE["armMode"]


async def test_state_follows_nvr_ws_frame_and_keeps_the_profile(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A status-only frame changes the state and keeps armProfileId beside it."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())
    assert _state(hass) == "disarmed"

    await _push(hass, mock_config_entry, _ws_frame({"status": "breach"}))

    assert _state(hass) == "triggered"
    assert _attributes(hass)["arm_profile_id"] == "arm_profile_away"
    stored = mock_config_entry.runtime_data.protect_coordinator.data["nvrs"]["nvr_1"]
    assert stored["armMode"]["armProfileId"] == "arm_profile_away"
    assert stored["armMode"]["breachEventCount"] == 0
    assert len(hass.states.async_entity_ids("alarm_control_panel")) == 1


async def test_nvr_ws_frame_without_arm_mode_keeps_cached_arm_mode(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A frame for some other NVR field must not wipe armMode."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr(status="armed"))

    await _push(
        hass,
        mock_config_entry,
        {
            "type": "update",
            "item": {"id": "nvr_1", "modelKey": "nvr", "name": "Renamed NVR"},
        },
    )

    assert _state(hass) == "armed_away"
    stored = mock_config_entry.runtime_data.protect_coordinator.data["nvrs"]["nvr_1"]
    assert stored["name"] == "Renamed NVR"
    assert stored["armMode"]["armProfileId"] == "arm_profile_away"


async def test_unavailable_when_arm_mode_disappears(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """The panel is never removed, it turns unavailable."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())
    assert _state(hass) == "disarmed"

    protect_client.nvr.get = AsyncMock(
        return_value=NVR.model_validate(_nvr_without_arm_mode())
    )
    await mock_config_entry.runtime_data.protect_coordinator.async_refresh()
    await hass.async_block_till_done()

    assert _state(hass) == STATE_UNAVAILABLE


def test_unavailable_when_protect_update_failed() -> None:
    """A Protect coordinator that cannot reach the NVR leaves no stale state."""
    coordinator = _unit_coordinator(copy.deepcopy(SAMPLE_NVR_WITH_ARM_MODE))
    panel = UnifiProtectNvrAlarmControlPanel(coordinator, "nvr_1")
    assert panel.available is True

    set_availability(coordinator, protect=False)

    assert panel.available is False


def test_unavailable_state_has_no_attributes() -> None:
    """Without an armMode there is nothing to report as attributes."""
    coordinator = _unit_coordinator({"id": "nvr_1"})
    panel = UnifiProtectNvrAlarmControlPanel(coordinator, "nvr_1")

    assert panel.available is False
    assert panel.extra_state_attributes is None
    assert panel.alarm_state is None


def test_alarm_translations_in_both_files() -> None:
    """strings.json and en.json agree on the entity name and the refusal text."""
    loaded = [
        json.loads((COMPONENT_DIR / path).read_text(encoding="utf-8"))
        for path in ("strings.json", "translations/en.json")
    ]

    names = [d["entity"]["alarm_control_panel"]["nvr_alarm"]["name"] for d in loaded]
    messages = [d["exceptions"]["global_alarm_manager"]["message"] for d in loaded]
    assert names == ["Alarm", "Alarm"]
    assert messages[0] == messages[1]
    assert "global alarm manager" in messages[0]
    assert "'{" not in messages[0]


async def test_reconcile_timer_picks_up_arming_turning_into_armed(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """Protect finishes arming on its own, and no WebSocket frame says so.

    The independent timer reads the NVR again without the main poll, which
    a busy console keeps pushing back.
    """
    await _setup(
        hass,
        mock_config_entry,
        protect_client,
        nvr=_nvr(status="arming", willBeArmedAt=ARMED_AT_MS),
    )
    assert _state(hass) == "arming"
    protect = mock_config_entry.runtime_data.protect_coordinator
    protect_client.nvr.get = AsyncMock(
        return_value=NVR.model_validate(_nvr(status="armed"))
    )

    with patch.object(protect, "_async_update_data", AsyncMock()) as main_poll:
        protect._handle_sensor_reconcile_interval()
        await hass.async_block_till_done(wait_background_tasks=True)

    assert _state(hass) == "armed_away"
    main_poll.assert_not_awaited()


async def test_reconcile_timer_picks_up_a_breach(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A breach the console raised on its own shows up as triggered."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr(status="armed"))
    protect = mock_config_entry.runtime_data.protect_coordinator
    protect_client.nvr.get = AsyncMock(
        return_value=NVR.model_validate(
            _nvr(status="breach", breachDetectedAt=ARMED_AT_MS, breachEventCount=1)
        )
    )

    await protect._reconcile_nvr_arm_mode()
    await hass.async_block_till_done()

    assert _state(hass) == "triggered"
    assert _attributes(hass)["breach_event_count"] == 1


async def test_reconcile_notifies_only_when_the_nvr_changed(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A quiet console costs no entity updates."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())
    protect = mock_config_entry.runtime_data.protect_coordinator
    listener = MagicMock()
    protect.async_add_listener(listener)

    await protect._reconcile_nvr_arm_mode()
    listener.assert_not_called()

    protect_client.nvr.get = AsyncMock(
        return_value=NVR.model_validate(_nvr(status="armed"))
    )
    await protect._reconcile_nvr_arm_mode()
    listener.assert_called_once()


async def test_reconcile_skips_an_nvr_without_arm_mode(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """Older firmware has no arm mode to reconcile, so nothing is requested."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr_without_arm_mode())
    protect = mock_config_entry.runtime_data.protect_coordinator
    polls_before = protect_client.nvr.get.await_count

    await protect._reconcile_nvr_arm_mode()

    assert protect_client.nvr.get.await_count == polls_before


async def test_reconcile_tick_includes_the_nvr(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """The sensor reconcile tick also reconciles the NVR's arm mode."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())
    protect = mock_config_entry.runtime_data.protect_coordinator

    with (
        patch.object(protect, "async_refresh_sensors", AsyncMock()) as sensors,
        patch.object(protect, "_reconcile_nvr_arm_mode", AsyncMock()) as nvr,
    ):
        await protect._reconcile_sensor_refresh()

    sensors.assert_awaited_once()
    nvr.assert_awaited_once()


GAM_ERROR = UniFiGlobalAlarmManagerError("API error", 400, GLOBAL_ALARM_MANAGER_BODY)
GAM_MONOTONIC = "custom_components.unifi_insights.coordinators.protect.time.monotonic"


async def _reconcile_at(
    hass: HomeAssistant, protect: Any, *, seconds_from_now: float
) -> None:
    """Run the NVR reconcile as if ``seconds_from_now`` had passed, then settle."""
    start = time.monotonic()
    with patch(GAM_MONOTONIC, side_effect=lambda: start + seconds_from_now):
        await protect._reconcile_nvr_arm_mode()
    await hass.async_block_till_done()


async def test_panel_is_not_created_while_the_global_alarm_manager_is_on(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Protect's own arm state does not follow the manager, so there is no panel."""
    caplog.set_level(logging.INFO)
    await _setup(
        hass, mock_config_entry, protect_client, nvr=_nvr(), gam_error=GAM_ERROR
    )

    assert hass.states.async_entity_ids("alarm_control_panel") == []
    protect = mock_config_entry.runtime_data.protect_coordinator
    assert protect.data["global_alarm_manager"] is True
    protect_client.arm_profiles.enable.assert_not_awaited()
    protect_client.arm_profiles.disable.assert_not_awaited()
    assert caplog.text.count("UniFi global alarm manager is enabled") == 1
    assert "local arm state does not follow it" in caplog.text


async def test_panel_is_created_while_the_global_alarm_manager_is_off(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A listing of the arm profiles that works means the manager is off."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())

    protect = mock_config_entry.runtime_data.protect_coordinator
    assert protect.data["global_alarm_manager"] is False
    assert _state(hass) == "disarmed"
    protect_client.arm_profiles.get_all.assert_awaited_once()


async def test_panel_turns_unavailable_when_the_manager_is_switched_on_and_back(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The entity is kept, not deleted, and comes back when the manager is off."""
    caplog.set_level(logging.INFO)
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())
    protect = mock_config_entry.runtime_data.protect_coordinator
    registry = er.async_get(hass)
    assert _state(hass) == "disarmed"

    protect_client.arm_profiles.get_all = AsyncMock(side_effect=GAM_ERROR)
    await _reconcile_at(hass, protect, seconds_from_now=3601)

    assert _state(hass) == STATE_UNAVAILABLE
    assert registry.async_get_entity_id("alarm_control_panel", DOMAIN, UNIQUE_ID)
    assert caplog.text.count("UniFi global alarm manager is enabled") == 1

    # Asked again while it is still on: still hidden, and the log is not repeated.
    await _reconcile_at(hass, protect, seconds_from_now=7300)
    assert _state(hass) == STATE_UNAVAILABLE
    assert caplog.text.count("UniFi global alarm manager is enabled") == 1

    protect_client.arm_profiles.get_all = AsyncMock(return_value=[])
    await _reconcile_at(hass, protect, seconds_from_now=11000)

    assert _state(hass) == "disarmed"
    assert "UniFi global alarm manager is no longer enabled" in caplog.text
    assert len(hass.states.async_entity_ids("alarm_control_panel")) == 1


async def test_panel_appears_when_the_manager_turns_out_to_be_off(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """On at setup, off later: the panel is created without a reload."""
    await _setup(
        hass, mock_config_entry, protect_client, nvr=_nvr(), gam_error=GAM_ERROR
    )
    assert hass.states.async_entity_ids("alarm_control_panel") == []
    protect = mock_config_entry.runtime_data.protect_coordinator

    protect_client.arm_profiles.get_all = AsyncMock(return_value=[])
    await _reconcile_at(hass, protect, seconds_from_now=3601)

    assert _state(hass) == "disarmed"


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("boom"),
        UniFiResponseError("API error", 500),
        UniFiResponseError("API error", 400, '{"error":"something else"}'),
        UniFiNotFoundError("Not Found", 404),
    ],
    ids=["exception", "500", "other-400", "404"],
)
@pytest.mark.parametrize("known_on", [False, True], ids=["known-off", "known-on"])
async def test_a_failed_probe_keeps_the_last_known_answer_and_backs_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    *,
    error: Exception,
    known_on: bool,
) -> None:
    """Only a definitive answer changes anything; a failure is retried in 5 minutes."""
    await _setup(
        hass,
        mock_config_entry,
        protect_client,
        nvr=_nvr(),
        gam_error=GAM_ERROR if known_on else None,
    )
    protect = mock_config_entry.runtime_data.protect_coordinator
    assert protect.data["global_alarm_manager"] is known_on
    protect_client.arm_profiles.get_all = AsyncMock(side_effect=error)

    await _reconcile_at(hass, protect, seconds_from_now=3601)
    # Not on the next ticks: the failure backs the probe off for 5 minutes.
    await _reconcile_at(hass, protect, seconds_from_now=3602)
    await _reconcile_at(hass, protect, seconds_from_now=3601 + 299)
    assert protect_client.arm_profiles.get_all.await_count == 1

    await _reconcile_at(hass, protect, seconds_from_now=3601 + 301)

    assert protect.data["global_alarm_manager"] is known_on
    assert protect_client.arm_profiles.get_all.await_count == 2
    assert bool(hass.states.async_entity_ids("alarm_control_panel")) is (not known_on)
    if not known_on:
        assert _state(hass) == "disarmed"


async def test_nothing_is_known_until_the_first_definitive_answer(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """Unknown means no panel, and Protect is asked again every 5 minutes."""
    await _setup(
        hass,
        mock_config_entry,
        protect_client,
        nvr=_nvr(),
        gam_error=RuntimeError("boom"),
    )
    protect = mock_config_entry.runtime_data.protect_coordinator
    assert protect.data["global_alarm_manager"] is None
    assert hass.states.async_entity_ids("alarm_control_panel") == []
    asked = protect_client.arm_profiles.get_all.await_count

    # Not on every tick: the 5 minutes since the failed attempt at setup.
    await _reconcile_at(hass, protect, seconds_from_now=1)
    await _reconcile_at(hass, protect, seconds_from_now=299)
    assert protect_client.arm_profiles.get_all.await_count == asked

    await _reconcile_at(hass, protect, seconds_from_now=301)
    assert protect_client.arm_profiles.get_all.await_count == asked + 1

    protect_client.arm_profiles.get_all = AsyncMock(return_value=[])
    await _reconcile_at(hass, protect, seconds_from_now=301 + 301)

    assert protect.data["global_alarm_manager"] is False
    assert _state(hass) == "disarmed"


async def _probe_at(protect: Any, *, seconds_from_now: float) -> None:
    """Probe as if ``seconds_from_now`` had passed, without involving entities."""
    start = time.monotonic()
    with patch(GAM_MONOTONIC, side_effect=lambda: start + seconds_from_now):
        await protect._probe_global_alarm_manager()


async def test_a_run_of_failed_probes_is_warned_about_once(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Three attempts in a row with no answer: one warning, and then silence."""
    caplog.set_level(logging.WARNING)
    await _setup(
        hass,
        mock_config_entry,
        protect_client,
        nvr=_nvr(),
        gam_error=UniFiResponseError("API error", 403),
    )
    protect = mock_config_entry.runtime_data.protect_coordinator
    assert "Could not tell whether" not in caplog.text  # the first attempt

    await _probe_at(protect, seconds_from_now=301)
    assert "Could not tell whether" not in caplog.text  # the second

    await _probe_at(protect, seconds_from_now=602)
    assert caplog.text.count("Could not tell whether") == 1  # the third
    assert "after 3 attempts" in caplog.text
    assert "the Protect alarm panel stays hidden until Protect answers" in caplog.text

    for attempt in (903, 1204, 1505):
        await _probe_at(protect, seconds_from_now=attempt)
    assert caplog.text.count("Could not tell whether") == 1


async def test_the_warning_comes_again_after_an_answer_resets_the_run(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An answer ends the run; three more failures warn again."""
    caplog.set_level(logging.WARNING)
    await _setup(
        hass,
        mock_config_entry,
        protect_client,
        nvr=_nvr(),
        gam_error=RuntimeError("boom"),
    )
    protect = mock_config_entry.runtime_data.protect_coordinator
    for attempt in (301, 602):
        await _probe_at(protect, seconds_from_now=attempt)
    assert caplog.text.count("Could not tell whether") == 1

    protect_client.arm_profiles.get_all = AsyncMock(return_value=[])
    await _probe_at(protect, seconds_from_now=903)
    assert protect.data["global_alarm_manager"] is False

    protect_client.arm_profiles.get_all = AsyncMock(side_effect=RuntimeError("boom"))
    for attempt in (903 + 3601, 903 + 3601 + 301, 903 + 3601 + 602):
        await _probe_at(protect, seconds_from_now=attempt)

    assert caplog.text.count("Could not tell whether") == 2
    # With an answer on record the panel is not hidden, so the warning says so.
    assert "keeps its last known state until Protect answers" in caplog.text


async def test_once_known_the_manager_is_asked_about_every_ten_minutes(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """The answer is trusted for ten minutes, then asked for again."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())
    protect = mock_config_entry.runtime_data.protect_coordinator
    asked = protect_client.arm_profiles.get_all.await_count

    await _reconcile_at(hass, protect, seconds_from_now=60)
    await _reconcile_at(hass, protect, seconds_from_now=599)
    assert protect_client.arm_profiles.get_all.await_count == asked

    await _reconcile_at(hass, protect, seconds_from_now=601)
    assert protect_client.arm_profiles.get_all.await_count == asked + 1

    # The ten minutes start again from that answer.
    await _reconcile_at(hass, protect, seconds_from_now=700)
    assert protect_client.arm_profiles.get_all.await_count == asked + 1
    await _reconcile_at(hass, protect, seconds_from_now=601 + 601)
    assert protect_client.arm_profiles.get_all.await_count == asked + 2


async def test_a_manager_switched_on_is_noticed_within_ten_minutes(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """The panel is hidden within ten minutes of the manager being enabled."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())
    protect = mock_config_entry.runtime_data.protect_coordinator
    assert _state(hass) == "disarmed"
    protect_client.arm_profiles.get_all = AsyncMock(side_effect=GAM_ERROR)

    await _reconcile_at(hass, protect, seconds_from_now=601)

    assert _state(hass) == STATE_UNAVAILABLE


async def test_reloading_the_entry_checks_the_manager_immediately(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A fresh coordinator has no answer, so its first poll asks."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())
    assert _state(hass) == "disarmed"
    protect_client.arm_profiles.get_all = AsyncMock(side_effect=GAM_ERROR)

    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    protect_client.arm_profiles.get_all.assert_awaited_once()
    protect = mock_config_entry.runtime_data.protect_coordinator
    assert protect.data["global_alarm_manager"] is True
    # The entity is kept in the registry, but nothing serves it any more.
    assert _state(hass) == STATE_UNAVAILABLE


async def test_the_manager_is_not_asked_about_when_the_nvr_has_no_arm_mode(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """Older firmware has no Alarm Manager, so there is nothing to ask."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr_without_arm_mode())
    protect = mock_config_entry.runtime_data.protect_coordinator

    await protect._probe_global_alarm_manager()

    protect_client.arm_profiles.get_all.assert_not_awaited()
    assert protect.data["global_alarm_manager"] is None


async def test_a_command_refused_by_the_manager_hides_the_panel(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A manager switched on since it was last asked is learned from the refusal."""
    caplog.set_level(logging.INFO)
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())
    protect = mock_config_entry.runtime_data.protect_coordinator
    protect_client.arm_profiles.enable = AsyncMock(side_effect=GAM_ERROR)
    asked = protect_client.arm_profiles.get_all.await_count

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "alarm_arm_away")

    assert err.value.translation_key == "global_alarm_manager"
    assert protect.data["global_alarm_manager"] is True
    assert _state(hass) == STATE_UNAVAILABLE
    assert caplog.text.count("UniFi global alarm manager is enabled") == 1
    # The refusal was the answer, so no extra question is asked.
    assert protect_client.arm_profiles.get_all.await_count == asked

    # A second refusal changes nothing and is not logged again.
    with pytest.raises(HomeAssistantError):
        await _call_service_directly(hass)
    assert caplog.text.count("UniFi global alarm manager is enabled") == 1


async def _call_service_directly(hass: HomeAssistant) -> None:
    """Ask the Protect owner to arm, as the panel would if it were still there."""
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await entry.runtime_data.coordinator.require_protect_coordinator().async_arm_alarm()


async def test_a_flag_that_survives_polls_and_nvr_frames(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """The answer lives beside the NVR record, which every poll and frame rewrites."""
    await _setup(
        hass, mock_config_entry, protect_client, nvr=_nvr(), gam_error=GAM_ERROR
    )
    protect = mock_config_entry.runtime_data.protect_coordinator

    await _push(hass, mock_config_entry, _ws_frame({"status": "armed"}))
    await protect._fetch_nvr()

    assert protect.data["global_alarm_manager"] is True
    assert "global_alarm_manager" not in protect.data["nvrs"]["nvr_1"]


async def test_marking_the_manager_enabled_twice_notifies_once(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """The coordinator tells its listeners only when the answer changes."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())
    protect = mock_config_entry.runtime_data.protect_coordinator
    listener = MagicMock()
    protect.async_add_listener(listener)

    protect.mark_global_alarm_manager_enabled()
    protect.mark_global_alarm_manager_enabled()

    listener.assert_called_once()
    assert protect.data["global_alarm_manager"] is True


@pytest.mark.parametrize("path", ["poll", "reconcile"])
async def test_older_nvr_read_does_not_overwrite_a_frame_that_landed_meanwhile(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    path: str,
) -> None:
    """The console arms while a read of the NVR is out; the frame wins.

    The read was answered before the console armed, so it says disabled. A WebSocket
    frame that landed while the request was out is newer, and applying the read
    would put the panel back to disarmed until the next frame or poll.
    """
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())
    assert _state(hass) == "disarmed"
    protect = mock_config_entry.runtime_data.protect_coordinator
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_nvr_get() -> NVR:
        started.set()
        await release.wait()
        return NVR.model_validate(_nvr(status="disabled"))

    protect_client.nvr.get = slow_nvr_get
    fetch = protect._fetch_nvr if path == "poll" else protect._reconcile_nvr_arm_mode
    task = asyncio.create_task(fetch())
    await started.wait()

    await _push(hass, mock_config_entry, _ws_frame({"status": "armed"}))
    assert _state(hass) == "armed_away"
    release.set()
    await task
    await hass.async_block_till_done()

    assert _state(hass) == "armed_away"
    stored = protect.data["nvrs"]["nvr_1"]
    assert stored["armMode"]["status"] == "armed"
    assert stored["armMode"]["armProfileId"] == "arm_profile_away"


async def test_nvr_read_started_after_a_frame_is_applied(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A frame that landed before the request started does not make it stale."""
    await _setup(hass, mock_config_entry, protect_client, nvr=_nvr())
    protect = mock_config_entry.runtime_data.protect_coordinator
    await _push(hass, mock_config_entry, _ws_frame({"status": "armed"}))
    protect_client.nvr.get = AsyncMock(
        return_value=NVR.model_validate(_nvr(status="breach"))
    )

    await protect._fetch_nvr()
    await hass.async_block_till_done()

    assert protect.data["nvrs"]["nvr_1"]["armMode"]["status"] == "breach"
