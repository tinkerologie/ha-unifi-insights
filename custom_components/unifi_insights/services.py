# Copyright 2026 UniFi Insights contributors
"""Services for the UniFi Insights integration."""

from __future__ import annotations

import logging
import math
import re
import uuid
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Final, NoReturn

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiError,
    UniFiNotFoundError,
    UniFiResponseError,
)
from unifi_official_api.network.models.client import ClientType

from .button import get_device_port, port_can_be_power_cycled
from .carrier_fabric_data import CarrierFabricData
from .const import (
    CONF_CARRIER_ACTIONS,
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_CARRIER_FABRIC,
    DEFAULT_CARRIER_ACTIONS,
    DEFAULT_VOUCHER_NAME,
    DOMAIN,
    HDR_MODE_AUTO,
    HDR_MODE_OFF,
    HDR_MODE_ON,
    LIGHT_MODE_ALWAYS,
    LIGHT_MODE_MOTION,
    LIGHT_MODE_OFF,
    MOBILITY_DEVICE_PREFIX,
    SERVICE_AUTHORIZE_GUEST,
    SERVICE_CARRIER_RESUME_SUBSCRIBER,
    SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
    SERVICE_CREATE_LIVEVIEW,
    SERVICE_DELETE_VOUCHER,
    SERVICE_EXPORT_NETWORK_RULES,
    SERVICE_GENERATE_VOUCHER,
    SERVICE_PLAY_CHIME_RINGTONE,
    SERVICE_POWER_CYCLE_PORT,
    SERVICE_PTZ_MOVE,
    SERVICE_PTZ_PATROL,
    SERVICE_RECONNECT_CLIENT,
    SERVICE_REMOVE_CLIENTS,
    SERVICE_SET_CHIME_PAIRED_DOORBELLS,
    SERVICE_SET_CHIME_REPEAT_TIMES,
    SERVICE_SET_CHIME_RINGTONE,
    SERVICE_SET_CHIME_VOLUME,
    SERVICE_SET_HDR_MODE,
    SERVICE_SET_LIGHT_LEVEL,
    SERVICE_SET_LIGHT_MODE,
    SERVICE_SET_LIVEVIEW,
    SERVICE_SET_MIC_VOLUME,
    SERVICE_SET_VIDEO_MODE,
    SERVICE_TRIGGER_ALARM,
    VIDEO_MODE_DEFAULT,
    VIDEO_MODE_HIGH_FPS,
    VIDEO_MODE_SLOW_SHUTTER,
    VIDEO_MODE_SPORT,
)
from .coordinators.carrier_fabric import InvalidSubscriberIdError
from .coordinators.rule_audit import RULE_TYPES
from .diagnostics import redact_network_rule_export
from .helpers import is_doorbell_camera_model
from .led_level import percent_to_led_level

# Schemas are typed against probatio, the library HA 2026.10 annotates its schema
# arguments with. Runtime still imports voluptuous: HA 2026.10 aliases it to probatio,
# and the 2026.6 minimum ships real voluptuous, so only the type checker sees probatio.
if TYPE_CHECKING:
    import probatio as vol
else:
    import voluptuous as vol

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse


_LOGGER = logging.getLogger(__name__)


def _get_coordinators(hass: HomeAssistant) -> list[Any]:
    """Get all UniFi Insights coordinators from config entries."""
    return [coordinator for _, coordinator in _get_titled_coordinators(hass)]


def _get_titled_coordinators(hass: HomeAssistant) -> list[tuple[str, Any]]:
    """
    Get every coordinator paired with its console's config entry title.

    ``refresh_data`` attempts every configured console and reports the
    failures together, so the message has to name the console that failed -
    with two consoles configured, "devices coordinator: timeout" on its own
    does not tell the user which one to go and look at.
    """
    return [
        (entry.title, entry.runtime_data.coordinator)
        for entry in hass.config_entries.async_entries(DOMAIN)
        if hasattr(entry, "runtime_data")
        and entry.runtime_data
        and not isinstance(entry.runtime_data, CarrierFabricData)
    ]


def _coord_data(entry: Any) -> dict[str, Any] | None:
    """Extract coordinator data dictionary if available and valid."""
    runtime_data = getattr(entry, "runtime_data", None)
    if isinstance(runtime_data, CarrierFabricData):
        return None
    coord = getattr(runtime_data, "coordinator", None) if runtime_data else None
    if coord and hasattr(coord, "data") and isinstance(coord.data, dict):
        return coord.data
    return None


def _entry_has_site(entry: Any, site_id: str) -> bool:
    """Check if config entry owns the given site ID."""
    data = _coord_data(entry)
    if data is None:
        return True
    sites = data.get("sites")
    if not isinstance(sites, dict):
        return True
    return site_id in sites


def _entry_has_device(entry: Any, site_id: str | None, device_id: str) -> bool:
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


def _entry_owns_device(entry: Any, site_id: str | None, device_id: str) -> bool:
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


def _entry_owns_client(entry: Any, site_id: str | None, client_id: str) -> bool:
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


def _entry_has_client(entry: Any, site_id: str | None, client_id: str) -> bool:
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


def _raise_for_mobility_target(
    target: str, ent_entry: er.RegistryEntry | None, dev_entry: Any
) -> None:
    """
    Refuse UniFi Mobility routers as Network device or client targets.

    Their unique ids and device identifiers would otherwise parse into a router
    UUID and a made-up site or client id, and reach the Network API.
    """
    identifiers = [
        identifier
        for domain, identifier in getattr(dev_entry, "identifiers", None) or ()
        if domain == DOMAIN
    ]
    if ent_entry is not None and ent_entry.unique_id:
        identifiers.append(ent_entry.unique_id)
    if any(identifier.startswith(MOBILITY_DEVICE_PREFIX) for identifier in identifiers):
        msg = (
            f"Target '{target}' is a UniFi Mobility router, "
            "which this action does not support"
        )
        raise ServiceValidationError(msg)


def _coord_section(entry: Any, key: str) -> dict[str, Any] | None:
    """Return a top-level dict section of the entry's coordinator data."""
    data = _coord_data(entry)
    if data is None:
        return None
    section = data.get(key)
    return section if isinstance(section, dict) else None


def _mac_key(value: Any) -> str | None:
    """
    Reduce a MAC to a separator-free lowercase key, or None if it is not one.

    ``DeviceCoordinator._normalize_mac`` only strips and lowercases, because it
    builds lookup keys from raw API values. Here the input is whatever a user
    typed in an action call, so ``aa:bb``, ``AA-BB`` and ``aabb`` must all
    compare equal.
    """
    if not isinstance(value, str):
        return None
    key = value.strip().lower().replace(":", "").replace("-", "").replace(".", "")
    return key or None


def _resolve_native_client_id(
    coordinator: Any, site_id: str, client_id: str | None
) -> str | None:
    """Resolve a target client ID or MAC to the native cached client ID."""
    data = coordinator.data
    if isinstance(data, dict):
        clients = data.get("clients", {})
        site_clients = clients.get(site_id, {}) if isinstance(clients, dict) else {}
        if (
            isinstance(site_clients, dict)
            and client_id is not None
            and client_id not in site_clients
        ):
            for native_id, record in site_clients.items():
                if _client_records_match({native_id: record}, client_id):
                    return str(native_id)
    return client_id


CLIENT_REMOVAL_MAX_SEEN_SECONDS: Final = 900


def _is_removal_timestamp(value: Any) -> bool:
    """Return True if a first_seen/last_seen value is missing or a finite number."""
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        # An int too large for a float can't be compared with a float time.
        return False


def _filter_clients_for_removal(
    clients: list[dict[str, Any]],
) -> list[str]:
    """
    Select historical client MACs eligible for removal.

    A client is eligible if:
    - It has a non-empty MAC address.
    - None of fixed_ip, hostname, or name is truthy.
    - If both first_seen and last_seen timestamps are present,
      last_seen - first_seen <= 900 seconds. If either timestamp
      is missing, the age check does not exclude the client.

    A client with a timestamp that is present but not a finite number is
    kept, so a record the filter can't read is never forgotten.
    """
    eligible_macs: list[str] = []
    seen_macs: set[str] = set()

    for client in clients:
        raw_mac = (
            client.get("mac") or client.get("macAddress") or client.get("mac_address")
        )
        if not raw_mac or not isinstance(raw_mac, str) or not raw_mac.strip():
            continue

        if any((client.get("fixed_ip"), client.get("hostname"), client.get("name"))):
            continue

        first_seen = client.get("first_seen")
        last_seen = client.get("last_seen")
        if not (_is_removal_timestamp(first_seen) and _is_removal_timestamp(last_seen)):
            continue
        if (
            first_seen
            and last_seen
            and last_seen - first_seen > CLIENT_REMOVAL_MAX_SEEN_SECONDS
        ):
            continue

        mac = raw_mac.strip().lower()
        if mac not in seen_macs:
            seen_macs.add(mac)
            eligible_macs.append(mac)

    return eligible_macs


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


def _protect_owns_resource(
    entry: Any, collection_key: str, resource_id: str
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
    entry: Any, collection_key: str, resource_id: str
) -> bool:
    """
    Check if a Protect config entry may own the given Protect resource ID.

    An entry that has not loaded the relevant data yet stays a candidate, so a
    console that is still warming up never silently loses a service call.
    """
    owns = _protect_owns_resource(entry, collection_key, resource_id)
    return True if owns is None else owns


def _extract_target_id(call: ServiceCall, primary_field: str) -> str | None:
    """Extract target resource ID from service call data, supporting targets."""
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
            raise ServiceValidationError(msg)
        if len(val) > 1:
            msg = (
                f"Multiple targets specified for {call.service}; "
                "action only supports a single target"
            )
            raise ServiceValidationError(msg)
        val = val[0]
    if isinstance(val, str):
        val = val.strip()
        return val or None
    return None


