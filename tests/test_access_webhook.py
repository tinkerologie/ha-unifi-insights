# Copyright 2026 UniFi Insights contributors
"""Verify the signed Access webhook: verification, receiver and lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from http import HTTPStatus
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components import webhook
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api import (
    UniFiConnectionError,
    UniFiNotFoundError,
    UniFiResponseError,
    UniFiValidationError,
)
from unifi_official_api.access.models import AccessWebhookEndpoint

from custom_components.unifi_insights import access_webhook as aw
from custom_components.unifi_insights.const import (
    ACCESS_WEBHOOK_EVENT,
    CONF_ACCESS_PORT,
    CONF_ACCESS_TOKEN,
    CONF_ACCESS_WEBHOOK,
    CONF_ACCESS_WEBHOOK_ENDPOINT_ID,
    CONF_ACCESS_WEBHOOK_ID,
    CONF_ACCESS_WEBHOOK_SECRET,
    DOMAIN,
)
from custom_components.unifi_insights.coordinators.access import (
    access_result_signal,
)

SECRET = "whsec-test-0123456789"  # noqa: S105
WEBHOOK_ID = "hook-test-abc"
ENDPOINT_ID = "endpoint-test-1"
INTERNAL_URL = "http://ha.test.local:8123"


def _sign(body: bytes, *, timestamp: int, secret: str = SECRET) -> str:
    """Build a Signature header the way the console does: HMAC over t.body."""
    digest = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def _unlock_event(
    result: str = "Access Granted",
    door_id: str = "door-1",
    authentication: str = "CALL",
) -> bytes:
    """Return an access.door.unlock body shaped like the verified sample."""
    return json.dumps(
        {
            "event": ACCESS_WEBHOOK_EVENT,
            "event_object_id": door_id,
            "data": {
                "location": {"id": door_id, "location_type": "door", "name": "Front"},
                "device": {"name": "Hub", "mac": "aa:bb:cc:dd:ee:ff"},
                "actor": {"id": "user-9", "name": "Hon***", "type": "user"},
                "object": {
                    "authentication_type": authentication,
                    "authentication_value": "CRED-VALUE-ABC",
                    "policy_id": "",
                    "policy_name": "",
                    "reader_id": "",
                    "result": result,
                },
            },
        }
    ).encode()


def _coordinator(client: MagicMock) -> MagicMock:
    """Return a stand-in coordinator exposing the runtime attributes used here."""
    coordinator = MagicMock()
    coordinator.client = client
    coordinator.async_set_webhook_active = MagicMock()
    coordinator.auth_failed = False
    coordinator.webhook_active = True
    coordinator.data = {"doors": {"door-1": {"id": "door-1"}}}
    return coordinator


def _client(
    *,
    endpoints: list[AccessWebhookEndpoint] | None = None,
    created: AccessWebhookEndpoint | None = None,
) -> MagicMock:
    """Return a client mock whose webhook calls behave like the console."""
    client = MagicMock()
    client.webhooks.get_all = AsyncMock(return_value=endpoints or [])
    client.webhooks.create = AsyncMock(
        return_value=created or AccessWebhookEndpoint(id=ENDPOINT_ID, secret=SECRET)  # type: ignore[arg-type]
    )
    client.webhooks.update = AsyncMock(
        return_value=AccessWebhookEndpoint(id=ENDPOINT_ID, secret=SECRET)  # type: ignore[arg-type]
    )
    client.webhooks.delete = AsyncMock(return_value=None)
    client.close = AsyncMock(return_value=None)
    return client


def _entry(
    hass: Any,
    *,
    enabled: bool = True,
    data: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Add an Access-enabled local entry to hass."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Main Console",
        data={
            CONF_ACCESS_TOKEN: "access-token-value",
            CONF_ACCESS_PORT: 12445,
            CONF_ACCESS_WEBHOOK_ID: WEBHOOK_ID,
            **(data or {}),
        },
        options={CONF_ACCESS_WEBHOOK: enabled},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def internal_url(hass: Any) -> None:
    """Give Home Assistant a URL so the webhook URL can be built."""
    hass.config.internal_url = INTERNAL_URL


@pytest.fixture
async def webhook_setup(hass: Any, internal_url: None) -> tuple[MagicMock, Any, Any]:
    """Enable the receiver through the lifecycle and return client, entry, events."""
    entry = _entry(hass)
    client = _client()
    coordinator = _coordinator(client)
    assert await aw.async_setup_access_webhook(hass, entry, coordinator) is True
    events: list[dict[str, Any]] = []
    async_dispatcher_connect(hass, access_result_signal(entry.entry_id), events.append)
    await async_setup_component(hass, "webhook", {})
    return client, entry, events


# Pure verification ---------------------------------------------------------


def test_parse_signature_header_accepts_first_values() -> None:
    """The first t and v1 win; extra or duplicate fields are ignored."""
    digest = "a" * 64
    timestamp, parsed = aw.parse_signature_header(f"t=1700,v1={digest},v1={'b' * 64}")
    assert (timestamp, parsed) == ("1700", digest)


@pytest.mark.parametrize(
    "header",
    [
        "",
        "garbage",
        "v1=" + "a" * 64,
        "t=abc,v1=" + "a" * 64,
        "t=1234567890123456,v1=" + "a" * 64,
        "t=1,v1=" + "a" * 63,
        "t=1,v1=" + "A" * 64,
        "t=1,v1=" + "g" * 64,
    ],
)
def test_parse_signature_header_rejects_malformed(header: str) -> None:
    """Anything that is not t=<digits>,v1=<64 lowercase hex> is malformed."""
    with pytest.raises(aw.AccessWebhookRejectedError) as err:
        aw.parse_signature_header(header)
    assert err.value.reason == "malformed"
    assert err.value.status == HTTPStatus.BAD_REQUEST


def test_verify_accepts_exact_age_and_future_boundaries() -> None:
    """Age exactly at the limit and skew exactly at the limit are accepted."""
    now = 1_700_000_000.0
    body = b"{}"
    old = int(now) - aw.MAX_SIGNATURE_AGE_SECONDS
    future = int(now) + aw.MAX_FUTURE_SKEW_SECONDS
    assert aw.verify_delivery(_sign(body, timestamp=old), body, SECRET, now=now) == (
        old,
        _sign(body, timestamp=old).split("v1=")[1],
    )
    assert aw.verify_delivery(_sign(body, timestamp=future), body, SECRET, now=now)[
        0
    ] == (future)


@pytest.mark.parametrize(
    ("timestamp_offset", "reason"),
    [
        (-aw.MAX_SIGNATURE_AGE_SECONDS - 1, "stale"),
        (aw.MAX_FUTURE_SKEW_SECONDS + 1, "future"),
    ],
)
def test_verify_rejects_stale_and_future(timestamp_offset: int, reason: str) -> None:
    """Freshness is checked only after the signature is proven authentic."""
    now = 1_700_000_000.0
    body = b"{}"
    timestamp = int(now) + timestamp_offset
    with pytest.raises(aw.AccessWebhookRejectedError) as err:
        aw.verify_delivery(_sign(body, timestamp=timestamp), body, SECRET, now=now)
    assert err.value.reason == reason


def test_verify_rejects_wrong_secret_and_altered_body() -> None:
    """A different secret or a changed body fails the constant-time comparison."""
    now = time.time()
    timestamp = int(now)
    header = _sign(b'{"a":1}', timestamp=timestamp)
    with pytest.raises(aw.AccessWebhookRejectedError) as wrong_secret:
        aw.verify_delivery(header, b'{"a":1}', "other-secret", now=now)
    assert wrong_secret.value.reason == "signature"
    with pytest.raises(aw.AccessWebhookRejectedError) as altered:
        aw.verify_delivery(header, b'{"a":2}', SECRET, now=now)
    assert altered.value.reason == "signature"


def test_replay_cache_refuses_repeat_of_a_live_digest() -> None:
    """A digest is admitted once while it is inside the age window."""
    cache = aw.ReplayCache(size=4)
    assert cache.check("a", 1000, now=1000.0) is None
    assert cache.check("a", 1000, now=1000.0) == "replay"


def test_replay_cache_fails_closed_when_saturated() -> None:
    """A full cache refuses new digests; it never evicts a still-fresh one."""
    cache = aw.ReplayCache(size=2)
    now = 1000.0
    assert cache.check("a", 1000, now=now) is None
    assert cache.check("b", 1000, now=now) is None
    assert cache.check("c", 1000, now=now) == aw.REPLAY_CACHE_FULL
    # "a" was not evicted to make room, so its replay is still refused.
    assert cache.check("a", 1000, now=now) == "replay"


def test_replay_cache_recovers_capacity_only_after_expiry() -> None:
    """Slots free up as digests pass the age window, not before."""
    cache = aw.ReplayCache(size=1)
    assert cache.check("a", 1000, now=1000.0) is None
    still_fresh = 1000.0 + aw.MAX_SIGNATURE_AGE_SECONDS
    assert cache.check("b", 1001, now=still_fresh) == aw.REPLAY_CACHE_FULL
    later = still_fresh + 1
    assert cache.check("b", 1001, now=later) is None
    # "a" was pruned on expiry, so the cache has no record of it; the handler's
    # freshness check (which runs before the cache) is what refuses that replay.
    assert cache.check("a", 1000, now=later) == aw.REPLAY_CACHE_FULL


KNOWN_DOORS = frozenset({"d"})


def _door_event(
    result: Any,
    *,
    location_type: str = "door",
    door: object = "d",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a minimal access.door.unlock event with the given result."""
    outcome: dict[str, Any] = {"result": result, **(extra or {})}
    return {
        "event": ACCESS_WEBHOOK_EVENT,
        "data": {
            "location": {"id": door, "location_type": location_type},
            "object": outcome,
        },
    }


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"data": "not-a-dict"}, None),
        ({"data": {"location": "x", "object": {}}}, None),
        (
            {"data": {"location": {"id": "d", "location_type": "door"}, "object": "x"}},
            None,
        ),
        (_door_event("Access Granted", door=""), None),
        (_door_event(1), None),
        (_door_event("Other"), None),
        (_door_event("Access Granted", location_type="gate"), None),
        (_door_event("Access Granted", door="unknown-door"), None),
        (
            _door_event("Access Granted", extra={"authentication_type": "x" * 65}),
            {"door_id": "d", "result": "granted", "authentication": None},
        ),
        (
            _door_event("BLOCKED"),
            {"door_id": "d", "result": "denied", "authentication": None},
        ),
        (
            _door_event("Access Denied", extra={"authentication_type": "NFC"}),
            {"door_id": "d", "result": "denied", "authentication": "NFC"},
        ),
        (
            _door_event("Access Granted", extra={"authentication_type": 7}),
            {"door_id": "d", "result": "granted", "authentication": None},
        ),
        (
            _door_event("Access Denied", extra={"authentication_type": ["NFC"]}),
            {"door_id": "d", "result": "denied", "authentication": None},
        ),
        ({"data": None}, None),
    ],
)
def test_access_result_payload_keeps_only_permitted_fields(
    body: dict[str, Any], expected: dict[str, Any] | None
) -> None:
    """Only a known door, its result and authentication type leave the parser."""
    assert aw.access_result_payload(body, KNOWN_DOORS) == expected


