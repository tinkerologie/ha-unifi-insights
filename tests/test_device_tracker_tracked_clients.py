# Copyright 2026 UniFi Insights contributors
"""
Tracked clients filter of the device tracker platform (issue #511, MAC slice).

The Wi-Fi and wired options decide which client types are tracked. A saved
Tracked clients filter then narrows that to the selected MACs. Removing a
tracker also removes its own `client_<mac>` device, but only when nothing else
is left on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from homeassistant.const import STATE_HOME, STATE_NOT_HOME
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.unifi_insights.const import (
    CONF_TRACK_WIFI_CLIENTS,
    CONF_TRACK_WIRED_CLIENTS,
    CONF_TRACKED_CLIENTS,
    DOMAIN,
)
from custom_components.unifi_insights.data_transforms import tracked_client_macs
from custom_components.unifi_insights.device_tracker import (
    UnifiClientTracker,
    _client_should_be_tracked,
    _partition_connected_clients,
    async_setup_entry,
)
from tests.fixtures.assertions import require_not_none

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

PHONE = "aa:bb:cc:00:00:01"
LAPTOP = "aa:bb:cc:00:00:02"
DESKTOP = "aa:bb:cc:00:00:03"
ENTRY_ID = "tracked_clients_entry"


def _client(mac: str, client_type: str = "WIRELESS", **fields: Any) -> dict[str, Any]:
    """Build a connected-client record."""
    return {"id": f"id-{mac}", "macAddress": mac, "type": client_type, **fields}


def _coordinator(*clients: dict[str, Any]) -> MagicMock:
    """Return a facade coordinator mock whose snapshot has these clients."""
    coordinator = MagicMock()
    coordinator.data = {
        "clients": {"site1": {client["id"]: client for client in clients}}
    }
    return coordinator


def _entry(
    hass: HomeAssistant,
    coordinator: MagicMock,
    options: dict[str, Any],
    entry_id: str = ENTRY_ID,
) -> MockConfigEntry:
    """Build a config entry wired to the coordinator."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": "remote", "console_id": "c", "api_key": "k"},
        options=options,
        entry_id=entry_id,
    )
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock()
    entry.runtime_data.mobility_coordinator = None
    entry.runtime_data.coordinator = coordinator
    return entry


def _client_device(
    hass: HomeAssistant, entry: MockConfigEntry, mac: str
) -> dr.DeviceEntry:
    """Create the standalone `client_<mac>` device of an entry."""
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"client_{mac}")},
        name=f"Client {mac}",
    )


def _register(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mac: str,
    *,
    unique_id: str | None = None,
    device_id: str | None = None,
) -> str:
    """Register a client tracker row and return its entity_id."""
    return (
        er.async_get(hass)
        .async_get_or_create(
            "device_tracker",
            DOMAIN,
            unique_id or f"{DOMAIN}_{mac}",
            config_entry=entry,
            device_id=device_id,
        )
        .entity_id
    )


def _get_client_device(
    hass: HomeAssistant, entry_id: str, mac: str
) -> dr.DeviceEntry | None:
    return dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"client_{mac}"), entry_id
    )


def _added(add_entities: MagicMock) -> list[UnifiClientTracker]:
    """Return every tracker passed to async_add_entities, in order."""
    return [entity for call in add_entities.call_args_list for entity in call.args[0]]


# --- The rule -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("client_type", "track_wifi", "track_wired", "expected"),
    [
        ("WIRELESS", True, False, True),
        ("WIRELESS", False, True, False),
        ("WIRED", False, True, True),
        ("WIRED", True, False, False),
        ("VPN", True, False, True),
        ("VPN", False, False, False),
    ],
)
def test_empty_filter_keeps_the_type_result(
    client_type: str, *, track_wifi: bool, track_wired: bool, expected: bool
) -> None:
    """With no saved filter, the Wi-Fi and wired options alone decide."""
    assert (
        _client_should_be_tracked(
            _client(PHONE, client_type),
            track_wifi=track_wifi,
            track_wired=track_wired,
            tracked_clients=frozenset(),
        )
        is expected
    )


