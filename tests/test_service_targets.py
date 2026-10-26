# Copyright 2026 UniFi Insights contributors
"""
Tests for the service-call target resolvers in service_targets.py (#339).

The resolvers are called directly with real device and entity registries,
``MockConfigEntry`` consoles and a facade whose command owners are autospecs
of the ADR-010 owner classes. No test here calls ``hass.services``: the
handler side (one resolver, one owner command) is tested in test_services.py.

The tables name consoles by title; a console's entry ID is its lower-case
title. A registry target is written as ``$device``, ``$entity`` or ``$<n>``
and replaced with the ID the registry gave it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_insights import CarrierFabricData
from custom_components.unifi_insights.const import (
    CAMERA_TYPE_DOORBELL,
    CONF_CARRIER_ACTIONS,
    CONF_CARRIER_ORG_ID,
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_CARRIER_FABRIC,
    DOMAIN,
)
from custom_components.unifi_insights.coordinators import (
    UnifiInsightsAccessCoordinator,
    UnifiInsightsCarrierFabricCoordinator,
    UnifiInsightsConfigCoordinator,
    UnifiInsightsDeviceCoordinator,
    UnifiInsightsProtectCoordinator,
)
from custom_components.unifi_insights.service_targets import (
    AccessTarget,
    ChimePairingTarget,
    NetworkClientTarget,
    NetworkDeviceTarget,
    NetworkSiteTarget,
    ProtectConsoleTarget,
    ProtectTarget,
    RuleExportTarget,
    _client_records_match,
    _console_entries,
    _coord_data,
    _coord_section,
    _device_owner_id,
    _entry_has_client,
    _entry_has_device,
    _entry_owns_client,
    _entry_owns_device,
    _extract_target_id,
    _mac_key,
    _protect_owns_resource,
    _resolve_native_client_id,
    _resolve_network_client_id,
    _resolve_network_device_id,
    _resolve_protect_resource_id,
    _select_network_entry,
    _select_protect_entry,
    resolve_access_call,
    resolve_carrier_subscriber_call,
    resolve_chime_pairing_call,
    resolve_network_client_call,
    resolve_network_consoles_call,
    resolve_network_device_call,
    resolve_network_site_call,
    resolve_protect_call,
    resolve_protect_console_call,
    resolve_refresh_call,
    resolve_rule_export_call,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

SUB_ID = "11111111-2222-3333-4444-555555555555"
MAC = "aa:bb:cc:dd:ee:ff"

# Cached data shapes the tables reuse.
NET = {"sites": {"s1": {}}, "devices": {"s1": {"d1": {}}}}
UNKNOWN: dict[str, Any] = {}  # nothing loaded: every ownership check says "maybe"


def _facade(data: dict[str, Any], *, protect: bool = False) -> MagicMock:
    """Return a facade read view whose command owners are autospecced."""
    facade = MagicMock()
    facade.data = data
    facade.device_coordinator = create_autospec(
        UnifiInsightsDeviceCoordinator, instance=True
    )
    facade.config_coordinator = create_autospec(
        UnifiInsightsConfigCoordinator, instance=True
    )
    facade.require_protect_coordinator.return_value = create_autospec(
        UnifiInsightsProtectCoordinator, instance=True
    )
    facade.protect_client = object() if protect else None
    return facade


def _console(
    hass: HomeAssistant,
    title: str,
    data: dict[str, Any],
    *,
    protect: bool = False,
    access: bool = False,
) -> MockConfigEntry:
    """Add a loaded console entry whose runtime data holds a facade."""
    entry = MockConfigEntry(domain=DOMAIN, title=title, entry_id=title.lower())
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = MagicMock(
        coordinator=_facade(data, protect=protect),
        access_coordinator=(
            create_autospec(UnifiInsightsAccessCoordinator, instance=True)
            if access
            else None
        ),
    )
    return entry


def _unloaded(hass: HomeAssistant, entry_id: str, domain: str = DOMAIN) -> None:
    """Add a config entry without runtime data (not loaded, or another domain)."""
    MockConfigEntry(domain=domain, entry_id=entry_id).add_to_hass(hass)


def _call(hass: HomeAssistant, service: str, **data: Any) -> ServiceCall:
    """Build a service call with already validated data."""
    return ServiceCall(hass, DOMAIN, service, data)


def _key(err: pytest.ExceptionInfo[ServiceValidationError]) -> str | None:
    """Return the translation key of a raised validation error."""
    assert err.value.translation_domain == DOMAIN
    return err.value.translation_key


@dataclass(frozen=True)
class Reg:
    """
    One registry record a table case creates before resolving.

    ``kind`` is ``device`` or an entity domain such as ``sensor``. An entity
    with ``on_device`` is attached to the device created just before it.
    """

    kind: str
    owner: str
    ident: str
    platform: str = DOMAIN
    on_device: bool = False


@dataclass(frozen=True)
class Case:
    """A routing table case: consoles, registry records, call data, outcome."""

    consoles: dict[str, Any]
    data: dict[str, Any]
    expect: tuple[Any, ...] | str
    placeholders: dict[str, str] | None = None
    registry: tuple[Reg, ...] = ()
    protect: bool = False
    foreign: tuple[str, ...] = field(default=())


def _arrange(hass: HomeAssistant, case: Case) -> dict[str, MockConfigEntry]:
    """Create the case's consoles and registry records; substitute their IDs."""
    entries = {
        title: _console(hass, title, data, protect=case.protect)
        for title, data in case.consoles.items()
    }
    for entry_id in case.foreign:
        _unloaded(hass, entry_id, "other" if entry_id == "foreign" else DOMAIN)
    return entries


def _substitute(hass: HomeAssistant, case: Case) -> dict[str, str]:
    """Create the registry records and map ``$device``/``$entity``/``$n`` to IDs."""
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    ids: dict[str, str] = {}
    device_id: str | None = None
    for index, reg in enumerate(case.registry):
        if reg.kind == "device":
            device_id = dev_reg.async_get_or_create(
                config_entry_id=reg.owner, identifiers={(reg.platform, reg.ident)}
            ).id
            ids["$device"] = ids[f"${index}"] = device_id
            continue
        entity_id = ent_reg.async_get_or_create(
            reg.kind,
            reg.platform,
            reg.ident,
            config_entry=hass.config_entries.async_get_entry(reg.owner)
            if reg.owner
            else None,
            device_id=device_id if reg.on_device else None,
        ).entity_id
        ids["$entity"] = ids[f"${index}"] = entity_id
    return ids


def _resolve_case(hass: HomeAssistant, case: Case, resolve: Any) -> Any:
    """Arrange a case and run the resolver on its call data."""
    entries = _arrange(hass, case)
    ids = _substitute(hass, case)

    def sub(value: Any) -> Any:
        return ids.get(value, value) if isinstance(value, str) else value

    data = {key: sub(value) for key, value in case.data.items()}
    if isinstance(case.expect, str):
        with pytest.raises(ServiceValidationError) as err:
            resolve(_call(hass, "x", **data))
        assert _key(err) == case.expect
        expected = (
            {k: sub(v) for k, v in case.placeholders.items()}
            if case.placeholders is not None
            else None
        )
        if case.placeholders is not None:
            assert err.value.translation_placeholders == expected
        return None
    result = resolve(_call(hass, "x", **data))
    title = case.expect[0]
    assert result.entry is entries[title]
    assert result.cache is entries[title].runtime_data.coordinator
    return result, tuple(sub(v) for v in case.expect[1:])


# --- Target extraction -------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"camera_id": "a", "entity_id": "b", "target": {"entity_id": "c"}}, "a"),
        ({"target": {"camera_id": "a", "entity_id": "b"}, "entity_id": "x"}, "a"),
        ({"target": {"entity_id": "b", "device_id": "c"}}, "b"),
        ({"target": {"device_id": "c"}, "entity_id": "x"}, "c"),
        ({"target": {"area_id": "kitchen"}, "entity_id": "b"}, "b"),
        ({"entity_id": "b", "device_id": "c"}, "b"),
        ({"device_id": [" c "]}, "c"),
        ({"device_id": "  "}, None),
        ({"target": {"area_id": "kitchen"}}, None),
        ({"target": "not-a-dict"}, None),
        ({"device_id": 5}, None),
        ({}, None),
    ],
    ids=[
        "primary-first",
        "target-primary",
        "target-entity",
        "target-device-before-top-level",
        "area-ignored",
        "entity-before-device",
        "list-trimmed",
        "blank",
        "area-only",
        "target-not-dict",
        "not-a-string",
        "nothing",
    ],
)
async def test_extraction_precedence(
    hass: HomeAssistant, data: dict[str, Any], expected: str | None
) -> None:
    """Primary field, then the target dict, then the top-level entity and device."""
    assert _extract_target_id(_call(hass, "set_hdr_mode", **data), "camera_id") == (
        expected
    )


@pytest.mark.parametrize(
    ("value", "key", "placeholders"),
    [
        ([], "target_required", None),
        (["a", "b"], "target_multiple", {"service": "set_hdr_mode"}),
    ],
)
async def test_extraction_needs_exactly_one_target(
    hass: HomeAssistant, value: list[str], key: str, placeholders: Any
) -> None:
    """An empty or multi-item list is refused before any routing."""
    with pytest.raises(ServiceValidationError) as err:
        _extract_target_id(_call(hass, "set_hdr_mode", camera_id=value), "camera_id")
    assert _key(err) == key
    assert err.value.translation_placeholders == placeholders


