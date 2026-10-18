# Copyright 2026 UniFi Insights contributors
"""Protect coordinator for UniFi Insights - handles Protect device data."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from datetime import UTC, datetime, timedelta
from functools import partial
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Final, NamedTuple, override

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_time_interval

from custom_components.unifi_insights.api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiGlobalAlarmManagerError,
    UniFiNotFoundError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from custom_components.unifi_insights.api.const import PROTECT_RATE_LIMIT_WINDOW
from custom_components.unifi_insights.const import (
    DEVICE_TYPE_CAMERA,
    DEVICE_TYPE_CHIME,
    DEVICE_TYPE_DOORLOCK,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_NVR,
    DEVICE_TYPE_SENSOR,
    DEVICE_TYPE_SIREN,
    DEVICE_TYPE_VIEWER,
    DEVICE_TYPE_VIEWPORT,
    DOMAIN,
    EVENT_TYPE_ALARM_HUB_DEVICE_TAMPER,
    MODEL_KEY_FOB,
    MODEL_KEY_LINKSTATION,
    SCAN_INTERVAL_PROTECT,
)
from custom_components.unifi_insights.helpers import async_get_device_entry

from .base import UnifiBaseCoordinator

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.unifi_insights.api.network import UniFiNetworkClient
    from custom_components.unifi_insights.api.protect import UniFiProtectClient

_LOGGER = logging.getLogger(__name__)

_MILLISECOND_EPOCH_THRESHOLD: Final = 100_000_000_000.0

# Bounded auto-off for the event-derived motion/smart-detect/ring latch (see
# `_reconcile_stale_events`). Protect gives no delivery guarantee on the
# "end" frame that closes out a motion/smartDetect/ring event (a WS
# reconnect, a dropped frame, a network glitch can all lose it), and the
# `camera_motion`/`camera_*_detection` binary sensors turn ON purely from
# "start seen, end not yet seen" - so a missing "end" would otherwise latch
# them ON forever. In a home security system a permanently-ON motion sensor
# is worse than one that never fires: it destroys the signal and can trigger
# automations or wake people up endlessly.
#
# Five minutes is chosen as an order-of-magnitude safety margin: it is well
# above SCAN_INTERVAL_PROTECT (30s, so it never fires on ordinary poll
# jitter) and well above a realistic single continuous Protect motion/smart
# -detect/doorbell-ring event (typically seconds to low minutes), while
# still being short enough that a stuck sensor self-heals within single
# -digit minutes rather than staying wrong for hours. It is deliberately not
# tied to any one event type - see `_reconcile_stale_events`.
STALE_EVENT_TIMEOUT: Final = timedelta(minutes=5)
MAX_CONSECUTIVE_EMPTY_FETCHES: Final = 3

# How many consecutive polls the door-state preservation in
# `_should_preserve_cached_door_state` may keep a cached sensor state ahead
# of what REST reports before REST is allowed to win regardless. Without
# this bound, `_merge_preserved_door_state` copies the preserved timestamp
# back onto the dict written to `self.data`, so a REST response that never
# reports a timestamp (or a controller whose REST timestamp is stuck) makes
# the cache re-seed its own "I am newer" signal every poll - a permanent
# wedge, worse than the stale-read bug this preservation exists to fix.
MAX_DOOR_STATE_PRESERVE_POLLS: Final = 3

# Separate, more generous cap for branch 1 (WS-recency) of
# `_should_preserve_cached_door_state`, evaluated independently per
# door/motion/tamper/leak GROUP (see `_PRESERVED_SENSOR_STATE_FIELD_GROUPS`
# below). Branch 1 only fires for a group when the incoming WebSocket frame
# actually carried a field belonging to THAT group (see the gated per-group
# stamp in `_handle_device_update`), so in the ordinary case it is
# self-resolving: it cannot keep firing without genuine new state
# continuing to arrive. This bound exists as a safety net for the residual
# case where that keeps happening anyway (e.g. a device re-announcing the
# same state over WS every poll while REST's own report of that state never
# catches up). It is deliberately NOT the tight MAX_DOOR_STATE_PRESERVE_POLLS
# cap: a genuinely flapping door legitimately produces a real WS state frame
# on every single poll, and preserving WS in that case is *correct*, not a
# bug - reusing the 3-poll cap here would let a stale REST value win over
# live, currently-arriving WebSocket data during real activity, which is
# worse than the wedge this exists to prevent.
#
# Once a group's cap trips, `_sensor_ws_recency_latched` LATCHES it: REST
# keeps winning for that group on every subsequent poll - the per-group
# counter is not reset to let the cycle restart - until REST's own report
# actually agrees with the cached value (see `_group_state_agrees`). Without
# the latch, resetting to 0 on trip let a sustained condition (a real WS
# frame landing inside every fetch window while REST never reflects it) win
# the cache back for another MAX_WS_RECENCY_PRESERVE_POLLS polls, then lose
# it again for one poll, repeating forever - a 1-in-11 flip on contacts that
# drive auto-lock automations, not the one-time resolution the cap is meant
# to provide.
MAX_WS_RECENCY_PRESERVE_POLLS: Final = 10

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

# How many consecutive polls a device may be absent from its collection before
# `_cleanup_stale_devices` removes it from the device registry. That removal is
# irreversible - it drops the area assignment, entity customizations and any
# automation keyed on device_id - while the absence itself is ambiguous: the
# coordinator cannot tell "unadopted from Protect" from "omitted by a partial
# controller response" or "skipped by `get_all()` on a ValidationError". The
# grace window keeps the eviction, just no longer on the strength of a single
# poll. 3 polls is ~90s at SCAN_INTERVAL_PROTECT (30s).
MAX_CONSECUTIVE_MISSING_POLLS: Final = 3

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

# Envelope-only keys that must never leak from the raw top-level WebSocket
# frame into a merged device/event dict - see `_pick_field` and the
# `_on_websocket_message`/`_on_websocket_event_message` docstrings for why.
_ENVELOPE_ONLY_KEYS: Final = frozenset({"type", "action", "payload", "item"})

# Field names (both camelCase API aliases and snake_case attribute names)
# that identify a door/motion/tamper/leak *state* frame, as opposed to an
# ordinary telemetry frame (temperature, humidity, battery, signal, etc),
# grouped by which physical state they describe. Used as the SINGLE source
# of truth in two places that must never drift apart from each other, PER
# GROUP:
#   1. `_merge_preserved_door_state` - which fields get copied from the
#      cache back onto an incoming REST response, for each group branch 1
#      of `_should_preserve_cached_door_state` decided to preserve.
#   2. `_handle_device_update` - which incoming WebSocket sensor frames are
#      allowed to stamp `_sensor_last_ws_update[device_id][group]`, which in
#      turn gates branch 1 of `_should_preserve_cached_door_state` for that
#      SAME group.
#
# This used to be ONE set covering all four groups together. That let a
# WebSocket frame carrying only e.g. `isMotionDetected` (a UP-Sense shares
# one device id across door/motion/environment sensing, and motion at an
# entryway fires constantly) make branch 1 preserve - and because the merge
# then copied back ALL FOUR groups' fields together, one motion frame could
# suppress a genuinely newer REST door transition for a poll. Splitting the
# set per group means a frame that only touches one group can only ever
# gate and merge THAT group - the other three are decided purely by their
# own WebSocket activity (or, for the "door" group only, the timestamp
# -comparison branches 2/3 below). The never-drift-apart guarantee is now
# per group instead of global: (1) and (2) still can't disagree about what
# counts as a "door" frame, but a "door" frame and a "motion" frame can no
# longer be confused for each other the way one shared set allowed.
_DOOR_STATE_FIELDS: Final[frozenset[str]] = frozenset(
    {"isOpened", "is_opened", "opened"}
)
_DOOR_TIMESTAMP_FIELDS: Final[frozenset[str]] = frozenset(
    {"openStatusChangedAt", "open_status_changed_at"}
)
_MOTION_STATE_FIELDS: Final[frozenset[str]] = frozenset(
    {"isMotionDetected", "is_motion_detected"}
)
_MOTION_TIMESTAMP_FIELDS: Final[frozenset[str]] = frozenset(
    {"motionDetectedAt", "motion_detected_at"}
)
_TAMPER_STATE_FIELDS: Final[frozenset[str]] = frozenset(
    {"isTamperingDetected", "is_tampering_detected"}
)
_TAMPER_TIMESTAMP_FIELDS: Final[frozenset[str]] = frozenset(
    {"tamperingDetectedAt", "tampering_detected_at"}
)
_LEAK_STATE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "isLeakDetected",
        "is_leak_detected",
        "isExternalLeakDetected",
        "is_external_leak_detected",
    }
)
# `externalLeakDetectedAt` is the second (external probe) channel of
# multi-channel leak sensors such as the USL-Environmental.
_LEAK_TIMESTAMP_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "leakDetectedAt",
        "leak_detected_at",
        "externalLeakDetectedAt",
        "external_leak_detected_at",
    }
)

# Group name -> every field spelling (state + timestamp) that identifies a
# WS frame as belonging to that group, and that `_merge_preserved_door_state`
# copies back when that group is preserved.
_PRESERVED_SENSOR_STATE_FIELD_GROUPS: Final[dict[str, frozenset[str]]] = {
    "door": _DOOR_STATE_FIELDS | _DOOR_TIMESTAMP_FIELDS,
    "motion": _MOTION_STATE_FIELDS | _MOTION_TIMESTAMP_FIELDS,
    "tamper": _TAMPER_STATE_FIELDS | _TAMPER_TIMESTAMP_FIELDS,
    "leak": _LEAK_STATE_FIELDS | _LEAK_TIMESTAMP_FIELDS,
}

# Group name -> just the *state* field spelling(s), excluding the `*At`
# timestamp. Used only by `_group_state_agrees` to decide whether a
# WS-recency latch (see `_should_preserve_cached_door_state` and
# `MAX_WS_RECENCY_PRESERVE_POLLS`) can clear: REST catching up on the
# actual reported state is what matters there, not whether its timestamp
# representation matches the cache byte-for-byte.
_PRESERVED_SENSOR_STATE_GROUP_STATE_FIELDS: Final[dict[str, frozenset[str]]] = {
    "door": _DOOR_STATE_FIELDS,
    "motion": _MOTION_STATE_FIELDS,
    "tamper": _TAMPER_STATE_FIELDS,
    # Sensors like the USL-Environmental report no `isLeakDetected` flag;
    # their leak state IS the `*LeakDetectedAt` timestamp (null when dry),
    # so the timestamps must take part in the agreement check.
    "leak": _LEAK_STATE_FIELDS | _LEAK_TIMESTAMP_FIELDS,
}


def _pick_field(containers: list[dict[str, Any]], *keys: str) -> Any:
    """
    Return the first truthy value for any of `keys`, in container order.

    Shared by both WebSocket adapters: a real frame's identifying fields
    (modelKey/id for devices, type/id for events) may live at the top
    level, under "item", under "payload", or split across an "action"
    header - trying every plausible container/key combination instead of
    picking one and giving up avoids silently dropping every real frame if
    the guess is wrong.
    """
    for container in containers:
        for key in keys:
            value = container.get(key)
            if value:
                return value
    return None


def _deep_merge(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """
    Merge ``new`` over ``old``, recursing where both sides hold a dict.

    Returns a new dict at every level it touches and never mutates either
    argument (see the in-place-mutation note in `_on_websocket_message`).
    Only dict-over-dict recurses: any other value, ``None`` included,
    replaces the old one, so a frame can still clear a nested object.

    Used for the WebSocket frames of the nested-object families (fobs, link
    stations, alarm hubs, NVRs): a partial frame such as
    ``{"alarmHub": {"deviceTamperStatus": "tampered"}}`` would otherwise
    wipe the hub's armed/battery state until the next poll, and an NVR frame
    of ``{"armMode": {"status": "armed"}}`` its armProfileId.
    """
    merged = dict(old)
    for key, value in new.items():
        existing = merged.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _number(value: Any) -> float | None:
    """Return ``value`` as a float if it is a real number (not a bool), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class _SirenStop(NamedTuple):
    """What is remembered of a manual siren stop (see `mark_siren_stopped`)."""

    # ``activatedAt`` of the run that was cached when it was stopped, on the
    # console's clock. None when the cache did not hold one.
    activated_at: float | None
    # Millisecond epoch of the stop on Home Assistant's clock.
    stopped_at_ms: float
    # Monotonic time of the stop, which bounds how long the guard lives.
    monotonic: float


