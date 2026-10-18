# Copyright 2026 UniFi Insights contributors
"""Tests for UniFi Insights buttons."""

import asyncio
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import UNDEFINED
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_insights.api import (
    ApiKeyAuth,
    ConnectionType,
    UniFiConnectionError,
)
from custom_components.unifi_insights.api.network import UniFiNetworkClient
from custom_components.unifi_insights.button import (
    BUTTON_TYPES,
    UnifiClientReconnectButton,
    UnifiClientWakeButton,
    UnifiGenerateVoucherButton,
    UnifiInsightsButton,
    UnifiInsightsPoePowerCycleButton,
    UnifiProtectChimePlayButton,
    UnifiProtectPTZPatrolStartButton,
    UnifiProtectPTZPatrolStopButton,
    _connected_client_macs,
    _get_port_label,
    _registered_wake_macs,
    async_setup_entry,
    get_device_port,
    get_device_ports,
    port_can_be_power_cycled,
)
from custom_components.unifi_insights.client_wake import select_wake_history
from custom_components.unifi_insights.const import (
    CONF_CLIENT_CONTROL,
    DOMAIN,
    MANUFACTURER,
)
from custom_components.unifi_insights.coordinators.facade import (
    UnifiFacadeCoordinator,
)
from custom_components.unifi_insights.coordinators.voucher_state import VoucherSettings
from tests.test_api_network_more_endpoints import _Response, _Session

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class TestButtonTypes:
    """Tests for button type definitions."""

    def test_button_types_defined(self):
        """Test that button types are defined."""
        assert len(BUTTON_TYPES) > 0

    def test_device_restart_button(self):
        """Test device restart button is defined."""
        restart = next((b for b in BUTTON_TYPES if b.key == "device_restart"), None)
        assert restart is not None

        assert restart.name in (None, UNDEFINED)
        assert restart.translation_key == "device_restart"
        assert restart.icon in (None, UNDEFINED)


