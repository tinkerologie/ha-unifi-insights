# Copyright 2026 UniFi Insights contributors
"""
Classify what a console's Network and Protect APIs say about a set of credentials.

Setup (``__init__.py``) and the config flow both need to answer the same
question for each application - is it usable, absent, rejecting the key, or
temporarily unreachable - and each used to answer it with its own broad
``except Exception``. That turned server errors and timeouts into "this
console has no Network/Protect", and a camera-free NVR probe into a crash.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiRateLimitError,
    UniFiResponseError,
    UniFiTimeoutError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from unifi_official_api.access import UniFiAccessClient
    from unifi_official_api.carrier_fabric import UniFiCarrierFabricClient
    from unifi_official_api.innerspace import UniFiInnerSpaceClient
    from unifi_official_api.network import UniFiNetworkClient
    from unifi_official_api.protect import UniFiProtectClient

_LOGGER = logging.getLogger(__name__)


class ProbeStatus(StrEnum):
    """Outcome of probing one UniFi application."""

    # The application answered with data this integration can use.
    AVAILABLE = "available"
    # The application answered successfully but has nothing to offer
    # (no Network sites, or Protect without an NVR).
    EMPTY = "empty"
    # The console does not expose this application: 404, another 4xx, a
    # redirect, or a 2xx web-UI page instead of JSON.
    UNSUPPORTED = "unsupported"
    # The key was rejected (401/403).
    AUTH_FAILED = "auth_failed"
    # Connection failure, timeout, 408, 5xx or rate limiting - worth retrying.
    UNREACHABLE = "unreachable"
    # Anything else, e.g. a payload that failed to parse.
    ERROR = "error"


@dataclass
class ProbeResult:
    """Result of probing one UniFi application."""

    status: ProbeStatus
    error: Exception | None = None
    sites: list[Any] = field(default_factory=list)
    org_id: str | None = None
    missing_scope: bool = False


def classify_error(err: Exception) -> ProbeStatus:
    """Map an exception raised while probing an application to a status."""
    if isinstance(err, UniFiAuthenticationError):
        return ProbeStatus.AUTH_FAILED
    if isinstance(err, (UniFiConnectionError, UniFiTimeoutError, UniFiRateLimitError)):
        return ProbeStatus.UNREACHABLE
    if isinstance(err, UniFiResponseError):
        if (
            err.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
            or err.status_code == HTTPStatus.REQUEST_TIMEOUT
        ):
            return ProbeStatus.UNREACHABLE
        return ProbeStatus.UNSUPPORTED
    return ProbeStatus.ERROR


def is_transient_error(err: Exception) -> bool:
    """Return True for errors that should be retried rather than reported."""
    return classify_error(err) is ProbeStatus.UNREACHABLE


async def async_probe_network(client: UniFiNetworkClient) -> ProbeResult:
    """Probe the Network application by listing sites."""
    try:
        # A console without the Network application answers with its HTML
        # page; that is the UNSUPPORTED answer, not a fault worth a warning.
        sites = await client.sites.get_all(expected_unsupported=True)
    except Exception as err:
        status = classify_error(err)
        _LOGGER.debug("Network API probe: %s (%r)", status, err)
        return ProbeResult(status, err)
    if not sites:
        _LOGGER.debug("Network API probe: no sites")
        return ProbeResult(ProbeStatus.EMPTY)
    return ProbeResult(ProbeStatus.AVAILABLE, sites=list(sites))


async def async_probe_protect(client: UniFiProtectClient) -> ProbeResult:
    """
    Probe the Protect application.

    Cameras are listed first. An empty camera list is valid - an NVR with no
    cameras adopted yet - so the NVR endpoint decides: an NVR record means
    Protect is usable, a successful response without one means it is empty.
    """
    try:
        # Likewise for a console without the Protect application (#196).
        cameras = await client.cameras.get_all(expected_unsupported=True)
    except Exception as err:
        status = classify_error(err)
        _LOGGER.debug("Protect API probe (cameras): %s (%r)", status, err)
        return ProbeResult(status, err)
    if cameras:
        return ProbeResult(ProbeStatus.AVAILABLE)

    try:
        nvr = await client.nvr.get()
    except ValueError as err:
        # nvr.get() raises ValueError("NVR not found") when the response
        # holds no NVR record; pydantic's ValidationError also subclasses
        # ValueError but means an NVR payload that failed to parse.
        if type(err) is ValueError:
            _LOGGER.debug("Protect API probe: no cameras and no NVR")
            return ProbeResult(ProbeStatus.EMPTY, err)
        _LOGGER.debug("Protect API probe (nvr): %s (%r)", ProbeStatus.ERROR, err)
        return ProbeResult(ProbeStatus.ERROR, err)
    except Exception as err:
        status = classify_error(err)
        _LOGGER.debug("Protect API probe (nvr): %s (%r)", status, err)
        return ProbeResult(status, err)
    if not nvr:
        _LOGGER.debug("Protect API probe: no cameras and no NVR")
        return ProbeResult(ProbeStatus.EMPTY)
    _LOGGER.debug("Protect API probe: NVR found with no cameras")
    return ProbeResult(ProbeStatus.AVAILABLE)


async def async_probe_innerspace(client: UniFiInnerSpaceClient) -> ProbeResult:
    """
    Probe the InnerSpace application.

    Returns ``ProbeStatus.AVAILABLE`` when the project endpoint returns a
    usable project or when any floor-plan, access-point, switch, or inventory
    endpoint returns records. Returns ``ProbeStatus.EMPTY`` when the
    endpoints succeed with no usable records.
    """
    try:
        project = await client.get_project(expected_unsupported=True)
    except Exception as err:
        status = classify_error(err)
        status_code = getattr(err, "status_code", None)
        _LOGGER.debug(
            "InnerSpace API probe (project): %s (status=%s, %r)",
            status,
            status_code,
            err,
        )
        return ProbeResult(status, err)

    if (
        (project.project is not None and bool(project.project.id))
        or bool(project.plans)
        or bool(project.products)
    ):
        return ProbeResult(ProbeStatus.AVAILABLE)

    for label, fetch in (
        ("floor_plans", client.list_floor_plans),
        ("access_points", client.list_access_points),
        ("switches", client.list_switches),
        ("inventory", client.list_inventory),
    ):
        try:
            records = await fetch()
        except Exception as err:
            status = classify_error(err)
            _LOGGER.debug("InnerSpace API probe (%s): %s (%r)", label, status, err)
            return ProbeResult(status, err)
        if records:
            return ProbeResult(ProbeStatus.AVAILABLE)

    _LOGGER.debug("InnerSpace API probe: no project or device records")
    return ProbeResult(ProbeStatus.EMPTY)


async def async_probe_access(client: UniFiAccessClient) -> ProbeResult:
    """
    Probe the UniFi Access developer API by listing doors.

    Access is optional and never gates setup, so every failure is a status
    rather than an exception: a rejected token is AUTH_FAILED (401/403), a
    missing console API or web page is UNSUPPORTED, and a connection or server
    failure is UNREACHABLE. An empty door list is EMPTY, and any door means
    AVAILABLE.
    """
    try:
        doors = await client.doors.get_all()
    except Exception as err:
        status = classify_error(err)
        _LOGGER.debug("Access API probe: %s (%r)", status, err)
        return ProbeResult(status, err)
    if not doors:
        _LOGGER.debug("Access API probe: no doors")
        return ProbeResult(ProbeStatus.EMPTY)
    return ProbeResult(ProbeStatus.AVAILABLE)


async def async_probe_with_client[ClientT](
    client_context: AbstractAsyncContextManager[ClientT],
    probe: Callable[[ClientT], Awaitable[ProbeResult]],
) -> ProbeResult:
    """
    Open a short-lived API client, probe with it, and close it.

    Errors raised while opening or closing the client are classified like
    errors from the probe itself.
    """
    try:
        async with client_context as client:
            return await probe(client)
    except Exception as err:
        status = classify_error(err)
        _LOGGER.debug("API probe client error: %s (%r)", status, err)
        return ProbeResult(status, err)


async def async_probe_carrier_fabric(
    client: UniFiCarrierFabricClient,
) -> ProbeResult:
    """
    Probe the Carrier Fabric application.

    Queries service plans and a single subscriber page (limit=1) to verify
    required API key scopes. Discovers and returns org_id if available.
    """
    try:
        plans = await client.service_plans.get_all()
    except Exception as err:
        status = classify_error(err)
        missing_scope = isinstance(err, UniFiAuthenticationError) and (
            err.status_code == HTTPStatus.FORBIDDEN
            or getattr(err, "api_error_code", None) == "insufficient_scope"
        )
        _LOGGER.debug("Carrier Fabric API probe (service plans): %s (%r)", status, err)
        return ProbeResult(status, err, missing_scope=missing_scope)

    org_id: str | None = None
    for plan in plans:
        if plan.org_id:
            org_id = plan.org_id
            break

    try:
        subscribers = await client.subscribers.get_all(limit=1)
    except Exception as err:
        status = classify_error(err)
        missing_scope = isinstance(err, UniFiAuthenticationError) and (
            err.status_code == HTTPStatus.FORBIDDEN
            or getattr(err, "api_error_code", None) == "insufficient_scope"
        )
        _LOGGER.debug("Carrier Fabric API probe (subscribers): %s (%r)", status, err)
        return ProbeResult(status, err, org_id=org_id, missing_scope=missing_scope)

    if org_id is None:
        for subscriber in subscribers:
            if subscriber.org_id:
                org_id = subscriber.org_id
                break

    if not plans and not subscribers:
        _LOGGER.debug("Carrier Fabric API probe: no plans and no subscribers")
        return ProbeResult(ProbeStatus.EMPTY, org_id=org_id)

    return ProbeResult(ProbeStatus.AVAILABLE, org_id=org_id)
