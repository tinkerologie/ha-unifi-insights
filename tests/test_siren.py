# Copyright 2026 UniFi Insights contributors
"""Tests for the UniFi Protect siren platform.

Most of these set the integration up through the real config entry and call the
real ``siren`` services, so a service call travels through the entity, the real
facade and the Protect coordinator that owns the siren commands, and only the
Protect API client is mocked.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.siren import (
    ATTR_DURATION,
    ATTR_VOLUME_LEVEL,
    SirenEntityFeature,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from unifi_official_api import (
    UniFiNotFoundError,
    UniFiResponseError,
)
from unifi_official_api.protect.models import Siren

from custom_components.unifi_insights import siren as siren_platform
from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.siren import (
    PARALLEL_UPDATES,
    async_setup_entry,
    siren_run_end_ms,
    siren_volume_from_level,
)
from tests.fixtures.assertions import require_instance, require_not_none
from tests.fixtures.library_responses import SAMPLE_SIREN
from tests.fixtures.protect_ws import FakeProtectWebSocket

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / DOMAIN


def _siren(siren_id: str = "siren_1", **overrides: Any) -> dict[str, Any]:
    """Return a copy of the live siren capture with ``overrides`` applied."""
    record = copy.deepcopy(SAMPLE_SIREN)
    record["id"] = siren_id
    record.update(overrides)
    return record


def _active(duration_ms: int = 30000, **extra: Any) -> dict[str, Any]:
    """Return a ``sirenStatus`` for a run that starts now (in the test's clock).

    Protect reports both ``activatedAt`` and ``duration`` in milliseconds.
    """
    return {
        "isActive": True,
        "activatedAt": int(dt_util.utcnow().timestamp() * 1000),
        "duration": duration_ms,
        **extra,
    }


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    protect_client: MagicMock,
    *,
    sirens: list[dict[str, Any]],
) -> None:
    """Set the integration up with ``sirens`` on a mocked Protect console."""
    endpoint = protect_client.sirens
    endpoint.get_all = AsyncMock(
        return_value=[Siren.model_validate(record) for record in sirens]
    )
    endpoint.play = AsyncMock(return_value=True)
    endpoint.stop = AsyncMock(return_value=True)
    endpoint.set_volume = AsyncMock()

    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant, siren_id: str = "siren_1") -> str:
    """Look the siren entity up by unique_id, never by a guessed entity_id."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "siren", DOMAIN, f"{DOMAIN}_siren_{siren_id}"
    )
    assert entity_id is not None, f"no siren entity for {siren_id}"
    return entity_id


def _state(hass: HomeAssistant, siren_id: str = "siren_1") -> str:
    state = hass.states.get(_entity_id(hass, siren_id))
    assert state is not None
    return state.state


async def _call(hass: HomeAssistant, service: str, **data: Any) -> None:
    """Call a real ``siren`` service on the default siren."""
    await hass.services.async_call(
        "siren",
        service,
        {ATTR_ENTITY_ID: _entity_id(hass), **data},
        blocking=True,
    )


def _ws_frame(siren_id: str, status: dict[str, Any]) -> dict[str, Any]:
    """Build a devices-stream frame in the confirmed {"type", "item"} envelope."""
    return {
        "type": "update",
        "item": {"id": siren_id, "modelKey": "siren", "sirenStatus": status},
    }


async def _push(
    hass: HomeAssistant, entry: MockConfigEntry, frame: dict[str, Any]
) -> None:
    """Deliver a WebSocket frame through the Protect devices subscription."""
    feed = entry.runtime_data.protect_coordinator.protect_client.websocket
    await feed.wait_subscribed("devices")
    feed.raw("devices", frame)
    await hass.async_block_till_done()


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


def test_parallel_updates_value() -> None:
    """Sirens are action based, so updates may run one at a time per platform."""
    assert PARALLEL_UPDATES == 1


@pytest.mark.parametrize(
    ("level", "expected"),
    [(0.0, 1), (0.004, 1), (0.125, 12), (0.5, 50), (0.999, 100), (1.0, 100)],
)
def test_volume_level_rounding_and_clamping(level: float, expected: int) -> None:
    """HA's 0..1 level becomes a rounded 1..100 volume; 0 is not an API value."""
    assert siren_volume_from_level(level) == expected


