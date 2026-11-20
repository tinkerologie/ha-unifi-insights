# Copyright 2026 UniFi Insights contributors
"""Tests for the strict grouped device inventory (issue #519)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.unifi_insights import device_grouping
from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.device_grouping import (
    DEVICE_GROUPS_SCHEMA_VERSION,
    _group_key,
    build_device_groups,
    build_terminal_device_groups,
    safe_name,
)
from tests.fixtures.facade_data import facade_data, with_device_fields, with_sections

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.unifi_insights.device_grouping import (
        DeviceGroupRow,
        DeviceGroups,
    )

NODE_KEY = b"test-key"
SITE = "site-a"
SITE_B = "site-b"
GW_MAC = "02:00:00:00:00:01"
SW_MAC = "02:00:00:00:00:02"
SW2_MAC = "02:00:00:00:00:03"
AP_MAC = "02:00:00:00:00:04"
CAM_MAC = "02:00:00:00:00:a1"
GW_IP = "10.0.0.1"


def _device(
    name: str, mac: str, legacy_type: str, parent_mac: str | None = None, **block: Any
) -> dict[str, Any]:
    """Return a Network device record with a classic parent link."""
    topology: dict[str, Any] = {"legacy_type": legacy_type}
    if parent_mac is not None:
        topology |= {"uplink_mac": parent_mac, "uplink_source": "current"}
    topology |= block
    return {
        "id": name,
        "name": name,
        "model": "Model",
        "macAddress": mac,
        "ipAddress": GW_IP,
        "state": "ONLINE",
        "topology": topology,
    }


def _chain() -> dict[str, dict[str, Any]]:
    """UDM Pro -> switch (port 5) -> AP (port 7)."""
    return {
        "gw": _device("UDM Pro", GW_MAC, "udm"),
        "sw": _device("Office switch", SW_MAC, "usw", GW_MAC, uplink_remote_port=5),
        "ap": _device("Garage AP", AP_MAC, "uap", SW_MAC, uplink_remote_port=7),
    }


def _data(
    devices: dict[str, dict[str, Any]] | None = None,
    *,
    clients: dict[str, dict[str, Any]] | None = None,
    links: dict[str, Any] | None = None,
    cameras: dict[str, dict[str, Any]] | None = None,
    extra_sites: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> Any:
    """Build a frozen facade snapshot for site A (and optional other sites)."""
    devices_by_site = {SITE: devices if devices is not None else _chain()}
    clients_by_site = {SITE: clients or {}}
    for site_id, records in (extra_sites or {}).items():
        devices_by_site[site_id] = records
        clients_by_site.setdefault(site_id, {})
    return facade_data(
        sites={
            SITE: {"id": SITE, "name": "Home"},
            SITE_B: {"id": SITE_B, "name": "Barn"},
        },
        devices=devices_by_site,
        clients=clients_by_site,
        client_links={SITE: links or {}},
        protect={"cameras": cameras or {}},
    )


class Rows:
    """Registry rows for one config entry, created through the real registries."""

    def __init__(self, hass: HomeAssistant, entry_id: str = "entry-1") -> None:
        self.hass = hass
        self.entry = MockConfigEntry(domain=DOMAIN, entry_id=entry_id)
        self.entry.add_to_hass(hass)

    def device(self, identifier: str, name: str, **kwargs: Any) -> dr.DeviceEntry:
        """Register a device with one DOMAIN identifier."""
        return dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={(DOMAIN, identifier)},
            name=name,
            **kwargs,
        )

    def network(self, device_id: str, name: str, site: str = SITE) -> dr.DeviceEntry:
        """Register a Network device row, named the way entity.py names it."""
        return self.device(f"{site}_{device_id}", f"{name} ({GW_IP})")

    def camera(self, camera_id: str, device: dr.DeviceEntry) -> er.RegistryEntry:
        """Register the camera entity that identifies a camera's device."""
        return er.async_get(self.hass).async_get_or_create(
            "camera",
            DOMAIN,
            f"{DOMAIN}_camera_{camera_id}",
            config_entry=self.entry,
            device_id=device.id,
        )

    def build(
        self,
        data: Any,
        *,
        site_ids: tuple[str, ...] = (SITE,),
        devices_available: bool = True,
    ) -> DeviceGroups:
        """Build the payload from this entry's registry rows."""
        return build_device_groups(
            data,
            entry_id=self.entry.entry_id,
            node_key=NODE_KEY,
            site_ids=site_ids,
            devices_available=devices_available,
            device_rows=dr.async_entries_for_config_entry(
                dr.async_get(self.hass), self.entry.entry_id
            ),
            entity_rows=er.async_entries_for_config_entry(
                er.async_get(self.hass), self.entry.entry_id
            ),
        )


