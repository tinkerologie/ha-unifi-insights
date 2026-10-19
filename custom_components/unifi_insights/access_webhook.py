# Copyright 2026 UniFi Insights contributors
"""Signed UniFi Access webhook for access-granted and access-denied results."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import weakref
from collections import OrderedDict
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Final

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.const import CONF_HOST, CONF_VERIFY_SSL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError
from unifi_official_api import (
    UniFiError,
    UniFiNotFoundError,
    UniFiResponseError,
    UniFiValidationError,
)
from unifi_official_api.access import UniFiAccessClient

from .const import (
    ACCESS_WEBHOOK_EVENT,
    CONF_ACCESS_PORT,
    CONF_ACCESS_TOKEN,
    CONF_ACCESS_WEBHOOK,
    CONF_ACCESS_WEBHOOK_ENDPOINT_ID,
    CONF_ACCESS_WEBHOOK_ID,
    CONF_ACCESS_WEBHOOK_SECRET,
    DEFAULT_ACCESS_PORT,
    DEFAULT_ACCESS_WEBHOOK,
    DEFAULT_API_HOST,
    DOMAIN,
)
from .coordinators.access import async_dispatch_access_result

if TYPE_CHECKING:
    from collections.abc import Container

    from aiohttp.web import Request
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from unifi_official_api.access.models import AccessWebhookEndpoint

    from .coordinators.access import UnifiInsightsAccessCoordinator

_LOGGER = logging.getLogger(__name__)

ACCESS_WEBHOOK_NAME: Final = "UniFi Insights Access"
ACCESS_ENDPOINT_NAME: Final = "UniFi Insights access results"
# Local only by default: the console must reach Home Assistant on the LAN, and
# Home Assistant refuses other networks instead of trusting a public URL.
ACCESS_WEBHOOK_LOCAL_ONLY: Final = True
SIGNATURE_HEADER: Final = "Signature"
MAX_BODY_BYTES: Final = 16 * 1024
# The console gives up after about five seconds; reading must finish inside that.
BODY_READ_TIMEOUT_SECONDS: Final = 4.0
MAX_SIGNATURE_AGE_SECONDS: Final = 300
MAX_FUTURE_SKEW_SECONDS: Final = 60
REPLAY_CACHE_SIZE: Final = 512
# Removal must not hang on an offline console; a missed delete is documented as
# manual cleanup, and the entry is removed either way.
REMOVE_DELETE_TIMEOUT_SECONDS: Final = 10.0
REPLAY_CACHE_FULL: Final = "replay_cache_full"
# Provenance of the result strings:
# - "Access Granted": object.result of the access.door.unlock webhook sample in
#   the vendor PDF (verified for the webhook payload).
# - "Access Denied" and "BLOCKED": the activity-log sample of access.door.unlock
#   in the same PDF (display message "Access Denied / Unknown (NFC)", result
#   "BLOCKED"). They are the only denied values on record, and neither is
#   confirmed as a webhook object.result. Any other value is ignored, not denied.
GRANTED_RESULTS: Final = frozenset({"Access Granted"})
DENIED_RESULTS: Final = frozenset({"Access Denied", "BLOCKED"})
_MAX_FIELD_LENGTH: Final = 64
_MAX_TIMESTAMP_DIGITS: Final = 12
_HEX_DIGITS: Final = frozenset("0123456789abcdef")
_CHUNK_BYTES: Final = 4096
_NOT_ENABLED: Final = (
    "UniFi Access webhook not enabled (%s); other Access features remain active"
)
_UNSUPPORTED_REASON: Final = "console has no webhook API; Access 2.2.10 or later"


class AccessWebhookRejectedError(Exception):
    """A delivery failed verification. ``reason`` is a category, never a value."""

    def __init__(self, reason: str, status: HTTPStatus) -> None:
        """Store the rejection category and the HTTP status to answer with."""
        super().__init__(reason)
        self.reason = reason
        self.status = status


def parse_signature_header(header: str) -> tuple[str, str]:
    """
    Return the raw timestamp text and the lowercase v1 digest from ``Signature``.

    The header looks like ``t=<unix-seconds>,v1=<hex>``. Only the first value of
    each key is used. The timestamp stays as text so the MAC covers the exact
    bytes the console signed.
    """
    fields: dict[str, str] = {}
    for part in header.split(","):
        key, separator, value = part.strip().partition("=")
        if not separator:
            raise AccessWebhookRejectedError(
                reason="malformed", status=HTTPStatus.BAD_REQUEST
            )
        fields.setdefault(key, value)
    timestamp = fields.get("t", "")
    digest = fields.get("v1", "")
    if (
        not timestamp.isdigit()
        or len(timestamp) > _MAX_TIMESTAMP_DIGITS
        or len(digest) != hashlib.sha256().digest_size * 2
        or not _HEX_DIGITS.issuperset(digest)
    ):
        raise AccessWebhookRejectedError(
            reason="malformed", status=HTTPStatus.BAD_REQUEST
        )
    return timestamp, digest


def verify_delivery(
    header: str, body: bytes, secret: str, *, now: float
) -> tuple[int, str]:
    """
    Check the HMAC-SHA256 over ``<t>.<raw body>`` and the timestamp window.

    Returns the accepted timestamp and digest. The comparison is constant time,
    and freshness is checked only after the signature is proven authentic.
    """
    timestamp_text, digest = parse_signature_header(header)
    expected = hmac.new(
        secret.encode(),
        f"{timestamp_text}.".encode() + body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, digest):
        raise AccessWebhookRejectedError(
            reason="signature", status=HTTPStatus.UNAUTHORIZED
        )
    timestamp = int(timestamp_text)
    if now - timestamp > MAX_SIGNATURE_AGE_SECONDS:
        raise AccessWebhookRejectedError(reason="stale", status=HTTPStatus.UNAUTHORIZED)
    if timestamp - now > MAX_FUTURE_SKEW_SECONDS:
        raise AccessWebhookRejectedError(
            reason="future", status=HTTPStatus.UNAUTHORIZED
        )
    return timestamp, digest


class ReplayCache:
    """
    Admit each authentic digest once while its timestamp is inside the window.

    The cache fails closed. When it is full of live digests it refuses new
    deliveries instead of evicting one: an evicted digest could be replayed
    while its own timestamp is still fresh. Memory stays bounded by ``size``,
    and slots free up as entries pass the age window.
    """

    def __init__(self, size: int = REPLAY_CACHE_SIZE) -> None:
        """Create an empty cache holding at most ``size`` live digests."""
        self._size = size
        self._expiry: OrderedDict[str, int] = OrderedDict()

    def check(self, digest: str, timestamp: int, *, now: float) -> str | None:
        """Admit a digest, or return the refusal reason (``replay`` or full)."""
        for seen, expires in list(self._expiry.items()):
            if expires < now:
                del self._expiry[seen]
        if digest in self._expiry:
            return "replay"
        if len(self._expiry) >= self._size:
            return REPLAY_CACHE_FULL
        self._expiry[digest] = timestamp + MAX_SIGNATURE_AGE_SECONDS
        return None


def _bounded_text(value: Any) -> str | None:
    """Return a short non-empty string, or None for anything else."""
    if isinstance(value, str) and 0 < len(value) <= _MAX_FIELD_LENGTH:
        return value
    return None


def access_result_payload(
    event: dict[str, Any], known_door_ids: Container[str]
) -> dict[str, Any] | None:
    """
    Build the dispatcher payload for one ``access.door.unlock`` event.

    Only a door location is accepted, and only when the door is one the
    coordinator already knows, so a result from another location or an unknown
    door is never emitted. Only the door ID, the result and the authentication
    type are kept. The authentication value, actor and device are never copied.
    An unknown or missing result returns None.
    """
    data = event.get("data")
    if not isinstance(data, dict):
        return None
    location = data.get("location")
    outcome = data.get("object")
    if (
        not isinstance(location, dict)
        or location.get("location_type") != "door"
        or not isinstance(outcome, dict)
    ):
        return None
    door_id = _bounded_text(location.get("id"))
    result = outcome.get("result")
    if door_id is None or door_id not in known_door_ids or not isinstance(result, str):
        return None
    if result in GRANTED_RESULTS:
        result_name = "granted"
    elif result in DENIED_RESULTS:
        result_name = "denied"
    else:
        return None
    return {
        "door_id": door_id,
        "result": result_name,
        "authentication": _bounded_text(outcome.get("authentication_type")),
    }


async def _async_read_body(request: Request) -> bytes | None:
    """Read the raw body without ever holding more than the size bound."""
    declared = request.content_length
    if declared is not None and declared > MAX_BODY_BYTES:
        return None
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.content.iter_chunked(_CHUNK_BYTES):
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_event(body: bytes) -> dict[str, Any] | None:
    """Return the decoded JSON object, or None when the body is not one."""
    try:
        event = json.loads(body)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def _dispatch_verified(
    hass: HomeAssistant,
    entry_id: str,
    coordinator: UnifiInsightsAccessCoordinator,
    event: dict[str, Any],
) -> None:
    """
    Dispatch one authenticated event when the receiver should still act on it.

    Runs synchronously inside the request, so nothing outlives the response. It
    drops events while the coordinator is unauthenticated or not accepting
    webhook results, which covers a delivery that was in flight during unload.
    """
    if event.get("event") != ACCESS_WEBHOOK_EVENT:
        _LOGGER.debug("Ignoring UniFi Access webhook event")
        return
    if coordinator.auth_failed or not coordinator.webhook_active:
        _LOGGER.debug("Ignoring UniFi Access result while the webhook is inactive")
        return
    doors = (coordinator.data or {}).get("doors", {})
    payload = access_result_payload(event, doors)
    if payload is None:
        _LOGGER.debug("Ignoring UniFi Access result from an unknown or unexpected door")
        return
    async_dispatch_access_result(hass, entry_id, payload)


# One replay cache per ConfigEntry object. Home Assistant keeps the same entry
# object across a reload, so a reload keeps the cache and a captured delivery
# cannot be replayed through the new receiver. The weak key lets the cache go
# when the entry object is dropped. A process restart starts with empty caches.
_REPLAY_CACHES: weakref.WeakKeyDictionary[ConfigEntry, ReplayCache] = (
    weakref.WeakKeyDictionary()
)


def _make_handler(
    entry: ConfigEntry, coordinator: UnifiInsightsAccessCoordinator
) -> webhook.HandlerType:
    """Build the receiver for one entry. It reads the secret per request."""
    replay = _REPLAY_CACHES.get(entry)
    if replay is None:
        replay = _REPLAY_CACHES[entry] = ReplayCache()

    async def _async_handle(
        hass: HomeAssistant, _webhook_id: str, request: Request
    ) -> web.Response:
        try:
            async with asyncio.timeout(BODY_READ_TIMEOUT_SECONDS):
                body = await _async_read_body(request)
        except TimeoutError:
            _LOGGER.warning("Rejected UniFi Access webhook delivery (timeout)")
            return web.Response(status=HTTPStatus.REQUEST_TIMEOUT)
        if body is None:
            return web.Response(status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        secret = entry.data.get(CONF_ACCESS_WEBHOOK_SECRET)
        now = time.time()
        try:
            if not isinstance(secret, str) or not secret:
                raise AccessWebhookRejectedError(
                    reason="no_secret", status=HTTPStatus.UNAUTHORIZED
                )
            timestamp, digest = verify_delivery(
                request.headers.get(SIGNATURE_HEADER, ""),
                body,
                secret,
                now=now,
            )
            refusal = replay.check(digest, timestamp, now=now)
            if refusal is not None:
                status = (
                    HTTPStatus.SERVICE_UNAVAILABLE
                    if refusal == REPLAY_CACHE_FULL
                    else HTTPStatus.UNAUTHORIZED
                )
                raise AccessWebhookRejectedError(reason=refusal, status=status)
        except AccessWebhookRejectedError as err:
            _LOGGER.warning("Rejected UniFi Access webhook delivery (%s)", err.reason)
            return web.Response(status=err.status)

        event = _parse_event(body)
        if event is None:
            _LOGGER.warning("Rejected UniFi Access webhook delivery (malformed)")
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        _dispatch_verified(hass, entry.entry_id, coordinator, event)
        return web.Response(status=HTTPStatus.OK)

    return _async_handle


def _webhook_requested(entry: ConfigEntry) -> bool:
    """Return whether the user opted in through the options flow."""
    return bool(entry.options.get(CONF_ACCESS_WEBHOOK, DEFAULT_ACCESS_WEBHOOK))


def _stored_webhook_id(entry: ConfigEntry) -> str | None:
    """Return the stored Home Assistant receiver ID, if any."""
    value = entry.data.get(CONF_ACCESS_WEBHOOK_ID)
    return value if isinstance(value, str) and value else None


def _async_unregister(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the Home Assistant receiver only. Safe to call repeatedly."""
    webhook_id = _stored_webhook_id(entry)
    if webhook_id is not None:
        webhook.async_unregister(hass, webhook_id)


