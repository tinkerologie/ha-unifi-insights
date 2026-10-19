---
applyTo: "custom_components/unifi_insights/__init__.py, custom_components/unifi_insights/probe.py, custom_components/unifi_insights/coordinators/**/*.py"
---

# API and Coordinator Instructions

**Applies to:** Integration setup, capability probing, coordinators, and use of the `unifi-official-api` library

## Architecture (CRITICAL)

**Entities → Coordinator → API Client** — Never skip layers

- **Entities:** Read `coordinator.data` only; never call API clients directly in state properties
- **Coordinators:** Call API clients, normalize data via `data_transforms.py`, handle errors and polling/WebSocket lifecycle
- **API Clients:** The `unifi-official-api` PyPI package, imported as `unifi_official_api` (`network`, `protect`, `access`, `innerspace`, `site_manager`, `mobility`, `carrier_fabric`)

## `unifi-official-api` Library

All device communication goes through the [`unifi-official-api`](https://pypi.org/project/unifi-official-api/) package from PyPI ([ADR-008](../../docs/development/adr/008-depend-on-unifi-official-api-from-pypi.md)). `manifest.json` `requirements`, `pyproject.toml` `[project].dependencies`, and `requirements.txt` pin the same version.

**Rules:**

- NEVER create ad-hoc `aiohttp` calls in the integration — reuse the library's client methods
- NEVER vendor library code or add local shims or monkey patches over library behaviour
- **Contract fixes go upstream first:** when an endpoint, model, exception, or client behaviour is wrong or missing, fix it in [tinkerologie/unifi-official-api](https://github.com/tinkerologie/unifi-official-api), release a new version, then raise the pin in all three files and run the full test suite against it
- Patch library symbols in tests where the integration looks them up (for example `custom_components.unifi_insights.coordinators.facade.TalkbackStream`), not inside `unifi_official_api`
- Inject Home Assistant's shared `aiohttp.ClientSession` via `async_get_clientsession(hass, verify_ssl=...)` (Platinum `inject-websession`)
- All client operations must be async (Platinum `async-dependency`)
- Always consult the [UniFi Developer Portal](https://developer.ui.com/) machine-readable specs before adding or modifying API endpoints or models:
  - `https://developer.ui.com/llms.txt` — root index of APIs and versions (as of 2026-10: Network `v10.6.106`, Protect `v7.3.70`, Mobility `v1.0.0`, InnerSpace `v1.3.23`, Site Manager `v1.0.0`, Carrier Fabric `v1.0.0`)
  - `https://developer.ui.com/{service}/{version}/llms.txt` — endpoint list for a service
  - `https://developer.ui.com/{service}/{version}/openapi.json` — authoritative OpenAPI request/response schemas

## Client Setup & Capability Probing

In `__init__.py` and `probe.py`:

- Clients are instantiated with Home Assistant's shared session (`async_get_clientsession`)
- Optional UniFi applications (Protect, Access, InnerSpace, Site Manager, Mobility) are probed gracefully via `probe.py` so consoles running only a subset of applications set up cleanly without failing the config entry

## Data Transforms

- Use `data_transforms.py` (`get_field`, `first_not_none`, model-to-dict helpers) for mapping API responses to internal coordinator dictionaries
- Use `first_not_none()` whenever a valid value can be `0`, `0.0`, `False`, or `""`
- Handle camelCase / snake_case tolerance consistently

## Exception Hierarchy

Map `unifi_official_api.exceptions` to Home Assistant exceptions:

| Library Exception                                                               | Home Assistant Exception                                             | Context                                                     |
| ------------------------------------------------------------------------------- | -------------------------------------------------------------------- | ----------------------------------------------------------- |
| `UniFiAuthenticationError`                                                      | `ConfigEntryAuthFailed`                                              | Setup (`__init__.py`) or Coordinator (`_async_update_data`) |
| `UniFiConnectionError`, `UniFiTimeoutError`                                     | `ConfigEntryNotReady`                                                | Setup (`__init__.py`) when controller is unreachable        |
| `UniFiConnectionError`, `UniFiTimeoutError`, `UniFiResponseError`, `UniFiError` | `UpdateFailed`                                                       | Coordinator refresh (`_async_update_data`)                  |
| `UniFiResponseError`, `UniFiError`                                              | `HomeAssistantError` (with `translation_domain` & `translation_key`) | Service actions (`services.py`) and entity action methods   |
