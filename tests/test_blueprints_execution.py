# Copyright (c) 2026 UniFi Insights contributors
"""Tests for the UniFi Insights automation blueprints.

Every blueprint is loaded with the Home Assistant YAML loader (which understands
``!input``), validated by the blueprint loader Home Assistant itself uses, and
then loaded through a patch of Home Assistant's blueprint loader. The execution
tests set up a real automation with ``use_blueprint``, change states or fire the
trigger, and assert on what the user-supplied actions did.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from freezegun import api as freezegun_api
from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.automation.helpers import async_get_blueprints
from homeassistant.components.blueprint.models import Blueprint
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers.entity import entity_sources
from homeassistant.setup import async_setup_component
from homeassistant.util import yaml as yaml_util
from pytest_homeassistant.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.unifi_insights.const import CONF_TRACK_WIFI_CLIENTS, DOMAIN

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from unittest.mock import MagicMock

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant, ServiceCall
    from homeassistant.helpers import entity_registry as er

BLUEPRINT_SOURCE = Path(__file__).parent.parent / "blueprints" / "automation" / DOMAIN
AP_OVERLOAD = "ap_overload.yaml"
INFRASTRUCTURE_OFFLINE = "infrastructure_offline.yaml"
SECURE_SUBNET = "secure_subnet_new_client.yaml"
ALL_BLUEPRINTS = (AP_OVERLOAD, INFRASTRUCTURE_OFFLINE, SECURE_SUBNET)
AUTOMATION_ENTITY_ID = "automation.blueprint_under_test"

# Real seconds a test waits for a run to reach the state it expects, and for the
# event loop to react to one simulated second of the freezer.
WAIT_TIMEOUT_SECONDS = 10.0
STEP_SETTLE_SECONDS = 0.02

# A Home Assistant log wording change makes these waits time out with a readable
# message.
BLUEPRINT_DELAY_LOG = "Executing step delay 0:00:10"
BLUEPRINT_CONDITION_ABORT_LOG = "Conditions not met, aborting automation"
BLUEPRINT_REGISTRY_CREATE_LOG = (
    "Bus:Handling <Event entity_registry_updated[L]: action=create,"
)

FIVE_MINUTES = {"hours": 0, "minutes": 5, "seconds": 0}


def _load(name: str) -> dict[str, Any]:
    """Read a repository blueprint with Home Assistant's YAML loader."""
    return yaml_util.load_yaml_dict(BLUEPRINT_SOURCE / name)


# Loggers that report a blueprint automation which fails or misbehaves at run time.
WATCHED_LOGGERS = (
    "homeassistant.components.automation",
    "homeassistant.components.homeassistant.triggers",
    "homeassistant.helpers.condition",
    "homeassistant.helpers.script",
    "homeassistant.helpers.template",
)


@pytest.fixture(autouse=True)
def _no_automation_warnings(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Fail a test whose automation logged a warning or an error.

    A dropped run ("Already running"), a template error or a trigger that could
    not initialise would otherwise only show up as a missing call.
    """
    yield
    noisy = [
        f"{record.name}: {record.getMessage()}"
        for record in [
            *caplog.get_records("setup"),
            *caplog.get_records("call"),
            *caplog.records,
        ]
        if record.levelno >= logging.WARNING and record.name.startswith(WATCHED_LOGGERS)
    ]
    assert not noisy, noisy


# ---------------------------------------------------------------------------
# Execution harness
# ---------------------------------------------------------------------------


@pytest.fixture
async def blueprint_dir(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> AsyncIterator[_BlueprintProgress]:
    """Patch blueprint loading to read repository files, as Core's tests do."""
    progress = _BlueprintProgress(caplog)

    async def load(path: str) -> Blueprint:
        progress.start = len(caplog.records)
        name = Path(path).name
        data = await hass.async_add_executor_job(_load, name)
        return Blueprint(
            data,
            path=path,
            expected_domain="automation",
            schema=AUTOMATION_BLUEPRINT_SCHEMA,
        )

    with patch.object(
        async_get_blueprints(hass), "async_get_blueprint", side_effect=load
    ):
        yield progress


@pytest.fixture
def calls(hass: HomeAssistant) -> list[ServiceCall]:
    """Record every call of the ``test.record`` service the user actions invoke."""
    return async_mock_service(hass, "test", "record")


async def _use_blueprint(
    hass: HomeAssistant, name: str, inputs: dict[str, Any]
) -> None:
    """Set up one automation that uses the installed blueprint."""
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "alias": "Blueprint under test",
                "use_blueprint": {"path": f"{DOMAIN}/{name}", "input": inputs},
            }
        },
    )
    await hass.async_block_till_done()
    state = hass.states.get(AUTOMATION_ENTITY_ID)
    assert state is not None
    assert state.state == "on", "the blueprint did not produce a working automation"


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, **delta: int
) -> None:
    """Move time forward and let every due timer run."""
    freezer.tick(timedelta(**delta))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _active_runs(hass: HomeAssistant) -> int:
    """Return how many runs of the automation under test are active or queued."""
    state = hass.states.get(AUTOMATION_ENTITY_ID)
    assert state is not None
    return int(state.attributes["current"])


async def _poll(condition: Callable[[], bool], seconds: float) -> bool:
    """Let the event loop run until ``condition`` holds or ``seconds`` have passed.

    The freezer also freezes the loop clock, so the limit is measured with the
    real clock; ``asyncio.sleep`` with a positive delay would never return.
    ``freezegun_api.real_monotonic`` is read through the module on purpose:
    freezegun replaces a real clock function imported into any other namespace.
    """
    deadline = freezegun_api.real_monotonic() + seconds
    while not condition():
        if freezegun_api.real_monotonic() >= deadline:
            return False
        await asyncio.sleep(0)
    return True


async def _wait_until(condition: Callable[[], bool], expected: str) -> None:
    """Wait for ``condition`` without moving time, or fail with what was expected.

    Runs parked on a ``delay`` or a ``wait_template`` must be observed through
    their run count or their recorded calls: ``async_block_till_done`` would
    wait for them to finish, which is exactly what the caller wants to watch.
    """
    if not await _poll(condition, WAIT_TIMEOUT_SECONDS):
        pytest.fail(
            f"Timed out after {WAIT_TIMEOUT_SECONDS:g} s waiting for {expected}"
        )


def _advance_time(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, **delta: int
) -> None:
    """Move time forward and fire the due timers, without waiting for any run.

    The caller then waits for what it expects with ``_wait_until``.
    """
    freezer.tick(timedelta(**delta))
    async_fire_time_changed(hass)


