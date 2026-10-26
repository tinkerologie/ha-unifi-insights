# Copyright 2026 UniFi Insights contributors
"""
Pure reconciliation of Protect WebSocket frames and REST polls.

The Protect coordinator has two sources of truth that race each other: the
"devices" and "events" WebSocket streams, and a REST poll every 30 seconds.
Every decision about how one input changes the cache lives here, as one pure
function (ADR-007's `protect_events.py` seam, #340)::

    reconcile(state, cache, input, now) -> Step(state, cache, effects)

- ``state`` is a frozen `ReconcilerState`: the event latches, when each
  stored event was first seen, per-sensor WebSocket recency and preserve
  counters, alarm hub tamper records, siren stop guards, WebSocket generation
  counters, and each device family's empty, error and missing-poll counts.
- ``cache`` is the coordinator's ``data``. It is never changed in place
  (copy-on-write, ADR-010): a step that changes anything returns a new root
  that shares every collection and record it did not touch, and a step that
  changes nothing returns ``cache`` itself.
- ``input`` is a frame (`DeviceFrame`, `EventFrame`), the answer to one REST
  request (`RestBatch`), a poll boundary (`PollStarted`, `PollCompleted`), a
  stream reconnect (`StreamReconnected`), a siren stop (`SirenStopped`) or a
  command overlay (`CommandOverlay`).
- ``now`` is an `Instant` carrying both clocks, read once per input by the
  coordinator: monotonic time for request stamps, the tamper race and the
  siren guard lifetime; wall time for the latch timeout (its expiry is written
  as an ISO string), the event store's retention and the siren stop fallback.

Side effects come back as data (`PublishUpdate`, `RemoveDevice`, `Escalate`,
`Log`) for the coordinator to carry out in order, so its log lines still come
from the ``coordinators.protect`` logger. This module imports nothing from
Home Assistant, the API library or the clock; a test enforces that.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal, assert_never

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
    EVENT_TYPE_ALARM_HUB_DEVICE_TAMPER,
    MAX_CONSECUTIVE_MISSING_POLLS,
    MODEL_KEY_FOB,
    MODEL_KEY_LINKSTATION,
    SCAN_INTERVAL_PROTECT,
    TIMELINE_MAX_HOURS,
)
from custom_components.unifi_insights.data_transforms import get_field

if TYPE_CHECKING:
    from collections.abc import Mapping

type Group = Literal["door", "motion", "tamper", "leak"]
type LatchKind = Literal["camera_motion", "light_motion", "camera_ring"]
type Family = Literal[
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
]
type GuardedFamily = Literal["nvrs", "sirens"]

# Every family the registry clean-up watches, in the order it walks them.
FAMILIES: Final[tuple[Family, ...]] = (
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
)

# Bounded auto-off for the event-derived motion, smart-detect and ring
# latches. Protect does not guarantee the "end" frame of an event (a
# reconnect, a dropped frame or a network glitch can lose it), and the motion
# and detection binary sensors are on from "start seen, end not yet seen", so
# a lost "end" would otherwise keep them on forever; a stuck motion sensor in
# a security system is worse than one that never fires. Five minutes is well
# above the 30 second poll (no false expiry from poll jitter) and above any
# real single motion or ring event, yet short enough that a stuck sensor heals
# within minutes. It is the same for every event type.
STALE_EVENT_TIMEOUT: Final = timedelta(minutes=5)

# How long the event store keeps an event, counted from the first sweep that
# saw it. The event timeline card, the store's only reader, shows at most the
# last `TIMELINE_MAX_HOURS` by the event's own start; an event starts before
# it arrives, so this keeps every event the timeline can show. The hour of
# margin covers a console clock ahead of Home Assistant's.
EVENT_RETENTION: Final = timedelta(hours=TIMELINE_MAX_HOURS + 1)

# How often the event store is swept. A sweep walks every stored event, so it
# runs at most this often rather than on every input; an event is kept at
# most this much longer than `EVENT_RETENTION`.
EVENT_SWEEP_INTERVAL: Final = timedelta(minutes=5)

# How many empty (or 404) answers in a row keep a family's cached devices,
# and how many transient errors in a row are absorbed before the poll fails.
MAX_CONSECUTIVE_EMPTY_FETCHES: Final = 3

# How many polls in a row the door timestamp comparison may keep a cached
# door state over REST. The preserved timestamp is copied back into the
# cache, so without a bound a REST answer that never reports a timestamp (or
# one stuck below the cache) would re-seed "the cache is newer" forever.
MAX_DOOR_STATE_PRESERVE_POLLS: Final = 3

# How many polls in a row a sensor group may be preserved because a frame for
# it landed while the request was out. It only fires for a group whose own
# fields arrived, so it normally resolves itself; the cap is a safety net for
# a device re-announcing a state that REST never catches up with. It is
# looser than the door cap because a door that really flaps sends a real
# frame on every poll, and live data should win then. Once the cap trips the
# group is latched: REST wins on every later poll until it agrees with the
# cache. Resetting the counter instead let the cycle restart, flipping a
# contact once every eleven polls.
MAX_WS_RECENCY_PRESERVE_POLLS: Final = 10

# A stopped siren is only guarded against a reading that was already in
# flight when it was stopped. A request does not outlive a poll interval or
# two, so the guard does not either.
SIREN_STOP_GUARD_TTL: Final = 2 * SCAN_INTERVAL_PROTECT.total_seconds()

_MILLISECOND_EPOCH_THRESHOLD: Final = 100_000_000_000.0

# The fields that make a sensor frame a door, motion, tamper or leak *state*
# frame rather than telemetry, per group. A frame stamps WebSocket recency
# only for the groups whose fields it carries, and a preserved group copies
# back only its own fields, so a motion-only frame from a multi-sensor can
# never hold back a newer REST door transition.
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
# `externalLeakDetectedAt` is the external probe channel of multi-channel
# leak sensors such as the USL-Environmental.
_LEAK_TIMESTAMP_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "leakDetectedAt",
        "leak_detected_at",
        "externalLeakDetectedAt",
        "external_leak_detected_at",
    }
)

# Group -> every spelling (state and timestamp) that marks a frame as that
# group's, and that a preserved group copies back from the cache.
_PRESERVED_SENSOR_STATE_FIELD_GROUPS: Final[dict[Group, frozenset[str]]] = {
    "door": _DOOR_STATE_FIELDS | _DOOR_TIMESTAMP_FIELDS,
    "motion": _MOTION_STATE_FIELDS | _MOTION_TIMESTAMP_FIELDS,
    "tamper": _TAMPER_STATE_FIELDS | _TAMPER_TIMESTAMP_FIELDS,
    "leak": _LEAK_STATE_FIELDS | _LEAK_TIMESTAMP_FIELDS,
}

# Group -> the state spellings a latched group compares to decide that REST
# has caught up (the reported state matters there, not its timestamp).
_PRESERVED_SENSOR_STATE_GROUP_STATE_FIELDS: Final[dict[Group, frozenset[str]]] = {
    "door": _DOOR_STATE_FIELDS,
    "motion": _MOTION_STATE_FIELDS,
    "tamper": _TAMPER_STATE_FIELDS,
    # Sensors like the USL-Environmental report no `isLeakDetected` flag;
    # their leak state is the `*LeakDetectedAt` timestamp (null when dry).
    "leak": _LEAK_STATE_FIELDS | _LEAK_TIMESTAMP_FIELDS,
}

# Device frame model key -> the collection it updates, for the families a
# frame merges into shallowly.
_SHALLOW_FRAME_COLLECTIONS: Final[dict[str, str]] = {
    DEVICE_TYPE_LIGHT: "lights",
    DEVICE_TYPE_VIEWER: "viewers",
    DEVICE_TYPE_CHIME: "chimes",
    DEVICE_TYPE_DOORLOCK: "doorlocks",
    DEVICE_TYPE_VIEWPORT: "viewports",
}

# Every model key a device frame is applied for; any other is ignored.
_KNOWN_FRAME_MODEL_KEYS: Final[frozenset[str]] = frozenset(
    {
        DEVICE_TYPE_CAMERA,
        DEVICE_TYPE_SENSOR,
        DEVICE_TYPE_NVR,
        DEVICE_TYPE_SIREN,
        MODEL_KEY_FOB,
        MODEL_KEY_LINKSTATION,
        *_SHALLOW_FRAME_COLLECTIONS,
    }
)

# Latch kind -> (collection, field prefix, list field emptied on expiry).
_LATCHES: Final[dict[LatchKind, tuple[Family, str, str | None]]] = {
    "camera_motion": ("cameras", "lastMotion", "lastSmartDetectTypes"),
    "light_motion": ("lights", "lastMotion", None),
    "camera_ring": ("cameras", "lastRing", None),
}

# Family -> latch kind -> the start field a REST rebuild of it drops.
_REBUILT_LATCH_STARTS: Final[dict[Family, dict[LatchKind, str]]] = {
    "cameras": {
        "camera_motion": "lastMotionStart",
        "camera_ring": "lastRingStart",
    },
    "lights": {"light_motion": "lastMotionStart"},
}

# The keys an event may carry its device id under. The library's event model
# has no generic "device" field (only camera/cameraId or sensor/sensorId);
# "device" is checked first and the rest are tolerated because the events
# stream has never been seen live.
_EVENT_DEVICE_KEYS: Final = (
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

# --- helpers -------------------------------------------------------------------


def pick_field(containers: list[dict[str, Any]], *keys: str) -> Any:
    """
    Return the first truthy value for any of `keys`, in container order.

    A frame's identifying fields (modelKey/id for devices, type/id for
    events) may live at the top level, under "item", under "payload", or split
    across an "action" header. Truthy on purpose (ADR-009 §1): an empty string
    id is no id.
    """
    for container in containers:
        for key in keys:
            value = container.get(key)
            if value:
                return value
    return None


def deep_merge(
    old: Mapping[str, Any], new: Mapping[str, Any], *, into_lists: bool = False
) -> dict[str, Any]:
    """
    Merge ``new`` over ``old``, recursing where both sides hold a dict.

    Returns a new dict at every level it touches and never changes either
    argument. Only dict-over-dict recurses: any other value, ``None``
    included, replaces the old one, so a frame can still clear a nested
    object.

    Used for the frames of the nested-object families (fobs, link stations,
    alarm hubs, NVRs, sirens): a partial frame such as
    ``{"alarmHub": {"deviceTamperStatus": "tampered"}}`` would otherwise wipe
    the hub's armed and battery state until the next poll, and an NVR frame
    of ``{"armMode": {"status": "armed"}}`` its armProfileId.

    ``into_lists`` is for command overlays (ADR-010): a dict over a list is
    then merged into every dict entry, in a new list, keeping each entry's
    other fields. A chime keeps its volume, repeat count and ringtone in one
    ``ringSettings`` entry per paired doorbell, and the console sets them all.
    """
    merged = dict(old)
    for key, value in new.items():
        existing = merged.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            merged[key] = deep_merge(existing, value, into_lists=into_lists)
        elif into_lists and isinstance(value, dict) and isinstance(existing, list):
            merged[key] = [
                deep_merge(entry, value, into_lists=True)
                if isinstance(entry, dict)
                else entry
                for entry in existing
            ]
        else:
            merged[key] = value
    return merged


def normalize_camera(camera: Mapping[str, Any]) -> dict[str, Any]:
    """Return a camera record with its alias and legacy field shapes resolved."""
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
                    feature_flag_types if isinstance(feature_flag_types, list) else []
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

    if not isinstance(normalized.get("lastSmartDetectTypes"), list):
        normalized["lastSmartDetectTypes"] = []
    normalized.setdefault("lastMotion", 0)
    normalized.setdefault("lastRing", 0)
    return normalized


def _number(value: Any) -> float | None:
    """Return ``value`` as a float if it is a real number (not a bool), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


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
    Normalize a timestamp to epoch seconds, or None when it is not comparable.

    Used on both sides of the door timestamp comparison. The REST side is an
    epoch-millisecond int once the library's model has validated it; the
    cached side can be anything a frame carried, since frames bypass the
    model. Numbers are seconds or milliseconds (split at
    `_MILLISECOND_EPOCH_THRESHOLD`), datetimes convert directly (naive ones
    are taken as UTC), and strings parse as ISO 8601. ``bool`` is excluded on
    purpose so a stray ``True`` never reads as an epoch of 1.
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


