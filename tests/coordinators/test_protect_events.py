# Copyright 2026 UniFi Insights contributors
"""Table-driven tests for the pure Protect reconciler (`protect_events`).

Every test drives `reconcile` and `begin_request` only. The `run` harness
feeds a list of operations into the reconciler, checks after each one that
its inputs were left untouched (deep copies before, equality after), and
returns the final state, cache and every step. Times are plain numbers: `at`
turns ``t`` into an `Instant` whose monotonic clock is ``t`` and whose wall
clock is ``T0 + t`` seconds.
"""

from __future__ import annotations

import ast
import copy
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pytest

from custom_components.unifi_insights.coordinators.protect_events import (
    EVENT_RETENTION,
    EVENT_SWEEP_INTERVAL,
    FAMILIES,
    MAX_CONSECUTIVE_EMPTY_FETCHES,
    MAX_CONSECUTIVE_MISSING_POLLS,
    MAX_DOOR_STATE_PRESERVE_POLLS,
    MAX_WS_RECENCY_PRESERVE_POLLS,
    SIREN_STOP_GUARD_TTL,
    STALE_EVENT_TIMEOUT,
    CommandOverlay,
    DeviceFrame,
    Escalate,
    EventFrame,
    Fetched,
    Instant,
    Log,
    NoAnswer,
    NotFound,
    PollCompleted,
    PollStarted,
    PublishUpdate,
    ReconcilerState,
    RemoveDevice,
    RequestStamp,
    RestBatch,
    SirenStopped,
    Step,
    StreamReconnected,
    TransientError,
    begin_request,
    initial_state,
    reconcile,
)
from custom_components.unifi_insights.dashboard_snapshots import (
    build_timeline_snapshot,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from custom_components.unifi_insights.coordinators.protect_events import (
        Effect,
        Family,
        GuardedFamily,
        ReconcilerInput,
        RestOutcome,
    )

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
TIMEOUT = STALE_EVENT_TIMEOUT.total_seconds()
RETENTION = EVENT_RETENTION.total_seconds()
SWEEP = EVENT_SWEEP_INTERVAL.total_seconds()
# An event start (epoch ms) at T0, so within the retention of every `at` time.
START = int(T0.timestamp() * 1000)
# A run time at which an event stamped by the sweep at SWEEP is past the
# retention, and a sweep is due.
EXPIRED = SWEEP + RETENTION + 1
ACTIVATED_AT = 1_760_000_000_000


def at(t: float) -> Instant:
    """Return the instant ``t`` seconds after ``T0`` on both clocks."""
    return Instant(monotonic=t, wall=T0 + timedelta(seconds=t))


class BoomError(Exception):
    """A transient error that survives the harness's deep-copy comparison."""

    def __eq__(self, other: object) -> bool:
        return type(other) is type(self) and self.args == other.args

    __hash__ = Exception.__hash__


@dataclass(frozen=True)
class Begin:
    """Stamp a REST request for ``family`` at ``t`` (under ``key``)."""

    family: Family
    t: float
    key: str = ""


@dataclass(frozen=True)
class Answer:
    """Answer the request stamped under ``key`` at ``t``."""

    key: str
    outcome: RestOutcome
    t: float


def poll(family: Family, outcome: RestOutcome, t: float = 0.0) -> list[Any]:
    """Return a request and its answer at the same instant."""
    return [Begin(family, t), Answer(family, outcome, t)]


type Op = tuple[float, ReconcilerInput] | Begin | Answer


@dataclass
class Result:
    """What a `run` ended with."""

    state: ReconcilerState
    cache: dict[str, Any]
    steps: list[Step] = field(default_factory=list)

    @property
    def last(self) -> Step:
        return self.steps[-1]

    def effects(self, kind: type | None = None) -> list[Effect]:
        found = [effect for step in self.steps for effect in step.effects]
        return [e for e in found if kind is None or isinstance(e, kind)]

    def logs(self, level: int | None = None) -> list[str]:
        return [
            e.msg % e.args
            for e in self.effects(Log)
            if isinstance(e, Log) and (level is None or e.level == level)
        ]


def cache(**collections: Any) -> dict[str, Any]:
    """Return a coordinator cache with every collection, overlaid by ``collections``."""
    base: dict[str, Any] = {
        name: {} for name in (*FAMILIES, "doorlocks", "viewports", "liveviews")
    }
    base["events"] = {}
    base["protect_info"] = {}
    base["global_alarm_manager"] = None
    base["last_update"] = None
    base.update(collections)
    return base


def run(
    seed: dict[str, Any], ops: Iterable[Op], *, state: ReconcilerState | None = None
) -> Result:
    """Feed ``ops`` into the reconciler, checking it never mutates its inputs."""
    result = Result(state or initial_state(), seed)
    stamps: dict[str, RequestStamp] = {}
    for op in ops:
        if isinstance(op, Begin):
            stamps[op.key or op.family] = begin_request(
                result.state, op.family, at(op.t)
            )
            continue
        if isinstance(op, Answer):
            t: float = op.t
            event: ReconcilerInput = RestBatch(stamps.pop(op.key), op.outcome)
        else:
            t, event = op
        before = copy.deepcopy((result.state, result.cache, event))
        step = reconcile(result.state, result.cache, event, at(t))
        assert (result.state, result.cache, event) == before, "inputs mutated"
        result.steps.append(step)
        result.state, result.cache = step.state, step.cache
    return result


def device(model_key: str, device_id: str, **fields: Any) -> DeviceFrame:
    """Return a device frame as the parser builds it."""
    return DeviceFrame(model_key, device_id, {"id": device_id, **fields})


def event(event_type: str, event_id: str, **fields: Any) -> EventFrame:
    """Return an event frame as the parser builds it."""
    return EventFrame(
        event_type, event_id, {"type": event_type, "id": event_id, **fields}
    )


def door(*, opened: bool, changed_at: Any = 1000, **extra: Any) -> dict[str, Any]:
    """Return a door sensor record."""
    record = {"id": "s1", "isOpened": opened, **extra}
    if changed_at is not None:
        record["openStatusChangedAt"] = changed_at
    return record


def siren(*, active: bool, activated_at: Any = ACTIVATED_AT) -> dict[str, Any]:
    """Return a siren record."""
    return {
        "id": "sr1",
        "sirenStatus": {"isActive": active, "activatedAt": activated_at},
    }


# --- purity -------------------------------------------------------------------

_SOURCE = (
    Path(__file__).parents[2]
    / "custom_components/unifi_insights/coordinators/protect_events.py"
)
_ALLOWED_INTEGRATION_IMPORTS = frozenset(
    {
        "custom_components.unifi_insights.const",
        "custom_components.unifi_insights.data_transforms",
    }
)
_ALLOWED_STDLIB = frozenset(
    {"__future__", "collections.abc", "dataclasses", "datetime", "logging", "typing"}
)


def test_protect_events_imports_only_pure_modules() -> None:
    """No Home Assistant, API library, clock or sibling coordinator module."""
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, node.module
            module = node.module or ""
            assert (
                module in _ALLOWED_STDLIB or module in _ALLOWED_INTEGRATION_IMPORTS
            ), module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in _ALLOWED_STDLIB, alias.name
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            # The coordinator reads the clocks; the reconciler gets an Instant.
            assert node.func.attr not in {"now", "utcnow", "today", "monotonic"}, (
                node.func.attr
            )


# --- sensors: WebSocket then REST ---------------------------------------------


@pytest.mark.parametrize(
    ("frame_at", "rest_changed_at", "expected_open"),
    [
        pytest.param(10.0, 1000, True, id="frame-while-request-out"),
        pytest.param(5.0, 1000, True, id="frame-at-the-request-stamp"),
        pytest.param(10.0, 9000, True, id="frame-beats-later-rest-timestamp"),
        pytest.param(4.0, 9000, False, id="frame-before-request-newer-rest"),
    ],
)
def test_door_frame_racing_a_sensor_poll(
    frame_at: float,
    rest_changed_at: int,
    expected_open: bool,  # noqa: FBT001
) -> None:
    """A door frame at or after the request stamp beats the REST answer."""
    frame = (frame_at, device("sensor", "s1", isOpened=True, openStatusChangedAt=2000))
    request = Begin("sensors", 5.0)
    result = run(
        cache(sensors={"s1": door(opened=False)}),
        [
            *([frame, request] if frame_at < request.t else [request, frame]),
            Answer(
                "sensors",
                Fetched({"s1": door(opened=False, changed_at=rest_changed_at)}),
                20.0,
            ),
        ],
    )
    assert result.cache["sensors"]["s1"]["isOpened"] is expected_open
    assert result.effects(PublishUpdate) == [PublishUpdate(mark_success=True)]


def test_motion_only_frame_does_not_hold_back_the_door() -> None:
    """A frame preserves only the groups whose fields it carries."""
    result = run(
        cache(
            sensors={"s1": door(opened=True, changed_at=1000, isMotionDetected=False)}
        ),
        [
            Begin("sensors", 5.0),
            (10.0, device("sensor", "s1", isMotionDetected=True)),
            Answer(
                "sensors",
                Fetched(
                    {"s1": door(opened=False, changed_at=3000, isMotionDetected=False)}
                ),
                20.0,
            ),
        ],
    )
    sensor = result.cache["sensors"]["s1"]
    assert sensor["isOpened"] is False
    assert sensor["openStatusChangedAt"] == 3000
    assert sensor["isMotionDetected"] is True
    assert dict(result.state.sensors["s1"].ws_seen) == {"motion": 10.0}


def test_telemetry_frame_leaves_no_recency_stamp() -> None:
    """Temperature, battery and signal frames never hold back REST."""
    result = run(
        cache(sensors={"s1": door(opened=False)}),
        [(1.0, device("sensor", "s1", stats={"temperature": {"value": 21}}))],
    )
    assert "s1" not in result.state.sensors
    assert result.cache["sensors"]["s1"]["stats"] == {"temperature": {"value": 21}}


def _recency_poll(t: float, rest: dict[str, Any], **frame: Any) -> list[Any]:
    """A sensor poll with a frame landing while the request is out."""
    return [
        Begin("sensors", t),
        (t + 1, device("sensor", "s1", **frame)),
        Answer("sensors", Fetched({"s1": rest}), t + 2),
    ]


def test_ws_recency_cap_latches_until_rest_agrees() -> None:
    """Ten preserved polls, then REST wins (WARNING once) until it agrees."""
    seed = cache(sensors={"s1": door(opened=False, changed_at=None)})
    ops: list[Any] = []
    for poll_number in range(MAX_WS_RECENCY_PRESERVE_POLLS):
        ops += _recency_poll(
            10.0 * poll_number, door(opened=False, changed_at=None), isOpened=True
        )
    result = run(seed, ops)
    assert result.cache["sensors"]["s1"]["isOpened"] is True
    assert result.state.sensors["s1"].ws_preserve_polls["door"] == 10

    # The eleventh poll trips the cap: REST wins, once with a WARNING.
    result = run(
        result.cache,
        _recency_poll(200.0, door(opened=False, changed_at=None), isOpened=True),
        state=result.state,
    )
    assert result.cache["sensors"]["s1"]["isOpened"] is False
    assert result.state.sensors["s1"].ws_latched == frozenset({"door"})
    assert len(result.logs(logging.WARNING)) == 1

    # Latched: a new frame still loses to REST, the counter stays put, and
    # nothing more is logged.
    result = run(
        result.cache,
        _recency_poll(300.0, door(opened=False, changed_at=None), isOpened=True),
        state=result.state,
    )
    assert result.cache["sensors"]["s1"]["isOpened"] is False
    assert result.state.sensors["s1"].ws_preserve_polls["door"] == 10
    assert not any("poll cap" in line for line in result.logs())

    # REST agrees with the cache: the latch clears and a fresh frame wins again.
    result = run(
        result.cache,
        _recency_poll(400.0, door(opened=True, changed_at=None), isOpened=True),
        state=result.state,
    )
    assert result.state.sensors["s1"].ws_latched == frozenset()
    assert result.state.sensors["s1"].ws_preserve_polls["door"] == 1
    assert result.cache["sensors"]["s1"]["isOpened"] is True


def _latch_clears(
    group: str, frame: dict[str, Any], cached: dict[str, Any], rest: dict[str, Any]
) -> bool:
    """Latch ``group`` with eleven recency polls, then poll ``rest`` over ``cached``."""
    ops: list[Any] = []
    for poll_number in range(MAX_WS_RECENCY_PRESERVE_POLLS + 1):
        ops += _recency_poll(10.0 * poll_number, {"id": "s1"}, **frame)
    latched = run(cache(sensors={"s1": {"id": "s1", **cached}}), ops)
    assert group in latched.state.sensors["s1"].ws_latched

    result = run(
        cache(sensors={"s1": {"id": "s1", **cached}}),
        [
            Begin("sensors", 500.0),
            Answer("sensors", Fetched({"s1": {"id": "s1", **rest}}), 501.0),
        ],
        state=latched.state,
    )
    return group not in result.state.sensors["s1"].ws_latched


@pytest.mark.parametrize(
    ("cached", "rest", "expected"),
    [
        pytest.param({"isOpened": True}, {"isOpened": True}, True, id="equal"),
        pytest.param({"isOpened": True}, {"isOpened": False}, False, id="differ"),
        pytest.param({}, {}, True, id="absent-both"),
        pytest.param({}, {"isOpened": False}, False, id="rest-only"),
        pytest.param({"isOpened": True}, {}, False, id="cached-only"),
    ],
)
def test_door_latch_clears_only_when_rest_agrees(
    cached: dict[str, Any],
    rest: dict[str, Any],
    expected: bool,  # noqa: FBT001
) -> None:
    """A field present on only one side is a discrepancy, not agreement.

    Treating it as agreement would clear the latch on a REST answer that
    simply omitted the door field, re-arming recency preservation against a
    controller that never confirmed the state.
    """
    assert _latch_clears("door", {"isOpened": True}, cached, rest) is expected


@pytest.mark.parametrize(
    ("cached", "rest", "expected"),
    [
        pytest.param(
            {"isLeakDetected": True, "leakDetectedAt": 100},
            {"isLeakDetected": True, "leakDetectedAt": 200},
            True,
            id="explicit-flag-agrees-ignoring-timestamp",
        ),
        pytest.param(
            {"isLeakDetected": True, "leakDetectedAt": 100},
            {"isLeakDetected": False, "leakDetectedAt": 100},
            False,
            id="explicit-flag-disagrees",
        ),
        pytest.param(
            {"isExternalLeakDetected": True, "externalLeakDetectedAt": 100},
            {"isExternalLeakDetected": True, "externalLeakDetectedAt": 200},
            True,
            id="explicit-external-flag-agrees-ignoring-timestamp",
        ),
        pytest.param(
            {"leakDetectedAt": 100, "externalLeakDetectedAt": None},
            {"leakDetectedAt": 100, "externalLeakDetectedAt": None},
            True,
            id="timestamp-only-equal",
        ),
        pytest.param(
            {"leakDetectedAt": 100, "externalLeakDetectedAt": None},
            {"leakDetectedAt": 200, "externalLeakDetectedAt": None},
            False,
            id="timestamp-only-differ",
        ),
        pytest.param(
            {"leakDetectedAt": None, "externalLeakDetectedAt": 100},
            {"leakDetectedAt": None, "externalLeakDetectedAt": None},
            False,
            id="external-timestamp-only-differ",
        ),
        pytest.param({"isLeakDetected": True}, {}, False, id="flag-on-one-side-only"),
    ],
)
def test_leak_latch_clears_when_rest_agrees(
    cached: dict[str, Any],
    rest: dict[str, Any],
    expected: bool,  # noqa: FBT001
) -> None:
    """Leak sensors compare their flags, or their timestamps when they have none."""
    assert _latch_clears("leak", {"leakDetectedAt": 7}, cached, rest) is expected


# --- sensors: stale REST after WebSocket --------------------------------------


def test_door_timestamp_preservation_is_capped_and_warns_once() -> None:
    """A newer cached timestamp wins three polls, then REST wins once."""
    seed = cache(sensors={"s1": door(opened=True, changed_at=5000)})
    ops: list[Any] = []
    for poll_number in range(2 * (MAX_DOOR_STATE_PRESERVE_POLLS + 1)):
        ops += poll(
            "sensors",
            Fetched({"s1": door(opened=False, changed_at=1000)}),
            10.0 * poll_number,
        )
    result = run(seed, ops)

    opened = [step.cache["sensors"]["s1"]["isOpened"] for step in result.steps]
    assert opened == [True, True, True, False, False, False, False, False]
    cap_logs = [
        e.level
        for e in result.effects(Log)
        if isinstance(e, Log) and "poll cap" in e.msg
    ]
    assert cap_logs == [logging.WARNING]
    assert result.state.sensors["s1"].cap_warned is True


def test_door_timestamp_cap_resets_so_it_can_win_again() -> None:
    """The door cap resets to 0, so a newer cache can win again, logged at DEBUG."""
    seed = cache(sensors={"s1": door(opened=True, changed_at=5000)})
    ops: list[Any] = []
    for poll_number in range(MAX_DOOR_STATE_PRESERVE_POLLS + 1):
        ops += poll(
            "sensors",
            Fetched({"s1": door(opened=False, changed_at=1000)}),
            10.0 * poll_number,
        )
    capped = run(seed, ops)
    assert capped.state.sensors["s1"].ts_preserve_polls == 0

    ops = []
    for poll_number in range(MAX_DOOR_STATE_PRESERVE_POLLS + 1):
        ops += poll(
            "sensors",
            Fetched({"s1": door(opened=False, changed_at=1000)}),
            100.0 + poll_number,
        )
    again = run(
        cache(sensors={"s1": door(opened=True, changed_at=5000)}),
        ops,
        state=capped.state,
    )
    opened = [step.cache["sensors"]["s1"]["isOpened"] for step in again.steps]
    assert opened == [True, True, True, False]
    assert not again.logs(logging.WARNING)
    assert any("poll cap" in line for line in again.logs(logging.DEBUG))


@pytest.mark.parametrize(
    ("cached_ts", "rest_ts", "preserved"),
    [
        pytest.param(5000, None, True, id="rest-without-timestamp"),
        pytest.param(5000, "not a time", True, id="rest-unparseable"),
        pytest.param(None, 1000, False, id="cache-without-timestamp"),
        pytest.param(
            1_700_000_000_000, 1_700_000_001, False, id="ms-cache-older-s-rest"
        ),
        pytest.param(
            1_700_000_002_000, 1_700_000_001, True, id="ms-cache-newer-s-rest"
        ),
        pytest.param(
            1_700_000_001, 1_700_000_000_000, True, id="s-cache-newer-ms-rest"
        ),
        pytest.param(
            datetime(2026, 1, 1, tzinfo=UTC),
            1_767_225_599_000,
            True,
            id="aware-datetime",
        ),
        pytest.param(
            datetime(2026, 1, 1),  # noqa: DTZ001
            1_767_225_601_000,
            False,
            id="naive-datetime-is-utc",
        ),
        pytest.param("2026-01-01T00:00:01+00:00", 1_767_225_600_000, True, id="iso"),
        pytest.param("2026-01-01T00:00:00", 1_767_225_601_000, False, id="naive-iso"),
        pytest.param(
            "2026-01-01T00:00:01", 1_767_225_600_000, True, id="naive-iso-newer"
        ),
        pytest.param("not-a-timestamp", 1000, False, id="cache-unparseable"),
        pytest.param(True, 1000, False, id="bool-is-no-timestamp"),
        pytest.param(5000, 5000, False, id="equal-timestamps"),
        pytest.param([1], 1000, False, id="other-type"),
    ],
)
def test_door_timestamp_comparison_normalizes_epochs(
    cached_ts: Any,
    rest_ts: Any,
    preserved: bool,  # noqa: FBT001
) -> None:
    """Both sides are normalized to epoch seconds before they are compared."""
    rest = door(opened=False, changed_at=None)
    if rest_ts is not None:
        rest["open_status_changed_at"] = rest_ts
    result = run(
        cache(sensors={"s1": door(opened=True, changed_at=cached_ts)}),
        poll("sensors", Fetched({"s1": rest})),
    )
    assert result.cache["sensors"]["s1"]["isOpened"] is preserved


def test_sensor_without_a_cached_record_is_taken_from_rest() -> None:
    """A sensor REST reports for the first time has nothing to preserve."""
    result = run(cache(), poll("sensors", Fetched({"s1": door(opened=False)})))
    assert result.cache["sensors"] == {"s1": door(opened=False)}
    assert "s1" not in result.state.sensors


def test_preservation_copies_rest_records_it_changes() -> None:
    """Preserved groups are laid on copies; untouched REST records are shared."""
    s2 = {"id": "s2", "isOpened": True, "openStatusChangedAt": 5000}
    s3 = {"id": "s3", "isOpened": False}
    rest = {
        "s1": door(opened=False, changed_at=1000),
        "s2": {**s2, "isOpened": False, "openStatusChangedAt": 1000},
        "s3": s3,
    }
    result = run(
        cache(sensors={"s1": door(opened=True, changed_at=5000), "s2": s2, "s3": s3}),
        poll("sensors", Fetched(rest)),
    )
    sensors = result.cache["sensors"]
    assert (sensors["s1"]["isOpened"], sensors["s2"]["isOpened"]) == (True, True)
    assert sensors["s3"] == s3
    assert rest["s1"]["isOpened"] is False


# --- WebSocket generations (NVRs and sirens) ----------------------------------


@pytest.mark.parametrize(
    ("family", "frame", "rest", "message"),
    [
        pytest.param(
            "nvrs",
            {"armMode": {"status": "armed"}},
            {"id": "n1", "armMode": {"status": "disarmed", "armProfileId": "p1"}},
            "the NVR arrived",
            id="nvr",
        ),
        pytest.param(
            "sirens",
            {"sirenStatus": {"isActive": True}},
            siren(active=False),
            "for sirens arrived",
            id="siren",
        ),
    ],
)
def test_answer_requested_before_a_frame_is_dropped(
    family: GuardedFamily, frame: dict[str, Any], rest: dict[str, Any], message: str
) -> None:
    """A frame that lands while the request is out beats the older answer."""
    record_id = rest["id"]
    seed = cache(**{family: {record_id: copy.deepcopy(rest)}})
    result = run(
        seed,
        [
            Begin(family, 1.0),
            # The model key is the family's singular ("nvr", "siren").
            (2.0, device(family[:-1], record_id, **frame)),
            Answer(family, Fetched({record_id: rest}), 3.0),
        ],
    )
    assert result.last.cache is result.steps[0].cache
    assert any(message in line for line in result.logs(logging.DEBUG))
    assert dict(result.state.ws_generation)[family] == 1


def test_partial_arm_mode_frame_keeps_the_profile_id() -> None:
    """NVR frames deep-merge, so a status-only frame keeps armProfileId."""
    nvr = {"id": "n1", "armMode": {"status": "disarmed", "armProfileId": "p1"}}
    result = run(
        cache(nvrs={"n1": nvr}),
        [(1.0, device("nvr", "n1", armMode={"status": "armed"}))],
    )
    assert result.cache["nvrs"]["n1"]["armMode"] == {
        "status": "armed",
        "armProfileId": "p1",
    }


def test_frame_before_the_request_does_not_drop_the_answer() -> None:
    """Only a frame that lands while the request is out wins."""
    result = run(
        cache(nvrs={"n1": {"id": "n1", "name": "old"}}),
        [
            (1.0, device("nvr", "n1", name="frame")),
            *poll("nvrs", Fetched({"n1": {"id": "n1", "name": "rest"}}), 2.0),
        ],
    )
    assert result.cache["nvrs"]["n1"]["name"] == "rest"
    assert "Protect coordinator: Successfully fetched NVR: n1" in result.logs(
        logging.DEBUG
    )


def test_nvr_404_is_not_guarded_by_the_generation() -> None:
    """A 404 counts toward the empty-answer window even after a frame."""
    result = run(
        cache(nvrs={"n1": {"id": "n1"}}),
        [
            Begin("nvrs", 1.0),
            (2.0, device("nvr", "n1", name="frame")),
            Answer("nvrs", NotFound(), 3.0),
        ],
    )
    assert result.state.families["nvrs"].empty_fetches == 1


def test_nvr_answer_without_an_id_counts_as_empty() -> None:
    """An NVR answer the coordinator could not key is an empty answer."""
    result = run(cache(nvrs={"n1": {"id": "n1"}}), poll("nvrs", Fetched({})))
    assert result.cache["nvrs"] == {"n1": {"id": "n1"}}
    assert result.state.families["nvrs"].empty_fetches == 1
    assert not any("Successfully" in line for line in result.logs())


# --- alarm hub tamper ---------------------------------------------------------


def _hub(status: str | None = "normal") -> dict[str, Any]:
    alarm_hub: dict[str, Any] = {"armed": True}
    if status is not None:
        alarm_hub["deviceTamperStatus"] = status
    return {"id": "h1", "isAlarmHub": True, "alarmHub": alarm_hub}


def _tamper(
    event_id: str = "e1", status: str | None = "tampered", **extra: Any
) -> EventFrame:
    metadata: dict[str, Any] = {"userName": "Ana"}
    if status is not None:
        metadata["status"] = {"text": status}
    return event(
        "alarmHubDeviceTamper", event_id, device="h1", metadata=metadata, **extra
    )


def test_tamper_event_during_a_poll_beats_the_older_rest_status() -> None:
    """The event arrived after the request went out, so REST predates it."""
    result = run(
        cache(alarm_hubs={"h1": _hub()}),
        [
            Begin("alarm_hubs", 1.0),
            (2.0, _tamper(start=1234)),
            Answer("alarm_hubs", Fetched({"h1": _hub("normal")}), 3.0),
        ],
    )
    hub = result.cache["alarm_hubs"]["h1"]
    assert hub["alarmHub"] == {"armed": True, "deviceTamperStatus": "tampered"}
    assert hub["_lastTamperUser"] == "Ana"
    assert hub["_lastTamperAt"] == 1234


def test_tamper_event_at_the_request_stamp_still_wins() -> None:
    """A tie goes to the event (``received >= requested_at``)."""
    result = run(
        cache(alarm_hubs={"h1": _hub()}),
        [
            (2.0, _tamper()),
            *poll("alarm_hubs", Fetched({"h1": _hub("normal")}), 2.0),
        ],
    )
    assert result.cache["alarm_hubs"]["h1"]["alarmHub"]["deviceTamperStatus"] == (
        "tampered"
    )


def test_next_poll_after_a_tamper_event_is_authoritative() -> None:
    """A REST answer requested after the event wins; user and time stay."""
    result = run(
        cache(alarm_hubs={"h1": _hub()}),
        [
            (1.0, _tamper(start=99)),
            *poll("alarm_hubs", Fetched({"h1": _hub("normal")}), 2.0),
        ],
    )
    hub = result.cache["alarm_hubs"]["h1"]
    assert hub["alarmHub"]["deviceTamperStatus"] == "normal"
    assert hub["_lastTamperUser"] == "Ana"
    assert hub["_lastTamperAt"] == 99


@pytest.mark.parametrize(
    "outcome",
    [
        pytest.param(Fetched({"h1": _hub(None)}), id="rest-without-status"),
        pytest.param(NoAnswer(), id="no-answer"),
    ],
)
def test_tamper_status_stands_when_rest_says_nothing(outcome: RestOutcome) -> None:
    """Protect 7.2.x sends the event but not the field; a skipped poll keeps it."""
    result = run(
        cache(alarm_hubs={"h1": _hub(None)}),
        [(1.0, _tamper()), *poll("alarm_hubs", outcome, 2.0)],
    )
    assert result.cache["alarm_hubs"]["h1"]["alarmHub"]["deviceTamperStatus"] == (
        "tampered"
    )


def test_later_frame_of_the_same_tamper_event_fills_its_gaps() -> None:
    """An "end" without status keeps the status and its arrival time."""
    result = run(
        cache(alarm_hubs={"h1": _hub()}),
        [
            (1.0, _tamper(start=10)),
            (
                5.0,
                event(
                    "alarmHubDeviceTamper",
                    "e1",
                    device="h1",
                    metadata={"status": 5},
                ),
            ),
        ],
    )
    record = result.state.tamper["h1"]
    assert (record.status, record.received, record.last_user, record.last_at) == (
        "tampered",
        1.0,
        "Ana",
        10,
    )
    assert result.logs(logging.INFO)[-1].endswith("status=None")


def test_a_new_tamper_event_replaces_the_previous_record() -> None:
    """A restore that names nobody does not inherit the previous user."""
    result = run(
        cache(alarm_hubs={"h1": _hub()}),
        [
            (1.0, _tamper("e1")),
            (
                2.0,
                event(
                    "alarmHubDeviceTamper",
                    "e2",
                    device="h1",
                    metadata={"status": {"text": "normal"}},
                ),
            ),
        ],
    )
    hub = result.cache["alarm_hubs"]["h1"]
    assert hub["_lastTamperUser"] is None
    assert hub["alarmHub"]["deviceTamperStatus"] == "normal"


def test_tamper_event_for_an_unknown_hub_is_only_stored() -> None:
    """No hub, no record; the event is still stored and published."""
    result = run(cache(), [(1.0, _tamper())])
    assert result.state.tamper == {}
    assert result.cache["events"]["alarmHubDeviceTamper"]["e1"]["device"] == "h1"
    assert result.effects(PublishUpdate) == [PublishUpdate(mark_success=False)]


def test_tamper_record_waits_for_a_hub_missing_from_the_answer() -> None:
    """A hub absent from one answer keeps its record for when it returns."""
    result = run(
        cache(alarm_hubs={"h1": _hub()}),
        [
            (1.0, _tamper()),
            *poll("alarm_hubs", Fetched({"h2": {"id": "h2"}}), 2.0),
        ],
    )
    assert result.cache["alarm_hubs"] == {"h2": {"id": "h2"}}
    assert "h1" in result.state.tamper


def test_tamper_event_without_metadata_records_nothing_but_ids() -> None:
    """A frame without metadata leaves the REST status in place."""
    result = run(
        cache(alarm_hubs={"h1": _hub()}),
        [(1.0, event("alarmHubDeviceTamper", "e1", device="h1", metadata="?"))],
    )
    assert result.cache["alarm_hubs"]["h1"]["alarmHub"]["deviceTamperStatus"] == (
        "normal"
    )


# --- siren stop racing a poll -------------------------------------------------


def _stopped(record: dict[str, Any] | None = None) -> Result:
    """A cached running siren, stopped at t=10."""
    return run(
        cache(sirens={"sr1": record or siren(active=True)}),
        [(10.0, SirenStopped("sr1"))],
    )


def _siren_poll(stopped: Result, reading: dict[str, Any], t: float) -> Result:
    return run(
        stopped.cache, poll("sirens", Fetched({"sr1": reading}), t), state=stopped.state
    )


def _active(result: Result) -> bool:
    return bool(result.cache["sirens"]["sr1"]["sirenStatus"]["isActive"])


def test_stop_shows_off_and_publishes_listeners_only() -> None:
    """The optimistic stop notifies without marking a poll successful."""
    stopped = _stopped()
    assert _active(stopped) is False
    assert stopped.effects() == [PublishUpdate(mark_success=False)]
    stop = stopped.state.siren_stops["sr1"]
    assert stop.activated_at == ACTIVATED_AT
    assert stop.monotonic == 10.0


@pytest.mark.parametrize(
    ("reading", "t", "active", "guarded"),
    [
        pytest.param(siren(active=True), 20.0, False, True, id="same-run-stays-off"),
        pytest.param(
            siren(active=True, activated_at=ACTIVATED_AT - 1),
            20.0,
            False,
            True,
            id="older-run",
        ),
        pytest.param(siren(active=False), 20.0, False, False, id="idle-drops-guard"),
        pytest.param(
            siren(active=True, activated_at=ACTIVATED_AT + 1),
            20.0,
            True,
            False,
            id="later-run",
        ),
        pytest.param(
            siren(active=True, activated_at=None),
            20.0,
            True,
            False,
            id="active-without-start",
        ),
        pytest.param(
            siren(active=True),
            10.0 + SIREN_STOP_GUARD_TTL,
            False,
            True,
            id="ttl-boundary",
        ),
        pytest.param(
            siren(active=True),
            10.5 + SIREN_STOP_GUARD_TTL,
            True,
            False,
            id="ttl-expired",
        ),
    ],
)
def test_reading_after_a_stop(
    reading: dict[str, Any],
    t: float,
    active: bool,  # noqa: FBT001
    guarded: bool,  # noqa: FBT001
) -> None:
    """Only a reading of the stopped run, within the TTL, is held off."""
    result = _siren_poll(_stopped(), reading, t)
    assert _active(result) is active
    assert ("sr1" in result.state.siren_stops) is guarded


def test_stop_without_cached_start_falls_back_to_the_wall_clock() -> None:
    """With no cached activatedAt the stop's own wall time is the limit."""
    stopped = _stopped(siren(active=True, activated_at=None))
    stopped_at_ms = at(10.0).wall.timestamp() * 1000
    assert stopped.state.siren_stops["sr1"].stopped_at_ms == stopped_at_ms

    held = _siren_poll(stopped, siren(active=True, activated_at=stopped_at_ms), 20.0)
    assert _active(held) is False
    believed = _siren_poll(
        stopped, siren(active=True, activated_at=stopped_at_ms + 1), 20.0
    )
    assert _active(believed) is True


def test_late_frame_of_the_stopped_run_stays_off() -> None:
    """Siren frames run the guards too."""
    stopped = _stopped()
    result = run(
        stopped.cache,
        [(15.0, device("siren", "sr1", sirenStatus={"isActive": True}))],
        state=stopped.state,
    )
    assert _active(result) is False
    assert result.effects(PublishUpdate) == [PublishUpdate(mark_success=True)]


def test_siren_that_disappears_drops_its_guard() -> None:
    """A guard for a siren no longer cached ends."""
    stopped = _stopped()
    result = run(
        stopped.cache,
        poll("sirens", Fetched({"sr2": {"id": "sr2"}}), 20.0),
        state=stopped.state,
    )
    assert result.state.siren_stops == {}


def test_failed_siren_poll_still_runs_the_guards() -> None:
    """Every siren outcome, a failed one included, re-checks the guards."""
    stopped = _stopped()
    result = run(
        stopped.cache,
        poll("sirens", NoAnswer(), 10.0 + SIREN_STOP_GUARD_TTL + 1),
        state=stopped.state,
    )
    assert result.state.siren_stops == {}


@pytest.mark.parametrize(
    "sirens",
    [pytest.param({}, id="unknown"), pytest.param({"sr1": "x"}, id="not-a-dict")],
)
def test_stopping_an_unknown_siren_does_nothing(sirens: dict[str, Any]) -> None:
    """No record to update: no state, no cache change, no publish."""
    seed = cache(sirens=sirens)
    result = run(seed, [(1.0, SirenStopped("sr1"))])
    assert result.cache is seed
    assert result.effects() == []
    assert result.state.siren_stops == {}


# --- missing polls ------------------------------------------------------------


@pytest.mark.parametrize("family", FAMILIES)
def test_device_is_removed_after_missing_more_than_three_polls(family: Family) -> None:
    """Three absences are tolerated; the fourth removes it, both id shapes."""
    present = run(cache(**{family: {"d1": {"id": "d1"}}}), [(0.0, PollCompleted())])
    gone = cache()
    result = run(
        gone,
        [(float(n), PollCompleted()) for n in range(MAX_CONSECUTIVE_MISSING_POLLS + 1)],
        state=present.state,
    )
    removals = result.effects(RemoveDevice)
    assert removals == [RemoveDevice((f"protect_{family[:-1]}_d1", "d1"))]
    assert result.steps[-1].effects[-2:] == (
        Log(
            logging.DEBUG,
            "Protect coordinator: %s device %s absent for %d consecutive polls; "
            "removing it",
            (family, "d1", MAX_CONSECUTIVE_MISSING_POLLS + 1),
        ),
        removals[0],
    )
    assert result.state.families[family].previous_ids == frozenset()
    assert result.state.families[family].missing_polls == {}


def test_link_station_and_alarm_hub_identifier_shapes() -> None:
    """The family's singular name is the identifier prefix."""
    present = run(
        cache(link_stations={"l1": {}}, alarm_hubs={"h1": {}}), [(0.0, PollCompleted())]
    )
    result = run(
        cache(),
        [(float(n), PollCompleted()) for n in range(MAX_CONSECUTIVE_MISSING_POLLS + 1)],
        state=present.state,
    )
    assert set(result.effects(RemoveDevice)) == {
        RemoveDevice(("protect_link_station_l1", "l1")),
        RemoveDevice(("protect_alarm_hub_h1", "h1")),
    }


def test_device_that_reports_again_restarts_its_window() -> None:
    """Back after two misses: the count starts over."""
    with_camera = cache(cameras={"c1": {}})
    result = run(with_camera, [(0.0, PollCompleted())])
    result = run(
        cache(), [(5.0, PollCompleted()), (6.0, PollCompleted())], state=result.state
    )
    assert result.state.families["cameras"].missing_polls == {"c1": 2}
    # Seen again, then missing three more polls: still not removed.
    result = run(with_camera, [(7.0, PollCompleted())], state=result.state)
    assert result.state.families["cameras"].missing_polls == {}
    result = run(
        cache(),
        [(8.0 + n, PollCompleted()) for n in range(MAX_CONSECUTIVE_MISSING_POLLS)],
        state=result.state,
    )
    assert result.effects(RemoveDevice) == []


def test_device_missing_on_a_single_poll_is_not_removed() -> None:
    """The first steps of the window."""
    result = run(cache(cameras={"c1": {}}), [(0.0, PollCompleted())])
    result = run(cache(), [(1.0, PollCompleted())], state=result.state)
    assert result.state.families["cameras"].missing_polls == {"c1": 1}
    assert "c1" in result.state.families["cameras"].previous_ids
    assert result.logs(logging.DEBUG) == [
        (
            "Protect coordinator: cameras device c1 missing from poll 1/3; "
            "deferring registry removal"
        )
    ]


def test_evicted_sensor_and_hub_forget_their_tracking() -> None:
    """A reused id starts with no recency, counters, latch or tamper record."""
    seeded = run(
        cache(sensors={"s1": door(opened=False)}, alarm_hubs={"h1": _hub()}),
        [
            (0.0, device("sensor", "s1", isOpened=True)),
            (0.0, _tamper()),
            (0.0, PollCompleted()),
        ],
    )
    assert "s1" in seeded.state.sensors
    assert "h1" in seeded.state.tamper
    result = run(
        cache(),
        [(float(n), PollCompleted()) for n in range(MAX_CONSECUTIVE_MISSING_POLLS + 1)],
        state=seeded.state,
    )
    assert "s1" not in result.state.sensors
    assert "h1" not in result.state.tamper


def test_evicted_sensor_without_tracking() -> None:
    """Eviction of a sensor no frame ever touched has nothing to forget."""
    seeded = run(cache(sensors={"s1": door(opened=False)}), [(0.0, PollCompleted())])
    result = run(
        cache(),
        [(float(n), PollCompleted()) for n in range(MAX_CONSECUTIVE_MISSING_POLLS + 1)],
        state=seeded.state,
    )
    assert len(result.effects(RemoveDevice)) == 1


# --- empty answers and 404s ---------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "description"),
    [
        pytest.param(Fetched({}), "empty response", id="empty"),
        pytest.param(NotFound(), "404", id="404"),
    ],
)
def test_empty_answers_keep_the_cache_three_times(
    outcome: RestOutcome, description: str
) -> None:
    """The fourth empty answer in a row clears the collection, with a WARNING."""
    seed = cache(chimes={"ch1": {"id": "ch1"}})
    ops: list[Any] = []
    for n in range(MAX_CONSECUTIVE_EMPTY_FETCHES + 1):
        ops += poll("chimes", outcome, float(n))
    result = run(seed, ops)
    kept = [step.cache["chimes"] for step in result.steps]
    assert kept == [{"ch1": {"id": "ch1"}}] * 3 + [{}]
    assert result.steps[0].cache is seed
    assert result.logs(logging.WARNING) == [
        (
            f"Protect coordinator: chimes fetch returned {description} for 4 "
            "consecutive polls; clearing cached devices"
        )
    ]
    assert result.state.families["chimes"].empty_fetches == 0