@pytest.mark.parametrize(
    ("call", "key", "placeholders"),
    [
        (
            lambda h, c: resolve_network_device_call(h, c, action="restart"),
            "device_target_required",
            None,
        ),
        (resolve_network_client_call, "client_target_required", None),
        (
            lambda h, c: resolve_protect_call(h, c, "light"),
            "protect_target_required",
            {"resource_type": "light"},
        ),
        (
            resolve_chime_pairing_call,
            "protect_target_required",
            {"resource_type": "chime"},
        ),
    ],
)
async def test_each_family_names_its_missing_target(
    hass: HomeAssistant, call: Any, key: str, placeholders: Any
) -> None:
    """A call without a target raises the family's own translated error."""
    _console(hass, "Office", {}, protect=True)
    with pytest.raises(ServiceValidationError) as err:
        call(hass, _call(hass, "x", site_id="s1"))
    assert _key(err) == key
    assert err.value.translation_placeholders == placeholders


# --- Network device ----------------------------------------------------------

_DEVICE_CASES = {
    "raw-id-site-from-cache": Case(
        {"Office": NET}, {"device_id": "d1"}, ("Office", "d1", "s1")
    ),
    "raw-id-with-site": Case(
        {"Office": NET}, {"device_id": "d1", "site_id": "s1"}, ("Office", "d1", "s1")
    ),
    "device-cached-identifier": Case(
        {"Office": NET},
        {"device_id": "$device"},
        ("Office", "d1", "s1"),
        registry=(Reg("device", "office", "s1_d1"),),
    ),
    "device-site-with-underscore": Case(
        {"Office": {"devices": "unknown"}},
        {"device_id": "$device", "site_id": "my_site"},
        ("Office", "dx", "my_site"),
        registry=(Reg("device", "office", "my_site_dx"),),
    ),
    "device-identifier-underscore-device-id": Case(
        {"Office": {"sites": {"default": {}}, "devices": {"default": {"dev_1": {}}}}},
        {"device_id": "$device"},
        ("Office", "dev_1", "default"),
        registry=(Reg("device", "office", "default_dev_1"),),
    ),
    "device-identifier-split": Case(
        {"Office": UNKNOWN},
        {"device_id": "$device"},
        "restart_site_required",
        {"device_id": "dx"},
        registry=(Reg("device", "office", "a_b_dx"),),
    ),
    "device-protect-identifier-skipped": Case(
        {"Office": UNKNOWN},
        {"device_id": "$device"},
        "network_device_unresolved",
        {"target": "$device"},
        registry=(Reg("device", "office", "protect_camera_c1"),),
    ),
    "device-malformed-identifier": Case(
        {"Office": UNKNOWN},
        {"device_id": "$device"},
        "network_device_unresolved",
        {"target": "$device"},
        registry=(Reg("device", "office", "malformed"),),
    ),
    "device-empty-site-cache": Case(
        {"Office": {"devices": {"s1": {}}}},
        {"device_id": "$device", "site_id": "s1"},
        "device_not_on_site_console",
        {"device_id": "native", "site_id": "s1"},
        registry=(Reg("device", "office", "s1_native"),),
    ),
    "entity-cached-prefix": Case(
        {"Office": NET},
        {"entity_id": "$entity"},
        ("Office", "d1", "s1"),
        registry=(Reg("sensor", "office", "s1_d1_cpu"),),
    ),
    "entity-exact-unique-id": Case(
        {"Office": NET},
        {"entity_id": "$entity"},
        ("Office", "d1", "s1"),
        registry=(Reg("button", "office", "s1_d1"),),
    ),
    "entity-uncached-split": Case(
        {"Office": {"devices": {}}},
        {"entity_id": "$entity"},
        "restart_site_required",
        {"device_id": "d7"},
        registry=(Reg("sensor", "office", "s7_d7_cpu"),),
    ),
    "legacy-entity-through-its-device": Case(
        {"Office": NET},
        {"entity_id": "$entity"},
        ("Office", "d1", "s1"),
        registry=(
            Reg("device", "office", "s1_d1"),
            Reg("sensor", "office", "legacy", on_device=True),
        ),
    ),
    "entity-not-in-registry": Case(
        {"Office": NET},
        {"entity_id": "sensor.nope"},
        "target_entity_not_found",
        {"target": "sensor.nope"},
    ),
    "entity-of-another-integration": Case(
        {"Office": NET},
        {"entity_id": "$entity"},
        "target_not_integration_entity",
        {"target": "$entity"},
        registry=(Reg("switch", "office", "x", platform="other"),),
    ),
    "entity-of-unloaded-console": Case(
        {"Office": NET},
        {"entity_id": "$entity"},
        "target_console_not_loaded",
        registry=(Reg("sensor", "unloaded", "s1_d1_cpu"),),
        foreign=("unloaded",),
    ),
    "device-of-another-integration": Case(
        {"Office": NET},
        {"device_id": "$device"},
        "target_not_integration_device",
        {"target": "$device"},
        registry=(Reg("device", "foreign", "x", platform="other"),),
        foreign=("foreign",),
    ),
    "foreign-device-with-our-identifier": Case(
        {"Office": NET},
        {"device_id": "$device"},
        "target_console_not_loaded",
        registry=(Reg("device", "foreign", "s1_d1"),),
        foreign=("foreign",),
    ),
    "mobility-device": Case(
        {"Office": NET},
        {"device_id": "$device"},
        "target_mobility_unsupported",
        {"target": "$device"},
        registry=(Reg("device", "office", "mobility_router"),),
    ),
    "mobility-entity": Case(
        {"Office": NET},
        {"entity_id": "$entity"},
        "target_mobility_unsupported",
        {"target": "$entity"},
        registry=(Reg("sensor", "office", "mobility_router_signal"),),
    ),
    "registry-owner-among-duplicates": Case(
        {"Office": NET, "Garage": NET},
        {"device_id": "$1"},
        ("Garage", "d1", "s1"),
        registry=(Reg("device", "office", "s1_d1"), Reg("device", "garage", "s1_d1")),
    ),
    "registry-owner-not-on-site": Case(
        {"Office": NET, "Garage": {"sites": {"s2": {}}}},
        {"device_id": "$device", "site_id": "s1"},
        "device_other_console",
        {"device_id": "$device", "site_id": "s1"},
        registry=(Reg("device", "garage", "s1_d1"),),
    ),
    "site-unknown-everywhere": Case(
        {"Office": NET},
        {"device_id": "d1", "site_id": "s9"},
        "site_not_found",
        {"site_id": "s9"},
    ),
    "device-on-another-console-than-site": Case(
        {
            "Office": {"sites": {"s1": {}}, "devices": {"s1": {}}},
            "Garage": {"sites": {"s2": {}}, "devices": {"s2": {"d1": {}}}},
        },
        {"device_id": "d1", "site_id": "s1"},
        "device_other_console",
        {"device_id": "d1", "site_id": "s1"},
    ),
    "single-explicit-owner": Case(
        {"Office": UNKNOWN, "Garage": NET},
        {"device_id": "d1"},
        ("Garage", "d1", "s1"),
    ),
    "explicit-owner-in-another-site": Case(
        {"Office": UNKNOWN, "Garage": {"devices": {"s1": {}, "s2": {"d1": {}}}}},
        {"device_id": "d1"},
        ("Garage", "d1", "s2"),
    ),
    "duplicate-device-with-site": Case(
        {"Office": NET, "Garage": NET},
        {"device_id": "d1", "site_id": "s1"},
        "device_site_ambiguous",
        {"device_id": "d1", "site_id": "s1"},
    ),
    "entity-without-config-entry": Case(
        {
            "Office": UNKNOWN,
            "Garage": {"devices": {"s0": "bad", "s1": {"d0": {}, "d1": {}}}},
        },
        {"entity_id": "$entity"},
        ("Garage", "d1", "s1"),
        registry=(Reg("sensor", "", "s1_d1_cpu"),),
    ),
    "entity-without-site-or-device": Case(
        {"Office": NET},
        {"entity_id": "$entity"},
        "network_device_unresolved",
        {"target": "$entity"},
        registry=(Reg("sensor", "office", "plain"),),
    ),
    "no-console": Case({}, {"device_id": "d1"}, "no_coordinator"),
}


@pytest.mark.parametrize("case", _DEVICE_CASES.values(), ids=_DEVICE_CASES.keys())
async def test_network_device_routing(hass: HomeAssistant, case: Case) -> None:
    """Restart targets resolve to the owning console's device owner."""
    resolved = _resolve_case(
        hass,
        case,
        lambda call: resolve_network_device_call(hass, call, action="restart"),
    )
    if resolved is not None:
        target, (device_id, site_id) = resolved
        assert target == NetworkDeviceTarget(
            entry=target.entry,
            cache=target.cache,
            device_commands=target.cache.device_coordinator,
            device_id=device_id,
            site_id=site_id,
        )


@pytest.mark.parametrize(
    ("action", "key"),
    [
        ("restart", "restart_site_required"),
        ("power_cycle", "power_cycle_site_required"),
    ],
)
@pytest.mark.parametrize("cached", ["not loaded", {"other": {}}])
async def test_network_device_without_a_cached_site_is_refused(
    hass: HomeAssistant, action: Any, key: str, cached: Any
) -> None:
    """The site is never guessed: each action names its own missing-site error."""
    entry = _console(hass, "Office", {"devices": cached})
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "site_native")}
    )
    target = device.id if isinstance(cached, dict) else "d1"

    with pytest.raises(ServiceValidationError) as err:
        resolve_network_device_call(
            hass, _call(hass, "restart_device", device_id=target), action=action
        )
    assert _key(err) == key
    native = "native" if isinstance(cached, dict) else "d1"
    assert err.value.translation_placeholders == {"device_id": native}