def _resolve_network_device_id(
    hass: HomeAssistant,
    device_id: str,
    site_id: str | None,
    entries: list[Any],
) -> tuple[str, str | None, Any | None]:
    """Resolve an HA device/entity registry ID to a native UniFi network device ID."""
    dev_reg = (
        dr.async_get(hass)
        if hasattr(hass, "data")
        and isinstance(hass.data, dict)
        and dr.DATA_REGISTRY in hass.data
        else None
    )
    ent_reg = (
        er.async_get(hass)
        if hasattr(hass, "data")
        and isinstance(hass.data, dict)
        and er.DATA_REGISTRY in hass.data
        else None
    )

    if "." in device_id and (ent_reg is None or ent_reg.async_get(device_id) is None):
        msg = f"Target entity '{device_id}' not found in entity registry"
        raise ServiceValidationError(msg)

    dev_entry: Any = None
    resolved_entry: Any | None = None
    ent_entry: er.RegistryEntry | None = None

    if ent_reg is not None:
        ent_entry = ent_reg.async_get(device_id)
        if ent_entry is not None:
            if ent_entry.platform != DOMAIN:
                msg = f"Target '{device_id}' is not a UniFi Insights entity"
                raise ServiceValidationError(msg)
            if ent_entry.config_entry_id:
                matching_e = [
                    e for e in entries if e.entry_id == ent_entry.config_entry_id
                ]
                if not matching_e:
                    msg = "Target's UniFi console is not loaded"
                    raise ServiceValidationError(msg)
                resolved_entry = matching_e[0]
            if ent_entry.device_id and dev_reg is not None:
                dev_entry = dev_reg.async_get(ent_entry.device_id)

    if dev_entry is None and dev_reg is not None:
        dev_entry = dev_reg.async_get(device_id)
    _raise_for_mobility_target(device_id, ent_entry, dev_entry)

    if dev_entry is not None or ent_entry is not None:
        if ent_entry is not None and ent_entry.unique_id:
            candidate_entries = [resolved_entry] if resolved_entry else entries
            for entry in candidate_entries:
                devices_by_site = _coord_section(entry, "devices")
                if isinstance(devices_by_site, dict):
                    for s, s_devs in devices_by_site.items():
                        if isinstance(s_devs, dict):
                            for d in s_devs:
                                prefix = f"{s}_{d}"
                                if (
                                    ent_entry.unique_id == prefix
                                    or ent_entry.unique_id.startswith(f"{prefix}_")
                                ):
                                    return d, s, entry
            parts = ent_entry.unique_id.split("_")
            if len(parts) > 1:
                return parts[1], parts[0], resolved_entry

        if dev_entry is not None:
            matching_config_entries = [
                e for e in entries if e.entry_id in dev_entry.config_entries
            ]
            has_domain_identifier = any(
                ident[0] == DOMAIN for ident in dev_entry.identifiers
            )
            if not matching_config_entries and not has_domain_identifier:
                msg = f"Target '{device_id}' is not a UniFi Insights device"
                raise ServiceValidationError(msg)

            if dev_entry.config_entries and not matching_config_entries:
                msg = "Target's UniFi console is not loaded"
                raise ServiceValidationError(msg)

            if resolved_entry is None and matching_config_entries:
                resolved_entry = matching_config_entries[0]

            candidate_entries = [resolved_entry] if resolved_entry else entries
            for domain, ident in dev_entry.identifiers:
                if domain != DOMAIN:
                    continue
                if ident.startswith(("protect_", "client_")):
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
                    d = ident[len(site_id) + 1 :]
                    return d, site_id, resolved_entry

                s, sep, d = ident.rpartition("_")
                if sep and s and d:
                    return d, s, resolved_entry

        msg = f"Could not resolve target '{device_id}' to a UniFi network device"
        raise ServiceValidationError(msg)

    return device_id, site_id, None


def _resolve_protect_resource_id(
    hass: HomeAssistant,
    resource_type: str,
    resource_id: str,
    protect_entries: list[Any],
) -> tuple[str, Any | None]:
    """Resolve an HA device/entity registry ID to a native UniFi Protect resource ID."""
    dev_reg = (
        dr.async_get(hass)
        if hasattr(hass, "data")
        and isinstance(hass.data, dict)
        and dr.DATA_REGISTRY in hass.data
        else None
    )
    ent_reg = (
        er.async_get(hass)
        if hasattr(hass, "data")
        and isinstance(hass.data, dict)
        and er.DATA_REGISTRY in hass.data
        else None
    )

    if "." in resource_id and (
        ent_reg is None or ent_reg.async_get(resource_id) is None
    ):
        msg = f"Target entity '{resource_id}' not found in entity registry"
        raise ServiceValidationError(msg)

    dev_entry: Any = None
    resolved_entry: Any | None = None
    ent_entry: er.RegistryEntry | None = None

    if ent_reg is not None:
        ent_entry = ent_reg.async_get(resource_id)
        if ent_entry is not None:
            if ent_entry.platform != DOMAIN:
                msg = f"Target '{resource_id}' is not a UniFi Protect {resource_type}"
                raise ServiceValidationError(msg)
            if ent_entry.config_entry_id:
                matching_e = [
                    e
                    for e in protect_entries
                    if e.entry_id == ent_entry.config_entry_id
                ]
                if not matching_e:
                    msg = "Target's UniFi console is not loaded"
                    raise ServiceValidationError(msg)
                resolved_entry = matching_e[0]
            if ent_entry.device_id and dev_reg is not None:
                dev_entry = dev_reg.async_get(ent_entry.device_id)

    if dev_entry is None and dev_reg is not None:
        dev_entry = dev_reg.async_get(resource_id)

    if dev_entry is not None or ent_entry is not None:
        if ent_entry is not None and ent_entry.unique_id:
            prefix = f"{DOMAIN}_{resource_type}_"
            if ent_entry.unique_id.startswith(prefix):
                remainder = ent_entry.unique_id[len(prefix) :]
                candidate_entries = (
                    [resolved_entry] if resolved_entry else protect_entries
                )
                for entry in candidate_entries:
                    protect_data = _coord_section(entry, "protect")
                    if isinstance(protect_data, dict):
                        collection = protect_data.get(
                            {
                                "camera": "cameras",
                                "light": "lights",
                                "chime": "chimes",
                                "viewer": "viewers",
                            }.get(resource_type, f"{resource_type}s")
                        )
                        if isinstance(collection, dict):
                            for k in collection:
                                if remainder == k or remainder.startswith(f"{k}_"):
                                    return k, entry
                parts = remainder.split("_")
                return parts[0], resolved_entry

        if dev_entry is not None:
            matching_config_entries = [
                e for e in protect_entries if e.entry_id in dev_entry.config_entries
            ]
            has_domain_identifier = any(
                ident[0] == DOMAIN for ident in dev_entry.identifiers
            )
            if not matching_config_entries and not has_domain_identifier:
                msg = f"Target '{resource_id}' is not a UniFi Protect {resource_type}"
                raise ServiceValidationError(msg)

            if dev_entry.config_entries and not matching_config_entries:
                msg = "Target's UniFi console is not loaded"
                raise ServiceValidationError(msg)

            if resolved_entry is None and matching_config_entries:
                resolved_entry = matching_config_entries[0]

            expected_prefix = f"protect_{resource_type}_"
            for domain, ident in dev_entry.identifiers:
                if domain != DOMAIN:
                    continue
                if ident.startswith(expected_prefix):
                    native_id = ident[len(expected_prefix) :]
                    return native_id, resolved_entry
                if ident.startswith("protect_"):
                    actual_type = ident.split("_")[1]
                    msg = (
                        f"Target '{resource_id}' is a {actual_type},"
                        f" not a {resource_type}"
                    )
                    raise ServiceValidationError(msg)

            if ent_reg is not None and resource_type == "camera":
                device_entities = er.async_entries_for_device(ent_reg, dev_entry.id)
                for ent in device_entities:
                    if ent.platform == DOMAIN and ent.unique_id.startswith(
                        f"{DOMAIN}_camera_"
                    ):
                        remainder = ent.unique_id[len(f"{DOMAIN}_camera_") :]
                        parts = remainder.split("_")
                        return parts[0], resolved_entry

        msg = (
            f"Could not resolve target '{resource_id}'"
            f" to a UniFi Protect {resource_type}"
        )
        raise ServiceValidationError(msg)

    return resource_id, None


def _resolve_network_client_id(
    hass: HomeAssistant,
    client_id: str,
    entries: list[Any],
) -> tuple[str, Any | None]:
    """Resolve an HA device/entity registry ID to a native client ID or MAC."""
    dev_reg = (
        dr.async_get(hass)
        if hasattr(hass, "data")
        and isinstance(hass.data, dict)
        and dr.DATA_REGISTRY in hass.data
        else None
    )
    ent_reg = (
        er.async_get(hass)
        if hasattr(hass, "data")
        and isinstance(hass.data, dict)
        and er.DATA_REGISTRY in hass.data
        else None
    )

    is_dotted_mac = re.fullmatch(r"(?:[0-9a-fA-F]{4}\.){2}[0-9a-fA-F]{4}", client_id)
    if (
        "." in client_id
        and not is_dotted_mac
        and (ent_reg is None or ent_reg.async_get(client_id) is None)
    ):
        msg = f"Target entity '{client_id}' not found in entity registry"
        raise ServiceValidationError(msg)

    dev_entry: Any = None
    resolved_entry: Any | None = None
    ent_entry: er.RegistryEntry | None = None

    if ent_reg is not None:
        ent_entry = ent_reg.async_get(client_id)
        if ent_entry is not None:
            if ent_entry.platform != DOMAIN:
                msg = f"Target '{client_id}' is not a UniFi Insights entity"
                raise ServiceValidationError(msg)
            if ent_entry.config_entry_id:
                matching_e = [
                    e for e in entries if e.entry_id == ent_entry.config_entry_id
                ]
                if not matching_e:
                    msg = "Target's UniFi console is not loaded"
                    raise ServiceValidationError(msg)
                resolved_entry = matching_e[0]
            if ent_entry.device_id and dev_reg is not None:
                dev_entry = dev_reg.async_get(ent_entry.device_id)

    if dev_entry is None and dev_reg is not None:
        dev_entry = dev_reg.async_get(client_id)
    _raise_for_mobility_target(client_id, ent_entry, dev_entry)

    if dev_entry is not None or ent_entry is not None:
        if ent_entry is not None and ent_entry.unique_id:
            match = re.search(
                r"(?:^|[^0-9a-fA-F])([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})(?:$|[^0-9a-fA-F])",
                ent_entry.unique_id,
            )
            if match:
                return (
                    match.group(1).replace("-", ":").replace("_", ":"),
                    resolved_entry,
                )
            parts = ent_entry.unique_id.split("_")
            if len(parts) > 1:
                return parts[1], resolved_entry

        if dev_entry is not None:
            matching_config_entries = [
                e for e in entries if e.entry_id in dev_entry.config_entries
            ]
            has_domain_identifier = any(
                ident[0] == DOMAIN for ident in dev_entry.identifiers
            )
            if not matching_config_entries and not has_domain_identifier:
                msg = f"Target '{client_id}' is not a UniFi Insights device"
                raise ServiceValidationError(msg)

            if dev_entry.config_entries and not matching_config_entries:
                msg = "Target's UniFi console is not loaded"
                raise ServiceValidationError(msg)

            if resolved_entry is None and matching_config_entries:
                resolved_entry = matching_config_entries[0]

            for domain, ident in dev_entry.identifiers:
                if domain == DOMAIN and ident.startswith("client_"):
                    return ident[len("client_") :], resolved_entry

    return client_id, resolved_entry