def _with[K, V](mapping: Mapping[K, V], key: K, value: V) -> dict[K, V]:
    """Return a copy of ``mapping`` with ``key`` set to ``value``."""
    return {**mapping, key: value}


def _without[K, V](mapping: Mapping[K, V], key: K) -> dict[K, V]:
    """Return a copy of ``mapping`` without ``key``."""
    return {k: v for k, v in mapping.items() if k != key}


# --- state ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Instant:
    """Both clocks, read once per input by the coordinator."""

    # Request stamps, WebSocket recency, the tamper race and the siren guard.
    monotonic: float
    # Aware UTC: latch start and expiry, and the siren stop fallback.
    wall: datetime


@dataclass(frozen=True, slots=True)
class SensorTracking:
    """What is remembered per sensor to keep a newer door state over REST."""

    # Group -> monotonic time of the last frame that carried its fields.
    ws_seen: Mapping[Group, float] = field(default_factory=dict)
    # Polls in a row the door timestamp comparison kept the cached state.
    ts_preserve_polls: int = 0
    # Group -> polls in a row WebSocket recency kept the cached state.
    ws_preserve_polls: Mapping[Group, int] = field(default_factory=dict)
    # Groups whose recency cap tripped: REST wins until it agrees.
    ws_latched: frozenset[Group] = frozenset()
    # Whether a preservation cap already logged its one WARNING.
    cap_warned: bool = False


@dataclass(frozen=True, slots=True)
class TamperRecord:
    """An alarm hub's latest alarmHubDeviceTamper event."""

    event_id: Any
    status: str | None
    # Monotonic time the status arrived, raced against a poll's request.
    received: float
    last_user: str | None
    last_at: Any


@dataclass(frozen=True, slots=True)
class SirenStop:
    """What is remembered of a manual siren stop."""

    # ``activatedAt`` of the run cached when it was stopped (console clock).
    activated_at: float | None
    # Millisecond epoch of the stop on Home Assistant's clock.
    stopped_at_ms: float
    # Monotonic time of the stop, which bounds how long the guard lives.
    monotonic: float


