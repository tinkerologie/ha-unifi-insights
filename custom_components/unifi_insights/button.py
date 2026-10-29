# Copyright 2026 UniFi Insights contributors
"""Support for UniFi Insights buttons."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.button import (
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client_wake import (
    find_client_by_mac,
    first_non_blank_text,
    mac_from_wake_unique_id,
    wake_unique_id,
)
from .console_identity import async_migrate_site_entity, resolve_console_namespace
from .const import (
    ATTR_CHIME_ID,
    ATTR_CHIME_NAME,
    ATTR_CHIME_RINGTONE_ID,
    CONF_CLIENT_CONTROL,
    DEFAULT_CLIENT_CONTROL,
    DEFAULT_VOUCHER_NAME,
    DEVICE_TYPE_CAMERA,
    DEVICE_TYPE_CHIME,
    DOMAIN,
    MANUFACTURER,
)
from .coordinators import UnifiInsightsFacadeCoordinator
from .coordinators.device import (
    get_device_port,
    port_can_be_power_cycled,
    power_cyclable_ports,
)
from .data_transforms import (
    current_chime_ringtone_id,
    get_port_label,
    network_client,
    owner_available,
    protect_collection,
    protect_device,
    section_by_site,
    site_record,
    site_section_available,
    voucher_request_kwargs,
    voucher_site_ids,
)
from .entity import (
    UnifiAccessDoorEntity,
    UnifiInsightsEntity,
    UnifiProtectEntity,
    UnifiSiteEntity,
    access_coordinator_for,
    access_unique_id,
    async_discover_access_entities,
    build_site_device_info,
    camera_supports_ptz,
    door_device_info,
    get_client_type,
    get_field,
)
from .topology import normalize_mac

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import UnifiInsightsConfigEntry
    from .coordinators.access import UnifiInsightsAccessCoordinator

_LOGGER = logging.getLogger(__name__)

# Buttons are action-based, allow parallel execution
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class UnifiInsightsButtonEntityDescription(ButtonEntityDescription):
    """Class describing UniFi Insights button entities."""


def _wired_client_mac(client: dict[str, Any]) -> str | None:
    """Return a wired client's normalized MAC, or None for any other client."""
    if get_client_type(client) != "WIRED":
        return None
    return normalize_mac(get_field(client, "macAddress", "mac_address", "mac"))


def _connected_client_macs(coordinator: UnifiInsightsFacadeCoordinator) -> set[str]:
    """Return the normalized MACs of all clients currently connected."""
    macs: set[str] = set()
    clients_by_site = section_by_site(coordinator.data, "clients") or {}
    for clients in clients_by_site.values():
        for client in clients.values():
            raw_mac = get_field(client, "macAddress", "mac_address", "mac")
            if (mac := normalize_mac(raw_mac)) is not None:
                macs.add(mac)
    return macs


def _registered_wake_macs(registry: er.EntityRegistry, entry_id: str) -> set[str]:
    """Return the MACs of this entry's Wake buttons already in the entity registry."""
    macs: set[str] = set()
    for reg_entry in er.async_entries_for_config_entry(registry, entry_id):
        if reg_entry.domain != "button" or reg_entry.platform != DOMAIN:
            continue
        if (mac := mac_from_wake_unique_id(reg_entry.unique_id)) is not None:
            macs.add(mac)
    return macs


BUTTON_TYPES: tuple[UnifiInsightsButtonEntityDescription, ...] = (
    UnifiInsightsButtonEntityDescription(
        key="device_restart",
        translation_key="device_restart",
    ),
)


