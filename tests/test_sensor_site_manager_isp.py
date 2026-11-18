# Copyright 2026 UniFi Insights contributors
"""Test entry-scoped Site Manager WAN diagnostic sensors."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from homeassistant.const import CONF_API_KEY, PERCENTAGE, UnitOfDataRate
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.unifi_insights.console_identity import build_cloud_site_id
from custom_components.unifi_insights.const import (
    CONF_CONNECTION_TYPE,
    CONF_CONSOLE_ID,
    CONNECTION_TYPE_LOCAL,
    CONNECTION_TYPE_REMOTE,
    DOMAIN,
)
from custom_components.unifi_insights.coordinators.facade import FacadeOwner
from custom_components.unifi_insights.sensor import (
    SITE_MANAGER_ISP_SENSORS,
    UnifiSiteManagerIspSensor,
    async_setup_entry,
)
from tests.fixtures.facade_data import facade_data

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


HOST = "console:account"
SITE = "cloud:site"


def _snapshot(
    timestamp: str,
    *,
    available: bool = True,
    host: str = HOST,
    name_host: str = "foreign",
):
    return facade_data(
        site_manager={
            "collections": {"isp_metrics": {"available": available}},
            "selected_host_id": "foreign",
            "sites": {SITE: {"hostId": name_host, "meta": {"name": "Reported site"}}},
            "isp_metrics": {
                host: {
                    SITE: {
                        "metric_time": timestamp,
                        "wan": {"download_kbps": 0, "packetLoss": False},
                        "isp": {"name": "ISP", "asn": "123"},
                    },
                    "second": {"metric_time": timestamp, "wan": {"avgLatency": 30}},
                },
                "foreign": {
                    SITE: {"metric_time": timestamp, "wan": {"download_kbps": 99}}
                },
            },
        },
    )


async def _setup(
    hass: HomeAssistant, coordinator: MagicMock, host: str | None, mode: str
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONNECTION_TYPE: mode,
            CONF_CONSOLE_ID: host,
            CONF_API_KEY: "private-key",
        },
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        coordinator=coordinator,
        mobility_coordinator=None,
        access_coordinator=None,
    )
    add = MagicMock()
    await async_setup_entry(hass, entry, add)
    listener = coordinator.async_add_listener.call_args.args[0]
    return add, listener


def test_wan_sensor_units_match_the_live_capture() -> None:
    """Uptime is a share of the period; downtime has no confirmed unit."""
    by_key = {d.key: d for d in SITE_MANAGER_ISP_SENSORS}
    assert "isp_downtime" not in by_key
    uptime = by_key["isp_uptime"]
    assert uptime.native_unit_of_measurement == PERCENTAGE
    assert uptime.device_class is None
    assert not uptime.entity_registry_enabled_default


def test_cloud_device_ids_keep_exact_host_boundaries() -> None:
    """IDs use exact host and site components and reject credential identities."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CONSOLE_ID: "Host-1", CONF_API_KEY: "private-key"},
    )
    other_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CONSOLE_ID: "host:1", CONF_API_KEY: "private-key"},
    )
    invalid_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CONSOLE_ID: "private-key", CONF_API_KEY: "private-key"},
    )
    assert build_cloud_site_id(entry, "site:1") == (
        "unifi_insights_site_manager:Host-1:site%3A1"
    )
    assert build_cloud_site_id(other_entry, "site:1") == (
        "unifi_insights_site_manager:host%3A1:site%3A1"
    )
    assert build_cloud_site_id(invalid_entry, "site:1") is None


async def test_remote_discovery_is_scoped_and_adds_late_samples(
    hass: HomeAssistant, mock_coordinator: MagicMock
) -> None:
    """Discovery stays on the exact host and adds each valid metric once."""
    timestamp = "2026-11-17T04:00:00+00:00"
    mock_coordinator.data = _snapshot(timestamp, host="different")
    add, listener = await _setup(hass, mock_coordinator, HOST, CONNECTION_TYPE_REMOTE)
    add.assert_not_called()
    mock_coordinator.data = _snapshot(timestamp)
    listener()
    listener()
    add.assert_called_once()
    entities = add.call_args.args[0]
    assert all(isinstance(entity, UnifiSiteManagerIspSensor) for entity in entities)
    by_key = {entity.entity_description.key: entity for entity in entities}
    assert {key: entity.native_value for key, entity in by_key.items()} == {
        "isp_download": 0,
        "isp_sample_time": datetime.fromisoformat(timestamp),
        "isp_provider": "ISP",
        "isp_average_latency": 30,
    }
    download = by_key["isp_download"]
    assert download.native_unit_of_measurement == UnitOfDataRate.KILOBITS_PER_SECOND
    assert download.extra_state_attributes == {"sample_period": "5m"}
    assert (
        download.unique_id
        == "unifi_insights_site_manager:console%3Aaccount:cloud%3Asite:isp_download"
    )
    assert download.device_info.name == "UniFi Site Manager (cloud:site)"
    assert by_key["isp_provider"].extra_state_attributes == {"isp_asn": "123"}
    assert not by_key["isp_provider"].entity_registry_enabled_default
    assert by_key["isp_sample_time"].extra_state_attributes is None

    local_add, _ = await _setup(hass, mock_coordinator, HOST, CONNECTION_TYPE_LOCAL)
    local_add.assert_not_called()


