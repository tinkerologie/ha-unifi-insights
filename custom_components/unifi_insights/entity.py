# Copyright 2026 UniFi Insights contributors
"""UniFi Insights entity base class."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceEntryType,
    DeviceInfo,
)
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEVICE_TYPE_ALARM_HUB,
    DEVICE_TYPE_CAMERA,
    DEVICE_TYPE_FOB,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_LINK_STATION,
    DEVICE_TYPE_NVR,
    DEVICE_TYPE_SENSOR,
    DOMAIN,
    MANUFACTURER,
    MOBILITY_DEVICE_PREFIX,
    MOBILITY_WORKSPACE_PREFIX,
)
from .coordinators import UnifiInsightsFacadeCoordinator
from .coordinators.access import UnifiInsightsAccessCoordinator
from .coordinators.base import CommandFailure, async_execute_command
from .coordinators.mobility import UnifiInsightsMobilityCoordinator

# Re-exported (see __all__): the platforms import the field helpers from here.
from .data_transforms import (
    NetworkDeviceFacts,
    client_connection_type,
    device_facts,
    device_kind,
    device_online_state,
    device_serviceable,
    first_not_none,
    get_field,
    get_site_gateway_id,
    network_device,
    network_device_stats,
    owner_available,
    protect_device,
    site_record,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Mapping

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity, EntityDescription
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import CarrierFabricConfigEntry
    from .coordinators.access import Feature
    from .coordinators.carrier_fabric import UnifiInsightsCarrierFabricCoordinator

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
    coordinator_data: Mapping[str, Any], site_id: str
) -> dict[str, Any]:
    """Build DeviceInfo dictionary for site-level entity grouping."""
    gateway_id = get_site_gateway_id(coordinator_data, site_id)
    if gateway_id is not None:
        return {"identifiers": {(DOMAIN, f"{site_id}_{gateway_id}")}}

    site_data = site_record(coordinator_data, site_id) or {}
    meta = site_data.get("meta")
    site_name = (meta.get("name") if isinstance(meta, dict) else None) or (
        site_data.get("name", site_id)
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


def is_device_serviceable(data: dict[str, Any]) -> bool:
    """Return service eligibility; connectivity remains is_device_online's rule."""
    return device_serviceable(data)


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


