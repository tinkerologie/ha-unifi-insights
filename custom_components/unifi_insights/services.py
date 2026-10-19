# Copyright 2026 UniFi Insights contributors
"""Services for the UniFi Insights integration."""

from __future__ import annotations

import logging
import math
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Final, NoReturn

from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiError,
    UniFiNotFoundError,
    UniFiResponseError,
)

from .const import (
    DEFAULT_VOUCHER_NAME,
    DOMAIN,
    HDR_MODE_AUTO,
    HDR_MODE_OFF,
    HDR_MODE_ON,
    LIGHT_MODE_ALWAYS,
    LIGHT_MODE_MOTION,
    LIGHT_MODE_OFF,
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
from .data_transforms import client_connection_type
from .diagnostics import redact_network_rule_export
from .led_level import percent_to_led_level
from .service_targets import (
    resolve_access_call,
    resolve_carrier_subscriber_call,
    resolve_chime_pairing_call,
    resolve_network_client_call,
    resolve_network_consoles_call,
    resolve_network_device_call,
    resolve_network_site_call,
    resolve_protect_call,
    resolve_protect_console_call,
    resolve_refresh_call,
    resolve_rule_export_call,
)

# Schemas are typed against probatio, the library HA 2026.10 annotates its schema
# arguments with. Runtime still imports voluptuous: HA 2026.10 aliases it to probatio,
# and the 2026.9 minimum ships real voluptuous, so only the type checker sees probatio.
if TYPE_CHECKING:
    import probatio as vol
else:
    import voluptuous as vol

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse


_LOGGER = logging.getLogger(__name__)

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


def _raise_carrier_action_error(
    err: UniFiError | InvalidSubscriberIdError, action: str, subscriber_id: str
) -> NoReturn:
    """
    Map a Carrier Fabric action failure to a translated Home Assistant error.

    The Carrier Fabric command owner raises the client's errors unchanged, so
    the action translates them here.
    """
    if isinstance(err, InvalidSubscriberIdError):
        msg = f"Invalid subscriber ID format '{subscriber_id}' (valid UUID expected)"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="carrier_invalid_subscriber_id",
            translation_placeholders={"target": subscriber_id},
        ) from err

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
    target = resolve_carrier_subscriber_call(hass, call)
    # CARRIER_SUSPEND_SUBSCRIBER_SCHEMA already made this a str of at most
    # MAX_SUSPEND_REASON_LENGTH characters.
    reason = call.data.get("reason")
    try:
        await target.carrier_commands.async_suspend_subscriber(
            target.subscriber_id, reason=reason
        )
    except (UniFiError, InvalidSubscriberIdError) as err:
        _raise_carrier_action_error(err, "suspend", target.subscriber_id)