def _get_coordinator_for_network_resource(
    hass: HomeAssistant,
    *,
    site_id: str | None = None,
    device_id: str | None = None,
    client_id: str | None = None,
) -> tuple[Any, str | None]:
    """Get the UniFi Insights coordinator owning the specified network resource."""
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if hasattr(entry, "runtime_data")
        and entry.runtime_data
        and not isinstance(entry.runtime_data, CarrierFabricData)
    ]
    if not entries:
        msg = "No UniFi Insights coordinator found"
        raise ServiceValidationError(msg)

    resolved_reg_entry: Any | None = None
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
        native_client_id, resolved_reg_entry = _resolve_network_client_id(
            hass, client_id, entries
        )
        resolved_target_id = native_client_id
        client_id = native_client_id

    # 1. Filter entries by site_id
    if site_id is not None:
        matching_entries = [
            entry for entry in entries if _entry_has_site(entry, site_id)
        ]
        if not matching_entries:
            msg = f"Site '{site_id}' not found on any configured UniFi console"
            raise ServiceValidationError(msg)
    else:
        matching_entries = entries

    # If resolved via registry, verify that the entry matches site_id
    if resolved_reg_entry is not None and resolved_reg_entry not in matching_entries:
        target_name = device_id or client_id
        msg = (
            f"Device '{target_name}' belongs to a different console than"
            f" site '{site_id}'"
        )
        raise ServiceValidationError(msg)

    # 2. Filter / validate by device_id if specified
    if native_device_id is not None:
        entries_with_device = [
            entry
            for entry in matching_entries
            if _entry_has_device(entry, site_id, native_device_id)
        ]
        if not entries_with_device:
            cross_console_entries = [
                entry
                for entry in entries
                if entry not in matching_entries
                and _entry_has_device(entry, None, native_device_id)
            ]
            if cross_console_entries and site_id:
                msg = (
                    f"Device '{native_device_id}' belongs to a different console than"
                    f" site '{site_id}'"
                )
                raise ServiceValidationError(msg)
            msg = (
                f"Device '{native_device_id}' not found on console for site '{site_id}'"
                if site_id
                else (
                    f"Device '{native_device_id}' not found"
                    " on any configured UniFi console"
                )
            )
            raise ServiceValidationError(msg)

        if resolved_reg_entry is not None and resolved_reg_entry in entries_with_device:
            return resolved_reg_entry.runtime_data.coordinator, native_device_id

        if len(entries_with_device) > 1:
            explicit_matches = [
                e
                for e in entries_with_device
                if _entry_owns_device(e, site_id, native_device_id)
            ]
            if len(explicit_matches) == 1:
                return explicit_matches[0].runtime_data.coordinator, native_device_id
            msg = (
                f"Multiple consoles found for site '{site_id}' and device"
                f" '{native_device_id}'; target is ambiguous"
                if site_id
                else (
                    f"Multiple consoles found for device '{native_device_id}';"
                    " target is ambiguous"
                )
            )
            raise ServiceValidationError(msg)

        return entries_with_device[0].runtime_data.coordinator, native_device_id

    # 3. Filter by client_id if specified
    if client_id is not None and len(matching_entries) > 1:
        if resolved_reg_entry is not None and resolved_reg_entry in matching_entries:
            return resolved_reg_entry.runtime_data.coordinator, client_id

        entries_with_client = [
            entry
            for entry in matching_entries
            if _entry_has_client(entry, site_id, client_id)
        ]
        if len(entries_with_client) == 1:
            return entries_with_client[0].runtime_data.coordinator, client_id
        owning_entries = [
            entry
            for entry in matching_entries
            if _entry_owns_client(entry, site_id, client_id)
        ]
        if len(owning_entries) == 1:
            return owning_entries[0].runtime_data.coordinator, client_id
        if len(owning_entries) > 1:
            msg = f"Multiple consoles contain client '{client_id}'; target is ambiguous"
            raise ServiceValidationError(msg)

    # 4. When multiple entries match site_id
    if len(matching_entries) > 1:
        explicit_matches = [
            entry
            for entry in matching_entries
            if site_id in (_coord_section(entry, "sites") or {})
        ]
        if len(explicit_matches) > 1:
            msg = f"Multiple consoles contain site '{site_id}'; target is ambiguous"
            raise ServiceValidationError(msg)
        if len(explicit_matches) == 1:
            return explicit_matches[0].runtime_data.coordinator, resolved_target_id

    if len(matching_entries) > 1:
        msg = (
            f"Multiple consoles found for site '{site_id}'; target is ambiguous"
            if site_id
            else "Multiple UniFi Insights consoles are configured; target is ambiguous"
        )
        raise ServiceValidationError(msg)

    return matching_entries[0].runtime_data.coordinator, resolved_target_id


def _entry_matches_console(entry: Any, console_id: str) -> bool:
    """Match a config entry by its entry ID or its title."""
    wanted = console_id.strip().lower()
    if str(getattr(entry, "entry_id", "")).lower() == wanted:
        return True
    return str(getattr(entry, "title", "")).strip().lower() == wanted


def _select_console(entries: list[Any], console_id: str, what: str) -> Any:
    """Pick the entry the user named, or explain why it could not be picked."""
    matches = [entry for entry in entries if _entry_matches_console(entry, console_id)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        known = ", ".join(sorted(str(getattr(e, "title", e.entry_id)) for e in entries))
        msg = (
            f"No configured {what} matches console '{console_id}'. Configured: {known}"
        )
        raise ServiceValidationError(msg)
    msg = f"Console '{console_id}' matches more than one {what}; use the entry ID"
    raise ServiceValidationError(msg)


def _get_coordinator_for_protect_resource(
    hass: HomeAssistant,
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
    secondary_resource_type: str | None = None,
    secondary_resource_id: str | None = None,
    console_id: str | None = None,
) -> tuple[Any, str | None]:
    """Get the UniFi Protect coordinator owning the specified protect resource."""
    protect_entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if hasattr(entry, "runtime_data")
        and entry.runtime_data
        and not isinstance(entry.runtime_data, CarrierFabricData)
        and getattr(entry.runtime_data.coordinator, "protect_client", None) is not None
    ]
    if not protect_entries:
        msg = "No UniFi Protect coordinator found"
        raise ServiceValidationError(msg)

    # 0. An explicit console always wins.
    if console_id:
        entry = _select_console(protect_entries, console_id, "UniFi Protect console")
        return entry.runtime_data.coordinator, None

    if resource_type is None or resource_id is None:
        if len(protect_entries) > 1:
            known = ", ".join(
                sorted(str(getattr(e, "title", e.entry_id)) for e in protect_entries)
            )
            msg = (
                "Multiple UniFi Protect consoles are configured and this action"
                " carries no target to route on; set 'console_id' to one of:"
                f" {known}"
            )
            raise ServiceValidationError(msg)
        return protect_entries[0].runtime_data.coordinator, None

    # Resolve resource_id if it is an HA registry ID
    native_resource_id, resolved_reg_entry = _resolve_protect_resource_id(
        hass, resource_type, resource_id, protect_entries
    )

    collection_key = {
        "camera": "cameras",
        "light": "lights",
        "chime": "chimes",
        "viewer": "viewers",
    }.get(resource_type, f"{resource_type}s")

    matching_entries = [
        entry
        for entry in protect_entries
        if _protect_entry_has_resource(entry, collection_key, native_resource_id)
    ]

    if not matching_entries:
        msg = (
            f"{resource_type.capitalize()} '{native_resource_id}' not found on any"
            " configured UniFi Protect console"
        )
        raise ServiceValidationError(msg)

    if resolved_reg_entry is not None and resolved_reg_entry not in matching_entries:
        msg = (
            f"{resource_type.capitalize()} '{resource_id}' belongs to a different"
            " console than the target console"
        )
        raise ServiceValidationError(msg)

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
            if len(explicit_matches) == 1:
                matching_entries = explicit_matches
            elif len(explicit_matches) > 1:
                msg = (
                    f"Multiple Protect consoles contain {resource_type}"
                    f" '{native_resource_id}';"
                    " target is ambiguous"
                )
                raise ServiceValidationError(msg)
            else:
                msg = (
                    f"Cannot tell which Protect console owns {resource_type}"
                    f" '{native_resource_id}' yet;"
                    " retry once the consoles have refreshed"
                )
                raise ServiceValidationError(msg)

    target_entry = matching_entries[0]

    # Validate secondary resource if provided (e.g. chime camera_id)
    if secondary_resource_type and secondary_resource_id:
        native_sec_id = secondary_resource_id
        secondary_entry = None
        if secondary_resource_type in ("camera", "light", "chime", "viewer"):
            native_sec_id, secondary_entry = _resolve_protect_resource_id(
                hass, secondary_resource_type, secondary_resource_id, protect_entries
            )

        if secondary_entry is not None and secondary_entry != target_entry:
            msg = (
                f"{secondary_resource_type.capitalize()} '{secondary_resource_id}'"
                " belongs to a different Protect console than"
                f" {resource_type} '{resource_id}'"
            )
            raise ServiceValidationError(msg)

        sec_collection_key = {
            "camera": "cameras",
            "liveview": "liveviews",
        }.get(secondary_resource_type, f"{secondary_resource_type}s")

        if not _protect_entry_has_resource(
            target_entry, sec_collection_key, native_sec_id
        ):
            cross_entries = [
                entry
                for entry in protect_entries
                if entry != target_entry
                and _protect_owns_resource(entry, sec_collection_key, native_sec_id)
                is True
            ]
            if cross_entries:
                msg = (
                    f"{secondary_resource_type.capitalize()} '{secondary_resource_id}'"
                    " belongs to a different Protect console than"
                    f" {resource_type} '{resource_id}'"
                )
                raise ServiceValidationError(msg)

            _LOGGER.debug(
                "%s '%s' is not in cached Protect data; proceeding on the console"
                " owning %s '%s'",
                secondary_resource_type.capitalize(),
                secondary_resource_id,
                resource_type,
                resource_id,
            )

    return target_entry.runtime_data.coordinator, native_resource_id


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