def _async_store(
    hass: HomeAssistant, entry: ConfigEntry, updates: dict[str, str | None]
) -> None:
    """Write webhook state into ``entry.data``; None removes the key."""
    data = dict(entry.data)
    for key, value in updates.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    if data != dict(entry.data):
        hass.config_entries.async_update_entry(entry, data=data)


def _is_not_found(err: UniFiError) -> bool:
    """Return True for a 404, whether the client typed it or only set the status."""
    if isinstance(err, UniFiNotFoundError):
        return True
    return (
        isinstance(err, UniFiResponseError) and err.status_code == HTTPStatus.NOT_FOUND
    )


def _describe_error(err: UniFiError) -> str:
    """Describe an API failure by category only: no body, no IDs, no values."""
    if isinstance(err, UniFiResponseError):
        return f"{type(err).__name__}, status {err.status_code}"
    return type(err).__name__


async def _async_delete_endpoint(client: UniFiAccessClient, endpoint_id: str) -> bool:
    """Delete one remote endpoint. Returns False when it may still exist."""
    try:
        await client.webhooks.delete(endpoint_id)
    except UniFiResponseError as err:
        if _is_not_found(err):
            return True
        _LOGGER.warning(
            "Could not delete UniFi Access webhook endpoint (%s)",
            _describe_error(err),
        )
        return False
    except UniFiError as err:
        _LOGGER.warning(
            "Could not delete UniFi Access webhook endpoint (%s)",
            _describe_error(err),
        )
        return False
    return True


