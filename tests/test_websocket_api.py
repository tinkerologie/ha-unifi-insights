# Copyright (c) 2026 Ruaan Deysel

"""Tests for the topology WebSocket API."""

from __future__ import annotations

import itertools
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from unittest.mock import MagicMock, Mock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry as MockConfigEntryForTest,
)
from unifi_official_api import UniFiResponseError

from custom_components.unifi_insights import CarrierFabricData
from custom_components.unifi_insights.console_identity import (
    async_get_device_entry,
    build_legacy_site_unique_id,
    build_site_unique_id,
)
from custom_components.unifi_insights.const import (
    CONF_CONNECTION_TYPE,
    CONF_CONSOLE_ID,
    CONNECTION_TYPE_CARRIER_FABRIC,
    DOMAIN,
)
from custom_components.unifi_insights.dashboard_snapshots import (
    WINDOWS,
    build_performance_snapshot,
    build_protect_snapshot,
    build_site_health_snapshot,
    build_unavailable_performance,
    build_unavailable_site_health,
)
from custom_components.unifi_insights.data_transforms import (
    NETWORK_FACTS_KEY,
    build_site_network_facts,
)
from custom_components.unifi_insights.device_tracker import UnifiClientTracker
from custom_components.unifi_insights.topology import build_site_topology
from custom_components.unifi_insights.websocket_api import (
    _UNLOAD_WATCHERS,
    ERR_ENTRY_NOT_LOADED,
    _device_in_entry,
    _generic_site_payload,
    _generic_site_unavailable,
    _protect_collections,
    _RequestError,
    ws_protect_subscribe,
    ws_site_health_subscribe,
    ws_topology_sources,
    ws_topology_subscribe,
)
from tests.fixtures.assertions import require_not_none
from tests.fixtures.facade_data import with_sections

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.unifi_insights.coordinators.facade import FacadeData

SITE = "site-1"
GW_MAC = "02:00:00:00:00:01"


def _publish(entry: MockConfigEntry, **sections: Any) -> FacadeData:
    """Publish a new facade snapshot with the given sections replaced."""
    facade = entry.runtime_data.coordinator
    facade.data = with_sections(facade.data, **sections)
    return facade.data


def _seed(entry: MockConfigEntry) -> FacadeData:
    """
    Add a two-device site with one client beside the mocked setup's data.

    The mocked "default" site is kept (merged, not replaced) so the entity
    listeners that also run on facade updates still find their own data. The
    seeded sections are published as a new snapshot, as an owner update would.
    """
    runtime = entry.runtime_data
    runtime.device_coordinator.last_update_success = True
    site = {SITE: {"id": SITE, "name": "Home"}}
    runtime.config_coordinator.data["sites"] = {
        **runtime.config_coordinator.data.get("sites", {}),
        **site,
    }
    data = runtime.coordinator.data
    sites = {**data.get("sites", {}), **site}
    devices = {
        **data.get("devices", {}),
        SITE: {
            "uuid-gw": {
                "id": "uuid-gw",
                "name": "Gateway",
                "model": "UDM Pro SE",
                "macAddress": GW_MAC,
                "state": "ONLINE",
                "topology": {"legacy_type": "udm"},
            },
            "uuid-ap": {
                "id": "uuid-ap",
                "name": "AP",
                "model": "U7 Pro XGS",
                "macAddress": "02:00:00:00:00:08",
                "state": "ONLINE",
                "topology": {
                    "legacy_type": "uap",
                    "uplink_mac": GW_MAC,
                    "uplink_type": "wire",
                },
            },
        },
    }
    clients = {
        **data.get("clients", {}),
        SITE: {
            "cli-1": {
                "id": "cli-1",
                "name": "Phone",
                "type": "WIRELESS",
                "macAddress": "12:00:00:00:00:02",
                "ipAddress": "10.2.0.9",
                "uplinkDeviceId": "uuid-ap",
            }
        },
    }
    return _publish(entry, sites=sites, devices=devices, clients=clients)