# --- Network selection (shared by every Network family) ----------------------


@pytest.mark.parametrize(
    ("routing", "consoles", "key", "placeholders"),
    [
        (
            {},
            ({"sites": {"s1": {}}}, {"sites": {"s2": {}}}),
            "network_console_ambiguous",
            None,
        ),
        (
            {"device_id": "d1"},
            ({"devices": {"s1": {}}}, {"devices": {"s2": {}}}),
            "device_not_on_any_console",
            {"device_id": "d1"},
        ),
        (
            {"device_id": "d1"},
            ({"devices": "unknown"}, {"devices": "unknown"}),
            "device_ambiguous",
            {"device_id": "d1"},
        ),
        (
            {"site_id": "s1"},
            ({"sites": "unknown"}, {"sites": "unknown"}),
            "site_consoles_ambiguous",
            {"site_id": "s1"},
        ),
        (
            {"site_id": "s1"},
            ({"sites": {"s1": {}}}, {"sites": {"s1": {}}}),
            "site_ambiguous",
            {"site_id": "s1"},
        ),
        (
            {"device_id": "d1", "site_id": "s1"},
            (
                {"sites": {"s1": {}}, "devices": {"s1": {}}},
                {"sites": {"s2": {}}, "devices": {"s2": {}}},
            ),
            "device_not_on_site_console",
            {"device_id": "d1", "site_id": "s1"},
        ),
    ],
)
async def test_network_selection_errors(
    hass: HomeAssistant,
    routing: dict[str, str],
    consoles: tuple[dict[str, Any], dict[str, Any]],
    key: str,
    placeholders: dict[str, str] | None,
) -> None:
    """Each way the Network selector can fail names its own key."""
    _console(hass, "Office", consoles[0])
    _console(hass, "Garage", consoles[1])

    with pytest.raises(ServiceValidationError) as err:
        _select_network_entry(hass, **routing)
    assert _key(err) == key
    assert err.value.translation_placeholders == placeholders


@pytest.mark.parametrize(
    ("consoles", "site_id", "expected"),
    [
        (
            {"Office": {"sites": {"s1": {}}}, "Garage": {"sites": {"s2": {}}}},
            "s2",
            "Garage",
        ),
        ({"Office": {"sites": {"s1": {}}}, "Garage": UNKNOWN}, "s1", "Office"),
        ({"Office": UNKNOWN}, "s1", "Office"),
    ],
    ids=["site-owner", "explicit-site-over-unknown", "only-console"],
)
async def test_network_site_routing(
    hass: HomeAssistant, consoles: dict[str, Any], site_id: str, expected: str
) -> None:
    """Vouchers run on the console whose cached sites hold the site."""
    entries = {title: _console(hass, title, data) for title, data in consoles.items()}
    facade = entries[expected].runtime_data.coordinator

    target = resolve_network_site_call(
        hass, _call(hass, "generate_voucher", site_id=site_id)
    )

    assert target == NetworkSiteTarget(
        entry=entries[expected],
        cache=facade,
        config_commands=facade.config_coordinator,
        site_id=site_id,
    )


# --- Network client ----------------------------------------------------------

_CLIENTS = {"sites": {"s1": {}}, "clients": {"s1": {"native": {"mac": MAC}}}}

_CLIENT_CASES = {
    "native-id": Case(
        {"Office": _CLIENTS}, {"client_id": "native"}, ("Office", "native")
    ),
    "dashed-mac": Case(
        {"Office": _CLIENTS}, {"client_id": "AA-BB-CC-DD-EE-FF"}, ("Office", "native")
    ),
    "dotted-mac": Case(
        {"Office": _CLIENTS}, {"client_id": "aabb.ccdd.eeff"}, ("Office", "native")
    ),
    "uncached-mac-kept": Case(
        {"Office": {"clients": {"s1": {"other": {"mac": "11:22:33:44:55:66"}}}}},
        {"client_id": MAC},
        ("Office", MAC),
    ),
    "tracker-entity-mac": Case(
        {"Office": _CLIENTS},
        {"entity_id": "$entity"},
        ("Office", "native"),
        registry=(Reg("device_tracker", "office", f"unifi_insights_{MAC}"),),
    ),
    "entity-dashed-mac": Case(
        {"Office": UNKNOWN},
        {"entity_id": "$entity"},
        ("Office", MAC),
        registry=(Reg("sensor", "office", "client_aa-bb-cc-dd-ee-ff_rx"),),
    ),
    "entity-split": Case(
        {"Office": UNKNOWN},
        {"entity_id": "$entity"},
        ("Office", "c7"),
        registry=(Reg("sensor", "office", "s1_c7_rx"),),
    ),
    "legacy-entity-through-its-device": Case(
        {"Office": _CLIENTS},
        {"entity_id": "$entity"},
        ("Office", "native"),
        registry=(
            Reg("device", "office", f"client_{MAC}"),
            Reg("sensor", "office", "legacy", on_device=True),
        ),
    ),
    "client-device": Case(
        {"Office": _CLIENTS},
        {"device_id": "$device"},
        ("Office", "native"),
        registry=(Reg("device", "office", f"client_{MAC}"),),
    ),
    "device-without-client-identifier": Case(
        {"Office": UNKNOWN},
        {"device_id": "$device"},
        ("Office", "$device"),
        registry=(Reg("device", "office", "s1_d1"),),
    ),
    "mobility-entity": Case(
        {"Office": _CLIENTS},
        {"entity_id": "$entity"},
        "target_mobility_unsupported",
        {"target": "$entity"},
        registry=(Reg("sensor", "office", "mobility_router_rx"),),
    ),
    "cache-not-loaded": Case(
        {"Office": "loading"}, {"client_id": "c1"}, ("Office", "c1")
    ),
    "registry-owner-among-candidates": Case(
        {"Office": _CLIENTS, "Garage": _CLIENTS},
        {"entity_id": "$entity"},
        ("Garage", "native"),
        registry=(Reg("device_tracker", "garage", f"unifi_insights_{MAC}"),),
    ),
    "registry-owner-not-on-site": Case(
        {"Office": _CLIENTS, "Garage": {"sites": {"s2": {}}}},
        {"entity_id": "$entity"},
        "device_other_console",
        {"device_id": MAC, "site_id": "s1"},
        registry=(Reg("device_tracker", "garage", f"unifi_insights_{MAC}"),),
    ),
    "only-console-with-client": Case(
        {"Office": {"sites": {"s1": {}}, "clients": {"s1": {}}}, "Garage": _CLIENTS},
        {"client_id": MAC},
        ("Garage", "native"),
    ),
    "explicit-client-owner": Case(
        {"Office": {"sites": {"s1": {}}}, "Garage": _CLIENTS},
        {"client_id": "native"},
        ("Garage", "native"),
    ),
    "client-on-two-consoles": Case(
        {"Office": _CLIENTS, "Garage": _CLIENTS},
        {"client_id": "native"},
        "client_ambiguous",
        {"client_id": "native"},
    ),
    "unknown-client-explicit-site": Case(
        {
            "Office": {"sites": {"s1": {}}},
            "Garage": {"sites": "?"},
        },
        {"client_id": "ghost"},
        ("Office", "ghost"),
    ),
    "unknown-client-two-sites": Case(
        {"Office": {"sites": {"s1": {}}}, "Garage": {"sites": {"s1": {}}}},
        {"client_id": "ghost"},
        "site_ambiguous",
        {"site_id": "s1"},
    ),
}


@pytest.mark.parametrize("case", _CLIENT_CASES.values(), ids=_CLIENT_CASES.keys())
async def test_network_client_routing(hass: HomeAssistant, case: Case) -> None:
    """Client targets become the native client ID of the selected site."""
    data_case = Case(
        case.consoles,
        {"site_id": "s1", **case.data},
        case.expect,
        case.placeholders,
        case.registry,
    )
    resolved = _resolve_case(
        hass, data_case, lambda call: resolve_network_client_call(hass, call)
    )
    if resolved is not None:
        target, (client_id,) = resolved
        facade = target.cache
        assert target == NetworkClientTarget(
            entry=target.entry,
            cache=facade,
            device_commands=facade.device_coordinator,
            config_commands=facade.config_coordinator,
            client_id=client_id,
            site_id="s1",
        )


# --- Protect -----------------------------------------------------------------

_CAMS = {"protect": {"cameras": {"cam1": {}}}}

