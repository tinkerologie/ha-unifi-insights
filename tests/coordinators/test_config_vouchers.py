# Copyright 2026 UniFi Insights contributors
"""Tests for hotspot vouchers: config coordinator state, commands and flows."""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_API_KEY
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from unifi_official_api import (
    ApiKeyAuth,
    ConnectionType,
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiNotFoundError,
    UniFiRateLimitError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from unifi_official_api.network import UniFiNetworkClient
from unifi_official_api.network.endpoints.vouchers import VouchersEndpoint
from unifi_official_api.network.models.voucher import Voucher
from unifi_official_api.validation import parse_record

from custom_components.unifi_insights.const import (
    CONF_SITE_IDS,
    DOMAIN,
)
from custom_components.unifi_insights.coordinators.config import (
    UnifiInsightsConfigCoordinator,
    refreshed_latest_vouchers,
)
from custom_components.unifi_insights.coordinators.device import (
    UnifiInsightsDeviceCoordinator,
)
from custom_components.unifi_insights.coordinators.facade import (
    UnifiInsightsFacadeCoordinator,
)
from custom_components.unifi_insights.data_transforms import (
    VoucherSettings,
    count_active_vouchers,
)
from custom_components.unifi_insights.image import UnifiVoucherQrCodeImage
from tests.fixtures.assertions import patch_attribute, require_mock, require_not_none
from tests.fixtures.command_owners import SITE_ID
from tests.fixtures.coordinator_clients import (
    _create_mock_model,
    _create_mock_network_client,
    _create_mock_protect_client,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from homeassistant.core import HomeAssistant

    from tests.fixtures.command_owners import SeededOwners


@pytest.mark.asyncio
async def test_generate_flow_updates_inventory_and_latest_voucher(
    hass: HomeAssistant,
    freezer: Any,
) -> None:
    """Generating a voucher updates inventory, latest_vouchers and fires listeners."""
    # Inside the fixture voucher's 12:00-20:00 window, so it counts as active.
    freezer.move_to("2026-10-09T13:00:00Z")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "test_api_key"},
        options={},
    )
    network_client = MagicMock()
    network_client.base_url = "https://192.168.1.1"
    network_client.sites = MagicMock()
    network_client.sites.get_all = AsyncMock(
        return_value=[_create_mock_model({"id": "site1", "name": "Default"})]
    )
    network_client.sites.get_legacy_all = AsyncMock(return_value=[])
    network_client.wifi = MagicMock()
    network_client.wifi.get_all = AsyncMock(return_value=[])
    network_client.firewall = MagicMock()
    network_client.firewall.list_rules = AsyncMock(return_value=[])
    network_client.devices = MagicMock()
    network_client.devices.get_all = AsyncMock(return_value=[])
    network_client.clients = MagicMock()
    network_client.clients.get_all = AsyncMock(return_value=[])
    network_client.reports = MagicMock()
    network_client.reports.get_site_report = AsyncMock(return_value=[])

    # Initial state: no vouchers
    created_voucher = Voucher.model_validate(
        {
            "id": "v-1",
            "code": "1234567890",
            "name": "Home Assistant",
            "time_limit_minutes": 480,
        }
    )
    updated_voucher_model = _create_mock_model(
        {
            "id": "v-1",
            "code": "1234567890",
            "name": "Home Assistant",
            "timeLimitMinutes": 480,
            "activatedAt": "2026-10-09T12:00:00Z",
            "expiresAt": "2026-10-09T20:00:00Z",
            "expired": False,
            "authorizedGuestCount": 1,
            "authorizedGuestLimit": 2,
        }
    )

    network_client.vouchers = MagicMock()
    network_client.vouchers.create = AsyncMock(return_value=[created_voucher])
    network_client.vouchers.get_all_pages = AsyncMock(
        side_effect=[
            [],  # initial poll
            [updated_voucher_model],  # after create
        ]
    )

    config_coord = UnifiInsightsConfigCoordinator(
        hass=hass,
        network_client=network_client,
        protect_client=None,
        entry=entry,
    )
    device_coord = UnifiInsightsDeviceCoordinator(
        hass=hass,
        network_client=network_client,
        protect_client=None,
        entry=entry,
        config_coordinator=config_coord,
    )
    facade = UnifiInsightsFacadeCoordinator(
        hass=hass,
        network_client=network_client,
        protect_client=None,
        entry=entry,
        config_coordinator=config_coord,
        device_coordinator=device_coord,
        protect_coordinator=None,
    )

    # Initial refresh
    await config_coord.async_refresh()
    await device_coord.async_refresh()
    facade._aggregate_data()

    assert count_active_vouchers(facade.data["vouchers"].get("site1", {})) == 0
    assert facade.data["latest_vouchers"] == {}

    listener = MagicMock()
    facade.async_add_listener(listener)

    # Generate voucher
    result = await config_coord.async_generate_voucher(
        "site1",
        name="Home Assistant",
        time_limit_minutes=480,
    )
    assert result == [created_voucher]

    # Listener was called
    listener.assert_called()

    # Inventory and latest vouchers are updated
    inventory = facade.data["vouchers"]["site1"]
    assert "v-1" in inventory
    assert inventory["v-1"]["code"] == "1234567890"
    assert inventory["v-1"]["activatedAt"] == "2026-10-09T12:00:00Z"

    latest = facade.data["latest_vouchers"]["site1"]
    assert latest["id"] == "v-1"
    assert latest["code"] == "1234567890"
    assert latest["activatedAt"] == "2026-10-09T12:00:00Z"

    # Active count is 1
    assert count_active_vouchers(inventory) == 1
    await facade.async_shutdown()


