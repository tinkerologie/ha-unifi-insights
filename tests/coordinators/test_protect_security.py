# Copyright 2026 UniFi Insights contributors
"""Tests for the Protect coordinator's security device families."""

from __future__ import annotations

import asyncio
import copy
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unifi_official_api import (
    UniFiNotFoundError,
    UniFiResponseError,
)
from unifi_official_api.protect.models import (
    Fob,
    LinkStation,
    Siren,
)

from custom_components.unifi_insights.const import (
    DOMAIN,
)
from custom_components.unifi_insights.coordinators.protect import (
    MAX_CONSECUTIVE_MISSING_POLLS,
    SIREN_STOP_GUARD_TTL,
    UNSUPPORTED_RESOURCE_RETRY,
    UnifiInsightsProtectCoordinator,
)
from custom_components.unifi_insights.coordinators.protect_events import (
    PollCompleted,
)
from tests.conftest import set_mock_device_lookup
from tests.fixtures.assertions import (
    patch_attribute,
    require_instance,
    require_not_none,
)
from tests.fixtures.coordinator_clients import (
    _create_mock_network_client,
    _create_mock_protect_client,
)
from tests.fixtures.library_responses import (
    SAMPLE_ALARM_HUB,
    SAMPLE_ALARM_HUB_TAMPER_EVENT,
    SAMPLE_KEYPAD_FOB,
    SAMPLE_SIREN,
    SAMPLE_THREAD_LINK_STATION,
)
from tests.fixtures.protect_ws import send_device, send_event

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


# --- Security device families ------------------------------------------------

_SECURITY_FAMILIES = [
    ("fobs", "_fetch_fobs", Fob, SAMPLE_KEYPAD_FOB),
    ("link_stations", "_fetch_link_stations", LinkStation, SAMPLE_THREAD_LINK_STATION),
    ("alarm_hubs", "_fetch_alarm_hubs", LinkStation, SAMPLE_ALARM_HUB),
    ("sirens", "_fetch_sirens", Siren, SAMPLE_SIREN),
]


