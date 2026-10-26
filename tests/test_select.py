# Copyright 2026 UniFi Insights contributors
"""Tests for UniFi Protect select entities."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.unifi_insights.const import (
    DEVICE_TYPE_CAMERA,
    DEVICE_TYPE_CHIME,
    DEVICE_TYPE_VIEWER,
    DOMAIN,
    HDR_MODE_AUTO,
    HDR_MODE_ON,
    VIDEO_MODE_API_TO_OPTION,
    VIDEO_MODE_DEFAULT,
    VIDEO_MODE_OPTIONS,
    VIDEO_MODES,
)
from custom_components.unifi_insights.select import (
    PARALLEL_UPDATES,
    UnifiProtectChimeRingtoneSelect,
    UnifiProtectHDRModeSelect,
    UnifiProtectPTZPresetSelect,
    UnifiProtectVideoModeSelect,
    UnifiProtectViewerLiveviewSelect,
    async_setup_entry,
    observed_ringtone_ids,
    require_observed_ringtone,
    resolve_liveview_id,
)
from tests.fixtures.assertions import patch_attribute, require_mock, require_not_none
from tests.fixtures.command_owners import autospec_command_owners, protect_owner
from tests.fixtures.entities import EntityCollector
from tests.fixtures.facade_data import with_protect_collection, with_protect_fields

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.fixture
def mock_coordinator(mock_coordinator: MagicMock) -> MagicMock:
    """Give the shared facade mock strict autospecs of its command owners."""
    return autospec_command_owners(mock_coordinator)


class TestParallelUpdates:
    """Tests for PARALLEL_UPDATES constant."""

    def test_parallel_updates_value(self):
        """Test that PARALLEL_UPDATES is set to 1 for action-based entities."""
        assert PARALLEL_UPDATES == 1


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    async def test_setup_entry_no_protect_client(self, mock_config_entry):
        """Test setup skips when no Protect client available."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = None
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        add_entities = EntityCollector(entities)

        await async_setup_entry(MagicMock(), mock_config_entry, add_entities)
        assert len(entities) == 0

    async def test_setup_entry_with_cameras(self, mock_config_entry):
        """Test setup creates select entities for cameras."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "Front Camera",
                        "state": "CONNECTED",
                        "isPtz": False,
                    },
                },
                "chimes": {},
                "viewers": {},
                "liveviews": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
            },
        }
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        add_entities = EntityCollector(entities)

        await async_setup_entry(MagicMock(), mock_config_entry, add_entities)
        # Should create 2 entities per camera (HDR mode + video mode)
        assert len(entities) == 2

    async def test_setup_entry_with_ptz_camera(self, mock_config_entry):
        """Test setup creates PTZ preset select for PTZ cameras."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "PTZ Camera",
                        "state": "CONNECTED",
                        "isPtz": True,
                    },
                },
                "chimes": {},
                "viewers": {},
                "liveviews": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
            },
        }
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        add_entities = EntityCollector(entities)

        await async_setup_entry(MagicMock(), mock_config_entry, add_entities)
        # Should create 3 entities (HDR + video + PTZ preset)
        assert len(entities) == 3

    async def test_setup_entry_with_chimes(self, mock_config_entry):
        """Test setup creates ringtone select for chimes."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {},
                "chimes": {
                    "chime1": {
                        "id": "chime1",
                        "name": "Door Chime",
                        "state": "CONNECTED",
                    },
                },
                "viewers": {},
                "liveviews": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
            },
        }
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        add_entities = EntityCollector(entities)

        await async_setup_entry(MagicMock(), mock_config_entry, add_entities)
        assert len(entities) == 1
        assert isinstance(entities[0], UnifiProtectChimeRingtoneSelect)

    async def test_setup_entry_with_viewers(self, mock_config_entry):
        """Test setup creates liveview select for viewers."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {},
                "chimes": {},
                "viewers": {
                    "viewer1": {
                        "id": "viewer1",
                        "name": "Living Room Viewer",
                        "state": "CONNECTED",
                    },
                },
                "liveviews": {
                    "lv1": {"id": "lv1", "name": "Default View"},
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
            },
        }
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        add_entities = EntityCollector(entities)

        await async_setup_entry(MagicMock(), mock_config_entry, add_entities)
        assert len(entities) == 1
        assert isinstance(entities[0], UnifiProtectViewerLiveviewSelect)

    async def test_setup_entry_rediscovery_dedupes_ptz_and_liveview_selects(
        self, mock_config_entry
    ):
        """Re-running discovery skips already-known PTZ preset/liveview selects."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = MagicMock()
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "PTZ Camera",
                        "state": "CONNECTED",
                        "isPtz": True,
                    },
                },
                "chimes": {},
                "viewers": {
                    "viewer1": {
                        "id": "viewer1",
                        "name": "Living Room Viewer",
                        "state": "CONNECTED",
                    },
                },
                "liveviews": {
                    "lv1": {"id": "lv1", "name": "Default View"},
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
            },
        }
        mock_config_entry.runtime_data = MagicMock()
        mock_config_entry.runtime_data.coordinator = mock_coordinator

        entities = []

        add_entities = EntityCollector(entities)

        await async_setup_entry(MagicMock(), mock_config_entry, add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        ptz_before = len(
            [e for e in entities if isinstance(e, UnifiProtectPTZPresetSelect)]
        )
        liveview_before = len(
            [e for e in entities if isinstance(e, UnifiProtectViewerLiveviewSelect)]
        )
        assert ptz_before == 1
        assert liveview_before == 1
        first_total = len(entities)

        # Re-running discovery with unchanged data must not add duplicates.
        listener()

        assert len(entities) == first_total
        assert (
            len([e for e in entities if isinstance(e, UnifiProtectPTZPresetSelect)])
            == ptz_before
        )
        assert (
            len(
                [e for e in entities if isinstance(e, UnifiProtectViewerLiveviewSelect)]
            )
            == liveview_before
        )


class TestUnifiProtectHDRModeSelect:
    """Tests for UnifiProtectHDRModeSelect entity."""

    def test_initialization(self, mock_coordinator):
        """Test HDR mode select initialization."""
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "Test Camera",
                        "state": "CONNECTED",
                        "hdrType": "auto",
                    },
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "chimes": {},
                "viewers": {},
                "liveviews": {},
            },
        }

        entity = UnifiProtectHDRModeSelect(
            coordinator=mock_coordinator,
            camera_id="cam1",
        )

        assert entity._device_type == DEVICE_TYPE_CAMERA
        assert entity._device_id == "cam1"
        assert entity._attr_translation_key == "hdr_mode"
        assert entity._attr_current_option == "auto"
        assert HDR_MODE_AUTO in entity._attr_options
        assert HDR_MODE_ON in entity._attr_options

    def test_update_from_data(self, mock_coordinator):
        """Test update from data."""
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "Test Camera",
                        "state": "CONNECTED",
                        "hdrType": "on",
                    },
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "chimes": {},
                "viewers": {},
                "liveviews": {},
            },
        }

        entity = UnifiProtectHDRModeSelect(
            coordinator=mock_coordinator,
            camera_id="cam1",
        )

        assert entity._attr_current_option == "on"

    async def test_async_select_option_success(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test selecting HDR mode option successfully."""
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "Test Camera",
                        "state": "CONNECTED",
                        "hdrType": "auto",
                    },
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "chimes": {},
                "viewers": {},
                "liveviews": {},
            },
        }
        entity = UnifiProtectHDRModeSelect(
            coordinator=mock_coordinator,
            camera_id="cam1",
        )
        patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())

        await entity.async_select_option("on")

        protect_owner(mock_coordinator).async_set_hdr_mode.assert_awaited_once_with(
            "cam1", "on"
        )
        # The owner publishes hdrType; the option follows from the data.
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "cameras", "cam1", hdrType="on"
        )
        entity._update_from_data()
        assert entity._attr_current_option == "on"

    async def test_async_select_option_error(self, mock_coordinator, monkeypatch):
        """Test selecting HDR mode option with error."""
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "Test Camera",
                        "state": "CONNECTED",
                        "hdrType": "auto",
                    },
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "chimes": {},
                "viewers": {},
                "liveviews": {},
            },
        }
        protect_owner(
            mock_coordinator
        ).async_set_hdr_mode.side_effect = HomeAssistantError(
            "Unable to set HDR mode for camera cam1"
        )

        entity = UnifiProtectHDRModeSelect(
            coordinator=mock_coordinator,
            camera_id="cam1",
        )
        patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())

        with pytest.raises(HomeAssistantError, match="Unable to set HDR mode"):
            await entity.async_select_option("on")