def _entry_id_for_coordinator(hass: HomeAssistant, coordinator: Any) -> str | None:
    """Return the config entry whose runtime coordinator is ``coordinator``."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime_data = getattr(entry, "runtime_data", None)
        if getattr(runtime_data, "coordinator", None) is coordinator:
            return entry.entry_id
    return None


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
    chime_entry_id: str | None,
    coordinator: Any,
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
    data = coordinator.data
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


SERVICE_REFRESH_DATA = "refresh_data"
SERVICE_RESTART_DEVICE = "restart_device"

TARGET_SELECTOR_SCHEMA = vol.Any(cv.string, [cv.string])
TARGET_DICT_SCHEMA = vol.Schema(
    {
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("area_id"): TARGET_SELECTOR_SCHEMA,
    },
    extra=vol.ALLOW_EXTRA,
)

# Schema for refresh_data service
REFRESH_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional("site_id"): cv.string,
    }
)

# Schema for restart_device service
RESTART_DEVICE_SCHEMA = vol.Schema(
    {
        vol.Optional("site_id"): cv.string,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
    }
)

# Schema for power_cycle_port service
POWER_CYCLE_PORT_SCHEMA = vol.Schema(
    {
        vol.Optional("site_id"): cv.string,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
        vol.Required("port_idx"): vol.All(cv.positive_int, vol.Range(min=1)),
    }
)


# Schema for set_hdr_mode service
SET_HDR_MODE_SCHEMA = vol.Schema(
    {
        vol.Optional("camera_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
        vol.Required("mode"): vol.In([HDR_MODE_AUTO, HDR_MODE_ON, HDR_MODE_OFF]),
    }
)

# Schema for set_video_mode service
SET_VIDEO_MODE_SCHEMA = vol.Schema(
    {
        vol.Optional("camera_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
        vol.Required("mode"): vol.In(
            [
                VIDEO_MODE_DEFAULT,
                VIDEO_MODE_HIGH_FPS,
                VIDEO_MODE_SPORT,
                VIDEO_MODE_SLOW_SHUTTER,
                "high_fps",
                "slow_shutter",
            ]
        ),
    }
)

# Schema for set_mic_volume service
SET_MIC_VOLUME_SCHEMA = vol.Schema(
    {
        vol.Optional("camera_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
        vol.Required("volume"): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
    }
)

# Schema for set_light_mode service
SET_LIGHT_MODE_SCHEMA = vol.Schema(
    {
        vol.Optional("light_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
        vol.Required("mode"): vol.In(
            [
                LIGHT_MODE_ALWAYS,
                LIGHT_MODE_MOTION,
                LIGHT_MODE_OFF,
            ]
        ),
    }
)

# Schema for set_light_level service
SET_LIGHT_LEVEL_SCHEMA = vol.Schema(
    {
        vol.Optional("light_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
        vol.Required("level"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
    }
)

# Schema for ptz_move service
PTZ_MOVE_SCHEMA = vol.Schema(
    {
        vol.Optional("camera_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
        vol.Required("preset"): vol.All(vol.Coerce(int), vol.Range(min=0, max=15)),
    }
)

# Schema for ptz_patrol service
PTZ_PATROL_SCHEMA = vol.Schema(
    {
        vol.Optional("camera_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
        vol.Required("action"): vol.In(["start", "stop"]),
        vol.Optional("slot", default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=15)
        ),
    }
)

# Schema for set_chime_volume service
SET_CHIME_VOLUME_SCHEMA = vol.Schema(
    {
        vol.Optional("chime_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
        vol.Required("volume"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
        vol.Optional("camera_id"): TARGET_SELECTOR_SCHEMA,
    }
)

# Ringtone IDs are opaque strings, not a fixed list of sound names.
RINGTONE_ID_SCHEMA = vol.All(str, vol.Strip, vol.Length(min=1))

# Schema for play_chime_ringtone service
PLAY_CHIME_RINGTONE_SCHEMA = vol.Schema(
    {
        vol.Optional("chime_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
        vol.Optional("ringtone_id"): str,
    }
)

# Schema for set_chime_ringtone service
SET_CHIME_RINGTONE_SCHEMA = vol.Schema(
    {
        vol.Optional("chime_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
        vol.Required("ringtone_id"): RINGTONE_ID_SCHEMA,
        vol.Optional("camera_id"): TARGET_SELECTOR_SCHEMA,
    }
)

# Schema for set_chime_repeat_times service
SET_CHIME_REPEAT_TIMES_SCHEMA = vol.Schema(
    {
        vol.Optional("chime_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
        vol.Required("repeat_times"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=10)
        ),
        vol.Optional("camera_id"): TARGET_SELECTOR_SCHEMA,
    }
)

# Schema for set_chime_paired_doorbells service. ``doorbells`` is a target
# selection (entity, device, area, floor, label), like Home Assistant's own
# UniFi Protect action; an empty or missing one unpairs every doorbell.
DOORBELLS_SELECTION_SCHEMA = vol.Schema(
    {
        vol.Optional(key): TARGET_SELECTOR_SCHEMA
        for key in ("entity_id", "device_id", "area_id", "floor_id", "label_id")
    }
)
SET_CHIME_PAIRED_DOORBELLS_SCHEMA = vol.Schema(
    {
        vol.Optional("chime_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
        vol.Optional("doorbells"): vol.Any(None, DOORBELLS_SELECTION_SCHEMA),
    }
)

# Schema for reconnect_client service
RECONNECT_CLIENT_SCHEMA = vol.Schema(
    {
        vol.Required("site_id"): cv.string,
        vol.Optional("client_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
    }
)

# Schema for remove_clients service
REMOVE_CLIENTS_SCHEMA = vol.Schema({})

# Schema for authorize_guest service
AUTHORIZE_GUEST_SCHEMA = vol.Schema(
    {
        vol.Required("site_id"): cv.string,
        vol.Optional("client_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
        # Note: these options are accepted for backwards compatibility but not
        # sent yet (a warning is logged), so the site's default guest limits
        # apply. The Integration API authorize action does accept them
        # (timeLimitMinutes, dataUsageLimitMBytes, rx/txRateLimitKbps in
        # Network API v10.6.106).
        vol.Optional("duration_minutes"): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional("upload_limit_kbps"): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("download_limit_kbps"): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("data_limit_mb"): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)

# Schema for generate_voucher service
GENERATE_VOUCHER_SCHEMA = vol.Schema(
    {
        vol.Required("site_id"): cv.string,
        vol.Optional("count", default=1): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
        vol.Optional("duration_minutes", default=480): vol.All(
            vol.Coerce(int), vol.Range(min=1)
        ),
        vol.Optional("upload_limit_kbps"): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("download_limit_kbps"): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("data_limit_mb"): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("note"): cv.string,
    }
)

# Schema for delete_voucher service
DELETE_VOUCHER_SCHEMA = vol.Schema(
    {
        vol.Required("site_id"): cv.string,
        vol.Required("voucher_id"): cv.string,
    }
)

# Schema for export_network_rules service
EXPORT_NETWORK_RULES_SCHEMA = vol.Schema(
    {
        vol.Optional("site_id"): cv.string,
        vol.Optional("console_id"): cv.string,
        vol.Optional("rule_types"): vol.All(cv.ensure_list, [vol.In(RULE_TYPES)]),
        vol.Optional("redact", default=True): cv.boolean,
    }
)

# Schema for trigger_alarm service
TRIGGER_ALARM_SCHEMA = vol.Schema(
    {
        vol.Required("alarm_id"): cv.string,
        vol.Optional("console_id"): cv.string,
    }
)

# Schema for create_liveview service
CREATE_LIVEVIEW_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Required("layout"): vol.All(vol.Coerce(int), vol.Range(min=1, max=4)),
        vol.Optional("is_default", default=False): cv.boolean,
        vol.Optional("console_id"): cv.string,
    }
)

# Schema for set_liveview service
SET_LIVEVIEW_SCHEMA = vol.Schema(
    {
        vol.Optional("viewer_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
        vol.Required("liveview_id"): cv.string,
    }
)


# Schemas for Carrier Fabric services
MAX_SUSPEND_REASON_LENGTH: Final = 1024

CARRIER_SUSPEND_SUBSCRIBER_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
        vol.Optional("reason"): vol.All(
            cv.string, vol.Length(max=MAX_SUSPEND_REASON_LENGTH)
        ),
    }
)

CARRIER_RESUME_SUBSCRIBER_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("entity_id"): TARGET_SELECTOR_SCHEMA,
        vol.Optional("target"): TARGET_DICT_SCHEMA,
    }
)


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


def _resolve_carrier_subscriber_target(
    hass: HomeAssistant, call: ServiceCall
) -> tuple[Any, str]:
    """Resolve a service call to a Carrier Fabric coordinator and subscriber ID."""
    targets = _extract_carrier_targets(call)
    if not targets:
        msg = "At least one target must be specified"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="carrier_target_required",
        )
    if len(targets) > 1:
        msg = (
            f"Multiple targets specified for {call.service}; "
            "action only supports a single target"
        )
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="carrier_multiple_targets",
            translation_placeholders={"service": call.service},
        )

    target_id = targets[0]
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    dev_entry: dr.DeviceEntry | dr.ChildDeviceEntry | None = None

    if "." in target_id:
        ent_entry = ent_reg.async_get(target_id)
        if ent_entry is None:
            msg = f"Target entity '{target_id}' not found in entity registry"
            raise ServiceValidationError(
                msg,
                translation_domain=DOMAIN,
                translation_key="carrier_target_not_found",
                translation_placeholders={"target": target_id},
            )
        if ent_entry.platform != DOMAIN:
            msg = f"Target '{target_id}' is not a UniFi Insights entity"
            raise ServiceValidationError(
                msg,
                translation_domain=DOMAIN,
                translation_key="carrier_target_not_subscriber",
                translation_placeholders={"target": target_id},
            )
        if ent_entry.device_id:
            dev_entry = dev_reg.async_get(ent_entry.device_id)
    else:
        dev_entry = dev_reg.async_get(target_id)
        if dev_entry is None:
            dev_entry = dev_reg.async_get_device(identifiers={(DOMAIN, target_id)})
        if dev_entry is None and not target_id.startswith("carrier_subscriber_"):
            dev_entry = dev_reg.async_get_device(
                identifiers={(DOMAIN, f"carrier_subscriber_{target_id}")}
            )

    if dev_entry is None:
        msg = f"Target '{target_id}' not found in device registry"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="carrier_target_not_found",
            translation_placeholders={"target": target_id},
        )

    has_domain_identifier = any(ident[0] == DOMAIN for ident in dev_entry.identifiers)
    if not has_domain_identifier:
        msg = f"Target '{target_id}' is not a UniFi Insights device"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="carrier_target_not_subscriber",
            translation_placeholders={"target": target_id},
        )

    matching_entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id in dev_entry.config_entries
    ]
    if not matching_entries:
        msg = "Target config entry is not loaded"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="carrier_entry_not_loaded",
        )

    target_entry = matching_entries[0]
    if target_entry.data.get(CONF_CONNECTION_TYPE) != CONNECTION_TYPE_CARRIER_FABRIC:
        msg = f"Target '{target_id}' is not a Carrier Fabric subscriber"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="carrier_target_not_subscriber",
            translation_placeholders={"target": target_id},
        )

    runtime_data = getattr(target_entry, "runtime_data", None)
    if target_entry.state is not ConfigEntryState.LOADED or not isinstance(
        runtime_data, CarrierFabricData
    ):
        msg = "Carrier Fabric integration entry is not loaded"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="carrier_entry_not_loaded",
        )

    subscriber_id: str | None = None
    for domain, ident in dev_entry.identifiers:
        if domain == DOMAIN and ident.startswith("carrier_subscriber_"):
            subscriber_id = ident[len("carrier_subscriber_") :]
            break

    if not subscriber_id:
        msg = f"Target '{target_id}' is not a Carrier Fabric subscriber"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="carrier_target_not_subscriber",
            translation_placeholders={"target": target_id},
        )

    try:
        uuid.UUID(subscriber_id)
    except ValueError as err:
        raise _invalid_carrier_subscriber_id(subscriber_id) from err

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

    return runtime_data.coordinator, subscriber_id


def _invalid_carrier_subscriber_id(subscriber_id: str) -> ServiceValidationError:
    """Build the validation error for a subscriber device id that is no UUID."""
    msg = f"Invalid subscriber ID format '{subscriber_id}' (valid UUID expected)"
    return ServiceValidationError(
        msg,
        translation_domain=DOMAIN,
        translation_key="carrier_invalid_subscriber_id",
        translation_placeholders={"target": subscriber_id},
    )


def _raise_carrier_action_error(
    err: UniFiError | InvalidSubscriberIdError, action: str, subscriber_id: str
) -> NoReturn:
    """Map a Carrier Fabric action failure to a translated Home Assistant error."""
    if isinstance(err, InvalidSubscriberIdError):
        raise _invalid_carrier_subscriber_id(subscriber_id) from err

    if isinstance(err, UniFiAuthenticationError) and (
        err.status_code == HTTPStatus.FORBIDDEN
        or err.api_error_code == "insufficient_scope"
    ):
        required_scope = f"{action}:service"
        msg = (
            f"Carrier Fabric API key missing required scope '{required_scope}' "
            f"to {action} subscriber {subscriber_id}"
        )
        raise HomeAssistantError(
            msg,
            translation_domain=DOMAIN,
            translation_key="carrier_missing_scope",
            translation_placeholders={
                "scope": required_scope,
                "action": action,
                "subscriber_id": subscriber_id,
            },
        ) from err

    # Never surface the raw error: it can carry subscriber data.
    msg = f"Failed to {action} subscriber"
    raise HomeAssistantError(
        msg,
        translation_domain=DOMAIN,
        translation_key="carrier_action_failed",
        translation_placeholders={"action": action},
    ) from err


async def _async_handle_carrier_suspend_subscriber(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle carrier_suspend_subscriber service call."""
    carrier_coordinator, subscriber_id = _resolve_carrier_subscriber_target(hass, call)
    # CARRIER_SUSPEND_SUBSCRIBER_SCHEMA already made this a str of at most
    # MAX_SUSPEND_REASON_LENGTH characters.
    reason = call.data.get("reason")
    try:
        await carrier_coordinator.async_suspend_subscriber(subscriber_id, reason=reason)
    except (UniFiError, InvalidSubscriberIdError) as err:
        _raise_carrier_action_error(err, "suspend", subscriber_id)


