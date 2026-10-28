# Copyright 2026 UniFi Insights contributors
"""Base coordinator for UniFi Insights."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta  # noqa: TC003
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiRateLimitError,
    UniFiResponseError,
    UniFiTimeoutError,
)

from custom_components.unifi_insights.console_identity import async_get_device_entry
from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.probe import is_malformed_response

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from unifi_official_api.network import UniFiNetworkClient
    from unifi_official_api.protect import UniFiProtectClient

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True, slots=True)
class CommandFailure:
    """
    How a failed write command is reported to the user.

    ``translation_key`` and ``translation_placeholders`` name the
    ``exceptions`` entry raised when the call fails. Placeholders must be safe
    identifiers (site, device or rule IDs), never raw exception text, response
    bodies or MAC addresses.

    ``message`` is an optional fixed English message. Translated errors
    normally generate their text from the key; callers that predate the
    translation keep their established wording through it.

    ``rejected_translation_key``, when set, turns a falsy result (``False``,
    ``None`` or an empty collection) into that translated error, for API
    calls whose result is the only sign that the console refused the write.
    """

    translation_key: str
    translation_placeholders: Mapping[str, str] = field(default_factory=dict)
    message: str | None = None
    rejected_translation_key: str | None = None


def _command_error(
    *,
    translation_key: str,
    translation_placeholders: Mapping[str, str],
    message: str | None,
) -> HomeAssistantError:
    """Build a translated command error, keeping a fixed message if given."""
    args = (message,) if message else ()
    return HomeAssistantError(
        *args,
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders=dict(translation_placeholders),
    )


async def async_execute_command[**P, T](
    failure: CommandFailure,
    action: Callable[P, Awaitable[T]],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    """
    Run one write command and translate its failure.

    The single error boundary for command owners (ADR-010). It holds no
    state: it registers no listener, schedules no refresh and looks nothing
    up by name, so the facade (which does not inherit this module's base
    class) and every domain coordinator can call it directly.

    A ``HomeAssistantError`` from the call passes through unchanged, so an
    owner's own translated validation survives. Any other exception is
    wrapped in the translated ``failure`` error raised ``from`` it, keeping
    the API error as the immediate cause for callers that inspect it. A
    successful answer whose record failed to parse (``UniFiResponseError``
    with status 200, see ``is_malformed_response``) is the exception: it
    raises ``invalid_api_response`` instead. The library's message names the
    failing fields only, never their values (a voucher code, for example), so
    it is logged without a traceback.

    The failure is bundled into one object so that ``action``'s own
    arguments (``state=``, ``enabled=`` ...) can never collide with the
    executor's options, and mypy checks them against ``action``.
    """
    try:
        result = await action(*args, **kwargs)
    except HomeAssistantError:
        raise
    except Exception as err:
        if is_malformed_response(err):
            context = failure.message or failure.translation_key
            _LOGGER.error(  # noqa: TRY400
                "%s: Invalid API response data (%s)", context, err.message
            )
            message = (
                f"{failure.message}: Invalid API response data ({err.message})"
                if failure.message
                else None
            )
            raise _command_error(
                translation_key="invalid_api_response",
                translation_placeholders={},
                message=message,
            ) from err
        _LOGGER.exception("%s", failure.message or failure.translation_key)
        raise _command_error(
            translation_key=failure.translation_key,
            translation_placeholders=failure.translation_placeholders,
            message=failure.message,
        ) from err
    if failure.rejected_translation_key is not None and not result:
        raise _command_error(
            translation_key=failure.rejected_translation_key,
            translation_placeholders=failure.translation_placeholders,
            message=None,
        )
    return result


class UnifiInsightsBaseCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Base class for UniFi Insights coordinators."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        network_client: UniFiNetworkClient,
        protect_client: UniFiProtectClient | None,
        entry: ConfigEntry,
        name: str,
        *,
        update_interval: timedelta,
    ) -> None:
        """Initialize the base coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{name}",
            update_interval=update_interval,
        )
        self.network_client = network_client
        self.protect_client = protect_client
        self._available = True
        self.data: dict[str, Any] = {}

    @property
    def available(self) -> bool:
        """Return coordinator availability."""
        return self._available

    def _remove_stale_device(self, *identifiers: str) -> bool:
        """
        Remove a device the console no longer reports from this config entry.

        Each identifier is tried in order, and the first device registered
        under it in this entry is removed. Returns whether a device was found.
        Callers decide when a device counts as stale.
        """
        device_registry = dr.async_get(self.hass)
        for identifier in identifiers:
            device = async_get_device_entry(
                device_registry, (DOMAIN, identifier), self.config_entry.entry_id
            )
            if device is None:
                continue
            _LOGGER.info("%s: removing stale device %s", self.name, identifier)
            # A device belongs to exactly one config entry (HA 2026.10+), so
            # remove it outright; remove_config_entry_id is deprecated.
            device_registry.async_remove_device(device.id)
            return True
        return False

    def _model_to_dict(self, model: Any) -> dict[str, Any]:
        """
        Convert a model object to a dictionary.

        Handles both pydantic models (with model_dump) and objects with __dict__.
        Uses by_alias=True to get camelCase field names that match the
        original API format.
        """
        if model is None:
            return {}
        if isinstance(model, dict):
            return model
        if hasattr(model, "model_dump"):
            # Use by_alias=True to get camelCase field names matching the API response
            # This ensures compatibility with entity code expecting camelCase keys
            try:
                result = model.model_dump(by_alias=True, exclude_none=False)
                return result if isinstance(result, dict) else {}
            except TypeError:
                # Fall back to regular model_dump if by_alias not supported
                result = model.model_dump()
                return result if isinstance(result, dict) else {}
        if hasattr(model, "__dict__"):
            return {k: v for k, v in model.__dict__.items() if not k.startswith("_")}
        return {}

    @staticmethod
    def _is_unsupported_response(err: Exception) -> bool:
        """
        Return True when an optional endpoint is simply not offered to this key.

        A 403, a 404 or another 4xx client error from an optional feature
        endpoint (WiFi, firewall policies, per-device statistics) means this
        console or API key does not expose that feature. So does a 2xx whose
        body is not JSON: the library raises that, with the body attached, for
        consoles that answer an endpoint they lack with the web UI's HTML
        page (see the sites fetch in coordinators/config.py). Both are valid,
        stable answers, so the caller treats them as "no data" instead of a
        failure. A 2xx error without a body is a JSON answer that holds no
        record or a record that failed to parse (``unifi-official-api`` 3.0.0
        raises ``UniFiNotFoundError`` / ``UniFiResponseError`` with status
        200 where 2.x raised ``ValueError`` or pydantic's ``ValidationError``):
        a failure, so the last known data is kept. A 401 is excluded because
        it means the credentials themselves were rejected and must trigger
        reauth, and 429 is excluded because rate limiting is transient.
        """
        if isinstance(err, UniFiAuthenticationError):
            return err.status_code == HTTPStatus.FORBIDDEN
        if isinstance(err, UniFiRateLimitError):
            return False
        if isinstance(err, UniFiResponseError):
            if err.status_code < HTTPStatus.MULTIPLE_CHOICES:
                return err.response_body is not None
            return (
                HTTPStatus.BAD_REQUEST
                <= err.status_code
                < HTTPStatus.INTERNAL_SERVER_ERROR
            )
        return False

    def _handle_auth_error(self, err: UniFiAuthenticationError) -> None:
        """Handle authentication error."""
        self._available = False
        msg = f"Authentication failed: {err}"
        raise ConfigEntryAuthFailed(msg) from err

    def _handle_connection_error(self, err: UniFiConnectionError) -> None:
        """Handle connection error."""
        self._available = False
        msg = f"Error communicating with API: {err}"
        raise UpdateFailed(msg) from err

    def _handle_timeout_error(self, err: UniFiTimeoutError) -> None:
        """Handle timeout error."""
        self._available = False
        msg = f"Timeout: {err}"
        raise UpdateFailed(msg) from err

    def _handle_response_error(self, err: UniFiResponseError) -> None:
        """Handle API response error."""
        self._available = False
        status_code = getattr(err, "status_code", None)
        if not (
            isinstance(status_code, int)
            and status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
        ):
            _LOGGER.debug("API error during update", exc_info=err)
        msg = f"API error ({status_code}): {err}"
        raise UpdateFailed(msg) from err

    def _handle_generic_error(self, err: Exception) -> None:
        """Handle generic error."""
        self._available = False
        _LOGGER.debug("Unexpected error updating data", exc_info=err)
        msg = f"Error updating data: {type(err).__name__}: {err}"
        raise UpdateFailed(msg) from err