async def test_cloud_discovery_rejects_missing_identity_and_bad_timestamps(
    hass: HomeAssistant, mock_coordinator: MagicMock
) -> None:
    """Only a valid host and timezone-aware source time may seed an entity."""
    valid_time = "2026-11-17T04:00:00Z"
    mock_coordinator.data = _snapshot(valid_time)
    add, _ = await _setup(hass, mock_coordinator, None, CONNECTION_TYPE_REMOTE)
    add.assert_not_called()

    mock_coordinator.data = _snapshot(valid_time, host="private-key")
    add, _ = await _setup(hass, mock_coordinator, "private-key", CONNECTION_TYPE_REMOTE)
    add.assert_not_called()

    mock_coordinator.data = _snapshot("2026-11-17T04:00:00", name_host=HOST)
    add, discover = await _setup(hass, mock_coordinator, HOST, CONNECTION_TYPE_REMOTE)
    add.assert_not_called()
    mock_coordinator.data = _snapshot("bad timestamp", name_host=HOST)
    discover()
    add.assert_not_called()
    mock_coordinator.data = _snapshot(valid_time, name_host=HOST)
    discover()
    sensor = next(
        e for e in add.call_args.args[0] if e.entity_description.key == "isp_download"
    )
    assert sensor.device_info.name == "UniFi Site Manager (Reported site)"


async def test_wan_sample_expires_even_after_a_successful_poll(
    hass: HomeAssistant,
    mock_coordinator: MagicMock,
    freezer,
) -> None:
    """The timer expires the source sample, and healthy new data recovers it."""
    now = datetime(2026, 11, 17, 4, tzinfo=UTC)
    freezer.move_to(now)
    mock_coordinator.data = _snapshot(now.isoformat())
    add, _ = await _setup(hass, mock_coordinator, HOST, CONNECTION_TYPE_REMOTE)
    sensor = next(
        e for e in add.call_args.args[0] if e.entity_description.key == "isp_download"
    )
    sensor.hass = hass
    assert sensor.available
    mock_coordinator.last_update_success = False
    assert sensor.available
    mock_coordinator.data = _snapshot(now.isoformat(), host="another-console")
    assert not sensor.available
    mock_coordinator.data = _snapshot(now.isoformat())

    with patch.object(sensor, "async_write_ha_state") as write:
        await sensor.async_added_to_hass()
        assert (
            mock_coordinator.async_add_listener.call_args.args[1]
            is FacadeOwner.SITE_MANAGER
        )
        mock_coordinator.data = _snapshot(now.isoformat(), available=False)
        sensor._handle_coordinator_update()
        assert not sensor.available

        mock_coordinator.data = _snapshot(now.isoformat())
        sensor._handle_coordinator_update()
        assert sensor.available
        write.reset_mock()
        freezer.move_to(now + timedelta(hours=1))
        async_fire_time_changed(hass, now + timedelta(hours=1))
        await hass.async_block_till_done()
        assert not sensor.available
        write.assert_called_once()

        mock_coordinator.data = _snapshot(now.isoformat())
        sensor._handle_coordinator_update()
        assert not sensor.available

        fresh = now + timedelta(hours=1)
        mock_coordinator.data = _snapshot(fresh.isoformat())
        sensor._handle_coordinator_update()
        assert sensor.available
        await sensor.async_will_remove_from_hass()
        write.reset_mock()
        freezer.move_to(fresh + timedelta(hours=1))
        async_fire_time_changed(hass, fresh + timedelta(hours=1))
        await hass.async_block_till_done()
        write.assert_not_called()
        sensor.async_entity_id_changed("sensor.previous")
        sensor._call_on_remove_callbacks()
