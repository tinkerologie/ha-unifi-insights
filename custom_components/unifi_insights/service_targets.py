# Copyright 2026 UniFi Insights contributors
"""
Service-call target resolution for the UniFi Insights service actions.

A service call names its target as an entity, a device or a native UniFi ID.
This module turns that into a typed result: the console config entry that owns
the target, the facade the handler reads cached data from, the command owner
(ADR-010) the handler calls, and the native ID and site. ``services.py`` keeps
the schemas, the handlers and the registration (ADR-009 seam).

Only the call-level ``resolve_*_call`` functions are public. Every error raised
here is a translated ``ServiceValidationError``; the English message stays the
first argument so the text logged and shown outside the frontend is unchanged.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, NoReturn, TypeGuard, cast

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)

from .const import (
    CONF_CARRIER_ACTIONS,
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_CARRIER_FABRIC,
    DEFAULT_CARRIER_ACTIONS,
    DOMAIN,
    MOBILITY_DEVICE_PREFIX,
)
from .data_transforms import is_doorbell_camera_model

# CarrierFabricData is imported inside functions: __init__.py imports this
# module while it loads, before the class exists.
if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall

    from . import CarrierFabricConfigEntry, UnifiInsightsConfigEntry
    from .coordinators import (
        UnifiInsightsAccessCoordinator,
        UnifiInsightsCarrierFabricCoordinator,
        UnifiInsightsConfigCoordinator,
        UnifiInsightsDeviceCoordinator,
        UnifiInsightsFacadeCoordinator,
        UnifiInsightsProtectCoordinator,
    )

_LOGGER = logging.getLogger(__name__)

ProtectResourceType = Literal["camera", "light", "chime", "viewer"]

_PROTECT_COLLECTIONS: Final = {
    "camera": "cameras",
    "light": "lights",
    "chime": "chimes",
    "viewer": "viewers",
}


# --- Typed results ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NetworkDeviceTarget:
    """A Network device a restart or power-cycle call targets."""

    entry: UnifiInsightsConfigEntry
    cache: UnifiInsightsFacadeCoordinator
    device_commands: UnifiInsightsDeviceCoordinator
    device_id: str
    site_id: str


@dataclass(frozen=True, slots=True)
class NetworkClientTarget:
    """A Network client a reconnect or guest authorization call targets."""

    entry: UnifiInsightsConfigEntry
    cache: UnifiInsightsFacadeCoordinator
    device_commands: UnifiInsightsDeviceCoordinator
    config_commands: UnifiInsightsConfigCoordinator
    client_id: str
    site_id: str


@dataclass(frozen=True, slots=True)
class NetworkSiteTarget:
    """The console owning a Network site, for the voucher actions."""

    entry: UnifiInsightsConfigEntry
    cache: UnifiInsightsFacadeCoordinator
    facade_commands: UnifiInsightsFacadeCoordinator
    site_id: str


@dataclass(frozen=True, slots=True)
class NetworkConsoleTarget:
    """One console with Network, and its cached sites, for ``remove_clients``."""

    title: str
    entry: UnifiInsightsConfigEntry
    facade_commands: UnifiInsightsFacadeCoordinator
    device_commands: UnifiInsightsDeviceCoordinator
    site_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConsoleRefreshTarget:
    """One console a ``refresh_data`` call refreshes."""

    title: str
    entry: UnifiInsightsConfigEntry
    facade_commands: UnifiInsightsFacadeCoordinator


@dataclass(frozen=True, slots=True)
class RefreshSelection:
    """The consoles a ``refresh_data`` call refreshes, and whether with Protect."""

    targets: tuple[ConsoleRefreshTarget, ...]
    site_id: str | None
    # A site is a Network concept, so only a call without one refreshes Protect.
    # A blank site selects every console but still skips Protect.
    include_protect: bool


@dataclass(frozen=True, slots=True)
class ProtectTarget:
    """A Protect camera, light, chime or viewer a call targets."""

    entry: UnifiInsightsConfigEntry
    cache: UnifiInsightsFacadeCoordinator
    protect_commands: UnifiInsightsProtectCoordinator
    resource_id: str


@dataclass(frozen=True, slots=True)
class ProtectConsoleTarget:
    """The Protect console of a call that names no resource."""

    entry: UnifiInsightsConfigEntry
    cache: UnifiInsightsFacadeCoordinator
    protect_commands: UnifiInsightsProtectCoordinator


@dataclass(frozen=True, slots=True)
class ChimePairingTarget:
    """A chime and the doorbell cameras to pair with it (empty: unpair all)."""

    entry: UnifiInsightsConfigEntry
    cache: UnifiInsightsFacadeCoordinator
    protect_commands: UnifiInsightsProtectCoordinator
    chime_id: str
    camera_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuleExportTarget:
    """The console a network rule export reads from."""

    entry: UnifiInsightsConfigEntry
    cache: UnifiInsightsFacadeCoordinator
    export_commands: UnifiInsightsFacadeCoordinator
    site_id: str | None


@dataclass(frozen=True, slots=True)
class AccessTarget:
    """The UniFi Access console an Access user action runs on."""

    entry: UnifiInsightsConfigEntry
    access_commands: UnifiInsightsAccessCoordinator


@dataclass(frozen=True, slots=True)
class CarrierSubscriberTarget:
    """A Carrier Fabric subscriber a suspend or resume call targets."""

    entry: CarrierFabricConfigEntry
    carrier_commands: UnifiInsightsCarrierFabricCoordinator
    subscriber_id: str


# --- Errors -----------------------------------------------------------------


def _invalid(message: str, key: str, **placeholders: str) -> ServiceValidationError:
    """Build a translated validation error that keeps its English message."""
    return ServiceValidationError(
        message,
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


# --- Console enumeration and cached data -----------------------------------


def _is_console_entry(entry: ConfigEntry[Any]) -> TypeGuard[UnifiInsightsConfigEntry]:
    """
    Return whether the entry is a console with runtime data.

    Only Carrier Fabric entries are excluded. There is deliberately no
    ``isinstance(UnifiInsightsData)`` check: it would change which entries are
    candidates, so the narrowing to ``UnifiInsightsConfigEntry`` is static only.
    """
    from . import CarrierFabricData  # noqa: PLC0415

    return (
        hasattr(entry, "runtime_data")
        and bool(entry.runtime_data)
        and not isinstance(entry.runtime_data, CarrierFabricData)
    )


def _console_entries(hass: HomeAssistant) -> list[UnifiInsightsConfigEntry]:
    """Return every console entry, in config entry order."""
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if _is_console_entry(entry)
    ]


def _coord_data(entry: UnifiInsightsConfigEntry) -> dict[str, Any] | None:
    """Extract coordinator data dictionary if available and valid."""
    from . import CarrierFabricData  # noqa: PLC0415

    runtime_data = getattr(entry, "runtime_data", None)
    if isinstance(runtime_data, CarrierFabricData):
        return None
    # getattr keeps the facade's RuntimeError when it is not initialised.
    coord = getattr(runtime_data, "coordinator", None) if runtime_data else None
    if coord and hasattr(coord, "data") and isinstance(coord.data, dict):
        return coord.data
    return None


def _coord_section(entry: UnifiInsightsConfigEntry, key: str) -> dict[str, Any] | None:
    """Return a top-level dict section of the entry's coordinator data."""
    data = _coord_data(entry)
    if data is None:
        return None
    section = data.get(key)
    return section if isinstance(section, dict) else None


# Ownership answers have three states. ``_entry_has_*`` keeps an entry that has
# not loaded the data as a candidate (True); ``_entry_owns_*`` only answers True
# when the cached data positively contains the resource.


