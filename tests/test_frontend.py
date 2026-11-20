# Copyright (c) 2026 Ruaan Deysel

"""Tests for serving the topology card bundle."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant.common import MockConfigEntry

from custom_components.unifi_insights import async_setup, frontend
from custom_components.unifi_insights.frontend import (
    CARD_FILES,
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
    topology_url = f"{FRONTEND_URL_BASE}/{CARD_FILES[0]}"
    resources = FakeResources(
        [{"id": "topology", "url": f"{topology_url}?v=old", "type": "module"}]
    )
    hass.data[LOVELACE_DATA].resources = resources
    with patch.object(frontend, "ResourceStorageCollection", FakeResources):
        await async_register_frontend(hass)

    resources.async_update_item.assert_awaited_once()
    assert resources.async_create_item.await_count == len(CARD_FILES) - 1
    assert resources.items[0]["url"] != f"{topology_url}?v=old"


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
    assert (FRONTEND_PATH / CARD_FILES[0]).is_file()


# --- Device groups panel (issue #519) ----------------------------------------


@pytest.fixture
def panel_web(hass, tmp_path):
    """Web components loaded, static path served, and a panel bundle on disk."""
    hass.config.components.update({"http", "frontend"})
    hass.data[frontend._STATIC_REGISTERED] = True
    (tmp_path / frontend.PANEL_FILE).write_text("// panel")
    with patch.object(frontend, "FRONTEND_PATH", tmp_path):
        yield tmp_path


def _entry(hass, entry_id: str, *, grouping: bool, state=None):
    """Add a UniFi Insights entry with runtime data carrying the applied option."""
    entry = MockConfigEntry(domain=frontend.DOMAIN, entry_id=entry_id)
    entry.add_to_hass(hass)
    entry.mock_state(hass, state or ConfigEntryState.LOADED)
    entry.runtime_data = SimpleNamespace(group_devices_by_uplink=grouping)
    return entry


@pytest.fixture
def panel_calls():
    """Patch the panel register/remove calls."""
    with (
        patch.object(frontend, "async_register_panel", new_callable=AsyncMock) as reg,
        patch.object(frontend, "async_remove_panel") as remove,
    ):
        yield reg, remove


async def test_panel_registers_once_for_two_entries_and_follows_unloads(
    hass, panel_web, panel_calls, enable_custom_integrations
) -> None:
    """Two enabled entries share one panel; it goes only with the last one."""
    register, remove = panel_calls
    _entry(hass, "a", grouping=True)
    _entry(hass, "b", grouping=True)

    await frontend.async_reconcile_device_groups_panel(hass, include="a")
    await frontend.async_reconcile_device_groups_panel(hass, include="b")
    register.assert_awaited_once()
    kwargs = register.await_args.kwargs
    digest = hashlib.sha256(b"// panel").hexdigest()[:8]
    version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
    assert kwargs == {
        "frontend_url_path": "unifi-insights-devices",
        "webcomponent_name": "unifi-insights-devices-panel",
        "sidebar_title": "UniFi devices",
        "sidebar_icon": "mdi:lan",
        "module_url": f"/unifi_insights/device-groups-panel.js?v={version}-{digest}",
        "embed_iframe": False,
        "require_admin": True,
        "config": {},
    }
    assert frontend.PANEL_FILE not in CARD_FILES

    await frontend.async_reconcile_device_groups_panel(hass, exclude="a")
    remove.assert_not_called()

    hass.config_entries.async_get_entry("a").mock_state(
        hass, ConfigEntryState.NOT_LOADED
    )
    await frontend.async_reconcile_device_groups_panel(hass, exclude="b")
    remove.assert_called_once_with(
        hass, "unifi-insights-devices", warn_if_unknown=False
    )


async def test_panel_concurrent_reconciles_register_once(
    hass, panel_web, panel_calls, enable_custom_integrations
) -> None:
    """The lock serialises reconciles started together."""
    register, _remove = panel_calls
    _entry(hass, "a", grouping=True)

    await asyncio.gather(
        *(frontend.async_reconcile_device_groups_panel(hass) for _ in range(3))
    )

    register.assert_awaited_once()


async def test_panel_ignores_disabled_unloaded_and_carrier_entries(
    hass, panel_web, panel_calls
) -> None:
    """Off, not loaded, excluded or runtime without the option: no panel."""
    register, _remove = panel_calls
    _entry(hass, "off", grouping=False)
    _entry(hass, "setup", grouping=True, state=ConfigEntryState.SETUP_IN_PROGRESS)
    _entry(hass, "leaving", grouping=True)
    carrier = _entry(hass, "carrier", grouping=True)
    carrier.runtime_data = SimpleNamespace()

    await frontend.async_reconcile_device_groups_panel(hass, exclude="leaving")

    register.assert_not_awaited()


async def test_panel_skips_without_web_server(hass, tmp_path, panel_calls) -> None:
    """Headless (no http component): nothing is registered."""
    register, _remove = panel_calls
    hass.config.components.add("frontend")
    hass.data[frontend._STATIC_REGISTERED] = True
    (tmp_path / frontend.PANEL_FILE).write_text("// panel")
    _entry(hass, "a", grouping=True)

    with patch.object(frontend, "FRONTEND_PATH", tmp_path):
        await frontend.async_reconcile_device_groups_panel(hass)

    register.assert_not_awaited()
    assert not hass.data.get(frontend._PANEL_REGISTERED)


async def test_panel_skips_without_static_path(hass, panel_web, panel_calls) -> None:
    """When the bundles could not be served, the panel is not registered."""
    register, _remove = panel_calls
    hass.data.pop(frontend._STATIC_REGISTERED)
    _entry(hass, "a", grouping=True)

    await frontend.async_reconcile_device_groups_panel(hass)

    register.assert_not_awaited()


async def test_panel_skips_missing_bundle(hass, panel_web, panel_calls, caplog) -> None:
    """A tree without the bundle (a partial build) skips registration quietly."""
    register, _remove = panel_calls
    (panel_web / frontend.PANEL_FILE).unlink()
    _entry(hass, "a", grouping=True)

    with caplog.at_level(logging.DEBUG, logger=frontend.__name__):
        await frontend.async_reconcile_device_groups_panel(hass)

    register.assert_not_awaited()
    assert "is missing" in caplog.text


async def test_panel_registration_error_is_logged_and_retried(
    hass, panel_web, panel_calls, caplog, enable_custom_integrations
) -> None:
    """A failure never escapes into entry setup, and the next reconcile retries."""
    register, _remove = panel_calls
    register.side_effect = [ValueError("Overwriting panel"), None]
    _entry(hass, "a", grouping=True)

    await frontend.async_reconcile_device_groups_panel(hass)
    assert "Unable to register the UniFi devices panel" in caplog.text
    await frontend.async_reconcile_device_groups_panel(hass)

    assert register.await_count == 2
    assert hass.data[frontend._PANEL_REGISTERED] is True