@dataclass(frozen=True, slots=True)
class FamilyTracking:
    """One device family's poll counters."""

    # Empty or 404 answers in a row while devices were cached.
    empty_fetches: int = 0
    # Transient errors in a row (cameras, lights and sensors).
    fetch_errors: int = 0
    # Ids seen at the last clean-up, plus those still in their grace window.
    previous_ids: frozenset[str] = frozenset()
    # Device id -> polls in a row it has been missing.
    missing_polls: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReconcilerState:
    """Everything the reconciler remembers between inputs."""

    # Latch kind -> device id -> wall time its "start" was seen.
    latches: Mapping[LatchKind, Mapping[str, datetime]]
    sensors: Mapping[str, SensorTracking]
    tamper: Mapping[str, TamperRecord]
    siren_stops: Mapping[str, SirenStop]
    # Frames applied per family; a REST answer requested before one is older.
    ws_generation: Mapping[GuardedFamily, int]
    families: Mapping[Family, FamilyTracking]
    # Frame model keys already logged as unknown (bounded by the console's
    # own model keys), so each is logged once.
    unknown_model_keys: frozenset[str] = frozenset()
    # Event type -> event id -> wall time of the first sweep that saw it.
    # Stamped by the sweep, not per frame, so a frame copies no map of it.
    event_seen: Mapping[str, Mapping[str, datetime]] = field(default_factory=dict)
    # Wall time of the last event store sweep (None before the first).
    events_swept_at: datetime | None = None


def initial_state() -> ReconcilerState:
    """Return the state of a coordinator that has seen nothing yet."""
    return ReconcilerState(
        latches={"camera_motion": {}, "light_motion": {}, "camera_ring": {}},
        sensors={},
        tamper={},
        siren_stops={},
        ws_generation={"nvrs": 0, "sirens": 0},
        families={family: FamilyTracking() for family in FAMILIES},
    )


# --- inputs --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeviceFrame:
    """A device update from the "devices" stream."""

    model_key: str
    device_id: str
    fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EventFrame:
    """An event from the "events" stream."""

    event_type: str
    event_id: str
    fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RequestStamp:
    """When a REST request went out, taken before its await."""

    family: Family
    at: float
    # The family's WebSocket generation then (None for unguarded families).
    ws_generation: int | None


@dataclass(frozen=True, slots=True)
class Fetched:
    """The family's list endpoint answered with these records (by id)."""

    items: Mapping[str, dict[str, Any]]
    # False when the library dropped a record it could not parse.
    complete: bool = True


@dataclass(frozen=True, slots=True)
class NotFound:
    """The endpoint answered 404."""


@dataclass(frozen=True, slots=True)
class TransientError:
    """A connection, timeout or response error (cameras, lights, sensors)."""

    error: Exception


@dataclass(frozen=True, slots=True)
class NoAnswer:
    """No answer to apply: skipped, failed, or unsupported on this console."""


type RestOutcome = Fetched | NotFound | TransientError | NoAnswer


@dataclass(frozen=True, slots=True)
class RestBatch:
    """The outcome of one REST request."""

    request: RequestStamp
    outcome: RestOutcome


@dataclass(frozen=True, slots=True)
class PollStarted:
    """The main poll is about to fetch (expires stale latches)."""


@dataclass(frozen=True, slots=True)
class StreamReconnected:
    """A WebSocket stream (re)connected (expires stale latches)."""

    stream: Literal["devices", "events"]


@dataclass(frozen=True, slots=True)
class PollCompleted:
    """The main poll finished (counts missing devices, removes stale ones)."""


@dataclass(frozen=True, slots=True)
class SirenStopped:
    """A siren was stopped from Home Assistant."""

    siren_id: str


@dataclass(frozen=True, slots=True)
class CommandOverlay:
    """A successful write's fields, laid over the record (ADR-010)."""

    collection: str
    device_id: str
    changes: Mapping[str, Any]


type ReconcilerInput = (
    DeviceFrame
    | EventFrame
    | RestBatch
    | PollStarted
    | StreamReconnected
    | PollCompleted
    | SirenStopped
    | CommandOverlay
)

# --- effects -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PublishUpdate:
    """Notify listeners; ``mark_success`` also resets the poll timer."""

    mark_success: bool


@dataclass(frozen=True, slots=True)
class RemoveDevice:
    """Detach a stale device, trying each identifier in turn."""

    identifiers: tuple[str, str]


@dataclass(frozen=True, slots=True)
class Escalate:
    """A transient error outlasted its window: the caller re-raises it."""


@dataclass(frozen=True, slots=True)
class Log:
    """A log line for the coordinator's logger."""

    level: int
    msg: str
    args: tuple[Any, ...] = ()


type Effect = PublishUpdate | RemoveDevice | Escalate | Log


@dataclass(frozen=True, slots=True)
class Step:
    """The result of one input: the new state and cache, and what to do."""

    state: ReconcilerState
    cache: dict[str, Any]
    effects: tuple[Effect, ...]


def begin_request(state: ReconcilerState, family: Family, now: Instant) -> RequestStamp:
    """Stamp a REST request; call it before the request's await."""
    return RequestStamp(family, now.monotonic, _generation(state, family))


def _generation(state: ReconcilerState, family: Family) -> int | None:
    """Return the family's WebSocket generation, or None if it has none."""
    if family in ("nvrs", "sirens"):
        return state.ws_generation.get(family)
    return None


# --- one step ------------------------------------------------------------------


class _Run:
    """
    The working copy of one `reconcile` call.

    Collects the new state, a lazily copied cache and the effects. It lives
    for one call only, so `reconcile` stays a pure function.
    """

    def __init__(
        self, state: ReconcilerState, cache: dict[str, Any], now: Instant
    ) -> None:
        self.state = state
        self.now = now
        self.effects: list[Effect] = []
        self._base = cache
        self._root: dict[str, Any] | None = None
        # Collections this run has already copied and may write into.
        self._owned: set[str] = set()

    # cache

    @property
    def data(self) -> dict[str, Any]:
        """Return the cache as this run has left it so far."""
        return self._root if self._root is not None else self._base

    def records(self, collection: str) -> dict[str, Any]:
        """Return a collection of the cache (empty if it is missing)."""
        records = self.data.get(collection)
        return records if isinstance(records, dict) else {}

    def _own_root(self) -> dict[str, Any]:
        if self._root is None:
            self._root = dict(self._base)
        return self._root

    def replace(self, collection: str, records: Mapping[str, Any]) -> None:
        """Replace a whole collection (with a copy of ``records``)."""
        if self.data.get(collection) == records:
            return
        self._own_root()[collection] = dict(records)
        self._owned.add(collection)

    def put(self, collection: str, record_id: str, record: Any) -> None:
        """Set one record, copying the root and the collection on first write."""
        current = self.records(collection)
        if record_id in current and current[record_id] == record:
            return
        self.put_changed(collection, record_id, record)

    def put_changed(self, collection: str, record_id: str, record: Any) -> None:
        """Set one record the caller knows differs from the cached one."""
        root = self._own_root()
        if collection not in self._owned:
            root[collection] = dict(self.records(collection))
            self._owned.add(collection)
        root[collection][record_id] = record

    # state

    def family(self, family: Family) -> FamilyTracking:
        return self.state.families.get(family, FamilyTracking())

    def set_family(self, family: Family, tracking: FamilyTracking) -> None:
        self.state = replace(
            self.state, families=_with(self.state.families, family, tracking)
        )

    def sensor(self, sensor_id: str) -> SensorTracking:
        return self.state.sensors.get(sensor_id, SensorTracking())

    def set_sensor(self, sensor_id: str, tracking: SensorTracking) -> None:
        self.state = replace(
            self.state, sensors=_with(self.state.sensors, sensor_id, tracking)
        )

    def latch(self, kind: LatchKind) -> Mapping[str, datetime]:
        return self.state.latches.get(kind, {})

    def set_latch(self, kind: LatchKind, latch: Mapping[str, datetime]) -> None:
        self.state = replace(self.state, latches=_with(self.state.latches, kind, latch))

    # effects

    def log(self, level: int, msg: str, *args: Any) -> None:
        self.effects.append(Log(level, msg, args))

    def step(self) -> Step:
        return Step(self.state, self.data, tuple(self.effects))