def _entry_has_site(entry: UnifiInsightsConfigEntry, site_id: str) -> bool:
    """Check if config entry owns the given site ID."""
    data = _coord_data(entry)
    if data is None:
        return True
    sites = data.get("sites")
    if not isinstance(sites, dict):
        return True
    return site_id in sites


def _entry_has_device(
    entry: UnifiInsightsConfigEntry, site_id: str | None, device_id: str
) -> bool:
    """Check if config entry owns the given device ID."""
    data = _coord_data(entry)
    if data is None:
        return True
    devices = data.get("devices")
    if not isinstance(devices, dict):
        return True
    if site_id is not None:
        site_devices = devices.get(site_id)
        if not isinstance(site_devices, dict):
            return True
        return device_id in site_devices
    return any(
        isinstance(devs, dict) and device_id in devs for devs in devices.values()
    )


def _entry_owns_device(
    entry: UnifiInsightsConfigEntry, site_id: str | None, device_id: str
) -> bool:
    """Return whether the entry's loaded data positively contains the device."""
    devices = _coord_section(entry, "devices")
    if devices is None:
        return False
    if site_id:
        site_devices = devices.get(site_id)
        if isinstance(site_devices, dict) and device_id in site_devices:
            return True
    return any(
        isinstance(devs, dict) and device_id in devs for devs in devices.values()
    )


def _entry_owns_client(
    entry: UnifiInsightsConfigEntry, site_id: str | None, client_id: str
) -> bool:
    """Return whether the entry's loaded data positively contains the client."""
    clients = _coord_section(entry, "clients")
    if clients is None:
        return False
    if site_id:
        site_clients = clients.get(site_id)
        if isinstance(site_clients, dict) and _client_records_match(
            site_clients, client_id
        ):
            return True
    return any(
        isinstance(cls, dict) and _client_records_match(cls, client_id)
        for cls in clients.values()
    )


def _entry_has_client(
    entry: UnifiInsightsConfigEntry, site_id: str | None, client_id: str
) -> bool:
    """Check if config entry owns the given client ID or MAC."""
    data = _coord_data(entry)
    if data is None:
        return True
    clients = data.get("clients")
    if not isinstance(clients, dict):
        return True
    if site_id is not None:
        site_clients = clients.get(site_id)
        if not isinstance(site_clients, dict):
            return True
        return _client_records_match(site_clients, client_id)
    return any(
        isinstance(cls, dict) and _client_records_match(cls, client_id)
        for cls in clients.values()
    )


def _protect_owns_resource(
    entry: UnifiInsightsConfigEntry, collection_key: str, resource_id: str
) -> bool | None:
    """
    Return whether the entry positively owns a Protect resource.

    ``None`` means "cannot tell": the entry has loaded none of the collections
    that could hold the resource, so callers must not read the answer as the
    entry either owning or disowning it.
    """
    data = _coord_data(entry)
    if data is None:
        return None
    protect_data = data.get("protect")
    if not isinstance(protect_data, dict):
        return None
    collection = protect_data.get(collection_key)
    if not isinstance(collection, dict):
        return None
    return resource_id in collection


def _protect_entry_has_resource(
    entry: UnifiInsightsConfigEntry, collection_key: str, resource_id: str
) -> bool:
    """
    Check if a Protect config entry may own the given Protect resource ID.

    An entry that has not loaded the relevant data yet stays a candidate, so a
    console that is still warming up never silently loses a service call.
    """
    owns = _protect_owns_resource(entry, collection_key, resource_id)
    return True if owns is None else owns


# --- Client identity --------------------------------------------------------


def _mac_key(value: Any) -> str | None:
    """
    Reduce a MAC to a separator-free lowercase key, or None if it is not one.

    ``DeviceCoordinator._normalize_mac`` only strips and lowercases, because it
    builds lookup keys from raw API values. Here the input is whatever a user
    typed in an action call, so ``aa:bb``, ``AA-BB`` and ``aabb`` must all
    compare equal.

    Identity policy: user-input matching. It does not validate the length or
    hex digits; the canonical ``data_transforms.normalize_mac`` does.
    """
    if not isinstance(value, str):
        return None
    key = value.strip().lower().replace(":", "").replace("-", "").replace(".", "")
    return key or None


def _client_records_match(records: dict[str, Any], client_id: str) -> bool:
    """Check a site's client records for an ID or MAC address match."""
    if client_id in records:
        return True
    wanted = _mac_key(client_id)
    if wanted is None:
        return False
    for record in records.values():
        if not isinstance(record, dict):
            continue
        for field in ("mac", "macAddress", "mac_address"):
            if _mac_key(record.get(field)) == wanted:
                return True
    return False


def _resolve_native_client_id(
    cache: UnifiInsightsFacadeCoordinator, site_id: str, client_id: str
) -> str:
    """Resolve a target client ID or MAC to the native cached client ID."""
    data = cache.data
    if isinstance(data, dict):
        clients = data.get("clients", {})
        site_clients = clients.get(site_id, {}) if isinstance(clients, dict) else {}
        if isinstance(site_clients, dict) and client_id not in site_clients:
            for native_id, record in site_clients.items():
                if _client_records_match({native_id: record}, client_id):
                    return str(native_id)
    return client_id


# --- Target extraction ------------------------------------------------------


def _extract_target_id(call: ServiceCall, primary_field: str) -> str | None:
    """
    Extract the single target of a call.

    Precedence: the primary field, then the ``target`` dict (primary field,
    entity, device), then the top-level entity and device fields. ``area_id``
    is accepted by the schemas but never read here.
    """
    val = call.data.get(primary_field)
    if val is None and "target" in call.data and isinstance(call.data["target"], dict):
        target_dict = call.data["target"]
        val = (
            target_dict.get(primary_field)
            or target_dict.get("entity_id")
            or target_dict.get("device_id")
        )
    if val is None:
        val = call.data.get("entity_id")
    if val is None:
        val = call.data.get("device_id")
    if isinstance(val, list):
        if not val:
            msg = "At least one target must be specified"
            raise _invalid(msg, "target_required")
        if len(val) > 1:
            msg = (
                f"Multiple targets specified for {call.service}; "
                "action only supports a single target"
            )
            raise _invalid(msg, "target_multiple", service=call.service)
        val = val[0]
    if isinstance(val, str):
        val = val.strip()
        return val or None
    return None


def _require_target(call: ServiceCall, primary_field: str, error: str) -> str:
    """Extract the call's target, or raise the family's "target required" error."""
    raw_id = _extract_target_id(call, primary_field)
    if raw_id:
        return raw_id
    if error == "device":
        msg = "Device ID or target is required"
        raise _invalid(msg, "device_target_required")
    if error == "client":
        msg = "Client ID or target is required"
        raise _invalid(msg, "client_target_required")
    msg = f"{error.capitalize()} ID or target is required"
    raise _invalid(msg, "protect_target_required", resource_type=error)


# --- Registry walk ----------------------------------------------------------


def _registries(
    hass: HomeAssistant,
) -> tuple[dr.DeviceRegistry | None, er.EntityRegistry | None]:
    """Return the registries, or None for one that is not set up."""
    loaded = hasattr(hass, "data") and isinstance(hass.data, dict)
    return (
        dr.async_get(hass) if loaded and dr.DATA_REGISTRY in hass.data else None,
        er.async_get(hass) if loaded and er.DATA_REGISTRY in hass.data else None,
    )