async def test_setup_without_protect_client_adds_nothing(hass: HomeAssistant) -> None:
    """A console with no Protect app creates no siren entities."""
    entry = MagicMock()
    entry.runtime_data.coordinator.protect_client = None
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    add_entities.assert_not_called()
    entry.async_on_unload.assert_not_called()


async def test_discovery_skips_malformed_records_and_retries_failures(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-dict record is ignored; a siren that fails to build is retried."""
    entry = MagicMock()
    coordinator = entry.runtime_data.coordinator
    coordinator.data = {
        "protect": {"sirens": {"junk": "not-a-dict", "s1": {"id": "s1"}}}
    }
    add_entities = MagicMock()
    built = MagicMock()

    with patch.object(
        siren_platform, "UnifiProtectSiren", side_effect=[ValueError("boom"), built]
    ) as factory:
        await async_setup_entry(hass, entry, add_entities)
        add_entities.assert_not_called()
        assert "Skipping siren s1 due to error: boom" in caplog.text

        # The next coordinator update retries it, and only it.
        (listener,) = coordinator.async_add_listener.call_args.args
        listener()
        add_entities.assert_called_once_with([built])

        # Once added it is known, so a further update adds nothing.
        listener()
        add_entities.assert_called_once()
        assert factory.call_count == 2


async def test_siren_entity_unique_id_and_device_linkage(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
) -> None:
    """The entity is keyed on the Protect id and named after its device."""
    await _setup(hass, mock_config_entry, protect_client, sirens=[_siren()])

    entity = er.async_get(hass).async_get(_entity_id(hass))
    assert entity is not None
    assert entity.unique_id == f"{DOMAIN}_siren_siren_1"
    device = dr.async_get(hass).async_get(require_not_none(entity.device_id))
    assert device is not None
    assert device.identifiers == {(DOMAIN, "protect_siren_siren_1")}
    assert (dr.CONNECTION_NETWORK_MAC, dr.format_mac("AABBCC000005")) in (
        require_instance(device, dr.DeviceEntry).connections
    )
    assert require_instance(device, dr.DeviceEntry).model == "UP-Siren-PoE"
    state = hass.states.get(entity.entity_id)
    assert state is not None
    assert state.attributes["friendly_name"] == "Garage Siren"


async def test_supported_features_match_core(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
) -> None:
    """Same feature set as Home Assistant's own UniFi Protect siren."""
    await _setup(hass, mock_config_entry, protect_client, sirens=[_siren()])

    state = hass.states.get(_entity_id(hass))
    assert state is not None
    assert state.attributes["supported_features"] == int(
        SirenEntityFeature.TURN_ON
        | SirenEntityFeature.TURN_OFF
        | SirenEntityFeature.DURATION
        | SirenEntityFeature.VOLUME_SET
    )


async def test_state_follows_siren_status_is_active(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
) -> None:
    """Off from the poll, on after a WebSocket frame, off after the next frame."""
    await _setup(hass, mock_config_entry, protect_client, sirens=[_siren()])
    assert _state(hass) == STATE_OFF

    await _push(hass, mock_config_entry, _ws_frame("siren_1", _active()))
    assert _state(hass) == STATE_ON

    await _push(hass, mock_config_entry, _ws_frame("siren_1", {"isActive": False}))
    assert _state(hass) == STATE_OFF


@pytest.mark.parametrize(
    "status",
    [None, "garbage", {"isActive": "yes"}, {"activatedAt": None}],
    ids=["null", "string", "non-bool", "missing-flag"],
)
async def test_state_unknown_when_siren_status_missing_or_malformed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    status: Any,
) -> None:
    """Anything but a boolean flag reads as unknown, never as a confident off."""
    await _setup(
        hass,
        mock_config_entry,
        protect_client,
        sirens=[_siren(sirenStatus=status)],
    )

    assert _state(hass) == "unknown"


async def test_unavailable_when_disconnected(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
) -> None:
    """A siren the console reports as disconnected is unavailable."""
    await _setup(
        hass,
        mock_config_entry,
        protect_client,
        sirens=[_siren(state="DISCONNECTED")],
    )

    assert _state(hass) == STATE_UNAVAILABLE


