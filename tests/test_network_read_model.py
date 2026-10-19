# Copyright 2026 UniFi Insights contributors
"""Table-driven tests for the network device read model (#337)."""

from __future__ import annotations

import ast
import copy
import dataclasses
import logging
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
from unifi_official_api.network.models.client import ClientType
from unifi_official_api.network.models.device import DeviceState

from custom_components.unifi_insights import entity, topology
from custom_components.unifi_insights.console_identity import (
    normalize_mac as console_normalize_mac,
)
from custom_components.unifi_insights.coordinators.device import (
    UnifiInsightsDeviceCoordinator,
)
from custom_components.unifi_insights.coordinators.protect import (
    UnifiInsightsProtectCoordinator,
)
from custom_components.unifi_insights.dashboard_snapshots import bits_per_second_to_int
from custom_components.unifi_insights.data_transforms import (
    NETWORK_DEVICE_STATES,
    NETWORK_FACTS_KEY,
    NetworkDeviceFacts,
    NetworkPortFacts,
    SiteNetworkFacts,
    _field,
    as_bits_per_second,
    build_site_network_facts,
    bytes_to_bits_per_second,
    client_connection_type,
    device_facts,
    device_kind,
    device_online_state,
    first_not_none,
    get_field,
    normalize_mac,
    port_index,
    site_network_facts,
    uplink_rate_bps,
)
from custom_components.unifi_insights.innerspace_transforms import (
    _normalize_innerspace_mac,
)
from custom_components.unifi_insights.sensor import bytes_to_bits
from custom_components.unifi_insights.service_targets import _mac_key
from tests.fixtures.library_responses import (
    SAMPLE_NETWORK_DEVICE,
    SAMPLE_PDU_DEVICE,
    SAMPLE_PDU_LEGACY_DEVICE,
    SAMPLE_UDM_PRO_OVERVIEW,
    SAMPLE_UDM_PRO_STATS,
)

# --- Constructed fixtures derived from the captured records ----------

# Constructed legacy port_table rows (not a capture), normalised through the
# coordinator's own legacy port normaliser so the shape is the stored one.
_LEGACY_PORT_TABLE: list[dict[str, Any]] = [
    {"port_idx": 1, "up": True, "speed": 1000, "media": "GE", "tx_bytes": 10},
    {"port_idx": 9, "up": True, "speed": 10000, "media": "SFP+", "is_uplink": True},
    {"port_idx": 11, "up": False, "media": "SFP+", "sfp_found": False},
]
_LEGACY_PORTS = [
    port
    for row in _LEGACY_PORT_TABLE
    if (port := UnifiInsightsDeviceCoordinator._normalize_legacy_port(row)) is not None
]

# Constructed: the captured overview after a successful legacy merge
# (the live console reported legacy_type "udm" and port_table-backed ports).
UDM_PRO_LEGACY_ENRICHED: dict[str, Any] = {
    **SAMPLE_UDM_PRO_OVERVIEW,
    "ports": _LEGACY_PORTS,
    "topology": {"legacy_type": "udm"},
}

# Constructed: a legacy-only device, as the v1-5xx fallback maps it.
LEGACY_ONLY_SWITCH = UnifiInsightsDeviceCoordinator._legacy_device_to_v1_dict(
    {
        "_id": "60a1b2c3d4e5f67890123456",
        "mac": "AA:BB:CC:00:00:E1",
        "model": "US24P250",
        "name": "Legacy Switch",
        "type": "usw",
        "up": True,
        "port_table": [{"port_idx": 2, "up": True, "speed": 1000}],
    }
)

# Constructed: explicit zeros everywhere a metric is read.
ZERO_STATS: dict[str, Any] = {
    "cpuUtilizationPct": 0,
    "memoryUtilizationPct": 0,
    "uptimeSec": 0,
    "uplink": {"txRateBps": 0, "rxRateBps": 0},
}