@pytest.fixture
def rows(hass: HomeAssistant) -> Rows:
    """Registry rows of the entry under test."""
    return Rows(hass)


def _section(payload: DeviceGroups, key: str) -> list[DeviceGroupRow]:
    """Return one section's rows."""
    (section,) = [s for s in payload["sections"] if s["key"] == key]
    return section["rows"]


def _by_key(
    payload: DeviceGroups, key: str = _group_key("site", NODE_KEY, SITE)
) -> dict[str, Any]:
    """Return a section's rows by key."""
    return {row["key"]: row for row in _section(payload, key)}


def _net(device_id: str, site: str = SITE) -> str:
    return _group_key("net", NODE_KEY, site, device_id)


async def test_gateway_switch_ap_chain_with_camera_and_other_device(
    rows: Rows,
) -> None:
    """The full acceptance tree: gateway > switch > AP, camera, other device."""
    gw = rows.network("gw", "UDM Pro")
    sw = rows.network("sw", "Office switch")
    camera_device = rows.device("protect_camera_cam1", "Front door")
    rows.camera("cam1", camera_device)
    site_device = rows.device(f"site_{SITE}", "UniFi Site (Home)")
    data = _data(
        clients={
            "c1": {
                "id": "c1",
                "type": "WIRED",
                "macAddress": CAM_MAC.upper(),
                "uplinkDeviceId": "sw",
            }
        },
        links={CAM_MAC: {"sw_mac": SW_MAC, "sw_port": 3}},
        cameras={"cam1": {"id": "cam1", "name": "Front door", "mac": CAM_MAC}},
    )

    payload = rows.build(data)

    assert payload["schema_version"] == DEVICE_GROUPS_SCHEMA_VERSION
    assert payload["status"] == "ok"
    assert payload["stale"] is False
    assert [s["key"] for s in payload["sections"]] == [
        _group_key("site", NODE_KEY, SITE),
        "unassigned",
        "other",
    ]
    assert _section(payload, _group_key("site", NODE_KEY, SITE)) == [
        {
            "key": _net("gw"),
            "name": "UDM Pro",
            "kind": "gateway",
            "ha_device_id": gw.id,
            "parent_key": None,
            "port": None,
            "reason": None,
        },
        {
            "key": _group_key("cam", NODE_KEY, "cam1"),
            "name": "Front door",
            "kind": "camera",
            "ha_device_id": camera_device.id,
            "parent_key": _net("sw"),
            "port": 3,
            "reason": None,
        },
        {
            "key": _net("ap"),
            "name": "Garage AP",
            "kind": "access_point",
            "ha_device_id": None,
            "parent_key": _net("sw"),
            "port": 7,
            "reason": None,
        },
        {
            "key": _net("sw"),
            "name": "Office switch",
            "kind": "switch",
            "ha_device_id": sw.id,
            "parent_key": _net("gw"),
            "port": 5,
            "reason": None,
        },
    ]
    assert _section(payload, "other") == [
        {
            "key": f"dev:{site_device.id}",
            "name": "UniFi Site (Home)",
            "kind": "other",
            "ha_device_id": site_device.id,
            "parent_key": None,
            "port": None,
            "reason": None,
        }
    ]
    assert _section(payload, "unassigned") == []


async def test_payload_has_no_ip_or_mac(rows: Rows) -> None:
    """Registry names end in the IP; neither addresses nor MACs are emitted."""
    rows.network("gw", "UDM Pro")
    rows.device("client_aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff")
    rows.device("other", "Box 192.168.1.20 fe80::1:2 [AA-BB-CC-DD-EE-01]")
    payload = rows.build(_data())

    text = json.dumps(payload).lower()
    for needle in (GW_IP, GW_MAC, SW_MAC, AP_MAC, "aa:bb", "192.168", "fe80", "aa-bb"):
        assert needle not in text
    names = {row["name"] for row in _section(payload, "other")}
    assert names == {"Unknown device", "Box"}