@pytest.mark.asyncio
async def test_slower_full_poll_does_not_regress_newer_targeted_refresh_state(
    hass: HomeAssistant,
) -> None:
    """A slower full config poll must not regress newer targeted voucher state."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "test_api_key"},
        options={"site_ids": ["site1"]},
    )
    network_client = MagicMock()
    network_client.base_url = "https://192.168.1.1"
    network_client.sites = MagicMock()
    network_client.sites.get_all = AsyncMock(
        return_value=[_create_mock_model({"id": "site1", "name": "Default"})]
    )
    network_client.sites.get_legacy_all = AsyncMock(return_value=[])
    network_client.wifi = MagicMock()
    network_client.wifi.get_all = AsyncMock(return_value=[])
    network_client.firewall = MagicMock()
    network_client.firewall.list_rules = AsyncMock(return_value=[])
    network_client.devices = MagicMock()
    network_client.devices.get_all = AsyncMock(return_value=[])
    network_client.clients = MagicMock()
    network_client.clients.get_all = AsyncMock(return_value=[])
    network_client.reports = MagicMock()
    network_client.reports.get_site_report = AsyncMock(return_value=[])

    older_snapshot = _create_mock_model(
        {
            "id": "v-1",
            "code": "1234567890",
            "name": "Home Assistant",
            "timeLimitMinutes": 480,
            "expired": False,
        }
    )
    newer_snapshot = _create_mock_model(
        {
            "id": "v-1",
            "code": "1234567890",
            "name": "Home Assistant",
            "timeLimitMinutes": 480,
            "expired": True,
            "activatedAt": "2026-10-09T12:00:00Z",
            "expiresAt": "2026-10-09T20:00:00Z",
        }
    )

    full_poll_started = asyncio.Event()
    targeted_done = asyncio.Event()

    call_count = 0

    async def mock_get_all_pages(site_id: str):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call is the initial setup
            return [older_snapshot]
        if call_count == 2:
            # Second call is the full poll
            full_poll_started.set()
            await targeted_done.wait()
            return [older_snapshot]
        # Third call is the targeted refresh
        return [newer_snapshot]

    network_client.vouchers = MagicMock()
    network_client.vouchers.get_all_pages = AsyncMock(side_effect=mock_get_all_pages)

    config_coord = UnifiInsightsConfigCoordinator(
        hass=hass,
        network_client=network_client,
        protect_client=None,
        entry=entry,
    )
    device_coord = UnifiInsightsDeviceCoordinator(
        hass=hass,
        network_client=network_client,
        protect_client=None,
        entry=entry,
        config_coordinator=config_coord,
    )
    facade = UnifiInsightsFacadeCoordinator(
        hass=hass,
        network_client=network_client,
        protect_client=None,
        entry=entry,
        config_coordinator=config_coord,
        device_coordinator=device_coord,
        protect_coordinator=None,
    )

    # Initial setup
    await config_coord.async_refresh()
    config_coord.data = {
        **config_coord.data,
        "latest_vouchers": {
            "site1": {"id": "v-1", "code": "1234567890", "expired": False}
        },
    }
    facade._aggregate_data()
    assert facade.data["latest_vouchers"]["site1"]["expired"] is False

    # Start slower full poll in background
    full_poll_task = asyncio.create_task(config_coord.async_refresh())
    await full_poll_started.wait()

    # Perform newer targeted refresh while full poll is outstanding
    await config_coord.async_refresh_vouchers("site1")
    facade._aggregate_data()
    assert config_coord.data["vouchers"]["site1"]["v-1"]["expired"] is True
    assert facade.data["latest_vouchers"]["site1"]["expired"] is True

    # Release older full poll
    targeted_done.set()
    await full_poll_task
    facade._aggregate_data()

    # State must NOT regress back to expired: False
    assert config_coord.data["vouchers"]["site1"]["v-1"]["expired"] is True
    assert facade.data["latest_vouchers"]["site1"]["expired"] is True
    await facade.async_shutdown()


@pytest.fixture
async def voucher_flow(
    hass: HomeAssistant,
) -> AsyncGenerator[
    tuple[UnifiInsightsConfigCoordinator, UnifiInsightsFacadeCoordinator]
]:
    """Build real coordinators with unrelated API sections mocked locally."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "test-key"})
    client = MagicMock()
    client.base_url = "https://192.168.1.1"
    client.sites.get_all = AsyncMock(
        return_value=[_create_mock_model({"id": "site1", "name": "Default"})]
    )
    for endpoint, method in (
        ("sites", "get_legacy_all"),
        ("wifi", "get_all"),
        ("firewall", "list_rules"),
        ("devices", "get_all"),
        ("clients", "get_all"),
        ("reports", "get_site_report"),
        ("vouchers", "get_all_pages"),
    ):
        setattr(getattr(client, endpoint), method, AsyncMock(return_value=[]))
    config = UnifiInsightsConfigCoordinator(hass, client, None, entry)
    device = UnifiInsightsDeviceCoordinator(hass, client, None, entry, config)
    facade = UnifiInsightsFacadeCoordinator(
        hass,
        client,
        None,
        entry,
        config,
        device_coordinator=device,
        protect_coordinator=None,
    )
    try:
        yield config, facade
    finally:
        await facade.async_shutdown()


def _voucher_transport(responses: list[Any]) -> UniFiNetworkClient:
    """Use the real `unifi-official-api` transport over queued mocked HTTP responses."""
    session = MagicMock(closed=False)
    contexts = []
    for body in responses:
        response = MagicMock(status=200, headers={}, history=())
        response.text = AsyncMock(return_value=json.dumps(body))
        response.json = AsyncMock(return_value=body)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        contexts.append(context)
    session.request.side_effect = contexts
    return UniFiNetworkClient(
        auth=ApiKeyAuth(api_key="test-key"),
        base_url="https://192.168.1.1",
        connection_type=ConnectionType.LOCAL,
        session=session,
    )


def _assert_credential_absent(
    caplog: pytest.LogCaptureFixture, credential: str
) -> None:
    """Inspect messages and formatted exception chains at every captured level."""
    assert credential not in caplog.text
    formatter = logging.Formatter()
    for record in caplog.records:
        assert credential not in record.getMessage()
        assert credential not in formatter.format(record)
        if record.exc_info:
            assert credential not in formatter.formatException(record.exc_info)


async def test_root_validation_error_does_not_log_credential(
    voucher_flow: tuple[UnifiInsightsConfigCoordinator, UnifiInsightsFacadeCoordinator],
    caplog: pytest.LogCaptureFixture,
    monkeypatch,
) -> None:
    """A root model error stays private through HTTP, endpoint and command."""
    config, _ = voucher_flow
    code = "1234567890"
    client = _voucher_transport([{"vouchers": [code]}])
    patch_attribute(monkeypatch, config.network_client, "vouchers", client.vouchers)
    with caplog.at_level(logging.DEBUG), pytest.raises(HomeAssistantError) as caught:
        await config.async_generate_voucher(
            "site1", name="Home Assistant", time_limit_minutes=480
        )
    _assert_credential_absent(caplog, code)
    assert code not in "".join(traceback.format_exception(caught.value))
    assert caught.value.translation_key == "invalid_api_response"
    cause = caught.value.__cause__
    assert isinstance(cause, UniFiResponseError)
    assert cause.status_code == 200
    assert cause.message == "Voucher response failed validation (root)"
    assert cause.__cause__ is None
    assert cause.__context__ is None
    assert caught.value.__suppress_context__ is True