class TestUnifiProtectVideoModeSelect:
    """Tests for UnifiProtectVideoModeSelect entity."""

    def test_option_mapping_is_bijective(self) -> None:
        """Every HA option must round-trip to exactly one supported API mode."""
        assert tuple(VIDEO_MODE_OPTIONS.values()) == VIDEO_MODES
        assert len(set(VIDEO_MODE_OPTIONS.values())) == len(VIDEO_MODE_OPTIONS)
        for option, mode in VIDEO_MODE_OPTIONS.items():
            assert VIDEO_MODE_API_TO_OPTION[mode] == option

    def test_initialization(self, mock_coordinator):
        """Test video mode select initialization."""
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "Test Camera",
                        "state": "CONNECTED",
                        "videoMode": "default",
                    },
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "chimes": {},
                "viewers": {},
                "liveviews": {},
            },
        }

        entity = UnifiProtectVideoModeSelect(
            coordinator=mock_coordinator,
            camera_id="cam1",
        )

        assert entity._device_type == DEVICE_TYPE_CAMERA
        assert entity._device_id == "cam1"
        assert entity._attr_translation_key == "video_mode"
        assert entity._attr_current_option == "default"
        assert VIDEO_MODE_DEFAULT in entity._attr_options
        assert "high_fps" in entity._attr_options

    @pytest.mark.parametrize(
        ("option", "mode"),
        [
            ("default", "default"),
            ("high_fps", "highFps"),
            ("sport", "sport"),
            ("slow_shutter", "slowShutter"),
            ("lpr_reflex", "lprReflex"),
            ("lpr_none_reflex", "lprNoneReflex"),
        ],
    )
    async def test_async_select_option_success(
        self, mock_coordinator: MagicMock, option: str, mode: str, monkeypatch
    ) -> None:
        """Test selecting video mode option successfully."""
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "Test Camera",
                        "state": "CONNECTED",
                        "videoMode": "default",
                    },
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "chimes": {},
                "viewers": {},
                "liveviews": {},
            },
        }
        entity = UnifiProtectVideoModeSelect(
            coordinator=mock_coordinator,
            camera_id="cam1",
        )
        patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())

        await entity.async_select_option(option)

        protect_owner(mock_coordinator).async_set_video_mode.assert_awaited_once_with(
            "cam1", mode
        )
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "cameras", "cam1", videoMode=mode
        )
        entity._update_from_data()
        assert entity._attr_current_option == option
        assert require_not_none(entity.extra_state_attributes)["video_mode"] == mode

    async def test_async_select_option_error(self, mock_coordinator, monkeypatch):
        """Test selecting video mode option with error."""
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "Test Camera",
                        "state": "CONNECTED",
                        "videoMode": "default",
                    },
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "chimes": {},
                "viewers": {},
                "liveviews": {},
            },
        }
        protect_owner(
            mock_coordinator
        ).async_set_video_mode.side_effect = HomeAssistantError(
            "Unable to set video mode for camera cam1"
        )

        entity = UnifiProtectVideoModeSelect(
            coordinator=mock_coordinator,
            camera_id="cam1",
        )
        patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())

        with pytest.raises(HomeAssistantError, match="Unable to set video mode"):
            await entity.async_select_option("high_fps")

    @pytest.mark.parametrize(
        "mode", [None, 123, ["invalid"], "unsupported", "high_fps", "slow_shutter"]
    )
    def test_unknown_api_mode_stays_unknown(
        self, mock_coordinator: MagicMock, mode: object
    ) -> None:
        """Malformed or unsupported API values must not become a valid mode."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "cameras",
            {"cam1": {"id": "cam1", "state": "CONNECTED", "videoMode": mode}},
        )
        entity = UnifiProtectVideoModeSelect(mock_coordinator, "cam1")
        assert entity.state is None
        assert require_not_none(entity.extra_state_attributes)["video_mode"] == mode

    def test_missing_api_mode_uses_default(self, mock_coordinator: MagicMock) -> None:
        """An omitted API field retains the established default fallback."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "cameras",
            {"cam1": {"id": "cam1", "state": "CONNECTED"}},
        )
        entity = UnifiProtectVideoModeSelect(mock_coordinator, "cam1")
        assert entity.state == "default"
        assert (
            require_not_none(entity.extra_state_attributes)["video_mode"] == "default"
        )


