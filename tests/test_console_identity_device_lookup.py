# Copyright 2026 UniFi Insights contributors
"""Tests for helper utilities in UniFi Insights."""

from unittest.mock import MagicMock

import pytest
from homeassistant.helpers import device_registry as dr

from custom_components.unifi_insights.console_identity import async_get_device_entry

IDENTIFIER = ("unifi_insights", "dev1")


@pytest.fixture
def registry() -> MagicMock:
    """Return a registry double that only has real DeviceRegistry methods.

    ``create_autospec`` can't be used: it evaluates the registry's annotations,
    which name types Home Assistant only imports for type checking.
    """
    return MagicMock(spec=dr.DeviceRegistry)


def test_uses_by_identifier_with_config_entry_id(registry: MagicMock) -> None:
    """With a config entry id the config-entry-scoped lookup is used."""
    device = object()
    registry.async_get_device_by_identifier.return_value = device

    assert async_get_device_entry(registry, IDENTIFIER, "entry1") is device
    registry.async_get_device_by_identifier.assert_called_once_with(
        IDENTIFIER, "entry1"
    )
    registry.async_get_device.assert_not_called()


def test_miss_does_not_fall_back_to_deprecated_api(registry: MagicMock) -> None:
    """A miss returns None without touching the deprecated async_get_device."""
    registry.async_get_device_by_identifier.return_value = None

    assert async_get_device_entry(registry, IDENTIFIER, "entry1") is None
    registry.async_get_device.assert_not_called()
    registry.async_get_devices.assert_not_called()


def test_uses_get_devices_without_config_entry_id(registry: MagicMock) -> None:
    """Without a config entry id the unscoped list lookup is used."""
    device = object()
    registry.async_get_devices.return_value = [device]

    assert async_get_device_entry(registry, IDENTIFIER) is device
    registry.async_get_devices.assert_called_once_with(identifiers={IDENTIFIER})
    registry.async_get_device.assert_not_called()


def test_get_devices_empty_returns_none(registry: MagicMock) -> None:
    """An empty list from async_get_devices resolves to None."""
    registry.async_get_devices.return_value = []

    assert async_get_device_entry(registry, IDENTIFIER) is None
    registry.async_get_device.assert_not_called()