async def test_turn_on_sets_volume_then_plays_with_duration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
) -> None:
    """The service reaches the real endpoint: volume PATCH first, then play."""
    await _setup(hass, mock_config_entry, protect_client, sirens=[_siren()])
    order: list[str] = []
    endpoint = protect_client.sirens
    endpoint.set_volume = AsyncMock(side_effect=lambda *_: order.append("set_volume"))
    endpoint.play = AsyncMock(side_effect=lambda *_, **__: order.append("play"))

    await _call(hass, "turn_on", **{ATTR_DURATION: 10, ATTR_VOLUME_LEVEL: 0.5})

    endpoint.set_volume.assert_awaited_once_with("siren_1", 50)
    endpoint.play.assert_awaited_once_with("siren_1", duration=10)
    assert order == ["set_volume", "play"]


@pytest.mark.parametrize(
    ("level", "expected"), [(0.0, 1), (0.5, 50), (1.0, 100)], ids=["min", "mid", "max"]
)
async def test_turn_on_volume_level_maps_to_api_volume(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    level: float,
    expected: int,
) -> None:
    """A volume without a duration still sets the volume, then plays by default."""
    await _setup(hass, mock_config_entry, protect_client, sirens=[_siren()])

    await _call(hass, "turn_on", **{ATTR_VOLUME_LEVEL: level})

    endpoint = protect_client.sirens
    endpoint.set_volume.assert_awaited_once_with("siren_1", expected)
    endpoint.play.assert_awaited_once_with("siren_1", duration=None)


async def test_turn_on_without_options_plays_default(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
) -> None:
    """With no options the siren plays Protect's default and the volume is left."""
    await _setup(hass, mock_config_entry, protect_client, sirens=[_siren()])

    await _call(hass, "turn_on")

    endpoint = protect_client.sirens
    endpoint.play.assert_awaited_once_with("siren_1", duration=None)
    endpoint.set_volume.assert_not_awaited()


async def test_turn_on_does_not_claim_the_siren_is_on(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
) -> None:
    """There is no optimistic "on": the WebSocket frame or poll is the source."""
    await _setup(hass, mock_config_entry, protect_client, sirens=[_siren()])

    await _call(hass, "turn_on", **{ATTR_DURATION: 5})

    assert _state(hass) == STATE_OFF


async def test_turn_on_invalid_duration_raises_translated_validation_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
) -> None:
    """An unsupported duration is refused before the volume PATCH or the play."""
    await _setup(hass, mock_config_entry, protect_client, sirens=[_siren()])

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "turn_on", **{ATTR_DURATION: 7, ATTR_VOLUME_LEVEL: 0.5})

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "siren_invalid_duration"
    assert err.value.translation_placeholders == {
        "duration": "7",
        "valid": "5, 10, 20, 30",
    }
    endpoint = protect_client.sirens
    endpoint.set_volume.assert_not_awaited()
    endpoint.play.assert_not_awaited()


async def test_turn_off_stops_and_is_optimistically_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
) -> None:
    """Protect sends no frame after a manual stop, so the cache says off at once."""
    await _setup(
        hass,
        mock_config_entry,
        protect_client,
        sirens=[_siren(sirenStatus=_active())],
    )
    assert _state(hass) == STATE_ON

    await _call(hass, "turn_off")

    protect_client.sirens.stop.assert_awaited_once_with("siren_1")
    assert _state(hass) == STATE_OFF


async def test_optimistic_off_survives_unrelated_coordinator_update(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
) -> None:
    """Every coordinator update rewrites every entity, so the off lives in the cache."""
    await _setup(
        hass,
        mock_config_entry,
        protect_client,
        sirens=[
            _siren("siren_1", sirenStatus=_active()),
            _siren("siren_22", mac="AABBCC000022"),
        ],
    )
    await _call(hass, "turn_off")
    assert _state(hass) == STATE_OFF

    # A frame for the other siren updates the coordinator, and with it this entity.
    await _push(hass, mock_config_entry, _ws_frame("siren_22", _active()))

    assert _state(hass, "siren_22") == STATE_ON
    assert _state(hass, "siren_1") == STATE_OFF


