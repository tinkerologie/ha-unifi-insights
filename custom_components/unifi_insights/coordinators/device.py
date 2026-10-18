# Copyright 2026 UniFi Insights contributors
"""Device coordinator for UniFi Insights - handles fast-changing device data."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.unifi_insights.api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from custom_components.unifi_insights.api.network.models import (
    device_id_is_mac,
    parse_outlet_metrics,
)
from custom_components.unifi_insights.const import DOMAIN, SCAN_INTERVAL_DEVICE
from custom_components.unifi_insights.data_transforms import (
    normalize_legacy_storage,
    normalize_legacy_wans,
)
from custom_components.unifi_insights.helpers import async_get_device_entry

from .base import UnifiBaseCoordinator

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.unifi_insights.api.network import UniFiNetworkClient
    from custom_components.unifi_insights.api.protect import UniFiProtectClient

    from .config import UnifiConfigCoordinator

_LOGGER = logging.getLogger(__name__)


# Stats keys the v1 statistics endpoint may already have populated. A legacy
# reading only fills a gap, it never overwrites a value v1 supplied.
_CPU_STAT_KEYS = ("cpuUtilizationPct", "cpu_utilization_pct", "cpu_percent", "cpu")
_MEMORY_STAT_KEYS = (
    "memoryUtilizationPct",
    "memory_utilization_pct",
    "memory_percent",
    "memory",
)
_UPTIME_STAT_KEYS = ("uptimeSec", "uptime_sec", "uptime_seconds", "uptime")


def _fill_missing_stat(
    stats: dict[str, Any],
    target_key: str,
    existing_keys: tuple[str, ...],
    value: Any,
    caster: Callable[[Any], Any],
) -> None:
    """Set ``target_key`` from a legacy reading when v1 reported none."""
    if value is None or any(stats.get(key) is not None for key in existing_keys):
        return
    with suppress(TypeError, ValueError):
        stats[target_key] = caster(value)


def _legacy_system_stats(legacy_device: dict[str, Any]) -> tuple[Any, Any, Any]:
    """
    Return ``(cpu, memory, uptime)`` from a legacy controller device dict.

    Gateways keyed by MAC rather than a v1 UUID report CPU and memory
    percentages under ``system-stats``. The similarly named ``sys_stats``
    object holds raw counters (``loadavg_*``, ``mem_total``, ``mem_used``) and
    carries no ``cpu``/``mem`` keys, yet real consoles report both objects.
    Each key is therefore resolved across ``system-stats``, ``sys_stats`` and
    the device root in turn, rather than picking a single object up front.
    """
    sources = [
        source
        for source in (
            legacy_device.get("system-stats"),
            legacy_device.get("sys_stats"),
            legacy_device,
        )
        if isinstance(source, dict)
    ]

    def _pick(key: str) -> Any:
        for source in sources:
            value = source.get(key)
            if value is not None:
                return value
        return None

    return _pick("cpu"), _pick("mem"), _pick("uptime")


def _merge_legacy_system_stats(
    stats: dict[str, Any], legacy_device: dict[str, Any]
) -> None:
    """Fill CPU, memory and uptime in ``stats`` from a legacy device dict."""
    cpu, mem, uptime = _legacy_system_stats(legacy_device)
    _fill_missing_stat(stats, "cpuUtilizationPct", _CPU_STAT_KEYS, cpu, float)
    _fill_missing_stat(stats, "memoryUtilizationPct", _MEMORY_STAT_KEYS, mem, float)
    _fill_missing_stat(stats, "uptimeSec", _UPTIME_STAT_KEYS, uptime, int)


# How many consecutive polls a device's last good statistics are reused for
# when its statistics call keeps failing, before its stats are dropped. Site
# VPN connection state is bounded the same way.
MAX_STATS_REUSE_POLLS = 3


class UnifiDeviceCoordinator(UnifiBaseCoordinator):
    """
    Coordinator for fast-changing device data (30 second updates).

    Handles:
    - Device online status
    - Device statistics (CPU, memory, uptime)
    - Client connections
    - Port statistics
    """

    def __init__(
        self,
        hass: HomeAssistant,
        network_client: UniFiNetworkClient,
        protect_client: UniFiProtectClient | None,
        entry: ConfigEntry,
        config_coordinator: UnifiConfigCoordinator,
    ) -> None:
        """Initialize the device coordinator."""
        super().__init__(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=entry,
            name="device",
            update_interval=SCAN_INTERVAL_DEVICE,
        )
        self.config_coordinator = config_coordinator
        # Map integration site IDs to classic ("legacy") site names used by the
        # /api/s/{site} endpoints. Populated on each update and reused for
        # classic-API client actions (block/unblock/reconnect/forget).
        self._legacy_site_names: dict[str, str] = {}
        # Successful legacy fallback does not fail the coordinator refresh.
        self._legacy_fallback_sites: set[str] = set()
        # Track previous device IDs for stale device cleanup (Gold requirement)
        self._previous_network_device_ids: set[str] = set()
        # Track previous port byte counts for rate computation
        # device_id -> (monotonic sample time, per-port byte counters)
        self._prev_port_bytes: dict[str, tuple[float, dict[int, dict[str, int]]]] = {}
        # Consecutive polls each device's statistics call has failed for, and
        # the devices whose stats were reused (not refetched) this refresh.
        self._stats_failures: dict[str, int] = {}
        self._reused_stats: set[str] = set()
        # Consecutive polls each site's VPN connections call has failed for.
        self._vpn_connection_failures: dict[str, int] = {}
        # Consecutive polls each site's legacy device call has failed for.
        self._legacy_wan_failures: dict[str, int] = {}
        self.data: dict[str, Any] = {
            "devices": {},
            "clients": {},
            "stats": {},
            "vpn_connections": {},
            "last_update": None,
        }

    @staticmethod
    def _normalize_mac(value: Any) -> str | None:
        """Normalize a MAC address for dictionary lookups."""
        if not isinstance(value, str) or not value:
            return None
        return value.strip().lower()

    @classmethod
    def _legacy_device_for(
        cls,
        device: dict[str, Any],
        legacy_devices_by_mac: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Return the legacy controller dict matching a v1 device, if any."""
        mac = cls._normalize_mac(device.get("mac") or device.get("macAddress"))
        if mac is None:
            return None
        return legacy_devices_by_mac.get(mac)

    @staticmethod
    def _normalize_legacy_port(port: dict[str, Any]) -> dict[str, Any] | None:
        """
        Normalize a single legacy port_table entry into a v1-shaped port dict.

        Shared by ``_legacy_device_to_v1_dict()`` and
        ``_merge_legacy_port_data()`` so the legacy-primary fallback and the
        merge path produce identical port data, including network_name, SFP
        fields, PoE and byte stats.
        """
        port_idx = port.get("port_idx")
        if port_idx is None:
            return None

        normalized: dict[str, Any] = {
            "idx": port_idx,
            "port_idx": port_idx,
            "state": "UP" if port.get("up") else "DOWN",
            "enabled": port.get("enable", True),
            "speedMbps": port.get("speed"),
            "speed": port.get("speed"),
        }

        # Port type identification fields
        media = port.get("media")
        if media:
            normalized["media"] = media

        is_uplink = port.get("is_uplink")
        if is_uplink is not None:
            normalized["is_uplink"] = is_uplink

        port_name = port.get("name")
        if port_name:
            normalized["name"] = port_name

        ifname = port.get("ifname")
        if ifname:
            normalized["ifname"] = ifname

        network_name = port.get("network_name")
        if network_name:
            normalized["network_name"] = network_name

        # SFP module data
        sfp_found = port.get("sfp_found")
        if sfp_found is not None:
            normalized["sfp_found"] = sfp_found

        for sfp_key in (
            "sfp_part",
            "sfp_vendor",
            "sfp_serial",
            "sfp_compliance",
        ):
            sfp_val = port.get(sfp_key)
            if sfp_val is not None:
                normalized[sfp_key] = sfp_val

        # PoE data — only for ports with PoE hardware (port_poe flag)
        poe_capable = port.get("port_poe", False)
        if poe_capable:
            poe_enabled = port.get("poe_enable", False)
            poe_power = port.get("poe_power") or port.get("poePower")
            poe_good = port.get("poe_good", False)
            normalized["poe"] = {
                "enabled": bool(poe_enabled),
                "power": poe_power,
                "good": bool(poe_good),
            }

        # TX/RX bytes
        normalized["stats"] = {
            "txBytes": port.get("tx_bytes", 0),
            "rxBytes": port.get("rx_bytes", 0),
        }

        return normalized

    @classmethod
    def _legacy_device_to_v1_dict(cls, legacy: dict[str, Any]) -> dict[str, Any]:
        """
        Map a legacy ``/stat/device`` dict to a v1-shaped device dict.

        The v1 Device model uses ``id`` and camelCase aliases; the legacy
        endpoint returns ``_id`` and snake_case. Only the fields the
        downstream pipeline actually reads are mapped; everything else is
        preserved as-is. A legacy device without an ``_id`` follows the same
        fallback as the v1 model and is keyed on its MAC, so
        ``device_id_is_mac()`` correctly skips id-addressed endpoints for it.
        """
        mapped: dict[str, Any] = dict(legacy)

        # Ensure macAddress is populated from mac for the merge helpers
        mac = legacy.get("mac")
        if mac and "macAddress" not in mapped:
            mapped["macAddress"] = mac

        # _id → id (the Device model requires ``id``); fall back to MAC
        legacy_id = legacy.get("_id")
        if legacy_id and "id" not in mapped:
            mapped["id"] = legacy_id
        if "id" not in mapped and mac:
            mapped["id"] = mac

        # Map common snake_case legacy fields to camelCase v1 aliases
        field_map = {
            "fw_version": "firmwareVersion",
            "last_seen": "lastSeen",
            "site_id": "siteId",
            "tx_bytes": "txBytes",
            "rx_bytes": "rxBytes",
            "uplink_table": "uplinkTable",
        }
        for legacy_key, v1_key in field_map.items():
            if legacy_key in legacy and v1_key not in mapped:
                mapped[v1_key] = legacy[legacy_key]

        # Legacy "up" (bool) is what ``is_device_online()`` actually needs: it
        # reads ``state``/``status`` as a string and accepts ONLINE/CONNECTED/UP.
        # The legacy numeric ``state`` (0/1) is not a string, so it would read
        # as offline. Only fill the gap when no usable string state exists.
        if "up" in legacy and not isinstance(mapped.get("state"), str):
            mapped["state"] = "ONLINE" if legacy["up"] else "OFFLINE"

        # Normalize port_table into ports for the sensor pipeline.
        # The sensor pipeline reads device_data["ports"] and falls back to
        # interfaces["ports"]; it does not consume port_table directly.
        port_table = legacy.get("port_table")
        if isinstance(port_table, list) and port_table and "ports" not in mapped:
            ports: list[dict[str, Any]] = []
            for port in port_table:
                if not isinstance(port, dict):
                    continue
                normalized = cls._normalize_legacy_port(port)
                if normalized is not None:
                    ports.append(normalized)
            if ports:
                mapped["ports"] = ports

        # Same parent-link block the merge path produces, so topology reads
        # one shape whichever endpoint served the device list.
        topology = cls._legacy_topology_block(legacy)
        if topology:
            mapped["topology"] = topology

        return mapped

    @staticmethod
    def _has_legacy_temperature_data(legacy_device: dict[str, Any]) -> bool:
        """Return True when legacy device data contains usable temperature info."""
        general_temperature = legacy_device.get("general_temperature")
        if general_temperature is None:
            general_temperature = legacy_device.get("generalTemperature")

        temperatures = legacy_device.get("temperatures")
        has_temperature = legacy_device.get("has_temperature")
        if has_temperature is None:
            has_temperature = legacy_device.get("hasTemperature")

        has_temperature_entries = isinstance(temperatures, list) and any(
            isinstance(item, dict) and item.get("value") is not None
            for item in temperatures
        )

        return bool(
            has_temperature
            or general_temperature is not None
            or has_temperature_entries
        )

    @classmethod
    def _merge_legacy_temperature_data(
        cls,
        device_dict: dict[str, Any],
        legacy_devices_by_mac: dict[str, dict[str, Any]],
    ) -> None:
        """Merge temperature-related legacy fields into device data."""
        mac_address = cls._normalize_mac(
            device_dict.get("macAddress") or device_dict.get("mac")
        )
        if mac_address is None:
            return

        legacy_device = legacy_devices_by_mac.get(mac_address)
        if legacy_device is None or not cls._has_legacy_temperature_data(legacy_device):
            return

        general_temperature = legacy_device.get("general_temperature")
        if general_temperature is None:
            general_temperature = legacy_device.get("generalTemperature")

        temperatures = legacy_device.get("temperatures")

        if general_temperature is not None:
            device_dict["generalTemperature"] = general_temperature

        if isinstance(temperatures, list):
            device_dict["temperatures"] = temperatures

        device_dict["hasTemperature"] = True

    @classmethod
    def _merge_legacy_port_data(
        cls,
        device_dict: dict[str, Any],
        legacy_devices_by_mac: dict[str, dict[str, Any]],
    ) -> None:
        """Merge port_table from legacy device data into device dict."""
        mac_address = cls._normalize_mac(
            device_dict.get("macAddress") or device_dict.get("mac")
        )
        if mac_address is None:
            return

        legacy_device = legacy_devices_by_mac.get(mac_address)
        if legacy_device is None:
            return

        port_table = legacy_device.get("port_table")
        if not isinstance(port_table, list) or not port_table:
            return

        # Normalize port_table entries into the format expected by sensor.py
        ports: list[dict[str, Any]] = []
        for port in port_table:
            if not isinstance(port, dict):
                continue
            # Reuse the shared normalizer so the merge path produces
            # identical port data to the legacy-primary fallback path.
            normalized = cls._normalize_legacy_port(port)
            if normalized is not None:
                ports.append(normalized)

        if ports:
            device_dict["ports"] = ports

    @classmethod
    def _merge_legacy_outlet_data(
        cls,
        device_dict: dict[str, Any],
        legacy_devices_by_mac: dict[str, dict[str, Any]],
    ) -> None:
        """Merge outlet_table and power totals from legacy data into the device."""
        mac_address = cls._normalize_mac(
            device_dict.get("macAddress") or device_dict.get("mac")
        )
        if mac_address is None:
            return

        legacy_device = legacy_devices_by_mac.get(mac_address)
        if legacy_device is None:
            return

        outlet_metrics = parse_outlet_metrics(legacy_device)
        if (
            not outlet_metrics.outlets
            and outlet_metrics.ac_power_consumption is None
            and outlet_metrics.ac_power_budget is None
        ):
            return

        if "_id" in legacy_device:
            device_dict["_id"] = legacy_device["_id"]

        if outlet_metrics.outlets:
            device_dict["outlet_table"] = [
                outlet.model_dump() for outlet in outlet_metrics.outlets
            ]
            if "outlet_overrides" in legacy_device:
                device_dict["outlet_overrides"] = legacy_device["outlet_overrides"]

        if outlet_metrics.ac_power_consumption is not None:
            device_dict["outlet_ac_power_consumption"] = (
                outlet_metrics.ac_power_consumption
            )
            device_dict["ac_power_consumption"] = outlet_metrics.ac_power_consumption

        if outlet_metrics.ac_power_budget is not None:
            device_dict["outlet_ac_power_budget"] = outlet_metrics.ac_power_budget
            device_dict["ac_power_budget"] = outlet_metrics.ac_power_budget

    @staticmethod
    def _strict_int(value: Any) -> int | None:
        """Return value when it is a real int (not a bool), else None."""
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    @classmethod
    def _legacy_topology_block(cls, legacy: dict[str, Any]) -> dict[str, Any]:
        """
        Extract the parent-link fields topology needs from a legacy device.

        The v1 API leaves ``uplink`` and ``type`` null on current firmware, so
        the classic ``/stat/device`` record is the only source of which device
        (and port) this one hangs off. ``last_uplink`` keeps the same keys when
        the device is offline and ``uplink`` has been emptied.
        """
        block: dict[str, Any] = {}

        legacy_type = legacy.get("type")
        if isinstance(legacy_type, str) and legacy_type:
            block["legacy_type"] = legacy_type.lower()

        uplink = legacy.get("uplink")
        link = uplink if isinstance(uplink, dict) else {}
        if cls._normalize_mac(link.get("uplink_mac")) is None:
            last_uplink = legacy.get("last_uplink")
            if isinstance(last_uplink, dict) and cls._normalize_mac(
                last_uplink.get("uplink_mac")
            ):
                link = {**link, **last_uplink}

        uplink_mac = cls._normalize_mac(link.get("uplink_mac"))
        if uplink_mac is not None:
            block["uplink_mac"] = uplink_mac
        for source_key, block_key in (
            ("uplink_remote_port", "uplink_remote_port"),
            ("port_idx", "uplink_port_idx"),
            ("speed", "uplink_speed"),
        ):
            value = cls._strict_int(link.get(source_key))
            if value is not None:
                block[block_key] = value
        uplink_type = link.get("type")
        if isinstance(uplink_type, str) and uplink_type:
            block["uplink_type"] = uplink_type.lower()

        return block

    @classmethod
    def _merge_legacy_uplink_data(
        cls,
        device_dict: dict[str, Any],
        legacy_devices_by_mac: dict[str, dict[str, Any]],
    ) -> None:
        """Attach the legacy parent link to a v1 device as ``topology``."""
        legacy_device = cls._legacy_device_for(device_dict, legacy_devices_by_mac)
        if legacy_device is None:
            return
        block = cls._legacy_topology_block(legacy_device)
        if block:
            device_dict["topology"] = block

    @classmethod
    def _merge_legacy_wan_data(
        cls,
        device_dict: dict[str, Any],
        legacy_devices_by_mac: dict[str, dict[str, Any]],
    ) -> None:
        """Merge per-WAN connection state from legacy gateway data."""
        mac_address = cls._normalize_mac(
            device_dict.get("macAddress") or device_dict.get("mac")
        )
        if mac_address is None:
            return
        legacy_device = legacy_devices_by_mac.get(mac_address)
        if legacy_device is None:
            return
        wans = normalize_legacy_wans(legacy_device)
        if wans:
            device_dict["wans"] = wans

    @classmethod
    def _merge_legacy_storage_data(
        cls,
        device_dict: dict[str, Any],
        legacy_devices_by_mac: dict[str, dict[str, Any]],
    ) -> None:
        """Merge the console's per-mount storage from legacy data (no extra request)."""
        mac_address = cls._normalize_mac(
            device_dict.get("macAddress") or device_dict.get("mac")
        )
        if mac_address is None:
            return
        legacy_device = legacy_devices_by_mac.get(mac_address)
        if legacy_device is None:
            return
        mounts = normalize_legacy_storage(legacy_device)
        if mounts:
            device_dict["storage_mounts"] = mounts

    def _vpn_connections_or_previous(
        self, site_id: str, connections: dict[str, dict[str, Any]] | None
    ) -> dict[str, dict[str, Any]] | None:
        """
        Return fresh VPN connections, or reuse the last good set for a few polls.

        A single failed call would otherwise flip every site-to-site VPN sensor
        on -> unknown -> on and fire automations. The reuse is bounded so a call
        that keeps failing cannot hold a stale "connected" forever; after that
        the site's entry is dropped and the sensors read unknown.
        """
        if connections is not None:
            self._vpn_connection_failures.pop(site_id, None)
            return connections
        failures = self._vpn_connection_failures.get(site_id, 0) + 1
        self._vpn_connection_failures[site_id] = failures
        previous = self.data["vpn_connections"].get(site_id)
        if failures <= MAX_STATS_REUSE_POLLS and isinstance(previous, dict):
            return previous
        return None

    def _reuse_previous_wans(self, site_id: str, devices: list[dict[str, Any]]) -> None:
        """
        Carry each device's last WAN and storage data over a failed legacy fetch.

        WAN links and storage mounts both only come from the legacy device call,
        so one failed call would otherwise drop them until the next poll.
        Like VPN connections, the reuse is bounded so a call that keeps
        failing cannot hold a stale state forever.
        """
        failures = self._legacy_wan_failures.get(site_id, 0) + 1
        self._legacy_wan_failures[site_id] = failures
        if failures > MAX_STATS_REUSE_POLLS:
            return
        previous_devices = self.data["devices"].get(site_id, {})
        for device in devices:
            previous = previous_devices.get(device.get("id", ""))
            if not isinstance(previous, dict):
                continue
            for field in ("wans", "storage_mounts"):
                value = previous.get(field)
                if isinstance(value, list):
                    device[field] = value

    async def _fetch_vpn_connections(
        self, site_id: str, legacy_site_name: str | None
    ) -> dict[str, dict[str, Any]] | None:
        """
        Return the site's live VPN connections keyed by network_id.

        Returns None when the state cannot be read (call failed, or no classic
        site name to ask with), so the caller can tell that apart from a site
        with no connected VPNs ({}). This only feeds optional entities, so a
        failure must never fail the device refresh - auth problems surface
        through _process_site.
        """
        if legacy_site_name is None:
            return None
        try:
            connections = await self.network_client.vpn_clients.list_vpn_connections(
                legacy_site_name
            )
        except Exception as err:
            _LOGGER.debug(
                "Device coordinator: VPN connections unavailable for site %s: %s",
                site_id,
                err,
            )
            return None
        return {connection["network_id"]: connection for connection in connections}

    def _map_legacy_site_names(
        self,
        site_ids: list[str],
        legacy_sites: list[dict[str, Any]],
    ) -> dict[str, str]:
        """Map integration site IDs to legacy site names used by `/api/s/{site}`."""
        mappings: dict[str, str] = {}

        def _match_string(value: Any) -> str | None:
            if not isinstance(value, str):
                return None
            stripped = value.strip().lower()
            return stripped or None

        normalized_legacy_sites: list[tuple[str, set[str]]] = []
        for legacy_site in legacy_sites:
            legacy_name = legacy_site.get("name")
            if not isinstance(legacy_name, str) or not legacy_name:
                continue

            candidates = {
                candidate
                for candidate in {
                    _match_string(legacy_name),
                    _match_string(legacy_site.get("desc")),
                    _match_string(legacy_site.get("description")),
                }
                if candidate is not None
            }
            normalized_legacy_sites.append((legacy_name, candidates))

        for site_id in site_ids:
            site_data = self.config_coordinator.get_site(site_id) or {}
            site_candidates = {
                candidate
                for candidate in {
                    _match_string(site_id),
                    _match_string(site_data.get("name")),
                    _match_string(site_data.get("description")),
                    _match_string(site_data.get("desc")),
                }
                if candidate is not None
            }

            for legacy_name, legacy_candidates in normalized_legacy_sites:
                if site_candidates & legacy_candidates:
                    mappings[site_id] = legacy_name
                    break

            if site_id not in mappings and len(normalized_legacy_sites) == 1:
                mappings[site_id] = normalized_legacy_sites[0][0]

        return mappings

    async def _process_device(
        self,
        site_id: str,
        device_dict: dict[str, Any],
        clients: list[dict[str, Any]],
        legacy_site_name: str | None = None,
        legacy_device: dict[str, Any] | None = None,
        *,
        clients_by_uplink: dict[str, list[dict[str, Any]]] | None = None,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """
        Process a single device and its stats.

        If clients_by_uplink is provided, clients for this device are looked up
        directly; otherwise falls back to scanning the clients list.
        """
        device_id: str = device_dict.get("id", "")
        device_name = device_dict.get("name", device_id)

        try:
            # Get device statistics. A device keyed on its MAC (the API sent
            # no id) cannot be looked up by id, so only legacy metrics apply.
            stats: dict[str, Any] = {}
            if not device_id_is_mac(device_dict):
                stats_model = await self.network_client.devices.get_statistics(
                    site_id, device_id=device_id
                )
                stats = self._model_to_dict(stats_model) if stats_model else {}

            # Use vendored API for legacy per-port PoE/byte metrics & system stats
            try:
                device_mac = (
                    device_dict.get("mac")
                    or device_dict.get("macAddress")
                    or device_dict.get("mac_address")
                )

                site_name = legacy_site_name or site_id
                if not legacy_site_name:
                    try:
                        site_obj = self.config_coordinator.get_site(site_id)
                        if isinstance(site_obj, dict):
                            site_name = (
                                site_obj.get("internalReference")
                                or site_obj.get("internal_reference")
                                or site_name
                            )
                    except Exception as err:
                        _LOGGER.debug(
                            "Device coordinator: Unable to resolve internal "
                            "site reference for %s: %s",
                            site_id,
                            err,
                        )

                if device_mac and site_name:
                    metrics = await self.network_client.devices.get_port_metrics(
                        site_name, device_mac
                    )

                    if metrics.port_bytes:
                        stats["port_bytes"] = {
                            idx: {"rx_bytes": pb.rx_bytes, "tx_bytes": pb.tx_bytes}
                            for idx, pb in metrics.port_bytes.items()
                        }

                    if metrics.poe_ports:
                        stats["poe_ports"] = dict(metrics.poe_ports)

                    if metrics.poe_total_w is not None:
                        stats["poe_total_w"] = metrics.poe_total_w

                    _fill_missing_stat(
                        stats,
                        "cpu_utilization_pct",
                        _CPU_STAT_KEYS,
                        metrics.cpu_utilization_pct,
                        float,
                    )
                    _fill_missing_stat(
                        stats,
                        "memory_utilization_pct",
                        _MEMORY_STAT_KEYS,
                        metrics.memory_utilization_pct,
                        float,
                    )
                    _fill_missing_stat(
                        stats,
                        "uptime_sec",
                        _UPTIME_STAT_KEYS,
                        metrics.uptime_sec,
                        int,
                    )
            except Exception as err:
                _LOGGER.debug(
                    "Legacy PoE/metrics fetch failed: %s",
                    err,
                )

            if isinstance(legacy_device, dict):
                _merge_legacy_system_stats(stats, legacy_device)

            # Add client data to stats
            if stats:
                if clients_by_uplink is not None:
                    stats["clients"] = clients_by_uplink.get(device_id, [])
                else:
                    # Use camelCase uplinkDeviceId as returned with aliases
                    stats["clients"] = [
                        c
                        for c in clients
                        if (c.get("uplinkDeviceId") or c.get("uplink_device_id"))
                        == device_id
                    ]
                stats["id"] = device_id

            self._stats_failures.pop(device_id, None)
            return device_id, device_dict, stats

        except Exception as err:
            if self._is_unsupported_response(err):
                self._stats_failures.pop(device_id, None)
                _LOGGER.debug(
                    "Statistics not available for device %s (%s): %s",
                    device_name,
                    device_id,
                    err,
                )
                return device_id, device_dict, {}
            if isinstance(err, UniFiAuthenticationError):
                # Revoked credentials: fail the refresh so reauth starts.
                raise
            # One device's statistics timing out, erroring or being rate
            # limited should not fail every entity on every site, and blanking
            # them would make its sensors drop to unknown. Reuse its last good
            # statistics for a few polls; a persistent failure then drops them
            # so frozen values don't stay "available" indefinitely.
            failures = self._stats_failures.get(device_id, 0) + 1
            self._stats_failures[device_id] = failures
            previous = self.data["stats"].get(site_id, {}).get(device_id)
            if failures <= MAX_STATS_REUSE_POLLS and isinstance(previous, dict):
                _LOGGER.debug(
                    "Error getting stats for device %s (%s), keeping last known "
                    "stats: %s",
                    device_name,
                    device_id,
                    err,
                )
                self._reused_stats.add(device_id)
                return device_id, device_dict, previous
            log = (
                _LOGGER.warning
                if failures == MAX_STATS_REUSE_POLLS + 1
                else _LOGGER.debug
            )
            log(
                "Statistics for device %s (%s) failed %d times in a row: %s",
                device_name,
                device_id,
                failures,
                err,
            )
            return device_id, device_dict, {}

    async def _process_site(
        self, site_id: str, legacy_site_name: str | None = None
    ) -> tuple[
        dict[str, dict[str, Any]],
        dict[str, dict[str, Any]],
        dict[str, dict[str, Any]],
    ]:
        """
        Process a single site's devices and clients.

        Errors propagate: a site whose devices or clients cannot be fetched
        fails the whole refresh (see ``_async_update_data``).

        The v1 devices endpoint can return HTTP 500 on controllers with device
        models the API cannot serialize (e.g. USW Pro XG family). When that
        happens and the site's legacy (classic) name is known, fall back to
        the legacy ``/stat/device`` endpoint, which serves every device, and
        map its payload into v1-shaped dicts so downstream code is unchanged.
        """
        # Get devices and clients in parallel using new API. Results are
        # inspected individually so a clients.get_all() failure cannot trigger
        # the device fallback when devices.get_all() succeeded.
        devices_task = self.network_client.devices.get_all(site_id)
        clients_task = self.network_client.clients.get_all(site_id)

        devices_result, clients_result = await asyncio.gather(
            devices_task, clients_task, return_exceptions=True
        )

        v1_devices_error: UniFiResponseError | None = None
        devices_models: list[Any] = []
        clients_models: list[Any] = []

        if isinstance(devices_result, BaseException):
            if (
                isinstance(devices_result, UniFiResponseError)
                and devices_result.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
                and legacy_site_name is not None
            ):
                _LOGGER.debug(
                    "Device coordinator: v1 devices endpoint returned %s for "
                    "site %s; falling back to legacy /stat/device",
                    devices_result.status_code,
                    site_id,
                )
                v1_devices_error = devices_result
            else:
                raise devices_result
        else:
            devices_models = devices_result

        if isinstance(clients_result, BaseException):
            if v1_devices_error is None:
                raise clients_result
            # The clients endpoint is independent of the devices endpoint, so
            # its 5xx is likely transient: retry once. A failed retry
            # propagates and fails the whole refresh, keeping the previous
            # site data instead of writing empty client data.
            _LOGGER.debug(
                "Device coordinator: Clients fetch also failed for site %s "
                "after v1 devices failure; retrying clients",
                site_id,
            )
            clients_models = await self.network_client.clients.get_all(site_id)
        else:
            clients_models = clients_result

        legacy_devices: list[dict[str, Any]] = []
        legacy_as_primary = False
        legacy_failed = False
        if legacy_site_name is not None:
            try:
                legacy_devices = (
                    await self.network_client.devices.get_legacy_site_devices(
                        legacy_site_name
                    )
                )
            except Exception as err:
                _LOGGER.debug(
                    "Device coordinator: Failed to fetch legacy device data "
                    "for site %s (%s): %s",
                    site_id,
                    legacy_site_name,
                    err,
                )
                if v1_devices_error is not None:
                    # Both v1 and legacy failed. Re-raise the v1 error so the
                    # refresh fails and the previous device state is kept,
                    # rather than wiping the device registry with empty data.
                    raise v1_devices_error from err
                legacy_failed = True

        # When v1 devices failed with 5xx, use legacy devices as the primary
        # device list, mapped to v1-shaped dicts.
        if v1_devices_error is not None:
            if not legacy_devices:
                # The legacy endpoint served nothing useful. It cannot be
                # distinguished from an empty site, so fail the refresh and
                # keep the previous device state rather than wiping it.
                raise v1_devices_error
            devices = [
                self._legacy_device_to_v1_dict(legacy_device)
                for legacy_device in legacy_devices
                if isinstance(legacy_device, dict)
            ]
            legacy_as_primary = True
            log = (
                _LOGGER.debug
                if site_id in self._legacy_fallback_sites
                else _LOGGER.info
            )
            self._legacy_fallback_sites.add(site_id)
            log(
                "Device coordinator: Using %d legacy devices as primary "
                "source for site %s (v1 endpoint unavailable)",
                len(devices),
                site_id,
            )
        else:
            # Convert model objects to dictionaries
            devices = [self._model_to_dict(d) for d in devices_models]
            if site_id in self._legacy_fallback_sites:
                self._legacy_fallback_sites.remove(site_id)
                _LOGGER.info(
                    "Device coordinator: v1 devices endpoint recovered for site %s",
                    site_id,
                )

        clients = [self._model_to_dict(c) for c in clients_models]

        legacy_devices_by_mac = {
            normalized_mac: legacy_device
            for legacy_device in legacy_devices
            if isinstance(legacy_device, dict)
            and (
                normalized_mac := self._normalize_mac(
                    legacy_device.get("mac") or legacy_device.get("macAddress")
                )
            )
            is not None
        }

        if legacy_devices_by_mac:
            for device in devices:
                if not legacy_as_primary:
                    self._merge_legacy_temperature_data(device, legacy_devices_by_mac)
                    self._merge_legacy_port_data(device, legacy_devices_by_mac)
                    self._merge_legacy_outlet_data(device, legacy_devices_by_mac)
                    # When legacy is primary, _legacy_device_to_v1_dict()
                    # already embeds the topology block directly.
                    self._merge_legacy_uplink_data(device, legacy_devices_by_mac)
                # The legacy-to-v1 mapping does not carry WAN links and storage, so they
                # are merged whichever source is primary.
                self._merge_legacy_wan_data(device, legacy_devices_by_mac)
                self._merge_legacy_storage_data(device, legacy_devices_by_mac)

        if legacy_failed:
            self._reuse_previous_wans(site_id, devices)
        else:
            self._legacy_wan_failures.pop(site_id, None)

        _LOGGER.debug(
            "Device coordinator: Site %s - Found %d devices and %d clients",
            site_id,
            len(devices),
            len(clients),
        )

        # Log sample device keys for debugging data format issues
        if devices:
            sample_device = devices[0]
            _LOGGER.debug(
                "Device coordinator: Sample device keys for site %s: %s",
                site_id,
                list(sample_device.keys()),
            )

        # Group clients by uplink device in a single pass
        clients_by_uplink: dict[str, list[dict[str, Any]]] = {}
        for client in clients:
            uplink_id = client.get("uplinkDeviceId") or client.get("uplink_device_id")
            if isinstance(uplink_id, str) and uplink_id:
                clients_by_uplink.setdefault(uplink_id, []).append(client)

        # Process devices in parallel (get stats)
        tasks = [
            self._process_device(
                site_id,
                device,
                clients,
                legacy_site_name=legacy_site_name,
                legacy_device=self._legacy_device_for(device, legacy_devices_by_mac),
                clients_by_uplink=clients_by_uplink,
            )
            for device in devices
        ]
        results = await asyncio.gather(*tasks)

        # Organize results
        devices_dict = {}
        stats_dict = {}
        for device_id, device, stats in results:
            if device_id:
                devices_dict[device_id] = device
                stats_dict[device_id] = stats

        clients_dict: dict[str, dict[str, Any]] = {
            str(client.get("id")): client for client in clients if client.get("id")
        }

        return devices_dict, stats_dict, clients_dict

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch device data from API."""
        try:
            # Get site IDs from config coordinator
            site_ids = self.config_coordinator.get_site_ids()

            # Drop data for sites no longer polled so their entities stop
            # reporting last-known values. Site-level fetch failures below
            # still keep a polled site's previous data.
            for key in ("devices", "stats", "clients", "vpn_connections"):
                self.data[key] = {
                    site_id: value
                    for site_id, value in self.data[key].items()
                    if site_id in site_ids
                }
            self._vpn_connection_failures = {
                site_id: count
                for site_id, count in self._vpn_connection_failures.items()
                if site_id in site_ids
            }
            self._legacy_wan_failures = {
                site_id: count
                for site_id, count in self._legacy_wan_failures.items()
                if site_id in site_ids
            }

            if not site_ids:
                # Deliberately no stale-device cleanup here: an empty site
                # list is also what a transient Network API failure looks
                # like, and purging the registry on it would lose devices.
                _LOGGER.debug(
                    "Device coordinator: No sites available from config coordinator"
                )
                self._available = True
                self.data["last_update"] = datetime.now(tz=UTC)
                return self.data

            _LOGGER.debug(
                "Device coordinator: Processing %d sites",
                len(site_ids),
            )

            self._reused_stats = set()
            legacy_site_names: dict[str, str] = {}
            try:
                legacy_sites = await self.network_client.sites.get_legacy_all()
                legacy_site_names = self._map_legacy_site_names(site_ids, legacy_sites)
                self._legacy_site_names = legacy_site_names
            except Exception as err:
                _LOGGER.debug(
                    "Device coordinator: Unable to fetch legacy site mapping: %s",
                    err,
                )

            # Process all sites in parallel
            tasks = [
                self._process_site(site_id, legacy_site_names.get(site_id))
                for site_id in site_ids
            ]
            vpn_tasks = [
                self._fetch_vpn_connections(site_id, legacy_site_names.get(site_id))
                for site_id in site_ids
            ]
            results, vpn_results = await asyncio.gather(
                asyncio.gather(*tasks, return_exceptions=True),
                asyncio.gather(*vpn_tasks),
            )

            # A site that could not be refreshed fails the whole update, and
            # nothing is written until every site has succeeded. Reporting
            # success here used to leave entities "available" on data that
            # was silently going stale, and swallowed a revoked API key so
            # reauth never started. DataUpdateCoordinator keeps the previous
            # self.data when the update raises.
            failures = [
                (site_id, result)
                for site_id, result in zip(site_ids, results, strict=True)
                if isinstance(result, BaseException)
            ]
            if failures:
                for failed_site_id, site_error in failures:
                    _LOGGER.debug(
                        "Device coordinator: Refresh failed for site %s: %r",
                        failed_site_id,
                        site_error,
                    )
                # Revoked credentials (401) on any site start reauth. A 403
                # is not sent to reauth: the flow re-validates against the
                # sites endpoint, which the same key passes, so it would loop.
                auth_failure = next(
                    (
                        site_error
                        for _, site_error in failures
                        if isinstance(site_error, UniFiAuthenticationError)
                        and site_error.status_code != HTTPStatus.FORBIDDEN
                    ),
                    None,
                )
                if auth_failure is not None:
                    raise auth_failure
                failed_site_id, first_error = failures[0]
                if isinstance(first_error, UniFiAuthenticationError):
                    self._available = False
                    msg = f"Access forbidden for site {failed_site_id}: {first_error}"
                    raise UpdateFailed(msg) from first_error
                raise first_error

            # Every site succeeded; the filter only narrows the type.
            site_results = [
                result for result in results if not isinstance(result, BaseException)
            ]

            # Update data structure with results
            for site_id, (devices_dict, stats_dict, clients_dict), vpn in zip(
                site_ids, site_results, vpn_results, strict=True
            ):
                self.data["devices"][site_id] = devices_dict
                self.data["stats"][site_id] = stats_dict
                self.data["clients"][site_id] = clients_dict
                connections = self._vpn_connections_or_previous(site_id, vpn)
                if connections is None:
                    self.data["vpn_connections"].pop(site_id, None)
                else:
                    self.data["vpn_connections"][site_id] = connections

                _LOGGER.debug(
                    "Device coordinator: Processed site %s - %d devices, %d clients",
                    site_id,
                    len(devices_dict),
                    len(clients_dict),
                )

            # Compute per-port byte rates from deltas
            self._compute_port_rates()

            # Forget failure counts for devices that are no longer reported.
            current_device_ids = {
                device_id
                for site_stats in self.data["stats"].values()
                for device_id in site_stats
            }
            self._stats_failures = {
                device_id: count
                for device_id, count in self._stats_failures.items()
                if device_id in current_device_ids
            }

            self._available = True
            self.data["last_update"] = datetime.now(tz=UTC)

            # Clean up stale devices (Gold requirement)
            self._cleanup_stale_devices()

            _LOGGER.debug(
                "Device coordinator: Update complete - %d sites processed",
                len(site_ids),
            )

            return self.data

        except UniFiAuthenticationError as err:
            self._handle_auth_error(err)
        except UniFiConnectionError as err:
            self._handle_connection_error(err)
        except UniFiTimeoutError as err:
            self._handle_timeout_error(err)
        except UniFiResponseError as err:
            self._handle_response_error(err)
        except UpdateFailed:
            # Already classified above (a site 403); the generic handler would
            # log it as an unexpected error with a traceback on every poll.
            raise
        except Exception as err:
            self._handle_generic_error(err)

        # Should never reach here due to raises above
        return self.data  # pragma: no cover

    def _compute_port_rates(self) -> None:
        """
        Compute per-port byte rates from consecutive poll deltas.

        Each device keeps its own last sample time. A device whose statistics
        were reused this poll (see ``_process_device``) is skipped and its
        previous sample carried forward: recomputing against the same
        counters would report a rate of 0, and the next fresh poll would then
        divide two polls' worth of bytes by one poll's elapsed time. Its
        reused stats keep the port_rates from the last real sample.
        """
        now = time.monotonic()
        previous = self._prev_port_bytes
        current: dict[str, tuple[float, dict[int, dict[str, int]]]] = {}

        for stats_dict in self.data.get("stats", {}).values():
            for device_id, stats in stats_dict.items():
                if not isinstance(stats, dict):
                    continue

                if device_id in self._reused_stats:
                    if device_id in previous:
                        current[device_id] = previous[device_id]
                    continue

                curr_dev = stats.get("port_bytes")
                if not isinstance(curr_dev, dict):
                    continue
                current[device_id] = (now, curr_dev)

                if device_id not in previous:
                    continue
                prev_time, prev_dev = previous[device_id]
                elapsed = now - prev_time
                if elapsed <= 0:
                    continue

                port_rates: dict[int, dict[str, float]] = {}
                for port_idx, curr in curr_dev.items():
                    prev = prev_dev.get(port_idx)
                    if prev is None:
                        continue

                    tx_delta = curr.get("tx_bytes", 0) - prev.get("tx_bytes", 0)
                    rx_delta = curr.get("rx_bytes", 0) - prev.get("rx_bytes", 0)

                    # Skip negative deltas (counter reset)
                    if tx_delta < 0 or rx_delta < 0:
                        continue

                    port_rates[port_idx] = {
                        "tx_bytes_rate": round(tx_delta / elapsed, 1),
                        "rx_bytes_rate": round(rx_delta / elapsed, 1),
                    }

                if port_rates:
                    stats["port_rates"] = port_rates

        # Update stored state for next poll
        self._prev_port_bytes = current

    def _cleanup_stale_devices(self) -> None:
        """Remove stale network devices from the device registry (Gold requirement)."""
        device_registry = dr.async_get(self.hass)

        # Collect current network device IDs
        current_network_device_ids: set[str] = set()
        for site_id, devices in self.data.get("devices", {}).items():
            for device_id in devices:
                current_network_device_ids.add(f"{site_id}_{device_id}")

        # Find and remove stale network devices
        stale_network_ids = (
            self._previous_network_device_ids - current_network_device_ids
        )
        for device_identifier in stale_network_ids:
            device = async_get_device_entry(
                device_registry,
                (DOMAIN, device_identifier),
                self.config_entry.entry_id,
            )
            if device:
                _LOGGER.info(
                    "Device coordinator: Removing stale network device: %s",
                    device_identifier,
                )
                device_registry.async_update_device(
                    device_id=device.id,
                    remove_config_entry_id=self.config_entry.entry_id,
                )

        self._previous_network_device_ids = current_network_device_ids

    def get_device(self, site_id: str, device_id: str) -> dict[str, Any] | None:
        """Get device data by site ID and device ID."""
        devices = self.data.get("devices", {}).get(site_id, {})
        result = devices.get(device_id)
        return result if isinstance(result, dict) else None

    def get_device_stats(self, site_id: str, device_id: str) -> dict[str, Any] | None:
        """Get device statistics by site ID and device ID."""
        stats = self.data.get("stats", {}).get(site_id, {})
        result = stats.get(device_id)
        return result if isinstance(result, dict) else None

    def get_clients(self, site_id: str) -> dict[str, Any]:
        """Get all clients for a site."""
        result: dict[str, Any] = self.data.get("clients", {}).get(site_id, {})
        return result

    def get_legacy_site_name(self, site_id: str) -> str | None:
        """
        Return the classic ("legacy") site name for an integration site ID.

        Used for classic-API client actions, which are scoped by the site name
        (for example ``default``) rather than the integration site UUID.
        """
        return self._legacy_site_names.get(site_id)