class TestLprVideoModeStates:
    """Exercise camera API responses through the real HA state machine."""

    @pytest.fixture
    def mock_protect_client(
        self, mock_protect_client: MagicMock, mode: str
    ) -> MagicMock:
        """Seed the API boundary before the integration builds its snapshots."""
        mock_protect_client.cameras.get_all.return_value = [
            {
                "id": "lpr_camera",
                "name": "LPR Camera",
                "state": "CONNECTED",
                "model": "UVC-G5-Bullet",
                "mac": "11:22:33:44:55:66",
                "videoMode": mode,
                "featureFlags": {},
            }
        ]
        return mock_protect_client

    @pytest.mark.parametrize(
        ("mode", "option"),
        [
            ("default", "default"),
            ("highFps", "high_fps"),
            ("sport", "sport"),
            ("slowShutter", "slow_shutter"),
            ("lprReflex", "lpr_reflex"),
            ("lprNoneReflex", "lpr_none_reflex"),
        ],
    )
    async def test_lpr_mode_serializes_as_reported(
        self,
        hass: HomeAssistant,
        init_integration: MockConfigEntry,
        mock_protect_client: MagicMock,
        mode: str,
        option: str,
    ) -> None:
        """A documented LPR mode must serialize as its value, never unknown."""
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "select", DOMAIN, "unifi_insights_camera_lpr_camera_video_mode"
        )
        assert entity_id is not None
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == option
        assert state.attributes["video_mode"] == mode
        assert state.attributes["options"] == [
            "default",
            "high_fps",
            "sport",
            "slow_shutter",
            "lpr_reflex",
            "lpr_none_reflex",
        ]

        # Public HA service validation rejects legacy select keys before I/O.
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": entity_id, "option": "highFps"},
                blocking=True,
            )
        mock_protect_client.cameras.set_video_mode.assert_not_awaited()
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": entity_id, "option": option},
            blocking=True,
        )
        mock_protect_client.cameras.set_video_mode.assert_awaited_once_with(
            "lpr_camera", mode
        )

        # A fresh API response must publish the new translated state through HA.
        raw_update = "highFps" if mode == "default" else "default"
        key_update = "high_fps" if mode == "default" else "default"
        mock_protect_client.cameras.get_all.return_value = [
            {
                **mock_protect_client.cameras.get_all.return_value[0],
                "videoMode": raw_update,
            }
        ]
        owner = init_integration.runtime_data.coordinator.require_protect_coordinator()
        await owner.async_refresh()
        await hass.async_block_till_done()
        updated = hass.states.get(entity_id)
        assert updated is not None
        assert updated.state == key_update
        assert updated.attributes["video_mode"] == raw_update