async def test_play_api_error_raises_home_assistant_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
) -> None:
    """A failed request reaches the user as a HomeAssistantError."""
    await _setup(hass, mock_config_entry, protect_client, sirens=[_siren()])
    protect_client.sirens.play = AsyncMock(side_effect=UniFiResponseError("x", 500))

    with pytest.raises(HomeAssistantError, match="Unable to sound siren siren_1"):
        await _call(hass, "turn_on")


async def test_stop_api_error_leaves_the_siren_on(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
) -> None:
    """A stop Protect refused must not make the siren read off."""
    await _setup(
        hass,
        mock_config_entry,
        protect_client,
        sirens=[_siren(sirenStatus=_active())],
    )
    protect_client.sirens.stop = AsyncMock(side_effect=UniFiResponseError("x", 500))

    with pytest.raises(HomeAssistantError, match="Unable to stop siren siren_1"):
        await _call(hass, "turn_off")

    assert _state(hass) == STATE_ON


@pytest.mark.parametrize(
    "error",
    [
        UniFiNotFoundError("Not Found", 404),
        UniFiResponseError("API returned non-JSON response (status 200)", 200),
    ],
    ids=["404", "non-json-200"],
)
async def test_sirens_endpoint_unsupported_creates_no_entity(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """A Protect without /sirens is logged once at INFO, not warned about."""
    caplog.set_level(logging.INFO)
    protect_client.sirens.get_all = AsyncMock(side_effect=error)

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.async_entity_ids("siren") == []
    assert caplog.text.count("does not expose sirens") == 1
    warnings = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING and "sirens" in record.getMessage()
    ]
    assert warnings == []


async def test_siren_added_on_later_poll(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
) -> None:
    """A siren adopted after setup appears without reloading the entry."""
    await _setup(hass, mock_config_entry, protect_client, sirens=[])
    assert hass.states.async_entity_ids("siren") == []

    protect_client.sirens.get_all = AsyncMock(
        return_value=[Siren.model_validate(_siren())]
    )
    await mock_config_entry.runtime_data.protect_coordinator.async_refresh()
    await hass.async_block_till_done()

    assert _state(hass) == STATE_OFF


def test_siren_exception_translation_in_both_files() -> None:
    """strings.json and en.json agree, and the placeholders are not quoted."""
    messages = []
    for path in ("strings.json", "translations/en.json"):
        data = json.loads((COMPONENT_DIR / path).read_text(encoding="utf-8"))
        messages.append(data["exceptions"]["siren_invalid_duration"]["message"])

    assert messages[0] == messages[1]
    assert "{duration}" in messages[0]
    assert "{valid}" in messages[0]
    # hassfest rejects a placeholder inside single quotes.
    assert "'{" not in messages[0]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({"activatedAt": 1000, "duration": 5000}, 6000.0),
        ({"activatedAt": 1000.5, "duration": 5000}, 6000.5),
        ({"activatedAt": 1000, "duration": 30000}, 31000.0),
        # Read as seconds when it can only be seconds (a run is at most 30 s).
        ({"activatedAt": 1000, "duration": 5}, 6000.0),
        ({"activatedAt": 1000, "duration": 30}, 31000.0),
        ({"activatedAt": 1000, "duration": 60}, 61000.0),
        # Above that it is milliseconds, however short.
        ({"activatedAt": 1000, "duration": 61}, 1061.0),
        ({"activatedAt": None, "duration": 5000}, None),
        ({"activatedAt": 1000, "duration": None}, None),
        ({"activatedAt": True, "duration": 5000}, None),
        ({"activatedAt": 1000, "duration": True}, None),
        ({"activatedAt": "1000", "duration": 5000}, None),
        ({}, None),
    ],
    ids=[
        "ints",
        "float",
        "ms-30s",
        "seconds-5",
        "seconds-30",
        "seconds-60",
        "ms-61",
        "no-start",
        "no-duration",
        "bool-start",
        "bool-duration",
        "string",
        "empty",
    ],
)
def test_siren_run_end_ms(status: dict[str, Any], expected: float | None) -> None:
    """The end of a run is start plus duration (milliseconds, or seconds if tiny)."""
    assert siren_run_end_ms(status) == expected