# Constructed: snake-case bits/s keys and the explicit bytes/s fallbacks.
SNAKE_AND_BYTES_STATS: dict[str, Any] = {
    "cpu_utilization_pct": 7.5,
    "memory_percent": 40,
    "uptime_seconds": 3600,
    "uplink": {"tx_rate_bps": 100},
    "rx_bytes_per_sec": 50,
}

# Constructed: malformed values.
MALFORMED_STATS: dict[str, Any] = {
    "cpuUtilizationPct": True,
    "memoryUtilizationPct": "bad",
    "uptimeSec": float("nan"),
    "uplink": {"txRateBps": "bad", "rxRateBps": float("nan")},
}


# --- Shared primitives ----------------------------------------------------


# ADR-011: data_transforms may import Home Assistant's pure utilities and the
# pure integration modules only, so coordinators and builders can use it
# without an import cycle.
_TRANSFORMS_ALLOWED_INTEGRATION_IMPORTS = frozenset({"const", "innerspace_transforms"})


def _transforms_imports() -> list[tuple[str, int]]:
    """Return (module, level) for every import in data_transforms.py."""
    path = (
        Path(__file__).parents[1]
        / "custom_components/unifi_insights/data_transforms.py"
    )
    found: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            found.append((node.module or "", node.level))
        elif isinstance(node, ast.Import):
            found.extend((alias.name, 0) for alias in node.names)
    return found


def test_data_transforms_imports_only_pure_modules() -> None:
    """Home Assistant imports are limited to homeassistant.util.*."""
    for module, level in _transforms_imports():
        if level:
            assert level == 1, module
            assert module in _TRANSFORMS_ALLOWED_INTEGRATION_IMPORTS, module
        elif module.startswith("homeassistant"):
            assert module.startswith("homeassistant.util"), module
        else:
            assert not module.startswith("custom_components"), module


@pytest.mark.parametrize(
    ("data", "keys", "kwargs", "expected"),
    [
        ({"a": 0, "b": 1}, ("a", "b"), {}, 0),
        ({"a": False, "b": True}, ("a", "b"), {}, False),
        ({"a": "", "b": "x"}, ("a", "b"), {}, ""),
        ({"a": None, "b": 2}, ("a", "b"), {}, 2),
        ({}, ("a", "b"), {}, None),
        ({}, ("a",), {"default": "fallback"}, "fallback"),
        ({"a": None}, ("a",), {"default": 5}, 5),
    ],
)
def test_get_field(
    data: dict[str, Any], keys: tuple[str, ...], kwargs: dict[str, Any], expected: Any
) -> None:
    """get_field returns the first non-None value and keeps falsy readings."""
    assert get_field(data, *keys, **kwargs) == expected


def test_first_not_none() -> None:
    """first_not_none keeps 0 and False and falls back to the default."""
    assert first_not_none(None, 0, 5) == 0
    flag = False
    assert first_not_none(None, flag) is False
    assert first_not_none(None, None) is None
    assert first_not_none(default="x") == "x"


def test_field_helpers_delegate_to_one_implementation() -> None:
    """The old helper names are thin wrappers over data_transforms.get_field."""
    assert entity.get_field is get_field
    assert entity.first_not_none is first_not_none
    assert topology.normalize_mac is normalize_mac
    record = {"a": None, "b": 0}
    assert topology.first_present(record, "a", "b") == 0
    assert _field(record, "a", "b") == 0
    assert _field(record, "a") is None
    assert UnifiInsightsProtectCoordinator._get_field(record, "a", default=3) == 3
    assert UnifiInsightsProtectCoordinator._get_field(record, "a", "b") == 0


@pytest.mark.parametrize(
    ("value", "bits", "from_bytes"),
    [
        (0, 0.0, 0.0),
        (100, 100.0, 800.0),
        (12.5, 12.5, 100.0),
        ("100", 100.0, 800.0),
        (None, None, None),
        (True, None, None),
        (False, None, None),
        ("bad", None, None),
        ([], None, None),
        (float("nan"), None, None),
        (float("inf"), None, None),
        (float("-inf"), None, None),
        ("nan", None, None),
        (10**400, None, None),
        (1e308, 1e308, None),
    ],
)
def test_numeric_primitives(value: Any, bits: Any, from_bytes: Any) -> None:
    """Both rate primitives keep zero and reject flags, junk and non-finite."""
    assert as_bits_per_second(value) == bits
    assert bytes_to_bits_per_second(value) == from_bytes


