# Copyright 2026 UniFi Insights contributors
"""
Tests for UniFi Insights service actions.

Every handler resolves one target with a ``service_targets`` resolver and runs
one owner command (ADR-010). The handler tests patch the resolver that
services.py imports and return a typed result whose command field is an
autospec of the owner class, so they assert only "the resolver returned X, so
the handler awaited this command with these arguments". Routing itself is
tested with real registries in test_service_targets.py; a few wiring tests
here run the real resolver end to end, one per target family.
"""

import ast
import inspect
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Self, cast
from unittest.mock import AsyncMock, MagicMock, call, create_autospec, patch

import pytest
import voluptuous as vol
import yaml
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api import ApiKeyAuth, ConnectionType
from unifi_official_api.network.client import UniFiNetworkClient
from unifi_official_api.network.models.client import ClientType
from unifi_official_api.protect import UniFiProtectClient
from yarl import URL

from custom_components.unifi_insights import services
from custom_components.unifi_insights.const import (
    DEFAULT_VOUCHER_NAME,
    DOMAIN,
    SERVICE_POWER_CYCLE_PORT,
)
from custom_components.unifi_insights.coordinators.access import (
    UnifiInsightsAccessCoordinator,
)
from custom_components.unifi_insights.coordinators.carrier_fabric import (
    UnifiInsightsCarrierFabricCoordinator,
)
from custom_components.unifi_insights.coordinators.config import (
    UnifiInsightsConfigCoordinator,
)
from custom_components.unifi_insights.coordinators.device import (
    UnifiInsightsDeviceCoordinator,
)
from custom_components.unifi_insights.coordinators.facade import (
    UnifiInsightsFacadeCoordinator,
)
from custom_components.unifi_insights.coordinators.protect import (
    UnifiInsightsProtectCoordinator,
)
from custom_components.unifi_insights.data_transforms import percent_to_led_level
from custom_components.unifi_insights.service_targets import (
    AccessTarget,
    CarrierSubscriberTarget,
    ChimePairingTarget,
    ConsoleRefreshTarget,
    NetworkClientTarget,
    NetworkConsoleTarget,
    NetworkDeviceTarget,
    NetworkSiteTarget,
    ProtectConsoleTarget,
    ProtectTarget,
    RefreshSelection,
    RuleExportTarget,
)
from custom_components.unifi_insights.services import (
    SERVICE_RECONNECT_CLIENT,
    SERVICE_REFRESH_DATA,
    SERVICE_REMOVE_CLIENTS,
    SERVICE_RESTART_DEVICE,
    SERVICE_SET_CHIME_RINGTONE,
    _filter_clients_for_removal,
    _is_wireless_client,
    async_setup_services,
    async_unload_services,
)
from tests.fixtures.assertions import require_mock
from tests.fixtures.command_owners import autospec_command_owners

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

_SERVICES = "custom_components.unifi_insights.services"
SUB_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
async def services_ready(hass: HomeAssistant) -> Any:
    """Register the service actions for one test and remove them after."""
    await async_setup_services(hass)
    yield
    await async_unload_services(hass)


