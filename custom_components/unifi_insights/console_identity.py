# Copyright 2026 UniFi Insights contributors
"""
Derive a stable identity for the console a config entry talks to.

The config flow and ``async_setup_entry`` both have to answer the same two
questions - which of the Network devices *is* this console, and what should
identify it - but they used to answer them separately and drifted apart. The
flow ignored ``is_gateway``, inspected only the first site's devices, and
treated a ``"default"`` site id differently from setup. Setup could therefore
adopt a MAC the flow was unable to derive, leaving the flow with a host-based
``unique_id`` that ``_abort_if_unique_id_configured()`` no longer matched, so
re-adding one console created a second config entry.

Keeping both callers on these helpers is what stops that drift returning. The
functions take plain dicts because the two callers hold different shapes:
setup reads coordinator data, while the flow dumps its API models with
``model_dump(by_alias=True)``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_API_KEY
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_CONSOLE_ID,
    CONSOLE_DEVICE_TOKENS,
    DOMAIN,
    SITE_ENTITY_SUFFIXES,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# The site id a controller reports when it has only the implicit default site.
# It is identical across consoles, so it can never identify one.
DEFAULT_SITE_ID = "default"


def is_console_device(dev_data: dict[str, Any]) -> bool:
    """Whether a Network device is the console itself (a gateway or Cloud Key)."""
    if dev_data.get("is_gateway"):
        return True
    haystack = " ".join(
        str(dev_data.get(key) or "") for key in ("type", "model")
    ).lower()
    return any(token in haystack for token in CONSOLE_DEVICE_TOKENS)


def normalize_mac(mac: Any) -> str | None:
    """
    Return a MAC in the one form stored as a console id, or None.

    Identity policy: persisted console ids (config entry unique IDs). It only
    lowercases and turns dashes into colons, without validating, and must not
    change or existing entries stop matching. It is deliberately not the
    canonical ``data_transforms.normalize_mac``.
    """
    if not isinstance(mac, str) or not mac:
        return None
    return mac.lower().replace("-", ":")


def normalize_console_namespace(
    value: Any, *, credentials: Iterable[Any] = ()
) -> str | None:
    """Normalize a stored console identity, rejecting credentials and bad values."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed or any(char.isspace() or not char.isprintable() for char in trimmed):
        return None
    credential_values = tuple(
        credential
        for credential in credentials
        if isinstance(credential, str) and credential
    )
    if value in credential_values:
        return None
    namespace = normalize_mac(trimmed)
    if any(
        namespace == normalize_mac(credential.strip())
        for credential in credential_values
    ):
        return None
    return namespace


def resolve_console_namespace(entry: ConfigEntry) -> str | None:
    """Read the canonical namespace without a host or display-name fallback."""
    return normalize_console_namespace(
        entry.data.get(CONF_CONSOLE_ID),
        credentials=(entry.data.get(CONF_API_KEY),),
    )


def build_legacy_site_unique_id(site_id: str, suffix: str) -> str:
    """Build the exact pre-namespace site identity for registry migration."""
    return f"{site_id}_{suffix}"


def build_site_unique_id(namespace: str, site_id: str, suffix: str) -> str:
    """Build a logical-site identity using an already validated namespace."""
    if normalize_console_namespace(namespace) != namespace or not namespace:
        err_msg = "A validated console namespace is required"
        raise ValueError(err_msg)
    return f"{namespace}_{build_legacy_site_unique_id(site_id, suffix)}"


def device_mac(dev_data: dict[str, Any]) -> str | None:
    """Return a device's MAC, accepting either the v1 alias or the raw key."""
    return normalize_mac(dev_data.get("macAddress") or dev_data.get("mac"))


def device_name(dev_data: dict[str, Any]) -> str | None:
    """Return the friendliest name a device offers."""
    for key in ("name", "model"):
        value = dev_data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def device_fields(dev: Any) -> dict[str, Any]:
    """
    Project an API device object onto the plain-dict shape used here.

    Only values of the expected type are copied across. An unguarded
    ``getattr`` would be enough in production, but it would also read a test
    double's auto-created attributes and make every device look like a
    gateway - and the same guard keeps a malformed payload from doing it for
    real.
    """
    fields: dict[str, Any] = {
        key: value
        for key in ("type", "model", "name", "mac", "macAddress")
        if type(value := getattr(dev, key, None)) is str
    }
    if getattr(dev, "is_gateway", None) is True:
        fields["is_gateway"] = True
    return fields


def resolve_console_identity(
    devices: Iterable[dict[str, Any]],
) -> tuple[str | None, str | None]:
    """
    Return ``(mac, name)`` for the first console among ``devices``.

    A device that looks like a console but reports no usable MAC does not end
    the search: another entry may still carry one, and a console identity
    without a MAC is exactly the degraded state this module exists to avoid.
    """
    fallback_name: str | None = None
    for dev_data in devices:
        if not isinstance(dev_data, dict) or not is_console_device(dev_data):
            continue
        mac = device_mac(dev_data)
        if mac is None:
            fallback_name = fallback_name or device_name(dev_data)
            continue
        return mac, device_name(dev_data)
    return None, fallback_name


def first_non_default_site_id(site_ids: Iterable[Any]) -> str | None:
    """
    Return the first non-default site ID in the supplied API order.

    Site IDs can be shared across consoles and do not establish console
    identity. ``"default"`` is skipped because every controller reports it.
    """
    for site_id in site_ids:
        if isinstance(site_id, str) and site_id and site_id != DEFAULT_SITE_ID:
            return site_id
    return None


# --- Site entity identity and registry lookups -------------------------------

# Helper utilities for UniFi Insights.


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


def async_get_device_entry(
    device_registry: dr.DeviceRegistry,
    identifier: tuple[str, str],
    config_entry_id: str | None = None,
) -> dr.DeviceEntry | None:
    """
    Look up a device entry by identifier, scoped to a config entry when given.

    Identifiers are only unique per config entry, and
    ``DeviceRegistry.async_get_device`` is deprecated (removal in 2027.8), so
    the lookup is scoped whenever the owning entry is known.
    """
    if config_entry_id is not None:
        return device_registry.async_get_device_by_identifier(
            identifier, config_entry_id
        )
    devices = device_registry.async_get_devices(identifiers={identifier})
    return devices[0] if devices else None
