# Copyright 2026 UniFi Insights contributors
"""
Seeded real command owners over autospecced UniFi clients (ADR-010).

The second adapter for command tests. The coordinators are the real config,
device, Protect and facade classes; only the API clients are replaced, by
``unittest.mock.create_autospec`` of the real ``unifi_official_api`` 2.0.0
client and endpoint classes. Calling a method the library does not have, or
calling one with arguments its signature does not accept, fails the test,
which a hand-written fake or a bare ``MagicMock`` would let pass.

Reads are seeded from deep copies of ``tests/fixtures/library_responses.py``
so a refresh exercises the real coordinator code. Writes keep the autospec
default; a test sets ``return_value`` or ``side_effect`` on the endpoint
method it exercises and asserts on the recorded call.
"""

from __future__ import annotations

import copy
import inspect
import typing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, NonCallableMagicMock, create_autospec

from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_VERIFY_SSL
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unifi_official_api.network import UniFiNetworkClient
from unifi_official_api.network.models.device import LegacyPortMetrics
from unifi_official_api.protect import UniFiProtectClient

from custom_components.unifi_insights.const import (
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_LOCAL,
    DOMAIN,
)
from custom_components.unifi_insights.coordinators import (
    UnifiInsightsConfigCoordinator,
    UnifiInsightsDeviceCoordinator,
    UnifiInsightsFacadeCoordinator,
    UnifiInsightsProtectCoordinator,
)