def test_answer_with_devices_resets_the_empty_count() -> None:
    """A real answer ends the empty streak."""
    seed = cache(viewers={"v1": {"id": "v1"}})
    result = run(
        seed,
        [
            *poll("viewers", Fetched({})),
            *poll("viewers", Fetched({"v2": {"id": "v2"}})),
        ],
    )
    assert result.cache["viewers"] == {"v2": {"id": "v2"}}
    assert result.state.families["viewers"].empty_fetches == 0


def test_partial_answer_merges_over_the_cache() -> None:
    """A device the library could not parse keeps its last-known record."""
    seed = cache(lights={"l1": {"id": "l1", "v": 1}, "l2": {"id": "l2", "v": 1}})
    result = run(
        seed, poll("lights", Fetched({"l1": {"id": "l1", "v": 2}}, complete=False))
    )
    assert result.cache["lights"] == {
        "l1": {"id": "l1", "v": 2},
        "l2": {"id": "l2", "v": 1},
    }
    assert any("was incomplete" in line for line in result.logs(logging.DEBUG))


def test_all_unparseable_answer_keeps_the_cache_without_counting() -> None:
    """A schema change that breaks every record does not start the eviction."""
    seed = cache(fobs={"f1": {"id": "f1"}})
    result = run(seed, poll("fobs", Fetched({}, complete=False)))
    assert result.cache is seed
    assert result.state.families["fobs"].empty_fetches == 0
    assert result.logs(logging.WARNING) == [
        (
            "Protect coordinator: every fobs in the response failed to parse; "
            "preserving 1 cached devices"
        )
    ]