@pytest.mark.parametrize(
    ("mac", "expected"),
    [(PHONE, True), (PHONE.upper(), True), (LAPTOP, False), ("", False)],
    ids=["selected", "selected_uppercase_payload", "not_selected", "no_mac"],
)
def test_filter_requires_a_selected_mac(mac: str, *, expected: bool) -> None:
    """A saved filter tracks a client of an enabled type only if it is listed."""
    assert (
        _client_should_be_tracked(
            _client(mac),
            track_wifi=True,
            track_wired=False,
            tracked_clients=frozenset({PHONE}),
        )
        is expected
    )


def test_type_rule_still_applies_to_a_selected_client() -> None:
    """Selecting a wired client does not track it while wired tracking is off."""
    assert not _client_should_be_tracked(
        _client(PHONE, "WIRED"),
        track_wifi=True,
        track_wired=False,
        tracked_clients=frozenset({PHONE}),
    )


def test_tracked_client_macs_normalises_the_saved_option() -> None:
    """The option reads as lowercase MACs; a missing or junk value is empty."""
    assert tracked_client_macs({}) == frozenset()
    assert tracked_client_macs({CONF_TRACKED_CLIENTS: None}) == frozenset()
    assert tracked_client_macs(
        {CONF_TRACKED_CLIENTS: [PHONE.upper(), PHONE, "", None, 3]}
    ) == frozenset({PHONE})


def test_partition_puts_unselected_clients_in_the_untracked_set() -> None:
    """An unselected connected client of a tracked type counts as untracked."""
    wanted, untracked = _partition_connected_clients(
        _coordinator(_client(PHONE), _client(LAPTOP)),
        track_wifi=True,
        track_wired=False,
        tracked_clients=frozenset({PHONE}),
    )

    assert wanted == {PHONE: "site1"}
    assert untracked == {LAPTOP}


# --- Setup reconciliation -----------------------------------------------------


async def test_deselected_offline_tracker_is_removed(hass: HomeAssistant) -> None:
    """The filter alone settles an absent row: an unselected MAC goes."""
    entry = _entry(
        hass,
        _coordinator(),
        {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE]},
    )
    kept = _register(hass, entry, PHONE)
    removed = _register(hass, entry, LAPTOP)

    await async_setup_entry(hass, entry, MagicMock())

    registry = er.async_get(hass)
    assert registry.async_get(kept) is not None
    assert registry.async_get(removed) is None


async def test_absent_tracker_is_kept_without_a_filter(hass: HomeAssistant) -> None:
    """No saved filter keeps every absent row, as before."""
    entry = _entry(hass, _coordinator(), {CONF_TRACK_WIFI_CLIENTS: True})
    entity_id = _register(hass, entry, LAPTOP)

    await async_setup_entry(hass, entry, MagicMock())

    assert er.async_get(hass).async_get(entity_id) is not None


async def test_deselected_legacy_bare_mac_row_is_removed(hass: HomeAssistant) -> None:
    """A row keyed by the API's bare, uppercase MAC is matched by its MAC."""
    entry = _entry(
        hass,
        _coordinator(),
        {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE]},
    )
    device = _client_device(hass, entry, LAPTOP)
    entity_id = _register(
        hass, entry, LAPTOP, unique_id=LAPTOP.upper(), device_id=device.id
    )

    await async_setup_entry(hass, entry, MagicMock())

    assert er.async_get(hass).async_get(entity_id) is None
    assert _get_client_device(hass, entry.entry_id, LAPTOP) is None


async def test_connected_unselected_client_is_removed_and_not_readded(
    hass: HomeAssistant,
) -> None:
    """A connected client outside the filter loses its row and gets no entity."""
    entry = _entry(
        hass,
        _coordinator(_client(PHONE), _client(LAPTOP)),
        {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE]},
    )
    entity_id = _register(hass, entry, LAPTOP)
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    assert er.async_get(hass).async_get(entity_id) is None
    assert [tracker.mac_address for tracker in _added(add_entities)] == [PHONE]