def _not_ours(target: str, kind: str, protect_type: str | None) -> NoReturn:
    """Raise "not a UniFi Insights <kind>" (or "not a UniFi Protect <type>")."""
    if protect_type is not None:
        msg = f"Target '{target}' is not a UniFi Protect {protect_type}"
        raise _invalid(
            msg,
            "target_not_protect_resource",
            target=target,
            resource_type=protect_type,
        )
    msg = f"Target '{target}' is not a UniFi Insights {kind}"
    raise _invalid(
        msg,
        (
            "target_not_integration_entity"
            if kind == "entity"
            else "target_not_integration_device"
        ),
        target=target,
    )


def _console_not_loaded() -> ServiceValidationError:
    """Build the error for a target whose owning console is not loaded."""
    msg = "Target's UniFi console is not loaded"
    return _invalid(msg, "target_console_not_loaded")


@dataclass(slots=True)
class _RegistryTarget:
    """What the registries know about a target ID."""

    entity: er.RegistryEntry | None
    device: dr.DeviceEntry | dr.ChildDeviceEntry | None
    owner: UnifiInsightsConfigEntry | None


def _device_owner_id(device: dr.DeviceEntry | dr.ChildDeviceEntry) -> str | None:
    """Return the config entry that owns a registry device."""
    return device.config_entry_id


def _lookup_registry_target(
    hass: HomeAssistant,
    target_id: str,
    entries: list[UnifiInsightsConfigEntry],
    *,
    looks_like_entity: bool,
    protect_type: str | None = None,
) -> _RegistryTarget:
    """
    Look a target up as an entity, then as a device.

    An entity of another integration, or one whose console is not among
    ``entries``, is refused. The owner is the entity's console entry, if any.
    """
    dev_reg, ent_reg = _registries(hass)
    if looks_like_entity and (ent_reg is None or ent_reg.async_get(target_id) is None):
        msg = f"Target entity '{target_id}' not found in entity registry"
        raise _invalid(msg, "target_entity_not_found", target=target_id)

    found = _RegistryTarget(entity=None, device=None, owner=None)
    if ent_reg is not None:
        found.entity = ent_reg.async_get(target_id)
        if found.entity is not None:
            if found.entity.platform != DOMAIN:
                _not_ours(target_id, "entity", protect_type)
            if found.entity.config_entry_id:
                owners = [
                    e for e in entries if e.entry_id == found.entity.config_entry_id
                ]
                if not owners:
                    raise _console_not_loaded()
                found.owner = owners[0]
            if found.entity.device_id and dev_reg is not None:
                found.device = dev_reg.async_get(found.entity.device_id)

    if found.device is None and dev_reg is not None:
        found.device = dev_reg.async_get(target_id)
    return found


def _device_entry_owner(
    found: _RegistryTarget,
    device: dr.DeviceEntry | dr.ChildDeviceEntry,
    target_id: str,
    entries: list[UnifiInsightsConfigEntry],
    *,
    protect_type: str | None = None,
) -> UnifiInsightsConfigEntry | None:
    """
    Check a registry device belongs to a loaded console, and return its owner.

    A device without a UniFi Insights identifier and no loaded owner is not
    ours; a device owned by an entry that is not loaded is refused. The entity's
    owner wins over the device's.
    """
    owner_id = _device_owner_id(device)
    owners = [e for e in entries if e.entry_id == owner_id]
    has_domain_identifier = any(ident[0] == DOMAIN for ident in device.identifiers)
    if not owners and not has_domain_identifier:
        _not_ours(target_id, "device", protect_type)
    if owner_id is not None and not owners:
        raise _console_not_loaded()
    if found.owner is None and owners:
        return owners[0]
    return found.owner


def _raise_for_mobility_target(target: str, found: _RegistryTarget) -> None:
    """
    Refuse UniFi Mobility routers as Network device or client targets.

    Their unique ids and device identifiers would otherwise parse into a router
    UUID and a made-up site or client id, and reach the Network API.
    """
    identifiers = [
        identifier
        for domain, identifier in getattr(found.device, "identifiers", None) or ()
        if domain == DOMAIN
    ]
    if found.entity is not None and found.entity.unique_id:
        identifiers.append(found.entity.unique_id)
    if any(identifier.startswith(MOBILITY_DEVICE_PREFIX) for identifier in identifiers):
        msg = (
            f"Target '{target}' is a UniFi Mobility router, "
            "which this action does not support"
        )
        raise _invalid(msg, "target_mobility_unsupported", target=target)


def _resolve_network_device_id(
    hass: HomeAssistant,
    device_id: str,
    site_id: str | None,
    entries: list[UnifiInsightsConfigEntry],
) -> tuple[str, str | None, UnifiInsightsConfigEntry | None]:
    """Resolve an HA device/entity registry ID to a native UniFi network device ID."""
    found = _lookup_registry_target(
        hass, device_id, entries, looks_like_entity="." in device_id
    )
    _raise_for_mobility_target(device_id, found)
    if found.device is None and found.entity is None:
        return device_id, site_id, None

    resolved_entry = found.owner
    ent_entry = found.entity
    if ent_entry is not None and ent_entry.unique_id:
        candidate_entries = [resolved_entry] if resolved_entry else entries
        for entry in candidate_entries:
            devices_by_site = _coord_section(entry, "devices")
            if isinstance(devices_by_site, dict):
                for s, s_devs in devices_by_site.items():
                    if isinstance(s_devs, dict):
                        for d in s_devs:
                            prefix = f"{s}_{d}"
                            if ent_entry.unique_id == prefix or (
                                ent_entry.unique_id.startswith(f"{prefix}_")
                            ):
                                return d, s, entry
        parts = ent_entry.unique_id.split("_")
        if len(parts) > 1:
            return parts[1], parts[0], resolved_entry

    if found.device is not None:
        resolved_entry = _device_entry_owner(found, found.device, device_id, entries)
        candidate_entries = [resolved_entry] if resolved_entry else entries
        for domain, ident in found.device.identifiers:
            if domain != DOMAIN or ident.startswith(("protect_", "client_")):
                continue

            for entry in candidate_entries:
                devices_by_site = _coord_section(entry, "devices")
                if isinstance(devices_by_site, dict):
                    for s, s_devs in devices_by_site.items():
                        if ident.startswith(f"{s}_"):
                            d = ident[len(s) + 1 :]
                            if isinstance(s_devs, dict) and d in s_devs:
                                return d, s, entry

            if site_id and ident.startswith(f"{site_id}_"):
                return ident[len(site_id) + 1 :], site_id, resolved_entry

            s, sep, d = ident.rpartition("_")
            if sep and s and d:
                return d, s, resolved_entry

    msg = f"Could not resolve target '{device_id}' to a UniFi network device"
    raise _invalid(msg, "network_device_unresolved", target=device_id)


