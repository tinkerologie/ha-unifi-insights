---
applyTo: "custom_components/unifi_insights/services.py, custom_components/unifi_insights/services.yaml"
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

## Multi-Console Routing Helpers

Service actions must run against the console that owns the target resource, never blindly against the first config entry:

- `_get_coordinator_for_network_resource(hass, *, site_id=, device_id=, client_id=)` — resolves the coordinator owning a Network site, device, or client
- `_get_coordinator_for_protect_resource(hass, *, resource_type=, resource_id=, secondary_resource_type=, secondary_resource_id=, console_id=)` — resolves the coordinator owning a Protect camera, light, chime, or viewer

Both raise `ServiceValidationError` when the target is unknown or ambiguous across multiple configured consoles. `_get_protect_owner(hass, call, resource_type, ...)` reads the target from the call, routes it the same way and returns the console's Protect command owner with the native ID.

After routing, call the same owner command that entities use (ADR-010): `coordinator.config_coordinator`, `coordinator.device_coordinator` or the Protect owner, never a facade pass-through or an API client. The owner raises the translated API errors; the handler only validates input and routes. Actions without a routable target (`trigger_alarm`, `create_liveview`) accept an optional `console_id` parameter (entry title or entry ID), required only when multiple consoles are configured.

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
- Define all `translation_key` entries under the top-level `"exceptions"` key in `strings.json` and `translations/en.json`.
- Never expose raw stack traces or untranslated API exception strings (`str(err)`) in user-facing error placeholders.

## HA 2026.10–2026.11 Service Helper Changes

- **No `hass` argument in service helpers (HA 2026.10+):** `homeassistant.helpers.service` functions (`verify_domain_control`, `extract_entity_ids`, `async_extract_entities`, `async_extract_entity_ids`, `async_extract_config_entry_ids`) no longer accept `hass` as the first parameter (`ServiceCall` already holds `call.hass`).
- **Target selector `device` filter removed (HA 2026.11+):** Never put a `device` filter inside a `target:` selector in `services.yaml` (only `integration`, `domain`, and `supported_features` are valid under `target.device` / `target.entity`).

## Adding a New Service Action

1. Define the schema in `services.yaml`
2. Implement the async handler in `services.py` using `probatio`/`voluptuous` validation and multi-console routing helpers
3. Register the action in `async_setup_services(hass)`
4. Add action name, description, and field translations under `"services"` and error translations under `"exceptions"` in `strings.json` and `translations/en.json`
5. Add unit tests covering success, validation errors (`ServiceValidationError`), and API errors (`HomeAssistantError`) in `tests/test_services.py`
