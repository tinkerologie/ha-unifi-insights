# Copyright 2026 UniFi Insights contributors
"""The UniFi Insights integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias, cast

import homeassistant.helpers.config_validation as cv
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_VERIFY_SSL, Platform
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.hass_dict import HassKey

from .access_webhook import (
    async_remove_access_webhook,
    async_setup_access_webhook,
    async_unload_access_webhook,
)
from .api import (
    ApiKeyAuth,
    ConnectionType,
    LocalAuth,
    UniFiConnectionError,
    UniFiError,
    UniFiTimeoutError,
)
from .api.access import UniFiAccessClient
from .api.carrier_fabric.client import UniFiCarrierFabricClient
from .api.innerspace import UniFiInnerSpaceClient
from .api.network import UniFiNetworkClient
from .api.protect import UniFiProtectClient
from .carrier_fabric_data import (
    CarrierFabricConfigEntry,
    CarrierFabricData,
)
from .console_identity import (
    first_non_default_site_id,
    is_console_device,
    normalize_console_namespace,
    normalize_mac,
    resolve_console_identity,
)
from .const import (
    CARRIER_FABRIC_REQUEST_TIMEOUT,
    CONF_ACCESS_PORT,
    CONF_ACCESS_TOKEN,
    CONF_CARRIER_ORG_ID,
    CONF_CONNECTION_TYPE,
    CONF_CONSOLE_ID,
    CONF_CONSOLE_NAME,
    CONNECTION_TYPE_CARRIER_FABRIC,
    CONNECTION_TYPE_LOCAL,
    DEFAULT_ACCESS_PORT,
    DEFAULT_API_HOST,
    DOMAIN,
    MOBILITY_DEVICE_PREFIX,
    MOBILITY_WORKSPACE_PREFIX,
)
from .const import (
    CONNECTION_TYPE_REMOTE as CONNECTION_TYPE_REMOTE,
)
from .coordinators import (
    UnifiCarrierFabricCoordinator,
    UnifiConfigCoordinator,
    UnifiDeviceCoordinator,
    UnifiFacadeCoordinator,
    UnifiInsightsInnerSpaceCoordinator,
    UnifiInsightsMobilityCoordinator,
    UnifiInsightsSiteManagerCoordinator,
    UnifiProtectCoordinator,
)
from .coordinators.access import UnifiInsightsAccessCoordinator
from .coordinators.mobility import (
    ACCESS_UNKNOWN,
    async_hand_over_mobility,
    async_setup_mobility,
)
from .coordinators.site_manager import (
    async_acquire_site_manager,
    async_release_site_manager,
)
from .frontend import async_register_frontend
from .probe import (
    ProbeResult,
    ProbeStatus,
    async_probe_access,
    async_probe_carrier_fabric,
    async_probe_innerspace,
    async_probe_network,
    async_probe_protect,
)
from .services import async_setup_services
from .topology_keys import async_load_node_keys, async_remove_node_key
from .websocket_api import async_register_websocket_commands

if TYPE_CHECKING:
    from aiohttp import ClientSession
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry


@dataclass
class UnifiInsightsData:
    """Runtime data for UniFi Insights integration (Platinum multi-coordinator)."""

    config_coordinator: UnifiConfigCoordinator
    device_coordinator: UnifiDeviceCoordinator
    protect_coordinator: UnifiProtectCoordinator | None
    network_client: UniFiNetworkClient
    protect_client: UniFiProtectClient | None
    innerspace_coordinator: UnifiInsightsInnerSpaceCoordinator | None = None
    innerspace_client: UniFiInnerSpaceClient | None = None
    site_manager_coordinator: UnifiInsightsSiteManagerCoordinator | None = None
    site_manager_fingerprint: str | None = None
    # Only the remote entry that owns its cloud key's Mobility data has one.
    mobility_coordinator: UnifiInsightsMobilityCoordinator | None = None
    access_client: UniFiAccessClient | None = None
    access_coordinator: UnifiInsightsAccessCoordinator | None = None
    # Facade coordinator for backward compatibility with entity classes
    _facade_coordinator: UnifiFacadeCoordinator | None = None

    @property
    def coordinator(self) -> UnifiFacadeCoordinator:
        """
        Return facade coordinator for backward compatibility.

        This property provides a unified view combining data from all
        specialized coordinators, ensuring existing entity classes
        continue to work without modifications.
        """
        if self._facade_coordinator is None:
            msg = "Facade coordinator not initialized"
            raise RuntimeError(msg)
        return self._facade_coordinator


# Use TypeAlias for proper mypy validation (Python 3.10+ style)
UnifiInsightsConfigEntry: TypeAlias = ConfigEntry[UnifiInsightsData]  # noqa: UP040

CARRIER_FABRIC_PLATFORMS: list[Platform] = [Platform.SENSOR]

PLATFORMS: list[Platform] = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.COVER,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
    Platform.IMAGE,
    Platform.LIGHT,
    Platform.LOCK,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.UPDATE,
    Platform.SIREN,
]

# Add CONFIG_SCHEMA definition
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:  # noqa: ARG001
    """Set up the UniFi Insights component."""
    # Register service actions at component setup so they are available (and
    # validatable) even when no config entry is loaded (Quality Scale:
    # action-setup).
    await async_setup_services(hass)
    # The topology WebSocket commands, like service actions, are registered
    # once per Home Assistant instance, not once per config entry. Their node
    # id keys are loaded first so the handlers can read them synchronously.
    await async_load_node_keys(hass)
    async_register_websocket_commands(hass)
    # The card bundle is served the same way: once per instance.
    await async_register_frontend(hass)
    return True


# Setup attempts to retry before falling back when the probes are not
# conclusive (see _raise_for_setup_probes). Home Assistant waits 5 s, 10 s,
# then 20 s between attempts (doubling, capped), so the budget lasts at least
# ~35 s from the first incomplete answer, longer if earlier retries while the
# console was unreachable have already raised the backoff.
SETUP_PROBE_RETRIES = 3
_SETUP_PROBE_ATTEMPTS: HassKey[dict[str, dict[str, int]]] = HassKey(
    f"{DOMAIN}_setup_probe_attempts"
)
# One application usable while the other is temporarily unreachable.
_PARTIAL = "partial"
# Nothing usable and nothing more specific than "absent" or "empty".
_INCONCLUSIVE = "inconclusive"


def _clear_setup_probe_attempts(hass: HomeAssistant, entry_id: str) -> None:
    """Forget an entry's setup retry budget."""
    hass.data.get(_SETUP_PROBE_ATTEMPTS, {}).pop(entry_id, None)


