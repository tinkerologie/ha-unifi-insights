# Copyright 2026 UniFi Insights contributors
"""Tests for the UniFi Insights diagnostics."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.components.diagnostics.const import REDACTED
from homeassistant.const import CONF_API_KEY
from homeassistant.helpers.json import ExtendedJSONEncoder, JSONEncoder

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

from unifi_official_api import __version__
from unifi_official_api.const import SENSITIVE_CREDENTIAL_KEYS
from unifi_official_api.network.models.client import Client

from custom_components.unifi_insights.const import (
    CONF_ACCESS_WEBHOOK_ENDPOINT_ID,
    CONF_ACCESS_WEBHOOK_ID,
    CONF_ACCESS_WEBHOOK_SECRET,
)
from custom_components.unifi_insights.coordinators.config import (
    UnifiConfigCoordinator,
)
from custom_components.unifi_insights.data_transforms import (
    NETWORK_FACTS_KEY,
    build_site_network_facts,
)
from custom_components.unifi_insights.diagnostics import (
    TO_REDACT,
    _redact_coordinator_data,
    async_get_config_entry_diagnostics,
)
from tests.fixtures.library_responses import (
    SAMPLE_ALARM_HUB,
    SAMPLE_ALARM_HUB_TAMPER_EVENT,
    SAMPLE_SIREN,
    SAMPLE_THREAD_LINK_STATION,
    SAMPLE_UDM_PRO_OVERVIEW,
    SAMPLE_UDM_PRO_STATS,
)
from tests.fixtures.network_rule_responses import (
    nat_rule_record,
    port_forward_record,
    static_route_record,
    traffic_rule_record,
)


async def test_diagnostics(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test diagnostics."""
    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)

    assert diagnostics["library_version"] == __version__
    assert "connection" in diagnostics
    assert "entry" in diagnostics
    assert "data" in diagnostics

    # Check connection info - host is always redacted in diagnostics
    assert diagnostics["connection"]["host"] == "**REDACTED**"
    assert diagnostics["connection"]["network_client_connected"] is True
    assert diagnostics["connection"]["protect_client_connected"] is True

    # Check that actual API key value is redacted (not appearing in output)
    assert "test_api_key" not in str(diagnostics)
    # The key name "api_key" will appear, but the value should be redacted
    assert diagnostics["entry"]["data"]["api_key"] == "**REDACTED**"


async def test_site_manager_diagnostics_exclude_raw_cloud_snapshot(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Account-wide Site Manager records never pass through generic redaction."""
    snapshot = {
        "hosts": {
            "secret-host": {
                "id": "secret-host",
                "reportedState": {"hostname": "private.example.test"},
            }
        },
        "sites": {},
        "devices": {},
        "isp_metrics": {},
        "sd_wan_configs": {},
        "collections": {},
        "last_attempt": None,
        "cooldown_until": None,
    }
    runtime = init_integration.runtime_data
    runtime.site_manager_coordinator = SimpleNamespace(data=snapshot)
    runtime.coordinator.data["site_manager"] = snapshot

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)

    assert diagnostics["site_manager"]["inventory"]["hosts"] == 1
    assert "site_manager" not in diagnostics["data"]
    assert "secret-host" not in repr(diagnostics)
    assert "private.example.test" not in repr(diagnostics)


async def test_diagnostics_includes_websocket_health(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test diagnostics surface the Protect WebSocket health signal.

    There was previously no way to tell "WS connected and delivering" from
    "connected but silent" from "reconnect-looping" (task 5) - diagnostics
    is the first place an operator would look to distinguish those.
    """
    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)

    assert "websocket" in diagnostics
    assert diagnostics["websocket"]["connected"] is False
    assert diagnostics["websocket"]["last_message_at"] is None

    # Review finding 1: a single shared connected/last_message_at pair
    # cannot tell "both subscriptions healthy" apart from "devices healthy,
    # events silently hung" - per-subscription detail must reach this
    # diagnostics payload too, not just the coordinator's own property.
    assert diagnostics["websocket"]["devices"] == {
        "connected": False,
        "last_message_at": None,
    }
    assert diagnostics["websocket"]["events"] == {
        "connected": False,
        "last_message_at": None,
    }