def _resolve_protect_resource_id(
    hass: HomeAssistant,
    resource_type: str,
    resource_id: str,
    protect_entries: list[UnifiInsightsConfigEntry],
) -> tuple[str, UnifiInsightsConfigEntry | None]:
    """Resolve an HA device/entity registry ID to a native UniFi Protect resource ID."""
    found = _lookup_registry_target(
        hass,
        resource_id,
        protect_entries,
        looks_like_entity="." in resource_id,
        protect_type=resource_type,
    )
    if found.device is None and found.entity is None:
        return resource_id, None

    resolved_entry = found.owner
    ent_entry = found.entity
    if ent_entry is not None and ent_entry.unique_id:
        prefix = f"{DOMAIN}_{resource_type}_"
        if ent_entry.unique_id.startswith(prefix):
            remainder = ent_entry.unique_id[len(prefix) :]
            candidate_entries = [resolved_entry] if resolved_entry else protect_entries
            collection_key = _PROTECT_COLLECTIONS.get(
                resource_type, f"{resource_type}s"
            )
            for entry in candidate_entries:
                protect_data = _coord_section(entry, "protect")
                if isinstance(protect_data, dict):
                    collection = protect_data.get(collection_key)
                    if isinstance(collection, dict):
                        for k in collection:
                            if remainder == k or remainder.startswith(f"{k}_"):
                                return k, entry
            return remainder.split("_")[0], resolved_entry

    if found.device is not None:
        resolved_entry = _device_entry_owner(
            found,
            found.device,
            resource_id,
            protect_entries,
            protect_type=resource_type,
        )
        expected_prefix = f"protect_{resource_type}_"
        for domain, ident in found.device.identifiers:
            if domain != DOMAIN:
                continue
            if ident.startswith(expected_prefix):
                return ident[len(expected_prefix) :], resolved_entry
            if ident.startswith("protect_"):
                actual_type = ident.split("_")[1]
                msg = (
                    f"Target '{resource_id}' is a {actual_type}, not a {resource_type}"
                )
                raise _invalid(
                    msg,
                    "protect_resource_wrong_type",
                    target=resource_id,
                    actual_type=actual_type,
                    resource_type=resource_type,
                )

        _dev_reg, ent_reg = _registries(hass)
        if ent_reg is not None and resource_type == "camera":
            camera_prefix = f"{DOMAIN}_camera_"
            for ent in er.async_entries_for_device(ent_reg, found.device.id):
                if ent.platform == DOMAIN and ent.unique_id.startswith(camera_prefix):
                    remainder = ent.unique_id[len(camera_prefix) :]
                    return remainder.split("_")[0], resolved_entry

    msg = f"Could not resolve target '{resource_id}' to a UniFi Protect {resource_type}"
    raise _invalid(
        msg,
        "protect_resource_unresolved",
        target=resource_id,
        resource_type=resource_type,
    )


_DOTTED_MAC: Final = re.compile(r"(?:[0-9a-fA-F]{4}\.){2}[0-9a-fA-F]{4}")
_MAC_IN_UNIQUE_ID: Final = re.compile(
    r"(?:^|[^0-9a-fA-F])([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})(?:$|[^0-9a-fA-F])"
)


def _resolve_network_client_id(
    hass: HomeAssistant,
    client_id: str,
    entries: list[UnifiInsightsConfigEntry],
) -> tuple[str, UnifiInsightsConfigEntry | None]:
    """Resolve an HA device/entity registry ID to a native client ID or MAC."""
    found = _lookup_registry_target(
        hass,
        client_id,
        entries,
        looks_like_entity="." in client_id and not _DOTTED_MAC.fullmatch(client_id),
    )
    _raise_for_mobility_target(client_id, found)
    resolved_entry = found.owner
    ent_entry = found.entity
    if ent_entry is not None and ent_entry.unique_id:
        match = _MAC_IN_UNIQUE_ID.search(ent_entry.unique_id)
        if match:
            return match.group(1).replace("-", ":"), resolved_entry
        parts = ent_entry.unique_id.split("_")
        if len(parts) > 1:
            return parts[1], resolved_entry

    if found.device is not None:
        resolved_entry = _device_entry_owner(found, found.device, client_id, entries)
        for domain, ident in found.device.identifiers:
            if domain == DOMAIN and ident.startswith("client_"):
                return ident[len("client_") :], resolved_entry

    return client_id, resolved_entry


# --- Network console selection ---------------------------------------------


def _select_network_entry(
    hass: HomeAssistant,
    *,
    site_id: str | None = None,
    device_id: str | None = None,
    client_id: str | None = None,
) -> tuple[UnifiInsightsConfigEntry, str | None]:
    """Return the console owning a Network site, device or client, and its ID."""
    entries = _console_entries(hass)
    if not entries:
        msg = "No UniFi Insights coordinator found"
        raise _invalid(msg, "no_coordinator")

    resolved_reg_entry: UnifiInsightsConfigEntry | None = None
    native_device_id: str | None = None
    resolved_target_id: str | None = None

    if device_id is not None:
        native_device_id, resolved_site, resolved_reg_entry = (
            _resolve_network_device_id(hass, device_id, site_id, entries)
        )
        resolved_target_id = native_device_id
        if site_id is None and resolved_site is not None:
            site_id = resolved_site

    if client_id is not None:
        client_id, resolved_reg_entry = _resolve_network_client_id(
            hass, client_id, entries
        )
        resolved_target_id = client_id

    # 1. Filter entries by site_id
    if site_id is not None:
        matching_entries = [e for e in entries if _entry_has_site(e, site_id)]
        if not matching_entries:
            msg = f"Site '{site_id}' not found on any configured UniFi console"
            raise _invalid(msg, "site_not_found", site_id=site_id)
    else:
        matching_entries = entries

    # If resolved via registry, verify that the entry matches site_id
    if resolved_reg_entry is not None and resolved_reg_entry not in matching_entries:
        target_name = str(device_id or client_id)
        msg = (
            f"Device '{target_name}' belongs to a different console than"
            f" site '{site_id}'"
        )
        raise _invalid(
            msg, "device_other_console", device_id=target_name, site_id=str(site_id)
        )

    # 2. Filter / validate by device_id if specified
    if native_device_id is not None:
        return _select_device_entry(
            entries, matching_entries, resolved_reg_entry, site_id, native_device_id
        )

    # 3. Filter by client_id if specified
    if client_id is not None and len(matching_entries) > 1:
        if resolved_reg_entry is not None and resolved_reg_entry in matching_entries:
            return resolved_reg_entry, client_id

        entries_with_client = [
            e for e in matching_entries if _entry_has_client(e, site_id, client_id)
        ]
        if len(entries_with_client) == 1:
            return entries_with_client[0], client_id
        owning_entries = [
            e for e in matching_entries if _entry_owns_client(e, site_id, client_id)
        ]
        if len(owning_entries) == 1:
            return owning_entries[0], client_id
        if len(owning_entries) > 1:
            msg = f"Multiple consoles contain client '{client_id}'; target is ambiguous"
            raise _invalid(msg, "client_ambiguous", client_id=client_id)

    # 4. When multiple entries match site_id
    if len(matching_entries) > 1:
        explicit_matches = [
            e for e in matching_entries if site_id in (_coord_section(e, "sites") or {})
        ]
        if len(explicit_matches) > 1:
            msg = f"Multiple consoles contain site '{site_id}'; target is ambiguous"
            raise _invalid(msg, "site_ambiguous", site_id=str(site_id))
        if len(explicit_matches) == 1:
            return explicit_matches[0], resolved_target_id

        if site_id:
            msg = f"Multiple consoles found for site '{site_id}'; target is ambiguous"
            raise _invalid(msg, "site_consoles_ambiguous", site_id=site_id)
        msg = "Multiple UniFi Insights consoles are configured; target is ambiguous"
        raise _invalid(msg, "network_console_ambiguous")

    return matching_entries[0], resolved_target_id