def test_converters_delegate_to_the_shared_primitive() -> None:
    """The sensor and dashboard converters use the validated primitive."""
    assert bytes_to_bits(12) == 96
    assert bytes_to_bits(None) is None
    assert bits_per_second_to_int(bytes_to_bits_per_second(12)) == 96
    assert isinstance(bits_per_second_to_int(12.5), int)
    assert bits_per_second_to_int(bytes_to_bits_per_second(float("nan"))) is None


# Each column pins one MAC policy: canonical (topology HMAC input), console
# identity, legacy key match, InnerSpace correlation and service input.
_MAC_POLICIES = (
    normalize_mac,
    console_normalize_mac,
    UnifiInsightsDeviceCoordinator._normalize_mac,
    _normalize_innerspace_mac,
    _mac_key,
)
_CANONICAL = "aa:bb:cc:dd:ee:ff"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(
            "AABBCCDDEEFF",
            (_CANONICAL, "aabbccddeeff", "aabbccddeeff", _CANONICAL, "aabbccddeeff"),
            id="bare",
        ),
        pytest.param(
            " AA:BB:CC:DD:EE:FF ",
            (_CANONICAL, " aa:bb:cc:dd:ee:ff ", _CANONICAL, _CANONICAL, "aabbccddeeff"),
            id="whitespace",
        ),
        pytest.param(
            "aabb.ccdd.eeff",
            (None, "aabb.ccdd.eeff", "aabb.ccdd.eeff", _CANONICAL, "aabbccddeeff"),
            id="dotted",
        ),
        pytest.param(
            "aa-bb:cc-dd:ee-ff",
            (_CANONICAL, _CANONICAL, "aa-bb:cc-dd:ee-ff", _CANONICAL, "aabbccddeeff"),
            id="mixed-separators",
        ),
        pytest.param(
            ":aa:bb:cc:dd:ee:ff:",
            (
                None,
                ":aa:bb:cc:dd:ee:ff:",
                ":aa:bb:cc:dd:ee:ff:",
                _CANONICAL,
                "aabbccddeeff",
            ),
            id="leading-trailing-separators",
        ),
        pytest.param(
            "zz:bb:cc:dd:ee:ff",
            (None, "zz:bb:cc:dd:ee:ff", "zz:bb:cc:dd:ee:ff", None, "zzbbccddeeff"),
            id="malformed",
        ),
        pytest.param("   ", (None, "   ", "", None, None), id="whitespace-only"),
        pytest.param("", (None, None, None, None, None), id="empty"),
        pytest.param(None, (None, None, None, None, None), id="none"),
    ],
)
def test_mac_policies_are_pinned(value: Any, expected: tuple[Any, ...]) -> None:
    """The canonical parser and the four identity policies keep their outputs."""
    assert tuple(policy(value) for policy in _MAC_POLICIES) == expected


# --- Online state ---------------------------------------------------------


def test_spec_state_tuple_matches_the_network_api() -> None:
    """The local tuple maps exactly the spec's ten network device states."""
    assert dict(NETWORK_DEVICE_STATES) == {
        "ONLINE": "online",
        "OFFLINE": "offline",
        "CONNECTION_INTERRUPTED": "offline",
        "ISOLATED": "offline",
        "U5G_INCORRECT_TOPOLOGY": "offline",
        "PENDING_ADOPTION": "unknown",
        "UPDATING": "unknown",
        "GETTING_READY": "unknown",
        "ADOPTING": "unknown",
        "DELETING": "unknown",
    }
    assert len(NETWORK_DEVICE_STATES) == len(dict(NETWORK_DEVICE_STATES))


@pytest.mark.parametrize(("state", "expected"), NETWORK_DEVICE_STATES)
def test_every_spec_state_is_mapped(state: str, expected: str) -> None:
    """Every spec state resolves through the tuple, in state and status."""
    assert device_online_state({"state": state}) == expected
    assert device_online_state({"status": state.lower()}) == expected