def test_404_on_an_empty_collection_is_logged_once_at_debug() -> None:
    """Nothing cached, nothing to keep: no device configured."""
    seed = cache()
    result = run(seed, poll("viewers", NotFound()))
    assert result.cache is seed
    assert result.logs() == [
        "Protect coordinator: viewers endpoint returned 404; no devices configured"
    ]


def test_missing_collection_is_created() -> None:
    """A cache without the collection gets an empty one."""
    seed = cache()
    del seed["chimes"]
    result = run(seed, poll("chimes", Fetched({})))
    assert result.cache["chimes"] == {}


@pytest.mark.parametrize("family", ["fobs", "link_stations", "alarm_hubs", "sirens"])
def test_device_family_answer_is_logged(family: Family) -> None:
    """Applied family answers report their size."""
    result = run(cache(), poll(family, Fetched({"d1": {"id": "d1"}})))
    assert f"Protect coordinator: Successfully fetched 1 {family}" in result.logs()


# --- transient errors ---------------------------------------------------------


@pytest.mark.parametrize("family", ["cameras", "lights", "sensors"])
def test_transient_errors_are_absorbed_three_times(family: Family) -> None:
    """The fourth error in a row escalates."""
    seed = cache(**{family: {"d1": {"id": "d1"}}})
    ops: list[Any] = []
    for n in range(MAX_CONSECUTIVE_EMPTY_FETCHES + 1):
        ops += poll(family, TransientError(BoomError("down")), float(n))
    result = run(seed, ops)
    assert [Escalate() in step.effects for step in result.steps] == [
        False,
        False,
        False,
        True,
    ]
    assert result.cache is seed
    assert result.logs()[-1] == (
        f"Protect coordinator: {family} fetch failed for 4 consecutive polls "
        "(down); failing the update"
    )