def _select_device_entry(
    entries: list[UnifiInsightsConfigEntry],
    matching_entries: list[UnifiInsightsConfigEntry],
    resolved_reg_entry: UnifiInsightsConfigEntry | None,
    site_id: str | None,
    device_id: str,
) -> tuple[UnifiInsightsConfigEntry, str]:
    """Pick the console of a native device ID among the site's candidates."""
    entries_with_device = [
        e for e in matching_entries if _entry_has_device(e, site_id, device_id)
    ]
    if not entries_with_device:
        cross_console_entries = [
            e
            for e in entries
            if e not in matching_entries and _entry_has_device(e, None, device_id)
        ]
        if cross_console_entries and site_id:
            msg = (
                f"Device '{device_id}' belongs to a different console than"
                f" site '{site_id}'"
            )
            raise _invalid(
                msg, "device_other_console", device_id=device_id, site_id=site_id
            )
        if site_id:
            msg = f"Device '{device_id}' not found on console for site '{site_id}'"
            raise _invalid(
                msg, "device_not_on_site_console", device_id=device_id, site_id=site_id
            )
        msg = f"Device '{device_id}' not found on any configured UniFi console"
        raise _invalid(msg, "device_not_on_any_console", device_id=device_id)

    if resolved_reg_entry is not None and resolved_reg_entry in entries_with_device:
        return resolved_reg_entry, device_id

    if len(entries_with_device) > 1:
        explicit_matches = [
            e for e in entries_with_device if _entry_owns_device(e, site_id, device_id)
        ]
        if len(explicit_matches) == 1:
            return explicit_matches[0], device_id
        if site_id:
            msg = (
                f"Multiple consoles found for site '{site_id}' and device"
                f" '{device_id}'; target is ambiguous"
            )
            raise _invalid(
                msg, "device_site_ambiguous", device_id=device_id, site_id=site_id
            )
        msg = f"Multiple consoles found for device '{device_id}'; target is ambiguous"
        raise _invalid(msg, "device_ambiguous", device_id=device_id)

    return entries_with_device[0], device_id


def _entry_matches_console(entry: UnifiInsightsConfigEntry, console_id: str) -> bool:
    """Match a config entry by its entry ID or its title."""
    wanted = console_id.strip().lower()
    if str(getattr(entry, "entry_id", "")).lower() == wanted:
        return True
    return str(getattr(entry, "title", "")).strip().lower() == wanted


def _console_titles(entries: list[UnifiInsightsConfigEntry]) -> str:
    """Return the sorted titles of the entries, for an error message."""
    return ", ".join(sorted(str(getattr(e, "title", e.entry_id)) for e in entries))


def _select_console(
    entries: list[UnifiInsightsConfigEntry], console_id: str, what: str
) -> UnifiInsightsConfigEntry:
    """Pick the entry the user named, or explain why it could not be picked."""
    matches = [entry for entry in entries if _entry_matches_console(entry, console_id)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        known = _console_titles(entries)
        msg = (
            f"No configured {what} matches console '{console_id}'. Configured: {known}"
        )
        raise _invalid(msg, "console_not_found", console_id=console_id, consoles=known)
    msg = f"Console '{console_id}' matches more than one {what}; use the entry ID"
    raise _invalid(msg, "console_ambiguous", console_id=console_id)


# --- Protect console selection ---------------------------------------------


def _protect_entries(hass: HomeAssistant) -> list[UnifiInsightsConfigEntry]:
    """Return every console entry with a Protect client."""
    return [
        entry
        for entry in _console_entries(hass)
        if getattr(entry.runtime_data.coordinator, "protect_client", None) is not None
    ]


def _select_protect_entry(
    hass: HomeAssistant,
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
    secondary_resource_type: str | None = None,
    secondary_resource_id: str | None = None,
    console_id: str | None = None,
) -> tuple[UnifiInsightsConfigEntry, str | None]:
    """Return the Protect console owning a resource, and the native resource ID."""
    protect_entries = _protect_entries(hass)
    if not protect_entries:
        msg = "No UniFi Protect coordinator found"
        raise _invalid(msg, "no_protect_coordinator")

    # 0. An explicit console always wins. It skips the ownership checks below.
    if console_id:
        entry = _select_console(protect_entries, console_id, "UniFi Protect console")
        return entry, None

    if resource_type is None or resource_id is None:
        if len(protect_entries) > 1:
            known = _console_titles(protect_entries)
            msg = (
                "Multiple UniFi Protect consoles are configured and this action"
                " carries no target to route on; set 'console_id' to one of:"
                f" {known}"
            )
            raise _invalid(msg, "protect_console_required", consoles=known)
        return protect_entries[0], None

    # Resolve resource_id if it is an HA registry ID
    native_resource_id, resolved_reg_entry = _resolve_protect_resource_id(
        hass, resource_type, resource_id, protect_entries
    )
    collection_key = _PROTECT_COLLECTIONS.get(resource_type, f"{resource_type}s")
    matching_entries = [
        entry
        for entry in protect_entries
        if _protect_entry_has_resource(entry, collection_key, native_resource_id)
    ]
    placeholders = {"resource_type": resource_type, "resource_id": native_resource_id}

    if not matching_entries:
        msg = (
            f"{resource_type.capitalize()} '{native_resource_id}' not found on any"
            " configured UniFi Protect console"
        )
        raise _invalid(msg, "protect_resource_not_found", **placeholders)

    if resolved_reg_entry is not None and resolved_reg_entry not in matching_entries:
        msg = (
            f"{resource_type.capitalize()} '{resource_id}' belongs to a different"
            " console than the target console"
        )
        raise _invalid(
            msg,
            "protect_resource_other_console",
            resource_type=resource_type,
            resource_id=resource_id,
        )

    if len(matching_entries) > 1:
        if resolved_reg_entry is not None and resolved_reg_entry in matching_entries:
            matching_entries = [resolved_reg_entry]
        else:
            explicit_matches = [
                entry
                for entry in matching_entries
                if _protect_owns_resource(entry, collection_key, native_resource_id)
                is True
            ]
            if len(explicit_matches) > 1:
                msg = (
                    f"Multiple Protect consoles contain {resource_type}"
                    f" '{native_resource_id}'; target is ambiguous"
                )
                raise _invalid(msg, "protect_resource_ambiguous", **placeholders)
            if not explicit_matches:
                msg = (
                    f"Cannot tell which Protect console owns {resource_type}"
                    f" '{native_resource_id}' yet;"
                    " retry once the consoles have refreshed"
                )
                raise _invalid(msg, "protect_resource_owner_unknown", **placeholders)
            matching_entries = explicit_matches

    target_entry = matching_entries[0]
    if secondary_resource_type and secondary_resource_id:
        _check_protect_secondary(
            hass,
            protect_entries,
            target_entry,
            (resource_type, resource_id),
            (secondary_resource_type, secondary_resource_id),
        )
    return target_entry, native_resource_id


def _check_protect_secondary(
    hass: HomeAssistant,
    protect_entries: list[UnifiInsightsConfigEntry],
    target_entry: UnifiInsightsConfigEntry,
    primary: tuple[str, str],
    secondary: tuple[str, str],
) -> None:
    """
    Check a validation-only secondary resource is on the primary's console.

    The secondary (a chime's camera, a viewer's liveview) is never passed to the
    command; it only has to belong to the same console. A secondary the console
    has not cached is allowed unless another console positively owns it.
    """
    resource_type, resource_id = primary
    secondary_type, secondary_id = secondary
    msg = (
        f"{secondary_type.capitalize()} '{secondary_id}'"
        " belongs to a different Protect console than"
        f" {resource_type} '{resource_id}'"
    )
    other_console = _invalid(
        msg,
        "protect_secondary_other_console",
        secondary_type=secondary_type,
        secondary_id=secondary_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )

    native_sec_id = secondary_id
    if secondary_type in _PROTECT_COLLECTIONS:
        native_sec_id, secondary_entry = _resolve_protect_resource_id(
            hass, secondary_type, secondary_id, protect_entries
        )
        if secondary_entry is not None and secondary_entry != target_entry:
            raise other_console

    sec_collection_key = {"camera": "cameras", "liveview": "liveviews"}.get(
        secondary_type, f"{secondary_type}s"
    )
    if _protect_entry_has_resource(target_entry, sec_collection_key, native_sec_id):
        return
    if any(
        entry != target_entry
        and _protect_owns_resource(entry, sec_collection_key, native_sec_id) is True
        for entry in protect_entries
    ):
        raise other_console
    _LOGGER.debug(
        "%s '%s' is not in cached Protect data; proceeding on the console"
        " owning %s '%s'",
        secondary_type.capitalize(),
        secondary_id,
        resource_type,
        resource_id,
    )


# --- Chime doorbell pairing -------------------------------------------------

# The doorbell ring binary sensor (binary_sensor.py, key "camera_doorbell_ring")
# is this integration's only doorbell marker: the public camera schema has no
# doorbell flag. Its unique_id is f"{DOMAIN}_camera_{camera_id}_camera_doorbell_ring".
_DOORBELL_RING_UNIQUE_ID_PREFIX: Final = f"{DOMAIN}_camera_"
_DOORBELL_RING_UNIQUE_ID_SUFFIX: Final = "_camera_doorbell_ring"


def _doorbell_camera_id(entry: er.RegistryEntry) -> str | None:
    """Return the camera behind this integration's doorbell ring sensor, else None."""
    unique_id = entry.unique_id
    if (
        entry.platform != DOMAIN
        or entry.domain != "binary_sensor"
        or not unique_id.startswith(_DOORBELL_RING_UNIQUE_ID_PREFIX)
        or not unique_id.endswith(_DOORBELL_RING_UNIQUE_ID_SUFFIX)
    ):
        return None
    camera_id = unique_id[
        len(_DOORBELL_RING_UNIQUE_ID_PREFIX) : -len(_DOORBELL_RING_UNIQUE_ID_SUFFIX)
    ]
    return camera_id or None


def _raise_chime_doorbell_error(key: str, target: str | None = None) -> NoReturn:
    """Raise a translated validation error for the chime pairing action."""
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={"target": target} if target else None,
    )