def _raise_for_setup_probes(
    hass: HomeAssistant,
    entry_id: str,
    network_probe: ProbeResult,
    protect_probe: ProbeResult,
    innerspace_probe: ProbeResult | None = None,
) -> None:
    """
    Decide from the Network, Protect, and InnerSpace probes whether setup can continue.

    - No application usable and one temporarily unreachable (connection,
      timeout, 408, 429, 5xx): retry setup for as long as that lasts. These
      retries don't use up either budget below, so a console that was
      unreachable while rebooting still gets them once it answers.
    - At least one usable and another temporarily unreachable: retry up to
      SETUP_PROBE_RETRIES times, so a console still starting doesn't silently
      lose that application, then load without it rather than keeping the
      working one offline too.
    - None usable and a key rejected (401/403): reauth.
    - None usable and nothing more specific (no sites, no NVR, no supported
      application, an unparseable response): retry up to SETUP_PROBE_RETRIES
      times - applications still starting answer this way - then reauth as
      before.
    Each budget is counted separately per entry and cleared when the entry
    loads, reauth starts, or the entry is unloaded or removed. Network-only
    consoles (Protect absent), Protect-only consoles (Network absent or
    rejecting the key), InnerSpace-only consoles, and camera-free NVRs load
    immediately.
    """
    budgets: dict[str, int] = hass.data.setdefault(
        _SETUP_PROBE_ATTEMPTS, {}
    ).setdefault(entry_id, {})
    probes: dict[str, ProbeResult] = {
        "Network": network_probe,
        "Protect": protect_probe,
    }
    if innerspace_probe is not None:
        probes["InnerSpace"] = innerspace_probe
    available = [
        name for name, probe in probes.items() if probe.status is ProbeStatus.AVAILABLE
    ]
    unreachable = [
        (name, probe.error)
        for name, probe in probes.items()
        if probe.status is ProbeStatus.UNREACHABLE
    ]

    def retry(msg: str, err: Exception | None, budget: str | None) -> None:
        if budget is not None:
            budgets[budget] = budgets.get(budget, 0) + 1
        _LOGGER.warning(msg)
        raise ConfigEntryNotReady(msg) from err

    def budget_left(budget: str) -> bool:
        return budgets.get(budget, 0) < SETUP_PROBE_RETRIES

    if unreachable and not available:
        name, err = unreachable[0]
        retry(f"Error communicating with UniFi {name} API: {err}", err, None)

    if unreachable and budget_left(_PARTIAL):
        name, err = unreachable[0]
        retry(f"UniFi {name} API is temporarily unavailable: {err}", err, _PARTIAL)

    if available:
        _clear_setup_probe_attempts(hass, entry_id)
        for name, probe in probes.items():
            if probe.status in (
                ProbeStatus.UNREACHABLE,
                ProbeStatus.AUTH_FAILED,
                ProbeStatus.ERROR,
            ):
                _LOGGER.warning(
                    "UniFi %s API could not be validated (%s), continuing "
                    "without %s support: %s",
                    name,
                    probe.status,
                    name,
                    probe.error,
                )
        return

    if any(probe.status is ProbeStatus.AUTH_FAILED for probe in probes.values()):
        _clear_setup_probe_attempts(hass, entry_id)
        msg = "Invalid API key or unable to connect to UniFi API"
        _LOGGER.warning(msg)
        raise ConfigEntryAuthFailed(msg)

    if budget_left(_INCONCLUSIVE):
        first_err = (
            network_probe.error
            or protect_probe.error
            or (innerspace_probe.error if innerspace_probe is not None else None)
        )
        retry(
            "No Network sites, Protect NVR, or InnerSpace project found on console "
            "yet, retrying in case it is still starting",
            first_err,
            _INCONCLUSIVE,
        )

    _clear_setup_probe_attempts(hass, entry_id)
    msg = "No Network sites, Protect NVR, or InnerSpace project found on console"
    _LOGGER.error(msg)
    raise ConfigEntryAuthFailed(msg)


