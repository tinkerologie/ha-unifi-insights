# Copyright 2026 UniFi Insights contributors
"""Config flow for UniFi Insights integration."""

from __future__ import annotations

import hashlib
import inspect
import logging
from typing import TYPE_CHECKING, Any, Literal

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_VERIFY_SSL
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from unifi_official_api import (
    ApiKeyAuth,
    ConnectionType,
    LocalAuth,
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiNotFoundError,
    UniFiResponseError,
    UniFiTimeoutError,
    UniFiValidationError,
)
from unifi_official_api.access import UniFiAccessClient
from unifi_official_api.carrier_fabric.client import UniFiCarrierFabricClient
from unifi_official_api.network import UniFiNetworkClient
from unifi_official_api.protect import UniFiProtectClient
from yarl import URL

from .console_identity import (
    DEFAULT_SITE_ID,
    SiteEntityIdentityError,
    async_rekey_site_entities,
    async_restore_site_entity_identities,
    device_fields,
    normalize_console_namespace,
    normalize_mac,
    resolve_console_identity,
)
from .const import (
    ACCESS_CONFIG_KEYS,
    CARRIER_FABRIC_REQUEST_TIMEOUT,
    CONF_ACCESS_PORT,
    CONF_ACCESS_TOKEN,
    CONF_ACCESS_WEBHOOK,
    CONF_ACCESS_WEBHOOK_ENDPOINT_ID,
    CONF_ACCESS_WEBHOOK_ID,
    CONF_ACCESS_WEBHOOK_SECRET,
    CONF_CARRIER_ACTIONS,
    CONF_CARRIER_ORG_ID,
    CONF_CLEAR_ACCESS_TOKEN,
    CONF_CLIENT_CONTROL,
    CONF_CONNECTION_TYPE,
    CONF_CONSOLE_ID,
    CONF_CONSOLE_NAME,
    CONF_SITE_IDS,
    CONF_TRACK_CLIENTS,
    CONF_TRACK_SUBSCRIBERS,
    CONF_TRACK_WIFI_CLIENTS,
    CONF_TRACK_WIRED_CLIENTS,
    CONNECTION_TYPE_CARRIER_FABRIC,
    CONNECTION_TYPE_LOCAL,
    CONNECTION_TYPE_REMOTE,
    DEFAULT_ACCESS_PORT,
    DEFAULT_ACCESS_WEBHOOK,
    DEFAULT_API_HOST,
    DEFAULT_CARRIER_ACTIONS,
    DEFAULT_CLIENT_CONTROL,
    DEFAULT_TRACK_CLIENTS,
    DEFAULT_TRACK_SUBSCRIBERS,
    DOMAIN,
)
from .probe import (
    ProbeResult,
    ProbeStatus,
    async_probe_access,
    async_probe_carrier_fabric,
    async_probe_network,
    async_probe_protect,
    async_probe_with_client,
    is_malformed_response,
    is_transient_error,
)
from .topology import normalize_mac as normalize_hardware_mac

# Schemas are typed against probatio, the library HA 2026.10 annotates its schema
# arguments with. Runtime still imports voluptuous: HA 2026.10 aliases it to probatio,
# and the 2026.9 minimum ships real voluptuous, so only the type checker sees probatio.
if TYPE_CHECKING:
    import probatio as vol
else:
    import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

# Access port field: the console's developer API listens on a TCP port.
_ACCESS_PORT_SCHEMA = vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))
_ACCESS_WEBHOOK_STATE_KEYS = (
    CONF_ACCESS_WEBHOOK_ID,
    CONF_ACCESS_WEBHOOK_ENDPOINT_ID,
    CONF_ACCESS_WEBHOOK_SECRET,
)


def _host_key(host: object) -> str | None:
    """Return the lower-cased host name of a host or URL, or None when unusable."""
    if not isinstance(host, str) or not host.strip():
        return None
    text = host.strip()
    parsed = URL(text) if "://" in text else URL(f"https://{text}")
    return parsed.host.lower() if parsed.host else None


def _clear_access_field(entry: ConfigEntry) -> dict[Any, Any]:
    """Offer the clear-token flag only for an entry that stores a token."""
    if entry.data.get(CONF_ACCESS_TOKEN):
        return {vol.Optional(CONF_CLEAR_ACCESS_TOKEN, default=False): bool}
    return {}


def _access_server_moved(entry: ConfigEntry, host: str, port: int) -> bool:
    """Return True when the Access server differs from the one the entry used."""
    old_port = entry.data.get(CONF_ACCESS_PORT, DEFAULT_ACCESS_PORT)
    return _host_key(entry.data.get(CONF_HOST)) != _host_key(host) or old_port != port


class UnifiInsightsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for UniFi Insights."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._connection_type: str | None = None
        self._remote_api_key: str | None = None
        self._discovered_remote_consoles: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,  # noqa: ARG004
    ) -> UnifiInsightsOptionsFlow:
        """Get the options flow for this handler."""
        return UnifiInsightsOptionsFlow()

    @staticmethod
    def _extract_remote_console_options(hosts: list[dict[str, Any]]) -> dict[str, str]:
        """Build selector labels for accessible remote consoles."""
        console_hosts: list[dict[str, Any]] = []
        network_servers: list[dict[str, Any]] = []

        for host in hosts:
            host_id = host.get("id")
            host_type = host.get("type")
            if (
                not isinstance(host_id, str)
                or not host_id
                or not isinstance(host_type, str)
            ):
                continue

            if host_type == "console":
                console_hosts.append(host)
            elif host_type == "network-server":
                network_servers.append(host)

        candidates = console_hosts or network_servers
        options: dict[str, str] = {}

        for host in sorted(candidates, key=lambda item: str(item.get("id", ""))):
            host_id = str(host["id"])
            reported_state = host.get("reportedState")
            hostname = (
                reported_state.get("hostname")
                if isinstance(reported_state, dict)
                else None
            )
            host_type = str(host.get("type", "console")).replace("-", " ")
            display_name = hostname or host_id
            options[host_id] = f"{display_name} ({host_type})"

        return options

    @staticmethod
    def _normalize_remote_console_id(
        console_id: str,
        discovered_consoles: dict[str, str],
    ) -> str | None:
        """Normalize manual or stored console IDs against discovered host IDs."""
        candidate = console_id.strip()
        if not candidate:
            return None

        lowered_candidate = candidate.lower()
        for discovered_id in discovered_consoles:
            lowered_discovered = discovered_id.lower()
            if lowered_candidate == lowered_discovered:
                return discovered_id

            discovered_prefix = lowered_discovered.split(":", 1)[0]
            if lowered_candidate == discovered_prefix:
                return discovered_id

        return None

    async def _async_discover_remote_consoles(self, api_key: str) -> dict[str, str]:
        """Discover accessible remote consoles for a UI.com API key."""
        auth = ApiKeyAuth(api_key=api_key)
        async with UniFiNetworkClient(
            auth=auth,
            connection_type=ConnectionType.REMOTE,
            timeout=30,
        ) as network_client:
            hosts = await network_client.get_hosts()

        return self._extract_remote_console_options(hosts)

    @staticmethod
    def _flow_error_for_probes(*probes: ProbeResult) -> str:
        """
        Pick the form error for a console where no application was usable.

        A temporary failure wins, so a correct key is never reported as
        invalid while the console is restarting or answering 5xx. Then a
        rejected key, a record that failed to parse, other unexpected
        errors, and a 404 or a Protect answer without an NVR record
        (api_unsupported). Anything else - no sites, or a web-UI page in
        place of the API - keeps the historical "invalid_auth": the console
        commonly answers that way for a key it does not accept.
        """
        statuses = {probe.status for probe in probes}
        if ProbeStatus.UNREACHABLE in statuses:
            return "cannot_connect"
        if ProbeStatus.AUTH_FAILED in statuses:
            return "invalid_auth"
        errors = [probe.error for probe in probes if probe.status is ProbeStatus.ERROR]
        if any(is_malformed_response(error) for error in errors):
            return "site_parse_error"
        if errors:
            return "unknown"
        # 404 from the integration API, or Protect answering without an NVR
        # record (UniFiNotFoundError with status 200), as before this probe
        # refactor.
        if any(isinstance(probe.error, UniFiNotFoundError) for probe in probes):
            return "api_unsupported"
        return "invalid_auth"

    async def _async_validate_local_connection(
        self,
        host: str,
        api_key: str,
        *,
        verify_ssl: bool = False,
        access_token: str | None = None,
    ) -> tuple[bool, str | None, dict[str, str]]:
        """Validate local connection against Network and/or Protect APIs."""
        auth = LocalAuth(api_key=api_key, verify_ssl=verify_ssl)
        console_info: dict[str, str] = {}

        # 1. Probe Network API
        async def _probe_network_and_extract(
            network_client: UniFiNetworkClient,
        ) -> ProbeResult:
            res = await async_probe_network(network_client)
            if res.status is ProbeStatus.AVAILABLE and res.sites:
                site_ids = [getattr(site, "id", None) for site in res.sites]
                # Sites are scanned in API order and the scan stops at the
                # first console found, so the ordinary single-site console
                # still costs one request. Only a controller whose gateway
                # lives outside the first site pays for more - and that is
                # exactly the case that used to leave the flow with no MAC
                # while setup found one, producing a duplicate entry.
                for site_id in site_ids:
                    # Guarding per site, not around the loop: the client stays
                    # usable after a failed request, and setup would still find
                    # the console in a later site. Abandoning the scan here
                    # would put the two paths back out of step.
                    try:
                        devices = await network_client.devices.get_all(
                            site_id=site_id or DEFAULT_SITE_ID
                        )
                    except Exception:
                        _LOGGER.debug(
                            "Could not inspect devices for site %s",
                            site_id,
                            exc_info=True,
                        )
                        continue
                    mac, name = resolve_console_identity(
                        device_fields(dev) for dev in devices
                    )
                    if name and "name" not in console_info:
                        console_info["name"] = name
                    if mac:
                        console_info["id"] = mac
                        console_info["mac"] = mac
                        break

                if "id" not in console_info:
                    # A shared site cannot identify a console. Match setup's
                    # explicit local-endpoint fallback when no MAC is available.
                    host_id = normalize_console_namespace(
                        host, credentials=(api_key, access_token)
                    )
                    if host_id:
                        console_info["id"] = host_id
                    site_name = getattr(res.sites[0], "name", None)
                    if type(site_name) is str:
                        console_info["name"] = site_name
            return res

        network = await async_probe_with_client(
            UniFiNetworkClient(
                auth=auth,
                base_url=host,
                connection_type=ConnectionType.LOCAL,
                timeout=30,
            ),
            _probe_network_and_extract,
        )
        if network.status is ProbeStatus.AVAILABLE:
            return True, None, console_info

        # 2. Probe Protect API
        async def _probe_protect_and_extract(
            protect_client: UniFiProtectClient,
        ) -> ProbeResult:
            res = await async_probe_protect(protect_client)
            if res.status is ProbeStatus.AVAILABLE:
                console_info["id"] = host.lower()
                console_info["name"] = "UniFi Protect"
                if hasattr(protect_client, "nvr") and hasattr(
                    protect_client.nvr, "get"
                ):
                    nvr_call = protect_client.nvr.get()
                    if inspect.isawaitable(nvr_call):
                        try:
                            nvr = await nvr_call
                            if nvr:
                                nvr_mac = normalize_mac(getattr(nvr, "mac", None))
                                if nvr_mac:
                                    console_info["id"] = nvr_mac
                                    console_info["mac"] = nvr_mac
                                nvr_name = getattr(nvr, "name", None) or getattr(
                                    nvr, "display_name", None
                                )
                                if type(nvr_name) is str:
                                    console_info["name"] = nvr_name
                        except Exception:
                            _LOGGER.debug(
                                "Could not inspect protect NVR", exc_info=True
                            )
            return res

        protect = await async_probe_with_client(
            UniFiProtectClient(
                auth=auth,
                base_url=host,
                connection_type=ConnectionType.LOCAL,
                timeout=30,
            ),
            _probe_protect_and_extract,
        )
        if protect.status is ProbeStatus.AVAILABLE:
            return True, None, console_info

        return False, self._flow_error_for_probes(network, protect), console_info

    @staticmethod
    def _access_token_input(user_input: dict[str, Any]) -> str | None:
        """Return the typed Access token without surrounding whitespace, or None."""
        value = user_input.get(CONF_ACCESS_TOKEN)
        if not isinstance(value, str):
            return None
        return value.strip() or None

    async def _async_validate_access(
        self,
        *,
        host: str,
        token: str,
        port: int,
        verify_ssl: bool,
    ) -> str | None:
        """
        Probe Access with a token on the given console host and port.

        Returns None when Access answers with data or is empty. Otherwise it
        returns the form error key. Access never gates the entry, but a token
        that is saved must work, so the entry is not saved on failure.
        """
        try:
            client = UniFiAccessClient(
                token=token,
                host=host,
                port=port,
                verify_ssl=verify_ssl,
                session=async_get_clientsession(self.hass, verify_ssl=verify_ssl),
            )
        except UniFiValidationError:
            return "access_unsupported"
        result = await async_probe_with_client(client, async_probe_access)
        if result.status in (ProbeStatus.AVAILABLE, ProbeStatus.EMPTY):
            return None
        if result.status is ProbeStatus.AUTH_FAILED:
            return "access_invalid_auth"
        if result.status is ProbeStatus.UNREACHABLE:
            return "access_cannot_connect"
        return "access_unsupported"

    async def _async_create_local_entry(
        self, user_input: dict[str, Any], console_info: dict[str, str]
    ) -> ConfigFlowResult:
        """Create a local entry; the Access token and port are stored only if given."""
        console_id = console_info.get("id") or user_input[CONF_HOST].lower()
        await self.async_set_unique_id(console_id)
        self._abort_if_unique_id_configured()

        entry_data: dict[str, Any] = {
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: user_input[CONF_HOST],
            CONF_API_KEY: user_input[CONF_API_KEY],
            CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, False),
        }
        if console_info.get("mac"):
            entry_data[CONF_CONSOLE_ID] = console_info["mac"]
        if console_info.get("name") and console_info.get("mac"):
            entry_data[CONF_CONSOLE_NAME] = console_info["name"]
        access_token = self._access_token_input(user_input)
        if access_token:
            entry_data[CONF_ACCESS_TOKEN] = access_token
            entry_data[CONF_ACCESS_PORT] = user_input.get(
                CONF_ACCESS_PORT, DEFAULT_ACCESS_PORT
            )

        return self.async_create_entry(
            title="UniFi Insights (Local)",
            data=entry_data,
        )

    async def _async_validate_remote_console(
        self,
        api_key: str,
        console_id: str,
    ) -> bool:
        """
        Validate remote connectivity for a specific console host ID.

        Returns False when neither application is usable with this key and
        console. A temporary failure (connection, timeout, 5xx, rate limit)
        raises UniFiConnectionError so the step reports cannot_connect
        rather than an invalid console.
        """
        auth = ApiKeyAuth(api_key=api_key)

        network = await async_probe_with_client(
            UniFiNetworkClient(
                auth=auth,
                connection_type=ConnectionType.REMOTE,
                console_id=console_id,
                timeout=30,
            ),
            async_probe_network,
        )
        if network.status is ProbeStatus.AVAILABLE:
            return True

        protect = await async_probe_with_client(
            UniFiProtectClient(
                auth=auth,
                connection_type=ConnectionType.REMOTE,
                console_id=console_id,
                timeout=30,
            ),
            async_probe_protect,
        )
        if protect.status is ProbeStatus.AVAILABLE:
            return True

        for probe in (network, protect):
            if probe.status is ProbeStatus.UNREACHABLE:
                msg = f"Remote console temporarily unavailable: {probe.error}"
                raise UniFiConnectionError(msg) from probe.error
        return False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step - connection type selection."""
        if user_input is not None:
            self._connection_type = user_input[CONF_CONNECTION_TYPE]
            if self._connection_type == CONNECTION_TYPE_LOCAL:
                return await self.async_step_local()
            if self._connection_type == CONNECTION_TYPE_CARRIER_FABRIC:
                return await self.async_step_carrier_fabric()
            return await self.async_step_remote()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONNECTION_TYPE, default=CONNECTION_TYPE_LOCAL
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=CONNECTION_TYPE_LOCAL,
                                    label="Local (Direct connection)",
                                ),
                                SelectOptionDict(
                                    value=CONNECTION_TYPE_REMOTE,
                                    label="Remote (UniFi Cloud)",
                                ),
                                SelectOptionDict(
                                    value=CONNECTION_TYPE_CARRIER_FABRIC,
                                    label="Carrier Fabric (ISP)",
                                ),
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def _async_validate_carrier_fabric_key(
        self, api_key: str
    ) -> tuple[ProbeResult, dict[str, str]]:
        """
        Probe Carrier Fabric with an ISP API key.

        Returns the probe result and the form errors for it (empty when the
        key works). Shared by setup, reauth and reconfigure.
        """
        client = UniFiCarrierFabricClient(
            auth=ApiKeyAuth(api_key=api_key),
            session=async_get_clientsession(self.hass),
            timeout=CARRIER_FABRIC_REQUEST_TIMEOUT,
        )
        probe_res = await async_probe_with_client(client, async_probe_carrier_fabric)

        if probe_res.status in (ProbeStatus.AVAILABLE, ProbeStatus.EMPTY):
            return probe_res, {}
        if probe_res.missing_scope:
            return probe_res, {"base": "carrier_missing_scope"}
        if probe_res.status is ProbeStatus.AUTH_FAILED:
            return probe_res, {CONF_API_KEY: "invalid_auth"}
        if probe_res.status is ProbeStatus.UNREACHABLE:
            return probe_res, {"base": "cannot_connect"}
        return probe_res, {"base": "unknown"}

    @staticmethod
    def _carrier_data_for_new_key(
        entry: ConfigEntry, api_key: str, probe_res: ProbeResult
    ) -> dict[str, Any] | None:
        """
        Return the entry data for a replacement key, or None for another org.

        The key is refused only when both the stored and the probed
        organisation are known and differ. Only the key (and a missing
        organisation id) change: the unique id never does.
        """
        stored_org_id = entry.data.get(CONF_CARRIER_ORG_ID)
        new_org_id = probe_res.org_id
        if (
            stored_org_id is not None
            and new_org_id is not None
            and stored_org_id != new_org_id
        ):
            return None

        new_data = {**entry.data, CONF_API_KEY: api_key}
        if stored_org_id is None and new_org_id is not None:
            new_data[CONF_CARRIER_ORG_ID] = new_org_id
        return new_data

    async def async_step_carrier_fabric(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle Carrier Fabric ISP setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            probe_res, errors = await self._async_validate_carrier_fabric_key(api_key)
            if not errors:
                if probe_res.org_id:
                    unique_id = f"carrier_{probe_res.org_id}"
                else:
                    key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]
                    unique_id = f"carrier_key_{key_hash}"

                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title="UniFi Carrier Fabric",
                    data={
                        CONF_CONNECTION_TYPE: CONNECTION_TYPE_CARRIER_FABRIC,
                        CONF_API_KEY: api_key,
                        CONF_CARRIER_ORG_ID: probe_res.org_id,
                    },
                )

        return self.async_show_form(
            step_id="carrier_fabric",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
        )

    async def async_step_local(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle local connection setup."""
        errors = {}

        if user_input is not None:
            try:
                (
                    is_valid,
                    error_code,
                    console_info,
                ) = await self._async_validate_local_connection(
                    host=user_input[CONF_HOST],
                    api_key=user_input[CONF_API_KEY],
                    verify_ssl=user_input.get(CONF_VERIFY_SSL, False),
                )
                if is_valid:
                    access_token = self._access_token_input(user_input)
                    if access_token:
                        access_error = await self._async_validate_access(
                            host=user_input[CONF_HOST],
                            token=access_token,
                            port=user_input.get(CONF_ACCESS_PORT, DEFAULT_ACCESS_PORT),
                            verify_ssl=user_input.get(CONF_VERIFY_SSL, False),
                        )
                        if access_error:
                            errors[CONF_ACCESS_TOKEN] = access_error
                    if not errors:
                        return await self._async_create_local_entry(
                            user_input, console_info
                        )
                elif error_code == "invalid_auth":
                    errors[CONF_API_KEY] = "invalid_auth"
                else:
                    errors["base"] = error_code or "cannot_connect"

            except AbortFlow:
                raise
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="local",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=DEFAULT_API_HOST): str,
                    vol.Required(CONF_API_KEY): str,
                    vol.Optional(CONF_VERIFY_SSL, default=False): bool,
                    vol.Optional(CONF_ACCESS_TOKEN): str,
                    vol.Optional(
                        CONF_ACCESS_PORT, default=DEFAULT_ACCESS_PORT
                    ): _ACCESS_PORT_SCHEMA,
                }
            ),
            errors=errors,
        )

    async def async_step_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle remote/cloud connection setup."""
        errors = {}

        if user_input is not None:
            try:
                api_key = user_input[CONF_API_KEY].strip()
                discovered_consoles = await self._async_discover_remote_consoles(
                    api_key
                )
                if not discovered_consoles:
                    errors["base"] = "no_remote_consoles"
                else:
                    self._remote_api_key = api_key
                    self._discovered_remote_consoles = discovered_consoles
                    return await self.async_step_select_console()

            except UniFiAuthenticationError:
                errors[CONF_API_KEY] = "invalid_auth"
                self._remote_api_key = None
                self._discovered_remote_consoles = {}
            except UniFiConnectionError:
                errors["base"] = "cannot_connect"
            except UniFiTimeoutError:
                errors["base"] = "cannot_connect"
            except UniFiNotFoundError:
                _LOGGER.exception(
                    "UniFi cloud returned 404 while discovering consoles. "
                    "The Site Manager API may be unavailable for this account."
                )
                errors["base"] = "api_unsupported"
            except UniFiResponseError as err:
                if is_transient_error(err):
                    _LOGGER.warning("UniFi API temporarily unavailable: %s", err)
                    errors["base"] = "cannot_connect"
                else:
                    _LOGGER.exception("Unexpected UniFi API response")
                    errors["base"] = "unknown"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="remote",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): str,
                }
            ),
            errors=errors,
        )

    async def async_step_select_console(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle remote console selection after API key validation."""
        if not self._remote_api_key or not self._discovered_remote_consoles:
            return await self.async_step_remote()

        errors = {}

        if user_input is not None:
            console_id = user_input[CONF_CONSOLE_ID]

            try:
                sites_found = await self._async_validate_remote_console(
                    self._remote_api_key,
                    console_id,
                )
                if sites_found:
                    await self.async_set_unique_id(console_id)
                    self._abort_if_unique_id_configured()

                    label = self._discovered_remote_consoles.get(console_id, "")
                    console_name = label.rsplit(" (", 1)[0] if " (" in label else label
                    title = (
                        f"UniFi - {console_name}"
                        if console_name and console_name != "Cloud"
                        else "UniFi Insights (Cloud)"
                    )

                    return self.async_create_entry(
                        title=title,
                        data={
                            CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE,
                            CONF_CONSOLE_ID: console_id,
                            CONF_API_KEY: self._remote_api_key,
                        },
                    )

                errors[CONF_CONSOLE_ID] = "invalid_console_id"
            except AbortFlow:
                raise
            except UniFiAuthenticationError:
                errors[CONF_CONSOLE_ID] = "invalid_console_id"
            except UniFiConnectionError:
                errors["base"] = "cannot_connect"
            except UniFiTimeoutError:
                errors["base"] = "cannot_connect"
            except UniFiNotFoundError:
                _LOGGER.exception(
                    "UniFi cloud returned 404 while accessing the selected "
                    "console. The console may not expose the Network "
                    "Integration API."
                )
                errors["base"] = "api_unsupported"
            except Exception as err:  # pylint: disable=broad-except
                if is_malformed_response(err):
                    _LOGGER.exception(
                        "Failed to parse site data from remote UniFi console"
                    )
                    errors["base"] = "site_parse_error"
                else:
                    _LOGGER.exception("Unexpected exception during console selection")
                    errors["base"] = "unknown"

        options: list[SelectOptionDict] = [
            SelectOptionDict(value=console_id, label=label)
            for console_id, label in self._discovered_remote_consoles.items()
        ]

        return self.async_show_form(
            step_id="select_console",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONSOLE_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle reauthorization if the API key becomes invalid."""
        _ = entry_data
        reauth_entry = self._get_reauth_entry()
        if (
            reauth_entry.data.get(CONF_CONNECTION_TYPE)
            == CONNECTION_TYPE_CARRIER_FABRIC
        ):
            return await self.async_step_reauth_carrier_fabric()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_carrier_fabric(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Dialog that informs the user that Carrier Fabric reauth is required."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            probe_res, errors = await self._async_validate_carrier_fabric_key(api_key)
            if not errors:
                new_data = self._carrier_data_for_new_key(
                    reauth_entry, api_key, probe_res
                )
                if new_data is None:
                    return self.async_abort(reason="carrier_org_mismatch")
                return self.async_update_reload_and_abort(reauth_entry, data=new_data)

        return self.async_show_form(
            step_id="reauth_carrier_fabric",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
        )

    def _async_update_site_entry_and_abort(
        self,
        entry: ConfigEntry,
        data: dict[str, Any],
        console_id: Any,
        *,
        unique_id: str | None = None,
        reason: str | None = None,
    ) -> ConfigFlowResult:
        """
        Compensate synchronous failures across registry and entry updates.

        Home Assistant schedules persistence and reloads after in-memory mutation.
        This protects the synchronous transition, not delayed disk failures or
        process crashes; both stores remain managed by native Home Assistant APIs.
        """
        previous_data = entry.data
        previous_unique_id = entry.unique_id
        updates = async_rekey_site_entities(
            self.hass, entry, console_id, pending_api_key=data.get(CONF_API_KEY)
        )
        kwargs: dict[str, Any] = {"data": data}
        if unique_id is not None:
            kwargs["unique_id"] = unique_id
        if reason is not None:
            kwargs["reason"] = reason
        try:
            return self.async_update_reload_and_abort(entry, **kwargs)
        except Exception:
            try:
                if entry.data != previous_data or entry.unique_id != previous_unique_id:
                    owner = (
                        self.hass.config_entries.async_entry_for_domain_unique_id(
                            DOMAIN, previous_unique_id
                        )
                        if previous_unique_id is not None
                        else None
                    )
                    if self.hass.config_entries.async_get_entry(
                        entry.entry_id
                    ) is not entry or (owner is not None and owner is not entry):
                        _LOGGER.warning(
                            "Cannot safely restore the saved connection identity"
                        )
                    else:
                        self.hass.config_entries.async_update_entry(
                            entry, data=previous_data, unique_id=previous_unique_id
                        )
            except Exception:
                _LOGGER.warning("Unable to fully restore saved connection settings")
            finally:
                async_restore_site_entity_identities(self.hass, entry, updates)
            raise

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Dialog that informs the user that reauth is required."""
        errors = {}
        reauth_entry = self._get_reauth_entry()
        connection_type = reauth_entry.data.get(
            CONF_CONNECTION_TYPE, CONNECTION_TYPE_LOCAL
        )

        if user_input is not None:
            try:
                if connection_type == CONNECTION_TYPE_LOCAL:
                    (
                        is_valid,
                        error_code,
                        _,
                    ) = await self._async_validate_local_connection(
                        host=reauth_entry.data.get(CONF_HOST, DEFAULT_API_HOST),
                        api_key=user_input[CONF_API_KEY],
                        verify_ssl=reauth_entry.data.get(CONF_VERIFY_SSL, False),
                    )
                    if is_valid:
                        console_id = normalize_console_namespace(
                            reauth_entry.data.get(CONF_CONSOLE_ID),
                            credentials=(
                                reauth_entry.data.get(CONF_API_KEY),
                                reauth_entry.data.get(CONF_ACCESS_TOKEN),
                            ),
                        )
                        new_data = {
                            **reauth_entry.data,
                            CONF_API_KEY: user_input[CONF_API_KEY],
                        }
                        if console_id is None:
                            # Do not promote the old credential to a namespace
                            # after rotation. Setup will discover a safe ID.
                            new_data.pop(CONF_CONSOLE_ID, None)
                        return self._async_update_site_entry_and_abort(
                            reauth_entry,
                            new_data,
                            console_id,
                        )
                    if error_code == "invalid_auth":
                        errors[CONF_API_KEY] = "invalid_auth"
                    else:
                        errors["base"] = error_code or "cannot_connect"
                else:
                    api_key = user_input[CONF_API_KEY].strip()
                    discovered_consoles = await self._async_discover_remote_consoles(
                        api_key
                    )
                    if not discovered_consoles:
                        errors["base"] = "no_remote_consoles"
                    else:
                        console_id = self._normalize_remote_console_id(
                            reauth_entry.data.get(CONF_CONSOLE_ID, ""),
                            discovered_consoles,
                        )
                        if console_id is None:
                            errors["base"] = "invalid_console_id"
                        else:
                            try:
                                sites_found = await self._async_validate_remote_console(
                                    api_key,
                                    console_id,
                                )
                                if sites_found:
                                    return self._async_update_site_entry_and_abort(
                                        reauth_entry,
                                        {
                                            **reauth_entry.data,
                                            CONF_API_KEY: api_key,
                                            CONF_CONSOLE_ID: console_id,
                                        },
                                        console_id,
                                    )
                                errors["base"] = "invalid_console_id"
                            except UniFiAuthenticationError:
                                errors["base"] = "invalid_console_id"

            except SiteEntityIdentityError as err:
                _LOGGER.warning("Cannot change console identity safely: %s", err)
                errors["base"] = "site_entity_identity_conflict"
            except UniFiAuthenticationError:
                errors[CONF_API_KEY] = "invalid_auth"
            except UniFiConnectionError:
                errors["base"] = "cannot_connect"
            except UniFiTimeoutError:
                errors["base"] = "cannot_connect"
            except UniFiNotFoundError:
                _LOGGER.exception(
                    "UniFi controller returned 404 during reauth. The "
                    "controller may not expose the Network Integration API."
                )
                errors["base"] = "api_unsupported"
            except UniFiResponseError as err:
                if is_transient_error(err):
                    _LOGGER.warning("UniFi API temporarily unavailable: %s", err)
                    errors["base"] = "cannot_connect"
                elif is_malformed_response(err):
                    _LOGGER.exception("Failed to parse site data during reauth")
                    errors["base"] = "site_parse_error"
                else:
                    _LOGGER.exception("Unexpected UniFi API response")
                    errors["base"] = "unknown"
            except Exception:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
        )

    async def _async_reconfigure_access(
        self, entry: ConfigEntry, user_input: dict[str, Any]
    ) -> tuple[dict[str, str], dict[str, Any], Literal["clear", "move"] | None]:
        """
        Validate and plan the Access fields for a local reconfigure.

        Returns the form errors, the Access fields to store, and the remote
        Access webhook cleanup the save needs, if any. Rotating the token on the
        same host and port keeps the stored webhook identifiers and secret.
        Moving to another host or port makes the stored registration belong to a
        server the entry no longer uses, so it is removed before the save and the
        save is refused if that removal cannot be confirmed. Clearing the token
        removes it on a best-effort basis. A moved entry validates the stored
        token against the new server first.
        """
        stored_token = entry.data.get(CONF_ACCESS_TOKEN)
        has_webhook = any(key in entry.data for key in _ACCESS_WEBHOOK_STATE_KEYS)
        stored_fields = {
            key: entry.data[key] for key in ACCESS_CONFIG_KEYS if key in entry.data
        }
        new_host = user_input[CONF_HOST]
        new_port = user_input.get(CONF_ACCESS_PORT, DEFAULT_ACCESS_PORT)
        verify_ssl = user_input.get(CONF_VERIFY_SSL, False)
        typed_token = self._access_token_input(user_input)
        moved = _access_server_moved(entry, new_host, new_port)
        clear = bool(user_input.get(CONF_CLEAR_ACCESS_TOKEN)) and typed_token is None

        if clear:
            # Clearing without a stored token keeps the existing behaviour: no
            # remote call, and nothing to drop.
            return {}, {}, "clear" if stored_token and has_webhook else None

        token = typed_token
        if token is None and stored_token and moved:
            token = stored_token
        if token is None:
            return {}, stored_fields, None

        access_error = await self._async_validate_access(
            host=new_host, token=token, port=new_port, verify_ssl=verify_ssl
        )
        if access_error:
            return {CONF_ACCESS_TOKEN: access_error}, {}, None

        access_fields: dict[str, Any] = {
            CONF_ACCESS_TOKEN: token,
            CONF_ACCESS_PORT: new_port,
        }
        if stored_token and has_webhook and moved:
            return {}, access_fields, "move"
        for key in _ACCESS_WEBHOOK_STATE_KEYS:
            if key in entry.data:
                access_fields[key] = entry.data[key]
        return {}, access_fields, None

    async def _async_reconfigure_local(
        self,
        entry: ConfigEntry,
        user_input: dict[str, Any],
        console_info: dict[str, str],
        *,
        access_data: dict[str, Any],
        cleanup: Literal["clear", "move"] | None,
    ) -> ConfigFlowResult:
        """Save a validated local reconfigure, with its Access fields and cleanup."""
        current_id = entry.data.get(CONF_CONSOLE_ID) or entry.unique_id
        if (
            normalize_console_namespace(
                current_id,
                credentials=(
                    entry.data.get(CONF_API_KEY),
                    user_input[CONF_API_KEY],
                ),
            )
            is None
        ):
            current_id = None
        # Keep the stored identity when device inspection cannot
        # discover hardware. A host fallback may be promoted
        # once the console reports a MAC, but two real hardware
        # addresses must still identify the same console.
        new_id = console_info.get("mac") or current_id or console_info.get("id")
        current_mac = normalize_hardware_mac(current_id)
        new_mac = normalize_hardware_mac(new_id)
        if current_mac and new_mac and new_mac != current_mac:
            return self.async_abort(reason="account_mismatch")

        # A legacy host or site identity can adopt a MAC only
        # when another entry does not already own it. The native
        # update/reload helper does not enforce that ownership.
        if new_id and new_id != entry.unique_id:
            entries = self.hass.config_entries
            owner = entries.async_entry_for_domain_unique_id(DOMAIN, new_id)
            if owner and owner.entry_id != entry.entry_id:
                return self.async_abort(reason="already_configured")

        new_data = {
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: user_input[CONF_HOST],
            CONF_API_KEY: user_input[CONF_API_KEY],
            CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, False),
        }
        if new_id:
            new_data[CONF_CONSOLE_ID] = new_id

        cleanup_ran = False
        if cleanup:
            # Remote cleanup runs after the new connection validated and
            # immediately before the save, so a rejected reconfigure keeps the
            # working registration. A move saves only once the old console has
            # confirmed the endpoint is gone; a cleared token is retired best
            # effort, so it stays removable while the old console is offline.
            # Local import: access_webhook depends on this module's package init.
            from .access_webhook import async_remove_access_webhook  # noqa: PLC0415

            keep_receiver = cleanup == "move"
            removed = await async_remove_access_webhook(
                self.hass, entry, keep_receiver_on_failure=keep_receiver
            )
            cleanup_ran = True
            if keep_receiver and not removed:
                return self.async_abort(reason="cannot_connect")
        new_data.update(access_data)
        try:
            return self._async_update_site_entry_and_abort(
                entry,
                new_data,
                new_id,
                unique_id=new_id or entry.unique_id,
                reason="reconfigure_successful",
            )
        except Exception:
            # The receiver is already unregistered, so the unchanged entry must
            # reload to register it again. The entry check skips a removed or
            # replaced entry, which would fail the reload and hide this error.
            if (
                cleanup_ran
                and self.hass.config_entries.async_get_entry(entry.entry_id) is entry
            ):
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
            raise

    async def async_step_reconfigure_carrier_fabric(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle Carrier Fabric reconfiguration of the integration."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            probe_res, errors = await self._async_validate_carrier_fabric_key(api_key)
            if not errors:
                new_data = self._carrier_data_for_new_key(entry, api_key, probe_res)
                if new_data is None:
                    return self.async_abort(reason="carrier_org_mismatch")
                return self.async_update_reload_and_abort(
                    entry, data=new_data, reason="reconfigure_successful"
                )

        return self.async_show_form(
            step_id="reconfigure_carrier_fabric",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration."""
        entry = self._get_reconfigure_entry()
        connection_type = entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_LOCAL)

        if connection_type == CONNECTION_TYPE_CARRIER_FABRIC:
            return await self.async_step_reconfigure_carrier_fabric(user_input)

        errors = {}

        if user_input is not None:
            try:
                if connection_type == CONNECTION_TYPE_LOCAL:
                    (
                        is_valid,
                        error_code,
                        console_info,
                    ) = await self._async_validate_local_connection(
                        host=user_input[CONF_HOST],
                        api_key=user_input[CONF_API_KEY],
                        verify_ssl=user_input.get(CONF_VERIFY_SSL, False),
                        access_token=entry.data.get(CONF_ACCESS_TOKEN),
                    )
                    if is_valid:
                        (
                            access_errors,
                            access_data,
                            cleanup,
                        ) = await self._async_reconfigure_access(entry, user_input)
                        if access_errors:
                            errors.update(access_errors)
                        else:
                            return await self._async_reconfigure_local(
                                entry,
                                user_input,
                                console_info,
                                access_data=access_data,
                                cleanup=cleanup,
                            )
                    elif error_code == "invalid_auth":
                        errors[CONF_API_KEY] = "invalid_auth"
                    else:
                        errors["base"] = error_code or "cannot_connect"
                else:
                    api_key = user_input[CONF_API_KEY].strip()
                    discovered_consoles = await self._async_discover_remote_consoles(
                        api_key
                    )
                    if not discovered_consoles:
                        errors["base"] = "no_remote_consoles"
                    else:
                        console_id = self._normalize_remote_console_id(
                            user_input[CONF_CONSOLE_ID],
                            discovered_consoles,
                        )
                        if console_id is None:
                            errors[CONF_CONSOLE_ID] = "invalid_console_id"
                        else:
                            try:
                                sites_found = await self._async_validate_remote_console(
                                    api_key,
                                    console_id,
                                )
                                if sites_found:
                                    if (
                                        console_id != entry.data.get(CONF_CONSOLE_ID)
                                        and entry.unique_id
                                        != entry.data.get(CONF_API_KEY)
                                        and entry.unique_id != console_id
                                    ):
                                        return self.async_abort(
                                            reason="account_mismatch"
                                        )

                                    new_data = {
                                        CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE,
                                        CONF_CONSOLE_ID: console_id,
                                        CONF_API_KEY: api_key,
                                    }
                                    return self._async_update_site_entry_and_abort(
                                        entry,
                                        new_data,
                                        console_id,
                                        unique_id=console_id,
                                        reason="reconfigure_successful",
                                    )
                                errors[CONF_CONSOLE_ID] = "invalid_console_id"
                            except UniFiAuthenticationError:
                                errors[CONF_CONSOLE_ID] = "invalid_console_id"

            except SiteEntityIdentityError as err:
                _LOGGER.warning("Cannot change console identity safely: %s", err)
                errors["base"] = "site_entity_identity_conflict"
            except UniFiAuthenticationError:
                errors[CONF_API_KEY] = "invalid_auth"
            except UniFiConnectionError:
                errors["base"] = "cannot_connect"
            except UniFiTimeoutError:
                errors["base"] = "cannot_connect"
            except UniFiNotFoundError:
                _LOGGER.exception(
                    "UniFi controller returned 404 during reconfiguration. "
                    "The controller may not expose the Network Integration "
                    "API."
                )
                errors["base"] = "api_unsupported"
            except UniFiResponseError as err:
                if is_transient_error(err):
                    _LOGGER.warning("UniFi API temporarily unavailable: %s", err)
                    errors["base"] = "cannot_connect"
                elif is_malformed_response(err):
                    _LOGGER.exception(
                        "Failed to parse site data during reconfiguration"
                    )
                    errors["base"] = "site_parse_error"
                else:
                    _LOGGER.exception("Unexpected UniFi API response")
                    errors["base"] = "unknown"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during reconfiguration")
                errors["base"] = "unknown"

        # Show form based on connection type
        if connection_type == CONNECTION_TYPE_LOCAL:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema(
                    {
                        vol.Required(
                            CONF_HOST,
                            default=entry.data.get(CONF_HOST, DEFAULT_API_HOST),
                        ): str,
                        vol.Required(
                            CONF_API_KEY,
                        ): str,
                        vol.Optional(
                            CONF_VERIFY_SSL,
                            default=entry.data.get(CONF_VERIFY_SSL, False),
                        ): bool,
                        vol.Optional(CONF_ACCESS_TOKEN): str,
                        vol.Optional(
                            CONF_ACCESS_PORT,
                            default=entry.data.get(
                                CONF_ACCESS_PORT, DEFAULT_ACCESS_PORT
                            ),
                        ): _ACCESS_PORT_SCHEMA,
                        **_clear_access_field(entry),
                    }
                ),
                errors=errors,
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONSOLE_ID, default=entry.data.get(CONF_CONSOLE_ID, "")
                    ): str,
                    vol.Required(
                        CONF_API_KEY,
                    ): str,
                }
            ),
            errors=errors,
        )


class UnifiInsightsOptionsFlow(OptionsFlow):
    """Handle options for UniFi Insights integration."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if (
            self.config_entry.data.get(CONF_CONNECTION_TYPE)
            == CONNECTION_TYPE_CARRIER_FABRIC
        ):
            if user_input is not None:
                return self.async_create_entry(title="", data=user_input)

            default_track_subscribers = self.config_entry.options.get(
                CONF_TRACK_SUBSCRIBERS, DEFAULT_TRACK_SUBSCRIBERS
            )
            default_carrier_actions = self.config_entry.options.get(
                CONF_CARRIER_ACTIONS, DEFAULT_CARRIER_ACTIONS
            )
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema(
                    {
                        vol.Optional(
                            CONF_TRACK_SUBSCRIBERS,
                            default=default_track_subscribers,
                        ): bool,
                        vol.Optional(
                            CONF_CARRIER_ACTIONS,
                            default=default_carrier_actions,
                        ): bool,
                    }
                ),
            )

        available_sites = self._available_sites()
        current_site_ids: list[str] = list(
            self.config_entry.options.get(CONF_SITE_IDS) or []
        )
        # Sites are only known while the entry is loaded. Offer the picker for
        # multi-site consoles, or whenever a filter is already saved so it can
        # be cleared.
        show_site_picker = bool(available_sites) and (
            len(available_sites) > 1 or bool(current_site_ids)
        )

        if user_input is not None:
            options = dict(user_input)
            if not show_site_picker:
                # The picker was not shown (entry not loaded, or a single-site
                # console), so keep whatever filter was saved before.
                options.pop(CONF_SITE_IDS, None)
                if current_site_ids:
                    options[CONF_SITE_IDS] = current_site_ids
            elif not options.get(CONF_SITE_IDS):
                # No selection means every site; don't store an empty filter.
                options.pop(CONF_SITE_IDS, None)
            return self.async_create_entry(title="", data=options)

        # Get current values, migrating from old CONF_TRACK_CLIENTS if needed
        old_track_clients = self.config_entry.options.get(
            CONF_TRACK_CLIENTS, DEFAULT_TRACK_CLIENTS
        )
        default_wifi = self.config_entry.options.get(
            CONF_TRACK_WIFI_CLIENTS, old_track_clients
        )
        default_wired = self.config_entry.options.get(
            CONF_TRACK_WIRED_CLIENTS, old_track_clients
        )
        default_client_control = self.config_entry.options.get(
            CONF_CLIENT_CONTROL, DEFAULT_CLIENT_CONTROL
        )

        schema: dict[Any, Any] = {
            vol.Optional(
                CONF_TRACK_WIFI_CLIENTS,
                default=default_wifi,
            ): bool,
            vol.Optional(
                CONF_TRACK_WIRED_CLIENTS,
                default=default_wired,
            ): bool,
            vol.Optional(
                CONF_CLIENT_CONTROL,
                default=default_client_control,
            ): bool,
        }
        if self._access_webhook_available():
            schema[
                vol.Optional(
                    CONF_ACCESS_WEBHOOK,
                    default=self.config_entry.options.get(
                        CONF_ACCESS_WEBHOOK, DEFAULT_ACCESS_WEBHOOK
                    ),
                )
            ] = bool
        if show_site_picker:
            site_options = [
                SelectOptionDict(value=site_id, label=name)
                for site_id, name in available_sites.items()
            ]
            # Keep a saved site that has since vanished visible, so it can be
            # deselected rather than silently lingering in the stored filter.
            site_options.extend(
                SelectOptionDict(value=site_id, label=site_id)
                for site_id in current_site_ids
                if site_id not in available_sites
            )
            # Suggested value, not a default: a default would be re-applied
            # when the field is submitted empty, so the filter could never
            # be cleared.
            schema[vol.Optional(CONF_SITE_IDS)] = SelectSelector(
                SelectSelectorConfig(
                    options=site_options,
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), {CONF_SITE_IDS: current_site_ids}
            ),
        )

    def _access_webhook_available(self) -> bool:
        """Offer the Access webhook toggle only for local entries with a token."""
        return self.config_entry.data.get(
            CONF_CONNECTION_TYPE
        ) == CONNECTION_TYPE_LOCAL and bool(
            self.config_entry.data.get(CONF_ACCESS_TOKEN)
        )

    def _available_sites(self) -> dict[str, str]:
        """Return the console's sites (id -> name) when the entry is loaded."""
        runtime_data = getattr(self.config_entry, "runtime_data", None)
        config_coordinator = getattr(runtime_data, "config_coordinator", None)
        sites = getattr(config_coordinator, "available_sites", None)
        return dict(sites) if isinstance(sites, dict) else {}