async def test_root_validation_error_does_not_log_credential_polling(
    voucher_flow: tuple[UnifiInsightsConfigCoordinator, UnifiInsightsFacadeCoordinator],
    caplog: pytest.LogCaptureFixture,
    monkeypatch,
) -> None:
    """A malformed root voucher preserves inventory without logging its code."""
    config, _ = voucher_flow
    await config.async_refresh()
    prior = {"old": {"id": "old", "code": "old-synthetic-code"}}
    config.data["vouchers"]["site1"] = prior
    code = "1234567890"
    client = _voucher_transport([{"data": [code], "totalCount": 1}])
    patch_attribute(monkeypatch, config.network_client, "vouchers", client.vouchers)
    with caplog.at_level(logging.DEBUG):
        await config.async_refresh_vouchers("site1")
    _assert_credential_absent(caplog, code)
    assert config.data["vouchers"]["site1"] == prior
    assert config._failed_sections == {("vouchers", "site1")}


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("code", [9876543210, [9876543210], {"value": 9876543210}])
async def test_numeric_code_does_not_log_credential(
    voucher_flow: tuple[UnifiInsightsConfigCoordinator, UnifiInsightsFacadeCoordinator],
    caplog: pytest.LogCaptureFixture,
    method: str,
    code: object,
    monkeypatch,
) -> None:
    """Invalid numeric and composite credential values stay private in DEBUG."""
    config, _ = voucher_flow
    await config.async_refresh()
    body = {"id": "v1", "code": code}
    client = _voucher_transport(
        [{"data": [body], "totalCount": 1} if method == "GET" else {"vouchers": [body]}]
    )
    patch_attribute(monkeypatch, config.network_client, "vouchers", client.vouchers)
    with caplog.at_level(logging.DEBUG):
        if method == "GET":
            await config.async_refresh_vouchers("site1")
            assert config.vouchers_available("site1") is False
        else:
            with pytest.raises(HomeAssistantError):
                await config.async_generate_voucher(
                    "site1", name="Home Assistant", time_limit_minutes=480
                )
    _assert_credential_absent(caplog, "9876543210")
    assert "<body omitted," in caplog.text


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize(
    "body",
    [
        {"data": {"vouchers": ["1234567890"]}},
        {"data": {"vouchers": [1234567890]}},
        {"data": "1234567890"},
        {"vouchers": "1234567890"},
        {"items": ["1234567890"]},
        "1234567890",
    ],
)
async def test_malformed_voucher_body_does_not_log_credential(
    voucher_flow: tuple[UnifiInsightsConfigCoordinator, UnifiInsightsFacadeCoordinator],
    caplog: pytest.LogCaptureFixture,
    method: str,
    body: Any,
    monkeypatch,
) -> None:
    """Malformed envelopes stay private through real transport and endpoint calls."""
    config, _ = voucher_flow
    client = _voucher_transport([body])
    patch_attribute(monkeypatch, config.network_client, "vouchers", client.vouchers)
    with caplog.at_level(logging.DEBUG):
        if method == "GET":
            await client.vouchers.get_all_pages("site1")
        else:
            with pytest.raises(HomeAssistantError):
                await config.async_generate_voucher(
                    "site1", name="Home Assistant", time_limit_minutes=480
                )
    _assert_credential_absent(caplog, "1234567890")
    assert f"<body omitted, {len(json.dumps(body).encode())} bytes>" in caplog.text


def _overlapping_pages() -> list[dict[str, Any]]:
    """Four advertised vouchers, but only three distinct IDs across two pages."""
    vouchers = [{"id": f"v{i}", "code": "1234567890"} for i in range(1, 4)]
    return [
        {"data": vouchers[:2], "totalCount": 4},
        {"data": vouchers[1:], "totalCount": 4},
    ]


async def test_partial_overlap_is_incomplete(caplog: pytest.LogCaptureFixture) -> None:
    """Reaching the row offset cannot establish a complete distinct inventory."""
    client = _voucher_transport(_overlapping_pages() * 2)
    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises(RuntimeError, match="Incomplete voucher listing") as caught,
    ):
        await client.vouchers.get_all_pages("site1")
    _assert_credential_absent(caplog, "1234567890")
    assert "1234567890" not in "".join(traceback.format_exception(caught.value))


async def test_partial_overlap_is_incomplete_coordinator(
    voucher_flow: tuple[UnifiInsightsConfigCoordinator, UnifiInsightsFacadeCoordinator],
    caplog: pytest.LogCaptureFixture,
    monkeypatch,
) -> None:
    """Incomplete transport results keep the prior inventory and isolate failure."""
    config, _ = voucher_flow
    await config.async_refresh()
    prior = {"old": {"id": "old", "code": "old-synthetic-code"}}
    config.data["vouchers"]["site1"] = prior
    client = _voucher_transport(_overlapping_pages() * 2)
    patch_attribute(monkeypatch, config.network_client, "vouchers", client.vouchers)
    with caplog.at_level(logging.DEBUG):
        await config.async_refresh()
    assert config.data["vouchers"]["site1"] == prior
    assert config.last_update_success is True
    assert config._failed_sections == {("vouchers", "site1")}
    assert config.wifi_available("site1") is True
    assert config.firewall_available("site1") is True
    _assert_credential_absent(caplog, "1234567890")


async def test_real_model_refresh_does_not_redisplay_expired_qr(
    hass: HomeAssistant,
    voucher_flow: tuple[UnifiInsightsConfigCoordinator, UnifiInsightsFacadeCoordinator],
    freezer: Any,
) -> None:
    """A real model's null timestamp defaults cannot resurrect an expired QR."""
    freezer.move_to("2026-10-09T11:59:00Z")
    config, facade = voucher_flow
    known = Voucher.model_validate(
        {
            "id": "v1",
            "code": "1234567890",
            "expired": False,
            "activatedAt": "2026-10-09T11:00:00Z",
            "expiresAt": "2026-10-09T12:00:00Z",
        }
    )
    require_mock(config.network_client.vouchers.get_all_pages).return_value = [known]
    await config.async_refresh()
    config.data = {
        **config.data,
        "latest_vouchers": {
            "site1": known.model_dump(by_alias=True, exclude_none=False)
        },
    }
    facade._aggregate_data()
    image = UnifiVoucherQrCodeImage(
        hass,
        facade,
        "site1",
        unique_id="console_site1_voucher_qr_code",
    )
    image.entity_id = "image.test_voucher"
    await image.async_added_to_hass()
    try:
        assert image.available is True
        assert await image.async_image() is not None
        assert image._expiration_unsub is not None
        freezer.move_to("2026-10-09T12:00:00Z")
        deadline = dt_util.parse_datetime("2026-10-09T12:00:00Z")
        assert deadline is not None
        with patch.object(image, "async_write_ha_state"):
            async_fire_time_changed(hass, deadline)
            await hass.async_block_till_done()
            assert image.available is False
            assert await image.async_image() is None
            assert image._expiration_unsub is None
            omitted = Voucher.model_validate(
                {"id": "v1", "code": "1234567890", "expired": False}
            )
            assert (
                omitted.model_dump(by_alias=True, exclude_none=False)["expiresAt"]
                is None
            )
            require_mock(config.network_client.vouchers.get_all_pages).return_value = [
                omitted
            ]
            await config.async_refresh_vouchers("site1")
            assert image.available is False
            assert await image.async_image() is None
            assert image._expiration_unsub is None
            assert (
                facade.data["latest_vouchers"]["site1"]["expiresAt"] == known.expires_at
            )
    finally:
        await image.async_will_remove_from_hass()