def _resolve_chime_doorbell_camera_ids(
    hass: HomeAssistant,
    doorbells: dict[str, Any] | None,
    *,
    chime_entry_id: str,
    cache: UnifiInsightsFacadeCoordinator,
) -> list[str]:
    """
    Turn a ``doorbells`` target selection into the camera ids to pair.

    Home Assistant's own UniFi Protect integration quietly ignores anything
    that is not a doorbell. This does so only for entities that came in
    through a device, area, floor or label, because a doorbell device has many
    entities besides the ring sensor. An entity the user named directly must be
    a doorbell ring sensor, and a selection that holds no doorbell at all is
    refused instead of unpairing every doorbell. An absent or empty selection
    still unpairs them all, as in core.

    Every doorbell must belong to the chime's console, and its camera must
    still be known to that console. The ring sensor alone is not proof of a
    doorbell: it is also created for a camera whose name merely looks like one
    (for example "Front Door"), so the camera's cached record must say by its
    type that it is a doorbell. Any other camera is handled like any other
    entity that is not a doorbell.
    """
    selection = TargetSelection(doorbells or {})
    if not selection.has_any_target:
        return []

    selected = async_extract_referenced_entity_ids(hass, selection)
    missing = sorted(
        selected.missing_devices
        | selected.missing_areas
        | selected.missing_floors
        | selected.missing_labels
    )
    if missing:
        _raise_chime_doorbell_error("chime_doorbell_not_found", missing[0])

    # Routing keeps a console that has not loaded its Protect data yet as a
    # candidate, so the cached cameras may not be there.
    data = cache.data
    protect_data = data.get("protect") if isinstance(data, dict) else None
    known_cameras = (
        protect_data.get("cameras") if isinstance(protect_data, dict) else None
    )
    if not isinstance(known_cameras, dict):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="chime_cameras_unavailable",
        )

    entity_registry = er.async_get(hass)
    camera_ids: set[str] = set()
    for entity_id in sorted(selected.referenced | selected.indirectly_referenced):
        entry = entity_registry.async_get(entity_id)
        camera_id = None if entry is None else _doorbell_camera_id(entry)
        if entry is None or camera_id is None:
            if entity_id in selected.referenced:
                _raise_chime_doorbell_error(
                    "chime_doorbell_not_found"
                    if entry is None
                    else "chime_doorbell_not_doorbell",
                    entity_id,
                )
            continue
        if entry.config_entry_id != chime_entry_id:
            _raise_chime_doorbell_error("chime_doorbell_other_console", entity_id)
        camera = known_cameras.get(camera_id)
        if camera is None:
            # An entity left behind by a camera the console no longer has.
            _raise_chime_doorbell_error("chime_doorbell_not_found", entity_id)
        if not is_doorbell_camera_model(camera):
            if entity_id in selected.referenced:
                _raise_chime_doorbell_error("chime_doorbell_not_doorbell", entity_id)
            continue
        camera_ids.add(camera_id)

    if not camera_ids:
        _raise_chime_doorbell_error("chime_doorbells_none_selected")
    return sorted(camera_ids)


# --- Carrier Fabric ---------------------------------------------------------

_CARRIER_SUBSCRIBER_PREFIX: Final = "carrier_subscriber_"


def _extract_carrier_targets(call: ServiceCall) -> list[str]:
    """
    Extract all target entity or device IDs from a service call.

    Subscribers are devices, so area targets are not supported and are
    ignored wherever they appear.
    """
    targets: list[str] = []

    def _add(val: Any) -> None:
        if isinstance(val, list):
            targets.extend(
                item.strip() for item in val if isinstance(item, str) and item.strip()
            )
        elif isinstance(val, str) and val.strip():
            targets.append(val.strip())

    if "target" in call.data and isinstance(call.data["target"], dict):
        td = call.data["target"]
        _add(td.get("entity_id"))
        _add(td.get("device_id"))

    _add(call.data.get("entity_id"))
    _add(call.data.get("device_id"))

    deduped: list[str] = []
    for t in targets:
        if t not in deduped:
            deduped.append(t)
    return deduped


def _carrier_device_by_identifier(
    hass: HomeAssistant, dev_reg: dr.DeviceRegistry, target_id: str
) -> dr.DeviceEntry | None:
    """
    Find the device carrying a subscriber identifier, one config entry at a time.

    Identifiers are only unique within a config entry, so each entry's devices
    are searched on their own: first for the identifier as given, then for the
    ``carrier_subscriber_`` form of a bare subscriber ID. An identifier that more
    than one entry has is ambiguous and must be targeted by entity or device.
    """
    candidates = [(DOMAIN, target_id)]
    if not target_id.startswith(_CARRIER_SUBSCRIBER_PREFIX):
        candidates.append((DOMAIN, f"{_CARRIER_SUBSCRIBER_PREFIX}{target_id}"))
    for identifier in candidates:
        matches = [
            device
            for entry in hass.config_entries.async_entries(DOMAIN)
            for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
            if identifier in device.identifiers
        ]
        if len(matches) > 1:
            msg = (
                f"Target '{target_id}' matches a device in more than one"
                " config entry; target the subscriber's entity or device instead"
            )
            raise _invalid(msg, "carrier_target_ambiguous", target=target_id)
        if matches:
            return matches[0]
    return None


def _carrier_subscriber_id(device: dr.DeviceEntry | dr.ChildDeviceEntry) -> str | None:
    """Return the subscriber ID in a device's identifiers, if it has one."""
    for domain, ident in device.identifiers:
        if domain == DOMAIN and ident.startswith(_CARRIER_SUBSCRIBER_PREFIX):
            return ident[len(_CARRIER_SUBSCRIBER_PREFIX) :]
    return None