async def _advance_until(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    condition: Callable[[], bool],
    expected: str,
    *,
    limit_seconds: int = 30,
) -> None:
    """Let simulated time pass one second at a time until ``condition`` holds.

    A ``delay`` or a timeout only starts counting when its run reaches it, so one
    big step could pass before the timer exists. Small steps cannot miss it, and
    each step gives the event loop a moment to react before the next tick.
    """
    for _ in range(limit_seconds):
        _advance_time(hass, freezer, seconds=1)
        if await _poll(condition, STEP_SETTLE_SECONDS):
            return
    pytest.fail(
        f"Timed out after {limit_seconds} simulated seconds waiting for {expected}"
    )


async def _set_state(
    hass: HomeAssistant,
    entity_id: str,
    state: str,
    attributes: dict[str, Any] | None = None,
) -> None:
    """Write a state and let the automation react."""
    hass.states.async_set(entity_id, state, attributes)
    await hass.async_block_till_done()


class _BlueprintProgress:
    """Observe actual event dispatch and delay/admission logs without loop counts."""

    def __init__(self, caplog: pytest.LogCaptureFixture) -> None:
        self.caplog = caplog
        self.start = 0

    @property
    def creations(self) -> int:
        """Count native create events since the automation loaded."""
        return sum(
            record.name == "homeassistant.core"
            and BLUEPRINT_REGISTRY_CREATE_LOG in record.getMessage()
            for record in self.caplog.records[self.start :]
        )

    @property
    def outcomes(self) -> int:
        """Count creations that have reached the delay or failed the conditions."""
        return sum(
            record.name == "homeassistant.components.automation.blueprint_under_test"
            and (
                BLUEPRINT_DELAY_LOG in record.getMessage()
                or BLUEPRINT_CONDITION_ABORT_LOG in record.getMessage()
            )
            for record in self.caplog.records[self.start :]
        )


def _data(calls: list[ServiceCall]) -> list[dict[str, Any]]:
    """Return the data of every recorded call."""
    return [dict(call.data) for call in calls]


# ---------------------------------------------------------------------------
# AP overload
# ---------------------------------------------------------------------------

AP_CLIENTS = "sensor.lobby_ap_wireless_clients"
AP_CLIENTS_2 = "sensor.garage_ap_wireless_clients"
AP_UTILISATION = "sensor.lobby_ap_channel_utilisation"
AP_ACTIONS = [
    {
        "action": "test.record",
        "data": {
            "id": "{{ trigger.id }}",
            "entity": "{{ trigger.entity_id }}",
            "value": "{{ trigger.to_state.state }}",
        },
    }
]


def _ap_inputs(**overrides: Any) -> dict[str, Any]:
    """Return the AP overload inputs: 30 clients, 80% utilisation, five minutes."""
    inputs: dict[str, Any] = {
        "client_sensors": [AP_CLIENTS],
        "client_threshold": 30,
        "utilisation_threshold": 80,
        "duration": FIVE_MINUTES,
        "alert_actions": AP_ACTIONS,
    }
    inputs.update(overrides)
    return inputs


async def test_ap_overload_alerts_when_clients_stay_above_the_threshold(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
) -> None:
    """Crossing the client threshold runs the actions once the duration passes."""
    hass.states.async_set(AP_CLIENTS, "12")
    await _use_blueprint(hass, AP_OVERLOAD, _ap_inputs())

    await _set_state(hass, AP_CLIENTS, "31")
    await _advance(hass, freezer, minutes=4)
    assert calls == []

    await _advance(hass, freezer, minutes=2)
    assert _data(calls) == [
        {"id": "client_overload", "entity": AP_CLIENTS, "value": 31}
    ]


async def test_ap_overload_alerts_when_utilisation_stays_above_the_threshold(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
) -> None:
    """A utilisation sensor from another integration triggers its own alert."""
    hass.states.async_set(AP_CLIENTS, "12")
    hass.states.async_set(AP_UTILISATION, "40")
    await _use_blueprint(
        hass, AP_OVERLOAD, _ap_inputs(utilisation_sensors=[AP_UTILISATION])
    )

    await _set_state(hass, AP_UTILISATION, "79")
    await _advance(hass, freezer, minutes=6)
    assert calls == []

    await _set_state(hass, AP_UTILISATION, "92")
    await _advance(hass, freezer, minutes=6)
    assert _data(calls) == [
        {"id": "utilisation", "entity": AP_UTILISATION, "value": 92}
    ]


@pytest.mark.parametrize(
    "utilisation_sensors", [None, []], ids=["default", "explicit-empty"]
)
async def test_ap_overload_with_no_utilisation_sensor_still_alerts_on_clients(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    utilisation_sensors: list[str] | None,
) -> None:
    """The optional utilisation input can stay at its empty default."""
    hass.states.async_set(AP_CLIENTS, "5")
    inputs = _ap_inputs()
    if utilisation_sensors is not None:
        inputs["utilisation_sensors"] = utilisation_sensors
    await _use_blueprint(hass, AP_OVERLOAD, inputs)

    await _set_state(hass, AP_CLIENTS, "45")
    await _advance(hass, freezer, minutes=6)

    assert _data(calls) == [
        {"id": "client_overload", "entity": AP_CLIENTS, "value": 45}
    ]


@pytest.mark.parametrize("value", ["29", "30"], ids=["below", "equal"])
async def test_ap_overload_ignores_a_value_at_or_below_the_threshold(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    value: str,
) -> None:
    """Overload means strictly more clients than the threshold."""
    hass.states.async_set(AP_CLIENTS, "5")
    await _use_blueprint(hass, AP_OVERLOAD, _ap_inputs())

    await _set_state(hass, AP_CLIENTS, value)
    await _advance(hass, freezer, minutes=10)

    assert calls == []


async def test_ap_overload_drop_before_the_duration_cancels_the_alert(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
) -> None:
    """A short spike must not alert."""
    hass.states.async_set(AP_CLIENTS, "5")
    await _use_blueprint(hass, AP_OVERLOAD, _ap_inputs())

    await _set_state(hass, AP_CLIENTS, "50")
    await _advance(hass, freezer, minutes=2)
    await _set_state(hass, AP_CLIENTS, "10")
    await _advance(hass, freezer, minutes=10)

    assert calls == []


