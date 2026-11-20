# Copyright 2026 UniFi Insights contributors
"""Tests for the admin-only device groups WebSocket commands (issue #519)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock, patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.unifi_insights import CarrierFabricData
from custom_components.unifi_insights.const import (
    CONF_GROUP_DEVICES_BY_UPLINK,
    DOMAIN,
)
from custom_components.unifi_insights.websocket_api import (
    _DEVICE_GROUP_STREAMS,
    async_end_device_group_streams,
    ws_device_groups_subscribe,
)
from tests.fixtures.facade_data import with_sections

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

SUBSCRIBE = "unifi_insights/device_groups/subscribe"
SOURCES = "unifi_insights/device_groups/sources"


async def _enable(
    hass: HomeAssistant, entry: MockConfigEntry, *, on: bool = True
) -> None:
    """Toggle the option; a grouping-only change applies in place."""
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_GROUP_DEVICES_BY_UPLINK: on}
    )
    await hass.async_block_till_done()


def _own_device(hass: HomeAssistant, entry: MockConfigEntry, ident: str, name: str):
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, ident)}, name=name
    )


async def _subscribe(client: Any, entry_id: str, msg_id: int = 1) -> dict[str, Any]:
    """Subscribe and return the initial snapshot."""
    await client.send_json({"id": msg_id, "type": SUBSCRIBE, "entry_id": entry_id})
    ack = await client.receive_json()
    assert ack["success"], ack
    assert ack["result"] is None
    event = await client.receive_json()
    assert event["type"] == "event"
    return event["event"]


async def _next_event(client: Any) -> dict[str, Any]:
    msg = await client.receive_json()
    assert msg["type"] == "event", msg
    return msg["event"]


async def _assert_no_event(client: Any, ping_id: int) -> None:
    """Prove nothing was pushed: the next message is the ping's pong."""
    await client.send_json({"id": ping_id, "type": "ping"})
    msg = await client.receive_json()
    assert msg["type"] == "pong", msg


def _rows(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["key"]: row for s in payload["sections"] for row in s["rows"]}


async def test_sources_list_only_enabled_loaded_entries(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Only loaded Network/Protect entries with the option on are sources."""
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": SOURCES})
    msg = await client.receive_json()
    assert msg["result"] == {"schema_version": 1, "entries": []}

    carrier = MockConfigEntry(domain=DOMAIN, title="A carrier", entry_id="cf")
    carrier.add_to_hass(hass)
    carrier.mock_state(hass, carrier.state.LOADED)
    carrier.runtime_data = CarrierFabricData(client=None, coordinator=None)  # type: ignore[arg-type]
    await _enable(hass, init_integration)

    await client.send_json({"id": 2, "type": SOURCES})
    msg = await client.receive_json()
    assert msg["result"] == {
        "schema_version": 1,
        "entries": [
            {"entry_id": init_integration.entry_id, "title": init_integration.title}
        ],
    }


async def test_sources_scrub_addresses_from_console_titles(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """The source picker also uses address-free labels."""
    hass.config_entries.async_update_entry(
        init_integration, title="Console (192.0.2.4) [AABB.CCDD.EEFF]"
    )
    await hass.async_block_till_done()
    await _enable(hass, init_integration)

    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": SOURCES})
    msg = await client.receive_json()

    assert msg["result"]["entries"] == [
        {"entry_id": init_integration.entry_id, "title": "Console"}
    ]


async def test_commands_require_admin(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client,
    hass_read_only_access_token: str,
) -> None:
    """A non-admin is refused by both commands and receives no data."""
    await _enable(hass, init_integration)
    client = await hass_ws_client(hass, hass_read_only_access_token)

    await client.send_json({"id": 1, "type": SOURCES})
    sources = await client.receive_json()
    await client.send_json(
        {"id": 2, "type": SUBSCRIBE, "entry_id": init_integration.entry_id}
    )
    subscribe = await client.receive_json()

    for msg in (sources, subscribe):
        assert not msg["success"]
        assert msg["error"]["code"] == "unauthorized"
    await _assert_no_event(client, 3)


@pytest.mark.parametrize(
    ("entry_id", "code"),
    [("missing", "entry_not_found"), ("test_entry_id", "grouping_disabled")],
)
async def test_subscribe_errors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client,
    entry_id: str,
    code: str,
) -> None:
    """Unknown entries and entries with the option off are refused."""
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": SUBSCRIBE, "entry_id": entry_id})
    msg = await client.receive_json()

    assert not msg["success"]
    assert msg["error"]["code"] == code


async def test_subscribe_unloaded_entry(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """An entry that is not loaded is refused with entry_not_loaded."""
    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": SUBSCRIBE, "entry_id": init_integration.entry_id}
    )
    msg = await client.receive_json()

    assert msg["error"]["code"] == "entry_not_loaded"


async def test_subscribe_snapshot_and_registry_updates(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Registry renames and membership changes of owned rows push snapshots."""
    entry = init_integration
    box = _own_device(hass, entry, "box", "Box")
    await _enable(hass, entry)
    client = await hass_ws_client(hass)

    snapshot = await _subscribe(client, entry.entry_id)
    assert snapshot["status"] == "ok"
    assert snapshot["entry_id"] == entry.entry_id
    assert _rows(snapshot)[f"dev:{box.id}"]["name"] == "Box"

    # Registry-only rename.
    dr.async_get(hass).async_update_device(box.id, name_by_user="Renamed")
    await hass.async_block_till_done()
    renamed = await _next_event(client)
    assert _rows(renamed)[f"dev:{box.id}"]["name"] == "Renamed"
    assert renamed["revision"] != snapshot["revision"]

    # Membership: a new owned row appears, a removed one disappears.
    extra = _own_device(hass, entry, "extra", "Extra")
    await hass.async_block_till_done()
    assert f"dev:{extra.id}" in _rows(await _next_event(client))
    dr.async_get(hass).async_remove_device(box.id)
    await hass.async_block_till_done()
    assert f"dev:{box.id}" not in _rows(await _next_event(client))

    # Another entry's rows never trigger a push.
    foreign = MockConfigEntry(domain=DOMAIN, entry_id="foreign")
    foreign.add_to_hass(hass)
    _own_device(hass, foreign, "foreign", "Foreign")
    er.async_get(hass).async_get_or_create(
        "camera", DOMAIN, "foreign_cam", config_entry=foreign
    )
    er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, "own_sensor", config_entry=entry
    )
    await hass.async_block_till_done()
    await _assert_no_event(client, 2)