async def test_sources_lists_loaded_entries(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Sources returns each loaded entry with its selected sites."""
    _seed(init_integration)
    client = await hass_ws_client(hass)

    await client.send_json({"id": 1, "type": "unifi_insights/topology/sources"})
    msg = await client.receive_json()

    assert msg["success"]
    (source,) = msg["result"]
    assert source["entry_id"] == init_integration.entry_id
    assert source["title"] == init_integration.title
    assert {"id": SITE, "name": "Home"} in source["sites"]


async def test_sources_skips_loaded_entry_without_runtime_data(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A briefly unloading entry cannot be offered as a topology source."""
    _seed(init_integration)
    connection = Mock()
    unloaded_entry = Mock(runtime_data=None)
    with patch.object(
        hass.config_entries,
        "async_loaded_entries",
        return_value=[unloaded_entry, init_integration],
    ):
        ws_topology_sources(hass, connection, {"id": 1})

    (sources,) = connection.send_result.call_args.args[1:]
    assert [source["entry_id"] for source in sources] == [init_integration.entry_id]


async def test_dashboard_commands_skip_loaded_carrier_fabric_entry(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """A loaded Carrier Fabric entry has no topology or Protect data to offer."""
    _seed(init_integration)
    carrier_entry = MockConfigEntryForTest(
        domain=DOMAIN,
        entry_id="carrier_entry",
        unique_id="carrier_org_1",
        data={
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
            "api_key": "isp_key",
        },
    )
    carrier_entry.add_to_hass(hass)
    carrier_entry.runtime_data = CarrierFabricData(client=Mock(), coordinator=Mock())
    carrier_entry.mock_state(hass, ConfigEntryState.LOADED)
    client = await hass_ws_client(hass)

    for msg_id, msg_type in (
        (1, "unifi_insights/topology/sources"),
        (2, "unifi_insights/protect/sources"),
    ):
        await client.send_json({"id": msg_id, "type": msg_type})
        msg = await client.receive_json()
        assert msg["success"], msg_type
        assert [source["entry_id"] for source in msg["result"]] == [
            init_integration.entry_id
        ], msg_type

    await client.send_json(
        {
            "id": 3,
            "type": "unifi_insights/protect/get",
            "entry_id": carrier_entry.entry_id,
        }
    )
    msg = await client.receive_json()
    assert not msg["success"]
    assert msg["error"]["code"] == ERR_ENTRY_NOT_LOADED


async def test_get_returns_snapshot(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Get returns an allowlisted snapshot with registry ids attached."""
    _seed(init_integration)
    registry_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=init_integration.entry_id,
        identifiers={(DOMAIN, f"{SITE}_uuid-ap")},
    )
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "unifi_insights/topology/get",
            "entry_id": init_integration.entry_id,
            "site_id": SITE,
        }
    )
    msg = await client.receive_json()

    assert msg["success"]
    snapshot = msg["result"]
    assert snapshot["status"] == "ok"
    assert snapshot["site_name"] == "Home"
    assert {edge["source"]: edge["target"] for edge in snapshot["edges"]} == {
        "dev:uuid-ap": "dev:uuid-gw",
        "cli:cli-1": "dev:uuid-ap",
    }
    ap = next(node for node in snapshot["nodes"] if node["id"] == "dev:uuid-ap")
    assert ap["ha_device_id"] == registry_device.id
    assert "10.2.0.9" not in str(snapshot)
    assert GW_MAC not in str(snapshot)


async def test_get_uses_entry_scoped_registry_lookup(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """
    Device lookups go through the entry-scoped helper.

    The helper selects the best API available in the installed HA version;
    the snapshot builder must pass the current config entry for scoping.
    """
    _seed(init_integration)
    registry_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=init_integration.entry_id,
        identifiers={(DOMAIN, f"{SITE}_uuid-ap")},
    )
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.unifi_insights.websocket_api.async_get_device_entry",
        wraps=async_get_device_entry,
    ) as lookup:
        await client.send_json(
            {
                "id": 1,
                "type": "unifi_insights/topology/get",
                "entry_id": init_integration.entry_id,
                "site_id": SITE,
            }
        )
        msg = await client.receive_json()

    assert msg["success"]
    assert any(
        call.args[1:] == ((DOMAIN, f"{SITE}_uuid-ap"), init_integration.entry_id)
        for call in lookup.call_args_list
    )
    snapshot = msg["result"]
    ap = next(node for node in snapshot["nodes"] if node["id"] == "dev:uuid-ap")
    assert ap["ha_device_id"] == registry_device.id


async def test_get_non_admin_user_allowed(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client,
    hass_read_only_access_token: str,
) -> None:
    """Dashboards are used by non-admins, so plain authentication suffices."""
    _seed(init_integration)
    client = await hass_ws_client(hass, hass_read_only_access_token)

    await client.send_json(
        {
            "id": 1,
            "type": "unifi_insights/topology/get",
            "entry_id": init_integration.entry_id,
            "site_id": SITE,
        }
    )
    msg = await client.receive_json()

    assert msg["success"]


async def test_get_max_clients(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """max_clients is honoured and validated against the hard cap."""
    _seed(init_integration)
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "unifi_insights/topology/get",
            "entry_id": init_integration.entry_id,
            "site_id": SITE,
            "max_clients": 0,
        }
    )
    msg = await client.receive_json()
    assert msg["result"]["truncation"] == {"clients_total": 1, "clients_included": 0}

    await client.send_json(
        {
            "id": 2,
            "type": "unifi_insights/topology/get",
            "entry_id": init_integration.entry_id,
            "site_id": SITE,
            "max_clients": 501,
        }
    )
    msg = await client.receive_json()
    assert not msg["success"]
    assert msg["error"]["code"] == "invalid_format"


async def test_get_errors(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Unknown entry, foreign domain and unselected site are reported by code."""
    _seed(init_integration)
    client = await hass_ws_client(hass)

    async def _get(msg_id: int, entry_id: str, site_id: str) -> dict:
        await client.send_json(
            {
                "id": msg_id,
                "type": "unifi_insights/topology/get",
                "entry_id": entry_id,
                "site_id": site_id,
            }
        )
        return await client.receive_json()

    missing = await _get(1, "nope", SITE)
    assert missing["error"]["code"] == "entry_not_found"

    other_domain = MockConfigEntryForTest(domain="other_domain")
    other_domain.add_to_hass(hass)
    foreign = await _get(2, other_domain.entry_id, SITE)
    assert foreign["error"]["code"] == "entry_not_found"

    unselected = await _get(3, init_integration.entry_id, "site-9")
    assert unselected["error"]["code"] == "site_not_selected"

    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    unloaded = await _get(4, init_integration.entry_id, SITE)
    assert unloaded["error"]["code"] == "entry_not_loaded"


async def _subscribe(client, entry: MockConfigEntry, msg_id: int = 1) -> dict:
    """Subscribe and return the initial snapshot event."""
    await client.send_json(
        {
            "id": msg_id,
            "type": "unifi_insights/topology/subscribe",
            "entry_id": entry.entry_id,
            "site_id": SITE,
        }
    )
    ack = await client.receive_json()
    assert ack["success"], ack
    event = await client.receive_json()
    assert event["type"] == "event"
    return event["event"]


def _publish_site(entry: MockConfigEntry, section: str, records: Any) -> FacadeData:
    """Publish a new facade snapshot with the seeded site's ``section`` replaced."""
    data = entry.runtime_data.coordinator.data
    return _publish(entry, **{section: {**(data.get(section) or {}), SITE: records}})


def _rename_ap(entry: MockConfigEntry, name: str) -> None:
    """Publish a renamed AP the way the coordinator does: a new site dict."""
    devices = entry.runtime_data.coordinator.data["devices"][SITE]
    _publish_site(
        entry, "devices", {**devices, "uuid-ap": {**devices["uuid-ap"], "name": name}}
    )


async def _assert_no_event(client, ping_id: int) -> None:
    """Prove nothing was pushed: the next message is the ping's pong."""
    await client.send_json({"id": ping_id, "type": "ping"})
    msg = await client.receive_json()
    assert msg["type"] == "pong", msg


async def test_subscribe_pushes_only_on_change(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Initial snapshot, then one push per real change, none for no-ops."""
    _seed(init_integration)
    facade = init_integration.runtime_data.coordinator
    # Settle dynamic entity/device discovery for the newly seeded site before
    # measuring: the platforms' own coordinator listeners (e.g. sensor.py's
    # async_discover_sensors) create HA device-registry entries for site-1's
    # devices on their first run after _seed(), which would otherwise look
    # like a spurious content change on the *next* listener call below.
    facade.async_update_listeners()
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)

    initial = await _subscribe(client, init_integration)
    assert initial["status"] == "ok"

    facade.async_update_listeners()
    await _assert_no_event(client, 50)

    # Replace the site dict rather than mutating it in place: that is how the
    # device coordinator publishes each poll, and the subscription only
    # rebuilds when the per-site dict's identity changes.
    _rename_ap(init_integration, "Hallway AP")
    facade.async_update_listeners()
    pushed = await client.receive_json()
    assert pushed["type"] == "event"
    assert pushed["event"]["revision"] != initial["revision"]
    names = {node["id"]: node["name"] for node in pushed["event"]["nodes"]}
    assert names["dev:uuid-ap"] == "Hallway AP"


async def test_subscribe_site_vanishes_then_recovers(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """A site dropped without a reload is reported once, and the stream recovers."""
    _seed(init_integration)
    runtime = init_integration.runtime_data
    facade = runtime.coordinator
    facade.async_update_listeners()
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    initial = await _subscribe(client, init_integration)

    # The site disappears from the selection without an entry reload (deleted
    # on the console, or the Network API went away).
    sites = runtime.config_coordinator.data["sites"]
    runtime.config_coordinator.data["sites"] = {
        site_id: site for site_id, site in sites.items() if site_id != SITE
    }
    facade.async_update_listeners()
    gone = await client.receive_json()
    assert gone["type"] == "event"
    assert gone["event"]["status"] == "unavailable"
    assert gone["event"]["issues"] == [
        {"code": "site_unavailable", "severity": "error"}
    ]
    assert gone["event"]["site_name"] == "Home"
    assert gone["event"]["nodes"] == []

    facade.async_update_listeners()
    await _assert_no_event(client, 60)

    runtime.config_coordinator.data["sites"] = sites
    facade.async_update_listeners()
    back = await client.receive_json()
    assert back["type"] == "event"
    assert back["event"]["status"] == "ok"
    assert back["event"]["revision"] == initial["revision"]


async def test_subscribe_skips_rebuild_when_inputs_unchanged(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Facade updates that touch nothing the snapshot reads cost no rebuild."""
    data = _seed(init_integration)
    facade = init_integration.runtime_data.coordinator
    facade.async_update_listeners()
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    await _subscribe(client, init_integration)

    with patch(
        "custom_components.unifi_insights.websocket_api.build_site_topology",
        wraps=build_site_topology,
    ) as builder:
        for _ in range(3):
            facade.async_update_listeners()
        await _assert_no_event(client, 70)
        assert builder.call_count == 0

        # A poll that republishes identical content rebuilds once but pushes
        # nothing: the revision still gates what reaches the card.
        data = _publish_site(init_integration, "devices", dict(data["devices"][SITE]))
        facade.async_update_listeners()
        await _assert_no_event(client, 71)
        assert builder.call_count == 1

        _rename_ap(init_integration, "Hallway AP")
        facade.async_update_listeners()
        pushed = await client.receive_json()
        assert builder.call_count == 2

    assert pushed["type"] == "event"
    names = {node["id"]: node["name"] for node in pushed["event"]["nodes"]}
    assert names["dev:uuid-ap"] == "Hallway AP"


async def test_subscribe_errors_use_get_codes(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Subscribe rejects the same bad requests as get."""
    _seed(init_integration)
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "unifi_insights/topology/subscribe",
            "entry_id": init_integration.entry_id,
            "site_id": "site-9",
        }
    )
    msg = await client.receive_json()

    assert msg["error"]["code"] == "site_not_selected"


async def test_unsubscribe_stops_pushes(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """After unsubscribe_events, changes are no longer pushed."""
    _seed(init_integration)
    facade = init_integration.runtime_data.coordinator
    client = await hass_ws_client(hass)
    await _subscribe(client, init_integration, msg_id=7)

    await client.send_json({"id": 8, "type": "unsubscribe_events", "subscription": 7})
    assert (await client.receive_json())["success"]

    _rename_ap(init_integration, "Changed")
    facade.async_update_listeners()
    await _assert_no_event(client, 9)


async def test_subscription_cleanup_callbacks_are_idempotent(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Overlapping unsubscribe and unload callbacks leave one clean listener."""
    _seed(init_integration)
    connection = Mock()
    connection.subscriptions = {}
    ws_topology_subscribe(
        hass,
        connection,
        {
            "id": 7,
            "entry_id": init_integration.entry_id,
            "site_id": SITE,
            "max_clients": 500,
        },
    )
    watchers = hass.data[f"{DOMAIN}_topology_unload_watchers"]
    (unload_callback,) = watchers[init_integration.entry_id]
    unsubscribe = connection.subscriptions[7]

    unsubscribe()
    unsubscribe()
    unload_callback()

    assert not watchers[init_integration.entry_id]
    assert connection.send_message.call_count == 1


async def test_subscribe_entry_unload_sends_final_snapshot(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Unloading the entry pushes an unavailable snapshot and ends the stream."""
    _seed(init_integration)
    client = await hass_ws_client(hass)
    await _subscribe(client, init_integration)

    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    final = await client.receive_json()
    assert final["type"] == "event"
    assert final["event"]["status"] == "unavailable"
    assert final["event"]["issues"] == [{"code": "entry_unloaded", "severity": "error"}]
    assert final["event"]["site_name"] == "Home"


async def test_subscribe_after_reload_gets_unload_snapshot(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """A subscription made after a reload still hears the next unload."""
    _seed(init_integration)
    client = await hass_ws_client(hass)
    await _subscribe(client, init_integration, msg_id=1)

    assert await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    first_final = await client.receive_json()
    assert first_final["id"] == 1
    assert first_final["event"]["issues"] == [
        {"code": "entry_unloaded", "severity": "error"}
    ]

    _seed(init_integration)
    await _subscribe(client, init_integration, msg_id=2)

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    # The ping is answered after anything the unload pushed, so a missing
    # final snapshot shows up as the pong arriving first instead of a hang.
    await client.send_json({"id": 3, "type": "ping"})
    final = await client.receive_json()
    assert final["type"] == "event", final
    assert final["id"] == 2
    assert final["event"]["status"] == "unavailable"
    assert final["event"]["issues"] == [{"code": "entry_unloaded", "severity": "error"}]
    assert (await client.receive_json())["type"] == "pong"


async def test_subscribe_unload_then_close_is_safe(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client,
    caplog,
) -> None:
    """Unload followed by connection close never removes the listener twice."""
    _seed(init_integration)
    client = await hass_ws_client(hass)
    await _subscribe(client, init_integration)

    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    await client.receive_json()  # final unavailable snapshot

    # A second remove_listener() raises KeyError, but ActiveConnection's close
    # handler catches every unsubscribe error and only logs it - so the proof
    # that nothing ran twice is the absence of an ERROR record, not a raise.
    with caplog.at_level(logging.ERROR):
        await client.close()
        await hass.async_block_till_done()

    errors = [rec for rec in caplog.records if rec.levelno >= logging.ERROR]
    assert not errors, [rec.getMessage() for rec in errors]


async def test_subscribe_close_then_unload_is_safe(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Connection close followed by unload never removes the listener twice."""
    _seed(init_integration)
    client = await hass_ws_client(hass)
    await _subscribe(client, init_integration)

    await client.close()
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.NOT_LOADED


async def test_subscribe_cycles_share_one_unload_hook(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Repeated subscribe/unsubscribe adds at most one unload hook per entry."""
    _seed(init_integration)
    client = await hass_ws_client(hass)
    # _on_unload is private, but async_on_unload returns no remover, so the
    # hook list itself is the only place a per-subscription leak would show.
    hooks_before = len(init_integration._on_unload or [])

    for cycle in range(3):
        sub_id = 10 + cycle * 2
        await _subscribe(client, init_integration, msg_id=sub_id)
        await client.send_json(
            {"id": sub_id + 1, "type": "unsubscribe_events", "subscription": sub_id}
        )
        assert (await client.receive_json())["success"]

    assert len(init_integration._on_unload or []) <= hooks_before + 1
    watchers = hass.data[f"{DOMAIN}_topology_unload_watchers"]
    assert not watchers[init_integration.entry_id]


async def test_subscribe_builder_error_does_not_break_listeners(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client,
    caplog,
) -> None:
    """
    A builder bug gets the guard's own per-site log line.

    Home Assistant already isolates listener failures, so "other listeners
    still run" holds with or without the guard; what the guard adds is a
    message naming the feature and site instead of HA's generic one.
    """
    _seed(init_integration)
    facade = init_integration.runtime_data.coordinator
    client = await hass_ws_client(hass)
    await _subscribe(client, init_integration)

    calls: list[str] = []
    facade.async_add_listener(lambda: calls.append("entity"))
    _rename_ap(init_integration, "Changed")
    with (
        caplog.at_level(logging.ERROR),
        patch(
            "custom_components.unifi_insights.websocket_api.build_site_topology",
            side_effect=RuntimeError("boom"),
        ),
    ):
        facade.async_update_listeners()

    assert calls == ["entity"]
    assert f"Failed to rebuild the topology snapshot for {SITE}" in caplog.text
    assert "Unexpected error updating listener" not in caplog.text


async def test_timeline_subscribe_pushes_on_a_new_protect_event(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Timeline stream updates when a Protect frame publishes a new event."""
    _seed(init_integration)
    facade = init_integration.runtime_data.coordinator
    _publish(init_integration, protect={"events": {"motion": {}}})
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 501,
            "type": "unifi_insights/timeline/subscribe",
            "entry_id": init_integration.entry_id,
            "site_id": SITE,
            "max_items": 10,
            "hours": 24,
            "categories": ["security"],
        }
    )
    assert (await client.receive_json())["success"]
    initial = await client.receive_json()
    assert initial["type"] == "event"
    assert initial["event"]["included"] == 0

    # The Protect cache is copy-on-write: the frame replaces the events map.
    _publish(
        init_integration,
        protect={
            "events": {
                "motion": {
                    "evt-1": {
                        "id": "evt-1",
                        "device_id": "uuid-ap",
                        "name": "AP",
                        "timestamp": 4_102_444_800_000,
                    }
                }
            }
        },
    )
    facade.async_update_listeners()

    update = await client.receive_json()
    assert update["type"] == "event"
    assert update["event"]["included"] == 1


async def test_site_health_get_returns_snapshot(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Site health get returns a snapshot for a selected site."""
    _seed(init_integration)
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 90,
            "type": "unifi_insights/site_health/get",
            "entry_id": init_integration.entry_id,
            "site_id": SITE,
        }
    )
    msg = await client.receive_json()

    assert msg["success"]
    assert msg["result"]["site_id"] == SITE
    assert msg["result"]["status"] in {"ok", "partial"}
    assert "health" in msg["result"]


async def test_internet_activity_get_returns_snapshot(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Internet activity get returns configured window totals."""
    _seed(init_integration)
    _publish(
        init_integration,
        internet_activity={
            SITE: {
                "1h": {"rx_bytes": 1000, "tx_bytes": 500},
                "1d": {"rx_bytes": 2000, "tx_bytes": 1000},
            }
        },
        internet_activity_unavailable=set(),
    )
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 91,
            "type": "unifi_insights/internet_activity/get",
            "entry_id": init_integration.entry_id,
            "site_id": SITE,
        }
    )
    msg = await client.receive_json()

    assert msg["success"]
    assert msg["result"]["windows"]["1h"]["download_bytes"] == 1000
    assert msg["result"]["windows"]["1h"]["upload_bytes"] == 500


@pytest.fixture
async def internet_activity_entries(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client: Mock,
    mock_protect_client: Mock,
    mock_local_auth: Mock,
    *,
    enable_custom_integrations: None,
) -> list[MockConfigEntry]:
    """Load two synthetic consoles with independent totals for one shared site."""
    entries = []
    for index, namespace in enumerate(("console_a", "console_b"), start=1):
        entry = MockConfigEntryForTest(
            domain=DOMAIN,
            entry_id=f"activity_entry_{namespace}",
            unique_id=namespace,
            data={**mock_config_entry.data, CONF_CONSOLE_ID: namespace},
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        _seed(entry)
        _publish(
            entry,
            internet_activity={
                SITE: {
                    window: {"rx_bytes": index * 1000, "tx_bytes": index * 500}
                    for window in WINDOWS
                }
            },
            internet_activity_unavailable=set(),
        )
        entries.append(entry)
    return entries


async def test_internet_activity_get_namespaces_shared_site_registry_rows(
    hass: HomeAssistant,
    internet_activity_entries: list[MockConfigEntry],
    entity_registry: er.EntityRegistry,
    hass_ws_client: Any,
) -> None:
    """The native WebSocket entrypoint forwards each console's namespace."""
    expected_ids = []
    for entry in internet_activity_entries:
        owned_ids = {}
        for direction in ("download", "upload"):
            for window in WINDOWS:
                key = f"internet_{direction}_{window}"
                row = entity_registry.async_get_or_create(
                    "sensor",
                    DOMAIN,
                    build_site_unique_id(entry.data[CONF_CONSOLE_ID], SITE, key),
                    config_entry=entry,
                    translation_key=key,
                )
                owned_ids[f"{direction}_{window}"] = row.entity_id
        expected_ids.append(owned_ids)
    registry_before = dict(entity_registry.entities)
    client = await hass_ws_client(hass)
    snapshots = []

    for index, (entry, owned_ids) in enumerate(
        zip(internet_activity_entries, expected_ids, strict=True), start=1
    ):
        await client.send_json(
            {
                "id": index,
                "type": "unifi_insights/internet_activity/get",
                "entry_id": entry.entry_id,
                "site_id": SITE,
            }
        )
        response = await client.receive_json()

        assert response["success"]
        snapshot = response["result"]
        assert snapshot["entry_id"] == entry.entry_id
        assert snapshot["site_id"] == SITE
        assert snapshot["entity_ids"] == owned_ids
        assert snapshot["windows"]["1h"]["download_bytes"] == index * 1000
        assert all(
            require_not_none(entity_registry.async_get(entity_id)).config_entry_id
            == entry.entry_id
            for entity_id in snapshot["entity_ids"].values()
        )
        snapshots.append(snapshot)

    assert len(snapshots[0]["entity_ids"]) == len(snapshots[1]["entity_ids"]) == 8
    assert set(snapshots[0]["entity_ids"].values()).isdisjoint(
        snapshots[1]["entity_ids"].values()
    )
    assert dict(entity_registry.entities) == registry_before


@pytest.mark.parametrize(
    ("legacy_owner", "target_owner", "expected_candidate"),
    [
        ("own", "foreign", "legacy"),
        ("own", "own", "legacy"),
        ("foreign", "own", "target"),
        ("foreign", "foreign", None),
        (None, "foreign", None),
        (None, "own", "target"),
    ],
)
async def test_generic_internet_activity_payload_preserves_candidate_ownership(
    hass: HomeAssistant,
    internet_activity_entries: list[MockConfigEntry],
    entity_registry: er.EntityRegistry,
    legacy_owner: Literal["own", "foreign"] | None,
    target_owner: Literal["own", "foreign"],
    *,
    expected_candidate: str | None,
) -> None:
    """The generic payload follows owned legacy identities without takeover."""
    entries = dict(zip(("own", "foreign"), internet_activity_entries, strict=True))
    key = "internet_download_1h"
    candidates = {}
    if legacy_owner is not None:
        candidates["legacy"] = entity_registry.async_get_or_create(
            "sensor",
            DOMAIN,
            build_legacy_site_unique_id(SITE, key),
            config_entry=entries[legacy_owner],
            translation_key=key,
        )
    candidates["target"] = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        build_site_unique_id("console_a", SITE, key),
        config_entry=entries[target_owner],
        translation_key=key,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    candidates["target"] = entity_registry.async_update_entity(
        candidates["target"].entity_id, name="Customized activity sensor"
    )
    registry_before = dict(entity_registry.entities)

    snapshot = _generic_site_payload(hass, entries["own"], SITE, "internet_activity")

    expected = (
        {"download_1h": candidates[expected_candidate].entity_id}
        if expected_candidate is not None
        else {}
    )
    assert snapshot["entity_ids"] == expected
    assert dict(entity_registry.entities) == registry_before
    assert all(
        require_not_none(entity_registry.async_get(entity_id)).config_entry_id
        == entries["own"].entry_id
        for entity_id in snapshot["entity_ids"].values()
    )


async def test_performance_get_returns_snapshot(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Performance get returns device performance rows."""
    _seed(init_integration)
    _publish_site(
        init_integration,
        "stats",
        {
            "uuid-gw": {
                "cpuUtilizationPct": 10,
                "memoryUtilizationPct": 20,
                "uplink": {"txRateBps": 100, "rxRateBps": 50},
            }
        },
    )
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 92,
            "type": "unifi_insights/performance/get",
            "entry_id": init_integration.entry_id,
            "site_id": SITE,
        }
    )
    msg = await client.receive_json()

    assert msg["success"]
    assert isinstance(msg["result"].get("devices"), list)
    assert any(device["id"] == f"{SITE}:uuid-gw" for device in msg["result"]["devices"])


# ADR-005 contract locks for the performance and site-health payloads (#337).
_PERFORMANCE_KEYS = {
    "version",
    "entry_id",
    "site_id",
    "site_name",
    "status",
    "issues",
    "devices",
    "revision",
}
_PERFORMANCE_DEVICE_KEYS = {
    "id",
    "ha_device_id",
    "name",
    "kind",
    "state",
    "cpu_pct",
    "memory_pct",
    "uptime_s",
    "tx_bps",
    "rx_bps",
    "poe_power_w",
    "clients",
}
_SITE_HEALTH_KEYS = {
    "version",
    "entry_id",
    "site_id",
    "site_name",
    "status",
    "issues",
    "health",
    "gateway",
    "devices",
    "attention_devices",
    "attention_omitted",
    "clients",
    "freshness",
    "revision",
}
_SITE_HEALTH_GATEWAY_KEYS = {
    "present",
    "name",
    "state",
    "uptime_s",
    "ha_device_id",
    "internet",
    "wan_links",
}
_COUNT_KEYS = {"online", "offline", "unknown"}
_KIND_KEYS = {"gateway", "switch", "access_point", "other"}


async def test_performance_and_site_health_contract_locks(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Version 1 payloads keep their exact key sets and report bit/s as sent."""
    data = _seed(init_integration)
    data = _publish_site(
        init_integration,
        "stats",
        {
            "uuid-gw": {
                "cpuUtilizationPct": 12.5,
                "memoryUtilizationPct": 63.2,
                "uptimeSec": 3600,
                "uplink": {"txRateBps": 100, "rxRateBps": 50},
            }
        },
    )
    # Publish the seeded site like the device coordinator does.
    data = _publish(
        init_integration,
        **{
            NETWORK_FACTS_KEY: {
                **(data.get(NETWORK_FACTS_KEY) or {}),
                SITE: build_site_network_facts(
                    data["devices"][SITE], data["stats"][SITE]
                ),
            }
        },
    )
    client = await hass_ws_client(hass)

    results = {}
    for msg_id, payload_type in ((95, "performance"), (96, "site_health")):
        await client.send_json(
            {
                "id": msg_id,
                "type": f"unifi_insights/{payload_type}/get",
                "entry_id": init_integration.entry_id,
                "site_id": SITE,
            }
        )
        msg = await client.receive_json()
        assert msg["success"]
        results[payload_type] = msg["result"]

    performance = results["performance"]
    assert performance["version"] == 1
    assert set(performance) == _PERFORMANCE_KEYS
    for device in performance["devices"]:
        assert set(device) == _PERFORMANCE_DEVICE_KEYS
    gateway = next(d for d in performance["devices"] if d["id"] == f"{SITE}:uuid-gw")
    assert (gateway["tx_bps"], gateway["rx_bps"]) == (100, 50)
    assert (gateway["cpu_pct"], gateway["memory_pct"]) == (12.5, 63.2)
    assert gateway["kind"] == "gateway"

    health = results["site_health"]
    assert health["version"] == 1
    assert set(health) == _SITE_HEALTH_KEYS
    assert set(health["gateway"]) == _SITE_HEALTH_GATEWAY_KEYS
    assert health["gateway"]["present"] is True
    assert health["gateway"]["uptime_s"] == 3600
    assert set(health["devices"]) == _KIND_KEYS
    assert all(set(counts) == _COUNT_KEYS for counts in health["devices"].values())
    assert set(health["clients"]) == {"total", "wired", "wireless"}
    assert set(health["health"]) == {"level", "reasons"}

    unavailable_performance = build_unavailable_performance("e", SITE, "x")
    assert unavailable_performance["version"] == 1
    assert set(unavailable_performance) == _PERFORMANCE_KEYS
    unavailable_health = build_unavailable_site_health("e", SITE, "x")
    assert unavailable_health["version"] == 1
    assert set(unavailable_health) == _SITE_HEALTH_KEYS
    assert set(unavailable_health["gateway"]) == _SITE_HEALTH_GATEWAY_KEYS
    assert set(unavailable_health["devices"]) == _KIND_KEYS


async def test_timeline_get_returns_snapshot(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Timeline get returns recent events in security category."""
    _seed(init_integration)
    protect = init_integration.runtime_data.coordinator.data["protect"]
    events = protect.get("events") or {}
    _publish(
        init_integration,
        protect={
            **protect,
            "events": {
                **events,
                "motion": {
                    **(events.get("motion") or {}),
                    "evt-1": {
                        "id": "evt-1",
                        "device_id": "uuid-ap",
                        "name": "AP",
                        "timestamp": int(datetime.now(tz=UTC).timestamp() * 1000),
                    },
                },
            },
        },
    )
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 93,
            "type": "unifi_insights/timeline/get",
            "entry_id": init_integration.entry_id,
            "site_id": SITE,
            "hours": 24,
            "max_items": 10,
            "categories": ["security"],
        }
    )
    msg = await client.receive_json()

    assert msg["success"]
    assert msg["result"]["status"] == "ok"
    assert isinstance(msg["result"].get("items"), list)
    assert any(item["id"] == "evt:evt-1" for item in msg["result"]["items"])


async def test_protect_sources_and_get_and_subscribe(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Protect sources, get, and subscribe return snapshots and stream updates."""
    data = _seed(init_integration)
    facade = init_integration.runtime_data.coordinator
    data = _publish(
        init_integration,
        protect={
            "cameras": {},
            "chimes": {},
            "nvrs": {"nvr-1": {"storage": {"healthy": True, "used": 40.0}}},
        },
    )
    client = await hass_ws_client(hass)

    await client.send_json({"id": 94, "type": "unifi_insights/protect/sources"})
    sources_msg = await client.receive_json()
    assert sources_msg["success"]
    assert any(
        source["entry_id"] == init_integration.entry_id
        for source in sources_msg["result"]
    )

    await client.send_json(
        {
            "id": 95,
            "type": "unifi_insights/protect/get",
            "entry_id": init_integration.entry_id,
        }
    )
    get_msg = await client.receive_json()
    assert get_msg["success"]
    assert get_msg["result"]["status"] == "ok"

    await client.send_json(
        {
            "id": 96,
            "type": "unifi_insights/protect/subscribe",
            "entry_id": init_integration.entry_id,
        }
    )
    assert (await client.receive_json())["success"]
    initial = await client.receive_json()
    assert initial["type"] == "event"
    assert initial["event"]["status"] == "ok"

    _publish(
        init_integration,
        protect={
            **data["protect"],
            "nvrs": {"nvr-1": {"storage": {"healthy": False, "used": 40.0}}},
        },
    )
    facade.async_update_listeners()
    updated = await client.receive_json()
    assert updated["type"] == "event"
    assert updated["event"]["status"] == "degraded"

    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    unloaded_evt = await client.receive_json()
    assert unloaded_evt["type"] == "event"
    assert unloaded_evt["event"]["status"] == "unavailable"


async def test_dashboard_site_subscriptions_and_errors(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client, caplog
) -> None:
    """Cover get/subscribe error codes, updates, and unload for site streams."""
    _seed(init_integration)
    facade = init_integration.runtime_data.coordinator
    _publish(
        init_integration,
        internet_activity={
            SITE: {"1h": {"rx_bytes": 1000, "tx_bytes": 500}},
        },
        internet_activity_unavailable=set(),
        stats={SITE: {"uuid-gw": {"cpuUtilizationPct": 12}}},
    )
    facade.async_update_listeners()
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)

    for idx, cmd in enumerate(
        (
            "unifi_insights/site_health/get",
            "unifi_insights/internet_activity/get",
            "unifi_insights/performance/get",
            "unifi_insights/timeline/get",
        ),
        start=200,
    ):
        await client.send_json(
            {"id": idx, "type": cmd, "entry_id": "missing", "site_id": SITE}
        )
        err = await client.receive_json()
        assert err["error"]["code"] == "entry_not_found"

    await client.send_json(
        {"id": 210, "type": "unifi_insights/protect/get", "entry_id": "missing"}
    )
    assert (await client.receive_json())["error"]["code"] == "entry_not_found"

    await client.send_json(
        {
            "id": 211,
            "type": "unifi_insights/protect/subscribe",
            "entry_id": "missing",
        }
    )
    assert (await client.receive_json())["error"]["code"] == "entry_not_found"

    await client.send_json(
        {
            "id": 212,
            "type": "unifi_insights/site_health/subscribe",
            "entry_id": "missing",
            "site_id": SITE,
        }
    )
    assert (await client.receive_json())["error"]["code"] == "entry_not_found"

    for idx, cmd in enumerate(
        (
            "unifi_insights/site_health/subscribe",
            "unifi_insights/internet_activity/subscribe",
            "unifi_insights/performance/subscribe",
        ),
        start=220,
    ):
        await client.send_json(
            {
                "id": idx,
                "type": cmd,
                "entry_id": init_integration.entry_id,
                "site_id": SITE,
            }
        )
        assert (await client.receive_json())["success"]
        initial = await client.receive_json()
        assert initial["type"] == "event"

    # Trigger rebuild error logging isolation
    _rename_ap(init_integration, "AP-Renamed")
    with (
        caplog.at_level(logging.ERROR),
        patch(
            "custom_components.unifi_insights.websocket_api.build_site_health_snapshot",
            side_effect=RuntimeError("boom"),
        ),
    ):
        facade.async_update_listeners()
    assert "Failed to rebuild site_health snapshot" in caplog.text

    # Drain performance update from _rename_ap
    perf_evt = await client.receive_json()
    assert perf_evt["type"] == "event"

    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()


async def test_protect_payload_device_mapping(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Test Protect payload maps cameras and chimes to HA device registry IDs."""
    _seed(init_integration)
    dev_reg = dr.async_get(hass)
    cam_device = dev_reg.async_get_or_create(
        config_entry_id=init_integration.entry_id,
        identifiers={(DOMAIN, "protect_camera_cam-1")},
        name="Front Camera",
    )
    chime_device = dev_reg.async_get_or_create(
        config_entry_id=init_integration.entry_id,
        identifiers={(DOMAIN, "protect_chime_chime-1")},
        name="Hall Chime",
    )

    _publish(
        init_integration,
        protect={
            "cameras": {
                "cam-1": {"name": "Front Camera", "state": "CONNECTED"},
                "cam-unreg": {"name": "Unregistered Cam", "state": "CONNECTED"},
            },
            "chimes": {
                "chime-1": {"name": "Hall Chime", "state": "CONNECTED"},
                "chime-unreg": {"name": "Unregistered Chime", "state": "CONNECTED"},
            },
            "nvrs": {},
        },
    )
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 310,
            "type": "unifi_insights/protect/get",
            "entry_id": init_integration.entry_id,
        }
    )
    msg = await client.receive_json()
    assert msg["success"]
    devices = msg["result"]["devices"]
    cam_item = next(d for d in devices if d["id"] == "cam-1")
    assert cam_item["ha_device_id"] == cam_device.id
    cam_unreg = next(d for d in devices if d["id"] == "cam-unreg")
    assert cam_unreg["ha_device_id"] is None

    chime_item = next(d for d in devices if d["id"] == "chime-1")
    assert chime_item["ha_device_id"] == chime_device.id
    chime_unreg = next(d for d in devices if d["id"] == "chime-unreg")
    assert chime_unreg["ha_device_id"] is None


async def test_protect_payload_protect_coordinator_none(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_network_client: Any,
    mock_protect_client: Any,
    hass_ws_client: Any,
    *,
    mock_local_auth: Any,
    enable_custom_integrations: Any,
) -> None:
    """Test Protect payload returns no_protect snapshot when Protect is disabled."""
    mock_protect_client.cameras.get_all.side_effect = UniFiResponseError(
        "Protect not supported", status_code=404
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.runtime_data.protect_coordinator is None
    assert mock_config_entry.runtime_data.protect_client is None

    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 311,
            "type": "unifi_insights/protect/get",
            "entry_id": mock_config_entry.entry_id,
        }
    )
    msg = await client.receive_json()
    assert msg["success"]
    assert msg["result"]["status"] == "no_protect"


async def test_protect_subscribe_gates_rebuilds_on_collection_identity(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Only a frame that replaces cameras, chimes or NVRs rebuilds the stream."""
    _seed(init_integration)
    facade = init_integration.runtime_data.coordinator
    data = _publish(
        init_integration,
        protect={
            "cameras": {"cam-1": {"name": "Front", "state": "CONNECTED"}},
            "chimes": {},
            "nvrs": {},
            "sensors": {},
            "events": {},
        },
    )
    facade.async_update_listeners()
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "unifi_insights/protect/subscribe",
            "entry_id": init_integration.entry_id,
        }
    )
    assert (await client.receive_json())["success"]
    assert (await client.receive_json())["type"] == "event"

    with patch(
        "custom_components.unifi_insights.websocket_api.build_protect_snapshot",
        wraps=build_protect_snapshot,
    ) as builder:
        # A new snapshot with the same Protect collections.
        data = _publish(init_integration, protect={**data["protect"]})
        facade.async_update_listeners()
        await _assert_no_event(client, 2)
        assert builder.call_count == 0

        # A frame that replaces only sensors and events. (A new sensor would
        # register entities, which rebuilds on its own.)
        data = _publish(
            init_integration,
            protect={
                **data["protect"],
                "sensors": {},
                "events": {"motion": {}},
            },
        )
        facade.async_update_listeners()
        await _assert_no_event(client, 3)
        assert builder.call_count == 0

        # A frame that changes a camera replaces the cameras map.
        _publish(
            init_integration,
            protect={
                **data["protect"],
                "cameras": {"cam-1": {"name": "Front", "state": "DISCONNECTED"}},
            },
        )
        facade.async_update_listeners()
        pushed = await client.receive_json()
        assert builder.call_count == 1

    assert pushed["type"] == "event"


def test_protect_collections_non_mapping() -> None:
    """Without Protect data every gated collection reads as None."""
    assert _protect_collections(None) == (None, None, None)
    assert _protect_collections("not_a_dict", keys=("events",)) == (None,)


def test_generic_site_payload_unsupported_and_site_disabled(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test _generic_site_payload rejects disabled site and unsupported payload type."""
    _seed(init_integration)
    with pytest.raises(_RequestError) as exc_info:
        _generic_site_payload(
            hass, init_integration, "disabled_site", payload_type="site_health"
        )
    assert exc_info.value.code == "site_not_selected"

    with pytest.raises(_RequestError) as exc_info:
        _generic_site_payload(
            hass, init_integration, SITE, payload_type="unsupported_payload"
        )
    assert exc_info.value.code == "invalid_format"


def test_generic_site_unavailable_branches(
    init_integration: MockConfigEntry,
) -> None:
    """Test _generic_site_unavailable builds snapshots for performance and fallback."""
    perf = _generic_site_unavailable(init_integration, SITE, "performance", "code_perf")
    assert perf["status"] == "unavailable"
    assert perf["issues"][0]["code"] == "code_perf"

    tl = _generic_site_unavailable(init_integration, SITE, "timeline", "code_tl")
    assert tl["status"] == "unavailable"
    assert tl["issues"][0]["code"] == "code_tl"

    unknown = _generic_site_unavailable(
        init_integration, SITE, "unknown_type", "code_unknown"
    )
    assert unknown["status"] == "unavailable"
    assert unknown["issues"][0]["code"] == "code_unknown"


async def test_protect_subscription_forwarder_branches(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client, caplog
) -> None:
    """Test Protect subscription forwarder branches for rebuild and deduplication."""
    data = _seed(init_integration)
    facade = init_integration.runtime_data.coordinator
    data = _publish(
        init_integration,
        protect={
            "cameras": {},
            "chimes": {},
            "nvrs": {"nvr-1": {"storage": {"healthy": True, "used": 40.0}}},
        },
    )
    facade.async_update_listeners()
    await hass.async_block_till_done()

    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 320,
            "type": "unifi_insights/protect/subscribe",
            "entry_id": init_integration.entry_id,
        }
    )
    assert (await client.receive_json())["success"]
    initial = await client.receive_json()
    assert initial["type"] == "event"

    # 1. Skip when inputs unchanged (_same_inputs)
    facade.async_update_listeners()
    await _assert_no_event(client, 321)

    # 2. Rebuild exception in _async_forward: fail at the snapshot-builder
    # boundary so the real _protect_payload path still runs up to it. The
    # NVR update replaces the nvrs map, as a copy-on-write Protect frame does.
    data = _publish(
        init_integration,
        protect={
            **data["protect"],
            "nvrs": {"nvr-1": {"storage": {"healthy": True, "used": 45.0}}},
        },
    )
    with (
        caplog.at_level(logging.ERROR),
        patch(
            "custom_components.unifi_insights.websocket_api.build_protect_snapshot",
            side_effect=RuntimeError("protect rebuild boom"),
        ),
    ):
        facade.async_update_listeners()
    assert "Failed to rebuild protect snapshot" in caplog.text
    await _assert_no_event(client, 322)

    # 3. Revision unchanged in _async_forward: an unrelated device-registry
    # entry changes the rebuild inputs but not the snapshot content.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=init_integration.entry_id,
        identifiers={(DOMAIN, "unrelated-device")},
    )
    facade.async_update_listeners()
    await _assert_no_event(client, 323)


async def test_protect_and_site_subscription_cleanup_idempotent(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test idempotent unsubscribe and unload callbacks for Protect/site payloads."""
    _seed(init_integration)
    connection = Mock()
    connection.subscriptions = {}

    # Protect subscription cleanup
    ws_protect_subscribe(
        hass,
        connection,
        {"id": 401, "entry_id": init_integration.entry_id},
    )
    protect_watchers = hass.data[_UNLOAD_WATCHERS]
    protect_cbs = list(protect_watchers[init_integration.entry_id])
    protect_unsub = connection.subscriptions[401]
    protect_unsub()
    protect_unsub()
    # Unsubscribing stops watching the entry, and a late unload is a no-op.
    assert protect_watchers[init_integration.entry_id].isdisjoint(protect_cbs)
    sent = connection.send_message.call_count
    for cb in protect_cbs:
        cb()
        cb()
    assert connection.send_message.call_count == sent

    # Site health subscription cleanup
    connection.subscriptions = {}
    ws_site_health_subscribe(
        hass,
        connection,
        {"id": 402, "entry_id": init_integration.entry_id, "site_id": SITE},
    )
    site_watchers = hass.data[_UNLOAD_WATCHERS]
    site_cbs = list(site_watchers[init_integration.entry_id])
    site_unsub = connection.subscriptions[402]
    site_unsub()
    site_unsub()
    assert site_watchers[init_integration.entry_id].isdisjoint(site_cbs)
    sent = connection.send_message.call_count
    for cb in site_cbs:
        cb()
        cb()
    assert connection.send_message.call_count == sent


async def test_site_subscription_forwarder_branches(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Test site subscription forwarder skips unchanged inputs and handles error."""
    _seed(init_integration)
    facade = init_integration.runtime_data.coordinator
    facade.async_update_listeners()
    await hass.async_block_till_done()

    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 410,
            "type": "unifi_insights/site_health/subscribe",
            "entry_id": init_integration.entry_id,
            "site_id": SITE,
        }
    )
    assert (await client.receive_json())["success"]
    initial = await client.receive_json()
    assert initial["type"] == "event"

    # Inputs unchanged: forwarder exits early on _same_inputs
    facade.async_update_listeners()
    await _assert_no_event(client, 411)

    # RequestError during forwarder rebuild: the site leaves the selection, so
    # the real _generic_site_payload raises and the forwarder reports it.
    init_integration.runtime_data.config_coordinator.data["sites"].pop(SITE)
    facade.async_update_listeners()

    err_evt = await client.receive_json()
    assert err_evt["type"] == "event"
    assert err_evt["event"]["status"] == "unavailable"


# --- unifi_insights/topology/node_details (admin-only identity lookup) --------

GW_IP = "10.2.0.1"
AP_MAC = "02:00:00:00:00:08"
AP_IP = "10.2.0.2"
# Devices and clients keyed by a MAC get a keyed-hash node id, not "dev:<id>".
SWITCH_MAC = "02:00:00:00:00:30"
SWITCH_IP = "10.2.0.3"
PHONE_MAC = "12:ab:00:00:00:02"
PHONE_IP = "10.2.0.9"
LAPTOP_MAC = "12:cd:00:00:00:03"
# The API may spell a MAC in upper case; trackers and their devices key on lower.
LAPTOP_MAC_RAW = LAPTOP_MAC.upper()
LAPTOP_IP = "10.2.0.10"
PRINTER_MAC = "12:00:00:00:00:44"
PRINTER_IP = "10.2.0.44"
# Every address the seeded site holds; none may ever reach a snapshot.
SENSITIVE_VALUES = (
    GW_MAC,
    GW_IP,
    AP_MAC,
    AP_IP,
    SWITCH_MAC,
    SWITCH_IP,
    PHONE_MAC,
    PHONE_IP,
    LAPTOP_MAC,
    LAPTOP_IP,
    PRINTER_MAC,
    PRINTER_IP,
)


def _seed_details(entry: MockConfigEntry) -> FacadeData:
    """
    Extend the seeded site with addresses, MAC-keyed records and a standalone client.

    Each record spells its address keys differently on purpose (camelCase,
    snake_case, bare ``ip``/``mac``), as the coordinators' payloads do.
    """
    data = _seed(entry)
    devices = data["devices"][SITE]
    data = _publish_site(
        entry,
        "devices",
        {
            **devices,
            "uuid-gw": {**devices["uuid-gw"], "ipAddress": GW_IP},
            "uuid-ap": {**devices["uuid-ap"], "ip_address": AP_IP},
            SWITCH_MAC: {
                "id": SWITCH_MAC,
                "name": "Switch",
                "model": "USW-24",
                "mac": SWITCH_MAC,
                "ip": SWITCH_IP,
                "state": "ONLINE",
                "topology": {
                    "legacy_type": "usw",
                    "uplink_mac": GW_MAC,
                    "uplink_type": "wire",
                },
            },
        },
    )
    clients = data["clients"][SITE]
    return _publish_site(
        entry,
        "clients",
        {
            **clients,
            "cli-1": {
                **clients["cli-1"],
                "macAddress": PHONE_MAC,
                "ipAddress": PHONE_IP,
            },
            # No uplink: its tracker sits on a standalone ``client_<mac>`` device.
            "cli-2": {
                "id": "cli-2",
                "name": "Laptop",
                "type": "WIRED",
                "mac_address": LAPTOP_MAC_RAW,
                "ip_address": LAPTOP_IP,
            },
            PRINTER_MAC: {
                "id": PRINTER_MAC,
                "name": "Printer",
                "type": "WIRED",
                "mac": PRINTER_MAC,
                "ip": PRINTER_IP,
                "uplinkDeviceId": SWITCH_MAC,
            },
        },
    )


async def _node_details(
    client,
    entry: MockConfigEntry,
    node_id: str,
    *,
    msg_id: int = 1,
    site_id: str = SITE,
    entry_id: str | None = None,
) -> dict[str, Any]:
    """Send the node_details command and return the raw response."""
    await client.send_json(
        {
            "id": msg_id,
            "type": "unifi_insights/topology/node_details",
            "entry_id": entry_id or entry.entry_id,
            "site_id": site_id,
            "node_id": node_id,
        }
    )
    return await client.receive_json()


async def _snapshot(client, entry: MockConfigEntry, msg_id: int = 1) -> dict[str, Any]:
    """Return the seeded site's snapshot from topology/get."""
    await client.send_json(
        {
            "id": msg_id,
            "type": "unifi_insights/topology/get",
            "entry_id": entry.entry_id,
            "site_id": SITE,
        }
    )
    msg = await client.receive_json()
    assert msg["success"], msg
    return msg["result"]  # type: ignore[no-any-return]


def _real_tracker(entry: MockConfigEntry, mac: str) -> UnifiClientTracker:
    """Build the platform's own tracker so ids and device come from its code."""
    return UnifiClientTracker(
        coordinator=entry.runtime_data.coordinator, mac=mac, site_id=SITE
    )


def _register_tracker(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    unique_id: str,
    *,
    device_id: str | None = None,
) -> er.RegistryEntry:
    """Register a device_tracker entity under ``entry``."""
    return er.async_get(hass).async_get_or_create(
        "device_tracker",
        DOMAIN,
        unique_id,
        config_entry=entry,
        device_id=device_id,
    )


def _foreign_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add a second UniFi Insights entry that owns look-alike registry rows."""
    other = MockConfigEntryForTest(
        domain=DOMAIN, entry_id="other_entry", unique_id="other_key", data={}
    )
    other.add_to_hass(hass)
    return other


async def test_node_details_infrastructure(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """An infrastructure node returns its addresses, device and connectivity entity."""
    _seed_details(init_integration)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=init_integration.entry_id,
        identifiers={(DOMAIN, f"{SITE}_uuid-gw")},
    )
    entity = er.async_get(hass).async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{SITE}_uuid-gw_device_status",
        config_entry=init_integration,
        device_id=device.id,
    )
    client = await hass_ws_client(hass)

    msg = await _node_details(client, init_integration, "dev:uuid-gw")

    assert msg["success"], msg
    assert msg["result"] == {
        "kind": "gateway",
        "ip_address": GW_IP,
        "mac_address": GW_MAC,
        "device_id": device.id,
        "entity_id": entity.entity_id,
    }
    # The targets are an HA device registry id and an entity_id, never a unique id
    # or an entity registry id.
    assert msg["result"]["entity_id"].startswith("binary_sensor.")
    assert msg["result"]["entity_id"] not in {entity.id, entity.unique_id}


async def test_node_details_resolves_every_snapshot_node(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """
    Every node id a snapshot hands out resolves, hashed or not, to its own record.

    The ids come from the snapshot command rather than being recomputed here, so
    this proves the two commands agree on node identity and on ``kind``.
    """
    _seed_details(init_integration)
    client = await hass_ws_client(hass)
    nodes = (await _snapshot(client, init_integration))["nodes"]

    hashed = {node["name"] for node in nodes if node["id"][4:5] == "h"}
    assert hashed == {"Switch", "Printer"}
    results: dict[str, dict[str, Any]] = {}
    for index, node in enumerate(nodes, start=2):
        msg = await _node_details(client, init_integration, node["id"], msg_id=index)
        assert msg["success"], (node, msg)
        assert msg["result"]["kind"] == node["kind"]
        results[node["name"]] = msg["result"]

    assert {name: result["kind"] for name, result in results.items()} == {
        "Gateway": "gateway",
        "AP": "access_point",
        "Switch": "switch",
        "Phone": "client",
        "Laptop": "client",
        "Printer": "client",
    }
    assert {name: result["ip_address"] for name, result in results.items()} == {
        "Gateway": GW_IP,
        "AP": AP_IP,
        "Switch": SWITCH_IP,
        "Phone": PHONE_IP,
        "Laptop": LAPTOP_IP,
        "Printer": PRINTER_IP,
    }
    assert {name: result["mac_address"] for name, result in results.items()} == {
        "Gateway": GW_MAC,
        "AP": AP_MAC,
        "Switch": SWITCH_MAC,
        "Phone": PHONE_MAC,
        "Laptop": LAPTOP_MAC,
        "Printer": PRINTER_MAC,
    }
    # Nothing is registered, so no target is guessed for any node.
    assert all(
        not {"device_id", "entity_id"} & result.keys() for result in results.values()
    )


@pytest.mark.parametrize(
    ("fields", "expected_ip", "expected_mac"),
    [
        pytest.param(
            {"ipAddress": "10.9.0.1", "macAddress": "aa:00:00:00:00:01"},
            "10.9.0.1",
            "aa:00:00:00:00:01",
            id="camel-case",
        ),
        pytest.param(
            {"ip_address": "10.9.0.2", "mac_address": "aa:00:00:00:00:02"},
            "10.9.0.2",
            "aa:00:00:00:00:02",
            id="snake-case",
        ),
        pytest.param(
            {"ipAddress": "10.9.0.6", "macAddress": "AA:BC:DE:00:00:06"},
            "10.9.0.6",
            "aa:bc:de:00:00:06",
            id="uppercase-mac",
        ),
        pytest.param(
            {"ip": "10.9.0.3", "mac": "aa:00:00:00:00:03"},
            "10.9.0.3",
            "aa:00:00:00:00:03",
            id="bare",
        ),
        pytest.param(
            {"ipAddress": "10.9.0.4", "ip": "10.9.9.9"},
            "10.9.0.4",
            None,
            id="first-spelling-wins",
        ),
        pytest.param(
            {"ipAddress": None, "ip": "10.9.0.5", "macAddress": None, "mac": "aa:5"},
            "10.9.0.5",
            "aa:5",
            id="null-falls-through",
        ),
        pytest.param({}, None, None, id="absent"),
        pytest.param({"ipAddress": None, "macAddress": None}, None, None, id="null"),
        pytest.param({"ipAddress": "", "macAddress": ""}, None, None, id="empty"),
        pytest.param({"ipAddress": "  ", "macAddress": " "}, None, None, id="blank"),
        pytest.param(
            {"ipAddress": 10, "macAddress": ["aa"]}, None, None, id="non-string"
        ),
    ],
)
async def test_node_details_values_follow_get_field_and_omit_absent(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client,
    *,
    fields: dict[str, Any],
    expected_ip: str | None,
    expected_mac: str | None,
) -> None:
    """Raw values come through get_field(); an absent value omits its key."""
    data = _seed_details(init_integration)
    devices = data["devices"][SITE]
    spellings = {"ipAddress", "ip_address", "ip", "macAddress", "mac_address", "mac"}
    record = {k: v for k, v in devices["uuid-gw"].items() if k not in spellings}
    data = _publish_site(
        init_integration, "devices", {**devices, "uuid-gw": {**record, **fields}}
    )
    client = await hass_ws_client(hass)

    msg = await _node_details(client, init_integration, "dev:uuid-gw")

    assert msg["success"], msg
    expected: dict[str, str] = {"kind": "gateway"}
    if expected_ip is not None:
        expected["ip_address"] = expected_ip
    if expected_mac is not None:
        expected["mac_address"] = expected_mac
    assert msg["result"] == expected


async def test_node_details_client_on_standalone_device(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """An HMAC client resolves its standalone device and tracker."""
    data = _seed_details(init_integration)
    clients = data["clients"][SITE]
    _publish_site(
        init_integration,
        "clients",
        {
            **{key: value for key, value in clients.items() if key != "cli-2"},
            LAPTOP_MAC: clients["cli-2"],
        },
    )
    tracker = _real_tracker(init_integration, LAPTOP_MAC)
    assert tracker.device_info["identifiers"] == {(DOMAIN, f"client_{LAPTOP_MAC}")}
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=init_integration.entry_id,
        identifiers=tracker.device_info["identifiers"],
    )
    registered = _register_tracker(
        hass, init_integration, require_not_none(tracker.unique_id), device_id=device.id
    )
    client = await hass_ws_client(hass)
    nodes = (await _snapshot(client, init_integration))["nodes"]
    (laptop,) = [node for node in nodes if node["name"] == "Laptop"]
    assert laptop["id"].startswith("cli:h")

    msg = await _node_details(client, init_integration, laptop["id"], msg_id=2)

    assert msg["success"], msg
    assert msg["result"] == {
        "kind": "client",
        "ip_address": LAPTOP_IP,
        "mac_address": LAPTOP_MAC,
        "device_id": device.id,
        "entity_id": registered.entity_id,
    }


async def test_node_details_grouped_client_tracker_has_no_device(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """A tracker grouped under its uplink AP never reports the AP as the client."""
    _seed_details(init_integration)
    tracker = _real_tracker(init_integration, PHONE_MAC)
    assert tracker.device_info["identifiers"] == {(DOMAIN, f"{SITE}_uuid-ap")}
    ap_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=init_integration.entry_id,
        identifiers=tracker.device_info["identifiers"],
    )
    registered = _register_tracker(
        hass,
        init_integration,
        require_not_none(tracker.unique_id),
        device_id=ap_device.id,
    )
    client = await hass_ws_client(hass)

    msg = await _node_details(client, init_integration, "cli:cli-1")

    assert msg["success"], msg
    assert msg["result"] == {
        "kind": "client",
        "ip_address": PHONE_IP,
        "mac_address": PHONE_MAC,
        "entity_id": registered.entity_id,
    }
    assert ap_device.id not in json.dumps(msg["result"])


@pytest.mark.parametrize(
    "unique_id",
    [
        pytest.param(f"{DOMAIN}_{PHONE_MAC}", id="current"),
        pytest.param(PHONE_MAC, id="legacy-bare-mac"),
        pytest.param(PHONE_MAC.upper(), id="legacy-api-spelling"),
    ],
)
async def test_node_details_client_tracker_unique_id_formats(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client,
    unique_id: str,
) -> None:
    """Current and legacy tracker unique ids both resolve to the client's tracker."""
    _seed_details(init_integration)
    registered = _register_tracker(hass, init_integration, unique_id)
    client = await hass_ws_client(hass)

    msg = await _node_details(client, init_integration, "cli:cli-1")

    assert msg["success"], msg
    assert msg["result"]["entity_id"] == registered.entity_id
    assert "device_id" not in msg["result"]


async def test_node_details_omits_ambiguous_legacy_tracker_matches(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Case-insensitive legacy matches are ambiguous even within one entry."""
    _seed_details(init_integration)
    _register_tracker(hass, init_integration, PHONE_MAC)
    _register_tracker(hass, init_integration, PHONE_MAC.upper())
    client = await hass_ws_client(hass)

    msg = await _node_details(client, init_integration, "cli:cli-1")

    assert msg["success"], msg
    assert "entity_id" not in msg["result"]


async def test_node_details_client_current_unique_id_takes_precedence(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """The current unique ID wins even when a legacy bare-MAC tracker exists."""
    _seed_details(init_integration)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=init_integration.entry_id,
        identifiers={(DOMAIN, f"client_{PHONE_MAC}")},
    )
    current = _register_tracker(
        hass, init_integration, f"{DOMAIN}_{PHONE_MAC}", device_id=device.id
    )
    _register_tracker(hass, init_integration, PHONE_MAC.upper(), device_id=device.id)
    client = await hass_ws_client(hass)

    msg = await _node_details(client, init_integration, "cli:cli-1")

    assert msg["success"], msg
    assert msg["result"] == {
        "kind": "client",
        "ip_address": PHONE_IP,
        "mac_address": PHONE_MAC,
        "device_id": device.id,
        "entity_id": current.entity_id,
    }


async def test_node_details_client_ignores_other_clients_and_non_trackers(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Only this client's tracker counts, not other MACs or non-tracker entities."""
    _seed_details(init_integration)
    _register_tracker(hass, init_integration, f"{DOMAIN}_{LAPTOP_MAC}")
    # Same unique id, wrong domain: a sensor must never be offered as the tracker.
    er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_{PHONE_MAC}", config_entry=init_integration
    )
    client = await hass_ws_client(hass)

    msg = await _node_details(client, init_integration, "cli:cli-1")

    assert msg["success"], msg
    assert "entity_id" not in msg["result"]
    assert "device_id" not in msg["result"]


async def test_node_details_client_without_mac_has_no_targets(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """A client record with no MAC returns its other fields and no targets."""
    data = _seed_details(init_integration)
    clients = data["clients"][SITE]
    data = _publish_site(
        init_integration,
        "clients",
        {
            **clients,
            "cli-1": {
                key: value
                for key, value in clients["cli-1"].items()
                if key not in {"macAddress", "mac", "mac_address"}
            },
        },
    )
    _register_tracker(hass, init_integration, f"{DOMAIN}_{PHONE_MAC}")
    client = await hass_ws_client(hass)

    msg = await _node_details(client, init_integration, "cli:cli-1")

    assert msg["success"], msg
    assert msg["result"] == {"kind": "client", "ip_address": PHONE_IP}


async def test_node_details_registry_rows_of_other_entries_are_not_returned(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Registry rows owned by another config entry are never a node's target."""
    _seed_details(init_integration)
    other = _foreign_entry(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    foreign_gw = device_registry.async_get_or_create(
        config_entry_id=other.entry_id, identifiers={(DOMAIN, f"{SITE}_uuid-gw")}
    )
    entity_registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{SITE}_uuid-gw_device_status",
        config_entry=other,
        device_id=foreign_gw.id,
    )
    foreign_client = device_registry.async_get_or_create(
        config_entry_id=other.entry_id, identifiers={(DOMAIN, f"client_{PHONE_MAC}")}
    )
    _register_tracker(hass, other, f"{DOMAIN}_{PHONE_MAC}", device_id=foreign_client.id)
    client = await hass_ws_client(hass)
    gateway_only = {"kind": "gateway", "ip_address": GW_IP, "mac_address": GW_MAC}
    phone_only = {"kind": "client", "ip_address": PHONE_IP, "mac_address": PHONE_MAC}

    gateway = await _node_details(client, init_integration, "dev:uuid-gw", msg_id=1)
    phone = await _node_details(client, init_integration, "cli:cli-1", msg_id=2)

    assert gateway["result"] == gateway_only
    assert phone["result"] == phone_only


async def test_node_details_checks_membership_of_every_device_lookup(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """
    A device the lookup helper returns must still belong to this config entry.

    The helper's fallback branches (older Home Assistant) match on identifier
    alone, so they can hand back another entry's device. Simulate that by making
    the lookup return foreign devices, and check neither node kind reports them.
    """
    _seed_details(init_integration)
    other = _foreign_entry(hass)
    device_registry = dr.async_get(hass)
    foreign_gw = device_registry.async_get_or_create(
        config_entry_id=other.entry_id, identifiers={(DOMAIN, f"{SITE}_uuid-gw")}
    )
    foreign_client = device_registry.async_get_or_create(
        config_entry_id=other.entry_id, identifiers={(DOMAIN, f"client_{PHONE_MAC}")}
    )
    # This entry's own tracker points at the foreign device, so the lookup result
    # and the tracker agree and only the membership check can reject it.
    _register_tracker(
        hass, init_integration, f"{DOMAIN}_{PHONE_MAC}", device_id=foreign_client.id
    )
    client = await hass_ws_client(hass)

    with patch(
        "custom_components.unifi_insights.websocket_api.async_get_device_entry",
        side_effect=[foreign_gw, foreign_client],
    ) as lookup:
        gateway = await _node_details(client, init_integration, "dev:uuid-gw", msg_id=1)
        phone = await _node_details(client, init_integration, "cli:cli-1", msg_id=2)

    assert lookup.call_count == 2
    assert "device_id" not in gateway["result"]
    assert "device_id" not in phone["result"]
    assert phone["result"]["entity_id"].startswith("device_tracker.")


def test_device_in_entry_compares_the_owning_entry() -> None:
    """A device belongs to the entry that owns it."""
    device = MagicMock(spec=dr.DeviceEntry, config_entry_id="entry-a")

    assert _device_in_entry(device, "entry-a")
    assert not _device_in_entry(device, "entry-b")


@pytest.mark.parametrize("field", ["entry_id", "site_id", "node_id"])
@pytest.mark.parametrize("invalid", [False, True])
async def test_node_details_schema_rejects_missing_or_invalid_fields(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client,
    field: str,
    *,
    invalid: bool,
) -> None:
    """Native HA schemas require each identity field to be a string."""
    client = await hass_ws_client(hass)
    request = {
        "id": 1,
        "type": "unifi_insights/topology/node_details",
        "entry_id": init_integration.entry_id,
        "site_id": SITE,
        "node_id": "dev:uuid-gw",
    }
    if invalid:
        request[field] = 42
    else:
        del request[field]
    await client.send_json(request)
    response = await client.receive_json()

    assert not response["success"]
    assert response["error"]["code"] == "invalid_format"
    assert "result" not in response


async def test_node_details_requires_admin(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client,
    hass_read_only_access_token: str,
) -> None:
    """A non-admin gets Home Assistant's unauthorized error and no identity data."""
    _seed_details(init_integration)
    client = await hass_ws_client(hass, hass_read_only_access_token)

    msg = await _node_details(client, init_integration, "dev:uuid-gw")

    assert not msg["success"]
    assert msg["error"]["code"] == "unauthorized"
    assert "result" not in msg
    assert GW_IP not in str(msg)
    assert GW_MAC not in str(msg)


async def test_node_details_errors(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Entry, site and node failures are reported by code."""
    data = _seed_details(init_integration)
    # Another site holds a device the requested site does not have.
    data = _publish(
        init_integration,
        devices={
            **data["devices"],
            "site-2": {"uuid-other": {"id": "uuid-other", "name": "Elsewhere"}},
        },
    )
    # Two ids that normalise to one MAC share a node id, so it names two nodes.
    dup_a, dup_b = "aa:bb:cc:dd:ee:01", "AA-BB-CC-DD-EE-01"
    data = _publish_site(
        init_integration,
        "devices",
        {
            **data["devices"][SITE],
            dup_a: {"id": dup_a, "name": "Twin A"},
            dup_b: {"id": dup_b, "name": "Twin B"},
            "junk": "not a device record",
        },
    )
    client = await hass_ws_client(hass)
    nodes = (await _snapshot(client, init_integration))["nodes"]
    twin_ids = {node["id"] for node in nodes if node["name"].startswith("Twin")}
    assert len(twin_ids) == 1
    (twin_id,) = twin_ids
    message_ids = itertools.count(2)

    async def _error(node_id: str, **kwargs: str) -> str:
        msg = await _node_details(
            client, init_integration, node_id, msg_id=next(message_ids), **kwargs
        )
        assert not msg["success"], msg
        assert "result" not in msg
        return msg["error"]["code"]

    other_domain = MockConfigEntryForTest(domain="other_domain")
    other_domain.add_to_hass(hass)

    assert await _error("dev:uuid-gw", entry_id="nope") == "entry_not_found"
    foreign = await _error("dev:uuid-gw", entry_id=other_domain.entry_id)
    assert foreign == "entry_not_found"
    assert await _error("dev:uuid-gw", site_id="site-9") == "site_not_selected"
    assert await _error("dev:does-not-exist") == "node_not_found"
    assert await _error("uuid-gw") == "node_not_found"
    assert await _error("dev:uuid-other") == "node_not_found"
    assert await _error("cli:uuid-gw") == "node_not_found"
    assert await _error("dev:cli-1") == "node_not_found"
    assert await _error(twin_id) == "node_not_found"
    assert await _error("dev:junk") == "node_not_found"

    # A node that left the coordinator data after the card rendered is stale.
    devices = data["devices"][SITE]
    data = _publish_site(
        init_integration,
        "devices",
        {k: v for k, v in devices.items() if k != "uuid-ap"},
    )
    assert await _error("dev:uuid-ap") == "node_not_found"
    ok = await _node_details(
        client, init_integration, "dev:uuid-gw", msg_id=next(message_ids)
    )
    assert ok["success"], ok


async def test_node_details_node_of_another_selected_site_is_not_found(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """A node id of one selected site cannot be read through another site."""
    data = _seed_details(init_integration)
    runtime = init_integration.runtime_data
    second = {"site-2": {"id": "site-2", "name": "Branch"}}
    runtime.config_coordinator.data["sites"] = {
        **runtime.config_coordinator.data["sites"],
        **second,
    }
    _publish(
        init_integration,
        sites={**data["sites"], **second},
        devices={
            **data["devices"],
            "site-2": {"uuid-branch": {"id": "uuid-branch", "ipAddress": "10.7.0.1"}},
        },
    )
    client = await hass_ws_client(hass)

    own = await _node_details(
        client, init_integration, "dev:uuid-branch", site_id="site-2"
    )
    crossed = await _node_details(
        client, init_integration, "dev:uuid-branch", msg_id=2, site_id=SITE
    )

    assert own["success"], own
    assert own["result"]["ip_address"] == "10.7.0.1"
    assert not crossed["success"]
    assert crossed["error"]["code"] == "node_not_found"


async def test_node_details_handles_missing_sections(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Missing or malformed device and client sections resolve to node_not_found."""
    _seed_details(init_integration)
    _publish(init_integration, clients=None, devices={SITE: None})
    client = await hass_ws_client(hass)

    for msg_id, node_id in ((1, "dev:uuid-gw"), (2, "cli:cli-1")):
        msg = await _node_details(client, init_integration, node_id, msg_id=msg_id)
        assert not msg["success"], msg
        assert msg["error"]["code"] == "node_not_found"


async def test_node_details_entry_not_loaded(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """An unloaded entry has no coordinator data to resolve nodes from."""
    _seed_details(init_integration)
    client = await hass_ws_client(hass)
    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    msg = await _node_details(client, init_integration, "dev:uuid-gw")

    assert not msg["success"]
    assert msg["error"]["code"] == "entry_not_loaded"


async def test_snapshot_commands_still_exclude_ip_and_mac(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """get and subscribe keep omitting addresses now that node_details serves them."""
    _seed_details(init_integration)
    client = await hass_ws_client(hass)
    got = await _snapshot(client, init_integration)
    # node_details is the only place an address may appear.
    details = await _node_details(client, init_integration, "dev:uuid-gw", msg_id=2)
    assert details["result"]["ip_address"] == GW_IP
    event = await _subscribe(client, init_integration, msg_id=3)

    for payload in (got, event):
        assert len(payload["nodes"]) == 6
        text = json.dumps(payload).lower()
        for value in SENSITIVE_VALUES:
            assert value.lower() not in text, value
        assert all(
            not {"ip_address", "mac_address"} & node.keys() for node in payload["nodes"]
        )


@pytest.mark.parametrize("tracker_on_uplink", [False, True])
async def test_node_details_client_device_resolves_independently_of_tracker(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client,
    *,
    tracker_on_uplink: bool,
) -> None:
    """Only the standalone identifier determines the client device target."""
    _seed_details(init_integration)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=init_integration.entry_id,
        identifiers={(DOMAIN, f"client_{PHONE_MAC}")},
    )
    expected = {
        "kind": "client",
        "ip_address": PHONE_IP,
        "mac_address": PHONE_MAC,
        "device_id": device.id,
    }
    if tracker_on_uplink:
        uplink = dr.async_get(hass).async_get_or_create(
            config_entry_id=init_integration.entry_id,
            identifiers={(DOMAIN, f"{SITE}_uuid-ap")},
        )
        tracker = _register_tracker(
            hass, init_integration, f"{DOMAIN}_{PHONE_MAC}", device_id=uplink.id
        )
        expected["entity_id"] = tracker.entity_id
    client = await hass_ws_client(hass)

    msg = await _node_details(client, init_integration, "cli:cli-1")

    assert msg["success"], msg
    assert msg["result"] == expected


async def test_node_details_foreign_current_tracker_falls_back_to_own_legacy(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """A foreign current tracker does not hide this entry's legacy tracker."""
    _seed_details(init_integration)
    other = _foreign_entry(hass)
    _register_tracker(hass, other, f"{DOMAIN}_{PHONE_MAC}")
    legacy = _register_tracker(hass, init_integration, PHONE_MAC)
    client = await hass_ws_client(hass)

    msg = await _node_details(client, init_integration, "cli:cli-1")

    assert msg["success"], msg
    assert msg["result"] == {
        "kind": "client",
        "ip_address": PHONE_IP,
        "mac_address": PHONE_MAC,
        "entity_id": legacy.entity_id,
    }


@pytest.mark.parametrize(
    "node_id", ["group:dev:uuid-ap", "group:root", "group:unconnected"]
)
async def test_node_details_group_ids_are_not_found(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client,
    node_id: str,
) -> None:
    """Synthetic frontend group IDs never resolve to source records."""
    _seed_details(init_integration)
    client = await hass_ws_client(hass)

    msg = await _node_details(client, init_integration, node_id)

    assert not msg["success"], msg
    assert msg["error"]["code"] == "node_not_found"
    assert "result" not in msg


WAN_IP = "203.0.113.7"


def _seed_wans(entry: MockConfigEntry) -> FacadeData:
    """Give the seeded gateway a connected fiber WAN and a down 5G backup WAN."""
    data = _seed(entry)
    devices = data["devices"][SITE]
    return _publish_site(
        entry,
        "devices",
        {
            **devices,
            "uuid-gw": {
                **devices["uuid-gw"],
                "wans": [
                    {"key": "wan", "name": "Fiber", "connected": True, "ip": WAN_IP},
                    {
                        "key": "wan2",
                        "name": "5G Backup",
                        "connected": False,
                        "ip": None,
                    },
                ],
            },
        },
    )


async def test_node_details_wan_resolves_to_gateway_and_connection_sensor(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """A WAN node returns its IP, its gateway device and its Connection sensor."""
    _seed_wans(init_integration)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=init_integration.entry_id,
        identifiers={(DOMAIN, f"{SITE}_uuid-gw")},
    )
    entity = er.async_get(hass).async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{SITE}_uuid-gw_wan_link_wan",
        config_entry=init_integration,
        device_id=device.id,
    )
    client = await hass_ws_client(hass)

    up = await _node_details(client, init_integration, "wan:uuid-gw:wan")
    down = await _node_details(client, init_integration, "wan:uuid-gw:wan2", msg_id=2)

    assert up["success"], up
    assert up["result"] == {
        "kind": "wan",
        "ip_address": WAN_IP,
        "device_id": device.id,
        "entity_id": entity.entity_id,
    }
    assert down["success"], down
    assert down["result"] == {"kind": "wan", "device_id": device.id}


async def test_node_details_resolves_snapshot_wan_ids(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """The WAN ids a snapshot hands out resolve; the snapshot never has the IP."""
    _seed_wans(init_integration)
    client = await hass_ws_client(hass)
    snapshot = await _snapshot(client, init_integration)

    assert WAN_IP not in json.dumps(snapshot)
    wans = [node for node in snapshot["nodes"] if node["kind"] == "wan"]
    assert {node["name"]: node["state"] for node in wans} == {
        "Fiber": "online",
        "5G Backup": "offline",
    }
    for index, node in enumerate(wans, start=2):
        msg = await _node_details(client, init_integration, node["id"], msg_id=index)
        assert msg["success"], (node, msg)
        assert msg["result"]["kind"] == "wan"


@pytest.mark.parametrize(
    "node_id",
    ["wan:uuid-gw:wan3", "wan:uuid-ap:wan", "wan:uuid-gw:", "wan:uuid-gw"],
)
async def test_node_details_unknown_wan_is_not_found(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client,
    node_id: str,
) -> None:
    """A WAN id of no gateway WAN record names no node."""
    data = _seed_wans(init_integration)
    devices = data["devices"][SITE]
    data = _publish_site(
        init_integration,
        "devices",
        {
            **devices,
            "uuid-gw": {
                **devices["uuid-gw"],
                "wans": [*devices["uuid-gw"]["wans"], "wan3", {"key": ""}],
            },
            "uuid-ap": {**devices["uuid-ap"], "wans": [{"key": "wan"}]},
        },
    )
    client = await hass_ws_client(hass)

    msg = await _node_details(client, init_integration, node_id)

    assert not msg["success"], msg
    assert msg["error"]["code"] == "node_not_found"


async def test_subscribe_pushes_when_a_wan_disconnects(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """A WAN going down on the next poll pushes a snapshot with the new state."""
    data = _seed_wans(init_integration)
    facade = init_integration.runtime_data.coordinator
    facade.async_update_listeners()
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)

    initial = await _subscribe(client, init_integration)
    states = {n["id"]: n["state"] for n in initial["nodes"] if n["kind"] == "wan"}
    assert states["wan:uuid-gw:wan"] == "online"

    devices = data["devices"][SITE]
    gateway = devices["uuid-gw"]
    data = _publish_site(
        init_integration,
        "devices",
        {
            **devices,
            "uuid-gw": {
                **gateway,
                "wans": [
                    {**gateway["wans"][0], "connected": False},
                    gateway["wans"][1],
                ],
            },
        },
    )
    facade.async_update_listeners()
    pushed = await client.receive_json()

    assert pushed["type"] == "event"
    assert pushed["event"]["revision"] != initial["revision"]
    states = {
        n["id"]: n["state"] for n in pushed["event"]["nodes"] if n["kind"] == "wan"
    }
    assert states["wan:uuid-gw:wan"] == "offline"


async def test_node_details_wan_with_repeated_key_matches_the_snapshot(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Keys differing only in case resolve to the first WAN, as in the snapshot."""
    data = _seed_wans(init_integration)
    devices = data["devices"][SITE]
    data = _publish_site(
        init_integration,
        "devices",
        {
            **devices,
            "uuid-gw": {
                **devices["uuid-gw"],
                "wans": [
                    {"key": "wan", "name": "Fiber", "connected": True, "ip": WAN_IP},
                    {
                        "key": "WAN",
                        "name": "Other",
                        "connected": True,
                        "ip": "203.0.113.9",
                    },
                ],
            },
        },
    )
    client = await hass_ws_client(hass)
    snapshot = await _snapshot(client, init_integration)
    (wan,) = [node for node in snapshot["nodes"] if node["kind"] == "wan"]

    msg = await _node_details(client, init_integration, wan["id"], msg_id=2)

    assert wan["name"] == "Fiber"
    assert msg["success"], msg
    assert msg["result"] == {"kind": "wan", "ip_address": WAN_IP}


@pytest.mark.parametrize(
    "case",
    [
        (
            "unifi_insights/topology/subscribe",
            "build_site_topology",
            build_site_topology,
        ),
        (
            "unifi_insights/site_health/subscribe",
            "build_site_health_snapshot",
            build_site_health_snapshot,
        ),
        (
            "unifi_insights/performance/subscribe",
            "build_performance_snapshot",
            build_performance_snapshot,
        ),
    ],
    ids=["topology", "site_health", "performance"],
)
async def test_subscriptions_rebuild_when_only_network_facts_change(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client,
    case: tuple[str, str, Any],
) -> None:
    """A replaced facts map alone rebuilds; equal content still pushes nothing."""
    command, builder_name, builder = case
    data = _seed(init_integration)
    data = _publish(
        init_integration,
        **{
            NETWORK_FACTS_KEY: {
                **(data.get(NETWORK_FACTS_KEY) or {}),
                SITE: build_site_network_facts(data["devices"][SITE], {}),
            }
        },
    )
    facade = init_integration.runtime_data.coordinator
    facade.async_update_listeners()
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": command,
            "entry_id": init_integration.entry_id,
            "site_id": SITE,
        }
    )
    assert (await client.receive_json())["success"]
    assert (await client.receive_json())["type"] == "event"

    with patch(
        f"custom_components.unifi_insights.websocket_api.{builder_name}",
        wraps=builder,
    ) as spy:
        facade.async_update_listeners()
        await _assert_no_event(client, 2)
        assert spy.call_count == 0

        # Only the facts map is republished; devices and stats are the same
        # objects. The inputs changed, so the snapshot is rebuilt, and its
        # content did not, so nothing is pushed.
        data = _publish(
            init_integration,
            **{
                NETWORK_FACTS_KEY: {
                    **require_not_none(data[NETWORK_FACTS_KEY]),
                    SITE: build_site_network_facts(data["devices"][SITE], {}),
                }
            },
        )
        facade.async_update_listeners()
        await _assert_no_event(client, 3)
        assert spy.call_count == 1