async def test_address_shaped_source_ids_and_labels_are_private(rows: Rows) -> None:
    """Legacy MAC keys and literal site titles never reach the payload."""
    chain = _chain()
    rows.network(GW_MAC, "Gateway", GW_IP)
    rows.network(SW_MAC, "Switch", GW_IP)
    data = facade_data(
        sites={GW_IP: {"id": GW_IP, "name": GW_IP}},
        devices={
            GW_IP: {
                GW_MAC: {**chain["gw"], "id": GW_MAC},
                SW_MAC: {
                    **chain["sw"],
                    "id": SW_MAC,
                    "name": "Office switch [0200.0000.0002]",
                },
            },
        },
        protect={
            "cameras": {
                CAM_MAC: {"name": "Doorbell 0200000000a1", "mac": CAM_MAC},
            },
        },
    )
    payload = rows.build(data, site_ids=(GW_IP,))

    text = json.dumps(payload).lower()
    for address in (GW_IP, GW_MAC, SW_MAC, CAM_MAC, "0200.0000.0002", "0200000000a1"):
        assert address not in text
    site = payload["sections"][0]
    assert site["title"] == "Unknown site"
    gateway, switch = site["rows"]
    assert gateway["name"] == "UDM Pro"
    assert switch["name"] == "Office switch"
    assert switch["parent_key"] == gateway["key"]
    assert rows.build(data, site_ids=(GW_IP,)) == payload


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Switch (10.0.0.2)", "Switch"),
        ("Cam 02:00:00:00:00:a1", "Cam"),
        ("AP [2001:db8::1]", "AP"),
        ("AP [::ffff:192.0.2.4]", "AP"),
        ("AP [fe80::1%eth0]", "AP"),
        ("Console_AABB.CCDD.EEFF", "Console_"),
        ("10.0.0.2", "fallback"),
        (None, "fallback"),
        ("  Plain name  ", "Plain name"),
        ("Rack 2 - 1:1", "Rack 2 - 1:1"),
        # Colon-separated words and times are not IPv6 addresses.
        ("Office::Cam", "Office::Cam"),
        ("Doorbell 10:30:15", "Doorbell 10:30:15"),
        ("Beef:Cafe:Deck", "Beef:Cafe:Deck"),
    ],
)
def test_safe_name(value: Any, expected: str) -> None:
    """Address-shaped text is removed; other text is kept."""
    assert safe_name(value, "fallback") == expected


async def test_devices_wired_directly_to_the_gateway(rows: Rows) -> None:
    """Switch and AP both on the gateway nest one level under it."""
    devices = {
        "gw": _device("Gateway", GW_MAC, "udm"),
        "sw": _device("Switch", SW_MAC, "usw", GW_MAC, uplink_remote_port=1),
        "ap": _device("AP", AP_MAC, "uap", GW_MAC, uplink_remote_port=2),
    }
    site = _by_key(rows.build(_data(devices)))

    assert site[_net("sw")]["parent_key"] == _net("gw")
    assert site[_net("ap")]["parent_key"] == _net("gw")
    assert (site[_net("sw")]["port"], site[_net("ap")]["port"]) == (1, 2)


async def test_cascaded_switches(rows: Rows) -> None:
    """Each switch nests under the one above it, with no port when unknown."""
    devices = {
        "gw": _device("Gateway", GW_MAC, "udm"),
        "sw1": _device("Core", SW_MAC, "usw", GW_MAC),
        "sw2": _device("Edge", SW2_MAC, "usw", SW_MAC, uplink_remote_port=24),
    }
    site = _by_key(rows.build(_data(devices)))

    assert site[_net("sw1")] | {"name": ""} == {
        "key": _net("sw1"),
        "name": "",
        "kind": "switch",
        "ha_device_id": None,
        "parent_key": _net("gw"),
        "port": None,
        "reason": None,
    }
    assert site[_net("sw2")]["parent_key"] == _net("sw1")
    assert site[_net("sw2")]["port"] == 24