def _real_protect_owner(
    hass: HomeAssistant, client: UniFiProtectClient
) -> UnifiInsightsProtectCoordinator:
    """Return a real Protect owner over ``client`` for end-to-end service tests."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    return UnifiInsightsProtectCoordinator(hass, MagicMock(), client, entry)


# --- Handlers: one resolved target, one owner command ----------------------


def _cache(data: Any = None) -> Any:
    """Return a cache read view that only holds explicit cached data."""
    cache = MagicMock(spec_set=["data"])
    cache.data = {} if data is None else data
    return cache


def _owner(owner_class: type) -> Any:
    """Return a strict autospec of an ADR-010 owner class."""
    return create_autospec(owner_class, instance=True)


_WIRELESS_CLIENT = {
    "clients": {"s1": {"native": {"connected": True, "type": "WIRELESS"}}}
}

# resolver name -> (owner class, builder of the typed result around the owner)
_RESULTS: dict[str, tuple[type, Callable[[Any], Any]]] = {
    "resolve_network_device_call": (
        UnifiInsightsDeviceCoordinator,
        lambda owner: NetworkDeviceTarget(
            entry=MagicMock(),
            cache=_cache(),
            device_commands=owner,
            device_id="d1",
            site_id="s1",
        ),
    ),
    "resolve_network_site_call": (
        UnifiInsightsConfigCoordinator,
        lambda owner: NetworkSiteTarget(
            entry=MagicMock(), cache=_cache(), config_commands=owner, site_id="s1"
        ),
    ),
    "resolve_protect_call": (
        UnifiInsightsProtectCoordinator,
        lambda owner: ProtectTarget(
            entry=MagicMock(), cache=_cache(), protect_commands=owner, resource_id="r1"
        ),
    ),
    "resolve_protect_console_call": (
        UnifiInsightsProtectCoordinator,
        lambda owner: ProtectConsoleTarget(
            entry=MagicMock(), cache=_cache(), protect_commands=owner
        ),
    ),
    "resolve_chime_pairing_call": (
        UnifiInsightsProtectCoordinator,
        lambda owner: ChimePairingTarget(
            entry=MagicMock(),
            cache=_cache(),
            protect_commands=owner,
            chime_id="c1",
            camera_ids=("a", "b"),
        ),
    ),
    "resolve_carrier_subscriber_call": (
        UnifiInsightsCarrierFabricCoordinator,
        lambda owner: CarrierSubscriberTarget(
            entry=MagicMock(), carrier_commands=owner, subscriber_id=SUB_ID
        ),
    ),
    "resolve_access_call": (
        UnifiInsightsAccessCoordinator,
        lambda owner: AccessTarget(entry=MagicMock(), access_commands=owner),
    ),
}


def _client_result(owner_class: type) -> Callable[[Any], Any]:
    """Build a client result whose other owner must stay untouched."""

    def build(owner: Any) -> NetworkClientTarget:
        device = owner if owner_class is UnifiInsightsDeviceCoordinator else None
        config = owner if owner_class is UnifiInsightsConfigCoordinator else None
        return NetworkClientTarget(
            entry=MagicMock(),
            cache=_cache(_WIRELESS_CLIENT),
            device_commands=device or _owner(UnifiInsightsDeviceCoordinator),
            config_commands=config or _owner(UnifiInsightsConfigCoordinator),
            client_id="native",
            site_id="s1",
        )

    return build


# (service, call data, resolver, resolver routing args, command, args, kwargs)
_HANDLER_CASES: list[tuple[Any, ...]] = [
    (
        SERVICE_RESTART_DEVICE,
        {"device_id": "x"},
        "resolve_network_device_call",
        {"action": "restart"},
        "async_restart_device",
        ("s1", "d1"),
        {},
    ),
    (
        SERVICE_POWER_CYCLE_PORT,
        {"device_id": "x", "port_idx": 3},
        "resolve_network_device_call",
        {"action": "power_cycle"},
        "async_power_cycle_port",
        ("s1", "d1", 3),
        {},
    ),
    (
        "set_hdr_mode",
        {"camera_id": "x", "mode": "on"},
        "resolve_protect_call",
        ("camera",),
        "async_set_hdr_mode",
        ("r1", "on"),
        {},
    ),
    (
        "set_video_mode",
        {"camera_id": "x", "mode": "highFps"},
        "resolve_protect_call",
        ("camera",),
        "async_set_video_mode",
        ("r1", "highFps"),
        {},
    ),
    (
        "set_video_mode",
        {"camera_id": "x", "mode": "lprReflex"},
        "resolve_protect_call",
        ("camera",),
        "async_set_video_mode",
        ("r1", "lprReflex"),
        {},
    ),
    (
        "set_video_mode",
        {"camera_id": "x", "mode": "lprNoneReflex"},
        "resolve_protect_call",
        ("camera",),
        "async_set_video_mode",
        ("r1", "lprNoneReflex"),
        {},
    ),
    (
        "set_mic_volume",
        {"camera_id": "x", "volume": 100},
        "resolve_protect_call",
        ("camera",),
        "async_set_microphone_volume",
        ("r1", 100),
        {},
    ),
    (
        "set_light_mode",
        {"light_id": "x", "mode": "motion"},
        "resolve_protect_call",
        ("light",),
        "async_set_light_mode",
        ("r1", "motion"),
        {},
    ),
    (
        "set_light_level",
        {"light_id": "x", "level": 50},
        "resolve_protect_call",
        ("light",),
        "async_set_light_brightness",
        ("r1", percent_to_led_level(50)),
        {},
    ),
    (
        "ptz_move",
        {"camera_id": "x", "preset": 3},
        "resolve_protect_call",
        ("camera",),
        "async_move_ptz_to_preset",
        ("r1", 3),
        {},
    ),
    (
        "ptz_patrol",
        {"camera_id": "x", "action": "start", "slot": 2},
        "resolve_protect_call",
        ("camera",),
        "async_start_ptz_patrol",
        ("r1", 2),
        {},
    ),
    (
        "ptz_patrol",
        {"camera_id": "x", "action": "stop"},
        "resolve_protect_call",
        ("camera",),
        "async_stop_ptz_patrol",
        ("r1",),
        {},
    ),
    # A chime's camera_id only selects the console; the command never gets it.
    (
        "set_chime_volume",
        {"chime_id": "x", "volume": 80, "camera_id": "cam9"},
        "resolve_protect_call",
        ("chime",),
        "async_set_chime_volume",
        ("r1", 80),
        {},
    ),
    (
        "play_chime_ringtone",
        {"chime_id": "x", "ringtone_id": "ignored"},
        "resolve_protect_call",
        ("chime",),
        "async_play_chime",
        ("r1",),
        {},
    ),
    (
        "set_chime_ringtone",
        {"chime_id": "x", "ringtone_id": " tone ", "camera_id": "c"},
        "resolve_protect_call",
        ("chime",),
        "async_set_chime_ringtone",
        ("r1", "tone"),
        {},
    ),
    (
        "set_chime_repeat_times",
        {"chime_id": "x", "repeat_times": 4, "camera_id": "c"},
        "resolve_protect_call",
        ("chime",),
        "async_set_chime_repeat",
        ("r1", 4),
        {},
    ),
    (
        "set_chime_paired_doorbells",
        {"chime_id": "x"},
        "resolve_chime_pairing_call",
        (),
        "async_set_chime_paired_cameras",
        ("c1", ["a", "b"]),
        {},
    ),
    # The viewer command receives the supplied liveview_id as given.
    (
        "set_liveview",
        {"viewer_id": "x", "liveview_id": "lv1"},
        "resolve_protect_call",
        ("viewer",),
        "async_update_viewer",
        ("r1",),
        {"liveview": "lv1"},
    ),
    (
        "trigger_alarm",
        {"alarm_id": "a1"},
        "resolve_protect_console_call",
        (),
        "async_trigger_alarm",
        ("a1",),
        {},
    ),
    (
        "create_liveview",
        {"name": "Lobby", "layout": 2},
        "resolve_protect_console_call",
        (),
        "async_create_liveview",
        (),
        {"name": "Lobby", "layout": 2, "is_default": False},
    ),
    (
        "create_liveview",
        {"name": "Lobby", "layout": 2, "is_default": True},
        "resolve_protect_console_call",
        (),
        "async_create_liveview",
        (),
        {"name": "Lobby", "layout": 2, "is_default": True},
    ),
    (
        "authorize_guest",
        {"site_id": "s1", "client_id": "x"},
        "resolve_network_client_call:config",
        (),
        "async_authorize_guest",
        ("s1", "native"),
        {},
    ),
    (
        "reconnect_client",
        {"site_id": "s1", "client_id": "x"},
        "resolve_network_client_call:device",
        (),
        "async_reconnect_client",
        ("s1", "native"),
        {},
    ),
    (
        "generate_voucher",
        {"site_id": "s1"},
        "resolve_network_site_call",
        (),
        "async_generate_voucher",
        ("s1",),
        {
            "count": 1,
            "time_limit_minutes": 480,
            "tx_rate_limit_kbps": None,
            "rx_rate_limit_kbps": None,
            "data_usage_limit_mbytes": None,
            "name": DEFAULT_VOUCHER_NAME,
        },
    ),
    (
        "generate_voucher",
        {
            "site_id": "s1",
            "count": 3,
            "duration_minutes": 60,
            "upload_limit_kbps": 0,
            "download_limit_kbps": 0,
            "data_limit_mb": 0,
            "note": "Lobby",
        },
        "resolve_network_site_call",
        (),
        "async_generate_voucher",
        ("s1",),
        {
            "count": 3,
            "time_limit_minutes": 60,
            "tx_rate_limit_kbps": 0,
            "rx_rate_limit_kbps": 0,
            "data_usage_limit_mbytes": 0,
            "name": "Lobby",
        },
    ),
    (
        "delete_voucher",
        {"site_id": "s1", "voucher_id": "v1"},
        "resolve_network_site_call",
        (),
        "async_delete_voucher",
        ("s1", "v1"),
        {},
    ),
    (
        "carrier_suspend_subscriber",
        {"device_id": "x", "reason": "unpaid"},
        "resolve_carrier_subscriber_call",
        (),
        "async_suspend_subscriber",
        (SUB_ID,),
        {"reason": "unpaid"},
    ),
    (
        "carrier_resume_subscriber",
        {"device_id": "x"},
        "resolve_carrier_subscriber_call",
        (),
        "async_resume_subscriber",
        (SUB_ID,),
        {},
    ),
    (
        "set_access_user_pin",
        {"user_id": "u1", "pin": "1234"},
        "resolve_access_call",
        (),
        "async_set_user_pin",
        ("u1", "1234"),
        {},
    ),
    (
        "remove_access_user_pin",
        {"user_id": "u1"},
        "resolve_access_call",
        (),
        "async_remove_user_pin",
        ("u1",),
        {},
    ),
    (
        "set_access_user_status",
        {"user_id": "u1", "status": "DEACTIVATED"},
        "resolve_access_call",
        (),
        "async_set_user_status",
        ("u1", "DEACTIVATED"),
        {},
    ),
]


def _case_id(case: tuple[Any, ...]) -> str:
    """Name a handler case by its service and command."""
    return f"{case[0]}-{case[4]}"


def _patched_resolver(resolver: str) -> tuple[str, Any, Any]:
    """Return the services.py resolver name, owner and typed result of a case."""
    name, _, client_owner = resolver.partition(":")
    if client_owner:
        owner_class = (
            UnifiInsightsConfigCoordinator
            if client_owner == "config"
            else UnifiInsightsDeviceCoordinator
        )
        owner = _owner(owner_class)
        return name, owner, _client_result(owner_class)(owner)
    owner_class, build = _RESULTS[name]
    owner = _owner(owner_class)
    return name, owner, build(owner)


@pytest.mark.usefixtures("services_ready")
@pytest.mark.parametrize(
    ("service", "data", "resolver", "routing", "command", "args", "kwargs"),
    _HANDLER_CASES,
    ids=[_case_id(case) for case in _HANDLER_CASES],
)
async def test_handler_runs_the_resolved_owner_command(
    hass: HomeAssistant,
    *,
    service: str,
    data: dict[str, Any],
    resolver: str,
    routing: tuple[str, ...] | dict[str, str],
    command: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    """The resolver's result decides the one owner command and its arguments."""
    name, owner, result = _patched_resolver(resolver)

    with patch(f"{_SERVICES}.{name}", return_value=result) as resolve:
        await hass.services.async_call(DOMAIN, service, data, blocking=True)

    assert owner.mock_calls == [getattr(call, command)(*args, **kwargs)]
    (resolved_hass, resolved_call, *extra), extra_kwargs = resolve.call_args
    assert resolved_hass is hass
    assert resolved_call.service == service
    assert (tuple(extra), extra_kwargs) == (
        (routing, {}) if isinstance(routing, tuple) else ((), routing)
    )
    if isinstance(result, NetworkClientTarget):
        other = (
            result.device_commands
            if owner is result.config_commands
            else result.config_commands
        )
        assert require_mock(other).mock_calls == []


@pytest.mark.usefixtures("services_ready")
@pytest.mark.parametrize(
    ("service", "data", "resolver"),
    [case[:3] for case in _HANDLER_CASES],
    ids=[_case_id(case) for case in _HANDLER_CASES],
)
async def test_resolver_error_stops_the_handler(
    hass: HomeAssistant, service: str, data: dict[str, Any], resolver: str
) -> None:
    """A routing error reaches the caller unchanged and no command runs."""
    error = ServiceValidationError(
        translation_domain=DOMAIN, translation_key="no_coordinator"
    )
    with (
        patch(f"{_SERVICES}.{resolver.partition(':')[0]}", side_effect=error),
        pytest.raises(ServiceValidationError) as raised,
    ):
        await hass.services.async_call(DOMAIN, service, data, blocking=True)
    assert raised.value is error


_OWNER_ERROR_CASES = [
    case
    for case in _HANDLER_CASES
    if not case[0].startswith(("carrier_", "set_access"))
]


