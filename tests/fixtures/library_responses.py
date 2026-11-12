# Copyright 2026 UniFi Insights contributors
"""Sample library response data for testing."""

import copy
from typing import Any

from unifi_official_api.network.models.device import Device
from unifi_official_api.network.models.stats import (
    StatisticsSource,
    StatsSeries,
    StatsSeriesBucket,
)

# Network API responses
SAMPLE_SITE = {
    "id": "default",
    "name": "Default",
    "desc": "Default Site",
}

SAMPLE_CLIENT = {
    "id": "client_1",
    "mac": "11:22:33:44:55:66",
    "name": "Test Device",
    "ip": "192.168.1.100",
    "site": "default",
}

SAMPLE_WIRED_CLIENT = {
    "id": "client_wired_1",
    "macAddress": "aa:bb:cc:dd:ee:33",
    "name": "Test Wired Client",
    "type": "WIRED",
    "connectedAt": "2026-10-25T12:00:00Z",
    "ipAddress": "192.168.1.133",
}

SAMPLE_PDU_DEVICE = {
    "id": "pdu_device_1",
    "mac": "00:11:22:33:44:55",
    "model": "USP-PDU-Pro",
    "name": "Server Room PDU",
    "status": "online",
    "state": "ONLINE",
    "adopted": True,
    "firmware_version": "6.6.65",
    "site": "default",
    "site_id": "default",
}

SAMPLE_PDU_LEGACY_DEVICE = {
    "_id": "60a1b2c3d4e5f67890123456",
    "mac": "00:11:22:33:44:55",
    "model": "USP-PDU-Pro",
    "name": "Server Room PDU",
    "outlet_ac_power_consumption": "245.50",
    "outlet_ac_power_budget": "1800.00",
    "outlet_table": [
        {
            "index": 1,
            "name": "Main Server",
            "relay_state": True,
            "cycle_enabled": True,
            "outlet_caps": 3,
            "outlet_voltage": "120.2",
            "outlet_current": "1.25",
            "outlet_power": "150.0",
            "outlet_power_factor": "0.98",
        },
        {
            "index": 2,
            "name": "Backup Server",
            "relay_state": False,
            "cycle_enabled": False,
            "outlet_caps": 3,
            "outlet_voltage": "120.1",
            "outlet_current": "0.00",
            "outlet_power": "0.0",
            "outlet_power_factor": "0.00",
        },
        {
            "index": 3,
            "name": "Unmetered Switch",
            "relay_state": True,
            "cycle_enabled": None,
            "outlet_caps": 1,
            "outlet_voltage": None,
            "outlet_current": None,
            "outlet_power": None,
            "outlet_power_factor": None,
        },
    ],
    "outlet_overrides": [
        {
            "index": 1,
            "name": "Main Server",
            "relay_state": True,
            "cycle_enabled": True,
        },
    ],
}


# --- Complete PDU outlet data for the #376 payload-integrity tests ---
#
# ``SAMPLE_PDU_LEGACY_DEVICE`` is deliberately sparse (one override for three
# table rows) and stays the cold-start rejection case. The variants below give
# the coordinator a structurally complete baseline. They are constructed, not
# captures: the 21-overrides/20-table shape mirrors the live diagnostics that
# showed the two arrays can differ, a difference a length or count rule cannot
# judge.
def _pdu_outlet_rows(count: int) -> list[dict[str, Any]]:
    """Return ``count`` complete outlet_overrides rows with mixed values."""
    return [
        {
            "index": index,
            "name": f"Outlet {index}",
            "relay_state": index % 2 == 1,
            "cycle_enabled": index % 2 == 1,
        }
        for index in range(1, count + 1)
    ]