def _temporary_client(
    hass: HomeAssistant, entry: ConfigEntry
) -> UniFiAccessClient | None:
    """Build a client from stored settings, for when no coordinator is loaded."""
    token = entry.data.get(CONF_ACCESS_TOKEN)
    if not isinstance(token, str) or not token:
        return None
    verify_ssl = bool(entry.data.get(CONF_VERIFY_SSL, False))
    try:
        return UniFiAccessClient(
            token=token,
            host=entry.data.get(CONF_HOST, DEFAULT_API_HOST),
            port=entry.data.get(CONF_ACCESS_PORT, DEFAULT_ACCESS_PORT),
            verify_ssl=verify_ssl,
            session=async_get_clientsession(hass, verify_ssl=verify_ssl),
        )
    except UniFiValidationError:
        return None


async def _async_publish_endpoint(
    client: UniFiAccessClient, entry: ConfigEntry, url: str
) -> AccessWebhookEndpoint:
    """
    Create or update the ``access.door.unlock`` endpoint for ``url``.

    The stored endpoint ID wins. Failing that, an endpoint already pointing at
    this receiver URL is adopted, which recovers from lost state without a
    duplicate. If the console forgets an endpoint between listing and updating
    it, the endpoint is created again. Raises UniFiError on other API failure.
    """
    endpoints = await client.webhooks.get_all()
    stored_id = entry.data.get(CONF_ACCESS_WEBHOOK_ENDPOINT_ID)
    existing_id = next(
        (
            endpoint.id
            for endpoint in endpoints
            if endpoint.id and endpoint.id == stored_id
        ),
        None,
    ) or next(
        (
            endpoint.id
            for endpoint in endpoints
            if endpoint.id and endpoint.endpoint == url
        ),
        None,
    )
    if existing_id is not None:
        try:
            return await client.webhooks.update(
                existing_id,
                name=ACCESS_ENDPOINT_NAME,
                endpoint=url,
                events=[ACCESS_WEBHOOK_EVENT],
            )
        except UniFiResponseError as err:
            if not _is_not_found(err):
                raise
    return await client.webhooks.create(
        name=ACCESS_ENDPOINT_NAME,
        endpoint=url,
        events=[ACCESS_WEBHOOK_EVENT],
    )


