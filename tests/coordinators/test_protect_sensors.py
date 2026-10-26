# Copyright 2026 UniFi Insights contributors
"""Tests for Protect sensor coordination and door state protection."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_VERIFY_SSL
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
)
from unifi_official_api.protect.models.sensor import Sensor

from custom_components.unifi_insights.const import (
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_LOCAL,
    DEVICE_TYPE_CAMERA,
    DEVICE_TYPE_SENSOR,
    DOMAIN,
)
from custom_components.unifi_insights.coordinators.protect import (
    MAX_CONSECUTIVE_EMPTY_FETCHES,
    MAX_CONSECUTIVE_MISSING_POLLS,
    MAX_DOOR_STATE_PRESERVE_POLLS,
    MAX_WS_RECENCY_PRESERVE_POLLS,
    UnifiInsightsProtectCoordinator,
)
from custom_components.unifi_insights.coordinators.protect_events import (
    PollCompleted,
    SensorTracking,
)
from tests.conftest import set_mock_device_lookup
from tests.fixtures.assertions import patch_attribute, require_not_none
from tests.fixtures.protect_ws import send_device

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from homeassistant.core import HomeAssistant


def _create_mock_model(data: dict[str, Any]) -> MagicMock:
    """Create a mock pydantic model returning proper dict from model_dump."""
    mock = MagicMock()
    mock.model_dump = MagicMock(return_value=data)
    for key, value in data.items():
        setattr(mock, key, value)
    return mock


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return default mocked config entry."""
    return MockConfigEntry(
        version=1,
        minor_version=0,
        domain=DOMAIN,
        title="UniFi Insights (Local)",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://192.168.1.1",
            CONF_API_KEY: "test_api_key",
            CONF_VERIFY_SSL: False,
        },
        options={},
        source="user",
        unique_id="test_api_key",
        entry_id="test_entry_id",
    )


@pytest.fixture
def mock_protect_client() -> MagicMock:
    """Create a mock protect client with sensor endpoint."""
    client = MagicMock()
    client.base_url = "https://192.168.1.1"

    client.cameras = MagicMock()
    client.cameras.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "cam1",
                    "name": "Front Camera",
                    "state": "CONNECTED",
                }
            )
        ]
    )
    client.cameras.last_result_complete = True

    client.lights = MagicMock()
    client.lights.get_all = AsyncMock(return_value=[])
    client.lights.last_result_complete = True

    client.sensors = MagicMock()
    client.sensors.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "sensor1",
                    "name": "Front Door Sensor",
                    "state": "CONNECTED",
                    "isOpened": False,
                    "openStatusChangedAt": 1000,
                    "batteryLevel": 100,
                }
            )
        ]
    )
    client.sensors.last_result_complete = True

    client.nvr = MagicMock()
    client.nvr.get = AsyncMock(
        return_value=_create_mock_model(
            {"id": "nvr1", "name": "UNVR", "version": "4.0.0"}
        )
    )

    client.chimes = MagicMock()
    client.chimes.get_all = AsyncMock(return_value=[])
    client.chimes.last_result_complete = True

    client.viewers = MagicMock()
    client.viewers.get_all = AsyncMock(return_value=[])
    client.viewers.last_result_complete = True

    client.liveviews = MagicMock()
    client.liveviews.get_all = AsyncMock(return_value=[])
    client.liveviews.last_result_complete = True

    client.get_host_id = AsyncMock(return_value="nvr1")
    client.websocket = MagicMock()
    client.websocket.subscribe_with_callback = AsyncMock()
    client.websocket.stop = MagicMock()

    return client


@pytest.fixture
async def coordinator(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_protect_client: MagicMock,
) -> AsyncGenerator[UnifiInsightsProtectCoordinator]:
    """Create a test Protect coordinator with automatic cleanup."""
    mock_network_client = MagicMock()
    coord = UnifiInsightsProtectCoordinator(
        hass=hass,
        entry=mock_config_entry,
        network_client=mock_network_client,
        protect_client=mock_protect_client,
        site_id="default",
    )
    yield coord
    await coord.async_shutdown()


class TestRapidTransitionsPreservation:
    """Test rapid WS+REST interleaving preserves the latest door transition.

    The original version of this test drove only the WebSocket path and
    passed identically on the parent commit (before sensor coordination
    existed) - it exercised no code from this change. Reframed here to
    interleave a rapid WS open/close/open sequence with an in-flight REST
    fetch, which does exercise the new door-state-preservation logic.
    """

    @pytest.mark.asyncio
    async def test_rapid_transitions_interleaved_with_rest_preserve_latest(
        self, coordinator: UnifiInsightsProtectCoordinator, monkeypatch
    ) -> None:
        """WS open->close->open during an in-flight REST fetch keeps the latest."""
        coordinator.data["sensors"] = {
            "sensor1": {
                "id": "sensor1",
                "name": "Front Door",
                "isOpened": False,
                "openStatusChangedAt": 1000,
            }
        }

        rest_gate = asyncio.Event()

        async def delayed_sensors_fetch() -> list[Any]:
            await rest_gate.wait()
            return [
                _create_mock_model(
                    {
                        "id": "sensor1",
                        "name": "Front Door",
                        "isOpened": False,
                        "openStatusChangedAt": 1000,
                    }
                )
            ]

        patch_attribute(
            monkeypatch,
            require_not_none(coordinator.protect_client).sensors,
            "get_all",
            delayed_sensors_fetch,
        )

        fetch_task = asyncio.create_task(coordinator._fetch_sensors())
        await asyncio.sleep(0.01)

        # Rapid WS open -> close -> open while the REST fetch is in flight.
        for is_opened, changed_at in ((True, 1001), (False, 1002), (True, 1003)):
            coordinator._on_websocket_message(
                {
                    "type": "update",
                    "item": {
                        "id": "sensor1",
                        "modelKey": "sensor",
                        "isOpened": is_opened,
                        "openStatusChangedAt": changed_at,
                    },
                }
            )

        rest_gate.set()
        await fetch_task

        sensor = coordinator.data["sensors"]["sensor1"]
        assert sensor["isOpened"] is True
        assert sensor["openStatusChangedAt"] == 1003