@pytest.mark.parametrize("boundary", ["config", "command"])
async def test_root_validation_error_does_not_log_credential_sanitizers(
    voucher_flow: tuple[UnifiInsightsConfigCoordinator, UnifiInsightsFacadeCoordinator],
    caplog: pytest.LogCaptureFixture,
    boundary: str,
) -> None:
    """The fetch and command boundaries log a malformed voucher without its code."""
    config, _ = voucher_flow
    code = "1234567890"
    with pytest.raises(UniFiResponseError) as invalid:
        parse_record(Voucher, code, "Voucher")
    fetch = AsyncMock(side_effect=invalid.value)
    with caplog.at_level(logging.DEBUG):
        if boundary == "config":
            assert (
                await config._fetch_optional_section("vouchers", "site1", fetch) is None
            )
        else:
            config.network_client.vouchers.create = fetch
            with pytest.raises(HomeAssistantError) as caught:
                await config.async_generate_voucher(
                    "site1", name="Guest", time_limit_minutes=60
                )
            assert caught.value.translation_key == "invalid_api_response"
            assert caught.value.__cause__ is invalid.value
            assert code not in "".join(traceback.format_exception(caught.value))
    _assert_credential_absent(caplog, code)
    assert "Voucher response failed validation (root)" in caplog.text


async def test_voucher_inventory_skips_records_without_id(
    voucher_flow: tuple[UnifiInsightsConfigCoordinator, UnifiInsightsFacadeCoordinator],
) -> None:
    """Voucher records lacking an id are not stored in the inventory."""
    config, _ = voucher_flow
    require_mock(config.network_client.vouchers.get_all_pages).return_value = [
        _create_mock_model({"id": "v1", "code": "1234567890"}),
        _create_mock_model({"code": "0987654321"}),
        _create_mock_model({"id": "", "code": "1111111111"}),
    ]
    await config.async_refresh()
    assert list(config.data["vouchers"]["site1"]) == ["v1"]


async def test_stale_targeted_voucher_refresh_is_discarded(
    voucher_flow: tuple[UnifiInsightsConfigCoordinator, UnifiInsightsFacadeCoordinator],
) -> None:
    """A slower, older targeted refresh cannot overwrite a newer inventory."""
    config, _ = voucher_flow
    await config.async_refresh()
    older = _create_mock_model({"id": "older", "code": "1234567890"})
    newer = _create_mock_model({"id": "newer", "code": "0987654321"})
    older_started = asyncio.Event()
    release_older = asyncio.Event()
    calls = 0

    async def get_all_pages(site_id: str) -> list[MagicMock]:
        nonlocal calls
        calls += 1
        if calls == 1:
            older_started.set()
            await release_older.wait()
            return [older]
        return [newer]

    config.network_client.vouchers.get_all_pages = AsyncMock(side_effect=get_all_pages)
    older_task = asyncio.create_task(config.async_refresh_vouchers("site1"))
    await older_started.wait()
    await config.async_refresh_vouchers("site1")
    assert list(config.data["vouchers"]["site1"]) == ["newer"]

    release_older.set()
    await older_task
    assert list(config.data["vouchers"]["site1"]) == ["newer"]
    assert config.vouchers_available("site1") is True


async def test_stale_full_poll_keeps_newer_voucher_failure(
    voucher_flow: tuple[UnifiInsightsConfigCoordinator, UnifiInsightsFacadeCoordinator],
) -> None:
    """A stale full poll does not clear a newer targeted refresh's failure."""
    config, _ = voucher_flow
    await config.async_refresh()
    prior = {"old": {"id": "old", "code": "old-synthetic-code"}}
    config.data["vouchers"]["site1"] = prior
    poll_started = asyncio.Event()
    release_poll = asyncio.Event()
    calls = 0

    async def get_all_pages(site_id: str) -> list[MagicMock]:
        nonlocal calls
        calls += 1
        if calls == 1:
            poll_started.set()
            await release_poll.wait()
            return [_create_mock_model({"id": "polled", "code": "1234567890"})]
        msg = "Refused"
        raise UniFiConnectionError(msg)

    config.network_client.vouchers.get_all_pages = AsyncMock(side_effect=get_all_pages)
    poll_task = asyncio.create_task(config.async_refresh())
    await poll_started.wait()
    await config.async_refresh_vouchers("site1")
    assert config.vouchers_available("site1") is False

    release_poll.set()
    await poll_task
    assert config.vouchers_available("site1") is False
    assert config.data["vouchers"]["site1"] == prior


# --- Voucher writes ----------------------------------------------------------