async def test_gateway_uplink_is_ignored(rows: Rows) -> None:
    """A gateway is always a root, even when its LLDP uplink names a switch."""
    devices = {
        "gw": _device("Gateway", GW_MAC, "udm", SW_MAC),
        "sw": _device("Switch", SW_MAC, "usw", GW_MAC),
    }
    site = _by_key(rows.build(_data(devices)))

    assert site[_net("gw")]["parent_key"] is None
    assert site[_net("gw")]["reason"] is None
    assert site[_net("sw")]["parent_key"] == _net("gw")


@pytest.mark.parametrize(
    ("block", "reason"),
    [
        ({}, "no_uplink"),
        ({"uplink_mac": "not-a-mac", "uplink_source": "current"}, "no_uplink"),
        ({"uplink_mac": GW_MAC, "uplink_source": "last"}, "historical"),
        ({"uplink_mac": GW_MAC}, "historical"),
        (
            {"uplink_mac": "02:00:00:00:00:99", "uplink_source": "current"},
            "parent_not_found",
        ),
        ({"uplink_mac": SW_MAC, "uplink_source": "current"}, "parent_not_found"),
    ],
    ids=["no_link", "bad_mac", "last_uplink", "unmarked", "unknown_parent", "self"],
)
async def test_unresolved_device_stays_top_level_with_reason(
    rows: Rows, block: dict[str, Any], reason: str
) -> None:
    """Missing, historical, unknown and self links are not grouped."""
    devices = {
        "gw": _device("Gateway", GW_MAC, "udm"),
        "sw": _device("Switch", SW_MAC, "usw")
        | {"topology": {"legacy_type": "usw", **block}},
    }
    row = _by_key(rows.build(_data(devices)))[_net("sw")]

    assert (row["parent_key"], row["port"], row["reason"]) == (None, None, reason)


async def test_non_dict_topology_block_has_no_uplink(rows: Rows) -> None:
    """A malformed block reads as no link; a device without a MAC is still listed."""
    devices = {
        "gw": _device("Gateway", GW_MAC, "udm"),
        "x": {"id": "x", "name": "Thing", "topology": "bad"},
    }
    row = _by_key(rows.build(_data(devices)))[_net("x")]

    assert (row["kind"], row["reason"]) == ("network_device", "no_uplink")


@pytest.mark.parametrize("dup_first", [True, False])
async def test_duplicate_parent_macs_are_ambiguous_in_either_order(
    rows: Rows, *, dup_first: bool
) -> None:
    """Two devices with the parent's MAC make the edge ambiguous, whatever the order."""
    gw = _device("Gateway", GW_MAC, "udm")
    twin = _device("Twin", GW_MAC, "usw")
    pair = {"twin": twin, "gw": gw} if dup_first else {"gw": gw, "twin": twin}
    devices = {**pair, "ap": _device("AP", AP_MAC, "uap", GW_MAC)}
    row = _by_key(rows.build(_data(devices)))[_net("ap")]

    assert (row["parent_key"], row["reason"]) == (None, "ambiguous")


async def test_mutual_cycle_drops_every_edge_in_the_cycle(rows: Rows) -> None:
    """Two switches naming each other both go top level; a child of one stays."""
    devices = {
        "sw1": _device("Switch 1", SW_MAC, "usw", SW2_MAC, uplink_remote_port=1),
        "sw2": _device("Switch 2", SW2_MAC, "usw", SW_MAC, uplink_remote_port=2),
        "ap": _device("AP", AP_MAC, "uap", SW_MAC, uplink_remote_port=3),
    }
    site = _by_key(rows.build(_data(devices)))

    for key in (_net("sw1"), _net("sw2")):
        assert (site[key]["parent_key"], site[key]["port"], site[key]["reason"]) == (
            None,
            None,
            "cycle",
        )
    assert site[_net("ap")]["parent_key"] == _net("sw1")
    assert site[_net("ap")]["reason"] is None


async def test_three_device_cycle_reached_from_a_tail(rows: Rows) -> None:
    """A walk entering a cycle from outside marks only the cycle members."""
    mac = "02:00:00:00:00:0{}".format
    devices = {
        "tail": _device("Tail", mac(5), "usw", mac(6)),
        "a": _device("A", mac(6), "usw", mac(7)),
        "b": _device("B", mac(7), "usw", mac(8)),
        "c": _device("C", mac(8), "usw", mac(6)),
    }
    site = _by_key(rows.build(_data(devices)))

    assert {key for key, row in site.items() if row["reason"] == "cycle"} == {
        _net("a"),
        _net("b"),
        _net("c"),
    }
    assert site[_net("tail")]["parent_key"] == _net("a")