async def _async_handle_carrier_resume_subscriber(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle carrier_resume_subscriber service call."""
    carrier_coordinator, subscriber_id = _resolve_carrier_subscriber_target(hass, call)
    try:
        await carrier_coordinator.async_resume_subscriber(subscriber_id)
    except (UniFiError, InvalidSubscriberIdError) as err:
        _raise_carrier_action_error(err, "resume", subscriber_id)


async def _async_handle_power_cycle_port(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle the power cycle port service call."""
    site_id = call.data.get("site_id")
    raw_device_id = _extract_target_id(call, "device_id")
    if not raw_device_id:
        msg = "Device ID or target is required"
        raise ServiceValidationError(msg)

    coordinator, device_id = _get_coordinator_for_network_resource(
        hass, site_id=site_id, device_id=raw_device_id
    )

    if not site_id and isinstance(coordinator.data, dict):
        devices_by_site = coordinator.data.get("devices", {})
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
        msg = f"Site ID is required to power-cycle port on device '{device_id}'"
        raise ServiceValidationError(msg)

    port_idx = call.data["port_idx"]

    # Validate against the device's interfaces.ports[] where poe.enabled is true
    devices = (
        coordinator.data.get("devices") if isinstance(coordinator.data, dict) else None
    )
    site_devices = devices.get(site_id) if isinstance(devices, dict) else None
    device_data = (
        site_devices.get(device_id) if isinstance(site_devices, dict) else None
    )

    if not isinstance(device_data, dict):
        msg = f"Device {device_id} was not found in site {site_id}"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={
                "device_id": str(device_id),
                "site_id": str(site_id),
            },
        )

    port = get_device_port(coordinator.data, site_id, device_id, port_idx)
    if port is None:
        msg = f"Port {port_idx} was not found on device {device_id}"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="port_not_found",
            translation_placeholders={
                "port_idx": str(port_idx),
                "device_id": str(device_id),
            },
        )

    if not port_can_be_power_cycled(port):
        msg = f"Port {port_idx} on device {device_id} is not PoE-enabled"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="port_not_poe",
            translation_placeholders={
                "port_idx": str(port_idx),
                "device_id": str(device_id),
            },
        )

    _LOGGER.info(
        "Power-cycling PoE port %d on device %s in site %s",
        port_idx,
        device_id,
        site_id,
    )
    await coordinator.async_power_cycle_port(site_id, device_id, port_idx)


def _is_wireless_client(client_record: dict[str, Any]) -> bool:
    """Check if client record represents an exact recognized wireless client."""
    raw_type = client_record.get("type") or client_record.get("connection_type")
    if isinstance(raw_type, ClientType):
        return raw_type == ClientType.WIRELESS
    if isinstance(raw_type, str):
        normalized = raw_type.strip().upper()
        return normalized in {"WIRELESS", "CLIENTTYPE.WIRELESS"}
    return False


async def _async_handle_reconnect_client(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle reconnect_client service call."""
    site_id = call.data["site_id"]
    raw_client_id = _extract_target_id(call, "client_id")
    if not raw_client_id:
        msg = "Client ID or target is required"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="client_target_required",
        )

    coordinator, client_id = _get_coordinator_for_network_resource(
        hass, site_id=site_id, client_id=raw_client_id
    )

    client_id = _resolve_native_client_id(coordinator, site_id, client_id)

    data = coordinator.data
    clients = data.get("clients", {}) if isinstance(data, dict) else {}
    site_clients = clients.get(site_id, {}) if isinstance(clients, dict) else {}
    client_record = (
        site_clients.get(client_id)
        if isinstance(site_clients, dict) and client_id
        else None
    )

    if not isinstance(client_record, dict) or not client_record.get("connected"):
        msg = "client is not currently connected"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="client_not_connected",
        )

    if not _is_wireless_client(client_record):
        msg = f"Client '{client_id}' is not a wireless client"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="client_not_wireless",
            translation_placeholders={"client_id": str(client_id)},
        )

    await coordinator.async_reconnect_client(site_id, client_id)


async def _async_handle_remove_clients(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle remove_clients service call."""
    _ = call
    failures: list[str] = []
    network_consoles = 0

    for title, coordinator in _get_titled_coordinators(hass):
        if not getattr(coordinator, "is_network_usable", True):
            continue
        network_consoles += 1

        sites = (coordinator.data or {}).get("sites") or {}
        site_ids = list(sites.keys())

        total_removed_console = 0
        for site_id in site_ids:
            try:
                historical_clients = await coordinator.async_get_historical_clients(
                    site_id
                )
                eligible_macs = _filter_clients_for_removal(historical_clients)
                if eligible_macs:
                    await coordinator.async_forget_clients_batch(site_id, eligible_macs)
                    total_removed_console += len(eligible_macs)
            except HomeAssistantError as err:
                _LOGGER.debug(
                    "Error removing clients from %s (site %s): %s",
                    title,
                    site_id,
                    err,
                )
                failures.append(f"{title} (site {site_id}): {err}")

        _LOGGER.debug("Removed %d clients for console %s", total_removed_console, title)

    if not network_consoles:
        msg = "No UniFi Insights console has Network available"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="no_network_console",
        )

    if failures:
        msg = "; ".join(failures)
        raise HomeAssistantError(
            msg,
            translation_domain=DOMAIN,
            translation_key="remove_clients_failed",
            translation_placeholders={"failures": msg},
        )


def _get_coordinator_for_rule_export(
    hass: HomeAssistant, *, site_id: str | None, console_id: str | None
) -> Any:
    """
    Return the console facade a rule export runs against.

    ``console_id`` (entry title or entry ID) names the console directly. Without
    it the console is found from ``site_id``, or is the only configured one.
    Raises a translated ``ServiceValidationError`` when the target is unknown
    or ambiguous.
    """
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if hasattr(entry, "runtime_data")
        and entry.runtime_data
        and not isinstance(entry.runtime_data, CarrierFabricData)
    ]
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_network_console",
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
        return entry.runtime_data.coordinator
    if site_id is None and len(entries) > 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="export_console_required",
        )
    if site_id is not None and not any(_entry_has_site(e, site_id) for e in entries):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="export_target_not_found",
            translation_placeholders={"target": site_id},
        )
    try:
        coordinator, _ = _get_coordinator_for_network_resource(hass, site_id=site_id)
    except ServiceValidationError as err:
        # The site exists, so what is left is the shared helper's untranslated
        # "more than one console has this site" error.
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="export_target_ambiguous",
            translation_placeholders={"target": str(site_id)},
        ) from err
    return coordinator


