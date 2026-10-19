---
applyTo: "custom_components/unifi_insights/services.py, custom_components/unifi_insights/service_targets.py, custom_components/unifi_insights/services.yaml"
---

# Service Action Instructions

**Applies to:** Service action definitions and implementations

## Terminology

- **Developer/Code:** "service action" (function names, comments)
- **User-facing:** "action" (UI, translations)
- **Files:** `services.py` and `services.yaml`

## Registration & Lifecycle (IQS `action-setup`)

- Register all service actions in `async_setup(hass, config)` (via `async_setup_services(hass)`), NOT in `async_setup_entry`.
- Service actions remain registered across config entry reloads and validate active config entries when invoked.

## Multi-Console Routing (`service_targets.py`)

Service actions must run against the console that owns the target resource, never blindly against the first config entry. Target routing lives in `service_targets.py` (an ADR-009 seam); `services.py` keeps the schemas, handlers and registration.

- Each handler calls exactly one public call-level resolver: `resolve_network_device_call(hass, call, action=)`, `resolve_network_client_call`, `resolve_network_site_call`, `resolve_network_consoles_call`, `resolve_refresh_call`, `resolve_protect_call(hass, call, resource_type)`, `resolve_protect_console_call`, `resolve_chime_pairing_call`, `resolve_rule_export_call`, `resolve_access_call` or `resolve_carrier_subscriber_call`. Family resolvers and helpers (including `_mac_key`) stay private.
- A resolver returns a frozen result: the owning `entry`, the facade to read cached data from (`cache`), the command owner (`device_commands`, `config_commands`, `protect_commands`, `facade_commands`, `export_commands`, `access_commands` or `carrier_commands`) and the native ID and site.
- Resolvers raise translated `ServiceValidationError`s when the target is missing, unknown, not loaded or ambiguous across consoles. Registry targets resolve to their owning entry (`DeviceEntry.config_entry_id`); identifier lookups are scoped to one config entry at a time.
- Add a new routing rule to `service_targets.py` with real-registry table tests in `tests/test_service_targets.py`; test the handler in `tests/test_services.py` by patching the resolver and asserting the one owner command.

After routing, call the result's owner command, the same one entities use (ADR-010), never a facade pass-through or an API client. The owner raises the translated API errors; the handler only validates input and routes. Actions without a routable target (`trigger_alarm`, `create_liveview`) accept an optional `console_id` parameter (entry title or entry ID), required only when multiple consoles are configured.

## Error Handling & Exception Translations (IQS `action-exceptions` & `exception-translations`)

- Raise `ServiceValidationError` for invalid user parameters, missing targets, or unsupported device capabilities:
  ```python
  raise ServiceValidationError(
      translation_domain=DOMAIN,
      translation_key="invalid_target",
      translation_placeholders={"target": target_id},
  )
  ```
- Raise `HomeAssistantError` (with `translation_domain=DOMAIN`, `translation_key=...`) when an API call or device operation fails, using fixed translated wording or safe identifier placeholders rather than raw exception strings:
  ```python
  raise HomeAssistantError(
      translation_domain=DOMAIN,
      translation_key="action_failed",
      translation_placeholders={"device_id": device_id},
  ) from err
  ```
- Define all `translation_key` entries under the top-level `"exceptions"` key in `strings.json` and `translations/en.json`. `tests/test_translations.py` checks every error `services.py` and `service_targets.py` build: a literal key (or each branch of a conditional, or each call site of a key-forwarding helper), defined in both files, with matching placeholders.
- Never expose raw stack traces or untranslated API exception strings (`str(err)`) in user-facing error placeholders.

## HA 2026.10–2026.11 Service Helper Changes

- **No `hass` argument in service helpers (HA 2026.10+):** `homeassistant.helpers.service` functions (`verify_domain_control`, `extract_entity_ids`, `async_extract_entities`, `async_extract_entity_ids`, `async_extract_config_entry_ids`) no longer accept `hass` as the first parameter (`ServiceCall` already holds `call.hass`).
- **Target selector `device` filter removed (HA 2026.11+):** Never put a `device` filter inside a `target:` selector in `services.yaml` (only `integration`, `domain`, and `supported_features` are valid under `target.device` / `target.entity`).

## Adding a New Service Action

1. Define the schema in `services.yaml`
2. Implement the async handler in `services.py` using `probatio`/`voluptuous` validation and one `service_targets.resolve_*_call` resolver
3. Register the action in `async_setup_services(hass)`
4. Add action name, description, and field translations under `"services"` and error translations under `"exceptions"` in `strings.json` and `translations/en.json`
5. Add unit tests covering success, validation errors (`ServiceValidationError`), and API errors (`HomeAssistantError`) in `tests/test_services.py`