_COMPLETE_OUTLET_TABLE = [
    {
        "index": 1,
        "name": "Main Server",
        "relay_state": True,
        "cycle_enabled": True,
        "outlet_caps": 3,
        "outlet_voltage": "120.2",
        "outlet_current": "1.25",
        "outlet_power": "150.0",
        "outlet_power_factor": "0.98",
    },
    {
        "index": 2,
        "name": "Backup Server",
        "relay_state": False,
        "cycle_enabled": True,
        "outlet_caps": 3,
        "outlet_voltage": "120.1",
        "outlet_current": "0.00",
        "outlet_power": "0.0",
        "outlet_power_factor": "0.00",
    },
    {
        "index": 3,
        "name": "Unmetered Switch",
        "relay_state": True,
        "cycle_enabled": False,
        "outlet_caps": 1,
        "outlet_voltage": None,
        "outlet_current": None,
        "outlet_power": None,
        "outlet_power_factor": None,
    },
]

_COMPLETE_OUTLET_OVERRIDES = [
    {
        "index": 1,
        "name": "Main Server",
        "relay_state": True,
        "cycle_enabled": True,
    },
    {
        "index": 2,
        "name": "Backup Server",
        "relay_state": False,
        "cycle_enabled": True,
    },
    {
        "index": 3,
        "name": "Unmetered Switch",
        "relay_state": True,
        "cycle_enabled": False,
    },
]

SAMPLE_PDU_LEGACY_DEVICE_COMPLETE = {
    **SAMPLE_PDU_LEGACY_DEVICE,
    "outlet_table": copy.deepcopy(_COMPLETE_OUTLET_TABLE),
    "outlet_overrides": copy.deepcopy(_COMPLETE_OUTLET_OVERRIDES),
}

SAMPLE_PDU_LEGACY_DEVICE_SUPERSET = {
    **SAMPLE_PDU_LEGACY_DEVICE,
    "outlet_table": [{**row, "cycle_enabled": True} for row in _pdu_outlet_rows(20)],
    "outlet_overrides": _pdu_outlet_rows(21),
}

# --- Network device read model (#337) ---
#
# Constructed (not a capture): a switch in the compatibility snake_case shape
# (``mac``, ``status``, ``firmware_version``, ``site_id``) that older code
# paths and hand-written test data use.
SAMPLE_NETWORK_DEVICE = {
    "id": "network_device_1",
    "mac": "00:11:22:33:44:66",
    "model": "USW Pro 24 PoE",
    "name": "Core Switch",
    "status": "online",
    "adopted": True,
    "features": ["switching"],
    "firmware_version": "7.1.26",
    "site": "default",
    "site_id": "default",
}

# Captured (redacted) from the maintainer's UDM Pro through the official
# Network API (GET /v1/sites/{site}/devices, 2026-10-19). It reports no v1
# ``type``, only ``features: ["switching"]`` and a list-shaped ``interfaces``.
# Only the id, MAC, IP and name are replaced with placeholders. This is the raw
# v1 overview item; SAMPLE_UDM_PRO_OVERVIEW is what the coordinator stores
# from it (``model_dump(by_alias=True)``, which adds ``type: None`` and an
# empty ``ports``).
SAMPLE_UDM_PRO_OVERVIEW_API = {
    "id": "00000000-0000-4000-8000-0000000000d1",
    "macAddress": "aa:bb:cc:00:00:d1",
    "ipAddress": "192.168.1.1",
    "name": "UDM-Pro",
    "model": "UDM Pro",
    "state": "ONLINE",
    "supported": True,
    "firmwareVersion": "5.1.33",
    "firmwareUpdatable": False,
    "features": ["switching"],
    "interfaces": ["ports"],
}
SAMPLE_UDM_PRO_OVERVIEW = Device.model_validate(SAMPLE_UDM_PRO_OVERVIEW_API).model_dump(
    by_alias=True
)

# Captured (redacted) from the same UDM Pro (GET
# /v1/sites/{site}/devices/{id}/statistics/latest, 2026-10-19). The record has
# no identifiers, so it is verbatim. ``uplink`` rates are bits/s (#337).
SAMPLE_UDM_PRO_STATS = {
    "uptimeSec": 3333412,
    "lastHeartbeatAt": "2026-10-19T06:10:15Z",
    "nextHeartbeatAt": "2026-10-19T06:10:39Z",
    "loadAverage1Min": 1.18,
    "loadAverage5Min": 1.3,
    "loadAverage15Min": 1.4,
    "cpuUtilizationPct": 14.6,
    "memoryUtilizationPct": 62.5,
    "uplink": {"txRateBps": 35072, "rxRateBps": 74016},
    "interfaces": {},
}


