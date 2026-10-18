# Copyright 2026 UniFi Insights contributors
"""Base coordinator for UniFi Insights."""

from __future__ import annotations

import logging
from datetime import timedelta  # noqa: TC003
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from custom_components.unifi_insights.api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiRateLimitError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from custom_components.unifi_insights.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.unifi_insights.api.network import UniFiNetworkClient
    from custom_components.unifi_insights.api.protect import UniFiProtectClient

_LOGGER = logging.getLogger(__name__)


class UnifiBaseCoordinator(DataUpdateCoordinator[dict[str, Any]]):
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
        body is not JSON: api/base.py raises that for consoles that answer an
        endpoint they lack with the web UI's HTML page (see the sites fetch
        in coordinators/config.py). Both are valid, stable answers, so the
        caller treats them as "no data" instead of a failure. A 401 is
        excluded because it means the credentials themselves were rejected
        and must trigger reauth, and 429 is excluded because rate limiting is
        transient.
        """
        if isinstance(err, UniFiAuthenticationError):
            return err.status_code == HTTPStatus.FORBIDDEN
        if isinstance(err, UniFiRateLimitError):
            return False
        if isinstance(err, UniFiResponseError):
            return (
                err.status_code < HTTPStatus.MULTIPLE_CHOICES
                or HTTPStatus.BAD_REQUEST
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