async def test_parent_in_another_site_is_not_found(rows: Rows) -> None:
    """Edges never cross sites; each selected site gets its own section."""
    data = _data(
        {"ap": _device("AP", AP_MAC, "uap", GW_MAC)},
        extra_sites={SITE_B: {"gw": _device("Gateway", GW_MAC, "udm")}},
    )
    payload = rows.build(data, site_ids=(SITE, SITE_B))

    # Sorted by title: "Barn" before "Home".
    assert [s["title"] for s in payload["sections"][:2]] == ["Barn", "Home"]
    assert _by_key(payload)[_net("ap")]["reason"] == "parent_not_found"
    assert _net("gw", SITE_B) in _by_key(payload, _group_key("site", NODE_KEY, SITE_B))


async def test_rows_of_other_entries_are_ignored(
    hass: HomeAssistant, rows: Rows
) -> None:
    """A foreign entry's device and camera entity rows never resolve."""
    foreign = Rows(hass, "entry-2")
    foreign.network("gw", "Foreign gateway")
    foreign_camera = foreign.device("protect_camera_cam1", "Foreign cam")
    foreign.camera("cam1", foreign_camera)
    data = _data(
        cameras={"cam1": {"id": "cam1", "name": "Cam", "mac": CAM_MAC}},
    )
    payload = build_device_groups(
        data,
        entry_id=rows.entry.entry_id,
        node_key=NODE_KEY,
        site_ids=(SITE,),
        devices_available=True,
        # Passing every row proves ownership is checked, not assumed.
        device_rows=list(dr.async_get(hass).devices),
        entity_rows=list(er.async_get(hass).entities.values()),
    )

    assert _by_key(payload)[_net("gw")]["ha_device_id"] is None
    (camera,) = _section(payload, "unassigned")
    assert camera["ha_device_id"] is None
    assert _section(payload, "other") == []


async def test_camera_entity_on_foreign_device_row_is_not_used(
    hass: HomeAssistant, rows: Rows
) -> None:
    """An owned camera entity whose device row is another entry's is skipped."""
    foreign = Rows(hass, "entry-2")
    foreign_device = foreign.device("protect_camera_cam1", "Foreign")
    rows.camera("cam1", foreign_device)
    payload = rows.build(_data(cameras={"cam1": {"id": "cam1", "mac": CAM_MAC}}))

    (camera,) = _section(payload, "unassigned")
    assert (camera["ha_device_id"], camera["name"]) == (None, "Unknown camera")


async def test_unavailable_device_coordinator_marks_rows_stale(rows: Rows) -> None:
    """With stale Network data no edge is trusted; gateways need no reason."""
    data = _data(
        clients={"c1": {"id": "c1", "macAddress": CAM_MAC, "uplinkDeviceId": "sw"}},
        cameras={"cam1": {"id": "cam1", "name": "Cam", "mac": CAM_MAC}},
    )
    payload = rows.build(data, devices_available=False)
    site = _by_key(payload)

    assert payload["stale"] is True
    assert site[_net("gw")]["reason"] is None
    for key in (_net("sw"), _net("ap"), _group_key("cam", NODE_KEY, "cam1")):
        assert (site[key]["parent_key"], site[key]["reason"]) == (None, "stale")


async def test_client_only_camera_appears_once_under_its_switch(rows: Rows) -> None:
    """The matched client is not a row of its own; its client device is the camera."""
    camera_device = rows.device("protect_camera_cam1", "Porch")
    rows.camera("cam1", camera_device)
    client_device = rows.device(f"client_{CAM_MAC}", "Porch client")
    data = _data(
        clients={
            "c1": {
                "id": "c1",
                "type": "WIRED",
                "macAddress": CAM_MAC,
                "uplinkDeviceId": "sw",
            },
            # VPN and Teleport clients have no MAC; they are skipped, not indexed.
            "vpn": {"id": "vpn", "type": "VPN", "macAddress": None},
        },
        links={CAM_MAC: {"sw_mac": SW_MAC, "sw_port": 9}},
        cameras={"cam1": {"id": "cam1", "name": "Porch", "mac": CAM_MAC}},
    )
    payload = rows.build(data)

    cameras = [
        row for s in payload["sections"] for row in s["rows"] if row["kind"] == "camera"
    ]
    assert len(cameras) == 1
    assert (cameras[0]["parent_key"], cameras[0]["port"]) == (_net("sw"), 9)
    all_ids = {row["ha_device_id"] for s in payload["sections"] for row in s["rows"]}
    assert client_device.id not in all_ids
    assert '"c1"' not in json.dumps(payload)


