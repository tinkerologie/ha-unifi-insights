# Copyright 2026 UniFi Insights contributors
"""
The WebSocket API and dashboard builders read the snapshot through its lookups.

A missing or malformed section, site or record reads as no record (ADR-013).
For a well-formed snapshot the payloads are the ones the builders produced
before they used the lookups.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.dashboard_snapshots import (
    build_internet_activity_snapshot,
    build_site_health_snapshot,
    build_unavailable_site_health,
)
from custom_components.unifi_insights.data_transforms import build_site_network_facts
from custom_components.unifi_insights.topology import (
    build_site_topology,
    opaque_node_id,
    wan_node_id,
)
from custom_components.unifi_insights.websocket_api import (
    ERR_NODE_NOT_FOUND,
    _find_node,
    _ha_device_ids,
    _RequestError,
)
from tests.fixtures.facade_data import facade_data, with_sections, without_sections

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

    from custom_components.unifi_insights.coordinators.facade import FacadeData

SITE = "site1"
GATEWAY = "gw1"
AP = "ap1"
CLIENT = "cli1"
ENTRY_ID = "entry1"
NODE_KEY = b"k" * 32

GATEWAY_RECORD: dict[str, Any] = {
    "id": GATEWAY,
    "name": "Gateway",
    "model": "UDM Pro SE",
    "state": "ONLINE",
    "macAddress": "02:00:00:00:00:01",
    "topology": {"legacy_type": "udm"},
    "wans": [
        {"key": "wan1", "wan_key": "WAN1", "connected": True, "status": "ok"},
        "not a WAN",
    ],
}
AP_RECORD: dict[str, Any] = {
    "id": AP,
    "name": "AP",
    "model": "U7 Pro XGS",
    "state": "OFFLINE",
    "macAddress": "02:00:00:00:00:08",
    "topology": {
        "legacy_type": "uap",
        "uplink_mac": "02:00:00:00:00:01",
        "uplink_type": "wire",
    },
}
CLIENT_RECORD: dict[str, Any] = {
    "id": CLIENT,
    "name": "Phone",
    "type": "WIRELESS",
    "macAddress": "12:00:00:00:00:02",
    "uplinkDeviceId": AP,
}
GATEWAY_STATS: dict[str, Any] = {
    "cpuUtilizationPct": 12.5,
    "memoryUtilizationPct": 42.0,
    "uptimeSec": 3600,
    "uplink": {"txRateBps": 100, "rxRateBps": 50},
}

# section, record ID -> snapshot without a usable record
type Variant = Callable[[FacadeData, str, str], FacadeData]


def _snapshot() -> FacadeData:
    """Return a well-formed one-site snapshot with published network facts."""
    devices = {GATEWAY: GATEWAY_RECORD, AP: AP_RECORD}
    stats = {GATEWAY: GATEWAY_STATS}
    return facade_data(
        sites={SITE: {"id": SITE, "name": "Home"}},
        devices={SITE: devices},
        stats={SITE: stats},
        clients={SITE: {CLIENT: CLIENT_RECORD}},
        internet_activity={
            SITE: {
                "1h": {"rx_bytes": 1000, "tx_bytes": 500},
                "1d": {"rx_bytes": 10_000, "tx_bytes": 5_000},
            }
        },
        network_facts={SITE: build_site_network_facts(devices, stats)},
    )


NO_SECTION = pytest.param(lambda d, s, _r: without_sections(d, s), id="no-section")
MALFORMED_SECTION = pytest.param(
    lambda d, s, _r: with_sections(d, **{s: []}), id="malformed-section"
)
NONE_SECTION = pytest.param(
    lambda d, s, _r: with_sections(d, **{s: None}), id="none-section"
)
NO_SITE = pytest.param(lambda d, s, _r: with_sections(d, **{s: {}}), id="no-site")
MALFORMED_SITE = pytest.param(
    lambda d, s, _r: with_sections(d, **{s: {SITE: []}}), id="malformed-site"
)
NONE_SITE = pytest.param(
    lambda d, s, _r: with_sections(d, **{s: {SITE: None}}), id="none-site"
)
NO_RECORD = pytest.param(
    lambda d, s, _r: with_sections(d, **{s: {SITE: {}}}), id="no-record"
)
MALFORMED_RECORD = pytest.param(
    lambda d, s, r: with_sections(d, **{s: {SITE: {r: "invalid"}}}),
    id="malformed-record",
)
NO_USABLE_RECORD = [
    NO_SECTION,
    MALFORMED_SECTION,
    NONE_SECTION,
    NO_SITE,
    MALFORMED_SITE,
    NONE_SITE,
    NO_RECORD,
    MALFORMED_RECORD,
]


# --- websocket_api._ha_device_ids -------------------------------------------


def _register_gateway(hass: HomeAssistant) -> dr.DeviceEntry:
    """Register the gateway's device under the test entry."""
    MockConfigEntry(domain=DOMAIN, entry_id=ENTRY_ID).add_to_hass(hass)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=ENTRY_ID, identifiers={(DOMAIN, f"{SITE}_{GATEWAY}")}
    )


