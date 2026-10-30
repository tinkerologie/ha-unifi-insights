# Copyright 2026 UniFi Insights contributors
"""Tests for dynamic entity discovery across UniFi Insights platforms."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.unifi_insights.binary_sensor import (
    async_setup_entry as async_setup_binary_sensor,
)
from custom_components.unifi_insights.button import (
    async_setup_entry as async_setup_button,
)
from custom_components.unifi_insights.camera import (
    async_setup_entry as async_setup_camera,
)
from custom_components.unifi_insights.const import (
    CONF_CLIENT_CONTROL,
)
from custom_components.unifi_insights.event import (
    async_setup_entry as async_setup_event,
)
from custom_components.unifi_insights.image import (
    async_setup_entry as async_setup_image,
)
from custom_components.unifi_insights.light import (
    async_setup_entry as async_setup_light,
)
from custom_components.unifi_insights.media_player import (
    async_setup_entry as async_setup_media_player,
)
from custom_components.unifi_insights.number import (
    async_setup_entry as async_setup_number,
)
from custom_components.unifi_insights.select import (
    async_setup_entry as async_setup_select,
)
from custom_components.unifi_insights.sensor import (
    RADIO_DETAILS_SENSOR_TYPES,
)
from custom_components.unifi_insights.sensor import (
    async_setup_entry as async_setup_sensor,
)
from custom_components.unifi_insights.switch import (
    async_setup_entry as async_setup_switch,
)
from custom_components.unifi_insights.update import (
    async_setup_entry as async_setup_update,
)
from tests.fixtures.facade_data import (
    frozen,
    with_camera,
    with_client,
    with_network_device,
    with_protect_collection,
    with_protect_device,
    with_sections,
)


@pytest.fixture
def mock_coordinator() -> MagicMock:
    """Create a mock coordinator with empty data."""
    coordinator = MagicMock()
    coordinator.protect_client = MagicMock()
    coordinator.network_client = MagicMock()
    coordinator.network_client.base_url = "https://192.168.1.1"
    coordinator.data = frozen(
        {
            "devices": {
                "site1": {},
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
            "wifi": {
                "site1": {},
            },
            "clients": {
                "site1": {},
            },
            "firewall_rules": {
                "site1": {},
            },
            "policy_based_routes": {
                "site1": {},
            },
            "vpn_clients": {
                "site1": {},
            },
        }
    )
    return coordinator


@pytest.fixture
def mock_config_entry(mock_coordinator: MagicMock) -> MagicMock:
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {"console_id": "console"}
    entry.options = {CONF_CLIENT_CONTROL: True}
    entry.async_on_unload = MagicMock()
    entry.async_create_background_task.side_effect = lambda hass, target, name: (
        hass.async_create_background_task(target, name)
    )
    mock_coordinator.async_get_wake_history = AsyncMock(return_value={})
    entry.runtime_data = MagicMock()
    entry.runtime_data.mobility_coordinator = None
    entry.runtime_data.coordinator = mock_coordinator
    entry.runtime_data.device_coordinator = MagicMock()
    return entry


class TestDynamicNetworkDeviceDiscovery:
    """Test dynamic discovery of network devices across platforms without reload."""

    @pytest.mark.asyncio
    async def test_dynamic_network_device_creation(
        self, hass: Any, mock_coordinator: MagicMock, mock_config_entry: MagicMock
    ) -> None:
        """Adopting a new network device creates entities on coordinator update."""
        platforms = [
            ("update", async_setup_update),
            ("binary_sensor", async_setup_binary_sensor),
            ("sensor", async_setup_sensor),
            ("button", async_setup_button),
            ("switch", async_setup_switch),
        ]

        callbacks = {}
        add_mocks = {}

        for name, setup_fn in platforms:
            mock_coordinator.async_add_listener.reset_mock()
            add_entities = MagicMock()
            add_mocks[name] = add_entities
            await setup_fn(hass, mock_config_entry, add_entities)
            assert mock_coordinator.async_add_listener.call_count == 1
            callbacks[name] = mock_coordinator.async_add_listener.call_args[0][0]

        # Initially, platforms were either called with 0 entities or not called
        initial_call_counts = {k: m.call_count for k, m in add_mocks.items()}

        # Now adopt a new network switch with ports and outlets
        mock_coordinator.data = with_network_device(
            mock_coordinator.data,
            "site1",
            "device_switch_1",
            {
                "id": "device_switch_1",
                "name": "Core Switch 24",
                "model": "USW-24-POE",
                "state": "ONLINE",
                "firmwareVersion": "6.5.55",
                "upgradeAvailable": False,
                "system-stats": {"cpu": 15.2, "mem": 42.1, "uptime": 12345},
                "interfaces": {
                    "ports": [
                        {
                            "idx": 1,
                            "name": "Port 1",
                            "state": "up",
                            "poe": {"power": 5.4, "good": True},
                            "speed": 1000,
                            "media": "copper",
                        },
                        {
                            "idx": 25,
                            "name": "SFP 1",
                            "state": "up",
                            "media": "sfp",
                            "sfp": {"present": True, "vendor": "Ubiquiti"},
                        },
                    ]
                },
                "outlet_table": [
                    {
                        "index": 1,
                        "name": "Outlet 1",
                        "relay_state": True,
                        "power": 12.0,
                        "voltage": 120.0,
                        "current": 0.1,
                        "power_factor": 0.95,
                    }
                ],
                "features": {"switching": True},
            },
        )

        # Trigger coordinator listeners for each platform without reload
        for callback in callbacks.values():
            callback()

        # Verify new entities were discovered and added
        for name, add_mock in add_mocks.items():
            assert add_mock.call_count > initial_call_counts[name], (
                f"Platform {name} did not discover new entities on update"
            )
            latest_entities = add_mock.call_args[0][0]
            assert len(latest_entities) > 0, (
                f"Platform {name} received empty list on update"
            )

        # Triggering update again with no new devices must not add duplicates
        post_adoption_counts = {k: m.call_count for k, m in add_mocks.items()}
        for callback in callbacks.values():
            callback()

        for name, add_mock in add_mocks.items():
            assert add_mock.call_count == post_adoption_counts[name], (
                f"Platform {name} added duplicate entities"
            )


class TestDynamicProtectDeviceDiscovery:
    """Test dynamic discovery of Protect devices across platforms without reload."""

    @pytest.mark.asyncio
    async def test_dynamic_protect_device_creation(
        self, hass: Any, mock_coordinator: MagicMock, mock_config_entry: MagicMock
    ) -> None:
        """Adopting a new Protect camera and sensor creates entities without reload."""
        platforms = [
            ("camera", async_setup_camera),
            ("light", async_setup_light),
            ("binary_sensor", async_setup_binary_sensor),
            ("sensor", async_setup_sensor),
            ("number", async_setup_number),
            ("select", async_setup_select),
            ("event", async_setup_event),
            ("button", async_setup_button),
            ("switch", async_setup_switch),
            ("media_player", async_setup_media_player),
        ]

        callbacks = {}
        add_mocks = {}

        for name, setup_fn in platforms:
            mock_coordinator.async_add_listener.reset_mock()
            add_entities = MagicMock()
            add_mocks[name] = add_entities
            await setup_fn(hass, mock_config_entry, add_entities)
            assert mock_coordinator.async_add_listener.call_count == 1
            callbacks[name] = mock_coordinator.async_add_listener.call_args[0][0]

        initial_call_counts = {k: m.call_count for k, m in add_mocks.items()}

        # Dynamically discover a camera, light, sensor, and chime in Protect
        mock_coordinator.data = with_camera(
            mock_coordinator.data,
            "cam_1",
            {
                "id": "cam_1",
                "name": "Front Door Camera",
                "state": "CONNECTED",
                "type": "camera",
                "modelKey": "camera",
                "featureFlags": {
                    "hasMic": True,
                    "hasSpeaker": True,
                    "hasHdr": True,
                    "hasPtz": True,
                    "isDoorbell": True,
                    "smartDetectTypes": ["person", "vehicle"],
                },
                "micVolume": 80,
                "hdrMode": "auto",
                "videoMode": "default",
                "ptz": {"presets": [{"id": 1, "name": "Entrance"}]},
                "isMicEnabled": True,
                "isLedEnabled": True,
            },
        )
        # Speaker-less camera to prove negative capability gating
        mock_coordinator.data = with_camera(
            mock_coordinator.data,
            "cam_2",
            {
                "id": "cam_2",
                "name": "Backyard Camera",
                "state": "CONNECTED",
                "type": "camera",
                "modelKey": "camera",
                "hasSpeaker": False,
                "featureFlags": {
                    "hasSpeaker": False,
                    "hasRemovableSpeaker": False,
                },
            },
        )
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "lights",
            "light_1",
            {
                "id": "light_1",
                "name": "Porch Floodlight",
                "state": "CONNECTED",
                "isLedEnabled": True,
                "lightDeviceSettings": {"ledLevel": 6},
            },
        )
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "sensors",
            "sensor_1",
            {
                "id": "sensor_1",
                "name": "Front Door Sensor",
                "state": "CONNECTED",
                "type": "sensor",
                "modelKey": "sensor",
                "stats": {
                    "temperature": {"value": 21.5},
                    "humidity": {"value": 45.0},
                    "light": {"value": 150.0},
                },
                "batteryStatus": {"percentage": 92},
                "isOpened": False,
                "isTamperingDetected": False,
            },
        )
        mock_coordinator.data = with_protect_device(
            mock_coordinator.data,
            "chimes",
            "chime_1",
            {
                "id": "chime_1",
                "name": "Hallway Chime",
                "state": "CONNECTED",
                "ringSettings": [
                    {
                        "volume": 70,
                        "repeatTimes": 2,
                        "ringtone": "dingdong",
                    }
                ],
            },
        )

        # Trigger listener updates across all platforms
        for callback in callbacks.values():
            callback()

        # All Protect platforms should have added their respective entities
        for name, add_mock in add_mocks.items():
            assert add_mock.call_count > initial_call_counts[name], (
                f"Platform {name} did not discover new Protect entities"
            )
            entities = add_mock.call_args[0][0]
            assert len(entities) > 0, (
                f"Platform {name} added empty entity list on Protect update"
            )

        # Specifically assert media_player discovered speaker camera cam_1 and not cam_2
        media_player_entities = add_mocks["media_player"].call_args[0][0]
        discovered_camera_ids = {e._device_id for e in media_player_entities}
        assert "cam_1" in discovered_camera_ids
        assert "cam_2" not in discovered_camera_ids

        # Subsequent updates must not add duplicate entities
        post_counts = {k: m.call_count for k, m in add_mocks.items()}
        for callback in callbacks.values():
            callback()

        for name, add_mock in add_mocks.items():
            assert add_mock.call_count == post_counts[name], (
                f"Platform {name} added duplicate Protect entities"
            )


class TestDynamicOptionsAndCapabilities:
    """Test dynamic additions respecting user configuration and options."""

    @pytest.mark.asyncio
    async def test_client_control_option_toggle(
        self, hass: Any, mock_coordinator: MagicMock, mock_config_entry: MagicMock
    ) -> None:
        """Buttons and switches for client control dynamically respect options."""
        mock_config_entry.options = {CONF_CLIENT_CONTROL: False}

        mock_coordinator.data = with_client(
            mock_coordinator.data,
            "site1",
            "client_mac_1",
            {
                "id": "client_mac_1",
                "mac": "aa:bb:cc:dd:ee:ff",
                "name": "Guest Phone",
                "blocked": False,
            },
        )

        button_add = MagicMock()
        await async_setup_button(hass, mock_config_entry, button_add)
        button_listener = mock_coordinator.async_add_listener.call_args[0][0]

        switch_add = MagicMock()
        await async_setup_switch(hass, mock_config_entry, switch_add)
        switch_listener = mock_coordinator.async_add_listener.call_args[0][0]

        # With client_control=False, no client buttons or switches created
        assert button_add.call_count == 0 or len(button_add.call_args[0][0]) == 0
        assert switch_add.call_count == 1
        assert len(switch_add.call_args[0][0]) == 0

        # Now enable client_control in options and trigger coordinator update
        mock_config_entry.options = {CONF_CLIENT_CONTROL: True}
        button_listener()
        switch_listener()

        assert button_add.call_count == 1
        assert len(button_add.call_args[0][0]) == 1
        assert button_add.call_args[0][0][0].unique_id == "site1_client_mac_1_reconnect"

        assert switch_add.call_count == 2
        assert len(switch_add.call_args[0][0]) == 1
        assert (
            switch_add.call_args[0][0][0].unique_id == "site1_client_mac_1_block_switch"
        )

    @pytest.mark.asyncio
    async def test_client_control_toggle_adds_wake_button_for_wired_client(
        self, hass: Any, mock_coordinator: MagicMock, mock_config_entry: MagicMock
    ) -> None:
        """Enabling client control discovers Wake button for wired clients."""
        mock_coordinator.async_get_wake_history = AsyncMock(return_value={})
        mock_config_entry.options = {CONF_CLIENT_CONTROL: False}

        mock_coordinator.data = with_client(
            mock_coordinator.data,
            "site1",
            "wired_pc",
            {
                "id": "wired_pc",
                "type": "WIRED",
                "macAddress": "00:11:22:33:44:55",
                "name": "Workstation",
            },
        )

        button_add = MagicMock()
        await async_setup_button(hass, mock_config_entry, button_add)
        button_listener = mock_coordinator.async_add_listener.call_args[0][0]

        # While client_control is False, no client buttons added
        assert button_add.call_count == 0 or len(button_add.call_args[0][0]) == 0

        # Enable client_control and trigger listener
        mock_config_entry.options = {CONF_CLIENT_CONTROL: True}
        button_listener()

        assert button_add.call_count == 1
        added = button_add.call_args[0][0]
        wake_buttons = [
            e
            for e in added
            if getattr(e, "unique_id", None) == "unifi_insights_00:11:22:33:44:55_wake"
        ]
        reconnect_buttons = [
            e
            for e in added
            if getattr(e, "unique_id", None) == "site1_wired_pc_reconnect"
        ]
        assert len(wake_buttons) == 1
        assert len(reconnect_buttons) == 1

        # Second listener run adds nothing
        button_listener()
        assert button_add.call_count == 1

    @pytest.mark.asyncio
    async def test_dynamic_wifi_qr_and_sensor(
        self, hass: Any, mock_coordinator: MagicMock, mock_config_entry: MagicMock
    ) -> None:
        """Adding a new WiFi network dynamically adds QR code image and sensor."""
        image_add = MagicMock()
        await async_setup_image(hass, mock_config_entry, image_add)
        image_listener = mock_coordinator.async_add_listener.call_args[0][0]

        sensor_add = MagicMock()
        await async_setup_sensor(hass, mock_config_entry, sensor_add)
        sensor_listener = mock_coordinator.async_add_listener.call_args[0][0]

        # Now add a new WiFi network
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            wifi={
                "site1": {
                    "wifi_guest": {
                        "id": "wifi_guest",
                        "name": "Guest WiFi",
                        "qr_code": "WIFI:S:Guest WiFi;T:WPA;P:secret;;",
                        "num_sta": 5,
                    }
                }
            },
        )

        image_listener()
        sensor_listener()

        assert image_add.call_count == 1
        image_entities = image_add.call_args[0][0]
        assert len(image_entities) == 1
        assert image_entities[0].unique_id == "site1_wifi_guest_qr_code"

        sensor_entities = sensor_add.call_args[0][0]
        wifi_sensors = [
            e
            for e in sensor_entities
            if getattr(e, "unique_id", "").startswith("site1_wifi_guest_client_count")
        ]
        assert len(wifi_sensors) == 1


class TestDynamicDiscoveryResilience:
    """Test resilience against empty payloads, missing keys, and disconnections."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("name", "setup_fn"),
        [
            ("update", async_setup_update),
            ("binary_sensor", async_setup_binary_sensor),
            ("sensor", async_setup_sensor),
            ("button", async_setup_button),
            ("switch", async_setup_switch),
            ("camera", async_setup_camera),
            ("light", async_setup_light),
            ("number", async_setup_number),
            ("select", async_setup_select),
            ("event", async_setup_event),
            ("image", async_setup_image),
        ],
    )
    async def test_listener_resilience_empty_or_none_data(
        self,
        name: str,
        setup_fn: Any,
        hass: Any,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Listeners handle None, empty, or malformed data gracefully."""
        add_entities = MagicMock()
        await setup_fn(hass, mock_config_entry, add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        # Test various disconnected or malformed states
        test_payloads = [
            None,
            {},
            {"devices": None, "protect": None},
            {"devices": {"site1": None}, "protect": {"cameras": None}},
            {"devices": {"site1": {"bad_dev": None}}},
            {"protect": {"cameras": {"bad_cam": None}, "lights": {"bad_light": None}}},
        ]

        for payload in test_payloads:
            mock_coordinator.data = payload
            # Must execute without throwing exceptions
            listener()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("name", "setup_fn"),
        [
            ("update", async_setup_update),
            ("binary_sensor", async_setup_binary_sensor),
            ("sensor", async_setup_sensor),
            ("button", async_setup_button),
            ("switch", async_setup_switch),
            ("camera", async_setup_camera),
            ("light", async_setup_light),
            ("number", async_setup_number),
            ("select", async_setup_select),
            ("event", async_setup_event),
            ("image", async_setup_image),
        ],
    )
    async def test_listeners_unregistered_on_unload(
        self,
        name: str,
        setup_fn: Any,
        hass: Any,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Every platform registers its listener with entry.async_on_unload."""
        add_entities = MagicMock()
        mock_config_entry.async_on_unload.reset_mock()
        mock_coordinator.async_add_listener.reset_mock()

        await setup_fn(hass, mock_config_entry, add_entities)

        # The event platform also unloads its Protect digital-input dispatcher
        # listener, on top of the coordinator listener every platform keeps.
        expected_unloads = 2 if name == "event" else 1
        assert mock_coordinator.async_add_listener.call_count == 1
        assert mock_config_entry.async_on_unload.call_count == expected_unloads

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("name", "setup_fn"),
        [
            ("update", async_setup_update),
            ("binary_sensor", async_setup_binary_sensor),
            ("sensor", async_setup_sensor),
            ("button", async_setup_button),
            ("switch", async_setup_switch),
            ("camera", async_setup_camera),
            ("light", async_setup_light),
            ("number", async_setup_number),
            ("select", async_setup_select),
            ("event", async_setup_event),
            ("image", async_setup_image),
        ],
    )
    async def test_listener_malformed_records_and_collections(
        self,
        name: str,
        setup_fn: Any,
        hass: Any,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Listeners handle malformed collections and records in coordinator data."""
        add_entities = MagicMock()
        await setup_fn(hass, mock_config_entry, add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        malformed_payloads = [
            # Collections are not dicts
            {
                "devices": {"site1": "not-a-dict"},
                "clients": {"site1": "not-a-dict"},
                "wifi": {"site1": "not-a-dict"},
                "firewall_rules": {"site1": "not-a-dict"},
                "policy_based_routes": {"site1": "not-a-dict"},
                "vpn_clients": {"site1": "not-a-dict"},
                "protect": {
                    "cameras": "not-a-dict",
                    "lights": "not-a-dict",
                    "sensors": "not-a-dict",
                    "chimes": "not-a-dict",
                    "viewers": "not-a-dict",
                },
                "stats": {"site1": "not-a-dict"},
            },
            # Records within collections are not dicts or missing fields
            {
                "devices": {
                    "site1": {
                        "dev_str": "not-a-dict",
                        "dev_bad_outlets": {
                            "id": "dev_bad_outlets",
                            "name": "PDU",
                            "features": ["switching"],
                            "outlet_table": "not-a-list",
                            "interfaces": {"ports": "not-a-list"},
                        },
                        "dev_bad_outlets_list": {
                            "id": "dev_bad_outlets_list",
                            "name": "PDU2",
                            "features": ["switching"],
                            "outlet_table": [
                                "not-a-dict",
                                {},
                                {"index": "not-an-int"},
                            ],
                            "interfaces": {
                                "ports": ["not-a-dict", {}],
                            },
                        },
                    }
                },
                "clients": {"site1": {"c_str": "not-a-dict"}},
                "wifi": {
                    "site1": {
                        "w_str": "not-a-dict",
                        "w_no_qr": {"name": "WiFi", "qr_code": None},
                        "w_valid": {"name": "Guest", "qr_code": "WIFI:S:Guest;;"},
                    }
                },
                "firewall_rules": {"site1": {"r_str": "not-a-dict"}},
                "policy_based_routes": {"site1": {"pr_str": "not-a-dict"}},
                "vpn_clients": {"site1": {"vpn_str": "not-a-dict"}},
                "protect": {
                    "cameras": {"cam_str": "not-a-dict"},
                    "lights": {"light_str": "not-a-dict"},
                    "sensors": {"sensor_str": "not-a-dict"},
                    "chimes": {"chime_str": "not-a-dict"},
                    "viewers": {"viewer_str": "not-a-dict"},
                },
                "stats": {
                    "site1": {
                        "dev_bad_outlets": "not-a-dict",
                    }
                },
            },
        ]

        for payload in malformed_payloads:
            mock_coordinator.data = payload
            listener()

        # Run again with same payload to trigger duplicate skips
        listener()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("name", "setup_fn"),
        [
            ("camera", async_setup_camera),
            ("light", async_setup_light),
            ("media_player", async_setup_media_player),
            ("number", async_setup_number),
        ],
    )
    async def test_protect_platforms_skip_when_no_protect_client(
        self,
        name: str,
        setup_fn: Any,
        hass: Any,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Protect platforms return early when protect_client is None."""
        mock_coordinator.protect_client = None
        add_entities = MagicMock()
        await setup_fn(hass, mock_config_entry, add_entities)
        add_entities.assert_not_called()


class TestDiscoveryRegressions:
    """Regressions for payload shapes discovery used to mishandle."""

    @pytest.mark.asyncio
    async def test_light_retried_after_a_failed_construction(
        self,
        hass: Any,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """A light that fails to construct is retried on the next update."""
        add_entities = MagicMock()
        await async_setup_light(hass, mock_config_entry, add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        mock_coordinator.data = with_protect_collection(
            mock_coordinator.data,
            "lights",
            {"light1": {"id": "light1", "name": "Porch"}},
        )

        with patch(
            "custom_components.unifi_insights.light.UnifiProtectLight",
            side_effect=ValueError("bad ledLevel"),
        ):
            listener()
        add_entities.assert_not_called()

        # Payload is fine now; the light must not stay hidden.
        listener()
        add_entities.assert_called_once()
        assert len(add_entities.call_args[0][0]) == 1

    @pytest.mark.asyncio
    async def test_wan_status_skipped_when_model_is_none(
        self,
        hass: Any,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """A device reporting model=None does not crash WAN status discovery."""
        add_entities = MagicMock()
        await async_setup_binary_sensor(hass, mock_config_entry, add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            devices={
                "site1": {"dev1": {"id": "dev1", "name": "Mesh AP", "model": None}}
            },
        )

        listener()

        added = [e for call in add_entities.call_args_list for e in call[0][0]]
        assert not any(
            getattr(e, "entity_description", None) is not None
            and e.entity_description.key == "wan_status"
            for e in added
        )

    @pytest.mark.asyncio
    async def test_mapping_shaped_features_still_create_sensors(
        self,
        hass: Any,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Devices reporting features as a mapping get feature-gated sensors."""
        add_entities = MagicMock()
        await async_setup_sensor(hass, mock_config_entry, add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            devices={
                "site1": {
                    "dev1": {
                        "id": "dev1",
                        "name": "Switch",
                        "model": "USW-24",
                        "features": {"switching": True},
                    }
                }
            },
            stats={"site1": {"dev1": {"poe_total_w": 12.0}}},
        )

        listener()

        added = [e for call in add_entities.call_args_list for e in call[0][0]]
        keys = {
            e.entity_description.key
            for e in added
            if getattr(e, "entity_description", None) is not None
        }
        assert "poe_total_power" in keys

    @pytest.mark.asyncio
    async def test_string_port_index_does_not_duplicate_a_port_sensor(
        self,
        hass: Any,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """A string port index dedupes against the int-keyed stats fallback."""
        add_entities = MagicMock()
        await async_setup_sensor(hass, mock_config_entry, add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            devices={
                "site1": {
                    "dev1": {
                        "id": "dev1",
                        "name": "Switch",
                        "model": "USW-24",
                        "features": {"switching": True},
                        "interfaces": {
                            "ports": [
                                {"idx": "1", "state": "UP", "poe": {"good": True}},
                            ]
                        },
                    }
                }
            },
            stats={"site1": {"dev1": {"poe_ports": {"1": 5.0}}}},
        )

        listener()

        added = [e for call in add_entities.call_args_list for e in call[0][0]]
        unique_ids = [e.unique_id for e in added if getattr(e, "unique_id", None)]
        assert len(unique_ids) == len(set(unique_ids))

    @pytest.mark.asyncio
    async def test_internet_activity_dynamic_discovery_and_site_unique_id_stability(
        self,
        hass: Any,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Report data discovers site activity sensors without changing site IDs."""
        add_entities = MagicMock()
        mock_coordinator.data = with_sections(
            mock_coordinator.data, internet_activity={}
        )
        await async_setup_sensor(hass, mock_config_entry, add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        initial_entities = [
            e for call in add_entities.call_args_list for e in call[0][0]
        ]
        initial_site_ids = {
            e.unique_id
            for e in initial_entities
            if getattr(e, "unique_id", "").startswith("console_site1_site_")
        }
        assert initial_site_ids == {
            "console_site1_site_total_clients",
            "console_site1_site_wired_clients",
            "console_site1_site_wireless_clients",
        }
        assert not [
            e
            for e in initial_entities
            if "internet_download_" in getattr(e, "unique_id", "")
            or "internet_upload_" in getattr(e, "unique_id", "")
        ]

        # Report data appears on a later refresh -> creates 8 sensors once
        add_entities.reset_mock()
        mock_coordinator.data = with_sections(
            mock_coordinator.data,
            internet_activity={
                "site1": {
                    "1h": {"rx_bytes": 100, "tx_bytes": 200},
                    "1d": {"rx_bytes": 300, "tx_bytes": 400},
                    "1w": {"rx_bytes": 500, "tx_bytes": 600},
                    "1m": {"rx_bytes": 700, "tx_bytes": 800},
                }
            },
        )
        listener()
        assert add_entities.call_count == 1
        discovered = add_entities.call_args[0][0]
        discovered_ids = {e.unique_id for e in discovered}
        assert discovered_ids == {
            "console_site1_internet_download_1h",
            "console_site1_internet_upload_1h",
            "console_site1_internet_download_1d",
            "console_site1_internet_upload_1d",
            "console_site1_internet_download_1w",
            "console_site1_internet_upload_1w",
            "console_site1_internet_download_1m",
            "console_site1_internet_upload_1m",
        }

        # Subsequent listener call does not duplicate sensors
        add_entities.reset_mock()
        listener()
        assert add_entities.call_count == 0


RADIO_AP: dict[str, Any] = {
    "id": "ap1",
    "name": "Office AP",
    "model": "U7-Pro",
    "state": "ONLINE",
    "features": ["accessPoint"],
}
RADIO_2_4: dict[str, Any] = {
    "frequencyGHz": 2.4,
    "channel": 6,
    "channelWidthMHz": 40,
    "txPowerMode": "AUTO",
    "wlanStandard": "802.11ax",
    "txRetriesPct": 1.5,
}
RADIO_5: dict[str, Any] = {
    "frequencyGHz": 5,
    "channel": 44,
    "channelWidthMHz": 80,
    "txPowerMode": "HIGH",
    "wlanStandard": "802.11ac",
    "txRetriesPct": 0,
}
RADIO_6: dict[str, Any] = {
    "frequencyGHz": 6,
    "channel": 37,
    "channelWidthMHz": 160,
    "txPowerMode": "AUTO",
    "wlanStandard": "802.11be",
    "txRetriesPct": 2,
}


class TestRadioSensorDiscovery:
    """Per-radio sensor discovery across coordinator updates (#454)."""

    @staticmethod
    def _radio_entities(add_mock: MagicMock) -> list[Any]:
        """Return every radio sensor handed to ``add_mock``, deduped by ID."""
        by_id: dict[str, Any] = {}
        for call in add_mock.call_args_list:
            for entity in call[0][0]:
                if "_radio_" in str(getattr(entity, "unique_id", "")):
                    by_id[entity.unique_id] = entity
        return list(by_id.values())

    def _publish(self, coordinator: MagicMock, *radios: dict[str, Any]) -> None:
        """Publish an AP whose stats and details carry ``radios``."""
        coordinator.data = with_sections(
            coordinator.data,
            devices={"site1": {"ap1": dict(RADIO_AP)}},
            stats={"site1": {"ap1": {"interfaces": {"radios": list(radios)}}}},
            radio_details={
                "site1": {
                    "ap1": {"radios": list(radios), "available": True},
                }
            },
        )

    @pytest.mark.asyncio
    async def test_radio_bands_are_discovered_without_reload(
        self,
        hass: Any,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Two radios create two retry sensors; a later 6 GHz adds one more."""
        add_entities = MagicMock()
        await async_setup_sensor(hass, mock_config_entry, add_entities)
        listener = mock_coordinator.async_add_listener.call_args[0][0]

        self._publish(mock_coordinator, RADIO_2_4, RADIO_5)
        listener()

        added = self._radio_entities(add_entities)
        by_key = {
            (
                entity.entity_description.key,
                entity.unique_id.split("_radio_", 1)[1],
            )
            for entity in added
        }
        assert by_key == {
            ("radio_tx_retries", f"{band}_radio_tx_retries")
            for band in ("2_4ghz", "5ghz")
        } | {
            (description.key, f"{band}_{description.key}")
            for band in ("2_4ghz", "5ghz")
            for description in RADIO_DETAILS_SENSOR_TYPES
        }

        # An unchanged notification adds no duplicates.
        add_entities.reset_mock()
        listener()
        assert self._radio_entities(add_entities) == []

        # A band that appears later is discovered on the next update.
        self._publish(mock_coordinator, RADIO_2_4, RADIO_5, RADIO_6)
        listener()

        late = self._radio_entities(add_entities)
        assert {entity.entity_description.key for entity in late} == {
            "radio_tx_retries",
            *{description.key for description in RADIO_DETAILS_SENSOR_TYPES},
        }
        assert all("6ghz" in str(entity.unique_id) for entity in late)
