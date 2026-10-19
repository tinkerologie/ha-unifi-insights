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

from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_API_KEY

from .const import CONF_CONSOLE_ID, CONSOLE_DEVICE_TOKENS

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.config_entries import ConfigEntry

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
