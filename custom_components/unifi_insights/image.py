# Copyright 2026 UniFi Insights contributors
"""Support for UniFi Insights QR codes and floor plan images."""

from __future__ import annotations

import io
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import segno
from homeassistant.components.image import ImageEntity
from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api.exceptions import UniFiError
from .console_identity import resolve_console_namespace
from .const import DOMAIN, MANUFACTURER
from .coordinators import UnifiFacadeCoordinator
from .coordinators.voucher_state import (
    _field,
    latest_voucher_qr_payload,
    parse_timestamp,
    voucher_site_ids,
)
from .entity import build_site_device_info
from .helpers import async_migrate_site_entity
from .innerspace_transforms import parse_floor_plan_asset_path, strip_url_query

if TYPE_CHECKING:
    import asyncio
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import UnifiInsightsConfigEntry

_LOGGER = logging.getLogger(__name__)

# Coordinator handles updates centrally
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up QR code and floor plan images for UniFi Insights."""
    coordinator = entry.runtime_data.coordinator
    known_wifi_keys: set[tuple[str, str]] = set()
    known_floor_plan_keys: set[str] = set()
    known_voucher_keys: set[str] = set()

    @callback
    def async_discover_images() -> None:
        """Discover and add new WiFi QR code and floor plan image entities."""
        if not coordinator.data or not isinstance(coordinator.data, dict):
            return

        new_entities: list[ImageEntity] = []

        # WiFi QR codes
        wifi_by_site = coordinator.data.get("wifi", {})
        if isinstance(wifi_by_site, dict):
            for site_id, wifi_networks in wifi_by_site.items():
                if not isinstance(wifi_networks, dict):
                    continue
                for wifi_id, wifi_data in wifi_networks.items():
                    if not isinstance(wifi_data, dict):
                        continue
                    # Only networks for which we resolved a connect string (i.e.
                    # the passphrase was available from the classic API) get a
                    # QR code.
                    if not wifi_data.get("qr_code"):
                        continue
                    key = (site_id, wifi_id)
                    if key in known_wifi_keys:
                        continue
                    known_wifi_keys.add(key)
                    wifi_name = wifi_data.get("name") or wifi_data.get("ssid", wifi_id)
                    _LOGGER.debug(
                        "Creating WiFi QR code image for %s (%s)", wifi_name, wifi_id
                    )
                    new_entities.append(
                        UnifiWifiQrCodeImage(
                            hass=hass,
                            coordinator=coordinator,
                            site_id=site_id,
                            wifi_id=wifi_id,
                        )
                    )

        # InnerSpace floor plans
        innerspace = coordinator.data.get("innerspace")
        if isinstance(innerspace, dict):
            floor_plans = innerspace.get("floor_plans")
            if isinstance(floor_plans, dict):
                for plan_id, plan_data in floor_plans.items():
                    if not isinstance(plan_id, str) or not isinstance(plan_data, dict):
                        continue
                    # Plans without an image (project-only, blank or geo plans)
                    # get no entity or device; they are picked up by a later
                    # coordinator update once they report an image_url.
                    if not plan_data.get("image_url"):
                        continue
                    if plan_id in known_floor_plan_keys:
                        continue
                    known_floor_plan_keys.add(plan_id)
                    plan_name = plan_data.get("name") or f"Floor Plan {plan_id}"
                    _LOGGER.debug(
                        "Creating InnerSpace floor plan image for %s (%s)",
                        plan_name,
                        plan_id,
                    )
                    new_entities.append(
                        UnifiFloorPlanImage(
                            hass=hass,
                            coordinator=coordinator,
                            plan_id=plan_id,
                        )
                    )

        # Hotspot voucher QR codes
        namespace = resolve_console_namespace(entry)
        for site_id in voucher_site_ids(coordinator.data):
            if site_id in known_voucher_keys:
                continue
            unique_id = async_migrate_site_entity(
                hass, entry, "image", namespace, site_id, suffix="voucher_qr_code"
            )
            if unique_id is None:
                continue
            entity = UnifiVoucherQrCodeImage(
                hass, coordinator, site_id, unique_id=unique_id
            )
            known_voucher_keys.add(site_id)
            new_entities.append(entity)

        if new_entities:
            _LOGGER.info("Adding %d UniFi image entities", len(new_entities))
            async_add_entities(new_entities)

    async_discover_images()
    entry.async_on_unload(coordinator.async_add_listener(async_discover_images))


class UnifiWifiQrCodeImage(CoordinatorEntity[UnifiFacadeCoordinator], ImageEntity):
    """A QR code image that joins a device to a WiFi network."""

    _attr_has_entity_name = True
    _attr_translation_key = "wifi_qr_code"
    _attr_content_type = "image/png"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: UnifiFacadeCoordinator,
        site_id: str,
        wifi_id: str,
    ) -> None:
        """Initialize the WiFi QR code image."""
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self.hass = hass
        self._site_id = site_id
        self._wifi_id = wifi_id

        wifi_data = self._get_wifi_data()
        wifi_name = wifi_data.get("name") or wifi_data.get("ssid", wifi_id)

        self._attr_unique_id = f"{site_id}_{wifi_id}_qr_code"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"wifi_{wifi_id}")},
            name=f"WiFi: {wifi_name}",
            manufacturer=MANUFACTURER,
            model="WiFi Network",
        )

        # Cache the rendered QR for a given payload so we only regenerate the
        # PNG when the SSID/passphrase actually changes.
        self._cached_payload: str | None = None
        self._cached_png: bytes | None = None
        self._attr_image_last_updated = dt_util.utcnow()

    def _get_wifi_data(self) -> dict[str, Any]:
        """Get WiFi data for this network from the coordinator."""
        result: dict[str, Any] = (
            self.coordinator.data.get("wifi", {})
            .get(self._site_id, {})
            .get(self._wifi_id, {})
        )
        return result

    def _current_payload(self) -> str | None:
        """Return the current WiFi connect (QR) payload, if available."""
        payload = self._get_wifi_data().get("qr_code")
        return payload if isinstance(payload, str) and payload else None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return bool(
            self.coordinator.wifi_available(self._site_id) and self._current_payload()
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh the image timestamp when the connect string changes."""
        payload = self._current_payload()
        if payload != self._cached_payload:
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        """Return the QR code as PNG bytes."""
        payload = self._current_payload()
        if payload is None:
            return None

        if payload == self._cached_payload and self._cached_png is not None:
            return self._cached_png

        buffer = io.BytesIO()
        segno.make(payload, error="m").save(buffer, kind="png", scale=6, border=2)
        self._cached_payload = payload
        self._cached_png = buffer.getvalue()
        return self._cached_png