async def test_camera_without_classic_link_is_grouped_without_port(rows: Rows) -> None:
    """The v1 parent alone groups the camera; only the classic link adds a port."""
    data = _data(
        clients={"c1": {"id": "c1", "macAddress": CAM_MAC, "uplink_device_id": "ap"}},
        links={CAM_MAC: "garbage"},
        cameras={"cam1": {"id": "cam1", "name": "Cam", "macAddress": CAM_MAC}},
    )
    row = _by_key(rows.build(data))[_group_key("cam", NODE_KEY, "cam1")]

    assert (row["parent_key"], row["port"], row["reason"]) == (_net("ap"), None, None)


async def test_wireless_camera_uses_the_access_point_link_and_no_port(
    rows: Rows,
) -> None:
    """A wireless camera's classic link is its AP; a port is never shown."""
    data = _data(
        clients={
            "c1": {
                "id": "c1",
                "type": "WIRELESS",
                "macAddress": CAM_MAC,
                "uplinkDeviceId": "ap",
            }
        },
        links={CAM_MAC: {"ap_mac": AP_MAC, "sw_mac": SW_MAC, "sw_port": 4}},
        cameras={"cam1": {"id": "cam1", "name": "Cam", "mac": CAM_MAC}},
    )
    row = _by_key(rows.build(data))[_group_key("cam", NODE_KEY, "cam1")]

    assert (row["parent_key"], row["port"], row["reason"]) == (_net("ap"), None, None)


async def test_classic_link_conflicting_with_v1_parent_is_ambiguous(
    rows: Rows,
) -> None:
    """A classic switch other than the v1 parent vetoes the edge."""
    data = _data(
        clients={
            "c1": {
                "id": "c1",
                "type": "WIRED",
                "macAddress": CAM_MAC,
                "uplinkDeviceId": "ap",
            }
        },
        links={CAM_MAC: {"sw_mac": SW_MAC, "sw_port": 4}},
        cameras={"cam1": {"id": "cam1", "name": "Cam", "mac": CAM_MAC}},
    )
    row = _by_key(rows.build(data))[_group_key("cam", NODE_KEY, "cam1")]

    assert (row["parent_key"], row["port"], row["reason"]) == (None, None, "ambiguous")


@pytest.mark.parametrize(
    ("client", "reason"),
    [
        ({"id": "c1", "macAddress": CAM_MAC}, "no_uplink"),
        ({"id": "c1", "macAddress": CAM_MAC, "uplinkDeviceId": ""}, "no_uplink"),
        (
            {"id": "c1", "macAddress": CAM_MAC, "uplinkDeviceId": "gone"},
            "parent_not_found",
        ),
    ],
)
async def test_camera_client_without_usable_uplink(
    rows: Rows, client: dict[str, Any], reason: str
) -> None:
    """A camera whose client has no resolvable v1 parent stays top level in its site."""
    data = _data(
        clients={"c1": client},
        cameras={"cam1": {"id": "cam1", "name": "Cam", "mac": CAM_MAC}},
    )
    row = _by_key(rows.build(data))[_group_key("cam", NODE_KEY, "cam1")]

    assert (row["parent_key"], row["reason"]) == (None, reason)