def test_404_ends_the_error_streak() -> None:
    """The endpoint answered, so the session is alive."""
    seed = cache(cameras={"c1": {"id": "c1"}})
    ops: list[Any] = []
    for n in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
        ops += poll("cameras", TransientError(BoomError("down")), float(n))
    ops += poll("cameras", NotFound(), 10.0)
    ops += poll("cameras", TransientError(BoomError("down")), 11.0)
    result = run(seed, ops)
    assert result.effects(Escalate) == []
    assert result.state.families["cameras"].fetch_errors == 1


def test_absorbed_error_resets_the_empty_count() -> None:
    """A failed fetch is no evidence that a device was removed."""
    seed = cache(cameras={"c1": {"id": "c1"}})
    result = run(
        seed,
        [
            *poll("cameras", Fetched({})),
            *poll("cameras", TransientError(BoomError("x"))),
        ],
    )
    assert result.state.families["cameras"].empty_fetches == 0


# --- latches ------------------------------------------------------------------


def _camera(**fields: Any) -> dict[str, Any]:
    return {"id": "c1", **fields}


@pytest.mark.parametrize(
    ("event_type", "start_key", "end_key"),
    [
        pytest.param("motion", "lastMotionStart", "lastMotionEnd", id="motion"),
        pytest.param("ring", "lastRingStart", "lastRingEnd", id="ring"),
    ],
)
def test_latch_expires_at_exactly_the_timeout(
    event_type: str, start_key: str, end_key: str
) -> None:
    """One second short it stays; at the timeout the end is written."""
    started = run(
        cache(cameras={"c1": _camera()}),
        [(0.0, event(event_type, "e1", device="c1", start=5))],
    )
    assert started.cache["cameras"]["c1"][start_key] == 5
    short = run(started.cache, [(TIMEOUT - 1, PollStarted())], state=started.state)
    assert short.cache is started.cache
    assert short.cache["cameras"]["c1"][end_key] is None

    expired = run(started.cache, [(TIMEOUT, PollStarted())], state=started.state)
    assert expired.cache["cameras"]["c1"][end_key] == at(TIMEOUT).wall.isoformat()
    assert expired.effects(PublishUpdate) == []
    assert len(expired.logs(logging.INFO)) == 1
    assert all(not latch for latch in expired.state.latches.values())