# The library's DeviceState values when this test was written. A library bump
# that adds or renames one fails here, so the new value gets a decision. The
# library spells the spec's ADOPTING in lower case.
_KNOWN_LIBRARY_STATES = frozenset(
    {
        *(state for state, _ in NETWORK_DEVICE_STATES if state != "ADOPTING"),
        "connected",
        "disconnected",
        "pending",
        "adopting",
        "provisioning",
        "upgrading",
        "unknown",
    }
)


def test_library_device_states_all_resolve() -> None:
    """Every library DeviceState member resolves, and none is new."""
    assert {member.value for member in DeviceState} == _KNOWN_LIBRARY_STATES
    spec = dict(NETWORK_DEVICE_STATES)
    for member in DeviceState:
        resolved = device_online_state({"state": member})
        assert resolved in {"online", "offline", "unknown"}
        assert resolved == spec.get(member.value.upper(), "unknown")


class _PlainState(Enum):
    """A non-str enum, as a decoded value that is not a StrEnum."""

    ONLINE = "ONLINE"
    BROKEN = 1


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        ({"state": "CONNECTED"}, "unknown"),
        ({"state": "UP"}, "unknown"),
        ({"state": "connected"}, "unknown"),
        ({"state": "up"}, "unknown"),
        ({"state": "DISCONNECTED"}, "unknown"),
        ({"state": "DOWN"}, "unknown"),
        ({}, "unknown"),
        ({"state": None}, "unknown"),
        ({"state": None, "status": "ONLINE"}, "online"),
        ({"state": "nonsense"}, "unknown"),
        ({"state": 1}, "unknown"),
        ({"state": ""}, "unknown"),
        ({"state": "online"}, "online"),
        ({"state": "  OFFLINE \n"}, "offline"),
        ({"state": DeviceState.ISOLATED}, "offline"),
        ({"state": _PlainState.ONLINE}, "online"),
        ({"state": _PlainState.BROKEN}, "unknown"),
        ({"status": "offline"}, "offline"),
        ({"state": "ONLINE", "status": "offline"}, "online"),
    ],
)
def test_online_state_edge_inputs(device: dict[str, Any], expected: str) -> None:
    """Non-spec, absent, invalid, cased, padded and enum inputs."""
    assert device_online_state(device) == expected


# --- Kind -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        pytest.param(SAMPLE_UDM_PRO_OVERVIEW, "gateway", id="null-type-udm-pro"),
        pytest.param(UDM_PRO_LEGACY_ENRICHED, "gateway", id="legacy-enriched"),
        pytest.param(LEGACY_ONLY_SWITCH, "switch", id="legacy-only-switch"),
        pytest.param(SAMPLE_NETWORK_DEVICE, "switch", id="compat-switch"),
        pytest.param(SAMPLE_PDU_DEVICE, "other", id="pdu"),
        pytest.param({"topology": {"legacy_type": "UDR"}}, "gateway", id="legacy-gw"),
        pytest.param({"topology": {"legacy_type": "uap"}}, "access_point", id="uap"),
        pytest.param({"topology": "bad", "type": "Gateway"}, "gateway", id="v1-gw"),
        pytest.param({"type": "switch"}, "switch", id="v1-switch"),
        pytest.param({"type": "Access Point"}, "access_point", id="v1-ap"),
        pytest.param({"type": "uap"}, "access_point", id="v1-uap"),
        pytest.param({"type": "pdu"}, "other", id="v1-other"),
        pytest.param({"model": "Cloud Gateway Max"}, "gateway", id="model-text"),
        pytest.param({"model": "UCG-Fiber"}, "gateway", id="model-prefix"),
        pytest.param(
            {"model": "USW Flex", "features": ["switching", "gateway"]},
            "gateway",
            id="gateway-feature-list",
        ),
        pytest.param(
            {"model": "USW Flex", "features": {"switching": {}, "gateway": {}}},
            "gateway",
            id="gateway-feature-object",
        ),
        pytest.param(
            {"model": "USW Flex", "features": ["router"]}, "gateway", id="router"
        ),
        pytest.param(
            {"features": {"switching": {"lags": []}, "accessPoint": None}},
            "switch",
            id="details-switch",
        ),
        pytest.param({"features": {"accessPoint": {}}}, "access_point", id="ap-obj"),
        pytest.param({"features": {"gateway": False}}, "other", id="false-feature"),
        pytest.param({"features": ["accessPoint"]}, "access_point", id="ap-list"),
        pytest.param({"features": "switching"}, "other", id="bad-features"),
        pytest.param(
            {"model": "USW Flex", "features": ("switching", "gateway")},
            "gateway",
            id="gateway-feature-tuple",
        ),
        pytest.param({"features": ("switching",)}, "switch", id="switch-tuple"),
        pytest.param({"model": 5}, "other", id="bad-model"),
    ],
)
def test_device_kind(device: dict[str, Any], expected: str) -> None:
    """Gateway checks run before access point and switching checks."""
    assert device_kind(device) == expected