@pytest.mark.parametrize("door", [1, None, ["d"]])
def test_access_result_payload_rejects_non_string_location_id(door: object) -> None:
    """A non-string location ID is refused, not coerced to a known door's text."""
    # The text forms of these values are all listed as known doors, so only a
    # refusal of the raw non-string value makes the payload None.
    known_doors = frozenset({"d", "1", "None", "['d']"})
    event = _door_event("Access Granted", door=door)
    assert aw.access_result_payload(event, known_doors) is None


def test_access_result_payload_drops_credential_value() -> None:
    """The authentication value and actor never reach the dispatcher payload."""
    payload = aw.access_result_payload(
        json.loads(_unlock_event()), frozenset({"door-1"})
    )
    assert payload == {
        "door_id": "door-1",
        "result": "granted",
        "authentication": "CALL",
    }
    assert "CRED-VALUE-ABC" not in json.dumps(payload)


def _coordinator_state(
    *,
    auth_failed: bool = False,
    webhook_active: bool = True,
    doors: dict[str, Any] | None = None,
) -> MagicMock:
    """Return a coordinator stand-in for the dispatch guard."""
    coordinator = MagicMock()
    coordinator.auth_failed = auth_failed
    coordinator.webhook_active = webhook_active
    coordinator.data = {"doors": doors if doors is not None else {"d": {"id": "d"}}}
    return coordinator


def test_dispatch_is_refused_while_inactive_or_unauthenticated(hass: Any) -> None:
    """Accepted events do not dispatch once the coordinator stops accepting results."""
    events: list[dict[str, Any]] = []

    @callback
    def _record(payload: dict[str, Any]) -> None:
        """Record on the event loop so synchronous assertions cannot race."""
        events.append(payload)

    async_dispatcher_connect(hass, access_result_signal("entry-x"), _record)
    event = _door_event("Access Granted")
    aw._dispatch_verified(
        hass, "entry-x", _coordinator_state(webhook_active=False), event
    )
    aw._dispatch_verified(hass, "entry-x", _coordinator_state(auth_failed=True), event)
    aw._dispatch_verified(hass, "entry-x", _coordinator_state(doors={}), event)
    aw._dispatch_verified(hass, "entry-x", _coordinator_state(), {"event": "other"})
    assert events == []
    aw._dispatch_verified(hass, "entry-x", _coordinator_state(), event)
    assert events == [{"door_id": "d", "result": "granted", "authentication": None}]


def test_parse_event_requires_a_json_object() -> None:
    """Non-JSON and non-object bodies are not events."""
    assert aw._parse_event(b"not json") is None
    assert aw._parse_event(b"[1, 2]") is None
    assert aw._parse_event(b'{"event": "x"}') == {"event": "x"}


# Receiver over hass_client -------------------------------------------------


