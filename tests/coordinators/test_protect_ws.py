# Copyright 2026 UniFi Insights contributors
"""Tests for the Protect WebSocket frame parsers and stream health."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from unifi_official_api.const import PROTECT_RATE_LIMIT_WINDOW

from custom_components.unifi_insights.coordinators.protect_ws import (
    DeviceFrame,
    EventFrame,
    ProtectStreams,
    RateLimited,
    pick_field,
)

AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("key", ["smartDetectTypes", "smart_detect_types"])
def test_event_parser_preserves_detection_metadata(key: str) -> None:
    """The dashboard can read classifications without changing event parsing."""
    fields = {
        "id": "e1",
        "type": "smartDetectZone",
        "camera": "c1",
        key: ["person", "animal", "vehicle"],
    }
    parsed = ProtectStreams().parse_event_message(
        {"type": "add", "payload": fields}, received_at=AT
    )
    assert isinstance(parsed, EventFrame)
    assert parsed.fields == fields


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        pytest.param(
            {"type": "update", "item": {"id": "s1", "modelKey": "sensor", "a": 1}},
            DeviceFrame("sensor", "s1", {"id": "s1", "modelKey": "sensor", "a": 1}),
            id="item-envelope",
        ),
        pytest.param(
            {"action": {"id": "s1", "modelKey": "sensor"}, "payload": {"a": 2}},
            DeviceFrame("sensor", "s1", {"id": "s1", "modelKey": "sensor", "a": 2}),
            id="action-payload-split",
        ),
        pytest.param(
            {"type": "update", "id": "c1", "model_key": "camera"},
            DeviceFrame("camera", "c1", {"id": "c1", "model_key": "camera"}),
            id="flat-envelope-keys-stripped",
        ),
        pytest.param(
            {
                "type": "update",
                "item": {"id": "s1", "modelKey": "sensor", "type": "UFP-SENSE"},
            },
            DeviceFrame(
                "sensor", "s1", {"id": "s1", "modelKey": "sensor", "type": "UFP-SENSE"}
            ),
            id="device-type-survives",
        ),
        pytest.param({"type": "update", "item": {"id": "s1"}}, None, id="no-model"),
        pytest.param({"item": {"modelKey": "sensor"}}, None, id="no-id"),
        pytest.param(["not", "a", "dict"], None, id="not-a-dict"),
    ],
)
def test_parse_device_message(message: Any, expected: DeviceFrame | None) -> None:
    """Device frames resolve their fields across every envelope shape."""
    assert ProtectStreams().parse_device_message(message, received_at=AT) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        pytest.param(
            {"type": "add", "item": {"type": "motion", "id": "e1", "device": "c1"}},
            EventFrame("motion", "e1", {"type": "motion", "id": "e1", "device": "c1"}),
            id="item-envelope",
        ),
        pytest.param(
            {"eventType": "ring", "id": "e2"},
            EventFrame("ring", "e2", {"eventType": "ring", "id": "e2"}),
            id="flat",
        ),
        pytest.param(
            {"type": "add", "action": {"type": "ring", "id": "e3", "device": "c1"}},
            EventFrame("ring", "e3", {"type": "ring", "id": "e3", "device": "c1"}),
            id="action-container",
        ),
        pytest.param(
            {"type": "add", "payload": {"event_type": "motion", "id": "e4"}},
            EventFrame("motion", "e4", {"event_type": "motion", "id": "e4"}),
            id="snake-case-event-type",
        ),
        pytest.param({"type": "add", "item": {"id": "e1"}}, None, id="no-type"),
        pytest.param({"type": "add", "item": {"type": "motion"}}, None, id="no-id"),
        pytest.param("text", None, id="not-a-dict"),
        pytest.param({"error": "Internal error"}, None, id="other-error"),
    ],
)
def test_parse_event_message(message: Any, expected: EventFrame | None) -> None:
    """Event frames take their type from the inner container, never the envelope."""
    assert ProtectStreams().parse_event_message(message, received_at=AT) == expected


@pytest.mark.parametrize(
    ("window_ms", "cooldown"),
    [
        (2500, 2.5),
        (None, PROTECT_RATE_LIMIT_WINDOW),
        (True, PROTECT_RATE_LIMIT_WINDOW),
        (-1, PROTECT_RATE_LIMIT_WINDOW),
        (float("inf"), PROTECT_RATE_LIMIT_WINDOW),
        (10**400, PROTECT_RATE_LIMIT_WINDOW),
    ],
)
def test_rate_limit_notice(window_ms: Any, cooldown: float) -> None:
    """A rate-limit notice becomes a cooldown, falling back to the library's."""
    parsed = ProtectStreams().parse_event_message(
        {"error": {"name": "TOO_MANY_REQUESTS_ERROR", "windowMs": window_ms}},
        received_at=AT,
    )
    assert parsed == RateLimited(cooldown, None, window_ms)


def test_rate_limit_notice_with_snake_case_window_and_limit() -> None:
    """A flat notice using ``window_ms`` and a limit is parsed the same way."""
    parsed = ProtectStreams().parse_event_message(
        {
            "error": "Too many requests",
            "name": "TOO_MANY_REQUESTS_ERROR",
            "window_ms": 4000,
            "limit": 20,
        },
        received_at=AT,
    )
    assert parsed == RateLimited(4.0, 20, 4000)


def test_unparseable_frames_warn_once_per_stream(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each stream warns once about a bad frame shape, then logs at debug."""
    streams = ProtectStreams()
    with caplog.at_level(logging.DEBUG):
        for _ in range(2):
            streams.parse_device_message("bad", received_at=AT)
            streams.parse_event_message("bad", received_at=AT)
    levels = [r.levelno for r in caplog.records]
    assert levels == [logging.WARNING, logging.WARNING, logging.DEBUG, logging.DEBUG]


def test_health_rolls_up_both_streams() -> None:
    """Connected only when both streams are; the newest frame time wins."""
    streams = ProtectStreams()
    assert streams.health() == {
        "connected": False,
        "last_message_at": None,
        "devices": {"connected": False, "last_message_at": None},
        "events": {"connected": False, "last_message_at": None},
    }

    streams.parse_device_message({"bad": True}, received_at=AT)
    assert streams.health()["devices"]["connected"] is True
    assert streams.connected is False

    later = AT + timedelta(seconds=5)
    streams.frame_received("events", later)
    assert streams.connected is True
    assert streams.last_message_at == later

    streams.connection_changed("events", connected=False)
    assert streams.health()["connected"] is False
    assert streams.health()["events"] == {
        "connected": False,
        "last_message_at": later.isoformat(),
    }


def test_pick_field_is_truthy() -> None:
    """An empty value is skipped in favour of a later container."""
    assert pick_field([{"id": ""}, {"id": "x"}], "id") == "x"
    assert pick_field([{"a": 1}], "id") is None
