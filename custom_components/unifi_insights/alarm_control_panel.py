# Copyright 2026 UniFi Insights contributors
"""Support for the UniFi Protect alarm control panel."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_ARM_PROFILE_ID,
    ATTR_ARMED_AT,
    ATTR_BREACH_DETECTED_AT,
    ATTR_BREACH_EVENT_COUNT,
    ATTR_WILL_BE_ARMED_AT,
    DEVICE_TYPE_NVR,
)
from .data_transforms import (
    owner_available,
    protect_collection,
    protect_device,
    uses_external_alarm_manager,
)
from .entity import UnifiProtectEntity, get_field

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import UnifiInsightsConfigEntry
    from .coordinators import UnifiInsightsFacadeCoordinator

_LOGGER = logging.getLogger(__name__)

# Arming and disarming are actions: run them one at a time
PARALLEL_UPDATES = 1

# The spec's armMode.status enum. A status outside it is deliberately not
# mapped: see UnifiProtectNvrAlarmControlPanel.alarm_state.
ARM_MODE_TO_STATE: Final[dict[str, AlarmControlPanelState]] = {
    "disabled": AlarmControlPanelState.DISARMED,
    "arming": AlarmControlPanelState.ARMING,
    "armed": AlarmControlPanelState.ARMED_AWAY,
    "breach": AlarmControlPanelState.TRIGGERED,
}

EXTERNAL_PROFILE_STATE_TO_STATE: Final[dict[str, AlarmControlPanelState]] = {
    "disarmed": AlarmControlPanelState.DISARMED,
    "arming": AlarmControlPanelState.ARMING,
    "armed": AlarmControlPanelState.ARMED_AWAY,
    "breaching": AlarmControlPanelState.PENDING,
    "breached": AlarmControlPanelState.TRIGGERED,
}


def _global_alarm_manager_is_off(coordinator: UnifiInsightsFacadeCoordinator) -> bool:
    """
    Return whether Protect has said the global alarm manager is not enabled.

    Protect's own arm state does not follow the global alarm manager, so the
    panel is only shown while that is known to be off. Not yet known counts
    as not off.
    """
    return coordinator.data["protect"].get("global_alarm_manager") is False


def _arm_mode(nvr: Any) -> dict[str, Any] | None:
    """Return the NVR's armMode, or None when it has none (older firmware)."""
    arm_mode = nvr.get("armMode") if isinstance(nvr, dict) else None
    return arm_mode if isinstance(arm_mode, dict) else None


def _epoch_ms_to_iso(value: Any) -> str | None:
    """Convert a millisecond epoch to an ISO 8601 UTC string, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return dt_util.utc_from_timestamp(value / 1000).isoformat()
    except OverflowError, OSError, ValueError:
        # NaN, infinity or a value past the range datetime can represent.
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the alarm control panel for the UniFi Protect NVR."""
    _ = hass
    coordinator: UnifiInsightsFacadeCoordinator = entry.runtime_data.coordinator

    # Skip if Protect API is not available
    if not coordinator.protect_client:
        _LOGGER.debug("Skipping alarm control panel setup - Protect API not available")
        return

    known_nvr_ids: set[str] = set()
    known_profile_keys: set[tuple[str, str]] = set()

    @callback
    def async_discover_panels() -> None:
        """Add a panel for each NVR armMode, or external profile panels."""
        new_entities: list[AlarmControlPanelEntity] = []
        nvrs = protect_collection(coordinator.data, "nvrs") or {}
        for nvr_id, nvr_data in nvrs.items():
            if uses_external_alarm_manager(nvr_data):
                profiles = (
                    protect_collection(coordinator.data, "external_arm_profiles") or {}
                )
                for profile_id in profiles:
                    prof_key = (nvr_id, profile_id)
                    if prof_key not in known_profile_keys:
                        known_profile_keys.add(prof_key)
                        new_entities.append(
                            UnifiProtectExternalArmProfileAlarmControlPanel(
                                coordinator, nvr_id, profile_id
                            )
                        )
                continue

            if (
                nvr_id in known_nvr_ids
                or _arm_mode(nvr_data) is None
                or not _global_alarm_manager_is_off(coordinator)
            ):
                continue
            panel = UnifiProtectNvrAlarmControlPanel(coordinator, nvr_id)
            known_nvr_ids.add(nvr_id)
            new_entities.append(panel)

        if new_entities:
            _LOGGER.info(
                "Adding %d UniFi Protect alarm control panels", len(new_entities)
            )
            async_add_entities(new_entities)

    async_discover_panels()
    entry.async_on_unload(coordinator.async_add_listener(async_discover_panels))