async def test_timed_run_reads_off_while_unrelated_frames_keep_arriving(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    freezer: Any,
) -> None:
    """A 5 s run ends on its own: no poll, no frame about this siren.

    Protect sends nothing when a timed run ends, and a frame from any device
    pushes the next poll back, so on a busy console nothing else would ever
    show the siren off.
    """
    freezer.move_to("2026-10-06 12:00:00+00:00")
    await _setup(
        hass,
        mock_config_entry,
        protect_client,
        sirens=[_siren(), _siren("siren_22", mac="AABBCC000022")],
    )
    polls = protect_client.sirens.get_all.await_count

    await _push(hass, mock_config_entry, _ws_frame("siren_1", _active(5000)))
    assert _state(hass) == STATE_ON

    freezer.tick(timedelta(seconds=4))
    async_fire_time_changed(hass)
    await _push(hass, mock_config_entry, _ws_frame("siren_22", {"isActive": False}))
    assert _state(hass) == STATE_ON

    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _state(hass) == STATE_OFF

    # A frame every 10 s for another device changes nothing, and polls nothing.
    for _ in range(2):
        freezer.tick(timedelta(seconds=10))
        await _push(hass, mock_config_entry, _ws_frame("siren_22", {"isActive": False}))
        assert _state(hass) == STATE_OFF
    assert protect_client.sirens.get_all.await_count == polls


async def test_run_in_progress_at_setup_ends_on_its_own(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    freezer: Any,
) -> None:
    """A siren already sounding when the integration starts is not left on."""
    freezer.move_to("2026-10-06 12:00:00+00:00")
    await _setup(
        hass,
        mock_config_entry,
        protect_client,
        sirens=[_siren(sirenStatus=_active(5000))],
    )
    assert _state(hass) == STATE_ON

    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert _state(hass) == STATE_OFF


@pytest.mark.parametrize("duration", [5, 5000], ids=["seconds", "milliseconds"])
async def test_duration_is_read_in_either_unit(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    freezer: Any,
    duration: int,
) -> None:
    """The spec says milliseconds but one library reads seconds; both end at 5 s."""
    freezer.move_to("2026-10-06 12:00:00+00:00")
    await _setup(hass, mock_config_entry, protect_client, sirens=[_siren()])
    await _push(hass, mock_config_entry, _ws_frame("siren_1", _active(duration)))
    assert _state(hass) == STATE_ON

    freezer.tick(timedelta(seconds=4))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _state(hass) == STATE_ON

    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _state(hass) == STATE_OFF


async def test_run_that_has_already_ended_reads_off_at_setup(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
) -> None:
    """A stale isActive from a console that never sent the end reads off."""
    stale = {"isActive": True, "activatedAt": 1759700000000, "duration": 10000}
    await _setup(
        hass, mock_config_entry, protect_client, sirens=[_siren(sirenStatus=stale)]
    )

    assert _state(hass) == STATE_OFF


async def test_expiry_is_judged_on_the_next_update_even_without_the_timer(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    freezer: Any,
) -> None:
    """The state does not depend on the timer: any update reads the clock."""
    freezer.move_to("2026-10-06 12:00:00+00:00")
    with patch.object(siren_platform, "async_call_later", MagicMock()):
        await _setup(
            hass,
            mock_config_entry,
            protect_client,
            sirens=[_siren(), _siren("siren_22", mac="AABBCC000022")],
        )
        await _push(hass, mock_config_entry, _ws_frame("siren_1", _active(5000)))
        assert _state(hass) == STATE_ON

        freezer.tick(timedelta(seconds=6))
        await _push(hass, mock_config_entry, _ws_frame("siren_22", {"isActive": False}))

    assert _state(hass) == STATE_OFF


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"activatedAt": None, "duration": None},
        {"duration": None},
        {"activatedAt": None},
    ],
    ids=["bare", "both-null", "no-duration", "no-start"],
)
async def test_run_with_an_unknown_end_stays_on_until_told_otherwise(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    extra: dict[str, Any],
) -> None:
    """Without both a start and a duration there is nothing to expire on."""
    status = {"isActive": True, **extra}
    with patch.object(siren_platform, "async_call_later", MagicMock()) as later:
        await _setup(
            hass, mock_config_entry, protect_client, sirens=[_siren(sirenStatus=status)]
        )

    assert _state(hass) == STATE_ON
    later.assert_not_called()