@pytest.mark.usefixtures("services_ready")
@pytest.mark.parametrize(
    ("service", "data", "resolver", "command"),
    [(*case[:3], case[4]) for case in _OWNER_ERROR_CASES],
    ids=[_case_id(case) for case in _OWNER_ERROR_CASES],
)
async def test_owner_error_reaches_the_caller_unchanged(
    hass: HomeAssistant, service: str, data: dict[str, Any], resolver: str, command: str
) -> None:
    """The owner raises the translated error; the handler does not rewrap it."""
    name, owner, result = _patched_resolver(resolver)
    error = HomeAssistantError(
        translation_domain=DOMAIN, translation_key="command_failed"
    )
    getattr(owner, command).side_effect = error

    with (
        patch(f"{_SERVICES}.{name}", return_value=result),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await hass.services.async_call(DOMAIN, service, data, blocking=True)
    assert raised.value is error


@pytest.mark.usefixtures("services_ready")
@pytest.mark.parametrize(
    ("cached", "client_id", "key", "placeholders"),
    [
        ("not loaded", "native", "client_not_connected", None),
        ({"clients": "not loaded"}, "native", "client_not_connected", None),
        ({"clients": {"s1": "not loaded"}}, "native", "client_not_connected", None),
        ({"clients": {}}, "native", "client_not_connected", None),
        ({"clients": {"s1": {}}}, "native", "client_not_connected", None),
        (
            {"clients": {"s1": {"native": "bad"}}},
            "native",
            "client_not_connected",
            None,
        ),
        (_WIRELESS_CLIENT, "", "client_not_connected", None),
        (
            {"clients": {"s1": {"native": {"connected": False, "type": "WIRELESS"}}}},
            "native",
            "client_not_connected",
            None,
        ),
        (
            {"clients": {"s1": {"native": {"connected": True, "type": "WIRED"}}}},
            "native",
            "client_not_wireless",
            {"client_id": "native"},
        ),
    ],
)
async def test_reconnect_needs_a_connected_wireless_client(
    hass: HomeAssistant,
    cached: Any,
    client_id: str,
    key: str,
    placeholders: dict[str, str] | None,
) -> None:
    """The handler checks the resolved client in the cache before the command."""
    device = _owner(UnifiInsightsDeviceCoordinator)
    target = NetworkClientTarget(
        entry=MagicMock(),
        cache=_cache(cached),
        device_commands=device,
        config_commands=_owner(UnifiInsightsConfigCoordinator),
        client_id=client_id,
        site_id="s1",
    )
    with (
        patch(f"{_SERVICES}.resolve_network_client_call", return_value=target),
        pytest.raises(ServiceValidationError) as raised,
    ):
        await hass.services.async_call(
            DOMAIN,
            "reconnect_client",
            {"site_id": "s1", "client_id": "x"},
            blocking=True,
        )
    assert raised.value.translation_key == key
    assert raised.value.translation_placeholders == placeholders
    assert device.mock_calls == []


@pytest.mark.usefixtures("services_ready")
async def test_authorize_guest_warns_about_ignored_limits(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Guest limits are accepted but not sent; the site's guest policy applies."""
    _, owner, result = _patched_resolver("resolve_network_client_call:config")
    with patch(f"{_SERVICES}.resolve_network_client_call", return_value=result):
        await hass.services.async_call(
            DOMAIN,
            "authorize_guest",
            {
                "site_id": "s1",
                "client_id": "x",
                "duration_minutes": 5,
                "data_limit_mb": 0,
            },
            blocking=True,
        )
    assert (
        "ignores unsupported option(s) duration_minutes, data_limit_mb" in caplog.text
    )
    assert owner.mock_calls == [call.async_authorize_guest("s1", "native")]


# --- Fan-out: refresh_data and remove_clients ------------------------------


def _refresh_target(title: str) -> ConsoleRefreshTarget:
    """Return one console of a refresh selection with an autospecced facade."""
    return ConsoleRefreshTarget(
        title=title,
        entry=MagicMock(),
        facade_commands=_owner(UnifiInsightsFacadeCoordinator),
    )


@pytest.mark.usefixtures("services_ready")
@pytest.mark.parametrize("include_protect", [True, False])
async def test_refresh_runs_every_selected_console(
    hass: HomeAssistant, *, include_protect: bool
) -> None:
    """Each selected console refreshes, with Protect only when the selection says."""
    targets = (_refresh_target("Office"), _refresh_target("Garage"))
    selection = RefreshSelection(
        targets=targets,
        site_id=None if include_protect else "",
        include_protect=include_protect,
    )
    with patch(f"{_SERVICES}.resolve_refresh_call", return_value=selection):
        await hass.services.async_call(DOMAIN, SERVICE_REFRESH_DATA, {}, blocking=True)

    for target in targets:
        assert require_mock(target.facade_commands).mock_calls == [
            call.async_refresh_or_raise(include_protect=include_protect)
        ]


@pytest.mark.usefixtures("services_ready")
async def test_refresh_failure_names_only_the_failed_consoles(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """One failing console does not stop the others; its error text stays in the log."""
    targets = tuple(_refresh_target(title) for title in ("Office", "Garage", "Attic"))
    for target in targets[::2]:
        require_mock(
            target.facade_commands.async_refresh_or_raise
        ).side_effect = HomeAssistantError(f"secret API text from {target.title}")
    selection = RefreshSelection(targets=targets, site_id="s1", include_protect=False)

    with (
        patch(f"{_SERVICES}.resolve_refresh_call", return_value=selection),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await hass.services.async_call(
            DOMAIN, SERVICE_REFRESH_DATA, {"site_id": "s1"}, blocking=True
        )

    assert raised.value.translation_key == "refresh_failed"
    assert raised.value.translation_placeholders == {"targets": "Office, Attic"}
    assert "secret" not in str(raised.value)
    assert "secret API text from Attic" in caplog.text
    require_mock(
        targets[1].facade_commands.async_refresh_or_raise
    ).assert_awaited_once()


def _removal_console(title: str, *site_ids: str) -> NetworkConsoleTarget:
    """Return one console of the removal fan-out with autospecced owners."""
    return NetworkConsoleTarget(
        title=title,
        entry=MagicMock(),
        facade_commands=_owner(UnifiInsightsFacadeCoordinator),
        device_commands=_owner(UnifiInsightsDeviceCoordinator),
        site_ids=site_ids,
    )


_STALE = {"mac": "AA:BB:CC:DD:EE:01", "first_seen": 1, "last_seen": 2}
_NAMED = {"mac": "AA:BB:CC:DD:EE:02", "name": "Phone"}


@pytest.mark.usefixtures("services_ready")
async def test_remove_clients_forgets_per_console_and_site(hass: HomeAssistant) -> None:
    """Each console's sites are read and forgotten through that console's owners."""
    office = _removal_console("Office", "default", "lab")
    garage = _removal_console("Garage", "default")
    empty = _removal_console("Empty")
    require_mock(office.facade_commands.async_get_historical_clients).side_effect = [
        [_STALE],
        [_NAMED],
    ]
    require_mock(garage.facade_commands.async_get_historical_clients).return_value = [
        _STALE
    ]

    with patch(
        f"{_SERVICES}.resolve_network_consoles_call",
        return_value=(office, garage, empty),
    ):
        await hass.services.async_call(
            DOMAIN, SERVICE_REMOVE_CLIENTS, {}, blocking=True
        )

    assert require_mock(office.facade_commands).mock_calls == [
        call.async_get_historical_clients("default"),
        call.async_get_historical_clients("lab"),
    ]
    assert require_mock(office.device_commands).mock_calls == [
        call.async_forget_clients_batch("default", ["aa:bb:cc:dd:ee:01"])
    ]
    assert require_mock(garage.device_commands).mock_calls == [
        call.async_forget_clients_batch("default", ["aa:bb:cc:dd:ee:01"])
    ]
    assert (
        require_mock(empty.facade_commands).mock_calls
        == require_mock(empty.device_commands).mock_calls
        == []
    )


@pytest.mark.usefixtures("services_ready")
@pytest.mark.parametrize("failing", [("Garage",), ("Office", "Garage")])
async def test_remove_clients_reports_failed_sites_without_raw_errors(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, failing: tuple[str, ...]
) -> None:
    """Every console is tried; the error names console and site, not the API text."""
    consoles = (_removal_console("Office", "s1"), _removal_console("Garage", "s1"))
    errors = {}
    for console in consoles:
        if console.title in failing:
            errors[console.title] = HomeAssistantError(f"secret text {console.title}")
            reader = console.facade_commands.async_get_historical_clients
            require_mock(reader).side_effect = errors[console.title]
        else:
            require_mock(
                console.facade_commands.async_get_historical_clients
            ).return_value = [_STALE]

    with (
        patch(f"{_SERVICES}.resolve_network_consoles_call", return_value=consoles),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await hass.services.async_call(
            DOMAIN, SERVICE_REMOVE_CLIENTS, {}, blocking=True
        )

    assert raised.value.translation_key == "remove_clients_failed"
    assert raised.value.translation_placeholders == {
        "failures": "; ".join(f"{title} (site s1)" for title in failing)
    }
    assert "secret" not in str(raised.value)
    assert all(f"secret text {title}" in caplog.text for title in failing)
    # A single failure is chained as the cause; several are not.
    expected_cause = errors["Garage"] if len(failing) == 1 else None
    assert raised.value.__cause__ is expected_cause
    if len(failing) == 1:
        require_mock(
            consoles[0].device_commands.async_forget_clients_batch
        ).assert_awaited_once()


# --- Responses and translated handler errors --------------------------------


@pytest.mark.usefixtures("services_ready")
@pytest.mark.parametrize("redact", [True, False])
async def test_export_returns_the_document_redacted_on_request(
    hass: HomeAssistant, *, redact: bool
) -> None:
    """The export reads one console's rules and redacts them unless told not to."""
    owner = _owner(UnifiInsightsFacadeCoordinator)
    document = {"sites": [{"id": "s1"}]}
    owner.async_export_network_rules.return_value = document
    target = RuleExportTarget(
        entry=MagicMock(), cache=_cache(), export_commands=owner, site_id="s1"
    )
    with (
        patch(f"{_SERVICES}.resolve_rule_export_call", return_value=target),
        patch(
            f"{_SERVICES}.redact_network_rule_export",
            return_value={"sites": "redacted"},
        ),
    ):
        response = await hass.services.async_call(
            DOMAIN,
            "export_network_rules",
            {"site_id": "s1", "rule_types": ["firewall_rule"], "redact": redact},
            blocking=True,
            return_response=True,
        )

    assert response == ({"sites": "redacted"} if redact else document)
    assert owner.mock_calls == [
        call.async_export_network_rules(["s1"], ["firewall_rule"])
    ]


@pytest.mark.usefixtures("services_ready")
async def test_export_without_sites_is_refused(hass: HomeAssistant) -> None:
    """An export of every site that finds none is a translated validation error."""
    owner = _owner(UnifiInsightsFacadeCoordinator)
    owner.async_export_network_rules.return_value = {"sites": []}
    target = RuleExportTarget(
        entry=MagicMock(), cache=_cache(), export_commands=owner, site_id=None
    )
    with (
        patch(f"{_SERVICES}.resolve_rule_export_call", return_value=target),
        pytest.raises(ServiceValidationError) as raised,
    ):
        await hass.services.async_call(
            DOMAIN, "export_network_rules", {}, blocking=True, return_response=True
        )
    assert raised.value.translation_key == "export_no_sites"
    assert owner.mock_calls == [call.async_export_network_rules(None, None)]


# --- Real resolver wiring, one per target family ---------------------------


def _loaded_console(
    hass: HomeAssistant, title: str, data: dict[str, Any], *, protect: bool = False
) -> tuple[MockConfigEntry, Any]:
    """Add a loaded console whose facade has autospecced command owners."""
    entry = MockConfigEntry(domain=DOMAIN, title=title, entry_id=title.lower())
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    facade = autospec_command_owners(MagicMock(data=data))
    facade.protect_client = object() if protect else None
    entry.runtime_data = MagicMock(coordinator=facade)
    return entry, facade


@pytest.mark.usefixtures("services_ready")
async def test_wiring_network_device_by_registry_device(hass: HomeAssistant) -> None:
    """restart_device routes a registry device to the console that owns it."""
    data = {"sites": {"s1": {}}, "devices": {"s1": {"d1": {}}}}
    _, office = _loaded_console(hass, "Office", data)
    garage_entry, garage = _loaded_console(hass, "Garage", data)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=garage_entry.entry_id, identifiers={(DOMAIN, "s1_d1")}
    )

    await hass.services.async_call(
        DOMAIN, SERVICE_RESTART_DEVICE, {"device_id": device.id}, blocking=True
    )

    garage.device_coordinator.async_restart_device.assert_awaited_once_with("s1", "d1")
    office.device_coordinator.async_restart_device.assert_not_awaited()


@pytest.mark.usefixtures("services_ready")
async def test_wiring_network_site(hass: HomeAssistant) -> None:
    """delete_voucher runs on the console whose cache holds the site."""
    _, office = _loaded_console(hass, "Office", {"sites": {"s1": {}}})
    _, garage = _loaded_console(hass, "Garage", {"sites": {"s2": {}}})

    await hass.services.async_call(
        DOMAIN, "delete_voucher", {"site_id": "s2", "voucher_id": "v1"}, blocking=True
    )

    garage.config_coordinator.async_delete_voucher.assert_awaited_once_with("s2", "v1")
    office.config_coordinator.async_delete_voucher.assert_not_awaited()


@pytest.mark.usefixtures("services_ready")
async def test_wiring_protect_entity_and_console(hass: HomeAssistant) -> None:
    """A camera entity and a console_id each select their own Protect console."""
    cams = {"protect": {"cameras": {"cam1": {}}}}
    _, office = _loaded_console(hass, "Office", cams, protect=True)
    garage_entry, garage = _loaded_console(hass, "Garage", cams, protect=True)
    camera = er.async_get(hass).async_get_or_create(
        "camera", DOMAIN, "unifi_insights_camera_cam1", config_entry=garage_entry
    )

    await hass.services.async_call(
        DOMAIN,
        "set_hdr_mode",
        {"entity_id": camera.entity_id, "mode": "off"},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        "trigger_alarm",
        {"alarm_id": "a1", "console_id": "Office"},
        blocking=True,
    )

    garage_owner = garage.require_protect_coordinator.return_value
    office_owner = office.require_protect_coordinator.return_value
    garage_owner.async_set_hdr_mode.assert_awaited_once_with("cam1", "off")
    office_owner.async_trigger_alarm.assert_awaited_once_with("a1")
    office_owner.async_set_hdr_mode.assert_not_awaited()
    garage_owner.async_trigger_alarm.assert_not_awaited()


@pytest.mark.usefixtures("services_ready")
async def test_wiring_refresh_and_removal_fan_out(hass: HomeAssistant) -> None:
    """refresh_data and remove_clients reach every console with Network."""
    _, office = _loaded_console(hass, "Office", {"sites": {"s1": {}}})
    _, garage = _loaded_console(hass, "Garage", {"sites": {"s2": {}}})
    for facade in (office, garage):
        facade.async_refresh_or_raise = AsyncMock()
        facade.async_get_historical_clients = AsyncMock(return_value=[_STALE])

    await hass.services.async_call(DOMAIN, SERVICE_REFRESH_DATA, {}, blocking=True)
    await hass.services.async_call(DOMAIN, SERVICE_REMOVE_CLIENTS, {}, blocking=True)

    for facade, site_id in ((office, "s1"), (garage, "s2")):
        facade.async_refresh_or_raise.assert_awaited_once_with(include_protect=True)
        facade.device_coordinator.async_forget_clients_batch.assert_awaited_once_with(
            site_id, ["aa:bb:cc:dd:ee:01"]
        )


# --- Schemas and registration -----------------------------------------------


@pytest.mark.parametrize("volume", [0, 101])
def test_mic_volume_schema_rejects_out_of_range(volume: int) -> None:
    """The Protect API takes a microphone volume of 1 to 100."""
    with pytest.raises(vol.Invalid):
        services.SET_MIC_VOLUME_SCHEMA({"camera_id": "cam1", "volume": volume})


class TestAsyncSetupServices:
    """Tests for async_setup_services."""

    async def test_setup_services_registers_services(self, hass: HomeAssistant):
        """Test that setup registers all services."""
        await async_setup_services(hass)

        # Check core services are registered
        assert hass.services.has_service(DOMAIN, SERVICE_REFRESH_DATA)
        assert hass.services.has_service(DOMAIN, SERVICE_RESTART_DEVICE)
        assert hass.services.has_service(DOMAIN, SERVICE_POWER_CYCLE_PORT)
        assert hass.services.has_service(DOMAIN, "set_hdr_mode")
        assert hass.services.has_service(DOMAIN, "set_video_mode")
        assert hass.services.has_service(DOMAIN, "set_mic_volume")
        assert hass.services.has_service(DOMAIN, "set_light_mode")
        assert hass.services.has_service(DOMAIN, "set_light_level")
        assert hass.services.has_service(DOMAIN, "ptz_move")
        assert hass.services.has_service(DOMAIN, "ptz_patrol")
        assert not hass.services.has_service(DOMAIN, "set_recording_mode")
        for action in (
            "set_chime_volume",
            "play_chime_ringtone",
            "set_chime_ringtone",
            "set_chime_repeat_times",
            "set_chime_paired_doorbells",
        ):
            assert hass.services.has_service(DOMAIN, action)

        # Clean up
        await async_unload_services(hass)


async def test_unload_without_registered_services(hass: HomeAssistant) -> None:
    """Unloading is safe when no service action is registered."""
    await async_unload_services(hass)
    assert not hass.services.async_services_for_domain(DOMAIN)


class TestAsyncUnloadServices:
    """Tests for async_unload_services."""

    async def test_unload_services_removes_services(self, hass: HomeAssistant):
        """Test that unload removes all services."""
        await async_setup_services(hass)
        assert hass.services.has_service(DOMAIN, SERVICE_REFRESH_DATA)
        assert hass.services.has_service(DOMAIN, SERVICE_RECONNECT_CLIENT)
        assert hass.services.has_service(DOMAIN, SERVICE_REMOVE_CLIENTS)

        await async_unload_services(hass)
        assert not hass.services.has_service(DOMAIN, SERVICE_REFRESH_DATA)
        assert not hass.services.has_service(DOMAIN, SERVICE_POWER_CYCLE_PORT)
        assert not hass.services.has_service(DOMAIN, SERVICE_RECONNECT_CLIENT)
        assert not hass.services.has_service(DOMAIN, SERVICE_REMOVE_CLIENTS)
        for action in (
            "set_chime_volume",
            "play_chime_ringtone",
            "set_chime_ringtone",
            "set_chime_repeat_times",
            "set_chime_paired_doorbells",
        ):
            assert not hass.services.has_service(DOMAIN, action)


@pytest.mark.parametrize(
    ("service", "field", "expected_values", "other_fields"),
    [
        ("set_hdr_mode", "mode", ["auto", "on", "off"], {}),
        (
            "set_video_mode",
            "mode",
            [
                "default",
                "highFps",
                "sport",
                "slowShutter",
                "lprReflex",
                "lprNoneReflex",
            ],
            {},
        ),
        ("set_light_mode", "mode", ["always", "motion", "off"], {}),
        ("set_light_level", "level", list(range(101)), {}),
        ("set_mic_volume", "volume", list(range(1, 101)), {}),
        ("set_chime_volume", "volume", list(range(101)), {}),
        ("ptz_move", "preset", list(range(16)), {}),
        ("ptz_patrol", "slot", list(range(16)), {"action": "start"}),
    ],
)
def test_protect_ui_selectors_match_service_schemas(
    service: str,
    field: str,
    expected_values: list[str | int],
    other_fields: dict[str, str],
) -> None:
    """Every selectable UI value must generate an accepted service payload."""
    definitions = yaml.safe_load(
        Path(services.__file__).with_suffix(".yaml").read_text(encoding="utf-8"),
    )
    fields = definitions[service]["fields"]
    selector = fields[field]["selector"]
    if "select" in selector:
        options = selector["select"]["options"]
        values = [
            value["value"] if isinstance(value, dict) else value for value in options
        ]
    else:
        number = selector["number"]
        values = list(range(number["min"], number["max"] + 1, number["step"]))
    assert values == expected_values
    if service == "set_video_mode":
        assert [value["label"] for value in options] == [
            "Normal",
            "High frame rate",
            "Fast motion",
            "Low light",
            "License plates (reflective)",
            "License plates (non-reflective)",
        ]
    schema = getattr(services, f"{service.upper()}_SCHEMA")
    for value in values:
        payload = {**other_fields, field: value}
        assert schema(payload)[field] == value
    if service == "ptz_move":
        assert fields["preset"]["required"] is True
        assert "direction" not in fields


@pytest.mark.parametrize(
    "mode", ["lprreflex", "auto", "", "lpr_reflex", "lpr_none_reflex"]
)
def test_video_mode_schema_rejects_undocumented_modes(mode: str) -> None:
    """The action must reject values outside the documented enum and aliases."""
    with pytest.raises(vol.Invalid):
        services.SET_VIDEO_MODE_SCHEMA({"mode": mode})


@pytest.mark.parametrize("mode", ["high_fps", "slow_shutter"])
def test_video_mode_schema_preserves_legacy_aliases(mode: str) -> None:
    """Existing action payload aliases must remain accepted without rewriting."""
    assert services.SET_VIDEO_MODE_SCHEMA({"mode": mode})["mode"] == mode


# The actions that act on exactly one entity, device or resource. Only the
# doorbells field of set_chime_paired_doorbells expands areas, floors and labels.
_SINGLE_TARGET_ACTIONS = {
    "restart_device": None,
    "power_cycle_port": None,
    "set_hdr_mode": "camera",
    "set_video_mode": "camera",
    "set_mic_volume": "camera",
    "ptz_move": "camera",
    "ptz_patrol": "camera",
    "set_light_mode": "light",
    "set_light_level": "light",
    "set_chime_volume": None,
    "play_chime_ringtone": None,
    "set_chime_ringtone": None,
    "set_chime_repeat_times": None,
    "set_chime_paired_doorbells": None,
    "reconnect_client": None,
    "authorize_guest": None,
    "set_liveview": None,
    "carrier_suspend_subscriber": None,
    "carrier_resume_subscriber": None,
}


@pytest.mark.usefixtures("services_ready")
async def test_single_target_actions_are_the_actions_with_a_nested_target(
    hass: HomeAssistant,
) -> None:
    """Every action whose schema takes a nested target is a single-target action."""
    registered = hass.services.async_services_for_domain(DOMAIN)
    with_target = set()
    for name, service in registered.items():
        schema = service.schema
        if schema is None:
            continue
        assert isinstance(schema, vol.Schema)
        if any(str(key) == "target" for key in schema.schema):
            with_target.add(name)
    assert with_target == set(_SINGLE_TARGET_ACTIONS)


@pytest.mark.usefixtures("services_ready")
@pytest.mark.parametrize("key", ["area_id", "floor_id", "label_id"])
@pytest.mark.parametrize("service", _SINGLE_TARGET_ACTIONS)
@pytest.mark.parametrize("with_entity", [False, True], ids=["alone", "with-entity"])
async def test_single_target_actions_refuse_area_floor_and_label_targets(
    hass: HomeAssistant, service: str, key: str, *, with_entity: bool
) -> None:
    """A nested area, floor or label target is a schema error naming the field."""
    target: dict[str, Any] = {key: "kitchen"}
    if with_entity:
        target["entity_id"] = "sensor.any"
    schema = hass.services.async_services_for_domain(DOMAIN)[service].schema
    assert schema is not None

    with pytest.raises(vol.MultipleInvalid) as err:
        schema({"target": target})
    refused = [error for error in err.value.errors if error.path == ["target", key]]
    assert len(refused) == 1
    assert key in refused[0].error_message
    assert "not supported by this action" in refused[0].error_message


@pytest.mark.usefixtures("services_ready")
@pytest.mark.parametrize("key", ["area_id", "floor_id", "label_id"])
async def test_top_level_area_floor_and_label_targets_are_refused(
    hass: HomeAssistant, key: str
) -> None:
    """An action-level area target reaches the schema as an unknown key."""
    with pytest.raises(vol.Invalid, match=key):
        await hass.services.async_call(
            DOMAIN,
            "set_hdr_mode",
            {"mode": "auto"},
            target={key: "kitchen"},
            blocking=True,
        )


@pytest.mark.usefixtures("services_ready")
async def test_nested_resource_and_entity_targets_still_validate(
    hass: HomeAssistant,
) -> None:
    """Nested entity, device and resource IDs pass the nested target schema."""
    schema = hass.services.async_services_for_domain(DOMAIN)["set_hdr_mode"].schema
    assert schema is not None
    target = {"entity_id": "camera.door", "device_id": "dev1", "camera_id": "cam1"}

    assert schema({"mode": "auto", "target": target})["target"] == target


@pytest.mark.parametrize(("service", "domain"), _SINGLE_TARGET_ACTIONS.items())
def test_single_target_actions_offer_one_entity_or_device_in_the_ui(
    service: str, domain: str | None
) -> None:
    """
    The UI offers one entity or device, never an area.

    Home Assistant's target selector always offers areas, so these actions
    have entity and device fields instead of an action-level target.
    """
    definitions = yaml.safe_load(
        Path(services.__file__).with_suffix(".yaml").read_text(encoding="utf-8")
    )
    definition = definitions[service]
    assert "target" not in definition
    fields = definition["fields"]
    entity_filter = {"integration": DOMAIN} | ({"domain": domain} if domain else {})
    assert fields["entity_id"]["selector"] == {"entity": entity_filter}
    assert fields["entity_id"]["required"] is False
    if service in {"restart_device", "power_cycle_port"}:
        # These two also take a native device ID, so the field stays text.
        assert fields["device_id"]["selector"] == {"text": None}
    else:
        assert fields["device_id"]["selector"] == {"device": {"integration": DOMAIN}}
        assert fields["device_id"]["required"] is False


def test_no_action_has_an_action_level_target() -> None:
    """No action offers Home Assistant's target selector, which includes areas."""
    definitions = yaml.safe_load(
        Path(services.__file__).with_suffix(".yaml").read_text(encoding="utf-8")
    )
    assert [
        name for name, spec in definitions.items() if "target" in (spec or {})
    ] == []


def test_new_target_fields_are_translated_in_both_files() -> None:
    """Actions with translations name their new entity and device fields."""
    loaded = [
        json.loads((Path(services.__file__).parent / path).read_text(encoding="utf-8"))[
            "services"
        ]
        for path in ("strings.json", "translations/en.json")
    ]
    for service, strings in loaded[0].items():
        if service not in _SINGLE_TARGET_ACTIONS:
            continue
        assert strings["fields"]["entity_id"]["name"] == "Entity"
        assert (
            strings["fields"]["entity_id"] == loaded[1][service]["fields"]["entity_id"]
        )
        if service not in {"restart_device", "power_cycle_port"}:
            assert strings["fields"]["device_id"]["name"] == "Device"
            assert (
                strings["fields"]["device_id"]
                == loaded[1][service]["fields"]["device_id"]
            )


class TestChimeServices:
    """Chime schemas, and the ringtone action against the real Protect owner."""

    def test_ringtone_schema_accepts_trimmed_opaque_id(
        self,
    ) -> None:
        """IDs need not match an invented enum or a fixed hex pattern."""
        validated = cast(
            "dict[str, Any]",
            services.SET_CHIME_RINGTONE_SCHEMA({"ringtone_id": "  opaque-tone-id  "}),
        )
        assert validated["ringtone_id"] == "opaque-tone-id"

    @pytest.mark.parametrize("ringtone_id", ["", " ", None, 12, False])
    def test_ringtone_schema_rejects_invalid_id(self, ringtone_id: Any) -> None:
        """Blank and non-string IDs fail schema validation."""
        with pytest.raises(vol.Invalid):
            services.SET_CHIME_RINGTONE_SCHEMA({"ringtone_id": ringtone_id})

    @pytest.mark.parametrize("ringtone_id", ["", " ", "tone-new", "default"])
    def test_playback_compatibility_field_is_a_plain_string(self, ringtone_id):
        """Compatibility values are ignored and are not ringtone selections."""
        assert services.PLAY_CHIME_RINGTONE_SCHEMA({"ringtone_id": ringtone_id}) == {
            "ringtone_id": ringtone_id
        }

    @pytest.mark.parametrize("ringtone_id", [None, 12, False])
    def test_playback_compatibility_field_rejects_non_strings(self, ringtone_id):
        """The optional compatibility field retains plain-string validation."""
        with pytest.raises(vol.Invalid):
            services.PLAY_CHIME_RINGTONE_SCHEMA({"ringtone_id": ringtone_id})

    @pytest.mark.parametrize("service", ["set_chime_ringtone", "play_chime_ringtone"])
    def test_ringtone_ui_uses_text_selector(self, service: str) -> None:
        """Action UI accepts actual IDs and does not invent ringtone options."""
        definitions = yaml.safe_load(
            Path(services.__file__).with_suffix(".yaml").read_text(encoding="utf-8")
        )
        field = definitions[service]["fields"]["ringtone_id"]
        assert field["selector"] == {"text": None}
        schema = getattr(services, f"{service.upper()}_SCHEMA")
        assert (
            schema({"ringtone_id": field["example"]})["ringtone_id"]
            == (field["example"])
        )

    @pytest.mark.parametrize(
        "settings",
        [
            None,
            [],
            [{}],
            [
                {
                    "cameraId": "doorbell-a",
                    "repeatTimes": 2,
                    "ringtoneId": "tone-old",
                    "volume": 25,
                },
                {
                    "cameraId": "doorbell-b",
                    "repeatTimes": 4,
                    "ringtoneId": "tone-other",
                    "volume": 75,
                },
            ],
        ],
    )
    async def test_set_ringtone_service_reaches_real_facade_and_client(
        self, hass: HomeAssistant, settings: Any
    ) -> None:
        """The service uses the spec body or exposes malformed settings safely."""
        client = UniFiProtectClient(
            auth=ApiKeyAuth(api_key="test-key"),
            base_url="https://192.168.1.1",
            connection_type=ConnectionType.LOCAL,
        )
        chime = {"id": "chime1", "mac": "AA:BB:CC:DD:EE:FF", "ringSettings": settings}
        client._get = AsyncMock(return_value=chime)
        client._patch = AsyncMock(return_value=chime)
        coordinator = MagicMock(protect_client=client)
        coordinator.data = {"protect": {"chimes": {"chime1": chime}}}
        owner = _real_protect_owner(hass, client)
        owner.async_request_refresh = AsyncMock()  # type: ignore[method-assign]
        coordinator.require_protect_coordinator.return_value = owner
        entry = MagicMock(
            entry_id="entry-chime", runtime_data=MagicMock(coordinator=coordinator)
        )
        await async_setup_services(hass)
        with patch.object(hass.config_entries, "async_entries", return_value=[entry]):
            if settings and settings[0]:
                await hass.services.async_call(
                    DOMAIN,
                    SERVICE_SET_CHIME_RINGTONE,
                    {"chime_id": "chime1", "ringtone_id": "  tone-new  "},
                    blocking=True,
                )
                client._patch.assert_awaited_once_with(
                    client.build_api_path("/chimes/chime1"),
                    json_data={
                        "ringSettings": [
                            {**setting, "ringtoneId": "tone-new"}
                            for setting in settings
                        ]
                    },
                )
                owner.async_request_refresh.assert_awaited_once()
            else:
                with pytest.raises(HomeAssistantError, match="Unable to set ringtone"):
                    await hass.services.async_call(
                        DOMAIN,
                        SERVICE_SET_CHIME_RINGTONE,
                        {"chime_id": "chime1", "ringtone_id": "tone-new"},
                        blocking=True,
                    )
                client._patch.assert_not_awaited()
                owner.async_request_refresh.assert_not_awaited()
        await async_unload_services(hass)


class TestLightServices:
    """The light level action against the real Protect owner and client."""

    @pytest.mark.parametrize(
        ("level", "led_level"),
        [(0, 1), (50, 3), (100, 6)],
    )
    async def test_set_light_level_sends_spec_patch_body(
        self, hass: HomeAssistant, level: int, led_level: int
    ):
        """Test set_light_level reaches PATCH /lights/{id} as a 1-6 ledLevel."""
        client = UniFiProtectClient(
            auth=ApiKeyAuth(api_key="test-key"),
            base_url="https://192.168.1.1",
            connection_type=ConnectionType.LOCAL,
        )
        client._patch = AsyncMock(return_value={"id": "light1", "mac": "00:11:22"})
        mock_coordinator = MagicMock()
        mock_coordinator.protect_client = client
        # The real Protect owner, so the whole path down to the client runs
        mock_coordinator.require_protect_coordinator.return_value = _real_protect_owner(
            hass, client
        )
        mock_entry = MagicMock()
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = mock_coordinator

        await async_setup_services(hass)

        with patch.object(
            hass.config_entries,
            "async_entries",
            return_value=[mock_entry],
        ):
            await hass.services.async_call(
                DOMAIN,
                "set_light_level",
                {"light_id": "light1", "level": level},
                blocking=True,
            )

        client._patch.assert_awaited_once_with(
            client.build_api_path("/lights/light1"),
            json_data={"lightDeviceSettings": {"ledLevel": led_level}},
        )

        await async_unload_services(hass)


@pytest.mark.parametrize(
    ("service", "entity_domain", "unique_id", "data", "method"),
    [
        (
            "restart_device",
            "sensor",
            "site_device_uptime",
            {},
            "device_coordinator.async_restart_device",
        ),
        (
            "set_hdr_mode",
            "camera",
            "unifi_insights_camera_cam",
            {"mode": "auto"},
            "require_protect_coordinator.return_value.async_set_hdr_mode",
        ),
        (
            "authorize_guest",
            "device_tracker",
            "unifi_insights_aa:bb:cc:dd:ee:ff",
            {"site_id": "site"},
            "config_coordinator.async_authorize_guest",
        ),
    ],
)
@pytest.mark.parametrize("target_kind", ["entity", "device"])
async def test_unloaded_target_console_does_not_fall_back(
    hass: HomeAssistant,
    *,
    service: str,
    entity_domain: str,
    unique_id: str,
    data: dict[str, str],
    method: str,
    target_kind: str,
) -> None:
    """A registry target must never be sent to a different loaded console."""
    owner = MockConfigEntry(domain=DOMAIN)
    owner.add_to_hass(hass)
    other = MockConfigEntry(domain=DOMAIN)
    other.add_to_hass(hass)
    coordinator = autospec_command_owners(MagicMock(data={}))
    action = AsyncMock()
    *owner_path, name = method.split(".")
    owner_obj = coordinator
    for part in owner_path:
        owner_obj = getattr(owner_obj, part)
    setattr(owner_obj, name, action)
    other.runtime_data = MagicMock(coordinator=coordinator)
    entity = er.async_get(hass).async_get_or_create(
        entity_domain,
        DOMAIN,
        unique_id,
        config_entry=owner,
    )
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id,
        identifiers={
            (
                DOMAIN,
                "protect_camera_cam"
                if entity_domain == "camera"
                else "client_aa:bb:cc:dd:ee:ff"
                if entity_domain == "device_tracker"
                else "site_device",
            )
        },
    )
    target = (
        {"entity_id": entity.entity_id}
        if target_kind == "entity"
        else {
            "device_id": device.id,
        }
    )
    await async_setup_services(hass)
    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(
            DOMAIN,
            service,
            {**data, **target},
            blocking=True,
        )
    assert raised.value.translation_key == "target_console_not_loaded"
    action.assert_not_awaited()


@pytest.mark.parametrize("target_kind", ["dotted_mac", "entity", "device"])
async def test_authorize_guest_accepts_dotted_mac(
    hass: HomeAssistant,
    target_kind: str,
) -> None:
    """Dotted MAC addresses remain valid raw client targets."""

    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    coordinator = MagicMock(
        data={
            "sites": {"site": {}},
            "clients": {
                "site": {"native-client-id": {"macAddress": "aa:bb:cc:dd:ee:ff"}}
            },
        }
    )
    coordinator.config_coordinator.async_authorize_guest = AsyncMock()
    entry.runtime_data = MagicMock(coordinator=coordinator)
    entity = er.async_get(hass).async_get_or_create(
        "device_tracker",
        DOMAIN,
        "unifi_insights_aa:bb:cc:dd:ee:ff",
        config_entry=entry,
    )
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "client_aa:bb:cc:dd:ee:ff")},
    )
    target = {
        "dotted_mac": {"client_id": "aabb.ccdd.eeff"},
        "entity": {"entity_id": entity.entity_id},
        "device": {"device_id": device.id},
    }[target_kind]
    await async_setup_services(hass)
    await hass.services.async_call(
        DOMAIN,
        "authorize_guest",
        {"site_id": "site", **target},
        blocking=True,
    )
    coordinator.config_coordinator.async_authorize_guest.assert_awaited_once_with(
        "site",
        "native-client-id",
    )