class UnifiAccessUnlockButton(UnifiAccessDoorEntity, ButtonEntity):
    """Remotely unlock one bound, non-gate Access door."""

    _attr_translation_key = "access_door_unlock"

    def __init__(
        self, coordinator: UnifiInsightsAccessCoordinator, door_id: str
    ) -> None:
        """Create the unlock button for one door."""
        super().__init__(coordinator, door_id)
        self._attr_unique_id = access_unique_id(self._namespace, door_id, "unlock")
        door = self.door or {}
        name = door.get("name")
        self._attr_device_info = door_device_info(
            self._namespace, door_id, name if isinstance(name, str) else None
        )

    def _is_present(self) -> bool:
        """Return True only for doors the console can remotely unlock."""
        return self._door_id in self.coordinator.unlock_door_ids

    async def async_press(self) -> None:
        """Send the remote unlock; the relay state comes from the read-back."""
        await self._async_command(
            "Could not unlock the Access door",
            self.coordinator.async_unlock_door,
            self._door_id,
        )


@callback
def async_setup_access_buttons(
    entry: UnifiInsightsConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create an unlock button for every door the console can unlock remotely."""
    coordinator = access_coordinator_for(entry)
    if coordinator is None:
        return

    async_discover_access_entities(
        entry,
        coordinator,
        async_add_entities,
        known_ids=set(),
        keys=lambda: sorted(coordinator.unlock_door_ids),
        make=lambda door_id: UnifiAccessUnlockButton(coordinator, door_id),
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up buttons for UniFi Insights integration."""
    # Access is set up before the Protect-dependent buttons below.
    async_setup_access_buttons(config_entry, async_add_entities)
    coordinator: UnifiInsightsFacadeCoordinator = config_entry.runtime_data.coordinator
    client_control = config_entry.options.get(
        CONF_CLIENT_CONTROL, DEFAULT_CLIENT_CONTROL
    )

    # Remove orphaned client buttons (reconnect, Wake) when client control is off.
    registry = er.async_get(hass)
    if not client_control:
        for reg_entry in er.async_entries_for_config_entry(
            registry, config_entry.entry_id
        ):
            if (
                reg_entry.domain == "button"
                and reg_entry.platform == DOMAIN
                and (
                    reg_entry.unique_id.endswith("_reconnect")
                    or mac_from_wake_unique_id(reg_entry.unique_id) is not None
                )
            ):
                _LOGGER.debug(
                    "Removing client button %s (client control disabled)",
                    reg_entry.entity_id,
                )
                registry.async_remove(reg_entry.entity_id)

    _LOGGER.debug("Setting up buttons for UniFi Insights")
    known_button_keys: set[tuple[Any, ...]] = set()

    # Restore enabled buttons immediately, even without a history response.
    # Disabled buttons qualify only while connected or in the recent named seed.
    known_wake_macs: set[str] = set()
    if client_control:
        registered_macs = _registered_wake_macs(registry, config_entry.entry_id)
        connected_macs = _connected_client_macs(coordinator)
        restored: dict[str, UnifiClientWakeButton] = {}
        enabled_macs: set[str] = set()
        snapshot: Mapping[str, Any] = coordinator.data
        coord_data = snapshot or {}
        clients_by_site = section_by_site(coordinator.data, "clients")
        for row in er.async_entries_for_config_entry(registry, config_entry.entry_id):
            mac = mac_from_wake_unique_id(row.unique_id)
            if row.domain != "button" or row.platform != DOMAIN or mac is None:
                continue
            if row.disabled_by is None:
                enabled_macs.add(mac)
            elif mac not in registered_macs & connected_macs:
                continue
            live = find_client_by_mac(clients_by_site, mac)
            site_id = live[0] if live else next(iter(coord_data.get("sites", {})), "")
            device = (
                dr.async_get(hass).async_get(row.device_id) if row.device_id else None
            )
            saved_name = first_non_blank_text(
                {"name": row.name, "original_name": row.original_name},
                "name",
                "original_name",
            )
            if saved_name:
                saved_name = (
                    "" if saved_name == "Wake" else saved_name.removeprefix("Wake ")
                )
            restored[mac] = UnifiClientWakeButton(
                coordinator,
                mac,
                site_id=site_id,
                fallback_name=saved_name,
                device_info=DeviceInfo(identifiers=device.identifiers)
                if device
                else None,
            )
        known_wake_macs.update(restored)
        if restored:
            async_add_entities(list(restored.values()))

        async def _async_seed_wake_history() -> None:
            """Fetch console history in background and seed Wake buttons."""
            try:
                history = await coordinator.async_get_wake_history(
                    enabled_macs=enabled_macs
                )
            except Exception as err:
                _LOGGER.debug("Wake-on-LAN client history seed failed: %s", err)
                return
            if not isinstance(history, dict) or not history:
                return
            entities: list[UnifiClientWakeButton] = []
            for mac, (fallback_name, seed_site) in sorted(history.items()):
                if mac in restored:
                    restored[mac].async_update_history_name(fallback_name)
                if mac in known_wake_macs:
                    continue
                known_wake_macs.add(mac)
                entities.append(
                    UnifiClientWakeButton(
                        coordinator,
                        mac,
                        fallback_name=fallback_name,
                        site_id=seed_site,
                    )
                )
            if entities:
                async_add_entities(entities)

        config_entry.async_create_background_task(
            hass, _async_seed_wake_history(), name=f"{DOMAIN}_wake_seed"
        )

    @callback
    def async_discover_buttons() -> None:
        """Discover and add new button entities."""
        current_client_control = config_entry.options.get(
            CONF_CLIENT_CONTROL, DEFAULT_CLIENT_CONTROL
        )
        entities: list[ButtonEntity] = []
        namespace = resolve_console_namespace(config_entry)

        for site_id in voucher_site_ids(coordinator.data):
            voucher_key = (site_id, "generate_voucher")
            if voucher_key in known_button_keys:
                continue
            unique_id = async_migrate_site_entity(
                hass,
                config_entry,
                "button",
                namespace,
                site_id,
                suffix="generate_voucher",
            )
            if unique_id is None:
                continue
            button = UnifiGenerateVoucherButton(
                coordinator, site_id, unique_id=unique_id
            )
            known_button_keys.add(voucher_key)
            entities.append(button)

        # Add buttons for each device in each site
        devices_by_site = section_by_site(coordinator.data, "devices") or {}
        for site_id, devices in devices_by_site.items():
            site_data = site_record(coordinator.data, site_id)
            site_name = (
                (site_data.get("meta") or {}).get("name", site_id)
                if site_data
                else site_id
            )

            _LOGGER.debug(
                "Processing site %s (%s) with %d devices",
                site_id,
                site_name,
                len(devices),
            )

            for device_id in devices:
                for description in BUTTON_TYPES:
                    btn_key = (site_id, device_id, description.key)
                    if btn_key in known_button_keys:
                        continue
                    known_button_keys.add(btn_key)
                    entities.append(
                        UnifiInsightsButton(
                            coordinator=coordinator,
                            description=description,
                            site_id=site_id,
                            device_id=device_id,
                        )
                    )

                # Discover PoE power-cycle buttons for PoE-enabled ports
                for port_facts in power_cyclable_ports(
                    coordinator.data, site_id, device_id
                ):
                    port = port_facts.record
                    port_idx = port_facts.raw_index

                    poe_btn_key = (
                        site_id,
                        device_id,
                        f"port{port_idx}_poe_power_cycle",
                    )
                    if poe_btn_key in known_button_keys:
                        continue
                    known_button_keys.add(poe_btn_key)

                    port_label = get_port_label(port, port_idx)
                    entities.append(
                        UnifiInsightsPoePowerCycleButton(
                            coordinator=coordinator,
                            site_id=site_id,
                            device_id=device_id,
                            port_idx=port_idx,
                            port_label=port_label,
                        )
                    )

        # Add reconnect buttons for connected clients (when client control is enabled)
        if current_client_control:
            clients_by_site = section_by_site(coordinator.data, "clients") or {}
            for site_id, clients in clients_by_site.items():
                for client_id in clients:
                    client_key = (site_id, client_id, "reconnect")
                    if client_key in known_button_keys:
                        continue
                    known_button_keys.add(client_key)
                    entities.append(
                        UnifiClientReconnectButton(
                            coordinator=coordinator,
                            site_id=site_id,
                            client_id=client_id,
                        )
                    )
            for site_id, clients in clients_by_site.items():
                for client_data in clients.values():
                    wake_mac = _wired_client_mac(client_data)
                    if wake_mac is None or wake_mac in known_wake_macs:
                        continue
                    known_wake_macs.add(wake_mac)
                    entities.append(
                        UnifiClientWakeButton(
                            coordinator, wake_mac, site_id=str(site_id)
                        )
                    )

        # Add UniFi Protect buttons
        if coordinator.protect_client:
            # Add play button for each chime
            chimes = protect_collection(coordinator.data, "chimes") or {}
            for chime_id in chimes:
                chime_key = (chime_id, "play")
                if chime_key not in known_button_keys:
                    known_button_keys.add(chime_key)
                    entities.append(
                        UnifiProtectChimePlayButton(
                            coordinator=coordinator,
                            chime_id=chime_id,
                        )
                    )

            # Add PTZ patrol start/stop buttons for cameras with PTZ support
            cameras = protect_collection(coordinator.data, "cameras") or {}
            for camera_id, camera_data in cameras.items():
                if not camera_supports_ptz(camera_data):
                    continue
                start_key = (camera_id, "ptz_start")
                if start_key not in known_button_keys:
                    known_button_keys.add(start_key)
                    entities.append(
                        UnifiProtectPTZPatrolStartButton(
                            coordinator=coordinator,
                            camera_id=camera_id,
                        )
                    )

                stop_key = (camera_id, "ptz_stop")
                if stop_key not in known_button_keys:
                    known_button_keys.add(stop_key)
                    entities.append(
                        UnifiProtectPTZPatrolStopButton(
                            coordinator=coordinator,
                            camera_id=camera_id,
                        )
                    )

        if entities:
            _LOGGER.info("Adding %d UniFi Insights buttons", len(entities))
            async_add_entities(entities)

    async_discover_buttons()
    config_entry.async_on_unload(coordinator.async_add_listener(async_discover_buttons))


class UnifiInsightsButton(UnifiInsightsEntity, ButtonEntity):
    """Representation of a UniFi Insights Button."""

    entity_description: UnifiInsightsButtonEntityDescription

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        description: UnifiInsightsButtonEntityDescription,
        site_id: str,
        device_id: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, description, site_id, device_id)

        _LOGGER.debug(
            "Initializing button %s for device %s in site %s",
            description.key,
            device_id,
            site_id,
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.debug(
            "Restarting device %s (%s) in site %s",
            self._device_id,
            self.device_data.get("name", self._device_id)
            if self.device_data
            else self._device_id,
            self._site_id,
        )

        await self.coordinator.device_coordinator.async_restart_device(
            self._site_id, self._device_id
        )

        _LOGGER.info(
            "Successfully initiated restart for device %s in site %s",
            self._device_id,
            self._site_id,
        )


class UnifiInsightsPoePowerCycleButton(UnifiInsightsEntity, ButtonEntity):
    """Representation of a UniFi Insights PoE port power-cycle button."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "poe_power_cycle"

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        site_id: str,
        device_id: str,
        port_idx: int,
        port_label: str,
    ) -> None:
        """Initialize the PoE power-cycle button."""
        description = ButtonEntityDescription(
            key=f"port{port_idx}_poe_power_cycle",
            translation_key="poe_power_cycle",
            entity_category=EntityCategory.CONFIG,
            entity_registry_enabled_default=False,
        )
        super().__init__(coordinator, description, site_id, device_id)
        self._port_idx = port_idx
        self._port_label = port_label
        self._attr_unique_id = f"{site_id}_{device_id}_port{port_idx}_poe_power_cycle"
        self._attr_translation_placeholders = {"port_label": port_label}

    @property
    def available(self) -> bool:
        """Return True if device is online, port exists, and PoE is enabled."""
        if not super().available:
            return False
        port = get_device_port(
            self.coordinator.data,
            self._site_id,
            self._device_id,
            self._port_idx,
        )
        return port is not None and port_can_be_power_cycled(port)

    @property
    def port_idx(self) -> int:
        """Return the port index."""
        return self._port_idx

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.info(
            "Power cycling PoE port %d on device %s",
            self._port_idx,
            self._device_id,
        )
        await self.coordinator.device_coordinator.async_power_cycle_port(
            self._site_id, self._device_id, self._port_idx
        )


class UnifiProtectChimePlayButton(UnifiProtectEntity, ButtonEntity):
    """Button to play a ringtone on a UniFi Protect Chime."""

    _attr_has_entity_name = True
    _attr_translation_key = "play"

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        chime_id: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, DEVICE_TYPE_CHIME, chime_id, "play")

        # Set attributes
        self._update_attributes()

    def _update_attributes(self) -> None:
        """Update button attributes."""
        chime_data = (
            protect_device(self.coordinator.data, "chimes", self._device_id) or {}
        )

        self._attr_extra_state_attributes = {
            ATTR_CHIME_ID: self._device_id,
            ATTR_CHIME_NAME: chime_data.get("name"),
            ATTR_CHIME_RINGTONE_ID: current_chime_ringtone_id(chime_data),
        }

    async def async_press(self) -> None:
        """Report that the official API cannot play a chime."""
        protect = self.coordinator.require_protect_coordinator()
        await protect.async_play_chime(self._device_id)


class UnifiClientReconnectButton(
    CoordinatorEntity[UnifiInsightsFacadeCoordinator], ButtonEntity
):
    """Button to force a client to reconnect."""

    _attr_has_entity_name = True
    _attr_translation_key = "client_reconnect"

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        site_id: str,
        client_id: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._site_id = site_id
        self._client_id = client_id

        # Get client info for naming
        client_data = self._get_client_data()
        client_name = (
            client_data.get("name")
            or client_data.get("hostname")
            or client_data.get("mac")
            or client_id
        )

        self._attr_unique_id = f"{site_id}_{client_id}_reconnect"
        self._attr_translation_placeholders = {"client_name": client_name}

        # Device info - associate with the connected network device (switch/AP)
        uplink_device_id = client_data.get("uplinkDeviceId") or client_data.get(
            "uplink_device_id"
        )
        if uplink_device_id:
            # Use the network device's identifiers to group under it
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{site_id}_{uplink_device_id}")},
            )
        else:
            # Fallback: create a standalone client device if no uplink found
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"client_{client_id}")},
                name=client_name,
                manufacturer=MANUFACTURER,
                model="Network Client",
            )

    def _get_client_data(self) -> dict[str, Any]:
        """Get client data from coordinator."""
        result = network_client(self.coordinator.data, self._site_id, self._client_id)
        return result if isinstance(result, dict) else {}

    @property
    def available(self) -> bool:
        """Return if button is available."""
        if not owner_available(self.coordinator.data, "device"):
            return False
        client_data = self._get_client_data()
        return bool(client_data)

    async def async_press(self) -> None:
        """Force client to reconnect."""
        _LOGGER.debug(
            "Reconnecting client %s in site %s", self._client_id, self._site_id
        )

        await self.coordinator.device_coordinator.async_reconnect_client(
            self._site_id, self._client_id
        )
        _LOGGER.info(
            "Successfully reconnected client %s in site %s",
            self._client_id,
            self._site_id,
        )


class UnifiClientWakeButton(
    CoordinatorEntity[UnifiInsightsFacadeCoordinator], ButtonEntity
):
    """Button that wakes a wired client with a Wake-on-LAN magic packet."""

    _attr_has_entity_name = True
    _attr_translation_key = "client_wake"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        mac: str,
        *,
        site_id: str | None,
        fallback_name: str | None = None,
        device_info: DeviceInfo | None = None,
    ) -> None:
        """
        Initialize the button.

        mac is the lower-case colon MAC. fallback_name is the name the
        console's client history holds, used only while the client is not connected.
        site_id is the client's site. Registry restoration preserves its device.
        """
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = wake_unique_id(mac)

        if device_info is not None:
            self._attr_device_info = device_info
        elif site_id:
            site_info = build_site_device_info(coordinator.data, site_id)
            self._attr_device_info = DeviceInfo(**site_info)  # type: ignore[typeddict-item, unused-ignore]
        self.async_update_history_name(fallback_name)

    @callback
    def async_update_history_name(self, fallback_name: str | None) -> None:
        """Apply a history name while preferring the current live client name."""
        snapshot: Mapping[str, Any] = self.coordinator.data
        live = find_client_by_mac((snapshot or {}).get("clients"), self._mac)
        live_name = first_non_blank_text(live[1], "name", "hostname") if live else None
        client_name = live_name or (
            fallback_name.strip()
            if fallback_name and fallback_name.strip()
            else self._mac
        )
        self._client_name = client_name
        self._attr_translation_placeholders = {"client_name": client_name}
        vars(self).pop("name", None)
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return True while the console can send a Wake-on-LAN packet."""
        return owner_available(self.coordinator.data, "device")

    async def async_press(self) -> None:
        """Send the Wake-on-LAN magic packet."""
        _LOGGER.debug("Sending Wake-on-LAN packet from a Wake button")
        await self.coordinator.device_coordinator.async_wake_client(
            self._mac, client_name=self._client_name
        )


class UnifiProtectPTZPatrolStartButton(UnifiProtectEntity, ButtonEntity):
    """Button to start PTZ patrol on a UniFi Protect camera."""

    _attr_has_entity_name = True
    _attr_translation_key = "ptz_patrol_start"

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        camera_id: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, DEVICE_TYPE_CAMERA, camera_id, "ptz_patrol_start")

    async def async_press(self) -> None:
        """Start PTZ patrol."""
        _LOGGER.debug("Starting PTZ patrol for camera %s", self._device_id)

        protect = self.coordinator.require_protect_coordinator()
        await protect.async_start_ptz_patrol(self._device_id, 0)
        _LOGGER.info("Successfully started PTZ patrol for camera %s", self._device_id)


