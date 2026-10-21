# Copyright 2026 UniFi Insights contributors
"""Tests for the facade snapshot lookups in data_transforms (ADR-013)."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.unifi_insights.coordinators.facade import capture_availability
from custom_components.unifi_insights.data_transforms import (
    latest_voucher,
    network_client,
    network_device,
    network_device_stats,
    owner_available,
    protect_collection,
    protect_device,
    rule_record,
    section_by_site,
    site_record,
    site_section,
    site_section_available,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from custom_components.unifi_insights.data_transforms import AvailabilityOwner

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


# --- Protect collections -----------------------------------------------------

LIGHT = {"id": "light-1", "name": "Porch"}
COLLECTIONS: dict[str, Any] = {
    "protect": {
        "cameras": {"cam-1": CAMERA, "cam-bad": "not-a-mapping", "cam-null": None},
        "lights": {"light-1": LIGHT},
        "sensors": {},
        "chimes": None,
        "viewers": "not-a-mapping",
    }
}


@pytest.mark.parametrize(
    ("collection", "expected"),
    [
        pytest.param("cameras", {"cam-1": CAMERA}, id="skips_malformed_records"),
        pytest.param("lights", {"light-1": LIGHT}, id="all_records"),
        pytest.param("sensors", {}, id="empty"),
    ],
)
def test_protect_collection_returns_the_dict_records(
    collection: str, expected: dict[str, Any]
) -> None:
    """Only the dict records come back, as the published objects."""
    records = protect_collection(COLLECTIONS, collection)

    assert records == expected
    assert records is not None
    for device_id, record in expected.items():
        assert records[device_id] is record


@pytest.mark.parametrize(
    ("data", "collection"),
    [
        pytest.param(COLLECTIONS, "chimes", id="null_collection"),
        pytest.param(COLLECTIONS, "viewers", id="malformed_collection"),
        pytest.param(COLLECTIONS, "nvrs", id="unknown_collection"),
        pytest.param({}, "cameras", id="no_section"),
        pytest.param({"protect": None}, "cameras", id="null_section"),
        pytest.param({"protect": []}, "cameras", id="malformed_section"),
        pytest.param(None, "cameras", id="no_data"),
    ],
)
def test_protect_collection_not_loaded_is_none(data: Any, collection: str) -> None:
    """A missing or malformed section or collection is None, never empty."""
    assert protect_collection(data, collection) is None


def test_protect_collection_reads_any_mapping() -> None:
    """Read-only sections and collections read the same; records stay dicts."""
    data = MappingProxyType(
        {"protect": MappingProxyType({"lights": MappingProxyType({"light-1": LIGHT})})}
    )

    assert protect_collection(data, "lights") == {"light-1": LIGHT}


# --- Site-keyed sections -----------------------------------------------------

WIFI = {"id": "wifi-1", "name": "Home"}
SECTIONS: dict[str, Any] = {
    "devices": {
        SITE: {"dev-1": DEVICE, "dev-bad": "not-a-mapping", "dev-null": None},
        "site-2": {},
        "site-null": None,
        "site-bad": "not-a-mapping",
    },
    "wifi": {SITE: MappingProxyType({"wifi-1": WIFI})},
    "clients": None,
    "stats": "not-a-mapping",
}


@pytest.mark.parametrize(
    ("section", "site_id", "expected"),
    [
        pytest.param("devices", SITE, {"dev-1": DEVICE}, id="skips_malformed_records"),
        pytest.param("devices", "site-2", {}, id="empty"),
        pytest.param("wifi", SITE, {"wifi-1": WIFI}, id="read_only_site"),
    ],
)
def test_site_section_returns_the_dict_records(
    section: str, site_id: str, expected: dict[str, Any]
) -> None:
    """Only one site's dict records come back, as the published objects."""
    records = site_section(SECTIONS, section, site_id)

    assert records == expected
    assert records is not None
    for record_id, record in expected.items():
        assert records[record_id] is record


@pytest.mark.parametrize(
    ("data", "section", "site_id"),
    [
        pytest.param(SECTIONS, "devices", "site-9", id="unknown_site"),
        pytest.param(SECTIONS, "devices", "site-null", id="null_site"),
        pytest.param(SECTIONS, "devices", "site-bad", id="malformed_site"),
        pytest.param(SECTIONS, "clients", SITE, id="null_section"),
        pytest.param(SECTIONS, "stats", SITE, id="malformed_section"),
        pytest.param(SECTIONS, "sites", SITE, id="no_section"),
        pytest.param(None, "devices", SITE, id="no_data"),
    ],
)
def test_site_section_not_loaded_is_none(data: Any, section: str, site_id: str) -> None:
    """A missing or malformed section or site is None, never empty."""
    assert site_section(data, section, site_id) is None


