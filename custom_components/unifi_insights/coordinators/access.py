# Copyright 2026 UniFi Insights contributors
"""Standalone UniFi Access coordinator: authoritative state and every Access command."""

from __future__ import annotations

import asyncio
import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal

from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiError,
    UniFiRateLimitError,
    UniFiResponseError,
    UniFiTimeoutError,
    UniFiValidationError,
)
from unifi_official_api.access import (
    GATE_COMMANDS,
    GateCommand,
)
from unifi_official_api.access.websocket import (
    AccessNotificationSocket,
)

from custom_components.unifi_insights.console_identity import (
    normalize_console_namespace,
)
from custom_components.unifi_insights.const import (
    CONF_ACCESS_TOKEN,
    CONF_CONSOLE_ID,
    DOMAIN,
    SCAN_INTERVAL_ACCESS,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from unifi_official_api.access import UniFiAccessClient
    from unifi_official_api.access.models.devices import (
        AccessDevice,
    )
    from unifi_official_api.access.models.doors import AccessDoor

_LOGGER = logging.getLogger(__name__)

ACCESS_DEBOUNCE_SECONDS = 2.0
# UA Hub Gate motor type, per community SDKs. Not in the vendor reference.
GATE_DEVICE_TYPE = "UGT"
# The remote_view device_type of a doorbell in the official reference.
DOORBELL_DEVICE_TYPES = frozenset({"UA-G2-PRO"})
# Error code the console returns for a PIN outside its preset length.
PIN_LENGTH_INVALID_CODE = "CODE_CREDS_PIN_CODE_CREDS_LENGTH_INVALID"
# A PIN is the console's own credential, so a user's status change is the
# only account-level control exposed here.
USER_STATUSES = ("ACTIVE", "DEACTIVATED")

EmergencyMode = Literal["lockdown", "evacuation"]
Feature = Literal["doors", "devices", "emergency", "notifications"]

ACCESS_UNAVAILABLE_KEY = "access_unreachable"
ACCESS_AUTH_FAILED_KEY = "access_auth_failed"
PERMISSION_DENIED_KEY = "permission_denied"


def access_doorbell_signal(entry_id: str) -> str:
    """Return the entry-scoped dispatcher signal for doorbell rings."""
    return f"{DOMAIN}_{entry_id}_access_doorbell"


def access_result_signal(entry_id: str) -> str:
    """Return the entry-scoped dispatcher signal for access-result events."""
    return f"{DOMAIN}_{entry_id}_access_result"


@callback
def async_dispatch_access_result(
    hass: HomeAssistant, entry_id: str, payload: dict[str, Any]
) -> None:
    """Send one verified access result. Callers pass only the documented keys."""
    async_dispatcher_send(hass, access_result_signal(entry_id), payload)


def resolve_access_namespace(entry: ConfigEntry) -> str | None:
    """
    Return the canonical console namespace, or None if it is not valid.

    The namespace never falls back to the entry id. The Access token is
    excluded as a credential so it can never become an identity.
    """
    return normalize_console_namespace(
        entry.data.get(CONF_CONSOLE_ID),
        credentials=(entry.data.get(CONF_API_KEY), entry.data.get(CONF_ACCESS_TOKEN)),
    )


def emergency_target(
    mode: EmergencyMode, *, enabled: bool, lockdown: bool, evacuation: bool
) -> tuple[bool, bool]:
    """
    Return the (lockdown, evacuation) pair to write.

    Turning a mode on clears the other one. Turning a mode off keeps the
    other mode at the value the console reported.
    """
    if enabled:
        return mode == "lockdown", mode == "evacuation"
    if mode == "lockdown":
        return False, evacuation
    return lockdown, False


def _text_or_none(value: Any) -> str | None:
    """Return a non-empty string, or None for anything else."""
    return value if isinstance(value, str) and value else None


def _door_record(door: AccessDoor) -> dict[str, Any] | None:
    """Return the coordinator view of one door, or None without an id."""
    if not door.id:
        return None
    return {
        "id": door.id,
        "name": door.name,
        "is_bind_hub": door.is_bind_hub is True,
        "lock_relay": door.door_lock_relay_status,
        "position": door.door_position_status,
    }


def _device_record(device: AccessDevice) -> dict[str, Any] | None:
    """Return the coordinator view of one device, or None without an id."""
    if not device.id:
        return None
    return {
        "id": device.id,
        "name": device.name,
        "type": device.type,
        "location_id": _text_or_none(device.location_id),
        "is_online": device.is_online if isinstance(device.is_online, bool) else None,
    }


def _emergency_record(lockdown: Any, evacuation: Any) -> dict[str, bool | None]:
    """Return the emergency state, with unknown values kept as None."""
    return {
        "lockdown": lockdown if isinstance(lockdown, bool) else None,
        "evacuation": evacuation if isinstance(evacuation, bool) else None,
    }


class UnifiInsightsAccessCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll Access doors, devices and emergency state, and run every command."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: UniFiAccessClient,
    ) -> None:
        """Initialize the coordinator for one config entry's Access client."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_access",
            update_interval=SCAN_INTERVAL_ACCESS,
        )
        self.client = client
        self.namespace = resolve_access_namespace(entry)
        self.auth_failed = False
        self.webhook_active = False
        self._denied: set[str] = set()
        self._emergency_lock = asyncio.Lock()
        self._ws_task: asyncio.Task[None] | None = None
        self._debounce_cancel: Callable[[], None] | None = None
        self._refresh_tasks: set[asyncio.Task[None]] = set()
        self._read_tasks: set[asyncio.Task[dict[str, Any]]] = set()
        self.data = self._empty_snapshot()

    @staticmethod
    def _empty_snapshot() -> dict[str, Any]:
        """Return a snapshot with no doors, devices or emergency state."""
        return {"doors": {}, "devices": {}, "emergency": None}

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch Access state. A 401 expires Access; other errors keep old data."""
        if self.auth_failed:
            return self._empty_snapshot()
        try:
            return await self._async_read()
        except UniFiAuthenticationError as err:
            await self._async_expire(err.status_code)
            return self._empty_snapshot()
        except (
            UniFiRateLimitError,
            UniFiConnectionError,
            UniFiTimeoutError,
            UniFiError,
        ) as err:
            msg = f"Error fetching UniFi Access data ({type(err).__name__})"
            raise UpdateFailed(msg) from err

    async def _async_read(self) -> dict[str, Any]:
        """
        Run one coordinator snapshot read as an entry task that stop can cancel.

        Poll, debounced and command-readback refreshes read through here. Direct
        client calls made by commands are not tracked.
        """
        read = self.config_entry.async_create_background_task(
            self.hass,
            self._async_fetch(),
            name=f"{DOMAIN}_access_read",
        )
        self._read_tasks.add(read)
        read.add_done_callback(self._read_tasks.discard)
        return await read

    async def _async_fetch(self) -> dict[str, Any]:
        """Read each feature; a 403 or 404 on one feature only disables that feature."""
        doors = await self._async_feature("doors", self.client.doors.get_all)
        devices = await self._async_feature("devices", self.client.devices.get_all)
        emergency = await self._async_feature("emergency", self.client.emergency.get)
        snapshot = self._empty_snapshot()
        if doors is not None:
            snapshot["doors"] = {
                record["id"]: record
                for record in (_door_record(door) for door in doors)
                if record is not None
            }
        if devices is not None:
            snapshot["devices"] = {
                record["id"]: record
                for record in (_device_record(device) for device in devices)
                if record is not None
            }
        if emergency is not None:
            snapshot["emergency"] = _emergency_record(
                emergency.lockdown, emergency.evacuation
            )
        return snapshot

    async def _async_feature[ResultT](
        self, feature: Feature, read: Callable[[], Awaitable[ResultT]]
    ) -> ResultT | None:
        """Run one feature read, marking the feature denied on 403 or 404."""
        if feature in self._denied:
            return None
        try:
            return await read()
        except UniFiAuthenticationError as err:
            if err.status_code == HTTPStatus.UNAUTHORIZED:
                raise
            self._deny(feature, err.status_code)
        except UniFiResponseError as err:
            if err.status_code != HTTPStatus.NOT_FOUND:
                raise
            self._deny(feature, err.status_code)
        return None

    def _deny(self, feature: Feature, status_code: int | None) -> None:
        """Remember that the console refuses one feature until the entry reloads."""
        self._denied.add(feature)
        _LOGGER.info(
            "UniFi Access %s is not available to this token (status %s)",
            feature,
            status_code,
        )

    async def _async_expire(self, status_code: int | None) -> None:
        """Stop Access polling and the websocket after the token was rejected."""
        if self.auth_failed:
            return
        self.auth_failed = True
        self.update_interval = None
        await self.async_stop_websocket()
        # A command or socket failure can happen outside a polling refresh.
        # Publish availability now, because polling has just been disabled.
        self.async_update_listeners()
        _LOGGER.warning(
            "UniFi Access token was rejected (status %s); replace it in "
            "reconfigure to restore Access entities",
            status_code,
        )

    @callback
    def _handle_socket_stop(self, status_code: int) -> None:
        """
        Apply a permanent handshake answer. Runs inside the socket task.

        The socket task is finishing, so it is not awaited here. A 401 expires
        Access on a separate task. A 403 or 404 disables only notifications.
        """
        self._ws_task = None
        if status_code == HTTPStatus.UNAUTHORIZED:
            self.hass.async_create_task(self._async_expire(status_code))
            return
        self._deny("notifications", status_code)
        self.async_update_listeners()

    def feature_available(self, feature: Feature) -> bool:
        """Return True when the token is valid and the feature was not denied."""
        return not self.auth_failed and feature not in self._denied

    @callback
    def async_set_webhook_active(self, *, active: bool) -> None:
        """Record whether the verified access-result webhook is registered."""
        self.webhook_active = active
        self.async_update_listeners()

    @property
    def gate_door_ids(self) -> frozenset[str]:
        """
        Return door ids that a UA Hub Gate is linked to by exact location id.

        Empty without the devices feature: a gate cannot be recognized, so no
        door may be treated as one.
        """
        if not self.feature_available("devices"):
            return frozenset()
        doors: dict[str, Any] = self.data["doors"]
        devices: dict[str, Any] = self.data["devices"]
        return frozenset(
            device["location_id"]
            for device in devices.values()
            if device["type"] == GATE_DEVICE_TYPE and device["location_id"] in doors
        )

    @property
    def lock_door_ids(self) -> frozenset[str]:
        """
        Return doors that get a lock entity. Gate doors use a cover instead.

        Empty without the devices feature, because a gate door would otherwise
        be treated as an ordinary lock. Fails closed.
        """
        if not self.feature_available("devices"):
            return frozenset()
        return frozenset(self.data["doors"]) - self.gate_door_ids

    @property
    def unlock_door_ids(self) -> frozenset[str]:
        """
        Return bound doors that are not linked to a recognized gate hub.

        Empty without the devices feature. Only the SDK's exact UGT type and
        location link identify a gate; other types retain ordinary door controls.
        """
        if not self.feature_available("devices"):
            return frozenset()
        return frozenset(
            door_id
            for door_id, door in self.data["doors"].items()
            if door["is_bind_hub"] and door_id not in self.gate_door_ids
        )

    @property
    def doorbell_device_ids(self) -> frozenset[str]:
        """Return devices of a known doorbell hardware type."""
        return frozenset(
            device_id
            for device_id, device in self.data["devices"].items()
            if device["type"] in DOORBELL_DEVICE_TYPES
        )

    def _require_command(self) -> None:
        """Refuse commands once the token has been rejected."""
        if self.auth_failed:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=ACCESS_AUTH_FAILED_KEY
            )

    async def _command_error(self, err: UniFiError, key: str) -> HomeAssistantError:
        """Translate a failed command into a user-facing error without secrets."""
        if isinstance(err, UniFiAuthenticationError):
            if err.status_code == HTTPStatus.UNAUTHORIZED:
                await self._async_expire(err.status_code)
                return HomeAssistantError(
                    translation_domain=DOMAIN, translation_key=ACCESS_AUTH_FAILED_KEY
                )
            return HomeAssistantError(
                translation_domain=DOMAIN, translation_key=PERMISSION_DENIED_KEY
            )
        if isinstance(
            err, UniFiConnectionError | UniFiTimeoutError | UniFiRateLimitError
        ):
            return HomeAssistantError(
                translation_domain=DOMAIN, translation_key=ACCESS_UNAVAILABLE_KEY
            )
        return HomeAssistantError(translation_domain=DOMAIN, translation_key=key)

    async def async_unlock_door(self, door_id: str) -> None:
        """Remotely unlock a bound, non-gate door, then read the state back."""
        self._require_command()
        if door_id not in self.unlock_door_ids:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="door_not_bound"
            )
        try:
            await self.client.doors.unlock(door_id)
        except UniFiError as err:
            raise await self._command_error(err, "unlock_failed") from err
        await self.async_refresh()

    async def async_lock_door(self, door_id: str) -> None:
        """Set the door to the documented ``reset`` rule, then read the state back."""
        self._require_command()
        if door_id not in self.lock_door_ids:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="door_not_lockable"
            )
        try:
            await self.client.doors.set_lock_rule(door_id, rule_type="reset")
        except UniFiResponseError as err:
            key = (
                "lock_rule_unsupported"
                if err.status_code == HTTPStatus.NOT_FOUND
                else "lock_failed"
            )
            raise await self._command_error(err, key) from err
        except UniFiError as err:
            raise await self._command_error(err, "lock_failed") from err
        await self.async_refresh()

    async def async_gate_command(self, door_id: str, command: GateCommand) -> None:
        """
        Drive a UA Hub Gate's motor, then read the state back.

        Only a door linked to a UGT gate by exact location id accepts this.
        Ordinary door hubs refuse it before any request is sent.
        """
        self._require_command()
        if command not in GATE_COMMANDS:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="gate_command_invalid"
            )
        if door_id not in self.gate_door_ids:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="gate_command_unsupported"
            )
        try:
            await self.client.doors.unlock(door_id, control_cmd=command)
        except UniFiError as err:
            raise await self._command_error(err, "gate_command_rejected") from err
        await self.async_refresh()

    async def async_set_emergency_mode(
        self, mode: EmergencyMode, *, enabled: bool
    ) -> None:
        """
        Set Lockdown or Evacuation without losing the other mode.

        Reads the current pair, writes the computed pair, then reads it back
        and checks the console applied it. Serialised by one lock.
        """
        self._require_command()
        async with self._emergency_lock:
            try:
                current = await self.client.emergency.get()
                if current.lockdown is None or current.evacuation is None:
                    raise HomeAssistantError(
                        translation_domain=DOMAIN,
                        translation_key="emergency_state_unknown",
                    )
                lockdown, evacuation = emergency_target(
                    mode,
                    enabled=enabled,
                    lockdown=current.lockdown,
                    evacuation=current.evacuation,
                )
                await self.client.emergency.set(
                    lockdown=lockdown, evacuation=evacuation
                )
                readback = await self.client.emergency.get()
            except UniFiError as err:
                raise await self._command_error(err, "emergency_failed") from err
            applied = _emergency_record(readback.lockdown, readback.evacuation)
            await self.async_refresh()
        if applied != {"lockdown": lockdown, "evacuation": evacuation}:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="emergency_not_applied"
            )

    async def async_set_user_pin(self, user_id: str, pin: str) -> None:
        """
        Assign a user-chosen PIN. Digits-only is checked here; length is server-side.

        The PIN is never logged, stored, or placed in an error message. The
        read-back is an authoritative refresh, as for every command.
        """
        self._require_command()
        if not pin or not pin.isascii() or not pin.isdigit():
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="pin_invalid_format"
            )
        try:
            await self.client.users.set_pin(user_id, pin)
        except UniFiValidationError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="pin_invalid_format"
            ) from err
        except UniFiResponseError as err:
            if err.status_code == HTTPStatus.NOT_FOUND:
                raise self._user_not_found() from None
            if err.api_error_code == PIN_LENGTH_INVALID_CODE:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="pin_rejected"
                ) from None
            raise await self._command_error(err, "pin_assign_failed") from err
        except UniFiError as err:
            raise await self._command_error(err, "pin_assign_failed") from err
        await self.async_refresh()

    async def async_remove_user_pin(self, user_id: str) -> None:
        """Remove a user's PIN, then refresh."""
        self._require_command()
        try:
            await self.client.users.remove_pin(user_id)
        except UniFiResponseError as err:
            if err.status_code == HTTPStatus.NOT_FOUND:
                raise self._user_not_found() from None
            raise await self._command_error(err, "pin_remove_failed") from err
        except UniFiError as err:
            raise await self._command_error(err, "pin_remove_failed") from err
        await self.async_refresh()

    async def async_set_user_status(self, user_id: str, status: str) -> None:
        """Set a user account to ACTIVE or DEACTIVATED, then refresh. Account-wide."""
        self._require_command()
        if status not in USER_STATUSES:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="status_invalid"
            )
        try:
            await self.client.users.set_status(user_id, status)  # type: ignore[arg-type]
        except UniFiResponseError as err:
            if err.status_code == HTTPStatus.NOT_FOUND:
                raise self._user_not_found() from None
            raise await self._command_error(err, "status_update_failed") from err
        except UniFiError as err:
            raise await self._command_error(err, "status_update_failed") from err
        await self.async_refresh()

    @staticmethod
    def _user_not_found() -> ServiceValidationError:
        """Return the unknown-user error. It carries no console detail."""
        return ServiceValidationError(
            translation_domain=DOMAIN, translation_key="access_user_not_found"
        )

    async def async_start_websocket(self) -> None:
        """Start the notification socket once, if Access permits it."""
        if (
            self._ws_task is not None
            or not self.feature_available("devices")
            or not self.feature_available("notifications")
        ):
            return
        socket = AccessNotificationSocket(self.client)
        self._ws_task = self.config_entry.async_create_background_task(
            self.hass,
            socket.run(self._handle_frame, on_stop=self._handle_socket_stop),
            name=f"{DOMAIN}_access_websocket",
        )

    async def async_stop_websocket(self) -> None:
        """
        Stop the socket, the debounce timer, and every tracked read and refresh.

        The socket stops first, so no frame can arm a new timer after the timer
        is cleared. Tracked means coordinator snapshot reads and debounced
        refreshes. Returning means no other tracked read or refresh is still
        running; the calling task is excluded. Direct client
        calls made by commands (emergency reads and writes, user and remote
        writes) are not tracked, and a remote write already sent may still be in
        flight. The calling task is never awaited: a refresh reaches here through
        a 401. Safe to call more than once.
        """
        task = self._ws_task
        self._ws_task = None
        if task is not None:
            task.cancel()
            await asyncio.wait({task})
        if self._debounce_cancel is not None:
            self._debounce_cancel()
            self._debounce_cancel = None
        current = asyncio.current_task()
        pending = {
            pending_task
            for pending_task in (*self._refresh_tasks, *self._read_tasks)
            if pending_task is not current
        }
        for pending_task in pending:
            pending_task.cancel()
        if pending:
            await asyncio.wait(pending)

    async def async_shutdown(self) -> None:
        """
        Stop polling, then stop the socket and settle tracked reads and refreshes.

        The base class sets the shutdown flag first, so a refresh that gets the
        lock after the cancelled read releases it returns without reading.
        """
        await super().async_shutdown()
        await self.async_stop_websocket()

    @callback
    def _handle_frame(self, frame: dict[str, Any]) -> None:
        """Route one notification frame. Ring payloads are transient and not stored."""
        event = frame.get("event")
        data = frame.get("data")
        if event == "access.remote_view":
            if not isinstance(data, dict):
                return
            device_id = _text_or_none(data.get("device_id"))
            if device_id is None:
                _LOGGER.debug("Dropped a doorbell frame without a device id")
                return
            async_dispatcher_send(
                self.hass,
                access_doorbell_signal(self.config_entry.entry_id),
                {
                    "device_id": device_id,
                    "device_name": _text_or_none(data.get("device_name")),
                    "door_name": _text_or_none(data.get("door_name")),
                    "device_type": _text_or_none(data.get("device_type")),
                },
            )
        elif event == "access.remote_view.change":
            self._schedule_debounced_refresh()

    @callback
    def _schedule_debounced_refresh(self) -> None:
        """Refresh once after status frames stop arriving for the debounce window."""
        if self._debounce_cancel is not None:
            self._debounce_cancel()
        self._debounce_cancel = async_call_later(
            self.hass, ACCESS_DEBOUNCE_SECONDS, self._async_debounced_fire
        )

    @callback
    def _async_debounced_fire(self, _now: Any) -> None:
        """Run the debounced refresh as a task that stop can cancel and await."""
        self._debounce_cancel = None
        refresh = self.config_entry.async_create_background_task(
            self.hass,
            self.async_refresh(),
            name=f"{DOMAIN}_access_debounced_refresh",
        )
        self._refresh_tasks.add(refresh)
        refresh.add_done_callback(self._refresh_tasks.discard)