def resolve_carrier_subscriber_call(
    hass: HomeAssistant, call: ServiceCall
) -> CarrierSubscriberTarget:
    """Resolve a Carrier Fabric subscriber action to its entry and subscriber."""
    from . import CarrierFabricData  # noqa: PLC0415

    targets = _extract_carrier_targets(call)
    if not targets:
        msg = "At least one target must be specified"
        raise _invalid(msg, "carrier_target_required")
    if len(targets) > 1:
        msg = (
            f"Multiple targets specified for {call.service}; "
            "action only supports a single target"
        )
        raise _invalid(msg, "carrier_multiple_targets", service=call.service)

    target_id = targets[0]
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    dev_entry: dr.DeviceEntry | dr.ChildDeviceEntry | None = None

    if "." in target_id:
        ent_entry = ent_reg.async_get(target_id)
        if ent_entry is None:
            msg = f"Target entity '{target_id}' not found in entity registry"
            raise _invalid(msg, "carrier_target_not_found", target=target_id)
        if ent_entry.platform != DOMAIN:
            msg = f"Target '{target_id}' is not a UniFi Insights entity"
            raise _invalid(msg, "carrier_target_not_subscriber", target=target_id)
        if ent_entry.device_id:
            dev_entry = dev_reg.async_get(ent_entry.device_id)
    else:
        dev_entry = dev_reg.async_get(target_id) or _carrier_device_by_identifier(
            hass, dev_reg, target_id
        )

    if dev_entry is None:
        msg = f"Target '{target_id}' not found in device registry"
        raise _invalid(msg, "carrier_target_not_found", target=target_id)

    if not any(ident[0] == DOMAIN for ident in dev_entry.identifiers):
        msg = f"Target '{target_id}' is not a UniFi Insights device"
        raise _invalid(msg, "carrier_target_not_subscriber", target=target_id)

    owner_id = _device_owner_id(dev_entry)
    matching_entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id == owner_id
    ]
    if not matching_entries:
        msg = "Target config entry is not loaded"
        raise _invalid(msg, "carrier_entry_not_loaded")

    target_entry = matching_entries[0]
    if target_entry.data.get(CONF_CONNECTION_TYPE) != CONNECTION_TYPE_CARRIER_FABRIC:
        msg = f"Target '{target_id}' is not a Carrier Fabric subscriber"
        raise _invalid(msg, "carrier_target_not_subscriber", target=target_id)

    runtime_data = getattr(target_entry, "runtime_data", None)
    if target_entry.state is not ConfigEntryState.LOADED or not isinstance(
        runtime_data, CarrierFabricData
    ):
        msg = "Carrier Fabric integration entry is not loaded"
        raise _invalid(msg, "carrier_entry_not_loaded")

    subscriber_id = _carrier_subscriber_id(dev_entry)
    if not subscriber_id:
        msg = f"Target '{target_id}' is not a Carrier Fabric subscriber"
        raise _invalid(msg, "carrier_target_not_subscriber", target=target_id)

    try:
        uuid.UUID(subscriber_id)
    except ValueError as err:
        msg = f"Invalid subscriber ID format '{subscriber_id}' (valid UUID expected)"
        raise _invalid(
            msg, "carrier_invalid_subscriber_id", target=subscriber_id
        ) from err

    if not target_entry.options.get(CONF_CARRIER_ACTIONS, DEFAULT_CARRIER_ACTIONS):
        msg = (
            "Carrier Fabric actions are disabled. Enable 'Enable service actions' "
            "in the integration options to perform this action."
        )
        raise HomeAssistantError(
            msg,
            translation_domain=DOMAIN,
            translation_key="carrier_actions_disabled",
        )

    return CarrierSubscriberTarget(
        entry=cast("CarrierFabricConfigEntry", target_entry),
        carrier_commands=runtime_data.coordinator,
        subscriber_id=subscriber_id,
    )


# --- Call-level resolvers ---------------------------------------------------


def resolve_network_device_call(
    hass: HomeAssistant,
    call: ServiceCall,
    *,
    action: Literal["restart", "power_cycle"],
) -> NetworkDeviceTarget:
    """
    Resolve a restart or power-cycle call to its device, console and site.

    The site is the call's ``site_id`` when set, else the first cached site of
    the selected console that holds the device.
    """
    site_id = call.data.get("site_id")
    raw_device_id = _require_target(call, "device_id", "device")
    entry, resolved_id = _select_network_entry(
        hass, site_id=site_id, device_id=raw_device_id
    )
    cache = entry.runtime_data.coordinator
    device_id = str(resolved_id)

    if not site_id and isinstance(cache.data, dict):
        devices_by_site = cache.data.get("devices")
        if isinstance(devices_by_site, dict):
            site_id = next(
                (
                    s
                    for s, s_devs in devices_by_site.items()
                    if isinstance(s_devs, dict) and device_id in s_devs
                ),
                None,
            )

    if not site_id:
        if action == "restart":
            msg = f"Site ID is required to restart device '{device_id}'"
        else:
            msg = f"Site ID is required to power-cycle port on device '{device_id}'"
        raise _invalid(
            msg,
            (
                "restart_site_required"
                if action == "restart"
                else "power_cycle_site_required"
            ),
            device_id=device_id,
        )

    return NetworkDeviceTarget(
        entry=entry,
        cache=cache,
        device_commands=cache.device_coordinator,
        device_id=device_id,
        site_id=site_id,
    )


def resolve_network_client_call(
    hass: HomeAssistant, call: ServiceCall
) -> NetworkClientTarget:
    """
    Resolve a reconnect or guest authorization call to its client and console.

    Trackers identify clients by MAC; the Integration API needs the native
    client ID from the selected site's cached client records.
    """
    site_id: str = call.data["site_id"]
    raw_client_id = _require_target(call, "client_id", "client")
    entry, resolved_id = _select_network_entry(
        hass, site_id=site_id, client_id=raw_client_id
    )
    cache = entry.runtime_data.coordinator
    return NetworkClientTarget(
        entry=entry,
        cache=cache,
        device_commands=cache.device_coordinator,
        config_commands=cache.config_coordinator,
        client_id=_resolve_native_client_id(cache, site_id, str(resolved_id)),
        site_id=site_id,
    )


def resolve_network_site_call(
    hass: HomeAssistant, call: ServiceCall
) -> NetworkSiteTarget:
    """Resolve a call's required ``site_id`` to the console that owns it."""
    site_id: str = call.data["site_id"]
    entry, _ = _select_network_entry(hass, site_id=site_id)
    cache = entry.runtime_data.coordinator
    return NetworkSiteTarget(
        entry=entry, cache=cache, facade_commands=cache, site_id=site_id
    )


def resolve_network_consoles_call(
    hass: HomeAssistant, call: ServiceCall
) -> tuple[NetworkConsoleTarget, ...]:
    """
    Return every console with Network, each with a snapshot of its cached sites.

    A console without the ``is_network_usable`` flag counts as usable. A usable
    console without cached sites is still returned, with no sites.
    """
    _ = call
    targets: list[NetworkConsoleTarget] = []
    for entry in _console_entries(hass):
        cache = entry.runtime_data.coordinator
        if not getattr(cache, "is_network_usable", True):
            continue
        sites = (cache.data or {}).get("sites") or {}
        targets.append(
            NetworkConsoleTarget(
                title=entry.title,
                entry=entry,
                facade_commands=cache,
                device_commands=cache.device_coordinator,
                site_ids=tuple(sites.keys()),
            )
        )
    if not targets:
        msg = "No UniFi Insights console has Network available"
        raise _invalid(msg, "no_network_console")
    return tuple(targets)


