# Copyright 2026 UniFi Insights contributors
"""
Tracked clients option of the options flow (issue #511, MAC slice).

The selector lists connected clients, this entry's client trackers and saved
MACs. Saved values are suggested, not defaults, so the filter can be cleared.
A change goes through the normal reload, never the in-place grouping path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.unifi_insights import _applied_options
from custom_components.unifi_insights.const import (
    CONF_CONNECTION_TYPE,
    CONF_GROUP_DEVICES_BY_UPLINK,
    CONF_TRACK_WIFI_CLIENTS,
    CONF_TRACKED_CLIENTS,
    CONNECTION_TYPE_CARRIER_FABRIC,
    DOMAIN,
    MOBILITY_DEVICE_PREFIX,
)
from custom_components.unifi_insights.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.fixtures.assertions import require_not_none

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigFlowResult
    from homeassistant.core import HomeAssistant

PHONE = "aa:bb:cc:00:00:01"
LAPTOP = "aa:bb:cc:00:00:02"
TABLET = "aa:bb:cc:00:00:03"
AWAY = "aa:bb:cc:00:00:04"
BASE_INPUT = {
    "track_wifi_clients": True,
    "track_wired_clients": False,
    "client_control": True,
}


def _client(mac: str, **fields: Any) -> dict[str, Any]:
    # The record ID must not embed the MAC, or diagnostics would show it.
    return {"id": f"client-{mac[-2:]}", "macAddress": mac, "type": "WIRELESS", **fields}


def _attach_clients(entry: MockConfigEntry, *clients: dict[str, Any]) -> None:
    """Give a config entry runtime data whose snapshot has these clients."""
    entry.runtime_data = MagicMock()
    entry.runtime_data.config_coordinator.available_sites = {}
    entry.runtime_data.coordinator.data = {
        "clients": {"default": {client["id"]: client for client in clients}}
    }


def _selector(result: ConfigFlowResult) -> Any:
    return require_not_none(result["data_schema"]).schema[CONF_TRACKED_CLIENTS]


def _choices(result: ConfigFlowResult) -> list[tuple[str, str]]:
    return [
        (option["value"], option["label"])
        for option in _selector(result).config["options"]
    ]


def _suggested(result: ConfigFlowResult) -> Any:
    for key in require_not_none(result["data_schema"]).schema:
        if key == CONF_TRACKED_CLIENTS:
            description: dict[str, Any] = getattr(key, "description", None) or {}
            return description["suggested_value"]
    raise AssertionError


async def _open(
    hass: HomeAssistant, entry: MockConfigEntry, options: dict[str, Any] | None = None
) -> ConfigFlowResult:
    if options is not None:
        hass.config_entries.async_update_entry(entry, options=options)
    return await hass.config_entries.options.async_init(entry.entry_id)


async def test_selector_hidden_without_choices(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """No snapshot, no tracker and no saved MAC: nothing to offer, no key saved."""
    mock_config_entry.add_to_hass(hass)

    result = await _open(hass, mock_config_entry)
    assert CONF_TRACKED_CLIENTS not in require_not_none(result["data_schema"]).schema

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=BASE_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_TRACKED_CLIENTS not in result["data"]


async def test_selector_lists_connected_clients_then_trackers_then_saved(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Choices are keyed by lowercase MAC and labelled by name with the MAC."""
    mock_config_entry.add_to_hass(hass)
    _attach_clients(
        mock_config_entry,
        _client(PHONE.upper(), name="Phone", hostname="phone-host"),
        _client(LAPTOP, hostname="laptop-host"),
        _client(TABLET),
        _client("aa:bb:cc:00:00:09", name="Gone", connected=False),
        {"id": "no-mac", "name": "No MAC"},
    )
    registry = er.async_get(hass)
    renamed = registry.async_get_or_create(
        "device_tracker", DOMAIN, f"{DOMAIN}_{AWAY}", config_entry=mock_config_entry
    )
    registry.async_update_entity(renamed.entity_id, name="Away phone")
    # Already in the snapshot: the live name wins.
    registry.async_get_or_create(
        "device_tracker",
        DOMAIN,
        f"{DOMAIN}_{PHONE}",
        config_entry=mock_config_entry,
        original_name="Old phone name",
    )
    # A standalone tracker carries its name on the client device.
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "client_aa:bb:cc:00:00:05")},
        name="Kitchen tablet",
    )
    registry.async_get_or_create(
        "device_tracker",
        DOMAIN,
        "AA:BB:CC:00:00:05",  # legacy bare-MAC row
        config_entry=mock_config_entry,
        device_id=device.id,
    )
    registry.async_get_or_create(
        "device_tracker",
        DOMAIN,
        f"{DOMAIN}_aa:bb:cc:00:00:06",
        config_entry=mock_config_entry,
    )
    registry.async_get_or_create(
        "device_tracker",
        DOMAIN,
        f"{MOBILITY_DEVICE_PREFIX}router1_location",
        config_entry=mock_config_entry,
    )

    result = await _open(
        hass,
        mock_config_entry,
        {CONF_TRACKED_CLIENTS: ["AA:BB:CC:00:00:07", AWAY]},
    )

    # Sorted by label; unmatched saved MACs last.
    assert _choices(result) == [
        (TABLET, TABLET),
        ("aa:bb:cc:00:00:06", "aa:bb:cc:00:00:06"),
        (AWAY, f"Away phone ({AWAY})"),
        ("aa:bb:cc:00:00:05", "Kitchen tablet (aa:bb:cc:00:00:05)"),
        (LAPTOP, f"laptop-host ({LAPTOP})"),
        (PHONE, f"Phone ({PHONE})"),
        ("aa:bb:cc:00:00:07", "aa:bb:cc:00:00:07"),
    ]
    assert _selector(result).config["multiple"] is True
    # Suggested, lowercase, not a default.
    assert _suggested(result) == [AWAY, "aa:bb:cc:00:00:07"]
    assert CONF_TRACKED_CLIENTS not in require_not_none(result["data_schema"])({})