@pytest.mark.parametrize("gap_state", [STATE_UNKNOWN, STATE_UNAVAILABLE])
@pytest.mark.parametrize(
    ("entity_id", "high", "low", "trigger_id"),
    [
        pytest.param(AP_CLIENTS, "50", "10", "client_overload", id="clients"),
        pytest.param(AP_UTILISATION, "95", "20", "utilisation", id="utilisation"),
    ],
)
async def test_ap_overload_ignores_a_crossing_that_follows_unknown_or_unavailable(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    gap_state: str,
    entity_id: str,
    high: str,
    low: str,
    trigger_id: str,
) -> None:
    """After an outage the first high reading says nothing about a real change."""
    hass.states.async_set(AP_CLIENTS, "10")
    hass.states.async_set(AP_UTILISATION, "20")
    await _use_blueprint(
        hass, AP_OVERLOAD, _ap_inputs(utilisation_sensors=[AP_UTILISATION])
    )

    await _set_state(hass, entity_id, gap_state)
    await _set_state(hass, entity_id, high)
    await _advance(hass, freezer, minutes=10)
    assert calls == [], "a reading that follows a gap must not alert"

    # The same automation still alerts for a genuine crossing afterwards.
    await _set_state(hass, entity_id, low)
    await _set_state(hass, entity_id, high)
    await _advance(hass, freezer, minutes=10)
    assert _data(calls) == [{"id": trigger_id, "entity": entity_id, "value": int(high)}]


@pytest.mark.parametrize("gap_state", [STATE_UNKNOWN, STATE_UNAVAILABLE])
async def test_ap_overload_does_not_alert_while_the_sensor_is_unknown_or_unavailable(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    gap_state: str,
) -> None:
    """Going unknown or unavailable on its own never runs the actions."""
    hass.states.async_set(AP_CLIENTS, "50")
    await _use_blueprint(hass, AP_OVERLOAD, _ap_inputs())

    await _set_state(hass, AP_CLIENTS, gap_state)
    await _advance(hass, freezer, minutes=10)

    assert calls == []


async def test_ap_overload_alerts_for_two_access_points_at_the_same_time(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
) -> None:
    """Simultaneous overloads are queued, not dropped as 'already running'."""
    hass.states.async_set(AP_CLIENTS, "5")
    hass.states.async_set(AP_CLIENTS_2, "5")
    actions = [
        {"delay": {"seconds": 5}},
        *AP_ACTIONS,
    ]
    await _use_blueprint(
        hass,
        AP_OVERLOAD,
        _ap_inputs(client_sensors=[AP_CLIENTS, AP_CLIENTS_2], alert_actions=actions),
    )

    hass.states.async_set(AP_CLIENTS, "40")
    hass.states.async_set(AP_CLIENTS_2, "41")
    await hass.async_block_till_done()
    # Both timers fire together; the first run parks on its delay, the second queues.
    _advance_time(hass, freezer, minutes=6)
    await _wait_until(lambda: _active_runs(hass) == 2, "both runs to start")
    assert calls == []
    await _advance_until(
        hass, freezer, lambda: len(calls) >= 1, "the first run to record its call"
    )
    assert len(calls) == 1
    await _advance_until(
        hass, freezer, lambda: len(calls) >= 2, "the queued run to record its call"
    )
    await hass.async_block_till_done()

    assert sorted(call["entity"] for call in _data(calls)) == sorted(
        [AP_CLIENTS, AP_CLIENTS_2]
    )


# ---------------------------------------------------------------------------
# Infrastructure offline
# ---------------------------------------------------------------------------

SWITCH_ONLINE = "binary_sensor.core_switch_status"
SWITCH_2_ONLINE = "binary_sensor.garage_switch_status"
OFFLINE_ACTIONS = [
    {
        "action": "test.record",
        "data": {
            "event": "offline",
            "entity": "{{ trigger.entity_id }}",
            "state": "{{ trigger.to_state.state }}",
        },
    }
]
RECOVERY_ACTIONS = [
    {
        "action": "test.record",
        "data": {
            "event": "recovery",
            "entity": "{{ trigger.entity_id }}",
            "state": "{{ trigger.to_state.state }}",
        },
    }
]


def _offline_inputs(**overrides: Any) -> dict[str, Any]:
    """Return the offline inputs with a five minute duration and both actions."""
    inputs: dict[str, Any] = {
        "connectivity_sensors": [SWITCH_ONLINE],
        "offline_delay": FIVE_MINUTES,
        "offline_actions": OFFLINE_ACTIONS,
        "recovery_actions": RECOVERY_ACTIONS,
    }
    inputs.update(overrides)
    return inputs


async def test_offline_actions_run_when_a_device_stays_off(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
) -> None:
    """A connectivity sensor that is ``off`` for the duration is offline."""
    hass.states.async_set(SWITCH_ONLINE, "on")
    await _use_blueprint(hass, INFRASTRUCTURE_OFFLINE, _offline_inputs())

    await _set_state(hass, SWITCH_ONLINE, "off")
    await _advance(hass, freezer, minutes=4)
    assert calls == []

    await _advance(hass, freezer, minutes=2)
    assert _data(calls) == [
        {"event": "offline", "entity": SWITCH_ONLINE, "state": "off"}
    ]


async def test_offline_actions_run_when_a_device_stays_unavailable(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
) -> None:
    """By default ``unavailable`` counts as offline: that is how a lost device reads."""
    hass.states.async_set(SWITCH_ONLINE, "on")
    await _use_blueprint(hass, INFRASTRUCTURE_OFFLINE, _offline_inputs())

    await _set_state(hass, SWITCH_ONLINE, STATE_UNAVAILABLE)
    await _advance(hass, freezer, minutes=6)

    assert _data(calls) == [
        {"event": "offline", "entity": SWITCH_ONLINE, "state": "unavailable"}
    ]


async def test_unavailable_is_ignored_when_it_is_not_treated_as_offline(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
) -> None:
    """With the switch off, unavailable stays quiet but ``off`` still alerts."""
    hass.states.async_set(SWITCH_ONLINE, "on")
    await _use_blueprint(
        hass,
        INFRASTRUCTURE_OFFLINE,
        _offline_inputs(treat_unavailable_as_offline=False),
    )

    await _set_state(hass, SWITCH_ONLINE, STATE_UNAVAILABLE)
    await _advance(hass, freezer, minutes=6)
    assert calls == []

    await _set_state(hass, SWITCH_ONLINE, "on")
    await _set_state(hass, SWITCH_ONLINE, "off")
    await _advance(hass, freezer, minutes=6)
    assert _data(calls) == [
        {"event": "offline", "entity": SWITCH_ONLINE, "state": "off"}
    ]