async def test_selected_offline_client_without_a_row_gets_a_tracker(
    hass: HomeAssistant,
) -> None:
    """A selected MAC with no row and no record still gets an offline tracker."""
    entry = _entry(
        hass,
        _coordinator(),
        {CONF_TRACK_WIRED_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE]},
    )
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    [tracker] = _added(add_entities)
    assert tracker.mac_address == PHONE
    assert tracker.unique_id == f"{DOMAIN}_{PHONE}"
    assert tracker.is_connected is False


async def test_selected_offline_client_with_a_row_is_not_duplicated(
    hass: HomeAssistant,
) -> None:
    """A selected MAC that kept its row gets exactly the restored tracker."""
    entry = _entry(
        hass,
        _coordinator(),
        {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE]},
    )
    entity_id = _register(hass, entry, PHONE)
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    assert er.async_get(hass).async_get(entity_id) is not None
    assert [tracker.mac_address for tracker in _added(add_entities)] == [PHONE]


@pytest.mark.parametrize(
    ("client", "expected"),
    [
        pytest.param(_client(PHONE), [PHONE], id="present_eligible_added_once"),
        pytest.param(_client(PHONE, "WIRED"), [], id="present_wrong_type"),
    ],
)
async def test_selected_present_client_follows_the_type_options(
    hass: HomeAssistant, client: dict[str, Any], expected: list[str]
) -> None:
    """A selected connected client is not created offline; types decide."""
    entry = _entry(
        hass,
        _coordinator(client),
        {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE]},
    )
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    assert [tracker.mac_address for tracker in _added(add_entities)] == expected


async def test_selected_client_gets_no_tracker_while_tracking_is_off(
    hass: HomeAssistant,
) -> None:
    """With both type options off, a saved filter creates nothing."""
    entry = _entry(hass, _coordinator(), {CONF_TRACKED_CLIENTS: [PHONE]})
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    add_entities.assert_not_called()


async def test_listener_adds_only_selected_clients(hass: HomeAssistant) -> None:
    """A client connecting later is added only if it is selected."""
    coordinator = _coordinator()
    entry = _entry(
        hass,
        coordinator,
        {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [LAPTOP]},
    )
    add_entities = MagicMock()
    await async_setup_entry(hass, entry, add_entities)
    [listener] = [
        call.args[0] for call in coordinator.async_add_listener.call_args_list
    ]
    add_entities.reset_mock()

    coordinator.data = _coordinator(_client(PHONE), _client(LAPTOP)).data
    listener()

    # LAPTOP already has its offline tracker from setup; PHONE is not selected.
    add_entities.assert_not_called()


# --- Device removal -----------------------------------------------------------


@pytest.mark.parametrize(
    ("options", "clients", "unique_id"),
    [
        pytest.param(
            {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [LAPTOP]},
            (),
            f"{DOMAIN}_{PHONE}",
            id="filter",
        ),
        pytest.param(
            {CONF_TRACK_WIFI_CLIENTS: True},
            (_client(PHONE, "WIRED"),),
            f"{DOMAIN}_{PHONE}",
            id="type_filter",
        ),
        pytest.param({}, (), f"{DOMAIN}_{PHONE}", id="tracking_disabled"),
        # The all-disabled path runs before legacy rows are re-keyed.
        pytest.param({}, (), PHONE.upper(), id="tracking_disabled_legacy_row"),
    ],
)
async def test_every_removal_path_removes_the_empty_client_device(
    hass: HomeAssistant,
    options: dict[str, Any],
    clients: tuple[dict[str, Any], ...],
    unique_id: str,
) -> None:
    """Filter, type and all-disabled cleanup each remove the empty device."""
    entry = _entry(hass, _coordinator(*clients), options)
    device = _client_device(hass, entry, PHONE)
    entity_id = _register(hass, entry, PHONE, unique_id=unique_id, device_id=device.id)

    await async_setup_entry(hass, entry, MagicMock())

    assert er.async_get(hass).async_get(entity_id) is None
    assert _get_client_device(hass, entry.entry_id, PHONE) is None