async def _async_disable(
    hass: HomeAssistant, entry: ConfigEntry, client: UniFiAccessClient
) -> None:
    """Stop receiving and delete the remote endpoint; keep it if deletion fails."""
    _async_unregister(hass, entry)
    endpoint_id = entry.data.get(CONF_ACCESS_WEBHOOK_ENDPOINT_ID)
    if (
        isinstance(endpoint_id, str)
        and endpoint_id
        and not await _async_delete_endpoint(client, endpoint_id)
    ):
        return
    _async_store(
        hass,
        entry,
        {CONF_ACCESS_WEBHOOK_ENDPOINT_ID: None, CONF_ACCESS_WEBHOOK_SECRET: None},
    )


async def async_setup_access_webhook(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: UnifiInsightsAccessCoordinator,
) -> bool:
    """
    Enable or retire the signed access-result webhook for an entry.

    Returns True only when the receiver is registered, the remote endpoint
    returned both an ID and a secret, and the coordinator is marked active. Any
    other outcome logs one warning, leaves Access working and returns False.
    """
    client = coordinator.client
    if not _webhook_requested(entry):
        await _async_disable(hass, entry, client)
        coordinator.async_set_webhook_active(active=False)
        return False

    webhook_id = _stored_webhook_id(entry) or webhook.async_generate_id()
    _async_store(hass, entry, {CONF_ACCESS_WEBHOOK_ID: webhook_id})
    try:
        # The receiver is local_only, so an external URL would be refused by the
        # receiver itself. Only an internal URL is acceptable; none means skip.
        url = webhook.async_generate_url(
            hass, webhook_id, allow_external=False, prefer_external=False
        )
    except NoURLAvailableError:
        _LOGGER.warning(
            "UniFi Access webhook not enabled: Home Assistant has no URL to offer "
            "the console; other Access features remain active"
        )
        _async_unregister(hass, entry)
        coordinator.async_set_webhook_active(active=False)
        return False

    try:
        endpoint = await _async_publish_endpoint(client, entry, url)
    except UniFiResponseError as err:
        reason = _UNSUPPORTED_REASON if _is_not_found(err) else _describe_error(err)
        _LOGGER.warning(_NOT_ENABLED, reason)
        _async_unregister(hass, entry)
        coordinator.async_set_webhook_active(active=False)
        return False
    except UniFiError as err:
        _LOGGER.warning(_NOT_ENABLED, _describe_error(err))
        _async_unregister(hass, entry)
        coordinator.async_set_webhook_active(active=False)
        return False

    secret = endpoint.secret.get_secret_value() if endpoint.secret else ""
    if not endpoint.id or not secret:
        _LOGGER.warning(_NOT_ENABLED, "endpoint ID or secret missing from response")
        if endpoint.id:
            await _async_delete_endpoint(client, endpoint.id)
        _async_unregister(hass, entry)
        _async_store(
            hass,
            entry,
            {CONF_ACCESS_WEBHOOK_ENDPOINT_ID: None, CONF_ACCESS_WEBHOOK_SECRET: None},
        )
        coordinator.async_set_webhook_active(active=False)
        return False

    _async_store(
        hass,
        entry,
        {
            CONF_ACCESS_WEBHOOK_ENDPOINT_ID: endpoint.id,
            CONF_ACCESS_WEBHOOK_SECRET: secret,
        },
    )
    _async_unregister(hass, entry)
    webhook.async_register(
        hass,
        DOMAIN,
        ACCESS_WEBHOOK_NAME,
        webhook_id,
        _make_handler(entry, coordinator),
        local_only=ACCESS_WEBHOOK_LOCAL_ONLY,
        allowed_methods=("POST",),
    )
    coordinator.async_set_webhook_active(active=True)
    return True


