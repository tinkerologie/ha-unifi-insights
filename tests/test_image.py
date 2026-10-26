# Copyright 2026 UniFi Insights contributors
"""Tests for the UniFi Insights WiFi QR code image platform."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import EVENT_STATE_CHANGED, STATE_UNAVAILABLE
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
    async_fire_time_changed,
)
from unifi_official_api.exceptions import (
    UniFiConnectionError,
    UniFiNotFoundError,
)

from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.image import (
    UnifiFloorPlanImage,
    UnifiVoucherQrCodeImage,
    UnifiWifiQrCodeImage,
    async_setup_entry,
)
from custom_components.unifi_insights.innerspace_transforms import strip_url_query
from tests.fixtures.assertions import (
    patch_attribute,
    require_instance,
    require_not_none,
)
from tests.fixtures.entities import EntityCallback, EntityCollector
from tests.fixtures.facade_data import (
    frozen,
    set_availability,
    with_rule,
    with_sections,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import Event, EventStateChangedData, HomeAssistant

    from custom_components.unifi_insights.coordinators.facade import FacadeData

QR_PAYLOAD: str = "WIFI:T:WPA;S:TestNet;P:secret123;;"


@pytest.fixture
def mock_coordinator() -> MagicMock:
    """Create mock coordinator with WiFi data."""
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {
        "wifi": {
            "site1": {
                "wifi1": {
                    "id": "wifi1",
                    "name": "TestNet",
                    "ssid": "TestNet",
                    "qr_code": QR_PAYLOAD,
                },
                "wifi2": {
                    "id": "wifi2",
                    "name": "NoSecretNet",
                    # No qr_code: passphrase unavailable from classic API
                },
            }
        },
    }
    return coordinator


def _with_wifi1(data: Mapping[str, Any], record: Mapping[str, Any]) -> FacadeData:
    """Return ``data`` with site1's ``wifi1`` network replaced."""
    return with_rule(data, "wifi", "site1", "wifi1", record)


def _without_qr_code(data: Mapping[str, Any]) -> FacadeData:
    """Return ``data`` with site1's ``wifi1`` network lacking a QR payload."""
    record = data["wifi"]["site1"]["wifi1"]
    return _with_wifi1(data, {k: v for k, v in record.items() if k != "qr_code"})


def _with_qr_code(data: Mapping[str, Any], payload: str) -> FacadeData:
    """Return ``data`` with site1's ``wifi1`` network carrying ``payload``."""
    return _with_wifi1(data, {**data["wifi"]["site1"]["wifi1"], "qr_code": payload})


def _with_floor_plans(data: Mapping[str, Any], floor_plans: Any) -> FacadeData:
    """Return ``data`` with the InnerSpace floor plans replaced."""
    return with_sections(
        data, innerspace=frozen({**data["innerspace"], "floor_plans": floor_plans})
    )


def _with_floor_plan_fields(
    data: Mapping[str, Any], plan_id: str, **fields: Any
) -> FacadeData:
    """Return ``data`` with fields of one InnerSpace floor plan replaced."""
    plans = data["innerspace"]["floor_plans"]
    return _with_floor_plans(data, {**plans, plan_id: {**plans[plan_id], **fields}})


