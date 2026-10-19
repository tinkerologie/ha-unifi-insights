# Copyright 2026 UniFi Insights contributors
"""UniFi Insights entity base class."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEVICE_TYPE_CAMERA,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_NVR,
    DEVICE_TYPE_SENSOR,
    DOMAIN,
    MANUFACTURER,
)
from .coordinators import UnifiInsightsFacadeCoordinator

# Re-exported (see __all__): the platforms import the field helpers from here.
from .data_transforms import (
    NetworkDeviceFacts,
    client_connection_type,
    device_facts,
    device_kind,
    device_online_state,
    first_not_none,
    get_field,
    get_site_gateway_id,
)
from .innerspace_entity import UnifiInnerSpaceEntity

_LOGGER = logging.getLogger(__name__)


def device_has_feature(device_data: dict[str, Any], *features_to_match: str) -> bool:
    """
    Return True when a device advertises any of the requested features.

    Controllers report `features` either as a list of names or as a mapping of
    name to a truthy value (sometimes a nested detail object), depending on
    firmware and endpoint, so both shapes are accepted.
    """
    features = device_data.get("features", [])
    if isinstance(features, dict):
        return any(
            bool(features.get(feature_name)) for feature_name in features_to_match
        )
    if isinstance(features, list):
        return any(feature_name in features for feature_name in features_to_match)
    return False


def is_gateway_device(device_data: dict[str, Any]) -> bool:
    """
    Return True when a network device is a gateway/router.

    Delegates to the read model's kind rule (``data_transforms.device_kind``),
    so entities and dashboards agree; a null-``type`` UDM Pro that advertises
    only ``switching`` is a gateway by its model.

    Merged legacy WAN data (``wans``) is deliberately not a signal: it is only
    present when the legacy fetch succeeded, and this check also decides which
    device site-level entities are grouped under, which must not depend on
    whether one poll's legacy call happened to work.
    """
    return device_kind(device_data) == "gateway"


def build_site_device_info(
    coordinator_data: dict[str, Any], site_id: str
) -> dict[str, Any]:
    """Build DeviceInfo dictionary for site-level entity grouping."""
    gateway_id = get_site_gateway_id(coordinator_data, site_id)
    if gateway_id is not None:
        return {"identifiers": {(DOMAIN, f"{site_id}_{gateway_id}")}}

    site_data = coordinator_data.get("sites", {}).get(site_id, {})
    meta = site_data.get("meta", {}) if isinstance(site_data, dict) else {}
    site_name = (meta.get("name") if isinstance(meta, dict) else None) or (
        site_data.get("name", site_id) if isinstance(site_data, dict) else site_id
    )

    return {
        "identifiers": {(DOMAIN, f"site_{site_id}")},
        "name": f"UniFi Site ({site_name})",
        "manufacturer": MANUFACTURER,
        "model": "UniFi Site",
    }


def is_device_online(data: dict[str, Any]) -> bool:
    """
    Return True when a network device is online.

    Delegates to the read model's spec state rule
    (``data_transforms.device_online_state``): only ``ONLINE`` (any case) is
    online. ``CONNECTED`` and ``UP`` are not network device states.
    """
    return device_online_state(data) == "online"


def get_client_type(client: dict[str, Any]) -> str:
    """
    Return a client's type as "WIRED", "WIRELESS" or its raw value.

    Recognition is the read model's exact rule
    (``data_transforms.client_connection_type``). Anything it does not
    recognise comes back upper-cased as is, so displayed attributes keep it.
    """
    connection = client_connection_type(client)
    if connection is not None:
        return connection.upper()
    return str(client.get("type") or client.get("connection_type", "")).upper()


def camera_supports_ptz(camera_data: dict[str, Any]) -> bool:
    """Return True if a Protect camera advertises PTZ support."""
    is_ptz = get_field(camera_data, "isPtz", "is_ptz", "hasPtz", default=False)
    if isinstance(is_ptz, bool):
        return is_ptz

    feature_flags = get_field(camera_data, "featureFlags", "feature_flags", default={})
    if isinstance(feature_flags, dict):
        return bool(feature_flags.get("hasPtz") or feature_flags.get("has_ptz"))

    return False


def camera_supports_speaker(camera_data: Any) -> bool:
    """Return True if a Protect camera advertises speaker support."""
    if not isinstance(camera_data, dict):
        return False

    has_speaker = get_field(camera_data, "hasSpeaker", "has_speaker", default=False)
    if has_speaker is True:
        return True

    feature_flags = get_field(camera_data, "featureFlags", "feature_flags", default={})
    if isinstance(feature_flags, dict):
        nested_speaker = get_field(
            feature_flags, "hasSpeaker", "has_speaker", default=False
        )
        if nested_speaker is True:
            return True
        removable_speaker = get_field(
            feature_flags,
            "hasRemovableSpeaker",
            "has_removable_speaker",
            default=False,
        )
        if removable_speaker is True:
            return True

    return False


class UnifiInsightsEntity(CoordinatorEntity[UnifiInsightsFacadeCoordinator]):
    """Base class for UniFi Insights entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        description: EntityDescription,
        site_id: str,
        device_id: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._site_id = site_id
        self._device_id = device_id

        # Get device data
        device_data = coordinator.data["devices"][site_id][device_id]
        device_name = get_field(
            device_data, "name", default=f"UniFi Device {device_id}"
        )
        ip_address = get_field(device_data, "ipAddress", "ip_address", "ip", default="")

        # Set unique ID
        self._attr_unique_id = f"{site_id}_{device_id}_{description.key}"

        # Create device info for individual device
        device_info: dict[str, Any] = {
            "identifiers": {(DOMAIN, f"{site_id}_{device_id}")},
            "name": f"{device_name} ({ip_address})" if ip_address else device_name,
            "manufacturer": MANUFACTURER,
            "model": get_field(device_data, "model", default="Unknown Model"),
            "sw_version": get_field(
                device_data, "firmwareVersion", "firmware_version", "version"
            ),
            "configuration_url": (
                f"{coordinator.network_client.base_url}/network/devices/{device_id}"
            ),
        }

        # Add network connections
        if mac := get_field(device_data, "macAddress", "mac_address", "mac"):
            device_info["connections"] = {(CONNECTION_NETWORK_MAC, mac)}

        # Add hardware version based on device features
        hw_info = []

        # Get port count
        if (ports := device_data.get("port_table", [])) and isinstance(ports, list):
            hw_info.append(f"{len(ports)} Ports")

        # Get radio info
        if (radio_table := device_data.get("radio_table", [])) and isinstance(
            radio_table, list
        ):
            for radio in radio_table:
                if not isinstance(radio, dict):
                    continue
                radio_name = radio.get("name", "")
                radio_type = radio.get("radio", "")
                if radio_name and radio_type:
                    hw_info.append(f"{radio_name} ({radio_type})")

        if hw_info:
            device_info["hw_version"] = " | ".join(hw_info)

        # Set suggested area based on device type
        model = get_field(device_data, "model", default="").lower()
        if any(
            model.startswith(prefix)
            for prefix in ("usw", "switch", "uap", "ap", "udm", "usg")
        ):
            device_info["suggested_area"] = "Network"

        self._attr_device_info = DeviceInfo(**device_info)  # type: ignore[typeddict-item]

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        # Cached device data can still read "online" long after the
        # coordinator has stopped getting fresh data from the API (see
        # UnifiProtectEntity.available for the incident this mirrors) - gate
        # on the device coordinator's last_update_success.
        if not self.coordinator.device_available:
            return False
        facts = self.device_facts
        return facts is not None and facts.online_state == "online"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

    @property
    def device_data(self) -> dict[str, Any] | None:
        """Return device data."""
        devices = self.coordinator.data["devices"].get(self._site_id, {})
        result = devices.get(self._device_id)
        return result if isinstance(result, dict) else None

    @property
    def device_facts(self) -> NetworkDeviceFacts | None:
        """Return the read-model facts the device coordinator published."""
        return device_facts(self.coordinator.data, self._site_id, self._device_id)

    @property
    def device_stats(self) -> dict[str, Any] | None:
        """Return device statistics."""
        stats = self.coordinator.data["stats"].get(self._site_id, {})
        result = stats.get(self._device_id)
        return result if isinstance(result, dict) else None


