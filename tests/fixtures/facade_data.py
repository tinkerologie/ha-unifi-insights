# Copyright 2026 UniFi Insights contributors
"""
Facade snapshot builders for tests (ADR-013).

The facade publishes a read-only root, so a test never writes into
``facade.data``: it builds a new snapshot and assigns it, as an owner update
would.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from homeassistant.util.read_only_dict import ReadOnlyDict

if TYPE_CHECKING:
    from collections.abc import Mapping

    from custom_components.unifi_insights.coordinators.facade import FacadeData


def with_sections(data: Mapping[str, Any], **sections: Any) -> FacadeData:
    """Return a copy of ``data`` with the given top-level sections replaced."""
    return cast("FacadeData", ReadOnlyDict({**data, **sections}))


def without_sections(data: Mapping[str, Any], *keys: str) -> FacadeData:
    """Return a copy of ``data`` without the given top-level sections."""
    return cast(
        "FacadeData",
        ReadOnlyDict({key: value for key, value in data.items() if key not in keys}),
    )