@pytest.mark.parametrize("previous", ["off", STATE_UNAVAILABLE])
async def test_recovery_actions_run_when_a_device_comes_back_on(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    previous: str,
) -> None:
    """Returning to ``on`` from ``off`` or ``unavailable`` is a recovery."""
    hass.states.async_set(SWITCH_ONLINE, previous)
    await _use_blueprint(hass, INFRASTRUCTURE_OFFLINE, _offline_inputs())

    await _advance(hass, freezer, minutes=6)
    await _set_state(hass, SWITCH_ONLINE, "on")

    assert _data(calls) == [
        {"event": "recovery", "entity": SWITCH_ONLINE, "state": "on"}
    ]


async def test_offline_then_recovery_runs_each_action_list_once(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
) -> None:
    """The trigger IDs keep the two action lists apart."""
    hass.states.async_set(SWITCH_ONLINE, "on")
    await _use_blueprint(hass, INFRASTRUCTURE_OFFLINE, _offline_inputs())

    await _set_state(hass, SWITCH_ONLINE, "off")
    await _advance(hass, freezer, minutes=6)
    await _set_state(hass, SWITCH_ONLINE, "on")

    assert [call["event"] for call in _data(calls)] == ["offline", "recovery"]


@pytest.mark.parametrize("offline_state", ["off", STATE_UNAVAILABLE])
async def test_a_short_outage_does_not_raise_offline_or_recovery_actions(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    offline_state: str,
) -> None:
    """Coming back inside the duration cancels the offline alert."""
    hass.states.async_set(SWITCH_ONLINE, "on")
    await _use_blueprint(hass, INFRASTRUCTURE_OFFLINE, _offline_inputs())

    await _set_state(hass, SWITCH_ONLINE, offline_state)
    await _advance(hass, freezer, minutes=2)
    await _set_state(hass, SWITCH_ONLINE, "on")
    await _advance(hass, freezer, minutes=10)

    assert calls == []


async def test_recovery_actions_are_optional(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
) -> None:
    """With no recovery actions the offline alert works and recovery is silent."""
    inputs = _offline_inputs()
    del inputs["recovery_actions"]
    hass.states.async_set(SWITCH_ONLINE, "on")
    await _use_blueprint(hass, INFRASTRUCTURE_OFFLINE, inputs)

    await _set_state(hass, SWITCH_ONLINE, "off")
    await _advance(hass, freezer, minutes=6)
    await _set_state(hass, SWITCH_ONLINE, "on")

    assert [call["event"] for call in _data(calls)] == ["offline"]