@pytest.mark.parametrize(
    "disabled_by",
    [None, er.RegistryEntryDisabler.USER],
    ids=["enabled_sibling", "disabled_sibling"],
)
async def test_client_device_with_another_entity_is_kept(
    hass: HomeAssistant, disabled_by: er.RegistryEntryDisabler | None
) -> None:
    """A sibling entity on the client device keeps it, even a disabled one."""
    entry = _entry(
        hass,
        _coordinator(),
        {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [LAPTOP]},
    )
    device = _client_device(hass, entry, PHONE)
    entity_id = _register(hass, entry, PHONE, device_id=device.id)
    er.async_get(hass).async_get_or_create(
        "switch",
        DOMAIN,
        f"block_{PHONE}",
        config_entry=entry,
        device_id=device.id,
        disabled_by=disabled_by,
    )

    await async_setup_entry(hass, entry, MagicMock())

    assert er.async_get(hass).async_get(entity_id) is None
    assert _get_client_device(hass, entry.entry_id, PHONE) is not None


async def test_shared_uplink_device_is_kept(hass: HomeAssistant) -> None:
    """A grouped tracker's uplink switch is never removed with the tracker."""
    entry = _entry(
        hass,
        _coordinator(),
        {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [LAPTOP]},
    )
    uplink = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "site1_switch1")},
        name="Switch",
    )
    entity_id = _register(hass, entry, PHONE, device_id=uplink.id)

    await async_setup_entry(hass, entry, MagicMock())

    assert er.async_get(hass).async_get(entity_id) is None
    assert dr.async_get(hass).async_get(uplink.id) is not None


async def test_another_entrys_client_device_is_kept(hass: HomeAssistant) -> None:
    """Only this entry's `client_<mac>` device is considered for removal."""
    other = _entry(hass, _coordinator(), {}, entry_id="other_entry")
    other_device = _client_device(hass, other, PHONE)
    entry = _entry(
        hass,
        _coordinator(),
        {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [LAPTOP]},
    )
    entity_id = _register(hass, entry, PHONE)

    await async_setup_entry(hass, entry, MagicMock())

    assert er.async_get(hass).async_get(entity_id) is None
    assert dr.async_get(hass).async_get(other_device.id) is not None


# --- Through Home Assistant's entity platform ----------------------------------


def _network_client(mock_network_client: MagicMock, *clients: dict[str, Any]) -> None:
    mock_network_client.sites.get_all.return_value = [
        {"id": "default", "name": "Default"}
    ]
    mock_network_client.clients.get_all.return_value = list(clients)


async def test_selected_client_goes_not_home_and_keeps_its_tracker(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    *,
    mock_local_auth: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A selected client that disconnects shows not_home, not a removal."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE]},
    )
    _network_client(mock_network_client, _client(PHONE, name="Phone"))

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "device_tracker", DOMAIN, f"{DOMAIN}_{PHONE}"
    )
    assert entity_id is not None
    assert (state := hass.states.get(entity_id)) is not None
    assert state.state == STATE_HOME

    mock_network_client.clients.get_all.return_value = []
    await mock_config_entry.runtime_data.device_coordinator.async_refresh()
    await hass.async_block_till_done()

    assert (state := hass.states.get(entity_id)) is not None
    assert state.state == STATE_NOT_HOME
    assert registry.async_get(entity_id) is not None

    # A restart while it is away keeps it too.
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert (state := hass.states.get(entity_id)) is not None
    assert state.state == STATE_NOT_HOME


async def test_selected_offline_client_reports_not_home_on_the_platform(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    *,
    mock_local_auth: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A selected MAC never seen gets a not_home tracker on its client device."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE]},
    )
    _network_client(mock_network_client)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = er.async_get(hass).async_get_entity_id(
        "device_tracker", DOMAIN, f"{DOMAIN}_{PHONE}"
    )
    assert entity_id is not None
    assert (state := hass.states.get(entity_id)) is not None
    assert state.state == STATE_NOT_HOME
    assert state.attributes["friendly_name"] == f"Client {PHONE}"
    device = _get_client_device(hass, mock_config_entry.entry_id, PHONE)
    assert device is not None
    assert require_not_none(er.async_get(hass).async_get(entity_id)).device_id == (
        device.id
    )


