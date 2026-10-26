# Copyright 2026 UniFi Insights contributors
"""Characterization tests for how the Protect coordinator reconciles its inputs.

These pin what the coordinator does today when WebSocket frames and REST polls
interleave, using only the seams that stay through #340's refactor: frames go
in through the WebSocket callbacks (``FakeProtectWebSocket``), REST answers
come from the client mocks, and polls run through the public refresh calls.
They must keep passing unchanged while the reconciliation moves into
``protect_ws.py`` and ``protect_events.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.helpers import device_registry as dr
from unifi_official_api.protect.models import Siren

from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.coordinators.protect import (
    MAX_CONSECUTIVE_MISSING_POLLS,
    STALE_EVENT_TIMEOUT,
    UnifiInsightsProtectCoordinator,
)
from tests.fixtures.assertions import require_not_none
from tests.fixtures.coordinator_clients import (
    _create_mock_model,
    _create_mock_network_client,
    _create_mock_protect_client,
)
from tests.fixtures.library_responses import SAMPLE_SIREN
from tests.fixtures.protect_ws import FakeProtectWebSocket

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

ACTIVATED_AT = 1_760_000_000_000


def _siren(*, active: bool, activated_at: int | None = ACTIVATED_AT) -> Siren:
    """Return a siren model with the given status."""
    record = copy.deepcopy(SAMPLE_SIREN)
    record["sirenStatus"] = {
        "isActive": active,
        "activatedAt": activated_at if active else None,
        "duration": 10000 if active else None,
    }
    return Siren.model_validate(record)


def _sensor(*, opened: bool, changed_at: int) -> Any:
    """Return a door sensor REST record."""
    return _create_mock_model(
        {
            "id": "sensor1",
            "name": "Door Sensor",
            "state": "CONNECTED",
            "isOpened": opened,
            "openStatusChangedAt": changed_at,
        }
    )


class _Gate:
    """A REST answer that waits until the test releases it."""

    def __init__(self, answer: list[Any]) -> None:
        self.answer = answer
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        self.started.set()
        await self.release.wait()
        return self.answer


async def _while_in_flight(
    gate: _Gate, request: Awaitable[Any], during: Callable[[], Awaitable[None] | None]
) -> None:
    """
    Run ``request``; call ``during`` once its REST call is out; then answer.

    Fails, instead of hanging, when the request never reaches the gated call,
    and never leaves the request task pending.
    """
    task = asyncio.ensure_future(request)
    started = asyncio.ensure_future(gate.started.wait())
    try:
        done, _ = await asyncio.wait(
            {task, started}, timeout=5, return_when=asyncio.FIRST_COMPLETED
        )
        if started not in done:
            if task.done():
                await task  # surface the request's own error
            msg = "the request never reached the gated REST call"
            raise AssertionError(msg)
        result = during()
        if result is not None:
            await result
    except BaseException:
        gate.release.set()
        started.cancel()
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        raise
    gate.release.set()
    await task


@pytest.fixture
async def protect(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> AsyncGenerator[tuple[UnifiInsightsProtectCoordinator, FakeProtectWebSocket]]:
    """Return a polled coordinator with both WebSocket streams subscribed."""
    mock_config_entry.add_to_hass(hass)
    client = _create_mock_protect_client()
    feed = FakeProtectWebSocket()
    client.websocket = feed
    for family in ("fobs", "link_stations", "alarm_hubs"):
        getattr(client, family).get_all = AsyncMock(return_value=[])
    client.sirens.get_all = AsyncMock(return_value=[_siren(active=False)])
    client.sirens.stop = AsyncMock()
    client.sensors.get_all = AsyncMock(
        return_value=[_sensor(opened=False, changed_at=1000)]
    )
    coordinator = UnifiInsightsProtectCoordinator(
        hass=hass,
        network_client=_create_mock_network_client(),
        protect_client=client,
        entry=mock_config_entry,
    )
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    await coordinator.async_start_websocket()
    await feed.wait_subscribed("devices", "events")
    yield coordinator, feed
    await coordinator.async_stop_websocket()
    await coordinator.async_shutdown()


# --- WebSocket then REST -------------------------------------------------------


async def test_door_frame_during_a_sensor_poll_beats_the_older_rest_reading(
    protect: tuple[UnifiInsightsProtectCoordinator, FakeProtectWebSocket],
) -> None:
    """A door that opens while the poll is out stays open; REST answers older."""
    coordinator, feed = protect
    gate = _Gate([_sensor(opened=False, changed_at=1000)])
    require_not_none(coordinator.protect_client).sensors.get_all = gate

    await _while_in_flight(
        gate,
        coordinator.async_refresh_sensors(),
        lambda: feed.device(
            "sensor", id="sensor1", isOpened=True, openStatusChangedAt=2000
        ),
    )

    assert coordinator.data["sensors"]["sensor1"]["isOpened"] is True


async def test_a_door_frame_during_the_poll_wins_even_over_a_later_timestamp(
    protect: tuple[UnifiInsightsProtectCoordinator, FakeProtectWebSocket],
) -> None:
    """The frame landed while the request was out, so REST predates it."""
    coordinator, feed = protect
    gate = _Gate([_sensor(opened=False, changed_at=2500)])
    require_not_none(coordinator.protect_client).sensors.get_all = gate

    await _while_in_flight(
        gate,
        coordinator.async_refresh_sensors(),
        lambda: feed.device(
            "sensor", id="sensor1", isOpened=True, openStatusChangedAt=2000
        ),
    )

    assert coordinator.data["sensors"]["sensor1"]["isOpened"] is True


async def test_a_newer_rest_reading_beats_an_earlier_door_frame(
    protect: tuple[UnifiInsightsProtectCoordinator, FakeProtectWebSocket],
) -> None:
    """A frame from before the poll does not hide the console's newer state."""
    coordinator, feed = protect
    feed.device("sensor", id="sensor1", isOpened=True, openStatusChangedAt=2000)
    assert coordinator.data["sensors"]["sensor1"]["isOpened"] is True
    await asyncio.sleep(0.01)

    require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
        return_value=[_sensor(opened=False, changed_at=3000)]
    )
    await coordinator.async_refresh_sensors()

    assert coordinator.data["sensors"]["sensor1"]["isOpened"] is False