async def test_each_update_reschedules_the_expiry_and_removal_cancels_it(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
    freezer: Any,
) -> None:
    """At most one timer is pending: replaced on every update, gone on removal."""
    freezer.move_to("2026-10-06 12:00:00+00:00")
    cancels: list[MagicMock] = []
    delays: list[float] = []

    def fake_call_later(_hass: Any, delay: float, _action: Any) -> MagicMock:
        delays.append(delay)
        cancels.append(MagicMock())
        return cancels[-1]

    with patch.object(siren_platform, "async_call_later", fake_call_later):
        await _setup(hass, mock_config_entry, protect_client, sirens=[_siren()])
        assert delays == []

        await _push(hass, mock_config_entry, _ws_frame("siren_1", _active(5000)))
        assert delays == [5.0]
        cancels[0].assert_not_called()

        # A new run replaces the pending timer with one for its own end.
        await _push(hass, mock_config_entry, _ws_frame("siren_1", _active(20000)))
        assert delays == [5.0, 20.0]
        cancels[0].assert_called_once_with()

        # The run being stopped cancels the timer and leaves none.
        await _push(hass, mock_config_entry, _ws_frame("siren_1", {"isActive": False}))
        assert delays == [5.0, 20.0]
        cancels[1].assert_called_once_with()

        # A run that is pending when the entry unloads is cancelled too.
        await _push(hass, mock_config_entry, _ws_frame("siren_1", _active(5000)))
        assert delays == [5.0, 20.0, 5.0]
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        cancels[2].assert_called_once_with()


async def test_reconcile_timer_refreshes_sirens_without_a_frame_or_the_main_poll(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect_client: MagicMock,
) -> None:
    """The independent timer picks up a change Protect made on its own."""
    await _setup(
        hass,
        mock_config_entry,
        protect_client,
        sirens=[_siren(sirenStatus=_active())],
    )
    assert _state(hass) == STATE_ON
    protect = mock_config_entry.runtime_data.protect_coordinator
    protect_client.sirens.get_all = AsyncMock(
        return_value=[Siren.model_validate(_siren(sirenStatus={"isActive": False}))]
    )

    with patch.object(protect, "_async_update_data", AsyncMock()) as main_poll:
        protect._handle_sensor_reconcile_interval()
        await hass.async_block_till_done(wait_background_tasks=True)

    assert _state(hass) == STATE_OFF
    protect_client.sirens.get_all.assert_awaited()
    main_poll.assert_not_awaited()


async def test_reconcile_tick_is_skipped_while_the_previous_one_is_running(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A console that stops answering must not collect a task per tick."""
    await _setup(hass, mock_config_entry, protect_client, sirens=[_siren()])
    protect = mock_config_entry.runtime_data.protect_coordinator
    release: asyncio.Event = asyncio.Event()
    started: int = 0

    async def hung_reconcile() -> None:
        nonlocal started
        started += 1
        await release.wait()

    with patch.object(protect, "_reconcile_sensor_refresh", hung_reconcile):
        protect._handle_sensor_reconcile_interval()
        first: asyncio.Task[None] | None = protect._sensor_reconcile_task
        await asyncio.sleep(0)
        for _ in range(3):
            protect._handle_sensor_reconcile_interval()

        assert started == 1
        assert protect._sensor_reconcile_task is first

        # Once it has finished the next tick starts a new one.
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)
        assert first is not None
        assert first.done()
        protect._handle_sensor_reconcile_interval()
        await hass.async_block_till_done(wait_background_tasks=True)

    assert started == 2


async def test_hung_reconcile_is_cancelled_when_the_entry_unloads(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, protect_client: MagicMock
) -> None:
    """A reconcile waiting on a hung console cannot outlive a reload."""
    await _setup(hass, mock_config_entry, protect_client, sirens=[_siren()])
    protect = mock_config_entry.runtime_data.protect_coordinator

    async def hung_reconcile() -> None:
        await asyncio.Event().wait()

    with patch.object(protect, "_reconcile_sensor_refresh", hung_reconcile):
        protect._handle_sensor_reconcile_interval()
        task: asyncio.Task[None] | None = protect._sensor_reconcile_task
        await asyncio.sleep(0)
        assert task is not None
        assert not task.done()

        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert task.cancelled()
    assert protect._sensor_reconcile_task is None