_PROTECT_CASES = {
    "native-id": Case({"Office": _CAMS}, {"camera_id": "cam1"}, ("Office", "cam1")),
    "entity-cached-prefix": Case(
        {"Office": _CAMS},
        {"entity_id": "$entity"},
        ("Office", "cam1"),
        registry=(Reg("switch", "office", "unifi_insights_camera_cam1_privacy"),),
    ),
    "entity-before-cache-loaded": Case(
        {"Office": {"protect": {}}},
        {"entity_id": "$entity"},
        ("Office", "cam9"),
        registry=(Reg("camera", "office", "unifi_insights_camera_cam9"),),
    ),
    "legacy-entity-through-its-device": Case(
        {"Office": _CAMS},
        {"entity_id": "$entity"},
        ("Office", "cam1"),
        registry=(
            Reg("device", "office", "protect_camera_cam1"),
            Reg("sensor", "office", "legacy", on_device=True),
        ),
    ),
    "device-identifier": Case(
        {"Office": _CAMS},
        {"device_id": "$device"},
        ("Office", "cam1"),
        registry=(Reg("device", "office", "protect_camera_cam1"),),
    ),
    "camera-device-through-its-entities": Case(
        {"Office": _CAMS},
        {"device_id": "$device"},
        ("Office", "cam1"),
        registry=(
            Reg("device", "office", "nvr_cam1"),
            Reg(
                "binary_sensor",
                "office",
                "unifi_insights_camera_cam1_motion",
                on_device=True,
            ),
        ),
    ),
    "device-of-another-type": Case(
        {"Office": _CAMS},
        {"device_id": "$device"},
        "protect_resource_wrong_type",
        {"target": "$device", "actual_type": "light", "resource_type": "camera"},
        registry=(Reg("device", "office", "protect_light_l1"),),
    ),
    "device-without-camera": Case(
        {"Office": _CAMS},
        {"device_id": "$device"},
        "protect_resource_unresolved",
        {"target": "$device", "resource_type": "camera"},
        registry=(Reg("device", "office", "s1_d1"),),
    ),
    "entity-of-another-integration": Case(
        {"Office": _CAMS},
        {"entity_id": "$entity"},
        "target_not_protect_resource",
        {"target": "$entity", "resource_type": "camera"},
        registry=(Reg("camera", "office", "x", platform="other"),),
    ),
    "not-cached-anywhere": Case(
        {"Office": _CAMS},
        {"camera_id": "cam9"},
        "protect_resource_not_found",
        {"resource_type": "camera", "resource_id": "cam9"},
    ),
    "registry-owner-without-the-camera": Case(
        {"Office": _CAMS, "Garage": {"protect": {"cameras": {}}}},
        {"entity_id": "$entity"},
        "protect_resource_other_console",
        {"resource_type": "camera", "resource_id": "$entity"},
        registry=(Reg("camera", "garage", "unifi_insights_camera_cam1"),),
    ),
    "registry-owner-among-duplicates": Case(
        {"Office": _CAMS, "Garage": _CAMS},
        {"entity_id": "$entity"},
        ("Garage", "cam1"),
        registry=(Reg("camera", "garage", "unifi_insights_camera_cam1"),),
    ),
    "single-explicit-owner": Case(
        {"Office": UNKNOWN, "Garage": _CAMS}, {"camera_id": "cam1"}, ("Garage", "cam1")
    ),
    "cached-on-two-consoles": Case(
        {"Office": _CAMS, "Garage": _CAMS},
        {"camera_id": "cam1"},
        "protect_resource_ambiguous",
        {"resource_type": "camera", "resource_id": "cam1"},
    ),
    "owner-not-known-yet": Case(
        {"Office": UNKNOWN, "Garage": {"protect": "loading"}},
        {"camera_id": "cam1"},
        "protect_resource_owner_unknown",
        {"resource_type": "camera", "resource_id": "cam1"},
    ),
    "entity-among-other-cached-cameras": Case(
        {
            "Office": UNKNOWN,
            "Garage": {"protect": {"cameras": {"cam0": {}, "cam1": {}}}},
        },
        {"entity_id": "$entity"},
        ("Garage", "cam1"),
        registry=(Reg("switch", "", "unifi_insights_camera_cam1_privacy"),),
    ),
    "entity-of-console-without-camera-list": Case(
        {"Office": {"protect": {"cameras": "loading"}}},
        {"entity_id": "$entity"},
        ("Office", "cam1"),
        registry=(Reg("switch", "office", "unifi_insights_camera_cam1_privacy"),),
    ),
    "entity-without-prefix-or-device": Case(
        {"Office": _CAMS},
        {"entity_id": "$entity"},
        "protect_resource_unresolved",
        {"target": "$entity", "resource_type": "camera"},
        registry=(Reg("sensor", "office", "plain"),),
    ),
    "device-with-only-foreign-identifiers": Case(
        {"Office": _CAMS},
        {"device_id": "$device"},
        "protect_resource_unresolved",
        {"target": "$device", "resource_type": "camera"},
        registry=(Reg("device", "office", "x", platform="other"),),
    ),
    "camera-device-with-other-entities": Case(
        {"Office": _CAMS},
        {"device_id": "$device"},
        "protect_resource_unresolved",
        {"target": "$device", "resource_type": "camera"},
        registry=(
            Reg("device", "office", "nvr_cam1"),
            Reg("sensor", "office", "nvr_storage", on_device=True),
        ),
    ),
    "no-protect-console": Case({}, {"camera_id": "cam1"}, "no_protect_coordinator"),
}


@pytest.mark.parametrize("case", _PROTECT_CASES.values(), ids=_PROTECT_CASES.keys())
async def test_protect_routing(hass: HomeAssistant, case: Case) -> None:
    """Camera targets resolve to the owning console's Protect owner."""
    protect_case = Case(
        case.consoles,
        case.data,
        case.expect,
        case.placeholders,
        case.registry,
        protect=True,
    )
    resolved = _resolve_case(
        hass, protect_case, lambda call: resolve_protect_call(hass, call, "camera")
    )
    if resolved is not None:
        target, (camera_id,) = resolved
        assert target == ProtectTarget(
            entry=target.entry,
            cache=target.cache,
            protect_commands=target.cache.require_protect_coordinator.return_value,
            resource_id=camera_id,
        )


@pytest.mark.parametrize(
    ("resource_type", "data"),
    [
        ("camera", {"camera_id": "cam1"}),
        ("light", {"light_id": "light1"}),
        ("chime", {"chime_id": "chime1", "camera_id": [" cam1 "]}),
        ("chime", {"chime_id": "chime1", "camera_id": []}),
        ("chime", {"chime_id": "chime1", "camera_id": "  "}),
        ("viewer", {"viewer_id": "viewer1", "liveview_id": "lv1"}),
    ],
)
async def test_protect_result_holds_the_protect_owner(
    hass: HomeAssistant, resource_type: Any, data: dict[str, Any]
) -> None:
    """The result holds the selected console's Protect owner and the native ID."""
    cached = {
        "cameras": {"cam1": {}},
        "lights": {"light1": {}},
        "chimes": {"chime1": {}},
        "viewers": {"viewer1": {}},
        "liveviews": {"lv1": {}},
    }
    entry = _console(hass, "Office", {"protect": cached}, protect=True)
    facade = entry.runtime_data.coordinator

    target = resolve_protect_call(hass, _call(hass, "x", **data), resource_type)

    assert target == ProtectTarget(
        entry=entry,
        cache=facade,
        protect_commands=facade.require_protect_coordinator.return_value,
        resource_id=next(iter(data.values())),
    )


@pytest.mark.parametrize(
    ("resource_type", "data", "garage", "registered"),
    [
        ("chime", {"camera_id": "cam9"}, {"cameras": {"cam9": {}}}, False),
        ("chime", {"camera_id": "$camera"}, {"cameras": {}}, True),
        ("viewer", {"liveview_id": "lv9"}, {"liveviews": {"lv9": {}}}, False),
    ],
    ids=["camera-cached-elsewhere", "camera-registered-elsewhere", "liveview"],
)
async def test_protect_secondary_on_another_console_is_refused(
    hass: HomeAssistant,
    resource_type: Any,
    data: dict[str, str],
    garage: dict[str, Any],
    *,
    registered: bool,
) -> None:
    """A chime's camera_id or a viewer's liveview_id must be on its console."""
    office = {
        "chimes": {"r1": {}},
        "viewers": {"r1": {}},
        "cameras": {},
        "liveviews": {},
    }
    _console(hass, "Office", {"protect": office}, protect=True)
    garage_entry = _console(hass, "Garage", {"protect": garage}, protect=True)
    if registered:
        data = {
            "camera_id": er.async_get(hass)
            .async_get_or_create(
                "camera",
                DOMAIN,
                "unifi_insights_camera_cam9",
                config_entry=garage_entry,
            )
            .entity_id
        }

    with pytest.raises(ServiceValidationError) as err:
        resolve_protect_call(
            hass,
            _call(hass, "x", **{f"{resource_type}_id": "r1"}, **data),
            resource_type,
        )
    assert _key(err) == "protect_secondary_other_console"
    secondary_type, secondary_id = next(iter(data.items()))
    assert err.value.translation_placeholders == {
        "secondary_type": secondary_type.removesuffix("_id"),
        "secondary_id": secondary_id,
        "resource_type": resource_type,
        "resource_id": "r1",
    }


async def test_protect_secondary_not_cached_anywhere_is_allowed(
    hass: HomeAssistant,
) -> None:
    """A camera no console has cached does not block the chime's console."""
    office = {"protect": {"chimes": {"r1": {}}, "cameras": {}}}
    entry = _console(hass, "Office", office, protect=True)
    _console(hass, "Garage", {"protect": {"cameras": {}}}, protect=True)

    target = resolve_protect_call(
        hass, _call(hass, "x", chime_id="r1", camera_id="cam9"), "chime"
    )

    assert target.entry is entry