def _without_floor_plan(data: Mapping[str, Any], plan_id: str) -> FacadeData:
    """Return ``data`` without one InnerSpace floor plan."""
    plans = data["innerspace"]["floor_plans"]
    return _with_floor_plans(
        data, {key: plan for key, plan in plans.items() if key != plan_id}
    )


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    @pytest.mark.asyncio
    async def test_setup_creates_qr_entities_only_with_payload(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Only networks with a resolved QR payload get an image entity."""
        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()
        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert entities[0].unique_id == "site1_wifi1_qr_code"

    @pytest.mark.asyncio
    async def test_setup_no_entities_without_payloads(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """No entities are added when no network has a QR payload."""
        mock_coordinator.data = _without_qr_code(mock_coordinator.data)

        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()
        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_not_called()

    @pytest.mark.asyncio
    async def test_setup_returns_early_when_wifi_not_a_dict(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """No entities are added and no exception raised when wifi data is malformed."""
        mock_coordinator.data = with_sections(mock_coordinator.data, wifi="not-a-dict")

        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        async_add_entities = MagicMock()
        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_not_called()


class TestUnifiWifiQrCodeImage:
    """Tests for the UnifiWifiQrCodeImage entity."""

    def _make_entity(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> UnifiWifiQrCodeImage:
        return UnifiWifiQrCodeImage(
            hass=hass,
            coordinator=mock_coordinator,
            site_id="site1",
            wifi_id="wifi1",
        )

    def test_initialization(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test unique ID, translation key, and device info."""
        entity = self._make_entity(hass, mock_coordinator)

        assert entity.unique_id == "site1_wifi1_qr_code"
        assert entity.translation_key == "wifi_qr_code"
        assert entity.has_entity_name is True
        assert getattr(entity, "_attr_name", None) is None
        assert entity._attr_content_type == "image/png"
        assert require_not_none(entity._attr_device_info)["name"] == "WiFi: TestNet"

    def test_available_with_payload(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Entity is available while a payload exists and updates succeed."""
        entity = self._make_entity(hass, mock_coordinator)
        assert entity.available is True

    def test_unavailable_without_payload(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Entity becomes unavailable when the payload disappears."""
        entity = self._make_entity(hass, mock_coordinator)
        mock_coordinator.data = _without_qr_code(mock_coordinator.data)
        assert entity.available is False

    def test_unavailable_on_failed_update(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Entity is unavailable when the coordinator update failed."""
        entity = self._make_entity(hass, mock_coordinator)
        set_availability(mock_coordinator, failed_sections={("wifi", "site1")})
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_image_returns_png(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """async_image renders a PNG for the current payload."""
        entity = self._make_entity(hass, mock_coordinator)

        image = await entity.async_image()

        assert image is not None
        assert image.startswith(b"\x89PNG")

    @pytest.mark.asyncio
    async def test_async_image_cached_until_payload_changes(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """The rendered PNG is cached for an unchanged payload."""
        entity = self._make_entity(hass, mock_coordinator)

        first = await entity.async_image()
        second = await entity.async_image()
        assert first is second

        mock_coordinator.data = _with_qr_code(
            mock_coordinator.data, "WIFI:T:WPA;S:TestNet;P:newsecret;;"
        )
        third = await entity.async_image()
        assert third is not None
        assert third != first

    @pytest.mark.asyncio
    async def test_async_image_none_without_payload(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """async_image returns None when no payload is available."""
        entity = self._make_entity(hass, mock_coordinator)
        mock_coordinator.data = _without_qr_code(mock_coordinator.data)

        assert await entity.async_image() is None

    def test_coordinator_update_refreshes_timestamp_on_change(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """A payload change bumps image_last_updated; no change keeps it."""
        entity = self._make_entity(hass, mock_coordinator)

        with patch.object(entity, "async_write_ha_state"):
            initial = entity._attr_image_last_updated

            # Payload changed since the cached render → timestamp bumps
            mock_coordinator.data = _with_qr_code(
                mock_coordinator.data, "WIFI:T:WPA;S:TestNet;P:rotated;;"
            )
            entity._handle_coordinator_update()
            changed = entity._attr_image_last_updated
            assert changed is not None
            assert initial is not None
            assert changed >= initial

            # No payload change since the last render → timestamp is kept
            entity._cached_payload = mock_coordinator.data["wifi"]["site1"]["wifi1"][
                "qr_code"
            ]
            entity._handle_coordinator_update()
            assert entity._attr_image_last_updated == changed


def _record_state_changes(hass: HomeAssistant) -> list[str]:
    """Record every state Home Assistant writes from now on, in order."""
    states: list[str] = []

    @callback
    def _record(event: Event[EventStateChangedData]) -> None:
        state = event.data["new_state"]
        if state is not None:
            states.append(state.state)

    hass.bus.async_listen(EVENT_STATE_CHANGED, _record)
    return states


class TestUnifiFloorPlanImage:
    """Tests for the UnifiFloorPlanImage entity."""

    @pytest.fixture(autouse=True)
    def state_writer_patcher(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._state_patcher = monkeypatch

    @pytest.fixture
    def innerspace_coordinator(self) -> MagicMock:
        """Create mock facade coordinator with InnerSpace floor plan data."""
        coordinator = MagicMock()
        coordinator.data = {
            "wifi": {},
            "innerspace": {
                "floor_plans": {
                    "fp-1": {
                        "id": "fp-1",
                        "name": "Ground Floor",
                        "floor_number": 0,
                        "width": 1200,
                        "height": 800,
                        "ppm": 20.0,
                        "origin_x": 0.0,
                        "origin_y": 0.0,
                        "site_id": "site-main",
                        "image_url": (
                            "/proxy/innerspace/integration/v1/assets/fp-1/ground.png"
                        ),
                    },
                    "fp-2": {
                        "id": "fp-2",
                        "name": "Second Floor",
                        "floor_number": 1,
                        "width": 1000,
                        "height": 600,
                        "ppm": 15.0,
                        "origin_x": 10.0,
                        "origin_y": 20.0,
                        "site_id": "site-main",
                        "image_url": None,
                    },
                }
            },
        }
        return coordinator

    def _make_entity(
        self, hass: HomeAssistant, coordinator: MagicMock, plan_id: str = "fp-1"
    ) -> UnifiFloorPlanImage:
        entity = UnifiFloorPlanImage(
            hass=hass,
            coordinator=coordinator,
            plan_id=plan_id,
        )
        # Not added to hass here: stub the state writer. Tests that assert the
        # written state use `_add_entity` instead.
        entity.hass = hass
        patch_attribute(
            self._state_patcher, entity, "async_write_ha_state", MagicMock()
        )
        return entity

    async def _add_entity(
        self, hass: HomeAssistant, coordinator: MagicMock, plan_id: str = "fp-1"
    ) -> UnifiFloorPlanImage:
        """Add a real entity to hass so its written state can be inspected."""
        entity = UnifiFloorPlanImage(
            hass=hass,
            coordinator=coordinator,
            plan_id=plan_id,
        )
        entry = MockConfigEntry(domain=DOMAIN)
        entry.add_to_hass(hass)
        platform = MockEntityPlatform(hass, domain="image", platform_name=DOMAIN)
        platform.config_entry = entry
        await platform.async_add_entities([entity])
        return entity

    def test_initialization_and_attributes(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """Test floor plan image initialization, naming, device info, and attributes."""
        entity = self._make_entity(hass, innerspace_coordinator, "fp-1")

        assert entity.unique_id == "innerspace_floor_plan_fp-1"
        assert entity.has_entity_name is True
        assert entity.translation_key == "floor_plan"
        assert entity.content_type == "image/png"
        assert require_not_none(entity.device_info)["name"] == "Ground Floor"
        assert require_not_none(entity.device_info)["identifiers"] == {
            (DOMAIN, "innerspace_floor_plan_fp-1")
        }
        assert "model" in require_not_none(entity.device_info)
        assert dict(require_not_none(entity.device_info))["model"] == "Floor Plan"

        attrs = entity.extra_state_attributes
        assert attrs["floor_plan_id"] == "fp-1"
        assert attrs["floor_number"] == 0
        assert attrs["width"] == 1200
        assert attrs["height"] == 800
        assert attrs["ppm"] == 20.0
        assert attrs["origin_x"] == 0.0
        assert attrs["origin_y"] == 0.0
        assert attrs["site_id"] == "site-main"
        assert (
            attrs["image_url"]
            == "/proxy/innerspace/integration/v1/assets/fp-1/ground.png"
        )

    def test_available_states(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """Verify available flag across various conditions."""
        entity = self._make_entity(hass, innerspace_coordinator, "fp-1")
        assert entity.available is True

        # When innerspace is unavailable
        set_availability(innerspace_coordinator, innerspace=False)
        assert entity.available is False

        set_availability(innerspace_coordinator, innerspace=True)
        assert entity.available is True

        # When image_url is None
        entity_no_url = self._make_entity(hass, innerspace_coordinator, "fp-2")
        assert entity_no_url.available is False

        # When floor plan is removed from coordinator data
        innerspace_coordinator.data = _without_floor_plan(
            innerspace_coordinator.data, "fp-1"
        )
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_image_success_and_caching(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """Test fetching image binary, setting content_type, and caching."""
        entity = self._make_entity(hass, innerspace_coordinator, "fp-1")
        innerspace_coordinator.async_get_floor_plan_image = AsyncMock(
            return_value=(b"fake_jpeg_bytes", "image/jpeg")
        )

        img = await entity.async_image()
        assert img == b"fake_jpeg_bytes"
        assert entity.content_type == "image/jpeg"
        innerspace_coordinator.async_get_floor_plan_image.assert_called_once_with(
            "fp-1", "ground.png"
        )

        # Second call returns cached bytes without another call
        innerspace_coordinator.async_get_floor_plan_image.reset_mock()
        second = await entity.async_image()
        assert second == b"fake_jpeg_bytes"
        innerspace_coordinator.async_get_floor_plan_image.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_image_none_without_image_url(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """Test async_image returns None when image_url is None without calling API."""
        entity = self._make_entity(hass, innerspace_coordinator, "fp-2")
        innerspace_coordinator.async_get_floor_plan_image = AsyncMock()

        assert await entity.async_image() is None
        innerspace_coordinator.async_get_floor_plan_image.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_image_error_handling_and_unavailability(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """404 or API errors do not raise out of async_image and mark unavailable."""
        entity = self._make_entity(hass, innerspace_coordinator, "fp-1")
        innerspace_coordinator.async_get_floor_plan_image = AsyncMock(
            side_effect=UniFiNotFoundError("Not found", status_code=404)
        )

        assert await entity.async_image() is None
        # Entity is marked unavailable after 404
        assert entity.available is False

        # Repeated async_image calls while failed return None via failure cache
        assert await entity.async_image() is None
        innerspace_coordinator.async_get_floor_plan_image.assert_awaited_once()

        # Subsequent fetch failure when already logged skips repeat logging
        entity._fetch_failed = False
        entity._cached_image_url = None
        assert await entity.async_image() is None

    @pytest.mark.asyncio
    async def test_async_image_none_result_handles_cleanly(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """When async_get_floor_plan_image returns None, async_image returns None."""
        entity = self._make_entity(hass, innerspace_coordinator, "fp-1")
        innerspace_coordinator.async_get_floor_plan_image = AsyncMock(return_value=None)

        assert await entity.async_image() is None
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_image_none_result_across_updates_logs_once(
        self,
        hass: HomeAssistant,
        innerspace_coordinator: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Coordinator returns None across updates and logs debug once."""
        entity = self._make_entity(hass, innerspace_coordinator, "fp-1")
        innerspace_coordinator.async_get_floor_plan_image = AsyncMock(return_value=None)

        with caplog.at_level(logging.DEBUG):
            assert await entity.async_image() is None
            assert entity.available is False

            entity._handle_coordinator_update()
            await require_not_none(entity._retry_task)
            assert entity.available is False

            # The retry already re-fetched: a view request does not fetch again
            assert await entity.async_image() is None
            assert entity.available is False

        assert innerspace_coordinator.async_get_floor_plan_image.call_count == 2
        not_found_logs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG
            and "Floor plan image not found for fp-1" in r.message
        ]
        assert len(not_found_logs) == 1

    @pytest.mark.asyncio
    async def test_transient_failure_recovers_via_background_retry(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """Failed fetch -> written unavailable; update -> retry -> written available."""
        entity = await self._add_entity(hass, innerspace_coordinator, "fp-1")
        entity_id = entity.entity_id
        assert require_not_none(hass.states.get(entity_id)).state != STATE_UNAVAILABLE

        written = _record_state_changes(hass)

        innerspace_coordinator.async_get_floor_plan_image = AsyncMock(
            side_effect=UniFiNotFoundError("Not found", status_code=404)
        )
        assert await entity.async_image() is None
        # The failed fetch itself is written; no coordinator update needed
        assert require_not_none(hass.states.get(entity_id)).state == STATE_UNAVAILABLE

        innerspace_coordinator.async_get_floor_plan_image = AsyncMock(
            return_value=(b"recovered_png_bytes", "image/jpeg")
        )
        entity._handle_coordinator_update()
        # The update must not clear the failure before the retry has a result
        assert require_not_none(hass.states.get(entity_id)).state == STATE_UNAVAILABLE

        await require_not_none(entity._retry_task)
        assert require_not_none(hass.states.get(entity_id)).state != STATE_UNAVAILABLE
        assert written[0] == STATE_UNAVAILABLE
        assert STATE_UNAVAILABLE not in written[1:]
        assert entity.available is True
        assert entity.content_type == "image/jpeg"

        # Served from the retry's cache, no further fetch
        assert await entity.async_image() == b"recovered_png_bytes"
        innerspace_coordinator.async_get_floor_plan_image.assert_awaited_once_with(
            "fp-1", "ground.png"
        )

    @pytest.mark.asyncio
    async def test_image_url_query_not_exposed_in_attributes_or_logs(
        self,
        hass: HomeAssistant,
        innerspace_coordinator: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A token in the image URL query or fragment never reaches state or logs."""
        base = "/proxy/innerspace/integration/v1/assets/fp-1/ground.png"
        innerspace_coordinator.data = _with_floor_plan_fields(
            innerspace_coordinator.data,
            "fp-1",
            image_url=f"{base}?token=SECRET123&x=1#frag-secret",
        )
        entity = self._make_entity(hass, innerspace_coordinator, "fp-1")
        innerspace_coordinator.async_get_floor_plan_image = AsyncMock(
            side_effect=UniFiNotFoundError("Not found", status_code=404)
        )

        with caplog.at_level(logging.DEBUG):
            assert await entity.async_image() is None

        assert entity.extra_state_attributes["image_url"] == base
        assert "SECRET123" not in caplog.text
        assert "frag-secret" not in caplog.text

        innerspace_coordinator.data = _with_floor_plan_fields(
            innerspace_coordinator.data,
            "fp-1",
            image_url="/proxy/innerspace/no-asset-path?token=SECRET456",
        )
        entity = self._make_entity(hass, innerspace_coordinator, "fp-1")
        with caplog.at_level(logging.DEBUG):
            assert await entity.async_image() is None
        assert "Invalid floor plan image URL" in caplog.text
        assert "SECRET456" not in caplog.text

    def test_strip_url_query_edge_cases(self) -> None:
        """Empty input yields None; userinfo, query and fragment are dropped."""
        assert strip_url_query(None) is None
        assert strip_url_query("") is None
        assert (
            strip_url_query("https://u:p@host/a/b.png?t=1#f") == "https://host/a/b.png"
        )

    @pytest.mark.asyncio
    async def test_unexpected_exception_is_not_swallowed(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """Only UniFi client errors mean an unavailable image; bugs propagate."""
        entity = self._make_entity(hass, innerspace_coordinator, "fp-1")
        innerspace_coordinator.async_get_floor_plan_image = AsyncMock(
            side_effect=TypeError("bug")
        )

        with pytest.raises(TypeError):
            await entity.async_image()

    @pytest.mark.asyncio
    async def test_client_error_marks_unavailable(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """A non-404 client error (connection failure) is handled like a 404."""
        entity = self._make_entity(hass, innerspace_coordinator, "fp-1")
        innerspace_coordinator.async_get_floor_plan_image = AsyncMock(
            side_effect=UniFiConnectionError("down")
        )

        assert await entity.async_image() is None
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_persistent_404_across_updates_logs_once(
        self,
        hass: HomeAssistant,
        innerspace_coordinator: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Persistent 404 stays written-unavailable over 3 updates, logging once."""
        entity = await self._add_entity(hass, innerspace_coordinator, "fp-1")
        entity_id = entity.entity_id
        innerspace_coordinator.async_get_floor_plan_image = AsyncMock(
            side_effect=UniFiNotFoundError("Not found", status_code=404)
        )
        written = _record_state_changes(hass)

        with caplog.at_level(logging.DEBUG):
            assert await entity.async_image() is None
            assert (
                require_not_none(hass.states.get(entity_id)).state == STATE_UNAVAILABLE
            )

            for _ in range(3):
                entity._handle_coordinator_update()
                await require_not_none(entity._retry_task)
                assert (
                    require_not_none(hass.states.get(entity_id)).state
                    == STATE_UNAVAILABLE
                )
                # Views do not hit the controller while the failure stands
                assert await entity.async_image() is None

        # The state never went back to available in between
        assert written == [STATE_UNAVAILABLE]
        # One view fetch plus one background retry per coordinator update
        assert innerspace_coordinator.async_get_floor_plan_image.await_count == 4
        error_logs = [
            record
            for record in caplog.records
            if record.levelno == logging.DEBUG
            and "Failed to fetch floor plan image for fp-1" in record.message
        ]
        assert len(error_logs) == 1

    @pytest.mark.asyncio
    async def test_retry_runs_once_at_a_time_and_is_cancelled_on_url_change(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """Only one retry is in flight; a new URL or removal cancels it."""
        entity = await self._add_entity(hass, innerspace_coordinator, "fp-1")
        gate = asyncio.Event()

        async def slow_fetch(plan_id: str, filename: str) -> None:
            await gate.wait()

        innerspace_coordinator.async_get_floor_plan_image = AsyncMock(
            side_effect=UniFiNotFoundError("Not found", status_code=404)
        )
        assert await entity.async_image() is None

        innerspace_coordinator.async_get_floor_plan_image = AsyncMock(
            side_effect=slow_fetch
        )
        entity._handle_coordinator_update()
        first = entity._retry_task
        entity._handle_coordinator_update()
        assert entity._retry_task is first
        await asyncio.sleep(0)
        assert innerspace_coordinator.async_get_floor_plan_image.await_count == 1

        # A new image URL abandons the retry and starts clean
        innerspace_coordinator.data = _with_floor_plan_fields(
            innerspace_coordinator.data,
            "fp-1",
            image_url="/proxy/innerspace/integration/v1/assets/fp-1/v2.png",
        )
        entity._handle_coordinator_update()
        await asyncio.sleep(0)
        assert require_not_none(first).cancelled()
        assert entity._retry_task is None
        assert entity._fetch_failed is False

        # Removing the entity cancels a retry that is still running
        innerspace_coordinator.async_get_floor_plan_image = AsyncMock(
            side_effect=UniFiNotFoundError("Not found", status_code=404)
        )
        assert await entity.async_image() is None
        innerspace_coordinator.async_get_floor_plan_image = AsyncMock(
            side_effect=slow_fetch
        )
        entity._handle_coordinator_update()
        pending = entity._retry_task
        await asyncio.sleep(0)
        await entity.async_remove()
        await asyncio.sleep(0)
        assert require_instance(pending, asyncio.Task).cancelled()
        assert entity._retry_task is None

    @pytest.mark.asyncio
    async def test_async_image_invalid_url_handling_and_unavailability(
        self,
        hass: HomeAssistant,
        innerspace_coordinator: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Invalid image URL marks entity unavailable and logs debug once."""
        innerspace_coordinator.data = _with_floor_plan_fields(
            innerspace_coordinator.data,
            "fp-1",
            image_url=(
                "/proxy/innerspace/integration/v1/invalid_prefix/fp-1/ground.png"
            ),
        )
        entity = self._make_entity(hass, innerspace_coordinator, "fp-1")
        innerspace_coordinator.async_get_floor_plan_image = AsyncMock()

        with caplog.at_level(logging.DEBUG):
            assert await entity.async_image() is None
            assert entity.available is False

            # Repeated call does not log again
            assert await entity.async_image() is None

            # Coordinator update retries in the background: re-parses, skips log
            entity._handle_coordinator_update()
            await require_not_none(entity._retry_task)

            assert await entity.async_image() is None
            assert entity.available is False

        innerspace_coordinator.async_get_floor_plan_image.assert_not_called()
        invalid_logs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG
            and "Invalid floor plan image URL for fp-1" in r.message
        ]
        assert len(invalid_logs) == 1

    @pytest.mark.asyncio
    async def test_async_image_uses_parsed_plan_id(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """async_image uses plan_id parsed from image_url instead of entity _plan_id."""
        innerspace_coordinator.data = _with_floor_plan_fields(
            innerspace_coordinator.data,
            "fp-1",
            image_url=(
                "/proxy/innerspace/integration/v1/assets/shared-plan-42/ground.png"
            ),
        )
        entity = self._make_entity(hass, innerspace_coordinator, "fp-1")
        innerspace_coordinator.async_get_floor_plan_image = AsyncMock(
            return_value=(b"shared_bytes", "image/png")
        )

        img = await entity.async_image()
        assert img == b"shared_bytes"
        innerspace_coordinator.async_get_floor_plan_image.assert_called_once_with(
            "shared-plan-42", "ground.png"
        )

    def test_coordinator_update_resets_cache_on_new_image_url(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """A new image_url drops the cache and bumps the image timestamp."""
        entity = self._make_entity(hass, innerspace_coordinator, "fp-1")
        initial_ts = entity._attr_image_last_updated
        entity._cached_image_url = (
            "/proxy/innerspace/integration/v1/assets/fp-1/ground.png"
        )
        entity._cached_bytes = b"cached"

        # Update floor plan in coordinator data with new name and new image_url
        innerspace_coordinator.data = _with_floor_plan_fields(
            innerspace_coordinator.data,
            "fp-1",
            name="Renamed Floor",
            image_url="/proxy/innerspace/integration/v1/assets/fp-1/v2_ground.png",
        )

        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()

        assert entity._cached_bytes is None
        assert entity._cached_image_url is None
        assert require_not_none(entity._attr_image_last_updated) >= require_not_none(
            initial_ts
        )

        # Subsequent update without url change does not bump timestamp
        entity._cached_image_url = (
            "/proxy/innerspace/integration/v1/assets/fp-1/v2_ground.png"
        )
        ts_before = entity._attr_image_last_updated
        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        assert entity._attr_image_last_updated == ts_before

        # Update when plan has empty name or is missing
        innerspace_coordinator.data = _with_floor_plan_fields(
            innerspace_coordinator.data, "fp-1", name=""
        )
        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        innerspace_coordinator.data = _without_floor_plan(
            innerspace_coordinator.data, "fp-1"
        )
        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()

    @pytest.mark.asyncio
    async def test_coordinator_update_with_cached_bytes_retains_cache(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """Coordinator update preserves cache when image URL is unchanged."""
        entity = self._make_entity(hass, innerspace_coordinator, "fp-1")
        innerspace_coordinator.async_get_floor_plan_image = AsyncMock(
            return_value=(b"cached_png_bytes", "image/png")
        )

        img = await entity.async_image()
        assert img == b"cached_png_bytes"
        assert entity._cached_bytes == b"cached_png_bytes"
        assert entity._fetch_failed is False
        innerspace_coordinator.async_get_floor_plan_image.assert_called_once()

        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()

        assert entity._cached_bytes == b"cached_png_bytes"
        assert entity._fetch_failed is False

        innerspace_coordinator.async_get_floor_plan_image.reset_mock()
        img2 = await entity.async_image()
        assert img2 == b"cached_png_bytes"
        innerspace_coordinator.async_get_floor_plan_image.assert_not_called()

    @pytest.mark.asyncio
    async def test_setup_entry_discovers_floor_plans(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """async_setup_entry creates both WiFi QR and floor plan image entities."""
        innerspace_coordinator.data = with_sections(
            innerspace_coordinator.data,
            wifi={
                "s1": {"w1": {"id": "w1", "name": "WiFi Net", "qr_code": "WIFI:..."}}
            },
        )
        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = innerspace_coordinator

        async_add_entities = MagicMock()
        await async_setup_entry(hass, mock_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        # 1 WiFi QR entity + 1 floor plan entity: fp-2 has no image yet
        assert len(entities) == 2
        ids = {e.unique_id for e in entities}
        assert "s1_w1_qr_code" in ids
        assert "innerspace_floor_plan_fp-1" in ids
        assert "innerspace_floor_plan_fp-2" not in ids

    @pytest.mark.asyncio
    async def test_setup_entry_skips_plan_without_image_until_it_has_one(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """A plan without image_url gets no entity or device until it has one."""
        entry = MockConfigEntry(domain=DOMAIN)
        entry.add_to_hass(hass)
        platform = MockEntityPlatform(hass, domain="image", platform_name=DOMAIN)
        platform.config_entry = entry
        added: list[list] = []

        def add_entities(entities: list) -> None:
            added.append(list(entities))
            hass.async_create_task(platform.async_add_entities(entities))

        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data.coordinator = innerspace_coordinator
        await async_setup_entry(hass, mock_entry, EntityCallback(add_entities))
        await hass.async_block_till_done()

        def device_identifiers() -> set[tuple[str, str]]:
            devices = dr.async_entries_for_config_entry(
                dr.async_get(hass), entry.entry_id
            )
            return {ident for device in devices for ident in device.identifiers}

        fp1_device = (DOMAIN, "innerspace_floor_plan_fp-1")
        fp2_device = (DOMAIN, "innerspace_floor_plan_fp-2")
        assert fp1_device in device_identifiers()
        assert fp2_device not in device_identifiers()
        assert [e.unique_id for e in added[0]] == ["innerspace_floor_plan_fp-1"]

        # A coordinator update without a new image changes nothing
        listener = innerspace_coordinator.async_add_listener.call_args[0][0]
        listener()
        assert len(added) == 1

        # Once the plan reports an image, the next update creates it, once
        innerspace_coordinator.data = _with_floor_plan_fields(
            innerspace_coordinator.data,
            "fp-2",
            image_url="/proxy/innerspace/integration/v1/assets/fp-2/second.png",
        )
        listener()
        await hass.async_block_till_done()
        listener()
        assert [[e.unique_id for e in batch] for batch in added] == [
            ["innerspace_floor_plan_fp-1"],
            ["innerspace_floor_plan_fp-2"],
        ]
        assert fp2_device in device_identifiers()

    def test_floor_plan_data_fallback_conditions(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """Test _get_floor_plan_data fallbacks when data is missing or malformed."""
        entity = self._make_entity(hass, innerspace_coordinator, "fp-1")
        assert entity._get_floor_plan_data() is not None

        innerspace_coordinator.data = None
        assert entity._get_floor_plan_data() is None

        innerspace_coordinator.data = {"innerspace": None}
        assert entity._get_floor_plan_data() is None

        innerspace_coordinator.data = {"innerspace": {"floor_plans": None}}
        assert entity._get_floor_plan_data() is None

    @pytest.mark.asyncio
    async def test_async_image_invalid_url_filename(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """Test async_image returns None when image_url has no filename."""
        entity = self._make_entity(hass, innerspace_coordinator, "fp-1")
        innerspace_coordinator.data = _with_floor_plan_fields(
            innerspace_coordinator.data, "fp-1", image_url="http://host/"
        )
        assert await entity.async_image() is None

    @pytest.mark.asyncio
    async def test_async_image_empty_content_type(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """Test async_image retains default type when returned type is empty."""
        entity = self._make_entity(hass, innerspace_coordinator, "fp-1")
        innerspace_coordinator.async_get_floor_plan_image = AsyncMock(
            return_value=(b"raw_bytes", "")
        )
        data = await entity.async_image()
        assert data == b"raw_bytes"
        assert entity.content_type == "image/png"

    @pytest.mark.asyncio
    async def test_setup_entry_skips_malformed_and_duplicate_floor_plans(
        self, hass: HomeAssistant, innerspace_coordinator: MagicMock
    ) -> None:
        """Verify discovery skips invalid entries and ignores already-added plans."""
        innerspace_coordinator.data = _with_floor_plans(
            innerspace_coordinator.data,
            {
                **innerspace_coordinator.data["innerspace"]["floor_plans"],
                "bad_key": "not-a-dict",
                123: {"name": "Bad ID"},
            },
        )

        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = innerspace_coordinator

        async_add_entities = MagicMock()
        await async_setup_entry(hass, mock_entry, async_add_entities)
        async_add_entities.assert_called_once()
        assert len(async_add_entities.call_args[0][0]) == 1

        # Trigger listener update without new floor plans
        listener = innerspace_coordinator.async_add_listener.call_args[0][0]
        async_add_entities.reset_mock()
        listener()
        async_add_entities.assert_not_called()

        # Listener update when floor_plans is not a dict
        innerspace_coordinator.data = _with_floor_plans(
            innerspace_coordinator.data, None
        )
        listener()
        async_add_entities.assert_not_called()


class TestUnifiVoucherQrCodeImage:
    """Tests for UnifiVoucherQrCodeImage entity."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        coord = MagicMock()
        coord.data = {
            "sites": {"default": {"desc": "Default"}},
            "vouchers": {"default": {}},
            "latest_vouchers": {
                "default": {
                    "id": "v1",
                    "code": "1234567890",
                    "createdAt": "2026-10-09T00:00:00Z",
                    "expiresAt": "2099-01-01T00:00:00Z",
                    "expired": False,
                }
            },
        }
        return coord

    def test_init_and_properties(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        entity = UnifiVoucherQrCodeImage(
            hass,
            mock_coordinator,
            "default",
            unique_id="console_default_voucher_qr_code",
        )
        assert entity.unique_id == "console_default_voucher_qr_code"
        assert entity.translation_key == "voucher_qr_code"
        assert entity.content_type == "image/png"
        assert entity.available is True
        assert ("unifi_insights", "site_default") in require_not_none(
            entity.device_info
        ).get("identifiers", set())

    @pytest.mark.asyncio
    async def test_image_rendering_and_caching(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        entity = UnifiVoucherQrCodeImage(
            hass,
            mock_coordinator,
            "default",
            unique_id="console_default_voucher_qr_code",
        )
        img_bytes = await entity.async_image()
        assert img_bytes is not None
        assert img_bytes.startswith(b"\x89PNG")

        # Second call returns cached bytes
        cached = await entity.async_image()
        assert cached is img_bytes

    @pytest.mark.asyncio
    async def test_payload_disappearance(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        entity = UnifiVoucherQrCodeImage(
            hass,
            mock_coordinator,
            "default",
            unique_id="console_default_voucher_qr_code",
        )
        assert await entity.async_image() is not None

        # Remove latest voucher
        mock_coordinator.data = with_sections(mock_coordinator.data, latest_vouchers={})
        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        assert entity.available is False
        assert await entity.async_image() is None

    def test_availability_vouchers_unavailable(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        entity = UnifiVoucherQrCodeImage(
            hass,
            mock_coordinator,
            "default",
            unique_id="console_default_voucher_qr_code",
        )
        set_availability(mock_coordinator, failed_sections={("vouchers", "default")})
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_discovery_and_deduplication(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        added_entities: list = []

        async_add_entities = EntityCollector(added_entities)

        listeners = []
        mock_coordinator.async_add_listener = listeners.append

        await async_setup_entry(hass, mock_entry, async_add_entities)

        voucher_images = [
            e for e in added_entities if isinstance(e, UnifiVoucherQrCodeImage)
        ]
        assert len(voucher_images) == 1
        assert voucher_images[0].unique_id == "console_default_voucher_qr_code"

        # Deduplication
        prev_count = len(added_entities)
        for listener in listeners:
            listener()
        assert len(added_entities) == prev_count

    @pytest.mark.asyncio
    async def test_voucher_image_expiration_deadline_frozen_time(
        self, hass: HomeAssistant, mock_coordinator: MagicMock, freezer: Any
    ) -> None:
        """QR image becomes unavailable before, at, and after expiration deadline."""
        freezer.move_to("2026-10-09T11:59:00Z")
        record = {
            "id": "v1",
            "code": "1234567890",
            "expired": False,
            "expiresAt": "2026-10-09T12:00:00Z",
        }
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            latest_vouchers={"default": record},
            vouchers={"default": {}},
        )

        entity = UnifiVoucherQrCodeImage(
            hass,
            mock_coordinator,
            "default",
            unique_id="console_default_voucher_qr_code",
        )
        entity._reschedule_expiration_timer()

        assert entity.available is True
        img_bytes = await entity.async_image()
        assert img_bytes is not None

        # At deadline
        freezer.move_to("2026-10-09T12:00:00Z")
        target_dt = dt_util.parse_datetime("2026-10-09T12:00:00Z")
        assert target_dt is not None
        with patch.object(entity, "async_write_ha_state"):
            async_fire_time_changed(hass, target_dt)
            await hass.async_block_till_done()

        assert entity.available is False
        assert await entity.async_image() is None

        # After deadline
        freezer.move_to("2026-10-09T12:00:05Z")
        assert entity.available is False
        assert await entity.async_image() is None

        entity._cancel_expiration_timer()

    @pytest.mark.asyncio
    async def test_voucher_image_reschedule_expiration_timer_branches(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test timer reschedule branches for expired or missing deadline."""
        entity = UnifiVoucherQrCodeImage(
            hass,
            mock_coordinator,
            "default",
            unique_id="console_default_voucher_qr_code",
        )

        # 1. Non-mapping latest_vouchers
        mock_coordinator.data = with_sections(
            mock_coordinator.data, latest_vouchers=None
        )
        entity._reschedule_expiration_timer()
        assert entity._current_payload() is None

        # 2. Expired record
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            latest_vouchers={
                "default": {"id": "v1", "code": "1234567890", "expired": True}
            },
        )
        entity._reschedule_expiration_timer()

        # 3. Missing expiresAt
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            latest_vouchers={
                "default": {"id": "v1", "code": "1234567890", "expired": False}
            },
        )
        entity._reschedule_expiration_timer()

        # 4. Past expiresAt
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            latest_vouchers={
                "default": {
                    "id": "v1",
                    "code": "1234567890",
                    "expired": False,
                    "expiresAt": "2020-01-01T00:00:00Z",
                }
            },
        )
        entity._reschedule_expiration_timer()

        # 5. Invalid timestamp
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            latest_vouchers={
                "default": {
                    "id": "v1",
                    "code": "1234567890",
                    "expired": False,
                    "expiresAt": "invalid-date",
                }
            },
        )
        entity._reschedule_expiration_timer()

        # 6. Valid future record scheduled and cancelled
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            latest_vouchers={
                "default": {
                    "id": "v1",
                    "code": "1234567890",
                    "expired": False,
                    "expiresAt": "2099-01-01T00:00:00Z",
                }
            },
        )
        entity._reschedule_expiration_timer()
        assert entity._expiration_unsub is not None
        entity._cancel_expiration_timer()
        assert entity._expiration_unsub is None

    @pytest.mark.asyncio
    async def test_voucher_image_reschedule_requires_hass(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """No expiration timer is scheduled for an entity without hass."""
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            latest_vouchers={
                "default": {
                    "id": "v1",
                    "code": "1234567890",
                    "expired": False,
                    "expiresAt": "2099-01-01T00:00:00Z",
                }
            },
        )
        entity = UnifiVoucherQrCodeImage(
            hass,
            mock_coordinator,
            "default",
            unique_id="console_default_voucher_qr_code",
        )
        entity.hass = None  # type: ignore[assignment]
        entity._reschedule_expiration_timer()
        assert entity._expiration_unsub is None

    @pytest.mark.asyncio
    async def test_voucher_image_expiration_without_observed_payload(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Expiring before any payload was observed keeps the image timestamp."""
        mock_coordinator.data = with_sections(mock_coordinator.data, latest_vouchers={})
        entity = UnifiVoucherQrCodeImage(
            hass,
            mock_coordinator,
            "default",
            unique_id="console_default_voucher_qr_code",
        )
        assert entity._last_observed_payload is None
        before = entity._attr_image_last_updated
        with patch.object(entity, "async_write_ha_state") as write_state:
            entity._handle_expiration(dt_util.utcnow())
        write_state.assert_called_once_with()
        assert entity._attr_image_last_updated == before
        assert entity._last_observed_payload is None
        assert entity._rendered_payload is None
        assert entity._rendered_png is None

    @pytest.mark.asyncio
    async def test_voucher_image_timestamp_stability(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Image timestamp remains stable when observed payload does not change."""
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            latest_vouchers={
                "default": {"id": "v1", "code": "1234567890", "expired": False}
            },
        )
        entity = UnifiVoucherQrCodeImage(
            hass,
            mock_coordinator,
            "default",
            unique_id="console_default_voucher_qr_code",
        )
        if hasattr(entity, "async_added_to_hass"):
            await entity.async_added_to_hass()
        initial_ts = entity.image_last_updated

        # 1. Update before rendering with SAME code: timestamp must NOT change
        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        assert entity.image_last_updated == initial_ts

        # 2. Render image: cached bytes created
        img1 = await entity.async_image()
        assert img1 is not None

        # 3. Update after rendering with SAME code:
        # timestamp must NOT change, cached bytes retained
        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        assert entity.image_last_updated == initial_ts
        img2 = await entity.async_image()
        assert img2 is img1

        # 4. New code: timestamp advances, cached bytes invalidated
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            latest_vouchers={
                "default": {"id": "v2", "code": "9876543210", "expired": False}
            },
        )
        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        assert require_not_none(entity.image_last_updated) > require_not_none(
            initial_ts
        )
        img3 = await entity.async_image()
        assert img3 is not None
        assert img3 != img1

        # 5. Disappearance: available False, image None
        mock_coordinator.data = with_sections(mock_coordinator.data, latest_vouchers={})
        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        assert entity.available is False
        assert await entity.async_image() is None
        if hasattr(entity, "async_will_remove_from_hass"):
            await entity.async_will_remove_from_hass()
