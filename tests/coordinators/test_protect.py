# Copyright 2026 UniFi Insights contributors
"""
Tests for the Protect owner's commands (ADR-010).

Pins the second test adapter against the real ``unifi_official_api`` 2.0.0
classes: the seeded Protect owner refreshes through real coordinator code,
and the autospecced client rejects methods and arguments the library does
not have. Each Protect command is tested once here: its request, its
translated error and its overlay or reconciliation policy.
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from pydantic import ValidationError
from unifi_official_api import (
    UniFiConnectionError,
    UniFiGlobalAlarmManagerError,
    UniFiResponseError,
)
from unifi_official_api.auth import ApiKeyAuth
from unifi_official_api.const import ConnectionType
from unifi_official_api.exceptions import UniFiUnsupportedError
from unifi_official_api.protect import UniFiProtectClient
from unifi_official_api.protect.endpoints.chimes import ChimesEndpoint
from unifi_official_api.protect.models.files import RTSPSStream, TalkbackSession
from unifi_official_api.protect.talkback import (
    TalkbackSchemeError,
    TalkbackStreamError,
)

from custom_components.unifi_insights.const import DOMAIN
from tests.fixtures.command_owners import (
    BASE_URL,
    CHIME_ID,
    DOORBELL_IDS,
    autospec_endpoint,
    build_seeded_owners,
)
from tests.fixtures.library_responses import SAMPLE_SIREN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

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
        collection: str,
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
        stream.start.assert_awaited_once_with()

    async def test_talkback_without_client(self, seeded_owners: SeededOwners) -> None:
        """Without a Protect client talkback raises its own translated error."""
        seeded_owners.protect.protect_client = None

        with pytest.raises(HomeAssistantError) as err:
            await seeded_owners.protect.async_start_camera_talkback(CAMERA_ID, "a.mp3")

        assert err.value.translation_key == "protect_not_available_for_talkback"

    async def test_session_failure_logs_only_sanitised_fields(
        self, seeded_owners: SeededOwners, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A failed session is translated; a validation error logs field names."""
        create = seeded_owners.protect_client.cameras.create_talkback_session
        create.side_effect = ValueError("Session failed")
        with pytest.raises(HomeAssistantError) as err:
            await seeded_owners.protect.async_start_camera_talkback(CAMERA_ID, "a.mp3")
        assert err.value.translation_key == "talkback_session_invalid"

        leaked_url = "rtp://custom-token@192.168.1.10:7004"
        create.side_effect = ValidationError.from_exception_data(
            "TalkbackSession",
            [
                {
                    "type": "string_type",
                    "loc": ("url",),
                    "msg": "Input should be a valid string",
                    "input": leaked_url,
                }
            ],
        )
        with caplog.at_level(logging.WARNING), pytest.raises(HomeAssistantError):
            await seeded_owners.protect.async_start_camera_talkback(CAMERA_ID, "a.mp3")
        assert "invalid fields: url" in caplog.text
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
        create.return_value = RTSPSStream(high="rtsps://nvr:7441/abc")

        assert (
            await protect.async_create_camera_stream_url(CAMERA_ID)
            == "rtsps://nvr:7441/abc"
        )
        create.assert_awaited_once_with(CAMERA_ID, qualities=["high"])

        create.return_value = RTSPSStream()
        assert await protect.async_create_camera_stream_url(CAMERA_ID) is None

        create.side_effect = UniFiConnectionError("down")
        assert await protect.async_create_camera_stream_url(CAMERA_ID) is None