async def test_protect_console_id_without_a_target_picks_the_console(
    hass: HomeAssistant,
) -> None:
    """Without a resource target, console_id alone picks the console."""
    _console(hass, "Office", {}, protect=True)
    garage = _console(hass, "Garage", {}, protect=True)
    facade = garage.runtime_data.coordinator

    target = resolve_protect_console_call(
        hass, _call(hass, "trigger_alarm", alarm_id="a", console_id="garage")
    )

    assert target == ProtectConsoleTarget(
        entry=garage,
        cache=facade,
        protect_commands=facade.require_protect_coordinator.return_value,
    )


@pytest.mark.parametrize(
    ("garage", "registered_on", "console_id", "expect"),
    [
        ({"cameras": {}}, None, "Garage", "refused"),
        ({"cameras": {}}, None, "garage", "refused"),
        ({"cameras": {"cam1": {}}}, "office", "Garage", "refused"),
        ({"cameras": {"cam1": {}}}, None, "Garage", "Garage"),
        ({"cameras": {"cam1": {}}}, None, "Office", "Office"),
        ({"cameras": {"cam1": {}}}, "office", "Office", "Office"),
        ("loading", None, "Garage", "refused"),
        ("loading", "garage", "Garage", "Garage"),
        ({"cameras": {}}, None, "nowhere", "console_not_found"),
    ],
    ids=[
        "cached-on-another-console",
        "named-by-entry-id",
        "registered-on-another-console",
        "collision-picks-named-console",
        "collision-picks-the-other-console",
        "registered-on-the-named-console",
        "named-console-not-loaded-yet-owned-elsewhere",
        "named-console-not-loaded-yet-registered-on-it",
        "unknown-console",
    ],
)
async def test_protect_console_id_with_a_target_must_own_it(
    hass: HomeAssistant,
    garage: Any,
    registered_on: str | None,
    console_id: str,
    expect: str,
) -> None:
    """With a target, console_id only chooses among the consoles that own it."""
    entries = {
        "Office": _console(hass, "Office", {"protect": _CAMS["protect"]}, protect=True),
        "Garage": _console(hass, "Garage", {"protect": garage}, protect=True),
    }
    resource_id = "cam1"
    if registered_on is not None:
        resource_id = (
            er.async_get(hass)
            .async_get_or_create(
                "camera",
                DOMAIN,
                "unifi_insights_camera_cam1",
                config_entry=hass.config_entries.async_get_entry(registered_on),
            )
            .entity_id
        )

    def select() -> tuple[Any, str | None]:
        return _select_protect_entry(
            hass, resource_type="camera", resource_id=resource_id, console_id=console_id
        )

    if expect in entries:
        assert select() == (entries[expect], "cam1")
        return
    with pytest.raises(ServiceValidationError) as err:
        select()
    if expect == "refused":
        assert _key(err) == "protect_resource_not_on_console"
        assert err.value.translation_placeholders == {
            "resource_type": "camera",
            "resource_id": resource_id,
            "console": "Garage",
        }
    else:
        assert _key(err) == expect


async def test_protect_console_id_falls_back_while_no_console_owns_it(
    hass: HomeAssistant,
) -> None:
    """While no console can tell it owns the target, the named one is used."""
    _console(hass, "Office", {"protect": "loading"}, protect=True)
    garage = _console(hass, "Garage", {"protect": "loading"}, protect=True)

    assert _select_protect_entry(
        hass, resource_type="camera", resource_id="cam1", console_id="Garage"
    ) == (garage, "cam1")


async def test_protect_console_id_still_checks_the_secondary_target(
    hass: HomeAssistant,
) -> None:
    """A named console that owns the chime still refuses another console's camera."""
    office = _console(
        hass, "Office", {"protect": {"chimes": {"r1": {}}, "cameras": {}}}, protect=True
    )
    _console(hass, "Garage", {"protect": {"cameras": {"cam9": {}}}}, protect=True)

    with pytest.raises(ServiceValidationError) as err:
        _select_protect_entry(
            hass,
            resource_type="chime",
            resource_id="r1",
            secondary_resource_type="camera",
            secondary_resource_id="cam9",
            console_id=office.title,
        )
    assert _key(err) == "protect_secondary_other_console"


@pytest.mark.parametrize("console_id", ["Garage", " GARAGE ", "garage"])
async def test_protect_console_id_matches_title_or_entry_id(
    hass: HomeAssistant, console_id: str
) -> None:
    """The console is named by its title or entry ID, case-insensitively."""
    _console(hass, "Office", {}, protect=True)
    garage = _console(hass, "Garage", {}, protect=True)
    _console(hass, "Network only", {})

    target = resolve_protect_console_call(
        hass, _call(hass, "create_liveview", console_id=console_id)
    )

    assert target.entry is garage


async def test_protect_single_console_needs_no_console_id(hass: HomeAssistant) -> None:
    """With one Protect console, an alarm ID is not looked up anywhere."""
    entry = _console(hass, "Office", {}, protect=True)
    target = resolve_protect_console_call(hass, _call(hass, "x", alarm_id="unknown"))
    assert target.entry is entry


@pytest.mark.parametrize(
    ("console_id", "key", "placeholders"),
    [
        ("nowhere", "console_not_found", {"console_id": "nowhere", "consoles": "A, A"}),
        ("A", "console_ambiguous", {"console_id": "A"}),
    ],
)
async def test_protect_console_id_must_match_one_console(
    hass: HomeAssistant, console_id: str, key: str, placeholders: dict[str, str]
) -> None:
    """A console_id that matches no console, or two, is refused."""
    for entry_id in ("a1", "a2"):
        entry = MockConfigEntry(domain=DOMAIN, title="A", entry_id=entry_id)
        entry.add_to_hass(hass)
        entry.runtime_data = MagicMock(coordinator=_facade({}, protect=True))

    with pytest.raises(ServiceValidationError) as err:
        resolve_protect_console_call(
            hass, _call(hass, "trigger_alarm", console_id=console_id)
        )
    assert _key(err) == key
    assert err.value.translation_placeholders == placeholders


async def test_protect_console_required_without_a_target(hass: HomeAssistant) -> None:
    """Two Protect consoles and no console_id cannot be routed."""
    _console(hass, "Office", {}, protect=True)
    _console(hass, "Garage", {}, protect=True)

    with pytest.raises(ServiceValidationError) as err:
        resolve_protect_console_call(hass, _call(hass, "create_liveview"))
    assert _key(err) == "protect_console_required"
    assert err.value.translation_placeholders == {"consoles": "Garage, Office"}


async def test_chime_pairing_without_doorbells_unpairs_all(hass: HomeAssistant) -> None:
    """An absent doorbells selection resolves to an empty camera list."""
    entry = _console(hass, "Office", {"protect": {"chimes": {"c1": {}}}}, protect=True)
    facade = entry.runtime_data.coordinator

    target = resolve_chime_pairing_call(hass, _call(hass, "x", chime_id="c1"))

    assert target == ChimePairingTarget(
        entry=entry,
        cache=facade,
        protect_commands=facade.require_protect_coordinator.return_value,
        chime_id="c1",
        camera_ids=(),
    )


async def test_chime_pairing_area_expands_to_its_doorbells(hass: HomeAssistant) -> None:
    """An area selects the doorbells in it; its other entities are skipped."""
    cameras = {
        "bell": {"_camera_type": CAMERA_TYPE_DOORBELL},
        "yard": {},
        "junk": "invalid",
    }
    entry = _console(
        hass,
        "Office",
        {"protect": {"chimes": {"c1": {}}, "cameras": cameras}},
        protect=True,
    )
    porch = ar.async_get(hass).async_create("Porch").id
    ent_reg = er.async_get(hass)
    for unique_id in (
        "unifi_insights_camera_bell_camera_doorbell_ring",
        "unifi_insights_camera_yard_camera_doorbell_ring",
        "unifi_insights_camera_bell_motion",
    ):
        reg_entry = ent_reg.async_get_or_create(
            "binary_sensor", DOMAIN, unique_id, config_entry=entry
        )
        ent_reg.async_update_entity(reg_entry.entity_id, area_id=porch)

    target = resolve_chime_pairing_call(
        hass, _call(hass, "x", chime_id="c1", doorbells={"area_id": porch})
    )

    assert target.camera_ids == ("bell",)


async def test_light_device_does_not_fall_back_to_camera_entities(
    hass: HomeAssistant,
) -> None:
    """Only a camera target may be found through its device's entities."""
    entry = _console(hass, "Office", {"protect": {"lights": {}}}, protect=True)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "nvr_l1")}
    )
    er.async_get(hass).async_get_or_create(
        "light",
        DOMAIN,
        "unifi_insights_camera_l1",
        config_entry=entry,
        device_id=device.id,
    )
    with pytest.raises(ServiceValidationError) as err:
        resolve_protect_call(hass, _call(hass, "x", light_id=device.id), "light")
    assert _key(err) == "protect_resource_unresolved"


_RING = "unifi_insights_camera_{}_camera_doorbell_ring"