from .library_responses import (
    SAMPLE_DOORBELL_CAMERA,
    SAMPLE_NVR,
    SAMPLE_PDU_DEVICE,
    SAMPLE_PDU_LEGACY_DEVICE,
    SAMPLE_PUBLIC_CHIME,
    SAMPLE_SITE,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from homeassistant.core import HomeAssistant

BASE_URL = "https://192.168.1.1"
SITE_ID = SAMPLE_SITE["id"]
PDU_ID = SAMPLE_PDU_DEVICE["id"]
CHIME_ID = SAMPLE_PUBLIC_CHIME["id"]
DOORBELL_IDS = (SAMPLE_DOORBELL_CAMERA["id"], "doorbell_cam_2")

# Read methods that return a list; every one is seeded empty first so an
# unseeded read is an empty answer, never a MagicMock leaking into the data.
_LIST_READ_PREFIXES = ("get_all", "list_", "get_legacy", "get_active_legacy")


def _two_doorbell_chime() -> dict[str, Any]:
    """Return the public chime paired with two doorbells."""
    chime = copy.deepcopy(SAMPLE_PUBLIC_CHIME)
    chime["cameraIds"] = list(DOORBELL_IDS)
    chime["ringSettings"] = [
        {
            "cameraId": camera_id,
            "repeatTimes": 1,
            "ringtoneId": "ringtone_default",
            "volume": 80,
        }
        for camera_id in DOORBELL_IDS
    ]
    return chime


def _second_doorbell() -> dict[str, Any]:
    """Return a second doorbell camera for the two-doorbell chime."""
    camera = copy.deepcopy(SAMPLE_DOORBELL_CAMERA)
    camera.update(id=DOORBELL_IDS[1], mac="AABBCC0000D2", name="Back Doorbell")
    return camera


def _endpoint_type(client_cls: type, name: str) -> type | None:
    """Return the endpoint or WebSocket class a client property returns."""
    prop = inspect.getattr_static(client_cls, name)
    try:
        hints = typing.get_type_hints(prop.fget)
    except NameError:  # pragma: no cover - library annotation guard
        return None
    returned = hints.get("return")
    if inspect.isclass(returned) and (
        ".endpoints." in returned.__module__
        or returned.__module__.endswith(".websocket")
    ):
        return returned
    return None


def autospec_endpoint(endpoint_cls: type) -> NonCallableMagicMock:
    """
    Return a strict autospec of one endpoint.

    The spec is taken from a real instance so instance attributes such as
    Protect's ``last_result_complete`` exist, while ``spec_set`` keeps a test
    from adding a method the library does not have.
    """
    try:
        instance = endpoint_cls(MagicMock())
    except TypeError:  # pragma: no cover - endpoints that need more args
        return create_autospec(endpoint_cls, instance=True, spec_set=True)
    endpoint: NonCallableMagicMock = create_autospec(instance, spec_set=True)
    if hasattr(instance, "last_result_complete"):
        endpoint.last_result_complete = True
    for name, _ in inspect.getmembers(endpoint_cls, inspect.iscoroutinefunction):
        if name.startswith(_LIST_READ_PREFIXES):
            getattr(endpoint, name).return_value = []
    return endpoint


def autospec_client(client_cls: type) -> NonCallableMagicMock:
    """Return a strict autospec of a UniFi client with autospecced endpoints."""
    client: NonCallableMagicMock = create_autospec(
        client_cls, instance=True, spec_set=True
    )
    for name, member in inspect.getmembers(client_cls):
        if not isinstance(member, property):
            continue
        if (endpoint_cls := _endpoint_type(client_cls, name)) is not None:
            setattr(client, name, autospec_endpoint(endpoint_cls))
    client.base_url = BASE_URL
    return client


def seed_network_client(client: NonCallableMagicMock) -> None:
    """Seed the Network reads that the config and device owners refresh."""
    site = copy.deepcopy(SAMPLE_SITE)
    site["internalReference"] = "default"
    client.sites.get_all.return_value = [site]
    client.sites.get_legacy_all.return_value = [
        {"name": "default", "desc": SAMPLE_SITE["desc"]}
    ]
    client.devices.get_all.return_value = [copy.deepcopy(SAMPLE_PDU_DEVICE)]
    client.devices.get_legacy_site_devices.return_value = [
        copy.deepcopy(SAMPLE_PDU_LEGACY_DEVICE)
    ]
    client.devices.get_statistics.return_value = {}
    client.devices.get_port_metrics.return_value = LegacyPortMetrics()
    client.get_site_report.return_value = []


def seed_protect_client(client: NonCallableMagicMock) -> None:
    """Seed the Protect reads, including a chime paired with two doorbells."""
    client.cameras.get_all.return_value = [
        copy.deepcopy(SAMPLE_DOORBELL_CAMERA),
        _second_doorbell(),
    ]
    client.chimes.get_all.return_value = [_two_doorbell_chime()]
    client.nvr.get.return_value = copy.deepcopy(SAMPLE_NVR)


@dataclass(frozen=True, kw_only=True, slots=True)
class SeededOwners:
    """Real command owners wired to autospecced clients."""

    entry: MockConfigEntry
    network_client: NonCallableMagicMock
    protect_client: NonCallableMagicMock
    config: UnifiInsightsConfigCoordinator
    device: UnifiInsightsDeviceCoordinator
    protect: UnifiInsightsProtectCoordinator
    facade: UnifiInsightsFacadeCoordinator

    def coordinators(self) -> Iterator[Any]:
        """Yield every owner, for refresh and shutdown."""
        yield from (self.config, self.device, self.protect, self.facade)


def build_seeded_owners(hass: HomeAssistant) -> SeededOwners:
    """Build the real owners over seeded autospecced clients, not refreshed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UniFi Insights",
        data={
            CONF_API_KEY: "test-api-key",
            CONF_HOST: BASE_URL,
            CONF_VERIFY_SSL: False,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
        },
    )
    entry.add_to_hass(hass)
    network_client = autospec_client(UniFiNetworkClient)
    protect_client = autospec_client(UniFiProtectClient)
    seed_network_client(network_client)
    seed_protect_client(protect_client)
    config = UnifiInsightsConfigCoordinator(hass, network_client, protect_client, entry)
    device = UnifiInsightsDeviceCoordinator(
        hass, network_client, protect_client, entry, config
    )
    protect = UnifiInsightsProtectCoordinator(
        hass, network_client, protect_client, entry
    )
    facade = UnifiInsightsFacadeCoordinator(
        hass,
        network_client,
        protect_client,
        entry,
        config,
        device_coordinator=device,
        protect_coordinator=protect,
    )
    return SeededOwners(
        entry=entry,
        network_client=network_client,
        protect_client=protect_client,
        config=config,
        device=device,
        protect=protect,
        facade=facade,
    )


def autospec_command_owners(facade: Any) -> Any:
    """
    Give a mock facade strict autospecs of its command owners.

    For entity and service tests that only check the typed owner call: every
    owner command is an ``AsyncMock`` with the real signature, so calling a
    command that does not exist, or with arguments it does not take, fails.
    The Protect owner is what ``require_protect_coordinator()`` returns
    (see `protect_owner`). Returns the facade for chaining.
    """
    facade.config_coordinator = create_autospec(
        UnifiInsightsConfigCoordinator, instance=True
    )
    facade.device_coordinator = create_autospec(
        UnifiInsightsDeviceCoordinator, instance=True
    )
    facade.require_protect_coordinator = MagicMock(
        return_value=create_autospec(UnifiInsightsProtectCoordinator, instance=True)
    )
    return facade


def protect_owner(facade: Any) -> Any:
    """Return the Protect owner that `autospec_command_owners` gave a facade."""
    return facade.require_protect_coordinator.return_value


async def async_refresh_owners(owners: SeededOwners) -> None:
    """Refresh the owners in setup order: config first, then the others."""
    await owners.config.async_refresh()
    await owners.device.async_refresh()
    await owners.protect.async_refresh()


async def async_shutdown_owners(owners: SeededOwners) -> None:
    """Release every owner's timers and listeners."""
    for coordinator in owners.coordinators():
        await coordinator.async_shutdown()