class TestProtectSecurityDeviceFamilies:
    """Polling, WebSocket and event handling for fobs/link stations/alarm hubs."""

    @pytest.fixture
    async def coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> AsyncGenerator[UnifiInsightsProtectCoordinator]:
        """Create a protect coordinator, shut down on teardown."""
        coord = UnifiInsightsProtectCoordinator(
            hass=hass,
            network_client=_create_mock_network_client(),
            protect_client=_create_mock_protect_client(),
            entry=mock_config_entry,
        )
        yield coord
        await coord.async_shutdown()

    def test_collections_are_initialised(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Each family has a data key named device_type + "s" from the start.

        `UnifiProtectEntity` indexes `data["protect"][f"{device_type}s"]`
        directly, so a missing key is a KeyError in every entity.
        """
        for collection in ("fobs", "link_stations", "alarm_hubs", "sirens"):
            assert coordinator.data[collection] == {}
            assert coordinator._events.families[collection].previous_ids == frozenset()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("collection", "fetch_method", "model", "sample"), _SECURITY_FAMILIES
    )
    async def test_fetch_populates_collection_with_camelcase_dicts(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        collection: str,
        fetch_method: str,
        model: type,
        sample: dict[str, Any],
    ) -> None:
        """A fetch stores the alias-keyed dump of each real model by id."""
        getattr(coordinator.protect_client, collection).get_all = AsyncMock(
            return_value=[model.model_validate(sample)]
        )

        await getattr(coordinator, fetch_method)()

        stored = coordinator.data[collection][sample["id"]]
        assert stored["id"] == sample["id"]
        assert stored["state"] == "CONNECTED"
        if collection == "fobs":
            assert stored["keypadSettings"]["beepVolume"] == 60
        elif collection == "sirens":
            assert stored["sirenStatus"]["isActive"] is False
            assert stored["volume"] == 50
        else:
            assert "threadState" in stored

    @pytest.mark.asyncio
    async def test_scheduled_poll_fetches_every_family(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """The 30s poll includes the three new families."""
        client = coordinator.protect_client
        require_not_none(client).fobs.get_all = AsyncMock(
            return_value=[Fob.model_validate(SAMPLE_KEYPAD_FOB)]
        )
        require_not_none(client).link_stations.get_all = AsyncMock(
            return_value=[LinkStation.model_validate(SAMPLE_THREAD_LINK_STATION)]
        )
        require_not_none(client).alarm_hubs.get_all = AsyncMock(
            return_value=[LinkStation.model_validate(SAMPLE_ALARM_HUB)]
        )
        require_not_none(client).sirens.get_all = AsyncMock(
            return_value=[Siren.model_validate(SAMPLE_SIREN)]
        )

        data = await coordinator._async_update_data()

        assert set(data["fobs"]) == {"fob_1"}
        assert set(data["link_stations"]) == {"link_station_thread"}
        assert set(data["alarm_hubs"]) == {"alarm_hub_1"}
        assert set(data["sirens"]) == {"siren_1"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("collection", "fetch_method", "model", "sample"), _SECURITY_FAMILIES
    )
    @pytest.mark.parametrize(
        "error",
        [
            UniFiNotFoundError("Not Found", 404),
            UniFiResponseError("API returned non-JSON response (status 200)", 200),
        ],
        ids=["404", "non-json-200"],
    )
    async def test_unsupported_answer_marks_family_unsupported_and_logs_once(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
        *,
        collection: str,
        fetch_method: str,
        model: type,
        sample: dict[str, Any],
        error: Exception,
    ) -> None:
        """An older Protect without the endpoint is polled hourly, logged once.

        It may answer 404 or serve its HTML page with a 200. Without this
        every 30s poll spends a request (and a warning) on an endpoint the
        console does not have.
        """
        get_all = AsyncMock(side_effect=error)
        getattr(coordinator.protect_client, collection).get_all = get_all
        clock = 1000.0

        with patch(
            "custom_components.unifi_insights.coordinators.protect.time.monotonic",
            side_effect=lambda: clock,
        ):
            with caplog.at_level(logging.INFO):
                await getattr(coordinator, fetch_method)()
                await getattr(coordinator, fetch_method)()
            assert get_all.await_count == 1
            assert coordinator.data[collection] == {}

            clock += UNSUPPORTED_RESOURCE_RETRY.total_seconds() + 1
            with caplog.at_level(logging.INFO):
                await getattr(coordinator, fetch_method)()
            assert get_all.await_count == 2

        unsupported = [r for r in caplog.records if "does not expose" in r.getMessage()]
        assert len(unsupported) == 1
        assert unsupported[0].levelno == logging.INFO
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        # Never answered, so the HTML page is expected and logged at DEBUG.
        get_all.assert_awaited_with(expected_unsupported=True)

    @pytest.mark.asyncio
    async def test_non_json_after_success_warns_and_keeps_polling(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A family that answered before is not written off on an HTML page.

        It stopped being an "unsupported endpoint" signal once the family
        answered, so the call opts out and the error warns like any other.
        """
        endpoint = require_not_none(coordinator.protect_client).link_stations
        endpoint.get_all = AsyncMock(
            return_value=[LinkStation.model_validate(SAMPLE_THREAD_LINK_STATION)]
        )
        await coordinator._fetch_link_stations()
        endpoint.get_all.assert_awaited_once_with(expected_unsupported=True)

        endpoint.get_all = AsyncMock(
            side_effect=UniFiResponseError(
                "API returned non-JSON response (status 200)", 200
            )
        )
        await coordinator._fetch_link_stations()
        await coordinator._fetch_link_stations()

        assert "link_station_thread" in coordinator.data["link_stations"]
        assert endpoint.get_all.await_count == 2
        endpoint.get_all.assert_awaited_with(expected_unsupported=False)
        assert "Error fetching link_stations" in caplog.text
        assert "does not expose" not in caplog.text

    @pytest.mark.asyncio
    async def test_http_400_before_first_answer_is_not_unsupported(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A 400 is a request problem, not a missing endpoint: warn, keep polling."""
        endpoint = require_not_none(coordinator.protect_client).fobs
        endpoint.get_all = AsyncMock(
            side_effect=UniFiResponseError("API error (status 400)", 400)
        )

        await coordinator._fetch_fobs()
        await coordinator._fetch_fobs()

        assert endpoint.get_all.await_count == 2
        assert "Error fetching fobs" in caplog.text
        assert "does not expose" not in caplog.text

    @pytest.mark.asyncio
    async def test_unsupported_family_recovers_once_endpoint_answers(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """A Protect upgrade is picked up at the next hourly re-probe."""
        endpoint = require_not_none(coordinator.protect_client).fobs
        endpoint.get_all = AsyncMock(
            side_effect=[
                UniFiNotFoundError("Not Found", 404),
                [Fob.model_validate(SAMPLE_KEYPAD_FOB)],
            ]
        )
        clock = 1000.0
        with patch(
            "custom_components.unifi_insights.coordinators.protect.time.monotonic",
            side_effect=lambda: clock,
        ):
            await coordinator._fetch_fobs()
            clock += UNSUPPORTED_RESOURCE_RETRY.total_seconds() + 1
            await coordinator._fetch_fobs()
            await coordinator._fetch_fobs()

        assert "fob_1" in coordinator.data["fobs"]
        # Not skipped after recovering: the third call went to the API.
        assert endpoint.get_all.await_count == 3

    @pytest.mark.asyncio
    async def test_404_after_success_keeps_cache_and_keeps_polling(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """A family that answered before is not written off on one 404.

        It goes through the bounded 404 cache path instead, like cameras.
        """
        endpoint = require_not_none(coordinator.protect_client).alarm_hubs
        endpoint.get_all = AsyncMock(
            return_value=[LinkStation.model_validate(SAMPLE_ALARM_HUB)]
        )
        await coordinator._fetch_alarm_hubs()

        endpoint.get_all = AsyncMock(side_effect=UniFiNotFoundError("Not Found", 404))
        await coordinator._fetch_alarm_hubs()
        await coordinator._fetch_alarm_hubs()

        assert "alarm_hub_1" in coordinator.data["alarm_hubs"]
        assert endpoint.get_all.await_count == 2

    @pytest.mark.asyncio
    async def test_fetch_error_keeps_cached_devices(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Any other error preserves the cache and is logged."""
        coordinator.data["link_stations"] = {"ls1": {"id": "ls1"}}
        require_not_none(coordinator.protect_client).link_stations.get_all = AsyncMock(
            side_effect=Exception("500 Internal Server Error")
        )

        await coordinator._fetch_link_stations()

        assert coordinator.data["link_stations"] == {"ls1": {"id": "ls1"}}
        assert "Error fetching link_stations" in caplog.text

    @pytest.mark.asyncio
    async def test_incomplete_response_is_merged_over_cache(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """A device the endpoint dropped on a parse error is not treated as gone."""
        coordinator.data["fobs"] = {"fob_old": {"id": "fob_old"}}
        endpoint = require_not_none(coordinator.protect_client).fobs
        endpoint.get_all = AsyncMock(
            return_value=[Fob.model_validate(SAMPLE_KEYPAD_FOB)]
        )
        endpoint.last_result_complete = False

        await coordinator._fetch_fobs()

        assert set(coordinator.data["fobs"]) == {"fob_old", "fob_1"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("collection", "identifier"),
        [
            ("fobs", "protect_fob_dev1"),
            ("link_stations", "protect_link_station_dev1"),
            ("alarm_hubs", "protect_alarm_hub_dev1"),
            ("sirens", "protect_siren_dev1"),
        ],
    )
    async def test_stale_cleanup_removes_vanished_device(
        self,
        hass: HomeAssistant,
        coordinator: UnifiInsightsProtectCoordinator,
        collection: str,
        identifier: str,
    ) -> None:
        """A device gone past the grace window leaves the registry.

        The identifier must match the entity's `protect_{device_type}_{id}`.
        """
        coordinator.data[collection] = {"dev1": {"id": "dev1"}}
        coordinator._apply(PollCompleted())
        coordinator.data[collection] = {}
        mock_device = MagicMock()
        mock_device.id = "registry_dev1"

        with (
            patch(
                "custom_components.unifi_insights.coordinators.base.dr.async_get"
            ) as mock_registry,
            patch(
                "custom_components.unifi_insights.coordinators.base.async_get_device_entry",
                side_effect=lambda _reg, ident, _entry: (
                    mock_device if ident == (DOMAIN, identifier) else None
                ),
            ),
        ):
            for _ in range(MAX_CONSECUTIVE_MISSING_POLLS + 1):
                coordinator._apply(PollCompleted())

            mock_registry.return_value.async_remove_device.assert_called_once_with(
                "registry_dev1"
            )

    # -- WebSocket device frames ------------------------------------------

    def _seed_hub(self, coordinator: UnifiInsightsProtectCoordinator) -> dict[str, Any]:
        hub = copy.deepcopy(SAMPLE_ALARM_HUB)
        hub["alarmHub"]["armed"] = "on"
        coordinator.data["alarm_hubs"] = {"alarm_hub_1": hub}
        return hub

    def test_linkstation_frame_for_known_alarm_hub_updates_alarm_hubs(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Alarm hubs and link stations share modelKey "linkstation".

        The frame is routed by which collection already holds the id.
        """
        self._seed_hub(coordinator)
        coordinator.data["link_stations"] = {
            "link_station_thread": copy.deepcopy(SAMPLE_THREAD_LINK_STATION)
        }

        send_device(
            coordinator, "linkstation", {"id": "alarm_hub_1", "state": "DISCONNECTED"}
        )
        send_device(
            coordinator, "linkstation", {"id": "link_station_thread", "name": "Renamed"}
        )

        assert coordinator.data["alarm_hubs"]["alarm_hub_1"]["state"] == "DISCONNECTED"
        assert "alarm_hub_1" not in coordinator.data["link_stations"]
        assert coordinator.data["link_stations"]["link_station_thread"]["name"] == (
            "Renamed"
        )
        assert "link_station_thread" not in coordinator.data["alarm_hubs"]

    @pytest.mark.parametrize(
        ("is_alarm_hub", "collection"),
        [(True, "alarm_hubs"), (False, "link_stations")],
    )
    def test_linkstation_frame_for_new_device_uses_is_alarm_hub(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        *,
        is_alarm_hub: bool,
        collection: str,
    ) -> None:
        """An id no poll has seen yet is routed by the frame's isAlarmHub."""
        send_device(
            coordinator, "linkstation", {"id": "new1", "isAlarmHub": is_alarm_hub}
        )

        assert "new1" in coordinator.data[collection]

    def test_linkstation_frame_for_unknown_device_is_dropped(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Without an id match or isAlarmHub there is no safe target."""
        send_device(coordinator, "linkstation", {"id": "mystery", "state": "CONNECTED"})

        assert "mystery" not in coordinator.data["alarm_hubs"]
        assert "mystery" not in coordinator.data["link_stations"]

    def test_partial_nested_frame_keeps_sibling_fields(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """A partial alarmHub frame must not wipe armed/battery until next poll."""
        self._seed_hub(coordinator)

        send_device(
            coordinator,
            "linkstation",
            {"id": "alarm_hub_1", "alarmHub": {"deviceTamperStatus": "tampered"}},
        )

        alarm_hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]["alarmHub"]
        assert alarm_hub["deviceTamperStatus"] == "tampered"
        assert alarm_hub["armed"] == "on"
        assert alarm_hub["battery"] == {
            "charging": "off",
            "batteryStatus": "ok",
            "voltage": 13.1,
        }

    def test_partial_thread_network_frame_keeps_sibling_fields(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Merging recurses through threadState.network."""
        coordinator.data["link_stations"] = {
            "link_station_thread": copy.deepcopy(SAMPLE_THREAD_LINK_STATION)
        }

        send_device(
            coordinator,
            "linkstation",
            {
                "id": "link_station_thread",
                "threadState": {"network": {"joinedDeviceCount": 5}},
            },
        )

        network = coordinator.data["link_stations"]["link_station_thread"][
            "threadState"
        ]["network"]
        assert network["joinedDeviceCount"] == 5
        assert network["role"] == "leader"
        assert network["channel"] == 15

    def test_explicit_null_in_frame_replaces_nested_value(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Only dict-over-dict merges; a null network is a real state change."""
        coordinator.data["link_stations"] = {
            "link_station_thread": copy.deepcopy(SAMPLE_THREAD_LINK_STATION)
        }

        send_device(
            coordinator,
            "linkstation",
            {"id": "link_station_thread", "threadState": {"network": None}},
        )

        state = coordinator.data["link_stations"]["link_station_thread"]["threadState"]
        assert state["network"] is None

    def test_merge_never_mutates_the_previous_dicts(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Listeners holding the old dict must see it unchanged."""
        hub = self._seed_hub(coordinator)
        old_alarm_hub = hub["alarmHub"]
        snapshot = copy.deepcopy(hub)

        send_device(
            coordinator,
            "linkstation",
            {"id": "alarm_hub_1", "alarmHub": {"deviceTamperStatus": "tampered"}},
        )

        assert hub == snapshot
        assert old_alarm_hub["deviceTamperStatus"] == "restored"
        new_hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert new_hub is not hub
        assert new_hub["alarmHub"] is not old_alarm_hub

    def test_fob_frame_deep_merges_keypad_settings(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Fob frames ("fob" modelKey) deep-merge into fobs."""
        coordinator.data["fobs"] = {"fob_1": copy.deepcopy(SAMPLE_KEYPAD_FOB)}

        send_device(
            coordinator, "fob", {"id": "fob_1", "keypadSettings": {"beepVolume": 20}}
        )

        keypad = coordinator.data["fobs"]["fob_1"]["keypadSettings"]
        assert keypad == {"beepEnabled": True, "beepVolume": 20}

    def test_siren_frame_deep_merges_siren_status(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """A partial siren frame keeps the nested siblings it does not carry.

        Goes through the confirmed {"type", "item"} envelope. The siren starts
        out sounding, and the frame carries only the changed leaves.
        """
        sounding = copy.deepcopy(SAMPLE_SIREN)
        sounding["sirenStatus"] = {
            "isActive": True,
            "activatedAt": 1759700000000,
            "duration": 10000,
        }
        coordinator.data["sirens"] = {"siren_1": sounding}
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        coordinator._on_websocket_message(
            {
                "type": "update",
                "item": {
                    "id": "siren_1",
                    "modelKey": "siren",
                    "sirenStatus": {"isActive": False},
                    "wirelessConnectionState": {"batteryStatus": {"percentage": 80}},
                },
            }
        )

        siren = coordinator.data["sirens"]["siren_1"]
        assert siren["sirenStatus"] == {
            "isActive": False,
            "activatedAt": 1759700000000,
            "duration": 10000,
        }
        wireless = siren["wirelessConnectionState"]
        assert wireless["batteryStatus"] == {"percentage": 80, "isLow": False}
        assert wireless["signalState"] == {
            "signalQuality": None,
            "signalStrength": None,
        }
        assert siren["volume"] == 50
        listener.assert_called()

    def test_siren_frame_for_unpolled_siren_creates_entry(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """A frame for a siren the poll has not seen yet creates its entry."""
        send_device(
            coordinator, "siren", {"id": "siren_new", "sirenStatus": {"isActive": True}}
        )

        assert coordinator.data["sirens"]["siren_new"] == {
            "modelKey": "siren",
            "id": "siren_new",
            "sirenStatus": {"isActive": True},
        }

    def test_mark_siren_stopped_clears_is_active_and_notifies(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """A manual stop shows off in the cache and notifies listeners once."""
        active = copy.deepcopy(SAMPLE_SIREN)
        active["sirenStatus"] = {
            "isActive": True,
            "activatedAt": 1759700000000,
            "duration": 10000,
        }
        coordinator.data["sirens"] = {"siren_1": active}
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        coordinator.mark_siren_stopped("siren_1")

        status = coordinator.data["sirens"]["siren_1"]["sirenStatus"]
        assert status == {
            "isActive": False,
            "activatedAt": 1759700000000,
            "duration": 10000,
        }
        listener.assert_called_once()

    def test_mark_siren_stopped_with_null_status_sets_status(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """A siren whose status was null still ends up with an explicit off."""
        siren = copy.deepcopy(SAMPLE_SIREN)
        siren["sirenStatus"] = None
        coordinator.data["sirens"] = {"siren_1": siren}

        coordinator.mark_siren_stopped("siren_1")

        status = coordinator.data["sirens"]["siren_1"]["sirenStatus"]
        assert status == {"isActive": False}

    @pytest.mark.parametrize("record", [None, "not-a-dict"], ids=["absent", "garbage"])
    def test_mark_siren_stopped_ignores_unknown_siren(
        self, coordinator: UnifiInsightsProtectCoordinator, record: Any
    ) -> None:
        """A stop for a siren the cache has no usable record of changes nothing."""
        if record is not None:
            coordinator.data["sirens"] = {"siren_1": record}
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        coordinator.mark_siren_stopped("siren_1")

        assert coordinator.data["sirens"] == (
            {} if record is None else {"siren_1": record}
        )
        listener.assert_not_called()

    def test_mark_siren_stopped_leaves_poll_state_alone(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Nothing was polled, so the poll is neither marked good nor pushed back."""
        active = copy.deepcopy(SAMPLE_SIREN)
        active["sirenStatus"] = {"isActive": True}
        coordinator.data["sirens"] = {"siren_1": active}
        coordinator.last_update_success = False
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        with patch.object(coordinator, "_schedule_refresh") as reschedule:
            coordinator.mark_siren_stopped("siren_1")

        assert coordinator.last_update_success is False
        reschedule.assert_not_called()
        listener.assert_called_once()

    @staticmethod
    def _siren_with_status(**status: Any) -> dict[str, Any]:
        siren = copy.deepcopy(SAMPLE_SIREN)
        siren["sirenStatus"] = {"isActive": True, "duration": 10000, **status}
        return siren

    async def _poll_sirens(
        self, coordinator: UnifiInsightsProtectCoordinator, siren: dict[str, Any]
    ) -> dict[str, Any]:
        """Run one siren poll that answers with ``siren`` and return its record."""
        require_not_none(coordinator.protect_client).sirens.get_all = AsyncMock(
            return_value=[Siren.model_validate(siren)]
        )
        await coordinator._fetch_sirens()
        return coordinator.data["sirens"]["siren_1"]["sirenStatus"]

    @pytest.mark.asyncio
    async def test_poll_in_flight_during_a_stop_cannot_switch_the_siren_back_on(
        self, coordinator: UnifiInsightsProtectCoordinator, freezer: Any
    ) -> None:
        """A reading of the run that was just stopped does not resurrect it."""
        freezer.move_to("2026-10-06 12:00:00+00:00")
        now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        running = self._siren_with_status(activatedAt=now_ms - 3000)
        coordinator.data["sirens"] = {"siren_1": copy.deepcopy(running)}

        coordinator.mark_siren_stopped("siren_1")
        status = await self._poll_sirens(coordinator, running)

        assert status["isActive"] is False
        # The reading is applied but for the flag, so nothing else is lost.
        assert status["activatedAt"] == now_ms - 3000

        # The console keeps saying so until it reports the siren idle.
        status = await self._poll_sirens(coordinator, running)
        assert status["isActive"] is False
        assert "siren_1" in coordinator._events.siren_stops

        idle = self._siren_with_status(isActive=False, activatedAt=now_ms - 3000)
        status = await self._poll_sirens(coordinator, idle)
        assert status["isActive"] is False
        assert coordinator._events.siren_stops == {}

    @pytest.mark.asyncio
    async def test_run_started_after_the_stop_is_believed(
        self, coordinator: UnifiInsightsProtectCoordinator, freezer: Any
    ) -> None:
        """A poll showing a run that began after the stop is a new run."""
        freezer.move_to("2026-10-06 12:00:00+00:00")
        now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        coordinator.data["sirens"] = {
            "siren_1": self._siren_with_status(activatedAt=now_ms - 3000)
        }
        coordinator.mark_siren_stopped("siren_1")

        freezer.tick(2)
        status = await self._poll_sirens(
            coordinator, self._siren_with_status(activatedAt=now_ms + 1500)
        )

        assert status["isActive"] is True
        assert coordinator._events.siren_stops == {}

    @pytest.mark.asyncio
    async def test_active_siren_without_a_start_time_is_believed(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """With no start time to compare, an active reading is taken as it is."""
        coordinator.data["sirens"] = {"siren_1": self._siren_with_status()}
        coordinator.mark_siren_stopped("siren_1")

        status = await self._poll_sirens(
            coordinator, self._siren_with_status(activatedAt=None)
        )

        assert status["isActive"] is True
        assert coordinator._events.siren_stops == {}

    @pytest.mark.asyncio
    async def test_stop_guard_is_dropped_for_a_siren_that_is_gone(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """A siren the console no longer lists leaves no guard behind."""
        coordinator.data["sirens"] = {"siren_1": self._siren_with_status()}
        coordinator.mark_siren_stopped("siren_1")

        require_not_none(coordinator.protect_client).sirens.get_all = AsyncMock(
            return_value=[]
        )
        coordinator.data["sirens"] = {}
        await coordinator._fetch_sirens()

        assert coordinator._events.siren_stops == {}

    @pytest.mark.asyncio
    async def test_console_clock_ahead_cannot_resurrect_the_stopped_run(
        self, coordinator: UnifiInsightsProtectCoordinator, freezer: Any
    ) -> None:
        """The run is recognised by its own start time, not Home Assistant's clock.

        A console 5 s ahead stamps the run with a time later than the moment
        Home Assistant stopped it, so comparing the two clocks would believe
        the stale reading.
        """
        freezer.move_to("2026-10-06 12:00:00+00:00")
        now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        running = self._siren_with_status(activatedAt=now_ms + 5000)
        coordinator.data["sirens"] = {"siren_1": copy.deepcopy(running)}
        coordinator.mark_siren_stopped("siren_1")

        status = await self._poll_sirens(coordinator, running)

        assert status["isActive"] is False
        assert "siren_1" in coordinator._events.siren_stops

    @pytest.mark.asyncio
    async def test_home_assistant_clock_ahead_does_not_hide_a_new_run(
        self, coordinator: UnifiInsightsProtectCoordinator, freezer: Any
    ) -> None:
        """A new run is later than the stopped one on the console's own clock.

        With Home Assistant 5 s ahead of the console, a run started 2 s after
        the stop is stamped earlier than the moment of the stop, so comparing
        the two clocks would hide it for its whole run.
        """
        freezer.move_to("2026-10-06 12:00:00+00:00")
        now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        coordinator.data["sirens"] = {
            "siren_1": self._siren_with_status(activatedAt=now_ms - 8000)
        }
        coordinator.mark_siren_stopped("siren_1")

        freezer.tick(2)
        status = await self._poll_sirens(
            coordinator, self._siren_with_status(activatedAt=now_ms - 3000)
        )

        assert status["isActive"] is True
        assert coordinator._events.siren_stops == {}

    @pytest.mark.asyncio
    async def test_siren_without_a_cached_start_falls_back_to_the_wall_clock(
        self, coordinator: UnifiInsightsProtectCoordinator, freezer: Any
    ) -> None:
        """With no start time cached at the stop, the clocks are all there is."""
        freezer.move_to("2026-10-06 12:00:00+00:00")
        now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        siren = copy.deepcopy(SAMPLE_SIREN)
        siren["sirenStatus"] = None
        coordinator.data["sirens"] = {"siren_1": siren}
        coordinator.mark_siren_stopped("siren_1")

        stale = await self._poll_sirens(
            coordinator, self._siren_with_status(activatedAt=now_ms - 3000)
        )
        assert stale["isActive"] is False

        fresh = await self._poll_sirens(
            coordinator, self._siren_with_status(activatedAt=now_ms + 1500)
        )
        assert fresh["isActive"] is True
        assert coordinator._events.siren_stops == {}

    @pytest.mark.asyncio
    async def test_stop_guard_expires_after_two_poll_intervals(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """The guard cannot linger: after its TTL a reading is taken as it is."""
        clock = [5000.0]
        running = self._siren_with_status(activatedAt=1_000_000)
        coordinator.data["sirens"] = {"siren_1": copy.deepcopy(running)}

        with patch(
            "custom_components.unifi_insights.coordinators.protect.time.monotonic",
            side_effect=lambda: clock[0],
        ):
            coordinator.mark_siren_stopped("siren_1")

            clock[0] += SIREN_STOP_GUARD_TTL - 1
            held = await self._poll_sirens(coordinator, running)
            assert held["isActive"] is False

            clock[0] += 2
            expired = await self._poll_sirens(coordinator, running)

        assert expired["isActive"] is True
        assert coordinator._events.siren_stops == {}

    def test_late_frame_of_the_stopped_run_does_not_switch_the_siren_back_on(
        self, coordinator: UnifiInsightsProtectCoordinator, freezer: Any
    ) -> None:
        """The guard covers WebSocket frames as well as polls."""
        freezer.move_to("2026-10-06 12:00:00+00:00")
        now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        coordinator.data["sirens"] = {
            "siren_1": self._siren_with_status(activatedAt=now_ms - 3000)
        }
        coordinator.mark_siren_stopped("siren_1")

        send_device(
            coordinator,
            "siren",
            {
                "id": "siren_1",
                "sirenStatus": {"isActive": True, "activatedAt": now_ms - 3000},
            },
        )

        assert coordinator.data["sirens"]["siren_1"]["sirenStatus"]["isActive"] is False

    async def _read_in_flight_while_a_frame_lands(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        *,
        collection: str,
        fetch: Any,
        frame_model_key: str,
        frame: dict[str, Any],
        older_reading: Any,
        frame_before_request: bool = False,
    ) -> None:
        """Start ``fetch``, land ``frame`` while its request is out, then answer.

        The answer is ``older_reading``: what the console reported before the
        frame, handed back late.
        """
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_get_all(*_args: Any, **_kwargs: Any) -> list[Any]:
            started.set()
            await release.wait()
            return [older_reading]

        getattr(coordinator.protect_client, collection).get_all = slow_get_all
        if frame_before_request:
            send_device(coordinator, frame_model_key, copy.deepcopy(frame))
        task = asyncio.create_task(fetch())
        await started.wait()
        if not frame_before_request:
            send_device(coordinator, frame_model_key, copy.deepcopy(frame))
        release.set()
        await task

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["poll", "reconcile"])
    async def test_older_read_does_not_overwrite_a_siren_frame_that_landed_meanwhile(
        self, coordinator: UnifiInsightsProtectCoordinator, path: str
    ) -> None:
        """A WebSocket frame that lands while a read is out is newer than the read.

        Applying the read would replace the siren family and switch the run
        the frame reported back off, until the next frame or poll.
        """
        coordinator.data["sirens"] = {
            "siren_1": self._siren_with_status(isActive=False)
        }
        fetch = (
            coordinator._fetch_sirens
            if path == "poll"
            else coordinator._reconcile_sirens
        )

        await self._read_in_flight_while_a_frame_lands(
            coordinator,
            collection="sirens",
            fetch=fetch,
            frame_model_key="siren",
            frame={
                "id": "siren_1",
                "sirenStatus": {"isActive": True, "activatedAt": 1_000_000},
            },
            older_reading=Siren.model_validate(self._siren_with_status(isActive=False)),
        )

        status = coordinator.data["sirens"]["siren_1"]["sirenStatus"]
        assert status["isActive"] is True
        assert status["activatedAt"] == 1_000_000

    @pytest.mark.asyncio
    async def test_read_started_after_a_siren_frame_is_applied(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Only a frame that lands after the request started makes it stale."""
        coordinator.data["sirens"] = {"siren_1": self._siren_with_status(isActive=True)}

        await self._read_in_flight_while_a_frame_lands(
            coordinator,
            collection="sirens",
            fetch=coordinator._fetch_sirens,
            frame_model_key="siren",
            frame={"id": "siren_1", "sirenStatus": {"isActive": True}},
            older_reading=Siren.model_validate(self._siren_with_status(isActive=False)),
            frame_before_request=True,
        )

        assert coordinator.data["sirens"]["siren_1"]["sirenStatus"]["isActive"] is False

    @pytest.mark.asyncio
    async def test_families_without_a_counter_are_fetched_as_before(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """The check applies to the families that have a generation, no others."""
        coordinator.data["fobs"] = {"fob_1": {"id": "fob_1"}}

        await self._read_in_flight_while_a_frame_lands(
            coordinator,
            collection="fobs",
            fetch=coordinator._fetch_fobs,
            frame_model_key="fob",
            frame={"id": "fob_1", "keypadSettings": {"beepVolume": 20}},
            older_reading=Fob.model_validate(SAMPLE_KEYPAD_FOB),
        )

        assert (
            require_instance(coordinator.data["fobs"]["fob_1"]["keypadSettings"], dict)[
                "beepVolume"
            ]
            == 60
        )

    @pytest.mark.asyncio
    async def test_reconcile_refreshes_sirens_and_notifies_only_on_change(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """The independent timer re-reads sirens, and stays quiet when nothing moved."""
        coordinator.data["sirens"] = {"siren_1": self._siren_with_status()}
        listener = MagicMock()
        coordinator.async_add_listener(listener)
        idle = self._siren_with_status(isActive=False)
        require_not_none(coordinator.protect_client).sirens.get_all = AsyncMock(
            return_value=[Siren.model_validate(idle)]
        )

        await coordinator._reconcile_sirens()

        assert coordinator.data["sirens"]["siren_1"]["sirenStatus"]["isActive"] is False
        listener.assert_called_once()

        await coordinator._reconcile_sirens()

        listener.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconcile_timer_refresh_includes_sirens(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """The sensor reconcile tick also reconciles the sirens."""
        with (
            patch.object(coordinator, "async_refresh_sensors", AsyncMock()) as sensors,
            patch.object(coordinator, "_reconcile_sirens", AsyncMock()) as sirens,
        ):
            await coordinator._reconcile_sensor_refresh()

        sensors.assert_awaited_once()
        sirens.assert_awaited_once()

    def test_real_envelope_linkstation_frame_reaches_alarm_hubs(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """End to end through the confirmed {"type", "item"} envelope."""
        self._seed_hub(coordinator)
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        coordinator._on_websocket_message(
            {
                "type": "update",
                "item": {
                    "id": "alarm_hub_1",
                    "modelKey": "linkstation",
                    "alarmHub": {"armed": "off"},
                },
            }
        )

        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["alarmHub"]["armed"] == "off"
        assert hub["alarmHub"]["deviceTamperStatus"] == "restored"
        assert hub["type"] == "UP-AlarmHub"
        listener.assert_called()

    # -- alarmHubDeviceTamper events --------------------------------------

    def test_tamper_event_sets_status_user_and_time(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """status is a {text} wrapper; userName is a plain string."""
        hub = self._seed_hub(coordinator)
        snapshot = copy.deepcopy(hub)

        send_event(
            coordinator,
            "alarmHubDeviceTamper",
            copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT),
        )

        updated = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert updated["alarmHub"]["deviceTamperStatus"] == "tampered"
        assert updated["alarmHub"]["armed"] == "on"
        assert updated["_lastTamperUser"] == "Installer"
        assert updated["_lastTamperAt"] == 1759628100000
        assert hub == snapshot

    @pytest.mark.parametrize(
        ("user_name", "expected"),
        [({"text": "Installer"}, "Installer"), (None, None), (42, None)],
    )
    def test_tamper_event_user_name_shapes(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        user_name: Any,
        expected: str | None,
    ) -> None:
        """A {text} wrapper is tolerated; anything else is no user."""
        self._seed_hub(coordinator)
        event = copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        event["metadata"]["userName"] = user_name

        send_event(coordinator, "alarmHubDeviceTamper", event)

        assert coordinator.data["alarm_hubs"]["alarm_hub_1"]["_lastTamperUser"] == (
            expected
        )

    def test_tamper_event_without_status_keeps_reported_status(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """An unreadable status never overwrites the hub's reported one."""
        self._seed_hub(coordinator)
        event = copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        event["metadata"]["status"] = {"value": "tampered"}  # no "text"

        send_event(coordinator, "alarmHubDeviceTamper", event)

        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["alarmHub"]["deviceTamperStatus"] == "restored"
        assert hub["_lastTamperAt"] == 1759628100000

    def test_tamper_event_without_metadata_records_time_only(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """A frame missing metadata changes no status and names no user."""
        self._seed_hub(coordinator)
        event = copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        event["metadata"] = None

        send_event(coordinator, "alarmHubDeviceTamper", event)

        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["alarmHub"]["deviceTamperStatus"] == "restored"
        assert hub["_lastTamperUser"] is None
        assert hub["_lastTamperAt"] == 1759628100000

    def test_tamper_event_for_unknown_device_is_ignored(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """An event for a hub no poll has seen changes nothing."""
        event = copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        event["device"] = "not_a_hub"

        send_event(coordinator, "alarmHubDeviceTamper", event)

        assert coordinator.data["alarm_hubs"] == {}
        assert coordinator._events.tamper == {}

    @pytest.mark.asyncio
    async def test_tamper_details_survive_the_next_poll(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """The REST poll rebuilds each hub dict; the event details persist.

        Status stays authoritative from REST.
        """
        self._seed_hub(coordinator)
        send_event(
            coordinator,
            "alarmHubDeviceTamper",
            copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT),
        )
        require_not_none(coordinator.protect_client).alarm_hubs.get_all = AsyncMock(
            return_value=[LinkStation.model_validate(SAMPLE_ALARM_HUB)]
        )

        await coordinator._fetch_alarm_hubs()

        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["alarmHub"]["deviceTamperStatus"] == "restored"
        assert hub["_lastTamperUser"] == "Installer"
        assert hub["_lastTamperAt"] == 1759628100000

    @pytest.mark.asyncio
    async def test_event_status_survives_poll_without_rest_status(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Protect 7.2.x has the tamper event but no REST deviceTamperStatus.

        The event's status must outlive the next poll, or the sensor blinks
        on for one poll and then sits at unknown.
        """
        rest_hub = copy.deepcopy(SAMPLE_ALARM_HUB)
        del rest_hub["alarmHub"]["deviceTamperStatus"]
        coordinator.data["alarm_hubs"] = {"alarm_hub_1": copy.deepcopy(rest_hub)}
        require_not_none(coordinator.protect_client).alarm_hubs.get_all = AsyncMock(
            return_value=[LinkStation.model_validate(rest_hub)]
        )

        send_event(
            coordinator,
            "alarmHubDeviceTamper",
            copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT),
        )
        await coordinator._fetch_alarm_hubs()
        await coordinator._fetch_alarm_hubs()

        alarm_hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]["alarmHub"]
        assert alarm_hub["deviceTamperStatus"] == "tampered"
        assert alarm_hub["armed"] == "off"

    @pytest.mark.asyncio
    async def test_event_during_in_flight_poll_beats_the_stale_snapshot(
        self, coordinator: UnifiInsightsProtectCoordinator, monkeypatch
    ) -> None:
        """A REST snapshot taken before the tamper must not read as clear.

        The next poll, requested after the event, is authoritative again.
        """
        self._seed_hub(coordinator)

        async def get_all_with_tamper_mid_flight(
            *, expected_unsupported: bool
        ) -> list[LinkStation]:
            send_event(
                coordinator,
                "alarmHubDeviceTamper",
                copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT),
            )
            return [LinkStation.model_validate(SAMPLE_ALARM_HUB)]  # "restored"

        endpoint = require_not_none(coordinator.protect_client).alarm_hubs
        patch_attribute(
            monkeypatch, endpoint, "get_all", get_all_with_tamper_mid_flight
        )
        await coordinator._fetch_alarm_hubs()
        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["alarmHub"]["deviceTamperStatus"] == "tampered"

        endpoint.get_all = AsyncMock(
            return_value=[LinkStation.model_validate(SAMPLE_ALARM_HUB)]
        )
        await coordinator._fetch_alarm_hubs()
        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["alarmHub"]["deviceTamperStatus"] == "restored"

    def test_later_frame_of_same_event_keeps_user_and_time(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """An update frame of the same event (e.g. its end) fills gaps only."""
        self._seed_hub(coordinator)
        send_event(
            coordinator,
            "alarmHubDeviceTamper",
            copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT),
        )

        send_event(
            coordinator,
            "alarmHubDeviceTamper",
            {"id": "event_tamper_1", "device": "alarm_hub_1", "end": 1759628200000},
        )

        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["_lastTamperUser"] == "Installer"
        assert hub["_lastTamperAt"] == 1759628100000
        assert hub["alarmHub"]["deviceTamperStatus"] == "tampered"

    def test_new_event_without_user_does_not_inherit_previous_user(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """A different event naming nobody must not show the last event's user."""
        self._seed_hub(coordinator)
        send_event(
            coordinator,
            "alarmHubDeviceTamper",
            copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT),
        )
        restored = copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        restored["id"] = "event_tamper_2"
        restored["start"] = 1759628300000
        restored["metadata"]["status"] = {"text": "restored"}
        del restored["metadata"]["userName"]

        send_event(coordinator, "alarmHubDeviceTamper", restored)

        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["_lastTamperUser"] is None
        assert hub["_lastTamperAt"] == 1759628300000
        assert hub["alarmHub"]["deviceTamperStatus"] == "restored"

    def test_tamper_event_through_events_stream_envelope(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """End to end: the event's top-level "device" names the hub."""
        self._seed_hub(coordinator)

        coordinator._on_websocket_event_message(
            {"type": "add", "item": copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)}
        )

        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["alarmHub"]["deviceTamperStatus"] == "tampered"

    def test_stale_cleanup_forgets_tamper_details(
        self, hass: HomeAssistant, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """An evicted hub's event details must not leak onto a reused id."""
        coordinator.data["alarm_hubs"] = {"gone": {"id": "gone"}}
        event = copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        event["device"] = "gone"
        send_event(coordinator, "alarmHubDeviceTamper", event)
        assert "gone" in coordinator._events.tamper
        coordinator._apply(PollCompleted())
        coordinator.data["alarm_hubs"] = {}

        with patch(
            "custom_components.unifi_insights.coordinators.base.dr.async_get"
        ) as mock_registry:
            set_mock_device_lookup(mock_registry.return_value, None)
            for _ in range(MAX_CONSECUTIVE_MISSING_POLLS + 1):
                coordinator._apply(PollCompleted())

        assert "gone" not in coordinator._events.tamper

    # -- remaining branches ------------------------------------------------

    def test_unrelated_model_key_leaves_security_collections_alone(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """A frame for a family this coordinator doesn't track changes nothing.

        It notifies nobody either, so it cannot re-arm the poll timer.
        """
        self._seed_hub(coordinator)
        before = copy.deepcopy(coordinator.data["alarm_hubs"])
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        send_device(coordinator, "relay", {"id": "alarm_hub_1"})

        assert coordinator.data["alarm_hubs"] == before
        assert coordinator.data["fobs"] == {}
        assert coordinator.data["link_stations"] == {}
        listener.assert_not_called()

    def test_later_frame_of_same_event_with_status_replaces_it(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """A same-event frame that carries its own values wins over the first."""
        self._seed_hub(coordinator)
        send_event(
            coordinator,
            "alarmHubDeviceTamper",
            copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT),
        )
        first_received = coordinator._events.tamper["alarm_hub_1"].received
        update = copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        update["metadata"]["status"] = {"text": "restored"}
        update["metadata"]["userName"] = "Owner"

        send_event(coordinator, "alarmHubDeviceTamper", update)

        record = coordinator._events.tamper["alarm_hub_1"]
        assert record.status == "restored"
        assert record.last_user == "Owner"
        assert record.received >= first_received
        hub = coordinator.data["alarm_hubs"]["alarm_hub_1"]
        assert hub["alarmHub"]["deviceTamperStatus"] == "restored"

    @pytest.mark.asyncio
    async def test_poll_skips_tamper_record_of_hub_no_longer_listed(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """A record whose hub is not in the collection is not re-added."""
        self._seed_hub(coordinator)
        coordinator.data["alarm_hubs"]["gone"] = {"id": "gone"}
        event = copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
        event["device"] = "gone"
        send_event(coordinator, "alarmHubDeviceTamper", event)
        assert "gone" in coordinator._events.tamper
        require_not_none(coordinator.protect_client).alarm_hubs.get_all = AsyncMock(
            return_value=[LinkStation.model_validate(SAMPLE_ALARM_HUB)]
        )

        await coordinator._fetch_alarm_hubs()

        assert set(coordinator.data["alarm_hubs"]) == {"alarm_hub_1"}

    @pytest.mark.asyncio
    async def test_fetch_without_protect_client_is_a_no_op(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """A coordinator without a Protect client fetches nothing."""
        coord = UnifiInsightsProtectCoordinator(
            hass=hass,
            network_client=_create_mock_network_client(),
            protect_client=None,
            entry=mock_config_entry,
        )
        try:
            await coord._fetch_fobs()
            await coord._fetch_link_stations()
            await coord._fetch_alarm_hubs()
            await coord._fetch_sirens()
            assert coord.data["fobs"] == {}
            assert coord.data["link_stations"] == {}
            assert coord.data["alarm_hubs"] == {}
            assert coord.data["sirens"] == {}
        finally:
            await coord.async_shutdown()

    @pytest.mark.asyncio
    async def test_device_without_id_is_skipped(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """An item the API returns without an id can't be keyed, so it's left out."""
        require_not_none(coordinator.protect_client).fobs.get_all = AsyncMock(
            return_value=[{"name": "no id"}, Fob.model_validate(SAMPLE_KEYPAD_FOB)]
        )

        await coordinator._fetch_fobs()

        assert set(coordinator.data["fobs"]) == {"fob_1"}