def test_smart_detect_expiry_clears_the_detected_types() -> None:
    """Smart detections share the camera motion latch."""
    started = run(
        cache(cameras={"c1": _camera()}),
        [
            (
                0.0,
                event(
                    "smartDetectZone", "e1", device="c1", smartDetectTypes=["person"]
                ),
            )
        ],
    )
    camera = started.cache["cameras"]["c1"]
    assert camera["lastSmartDetectTypes"] == ["person"]
    assert camera["lastMotionStart"] is None
    expired = run(started.cache, [(TIMEOUT, PollStarted())], state=started.state)
    assert expired.cache["cameras"]["c1"]["lastSmartDetectTypes"] == []


def test_smart_detect_with_invalid_types_stores_none() -> None:
    """A malformed smartDetectTypes reads as no types."""
    result = run(
        cache(cameras={"c1": _camera()}),
        [(0.0, event("smartDetect", "e1", device="c1", smartDetectTypes="person"))],
    )
    assert result.cache["cameras"]["c1"]["lastSmartDetectTypes"] == []


@pytest.mark.parametrize("key", ["smartDetectTypes", "smart_detect_types"])
def test_cached_event_preserves_classifications(key: str) -> None:
    """Cache metadata as received, without borrowing a different event's types."""
    frame = event(
        "smartDetectZone", "e1", camera="c1", **{key: ["person", "vehicle", "animal"]}
    )
    result = run(cache(cameras={"c1": _camera()}), [(0.0, frame)])
    assert result.cache["events"]["smartDetectZone"]["e1"] == frame.fields


def test_end_event_clears_the_latch() -> None:
    """A real end needs no auto-off."""
    result = run(
        cache(cameras={"c1": _camera()}),
        [
            (0.0, event("motion", "e1", device="c1", start=1)),
            (1.0, event("motion", "e1", device="c1", start=1, end=2)),
            (TIMEOUT + 1, PollStarted()),
        ],
    )
    assert result.cache["cameras"]["c1"]["lastMotionEnd"] == 2
    assert result.logs(logging.INFO)[-1].endswith("end=2")
    assert result.state.latches["camera_motion"] == {}


def test_end_for_an_unlatched_device_is_harmless() -> None:
    """An end without a start leaves the latches alone."""
    result = run(
        cache(cameras={"c1": _camera()}),
        [(0.0, event("motion", "e1", device="c1", start=1, end=2))],
    )
    assert result.state.latches["camera_motion"] == {}


def test_second_start_does_not_extend_the_latch() -> None:
    """Motion and smart detections keep the earliest start."""
    result = run(
        cache(cameras={"c1": _camera()}),
        [
            (0.0, event("motion", "e1", device="c1", start=1)),
            (60.0, event("smartDetect", "e2", device="c1", start=2)),
            (TIMEOUT, PollStarted()),
        ],
    )
    assert result.cache["cameras"]["c1"]["lastMotionEnd"] is not None