class TestConfigCoordinatorVouchers:
    """Tests for voucher polling in UnifiInsightsConfigCoordinator."""

    @pytest.fixture
    async def coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> AsyncGenerator[UnifiInsightsConfigCoordinator]:
        """Create a config coordinator for testing."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        coordinator = UnifiInsightsConfigCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )
        try:
            yield coordinator
        finally:
            await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_vouchers_polled_for_each_selected_site(
        self, hass: HomeAssistant
    ) -> None:
        """Vouchers are polled for each selected site and stored keyed by voucher ID."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_API_KEY: "test_api_key"},
            options={CONF_SITE_IDS: ["default", "site2"]},
        )
        network_client = _create_mock_network_client()
        network_client.vouchers.get_all_pages = AsyncMock(
            side_effect=lambda site_id: [
                _create_mock_model(
                    {
                        "id": f"{site_id}_v1",
                        "code": "1234567890",
                        "timeLimitMinutes": 480,
                    }
                )
            ]
        )
        coordinator = UnifiInsightsConfigCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=None,
            entry=entry,
        )
        result = await coordinator._async_update_data()
        assert (
            require_mock(coordinator.network_client.vouchers.get_all_pages).await_count
            == 2
        )
        polled_sites = [
            c.args[0]
            for c in require_mock(
                coordinator.network_client.vouchers.get_all_pages
            ).await_args_list
        ]
        assert polled_sites == ["default", "site2"]
        assert "vouchers" in result
        assert result["vouchers"]["default"]["default_v1"]["code"] == "1234567890"
        assert result["vouchers"]["default"]["default_v1"]["timeLimitMinutes"] == 480
        assert result["vouchers"]["site2"]["site2_v1"]["code"] == "1234567890"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            UniFiNotFoundError("Not found", status_code=404),
            UniFiResponseError("Bad request", status_code=400),
            UniFiAuthenticationError("Forbidden", status_code=403),
            UniFiResponseError(
                "HTML instead of JSON", status_code=200, response_body="<html></html>"
            ),
        ],
        ids=["404", "400", "403", "200-non-json"],
    )
    async def test_vouchers_unsupported_endpoint_gives_empty_inventory(
        self, coordinator: UnifiInsightsConfigCoordinator, error: Exception
    ) -> None:
        """A 4xx / non-JSON error gives empty inventory and stays available."""
        coordinator.network_client.vouchers.get_all_pages = AsyncMock(side_effect=error)
        result = await coordinator._async_update_data()
        assert result["vouchers"]["default"] == {}
        assert coordinator.vouchers_available("default") is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            UniFiResponseError("Server error", status_code=500),
            UniFiRateLimitError("Slow down", status_code=429),
            UniFiConnectionError("Connection refused"),
            UniFiTimeoutError("Timed out"),
            ValueError("Unparseable payload"),
        ],
        ids=["500", "429", "connection", "timeout", "unexpected"],
    )
    async def test_vouchers_transient_failure_keeps_inventory_and_flags_section(
        self, coordinator: UnifiInsightsConfigCoordinator, error: Exception
    ) -> None:
        """Transient error keeps inventory and marks section unavailable."""
        coordinator.network_client.vouchers.get_all_pages = AsyncMock(
            return_value=[_create_mock_model({"id": "v1", "code": "1111111111"})]
        )
        await coordinator.async_refresh()
        assert coordinator.vouchers_available("default") is True
        assert coordinator.wifi_available("default") is True
        assert coordinator.firewall_available("default") is True
        assert "v1" in coordinator.data["vouchers"]["default"]

        coordinator.network_client.vouchers.get_all_pages = AsyncMock(side_effect=error)
        await coordinator.async_refresh()

        assert coordinator.last_update_success is True
        assert "v1" in coordinator.data["vouchers"]["default"]
        assert coordinator.vouchers_available("default") is False
        assert coordinator.wifi_available("default") is True
        assert coordinator.firewall_available("default") is True

    @pytest.mark.asyncio
    async def test_vouchers_recover_after_failure(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Section flags as recovered and available after transient failure resolves."""
        coordinator.network_client.vouchers.get_all_pages = AsyncMock(
            side_effect=UniFiConnectionError("Connection lost")
        )
        await coordinator.async_refresh()
        assert coordinator.vouchers_available("default") is False

        coordinator.network_client.vouchers.get_all_pages = AsyncMock(
            return_value=[_create_mock_model({"id": "v1", "code": "1111111111"})]
        )
        await coordinator.async_refresh()
        assert coordinator.vouchers_available("default") is True
        assert "v1" in coordinator.data["vouchers"]["default"]

    @pytest.mark.asyncio
    async def test_vouchers_authentication_failure_raises_auth_failed(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """A 401 error from vouchers endpoint raises ConfigEntryAuthFailed."""
        coordinator.network_client.vouchers.get_all_pages = AsyncMock(
            side_effect=UniFiAuthenticationError("Revoked", status_code=401)
        )
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()
        assert coordinator._available is False

    @pytest.mark.asyncio
    async def test_vouchers_pruned_when_site_deselected(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Vouchers for deselected sites are pruned from coordinator data."""
        coordinator.data["vouchers"]["stale_site"] = {"v1": {"id": "v1"}}
        result = await coordinator._async_update_data()
        assert "stale_site" not in result["vouchers"]
        assert "default" in result["vouchers"]

    @pytest.mark.asyncio
    async def test_vouchers_cleared_when_no_site_selected(
        self, hass: HomeAssistant
    ) -> None:
        """Vouchers dict is cleared when no sites exist / match selection."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_API_KEY: "test_api_key"},
            options={CONF_SITE_IDS: ["nonexistent"]},
        )
        coordinator = UnifiInsightsConfigCoordinator(
            hass=hass,
            network_client=_create_mock_network_client(),
            protect_client=None,
            entry=entry,
        )
        result = await coordinator._async_update_data()
        assert result["vouchers"] == {}

    @pytest.mark.asyncio
    async def test_async_refresh_vouchers_updates_only_that_site(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """async_refresh_vouchers refreshes only target site and notifies listeners."""
        await coordinator.async_refresh()
        listener = MagicMock()
        coordinator.async_add_listener(listener)

        coordinator.network_client.vouchers.get_all_pages = AsyncMock(
            return_value=[_create_mock_model({"id": "v-new", "code": "2222222222"})]
        )
        await coordinator.async_refresh_vouchers("default")

        assert coordinator.network_client.vouchers.get_all_pages.await_count == 1
        assert require_not_none(
            coordinator.network_client.vouchers.get_all_pages.await_args
        )[0] == ("default",)
        assert "v-new" in coordinator.data["vouchers"]["default"]
        listener.assert_called_once()

        # Target error sets failed section without throwing
        listener.reset_mock()
        coordinator.network_client.vouchers.get_all_pages = AsyncMock(
            side_effect=UniFiConnectionError("Refused")
        )
        await coordinator.async_refresh_vouchers("default")
        assert coordinator.vouchers_available("default") is False
        assert "v-new" in coordinator.data["vouchers"]["default"]
        listener.assert_called_once()

        # Target success clears failed section
        coordinator.network_client.vouchers.get_all_pages = AsyncMock(return_value=[])
        await coordinator.async_refresh_vouchers("default")
        assert coordinator.vouchers_available("default") is True

        # Unselected site returns without call
        coordinator.network_client.vouchers.get_all_pages.reset_mock()
        await coordinator.async_refresh_vouchers("unknown_site")
        coordinator.network_client.vouchers.get_all_pages.assert_not_called()

    @pytest.mark.asyncio
    async def test_vouchers_available_follows_last_update_success(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """vouchers_available returns False when last_update_success is False."""
        await coordinator.async_refresh()
        assert coordinator.vouchers_available("default") is True
        coordinator.last_update_success = False
        assert coordinator.vouchers_available("default") is False

    @pytest.mark.asyncio
    async def test_vouchers_incomplete_listing_keeps_inventory_and_flags_section(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Incomplete voucher listing keeps inventory and marks unavailable."""
        coordinator.network_client.vouchers.get_all_pages = AsyncMock(
            return_value=[_create_mock_model({"id": "v1", "code": "1111111111"})]
        )
        await coordinator.async_refresh()
        assert coordinator.vouchers_available("default") is True
        assert "v1" in coordinator.data["vouchers"]["default"]

        coordinator.network_client.vouchers.get_all_pages = AsyncMock(
            side_effect=RuntimeError(
                "Incomplete voucher listing for site default: reached page limit 50"
            )
        )
        await coordinator.async_refresh()
        assert coordinator.vouchers_available("default") is False
        assert "v1" in coordinator.data["vouchers"]["default"]

    async def test_vouchers_validation_error_does_not_leak_code_in_logs(
        self,
        coordinator: UnifiInsightsConfigCoordinator,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A malformed voucher during polling does not log the voucher code."""
        synthetic_code = "1234567890"

        class _TestClient:
            def build_api_path(self, path: str) -> str:
                return path

            async def _get(
                self,
                path: str,
                params: Any = None,
                *,
                expected_unsupported: bool = False,
                log_body: bool = True,
            ) -> Any:
                assert path.endswith("/hotspot/vouchers")
                assert params == {"offset": 0, "limit": 1000}
                assert expected_unsupported is True
                assert log_body is False
                return {"data": [{"code": synthetic_code}], "totalCount": 1}

        coordinator.network_client.vouchers = VouchersEndpoint(_TestClient())  # type: ignore[assignment]
        with caplog.at_level("DEBUG"):
            await coordinator.async_refresh()

        assert coordinator.vouchers_available("default") is False
        assert synthetic_code not in caplog.text
        assert "Voucher response failed validation (id)" in caplog.text


# --- Voucher commands and state on the config coordinator (ADR-013) ----------


def _voucher(voucher_id: str, code: str, name: str = "G") -> Voucher:
    """Return a library voucher model."""
    return Voucher.model_validate({"id": voucher_id, "code": code, "name": name})


def _seed_latest(
    config: UnifiInsightsConfigCoordinator, site_id: str, record: dict[str, Any]
) -> None:
    """Publish a latest voucher the way a generate does, copy-on-write."""
    config.data = {
        **config.data,
        "latest_vouchers": {**config.data["latest_vouchers"], site_id: record},
    }


async def test_voucher_failure_carries_command_failed(
    seeded_owners: SeededOwners,
) -> None:
    """A voucher command keeps its message and gains the generic key."""
    delete = seeded_owners.network_client.vouchers.delete
    cause = UniFiConnectionError("down")
    delete.side_effect = cause

    with pytest.raises(
        HomeAssistantError, match=r"^Unable to delete voucher"
    ) as caught:
        await seeded_owners.config.async_delete_voucher(SITE_ID, "v1")

    assert caught.value.translation_key == "command_failed"
    assert caught.value.__cause__ is cause
    delete.assert_awaited_once_with(SITE_ID, "v1")


class TestConfigGenerateVoucher:
    """Voucher generate and delete, owned by the config coordinator."""

    async def test_generate_voucher_with_rate_limits(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Test async_generate_voucher with rate limits and usage limits."""
        config = seeded_owners.config
        require_mock(config.network_client.vouchers.create).return_value = []
        await config.async_generate_voucher(
            site_id=SITE_ID,
            name="VIP Guest",
            time_limit_minutes=120,
            count=2,
            tx_rate_limit_kbps=1000,
            rx_rate_limit_kbps=2000,
            data_usage_limit_mbytes=500,
        )
        require_mock(config.network_client.vouchers.create).assert_awaited_once_with(
            SITE_ID,
            name="VIP Guest",
            time_limit_minutes=120,
            count=2,
            tx_rate_limit_kbps=1000,
            rx_rate_limit_kbps=2000,
            data_usage_limit_mbytes=500,
        )

    async def test_generate_voucher_without_limits(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Test async_generate_voucher without optional limits."""
        config = seeded_owners.config
        require_mock(config.network_client.vouchers.create).return_value = []
        await config.async_generate_voucher(
            site_id=SITE_ID,
            name="Standard Guest",
            time_limit_minutes=60,
            count=1,
        )
        require_mock(config.network_client.vouchers.create).assert_awaited_once_with(
            SITE_ID,
            name="Standard Guest",
            time_limit_minutes=60,
            count=1,
        )

    async def test_generate_voucher_passes_authorized_guest_limit(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Test async_generate_voucher passes authorized_guest_limit to create."""
        config = seeded_owners.config
        require_mock(config.network_client.vouchers.create).return_value = []
        await config.async_generate_voucher(
            site_id=SITE_ID,
            name="Guest",
            time_limit_minutes=60,
            count=1,
            authorized_guest_limit=2,
        )
        require_mock(config.network_client.vouchers.create).assert_awaited_once_with(
            SITE_ID,
            name="Guest",
            time_limit_minutes=60,
            count=1,
            authorized_guest_limit=2,
        )

    async def test_generate_voucher_returns_created_vouchers_and_records_latest(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Test async_generate_voucher returns created vouchers and records latest."""
        config, facade = seeded_owners.config, seeded_owners.facade
        v1 = _voucher("v1", "1111111111", "G1")
        require_mock(config.network_client.vouchers.create).return_value = [v1]
        result1 = await config.async_generate_voucher(
            site_id=SITE_ID, name="G1", time_limit_minutes=60
        )
        assert result1 == [v1]
        assert facade.data["latest_vouchers"][SITE_ID]["id"] == "v1"

        v2 = _voucher("v2", "2222222222", "G2")
        require_mock(config.network_client.vouchers.create).return_value = [v2]
        result2 = await config.async_generate_voucher(
            site_id=SITE_ID, name="G2", time_limit_minutes=60
        )
        assert result2 == [v2]
        assert facade.data["latest_vouchers"][SITE_ID]["id"] == "v2"

    async def test_generate_voucher_records_last_voucher_of_a_batch(
        self, seeded_owners: SeededOwners
    ) -> None:
        """When multiple vouchers are created, the last one is recorded as latest."""
        config = seeded_owners.config
        require_mock(config.network_client.vouchers.create).return_value = [
            _voucher("v1", "1111111111", "Batch"),
            _voucher("v2", "2222222222", "Batch"),
        ]
        await config.async_generate_voucher(
            site_id=SITE_ID, name="Batch", time_limit_minutes=60, count=2
        )
        assert seeded_owners.facade.data["latest_vouchers"][SITE_ID]["id"] == "v2"

    async def test_generate_voucher_with_unusable_result_records_nothing(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Unusable result (MagicMock) does not record latest voucher."""
        config = seeded_owners.config
        require_mock(config.network_client.vouchers.create).return_value = MagicMock()
        result = await config.async_generate_voucher(
            site_id=SITE_ID, name="Unusable", time_limit_minutes=60
        )
        assert result == []
        assert seeded_owners.facade.data["latest_vouchers"] == {}

    async def test_generate_voucher_refreshes_that_sites_inventory(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Creating a voucher triggers targeted refresh for that site."""
        config = seeded_owners.config
        get_all_pages = config.network_client.vouchers.get_all_pages
        require_mock(get_all_pages).reset_mock()
        require_mock(config.network_client.vouchers.create).return_value = [
            _voucher("v1", "1111111111", "G1")
        ]
        await config.async_generate_voucher(
            site_id=SITE_ID, name="G1", time_limit_minutes=60
        )
        require_mock(get_all_pages).assert_awaited_once_with(SITE_ID)

    async def test_generate_voucher_survives_refresh_failure(
        self,
        seeded_owners: SeededOwners,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Targeted refresh failure still succeeds without voucher code in log."""
        config = seeded_owners.config
        v = _voucher("v1", "1111111111", "G1")
        require_mock(config.network_client.vouchers.create).return_value = [v]
        with (
            patch.object(
                config,
                "async_refresh_vouchers",
                AsyncMock(side_effect=RuntimeError("Refresh error")),
            ),
            caplog.at_level("WARNING"),
        ):
            result = await config.async_generate_voucher(
                site_id=SITE_ID, name="G1", time_limit_minutes=60
            )
        assert result == [v]
        assert seeded_owners.facade.data["latest_vouchers"][SITE_ID]["id"] == "v1"
        assert f"Unable to refresh the vouchers of site {SITE_ID}" in caplog.text
        assert "1111111111" not in caplog.text

    async def test_generate_voucher_validation_error_does_not_leak_code_in_logs(
        self,
        seeded_owners: SeededOwners,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A malformed created voucher does not leak its code in logs."""
        synthetic_code = "1234567890"
        with pytest.raises(UniFiResponseError) as validation_info:
            parse_record(Voucher, {"code": synthetic_code}, "Voucher")
        config = seeded_owners.config
        require_mock(
            config.network_client.vouchers.create
        ).side_effect = validation_info.value
        with (
            caplog.at_level("DEBUG"),
            pytest.raises(HomeAssistantError),
        ):
            await config.async_generate_voucher(
                site_id=SITE_ID, name="G1", time_limit_minutes=60
            )

        assert synthetic_code not in caplog.text

    async def test_generate_voucher_real_endpoint_validation_error_does_not_leak_code(
        self,
        seeded_owners: SeededOwners,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Real endpoint validation failure does not leak code in logs."""
        synthetic_code = "1234567890"

        class _TestClient:
            def build_api_path(self, path: str) -> str:
                return path

            async def _post(
                self,
                path: str,
                json_data: Any = None,
                *,
                log_body: bool = True,
            ) -> Any:
                assert path.endswith("/hotspot/vouchers")
                assert json_data == {
                    "count": 1,
                    "name": "G1",
                    "timeLimitMinutes": 60,
                }
                assert log_body is False
                return {"vouchers": [{"code": synthetic_code}]}

        config = seeded_owners.config
        config.network_client.vouchers = VouchersEndpoint(_TestClient())  # type: ignore[assignment]
        with (
            caplog.at_level("DEBUG"),
            pytest.raises(HomeAssistantError),
        ):
            await config.async_generate_voucher(
                site_id=SITE_ID, name="G1", time_limit_minutes=60
            )

        assert synthetic_code not in caplog.text
        assert "Voucher response failed validation (id)" in caplog.text

    async def test_refresh_vouchers_auth_failure_starts_reauth_and_notifies(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A targeted voucher refresh auth failure starts reauth and notifies."""
        config, facade = seeded_owners.config, seeded_owners.facade
        require_mock(
            config.network_client.vouchers.get_all_pages
        ).side_effect = UniFiAuthenticationError("Revoked", status_code=401)
        listener = MagicMock()
        facade.async_add_listener(listener)

        with patch.object(config.config_entry, "async_start_reauth") as start_reauth:
            await config._async_refresh_vouchers_after_change(SITE_ID)

        start_reauth.assert_called_once_with(config.hass)
        listener.assert_called_once()

    async def test_refresh_vouchers_expected_failure_notifies(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Expected targeted voucher refresh errors keep the action successful."""
        config, facade = seeded_owners.config, seeded_owners.facade
        listener = MagicMock()
        facade.async_add_listener(listener)

        with patch.object(
            config,
            "async_refresh_vouchers",
            AsyncMock(side_effect=UniFiConnectionError("Down")),
        ):
            await config._async_refresh_vouchers_after_change(SITE_ID)

        listener.assert_called_once()

    async def test_refresh_vouchers_unexpected_failure_propagates(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Unexpected targeted voucher refresh errors are not hidden."""
        config = seeded_owners.config
        error = TypeError("broken implementation")

        with (
            patch.object(
                config, "async_refresh_vouchers", AsyncMock(side_effect=error)
            ),
            pytest.raises(TypeError) as exc_info,
        ):
            await config._async_refresh_vouchers_after_change(SITE_ID)

        assert exc_info.value is error

    async def test_generate_voucher_api_error_raises_and_skips_refresh(
        self, seeded_owners: SeededOwners
    ) -> None:
        """API failure raises HomeAssistantError and does not call refresh."""
        config = seeded_owners.config
        get_all_pages = config.network_client.vouchers.get_all_pages
        require_mock(get_all_pages).reset_mock()
        require_mock(config.network_client.vouchers.create).side_effect = RuntimeError(
            "Network failure"
        )
        with pytest.raises(HomeAssistantError, match="Unable to generate voucher"):
            await config.async_generate_voucher(
                site_id=SITE_ID, name="G1", time_limit_minutes=60
            )
        require_mock(get_all_pages).assert_not_awaited()
        assert config.data["latest_vouchers"] == {}

    async def test_delete_voucher_refreshes_and_clears_matching_latest(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Deleting matching voucher clears latest and refreshes inventory."""
        config = seeded_owners.config
        _seed_latest(config, SITE_ID, {"id": "v1", "code": "1111111111"})
        get_all_pages = config.network_client.vouchers.get_all_pages
        require_mock(get_all_pages).reset_mock()
        require_mock(config.network_client.vouchers.delete).return_value = True
        await config.async_delete_voucher(SITE_ID, "v1")
        assert SITE_ID not in config.data["latest_vouchers"]
        assert SITE_ID not in seeded_owners.facade.data["latest_vouchers"]
        require_mock(get_all_pages).assert_awaited_once_with(SITE_ID)

    async def test_delete_voucher_keeps_latest_of_other_voucher(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Deleting another voucher leaves latest intact and refreshes inventory."""
        config = seeded_owners.config
        _seed_latest(config, SITE_ID, {"id": "v2", "code": "2222222222"})
        get_all_pages = config.network_client.vouchers.get_all_pages
        require_mock(get_all_pages).reset_mock()
        require_mock(config.network_client.vouchers.delete).return_value = True
        await config.async_delete_voucher(SITE_ID, "v1")
        assert config.data["latest_vouchers"][SITE_ID]["id"] == "v2"
        require_mock(get_all_pages).assert_awaited_once_with(SITE_ID)

    async def test_latest_voucher_is_refreshed_from_inventory(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Latest voucher updates attributes when refreshed from inventory."""
        config, facade = seeded_owners.config, seeded_owners.facade
        _seed_latest(config, SITE_ID, {"id": "v1", "code": "1111111111"})
        # A dict passes _model_to_dict unchanged, keeping the string timestamp.
        require_mock(config.network_client.vouchers.get_all_pages).return_value = [
            {
                "id": "v1",
                "code": "1111111111",
                "expired": True,
                "activatedAt": "2026-10-09T12:00:00Z",
            }
        ]
        await config.async_refresh_vouchers(SITE_ID)
        latest = facade.data["latest_vouchers"][SITE_ID]
        assert latest["expired"] is True
        assert latest["activatedAt"] == "2026-10-09T12:00:00Z"

    async def test_latest_voucher_is_kept_when_missing_from_inventory(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Latest voucher is kept in memory even if missing from polled inventory."""
        config, facade = seeded_owners.config, seeded_owners.facade
        _seed_latest(config, SITE_ID, {"id": "v1", "code": "1111111111"})
        require_mock(config.network_client.vouchers.get_all_pages).return_value = []
        await config.async_refresh()
        assert config.data["vouchers"][SITE_ID] == {}
        assert facade.data["latest_vouchers"][SITE_ID]["id"] == "v1"

    def test_latest_voucher_is_kept_when_inventory_is_not_a_mapping(self) -> None:
        """A malformed voucher inventory leaves the latest voucher untouched."""
        latest = {"id": "v1", "code": "1111111111"}
        assert refreshed_latest_vouchers({"s": latest}, None, ("s",)) == {"s": latest}
        assert refreshed_latest_vouchers({"s": latest}, {"s": None}, ("s",)) == {
            "s": latest
        }

    async def test_get_voucher_settings_creates_defaults_once_per_site(
        self, seeded_owners: SeededOwners
    ) -> None:
        """get_voucher_settings returns existing or initializes default settings."""
        config = seeded_owners.config
        s1 = config.get_voucher_settings("site1")
        assert isinstance(s1, VoucherSettings)
        assert s1.duration_minutes == 480
        s2 = config.get_voucher_settings("site1")
        assert s1 is s2

    async def test_async_generate_voucher(self, seeded_owners: SeededOwners) -> None:
        """Test async_generate_voucher delegates correctly."""
        config = seeded_owners.config
        require_mock(config.network_client.vouchers.create).return_value = []
        await config.async_generate_voucher(
            SITE_ID,
            count=5,
            time_limit_minutes=60,
            tx_rate_limit_kbps=1024,
            rx_rate_limit_kbps=2048,
            data_usage_limit_mbytes=500,
            name="test",
        )
        require_mock(config.network_client.vouchers.create).assert_called_once_with(
            SITE_ID,
            count=5,
            time_limit_minutes=60,
            tx_rate_limit_kbps=1024,
            rx_rate_limit_kbps=2048,
            data_usage_limit_mbytes=500,
            name="test",
        )

    async def test_async_delete_voucher(self, seeded_owners: SeededOwners) -> None:
        """Test async_delete_voucher delegates correctly."""
        config = seeded_owners.config
        await config.async_delete_voucher(SITE_ID, "voucher1")
        require_mock(config.network_client.vouchers.delete).assert_called_once_with(
            SITE_ID, "voucher1"
        )


class TestConfigVoucherState:
    """Voucher settings and latest vouchers are replaced, never changed in place."""

    async def test_set_voucher_setting_replaces_the_settings(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A set replaces the site's settings; the old object stays unchanged."""
        config = seeded_owners.config
        before = config.get_voucher_settings(SITE_ID)

        config.set_voucher_setting(SITE_ID, "guest_limit", 3)

        after = config.get_voucher_settings(SITE_ID)
        assert after is not before
        assert before == VoucherSettings()
        assert after == VoucherSettings(guest_limit=3)
        assert config.get_voucher_settings("other-site") == VoucherSettings()

    def test_voucher_settings_are_frozen(self) -> None:
        """VoucherSettings cannot be changed in place."""
        settings = VoucherSettings()
        with pytest.raises(FrozenInstanceError):
            settings.guest_limit = 2  # type: ignore[misc]

    async def test_set_voucher_setting_does_not_notify(
        self, seeded_owners: SeededOwners
    ) -> None:
        """Settings live in Home Assistant only, so no listener is notified."""
        listener = MagicMock()
        seeded_owners.facade.async_add_listener(listener)
        seeded_owners.config.set_voucher_setting(SITE_ID, "duration_minutes", 60)
        listener.assert_not_called()

    async def test_generate_publishes_latest_vouchers_copy_on_write(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A generate publishes a new latest map; older snapshots keep theirs."""
        config, facade = seeded_owners.config, seeded_owners.facade
        old_root = config.data
        old_latest = config.data["latest_vouchers"]
        old_snapshot = facade.data
        require_mock(config.network_client.vouchers.create).return_value = [
            _voucher("v1", "1111111111")
        ]

        await config.async_generate_voucher(SITE_ID, name="G", time_limit_minutes=60)

        assert old_latest == {}
        assert old_root["latest_vouchers"] is old_latest
        assert old_snapshot["latest_vouchers"] == {}
        assert config.data["latest_vouchers"] is not old_latest
        assert facade.data["latest_vouchers"] is config.data["latest_vouchers"]

    async def test_full_refresh_keeps_latest_of_deselected_site(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A site without a committed inventory keeps its latest voucher."""
        config = seeded_owners.config
        record = {"id": "v9", "code": "9999999999"}
        _seed_latest(config, "gone-site", record)
        await config.async_refresh()
        assert config.data["latest_vouchers"]["gone-site"] is record

    async def test_refresh_of_unknown_site_publishes_nothing(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A targeted refresh of an unknown site leaves the data root alone."""
        config = seeded_owners.config
        root = config.data
        await config.async_refresh_vouchers("unknown-site")
        assert config.data is root

    async def test_facade_latest_vouchers_is_the_config_object(
        self, seeded_owners: SeededOwners
    ) -> None:
        """The facade passes latest vouchers through by reference, across rebuilds."""
        config, facade = seeded_owners.config, seeded_owners.facade
        _seed_latest(config, SITE_ID, {"id": "v1", "code": "1111111111"})
        facade._aggregate_data()
        latest = config.data["latest_vouchers"]
        assert facade.data["latest_vouchers"] is latest

        # A device refresh rebuilds the facade but leaves the section alone.
        await seeded_owners.device.async_refresh()
        assert facade.data["latest_vouchers"] is latest