# Re-exported under its original private name: the config flow and this module
# must agree on what a console is, so the definition lives in one place now.
_is_console_device = is_console_device


def _first_site_id(config_coordinator: UnifiConfigCoordinator) -> str | None:
    """Return the first non-default site ID in coordinator API order."""
    data = getattr(config_coordinator, "data", None)
    if not isinstance(data, dict):
        return None
    sites = data.get("sites")
    if not isinstance(sites, dict):
        return None
    # dict order is the API's site order, which is what the flow sees too.
    return first_non_default_site_id(sites)


async def _async_setup_carrier_fabric_entry(
    hass: HomeAssistant, entry: CarrierFabricConfigEntry
) -> bool:
    """Set up UniFi Carrier Fabric integration from a config entry."""
    _LOGGER.info("Setting up UniFi Carrier Fabric integration")
    api_key = entry.data[CONF_API_KEY]
    session = async_get_clientsession(hass)
    auth = ApiKeyAuth(api_key=api_key)
    client = UniFiCarrierFabricClient(
        auth=auth, session=session, timeout=CARRIER_FABRIC_REQUEST_TIMEOUT
    )

    # Register client cleanup via entry.async_on_unload BEFORE anything can fail
    entry.async_on_unload(client.close)

    # Probe
    probe_res = await async_probe_carrier_fabric(client)
    if probe_res.status is ProbeStatus.AUTH_FAILED:
        msg = f"Carrier Fabric authentication failed: {probe_res.error}"
        raise ConfigEntryAuthFailed(msg) from probe_res.error
    if probe_res.status is ProbeStatus.UNREACHABLE:
        msg = f"Carrier Fabric API unreachable: {probe_res.error}"
        raise ConfigEntryNotReady(msg) from probe_res.error
    if probe_res.status in (ProbeStatus.ERROR, ProbeStatus.UNSUPPORTED):
        msg = f"Carrier Fabric API probe failed ({probe_res.status}): {probe_res.error}"
        raise ConfigEntryNotReady(msg) from probe_res.error

    if probe_res.org_id and not entry.data.get(CONF_CARRIER_ORG_ID):
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_CARRIER_ORG_ID: probe_res.org_id},
        )

    # Coordinator
    coordinator = UnifiCarrierFabricCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = CarrierFabricData(client=client, coordinator=coordinator)

    # Forward ONLY sensor platform
    await hass.config_entries.async_forward_entry_setups(
        entry, CARRIER_FABRIC_PLATFORMS
    )

    # Register options reload listener (deviation 8)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    _LOGGER.info("UniFi Carrier Fabric integration setup completed successfully")
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: UnifiInsightsConfigEntry | CarrierFabricConfigEntry
) -> bool:
    """Set up UniFi Insights from a config entry."""
    _LOGGER.info("Setting up UniFi Insights integration")

    # Determine connection type (default to local for backward compatibility)
    connection_type = entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_LOCAL)
    if connection_type == CONNECTION_TYPE_CARRIER_FABRIC:
        return await _async_setup_carrier_fabric_entry(
            hass, cast("CarrierFabricConfigEntry", entry)
        )

    is_local = connection_type == CONNECTION_TYPE_LOCAL

    # Get Home Assistant's aiohttp session for efficient connection pooling
    # (Platinum requirement)
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, False) if is_local else True
    websession = async_get_clientsession(hass, verify_ssl=verify_ssl)

    try:
        if is_local:
            _LOGGER.debug(
                "Initializing UniFi API clients (LOCAL) with host: %s",
                entry.data.get(CONF_HOST, DEFAULT_API_HOST),
            )

            # Create authentication object for local connection
            auth: LocalAuth | ApiKeyAuth = LocalAuth(
                api_key=entry.data[CONF_API_KEY],
                verify_ssl=verify_ssl,
            )

            # Initialize UniFi Network API client with injected websession
            network_client = UniFiNetworkClient(
                auth=auth,
                base_url=entry.data.get(CONF_HOST, DEFAULT_API_HOST),
                connection_type=ConnectionType.LOCAL,
                timeout=30,
                session=websession,
            )
        else:
            _LOGGER.debug(
                "Initializing UniFi API clients (REMOTE) with console_id: %s",
                entry.data.get(CONF_CONSOLE_ID),
            )

            # Create authentication object for remote connection
            auth = ApiKeyAuth(api_key=entry.data[CONF_API_KEY])

            # Initialize UniFi Network API client for remote connection
            network_client = UniFiNetworkClient(
                auth=auth,
                connection_type=ConnectionType.REMOTE,
                console_id=entry.data.get(CONF_CONSOLE_ID),
                timeout=30,
                session=websession,
            )

        # Initialize UniFi Protect API client
        if is_local:
            _LOGGER.debug("Initializing UniFi Protect API client (LOCAL)")
            protect_api = UniFiProtectClient(
                auth=auth,
                base_url=entry.data.get(CONF_HOST, DEFAULT_API_HOST),
                connection_type=ConnectionType.LOCAL,
                timeout=30,
                session=websession,
            )
            _LOGGER.debug("Initializing UniFi InnerSpace API client (LOCAL)")
            innerspace_api = UniFiInnerSpaceClient(
                auth=auth,
                base_url=entry.data.get(CONF_HOST, DEFAULT_API_HOST),
                connection_type=ConnectionType.LOCAL,
                timeout=30,
                session=websession,
            )
        else:
            _LOGGER.debug("Initializing UniFi Protect API client (REMOTE)")
            protect_api = UniFiProtectClient(
                auth=auth,
                connection_type=ConnectionType.REMOTE,
                console_id=entry.data.get(CONF_CONSOLE_ID),
                timeout=30,
                session=websession,
            )
            _LOGGER.debug("Initializing UniFi InnerSpace API client (REMOTE)")
            innerspace_api = UniFiInnerSpaceClient(
                auth=auth,
                connection_type=ConnectionType.REMOTE,
                console_id=entry.data.get(CONF_CONSOLE_ID),
                timeout=30,
                session=websession,
            )

        # Probe Network, Protect, and InnerSpace applications. Each result says
        # whether the application is usable, absent, rejecting the key, or
        # temporarily unreachable.
        _LOGGER.debug("Validating Network, Protect, and InnerSpace API connections")
        network_probe = await async_probe_network(network_client)
        protect_probe = await async_probe_protect(protect_api)
        innerspace_probe = await async_probe_innerspace(innerspace_api)
        network_available = network_probe.status is ProbeStatus.AVAILABLE
        protect_available = protect_probe.status is ProbeStatus.AVAILABLE
        innerspace_available = innerspace_probe.status is ProbeStatus.AVAILABLE
        sites = network_probe.sites
        if network_available:
            _LOGGER.info(
                "Network API validated successfully, found %d sites", len(sites)
            )
        protect_client: UniFiProtectClient | None = None
        if protect_available:
            _LOGGER.info("UniFi Protect API validated successfully")
            protect_client = protect_api
        innerspace_client: UniFiInnerSpaceClient | None = None
        if innerspace_available:
            _LOGGER.info("UniFi InnerSpace API validated successfully")
            innerspace_client = innerspace_api
        else:
            try:
                await innerspace_api.close()
            except Exception as err:
                _LOGGER.debug("Error closing unused InnerSpace client: %s", err)

        _raise_for_setup_probes(
            hass,
            entry.entry_id,
            network_probe,
            protect_probe,
            innerspace_probe=innerspace_probe,
        )

    # Probe failures are classified in _raise_for_setup_probes; these catch
    # anything raised while building the clients.
    except UniFiConnectionError as err:
        _LOGGER.warning("Connection error: %s", err)
        msg = f"Error communicating with UniFi API: {err}"
        raise ConfigEntryNotReady(msg) from err
    except UniFiTimeoutError as err:
        _LOGGER.warning("Timeout error: %s", err)
        msg = f"Timeout connecting to UniFi API: {err}"
        raise ConfigEntryNotReady(msg) from err

    # Create multi-coordinator architecture (Platinum compliance)
    _LOGGER.debug("Creating multi-coordinator architecture")

    # 1. Config coordinator - slow updates (5 minutes) for sites, WiFi
    config_coordinator = UnifiConfigCoordinator(
        hass=hass,
        network_client=network_client,
        protect_client=protect_client,
        entry=entry,
        network_available=network_available,
        innerspace_available=innerspace_available,
    )

    # 2. Device coordinator - fast updates (30 seconds) for devices, stats
    device_coordinator = UnifiDeviceCoordinator(
        hass=hass,
        network_client=network_client,
        protect_client=protect_client,
        entry=entry,
        config_coordinator=config_coordinator,
    )

    # 3. Protect coordinator - fast updates (30 seconds) + WebSocket for events
    # WebSocket site_id: LOCAL consoles ignore site_id for the Protect REST/WS
    # paths, so "default" is fine. REMOTE (cloud connector) routing uses the
    # console_id embedded in the path, but if the Network API returned sites
    # for this console, prefer the first real site id over the placeholder.
    protect_site_id = "default"
    if not is_local and network_available and sites and sites[0].id:
        protect_site_id = sites[0].id

    protect_coordinator: UnifiProtectCoordinator | None = None
    if protect_client:
        protect_coordinator = UnifiProtectCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=entry,
            site_id=protect_site_id,
        )

    # 4. InnerSpace coordinator - slow updates (5 minutes) for floor plans & inventory
    innerspace_coordinator: UnifiInsightsInnerSpaceCoordinator | None = None
    if innerspace_client:
        innerspace_coordinator = UnifiInsightsInnerSpaceCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            innerspace_client=innerspace_client,
            entry=entry,
            device_coordinator=device_coordinator,
            protect_coordinator=protect_coordinator,
        )

    # Fetch initial data - config first, then device/protect/innerspace in parallel
    _LOGGER.debug("Fetching initial data from coordinators")
    await config_coordinator.async_config_entry_first_refresh()

    # Device coordinator needs sites from config coordinator
    refresh_tasks = [device_coordinator.async_config_entry_first_refresh()]
    if protect_coordinator:
        refresh_tasks.append(protect_coordinator.async_config_entry_first_refresh())
    if innerspace_coordinator:
        refresh_tasks.append(innerspace_coordinator.async_config_entry_first_refresh())
    await asyncio.gather(*refresh_tasks)

    # Discover and update console identity if needed (backward compatibility)
    console_id = entry.data.get(CONF_CONSOLE_ID)
    console_name = entry.data.get(CONF_CONSOLE_NAME)
    console_mac: str | None = None

    if protect_coordinator and protect_coordinator.data:
        nvrs = protect_coordinator.data.get("nvrs", {})
        if isinstance(nvrs, dict) and nvrs:
            first_nvr = next(iter(nvrs.values()), None)
            if isinstance(first_nvr, dict):
                console_mac = normalize_mac(first_nvr.get("mac"))
                if not console_name:
                    console_name = first_nvr.get("name")

    if not console_mac and device_coordinator and device_coordinator.data:
        devices_by_site = device_coordinator.data.get("devices", {})
        if isinstance(devices_by_site, dict):
            console_mac, found_name = resolve_console_identity(
                dev_data
                for site_devices in devices_by_site.values()
                if isinstance(site_devices, dict)
                for dev_data in site_devices.values()
            )
            if not console_name:
                console_name = found_name

    if (
        is_local
        and normalize_console_namespace(
            console_id, credentials=(entry.data.get(CONF_API_KEY),)
        )
        is None
    ):
        console_mac = normalize_console_namespace(
            console_mac, credentials=(entry.data.get(CONF_API_KEY),)
        )
        if console_mac:
            console_id = console_mac
        else:
            # A site ID may be shared across consoles. Use the actual local
            # endpoint until hardware identity is available; without a safe
            # endpoint, retain legacy rows and defer namespaced discovery.
            console_id = normalize_console_namespace(
                entry.data.get(CONF_HOST),
                credentials=(entry.data.get(CONF_API_KEY),),
            )

    updates: dict[str, Any] = {}
    new_data = dict(entry.data)
    if console_id and new_data.get(CONF_CONSOLE_ID) != console_id:
        new_data[CONF_CONSOLE_ID] = console_id
        updates["data"] = new_data
    if console_name and new_data.get(CONF_CONSOLE_NAME) != console_name:
        new_data[CONF_CONSOLE_NAME] = console_name
        updates["data"] = new_data

    target_unique_id = console_id or entry.unique_id
    if target_unique_id and target_unique_id != entry.unique_id:
        existing_entry = hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, target_unique_id
        )
        if not existing_entry or existing_entry.entry_id == entry.entry_id:
            updates["unique_id"] = target_unique_id

    if console_name and entry.title in (
        "UniFi Insights (Local)",
        "UniFi Insights (Cloud)",
    ):
        updates["title"] = f"UniFi - {console_name}"

    if updates:
        hass.config_entries.async_update_entry(entry, **updates)

    # Start the real-time Protect WebSocket subscription (additive to the
    # 30s poll above, which stays as the fallback - see
    # UnifiProtectCoordinator.async_start_websocket). Registered for
    # on-unload cleanup immediately so a setup failure later in this
    # function (e.g. platform forwarding below) can't leak the background
    # task - HA still runs async_on_unload callbacks when setup fails.
    if protect_coordinator:
        await protect_coordinator.async_start_websocket()
        entry.async_on_unload(protect_coordinator.async_stop_websocket)

    access_client: UniFiAccessClient | None = None
    access_coordinator: UnifiInsightsAccessCoordinator | None = None
    if is_local and entry.data.get(CONF_ACCESS_TOKEN):
        access_client, access_coordinator = await _async_setup_access(
            hass,
            cast("UnifiInsightsConfigEntry", entry),
            websession,
            verify_ssl=verify_ssl,
        )

    site_manager_fingerprint: str | None = None
    site_manager_coordinator: UnifiInsightsSiteManagerCoordinator | None = None
    mobility_coordinator: UnifiInsightsMobilityCoordinator | None = None
    if not is_local:
        site_manager_fingerprint, account = await async_acquire_site_manager(
            hass, entry.data[CONF_API_KEY], entry.entry_id, websession
        )
        site_manager_coordinator = account.coordinator
        # Mobility is not part of the facade: its polls do not update
        # console entities, and its outages do not affect their availability.
        mobility_coordinator = async_setup_mobility(hass, entry, websession)

    try:
        # The facade aggregates the optional account snapshot separately.
        facade_coordinator = UnifiFacadeCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=entry,
            config_coordinator=config_coordinator,
            device_coordinator=device_coordinator,
            protect_coordinator=protect_coordinator,
            innerspace_client=innerspace_client,
            innerspace_coordinator=innerspace_coordinator,
            site_manager_coordinator=site_manager_coordinator,
        )

        entry.runtime_data = UnifiInsightsData(
            config_coordinator=config_coordinator,
            device_coordinator=device_coordinator,
            protect_coordinator=protect_coordinator,
            network_client=network_client,
            protect_client=protect_client,
            innerspace_coordinator=innerspace_coordinator,
            innerspace_client=innerspace_client,
            site_manager_coordinator=site_manager_coordinator,
            site_manager_fingerprint=site_manager_fingerprint,
            mobility_coordinator=mobility_coordinator,
            access_client=access_client,
            access_coordinator=access_coordinator,
            _facade_coordinator=facade_coordinator,
        )

        if access_coordinator:
            entry.async_on_unload(lambda: async_unload_access_webhook(hass, entry))
            await async_setup_access_webhook(hass, entry, access_coordinator)

        _LOGGER.debug("Setting up platforms: %s", PLATFORMS)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        if access_coordinator:
            await access_coordinator.async_start_websocket()
        entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    except BaseException:
        if site_manager_fingerprint:
            await async_release_site_manager(
                hass, site_manager_fingerprint, entry.entry_id
            )
        raise

    _LOGGER.info("UniFi Insights integration setup completed successfully")
    return True


