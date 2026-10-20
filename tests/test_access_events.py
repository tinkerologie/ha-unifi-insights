# Copyright 2026 UniFi Insights contributors
"""Verify Access doorbell rings and verified access-result events."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

from homeassistant.const import CONF_API_KEY
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_insights import UnifiInsightsData
from custom_components.unifi_insights import event as event_platform
from custom_components.unifi_insights.const import CONF_ACCESS_TOKEN, DOMAIN
from custom_components.unifi_insights.coordinators.access import (
    access_doorbell_signal,
    access_result_signal,
    async_dispatch_access_result,
)
from tests.fixtures.access import DEVICES, DOORS, ENTRY_DATA, _client, _ready

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


def _collector(added: list[Any]) -> AddEntitiesCallback:
    """Return a platform add callback that appends entities to ``added``."""

    def _add(new_entities: Iterable[Entity], update_before_add: object = None) -> None:
        """Accept HA's optional update flag while only collecting entities."""
        added.extend(new_entities)

    return _add


def _entry(hass: Any, data: dict[str, Any] | None = None) -> MockConfigEntry:
    """Return an entry with the runtime data the event platform reads."""
    entry = MockConfigEntry(domain=DOMAIN, data=data or dict(ENTRY_DATA))
    entry.add_to_hass(hass)
    entry.runtime_data = UnifiInsightsData(
        config_coordinator=MagicMock(name="config"),
        device_coordinator=MagicMock(name="device"),
        protect_coordinator=None,
        network_client=MagicMock(name="network"),
        protect_client=None,
    )
    return entry


async def _wired(hass: Any, *, devices: list[Any] | None = None) -> tuple[Any, ...]:
    """Attach an Access coordinator and run the event setup, collecting entities."""
    entry = _entry(hass)
    coordinator = await _ready(
        hass,
        _client(doors=DOORS, devices=devices if devices is not None else DEVICES),
        entry,
    )
    entry.runtime_data.access_coordinator = coordinator
    added: list[Any] = []
    event_platform.async_setup_access_events(hass, entry, _collector(added))
    return entry, coordinator, added


async def _added(entity: Any) -> None:
    """Add an entity the way Home Assistant does, without writing to a real platform."""
    with patch.object(entity, "async_write_ha_state"):
        await entity.async_added_to_hass()


def _doorbells(added: list[Any]) -> dict[str, Any]:
    return {
        e._device_id: e
        for e in added
        if isinstance(e, event_platform.UnifiAccessDoorbellEvent)
    }


def _results(added: list[Any]) -> dict[str, Any]:
    return {
        e._door_id: e
        for e in added
        if isinstance(e, event_platform.UnifiAccessResultEvent)
    }