class TestIndependentSensorRecovery:
    """Test independent sensor recovery unaffected by camera traffic."""

    @pytest.mark.asyncio
    async def test_camera_traffic_does_not_postpone_sensor_reconcile_timer(
        self, hass: HomeAssistant, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Continuous camera traffic continually resets coordinator refresh timer,

        but the independent sensor reconciliation timer fires on schedule.
        """
        coordinator._start_sensor_reconcile_timer()
        reconcile_mock = AsyncMock()

        with patch.object(coordinator, "async_refresh_sensors", reconcile_mock):
            # Simulate high-rate camera frames arriving every 2 seconds for 28 seconds
            # In DataUpdateCoordinator, async_set_updated_data() reschedules
            # _unsub_refresh to now + 30s, meaning the main poll never fires if
            # interval is reset every 2s.
            for i in range(14):
                send_device(
                    coordinator,
                    DEVICE_TYPE_CAMERA,
                    {"id": "cam1", "modelKey": "camera", "lastMotion": i},
                )

            # Reconcile has not fired yet before 30 seconds
            assert reconcile_mock.call_count == 0

            # Advance time past 30 seconds
            async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
            await hass.async_block_till_done()

            # Independent sensor reconciliation fired despite camera traffic!
            assert reconcile_mock.call_count >= 1

        coordinator._stop_sensor_reconcile_timer()

    @pytest.mark.asyncio
    async def test_devices_websocket_reconnect_triggers_sensor_refresh(
        self, hass: HomeAssistant, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """When devices WebSocket stream reconnects, sensors refresh immediately."""
        refresh_mock = AsyncMock()

        with patch.object(coordinator, "async_refresh_sensors", refresh_mock):
            # Reconnect devices stream
            coordinator._on_websocket_connection_state_change("devices", connected=True)
            await hass.async_block_till_done()

            assert refresh_mock.call_count == 1

            # Disconnect should not trigger sensor refresh
            coordinator._on_websocket_connection_state_change(
                "devices", connected=False
            )
            await hass.async_block_till_done()

            assert refresh_mock.call_count == 1

            # Reconnect of events stream should not trigger sensor refresh
            # (only devices stream)
            coordinator._on_websocket_connection_state_change("events", connected=True)
            await hass.async_block_till_done()

            assert refresh_mock.call_count == 1

    @pytest.mark.asyncio
    async def test_refresh_coalescing_single_in_flight_request(
        self, coordinator: UnifiInsightsProtectCoordinator, monkeypatch
    ) -> None:
        """Overlapping sensor refresh calls never run two REST fetches
        concurrently, and a waiter's request is coalesced into exactly one
        deterministic follow-up fetch by the owner.

        This is the real single-flight-in-time guarantee: no two `/sensors`
        calls ever overlap. It is NOT the same as "only one call ever" - a
        waiter arriving while a fetch is in flight always causes the owner
        to run exactly one more fetch afterward (see docstring on
        `async_refresh_sensors`). The old assertion `call_count <= 2` proved
        nothing because that bound also holds if coalescing were deleted
        entirely and each of the two calls in this test just ran its own
        independent fetch.
        """
        call_count = 0
        concurrent = 0
        max_concurrent = 0
        fetch_gate = asyncio.Event()

        async def slow_get_all() -> list[Any]:
            nonlocal call_count, concurrent, max_concurrent
            call_count += 1
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
            await fetch_gate.wait()
            concurrent -= 1
            return [
                _create_mock_model(
                    {
                        "id": "sensor1",
                        "name": "Front Door",
                        "isOpened": False,
                    }
                )
            ]

        patch_attribute(
            monkeypatch,
            require_not_none(coordinator.protect_client).sensors,
            "get_all",
            slow_get_all,
        )

        # Start first refresh task; it becomes the owner and blocks in the
        # fetch on fetch_gate.
        task1 = asyncio.create_task(coordinator.async_refresh_sensors())
        await asyncio.sleep(0)  # let task1 enter fetch_gate.wait()

        # Start second concurrent refresh call while the first is in flight.
        task2 = asyncio.create_task(coordinator.async_refresh_sensors())
        await asyncio.sleep(0)  # let task2 become a waiter

        # While the owner's fetch is still in flight, the waiter must NOT
        # have dispatched a second, overlapping REST call.
        assert call_count == 1, (
            "a waiter arriving mid-flight must not trigger a concurrent "
            "second REST call - it should only mark a follow-up pending"
        )

        # Unblock the in-flight REST call.
        fetch_gate.set()
        await asyncio.gather(task1, task2)

        # The waiter's pending request was coalesced into exactly one
        # follow-up fetch, run strictly after the first completed - never
        # concurrently, and never more than the two calls made here.
        assert call_count == 2
        assert max_concurrent == 1, (
            "two REST /sensors fetches were in flight at the same time - "
            "this violates the single-flight-in-time guarantee"
        )

    @pytest.mark.asyncio
    async def test_waiter_reraises_error_when_raise_on_error_true(
        self, coordinator: UnifiInsightsProtectCoordinator, monkeypatch
    ) -> None:
        """A waiter awaiting an in-flight owner fetch must see the owner's
        error re-raised when raise_on_error=True.

        This is the path `_async_update_data(raise_on_error=True)` takes
        whenever the reconcile timer's own refresh is already in flight -
        the routine 30s/30s collision `async_refresh_sensors`'s docstring
        itself calls out - and it had zero test coverage: mutating the
        waiter branch to swallow instead of re-raise left the full 29/29
        new tests green.
        """
        # Exhaust the escalation window first so the very next fetch failure
        # raises immediately instead of being absorbed (see
        # `test_transient_fetch_errors_preserve_cache`), which keeps this
        # test to a single gated fetch.
        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            side_effect=UniFiConnectionError("Connection lost")
        )
        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._fetch_sensors()

        fetch_gate = asyncio.Event()

        async def slow_failing_fetch() -> list[Any]:
            await fetch_gate.wait()
            msg = "Connection lost"
            raise UniFiConnectionError(msg)

        patch_attribute(
            monkeypatch,
            require_not_none(coordinator.protect_client).sensors,
            "get_all",
            slow_failing_fetch,
        )

        # Owner: becomes the in-flight fetch, blocks on fetch_gate.
        owner_task = asyncio.create_task(coordinator.async_refresh_sensors())
        await asyncio.sleep(0)

        # Waiter: arrives while the owner is in flight, with
        # raise_on_error=True - this is the flag `_async_update_data`
        # passes on the scheduled-poll path.
        waiter_task = asyncio.create_task(
            coordinator.async_refresh_sensors(raise_on_error=True)
        )
        await asyncio.sleep(0)

        # Unblock the shared in-flight fetch; it fails.
        fetch_gate.set()

        with pytest.raises(UniFiConnectionError):
            await waiter_task

        # The owner itself was called with the default raise_on_error=False
        # (fire-and-forget / coalescing caller) and must not raise despite
        # seeing the same underlying error.
        await owner_task

    @pytest.mark.asyncio
    async def test_cleanup_on_unload_and_shutdown(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Timers, all three driver tasks, and the debouncer are torn down.

        Populates `_sensor_refresh_task`, `_sensor_reconcile_task`, AND
        `_sensor_reconnect_task` - not just the first - since
        `_cancel_sensor_background_task` is a shared helper invoked for
        all three from both `async_stop_websocket` and `async_shutdown`,
        but every prior version of this test only ever populated
        `_sensor_refresh_task`, so a regression that turned the helper
        into a no-op for the other two attrs left the full suite green.
        """
        coordinator._start_sensor_reconcile_timer()
        assert coordinator._unsub_sensor_reconcile is not None

        mock_refresh_task = MagicMock()
        mock_refresh_task.done.return_value = False
        mock_reconcile_task = MagicMock()
        mock_reconcile_task.done.return_value = False
        mock_reconnect_task = MagicMock()
        mock_reconnect_task.done.return_value = False
        coordinator._sensor_refresh_task = mock_refresh_task
        coordinator._sensor_reconcile_task = mock_reconcile_task
        coordinator._sensor_reconnect_task = mock_reconnect_task

        # Drive a real debounced call so the debouncer schedules its
        # actual trailing-edge `_timer_task` (a raw `hass.loop.call_later`
        # - see homeassistant.helpers.debounce.Debouncer), rather than
        # asserting against a mock: only pytest's own lingering-timer
        # teardown guard was catching a broken async_cancel()/
        # async_shutdown() before this test asserted it directly.
        await coordinator._sensor_reconnect_debouncer.async_call()
        assert coordinator._sensor_reconnect_debouncer._timer_task is not None

        await coordinator.async_stop_websocket()
        assert coordinator._unsub_sensor_reconcile is None
        mock_refresh_task.cancel.assert_called_once()
        mock_reconcile_task.cancel.assert_called_once()
        mock_reconnect_task.cancel.assert_called_once()
        assert coordinator._sensor_refresh_task is None
        assert coordinator._sensor_reconcile_task is None
        assert coordinator._sensor_reconnect_task is None
        # async_stop_websocket only cancels the pending trailing-edge timer
        # - it does not permanently disable the debouncer (see
        # async_shutdown below) - so no timer may survive it.
        assert coordinator._sensor_reconnect_debouncer._timer_task is None

        # Test async_shutdown
        coordinator._start_sensor_reconcile_timer()
        mock_refresh_task2 = MagicMock()
        mock_refresh_task2.done.return_value = False
        mock_reconcile_task2 = MagicMock()
        mock_reconcile_task2.done.return_value = False
        mock_reconnect_task2 = MagicMock()
        mock_reconnect_task2.done.return_value = False
        coordinator._sensor_refresh_task = mock_refresh_task2
        coordinator._sensor_reconcile_task = mock_reconcile_task2
        coordinator._sensor_reconnect_task = mock_reconnect_task2

        await coordinator.async_shutdown()
        assert coordinator._unsub_sensor_reconcile is None
        mock_refresh_task2.cancel.assert_called_once()
        mock_reconcile_task2.cancel.assert_called_once()
        mock_reconnect_task2.cancel.assert_called_once()
        assert coordinator._sensor_refresh_task is None
        assert coordinator._sensor_reconcile_task is None
        assert coordinator._sensor_reconnect_task is None
        # async_shutdown is the PERMANENT teardown - unlike async_cancel,
        # it sets _shutdown_requested so the debouncer can never schedule
        # another call for the rest of this coordinator instance's life.
        assert coordinator._sensor_reconnect_debouncer._shutdown_requested is True


class TestProtectNewerDoorStates:
    """Test protecting newer door states from older REST responses."""

    @pytest.mark.asyncio
    async def test_in_flight_websocket_update_not_overwritten_by_older_rest(
        self, coordinator: UnifiInsightsProtectCoordinator, monkeypatch
    ) -> None:
        """A WebSocket push during in-flight REST fetch must win over REST."""
        # Initial cached state: door is closed
        coordinator.data["sensors"] = {
            "sensor1": {
                "id": "sensor1",
                "name": "Front Door",
                "isOpened": False,
                "openStatusChangedAt": 1000,
                "batteryLevel": 80,
            }
        }

        # Slow REST fetch that returns older door closed state
        rest_gate = asyncio.Event()

        async def delayed_sensors_fetch() -> list[Any]:
            await rest_gate.wait()
            return [
                _create_mock_model(
                    {
                        "id": "sensor1",
                        "name": "Front Door",
                        "isOpened": False,
                        "openStatusChangedAt": 1000,
                        "batteryLevel": 90,  # fresh battery info from REST
                    }
                )
            ]

        patch_attribute(
            monkeypatch,
            require_not_none(coordinator.protect_client).sensors,
            "get_all",
            delayed_sensors_fetch,
        )

        # 1. Start REST fetch
        fetch_task = asyncio.create_task(coordinator._fetch_sensors())
        await asyncio.sleep(0.01)

        # 2. Door opens via WebSocket while REST is in-flight!
        send_device(
            coordinator,
            DEVICE_TYPE_SENSOR,
            {
                "id": "sensor1",
                "isOpened": True,
                "openStatusChangedAt": 1050,
            },
        )
        assert coordinator.data["sensors"]["sensor1"]["isOpened"] is True

        # 3. Older REST response finishes
        rest_gate.set()
        await fetch_task

        # Newer door state preserved, non-door fields like batteryLevel update!
        sensor = coordinator.data["sensors"]["sensor1"]
        assert sensor["isOpened"] is True
        assert sensor["openStatusChangedAt"] == 1050
        assert sensor["batteryLevel"] == 90

    @pytest.mark.asyncio
    async def test_ws_recency_wins_when_ws_frame_has_no_timestamp(
        self, coordinator: UnifiInsightsProtectCoordinator, monkeypatch
    ) -> None:
        """Branch 1 (WS-recency) must win even when the WS frame carries no
        `openStatusChangedAt` at all - and REST's own timestamp is
        strictly NEWER than the cache's.

        `test_in_flight_websocket_update_not_overwritten_by_older_rest`
        above is named for branch 1 but actually passes via branch 2 (its
        WS frame includes `openStatusChangedAt: 1050 > REST's 1000`, so
        the timestamp comparison alone satisfies every assertion there,
        proven by mutation testing: deleting branch 1 entirely left the
        full 1363-test suite green). This test's WS frame deliberately
        omits the timestamp field entirely, and REST's own timestamp
        (2000) is newer than the cache's (1000) - so branches 2/3 alone
        would let REST win. Only branch 1 (a real door/motion/tamper/leak
        WS frame arrived during this REST fetch) can make the WS write
        win here.
        """
        coordinator.data["sensors"] = {
            "sensor1": {
                "id": "sensor1",
                "name": "Front Door",
                "isOpened": False,
                "openStatusChangedAt": 1000,
            }
        }

        rest_gate = asyncio.Event()

        async def delayed_sensors_fetch() -> list[Any]:
            await rest_gate.wait()
            return [
                _create_mock_model(
                    {
                        "id": "sensor1",
                        "name": "Front Door",
                        "isOpened": False,
                        # Strictly newer than the cache's 1000 - branches
                        # 2/3 alone would let this REST response win.
                        "openStatusChangedAt": 2000,
                    }
                )
            ]

        patch_attribute(
            monkeypatch,
            require_not_none(coordinator.protect_client).sensors,
            "get_all",
            delayed_sensors_fetch,
        )

        # 1. Start REST fetch
        fetch_task = asyncio.create_task(coordinator._fetch_sensors())
        await asyncio.sleep(0.01)

        # 2. Door opens via WebSocket while REST is in-flight - no
        # openStatusChangedAt in this frame at all.
        send_device(
            coordinator,
            DEVICE_TYPE_SENSOR,
            {
                "id": "sensor1",
                "isOpened": True,
            },
        )
        assert coordinator.data["sensors"]["sensor1"]["isOpened"] is True

        # 3. REST response (with a newer timestamp than the ORIGINAL
        # cache, but no knowledge of the in-flight WS write) finishes.
        rest_gate.set()
        await fetch_task

        sensor = coordinator.data["sensors"]["sensor1"]
        assert sensor["isOpened"] is True, (
            "a WS frame that arrived during the REST fetch must win via "
            "WS-recency (branch 1) even with no timestamp of its own, "
            "against a REST response whose own timestamp is newer than "
            "the ORIGINAL cache - branches 2/3 alone cannot produce this "
            "result"
        )
        # The cache's original timestamp is what gets copied back (the WS
        # frame carried none), not REST's newer one.
        assert sensor["openStatusChangedAt"] == 1000

    @pytest.mark.asyncio
    async def test_rest_response_with_older_timestamp_does_not_overwrite_cache(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """If cached timestamp is newer than REST, cached door state is preserved."""
        coordinator.data["sensors"] = {
            "sensor1": {
                "id": "sensor1",
                "name": "Front Door",
                "isOpened": True,
                "openStatusChangedAt": 2000,
            }
        }

        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            return_value=[
                _create_mock_model(
                    {
                        "id": "sensor1",
                        "name": "Front Door Updated Name",
                        "isOpened": False,
                        "openStatusChangedAt": 1500,  # older than cached 2000
                    }
                )
            ]
        )

        await coordinator._fetch_sensors()

        sensor = coordinator.data["sensors"]["sensor1"]
        assert sensor["isOpened"] is True
        assert sensor["openStatusChangedAt"] == 2000
        assert sensor["name"] == "Front Door Updated Name"

    @pytest.mark.asyncio
    async def test_rest_response_with_newer_timestamp_updates_cache(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """If REST response has newer timestamp, it updates cache."""
        coordinator.data["sensors"] = {
            "sensor1": {
                "id": "sensor1",
                "name": "Front Door",
                "isOpened": True,
                "openStatusChangedAt": 1000,
            }
        }
        # A door frame landed before the fetch started.
        send_device(
            coordinator,
            DEVICE_TYPE_SENSOR,
            {"id": "sensor1", "isOpened": True, "openStatusChangedAt": 1000},
        )
        await asyncio.sleep(0.001)

        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            return_value=[
                _create_mock_model(
                    {
                        "id": "sensor1",
                        "name": "Front Door",
                        "isOpened": False,
                        "openStatusChangedAt": 2000,  # newer than cached 1000
                    }
                )
            ]
        )

        await coordinator._fetch_sensors()

        sensor = coordinator.data["sensors"]["sensor1"]
        assert sensor["isOpened"] is False
        assert sensor["openStatusChangedAt"] == 2000

    @pytest.mark.asyncio
    async def test_transient_fetch_errors_preserve_cache(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Transient network errors absorb and preserve cached sensors up to limit."""
        coordinator.data["sensors"] = {
            "sensor1": {"id": "sensor1", "name": "Front Door", "isOpened": True}
        }

        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            side_effect=UniFiConnectionError("Connection lost")
        )

        # 1st transient error absorbed, cache preserved
        await coordinator._fetch_sensors()
        assert coordinator.data["sensors"]["sensor1"]["isOpened"] is True
        assert coordinator._events.families["sensors"].fetch_errors == 1

        # 2nd transient error absorbed, cache preserved
        await coordinator._fetch_sensors()
        assert coordinator.data["sensors"]["sensor1"]["isOpened"] is True
        assert coordinator._events.families["sensors"].fetch_errors == 2

        # 3rd transient error absorbed - still within
        # MAX_CONSECUTIVE_EMPTY_FETCHES (3), cache still preserved.
        await coordinator._fetch_sensors()
        assert coordinator.data["sensors"]["sensor1"]["isOpened"] is True
        assert coordinator._events.families["sensors"].fetch_errors == 3

        # 4th transient error crosses the escalation boundary: bounded
        # absorption means this one must escape as a real failure instead
        # of being silently swallowed forever.
        with pytest.raises(UniFiConnectionError):
            await coordinator._fetch_sensors()
        assert coordinator._events.families["sensors"].fetch_errors == 4

    @pytest.mark.asyncio
    async def test_auth_error_triggers_reauth_failure(
        self, hass: HomeAssistant, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Authentication error raises ConfigEntryAuthFailed on every path:

        directly from `_fetch_sensors`, through the coalescing wrapper
        `async_refresh_sensors`, and - critically - through the
        `_handle_sensor_reconcile_interval` background-task timer path,
        where it must reach `config_entry.async_start_reauth` rather than
        vanishing as an unhandled background-task traceback.
        """
        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            side_effect=UniFiAuthenticationError("Invalid credentials")
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._fetch_sensors()

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator.async_refresh_sensors()

        reauth_mock = MagicMock()
        with patch.object(coordinator.config_entry, "async_start_reauth", reauth_mock):
            coordinator._handle_sensor_reconcile_interval()
            await hass.async_block_till_done()

        reauth_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_websocket_reconnect_auth_error_triggers_reauth(
        self, hass: HomeAssistant, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """A devices-stream reconnect that hits an auth failure must route
        to reauth via `config_entry.async_start_reauth`, not vanish as an
        unhandled background-task traceback (finding 5, WS-reconnect path).
        """
        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            side_effect=UniFiAuthenticationError("Invalid credentials")
        )

        reauth_mock = MagicMock()
        with patch.object(coordinator.config_entry, "async_start_reauth", reauth_mock):
            coordinator._on_websocket_connection_state_change("devices", connected=True)
            await hass.async_block_till_done()

        reauth_mock.assert_called_once()


class TestGateStampDiscriminatesFrameContent:
    """Round-4 review finding 1: the per-group WS-recency gate must only

    stamp WebSocket recency for the specific group(s) a frame actually
    carries, not for every sensor WS frame regardless of content. Mutating
    the per-group `isdisjoint` check in `protect_events` to an
    unconditional stamp must make this test fail.
    """

    @pytest.mark.asyncio
    async def test_telemetry_only_frame_does_not_stamp_or_suppress_rest(
        self, coordinator: UnifiInsightsProtectCoordinator, monkeypatch
    ) -> None:
        """A temperature-only WS frame must not gate-stamp any group."""
        coordinator.data["sensors"] = {
            "sensor1": {
                "id": "sensor1",
                "name": "Front Door",
                "isOpened": False,
                "openStatusChangedAt": 1000,
            }
        }

        rest_gate = asyncio.Event()

        async def delayed_sensors_fetch() -> list[Any]:
            await rest_gate.wait()
            return [
                _create_mock_model(
                    {
                        "id": "sensor1",
                        "name": "Front Door",
                        "isOpened": True,
                        "openStatusChangedAt": 2000,  # genuinely newer
                    }
                )
            ]

        patch_attribute(
            monkeypatch,
            require_not_none(coordinator.protect_client).sensors,
            "get_all",
            delayed_sensors_fetch,
        )

        fetch_task = asyncio.create_task(coordinator._fetch_sensors())
        await asyncio.sleep(0.01)

        # Real dispatch path (not poking coordinator.data or internals
        # directly) - a frame carrying ONLY telemetry, no door/motion/
        # tamper/leak field at all.
        send_device(
            coordinator,
            DEVICE_TYPE_SENSOR,
            {"id": "sensor1", "temperature": 22.5},
        )

        rest_gate.set()
        await fetch_task

        sensor = coordinator.data["sensors"]["sensor1"]
        assert sensor["isOpened"] is True, (
            "a pure telemetry frame must not gate-stamp any group, so "
            "REST's genuinely newer door transition must win"
        )
        assert sensor["openStatusChangedAt"] == 2000
        assert not coordinator._events.sensors["sensor1"].ws_seen, (
            "a frame matching no preserved-state group must leave the "
            "sensor without any WebSocket recency tracking"
        )

    @pytest.mark.asyncio
    async def test_motion_only_frame_does_not_suppress_newer_rest_door_transition(
        self, coordinator: UnifiInsightsProtectCoordinator, monkeypatch
    ) -> None:
        """Round-4 review finding 5 regression guard.

        Before per-group field sets, ANY WS frame carrying a door/motion/
        tamper/leak field - including a motion-only frame - stamped a
        SINGLE shared per-sensor WebSocket timestamp, and the merge
        then copied back ALL FOUR groups'
        fields together. A UP-Sense shares one device id across door/
        motion/environment sensing, with motion firing constantly at an
        entryway, so a motion-only frame mid-fetch could suppress a
        genuinely newer REST door transition for a poll. Per-group fields
        mean a motion-only frame can only ever justify preserving motion.
        """
        coordinator.data["sensors"] = {
            "sensor1": {
                "id": "sensor1",
                "name": "Front Door",
                "isOpened": False,
                "openStatusChangedAt": 1000,
                "isMotionDetected": False,
            }
        }

        rest_gate = asyncio.Event()

        async def delayed_sensors_fetch() -> list[Any]:
            await rest_gate.wait()
            return [
                _create_mock_model(
                    {
                        "id": "sensor1",
                        "name": "Front Door",
                        "isOpened": True,
                        "openStatusChangedAt": 5000,  # newer door transition
                        "isMotionDetected": False,
                    }
                )
            ]

        patch_attribute(
            monkeypatch,
            require_not_none(coordinator.protect_client).sensors,
            "get_all",
            delayed_sensors_fetch,
        )

        fetch_task = asyncio.create_task(coordinator._fetch_sensors())
        await asyncio.sleep(0.01)

        # Motion-only WS frame mid-fetch - no door field at all.
        send_device(
            coordinator,
            DEVICE_TYPE_SENSOR,
            {"id": "sensor1", "isMotionDetected": True},
        )

        rest_gate.set()
        await fetch_task

        sensor = coordinator.data["sensors"]["sensor1"]
        assert sensor["isOpened"] is True, (
            "a motion-only WS frame must not suppress a genuinely newer "
            "REST door transition - door and motion are decoupled per-group"
        )
        assert sensor["openStatusChangedAt"] == 5000
        # Motion itself IS legitimately preserved - its own group's
        # WS-recency fired for the motion frame that just arrived.
        assert sensor["isMotionDetected"] is True


class TestDoorStatePreservationIsBounded:
    """Finding 1 (BLOCKER): preservation must not wedge the cache forever.

    The door timestamp preservation in `protect_events` can re-seed its own
    "cache is newer" signal every poll. Both branches that
    can wedge - "REST omits the timestamp" and "cached timestamp always
    compares newer" - must give way to REST after
    `MAX_DOOR_STATE_PRESERVE_POLLS` consecutive preserves.
    """

    @pytest.mark.asyncio
    async def test_bounded_when_rest_omits_timestamp(
        self, coordinator: UnifiInsightsProtectCoordinator, monkeypatch
    ) -> None:
        """REST never reporting a timestamp must not wedge the door forever."""
        coordinator.data["sensors"] = {
            "sensor1": {
                "id": "sensor1",
                "name": "Front Door",
                "isOpened": True,
                "openStatusChangedAt": 1000,
            }
        }

        async def rest_without_timestamp() -> list[Any]:
            return [
                _create_mock_model(
                    {"id": "sensor1", "name": "Front Door", "isOpened": False}
                )
            ]

        patch_attribute(
            monkeypatch,
            require_not_none(coordinator.protect_client).sensors,
            "get_all",
            rest_without_timestamp,
        )

        for _ in range(10):
            await coordinator._fetch_sensors()

        sensor = coordinator.data["sensors"]["sensor1"]
        assert sensor["isOpened"] is False, (
            "REST must eventually win once preservation exceeds "
            "MAX_DOOR_STATE_PRESERVE_POLLS - the cache must not wedge the "
            "door open forever just because REST omits the timestamp"
        )

    @pytest.mark.asyncio
    async def test_bounded_when_cached_timestamp_always_compares_newer(
        self, coordinator: UnifiInsightsProtectCoordinator, monkeypatch
    ) -> None:
        """A REST timestamp stuck below the cache must not wedge forever."""
        coordinator.data["sensors"] = {
            "sensor1": {
                "id": "sensor1",
                "name": "Front Door",
                "isOpened": True,
                "openStatusChangedAt": 5000,
            }
        }

        async def rest_stuck_below_cache() -> list[Any]:
            # A fresh dict every call, so no call can see another's answer.
            return [
                _create_mock_model(
                    {
                        "id": "sensor1",
                        "name": "Front Door",
                        "isOpened": False,
                        "openStatusChangedAt": 4000,  # always older than cached
                    }
                )
            ]

        patch_attribute(
            monkeypatch,
            require_not_none(coordinator.protect_client).sensors,
            "get_all",
            rest_stuck_below_cache,
        )

        for _ in range(10):
            await coordinator._fetch_sensors()

        sensor = coordinator.data["sensors"]["sensor1"]
        assert sensor["isOpened"] is False, (
            "A REST timestamp that never advances past the cache must not "
            "wedge the door state forever - preservation is bounded by "
            "MAX_DOOR_STATE_PRESERVE_POLLS"
        )

    @pytest.mark.asyncio
    async def test_preserve_count_resets_when_rest_wins_normally(
        self, coordinator: UnifiInsightsProtectCoordinator, monkeypatch
    ) -> None:
        """A genuine REST win resets the bounded-preservation counter."""
        coordinator.data["sensors"] = {
            "sensor1": {
                "id": "sensor1",
                "name": "Front Door",
                "isOpened": True,
                "openStatusChangedAt": 1000,
            }
        }

        # Two preserves (REST always reports an older timestamp).
        async def rest_always_older() -> list[Any]:
            return [
                _create_mock_model(
                    {
                        "id": "sensor1",
                        "name": "Front Door",
                        "isOpened": False,
                        "openStatusChangedAt": 500,
                    }
                )
            ]

        patch_attribute(
            monkeypatch,
            require_not_none(coordinator.protect_client).sensors,
            "get_all",
            rest_always_older,
        )
        await coordinator._fetch_sensors()
        await coordinator._fetch_sensors()
        assert coordinator._events.sensors["sensor1"].ts_preserve_polls == 2

        # REST reports a genuinely newer timestamp: it wins and the
        # bounded-preservation counter resets to zero.
        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            return_value=[
                _create_mock_model(
                    {
                        "id": "sensor1",
                        "name": "Front Door",
                        "isOpened": False,
                        "openStatusChangedAt": 2000,
                    }
                )
            ]
        )
        await coordinator._fetch_sensors()
        assert coordinator.data["sensors"]["sensor1"]["isOpened"] is False
        assert coordinator._events.sensors["sensor1"].ts_preserve_polls == 0

    @pytest.mark.asyncio
    async def test_ws_recency_cap_latches_instead_of_oscillating(
        self, coordinator: UnifiInsightsProtectCoordinator, monkeypatch
    ) -> None:
        """Round-4 review finding 6: a sustained WS-recency condition must

        resolve ONCE, not flip every MAX_WS_RECENCY_PRESERVE_POLLS-th poll.

        REST persistently reports the door closed while a genuine
        WebSocket door-open frame lands inside every fetch window -
        simulating a controller whose REST endpoint never reflects the
        real, currently-true (WS-reported) door state. Without the latch,
        hitting the cap reset the counter to 0, so WS won the cache back
        for another MAX_WS_RECENCY_PRESERVE_POLLS polls, then lost it
        again for one poll, repeating forever - a spurious flip on
        contacts that drive auto-lock automations. With the latch, once
        the cap trips, REST keeps winning on every subsequent poll (even
        though the WS frame keeps arriving) until REST's own report
        agrees with the cache.
        """
        coordinator.data["sensors"] = {
            "sensor1": {
                "id": "sensor1",
                "name": "Front Door",
                "isOpened": True,
                "openStatusChangedAt": 1000,
            }
        }

        async def rest_reports_closed() -> list[Any]:
            return [
                _create_mock_model(
                    {
                        "id": "sensor1",
                        "name": "Front Door",
                        "isOpened": False,
                        # Deliberately BIGGER than every WS timestamp used
                        # below, so branches 2/3 (door-timestamp
                        # comparison) never independently preserve once
                        # branch 1 (WS-recency) is latched out - this test
                        # isolates branch 1's latch behaviour specifically.
                        "openStatusChangedAt": 50_000,
                    }
                )
            ]

        async def poll_with_inflight_ws_door_frame(ts: int) -> None:
            rest_gate = asyncio.Event()

            async def delayed() -> list[Any]:
                await rest_gate.wait()
                return await rest_reports_closed()

            patch_attribute(
                monkeypatch,
                require_not_none(coordinator.protect_client).sensors,
                "get_all",
                delayed,
            )
            fetch_task = asyncio.create_task(coordinator._fetch_sensors())
            await asyncio.sleep(0.01)
            send_device(
                coordinator,
                DEVICE_TYPE_SENSOR,
                {"id": "sensor1", "isOpened": True, "openStatusChangedAt": ts},
            )
            rest_gate.set()
            await fetch_task

        # Polls 1..MAX_WS_RECENCY_PRESERVE_POLLS: WS-recency preserves
        # (cap not yet hit); the door stays open.
        for i in range(MAX_WS_RECENCY_PRESERVE_POLLS):
            await poll_with_inflight_ws_door_frame(1000 + i)
            assert coordinator.data["sensors"]["sensor1"]["isOpened"] is True

        # Poll MAX_WS_RECENCY_PRESERVE_POLLS + 1: the cap trips. REST wins
        # for the first time.
        await poll_with_inflight_ws_door_frame(1000 + MAX_WS_RECENCY_PRESERVE_POLLS)
        assert coordinator.data["sensors"]["sensor1"]["isOpened"] is False, (
            "REST must win once the WS-recency cap is hit"
        )

        # The SAME sustained condition keeps arriving for several more
        # polls. Without the latch this would flip back to True at the
        # very next poll (fresh counter) and stay True for another
        # MAX_WS_RECENCY_PRESERVE_POLLS polls.
        for i in range(4):
            await poll_with_inflight_ws_door_frame(3000 + i)
            assert coordinator.data["sensors"]["sensor1"]["isOpened"] is False, (
                "the latched WS-recency cap must keep REST winning every "
                "subsequent poll, not flip back to the WS value every "
                f"{MAX_WS_RECENCY_PRESERVE_POLLS} polls"
            )

        # REST's own report catches up to the (live, WS-written) true
        # state - the latch clears once cached and REST agree.
        async def rest_reports_open() -> list[Any]:
            return [
                _create_mock_model(
                    {
                        "id": "sensor1",
                        "name": "Front Door",
                        "isOpened": True,
                        # Bigger than the closed-phase REST timestamp
                        # (50_000) so branch 2/3 correctly recognizes this
                        # as a newer REST report rather than mistaking the
                        # stale-but-numerically-larger closed timestamp
                        # for "cache is still newer".
                        "openStatusChangedAt": 99_999,
                    }
                )
            ]

        patch_attribute(
            monkeypatch,
            require_not_none(coordinator.protect_client).sensors,
            "get_all",
            rest_reports_open,
        )
        await coordinator._fetch_sensors()
        await coordinator._fetch_sensors()
        assert coordinator.data["sensors"]["sensor1"]["isOpened"] is True
        assert "door" not in coordinator._events.sensors["sensor1"].ws_latched, (
            "the latch must clear once REST's own report agrees with the cache"
        )

    @pytest.mark.asyncio
    async def test_preserve_cap_hit_warns_once_then_debug(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
        monkeypatch,
    ) -> None:
        """Round-4 review finding 4: a sensor stuck at the door-state

        preservation cap must WARNING once, then fall to DEBUG for every
        subsequent cap-hit - not re-WARNING every time.
        """
        caplog.set_level(
            logging.DEBUG,
            logger="custom_components.unifi_insights.coordinators.protect",
        )

        coordinator.data["sensors"] = {
            "sensor1": {
                "id": "sensor1",
                "name": "Front Door",
                "isOpened": True,
                "openStatusChangedAt": 1000,
            }
        }

        async def rest_fixed_older() -> list[Any]:
            return [
                _create_mock_model(
                    {
                        "id": "sensor1",
                        "name": "Front Door",
                        "isOpened": False,
                        "openStatusChangedAt": 500,  # always older than cache
                    }
                )
            ]

        patch_attribute(
            monkeypatch,
            require_not_none(coordinator.protect_client).sensors,
            "get_all",
            rest_fixed_older,
        )

        # First cap-hit: WARNING. (REST always reporting the same older
        # timestamp is self-resolving after one cap-trip - see
        # `test_bounded_when_cached_timestamp_always_compares_newer` - so
        # a plain sustained-condition loop only trips this once.)
        for _ in range(MAX_DOOR_STATE_PRESERVE_POLLS + 1):
            await coordinator._fetch_sensors()

        # A genuine WS door frame - NOT in-flight during a fetch - directly
        # re-establishes a cached timestamp newer than REST's fixed value.
        # By the time the next `_fetch_sensors()` call captures its own
        # `fetch_start_time`, this write is already in the past, so only
        # the timestamp-comparison branch (2/3) can fire from it, not
        # WS-recency (branch 1) - isolating the door-timestamp cap's
        # warn-once behaviour specifically.
        send_device(
            coordinator,
            DEVICE_TYPE_SENSOR,
            {"id": "sensor1", "isOpened": True, "openStatusChangedAt": 2000},
        )
        await asyncio.sleep(0.001)

        # Second cap-hit: DEBUG, not WARNING again.
        for _ in range(MAX_DOOR_STATE_PRESERVE_POLLS + 1):
            await coordinator._fetch_sensors()

        cap_hit_records = [
            record
            for record in caplog.records
            if "door state preservation" in record.message and "cap" in record.message
        ]
        assert len(cap_hit_records) >= 2, (
            f"expected at least 2 cap-hit log records, got "
            f"{len(cap_hit_records)}: {[r.message for r in caplog.records]}"
        )
        assert cap_hit_records[0].levelname == "WARNING"
        assert cap_hit_records[1].levelname == "DEBUG"


class TestTimestampNormalization:
    """Findings 2 and 3: timestamps must be normalized to a common unit

    before comparison, not compared as raw numbers or matched only when
    both sides happen to share the same Python type.
    """

    @pytest.mark.asyncio
    async def test_millisecond_cache_normalized_against_second_epoch_rest(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """A ms-epoch cached value must not out-rank a newer s-epoch REST value.

        Finding 2: the numeric/numeric comparison branch was a raw
        `ts1 > ts2` with no millisecond normalization, so a chronologically
        OLDER cached value stored in milliseconds numerically dwarfs a
        genuinely NEWER value reported in seconds, forever.
        """
        older_time_s = 1_700_000_000  # ~2023
        newer_time_s = 1_800_000_000  # ~2027

        coordinator.data["sensors"] = {
            "sensor1": {
                "id": "sensor1",
                "name": "Front Door",
                "isOpened": True,
                "openStatusChangedAt": older_time_s * 1000,  # stored as ms-epoch
            }
        }

        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            return_value=[
                _create_mock_model(
                    {
                        "id": "sensor1",
                        "name": "Front Door",
                        "isOpened": False,
                        "openStatusChangedAt": newer_time_s,  # seconds-epoch
                    }
                )
            ]
        )

        await coordinator._fetch_sensors()

        sensor = coordinator.data["sensors"]["sensor1"]
        assert sensor["isOpened"] is False, (
            "REST's genuinely newer timestamp must win once both sides are "
            "normalized to the same unit"
        )

    @pytest.mark.asyncio
    async def test_int_cached_vs_datetime_rest_compares_correctly(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Mismatched timestamp types must still compare correctly.

        Finding 3: `_compare_timestamps` had no (int, datetime) branch, so
        it returned None and fell through to a line that treats "REST is
        present" as "REST wins" - silently flipping the door to a stale
        REST value whenever the two sides happened to be different types.
        """
        newer_epoch = 1_800_000_000
        older_dt = datetime.fromtimestamp(1_700_000_000, tz=UTC)

        coordinator.data["sensors"] = {
            "sensor1": {
                "id": "sensor1",
                "name": "Front Door",
                "isOpened": True,
                "openStatusChangedAt": newer_epoch,  # int, genuinely newer
            }
        }

        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            return_value=[
                _create_mock_model(
                    {
                        "id": "sensor1",
                        "name": "Front Door",
                        "isOpened": False,
                        "openStatusChangedAt": older_dt,  # datetime, older
                    }
                )
            ]
        )

        await coordinator._fetch_sensors()

        sensor = coordinator.data["sensors"]["sensor1"]
        assert sensor["isOpened"] is True, (
            "cached int timestamp is chronologically newer than REST's "
            "datetime timestamp; mismatched types must not cause the "
            "comparator to silently treat REST as newer"
        )
        assert sensor["openStatusChangedAt"] == newer_epoch

    @pytest.mark.asyncio
    async def test_datetime_cached_vs_str_rest_compares_correctly(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """A naive-vs-aware / datetime-vs-str pairing must still compare."""
        newer_dt = datetime.fromtimestamp(1_800_000_000, tz=UTC)
        older_iso = datetime.fromtimestamp(1_700_000_000, tz=UTC).isoformat()

        coordinator.data["sensors"] = {
            "sensor1": {
                "id": "sensor1",
                "name": "Front Door",
                "isOpened": True,
                "openStatusChangedAt": newer_dt,
            }
        }

        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            return_value=[
                _create_mock_model(
                    {
                        "id": "sensor1",
                        "name": "Front Door",
                        "isOpened": False,
                        "openStatusChangedAt": older_iso,
                    }
                )
            ]
        )

        await coordinator._fetch_sensors()

        sensor = coordinator.data["sensors"]["sensor1"]
        assert sensor["isOpened"] is True


class TestSensorModelTimestampCoercion:
    """Finding 3 (model layer): the Sensor model must coerce timestamp

    payloads to int milliseconds at the pydantic ingestion boundary rather
    than accept a loose `datetime | int | float` union, since the
    coordinator-side comparator needs a single normalizable type and the
    controller has been observed emitting all of int, ISO string, and
    (via some payload shapes) datetime for these fields.
    """

    def test_int_passes_through_unchanged(self) -> None:
        sensor = Sensor(id="s1", mac="aa:bb", openStatusChangedAt=1_700_000_000_000)
        assert sensor.open_status_changed_at == 1_700_000_000_000

    def test_iso_string_is_coerced_to_int_millis(self) -> None:
        sensor = Sensor.model_validate(
            {
                "id": "s1",
                "mac": "aa:bb",
                "openStatusChangedAt": "2023-11-14T22:13:20+00:00",
            }
        )
        assert isinstance(sensor.open_status_changed_at, int)
        assert sensor.open_status_changed_at == 1_700_000_000_000

    def test_datetime_is_coerced_to_int_millis(self) -> None:
        dt = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
        sensor = Sensor.model_validate(
            {"id": "s1", "mac": "aa:bb", "motionDetectedAt": dt}
        )
        assert isinstance(sensor.motion_detected_at, int)
        assert sensor.motion_detected_at == 1_700_000_000_000

    def test_naive_datetime_is_assumed_utc(self) -> None:
        dt = datetime(2023, 11, 14, 22, 13, 20)  # noqa: DTZ001 - naive on purpose
        sensor = Sensor.model_validate(
            {"id": "s1", "mac": "aa:bb", "openStatusChangedAt": dt}
        )
        assert sensor.open_status_changed_at == 1_700_000_000_000

    def test_unparseable_string_does_not_raise_validation_error(self) -> None:
        """An unparseable payload must not drop the entire sensor.

        A bare `int | None` annotation would raise ValidationError here,
        which propagates out of `sensors.get_all()` and drops this sensor
        from the whole fetch - strictly worse than a missing timestamp.
        """
        sensor = Sensor.model_validate(
            {"id": "s1", "mac": "aa:bb", "openStatusChangedAt": "not-a-timestamp"}
        )
        assert sensor.open_status_changed_at is None

    def test_none_passes_through(self) -> None:
        sensor = Sensor(id="s1", mac="aa:bb")
        assert sensor.open_status_changed_at is None
        assert sensor.motion_detected_at is None


class TestBoundedEscalationPropagatesOnMainPoll:
    """Finding 4 (MAJOR): a sustained /sensors outage must fail the poll.

    `_fetch_sensors` re-raises past `MAX_CONSECUTIVE_EMPTY_FETCHES`, but
    `async_refresh_sensors`'s own `except Exception: debug-log` swallowed it,
    and `_async_update_data` called it with `notify=False` - so a sustained
    outage never marked the scheduled poll failed.
    """

    @pytest.mark.asyncio
    async def test_sustained_sensor_outage_fails_the_poll(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """After the escalation boundary, `_async_update_data` must raise."""
        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            side_effect=UniFiConnectionError("Connection lost")
        )

        # First MAX_CONSECUTIVE_EMPTY_FETCHES polls are absorbed.
        for _ in range(3):
            await coordinator._async_update_data()

        # The next poll crosses the escalation boundary and must fail.
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_sustained_sensor_auth_failure_triggers_reauth(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """An auth failure via the main poll's sensors fetch must raise

        ConfigEntryAuthFailed (triggering reauth), not get relabeled as
        UpdateFailed by `_async_update_data`'s generic exception handler.
        """
        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            side_effect=UniFiAuthenticationError("Invalid credentials")
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()


class TestReconnectStormBounded:
    """Finding 6 (MAJOR): the owner refresh loop and devices-reconnect

    handler must both be bounded - an unbounded `while True` combined with
    a flapping devices WS stream can otherwise trigger unbounded
    back-to-back REST /sensors calls.
    """

    @pytest.mark.asyncio
    async def test_refresh_loop_caps_iterations_under_sustained_pending(
        self, coordinator: UnifiInsightsProtectCoordinator, monkeypatch
    ) -> None:
        """The owner loop must stop even if a caller keeps re-marking pending."""
        call_count = 0

        async def instant_get_all() -> list[Any]:
            nonlocal call_count
            call_count += 1
            # Re-mark pending on every fetch (but stop after 9, so the
            # unbounded pre-fix loop terminates instead of hanging the test).
            if call_count < 9:
                coordinator._sensor_refresh_pending = True
            return [_create_mock_model({"id": "sensor1", "isOpened": False})]

        patch_attribute(
            monkeypatch,
            require_not_none(coordinator.protect_client).sensors,
            "get_all",
            instant_get_all,
        )

        await coordinator.async_refresh_sensors()

        assert call_count <= 2, (
            "the owner refresh loop must be capped, not spin for every "
            "pending re-mark from a sustained stream of callers"
        )

    @pytest.mark.asyncio
    async def test_flapping_devices_reconnect_is_debounced(
        self,
        hass: HomeAssistant,
        coordinator: UnifiInsightsProtectCoordinator,
        monkeypatch,
    ) -> None:
        """A flapping devices stream must not trigger unbounded REST calls."""
        call_count = 0

        async def fast_get_all() -> list[Any]:
            nonlocal call_count
            call_count += 1
            return [_create_mock_model({"id": "sensor1", "isOpened": False})]

        patch_attribute(
            monkeypatch,
            require_not_none(coordinator.protect_client).sensors,
            "get_all",
            fast_get_all,
        )
        coordinator._sensor_reconnect_debouncer.cooldown = 0.02

        # 20 rapid devices-stream reconnects, as during a flapping WS
        # connection or a controller upgrade.
        for _ in range(20):
            coordinator._on_websocket_connection_state_change("devices", connected=True)
        await hass.async_block_till_done()

        # Only the immediate (leading-edge) call has run so far.
        assert call_count == 1

        # Let the debounce cooldown elapse for the single trailing-edge
        # follow-up call.
        await asyncio.sleep(0.05)
        await hass.async_block_till_done()

        assert call_count == 2, (
            "20 rapid reconnects must coalesce into a leading call plus one "
            "trailing follow-up, not 20 back-to-back REST calls"
        )


class TestSensorTrackerCleanup:
    """Finding 12/13 (MINOR): per-sensor tracking dicts must be pruned on

    device removal, and cancelled background task references must be
    nulled so a later caller cannot await an already-cancelling task.
    """

    @pytest.mark.asyncio
    async def test_stale_sensor_removal_prunes_ws_and_preserve_trackers(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Evicting a sensor from the registry must prune ALL its tracking.

        WebSocket recency, both preserve counters, the recency latch and the
        warn-once flag all go, so a re-discovered sensor id starts completely
        fresh, never inheriting count/latch/warning state from before its
        eviction.
        """
        coordinator._events = replace(
            coordinator._events,
            sensors={
                "sensor_gone": SensorTracking(
                    ws_seen={"door": time.monotonic()},
                    ts_preserve_polls=2,
                    ws_preserve_polls={"door": 5},
                    ws_latched=frozenset({"door"}),
                    cap_warned=True,
                )
            },
        )
        coordinator.data["sensors"] = {"sensor_gone": {"id": "sensor_gone"}}
        coordinator._apply(PollCompleted())
        coordinator.data["sensors"] = {}

        with patch(
            "custom_components.unifi_insights.coordinators.base.dr.async_get"
        ) as mock_registry:
            mock_device = MagicMock()
            mock_device.id = "device_entry_id"
            set_mock_device_lookup(mock_registry.return_value, mock_device)

            for _ in range(MAX_CONSECUTIVE_MISSING_POLLS + 1):
                coordinator._apply(PollCompleted())

            mock_registry.return_value.async_update_device.assert_called()

        assert "sensor_gone" not in coordinator._events.sensors

        # Clean re-adoption: the sensor reports back in, and none of the
        # pruned tracking reappears on its own.
        coordinator.data["sensors"] = {"sensor_gone": {"id": "sensor_gone"}}
        coordinator._apply(PollCompleted())

        assert "sensor_gone" not in coordinator._events.sensors

    @pytest.mark.asyncio
    async def test_cancelled_refresh_task_ref_is_nulled(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Stop/shutdown must null `_sensor_refresh_task` after cancelling it."""
        mock_task = MagicMock()
        mock_task.done.return_value = False
        coordinator._sensor_refresh_task = mock_task

        await coordinator.async_stop_websocket()

        mock_task.cancel.assert_called_once()
        assert coordinator._sensor_refresh_task is None


class TestRefreshWaiterPaths:
    """Paths taken by a caller that arrives while a sensor fetch is in flight."""

    @pytest.mark.asyncio
    async def test_waiter_propagates_auth_failure_from_in_flight_fetch(
        self, coordinator: UnifiInsightsProtectCoordinator, monkeypatch
    ) -> None:
        """An auth failure must reach a waiter, not be swallowed as a blip.

        The waiter branch catches `Exception` to absorb transient errors,
        and `ConfigEntryAuthFailed` is an `Exception` - without its explicit
        re-raise a waiter would log the failure at debug and return, so a
        revoked API key surfacing mid-collision would never start reauth.
        """
        fetch_gate = asyncio.Event()

        async def slow_auth_failure() -> list[Any]:
            await fetch_gate.wait()
            msg = "Invalid credentials"
            raise UniFiAuthenticationError(msg)

        patch_attribute(
            monkeypatch,
            require_not_none(coordinator.protect_client).sensors,
            "get_all",
            slow_auth_failure,
        )

        owner_task = asyncio.create_task(coordinator.async_refresh_sensors())
        await asyncio.sleep(0)
        waiter_task = asyncio.create_task(coordinator.async_refresh_sensors())
        await asyncio.sleep(0)

        fetch_gate.set()

        with pytest.raises(ConfigEntryAuthFailed):
            await waiter_task
        with pytest.raises(ConfigEntryAuthFailed):
            await owner_task

    @pytest.mark.asyncio
    async def test_waiter_with_notify_false_does_not_notify_listeners(
        self, coordinator: UnifiInsightsProtectCoordinator, monkeypatch
    ) -> None:
        """`notify=False` must hold on the waiter branch too.

        `_async_update_data` passes `notify=False` because the coordinator
        notifies once with the full poll result; a waiter that notified
        anyway would push a partial update mid-poll.
        """
        fetch_gate = asyncio.Event()

        async def slow_fetch() -> list[Any]:
            await fetch_gate.wait()
            return []

        patch_attribute(
            monkeypatch,
            require_not_none(coordinator.protect_client).sensors,
            "get_all",
            slow_fetch,
        )

        with patch.object(coordinator, "async_update_listeners") as notify_mock:
            owner_task = asyncio.create_task(
                coordinator.async_refresh_sensors(notify=False)
            )
            await asyncio.sleep(0)
            waiter_task = asyncio.create_task(
                coordinator.async_refresh_sensors(notify=False)
            )
            await asyncio.sleep(0)

            fetch_gate.set()
            await waiter_task
            await owner_task

        notify_mock.assert_not_called()
