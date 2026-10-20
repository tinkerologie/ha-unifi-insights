# Copyright 2026 UniFi Insights contributors
"""Protect coordinator for UniFi Insights - handles Protect device data."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Final, Literal

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_time_interval
from pydantic import ValidationError
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiGlobalAlarmManagerError,
    UniFiNotFoundError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from unifi_official_api.exceptions import UniFiUnsupportedError
from unifi_official_api.protect.talkback import (
    TalkbackCodecError,
    TalkbackSamplingRateError,
    TalkbackSchemeError,
    TalkbackStream,
)
from unifi_official_api.validation import sanitized_validation_fields

from custom_components.unifi_insights.const import DOMAIN, SCAN_INTERVAL_PROTECT

from .base import CommandFailure, UnifiInsightsBaseCoordinator, async_execute_command
from .protect_events import (
    MAX_CONSECUTIVE_EMPTY_FETCHES,
    MAX_CONSECUTIVE_MISSING_POLLS,
    MAX_DOOR_STATE_PRESERVE_POLLS,
    MAX_WS_RECENCY_PRESERVE_POLLS,
    SIREN_STOP_GUARD_TTL,
    STALE_EVENT_TIMEOUT,
    CommandOverlay,
    Escalate,
    Fetched,
    Instant,
    Log,
    NoAnswer,
    NotFound,
    PollCompleted,
    PollStarted,
    PublishUpdate,
    RemoveDevice,
    RestBatch,
    SirenStopped,
    StreamReconnected,
    TransientError,
    begin_request,
    initial_state,
    normalize_camera,
    reconcile,
)
from .protect_ws import ProtectStreams, RateLimited, WebSocketConnectionStateCallback

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Mapping

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from unifi_official_api.network import UniFiNetworkClient
    from unifi_official_api.protect import UniFiProtectClient

    from .protect_events import (
        ReconcilerInput,
        ReconcilerState,
        RequestStamp,
        RestOutcome,
        Step,
    )
    from .protect_ws import Stream

type _DeviceFamily = Literal["fobs", "link_stations", "alarm_hubs", "sirens"]

_LOGGER = logging.getLogger(__name__)

# Re-exported: the reconciler's bounds are part of the coordinator's contract.
__all__ = [
    "GLOBAL_ALARM_MANAGER_PROBE_BACKOFF",
    "GLOBAL_ALARM_MANAGER_PROBE_WARN_AFTER",
    "GLOBAL_ALARM_MANAGER_REPROBE",
    "MAX_CONSECUTIVE_EMPTY_FETCHES",
    "MAX_CONSECUTIVE_MISSING_POLLS",
    "MAX_DOOR_STATE_PRESERVE_POLLS",
    "MAX_SENSOR_REFRESH_LOOP_ITERATIONS",
    "MAX_WS_RECENCY_PRESERVE_POLLS",
    "SENSOR_RECONNECT_DEBOUNCE_SECONDS",
    "SIREN_STOP_GUARD_TTL",
    "STALE_EVENT_TIMEOUT",
    "UNSUPPORTED_RESOURCE_RETRY",
    "UnifiInsightsProtectCoordinator",
]

# Coalescing window for a devices-stream reconnect refresh (see
# `_on_websocket_connection_state_change`). A flapping stream (e.g. during a
# controller firmware upgrade) can fire this handler many times per second;
# a short cooldown collapses that into one immediate refresh plus at most
# one trailing follow-up, instead of one REST /sensors call per flap.
SENSOR_RECONNECT_DEBOUNCE_SECONDS: Final = 5.0

# Hard cap on how many times `async_refresh_sensors`'s owner loop may
# re-fetch for a request that keeps arriving while the previous fetch is
# still in flight. Coalescing is intentionally "at most 2" per burst (see
# that method's docstring), not true single-flight; this cap prevents an
# unbroken stream of overlapping callers from turning that into an
# unbounded back-to-back REST call loop.
MAX_SENSOR_REFRESH_LOOP_ITERATIONS: Final = 2

# How long a Protect device family whose list endpoint answered 404 before it
# ever answered successfully (a Protect version that predates it) is left
# alone before it is probed again. Re-probing at all lets a Protect upgrade
# be picked up without a reload; doing it hourly rather than every poll keeps
# an old console from spending a request per family every 30s.
UNSUPPORTED_RESOURCE_RETRY: Final = timedelta(hours=1)

# How often the UniFi global alarm manager is asked about again once Protect
# has answered. It can be switched on or off in the UniFi app at any time, and
# while it is on the alarm panel would show a wrong state, so a change should
# not wait an hour to be noticed. The question is one read-only request, which
# makes about ten minutes cheap. The first question is asked at setup, so
# reloading the entry checks immediately.
GLOBAL_ALARM_MANAGER_REPROBE: Final = timedelta(minutes=10)

# After a probe that gave no answer (an error that is not the global alarm
# manager's own refusal) Protect is asked again after this, instead of on every
# poll and every reconcile tick, so a console that keeps failing the request is
# not hit with it 20 times in 10 minutes. A warning is logged once that many
# attempts in a row have given no answer.
GLOBAL_ALARM_MANAGER_PROBE_BACKOFF: Final = timedelta(minutes=5)
GLOBAL_ALARM_MANAGER_PROBE_WARN_AFTER: Final = 3


def _escalated(step: Step) -> bool:
    """Return whether the reconciler asked the caller to re-raise."""
    return any(isinstance(effect, Escalate) for effect in step.effects)


class UnifiInsightsProtectCoordinator(UnifiInsightsBaseCoordinator):
    """
    Coordinator for UniFi Protect device data (30 second updates + WebSocket).

    Handles:
    - Cameras (with streaming support)
    - Lights
    - Sensors
    - NVR
    - Viewers
    - Chimes
    - Liveviews
    - Fobs, link stations and alarm hubs (Protect 7.3.70 security devices)
    - Real-time events via WebSocket

    Every change to ``data`` from a frame, a poll answer, a siren stop or a
    command overlay goes through `_apply`, which hands it to the pure
    reconciler in `protect_events` and carries out the effects it returns.
    This class keeps the REST I/O and its error mapping, the family support
    bookkeeping, the global alarm manager probe, the timers and the WebSocket
    lifecycle.

    UNVALIDATED SCHEMA WARNING: the "events" WebSocket subscription has never
    run against real Protect traffic. Every frame-shape assumption in
    `protect_ws` and `protect_events` (envelope shape, which key carries the
    device id, whether "smartDetectZone" or "smartDetect" is the real
    smart-detect type) is a best-effort guess pending a real capture. The
    path degrades safely if a guess is wrong: it never raises into the
    WebSocket callback, it logs an unparseable frame once at WARNING then at
    DEBUG, and it never latches a binary sensor on indefinitely (see
    `STALE_EVENT_TIMEOUT`).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        network_client: UniFiNetworkClient,
        protect_client: UniFiProtectClient | None,
        entry: ConfigEntry,
        site_id: str = "default",
    ) -> None:
        """Initialize the Protect coordinator."""
        super().__init__(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=entry,
            name="protect",
            update_interval=SCAN_INTERVAL_PROTECT,
        )
        # Latches, sensor recency, tamper records, siren stop guards,
        # WebSocket generations and per-family counters, owned by the pure
        # reconciler (`protect_events`). Only `_apply` writes it.
        self._events: ReconcilerState = initial_state()
        # Families whose list endpoint has answered successfully at least
        # once. A 404 from one of these is a transient/removed-device signal
        # for the reconciler, not "this Protect is too old".
        self._answered_families: set[str] = set()
        # These failures are swallowed, so DataUpdateCoordinator cannot coalesce them.
        self._failed_families: set[str] = set()
        # family -> monotonic time before which it is not polled again,
        # after a 404 from a Protect version that predates the endpoint.
        # See UNSUPPORTED_RESOURCE_RETRY.
        self._unsupported_until: dict[str, float] = {}
        # Families already reported once as unsupported (logged at INFO the
        # first time only, not on every hourly re-probe).
        self._unsupported_logged: set[str] = set()
        # Monotonic time before which the global alarm manager is not asked
        # about again (see `_probe_global_alarm_manager`).
        self._global_alarm_manager_probe_after: float = 0.0
        # Consecutive probes that gave no answer, for the one warning.
        self._global_alarm_manager_probe_failures: int = 0
        self.data: dict[str, Any] = {
            "cameras": {},
            "lights": {},
            "sensors": {},
            "nvrs": {},
            "viewers": {},
            "chimes": {},
            # Keyed device_type + "s" (see DEVICE_TYPE_FOB and friends).
            "fobs": {},
            "link_stations": {},
            "alarm_hubs": {},
            "sirens": {},
            "doorlocks": {},
            "viewports": {},
            "liveviews": {},
            "protect_info": {},
            "events": {},
            # Whether the UniFi global alarm manager owns the alarm: True or
            # False once Protect has answered, None until then. Kept apart
            # from the NVR record, which every poll replaces.
            "global_alarm_manager": None,
            "last_update": None,
        }

        # Site ID used for WebSocket subscriptions (ignored for LOCAL REST
        # calls, kept here for REMOTE/cloud routing - see
        # ProtectWebSocket._subscribe_path).
        self._site_id = site_id

        # Populated by async_start_websocket(); the background task handles
        # are read by __init__.py's async_unload_entry to cancel the
        # WebSocket loops on unload/reload. Two independent subscriptions -
        # "devices" (websocket_task) and "events" (events_websocket_task) -
        # run concurrently on the same ProtectWebSocket instance.
        self.websocket_task: asyncio.Task[None] | None = None
        self.events_websocket_task: asyncio.Task[None] | None = None
        self._protect_websocket: Any = (
            self.protect_client.websocket if self.protect_client else None
        )
        # Frame parsing, warn-once logging and per-stream health (protect_ws).
        self._streams = ProtectStreams()
        self._on_devices_connection_state_change = callback(
            WebSocketConnectionStateCallback(
                self._on_websocket_connection_state_change, "devices"
            )
        )
        self._on_events_connection_state_change = callback(
            WebSocketConnectionStateCallback(
                self._on_websocket_connection_state_change, "events"
            )
        )

        # Independent sensor reconciliation: camera traffic resets coordinator
        # update interval via async_set_updated_data, postponing the 30s full
        # poll indefinitely. An independent timer guarantees sensor state
        # reconciliation every 30 seconds regardless of camera activity.
        self._sensor_reconcile_interval: timedelta = SCAN_INTERVAL_PROTECT
        self._unsub_sensor_reconcile: Callable[[], None] | None = None
        self._sensor_refresh_task: asyncio.Task[None] | None = None
        self._sensor_reconcile_task: asyncio.Task[None] | None = None
        self._sensor_reconnect_task: asyncio.Task[None] | None = None
        self._sensor_refresh_pending: bool = False
        # Debounces a devices-stream reconnect refresh (see
        # `_on_websocket_connection_state_change`): `immediate=True` runs
        # the first refresh right away, and coalesces any reconnects that
        # arrive during the cooldown into exactly one trailing follow-up -
        # instead of one REST /sensors call per flap of a stream that can
        # bounce many times a second during e.g. a controller upgrade.
        self._sensor_reconnect_debouncer: Debouncer[Coroutine[Any, Any, None]] = (
            Debouncer(
                hass,
                _LOGGER,
                cooldown=SENSOR_RECONNECT_DEBOUNCE_SECONDS,
                immediate=True,
                function=self._debounced_sensor_reconnect_refresh,
            )
        )
        # `Debouncer` schedules its trailing-edge cooldown via a raw
        # `hass.loop.call_later` (see homeassistant.helpers.debounce),
        # which - unlike `async_track_time_interval(cancel_on_shutdown=True)`
        # above - has no shutdown-cancellation of its own. `async_shutdown`/
        # `async_stop_websocket` already cancel it on the normal unload
        # path, but this coordinator can also be constructed directly in
        # tests without ever going through config-entry setup/unload; this
        # listener is the same safety net `cancel_on_shutdown` gives the
        # reconcile timer, so a scheduled cooldown can never outlive `hass`
        # itself and trip pytest-homeassistant-custom-component's
        # lingering-timer check.
        #
        # Registered via `entry.async_on_unload` (not called bare): the
        # unsub `hass.bus.async_listen_once` returns was previously
        # discarded, and the lambda closes over `self` - HA's
        # `_OneTimeListener` holds that closure (and therefore this whole
        # coordinator, including `self.data`) alive until
        # EVENT_HOMEASSISTANT_STOP actually fires, which in practice means
        # "never" for a coordinator that lives through a config-entry
        # reload. `async_on_unload` runs the unsub on every unload/reload,
        # not just final HA shutdown, closing that leak.
        self.config_entry.async_on_unload(
            hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP,
                lambda _event: self._sensor_reconnect_debouncer.async_shutdown(),
            )
        )

    async def async_start_websocket(self) -> None:
        """
        Start the real-time Protect WebSocket subscription.

        This is additive to the 30 second poll (SCAN_INTERVAL_PROTECT), which
        remains the fallback if the WebSocket is unavailable or drops - it is
        never removed by this method. Any failure here is logged and
        swallowed so a WebSocket problem never blocks integration setup or
        leaves the coordinator without its polling fallback.
        """
        if not self.protect_client or not self._protect_websocket:
            return
        if self.websocket_task is not None and not self.websocket_task.done():
            _LOGGER.debug("Protect coordinator: WebSocket already running")
            return

        try:
            host_id = await self.protect_client.get_host_id()
        except Exception as err:
            _LOGGER.warning(
                "Protect coordinator: Unable to resolve host_id for WebSocket "
                "subscription, falling back to %s polling only: %s",
                SCAN_INTERVAL_PROTECT,
                err,
            )
            return

        self.websocket_task = self.hass.async_create_background_task(
            self._protect_websocket.subscribe_with_callback(
                host_id,
                self._site_id,
                "devices",
                self._on_websocket_message,
                reconnect=True,
                on_connection_state_change=self._on_devices_connection_state_change,
            ),
            name=f"{DOMAIN}_protect_websocket",
        )
        # Second, independent subscription: without it no event frame ever
        # arrives, so `lastMotionStart`/`lastMotionEnd`/`lastSmartDetectTypes`
        # are never written and every motion/smart-detect binary sensor is
        # permanently OFF. Runs on the same
        # ProtectWebSocket instance as the devices subscription above -
        # `ProtectWebSocket._running` is a single shared gate, so a shared
        # `stop()` call (see async_stop_websocket) correctly ends both.
        self.events_websocket_task = self.hass.async_create_background_task(
            self._protect_websocket.subscribe_with_callback(
                host_id,
                self._site_id,
                "events",
                self._on_websocket_event_message,
                reconnect=True,
                on_connection_state_change=self._on_events_connection_state_change,
            ),
            name=f"{DOMAIN}_protect_websocket_events",
        )
        _LOGGER.debug(
            "Protect coordinator: WebSocket subscriptions started (host_id=%s, "
            "site_id=%s)",
            host_id,
            self._site_id,
        )
        self._start_sensor_reconcile_timer()

    async def async_stop_websocket(self) -> None:
        """
        Stop both WebSocket subscriptions and await their background tasks.

        Safe to call when the WebSocket was never started. Signals the
        `ProtectWebSocket` loop to stop reconnecting *before* cancelling the
        task, then awaits the cancellation - `cancel()` alone leaves the
        reconnect loop free to spin back up, and the loop being parked in
        `async for msg in ws` means `stop()` alone won't unblock it either;
        both are needed to avoid an orphaned WebSocket loop. `stop()` is
        called once (it flips a single flag shared by both subscriptions on
        the same ProtectWebSocket instance - see async_start_websocket) and
        applies to both the devices and events tasks, which are then each
        cancelled and awaited in turn using that same ordering.

        Registered with `entry.async_on_unload()` (covers a setup failure
        that happens after the WebSocket started but before setup
        completes) and also called directly from `async_unload_entry`'s
        normal unload path.
        """
        self._stop_sensor_reconcile_timer()
        self._cancel_sensor_background_task("_sensor_refresh_task")
        self._cancel_sensor_background_task("_sensor_reconcile_task")
        self._cancel_sensor_background_task("_sensor_reconnect_task")
        # Only cancels a pending trailing-edge timer - does not permanently
        # disable the debouncer, since `async_stop_websocket` can be called
        # while this coordinator instance otherwise stays alive (see
        # `async_shutdown` below for the permanent teardown).
        self._sensor_reconnect_debouncer.async_cancel()
        if self._protect_websocket:
            self._protect_websocket.stop()
        for task in (self.websocket_task, self.events_websocket_task):
            if task:
                task.cancel()
                if isinstance(task, asyncio.Task):
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

    async def async_shutdown(self) -> None:
        """Cancel background tasks and timers on shutdown."""
        self._stop_sensor_reconcile_timer()
        self._cancel_sensor_background_task("_sensor_refresh_task")
        self._cancel_sensor_background_task("_sensor_reconcile_task")
        self._cancel_sensor_background_task("_sensor_reconnect_task")
        self._sensor_reconnect_debouncer.async_shutdown()
        await super().async_shutdown()

    def _cancel_sensor_background_task(self, attr: str) -> None:
        """
        Cancel and null a tracked sensor background task attribute.

        `async_stop_websocket`/`async_shutdown` previously cancelled
        `_sensor_refresh_task` without clearing the reference, so a later
        `async_refresh_sensors` call could see a non-None, not-yet-done
        (cancelling) task and `await` it, taking a `CancelledError` instead
        of treating the slot as free. Nulling the reference here closes
        that window for every tracked sensor task, not just the inner fetch
        task - `_sensor_reconcile_task`/`_sensor_reconnect_task` are the
        "outer driver" tasks spawned by the reconcile timer and the
        devices-reconnect handler respectively.
        """
        task: asyncio.Task[None] | None = getattr(self, attr)
        if task is not None and not task.done():
            task.cancel()
        setattr(self, attr, None)

    @callback
    def _start_sensor_reconcile_timer(self) -> None:
        """Start independent sensor reconciliation timer."""
        if self._unsub_sensor_reconcile is not None or not self.protect_client:
            return
        self._unsub_sensor_reconcile = async_track_time_interval(
            self.hass,
            self._handle_sensor_reconcile_interval,
            self._sensor_reconcile_interval,
            cancel_on_shutdown=True,
        )

    @callback
    def _stop_sensor_reconcile_timer(self) -> None:
        """Stop independent sensor reconciliation timer."""
        if self._unsub_sensor_reconcile is not None:
            self._unsub_sensor_reconcile()
            self._unsub_sensor_reconcile = None

    @callback
    def _handle_sensor_reconcile_interval(self, _now: datetime | None = None) -> None:
        """
        Handle sensor reconciliation timer tick.

        A tick is skipped while the previous one is still running. A reconcile
        is several requests now (the NVR, the sensors and the sirens), and a
        console that stops answering holds each one for the client's 30 second
        timeout, so starting a new one every tick would pile up tasks that all
        wait on the same console. The one tracked task is also the one that is
        cancelled when the entry unloads.
        """
        previous = self._sensor_reconcile_task
        if previous is not None and not previous.done():
            _LOGGER.debug(
                "Protect coordinator: the previous reconcile is still running; "
                "skipping this tick"
            )
            return
        self._sensor_reconcile_task = self.config_entry.async_create_background_task(
            self.hass,
            self._reconcile_sensor_refresh(),
            name=f"{DOMAIN}_protect_sensor_reconciliation",
        )

    def _nvr_has_arm_mode(self) -> bool:
        """Return whether an NVR reports an arm mode (older firmware does not)."""
        return any(
            isinstance(nvr, dict) and isinstance(nvr.get("armMode"), dict)
            for nvr in self.data["nvrs"].values()
        )

    async def _reconcile_nvr_arm_mode(self) -> None:
        """
        Re-read the NVR's arm mode on the reconcile timer, outside the main poll.

        Protect changes it on its own: arming turns into armed when its
        countdown ends, and a breach is raised by an alarm. WebSocket frames
        from the whole console keep pushing the main poll back, so without
        this the alarm control panel could read stale for as long as the
        console is busy. The global alarm manager question is asked here too,
        for the same reason. Nothing is fetched for an NVR that reports no
        arm mode, and listeners are only notified when something changed.
        """
        if not self._nvr_has_arm_mode():
            return
        before = (self.data["nvrs"], self.data["global_alarm_manager"])
        await self._fetch_nvr()
        await self._probe_global_alarm_manager()
        if (self.data["nvrs"], self.data["global_alarm_manager"]) != before:
            self.async_update_listeners()

    async def _probe_global_alarm_manager(self) -> None:
        """
        Find out whether the UniFi global alarm manager owns the alarm.

        Protect's own arm state does not follow the global alarm manager, so
        while it is enabled the alarm control panel would show a false
        "disarmed". Protect refuses to list arm profiles then (a 400 naming
        the global alarm manager), and lists them otherwise, which makes that
        read-only request the way to tell. Only an NVR that reports an arm
        mode is asked. It is asked again after `GLOBAL_ALARM_MANAGER_REPROBE`.
        Any other failure answers nothing: the last known answer stands, and
        Protect is asked again after `GLOBAL_ALARM_MANAGER_PROBE_BACKOFF`. Until
        it answers there is no panel, which is why a run of failures is
        reported (see `_global_alarm_manager_probe_failed`).
        """
        if not self.protect_client or not self._nvr_has_arm_mode():
            return
        if time.monotonic() < self._global_alarm_manager_probe_after:
            return
        try:
            await self.protect_client.arm_profiles.get_all()
        except UniFiGlobalAlarmManagerError:
            enabled = True
        except Exception as err:
            self._global_alarm_manager_probe_failed(err)
            return
        else:
            enabled = False
        self._global_alarm_manager_probe_failures = 0
        self._global_alarm_manager_probe_after = (
            time.monotonic() + GLOBAL_ALARM_MANAGER_REPROBE.total_seconds()
        )
        self._set_global_alarm_manager(enabled=enabled)

    @callback
    def _global_alarm_manager_probe_failed(self, err: Exception) -> None:
        """Back off after a probe that gave no answer, and warn once in a run."""
        self._global_alarm_manager_probe_after = (
            time.monotonic() + GLOBAL_ALARM_MANAGER_PROBE_BACKOFF.total_seconds()
        )
        self._global_alarm_manager_probe_failures += 1
        if (
            self._global_alarm_manager_probe_failures
            != GLOBAL_ALARM_MANAGER_PROBE_WARN_AFTER
        ):
            _LOGGER.debug(
                "Protect coordinator: Could not tell whether the global alarm "
                "manager is enabled: %s",
                err,
            )
            return
        _LOGGER.warning(
            "Could not tell whether the UniFi global alarm manager is enabled "
            "after %d attempts (%s); %s",
            self._global_alarm_manager_probe_failures,
            err,
            "the Protect alarm panel keeps its last known state until Protect answers"
            if self.data["global_alarm_manager"] is False
            else "the Protect alarm panel stays hidden until Protect answers",
        )

    @callback
    def _set_global_alarm_manager(self, *, enabled: bool) -> None:
        """Store the answer, logging each change of it once."""
        previous = self.data["global_alarm_manager"]
        self.data = {**self.data, "global_alarm_manager": enabled}
        if enabled and previous is not True:
            _LOGGER.info(
                "UniFi global alarm manager is enabled; the Protect alarm panel "
                "is hidden because Protect's local arm state does not follow it"
            )
        elif not enabled and previous is True:
            _LOGGER.info(
                "UniFi global alarm manager is no longer enabled; the Protect "
                "alarm panel is available again"
            )

    @callback
    def mark_global_alarm_manager_enabled(self) -> None:
        """Record that Protect refused an arm or disarm because the manager is on."""
        if self.data["global_alarm_manager"] is True:
            return
        self._global_alarm_manager_probe_after = (
            time.monotonic() + GLOBAL_ALARM_MANAGER_REPROBE.total_seconds()
        )
        self._set_global_alarm_manager(enabled=True)
        self.async_update_listeners()

    async def _reconcile_sensor_refresh(self) -> None:
        """
        Run the periodic reconcile-timer refresh, routing auth failures to reauth.

        `async_refresh_sensors` re-raises `ConfigEntryAuthFailed` rather
        than swallowing it (see that method), which is correct for the
        `_async_update_data` poll path where the coordinator's own
        exception handling converts it into a reauth flow. But this
        coroutine only ever runs detached inside a background task (see
        `_handle_sensor_reconcile_interval`) - nothing above it will ever
        catch that exception, so without this wrapper it becomes an
        unhandled background-task traceback and reauth never fires.
        """
        await self._reconcile_nvr_arm_mode()
        try:
            await self.async_refresh_sensors()
        except ConfigEntryAuthFailed:
            self.config_entry.async_start_reauth(self.hass)
        await self._reconcile_sirens()

    async def _reconcile_sirens(self) -> None:
        """
        Re-read the sirens on the reconcile timer, outside the main poll.

        WebSocket frames from the whole console keep pushing the main poll
        back, so a siren whose state changed without a frame of its own (a
        run that ended, or one started from the UniFi app) would otherwise
        stay stale for as long as the console is busy. Listeners are only
        notified when something changed, so this adds no entity updates on a
        quiet console.
        """
        before = self.data["sirens"]
        await self._fetch_sirens()
        if self.data["sirens"] != before:
            self.async_update_listeners()

    async def _debounced_sensor_reconnect_refresh(self) -> None:
        """
        Run the devices-reconnect sensor refresh, routing auth failures to reauth.

        Used as the wrapped function for `_sensor_reconnect_debouncer` -
        see `_on_websocket_connection_state_change`. Same reasoning as
        `_reconcile_sensor_refresh`: this always runs detached from a
        background task, so `ConfigEntryAuthFailed` must be handled here or
        it is lost.
        """
        try:
            await self.async_refresh_sensors()
        except ConfigEntryAuthFailed:
            self.config_entry.async_start_reauth(self.hass)

    async def async_refresh_sensors(
        self, *, notify: bool = True, raise_on_error: bool = False
    ) -> None:
        """
        Refresh sensor data independently with request coalescing.

        Coalescing is "at most 2 REST calls per overlapping burst", not
        true single-flight: a caller that arrives while a fetch is already
        in flight (a "waiter") awaits that in-flight fetch AND marks a
        follow-up as pending, so the owner runs exactly one more fetch
        after the first completes. The waiter's own `await` returns as
        soon as the *original* in-flight fetch finishes - it does not wait
        for that follow-up fetch. This is a deliberate, bounded trade-off
        (see `MAX_SENSOR_REFRESH_LOOP_ITERATIONS`): the 30s reconcile timer
        and the 30s main poll share `SCAN_INTERVAL_PROTECT`, so this
        collision is routine, not a rare edge case.

        `raise_on_error`: when True, an error from the underlying fetch
        (after `_fetch_sensors`'s own bounded absorption has already given
        up) is re-raised to the caller instead of only being logged at
        debug. `_async_update_data` passes this so a sustained /sensors
        outage still fails the scheduled poll; background/timer-driven
        callers leave this False so a blip doesn't crash a fire-and-forget
        task. `ConfigEntryAuthFailed` always propagates regardless of this
        flag - it is a distinct concern (see `_reconcile_sensor_refresh`).
        """
        if not self.protect_client:
            return

        if (
            self._sensor_refresh_task is not None
            and not self._sensor_refresh_task.done()
        ):
            self._sensor_refresh_pending = True
            error: Exception | None = None
            try:
                await self._sensor_refresh_task
            except ConfigEntryAuthFailed:
                raise
            except Exception as err:
                error = err
                _LOGGER.debug(
                    "Protect coordinator: Awaited sensor refresh failed: %s",
                    err,
                )
            if notify:
                self.async_update_listeners()
            if raise_on_error and error is not None:
                raise error
            return

        error = None
        iterations = 0
        while True:
            iterations += 1
            self._sensor_refresh_pending = False
            # Entry-scoped (not `self.hass.async_create_background_task`),
            # matching both outer driver tasks (`_sensor_reconcile_task`/
            # `_sensor_reconnect_task`) - keeps this inner fetch task
            # consistent with the rest of the sensor-refresh machinery and
            # auto-cancelled on unload rather than only hass-scoped.
            task = self.config_entry.async_create_background_task(
                self.hass,
                self._fetch_sensors(),
                name=f"{DOMAIN}_protect_fetch_sensors",
            )
            self._sensor_refresh_task = task
            try:
                await task
                error = None
            except ConfigEntryAuthFailed:
                raise
            except Exception as err:
                error = err
                _LOGGER.debug("Protect coordinator: Sensor refresh failed: %s", err)
            finally:
                if self._sensor_refresh_task is task:
                    self._sensor_refresh_task = None

            if notify:
                self.async_update_listeners()

            if not self._sensor_refresh_pending:
                break
            if iterations >= MAX_SENSOR_REFRESH_LOOP_ITERATIONS:
                _LOGGER.debug(
                    "Protect coordinator: sensor refresh loop hit the %d-"
                    "iteration cap with a refresh still pending; deferring "
                    "to the next reconcile tick instead of looping "
                    "indefinitely",
                    MAX_SENSOR_REFRESH_LOOP_ITERATIONS,
                )
                break

        if raise_on_error and error is not None:
            raise error

    @callback
    def _on_websocket_message(self, message: Any) -> None:
        """Apply a raw "devices" frame (parsed by `ProtectStreams`)."""
        frame = self._streams.parse_device_message(
            message, received_at=datetime.now(UTC)
        )
        if frame is not None:
            # Not guarded: a device frame that cannot be applied is a bug to
            # surface, unlike an event frame of a guessed shape (below).
            self._apply(frame)

    @callback
    def _on_websocket_connection_state_change(
        self, stream: Stream, *, connected: bool
    ) -> None:
        """
        Track WS connect/reconnect/disconnect transitions for `stream`.

        `stream` is "devices" or "events". Each subscription has its own
        callback, so a devices-only reconnect never reads as a full recovery
        while the events subscription stays hung.

        Drives the per-stream half of the health signal and, on every
        (re)connect of either stream, the stale-latch sweep: the stream was
        down for a while and may have missed an "end" frame, so waiting for
        the next poll would leave a latched sensor on longer than needed.
        The sweep notifies nobody (kept as it was); the next update shows it.
        """
        self._streams.connection_changed(stream, connected=connected)
        if connected:
            self._apply(StreamReconnected(stream))
            if stream == "devices":
                # Routed through `_sensor_reconnect_debouncer` (finding 6):
                # a flapping devices stream can fire this handler many
                # times a second (e.g. during a controller upgrade), and
                # each one used to spawn its own `async_refresh_sensors()`
                # background task - a waiter that marks a follow-up
                # pending on every single flap, which combined with the
                # (now-capped) owner loop still adds up to unbounded REST
                # calls over a sustained flapping period. The debouncer
                # collapses that into one immediate refresh plus at most
                # one trailing follow-up.
                self._sensor_reconnect_task = (
                    self.config_entry.async_create_background_task(
                        self.hass,
                        self._sensor_reconnect_debouncer.async_call(),
                        name=f"{DOMAIN}_protect_sensor_reconnect_refresh",
                    )
                )

    @property
    def websocket_health(self) -> dict[str, Any]:
        """Return WebSocket connectivity and delivery health for diagnostics."""
        return self._streams.health()

    def _now(self) -> Instant:
        """Read both clocks once, for one reconciler input."""
        return Instant(time.monotonic(), datetime.now(UTC))

    @callback
    def _apply(self, event: ReconcilerInput) -> Step:
        """
        Run one input through the reconciler and carry out its effects.

        The only writer of ``self._events``. The new state and cache are
        committed before any effect runs, so listeners and the registry see
        the step's result. `Escalate` is the caller's to act on.
        """
        step = reconcile(self._events, self.data, event, self._now())
        self._events, self.data = step.state, step.cache
        for effect in step.effects:
            match effect:
                case Log(level=level, msg=msg, args=args):
                    _LOGGER.log(level, msg, *args)
                case RemoveDevice(identifiers=identifiers):
                    self._remove_stale_device(*identifiers)
                case PublishUpdate(mark_success=True):
                    # Also marks the update successful and re-arms the poll
                    # timer, so entities stay available while frames flow.
                    self.async_set_updated_data(self.data)
                case PublishUpdate():
                    self.async_update_listeners()
                case _:
                    # Escalate: the caller re-raises the error it holds.
                    pass
        return step

    def _apply_reporting_errors(
        self, event: ReconcilerInput, report: Callable[[Exception], None]
    ) -> bool:
        """
        Apply a REST answer; report an unexpected failure like a fetch error.

        These fetchers have always kept the poll going when their answer
        could not be merged into the cache, so a bad record cannot fail the
        whole Protect poll. A failed step commits nothing.
        """
        try:
            self._apply(event)
        except Exception as err:
            report(err)
            return False
        return True

    @callback
    def _on_websocket_event_message(self, message: Any) -> None:
        """Apply a raw "events" frame (parsed by `ProtectStreams`)."""
        parsed = self._streams.parse_event_message(
            message, received_at=datetime.now(UTC)
        )
        if isinstance(parsed, RateLimited):
            if self.protect_client is not None:
                self.protect_client._defer_after_rate_limit(parsed.cooldown)
                _LOGGER.debug(
                    "Protect coordinator: events stream rate limited "
                    "(limit=%s, windowMs=%s); requests deferred",
                    parsed.limit,
                    parsed.window_ms,
                )
            return
        if parsed is None:
            return
        try:
            self._apply(parsed)
        except Exception:
            # The events stream has never been seen live: a wrong schema
            # assumption must never take down the WebSocket reconnect loop.
            # STALE_EVENT_TIMEOUT reconciliation is the real backstop for a
            # dropped or misparsed frame.
            _LOGGER.exception(
                "Protect coordinator: unexpected error processing WebSocket "
                "%s event frame; dropping it and continuing",
                parsed.event_type,
            )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch Protect data from API."""
        if not self.protect_client:
            _LOGGER.debug("Protect coordinator: No Protect client available")
            return self.data

        try:
            _LOGGER.debug("Protect coordinator: Fetching Protect data")

            # Expire stale event latches against the pre-poll data, before
            # `_fetch_cameras` rebuilds the cameras from REST.
            self._apply(PollStarted())

            # Fetch cameras
            await self._fetch_cameras()

            # Fetch lights
            await self._fetch_lights()

            # Fetch sensors (coordinated through async_refresh_sensors).
            # raise_on_error=True: `_fetch_sensors` already bounds its own
            # transient-error absorption (MAX_CONSECUTIVE_EMPTY_FETCHES),
            # but async_refresh_sensors's own coalescing wrapper otherwise
            # only logs a swallowed error at debug - without this flag a
            # sustained /sensors outage never fails the scheduled poll
            # (finding 4). Cameras/lights/etc. are unaffected: they call
            # their `_fetch_*` methods directly, so their escalated errors
            # already propagate here.
            await self.async_refresh_sensors(notify=False, raise_on_error=True)

            # Fetch NVR
            await self._fetch_nvr()
            await self._probe_global_alarm_manager()

            # Fetch chimes
            await self._fetch_chimes()

            # Fetch viewers
            await self._fetch_viewers()

            # Fetch liveviews
            await self._fetch_liveviews()

            # Fetch Protect 7.3.70 security devices
            await self._fetch_fobs()
            await self._fetch_link_stations()
            await self._fetch_alarm_hubs()
            await self._fetch_sirens()

            self._available = True
            self.data = {**self.data, "last_update": datetime.now(tz=UTC)}

            # Count missing devices and remove stale ones (Gold requirement).
            self._apply(PollCompleted())

            _LOGGER.debug(
                "Protect coordinator: Update complete - "
                "%d cameras, %d lights, %d sensors, %d NVRs, "
                "%d chimes, %d viewers, %d liveviews, "
                "%d fobs, %d link stations, %d alarm hubs, %d sirens",
                len(self.data["cameras"]),
                len(self.data["lights"]),
                len(self.data["sensors"]),
                len(self.data["nvrs"]),
                len(self.data["chimes"]),
                len(self.data["viewers"]),
                len(self.data["liveviews"]),
                len(self.data["fobs"]),
                len(self.data["link_stations"]),
                len(self.data["alarm_hubs"]),
                len(self.data["sirens"]),
            )

            return self.data

        except ConfigEntryAuthFailed:
            # `_fetch_sensors` converts UniFiAuthenticationError to
            # ConfigEntryAuthFailed itself (via `_handle_auth_error`) before
            # `async_refresh_sensors(raise_on_error=True)` re-raises it
            # here - it does NOT arrive as a UniFiAuthenticationError like
            # the other fetchers' errors do. Without this explicit clause
            # it falls into the generic `except Exception` below and gets
            # relabeled as UpdateFailed, losing the reauth flow entirely.
            raise
        except UniFiAuthenticationError as err:
            self._handle_auth_error(err)
        except UniFiConnectionError as err:
            self._handle_connection_error(err)
        except UniFiTimeoutError as err:
            self._handle_timeout_error(err)
        except UniFiResponseError as err:
            self._handle_response_error(err)
        except Exception as err:
            self._handle_generic_error(err)

        # Should never reach here due to raises above
        return self.data  # pragma: no cover

    async def _fetch_cameras(self) -> None:
        """Fetch camera data."""
        if not self.protect_client:
            return

        _LOGGER.debug("Protect coordinator: Fetching cameras")
        stamp = begin_request(self._events, "cameras", self._now())
        outcome: RestOutcome
        try:
            cameras_models = await self.protect_client.cameras.get_all()
            # Rebuild the dict from the API response so cameras removed from
            # Protect disappear from coordinator data (enables stale cleanup).
            cameras: dict[str, Any] = {}
            for camera_model in cameras_models:
                camera = normalize_camera(self._model_to_dict(camera_model))
                camera_id = camera.get("id")
                if camera_id:
                    cameras[camera_id] = camera

                    _LOGGER.debug(
                        "Protect coordinator: Camera %s supports smart detection: %s",
                        camera.get("name", camera_id),
                        camera.get("smartDetectTypes", []),
                    )
            outcome = Fetched(
                cameras, complete=self.protect_client.cameras.last_result_complete
            )
        except UniFiNotFoundError:
            outcome = NotFound()
        except (UniFiConnectionError, UniFiTimeoutError, UniFiResponseError) as err:
            # Auth and unexpected errors deliberately stay uncaught: reauth must
            # still trigger, and an unknown failure should not be papered over.
            # The reconciler absorbs a bounded number of these.
            if _escalated(self._apply(RestBatch(stamp, TransientError(err)))):
                raise
            return
        self._apply(RestBatch(stamp, outcome))

    async def _fetch_lights(self) -> None:
        """Fetch light data."""
        if not self.protect_client:
            return

        _LOGGER.debug("Protect coordinator: Fetching lights")
        stamp = begin_request(self._events, "lights", self._now())
        outcome: RestOutcome
        try:
            lights_models = await self.protect_client.lights.get_all()
            lights: dict[str, Any] = {}
            for light_model in lights_models:
                light = self._model_to_dict(light_model)
                light_id = light.get("id")
                if light_id:
                    lights[light_id] = light
            outcome = Fetched(
                lights, complete=self.protect_client.lights.last_result_complete
            )
        except UniFiNotFoundError:
            outcome = NotFound()
        except (UniFiConnectionError, UniFiTimeoutError, UniFiResponseError) as err:
            # See `_fetch_cameras` for why this is bounded rather than swallowed.
            if _escalated(self._apply(RestBatch(stamp, TransientError(err)))):
                raise
            return
        self._apply(RestBatch(stamp, outcome))

    async def _fetch_sensors(self) -> None:
        """
        Fetch sensor data.

        The request is stamped before it goes out: a door, motion, tamper or
        leak frame that lands from then on is newer than the answer, and the
        reconciler keeps it (see `protect_events`).
        """
        if not self.protect_client:
            return

        _LOGGER.debug("Protect coordinator: Fetching sensors")
        stamp = begin_request(self._events, "sensors", self._now())
        outcome: RestOutcome
        try:
            sensors_models = await self.protect_client.sensors.get_all()
            sensors: dict[str, Any] = {}
            for sensor_model in sensors_models:
                sensor = self._model_to_dict(sensor_model)
                sensor_id = sensor.get("id")
                if sensor_id:
                    sensors[sensor_id] = sensor
            outcome = Fetched(
                sensors, complete=self.protect_client.sensors.last_result_complete
            )
        except UniFiNotFoundError:
            outcome = NotFound()
        except UniFiAuthenticationError as err:
            self._handle_auth_error(err)
            return  # pragma: no cover - the handler raises
        except (UniFiConnectionError, UniFiTimeoutError, UniFiResponseError) as err:
            if _escalated(self._apply(RestBatch(stamp, TransientError(err)))):
                raise
            return
        except Exception as err:
            _LOGGER.warning("Protect coordinator: Error fetching sensors: %s", err)
            return
        applied = self._apply_reporting_errors(
            RestBatch(stamp, outcome),
            lambda err: _LOGGER.warning(
                "Protect coordinator: Error fetching sensors: %s", err
            ),
        )
        if applied and isinstance(outcome, Fetched):
            _LOGGER.debug(
                "Protect coordinator: Successfully fetched %d sensors",
                len(sensors_models),
            )

    async def _fetch_nvr(self) -> None:
        """
        Fetch NVR data.

        A frame for the NVR that lands while the request is out is newer than
        the answer (an arm mode that has just changed, for one), so the
        reconciler drops the answer then.
        """
        if not self.protect_client:
            return

        _LOGGER.debug("Protect coordinator: Fetching NVR")
        stamp = begin_request(self._events, "nvrs", self._now())
        outcome: RestOutcome
        try:
            nvr = self._model_to_dict(await self.protect_client.nvr.get())
            nvr_id = nvr.get("id")
            outcome = Fetched({nvr_id: nvr} if nvr_id else {})
        except UniFiNotFoundError:
            outcome = NotFound()
        except Exception as err:
            _LOGGER.debug("Protect coordinator: Error fetching NVR: %s", err)
            return
        self._apply_reporting_errors(
            RestBatch(stamp, outcome),
            lambda err: _LOGGER.debug(
                "Protect coordinator: Error fetching NVR: %s", err
            ),
        )

    async def _fetch_chimes(self) -> None:
        """Fetch chime data."""
        if not self.protect_client:
            return

        _LOGGER.debug("Protect coordinator: Fetching chimes")
        stamp = begin_request(self._events, "chimes", self._now())
        outcome: RestOutcome
        try:
            chimes_models = await self.protect_client.chimes.get_all()
            chimes: dict[str, Any] = {}
            for chime_model in chimes_models:
                chime = self._model_to_dict(chime_model)
                chime_id = chime.get("id")
                if chime_id:
                    chimes[chime_id] = chime
            outcome = Fetched(
                chimes, complete=self.protect_client.chimes.last_result_complete
            )
        except UniFiNotFoundError:
            outcome = NotFound()
        except Exception as err:
            self._log_family_fetch_error("chimes", err)
            return
        applied = self._apply_reporting_errors(
            RestBatch(stamp, outcome),
            lambda err: self._log_family_fetch_error("chimes", err),
        )
        if applied and isinstance(outcome, Fetched):
            self._log_family_fetch_recovery("chimes")
            _LOGGER.debug(
                "Protect coordinator: Successfully fetched %d chimes",
                len(chimes_models),
            )

    async def _fetch_viewers(self) -> None:
        """Fetch viewer data."""
        if not self.protect_client:
            return

        _LOGGER.debug("Protect coordinator: Fetching viewers")
        if not hasattr(self.protect_client, "viewers"):
            return
        stamp = begin_request(self._events, "viewers", self._now())
        outcome: RestOutcome
        try:
            viewers_models = await self.protect_client.viewers.get_all()
            viewers: dict[str, Any] = {}
            for viewer_model in viewers_models:
                viewer = self._model_to_dict(viewer_model)
                viewer_id = viewer.get("id")
                if viewer_id:
                    viewers[viewer_id] = viewer
            outcome = Fetched(
                viewers, complete=self.protect_client.viewers.last_result_complete
            )
        except UniFiNotFoundError:
            outcome = NotFound()
        except Exception as err:
            _LOGGER.debug("Protect coordinator: Error fetching viewers: %s", err)
            return
        self._apply(RestBatch(stamp, outcome))
        if isinstance(outcome, Fetched):
            _LOGGER.debug(
                "Protect coordinator: Successfully fetched %d viewers",
                len(viewers_models),
            )

    async def _fetch_liveviews(self) -> None:
        """Fetch liveview data (replaced as a whole; no device registry entries)."""
        if not self.protect_client:
            return

        _LOGGER.debug("Protect coordinator: Fetching liveviews")
        try:
            if hasattr(self.protect_client, "liveviews"):
                liveviews_models = await self.protect_client.liveviews.get_all()
                liveviews: dict[str, Any] = {}
                for liveview_model in liveviews_models:
                    liveview = self._model_to_dict(liveview_model)
                    liveview_id = liveview.get("id")
                    if liveview_id:
                        liveviews[liveview_id] = liveview
                self.data = {**self.data, "liveviews": liveviews}
                _LOGGER.debug(
                    "Protect coordinator: Successfully fetched %d liveviews",
                    len(liveviews_models),
                )
        except Exception as err:
            _LOGGER.debug("Protect coordinator: Error fetching liveviews: %s", err)

    async def _fetch_fobs(self) -> None:
        """Fetch fob data."""
        await self._fetch_device_family("fobs")

    async def _fetch_link_stations(self) -> None:
        """Fetch link station data."""
        await self._fetch_device_family("link_stations")

    async def _fetch_alarm_hubs(self) -> None:
        """
        Fetch alarm hub data, keeping the latest tamper event on top.

        Stamped before the unsupported-family check: a tamper event that
        arrives from then on is newer than the answer, whatever it is.
        """
        stamp = begin_request(self._events, "alarm_hubs", self._now())
        await self._fetch_device_family("alarm_hubs", stamp)

    async def _fetch_sirens(self) -> None:
        """Fetch siren data (the endpoint is absent on older Protect versions)."""
        await self._fetch_device_family("sirens")

    def _log_family_fetch_error(self, collection: str, err: Exception) -> None:
        """Report a family outage once while retaining details on later polls."""
        log = _LOGGER.debug if collection in self._failed_families else _LOGGER.warning
        self._failed_families.add(collection)
        log("Protect coordinator: Error fetching %s: %s", collection, err)

    def _log_family_fetch_recovery(self, collection: str) -> None:
        """Re-arm a family's outage warning after a successful fetch."""
        if collection in self._failed_families:
            self._failed_families.remove(collection)
            _LOGGER.info("Protect coordinator: Fetching %s recovered", collection)

    async def _fetch_device_family(
        self, collection: _DeviceFamily, stamp: RequestStamp | None = None
    ) -> None:
        """
        Fetch one simple device family and hand the outcome to the reconciler.

        Every outcome is applied, a skipped or failed one included, so the
        alarm hub tamper overlay and the siren stop guards always run.
        """
        if stamp is None:
            stamp = begin_request(self._events, collection, self._now())
        outcome = await self._read_device_family(collection)
        self._apply(RestBatch(stamp, outcome))

    async def _read_device_family(self, collection: _DeviceFamily) -> RestOutcome:
        """
        Read one simple device family ("fobs", "link_stations", ...).

        ``collection`` is both the data key and the protect_client endpoint
        attribute. A 404, or a 2xx web page instead of JSON, before the family
        has ever answered means this Protect version does not have the
        endpoint (it is not "no devices": an empty family answers ``[]``).
        That is logged once at INFO and the family is skipped until
        UNSUPPORTED_RESOURCE_RETRY has passed. A 404 after a successful answer
        goes through the bounded 404 cache path like every other collection.
        Any other error - including a web page after a successful answer, or
        a 400 - keeps the cached devices, as for chimes.
        """
        if not self.protect_client:
            return NoAnswer()

        retry_at = self._unsupported_until.get(collection)
        if retry_at is not None and time.monotonic() < retry_at:
            return NoAnswer()

        endpoint = getattr(self.protect_client, collection)
        answered = collection in self._answered_families
        _LOGGER.debug("Protect coordinator: Fetching %s", collection)
        try:
            models = await endpoint.get_all(expected_unsupported=not answered)
        except UniFiNotFoundError:
            if answered:
                return NotFound()
            self._mark_family_unsupported(collection, "HTTP 404")
            return NoAnswer()
        except UniFiResponseError as err:
            if answered or err.status_code >= HTTPStatus.MULTIPLE_CHOICES:
                self._log_family_fetch_error(collection, err)
                return NoAnswer()
            self._mark_family_unsupported(collection, "non-JSON response")
            return NoAnswer()
        except Exception as err:
            self._log_family_fetch_error(collection, err)
            return NoAnswer()

        self._log_family_fetch_recovery(collection)
        self._unsupported_until.pop(collection, None)
        self._answered_families.add(collection)
        devices: dict[str, Any] = {}
        for model in models:
            device = self._model_to_dict(model)
            device_id = device.get("id")
            if device_id:
                devices[device_id] = device
        return Fetched(devices, complete=endpoint.last_result_complete)

    def _mark_family_unsupported(self, collection: str, reason: str) -> None:
        """Skip a family this Protect version lacks until the next re-probe."""
        self._unsupported_until[collection] = (
            time.monotonic() + UNSUPPORTED_RESOURCE_RETRY.total_seconds()
        )
        if collection not in self._unsupported_logged:
            self._unsupported_logged.add(collection)
            _LOGGER.info(
                "Protect coordinator: this Protect version does not expose "
                "%s (%s); checking again every %s",
                collection,
                reason,
                UNSUPPORTED_RESOURCE_RETRY,
            )

    def get_camera(self, camera_id: str) -> dict[str, Any] | None:
        """Get camera data by ID."""
        result = self.data.get("cameras", {}).get(camera_id)
        return result if isinstance(result, dict) else None

    def get_light(self, light_id: str) -> dict[str, Any] | None:
        """Get light data by ID."""
        result = self.data.get("lights", {}).get(light_id)
        return result if isinstance(result, dict) else None

    def get_sensor(self, sensor_id: str) -> dict[str, Any] | None:
        """Get sensor data by ID."""
        result = self.data.get("sensors", {}).get(sensor_id)
        return result if isinstance(result, dict) else None

    def get_nvr(self, nvr_id: str) -> dict[str, Any] | None:
        """Get NVR data by ID."""
        result = self.data.get("nvrs", {}).get(nvr_id)
        return result if isinstance(result, dict) else None

    @callback
    def mark_siren_stopped(self, siren_id: str) -> None:
        """
        Record a manual stop in the cache and notify listeners.

        Protect sends no WebSocket frame after a manual stop, so without this
        the siren would keep reading "on" until the next poll. The cache is
        shared by every entity and rewritten on any device's frame, so the
        optimistic "off" has to live here and not on the entity.

        Listeners are notified without ``async_set_updated_data``: that would
        also mark the last poll as successful and push the next one back,
        when nothing has been polled.

        The run that was stopped is remembered by its own ``activatedAt``, so
        that a reading of the siren that was already on its way when it was
        stopped, and still says it is active, cannot switch it back on (the
        reconciler's siren stop guards).
        """
        self._apply(SirenStopped(siren_id))

    # Commands (ADR-010): settings writes publish an overlay and are
    # reconciled by WebSocket frames and the next poll.

    @property
    def _client(self) -> UniFiProtectClient:
        """Return the Protect client, or raise if Protect is not configured."""
        if self.protect_client is None:
            msg = "Protect is not available for this config"
            raise HomeAssistantError(
                msg,
                translation_domain=DOMAIN,
                translation_key="no_protect_coordinator",
            )
        return self.protect_client

    @staticmethod
    async def _async_command[**P, T](
        message: str,
        action: Callable[P, Awaitable[T]],
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> T:
        """Run one Protect write, raising ``command_failed`` with ``message``."""
        return await async_execute_command(
            CommandFailure(translation_key="command_failed", message=message),
            action,
            *args,
            **kwargs,
        )

    @callback
    def _publish_overlay(
        self, collection: str, device_id: str, changes: Mapping[str, Any]
    ) -> None:
        """
        Publish a successful write's fields as a copy-on-write overlay.

        The root, the collection map and the record (and any list on the
        changed path) are replaced, never mutated, and listeners are notified
        without ``async_set_updated_data()``. No rollback (the overlay follows
        success) and no generation tracking: a poll that started before the
        write may restore the old value until the next frame or poll.
        """
        self._apply(CommandOverlay(collection, device_id, changes))

    async def _async_reconcile(self) -> None:
        """Request a targeted refresh after a write; a failure is only logged."""
        await self.async_request_refresh()
        if not self.last_update_success:
            _LOGGER.debug(
                "Protect coordinator: refresh after a write failed: %s",
                self.last_exception,
            )

    async def async_update_camera(self, camera_id: str, **kwargs: Any) -> None:
        """Update camera settings and publish the written fields."""
        await self._async_command(
            f"Unable to update camera {camera_id}",
            self._client.cameras.update,
            camera_id,
            **kwargs,
        )
        self._publish_overlay("cameras", camera_id, kwargs)

    async def async_update_camera_settings(self, camera_id: str, **kwargs: Any) -> None:
        """Update camera settings (alias for async_update_camera)."""
        await self.async_update_camera(camera_id, **kwargs)

    async def async_set_hdr_mode(self, camera_id: str, mode: str) -> None:
        """Set a camera's HDR mode."""
        await self._async_command(
            f"Unable to set HDR mode for camera {camera_id}",
            self._client.cameras.set_hdr_mode,
            camera_id,
            mode,
        )
        self._publish_overlay("cameras", camera_id, {"hdrType": mode})

    async def async_set_video_mode(self, camera_id: str, mode: str) -> None:
        """Set a camera's video mode."""
        await self._async_command(
            f"Unable to set video mode for camera {camera_id}",
            self._client.cameras.set_video_mode,
            camera_id,
            mode,
        )
        self._publish_overlay("cameras", camera_id, {"videoMode": mode})

    async def async_set_microphone_volume(self, camera_id: str, volume: int) -> None:
        """Set a camera's microphone volume."""
        await self._async_command(
            f"Unable to set microphone volume for camera {camera_id}",
            self._client.cameras.set_microphone_volume,
            camera_id,
            volume,
        )
        self._publish_overlay("cameras", camera_id, {"micVolume": volume})

    async def async_move_ptz_to_preset(self, camera_id: str, preset: int) -> None:
        """Move a PTZ camera to a preset slot."""
        await self._async_command(
            f"Unable to move camera {camera_id} to PTZ preset {preset}",
            self._client.cameras.ptz_goto_preset,
            camera_id,
            str(preset),
        )

    async def async_start_ptz_patrol(self, camera_id: str, slot: int) -> None:
        """Start a PTZ camera's patrol."""
        await self._async_command(
            f"Unable to start PTZ patrol for camera {camera_id}",
            self._client.cameras.ptz_patrol_start,
            camera_id,
            slot,
        )

    async def async_stop_ptz_patrol(self, camera_id: str) -> None:
        """Stop a PTZ camera's patrol."""
        await self._async_command(
            f"Unable to stop PTZ patrol for camera {camera_id}",
            self._client.cameras.ptz_patrol_stop,
            camera_id,
        )

    async def async_set_light_mode(self, light_id: str, mode: str) -> None:
        """Set a light's mode."""
        await self._async_command(
            f"Unable to set mode for light {light_id}",
            self._client.lights.update,
            light_id,
            lightModeSettings={"mode": mode},
        )
        self._publish_overlay("lights", light_id, {"lightModeSettings": {"mode": mode}})

    async def async_set_light_brightness(self, light_id: str, led_level: int) -> None:
        """Set a light's LED level (1-6)."""
        await self._async_command(
            f"Unable to set brightness for light {light_id}",
            self._client.lights.set_brightness,
            light_id,
            led_level,
        )
        self._publish_overlay(
            "lights", light_id, {"lightDeviceSettings": {"ledLevel": led_level}}
        )

    async def async_set_chime_volume(self, chime_id: str, volume: int) -> None:
        """Set a chime's volume for every paired doorbell."""
        await self._async_command(
            f"Unable to set volume for chime {chime_id}",
            self._client.chimes.set_volume,
            chime_id,
            volume,
        )
        self._publish_overlay("chimes", chime_id, {"ringSettings": {"volume": volume}})

    async def async_set_chime_repeat(self, chime_id: str, repeat_times: int) -> None:
        """Set a chime's repeat count for every paired doorbell."""
        await self._async_command(
            f"Unable to set repeat count for chime {chime_id}",
            self._client.chimes.set_repeat_times,
            chime_id,
            repeat_times,
        )
        self._publish_overlay(
            "chimes", chime_id, {"ringSettings": {"repeatTimes": repeat_times}}
        )

    async def async_set_chime_ringtone(self, chime_id: str, ringtone_id: str) -> None:
        """Set a chime's ringtone (an opaque ID) for every doorbell, then refresh."""
        await self._async_command(
            f"Unable to set ringtone for chime {chime_id}",
            self._client.chimes.set_ringtone,
            chime_id,
            ringtone_id,
        )
        self._publish_overlay(
            "chimes", chime_id, {"ringSettings": {"ringtoneId": ringtone_id}}
        )
        await self._async_reconcile()

    async def async_set_chime_paired_cameras(
        self, chime_id: str, camera_ids: list[str]
    ) -> None:
        """
        Replace the doorbell cameras paired to a chime, then refresh.

        An empty list unpairs every doorbell. The body is exactly
        {"cameraIds": [...]}: the chime PATCH forbids additional properties.
        """
        paired = list(camera_ids)
        await self._async_command(
            f"Unable to set the paired doorbells of chime {chime_id}",
            self._client.chimes.update,
            chime_id,
            cameraIds=paired,
        )
        self._publish_overlay("chimes", chime_id, {"cameraIds": paired})
        await self._async_reconcile()

    async def async_play_chime(self, chime_id: str) -> None:
        """Report the official API's chime playback limitation."""
        chimes = self._client.chimes

        async def _play() -> None:
            try:
                await chimes.play(chime_id)
            except UniFiUnsupportedError as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="chime_playback_unsupported",
                ) from err

        await self._async_command(f"Unable to play chime {chime_id}", _play)

    async def async_update_viewer(self, viewer_id: str, **kwargs: Any) -> None:
        """Update a viewer (for example its ``liveview``) and publish it."""
        await self._async_command(
            f"Unable to update viewer {viewer_id}",
            self._client.viewers.update,
            viewer_id,
            **kwargs,
        )
        self._publish_overlay("viewers", viewer_id, kwargs)

    async def async_create_liveview(
        self, *, name: str, layout: int, is_default: bool = False
    ) -> None:
        """Create a liveview."""
        await self._async_command(
            f"Unable to create liveview {name}",
            self._client.liveviews.create,
            name=name,
            layout=layout,
            isDefault=is_default,
        )

    async def async_trigger_alarm(self, alarm_id: str) -> None:
        """Trigger an alarm manager webhook."""
        await self._async_command(
            f"Unable to trigger alarm {alarm_id}",
            self._client.application.trigger_alarm_webhook,
            alarm_id,
        )

    async def async_arm_alarm(self) -> None:
        """Arm the console's alarm with its currently selected arm profile."""
        await self._async_send_arm_command(enable=True)

    async def async_disarm_alarm(self) -> None:
        """Disarm the console's alarm."""
        await self._async_send_arm_command(enable=False)

    async def _async_send_arm_command(self, *, enable: bool) -> None:
        """
        Send an arm or disarm request, then ask Protect for fresh state.

        While the global alarm manager owns the alarm, Protect refuses both
        with a 400, raised as ``global_alarm_manager``. No overlay: the NVR's
        WebSocket frame normally arrives first and the refresh is the fallback.
        """
        profiles = self._client.arm_profiles
        command = profiles.enable if enable else profiles.disable

        async def _send() -> bool:
            try:
                return await command()
            except UniFiGlobalAlarmManagerError as err:
                # The refusal is also the answer to whether the manager is on,
                # e.g. when it was switched on since it was last asked.
                self.mark_global_alarm_manager_enabled()
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="global_alarm_manager",
                ) from err

        await self._async_command(
            f"Unable to {'arm' if enable else 'disarm'} the UniFi Protect alarm",
            _send,
        )
        await self._async_reconcile()

    async def async_play_siren(
        self, siren_id: str, *, duration: int | None = None
    ) -> None:
        """Sound a siren, optionally for 5, 10, 20 or 30 seconds."""
        await self._async_command(
            f"Unable to sound siren {siren_id}",
            self._client.sirens.play,
            siren_id,
            duration=duration,
        )

    async def async_set_siren_volume(self, siren_id: str, volume: int) -> None:
        """Set a siren's volume (1-100)."""
        await self._async_command(
            f"Unable to set volume for siren {siren_id}",
            self._client.sirens.set_volume,
            siren_id,
            volume,
        )

    async def async_stop_siren(self, siren_id: str) -> None:
        """Stop a siren and show it off at once (Protect sends no frame)."""
        await self._async_command(
            f"Unable to stop siren {siren_id}",
            self._client.sirens.stop,
            siren_id,
        )
        self.mark_siren_stopped(siren_id)

    async def async_start_camera_talkback(
        self, camera_id: str, media_url: str
    ) -> TalkbackStream:
        """Start talkback to a camera; every failure is a translated error."""
        if self.protect_client is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="protect_not_available_for_talkback",
            )
        try:
            session = await self.protect_client.cameras.create_talkback_session(
                camera_id
            )
        except Exception as err:
            detail = (
                f"invalid fields: {sanitized_validation_fields(err)}"
                if isinstance(err, ValidationError)
                else type(err).__name__
            )
            _LOGGER.warning("Talkback session failed for %s: %s", camera_id, detail)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="talkback_session_invalid",
            ) from err

        try:
            stream = TalkbackStream(session, media_url, loop=self.hass.loop)
            await stream.start()
        except (
            TalkbackSchemeError,
            TalkbackCodecError,
            TalkbackSamplingRateError,
        ) as err:
            _LOGGER.warning(
                "Unsupported talkback parameters for %s: %s", camera_id, err
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="talkback_unsupported_codec_or_scheme",
            ) from err
        except Exception as err:
            _LOGGER.warning(
                "Failed to start talkback for camera %s: %s", camera_id, err
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="talkback_stream_failure",
            ) from err
        return stream

    async def async_get_camera_snapshot(self, camera_id: str) -> bytes | None:
        """Return a camera's snapshot, or None (width/height cause an HTTP 500)."""
        try:
            snapshot = await self._client.cameras.get_snapshot(camera_id)
        except Exception:
            _LOGGER.debug("Error getting camera snapshot", exc_info=True)
            return None
        return snapshot if isinstance(snapshot, bytes) else None

    async def async_create_camera_stream_url(self, camera_id: str) -> str | None:
        """Return a new high-quality RTSPS stream URL, or None on failure."""
        try:
            stream = await self._client.cameras.create_rtsps_stream(
                camera_id, qualities=["high"]
            )
        except Exception:
            _LOGGER.debug(
                "Failed to create dynamic stream, falling back to static URL",
                exc_info=True,
            )
            return None
        return stream.high if stream and stream.high else None