@pytest.mark.parametrize(
    ("doorbells", "key", "target"),
    [
        ({"entity_id": "$bell"}, None, None),
        ({"device_id": "nope"}, "chime_doorbell_not_found", "nope"),
        ({"entity_id": "binary_sensor.ghost"}, "chime_doorbell_not_found", "$"),
        ({"entity_id": "$motion"}, "chime_doorbell_not_doorbell", "$"),
        ({"entity_id": "$yard"}, "chime_doorbell_not_doorbell", "$"),
        ({"entity_id": "$gone"}, "chime_doorbell_not_found", "$"),
        ({"entity_id": "$junk"}, "chime_doorbell_not_found", "$"),
        ({"entity_id": "$garage"}, "chime_doorbell_other_console", "$"),
        ({"device_id": "$device"}, "chime_doorbells_none_selected", None),
        ({"entity_id": "$bell"}, "chime_cameras_unavailable", None),
    ],
    ids=[
        "doorbell",
        "unknown-device",
        "unknown-entity",
        "not-a-ring-sensor",
        "camera-not-a-doorbell",
        "camera-gone",
        "camera-malformed",
        "other-console",
        "device-without-doorbells",
        "cameras-not-loaded",
    ],
)
async def test_chime_pairing_doorbell_selection(
    hass: HomeAssistant, doorbells: dict[str, str], key: str | None, target: str | None
) -> None:
    """Named entities must be doorbells of the chime's console; others are skipped."""
    cameras = {"bell": {"_camera_type": CAMERA_TYPE_DOORBELL}, "yard": {}}
    protect = {"chimes": {"c1": {}}}
    if key != "chime_cameras_unavailable":
        protect["cameras"] = cameras
    office = _console(hass, "Office", {"protect": protect}, protect=True)
    garage = _console(hass, "Garage", {"protect": {}}, protect=True)
    ent_reg = er.async_get(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=office.entry_id, identifiers={(DOMAIN, "protect_camera_yard")}
    )
    ids = {"$device": device.id}
    for name, entry, unique_id in (
        ("bell", office, _RING.format("bell")),
        ("yard", office, _RING.format("yard")),
        ("gone", office, _RING.format("gone")),
        ("junk", office, _RING.format("junk")),
        ("garage", garage, _RING.format("far")),
        ("motion", office, "unifi_insights_camera_bell_motion"),
    ):
        ids[f"${name}"] = ent_reg.async_get_or_create(
            "binary_sensor",
            DOMAIN,
            unique_id,
            config_entry=entry,
            device_id=device.id if name == "motion" else None,
        ).entity_id
    selection = {field: ids.get(value, value) for field, value in doorbells.items()}
    call = _call(hass, "x", chime_id="c1", doorbells=selection)

    if key is None:
        assert resolve_chime_pairing_call(hass, call).camera_ids == ("bell",)
        return
    with pytest.raises(HomeAssistantError) as err:
        resolve_chime_pairing_call(hass, call)
    assert err.value.translation_key == key
    expected = next(iter(selection.values())) if target else None
    assert err.value.translation_placeholders == (
        {"target": expected} if expected else None
    )


# --- Rule export and Access --------------------------------------------------


async def test_rule_export_and_access_results(hass: HomeAssistant) -> None:
    """Rule export reads through the facade; Access uses its own owner."""
    entry = _console(hass, "Office", {"sites": {"s1": {}}}, access=True)
    facade = entry.runtime_data.coordinator

    assert resolve_rule_export_call(
        hass, _call(hass, "export_network_rules", site_id="s1")
    ) == RuleExportTarget(
        entry=entry, cache=facade, export_commands=facade, site_id="s1"
    )
    assert resolve_access_call(hass, _call(hass, "x")) == AccessTarget(
        entry=entry, access_commands=entry.runtime_data.access_coordinator
    )


@pytest.mark.parametrize(
    ("consoles", "data", "expected", "placeholders"),
    [
        ({}, {}, "no_network_console", None),
        ({"Office": NET}, {}, ("Office", None), None),
        (
            {"Office": NET, "Garage": NET},
            {"console_id": "garage", "site_id": "s1"},
            ("Garage", "s1"),
            None,
        ),
        (
            {"Office": NET},
            {"console_id": "x"},
            "export_target_not_found",
            {"target": "x"},
        ),
        (
            {"Office": NET},
            {"console_id": "Office", "site_id": "s9"},
            "export_site_not_on_console",
            {"site_id": "s9", "console": "Office"},
        ),
        ({"Office": NET, "Garage": NET}, {}, "export_console_required", None),
        (
            {"Office": NET, "Garage": {"sites": {"s2": {}}}},
            {"site_id": "s2"},
            ("Garage", "s2"),
            None,
        ),
        (
            {"Office": NET},
            {"site_id": "s9"},
            "export_target_not_found",
            {"target": "s9"},
        ),
        (
            {"Office": NET, "Garage": NET},
            {"site_id": "s1"},
            "export_target_ambiguous",
            {"target": "s1"},
        ),
    ],
    ids=[
        "no-console",
        "only-console",
        "console-and-site",
        "unknown-console",
        "site-not-on-console",
        "console-required",
        "site-owner",
        "unknown-site",
        "site-on-two-consoles",
    ],
)
async def test_rule_export_routing(
    hass: HomeAssistant,
    consoles: dict[str, Any],
    data: dict[str, str],
    expected: tuple[str, str | None] | str,
    placeholders: dict[str, str] | None,
) -> None:
    """console_id names the console; otherwise the site, or the only console."""
    entries = {
        title: _console(hass, title, cached) for title, cached in consoles.items()
    }
    call = _call(hass, "export_network_rules", **data)
    if isinstance(expected, str):
        with pytest.raises(ServiceValidationError) as err:
            resolve_rule_export_call(hass, call)
        assert _key(err) == expected
        assert err.value.translation_placeholders == placeholders
        return
    title, site_id = expected
    facade = entries[title].runtime_data.coordinator
    assert resolve_rule_export_call(hass, call) == RuleExportTarget(
        entry=entries[title], cache=facade, export_commands=facade, site_id=site_id
    )


async def test_rule_export_console_id_matching_two_consoles(
    hass: HomeAssistant,
) -> None:
    """A console_id that matches two consoles is ambiguous."""
    for entry_id in ("a1", "a2"):
        entry = MockConfigEntry(domain=DOMAIN, title="A", entry_id=entry_id)
        entry.add_to_hass(hass)
        entry.runtime_data = MagicMock(coordinator=_facade({}))
    with pytest.raises(ServiceValidationError) as err:
        resolve_rule_export_call(hass, _call(hass, "x", console_id="A"))
    assert _key(err) == "export_target_ambiguous"
    assert err.value.translation_placeholders == {"target": "A"}


async def test_access_console_id_selects_the_console(hass: HomeAssistant) -> None:
    """With two Access consoles, console_id picks one."""
    _console(hass, "Office", {}, access=True)
    garage = _console(hass, "Garage", {}, access=True)

    target = resolve_access_call(hass, _call(hass, "x", console_id="Garage"))

    assert target.entry is garage


@pytest.mark.parametrize(
    ("titles", "key"),
    [((), "access_not_configured"), (("Office", "Garage"), "access_console_required")],
)
async def test_access_needs_exactly_one_console(
    hass: HomeAssistant, titles: tuple[str, ...], key: str
) -> None:
    """No Access console, or two without console_id, is refused."""
    _console(hass, "Network only", {})
    for title in titles:
        _console(hass, title, {}, access=True)
    with pytest.raises(ServiceValidationError) as err:
        resolve_access_call(hass, _call(hass, "x"))
    assert _key(err) == key


# --- Fan-out -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "titles", "include_protect"),
    [
        ({}, ("Office", "Garage"), True),
        ({"site_id": ""}, ("Office", "Garage"), False),
        ({"site_id": "s2"}, ("Garage",), False),
    ],
    ids=["absent", "blank", "set"],
)
async def test_refresh_selection(
    hass: HomeAssistant,
    data: dict[str, Any],
    titles: tuple[str, ...],
    include_protect: bool,  # noqa: FBT001
) -> None:
    """Only an absent site refreshes Protect; a blank site selects every console."""
    _console(hass, "Office", {"sites": {"s1": {}}})
    _console(hass, "Garage", {"sites": {"s2": {}}})

    selection = resolve_refresh_call(hass, _call(hass, "refresh_data", **data))

    assert tuple(t.title for t in selection.targets) == titles
    assert selection.include_protect is include_protect
    assert all(
        t.facade_commands is t.entry.runtime_data.coordinator for t in selection.targets
    )


async def test_refresh_errors(hass: HomeAssistant) -> None:
    """No console, or no console with the site, is refused."""
    with pytest.raises(ServiceValidationError) as err:
        resolve_refresh_call(hass, _call(hass, "refresh_data"))
    assert _key(err) == "no_coordinator"

    _console(hass, "Office", {"sites": {"s1": {}}})
    _console(hass, "Empty", {"sites": None})
    with pytest.raises(ServiceValidationError) as err:
        resolve_refresh_call(hass, _call(hass, "refresh_data", site_id="s9"))
    assert _key(err) == "refresh_site_not_found"
    assert err.value.translation_placeholders == {"site_id": "s9"}


async def test_network_consoles_keep_duplicate_sites_and_empty_consoles(
    hass: HomeAssistant,
) -> None:
    """Each usable console is its own target, even with the same site IDs."""
    office = _console(hass, "Office", {"sites": {"default": {}}})
    garage = _console(hass, "Garage", {"sites": {"default": {}}})
    empty = _console(hass, "Empty", {})
    offline = _console(hass, "Offline", {"sites": {"x": {}}})
    offline.runtime_data.coordinator.is_network_usable = False
    carrier = MockConfigEntry(domain=DOMAIN, entry_id="carrier")
    carrier.add_to_hass(hass)
    carrier.runtime_data = CarrierFabricData(
        client=MagicMock(), coordinator=MagicMock()
    )

    targets = resolve_network_consoles_call(hass, _call(hass, "remove_clients"))

    assert [(t.entry, t.site_ids) for t in targets] == [
        (office, ("default",)),
        (garage, ("default",)),
        (empty, ()),
    ]
    assert (
        targets[0].device_commands is office.runtime_data.coordinator.device_coordinator
    )


