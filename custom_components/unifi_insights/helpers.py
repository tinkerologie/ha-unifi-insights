# Copyright 2026 UniFi Insights contributors
"""Helper utilities for UniFi Insights."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from homeassistant.const import CONF_API_KEY
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .console_identity import (
    build_legacy_site_unique_id,
    build_site_unique_id,
    normalize_console_namespace,
    resolve_console_namespace,
)
from .const import (
    CAMERA_TYPE_DOORBELL,
    CAMERA_TYPE_DOORBELL_MAIN,
    CAMERA_TYPE_DOORBELL_PACKAGE,
    CAMERA_TYPE_DOORBELL_WITH_PACKAGE_DETECTION,
    CONF_CONSOLE_ID,
    DOMAIN,
    SITE_ENTITY_SUFFIXES,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# HA 2026.8 added identifier/connection lookups scoped to a config entry, and
# HA 2026.9 deprecated DeviceRegistry.async_get_device (removal in 2027.8).
# Capability is probed on the class rather than the instance so that test
# doubles follow whatever the installed HA actually supports, instead of
# MagicMock's auto-attributes selecting a branch that does not exist at runtime.
_SUPPORTS_BY_IDENTIFIER = hasattr(dr.DeviceRegistry, "async_get_device_by_identifier")
_SUPPORTS_GET_DEVICES = hasattr(dr.DeviceRegistry, "async_get_devices")


def get_port_label(port: dict[str, Any], port_idx: int) -> str:
    """Return user-friendly port label based on port type."""
    name = port.get("name")
    if name and name != f"Port {port_idx}":
        return str(name)
    media = port.get("media")
    if isinstance(media, str) and media.startswith("SFP"):
        return f"{media} {port_idx}"
    return f"Port {port_idx}"


def owned_site_entity_unique_id(
    registry: er.EntityRegistry,
    entry_id: str,
    domain: str,
    legacy_unique_id: str,
    unique_id: str | None,
    *,
    translation_key: str,
) -> str | None:
    """Select an owned logical identity, never reusing physical or ambiguous rows."""
    legacy_entity_id = registry.async_get_entity_id(domain, DOMAIN, legacy_unique_id)
    if legacy_entity_id:
        legacy = registry.async_get(legacy_entity_id)
        if legacy is not None and legacy.config_entry_id == entry_id:
            if legacy.translation_key == translation_key:
                return legacy_unique_id
            if not legacy.translation_key:
                _LOGGER.warning(
                    "Skipping a site entity with ambiguous registry metadata"
                )
                return None
            _LOGGER.debug(
                "Leaving a legacy entity outside the logical-site family unchanged"
            )
        else:
            _LOGGER.debug("Leaving a foreign legacy site entity unchanged")
    if unique_id is None:
        return None
    entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
    if entity_id:
        entity = registry.async_get(entity_id)
        if entity is None or entity.config_entry_id != entry_id:
            return None
        if entity.translation_key != translation_key:
            _LOGGER.warning(
                "Skipping a site entity with incompatible registry metadata"
            )
            return None
    return unique_id


def async_migrate_site_entity(
    hass: HomeAssistant,
    entry: ConfigEntry,
    domain: str,
    namespace: str | None,
    site_id: str,
    *,
    suffix: str,
) -> str | None:
    """Migrate an owned legacy row, returning the identity safe to construct."""
    registry = er.async_get(hass)
    legacy = build_legacy_site_unique_id(site_id, suffix)
    target = build_site_unique_id(namespace, site_id, suffix) if namespace else None
    effective = owned_site_entity_unique_id(
        registry, entry.entry_id, domain, legacy, target, translation_key=suffix
    )
    if effective != legacy or target is None or target == legacy:
        return effective
    if registry.async_get_entity_id(domain, DOMAIN, target):
        _LOGGER.warning(
            "Retaining an owned legacy site entity: target identity is occupied"
        )
        return legacy
    entity_id = registry.async_get_entity_id(domain, DOMAIN, legacy)
    if entity_id is None:
        return None
    try:
        registry.async_update_entity(entity_id, new_unique_id=target)
    except ValueError:
        _LOGGER.warning(
            "Site entity registry update failed; keeping an eligible owned identity"
        )
        effective = owned_site_entity_unique_id(
            registry, entry.entry_id, domain, legacy, target, translation_key=suffix
        )
        if effective is None:
            return None
        # After a native post-mutation failure, construct only an existing owned
        # identity. A missing registry row must not recreate a fresh legacy row.
        return (
            effective
            if registry.async_get_entity_id(domain, DOMAIN, effective)
            else None
        )
    return target


class SiteEntityIdentityError(ValueError):
    """A console identity change cannot preserve logical-site registry rows."""


type SiteEntityIdentityUpdates = tuple[tuple[er.RegistryEntry, str], ...]


def async_restore_site_entity_identities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    updates: SiteEntityIdentityUpdates,
) -> bool:
    """Restore exact owned rows after a synchronous transition failure."""
    registry = er.async_get(hass)
    restored = True
    for previous, target in reversed(updates):
        try:
            current = registry.async_get(previous.entity_id)
            if (
                current is None
                or current.id != previous.id
                or current.config_entry_id != entry.entry_id
                or current.domain != previous.domain
                or current.platform != previous.platform
                or current.translation_key != previous.translation_key
            ):
                restored = False
                continue
            if current.unique_id == previous.unique_id:
                continue
            occupied = registry.async_get_entity_id(
                previous.domain, DOMAIN, previous.unique_id
            )
            if current.unique_id != target or (
                occupied is not None and occupied != current.entity_id
            ):
                restored = False
                continue
            registry.async_update_entity(
                current.entity_id, new_unique_id=previous.unique_id
            )
        except Exception:
            # A native update may raise after mutation. Continue attempting every
            # other row without claiming that save scheduling succeeded.
            restored = False
    if not restored:
        _LOGGER.warning("Unable to fully roll back logical-site registry identities")
    return restored


def async_rekey_site_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    new_console_id: Any,
    *,
    pending_api_key: Any = None,
) -> SiteEntityIdentityUpdates:
    """Preflight and re-key exact families, retaining safe compensation records."""
    old = resolve_console_namespace(entry)
    new = normalize_console_namespace(
        new_console_id,
        credentials=(entry.data.get(CONF_API_KEY), pending_api_key),
    )
    if new is None:
        if new_console_id in (None, "") and old is None:
            return ()
        err_msg = "A safe console namespace is required"
        raise SiteEntityIdentityError(err_msg)
    if (
        old is not None
        and normalize_console_namespace(
            entry.data.get(CONF_CONSOLE_ID),
            credentials=(entry.data.get(CONF_API_KEY), pending_api_key),
        )
        is None
    ):
        err_msg = "The pending credential conflicts with console identity"
        raise SiteEntityIdentityError(err_msg)
    if old is None or old == new:
        return ()
    registry = er.async_get(hass)
    updates: list[tuple[er.RegistryEntry, str]] = []
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        suffixes = SITE_ENTITY_SUFFIXES.get(entity.domain)
        if entity.platform != DOMAIN or suffixes is None:
            continue
        prefix = f"{old}_"
        if not entity.unique_id.startswith(prefix):
            continue
        remainder = entity.unique_id[len(prefix) :]
        possible_keys = [key for key in suffixes if remainder.endswith(f"_{key}")]
        key = entity.translation_key
        if key not in suffixes:
            if not key and possible_keys:
                err_msg = "Logical-site registry metadata is missing"
                raise SiteEntityIdentityError(err_msg)
            continue
        ending = f"_{key}"
        site_id = remainder[: -len(ending)] if remainder.endswith(ending) else ""
        if not site_id or entity.unique_id != build_site_unique_id(old, site_id, key):
            err_msg = "Logical-site registry metadata is ambiguous"
            raise SiteEntityIdentityError(err_msg)
        target = build_site_unique_id(new, site_id, key)
        if registry.async_get_entity_id(entity.domain, DOMAIN, target):
            err_msg = "Logical-site target identity is occupied"
            raise SiteEntityIdentityError(err_msg)
        updates.append((entity, target))

    attempted: list[tuple[er.RegistryEntry, str]] = []
    try:
        for entity, target in updates:
            attempted.append((entity, target))
            registry.async_update_entity(entity.entity_id, new_unique_id=target)
    except Exception:
        async_restore_site_entity_identities(hass, entry, tuple(attempted))
        raise
    return tuple(attempted)


def is_doorbell_camera_model(camera_data: dict[str, Any]) -> bool:
    """
    Return whether a cached camera record says, by its model, that it is a doorbell.

    This is the evidence a camera's type gives, as opposed to its name: a
    camera called "Front Door" is not a doorbell because of that.
    """
    camera_type = camera_data.get("_camera_type") or ""
    if camera_type in (
        CAMERA_TYPE_DOORBELL,
        CAMERA_TYPE_DOORBELL_WITH_PACKAGE_DETECTION,
        CAMERA_TYPE_DOORBELL_MAIN,
        CAMERA_TYPE_DOORBELL_PACKAGE,
    ):
        return True

    # The camera type field from the API (handle None safely)
    api_camera_type = (camera_data.get("type") or "").lower()
    return any(
        doorbell_type in api_camera_type
        for doorbell_type in (
            "doorbell",
            "g4-doorbell",
            "ai-doorbell",
            "g4doorbell",
            "aidoorbell",
        )
    )


def async_get_device_entry(
    device_registry: dr.DeviceRegistry,
    identifier: tuple[str, str],
    config_entry_id: str | None = None,
) -> dr.DeviceEntry | None:
    """
    Look up a device entry by identifier, scoped to a config entry when possible.

    Each branch is authoritative: a miss returns None rather than falling
    through to the next lookup. Falling through would call the deprecated
    async_get_device on every miss, which is exactly the common case for the
    stale-device cleanup paths this helper serves.
    """
    # Accessed through Any so this type-checks against both an HA that predates
    # these methods and one that has them; a `type: ignore` would instead turn
    # into an unused-ignore error the moment HA is upgraded.
    registry = cast("Any", device_registry)

    if _SUPPORTS_BY_IDENTIFIER and config_entry_id is not None:
        device: dr.DeviceEntry | None = registry.async_get_device_by_identifier(
            identifier, config_entry_id
        )
        return device

    if _SUPPORTS_GET_DEVICES:
        devices: list[dr.DeviceEntry] = registry.async_get_devices(
            identifiers={identifier}
        )
        return devices[0] if devices else None

    return device_registry.async_get_device(identifiers={identifier})