async def test_refresh_data_failure_is_translated(hass: HomeAssistant) -> None:
    """A failed refresh_data names the failed consoles in refresh_failed."""
    broken = MagicMock()
    broken.data = {"sites": {"site1": {}}}
    broken.async_refresh_or_raise = AsyncMock(
        side_effect=HomeAssistantError("Error refreshing UniFi Insights data: x")
    )
    broken_entry = MagicMock()
    broken_entry.title = "Upstairs console"
    broken_entry.runtime_data = MagicMock()
    broken_entry.runtime_data.coordinator = broken

    await async_setup_services(hass)
    with (
        patch.object(hass.config_entries, "async_entries", return_value=[broken_entry]),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await hass.services.async_call(DOMAIN, SERVICE_REFRESH_DATA, {}, blocking=True)

    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "refresh_failed"
    assert raised.value.translation_placeholders == {"targets": "Upstairs console"}
    await async_unload_services(hass)


def test_is_wireless_client_helper():
    """Test all branches of _is_wireless_client helper."""
    assert _is_wireless_client({"type": ClientType.WIRELESS}) is True
    assert _is_wireless_client({"type": ClientType.WIRED}) is False
    assert _is_wireless_client({"type": "WIRELESS"}) is True
    assert _is_wireless_client({"type": "CLIENTTYPE.WIRELESS"}) is True
    assert _is_wireless_client({"type": " wireless "}) is True
    assert _is_wireless_client({"type": "UNKNOWN_WIRELESS"}) is False
    assert _is_wireless_client({"type": "WIRED"}) is False
    assert _is_wireless_client({"connection_type": "WIRELESS"}) is True
    assert _is_wireless_client({"connection_type": "WIRED"}) is False
    assert _is_wireless_client({"type": 123}) is False
    assert _is_wireless_client({}) is False


class TestServiceCoordinatorContract:
    """Guard the service layer against calling methods a coordinator lacks."""

    # Each command field of the service_targets results, and the owner class
    # it holds (ADR-010). ``cache`` is only read, never called.
    _FIELD_CLASSES: ClassVar[dict[str, type]] = {
        "facade_commands": UnifiInsightsFacadeCoordinator,
        "export_commands": UnifiInsightsFacadeCoordinator,
        "device_commands": UnifiInsightsDeviceCoordinator,
        "config_commands": UnifiInsightsConfigCoordinator,
        "protect_commands": UnifiInsightsProtectCoordinator,
        "access_commands": UnifiInsightsAccessCoordinator,
        "carrier_commands": UnifiInsightsCarrierFabricCoordinator,
    }

    @staticmethod
    def _services_tree() -> ast.Module:
        services_py = (
            Path(__file__).parent.parent
            / "custom_components"
            / "unifi_insights"
            / "services.py"
        )
        return ast.parse(services_py.read_text(encoding="utf-8"))

    def test_every_called_result_field_has_an_owner_class(self):
        """A call on ``<result>.<field>.<method>()`` must use a mapped field."""
        unmapped = sorted(
            {
                node.func.value.attr
                for node in ast.walk(self._services_tree())
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr.endswith("_commands")
                and node.func.value.attr not in self._FIELD_CLASSES
            }
        )
        assert not unmapped, f"result fields without an owner class: {unmapped}"

    @pytest.mark.parametrize(
        ("variable", "coordinator_class"),
        list(_FIELD_CLASSES.items()),
    )
    def test_every_coordinator_method_called_by_services_exists(
        self, variable, coordinator_class
    ):
        """Every ``<result>.<field>.<method>()`` in services.py must exist.

        Each command field of a service_targets result holds one owner class
        (ADR-010): the console facade, the device, config or Protect owner, the
        UniFi Access coordinator or the Carrier Fabric coordinator.

        The service tests drive MagicMock coordinators, which happily accept any
        attribute name. That is how ``async_set_camera_chime_volume`` - a method
        no coordinator ever defined - shipped green. This test reads the real
        source instead of a mock.
        """
        tree = self._services_tree()

        calls: list[tuple[str, int, list[str], bool]] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == variable
            ):
                starred = any(isinstance(a, ast.Starred) for a in node.args) or any(
                    kw.arg is None for kw in node.keywords
                )
                calls.append(
                    (
                        node.func.attr,
                        len(node.args),
                        [kw.arg for kw in node.keywords if kw.arg],
                        starred,
                    )
                )

        assert calls, f"no {variable}.<method>() calls found - parser broke"

        missing = sorted(
            {name for name, _, _, _ in calls if not hasattr(coordinator_class, name)}
        )
        assert not missing, (
            f"services.py calls {variable} methods that do not exist on "
            f"{coordinator_class.__name__}: {missing}"
        )

        # hasattr alone would still accept a 3-arg call to a 2-arg method, which
        # is exactly how the per-camera chime bug could come back. Bind instead.
        bad_arity: list[str] = []
        for name, positional, keywords, starred in calls:
            if starred:
                continue
            signature = inspect.signature(getattr(coordinator_class, name))
            try:
                signature.bind(
                    object(),  # self
                    *[object()] * positional,
                    **dict.fromkeys(keywords, object()),
                )
            except TypeError as err:
                bad_arity.append(f"{name}: {err}")
        assert not bad_arity, (
            f"services.py calls {variable} methods with arguments they do not "
            f"accept: {bad_arity}"
        )