def reconcile(
    state: ReconcilerState,
    cache: dict[str, Any],
    event: ReconcilerInput,
    now: Instant,
) -> Step:
    """
    Apply one input to the state and the cache.

    Never changes ``state``, ``cache`` or ``event``. The returned cache is a
    new root when anything changed, sharing every untouched collection and
    record, and ``cache`` itself when nothing did.
    """
    run = _Run(state, cache, now)
    match event:
        case DeviceFrame():
            # Every device frame re-arms the poll timer, so under steady
            # frames the poll and its sweeps can wait indefinitely: the frame
            # sweeps too. A frame that publishes shows the sweeps with it.
            swept = _expire_stale_latches(run)
            swept = _sweep_event_store(run) or swept
            if not _apply_device_frame(run, event) and swept:
                run.effects.append(PublishUpdate(mark_success=False))
        case EventFrame():
            _apply_event_frame(run, event)
        case RestBatch():
            _apply_rest_batch(run, event)
        case PollStarted():
            # The poll publishes once it has fetched, which shows the sweeps.
            # Sweeping the event store here too expires old events while no
            # event frames arrive.
            _expire_stale_latches(run)
            _sweep_event_store(run)
        case StreamReconnected():
            if _expire_stale_latches(run):
                run.effects.append(PublishUpdate(mark_success=False))
        case PollCompleted():
            _evict_missing_devices(run)
        case SirenStopped():
            _apply_siren_stop(run, event.siren_id)
        case CommandOverlay():
            _apply_overlay(run, event)
        case _:  # pragma: no cover - exhaustive
            assert_never(event)
    return run.step()


# --- device frames -------------------------------------------------------------


def _apply_device_frame(run: _Run, frame: DeviceFrame) -> bool:
    """
    Merge a device frame into its collection; return True if it published.

    A frame with a model key nothing tracks changes nothing, so it publishes
    nothing either: marking it successful would re-arm the poll timer, and a
    console that keeps sending such frames would hold the poll off for good.
    Its model key is logged once.
    """
    model_key, device_id, fields = frame.model_key, frame.device_id, frame.fields
    if model_key not in _KNOWN_FRAME_MODEL_KEYS:
        if model_key not in run.state.unknown_model_keys:
            run.state = replace(
                run.state,
                unknown_model_keys=run.state.unknown_model_keys | {model_key},
            )
            run.log(
                logging.DEBUG,
                "Protect coordinator: ignoring WebSocket device updates for "
                "unknown model key %s",
                model_key,
            )
        return False
    run.log(
        logging.DEBUG,
        "Protect coordinator: WebSocket device update for %s: %s",
        model_key,
        device_id,
    )
    if model_key == DEVICE_TYPE_CAMERA:
        existing = run.records("cameras").get(device_id, {})
        run.put("cameras", device_id, normalize_camera({**existing, **fields}))
    elif model_key == DEVICE_TYPE_SENSOR:
        _stamp_sensor_groups(run, device_id, fields)
        existing = run.records("sensors").get(device_id, {})
        run.put("sensors", device_id, {**existing, **fields})
    elif model_key in _SHALLOW_FRAME_COLLECTIONS:
        collection = _SHALLOW_FRAME_COLLECTIONS[model_key]
        existing = run.records(collection).get(device_id, {})
        run.put(collection, device_id, {**existing, **fields})
    elif model_key in (DEVICE_TYPE_NVR, DEVICE_TYPE_SIREN):
        # Deep merge: a partial frame carries only the changed part of nested
        # objects (armMode, sirenStatus), which must not drop their siblings.
        guarded: GuardedFamily = "nvrs" if model_key == DEVICE_TYPE_NVR else "sirens"
        run.state = replace(
            run.state,
            ws_generation=_with(
                run.state.ws_generation,
                guarded,
                run.state.ws_generation.get(guarded, 0) + 1,
            ),
        )
        existing = run.records(guarded).get(device_id, {})
        run.put(guarded, device_id, deep_merge(existing, fields))
        if guarded == "sirens":
            _apply_siren_stop_guards(run)
    elif model_key == MODEL_KEY_FOB:
        existing = run.records("fobs").get(device_id, {})
        run.put("fobs", device_id, deep_merge(existing, fields))
    elif model_key == MODEL_KEY_LINKSTATION:
        target = _linkstation_collection(run, device_id, fields)
        if target is None:
            run.log(
                logging.DEBUG,
                "Protect coordinator: dropping WebSocket linkstation update "
                "for unknown device %s; the next poll will pick it up",
                device_id,
            )
            return False
        existing = run.records(target).get(device_id, {})
        run.put(target, device_id, deep_merge(existing, fields))
    # Every applied frame marks the update successful and re-arms the poll
    # timer, so entities stay available while frames flow and the fallback
    # poll waits for a quiet stream.
    run.effects.append(PublishUpdate(mark_success=True))
    return True


def _stamp_sensor_groups(run: _Run, sensor_id: str, fields: Mapping[str, Any]) -> None:
    """Stamp WebSocket recency for each state group the frame carries."""
    groups: list[Group] = [
        group
        for group, group_fields in _PRESERVED_SENSOR_STATE_FIELD_GROUPS.items()
        if not group_fields.isdisjoint(fields)
    ]
    if not groups:
        # Telemetry (temperature, battery, ...) never holds back REST.
        return
    tracking = run.sensor(sensor_id)
    ws_seen = dict(tracking.ws_seen)
    for group in groups:
        ws_seen[group] = run.now.monotonic
    run.set_sensor(sensor_id, replace(tracking, ws_seen=ws_seen))


def _linkstation_collection(
    run: _Run, device_id: str, fields: Mapping[str, Any]
) -> str | None:
    """
    Pick the collection a "linkstation" frame belongs to.

    Link stations and alarm hubs share the "linkstation" modelKey, so the
    frame goes where the id already is, then by its own isAlarmHub. A partial
    frame for an unknown id without isAlarmHub has no safe target: None.
    """
    if device_id in run.records("alarm_hubs"):
        return "alarm_hubs"
    if device_id in run.records("link_stations"):
        return "link_stations"
    is_alarm_hub = fields.get("isAlarmHub")
    if isinstance(is_alarm_hub, bool):
        return "alarm_hubs" if is_alarm_hub else "link_stations"
    return None


# --- event frames --------------------------------------------------------------


