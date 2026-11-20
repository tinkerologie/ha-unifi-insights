# Copyright 2026 UniFi Insights contributors
"""
Lifecycle of the "Group devices by network uplink" option (issue #519).

A grouping-only option change is applied in place: no reload, so setup's
registry reconciliation never runs for it. Any other change still reloads.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.frontend import DATA_PANELS
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.unifi_insights import (
    UnifiInsightsData,
    _applied_options,
    _async_apply_grouping_in_place,
    async_reload_entry,
    frontend,
)
from custom_components.unifi_insights.const import (
    CONF_ACCESS_WEBHOOK,
    CONF_CLIENT_CONTROL,
    CONF_GROUP_DEVICES_BY_UPLINK,
    CONF_SITE_IDS,
    CONF_TRACK_CLIENTS,
    CONF_TRACK_WIFI_CLIENTS,
    CONF_TRACK_WIRED_CLIENTS,
    DOMAIN,
)
from tests.fixtures.assertions import require_not_none

if TYPE_CHECKING:
    from pathlib import Path

    from homeassistant.core import HomeAssistant

GROUPING = CONF_GROUP_DEVICES_BY_UPLINK


def _registry_state(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    """Snapshot every identity and customisation of the entry's registry rows."""
    devices = {
        device.id: (
            frozenset(device.identifiers),
            device.name,
            device.name_by_user,
            device.area_id,
            device.disabled_by,
        )
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry_id)
    }
    entities = {
        entity.entity_id: (
            entity.unique_id,
            entity.device_id,
            entity.name,
            entity.area_id,
            entity.disabled_by,
        )
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry_id)
    }
    return {"devices": devices, "entities": entities}


def _customise(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """
    Customise a device and a client tracker row of the entry.

    The tracker is for a client the coordinator does not report while client
    tracking is off, so setup's tracker reconciliation would remove it: a
    reload would fail the preservation check below.
    """
    area = ar.async_get(hass).async_create("Rack")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "default_device1")},
        name="Test Device (192.168.1.10)",
    )
    dr.async_get(hass).async_update_device(
        device.id, name_by_user="My switch", area_id=area.id
    )
    tracker = er.async_get(hass).async_get_or_create(
        "device_tracker",
        DOMAIN,
        f"{DOMAIN}_aa:bb:cc:00:00:01",
        config_entry=entry,
        device_id=device.id,
    )
    er.async_get(hass).async_update_entity(
        tracker.entity_id,
        name="My phone",
        area_id=area.id,
        disabled_by=er.RegistryEntryDisabler.USER,
    )


async def _set_options(
    hass: HomeAssistant, entry: MockConfigEntry, options: dict[str, Any]
) -> None:
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()


