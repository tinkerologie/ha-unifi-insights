# Copyright 2026 UniFi Insights contributors
"""Pin every entity unique_id a seeded Network and Protect entry registers."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from unifi_official_api.network import UniFiNetworkClient
from unifi_official_api.protect import UniFiProtectClient

from custom_components.unifi_insights.const import (
    CONF_TRACK_WIFI_CLIENTS,
    CONF_TRACK_WIRED_CLIENTS,
)
from tests.fixtures.command_owners import (
    autospec_client,
    seed_network_client,
    seed_protect_client,
)
from tests.fixtures.library_responses import (
    SAMPLE_ALARM_HUB,
    SAMPLE_CLIENT,
    SAMPLE_KEYPAD_FOB,
    SAMPLE_LINK_STATION,
    SAMPLE_NVR_WITH_ARM_MODE,
    SAMPLE_PDU_DEVICE,
    SAMPLE_SIREN,
    SAMPLE_THREAD_LINK_STATION,
    SAMPLE_UDM_PRO_OVERVIEW_API,
    SAMPLE_UDM_PRO_STATS,
    SAMPLE_USL_ENTRY_SENSOR,
)
from tests.fixtures.network_rule_responses import (
    nat_rule_record,
    port_forward_record,
    static_route_record,
    traffic_rule_record,
)

if TYPE_CHECKING:
    from unittest.mock import NonCallableMagicMock

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

# Sorted (platform, unique_id) of every entity the seeded entry registers. A
# change here renames entities for every user, so it needs a migration.
EXPECTED_UNIQUE_IDS: list[tuple[str, str]] = [
    ("alarm_control_panel", "unifi_insights_nvr_nvr_1_alarm"),
    ("binary_sensor", "default_00000000-0000-4000-8000-0000000000d1_device_status"),
    ("binary_sensor", "default_00000000-0000-4000-8000-0000000000d1_wan_status"),
    ("binary_sensor", "default_pdu_device_1_device_status"),
    ("binary_sensor", "unifi_insights_alarm_hub_alarm_hub_1_alarm_hub_device_tamper"),
    ("binary_sensor", "unifi_insights_camera_doorbell_cam_1_camera_animal_detection"),
    ("binary_sensor", "unifi_insights_camera_doorbell_cam_1_camera_doorbell_ring"),
    ("binary_sensor", "unifi_insights_camera_doorbell_cam_1_camera_microphone"),
    ("binary_sensor", "unifi_insights_camera_doorbell_cam_1_camera_motion"),
    ("binary_sensor", "unifi_insights_camera_doorbell_cam_1_camera_package_detection"),
    ("binary_sensor", "unifi_insights_camera_doorbell_cam_1_camera_person_detection"),
    ("binary_sensor", "unifi_insights_camera_doorbell_cam_1_camera_vehicle_detection"),
    ("binary_sensor", "unifi_insights_camera_doorbell_cam_2_camera_animal_detection"),
    ("binary_sensor", "unifi_insights_camera_doorbell_cam_2_camera_doorbell_ring"),
    ("binary_sensor", "unifi_insights_camera_doorbell_cam_2_camera_microphone"),
    ("binary_sensor", "unifi_insights_camera_doorbell_cam_2_camera_motion"),
    ("binary_sensor", "unifi_insights_camera_doorbell_cam_2_camera_package_detection"),
    ("binary_sensor", "unifi_insights_camera_doorbell_cam_2_camera_person_detection"),
    ("binary_sensor", "unifi_insights_camera_doorbell_cam_2_camera_vehicle_detection"),
    ("binary_sensor", "unifi_insights_fob_fob_1_fob_keypad_beep"),
    (
        "binary_sensor",
        "unifi_insights_link_station_link_station_thread_thread_network_problem",
    ),
    ("binary_sensor", "unifi_insights_sensor_test_sensor_usl_sensor_door"),
    ("binary_sensor", "unifi_insights_sensor_test_sensor_usl_sensor_tamper"),
    ("button", "aabbcc0000aa_default_generate_voucher"),
    ("button", "default_00000000-0000-4000-8000-0000000000d1_device_restart"),
    ("button", "default_client_1_reconnect"),
    ("button", "default_pdu_device_1_device_restart"),
    ("button", "unifi_insights_11:22:33:44:55:66_wake"),
    ("button", "unifi_insights_chime_chime_1_play"),
    ("camera", "unifi_insights_camera_doorbell_cam_1"),
    ("camera", "unifi_insights_camera_doorbell_cam_2"),
    ("device_tracker", "unifi_insights_11:22:33:44:55:66"),
    ("event", "aabbcc0000aa_default_network_rule_changes"),
    ("event", "unifi_insights_camera_doorbell_cam_1_doorbell_event"),
    ("event", "unifi_insights_camera_doorbell_cam_2_doorbell_event"),
    ("event", "unifi_insights_sensor_test_sensor_usl_event"),
    ("image", "aabbcc0000aa_default_voucher_qr_code"),
    ("light", "unifi_insights_light_light_1"),
    ("number", "aabbcc0000aa_default_voucher_data_limit"),
    ("number", "aabbcc0000aa_default_voucher_download_limit"),
    ("number", "aabbcc0000aa_default_voucher_duration"),
    ("number", "aabbcc0000aa_default_voucher_guest_limit"),
    ("number", "aabbcc0000aa_default_voucher_upload_limit"),
    ("number", "unifi_insights_camera_doorbell_cam_1_microphone_volume"),
    ("number", "unifi_insights_camera_doorbell_cam_2_microphone_volume"),
    ("number", "unifi_insights_chime_chime_1_repeat_times"),
    ("number", "unifi_insights_chime_chime_1_volume"),
    ("number", "unifi_insights_light_light_1_brightness_level"),
    ("select", "unifi_insights_camera_doorbell_cam_1_hdr_mode"),
    ("select", "unifi_insights_camera_doorbell_cam_1_video_mode"),
    ("select", "unifi_insights_camera_doorbell_cam_2_hdr_mode"),
    ("select", "unifi_insights_camera_doorbell_cam_2_video_mode"),
    ("select", "unifi_insights_chime_chime_1_ringtone"),
    ("select", "unifi_insights_viewer_viewer_1_liveview"),
    ("sensor", "aabbcc0000aa_default_active_vouchers"),
    ("sensor", "aabbcc0000aa_default_latest_voucher_code"),
    ("sensor", "aabbcc0000aa_default_latest_voucher_expiration"),
    ("sensor", "aabbcc0000aa_default_site_total_clients"),
    ("sensor", "aabbcc0000aa_default_site_wired_clients"),
    ("sensor", "aabbcc0000aa_default_site_wireless_clients"),
    ("sensor", "default_00000000-0000-4000-8000-0000000000d1_cpu_usage"),
    ("sensor", "default_00000000-0000-4000-8000-0000000000d1_firmware_version"),
    ("sensor", "default_00000000-0000-4000-8000-0000000000d1_memory_usage"),
    ("sensor", "default_00000000-0000-4000-8000-0000000000d1_rx_rate"),
    ("sensor", "default_00000000-0000-4000-8000-0000000000d1_tx_rate"),
    ("sensor", "default_00000000-0000-4000-8000-0000000000d1_uptime"),
    ("sensor", "default_00000000-0000-4000-8000-0000000000d1_wan_ip"),
    ("sensor", "default_00000000-0000-4000-8000-0000000000d1_wan_uptime"),
    ("sensor", "default_00000000-0000-4000-8000-0000000000d1_wired_clients"),
    ("sensor", "default_pdu_device_1_ac_power_budget"),
    ("sensor", "default_pdu_device_1_ac_power_consumption"),
    ("sensor", "default_pdu_device_1_cpu_usage"),
    ("sensor", "default_pdu_device_1_firmware_version"),
    ("sensor", "default_pdu_device_1_memory_usage"),
    ("sensor", "default_pdu_device_1_outlet_1_current"),
    ("sensor", "default_pdu_device_1_outlet_1_power"),
    ("sensor", "default_pdu_device_1_outlet_1_power_factor"),
    ("sensor", "default_pdu_device_1_outlet_1_voltage"),
    ("sensor", "default_pdu_device_1_outlet_2_current"),
    ("sensor", "default_pdu_device_1_outlet_2_power"),
    ("sensor", "default_pdu_device_1_outlet_2_power_factor"),
    ("sensor", "default_pdu_device_1_outlet_2_voltage"),
    ("sensor", "default_pdu_device_1_rx_rate"),
    ("sensor", "default_pdu_device_1_tx_rate"),
    ("sensor", "default_pdu_device_1_uptime"),
    ("sensor", "default_wifi_main_client_count"),
    ("sensor", "unifi_insights_fob_fob_1_fob_arm_control"),
    ("sensor", "unifi_insights_fob_fob_1_fob_keypad_beep_volume"),
    ("sensor", "unifi_insights_link_station_link_station_thread_thread_joined_devices"),
    ("sensor", "unifi_insights_link_station_link_station_thread_thread_role"),
    ("sensor", "unifi_insights_sensor_test_sensor_usl_battery"),
    ("siren", "unifi_insights_siren_siren_1"),
    ("switch", "default_client_1_block_switch"),
    ("switch", "default_fw_rule_firewall_rule"),
    ("switch", "default_nat-dns_nat_rule"),
    ("switch", "default_pdu_device_1_outlet_1"),
    ("switch", "default_pdu_device_1_outlet_1_cycle_enabled"),
    ("switch", "default_pdu_device_1_outlet_2"),
    ("switch", "default_pdu_device_1_outlet_2_cycle_enabled"),
    ("switch", "default_pdu_device_1_outlet_3"),
    ("switch", "default_pf-plex_port_forward"),
    ("switch", "default_route-lab_static_route"),
    ("switch", "default_route_rule_policy_based_route"),
    ("switch", "default_tr-bedtime_traffic_rule"),
    ("switch", "default_vpn_client_vpn_client"),
    ("switch", "default_wifi_main_wifi_switch"),
    ("switch", "unifi_insights_camera_doorbell_cam_1_status_light"),
    ("switch", "unifi_insights_camera_doorbell_cam_2_status_light"),
    ("update", "default_00000000-0000-4000-8000-0000000000d1_firmware_update"),
    ("update", "default_pdu_device_1_firmware_update"),
]


def _network_client() -> NonCallableMagicMock:
    """Return a Network client seeded with a device, client and every config family."""
    client = autospec_client(UniFiNetworkClient)
    seed_network_client(client)
    client.devices.get_all.return_value = [
        copy.deepcopy(SAMPLE_PDU_DEVICE),
        copy.deepcopy(SAMPLE_UDM_PRO_OVERVIEW_API),
    ]
    client.devices.get_statistics.return_value = copy.deepcopy(SAMPLE_UDM_PRO_STATS)
    client.clients.get_all.return_value = [
        {**copy.deepcopy(SAMPLE_CLIENT), "type": "WIRED"}
    ]
    client.wifi.get_all.return_value = [{"id": "wifi_main", "name": "Main"}]
    client.firewall.list_rules.return_value = [
        {"id": "fw_rule", "name": "Block IoT", "enabled": False}
    ]
    client.routes.list_routes.return_value = [
        {"_id": "route_rule", "description": "Via VPN", "enabled": False}
    ]
    client.vpn_clients.list_vpn_clients.return_value = [
        {"_id": "vpn_client", "name": "Office VPN", "enabled": False}
    ]
    client.port_forwards.list_port_forwards.return_value = [port_forward_record()]
    client.traffic_rules.list_traffic_rules.return_value = [traffic_rule_record()]
    client.nat_rules.list_nat_rules.return_value = [nat_rule_record()]
    client.static_routes.list_static_routes.return_value = [static_route_record()]
    return client


def _protect_client() -> NonCallableMagicMock:
    """Return a Protect client seeded with every device family it polls."""
    client = autospec_client(UniFiProtectClient)
    seed_protect_client(client)
    client.nvr.get.return_value = copy.deepcopy(SAMPLE_NVR_WITH_ARM_MODE)
    client.sensors.get_all.return_value = [copy.deepcopy(SAMPLE_USL_ENTRY_SENSOR)]
    client.link_stations.get_all.return_value = [
        copy.deepcopy(SAMPLE_LINK_STATION),
        copy.deepcopy(SAMPLE_THREAD_LINK_STATION),
    ]
    client.alarm_hubs.get_all.return_value = [copy.deepcopy(SAMPLE_ALARM_HUB)]
    client.fobs.get_all.return_value = [copy.deepcopy(SAMPLE_KEYPAD_FOB)]
    client.sirens.get_all.return_value = [copy.deepcopy(SAMPLE_SIREN)]
    client.lights.get_all.return_value = [
        {
            "id": "light_1",
            "modelKey": "light",
            "name": "Porch Light",
            "state": "CONNECTED",
            "type": "UP FloodLight",
            "mac": "AABBCC0000E1",
            "lightModeSettings": {"mode": "motion", "enableAt": "fulltime"},
            "lightDeviceSettings": {"ledLevel": 3},
        }
    ]
    client.viewers.get_all.return_value = [
        {
            "id": "viewer_1",
            "modelKey": "viewer",
            "name": "Lobby Viewer",
            "state": "CONNECTED",
            "type": "UP Viewport",
            "mac": "AABBCC0000E2",
            "liveview": "liveview_1",
        }
    ]
    client.liveviews.get_all.return_value = [
        {"id": "liveview_1", "modelKey": "liveview", "name": "All Cameras"}
    ]
    return client


async def test_seeded_entry_unique_ids(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_local_auth: MagicMock,
    entity_registry: er.EntityRegistry,
    *,
    enable_custom_integrations: None,
) -> None:
    """Every (platform, unique_id) of a seeded entry stays exactly as pinned."""
    network = _network_client()
    protect = _protect_client()
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_TRACK_WIFI_CLIENTS: True, CONF_TRACK_WIRED_CLIENTS: True},
    )
    with (
        patch(
            "custom_components.unifi_insights.UniFiNetworkClient",
            MagicMock(return_value=network),
        ),
        patch(
            "custom_components.unifi_insights.UniFiProtectClient",
            MagicMock(return_value=protect),
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    registered = sorted(
        (row.domain, row.unique_id)
        for row in er.async_entries_for_config_entry(
            entity_registry, mock_config_entry.entry_id
        )
    )
    assert registered == EXPECTED_UNIQUE_IDS