def _apply_event_frame(run: _Run, frame: EventFrame) -> None:
    """Store an event and apply it to its device."""
    event_type, event_id, fields = frame.event_type, frame.event_id, frame.fields
    if not event_id:
        return
    run.log(
        logging.DEBUG,
        "Protect coordinator: WebSocket event update for %s: %s",
        event_type,
        event_id,
    )
    # Sweep first, so the event this frame stores is never the one dropped.
    _sweep_event_store(run)
    by_id = run.records("events").get(event_type, {})
    # A new event or a changed one copies only its type's map, which the
    # sweep bounds, and leaves every other type's map shared. An unstored
    # event that started more than the retention ago is a late frame the
    # timeline can no longer show, most likely of an event the sweep already
    # dropped: storing it would keep it another full retention. It still
    # updates its device below.
    if (
        by_id[event_id] != fields
        if event_id in by_id
        else not _started_before_retention(fields, run.now.wall)
    ):
        run.put_changed("events", event_type, _with(by_id, event_id, fields))
    device_id = pick_field([fields], *_EVENT_DEVICE_KEYS)
    if device_id:
        _apply_event_to_device(run, event_type, fields, device_id)
    run.effects.append(PublishUpdate(mark_success=False))


def _started_before_retention(fields: Mapping[str, Any], now: datetime) -> bool:
    """
    Return True if an event's own start is more than `EVENT_RETENTION` ago.

    Reads the start as the timeline does (``start``, then ``time``, then
    ``timestamp``). An event without a readable start is never judged old.
    """
    started = _normalize_epoch_seconds(
        fields.get("start") or fields.get("time") or fields.get("timestamp")
    )
    return started is not None and started < (now - EVENT_RETENTION).timestamp()


def _sweep_event_store(run: _Run) -> bool:
    """
    Drop the events first seen more than `EVENT_RETENTION` ago.

    Runs at most every `EVENT_SWEEP_INTERVAL` (and again if the wall clock
    went back), from event frames, device frames and the poll's start. An
    event is stamped by the first sweep that sees it, so a frame never copies
    the stamps; its age then runs up to one interval late, which keeps it
    longer, never shorter. A repeated frame of a stored event keeps its
    stamp: the timeline places an event by its start. A type whose map loses
    nothing keeps its map, and a type that loses every event is removed. The
    cache changes only when an event is dropped; returns True if one was, so
    the caller knows listeners have something new to show.
    """
    now = run.now.wall
    swept_at = run.state.events_swept_at
    if swept_at is not None and swept_at <= now < swept_at + EVENT_SWEEP_INTERVAL:
        return False
    cutoff = now - EVENT_RETENTION
    events = run.records("events")
    kept: dict[str, Any] = {}
    seen: dict[str, dict[str, datetime]] = {}
    for event_type, by_id in events.items():
        type_seen = run.state.event_seen.get(event_type, {})
        stamps = {
            event_id: stamp
            for event_id in by_id
            if (stamp := type_seen.get(event_id, now)) >= cutoff
        }
        if not stamps:
            continue
        seen[event_type] = stamps
        kept[event_type] = (
            by_id
            if len(stamps) == len(by_id)
            else {event_id: by_id[event_id] for event_id in stamps}
        )
    run.state = replace(run.state, event_seen=seen, events_swept_at=now)
    dropped = sum(map(len, seen.values())) != sum(map(len, events.values()))
    if dropped:
        run.replace("events", kept)
    return dropped


def _apply_event_to_device(
    run: _Run, event_type: str, fields: Mapping[str, Any], device_id: Any
) -> None:
    """Write an event's start and end onto its camera, light or alarm hub."""
    if event_type == "motion" and device_id in run.records("cameras"):
        _apply_start_end(run, "camera_motion", device_id, fields)
        run.log(
            logging.INFO,
            "Protect coordinator: Motion event for camera %s: start=%s, end=%s",
            device_id,
            fields.get("start"),
            fields.get("end"),
        )
    elif event_type == "motion" and device_id in run.records("lights"):
        _apply_start_end(run, "light_motion", device_id, fields)
    elif (
        # The library's EventType says "smartDetect"; the integration first
        # matched "smartDetectZone". The stream has never been seen live, so
        # both are accepted.
        event_type in ("smartDetectZone", "smartDetect")
        and device_id in run.records("cameras")
    ):
        smart_detect_types = fields.get("smartDetectTypes", [])
        if not isinstance(smart_detect_types, list):
            smart_detect_types = []
        _apply_start_end(
            run,
            "camera_motion",
            device_id,
            fields,
            extra={"lastSmartDetectTypes": smart_detect_types},
        )
        run.log(
            logging.INFO,
            "Protect coordinator: Smart detection for camera %s: %s (start=%s, end=%s)",
            device_id,
            smart_detect_types,
            fields.get("start", 0),
            fields.get("end"),
        )
    elif event_type == "ring" and device_id in run.records("cameras"):
        _apply_start_end(run, "camera_ring", device_id, fields)
        run.log(
            logging.INFO,
            "Protect coordinator: Doorbell ring for camera %s: start=%s, end=%s",
            device_id,
            fields.get("start"),
            fields.get("end"),
        )
    elif event_type == EVENT_TYPE_ALARM_HUB_DEVICE_TAMPER and device_id in run.records(
        "alarm_hubs"
    ):
        _apply_tamper_event(run, device_id, fields)


