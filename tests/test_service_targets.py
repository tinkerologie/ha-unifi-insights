# Copyright 2026 UniFi Insights contributors
"""Tests for the service-call target resolvers in service_targets.py (#339)."""

from __future__ import annotations

import ast
import json
import string
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, create_autospec

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_insights.carrier_fabric_data import CarrierFabricData
from custom_components.unifi_insights.const import (
    CONF_CARRIER_ACTIONS,
    CONF_CARRIER_ORG_ID,
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_CARRIER_FABRIC,
    DOMAIN,
)
from custom_components.unifi_insights.coordinators import (
    UnifiCarrierFabricCoordinator,
    UnifiConfigCoordinator,
    UnifiDeviceCoordinator,
    UnifiInsightsAccessCoordinator,
    UnifiProtectCoordinator,
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
    _select_network_entry,
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

_COMPONENT = Path(__file__).parent.parent / "custom_components" / DOMAIN
SUB_ID = "11111111-2222-3333-4444-555555555555"


def _facade(data: dict[str, Any], *, protect: bool = False) -> MagicMock:
    """Return a facade read view whose command owners are autospecced."""
    facade = MagicMock()
    facade.data = data
    facade.device_coordinator = create_autospec(UnifiDeviceCoordinator, instance=True)
    facade.config_coordinator = create_autospec(UnifiConfigCoordinator, instance=True)
    facade.require_protect_coordinator.return_value = create_autospec(
        UnifiProtectCoordinator, instance=True
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


def _call(hass: HomeAssistant, service: str, **data: Any) -> ServiceCall:
    """Build a service call with already validated data."""
    return ServiceCall(hass, DOMAIN, service, data)


def _key(err: pytest.ExceptionInfo[ServiceValidationError]) -> str | None:
    """Return the translation key of a raised validation error."""
    assert err.value.translation_domain == DOMAIN
    return err.value.translation_key


# --- Typed results ----------------------------------------------------------


async def test_network_device_result_derives_the_site_from_the_cache(
    hass: HomeAssistant,
) -> None:
    """A device without a site_id gets the first cached site that holds it."""
    entry = _console(
        hass,
        "Office",
        {"sites": {"s1": {}, "s2": {}}, "devices": {"s1": {}, "s2": {"d1": {}}}},
    )
    facade = entry.runtime_data.coordinator

    target = resolve_network_device_call(
        hass, _call(hass, "restart_device", device_id="d1"), action="restart"
    )

    assert target == NetworkDeviceTarget(
        entry=entry,
        cache=facade,
        device_commands=facade.device_coordinator,
        device_id="d1",
        site_id="s2",
    )


@pytest.mark.parametrize(
    ("action", "key"),
    [
        ("restart", "restart_site_required"),
        ("power_cycle", "power_cycle_site_required"),
    ],
)
async def test_network_device_without_a_cached_site_is_refused(
    hass: HomeAssistant, action: str, key: str
) -> None:
    """Each action names its own missing-site error."""
    _console(hass, "Office", {"sites": {}, "devices": "not loaded"})

    with pytest.raises(ServiceValidationError) as err:
        resolve_network_device_call(
            hass, _call(hass, "restart_device", device_id="d1"), action=action
        )
    assert _key(err) == key
    assert err.value.translation_placeholders == {"device_id": "d1"}


async def test_network_device_target_is_required(hass: HomeAssistant) -> None:
    """A call without any target raises the translated device error."""
    _console(hass, "Office", {})
    with pytest.raises(ServiceValidationError, match="Device ID or target") as err:
        resolve_network_device_call(
            hass, _call(hass, "restart_device"), action="restart"
        )
    assert _key(err) == "device_target_required"


async def test_network_client_result_holds_the_native_id_and_both_owners(
    hass: HomeAssistant,
) -> None:
    """A MAC target becomes the native client ID of the selected site."""
    entry = _console(
        hass,
        "Office",
        {"sites": {"s1": {}}, "clients": {"s1": {"native": {"mac": "AA:BB:CC"}}}},
    )
    facade = entry.runtime_data.coordinator

    target = resolve_network_client_call(
        hass, _call(hass, "reconnect_client", site_id="s1", client_id="aa-bb-cc")
    )

    assert target == NetworkClientTarget(
        entry=entry,
        cache=facade,
        device_commands=facade.device_coordinator,
        config_commands=facade.config_coordinator,
        client_id="native",
        site_id="s1",
    )


async def test_network_client_target_is_required(hass: HomeAssistant) -> None:
    """A client call without any target raises the translated client error."""
    _console(hass, "Office", {})
    with pytest.raises(ServiceValidationError) as err:
        resolve_network_client_call(hass, _call(hass, "authorize_guest", site_id="s1"))
    assert _key(err) == "client_target_required"


async def test_network_site_result_routes_to_the_console_with_the_site(
    hass: HomeAssistant,
) -> None:
    """Vouchers run on the console whose cached sites hold the site."""
    _console(hass, "Office", {"sites": {"s1": {}}})
    garage = _console(hass, "Garage", {"sites": {"s2": {}}})
    facade = garage.runtime_data.coordinator

    target = resolve_network_site_call(
        hass, _call(hass, "generate_voucher", site_id="s2")
    )

    assert target == NetworkSiteTarget(
        entry=garage, cache=facade, facade_commands=facade, site_id="s2"
    )


@pytest.mark.parametrize(
    ("resource_type", "data"),
    [
        ("camera", {"camera_id": "cam1"}),
        ("light", {"light_id": "light1"}),
        ("chime", {"chime_id": "chime1", "camera_id": [" cam1 "]}),
        ("viewer", {"viewer_id": "viewer1", "liveview_id": "lv1"}),
    ],
)
async def test_protect_result_holds_the_protect_owner(
    hass: HomeAssistant, resource_type: Any, data: dict[str, Any]
) -> None:
    """The Protect owner comes from the selected console's facade."""
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


async def test_protect_secondary_on_another_console_is_refused(
    hass: HomeAssistant,
) -> None:
    """A chime's camera_id must be on the chime's console."""
    office = {"protect": {"chimes": {"chime1": {}}, "cameras": {}}}
    _console(hass, "Office", office, protect=True)
    garage = {"protect": {"chimes": {}, "cameras": {"cam9": {}}}}
    _console(hass, "Garage", garage, protect=True)

    with pytest.raises(ServiceValidationError) as err:
        resolve_protect_call(
            hass, _call(hass, "x", chime_id="chime1", camera_id="cam9"), "chime"
        )
    assert _key(err) == "protect_secondary_other_console"
    assert err.value.translation_placeholders == {
        "secondary_type": "camera",
        "secondary_id": "cam9",
        "resource_type": "chime",
        "resource_id": "chime1",
    }


async def test_protect_console_id_skips_the_ownership_checks(
    hass: HomeAssistant,
) -> None:
    """An explicit console_id picks that console without any further check."""
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


async def test_chime_pairing_target_is_required(hass: HomeAssistant) -> None:
    """The chime pairing action reuses the Protect target error."""
    _console(hass, "Office", {}, protect=True)
    with pytest.raises(ServiceValidationError, match="Chime ID or target") as err:
        resolve_chime_pairing_call(hass, _call(hass, "x"))
    assert _key(err) == "protect_target_required"
    assert err.value.translation_placeholders == {"resource_type": "chime"}


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


async def test_access_console_id_selects_the_console(hass: HomeAssistant) -> None:
    """With two Access consoles, console_id picks one."""
    _console(hass, "Office", {}, access=True)
    garage = _console(hass, "Garage", {}, access=True)

    target = resolve_access_call(hass, _call(hass, "x", console_id="Garage"))

    assert target.entry is garage


# --- Fan-out ----------------------------------------------------------------


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


# --- Network selection edge cases -------------------------------------------


@pytest.mark.parametrize(
    ("routing", "consoles", "key", "placeholders"),
    [
        (
            {},
            (
                {"sites": {"s1": {}}},
                {"sites": {"s2": {}}},
            ),
            "network_console_ambiguous",
            None,
        ),
        (
            {"device_id": "d1"},
            (
                {"devices": {"s1": {}}},
                {"devices": {"s2": {}}},
            ),
            "device_not_on_any_console",
            {"device_id": "d1"},
        ),
        (
            {"device_id": "d1"},
            (
                {"devices": "unknown"},
                {"devices": "unknown"},
            ),
            "device_ambiguous",
            {"device_id": "d1"},
        ),
        (
            {"site_id": "s1"},
            (
                {"sites": "unknown"},
                {"sites": "unknown"},
            ),
            "site_consoles_ambiguous",
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


# --- Registry ownership (config_entry_id, entry-scoped lookups) -------------


async def test_device_of_a_foreign_entry_is_refused(hass: HomeAssistant) -> None:
    """A device carrying our identifier but owned by another entry is not loaded."""
    _console(hass, "Office", {"devices": {"s1": {"d1": {}}}})
    foreign = MockConfigEntry(domain="other", entry_id="foreign")
    foreign.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=foreign.entry_id, identifiers={(DOMAIN, "s1_d1")}
    )

    with pytest.raises(ServiceValidationError, match="not loaded") as err:
        resolve_network_device_call(
            hass, _call(hass, "restart_device", device_id=device.id), action="restart"
        )
    assert _key(err) == "target_console_not_loaded"


async def test_device_of_an_unloaded_console_is_refused(hass: HomeAssistant) -> None:
    """A device of a console entry without runtime data is not loaded."""
    _console(hass, "Office", {"devices": {"s1": {"d1": {}}}})
    unloaded = MockConfigEntry(domain=DOMAIN, entry_id="unloaded")
    unloaded.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=unloaded.entry_id, identifiers={(DOMAIN, "s1_d1")}
    )

    with pytest.raises(ServiceValidationError) as err:
        resolve_network_device_call(
            hass, _call(hass, "restart_device", device_id=device.id), action="restart"
        )
    assert _key(err) == "target_console_not_loaded"


async def test_same_identifier_in_two_entries_routes_to_the_device_owner(
    hass: HomeAssistant,
) -> None:
    """Identifiers are per entry, so the targeted device decides the console."""
    devices = {"sites": {"s1": {}}, "devices": {"s1": {"d1": {}}}}
    _console(hass, "Office", devices)
    garage = _console(hass, "Garage", devices)
    dev_reg = dr.async_get(hass)
    dev_reg.async_get_or_create(
        config_entry_id="office", identifiers={(DOMAIN, "s1_d1")}
    )
    garage_device = dev_reg.async_get_or_create(
        config_entry_id=garage.entry_id, identifiers={(DOMAIN, "s1_d1")}
    )

    target = resolve_network_device_call(
        hass,
        _call(hass, "restart_device", device_id=garage_device.id),
        action="restart",
    )

    assert target.entry is garage
    assert (target.device_id, target.site_id) == ("d1", "s1")


async def test_entity_of_a_foreign_integration_is_refused(hass: HomeAssistant) -> None:
    """An entity of another integration is not a Network target."""
    _console(hass, "Office", {})
    entity = er.async_get(hass).async_get_or_create("switch", "other", "x")

    with pytest.raises(ServiceValidationError) as err:
        resolve_network_device_call(
            hass,
            _call(hass, "restart_device", entity_id=entity.entity_id),
            action="restart",
        )
    assert _key(err) == "target_not_integration_entity"
    assert err.value.translation_placeholders == {"target": entity.entity_id}


def _carrier_entry(hass: HomeAssistant, entry_id: str) -> MockConfigEntry:
    """Add a loaded Carrier Fabric entry with an autospecced coordinator."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=entry_id,
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            CONF_API_KEY: "key",
            CONF_CARRIER_ORG_ID: entry_id,
        },
        options={CONF_CARRIER_ACTIONS: True},
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = CarrierFabricData(
        client=MagicMock(),
        coordinator=create_autospec(UnifiCarrierFabricCoordinator, instance=True),
    )
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry_id,
        identifiers={(DOMAIN, f"carrier_subscriber_{SUB_ID}")},
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


# --- Translations -----------------------------------------------------------


def _literal_keys(module: str) -> set[str]:
    """Return every literal translation key a module passes to an error."""
    keys: set[str] = set()
    for node in ast.walk(ast.parse((_COMPONENT / module).read_text())):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", getattr(node.func, "attr", ""))
        if name == "_invalid":
            candidates = [node.args[1]]
        else:
            candidates = [k.value for k in node.keywords if k.arg == "translation_key"]
        for value in candidates:
            branches = (
                [value.body, value.orelse] if isinstance(value, ast.IfExp) else [value]
            )
            keys |= {
                b.value
                for b in branches
                if isinstance(b, ast.Constant) and isinstance(b.value, str)
            }
    return keys


@pytest.mark.parametrize("module", ["services.py", "service_targets.py"])
def test_service_error_keys_are_translated(module: str) -> None:
    """Every key the service modules raise exists, the same, in both files."""
    strings = json.loads((_COMPONENT / "strings.json").read_text())["exceptions"]
    english = json.loads((_COMPONENT / "translations" / "en.json").read_text())[
        "exceptions"
    ]
    keys = _literal_keys(module)

    assert keys
    assert sorted(k for k in keys if k not in strings) == []
    assert all(strings[k] == english[k] for k in keys)


def test_new_resolver_messages_name_their_placeholders() -> None:
    """The placeholders each new key's message uses are the ones raised."""
    strings = json.loads((_COMPONENT / "strings.json").read_text())["exceptions"]
    expected = {
        "carrier_target_ambiguous": {"target"},
        "protect_secondary_other_console": {
            "secondary_type",
            "secondary_id",
            "resource_type",
            "resource_id",
        },
        "console_not_found": {"console_id", "consoles"},
        "device_site_ambiguous": {"device_id", "site_id"},
        "restart_site_required": {"device_id"},
        "power_cycle_site_required": {"device_id"},
        "refresh_site_not_found": {"site_id"},
        "target_multiple": {"service"},
    }
    for key, fields in expected.items():
        message = strings[key]["message"]
        assert {f for _, f, _, _ in string.Formatter().parse(message) if f} == fields
