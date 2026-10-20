# Copyright 2026 UniFi Insights contributors
"""Tests for the facade snapshot lookups in data_transforms (ADR-013)."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.unifi_insights.data_transforms import (
    latest_voucher,
    network_client,
    network_device,
    network_device_stats,
    protect_device,
    rule_record,
    site_record,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

SITE = "site-1"
DEVICE = {"id": "dev-1", "name": "Switch"}
STATS = {"cpuUtilizationPct": 12}
CLIENT = {"id": "cli-1", "macAddress": "02:00:00:00:00:02"}
SITE_RECORD = {"id": SITE, "name": "Home"}
RULE = {"id": "rule-1", "enabled": True}
CAMERA = {"id": "cam-1", "state": "CONNECTED"}
VOUCHER = {"id": "v-1", "code": "1234512345"}

DATA: dict[str, Any] = {
    "sites": {SITE: SITE_RECORD},
    "devices": {SITE: {"dev-1": DEVICE, "dev-bad": "not-a-mapping"}},
    "stats": {SITE: {"dev-1": STATS}},
    "clients": {SITE: {"cli-1": CLIENT}},
    "firewall_rules": {SITE: {"rule-1": RULE}},
    "port_forwards": {SITE: None},
    "protect": {"cameras": {"cam-1": CAMERA}, "chimes": None},
    "latest_vouchers": {SITE: VOUCHER, "site-2": None},
}

type Lookup = Callable[[Mapping[str, Any]], Mapping[str, Any] | None]

FOUND: list[tuple[str, Lookup, dict[str, Any]]] = [
    ("network_device", lambda d: network_device(d, SITE, "dev-1"), DEVICE),
    ("network_device_stats", lambda d: network_device_stats(d, SITE, "dev-1"), STATS),
    ("network_client", lambda d: network_client(d, SITE, "cli-1"), CLIENT),
    ("site_record", lambda d: site_record(d, SITE), SITE_RECORD),
    (
        "rule_record",
        lambda d: rule_record(d, "firewall_rules", SITE, "rule-1"),
        RULE,
    ),
    ("protect_device", lambda d: protect_device(d, "cameras", "cam-1"), CAMERA),
    ("latest_voucher", lambda d: latest_voucher(d, SITE), VOUCHER),
]

MISSING: list[tuple[str, Lookup]] = [
    ("unknown_device", lambda d: network_device(d, SITE, "dev-9")),
    ("malformed_device", lambda d: network_device(d, SITE, "dev-bad")),
    ("unknown_site_devices", lambda d: network_device(d, "site-9", "dev-1")),
    ("unknown_stats", lambda d: network_device_stats(d, SITE, "dev-9")),
    ("unknown_client", lambda d: network_client(d, SITE, "cli-9")),
    ("unknown_site", lambda d: site_record(d, "site-9")),
    ("unknown_rule", lambda d: rule_record(d, "firewall_rules", SITE, "rule-9")),
    ("unknown_rule_kind", lambda d: rule_record(d, "nat_rules", SITE, "rule-1")),
    ("null_rule_site", lambda d: rule_record(d, "port_forwards", SITE, "rule-1")),
    ("unknown_camera", lambda d: protect_device(d, "cameras", "cam-9")),
    ("null_collection", lambda d: protect_device(d, "chimes", "chime-1")),
    ("unknown_collection", lambda d: protect_device(d, "lights", "light-1")),
    ("unknown_voucher_site", lambda d: latest_voucher(d, "site-9")),
    ("null_voucher", lambda d: latest_voucher(d, "site-2")),
]


@pytest.mark.parametrize(
    ("lookup", "expected"),
    [pytest.param(lookup, expected, id=name) for name, lookup, expected in FOUND],
)
def test_lookup_returns_the_record_itself(
    lookup: Lookup, expected: dict[str, Any]
) -> None:
    """Each lookup returns the published record, not a copy."""
    assert lookup(DATA) is expected


@pytest.mark.parametrize(
    "lookup", [pytest.param(lookup, id=name) for name, lookup in MISSING]
)
def test_lookup_of_a_missing_record_is_none(lookup: Lookup) -> None:
    """A missing or malformed record is None."""
    assert lookup(DATA) is None


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({}, id="empty"),
        pytest.param(dict.fromkeys(DATA), id="null_sections"),
        pytest.param(dict.fromkeys(DATA, "not-a-mapping"), id="malformed_sections"),
        pytest.param(None, id="no_data"),
    ],
)
@pytest.mark.parametrize(
    "lookup", [pytest.param(lookup, id=name) for name, lookup, _ in FOUND]
)
def test_lookup_never_raises_on_malformed_data(lookup: Lookup, data: Any) -> None:
    """A missing or malformed section reads as no record."""
    assert lookup(data) is None


@pytest.mark.parametrize(
    "lookup", [pytest.param(lookup, id=name) for name, lookup, _ in FOUND]
)
def test_lookup_reads_any_mapping(lookup: Lookup) -> None:
    """Read-only mappings, as the test builders freeze them, read the same."""

    def freeze(value: Any) -> Any:
        if isinstance(value, dict):
            return MappingProxyType({key: freeze(item) for key, item in value.items()})
        return value

    assert lookup(freeze(DATA)) == lookup(DATA)
