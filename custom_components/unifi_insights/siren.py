# Copyright 2026 UniFi Insights contributors
"""Support for UniFi Protect sirens."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.siren import (
    ATTR_DURATION,
    ATTR_VOLUME_LEVEL,
    SirenEntity,
    SirenEntityFeature,
)
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util
from unifi_official_api.protect.endpoints.sirens import (
    SIREN_PLAY_DURATIONS,
    SIREN_VOLUME_MAX,
    SIREN_VOLUME_MIN,
)

from .const import DEVICE_TYPE_SIREN, DOMAIN
from .data_transforms import protect_collection
from .entity import UnifiProtectEntity, async_delegate_entity_id_changed

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import CALLBACK_TYPE, HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import UnifiInsightsConfigEntry
    from .coordinators import UnifiInsightsFacadeCoordinator

_LOGGER = logging.getLogger(__name__)

# Sirens are action-based: run one action at a time
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sirens for UniFi Protect integration."""
    _ = hass
    coordinator: UnifiInsightsFacadeCoordinator = entry.runtime_data.coordinator

    # Skip if Protect API is not available
    if not coordinator.protect_client:
        _LOGGER.debug("Skipping siren setup - Protect API not available")
        return

    known_siren_ids: set[str] = set()

    @callback
    def async_discover_sirens() -> None:
        """Discover and add new sirens."""
        sirens = protect_collection(coordinator.data, "sirens") or {}

        new_entities: list[UnifiProtectSiren] = []
        for siren_id in sirens:
            if siren_id in known_siren_ids:
                continue
            try:
                siren = UnifiProtectSiren(coordinator, siren_id)
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.warning("Skipping siren %s due to error: %s", siren_id, err)
                continue
            # Only after construction succeeds, so a siren that failed on a
            # malformed payload is retried on the next coordinator update.
            known_siren_ids.add(siren_id)
            new_entities.append(siren)

        if new_entities:
            _LOGGER.info("Adding %d UniFi Protect sirens", len(new_entities))
            async_add_entities(new_entities)

    async_discover_sirens()
    entry.async_on_unload(coordinator.async_add_listener(async_discover_sirens))


def siren_volume_from_level(volume_level: float) -> int:
    """
    Convert a Home Assistant volume level (0.0-1.0) to a Protect volume (1-100).

    Rounds like Home Assistant's own UniFi Protect integration, then clamps to
    the API's minimum: Protect rejects a volume of 0, so a level of 0.0 (mute)
    is sent as the quietest volume the siren accepts.
    """
    return max(SIREN_VOLUME_MIN, min(SIREN_VOLUME_MAX, round(volume_level * 100)))


# The most a siren status duration can be, in seconds, if it is not in the
# milliseconds the spec says. See `siren_run_end_ms`.
SIREN_DURATION_SECONDS_CEILING: Final = 60


def siren_run_end_ms(status: dict[str, Any]) -> float | None:
    """
    Return when a timed run ends, as a millisecond epoch, if Protect said so.

    ``sirenStatus.activatedAt`` is an epoch in milliseconds and
    ``sirenStatus.duration`` is in milliseconds too (spec ``sirenDuration``:
    5000 to 30000), while the play request's own duration is in seconds.
    Either one missing or not a number means the end of the run is unknown.

    The unit of ``duration`` is read defensively. The spec and the uiprotect
    documentation say milliseconds, but the uiprotect 10.5.1 wheel treats the
    value as seconds, and no capture of an active run has been taken from a
    console to settle it. A run is never longer than 30 seconds, so a value of
    60 or less can only be seconds and is converted; anything larger is
    milliseconds.
    """
    activated_at = status.get("activatedAt")
    duration = status.get("duration")
    if (
        isinstance(activated_at, bool)
        or not isinstance(activated_at, (int, float))
        or isinstance(duration, bool)
        or not isinstance(duration, (int, float))
    ):
        return None
    duration_ms: float = float(duration)
    if duration_ms <= SIREN_DURATION_SECONDS_CEILING:
        duration_ms *= 1000
    return float(activated_at) + duration_ms