async def test_offline_alerts_for_several_devices_that_fail_together(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
) -> None:
    """A failed uplink takes several devices down at once: none may be dropped."""
    hass.states.async_set(SWITCH_ONLINE, "on")
    hass.states.async_set(SWITCH_2_ONLINE, "on")
    actions = [{"delay": {"seconds": 5}}, *OFFLINE_ACTIONS]
    await _use_blueprint(
        hass,
        INFRASTRUCTURE_OFFLINE,
        _offline_inputs(
            connectivity_sensors=[SWITCH_ONLINE, SWITCH_2_ONLINE],
            offline_actions=actions,
        ),
    )

    hass.states.async_set(SWITCH_ONLINE, STATE_UNAVAILABLE)
    hass.states.async_set(SWITCH_2_ONLINE, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    # Both timers fire together; the first run parks on its delay, the second queues.
    _advance_time(hass, freezer, minutes=6)
    await _wait_until(lambda: _active_runs(hass) == 2, "both runs to start")
    assert calls == []
    await _advance_until(
        hass, freezer, lambda: len(calls) >= 1, "the first run to record its call"
    )
    assert len(calls) == 1
    await _advance_until(
        hass, freezer, lambda: len(calls) >= 2, "the queued run to record its call"
    )
    await hass.async_block_till_done()

    assert sorted(call["entity"] for call in _data(calls)) == sorted(
        [SWITCH_ONLINE, SWITCH_2_ONLINE]
    )


async def test_offline_ignores_sensors_that_were_not_selected(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
) -> None:
    """Only the selected connectivity sensors count."""
    hass.states.async_set(SWITCH_ONLINE, "on")
    hass.states.async_set(SWITCH_2_ONLINE, "on")
    await _use_blueprint(hass, INFRASTRUCTURE_OFFLINE, _offline_inputs())

    await _set_state(hass, SWITCH_2_ONLINE, "off")
    await _advance(hass, freezer, minutes=10)
    await _set_state(hass, SWITCH_2_ONLINE, "on")

    assert calls == []


# ---------------------------------------------------------------------------
# Secure VLAN: new client alert, against the real device_tracker platform
# ---------------------------------------------------------------------------

VLAN_ACTIONS = [
    {
        "action": "test.record",
        "data": {
            "entity": "{{ tracker }}",
            "ip": "{{ ip_address }}",
            "prefix": "{{ matched_prefix }}",
        },
    }
]


def _vlan_inputs(**overrides: Any) -> dict[str, Any]:
    """Return the secure VLAN inputs with one secure prefix."""
    inputs: dict[str, Any] = {
        "subnet_prefixes": ["10.0.50."],
        "alert_actions": VLAN_ACTIONS,
    }
    inputs.update(overrides)
    return inputs


def _client(number: int, name: str, ip: str) -> dict[str, Any]:
    """Build one connected wireless client payload."""
    return {
        "id": f"client_{number}",
        "macAddress": f"aa:bb:cc:dd:ee:{number:02x}",
        "name": name,
        "hostname": name.lower().replace(" ", "-"),
        "type": "WIRELESS",
        "connected": True,
        "uplinkDeviceId": None,
        "ipAddress": ip,
    }


@pytest.fixture
async def tracking_entry(
    hass: HomeAssistant,
    *,
    mock_config_entry: MockConfigEntry,
    mock_network_client: MagicMock,
    mock_protect_client: MagicMock,
    mock_local_auth: MagicMock,
    enable_custom_integrations: None,
) -> MockConfigEntry:
    """Set up the integration with client tracking on and no client connected."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_TRACK_WIFI_CLIENTS: True}
    )
    mock_network_client.sites.get_all.return_value = [
        {"id": "default", "name": "Default"}
    ]
    mock_network_client.clients.get_all.return_value = []
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


async def _connect(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    network_client: MagicMock,
    *clients: dict[str, Any],
) -> None:
    """Make clients appear on the controller and let the integration poll them."""
    network_client.clients.get_all.return_value = list(clients)
    await entry.runtime_data.device_coordinator.async_refresh()
    await _wait_until(
        lambda: all(
            hass.states.get("device_tracker." + c["name"].lower().replace(" ", "_"))
            is not None
            for c in clients
        ),
        "the real integration to publish every client tracker",
    )


async def test_new_tracker_on_a_secure_prefix_runs_the_actions(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    tracking_entry: MockConfigEntry,
    mock_network_client: MagicMock,
) -> None:
    """A tracker created for a client in a secure prefix reaches the actions."""
    await _use_blueprint(hass, SECURE_SUBNET, _vlan_inputs())

    await _connect(
        hass,
        tracking_entry,
        mock_network_client,
        _client(1, "Guest Laptop", "10.0.50.23"),
    )

    await _finish_secure_wait(hass, freezer, blueprint_dir)

    # The real platform publishes the ip attribute the blueprint filters on.
    state = hass.states.get("device_tracker.guest_laptop")
    assert state is not None
    assert state.attributes["ip"] == "10.0.50.23"
    assert state.attributes["source_type"] == "router"
    assert "ip_address" not in state.attributes
    assert _data(calls) == [
        {
            "entity": "device_tracker.guest_laptop",
            "prefix": "10.0.50.",
            "ip": "10.0.50.23",
        }
    ]


async def test_new_tracker_on_another_prefix_is_ignored(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    tracking_entry: MockConfigEntry,
    mock_network_client: MagicMock,
) -> None:
    """Clients outside the secure prefixes do not alert."""
    await _use_blueprint(hass, SECURE_SUBNET, _vlan_inputs())

    await _connect(
        hass,
        tracking_entry,
        mock_network_client,
        _client(2, "Living Room TV", "192.168.1.40"),
        # 10.0.5. is a different network from 10.0.50.
        _client(3, "Printer", "10.0.5.12"),
    )

    await _finish_secure_wait(hass, freezer, blueprint_dir)
    assert hass.states.get("device_tracker.living_room_tv") is not None
    assert hass.states.get("device_tracker.printer") is not None
    assert calls == []


async def test_allowlisted_tracker_on_a_secure_prefix_is_excluded(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    tracking_entry: MockConfigEntry,
    mock_network_client: MagicMock,
) -> None:
    """A known device on the secure VLAN does not alert; a stranger still does."""
    await _use_blueprint(
        hass,
        SECURE_SUBNET,
        _vlan_inputs(allowed_trackers=["device_tracker.alarm_panel"]),
    )

    await _connect(
        hass,
        tracking_entry,
        mock_network_client,
        _client(4, "Alarm Panel", "10.0.50.10"),
        _client(5, "Stranger Phone", "10.0.50.77"),
    )

    await _finish_secure_wait(hass, freezer, blueprint_dir)

    # Both trackers exist; the allowlisted entity id is the one that was created.
    assert hass.states.get("device_tracker.alarm_panel") is not None
    assert [call["entity"] for call in _data(calls)] == [
        "device_tracker.stranger_phone"
    ]


async def test_first_time_tracking_creates_many_trackers_and_each_secure_one_alerts(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    tracking_entry: MockConfigEntry,
    mock_network_client: MagicMock,
) -> None:
    """A burst of new trackers is processed completely, across several prefixes."""
    await _use_blueprint(
        hass,
        SECURE_SUBNET,
        _vlan_inputs(subnet_prefixes=["10.0.50.", "10.0.60."]),
    )
    clients = [
        _client(10 + n, f"Camera {n}", f"10.0.{50 if n % 2 else 60}.{n}")
        for n in range(1, 7)
    ] + [_client(30 + n, f"Laptop {n}", f"192.168.1.{n}") for n in range(1, 7)]

    await _connect(hass, tracking_entry, mock_network_client, *clients)

    await _finish_secure_wait(hass, freezer, blueprint_dir)
    assert sorted(
        call["entity"].removeprefix("device_tracker.") for call in _data(calls)
    ) == [f"camera_{n}" for n in range(1, 7)]
    assert sorted(call["ip"] for call in _data(calls)) == sorted(
        f"10.0.{50 if n % 2 else 60}.{n}" for n in range(1, 7)
    )
    assert {call["entity"]: call["prefix"] for call in _data(calls)} == {
        f"device_tracker.camera_{n}": f"10.0.{50 if n % 2 else 60}."
        for n in range(1, 7)
    }


async def test_blank_secure_prefix_never_matches_every_client(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    tracking_entry: MockConfigEntry,
    mock_network_client: MagicMock,
) -> None:
    """An empty or whitespace prefix entry is ignored, not a wildcard."""
    await _use_blueprint(
        hass, SECURE_SUBNET, _vlan_inputs(subnet_prefixes=["", "  ", "10.0.50."])
    )

    await _connect(
        hass,
        tracking_entry,
        mock_network_client,
        _client(6, "Elsewhere", "192.168.9.9"),
    )
    await _finish_secure_wait(hass, freezer, blueprint_dir)
    assert calls == []

    await _connect(
        hass,
        tracking_entry,
        mock_network_client,
        _client(6, "Elsewhere", "192.168.9.9"),
        _client(7, "Inside", "10.0.50.9"),
    )
    await _finish_secure_wait(hass, freezer, blueprint_dir)
    assert [
        call["entity"].removeprefix("device_tracker.") for call in _data(calls)
    ] == ["inside"]


async def test_prefix_must_match_the_start_of_the_ip_address(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    tracking_entry: MockConfigEntry,
    mock_network_client: MagicMock,
) -> None:
    """``168.1.`` is inside 192.168.1.40 but is not its prefix."""
    await _use_blueprint(hass, SECURE_SUBNET, _vlan_inputs(subnet_prefixes=["168.1."]))

    await _connect(
        hass,
        tracking_entry,
        mock_network_client,
        _client(20, "Inside Only", "192.168.1.40"),
        _client(21, "Starts With", "168.1.2.3"),
    )

    await _finish_secure_wait(hass, freezer, blueprint_dir)
    assert [
        call["entity"].removeprefix("device_tracker.") for call in _data(calls)
    ] == ["starts_with"]


async def test_new_secure_clients_that_connect_together_all_run_slow_actions(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
) -> None:
    """All runs enter the user actions before any slow action is released."""
    mock_config_entry.add_to_hass(hass)
    hass.states.async_set("input_boolean.release", "off")
    await _use_blueprint(
        hass,
        SECURE_SUBNET,
        _vlan_inputs(
            alert_actions=[
                *VLAN_ACTIONS,
                {"wait_template": "{{ is_state('input_boolean.release', 'on') }}"},
                {"action": "test.record", "data": {"finished": "{{ tracker }}"}},
            ]
        ),
    )
    trackers = [
        _create_tracker(
            hass, entity_registry, mock_config_entry, f"camera_{n}", ip=f"10.0.50.{n}"
        )
        for n in range(1, 4)
    ]
    await _wait_until(
        lambda: _active_runs(hass) == 3, "three creations inside 10 seconds"
    )
    _advance_time(hass, freezer, seconds=10)
    await _wait_until(
        lambda: len(calls) == 3,
        "all three parallel runs to enter alert_actions before releasing any run",
    )
    assert sorted(call["entity"] for call in _data(calls)) == sorted(trackers)
    assert all(call["prefix"] == "10.0.50." for call in _data(calls))
    assert _active_runs(hass) == 3
    hass.states.async_set("input_boolean.release", "on")
    await _wait_until(lambda: len(calls) == 6, "all three slow actions to finish")
    await hass.async_block_till_done()
    assert sorted(call["finished"] for call in _data(calls)[3:]) == sorted(trackers)


async def test_single_octet_prefix_keeps_its_trailing_dot(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    tracking_entry: MockConfigEntry,
    mock_network_client: MagicMock,
) -> None:
    """``10.`` means the whole 10.x network, not the number 10.0 and not 100.x."""
    await _use_blueprint(hass, SECURE_SUBNET, _vlan_inputs(subnet_prefixes=["10."]))

    await _connect(
        hass,
        tracking_entry,
        mock_network_client,
        _client(8, "Inside Ten", "10.5.1.1"),
        _client(9, "Carrier Grade NAT", "100.64.0.9"),
    )

    await _finish_secure_wait(hass, freezer, blueprint_dir)
    assert [
        call["entity"].removeprefix("device_tracker.") for call in _data(calls)
    ] == ["inside_ten"]


async def test_a_tracker_that_never_gets_a_state_finishes_after_the_fixed_delay(
    hass: HomeAssistant,
    *,
    entity_registry: er.EntityRegistry,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    tracking_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A registry entry without a state ends the run instead of hanging or alerting."""
    await _use_blueprint(hass, SECURE_SUBNET, _vlan_inputs())

    tracker = entity_registry.async_get_or_create(
        "device_tracker",
        DOMAIN,
        "stateless-client",
        config_entry=tracking_entry,
        suggested_object_id="stateless_client",
    ).entity_id
    entity_sources(hass)[tracker] = {
        "domain": DOMAIN,
        "config_entry": tracking_entry.entry_id,
    }
    # The run is parked on the fixed delay: do not block on it.
    await _wait_until(
        lambda: _active_runs(hass) == 1, "the run to start its fixed delay"
    )

    await _advance_until(
        hass,
        freezer,
        lambda: _active_runs(hass) == 0,
        "the fixed-delay run to finish without a state",
    )
    await hass.async_block_till_done()

    assert calls == []
    assert _active_runs(hass) == 0


async def _finish_secure_wait(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    progress: _BlueprintProgress,
) -> None:
    """Observe admission/rejection of every create event before advancing time."""
    await _wait_until(
        lambda: progress.creations > 0 and progress.outcomes == progress.creations,
        "every native creation to reach its fixed delay or fail admission",
    )
    _advance_time(hass, freezer, seconds=10)
    await _wait_until(lambda: _active_runs(hass) == 0, "secure-client runs to finish")
    await hass.async_block_till_done()


def _create_tracker(
    hass: HomeAssistant,
    registry: er.EntityRegistry,
    entry: MockConfigEntry,
    object_id: str,
    *,
    platform: str = DOMAIN,
    source_type: str = "router",
    ip: Any = "10.0.50.23",
    domain: str = "device_tracker",
) -> str:
    """Create a real registry entry (native event), then publish its state."""
    tracker = registry.async_get_or_create(
        domain,
        platform,
        object_id,
        config_entry=entry,
        suggested_object_id=object_id,
    ).entity_id
    # Registry + states alone omit the entity-platform source metadata that
    # integration_entities() reads. Populate HA's source registry, not the template.
    entity_sources(hass)[tracker] = {"domain": platform, "config_entry": entry.entry_id}
    hass.states.async_set(tracker, "home", {"source_type": source_type, "ip": ip})
    return tracker


@pytest.mark.parametrize(
    ("overrides", "tracker_options"),
    [
        pytest.param({"subnet_prefixes": ["", "  "]}, {}, id="blank-prefixes"),
        pytest.param(
            {"allowed_trackers": ["device_tracker.registry_client"]},
            {},
            id="allowlisted",
        ),
        pytest.param({}, {"source_type": "gps"}, id="mobility-gps"),
        pytest.param({}, {"source_type": ""}, id="missing-source"),
        pytest.param({}, {"ip": "192.168.1.5"}, id="other-subnet"),
        pytest.param({}, {"ip": None}, id="missing-ip"),
        pytest.param({}, {"ip": ""}, id="empty-ip"),
        pytest.param({}, {"ip": 123}, id="non-string-ip"),
        pytest.param({}, {"platform": "other_integration"}, id="other-platform"),
        pytest.param({}, {"domain": "sensor"}, id="non-tracker"),
    ],
)
async def test_secure_subnet_registry_creation_filters(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    overrides: dict[str, Any],
    tracker_options: dict[str, Any],
) -> None:
    """Native create events enforce every eligibility/source/address check."""
    mock_config_entry.add_to_hass(hass)
    await _use_blueprint(hass, SECURE_SUBNET, _vlan_inputs(**overrides))
    _create_tracker(
        hass, entity_registry, mock_config_entry, "registry_client", **tracker_options
    )
    await _wait_until(
        lambda: blueprint_dir.outcomes == 1,
        "the native creation to reach its delay or fail admission",
    )
    # These exclusions must happen before admission, not consume a delayed slot.
    if overrides or "platform" in tracker_options or "domain" in tracker_options:
        assert _active_runs(hass) == 0
    else:
        assert _active_runs(hass) == 1
    await _finish_secure_wait(hass, freezer, blueprint_dir)
    assert calls == []


async def test_secure_subnet_waits_exactly_ten_seconds_and_alerts_only_on_creation(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A trimmed matching prefix alerts at 10 seconds; returning clients do not."""
    mock_config_entry.add_to_hass(hass)
    await _use_blueprint(
        hass, SECURE_SUBNET, _vlan_inputs(subnet_prefixes=[" ", " 10.0.50. "])
    )
    tracker = _create_tracker(
        hass, entity_registry, mock_config_entry, "registry_client"
    )
    await _wait_until(lambda: _active_runs(hass) == 1, "the fixed-delay run to start")
    _advance_time(hass, freezer, seconds=9)
    assert not await _poll(lambda: bool(calls), STEP_SETTLE_SECONDS)
    assert _active_runs(hass) == 1
    _advance_time(hass, freezer, seconds=1)
    await _wait_until(lambda: len(calls) == 1, "the alert at exactly 10 seconds")
    await hass.async_block_till_done()
    assert _data(calls) == [
        {"entity": tracker, "ip": "10.0.50.23", "prefix": "10.0.50."}
    ]
    await _set_state(
        hass, tracker, "not_home", {"source_type": "router", "ip": "10.0.50.23"}
    )
    await _set_state(
        hass, tracker, "home", {"source_type": "router", "ip": "10.0.50.23"}
    )
    await _advance(hass, freezer, seconds=20)
    assert len(calls) == 1


@pytest.mark.parametrize("treat_unavailable", [False, True])
async def test_recovery_from_unavailable_obeys_the_flag(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    treat_unavailable: bool,
) -> None:
    """A long unavailable outage recovers only when the user counts it offline."""
    hass.states.async_set(SWITCH_ONLINE, "on")
    await _use_blueprint(
        hass,
        INFRASTRUCTURE_OFFLINE,
        _offline_inputs(treat_unavailable_as_offline=treat_unavailable),
    )
    await _set_state(hass, SWITCH_ONLINE, STATE_UNAVAILABLE)
    await _advance(hass, freezer, minutes=6)
    await _set_state(hass, SWITCH_ONLINE, "on")
    assert [call["event"] for call in _data(calls)] == (
        ["offline", "recovery"] if treat_unavailable else []
    )


async def test_ap_overload_exposes_inputs_and_trigger_values_to_actions(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
) -> None:
    """User templates can read inputs and trigger values as named variables."""
    hass.states.async_set(AP_CLIENTS, "12")
    await _use_blueprint(
        hass,
        AP_OVERLOAD,
        _ap_inputs(
            alert_actions=[
                {
                    "action": "test.record",
                    "data": {
                        "client_sensors": "{{ client_sensors }}",
                        "client_threshold": "{{ client_threshold }}",
                        "utilisation_sensors": "{{ utilisation_sensors }}",
                        "utilisation_threshold": "{{ utilisation_threshold }}",
                        "duration": "{{ duration }}",
                        "id": "{{ trigger_id }}",
                        "entity": "{{ entity_id }}",
                        "previous": "{{ from_state.state }}",
                        "value": "{{ to_state.state }}",
                    },
                }
            ]
        ),
    )
    await _set_state(hass, AP_CLIENTS, "31")
    await _advance(hass, freezer, minutes=6)
    assert _data(calls) == [
        {
            "client_sensors": [AP_CLIENTS],
            "client_threshold": 30,
            "utilisation_sensors": [],
            "utilisation_threshold": 80,
            "duration": FIVE_MINUTES,
            "id": "client_overload",
            "entity": AP_CLIENTS,
            "previous": 12,
            "value": 31,
        }
    ]


async def test_ap_overload_runs_repeat_actions_with_action_scope_templates(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
) -> None:
    """Action templates retain access to repeat.item when the blueprint runs."""
    hass.states.async_set(AP_CLIENTS, "12")
    await _use_blueprint(
        hass,
        AP_OVERLOAD,
        _ap_inputs(
            alert_actions=[
                {
                    "repeat": {
                        "for_each": ["a", "b"],
                        "sequence": [
                            {
                                "action": "test.record",
                                "data": {"item": "{{ repeat.item }}"},
                            }
                        ],
                    }
                },
                {"action": "test.record", "data": {"item": "plain"}},
            ]
        ),
    )
    await _set_state(hass, AP_CLIENTS, "31")
    await _advance(hass, freezer, minutes=6)
    assert _data(calls) == [{"item": "a"}, {"item": "b"}, {"item": "plain"}]


@pytest.mark.parametrize(
    ("offline_delay", "delay_seconds"),
    [
        pytest.param("00:05:00", 300, id="hh-mm-ss"),
        pytest.param("00:05", 300, id="hh-mm"),
        pytest.param(300, 300, id="seconds"),
        pytest.param("01:30:15", 5415, id="hh-mm-ss-with-hours-and-seconds"),
        pytest.param("01:30", 5400, id="hh-mm-with-hours"),
        pytest.param(
            {"hours": 1, "minutes": 30, "seconds": 15},
            5415,
            id="mapping-with-hours-and-seconds",
        ),
    ],
)
@pytest.mark.parametrize("shortfall", [1, 0], ids=["one-second-short", "at-delay"])
async def test_recovery_accepts_string_offline_delay(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    offline_delay: str | int | dict[str, int],
    delay_seconds: int,
    shortfall: int,
) -> None:
    """Duration forms permit recovery only once the full delay has elapsed."""
    hass.states.async_set(SWITCH_ONLINE, "off")
    await _use_blueprint(
        hass, INFRASTRUCTURE_OFFLINE, _offline_inputs(offline_delay=offline_delay)
    )
    await _advance(hass, freezer, seconds=delay_seconds - shortfall)
    await _set_state(hass, SWITCH_ONLINE, "on")
    assert _data(calls) == (
        []
        if shortfall
        else [{"event": "recovery", "entity": SWITCH_ONLINE, "state": "on"}]
    )


@pytest.mark.parametrize("elapsed", [299, 300], ids=["below-delay", "exact-delay"])
async def test_recovery_previous_state_duration_boundary(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    elapsed: int,
) -> None:
    """Recovery requires at least, rather than strictly more than, offline_delay."""
    hass.states.async_set(SWITCH_ONLINE, "off")
    await _use_blueprint(hass, INFRASTRUCTURE_OFFLINE, _offline_inputs())
    await _advance(hass, freezer, seconds=elapsed)
    await _set_state(hass, SWITCH_ONLINE, "on")
    assert _data(calls) == (
        [{"event": "recovery", "entity": SWITCH_ONLINE, "state": "on"}]
        if elapsed == 300
        else []
    )


@pytest.mark.parametrize("address_at", [9, 11], ids=["inside-wait", "after-wait"])
async def test_secure_subnet_reads_ip_once_after_the_fixed_wait(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    address_at: int,
) -> None:
    """An IP arriving during the wait alerts; an IP arriving afterwards does not."""
    mock_config_entry.add_to_hass(hass)
    await _use_blueprint(hass, SECURE_SUBNET, _vlan_inputs())
    tracker = _create_tracker(
        hass, entity_registry, mock_config_entry, "late_address", ip=None
    )
    await _wait_until(lambda: blueprint_dir.outcomes == 1, "the fixed delay to begin")
    _advance_time(hass, freezer, seconds=9 if address_at == 9 else 10)
    if address_at == 11:
        await _wait_until(lambda: _active_runs(hass) == 0, "the missing-IP run to end")
        _advance_time(hass, freezer, seconds=1)
    hass.states.async_set(
        tracker, "home", {"source_type": "router", "ip": "10.0.50.23"}
    )
    if address_at == 9:
        assert not await _poll(lambda: bool(calls), STEP_SETTLE_SECONDS)
        _advance_time(hass, freezer, seconds=1)
        await _wait_until(
            lambda: len(calls) == 1, "the IP at the fixed deadline to alert"
        )
    await hass.async_block_till_done()
    assert _data(calls) == (
        [{"entity": tracker, "ip": "10.0.50.23", "prefix": "10.0.50."}]
        if address_at == 9
        else []
    )


@pytest.mark.parametrize("treat_unavailable", [True, False])
async def test_offline_alerts_when_device_transitions_through_brief_unavailable(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    treat_unavailable: bool,
) -> None:
    """Brief unavailable before off still runs offline actions once delay elapses."""
    hass.states.async_set(SWITCH_ONLINE, "on")
    await _use_blueprint(
        hass,
        INFRASTRUCTURE_OFFLINE,
        _offline_inputs(treat_unavailable_as_offline=treat_unavailable),
    )

    await _set_state(hass, SWITCH_ONLINE, STATE_UNAVAILABLE)
    await _advance(hass, freezer, minutes=2)
    assert calls == []

    await _set_state(hass, SWITCH_ONLINE, "off")
    await _advance(hass, freezer, minutes=4)
    assert calls == []

    await _advance(hass, freezer, minutes=2)
    assert _data(calls) == [
        {"event": "offline", "entity": SWITCH_ONLINE, "state": "off"}
    ]


async def test_offline_alerts_when_device_transitions_through_brief_off(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
) -> None:
    """Brief off before unavailable still alerts when unavailable counts as offline."""
    hass.states.async_set(SWITCH_ONLINE, "on")
    await _use_blueprint(
        hass,
        INFRASTRUCTURE_OFFLINE,
        _offline_inputs(treat_unavailable_as_offline=True),
    )

    await _set_state(hass, SWITCH_ONLINE, "off")
    await _advance(hass, freezer, minutes=2)
    assert calls == []

    await _set_state(hass, SWITCH_ONLINE, STATE_UNAVAILABLE)
    await _advance(hass, freezer, minutes=4)
    assert calls == []

    await _advance(hass, freezer, minutes=2)
    assert _data(calls) == [
        {"event": "offline", "entity": SWITCH_ONLINE, "state": "unavailable"}
    ]


async def test_offline_does_not_re_alert_when_already_alerted_state_changes(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
) -> None:
    """An offline state that already alerted does not trigger a duplicate alert."""
    hass.states.async_set(SWITCH_ONLINE, "on")
    await _use_blueprint(
        hass,
        INFRASTRUCTURE_OFFLINE,
        _offline_inputs(treat_unavailable_as_offline=True),
    )

    await _set_state(hass, SWITCH_ONLINE, "off")
    await _advance(hass, freezer, minutes=6)
    assert _data(calls) == [
        {"event": "offline", "entity": SWITCH_ONLINE, "state": "off"}
    ]

    await _set_state(hass, SWITCH_ONLINE, STATE_UNAVAILABLE)
    await _advance(hass, freezer, minutes=6)
    assert len(calls) == 1


async def test_offline_alerts_on_off_after_unalerted_unavailable(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
) -> None:
    """With unavailable not treated offline, subsequent off held past delay alerts."""
    hass.states.async_set(SWITCH_ONLINE, "on")
    await _use_blueprint(
        hass,
        INFRASTRUCTURE_OFFLINE,
        _offline_inputs(treat_unavailable_as_offline=False),
    )

    await _set_state(hass, SWITCH_ONLINE, STATE_UNAVAILABLE)
    await _advance(hass, freezer, minutes=6)
    assert calls == []

    await _set_state(hass, SWITCH_ONLINE, "off")
    await _advance(hass, freezer, minutes=6)
    assert _data(calls) == [
        {"event": "offline", "entity": SWITCH_ONLINE, "state": "off"}
    ]


@pytest.mark.parametrize("treat_unavailable", [True, False])
@pytest.mark.parametrize("startup_minutes", [0, 6])
async def test_offline_alerts_after_restored_startup_state(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
    treat_unavailable: bool,
    startup_minutes: int,
) -> None:
    """A device found offline after a restart alerts once the delay passes.

    A restored startup state never counts as already alerted, even when the
    integration takes longer than the delay to load, so a device that went
    offline while Home Assistant was down is not missed.
    """
    hass.states.async_set(SWITCH_ONLINE, STATE_UNAVAILABLE, {"restored": True})
    await _use_blueprint(
        hass,
        INFRASTRUCTURE_OFFLINE,
        _offline_inputs(treat_unavailable_as_offline=treat_unavailable),
    )
    if startup_minutes:
        await _advance(hass, freezer, minutes=startup_minutes)

    await _set_state(hass, SWITCH_ONLINE, "off")
    await _advance(hass, freezer, minutes=4)
    assert calls == []

    await _advance(hass, freezer, minutes=2)
    assert _data(calls) == [
        {"event": "offline", "entity": SWITCH_ONLINE, "state": "off"}
    ]


async def test_offline_skips_brief_off_after_restored_startup_state(
    hass: HomeAssistant,
    *,
    freezer: FrozenDateTimeFactory,
    blueprint_dir: _BlueprintProgress,
    calls: list[ServiceCall],
) -> None:
    """A device that is briefly off after a restart and comes back stays quiet."""
    hass.states.async_set(SWITCH_ONLINE, STATE_UNAVAILABLE, {"restored": True})
    await _use_blueprint(hass, INFRASTRUCTURE_OFFLINE, _offline_inputs())

    await _set_state(hass, SWITCH_ONLINE, "off")
    await _advance(hass, freezer, minutes=2)
    await _set_state(hass, SWITCH_ONLINE, "on")
    await _advance(hass, freezer, minutes=6)
    assert calls == []