# --- Ports ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (3, 3),
        (0, 0),
        ("03", 3),
        (" 3 ", 3),
        ("x", None),
        ("", None),
        (True, None),
        (3.0, None),
        (None, None),
    ],
)
def test_port_index(raw: Any, expected: int | None) -> None:
    """Ints and numeric strings normalise with int(); nothing else does."""
    assert port_index(raw) == expected


def _facts(device: dict[str, Any], stats: dict[str, Any] | None = None) -> Any:
    """Build one device's facts through the site builder."""
    site = build_site_network_facts({"dev": device}, {"dev": stats or {}})
    return site.devices["dev"]


def test_ports_come_from_the_legacy_merge() -> None:
    """Legacy-enriched ports keep source order, raw spelling and the uplink."""
    facts = _facts(UDM_PRO_LEGACY_ENRICHED)

    assert [port.raw_index for port in facts.ports] == [1, 9, 11]
    assert [port.is_uplink for port in facts.ports] == [False, True, False]
    assert facts.port_by_index(9) is facts.ports[1]
    assert facts.port_by_index(2) is None
    assert facts.ports[2].record["sfp_found"] is False


def test_v1_overview_has_no_ports() -> None:
    """A list-shaped interfaces and an empty ports list yield no ports."""
    assert _facts(SAMPLE_UDM_PRO_OVERVIEW).ports == ()


def test_port_index_spellings_zero_and_duplicates() -> None:
    """Raw spellings are kept, zero is a port, and the first record wins."""
    device = {
        "ports": [
            {"idx": 0, "name": "zero"},
            {"port_idx": "03", "name": "padded"},
            {"portIdx": " 3 ", "name": "spaced"},
            {"index": 4, "isUplink": True},
            {"idx": True},
            {"idx": None, "port_idx": 5, "name": "first-5"},
            {"idx": 5, "name": "second-5"},
            "not-a-port",
        ]
    }
    facts = _facts(device)

    assert [port.raw_index for port in facts.ports] == [0, "03", " 3 ", 4, True, 5, 5]
    assert [port.index for port in facts.ports] == [0, 3, 3, 4, None, 5, 5]
    assert facts.port_by_index(0).record["name"] == "zero"
    assert facts.port_by_index(3).record["name"] == "padded"
    assert facts.port_by_index(5).record["name"] == "first-5"
    assert facts.port_by_raw_index(" 3 ").record["name"] == "spaced"
    assert facts.port_by_raw_index("03").record["name"] == "padded"
    assert facts.port_by_raw_index(3) is None
    assert facts.port_by_raw_index(1) is None
    flag = True
    assert facts.port_by_raw_index(flag).raw_index is True
    assert facts.ports[3].is_uplink is True