async def _async_handle_export_network_rules(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    """Return a fresh export of the network rules of one console."""
    site_id = call.data.get("site_id")
    coordinator = _get_coordinator_for_rule_export(
        hass, site_id=site_id, console_id=call.data.get("console_id")
    )
    document: dict[str, Any] = await coordinator.async_export_network_rules(
        [site_id] if site_id else None, call.data.get("rule_types") or None
    )
    if not document["sites"]:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="export_no_sites",
        )
    if call.data["redact"]:
        document = redact_network_rule_export(document)
    return document


SERVICE_SET_ACCESS_USER_PIN: Final = "set_access_user_pin"
SERVICE_REMOVE_ACCESS_USER_PIN: Final = "remove_access_user_pin"
SERVICE_SET_ACCESS_USER_STATUS: Final = "set_access_user_status"

# Access user IDs become URL path segments, so only the characters the console
# issues are accepted. PIN digits and length are not checked here: the console
# owns that contract and the coordinator reports its answer.
_ACCESS_USER_ID = vol.All(cv.string, vol.Match(r"^[0-9A-Za-z-]{1,64}$"))

SET_ACCESS_USER_PIN_SCHEMA = vol.Schema(
    {
        vol.Required("user_id"): _ACCESS_USER_ID,
        # Reject YAML/JSON numbers rather than silently changing a credential.
        vol.Required("pin"): str,
        vol.Optional("console_id"): cv.string,
    }
)

REMOVE_ACCESS_USER_PIN_SCHEMA = vol.Schema(
    {
        vol.Required("user_id"): _ACCESS_USER_ID,
        vol.Optional("console_id"): cv.string,
    }
)

SET_ACCESS_USER_STATUS_SCHEMA = vol.Schema(
    {
        vol.Required("user_id"): _ACCESS_USER_ID,
        vol.Required("status"): vol.In(("ACTIVE", "DEACTIVATED")),
        vol.Optional("console_id"): cv.string,
    }
)


def _access_coordinator_of(entry: Any) -> Any:
    """Return an entry's Access coordinator, or None when Access is not loaded."""
    runtime_data = getattr(entry, "runtime_data", None)
    return getattr(runtime_data, "access_coordinator", None)


def _get_access_coordinator(hass: HomeAssistant, *, console_id: str | None) -> Any:
    """Route an Access user action to the one console that owns it."""
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if _access_coordinator_of(entry) is not None
    ]
    if not entries:
        msg = "No UniFi Access console is loaded"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="access_not_configured",
        )
    if console_id:
        return _access_coordinator_of(
            _select_console(entries, console_id, "UniFi Access console")
        )
    if len(entries) > 1:
        msg = "Multiple UniFi Access consoles are loaded; set 'console_id'"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="access_console_required",
        )
    return _access_coordinator_of(entries[0])


