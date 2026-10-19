# Copyright 2026 UniFi Insights contributors
"""UniFi Mobility API records taken from the published v1.0.0 spec examples."""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from unifi_official_api import UniFiNotFoundError

WORKSPACE_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
BRANCH_WORKSPACE_ID = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
PENDING_WORKSPACE_ID = "c3d4e5f6-a7b8-9012-cdef-123456789012"
OFFICE_ROUTER_ID = "550e8400-e29b-41d4-a716-446655440000"
BRANCH_ROUTER_ID = "661f9511-f3ac-52e5-b827-557766551111"

WORKSPACES: list[dict[str, Any]] = [
    {
        "workspace_id": WORKSPACE_ID,
        "workspace_name": "Headquarters",
        "is_owner": True,
        "status": "ACTIVE",
    },
    {
        "workspace_id": PENDING_WORKSPACE_ID,
        "workspace_name": "Invited Workspace",
        "is_owner": False,
        "status": "PENDING",
    },
]

DEVICE_SUMMARIES: list[dict[str, Any]] = [
    {
        "id": OFFICE_ROUTER_ID,
        "name": "Office Router",
        "model": "UMR",
        "state": "CONNECTED",
        "firmware_version": "3.1.14",
        "mac_address": "00:1A:2B:3C:4D:5E",
    },
    {
        "id": BRANCH_ROUTER_ID,
        "name": "Branch Router",
        "model": "UMR Ultra",
        "state": "DISCONNECTED",
        "firmware_version": "3.1.12",
        "mac_address": "AA:BB:CC:DD:EE:FF",
    },
]

OFFICE_ROUTER_DETAIL: dict[str, Any] = {
    **DEVICE_SUMMARIES[0],
    "wan_source": "LTE",
    "wan_ip": "203.0.113.42",
    "enabled_wans": ["LTE", "WAN"],
    "isp": "AT&T",
    "lte_signal_level": "FAIR",
    "cellular_data_usage_bytes": 524288000,
    "cellular_data_limit_bytes": 5368709120,
    "memory_usage_percent": 42,
    "uptime_seconds": 86400,
    "client_count": 5,
    "host_address": "192.168.1.1",
    "poe_passthrough": False,
    "device_mode": "ROUTER",
    "wifi_enabled": True,
    "wifi_ssid": "UniFi-LTE",
    "tx_power_level": "HIGH",
    "vpn_profile_name": "HQ-VPN",
    "vpn_status": "CONNECTED",
    "firewall_rule_names": ["Block-IoT-Outbound"],
    "routing_rule_names": [],
    "ddns_profile_names": [],
    "subscription_plan": "5GB",
    "subscription_status": "ACTIVE",
    "location": {
        "latitude": 37.7749,
        "longitude": -122.4194,
        "last_updated": 1709712000000,
    },
}

# A disconnected router with no GPS fix: `location` is omitted, and the
# fields that need a live connection are empty or zero.
BRANCH_ROUTER_DETAIL: dict[str, Any] = {
    **DEVICE_SUMMARIES[1],
    "wan_source": "",
    "wan_ip": "",
    "enabled_wans": [],
    "isp": "",
    "lte_signal_level": "",
    "cellular_data_usage_bytes": 0,
    "cellular_data_limit_bytes": -1,
    "memory_usage_percent": 0,
    "uptime_seconds": 0,
    "client_count": 0,
    "host_address": "",
    "poe_passthrough": False,
    "device_mode": "ROUTER",
    "wifi_enabled": False,
    "wifi_ssid": "Branch-Private",
    "tx_power_level": "",
    "vpn_profile_name": "",
    "vpn_status": "",
    "firewall_rule_names": [],
    "routing_rule_names": [],
    "ddns_profile_names": [],
    "subscription_plan": "",
    "subscription_status": "INACTIVE",
}

DEVICE_DETAILS: dict[str, dict[str, Any]] = {
    OFFICE_ROUTER_ID: OFFICE_ROUTER_DETAIL,
    BRANCH_ROUTER_ID: BRANCH_ROUTER_DETAIL,
}


WORKSPACE_ADMINS: list[dict[str, Any]] = [
    {
        "name": "Alice Owner",
        "email": "alice@example.com",
        "status": "ACTIVE",
        "is_owner": True,
        "permissions": {
            "umr": "ALL",
        },
    },
    {
        "name": "Bob Viewer",
        "email": "bob@example.com",
        "status": "ACTIVE",
        "is_owner": False,
        "permissions": {
            "umr": "VIEW_ONLY",
        },
    },
    {
        "name": "Charlie Pending",
        "email": "charlie@example.com",
        "status": "PENDING",
        "is_owner": False,
        "permissions": None,
    },
]

DEVICE_CLIENTS: list[dict[str, Any]] = [
    {
        "mac": "AA:BB:CC:DD:EE:FF",
        "name": "John's iPhone",
        "type": "WIRELESS",
        "connection_status": "ONLINE",
        "ip_address": "192.168.1.100",
        "is_blocked": False,
        "wifi_experience": 85,
    },
    {
        "mac": "11:22:33:44:55:66",
        "name": "NAS",
        "type": "WIRED",
        "connection_status": "ONLINE",
        "ip_address": "192.168.1.50",
        "is_blocked": False,
    },
]


def mobility_client_mock() -> MagicMock:
    """Return a Mobility client mock that serves the spec examples."""
    client = MagicMock()
    client.list_workspaces = AsyncMock(return_value=copy.deepcopy(WORKSPACES))

    async def list_devices(workspace_id: str) -> list[dict[str, Any]]:
        if workspace_id != WORKSPACE_ID:
            return []
        return copy.deepcopy(DEVICE_SUMMARIES)

    async def get_device(workspace_id: str, device_id: str) -> dict[str, Any]:
        if workspace_id != WORKSPACE_ID or device_id not in DEVICE_DETAILS:
            msg = "device not found"
            raise UniFiNotFoundError(msg, status_code=404)
        return copy.deepcopy(DEVICE_DETAILS[device_id])

    client.list_devices = AsyncMock(side_effect=list_devices)
    client.get_device = AsyncMock(side_effect=get_device)

    async def list_workspace_admins(workspace_id: str) -> list[dict[str, Any]]:
        if workspace_id != WORKSPACE_ID:
            return []
        return copy.deepcopy(WORKSPACE_ADMINS)

    async def list_device_clients(
        workspace_id: str, device_id: str
    ) -> list[dict[str, Any]]:
        if workspace_id != WORKSPACE_ID or device_id not in DEVICE_DETAILS:
            return []
        return copy.deepcopy(DEVICE_CLIENTS)

    client.list_workspace_admins = AsyncMock(side_effect=list_workspace_admins)
    client.list_device_clients = AsyncMock(side_effect=list_device_clients)
    client.update_device_name = AsyncMock(return_value=None)
    client.update_device_network = AsyncMock(return_value=None)
    client.update_device_wireless = AsyncMock(return_value=None)
    client.close = AsyncMock()
    return client