# --- Review round 3 -----------------------------------------------------------


def _sites_coordinator(*sites: tuple[str, tuple[dict[str, Any], ...]]) -> MagicMock:
    """Return a facade coordinator mock with these sites, in this order."""
    coordinator = MagicMock()
    coordinator.data = {
        "clients": {
            site_id: {client["id"]: client for client in clients}
            for site_id, clients in sites
        }
    }
    return coordinator


@pytest.mark.parametrize(
    "unique_id",
    [f"{DOMAIN}_{PHONE}", PHONE.upper()],
    ids=["canonical_row", "legacy_bare_mac_row"],
)
async def test_offline_tracker_skips_a_mac_another_entry_owns(
    hass: HomeAssistant, unique_id: str
) -> None:
    """Another entry's row for a selected MAC is never taken over offline."""
    other = _entry(hass, _coordinator(), {}, entry_id="other_entry")
    other_entity_id = _register(hass, other, PHONE, unique_id=unique_id)
    entry = _entry(
        hass,
        _coordinator(),
        {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE, LAPTOP]},
    )
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    # LAPTOP is nobody's, so it still gets its offline tracker.
    assert [tracker.mac_address for tracker in _added(add_entities)] == [LAPTOP]
    reg_entry = require_not_none(er.async_get(hass).async_get(other_entity_id))
    assert reg_entry.config_entry_id == other.entry_id


@pytest.mark.parametrize(
    ("domain", "platform", "unique_id"),
    [
        pytest.param("device_tracker", "nmap_tracker", PHONE, id="other_integration"),
        pytest.param("button", DOMAIN, f"{DOMAIN}_{PHONE}", id="other_domain"),
    ],
)
async def test_offline_tracker_ignores_rows_that_are_not_client_trackers(
    hass: HomeAssistant, domain: str, platform: str, unique_id: str
) -> None:
    """Only this platform's tracker rows can hold a MAC's offline tracker back."""
    other = _entry(hass, _coordinator(), {}, entry_id="other_entry")
    er.async_get(hass).async_get_or_create(
        domain, platform, unique_id, config_entry=other
    )
    entry = _entry(
        hass,
        _coordinator(),
        {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE]},
    )
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    assert [tracker.mac_address for tracker in _added(add_entities)] == [PHONE]


@pytest.mark.parametrize(
    ("client_type", "expected"),
    [("WIRED", False), ("WIRELESS", True)],
)
@pytest.mark.parametrize(
    ("registered", "options"),
    [
        pytest.param(
            False,
            {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE]},
            id="offline_new",
        ),
        pytest.param(
            True,
            {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE]},
            id="kept_row",
        ),
        pytest.param(True, {CONF_TRACK_WIFI_CLIENTS: True}, id="kept_row_no_filter"),
    ],
)
async def test_tracker_created_offline_applies_the_type_rule_when_seen(
    hass: HomeAssistant,
    client_type: str,
    options: dict[str, Any],
    *,
    expected: bool,
    registered: bool,
) -> None:
    """A tracker set up with its client absent is home only for a tracked type."""
    coordinator = _coordinator()
    entry = _entry(hass, coordinator, options)
    if registered:
        _register(hass, entry, PHONE)
    add_entities = MagicMock()
    await async_setup_entry(hass, entry, add_entities)
    [tracker] = _added(add_entities)
    assert tracker.is_connected is False

    coordinator.data = _coordinator(_client(PHONE, client_type)).data

    assert tracker.is_connected is expected


async def test_type_rule_counts_every_site_record_of_the_mac(
    hass: HomeAssistant,
) -> None:
    """An offline-created tracker is home when any site shows a tracked type."""
    coordinator = _coordinator()
    entry = _entry(
        hass,
        coordinator,
        {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE]},
    )
    add_entities = MagicMock()
    await async_setup_entry(hass, entry, add_entities)
    [tracker] = _added(add_entities)

    # The wired record is found first: it must not decide alone.
    coordinator.data = _sites_coordinator(
        ("site_wired", (_client(PHONE, "WIRED"),)),
        ("site_wifi", (_client(PHONE),)),
    ).data

    assert tracker.is_connected is True


