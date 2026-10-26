# Copyright (c) 2026 Ruaan Deysel
"""Tests for client Wake-on-LAN pure helpers, the wake command and wake history."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import async_mock_service
from unifi_official_api import UniFiResponseError

from custom_components.unifi_insights.client_wake import (
    derive_directed_broadcast,
    find_client_by_mac,
    history_network_hint,
    mac_from_wake_unique_id,
    select_wake_history,
    wake_unique_id,
)
from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.coordinators.config import (
    UnifiInsightsConfigCoordinator,
)
from custom_components.unifi_insights.coordinators.device import (
    UnifiInsightsDeviceCoordinator,
)
from custom_components.unifi_insights.coordinators.facade import (
    UnifiInsightsFacadeCoordinator,
)
from tests.fixtures.assertions import patch_attribute, require_mock, require_not_none

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


# =============================================================================
# Section A: Pure helpers
# =============================================================================


def test_wake_unique_id_round_trips_through_mac_from_wake_unique_id() -> None:
    """wake_unique_id generates IDs that mac_from_wake_unique_id parses back."""
    mac = "00:11:22:33:44:55"
    uid = wake_unique_id(mac)
    assert uid == f"{DOMAIN}_{mac}_wake"
    assert mac_from_wake_unique_id(uid) == mac


@pytest.mark.parametrize(
    "invalid_id",
    [
        "site_client_reconnect",
        "unifi_insights_aa:bb:cc:dd:ee:ff",
        "unifi_insights_wake",
        "unifi_insights_not-a-mac_wake",
        "other_aa:bb:cc:dd:ee:ff_wake",
        "unifi_insights_AA:BB:CC:DD:EE:FF_wake",
    ],
)
def test_mac_from_wake_unique_id_rejects_other_ids(invalid_id: str) -> None:
    """mac_from_wake_unique_id returns None for non-wake or non-normalized IDs."""
    assert mac_from_wake_unique_id(invalid_id) is None


def test_find_client_by_mac_matches_any_spelling_and_field_name() -> None:
    """find_client_by_mac finds client across field spellings and multiple sites."""
    clients_by_site = {
        "site1": {
            "c1": {"macAddress": "AA:BB:CC:DD:EE:01", "name": "Device 1"},
            "c2": {"mac_address": "00:11:22:33:44:55", "name": "Device 2"},
        },
        "site2": {
            "c3": {"mac": "66:77:88:99:aa:bb", "name": "Device 3"},
        },
    }
    # Matches uppercase macAddress
    res1 = find_client_by_mac(clients_by_site, "aa:bb:cc:dd:ee:01")
    assert res1 is not None
    assert res1[0] == "site1"
    assert res1[1]["name"] == "Device 1"

    # Matches mac_address
    res2 = find_client_by_mac(clients_by_site, "00:11:22:33:44:55")
    assert res2 is not None
    assert res2[0] == "site1"
    assert res2[1]["name"] == "Device 2"

    # Matches second site
    res3 = find_client_by_mac(clients_by_site, "66:77:88:99:aa:bb")
    assert res3 is not None
    assert res3[0] == "site2"
    assert res3[1]["name"] == "Device 3"


def test_find_client_by_mac_ignores_malformed_data() -> None:
    """find_client_by_mac ignores non-dicts and returns None if unmatched."""
    assert find_client_by_mac("not-a-dict", "00:11:22:33:44:55") is None
    malformed = {
        "site1": "not-a-dict",
        "site2": {
            "c1": "not-a-dict",
            "c2": {"name": "No MAC"},
        },
    }
    assert find_client_by_mac(malformed, "00:11:22:33:44:55") is None


def test_select_wake_history_keeps_recent_named_wired_clients() -> None:
    """select_wake_history keeps recently seen named wired clients."""
    now = 1_000_000.0
    records = [
        {
            "mac": "00:11:22:33:44:55",
            "is_wired": True,
            "name": "Workstation",
            "last_seen": now - 3600,
        },
        {
            "mac": "66:77:88:99:aa:bb",
            "is_wired": True,
            "hostname": "NAS-Server",
            "last_seen": now - 86400 * 10,
        },
    ]
    selected = select_wake_history(records, now=now)
    assert selected == {
        "00:11:22:33:44:55": "Workstation",
        "66:77:88:99:aa:bb": "NAS-Server",
    }


@pytest.mark.parametrize(
    "record",
    [
        {
            "mac": "00:11:22:33:44:55",
            "is_wired": False,
            "name": "WiFi PC",
            "last_seen": 1_000_000,
        },
        {"mac": "00:11:22:33:44:55", "name": "No wired flag", "last_seen": 1_000_000},
        {
            "mac": "not-a-mac",
            "is_wired": True,
            "name": "Bad MAC",
            "last_seen": 1_000_000,
        },
        {"mac": "00:11:22:33:44:55", "is_wired": True, "last_seen": 1_000_000},
        {
            "mac": "00:11:22:33:44:55",
            "is_wired": True,
            "name": "   ",
            "last_seen": 1_000_000,
        },
        {
            "mac": "00:11:22:33:44:55",
            "is_wired": True,
            "name": "Old PC",
            "last_seen": 1_000_000 - 31 * 86400,
        },
        {"mac": "00:11:22:33:44:55", "is_wired": True, "name": "No last seen"},
        {
            "mac": "00:11:22:33:44:55",
            "is_wired": True,
            "name": "Bool seen",
            "last_seen": True,
        },
        {
            "mac": "00:11:22:33:44:55",
            "is_wired": True,
            "name": "Str seen",
            "last_seen": "yesterday",
        },
        {
            "mac": "00:11:22:33:44:55",
            "is_wired": True,
            "name": "NaN seen",
            "last_seen": float("nan"),
        },
        {
            "mac": "00:11:22:33:44:55",
            "is_wired": True,
            "name": "Huge seen",
            "last_seen": -(10**400),
        },
        "not-a-dict",
    ],
)
def test_select_wake_history_skips_ineligible_records(record: Any) -> None:
    """select_wake_history drops records that do not meet eligibility criteria."""
    now = 1_000_000.0
    assert select_wake_history([record], now=now) == {}


def test_select_wake_history_uses_hostname_and_keeps_first_duplicate() -> None:
    """select_wake_history uses hostname fallback and keeps first duplicate."""
    now = 1_000_000.0
    records = [
        {
            "mac": "00:11:22:33:44:55",
            "is_wired": True,
            "hostname": "Host1",
            "last_seen": now - 100,
        },
        {
            "mac": "00:11:22:33:44:55",
            "is_wired": True,
            "name": "Host2",
            "last_seen": now - 50,
        },
    ]
    selected = select_wake_history(records, now=now)
    assert selected == {"00:11:22:33:44:55": "Host1"}


def test_history_network_hint_reads_first_matching_record() -> None:
    """history_network_hint extracts IP and network ID preferring prioritized fields."""
    records = [
        {
            "mac": "66:77:88:99:aa:bb",
            "last_ip": "10.0.0.99",
            "last_connection_network_id": "other_net",
        },
        {
            "mac": "00:11:22:33:44:55",
            "last_ip": "10.0.0.50",
            "fixed_ip": "10.0.0.51",
            "last_connection_network_id": "net1",
            "network_id": "net2",
        },
    ]
    ip, net_id = history_network_hint(records, "00:11:22:33:44:55")
    assert ip == "10.0.0.50"
    assert net_id == "net1"


def test_history_network_hint_without_match_or_fields() -> None:
    """history_network_hint returns (None, None) when no record or fields match."""
    records: list[Any] = [
        "not-a-dict",
        {"mac": "00:11:22:33:44:55"},
        {"mac": "99:99:99:99:99:99", "last_ip": "10.0.0.1"},
    ]
    assert history_network_hint(records, "11:22:33:44:55:66") == (None, None)
    assert history_network_hint(records, "00:11:22:33:44:55") == (None, None)


def test_derive_directed_broadcast_from_client_ip() -> None:
    """derive_directed_broadcast computes broadcast from matching client IP."""
    networks = [
        {
            "id": "net1",
            "purpose": "corporate",
            "enabled": True,
            "ip_subnet": "10.0.0.1/24",
        }
    ]
    assert derive_directed_broadcast(networks, ip="10.0.0.25") == "10.0.0.255"


def test_derive_directed_broadcast_prefers_network_id_over_ip() -> None:
    """derive_directed_broadcast prioritizes network_id match over IP match."""
    networks = [
        {
            "id": "net1",
            "purpose": "corporate",
            "enabled": True,
            "ip_subnet": "10.0.1.1/24",
        },
        {
            "id": "net2",
            "purpose": "corporate",
            "enabled": True,
            "ip_subnet": "10.0.2.1/24",
        },
    ]
    assert (
        derive_directed_broadcast(networks, ip="10.0.2.50", network_id="net1")
        == "10.0.1.255"
    )


def test_derive_directed_broadcast_handles_non_24_prefix() -> None:
    """derive_directed_broadcast handles /23 subnets correctly."""
    networks = [
        {
            "id": "net1",
            "purpose": "corporate",
            "enabled": True,
            "ip_subnet": "10.0.0.1/23",
        }
    ]
    assert derive_directed_broadcast(networks, ip="10.0.1.20") == "10.0.1.255"


def test_derive_directed_broadcast_ignores_unusable_networks() -> None:
    """derive_directed_broadcast filters disabled, non-LAN, IPv6, and /31-/32."""
    networks = [
        "not-a-dict",
        {"id": "wan1", "purpose": "wan", "ip_subnet": "10.0.0.1/24"},
        {"id": "vpn1", "purpose": "site-vpn", "ip_subnet": "10.0.1.1/24"},
        {
            "id": "dis",
            "purpose": "corporate",
            "enabled": False,
            "ip_subnet": "10.0.2.1/24",
        },
        {"id": "v6", "purpose": "corporate", "ip_subnet": "2001:db8::1/64"},
        {"id": "p31", "purpose": "corporate", "ip_subnet": "10.0.3.1/31"},
        {"id": "p32", "purpose": "corporate", "ip_subnet": "10.0.4.1/32"},
        {"id": "bad", "purpose": "corporate", "ip_subnet": "invalid-subnet"},
        {"id": "num", "purpose": "corporate", "ip_subnet": 123},
    ]
    assert derive_directed_broadcast(networks, ip="10.0.0.5") is None
    assert derive_directed_broadcast(networks, ip="10.0.2.5") is None
    assert derive_directed_broadcast(networks, ip="10.0.3.1") is None
    assert derive_directed_broadcast(networks, ip="10.0.4.1") is None
    assert derive_directed_broadcast(networks, network_id="wan1") is None
    assert derive_directed_broadcast(networks, network_id="dis") is None
    assert derive_directed_broadcast(networks, network_id="p32") is None


def test_derive_directed_broadcast_returns_none_when_unknown_or_ambiguous() -> None:
    """derive_directed_broadcast returns None when ambiguous or IP does not match."""
    # 1. No hints
    assert derive_directed_broadcast([]) is None

    # 2. Bad IP
    networks = [{"id": "net1", "purpose": "corporate", "ip_subnet": "10.0.0.1/24"}]
    assert derive_directed_broadcast(networks, ip="bad-ip") is None
    assert derive_directed_broadcast(networks, ip="2001:db8::1") is None

    # 3. Two overlapping networks
    overlapping = [
        {"id": "net1", "purpose": "corporate", "ip_subnet": "10.0.0.1/16"},
        {"id": "net2", "purpose": "corporate", "ip_subnet": "10.0.0.1/24"},
    ]
    assert derive_directed_broadcast(overlapping, ip="10.0.0.50") is None

    # 4. Unknown network ID and IP outside all subnets
    assert (
        derive_directed_broadcast(networks, ip="192.168.1.1", network_id="net99")
        is None
    )


# =============================================================================
# Section B: Device-coordinator wake command and facade wake history
# =============================================================================


@pytest.fixture
def mock_sub_coordinators() -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    """Create mock sub-coordinators for facade tests."""
    config_coord = MagicMock()
    config_coord.last_update_success = True
    config_coord.get_site.return_value = {"internalReference": "default"}
    config_coord.get_site_ids.return_value = ["site1"]
    # The real single legacy site-name rule, over the mocked get_site.
    config_coord.data = {}
    config_coord.resolve_legacy_site_name.side_effect = (
        lambda site_id, *, verified_only: (
            UnifiInsightsConfigCoordinator.resolve_legacy_site_name(
                config_coord, site_id, verified_only=verified_only
            )
        )
    )

    device_coord = MagicMock()
    device_coord.last_update_success = True
    protect_coord = MagicMock()
    innerspace_coord = MagicMock()

    return config_coord, device_coord, protect_coord, innerspace_coord


@pytest.fixture
def facade(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
    monkeypatch,
) -> UnifiInsightsFacadeCoordinator:
    """Create a facade coordinator fixture for wake tests."""
    config_coord, device_coord, protect_coord, innerspace_coord = mock_sub_coordinators
    network_client = MagicMock()
    network_client.networks.get_legacy_all = AsyncMock(
        return_value=[
            {
                "id": "net1",
                "purpose": "corporate",
                "enabled": True,
                "ip_subnet": "10.0.0.1/24",
            }
        ]
    )
    network_client.clients.get_historical_legacy = AsyncMock(return_value=[])

    coord = UnifiInsightsFacadeCoordinator(
        hass=hass,
        network_client=network_client,
        protect_client=None,
        entry=mock_config_entry,
        config_coordinator=config_coord,
        device_coordinator=device_coord,
        protect_coordinator=protect_coord,
        innerspace_coordinator=innerspace_coord,
    )
    patch_attribute(monkeypatch, coord, "data", {"clients": {}})
    return coord


@pytest.fixture
def device(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    facade: UnifiInsightsFacadeCoordinator,
    mock_sub_coordinators: tuple[MagicMock, MagicMock, MagicMock, MagicMock],
) -> UnifiInsightsDeviceCoordinator:
    """Create the device coordinator that owns the wake command."""
    coord = UnifiInsightsDeviceCoordinator(
        hass,
        facade.network_client,
        None,
        mock_config_entry,
        mock_sub_coordinators[0],
    )
    coord.data = {"clients": {}}
    return coord


async def test_async_wake_client_sends_packet_with_broadcast_for_online_client(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator
) -> None:
    """Online client with IP uses derived broadcast without fetching history."""
    mac = "00:11:22:33:44:55"
    device.data = {
        "clients": {"site1": {"c1": {"macAddress": mac, "ipAddress": "10.0.0.50"}}}
    }
    calls = async_mock_service(hass, "wake_on_lan", "send_magic_packet")

    await device.async_wake_client(mac)

    assert len(calls) == 1
    assert calls[0].data == {
        "mac": mac,
        "broadcast_address": "10.0.0.255",
    }
    require_mock(
        device.network_client.clients.get_historical_legacy
    ).assert_not_awaited()


async def test_async_wake_client_uses_history_network_for_offline_client(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator
) -> None:
    """Offline client fetches history and uses last_connection_network_id broadcast."""
    mac = "00:11:22:33:44:55"
    device.data = {"clients": {}}
    device.network_client.clients.get_historical_legacy = AsyncMock(
        return_value=[
            {
                "mac": mac,
                "is_wired": True,
                "last_connection_network_id": "net1",
            }
        ]
    )
    calls = async_mock_service(hass, "wake_on_lan", "send_magic_packet")

    await device.async_wake_client(mac)

    assert len(calls) == 1
    assert calls[0].data == {
        "mac": mac,
        "broadcast_address": "10.0.0.255",
    }


async def test_async_wake_client_falls_back_to_history_when_online_client_has_no_ip(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator
) -> None:
    """Online client without IP falls back to history network lookup."""
    mac = "00:11:22:33:44:55"
    device.data = {"clients": {"site1": {"c1": {"macAddress": mac}}}}
    device.network_client.clients.get_historical_legacy = AsyncMock(
        return_value=[
            {
                "mac": mac,
                "is_wired": True,
                "last_connection_network_id": "net1",
            }
        ]
    )
    calls = async_mock_service(hass, "wake_on_lan", "send_magic_packet")

    await device.async_wake_client(mac)

    assert len(calls) == 1
    assert calls[0].data == {
        "mac": mac,
        "broadcast_address": "10.0.0.255",
    }


async def test_async_wake_client_omits_broadcast_when_nothing_matches(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator
) -> None:
    """Packet omits broadcast_address when derivation finds no match."""
    mac = "00:11:22:33:44:55"
    device.data = {
        "clients": {"site1": {"c1": {"macAddress": mac, "ipAddress": "192.168.99.10"}}}
    }
    calls = async_mock_service(hass, "wake_on_lan", "send_magic_packet")

    await device.async_wake_client(mac)

    assert len(calls) == 1
    assert calls[0].data == {"mac": mac}


async def test_async_wake_client_omits_broadcast_when_lookup_fails(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator
) -> None:
    """Packet omits broadcast_address if network lookup raises an exception."""
    mac = "00:11:22:33:44:55"
    device.data = {
        "clients": {"site1": {"c1": {"macAddress": mac, "ipAddress": "10.0.0.50"}}}
    }
    device.network_client.networks.get_legacy_all = AsyncMock(
        side_effect=RuntimeError("Network lookup failed")
    )
    calls = async_mock_service(hass, "wake_on_lan", "send_magic_packet")

    await device.async_wake_client(mac)

    assert len(calls) == 1
    assert calls[0].data == {"mac": mac}


async def test_async_wake_client_omits_broadcast_when_derivation_raises(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator
) -> None:
    """A derivation failure still sends the magic packet without a broadcast."""
    mac = "00:11:22:33:44:55"
    device._async_derive_wake_broadcast = AsyncMock(
        side_effect=RuntimeError("Unexpected derivation failure")
    )
    calls = async_mock_service(hass, "wake_on_lan", "send_magic_packet")

    await device.async_wake_client(mac)

    device._async_derive_wake_broadcast.assert_awaited_once_with(mac)
    assert len(calls) == 1
    assert calls[0].data == {"mac": mac}


async def test_async_wake_client_skips_sites_without_a_verified_name(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator
) -> None:
    """Sites without verified legacy site name are skipped without failing."""
    mac = "00:11:22:33:44:55"
    require_mock(device.config_coordinator.get_site_ids).return_value = [
        "site1",
        "site2",
    ]
    require_mock(device.config_coordinator.get_site).side_effect = lambda s: (
        None if s == "site1" else {"internalReference": "site2_ref"}
    )
    calls = async_mock_service(hass, "wake_on_lan", "send_magic_packet")

    await device.async_wake_client(mac)

    assert len(calls) == 1
    assert calls[0].data["mac"] == mac


async def test_async_wake_client_normalizes_the_mac(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator
) -> None:
    """MAC address is normalized before sending to service."""
    calls = async_mock_service(hass, "wake_on_lan", "send_magic_packet")

    await device.async_wake_client("AA-BB-CC-DD-EE-FF")

    assert len(calls) == 1
    assert calls[0].data["mac"] == "aa:bb:cc:dd:ee:ff"


async def test_async_wake_client_rejects_invalid_mac(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator
) -> None:
    """Invalid MAC address raises HomeAssistantError without calling service."""
    calls = async_mock_service(hass, "wake_on_lan", "send_magic_packet")

    with pytest.raises(HomeAssistantError) as exc_info:
        await device.async_wake_client("not-a-mac")

    assert exc_info.value.translation_domain == DOMAIN
    assert exc_info.value.translation_key == "wake_failed"
    assert exc_info.value.translation_placeholders == {
        "client_name": "not-a-mac",
        "error": "invalid MAC address",
    }
    assert len(calls) == 0


async def test_async_wake_client_reports_missing_wake_on_lan_service(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator
) -> None:
    """ServiceNotFound from wake_on_lan service is mapped to wake_on_lan_unavailable."""
    with pytest.raises(HomeAssistantError) as exc_info:
        await device.async_wake_client("00:11:22:33:44:55")

    assert exc_info.value.translation_domain == DOMAIN
    assert exc_info.value.translation_key == "wake_on_lan_unavailable"


@pytest.mark.parametrize(
    "error",
    [
        OSError("Network is unreachable"),
        ValueError("bad"),
    ],
)
async def test_async_wake_client_maps_send_errors(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator, error: Exception
) -> None:
    """OSError and ValueError from service are mapped to wake_failed."""

    async def handler(call: Any) -> None:
        raise error

    hass.services.async_register("wake_on_lan", "send_magic_packet", handler)

    with pytest.raises(HomeAssistantError) as exc_info:
        await device.async_wake_client("00:11:22:33:44:55")

    assert exc_info.value.translation_domain == DOMAIN
    assert exc_info.value.translation_key == "wake_failed"
    assert exc_info.value.translation_placeholders == {
        "client_name": "Client 00:11:22:33:44:55",
        "error": str(error),
    }


async def test_async_wake_client_passes_home_assistant_errors_through(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator
) -> None:
    """Generic HomeAssistantError passes through without re-wrapping."""

    async def handler(call: Any) -> None:
        msg = "custom error message"
        raise HomeAssistantError(msg)

    hass.services.async_register("wake_on_lan", "send_magic_packet", handler)

    with pytest.raises(HomeAssistantError) as exc_info:
        await device.async_wake_client("00:11:22:33:44:55")

    assert str(exc_info.value) == "custom error message"
    assert exc_info.value.translation_key is None


async def test_async_get_wake_history_merges_sites_and_ignores_failures(
    facade: UnifiInsightsFacadeCoordinator, freezer: Any
) -> None:
    """async_get_wake_history merges records from multiple sites and skips errors."""
    freezer.move_to("2026-10-09T12:00:00Z")
    now = 1_791_547_200.0  # epoch corresponding to fixed time

    require_mock(facade._config_coordinator.get_site_ids).return_value = [
        "site1",
        "site2",
        "site3",
    ]
    require_mock(facade._config_coordinator.get_site).side_effect = lambda s: {
        "internalReference": f"{s}_ref"
    }

    records_site1 = [
        {
            "mac": "00:11:22:33:44:01",
            "is_wired": True,
            "name": "PC 1",
            "last_seen": now - 100,
        },
        {
            "mac": "00:11:22:33:44:02",
            "is_wired": True,
            "name": "PC 2 (Site 1)",
            "last_seen": now - 100,
        },
    ]
    records_site2 = [
        {
            "mac": "00:11:22:33:44:02",
            "is_wired": True,
            "name": "PC 2 (Site 2)",
            "last_seen": now - 50,
        },
        {
            "mac": "00:11:22:33:44:03",
            "is_wired": True,
            "name": "PC 3",
            "last_seen": now - 200,
        },
    ]

    async def get_history(site_name: str) -> list[dict[str, Any]]:
        if site_name == "site1_ref":
            return records_site1
        if site_name == "site2_ref":
            return records_site2
        msg = "API failure"
        raise UniFiResponseError(msg, status_code=500)

    require_mock(
        facade.network_client.clients.get_historical_legacy
    ).side_effect = get_history

    history = await facade.async_get_wake_history()

    assert history == {
        "00:11:22:33:44:01": ("PC 1", "site1"),
        "00:11:22:33:44:02": ("PC 2 (Site 1)", "site1"),
        "00:11:22:33:44:03": ("PC 3", "site2"),
    }


async def test_async_get_wake_history_is_empty_without_sites(
    facade: UnifiInsightsFacadeCoordinator,
) -> None:
    """async_get_wake_history returns empty dict when no sites configured."""
    require_mock(facade._config_coordinator.get_site_ids).return_value = []
    assert await facade.async_get_wake_history() == {}


async def test_async_get_wake_history_keeps_any_age_name_only_for_enabled_buttons(
    facade: UnifiInsightsFacadeCoordinator, freezer: Any
) -> None:
    """Enabled Wake rows use old names; new buttons retain the recent-wired rule."""
    freezer.move_to("2026-10-09T12:00:00Z")
    mac = "00:11:22:33:44:55"
    require_mock(facade.network_client.clients.get_historical_legacy).return_value = [
        {
            "mac": mac,
            "is_wired": True,
            "name": " ",
            "hostname": "Old PC",
            "last_seen": 1_791_547_200.0 - 40 * 86400,
        }
    ]
    assert await facade.async_get_wake_history() == {}
    assert await facade.async_get_wake_history(enabled_macs={mac}) == {
        mac: ("Old PC", "site1")
    }


@pytest.mark.parametrize("name", ["", "  "])
async def test_async_wake_client_error_uses_hostname_after_blank_name(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator, name: str
) -> None:
    """Blank names do not hide a useful hostname in translated errors."""
    mac = "00:11:22:33:44:55"
    device.data = {
        "clients": {
            "site1": {"c1": {"macAddress": mac, "name": name, "hostname": "Host PC"}}
        }
    }
    with (
        patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=OSError("send failed"),
        ),
        pytest.raises(HomeAssistantError) as exc,
    ):
        await device.async_wake_client(mac)
    assert (
        require_not_none(exc.value.translation_placeholders)["client_name"] == "Host PC"
    )


async def test_async_wake_client_error_uses_supplied_client_name(
    hass: HomeAssistant,
    device: UnifiInsightsDeviceCoordinator,
    init_integration: MockConfigEntry,
) -> None:
    """The caller's name appears in errors even when there is no live client."""
    with (
        patch(
            "homeassistant.core.ServiceRegistry.async_call",
            side_effect=OSError("send failed"),
        ),
        pytest.raises(HomeAssistantError) as exc,
    ):
        await device.async_wake_client("00:11:22:33:44:55", client_name="Saved PC")
    assert (
        require_not_none(exc.value.translation_placeholders)["client_name"]
        == "Saved PC"
    )
    assert "Saved PC" in str(exc.value)