# --- Stale REST after WebSocket ------------------------------------------------


async def test_a_siren_frame_during_a_poll_beats_the_polls_older_answer(
    protect: tuple[UnifiInsightsProtectCoordinator, FakeProtectWebSocket],
) -> None:
    """The siren frame that lands mid-poll is newer than the poll's answer."""
    coordinator, feed = protect
    gate = _Gate([_siren(active=False)])
    require_not_none(coordinator.protect_client).sirens.get_all = gate

    await _while_in_flight(
        gate,
        coordinator.async_refresh(),
        lambda: feed.device(
            "siren",
            id="siren_1",
            sirenStatus={"isActive": True, "activatedAt": ACTIVATED_AT},
        ),
    )

    status = coordinator.data["sirens"]["siren_1"]["sirenStatus"]
    assert status["isActive"] is True


# --- Siren stop racing a poll ----------------------------------------------------


async def test_a_siren_stopped_during_a_poll_stays_off_until_a_new_run(
    protect: tuple[UnifiInsightsProtectCoordinator, FakeProtectWebSocket],
) -> None:
    """A poll answering with the stopped run never turns the siren back on."""
    coordinator, feed = protect
    feed.device(
        "siren",
        id="siren_1",
        sirenStatus={"isActive": True, "activatedAt": ACTIVATED_AT},
    )
    still_running = _Gate([_siren(active=True)])
    require_not_none(coordinator.protect_client).sirens.get_all = still_running

    await _while_in_flight(
        still_running,
        coordinator.async_refresh(),
        lambda: coordinator.async_stop_siren("siren_1"),
    )
    assert coordinator.data["sirens"]["siren_1"]["sirenStatus"]["isActive"] is False

    # The same stale reading on the next poll is still the stopped run.
    require_not_none(coordinator.protect_client).sirens.get_all = AsyncMock(
        return_value=[_siren(active=True)]
    )
    await coordinator.async_refresh()
    assert coordinator.data["sirens"]["siren_1"]["sirenStatus"]["isActive"] is False

    # A later run is believed.
    require_not_none(coordinator.protect_client).sirens.get_all = AsyncMock(
        return_value=[_siren(active=True, activated_at=ACTIVATED_AT + 60_000)]
    )
    await coordinator.async_refresh()
    assert coordinator.data["sirens"]["siren_1"]["sirenStatus"]["isActive"] is True


# --- Missing polls ---------------------------------------------------------------


async def test_a_camera_is_removed_only_after_it_misses_several_polls(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    protect: tuple[UnifiInsightsProtectCoordinator, FakeProtectWebSocket],
) -> None:
    """One missed poll is not evidence of removal; a sustained absence is."""
    coordinator, _feed = protect
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "protect_camera_camera1")},
    )
    other = _create_mock_model(
        {"id": "camera2", "name": "Back Door", "state": "CONNECTED"}
    )
    require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
        return_value=[other]
    )

    def registered() -> bool:
        return (
            registry.async_get_device_by_identifier(
                (DOMAIN, "protect_camera_camera1"), mock_config_entry.entry_id
            )
            is not None
        )

    for _ in range(MAX_CONSECUTIVE_MISSING_POLLS):
        await coordinator.async_refresh()
        assert registered()

    await coordinator.async_refresh()
    assert not registered()


# --- Latch expiry ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("event_type", "end_field"), [("motion", "lastMotionEnd"), ("ring", "lastRingEnd")]
)
async def test_a_missed_end_expires_on_reconnect_after_the_timeout(
    freezer: FrozenDateTimeFactory,
    protect: tuple[UnifiInsightsProtectCoordinator, FakeProtectWebSocket],
    event_type: str,
    end_field: str,
) -> None:
    """A start whose end never arrives is cleared once the timeout has passed."""
    coordinator, feed = protect
    feed.event(event_type, id="event1", device="camera1", start=ACTIVATED_AT)
    assert coordinator.data["cameras"]["camera1"].get(end_field) is None

    freezer.tick(STALE_EVENT_TIMEOUT - timedelta(seconds=1))
    feed.connection("events", connected=True)
    assert coordinator.data["cameras"]["camera1"].get(end_field) is None

    freezer.tick(timedelta(seconds=1))
    feed.connection("events", connected=True)
    assert coordinator.data["cameras"]["camera1"].get(end_field) is not None