class TestUnifiProtectChimeRingtoneSelect:
    """Tests for UnifiProtectChimeRingtoneSelect entity."""

    @pytest.fixture
    def ringtone_coordinator(self, mock_coordinator: MagicMock) -> MagicMock:
        """Provide two console-local ringtone IDs and a facade action."""
        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "chimes",
            {
                "chime1": {
                    "id": "chime1",
                    "name": "Test Chime",
                    "state": "CONNECTED",
                    "ringSettings": [{"ringtoneId": "tone-2"}],
                },
                "chime2": {
                    "id": "chime2",
                    "ringSettings": [
                        {"ringtoneId": "tone-1"},
                        {"ringtoneId": "tone-2"},
                    ],
                },
            },
        )
        return mock_coordinator

    def test_initialization(self, ringtone_coordinator: MagicMock) -> None:
        """Sorted distinct IDs retain existing entity and device identities."""
        entity = UnifiProtectChimeRingtoneSelect(
            coordinator=ringtone_coordinator,
            chime_id="chime1",
        )

        assert entity._device_type == DEVICE_TYPE_CHIME
        assert entity._device_id == "chime1"
        assert entity._attr_translation_key == "ringtone"
        assert entity.unique_id == "unifi_insights_chime_chime1_ringtone"
        assert entity.device_info is not None
        assert entity.device_info["identifiers"] == {
            ("unifi_insights", "protect_chime_chime1")
        }
        assert entity._attr_current_option == "tone-2"
        assert entity._attr_options == ["tone-1", "tone-2"]
        assert entity.extra_state_attributes is not None
        assert entity.extra_state_attributes["chime_ringtone_id"] == "tone-2"

    @pytest.mark.parametrize(
        "settings",
        [
            None,
            [],
            {},
            "invalid",
            [None],
            [{}],
            [{"ringtoneId": None}],
            [{"ringtoneId": ""}],
            [{"ringtoneId": "  "}],
            [{"ringtoneId": 1}],
            [{"ringtoneId": "tone-1"}, {"ringtoneId": "tone-2"}],
            [{"ringtoneId": "tone-1"}, None],
        ],
    )
    def test_missing_or_invalid_current_settings(
        self, ringtone_coordinator: MagicMock, settings: object
    ) -> None:
        """Missing or malformed settings have no fabricated current ringtone."""
        ringtone_coordinator.data = with_protect_fields(
            ringtone_coordinator.data, "chimes", "chime1", ringSettings=settings
        )
        entity = UnifiProtectChimeRingtoneSelect(
            coordinator=ringtone_coordinator,
            chime_id="chime1",
        )
        assert entity._attr_current_option is None
        assert entity.extra_state_attributes is not None
        assert entity.extra_state_attributes["chime_ringtone_id"] is None
        assert entity._attr_options == ["tone-1", "tone-2"]

    def test_skip_malformed_chimes_and_entries(
        self, ringtone_coordinator: MagicMock
    ) -> None:
        """Malformed neighbouring chimes do not crash or add invalid options."""
        chimes = ringtone_coordinator.data["protect"]["chimes"]
        ringtone_coordinator.data = with_protect_collection(
            ringtone_coordinator.data,
            "chimes",
            {
                **chimes,
                "bad-chime": None,
                "bad-settings": {"ringSettings": {"ringtoneId": "foreign-tone"}},
                "bad-entries": {
                    "ringSettings": [None, "bad", {}, {"ringtoneId": False}]
                },
            },
        )
        entity = UnifiProtectChimeRingtoneSelect(
            coordinator=ringtone_coordinator,
            chime_id="chime1",
        )
        assert entity._attr_options == ["tone-1", "tone-2"]

        chimes = ringtone_coordinator.data["protect"]["chimes"]
        ringtone_coordinator.data = with_protect_collection(
            ringtone_coordinator.data, "chimes", {**chimes, "chime1": None}
        )
        entity._update_from_data()
        assert entity._attr_current_option is None

        ringtone_coordinator.data = with_protect_collection(
            ringtone_coordinator.data, "chimes", []
        )
        entity._update_from_data()
        assert entity._attr_options == []
        assert entity._attr_current_option is None

    def test_options_follow_console_updates(
        self, ringtone_coordinator: MagicMock
    ) -> None:
        """Options update from this coordinator; another console is not consulted."""
        entity = UnifiProtectChimeRingtoneSelect(
            coordinator=ringtone_coordinator, chime_id="chime1"
        )
        other = MagicMock()
        other.data = {
            "protect": {
                "chimes": {
                    "foreign": {"ringSettings": [{"ringtoneId": "foreign-tone"}]}
                }
            }
        }
        other_entity = UnifiProtectChimeRingtoneSelect(other, "foreign")
        assert other_entity._attr_options == ["foreign-tone"]
        assert "foreign-tone" not in entity._attr_options

        ringtone_coordinator.data = with_protect_collection(
            ringtone_coordinator.data,
            "chimes",
            {
                "chime1": {
                    **ringtone_coordinator.data["protect"]["chimes"]["chime1"],
                    "ringSettings": [
                        {"ringtoneId": "tone-new"},
                        {"ringtoneId": "tone-1"},
                    ],
                }
            },
        )
        entity._update_from_data()
        assert entity._attr_options == ["tone-1", "tone-new"]
        assert entity._attr_current_option is None

    async def test_async_select_option_success(
        self, ringtone_coordinator: MagicMock
    ) -> None:
        """A valid observed ID reaches the owner; state follows its overlay."""
        entity = UnifiProtectChimeRingtoneSelect(
            coordinator=ringtone_coordinator, chime_id="chime1"
        )
        with patch.object(entity, "async_write_ha_state"):
            await entity.async_select_option("tone-1")
        protect = protect_owner(ringtone_coordinator)
        protect.async_set_chime_ringtone.assert_awaited_once_with("chime1", "tone-1")
        ringtone_coordinator.data = with_protect_fields(
            ringtone_coordinator.data,
            "chimes",
            "chime1",
            ringSettings=[{"ringtoneId": "tone-1"}],
        )
        entity._update_from_data()
        assert entity._attr_current_option == "tone-1"

    @pytest.mark.parametrize(
        "data",
        [
            None,
            [],
            {},
            {"protect": None},
            {"protect": []},
            {"protect": {"chimes": None}},
        ],
    )
    def test_malformed_cache_update_has_no_current_ringtone(
        self, ringtone_coordinator: MagicMock, data: object
    ) -> None:
        """Malformed cache layers clear the value and options without raising."""
        entity = UnifiProtectChimeRingtoneSelect(ringtone_coordinator, "chime1")
        ringtone_coordinator.data = data
        entity._update_from_data()
        assert entity.current_option is None
        assert entity.options == []

    async def test_async_select_option_error(
        self, ringtone_coordinator: MagicMock, monkeypatch
    ) -> None:
        """A failed write leaves state unchanged and propagates the API error."""
        protect_owner(
            ringtone_coordinator
        ).async_set_chime_ringtone.side_effect = HomeAssistantError(
            "Unable to set ringtone"
        )
        entity = UnifiProtectChimeRingtoneSelect(
            coordinator=ringtone_coordinator, chime_id="chime1"
        )
        patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())
        with pytest.raises(HomeAssistantError, match="Unable to set ringtone"):
            await entity.async_select_option("tone-1")
        assert entity._attr_current_option == "tone-2"
        require_mock(entity.async_write_ha_state).assert_not_called()

    async def test_invalid_option_sends_nothing(
        self, ringtone_coordinator: MagicMock
    ) -> None:
        """Invented or unobserved options are rejected before any action."""
        entity = UnifiProtectChimeRingtoneSelect(
            coordinator=ringtone_coordinator, chime_id="chime1"
        )
        with pytest.raises(
            ServiceValidationError, match="Ringtone ID not found"
        ) as caught:
            await entity.async_select_option("unobserved-tone")
        assert caught.value.translation_domain == DOMAIN
        assert caught.value.translation_key == "ringtone_not_found"
        assert caught.value.translation_placeholders == {
            "ringtone_id": "unobserved-tone",
            "chime_id": "chime1",
        }
        protect_owner(ringtone_coordinator).async_set_chime_ringtone.assert_not_called()