async def test_ha_device_ids_maps_the_site_devices(hass: HomeAssistant) -> None:
    """Each registered device of the site maps to its registry id."""
    device = _register_gateway(hass)

    assert _ha_device_ids(hass, _snapshot(), SITE, ENTRY_ID) == {GATEWAY: device.id}


@pytest.mark.parametrize("variant", NO_USABLE_RECORD)
async def test_ha_device_ids_without_the_device_maps_nothing(
    hass: HomeAssistant, variant: Variant
) -> None:
    """A missing or malformed section, site or record maps no device."""
    _register_gateway(hass)
    data = variant(_snapshot(), "devices", GATEWAY)

    assert _ha_device_ids(hass, data, SITE, ENTRY_ID) == {}


# --- websocket_api._find_node -----------------------------------------------


def test_find_node_resolves_devices_clients_and_wans() -> None:
    """Device, client and WAN node ids each resolve to their record."""
    data = _snapshot()

    gateway = _find_node(data, SITE, NODE_KEY, opaque_node_id("dev", NODE_KEY, GATEWAY))
    client = _find_node(data, SITE, NODE_KEY, opaque_node_id("cli", NODE_KEY, CLIENT))
    wan = _find_node(data, SITE, NODE_KEY, wan_node_id(NODE_KEY, GATEWAY, "wan1"))

    assert gateway == ("gateway", GATEWAY, GATEWAY_RECORD)
    assert client == ("client", CLIENT, CLIENT_RECORD)
    assert wan == ("wan", GATEWAY, GATEWAY_RECORD["wans"][0])


@pytest.mark.parametrize(
    ("section", "record_id", "node_id"),
    [
        ("devices", GATEWAY, opaque_node_id("dev", NODE_KEY, GATEWAY)),
        ("devices", GATEWAY, wan_node_id(NODE_KEY, GATEWAY, "wan1")),
        ("clients", CLIENT, opaque_node_id("cli", NODE_KEY, CLIENT)),
    ],
    ids=["device", "wan", "client"],
)
@pytest.mark.parametrize("variant", NO_USABLE_RECORD)
def test_find_node_without_the_record_is_not_found(
    variant: Variant, section: str, record_id: str, node_id: str
) -> None:
    """A missing or malformed section, site or record names no node."""
    data = variant(_snapshot(), section, record_id)

    with pytest.raises(_RequestError) as err:
        _find_node(data, SITE, NODE_KEY, node_id)

    assert err.value.code == ERR_NODE_NOT_FOUND


# --- dashboard_snapshots.build_site_health_snapshot -------------------------


def _site_health(data: FacadeData) -> dict[str, Any]:
    """Build the site-health payload of ``data``."""
    return build_site_health_snapshot(
        data,
        entry_id=ENTRY_ID,
        site_id=SITE,
        ha_device_ids={GATEWAY: "dev-gw", AP: "dev-ap"},
        devices_available=True,
    )


def test_site_health_payload_is_unchanged() -> None:
    """A well-formed snapshot builds the same site-health payload as before."""
    assert _site_health(_snapshot()) == SITE_HEALTH_GOLDEN


@pytest.mark.parametrize(
    "variant",
    # The sites section is keyed by site ID only, so the site is the record.
    [NO_SECTION, MALFORMED_SECTION, NONE_SECTION, NO_SITE, MALFORMED_SITE, NONE_SITE],
)
def test_site_health_without_the_site_is_unavailable(variant: Variant) -> None:
    """A missing or malformed sites section or site record is unavailable."""
    data = variant(_snapshot(), "sites", SITE)

    assert _site_health(data) == build_unavailable_site_health(
        ENTRY_ID, SITE, "site_unavailable"
    )


# --- dashboard_snapshots.build_internet_activity_snapshot -------------------


class _EmptyRegistry:
    """An entity registry that holds no rows."""

    def async_get_entity_id(self, *_args: str) -> None:
        return None

    def async_get(self, _entity_id: str) -> None:
        return None


def _internet_activity(data: FacadeData) -> dict[str, Any]:
    """Build the internet-activity payload of ``data``."""
    return build_internet_activity_snapshot(
        data, entry_id=ENTRY_ID, site_id=SITE, entity_registry=_EmptyRegistry()
    )


def test_internet_activity_payload_is_unchanged() -> None:
    """A well-formed snapshot builds the same internet-activity payload as before."""
    assert _internet_activity(_snapshot()) == INTERNET_ACTIVITY_GOLDEN


@pytest.mark.parametrize("variant", NO_USABLE_RECORD)
def test_internet_activity_without_the_gateway_has_no_wan_health(
    variant: Variant,
) -> None:
    """A missing or malformed gateway record leaves throughput and WAN health out."""
    payload = _internet_activity(variant(_snapshot(), "devices", GATEWAY))

    assert payload["throughput"] is None
    assert payload["wan_health"] == {"gateway_online": False, "links": []}
    assert payload["windows"] == INTERNET_ACTIVITY_GOLDEN["windows"]
    assert payload["status"] == "ok"