class UnifiFloorPlanImage(CoordinatorEntity[UnifiFacadeCoordinator], ImageEntity):
    """An image entity representing a UniFi InnerSpace floor plan."""

    _attr_has_entity_name = True
    _attr_translation_key = "floor_plan"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: UnifiFacadeCoordinator,
        plan_id: str,
    ) -> None:
        """Initialize the floor plan image entity."""
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._plan_id = plan_id
        self._attr_unique_id = f"innerspace_floor_plan_{plan_id}"

        plan = self._get_floor_plan_data() or {}
        plan_name = plan.get("name") or f"Floor Plan {plan_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"innerspace_floor_plan_{plan_id}")},
            name=plan_name,
            manufacturer=MANUFACTURER,
            model="Floor Plan",
        )

        self._cached_image_url: str | None = None
        self._cached_bytes: bytes | None = None
        self._attr_content_type = "image/png"
        self._attr_image_last_updated = dt_util.utcnow()
        self._logged_fetch_error = False
        self._fetch_failed = False
        self._retry_task: asyncio.Task[None] | None = None

    def _get_floor_plan_data(self) -> dict[str, Any] | None:
        """Get floor plan data from coordinator."""
        if not self.coordinator.data or not isinstance(self.coordinator.data, dict):
            return None
        innerspace = self.coordinator.data.get("innerspace")
        if not isinstance(innerspace, dict):
            return None
        floor_plans = innerspace.get("floor_plans")
        if not isinstance(floor_plans, dict):
            return None
        plan = floor_plans.get(self._plan_id)
        return plan if isinstance(plan, dict) else None

    def _current_image_url(self) -> str | None:
        """Return current floor plan image_url."""
        plan = self._get_floor_plan_data()
        if not plan:
            return None
        url = plan.get("image_url")
        return str(url) if url else None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.coordinator.innerspace_available:
            return False
        if not self._current_image_url():
            return False
        if self._fetch_failed:
            return False
        return self._get_floor_plan_data() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes for floor plan."""
        plan = self._get_floor_plan_data() or {}
        attrs: dict[str, Any] = {
            "floor_plan_id": self._plan_id,
            "floor_number": plan.get("floor_number"),
            "width": plan.get("width"),
            "height": plan.get("height"),
            "ppm": plan.get("ppm"),
            "origin_x": plan.get("origin_x"),
            "origin_y": plan.get("origin_y"),
            "site_id": plan.get("site_id"),
            "image_url": strip_url_query(plan.get("image_url")),
        }
        return {k: v for k, v in attrs.items() if v is not None}

    async def async_will_remove_from_hass(self) -> None:
        """Cancel a pending background retry when the entity is removed."""
        self._cancel_retry()
        await super().async_will_remove_from_hass()

    def _cancel_retry(self) -> None:
        """Cancel the background retry task, if one is running."""
        if self._retry_task is not None:
            self._retry_task.cancel()
            self._retry_task = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        image_url = self._current_image_url()
        if image_url != self._cached_image_url:
            self._cancel_retry()
            self._cached_image_url = None
            self._cached_bytes = None
            self._fetch_failed = False
            self._logged_fetch_error = False
            self._attr_image_last_updated = dt_util.utcnow()
        elif image_url is not None and self._fetch_failed:
            # The last fetch for this URL failed. Stay unavailable until a
            # retry proves otherwise: clearing the flag here would flap the
            # written state between available and unavailable.
            self._schedule_retry(image_url)
        super()._handle_coordinator_update()

    @callback
    def _schedule_retry(self, image_url: str) -> None:
        """Re-fetch the image once in the background (one task at a time)."""
        if self._retry_task is not None and not self._retry_task.done():
            return
        self._retry_task = self.hass.async_create_background_task(
            self._async_retry_fetch(image_url),
            f"{DOMAIN} floor plan image retry {self._plan_id}",
            eager_start=False,
        )

    async def _async_retry_fetch(self, image_url: str) -> None:
        """Retry the failed fetch and write the resulting availability."""
        await self._async_fetch(image_url)
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        """Fetch and return the floor plan image binary."""
        image_url = self._current_image_url()
        if not image_url:
            return None

        if image_url == self._cached_image_url and self._cached_bytes is not None:
            return self._cached_bytes

        if self._fetch_failed and image_url == self._cached_image_url:
            return None

        image_bytes = await self._async_fetch(image_url)
        if image_bytes is None:
            # Publish the unavailability instead of leaving it to the next
            # coordinator update.
            self.async_write_ha_state()
        return image_bytes

    def _mark_fetch_failed(self, image_url: str) -> None:
        """Remember that fetching image_url failed (nothing is cached for it)."""
        self._cached_image_url = image_url
        self._cached_bytes = None
        self._fetch_failed = True

    async def _async_fetch(self, image_url: str) -> bytes | None:
        """Fetch image_url, updating the cache and failure flags."""
        # Never log the raw URL: its query string may carry a token.
        safe_url = strip_url_query(image_url)
        parsed = parse_floor_plan_asset_path(image_url)
        if not parsed:
            if not self._logged_fetch_error:
                _LOGGER.debug(
                    "Invalid floor plan image URL for %s: %s",
                    self._plan_id,
                    safe_url,
                )
                self._logged_fetch_error = True
            self._mark_fetch_failed(image_url)
            return None

        plan_id, filename = parsed

        try:
            result = await self.coordinator.async_get_floor_plan_image(
                plan_id, filename
            )
        except UniFiError as err:
            if not self._logged_fetch_error:
                _LOGGER.debug(
                    "Failed to fetch floor plan image for %s (%s): %s",
                    self._plan_id,
                    safe_url,
                    err,
                )
                self._logged_fetch_error = True
            self._mark_fetch_failed(image_url)
            return None

        if result is None:
            if not self._logged_fetch_error:
                _LOGGER.debug(
                    "Floor plan image not found for %s (%s)",
                    self._plan_id,
                    safe_url,
                )
                self._logged_fetch_error = True
            self._mark_fetch_failed(image_url)
            return None

        image_bytes, content_type = result
        self._cached_image_url = image_url
        self._cached_bytes = image_bytes
        if content_type:
            self._attr_content_type = content_type
        self._fetch_failed = False
        self._logged_fetch_error = False
        return self._cached_bytes


class UnifiVoucherQrCodeImage(CoordinatorEntity[UnifiFacadeCoordinator], ImageEntity):
    """
    QR code of the latest generated hotspot voucher code.

    This QR code contains only the plain voucher code string for portal entry.
    It does NOT configure Wi-Fi credentials or join a network.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "voucher_qr_code"
    _attr_content_type = "image/png"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: UnifiFacadeCoordinator,
        site_id: str,
        *,
        unique_id: str,
    ) -> None:
        """Initialize the voucher QR code image."""
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self.hass = hass
        self._site_id = site_id
        self._attr_unique_id = unique_id
        self._attr_device_info = DeviceInfo(
            **build_site_device_info(coordinator.data, site_id)  # type: ignore[typeddict-item]
        )
        self._last_observed_payload: str | None = self._current_payload()
        self._rendered_payload: str | None = None
        self._rendered_png: bytes | None = None
        self._attr_image_last_updated = dt_util.utcnow()
        self._expiration_unsub: CALLBACK_TYPE | None = None

    def _cancel_expiration_timer(self) -> None:
        """Cancel any scheduled expiration callback."""
        if self._expiration_unsub is not None:
            self._expiration_unsub()
            self._expiration_unsub = None

    def _reschedule_expiration_timer(self) -> None:
        """Schedule a timer to expire the QR image at the voucher deadline."""
        self._cancel_expiration_timer()
        if not getattr(self, "hass", None):
            return
        latest_vouchers = self.coordinator.data.get("latest_vouchers")
        if not isinstance(latest_vouchers, Mapping):
            return
        record = latest_vouchers.get(self._site_id)
        if not isinstance(record, Mapping):
            return
        if record.get("expired") is True:
            return
        expires_at_raw = _field(record, "expiresAt", "expires_at")
        if expires_at_raw is None:
            return
        expires_at = parse_timestamp(expires_at_raw)
        if expires_at is None:
            return
        now = dt_util.utcnow()
        if expires_at <= now:
            return
        self._expiration_unsub = async_track_point_in_time(
            self.hass, self._handle_expiration, expires_at
        )

    @callback
    def _handle_expiration(self, _now: datetime) -> None:
        """Handle expiration deadline reaching current time."""
        self._expiration_unsub = None
        self._rendered_payload = None
        self._rendered_png = None
        if self._last_observed_payload is not None:
            self._last_observed_payload = None
            self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register lifecycle and timer on add."""
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_expiration_timer)
        self._reschedule_expiration_timer()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel timer on removal."""
        self._cancel_expiration_timer()
        await super().async_will_remove_from_hass()

    def _current_payload(self) -> str | None:
        """Return the QR payload for the latest voucher if active and unexpired."""
        latest_vouchers = self.coordinator.data.get("latest_vouchers")
        if not isinstance(latest_vouchers, Mapping):
            return None
        return latest_voucher_qr_payload(latest_vouchers.get(self._site_id))

    @property
    def available(self) -> bool:
        """Return True if vouchers are available and a valid QR payload exists."""
        return bool(
            self.coordinator.vouchers_available(self._site_id)
            and self._current_payload()
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh timestamp when payload changes; invalidate cache on disappearance."""
        payload = self._current_payload()
        if payload != self._last_observed_payload:
            self._attr_image_last_updated = dt_util.utcnow()
            self._last_observed_payload = payload
            self._rendered_payload = None
            self._rendered_png = None
        if payload is None:
            self._rendered_payload = None
            self._rendered_png = None
        self._reschedule_expiration_timer()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        """Return the QR code as PNG bytes."""
        payload = self._current_payload()
        if payload is None:
            return None

        if payload == self._rendered_payload and self._rendered_png is not None:
            return self._rendered_png

        buffer = io.BytesIO()
        segno.make(payload, error="m").save(buffer, kind="png", scale=6, border=2)
        self._rendered_payload = payload
        self._rendered_png = buffer.getvalue()
        return self._rendered_png