async def async_unload_access_webhook(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Unregister the Home Assistant receiver. The remote endpoint is untouched.

    When the coordinator is still reachable through the entry, it is also marked
    inactive, so a delivery whose body was still being read cannot dispatch.
    """
    _async_unregister(hass, entry)
    coordinator = getattr(
        getattr(entry, "runtime_data", None), "access_coordinator", None
    )
    if coordinator is not None:
        coordinator.async_set_webhook_active(active=False)


async def _async_delete_stored_endpoint(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Delete the stored endpoint within a bound. True when none is left remotely."""
    endpoint_id = entry.data.get(CONF_ACCESS_WEBHOOK_ENDPOINT_ID)
    if not isinstance(endpoint_id, str) or not endpoint_id:
        return True
    client = _temporary_client(hass, entry)
    if client is None:
        _LOGGER.warning(
            "Could not delete UniFi Access webhook endpoint: no usable stored settings"
        )
        return False
    try:
        async with asyncio.timeout(REMOVE_DELETE_TIMEOUT_SECONDS):
            return await _async_delete_endpoint(client, endpoint_id)
    except TimeoutError:
        _LOGGER.warning(
            "Timed out deleting UniFi Access webhook endpoint; console may be offline"
        )
        return False
    finally:
        await client.close()


async def async_remove_access_webhook(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    keep_receiver_on_failure: bool = False,
) -> bool:
    """
    Delete the remote endpoint and unregister the receiver, best effort.

    Works without a loaded coordinator, so it runs for disabled, failed and
    removed entries. A temporary client is built from the stored token, host,
    port and TLS setting, and closed afterwards. The delete is bounded by
    ``REMOVE_DELETE_TIMEOUT_SECONDS`` so an offline console cannot block removal.

    Returns True when the remote endpoint is confirmed gone: it was deleted, the
    console reported it missing, or none was stored. Returns False when it may
    still exist.

    By default the receiver is unregistered whatever the outcome. With
    ``keep_receiver_on_failure`` the delete runs first and the receiver is
    unregistered only after a confirmed delete, so a failed host or port move
    keeps the working registration for the old console.
    """
    if not keep_receiver_on_failure:
        _async_unregister(hass, entry)
    deleted = await _async_delete_stored_endpoint(hass, entry)
    if keep_receiver_on_failure and deleted:
        _async_unregister(hass, entry)
    return deleted