class TestUnifiProtectPTZPresetSelect:
    """Tests for UnifiProtectPTZPresetSelect entity."""

    def test_initialization(self, mock_coordinator):
        """Test PTZ preset select initialization."""
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "PTZ Camera",
                        "state": "CONNECTED",
                        "isPtz": True,
                        "currentPtzPreset": 2,
                    },
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "chimes": {},
                "viewers": {},
                "liveviews": {},
            },
        }

        entity = UnifiProtectPTZPresetSelect(
            coordinator=mock_coordinator,
            camera_id="cam1",
        )

        assert entity._device_type == DEVICE_TYPE_CAMERA
        assert entity._device_id == "cam1"
        assert entity._attr_translation_key == "ptz_preset"
        assert entity._attr_current_option == "2"
        assert "0" in entity._attr_options

    async def test_async_select_option_success(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test selecting PTZ preset option successfully."""
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "PTZ Camera",
                        "state": "CONNECTED",
                        "isPtz": True,
                        "currentPtzPreset": 0,
                    },
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "chimes": {},
                "viewers": {},
                "liveviews": {},
            },
        }
        entity = UnifiProtectPTZPresetSelect(
            coordinator=mock_coordinator,
            camera_id="cam1",
        )
        patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())

        await entity.async_select_option("3")

        protect = protect_owner(mock_coordinator)
        protect.async_move_ptz_to_preset.assert_awaited_once_with("cam1", 3)
        assert entity._attr_current_option == "3"

    async def test_async_select_option_error(self, mock_coordinator, monkeypatch):
        """Test selecting PTZ preset option with error."""
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {
                    "cam1": {
                        "id": "cam1",
                        "name": "PTZ Camera",
                        "state": "CONNECTED",
                        "isPtz": True,
                        "currentPtzPreset": 0,
                    },
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "chimes": {},
                "viewers": {},
                "liveviews": {},
            },
        }
        protect_owner(
            mock_coordinator
        ).async_move_ptz_to_preset.side_effect = HomeAssistantError(
            "Unable to move camera cam1 to PTZ preset 3"
        )

        entity = UnifiProtectPTZPresetSelect(
            coordinator=mock_coordinator,
            camera_id="cam1",
        )
        patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())

        with pytest.raises(HomeAssistantError, match="Unable to move camera"):
            await entity.async_select_option("3")


class TestUnifiProtectViewerLiveviewSelect:
    """Tests for UnifiProtectViewerLiveviewSelect entity."""

    def test_initialization(self, mock_coordinator):
        """Test viewer liveview select initialization."""
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {},
                "chimes": {},
                "viewers": {
                    "viewer1": {
                        "id": "viewer1",
                        "name": "Test Viewer",
                        "state": "CONNECTED",
                        "liveview": "lv1",
                    },
                },
                "liveviews": {
                    "lv1": {"id": "lv1", "name": "Default View"},
                    "lv2": {"id": "lv2", "name": "All Cameras"},
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
            },
        }

        entity = UnifiProtectViewerLiveviewSelect(
            coordinator=mock_coordinator,
            viewer_id="viewer1",
        )

        assert entity._device_type == DEVICE_TYPE_VIEWER
        assert entity._device_id == "viewer1"
        assert entity._attr_translation_key == "liveview"
        assert entity._attr_current_option == "Default View"
        assert "Default View" in entity._attr_options
        assert "All Cameras" in entity._attr_options

    def test_initialization_no_liveview_set(self, mock_coordinator):
        """Test viewer liveview select with no liveview set."""
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {},
                "chimes": {},
                "viewers": {
                    "viewer1": {
                        "id": "viewer1",
                        "name": "Test Viewer",
                        "state": "CONNECTED",
                    },
                },
                "liveviews": {
                    "lv1": {"id": "lv1", "name": "Default View"},
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
            },
        }

        entity = UnifiProtectViewerLiveviewSelect(
            coordinator=mock_coordinator,
            viewer_id="viewer1",
        )

        assert entity._attr_current_option is None

    async def test_async_select_option_success(
        self, mock_coordinator: MagicMock, monkeypatch
    ) -> None:
        """Test selecting liveview option successfully."""
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {},
                "chimes": {},
                "viewers": {
                    "viewer1": {
                        "id": "viewer1",
                        "name": "Test Viewer",
                        "state": "CONNECTED",
                        "liveview": "lv1",
                    },
                },
                "liveviews": {
                    "lv1": {"id": "lv1", "name": "Default View"},
                    "lv2": {"id": "lv2", "name": "All Cameras"},
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
            },
        }
        entity = UnifiProtectViewerLiveviewSelect(
            coordinator=mock_coordinator,
            viewer_id="viewer1",
        )
        patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())

        await entity.async_select_option("All Cameras")

        # The owner takes the native liveview ID, not the display name.
        protect_owner(mock_coordinator).async_update_viewer.assert_awaited_once_with(
            "viewer1", liveview="lv2"
        )
        mock_coordinator.data = with_protect_fields(
            mock_coordinator.data, "viewers", "viewer1", liveview="lv2"
        )
        entity._update_from_data()
        assert entity._attr_current_option == "All Cameras"

    async def test_async_select_option_error(self, mock_coordinator, monkeypatch):
        """Test selecting liveview option with error."""
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {},
                "chimes": {},
                "viewers": {
                    "viewer1": {
                        "id": "viewer1",
                        "name": "Test Viewer",
                        "state": "CONNECTED",
                        "liveview": "lv1",
                    },
                },
                "liveviews": {
                    "lv1": {"id": "lv1", "name": "Default View"},
                    "lv2": {"id": "lv2", "name": "All Cameras"},
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
            },
        }
        protect_owner(
            mock_coordinator
        ).async_update_viewer.side_effect = HomeAssistantError(
            "Unable to update viewer viewer1"
        )

        entity = UnifiProtectViewerLiveviewSelect(
            coordinator=mock_coordinator,
            viewer_id="viewer1",
        )
        patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())

        with pytest.raises(HomeAssistantError, match="Unable to update viewer"):
            await entity.async_select_option("All Cameras")

    async def test_async_select_option_liveview_not_found(
        self, mock_coordinator, monkeypatch
    ):
        """Test selecting liveview that doesn't exist."""
        mock_coordinator.data = {
            "sites": {},
            "devices": {},
            "protect": {
                "cameras": {},
                "chimes": {},
                "viewers": {
                    "viewer1": {
                        "id": "viewer1",
                        "name": "Test Viewer",
                        "state": "CONNECTED",
                    },
                },
                "liveviews": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
            },
        }
        entity = UnifiProtectViewerLiveviewSelect(
            coordinator=mock_coordinator,
            viewer_id="viewer1",
        )
        patch_attribute(monkeypatch, entity, "async_write_ha_state", MagicMock())

        with pytest.raises(ServiceValidationError, match="Liveview not found") as err:
            await entity.async_select_option("NonExistent")

        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "liveview_not_found"
        assert err.value.translation_placeholders == {"liveview": "NonExistent"}
        protect_owner(mock_coordinator).async_update_viewer.assert_not_called()


class TestNameAndRingtoneResolution:
    """The select-side translation of observed values into native IDs."""

    def test_observed_ringtone_ids_skip_malformed_entries(self) -> None:
        """Only non-blank string IDs of dict entries are collected, sorted."""
        chimes = {
            "a": {"ringSettings": [{"ringtoneId": "t2"}, "junk", {"ringtoneId": " "}]},
            "b": {"ringSettings": "junk"},
            "c": "junk",
            "d": {"ringSettings": [{"ringtoneId": "t1"}, {"ringtoneId": 5}]},
        }
        assert observed_ringtone_ids(chimes) == ["t1", "t2"]
        assert observed_ringtone_ids(None) == []

    def test_require_observed_ringtone_accepts_known_ids(self) -> None:
        """A ringtone another chime uses is accepted."""
        chimes = {"b": {"ringSettings": [{"ringtoneId": "t1"}]}}
        require_observed_ringtone(chimes, "a", "t1")

    def test_resolve_liveview_id(self) -> None:
        """A name resolves to its ID; malformed entries are skipped."""
        liveviews = {"x": "junk", "lv2": {"name": "All"}}
        assert resolve_liveview_id(liveviews, "All") == "lv2"
        with pytest.raises(ServiceValidationError):
            resolve_liveview_id(None, "All")

    def test_resolve_liveview_id_matches_unnamed_by_id(self) -> None:
        """An unnamed liveview is offered by its ID, so its ID resolves too."""
        liveviews = {"lv3": {"id": "lv3"}, "lv2": {"name": "All"}}
        assert resolve_liveview_id(liveviews, "lv3") == "lv3"