@pytest.mark.parametrize(
    ("device", "names"),
    [
        pytest.param(
            {"ports": [], "interfaces": {"ports": [{"idx": 1, "name": "v1"}]}},
            ["v1"],
            id="details-interfaces",
        ),
        pytest.param(
            {
                "ports": [{"idx": 1, "name": "legacy"}],
                "interfaces": {"ports": [{"idx": 2, "name": "v1"}]},
            },
            ["legacy"],
            id="no-merge",
        ),
        pytest.param({"interfaces": ["ports", "radios"]}, [], id="overview-list"),
        pytest.param({"interfaces": {"ports": "bad"}}, [], id="bad-interfaces"),
        pytest.param({"ports": "bad"}, [], id="bad-ports"),
    ],
)
def test_port_source(device: dict[str, Any], names: list[str]) -> None:
    """Top-level ports win; mapping interfaces.ports is the only fallback."""
    assert [port.record["name"] for port in _facts(device).ports] == names


def test_port_records_are_read_only_copies() -> None:
    """A port record cannot be changed and does not follow its source."""
    source = {"idx": 1, "name": "a"}
    facts = _facts({"ports": [source]})
    record = facts.ports[0].record

    with pytest.raises(TypeError):
        record["name"] = "b"  # type: ignore[index]
    source["name"] = "changed"
    assert record["name"] == "a"


# --- Metrics --------------------------------------------------------------


@pytest.mark.parametrize(
    ("stats", "expected"),
    [
        pytest.param(
            {"uplink": {"txRateBps": 100, "rxRateBps": 50}}, (100, 50), id="public"
        ),
        pytest.param(
            {"uplink": {"tx_rate_bps": 100, "rx_rate_bps": 50}}, (100, 50), id="snake"
        ),
        pytest.param(
            {"uplink": {"tx_bytes_per_sec": 100, "rx_bytes_per_sec": 50}},
            (800, 400),
            id="bytes",
        ),
        pytest.param({"txRateBps": 100, "rxRateBps": 50}, (100, 50), id="top-level"),
        pytest.param(
            {"uplink": {"txRateBps": 1}, "txRateBps": 2, "rx_bytes_per_sec": 3},
            (1, 24),
            id="uplink-first",
        ),
        pytest.param(
            {"uplink": {"txRateBps": None, "tx_bytes_per_sec": 2}}, (16, None), id="n"
        ),
        pytest.param(
            {"uplink_stats": {"txRateBps": 7, "rxRateBps": 8}}, (7, 8), id="alias"
        ),
        pytest.param({"uplink": "bad", "txRateBps": 9}, (9, None), id="bad-uplink"),
        pytest.param(ZERO_STATS, (0, 0), id="zeros"),
        pytest.param(MALFORMED_STATS, (None, None), id="malformed"),
        pytest.param(
            {"uplink": {"txRateBps": "bad"}, "txRateBps": 5}, (None, None), id="no-fall"
        ),
        pytest.param({"tx_rate": 1, "txRate": 2, "uplinkTxRate": 3}, (None, None)),
        pytest.param({}, (None, None), id="empty"),
    ],
)
def test_uplink_rate_bps(stats: dict[str, Any], expected: tuple[Any, Any]) -> None:
    """Public rates are bits/s; only the explicit bytes/s keys are x8."""
    assert (uplink_rate_bps(stats, "tx"), uplink_rate_bps(stats, "rx")) == expected


@pytest.mark.parametrize(
    ("device", "stats", "expected"),
    [
        pytest.param(
            SAMPLE_UDM_PRO_OVERVIEW,
            SAMPLE_UDM_PRO_STATS,
            (14.6, 62.5, 3333412, 35072.0, 74016.0),
            id="captured",
        ),
        pytest.param({}, ZERO_STATS, (0.0, 0.0, 0, 0.0, 0.0), id="zeros"),
        pytest.param(
            {}, SNAKE_AND_BYTES_STATS, (7.5, 40.0, 3600, 100.0, 400.0), id="snake"
        ),
        pytest.param(
            {}, MALFORMED_STATS, (None, None, None, None, None), id="malformed"
        ),
        pytest.param(
            {"cpu": 1, "memory": 2, "uptime": 3},
            {},
            (1.0, 2.0, 3, None, None),
            id="device-record-fallback",
        ),
        pytest.param(
            {"cpu": 1},
            {"cpuUtilizationPct": 9},
            (9.0, None, None, None, None),
            id="stats-first",
        ),
    ],
)
def test_metrics(
    device: dict[str, Any], stats: dict[str, Any], expected: tuple[Any, ...]
) -> None:
    """Metrics read stats then device, keep zeros and reject junk."""
    facts = _facts(device, stats)
    assert (
        facts.cpu_pct,
        facts.memory_pct,
        facts.uptime_s,
        facts.tx_bps,
        facts.rx_bps,
    ) == expected
    assert facts.uptime_s is None or isinstance(facts.uptime_s, int)