async def _async_setup_access(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,
    session: ClientSession,
    *,
    verify_ssl: bool,
) -> tuple[UniFiAccessClient | None, UnifiInsightsAccessCoordinator | None]:
    """
    Set up optional Access without changing the existing application gate.

    A transient outage keeps the coordinator. Its poll listeners discover door
    entities on a later good poll, so no reload is needed. Rejected credentials
    and unsupported or unexpected probe answers close the client. A 401 during
    the first refresh also closes it.
    """
    client: UniFiAccessClient | None = None
    try:
        client = UniFiAccessClient(
            token=entry.data[CONF_ACCESS_TOKEN],
            host=entry.data.get(CONF_HOST, DEFAULT_API_HOST),
            port=entry.data.get(CONF_ACCESS_PORT, DEFAULT_ACCESS_PORT),
            verify_ssl=verify_ssl,
            session=session,
        )
        result = await async_probe_access(client)
        if result.status not in (
            ProbeStatus.AVAILABLE,
            ProbeStatus.EMPTY,
            ProbeStatus.UNREACHABLE,
        ):
            _LOGGER.warning(
                "UniFi Access is unavailable (%s); other applications remain active",
                result.status,
            )
            await _async_close_access(client)
            return None, None
        coordinator = UnifiInsightsAccessCoordinator(hass, entry, client)
        # Not async_config_entry_first_refresh: that raises on failure and would
        # discard the coordinator. The failed snapshot stays empty and unavailable.
        await coordinator.async_refresh()
        if coordinator.auth_failed:
            await _async_close_access(client)
            return None, None
        if not coordinator.last_update_success and coordinator.namespace is None:
            # Entities need a namespace-backed listener to poll and recover.
            _LOGGER.warning(
                "UniFi Access is unavailable and has no console identity; "
                "other applications remain active"
            )
            await _async_close_access(client)
            return None, None
        if not coordinator.last_update_success:
            _LOGGER.warning(
                "UniFi Access is unreachable at startup; its door entities appear "
                "when the console responds"
            )

        # Cleanup also runs if platform forwarding subsequently fails. HA runs
        # on_unload callbacks as concurrent tasks, so the close must follow the
        # shutdown inside one coroutine rather than as a separate callback.
        async def _async_teardown_access() -> None:
            await coordinator.async_shutdown()
            await _async_close_access(client)

        entry.async_on_unload(_async_teardown_access)
    except UniFiError as err:
        _LOGGER.warning(
            "UniFi Access setup failed (%s); other applications remain active",
            type(err).__name__,
        )
        if client is not None:
            await _async_close_access(client)
        return None, None
    return client, coordinator