class UnifiProtectEntity(CoordinatorEntity[UnifiInsightsFacadeCoordinator]):
    """Base class for UniFi Protect entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        device_type: str,
        device_id: str,
        entity_type: str | None = None,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._device_type = device_type
        self._device_id = device_id
        self._entity_type = entity_type

        # Get device data
        device_data = coordinator.data["protect"][f"{device_type}s"].get(device_id, {})
        device_name = device_data.get(
            "name", f"UniFi {device_type.capitalize()} {device_id}"
        )

        # For dual-camera entities, extract the original device ID for device grouping
        original_device_id = device_id
        parent_camera_id = device_data.get("_parent_camera_id")
        if parent_camera_id:
            original_device_id = parent_camera_id
            # Use original device name without camera type suffix for device grouping
            original_device_name = device_name
            if " Main Camera" in device_name:
                original_device_name = device_name.replace(" Main Camera", "")
            elif " Package Camera" in device_name:
                original_device_name = device_name.replace(" Package Camera", "")
            device_name = original_device_name

        # Set unique ID - include entity type if provided
        if entity_type:
            self._attr_unique_id = f"{DOMAIN}_{device_type}_{device_id}_{entity_type}"
        else:
            self._attr_unique_id = f"{DOMAIN}_{device_type}_{device_id}"

        # Check if this device has a corresponding network device
        network_device_id = None
        network_site_id = None

        # For cameras and NVRs, try to find a matching network device by MAC address
        # This ensures devices like UDM-Pro show as a single device in HA
        # Use the original device ID for MAC lookup in case of dual-camera
        lookup_device_id = original_device_id if parent_camera_id else device_id
        lookup_device_data = coordinator.data["protect"][f"{device_type}s"].get(
            lookup_device_id, device_data
        )

        # Match cameras and NVRs to network devices by MAC
        should_match_mac = (
            device_type in (DEVICE_TYPE_CAMERA, DEVICE_TYPE_NVR)
            and "mac" in lookup_device_data
        )
        if should_match_mac:
            device_mac = lookup_device_data.get("mac")
            if device_mac:
                # Search for a network device with the same MAC
                for site_id, devices in coordinator.data["devices"].items():
                    for net_device_id, net_device in devices.items():
                        if net_device.get("macAddress") == device_mac:
                            network_device_id = net_device_id
                            network_site_id = site_id
                            _LOGGER.debug(
                                "Matched network device %s at site %s for %s %s",
                                net_device_id,
                                site_id,
                                device_type,
                                lookup_device_id,
                            )
                            break
                    if network_device_id:
                        break

        # Create device info based on whether we found a matching network device
        device_info: dict[str, Any]
        if network_device_id and network_site_id:
            # Use the network device's identifiers to ensure all entities
            # appear under the same device
            network_device = coordinator.data["devices"][network_site_id][
                network_device_id
            ]
            network_device_name = network_device.get(
                "name", f"UniFi Device {network_device_id}"
            )
            ip_address = network_device.get("ipAddress", "")

            device_info = {
                "identifiers": {(DOMAIN, f"{network_site_id}_{network_device_id}")},
                "name": f"{network_device_name} ({ip_address})"
                if ip_address
                else network_device_name,
                "manufacturer": MANUFACTURER,
                "model": network_device.get("model", "Unknown Model"),
                "sw_version": (
                    network_device.get("firmwareVersion")
                    or network_device.get("firmware_version")
                    or network_device.get("version")
                ),
                "configuration_url": (
                    f"{coordinator.network_client.base_url}/network/devices/{network_device_id}"
                ),
            }

            # Add network connections
            device_info["connections"] = {
                (CONNECTION_NETWORK_MAC, network_device.get("macAddress"))
            }

            _LOGGER.debug(
                "Using network info for %s %s (net %s site %s)",
                device_type,
                device_id,
                network_device_id,
                network_site_id,
            )
        else:
            # Create a new device entry for this Protect device
            # Use original device ID for dual-camera grouping
            device_id_for_identifier = (
                original_device_id if parent_camera_id else device_id
            )
            protect_base_url = (
                coordinator.protect_client.base_url
                if coordinator.protect_client is not None
                else None
            )
            device_info = {
                "identifiers": {
                    (DOMAIN, f"protect_{device_type}_{device_id_for_identifier}")
                },
                "name": device_name,
                "manufacturer": MANUFACTURER,
                "model": lookup_device_data.get(
                    "type", f"UniFi {device_type.capitalize()}"
                ),
            }
            if protect_base_url:
                device_info["configuration_url"] = (
                    f"{protect_base_url}/protect/devices/{device_id_for_identifier}"
                )

            # Set suggested area
            if device_type == DEVICE_TYPE_CAMERA:
                device_info["suggested_area"] = "Security"
            elif device_type == DEVICE_TYPE_LIGHT:
                device_info["suggested_area"] = "Exterior"
            elif device_type == DEVICE_TYPE_SENSOR:
                device_info["suggested_area"] = "Security"

            # Add MAC connection if available and not None
            if mac := lookup_device_data.get("mac"):
                device_info["connections"] = {(CONNECTION_NETWORK_MAC, mac)}

            _LOGGER.debug(
                "Created new device info for %s device %s", device_type, device_id
            )

        self._attr_device_info = DeviceInfo(**device_info)  # type: ignore[typeddict-item]

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        # Cached protect device data keeps reporting the last-known
        # "CONNECTED" state indefinitely once the protect coordinator stops
        # getting fresh data (e.g. the NVR's local session periodically
        # expiring) - gate on the protect coordinator's last_update_success.
        if not self.coordinator.protect_available:
            return False
        device_data = self.coordinator.data["protect"][f"{self._device_type}s"].get(
            self._device_id
        )
        if not device_data or not isinstance(device_data, dict):
            return False
        state = device_data.get("state")
        return isinstance(state, str) and state == "CONNECTED"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator.protect_available:
            self.async_write_ha_state()
            return

        device_data = self.device_data
        if not device_data:
            self.async_write_ha_state()
            return

        self._update_from_data()
        self.async_write_ha_state()

    def _update_from_data(self) -> None:
        """Update entity from data."""
        # To be implemented by subclasses

    @property
    def device_data(self) -> dict[str, Any] | None:
        """Return device data."""
        data = self.coordinator.data["protect"][f"{self._device_type}s"].get(
            self._device_id
        )
        return data if isinstance(data, dict) else None


__all__ = [
    "UnifiInnerSpaceEntity",
    "UnifiInsightsEntity",
    "UnifiProtectEntity",
    "camera_supports_speaker",
    "first_not_none",
    "get_field",
    "is_device_online",
]