async def test_option_defaults_to_off(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A fresh entry has grouping off and records what setup applied."""
    runtime = init_integration.runtime_data

    assert runtime.group_devices_by_uplink is False
    assert runtime.applied_data == dict(init_integration.data)
    assert runtime.applied_options == _applied_options({})


async def test_grouping_toggle_does_not_reload_and_keeps_registry_rows(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Both directions apply in place; IDs and customisations are untouched."""
    entry_id = init_integration.entry_id
    _customise(hass, init_integration)
    before = _registry_state(hass, entry_id)
    runtime = init_integration.runtime_data
    assert before["devices"]
    assert before["entities"]

    with patch.object(
        hass.config_entries, "async_reload", wraps=hass.config_entries.async_reload
    ) as reload:
        await _set_options(hass, init_integration, {GROUPING: True})
        assert runtime.group_devices_by_uplink is True
        assert _registry_state(hass, entry_id) == before

        await _set_options(hass, init_integration, {GROUPING: False})
        assert runtime.group_devices_by_uplink is False

    reload.assert_not_called()
    assert init_integration.runtime_data is runtime
    assert init_integration.state is ConfigEntryState.LOADED
    assert _registry_state(hass, entry_id) == before


async def test_reload_would_have_changed_the_customised_rows(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Control for the test above: a real reload does not keep these rows."""
    entry_id = init_integration.entry_id
    _customise(hass, init_integration)
    before = _registry_state(hass, entry_id)

    assert await hass.config_entries.async_reload(entry_id)
    await hass.async_block_till_done()

    assert _registry_state(hass, entry_id) != before


async def test_mixed_change_still_reloads(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Grouping plus any other option goes through the reload path."""
    with patch.object(
        hass.config_entries, "async_reload", new_callable=AsyncMock
    ) as reload:
        await _set_options(
            hass, init_integration, {GROUPING: True, CONF_TRACK_WIFI_CLIENTS: True}
        )

    reload.assert_awaited_once_with(init_integration.entry_id)
    assert init_integration.runtime_data.group_devices_by_uplink is False


async def test_data_change_still_reloads(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A change to entry data is never applied in place."""
    with patch.object(
        hass.config_entries, "async_reload", new_callable=AsyncMock
    ) as reload:
        hass.config_entries.async_update_entry(
            init_integration, data={**init_integration.data, "verify_ssl": True}
        )
        await hass.async_block_till_done()

    reload.assert_awaited_once_with(init_integration.entry_id)


@pytest.mark.parametrize(
    "explicit",
    [
        {
            CONF_TRACK_WIFI_CLIENTS: False,
            CONF_TRACK_WIRED_CLIENTS: False,
            CONF_CLIENT_CONTROL: True,
            CONF_ACCESS_WEBHOOK: False,
            CONF_SITE_IDS: [],
        },
        {CONF_TRACK_CLIENTS: False},
        {CONF_SITE_IDS: None},
    ],
    ids=["new_defaults", "legacy_default", "empty_sites"],
)
async def test_missing_and_explicit_defaults_compare_equal(
    hass: HomeAssistant, init_integration: MockConfigEntry, explicit: dict[str, Any]
) -> None:
    """Saving the defaults explicitly alongside grouping is still grouping-only."""
    with patch.object(
        hass.config_entries, "async_reload", new_callable=AsyncMock
    ) as reload:
        await _set_options(hass, init_integration, {**explicit, GROUPING: True})

    reload.assert_not_called()
    assert init_integration.runtime_data.group_devices_by_uplink is True


def test_applied_options_resolve_legacy_tracking() -> None:
    """The legacy option feeds both tracking options unless they are set."""
    legacy = _applied_options({CONF_TRACK_CLIENTS: True, GROUPING: True})
    explicit = _applied_options(
        {CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACK_WIRED_CLIENTS: True}
    )

    assert legacy == explicit
    assert GROUPING not in legacy
    assert _applied_options({CONF_TRACK_CLIENTS: True}) != _applied_options({})
    assert _applied_options({"unknown": 1}) != _applied_options({})


async def test_sibling_entry_is_not_affected(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_network_client: MagicMock,
) -> None:
    """Toggling one entry neither reloads nor changes another loaded entry."""
    sibling = MockConfigEntry(
        domain=DOMAIN,
        title="Sibling",
        data={**init_integration.data, "host": "https://192.168.1.2"},
        options={},
        unique_id="sibling",
        entry_id="sibling_entry_id",
    )
    sibling.add_to_hass(hass)
    assert await hass.config_entries.async_setup(sibling.entry_id)
    await hass.async_block_till_done()
    sibling_runtime = sibling.runtime_data

    with patch.object(
        hass.config_entries, "async_reload", new_callable=AsyncMock
    ) as reload:
        await _set_options(hass, init_integration, {GROUPING: True})

    reload.assert_not_called()
    assert sibling.runtime_data is sibling_runtime
    assert sibling_runtime.group_devices_by_uplink is False
    assert init_integration.runtime_data.group_devices_by_uplink is True


async def test_in_place_requires_a_loaded_entry_with_applied_state(
    hass: HomeAssistant,
) -> None:
    """Without a loaded entry and a recorded applied state, reload is chosen."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={GROUPING: True})
    entry.add_to_hass(hass)

    assert not _async_apply_grouping_in_place(hass, entry)  # not loaded

    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = SimpleNamespace(group_devices_by_uplink=False)
    assert not _async_apply_grouping_in_place(hass, entry)  # foreign runtime

    runtime = UnifiInsightsData(
        config_coordinator=MagicMock(),
        device_coordinator=MagicMock(),
        protect_coordinator=None,
        network_client=MagicMock(),
        protect_client=None,
    )
    entry.runtime_data = runtime
    assert not _async_apply_grouping_in_place(hass, entry)  # nothing applied yet

    runtime.applied_data = {}
    runtime.applied_options = _applied_options({})
    assert _async_apply_grouping_in_place(hass, entry)
    assert runtime.group_devices_by_uplink is True
    # Unchanged again: still in place, nothing to do.
    assert _async_apply_grouping_in_place(hass, entry)


async def test_reload_entry_falls_back_to_reload(hass: HomeAssistant) -> None:
    """async_reload_entry reloads whenever the in-place path declines."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    with patch.object(
        hass.config_entries, "async_reload", new_callable=AsyncMock
    ) as reload:
        await async_reload_entry(hass, entry)

    reload.assert_awaited_once_with(entry.entry_id)


# --- The panel follows the option through real setup, toggle and unload -----


@pytest.fixture
def web(hass: HomeAssistant, tmp_path: Path) -> Path:
    """Pretend the web components are loaded and ship a panel bundle."""
    http = MagicMock()
    http.async_register_static_paths = AsyncMock()
    hass.http = http
    hass.config.components.update({"http", "frontend"})
    (tmp_path / frontend.PANEL_FILE).write_text("// panel")
    for card in frontend.CARD_FILES:
        (tmp_path / card).write_text("// card")
    return tmp_path


async def test_panel_follows_option_setup_toggle_and_unload(
    hass: HomeAssistant,
    web: Path,
    mock_config_entry: MockConfigEntry,
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    mock_local_auth: MagicMock,
    *,
    enable_custom_integrations: None,
) -> None:
    """Setup with the option on registers it; off and unload remove it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=mock_config_entry.title,
        data=dict(mock_config_entry.data),
        options={GROUPING: True},
        unique_id="panel_entry",
        entry_id="panel_entry",
    )
    entry.add_to_hass(hass)
    with patch.object(frontend, "FRONTEND_PATH", web):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        panel = hass.data[DATA_PANELS][frontend.PANEL_URL_PATH]
        assert panel.require_admin is True
        assert panel.sidebar_title == "UniFi devices"
        assert panel.sidebar_icon == "mdi:lan"
        custom = require_not_none(panel.config)["_panel_custom"]
        assert custom["name"] == frontend.PANEL_COMPONENT
        assert custom["module_url"].startswith(
            "/unifi_insights/device-groups-panel.js?v="
        )

        await _set_options(hass, entry, {GROUPING: False})
        assert frontend.PANEL_URL_PATH not in hass.data[DATA_PANELS]

        await _set_options(hass, entry, {GROUPING: True})
        assert frontend.PANEL_URL_PATH in hass.data[DATA_PANELS]

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert frontend.PANEL_URL_PATH not in hass.data[DATA_PANELS]


async def test_failed_unload_keeps_the_panel(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The panel is only reconciled after an unload that succeeded."""
    with (
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "custom_components.unifi_insights.async_reconcile_device_groups_panel",
            new_callable=AsyncMock,
        ) as reconcile,
    ):
        await hass.config_entries.async_unload(init_integration.entry_id)

    reconcile.assert_not_awaited()