# Protect API responses
SAMPLE_USL_ENTRY_SENSOR = {
    "id": "test_sensor_usl",
    "name": "Front Door Sensor",
    "model": "USL-Entry",
    "state": "CONNECTED",
    "mountType": "door",
    "isOpened": False,
    "isTamperingDetected": False,
    "batteryStatus": {"percentage": 92, "isLow": False},
    "temperature": None,
    "humidity": None,
    "lightValue": None,
}

# A doorbell camera and a chime shaped like the public Integration API returns
# them (spec: chime keys id, modelKey, state, name, type, guid, mac, cameraIds,
# ringSettings). The ids, MACs and guid are fakes.
SAMPLE_DOORBELL_CAMERA = {
    "id": "doorbell_cam_1",
    "modelKey": "camera",
    "mac": "AABBCC0000D1",
    "name": "Front Doorbell",
    "type": "UVC G4 Doorbell Pro",
    "state": "CONNECTED",
}

SAMPLE_PUBLIC_CHIME = {
    "id": "chime_1",
    "modelKey": "chime",
    "state": "CONNECTED",
    "name": "Hallway Chime",
    "type": "UP-Chime",
    "guid": "00000000-0000-4000-8000-0000000000c1",
    "mac": "AABBCC0000C1",
    "cameraIds": [],
    "ringSettings": [],
}

SAMPLE_NVR = {
    "id": "nvr_1",
    "name": "UniFi Protect",
    "model": "UNVR",
    "version": "3.0.22",
}

# Redacted capture of a real /nvrs object on Protect 7.3.70 (2026-10-06) with
# the global alarm manager on. The id, MAC, guid, name and armProfileId are
# fakes; every other value is as the console reported it.
SAMPLE_NVR_WITH_ARM_MODE = {
    "id": "nvr_1",
    "modelKey": "nvr",
    "name": "UniFi Protect",
    "type": "UNVR-PRO",
    "guid": "00000000-0000-4000-8000-0000000000aa",
    "mac": "AABBCC0000AA",
    "doorbellSettings": {
        "defaultMessageText": "WELCOME",
        "defaultMessageResetTimeoutMs": 60000,
        "customMessages": [],
        "customImages": [],
    },
    "armMode": {
        "status": "disabled",
        "armProfileId": "arm_profile_away",
        "armedAt": 1755916908659,
        "willBeArmedAt": None,
        "breachDetectedAt": None,
        "breachEventCount": 0,
        "breachTriggerEventId": None,
        "breachEventId": None,
    },
}

# Redacted capture of a real /link-stations item on Protect 7.3.70
# (2026-10-05). A SuperLink sends no "threadState" key at all, although the
# spec marks it required.
SAMPLE_LINK_STATION = {
    "id": "link_station_1",
    "modelKey": "linkstation",
    "state": "CONNECTED",
    "name": "SuperLink",
    "type": "UP-SuperLink-US",
    "guid": "00000000-0000-0000-0000-000000000001",
    "mac": "AABBCC000001",
    "isAlarmHub": False,
    "ledSettings": {"isEnabled": True},
    "lastEvent": None,
}

# The rest are built from the Protect 7.3.70 OpenAPI schemas: no console used
# for these captures had a Thread gateway, an alarm hub or a fob.
SAMPLE_THREAD_LINK_STATION = {
    "id": "link_station_thread",
    "modelKey": "linkstation",
    "state": "CONNECTED",
    "name": "Thread Gateway",
    "type": "UP-SuperLink-US",
    "mac": "AABBCC000002",
    "isAlarmHub": False,
    "ledSettings": {"isEnabled": True},
    "lastEvent": 1759628000000,
    "threadState": {
        "network": {
            "status": "ready",
            "role": "leader",
            "networkName": "Home Thread",
            "channel": 15,
            "panId": "1a2b",
            "extendedPanId": "0011223344556677",
            "joinedDeviceCount": 4,
            "errorReason": None,
            "lastUpdatedAt": 1759628000000,
        }
    },
}