async def test_camera_entity_changes_rebuild(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Owned camera entities (camera identity) trigger rebuilds, renames too."""
    entry = init_integration
    await _enable(hass, entry)
    facade = entry.runtime_data.coordinator
    facade.data = with_sections(
        facade.data,
        protect={
            **facade.data.get("protect", {}),
            "cameras": {"cam9": {"id": "cam9", "name": "Yard"}},
        },
    )
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    snapshot = await _subscribe(client, entry.entry_id)
    (camera,) = [row for row in _rows(snapshot).values() if row["kind"] == "camera"]
    camera_key = camera["key"]
    assert camera["ha_device_id"] is None

    device = _own_device(hass, entry, "protect_camera_cam9", "Yard cam")
    await hass.async_block_till_done()
    await _next_event(client)  # the new device row in "other"
    camera = er.async_get(hass).async_get_or_create(
        "camera",
        DOMAIN,
        f"{DOMAIN}_camera_cam9",
        config_entry=entry,
        device_id=device.id,
    )
    await hass.async_block_till_done()
    linked = await _next_event(client)
    assert _rows(linked)[camera_key]["ha_device_id"] == device.id

    # A rename (old_entity_id) of a tracked camera entity is relevant; the
    # resulting snapshot is unchanged, so nothing is sent.
    er.async_get(hass).async_update_entity(
        camera.entity_id, new_entity_id="camera.renamed_yard"
    )
    await hass.async_block_till_done()
    await _assert_no_event(client, 5)

    er.async_get(hass).async_remove("camera.renamed_yard")
    await hass.async_block_till_done()
    unlinked = await _next_event(client)
    assert _rows(unlinked)[camera_key]["ha_device_id"] is None


async def test_coordinator_updates_push_only_changes(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Unchanged inputs skip the rebuild; a changed name pushes a new revision."""
    entry = init_integration
    await _enable(hass, entry)
    client = await hass_ws_client(hass)
    await _subscribe(client, entry.entry_id)
    facade = entry.runtime_data.coordinator

    # Same objects: no rebuild at all.
    with patch(
        "custom_components.unifi_insights.websocket_api._device_groups_payload"
    ) as build:
        facade.async_update_listeners()
        build.assert_not_called()

    # New objects with the same content: rebuilt, same revision, no push.
    facade.data = with_sections(facade.data, devices=dict(facade.data["devices"]))
    facade.async_update_listeners()
    await _assert_no_event(client, 2)

    site_id = "site-1"
    config = entry.runtime_data.config_coordinator
    config.data["sites"] = {**config.data.get("sites", {}), site_id: {"id": site_id}}
    facade.data = with_sections(
        facade.data, devices={site_id: {"d1": {"id": "d1", "name": "Renamed device"}}}
    )
    facade.async_update_listeners()
    update = await _next_event(client)
    (row,) = _rows(update).values()
    assert row["name"] == "Renamed device"


async def test_rebuild_failure_is_logged_and_stream_survives(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A builder error is logged per entry; the next good build still flows."""
    entry = init_integration
    box = _own_device(hass, entry, "box", "Box")
    await _enable(hass, entry)
    client = await hass_ws_client(hass)
    await _subscribe(client, entry.entry_id)

    with (
        caplog.at_level(logging.ERROR),
        patch(
            "custom_components.unifi_insights.websocket_api.build_device_groups",
            side_effect=RuntimeError("boom"),
        ),
    ):
        dr.async_get(hass).async_update_device(box.id, name_by_user="X")
        await hass.async_block_till_done()
    assert "Failed to rebuild the device groups" in caplog.text
    await _assert_no_event(client, 2)

    dr.async_get(hass).async_update_device(box.id, name_by_user="Y")
    await hass.async_block_till_done()
    assert _rows(await _next_event(client))[f"dev:{box.id}"]["name"] == "Y"


async def test_unload_ends_stream_with_entry_unloaded(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Unloading the entry sends the terminal event and stops listening."""
    entry = init_integration
    await _enable(hass, entry)
    client = await hass_ws_client(hass)
    await _subscribe(client, entry.entry_id)

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert await _next_event(client) == {
        "schema_version": 1,
        "entry_id": entry.entry_id,
        "status": "entry_unloaded",
        "revision": "",
        "stale": False,
        "sections": [],
    }
    assert entry.entry_id not in hass.data.get(_DEVICE_GROUP_STREAMS, {})
    _own_device(hass, entry, "late", "Late")
    await hass.async_block_till_done()
    await _assert_no_event(client, 2)


async def test_turning_option_off_drains_streams(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """An in-place toggle off ends every stream with grouping_disabled."""
    entry = init_integration
    await _enable(hass, entry)
    client = await hass_ws_client(hass)
    await _subscribe(client, entry.entry_id, msg_id=1)
    await _subscribe(client, entry.entry_id, msg_id=2)

    await _enable(hass, entry, on=False)

    ended = {}
    for _ in range(2):
        msg = await client.receive_json()
        ended[msg["id"]] = msg["event"]["status"]
    assert ended == {1: "grouping_disabled", 2: "grouping_disabled"}
    assert entry.entry_id not in hass.data[_DEVICE_GROUP_STREAMS]
    # Draining again, or an entry with no streams, is a no-op.
    async_end_device_group_streams(hass, entry.entry_id)
    await _assert_no_event(client, 3)


async def test_unsubscribe_keeps_other_streams(
    hass: HomeAssistant, init_integration: MockConfigEntry, hass_ws_client
) -> None:
    """Unsubscribing one stream leaves the entry's other stream running."""
    entry = init_integration
    box = _own_device(hass, entry, "box", "Box")
    await _enable(hass, entry)
    client = await hass_ws_client(hass)
    await _subscribe(client, entry.entry_id, msg_id=1)
    await _subscribe(client, entry.entry_id, msg_id=2)

    await client.send_json({"id": 3, "type": "unsubscribe_events", "subscription": 1})
    assert (await client.receive_json())["success"]
    assert len(hass.data[_DEVICE_GROUP_STREAMS][entry.entry_id]) == 1

    dr.async_get(hass).async_update_device(box.id, name_by_user="Z")
    await hass.async_block_till_done()
    msg = await client.receive_json()
    assert msg["id"] == 2
    await _assert_no_event(client, 4)


async def test_stream_cleanup_callbacks_are_idempotent(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Unsubscribe, drain and unload overlapping leave nothing behind, once."""
    entry = init_integration
    await _enable(hass, entry)
    connection = Mock()
    connection.subscriptions = {}
    ws_device_groups_subscribe(hass, connection, {"id": 7, "entry_id": entry.entry_id})
    (end,) = hass.data[_DEVICE_GROUP_STREAMS][entry.entry_id]
    unsubscribe = connection.subscriptions[7]
    sent = connection.send_message.call_count

    unsubscribe()
    unsubscribe()
    end("grouping_disabled")
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.entry_id not in hass.data[_DEVICE_GROUP_STREAMS]
    assert connection.send_message.call_count == sent