async def test_valid_granted_delivery_dispatches_permitted_fields(
    hass: Any, hass_client: Any, webhook_setup: tuple[MagicMock, Any, Any]
) -> None:
    """A signed, fresh granted result is accepted and dispatched once."""
    _, _, events = webhook_setup
    body = _unlock_event()
    client = await hass_client()
    response = await client.post(
        webhook.async_generate_path(WEBHOOK_ID),
        data=body,
        headers={"Signature": _sign(body, timestamp=int(time.time()))},
    )
    await hass.async_block_till_done()
    assert response.status == HTTPStatus.OK
    assert events == [
        {"door_id": "door-1", "result": "granted", "authentication": "CALL"}
    ]


async def test_denied_result_is_dispatched_as_denied(
    hass: Any, hass_client: Any, webhook_setup: tuple[MagicMock, Any, Any]
) -> None:
    """The BLOCKED result maps to a denied dispatch."""
    _, _, events = webhook_setup
    body = _unlock_event(result="BLOCKED", authentication="NFC")
    client = await hass_client()
    response = await client.post(
        webhook.async_generate_path(WEBHOOK_ID),
        data=body,
        headers={"Signature": _sign(body, timestamp=int(time.time()))},
    )
    await hass.async_block_till_done()
    assert response.status == HTTPStatus.OK
    assert events == [
        {"door_id": "door-1", "result": "denied", "authentication": "NFC"}
    ]


@pytest.mark.parametrize(
    ("body", "header_factory", "status", "reason"),
    [
        (
            _unlock_event(),
            lambda body: _sign(
                body,
                timestamp=int(time.time()),
                secret="wrong",  # noqa: S106
            ),
            HTTPStatus.UNAUTHORIZED,
            "signature",
        ),
        (
            _unlock_event(),
            lambda body: _sign(body, timestamp=int(time.time()) - 400),
            HTTPStatus.UNAUTHORIZED,
            "stale",
        ),
        (
            _unlock_event(),
            lambda body: _sign(body, timestamp=int(time.time()) + 300),
            HTTPStatus.UNAUTHORIZED,
            "future",
        ),
        (_unlock_event(), lambda body: "garbage", HTTPStatus.BAD_REQUEST, "malformed"),
    ],
)
async def test_bad_deliveries_are_refused_without_dispatch(
    hass: Any,
    hass_client: Any,
    webhook_setup: tuple[MagicMock, Any, Any],
    caplog: pytest.LogCaptureFixture,
    *,
    body: bytes,
    header_factory: Any,
    status: HTTPStatus,
    reason: str,
) -> None:
    """Bad signatures, stale and future timestamps and malformed headers are refused."""
    _, _, events = webhook_setup
    client = await hass_client()
    with caplog.at_level(logging.WARNING):
        response = await client.post(
            webhook.async_generate_path(WEBHOOK_ID),
            data=body,
            headers={"Signature": header_factory(body)},
        )
    await hass.async_block_till_done()
    assert response.status == status
    assert events == []
    assert f"({reason})" in caplog.text
    assert SECRET not in caplog.text
    assert "CRED-VALUE-ABC" not in caplog.text


async def test_replayed_delivery_is_refused_on_second_arrival(
    hass: Any, hass_client: Any, webhook_setup: tuple[MagicMock, Any, Any]
) -> None:
    """The same signed delivery is accepted once and refused the second time."""
    _, _, events = webhook_setup
    body = _unlock_event()
    header = _sign(body, timestamp=int(time.time()))
    client = await hass_client()
    path = webhook.async_generate_path(WEBHOOK_ID)
    first = await client.post(path, data=body, headers={"Signature": header})
    second = await client.post(path, data=body, headers={"Signature": header})
    await hass.async_block_till_done()
    assert first.status == HTTPStatus.OK
    assert second.status == HTTPStatus.UNAUTHORIZED
    assert len(events) == 1


async def test_non_post_methods_are_not_accepted(
    hass: Any, hass_client: Any, webhook_setup: tuple[MagicMock, Any, Any]
) -> None:
    """Only POST runs the receiver; GET is refused and HEAD only probes existence."""
    _, _, events = webhook_setup
    client = await hass_client()
    path = webhook.async_generate_path(WEBHOOK_ID)
    get_response = await client.get(path)
    head_response = await client.head(path)
    await hass.async_block_till_done()
    assert get_response.status == HTTPStatus.METHOD_NOT_ALLOWED
    assert head_response.status == HTTPStatus.OK
    assert events == []


async def test_declared_oversized_body_is_refused(
    hass: Any, hass_client: Any, webhook_setup: tuple[MagicMock, Any, Any]
) -> None:
    """A Content-Length above the bound is refused before any reading."""
    _, _, events = webhook_setup
    body = b"x" * (aw.MAX_BODY_BYTES + 1)
    client = await hass_client()
    response = await client.post(
        webhook.async_generate_path(WEBHOOK_ID),
        data=body,
        headers={"Signature": _sign(body, timestamp=int(time.time()))},
    )
    assert response.status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert events == []


