# Copyright 2026 UniFi Insights contributors
"""A fake Protect WebSocket that lets tests push frames into a coordinator.

The coordinator subscribes to two streams, "devices" and "events", through
``ProtectWebSocket.subscribe_with_callback``. This fake records each stream's
message and connection-state callbacks and returns at once (the real loop
blocks until ``stop()``; returning keeps ``hass.async_block_till_done(
wait_background_tasks=True)`` usable). Tests reach the coordinator only
through the seam the library uses.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


class FakeProtectWebSocket:
    """Stand-in for ``ProtectWebSocket`` with helpers to push frames."""

    def __init__(self) -> None:
        """Start with no subscriptions."""
        self._messages: dict[str, Callable[[Any], None]] = {}
        self._states: dict[str, Callable[[bool], None] | None] = {}
        self.stopped = False
        self._subscribed = asyncio.Condition()

    async def subscribe_with_callback(
        self,
        host_id: str,
        site_id: str | None,
        stream: str,
        callback: Callable[[Any], None],
        *,
        reconnect: bool = True,
        on_connection_state_change: Callable[[bool], None] | None = None,
    ) -> None:
        """Record the stream's callbacks."""
        _ = (host_id, site_id, reconnect)
        self._messages[stream] = callback
        self._states[stream] = on_connection_state_change
        async with self._subscribed:
            self._subscribed.notify_all()

    def stop(self) -> None:
        """End every subscription."""
        self.stopped = True

    async def wait_subscribed(self, *streams: str, within: float = 5) -> None:
        """Wait until every named stream has subscribed, or fail naming the rest."""
        try:
            async with asyncio.timeout(within), self._subscribed:
                await self._subscribed.wait_for(
                    lambda: set(streams) <= set(self._messages)
                )
        except TimeoutError:
            missing = sorted(set(streams) - set(self._messages))
            msg = f"streams never subscribed: {missing}"
            raise AssertionError(msg) from None

    def raw(self, stream: str, message: Any) -> None:
        """Deliver a raw message on ``stream``."""
        self._messages[stream](message)

    def device(self, model_key: str, **fields: Any) -> None:
        """Deliver a device update in the live console's envelope."""
        self.raw(
            "devices", {"type": "update", "item": {"modelKey": model_key, **fields}}
        )

    def event(self, event_type: str, **fields: Any) -> None:
        """Deliver an event in the same envelope."""
        self.raw("events", {"type": "add", "item": {"type": event_type, **fields}})

    def connection(self, stream: str, *, connected: bool) -> None:
        """Report a stream connecting or dropping."""
        state = self._states[stream]
        assert state is not None
        state(connected)