def test_camera_rebuild_without_start_drops_the_latch() -> None:
    """REST camera records carry no start, so the rebuild ends the latch."""
    started = run(
        cache(cameras={"c1": _camera()}),
        [
            (0.0, event("motion", "e1", device="c1", start=1)),
            (0.0, event("ring", "e2", device="c1", start=1)),
        ],
    )
    rebuilt = run(
        started.cache,
        [*poll("cameras", Fetched({"c1": _camera()}), 30.0), (TIMEOUT, PollStarted())],
        state=started.state,
    )
    assert rebuilt.state.latches["camera_motion"] == {}
    assert rebuilt.state.latches["camera_ring"] == {}
    assert rebuilt.logs(logging.INFO) == []


def test_camera_latch_survives_an_absorbed_error() -> None:
    """The cache did not change, and the camera still has its start."""
    started = run(
        cache(cameras={"c1": _camera()}),
        [(0.0, event("motion", "e1", device="c1", start=1))],
    )
    result = run(
        started.cache,
        poll("cameras", TransientError(BoomError("x")), 1.0),
        state=started.state,
    )
    assert "c1" in result.state.latches["camera_motion"]


def test_latch_of_a_vanished_device_is_dropped_silently() -> None:
    """A device that has gone has no end to write."""
    started = run(
        cache(cameras={"c1": _camera()}),
        [(0.0, event("motion", "e1", device="c1", start=1))],
    )
    result = run(cache(), [(TIMEOUT, PollStarted())], state=started.state)
    assert result.state.latches["camera_motion"] == {}
    assert result.logs() == []


@pytest.mark.parametrize("stream", ["devices", "events"])
def test_reconnect_sweep_that_ends_a_latch_notifies(
    stream: Literal["devices", "events"],
) -> None:
    """Listeners see the end at once, without re-arming the poll timer."""
    started = run(
        cache(cameras={"c1": _camera()}),
        [(0.0, event("motion", "e1", device="c1", start=1))],
    )
    result = run(
        started.cache, [(TIMEOUT, StreamReconnected(stream))], state=started.state
    )
    assert (
        result.cache["cameras"]["c1"]["lastMotionEnd"] == at(TIMEOUT).wall.isoformat()
    )
    assert result.effects(PublishUpdate) == [PublishUpdate(mark_success=False)]


@pytest.mark.parametrize(
    ("seed", "t"),
    [
        pytest.param(cache(cameras={"c1": _camera()}), TIMEOUT - 1, id="not-yet-stale"),
        pytest.param(cache(), TIMEOUT, id="device-gone"),
    ],
)
def test_reconnect_sweep_that_ends_nothing_publishes_nothing(
    seed: dict[str, Any], t: float
) -> None:
    """A running latch, or one of a vanished device, shows nothing new."""
    started = run(
        cache(cameras={"c1": _camera()}),
        [(0.0, event("motion", "e1", device="c1", start=1))],
    )
    result = run(seed, [(t, StreamReconnected("events"))], state=started.state)
    assert result.cache is seed
    assert result.effects(PublishUpdate) == []


@pytest.mark.parametrize(
    ("rest_light", "latched", "end"),
    [
        pytest.param(
            {"id": "l1", "name": "Porch"}, False, None, id="rebuilt-without-start"
        ),
        pytest.param(
            {"id": "l1", "name": "Porch", "lastMotionStart": 1},
            True,
            at(TIMEOUT).wall.isoformat(),
            id="rebuilt-with-start",
        ),
    ],
)
def test_light_rebuild_drops_the_latch_only_without_a_start(
    rest_light: dict[str, Any],
    latched: bool,  # noqa: FBT001
    end: str | None,
) -> None:
    """Lights follow the camera rule: no start in REST, no latch to expire."""
    started = run(
        cache(lights={"l1": {"id": "l1", "name": "Porch"}}),
        [(0.0, event("motion", "e1", device="l1", start=1))],
    )
    assert started.cache["lights"]["l1"]["lastMotionStart"] == 1
    rebuilt = run(
        started.cache,
        poll("lights", Fetched({"l1": rest_light}), 30.0),
        state=started.state,
    )
    assert ("l1" in rebuilt.state.latches["light_motion"]) is latched
    swept = run(rebuilt.cache, [(TIMEOUT, PollStarted())], state=rebuilt.state)
    assert swept.cache["lights"]["l1"].get("lastMotionEnd") == end
    assert len(swept.logs(logging.INFO)) == int(latched)


def test_light_latch_survives_an_absorbed_error() -> None:
    """The cached light keeps its start, so its latch stays."""
    started = run(
        cache(lights={"l1": {"id": "l1"}}),
        [(0.0, event("motion", "e1", device="l1", start=1))],
    )
    result = run(
        started.cache,
        poll("lights", TransientError(BoomError("x")), 1.0),
        state=started.state,
    )
    assert "l1" in result.state.latches["light_motion"]


def test_device_frame_sweeps_stale_latches_of_every_device() -> None:
    """Steady frames that postpone the poll still end a stale latch, anywhere."""
    started = run(
        cache(cameras={"c1": _camera(), "c2": _camera(id="c2")}),
        [
            (0.0, event("motion", "e1", device="c1", start=1)),
            (0.0, event("ring", "e2", device="c2", start=1)),
        ],
    )
    short = run(
        started.cache,
        [(TIMEOUT - 1, device("camera", "c2", name="still streaming"))],
        state=started.state,
    )
    assert short.cache["cameras"]["c1"]["lastMotionEnd"] is None
    assert short.effects(PublishUpdate) == [PublishUpdate(mark_success=True)]

    result = run(
        short.cache,
        [(TIMEOUT, device("camera", "c2", name="still streaming"))],
        state=short.state,
    )
    expected_end = at(TIMEOUT).wall.isoformat()
    assert result.cache["cameras"]["c1"]["lastMotionEnd"] == expected_end
    assert result.cache["cameras"]["c2"]["lastRingEnd"] == expected_end
    assert result.cache["cameras"]["c2"]["name"] == "still streaming"
    assert all(not latch for latch in result.state.latches.values())
    # The frame's own publish shows the expiry: no second one.
    assert result.effects(PublishUpdate) == [PublishUpdate(mark_success=True)]


def test_device_frame_wins_over_the_sweep_end() -> None:
    """The sweep runs first, so an end the frame carries is kept."""
    started = run(
        cache(cameras={"c1": _camera()}),
        [(0.0, event("motion", "e1", device="c1", start=1))],
    )
    result = run(
        started.cache,
        [(TIMEOUT, device("camera", "c1", lastMotionEnd=42))],
        state=started.state,
    )
    assert result.cache["cameras"]["c1"]["lastMotionEnd"] == 42


def test_dropped_frame_that_sweeps_notifies_without_marking_success() -> None:
    """A frame with no target still ends a stale latch, and listeners see it."""
    started = run(
        cache(cameras={"c1": _camera()}),
        [(0.0, event("motion", "e1", device="c1", start=1))],
    )
    result = run(
        started.cache,
        [(TIMEOUT, device("linkstation", "d1", name="x"))],
        state=started.state,
    )
    assert result.cache["cameras"]["c1"]["lastMotionEnd"] is not None
    assert result.effects(PublishUpdate) == [PublishUpdate(mark_success=False)]


def test_motion_hits_the_camera_before_a_light_with_the_same_id() -> None:
    """A camera and a light sharing an id: the camera takes the event."""
    result = run(
        cache(cameras={"x": {"id": "x"}}, lights={"x": {"id": "x"}}),
        [(0.0, event("motion", "e1", device="x", start=1))],
    )
    assert result.cache["cameras"]["x"]["lastMotionStart"] == 1
    assert "lastMotionStart" not in result.cache["lights"]["x"]


# --- frames -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model_key", "collection"),
    [
        ("light", "lights"),
        ("sensor", "sensors"),
        ("viewer", "viewers"),
        ("chime", "chimes"),
        ("doorlock", "doorlocks"),
        ("viewport", "viewports"),
    ],
)
def test_shallow_frames_replace_nested_objects(model_key: str, collection: str) -> None:
    """These families merge frames one level deep."""
    seed = cache(**{collection: {"d1": {"id": "d1", "nested": {"a": 1, "b": 2}}}})
    result = run(seed, [(0.0, device(model_key, "d1", nested={"a": 3}))])
    assert result.cache[collection]["d1"] == {"id": "d1", "nested": {"a": 3}}
    assert result.effects(PublishUpdate) == [PublishUpdate(mark_success=True)]


def test_camera_frame_is_normalized() -> None:
    """Camera frames merge shallowly and resolve the legacy shapes."""
    result = run(
        cache(cameras={"c1": {"id": "c1", "feature_flags": {"has_ptz": True}}}),
        [(0.0, device("camera", "c1", smart_detect_types=["person"]))],
    )
    camera = result.cache["cameras"]["c1"]
    assert camera["smartDetectTypes"] == ["person"]
    assert camera["isPtz"] is True
    assert camera["hasPtz"] is True
    assert (camera["lastMotion"], camera["lastRing"]) == (0, 0)


@pytest.mark.parametrize(
    ("camera", "smart_types", "is_ptz"),
    [
        pytest.param({"is_ptz": False, "hasPtz": True}, [], False, id="legacy-is-ptz"),
        pytest.param(
            {"featureFlags": {"smartDetectTypes": ["car"], "hasPtz": True}},
            ["car"],
            True,
            id="feature-flags",
        ),
        pytest.param(
            {"featureFlags": {"smart_detect_types": ["dog"]}},
            ["dog"],
            False,
            id="feature-flags-legacy",
        ),
        pytest.param(
            {"smartDetectTypes": ["x"], "isPtz": True, "lastSmartDetectTypes": ["x"]},
            ["x"],
            True,
            id="already-normal",
        ),
    ],
)
def test_camera_normalization_shapes(
    camera: dict[str, Any],
    smart_types: list[str],
    is_ptz: bool,  # noqa: FBT001
) -> None:
    """Every legacy and alias shape resolves to the camelCase fields."""
    result = run(cache(), [(0.0, device("camera", "c1", **camera))])
    normalized = result.cache["cameras"]["c1"]
    assert normalized["smartDetectTypes"] == smart_types
    assert normalized["isPtz"] is is_ptz