def resolve_refresh_call(hass: HomeAssistant, call: ServiceCall) -> RefreshSelection:
    """
    Return the consoles a ``refresh_data`` call refreshes.

    With a site, only the consoles whose cached sites hold it; with a blank or
    no site, every console. Only a call without ``site_id`` includes Protect.
    """
    site_id = call.data.get("site_id")
    entries = _console_entries(hass)
    if not entries:
        _LOGGER.error("No UniFi Insights coordinators found")
        msg = "No UniFi Insights coordinators found"
        raise _invalid(msg, "no_coordinator")

    targets: list[ConsoleRefreshTarget] = []
    for entry in entries:
        cache = entry.runtime_data.coordinator
        sites = (cache.data or {}).get("sites") or {}
        if site_id and site_id not in sites:
            _LOGGER.debug("Skipping coordinator - site %s not found", site_id)
            continue
        targets.append(
            ConsoleRefreshTarget(title=entry.title, entry=entry, facade_commands=cache)
        )

    if site_id and not targets:
        msg = f"No UniFi Insights console is configured for site '{site_id}'"
        raise _invalid(msg, "refresh_site_not_found", site_id=site_id)
    return RefreshSelection(
        targets=tuple(targets), site_id=site_id, include_protect=site_id is None
    )


def _protect_secondary(
    call: ServiceCall, resource_type: ProtectResourceType
) -> tuple[str | None, str | None]:
    """
    Return a call's validation-only secondary Protect resource.

    A chime's optional ``camera_id`` (first list item, trimmed) and a viewer's
    ``liveview_id`` only have to be on the same console; neither is read from
    the call's target.
    """
    if resource_type == "viewer":
        return "liveview", call.data.get("liveview_id")
    if resource_type != "chime":
        return None, None
    raw_camera_id = call.data.get("camera_id")
    if isinstance(raw_camera_id, list):
        raw_camera_id = raw_camera_id[0] if raw_camera_id else None
    if isinstance(raw_camera_id, str):
        raw_camera_id = raw_camera_id.strip() or None
    return ("camera" if raw_camera_id else None), raw_camera_id


def resolve_protect_call(
    hass: HomeAssistant, call: ServiceCall, resource_type: ProtectResourceType
) -> ProtectTarget:
    """
    Resolve a Protect camera, light, chime or viewer call to its console.

    The target is read from ``<resource_type>_id`` or the call's target. A
    chime's ``camera_id`` and a viewer's ``liveview_id`` are checked to be on
    the same console, but are not part of the result.
    """
    raw_id = _require_target(call, f"{resource_type}_id", resource_type)
    secondary_type, secondary_id = _protect_secondary(call, resource_type)
    entry, resource_id = _select_protect_entry(
        hass,
        resource_type=resource_type,
        resource_id=raw_id,
        secondary_resource_type=secondary_type,
        secondary_resource_id=secondary_id,
    )
    if secondary_type == "camera":
        _LOGGER.debug(
            "camera_id %s selects the console owning chime %s; the setting"
            " applies to the whole chime",
            secondary_id,
            resource_id,
        )
    cache = entry.runtime_data.coordinator
    return ProtectTarget(
        entry=entry,
        cache=cache,
        protect_commands=cache.require_protect_coordinator(),
        resource_id=str(resource_id),
    )


def resolve_protect_console_call(
    hass: HomeAssistant, call: ServiceCall
) -> ProtectConsoleTarget:
    """
    Resolve a Protect call that names no resource to its console.

    ``console_id`` (entry title or entry ID) picks the console; without it
    there must be exactly one Protect console.
    """
    entry, _ = _select_protect_entry(hass, console_id=call.data.get("console_id"))
    cache = entry.runtime_data.coordinator
    return ProtectConsoleTarget(
        entry=entry, cache=cache, protect_commands=cache.require_protect_coordinator()
    )


def resolve_chime_pairing_call(
    hass: HomeAssistant, call: ServiceCall
) -> ChimePairingTarget:
    """
    Resolve a chime pairing call to the chime and its doorbell camera IDs.

    Every doorbell is checked before the result is returned, so an invalid or
    foreign doorbell fails the whole call before any command runs.
    """
    raw_chime_id = _require_target(call, "chime_id", "chime")
    entry, chime_id = _select_protect_entry(
        hass, resource_type="chime", resource_id=raw_chime_id
    )
    cache = entry.runtime_data.coordinator
    camera_ids = _resolve_chime_doorbell_camera_ids(
        hass, call.data.get("doorbells"), chime_entry_id=entry.entry_id, cache=cache
    )
    return ChimePairingTarget(
        entry=entry,
        cache=cache,
        protect_commands=cache.require_protect_coordinator(),
        chime_id=str(chime_id),
        camera_ids=tuple(camera_ids),
    )


def resolve_rule_export_call(
    hass: HomeAssistant, call: ServiceCall
) -> RuleExportTarget:
    """
    Resolve a network rule export to the console it reads from.

    ``console_id`` (entry title or entry ID) names the console directly. Without
    it the console is found from ``site_id``, or is the only configured one.
    """
    site_id: str | None = call.data.get("site_id")
    console_id: str | None = call.data.get("console_id")
    entries = _console_entries(hass)
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_network_console"
        )
    if console_id:
        matches = [e for e in entries if _entry_matches_console(e, console_id)]
        if len(matches) != 1:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key=(
                    "export_target_ambiguous" if matches else "export_target_not_found"
                ),
                translation_placeholders={"target": console_id},
            )
        entry = matches[0]
        if site_id and not _entry_has_site(entry, site_id):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="export_site_not_on_console",
                translation_placeholders={"site_id": site_id, "console": console_id},
            )
    else:
        if site_id is None and len(entries) > 1:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="export_console_required"
            )
        if site_id is not None and not any(
            _entry_has_site(e, site_id) for e in entries
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="export_target_not_found",
                translation_placeholders={"target": site_id},
            )
        try:
            entry, _ = _select_network_entry(hass, site_id=site_id)
        except ServiceValidationError as err:
            # The site exists, so what is left is "more than one console has
            # this site".
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="export_target_ambiguous",
                translation_placeholders={"target": str(site_id)},
            ) from err
    cache = entry.runtime_data.coordinator
    return RuleExportTarget(
        entry=entry, cache=cache, export_commands=cache, site_id=site_id
    )


def _access_coordinator_of(
    entry: ConfigEntry[Any],
) -> UnifiInsightsAccessCoordinator | None:
    """Return an entry's Access coordinator, or None when Access is not loaded."""
    runtime_data = getattr(entry, "runtime_data", None)
    return cast(
        "UnifiInsightsAccessCoordinator | None",
        getattr(runtime_data, "access_coordinator", None),
    )


def resolve_access_call(hass: HomeAssistant, call: ServiceCall) -> AccessTarget:
    """Route an Access user action to the one console that owns it."""
    console_id: str | None = call.data.get("console_id")
    entries = [
        cast("UnifiInsightsConfigEntry", entry)
        for entry in hass.config_entries.async_entries(DOMAIN)
        if _access_coordinator_of(entry) is not None
    ]
    if not entries:
        msg = "No UniFi Access console is loaded"
        raise _invalid(msg, "access_not_configured")
    if console_id:
        entry = _select_console(entries, console_id, "UniFi Access console")
    elif len(entries) > 1:
        msg = "Multiple UniFi Access consoles are loaded; set 'console_id'"
        raise _invalid(msg, "access_console_required")
    else:
        entry = entries[0]
    return AccessTarget(
        entry=entry,
        access_commands=cast(
            "UnifiInsightsAccessCoordinator", _access_coordinator_of(entry)
        ),
    )