def test_manifest_declares_wake_on_lan_dependency() -> None:
    """manifest.json must declare wake_on_lan as a dependency."""
    manifest_path = (
        Path(__file__).parents[1] / "custom_components/unifi_insights/manifest.json"
    )
    manifest = json.loads(manifest_path.read_text())
    assert "wake_on_lan" in manifest.get("dependencies", [])


async def test_wake_on_lan_dependency_registers_the_service(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Loading integration loads wake_on_lan and registers service."""
    assert hass.services.has_service("wake_on_lan", "send_magic_packet")


async def test_async_wake_client_broadcast_lookup_timeout_fallback(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator
) -> None:
    """Press-time broadcast lookup times out and sends packet without broadcast."""
    mac = "00:11:22:33:44:55"
    device.data = {
        "clients": {"site1": {"c1": {"macAddress": mac, "ipAddress": "10.0.0.50"}}}
    }

    hang_event = asyncio.Event()

    async def hang(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        await hang_event.wait()
        return []

    device.network_client.networks.get_legacy_all = AsyncMock(side_effect=hang)
    calls = async_mock_service(hass, "wake_on_lan", "send_magic_packet")

    with patch(
        "custom_components.unifi_insights.coordinators.device.WAKE_BROADCAST_TIMEOUT_SECONDS",
        0.01,
        create=True,
    ):
        async with asyncio.timeout(1):
            await device.async_wake_client(mac)

    assert len(calls) == 1
    assert calls[0].data == {"mac": mac}


@pytest.mark.parametrize("failed_coordinator", ["config_coordinator", "device"])
async def test_async_wake_client_skips_lookup_when_sub_coordinator_failed(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator, failed_coordinator: str
) -> None:
    """Broadcast lookup is skipped when either network sub-coordinator fails."""
    mac = "00:11:22:33:44:55"
    device.data = {
        "clients": {"site1": {"c1": {"macAddress": mac, "ipAddress": "10.0.0.50"}}}
    }
    owner = device if failed_coordinator == "device" else device.config_coordinator
    owner.last_update_success = False
    calls = async_mock_service(hass, "wake_on_lan", "send_magic_packet")

    await device.async_wake_client(mac)

    assert len(calls) == 1
    assert calls[0].data == {"mac": mac}
    require_mock(device.network_client.networks.get_legacy_all).assert_not_awaited()


def test_select_wake_history_30_day_boundary() -> None:
    """30-day boundary: 30 days and just inside kept, just outside dropped."""
    now = 1000000.0
    window = 30 * 86400
    exact = now - window
    inside = now - window + 1
    outside = now - window - 1

    records = [
        {
            "mac": "00:11:22:33:44:01",
            "name": "Exact",
            "is_wired": True,
            "last_seen": exact,
        },
        {
            "mac": "00:11:22:33:44:02",
            "name": "Inside",
            "is_wired": True,
            "last_seen": inside,
        },
        {
            "mac": "00:11:22:33:44:03",
            "name": "Outside",
            "is_wired": True,
            "last_seen": outside,
        },
    ]
    res = select_wake_history(records, now=now)
    assert "00:11:22:33:44:01" in res
    assert "00:11:22:33:44:02" in res
    assert "00:11:22:33:44:03" not in res


async def test_async_wake_client_press_path_site_name_from_internal_reference_only(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator
) -> None:
    """Press path resolves legacy site name from internalReference only."""
    mac = "00:11:22:33:44:55"
    device.data = {"clients": {}}
    require_mock(device.config_coordinator.get_site_ids).return_value = [
        "site1",
        "site2",
    ]
    require_mock(device.config_coordinator.get_site).side_effect = lambda s: (
        {"internalReference": "alpha"} if s == "site1" else {}
    )
    device.network_client.networks.get_legacy_all = AsyncMock(return_value=[])
    device.network_client.clients.get_historical_legacy = AsyncMock(return_value=[])
    calls = async_mock_service(hass, "wake_on_lan", "send_magic_packet")

    await device.async_wake_client(mac)

    assert len(calls) == 1
    assert device.network_client.networks.get_legacy_all.await_args_list == [
        call("alpha")
    ]


async def test_async_wake_client_online_queries_only_own_site(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator
) -> None:
    """An online client queries only its own site, not every site."""
    mac = "00:11:22:33:44:55"
    device.data = {
        "clients": {"site1": {"c1": {"macAddress": mac, "ipAddress": "10.0.0.50"}}}
    }
    require_mock(device.config_coordinator.get_site_ids).return_value = [
        "site1",
        "site2",
    ]
    require_mock(device.config_coordinator.get_site).side_effect = lambda s: {
        "internalReference": f"{s}_ref"
    }
    device.network_client.networks.get_legacy_all = AsyncMock(return_value=[])
    calls = async_mock_service(hass, "wake_on_lan", "send_magic_packet")

    await device.async_wake_client(mac)

    assert len(calls) == 1
    assert device.network_client.networks.get_legacy_all.await_args_list == [
        call("site1_ref")
    ]


async def test_async_wake_client_calls_service_blocking(
    hass: HomeAssistant, device: UnifiInsightsDeviceCoordinator
) -> None:
    """Service call to wake_on_lan explicitly passes blocking=True."""
    mac = "00:11:22:33:44:55"
    device.data = {"clients": {}}
    with patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ) as mock_call:
        await device.async_wake_client(mac)
        mock_call.assert_awaited_once_with(
            "wake_on_lan",
            "send_magic_packet",
            {"mac": mac},
            blocking=True,
        )