class _Response:
    """Minimal aiohttp response replacement for transport tests."""

    def __init__(
        self,
        body: Any = None,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}
        self.url = URL("https://192.168.1.1")
        self.method = "GET"
        self.history = ()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def text(self) -> str:
        if isinstance(self._body, (dict, list)):
            return json.dumps(self._body)
        if self._body is None:
            return ""
        return str(self._body)

    async def json(self) -> Any:
        if self._body is None or isinstance(self._body, str):
            msg = "No JSON body"
            raise ValueError(msg)
        return self._body


class _Session:
    """Mock session recording requests and returning queued responses."""

    closed = False

    def __init__(
        self, responses: list[_Response | dict[str, Any] | list[Any] | None]
    ) -> None:
        self._responses = iter(
            [r if isinstance(r, _Response) else _Response(r) for r in responses]
        )
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: object, **kwargs: Any) -> _Response:
        self.requests.append({"method": method, "url": str(url), **kwargs})
        return next(self._responses)

    async def close(self) -> None:
        self.closed = True


class TestRemoveClientsService:
    """remove_clients: the filter, admin and schema checks, and the HTTP path."""

    def test_filter_clients_for_removal_matrix(self):
        """Test pure selection helper _filter_clients_for_removal against core rules."""
        records = [
            # Eligible client: short-lived, unnamed, no fixed IP
            {"mac": "AA:BB:CC:DD:EE:01", "first_seen": 100, "last_seen": 200},
            # Eligible: exactly 900 seconds
            {"mac": "AA:BB:CC:DD:EE:02", "first_seen": 100, "last_seen": 1000},
            # Excluded: > 900 seconds (901s)
            {"mac": "AA:BB:CC:DD:EE:03", "first_seen": 100, "last_seen": 1001},
            # Eligible: missing first_seen
            {"mac": "AA:BB:CC:DD:EE:04", "first_seen": None, "last_seen": 500},
            # Eligible: missing last_seen
            {"mac": "AA:BB:CC:DD:EE:05", "first_seen": 100, "last_seen": None},
            # Excluded: has name
            {
                "mac": "AA:BB:CC:DD:EE:06",
                "name": "Alice iPhone",
                "first_seen": 100,
                "last_seen": 200,
            },
            # Excluded: has hostname
            {
                "mac": "AA:BB:CC:DD:EE:07",
                "hostname": "alices-phone",
                "first_seen": 100,
                "last_seen": 200,
            },
            # Excluded: has fixed_ip
            {
                "mac": "AA:BB:CC:DD:EE:08",
                "fixed_ip": "192.168.1.50",
                "first_seen": 100,
                "last_seen": 200,
            },
            # Excluded: missing MAC
            {"first_seen": 100, "last_seen": 200},
            # Excluded: empty string MAC
            {"mac": "   ", "first_seen": 100, "last_seen": 200},
            # Duplicate of EE:01 with different casing
            {"mac": "aa:bb:cc:dd:ee:01", "first_seen": 100, "last_seen": 200},
            # Ineligible: invalid timestamp (malformed data kept)
            {"mac": "AA:BB:CC:DD:EE:09", "first_seen": "invalid", "last_seen": 200},
            # Ineligible: boolean timestamp (not a real number, kept)
            {"mac": "AA:BB:CC:DD:EE:0A", "first_seen": True, "last_seen": 200},
            # Ineligible: boolean last_seen (not a real number, kept)
            {"mac": "AA:BB:CC:DD:EE:0B", "first_seen": 100, "last_seen": False},
            # Ineligible: invalid first_seen with no last_seen (kept)
            {"mac": "AA:BB:CC:DD:EE:0C", "first_seen": "invalid", "last_seen": None},
            # Ineligible: falsy non-number first_seen (kept)
            {"mac": "AA:BB:CC:DD:EE:0D", "first_seen": [], "last_seen": 200},
            # Eligible: numeric zero first_seen skips the age check, as in core
            {"mac": "AA:BB:CC:DD:EE:0E", "first_seen": 0, "last_seen": 5000},
            # Excluded: unhashable name is still a name
            {
                "mac": "AA:BB:CC:DD:EE:0F",
                "name": {"value": "protected"},
                "first_seen": 100,
                "last_seen": 200,
            },
            # Ineligible: NaN and infinite timestamps (kept)
            {"mac": "AA:BB:CC:DD:EE:10", "first_seen": float("nan"), "last_seen": 200},
            {"mac": "AA:BB:CC:DD:EE:11", "first_seen": 100, "last_seen": float("inf")},
            # Ineligible: int too large to compare with a float time (kept)
            {"mac": "AA:BB:CC:DD:EE:12", "first_seen": 10**309, "last_seen": 200.0},
        ]

        result = _filter_clients_for_removal(records)
        assert result == [
            "aa:bb:cc:dd:ee:01",
            "aa:bb:cc:dd:ee:02",
            "aa:bb:cc:dd:ee:04",
            "aa:bb:cc:dd:ee:05",
            "aa:bb:cc:dd:ee:0e",
        ]

    async def test_remove_clients_unreadable_timestamp_keeps_client_and_continues(
        self, hass: HomeAssistant
    ):
        """Test an unreadable timestamp keeps the client and the next site runs."""
        coordinator = MagicMock()
        coordinator.is_network_usable = True
        coordinator.data = {"sites": {"site1": {}, "site2": {}}}
        coordinator.async_get_historical_clients = AsyncMock(
            side_effect=[
                [
                    {
                        "mac": "11:11:11:11:11:11",
                        "first_seen": 10**309,
                        "last_seen": 200.0,
                    }
                ],
                [{"mac": "22:22:22:22:22:22", "first_seen": 100, "last_seen": 200}],
            ]
        )
        coordinator.device_coordinator.async_forget_clients_batch = AsyncMock(
            return_value=True
        )
        entry = MagicMock(
            entry_id="entry1",
            title="Console 1",
            runtime_data=MagicMock(coordinator=coordinator),
        )

        await async_setup_services(hass)
        with patch.object(hass.config_entries, "async_entries", return_value=[entry]):
            await hass.services.async_call(
                DOMAIN, SERVICE_REMOVE_CLIENTS, {}, blocking=True
            )
        await async_unload_services(hass)

        coordinator.device_coordinator.async_forget_clients_batch.assert_awaited_once_with(
            "site2", ["22:22:22:22:22:22"]
        )

    async def test_remove_clients_non_admin_raises_unauthorized(
        self, hass: HomeAssistant
    ):
        """Test non-admin user call to remove_clients raises Unauthorized."""
        await async_setup_services(hass)
        user = await hass.auth.async_create_user("non_admin_user")
        user.is_owner = False
        user.groups = []  # Non-admin
        user.invalidate_cache()
        context = Context(user_id=user.id)

        with pytest.raises(Unauthorized):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REMOVE_CLIENTS,
                {},
                context=context,
                blocking=True,
            )
        await async_unload_services(hass)

    async def test_remove_clients_rejects_extra_fields(self, hass: HomeAssistant):
        """Test remove_clients rejects unexpected arguments via schema."""
        await async_setup_services(hass)
        with pytest.raises(vol.MultipleInvalid):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REMOVE_CLIENTS,
                {"unexpected_param": "value"},
                blocking=True,
            )
        await async_unload_services(hass)

    async def test_remove_clients_classic_error_envelope_failure(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ):
        """Test classic error response reports failure and forgets nothing."""
        for error_type in ["error", "failed", "missing_meta"]:
            coord = MagicMock()
            coord.is_network_usable = True
            coord.data = {"sites": {"site1": {}}}
            coord.async_get_historical_clients = AsyncMock(
                side_effect=HomeAssistantError(f"Classic API call failed: {error_type}")
            )
            coord.device_coordinator.async_forget_clients_batch = AsyncMock()
            entry = MagicMock(
                entry_id="entry1",
                title="Console 1",
                runtime_data=MagicMock(coordinator=coord),
            )

            await async_setup_services(hass)
            with (
                patch.object(
                    hass.config_entries, "async_entries", return_value=[entry]
                ),
                pytest.raises(HomeAssistantError) as exc_info,
            ):
                await hass.services.async_call(
                    DOMAIN,
                    SERVICE_REMOVE_CLIENTS,
                    {},
                    blocking=True,
                )

            assert exc_info.value.translation_key == "remove_clients_failed"
            assert f"Classic API call failed: {error_type}" not in str(exc_info.value)
            assert f"Classic API call failed: {error_type}" in caplog.text
            coord.device_coordinator.async_forget_clients_batch.assert_not_called()
            await async_unload_services(hass)

    async def test_remove_clients_end_to_end_http_boundary(self, hass: HomeAssistant):
        """End-to-end service test mocking only the HTTP boundary."""
        session = _Session(
            [
                # 1. GET /api/s/alpha/stat/alluser
                {
                    "meta": {"rc": "ok"},
                    "data": [
                        # Candidate 1: anonymous seen for 300s
                        {
                            "mac": "11:22:33:44:55:01",
                            "first_seen": 100,
                            "last_seen": 400,
                        },
                        # Candidate 2: missing first_seen
                        {
                            "mac": "11:22:33:44:55:02",
                            "first_seen": None,
                            "last_seen": 400,
                        },
                        # Excluded: named client
                        {
                            "mac": "11:22:33:44:55:03",
                            "name": "Alice iPhone",
                            "first_seen": 100,
                            "last_seen": 400,
                        },
                        # Excluded: client with hostname
                        {
                            "mac": "11:22:33:44:55:04",
                            "hostname": "printer",
                            "first_seen": 100,
                            "last_seen": 400,
                        },
                        # Excluded: client with fixed IP
                        {
                            "mac": "11:22:33:44:55:05",
                            "fixed_ip": "10.0.0.1",
                            "first_seen": 100,
                            "last_seen": 400,
                        },
                        # Excluded: client seen for more than 900s
                        {
                            "mac": "11:22:33:44:55:06",
                            "first_seen": 100,
                            "last_seen": 1200,
                        },
                        # Excluded: malformed timestamp kept
                        {
                            "mac": "11:22:33:44:55:07",
                            "first_seen": "invalid",
                            "last_seen": 400,
                        },
                    ],
                },
                # 2. POST /api/s/alpha/cmd/stamgr
                {"meta": {"rc": "ok"}},
            ]
        )

        network_client = UniFiNetworkClient(
            auth=ApiKeyAuth(api_key="test-key"),
            base_url="https://192.168.1.1",
            connection_type=ConnectionType.LOCAL,
            session=session,  # type: ignore[arg-type]
        )

        mock_config_coordinator = MagicMock()
        mock_config_coordinator.network_available = True
        mock_config_coordinator.data = {
            "sites": {
                "site1": {
                    "id": "site1",
                    "name": "Office",
                    "internalReference": "alpha",
                }
            },
            # The published fuzzy mapping points elsewhere and must be ignored.
            "legacy_site_names": {"site1": "beta"},
        }
        mock_config_coordinator.get_site = mock_config_coordinator.data["sites"].get
        mock_config_coordinator.resolve_legacy_site_name = (
            UnifiInsightsConfigCoordinator.resolve_legacy_site_name.__get__(
                mock_config_coordinator
            )
        )

        mock_device_coordinator = MagicMock()
        mock_device_coordinator.data = {}
        # The real batch-forget owner command, so the request stays end to end.
        mock_device_coordinator.network_client = network_client
        mock_device_coordinator.config_coordinator = mock_config_coordinator
        mock_device_coordinator.async_forget_clients_batch = (
            UnifiInsightsDeviceCoordinator.async_forget_clients_batch.__get__(
                mock_device_coordinator
            )
        )

        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.title = "UDM Pro"
        entry.data = {}

        facade = UnifiInsightsFacadeCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=None,
            entry=entry,
            config_coordinator=mock_config_coordinator,
            device_coordinator=mock_device_coordinator,
            protect_coordinator=None,
        )

        entry.runtime_data = MagicMock(coordinator=facade)

        await async_setup_services(hass)
        with patch.object(hass.config_entries, "async_entries", return_value=[entry]):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REMOVE_CLIENTS,
                {},
                blocking=True,
            )

        assert len(session.requests) == 2
        req1 = session.requests[0]
        assert req1["method"] == "GET"
        assert (
            req1["url"] == "https://192.168.1.1/proxy/network/api/s/alpha/stat/alluser"
        )

        req2 = session.requests[1]
        assert req2["method"] == "POST"
        assert req2["url"] == "https://192.168.1.1/proxy/network/api/s/alpha/cmd/stamgr"
        assert req2["json"] == {
            "cmd": "forget-sta",
            "macs": ["11:22:33:44:55:01", "11:22:33:44:55:02"],
        }
        await async_unload_services(hass)