async def _async_close_access(client: UniFiAccessClient) -> None:
    """Close the optional client without exposing secrets or blocking teardown."""
    try:
        await client.close()
    except Exception as err:
        _LOGGER.debug("Error closing Access client (%s)", type(err).__name__)


async def async_unload_entry(
    hass: HomeAssistant, entry: UnifiInsightsConfigEntry | CarrierFabricConfigEntry
) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading UniFi Insights config entry")
    _clear_setup_probe_attempts(hass, entry.entry_id)

    is_carrier_fabric = (
        entry.data.get(CONF_CONNECTION_TYPE) == CONNECTION_TYPE_CARRIER_FABRIC
    )
    if is_carrier_fabric or isinstance(entry.runtime_data, CarrierFabricData):
        return await hass.config_entries.async_unload_platforms(
            entry, CARRIER_FABRIC_PLATFORMS
        )

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Only tear down the API clients once the platforms are gone; closing them
    # first would leave loaded entities behind with dead clients if a platform
    # failed to unload.
    if unload_ok and isinstance(entry.runtime_data, UnifiInsightsData):
        data = entry.runtime_data
        _LOGGER.debug("Closing API clients")
        if data.access_coordinator:
            await data.access_coordinator.async_shutdown()
        await async_unload_access_webhook(hass, entry)
        if data.access_client:
            await _async_close_access(data.access_client)
        # Stop the WebSocket (and await its background task) *before*
        # closing the client below - stopping it after would leave the
        # loop trying to use an already-closed session for whatever
        # brief window passes before entry.async_on_unload's registered
        # copy of this same call runs (see async_setup_entry - that
        # registration is the safety net for a setup failure that
        # happens after the WebSocket starts but before this function
        # ever runs; async_stop_websocket() is idempotent so running it
        # twice on a normal unload is harmless).
        if data.protect_coordinator:
            await data.protect_coordinator.async_stop_websocket()
        if data.protect_client:
            # Close Protect client (await the async close)
            try:
                await data.protect_client.close()
            except Exception as err:
                _LOGGER.debug("Error closing Protect client: %s", err)

        # Close InnerSpace client (await the async close)
        if data.innerspace_client:
            try:
                await data.innerspace_client.close()
            except Exception as err:
                _LOGGER.debug("Error closing InnerSpace client: %s", err)

        # Close Network client (await the async close)
        try:
            await data.network_client.close()
        except Exception as err:
            _LOGGER.debug("Error closing Network client: %s", err)

        if data.site_manager_fingerprint:
            await async_release_site_manager(
                hass, data.site_manager_fingerprint, entry.entry_id
            )

        # A disabled owner will not come back to poll Mobility; a reload or a
        # Home Assistant restart will, so those keep it.
        if data.mobility_coordinator and entry.disabled_by is not None:
            async_hand_over_mobility(hass, entry)

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant,  # noqa: ARG001
    entry: UnifiInsightsConfigEntry | CarrierFabricConfigEntry,
    device_entry: DeviceEntry,
) -> bool:
    """
    Allow deleting a device the integration no longer provides.

    Deselecting a site in options stops it being polled, so its devices can
    never come back and would otherwise sit unavailable with no way to remove
    them. WiFi devices carry no site id, so they are allowed once no polled
    site serves that network any more. Mobility routers and workspaces are
    allowed once Mobility no longer reports them. Everything else is refused:
    devices of polled sites are live, and Protect and client devices are not
    site-scoped.
    """
    runtime_data = getattr(entry, "runtime_data", None)
    if runtime_data is None:
        return False

    if isinstance(runtime_data, CarrierFabricData):
        for domain, identifier in device_entry.identifiers:
            if domain != DOMAIN:
                continue
            if identifier == entry.unique_id:
                # Organisation device: refuse removal
                return False
            if identifier.startswith("carrier_subscriber_"):
                sub_id = identifier.removeprefix("carrier_subscriber_")
                coord_subs = runtime_data.coordinator.data.get("subscribers", {})
                return sub_id not in coord_subs
        return False

    config_coordinator = runtime_data.config_coordinator
    polled = config_coordinator.get_site_ids()
    deselected = set(config_coordinator.available_sites) - set(polled)
    return any(
        domain == DOMAIN
        and (
            any(
                _is_site_scoped_identifier(identifier, site_id)
                for site_id in deselected
            )
            or _is_stale_wifi_identifier(identifier, config_coordinator, polled)
            or _is_stale_mobility_identifier(
                identifier, runtime_data.mobility_coordinator
            )
        )
        for domain, identifier in device_entry.identifiers
    )