async def test_network_consoles_require_one_usable_console(hass: HomeAssistant) -> None:
    """Without a console that has Network the removal is refused."""
    offline = _console(hass, "Offline", {})
    offline.runtime_data.coordinator.is_network_usable = False
    with pytest.raises(ServiceValidationError) as err:
        resolve_network_consoles_call(hass, _call(hass, "remove_clients"))
    assert _key(err) == "no_network_console"


async def test_carrier_entries_are_never_consoles(hass: HomeAssistant) -> None:
    """A loaded Carrier Fabric entry is skipped by every console selector."""
    carrier = _carrier_entry(hass, "carrier_a", subscriber=None)

    assert _coord_data(carrier) is None
    assert _console_entries(hass) == []
    with pytest.raises(ServiceValidationError) as err:
        resolve_network_site_call(hass, _call(hass, "x", site_id="s1"))
    assert _key(err) == "no_coordinator"
    with pytest.raises(ServiceValidationError) as err:
        resolve_protect_console_call(hass, _call(hass, "x"))
    assert _key(err) == "no_protect_coordinator"


# --- Ownership states (unknown, known absent, positively owned) -------------


def _entry_with(data: Any, *, runtime: bool = True) -> MagicMock:
    """Return a console entry whose facade holds ``data``."""
    entry = MagicMock()
    entry.runtime_data = (
        MagicMock(coordinator=MagicMock(data=data)) if runtime else None
    )
    return entry


@pytest.mark.parametrize(
    ("data", "has", "owns"),
    [
        (None, True, False),
        ("not-a-dict", True, False),
        ({"devices": "invalid"}, True, False),
        ({"devices": {"s1": "invalid"}}, True, False),
        ({"devices": {"s1": {}}}, False, False),
        ({"devices": {"s1": {"d1": {}}}}, True, True),
        ({"devices": {"s1": {}, "s2": {"d1": {}}}}, False, True),
        ({"devices": {"s1": {"d1": "invalid"}}}, False, False),
        ({"devices": {"s2": {"d1": {}}}}, True, True),
    ],
    ids=[
        "no-data",
        "malformed-data",
        "malformed-section",
        "malformed-site",
        "no-record",
        "record",
        "record-in-other-site",
        "malformed-record",
        "no-site",
    ],
)
def test_device_ownership_states(data: Any, *, has: bool, owns: bool) -> None:
    """Unloaded data keeps a candidate; only cached data proves ownership."""
    entry = _entry_with(data, runtime=data is not None)
    assert _entry_has_device(entry, "s1", "d1") is has
    assert _entry_owns_device(entry, "s1", "d1") is owns


@pytest.mark.parametrize(
    ("data", "has", "owns"),
    [
        (None, True, False),
        ({"clients": "invalid"}, True, False),
        ({"clients": {"s1": "invalid"}}, True, False),
        ({"clients": {"s1": {"c1": {}}}}, True, True),
        ({"clients": {"s1": {}, "s2": {"x": {"mac": "C1"}}}}, False, True),
        ({"clients": {"s1": {"c1": "invalid"}}}, False, False),
        ({"clients": {"s2": {"c1": {}}}}, True, True),
    ],
    ids=[
        "no-data",
        "malformed-section",
        "malformed-site",
        "record",
        "record-in-other-site",
        "malformed-record",
        "no-site",
    ],
)
def test_client_ownership_states(data: Any, *, has: bool, owns: bool) -> None:
    """A client is found by ID or MAC, in its site or in any other."""
    entry = _entry_with(data, runtime=data is not None)
    assert _entry_has_client(entry, "s1", "c1") is has
    assert _entry_owns_client(entry, "s1", "c1") is owns


def test_ownership_without_a_site_scans_every_site() -> None:
    """With no site, any cached site holding the resource counts."""
    entry = _entry_with({"devices": {"s1": {"d1": {}}}, "clients": {"s1": {"c1": {}}}})
    assert _entry_owns_device(entry, None, "d1") is True
    assert _entry_owns_device(entry, None, "d2") is False
    assert _entry_has_client(entry, None, "c1") is True
    assert _entry_owns_client(entry, None, "nope") is False


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"clients": {"s1": {"native": {"mac": "AA:BB:CC:DD:EE:01"}}}}, "native"),
        ({"clients": {"s1": {"aa:bb:cc:dd:ee:01": {}}}}, "aa:bb:cc:dd:ee:01"),
        ({}, "aa:bb:cc:dd:ee:01"),
        ({"clients": "invalid"}, "aa:bb:cc:dd:ee:01"),
        ({"clients": {}}, "aa:bb:cc:dd:ee:01"),
        ({"clients": {"s1": "invalid"}}, "aa:bb:cc:dd:ee:01"),
        ({"clients": {"s1": {"native": "invalid"}}}, "aa:bb:cc:dd:ee:01"),
    ],
    ids=[
        "by-mac",
        "native-id",
        "no-section",
        "malformed-section",
        "no-site",
        "malformed-site",
        "malformed-record",
    ],
)
def test_resolve_native_client_id(data: Any, expected: str) -> None:
    """A MAC resolves to the cached native ID; anything else stays as given."""
    cache = MagicMock(data=data)
    assert _resolve_native_client_id(cache, "s1", "aa:bb:cc:dd:ee:01") == expected


@pytest.mark.parametrize(
    ("data", "has", "owns"),
    [
        ({}, True, False),
        ({"devices": "invalid", "clients": "invalid"}, True, False),
        (
            {"devices": {"s1": "invalid"}, "clients": {"s1": "invalid"}},
            False,
            False,
        ),
        (
            {
                "devices": {"s1": {"r1": "invalid"}},
                "clients": {"s1": {"r1": "invalid"}},
            },
            False,
            False,
        ),
        (
            {
                "devices": {"s1": "invalid", "s2": {"r1": {}}},
                "clients": {"s1": "invalid", "s2": {"r1": {}}},
            },
            True,
            True,
        ),
    ],
    ids=[
        "no-section",
        "malformed-section",
        "malformed-site",
        "malformed-record",
        "record-beside-malformed-site",
    ],
)
def test_ownership_without_a_site_skips_malformed_data(
    data: Any, *, has: bool, owns: bool
) -> None:
    """Without a site, a malformed section, site or record proves nothing."""
    entry = _entry_with(data)
    assert _entry_has_device(entry, None, "r1") is has
    assert _entry_owns_device(entry, None, "r1") is owns
    assert _entry_has_client(entry, None, "r1") is has
    assert _entry_owns_client(entry, None, "r1") is owns


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (None, None),
        ({}, None),
        ({"protect": "invalid"}, None),
        ({"protect": {}}, None),
        ({"protect": {"cameras": "invalid"}}, None),
        ({"protect": {"cameras": {}}}, False),
        ({"protect": {"cameras": {"cam1": "invalid"}}}, False),
        ({"protect": {"cameras": {"cam1": {}}}}, True),
    ],
    ids=[
        "no-data",
        "no-section",
        "malformed-section",
        "no-collection",
        "malformed-collection",
        "no-record",
        "malformed-record",
        "record",
    ],
)
def test_protect_ownership_states(data: Any, *, expected: bool | None) -> None:
    """None means "cannot tell", never owned or disowned."""
    entry = _entry_with(data, runtime=data is not None)
    assert _protect_owns_resource(entry, "cameras", "cam1") is expected


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"protect": {"cameras": {"cam_1": {}}}}, "cam_1"),
        ({}, "cam"),
        ({"protect": "invalid"}, "cam"),
        ({"protect": {"cameras": "invalid"}}, "cam"),
        ({"protect": {"cameras": {"cam_1": "invalid"}}}, "cam"),
    ],
    ids=[
        "record",
        "no-section",
        "malformed-section",
        "malformed-collection",
        "malformed-record",
    ],
)
async def test_protect_entity_target_matches_cached_device_ids(
    hass: HomeAssistant, data: dict[str, Any], expected: str
) -> None:
    """An entity's unique ID resolves to a cached device ID, else its first part."""
    entry = _console(hass, "Office", data, protect=True)
    entity = er.async_get(hass).async_get_or_create(
        "switch", DOMAIN, f"{DOMAIN}_camera_cam_1_status_light", config_entry=entry
    )

    assert _resolve_protect_resource_id(hass, "camera", entity.entity_id, [entry]) == (
        expected,
        entry,
    )


def test_cached_data_of_entries_without_a_usable_facade() -> None:
    """Carrier entries, missing facades and non-dict data have no sections."""
    assert _coord_data(None) is None  # type: ignore[arg-type]
    carrier = MagicMock(
        runtime_data=CarrierFabricData(client=MagicMock(), coordinator=MagicMock())
    )
    assert _coord_data(carrier) is None
    assert _coord_data(MagicMock(runtime_data=MagicMock(coordinator=None))) is None
    entry = _entry_with({"devices": "not-a-dict", "sites": {"s1": {}}})
    assert _coord_section(entry, "devices") is None
    assert _coord_section(entry, "sites") == {"s1": {}}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (123, None),
        ("", None),
        ("   ", None),
        (":::---", None),
        ("AA:BB:CC:DD:EE:FF", "aabbccddeeff"),
        ("aa-bb-cc-dd-ee-ff", "aabbccddeeff"),
        ("aabb.ccdd.eeff", "aabbccddeeff"),
    ],
)
def test_mac_key(value: Any, expected: str | None) -> None:
    """User input MACs compare equal whatever their separators."""
    assert _mac_key(value) == expected