def _apply_start_end(
    run: _Run,
    kind: LatchKind,
    device_id: str,
    fields: Mapping[str, Any],
    *,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """
    Write an event's start and end, and track the latch for the auto-off.

    The latch uses Home Assistant's wall clock, not the event's own times,
    so the timeout holds whatever format Protect sends. A second "start"
    keeps the first start time, and an "end" clears the latch.
    """
    collection, prefix, _cleared = _LATCHES[kind]
    end = fields.get("end")
    record = {
        **run.records(collection)[device_id],
        f"{prefix}Start": fields.get("start"),
        f"{prefix}End": end,
        **(extra or {}),
    }
    run.put(collection, device_id, record)
    latch = run.latch(kind)
    if end is None:
        if device_id not in latch:
            run.set_latch(kind, _with(latch, device_id, run.now.wall))
    elif device_id in latch:
        run.set_latch(kind, _without(latch, device_id))


def _apply_tamper_event(run: _Run, hub_id: str, fields: Mapping[str, Any]) -> None:
    """
    Apply an alarmHubDeviceTamper event to its hub.

    The status goes into ``alarmHub.deviceTamperStatus`` at once. ``userName``
    is kept as ``_lastTamperUser`` without assuming whose name it is, and
    ``start`` (epoch ms) as ``_lastTamperAt``. A later frame of the same event
    fills its gaps from the earlier one rather than wiping them; a different
    event replaces the record, so a restore naming nobody does not show the
    previous event's user.
    """
    metadata = fields.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    status = _event_metadata_text(metadata.get("status"))
    record = TamperRecord(
        event_id=fields.get("id"),
        status=status,
        received=run.now.monotonic,
        last_user=_event_metadata_text(metadata.get("userName")),
        last_at=fields.get("start"),
    )
    previous = run.state.tamper.get(hub_id)
    if previous is not None and previous.event_id == record.event_id:
        record = TamperRecord(
            event_id=record.event_id,
            status=record.status if record.status is not None else previous.status,
            received=previous.received if status is None else record.received,
            last_user=(
                record.last_user if record.last_user is not None else previous.last_user
            ),
            last_at=record.last_at if record.last_at is not None else previous.last_at,
        )
    run.state = replace(run.state, tamper=_with(run.state.tamper, hub_id, record))
    hub = run.records("alarm_hubs")[hub_id]
    run.put("alarm_hubs", hub_id, _with_tamper_record(hub, record, requested_at=None))
    run.log(
        logging.INFO,
        "Protect coordinator: Tamper event for alarm hub %s: status=%s",
        hub_id,
        status,
    )


def _with_tamper_record(
    hub: Mapping[str, Any], record: TamperRecord, *, requested_at: float | None
) -> dict[str, Any]:
    """
    Return ``hub`` with its latest tamper event laid on top.

    The event's user and time always apply. Its status applies when the event
    itself is being applied (``requested_at`` None), when the REST record has
    no ``deviceTamperStatus`` of its own (Protect 7.2.x sends the event but
    not the field, and on 7.3.70 it is optional), or when the event arrived
    after the poll's request went out: that snapshot predates the tamper and
    would read as a false all-clear. Otherwise REST is authoritative. Both
    times are Home Assistant's monotonic clock, so console clock skew cannot
    matter.
    """
    overlay: dict[str, Any] = {
        "_lastTamperUser": record.last_user,
        "_lastTamperAt": record.last_at,
    }
    if record.status is not None:
        alarm_hub = hub.get("alarmHub")
        reported = (
            alarm_hub.get("deviceTamperStatus") if isinstance(alarm_hub, dict) else None
        )
        if (
            requested_at is None
            or not isinstance(reported, str)
            or record.received >= requested_at
        ):
            overlay["alarmHub"] = {"deviceTamperStatus": record.status}
    return deep_merge(hub, overlay)


# --- REST answers --------------------------------------------------------------


def _apply_rest_batch(run: _Run, batch: RestBatch) -> None:
    """Apply one REST answer to its family."""
    request, outcome = batch.request, batch.outcome
    family = request.family
    match outcome:
        case TransientError():
            if not _absorb_transient_error(run, family, outcome.error):
                run.effects.append(Escalate())
                return
        case NotFound():
            # The endpoint answered, so the session is alive: end any streak.
            run.set_family(family, replace(run.family(family), fetch_errors=0))
            _update_collection(run, family, {}, is_404=True)
        case Fetched():
            _apply_fetched(run, request, outcome)
        case NoAnswer():
            pass
        case _:  # pragma: no cover - exhaustive
            assert_never(outcome)

    if family in _REBUILT_LATCH_STARTS:
        _drop_rebuilt_latches(run, family)
    elif family == "alarm_hubs":
        _overlay_tamper_records(run, request.at)
    elif family == "sirens":
        _apply_siren_stop_guards(run)


def _apply_fetched(run: _Run, request: RequestStamp, outcome: Fetched) -> None:
    """Apply a list endpoint's answer, unless a frame overtook the request."""
    family = request.family
    if _generation(run.state, family) != request.ws_generation:
        # A frame for the family landed while the request was out, so it is
        # newer than the answer, and applying the answer would undo it.
        if family == "nvrs":
            run.log(
                logging.DEBUG,
                "Protect coordinator: a WebSocket frame for the NVR arrived "
                "while it was being fetched; keeping it instead of the "
                "older reading",
            )
        else:
            run.log(
                logging.DEBUG,
                "Protect coordinator: a WebSocket frame for %s arrived while "
                "it was being fetched; keeping it instead of the older reading",
                family,
            )
        return
    run.set_family(family, replace(run.family(family), fetch_errors=0))
    items: Mapping[str, dict[str, Any]] = outcome.items
    if family == "sensors":
        items = _preserve_sensor_states(run, request, items)
    _update_collection(run, family, items, is_partial=not outcome.complete)
    if family == "nvrs":
        if items:
            run.log(
                logging.DEBUG,
                "Protect coordinator: Successfully fetched NVR: %s",
                next(iter(items)),
            )
    elif family in ("fobs", "link_stations", "alarm_hubs", "sirens"):
        run.log(
            logging.DEBUG,
            "Protect coordinator: Successfully fetched %d %s",
            len(items),
            family,
        )


def _absorb_transient_error(run: _Run, family: Family, error: Exception) -> bool:
    """
    Return True if a transient fetch error is absorbed this poll.

    Cameras and lights are fetched first, so an escaping error fails the
    whole poll and takes every Protect entity unavailable over one blipped
    endpoint. The first `MAX_CONSECUTIVE_EMPTY_FETCHES` errors keep the cache;
    past that the error propagates, so a real outage still shows.
    """
    tracking = run.family(family)
    count = tracking.fetch_errors + 1
    if count > MAX_CONSECUTIVE_EMPTY_FETCHES:
        run.set_family(family, replace(tracking, fetch_errors=count))
        run.log(
            logging.DEBUG,
            "Protect coordinator: %s fetch failed for %d consecutive polls "
            "(%s); failing the update",
            family,
            count,
            error,
        )
        return False
    run.log(
        logging.DEBUG,
        "Protect coordinator: Error fetching %s (poll %d/%d): %s; "
        "preserving cached devices",
        family,
        count,
        MAX_CONSECUTIVE_EMPTY_FETCHES,
        error,
    )
    # A failed fetch is no evidence that a device was removed, so the empty
    # answer counter stays at zero.
    run.set_family(family, replace(tracking, fetch_errors=count, empty_fetches=0))
    return True


def _update_collection(
    run: _Run,
    family: Family,
    items: Mapping[str, dict[str, Any]],
    *,
    is_404: bool = False,
    is_partial: bool = False,
) -> None:
    """
    Replace a family's collection, keeping its cache over short outages.

    Up to `MAX_CONSECUTIVE_EMPTY_FETCHES` empty or 404 answers in a row keep
    the cached devices; past that the collection is cleared so removed
    devices leave the registry. A partial answer (the library dropped a
    record it could not parse) is merged over the cache instead of replacing
    it, because the missing device is still adopted.
    """
    existing = run.records(family)
    tracking = run.family(family)

    if items:
        run.set_family(family, replace(tracking, empty_fetches=0))
        if is_partial:
            run.log(
                logging.DEBUG,
                "Protect coordinator: %s response was incomplete; merging "
                "%d fetched over %d cached devices",
                family,
                len(items),
                len(existing),
            )
            run.replace(family, {**existing, **items})
            return
        # Known gap: a short answer the library did not flag (the controller
        # omitted a device) looks authoritative here; the missing-poll grace
        # window in `_evict_missing_devices` covers that.
        run.replace(family, items)
        return

    # A partial answer with nothing left is a parse failure across the whole
    # family, not an empty controller: keep the cache and do not count it, or
    # a schema change would purge every device a few polls later.
    if is_partial and existing:
        run.log(
            logging.WARNING,
            "Protect coordinator: every %s in the response failed to parse; "
            "preserving %d cached devices",
            family,
            len(existing),
        )
        run.set_family(family, replace(tracking, empty_fetches=0))
        return

    if not existing:
        run.replace(family, {})
        run.set_family(family, replace(tracking, empty_fetches=0))
        if is_404:
            run.log(
                logging.DEBUG,
                "Protect coordinator: %s endpoint returned 404; no devices configured",
                family,
            )
        return

    count = tracking.empty_fetches + 1
    status_desc = "404" if is_404 else "empty response"
    if count <= MAX_CONSECUTIVE_EMPTY_FETCHES:
        run.set_family(family, replace(tracking, empty_fetches=count))
        run.log(
            logging.DEBUG,
            "Protect coordinator: %s fetch returned %s (poll %d/%d); "
            "preserving %d cached devices",
            family,
            status_desc,
            count,
            MAX_CONSECUTIVE_EMPTY_FETCHES,
            len(existing),
        )
        return
    run.log(
        logging.WARNING,
        "Protect coordinator: %s fetch returned %s for %d consecutive polls; "
        "clearing cached devices",
        family,
        status_desc,
        count,
    )
    run.replace(family, {})
    run.set_family(family, replace(tracking, empty_fetches=0))


def _drop_rebuilt_latches(run: _Run, family: Family) -> None:
    """
    Drop camera and light latches whose start field a REST rebuild removed.

    REST camera and light records never carry ``lastMotionStart`` or
    ``lastRingStart`` (only an event writes them), so after a rebuild a
    still-armed latch would point at a device with no start, and five minutes
    later the sweep would write a false end and log a false "missed 'end'
    event?". A device that vanished outright is left to the sweep.
    """
    records = run.records(family)
    for kind, start_key in _REBUILT_LATCH_STARTS[family].items():
        latch = run.latch(kind)
        kept = {
            device_id: started
            for device_id, started in latch.items()
            if not (
                isinstance(record := records.get(device_id), dict)
                and start_key not in record
            )
        }
        if len(kept) != len(latch):
            run.set_latch(kind, kept)


def _overlay_tamper_records(run: _Run, requested_at: float) -> None:
    """Lay every hub's latest tamper event back over the polled hubs."""
    for hub_id, record in run.state.tamper.items():
        hub = run.records("alarm_hubs").get(hub_id)
        if isinstance(hub, dict):
            run.put(
                "alarm_hubs",
                hub_id,
                _with_tamper_record(hub, record, requested_at=requested_at),
            )


# --- sensor preservation -------------------------------------------------------


def _preserve_sensor_states(
    run: _Run, request: RequestStamp, items: Mapping[str, dict[str, Any]]
) -> Mapping[str, dict[str, Any]]:
    """
    Keep cached door/motion/tamper/leak states that are newer than REST.

    Returns ``items`` with each preserved group's fields copied from the
    cache onto a copy of the REST record. Each group is decided on its own,
    so a frame carrying one group's fields can never hold back another's.
    """
    cached_sensors = run.records("sensors")
    result: dict[str, dict[str, Any]] | None = None
    for sensor_id, rest_sensor in items.items():
        cached_sensor = cached_sensors.get(sensor_id)
        if not isinstance(cached_sensor, dict):
            continue
        groups = _preserved_groups(
            run, sensor_id, cached_sensor, rest_sensor, request.at
        )
        if not groups:
            continue
        merged = dict(rest_sensor)
        for group in groups:
            for name in _PRESERVED_SENSOR_STATE_FIELD_GROUPS[group]:
                if name in cached_sensor:
                    merged[name] = cached_sensor[name]
        if result is None:
            result = dict(items)
        result[sensor_id] = merged
    return items if result is None else result


def _preserved_groups(
    run: _Run,
    sensor_id: str,
    cached_sensor: Mapping[str, Any],
    rest_sensor: Mapping[str, Any],
    requested_at: float,
) -> list[Group]:
    """
    Decide which sensor groups keep their cached state over this REST answer.

    1. WebSocket recency, per group: a frame carrying the group's fields
       landed at or after the request went out (``ws_seen >= requested_at``),
       so REST predates it. Bounded by `MAX_WS_RECENCY_PRESERVE_POLLS` and
       latched once the cap trips, until REST agrees with the cache.
    2. Door timestamps, for the door group only when (1) did not keep it: the
       cache's ``openStatusChangedAt`` is newer than REST's, or REST has none.
       Bounded by `MAX_DOOR_STATE_PRESERVE_POLLS`; the counter resets when the
       cap trips, so the comparison may win again afterwards.
    """
    tracking = run.sensor(sensor_id)
    ws_counts = dict(tracking.ws_preserve_polls)
    latched = set(tracking.ws_latched)
    cap_warned = tracking.cap_warned
    preserved: list[Group] = []

    def cap_hit(msg: str, *args: Any) -> None:
        nonlocal cap_warned
        run.log(logging.DEBUG if cap_warned else logging.WARNING, msg, *args)
        cap_warned = True

    for group in _PRESERVED_SENSOR_STATE_FIELD_GROUPS:
        if group in latched:
            if not _group_state_agrees(cached_sensor, rest_sensor, group):
                # Still disagreeing: REST keeps winning and the counter is
                # left alone, which makes the resolution a one-time event.
                continue
            # REST caught up: clear the latch and evaluate afresh.
            latched.discard(group)
            ws_counts[group] = 0
        last_ws = tracking.ws_seen.get(group)
        if last_ws is None or last_ws < requested_at:
            ws_counts[group] = 0
            continue
        count = ws_counts.get(group, 0)
        if count >= MAX_WS_RECENCY_PRESERVE_POLLS:
            cap_hit(
                "Protect coordinator: sensor %s %s WS-recency door state "
                "preservation hit the %d-poll cap; letting REST win until it "
                "agrees with the cached state, to avoid wedging or "
                "flip-flopping the cached state",
                sensor_id,
                group,
                MAX_WS_RECENCY_PRESERVE_POLLS,
            )
            latched.add(group)
            continue
        ws_counts[group] = count + 1
        preserved.append(group)

    ts_polls = tracking.ts_preserve_polls
    if "door" not in preserved:
        cached_ts = _normalize_epoch_seconds(
            get_field(cached_sensor, "openStatusChangedAt", "open_status_changed_at")
        )
        rest_ts = _normalize_epoch_seconds(
            get_field(rest_sensor, "openStatusChangedAt", "open_status_changed_at")
        )
        if cached_ts is not None and (rest_ts is None or cached_ts > rest_ts):
            if ts_polls >= MAX_DOOR_STATE_PRESERVE_POLLS:
                cap_hit(
                    "Protect coordinator: sensor %s door state preservation "
                    "hit the %d-poll cap; letting REST win to avoid wedging "
                    "the cached state permanently",
                    sensor_id,
                    MAX_DOOR_STATE_PRESERVE_POLLS,
                )
                ts_polls = 0
            else:
                ts_polls += 1
                preserved.append("door")
        else:
            ts_polls = 0

    run.set_sensor(
        sensor_id,
        replace(
            tracking,
            ts_preserve_polls=ts_polls,
            ws_preserve_polls=ws_counts,
            ws_latched=frozenset(latched),
            cap_warned=cap_warned,
        ),
    )
    return preserved


def _group_state_agrees(
    cached_sensor: Mapping[str, Any], rest_sensor: Mapping[str, Any], group: Group
) -> bool:
    """
    Return True if REST's report for ``group`` already matches the cache.

    Compares the group's state fields only. A spelling absent from both sides
    agrees; present on one side only does not. Leak sensors that report a
    leak flag compare the flag; those without one compare the timestamps.
    """
    fields = _PRESERVED_SENSOR_STATE_GROUP_STATE_FIELDS[group]
    if group == "leak" and any(
        cached_sensor.get(name) is not None or rest_sensor.get(name) is not None
        for name in _LEAK_STATE_FIELDS
    ):
        fields = _LEAK_STATE_FIELDS
    for name in fields:
        cached_has = name in cached_sensor
        if cached_has != (name in rest_sensor):
            return False
        if cached_has and cached_sensor[name] != rest_sensor[name]:
            return False
    return True


# --- latches -------------------------------------------------------------------


def _expire_stale_latches(run: _Run) -> bool:
    """
    Force-expire motion, smart-detect and ring latches held open too long.

    A latch at least `STALE_EVENT_TIMEOUT` old is dropped; if its device is
    still cached with no end, the end is written as the current wall time
    (and a camera's smart detections are cleared). A latch whose device has
    gone is dropped too. Returns True if an end was written, so the caller
    knows whether listeners have anything new to show.
    """
    now = run.now.wall
    wrote_end = False
    for kind, (collection, prefix, cleared) in _LATCHES.items():
        end_key = f"{prefix}End"
        latch = run.latch(kind)
        expired = [
            device_id
            for device_id, started in latch.items()
            if now - started >= STALE_EVENT_TIMEOUT
        ]
        if not expired:
            continue
        for device_id in expired:
            device = run.records(collection).get(device_id)
            if isinstance(device, dict) and device.get(end_key) is None:
                run.log(
                    logging.INFO,
                    "Protect coordinator: auto-clearing stale %s.%s latch for "
                    "%s after exceeding the %s safety timeout (missed 'end' "
                    "event?)",
                    collection,
                    end_key,
                    device_id,
                    STALE_EVENT_TIMEOUT,
                )
                record = {**device, end_key: now.isoformat()}
                if cleared is not None:
                    record[cleared] = []
                run.put(collection, device_id, record)
                wrote_end = True
        run.set_latch(
            kind,
            {k: v for k, v in latch.items() if k not in expired},
        )
    return wrote_end


# --- registry clean-up ---------------------------------------------------------


def _evict_missing_devices(run: _Run) -> None:
    """
    Count missing devices and remove those absent for too long (Gold rule).

    A device is removed only once it has been absent for more than
    `MAX_CONSECUTIVE_MISSING_POLLS` polls in a row; until then it stays
    tracked so its absence keeps counting. A device that reports in again
    restarts its window. An evicted sensor's tracking and an evicted hub's
    tamper record go with it, so a reused id starts clean.
    """
    for family in FAMILIES:
        current = frozenset(run.records(family))
        tracking = run.family(family)
        missing = {
            device_id: count
            for device_id, count in tracking.missing_polls.items()
            if device_id not in current
        }
        pending: set[str] = set()
        for device_id in tracking.previous_ids - current:
            count = missing.get(device_id, 0) + 1
            if count <= MAX_CONSECUTIVE_MISSING_POLLS:
                missing[device_id] = count
                pending.add(device_id)
                run.log(
                    logging.DEBUG,
                    "Protect coordinator: %s device %s missing from poll "
                    "%d/%d; deferring registry removal",
                    family,
                    device_id,
                    count,
                    MAX_CONSECUTIVE_MISSING_POLLS,
                )
                continue
            missing.pop(device_id, None)
            run.log(
                logging.DEBUG,
                "Protect coordinator: %s device %s absent for %d consecutive "
                "polls; removing it",
                family,
                device_id,
                count,
            )
            # Both identifier shapes: protect_camera_xyz, then the bare id.
            run.effects.append(
                RemoveDevice((f"protect_{family[:-1]}_{device_id}", device_id))
            )
            _forget_device(run, family, device_id)
        run.set_family(
            family,
            replace(tracking, previous_ids=current | pending, missing_polls=missing),
        )


def _forget_device(run: _Run, family: Family, device_id: str) -> None:
    """
    Drop the per-device tracking of an evicted sensor or alarm hub.

    Accepted gap: a sensor that only ever arrives by frame (the library keeps
    skipping its REST record) is never evicted, so its tracking stays. That is
    bounded by the console's own device count.
    """
    if family == "sensors" and device_id in run.state.sensors:
        run.state = replace(run.state, sensors=_without(run.state.sensors, device_id))
    elif family == "alarm_hubs" and device_id in run.state.tamper:
        run.state = replace(run.state, tamper=_without(run.state.tamper, device_id))


# --- sirens --------------------------------------------------------------------


def _apply_siren_stop(run: _Run, siren_id: str) -> None:
    """
    Show a stopped siren off at once and remember the run that was stopped.

    Protect sends no frame after a manual stop. The run is remembered by its
    own ``activatedAt`` so that a reading already on its way, which still
    says active, cannot switch it back on (see `_apply_siren_stop_guards`).
    """
    siren = run.records("sirens").get(siren_id)
    if not isinstance(siren, dict):
        return
    status = siren.get("sirenStatus")
    stop = SirenStop(
        activated_at=_number(
            status.get("activatedAt") if isinstance(status, dict) else None
        ),
        stopped_at_ms=run.now.wall.timestamp() * 1000,
        monotonic=run.now.monotonic,
    )
    run.state = replace(
        run.state, siren_stops=_with(run.state.siren_stops, siren_id, stop)
    )
    run.put("sirens", siren_id, deep_merge(siren, {"sirenStatus": {"isActive": False}}))
    # Listeners only: marking the update successful would also push the next
    # poll back when nothing has been polled.
    run.effects.append(PublishUpdate(mark_success=False))


def _apply_siren_stop_guards(run: _Run) -> None:
    """
    Keep a stopped siren off when a stale reading says it is still active.

    A reading whose ``activatedAt`` is the stopped run's or older is that run
    (both stamped by the console, so clock skew does not matter); only a
    siren whose cache held no ``activatedAt`` at the stop falls back to the
    stop's wall time. The guard ends with the first reading that is not the
    stopped run (idle, gone, a new run, or active with no start time) and
    after `SIREN_STOP_GUARD_TTL` (inclusive).
    """
    stops = run.state.siren_stops
    if not stops:
        return
    kept: dict[str, SirenStop] = {}
    for siren_id, stop in stops.items():
        siren = run.records("sirens").get(siren_id)
        status = siren.get("sirenStatus") if isinstance(siren, dict) else None
        activated_at = _number(
            status.get("activatedAt") if isinstance(status, dict) else None
        )
        limit = (
            stop.activated_at if stop.activated_at is not None else stop.stopped_at_ms
        )
        if (
            run.now.monotonic - stop.monotonic <= SIREN_STOP_GUARD_TTL
            and isinstance(siren, dict)
            and isinstance(status, dict)
            and status.get("isActive") is True
            and activated_at is not None
            and activated_at <= limit
        ):
            run.put(
                "sirens",
                siren_id,
                deep_merge(siren, {"sirenStatus": {"isActive": False}}),
            )
            kept[siren_id] = stop
    if len(kept) != len(stops):
        run.state = replace(run.state, siren_stops=kept)


# --- command overlays ----------------------------------------------------------


def _apply_overlay(run: _Run, overlay: CommandOverlay) -> None:
    """Lay a successful write's fields over its record (lists merged into)."""
    records = run.data.get(overlay.collection)
    record = records.get(overlay.device_id) if isinstance(records, dict) else None
    if not isinstance(record, dict):
        return
    run.put(
        overlay.collection,
        overlay.device_id,
        deep_merge(record, overlay.changes, into_lists=True),
    )
    run.effects.append(PublishUpdate(mark_success=False))
