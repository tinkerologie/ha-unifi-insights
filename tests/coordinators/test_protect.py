# Copyright 2026 UniFi Insights contributors
"""
Tests for the Protect owner's commands (ADR-010).

Pins the second test adapter against the real ``unifi_official_api`` 3.0.0
classes: the seeded Protect owner refreshes through real coordinator code,
and the autospecced client rejects methods and arguments the library does
not have. Each Protect command is tested once here: its request, its
translated error and its overlay or reconciliation policy.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from unifi_official_api import (
    ApiKeyAuth,
    ConnectionType,
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiGlobalAlarmManagerError,
    UniFiNotFoundError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from unifi_official_api.exceptions import UniFiUnsupportedError
from unifi_official_api.protect import UniFiProtectClient
from unifi_official_api.protect.endpoints.chimes import ChimesEndpoint
from unifi_official_api.protect.models import (
    Camera,
    Chime,
    Light,
    LiveView,
    Sensor,
    Viewer,
)
from unifi_official_api.protect.models.files import RTSPSStream, TalkbackSession
from unifi_official_api.protect.talkback import (
    TalkbackSchemeError,
    TalkbackStreamError,
)
from unifi_official_api.validation import parse_record

from custom_components.unifi_insights.const import (
    DOMAIN,
    SCAN_INTERVAL_PROTECT,
)
from custom_components.unifi_insights.coordinators.protect import (
    MAX_CONSECUTIVE_EMPTY_FETCHES,
    MAX_CONSECUTIVE_MISSING_POLLS,
    STALE_EVENT_TIMEOUT,
    UnifiInsightsProtectCoordinator,
)
from custom_components.unifi_insights.coordinators.protect_events import (
    Family,
    PollCompleted,
    PollStarted,
    StreamReconnected,
    normalize_camera,
)
from tests.conftest import mock_device_lookup_method, set_mock_device_lookup
from tests.fixtures.assertions import require_mock, require_not_none
from tests.fixtures.command_owners import (
    BASE_URL,
    CHIME_ID,
    DOORBELL_IDS,
    autospec_endpoint,
    build_seeded_owners,
)
from tests.fixtures.coordinator_clients import (
    _create_mock_model,
    _create_mock_network_client,
    _create_mock_protect_client,
)
from tests.fixtures.library_responses import SAMPLE_SIREN
from tests.fixtures.protect_ws import send_device, send_event

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.fixtures.command_owners import SeededOwners


async def test_seeded_owners_refresh_through_real_code(
    seeded_owners: SeededOwners,
) -> None:
    """Every owner refreshes successfully from the seeded reads."""
    for owner in (seeded_owners.config, seeded_owners.device, seeded_owners.protect):
        assert owner.last_update_success is True
    protect = seeded_owners.facade.data["protect"]
    assert set(protect["cameras"]) == set(DOORBELL_IDS)
    assert protect["nvrs"]


async def test_seeded_chime_is_paired_with_two_doorbells(
    seeded_owners: SeededOwners,
) -> None:
    """The chime seed keeps one ring setting per paired doorbell."""
    chime = seeded_owners.protect.data["chimes"][CHIME_ID]

    assert chime["cameraIds"] == list(DOORBELL_IDS)
    assert [entry["cameraId"] for entry in chime["ringSettings"]] == list(DOORBELL_IDS)


async def test_client_rejects_methods_the_library_does_not_have(
    hass: HomeAssistant,
) -> None:
    """The autospec fails a call that a bare MagicMock would let pass."""
    owners = build_seeded_owners(hass)
    chimes = owners.protect_client.chimes

    # The shared conftest mock offers set_repeat; the real 2.0.0 method is
    # set_repeat_times.
    with pytest.raises(AttributeError):
        _ = chimes.set_repeat
    with pytest.raises(AttributeError):
        chimes.set_repeat = None
    with pytest.raises(AttributeError):
        _ = owners.protect_client.not_an_endpoint
    with pytest.raises(AttributeError):
        _ = owners.network_client.devices.reboot


async def test_client_enforces_the_library_signatures(hass: HomeAssistant) -> None:
    """A call that the real signature does not accept raises TypeError."""
    owners = build_seeded_owners(hass)

    with pytest.raises(TypeError):
        await owners.protect_client.chimes.set_volume(CHIME_ID)
    with pytest.raises(TypeError):
        await owners.network_client.devices.restart("default")

    await owners.protect_client.chimes.set_repeat_times(CHIME_ID, 2)
    owners.protect_client.chimes.set_repeat_times.assert_awaited_once_with(CHIME_ID, 2)


def test_endpoint_autospec_keeps_instance_attributes() -> None:
    """Instance attributes of the real endpoint are part of the spec."""
    chimes = autospec_endpoint(ChimesEndpoint)

    assert chimes.last_result_complete is True
    assert chimes.get_all.return_value == []


CAMERA_ID = DOORBELL_IDS[0]
# An event start (epoch ms) recent enough for the event store to keep.
RECENT_START = int(datetime.now(tz=UTC).timestamp() * 1000)
LIGHT_ID = "light_1"
VIEWER_ID = "viewer_1"
SIREN_ID = SAMPLE_SIREN["id"]


def _seed(owners: SeededOwners) -> None:
    """Add a light, a viewer and a siren to the seeded Protect data."""
    owners.protect.data["lights"][LIGHT_ID] = {
        "id": LIGHT_ID,
        "lightModeSettings": {"mode": "motion", "enableAt": "dark"},
        "lightDeviceSettings": None,
    }
    owners.protect.data["viewers"][VIEWER_ID] = {"id": VIEWER_ID, "liveview": "lv1"}
    owners.protect.data["sirens"][SIREN_ID] = copy.deepcopy(SAMPLE_SIREN)


def _listen(owners: SeededOwners) -> MagicMock:
    """Return a listener registered on the Protect owner."""
    listener = MagicMock()
    owners.protect.async_add_listener(listener)
    return listener


# (command, args, kwargs, endpoint, method, expected args, expected kwargs,
#  collection, record, overlay check: path -> value)
OVERLAY_COMMANDS: list[tuple[Any, ...]] = [
    (
        "async_update_camera",
        (CAMERA_ID,),
        {"ledSettings": {"isEnabled": False}},
        "cameras",
        "update",
        (CAMERA_ID,),
        {"ledSettings": {"isEnabled": False}},
        "cameras",
        CAMERA_ID,
        (("ledSettings", "isEnabled"), False),
    ),
    (
        "async_update_camera_settings",
        (CAMERA_ID,),
        {"videoMode": "highFps"},
        "cameras",
        "update",
        (CAMERA_ID,),
        {"videoMode": "highFps"},
        "cameras",
        CAMERA_ID,
        (("videoMode",), "highFps"),
    ),
    (
        "async_set_hdr_mode",
        (CAMERA_ID, "on"),
        {},
        "cameras",
        "set_hdr_mode",
        (CAMERA_ID, "on"),
        {},
        "cameras",
        CAMERA_ID,
        (("hdrType",), "on"),
    ),
    (
        "async_set_video_mode",
        (CAMERA_ID, "sport"),
        {},
        "cameras",
        "set_video_mode",
        (CAMERA_ID, "sport"),
        {},
        "cameras",
        CAMERA_ID,
        (("videoMode",), "sport"),
    ),
    (
        "async_set_video_mode",
        (CAMERA_ID, "lprReflex"),
        {},
        "cameras",
        "set_video_mode",
        (CAMERA_ID, "lprReflex"),
        {},
        "cameras",
        CAMERA_ID,
        (("videoMode",), "lprReflex"),
    ),
    (
        "async_set_video_mode",
        (CAMERA_ID, "lprNoneReflex"),
        {},
        "cameras",
        "set_video_mode",
        (CAMERA_ID, "lprNoneReflex"),
        {},
        "cameras",
        CAMERA_ID,
        (("videoMode",), "lprNoneReflex"),
    ),
    (
        "async_set_microphone_volume",
        (CAMERA_ID, 40),
        {},
        "cameras",
        "set_microphone_volume",
        (CAMERA_ID, 40),
        {},
        "cameras",
        CAMERA_ID,
        (("micVolume",), 40),
    ),
    (
        "async_set_light_mode",
        (LIGHT_ID, "always"),
        {},
        "lights",
        "update",
        (LIGHT_ID,),
        {"lightModeSettings": {"mode": "always"}},
        "lights",
        LIGHT_ID,
        (("lightModeSettings", "mode"), "always"),
    ),
    (
        "async_set_light_brightness",
        (LIGHT_ID, 4),
        {},
        "lights",
        "set_brightness",
        (LIGHT_ID, 4),
        {},
        "lights",
        LIGHT_ID,
        (("lightDeviceSettings", "ledLevel"), 4),
    ),
    (
        "async_update_viewer",
        (VIEWER_ID,),
        {"liveview": "lv2"},
        "viewers",
        "update",
        (VIEWER_ID,),
        {"liveview": "lv2"},
        "viewers",
        VIEWER_ID,
        (("liveview",), "lv2"),
    ),
]


def _at(record: dict[str, Any], path: tuple[str, ...]) -> Any:
    """Return the value at ``path`` in a nested record."""
    value: Any = record
    for key in path:
        value = value[key]
    return value


def _assert_error_warning_unspent(
    coordinator: UnifiInsightsProtectCoordinator, caplog: pytest.LogCaptureFixture
) -> None:
    """The next real events-stream error still logs its one-time WARNING."""
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        coordinator._on_websocket_event_message({"error": "Internal error"})
    assert any(
        r.levelno == logging.WARNING and "reported an error" in r.getMessage()
        for r in caplog.records
    )


class TestSettingsCommands:
    """Settings writes: one call, a published overlay, no refresh."""

    @pytest.mark.parametrize(
        (
            "command",
            "args",
            "kwargs",
            "endpoint",
            "method",
            "call_args",
            "call_kwargs",
            "collection",
            "record_id",
            "check",
        ),
        OVERLAY_COMMANDS,
    )
    async def test_write_publishes_the_written_fields(
        self,
        seeded_owners: SeededOwners,
        *,
        command: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        endpoint: str,
        method: str,
        call_args: tuple[Any, ...],
        call_kwargs: dict[str, Any],
        collection: Family,
        record_id: str,
        check: tuple[tuple[str, ...], Any],
    ) -> None:
        """The request matches the library call and the overlay follows it."""
        _seed(seeded_owners)
        protect = seeded_owners.protect
        client = seeded_owners.protect_client
        before = protect.data[collection][record_id]
        snapshot = copy.deepcopy(before)
        listener = _listen(seeded_owners)
        polls = client.cameras.get_all.await_count

        await getattr(protect, command)(*args, **kwargs)

        getattr(getattr(client, endpoint), method).assert_awaited_once_with(
            *call_args, **call_kwargs
        )
        path, value = check
        assert _at(protect.data[collection][record_id], path) == value
        assert (
            seeded_owners.facade.data["protect"][collection][record_id]
            is (protect.data[collection][record_id])
        )
        assert before == snapshot
        listener.assert_called_once_with()
        assert client.cameras.get_all.await_count == polls

    async def test_overlay_is_copy_on_write(self, seeded_owners: SeededOwners) -> None:
        """Every ancestor is replaced; retained snapshots keep their contents."""
        protect = seeded_owners.protect
        protect.data["cameras"][CAMERA_ID]["ledSettings"] = {
            "isEnabled": True,
            "blinkRate": 0,
        }
        old_root = protect.data
        old_cameras = old_root["cameras"]
        old_camera = old_cameras[CAMERA_ID]
        old_led = old_camera["ledSettings"]
        other = old_cameras[DOORBELL_IDS[1]]
        listener = _listen(seeded_owners)

        with patch.object(protect, "_schedule_refresh") as schedule:
            await protect.async_update_camera(
                CAMERA_ID, ledSettings={"isEnabled": False}
            )

        new_camera = protect.data["cameras"][CAMERA_ID]
        assert protect.data is not old_root
        assert protect.data["cameras"] is not old_cameras
        assert new_camera is not old_camera
        assert new_camera["ledSettings"] == {"isEnabled": False, "blinkRate": 0}
        assert old_led == {"isEnabled": True, "blinkRate": 0}
        assert old_cameras[CAMERA_ID] is old_camera
        assert protect.data["cameras"][DOORBELL_IDS[1]] is other
        listener.assert_called_once_with()
        schedule.assert_not_called()
        assert protect.last_update_success is True

    async def test_unknown_record_gets_no_overlay(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A write to a record the cache does not hold is sent, not published."""
        protect = seeded_owners.protect
        before = protect.data
        listener = _listen(seeded_owners)

        await protect.async_set_hdr_mode("unknown", "on")

        seeded_owners.protect_client.cameras.set_hdr_mode.assert_awaited_once_with(
            "unknown", "on"
        )
        assert protect.data is before
        listener.assert_not_called()

    @pytest.mark.parametrize(
        ("command", "args", "endpoint", "method", "message"),
        [
            ("async_update_camera", (CAMERA_ID,), "cameras", "update", "update camera"),
            (
                "async_set_microphone_volume",
                (CAMERA_ID, 5),
                "cameras",
                "set_microphone_volume",
                "set microphone volume",
            ),
            ("async_set_light_mode", (LIGHT_ID, "off"), "lights", "update", "set mode"),
            (
                "async_set_chime_volume",
                (CHIME_ID, 5),
                "chimes",
                "set_volume",
                "set volume for chime",
            ),
            (
                "async_move_ptz_to_preset",
                (CAMERA_ID, 2),
                "cameras",
                "ptz_goto_preset",
                "move camera",
            ),
            ("async_stop_siren", (SIREN_ID,), "sirens", "stop", "stop siren"),
        ],
    )
    async def test_failed_write_is_translated_and_publishes_nothing(
        self,
        seeded_owners: SeededOwners,
        *,
        command: str,
        args: tuple[Any, ...],
        endpoint: str,
        method: str,
        message: str,
    ) -> None:
        """A failed write keeps its message, the API cause and the old data."""
        _seed(seeded_owners)
        protect = seeded_owners.protect
        cause = UniFiConnectionError("down")
        getattr(
            getattr(seeded_owners.protect_client, endpoint), method
        ).side_effect = cause
        before = copy.deepcopy(protect.data)
        listener = _listen(seeded_owners)

        with pytest.raises(HomeAssistantError, match=f"^Unable to {message}") as err:
            await getattr(protect, command)(*args)

        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "command_failed"
        assert err.value.__cause__ is cause
        assert protect.data == before
        listener.assert_not_called()

    async def test_missing_client_is_a_translated_error(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Without a Protect client a command raises no_protect_coordinator."""
        protect = seeded_owners.protect
        protect.protect_client = None

        with pytest.raises(HomeAssistantError, match="Protect is not available") as err:
            await protect.async_trigger_alarm("alarm1")

        assert err.value.translation_key == "no_protect_coordinator"

    @pytest.mark.parametrize(
        ("command", "args", "body"),
        [
            (
                "async_set_light_mode",
                ("light1", "motion"),
                {"lightModeSettings": {"mode": "motion"}},
            ),
            (
                "async_set_light_brightness",
                ("light1", 6),
                {"lightDeviceSettings": {"ledLevel": 6}},
            ),
        ],
    )
    async def test_light_writes_send_the_spec_patch_body(
        self,
        seeded_owners: SeededOwners,
        command: str,
        args: tuple[Any, ...],
        body: dict[str, Any],
    ) -> None:
        """Light writes reach the real client as the spec's PATCH body."""
        client = UniFiProtectClient(
            auth=ApiKeyAuth(api_key="test-key"),
            base_url=BASE_URL,
            connection_type=ConnectionType.LOCAL,
        )
        client._patch = AsyncMock(  # type: ignore[method-assign]
            return_value={"id": "light1", "mac": "00:11:22:33:44:66"}
        )
        seeded_owners.protect.protect_client = client

        await getattr(seeded_owners.protect, command)(*args)

        client._patch.assert_awaited_once_with(
            client.build_api_path("/lights/light1"), json_data=body
        )


class TestChimeCommands:
    """Chime writes update every paired doorbell's ring settings."""

    @pytest.mark.parametrize(
        ("command", "value", "method", "field"),
        [
            ("async_set_chime_volume", 35, "set_volume", "volume"),
            ("async_set_chime_repeat", 4, "set_repeat_times", "repeatTimes"),
        ],
    )
    async def test_two_doorbell_overlay_keeps_sibling_fields(
        self,
        seeded_owners: SeededOwners,
        command: str,
        value: int,
        method: str,
        field: str,
    ) -> None:
        """Both ringSettings entries change; their other fields stay."""
        protect = seeded_owners.protect
        chimes = seeded_owners.protect_client.chimes
        old_settings = protect.data["chimes"][CHIME_ID]["ringSettings"]
        snapshot = copy.deepcopy(old_settings)
        polls = chimes.get_all.await_count

        await getattr(protect, command)(CHIME_ID, value)

        getattr(chimes, method).assert_awaited_once_with(CHIME_ID, value)
        settings = protect.data["chimes"][CHIME_ID]["ringSettings"]
        assert settings is not old_settings
        assert [entry[field] for entry in settings] == [value, value]
        assert [entry["cameraId"] for entry in settings] == list(DOORBELL_IDS)
        assert all(entry["ringtoneId"] == "ringtone_default" for entry in settings)
        assert old_settings == snapshot
        assert chimes.get_all.await_count == polls

    async def test_overlay_keeps_entries_that_are_not_records(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A malformed ringSettings entry is kept as it is."""
        protect = seeded_owners.protect
        protect.data["chimes"][CHIME_ID]["ringSettings"].append("junk")

        await protect.async_set_chime_volume(CHIME_ID, 10)

        assert protect.data["chimes"][CHIME_ID]["ringSettings"][-1] == "junk"

    async def test_ringtone_overlays_and_refreshes(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A ringtone write is published, then Protect is refreshed."""
        protect = seeded_owners.protect
        chimes = seeded_owners.protect_client.chimes
        polls = chimes.get_all.await_count
        seen: list[Any] = []
        protect.async_add_listener(
            lambda: seen.append(protect.data["chimes"][CHIME_ID]["ringSettings"])
        )

        await protect.async_set_chime_ringtone(CHIME_ID, "tone-2")

        chimes.set_ringtone.assert_awaited_once_with(CHIME_ID, "tone-2")
        assert [entry["ringtoneId"] for entry in seen[0]] == ["tone-2", "tone-2"]
        assert chimes.get_all.await_count == polls + 1

    async def test_ringtone_refresh_failure_keeps_the_write(
        self, seeded_owners: SeededOwners, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A refresh recorded as failed is logged and does not fail the write."""
        protect = seeded_owners.protect

        async def failed_refresh() -> None:
            protect.last_update_success = False

        with (
            patch.object(protect, "async_request_refresh", side_effect=failed_refresh),
            caplog.at_level(logging.DEBUG),
        ):
            await protect.async_set_chime_ringtone(CHIME_ID, "tone-2")

        seeded_owners.protect_client.chimes.set_ringtone.assert_awaited_once()
        assert "refresh after a write failed" in caplog.text

    @pytest.mark.parametrize("camera_ids", [list(DOORBELL_IDS[:1]), []])
    async def test_pairing_sends_only_camera_ids_then_refreshes(
        self, seeded_owners: SeededOwners, camera_ids: list[str]
    ) -> None:
        """The PATCH body is cameraIds alone; the overlay and refresh follow."""
        protect = seeded_owners.protect

        with patch.object(protect, "async_request_refresh", AsyncMock()) as refresh:
            await protect.async_set_chime_paired_cameras(CHIME_ID, camera_ids)

        seeded_owners.protect_client.chimes.update.assert_awaited_once_with(
            CHIME_ID, cameraIds=camera_ids
        )
        assert protect.data["chimes"][CHIME_ID]["cameraIds"] == camera_ids
        refresh.assert_awaited_once_with()

    @pytest.mark.parametrize(
        ("command", "args", "method", "message"),
        [
            (
                "async_set_chime_paired_cameras",
                (CHIME_ID, []),
                "update",
                "set the paired doorbells of chime",
            ),
            (
                "async_set_chime_ringtone",
                (CHIME_ID, "tone-2"),
                "set_ringtone",
                "set ringtone for chime",
            ),
        ],
    )
    async def test_failed_write_skips_the_refresh(
        self,
        seeded_owners: SeededOwners,
        command: str,
        args: tuple[Any, ...],
        method: str,
        message: str,
    ) -> None:
        """A rejected write raises command_failed and refreshes nothing."""
        protect = seeded_owners.protect
        getattr(
            seeded_owners.protect_client.chimes, method
        ).side_effect = UniFiResponseError("x", 400)

        with (
            patch.object(protect, "async_request_refresh", AsyncMock()) as refresh,
            pytest.raises(HomeAssistantError, match=f"Unable to {message}") as err,
        ):
            await getattr(protect, command)(*args)

        assert err.value.translation_key == "command_failed"
        refresh.assert_not_awaited()

    async def test_playback_is_unsupported_without_a_request(
        self, seeded_owners: SeededOwners
    ) -> None:
        """The real chimes endpoint refuses playback before any request."""
        client = UniFiProtectClient(
            auth=ApiKeyAuth(api_key="test-key"),
            base_url=BASE_URL,
            connection_type=ConnectionType.LOCAL,
        )
        client._post = AsyncMock()  # type: ignore[method-assign]
        seeded_owners.protect.protect_client = client

        with pytest.raises(HomeAssistantError) as err:
            await seeded_owners.protect.async_play_chime(CHIME_ID)

        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "chime_playback_unsupported"
        assert isinstance(err.value.__cause__, UniFiUnsupportedError)
        client._post.assert_not_awaited()

    async def test_unexpected_playback_failure_is_wrapped(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Any other playback failure keeps the chime-specific message."""
        cause = RuntimeError("boom")
        seeded_owners.protect_client.chimes.play.side_effect = cause

        with pytest.raises(HomeAssistantError, match="Unable to play chime") as err:
            await seeded_owners.protect.async_play_chime(CHIME_ID)

        assert err.value.translation_key == "command_failed"
        assert err.value.__cause__ is cause


class TestActionCommands:
    """PTZ, alarm, liveview and siren commands: one call, no overlay."""

    @pytest.mark.parametrize(
        ("command", "args", "kwargs", "endpoint", "method", "call_args", "call_kwargs"),
        [
            (
                "async_move_ptz_to_preset",
                (CAMERA_ID, 3),
                {},
                "cameras",
                "ptz_goto_preset",
                (CAMERA_ID, "3"),
                {},
            ),
            (
                "async_start_ptz_patrol",
                (CAMERA_ID, 1),
                {},
                "cameras",
                "ptz_patrol_start",
                (CAMERA_ID, 1),
                {},
            ),
            (
                "async_stop_ptz_patrol",
                (CAMERA_ID,),
                {},
                "cameras",
                "ptz_patrol_stop",
                (CAMERA_ID,),
                {},
            ),
            (
                "async_trigger_alarm",
                ("alarm1",),
                {},
                "application",
                "trigger_alarm_webhook",
                ("alarm1",),
                {},
            ),
            (
                "async_create_liveview",
                (),
                {"name": "Main", "layout": 2, "is_default": True},
                "liveviews",
                "create",
                (),
                {"name": "Main", "layout": 2, "isDefault": True},
            ),
            (
                "async_play_siren",
                (SIREN_ID,),
                {"duration": 10},
                "sirens",
                "play",
                (SIREN_ID,),
                {"duration": 10},
            ),
            (
                "async_play_siren",
                (SIREN_ID,),
                {},
                "sirens",
                "play",
                (SIREN_ID,),
                {"duration": None},
            ),
            (
                "async_set_siren_volume",
                (SIREN_ID, 50),
                {},
                "sirens",
                "set_volume",
                (SIREN_ID, 50),
                {},
            ),
        ],
    )
    async def test_action_sends_one_request(
        self,
        seeded_owners: SeededOwners,
        *,
        command: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        endpoint: str,
        method: str,
        call_args: tuple[Any, ...],
        call_kwargs: dict[str, Any],
    ) -> None:
        """The request matches the library call; nothing is published."""
        protect = seeded_owners.protect
        before = protect.data
        listener = _listen(seeded_owners)

        await getattr(protect, command)(*args, **kwargs)

        getattr(
            getattr(seeded_owners.protect_client, endpoint), method
        ).assert_awaited_once_with(*call_args, **call_kwargs)
        assert protect.data is before
        listener.assert_not_called()

    @pytest.mark.parametrize(
        ("command", "method"),
        [("async_arm_alarm", "enable"), ("async_disarm_alarm", "disable")],
    )
    async def test_arm_and_disarm_request_a_refresh(
        self, seeded_owners: SeededOwners, command: str, method: str
    ) -> None:
        """Arm and disarm call their endpoint, then refresh Protect."""
        protect = seeded_owners.protect
        endpoint = getattr(seeded_owners.protect_client.arm_profiles, method)
        endpoint.return_value = True

        with patch.object(protect, "async_request_refresh", AsyncMock()) as refresh:
            await getattr(protect, command)()

        endpoint.assert_awaited_once_with()
        refresh.assert_awaited_once_with()

    @pytest.mark.parametrize(
        ("command", "method"),
        [("async_arm_alarm", "enable"), ("async_disarm_alarm", "disable")],
    )
    async def test_global_alarm_manager_refusal_is_translated(
        self, seeded_owners: SeededOwners, command: str, method: str
    ) -> None:
        """The refusal is translated, records the manager and refreshes nothing."""
        protect = seeded_owners.protect
        getattr(
            seeded_owners.protect_client.arm_profiles, method
        ).side_effect = UniFiGlobalAlarmManagerError("x", 400, "body")
        protect.data["global_alarm_manager"] = False

        with (
            patch.object(protect, "async_request_refresh", AsyncMock()) as refresh,
            pytest.raises(HomeAssistantError) as err,
        ):
            await getattr(protect, command)()

        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "global_alarm_manager"
        refresh.assert_not_awaited()
        assert protect.data["global_alarm_manager"] is True

    async def test_other_arm_failure_keeps_the_message(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Any other arm failure keeps the "Unable to ..." wording."""
        protect = seeded_owners.protect
        seeded_owners.protect_client.arm_profiles.enable.side_effect = (
            UniFiResponseError("x", 500)
        )

        with (
            patch.object(protect, "async_request_refresh", AsyncMock()) as refresh,
            pytest.raises(
                HomeAssistantError, match="Unable to arm the UniFi Protect alarm"
            ) as err,
        ):
            await protect.async_arm_alarm()

        assert err.value.translation_key == "command_failed"
        refresh.assert_not_awaited()

    async def test_stop_siren_shows_it_off(self, seeded_owners: SeededOwners) -> None:
        """A stop is sent, then the facade data shows the siren off."""
        _seed(seeded_owners)
        protect = seeded_owners.protect
        protect.data["sirens"][SIREN_ID]["sirenStatus"] = {"isActive": True}

        await protect.async_stop_siren(SIREN_ID)

        seeded_owners.protect_client.sirens.stop.assert_awaited_once_with(SIREN_ID)
        siren = seeded_owners.facade.data["protect"]["sirens"][SIREN_ID]
        assert siren["sirenStatus"]["isActive"] is False


SESSION = TalkbackSession(
    url="rtp://192.168.1.10:7004",
    codec="opus",
    samplingRate=24000,
    bitsPerSample=16,
)
STREAM_CLASS = "custom_components.unifi_insights.coordinators.protect.TalkbackStream"


class TestTalkback:
    """Talkback creates a session and starts the stream, or raises translated."""

    async def test_talkback_starts_the_stream(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A session is created and its stream started."""
        protect = seeded_owners.protect
        create = seeded_owners.protect_client.cameras.create_talkback_session
        create.return_value = SESSION

        with patch(STREAM_CLASS) as stream_cls:
            stream_cls.return_value.start = AsyncMock()
            stream = await protect.async_start_camera_talkback(CAMERA_ID, "media.mp3")

        assert stream is stream_cls.return_value
        create.assert_awaited_once_with(CAMERA_ID)
        stream_cls.assert_called_once_with(
            SESSION, "media.mp3", loop=seeded_owners.facade.hass.loop
        )
        require_mock(stream.start).assert_awaited_once_with()

    async def test_talkback_without_client(self, seeded_owners: SeededOwners) -> None:
        """Without a Protect client talkback raises its own translated error."""
        seeded_owners.protect.protect_client = None

        with pytest.raises(HomeAssistantError) as err:
            await seeded_owners.protect.async_start_camera_talkback(CAMERA_ID, "a.mp3")

        assert err.value.translation_key == "protect_not_available_for_talkback"

    async def test_session_failure_logs_only_sanitised_fields(
        self, seeded_owners: SeededOwners, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A failed session is translated; a malformed one logs field names."""
        create = seeded_owners.protect_client.cameras.create_talkback_session
        create.side_effect = ValueError("Session failed")
        with pytest.raises(HomeAssistantError) as err:
            await seeded_owners.protect.async_start_camera_talkback(CAMERA_ID, "a.mp3")
        assert err.value.translation_key == "talkback_session_invalid"

        leaked_url = "rtp://custom-token@192.168.1.10:7004"
        with pytest.raises(UniFiResponseError) as invalid:
            parse_record(TalkbackSession, {"url": [leaked_url]}, "Talkback session")
        create.side_effect = invalid.value
        with caplog.at_level(logging.WARNING), pytest.raises(HomeAssistantError):
            await seeded_owners.protect.async_start_camera_talkback(CAMERA_ID, "a.mp3")
        assert "Talkback session response failed validation (url)" in caplog.text
        assert leaked_url not in caplog.text

    @pytest.mark.parametrize(
        ("error", "key"),
        [
            (TalkbackSchemeError("Bad scheme"), "talkback_unsupported_codec_or_scheme"),
            (TalkbackStreamError("Stream failed"), "talkback_stream_failure"),
        ],
    )
    async def test_stream_failure_is_translated(
        self, seeded_owners: SeededOwners, error: Exception, key: str
    ) -> None:
        """An unsupported or failed stream raises its translated error."""
        create = seeded_owners.protect_client.cameras.create_talkback_session
        create.return_value = SESSION

        with (
            patch(f"{STREAM_CLASS}.start", side_effect=error),
            pytest.raises(HomeAssistantError) as err,
        ):
            await seeded_owners.protect.async_start_camera_talkback(CAMERA_ID, "a.mp3")

        assert err.value.translation_key == key


class TestCameraReads:
    """Snapshot and stream reads never raise into the camera entity."""

    async def test_snapshot(self, seeded_owners: SeededOwners) -> None:
        """Bytes are returned; anything else, or a failure, is None."""
        protect = seeded_owners.protect
        get_snapshot = seeded_owners.protect_client.cameras.get_snapshot
        get_snapshot.return_value = b"jpeg"

        assert await protect.async_get_camera_snapshot(CAMERA_ID) == b"jpeg"
        get_snapshot.assert_awaited_once_with(CAMERA_ID)

        get_snapshot.return_value = "not-bytes"
        assert await protect.async_get_camera_snapshot(CAMERA_ID) is None

        get_snapshot.side_effect = UniFiConnectionError("down")
        assert await protect.async_get_camera_snapshot(CAMERA_ID) is None

    async def test_stream_url(self, seeded_owners: SeededOwners) -> None:
        """The high-quality URL is returned; no URL or a failure is None."""
        protect = seeded_owners.protect
        create = seeded_owners.protect_client.cameras.create_rtsps_stream
        create.return_value = RTSPSStream.model_validate(
            {"high": "rtsps://nvr:7441/abc"}
        )

        assert (
            await protect.async_create_camera_stream_url(CAMERA_ID)
            == "rtsps://nvr:7441/abc"
        )
        create.assert_awaited_once_with(CAMERA_ID, qualities=["high"])

        create.return_value = RTSPSStream.model_validate({})
        assert await protect.async_create_camera_stream_url(CAMERA_ID) is None

        create.side_effect = UniFiConnectionError("down")
        assert await protect.async_create_camera_stream_url(CAMERA_ID) is None


# --- Protect coordinator -----------------------------------------------------


def _poll_completed(
    coordinator: UnifiInsightsProtectCoordinator, times: int = 1
) -> None:
    """Run the end-of-poll registry clean-up, as the main poll does."""
    for _ in range(times):
        coordinator._apply(PollCompleted())


def _seen_by_a_poll(coordinator: UnifiInsightsProtectCoordinator, *ids: str) -> None:
    """Record ``ids`` as cameras a poll saw."""
    coordinator.data["cameras"] = {camera_id: {"id": camera_id} for camera_id in ids}
    _poll_completed(coordinator)


def _sweep_latches(coordinator: UnifiInsightsProtectCoordinator) -> None:
    """Expire stale event latches, as the start of every main poll does."""
    coordinator._apply(PollStarted())


class TestUnifiProtectCoordinator:
    """Tests for UnifiInsightsProtectCoordinator."""

    @pytest.mark.parametrize(
        "family", ["chimes", "fobs", "link_stations", "alarm_hubs", "sirens"]
    )
    @pytest.mark.parametrize(
        "error",
        [UniFiTimeoutError("offline"), UniFiResponseError("offline", status_code=502)],
    )
    async def test_family_poll_outage_logs_only_transitions(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
        family: str,
        error: Exception,
    ) -> None:
        """Swallowed family failures log once per streak while polls succeed."""
        caplog.set_level(logging.DEBUG)
        endpoint = getattr(coordinator.protect_client, family)
        endpoint.get_all.side_effect = error
        failure = f"Protect coordinator: Error fetching {family}: {error}"
        recovery = f"Protect coordinator: Fetching {family} recovered"
        for _ in range(4):
            await coordinator.async_refresh()
            assert coordinator.last_update_success
        assert [r.levelno for r in caplog.records if r.getMessage() == failure] == [
            logging.WARNING,
            logging.DEBUG,
            logging.DEBUG,
            logging.DEBUG,
        ]

        # Recovery of this family must not reset another family's outage guard.
        other = "fobs" if family == "chimes" else "chimes"
        other_endpoint = getattr(coordinator.protect_client, other)
        other_endpoint.get_all.side_effect = error
        await coordinator.async_refresh()
        endpoint.get_all.side_effect = None
        for _ in range(2):
            await coordinator.async_refresh()
            assert coordinator.last_update_success
        assert caplog.messages.count(recovery) == 1
        assert (
            next(r.levelno for r in caplog.records if r.getMessage() == recovery)
            == logging.INFO
        )
        assert (
            sum(
                r.levelno == logging.WARNING
                and r.getMessage()
                == f"Protect coordinator: Error fetching {other}: {error}"
                for r in caplog.records
            )
            == 1
        )

        endpoint.get_all.side_effect = error
        for _ in range(2):
            await coordinator.async_refresh()
        assert (
            sum(
                r.levelno == logging.WARNING and r.getMessage() == failure
                for r in caplog.records
            )
            == 2
        )

    @pytest.fixture
    async def coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> AsyncGenerator[UnifiInsightsProtectCoordinator]:
        """Create a protect coordinator for testing, shut down on teardown.

        Was a plain (non-yield) fixture with no teardown at all - this
        class constructs `UnifiInsightsProtectCoordinator` directly rather than
        going through config-entry setup/unload, so nothing ever cancelled
        the sensor-reconcile timer, the three tracked sensor background
        tasks (`_sensor_refresh_task`/`_sensor_reconcile_task`/
        `_sensor_reconnect_task`), or shut down the
        `_sensor_reconnect_debouncer`'s trailing-edge timer that a test
        left pending. `async_shutdown()` (see coordinators/protect.py)
        cancels exactly those per-test - it is hygiene for this test file's
        own per-test coordinator instances, not a fix for a leak or a
        regression guard: measured via a real
        `entry._async_process_on_unload(hass)` reload-cycle probe, the
        `EVENT_HOMEASSISTANT_STOP` listener wrapped in
        `entry.async_on_unload` releases its coordinator on unload
        regardless of whether this fixture calls `async_shutdown()`, and
        the full 286-test file passes unmodified even with this fixture
        reverted to a plain non-yield fixture.
        """
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        coord = UnifiInsightsProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )
        yield coord
        await coord.async_shutdown()

    async def test_protect_poll_outage_logs_only_transitions(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Absorption and escalation do not add warnings before UpdateFailed."""
        caplog.set_level(logging.INFO)
        require_mock(
            require_not_none(coordinator.protect_client).cameras.get_all
        ).side_effect = UniFiTimeoutError("offline")
        for poll in range(MAX_CONSECUTIVE_EMPTY_FETCHES + 4):
            await coordinator.async_refresh()
            assert sum(r.levelno >= logging.WARNING for r in caplog.records) == int(
                poll >= MAX_CONSECUTIVE_EMPTY_FETCHES
            )
        require_mock(
            require_not_none(coordinator.protect_client).cameras.get_all
        ).side_effect = None
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        assert sum("recovered" in r.getMessage().lower() for r in caplog.records) == 1
        require_mock(
            require_not_none(coordinator.protect_client).cameras.get_all
        ).side_effect = UniFiTimeoutError("offline")
        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES + 1):
            await coordinator.async_refresh()
        assert sum(r.levelno >= logging.WARNING for r in caplog.records) == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("fetch", "collection", "level", "message"),
        [
            ("_fetch_sensors", "sensors", logging.WARNING, "Error fetching sensors"),
            ("_fetch_nvr", "nvrs", logging.DEBUG, "Error fetching NVR"),
            ("_fetch_chimes", "chimes", logging.WARNING, "Error fetching chimes"),
        ],
    )
    async def test_a_failed_merge_is_reported_like_a_fetch_error(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
        *,
        fetch: str,
        collection: Family,
        level: int,
        message: str,
    ) -> None:
        """A REST answer that cannot be merged keeps the cache and the poll going."""
        before = copy.deepcopy(coordinator.data[collection])
        with (
            caplog.at_level(logging.DEBUG),
            patch(
                "custom_components.unifi_insights.coordinators.protect.reconcile",
                side_effect=RuntimeError("bad record"),
            ),
        ):
            await getattr(coordinator, fetch)()

        assert coordinator.data[collection] == before
        assert any(
            r.levelno == level and message in r.getMessage() for r in caplog.records
        )

    @pytest.fixture
    async def coordinator_no_protect(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> AsyncGenerator[UnifiInsightsProtectCoordinator]:
        """Create a protect coordinator without protect client, shut down on teardown.

        See `coordinator` above - same per-test cleanup, this fixture just
        omits the `protect_client`.
        """
        network_client = _create_mock_network_client()
        coord = UnifiInsightsProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=None,
            entry=mock_config_entry,
        )
        yield coord
        await coord.async_shutdown()

    def test_initialization(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test coordinator initialization."""
        assert coordinator.name == f"{DOMAIN}_protect"
        assert coordinator.update_interval == SCAN_INTERVAL_PROTECT
        assert "cameras" in coordinator.data
        assert "lights" in coordinator.data
        assert "sensors" in coordinator.data
        assert "nvrs" in coordinator.data
        assert "chimes" in coordinator.data
        assert "viewers" in coordinator.data
        assert "liveviews" in coordinator.data
        assert "events" in coordinator.data

    def test_websocket_state_initialized(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test WebSocket state is initialized but not started at construction."""
        assert coordinator.websocket_task is None
        assert coordinator.events_websocket_task is None
        assert (
            coordinator._protect_websocket
            is require_not_none(coordinator.protect_client).websocket
        )

    def test_websocket_state_without_client(
        self, coordinator_no_protect: UnifiInsightsProtectCoordinator
    ):
        """Test WebSocket state without a protect client."""
        assert coordinator_no_protect.protect_client is None
        assert coordinator_no_protect._protect_websocket is None
        assert coordinator_no_protect.websocket_task is None
        assert coordinator_no_protect.events_websocket_task is None

    @pytest.mark.asyncio
    async def test_async_start_websocket_without_client(
        self, coordinator_no_protect: UnifiInsightsProtectCoordinator
    ):
        """Test async_start_websocket returns early without a protect client."""
        await coordinator_no_protect.async_start_websocket()
        assert coordinator_no_protect.websocket_task is None

    @pytest.mark.asyncio
    async def test_async_start_websocket_success(
        self, hass: HomeAssistant, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test async_start_websocket resolves host_id and starts both the
        devices and events subscriptions (see task 1: the missing events
        subscription is the root cause of motion detection never firing).

        Each subscription is registered with its OWN connection-state
        callback (review finding 1: a single shared callback couldn't tell
        the caller which subscription actually transitioned, which is what
        made a devices-only reconnect look identical to a full recovery).
        """
        require_not_none(coordinator.protect_client).get_host_id = AsyncMock(
            return_value="nvr1"
        )
        coordinator._protect_websocket.subscribe_with_callback = AsyncMock()

        await coordinator.async_start_websocket()

        assert coordinator.websocket_task is not None
        assert coordinator.events_websocket_task is not None
        require_mock(
            require_not_none(coordinator.protect_client).get_host_id
        ).assert_awaited_once()
        await coordinator.websocket_task
        await coordinator.events_websocket_task

        coordinator._protect_websocket.subscribe_with_callback.assert_any_await(
            "nvr1",
            coordinator._site_id,
            "devices",
            coordinator._on_websocket_message,
            reconnect=True,
            on_connection_state_change=coordinator._on_devices_connection_state_change,
        )
        coordinator._protect_websocket.subscribe_with_callback.assert_any_await(
            "nvr1",
            coordinator._site_id,
            "events",
            coordinator._on_websocket_event_message,
            reconnect=True,
            on_connection_state_change=coordinator._on_events_connection_state_change,
        )
        assert coordinator._protect_websocket.subscribe_with_callback.await_count == 2

    @pytest.mark.asyncio
    async def test_async_start_websocket_already_running(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test async_start_websocket is a no-op if already running."""
        existing_task = asyncio.get_event_loop().create_future()
        coordinator.websocket_task = existing_task  # type: ignore[assignment]
        require_not_none(coordinator.protect_client).get_host_id = AsyncMock(
            return_value="nvr1"
        )

        await coordinator.async_start_websocket()

        require_mock(
            require_not_none(coordinator.protect_client).get_host_id
        ).assert_not_called()
        assert coordinator.websocket_task is existing_task
        existing_task.set_result(None)

    @pytest.mark.asyncio
    async def test_async_start_websocket_host_id_failure(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test async_start_websocket swallows host_id resolution failures."""
        require_not_none(coordinator.protect_client).get_host_id = AsyncMock(
            side_effect=Exception("no NVR")
        )

        # Should not raise - WebSocket is additive, polling stays the fallback.
        await coordinator.async_start_websocket()

        assert coordinator.websocket_task is None
        assert coordinator.events_websocket_task is None

    def test_on_websocket_message_top_level(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test the WS message adapter with fields at the top level."""
        coordinator._on_websocket_message(
            {"modelKey": "sensor", "id": "sensor3", "isOpened": True}
        )

        assert coordinator.data["sensors"]["sensor3"]["isOpened"] is True

    def test_on_websocket_message_nested_payload(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test the WS message adapter with fields nested under 'payload'."""
        coordinator._on_websocket_message(
            {"payload": {"modelKey": "sensor", "id": "sensor4", "isOpened": False}}
        )

        assert coordinator.data["sensors"]["sensor4"]["isOpened"] is False

    def test_on_websocket_message_item_wrapper(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test the WS message adapter with fields nested under 'item'.

        Confirmed live against hardware 2026-08-12: local-console update
        frames use {"type": "update", "item": {...}}, not top-level fields.
        """
        coordinator._on_websocket_message(
            {
                "type": "update",
                "item": {"modelKey": "sensor", "id": "sensor9", "isOpened": True},
            }
        )

        assert coordinator.data["sensors"]["sensor9"]["isOpened"] is True

    def test_on_websocket_message_snake_case_model_key(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test the WS message adapter accepts snake_case model_key."""
        coordinator._on_websocket_message({"model_key": "light", "id": "light3"})

        assert "light3" in coordinator.data["lights"]

    def test_on_websocket_message_missing_model_key(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ):
        """Test the WS message adapter warns and drops unparseable messages."""
        with caplog.at_level(logging.WARNING):
            coordinator._on_websocket_message({"id": "sensor5"})

        assert "missing modelKey" in caplog.text
        assert "sensor5" not in coordinator.data["sensors"]

    def test_on_websocket_message_not_a_dict(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ):
        """Test the WS message adapter warns on non-dict payloads."""
        with caplog.at_level(logging.WARNING):
            coordinator._on_websocket_message("not-a-dict")  # type: ignore[arg-type]

        assert "not a JSON object" in caplog.text

    def test_on_websocket_message_action_payload_split(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test the adapter merges a header/payload-split message.

        Some UniFi WebSocket surfaces split the model identity into an
        "action" header and the changed fields into "payload" (rather than
        a single flat object). The adapter must resolve modelKey/id and the
        field dict independently instead of picking one container and
        giving up, or a message shaped this way would silently drop every
        real frame.
        """
        coordinator._on_websocket_message(
            {
                "action": {"action": "update", "modelKey": "sensor", "id": "sensor6"},
                "payload": {"isOpened": True},
            }
        )

        assert coordinator.data["sensors"]["sensor6"]["id"] == "sensor6"
        assert coordinator.data["sensors"]["sensor6"]["isOpened"] is True

    def test_on_websocket_message_model_key_without_id(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ):
        """Test a resolvable modelKey with no id logs at debug, not silently."""
        with caplog.at_level(logging.DEBUG):
            coordinator._on_websocket_message({"modelKey": "sensor", "isOpened": True})

        assert "missing device id" in caplog.text
        assert coordinator.data["sensors"] == {}

    def test_on_websocket_message_warning_capped_after_first(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ):
        """Test repeated unparseable messages only WARNING once, then DEBUG."""
        with caplog.at_level(logging.DEBUG):
            coordinator._on_websocket_message({"id": "sensor7"})
            caplog.clear()
            coordinator._on_websocket_message({"id": "sensor8"})

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert warning_records == []
        assert any("sensor8" in r.getMessage() for r in debug_records)

    def test_on_websocket_message_does_not_clobber_type_with_envelope_verb(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """The envelope's top-level "type" (the action verb) must not
        overwrite a real device "type" field on a partial update frame.

        Regression test: `containers = [payload, action, item, message]`
        included the raw envelope unconditionally, so its "type": "update"
        leaked into the merged device dict whenever `item` was a partial
        delta that didn't re-send the unchanged "type" field - clobbering
        the previously-correct hardware model string. Measured in
        production flipping back and forth 49 times in 10 minutes for
        UFP-SENSE/USL-Entry-US/USL-Environmental-US sensors.
        """
        coordinator.data["sensors"]["sensor10"] = {
            "id": "sensor10",
            "modelKey": "sensor",
            "type": "UFP-SENSE",
            "isOpened": False,
        }

        # A partial WS update: the envelope says "update", and the item
        # only carries the field that actually changed (isOpened) - real
        # Protect delta frames are not guaranteed to re-send "type".
        coordinator._on_websocket_message(
            {
                "type": "update",
                "item": {"modelKey": "sensor", "id": "sensor10", "isOpened": True},
            }
        )

        assert coordinator.data["sensors"]["sensor10"]["type"] == "UFP-SENSE"
        assert coordinator.data["sensors"]["sensor10"]["isOpened"] is True

    def test_on_websocket_message_replaces_device_dict_not_mutates_in_place(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """A WS update must produce a new per-device dict, not mutate the
        old one in place - in-place mutation can make HA listeners that
        hold a stale reference miss the transition (see task 4).
        """
        original = {"id": "sensor11", "modelKey": "sensor", "isOpened": False}
        coordinator.data["sensors"]["sensor11"] = original

        coordinator._on_websocket_message(
            {"modelKey": "sensor", "id": "sensor11", "isOpened": True}
        )

        assert coordinator.data["sensors"]["sensor11"] is not original
        assert original["isOpened"] is False
        assert coordinator.data["sensors"]["sensor11"]["isOpened"] is True

    @pytest.mark.asyncio
    async def test_async_stop_websocket_noop_when_never_started(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test async_stop_websocket is safe when no task was ever started.

        It still calls ProtectWebSocket.stop() unconditionally (harmless -
        it just sets a flag), but must not touch websocket_task since it's
        None.
        """
        await coordinator.async_stop_websocket()

        assert coordinator.websocket_task is None

    @pytest.mark.asyncio
    async def test_async_stop_websocket_stops_and_awaits_real_task(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test async_stop_websocket stops the socket and awaits BOTH real
        tasks (devices and events - task 1 requires both to be stopped and
        awaited, mirroring the existing devices-only teardown ordering).
        """
        require_not_none(coordinator.protect_client).get_host_id = AsyncMock(
            return_value="nvr1"
        )

        async def _run_forever(*_args, **_kwargs):
            await asyncio.sleep(3600)

        coordinator._protect_websocket.subscribe_with_callback = AsyncMock(
            side_effect=_run_forever
        )
        await coordinator.async_start_websocket()
        devices_task = coordinator.websocket_task
        events_task = coordinator.events_websocket_task
        assert devices_task is not None
        assert events_task is not None
        assert not devices_task.done()
        assert not events_task.done()

        await coordinator.async_stop_websocket()

        coordinator._protect_websocket.stop.assert_called_once()
        assert devices_task.done()
        assert events_task.done()

    @pytest.mark.asyncio
    async def test_async_update_data_success(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test successful data fetch."""
        result = await coordinator._async_update_data()

        assert "cameras" in result
        assert "camera1" in result["cameras"]
        assert "lights" in result
        assert "light1" in result["lights"]
        assert "sensors" in result
        assert "sensor1" in result["sensors"]
        assert "nvrs" in result
        assert "nvr1" in result["nvrs"]
        assert "chimes" in result
        assert "chime1" in result["chimes"]
        assert "viewers" in result
        assert "viewer1" in result["viewers"]
        assert "liveviews" in result
        assert "liveview1" in result["liveviews"]
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_no_protect_client(
        self, coordinator_no_protect: UnifiInsightsProtectCoordinator
    ):
        """Test data fetch without protect client."""
        result = await coordinator_no_protect._async_update_data()

        # Should return empty data
        assert result["cameras"] == {}
        assert result["lights"] == {}

    @pytest.mark.asyncio
    async def test_async_update_data_sensors_error(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test data fetch with sensors error (should not fail)."""
        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            side_effect=Exception("Sensors failed")
        )

        result = await coordinator._async_update_data()

        # Other devices should still be fetched
        assert "camera1" in result["cameras"]
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_nvr_error(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test data fetch with NVR error (should not fail)."""
        require_not_none(coordinator.protect_client).nvr.get = AsyncMock(
            side_effect=Exception("NVR failed")
        )

        result = await coordinator._async_update_data()

        # Other devices should still be fetched
        assert "camera1" in result["cameras"]
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_chimes_error(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test data fetch with chimes error (should not fail)."""
        require_not_none(coordinator.protect_client).chimes.get_all = AsyncMock(
            side_effect=Exception("Chimes failed")
        )

        result = await coordinator._async_update_data()

        # Other devices should still be fetched
        assert "camera1" in result["cameras"]

    @pytest.mark.asyncio
    async def test_async_update_data_viewers_error(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test data fetch with viewers error (should not fail)."""
        require_not_none(coordinator.protect_client).viewers.get_all = AsyncMock(
            side_effect=Exception("Viewers failed")
        )

        result = await coordinator._async_update_data()

        # Other devices should still be fetched
        assert "camera1" in result["cameras"]

    @pytest.mark.asyncio
    async def test_async_update_data_liveviews_error(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test data fetch with liveviews error (should not fail)."""
        require_not_none(coordinator.protect_client).liveviews.get_all = AsyncMock(
            side_effect=Exception("Liveviews failed")
        )

        result = await coordinator._async_update_data()

        # Other devices should still be fetched
        assert "camera1" in result["cameras"]

    @pytest.mark.asyncio
    async def test_async_update_data_auth_error(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test data fetch with auth error."""
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            side_effect=UniFiAuthenticationError("Invalid API key")
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_connection_error(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test a sustained connection error eventually fails the poll.

        The first MAX_CONSECUTIVE_EMPTY_FETCHES polls are absorbed so a single
        blipped endpoint does not take every Protect entity unavailable; past
        that window the error propagates and the coordinator reports failure.
        """
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            side_effect=UniFiConnectionError("Connection refused")
        )

        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._async_update_data()

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    def test_device_frame_camera(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test handling camera device update."""
        send_device(coordinator, "camera", {"id": "camera2", "name": "Back Camera"})

        assert "camera2" in coordinator.data["cameras"]
        assert coordinator.data["cameras"]["camera2"]["name"] == "Back Camera"

    def test_device_frame_light(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test handling light device update."""
        send_device(coordinator, "light", {"id": "light2", "name": "Porch Light"})

        assert "light2" in coordinator.data["lights"]

    def test_device_frame_sensor(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test handling sensor device update."""
        send_device(coordinator, "sensor", {"id": "sensor2", "name": "Window Sensor"})

        assert "sensor2" in coordinator.data["sensors"]

    def test_device_frame_nvr(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test handling NVR device update."""
        send_device(coordinator, "nvr", {"id": "nvr2", "name": "NVR 2"})

        assert "nvr2" in coordinator.data["nvrs"]

    def test_device_frame_viewer(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test handling viewer device update."""
        send_device(coordinator, "viewer", {"id": "viewer2", "name": "Viewport 2"})

        assert "viewer2" in coordinator.data["viewers"]

    def test_device_frame_chime(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test handling chime device update."""
        send_device(coordinator, "chime", {"id": "chime2", "name": "Chime 2"})

        assert "chime2" in coordinator.data["chimes"]

    def test_device_frame_no_id(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test handling device update without ID."""
        send_device(coordinator, "camera", {"name": "No ID Camera"})

        # Should not add device without ID
        assert len(coordinator.data["cameras"]) == 0

    def test_device_frame_unknown_model_type(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Test an unknown device model type changes nothing and publishes
        nothing, so it neither notifies listeners nor re-arms the poll timer.
        """
        coordinator.data["cameras"] = {"camera1": {"id": "camera1", "name": "Cam 1"}}
        coordinator.data["lights"] = {"light1": {"id": "light1", "name": "Light 1"}}
        snapshot_cameras = dict(coordinator.data["cameras"])
        snapshot_lights = dict(coordinator.data["lights"])
        snapshot_sensors = dict(coordinator.data["sensors"])
        snapshot_nvrs = dict(coordinator.data["nvrs"])
        snapshot_viewers = dict(coordinator.data["viewers"])
        snapshot_chimes = dict(coordinator.data["chimes"])

        listener = MagicMock()
        coordinator.async_add_listener(listener)

        with patch.object(
            coordinator,
            "async_set_updated_data",
            wraps=coordinator.async_set_updated_data,
        ) as mock_set_data:
            # 1. Update without id returns early and does not notify
            send_device(coordinator, "unknown_type", {"name": "No ID"})
            send_device(coordinator, "unknown_type", {"id": "", "name": "Empty ID"})
            listener.assert_not_called()
            mock_set_data.assert_not_called()

            # 2. Update with id is ignored too: nothing to publish
            send_device(coordinator, "unknown_type", {"id": "unk1", "name": "Unknown"})
            listener.assert_not_called()
            mock_set_data.assert_not_called()

        # 3. Known collections are untouched
        assert coordinator.data["cameras"] == snapshot_cameras
        assert coordinator.data["lights"] == snapshot_lights
        assert coordinator.data["sensors"] == snapshot_sensors
        assert coordinator.data["nvrs"] == snapshot_nvrs
        assert coordinator.data["viewers"] == snapshot_viewers
        assert coordinator.data["chimes"] == snapshot_chimes

    def test_event_frame_motion(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test handling motion event."""
        # First add a camera
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}

        send_event(
            coordinator,
            "motion",
            {
                "id": "event1",
                "device": "camera1",
                "start": RECENT_START,
                "end": None,
            },
        )

        assert "motion" in coordinator.data["events"]
        assert coordinator.data["cameras"]["camera1"]["lastMotionStart"] == RECENT_START
        assert coordinator.data["cameras"]["camera1"]["lastMotionEnd"] is None

    def test_event_frame_smart_detect(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test handling smart detection event."""
        # First add a camera
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}

        send_event(
            coordinator,
            "smartDetectZone",
            {
                "id": "event2",
                "device": "camera1",
                "smartDetectTypes": ["person", "vehicle"],
                "start": 1234567890,
                "end": None,
            },
        )

        assert coordinator.data["cameras"]["camera1"]["lastSmartDetectTypes"] == [
            "person",
            "vehicle",
        ]

    def test_event_frame_doorbell_ring(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test handling doorbell ring event."""
        # First add a camera
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Doorbell"}

        send_event(
            coordinator,
            "ring",
            {
                "id": "event3",
                "device": "camera1",
                "start": 1234567890,
                "end": None,
            },
        )

        assert coordinator.data["cameras"]["camera1"]["lastRingStart"] == 1234567890
        assert coordinator.data["cameras"]["camera1"]["lastRingEnd"] is None

    def test_event_frame_light_motion(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test handling light motion event."""
        # First add a light
        coordinator.data["lights"]["light1"] = {"id": "light1", "name": "Test"}

        send_event(
            coordinator,
            "motion",
            {
                "id": "event4",
                "device": "light1",
                "start": 1234567890,
                "end": None,
            },
        )

        assert coordinator.data["lights"]["light1"]["lastMotionStart"] == 1234567890

    def test_event_frame_no_id(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test handling event without ID."""
        initial_events = dict(coordinator.data["events"])
        send_event(coordinator, "motion", {"device": "camera1"})

        # Events should not change without event ID
        assert coordinator.data["events"] == initial_events

    def test_event_frame_event_id_semantics(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Test event ID semantics: missing/empty IDs dropped, distinct IDs accumulate,
        repeated IDs replace, notification count matches accepted events."""
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        with patch.object(
            coordinator,
            "async_update_listeners",
            wraps=coordinator.async_update_listeners,
        ) as notify:
            # 1. Missing or empty event ID is dropped and produces no notification
            send_event(coordinator, "motion", {"device": "camera1"})
            send_event(coordinator, "motion", {"id": "", "device": "camera1"})
            assert coordinator.data["events"].get("motion", {}) == {}
            notify.assert_not_called()
            listener.assert_not_called()

            # 2. Distinct IDs accumulate and notify once per accepted event
            send_event(
                coordinator,
                "motion",
                {"id": "ev1", "device": "camera1", "start": RECENT_START},
            )
            assert notify.call_count == 1
            assert listener.call_count == 1
            assert coordinator.data["events"]["motion"]["ev1"] == {
                "type": "motion",
                "id": "ev1",
                "device": "camera1",
                "start": RECENT_START,
            }

            send_event(
                coordinator,
                "motion",
                {"id": "ev2", "device": "camera1", "start": RECENT_START + 1},
            )
            assert notify.call_count == 2
            assert listener.call_count == 2
            assert set(coordinator.data["events"]["motion"].keys()) == {"ev1", "ev2"}
            assert coordinator.data["events"]["motion"]["ev2"] == {
                "type": "motion",
                "id": "ev2",
                "device": "camera1",
                "start": RECENT_START + 1,
            }

            # 3. Repeated ID replaces previous event and notifies
            send_event(
                coordinator, "motion", {"id": "ev1", "device": "camera1", "end": 400}
            )
            assert notify.call_count == 3
            assert listener.call_count == 3
            assert set(coordinator.data["events"]["motion"].keys()) == {"ev1", "ev2"}
            assert coordinator.data["events"]["motion"]["ev1"] == {
                "type": "motion",
                "id": "ev1",
                "device": "camera1",
                "end": 400,
            }

    def test_get_camera_existing(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test getting existing camera."""
        coordinator.data["cameras"] = {"camera1": {"id": "camera1", "name": "Test"}}
        result = coordinator.get_camera("camera1")
        assert result == {"id": "camera1", "name": "Test"}

    def test_get_camera_missing(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test getting missing camera."""
        result = coordinator.get_camera("nonexistent")
        assert result is None

    def test_get_light_existing(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test getting existing light."""
        coordinator.data["lights"] = {"light1": {"id": "light1", "name": "Test"}}
        result = coordinator.get_light("light1")
        assert result == {"id": "light1", "name": "Test"}

    def test_get_light_missing(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test getting missing light."""
        result = coordinator.get_light("nonexistent")
        assert result is None

    def test_get_sensor_existing(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test getting existing sensor."""
        coordinator.data["sensors"] = {"sensor1": {"id": "sensor1", "name": "Test"}}
        result = coordinator.get_sensor("sensor1")
        assert result == {"id": "sensor1", "name": "Test"}

    def test_get_sensor_missing(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test getting missing sensor."""
        result = coordinator.get_sensor("nonexistent")
        assert result is None

    def test_get_nvr_existing(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test getting existing NVR."""
        coordinator.data["nvrs"] = {"nvr1": {"id": "nvr1", "name": "Test"}}
        result = coordinator.get_nvr("nvr1")
        assert result == {"id": "nvr1", "name": "Test"}

    def test_get_nvr_missing(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test getting missing NVR."""
        result = coordinator.get_nvr("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_device_eviction(
        self, hass: HomeAssistant, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test stale device cleanup."""
        # A poll saw both cameras.
        coordinator.data["cameras"] = {
            "camera1": {"id": "camera1"},
            "camera2": {"id": "camera2"},
        }
        _poll_completed(coordinator)

        # Current cameras only have camera1
        coordinator.data["cameras"] = {"camera1": {"id": "camera1"}}

        # Mock device registry
        with patch(
            "custom_components.unifi_insights.coordinators.base.dr.async_get"
        ) as mock_registry:
            mock_device = MagicMock()
            mock_device.id = "device_entry_id"
            set_mock_device_lookup(mock_registry.return_value, mock_device)

            # Removal waits out the MAX_CONSECUTIVE_MISSING_POLLS grace window.
            _poll_completed(coordinator, MAX_CONSECUTIVE_MISSING_POLLS + 1)

            # camera2 should be marked for removal
            mock_registry.return_value.async_update_device.assert_called()

    def test_websocket_construction_does_not_start_task(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ):
        """Test constructing the coordinator never starts the WebSocket task.

        Starting requires an await (to resolve host_id), so it must happen via
        async_start_websocket(), not as a side effect of __init__.
        """
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()

        coordinator = UnifiInsightsProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )
        assert coordinator.websocket_task is None

    def test_event_frame_unknown_device(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test handling event for unknown device - no device_id match."""
        # Don't add any cameras/lights/sensors
        coordinator.data["cameras"] = {}
        coordinator.data["lights"] = {}

        # Event with device_id that doesn't match any known device type
        send_event(
            coordinator,
            "motion",
            {
                "id": "event_unknown",
                "device": "unknown_device",
                "start": RECENT_START,
                "end": None,
            },
        )

        # Events should still be stored
        assert "motion" in coordinator.data["events"]
        assert "event_unknown" in coordinator.data["events"]["motion"]

    def test_event_frame_no_device_id(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test handling event without device field."""
        send_event(
            coordinator,
            "motion",
            {
                "id": "event_no_device",
                "start": RECENT_START,
                "end": None,
                # No "device" field
            },
        )

        # Events should still be stored
        assert "motion" in coordinator.data["events"]
        assert "event_no_device" in coordinator.data["events"]["motion"]

    def test_event_frame_resolves_camera_id_field(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test device id resolution tolerates "cameraId" (models/event.py's
        actual field name), not just the invented "device" key.

        UNVALIDATED (task 3): the real `Event` model
        (unifi_official_api.protect.models.event) has no generic "device" field - only
        `camera`/`cameraId` or `sensor`/`sensorId`. Before this fix,
        applying an event to its device was unreachable in practice
        because it only ever looked for "device".
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}

        send_event(
            coordinator,
            "motion",
            {"id": "event20", "cameraId": "camera1", "start": 111, "end": None},
        )

        assert coordinator.data["cameras"]["camera1"]["lastMotionStart"] == 111

    def test_event_frame_smart_detect_type_alias(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test the "smartDetect" event type (models/event.py's
        EventType.SMART_DETECT) is accepted alongside the original
        "smartDetectZone" comparison - UNVALIDATED (task 3) which of the
        two (or both) a real frame actually uses.
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}

        send_event(
            coordinator,
            "smartDetect",
            {
                "id": "event21",
                "device": "camera1",
                "smartDetectTypes": ["person"],
                "start": 111,
                "end": None,
            },
        )

        assert coordinator.data["cameras"]["camera1"]["lastSmartDetectTypes"] == [
            "person"
        ]

    # -- Task 1 / 3: the "events" WebSocket subscription adapter ------------

    def test_on_websocket_event_message_item_wrapper(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test the events adapter with fields nested under "item", mirroring
        the confirmed "devices" envelope shape (see _on_websocket_message).
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}

        coordinator._on_websocket_event_message(
            {
                "type": "add",
                "item": {
                    "type": "motion",
                    "id": "event30",
                    "camera": "camera1",
                    "start": RECENT_START,
                    "end": None,
                },
            }
        )

        assert "event30" in coordinator.data["events"]["motion"]
        assert coordinator.data["cameras"]["camera1"]["lastMotionStart"] == RECENT_START
        assert coordinator.data["cameras"]["camera1"]["lastMotionEnd"] is None

    def test_on_websocket_event_message_flat_frame(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test the events adapter with no item/payload/action wrapper - a
        flat frame's own top-level "type" is the real event type (there is
        no separate envelope to strip it from in this shape).
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}

        coordinator._on_websocket_event_message(
            {"type": "motion", "id": "event31", "camera": "camera1", "start": 777}
        )

        assert coordinator.data["cameras"]["camera1"]["lastMotionStart"] == 777

    def test_on_websocket_event_message_not_a_dict(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ):
        """Test the events adapter warns and drops non-dict payloads instead
        of raising (task 3: never raise into the WS callback).
        """
        with caplog.at_level(logging.WARNING):
            coordinator._on_websocket_event_message("not-a-dict")  # type: ignore[arg-type]

        assert "not a JSON object" in caplog.text

    def test_on_websocket_event_message_missing_type_warns_once_then_debug(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ):
        """Test an unparseable (missing event type) frame logs WARNING once,
        then DEBUG on repeat - mirrors the devices adapter's existing cap.
        """
        with caplog.at_level(logging.DEBUG):
            coordinator._on_websocket_event_message({"id": "event32"})
            caplog.clear()
            coordinator._on_websocket_event_message({"id": "event33"})

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert warning_records == []
        assert any("event33" in r.getMessage() for r in debug_records)

    @pytest.mark.parametrize(
        ("window_ms", "expected_delay"),
        [
            (1000, 1.0),
            (2500, 2.5),
            (None, 1.0),
            ("invalid", 1.0),
            (True, 1.0),
            (-1, 1.0),
            (float("inf"), 1.0),
            (10**1000, 1.0),
        ],
    )
    def test_on_websocket_event_rate_limit_defers_without_warning(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
        window_ms: Any,
        expected_delay: float,
    ) -> None:
        """A rate-limit frame defers requests without spending warning flags."""
        frame: dict[str, Any] = {
            "error": "Too many requests",
            "name": "TOO_MANY_REQUESTS_ERROR",
            "windowMs": window_ms,
            "limit": 10,
        }

        with caplog.at_level(logging.DEBUG):
            coordinator._on_websocket_event_message(frame)

        assert coordinator.protect_client is not None
        require_mock(
            coordinator.protect_client._defer_after_rate_limit
        ).assert_called_once_with(expected_delay)
        assert "requests deferred" in caplog.text
        assert "limit=10" in caplog.text
        assert not any(r.levelno == logging.WARNING for r in caplog.records)
        _assert_error_warning_unspent(coordinator, caplog)

    def test_on_websocket_event_rate_limit_in_nested_error(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An error object can carry its own rate-limit fields."""
        coordinator._on_websocket_event_message(
            {
                "payload": {
                    "error": {"name": "TOO_MANY_REQUESTS_ERROR", "windowMs": 1500}
                }
            }
        )

        assert coordinator.protect_client is not None
        require_mock(
            coordinator.protect_client._defer_after_rate_limit
        ).assert_called_once_with(1.5)
        _assert_error_warning_unspent(coordinator, caplog)

    def test_on_websocket_event_rate_limit_without_protect_client(
        self,
        coordinator_no_protect: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A late rate-limit frame is harmless after the Protect client is gone."""
        with caplog.at_level(logging.DEBUG):
            coordinator_no_protect._on_websocket_event_message(
                {
                    "error": "Too many requests",
                    "name": "TOO_MANY_REQUESTS_ERROR",
                    "windowMs": 1000,
                }
            )

        assert coordinator_no_protect.websocket_health["events"]["last_message_at"]
        assert not any(r.levelno == logging.WARNING for r in caplog.records)
        _assert_error_warning_unspent(coordinator_no_protect, caplog)

    def test_on_websocket_event_message_error_frame_warns_once_then_debug(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Other console errors still warn once, then log at DEBUG."""
        frame: dict[str, Any] = {
            "error": "Unexpected stream fault",
            "name": "UNEXPECTED_ERROR",
        }

        with caplog.at_level(logging.DEBUG):
            coordinator._on_websocket_event_message(dict(frame))
            first: list[logging.LogRecord] = [
                r for r in caplog.records if r.levelno == logging.WARNING
            ]
            assert any("reported an error" in r.getMessage() for r in first)
            assert not any("missing event type" in r.getMessage() for r in first)
            caplog.clear()
            coordinator._on_websocket_event_message(dict(frame))

        assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
        assert any(
            "reported an error" in r.getMessage()
            for r in caplog.records
            if r.levelno == logging.DEBUG
        )
        assert coordinator.protect_client is not None
        require_mock(
            coordinator.protect_client._defer_after_rate_limit
        ).assert_not_called()

    def test_on_websocket_event_message_error_frame_keeps_parse_warning(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test an error frame does not spend the unparseable-frame warning.

        The two share a stream but not a cause, so a genuinely malformed
        frame must still reach WARNING after an error frame has arrived.
        """
        with caplog.at_level(logging.DEBUG):
            coordinator._on_websocket_event_message(
                {
                    "error": "Too many requests",
                    "name": "TOO_MANY_REQUESTS_ERROR",
                    "windowMs": 1000,
                    "limit": 10,
                }
            )
            caplog.clear()
            coordinator._on_websocket_event_message({"id": "event41"})

        warnings: list[logging.LogRecord] = [
            r for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("missing event type" in r.getMessage() for r in warnings)

    def test_on_websocket_event_message_missing_id_is_dropped(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ):
        """Test a resolvable type with no id logs at debug and is dropped."""
        with caplog.at_level(logging.DEBUG):
            coordinator._on_websocket_event_message({"type": "motion"})

        assert "missing event id" in caplog.text
        assert coordinator.data["events"] == {}

    def test_on_websocket_event_message_never_raises_on_handler_error(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        caplog: pytest.LogCaptureFixture,
    ):
        """Test an exception while applying an event frame is logged and
        swallowed, not propagated into the WS callback (task 3: the events
        path has never run in production, so its schema assumptions must
        degrade safely rather than kill the reconnect loop).
        """
        with (
            patch.object(
                coordinator,
                "_apply",
                side_effect=RuntimeError("boom"),
            ),
            caplog.at_level(logging.ERROR),
        ):
            coordinator._on_websocket_event_message(
                {"type": "motion", "id": "event34", "device": "camera1"}
            )

        assert "unexpected error processing" in caplog.text.lower()

    # -- Task 2: bounded auto-off for the motion/smart-detect/ring latch ----

    def test_motion_latch_survives_within_timeout(
        self, coordinator: UnifiInsightsProtectCoordinator, freezer
    ):
        """Test reconciliation does not clear a latch that is still young."""
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        send_event(
            coordinator,
            "motion",
            {"id": "event40", "device": "camera1", "start": 1, "end": None},
        )

        freezer.tick(STALE_EVENT_TIMEOUT - timedelta(seconds=1))
        _sweep_latches(coordinator)

        assert coordinator.data["cameras"]["camera1"]["lastMotionEnd"] is None

    def test_motion_latch_auto_clears_after_stale_timeout(
        self, coordinator: UnifiInsightsProtectCoordinator, freezer
    ):
        """CRITICAL regression test: a dropped "end" frame must not latch
        `camera_motion` ON forever - it must self-clear after
        STALE_EVENT_TIMEOUT rather than trusting event pairing alone. A
        permanently-ON motion sensor in a home security system is worse
        than one that never fires (see coordinator module docstring).
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        send_event(
            coordinator,
            "motion",
            {"id": "event41", "device": "camera1", "start": 1, "end": None},
        )
        assert coordinator.data["cameras"]["camera1"]["lastMotionEnd"] is None

        freezer.tick(STALE_EVENT_TIMEOUT + timedelta(seconds=1))
        _sweep_latches(coordinator)

        assert coordinator.data["cameras"]["camera1"]["lastMotionEnd"] is not None

    def test_smart_detect_latch_auto_clears_and_resets_types(
        self, coordinator: UnifiInsightsProtectCoordinator, freezer
    ):
        """Test a stale smart-detect latch clears lastMotionEnd AND resets
        lastSmartDetectTypes, so person/vehicle/animal detection sensors
        also turn back off rather than staying latched on a stale type.
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        send_event(
            coordinator,
            "smartDetectZone",
            {
                "id": "event42",
                "device": "camera1",
                "smartDetectTypes": ["person"],
                "start": 1,
                "end": None,
            },
        )

        freezer.tick(STALE_EVENT_TIMEOUT + timedelta(seconds=1))
        _sweep_latches(coordinator)

        assert coordinator.data["cameras"]["camera1"]["lastMotionEnd"] is not None
        assert coordinator.data["cameras"]["camera1"]["lastSmartDetectTypes"] == []

    def test_smart_detect_event_tolerates_non_list_smart_detect_types(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test a non-list smartDetectTypes value (unconfirmed real shape -
        see class docstring) degrades to an empty list instead of storing
        garbage or raising.
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}

        send_event(
            coordinator,
            "smartDetectZone",
            {
                "id": "event46",
                "device": "camera1",
                "smartDetectTypes": "person",  # not a list
                "start": 1,
                "end": 2,
            },
        )

        assert coordinator.data["cameras"]["camera1"]["lastSmartDetectTypes"] == []

    def test_light_motion_latch_auto_clears_after_stale_timeout(
        self, coordinator: UnifiInsightsProtectCoordinator, freezer
    ):
        """Test the same bounded auto-off applies to light motion latches."""
        coordinator.data["lights"]["light1"] = {"id": "light1", "name": "Test"}
        send_event(
            coordinator,
            "motion",
            {"id": "event43", "device": "light1", "start": 1, "end": None},
        )

        freezer.tick(STALE_EVENT_TIMEOUT + timedelta(seconds=1))
        _sweep_latches(coordinator)

        assert coordinator.data["lights"]["light1"]["lastMotionEnd"] is not None

    def test_ring_latch_auto_clears_after_stale_timeout(
        self, coordinator: UnifiInsightsProtectCoordinator, freezer
    ):
        """Test the doorbell ring latch is bounded the same way as motion -
        it is exposed to the identical dropped-end-frame risk once the
        events stream is live.
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        send_event(
            coordinator,
            "ring",
            {"id": "event44", "device": "camera1", "start": 1, "end": None},
        )

        freezer.tick(STALE_EVENT_TIMEOUT + timedelta(seconds=1))
        _sweep_latches(coordinator)

        assert coordinator.data["cameras"]["camera1"]["lastRingEnd"] is not None

    def test_ring_latch_normal_end_event_clears_tracker(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test a normal paired ring "end" event pops the ring tracker
        immediately, mirroring the motion latch's normal-clear path.
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        send_event(
            coordinator,
            "ring",
            {"id": "event47", "device": "camera1", "start": 1, "end": None},
        )
        assert "camera1" in coordinator._events.latches["camera_ring"]

        send_event(
            coordinator,
            "ring",
            {"id": "event47", "device": "camera1", "start": 1, "end": 2},
        )

        assert coordinator.data["cameras"]["camera1"]["lastRingEnd"] == 2
        assert "camera1" not in coordinator._events.latches["camera_ring"]

    def test_latch_sweep_skips_tracker_for_removed_device(
        self, coordinator: UnifiInsightsProtectCoordinator, freezer
    ):
        """Test reconciliation for a stale tracker entry whose device has
        since disappeared (e.g. removed by a REST poll rebuild) just drops
        the tracker entry instead of raising or recreating the device.
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        send_event(
            coordinator,
            "motion",
            {"id": "event48", "device": "camera1", "start": 1, "end": None},
        )
        assert "camera1" in coordinator._events.latches["camera_motion"]

        # Simulate the camera vanishing (e.g. a REST poll rebuild that no
        # longer includes it) before the timeout elapses.
        coordinator.data = {**coordinator.data, "cameras": {}}

        freezer.tick(STALE_EVENT_TIMEOUT + timedelta(seconds=1))
        _sweep_latches(coordinator)

        assert "camera1" not in coordinator._events.latches["camera_motion"]
        assert "camera1" not in coordinator.data["cameras"]

    def test_motion_latch_normal_end_event_clears_without_reconciliation(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test a normal paired "end" event clears the latch immediately -
        reconciliation is a safety net, not the primary clearing path.
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        send_event(
            coordinator,
            "motion",
            {"id": "event45", "device": "camera1", "start": 1, "end": None},
        )
        assert "camera1" in coordinator._events.latches["camera_motion"]

        send_event(
            coordinator,
            "motion",
            {"id": "event45", "device": "camera1", "start": 1, "end": 2},
        )

        assert coordinator.data["cameras"]["camera1"]["lastMotionEnd"] == 2
        assert "camera1" not in coordinator._events.latches["camera_motion"]

    @pytest.mark.asyncio
    async def test_latch_sweep_runs_on_periodic_poll(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test the periodic REST poll also reconciles stale latches,
        rather than relying solely on event pairing.
        """
        with patch.object(
            coordinator, "_apply", wraps=coordinator._apply
        ) as mock_apply:
            await coordinator._async_update_data()

        inputs = [call.args[0] for call in mock_apply.call_args_list]
        assert inputs.count(PollStarted()) == 1
        # Before the first fetch, so it runs against the pre-poll cameras.
        assert inputs[0] == PollStarted()

    def test_latch_sweep_runs_on_websocket_reconnect(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test a WS (re)connect triggers reconciliation - a missed "end"
        frame during the outage would otherwise wait for the next poll.

        Applies per-stream (the callback now needs a `stream` argument -
        see review finding 1), but a reconnect of EITHER subscription
        still triggers reconciliation, matching the original behavior.
        """
        with patch.object(coordinator, "_apply") as mock_apply:
            coordinator._on_websocket_connection_state_change("devices", connected=True)

        mock_apply.assert_called_once_with(StreamReconnected("devices"))

        with patch.object(coordinator, "_apply") as mock_apply_disconnect:
            coordinator._on_websocket_connection_state_change(
                "devices", connected=False
            )

        mock_apply_disconnect.assert_not_called()

    # -- Task 5: WebSocket health signal -------------------------------------

    def test_websocket_health_initial_state(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test the WS health signal starts unconnected/unknown, per stream
        and in the top-level roll-up.
        """
        assert coordinator.websocket_health == {
            "connected": False,
            "last_message_at": None,
            "devices": {"connected": False, "last_message_at": None},
            "events": {"connected": False, "last_message_at": None},
        }

    def test_websocket_health_updates_on_devices_frame(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test an inbound devices frame marks only the devices stream
        connected - even an unparseable one, since receiving anything is
        evidence that stream's wire is alive. The top-level roll-up must
        NOT report overall-healthy off of one stream alone (review finding
        1): the events subscription is still unknown/disconnected here.
        """
        coordinator._on_websocket_message({"id": "sensor50"})

        assert coordinator.websocket_health["devices"]["connected"] is True
        assert coordinator.websocket_health["devices"]["last_message_at"] is not None
        assert coordinator.websocket_health["events"]["connected"] is False
        assert coordinator.websocket_health["events"]["last_message_at"] is None

        assert coordinator.websocket_health["connected"] is False
        # The top-level "any wire alive" timestamp still advances.
        assert coordinator.websocket_health["last_message_at"] is not None

    def test_websocket_health_updates_on_events_frame(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test an inbound events frame marks only the events stream
        connected, mirroring the devices case above.
        """
        coordinator._on_websocket_event_message({"id": "event51"})

        assert coordinator.websocket_health["events"]["connected"] is True
        assert coordinator.websocket_health["events"]["last_message_at"] is not None
        assert coordinator.websocket_health["devices"]["connected"] is False

        assert coordinator.websocket_health["connected"] is False
        assert coordinator.websocket_health["last_message_at"] is not None

    def test_websocket_health_reflects_connection_state_changes(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test the connection-state callback drives per-stream state, and
        the top-level roll-up only reports connected once BOTH streams are.
        """
        coordinator._on_websocket_connection_state_change("devices", connected=True)
        assert coordinator.websocket_health["devices"]["connected"] is True
        assert (
            coordinator.websocket_health["connected"] is False
        )  # events not connected yet

        coordinator._on_websocket_connection_state_change("events", connected=True)
        assert coordinator.websocket_health["events"]["connected"] is True
        assert (
            coordinator.websocket_health["connected"] is True
        )  # both streams now connected

        coordinator._on_websocket_connection_state_change("devices", connected=False)
        assert coordinator.websocket_health["devices"]["connected"] is False
        assert coordinator.websocket_health["events"]["connected"] is True
        assert coordinator.websocket_health["connected"] is False

    def test_on_devices_connection_state_change_updates_devices_stream_only(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test the bound wrapper registered as the devices subscription's
        `on_connection_state_change` callback (see `async_start_websocket`)
        updates only the devices stream's health entry.
        """
        coordinator._on_devices_connection_state_change(connected=True)

        assert coordinator.websocket_health["devices"]["connected"] is True
        assert coordinator.websocket_health["events"]["connected"] is False

    def test_on_events_connection_state_change_updates_events_stream_only(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test the bound wrapper registered as the events subscription's
        `on_connection_state_change` callback updates only the events
        stream's health entry.
        """
        coordinator._on_events_connection_state_change(connected=True)

        assert coordinator.websocket_health["events"]["connected"] is True
        assert coordinator.websocket_health["devices"]["connected"] is False

    def test_websocket_health_detects_half_dead_pair(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """CRITICAL regression test (review finding 1, Major): a chatty
        devices subscription must never mask a hung/disconnected events
        subscription in `websocket_health`.

        This health signal exists specifically to catch "motion silently
        stopped working for days and nobody noticed" - that failure mode is
        exactly an NVR restart where the devices stream reconnects cleanly
        and keeps delivering frames while the events stream hangs half-open
        (no error, no close frame, just blocked forever). Before this fix,
        both subscriptions wrote the same shared `_ws_connected`/
        `_last_ws_message` fields, so devices traffic alone kept
        `websocket_health` reporting `connected: True` with a fresh
        `last_message_at` while motion detection was silently dead.
        """
        # Devices subscription connects and stays chatty.
        coordinator._on_websocket_connection_state_change("devices", connected=True)
        coordinator._on_websocket_message({"id": "sensor52", "modelKey": "sensor"})
        coordinator._on_websocket_message({"id": "sensor52", "modelKey": "sensor"})

        # Events subscription connected once, then hung - no more frames,
        # no disconnect callback (that's exactly what a half-open hang
        # looks like: nothing fires, `async for msg in ws` just blocks).
        coordinator._on_websocket_connection_state_change("events", connected=True)
        coordinator._on_websocket_event_message({"id": "event52", "type": "motion"})

        health = coordinator.websocket_health

        # Per-stream detail must show the events stream as connected but
        # its own traffic is what a human would inspect for staleness -
        # the critical assertion is that the top-level summary does not
        # paper over an events-side outage with devices-side traffic.
        assert health["devices"]["connected"] is True
        assert health["events"]["connected"] is True

        # Now the events subscription actually drops (half-open hang
        # eventually surfaces as a connection-state transition once the
        # heartbeat/reconnect logic in websocket.py notices) while devices
        # keeps flowing.
        coordinator._on_websocket_connection_state_change("events", connected=False)
        coordinator._on_websocket_message({"id": "sensor52", "modelKey": "sensor"})

        health = coordinator.websocket_health
        assert health["devices"]["connected"] is True
        assert health["events"]["connected"] is False
        # The overall roll-up must reflect the outage, not the chatty
        # devices stream alone.
        assert health["connected"] is False

    def test_missing_device_no_match(
        self, hass: HomeAssistant, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test stale device cleanup when device not found in registry."""
        # A poll saw one device of each of these families.
        coordinator.data["cameras"] = {"stale_camera": {"id": "stale_camera"}}
        coordinator.data["lights"] = {"stale_light": {"id": "stale_light"}}
        coordinator.data["viewers"] = {"stale_viewer": {"id": "stale_viewer"}}
        coordinator.data["chimes"] = {"stale_chime": {"id": "stale_chime"}}
        _poll_completed(coordinator)

        # Current data has nothing
        coordinator.data["cameras"] = {}
        coordinator.data["lights"] = {}
        coordinator.data["viewers"] = {}
        coordinator.data["chimes"] = {}

        # Mock device registry to return None (device not found)
        with patch(
            "custom_components.unifi_insights.coordinators.base.dr.async_get"
        ) as mock_registry:
            set_mock_device_lookup(mock_registry.return_value, None)

            # Poll past the grace window so eviction is actually attempted,
            # then fall through both identifier patterns without a match.
            _poll_completed(coordinator, MAX_CONSECUTIVE_MISSING_POLLS + 1)

            # No device updates should happen (nothing found)
            mock_registry.return_value.async_update_device.assert_not_called()
            assert mock_device_lookup_method(
                mock_registry.return_value, coordinator.config_entry.entry_id
            ).called

    @pytest.mark.asyncio
    async def test_fetch_sensors_error(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test sensor fetch handles errors gracefully."""
        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            side_effect=Exception("Sensors error")
        )

        # Call the internal method directly
        await coordinator._fetch_sensors()

        # Should not raise, sensors should remain empty
        assert coordinator.data["sensors"] == {}

    @pytest.mark.asyncio
    async def test_fetch_sensors_preserves_cache_on_empty(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test sensor fetch preserves cached sensors across transient empty polls."""
        cached = {"sensor1": {"id": "sensor1", "state": "CONNECTED"}}
        coordinator.data["sensors"] = cached
        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            return_value=[]
        )

        # Polls 1, 2, 3 should preserve cache
        for _ in range(3):
            await coordinator._fetch_sensors()
            assert "sensor1" in coordinator.data["sensors"]

    @pytest.mark.asyncio
    async def test_fetch_sensors_clears_cache_after_max_consecutive_empty(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test sensor cache is cleared after MAX_CONSECUTIVE_EMPTY_FETCHES polls."""
        cached = {"sensor1": {"id": "sensor1", "state": "CONNECTED"}}
        coordinator.data["sensors"] = cached
        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            return_value=[]
        )

        # 4 consecutive empty polls exceeds threshold of 3
        for _ in range(4):
            await coordinator._fetch_sensors()

        assert coordinator.data["sensors"] == {}

    @pytest.mark.asyncio
    async def test_fetch_sensors_preserves_cache_on_404(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test sensor fetch preserves cached sensors across transient 404s."""
        cached = {"sensor1": {"id": "sensor1", "state": "CONNECTED"}}
        coordinator.data["sensors"] = cached
        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            side_effect=UniFiNotFoundError("Not Found", 404)
        )

        for _ in range(3):
            await coordinator._fetch_sensors()
            assert "sensor1" in coordinator.data["sensors"]

    @pytest.mark.asyncio
    async def test_fetch_sensors_clears_cache_after_max_consecutive_404(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test sensor cache is cleared after consecutive 404s exceed threshold."""
        cached = {"sensor1": {"id": "sensor1", "state": "CONNECTED"}}
        coordinator.data["sensors"] = cached
        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            side_effect=UniFiNotFoundError("Not Found", 404)
        )

        for _ in range(4):
            await coordinator._fetch_sensors()

        assert coordinator.data["sensors"] == {}

    @pytest.mark.asyncio
    async def test_fetch_sensors_404_when_no_sensors_configured(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test sensor fetch 404 leaves dict empty without error."""
        coordinator.data["sensors"] = {}
        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            side_effect=UniFiNotFoundError("Not Found", 404)
        )

        await coordinator._fetch_sensors()
        assert coordinator.data["sensors"] == {}

    async def test_rest_answer_handles_non_dict_existing(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """An empty answer over a collection that is not a dict leaves it empty."""
        coordinator.data["cameras"] = None
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            return_value=[]
        )
        require_not_none(coordinator.protect_client).cameras.last_result_complete = True
        await coordinator._fetch_cameras()
        assert coordinator.data["cameras"] == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("collection", "client_attr", "client_method", "fetch_method"),
        [
            ("cameras", "cameras", "get_all", "_fetch_cameras"),
            ("lights", "lights", "get_all", "_fetch_lights"),
            ("nvrs", "nvr", "get", "_fetch_nvr"),
            ("chimes", "chimes", "get_all", "_fetch_chimes"),
            ("viewers", "viewers", "get_all", "_fetch_viewers"),
        ],
    )
    async def test_fetch_endpoints_preserve_cache_on_404(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        collection: Family,
        client_attr: str,
        client_method: str,
        fetch_method: str,
    ):
        """Test fetch methods handle UniFiNotFoundError (404) by preserving cache."""
        device_id = f"{collection}_1"
        cached = {device_id: {"id": device_id}}
        coordinator.data[collection] = cached
        setattr(
            getattr(coordinator.protect_client, client_attr),
            client_method,
            AsyncMock(side_effect=UniFiNotFoundError("Not Found", 404)),
        )
        await getattr(coordinator, fetch_method)()
        assert device_id in coordinator.data[collection]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("collection", ["sensors", "nvrs", "chimes", "viewers"])
    async def test_fetch_preserves_cache_across_persistent_errors(
        self,
        hass: HomeAssistant,
        coordinator: UnifiInsightsProtectCoordinator,
        collection: Family,
    ):
        """Test a persistent fetch error never evicts devices or registry entries.

        An error means "we could not ask", not "the server says they are gone",
        so it must not feed the empty-response eviction counter. When it did,
        roughly two minutes of HTTP 500s cleared the collection and
        the registry clean-up then removed every device in it from the device
        registry, losing area assignments and any automation keyed on device_id.
        """
        # collection -> (protect_client attribute, its fetch method, coordinator fetch)
        endpoints = {
            "sensors": ("sensors", "get_all", "_fetch_sensors"),
            "nvrs": ("nvr", "get", "_fetch_nvr"),
            "chimes": ("chimes", "get_all", "_fetch_chimes"),
            "viewers": ("viewers", "get_all", "_fetch_viewers"),
        }
        client_attr, client_method, fetch_method = endpoints[collection]

        device_id = f"{collection}_device1"
        coordinator.data[collection] = {device_id: {"id": device_id}}
        _poll_completed(coordinator)
        setattr(
            getattr(coordinator.protect_client, client_attr),
            client_method,
            AsyncMock(side_effect=Exception("500 Internal Server Error")),
        )

        # Well past MAX_CONSECUTIVE_EMPTY_FETCHES: errors have no threshold.
        for _ in range(6):
            await getattr(coordinator, fetch_method)()
            assert device_id in coordinator.data[collection]

        assert coordinator._events.families[collection].empty_fetches == 0

        with patch(
            "custom_components.unifi_insights.coordinators.base.dr.async_get"
        ) as mock_registry:
            set_mock_device_lookup(mock_registry.return_value, MagicMock())
            _poll_completed(coordinator)
            mock_registry.return_value.async_update_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_nvr_missing_id_clears_cache_after_max_consecutive(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test NVR response without id is treated as empty."""
        cached = {"nvr1": {"id": "nvr1", "name": "NVR"}}
        coordinator.data["nvrs"] = cached
        mock_model = MagicMock()
        coordinator._model_to_dict = MagicMock(return_value={"name": "No ID"})
        require_not_none(coordinator.protect_client).nvr.get = AsyncMock(
            return_value=mock_model
        )

        for _ in range(3):
            await coordinator._fetch_nvr()
            assert "nvr1" in coordinator.data["nvrs"]

        await coordinator._fetch_nvr()
        assert coordinator.data["nvrs"] == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "empty_response",
        [
            None,
            {},
            {"name": "No ID NVR"},
        ],
        ids=["none", "empty_dict", "id_less_record"],
    )
    async def test_fetch_nvr_empty_responses_preserve_then_clear_cache(
        self, coordinator: UnifiInsightsProtectCoordinator, empty_response: Any
    ) -> None:
        """Test NVR responses with None, empty dict, or ID-less record preserve
        cache for MAX_CONSECUTIVE_EMPTY_FETCHES polls and then clear it,
        without mocking _model_to_dict.
        """
        cached = {"nvr1": {"id": "nvr1", "name": "NVR"}}
        coordinator.data["nvrs"] = dict(cached)
        require_not_none(coordinator.protect_client).nvr.get = AsyncMock(
            return_value=empty_response
        )

        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._fetch_nvr()
            assert "nvr1" in coordinator.data["nvrs"]

        await coordinator._fetch_nvr()
        assert coordinator.data["nvrs"] == {}

    @pytest.mark.asyncio
    async def test_fetch_cameras_preserves_cache_on_transient_empty(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test camera fetch preserves cache across transient empty polls."""
        cached = {"camera1": {"id": "camera1", "state": "CONNECTED"}}
        coordinator.data["cameras"] = cached
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            return_value=[]
        )

        await coordinator._fetch_cameras()
        assert "camera1" in coordinator.data["cameras"]

    @pytest.mark.asyncio
    async def test_fetch_cameras_clears_cache_after_max_consecutive_empty(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test camera cache is cleared after consecutive empty polls."""
        cached = {"camera1": {"id": "camera1", "state": "CONNECTED"}}
        coordinator.data["cameras"] = cached
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            return_value=[]
        )

        for _ in range(4):
            await coordinator._fetch_cameras()

        assert coordinator.data["cameras"] == {}

    @pytest.mark.asyncio
    async def test_fetch_lights_preserves_cache_on_transient_empty(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test light fetch preserves cached lights across transient empty responses."""
        cached = {"light1": {"id": "light1", "state": "CONNECTED"}}
        coordinator.data["lights"] = cached
        require_not_none(coordinator.protect_client).lights.get_all = AsyncMock(
            return_value=[]
        )

        await coordinator._fetch_lights()
        assert "light1" in coordinator.data["lights"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "err",
        [
            # The session-drop the controller serves as an HTML login page.
            pytest.param(
                UniFiResponseError("Response is not JSON", status_code=200),
                id="response_error",
            ),
            pytest.param(UniFiConnectionError("Connection reset"), id="connection"),
            pytest.param(UniFiTimeoutError("Timed out"), id="timeout"),
        ],
    )
    async def test_fetch_cameras_preserves_cache_on_transient_error(
        self, coordinator: UnifiInsightsProtectCoordinator, err: Exception
    ):
        """A transient camera fetch error must not fail the whole Protect poll.

        `_fetch_cameras` runs first, so letting the error escape aborts every
        later fetcher and raises `UpdateFailed`, marking all Protect entities
        unavailable for that cycle.
        """
        cached = {"camera1": {"id": "camera1", "state": "CONNECTED"}}
        coordinator.data["cameras"] = cached
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            side_effect=err
        )

        await coordinator._fetch_cameras()

        assert "camera1" in coordinator.data["cameras"]

    @pytest.mark.asyncio
    async def test_fetch_lights_preserves_cache_on_transient_error(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """A transient light fetch error must not fail the whole Protect poll."""
        cached = {"light1": {"id": "light1", "state": "CONNECTED"}}
        coordinator.data["lights"] = cached
        require_not_none(coordinator.protect_client).lights.get_all = AsyncMock(
            side_effect=UniFiResponseError("Response is not JSON", status_code=200)
        )

        await coordinator._fetch_lights()

        assert "light1" in coordinator.data["lights"]

    @pytest.mark.asyncio
    async def test_fetch_cameras_transient_error_does_not_advance_eviction(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """A fetch error is not evidence a camera was removed, unlike an empty poll.

        An empty response clears the cache after MAX_CONSECUTIVE_EMPTY_FETCHES so
        genuinely removed devices are cleaned up. An error says nothing about the
        device set, so it must not advance that counter: errored polls followed by
        a single empty one must still preserve the cache.
        """
        cached = {"camera1": {"id": "camera1", "state": "CONNECTED"}}
        coordinator.data["cameras"] = cached
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            side_effect=UniFiResponseError("Bad gateway", status_code=502)
        )
        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._fetch_cameras()

        # First clean poll after the outage genuinely returns no cameras.
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            return_value=[]
        )
        await coordinator._fetch_cameras()

        assert "camera1" in coordinator.data["cameras"]

    @pytest.mark.asyncio
    async def test_sustained_camera_fetch_errors_fail_the_poll(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Preservation is bounded: a lasting outage must mark Protect unavailable.

        Serving a warm cache forever would leave every Protect entity reporting
        stale state through a controller outage, which is worse than the flapping
        the bounded window prevents.
        """
        coordinator.data["cameras"] = {"camera1": {"id": "camera1"}}
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            side_effect=UniFiResponseError("Response is not JSON", status_code=200)
        )

        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._fetch_cameras()

        with pytest.raises(UniFiResponseError):
            await coordinator._fetch_cameras()

    @pytest.mark.asyncio
    async def test_camera_fetch_error_counter_resets_on_handled_404(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """A 404 is a healthy answer, so it breaks the transient-error streak.

        The endpoint replied, which is evidence the session is alive. Without
        the reset, three absorbed errors plus a 404 plus one more error would
        fail the poll on errors that were never consecutive.
        """
        coordinator.data["cameras"] = {"camera1": {"id": "camera1"}}
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            side_effect=UniFiResponseError("Blip", status_code=502)
        )
        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._fetch_cameras()

        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            side_effect=UniFiNotFoundError("No cameras configured", 404)
        )
        await coordinator._fetch_cameras()

        # Streak was broken, so this error is the first of a new window.
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            side_effect=UniFiResponseError("Blip", status_code=502)
        )
        await coordinator._fetch_cameras()

    @pytest.mark.asyncio
    async def test_light_fetch_error_counter_resets_on_handled_404(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """See the camera case: a handled 404 resets the light error streak."""
        coordinator.data["lights"] = {"light1": {"id": "light1"}}
        require_not_none(coordinator.protect_client).lights.get_all = AsyncMock(
            side_effect=UniFiResponseError("Blip", status_code=502)
        )
        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._fetch_lights()

        require_not_none(coordinator.protect_client).lights.get_all = AsyncMock(
            side_effect=UniFiNotFoundError("No lights configured", 404)
        )
        await coordinator._fetch_lights()

        require_not_none(coordinator.protect_client).lights.get_all = AsyncMock(
            side_effect=UniFiResponseError("Blip", status_code=502)
        )
        await coordinator._fetch_lights()

    @pytest.mark.asyncio
    async def test_camera_fetch_error_counter_resets_on_success(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """A recovered poll re-arms the full preservation window."""
        coordinator.data["cameras"] = {"camera1": {"id": "camera1"}}
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            side_effect=UniFiResponseError("Blip", status_code=502)
        )
        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._fetch_cameras()

        camera = MagicMock()
        camera.model_dump = MagicMock(return_value={"id": "camera1", "name": "Cam"})
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            return_value=[camera]
        )
        await coordinator._fetch_cameras()

        # Window is re-armed, so the next error is absorbed rather than raised.
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            side_effect=UniFiResponseError("Blip", status_code=502)
        )
        await coordinator._fetch_cameras()

        assert "camera1" in coordinator.data["cameras"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["_fetch_cameras", "_fetch_lights"])
    async def test_fetch_propagates_auth_error(
        self, coordinator: UnifiInsightsProtectCoordinator, method: str
    ):
        """An expired key must still reach the reauth flow, not be swallowed."""
        collection = "cameras" if method == "_fetch_cameras" else "lights"
        getattr(coordinator.protect_client, collection).get_all = AsyncMock(
            side_effect=UniFiAuthenticationError("API key expired")
        )

        with pytest.raises(UniFiAuthenticationError):
            await getattr(coordinator, method)()

    @pytest.mark.asyncio
    async def test_missing_device_preserves_devices_on_transient_empty(
        self, hass: HomeAssistant, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test stale device cleanup preserves devices during transient empty poll."""
        coordinator.data["sensors"] = {"sensor1": {"id": "sensor1"}}
        _poll_completed(coordinator)
        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            return_value=[]
        )

        # 1 transient empty fetch
        await coordinator._fetch_sensors()
        assert "sensor1" in coordinator.data["sensors"]

        with patch(
            "custom_components.unifi_insights.coordinators.base.dr.async_get"
        ) as mock_registry:
            mock_device = MagicMock()
            set_mock_device_lookup(mock_registry.return_value, mock_device)
            _poll_completed(coordinator)
            mock_registry.return_value.async_update_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_device_removes_device_after_max_consecutive_empty(
        self, hass: HomeAssistant, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test stale device cleanup removes devices after max empty polls."""
        coordinator.data["sensors"] = {"sensor1": {"id": "sensor1"}}
        _poll_completed(coordinator)
        require_not_none(coordinator.protect_client).sensors.get_all = AsyncMock(
            return_value=[]
        )

        # 4 consecutive empty polls
        for _ in range(4):
            await coordinator._fetch_sensors()

        assert coordinator.data["sensors"] == {}

        with patch(
            "custom_components.unifi_insights.coordinators.base.dr.async_get"
        ) as mock_registry:
            mock_device = MagicMock()
            mock_device.id = "sensor1_entry_id"
            set_mock_device_lookup(mock_registry.return_value, mock_device)
            # Removal waits out the MAX_CONSECUTIVE_MISSING_POLLS grace window.
            _poll_completed(coordinator, MAX_CONSECUTIVE_MISSING_POLLS + 1)
            mock_registry.return_value.async_update_device.assert_called()

    @pytest.mark.asyncio
    async def test_fetch_cameras_preserves_camera_skipped_by_validation(
        self, hass: HomeAssistant, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test a camera dropped on a ValidationError keeps its cached entry.

        `get_all()` skips an item whose payload will not parse - typically a
        field reshaped by a Protect release - and returns the rest. That short
        list is non-empty, so the bounded empty-response guard never engages
        and the grace window only delays the loss: the skip is deterministic
        and repeats on every poll. The collection must merge instead of
        replace while the endpoint reports the result incomplete.
        """
        coordinator.data["cameras"] = {
            "camera1": {"id": "camera1", "name": "Front"},
            "camera2": {"id": "camera2", "name": "Back"},
        }
        _poll_completed(coordinator)

        mock_camera = MagicMock()
        mock_camera.model_dump = MagicMock(
            return_value={"id": "camera1", "name": "Front"}
        )
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            return_value=[mock_camera]
        )
        require_not_none(
            coordinator.protect_client
        ).cameras.last_result_complete = False

        with patch(
            "custom_components.unifi_insights.coordinators.base.dr.async_get"
        ) as mock_registry:
            set_mock_device_lookup(mock_registry.return_value, MagicMock())
            for _ in range(MAX_CONSECUTIVE_MISSING_POLLS + 2):
                await coordinator._fetch_cameras()
                _poll_completed(coordinator)

            assert "camera2" in coordinator.data["cameras"]
            mock_registry.return_value.async_update_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_cameras_still_refreshes_data_when_result_incomplete(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test preserving a skipped camera does not stall the others' data.

        Merging must layer the fresh response over the cache, not fall back to
        it, or one unparseable camera would freeze every sibling's state.
        """
        coordinator.data["cameras"] = {
            "camera1": {"id": "camera1", "state": "DISCONNECTED"},
            "camera2": {"id": "camera2", "state": "CONNECTED"},
        }

        mock_camera = MagicMock()
        mock_camera.model_dump = MagicMock(
            return_value={"id": "camera1", "state": "CONNECTED"}
        )
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            return_value=[mock_camera]
        )
        require_not_none(
            coordinator.protect_client
        ).cameras.last_result_complete = False

        await coordinator._fetch_cameras()

        assert coordinator.data["cameras"]["camera1"]["state"] == "CONNECTED"
        assert coordinator.data["cameras"]["camera2"]["state"] == "CONNECTED"

    @pytest.mark.asyncio
    async def test_fetch_cameras_replaces_collection_when_result_complete(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test a complete response still drops cameras removed from Protect.

        The merge is scoped to incomplete results; a clean listing remains
        authoritative so genuinely unadopted cameras are cleaned up.
        """
        coordinator.data["cameras"] = {
            "camera1": {"id": "camera1"},
            "camera2": {"id": "camera2"},
        }

        mock_camera = MagicMock()
        mock_camera.model_dump = MagicMock(return_value={"id": "camera1"})
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            return_value=[mock_camera]
        )
        require_not_none(coordinator.protect_client).cameras.last_result_complete = True

        await coordinator._fetch_cameras()

        assert set(coordinator.data["cameras"]) == {"camera1"}

    @pytest.mark.asyncio
    async def test_fetch_cameras_preserves_cache_when_every_camera_fails_parsing(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test a schema break that drops every camera does not purge the cache.

        A Protect release reshaping a shared field fails validation for all
        cameras at once, so `get_all()` returns an empty list that looks
        exactly like "no cameras adopted". Left there, the bounded
        empty-response guard clears the collection a few polls later and hands
        every camera to the registry clean-up. An endpoint that reports the
        result incomplete must not advance that counter.
        """
        coordinator.data["cameras"] = {"camera1": {"id": "camera1"}}
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            return_value=[]
        )
        require_not_none(
            coordinator.protect_client
        ).cameras.last_result_complete = False

        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES + 3):
            await coordinator._fetch_cameras()

        assert "camera1" in coordinator.data["cameras"]
        assert coordinator._events.families["cameras"].empty_fetches == 0

    @pytest.mark.asyncio
    async def test_missing_device_defers_removal_on_brief_absence(
        self, hass: HomeAssistant, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test a device missing from one poll is not purged from the registry.

        A partial controller response - or an item that `get_all()` skipped on
        a ValidationError - drops a still-adopted device out of the collection.
        Acting on that single poll is irreversible: the registry entry, its
        area assignment and every automation keyed on device_id are lost.
        """
        _seen_by_a_poll(coordinator, "cam1", "cam2")
        coordinator.data["cameras"] = {"cam1": {"id": "cam1"}}

        with patch(
            "custom_components.unifi_insights.coordinators.base.dr.async_get"
        ) as mock_registry:
            set_mock_device_lookup(mock_registry.return_value, MagicMock())
            _poll_completed(coordinator, MAX_CONSECUTIVE_MISSING_POLLS)

            mock_registry.return_value.async_update_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_device_removes_device_after_sustained_absence(
        self, hass: HomeAssistant, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test a device absent past the grace window is still evicted.

        Guards the other half of the deferral: a device genuinely unadopted
        from Protect must not be tracked forever just because removal is no
        longer immediate (Gold stale-device requirement).
        """
        _seen_by_a_poll(coordinator, "cam1", "cam2")
        coordinator.data["cameras"] = {"cam1": {"id": "cam1"}}
        mock_device = MagicMock()
        mock_device.id = "registry_id_cam2"

        with patch(
            "custom_components.unifi_insights.coordinators.base.dr.async_get"
        ) as mock_registry:
            set_mock_device_lookup(mock_registry.return_value, mock_device)
            _poll_completed(coordinator, MAX_CONSECUTIVE_MISSING_POLLS + 1)

            mock_registry.return_value.async_update_device.assert_called_once_with(
                device_id="registry_id_cam2",
                remove_config_entry_id=coordinator.config_entry.entry_id,
            )

    @pytest.mark.asyncio
    async def test_missing_device_resets_absence_counter_on_return(
        self, hass: HomeAssistant, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test a device that reappears restarts its grace window.

        Without a reset, intermittent partial responses would accumulate
        across unrelated polls and eventually evict a healthy device.
        """
        _seen_by_a_poll(coordinator, "cam1", "cam2")

        with patch(
            "custom_components.unifi_insights.coordinators.base.dr.async_get"
        ) as mock_registry:
            set_mock_device_lookup(mock_registry.return_value, MagicMock())
            # Absent for one poll short of the threshold.
            coordinator.data["cameras"] = {"cam1": {"id": "cam1"}}
            _poll_completed(coordinator, MAX_CONSECUTIVE_MISSING_POLLS)

            # cam2 comes back, then goes missing again for the same span.
            coordinator.data["cameras"] = {
                "cam1": {"id": "cam1"},
                "cam2": {"id": "cam2"},
            }
            _poll_completed(coordinator)

            coordinator.data["cameras"] = {"cam1": {"id": "cam1"}}
            _poll_completed(coordinator, MAX_CONSECUTIVE_MISSING_POLLS)

            mock_registry.return_value.async_update_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_nvr_error(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test NVR fetch handles errors gracefully."""
        require_not_none(coordinator.protect_client).nvr.get = AsyncMock(
            side_effect=Exception("NVR error")
        )

        # Call the internal method directly
        await coordinator._fetch_nvr()

        # Should not raise, nvrs should remain empty
        assert coordinator.data["nvrs"] == {}

    @pytest.mark.asyncio
    async def test_fetch_cameras_processes_smart_detect_types(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test camera fetch extracts smartDetectTypes from featureFlags."""
        mock_camera = MagicMock()
        mock_camera.model_dump = MagicMock(
            return_value={
                "id": "camera1",
                "name": "Front Camera",
                "featureFlags": {
                    "smartDetectTypes": ["person", "vehicle"],
                },
                "isPtz": True,
            }
        )
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            return_value=[mock_camera]
        )

        await coordinator._fetch_cameras()

        assert "camera1" in coordinator.data["cameras"]
        assert coordinator.data["cameras"]["camera1"]["smartDetectTypes"] == [
            "person",
            "vehicle",
        ]
        assert coordinator.data["cameras"]["camera1"]["hasPtz"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            {"featureFlags": "not_a_dict"},
            {"feature_flags": "x"},
            {"featureFlags": "not_a_dict", "feature_flags": "x"},
            {"feature_flags": None},
            {"featureFlags": "not_a_dict", "feature_flags": None},
            {"feature_flags": []},
            {"featureFlags": "not_a_dict", "feature_flags": []},
            {"feature_flags": 5},
            {"featureFlags": "not_a_dict", "feature_flags": 5},
        ],
        ids=[
            "camel_non_dict",
            "snake_str_absent_camel",
            "snake_str_non_dict_camel",
            "snake_none_absent_camel",
            "snake_none_non_dict_camel",
            "snake_list_absent_camel",
            "snake_list_non_dict_camel",
            "snake_int_absent_camel",
            "snake_int_non_dict_camel",
        ],
    )
    async def test_fetch_cameras_non_dict_feature_flags(
        self, coordinator: UnifiInsightsProtectCoordinator, payload: dict[str, Any]
    ) -> None:
        """Test camera fetch handles non-dict featureFlags and
        snake-case feature_flags.
        """
        mock_camera = MagicMock()
        mock_camera.model_dump = MagicMock(
            return_value={
                "id": "camera2",
                "name": "Camera 2",
                **payload,
            }
        )
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            return_value=[mock_camera]
        )

        await coordinator._fetch_cameras()

        assert "camera2" in coordinator.data["cameras"]
        # Should default to empty list when featureFlags is not a dict
        assert coordinator.data["cameras"]["camera2"]["smartDetectTypes"] == []
        assert coordinator.data["cameras"]["camera2"]["featureFlags"] == {}

    @pytest.mark.asyncio
    async def test_fetch_cameras_valid_snake_case_feature_flags(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Test camera fetch normalises valid snake-case feature_flags into
        featureFlags.
        """
        mock_camera = MagicMock()
        mock_camera.model_dump = MagicMock(
            return_value={
                "id": "camera2",
                "name": "Camera 2",
                "feature_flags": {"smartDetectTypes": ["person"]},
            }
        )
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            return_value=[mock_camera]
        )

        await coordinator._fetch_cameras()

        assert "camera2" in coordinator.data["cameras"]
        assert coordinator.data["cameras"]["camera2"]["featureFlags"] == {
            "smartDetectTypes": ["person"]
        }
        assert coordinator.data["cameras"]["camera2"]["smartDetectTypes"] == ["person"]

    @pytest.mark.asyncio
    async def test_fetch_cameras_no_feature_flags(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test camera fetch handles missing featureFlags."""
        mock_camera = MagicMock()
        mock_camera.model_dump = MagicMock(
            return_value={
                "id": "camera3",
                "name": "Camera 3",
                # No featureFlags key
            }
        )
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            return_value=[mock_camera]
        )

        await coordinator._fetch_cameras()

        assert "camera3" in coordinator.data["cameras"]
        assert coordinator.data["cameras"]["camera3"]["smartDetectTypes"] == []

    @pytest.mark.asyncio
    async def test_fetch_cameras_rebuild_drops_motion_tracker_for_fieldless_camera(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        freezer,
        caplog: pytest.LogCaptureFixture,
    ):
        """Test a `_fetch_cameras()` rebuild pops the in-progress motion
        latch tracker for a camera whose rebuilt dict carries no
        `lastMotionStart` field, instead of leaving it to be "discovered"
        stale 5 minutes later.

        Regression test (review finding 2): the REST camera model never
        carries `lastMotionStart`/`lastMotionEnd` (those are only ever
        written by a paired WebSocket "start"/"end" event - see
        `protect_events`). Before this fix, a `_fetch_cameras()`
        rebuild silently cleared the latch's own fields without popping the
        tracker entry, so on virtually every real motion event the
        latch sweep would later "discover" the orphaned
        tracker and log a false "missed 'end' event?" warning for motion
        detection that was already correctly cleared ~4m30s earlier by the
        REST poll - flooding INFO logs during exactly the window someone is
        watching them to confirm a deploy worked.
        """
        # A live event-derived latch: WebSocket saw a "start" with no "end"
        # yet, so the tracker is armed - mirrors real motion detection.
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        send_event(
            coordinator,
            "motion",
            {"id": "event49", "device": "camera1", "start": 1, "end": None},
        )
        assert "camera1" in coordinator._events.latches["camera_motion"]
        assert coordinator.data["cameras"]["camera1"]["lastMotionStart"] == 1

        # The mock protect client's default camera fixture returns
        # "camera1" but with no lastMotionStart/lastMotionEnd fields at all
        # (matching the library REST model, unifi_official_api.protect.models.camera).
        await coordinator._fetch_cameras()

        assert "lastMotionStart" not in coordinator.data["cameras"]["camera1"]
        assert "camera1" not in coordinator._events.latches["camera_motion"]

        # 5+ minutes later, the periodic reconciliation safety net must not
        # find (and log about) a tracker that no longer exists.
        freezer.tick(STALE_EVENT_TIMEOUT + timedelta(seconds=1))
        with caplog.at_level(logging.INFO):
            _sweep_latches(coordinator)

        assert "missed 'end' event" not in caplog.text

    @pytest.mark.asyncio
    async def test_fetch_cameras_rebuild_drops_ring_tracker_for_fieldless_camera(
        self,
        coordinator: UnifiInsightsProtectCoordinator,
        freezer,
        caplog: pytest.LogCaptureFixture,
    ):
        """Test the identical fix also applies to the doorbell ring latch.

        The ring latch is stored on the same per-camera dict that
        `_fetch_cameras()` wholesale-replaces, so it is exposed to the
        identical dropped-tracker risk as the motion latch above.
        """
        coordinator.data["cameras"]["camera1"] = {"id": "camera1", "name": "Test"}
        send_event(
            coordinator,
            "ring",
            {"id": "event50", "device": "camera1", "start": 1, "end": None},
        )
        assert "camera1" in coordinator._events.latches["camera_ring"]

        await coordinator._fetch_cameras()

        assert "lastRingStart" not in coordinator.data["cameras"]["camera1"]
        assert "camera1" not in coordinator._events.latches["camera_ring"]

        freezer.tick(STALE_EVENT_TIMEOUT + timedelta(seconds=1))
        with caplog.at_level(logging.INFO):
            _sweep_latches(coordinator)

        assert "missed 'end' event" not in caplog.text

    @pytest.mark.asyncio
    async def test_async_update_data_response_error(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test a sustained response error eventually fails the poll.

        The first MAX_CONSECUTIVE_EMPTY_FETCHES polls are absorbed so a single
        blipped endpoint does not take every Protect entity unavailable; past
        that window the error propagates and the coordinator reports failure.
        """
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            side_effect=UniFiResponseError("Invalid response", status_code=400)
        )

        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._async_update_data()

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_timeout_error(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test a sustained timeout error eventually fails the poll.

        The first MAX_CONSECUTIVE_EMPTY_FETCHES polls are absorbed so a single
        blipped endpoint does not take every Protect entity unavailable; past
        that window the error propagates and the coordinator reports failure.
        """
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            side_effect=UniFiTimeoutError("Request timed out")
        )

        for _ in range(MAX_CONSECUTIVE_EMPTY_FETCHES):
            await coordinator._async_update_data()

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_generic_error(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test data fetch with generic error."""
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            side_effect=Exception("Unknown error")
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_fetch_lights_no_protect_client(
        self, coordinator_no_protect: UnifiInsightsProtectCoordinator
    ):
        """Test light fetch returns early without protect client."""
        await coordinator_no_protect._fetch_lights()
        # Should not raise, lights remain empty
        assert coordinator_no_protect.data["lights"] == {}

    @pytest.mark.asyncio
    async def test_fetch_sensors_no_protect_client(
        self, coordinator_no_protect: UnifiInsightsProtectCoordinator
    ):
        """Test sensor fetch returns early without protect client."""
        await coordinator_no_protect._fetch_sensors()
        assert coordinator_no_protect.data["sensors"] == {}

    @pytest.mark.asyncio
    async def test_fetch_nvr_no_protect_client(
        self, coordinator_no_protect: UnifiInsightsProtectCoordinator
    ):
        """Test NVR fetch returns early without protect client."""
        await coordinator_no_protect._fetch_nvr()
        assert coordinator_no_protect.data["nvrs"] == {}

    @pytest.mark.asyncio
    async def test_fetch_chimes_no_protect_client(
        self, coordinator_no_protect: UnifiInsightsProtectCoordinator
    ):
        """Test chime fetch returns early without protect client."""
        await coordinator_no_protect._fetch_chimes()
        assert coordinator_no_protect.data["chimes"] == {}

    @pytest.mark.asyncio
    async def test_fetch_viewers_no_protect_client(
        self, coordinator_no_protect: UnifiInsightsProtectCoordinator
    ):
        """Test viewer fetch returns early without protect client."""
        await coordinator_no_protect._fetch_viewers()
        assert coordinator_no_protect.data["viewers"] == {}

    @pytest.mark.asyncio
    async def test_fetch_liveviews_no_protect_client(
        self, coordinator_no_protect: UnifiInsightsProtectCoordinator
    ):
        """Test liveview fetch returns early without protect client."""
        await coordinator_no_protect._fetch_liveviews()
        assert coordinator_no_protect.data["liveviews"] == {}

    @pytest.mark.asyncio
    async def test_fetch_cameras_no_protect_client(
        self, coordinator_no_protect: UnifiInsightsProtectCoordinator
    ):
        """Test camera fetch returns early without protect client."""
        await coordinator_no_protect._fetch_cameras()
        assert coordinator_no_protect.data["cameras"] == {}

    @pytest.mark.asyncio
    async def test_async_stop_websocket_no_protect_websocket(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test async_stop_websocket when _protect_websocket is None."""
        coordinator._protect_websocket = None
        await coordinator.async_stop_websocket()
        assert coordinator.websocket_task is None
        assert coordinator.events_websocket_task is None
        assert coordinator._sensor_refresh_task is None
        assert coordinator._sensor_reconcile_task is None
        assert coordinator._sensor_reconnect_task is None

    def test_device_frame_doorlock(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test handling doorlock device raw dictionary merge."""
        coordinator.data["doorlocks"] = {
            "lock1": {"id": "lock1", "name": "Front Door", "state": "LOCKED"}
        }
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        send_device(
            coordinator, "doorlock", {"id": "lock1", "state": "UNLOCKED", "battery": 90}
        )

        # The frame's own modelKey is kept, as it is for a live frame.
        assert coordinator.data["doorlocks"]["lock1"] == {
            "modelKey": "doorlock",
            "id": "lock1",
            "name": "Front Door",
            "state": "UNLOCKED",
            "battery": 90,
        }
        listener.assert_called_once()

    def test_device_frame_viewport(self, coordinator: UnifiInsightsProtectCoordinator):
        """Test handling viewport device raw dictionary merge."""
        coordinator.data["viewports"] = {
            "vp1": {"id": "vp1", "name": "Viewport 1", "state": "ONLINE"}
        }
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        send_device(coordinator, "viewport", {"id": "vp1", "name": "Renamed Viewport"})

        assert coordinator.data["viewports"]["vp1"] == {
            "modelKey": "viewport",
            "id": "vp1",
            "name": "Renamed Viewport",
            "state": "ONLINE",
        }
        listener.assert_called_once()

    def test_normalize_camera_data_sparse_defensive_fallbacks(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Cover smart-detect and PTZ fallback arms with sparse dictionaries."""
        # 1. Sparse camera dictionary with legacy snake_case fields (defensive fallback)
        sparse_cam = {
            "id": "cam_sparse",
            "name": "Sparse Camera",
            "smart_detect_types": ["person", "vehicle"],
            "is_ptz": True,
        }
        normalized = normalize_camera(sparse_cam)
        assert normalized["smartDetectTypes"] == ["person", "vehicle"]
        assert normalized["isPtz"] is True

        # Sparse camera with legacy is_ptz=False fallback
        sparse_cam_no_ptz = {
            "id": "cam_sparse_no_ptz",
            "name": "Sparse Camera No PTZ",
            "is_ptz": False,
        }
        normalized_no_ptz = normalize_camera(sparse_cam_no_ptz)
        assert normalized_no_ptz["isPtz"] is False

        # 2. Real Camera model dump showing default fields suppress the fallbacks
        real_cam = Camera(id="cam_real", name="Real Camera", mac="AA:BB:CC:DD:EE:FF")
        real_dump = real_cam.model_dump(by_alias=True)
        real_dump["smart_detect_types"] = ["ignored_legacy"]
        real_dump["is_ptz"] = True
        normalized_real = normalize_camera(real_dump)
        assert normalized_real["smartDetectTypes"] == []
        assert normalized_real["isPtz"] is False

    async def test_prune_rebuilt_trackers_motion_and_ring(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """A camera rebuild drops only the latches whose start field it removed.

        A camera still carrying its start keeps its latch, and one missing from
        the answer is left to the latch sweep.
        """
        for camera_id in ("cam_survives", "cam_pruned", "cam_missing"):
            coordinator.data["cameras"][camera_id] = {"id": camera_id}
            for event_type in ("motion", "ring"):
                send_event(
                    coordinator,
                    event_type,
                    {
                        "id": f"{event_type}-{camera_id}",
                        "device": camera_id,
                        "start": 1,
                    },
                )
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            return_value=[
                _create_mock_model(
                    {
                        "id": "cam_survives",
                        "lastMotionStart": 1000,
                        "lastRingStart": 2000,
                    }
                ),
                _create_mock_model({"id": "cam_pruned"}),
            ]
        )
        require_not_none(coordinator.protect_client).cameras.last_result_complete = True

        await coordinator._fetch_cameras()

        for kind in ("camera_motion", "camera_ring"):
            assert set(coordinator._events.latches[kind]) == {
                "cam_survives",
                "cam_missing",
            }

    @pytest.mark.asyncio
    async def test_fetch_lights_unabsorbed_error_raises(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test _fetch_lights raises when errors exceed threshold."""
        protect_client = coordinator.protect_client
        assert protect_client is not None
        get_lights = AsyncMock(side_effect=UniFiConnectionError("Connection failure"))
        protect_client.lights.get_all = get_lights
        for expected_count in range(1, MAX_CONSECUTIVE_EMPTY_FETCHES + 1):
            await coordinator._fetch_lights()
            assert coordinator._events.families["lights"].fetch_errors == expected_count
        with pytest.raises(UniFiConnectionError, match="Connection failure"):
            await coordinator._fetch_lights()
        assert (
            coordinator._events.families["lights"].fetch_errors
            == MAX_CONSECUTIVE_EMPTY_FETCHES + 1
        )
        assert get_lights.await_count == MAX_CONSECUTIVE_EMPTY_FETCHES + 1

    @pytest.mark.asyncio
    async def test_fetch_collections_skip_records_without_id(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test Protect collection fetches skip ID-less records."""
        protect_client = coordinator.protect_client
        assert protect_client is not None
        protect_client.lights.get_all = AsyncMock(
            return_value=[
                Light.model_validate(
                    {"id": "light1", "mac": "00:11:22:33:44:01", "name": "Light 1"}
                ),
                Light.model_validate(
                    {"id": "", "mac": "00:11:22:33:44:02", "name": "NoIDLight"}
                ),
            ]
        )
        protect_client.lights.last_result_complete = True
        await coordinator._fetch_lights()
        assert list(coordinator.data["lights"].keys()) == ["light1"]
        protect_client.cameras.get_all = AsyncMock(
            return_value=[
                Camera.model_validate(
                    {"id": "camera1", "mac": "00:11:22:33:44:00", "name": "Camera 1"}
                ),
                Camera.model_validate(
                    {"id": "", "mac": "00:11:22:33:44:01", "name": "NoIDCamera"}
                ),
            ]
        )
        protect_client.cameras.last_result_complete = True
        await coordinator._fetch_cameras()
        assert list(coordinator.data["cameras"].keys()) == ["camera1"]
        protect_client.sensors.get_all = AsyncMock(
            return_value=[
                Sensor.model_validate(
                    {"id": "sensor1", "mac": "00:11:22:33:44:03", "name": "Sensor 1"}
                ),
                Sensor.model_validate(
                    {"id": "", "mac": "00:11:22:33:44:04", "name": "NoIDSensor"}
                ),
            ]
        )
        await coordinator._fetch_sensors()
        assert list(coordinator.data["sensors"].keys()) == ["sensor1"]
        protect_client.chimes.get_all = AsyncMock(
            return_value=[
                Chime.model_validate(
                    {"id": "chime1", "mac": "00:11:22:33:44:05", "name": "Chime 1"}
                ),
                Chime.model_validate(
                    {"id": "", "mac": "00:11:22:33:44:06", "name": "NoIDChime"}
                ),
            ]
        )
        protect_client.chimes.last_result_complete = True
        await coordinator._fetch_chimes()
        assert list(coordinator.data["chimes"].keys()) == ["chime1"]
        protect_client.viewers.get_all = AsyncMock(
            return_value=[
                Viewer.model_validate(
                    {
                        "id": "viewer1",
                        "mac": "00:11:22:33:44:07",
                        "state": "ONLINE",
                        "name": "Viewer 1",
                    }
                ),
                Viewer.model_validate(
                    {
                        "id": "",
                        "mac": "00:11:22:33:44:08",
                        "state": "ONLINE",
                        "name": "NoIDViewer",
                    }
                ),
            ]
        )
        protect_client.viewers.last_result_complete = True
        await coordinator._fetch_viewers()
        assert list(coordinator.data["viewers"].keys()) == ["viewer1"]
        protect_client.liveviews.get_all = AsyncMock(
            return_value=[
                LiveView.model_validate({"id": "liveview1", "name": "Liveview 1"}),
                LiveView.model_validate({"id": "", "name": "NoIDLiveview"}),
            ]
        )
        await coordinator._fetch_liveviews()
        assert list(coordinator.data["liveviews"].keys()) == ["liveview1"]

    @pytest.mark.asyncio
    async def test_async_refresh_sensors_task_cleared_mid_execution(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test _sensor_refresh_task cleared by production helper mid-execution."""
        entered_fetch = asyncio.Event()
        continue_fetch = asyncio.Event()

        async def mock_fetch():
            entered_fetch.set()
            await continue_fetch.wait()

        coordinator._fetch_sensors = mock_fetch

        refresh_task = asyncio.create_task(
            coordinator.async_refresh_sensors(notify=False)
        )
        await entered_fetch.wait()

        # Task was launched and assigned through production helper
        tracked_task = coordinator._sensor_refresh_task
        assert tracked_task is not None
        assert not tracked_task.done()

        # Cancellation clears slot before await resumes
        coordinator._cancel_sensor_background_task("_sensor_refresh_task")
        assert coordinator._sensor_refresh_task is None

        # Resume fetch and await completion (suppressing task cancellation)
        continue_fetch.set()
        with contextlib.suppress(asyncio.CancelledError):
            await refresh_task
        assert tracked_task.cancelled()
        assert coordinator._sensor_refresh_task is None

    @pytest.mark.asyncio
    async def test_async_refresh_sensors_completed_fetch_shutdown_race(
        self, coordinator: UnifiInsightsProtectCoordinator
    ) -> None:
        """Test completed-fetch shutdown race preserves newer fetch owner.

        Covers Protect coordinator 1162->1165: when _fetch_sensors finishes,
        the fetch Task transitions to done. Before the awaiting driver task
        (async_refresh_sensors) resumes on the event loop ready queue, a
        concurrent public async_stop_websocket task executes, sees that
        _sensor_refresh_task is already done, avoids cancelling it, and nulls
        the attribute reference via the production helper. Immediately following
        the stop task, a second async_refresh_sensors task executes, finds the
        cleared slot, and launches a new inner fetch Task through the production
        helper, becoming the legitimate new owner. When the original awaiting
        driver task then resumes, its finally block observes
        `self._sensor_refresh_task is task` as False (the newer fetch Task is
        not the original task) and bypasses nulling without error, preserving
        the newer owner's task in the slot.
        """
        entered_first_fetch = asyncio.Event()
        release_first_fetch = asyncio.Event()
        entered_second_fetch = asyncio.Event()
        release_second_fetch = asyncio.Event()
        fetch_tasks: list[asyncio.Task[Any]] = []
        stop_tasks: list[asyncio.Task[None]] = []
        second_refresh_tasks: list[asyncio.Task[None]] = []
        listener_calls: list[int] = []
        coordinator.async_add_listener(lambda: listener_calls.append(1))

        fetch_count = 0

        async def mock_fetch() -> None:
            nonlocal fetch_count
            fetch_count += 1
            current = asyncio.current_task()
            assert current is not None
            fetch_tasks.append(current)

            if fetch_count == 1:
                entered_first_fetch.set()
                await release_first_fetch.wait()
                # Queue public async_stop_websocket Task and second refresh Task
                # with eager_start=False before fetch returns without another await,
                # so stop_task and second_refresh_task are queued before inner fetch
                # completion queues driver resumption.
                stop_task = asyncio.create_task(
                    coordinator.async_stop_websocket(),
                    eager_start=False,
                )
                stop_tasks.append(stop_task)
                second_refresh_task = asyncio.create_task(
                    coordinator.async_refresh_sensors(notify=False),
                    eager_start=False,
                )
                second_refresh_tasks.append(second_refresh_task)
            else:
                entered_second_fetch.set()
                await release_second_fetch.wait()

        coordinator._fetch_sensors = mock_fetch

        driver_task: asyncio.Task[None] = asyncio.create_task(
            coordinator.async_refresh_sensors(notify=True)
        )
        await entered_first_fetch.wait()

        # Task was launched and assigned through production helper
        assert len(fetch_tasks) == 1
        inner_fetch_task = fetch_tasks[0]
        assert coordinator._sensor_refresh_task is inner_fetch_task
        assert not inner_fetch_task.done()

        # Unblock first fetch; fetch queues stop_task then second_refresh_task,
        # and returns without another await.
        release_first_fetch.set()

        await driver_task
        assert len(stop_tasks) == 1
        stop_task = stop_tasks[0]
        await stop_task
        assert len(second_refresh_tasks) == 1
        second_refresh_task = second_refresh_tasks[0]

        second_fetch_task: asyncio.Task[Any] | None = None
        try:
            # Assert second fetch has started and coordinator owns it
            assert entered_second_fetch.is_set()
            assert len(fetch_tasks) == 2
            second_fetch_task = fetch_tasks[1]
            assert coordinator._sensor_refresh_task is second_fetch_task
            assert not second_fetch_task.done()
            assert not second_fetch_task.cancelled()
            assert inner_fetch_task.done()
            assert not inner_fetch_task.cancelled()
            assert stop_task.done()
            assert not stop_task.cancelled()
            assert driver_task.done()
            assert not driver_task.cancelled()
            assert listener_calls == [1]
            assert coordinator.protect_client is not None
            assert coordinator.protect_client.websocket is not None
            stop_websocket = coordinator.protect_client.websocket.stop
            assert isinstance(stop_websocket, MagicMock)
            stop_websocket.assert_called_once()
        finally:
            # Robustness: release and await second refresh even if identity assertion
            # fails under guard mutation, preventing hung teardown or leaked tasks.
            release_second_fetch.set()
            await second_refresh_task

        # Assert second fetch + driver DONE/not cancelled, slot None,
        # listener still once
        assert second_fetch_task is not None
        assert second_fetch_task.done()
        assert not second_fetch_task.cancelled()
        assert second_refresh_task.done()
        assert not second_refresh_task.cancelled()
        assert coordinator._sensor_refresh_task is None
        assert listener_calls == [1]


class TestProtectCoordinatorEdgeCases:
    """Tests for edge cases in UnifiInsightsProtectCoordinator."""

    @pytest.fixture
    def coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> UnifiInsightsProtectCoordinator:
        """Create a protect coordinator for testing."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        return UnifiInsightsProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )

    def test_site_id_default(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ):
        """Test the coordinator defaults site_id to 'default' when unset."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()

        coordinator = UnifiInsightsProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )

        assert coordinator._site_id == "default"

    def test_site_id_custom(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ):
        """Test the coordinator accepts a custom site_id (REMOTE routing)."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()

        coordinator = UnifiInsightsProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
            site_id="site2",
        )

        assert coordinator._site_id == "site2"

    @pytest.mark.asyncio
    async def test_fetch_viewers_no_viewers_attribute(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ):
        """Test fetch viewers when protect_client lacks viewers attribute."""
        network_client = _create_mock_network_client()
        protect_client = MagicMock()
        protect_client.register_device_update_callback = MagicMock()
        protect_client.register_event_update_callback = MagicMock()
        protect_client.cameras = MagicMock()
        protect_client.cameras.get_all = AsyncMock(return_value=[])
        protect_client.lights = MagicMock()
        protect_client.lights.get_all = AsyncMock(return_value=[])
        protect_client.sensors = MagicMock()
        protect_client.sensors.get_all = AsyncMock(return_value=[])
        protect_client.nvr = MagicMock()
        protect_client.nvr.get = AsyncMock(return_value=MagicMock(id="nvr1"))
        protect_client.chimes = MagicMock()
        protect_client.chimes.get_all = AsyncMock(return_value=[])
        # Remove viewers attribute
        del protect_client.viewers
        protect_client.liveviews = MagicMock()
        protect_client.liveviews.get_all = AsyncMock(return_value=[])

        coordinator = UnifiInsightsProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )

        result = await coordinator._async_update_data()

        # Should not raise and viewers should be empty
        assert result["viewers"] == {}

    @pytest.mark.asyncio
    async def test_fetch_liveviews_no_liveviews_attribute(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ):
        """Test fetch liveviews when protect_client lacks liveviews attribute."""
        network_client = _create_mock_network_client()
        protect_client = MagicMock()
        protect_client.register_device_update_callback = MagicMock()
        protect_client.register_event_update_callback = MagicMock()
        protect_client.cameras = MagicMock()
        protect_client.cameras.get_all = AsyncMock(return_value=[])
        protect_client.lights = MagicMock()
        protect_client.lights.get_all = AsyncMock(return_value=[])
        protect_client.sensors = MagicMock()
        protect_client.sensors.get_all = AsyncMock(return_value=[])
        protect_client.nvr = MagicMock()
        protect_client.nvr.get = AsyncMock(return_value=MagicMock(id="nvr1"))
        protect_client.chimes = MagicMock()
        protect_client.chimes.get_all = AsyncMock(return_value=[])
        protect_client.viewers = MagicMock()
        protect_client.viewers.get_all = AsyncMock(return_value=[])
        # Remove liveviews attribute
        del protect_client.liveviews

        coordinator = UnifiInsightsProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )

        result = await coordinator._async_update_data()

        # Should not raise and liveviews should be empty
        assert result["liveviews"] == {}

    @pytest.mark.asyncio
    async def test_fetch_cameras_feature_flags_not_dict(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test camera fetch when featureFlags is not a dict."""
        camera_mock = MagicMock()
        camera_mock.id = "camera1"
        camera_mock.name = "Test Camera"
        camera_mock.feature_flags = "not_a_dict"  # Invalid type
        # Make model_dump return the non-dict featureFlags
        camera_mock.model_dump = MagicMock(
            return_value={
                "id": "camera1",
                "name": "Test Camera",
                "featureFlags": "not_a_dict",
            }
        )
        require_not_none(coordinator.protect_client).cameras.get_all = AsyncMock(
            return_value=[camera_mock]
        )

        result = await coordinator._async_update_data()

        # Should still work, smartDetectTypes should be empty list
        assert result["cameras"]["camera1"]["smartDetectTypes"] == []

    def test_handle_event_unknown_type(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test handling event with unknown type."""
        # Add camera first
        coordinator.data["cameras"]["camera1"] = {"id": "camera1"}

        # Process unknown event type
        send_event(
            coordinator,
            "unknown_event",
            {"id": "event1", "device": "camera1", "start": RECENT_START},
        )

        # Event should be stored but no device update
        assert "unknown_event" in coordinator.data["events"]

    def test_handle_event_device_not_found(
        self, coordinator: UnifiInsightsProtectCoordinator
    ):
        """Test handling event when device not in data."""
        send_event(
            coordinator,
            "motion",
            {"id": "event1", "device": "nonexistent_device", "start": RECENT_START},
        )

        # Event should be stored but no error
        assert "motion" in coordinator.data["events"]