def test_client_records_match_by_id_or_mac() -> None:
    """Records match by key, or by any of the three MAC field spellings."""
    records = {"c1": "not-a-dict", "c2": {"macAddress": "11:22:33:44:55:66"}}
    assert _client_records_match(records, "c1") is True
    assert _client_records_match(records, "11-22-33-44-55-66") is True
    assert _client_records_match(records, "unknown") is False
    assert _client_records_match(records, ":::") is False


@pytest.mark.parametrize("resource", ["network", "camera", "client"])
async def test_raw_targets_without_registries(
    hass: HomeAssistant, resource: str
) -> None:
    """Raw IDs stay usable before the registries are set up."""
    with patch.dict(hass.data):
        hass.data.pop(er.DATA_REGISTRY, None)
        hass.data.pop(dr.DATA_REGISTRY, None)
        if resource == "network":
            result: Any = _resolve_network_device_id(hass, "native", "site", [])
            assert result == ("native", "site", None)
        elif resource == "camera":
            result = _resolve_protect_resource_id(hass, "camera", "native", [])
            assert result == ("native", None)
        else:
            assert _resolve_network_client_id(hass, "native", []) == ("native", None)


async def test_entity_lookup_without_an_entity_registry(hass: HomeAssistant) -> None:
    """An entity ID cannot be resolved while the entity registry is not set up."""
    with patch.dict(hass.data):
        hass.data.pop(er.DATA_REGISTRY, None)
        with pytest.raises(ServiceValidationError) as err:
            _resolve_network_device_id(hass, "sensor.x", None, [])
    assert _key(err) == "target_entity_not_found"


# --- Carrier Fabric ----------------------------------------------------------


def _carrier_entry(
    hass: HomeAssistant,
    entry_id: str,
    *,
    actions: bool = True,
    loaded: bool = True,
    subscriber: str | None = SUB_ID,
) -> MockConfigEntry:
    """Add a Carrier Fabric entry with an autospecced coordinator and a subscriber."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=entry_id,
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            CONF_API_KEY: "key",
            CONF_CARRIER_ORG_ID: entry_id,
        },
        options={CONF_CARRIER_ACTIONS: actions},
    )
    entry.add_to_hass(hass)
    if loaded:
        entry.mock_state(hass, ConfigEntryState.LOADED)
        entry.runtime_data = CarrierFabricData(
            client=MagicMock(),
            coordinator=create_autospec(
                UnifiInsightsCarrierFabricCoordinator, instance=True
            ),
        )
    if subscriber is not None:
        dr.async_get(hass).async_get_or_create(
            config_entry_id=entry_id,
            identifiers={(DOMAIN, f"carrier_subscriber_{subscriber}")},
        )
    return entry


@pytest.mark.parametrize("target", [SUB_ID, f"carrier_subscriber_{SUB_ID}"])
async def test_carrier_identifier_in_one_entry_resolves(
    hass: HomeAssistant, target: str
) -> None:
    """A bare or prefixed subscriber ID found in one entry resolves to it."""
    entry = _carrier_entry(hass, "carrier_a")

    resolved = resolve_carrier_subscriber_call(
        hass, _call(hass, "carrier_resume_subscriber", device_id=target)
    )

    assert resolved.entry is entry
    assert resolved.carrier_commands is entry.runtime_data.coordinator
    assert resolved.subscriber_id == SUB_ID


@pytest.mark.parametrize("target", [SUB_ID, f"carrier_subscriber_{SUB_ID}"])
async def test_carrier_identifier_in_two_entries_is_ambiguous(
    hass: HomeAssistant, target: str
) -> None:
    """The same subscriber identifier in two entries is not guessed."""
    _carrier_entry(hass, "carrier_a")
    _carrier_entry(hass, "carrier_b")

    with pytest.raises(ServiceValidationError, match="more than one") as err:
        resolve_carrier_subscriber_call(
            hass, _call(hass, "carrier_resume_subscriber", device_id=target)
        )
    assert _key(err) == "carrier_target_ambiguous"
    assert err.value.translation_placeholders == {"target": target}


async def test_carrier_entity_of_a_subscriber_resolves(hass: HomeAssistant) -> None:
    """A subscriber entity resolves through its device."""
    entry = _carrier_entry(hass, "carrier_a")
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    entity = er.async_get(hass).async_get_or_create(
        "switch", DOMAIN, "sub", config_entry=entry, device_id=device.id
    )

    resolved = resolve_carrier_subscriber_call(
        hass,
        _call(
            hass,
            "carrier_suspend_subscriber",
            target={"entity_id": [entity.entity_id]},
            entity_id=entity.entity_id,
        ),
    )

    assert resolved.subscriber_id == SUB_ID


@pytest.mark.parametrize(
    ("data", "key"),
    [
        ({}, "carrier_target_required"),
        ({"device_id": ["a", "b"]}, "carrier_multiple_targets"),
        ({"entity_id": "switch.nope"}, "carrier_target_not_found"),
        ({"device_id": "nope"}, "carrier_target_not_found"),
        ({"entity_id": "$entity"}, "carrier_target_not_found"),
    ],
)
async def test_carrier_target_errors(
    hass: HomeAssistant, data: dict[str, Any], key: str
) -> None:
    """Missing, multiple and unknown subscriber targets are refused."""
    entry = _carrier_entry(hass, "carrier_a")
    if data.get("entity_id") == "$entity":
        data = {
            "entity_id": er.async_get(hass)
            .async_get_or_create("sensor", DOMAIN, "orphan", config_entry=entry)
            .entity_id
        }
    with pytest.raises(ServiceValidationError) as err:
        resolve_carrier_subscriber_call(hass, _call(hass, "x", **data))
    assert _key(err) == key


@pytest.mark.parametrize(
    ("setup", "key"),
    [
        ("foreign-entity", "carrier_target_not_subscriber"),
        ("foreign-device", "carrier_target_not_subscriber"),
        ("console-device", "carrier_target_not_subscriber"),
        ("no-subscriber-identifier", "carrier_target_not_subscriber"),
        ("invalid-uuid", "carrier_invalid_subscriber_id"),
        ("foreign-owner", "carrier_entry_not_loaded"),
        ("not-loaded", "carrier_entry_not_loaded"),
    ],
)
async def test_carrier_subscriber_validation(
    hass: HomeAssistant, setup: str, key: str
) -> None:
    """Only a loaded Carrier entry's subscriber with a UUID is a target."""
    dev_reg = dr.async_get(hass)
    if setup == "foreign-entity":
        _unloaded(hass, "foreign", "other")
        target = (
            er.async_get(hass)
            .async_get_or_create(
                "switch",
                "other",
                "x",
                config_entry=hass.config_entries.async_get_entry("foreign"),
            )
            .entity_id
        )
    elif setup == "foreign-device":
        _unloaded(hass, "foreign", "other")
        target = dev_reg.async_get_or_create(
            config_entry_id="foreign", identifiers={("other", "x")}
        ).id
    elif setup == "console-device":
        console = _console(hass, "Office", {})
        target = dev_reg.async_get_or_create(
            config_entry_id=console.entry_id, identifiers={(DOMAIN, "s1_d1")}
        ).id
    elif setup == "no-subscriber-identifier":
        _carrier_entry(hass, "carrier_a", subscriber=None)
        target = dev_reg.async_get_or_create(
            config_entry_id="carrier_a", identifiers={(DOMAIN, "carrier_site_x")}
        ).id
    elif setup == "invalid-uuid":
        _carrier_entry(hass, "carrier_a", subscriber="not-a-uuid")
        target = "carrier_subscriber_not-a-uuid"
    elif setup == "foreign-owner":
        # Our identifier on a device another integration's entry owns.
        _unloaded(hass, "foreign", "other")
        target = (
            dr.async_get(hass)
            .async_get_or_create(
                config_entry_id="foreign",
                identifiers={(DOMAIN, f"carrier_subscriber_{SUB_ID}")},
            )
            .id
        )
    else:
        _carrier_entry(hass, "carrier_a", loaded=False)
        target = SUB_ID

    with pytest.raises(ServiceValidationError) as err:
        resolve_carrier_subscriber_call(hass, _call(hass, "x", device_id=target))
    assert _key(err) == key


async def test_carrier_actions_disabled(hass: HomeAssistant) -> None:
    """A subscriber of an entry with actions disabled is refused, translated."""
    _carrier_entry(hass, "carrier_a", actions=False)
    with pytest.raises(HomeAssistantError) as err:
        resolve_carrier_subscriber_call(hass, _call(hass, "x", device_id=SUB_ID))
    assert err.value.translation_key == "carrier_actions_disabled"


async def test_device_owner_id_reads_the_owning_entry(hass: HomeAssistant) -> None:
    """A registry device's owner is its single config_entry_id."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id="owner")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "s1_d1")}
    )

    assert device.config_entry_id == "owner"
    assert _device_owner_id(device) == "owner"