class UnifiProtectPTZPatrolStopButton(UnifiProtectEntity, ButtonEntity):
    """Button to stop PTZ patrol on a UniFi Protect camera."""

    _attr_has_entity_name = True
    _attr_translation_key = "ptz_patrol_stop"

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        camera_id: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, DEVICE_TYPE_CAMERA, camera_id, "ptz_patrol_stop")

    async def async_press(self) -> None:
        """Stop PTZ patrol."""
        _LOGGER.debug("Stopping PTZ patrol for camera %s", self._device_id)

        protect = self.coordinator.require_protect_coordinator()
        await protect.async_stop_ptz_patrol(self._device_id)
        _LOGGER.info("Successfully stopped PTZ patrol for camera %s", self._device_id)


class UnifiGenerateVoucherButton(UnifiSiteEntity, ButtonEntity):
    """Button entity that creates a new hotspot voucher using HA-stored settings."""

    _attr_translation_key = "generate_voucher"

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        site_id: str,
        *,
        unique_id: str,
    ) -> None:
        """Initialize the voucher generation button."""
        super().__init__(coordinator, site_id, unique_id=unique_id)

    @property
    def available(self) -> bool:
        """Return True if vouchers are available for this site."""
        return site_section_available(self.coordinator.data, self._site_id, "vouchers")

    async def async_press(self) -> None:
        """Generate a guest voucher with current HA settings."""
        owner = self.coordinator.config_coordinator
        settings = owner.get_voucher_settings(self._site_id)
        kwargs: dict[str, Any] = voucher_request_kwargs(settings)
        created = await owner.async_generate_voucher(
            self._site_id, name=DEFAULT_VOUCHER_NAME, count=1, **kwargs
        )
        if not created:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="voucher_not_created",
                translation_placeholders={"site_id": self._site_id},
            )