def _strings(value: Any) -> list[str]:
    """Return every string key and value in a nested diagnostics payload."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(value, (list, tuple, set)):
        return [s for item in value for s in _strings(item)]
    return []


@pytest.mark.parametrize(
    ("passphrase", "escaped"),
    [
        ("simple-password", "simple-password"),
        ('sp;e:c,i"a\\l-pass', 'sp\\;e\\:c\\,i\\"a\\\\l-pass'),
    ],
)
async def test_diagnostics_redacts_wifi_qr_code(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
    passphrase: str,
    escaped: str,
) -> None:
    """Test diagnostics does not disclose password-bearing Wi-Fi QR payloads."""
    wifi = {"wifi-1": {"name": "Test WiFi"}}
    UnifiConfigCoordinator._enrich_wifi(
        wifi,
        [
            {
                "name": "Test WiFi",
                "x_passphrase": passphrase,
                "security": "wpa2",
            }
        ],
        [],
    )
    qr_code = wifi["wifi-1"]["qr_code"]
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["wifi"] = {"site-1": wifi}

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)
    diagnostic_wifi = diagnostics["data"]["wifi"]["site-1"]["wifi-1"]

    assert qr_code.startswith("WIFI:T:WPA;S:Test WiFi;P:")
    assert wifi["wifi-1"]["passphrase"] == passphrase
    assert coordinator.data["wifi"]["site-1"]["wifi-1"]["qr_code"] == qr_code
    assert diagnostic_wifi["passphrase"] == REDACTED
    assert diagnostic_wifi["qr_code"] == REDACTED
    assert f"P:{escaped};" in qr_code
    for text in _strings(diagnostics):
        assert passphrase not in text
        assert escaped not in text


CLIENT_PAYLOAD = {
    "id": "client-1",
    "macAddress": "AA:BB:CC:11:22:33",
    "name": "Sarah's iPhone",
    "hostname": "sarahs-iphone",
    "ipAddress": "192.168.1.55",
    "type": "WIRELESS",
    "essid": "Sarah and Tom 5G",
    "bssid": "AA:BB:CC:44:55:66",
    "apMac": "AA:BB:CC:44:55:60",
    "swMac": "AA:BB:CC:77:88:99",
    "deviceName": "Sarah's iPhone 15",
    "osName": "iOS",
    "signal": -52,
    # Client accepts unknown extra fields, so anything the controller starts
    # sending lands in the diagnostics payload untouched by a key list.
    "note": "Sarah - bedroom",
    "gwMac": "AA:BB:CC:AB:CD:EF",
}

PERSONAL_STRINGS = (
    "Sarah's iPhone",
    "sarahs-iphone",
    "Sarah and Tom 5G",
    "Sarah's iPhone 15",
    "Sarah - bedroom",
    "192.168.1.55",
    "AA:BB:CC:11:22:33",
    "AA:BB:CC:44:55:66",
    "AA:BB:CC:44:55:60",
    "AA:BB:CC:77:88:99",
    "AA:BB:CC:AB:CD:EF",
)


def _client_record() -> dict[str, Any]:
    """Return a client record shaped exactly like the coordinator stores one."""
    return Client.model_validate(CLIENT_PAYLOAD).model_dump(by_alias=True)


def _seed_client_data(coordinator: Any) -> dict[str, Any]:
    """Put a synthetic client into every place the coordinator keeps one."""
    record = _client_record()
    coordinator.data["clients"] = {"site-1": {"client-1": record}}
    coordinator.data["stats"] = {
        "site-1": {"device-1": {"id": "device-1", "clients": [record]}}
    }
    return record


async def test_diagnostics_redacts_client_identity(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test diagnostics does not disclose who is on the network.

    Redacting passwords is not enough: a client record names its owner
    ("Sarah's iPhone"), the SSID they connect to, and the MAC addresses of
    both the client and the access point or switch it sits behind.
    """
    coordinator = init_integration.runtime_data.coordinator
    record = _seed_client_data(coordinator)

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)
    client = diagnostics["data"]["clients"]["site-1"]["client-1"]

    assert client["name"] == REDACTED
    assert client["hostname"] == REDACTED
    assert client["deviceName"] == REDACTED
    assert client["note"] == REDACTED
    assert client["essid"] == REDACTED
    assert client["ipAddress"] == REDACTED
    for key in ("macAddress", "bssid", "apMac", "swMac", "gwMac"):
        assert client[key].startswith("**REDACTED-MAC-")

    # Non-identifying telemetry is what makes the report useful - keep it.
    assert client["osName"] == "iOS"
    assert client["signal"] == -52
    assert client["type"] == "WIRELESS"

    # The same record is copied into per-device statistics; it has to be
    # redacted there too, not just in the clients collection.
    stats_client = diagnostics["data"]["stats"]["site-1"]["device-1"]["clients"][0]
    assert stats_client["name"] == REDACTED
    assert stats_client["essid"] == REDACTED

    texts = _strings(diagnostics)
    for personal in PERSONAL_STRINGS:
        assert personal not in texts

    # The coordinator's own data is untouched; entities still need it.
    assert record["name"] == "Sarah's iPhone"
    assert coordinator.data["clients"]["site-1"]["client-1"]["apMac"] == (
        "AA:BB:CC:44:55:60"
    )