@callback
def async_delegate_entity_id_changed(parent: Any, old_entity_id: str) -> None:
    """
    Run the parent's ``async_entity_id_changed``, when Home Assistant has one.

    Home Assistant 2026.11 changes an entity's ID in place, but re-adds an
    entity of a custom integration whose class overrides an add or remove hook
    unless that class also defines ``async_entity_id_changed``; Home Assistant
    2027.11 drops the re-add. Each such class defines the hook and passes
    ``super()`` from it, so the MRO reaches Home Assistant's own bookkeeping,
    such as restore state. Before 2026.11 the parent has no hook to call.
    """
    if (hook := getattr(parent, "async_entity_id_changed", None)) is not None:
        hook(old_entity_id)


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

        device_data = network_device(coordinator.data, site_id, device_id)
        if device_data is None:
            msg = f"Network device {device_id} is not in site {site_id}"
            raise KeyError(msg)
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

        self._attr_device_info = DeviceInfo(**device_info)  # type: ignore[typeddict-item, unused-ignore]

    @property
    def available(self) -> bool:
        """Return availability from Device health and service eligibility."""
        # Cached device data can still read "online" long after the
        # coordinator has stopped getting fresh data from the API (see
        # UnifiProtectEntity.available for the incident this mirrors) - gate
        # on the device coordinator's last_update_success.
        if not owner_available(self.coordinator.data, "device"):
            return False
        facts = self.device_facts
        return facts is not None and facts.serviceable

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

    @property
    def device_data(self) -> dict[str, Any] | None:
        """Return device data."""
        record = network_device(self.coordinator.data, self._site_id, self._device_id)
        return record if isinstance(record, dict) else None

    @property
    def device_facts(self) -> NetworkDeviceFacts | None:
        """Return the read-model facts the device coordinator published."""
        return device_facts(self.coordinator.data, self._site_id, self._device_id)

    @property
    def device_stats(self) -> dict[str, Any] | None:
        """Return device statistics."""
        record = network_device_stats(
            self.coordinator.data, self._site_id, self._device_id
        )
        return record if isinstance(record, dict) else None


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

        collection = f"{device_type}s"
        device_data = protect_device(coordinator.data, collection, device_id) or {}
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
        matched_network_device: Mapping[str, Any] = {}

        # For cameras and NVRs, try to find a matching network device by MAC address
        # This ensures devices like UDM-Pro show as a single device in HA
        # Use the original device ID for MAC lookup in case of dual-camera
        lookup_device_id = original_device_id if parent_camera_id else device_id
        lookup_record = protect_device(coordinator.data, collection, lookup_device_id)
        lookup_device_data = device_data if lookup_record is None else lookup_record

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
                            matched_network_device = net_device
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
            network_device_name = matched_network_device.get(
                "name", f"UniFi Device {network_device_id}"
            )
            ip_address = matched_network_device.get("ipAddress", "")

            device_info = {
                "identifiers": {(DOMAIN, f"{network_site_id}_{network_device_id}")},
                "name": f"{network_device_name} ({ip_address})"
                if ip_address
                else network_device_name,
                "manufacturer": MANUFACTURER,
                "model": matched_network_device.get("model", "Unknown Model"),
                "sw_version": (
                    matched_network_device.get("firmwareVersion")
                    or matched_network_device.get("firmware_version")
                    or matched_network_device.get("version")
                ),
                "configuration_url": (
                    f"{coordinator.network_client.base_url}/network/devices/{network_device_id}"
                ),
            }

            # Add network connections
            device_info["connections"] = {
                (CONNECTION_NETWORK_MAC, matched_network_device.get("macAddress"))
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

        self._attr_device_info = DeviceInfo(**device_info)  # type: ignore[typeddict-item, unused-ignore]

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        # Cached protect device data keeps reporting the last-known
        # "CONNECTED" state indefinitely once the protect coordinator stops
        # getting fresh data (e.g. the NVR's local session periodically
        # expiring) - gate on the protect coordinator's last_update_success.
        if not owner_available(self.coordinator.data, "protect"):
            return False
        device_data = protect_device(
            self.coordinator.data, f"{self._device_type}s", self._device_id
        )
        return device_data is not None and device_data.get("state") == "CONNECTED"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not owner_available(self.coordinator.data, "protect"):
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
        record = protect_device(
            self.coordinator.data, f"{self._device_type}s", self._device_id
        )
        return record if isinstance(record, dict) else None


class UnifiSiteEntity(CoordinatorEntity[UnifiInsightsFacadeCoordinator]):
    """
    Base entity for one site, grouped under the site's gateway or site device.

    Subclasses decide availability: each site entity depends on a different
    coordinator section.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        site_id: str,
        *,
        unique_id: str,
        description: EntityDescription | None = None,
    ) -> None:
        """Initialize the site entity."""
        super().__init__(coordinator)
        if description is not None:
            self.entity_description = description
        self._site_id = site_id
        self._attr_unique_id = unique_id
        self._attr_device_info = DeviceInfo(
            **build_site_device_info(coordinator.data, site_id)  # type: ignore[typeddict-item, unused-ignore]
        )


__all__ = [
    "UnifiInnerSpaceEntity",
    "UnifiInsightsEntity",
    "UnifiProtectEntity",
    "camera_supports_speaker",
    "first_not_none",
    "get_field",
    "is_device_online",
]


# --- Access ------------------------------------------------------------------

# Shared base entity, identity and discovery for UniFi Access entities.


def access_unique_id(namespace: str, *parts: str) -> str:
    """Return an entity unique id scoped to one console."""
    return "_".join(("unifi_insights_access", namespace, *parts))


def door_device_info(namespace: str, door_id: str, door_name: str | None) -> DeviceInfo:
    """Return the device a door's entities belong to."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"access_door_{namespace}_{door_id}")},
        name=door_name or f"Access door {door_id}",
        manufacturer=MANUFACTURER,
        model="UniFi Access door",
    )


