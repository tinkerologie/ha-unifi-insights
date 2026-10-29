---
applyTo: "custom_components/unifi_insights/config_flow.py, custom_components/unifi_insights/probe.py"
---

# Config Flow Instructions

**Applies to:** Configuration flow and application capability probing

## Connection Modes

This integration supports three connection modes:

- **Local (`local`)** — Host URL + API key (+ optional `verify_ssl`, optional UniFi Access API key/port)
- **Remote (`remote`)** — Console ID + API key (+ optional Site Manager API key)
- **Carrier Fabric (`carrier_fabric`)** — Carrier Fabric API key + Organization ID (sets up `CARRIER_FABRIC_PLATFORMS = [Platform.SENSOR]`)

## Unique ID Resolution

- **Local (`local`):** Resolved from stable hardware console identity (`resolve_console_identity()` gateway/Cloud Key MAC address across sites, or Protect NVR MAC `nvr.mac`). Never use raw API keys, raw IP addresses, or mutable hostnames as unique IDs.
- **Remote (`remote`):** Set to the cloud `console_id` returned by the UniFi Site Manager API (`get_hosts()`).
- **Carrier Fabric (`carrier_fabric`):** Set to `f"carrier_{org_id}"` (or `f"carrier_key_{sha256[:16]}"` when `org_id` is not returned by the probe).
- In user setup and discovery flows, call `await self.async_set_unique_id(...)` followed by `self._abort_if_unique_id_configured()`. In reauth and reconfigure flows, call `self._abort_if_unique_id_mismatch()` when verifying the console's `unique_id`.

## Supported Flows

- **User setup (`async_step_user`)** — Connection type selection → mode-specific credentials (`local`, `remote`, or `carrier_fabric`)
- **SSDP discovery (`async_step_ssdp`)** — Auto-discovers UniFi Dream Machine / UniFi OS consoles and pre-fills host
- **Reauth (`async_step_reauth` / `async_step_reauth_confirm`)** — Updates expired/invalid API keys (Silver `reauthentication-flow`)
- **Reconfigure (`async_step_reconfigure`)** — Updates host, SSL verification, or connection settings in place (Gold `reconfiguration-flow`)
- **Options flow (`UnifiInsightsOptionsFlow`)** — Configures polling intervals, WiFi/client tracking filters, rule audit/export options, and optional Site Manager / Access credentials

## Capability Probing & Validation (`probe.py`)

- Use the helpers in `probe.py` (`async_probe_network`, `async_probe_protect`, `async_probe_access`, `async_probe_innerspace`, `async_probe_carrier_fabric`) to validate connectivity and persist detected application capabilities in config entry data.
- **IMPORTANT:** Never raise `ConfigEntryNotReady` or `ConfigEntryAuthFailed` inside `ConfigFlow` step methods (those exceptions are only for `async_setup_entry`). In `ConfigFlow`, catch authentication/connection/timeout errors and set `errors["base"] = "invalid_auth"` / `"cannot_connect"` / `"unknown"`, then re-render the form.

## Schema Typing & Selectors (HA 2026.5–2026.10+)

- Use `probatio` under `if TYPE_CHECKING:` for strict Voluptuous schema typing:
  ```python
  if TYPE_CHECKING:
      import probatio as vol  # type: ignore[import-not-found]
  else:
      import voluptuous as vol
  ```
- **Do NOT use `FlowHandler.show_advanced_options`** (deprecated in HA 2026.5). Use collapsible data entry flow sections (`section(...)`) instead.
- **Avoid double reloads (HA 2026.6+):** Combining an `entry.add_update_listener(...)` with reloading flow helpers (`async_update_reload_and_abort` or `_abort_if_unique_id_configured(reload_on_update=True)`) is deprecated in HA 2026.6 and raises an error in HA 2026.12. Ensure reloading is handled in one place consistently.
- **Modern Selectors (HA 2026.8–2026.11):**
  - `DeviceClassSelector`, `StateClassSelector` (2026.9)
  - `UnitOfMeasurementSelector` (2026.11, not in 2026.10). It is newer than the minimum supported Home Assistant, so using it needs a raised minimum in `hacs.json` or a tested fallback.
  - `TextSelector` supports `pattern` and `validation_message` (2026.8)
  - `EntitySelector` supports `device` filtering (2026.8)

## Best Practices

- Provide `suggested_values` (via `self.add_suggested_values_to_schema(...)`) for reconfigure and options flows.
- Keep all step titles, field labels, `data_description`, error messages, and abort reasons in `strings.json` and `translations/en.json`.
- Achieve 100% test coverage on all config, discovery, reauth, reconfigure, and options flow paths (`config-flow-test-coverage` Bronze rule).