async def test_chunked_oversized_body_is_refused(
    hass: Any, hass_client: Any, webhook_setup: tuple[MagicMock, Any, Any]
) -> None:
    """A body without Content-Length is still bounded while it is read."""
    _, _, events = webhook_setup

    async def chunks() -> Any:
        for _ in range(3):
            yield b"y" * (aw.MAX_BODY_BYTES // 2 + 1)

    client = await hass_client()
    response = await client.post(
        webhook.async_generate_path(WEBHOOK_ID),
        data=chunks(),
        headers={"Signature": "t=1,v1=" + "a" * 64},
    )
    assert response.status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert events == []


async def test_signed_non_json_body_is_malformed(
    hass: Any, hass_client: Any, webhook_setup: tuple[MagicMock, Any, Any]
) -> None:
    """A correctly signed body that is not a JSON object is refused as malformed."""
    _, _, events = webhook_setup
    body = b"[not-an-object"
    client = await hass_client()
    response = await client.post(
        webhook.async_generate_path(WEBHOOK_ID),
        data=body,
        headers={"Signature": _sign(body, timestamp=int(time.time()))},
    )
    await hass.async_block_till_done()
    assert response.status == HTTPStatus.BAD_REQUEST
    assert events == []


async def test_other_events_and_unknown_results_do_not_dispatch(
    hass: Any, hass_client: Any, webhook_setup: tuple[MagicMock, Any, Any]
) -> None:
    """Signed deliveries that are not a known result are answered but not emitted."""
    _, _, events = webhook_setup
    client = await hass_client()
    path = webhook.async_generate_path(WEBHOOK_ID)
    other = json.dumps({"event": "access.doorbell.incoming", "data": {}}).encode()
    unknown = _unlock_event(result="Something New")
    no_location = json.dumps(
        {"event": ACCESS_WEBHOOK_EVENT, "data": {"object": {"result": "BLOCKED"}}}
    ).encode()
    for body in (other, unknown, no_location):
        response = await client.post(
            path,
            data=body,
            headers={"Signature": _sign(body, timestamp=int(time.time()))},
        )
        assert response.status == HTTPStatus.OK
    await hass.async_block_till_done()
    assert events == []


async def test_missing_secret_refuses_every_delivery(
    hass: Any, hass_client: Any, webhook_setup: tuple[MagicMock, Any, Any]
) -> None:
    """If the stored secret disappears, nothing is accepted."""
    _, entry, events = webhook_setup
    hass.config_entries.async_update_entry(
        entry,
        data={k: v for k, v in entry.data.items() if k != CONF_ACCESS_WEBHOOK_SECRET},
    )
    body = _unlock_event()
    client = await hass_client()
    response = await client.post(
        webhook.async_generate_path(WEBHOOK_ID),
        data=body,
        headers={"Signature": _sign(body, timestamp=int(time.time()))},
    )
    await hass.async_block_till_done()
    assert response.status == HTTPStatus.UNAUTHORIZED
    assert events == []


async def test_header_with_extra_fields_still_verifies(
    hass: Any, hass_client: Any, webhook_setup: tuple[MagicMock, Any, Any]
) -> None:
    """Unknown header fields do not break a valid signature."""
    _, _, events = webhook_setup
    body = _unlock_event()
    header = _sign(body, timestamp=int(time.time())) + ",v0=ignored"
    client = await hass_client()
    response = await client.post(
        webhook.async_generate_path(WEBHOOK_ID),
        data=body,
        headers={"Signature": header},
    )
    await hass.async_block_till_done()
    assert response.status == HTTPStatus.OK
    assert len(events) == 1


async def test_saturated_replay_cache_refuses_fresh_delivery_with_503(
    hass: Any, hass_client: Any, webhook_setup: tuple[MagicMock, Any, Any]
) -> None:
    """A full cache refuses a new authentic delivery rather than evicting a digest."""
    _, _, events = webhook_setup
    client = await hass_client()
    path = webhook.async_generate_path(WEBHOOK_ID)
    # Fill the cache to its bound with distinct, fresh, authentic deliveries.
    first_body = _unlock_event(door_id="door-0")
    first_header = _sign(first_body, timestamp=int(time.time()))
    response = await client.post(
        path, data=first_body, headers={"Signature": first_header}
    )
    assert response.status == HTTPStatus.OK
    for index in range(1, aw.REPLAY_CACHE_SIZE):
        body = _unlock_event(door_id=f"door-{index}")
        response = await client.post(
            path,
            data=body,
            headers={"Signature": _sign(body, timestamp=int(time.time()))},
        )
        assert response.status == HTTPStatus.OK
    fresh_body = _unlock_event(door_id="door-fresh", authentication="NFC")
    response = await client.post(
        path,
        data=fresh_body,
        headers={"Signature": _sign(fresh_body, timestamp=int(time.time()))},
    )
    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    # The first delivery is still refused as a replay; its digest was not evicted.
    replay = await client.post(
        path, data=first_body, headers={"Signature": first_header}
    )
    assert replay.status == HTTPStatus.UNAUTHORIZED
    await hass.async_block_till_done()
    # Only door-1 is known to the coordinator, so only its delivery dispatched.
    assert len(events) == 1


async def test_slow_body_is_cut_off_with_request_timeout(
    hass: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A body that trickles past the read deadline gets 408 and is never parsed."""
    entry = _entry(hass)
    handler = aw._make_handler(entry, _coordinator(_client()))

    async def slow_chunks(_size: int) -> Any:
        yield b"{"
        await asyncio.sleep(10)
        yield b"}"

    request = MagicMock()
    request.content_length = None
    request.content.iter_chunked = slow_chunks
    request.headers = {}
    with (
        patch.object(aw, "BODY_READ_TIMEOUT_SECONDS", 0.05),
        caplog.at_level(logging.WARNING),
    ):
        response = await handler(hass, WEBHOOK_ID, request)
    assert response is not None
    assert response.status == HTTPStatus.REQUEST_TIMEOUT
    assert "(timeout)" in caplog.text


async def test_access_denied_string_is_dispatched_as_denied(
    hass: Any, hass_client: Any, webhook_setup: tuple[MagicMock, Any, Any]
) -> None:
    """The activity-log denied display string is accepted as a denied result."""
    _, _, events = webhook_setup
    body = _unlock_event(result="Access Denied", authentication="NFC")
    client = await hass_client()
    response = await client.post(
        webhook.async_generate_path(WEBHOOK_ID),
        data=body,
        headers={"Signature": _sign(body, timestamp=int(time.time()))},
    )
    await hass.async_block_till_done()
    assert response.status == HTTPStatus.OK
    assert events == [
        {"door_id": "door-1", "result": "denied", "authentication": "NFC"}
    ]


async def test_unknown_door_and_foreign_location_are_not_dispatched(
    hass: Any, hass_client: Any, webhook_setup: tuple[MagicMock, Any, Any]
) -> None:
    """Results for unknown doors or non-door locations are answered, not emitted."""
    _, _, events = webhook_setup
    client = await hass_client()
    path = webhook.async_generate_path(WEBHOOK_ID)
    foreign = json.loads(_unlock_event())
    foreign["data"]["location"]["location_type"] = "gate"
    unknown_door = _unlock_event(door_id="door-missing")
    for body in (json.dumps(foreign).encode(), unknown_door):
        response = await client.post(
            path,
            data=body,
            headers={"Signature": _sign(body, timestamp=int(time.time()))},
        )
        assert response.status == HTTPStatus.OK
    await hass.async_block_till_done()
    assert events == []


async def test_replay_is_refused_after_reload_of_the_same_entry(
    hass: Any, hass_client: Any, internal_url: None
) -> None:
    """A captured signed delivery is accepted once; a reload does not reset replay."""
    entry = _entry(hass)
    assert await aw.async_setup_access_webhook(hass, entry, _coordinator(_client()))
    events: list[dict[str, Any]] = []
    async_dispatcher_connect(hass, access_result_signal(entry.entry_id), events.append)
    await async_setup_component(hass, "webhook", {})
    body = _unlock_event()
    header = _sign(body, timestamp=int(time.time()))
    path = webhook.async_generate_path(WEBHOOK_ID)
    http = await hass_client()
    first = await http.post(path, data=body, headers={"Signature": header})
    assert first.status == HTTPStatus.OK

    # Reload: unload the receiver, then set up again with the same entry object
    # and a new coordinator, as a real reload does.
    await aw.async_unload_access_webhook(hass, entry)
    assert await aw.async_setup_access_webhook(hass, entry, _coordinator(_client()))
    replay = await http.post(path, data=body, headers={"Signature": header})
    await hass.async_block_till_done()
    assert replay.status == HTTPStatus.UNAUTHORIZED
    assert len(events) == 1


async def test_fresh_entry_object_starts_with_an_empty_cache(
    hass: Any, hass_client: Any, internal_url: None
) -> None:
    """Documented limit: a new entry object (a restart) has no replay history."""
    body = _unlock_event()
    header = _sign(body, timestamp=int(time.time()))
    path = webhook.async_generate_path(WEBHOOK_ID)
    entry = _entry(hass)
    assert await aw.async_setup_access_webhook(hass, entry, _coordinator(_client()))
    await async_setup_component(hass, "webhook", {})
    http = await hass_client()
    assert (
        await http.post(path, data=body, headers={"Signature": header})
    ).status == HTTPStatus.OK
    await aw.async_unload_access_webhook(hass, entry)
    restarted = _entry(hass)
    assert await aw.async_setup_access_webhook(hass, restarted, _coordinator(_client()))
    accepted = await http.post(path, data=body, headers={"Signature": header})
    assert accepted.status == HTTPStatus.OK


async def test_inactive_coordinator_refuses_dispatch(
    hass: Any, hass_client: Any, internal_url: None
) -> None:
    """A verified delivery is not emitted once the coordinator stops accepting."""
    entry = _entry(hass)
    coordinator = _coordinator(_client())
    assert await aw.async_setup_access_webhook(hass, entry, coordinator) is True
    events: list[dict[str, Any]] = []
    async_dispatcher_connect(hass, access_result_signal(entry.entry_id), events.append)
    await async_setup_component(hass, "webhook", {})
    # The handler holds this same coordinator object, so flipping it is live.
    coordinator.webhook_active = False
    body = _unlock_event()
    http = await hass_client()
    response = await http.post(
        webhook.async_generate_path(WEBHOOK_ID),
        data=body,
        headers={"Signature": _sign(body, timestamp=int(time.time()))},
    )
    await hass.async_block_till_done()
    assert response.status == HTTPStatus.OK
    assert events == []


async def test_unload_marks_reachable_coordinator_inactive(hass: Any) -> None:
    """Unload stops dispatch even for a delivery still reading when it happened."""
    entry = _entry(hass)
    coordinator = _coordinator(_client())
    entry.runtime_data = SimpleNamespace(access_coordinator=coordinator)
    await aw.async_unload_access_webhook(hass, entry)
    coordinator.async_set_webhook_active.assert_called_with(active=False)


async def test_update_404_recreates_the_endpoint(hass: Any, internal_url: None) -> None:
    """A console that forgets the endpoint mid-update gets it created again."""
    entry = _entry(
        hass,
        data={
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
            CONF_ACCESS_WEBHOOK_SECRET: SECRET,
        },
    )
    client = _client(
        endpoints=[AccessWebhookEndpoint(id=ENDPOINT_ID, endpoint="http://old")]
    )
    client.webhooks.update = AsyncMock(
        side_effect=UniFiResponseError("gone", status_code=404)
    )
    assert (
        await aw.async_setup_access_webhook(hass, entry, _coordinator(client)) is True
    )
    client.webhooks.create.assert_awaited_once()
    assert entry.data[CONF_ACCESS_WEBHOOK_ENDPOINT_ID] == ENDPOINT_ID


async def test_update_other_failure_is_not_recreated(
    hass: Any, internal_url: None
) -> None:
    """Only a 404 on update triggers a create; other failures keep the skip path."""
    entry = _entry(
        hass,
        data={
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
            CONF_ACCESS_WEBHOOK_SECRET: SECRET,
        },
    )
    client = _client(
        endpoints=[AccessWebhookEndpoint(id=ENDPOINT_ID, endpoint="http://old")]
    )
    client.webhooks.update = AsyncMock(
        side_effect=UniFiResponseError("boom", status_code=500)
    )
    assert (
        await aw.async_setup_access_webhook(hass, entry, _coordinator(client)) is False
    )
    client.webhooks.create.assert_not_called()


# Lifecycle -----------------------------------------------------------------


async def test_enable_creates_endpoint_and_stores_state(
    hass: Any, internal_url: None
) -> None:
    """A first enable creates the endpoint, stores its ID and secret and activates."""
    entry = _entry(hass, data={CONF_ACCESS_WEBHOOK_ID: ""})
    client = _client()
    coordinator = _coordinator(client)
    assert await aw.async_setup_access_webhook(hass, entry, coordinator) is True
    create_args = client.webhooks.create.await_args.kwargs
    assert create_args["events"] == [ACCESS_WEBHOOK_EVENT]
    assert create_args["endpoint"].startswith(INTERNAL_URL + "/api/webhook/")
    stored_id = entry.data[CONF_ACCESS_WEBHOOK_ID]
    assert stored_id
    assert create_args["endpoint"].endswith(stored_id)
    assert entry.data[CONF_ACCESS_WEBHOOK_ENDPOINT_ID] == ENDPOINT_ID
    assert entry.data[CONF_ACCESS_WEBHOOK_SECRET] == SECRET
    coordinator.async_set_webhook_active.assert_called_with(active=True)


async def test_reload_reuses_stored_endpoint_and_webhook_id(
    hass: Any, internal_url: None
) -> None:
    """A reload updates the stored endpoint rather than creating a second one."""
    entry = _entry(
        hass,
        data={
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
            CONF_ACCESS_WEBHOOK_SECRET: SECRET,
        },
    )
    client = _client(
        endpoints=[AccessWebhookEndpoint(id=ENDPOINT_ID, endpoint="http://old")],
    )
    assert await aw.async_setup_access_webhook(hass, entry, _coordinator(client))
    client.webhooks.create.assert_not_called()
    update_args = client.webhooks.update.await_args
    assert update_args.args == (ENDPOINT_ID,)
    assert entry.data[CONF_ACCESS_WEBHOOK_ID] == WEBHOOK_ID


async def test_existing_endpoint_for_same_url_is_adopted(
    hass: Any, internal_url: None
) -> None:
    """With lost state, an endpoint already pointing at this URL is reused."""
    url = f"{INTERNAL_URL}{webhook.async_generate_path(WEBHOOK_ID)}"
    entry = _entry(hass)
    client = _client(
        endpoints=[
            AccessWebhookEndpoint(id="other", endpoint="http://elsewhere"),
            AccessWebhookEndpoint(id="adopt-me", endpoint=url),
        ],
    )
    assert await aw.async_setup_access_webhook(hass, entry, _coordinator(client))
    assert client.webhooks.update.await_args.args == ("adopt-me",)
    client.webhooks.create.assert_not_called()


async def test_missing_secret_cleans_up_and_skips(
    hass: Any, internal_url: None
) -> None:
    """An endpoint without a secret is deleted and the receiver stays off."""
    entry = _entry(hass)
    client = _client(created=AccessWebhookEndpoint(id=ENDPOINT_ID))
    coordinator = _coordinator(client)
    assert await aw.async_setup_access_webhook(hass, entry, coordinator) is False
    client.webhooks.delete.assert_awaited_once_with(ENDPOINT_ID)
    assert CONF_ACCESS_WEBHOOK_ENDPOINT_ID not in entry.data
    assert CONF_ACCESS_WEBHOOK_SECRET not in entry.data
    coordinator.async_set_webhook_active.assert_called_with(active=False)


async def test_missing_id_skips_without_delete(hass: Any, internal_url: None) -> None:
    """An endpoint with no ID cannot be deleted; the receiver is skipped."""
    entry = _entry(hass)
    client = _client(created=AccessWebhookEndpoint(secret=SECRET))  # type: ignore[arg-type]
    assert (
        await aw.async_setup_access_webhook(hass, entry, _coordinator(client)) is False
    )
    client.webhooks.delete.assert_not_called()


async def test_endpoint_without_secret_value_is_skipped(
    hass: Any, internal_url: None
) -> None:
    """A response with an ID but no secret value counts as no secret."""
    entry = _entry(hass)
    client = _client(created=AccessWebhookEndpoint(id=ENDPOINT_ID, secret=""))  # type: ignore[arg-type]
    assert (
        await aw.async_setup_access_webhook(hass, entry, _coordinator(client)) is False
    )
    client.webhooks.delete.assert_awaited_once_with(ENDPOINT_ID)


async def test_console_without_webhook_api_is_reported_not_fatal(
    hass: Any, internal_url: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A 404 from the webhook API means the console predates 2.2.10; Access stays up."""
    entry = _entry(hass)
    client = _client()
    client.webhooks.get_all = AsyncMock(
        side_effect=UniFiResponseError("absent", status_code=404)
    )
    coordinator = _coordinator(client)
    with caplog.at_level(logging.WARNING):
        assert await aw.async_setup_access_webhook(hass, entry, coordinator) is False
    assert "2.2.10" in caplog.text
    coordinator.async_set_webhook_active.assert_called_with(active=False)


async def test_other_api_failures_skip_without_losing_state(
    hass: Any, internal_url: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A transient failure is logged by category and keeps stored IDs for retry."""
    entry = _entry(
        hass,
        data={
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
            CONF_ACCESS_WEBHOOK_SECRET: SECRET,
        },
    )
    client = _client()
    client.webhooks.get_all = AsyncMock(side_effect=UniFiConnectionError("down"))
    with caplog.at_level(logging.WARNING):
        assert (
            await aw.async_setup_access_webhook(hass, entry, _coordinator(client))
            is False
        )
    assert "UniFiConnectionError" in caplog.text
    assert SECRET not in caplog.text
    assert entry.data[CONF_ACCESS_WEBHOOK_ENDPOINT_ID] == ENDPOINT_ID


async def test_server_error_response_is_described_by_status_only(
    hass: Any, internal_url: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-404 response error logs its class and status, never its body."""
    entry = _entry(hass)
    client = _client()
    client.webhooks.get_all = AsyncMock(
        side_effect=UniFiResponseError("boom", status_code=500, response_body="x")
    )
    with caplog.at_level(logging.WARNING):
        assert (
            await aw.async_setup_access_webhook(hass, entry, _coordinator(client))
            is False
        )
    assert "status 500" in caplog.text


async def test_missing_url_skips_without_calling_console(
    hass: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """With no Home Assistant URL the console is never asked to register."""
    entry = _entry(hass)
    client = _client()
    with (
        patch.object(
            aw.webhook,
            "async_generate_url",
            side_effect=NoURLAvailableError,
        ),
        caplog.at_level(logging.WARNING),
    ):
        assert (
            await aw.async_setup_access_webhook(hass, entry, _coordinator(client))
            is False
        )
    client.webhooks.get_all.assert_not_called()
    assert "no URL" in caplog.text


async def test_external_only_url_is_skipped(
    hass: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """With only an external URL the receiver would refuse the console, so it skips."""
    hass.config.internal_url = None
    hass.config.external_url = "https://ha.example.invalid"
    entry = _entry(hass)
    client = _client()
    coordinator = _coordinator(client)
    with caplog.at_level(logging.WARNING):
        assert await aw.async_setup_access_webhook(hass, entry, coordinator) is False
    client.webhooks.get_all.assert_not_called()
    coordinator.async_set_webhook_active.assert_called_with(active=False)
    assert "no URL" in caplog.text


async def test_internal_url_is_preferred_over_external(
    hass: Any, internal_url: None
) -> None:
    """With both URLs configured the endpoint points at the internal one."""
    hass.config.external_url = "https://ha.example.invalid"
    entry = _entry(hass)
    client = _client()
    assert (
        await aw.async_setup_access_webhook(hass, entry, _coordinator(client)) is True
    )
    endpoint = client.webhooks.create.await_args.kwargs["endpoint"]
    assert endpoint.startswith(INTERNAL_URL + "/api/webhook/")


async def test_disable_deletes_remote_endpoint_and_clears_state(
    hass: Any, internal_url: None
) -> None:
    """Turning the option off deletes the endpoint and forgets its secret."""
    entry = _entry(
        hass,
        enabled=False,
        data={
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
            CONF_ACCESS_WEBHOOK_SECRET: SECRET,
        },
    )
    client = _client()
    coordinator = _coordinator(client)
    assert await aw.async_setup_access_webhook(hass, entry, coordinator) is False
    client.webhooks.delete.assert_awaited_once_with(ENDPOINT_ID)
    assert CONF_ACCESS_WEBHOOK_SECRET not in entry.data
    coordinator.async_set_webhook_active.assert_called_with(active=False)


async def test_disable_keeps_state_when_delete_fails(
    hass: Any, internal_url: None
) -> None:
    """A failed delete keeps the endpoint ID so a later removal can retry it."""
    entry = _entry(
        hass,
        enabled=False,
        data={
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
            CONF_ACCESS_WEBHOOK_SECRET: SECRET,
        },
    )
    client = _client()
    client.webhooks.delete = AsyncMock(side_effect=UniFiConnectionError("down"))
    assert (
        await aw.async_setup_access_webhook(hass, entry, _coordinator(client)) is False
    )
    assert entry.data[CONF_ACCESS_WEBHOOK_ENDPOINT_ID] == ENDPOINT_ID
    assert entry.data[CONF_ACCESS_WEBHOOK_SECRET] == SECRET


async def test_disable_treats_already_deleted_endpoint_as_done(
    hass: Any, internal_url: None
) -> None:
    """A 404 on delete means the endpoint is already gone, so state is cleared."""
    entry = _entry(
        hass,
        enabled=False,
        data={
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
            CONF_ACCESS_WEBHOOK_SECRET: SECRET,
        },
    )
    client = _client()
    client.webhooks.delete = AsyncMock(
        side_effect=UniFiResponseError("gone", status_code=404)
    )
    assert (
        await aw.async_setup_access_webhook(hass, entry, _coordinator(client)) is False
    )
    assert CONF_ACCESS_WEBHOOK_ENDPOINT_ID not in entry.data


async def test_typed_not_found_counts_as_already_deleted(
    hass: Any, internal_url: None
) -> None:
    """A not-found error from the client clears state, even without a 404 status."""
    entry = _entry(
        hass,
        enabled=False,
        data={
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
            CONF_ACCESS_WEBHOOK_SECRET: SECRET,
        },
    )
    client = _client()
    client.webhooks.delete = AsyncMock(
        side_effect=UniFiNotFoundError("gone", status_code=200)
    )
    assert (
        await aw.async_setup_access_webhook(hass, entry, _coordinator(client)) is False
    )
    assert CONF_ACCESS_WEBHOOK_ENDPOINT_ID not in entry.data


async def test_disable_without_endpoint_makes_no_remote_call(
    hass: Any, internal_url: None
) -> None:
    """With nothing stored, turning the option off contacts the console not at all."""
    entry = _entry(hass, enabled=False)
    client = _client()
    assert (
        await aw.async_setup_access_webhook(hass, entry, _coordinator(client)) is False
    )
    client.webhooks.delete.assert_not_called()


async def test_unload_unregisters_receiver_only_and_is_idempotent(
    hass: Any, hass_client: Any, internal_url: None
) -> None:
    """Unload stops delivery to the handler and never touches the remote endpoint."""
    entry = _entry(hass)
    client = _client()
    await async_setup_component(hass, "webhook", {})
    assert await aw.async_setup_access_webhook(hass, entry, _coordinator(client))
    await aw.async_unload_access_webhook(hass, entry)
    await aw.async_unload_access_webhook(hass, entry)
    client.webhooks.delete.assert_not_called()
    events: list[dict[str, Any]] = []
    async_dispatcher_connect(hass, access_result_signal(entry.entry_id), events.append)
    body = _unlock_event()
    http = await hass_client()
    response = await http.post(
        webhook.async_generate_path(WEBHOOK_ID),
        data=body,
        headers={"Signature": _sign(body, timestamp=int(time.time()))},
    )
    await hass.async_block_till_done()
    assert response.status == HTTPStatus.OK
    assert events == []


async def test_unload_without_stored_webhook_is_noop(hass: Any) -> None:
    """Unload is safe for an entry that never enabled the receiver."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ACCESS_TOKEN: "t"})
    entry.add_to_hass(hass)
    await aw.async_unload_access_webhook(hass, entry)


async def test_remove_deletes_remote_endpoint_without_coordinator(
    hass: Any,
) -> None:
    """Removal works with no loaded coordinator, using stored settings."""
    entry = _entry(
        hass,
        data={
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
            CONF_ACCESS_WEBHOOK_SECRET: SECRET,
        },
    )
    client = _client()
    with patch.object(aw, "UniFiAccessClient", return_value=client) as factory:
        await aw.async_remove_access_webhook(hass, entry)
    factory.assert_called_once()
    assert factory.call_args.kwargs["token"] == "access-token-value"  # noqa: S105
    assert factory.call_args.kwargs["port"] == 12445
    client.webhooks.delete.assert_awaited_once_with(ENDPOINT_ID)
    client.close.assert_awaited_once()


async def test_remove_without_endpoint_builds_no_client(hass: Any) -> None:
    """With no stored endpoint there is nothing to delete remotely."""
    entry = _entry(hass)
    with patch.object(aw, "UniFiAccessClient") as factory:
        await aw.async_remove_access_webhook(hass, entry)
    factory.assert_not_called()


async def test_remove_without_token_warns_and_leaves_endpoint(
    hass: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Without a stored token the console endpoint is left for manual cleanup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
            CONF_ACCESS_WEBHOOK_SECRET: SECRET,
        },
    )
    entry.add_to_hass(hass)
    with caplog.at_level(logging.WARNING):
        await aw.async_remove_access_webhook(hass, entry)
    assert "no usable stored settings" in caplog.text
    assert SECRET not in caplog.text


async def test_remove_with_invalid_stored_host_warns(
    hass: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """A stored host that cannot form a URL is reported and no call is made."""
    entry = _entry(
        hass,
        data={
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
            CONF_ACCESS_WEBHOOK_SECRET: SECRET,
            "host": "",
        },
    )
    with caplog.at_level(logging.WARNING):
        await aw.async_remove_access_webhook(hass, entry)
    assert "no usable stored settings" in caplog.text


async def test_remove_logs_refused_delete_by_status_only(
    hass: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """A refused delete is reported with its status, never its response body."""
    entry = _entry(
        hass,
        data={
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
            CONF_ACCESS_WEBHOOK_SECRET: SECRET,
        },
    )
    client = _client()
    client.webhooks.delete = AsyncMock(
        side_effect=UniFiResponseError(
            "refused", status_code=500, response_body="body-x"
        )
    )
    with (
        patch.object(aw, "UniFiAccessClient", return_value=client),
        caplog.at_level(logging.WARNING),
    ):
        await aw.async_remove_access_webhook(hass, entry)
    assert "status 500" in caplog.text
    assert "body-x" not in caplog.text


async def test_remove_is_bounded_when_console_is_offline(
    hass: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """An offline console cannot block removal: the delete is cut off and logged."""
    entry = _entry(
        hass,
        data={
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
            CONF_ACCESS_WEBHOOK_SECRET: SECRET,
        },
    )
    client = _client()

    async def hang(_endpoint_id: str) -> None:
        await asyncio.sleep(10)

    client.webhooks.delete = AsyncMock(side_effect=hang)
    with (
        patch.object(aw, "UniFiAccessClient", return_value=client),
        patch.object(aw, "REMOVE_DELETE_TIMEOUT_SECONDS", 0.05),
        caplog.at_level(logging.WARNING),
    ):
        await aw.async_remove_access_webhook(hass, entry)
    assert "Timed out" in caplog.text
    client.close.assert_awaited_once()


async def test_remove_survives_delete_failure(
    hass: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed best-effort delete is logged and never raised during removal."""
    entry = _entry(
        hass,
        data={
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID: ENDPOINT_ID,
            CONF_ACCESS_WEBHOOK_SECRET: SECRET,
        },
    )
    client = _client()
    client.webhooks.delete = AsyncMock(side_effect=UniFiValidationError("bad"))
    with (
        patch.object(aw, "UniFiAccessClient", return_value=client),
        caplog.at_level(logging.WARNING),
    ):
        assert await aw.async_remove_access_webhook(hass, entry) is False
    assert "Could not delete" in caplog.text
    client.close.assert_awaited_once()


async def _receiver_accepts(hass: Any, hass_client: Any, events: list[Any]) -> bool:
    """Post a fresh signed grant and report whether the receiver dispatched it."""
    events.clear()
    body = _unlock_event()
    http = await hass_client()
    await http.post(
        webhook.async_generate_path(WEBHOOK_ID),
        data=body,
        headers={"Signature": _sign(body, timestamp=int(time.time()))},
    )
    await hass.async_block_till_done()
    return bool(events)


def _stored_receiver(
    webhook_setup: tuple[MagicMock, Any, Any],
) -> tuple[Any, list[Any]]:
    """Return the entry and events of an enabled receiver with a stored endpoint."""
    _, entry, events = webhook_setup
    assert entry.data[CONF_ACCESS_WEBHOOK_ENDPOINT_ID] == ENDPOINT_ID
    return entry, events


@pytest.mark.parametrize(
    "delete_error",
    [
        None,
        UniFiNotFoundError("gone", status_code=404),
        UniFiResponseError("gone", status_code=404),
    ],
)
async def test_keep_receiver_retires_receiver_after_confirmed_delete(
    hass: Any,
    hass_client: Any,
    webhook_setup: tuple[MagicMock, Any, Any],
    delete_error: Exception | None,
) -> None:
    """Deleted or already-missing endpoints return True and drop the receiver."""
    entry, events = _stored_receiver(webhook_setup)
    assert await _receiver_accepts(hass, hass_client, events) is True
    client = _client()
    client.webhooks.delete = AsyncMock(side_effect=delete_error)
    with patch.object(aw, "UniFiAccessClient", return_value=client):
        result = await aw.async_remove_access_webhook(
            hass, entry, keep_receiver_on_failure=True
        )
    assert result is True
    client.webhooks.delete.assert_awaited_once_with(ENDPOINT_ID)
    client.close.assert_awaited_once()
    assert await _receiver_accepts(hass, hass_client, events) is False


async def test_keep_receiver_keeps_registration_when_delete_is_refused(
    hass: Any,
    hass_client: Any,
    webhook_setup: tuple[MagicMock, Any, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A refused delete keeps the receiver, the entry data and the secret."""
    entry, events = _stored_receiver(webhook_setup)
    before = dict(entry.data)
    client = _client()
    client.webhooks.delete = AsyncMock(
        side_effect=UniFiResponseError("refused", status_code=500)
    )
    with (
        patch.object(aw, "UniFiAccessClient", return_value=client),
        caplog.at_level(logging.WARNING),
    ):
        result = await aw.async_remove_access_webhook(
            hass, entry, keep_receiver_on_failure=True
        )
    assert result is False
    assert dict(entry.data) == before
    assert await _receiver_accepts(hass, hass_client, events) is True
    client.close.assert_awaited_once()
    assert "status 500" in caplog.text
    assert ENDPOINT_ID not in caplog.text
    assert SECRET not in caplog.text


async def test_keep_receiver_keeps_registration_when_console_times_out(
    hass: Any,
    hass_client: Any,
    webhook_setup: tuple[MagicMock, Any, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A timed-out delete keeps the receiver and still closes the client."""
    entry, events = _stored_receiver(webhook_setup)
    before = dict(entry.data)
    client = _client()

    async def hang(_endpoint_id: str) -> None:
        await asyncio.sleep(10)

    client.webhooks.delete = AsyncMock(side_effect=hang)
    with (
        patch.object(aw, "UniFiAccessClient", return_value=client),
        patch.object(aw, "REMOVE_DELETE_TIMEOUT_SECONDS", 0.05),
        caplog.at_level(logging.WARNING),
    ):
        result = await aw.async_remove_access_webhook(
            hass, entry, keep_receiver_on_failure=True
        )
    assert result is False
    assert dict(entry.data) == before
    assert await _receiver_accepts(hass, hass_client, events) is True
    client.close.assert_awaited_once()
    assert "Timed out" in caplog.text


async def test_keep_receiver_without_usable_client_keeps_registration(
    hass: Any,
    hass_client: Any,
    webhook_setup: tuple[MagicMock, Any, Any],
) -> None:
    """An endpoint that cannot be reached without a token is never claimed deleted."""
    entry, events = _stored_receiver(webhook_setup)
    data = dict(entry.data)
    data.pop(CONF_ACCESS_TOKEN)
    hass.config_entries.async_update_entry(entry, data=data)
    with patch.object(aw, "UniFiAccessClient") as factory:
        result = await aw.async_remove_access_webhook(
            hass, entry, keep_receiver_on_failure=True
        )
    assert result is False
    factory.assert_not_called()
    assert await _receiver_accepts(hass, hass_client, events) is True


async def test_keep_receiver_without_endpoint_retires_receiver(
    hass: Any,
    hass_client: Any,
    webhook_setup: tuple[MagicMock, Any, Any],
) -> None:
    """With no stored endpoint there is nothing remote to keep; the receiver goes."""
    entry, events = _stored_receiver(webhook_setup)
    data = {
        key: value
        for key, value in entry.data.items()
        if key not in (CONF_ACCESS_WEBHOOK_ENDPOINT_ID, CONF_ACCESS_WEBHOOK_SECRET)
    }
    hass.config_entries.async_update_entry(entry, data=data)
    with patch.object(aw, "UniFiAccessClient") as factory:
        result = await aw.async_remove_access_webhook(
            hass, entry, keep_receiver_on_failure=True
        )
    assert result is True
    factory.assert_not_called()
    assert await _receiver_accepts(hass, hass_client, events) is False


async def test_default_removal_unregisters_even_when_delete_is_refused(
    hass: Any,
    hass_client: Any,
    webhook_setup: tuple[MagicMock, Any, Any],
) -> None:
    """Entry removal stays best effort: the receiver goes even when delete fails."""
    entry, events = _stored_receiver(webhook_setup)
    client = _client()
    client.webhooks.delete = AsyncMock(
        side_effect=UniFiResponseError("refused", status_code=500)
    )
    with patch.object(aw, "UniFiAccessClient", return_value=client):
        result = await aw.async_remove_access_webhook(hass, entry)
    assert result is False
    assert await _receiver_accepts(hass, hass_client, events) is False


async def test_default_removal_unregisters_when_console_times_out(
    hass: Any,
    hass_client: Any,
    webhook_setup: tuple[MagicMock, Any, Any],
) -> None:
    """A bounded, timed-out entry removal still unregisters the receiver."""
    entry, events = _stored_receiver(webhook_setup)
    client = _client()

    async def hang(_endpoint_id: str) -> None:
        await asyncio.sleep(10)

    client.webhooks.delete = AsyncMock(side_effect=hang)
    with (
        patch.object(aw, "UniFiAccessClient", return_value=client),
        patch.object(aw, "REMOVE_DELETE_TIMEOUT_SECONDS", 0.05),
    ):
        result = await aw.async_remove_access_webhook(hass, entry)
    assert result is False
    assert await _receiver_accepts(hass, hass_client, events) is False


async def test_failed_keep_removal_preserves_replay_cache(
    hass: Any,
    hass_client: Any,
    webhook_setup: tuple[MagicMock, Any, Any],
) -> None:
    """A refused move keeps replay memory: a replayed grant stays refused."""
    entry, events = _stored_receiver(webhook_setup)
    body = _unlock_event()
    headers = {"Signature": _sign(body, timestamp=int(time.time()))}
    http = await hass_client()
    url = webhook.async_generate_path(WEBHOOK_ID)
    first = await http.post(url, data=body, headers=headers)
    await hass.async_block_till_done()
    assert first.status == HTTPStatus.OK
    assert len(events) == 1
    client = _client()
    client.webhooks.delete = AsyncMock(
        side_effect=UniFiResponseError("refused", status_code=500)
    )
    with patch.object(aw, "UniFiAccessClient", return_value=client):
        assert (
            await aw.async_remove_access_webhook(
                hass, entry, keep_receiver_on_failure=True
            )
            is False
        )
    replayed = await http.post(url, data=body, headers=headers)
    await hass.async_block_till_done()
    assert replayed.status == HTTPStatus.UNAUTHORIZED
    assert len(events) == 1
