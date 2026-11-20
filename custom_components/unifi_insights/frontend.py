# Copyright (c) 2026 Ruaan Deysel

"""Serve the Lovelace dashboard card bundles from the integration."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Final

from homeassistant.components.frontend import add_extra_js_url, async_remove_panel
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.components.panel_custom import async_register_panel
from homeassistant.loader import async_get_integration
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

FRONTEND_URL_BASE: Final = f"/{DOMAIN}"
FRONTEND_PATH: Final = Path(__file__).parent / "frontend"
CARD_FILES: Final = (
    "topology-card.js",
    "site-health-card.js",
    "internet-activity-card.js",
    "performance-card.js",
    "protect-status-card.js",
    "timeline-card.js",
)
_REGISTERED: HassKey[bool] = HassKey(f"{DOMAIN}_frontend_registered")
_STATIC_REGISTERED: HassKey[bool] = HassKey(f"{DOMAIN}_frontend_static_registered")

# The admin-only "UniFi devices" panel (device groups, issue #519). It is not a
# Lovelace card, so it is not in CARD_FILES; it is served from the same static
# path with the same cache-busting query.
PANEL_FILE: Final = "device-groups-panel.js"
PANEL_URL_PATH: Final = "unifi-insights-devices"
PANEL_COMPONENT: Final = "unifi-insights-devices-panel"
PANEL_TITLE: Final = "UniFi devices"
PANEL_ICON: Final = "mdi:lan"
_PANEL_OWNERS: HassKey[set[str]] = HassKey(f"{DOMAIN}_device_groups_panel_owners")
_PANEL_LOCK: HassKey[asyncio.Lock] = HassKey(f"{DOMAIN}_device_groups_panel_lock")
_PANEL_REGISTERED: HassKey[bool] = HassKey(f"{DOMAIN}_device_groups_panel_registered")


def _bundle_digest(path: Path) -> str:
    """Short content hash of one bundle, so rebuilds bust caches too."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


async def async_register_frontend(hass: HomeAssistant) -> None:
    """
    Serve card bundles and register them as Lovelace resources once.

    Called from async_setup, like the topology WebSocket commands, so it runs
    once per Home Assistant instance rather than once per config entry. The
    bundles are served with long-lived cache headers, so each URL carries the
    integration version plus a hash of its bundle: a release or a rebuilt
    bundle under the same version (a fork or test build) busts browser caches.
    Lovelace loads its resources before creating cards. An extra frontend JS
    URL can run after card creation on a cold dashboard load, leaving a
    permanent "custom element doesn't exist" error until the page reloads.
    Storage-mode Lovelace gets a managed resource; YAML-mode keeps the extra
    JS URL as a fallback. Static paths cannot be unregistered, so nothing is
    undone on unload; the card shows its own "no integration loaded" state.
    """
    if hass.data.get(_REGISTERED):
        return
    if not {"http", "frontend"} <= hass.config.components:
        _LOGGER.debug("http/frontend not loaded; dashboard bundles not registered")
        return
    try:
        integration = await async_get_integration(hass, DOMAIN)
        if not hass.data.get(_STATIC_REGISTERED):
            await hass.http.async_register_static_paths(
                [
                    StaticPathConfig(
                        FRONTEND_URL_BASE, str(FRONTEND_PATH), cache_headers=True
                    )
                ]
            )
            hass.data[_STATIC_REGISTERED] = True

        urls: dict[str, str] = {}
        for filename in CARD_FILES:
            path = FRONTEND_PATH / filename
            digest = await hass.async_add_executor_job(_bundle_digest, path)
            url = f"{FRONTEND_URL_BASE}/{filename}"
            urls[url] = f"{url}?v={integration.version}-{digest}"

        lovelace = hass.data.get(LOVELACE_DATA)
        resources = lovelace.resources if lovelace is not None else None
        if isinstance(resources, ResourceStorageCollection):
            await resources.async_get_info()  # Load before inspecting items.
            existing_by_url = {
                item["url"].partition("?")[0]: item for item in resources.async_items()
            }
            for base_url, versioned_url in urls.items():
                existing = existing_by_url.get(base_url)
                if existing is None:
                    await resources.async_create_item(
                        {"url": versioned_url, "res_type": "module"}
                    )
                elif existing["url"] != versioned_url or existing["type"] != "module":
                    await resources.async_update_item(
                        existing["id"], {"url": versioned_url, "res_type": "module"}
                    )
        else:
            for url in urls.values():
                add_extra_js_url(hass, url)
    except Exception:
        _LOGGER.exception("Unable to register optional dashboard card frontend")
    else:
        hass.data[_REGISTERED] = True


async def _async_register_panel(hass: HomeAssistant) -> bool:
    """Register the panel; return False when it cannot be served here."""
    if not {"http", "frontend"} <= hass.config.components or not hass.data.get(
        _STATIC_REGISTERED
    ):
        _LOGGER.debug("http/frontend not loaded; device groups panel not registered")
        return False
    path = FRONTEND_PATH / PANEL_FILE
    try:
        digest = await hass.async_add_executor_job(_bundle_digest, path)
    except OSError:
        _LOGGER.debug("Device groups panel bundle %s is missing; not registered", path)
        return False
    integration = await async_get_integration(hass, DOMAIN)
    await async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=f"{FRONTEND_URL_BASE}/{PANEL_FILE}?v={integration.version}-{digest}",
        embed_iframe=False,
        require_admin=True,
        config={},
    )
    return True


async def async_reconcile_device_groups_panel(
    hass: HomeAssistant, entry_id: str, *, enabled: bool
) -> None:
    """
    Update one entry's panel ownership and reconcile the shared sidebar panel.

    Successful setup, unload and in-place changes update the owner set.
    An entry keeps ownership while its unload is pending or fails. A lock
    serialises registration so concurrent changes share a single panel.
    """
    owners = hass.data.setdefault(_PANEL_OWNERS, set())
    if enabled:
        owners.add(entry_id)
    else:
        owners.discard(entry_id)
    lock = hass.data.setdefault(_PANEL_LOCK, asyncio.Lock())
    async with lock:
        wanted = bool(owners)
        registered = hass.data.get(_PANEL_REGISTERED, False)
        if wanted == registered:
            return
        if not wanted:
            async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
            hass.data[_PANEL_REGISTERED] = False
            return
        try:
            hass.data[_PANEL_REGISTERED] = await _async_register_panel(hass)
        except Exception:
            _LOGGER.exception("Unable to register the UniFi devices panel")
