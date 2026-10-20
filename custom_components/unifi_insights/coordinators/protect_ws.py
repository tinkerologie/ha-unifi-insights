# Copyright 2026 UniFi Insights contributors
"""
WebSocket stream adapters and stream health for the Protect coordinator.

The Protect coordinator subscribes to two independent WebSocket streams,
"devices" and "events" (see `UnifiInsightsProtectCoordinator.async_start_websocket`).
This module turns their raw frames into typed values and tracks each stream's
health; the coordinator applies the values and owns the subscription
lifecycle (ADR-007's `protect_ws.py` seam, #340).

Frame shapes. The local console sends device updates as
``{"type": "update", "item": {"id", "modelKey", ...fields}}`` (confirmed live
2026-08-12). Fields are still resolved across every plausible container
("payload", "action", "item" and the envelope itself), because picking one
container wrong would silently drop every real frame - for a door sensor, a
missed open/close. The envelope's own "type"/"action"/"payload"/"item" keys
are stripped before merging: the envelope's "type": "update" once clobbered
the hardware model string on partial updates, flipping it against the next
REST poll 49 times in 10 minutes. The "events" stream has never been seen
live, so its parser mirrors that envelope and tolerates every guess.

Stream health. Each stream has its own `connected` flag and last frame time,
so a chatty devices stream can never hide a hung events stream (an NVR
restart can leave events half-open forever with no error or close frame).
The roll-up is connected only when both streams are, and reports the newest
frame from either. Any frame counts as evidence that its stream is alive,
including one that fails to parse.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, Final, Literal, override

from unifi_official_api.const import PROTECT_RATE_LIMIT_WINDOW

if TYPE_CHECKING:
    from datetime import datetime

_LOGGER = logging.getLogger(__name__)

type Stream = Literal["devices", "events"]

# Envelope-only keys that must never leak from the raw top-level frame into a
# merged device or event dict.
ENVELOPE_ONLY_KEYS: Final = frozenset({"type", "action", "payload", "item"})

_RATE_LIMIT_ERROR: Final = "TOO_MANY_REQUESTS_ERROR"


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


class WebSocketConnectionStateCallback(partial[None]):
    """Adapt the API's positional boolean to the coordinator's named argument."""

    @override
    def __call__(self, connected: bool) -> None:
        """Forward the connection state using the coordinator's named argument."""
        super().__call__(connected=connected)


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
class RateLimited:
    """The console asked the integration to slow down its requests."""

    cooldown: float
    limit: Any
    window_ms: Any


@dataclass(slots=True)
class _StreamHealth:
    connected: bool = False
    last_message_at: datetime | None = None