async def test_type_rule_ignores_a_malformed_clients_section(
    hass: HomeAssistant,
) -> None:
    """A clients section that is not a mapping reads as absent, not an error."""
    coordinator = _coordinator()
    entry = _entry(
        hass,
        coordinator,
        {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE]},
    )
    add_entities = MagicMock()
    await async_setup_entry(hass, entry, add_entities)
    [tracker] = _added(add_entities)

    coordinator.data = {"clients": [_client(PHONE)]}

    assert tracker.is_connected is False


@pytest.mark.parametrize("registered", [False, True], ids=["new", "kept_row"])
async def test_tracker_set_up_from_a_live_record_skips_the_type_rule(
    hass: HomeAssistant, *, registered: bool
) -> None:
    """A tracker whose client was present at setup keeps the existing behaviour."""
    coordinator = _coordinator(_client(PHONE))
    entry = _entry(
        hass,
        coordinator,
        {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE]},
    )
    if registered:
        _register(hass, entry, PHONE)
    add_entities = MagicMock()
    await async_setup_entry(hass, entry, add_entities)
    [tracker] = _added(add_entities)

    coordinator.data = _coordinator(_client(PHONE, "WIRED")).data

    assert tracker.is_connected is True


@pytest.mark.parametrize(
    "site_order",
    [("site_wifi", "site_wired"), ("site_wired", "site_wifi")],
    ids=["eligible_first", "excluded_first"],
)
@pytest.mark.parametrize(
    "options",
    [
        {CONF_TRACK_WIFI_CLIENTS: True},
        {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE]},
    ],
    ids=["no_filter", "filter"],
)
async def test_mac_eligible_on_one_site_keeps_its_row_and_device(
    hass: HomeAssistant, site_order: tuple[str, str], options: dict[str, Any]
) -> None:
    """A record of a tracked type anywhere outweighs an excluded one elsewhere."""
    records = {
        "site_wifi": (_client(PHONE),),
        "site_wired": (_client(PHONE, "WIRED"),),
    }
    coordinator = _sites_coordinator(*((site, records[site]) for site in site_order))
    entry = _entry(hass, coordinator, options)
    device = _client_device(hass, entry, PHONE)
    entity_id = _register(hass, entry, PHONE, device_id=device.id)
    registry = er.async_get(hass)
    registry.async_update_entity(entity_id, name="My phone")
    original = require_not_none(registry.async_get(entity_id))
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    kept = require_not_none(registry.async_get(entity_id))
    assert kept.id == original.id
    assert kept.name == "My phone"
    assert dr.async_get(hass).async_get(device.id) is not None
    [tracker] = _added(add_entities)
    assert tracker.mac_address == PHONE
    assert tracker.is_connected is True


def test_partition_never_marks_an_eligible_mac_untracked() -> None:
    """A MAC wanted on one site is not also in the untracked set."""
    wanted, untracked = _partition_connected_clients(
        _sites_coordinator(
            ("site_wifi", (_client(PHONE),)),
            ("site_wired", (_client(PHONE, "WIRED"), _client(LAPTOP, "WIRED"))),
        ),
        track_wifi=True,
        track_wired=False,
    )

    assert wanted == {PHONE: "site_wifi"}
    assert untracked == {LAPTOP}


async def test_offline_tracker_seen_wired_is_away_then_removed_on_reload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    *,
    mock_local_auth: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """Wi-Fi only: a selected MAC that turns up wired is Away, then removed."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE]},
    )
    _network_client(mock_network_client)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = require_not_none(
        registry.async_get_entity_id("device_tracker", DOMAIN, f"{DOMAIN}_{PHONE}")
    )

    mock_network_client.clients.get_all.return_value = [_client(PHONE, "WIRED")]
    await mock_config_entry.runtime_data.device_coordinator.async_refresh()
    await hass.async_block_till_done()

    assert require_not_none(hass.states.get(entity_id)).state == STATE_NOT_HOME

    # The next reconciliation sees a present client of an untracked type.
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(entity_id) is None
    assert _get_client_device(hass, mock_config_entry.entry_id, PHONE) is None