class UnifiProtectNvrAlarmControlPanel(UnifiProtectEntity, AlarmControlPanelEntity):
    """The UniFi Protect Alarm Manager as an alarm control panel."""

    _attr_has_entity_name = True
    _attr_translation_key = "nvr_alarm"
    _attr_code_arm_required = False
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        nvr_id: str,
    ) -> None:
        """Initialize the alarm control panel."""
        super().__init__(coordinator, DEVICE_TYPE_NVR, nvr_id, "alarm")
        # Unrecognised statuses already reported, so each is logged once.
        self._logged_unknown: set[str] = set()

    @property
    def available(self) -> bool:
        """
        Return True while the panel shows a state that can be trusted.

        That needs Protect to be reachable, the NVR to report an armMode,
        the NVR not to use an external alarm manager, and the UniFi global alarm
        manager to be known to be off, because Protect's own arm state does not
        follow it. An NVR has no ``state`` field, so the base class check, which
        wants "CONNECTED", would never pass.
        """
        return (
            owner_available(self.coordinator.data, "protect")
            and _arm_mode(self.device_data) is not None
            and _global_alarm_manager_is_off(self.coordinator)
            and not uses_external_alarm_manager(self.device_data)
        )

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """
        Return the alarm state, or None (unknown) for an unrecognised status.

        Home Assistant's own UniFi Protect integration reports an unrecognised
        status as disarmed. This integration deliberately does not: on a
        security device a status it cannot interpret, for example a mode a
        later firmware adds, must never read as an all-clear.
        """
        arm_mode = _arm_mode(self.device_data)
        status = arm_mode.get("status") if arm_mode else None
        if not isinstance(status, str):
            return None
        state = ARM_MODE_TO_STATE.get(status.lower())
        if state is None and status not in self._logged_unknown:
            self._logged_unknown.add(status)
            _LOGGER.debug(
                "Unrecognised armMode status %r for NVR %s; reporting unknown",
                status,
                self._device_id,
            )
        return state

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the arm profile, the arming and breach times and the count."""
        arm_mode = _arm_mode(self.device_data)
        if arm_mode is None:
            return None
        profile_id = arm_mode.get("armProfileId")
        breach_count = arm_mode.get("breachEventCount")
        attributes = {
            ATTR_ARM_PROFILE_ID: profile_id if isinstance(profile_id, str) else None,
            ATTR_ARMED_AT: _epoch_ms_to_iso(arm_mode.get("armedAt")),
            ATTR_WILL_BE_ARMED_AT: _epoch_ms_to_iso(arm_mode.get("willBeArmedAt")),
            ATTR_BREACH_DETECTED_AT: _epoch_ms_to_iso(arm_mode.get("breachDetectedAt")),
            ATTR_BREACH_EVENT_COUNT: (
                breach_count
                if isinstance(breach_count, (int, float))
                and not isinstance(breach_count, bool)
                else None
            ),
        }
        return {key: value for key, value in attributes.items() if value is not None}

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """
        Arm the alarm using the console's currently selected arm profile.

        No state is set here: the NVR's WebSocket frame, or the refresh the
        Protect coordinator requests, reports whether Protect actually armed.
        """
        _ = code
        await self.coordinator.require_protect_coordinator().async_arm_alarm()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm the alarm."""
        _ = code
        await self.coordinator.require_protect_coordinator().async_disarm_alarm()


class UnifiProtectExternalArmProfileAlarmControlPanel(
    UnifiProtectEntity, AlarmControlPanelEntity
):
    """The UniFi Protect External Arm Profile as an alarm control panel."""

    _attr_has_entity_name = True
    _attr_translation_key = "external_arm_profile"
    _attr_code_arm_required = False
    _attr_code_format = None
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY

    def __init__(
        self,
        coordinator: UnifiInsightsFacadeCoordinator,
        nvr_id: str,
        profile_id: str,
    ) -> None:
        """Initialize the external arm profile panel."""
        super().__init__(
            coordinator, DEVICE_TYPE_NVR, nvr_id, f"external_arm_profile_{profile_id}"
        )
        self._profile_id = profile_id

    @property
    def name(self) -> str | None:
        """Return the name from the profile title."""
        profile = self._profile_data
        if profile and (title := profile.get("title")):
            return str(title)
        return None

    @property
    def available(self) -> bool:
        """
        Return True while the profile panel is available.

        Requires Protect to be reachable, the NVR to use external alarm manager,
        and the profile to be present in coordinator data. When a profile is
        missing, it turns unavailable without being removed.
        """
        if not owner_available(self.coordinator.data, "protect"):
            return False
        nvr = protect_device(self.coordinator.data, "nvrs", self._device_id)
        if not uses_external_alarm_manager(nvr):
            return False
        return self._profile_data is not None

    @property
    def _profile_data(self) -> dict[str, Any] | None:
        """Return profile data from coordinator data."""
        # A single-record lookup: protect_collection copies the whole
        # collection, which every panel would repeat on every state read.
        record = protect_device(
            self.coordinator.data, "external_arm_profiles", self._profile_id
        )
        return record if isinstance(record, dict) else None

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Return the alarm state, or None (unknown) for an unrecognised status."""
        profile = self._profile_data
        if profile is None:
            return None
        state = profile.get("state")
        raw_state = getattr(state, "value", state)
        if not isinstance(raw_state, str):
            return None
        return EXTERNAL_PROFILE_STATE_TO_STATE.get(raw_state.lower())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the extra state attributes (never exposing pin_code)."""
        profile = self._profile_data
        if profile is None:
            return None
        attrs = {
            "has_pin": get_field(profile, "hasPin", "has_pin"),
            "alarm_ids": get_field(profile, "alarmIds", "alarm_ids"),
            "state_set_at": get_field(profile, "stateSetAt", "state_set_at"),
            "state_promotion_due_at": get_field(
                profile, "statePromotionDueAt", "state_promotion_due_at"
            ),
            "active_breach_id": get_field(
                profile, "activeBreachId", "active_breach_id"
            ),
        }
        return {key: value for key, value in attrs.items() if value is not None}

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Arm the external profile."""
        _ = code
        protect = self.coordinator.require_protect_coordinator()
        await protect.async_arm_external_profile(self._profile_id)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm the external profile."""
        _ = code
        protect = self.coordinator.require_protect_coordinator()
        await protect.async_disarm_external_profile(self._profile_id)