# --- topology.build_site_topology -------------------------------------------


def _topology(data: FacadeData) -> dict[str, Any]:
    """Build the topology payload of ``data``."""
    return dict(
        build_site_topology(
            data,
            ENTRY_ID,
            SITE,
            node_key=NODE_KEY,
            ha_device_ids={GATEWAY: "dev-gw"},
        )
    )


def test_topology_payload_is_unchanged() -> None:
    """A well-formed snapshot builds the same topology payload as before."""
    assert _topology(_snapshot()) == TOPOLOGY_GOLDEN


def test_topology_site_without_devices_is_not_unavailable() -> None:
    """A site that holds no devices is loaded, not unavailable."""
    data = with_sections(_snapshot(), devices={SITE: {}})

    codes = [issue["code"] for issue in _topology(data)["issues"]]

    assert "devices_unavailable" not in codes


@pytest.mark.parametrize(
    "variant",
    [NO_SECTION, MALFORMED_SECTION, NONE_SECTION, NO_SITE, MALFORMED_SITE, NONE_SITE],
)
def test_topology_without_the_site_devices_is_unavailable(variant: Variant) -> None:
    """A missing or malformed devices section or site reports devices_unavailable."""
    payload = _topology(variant(_snapshot(), "devices", GATEWAY))

    assert {"code": "devices_unavailable", "severity": "error"} in payload["issues"]
    assert payload["status"] == "unavailable"


# --- Payloads the builders produced before the lookups ----------------------

SITE_HEALTH_GOLDEN: dict[str, Any] = {
    "version": 1,
    "entry_id": "entry1",
    "site_id": "site1",
    "site_name": "Home",
    "status": "ok",
    "issues": [],
    "health": {"level": "degraded", "reasons": ["devices_offline"]},
    "gateway": {
        "present": True,
        "name": "Gateway",
        "state": "online",
        "uptime_s": 3600,
        "ha_device_id": "dev-gw",
        "internet": "online",
        "wan_links": [{"name": "WAN1", "state": "up"}],
    },
    "devices": {
        "gateway": {"online": 1, "offline": 0, "unknown": 0},
        "switch": {"online": 0, "offline": 0, "unknown": 0},
        "access_point": {"online": 0, "offline": 1, "unknown": 0},
        "other": {"online": 0, "offline": 0, "unknown": 0},
    },
    "attention_devices": [
        {
            "id": "ap1",
            "name": "AP",
            "kind": "access_point",
            "state": "offline",
            "ha_device_id": "dev-ap",
        }
    ],
    "attention_omitted": 0,
    "clients": {"total": 1, "wired": 0, "wireless": 1},
    "freshness": "fresh",
    "revision": "c32c8fb51f775da5",
}

INTERNET_ACTIVITY_GOLDEN: dict[str, Any] = {
    "version": 1,
    "entry_id": "entry1",
    "site_id": "site1",
    "site_name": "Home",
    "status": "ok",
    "issues": [],
    "windows": {
        "1h": {
            "download_bytes": 1000,
            "upload_bytes": 500,
            "report_interval": "1h",
            "available": True,
            "series": [],
            "coverage": None,
            "partial": False,
            "peak": None,
        },
        "1d": {
            "download_bytes": 10000,
            "upload_bytes": 5000,
            "report_interval": "1d",
            "available": True,
            "series": [],
            "coverage": None,
            "partial": False,
            "peak": None,
        },
    },
    "entity_ids": {},
    "throughput": {"tx_bps": 100, "rx_bps": 50, "source": "gateway_uplink"},
    "wan_health": {
        "gateway_online": True,
        "links": [{"wan_key": "WAN1", "connected": True, "status": "ok"}],
    },
    "revision": "63aa06cf0e0c3455",
}

TOPOLOGY_GOLDEN: dict[str, Any] = {
    "schema_version": 2,
    "entry_id": "entry1",
    "site_id": "site1",
    "site_name": "Home",
    "revision": "003f8fb7efe02518",
    "status": "ok",
    "issues": [],
    "nodes": [
        {
            "id": "wan:gw1:wan1",
            "kind": "wan",
            "name": "WAN1",
            "state": "online",
            "ha_device_id": "dev-gw",
        },
        {
            "id": "dev:gw1",
            "kind": "gateway",
            "name": "Gateway",
            "state": "online",
            "model": "UDM Pro SE",
            "ha_device_id": "dev-gw",
        },
        {
            "id": "dev:ap1",
            "kind": "access_point",
            "name": "AP",
            "state": "offline",
            "model": "U7 Pro XGS",
        },
        {
            "id": "cli:cli1",
            "kind": "client",
            "name": "Phone",
            "state": "online",
            "connection": "wireless",
        },
    ],
    "edges": [
        {"source": "cli:cli1", "target": "dev:ap1", "medium": "wireless"},
        {"source": "dev:ap1", "target": "dev:gw1", "medium": "wired"},
        {"source": "dev:gw1", "target": "wan:gw1:wan1", "medium": "unknown"},
    ],
    "unresolved": [],
    "truncation": None,
}