def test_section_by_site_skips_malformed_sites_and_records() -> None:
    """Every site that is a mapping comes back, with only its dict records."""
    by_site = section_by_site(SECTIONS, "devices")

    assert by_site == {SITE: {"dev-1": DEVICE}, "site-2": {}}
    assert by_site is not None
    assert by_site[SITE]["dev-1"] is DEVICE


def test_section_by_site_reads_any_mapping() -> None:
    """Read-only sections and sites read the same; records stay dicts."""
    data = MappingProxyType({"wifi": MappingProxyType({SITE: SECTIONS["wifi"][SITE]})})

    assert section_by_site(data, "wifi") == {SITE: {"wifi-1": WIFI}}


@pytest.mark.parametrize(
    ("data", "section"),
    [
        pytest.param(SECTIONS, "clients", id="null_section"),
        pytest.param(SECTIONS, "stats", id="malformed_section"),
        pytest.param(SECTIONS, "sites", id="no_section"),
        pytest.param(None, "devices", id="no_data"),
    ],
)
def test_section_by_site_not_loaded_is_none(data: Any, section: str) -> None:
    """A missing or malformed section is None, never empty."""
    assert section_by_site(data, section) is None


# --- Captured availability ---------------------------------------------------

OWNERS = ("device", "config", "protect", "innerspace", "network")


def _captured(
    *, config: bool = True, failed: frozenset[tuple[str, str]] = frozenset()
) -> dict[str, Any]:
    """Return a snapshot whose availability the facade captured."""
    return {
        "availability": capture_availability(
            device=True,
            config=config,
            protect=False,
            innerspace=True,
            network=False,
            failed_sections=failed,
            site_ids=(SITE,),
        )
    }


@pytest.mark.parametrize("owner", OWNERS)
def test_owner_available_reads_the_capture(owner: AvailabilityOwner) -> None:
    """Each owner reads what the facade captured for it."""
    data = _captured()

    assert owner_available(data, owner) is data["availability"][owner]


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({}, id="no_section"),
        pytest.param(None, id="no_data"),
        pytest.param({"availability": ["device"]}, id="malformed_section"),
        pytest.param({"availability": {}}, id="no_owner"),
        pytest.param({"availability": {"device": "no"}}, id="malformed_owner"),
    ],
)
def test_owner_available_without_a_capture_is_available(data: Any) -> None:
    """A missing or malformed capture reads as available and never raises."""
    assert owner_available(data, "device") is True


@pytest.mark.parametrize(
    ("data", "site_id", "section", "expected"),
    [
        pytest.param(_captured(), SITE, "wifi", True, id="fetched"),
        pytest.param(
            _captured(failed=frozenset({("wifi", SITE)})),
            SITE,
            "wifi",
            False,
            id="failed_section",
        ),
        pytest.param(
            _captured(failed=frozenset({("wifi", SITE)})),
            SITE,
            "vouchers",
            True,
            id="other_section_of_failed_site",
        ),
        pytest.param(
            _captured(config=False), SITE, "vouchers", False, id="config_failed"
        ),
        pytest.param(_captured(), "unknown", "wifi", True, id="unknown_site"),
        pytest.param(
            _captured(config=False),
            "unknown",
            "wifi",
            False,
            id="unknown_site_config_failed",
        ),
        pytest.param(
            _captured(config=False),
            SITE,
            "unknown",
            False,
            id="unknown_section_config_failed",
        ),
        pytest.param({}, SITE, "wifi", True, id="no_section"),
        pytest.param(None, SITE, "wifi", True, id="no_data"),
        pytest.param(
            {"availability": {"config": False, "sites": [SITE]}},
            SITE,
            "wifi",
            False,
            id="malformed_sites",
        ),
        pytest.param(
            {"availability": {"config": False, "sites": {SITE: "wifi"}}},
            SITE,
            "wifi",
            False,
            id="malformed_site",
        ),
        pytest.param(
            {"availability": {"config": False, "sites": {SITE: {"wifi": 1}}}},
            SITE,
            "wifi",
            False,
            id="malformed_value",
        ),
    ],
)
def test_site_section_available(
    data: Any, site_id: str, section: str, *, expected: bool
) -> None:
    """A listed section reads its capture; anything else, the config owner's."""
    assert site_section_available(data, site_id, section) is expected