class TestUnifiInsightsButton:
    """Tests for UnifiInsightsButton."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.network_client.devices.restart = AsyncMock(return_value=True)
        coordinator.protect_client = None
        coordinator.data = {
            "sites": {"site1": {"id": "site1", "meta": {"name": "Default"}}},
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "name": "Test Switch",
                        "model": "USW-24-POE",
                        "state": "ONLINE",
                        "macAddress": "AA:BB:CC:DD:EE:FF",
                        "ipAddress": "192.168.1.10",
                        "firmwareVersion": "6.5.55",
                    },
                    "device2": {
                        "id": "device2",
                        "name": "Offline Switch",
                        "model": "USW-24-POE",
                        "state": "OFFLINE",
                        "macAddress": "11:22:33:44:55:66",
                    },
                },
            },
            "stats": {},
            "clients": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    async def test_button_init(self, hass: HomeAssistant, mock_coordinator):
        """Test button initialization."""
        description = BUTTON_TYPES[0]

        button = UnifiInsightsButton(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        assert button._site_id == "site1"
        assert button._device_id == "device1"

    async def test_button_available_online(self, hass: HomeAssistant, mock_coordinator):
        """Test button available when device online."""
        description = BUTTON_TYPES[0]

        button = UnifiInsightsButton(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        assert button.available is True

    async def test_button_available_offline(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test button unavailable when device offline."""
        description = BUTTON_TYPES[0]

        button = UnifiInsightsButton(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device2",
        )

        assert button.available is False

    async def test_button_press_success(self, hass: HomeAssistant, mock_coordinator):
        """Test button press success."""
        description = BUTTON_TYPES[0]

        button = UnifiInsightsButton(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        await button.async_press()

        mock_coordinator.network_client.devices.restart.assert_called_once_with(
            "site1", "device1"
        )

    async def test_button_press_failure(self, hass: HomeAssistant, mock_coordinator):
        """Test button press failure."""
        mock_coordinator.network_client.devices.restart = AsyncMock(return_value=False)
        description = BUTTON_TYPES[0]

        button = UnifiInsightsButton(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        with pytest.raises(HomeAssistantError, match="Unable to restart device"):
            await button.async_press()

        mock_coordinator.network_client.devices.restart.assert_called_once()

    async def test_button_press_exception(self, hass: HomeAssistant, mock_coordinator):
        """Test button press handles exception."""
        mock_coordinator.network_client.devices.restart = AsyncMock(
            side_effect=Exception("API Error")
        )
        description = BUTTON_TYPES[0]

        button = UnifiInsightsButton(
            coordinator=mock_coordinator,
            description=description,
            site_id="site1",
            device_id="device1",
        )

        with pytest.raises(HomeAssistantError, match="Unable to restart device"):
            await button.async_press()

    async def test_button_press_uses_facade_restart(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """The facade coroutine runs when present (no fallback)."""
        mock_coordinator.async_restart_device = AsyncMock(return_value=True)
        button = UnifiInsightsButton(
            coordinator=mock_coordinator,
            description=BUTTON_TYPES[0],
            site_id="site1",
            device_id="device1",
        )

        await button.async_press()

        mock_coordinator.async_restart_device.assert_awaited_once_with(
            "site1", "device1"
        )
        mock_coordinator.network_client.devices.restart.assert_not_called()


class TestUnifiClientReconnectButton:
    """Tests for UnifiClientReconnectButton."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.network_client.clients = MagicMock()
        coordinator.network_client.clients.reconnect = AsyncMock()
        coordinator.async_reconnect_client = AsyncMock()
        coordinator.protect_client = None
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "name": "Test Switch",
                        "model": "USW-24-POE",
                        "state": "ONLINE",
                        "macAddress": "AA:BB:CC:DD:EE:FF",
                    },
                },
            },
            "stats": {},
            "clients": {
                "site1": {
                    "client1": {
                        "id": "client1",
                        "name": "My Laptop",
                        "mac": "11:22:33:44:55:66",
                        "hostname": "laptop",
                        "uplinkDeviceId": "device1",
                    },
                    "client2": {
                        "id": "client2",
                        "mac": "AA:BB:CC:DD:EE:00",
                        "hostname": "unknown",
                    },
                },
            },
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    async def test_reconnect_button_init(self, hass: HomeAssistant, mock_coordinator):
        """Test client reconnect button initialization."""
        button = UnifiClientReconnectButton(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        assert button._client_id == "client1"
        assert button.translation_key == "client_reconnect"
        assert button.translation_placeholders == {"client_name": "My Laptop"}
        assert button.has_entity_name is True
        assert getattr(button, "_attr_name", None) is None

    @pytest.mark.parametrize("mac", [None, ""])
    async def test_reconnect_placeholder_falls_back_with_empty_mac(
        self, hass: HomeAssistant, mock_coordinator, mac
    ):
        """A present but empty MAC still falls back to the stable client ID."""
        mock_coordinator.data["clients"]["site1"]["client1"] = {
            "name": None,
            "hostname": None,
            "mac": mac,
        }
        button = UnifiClientReconnectButton(mock_coordinator, "site1", "client1")
        assert button.translation_placeholders == {"client_name": "client1"}
        assert button.unique_id == "site1_client1_reconnect"

    async def test_reconnect_button_available(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test button available when client exists."""
        button = UnifiClientReconnectButton(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        assert button.available is True

    async def test_reconnect_button_unavailable_no_client(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test button unavailable when client doesn't exist."""
        button = UnifiClientReconnectButton(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="nonexistent",
        )

        assert button.available is False

    async def test_reconnect_button_unavailable_when_device_refresh_fails(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test button unavailable while the device refresh is failing."""
        button = UnifiClientReconnectButton(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )
        mock_coordinator.device_available = False

        assert button.available is False

    async def test_reconnect_button_press(self, hass: HomeAssistant, mock_coordinator):
        """Test client reconnect button press."""
        button = UnifiClientReconnectButton(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        await button.async_press()

        mock_coordinator.async_reconnect_client.assert_called_once_with(
            "site1", "client1"
        )

    async def test_reconnect_button_press_exception(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test reconnect button handles exception."""
        mock_coordinator.async_reconnect_client = AsyncMock(
            side_effect=Exception("API Error")
        )

        button = UnifiClientReconnectButton(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        with pytest.raises(HomeAssistantError, match="Unable to reconnect client"):
            await button.async_press()


class TestUnifiProtectChimePlayButton:
    """Tests for UnifiProtectChimePlayButton."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.protect_client.chimes.play = AsyncMock()
        coordinator.data = {
            "sites": {},
            "devices": {},
            "stats": {},
            "clients": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {
                    "chime1": {
                        "id": "chime1",
                        "name": "Front Door Chime",
                        "state": "CONNECTED",
                        "ringSettings": [
                            {"ringtoneId": "mechanical", "cameraId": "camera1"}
                        ],
                    },
                },
                "liveviews": {},
            },
        }
        return coordinator

    async def test_chime_button_init(self, hass: HomeAssistant, mock_coordinator):
        """Test chime play button initialization."""
        button = UnifiProtectChimePlayButton(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )

        assert button._device_id == "chime1"
        assert button._attr_translation_key == "play"
        assert button.unique_id == "unifi_insights_chime_chime1_play"
        assert button.device_info is not None
        assert button.device_info["identifiers"] == {(DOMAIN, "protect_chime_chime1")}

    async def test_chime_button_attributes(self, hass: HomeAssistant, mock_coordinator):
        """Test chime button attributes."""
        button = UnifiProtectChimePlayButton(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )

        attrs = button.extra_state_attributes
        assert attrs is not None
        assert attrs["chime_id"] == "chime1"
        assert attrs["chime_name"] == "Front Door Chime"
        assert attrs["chime_ringtone_id"] == "mechanical"

    async def test_chime_button_press_uses_facade(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """The facade receives only the ID and its translated error survives."""
        error = HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="chime_playback_unsupported",
        )
        mock_coordinator.async_play_chime = AsyncMock(side_effect=error)
        button = UnifiProtectChimePlayButton(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )

        with pytest.raises(HomeAssistantError) as raised:
            await button.async_press()

        assert raised.value is error
        mock_coordinator.async_play_chime.assert_awaited_once_with("chime1")
        mock_coordinator.protect_client.chimes.play.assert_not_called()

    async def test_chime_button_press(self, hass: HomeAssistant, mock_coordinator):
        """The fallback raises the translated error without calling the client."""
        button = UnifiProtectChimePlayButton(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )

        with pytest.raises(HomeAssistantError) as raised:
            await button.async_press()
        assert raised.value.translation_key == "chime_playback_unsupported"
        assert raised.value.translation_domain == DOMAIN
        mock_coordinator.protect_client.chimes.play.assert_not_called()
        mock_coordinator.protect_client._post.assert_not_called()

    async def test_chime_button_press_exception(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test chime button handles exception."""
        mock_coordinator.async_play_chime = AsyncMock(
            side_effect=Exception("API Error")
        )

        button = UnifiProtectChimePlayButton(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )

        with pytest.raises(
            HomeAssistantError, match="Unable to play ringtone on chime"
        ):
            await button.async_press()

    @pytest.mark.parametrize(
        "data",
        [
            None,
            [],
            {"protect": None},
            {"protect": []},
            {"protect": {"chimes": []}},
            {"protect": {"chimes": {"chime1": None}}},
            {
                "protect": {
                    "chimes": {
                        "chime1": {
                            "ringSettings": [
                                {"ringtoneId": "tone-a"},
                                {"ringtoneId": "tone-b"},
                            ]
                        }
                    }
                }
            },
        ],
    )
    def test_chime_button_safe_current_id(self, mock_coordinator, data):
        """Missing, malformed or mixed cache data never invents a ringtone."""
        button = UnifiProtectChimePlayButton(mock_coordinator, "chime1")
        mock_coordinator.data = data
        button._update_attributes()
        assert button.extra_state_attributes is not None
        assert button.extra_state_attributes["chime_ringtone_id"] is None


class TestUnifiProtectPTZButtons:
    """Tests for PTZ patrol buttons."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.protect_client.ptz_start_patrol = AsyncMock()
        coordinator.protect_client.ptz_stop_patrol = AsyncMock()
        coordinator.data = {
            "sites": {},
            "devices": {},
            "stats": {},
            "clients": {},
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "PTZ Camera",
                        "state": "CONNECTED",
                        "isPtz": True,
                    },
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    async def test_ptz_start_button_init(self, hass: HomeAssistant, mock_coordinator):
        """Test PTZ patrol start button initialization."""
        button = UnifiProtectPTZPatrolStartButton(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert button._device_id == "camera1"
        assert button._attr_translation_key == "ptz_patrol_start"

    async def test_ptz_start_button_press(self, hass: HomeAssistant, mock_coordinator):
        """Test PTZ patrol start button press."""
        button = UnifiProtectPTZPatrolStartButton(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        await button.async_press()

        mock_coordinator.protect_client.ptz_start_patrol.assert_called_once_with(
            camera_id="camera1",
            slot=0,
        )

    async def test_ptz_start_button_exception(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test PTZ start button handles exception."""
        mock_coordinator.protect_client.ptz_start_patrol = AsyncMock(
            side_effect=Exception("API Error")
        )

        button = UnifiProtectPTZPatrolStartButton(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        with pytest.raises(HomeAssistantError, match="Unable to start PTZ patrol"):
            await button.async_press()

    async def test_ptz_stop_button_init(self, hass: HomeAssistant, mock_coordinator):
        """Test PTZ patrol stop button initialization."""
        button = UnifiProtectPTZPatrolStopButton(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        assert button._device_id == "camera1"
        assert button._attr_translation_key == "ptz_patrol_stop"

    async def test_ptz_stop_button_press(self, hass: HomeAssistant, mock_coordinator):
        """Test PTZ patrol stop button press."""
        button = UnifiProtectPTZPatrolStopButton(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        await button.async_press()

        mock_coordinator.protect_client.ptz_stop_patrol.assert_called_once_with(
            camera_id="camera1",
        )


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.get_site = MagicMock(
            return_value={"id": "site1", "meta": {"name": "Default"}}
        )
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "name": "Test Switch",
                        "model": "USW-24-POE",
                        "state": "ONLINE",
                        "macAddress": "AA:BB:CC:DD:EE:FF",
                        "ipAddress": "192.168.1.10",
                        "features": ["switching"],
                        "interfaces": {
                            "ports": [
                                {"idx": 1, "poe": {"enabled": True}},
                            ],
                        },
                    },
                },
            },
            "stats": {},
            "clients": {},
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "PTZ Camera",
                        "state": "CONNECTED",
                        "isPtz": True,
                    },
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {
                    "chime1": {
                        "id": "chime1",
                        "name": "Front Door Chime",
                        "state": "CONNECTED",
                    },
                },
                "liveviews": {},
            },
        }
        return coordinator

    @pytest.fixture
    def mock_config_entry(self, hass: HomeAssistant, mock_coordinator):
        """Create a mock entry that schedules its background coroutines."""
        entry = MagicMock()
        entry.runtime_data = MagicMock(coordinator=mock_coordinator)
        entry.async_create_background_task.side_effect = lambda hass, target, name: (
            hass.async_create_background_task(target, name)
        )
        mock_coordinator.async_get_wake_history = AsyncMock(return_value={})
        return entry

    async def test_setup_entry_creates_buttons(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test that setup entry creates buttons."""
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        assert len(added_entities) > 0

    async def test_setup_entry_creates_device_buttons(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test that setup creates device restart buttons."""
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        device_buttons = [
            e for e in added_entities if isinstance(e, UnifiInsightsButton)
        ]
        assert len(device_buttons) > 0

    async def test_setup_entry_creates_chime_buttons(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test that setup creates chime play buttons."""
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        chime_buttons = [
            e for e in added_entities if isinstance(e, UnifiProtectChimePlayButton)
        ]
        assert len(chime_buttons) > 0

    async def test_setup_entry_creates_ptz_buttons(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test that setup creates PTZ patrol buttons."""
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        ptz_start_buttons = [
            e for e in added_entities if isinstance(e, UnifiProtectPTZPatrolStartButton)
        ]
        ptz_stop_buttons = [
            e for e in added_entities if isinstance(e, UnifiProtectPTZPatrolStopButton)
        ]
        assert len(ptz_start_buttons) > 0
        assert len(ptz_stop_buttons) > 0

    async def test_setup_entry_without_protect_client(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test setup without protect client."""
        mock_coordinator.protect_client = None

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        # Should still create device buttons
        device_buttons = [
            e for e in added_entities if isinstance(e, UnifiInsightsButton)
        ]
        assert len(device_buttons) > 0

        # But no protect buttons
        chime_buttons = [
            e for e in added_entities if isinstance(e, UnifiProtectChimePlayButton)
        ]
        assert len(chime_buttons) == 0

    async def test_setup_entry_skips_non_switching_devices(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test that setup skips devices without switching feature."""
        # Add device without switching feature
        mock_coordinator.data["devices"]["site1"]["device2"] = {
            "id": "device2",
            "name": "Access Point",
            "model": "UAP-AC-PRO",
            "state": "ONLINE",
            "features": ["accessPoint"],  # No switching
        }

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

    async def test_setup_entry_dedupes_ptz_and_chime_buttons_on_rediscovery(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Re-running discovery with unchanged data skips already-known buttons."""
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        first_count = len(added_entities)
        assert first_count > 0

        ptz_start_before = len(
            [
                e
                for e in added_entities
                if isinstance(e, UnifiProtectPTZPatrolStartButton)
            ]
        )
        ptz_stop_before = len(
            [
                e
                for e in added_entities
                if isinstance(e, UnifiProtectPTZPatrolStopButton)
            ]
        )
        chime_before = len(
            [e for e in added_entities if isinstance(e, UnifiProtectChimePlayButton)]
        )

        # Re-run discovery on unchanged data; nothing new should be added.
        listener()

        assert len(added_entities) == first_count
        assert (
            len(
                [
                    e
                    for e in added_entities
                    if isinstance(e, UnifiProtectPTZPatrolStartButton)
                ]
            )
            == ptz_start_before
        )
        assert (
            len(
                [
                    e
                    for e in added_entities
                    if isinstance(e, UnifiProtectPTZPatrolStopButton)
                ]
            )
            == ptz_stop_before
        )
        assert (
            len(
                [
                    e
                    for e in added_entities
                    if isinstance(e, UnifiProtectChimePlayButton)
                ]
            )
            == chime_before
        )

    async def test_setup_entry_dedupes_client_reconnect_button_on_rediscovery(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Reconnect buttons are only created once per client across rediscovery."""
        mock_config_entry.options = {CONF_CLIENT_CONTROL: True}
        mock_coordinator.data["clients"]["site1"] = {
            "client1": {
                "id": "client1",
                "mac": "aa:bb:cc:dd:ee:ff",
                "name": "Test Client",
            }
        }

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        reconnect_buttons = [
            e for e in added_entities if isinstance(e, UnifiClientReconnectButton)
        ]
        assert len(reconnect_buttons) == 1

        listener()

        reconnect_buttons = [
            e for e in added_entities if isinstance(e, UnifiClientReconnectButton)
        ]
        assert len(reconnect_buttons) == 1

    async def test_setup_entry_clients_not_a_dict_is_skipped(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """A non-dict top-level clients collection is skipped without raising."""
        mock_config_entry.options = {CONF_CLIENT_CONTROL: True}
        mock_coordinator.data["clients"] = "not-a-dict"

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        reconnect_buttons = [
            e for e in added_entities if isinstance(e, UnifiClientReconnectButton)
        ]
        assert len(reconnect_buttons) == 0


class TestClientReconnectButtonEdgeCases:
    """Test edge cases for UnifiClientReconnectButton."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.network_client.clients = MagicMock()
        coordinator.network_client.clients.reconnect = AsyncMock()
        coordinator.async_reconnect_client = AsyncMock()
        coordinator.protect_client = None
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "name": "Test Switch",
                        "model": "USW-24-POE",
                        "state": "ONLINE",
                    },
                },
            },
            "stats": {},
            "clients": {
                "site1": {
                    "client1": {
                        "id": "client1",
                        "name": None,
                        "hostname": None,
                        "mac": "11:22:33:44:55:66",
                    },
                },
            },
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
            },
        }
        return coordinator

    async def test_reconnect_button_name_from_mac(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test button name defaults to MAC when no name or hostname."""
        button = UnifiClientReconnectButton(
            coordinator=mock_coordinator,
            site_id="site1",
            client_id="client1",
        )

        # Name should include MAC address
        assert button.translation_key == "client_reconnect"
        assert button.translation_placeholders == {"client_name": "11:22:33:44:55:66"}


class TestChimePlayButtonEdgeCases:
    """Test edge cases for UnifiProtectChimePlayButton."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.protect_client.chimes.play = AsyncMock()
        coordinator.data = {
            "sites": {},
            "devices": {},
            "stats": {},
            "clients": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {
                    "chime1": {
                        "id": "chime1",
                        "name": "Front Door Chime",
                        "state": "CONNECTED",
                        "ringSettings": [],  # Empty ring settings
                    },
                },
                "liveviews": {},
            },
        }
        return coordinator

    async def test_chime_button_empty_ring_settings(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Empty ring settings have no fabricated current ringtone."""
        button = UnifiProtectChimePlayButton(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )

        attrs = button.extra_state_attributes
        assert attrs is not None
        assert attrs["chime_ringtone_id"] is None

    async def test_chime_button_press_with_empty_ring_settings(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """An unpaired chime also fails without attempting playback."""
        button = UnifiProtectChimePlayButton(
            coordinator=mock_coordinator,
            chime_id="chime1",
        )

        with pytest.raises(HomeAssistantError) as raised:
            await button.async_press()
        assert raised.value.translation_key == "chime_playback_unsupported"
        mock_coordinator.protect_client.chimes.play.assert_not_called()


class TestAsyncSetupEntryWithClients:
    """Tests for async_setup_entry with client data."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create mock coordinator with clients."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.protect_client = None
        coordinator.get_site = MagicMock(
            return_value={"id": "site1", "meta": {"name": "Default"}}
        )
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "name": "Test Switch",
                        "model": "USW-24-POE",
                        "state": "ONLINE",
                        "macAddress": "AA:BB:CC:DD:EE:FF",
                        "ipAddress": "192.168.1.10",
                        "features": ["switching"],
                        "interfaces": {},
                    },
                },
            },
            "stats": {},
            "clients": {
                "site1": {
                    "client1": {
                        "id": "client1",
                        "name": "Test Client",
                        "mac": "00:11:22:33:44:55",
                    },
                    "client2": {
                        "id": "client2",
                        "hostname": "laptop-hostname",
                        "mac": "66:77:88:99:AA:BB",
                    },
                    "client3": {
                        "id": "client3",
                        "mac": "CC:DD:EE:FF:00:11",
                    },
                },
            },
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    @pytest.fixture
    def mock_config_entry(self, hass: HomeAssistant, mock_coordinator):
        """Create a mock entry that schedules its background coroutines."""
        entry = MagicMock()
        entry.runtime_data = MagicMock(coordinator=mock_coordinator)
        entry.async_create_background_task.side_effect = lambda hass, target, name: (
            hass.async_create_background_task(target, name)
        )
        mock_coordinator.async_get_wake_history = AsyncMock(return_value={})
        return entry

    async def test_setup_entry_creates_client_reconnect_buttons(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test that setup creates client reconnect buttons."""
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        # Should have client reconnect buttons
        reconnect_buttons = [
            e for e in added_entities if isinstance(e, UnifiClientReconnectButton)
        ]
        assert len(reconnect_buttons) == 3

    async def test_setup_entry_client_button_uses_name(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test client button uses name when available."""
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        reconnect_buttons = [
            e for e in added_entities if isinstance(e, UnifiClientReconnectButton)
        ]
        # Find button for client1 which has a name
        client1_button = next(
            (b for b in reconnect_buttons if b._client_id == "client1"), None
        )
        assert client1_button is not None

    async def test_setup_entry_client_button_uses_hostname_fallback(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test client button uses hostname when name not available."""
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        reconnect_buttons = [
            e for e in added_entities if isinstance(e, UnifiClientReconnectButton)
        ]
        # Find button for client2 which has hostname but no name
        client2_button = next(
            (b for b in reconnect_buttons if b._client_id == "client2"), None
        )
        assert client2_button is not None
        assert client2_button.translation_key == "client_reconnect"
        assert client2_button.translation_placeholders == {
            "client_name": "laptop-hostname"
        }

    async def test_setup_entry_client_button_uses_mac_fallback(
        self, hass: HomeAssistant, mock_coordinator, mock_config_entry
    ):
        """Test client button uses MAC when name and hostname not available."""
        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_config_entry, add_entities)

        reconnect_buttons = [
            e for e in added_entities if isinstance(e, UnifiClientReconnectButton)
        ]
        # Find button for client3 which only has MAC
        client3_button = next(
            (b for b in reconnect_buttons if b._client_id == "client3"), None
        )
        assert client3_button is not None
        assert client3_button.translation_key == "client_reconnect"
        assert client3_button.translation_placeholders == {
            "client_name": "CC:DD:EE:FF:00:11"
        }


class TestUnifiInsightsButtonAvailableEdgeCases:
    """Tests for available property edge cases in UnifiInsightsButton."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.protect_client = None
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "name": "Test Switch",
                        "model": "USW-24-POE",
                        "state": "ONLINE",
                        "macAddress": "AA:BB:CC:DD:EE:FF",
                    },
                },
            },
            "stats": {},
            "clients": {},
            "protect": {
                "cameras": {},
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    async def test_available_devices_not_dict(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test available returns False when devices is not a dict."""
        button = UnifiInsightsButton(
            coordinator=mock_coordinator,
            description=BUTTON_TYPES[0],
            site_id="site1",
            device_id="device1",
        )

        # Set devices to non-dict value
        mock_coordinator.data["devices"] = "not a dict"
        assert button.available is False

    async def test_available_site_devices_not_dict(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test available returns False when site_devices is not a dict."""
        button = UnifiInsightsButton(
            coordinator=mock_coordinator,
            description=BUTTON_TYPES[0],
            site_id="site1",
            device_id="device1",
        )

        # Set site devices to non-dict value
        mock_coordinator.data["devices"]["site1"] = "not a dict"
        assert button.available is False

    async def test_available_device_data_not_dict(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test available returns False when device_data is not a dict."""
        button = UnifiInsightsButton(
            coordinator=mock_coordinator,
            description=BUTTON_TYPES[0],
            site_id="site1",
            device_id="device1",
        )

        # Set device data to non-dict value
        mock_coordinator.data["devices"]["site1"]["device1"] = "not a dict"
        assert button.available is False


class TestPTZPatrolStopButtonException:
    """Tests for PTZ patrol stop button exception handling."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.protect_client = MagicMock()
        coordinator.protect_client.base_url = "https://192.168.1.1"
        coordinator.protect_client.ptz_stop_patrol = AsyncMock(
            side_effect=Exception("API Error")
        )
        coordinator.data = {
            "sites": {},
            "devices": {},
            "stats": {},
            "clients": {},
            "protect": {
                "cameras": {
                    "camera1": {
                        "id": "camera1",
                        "name": "PTZ Camera",
                        "state": "CONNECTED",
                        "isPtz": True,
                    },
                },
                "lights": {},
                "sensors": {},
                "nvrs": {},
                "viewers": {},
                "chimes": {},
                "liveviews": {},
            },
        }
        return coordinator

    async def test_ptz_stop_patrol_exception(
        self, hass: HomeAssistant, mock_coordinator
    ):
        """Test PTZ stop patrol handles exceptions."""
        button = UnifiProtectPTZPatrolStopButton(
            coordinator=mock_coordinator,
            camera_id="camera1",
        )

        with pytest.raises(HomeAssistantError, match="Unable to stop PTZ patrol"):
            await button.async_press()

        mock_coordinator.protect_client.ptz_stop_patrol.assert_called_once_with(
            camera_id="camera1",
        )


class TestUnifiInsightsPoePowerCycleButton:
    """Tests for UnifiInsightsPoePowerCycleButton."""

    @pytest.fixture
    def mock_coordinator(self, hass: HomeAssistant):
        """Create mock coordinator."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.network_client = MagicMock()
        coordinator.network_client.base_url = "https://192.168.1.1"
        coordinator.network_client.devices = MagicMock()
        coordinator.network_client.devices.execute_port_action = AsyncMock()
        coordinator.async_power_cycle_port = AsyncMock()
        coordinator.protect_client = None
        coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "name": "Test Switch",
                        "model": "USW-24-POE",
                        "state": "ONLINE",
                        "macAddress": "AA:BB:CC:DD:EE:FF",
                        "interfaces": {
                            "ports": [
                                {
                                    "idx": 1,
                                    "name": "Port 1",
                                    "poe": {"enabled": True},
                                },
                            ]
                        },
                    },
                },
            },
        }
        return coordinator

    async def test_poe_power_cycle_button_init(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test button initialization and properties."""
        button = UnifiInsightsPoePowerCycleButton(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
            port_idx=1,
            port_label="Port 1",
        )

        assert button.unique_id == "site1_device1_port1_poe_power_cycle"
        assert button.entity_category == EntityCategory.CONFIG
        assert button.entity_registry_enabled_default is False
        assert button.translation_key == "poe_power_cycle"
        assert button.translation_placeholders == {"port_label": "Port 1"}
        assert button.port_idx == 1

    async def test_poe_power_cycle_button_availability(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test button availability tracks port presence and PoE enablement."""
        mock_coordinator.device_available = True
        button = UnifiInsightsPoePowerCycleButton(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
            port_idx=1,
            port_label="Port 1",
        )

        # Initially available
        assert button.available is True

        # PoE disabled after setup -> button becomes unavailable
        ports = mock_coordinator.data["devices"]["site1"]["device1"]["interfaces"][
            "ports"
        ]
        ports[0]["poe"]["enabled"] = False
        assert button.available is False

        # Port disappears -> button becomes unavailable
        mock_coordinator.data["devices"]["site1"]["device1"]["interfaces"]["ports"] = []
        assert button.available is False

        # Port reappears with PoE enabled -> button becomes available again
        mock_coordinator.data["devices"]["site1"]["device1"]["interfaces"]["ports"] = [
            {"idx": 1, "name": "Port 1", "poe": {"enabled": True}}
        ]
        assert button.available is True

        # Device offline -> button becomes unavailable
        mock_coordinator.data["devices"]["site1"]["device1"]["state"] = "OFFLINE"
        assert button.available is False

    async def test_poe_power_cycle_button_press_uses_facade(
        self,
        hass: HomeAssistant,
        mock_coordinator: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Button press uses facade coroutine when present."""
        button = UnifiInsightsPoePowerCycleButton(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
            port_idx=1,
            port_label="Port 1",
        )

        with caplog.at_level(logging.INFO):
            await button.async_press()

        assert "Power cycling PoE port 1 on device device1" in caplog.text

        mock_coordinator.async_power_cycle_port.assert_awaited_once_with(
            "site1", "device1", 1
        )
        mock_coordinator.network_client.devices.execute_port_action.assert_not_called()

    async def test_poe_power_cycle_button_press_fallback(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Test button press falls back to execute_port_action."""
        mock_coordinator.async_power_cycle_port = None
        button = UnifiInsightsPoePowerCycleButton(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
            port_idx=1,
            port_label="Port 1",
        )

        await button.async_press()

        devices_api = mock_coordinator.network_client.devices
        devices_api.execute_port_action.assert_awaited_once_with(
            "site1", "device1", 1, "POWER_CYCLE"
        )

    async def test_poe_power_cycle_button_press_failure(
        self, hass: HomeAssistant, mock_coordinator: MagicMock
    ) -> None:
        """Button press raises HomeAssistantError on exception."""
        mock_coordinator.async_power_cycle_port = AsyncMock(
            side_effect=Exception("API Error")
        )
        button = UnifiInsightsPoePowerCycleButton(
            coordinator=mock_coordinator,
            site_id="site1",
            device_id="device1",
            port_idx=1,
            port_label="Port 1",
        )

        with pytest.raises(
            HomeAssistantError, match="Unable to power cycle PoE port 1"
        ):
            await button.async_press()


class TestSetupEntryPoeButtons:
    """Tests for setup entry PoE button discovery."""

    async def test_setup_entry_creates_poe_power_cycle_buttons(
        self, hass: HomeAssistant
    ):
        """Test that setup creates PoE power-cycle buttons for PoE ports only."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = None
        mock_coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "name": "Test Switch",
                        "model": "USW-24-POE",
                        "interfaces": {
                            "ports": [
                                {
                                    "idx": 1,
                                    "name": "Port 1",
                                    "poe": {"enabled": True},
                                },
                                {
                                    "idx": 2,
                                    "name": "Port 2",
                                    "poe": {"enabled": False},
                                },
                            ]
                        },
                    },
                },
            },
        }

        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.async_create_background_task.side_effect = (
            lambda hass, target, name: hass.async_create_background_task(target, name)
        )
        mock_coordinator.async_get_wake_history = AsyncMock(return_value={})
        mock_entry.entry_id = "test_entry"
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.options = {}

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        await async_setup_entry(hass, mock_entry, add_entities)

        poe_buttons = [
            e for e in added_entities if isinstance(e, UnifiInsightsPoePowerCycleButton)
        ]
        assert len(poe_buttons) == 1
        assert poe_buttons[0].unique_id == "site1_device1_port1_poe_power_cycle"
        assert poe_buttons[0].entity_category == EntityCategory.CONFIG
        assert poe_buttons[0].entity_registry_enabled_default is False

    def test_get_port_label(self):
        """Test _get_port_label helper variations."""
        assert _get_port_label({"name": "Custom Name"}, 1) == "Custom Name"
        assert _get_port_label({"name": "Port 1"}, 1) == "Port 1"
        assert _get_port_label({"media": "SFP+"}, 2) == "SFP+ 2"
        assert _get_port_label({}, 3) == "Port 3"

    def test_get_device_port_and_helpers(self):
        """Test get_device_port and related helper edge cases."""
        # Missing site_id or device_id
        assert get_device_port({}, None, "dev1", 1) is None
        assert get_device_port({}, "site1", None, 1) is None

        # Non-dict coordinator data or subkeys
        assert get_device_port(None, "site1", "dev1", 1) is None
        assert get_device_port({"devices": None}, "site1", "dev1", 1) is None
        assert get_device_port({"devices": {"site1": None}}, "site1", "dev1", 1) is None
        assert (
            get_device_port({"devices": {"site1": {"dev1": None}}}, "site1", "dev1", 1)
            is None
        )

        # get_device_ports non-dict interfaces and non-list ports
        assert get_device_ports({}) == []
        assert get_device_ports({"interfaces": "invalid", "ports": "invalid"}) == []

        # Top-level (legacy-merged) ports win; interfaces["ports"] is the fallback
        legacy = [{"idx": 1, "poe": {"enabled": True}}]
        v1 = [{"idx": 2, "poe": {"enabled": True}}]
        both = {"ports": legacy, "interfaces": {"ports": v1}}
        assert get_device_ports(both) == legacy
        assert get_device_ports({"ports": legacy, "interfaces": {}}) == legacy
        assert get_device_ports({"ports": [], "interfaces": {"ports": v1}}) == v1
        assert get_device_ports({"interfaces": {"ports": [None, *v1]}}) == v1

        # Successful port lookup and power-cycle capability checks
        data = {
            "devices": {
                "site1": {
                    "dev1": {
                        "interfaces": {
                            "ports": [
                                {"idx": 1, "poe": {"enabled": True}},
                                {"idx": 2, "poe": {"enabled": False}},
                            ]
                        }
                    }
                }
            }
        }
        port1 = get_device_port(data, "site1", "dev1", 1)
        assert port1 == {"idx": 1, "poe": {"enabled": True}}
        assert port_can_be_power_cycled(port1) is True

        port2 = get_device_port(data, "site1", "dev1", 2)
        assert port2 == {"idx": 2, "poe": {"enabled": False}}
        assert port_can_be_power_cycled(port2) is False

        assert get_device_port(data, "site1", "dev1", 3) is None

    async def test_setup_entry_poe_discovery_edge_cases_and_dedup(
        self, hass: HomeAssistant
    ):
        """Test PoE button discovery edge cases, legacy formats, and deduplication."""
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = None
        mock_coordinator.data = {
            "sites": {"site1": {"id": "site1"}},
            "devices": {
                "site1": {
                    "device1": {
                        "id": "device1",
                        "ports": [
                            None,  # non-dict
                            {"name": "No Idx"},  # no idx
                            {"idx": "invalid"},  # non-int idx
                            {
                                "idx": 3,
                                "poe": {"enabled": True},
                                "name": "AP Port",
                            },
                            {
                                "idx": 4,
                                "poe": {"enabled": False},
                            },
                            {
                                "idx": 6,
                                "name": "No PoE Info",
                            },
                        ],
                    },
                },
            },
        }

        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.async_create_background_task.side_effect = (
            lambda hass, target, name: hass.async_create_background_task(target, name)
        )
        mock_coordinator.async_get_wake_history = AsyncMock(return_value={})
        mock_entry.entry_id = "test_entry"
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.options = {}

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        listeners = []
        mock_coordinator.async_add_listener = listeners.append

        await async_setup_entry(hass, mock_entry, add_entities)

        poe_buttons = [
            e for e in added_entities if isinstance(e, UnifiInsightsPoePowerCycleButton)
        ]
        assert len(poe_buttons) == 1
        assert poe_buttons[0].unique_id == "site1_device1_port3_poe_power_cycle"
        assert poe_buttons[0].translation_placeholders == {"port_label": "AP Port"}

        # Trigger rediscovery callback to verify deduplication
        prev_count = len(added_entities)
        for listener in listeners:
            listener()
        assert len(added_entities) == prev_count


class TestGenerateVoucherButton:
    """Tests for UnifiGenerateVoucherButton."""

    @pytest.fixture
    def mock_coordinator(self):
        coord = MagicMock()
        coord.data = {
            "sites": {"default": {"desc": "Default"}},
            "vouchers": {"default": []},
        }
        coord.vouchers_available = MagicMock(return_value=True)
        coord.get_voucher_settings = MagicMock(
            return_value=VoucherSettings(
                duration_minutes=1440,
                guest_limit=1,
                download_limit_mbps=0,
                upload_limit_mbps=0,
                data_limit_mb=0,
            )
        )
        coord.async_generate_voucher = AsyncMock(
            return_value=[MagicMock(code="1234567890")]
        )
        return coord

    def test_init_and_properties(self, mock_coordinator):
        button = UnifiGenerateVoucherButton(
            mock_coordinator,
            "default",
            unique_id="console_default_generate_voucher",
        )
        assert button.unique_id == "console_default_generate_voucher"
        assert button.translation_key == "generate_voucher"
        assert button.available is True
        assert button.has_entity_name is True

    def test_available(self, mock_coordinator):
        button = UnifiGenerateVoucherButton(
            mock_coordinator,
            "default",
            unique_id="console_default_generate_voucher",
        )
        mock_coordinator.vouchers_available.return_value = False
        assert button.available is False

    async def test_async_press_success(self, mock_coordinator):
        button = UnifiGenerateVoucherButton(
            mock_coordinator,
            "default",
            unique_id="console_default_generate_voucher",
        )
        await button.async_press()
        mock_coordinator.get_voucher_settings.assert_called_once_with("default")
        mock_coordinator.async_generate_voucher.assert_awaited_once_with(
            "default",
            name="Home Assistant",
            count=1,
            time_limit_minutes=1440,
            authorized_guest_limit=1,
        )

    async def test_async_press_empty_result_raises_error(self, mock_coordinator):
        button = UnifiGenerateVoucherButton(
            mock_coordinator,
            "default",
            unique_id="console_default_generate_voucher",
        )
        mock_coordinator.async_generate_voucher.return_value = []
        with pytest.raises(HomeAssistantError, match="UniFi returned no voucher"):
            await button.async_press()

    async def test_async_press_coordinator_error_raises_error(self, mock_coordinator):
        button = UnifiGenerateVoucherButton(
            mock_coordinator,
            "default",
            unique_id="console_default_generate_voucher",
        )
        mock_coordinator.async_generate_voucher.side_effect = RuntimeError(
            "API failure"
        )
        with pytest.raises(
            HomeAssistantError, match="Unable to generate a guest voucher"
        ):
            await button.async_press()

    async def test_discovery_and_deduplication(self, hass, mock_coordinator):
        mock_entry = MagicMock()
        mock_entry.data = {"console_id": "console"}
        mock_entry.async_create_background_task.side_effect = (
            lambda hass, target, name: hass.async_create_background_task(target, name)
        )
        mock_coordinator.async_get_wake_history = AsyncMock(return_value={})
        mock_entry.entry_id = "test_entry"
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator
        mock_entry.options = {}

        added_entities: list = []

        def add_entities(new_entities, **kwargs):
            added_entities.extend(new_entities)

        listeners = []
        mock_coordinator.async_add_listener = listeners.append

        await async_setup_entry(hass, mock_entry, add_entities)

        voucher_buttons = [
            e for e in added_entities if isinstance(e, UnifiGenerateVoucherButton)
        ]
        assert len(voucher_buttons) == 1
        assert voucher_buttons[0].unique_id == "console_default_generate_voucher"

        # Deduplication
        prev_count = len(added_entities)
        for listener in listeners:
            listener()
        assert len(added_entities) == prev_count

    async def test_button_press_through_real_facade_and_real_endpoint_pins_http(
        self, hass: HomeAssistant
    ) -> None:
        """Button press through real facade and real endpoint pins POST URL and body."""
        post_response = {
            "vouchers": [
                {
                    "id": "v-1",
                    "code": "1234567890",
                    "name": "Home Assistant",
                    "timeLimitMinutes": 90,
                }
            ]
        }
        session = _Session(
            [
                _Response(post_response),
                _Response(post_response),
            ]
        )
        client = UniFiNetworkClient(
            auth=ApiKeyAuth(api_key="test-key"),
            base_url="https://192.168.1.1",
            connection_type=ConnectionType.LOCAL,
            session=session,  # type: ignore[arg-type]
        )
        config_coord = MagicMock()
        config_coord.data = {"sites": {"default": {"id": "default"}}, "vouchers": {}}
        config_coord.vouchers_available.return_value = True
        config_coord.async_refresh_vouchers = AsyncMock()

        facade = UnifiFacadeCoordinator(
            hass=hass,
            network_client=client,
            protect_client=None,
            entry=MagicMock(),
            config_coordinator=config_coord,
            device_coordinator=MagicMock(),
            protect_coordinator=None,
        )

        settings = facade.get_voucher_settings("default")
        settings.duration_minutes = 90
        settings.guest_limit = 3
        settings.download_limit_mbps = 1.5
        settings.upload_limit_mbps = 0.5
        settings.data_limit_mb = 2048

        button = UnifiGenerateVoucherButton(
            facade,
            "default",
            unique_id="console_default_generate_voucher",
        )
        await button.async_press()

        assert len(session.requests) == 1
        req1 = session.requests[0]
        assert req1["method"] == "POST"
        assert (
            req1["url"]
            == "https://192.168.1.1/proxy/network/integration/v1/sites/default/hotspot/vouchers"
        )
        assert req1["json"] == {
            "count": 1,
            "name": "Home Assistant",
            "timeLimitMinutes": 90,
            "authorizedGuestLimit": 3,
            "dataUsageLimitMBytes": 2048,
            "rxRateLimitKbps": 1500,
            "txRateLimitKbps": 500,
        }

        # Second press: zero limits omitted
        settings.guest_limit = 0
        settings.download_limit_mbps = 0.0
        settings.upload_limit_mbps = 0.0
        settings.data_limit_mb = 0
        await button.async_press()

        assert len(session.requests) == 2
        req2 = session.requests[1]
        assert req2["method"] == "POST"
        assert (
            req2["url"]
            == "https://192.168.1.1/proxy/network/integration/v1/sites/default/hotspot/vouchers"
        )
        assert req2["json"] == {
            "count": 1,
            "name": "Home Assistant",
            "timeLimitMinutes": 90,
        }


class TestUnifiClientWakeButton:
    """Tests for UnifiClientWakeButton entity."""

    @pytest.mark.parametrize("name", ["", "  "])
    def test_wake_button_blank_name_uses_hostname(
        self, hass: HomeAssistant, name: str
    ) -> None:
        """Empty and whitespace names fall through to the live hostname."""
        mac = "00:11:22:33:44:55"
        coordinator = MagicMock(hass=hass)
        coordinator.data = {
            "clients": {
                "site1": {
                    "c1": {"macAddress": mac, "name": name, "hostname": "Host PC"}
                }
            }
        }
        button = UnifiClientWakeButton(
            coordinator, mac, site_id="site1", fallback_name="History PC"
        )
        assert button.translation_placeholders == {"client_name": "Host PC"}

    def test_wake_button_identity(self, hass: HomeAssistant) -> None:
        """Wake button identity, unique_id, and device_info attributes."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {"clients": {}}
        mac = "00:11:22:33:44:55"

        button = UnifiClientWakeButton(coordinator, mac, site_id="site1")

        assert button.unique_id == f"unifi_insights_{mac}_wake"
        assert button.translation_key == "client_wake"
        assert button.has_entity_name is True
        assert button.translation_placeholders == {"client_name": mac}
        assert button.device_info is not None
        assert button.device_info["identifiers"] == {(DOMAIN, "site_site1")}
        assert button.device_info["manufacturer"] == MANUFACTURER
        assert button.device_info["model"] == "UniFi Site"

    def test_wake_button_without_site_does_not_attach_a_device(
        self, hass: HomeAssistant
    ) -> None:
        """A restored button without site metadata remains unattached."""
        coordinator = MagicMock(hass=hass)
        coordinator.data = {"clients": {}}

        button = UnifiClientWakeButton(coordinator, "00:11:22:33:44:55", site_id=None)

        assert button.device_info is None

    def test_wake_button_device_info_attaches_to_gateway_or_site_when_no_gateway(
        self, hass: HomeAssistant
    ) -> None:
        """Device info attaches to site gateway, or site device when no gateway."""
        mac = "00:11:22:33:44:55"
        coordinator = MagicMock()
        coordinator.hass = hass

        # Site with gateway: attaches to gateway
        coordinator.data = {
            "devices": {
                "site1": {
                    "gw1": {
                        "model": "UniFi Dream Machine Pro",
                        "features": ["gateway"],
                    }
                }
            },
            "clients": {"site1": {"c1": {"macAddress": mac, "name": "Gaming PC"}}},
        }
        btn_gw = UnifiClientWakeButton(coordinator, mac, site_id="site1")
        assert btn_gw.device_info is not None
        assert btn_gw.device_info["identifiers"] == {(DOMAIN, "site1_gw1")}

        # Site without gateway: falls back to site device
        coordinator.data = {
            "sites": {"site1": {"name": "Home Site"}},
            "devices": {"site1": {}},
            "clients": {"site1": {"c1": {"macAddress": mac, "name": "Gaming PC"}}},
        }
        btn_site = UnifiClientWakeButton(coordinator, mac, site_id="site1")
        assert btn_site.device_info is not None
        assert btn_site.device_info["identifiers"] == {(DOMAIN, "site_site1")}
        assert btn_site.device_info["name"] == "UniFi Site (Home Site)"
        assert btn_site.device_info["manufacturer"] == MANUFACTURER
        assert btn_site.device_info["model"] == "UniFi Site"

    def test_wake_button_device_info_never_contains_client_identifier(
        self, hass: HomeAssistant
    ) -> None:
        """Wake button device_info never contains a client_ identifier."""
        mac = "00:11:22:33:44:55"
        coordinator = MagicMock()
        coordinator.hass = hass

        # Connected client on site with gateway
        coordinator.data = {
            "devices": {
                "site1": {
                    "gw1": {
                        "model": "UniFi Gateway Max",
                    }
                }
            },
            "clients": {"site1": {"c1": {"macAddress": mac, "name": "Workstation"}}},
        }
        btn_gw = UnifiClientWakeButton(coordinator, mac, site_id="site1")
        for _, identifier in btn_gw.device_info["identifiers"]:
            assert not identifier.startswith("client_")

        # Offline unseeded client
        coordinator.data = {"devices": {}, "clients": {}}
        btn_offline = UnifiClientWakeButton(coordinator, mac, site_id="site1")
        for _, identifier in btn_offline.device_info["identifiers"]:
            assert not identifier.startswith("client_")

    def test_wake_button_translation_placeholders_live_history_and_mac_only(
        self, hass: HomeAssistant
    ) -> None:
        """Name uses translation placeholder for live, history, and MAC-only clients."""
        mac = "00:11:22:33:44:55"
        coordinator = MagicMock()
        coordinator.hass = hass

        # Live client name
        coordinator.data = {
            "clients": {"site1": {"c1": {"macAddress": mac, "name": "Gaming PC"}}}
        }
        btn_live = UnifiClientWakeButton(coordinator, mac, site_id="site1")
        assert btn_live.translation_placeholders == {"client_name": "Gaming PC"}

        # Live client hostname fallback
        coordinator.data = {
            "clients": {"site1": {"c1": {"macAddress": mac, "hostname": "pc-host"}}}
        }
        btn_host = UnifiClientWakeButton(coordinator, mac, site_id="site1")
        assert btn_host.translation_placeholders == {"client_name": "pc-host"}

        # History-only client
        coordinator.data = {"clients": {}}
        btn_hist = UnifiClientWakeButton(
            coordinator, mac, fallback_name="Saved PC", site_id="site1"
        )
        assert btn_hist.translation_placeholders == {"client_name": "Saved PC"}

        # MAC-only client (offline and unnamed)
        btn_mac = UnifiClientWakeButton(coordinator, mac, site_id="site1")
        assert btn_mac.translation_placeholders == {"client_name": mac}

    def test_wake_button_offline_seeded_client_attaches_to_seed_site_gateway(
        self, hass: HomeAssistant
    ) -> None:
        """Offline seeded client button attaches to the seed site's gateway."""
        mac = "00:11:22:33:44:99"
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {
            "devices": {
                "remote_site": {
                    "gw_remote": {
                        "model": "UniFi Gateway Lite",
                    }
                }
            },
            "clients": {},
        }

        button = UnifiClientWakeButton(
            coordinator,
            mac,
            fallback_name="Offline Seeded PC",
            site_id="remote_site",
        )
        assert button.device_info is not None
        assert button.device_info["identifiers"] == {(DOMAIN, "remote_site_gw_remote")}
        assert button.translation_placeholders == {"client_name": "Offline Seeded PC"}

    def test_wake_button_name_from_live_name(self, hass: HomeAssistant) -> None:
        """Wake button uses client live name when connected."""
        mac = "00:11:22:33:44:55"
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {
            "clients": {"site1": {"c1": {"macAddress": mac, "name": "Gaming PC"}}}
        }

        button = UnifiClientWakeButton(coordinator, mac, site_id="site1")
        assert button.translation_placeholders == {"client_name": "Gaming PC"}

    def test_wake_button_name_falls_back_to_hostname(self, hass: HomeAssistant) -> None:
        """Wake button uses live hostname when name is missing."""
        mac = "00:11:22:33:44:55"
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {
            "clients": {"site1": {"c1": {"macAddress": mac, "hostname": "pc-host"}}}
        }

        button = UnifiClientWakeButton(coordinator, mac, site_id="site1")
        assert button.translation_placeholders == {"client_name": "pc-host"}

    def test_wake_button_name_uses_history_name_when_not_connected(
        self, hass: HomeAssistant
    ) -> None:
        """Wake button uses fallback_name from history when client is offline."""
        mac = "00:11:22:33:44:55"
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {"clients": {}}

        button = UnifiClientWakeButton(
            coordinator, mac, fallback_name="Saved PC", site_id="site1"
        )
        assert button.translation_placeholders == {"client_name": "Saved PC"}

    def test_wake_button_name_falls_back_to_mac(self, hass: HomeAssistant) -> None:
        """Wake button falls back to MAC when no other names exist."""
        mac = "00:11:22:33:44:55"
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {"clients": {}}

        button = UnifiClientWakeButton(coordinator, mac, site_id="site1")
        assert button.translation_placeholders == {"client_name": mac}

    @pytest.mark.parametrize("device_available", [True, False])
    def test_wake_button_availability_follows_console(
        self, hass: HomeAssistant, *, device_available: bool
    ) -> None:
        """Offline clients remain wakeable only while their console is reachable."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {"clients": {}}
        coordinator.device_available = device_available

        button = UnifiClientWakeButton(
            coordinator, "00:11:22:33:44:55", site_id="site1"
        )
        assert button.available is device_available

    async def test_wake_button_press_calls_facade_with_mac(
        self, hass: HomeAssistant
    ) -> None:
        """Button press delegates to coordinator.async_wake_client with MAC."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {"clients": {}}
        coordinator.async_wake_client = AsyncMock()
        mac = "00:11:22:33:44:55"

        button = UnifiClientWakeButton(coordinator, mac, site_id="site1")
        await button.async_press()

        coordinator.async_wake_client.assert_awaited_once_with(mac, client_name=mac)

    async def test_wake_button_press_wraps_unexpected_errors(
        self, hass: HomeAssistant
    ) -> None:
        """Unexpected error during press is wrapped in HomeAssistantError."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {"clients": {}}
        coordinator.async_wake_client = AsyncMock(
            side_effect=RuntimeError("Socket error")
        )
        mac = "00:11:22:33:44:55"

        button = UnifiClientWakeButton(coordinator, mac, site_id="site1")
        with pytest.raises(HomeAssistantError, match=f"Unable to wake client {mac}"):
            await button.async_press()

    async def test_wake_button_press_keeps_translated_errors(
        self, hass: HomeAssistant
    ) -> None:
        """Translated HomeAssistantError passes through without re-wrapping."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {"clients": {}}
        err = HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="wake_failed",
            translation_placeholders={"client_name": "Test PC", "error": "test"},
        )
        coordinator.async_wake_client = AsyncMock(side_effect=err)

        button = UnifiClientWakeButton(
            coordinator, "00:11:22:33:44:55", site_id="site1"
        )
        with pytest.raises(HomeAssistantError) as exc_info:
            await button.async_press()

        assert exc_info.value is err
        assert exc_info.value.translation_key == "wake_failed"


@pytest.mark.parametrize("button_kind", ["reconnect", "wake", "restart"])
async def test_buttons_publish_device_coordinator_availability(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    *,
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    mock_local_auth: MagicMock,
    enable_custom_integrations,
    button_kind: str,
) -> None:
    """Real platform subscriptions publish outage/recovery and unload cleanly."""
    mac = "00:11:22:33:44:55"
    mock_network_client.sites.get_all.return_value = [
        {"id": "site1", "name": "Home", "internalReference": "default"}
    ]
    mock_network_client.clients.get_all.return_value = [
        {"id": "c1", "macAddress": mac, "name": "Gaming PC", "type": "WIRED"}
    ]
    mock_network_client.clients.get_historical_legacy = AsyncMock(return_value=[])
    mock_network_client.devices.get_all.return_value = [
        {
            "id": "gateway",
            "name": "Gateway",
            "model": "UniFi Dream Machine Pro",
            "state": "ONLINE",
            "features": ["gateway"],
        }
    ]
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_CLIENT_CONTROL: True}
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    registry = er.async_get(hass)
    unique_id = {
        "reconnect": "site1_c1_reconnect",
        "wake": f"{DOMAIN}_{mac}_wake",
        "restart": "site1_gateway_device_restart",
    }[button_kind]
    entity_id = registry.async_get_entity_id("button", DOMAIN, unique_id)
    assert entity_id is not None
    registry.async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    # Enabling a disabled entity reloads the entry; read its live facade.
    facade = mock_config_entry.runtime_data.coordinator
    device = facade._device_coordinator
    assert hass.states.get(entity_id).state != "unavailable"
    mock_network_client.devices.get_all.side_effect = UniFiConnectionError("offline")
    await device.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"
    mock_network_client.devices.get_all.side_effect = None
    await device.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state != "unavailable"
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert not facade._listeners


class TestWakeButtonSetup:
    """Tests for Wake button platform setup and registry lifecycle."""

    async def test_wake_button_real_platform_device_registry_and_rendered_name(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        *,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        mock_local_auth: MagicMock,
        enable_custom_integrations,
    ) -> None:
        """Real platform creates a disabled Wake row on its gateway with its name."""
        mac = "00:11:22:33:44:55"
        mock_network_client.sites.get_all.return_value = [
            {"id": "site1", "name": "Home", "internalReference": "default"}
        ]
        mock_network_client.clients.get_all.return_value = [
            {"id": "c1", "macAddress": mac, "name": "Gaming PC", "type": "WIRED"}
        ]
        mock_network_client.clients.get_historical_legacy = AsyncMock(return_value=[])
        mock_network_client.devices.get_all.return_value = [
            {
                "id": "gateway",
                "name": "Home Gateway",
                "model": "UniFi Dream Machine Pro",
                "state": "ONLINE",
                "features": ["gateway"],
            }
        ]
        mock_config_entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(
            mock_config_entry, options={CONF_CLIENT_CONTROL: True}
        )
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "button", DOMAIN, f"{DOMAIN}_{mac}_wake"
        )
        assert entity_id is not None
        row = registry.async_get(entity_id)
        assert row.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert row.original_name == "Wake Gaming PC"
        assert row.device_id is not None
        device = dr.async_get(hass).async_get(row.device_id)
        assert device.identifiers == {(DOMAIN, "site1_gateway")}
        assert hass.states.get(entity_id) is None
        registry.async_update_entity(entity_id, disabled_by=None)
        mock_network_client.clients.get_all.return_value = []
        mock_network_client.clients.get_historical_legacy.return_value = [
            {
                "mac": mac,
                "is_wired": True,
                "name": "Old PC",
                "last_seen": 1,
            }
        ]
        await hass.config_entries.async_reload(mock_config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.attributes["friendly_name"] == "Home Gateway Wake Old PC"
        assert registry.async_get(entity_id).device_id == device.id

    async def test_wake_history_seed_cancelled_on_entry_unload(
        self, hass: HomeAssistant, init_integration: MockConfigEntry
    ) -> None:
        """A pending seed is cancelled by config entry unload, not HA shutdown."""
        entry = init_integration
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def pending_history(**kwargs) -> dict[str, tuple[str, str]]:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            return {}

        entry.runtime_data.coordinator.async_get_wake_history = pending_history
        await async_setup_entry(hass, entry, lambda entities: None)
        await started.wait()
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert cancelled.is_set()

    @pytest.mark.parametrize("history_case", ["unnamed", "old", "raises"])
    @pytest.mark.parametrize("registry_name", [None, "Wake Registry PC", "Wake   "])
    @pytest.mark.parametrize("existing_device", [False, True])
    async def test_setup_restores_enabled_offline_wake_before_history(
        self,
        hass: HomeAssistant,
        history_case: str,
        *,
        registry_name: str | None,
        existing_device: bool,
    ) -> None:
        """Enabled offline buttons exist and work before any history response."""
        mac = "00:11:22:33:44:99"
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {
            "sites": {"site1": {}},
            "devices": {},
            "clients": {},
            "protect": {},
        }
        coordinator.protect_client = None
        coordinator.async_wake_client = AsyncMock()
        started, proceed = asyncio.Event(), asyncio.Event()

        async def history(**kwargs) -> dict[str, tuple[str, str]]:
            started.set()
            await proceed.wait()
            if history_case == "raises":
                msg = "history unavailable"
                raise RuntimeError(msg)
            records = (
                [
                    {
                        "mac": mac,
                        "is_wired": True,
                        "name": "Old PC",
                        "last_seen": 10_000_000.0 - 40 * 86400,
                    }
                ]
                if history_case == "old"
                else [{"mac": mac, "is_wired": True}]
            )
            names = select_wake_history(
                records, now=10_000_000.0, enabled_macs=kwargs.get("enabled_macs")
            )
            return {mac: (name, "site1") for mac, name in names.items()}

        coordinator.async_get_wake_history = history
        entry = MockConfigEntry(domain=DOMAIN, options={CONF_CLIENT_CONTROL: True})
        entry.add_to_hass(hass)
        entry.runtime_data = MagicMock(coordinator=coordinator)
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, "site1_old_gateway")}
        )
        row = er.async_get(hass).async_get_or_create(
            "button",
            DOMAIN,
            f"unifi_insights_{mac}_wake",
            config_entry=entry,
            device_id=device.id if existing_device else None,
            original_name=registry_name,
        )
        added: list = []
        await async_setup_entry(hass, entry, added.extend)
        wakes = [e for e in added if isinstance(e, UnifiClientWakeButton)]
        assert len(wakes) == 1
        button = wakes[0]
        assert button.device_info["identifiers"] == (
            device.identifiers if existing_device else {(DOMAIN, "site_site1")}
        )
        saved_name = "Registry PC" if registry_name == "Wake Registry PC" else mac
        assert button.translation_placeholders == {"client_name": saved_name}
        await button.async_press()
        coordinator.async_wake_client.assert_awaited_once_with(
            mac, client_name=saved_name
        )
        assert er.async_get(hass).async_get(row.entity_id).disabled_by is None
        await started.wait()
        proceed.set()
        await hass.async_block_till_done(wait_background_tasks=True)
        assert button.translation_placeholders == {
            "client_name": "Old PC" if history_case == "old" else saved_name
        }
        assert len([e for e in added if isinstance(e, UnifiClientWakeButton)]) == 1

    async def test_setup_creates_wake_buttons_for_wired_clients_only(
        self, hass: HomeAssistant
    ) -> None:
        """Only wired clients with valid MAC get Wake buttons."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {
            "devices": {},
            "protect": {},
            "clients": {
                "site1": {
                    "wired": {"type": "WIRED", "macAddress": "00:11:22:33:44:01"},
                    "wireless": {"type": "WIRELESS", "macAddress": "00:11:22:33:44:02"},
                    "vpn": {"type": "VPN"},
                    "empty_type": {"type": "", "macAddress": "00:11:22:33:44:03"},
                    "no_mac": {"type": "WIRED"},
                }
            },
        }
        coordinator.protect_client = None
        coordinator.async_get_wake_history = AsyncMock(return_value={})

        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id="wake_entry",
            options={CONF_CLIENT_CONTROL: True},
        )
        entry.add_to_hass(hass)
        entry.runtime_data = MagicMock(coordinator=coordinator)

        added: list = []
        await async_setup_entry(hass, entry, lambda ents, **kw: added.extend(ents))

        wake_buttons = [e for e in added if isinstance(e, UnifiClientWakeButton)]
        assert len(wake_buttons) == 1
        assert wake_buttons[0].unique_id == "unifi_insights_00:11:22:33:44:01_wake"

    async def test_setup_dedupes_wake_buttons_across_sites_and_rediscovery(
        self, hass: HomeAssistant
    ) -> None:
        """Same client across sites or rediscovery creates only one Wake button."""
        mac = "00:11:22:33:44:01"
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {
            "devices": {},
            "protect": {},
            "clients": {
                "site1": {"c1": {"type": "WIRED", "macAddress": mac}},
                "site2": {"c2": {"type": "WIRED", "macAddress": mac}},
            },
        }
        coordinator.protect_client = None
        coordinator.async_get_wake_history = AsyncMock(return_value={})

        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id="wake_entry",
            options={CONF_CLIENT_CONTROL: True},
        )
        entry.add_to_hass(hass)
        entry.runtime_data = MagicMock(coordinator=coordinator)

        added: list = []
        listeners = []
        coordinator.async_add_listener = listeners.append

        await async_setup_entry(hass, entry, lambda ents, **kw: added.extend(ents))

        wake_buttons = [e for e in added if isinstance(e, UnifiClientWakeButton)]
        assert len(wake_buttons) == 1

        # Run listener
        for listener in listeners:
            listener()

        wake_buttons_after = [e for e in added if isinstance(e, UnifiClientWakeButton)]
        assert len(wake_buttons_after) == 1

    async def test_wake_button_added_when_client_turns_wired_later(
        self, hass: HomeAssistant
    ) -> None:
        """Client initially wireless gets Wake button when it becomes wired."""
        mac = "00:11:22:33:44:01"
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {
            "devices": {},
            "protect": {},
            "clients": {"site1": {"c1": {"type": "WIRELESS", "macAddress": mac}}},
        }
        coordinator.protect_client = None
        coordinator.async_get_wake_history = AsyncMock(return_value={})

        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id="wake_entry",
            options={CONF_CLIENT_CONTROL: True},
        )
        entry.add_to_hass(hass)
        entry.runtime_data = MagicMock(coordinator=coordinator)

        added: list = []
        listeners = []
        coordinator.async_add_listener = listeners.append

        await async_setup_entry(hass, entry, lambda ents, **kw: added.extend(ents))

        wake_buttons = [e for e in added if isinstance(e, UnifiClientWakeButton)]
        assert len(wake_buttons) == 0

        # Change client to WIRED and notify coordinator listener
        coordinator.data["clients"]["site1"]["c1"]["type"] = "WIRED"
        for listener in listeners:
            listener()

        wake_buttons_after = [e for e in added if isinstance(e, UnifiClientWakeButton)]
        assert len(wake_buttons_after) == 1
        assert wake_buttons_after[0].unique_id == f"unifi_insights_{mac}_wake"

    async def test_setup_keeps_disabled_offline_wake_row_without_creating_entity(
        self, hass: HomeAssistant
    ) -> None:
        """Offline clients outside history seed are not restored as live entities."""
        mac = "00:11:22:33:44:99"
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {"devices": {}, "protect": {}, "clients": {}}
        coordinator.protect_client = None
        coordinator.async_get_wake_history = AsyncMock(return_value={})

        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id="wake_entry",
            options={CONF_CLIENT_CONTROL: True},
        )
        entry.add_to_hass(hass)
        entry.runtime_data = MagicMock(coordinator=coordinator)

        ent_reg = er.async_get(hass)
        reg_entry = ent_reg.async_get_or_create(
            "button",
            DOMAIN,
            f"unifi_insights_{mac}_wake",
            config_entry=entry,
            disabled_by=er.RegistryEntryDisabler.INTEGRATION,
        )

        added: list = []
        await async_setup_entry(hass, entry, lambda ents, **kw: added.extend(ents))

        # No live entity created for unseeded offline client
        wake_buttons = [e for e in added if isinstance(e, UnifiClientWakeButton)]
        assert len(wake_buttons) == 0

        # Registry row remains untouched
        assert ent_reg.async_get(reg_entry.entity_id) is not None

    async def test_setup_keeps_registered_wake_button_when_client_is_now_wireless(
        self, hass: HomeAssistant
    ) -> None:
        """Registered Wake button stays even if client is now wireless."""
        mac = "00:11:22:33:44:99"
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {
            "devices": {},
            "protect": {},
            "clients": {"site1": {"c1": {"type": "WIRELESS", "macAddress": mac}}},
        }
        coordinator.protect_client = None
        coordinator.async_get_wake_history = AsyncMock(return_value={})

        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id="wake_entry",
            options={CONF_CLIENT_CONTROL: True},
        )
        entry.add_to_hass(hass)
        entry.runtime_data = MagicMock(coordinator=coordinator)

        ent_reg = er.async_get(hass)
        ent_reg.async_get_or_create(
            "button",
            DOMAIN,
            f"unifi_insights_{mac}_wake",
            config_entry=entry,
        )

        added: list = []
        await async_setup_entry(hass, entry, lambda ents, **kw: added.extend(ents))

        wake_buttons = [e for e in added if isinstance(e, UnifiClientWakeButton)]
        assert len(wake_buttons) == 1
        assert wake_buttons[0].unique_id == f"unifi_insights_{mac}_wake"

    async def test_setup_creates_wake_buttons_from_history(
        self, hass: HomeAssistant
    ) -> None:
        """Wake button created from startup history seed with fallback name."""
        mac = "00:11:22:33:44:77"
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {
            "devices": {"site1": {"gw1": {"model": "UniFi Security Gateway"}}},
            "protect": {},
            "clients": {},
        }
        coordinator.protect_client = None
        coordinator.async_get_wake_history = AsyncMock(
            return_value={mac: ("History PC", "site1")}
        )

        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id="wake_entry",
            options={CONF_CLIENT_CONTROL: True},
        )
        entry.add_to_hass(hass)
        entry.runtime_data = MagicMock(coordinator=coordinator)

        added: list = []
        await async_setup_entry(hass, entry, lambda ents, **kw: added.extend(ents))

        wake_buttons = [e for e in added if isinstance(e, UnifiClientWakeButton)]
        assert len(wake_buttons) == 1
        assert wake_buttons[0].unique_id == f"unifi_insights_{mac}_wake"
        assert wake_buttons[0].translation_placeholders == {"client_name": "History PC"}
        assert wake_buttons[0].device_info is not None
        assert wake_buttons[0].device_info["identifiers"] == {(DOMAIN, "site1_gw1")}

    async def test_setup_creates_one_button_when_registry_history_and_live_overlap(
        self, hass: HomeAssistant
    ) -> None:
        """Single button created when MAC exists in registry, history, and live."""
        mac = "00:11:22:33:44:88"
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {
            "devices": {},
            "protect": {},
            "clients": {
                "site1": {"c1": {"type": "WIRED", "macAddress": mac, "name": "Live PC"}}
            },
        }
        coordinator.protect_client = None
        coordinator.async_get_wake_history = AsyncMock(
            return_value={mac: ("History PC", "site1")}
        )

        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id="wake_entry",
            options={CONF_CLIENT_CONTROL: True},
        )
        entry.add_to_hass(hass)
        entry.runtime_data = MagicMock(coordinator=coordinator)

        ent_reg = er.async_get(hass)
        ent_reg.async_get_or_create(
            "button",
            DOMAIN,
            f"unifi_insights_{mac}_wake",
            config_entry=entry,
        )

        added: list = []
        await async_setup_entry(hass, entry, lambda ents, **kw: added.extend(ents))

        wake_buttons = [e for e in added if isinstance(e, UnifiClientWakeButton)]
        assert len(wake_buttons) == 1
        assert wake_buttons[0].translation_placeholders == {"client_name": "Live PC"}

    async def test_setup_ignores_history_failure(self, hass: HomeAssistant) -> None:
        """History loading failure does not break button platform setup."""
        mac = "00:11:22:33:44:01"
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {
            "devices": {},
            "protect": {},
            "clients": {"site1": {"c1": {"type": "WIRED", "macAddress": mac}}},
        }
        coordinator.protect_client = None
        coordinator.async_get_wake_history = AsyncMock(
            side_effect=RuntimeError("History error")
        )

        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id="wake_entry",
            options={CONF_CLIENT_CONTROL: True},
        )
        entry.add_to_hass(hass)
        entry.runtime_data = MagicMock(coordinator=coordinator)

        added: list = []
        await async_setup_entry(hass, entry, lambda ents, **kw: added.extend(ents))

        wake_buttons = [e for e in added if isinstance(e, UnifiClientWakeButton)]
        assert len(wake_buttons) == 1

    async def test_setup_ignores_non_dict_history(self, hass: HomeAssistant) -> None:
        """Non-dict return from history is safely ignored."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {"devices": {}, "protect": {}, "clients": {}}
        coordinator.protect_client = None
        coordinator.async_get_wake_history = AsyncMock(return_value=["not", "dict"])

        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id="wake_entry",
            options={CONF_CLIENT_CONTROL: True},
        )
        entry.add_to_hass(hass)
        entry.runtime_data = MagicMock(coordinator=coordinator)

        added: list = []
        await async_setup_entry(hass, entry, lambda ents, **kw: added.extend(ents))

        wake_buttons = [e for e in added if isinstance(e, UnifiClientWakeButton)]
        assert len(wake_buttons) == 0

    async def test_setup_with_client_control_off_removes_wake_and_reconnect_entries_only(  # noqa: E501
        self, hass: HomeAssistant
    ) -> None:
        """Disabling client control cleans up reconnect and wake buttons only."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {"devices": {}, "protect": {}, "clients": {}}
        coordinator.protect_client = None
        coordinator.async_get_wake_history = AsyncMock(return_value={})

        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id="wake_entry",
            options={CONF_CLIENT_CONTROL: False},
        )
        entry.add_to_hass(hass)
        entry.runtime_data = MagicMock(coordinator=coordinator)

        ent_reg = er.async_get(hass)
        r_wake = ent_reg.async_get_or_create(
            "button",
            DOMAIN,
            "unifi_insights_00:11:22:33:44:01_wake",
            config_entry=entry,
        )
        r_reconnect = ent_reg.async_get_or_create(
            "button", DOMAIN, "site1_c1_reconnect", config_entry=entry
        )
        r_other_btn = ent_reg.async_get_or_create(
            "button", DOMAIN, "site1_dev1_device_restart", config_entry=entry
        )
        r_other_domain = ent_reg.async_get_or_create(
            "switch", DOMAIN, "site1_c1_block_switch", config_entry=entry
        )

        added: list = []
        await async_setup_entry(hass, entry, lambda ents, **kw: added.extend(ents))

        assert ent_reg.async_get(r_wake.entity_id) is None
        assert ent_reg.async_get(r_reconnect.entity_id) is None
        assert ent_reg.async_get(r_other_btn.entity_id) is not None
        assert ent_reg.async_get(r_other_domain.entity_id) is not None

    async def test_setup_with_client_control_off_does_not_load_history(
        self, hass: HomeAssistant
    ) -> None:
        """History is not fetched when client control is disabled."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {"devices": {}, "protect": {}, "clients": {}}
        coordinator.protect_client = None
        coordinator.async_get_wake_history = AsyncMock(return_value={})

        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id="wake_entry",
            options={CONF_CLIENT_CONTROL: False},
        )
        entry.add_to_hass(hass)
        entry.runtime_data = MagicMock(coordinator=coordinator)

        added: list = []
        await async_setup_entry(hass, entry, lambda ents, **kw: added.extend(ents))

        coordinator.async_get_wake_history.assert_not_awaited()

    def test_registered_wake_macs_ignores_foreign_entries(
        self, hass: HomeAssistant
    ) -> None:
        """_registered_wake_macs filters only valid wake button entries."""
        entry = MockConfigEntry(domain=DOMAIN, entry_id="wake_entry")
        entry.add_to_hass(hass)
        ent_reg = er.async_get(hass)

        # Valid wake button
        ent_reg.async_get_or_create(
            "button",
            DOMAIN,
            "unifi_insights_00:11:22:33:44:55_wake",
            config_entry=entry,
        )
        # Foreign platform
        ent_reg.async_get_or_create(
            "button",
            "other_domain",
            "other_domain_00:11:22:33:44:66_wake",
            config_entry=entry,
        )
        # Foreign domain
        ent_reg.async_get_or_create(
            "switch",
            DOMAIN,
            "unifi_insights_00:11:22:33:44:77_wake",
            config_entry=entry,
        )
        # Reconnect button
        ent_reg.async_get_or_create(
            "button", DOMAIN, "site1_c1_reconnect", config_entry=entry
        )
        # Device tracker ID
        ent_reg.async_get_or_create(
            "device_tracker",
            DOMAIN,
            "unifi_insights_00:11:22:33:44:88",
            config_entry=entry,
        )

        macs = _registered_wake_macs(ent_reg, entry.entry_id)
        assert macs == {"00:11:22:33:44:55"}

    def test_connected_client_macs_ignores_malformed_site_and_client_data(
        self,
    ) -> None:
        """Malformed coordinator records do not hide valid connected clients."""
        coordinator = MagicMock()
        coordinator.data = {
            "clients": {
                "malformed_site": ["not-a-client-map"],
                "site1": {
                    "valid": {"macAddress": "AA:BB:CC:DD:EE:FF"},
                    "malformed_client": "not-a-client",
                },
            }
        }

        assert _connected_client_macs(coordinator) == {"aa:bb:cc:dd:ee:ff"}

    async def test_wake_button_disabled_by_default(self, hass: HomeAssistant) -> None:
        """Wake button is disabled by default for every client."""
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {"clients": {}}
        button = UnifiClientWakeButton(
            coordinator, "00:11:22:33:44:55", site_id="site1"
        )
        assert button.entity_registry_enabled_default is False

    async def test_setup_restores_disabled_wake_only_if_connected_or_in_history_seed(
        self, hass: HomeAssistant
    ) -> None:
        """Restore registered Wake button only if connected or in history seed."""
        mac_offline_unseeded = "00:11:22:33:44:01"
        mac_connected = "00:11:22:33:44:02"
        mac_offline_seeded = "00:11:22:33:44:03"

        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {
            "devices": {},
            "protect": {},
            "clients": {
                "site1": {"c2": {"type": "WIRED", "macAddress": mac_connected}}
            },
        }
        coordinator.protect_client = None
        coordinator.async_get_wake_history = AsyncMock(
            return_value={mac_offline_seeded: ("Seeded PC", "site1")}
        )

        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id="wake_entry",
            options={CONF_CLIENT_CONTROL: True},
        )
        entry.add_to_hass(hass)
        entry.runtime_data = MagicMock(coordinator=coordinator)

        ent_reg = er.async_get(hass)
        for mac in (mac_offline_unseeded, mac_connected, mac_offline_seeded):
            ent_reg.async_get_or_create(
                "button",
                DOMAIN,
                f"unifi_insights_{mac}_wake",
                config_entry=entry,
                disabled_by=er.RegistryEntryDisabler.INTEGRATION,
            )

        added: list = []
        await async_setup_entry(hass, entry, lambda ents, **kw: added.extend(ents))

        # At synchronous setup time: only mac_connected is added
        wake_ids = [e.unique_id for e in added if isinstance(e, UnifiClientWakeButton)]
        assert f"unifi_insights_{mac_connected}_wake" in wake_ids
        assert f"unifi_insights_{mac_offline_unseeded}_wake" not in wake_ids

        # Await the background seed task
        await asyncio.sleep(0)

        wake_ids_after = [
            e.unique_id for e in added if isinstance(e, UnifiClientWakeButton)
        ]
        assert f"unifi_insights_{mac_offline_seeded}_wake" in wake_ids_after
        assert f"unifi_insights_{mac_offline_unseeded}_wake" not in wake_ids_after

        # The unseeded offline row remains in entity registry untouched
        assert (
            ent_reg.async_get_entity_id(
                "button", DOMAIN, f"unifi_insights_{mac_offline_unseeded}_wake"
            )
            is not None
        )

    async def test_setup_history_seed_runs_as_background_task(
        self, hass: HomeAssistant
    ) -> None:
        """Platform setup does not await seed; entities added when task finishes."""
        mac = "00:11:22:33:44:77"
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.data = {"devices": {}, "protect": {}, "clients": {}}
        coordinator.protect_client = None

        seed_started = asyncio.Event()
        seed_proceed = asyncio.Event()

        async def slow_history(**kwargs) -> dict[str, tuple[str, str]]:
            seed_started.set()
            await seed_proceed.wait()
            return {mac: ("History PC", "site1")}

        coordinator.async_get_wake_history = slow_history

        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id="wake_entry",
            options={CONF_CLIENT_CONTROL: True},
        )
        entry.add_to_hass(hass)
        entry.runtime_data = MagicMock(coordinator=coordinator)

        added: list = []
        # Run setup with a 1-second timeout to prove setup does NOT block on the seed
        async with asyncio.timeout(1):
            await async_setup_entry(hass, entry, lambda ents, **kw: added.extend(ents))

        wake_buttons = [e for e in added if isinstance(e, UnifiClientWakeButton)]
        assert len(wake_buttons) == 0

        seed_proceed.set()
        await asyncio.sleep(0)

        wake_buttons_after = [e for e in added if isinstance(e, UnifiClientWakeButton)]
        assert len(wake_buttons_after) == 1
        assert wake_buttons_after[0].unique_id == f"unifi_insights_{mac}_wake"