async def test_diagnostics_keeps_hardware_names_readable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test device and site names survive redaction.

    A report where every name is **REDACTED** cannot be read: the names of
    switches, access points and sites are what tell an operator which record
    is which, and none of them name a person.
    """
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["sites"] = {"site-1": {"id": "site-1", "name": "Home"}}
    coordinator.data["devices"] = {
        "site-1": {
            "device-1": {
                "id": "device-1",
                "name": "Garage Switch",
                "model": "USW-24",
                "macAddress": "AA:BB:CC:77:88:99",
            }
        }
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)

    assert diagnostics["data"]["sites"]["site-1"]["name"] == "Home"
    device = diagnostics["data"]["devices"]["site-1"]["device-1"]
    assert device["name"] == "Garage Switch"
    assert device["model"] == "USW-24"
    assert device["macAddress"].startswith("**REDACTED-MAC-")


async def test_diagnostics_redacts_wan_link_addresses(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test merged WAN link addresses are redacted but link state is kept."""
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["devices"] = {
        "site-1": {
            "device-1": {
                "id": "device-1",
                "model": "UCG-Ultra",
                "features": {"gateway": True},
                "wans": [
                    {
                        "key": "wan",
                        "name": "WAN",
                        "status": "online",
                        "ip": "198.51.100.7",
                        "connected": True,
                    }
                ],
            }
        }
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)

    device = diagnostics["data"]["devices"]["site-1"]["device-1"]
    wan = device["wans"][0]
    assert wan["ip"] == REDACTED
    assert device["features"] == {"gateway": True}
    assert wan["status"] == "online"
    assert wan["connected"] is True