def console_device_info(namespace: str) -> DeviceInfo:
    """Return the console-level Access device."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"access_console_{namespace}")},
        name="UniFi Access",
        manufacturer=MANUFACTURER,
        model="UniFi Access",
    )


def doorbell_device_info(
    namespace: str, device_id: str, device_name: str | None
) -> DeviceInfo:
    """Return the Access hardware that reports doorbell rings."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"access_device_{namespace}_{device_id}")},
        name=device_name or f"Access device {device_id}",
        manufacturer=MANUFACTURER,
    )


class UnifiAccessEntity(CoordinatorEntity[UnifiInsightsAccessCoordinator]):
    """Base entity for Access. Available only with valid, undenied, present data."""

    _attr_has_entity_name = True
    _feature: Feature = "doors"

    def __init__(self, coordinator: UnifiInsightsAccessCoordinator) -> None:
        """Bind the entity to the coordinator that owns its console."""
        super().__init__(coordinator)
        namespace = coordinator.namespace
        if namespace is None:
            msg = "Access entities need a valid console namespace"
            raise ValueError(msg)
        self._namespace = namespace

    @property
    def available(self) -> bool:
        """Return False unless the last refresh worked and the resource is present."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.feature_available(self._feature)
            and self._is_present()
        )

    def _is_present(self) -> bool:
        """Return True when the entity's resource is in the current data."""
        return True

    @staticmethod
    async def _async_command[**P](
        message: str,
        command: Callable[P, Awaitable[None]],
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> None:
        """
        Call an Access coordinator command (ADR-010).

        The coordinator translates every UniFi API failure itself. Any other
        failure keeps ``message`` as a translated ``command_failed`` error,
        as before the command path was typed.
        """
        await async_execute_command(
            CommandFailure(translation_key="command_failed", message=message),
            command,
            *args,
            **kwargs,
        )


class UnifiAccessDoorEntity(UnifiAccessEntity):
    """Base entity for one Access door."""

    def __init__(
        self, coordinator: UnifiInsightsAccessCoordinator, door_id: str
    ) -> None:
        """Bind the entity to one door id."""
        super().__init__(coordinator)
        self._door_id = door_id

    @property
    def door(self) -> dict[str, object] | None:
        """Return the door record, or None when the door is gone."""
        door: dict[str, object] | None = self.coordinator.data["doors"].get(
            self._door_id
        )
        return door

    def _is_present(self) -> bool:
        """Return True while the door is in the current data."""
        return self.door is not None


def access_coordinator_for(
    entry: ConfigEntry,
) -> UnifiInsightsAccessCoordinator | None:
    """Return the entry's Access coordinator, or None for any other entry type."""
    # Imported here: the package __init__ imports the platforms, so a module-level
    # import would make a cycle.
    from . import UnifiInsightsData  # noqa: PLC0415

    data = getattr(entry, "runtime_data", None)
    if not isinstance(data, UnifiInsightsData):
        return None
    return data.access_coordinator


@callback
def async_discover_access_entities(
    entry: ConfigEntry,
    coordinator: UnifiInsightsAccessCoordinator,
    add_entities: AddEntitiesCallback,
    *,
    known_ids: set[str],
    keys: Callable[[], Iterable[str]],
    make: Callable[[str], Entity],
) -> None:
    """
    Add entities for new keys now and after every coordinator update.

    Nothing is added and no listener is registered without a valid console
    namespace, so entities never get an identity the console does not own.
    """
    if coordinator.namespace is None:
        return

    @callback
    def _discover() -> None:
        """Add the entities for keys that have not been seen before."""
        new_keys = [key for key in keys() if key not in known_ids]
        if not new_keys:
            return
        known_ids.update(new_keys)
        add_entities([make(key) for key in new_keys])

    _discover()
    entry.async_on_unload(coordinator.async_add_listener(_discover))


# --- Carrier Fabric ----------------------------------------------------------

# Base entity classes for UniFi Carrier Fabric integration.


def carrier_organisation_device_fields(
    entry: CarrierFabricConfigEntry,
) -> dict[str, Any]:
    """
    Return the fields of the service device that groups an organisation.

    A plain dict, so setup can pass it to ``async_get_or_create`` as keyword
    arguments: ``DeviceInfo`` is a dataclass from Home Assistant 2026.11.
    """
    return {
        "identifiers": {(DOMAIN, entry.unique_id or entry.entry_id)},
        "name": "UniFi Carrier Fabric",
        "manufacturer": "Ubiquiti",
        "model": "Carrier Fabric",
        "entry_type": DeviceEntryType.SERVICE,
    }


def carrier_organisation_device_info(entry: CarrierFabricConfigEntry) -> DeviceInfo:
    """Return the service device that groups an organisation's entities."""
    device_info: DeviceInfo = DeviceInfo(**carrier_organisation_device_fields(entry))  # type: ignore[typeddict-item, unused-ignore]
    return device_info


class UnifiCarrierFabricEntity(
    CoordinatorEntity["UnifiInsightsCarrierFabricCoordinator"]
):
    """Base entity for UniFi Carrier Fabric organisation-level entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: UnifiInsightsCarrierFabricCoordinator,
        entry: CarrierFabricConfigEntry,
    ) -> None:
        """Initialize the Carrier Fabric entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._entry_unique_id: str = entry.unique_id or entry.entry_id

    @property
    def device_info(self) -> DeviceInfo:
        """Return organisation device info."""
        return carrier_organisation_device_info(self._entry)


class UnifiCarrierFabricSubscriberEntity(UnifiCarrierFabricEntity):
    """Base entity for UniFi Carrier Fabric subscriber entities."""

    def __init__(
        self,
        coordinator: UnifiInsightsCarrierFabricCoordinator,
        entry: CarrierFabricConfigEntry,
        subscriber_id: str,
        organisation_device_id: str,
    ) -> None:
        """Initialize the subscriber entity under its organisation's device."""
        super().__init__(coordinator, entry)
        self._subscriber_id = subscriber_id
        self._organisation_device_id = organisation_device_id

    @property
    def _subscriber_data(self) -> dict[str, Any] | None:
        """Get subscriber data from coordinator."""
        subs = self.coordinator.data.get("subscribers", {})
        if isinstance(subs, dict):
            return subs.get(self._subscriber_id)
        return None

    @property
    def available(self) -> bool:
        """Return True if subscriber is present in coordinator data."""
        if not super().available:
            return False
        subs = self.coordinator.data.get("subscribers", {})
        return isinstance(subs, dict) and self._subscriber_id in subs

    @property
    def device_info(self) -> DeviceInfo:
        """Return subscriber device info."""
        sub = self._subscriber_data or {}
        name = (
            sub.get("name")
            or sub.get("subscriberNumber")
            or f"Subscriber {self._subscriber_id[:8]}"
        )
        return DeviceInfo(
            identifiers={(DOMAIN, f"carrier_subscriber_{self._subscriber_id}")},
            via_device_id=self._organisation_device_id,
            name=str(name),
            manufacturer="Ubiquiti",
            model="Subscriber",
            entry_type=DeviceEntryType.SERVICE,
        )


# --- InnerSpace --------------------------------------------------------------


class UnifiInnerSpaceEntity(CoordinatorEntity[UnifiInsightsFacadeCoordinator]):
    """Base class for UniFi InnerSpace entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        description: EntityDescription,
        record_id: str,
    ) -> None:
        """Initialize the InnerSpace entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._record_id = record_id
        self._attr_unique_id = f"innerspace_{record_id}_{description.key}"
        self._set_device_fields(self._build_device_fields())

    @property
    def innerspace_record(self) -> dict[str, Any] | None:
        """Return the normalized InnerSpace record from coordinator data."""
        innerspace = self.coordinator.data.get("innerspace", {})
        if not isinstance(innerspace, dict):
            return None
        devices = innerspace.get("devices", {})
        if not isinstance(devices, dict):
            return None
        record = devices.get(self._record_id)
        return record if isinstance(record, dict) else None

    def _build_device_fields(self) -> dict[str, Any]:
        """
        Build the device fields for an InnerSpace record.

        Attaches to an existing Network or Protect device when MAC correlation
        is unambiguous, or creates a standalone InnerSpace device otherwise.
        Does not set ``suggested_area`` from floor-plan names. The fields stay
        a plain dict because reconciliation reads them again; ``DeviceInfo``
        is a dataclass from Home Assistant 2026.11.
        """
        record = self.innerspace_record or {}
        matched_domain = record.get("matched_domain")
        matched_site_id = record.get("matched_site_id")
        matched_device_id = record.get("matched_device_id")
        matched_protect_type = record.get("matched_protect_type")

        if (
            matched_domain == "network"
            and isinstance(matched_site_id, str)
            and isinstance(matched_device_id, str)
            and network_device(
                self.coordinator.data, matched_site_id, matched_device_id
            )
            is not None
        ):
            return {
                "identifiers": {(DOMAIN, f"{matched_site_id}_{matched_device_id}")},
            }

        if (
            matched_domain == "protect"
            and isinstance(matched_protect_type, str)
            and isinstance(matched_device_id, str)
            and protect_device(
                self.coordinator.data, f"{matched_protect_type}s", matched_device_id
            )
            is not None
        ):
            return {
                "identifiers": {
                    (DOMAIN, f"protect_{matched_protect_type}_{matched_device_id}")
                },
            }

        name = (
            record.get("name")
            or record.get("model")
            or f"UniFi InnerSpace Device {self._record_id}"
        )
        fields: dict[str, Any] = {
            "identifiers": {(DOMAIN, f"innerspace_{self._record_id}")},
            "name": str(name),
            "manufacturer": MANUFACTURER,
            "model": record.get("model") or "UniFi InnerSpace Device",
        }
        if serial := record.get("serial"):
            fields["serial_number"] = str(serial)
        return fields

    def _set_device_fields(self, fields: dict[str, Any]) -> DeviceInfo:
        """Keep the fields and the device info built from them together."""
        self._device_fields = fields
        device_info: DeviceInfo = DeviceInfo(**fields)  # type: ignore[typeddict-item, unused-ignore]
        self._attr_device_info = device_info
        return device_info

    @property
    def device_info(self) -> DeviceInfo:
        """Return current device info reflecting any updated MAC correlation."""
        return self._set_device_fields(self._build_device_fields())

    def _reconcile_device_association(self, new_fields: dict[str, Any]) -> None:
        """Reconcile entity registry device_id when MAC correlation changes."""
        old_identifiers = self._device_fields.get("identifiers")
        new_identifiers = new_fields.get("identifiers")
        self._set_device_fields(new_fields)
        if (
            old_identifiers == new_identifiers
            or not new_identifiers
            or self.hass is None
            or not self.entity_id
            or self.coordinator.config_entry is None
        ):
            return
        ent_reg = er.async_get(self.hass)
        if ent_reg.async_get(self.entity_id) is None:
            return
        dev_reg = dr.async_get(self.hass)
        # Pass only the fields that are set: an explicit None clears a field,
        # and a matched Network or Protect device keeps its own metadata.
        device = dev_reg.async_get_or_create(
            config_entry_id=self.coordinator.config_entry.entry_id,
            **{
                key: new_fields[key]
                for key in (
                    "identifiers",
                    "name",
                    "manufacturer",
                    "model",
                    "serial_number",
                )
                if key in new_fields
            },
        )
        ent_reg.async_update_entity(self.entity_id, device_id=device.id)

    @property
    def available(self) -> bool:
        """Return True if the InnerSpace coordinator and record are available."""
        return bool(
            owner_available(self.coordinator.data, "innerspace")
            and self.innerspace_record is not None
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._reconcile_device_association(self._build_device_fields())
        self.async_write_ha_state()


# --- Mobility ----------------------------------------------------------------

_DEFAULT_ROUTER_NAME = "UniFi Mobile Router"


_WORKSPACE_MODEL = "UniFi Mobility workspace"


def mobility_workspace_device_fields(
    workspace_id: str, name: str | None
) -> dict[str, Any]:
    """
    Return the fields of the service device that groups a workspace's routers.

    A plain dict, so discovery can pass it to ``async_get_or_create`` as
    keyword arguments: ``DeviceInfo`` is a dataclass from Home Assistant 2026.11.
    """
    return {
        "identifiers": {(DOMAIN, f"{MOBILITY_WORKSPACE_PREFIX}{workspace_id}")},
        "name": name or _WORKSPACE_MODEL,
        "manufacturer": MANUFACTURER,
        "model": _WORKSPACE_MODEL,
        "entry_type": DeviceEntryType.SERVICE,
    }


def mobility_workspace_device_info(workspace_id: str, name: str | None) -> DeviceInfo:
    """Return the service device that groups a workspace's routers."""
    fields = mobility_workspace_device_fields(workspace_id, name)
    device_info: DeviceInfo = DeviceInfo(**fields)  # type: ignore[typeddict-item, unused-ignore]
    return device_info


class UnifiMobilityRouterEntity(CoordinatorEntity[UnifiInsightsMobilityCoordinator]):
    """Base class for entities of one Mobility router."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: UnifiInsightsMobilityCoordinator,
        description: EntityDescription,
        device_id: str,
        workspace_device_id: str,
    ) -> None:
        """Initialize the router entity under its workspace's device."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_id = device_id
        # Keyed by the router alone, so moving it to another workspace keeps
        # its entities and history.
        self._attr_unique_id = f"{MOBILITY_DEVICE_PREFIX}{device_id}_{description.key}"
        device = self.router or {}
        name = device.get("name") or device.get("model") or _DEFAULT_ROUTER_NAME
        # No MAC connection: a Mobility router is its own device, and must not
        # merge into a Network device that happens to share the address.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{MOBILITY_DEVICE_PREFIX}{device_id}")},
            name=str(name),
            manufacturer=MANUFACTURER,
            model=str(device.get("model") or _DEFAULT_ROUTER_NAME),
            sw_version=device.get("firmware_version") or None,
            via_device_id=workspace_device_id,
        )

    @property
    def router(self) -> dict[str, Any] | None:
        """Return the router's latest record, if Mobility still reports it."""
        device = self.coordinator.data["devices"].get(self._device_id)
        return device if isinstance(device, dict) else None

    @property
    def available(self) -> bool:
        """Return True if the last poll succeeded and still lists the router."""
        return super().available and self.router is not None


@callback
def async_add_mobility_router_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: UnifiInsightsMobilityCoordinator,
    async_add_entities: AddEntitiesCallback,
    build: Callable[[str, str], list[Entity]],
) -> None:
    """Add a platform's router entities now and for routers found later."""
    known: set[str] = set()
    device_registry = dr.async_get(hass)

    @callback
    def async_discover() -> None:
        entities: list[Entity] = []
        for device_id, device in coordinator.data["devices"].items():
            if device_id in known:
                continue
            known.add(device_id)
            # A router device links to its workspace's device, which must be
            # registered first. Platforms set up in any order, so whichever
            # adds a router first registers the workspace.
            workspace_id = device.get("workspace_id")
            workspace = coordinator.data["workspaces"].get(workspace_id) or {}
            workspace_device = device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                **mobility_workspace_device_fields(workspace_id, workspace.get("name")),
            )
            entities.extend(build(device_id, workspace_device.id))
        if entities:
            async_add_entities(entities)

    async_discover()
    entry.async_on_unload(coordinator.async_add_listener(async_discover))


# --- Protect security devices ------------------------------------------------


def protect_security_devices(
    coordinator: UnifiInsightsFacadeCoordinator,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Return (device_type, device_id, data) for every security device."""
    if not coordinator.protect_client:
        return []
    protect = coordinator.data.get("protect")
    if not isinstance(protect, dict):
        return []
    devices: list[tuple[str, str, dict[str, Any]]] = []
    for device_type in (
        DEVICE_TYPE_ALARM_HUB,
        DEVICE_TYPE_LINK_STATION,
        DEVICE_TYPE_FOB,
    ):
        collection = protect.get(f"{device_type}s")
        if not isinstance(collection, dict):
            continue
        devices.extend(
            (device_type, device_id, data)
            for device_id, data in collection.items()
            if isinstance(data, dict)
        )
    return devices