SAMPLE_ALARM_HUB = {
    "id": "alarm_hub_1",
    "modelKey": "linkstation",
    "state": "CONNECTED",
    "name": "Alarm Hub",
    "type": "UP-AlarmHub",
    "mac": "AABBCC000003",
    "isAlarmHub": True,
    "ledSettings": {"isEnabled": True},
    "lastEvent": None,
    "alarmHub": {
        "armed": "off",
        "battery": {"charging": "off", "batteryStatus": "ok", "voltage": 13.1},
        "deviceTamperStatus": "restored",
    },
    "threadState": {"network": None},
}

SAMPLE_KEYPAD_FOB = {
    "id": "fob_1",
    "modelKey": "fob",
    "state": "CONNECTED",
    "name": "Hallway Keypad",
    "type": "UP-Keypad",
    "mac": "AABBCC000004",
    "awayState": "ONLINE",
    "buttonLabels": "securityActions",
    "featureFlags": {"buttons": ["arm", "disarm", "night"], "hasKeypad": True},
    "armControlSettings": {
        "enabled": True,
        "armProfileId": "arm_profile_away",
        "nightProfileId": None,
    },
    "keypadSettings": {"beepEnabled": True, "beepVolume": 60},
    "wirelessConnectionState": {
        "signalState": "good",
        "batteryStatus": {"percentage": 90, "isLow": False},
        "bridge": "link_station_1",
    },
}

# Redacted capture of a live UP-Siren-PoE (Protect 7.3.70). The id, MAC and guid
# are fakes; every other value is as the console reported it.
SAMPLE_SPEAKER = {
    "id": "speaker_1",
    "modelKey": "speaker",
    "state": "CONNECTED",
    "name": "Yard Horn",
    "type": "UP-Speaker",
    "mac": "AABBCC000006",
    "volume": 80,
    "micVolume": 100,
    "isMicEnabled": True,
}

SAMPLE_EXTERNAL_ARM_PROFILE = {
    "id": "profile_1",
    "title": "Perimeter",
    "state": "armed",
    "state_set_at": "2026-04-10T12:00:00Z",
    "state_promotion_due_at": "2026-04-10T12:01:00Z",
    "created_at": "2026-04-01T10:00:00Z",
    "created_by": "user_1",
    "updated_at": "2026-04-10T12:00:00Z",
    "updated_by": "user_1",
    "activation_delay": 60,
    "deactivation_delay": 30,
    "active_breach_id": None,
    "alarm_ids": ["alarm_1", "alarm_2"],
    "has_pin": True,
    "schedule": None,
}

SAMPLE_EXTERNAL_ARM_PROFILE_2 = {
    "id": "profile_2",
    "title": "Interior",
    "state": "disarmed",
    "state_set_at": "2026-04-10T12:00:00Z",
    "state_promotion_due_at": None,
    "created_at": "2026-04-01T10:00:00Z",
    "created_by": "user_1",
    "updated_at": "2026-04-10T12:00:00Z",
    "updated_by": "user_1",
    "activation_delay": 0,
    "deactivation_delay": 0,
    "active_breach_id": None,
    "alarm_ids": ["alarm_3"],
    "has_pin": False,
    "schedule": None,
}

SAMPLE_SIREN = {
    "id": "siren_1",
    "modelKey": "siren",
    "state": "CONNECTED",
    "name": "Garage Siren",
    "type": "UP-Siren-PoE",
    "guid": "00000000-0000-4000-8000-000000000005",
    "mac": "AABBCC000005",
    "volume": 50,
    "ledSettings": {"isEnabled": True},
    "sirenStatus": {"isActive": False, "activatedAt": None, "duration": None},
    "connectionType": "ucp4",
    "wirelessConnectionState": {
        "signalState": {"signalQuality": None, "signalStrength": None},
        "batteryStatus": {"percentage": None, "isLow": False},
        "bridge": None,
    },
}