async def test_selection_is_saved_lowercase(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A saved uppercase value round-trips as lowercase, without duplicates."""
    mock_config_entry.add_to_hass(hass)
    _attach_clients(mock_config_entry, _client(PHONE), _client(LAPTOP))

    result = await _open(
        hass, mock_config_entry, {CONF_TRACKED_CLIENTS: [LAPTOP.upper()]}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={**BASE_INPUT, CONF_TRACKED_CLIENTS: [LAPTOP, PHONE, LAPTOP]},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TRACKED_CLIENTS] == [LAPTOP, PHONE]


@pytest.mark.parametrize(
    "submitted", [{CONF_TRACKED_CLIENTS: []}, {}], ids=["empty", "omitted"]
)
async def test_shown_selector_left_empty_removes_the_filter(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, submitted: dict
) -> None:
    """Clearing or omitting the field removes the key, despite the merge."""
    mock_config_entry.add_to_hass(hass)

    # Unloaded entry: the saved, offline MAC is the only choice.
    result = await _open(hass, mock_config_entry, {CONF_TRACKED_CLIENTS: [AWAY]})
    assert _choices(result) == [(AWAY, AWAY)]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={**BASE_INPUT, **submitted}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_TRACKED_CLIENTS not in result["data"]


async def test_saved_offline_value_is_kept_when_resubmitted(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Saving other options with the suggested value keeps the filter."""
    mock_config_entry.add_to_hass(hass)

    result = await _open(hass, mock_config_entry, {CONF_TRACKED_CLIENTS: [AWAY]})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            **BASE_INPUT,
            "client_control": False,
            CONF_TRACKED_CLIENTS: [AWAY],
        },
    )

    assert result["data"][CONF_TRACKED_CLIENTS] == [AWAY]
    assert result["data"]["client_control"] is False


async def test_selector_hidden_at_submit_discards_the_value(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Visibility is recomputed on submit: a hidden selector saves nothing."""
    mock_config_entry.add_to_hass(hass)
    _attach_clients(mock_config_entry, _client(PHONE))

    result = await _open(hass, mock_config_entry)
    assert _choices(result) == [(PHONE, PHONE)]
    # The client disconnects while the form is open.
    _attach_clients(mock_config_entry)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={**BASE_INPUT, CONF_TRACKED_CLIENTS: [PHONE]}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_TRACKED_CLIENTS not in result["data"]


async def test_other_entrys_trackers_are_not_offered(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Only this entry's tracker rows become choices."""
    mock_config_entry.add_to_hass(hass)
    other = MockConfigEntry(domain=DOMAIN, data={}, entry_id="other_entry")
    other.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "device_tracker", DOMAIN, f"{DOMAIN}_{PHONE}", config_entry=other
    )

    result = await _open(hass, mock_config_entry)

    assert CONF_TRACKED_CLIENTS not in require_not_none(result["data_schema"]).schema


