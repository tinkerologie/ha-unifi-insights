# Copyright 2026 UniFi Insights contributors
"""End-to-end flow tests for hotspot vouchers with real coordinators."""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from homeassistant.core import HomeAssistant

from homeassistant.const import CONF_API_KEY
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from unifi_official_api import ApiKeyAuth, ConnectionType
from unifi_official_api.exceptions import UniFiConnectionError
from unifi_official_api.network import UniFiNetworkClient
from unifi_official_api.network.models.voucher import Voucher

from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.coordinators.config import (
    UnifiInsightsConfigCoordinator,
)
from custom_components.unifi_insights.coordinators.device import (
    UnifiInsightsDeviceCoordinator,
)
from custom_components.unifi_insights.coordinators.facade import (
    UnifiInsightsFacadeCoordinator,
)
from custom_components.unifi_insights.data_transforms import (
    count_active_vouchers,
)
from custom_components.unifi_insights.image import UnifiVoucherQrCodeImage


def _create_mock_model(data: dict) -> MagicMock:
    """Create a mock model returning data from model_dump."""
    mock = MagicMock()
    mock.model_dump = MagicMock(return_value=data)
    for k, v in data.items():
        setattr(mock, k, v)
    return mock


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
    result = await facade.async_generate_voucher(
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
    facade._latest_vouchers["site1"] = {
        "id": "v-1",
        "code": "1234567890",
        "expired": False,
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
) -> None:
    """A root model error stays private through HTTP, endpoint and facade."""
    _, facade = voucher_flow
    code = "1234567890"
    client = _voucher_transport([{"vouchers": [code]}])
    facade.network_client.vouchers = client.vouchers
    with caplog.at_level(logging.DEBUG), pytest.raises(HomeAssistantError) as caught:
        await facade.async_generate_voucher(
            "site1", name="Home Assistant", time_limit_minutes=480
        )
    _assert_credential_absent(caplog, code)
    assert code not in "".join(traceback.format_exception(caught.value))
    cause = caught.value.__cause__
    assert isinstance(cause, ValueError)
    assert str(cause) == "Invalid voucher data (fields: root)"
    assert cause.__cause__ is None
    assert cause.__suppress_context__ is True
    assert caught.value.__suppress_context__ is True


async def test_root_validation_error_does_not_log_credential_polling(
    voucher_flow: tuple[UnifiInsightsConfigCoordinator, UnifiInsightsFacadeCoordinator],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A malformed root voucher preserves inventory without logging its code."""
    config, _ = voucher_flow
    await config.async_refresh()
    prior = {"old": {"id": "old", "code": "old-synthetic-code"}}
    config.data["vouchers"]["site1"] = prior
    code = "1234567890"
    client = _voucher_transport([{"data": [code], "totalCount": 1}])
    config.network_client.vouchers = client.vouchers
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
) -> None:
    """Invalid numeric and composite credential values stay private in DEBUG."""
    config, facade = voucher_flow
    await config.async_refresh()
    body = {"id": "v1", "code": code}
    client = _voucher_transport(
        [{"data": [body], "totalCount": 1} if method == "GET" else {"vouchers": [body]}]
    )
    config.network_client.vouchers = client.vouchers
    with caplog.at_level(logging.DEBUG):
        if method == "GET":
            await config.async_refresh_vouchers("site1")
            assert config.vouchers_available("site1") is False
        else:
            with pytest.raises(HomeAssistantError):
                await facade.async_generate_voucher(
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
) -> None:
    """Malformed envelopes stay private through real transport and endpoint calls."""
    config, facade = voucher_flow
    client = _voucher_transport([body])
    config.network_client.vouchers = client.vouchers
    with caplog.at_level(logging.DEBUG):
        if method == "GET":
            await client.vouchers.get_all_pages("site1")
        else:
            with pytest.raises(HomeAssistantError):
                await facade.async_generate_voucher(
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
) -> None:
    """Incomplete transport results keep the prior inventory and isolate failure."""
    config, _ = voucher_flow
    await config.async_refresh()
    prior = {"old": {"id": "old", "code": "old-synthetic-code"}}
    config.data["vouchers"]["site1"] = prior
    client = _voucher_transport(_overlapping_pages() * 2)
    config.network_client.vouchers = client.vouchers
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
    config.network_client.vouchers.get_all_pages.return_value = [known]
    await config.async_refresh()
    facade._latest_vouchers["site1"] = known.model_dump(
        by_alias=True, exclude_none=False
    )
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
            config.network_client.vouchers.get_all_pages.return_value = [omitted]
            await config.async_refresh_vouchers("site1")
            assert image.available is False
            assert await image.async_image() is None
            assert image._expiration_unsub is None
            assert (
                facade.data["latest_vouchers"]["site1"]["expiresAt"] == known.expires_at
            )
    finally:
        await image.async_will_remove_from_hass()


@pytest.mark.parametrize("boundary", ["config", "facade"])
async def test_root_validation_error_does_not_log_credential_sanitizers(
    voucher_flow: tuple[UnifiInsightsConfigCoordinator, UnifiInsightsFacadeCoordinator],
    caplog: pytest.LogCaptureFixture,
    boundary: str,
) -> None:
    """Both coordinator sanitizers handle direct empty-location errors safely."""
    config, facade = voucher_flow
    code = "1234567890"
    with pytest.raises(ValidationError) as invalid:
        Voucher.model_validate(code)
    fetch = AsyncMock(side_effect=invalid.value)
    with caplog.at_level(logging.DEBUG):
        if boundary == "config":
            assert (
                await config._fetch_optional_section("vouchers", "site1", fetch) is None
            )
        else:
            facade.network_client.vouchers.create = fetch
            with pytest.raises(HomeAssistantError) as caught:
                await facade.async_generate_voucher(
                    "site1", name="Guest", time_limit_minutes=60
                )
            assert caught.value.__cause__ is None
            assert caught.value.__suppress_context__ is True
            assert code not in "".join(traceback.format_exception(caught.value))
    _assert_credential_absent(caplog, code)
    assert "root" in caplog.text


async def test_voucher_inventory_skips_records_without_id(
    voucher_flow: tuple[UnifiInsightsConfigCoordinator, UnifiInsightsFacadeCoordinator],
) -> None:
    """Voucher records lacking an id are not stored in the inventory."""
    config, _ = voucher_flow
    config.network_client.vouchers.get_all_pages.return_value = [
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