class ProtectStreams:
    """Parse both streams' frames and track their health."""

    def __init__(self) -> None:
        """Start with both streams disconnected and nothing logged."""
        self._health: dict[Stream, _StreamHealth] = {
            "devices": _StreamHealth(),
            "events": _StreamHealth(),
        }
        # Each stream warns once about an unparseable frame, then logs at
        # debug, so a persistently wrong shape can't flood the log; the
        # streams are separate failure classes.
        self._parse_warned: dict[Stream, bool] = {"devices": False, "events": False}
        self._event_error_warned = False

    # --- health ------------------------------------------------------------

    def connection_changed(self, stream: Stream, *, connected: bool) -> None:
        """Record a stream connecting or dropping."""
        self._health[stream].connected = connected

    def frame_received(self, stream: Stream, received_at: datetime) -> None:
        """Record that a frame arrived on `stream`, parseable or not."""
        health = self._health[stream]
        health.last_message_at = received_at
        health.connected = True

    @property
    def connected(self) -> bool:
        """Return True only when both streams are connected."""
        return all(health.connected for health in self._health.values())

    @property
    def last_message_at(self) -> datetime | None:
        """Return the newest frame time from either stream."""
        times = [h.last_message_at for h in self._health.values() if h.last_message_at]
        return max(times) if times else None

    def health(self) -> dict[str, Any]:
        """
        Return the roll-up and per-stream health for diagnostics.

        The top-level `connected`/`last_message_at` keys keep their original
        names for existing consumers; the per-stream detail is what tells
        "both healthy" apart from "devices healthy, events hung".
        """

        def payload(health: _StreamHealth) -> dict[str, Any]:
            last = health.last_message_at
            return {
                "connected": health.connected,
                "last_message_at": last.isoformat() if last else None,
            }

        last = self.last_message_at
        return {
            "connected": self.connected,
            "last_message_at": last.isoformat() if last else None,
            "devices": payload(self._health["devices"]),
            "events": payload(self._health["events"]),
        }

    # --- parsing -------------------------------------------------------------

    def _log_unparseable(self, stream: Stream, msg: str, *args: Any) -> None:
        """Log an unparseable frame once at WARNING per stream, then at DEBUG."""
        level = logging.DEBUG if self._parse_warned[stream] else logging.WARNING
        _LOGGER.log(level, msg, *args)
        self._parse_warned[stream] = True

    def parse_device_message(
        self, message: Any, *, received_at: datetime
    ) -> DeviceFrame | None:
        """Turn a raw "devices" frame into a `DeviceFrame`, or None to drop it."""
        self.frame_received("devices", received_at)

        if not isinstance(message, dict):
            self._log_unparseable(
                "devices",
                "Protect coordinator: WebSocket message was not a JSON object: %r",
                message,
            )
            return None

        containers = [
            c
            for c in (
                message.get("payload"),
                message.get("action"),
                message.get("item"),
                message,
            )
            if isinstance(c, dict)
        ]

        model_key = pick_field(containers, "modelKey", "model_key")
        if not model_key:
            self._log_unparseable(
                "devices",
                "Protect coordinator: WebSocket device message missing modelKey: %s",
                message,
            )
            return None

        device_id = pick_field(containers, "id")
        if not device_id:
            _LOGGER.debug(
                "Protect coordinator: WebSocket %s update missing device id: %s",
                model_key,
                message,
            )
            return None

        # A new dict, never a container changed in place, so fields split
        # across payload/action (id in the header, state in the payload) are
        # all kept. Only the raw envelope has its own keys stripped.
        fields: dict[str, Any] = {}
        for container in reversed(containers):
            if container is message:
                fields.update(
                    {k: v for k, v in container.items() if k not in ENVELOPE_ONLY_KEYS}
                )
            else:
                fields.update(container)
        fields["id"] = device_id
        return DeviceFrame(model_key, device_id, fields)

    def parse_event_message(
        self, message: Any, *, received_at: datetime
    ) -> EventFrame | RateLimited | None:
        """
        Turn a raw "events" frame into an `EventFrame`, a rate limit, or None.

        The event's own "type" (motion/smartDetect/ring) comes from an inner
        item/payload/action container when there is one, never from the raw
        envelope, whose "type" is the action verb ("add"/"update"). A flat
        frame with no inner container holds the real fields itself.
        """
        self.frame_received("events", received_at)

        if not isinstance(message, dict):
            self._log_unparseable(
                "events",
                "Protect coordinator: WebSocket event message was not a JSON "
                "object: %r",
                message,
            )
            return None

        containers = [
            c
            for c in (
                message.get("payload"),
                message.get("item"),
                message.get("action"),
            )
            if isinstance(c, dict)
        ]
        if not containers:
            containers = [message]

        # The console reports its own faults on this stream as a frame with an
        # "error" field and no event fields, a rate-limit notice being the
        # common one. That is not a parse failure, so it doesn't spend the
        # one-time WARNING meant for a genuinely wrong frame shape.
        error: Any = pick_field(containers, "error")
        if error:
            return self._parse_error(message, error, containers)

        event_type = pick_field(containers, "type", "eventType", "event_type")
        if not event_type:
            self._log_unparseable(
                "events",
                "Protect coordinator: WebSocket event message missing event type: %s",
                message,
            )
            return None

        event_id = pick_field([*containers, message], "id")
        if not event_id:
            _LOGGER.debug(
                "Protect coordinator: WebSocket %s event missing event id: %s",
                event_type,
                message,
            )
            return None

        fields: dict[str, Any] = {}
        for container in reversed(containers):
            fields.update(container)
        fields["id"] = event_id
        return EventFrame(event_type, event_id, fields)

    def _parse_error(
        self, message: dict[str, Any], error: Any, containers: list[dict[str, Any]]
    ) -> RateLimited | None:
        """Return a rate limit for a rate-limit notice; log any other error."""
        error_containers = (
            [error, *containers] if isinstance(error, dict) else containers
        )
        if pick_field(error_containers, "name") == _RATE_LIMIT_ERROR:
            window_ms = pick_field(error_containers, "windowMs", "window_ms")
            cooldown = PROTECT_RATE_LIMIT_WINDOW
            if isinstance(window_ms, (int, float)) and not isinstance(window_ms, bool):
                try:
                    candidate = float(window_ms) / 1000
                except OverflowError:
                    candidate = math.inf
                if math.isfinite(candidate) and candidate > 0:
                    cooldown = candidate
            return RateLimited(
                cooldown, pick_field(error_containers, "limit"), window_ms
            )
        level = logging.DEBUG if self._event_error_warned else logging.WARNING
        _LOGGER.log(
            level, "Protect coordinator: events stream reported an error: %s", message
        )
        self._event_error_warned = True
        return None
