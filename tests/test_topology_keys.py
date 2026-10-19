# Copyright 2026 UniFi Insights contributors
"""Tests for the per-entry topology node-id keys."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import flush_store

from custom_components.unifi_insights.topology import (
    _NODE_KEYS,
    async_get_node_key,
    async_load_node_keys,
    async_remove_node_key,
)

if TYPE_CHECKING:
    import pytest
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

STORAGE_KEY = "unifi_insights.topology_keys"


async def _flush(hass: HomeAssistant) -> None:
    """Write the store's pending delayed save now."""
    await flush_store(hass.data[_NODE_KEYS].store)


def _stored_keys(hass_storage: dict[str, Any]) -> dict[str, str]:
    return hass_storage[STORAGE_KEY]["data"]["keys"]


async def test_key_is_created_persisted_and_reloaded(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A key is random per entry, stable, and survives a restart."""
    await async_load_node_keys(hass)
    key = async_get_node_key(hass, "entry-1")

    assert len(key) == 32
    assert async_get_node_key(hass, "entry-1") == key
    assert async_get_node_key(hass, "entry-2") != key

    await _flush(hass)
    assert _stored_keys(hass_storage)["entry-1"] == key.hex()

    # Simulate a restart: nothing in memory, the store is read again.
    hass.data.pop(_NODE_KEYS)
    await async_load_node_keys(hass)
    assert async_get_node_key(hass, "entry-1") == key


async def test_load_is_idempotent(hass: HomeAssistant) -> None:
    """A second load keeps keys created since the first one."""
    await async_load_node_keys(hass)
    key = async_get_node_key(hass, "entry-1")

    await async_load_node_keys(hass)

    assert async_get_node_key(hass, "entry-1") == key


async def test_load_drops_malformed_keys(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Only 32-byte lower-case hex keys are trusted from storage."""
    good = "ab" * 32
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {"keys": {"good": good, "short": "abcd", "upper": "AB" * 32, "num": 5}},
    }

    await async_load_node_keys(hass)

    assert async_get_node_key(hass, "good") == bytes.fromhex(good)
    for entry_id in ("short", "upper", "num"):
        assert len(async_get_node_key(hass, entry_id)) == 32
    assert async_get_node_key(hass, "upper") != bytes.fromhex("AB" * 32)


async def test_load_ignores_non_dict_keys(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A stored payload with the wrong shape starts from no keys."""
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {"keys": ["not", "a", "dict"]},
    }

    await async_load_node_keys(hass)

    assert hass.data[_NODE_KEYS].keys == {}


async def test_unreadable_store_starts_fresh(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A corrupt store logs a warning instead of failing component setup."""
    with (
        caplog.at_level(logging.WARNING),
        patch(
            "custom_components.unifi_insights.topology.Store.async_load",
            side_effect=HomeAssistantError("corrupt"),
        ),
    ):
        await async_load_node_keys(hass)

    assert "Could not read topology node keys" in caplog.text
    assert len(async_get_node_key(hass, "entry-1")) == 32


async def test_remove_node_key(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Removing an entry's key persists; unknown entries are a no-op."""
    await async_load_node_keys(hass)
    async_get_node_key(hass, "entry-1")
    kept = async_get_node_key(hass, "entry-2")
    await _flush(hass)

    await async_remove_node_key(hass, "entry-1")
    await async_remove_node_key(hass, "missing")
    await _flush(hass)

    assert _stored_keys(hass_storage) == {"entry-2": kept.hex()}


async def test_remove_node_key_loads_store_first(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Removal works before anything loaded the keys (entry not set up)."""
    await async_load_node_keys(hass)
    async_get_node_key(hass, "entry-1")
    await _flush(hass)
    hass.data.pop(_NODE_KEYS)

    await async_remove_node_key(hass, "entry-1")
    await _flush(hass)

    assert _stored_keys(hass_storage) == {}


async def test_entry_removal_forgets_key(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    init_integration: MockConfigEntry,
) -> None:
    """Deleting the config entry deletes its node-id key."""
    async_get_node_key(hass, init_integration.entry_id)
    await _flush(hass)
    assert init_integration.entry_id in _stored_keys(hass_storage)

    await hass.config_entries.async_remove(init_integration.entry_id)
    await _flush(hass)

    assert init_integration.entry_id not in _stored_keys(hass_storage)