@pytest.mark.parametrize(
    ("model_key", "collection"),
    [
        ("fob", "fobs"),
        ("nvr", "nvrs"),
        ("siren", "sirens"),
        ("linkstation", "link_stations"),
        ("linkstation", "alarm_hubs"),
    ],
)
def test_deep_frames_keep_nested_siblings(model_key: str, collection: str) -> None:
    """Nested-object families deep-merge; an explicit null still clears."""
    seed = cache(
        **{
            collection: {
                "d1": {"id": "d1", "nested": {"a": 1, "b": 2}, "gone": {"x": 1}}
            }
        }
    )
    result = run(seed, [(0.0, device(model_key, "d1", nested={"a": 3}, gone=None))])
    assert result.cache[collection]["d1"] == {
        "id": "d1",
        "nested": {"a": 3, "b": 2},
        "gone": None,
    }


@pytest.mark.parametrize(
    ("is_alarm_hub", "collection"),
    [(True, "alarm_hubs"), (False, "link_stations")],
)
def test_new_linkstation_frame_is_routed_by_is_alarm_hub(
    is_alarm_hub: bool,  # noqa: FBT001
    collection: str,
) -> None:
    """A frame for an unknown id goes where its isAlarmHub says."""
    result = run(cache(), [(0.0, device("linkstation", "d1", isAlarmHub=is_alarm_hub))])
    assert "d1" in result.cache[collection]


def test_unknown_linkstation_frame_is_dropped() -> None:
    """No id match and no isAlarmHub: no safe target, no publish."""
    seed = cache()
    result = run(seed, [(0.0, device("linkstation", "d1", name="x"))])
    assert result.cache is seed
    assert result.effects(PublishUpdate) == []
    assert "dropping WebSocket linkstation update" in result.logs()[-1]


def test_unknown_model_key_publishes_nothing_and_logs_once() -> None:
    """A frame nothing applies leaves the poll timer alone; each key logs once."""
    seed = cache()
    result = run(
        seed,
        [
            (0.0, device("bridge", "b1", name="x")),
            (1.0, device("bridge", "b2", name="y")),
            (2.0, device("gateway", "g1", name="z")),
        ],
    )
    assert result.cache is seed
    assert result.effects(PublishUpdate) == []
    prefix = "Protect coordinator: ignoring WebSocket device updates for unknown"
    assert result.logs() == [
        f"{prefix} model key bridge",
        f"{prefix} model key gateway",
    ]
    assert result.state.unknown_model_keys == {"bridge", "gateway"}


def test_unknown_model_key_frame_that_sweeps_notifies_without_marking_success() -> None:
    """It still ends a stale latch, which listeners see; the timer is left alone."""
    started = run(
        cache(cameras={"c1": _camera()}),
        [(0.0, event("motion", "e1", device="c1", start=1))],
    )
    result = run(
        started.cache,
        [(TIMEOUT, device("bridge", "b1", name="x"))],
        state=started.state,
    )
    assert result.cache["cameras"]["c1"]["lastMotionEnd"] is not None
    assert result.effects(PublishUpdate) == [PublishUpdate(mark_success=False)]


def test_frames_share_untouched_collections() -> None:
    """Copy-on-write: only the path to the changed record is new."""
    seed = cache(
        lights={"l1": {"id": "l1"}, "l2": {"id": "l2"}}, cameras={"c1": {"id": "c1"}}
    )
    result = run(seed, [(0.0, device("light", "l1", on=True))])
    assert result.cache is not seed
    assert result.cache["cameras"] is seed["cameras"]
    assert result.cache["lights"] is not seed["lights"]
    assert result.cache["lights"]["l2"] is seed["lights"]["l2"]


def test_duplicate_frame_changes_nothing_but_publishes() -> None:
    """A frame that repeats the cached record keeps the cache object."""
    seed = cache(viewers={"v1": {"id": "v1", "name": "x"}})
    result = run(seed, [(0.0, device("viewer", "v1", name="x"))])
    assert result.cache is seed
    assert result.effects(PublishUpdate) == [PublishUpdate(mark_success=True)]


# --- events -------------------------------------------------------------------


def test_event_without_an_id_is_ignored() -> None:
    """No id, no store, no publish."""
    seed = cache()
    result = run(seed, [(0.0, EventFrame("motion", "", {"device": "c1"}))])
    assert result.cache is seed
    assert result.effects() == []


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"device": "nobody"}, id="unknown-device"),
        pytest.param({}, id="no-device"),
        pytest.param({"cameraId": "c1"}, id="unhandled-type"),
    ],
)
def test_event_is_stored_and_published_even_unapplied(fields: dict[str, Any]) -> None:
    """Listeners are notified for every event with an id."""
    result = run(
        cache(cameras={"c1": {"id": "c1"}}), [(0.0, event("access", "e1", **fields))]
    )
    assert result.cache["events"]["access"]["e1"]["id"] == "e1"
    assert result.cache["cameras"]["c1"] == {"id": "c1"}
    assert result.effects(PublishUpdate) == [PublishUpdate(mark_success=False)]


def test_event_frame_copies_only_its_type_map() -> None:
    """A new event replaces its type's map and shares every other type's."""
    first = run(cache(), [(0.0, event("ring", "e1")), (1.0, event("motion", "m1"))])
    result = run(first.cache, [(2.0, event("ring", "e2"))], state=first.state)
    assert set(result.cache["events"]["ring"]) == {"e1", "e2"}
    assert result.cache["events"] is not first.cache["events"]
    assert result.cache["events"]["motion"] is first.cache["events"]["motion"]
    assert first.cache["events"]["ring"].keys() == {"e1"}


def test_changed_event_frame_replaces_the_stored_event() -> None:
    """A later frame of a stored event replaces it in a new map."""
    first = run(cache(), [(0.0, event("motion", "m1", start=START))])
    result = run(first.cache, [(1.0, event("motion", "m1", end=2))], state=first.state)
    assert result.cache["events"]["motion"]["m1"] == {
        "type": "motion",
        "id": "m1",
        "end": 2,
    }
    assert result.cache["events"]["motion"] is not first.cache["events"]["motion"]


def test_repeated_event_frame_keeps_the_cache() -> None:
    """A frame equal to the stored event changes nothing but still publishes."""
    first = run(cache(), [(0.0, event("motion", "m1", start=START))])
    assert first.cache["events"]["motion"]["m1"]["start"] == START
    result = run(
        first.cache, [(1.0, event("motion", "m1", start=START))], state=first.state
    )
    assert result.cache is first.cache
    assert result.effects(PublishUpdate) == [PublishUpdate(mark_success=False)]


def test_event_store_drops_events_past_the_retention() -> None:
    """An event first seen more than the retention ago is dropped by a sweep."""
    result = run(
        cache(),
        [
            # Sweeps the empty store, then stores m1.
            (0.0, event("motion", "m1")),
            # Sweeps and stamps m1, then stores r1 (stamped by the next sweep).
            (SWEEP, event("ring", "r1")),
            # m1 is exactly the retention old: kept.
            (SWEEP + RETENTION, event("ring", "r2")),
        ],
    )
    assert set(result.cache["events"]) == {"motion", "ring"}
    kept = result.cache
    result = run(
        kept,
        [(SWEEP + RETENTION + SWEEP, event("ring", "r3"))],
        state=result.state,
    )
    # m1 is past the retention: dropped, and its empty type with it.
    assert set(result.cache["events"]) == {"ring"}
    assert set(result.cache["events"]["ring"]) == {"r1", "r2", "r3"}
    assert set(result.state.event_seen) == {"ring"}
    assert kept["events"]["motion"].keys() == {"m1"}


def test_sweep_copies_only_the_maps_that_lose_events() -> None:
    """A type that loses events gets a new map; the others keep theirs."""
    first = run(
        cache(),
        [
            (0.0, event("ring", "r1")),
            (0.0, event("motion", "m1")),
            (SWEEP, event("motion", "m2")),
        ],
    )
    result = run(
        first.cache,
        [(SWEEP + RETENTION + 1, event("access", "a1"))],
        state=first.state,
    )
    # r1 and m1 were stamped at SWEEP and are past the retention; m2 was
    # stored after that sweep and stamped by this one.
    assert set(result.cache["events"]) == {"motion", "access"}
    assert result.cache["events"]["motion"].keys() == {"m2"}
    assert result.cache["events"]["motion"] is not first.cache["events"]["motion"]


def test_sweep_that_drops_nothing_keeps_the_maps() -> None:
    """A due sweep that drops nothing shares every type's map."""
    first = run(cache(), [(0.0, event("ring", "r1")), (1.0, event("motion", "m1"))])
    result = run(first.cache, [(SWEEP, event("motion", "m1"))], state=first.state)
    assert result.cache is first.cache
    assert result.state.events_swept_at == at(SWEEP).wall
    assert set(result.state.event_seen) == {"ring", "motion"}


@pytest.mark.parametrize(
    ("later", "sweeps"),
    [
        pytest.param(SWEEP - 1, False, id="within-the-interval"),
        pytest.param(SWEEP, True, id="interval-elapsed"),
        pytest.param(-1.0, True, id="wall-clock-went-back"),
    ],
)
def test_event_store_sweeps_at_most_every_interval(
    later: float, *, sweeps: bool
) -> None:
    """Event frames sweep once per interval, or when the wall clock went back."""
    result = run(cache(), [(0.0, event("ring", "r1")), (later, event("ring", "r2"))])
    assert result.state.events_swept_at == at(later if sweeps else 0.0).wall
    assert ("ring" in result.state.event_seen) is sweeps