@pytest.mark.parametrize(
    ("camera", "clients_by_site", "reason"),
    [
        ({"id": "cam1", "name": "Cam"}, {}, "no_uplink"),
        ({"id": "cam1", "name": "Cam", "mac": CAM_MAC}, {}, "no_uplink"),
        (
            {"id": "cam1", "name": "Cam", "mac": CAM_MAC},
            {SITE: {"c1": {"macAddress": CAM_MAC}}, SITE_B: {"c2": {"mac": CAM_MAC}}},
            "ambiguous",
        ),
    ],
    ids=["no_mac", "no_client", "two_sites"],
)
async def test_camera_without_a_unique_site_is_unassigned(
    rows: Rows,
    camera: dict[str, Any],
    clients_by_site: dict[str, Any],
    reason: str,
) -> None:
    """A camera whose MAC matches no client, or clients in two sites, is unassigned."""
    data = facade_data(
        sites={
            SITE: {"id": SITE, "name": "Home"},
            SITE_B: {"id": SITE_B, "name": "Barn"},
        },
        devices={SITE: _chain(), SITE_B: {}},
        clients=clients_by_site,
        protect={"cameras": {"cam1": camera}},
    )
    payload = rows.build(data, site_ids=(SITE, SITE_B))

    (row,) = _section(payload, "unassigned")
    assert (row["key"], row["kind"], row["parent_key"], row["reason"]) == (
        _group_key("cam", NODE_KEY, "cam1"),
        "camera",
        None,
        reason,
    )


async def test_camera_merged_with_network_device_is_listed_once(rows: Rows) -> None:
    """A camera whose device row is the Network row is that row, not a second one."""
    switch_row = rows.network("sw", "Office switch")
    rows.camera("cam1", switch_row)
    payload = rows.build(
        _data(cameras={"cam1": {"id": "cam1", "name": "Doorbell", "mac": CAM_MAC}})
    )

    assert _group_key("cam", NODE_KEY, "cam1") not in _by_key(payload)
    assert _section(payload, "unassigned") == []
    assert _by_key(payload)[_net("sw")]["ha_device_id"] == switch_row.id


async def test_camera_without_owned_row_does_not_merge_by_mac(rows: Rows) -> None:
    """A MAC match by itself does not prove that Network represents the camera."""
    payload = rows.build(
        _data(cameras={"cam1": {"id": "cam1", "name": "NVR cam", "mac": SW_MAC}})
    )

    (camera,) = _section(payload, "unassigned")
    assert camera["kind"] == "camera"
    assert camera["ha_device_id"] is None
    assert _by_key(payload)[_net("sw")]["kind"] == "switch"


async def test_camera_keeps_verified_uplink_with_same_network_mac_in_another_site(
    rows: Rows,
) -> None:
    """A camera only merges if its entity points at a represented device row."""
    network = rows.network("duplicate", "Network device", SITE_B)
    camera = rows.device("protect_camera_cam1", "Doorbell")
    rows.camera("cam1", camera)
    data = _data(
        clients={"c1": {"macAddress": CAM_MAC, "uplinkDeviceId": "sw"}},
        cameras={"cam1": {"name": "Doorbell", "mac": CAM_MAC}},
        extra_sites={SITE_B: {"duplicate": _device("Network device", CAM_MAC, "usw")}},
    )

    payload = rows.build(data, site_ids=(SITE, SITE_B))
    site = next(s for s in payload["sections"] if s["title"] == "Home")
    (camera_row,) = [row for row in site["rows"] if row["kind"] == "camera"]
    switch = next(row for row in site["rows"] if row["kind"] == "switch")
    assert (camera_row["ha_device_id"], camera_row["parent_key"]) == (
        camera.id,
        switch["key"],
    )
    barn = next(s for s in payload["sections"] if s["title"] == "Barn")
    assert [row["ha_device_id"] for row in barn["rows"]] == [network.id]
    assert not _section(payload, "other")


async def test_camera_client_index_scans_each_sites_clients_once(rows: Rows) -> None:
    """Normalization of each client is shared across all camera lookups."""
    data = _data(
        clients={"c1": {"macAddress": "0200000000A1", "uplinkDeviceId": "sw"}},
        cameras={
            "cam1": {"name": "Doorbell", "mac": CAM_MAC},
            "cam2": {"name": "Garage camera", "mac": "02:00:00:00:00:a2"},
        },
        extra_sites={SITE_B: {"gw": _device("Barn gateway", GW_MAC, "udm")}},
    )
    data = with_sections(
        data,
        clients={
            **data["clients"],
            SITE_B: {"c2": {"mac": "02-00-00-00-00-A2", "uplinkDeviceId": "gw"}},
        },
    )
    with patch.object(
        device_grouping, "get_field", wraps=device_grouping.get_field
    ) as get_field:
        payload = rows.build(data, site_ids=(SITE, SITE_B))

    by_title = {section["title"]: section["rows"] for section in payload["sections"]}
    for title, name, parent in (
        ("Home", "Doorbell", "Office switch"),
        ("Barn", "Garage camera", "Barn gateway"),
    ):
        site = {row["name"]: row for row in by_title[title]}
        assert site[name]["parent_key"] == site[parent]["key"]
    assert (
        sum(call.args[1:] == ("macAddress", "mac") for call in get_field.call_args_list)
        == 2
    )


