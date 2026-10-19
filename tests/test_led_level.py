# Copyright 2026 UniFi Insights contributors
"""Tests for the UniFi Protect LED level conversions."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.unifi_insights.data_transforms import (
    led_level_to_percent,
    percent_to_led_level,
    valid_led_level,
)


@pytest.mark.parametrize(
    ("percent", "expected"),
    [(0, 1), (1, 1), (16, 1), (17, 1), (50, 3), (99, 6), (100, 6)],
)
def test_percent_to_led_level(percent: int, expected: int) -> None:
    """Test percent maps to the nearest LED level, clamped to 1-6."""
    assert percent_to_led_level(percent) == expected


@pytest.mark.parametrize(
    ("level", "expected"),
    [(1, 17), (2, 33), (3, 50), (4, 67), (5, 83), (6, 100), (3.0, 50)],
)
def test_led_level_to_percent(level: Any, expected: int) -> None:
    """Test LED levels 1-6 map to a percentage."""
    assert led_level_to_percent(level) == expected


@pytest.mark.parametrize("level", [0, 7, -1, None, "3", True, 3.5, float("nan")])
def test_led_level_to_percent_invalid(level: Any) -> None:
    """Test anything that is not a valid level maps to None."""
    assert led_level_to_percent(level) is None
    assert valid_led_level(level) is None


def test_percent_round_trip_is_stable() -> None:
    """Test a level read back as a percentage converts to the same level."""
    for level in range(1, 7):
        percent = led_level_to_percent(level)
        assert percent is not None
        assert percent_to_led_level(percent) == level
