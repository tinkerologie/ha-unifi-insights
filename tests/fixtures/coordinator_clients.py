# Copyright 2026 UniFi Insights contributors
"""Network and Protect client doubles shared by the coordinator tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# --- Client doubles ----------------------------------------------------------


def _create_mock_model(data: dict) -> MagicMock:
    """Create a mock pydantic model that returns proper dict from model_dump."""
    mock = MagicMock()
    mock.model_dump = MagicMock(return_value=data)
    # Also set attributes for direct access
    for key, value in data.items():
        setattr(mock, key, value)
    return mock


def _create_mock_network_client() -> MagicMock:
    """Create a mock network client."""
    client = MagicMock()
    client.base_url = "https://192.168.1.1"

    # Sites namespace
    client.sites = MagicMock()
    client.sites.get_all = AsyncMock(
        return_value=[
            _create_mock_model({"id": "default", "name": "Default"}),
            _create_mock_model({"id": "site2", "name": "Site 2"}),
        ]
    )
    client.sites.get_legacy_all = AsyncMock(
        return_value=[
            {"name": "default", "desc": "Default"},
            {"name": "site2", "desc": "Site 2"},
        ]
    )

    # Port forwards namespace
    client.port_forwards = MagicMock()
    client.port_forwards.list_port_forwards = AsyncMock(return_value=[])
    client.port_forwards.update_port_forward = AsyncMock()

    # Traffic rules namespace
    client.traffic_rules = MagicMock()
    client.traffic_rules.list_traffic_rules = AsyncMock(return_value=[])
    client.traffic_rules.update_traffic_rule = AsyncMock()

    # NAT rules namespace
    client.nat_rules = MagicMock()
    client.nat_rules.list_nat_rules = AsyncMock(return_value=[])
    client.nat_rules.update_nat_rule = AsyncMock()

    # Static routes namespace
    client.static_routes = MagicMock()
    client.static_routes.list_static_routes = AsyncMock(return_value=[])
    client.static_routes.update_static_route = AsyncMock()

    # WiFi namespace
    client.wifi = MagicMock()
    client.wifi.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {"id": "wifi1", "name": "MainWiFi", "ssid": "MyNetwork"}
            ),
        ]
    )

    # Vouchers namespace
    client.vouchers = MagicMock()
    client.vouchers.get_all_pages = AsyncMock(return_value=[])

    # Firewall namespace
    client.firewall = MagicMock()
    client.firewall.list_rules = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "rule1",
                    "name": "Block Instagram",
                    "enabled": True,
                    "action": "drop",
                    "protocol": "all",
                }
            )
        ]
    )
    client.firewall.update_rule = AsyncMock()

    # Routes namespace
    client.routes = MagicMock()
    client.routes.list_routes = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "route1",
                    "description": "Route via VPN",
                    "enabled": True,
                    "interface": "vpn",
                }
            )
        ]
    )
    client.routes.update_route = AsyncMock()

    # Devices namespace
    client.devices = MagicMock()
    client.devices.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "device1",
                    "name": "Test Switch",
                    "model": "USW-24",
                    "mac": "AA:BB:CC:DD:EE:FF",
                }
            )
        ]
    )
    client.devices.get_statistics = AsyncMock(
        return_value=_create_mock_model(
            {
                "cpuUtilizationPct": 15.2,
                "memoryUtilizationPct": 42.8,
                "uptimeSec": 864000,
            }
        )
    )
    # Per-device details endpoint, read for access point radios only (#454).
    # The switch above never calls it; tests with an access point override
    # ``get_all`` and assert on this call.
    client.devices.get = AsyncMock(
        return_value=_create_mock_model(
            {
                "id": "device1",
                "interfaces": {
                    "radios": [
                        {
                            "frequencyGHz": 2.4,
                            "channel": 6,
                            "channelWidthMHz": 40,
                            "txPowerMode": "AUTO",
                            "wlanStandard": "802.11ax",
                        },
                        {
                            "frequencyGHz": 5,
                            "channel": 44,
                            "channelWidthMHz": 80,
                            "txPowerMode": "HIGH",
                            "wlanStandard": "802.11ac",
                        },
                    ]
                },
            }
        )
    )
    client.devices.get_legacy_site_devices = AsyncMock(return_value=[])
    client.devices.execute_port_action = AsyncMock(return_value=True)

    # Clients namespace
    client.clients = MagicMock()
    client.clients.get_all = AsyncMock(
        return_value=[
            _create_mock_model({"id": "client1", "name": "iPhone", "type": "WIRELESS"}),
        ]
    )

    client.close = AsyncMock()
    return client


def _create_mock_protect_client() -> MagicMock:
    """Create a mock protect client."""
    client = MagicMock()
    client.base_url = "https://192.168.1.1"

    # Cameras namespace
    client.cameras = MagicMock()
    client.cameras.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "camera1",
                    "name": "Front Door",
                    "state": "CONNECTED",
                    "type": "UVC-G4-DOORBELL",
                    "mac": "11:22:33:44:55:66",
                    "featureFlags": {"smartDetectTypes": ["person", "vehicle"]},
                }
            )
        ]
    )

    # Lights namespace
    client.lights = MagicMock()
    client.lights.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "light1",
                    "name": "Garage Light",
                    "state": "CONNECTED",
                }
            )
        ]
    )

    # Sensors namespace
    client.sensors = MagicMock()
    client.sensors.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "sensor1",
                    "name": "Door Sensor",
                    "state": "CONNECTED",
                }
            )
        ]
    )

    # NVR namespace
    client.nvr = MagicMock()
    client.nvr.get = AsyncMock(
        return_value=_create_mock_model({"id": "nvr1", "name": "NVR", "type": "UNVR"})
    )

    # Chimes namespace
    client.chimes = MagicMock()
    client.chimes.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "chime1",
                    "name": "Door Chime",
                    "state": "CONNECTED",
                }
            )
        ]
    )

    # Viewers namespace
    client.viewers = MagicMock()
    client.viewers.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "viewer1",
                    "name": "Viewport",
                    "state": "CONNECTED",
                }
            )
        ]
    )

    # Liveviews namespace
    client.liveviews = MagicMock()
    client.liveviews.get_all = AsyncMock(
        return_value=[
            _create_mock_model(
                {
                    "id": "liveview1",
                    "name": "Main View",
                    "isDefault": True,
                }
            )
        ]
    )

    # Protect 7.3.70 security device families
    for family in ("fobs", "link_stations", "alarm_hubs", "sirens"):
        endpoint = MagicMock()
        endpoint.get_all = AsyncMock(return_value=[])
        endpoint.last_result_complete = True
        setattr(client, family, endpoint)
    # WebSocket support
    client.get_host_id = AsyncMock(return_value="nvr1")
    client.websocket = MagicMock()
    client.websocket.subscribe_with_callback = AsyncMock()
    client.websocket.stop = MagicMock()

    client.close = AsyncMock()
    return client


# --- Rule sections -----------------------------------------------------------

RULE_SECTION_PARAMS = [
    pytest.param(
        "port_forwards",
        "port_forwards",
        "list_port_forwards",
        "port_forwards_available",
        id="port_forward",
    ),
    pytest.param(
        "traffic_rules",
        "traffic_rules",
        "list_traffic_rules",
        "traffic_rules_available",
        id="traffic_rule",
    ),
    pytest.param(
        "nat_rules",
        "nat_rules",
        "list_nat_rules",
        "nat_rules_available",
        id="nat_rule",
    ),
    pytest.param(
        "static_routes",
        "static_routes",
        "list_static_routes",
        "static_routes_available",
        id="static_route",
    ),
]