async def test_diagnostics_mac_placeholders_are_stable_and_distinct(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test MAC placeholders keep a report correlatable without exposing MACs.

    A blanket **REDACTED** would lose which access point a client is on, and
    would collapse the devices the API lists without an id - those are keyed
    by MAC - into a single colliding entry.
    """
    coordinator = init_integration.runtime_data.coordinator
    _seed_client_data(coordinator)
    coordinator.data["devices"] = {
        "site-1": {
            # Devices reported without an id are keyed by MAC (#128).
            "aa:bb:cc:44:55:60": {"name": "Hallway AP", "mac": "AA:BB:CC:44:55:60"},
            # Unpunctuated under a known MAC key is still the same MAC.
            "aa:bb:cc:77:88:99": {"name": "Garage Switch", "mac": "aabbcc778899"},
        }
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)
    devices = diagnostics["data"]["devices"]["site-1"]
    client = diagnostics["data"]["clients"]["site-1"]["client-1"]

    # Two MAC-keyed devices stay two entries.
    assert len(devices) == 2
    access_point, switch = devices.values()
    assert access_point["name"] == "Hallway AP"
    assert switch["name"] == "Garage Switch"

    # The client's uplinks still point at the right hardware.
    assert client["apMac"] == access_point["mac"]
    assert client["swMac"] == switch["mac"]
    assert client["apMac"] != client["swMac"]
    assert client["macAddress"] not in (client["apMac"], client["swMac"])

    # Differences in case, separator or punctuation are the same MAC, so the
    # entry keys and the records inside them still agree.
    assert list(devices) == [access_point["mac"], switch["mac"]]


async def test_diagnostics_redacts_wifi_ssid(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test the SSID is redacted wherever the WiFi record carries it."""
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["wifi"] = {
        "site-1": {
            "wifi-1": {
                "id": "wifi-1",
                "name": "Sarah and Tom 5G",
                "ssid": "Sarah and Tom 5G",
                "enabled": True,
                "security": "wpa2",
                "num_connected_clients": 4,
            }
        }
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)
    wifi = diagnostics["data"]["wifi"]["site-1"]["wifi-1"]

    assert wifi["name"] == REDACTED
    assert wifi["ssid"] == REDACTED
    assert wifi["enabled"] is True
    assert wifi["num_connected_clients"] == 4
    assert "Sarah and Tom 5G" not in _strings(diagnostics)


async def test_diagnostics_redacts_voucher_codes_and_notes(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test voucher codes and notes are redacted in vouchers and latest_vouchers."""
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["vouchers"] = {
        "site-1": {
            "v-1": {
                "id": "v-1",
                "code": "secret-code-123",
                "name": "Guest John",
                "note": "Secret Guest Note",
                "timeLimitMinutes": 480,
                "expired": False,
            }
        }
    }
    coordinator.data["latest_vouchers"] = {
        "site-1": {
            "id": "v-1",
            "code": "secret-code-123",
            "name": "Guest John",
            "note": "Secret Guest Note",
            "timeLimitMinutes": 480,
            "expired": False,
        }
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)
    voucher = diagnostics["data"]["vouchers"]["site-1"]["v-1"]
    latest = diagnostics["data"]["latest_vouchers"]["site-1"]

    assert voucher["code"] == REDACTED
    assert voucher["name"] == REDACTED
    assert voucher["note"] == REDACTED
    assert voucher["timeLimitMinutes"] == 480
    assert voucher["expired"] is False

    assert latest["code"] == REDACTED
    assert latest["name"] == REDACTED
    assert latest["note"] == REDACTED
    assert latest["timeLimitMinutes"] == 480
    assert latest["expired"] is False

    all_diag_strings = _strings(diagnostics)
    assert "secret-code-123" not in all_diag_strings
    assert "Guest John" not in all_diag_strings
    assert "Secret Guest Note" not in all_diag_strings


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {"clients": "not-a-mapping", "wifi": None},
        {"stats": {"site-1": None}},
        {"stats": {"site-1": {"device-1": {"uptime": 42}}}},
        {"vouchers": "x", "latest_vouchers": None},
        {"latest_vouchers": {"s": None}},
    ],
)
def test_redact_coordinator_data_tolerates_unexpected_shapes(data: Any) -> None:
    """Test redaction never raises on a snapshot that is not fully populated.

    A diagnostics download must still produce a report when the coordinator
    has not loaded yet, or when a section is missing or shaped unexpectedly.
    """
    assert _redact_coordinator_data(data) == data


def test_redact_coordinator_data_hides_thread_ids_and_tamper_user() -> None:
    """Thread network ids identify the network; userName names a person."""
    hub = {**copy.deepcopy(SAMPLE_ALARM_HUB), "_lastTamperUser": "Installer"}
    data = {
        "protect": {
            "link_stations": {"ls": copy.deepcopy(SAMPLE_THREAD_LINK_STATION)},
            "alarm_hubs": {"hub": hub},
            "events": {
                "alarmHubDeviceTamper": {
                    "e1": copy.deepcopy(SAMPLE_ALARM_HUB_TAMPER_EVENT)
                }
            },
        }
    }

    redacted = _redact_coordinator_data(data)

    network = redacted["protect"]["link_stations"]["ls"]["threadState"]["network"]
    for key in ("networkName", "panId", "extendedPanId"):
        assert network[key] == REDACTED
    assert network["role"] == "leader"
    assert network["channel"] == 15
    assert redacted["protect"]["alarm_hubs"]["hub"]["_lastTamperUser"] == REDACTED
    event = redacted["protect"]["events"]["alarmHubDeviceTamper"]["e1"]
    assert event["metadata"]["userName"] == REDACTED
    text = str(redacted)
    for secret in ("Installer", "Home Thread", "0011223344556677", "1a2b"):
        assert secret not in text


def test_redact_coordinator_data_keeps_siren_state_and_hides_ids() -> None:
    """A siren record keeps the state worth debugging and loses its identifier."""
    data = {"protect": {"sirens": {"siren_1": copy.deepcopy(SAMPLE_SIREN)}}}

    redacted = _redact_coordinator_data(data)

    siren = redacted["protect"]["sirens"]["siren_1"]
    assert siren["id"] == REDACTED
    assert siren["sirenStatus"] == {
        "isActive": False,
        "activatedAt": None,
        "duration": None,
    }
    assert siren["volume"] == 50
    assert siren["name"] == "Garage Siren"
    assert siren["state"] == "CONNECTED"


async def test_diagnostics_pseudonymizes_siren_mac(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """A siren's MAC reaches the report only as a placeholder."""
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["protect"]["sirens"] = {"siren_1": copy.deepcopy(SAMPLE_SIREN)}

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)

    siren = diagnostics["data"]["protect"]["sirens"]["siren_1"]
    assert siren["mac"].startswith("**REDACTED-MAC-")
    assert siren["id"] == REDACTED
    assert "AABBCC000005" not in _strings(diagnostics)


async def test_diagnostics_redacts_unpunctuated_macs_anywhere(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test an unpunctuated MAC is redacted under an unknown key or as a key.

    The controller is not consistent about punctuation, and the key list only
    knows the MAC fields that exist today. A bare twelve-hex value is an
    address wherever it turns up, and it is the same address as its punctuated
    form, so both have to reach the same placeholder.
    """
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["clients"] = {
        "site-1": {
            "client-1": {
                "macAddress": "AA:BB:CC:44:55:60",
                # A field no model declares, holding the unpunctuated form.
                "wiredUplinkMacAddress": "aabbcc445560",
            }
        }
    }
    coordinator.data["devices"] = {
        # Devices reported without an id are keyed by MAC, and nothing
        # guarantees the controller punctuates it.
        "site-1": {"aabbcc445560": {"name": "Hallway AP", "model": "U7-Pro"}}
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)
    client = diagnostics["data"]["clients"]["site-1"]["client-1"]
    (device_key,) = diagnostics["data"]["devices"]["site-1"]

    assert client["wiredUplinkMacAddress"].startswith("**REDACTED-MAC-")
    assert device_key.startswith("**REDACTED-MAC-")
    # One address, one placeholder, whichever way it was written.
    assert client["wiredUplinkMacAddress"] == client["macAddress"] == device_key
    assert "aabbcc445560" not in _strings(diagnostics)


async def test_diagnostics_keeps_malformed_mac_values_distinct(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test two unparsable values under MAC keys do not share a placeholder.

    The models accept any string for a MAC field. Canonicalizing by discarding
    everything that is not a hex character would make `not-a-mac` and `aac`
    the same value, which quietly rewrites the client-to-device relationships
    the placeholders exist to preserve.
    """
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["clients"] = {
        "site-1": {
            "client-1": {"macAddress": "not-a-mac", "apMac": "aac"},
            "client-2": {"macAddress": "aac"},
        }
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)
    clients = diagnostics["data"]["clients"]["site-1"]
    first = clients["client-1"]
    second = clients["client-2"]

    assert first["macAddress"] != first["apMac"]
    # The same unparsable value is still the same value.
    assert first["apMac"] == second["macAddress"]
    for value in (first["macAddress"], first["apMac"]):
        assert value.startswith("**REDACTED-MAC-")
    assert "not-a-mac" not in _strings(diagnostics)


async def test_diagnostics_placeholders_topology_uplink_mac(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """The copied legacy parent MAC is placeholdered like every other MAC."""
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["devices"] = {
        "site-1": {
            "uuid-child": {
                "name": "Ultra",
                "macAddress": "58:d6:1f:00:00:02",
                "topology": {
                    "legacy_type": "usw",
                    "uplink_mac": "28:70:4e:00:00:01",
                    "uplink_remote_port": 6,
                },
            },
            "uuid-parent": {"name": "Core", "macAddress": "28:70:4e:00:00:01"},
        }
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)
    devices = diagnostics["data"]["devices"]["site-1"]
    uplink_mac = devices["uuid-child"]["topology"]["uplink_mac"]

    assert uplink_mac.startswith("**REDACTED-MAC-")
    # Same MAC, same placeholder: the parent link stays traceable.
    assert uplink_mac == devices["uuid-parent"]["macAddress"]
    assert devices["uuid-child"]["topology"]["uplink_remote_port"] == 6
    assert "28:70:4e:00:00:01" not in _strings(diagnostics)


async def test_diagnostics_placeholders_client_links(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Client link MACs (keys and values) are placeholdered; VLAN stays."""
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["client_links"] = {
        "site-1": {
            "8c:ed:e1:00:00:01": {
                "sw_mac": "28:70:4e:00:00:01",
                "sw_port": 14,
                "vlan": 3,
                "network_name": "Cameras",
            }
        }
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)
    (link_key,) = diagnostics["data"]["client_links"]["site-1"]
    link = diagnostics["data"]["client_links"]["site-1"][link_key]

    assert link_key.startswith("**REDACTED-MAC-")
    assert link["sw_mac"].startswith("**REDACTED-MAC-")
    assert link["vlan"] == 3
    assert link["network_name"] == "Cameras"
    for raw in ("8c:ed:e1:00:00:01", "28:70:4e:00:00:01"):
        assert raw not in _strings(diagnostics)


def test_redact_coordinator_data_keeps_nvr_arm_mode_status() -> None:
    """An NVR record keeps the arm mode worth debugging and loses its identifier."""
    data = {
        "protect": {
            "nvrs": {
                "nvr_1": {
                    "id": "nvr_1",
                    "name": "UniFi Protect",
                    "armMode": {
                        "status": "breach",
                        "armProfileId": "arm_profile_away",
                        "armedAt": 1755916908659,
                        "willBeArmedAt": None,
                        "breachDetectedAt": 1755916999000,
                        "breachEventCount": 2,
                    },
                }
            }
        }
    }

    redacted = _redact_coordinator_data(data)

    nvr = redacted["protect"]["nvrs"]["nvr_1"]
    assert nvr["id"] == REDACTED
    assert nvr["armMode"] == data["protect"]["nvrs"]["nvr_1"]["armMode"]


@pytest.mark.parametrize("enabled", [True, False, None])
async def test_diagnostics_show_whether_the_global_alarm_manager_is_enabled(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
    *,
    enabled: bool | None,
) -> None:
    """The flag that hides the alarm panel is in the report, so it can be checked."""
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["protect"]["global_alarm_manager"] = enabled

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)

    assert diagnostics["data"]["protect"]["global_alarm_manager"] is enabled


async def test_diagnostics_redacts_port_forward_addresses(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations: None,
) -> None:
    """Test diagnostics redacts port forward IP addresses but preserves rule info."""
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["port_forwards"] = {
        "site-1": {
            "pf-plex": port_forward_record(destination_ips=["203.0.113.8"]),
        }
    }

    result = await async_get_config_entry_diagnostics(hass, init_integration)
    dumped = json.dumps(result, default=str)
    assert "192.168.1.50" not in dumped
    assert "198.51.100.0/24" not in dumped
    assert "203.0.113.7" not in dumped
    assert "203.0.113.8" not in dumped
    assert "Plex" in dumped
    assert "32400" in dumped
    assert "tcp_udp" in dumped


async def test_diagnostics_redacts_traffic_rule_targets(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations: None,
) -> None:
    """Test diagnostics redacts traffic rule target domains, IPs and MACs."""
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["traffic_rules"] = {
        "site-1": {
            "tr-bedtime": traffic_rule_record(
                ip_addresses=[
                    {
                        "ip_or_subnet": "203.0.113.9/32",
                        "ip_version": "v4",
                        "port_ranges": [],
                        "ports": [],
                    }
                ]
            ),
        }
    }

    result = await async_get_config_entry_diagnostics(hass, init_integration)
    dumped = json.dumps(result, default=str)
    assert "blocked.example" not in dumped
    assert "192.168.1.100" not in dumped
    assert "203.0.113.9" not in dumped
    assert "aa:bb:cc:dd:ee:01" not in dumped
    assert "Bedtime" in dumped
    assert "BLOCK" in dumped


async def test_diagnostics_redacts_nat_rule_addresses(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations: None,
) -> None:
    """Test diagnostics redacts NAT rule translated and filter addresses."""
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["nat_rules"] = {
        "site-1": {
            "nat-dns": nat_rule_record(),
        }
    }

    result = await async_get_config_entry_diagnostics(hass, init_integration)
    dumped = json.dumps(result, default=str)
    assert "192.0.2.53" not in dumped
    assert "192.0.2.54" not in dumped
    assert "All DNS to resolver" in dumped
    assert "DNAT" in dumped
    assert "tcp_udp" in dumped


async def test_diagnostics_redacts_static_route_targets(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations: None,
) -> None:
    """Test diagnostics redacts static route networks, next hops and gateway MACs."""
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["static_routes"] = {
        "site-1": {
            "route-lab": static_route_record(**{"static-route_nexthop": "192.0.2.1"}),
        }
    }

    result = await async_get_config_entry_diagnostics(hass, init_integration)
    dumped = json.dumps(result, default=str)
    assert "192.168.2.0/24" not in dumped
    assert "192.0.2.1" not in dumped
    assert "aa:bb:cc:dd:ee:ff" not in dumped
    assert "Lab" in dumped
    assert "interface-route" in dumped


async def test_diagnostics_redacts_firewall_policy_filters(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations: None,
) -> None:
    """Test diagnostics redacts the nested address, domain and MAC filters."""
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["firewall_rules"] = {
        "site-1": {
            "f1": {
                "id": "f1",
                "name": "Block cameras",
                "enabled": True,
                "source": {
                    "ipAddressFilter": {"items": [{"value": "10.3.3.3"}]},
                    "macAddressFilter": {"macAddresses": ["aa:bb:cc:dd:ee:01"]},
                },
                "destination": {
                    "domainFilter": {"domains": ["blocked.example"]},
                    "portFilter": {"items": [{"value": 443}]},
                },
            }
        }
    }

    result = await async_get_config_entry_diagnostics(hass, init_integration)
    dumped = json.dumps(result, default=str)
    assert "10.3.3.3" not in dumped
    assert "aa:bb:cc:dd:ee:01" not in dumped
    assert "blocked.example" not in dumped
    assert "Block cameras" in dumped
    assert "443" in dumped


async def test_diagnostics_redacts_policy_based_route_targets(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations: None,
) -> None:
    """Test diagnostics redacts policy-based route domains, ranges and next hop."""
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["policy_based_routes"] = {
        "site-1": {
            "p1": {
                "_id": "p1",
                "description": "VPN",
                "enabled": True,
                "domains": [{"domain": "routed.example"}],
                "ipRanges": [{"ip_start": "10.5.5.1"}],
                "nextHop": "10.4.4.4",
            }
        }
    }

    result = await async_get_config_entry_diagnostics(hass, init_integration)
    dumped = json.dumps(result, default=str)
    assert "routed.example" not in dumped
    assert "10.5.5.1" not in dumped
    assert "10.4.4.4" not in dumped
    assert "VPN" in dumped


async def test_diagnostics_anonymizes_dotted_client_mac(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations: None,
) -> None:
    """Test diagnostics anonymizes dotted client_mac in MAC_KEYS."""
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["traffic_rules"] = {
        "site-1": {
            "tr-1": {
                "_id": "tr-1",
                "target_devices": [{"client_mac": "aabb.ccdd.eeff", "type": "CLIENT"}],
            }
        }
    }

    result = await async_get_config_entry_diagnostics(hass, init_integration)
    dumped = json.dumps(result, default=str)
    assert "aabb.ccdd.eeff" not in dumped
    rule = result["data"]["traffic_rules"]["site-1"]["tr-1"]
    assert rule["target_devices"][0]["client_mac"].startswith("**REDACTED-MAC-")


async def test_diagnostics_keeps_storage_mounts_readable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """Test storage_mounts on console devices remain readable in diagnostics."""
    udm_storage = [
        {
            "mount_point": "/data",
            "name": "eMMC",
            "type": "eMMC",
            "size": 4143677440,
            "used": 1700257792,
        },
        {
            "mount_point": "/persistent",
            "name": "Backup",
            "type": "eMMC",
            "size": 2046640128,
            "used": 318767104,
        },
        {
            "mount_point": "/" + "tmp",
            "name": "Temporary",
            "type": "other",
            "size": 1073741824,
            "used": 1748992,
        },
    ]
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["devices"] = {
        "site-1": {
            "device-1": {
                "id": "device-1",
                "name": "Dream Machine Pro SE",
                "model": "UDMPROSE",
                "macAddress": "AA:BB:CC:77:88:99",
                "storage_mounts": copy.deepcopy(udm_storage),
            }
        }
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)

    device = diagnostics["data"]["devices"]["site-1"]["device-1"]
    assert device["storage_mounts"] == udm_storage


def test_diagnostics_shared_credentials_policy_invariants() -> None:
    """Verify shared credentials subset, aliases, x_* keys, and webhooks."""
    # Shared credentials must be a subset of TO_REDACT
    assert SENSITIVE_CREDENTIAL_KEYS.issubset(TO_REDACT)

    # Token aliases
    for key in ("accessToken", "access_token", "refreshToken", "refresh_token"):
        assert key in TO_REDACT

    # qr_code
    assert "qr_code" in TO_REDACT

    # Classic API x_* keys
    for key in (
        "x_passphrase",
        "x_authkey",
        "x_fingerprint",
        "x_iapp_key",
        "x_ssh_hostkey_fingerprint",
        "x_vwirekey",
    ):
        assert key in TO_REDACT

    # HA config and Access webhook constants
    assert CONF_API_KEY in TO_REDACT
    assert CONF_ACCESS_WEBHOOK_ID in TO_REDACT
    assert CONF_ACCESS_WEBHOOK_ENDPOINT_ID in TO_REDACT
    assert CONF_ACCESS_WEBHOOK_SECRET in TO_REDACT

    # Transport-only keys must NOT be globally in TO_REDACT (code/pin contextual)
    assert "code" not in TO_REDACT
    assert "pin" not in TO_REDACT
    assert "pin_code" not in TO_REDACT
    assert "pin_codes" not in TO_REDACT


async def test_diagnostics_exclude_network_facts(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enable_custom_integrations,
) -> None:
    """
    The frozen read-model records never reach the diagnostics payload.

    The MAC pass cannot walk into dataclasses, and the download encoder falls
    back to repr() for them, which would print raw MACs. The section repeats
    devices and stats, so it is dropped before redaction.
    """
    device_mac = "AA:BB:CC:12:34:56"
    lldp_mac = "aa:bb:cc:65:43:21"
    devices = {
        # A device the API listed without an id is keyed by its MAC.
        device_mac.lower(): {
            **SAMPLE_UDM_PRO_OVERVIEW,
            "id": device_mac.lower(),
            "macAddress": device_mac,
            "ports": [
                {
                    "idx": 1,
                    "name": f"Port to {lldp_mac}",
                    "lldp_table": [{"chassis_id": lldp_mac}],
                }
            ],
        }
    }
    stats = {device_mac.lower(): dict(SAMPLE_UDM_PRO_STATS)}
    facts = {"site-1": build_site_network_facts(devices, stats)}
    coordinator = init_integration.runtime_data.coordinator
    coordinator.data["devices"] = {"site-1": devices}
    coordinator.data["stats"] = {"site-1": stats}
    coordinator.data[NETWORK_FACTS_KEY] = facts
    before = copy.deepcopy({"devices": devices, "stats": stats})

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)

    assert NETWORK_FACTS_KEY not in diagnostics["data"]
    # HA's strict encoder has no repr() fallback, so an opaque record fails it.
    strict = json.dumps(diagnostics, cls=JSONEncoder)
    downloaded = json.dumps(diagnostics, cls=ExtendedJSONEncoder)
    for raw in (device_mac, device_mac.lower(), lldp_mac, lldp_mac.upper()):
        assert raw not in strict
        assert raw not in downloaded
    assert coordinator.data[NETWORK_FACTS_KEY] is facts
    assert {"devices": devices, "stats": stats} == before
    # The raw sections are still there, with anonymised MACs.
    (device,) = diagnostics["data"]["devices"]["site-1"].values()
    assert device["macAddress"] != device_mac
    assert device["name"] == "UDM-Pro"
    assert diagnostics["data"]["stats"]["site-1"]