class _WebSocketConnectionStateCallback(partial[None]):
    """Adapt the API's positional boolean to the coordinator's named argument."""

    @override
    def __call__(self, connected: bool) -> None:
        """Forward the connection state using the coordinator's named argument."""
        super().__call__(connected=connected)


# A stopped siren is only guarded against a reading that was already in flight
# when it was stopped. A request does not outlive a poll interval or two, so
# the guard does not either.
SIREN_STOP_GUARD_TTL: Final = 2 * SCAN_INTERVAL_PROTECT.total_seconds()


def _event_metadata_text(value: Any) -> str | None:
    """
    Read one event metadata value as text.

    Protect wraps most event metadata as ``{"text": ...}`` but sends some
    fields (``alarmHubDeviceTamper``'s ``userName``) as a plain string, so
    both are accepted. Anything else reads as None.
    """
    if isinstance(value, dict):
        value = value.get("text")
    return value if isinstance(value, str) else None


def _normalize_epoch_seconds(value: Any) -> float | None:
    """
    Normalize a timestamp payload to a single epoch-seconds float.

    Single normalization boundary for `_should_preserve_cached_door_state`,
    used on BOTH sides of the comparison: the REST side (now always an int
    epoch-millisecond value once it round-trips through the `Sensor` model's
    `_coerce_epoch_millis` validator) and the cached side (which can be
    whatever type was written into `self.data` last - the WebSocket path in
    `_handle_device_update` merges raw JSON straight from the wire and
    bypasses pydantic entirely, so it is NOT guaranteed to already be an
    int).

    Handles:
    - `int`/`float`: epoch value in either seconds or milliseconds,
      disambiguated by `_MILLISECOND_EPOCH_THRESHOLD` (mirrors the model
      validator's own millisecond assumption for the int case).
    - `datetime`: aware instances convert via `.timestamp()`; naive
      instances are assumed UTC (the Protect controller does not appear to
      emit naive local time, but a naive value must not raise or silently
      compare against an aware one incorrectly).
    - `str`: parsed as ISO 8601 via `datetime.fromisoformat`.

    Returns `None` for `None`, `bool` (an `int` subclass - deliberately
    excluded so a stray `True`/`False` can't be misread as an epoch of 1/0),
    or any value that doesn't parse, so callers can treat "not comparable"
    uniformly rather than needing a matrix of isinstance branches per
    ordered pair (the bug this replaces: `_compare_timestamps` had no
    branch at all for e.g. `(int, datetime)`, which returned `None` and
    fell through to code that misread "no comparison possible" as "REST
    wins").
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value / 1000.0 if value > _MILLISECOND_EPOCH_THRESHOLD else float(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return dt.timestamp()
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    return None


class UnifiProtectCoordinator(UnifiBaseCoordinator):
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

    UNVALIDATED SCHEMA WARNING: the "events" WebSocket subscription
    (`_on_websocket_event_message` / `_handle_event_update` /
    `_process_event_for_device`) has never executed against real Protect
    traffic before this coordinator started subscribing to it. Every frame
    -shape assumption in that path (envelope shape, which key carries the
    device id, whether "smartDetectZone" or "smartDetect" is the real
    smart-detect event type) is a best-effort guess pending a real capture
    - see the inline UNVALIDATED comments at each guess. The whole path is
    written to degrade safely if a guess is wrong: it never raises into the
    WS callback, it logs an unparseable frame once at WARNING then at
    DEBUG, and it never latches a binary sensor on indefinitely (see
    `STALE_EVENT_TIMEOUT` / `_reconcile_stale_events`).
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
        # Track previous device IDs for stale device cleanup (Gold requirement)
        self._previous_protect_device_ids: dict[str, set[str]] = {
            "cameras": set(),
            "lights": set(),
            "sensors": set(),
            "nvrs": set(),
            "viewers": set(),
            "chimes": set(),
            "fobs": set(),
            "link_stations": set(),
            "alarm_hubs": set(),
            "sirens": set(),
        }
        self._consecutive_empty_fetches: dict[str, int] = {
            "cameras": 0,
            "lights": 0,
            "sensors": 0,
            "nvrs": 0,
            "viewers": 0,
            "chimes": 0,
            "fobs": 0,
            "link_stations": 0,
            "alarm_hubs": 0,
            "sirens": 0,
        }
        # collection -> consecutive polls whose fetch raised a transient error.
        # Kept separate from `_consecutive_empty_fetches`: an empty response is
        # evidence the devices are gone, an error is evidence of nothing.
        self._consecutive_fetch_errors: dict[str, int] = {
            "cameras": 0,
            "lights": 0,
            "sensors": 0,
        }
        # device_type -> {device_id: consecutive polls the device has been
        # missing from its collection}. Drives the registry-removal grace
        # window in `_cleanup_stale_devices`.
        self._consecutive_missing_polls: dict[str, dict[str, int]] = {
            "cameras": {},
            "lights": {},
            "sensors": {},
            "nvrs": {},
            "viewers": {},
            "chimes": {},
            "fobs": {},
            "link_stations": {},
            "alarm_hubs": {},
            "sirens": {},
        }
        # Families whose list endpoint has answered successfully at least
        # once. A 404 from one of these is a transient/removed-device signal
        # for `_update_device_collection`, not "this Protect is too old".
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
        # alarm hub id -> its latest alarmHubDeviceTamper event: "event_id",
        # "status", "received" (monotonic time the status arrived) and the
        # "_lastTamperUser"/"_lastTamperAt" keys written onto the hub dict.
        # Kept outside `self.data` because the REST poll rebuilds every hub
        # dict; `_fetch_alarm_hubs` lays it back on top (see
        # `_with_tamper_record`) so the event outlives the next poll.
        self._alarm_hub_last_tamper: dict[str, dict[str, Any]] = {}
        # siren id -> its last manual stop, for sirens whose cache still has
        # to be checked against a reading that was already in flight when they
        # were stopped. See `mark_siren_stopped`.
        self._siren_stopped_at: dict[str, _SirenStop] = {}
        # family -> count of WebSocket frames applied to it. A REST read of the
        # family that was started before a frame landed is older than that
        # frame and must not replace it (see `_fetch_device_family`).
        self._ws_generation: dict[str, int] = {"sirens": 0, "nvrs": 0}
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
        # Caps the "can't parse WebSocket message" warning to once per
        # stream, so a persistently wrong frame shape can't log-storm a
        # production instance; every subsequent occurrence still logs at
        # debug. Devices and events are tracked separately since they are
        # different failure classes.
        self._ws_parse_warned = False
        self._ws_event_parse_warned = False
        self._ws_event_error_warned: bool = False
        self._on_devices_connection_state_change = callback(
            _WebSocketConnectionStateCallback(
                self._on_websocket_connection_state_change, "devices"
            )
        )
        self._on_events_connection_state_change = callback(
            _WebSocketConnectionStateCallback(
                self._on_websocket_connection_state_change, "events"
            )
        )

        # WebSocket health signal (task 5, hardened by review finding 1):
        # there was previously no way to tell "connected and delivering"
        # from "connected but silent" from "reconnect-looping" - surfaced
        # via `websocket_health` in diagnostics.py.
        #
        # Tracked PER SUBSCRIPTION, not as one shared pair of fields: the
        # devices and events subscriptions are independent WebSocket
        # connections (see async_start_websocket), and a shared field would
        # let a chatty devices stream mask a hung/disconnected events
        # stream - exactly the "motion silently stopped working for days"
        # failure this signal exists to catch (an NVR restart where devices
        # reconnects cleanly but events hangs half-open forever, no error,
        # no close frame). See `_mark_ws_frame_received` and
        # `_on_websocket_connection_state_change`.
        self._ws_stream_health: dict[str, dict[str, Any]] = {
            "devices": {"connected": False, "last_message_at": None},
            "events": {"connected": False, "last_message_at": None},
        }
        # Top-level roll-up, recomputed by `_recompute_ws_health_rollup()`
        # on every per-stream update. `connected` is True only when BOTH
        # streams are connected (so a half-dead pair correctly reads as
        # unhealthy even without inspecting the per-stream detail);
        # `last_message_at` is the most recent frame from EITHER stream
        # (preserves the old "any wire alive" semantics as a coarse
        # liveness signal). Kept as real fields (not just derived in the
        # `websocket_health` property) so backwards-compatible attribute
        # access (`coordinator._ws_connected`) keeps working.
        self._last_ws_message: datetime | None = None
        self._ws_connected: bool = False

        # Wall-clock (HA-local, not Protect's) time each camera/light/ring
        # latch became active (event "start" seen, "end" not yet seen).
        # Deliberately independent of the event payload's own start/end
        # timestamp format (unconfirmed - see class docstring) so the
        # STALE_EVENT_TIMEOUT auto-off in `_reconcile_stale_events` never
        # depends on that guess being right. Popped as soon as a real "end"
        # arrives; see `_apply_motion_event` / `_apply_ring_event`.
        self._camera_motion_started: dict[str, datetime] = {}
        self._light_motion_started: dict[str, datetime] = {}
        self._camera_ring_started: dict[str, datetime] = {}

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
        # ACCEPTED GAP (not fixed): `_cleanup_stale_devices` only prunes an
        # id that appears in `previous_ids - current_ids`, and
        # `previous_ids` is only ever populated from a successful REST
        # `_fetch_sensors` poll (see that method's `current_ids` source,
        # `self.data["sensors"].keys()`). A sensor id that `get_all()`
        # keeps skipping on a `ValidationError` - so it never lands in a
        # REST poll's `sensors` dict - can still receive WebSocket frames
        # and pick up an entry here that is then never pruned. Left
        # unbounded deliberately: the growth is one `float` per distinct
        # id that both exists in Protect and is persistently unparseable
        # by the `Sensor` model, which in practice is bounded by the
        # controller's own device count and does not grow without limit -
        # not worth the complexity of a second eviction path for a
        # genuinely rare, self-limiting case.
        # sensor_id -> group -> monotonic time of the last WebSocket frame
        # that carried a field belonging to that group (see
        # `_PRESERVED_SENSOR_STATE_FIELD_GROUPS`). A device_id key only
        # exists here if at least one group has ever been stamped for it;
        # an individual group key only exists once THAT group has been
        # stamped. Gates branch 1 of `_should_preserve_cached_door_state`,
        # per group.
        self._sensor_last_ws_update: dict[str, dict[str, float]] = {}
        # sensor_id -> consecutive polls `_should_preserve_cached_door_state`
        # has preserved the cache over REST via the door-timestamp
        # comparison (branches 2/3, "door" group only). Bounds preservation
        # at MAX_DOOR_STATE_PRESERVE_POLLS so a REST response that never
        # reports a timestamp - or one stuck below the cache - cannot wedge
        # a door state forever; reset to 0 whenever REST wins normally.
        self._sensor_preserve_counts: dict[str, int] = {}
        # sensor_id -> group -> consecutive polls branch 1 (WS-recency) of
        # `_should_preserve_cached_door_state` has preserved that group's
        # cache. Kept separate from `_sensor_preserve_counts` (and bounded
        # by the more generous MAX_WS_RECENCY_PRESERVE_POLLS, not
        # MAX_DOOR_STATE_PRESERVE_POLLS) so the two branches' bounds can
        # never conflate a WS-recency streak with a timestamp-comparison
        # streak into hitting the wrong cap early - and per group, so one
        # group's streak can never hit another group's cap early either.
        self._sensor_ws_recency_preserve_counts: dict[str, dict[str, int]] = {}
        # sensor_id -> set of groups currently LATCHED after hitting
        # MAX_WS_RECENCY_PRESERVE_POLLS: while a group is in this set,
        # branch 1 returns "do not preserve" for it unconditionally (REST
        # keeps winning) regardless of new WebSocket activity, until
        # `_group_state_agrees` reports REST has caught up to the cached
        # value - see MAX_WS_RECENCY_PRESERVE_POLLS's docstring for why a
        # plain counter-reset-to-0 on cap-trip produced a repeating flip
        # instead of a one-time resolution. Safe to suppress the merge like
        # this because `_handle_device_update` already wrote every genuine
        # WebSocket frame directly into `self.data["sensors"]` as it
        # arrived - latching only affects whether a REST *poll* is allowed
        # to overwrite that already-current value, not whether live
        # WebSocket state keeps updating in real time.
        self._sensor_ws_recency_latched: dict[str, set[str]] = {}
        # sensor_id set: which sensors have already logged the door-state
        # -preservation cap-hit WARNING at least once (any branch, any
        # group). Matches `_log_unparseable_ws_message`'s warn-once-then
        # -debug precedent (`_ws_parse_warned`) - without it, a sensor
        # stuck at the cap re-logs a WARNING roughly every cap-many polls
        # for as long as the condition persists.
        self._sensor_preserve_cap_warned: set[str] = set()
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
        # Second, independent subscription (task 1 - the actual fix): without
        # this, `_handle_event_update`/`_process_event_for_device` have no
        # caller anywhere, so `lastMotionStart`/`lastMotionEnd`/
        # `lastSmartDetectTypes` are never written and every motion/smart
        # -detect binary sensor is permanently OFF. Runs on the same
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
        self.data["global_alarm_manager"] = enabled
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
        """
        Adapt a raw WebSocket "devices" message to `_handle_device_update`.

        Confirmed live against hardware 2026-08-12: the local-console shape is
        `{"type": "update", "item": {"id", "modelKey", ...fields}}` - the
        device delta lives under an "item" key, not at top level. `modelKey`
        and `id` are still resolved independently across every plausible
        container - "item", a "payload" key (REST-response-shaped push), and
        an "action" key (the header/payload split used by UniFi's private
        app WebSocket) - rather than picking one container and giving up.
        Picking a single container wrong would silently drop every real
        frame, which for a door sensor means a missed open/close event.

        The raw envelope (`message`) is included as a last-resort container
        so a fully flat frame (no item/payload/action wrapper) still works,
        but its own "type"/"action"/"payload"/"item" keys are stripped
        before merging - confirmed in production: without that exclusion,
        the envelope's "type": "update" (the action verb, not a device
        field) clobbered the real hardware model string (UFP-SENSE,
        USL-Entry-US, USL-Environmental-US) on any partial update frame
        that didn't re-send the unchanged "type" field, flipping it back
        and forth against the next REST poll (measured 49 times in 10
        minutes). Only `message` is filtered this way - item/payload/action
        are still merged in full, since a real device "type" field nested
        under one of those is legitimate data, not envelope noise.
        """
        self._mark_ws_frame_received("devices")

        if not isinstance(message, dict):
            self._log_unparseable_ws_message(
                "Protect coordinator: WebSocket message was not a JSON object: %r",
                message,
            )
            return

        action = message.get("action")
        payload = message.get("payload")
        item = message.get("item")
        containers = [
            c for c in (payload, action, item, message) if isinstance(c, dict)
        ]

        model_key = _pick_field(containers, "modelKey", "model_key")
        if not model_key:
            self._log_unparseable_ws_message(
                "Protect coordinator: WebSocket device message missing modelKey: %s",
                message,
            )
            return

        device_id = _pick_field(containers, "id")
        if not device_id:
            _LOGGER.debug(
                "Protect coordinator: WebSocket %s update missing device id: %s",
                model_key,
                message,
            )
            return

        # Merge every dict container into a brand-new dict (never mutating
        # `self.data` or any container in place - in-place mutation can
        # make HA listeners holding a stale reference miss the transition)
        # so fields split across payload/action (e.g. id in the header,
        # state fields in the payload) are all kept.
        device_data: dict[str, Any] = {}
        for container in reversed(containers):
            if container is message:
                device_data.update(
                    {k: v for k, v in container.items() if k not in _ENVELOPE_ONLY_KEYS}
                )
            else:
                device_data.update(container)
        device_data["id"] = device_id

        self._handle_device_update(model_key, device_data)

    def _log_unparseable_ws_message(
        self, msg: str, *args: Any, event_stream: bool = False
    ) -> None:
        """
        Log an unparseable WS message once at WARNING, then at DEBUG.

        `event_stream` selects an independent warned-once flag for the
        "events" subscription so a persistently-wrong events frame shape
        (see class docstring) doesn't share its one-time WARNING with the
        unrelated "devices" subscription, or vice versa.
        """
        warned_attr = "_ws_event_parse_warned" if event_stream else "_ws_parse_warned"
        level = logging.WARNING if not getattr(self, warned_attr) else logging.DEBUG
        _LOGGER.log(level, msg, *args)
        setattr(self, warned_attr, True)

    def _mark_ws_frame_received(self, stream: str) -> None:
        """
        Record that a WebSocket frame was just delivered on `stream` (task 5).

        Hardened by review finding 1 to be per-subscription, not shared.
        Called unconditionally at the top of both adapters, even for a
        frame that turns out to be unparseable - receiving anything at all
        is evidence that stream's wire is alive, which is exactly the
        "connected but silent" vs. "delivering" distinction
        `websocket_health` exists to answer. Only `stream`'s own entry in
        `_ws_stream_health` is touched, so a chatty devices stream can never
        make a silent events stream look alive, or vice versa.
        """
        self._ws_stream_health[stream]["last_message_at"] = datetime.now(UTC)
        self._ws_stream_health[stream]["connected"] = True
        self._recompute_ws_health_rollup()

    @callback
    def _on_websocket_connection_state_change(
        self, stream: str, *, connected: bool
    ) -> None:
        """
        Track WS connect/reconnect/disconnect transitions for `stream`.

        `stream` is "devices" or "events" - each subscription is registered
        with its own typed callback (`_on_devices_connection_state_change` /
        `_on_events_connection_state_change`) so this always knows which one
        transitioned, rather than the two subscriptions clobbering one
        shared flag (review finding 1: that let a devices-only reconnect
        read identically to a full recovery while the events subscription
        stayed hung).

        Drives two things: the per-stream half of the health signal
        (task 5), and stale-latch reconciliation on every (re)connect of
        EITHER stream (task 2) - a reconnect means that subscription was
        down for some stretch of time during which an "end" frame could
        have been missed entirely, so waiting for the next 30s REST poll to
        notice would leave a latched sensor ON longer than necessary.
        """
        self._ws_stream_health[stream]["connected"] = connected
        self._recompute_ws_health_rollup()
        if connected:
            self._reconcile_stale_events()
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

    def _recompute_ws_health_rollup(self) -> None:
        """
        Recompute the top-level `_ws_connected`/`_last_ws_message` roll-up.

        `_ws_connected` is True only when BOTH the devices and events
        streams are connected - the whole point of review finding 1 is that
        a half-dead pair (one stream healthy, one hung) must read as
        unhealthy at the top level too, not just in the per-stream detail
        that a human has to know to go look for. `_last_ws_message` stays
        the most recent frame from EITHER stream, preserving the original
        "is the wire alive at all" coarse-liveness semantics as a secondary
        signal.
        """
        streams = self._ws_stream_health.values()
        self._ws_connected = all(s["connected"] for s in streams)
        timestamps = [s["last_message_at"] for s in streams if s["last_message_at"]]
        self._last_ws_message = max(timestamps) if timestamps else None

    @property
    def websocket_health(self) -> dict[str, Any]:
        """
        Expose WS connectivity/delivery health for diagnostics.py (task 5).

        Surfaces BOTH a top-level roll-up (`connected`/`last_message_at`,
        same key names as before this fix - review finding 1 asked to
        preserve these where cheap so any existing consumer/dashboard
        keeps working) AND per-subscription detail under `devices`/`events`,
        since the top-level pair alone cannot distinguish "both streams
        healthy" from "devices healthy, events silently hung" - exactly the
        failure this signal exists to catch.
        """

        def _stream_payload(stream: dict[str, Any]) -> dict[str, Any]:
            last_message_at = stream["last_message_at"]
            return {
                "connected": stream["connected"],
                "last_message_at": (
                    last_message_at.isoformat() if last_message_at else None
                ),
            }

        return {
            "connected": self._ws_connected,
            "last_message_at": (
                self._last_ws_message.isoformat() if self._last_ws_message else None
            ),
            "devices": _stream_payload(self._ws_stream_health["devices"]),
            "events": _stream_payload(self._ws_stream_health["events"]),
        }

    @callback
    def _handle_device_update(
        self, model_key: str, device_data: dict[str, Any]
    ) -> None:
        """Handle device update from WebSocket."""
        device_id = device_data.get("id")
        if not device_id:
            return

        _LOGGER.debug(
            "Protect coordinator: WebSocket device update for %s: %s",
            model_key,
            device_id,
        )

        if model_key == DEVICE_TYPE_CAMERA:
            existing_camera = self.data["cameras"].get(device_id, {})
            merged_camera = {
                **existing_camera,
                **device_data,
            }
            self.data["cameras"][device_id] = self._normalize_camera_data(merged_camera)
        elif model_key == DEVICE_TYPE_LIGHT:
            self.data["lights"][device_id] = {
                **self.data["lights"].get(device_id, {}),
                **device_data,
            }
        elif model_key == DEVICE_TYPE_SENSOR:
            # Gated PER GROUP to door/motion/tamper/leak *state* frames
            # only (see `_PRESERVED_SENSOR_STATE_FIELD_GROUPS`) - stamping
            # this for every sensor WS frame (temperature, humidity,
            # battery, signal...) regardless of content let one benign
            # telemetry push make branch 1 of
            # `_should_preserve_cached_door_state` preserve the cache, and
            # stamping all four groups together off of any ONE group's
            # frame let e.g. a motion-only frame suppress a genuinely
            # newer REST door transition. A frame only stamps the specific
            # group(s) whose fields it actually carries; a frame matching
            # no group leaves `device_id` absent from
            # `_sensor_last_ws_update` entirely.
            now = time.monotonic()
            for group, fields in _PRESERVED_SENSOR_STATE_FIELD_GROUPS.items():
                if not fields.isdisjoint(device_data):
                    self._sensor_last_ws_update.setdefault(device_id, {})[group] = now
            self.data["sensors"][device_id] = {
                **self.data["sensors"].get(device_id, {}),
                **device_data,
            }
        elif model_key == DEVICE_TYPE_NVR:
            # Deep merge: armMode is nested and a frame may carry only its
            # status, which must not drop the armProfileId beside it.
            self._ws_generation["nvrs"] += 1
            self.data["nvrs"][device_id] = _deep_merge(
                self.data["nvrs"].get(device_id, {}), device_data
            )
        elif model_key == DEVICE_TYPE_VIEWER:
            self.data["viewers"][device_id] = {
                **self.data["viewers"].get(device_id, {}),
                **device_data,
            }
        elif model_key == DEVICE_TYPE_CHIME:
            self.data["chimes"][device_id] = {
                **self.data["chimes"].get(device_id, {}),
                **device_data,
            }
        elif model_key == DEVICE_TYPE_DOORLOCK:
            self.data["doorlocks"][device_id] = {
                **self.data["doorlocks"].get(device_id, {}),
                **device_data,
            }
        elif model_key == DEVICE_TYPE_VIEWPORT:
            self.data["viewports"][device_id] = {
                **self.data["viewports"].get(device_id, {}),
                **device_data,
            }
        elif model_key == MODEL_KEY_FOB:
            self.data["fobs"][device_id] = _deep_merge(
                self.data["fobs"].get(device_id, {}), device_data
            )
        elif model_key == MODEL_KEY_LINKSTATION:
            collection = self._linkstation_collection(device_id, device_data)
            if collection is None:
                _LOGGER.debug(
                    "Protect coordinator: dropping WebSocket linkstation update "
                    "for unknown device %s; the next poll will pick it up",
                    device_id,
                )
                return
            self.data[collection][device_id] = _deep_merge(
                self.data[collection].get(device_id, {}), device_data
            )
        elif model_key == DEVICE_TYPE_SIREN:
            # Deep merge: a partial frame carries only the changed part of the
            # nested sirenStatus / wirelessConnectionState objects.
            self._ws_generation["sirens"] += 1
            self.data["sirens"][device_id] = _deep_merge(
                self.data["sirens"].get(device_id, {}), device_data
            )
            self._apply_siren_stop_guards()

        # async_set_updated_data (rather than async_update_listeners) also
        # marks the last update as successful and resets the poll timer, so
        # entities relying on last_update_success don't stay unavailable
        # while WebSocket data is flowing, and the 30s poll fallback re-arms
        # from the last WebSocket message rather than firing needlessly.
        self.async_set_updated_data(self.data)

    def _linkstation_collection(
        self, device_id: str, device_data: dict[str, Any]
    ) -> str | None:
        """
        Pick the collection a WebSocket "linkstation" frame belongs to.

        Link stations and alarm hubs share the "linkstation" modelKey, so the
        frame is routed by which collection already holds the id, then by the
        frame's own isAlarmHub. A partial frame for an id no poll has seen
        and without isAlarmHub has no safe target: None (drop it).
        """
        if device_id in self.data["alarm_hubs"]:
            return "alarm_hubs"
        if device_id in self.data["link_stations"]:
            return "link_stations"
        is_alarm_hub = device_data.get("isAlarmHub")
        if isinstance(is_alarm_hub, bool):
            return "alarm_hubs" if is_alarm_hub else "link_stations"
        return None

    def _normalize_camera_data(self, camera: dict[str, Any]) -> dict[str, Any]:
        """Normalize camera fields across alias and legacy payload shapes."""
        normalized = dict(camera)

        feature_flags = normalized.get("featureFlags")
        if not isinstance(feature_flags, dict):
            legacy_feature_flags = normalized.get("feature_flags")
            feature_flags = (
                legacy_feature_flags if isinstance(legacy_feature_flags, dict) else {}
            )
        normalized["featureFlags"] = feature_flags

        smart_detect_types = normalized.get("smartDetectTypes")
        if not isinstance(smart_detect_types, list):
            legacy_smart_detect_types = normalized.get("smart_detect_types")
            if isinstance(legacy_smart_detect_types, list):
                smart_detect_types = legacy_smart_detect_types
            else:
                smart_detect_types = feature_flags.get("smartDetectTypes")
                if not isinstance(smart_detect_types, list):
                    feature_flag_types = feature_flags.get("smart_detect_types")
                    smart_detect_types = (
                        feature_flag_types
                        if isinstance(feature_flag_types, list)
                        else []
                    )
        normalized["smartDetectTypes"] = smart_detect_types

        is_ptz = normalized.get("isPtz")
        if not isinstance(is_ptz, bool):
            legacy_is_ptz = normalized.get("is_ptz")
            if isinstance(legacy_is_ptz, bool):
                is_ptz = legacy_is_ptz
            else:
                is_ptz = bool(
                    normalized.get("hasPtz")
                    or feature_flags.get("hasPtz")
                    or feature_flags.get("has_ptz")
                )
        normalized["isPtz"] = is_ptz
        normalized["hasPtz"] = is_ptz

        last_smart_detect_types = normalized.get("lastSmartDetectTypes")
        if not isinstance(last_smart_detect_types, list):
            normalized["lastSmartDetectTypes"] = []

        if "lastMotion" not in normalized:
            normalized["lastMotion"] = 0
        if "lastRing" not in normalized:
            normalized["lastRing"] = 0

        return normalized

    @callback
    def _on_websocket_event_message(self, message: Any) -> None:
        """
        Adapt a raw WebSocket "events" message to `_handle_event_update`.

        UNVALIDATED against live traffic (see class docstring): this
        subscription has never received a real Protect "events" frame, so
        the envelope shape assumed here - mirroring the confirmed "devices"
        envelope (payload/item/action splits, see `_on_websocket_message`)
        - is a best-effort guess pending a real capture. Every extraction
        is deliberately tolerant (multiple candidate keys, never raises) so
        a wrong guess degrades to a dropped/logged frame instead of a crash
        or a wedged coordinator.

        The event's own "type" (motion/smartDetect/ring) is resolved only
        from an inner item/payload/action container when one is present -
        deliberately excluding the raw envelope `message`, whose top-level
        "type" would otherwise be the envelope action verb ("add"/
        "update"), not the Protect event type (see the identical "type"
        -clobbering fix in `_on_websocket_message`). When no inner
        container exists at all, the frame is flat and `message` itself
        holds the real fields - there is no separate envelope to strip in
        that shape, so it is used directly.
        """
        self._mark_ws_frame_received("events")

        if not isinstance(message, dict):
            self._log_unparseable_ws_message(
                "Protect coordinator: WebSocket event message was not a JSON "
                "object: %r",
                message,
                event_stream=True,
            )
            return

        payload = message.get("payload")
        item = message.get("item")
        action = message.get("action")
        containers = [c for c in (payload, item, action) if isinstance(c, dict)]
        if not containers:
            containers = [message]

        # The console reports its own faults on this stream as a frame with
        # an "error" field and no event fields, a rate-limit notice being the
        # common one. That is not a parse failure, and letting it fall through
        # to the unparseable path spends the one-time WARNING that exists to
        # surface a genuinely wrong frame shape, leaving a real problem to be
        # logged at DEBUG afterwards. Reported on its own warned-once flag.
        error: Any = _pick_field(containers, "error")
        if error:
            error_containers = (
                [error, *containers] if isinstance(error, dict) else containers
            )
            if _pick_field(error_containers, "name") == "TOO_MANY_REQUESTS_ERROR":
                window_ms = _pick_field(error_containers, "windowMs", "window_ms")
                cooldown = PROTECT_RATE_LIMIT_WINDOW
                if isinstance(window_ms, (int, float)) and not isinstance(
                    window_ms, bool
                ):
                    try:
                        candidate = float(window_ms) / 1000
                    except OverflowError:
                        candidate = math.inf
                    if math.isfinite(candidate) and candidate > 0:
                        cooldown = candidate
                if self.protect_client is not None:
                    self.protect_client._defer_after_rate_limit(cooldown)
                    _LOGGER.debug(
                        "Protect coordinator: events stream rate limited "
                        "(limit=%s, windowMs=%s); requests deferred",
                        _pick_field(error_containers, "limit"),
                        window_ms,
                    )
                return
            level: int = (
                logging.DEBUG if self._ws_event_error_warned else logging.WARNING
            )
            _LOGGER.log(
                level,
                "Protect coordinator: events stream reported an error: %s",
                message,
            )
            self._ws_event_error_warned = True
            return

        event_type = _pick_field(containers, "type", "eventType", "event_type")
        if not event_type:
            self._log_unparseable_ws_message(
                "Protect coordinator: WebSocket event message missing event type: %s",
                message,
                event_stream=True,
            )
            return

        event_id = _pick_field([*containers, message], "id")
        if not event_id:
            _LOGGER.debug(
                "Protect coordinator: WebSocket %s event missing event id: %s",
                event_type,
                message,
            )
            return

        # New dict, never mutating a container in place (same rationale as
        # _on_websocket_message).
        event_data: dict[str, Any] = {}
        for container in reversed(containers):
            event_data.update(container)
        event_data["id"] = event_id

        try:
            self._handle_event_update(event_type, event_data)
        except Exception:
            # This whole path has never executed against real traffic
            # (class docstring) - never let a wrong schema assumption take
            # down the WS reconnect loop. STALE_EVENT_TIMEOUT reconciliation
            # is the real backstop for a dropped/misparsed frame, not this
            # try/except - this only guarantees the frame doesn't crash us.
            _LOGGER.exception(
                "Protect coordinator: unexpected error processing WebSocket "
                "%s event frame; dropping it and continuing",
                event_type,
            )

    @callback
    def _handle_event_update(self, event_type: str, event_data: dict[str, Any]) -> None:
        """Handle event update from WebSocket."""
        event_id = event_data.get("id")
        if not event_id:
            return

        _LOGGER.debug(
            "Protect coordinator: WebSocket event update for %s: %s",
            event_type,
            event_id,
        )

        # Store event data
        if event_type not in self.data["events"]:
            self.data["events"][event_type] = {}

        self.data["events"][event_type][event_id] = event_data

        # UNVALIDATED (see class docstring): api/protect/models/event.py's
        # `Event` model has no generic "device" field - only camera/
        # cameraId or sensor/sensorId. "device" is checked first to keep
        # existing direct-call tests/behavior unchanged; the rest are
        # tolerated so a real frame using the model's actual field names
        # still resolves instead of `_process_event_for_device` silently
        # never being called (confirmed dead code before this fix).
        device_id = _pick_field(
            [event_data],
            "device",
            "camera",
            "cameraId",
            "camera_id",
            "sensor",
            "sensorId",
            "sensor_id",
            "deviceId",
            "device_id",
        )
        if device_id:
            self._process_event_for_device(event_type, event_data, device_id)

        self.async_update_listeners()

    def _process_event_for_device(
        self, event_type: str, event_data: dict[str, Any], device_id: str
    ) -> None:
        """Process event data and update relevant device."""
        # Check if this is a camera motion event
        if event_type == "motion" and device_id in self.data["cameras"]:
            self._apply_motion_event(
                self.data["cameras"], device_id, event_data, self._camera_motion_started
            )
            _LOGGER.info(
                "Protect coordinator: Motion event for camera %s: start=%s, end=%s",
                device_id,
                event_data.get("start"),
                event_data.get("end"),
            )

        # Check if this is a light motion event
        elif event_type == "motion" and device_id in self.data["lights"]:
            self._apply_motion_event(
                self.data["lights"], device_id, event_data, self._light_motion_started
            )

        # Check if this is a smart detection event
        elif (
            # UNVALIDATED (see class docstring): models/event.py's
            # EventType enum defines SMART_DETECT = "smartDetect", but this
            # integration's original code compared only against
            # "smartDetectZone" - unconfirmed which (or both, e.g. a
            # zone-specific sub-event vs. the general type) a real frame
            # actually sends, so both are accepted rather than guessing.
            event_type in ("smartDetectZone", "smartDetect")
            and device_id in self.data["cameras"]
        ):
            smart_detect_types = event_data.get("smartDetectTypes", [])
            if not isinstance(smart_detect_types, list):
                smart_detect_types = []
            event_start = event_data.get("start", 0)
            event_end = event_data.get("end")

            self._apply_motion_event(
                self.data["cameras"], device_id, event_data, self._camera_motion_started
            )
            self.data["cameras"][device_id]["lastSmartDetectTypes"] = smart_detect_types

            _LOGGER.info(
                "Protect coordinator: Smart detection for camera %s: %s "
                "(start=%s, end=%s)",
                device_id,
                smart_detect_types,
                event_start,
                event_end,
            )

        # Check if this is a doorbell ring event
        elif event_type == "ring" and device_id in self.data["cameras"]:
            self._apply_ring_event(self.data["cameras"], device_id, event_data)
            _LOGGER.info(
                "Protect coordinator: Doorbell ring for camera %s: start=%s, end=%s",
                device_id,
                event_data.get("start"),
                event_data.get("end"),
            )

        elif (
            event_type == EVENT_TYPE_ALARM_HUB_DEVICE_TAMPER
            and device_id in self.data["alarm_hubs"]
        ):
            self._apply_alarm_hub_tamper_event(device_id, event_data)

    def _apply_alarm_hub_tamper_event(
        self, device_id: str, event_data: dict[str, Any]
    ) -> None:
        """
        Apply an alarmHubDeviceTamper event to its hub.

        The status is written into ``alarmHub.deviceTamperStatus`` straight
        away. ``userName`` is recorded as ``_lastTamperUser`` without assuming
        whose name it is (the spec does not say whether it is the person who
        tampered or who restored), and ``start`` (epoch ms) as
        ``_lastTamperAt``.

        A later frame of the same event (its "end", say) may carry only some
        fields, so it fills gaps from the earlier frame rather than wiping
        them. A different event replaces the record outright, so a restore
        that names nobody does not show the previous event's user.
        """
        metadata = event_data.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        status = _event_metadata_text(metadata.get("status"))
        record: dict[str, Any] = {
            "event_id": event_data.get("id"),
            "status": status,
            "received": time.monotonic(),
            "_lastTamperUser": _event_metadata_text(metadata.get("userName")),
            "_lastTamperAt": event_data.get("start"),
        }
        previous = self._alarm_hub_last_tamper.get(device_id)
        if previous is not None and previous.get("event_id") == record["event_id"]:
            if status is None:
                record["received"] = previous["received"]
            for key in ("status", "_lastTamperUser", "_lastTamperAt"):
                if record[key] is None:
                    record[key] = previous[key]
        self._alarm_hub_last_tamper[device_id] = record
        self.data["alarm_hubs"][device_id] = self._with_tamper_record(
            self.data["alarm_hubs"][device_id], record, requested_at=None
        )
        _LOGGER.info(
            "Protect coordinator: Tamper event for alarm hub %s: status=%s",
            device_id,
            status,
        )

    @staticmethod
    def _with_tamper_record(
        hub: dict[str, Any], record: dict[str, Any], *, requested_at: float | None
    ) -> dict[str, Any]:
        """
        Return ``hub`` with its latest tamper event laid on top.

        The event's user and time always apply. Its status applies when
        ``requested_at`` is None (the event itself is being applied), when the
        REST payload has no ``deviceTamperStatus`` of its own (Protect 7.2.x
        sends the event but not the field, and on 7.3.70 the field is
        optional), or when the event arrived after the poll's request was
        sent: that REST snapshot predates the tamper and would otherwise read
        as a false all-clear. Otherwise REST is authoritative. Both times are
        HA's own monotonic clock, so Protect's clock skew cannot matter.
        """
        overlay: dict[str, Any] = {
            "_lastTamperUser": record["_lastTamperUser"],
            "_lastTamperAt": record["_lastTamperAt"],
        }
        status = record["status"]
        if status is not None:
            alarm_hub = hub.get("alarmHub")
            reported = (
                alarm_hub.get("deviceTamperStatus")
                if isinstance(alarm_hub, dict)
                else None
            )
            if (
                requested_at is None
                or not isinstance(reported, str)
                or record["received"] >= requested_at
            ):
                overlay["alarmHub"] = {"deviceTamperStatus": status}
        return _deep_merge(hub, overlay)

    def _apply_motion_event(
        self,
        bucket: dict[str, Any],
        device_id: str,
        event_data: dict[str, Any],
        tracker: dict[str, datetime],
    ) -> None:
        """
        Write lastMotionStart/lastMotionEnd and track the latch for auto-off.

        Feeds the bounded auto-off in `_reconcile_stale_events` (task 2).
        Tracking uses HA's own wall clock rather than the event payload's
        start/end values, so the auto-off timeout is correct regardless of
        whatever timestamp format/units Protect actually sends (unconfirmed
        - see class docstring).
        """
        end = event_data.get("end")
        bucket[device_id]["lastMotionStart"] = event_data.get("start")
        bucket[device_id]["lastMotionEnd"] = end
        if end is None:
            tracker.setdefault(device_id, datetime.now(UTC))
        else:
            tracker.pop(device_id, None)

    def _apply_ring_event(
        self, bucket: dict[str, Any], device_id: str, event_data: dict[str, Any]
    ) -> None:
        """
        Write lastRingStart/lastRingEnd and track the latch for auto-off.

        The doorbell ring latch has the identical dropped-"end"-frame risk
        as motion once the events stream is live, so it gets the same
        safety net (see `_apply_motion_event`).
        """
        end = event_data.get("end")
        bucket[device_id]["lastRingStart"] = event_data.get("start")
        bucket[device_id]["lastRingEnd"] = end
        if end is None:
            self._camera_ring_started.setdefault(device_id, datetime.now(UTC))
        else:
            self._camera_ring_started.pop(device_id, None)

    def _reconcile_stale_events(self) -> None:
        """
        Force-expire motion/smart-detect/ring latches held open too long.

        CRITICAL safety net (task 2): `camera_motion`'s ON condition
        (`isMotionDetected OR (lastMotionStart is not None AND
        lastMotionEnd is None)`) becomes live the moment the events stream
        is wired up. Protect gives no delivery guarantee on the "end"
        frame that closes an event - a WS reconnect, a dropped frame, or a
        network glitch can lose it - and without this, a missing "end"
        would latch a motion/person/vehicle/animal/package-detection or
        doorbell-ring binary sensor ON forever. A permanently-ON motion
        sensor in a home security system is worse than one that never
        fires, so this does not trust event pairing alone: it is called
        from `_async_update_data` (every ~30s REST poll) and from
        `_on_websocket_connection_state_change` (every WS reconnect), in
        addition to the normal paired "end" event clearing the latch
        immediately in `_apply_motion_event`/`_apply_ring_event`.
        """
        now = datetime.now(UTC)
        self._expire_stale_latch(
            "cameras",
            self._camera_motion_started,
            "lastMotionEnd",
            now,
            also_clear_key="lastSmartDetectTypes",
            also_clear_value=[],
        )
        self._expire_stale_latch(
            "lights", self._light_motion_started, "lastMotionEnd", now
        )
        self._expire_stale_latch(
            "cameras", self._camera_ring_started, "lastRingEnd", now
        )

    def _expire_stale_latch(
        self,
        category: str,
        tracker: dict[str, datetime],
        end_key: str,
        now: datetime,
        *,
        also_clear_key: str | None = None,
        also_clear_value: Any = None,
    ) -> None:
        """Clear one tracked latch bucket if it has exceeded STALE_EVENT_TIMEOUT."""
        for device_id in list(tracker):
            started_at = tracker[device_id]
            if now - started_at < STALE_EVENT_TIMEOUT:
                continue
            device = self.data.get(category, {}).get(device_id)
            if isinstance(device, dict) and device.get(end_key) is None:
                _LOGGER.info(
                    "Protect coordinator: auto-clearing stale %s.%s latch for "
                    "%s after exceeding the %s safety timeout (missed 'end' "
                    "event?)",
                    category,
                    end_key,
                    device_id,
                    STALE_EVENT_TIMEOUT,
                )
                device[end_key] = now.isoformat()
                if also_clear_key is not None:
                    device[also_clear_key] = also_clear_value
            tracker.pop(device_id, None)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch Protect data from API."""
        if not self.protect_client:
            _LOGGER.debug("Protect coordinator: No Protect client available")
            return self.data

        try:
            _LOGGER.debug("Protect coordinator: Fetching Protect data")

            # Reconcile stale event-derived latches on every periodic poll
            # (task 2) - runs against the pre-poll data below, before
            # _fetch_cameras() rebuilds the cameras dict wholesale from the
            # REST response.
            self._reconcile_stale_events()

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
            self.data["last_update"] = datetime.now(tz=UTC)

            # Clean up stale devices (Gold requirement)
            self._cleanup_stale_devices()

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

    def _absorb_transient_fetch_error(
        self, collection_key: str, err: Exception
    ) -> bool:
        """
        Return True if a transient fetch error should be absorbed this poll.

        `_fetch_cameras` and `_fetch_lights` run before every other fetcher, so
        letting an error escape aborts the whole poll and raises `UpdateFailed`,
        taking every Protect entity unavailable over one blipped endpoint. The
        first `MAX_CONSECUTIVE_EMPTY_FETCHES` failures are absorbed and the cache
        is preserved instead.

        Absorption is bounded on purpose: past the window the error propagates so
        a real controller outage still marks the entities unavailable rather than
        serving a stale cache indefinitely.
        """
        count = self._consecutive_fetch_errors.get(collection_key, 0) + 1
        self._consecutive_fetch_errors[collection_key] = count
        if count > MAX_CONSECUTIVE_EMPTY_FETCHES:
            _LOGGER.debug(
                "Protect coordinator: %s fetch failed for %d consecutive polls "
                "(%s); failing the update",
                collection_key,
                count,
                err,
            )
            return False
        _LOGGER.debug(
            "Protect coordinator: Error fetching %s (poll %d/%d): %s; "
            "preserving cached devices",
            collection_key,
            count,
            MAX_CONSECUTIVE_EMPTY_FETCHES,
            err,
        )
        # Leave the cached collection untouched and hold the empty-response
        # eviction counter at zero: a failed fetch is no evidence a device was
        # removed. Deliberately not routed through `_update_device_collection`,
        # whose `is_partial` path would log a parse failure that did not happen.
        self._consecutive_empty_fetches[collection_key] = 0
        return True

    def _update_device_collection(
        self,
        collection_key: str,
        new_items: dict[str, Any],
        *,
        is_404: bool = False,
        is_partial: bool = False,
    ) -> None:
        """
        Update a device collection with bounded cache preservation.

        Preserves existing cached items across up to MAX_CONSECUTIVE_EMPTY_FETCHES
        transient empty responses or 404 errors. If empty/404 persists beyond the
        threshold, the collection is cleared so genuinely removed or unadopted devices
        are cleaned up from the device registry.

        `is_partial` marks a response the endpoint already knows is short - it
        dropped an item on a ValidationError. Those results are merged over the
        cache rather than replacing it, because the missing device is still
        adopted and the bounded empty-response guard never sees a short list.
        """
        existing = self.data.get(collection_key)
        if not isinstance(existing, dict):
            existing = {}
            self.data[collection_key] = existing

        if new_items:
            self._consecutive_empty_fetches[collection_key] = 0
            if is_partial:
                # Layer the fresh response over the cache: siblings still get
                # their new state, and the dropped device keeps its last-known
                # entry so `_cleanup_stale_devices` never reads it as removed.
                _LOGGER.debug(
                    "Protect coordinator: %s response was incomplete; merging "
                    "%d fetched over %d cached devices",
                    collection_key,
                    len(new_items),
                    len(existing),
                )
                self.data[collection_key] = {**existing, **new_items}
                return
            # known gap: completeness is only known for items the endpoint itself
            # dropped. A response short because the controller omitted a device
            # (e.g. 3 of 5 during a partial start) still looks authoritative here;
            # the grace window in `_cleanup_stale_devices` is what covers that.
            self.data[collection_key] = new_items
            return

        # An incomplete response that came back with nothing left is a parse
        # failure across the whole collection, not an empty controller. Treat
        # it like a fetch error: preserve the cache and do not advance the
        # eviction counter, or a schema change affecting every device of a
        # family would purge them all a few polls later.
        if is_partial and existing:
            _LOGGER.warning(
                "Protect coordinator: every %s in the response failed to parse; "
                "preserving %d cached devices",
                collection_key,
                len(existing),
            )
            self._consecutive_empty_fetches[collection_key] = 0
            return

        # Response is empty or 404
        if not existing:
            # Collection was already empty; nothing to preserve
            self.data[collection_key] = {}
            self._consecutive_empty_fetches[collection_key] = 0
            if is_404:
                _LOGGER.debug(
                    (
                        "Protect coordinator: %s endpoint returned 404;"
                        " no devices configured"
                    ),
                    collection_key,
                )
            return

        # Collection had items; handle transient vs persistent outage
        count = self._consecutive_empty_fetches.get(collection_key, 0) + 1
        self._consecutive_empty_fetches[collection_key] = count
        status_desc = "404" if is_404 else "empty response"

        if count <= MAX_CONSECUTIVE_EMPTY_FETCHES:
            _LOGGER.debug(
                "Protect coordinator: %s fetch returned %s (poll %d/%d); "
                "preserving %d cached devices",
                collection_key,
                status_desc,
                count,
                MAX_CONSECUTIVE_EMPTY_FETCHES,
                len(existing),
            )
        else:
            _LOGGER.warning(
                "Protect coordinator: %s fetch returned %s for %d consecutive polls; "
                "clearing cached devices",
                collection_key,
                status_desc,
                count,
            )
            self.data[collection_key] = {}
            self._consecutive_empty_fetches[collection_key] = 0

    async def _fetch_cameras(self) -> None:
        """Fetch camera data."""
        if not self.protect_client:
            return

        _LOGGER.debug("Protect coordinator: Fetching cameras")
        try:
            cameras_models = await self.protect_client.cameras.get_all()
            # Rebuild the dict from the API response so cameras removed from
            # Protect disappear from coordinator data (enables stale cleanup).
            cameras: dict[str, Any] = {}
            for camera_model in cameras_models:
                camera = self._normalize_camera_data(self._model_to_dict(camera_model))
                camera_id = camera.get("id")
                if camera_id:
                    cameras[camera_id] = camera

                    _LOGGER.debug(
                        "Protect coordinator: Camera %s supports smart detection: %s",
                        camera.get("name", camera_id),
                        camera.get("smartDetectTypes", []),
                    )
            self._consecutive_fetch_errors["cameras"] = 0
            self._update_device_collection(
                "cameras",
                cameras,
                is_partial=not self.protect_client.cameras.last_result_complete,
            )
        except UniFiNotFoundError:
            # The endpoint answered, so the session is alive: end any error streak.
            self._consecutive_fetch_errors["cameras"] = 0
            self._update_device_collection("cameras", {}, is_404=True)
        except (UniFiConnectionError, UniFiTimeoutError, UniFiResponseError) as err:
            # Auth and unexpected errors deliberately stay uncaught: reauth must
            # still trigger, and an unknown failure should not be papered over.
            if not self._absorb_transient_fetch_error("cameras", err):
                raise
        self._drop_rebuilt_latch_trackers(self.data["cameras"])

    def _drop_rebuilt_latch_trackers(self, cameras: dict[str, Any]) -> None:
        """
        Pop event-derived latch trackers whose backing field vanished under them.

        `_fetch_cameras()` wholesale-replaces `self.data["cameras"]` every
        ~30s from REST models that carry no `lastMotionStart`/`lastRingStart`
        field at all - those are only ever written by a paired WebSocket
        "start" event (see `_apply_motion_event`/`_apply_ring_event`), never
        by the REST API (confirmed: api/protect/models/camera.py declares
        neither field). Without this, a still-armed tracker survives the
        rebuild pointing at a camera dict that now has no "start" (or "end")
        field either, and ~5 minutes later `_reconcile_stale_events` treats
        that as an orphaned latch and logs a false "missed 'end' event?"
        warning - even though the latch was already correctly cleared by
        this exact REST poll 4m30s earlier. This fires on virtually every
        real motion/ring event (the common "start"-only frame), flooding
        INFO logs during exactly the window someone is watching them to
        confirm a deploy worked.

        Only pops when the camera is still present but has lost the field
        entirely; a camera that vanished outright is left to the existing
        "removed device" handling in `_expire_stale_latch`.
        """
        for device_id in list(self._camera_motion_started):
            camera = cameras.get(device_id)
            if isinstance(camera, dict) and "lastMotionStart" not in camera:
                self._camera_motion_started.pop(device_id, None)

        for device_id in list(self._camera_ring_started):
            camera = cameras.get(device_id)
            if isinstance(camera, dict) and "lastRingStart" not in camera:
                self._camera_ring_started.pop(device_id, None)

    async def _fetch_lights(self) -> None:
        """Fetch light data."""
        if not self.protect_client:
            return

        _LOGGER.debug("Protect coordinator: Fetching lights")
        try:
            lights_models = await self.protect_client.lights.get_all()
            lights: dict[str, Any] = {}
            for light_model in lights_models:
                light = self._model_to_dict(light_model)
                light_id = light.get("id")
                if light_id:
                    lights[light_id] = light
            self._consecutive_fetch_errors["lights"] = 0
            self._update_device_collection(
                "lights",
                lights,
                is_partial=not self.protect_client.lights.last_result_complete,
            )
        except UniFiNotFoundError:
            # The endpoint answered, so the session is alive: end any error streak.
            self._consecutive_fetch_errors["lights"] = 0
            self._update_device_collection("lights", {}, is_404=True)
        except (UniFiConnectionError, UniFiTimeoutError, UniFiResponseError) as err:
            # See `_fetch_cameras` for why this is bounded rather than swallowed.
            if not self._absorb_transient_fetch_error("lights", err):
                raise

    async def _fetch_sensors(self) -> None:
        """Fetch sensor data."""
        if not self.protect_client:
            return

        _LOGGER.debug("Protect coordinator: Fetching sensors")
        fetch_start_time = time.monotonic()
        try:
            sensors_models = await self.protect_client.sensors.get_all()
            sensors: dict[str, Any] = {}
            for sensor_model in sensors_models:
                sensor = self._model_to_dict(sensor_model)
                sensor_id = sensor.get("id")
                if sensor_id:
                    sensors[sensor_id] = sensor

            # Protect newer door/sensor states from being overwritten
            # by older REST responses
            existing_sensors = self.data.get("sensors", {})
            if isinstance(existing_sensors, dict):
                for s_id, rest_sensor in sensors.items():
                    cached_sensor = existing_sensors.get(s_id)
                    if not isinstance(cached_sensor, dict):
                        continue
                    preserved_groups = self._should_preserve_cached_door_state(
                        cached_sensor, rest_sensor, fetch_start_time, s_id
                    )
                    if preserved_groups:
                        self._merge_preserved_door_state(
                            cached_sensor, rest_sensor, preserved_groups
                        )

            self._update_device_collection(
                "sensors",
                sensors,
                is_partial=not self.protect_client.sensors.last_result_complete,
            )
            self._consecutive_fetch_errors["sensors"] = 0
            _LOGGER.debug(
                "Protect coordinator: Successfully fetched %d sensors",
                len(sensors_models),
            )
        except UniFiNotFoundError:
            self._consecutive_fetch_errors["sensors"] = 0
            self._update_device_collection("sensors", {}, is_404=True)
        except UniFiAuthenticationError as err:
            self._handle_auth_error(err)
        except (UniFiConnectionError, UniFiTimeoutError, UniFiResponseError) as err:
            if not self._absorb_transient_fetch_error("sensors", err):
                raise
        except Exception as err:
            _LOGGER.warning("Protect coordinator: Error fetching sensors: %s", err)

    def _should_preserve_cached_door_state(
        self,
        cached_sensor: dict[str, Any],
        rest_sensor: dict[str, Any],
        fetch_start_time: float,
        sensor_id: str,
    ) -> frozenset[str]:
        """
        Determine which door/motion/tamper/leak field GROUPS to keep cached.

        Returns the subset of `_PRESERVED_SENSOR_STATE_FIELD_GROUPS` keys
        whose fields `_merge_preserved_door_state` should copy from the
        cache onto the incoming REST sensor dict, instead of letting REST's
        own value win. Evaluated independently per group so that a
        WebSocket frame carrying only one group's fields (e.g. a
        motion-only frame from a UP-Sense that shares one device id across
        door/motion/environment sensing) can only ever preserve THAT
        group - it can no longer suppress a genuinely newer REST
        transition on an unrelated group the way a single shared
        preservation decision used to.

        Branch 1 (WebSocket-recency) below is genuinely per-group: it reads
        `self._sensor_last_ws_update[sensor_id]`, which
        `_handle_device_update` only stamps for the specific group(s) a
        frame actually carried. Branches 2/3 (REST-vs-cache
        `openStatusChangedAt` timestamp comparison) have no equivalent
        signal for motion/tamper/leak - they only ever reasoned about the
        door group's own timestamp - so they can only ever contribute
        "door" to the returned set, exactly as before this method returned
        a single bool.

        Bounded by `MAX_DOOR_STATE_PRESERVE_POLLS` via
        `self._sensor_preserve_counts` for the door-timestamp branches:
        `_merge_preserved_door_state` copies the preserved timestamp back
        onto the dict written to `self.data["sensors"]`, so without a
        bound the cache would re-seed its own "I am newer" signal on every
        subsequent poll whenever REST never reports a timestamp, or
        reports one that never advances past the cache - a permanent
        wedge, worse than the stale-read bug this preservation exists to
        fix.

        The WebSocket-recency check (branch 1) is bounded separately, per
        group, by `MAX_WS_RECENCY_PRESERVE_POLLS` via
        `self._sensor_ws_recency_preserve_counts` - NOT the tighter
        `MAX_DOOR_STATE_PRESERVE_POLLS` used below - and additionally
        LATCHED via `self._sensor_ws_recency_latched` once a group's cap
        trips, so a sustained condition resolves once instead of flipping
        every `MAX_WS_RECENCY_PRESERVE_POLLS`-th poll. See both constants'
        docstrings for the full history of why an earlier, unbounded
        version of this branch wedged the cache, and why a naive bounded
        -but-not-latched version merely turned the wedge into a periodic
        flip.
        """
        preserved_groups: set[str] = set()

        # 1. WebSocket update arrived during or after this REST request
        # started, for a SPECIFIC group - i.e. a real frame carrying that
        # group's field(s) (see the gated per-group stamp in
        # `_handle_device_update`) raced ahead of this specific REST
        # request. Bounded and latched per (sensor, group) pair so that
        # neither counters nor latches from one group, or from the
        # timestamp-comparison branches below, can conflate with another.
        group_ws_updates = self._sensor_last_ws_update.get(sensor_id, {})
        ws_counts = self._sensor_ws_recency_preserve_counts.setdefault(sensor_id, {})
        latched_groups = self._sensor_ws_recency_latched.setdefault(sensor_id, set())

        def _evaluate_ws_recency(group: str) -> bool:
            if group in latched_groups:
                if self._group_state_agrees(cached_sensor, rest_sensor, group):
                    # REST has caught up: clear the latch and fall through
                    # to a fresh evaluation below, exactly as if this group
                    # had never hit the cap.
                    latched_groups.discard(group)
                    ws_counts[group] = 0
                else:
                    # Still disagreeing: keep letting REST win, and do NOT
                    # touch the counter - this is what makes the
                    # resolution a one-time event, not a repeating flip.
                    return False

            last_ws_update = group_ws_updates.get(group)
            if last_ws_update is None or last_ws_update < fetch_start_time:
                ws_counts[group] = 0
                return False

            ws_preserve_count = ws_counts.get(group, 0)
            if ws_preserve_count >= MAX_WS_RECENCY_PRESERVE_POLLS:
                self._log_preserve_cap_hit(
                    sensor_id,
                    "Protect coordinator: sensor %s %s WS-recency door "
                    "state preservation hit the %d-poll cap; letting REST "
                    "win until it agrees with the cached state, to avoid "
                    "wedging or flip-flopping the cached state",
                    sensor_id,
                    group,
                    MAX_WS_RECENCY_PRESERVE_POLLS,
                )
                latched_groups.add(group)
                return False
            ws_counts[group] = ws_preserve_count + 1
            return True

        for group in _PRESERVED_SENSOR_STATE_FIELD_GROUPS:
            if _evaluate_ws_recency(group):
                preserved_groups.add(group)

        # 2/3. Door-only timestamp comparison, skipped if branch 1 above
        # already decided to preserve "door" via WS-recency.
        if "door" not in preserved_groups:
            cached_ts = self._get_field(
                cached_sensor, "openStatusChangedAt", "open_status_changed_at"
            )
            rest_ts = self._get_field(
                rest_sensor, "openStatusChangedAt", "open_status_changed_at"
            )
            preserve_count = self._sensor_preserve_counts.get(sensor_id, 0)

            def _preserve_bounded() -> bool:
                if preserve_count >= MAX_DOOR_STATE_PRESERVE_POLLS:
                    self._log_preserve_cap_hit(
                        sensor_id,
                        "Protect coordinator: sensor %s door state "
                        "preservation hit the %d-poll cap; letting REST "
                        "win to avoid wedging the cached state "
                        "permanently",
                        sensor_id,
                        MAX_DOOR_STATE_PRESERVE_POLLS,
                    )
                    self._sensor_preserve_counts[sensor_id] = 0
                    return False
                self._sensor_preserve_counts[sensor_id] = preserve_count + 1
                return True

            # Both sides report a timestamp: normalize to a common unit
            # (finding 2: raw numeric comparison let a ms-epoch cache
            # value always beat a genuinely newer s-epoch REST value) and
            # compare regardless of the two payloads' original Python
            # types (finding 3: the old isinstance-per-pair comparator had
            # no (int, datetime) branch, so a type mismatch fell through
            # and was misread as "REST wins").
            cached_norm = _normalize_epoch_seconds(cached_ts)
            rest_norm = _normalize_epoch_seconds(rest_ts)
            if cached_norm is not None and rest_norm is not None:
                if cached_norm > rest_norm:
                    if _preserve_bounded():
                        preserved_groups.add("door")
                else:
                    self._sensor_preserve_counts[sensor_id] = 0
            elif cached_norm is not None and rest_norm is None:
                # Cached has a (normalizable) timestamp but REST's is
                # missing or unparseable: preserve, bounded the same way.
                if _preserve_bounded():
                    preserved_groups.add("door")
            else:
                self._sensor_preserve_counts[sensor_id] = 0

        return frozenset(preserved_groups)

    @staticmethod
    def _group_state_agrees(
        cached_sensor: dict[str, Any], rest_sensor: dict[str, Any], group: str
    ) -> bool:
        """
        Return True if REST's report for `group` already matches the cache.

        Compares only the group's *state* field(s) (see
        `_PRESERVED_SENSOR_STATE_GROUP_STATE_FIELDS`), not its `*At`
        timestamp - a matching timestamp representation is not required for
        the WS-recency latch in `_should_preserve_cached_door_state` to
        clear, only agreement on what actually happened. A field spelling
        absent from both sides is treated as agreeing (nothing to disagree
        about); present on only one side is treated as NOT agreeing, since
        that is itself a discrepancy.
        """
        fields = _PRESERVED_SENSOR_STATE_GROUP_STATE_FIELDS[group]
        if group == "leak" and any(
            cached_sensor.get(field) is not None or rest_sensor.get(field) is not None
            for field in _LEAK_STATE_FIELDS
        ):
            fields = _LEAK_STATE_FIELDS

        for field in fields:
            cached_has = field in cached_sensor
            rest_has = field in rest_sensor
            if cached_has != rest_has:
                return False
            if cached_has and cached_sensor[field] != rest_sensor[field]:
                return False
        return True

    @staticmethod
    def _merge_preserved_door_state(
        cached_sensor: dict[str, Any],
        rest_sensor: dict[str, Any],
        preserved_groups: frozenset[str],
    ) -> None:
        """
        Preserve cached sensor event states on the incoming REST sensor dictionary.

        Copies ONLY the fields belonging to `preserved_groups` - the exact
        groups `_should_preserve_cached_door_state` decided to preserve -
        using `_PRESERVED_SENSOR_STATE_FIELD_GROUPS` as the SAME
        per-group field mapping `_handle_device_update` uses to decide
        whether an incoming WebSocket frame counts as belonging to that
        group, so "what we preserve" and "what counts as a group's state
        frame" can never drift apart per group (see that constant's
        docstring). Groups NOT in `preserved_groups` are left alone, so a
        WebSocket frame that only justified preserving e.g. "motion"
        cannot also drag a stale cached "door" value back over a
        genuinely newer REST door transition.
        """
        for group in preserved_groups:
            for field in _PRESERVED_SENSOR_STATE_FIELD_GROUPS[group]:
                if field in cached_sensor:
                    rest_sensor[field] = cached_sensor[field]

    def _log_preserve_cap_hit(self, sensor_id: str, msg: str, *args: Any) -> None:
        """
        Log a door-state-preservation cap-hit warning once per sensor id.

        Matches `_log_unparseable_ws_message`'s warn-once-then-debug
        precedent (`_ws_parse_warned`): without this, a sensor whose REST
        timestamp is persistently stuck below the cache - or whose
        WebSocket stream keeps re-announcing the same state - re-logs a
        WARNING roughly every cap-many polls (~90s at
        MAX_DOOR_STATE_PRESERVE_POLLS's default) for as long as the
        condition persists, which can log-storm a production instance the
        same way an unparseable WS frame could before that precedent was
        added. Shared across both the WS-recency cap and the timestamp
        -comparison cap - a sensor that already warned for one does not
        need a second first-time WARNING for the other.
        """
        level = (
            logging.DEBUG
            if sensor_id in self._sensor_preserve_cap_warned
            else logging.WARNING
        )
        _LOGGER.log(level, msg, *args)
        self._sensor_preserve_cap_warned.add(sensor_id)

    @staticmethod
    def _get_field(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
        """Get a field from data using multiple possible key names."""
        for key in keys:
            if key in data and data[key] is not None:
                return data[key]
        return default

    async def _fetch_nvr(self) -> None:
        """Fetch NVR data."""
        if not self.protect_client:
            return

        _LOGGER.debug("Protect coordinator: Fetching NVR")
        generation = self._ws_generation["nvrs"]
        try:
            nvr_model = await self.protect_client.nvr.get()
            if self._ws_generation["nvrs"] != generation:
                # A frame for the NVR landed while the request was out, so it
                # is newer than the answer, and applying the answer would undo
                # it (an arm mode that has just changed, for one).
                _LOGGER.debug(
                    "Protect coordinator: a WebSocket frame for the NVR arrived "
                    "while it was being fetched; keeping it instead of the "
                    "older reading"
                )
                return
            nvr = self._model_to_dict(nvr_model)
            nvr_id = nvr.get("id") if isinstance(nvr, dict) else None
            if nvr_id:
                self._update_device_collection("nvrs", {nvr_id: nvr})
                _LOGGER.debug(
                    "Protect coordinator: Successfully fetched NVR: %s", nvr_id
                )
            else:
                self._update_device_collection("nvrs", {})
        except UniFiNotFoundError:
            self._update_device_collection("nvrs", {}, is_404=True)
        except Exception as err:
            _LOGGER.debug("Protect coordinator: Error fetching NVR: %s", err)

    async def _fetch_chimes(self) -> None:
        """Fetch chime data."""
        if not self.protect_client:
            return

        _LOGGER.debug("Protect coordinator: Fetching chimes")
        try:
            chimes_models = await self.protect_client.chimes.get_all()
            chimes: dict[str, Any] = {}
            for chime_model in chimes_models:
                chime = self._model_to_dict(chime_model)
                chime_id = chime.get("id")
                if chime_id:
                    chimes[chime_id] = chime
            self._update_device_collection(
                "chimes",
                chimes,
                is_partial=not self.protect_client.chimes.last_result_complete,
            )
            self._log_family_fetch_recovery("chimes")
            _LOGGER.debug(
                "Protect coordinator: Successfully fetched %d chimes",
                len(chimes_models),
            )
        except UniFiNotFoundError:
            self._update_device_collection("chimes", {}, is_404=True)
        except Exception as err:
            self._log_family_fetch_error("chimes", err)

    async def _fetch_viewers(self) -> None:
        """Fetch viewer data."""
        if not self.protect_client:
            return

        _LOGGER.debug("Protect coordinator: Fetching viewers")
        try:
            if hasattr(self.protect_client, "viewers"):
                viewers_models = await self.protect_client.viewers.get_all()
                viewers: dict[str, Any] = {}
                for viewer_model in viewers_models:
                    viewer = self._model_to_dict(viewer_model)
                    viewer_id = viewer.get("id")
                    if viewer_id:
                        viewers[viewer_id] = viewer
                complete = self.protect_client.viewers.last_result_complete
                self._update_device_collection(
                    "viewers", viewers, is_partial=not complete
                )
                _LOGGER.debug(
                    "Protect coordinator: Successfully fetched %d viewers",
                    len(viewers_models),
                )
        except UniFiNotFoundError:
            self._update_device_collection("viewers", {}, is_404=True)
        except Exception as err:
            _LOGGER.debug("Protect coordinator: Error fetching viewers: %s", err)

    async def _fetch_liveviews(self) -> None:
        """Fetch liveview data."""
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
                self.data["liveviews"] = liveviews
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
        """Fetch alarm hub data, keeping the latest tamper event on top."""
        requested_at = time.monotonic()
        await self._fetch_device_family("alarm_hubs")
        hubs = self.data["alarm_hubs"]
        for hub_id, record in self._alarm_hub_last_tamper.items():
            hub = hubs.get(hub_id)
            if isinstance(hub, dict):
                hubs[hub_id] = self._with_tamper_record(
                    hub, record, requested_at=requested_at
                )

    async def _fetch_sirens(self) -> None:
        """Fetch siren data (the endpoint is absent on older Protect versions)."""
        await self._fetch_device_family("sirens")
        self._apply_siren_stop_guards()

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

    async def _fetch_device_family(self, collection: str) -> None:
        """
        Fetch one simple device family into ``self.data[collection]``.

        ``collection`` is both the data key and the protect_client endpoint
        attribute ("fobs", "link_stations", "alarm_hubs", "sirens").

        A 404, or a 2xx web page instead of JSON, before the family has ever
        answered means this Protect version does not have the endpoint (it is
        not "no devices": an empty family answers ``[]``). That is logged once
        at INFO and the family is skipped until UNSUPPORTED_RESOURCE_RETRY has
        passed. A 404 after a successful answer goes through the bounded 404
        cache path like every other collection. Any other error - including a
        web page after a successful answer, or a 400 - keeps the cached
        devices, as for chimes.
        """
        if not self.protect_client:
            return

        retry_at = self._unsupported_until.get(collection)
        if retry_at is not None and time.monotonic() < retry_at:
            return

        endpoint = getattr(self.protect_client, collection)
        answered = collection in self._answered_families
        generation = self._ws_generation.get(collection)
        _LOGGER.debug("Protect coordinator: Fetching %s", collection)
        try:
            models = await endpoint.get_all(expected_unsupported=not answered)
        except UniFiNotFoundError:
            if answered:
                self._update_device_collection(collection, {}, is_404=True)
                return
            self._mark_family_unsupported(collection, "HTTP 404")
            return
        except UniFiResponseError as err:
            if answered or err.status_code >= HTTPStatus.MULTIPLE_CHOICES:
                self._log_family_fetch_error(collection, err)
                return
            self._mark_family_unsupported(collection, "non-JSON response")
            return
        except Exception as err:
            self._log_family_fetch_error(collection, err)
            return

        self._log_family_fetch_recovery(collection)
        self._unsupported_until.pop(collection, None)
        self._answered_families.add(collection)
        if self._ws_generation.get(collection) != generation:
            # The console sent a frame for this family while the request was
            # out, so the frame is newer than the answer. Applying the answer
            # would replace the family and undo it.
            _LOGGER.debug(
                "Protect coordinator: a WebSocket frame for %s arrived while "
                "it was being fetched; keeping it instead of the older reading",
                collection,
            )
            return
        devices: dict[str, Any] = {}
        for model in models:
            device = self._model_to_dict(model)
            device_id = device.get("id")
            if device_id:
                devices[device_id] = device
        self._update_device_collection(
            collection, devices, is_partial=not endpoint.last_result_complete
        )
        _LOGGER.debug(
            "Protect coordinator: Successfully fetched %d %s", len(devices), collection
        )

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

    def _cleanup_stale_devices(self) -> None:
        """
        Remove stale Protect devices from the device registry (Gold requirement).

        A device is only evicted once it has been absent for more than
        MAX_CONSECUTIVE_MISSING_POLLS consecutive polls. A single poll is not
        evidence of removal: a partial controller response, or an item that
        `get_all()` skipped on a ValidationError, drops a still-adopted device
        out of the collection, and `async_update_device(remove_config_entry_id=)`
        cannot be undone. Devices still inside the grace window stay in the
        tracked set so their absence keeps accumulating across polls.
        """
        device_registry = dr.async_get(self.hass)

        for device_type in [
            "cameras",
            "lights",
            "sensors",
            "nvrs",
            "viewers",
            "chimes",
            "fobs",
            "link_stations",
            "alarm_hubs",
            "sirens",
        ]:
            current_ids: set[str] = set(self.data.get(device_type, {}).keys())
            previous_ids = self._previous_protect_device_ids.get(device_type, set())
            missing_polls = self._consecutive_missing_polls.setdefault(device_type, {})

            # A device that reported in restarts its grace window.
            for device_id in current_ids:
                missing_polls.pop(device_id, None)

            # Devices still within the grace window are kept under observation
            # rather than evicted, and stay tracked for the next poll.
            pending_ids: set[str] = set()

            for device_id in previous_ids - current_ids:
                count = missing_polls.get(device_id, 0) + 1
                if count <= MAX_CONSECUTIVE_MISSING_POLLS:
                    missing_polls[device_id] = count
                    pending_ids.add(device_id)
                    _LOGGER.debug(
                        "Protect coordinator: %s device %s missing from poll "
                        "%d/%d; deferring registry removal",
                        device_type,
                        device_id,
                        count,
                        MAX_CONSECUTIVE_MISSING_POLLS,
                    )
                    continue

                missing_polls.pop(device_id, None)
                # Try both identifier patterns (with and without "protect_" prefix)
                for identifier in [
                    f"protect_{device_type[:-1]}_{device_id}",  # protect_camera_xyz
                    device_id,  # Just the device ID
                ]:
                    device = async_get_device_entry(
                        device_registry,
                        (DOMAIN, identifier),
                        self.config_entry.entry_id,
                    )
                    if device:
                        _LOGGER.info(
                            "Protect coordinator: Removing stale %s device: %s "
                            "(absent for %d consecutive polls)",
                            device_type,
                            device_id,
                            count,
                        )
                        device_registry.async_update_device(
                            device_id=device.id,
                            remove_config_entry_id=self.config_entry.entry_id,
                        )
                        break

                if device_type == "sensors":
                    # Matches `_drop_rebuilt_latch_trackers` / the removed-
                    # device branch of `_expire_stale_latch`: a sensor's
                    # per-device tracking state must not survive its
                    # eviction from the registry, or a device ID reused
                    # (unlikely but not impossible) or simply retained in
                    # these dicts forever would leak memory and could feed
                    # a stale `_sensor_last_ws_update` timestamp into
                    # `_should_preserve_cached_door_state` for an unrelated
                    # future adoption of the same ID. Popping by device_id
                    # removes the whole per-group sub-dict/sub-set for the
                    # nested trackers below in one call, so a sensor
                    # re-adopted on a later poll always starts with a
                    # counter of 0 and no latch or warning flag, never
                    # inheriting state left over from the evicted device.
                    self._sensor_last_ws_update.pop(device_id, None)
                    self._sensor_preserve_counts.pop(device_id, None)
                    self._sensor_ws_recency_preserve_counts.pop(device_id, None)
                    self._sensor_ws_recency_latched.pop(device_id, None)
                    self._sensor_preserve_cap_warned.discard(device_id)
                elif device_type == "alarm_hubs":
                    self._alarm_hub_last_tamper.pop(device_id, None)

            self._previous_protect_device_ids[device_type] = current_ids | pending_ids

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
        stopped, and still says it is active, cannot switch it back on (see
        `_apply_siren_stop_guards`).
        """
        siren = self.data["sirens"].get(siren_id)
        if not isinstance(siren, dict):
            return
        status = siren.get("sirenStatus")
        self._siren_stopped_at[siren_id] = _SirenStop(
            activated_at=_number(
                status.get("activatedAt") if isinstance(status, dict) else None
            ),
            stopped_at_ms=datetime.now(tz=UTC).timestamp() * 1000,
            monotonic=time.monotonic(),
        )
        self.data["sirens"][siren_id] = _deep_merge(
            siren, {"sirenStatus": {"isActive": False}}
        )
        self.async_update_listeners()

    @callback
    def _apply_siren_stop_guards(self) -> None:
        """
        Keep a stopped siren off when a stale reading says it is still active.

        A poll that was already in flight when the siren was stopped answers
        with ``isActive: true`` and the ``activatedAt`` of the run that was
        just stopped. That run is recognised by comparing ``activatedAt`` with
        the one cached at the stop, both stamped by the console: a reading
        whose ``activatedAt`` is the same, or older, is the stopped run, and a
        later one is a new run. Home Assistant's clock is not compared with
        the console's, which would be wrong by however far apart they are. Only
        a siren whose cache held no ``activatedAt`` at the stop falls back to
        the wall clock.

        The guard ends with the first reading that is not the stopped run: an
        idle siren, a siren that has gone, a new run, or an active siren with
        no start time to compare. It also ends after `SIREN_STOP_GUARD_TTL`,
        so it cannot linger.
        """
        now = time.monotonic()
        for siren_id, stop in list(self._siren_stopped_at.items()):
            siren = self.data["sirens"].get(siren_id)
            status = siren.get("sirenStatus") if isinstance(siren, dict) else None
            activated_at = _number(
                status.get("activatedAt") if isinstance(status, dict) else None
            )
            limit = (
                stop.activated_at
                if stop.activated_at is not None
                else stop.stopped_at_ms
            )
            if (
                now - stop.monotonic <= SIREN_STOP_GUARD_TTL
                and isinstance(siren, dict)
                and isinstance(status, dict)
                and status.get("isActive") is True
                and activated_at is not None
                and activated_at <= limit
            ):
                self.data["sirens"][siren_id] = _deep_merge(
                    siren, {"sirenStatus": {"isActive": False}}
                )
            else:
                del self._siren_stopped_at[siren_id]