async def _async_run_access_user_action(action: Awaitable[None]) -> None:
    """Run a coordinator user action and report an unknown user as validation."""
    try:
        await action
    except HomeAssistantError as err:
        cause = err.__cause__
        if isinstance(cause, UniFiResponseError) and (
            isinstance(cause, UniFiNotFoundError)
            or cause.status_code == HTTPStatus.NOT_FOUND
        ):
            msg = "No UniFi Access user matches that user ID"
            raise ServiceValidationError(
                msg,
                translation_domain=DOMAIN,
                translation_key="access_user_not_found",
            ) from err
        raise


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up the UniFi Insights services."""
    _LOGGER.debug("Setting up UniFi Insights services")

    async def async_handle_refresh_data(call: ServiceCall) -> None:
        """Handle the refresh data service call."""
        _LOGGER.debug("Handling refresh_data service call with data: %s", call.data)

        site_id = call.data.get("site_id")

        # Get all coordinators from config entries
        coordinators = _get_coordinators(hass)

        if not coordinators:
            _LOGGER.error("No UniFi Insights coordinators found")
            msg = "No UniFi Insights coordinators found"
            raise ServiceValidationError(msg)

        _LOGGER.info(
            "Refreshing data for %s site%s",
            "specific" if site_id else "all",
            f" (ID: {site_id})" if site_id else "s",
        )

        refreshed = 0
        failures: list[str] = []

        for title, coordinator in _get_titled_coordinators(hass):
            # If site_id is specified, only refresh the console owning it.
            sites = (coordinator.data or {}).get("sites") or {}
            if site_id and site_id not in sites:
                _LOGGER.debug("Skipping coordinator - site %s not found", site_id)
                continue

            refreshed += 1
            try:
                # A site is a Network concept, so a site-scoped call has no
                # reason to spend a Protect round trip.
                await coordinator.async_refresh_or_raise(
                    include_protect=site_id is None
                )
            except HomeAssistantError as err:
                # Keep going: one unreachable console must not stop the others
                # from being refreshed. Logged at debug because every failure
                # is re-raised to the caller below, which Home Assistant
                # already logs - an error line here would just duplicate it.
                _LOGGER.debug("Error refreshing %s: %s", title, err)
                failures.append(f"{title}: {err}")
            else:
                _LOGGER.info("Successfully refreshed coordinator data")

        if site_id and not refreshed:
            msg = f"No UniFi Insights console is configured for site '{site_id}'"
            raise ServiceValidationError(msg)

        if failures:
            # Each failure already carries its own "Error refreshing ..."
            # sentence from the facade, so do not prefix a second one.
            raise HomeAssistantError("; ".join(failures))

    async def async_handle_restart_device(call: ServiceCall) -> None:
        """Handle the restart device service call."""
        site_id = call.data.get("site_id")
        raw_device_id = _extract_target_id(call, "device_id")
        if not raw_device_id:
            msg = "Device ID or target is required"
            raise ServiceValidationError(msg)

        coordinator, device_id = _get_coordinator_for_network_resource(
            hass, site_id=site_id, device_id=raw_device_id
        )

        if not site_id and isinstance(coordinator.data, dict):
            devices_by_site = coordinator.data.get("devices")
            if isinstance(devices_by_site, dict):
                for s, s_devs in devices_by_site.items():
                    if isinstance(s_devs, dict) and device_id in s_devs:
                        site_id = s
                        break

        if not site_id:
            msg = f"Site ID is required to restart device '{device_id}'"
            raise ServiceValidationError(msg)

        _LOGGER.info("Restarting device %s in site %s", device_id, site_id)
        await coordinator.async_restart_device(site_id, device_id)

    async def async_handle_power_cycle_port(call: ServiceCall) -> None:
        """Handle the power cycle port service call."""
        await _async_handle_power_cycle_port(hass, call)

    async def async_handle_set_hdr_mode(call: ServiceCall) -> None:
        """Handle the set_hdr_mode service call."""
        raw_camera_id = _extract_target_id(call, "camera_id")
        if not raw_camera_id:
            msg = "Camera ID or target is required"
            raise ServiceValidationError(msg)
        mode = call.data["mode"]

        coordinator, camera_id = _get_coordinator_for_protect_resource(
            hass, resource_type="camera", resource_id=raw_camera_id
        )

        _LOGGER.info("Setting HDR mode for camera %s to %s", camera_id, mode)
        await coordinator.async_set_hdr_mode(camera_id, mode)

    async def async_handle_set_video_mode(call: ServiceCall) -> None:
        """Handle the set_video_mode service call."""
        raw_camera_id = _extract_target_id(call, "camera_id")
        if not raw_camera_id:
            msg = "Camera ID or target is required"
            raise ServiceValidationError(msg)
        mode = call.data["mode"]

        coordinator, camera_id = _get_coordinator_for_protect_resource(
            hass, resource_type="camera", resource_id=raw_camera_id
        )

        _LOGGER.info("Setting video mode for camera %s to %s", camera_id, mode)
        await coordinator.async_set_video_mode(camera_id, mode)

    async def async_handle_set_mic_volume(call: ServiceCall) -> None:
        """Handle the set_mic_volume service call."""
        raw_camera_id = _extract_target_id(call, "camera_id")
        if not raw_camera_id:
            msg = "Camera ID or target is required"
            raise ServiceValidationError(msg)
        volume = call.data["volume"]

        coordinator, camera_id = _get_coordinator_for_protect_resource(
            hass, resource_type="camera", resource_id=raw_camera_id
        )

        _LOGGER.info("Setting mic volume for camera %s to %d%%", camera_id, volume)
        await coordinator.async_set_microphone_volume(camera_id, volume)

    # Register services
    _LOGGER.debug("Registering UniFi Insights services")
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_DATA,
        async_handle_refresh_data,
        schema=REFRESH_DATA_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESTART_DEVICE,
        async_handle_restart_device,
        schema=RESTART_DEVICE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_POWER_CYCLE_PORT,
        async_handle_power_cycle_port,
        schema=POWER_CYCLE_PORT_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_HDR_MODE,
        async_handle_set_hdr_mode,
        schema=SET_HDR_MODE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_VIDEO_MODE,
        async_handle_set_video_mode,
        schema=SET_VIDEO_MODE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_MIC_VOLUME,
        async_handle_set_mic_volume,
        schema=SET_MIC_VOLUME_SCHEMA,
    )

    async def async_handle_set_light_mode(call: ServiceCall) -> None:
        """Handle the set_light_mode service call."""
        raw_light_id = _extract_target_id(call, "light_id")
        if not raw_light_id:
            msg = "Light ID or target is required"
            raise ServiceValidationError(msg)
        mode = call.data["mode"]

        coordinator, light_id = _get_coordinator_for_protect_resource(
            hass, resource_type="light", resource_id=raw_light_id
        )

        _LOGGER.info("Setting light mode for %s to %s", light_id, mode)
        await coordinator.async_set_light_mode(light_id, mode)

    async def async_handle_set_light_level(call: ServiceCall) -> None:
        """Handle the set_light_level service call."""
        raw_light_id = _extract_target_id(call, "light_id")
        if not raw_light_id:
            msg = "Light ID or target is required"
            raise ServiceValidationError(msg)
        level = call.data["level"]

        coordinator, light_id = _get_coordinator_for_protect_resource(
            hass, resource_type="light", resource_id=raw_light_id
        )

        led_level = percent_to_led_level(level)
        _LOGGER.info(
            "Setting light level for %s to %d%% (led_level %d)",
            light_id,
            level,
            led_level,
        )
        await coordinator.async_set_light_brightness(light_id, led_level)

    async def async_handle_ptz_move(call: ServiceCall) -> None:
        """Handle the ptz_move service call."""
        raw_camera_id = _extract_target_id(call, "camera_id")
        if not raw_camera_id:
            msg = "Camera ID or target is required"
            raise ServiceValidationError(msg)
        preset = call.data["preset"]

        coordinator, camera_id = _get_coordinator_for_protect_resource(
            hass, resource_type="camera", resource_id=raw_camera_id
        )

        _LOGGER.info("Moving camera %s to preset %d", camera_id, preset)
        await coordinator.async_move_ptz_to_preset(camera_id, preset)

    async def async_handle_ptz_patrol(call: ServiceCall) -> None:
        """Handle the ptz_patrol service call."""
        raw_camera_id = _extract_target_id(call, "camera_id")
        if not raw_camera_id:
            msg = "Camera ID or target is required"
            raise ServiceValidationError(msg)
        action = call.data["action"]
        slot = call.data.get("slot", 0)

        coordinator, camera_id = _get_coordinator_for_protect_resource(
            hass, resource_type="camera", resource_id=raw_camera_id
        )

        if action == "start":
            _LOGGER.info("Starting PTZ patrol slot %d for camera %s", slot, camera_id)
            await coordinator.async_start_ptz_patrol(camera_id, slot)
        else:
            _LOGGER.info("Stopping PTZ patrol for camera %s", camera_id)
            await coordinator.async_stop_ptz_patrol(camera_id)

    # Register light services
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_LIGHT_MODE,
        async_handle_set_light_mode,
        schema=SET_LIGHT_MODE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_LIGHT_LEVEL,
        async_handle_set_light_level,
        schema=SET_LIGHT_LEVEL_SCHEMA,
    )

    # Register PTZ services
    hass.services.async_register(
        DOMAIN,
        SERVICE_PTZ_MOVE,
        async_handle_ptz_move,
        schema=PTZ_MOVE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_PTZ_PATROL,
        async_handle_ptz_patrol,
        schema=PTZ_PATROL_SCHEMA,
    )

    async def async_handle_set_chime_volume(call: ServiceCall) -> None:
        """Handle the set_chime_volume service call."""
        raw_chime_id = _extract_target_id(call, "chime_id")
        if not raw_chime_id:
            msg = "Chime ID or target is required"
            raise ServiceValidationError(msg)
        volume = call.data["volume"]
        raw_camera_id = call.data.get("camera_id")
        if isinstance(raw_camera_id, list):
            raw_camera_id = raw_camera_id[0] if raw_camera_id else None
        if isinstance(raw_camera_id, str):
            raw_camera_id = raw_camera_id.strip() or None

        coordinator, chime_id = _get_coordinator_for_protect_resource(
            hass,
            resource_type="chime",
            resource_id=raw_chime_id,
            secondary_resource_type="camera" if raw_camera_id else None,
            secondary_resource_id=raw_camera_id,
        )

        if raw_camera_id:
            _LOGGER.debug(
                "camera_id %s selects the console owning chime %s; volume"
                " applies to the whole chime",
                raw_camera_id,
                chime_id,
            )
        _LOGGER.info("Setting chime %s volume to %d%%", chime_id, volume)
        await coordinator.async_set_chime_volume(chime_id, volume)

    async def async_handle_play_chime_ringtone(call: ServiceCall) -> None:
        """Handle the play_chime_ringtone service call."""
        raw_chime_id = _extract_target_id(call, "chime_id")
        if not raw_chime_id:
            msg = "Chime ID or target is required"
            raise ServiceValidationError(msg)

        coordinator, chime_id = _get_coordinator_for_protect_resource(
            hass, resource_type="chime", resource_id=raw_chime_id
        )

        await coordinator.async_play_chime(chime_id)

    async def async_handle_set_chime_ringtone(call: ServiceCall) -> None:
        """Handle the set_chime_ringtone service call."""
        raw_chime_id = _extract_target_id(call, "chime_id")
        if not raw_chime_id:
            msg = "Chime ID or target is required"
            raise ServiceValidationError(msg)
        ringtone_id = call.data["ringtone_id"]
        raw_camera_id = call.data.get("camera_id")
        if isinstance(raw_camera_id, list):
            raw_camera_id = raw_camera_id[0] if raw_camera_id else None
        if isinstance(raw_camera_id, str):
            raw_camera_id = raw_camera_id.strip() or None

        coordinator, chime_id = _get_coordinator_for_protect_resource(
            hass,
            resource_type="chime",
            resource_id=raw_chime_id,
            secondary_resource_type="camera" if raw_camera_id else None,
            secondary_resource_id=raw_camera_id,
        )

        if raw_camera_id:
            _LOGGER.debug(
                "camera_id %s selects the console owning chime %s; ringtone"
                " applies to the whole chime",
                raw_camera_id,
                chime_id,
            )
        _LOGGER.info("Setting chime %s ringtone to %s", chime_id, ringtone_id)
        await coordinator.async_set_chime_ringtone(chime_id, ringtone_id)

    async def async_handle_set_chime_repeat_times(call: ServiceCall) -> None:
        """Handle the set_chime_repeat_times service call."""
        raw_chime_id = _extract_target_id(call, "chime_id")
        if not raw_chime_id:
            msg = "Chime ID or target is required"
            raise ServiceValidationError(msg)
        repeat_times = call.data["repeat_times"]
        raw_camera_id = call.data.get("camera_id")
        if isinstance(raw_camera_id, list):
            raw_camera_id = raw_camera_id[0] if raw_camera_id else None
        if isinstance(raw_camera_id, str):
            raw_camera_id = raw_camera_id.strip() or None

        coordinator, chime_id = _get_coordinator_for_protect_resource(
            hass,
            resource_type="chime",
            resource_id=raw_chime_id,
            secondary_resource_type="camera" if raw_camera_id else None,
            secondary_resource_id=raw_camera_id,
        )

        if raw_camera_id:
            _LOGGER.debug(
                "camera_id %s selects the console owning chime %s; repeat times"
                " apply to the whole chime",
                raw_camera_id,
                chime_id,
            )
        _LOGGER.info("Setting chime %s repeat times to %d", chime_id, repeat_times)
        await coordinator.async_set_chime_repeat(chime_id, repeat_times)

    async def async_handle_set_chime_paired_doorbells(call: ServiceCall) -> None:
        """Handle the set_chime_paired_doorbells service call."""
        raw_chime_id = _extract_target_id(call, "chime_id")
        if not raw_chime_id:
            msg = "Chime ID or target is required"
            raise ServiceValidationError(msg)

        coordinator, chime_id = _get_coordinator_for_protect_resource(
            hass,
            resource_type="chime",
            resource_id=raw_chime_id,
        )
        camera_ids = _resolve_chime_doorbell_camera_ids(
            hass,
            call.data.get("doorbells"),
            chime_entry_id=_entry_id_for_coordinator(hass, coordinator),
            coordinator=coordinator,
        )
        _LOGGER.info("Pairing chime %s with doorbell cameras %s", chime_id, camera_ids)
        await coordinator.async_set_chime_paired_cameras(chime_id, camera_ids)

    async def async_handle_authorize_guest(call: ServiceCall) -> None:
        """Handle the authorize_guest service call."""
        site_id = call.data["site_id"]
        raw_client_id = _extract_target_id(call, "client_id")
        if not raw_client_id:
            msg = "Client ID or target is required"
            raise ServiceValidationError(msg)

        ignored = [
            key
            for key in (
                "duration_minutes",
                "upload_limit_kbps",
                "download_limit_kbps",
                "data_limit_mb",
            )
            if call.data.get(key) is not None
        ]
        if ignored:
            _LOGGER.warning(
                "authorize_guest ignores unsupported option(s) %s; the UniFi "
                "Integration API authorizes guests using the network's default "
                "guest policy",
                ", ".join(ignored),
            )

        coordinator, client_id = _get_coordinator_for_network_resource(
            hass, site_id=site_id, client_id=raw_client_id
        )

        # Trackers identify clients by MAC; the Integration API needs the
        # native client ID from the selected site's cached client records.
        client_id = _resolve_native_client_id(coordinator, site_id, client_id)

        await coordinator.async_authorize_guest(site_id, client_id)

    async def async_handle_reconnect_client(call: ServiceCall) -> None:
        """Handle the reconnect_client service call."""
        await _async_handle_reconnect_client(hass, call)

    async def async_handle_remove_clients(call: ServiceCall) -> None:
        """Handle the remove_clients service call."""
        await _async_handle_remove_clients(hass, call)

    async def async_handle_generate_voucher(call: ServiceCall) -> None:
        """Handle the generate_voucher service call."""
        site_id = call.data["site_id"]
        count = call.data.get("count", 1)
        time_limit_minutes = call.data["duration_minutes"]
        upload_limit_kbps = call.data.get("upload_limit_kbps")
        download_limit_kbps = call.data.get("download_limit_kbps")
        data_limit_mb = call.data.get("data_limit_mb")
        # The Network API requires a voucher name.
        name = call.data.get("note") or DEFAULT_VOUCHER_NAME

        coordinator, _ = _get_coordinator_for_network_resource(hass, site_id=site_id)

        await coordinator.async_generate_voucher(
            site_id,
            count=count,
            time_limit_minutes=time_limit_minutes,
            tx_rate_limit_kbps=upload_limit_kbps,
            rx_rate_limit_kbps=download_limit_kbps,
            data_usage_limit_mbytes=data_limit_mb,
            name=name,
        )

    async def async_handle_delete_voucher(call: ServiceCall) -> None:
        """Handle the delete_voucher service call."""
        site_id = call.data["site_id"]
        voucher_id = call.data["voucher_id"]

        coordinator, _ = _get_coordinator_for_network_resource(hass, site_id=site_id)

        await coordinator.async_delete_voucher(site_id, voucher_id)

    async def async_handle_trigger_alarm(call: ServiceCall) -> None:
        """Handle the trigger_alarm service call."""
        alarm_id = call.data["alarm_id"]

        coordinator, _ = _get_coordinator_for_protect_resource(
            hass, console_id=call.data.get("console_id")
        )

        await coordinator.async_trigger_alarm(alarm_id)

    async def async_handle_create_liveview(call: ServiceCall) -> None:
        """Handle the create_liveview service call."""
        name = call.data["name"]
        layout = call.data["layout"]
        is_default = call.data.get("is_default", False)

        coordinator, _ = _get_coordinator_for_protect_resource(
            hass, console_id=call.data.get("console_id")
        )

        await coordinator.async_create_liveview(
            name=name,
            layout=layout,
            is_default=is_default,
        )

    async def async_handle_set_liveview(call: ServiceCall) -> None:
        """Handle the set_liveview service call."""
        raw_viewer_id = _extract_target_id(call, "viewer_id")
        if not raw_viewer_id:
            msg = "Viewer ID or target is required"
            raise ServiceValidationError(msg)
        liveview_id = call.data["liveview_id"]

        coordinator, viewer_id = _get_coordinator_for_protect_resource(
            hass,
            resource_type="viewer",
            resource_id=raw_viewer_id,
            secondary_resource_type="liveview",
            secondary_resource_id=liveview_id,
        )

        await coordinator.async_update_viewer(viewer_id, liveview=liveview_id)

    # Register chime services
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CHIME_VOLUME,
        async_handle_set_chime_volume,
        schema=SET_CHIME_VOLUME_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_PLAY_CHIME_RINGTONE,
        async_handle_play_chime_ringtone,
        schema=PLAY_CHIME_RINGTONE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CHIME_RINGTONE,
        async_handle_set_chime_ringtone,
        schema=SET_CHIME_RINGTONE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CHIME_REPEAT_TIMES,
        async_handle_set_chime_repeat_times,
        schema=SET_CHIME_REPEAT_TIMES_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CHIME_PAIRED_DOORBELLS,
        async_handle_set_chime_paired_doorbells,
        schema=SET_CHIME_PAIRED_DOORBELLS_SCHEMA,
    )

    # Register UniFi Network services
    hass.services.async_register(
        DOMAIN,
        SERVICE_AUTHORIZE_GUEST,
        async_handle_authorize_guest,
        schema=AUTHORIZE_GUEST_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RECONNECT_CLIENT,
        async_handle_reconnect_client,
        schema=RECONNECT_CLIENT_SCHEMA,
    )

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_REMOVE_CLIENTS,
        async_handle_remove_clients,
        schema=REMOVE_CLIENTS_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_GENERATE_VOUCHER,
        async_handle_generate_voucher,
        schema=GENERATE_VOUCHER_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_VOUCHER,
        async_handle_delete_voucher,
        schema=DELETE_VOUCHER_SCHEMA,
    )

    async def async_handle_export_network_rules(call: ServiceCall) -> ServiceResponse:
        """Handle the export_network_rules service call."""
        return await _async_handle_export_network_rules(hass, call)

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_EXPORT_NETWORK_RULES,
        async_handle_export_network_rules,
        schema=EXPORT_NETWORK_RULES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    # Register UniFi Protect services
    hass.services.async_register(
        DOMAIN,
        SERVICE_TRIGGER_ALARM,
        async_handle_trigger_alarm,
        schema=TRIGGER_ALARM_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_LIVEVIEW,
        async_handle_create_liveview,
        schema=CREATE_LIVEVIEW_SCHEMA,
    )

    async def async_handle_carrier_suspend_subscriber(call: ServiceCall) -> None:
        """Handle carrier_suspend_subscriber service call."""
        await _async_handle_carrier_suspend_subscriber(hass, call)

    async def async_handle_carrier_resume_subscriber(call: ServiceCall) -> None:
        """Handle carrier_resume_subscriber service call."""
        await _async_handle_carrier_resume_subscriber(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_LIVEVIEW,
        async_handle_set_liveview,
        schema=SET_LIVEVIEW_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CARRIER_SUSPEND_SUBSCRIBER,
        async_handle_carrier_suspend_subscriber,
        schema=CARRIER_SUSPEND_SUBSCRIBER_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CARRIER_RESUME_SUBSCRIBER,
        async_handle_carrier_resume_subscriber,
        schema=CARRIER_RESUME_SUBSCRIBER_SCHEMA,
    )

    # Admin-only: a PIN is a credential, so non-admin callers are refused by HA.
    async def async_handle_set_access_user_pin(call: ServiceCall) -> None:
        """Assign a user-chosen PIN through the console that owns the user."""
        access_coordinator = _get_access_coordinator(
            hass, console_id=call.data.get("console_id")
        )
        await _async_run_access_user_action(
            access_coordinator.async_set_user_pin(
                call.data["user_id"], call.data["pin"]
            )
        )

    async def async_handle_remove_access_user_pin(call: ServiceCall) -> None:
        """Remove a user's PIN through the console that owns the user."""
        access_coordinator = _get_access_coordinator(
            hass, console_id=call.data.get("console_id")
        )
        await _async_run_access_user_action(
            access_coordinator.async_remove_user_pin(call.data["user_id"])
        )

    async def async_handle_set_access_user_status(call: ServiceCall) -> None:
        """Set a user account to ACTIVE or DEACTIVATED; this is account-wide."""
        access_coordinator = _get_access_coordinator(
            hass, console_id=call.data.get("console_id")
        )
        await _async_run_access_user_action(
            access_coordinator.async_set_user_status(
                call.data["user_id"], call.data["status"]
            )
        )

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_SET_ACCESS_USER_PIN,
        async_handle_set_access_user_pin,
        schema=SET_ACCESS_USER_PIN_SCHEMA,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_REMOVE_ACCESS_USER_PIN,
        async_handle_remove_access_user_pin,
        schema=REMOVE_ACCESS_USER_PIN_SCHEMA,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_SET_ACCESS_USER_STATUS,
        async_handle_set_access_user_status,
        schema=SET_ACCESS_USER_STATUS_SCHEMA,
    )

    _LOGGER.info("UniFi Insights services registered successfully")


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload UniFi Insights services."""
    _LOGGER.debug("Unloading UniFi Insights services")

    # Unload core services
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH_DATA):
        hass.services.async_remove(DOMAIN, SERVICE_REFRESH_DATA)

    if hass.services.has_service(DOMAIN, SERVICE_RESTART_DEVICE):
        hass.services.async_remove(DOMAIN, SERVICE_RESTART_DEVICE)

    if hass.services.has_service(DOMAIN, SERVICE_POWER_CYCLE_PORT):
        hass.services.async_remove(DOMAIN, SERVICE_POWER_CYCLE_PORT)

    if hass.services.has_service(DOMAIN, SERVICE_SET_HDR_MODE):
        hass.services.async_remove(DOMAIN, SERVICE_SET_HDR_MODE)

    if hass.services.has_service(DOMAIN, SERVICE_SET_VIDEO_MODE):
        hass.services.async_remove(DOMAIN, SERVICE_SET_VIDEO_MODE)

    if hass.services.has_service(DOMAIN, SERVICE_SET_MIC_VOLUME):
        hass.services.async_remove(DOMAIN, SERVICE_SET_MIC_VOLUME)

    if hass.services.has_service(DOMAIN, SERVICE_SET_LIGHT_MODE):
        hass.services.async_remove(DOMAIN, SERVICE_SET_LIGHT_MODE)

    if hass.services.has_service(DOMAIN, SERVICE_SET_LIGHT_LEVEL):
        hass.services.async_remove(DOMAIN, SERVICE_SET_LIGHT_LEVEL)

    if hass.services.has_service(DOMAIN, SERVICE_PTZ_MOVE):
        hass.services.async_remove(DOMAIN, SERVICE_PTZ_MOVE)

    if hass.services.has_service(DOMAIN, SERVICE_PTZ_PATROL):
        hass.services.async_remove(DOMAIN, SERVICE_PTZ_PATROL)

    # Unload chime services
    if hass.services.has_service(DOMAIN, SERVICE_SET_CHIME_VOLUME):
        hass.services.async_remove(DOMAIN, SERVICE_SET_CHIME_VOLUME)

    if hass.services.has_service(DOMAIN, SERVICE_PLAY_CHIME_RINGTONE):
        hass.services.async_remove(DOMAIN, SERVICE_PLAY_CHIME_RINGTONE)

    if hass.services.has_service(DOMAIN, SERVICE_SET_CHIME_RINGTONE):
        hass.services.async_remove(DOMAIN, SERVICE_SET_CHIME_RINGTONE)

    if hass.services.has_service(DOMAIN, SERVICE_SET_CHIME_REPEAT_TIMES):
        hass.services.async_remove(DOMAIN, SERVICE_SET_CHIME_REPEAT_TIMES)

    if hass.services.has_service(DOMAIN, SERVICE_SET_CHIME_PAIRED_DOORBELLS):
        hass.services.async_remove(DOMAIN, SERVICE_SET_CHIME_PAIRED_DOORBELLS)

    # Unload UniFi Network services
    if hass.services.has_service(DOMAIN, SERVICE_AUTHORIZE_GUEST):
        hass.services.async_remove(DOMAIN, SERVICE_AUTHORIZE_GUEST)

    if hass.services.has_service(DOMAIN, SERVICE_RECONNECT_CLIENT):
        hass.services.async_remove(DOMAIN, SERVICE_RECONNECT_CLIENT)

    if hass.services.has_service(DOMAIN, SERVICE_REMOVE_CLIENTS):
        hass.services.async_remove(DOMAIN, SERVICE_REMOVE_CLIENTS)

    if hass.services.has_service(DOMAIN, SERVICE_GENERATE_VOUCHER):
        hass.services.async_remove(DOMAIN, SERVICE_GENERATE_VOUCHER)

    if hass.services.has_service(DOMAIN, SERVICE_DELETE_VOUCHER):
        hass.services.async_remove(DOMAIN, SERVICE_DELETE_VOUCHER)

    if hass.services.has_service(DOMAIN, SERVICE_EXPORT_NETWORK_RULES):
        hass.services.async_remove(DOMAIN, SERVICE_EXPORT_NETWORK_RULES)

    # Unload UniFi Protect services
    if hass.services.has_service(DOMAIN, SERVICE_TRIGGER_ALARM):
        hass.services.async_remove(DOMAIN, SERVICE_TRIGGER_ALARM)

    if hass.services.has_service(DOMAIN, SERVICE_CREATE_LIVEVIEW):
        hass.services.async_remove(DOMAIN, SERVICE_CREATE_LIVEVIEW)

    if hass.services.has_service(DOMAIN, SERVICE_SET_LIVEVIEW):
        hass.services.async_remove(DOMAIN, SERVICE_SET_LIVEVIEW)

    if hass.services.has_service(DOMAIN, SERVICE_CARRIER_SUSPEND_SUBSCRIBER):
        hass.services.async_remove(DOMAIN, SERVICE_CARRIER_SUSPEND_SUBSCRIBER)

    if hass.services.has_service(DOMAIN, SERVICE_CARRIER_RESUME_SUBSCRIBER):
        hass.services.async_remove(DOMAIN, SERVICE_CARRIER_RESUME_SUBSCRIBER)

    # Unload UniFi Access user services
    for service in (
        SERVICE_SET_ACCESS_USER_PIN,
        SERVICE_REMOVE_ACCESS_USER_PIN,
        SERVICE_SET_ACCESS_USER_STATUS,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)

    _LOGGER.info("UniFi Insights services unloaded successfully")
