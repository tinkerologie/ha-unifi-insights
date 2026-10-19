---
agent: "agent"
tools: ["search/codebase", "edit", "search"]
description: "Add a new service action to the UniFi Insights integration"
---

# Add Service Action

Your goal is to add a new **service action** to the UniFi Insights integration following Home Assistant 2026.10+ and Integration Quality Scale (`action-setup`, `action-exceptions`, `exception-translations`) rules.

If not provided, ask for:

- Service action name (`snake_case`) and purpose
- Parameters required (types, defaults, selectors)
- Target resource type (Network site/device/client, Protect camera/light/chime/viewer, or console-wide)
- Which API client method it calls

## Implementation Steps

### 1. Define schema in `services.yaml`

**File:** `custom_components/unifi_insights/services.yaml`

```yaml
action_name:
  fields:
    parameter_name:
      required: true
      selector:
        text:
```

- **CRITICAL (HA 2026.11):** Never add a `device:` filter inside a `target:` selector block.

### 2. Add handler and register in `services.py`

**File:** `custom_components/unifi_insights/services.py`

- Register all service actions inside `async_setup_services(hass)` called from `async_setup` (Bronze `action-setup`).
- Route resource targets with one public `resolve_*_call(...)` resolver from `service_targets.py` (add routing rules there, not in `services.py`) and run the owner command its typed result holds. For console-wide actions without a resource target, accept an optional `console_id` parameter (required when multiple config entries exist) and route to the matching entry — never blindly pick the first config entry.
- If using `homeassistant.helpers.service` extraction helpers (`async_extract_entities`, `extract_entity_ids`, `async_extract_config_entry_ids`), do **not** pass `hass` as the first argument (removed in HA 2026.10).
- Raise `ServiceValidationError(translation_domain=DOMAIN, translation_key="...", translation_placeholders={...})` for invalid input or unknown targets.
- Raise `HomeAssistantError(translation_domain=DOMAIN, translation_key="...", translation_placeholders={...}) from err` for API/device failures.

### 3. Add translations (`services` and `exceptions`)

**Files:** `custom_components/unifi_insights/strings.json` and `custom_components/unifi_insights/translations/en.json`

Add action labels under `"services"` and any new error keys under `"exceptions"`:

```json
"services": {
  "action_name": {
    "name": "Action display name",
    "description": "What this action does.",
    "fields": {
      "parameter_name": {
        "name": "Parameter display name",
        "description": "What this parameter controls."
      }
    }
  }
},
"exceptions": {
  "action_failed": {
    "message": "Failed to execute action on {device_id}."
  }
}
```

### 4. Add tests and validate

- Add unit tests in `tests/test_services.py` covering success, `ServiceValidationError`, and `HomeAssistantError`
- Run `./script/lint`, `./script/mypy`, and `pytest tests/test_services.py`
