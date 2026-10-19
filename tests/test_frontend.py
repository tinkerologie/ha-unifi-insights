# Copyright (c) 2026 Ruaan Deysel

"""Tests for serving the topology card bundle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.lovelace.const import LOVELACE_DATA

from custom_components.unifi_insights import async_setup, frontend
from custom_components.unifi_insights.frontend import (
    CARD_FILES,
    CARD_PATH,
    CARD_URL,
    FRONTEND_PATH,
    FRONTEND_URL_BASE,
    async_register_frontend,
)

MANIFEST = Path(frontend.__file__).parent / "manifest.json"


def _expected_urls() -> dict[str, str]:
    version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
    urls: dict[str, str] = {}
    for filename in CARD_FILES:
        digest = hashlib.sha256((FRONTEND_PATH / filename).read_bytes()).hexdigest()[:8]
        base = f"{FRONTEND_URL_BASE}/{filename}"
        urls[base] = f"{base}?v={version}-{digest}"
    return urls


class FakeResources:
    """A storage resource collection with the Home Assistant CRUD contract."""

    def __init__(self, items: list[dict] | None = None) -> None:
        self.items = items or []
        self.async_get_info = AsyncMock(return_value={"resources": len(self.items)})
        self.async_create_item = AsyncMock(side_effect=self._create)
        self.async_update_item = AsyncMock(side_effect=self._update)

    def async_items(self) -> list[dict]:
        """Return loaded resources."""
        return self.items

    def _create(self, data: dict) -> dict:
        item = {
            "id": f"res-{len(self.items)}",
            "url": data["url"],
            "type": data["res_type"],
        }
        self.items.append(item)
        return item

    def _update(self, item_id: str, data: dict) -> dict:
        item = next(item for item in self.items if item["id"] == item_id)
        item.update(url=data["url"], type=data["res_type"])
        return item


@pytest.fixture
def http(hass):
    """Pretend the web components are loaded with a storage dashboard."""
    mock = MagicMock()
    mock.async_register_static_paths = AsyncMock()
    hass.http = mock
    hass.config.components.update({"http", "frontend", "lovelace"})
    hass.data[LOVELACE_DATA] = SimpleNamespace(resources=FakeResources())
    return mock


async def test_registers_bundle_once(hass, http, enable_custom_integrations) -> None:
    """The static path and the Lovelace module resources register once."""
    expected = _expected_urls()
    with (
        patch.object(frontend, "ResourceStorageCollection", FakeResources),
        patch.object(frontend, "add_extra_js_url") as add_js,
    ):
        await async_register_frontend(hass)
        await async_register_frontend(hass)

    http.async_register_static_paths.assert_awaited_once()
    (configs,) = http.async_register_static_paths.await_args.args
    assert [(c.url_path, c.path, c.cache_headers) for c in configs] == [
        (FRONTEND_URL_BASE, str(FRONTEND_PATH), True)
    ]
    resources = hass.data[LOVELACE_DATA].resources
    assert [item["url"] for item in resources.items] == list(expected.values())
    assert resources.async_create_item.await_count == len(CARD_FILES)
    add_js.assert_not_called()


async def test_updates_resource_when_bundle_changes(
    hass, http, enable_custom_integrations
) -> None:
    """Upgrade an existing versioned resource rather than adding a duplicate."""
    resources = FakeResources(
        [{"id": "topology", "url": f"{CARD_URL}?v=old", "type": "module"}]
    )
    hass.data[LOVELACE_DATA].resources = resources
    with patch.object(frontend, "ResourceStorageCollection", FakeResources):
        await async_register_frontend(hass)

    resources.async_update_item.assert_awaited_once()
    assert resources.async_create_item.await_count == len(CARD_FILES) - 1
    assert resources.items[0]["url"] != f"{CARD_URL}?v=old"


async def test_keeps_current_resource_unchanged(
    hass, http, enable_custom_integrations
) -> None:
    """A current Lovelace resource needs neither creation nor an update."""
    expected = _expected_urls()
    items = [
        {"id": f"res-{idx}", "url": url, "type": "module"}
        for idx, url in enumerate(expected.values())
    ]
    resources = FakeResources(list(items))
    hass.data[LOVELACE_DATA].resources = resources

    with patch.object(frontend, "ResourceStorageCollection", FakeResources):
        await async_register_frontend(hass)

    resources.async_create_item.assert_not_awaited()
    resources.async_update_item.assert_not_awaited()
    assert resources.items == items
    http.async_register_static_paths.assert_awaited_once()


async def test_resource_failure_does_not_fail_setup_and_can_retry(
    hass, http, enable_custom_integrations, caplog
) -> None:
    """Optional card storage errors leave the integration and a retry usable."""
    resources = hass.data[LOVELACE_DATA].resources
    resources.async_create_item.side_effect = OSError("storage unavailable")
    with patch.object(frontend, "ResourceStorageCollection", FakeResources):
        await async_register_frontend(hass)
        assert not hass.data.get(frontend._REGISTERED)
        resources.async_create_item.side_effect = resources._create
        await async_register_frontend(hass)

    assert "Unable to register optional dashboard card frontend" in caplog.text
    assert hass.data[frontend._REGISTERED]
    http.async_register_static_paths.assert_awaited_once()
    assert len(resources.items) == len(CARD_FILES)


async def test_yaml_resources_use_frontend_fallback(
    hass, http, enable_custom_integrations
) -> None:
    """YAML resources cannot be written through the storage collection."""
    hass.data[LOVELACE_DATA].resources = MagicMock()
    with patch.object(frontend, "add_extra_js_url") as add_js:
        await async_register_frontend(hass)

    assert add_js.call_count == len(CARD_FILES)


@pytest.mark.parametrize("missing", ["http", "frontend"])
async def test_skips_when_a_web_component_is_not_loaded(hass, missing) -> None:
    """Headless setups (and the test harness) get no card, and no error."""
    hass.http = MagicMock()
    hass.http.async_register_static_paths = AsyncMock()
    hass.config.components.update({"http", "frontend"} - {missing})
    with patch.object(frontend, "add_extra_js_url") as add_js:
        await async_register_frontend(hass)

    hass.http.async_register_static_paths.assert_not_awaited()
    add_js.assert_not_called()


async def test_async_setup_registers_the_card(hass) -> None:
    """Component setup registers the card alongside the WebSocket commands."""
    with (
        patch("custom_components.unifi_insights.async_setup_services"),
        patch("custom_components.unifi_insights.async_load_node_keys"),
        patch("custom_components.unifi_insights.async_register_websocket_commands"),
        patch("custom_components.unifi_insights.async_register_frontend") as register,
    ):
        assert await async_setup(hass, {})

    register.assert_awaited_once_with(hass)


def test_manifest_loads_after_web_components() -> None:
    """http/frontend are ordering hints, not hard dependencies.

    Tests run without hass_frontend.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert set(manifest["after_dependencies"]) >= {"http", "frontend", "lovelace"}
    assert "frontend" not in manifest["dependencies"]


def test_bundle_is_shipped() -> None:
    """The Marketplace and HACS install the repository tree, so commit the bundle."""
    assert CARD_PATH.is_file()