def _is_stale_mobility_identifier(
    identifier: str, coordinator: UnifiInsightsMobilityCoordinator | None
) -> bool:
    """
    Return True if a Mobility router or workspace is no longer reported.

    An entry without a Mobility coordinator no longer owns its key's Mobility
    data, so devices it created earlier can never update again. Otherwise only
    a successful poll that answered for the key is proof a device is gone.
    """
    if not identifier.startswith(MOBILITY_DEVICE_PREFIX):
        return False
    if coordinator is None:
        return True
    data = coordinator.data
    if not coordinator.last_update_success or data["access"] == ACCESS_UNKNOWN:
        return False
    if identifier.startswith(MOBILITY_WORKSPACE_PREFIX):
        workspace_id = identifier.removeprefix(MOBILITY_WORKSPACE_PREFIX)
        return workspace_id not in data["workspaces"]
    return identifier.removeprefix(MOBILITY_DEVICE_PREFIX) not in data["devices"]


def _is_stale_wifi_identifier(
    identifier: str, config_coordinator: UnifiConfigCoordinator, polled: list[str]
) -> bool:
    """
    Return True if a ``wifi_{id}`` device is served by no polled site.

    The WiFi device id has no site in it, so the only proof it is gone is that
    every polled site's WiFi list was fetched and none of them has the id. A
    failed fetch keeps the previous data, so it is refused rather than trusted.
    """
    if not identifier.startswith("wifi_"):
        return False
    wifi_id = identifier.removeprefix("wifi_")
    if not config_coordinator.last_update_success:
        return False
    wifi_by_site = config_coordinator.data.get("wifi", {})
    return all(
        config_coordinator.wifi_available(site_id)
        and wifi_id not in wifi_by_site.get(site_id, {})
        for site_id in polled
    )