async def test_dual_camera_children_collapse_into_one_row(rows: Rows) -> None:
    """Both lenses of a dual camera share one device row and one list row."""
    camera_device = rows.device("protect_camera_dual", "Doorbell")
    rows.camera("dual_package", camera_device)
    data = _data(
        clients={"c1": {"id": "c1", "macAddress": CAM_MAC, "uplinkDeviceId": "sw"}},
        cameras={
            "dual_package": {
                "id": "dual_package",
                "name": "Doorbell Package Camera",
                "_parent_camera_id": "dual",
            },
            "dual_main": {
                "id": "dual_main",
                "name": "Doorbell Main Camera",
                "mac": CAM_MAC,
                "_parent_camera_id": "dual",
            },
        },
    )
    payload = rows.build(data)
    cameras = [
        row
        for row in _section(payload, _group_key("site", NODE_KEY, SITE))
        if row["kind"] == "camera"
    ]

    assert cameras == [
        {
            "key": _group_key("cam", NODE_KEY, "dual"),
            "name": "Doorbell",
            "kind": "camera",
            "ha_device_id": camera_device.id,
            "parent_key": _net("sw"),
            "port": None,
            "reason": None,
        }
    ]
    assert _section(payload, "other") == []


async def test_dual_camera_name_comes_from_the_parent_record(rows: Rows) -> None:
    """A parent record's name wins; a child name loses only its lens suffix."""
    data = _data(
        cameras={
            "dual": {"id": "dual", "name": "Gate"},
            "dual_main": {
                "id": "dual_main",
                "name": "X Main Camera",
                "_parent_camera_id": "dual",
            },
        }
    )
    (row,) = _section(rows.build(data), "unassigned")

    assert row["name"] == "Gate"


async def test_user_names_win_and_rows_sort_gateways_first(rows: Rows) -> None:
    """name_by_user is shown; gateways lead, then names case-insensitively."""
    rows.network("ap", "Garage AP")
    dr.async_get(rows.hass).async_update_device(
        rows.network("sw", "Office switch").id, name_by_user="aardvark switch"
    )
    payload = rows.build(_data())

    assert [
        row["name"] for row in _section(payload, _group_key("site", NODE_KEY, SITE))
    ] == [
        "UDM Pro",
        "aardvark switch",
        "Garage AP",
    ]


async def test_uplink_move_across_sequential_snapshots(rows: Rows) -> None:
    """Moving the AP from the switch to the gateway re-parents it, new revision."""
    data = _data()
    before = rows.build(data)
    moved = with_device_fields(
        data,
        SITE,
        "ap",
        topology={
            "legacy_type": "uap",
            "uplink_mac": GW_MAC,
            "uplink_source": "current",
            "uplink_remote_port": 2,
        },
    )
    after = rows.build(moved)

    assert _by_key(before)[_net("ap")]["parent_key"] == _net("sw")
    assert (
        _by_key(after)[_net("ap")]["parent_key"],
        _by_key(after)[_net("ap")]["port"],
    ) == (
        _net("gw"),
        2,
    )
    assert before["revision"] != after["revision"]
    assert rows.build(moved)["revision"] == after["revision"]


async def test_missing_sections_build_empty_sections(rows: Rows) -> None:
    """A snapshot without Network or Protect data still lists every section."""
    payload = rows.build(facade_data(), site_ids=(SITE,))

    assert [(s["key"], s["title"], s["rows"]) for s in payload["sections"]] == [
        (_group_key("site", NODE_KEY, SITE), SITE, []),
        ("unassigned", None, []),
        ("other", None, []),
    ]


def test_terminal_payload() -> None:
    """A stream ends with an empty payload carrying the reason."""
    assert build_terminal_device_groups("e1", "grouping_disabled") == {
        "schema_version": 1,
        "entry_id": "e1",
        "status": "grouping_disabled",
        "revision": "",
        "stale": False,
        "sections": [],
    }