async def test_setup_is_a_no_op_without_namespace(hass: Any) -> None:
    """Without a valid console namespace no event entities and no listeners exist."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_API_KEY: "key", CONF_ACCESS_TOKEN: "token"}
    )
    entry.add_to_hass(hass)
    entry.runtime_data = UnifiInsightsData(
        config_coordinator=MagicMock(),
        device_coordinator=MagicMock(),
        protect_coordinator=None,
        network_client=MagicMock(),
        protect_client=None,
    )
    entry.runtime_data.access_coordinator = await _ready(
        hass, _client(doors=DOORS, devices=DEVICES), entry
    )
    added: list[Any] = []

    event_platform.async_setup_access_events(hass, entry, _collector(added))
    async_dispatcher_send(
        hass, access_doorbell_signal(entry.entry_id), {"device_id": "reader"}
    )
    await hass.async_block_till_done()

    assert added == []


async def test_known_doorbell_device_is_created_without_ringing(hass: Any) -> None:
    """Discovery from the device list creates a doorbell without firing a ring."""
    _, _, added = await _wired(hass)
    doorbells = _doorbells(added)

    assert set(doorbells) == {"reader"}
    entity = doorbells["reader"]
    assert entity.unique_id.endswith("_reader_doorbell")
    with patch.object(entity, "_trigger_event") as trigger:
        await _added(entity)
    trigger.assert_not_called()


async def test_ring_for_a_known_device_fires_one_ring_event(hass: Any) -> None:
    """A live ring frame for a known doorbell fires one ring event on that entity."""
    entry, _, added = await _wired(hass)
    entity = _doorbells(added)["reader"]
    await _added(entity)

    with (
        patch.object(entity, "_trigger_event") as trigger,
        patch.object(entity, "async_write_ha_state"),
    ):
        async_dispatcher_send(
            hass,
            access_doorbell_signal(entry.entry_id),
            {
                "device_id": "reader",
                "device_name": "Lobby",
                "door_name": "Front",
                "device_type": "UA-G2-PRO",
            },
        )
        await hass.async_block_till_done()

    trigger.assert_called_once_with("ring")


async def test_ring_from_an_unknown_device_creates_and_fires_once(hass: Any) -> None:
    """A ring from hardware the device list did not name creates that entity."""
    entry, _, added = await _wired(hass, devices=[])
    assert added == []

    async_dispatcher_send(
        hass,
        access_doorbell_signal(entry.entry_id),
        {
            "device_id": "new-reader",
            "device_name": "Side",
            "door_name": None,
            "device_type": "UA-G2-PRO",
        },
    )
    await hass.async_block_till_done()

    assert set(_doorbells(added)) == {"new-reader"}
    entity = _doorbells(added)["new-reader"]
    assert entity._pending_ring is True
    with (
        patch.object(entity, "_trigger_event") as trigger,
        patch.object(entity, "async_write_ha_state"),
    ):
        await entity.async_added_to_hass()
    trigger.assert_called_once_with("ring")
    assert entity._pending_ring is False


async def test_ring_before_add_is_held_then_fired_on_add(hass: Any) -> None:
    """A ring that arrives before Home Assistant adds the entity is fired on add."""
    _, coordinator, _ = await _wired(hass)
    entity = event_platform.UnifiAccessDoorbellEvent(coordinator, "reader", None)

    entity.async_ring()
    assert entity._pending_ring is True

    with (
        patch.object(entity, "_trigger_event") as trigger,
        patch.object(entity, "async_write_ha_state"),
    ):
        await entity.async_added_to_hass()
    trigger.assert_called_once_with("ring")


async def test_reload_does_not_repeat_an_old_ring(hass: Any) -> None:
    """An entity created without a live ring never fires one, so reloads stay quiet."""
    _, coordinator, _ = await _wired(hass)
    entity = event_platform.UnifiAccessDoorbellEvent(coordinator, "reader", None)

    with patch.object(entity, "_trigger_event") as trigger:
        await _added(entity)

    trigger.assert_not_called()
    assert entity._pending_ring is False


async def test_doorbell_is_unavailable_when_notifications_are_denied(hass: Any) -> None:
    """Once the notification endpoint refuses, doorbell entities report unavailable."""
    _, coordinator, added = await _wired(hass)
    entity = _doorbells(added)["reader"]
    assert entity.available

    coordinator._deny("notifications", 403)

    assert not entity.available


async def test_result_entities_exist_only_while_webhook_is_active(hass: Any) -> None:
    """Result entities are created only once the verified webhook is active."""
    _, coordinator, added = await _wired(hass)
    assert _results(added) == {}

    coordinator.async_set_webhook_active(active=True)

    assert set(_results(added)) == {"door-gate", "door-plain", "door-unbound"}


async def test_granted_and_denied_results_fire_the_matching_event(hass: Any) -> None:
    """A verified result fires the event type for its door, with no secrets."""
    entry, coordinator, added = await _wired(hass)
    coordinator.async_set_webhook_active(active=True)
    entity = _results(added)["door-plain"]
    await _added(entity)

    with (
        patch.object(entity, "_trigger_event") as trigger,
        patch.object(entity, "async_write_ha_state"),
    ):
        async_dispatch_access_result(
            hass,
            entry.entry_id,
            {"door_id": "door-plain", "result": "granted", "authentication": "nfc"},
        )
        async_dispatch_access_result(
            hass,
            entry.entry_id,
            {"door_id": "door-plain", "result": "denied", "authentication": None},
        )
        await hass.async_block_till_done()

    assert trigger.call_args_list[0].args == (
        "access_granted",
        {"door_id": "door-plain", "authentication": "nfc"},
    )
    assert trigger.call_args_list[1].args == (
        "access_denied",
        {"door_id": "door-plain", "authentication": None},
    )


async def test_result_for_unknown_door_or_result_is_ignored(hass: Any) -> None:
    """Results for doors without an entity, or with unknown results, fire nothing."""
    entry, coordinator, added = await _wired(hass)
    coordinator.async_set_webhook_active(active=True)
    entity = _results(added)["door-plain"]
    await _added(entity)

    with patch.object(entity, "_trigger_event") as trigger:
        async_dispatch_access_result(
            hass,
            entry.entry_id,
            {"door_id": "missing-door", "result": "granted", "authentication": None},
        )
        async_dispatch_access_result(
            hass,
            entry.entry_id,
            {"door_id": "door-plain", "result": "maybe", "authentication": None},
        )
        await hass.async_block_till_done()

    trigger.assert_not_called()


async def test_result_before_add_is_dropped(hass: Any) -> None:
    """A result for an entity that Home Assistant has not added is not written."""
    _, coordinator, _ = await _wired(hass)
    entity = event_platform.UnifiAccessResultEvent(coordinator, "door-plain")

    with patch.object(entity, "_trigger_event") as trigger:
        entity.async_result("granted", None)

    trigger.assert_not_called()


async def test_result_entity_is_unavailable_when_webhook_turns_off(hass: Any) -> None:
    """Turning the webhook off makes its result entities unavailable."""
    _, coordinator, added = await _wired(hass)
    coordinator.async_set_webhook_active(active=True)
    entity = _results(added)["door-plain"]
    assert entity.available

    coordinator.async_set_webhook_active(active=False)

    assert not entity.available


async def test_result_setup_listens_on_the_entry_scoped_signal(hass: Any) -> None:
    """Results sent on another entry's signal never reach this entry's entities."""
    _, coordinator, added = await _wired(hass)
    coordinator.async_set_webhook_active(active=True)
    entity = _results(added)["door-plain"]
    await _added(entity)

    with patch.object(entity, "_trigger_event") as trigger:
        async_dispatcher_send(
            hass,
            access_result_signal("another-entry"),
            {"door_id": "door-plain", "result": "granted", "authentication": None},
        )
        await hass.async_block_till_done()

    trigger.assert_not_called()


async def test_doorbell_signal_is_entry_scoped(hass: Any) -> None:
    """Rings sent on another entry's signal do not create entities here."""
    _, _, added = await _wired(hass, devices=[])

    async_dispatcher_send(
        hass,
        access_doorbell_signal("another-entry"),
        {
            "device_id": "stranger",
            "device_name": None,
            "door_name": None,
            "device_type": None,
        },
    )
    await hass.async_block_till_done()

    assert added == []
