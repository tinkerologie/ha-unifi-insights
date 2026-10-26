# Copyright 2026 UniFi Insights contributors
"""Assertions that retain the concrete type of optional fixture values."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import NonCallableMock

if TYPE_CHECKING:
    import pytest


def require_not_none[T](value: T | None) -> T:
    """Require an expected state, record, owner or call before inspecting it."""
    assert value is not None
    return value


def require_mock(value: object) -> NonCallableMock:
    """Assert a configured collaborator really is a mock before using mock APIs."""
    assert isinstance(value, NonCallableMock)
    return value


def require_instance[T](value: object, cls: type[T]) -> T:
    """Assert the concrete entity/device type before inspecting its fields."""
    assert isinstance(value, cls)
    return value


def patch_attribute[T](
    patcher: pytest.MonkeyPatch, target: object, name: str, value: T
) -> T:
    """Patch an existing collaborator and restore it when its test finishes."""
    patcher.setattr(target, name, value)
    return value