# --- Client connection type -----------------------------------------------


@pytest.mark.parametrize(
    ("client", "expected"),
    [
        ({"type": "WIRED"}, "wired"),
        ({"type": "wireless"}, "wireless"),
        ({"type": " Wired "}, "wired"),
        ({"type": "ClientType.WIRELESS"}, "wireless"),
        ({"type": ClientType.WIRED}, "wired"),
        ({"type": None, "connection_type": "WIRELESS"}, "wireless"),
        ({"connection_type": "wired"}, "wired"),
        ({"type": "VPN"}, None),
        ({"type": "WIRED_GUEST"}, None),
        ({"type": "UNKNOWN_WIRELESS"}, None),
        ({"type": ""}, None),
        ({"type": 1}, None),
        ({}, None),
    ],
)
def test_client_connection_type(client: dict[str, Any], expected: Any) -> None:
    """Recognition is exact after enum unwrap and prefix removal."""
    assert client_connection_type(client) == expected


# --- Builder and table rows -----------------------------------------------


@pytest.mark.parametrize(
    ("device", "stats", "expected"),
    [
        pytest.param(
            LEGACY_ONLY_SWITCH,
            {},
            ("online", "switch", False, [2], "aa:bb:cc:00:00:e1"),
            id="legacy-only",
        ),
        pytest.param(
            SAMPLE_UDM_PRO_OVERVIEW,
            SAMPLE_UDM_PRO_STATS,
            ("online", "gateway", True, [], "aa:bb:cc:00:00:d1"),
            id="v1-only-null-type",
        ),
        pytest.param(
            UDM_PRO_LEGACY_ENRICHED,
            SAMPLE_UDM_PRO_STATS,
            ("online", "gateway", True, [1, 9, 11], "aa:bb:cc:00:00:d1"),
            id="both",
        ),
        pytest.param(
            SAMPLE_NETWORK_DEVICE,
            ZERO_STATS,
            ("online", "switch", False, [], "00:11:22:33:44:66"),
            id="compat-explicit-zeros",
        ),
        pytest.param(
            {**SAMPLE_PDU_DEVICE, "state": "bad", "mac": "junk"},
            MALFORMED_STATS,
            ("unknown", "other", False, [], None),
            id="malformed",
        ),
    ],
)
def test_read_model_rows(
    device: dict[str, Any], stats: dict[str, Any], expected: tuple[Any, ...]
) -> None:
    """One row per source mix: legacy only, v1 only, both, zeros, malformed."""
    facts = _facts(device, stats)
    assert (
        facts.online_state,
        facts.kind,
        facts.is_site_gateway,
        [port.raw_index for port in facts.ports],
        facts.mac,
    ) == expected


def test_legacy_pdu_fixture_has_no_ports_or_gateway() -> None:
    """The legacy PDU record is not a gateway and carries no port table."""
    facts = _facts(SAMPLE_PDU_LEGACY_DEVICE)
    assert facts.kind == "other"
    assert facts.ports == ()
    assert facts.mac == "00:11:22:33:44:55"