async def _async_handle_carrier_resume_subscriber(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle carrier_resume_subscriber service call."""
    target = resolve_carrier_subscriber_call(hass, call)
    try:
        await target.carrier_commands.async_resume_subscriber(target.subscriber_id)
    except (UniFiError, InvalidSubscriberIdError) as err:
        _raise_carrier_action_error(err, "resume", target.subscriber_id)


async def _async_handle_power_cycle_port(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle the power cycle port service call."""
    target = resolve_network_device_call(hass, call, action="power_cycle")
    port_idx = call.data["port_idx"]

    _LOGGER.info(
        "Power-cycling PoE port %d on device %s in site %s",
        port_idx,
        target.device_id,
        target.site_id,
    )
    # The owner checks the device's ports where poe.enabled is true first.
    await target.device_commands.async_power_cycle_port(
        target.site_id, target.device_id, port_idx
    )


def _is_wireless_client(client_record: dict[str, Any]) -> bool:
    """Check if client record represents an exact recognized wireless client."""
    return client_connection_type(client_record) == "wireless"


async def _async_handle_reconnect_client(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle reconnect_client service call."""
    target = resolve_network_client_call(hass, call)
    client_id = target.client_id

    data = target.cache.data
    clients = data.get("clients", {}) if isinstance(data, dict) else {}
    site_clients = clients.get(target.site_id, {}) if isinstance(clients, dict) else {}
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
            translation_placeholders={"client_id": client_id},
        )

    await target.device_commands.async_reconnect_client(target.site_id, client_id)


async def _async_handle_remove_clients(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle remove_clients service call."""
    failures: list[str] = []
    errors: list[HomeAssistantError] = []

    for console in resolve_network_consoles_call(hass, call):
        total_removed_console = 0
        for site_id in console.site_ids:
            try:
                historical_clients = (
                    await console.facade_commands.async_get_historical_clients(site_id)
                )
                eligible_macs = _filter_clients_for_removal(historical_clients)
                if eligible_macs:
                    await console.device_commands.async_forget_clients_batch(
                        site_id, eligible_macs
                    )
                    total_removed_console += len(eligible_macs)
            except HomeAssistantError as err:
                # The error text stays in the log; the action error only names
                # the console and site, because API text is not user-facing.
                _LOGGER.warning(
                    "Error removing clients from %s (site %s): %s",
                    console.title,
                    site_id,
                    err,
                )
                failures.append(f"{console.title} (site {site_id})")
                errors.append(err)

        _LOGGER.debug(
            "Removed %d clients for console %s", total_removed_console, console.title
        )

    if failures:
        joined = "; ".join(failures)
        msg = f"Failed to remove clients: {joined}"
        raise HomeAssistantError(
            msg,
            translation_domain=DOMAIN,
            translation_key="remove_clients_failed",
            translation_placeholders={"failures": joined},
        ) from (errors[0] if len(errors) == 1 else None)


async def _async_handle_export_network_rules(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    """Return a fresh export of the network rules of one console."""
    target = resolve_rule_export_call(hass, call)
    site_id = target.site_id
    document: dict[str, Any] = await target.export_commands.async_export_network_rules(
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
        selection = resolve_refresh_call(hass, call)
        site_id = selection.site_id
        _LOGGER.info(
            "Refreshing data for %s site%s",
            "specific" if site_id else "all",
            f" (ID: {site_id})" if site_id else "s",
        )

        failed_titles: list[str] = []
        for console in selection.targets:
            try:
                # A site is a Network concept, so a site-scoped call has no
                # reason to spend a Protect round trip.
                await console.facade_commands.async_refresh_or_raise(
                    include_protect=selection.include_protect
                )
            except HomeAssistantError as err:
                # Keep going: one unreachable console must not stop the others
                # from being refreshed. The error text stays in the log; the
                # action error only names the consoles that failed.
                _LOGGER.warning("Error refreshing %s: %s", console.title, err)
                failed_titles.append(console.title)
            else:
                _LOGGER.info("Successfully refreshed coordinator data")

        if failed_titles:
            targets = ", ".join(failed_titles)
            msg = f"Unable to refresh UniFi Insights data from {targets}"
            raise HomeAssistantError(
                msg,
                translation_domain=DOMAIN,
                translation_key="refresh_failed",
                translation_placeholders={"targets": targets},
            )

    async def async_handle_restart_device(call: ServiceCall) -> None:
        """Handle the restart device service call."""
        target = resolve_network_device_call(hass, call, action="restart")
        _LOGGER.info(
            "Restarting device %s in site %s", target.device_id, target.site_id
        )
        await target.device_commands.async_restart_device(
            target.site_id, target.device_id
        )

    async def async_handle_power_cycle_port(call: ServiceCall) -> None:
        """Handle the power cycle port service call."""
        await _async_handle_power_cycle_port(hass, call)

    async def async_handle_set_hdr_mode(call: ServiceCall) -> None:
        """Handle the set_hdr_mode service call."""
        mode = call.data["mode"]

        target = resolve_protect_call(hass, call, "camera")
        camera_id = target.resource_id

        _LOGGER.info("Setting HDR mode for camera %s to %s", camera_id, mode)
        await target.protect_commands.async_set_hdr_mode(camera_id, mode)

    async def async_handle_set_video_mode(call: ServiceCall) -> None:
        """Handle the set_video_mode service call."""
        mode = call.data["mode"]

        target = resolve_protect_call(hass, call, "camera")
        camera_id = target.resource_id

        _LOGGER.info("Setting video mode for camera %s to %s", camera_id, mode)
        await target.protect_commands.async_set_video_mode(camera_id, mode)

    async def async_handle_set_mic_volume(call: ServiceCall) -> None:
        """Handle the set_mic_volume service call."""
        volume = call.data["volume"]

        target = resolve_protect_call(hass, call, "camera")
        camera_id = target.resource_id

        _LOGGER.info("Setting mic volume for camera %s to %d%%", camera_id, volume)
        await target.protect_commands.async_set_microphone_volume(camera_id, volume)

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
        mode = call.data["mode"]

        target = resolve_protect_call(hass, call, "light")
        light_id = target.resource_id

        _LOGGER.info("Setting light mode for %s to %s", light_id, mode)
        await target.protect_commands.async_set_light_mode(light_id, mode)

    async def async_handle_set_light_level(call: ServiceCall) -> None:
        """Handle the set_light_level service call."""
        level = call.data["level"]

        target = resolve_protect_call(hass, call, "light")
        light_id = target.resource_id

        led_level = percent_to_led_level(level)
        _LOGGER.info(
            "Setting light level for %s to %d%% (led_level %d)",
            light_id,
            level,
            led_level,
        )
        await target.protect_commands.async_set_light_brightness(light_id, led_level)

    async def async_handle_ptz_move(call: ServiceCall) -> None:
        """Handle the ptz_move service call."""
        preset = call.data["preset"]

        target = resolve_protect_call(hass, call, "camera")
        camera_id = target.resource_id

        _LOGGER.info("Moving camera %s to preset %d", camera_id, preset)
        await target.protect_commands.async_move_ptz_to_preset(camera_id, preset)

    async def async_handle_ptz_patrol(call: ServiceCall) -> None:
        """Handle the ptz_patrol service call."""
        action = call.data["action"]
        slot = call.data.get("slot", 0)

        target = resolve_protect_call(hass, call, "camera")
        camera_id = target.resource_id

        if action == "start":
            _LOGGER.info("Starting PTZ patrol slot %d for camera %s", slot, camera_id)
            await target.protect_commands.async_start_ptz_patrol(camera_id, slot)
        else:
            _LOGGER.info("Stopping PTZ patrol for camera %s", camera_id)
            await target.protect_commands.async_stop_ptz_patrol(camera_id)

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
        volume = call.data["volume"]
        # A camera_id only selects the chime's console; the command applies to
        # the whole chime and does not receive it.
        target = resolve_protect_call(hass, call, "chime")
        chime_id = target.resource_id
        _LOGGER.info("Setting chime %s volume to %d%%", chime_id, volume)
        await target.protect_commands.async_set_chime_volume(chime_id, volume)

    async def async_handle_play_chime_ringtone(call: ServiceCall) -> None:
        """Handle the play_chime_ringtone service call."""
        target = resolve_protect_call(hass, call, "chime")
        chime_id = target.resource_id

        await target.protect_commands.async_play_chime(chime_id)

    async def async_handle_set_chime_ringtone(call: ServiceCall) -> None:
        """Handle the set_chime_ringtone service call."""
        ringtone_id = call.data["ringtone_id"]
        # A camera_id only selects the chime's console; the command applies to
        # the whole chime and does not receive it.
        target = resolve_protect_call(hass, call, "chime")
        chime_id = target.resource_id
        _LOGGER.info("Setting chime %s ringtone to %s", chime_id, ringtone_id)
        await target.protect_commands.async_set_chime_ringtone(chime_id, ringtone_id)

    async def async_handle_set_chime_repeat_times(call: ServiceCall) -> None:
        """Handle the set_chime_repeat_times service call."""
        repeat_times = call.data["repeat_times"]
        # A camera_id only selects the chime's console; the command applies to
        # the whole chime and does not receive it.
        target = resolve_protect_call(hass, call, "chime")
        chime_id = target.resource_id
        _LOGGER.info("Setting chime %s repeat times to %d", chime_id, repeat_times)
        await target.protect_commands.async_set_chime_repeat(chime_id, repeat_times)

    async def async_handle_set_chime_paired_doorbells(call: ServiceCall) -> None:
        """Handle the set_chime_paired_doorbells service call."""
        target = resolve_chime_pairing_call(hass, call)
        camera_ids = list(target.camera_ids)
        _LOGGER.info(
            "Pairing chime %s with doorbell cameras %s", target.chime_id, camera_ids
        )
        await target.protect_commands.async_set_chime_paired_cameras(
            target.chime_id, camera_ids
        )

    async def async_handle_authorize_guest(call: ServiceCall) -> None:
        """Handle the authorize_guest service call."""
        target = resolve_network_client_call(hass, call)
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

        await target.config_commands.async_authorize_guest(
            target.site_id, target.client_id
        )

    async def async_handle_reconnect_client(call: ServiceCall) -> None:
        """Handle the reconnect_client service call."""
        await _async_handle_reconnect_client(hass, call)

    async def async_handle_remove_clients(call: ServiceCall) -> None:
        """Handle the remove_clients service call."""
        await _async_handle_remove_clients(hass, call)

    async def async_handle_generate_voucher(call: ServiceCall) -> None:
        """Handle the generate_voucher service call."""
        target = resolve_network_site_call(hass, call)
        count = call.data.get("count", 1)
        time_limit_minutes = call.data["duration_minutes"]
        upload_limit_kbps = call.data.get("upload_limit_kbps")
        download_limit_kbps = call.data.get("download_limit_kbps")
        data_limit_mb = call.data.get("data_limit_mb")
        # The Network API requires a voucher name.
        name = call.data.get("note") or DEFAULT_VOUCHER_NAME

        await target.facade_commands.async_generate_voucher(
            target.site_id,
            count=count,
            time_limit_minutes=time_limit_minutes,
            tx_rate_limit_kbps=upload_limit_kbps,
            rx_rate_limit_kbps=download_limit_kbps,
            data_usage_limit_mbytes=data_limit_mb,
            name=name,
        )

    async def async_handle_delete_voucher(call: ServiceCall) -> None:
        """Handle the delete_voucher service call."""
        target = resolve_network_site_call(hass, call)
        await target.facade_commands.async_delete_voucher(
            target.site_id, call.data["voucher_id"]
        )

    async def async_handle_trigger_alarm(call: ServiceCall) -> None:
        """Handle the trigger_alarm service call."""
        target = resolve_protect_console_call(hass, call)
        await target.protect_commands.async_trigger_alarm(call.data["alarm_id"])

    async def async_handle_create_liveview(call: ServiceCall) -> None:
        """Handle the create_liveview service call."""
        name = call.data["name"]
        layout = call.data["layout"]
        is_default = call.data.get("is_default", False)

        target = resolve_protect_console_call(hass, call)
        await target.protect_commands.async_create_liveview(
            name=name,
            layout=layout,
            is_default=is_default,
        )

    async def async_handle_set_liveview(call: ServiceCall) -> None:
        """Handle the set_liveview service call."""
        liveview_id = call.data["liveview_id"]

        # The liveview is checked to be on the viewer's console and is passed
        # to the command as given.
        target = resolve_protect_call(hass, call, "viewer")
        await target.protect_commands.async_update_viewer(
            target.resource_id, liveview=liveview_id
        )

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
        target = resolve_access_call(hass, call)
        await _async_run_access_user_action(
            target.access_commands.async_set_user_pin(
                call.data["user_id"], call.data["pin"]
            )
        )

    async def async_handle_remove_access_user_pin(call: ServiceCall) -> None:
        """Remove a user's PIN through the console that owns the user."""
        target = resolve_access_call(hass, call)
        await _async_run_access_user_action(
            target.access_commands.async_remove_user_pin(call.data["user_id"])
        )

    async def async_handle_set_access_user_status(call: ServiceCall) -> None:
        """Set a user account to ACTIVE or DEACTIVATED; this is account-wide."""
        target = resolve_access_call(hass, call)
        await _async_run_access_user_action(
            target.access_commands.async_set_user_status(
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