SAMPLE_ALARM_HUB_TAMPER_EVENT = {
    "id": "event_tamper_1",
    "modelKey": "event",
    "type": "alarmHubDeviceTamper",
    "start": 1759628100000,
    "end": None,
    "device": "alarm_hub_1",
    "metadata": {
        "status": {"text": "tampered"},
        "deviceId": {"text": "alarm_hub_1"},
        "deviceName": {"text": "Alarm Hub"},
        "userName": "Installer",
    },
}

SAMPLE_INNERSPACE_PROJECT = {
    "project": {
        "id": "project_1",
        "name": "Main Floor",
    },
    "plans": [],
    "products": [],
}

SAMPLE_SITE_REPORT_RESPONSE = {
    "meta": {"rc": "ok"},
    "data": [
        {
            "time": 1790373600000,
            "wan-rx_bytes": 1.2628779867391305e8,
            "wan-tx_bytes": 5991446.652173913,
            "wan2-rx_bytes": 0.0,
            "wan2-tx_bytes": 0.0,
            "site": "600ee7a7c6aeaa055108e337",
            "o": "site",
            "oid": "600ee7a7c6aeaa055108e337",
        },
        {
            "time": 1790373900000,
            "wan-rx_bytes": 9.552575601358695e7,
            "wan-tx_bytes": 4120000.0,
            "wan2-rx_bytes": 1000000.0,
            "wan2-tx_bytes": 500000.0,
            "site": "600ee7a7c6aeaa055108e337",
            "o": "site",
            "oid": "600ee7a7c6aeaa055108e337",
        },
    ],
}


# Mock exception responses
class MockUniFiAuthError(Exception):
    """Mock authentication error."""


class MockUniFiConnectionError(Exception):
    """Mock connection error."""


class MockUniFiTimeoutError(Exception):
    """Mock timeout error."""


class MockUniFiAPIError(Exception):
    """Mock API error."""


# Statistics engine fixtures
SAMPLE_STATS_SOURCE_MODERN = StatisticsSource("MODERN")
SAMPLE_STATS_SOURCE_LEGACY = StatisticsSource("LEGACY")

SAMPLE_INTEGRATION_SITE_1 = {
    "id": "88f7af54-1111-2222-3333-444455556666",
    "name": "Primary Site",
    "desc": "Primary Site Desc",
    "internalReference": "default",
}
SAMPLE_LEGACY_SITE_RECORD_1 = {
    "_id": "ab632056-1111-2222-3333-444455556666",
    "external_id": "88f7af54-1111-2222-3333-444455556666",
    "name": "default",
    "desc": "Primary Site Desc",
    "legacy_id": "600ee7a7c6aeaa055108e111",
}
SAMPLE_STATS_SERIES_SITE_1 = StatsSeries(
    siteId="ab632056-1111-2222-3333-444455556666",
    series=[
        StatsSeriesBucket(
            timestamp=1790373600000,
            duration=300000,
            rxBytes=1000.0,
            txBytes=2000.0,
        ),
        StatsSeriesBucket(
            timestamp=1790373900000,
            duration=270216,
            rxBytes=500.0,
            txBytes=1000.0,
        ),
    ],
)

SAMPLE_INTEGRATION_SITE_2 = {
    "id": "99f8ba65-7777-8888-9999-000011112222",
    "name": "Secondary Site",
    "desc": "Secondary Site Desc",
    "internalReference": "site_two",
}
SAMPLE_LEGACY_SITE_RECORD_2 = {
    "_id": "cd743167-7777-8888-9999-000011112222",
    "external_id": "99f8ba65-7777-8888-9999-000011112222",
    "name": "site_two",
    "desc": "Secondary Site Desc",
    "legacy_id": "600ee7a7c6aeaa055108e222",
}
SAMPLE_STATS_SERIES_SITE_2 = StatsSeries(
    siteId="cd743167-7777-8888-9999-000011112222",
    series=[
        StatsSeriesBucket(
            timestamp=1790373600000,
            duration=300000,
            rxBytes=3000.0,
            txBytes=4000.0,
        ),
    ],
)