async def test_carrier_fabric_entry_has_no_selector(hass: HomeAssistant) -> None:
    """Carrier Fabric entries have no client trackers to filter."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC},
        options={CONF_TRACKED_CLIENTS: [PHONE]},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert CONF_TRACKED_CLIENTS not in require_not_none(result["data_schema"]).schema


# --- Applying a change ----------------------------------------------------------


def test_applied_options_resolve_the_filter() -> None:
    """A missing, empty or differently spelt filter compares equal."""
    assert _applied_options({}) == _applied_options({CONF_TRACKED_CLIENTS: []})
    assert _applied_options({CONF_TRACKED_CLIENTS: [PHONE, LAPTOP]}) == (
        _applied_options({CONF_TRACKED_CLIENTS: [LAPTOP.upper(), PHONE]})
    )
    assert _applied_options({CONF_TRACKED_CLIENTS: [PHONE]}) != _applied_options({})


@pytest.fixture
async def tracked_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    mock_local_auth: MagicMock,
    *,
    enable_custom_integrations: None,
) -> MockConfigEntry:
    """Set up an entry tracking two connected Wi-Fi clients by MAC."""
    mock_network_client.sites.get_all.return_value = [
        {"id": "default", "name": "Default"}
    ]
    mock_network_client.clients.get_all.return_value = [
        _client(PHONE, name="Phone"),
        _client(LAPTOP, name="Laptop"),
        # Connected but not selected: no tracker row, so only the live
        # snapshot can offer it.
        _client(TABLET, name="Tablet"),
    ]
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACKED_CLIENTS: [PHONE, LAPTOP]},
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


def _tracker(hass: HomeAssistant, mac: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        "device_tracker", DOMAIN, f"{DOMAIN}_{mac}"
    )


async def test_filter_change_reloads_and_removes_tracker_and_device(
    hass: HomeAssistant, tracked_integration: MockConfigEntry
) -> None:
    """Deselecting a connected client reloads; its tracker and device go."""
    entry = tracked_integration
    devices = dr.async_get(hass)
    assert _tracker(hass, LAPTOP) is not None
    assert devices.async_get_device_by_identifier(
        (DOMAIN, f"client_{LAPTOP}"), entry.entry_id
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert _tracker(hass, TABLET) is None
    assert _choices(result) == [
        (LAPTOP, f"Laptop ({LAPTOP})"),
        (PHONE, f"Phone ({PHONE})"),
        (TABLET, f"Tablet ({TABLET})"),
    ]
    with patch.object(
        hass.config_entries, "async_reload", wraps=hass.config_entries.async_reload
    ) as reload:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={**BASE_INPUT, CONF_TRACKED_CLIENTS: [PHONE]},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    reload.assert_awaited_once_with(entry.entry_id)
    assert _tracker(hass, PHONE) is not None
    assert _tracker(hass, LAPTOP) is None
    assert (
        devices.async_get_device_by_identifier(
            (DOMAIN, f"client_{LAPTOP}"), entry.entry_id
        )
        is None
    )

    # Still connected on the next poll, the deselected client is not re-added.
    await entry.runtime_data.device_coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _tracker(hass, LAPTOP) is None


async def test_grouping_toggle_with_a_saved_filter_stays_in_place(
    hass: HomeAssistant, tracked_integration: MockConfigEntry
) -> None:
    """Resubmitting the same filter with a grouping change does not reload."""
    entry = tracked_integration

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch.object(
        hass.config_entries, "async_reload", new_callable=AsyncMock
    ) as reload:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                **BASE_INPUT,
                CONF_GROUP_DEVICES_BY_UPLINK: True,
                CONF_TRACKED_CLIENTS: [LAPTOP, PHONE],
            },
        )
        await hass.async_block_till_done()

    reload.assert_not_called()
    assert entry.runtime_data.group_devices_by_uplink is True


async def test_saved_filter_macs_are_anonymised_in_diagnostics(
    hass: HomeAssistant, tracked_integration: MockConfigEntry
) -> None:
    """The saved MACs never appear in a diagnostics download."""
    diagnostics = await async_get_config_entry_diagnostics(hass, tracked_integration)

    assert len(diagnostics["entry"]["options"][CONF_TRACKED_CLIENTS]) == 2
    assert PHONE not in str(diagnostics).lower()
    assert LAPTOP not in str(diagnostics).lower()


async def test_uppercase_submission_is_rejected(
    hass: HomeAssistant, tracked_integration: MockConfigEntry
) -> None:
    """The selector accepts only its lowercase choices, so saves stay lowercase."""
    result = await hass.config_entries.options.async_init(tracked_integration.entry_id)

    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={**BASE_INPUT, CONF_TRACKED_CLIENTS: [PHONE.upper()]},
        )