def test_site_gateway_is_first_gateway_in_map_order() -> None:
    """The first gateway in map order is the site gateway; others are not."""
    devices = {
        "switch": SAMPLE_NETWORK_DEVICE,
        "udm": SAMPLE_UDM_PRO_OVERVIEW,
        "second": {"model": "UXG-Lite"},
        7: {"model": "U6 Pro", "features": ["accessPoint"]},
        "junk": "not-a-device",
    }
    site = build_site_network_facts(devices, {"udm": SAMPLE_UDM_PRO_STATS, 7: "x"})

    assert site.gateway_id == "udm"
    assert list(site.devices) == ["switch", "udm", "second", "7"]
    assert site.devices["udm"].is_site_gateway is True
    assert site.devices["second"].kind == "gateway"
    assert site.devices["second"].is_site_gateway is False
    assert site.devices["udm"].tx_bps == 35072.0
    assert site.devices["7"].device_id == "7"
    assert site.devices["7"].cpu_pct is None


def test_builder_without_gateway_or_inputs() -> None:
    """No gateway gives None; non-mapping inputs give an empty site."""
    assert build_site_network_facts({"a": SAMPLE_NETWORK_DEVICE}, {}).gateway_id is None
    empty = build_site_network_facts(None, None)
    assert empty.gateway_id is None
    assert dict(empty.devices) == {}


def test_builder_does_not_mutate_inputs_and_is_read_only() -> None:
    """The builder leaves its inputs alone and returns frozen records."""
    devices = {"udm": copy.deepcopy(UDM_PRO_LEGACY_ENRICHED)}
    stats = {"udm": copy.deepcopy(SAMPLE_UDM_PRO_STATS)}
    before = copy.deepcopy((devices, stats))

    site = build_site_network_facts(devices, stats)

    assert (devices, stats) == before
    with pytest.raises(TypeError):
        site.devices["other"] = site.devices["udm"]  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        site.gateway_id = "x"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        site.devices["udm"].kind = "switch"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        site.devices["udm"].ports[0].index = 5  # type: ignore[misc]
    assert isinstance(site.devices["udm"].ports[0], NetworkPortFacts)


# --- Accessors ------------------------------------------------------------


def _raw_data() -> dict[str, Any]:
    """Coordinator-shaped data without published facts."""
    return {
        "devices": {"site": {"udm": SAMPLE_UDM_PRO_OVERVIEW}},
        "stats": {"site": {"udm": SAMPLE_UDM_PRO_STATS}},
    }


def test_accessor_returns_published_facts_without_rebuilding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Published facts come back by identity and the builder is not called."""
    published = build_site_network_facts(
        {"udm": SAMPLE_UDM_PRO_OVERVIEW}, {"udm": SAMPLE_UDM_PRO_STATS}
    )
    data = {**_raw_data(), NETWORK_FACTS_KEY: {"site": published}}

    def _fail(*_args: Any) -> SiteNetworkFacts:
        raise AssertionError

    monkeypatch.setattr(
        "custom_components.unifi_insights.data_transforms.build_site_network_facts",
        _fail,
    )

    assert site_network_facts(data, "site") is published
    assert device_facts(data, "site", "udm") is published.devices["udm"]
    assert site_network_facts(data, "other") is None
    assert device_facts(data, "other", "udm") is None
    assert site_network_facts({NETWORK_FACTS_KEY: {"site": "bad"}}, "site") is None


def test_accessor_falls_back_to_the_builder(caplog: pytest.LogCaptureFixture) -> None:
    """Without published facts the same builder runs and says so at debug."""
    with caplog.at_level(
        logging.DEBUG, logger="custom_components.unifi_insights.data_transforms"
    ):
        site = site_network_facts(_raw_data(), "site")

    assert site is not None
    assert site.gateway_id == "udm"
    assert "Building unpublished network facts for site site" in caplog.text
    facts = device_facts({**_raw_data(), NETWORK_FACTS_KEY: None}, "site", "udm")
    assert isinstance(facts, NetworkDeviceFacts)
    assert device_facts(_raw_data(), "site", "missing") is None
    assert site_network_facts({"devices": {"site": "bad"}}, "site") is None
    assert site_network_facts({}, "site") is None
    no_stats = site_network_facts({"devices": _raw_data()["devices"]}, "site")
    assert no_stats is not None
    assert no_stats.devices["udm"].cpu_pct is None
