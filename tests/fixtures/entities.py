# Copyright 2026 UniFi Insights contributors
"""Entity-platform callback doubles with the full Home Assistant signature."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from homeassistant.helpers.entity import Entity


class EntityCollector:
    """Collect platform entities and retain callback flags for assertions."""

    def __init__(self, entities: list[Entity]) -> None:
        self.entities = entities
        self.calls: list[tuple[list[Entity], bool]] = []

    def __call__(
        self,
        new_entities: Iterable[Entity],
        update_before_add: bool = False,  # noqa: FBT001, FBT002 - HA callback protocol
    ) -> None:
        batch = list(new_entities)
        self.entities.extend(batch)
        self.calls.append((batch, update_before_add))


class EntityCallback:
    """Adapt a custom test observer to the full entity-platform callback API."""

    def __init__(self, handler: Callable[[list[Entity]], None]) -> None:
        self.handler = handler

    def __call__(
        self,
        new_entities: Iterable[Entity],
        update_before_add: bool = False,  # noqa: FBT001, FBT002 - HA callback protocol
    ) -> None:
        self.handler(list(new_entities))