def _expiring_store() -> Result:
    """Return a run whose stored m1 was stamped by the sweep at SWEEP."""
    return run(
        cache(), [(0.0, event("motion", "m1", start=START)), (SWEEP, PollStarted())]
    )


def test_late_frame_of_an_expired_event_is_not_stored() -> None:
    """A late frame of a swept event keeps it out; a fresh event still goes in."""
    stored = _expiring_store()
    result = run(
        stored.cache,
        [
            (EXPIRED, event("motion", "m1", start=START, end=START + 1)),
            (EXPIRED + 1, event("motion", "m2", start=START + EXPIRED * 1000)),
        ],
        state=stored.state,
    )
    assert result.cache["events"] == {
        "motion": {
            "m2": {"type": "motion", "id": "m2", "start": START + EXPIRED * 1000}
        }
    }
    assert result.effects(PublishUpdate) == [PublishUpdate(mark_success=False)] * 2


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"start": 1}, id="start-seconds"),
        pytest.param({"time": START - (RETENTION + 1) * 1000}, id="time-ms"),
        pytest.param({"timestamp": "2026-10-19T10:59:59+00:00"}, id="timestamp-iso"),
    ],
)
def test_unstored_event_that_started_past_the_retention_is_not_stored(
    fields: dict[str, Any],
) -> None:
    """The start is read as the timeline reads it: start, time, then timestamp."""
    seed = cache()
    result = run(seed, [(0.0, event("motion", "m1", **fields))])
    assert result.cache["events"] == {}
    assert result.effects(PublishUpdate) == [PublishUpdate(mark_success=False)]


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({}, id="no-start"),
        pytest.param({"start": "soon"}, id="unreadable-start"),
        pytest.param({"start": START - RETENTION * 1000}, id="exactly-the-retention"),
    ],
)
def test_event_without_an_old_start_is_stored(fields: dict[str, Any]) -> None:
    """An event whose start is unreadable or within the retention is stored."""
    result = run(cache(), [(0.0, event("motion", "m1", **fields))])
    assert result.cache["events"]["motion"].keys() == {"m1"}


def test_stored_event_that_started_past_the_retention_still_updates() -> None:
    """Only an unstored event is judged by its start; a stored one is replaced."""
    first = run(cache(), [(0.0, event("motion", "m1"))])
    result = run(
        first.cache, [(1.0, event("motion", "m1", start=1))], state=first.state
    )
    assert result.cache["events"]["motion"]["m1"]["start"] == 1


def test_late_frame_of_an_expired_event_still_updates_its_device() -> None:
    """A frame kept out of the store still writes its end onto the camera."""
    stored = _expiring_store()
    seed = {**stored.cache, "cameras": {"c1": _camera(lastMotionStart=START)}}
    result = run(
        seed,
        [(EXPIRED, event("motion", "m1", device="c1", start=START, end=START + 1))],
        state=stored.state,
    )
    assert result.cache["events"] == {}
    assert result.cache["cameras"]["c1"]["lastMotionEnd"] == START + 1


def test_poll_start_expires_old_events_once_per_interval() -> None:
    """With no event frames, the poll's start sweeps the store and publishes nothing."""
    stored = _expiring_store()
    within = run(stored.cache, [(SWEEP + 1, PollStarted())], state=stored.state)
    assert within.cache is stored.cache
    assert within.state.events_swept_at == at(SWEEP).wall
    result = run(
        stored.cache,
        [(EXPIRED, PollStarted()), (EXPIRED + 1, PollStarted())],
        state=stored.state,
    )
    assert result.cache["events"] == {}
    assert result.state.events_swept_at == at(EXPIRED).wall
    assert result.steps[1].cache is result.steps[0].cache
    assert result.effects(PublishUpdate) == []


def test_poll_start_sweep_that_drops_nothing_keeps_the_cache() -> None:
    """A due sweep that drops nothing stamps the events and leaves the cache."""
    first = run(cache(), [(0.0, event("motion", "m1", start=START))])
    result = run(first.cache, [(SWEEP, PollStarted())], state=first.state)
    assert result.cache is first.cache
    assert result.state.events_swept_at == at(SWEEP).wall
    assert result.state.event_seen == {"motion": {"m1": at(SWEEP).wall}}


def test_poll_start_sweep_without_an_event_store_adds_none() -> None:
    """A sweep of a cache with no events collection leaves it without one."""
    seed = {"cameras": {}}
    result = run(seed, [(0.0, PollStarted())])
    assert result.cache is seed


def test_unpublished_device_frame_that_drops_events_notifies() -> None:
    """A frame that publishes nothing itself still shows the dropped events."""
    stored = _expiring_store()
    result = run(
        stored.cache,
        [
            (EXPIRED, device("bridge", "b1", name="x")),
            (EXPIRED + 1, device("bridge", "b1", name="x")),
        ],
        state=stored.state,
    )
    assert result.cache["events"] == {}
    assert result.state.events_swept_at == at(EXPIRED).wall
    assert result.effects(PublishUpdate) == [PublishUpdate(mark_success=False)]


def test_applied_device_frame_that_drops_events_publishes_once() -> None:
    """A frame that publishes shows the dropped events in its own update."""
    stored = _expiring_store()
    seed = {**stored.cache, "lights": {"l1": {"id": "l1"}}}
    result = run(seed, [(EXPIRED, device("light", "l1", on=True))], state=stored.state)
    assert result.cache["events"] == {}
    assert result.cache["lights"]["l1"]["on"] is True
    assert result.effects(PublishUpdate) == [PublishUpdate(mark_success=True)]


def test_device_frame_sweep_that_drops_nothing_keeps_the_cache() -> None:
    """A due sweep from an ignored frame that drops nothing changes nothing."""
    first = run(cache(), [(0.0, event("motion", "m1", start=START))])
    result = run(
        first.cache, [(SWEEP, device("bridge", "b1", name="x"))], state=first.state
    )
    assert result.cache is first.cache
    assert result.state.events_swept_at == at(SWEEP).wall
    assert result.effects(PublishUpdate) == []


def test_timeline_oldest_event_survives_the_sweep() -> None:
    """The oldest event the 24-hour timeline shows is kept by the sweep."""
    now = datetime.now(tz=UTC)
    received = now - timedelta(hours=24) + timedelta(minutes=1)
    oldest = event("motion", "oldest", start=received.timestamp() * 1000)
    # Stamped at the instant it arrived, the earliest any sweep can stamp it.
    state = replace(
        initial_state(),
        event_seen={"motion": {"oldest": received}},
        events_swept_at=received,
    )
    seed = cache(events={"motion": {"oldest": oldest.fields}})
    newest = event("motion", "newest", start=now.timestamp() * 1000)
    step = reconcile(state, seed, newest, Instant(monotonic=0.0, wall=now))
    assert step.state.events_swept_at == now
    timeline = build_timeline_snapshot(
        {"protect": step.cache}, entry_id="entry", site_id="site", hours=24
    )
    assert [item["id"] for item in timeline["items"]] == ["evt:newest", "evt:oldest"]
    assert timeline["recording_since"] == timeline["items"][-1]["timestamp"]


def test_light_motion_event_uses_its_own_latch() -> None:
    """Motion for a light id goes to the light latch."""
    result = run(
        cache(lights={"l1": {"id": "l1"}}),
        [(0.0, event("motion", "e1", device="l1", start=3))],
    )
    assert result.cache["lights"]["l1"]["lastMotionStart"] == 3
    assert set(result.state.latches["light_motion"]) == {"l1"}


# --- command overlays ---------------------------------------------------------


def test_overlay_merges_into_lists_and_keeps_siblings() -> None:
    """A chime's ringSettings entries each take the written field."""
    chime = {
        "id": "ch1",
        "name": "Hall",
        "ringSettings": [
            {"cameraId": "a", "volume": 1},
            {"cameraId": "b", "volume": 2},
            "x",
        ],
    }
    seed = cache(chimes={"ch1": chime, "ch2": {"id": "ch2"}})
    result = run(
        seed, [(0.0, CommandOverlay("chimes", "ch1", {"ringSettings": {"volume": 9}}))]
    )
    assert result.cache["chimes"]["ch1"]["ringSettings"] == [
        {"cameraId": "a", "volume": 9},
        {"cameraId": "b", "volume": 9},
        "x",
    ]
    assert result.cache["chimes"]["ch2"] is seed["chimes"]["ch2"]
    assert result.effects() == [PublishUpdate(mark_success=False)]


@pytest.mark.parametrize(
    "seed",
    [
        pytest.param(cache(), id="unknown-record"),
        pytest.param(cache(chimes={"ch1": "x"}), id="not-a-dict"),
        pytest.param(cache(chimes=None), id="no-collection"),
    ],
)
def test_overlay_for_an_unknown_record_does_nothing(seed: dict[str, Any]) -> None:
    """No record, no change, no publish."""
    result = run(seed, [(0.0, CommandOverlay("chimes", "ch1", {"volume": 1}))])
    assert result.cache is seed
    assert result.effects() == []


# --- request stamps -----------------------------------------------------------


@pytest.mark.parametrize(
    ("family", "generation"),
    [("nvrs", 0), ("sirens", 0), ("cameras", None), ("alarm_hubs", None)],
)
def test_begin_request_stamps_time_and_generation(
    family: Family, generation: int | None
) -> None:
    """Only NVRs and sirens carry a WebSocket generation."""
    stamp = begin_request(initial_state(), family, at(7.0))
    assert stamp == RequestStamp(family, 7.0, generation)


def test_poll_boundaries_without_work_return_the_same_cache() -> None:
    """Nothing to sweep or evict: the cache object is returned as is."""
    seed = cache()
    result = run(seed, [(0.0, PollStarted()), (1.0, PollCompleted())])
    assert result.cache is seed
    assert result.effects() == []