def _is_site_scoped_identifier(identifier: str, site_id: str) -> bool:
    """
    Return True if a device identifier belongs to the given site.

    Only the exact formats built for site-scoped devices match: network
    devices (``{site}_{device}``), the per-site firewall, route and VPN
    devices, and the ``site_{site}`` device that holds a gateway-less site's
    client count. A loose suffix check would also match a Protect or WiFi id that
    merely ends in the site id; WiFi devices are handled by
    ``_is_stale_wifi_identifier`` instead.
    """
    return identifier.startswith(f"{site_id}_") or identifier in {
        f"firewall_policies_{site_id}",
        f"policy_based_routes_{site_id}",
        f"vpn_clients_{site_id}",
        f"site_{site_id}",
    }


async def async_remove_entry(
    hass: HomeAssistant, entry: UnifiInsightsConfigEntry | CarrierFabricConfigEntry
) -> None:
    """Forget per-entry setup state when an entry is deleted."""
    _clear_setup_probe_attempts(hass, entry.entry_id)
    await async_remove_node_key(hass, entry.entry_id)
    async_hand_over_mobility(hass, entry)
    if entry.data.get(CONF_ACCESS_TOKEN):
        await async_remove_access_webhook(hass, entry)


async def async_reload_entry(
    hass: HomeAssistant, entry: UnifiInsightsConfigEntry | CarrierFabricConfigEntry
) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry to separate console identity from transport credentials."""
    _LOGGER.debug(
        "Migrating entry %s from version %s.%s",
        config_entry.entry_id,
        config_entry.version,
        config_entry.minor_version,
    )

    if config_entry.version > 1:
        # Cannot downgrade from higher major version
        return False

    if config_entry.version == 1:
        new_data = dict(config_entry.data)
        new_unique_id = config_entry.unique_id

        # Remote migration: ensure unique_id is the console_id
        if (
            new_data.get(CONF_CONNECTION_TYPE) == CONNECTION_TYPE_REMOTE
            or new_data.get(CONF_CONNECTION_TYPE) == CONNECTION_TYPE_LOCAL
        ):
            console_id = new_data.get(CONF_CONSOLE_ID)
            if console_id and new_unique_id != console_id:
                existing = hass.config_entries.async_entry_for_domain_unique_id(
                    DOMAIN, console_id
                )
                if not existing or existing.entry_id == config_entry.entry_id:
                    new_unique_id = console_id

        hass.config_entries.async_update_entry(
            config_entry,
            data=new_data,
            unique_id=new_unique_id,
            version=1,
            minor_version=2,
        )

    _LOGGER.info(
        "Migration of entry %s to version %s.%s successful",
        config_entry.entry_id,
        config_entry.version,
        config_entry.minor_version,
    )
    return True
