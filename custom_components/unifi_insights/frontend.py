# Copyright (c) 2026 Ruaan Deysel

"""Serve the Lovelace dashboard card bundles from the integration."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Final

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import ResourceStorageCollection
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