class UnifiProtectSiren(UnifiProtectEntity, SirenEntity):
    """Representation of a UniFi Protect siren."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = (
        SirenEntityFeature.TURN_ON
        | SirenEntityFeature.TURN_OFF
        | SirenEntityFeature.DURATION
        | SirenEntityFeature.VOLUME_SET
    )

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        siren_id: str,
    ) -> None:
        """Initialize the siren."""
        super().__init__(coordinator, DEVICE_TYPE_SIREN, siren_id)
        self._unsub_expiry: CALLBACK_TYPE | None = None

    @property
    def is_on(self) -> bool | None:
        """
        Return True while the siren is sounding.

        Unknown (None) when Protect did not report a boolean, so a missing or
        malformed ``sirenStatus`` never reads as a confident "off".

        Protect sends no event when a timed run ends, so ``isActive`` alone
        would keep reading true until something refreshes the siren. A siren
        that reports when it started and for how long reads off once that
        time has passed, as in Home Assistant's own UniFi Protect integration.
        """
        device_data = self.device_data
        status = device_data.get("sirenStatus") if device_data else None
        if not isinstance(status, dict):
            return None
        is_active = status.get("isActive")
        if not isinstance(is_active, bool):
            return None
        if not is_active:
            return False
        run_end = siren_run_end_ms(status)
        if run_end is None:
            return True
        return dt_util.utcnow().timestamp() * 1000 < run_end

    @callback
    def _cancel_expiry(self) -> None:
        """Cancel the pending end-of-run update."""
        if self._unsub_expiry is not None:
            self._unsub_expiry()
            self._unsub_expiry = None

    @callback
    def _schedule_expiry(self) -> None:
        """
        Write the state when a timed run is due to end.

        Nothing else would: no frame arrives, and WebSocket traffic from the
        rest of the console keeps pushing the next poll back. Any earlier
        timer is cancelled first, so each update leaves at most one pending.
        """
        self._cancel_expiry()
        device_data = self.device_data
        status = device_data.get("sirenStatus") if device_data else None
        if not isinstance(status, dict) or status.get("isActive") is not True:
            return
        run_end = siren_run_end_ms(status)
        if run_end is None:
            return
        delay = run_end / 1000 - dt_util.utcnow().timestamp()
        if delay > 0:
            self._unsub_expiry = async_call_later(self.hass, delay, self._handle_expiry)

    @callback
    def _handle_expiry(self, _now: datetime) -> None:
        """Show the siren off, or wait again if the clock has not quite got there."""
        self._unsub_expiry = None
        self.async_write_ha_state()
        self._schedule_expiry()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        super()._handle_coordinator_update()
        self._schedule_expiry()

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep the entity on an entity ID change; nothing here is keyed on it."""
        async_delegate_entity_id_changed(super(), old_entity_id)

    async def async_added_to_hass(self) -> None:
        """Arm the end-of-run update for a run that is already under way."""
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_expiry)
        self._schedule_expiry()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """
        Sound the siren, optionally for a duration and at a volume.

        The duration is checked before any request is sent, so a rejected
        call leaves the siren's volume untouched. The volume is a separate
        PATCH that has to land before the play request.
        """
        duration: int | None = kwargs.get(ATTR_DURATION)
        volume_level: float | None = kwargs.get(ATTR_VOLUME_LEVEL)

        if duration is not None and duration not in SIREN_PLAY_DURATIONS:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="siren_invalid_duration",
                translation_placeholders={
                    "duration": str(duration),
                    "valid": ", ".join(map(str, SIREN_PLAY_DURATIONS)),
                },
            )

        protect = self.coordinator.require_protect_coordinator()
        if volume_level is not None:
            await protect.async_set_siren_volume(
                self._device_id, siren_volume_from_level(volume_level)
            )
        await protect.async_play_siren(self._device_id, duration=duration)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the siren."""
        _ = kwargs
        protect = self.coordinator.require_protect_coordinator()
        await protect.async_stop_siren(self._device_id)
