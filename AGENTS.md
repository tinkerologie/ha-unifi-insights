# AI Agent Instructions

## Mandatory Development Workflow for AI Agents

All AI coding assistants (Claude Code, Gemini, GitHub Copilot, Cursor, Roo, Devin, etc.) MUST strictly adhere to the following workflow when working on this repository:

1. Issue-First Requirement: Never start writing code or creating pull requests without an existing GitHub Issue.
2. Issue Template: Issues must be created using the official Issue Forms (`01-bug-report.yml` for bugs or `02-enhancement-request.yml` for enhancements).
3. CodeRabbit AI Plan: Every issue must have a generated Coding Plan from CodeRabbit AI (`@coderabbitai plan`). AI agents must read and implement strictly according to the approved plan.
4. Branch Discipline: Always create and work on a dedicated branch (`feat/<issue-id>-<description>` or `fix/<issue-id>-<description>`). NEVER commit directly to `main`.
5. Documentation & Changelog: Update `CHANGELOG.md` under `## [Unreleased]` for any user-facing or architectural changes.
6. Local Verification: Run all test, lint, format, and typecheck commands locally before committing. All tests must pass with zero errors and zero warnings.
7. PR Template Completion: Always use `.github/pull_request_template.md`. Complete all fields, link the issue using `Closes #<id>` or `Fixes #<id>` when the PR fully resolves it, or `Part of #<id>` for partial work (only maintainers with write access may write `None`, for housekeeping), and check all Pre-Submission Governance boxes with real terminal output.
8. Draft PR First: ALWAYS open pull requests as DRAFT (`gh pr create --draft`).
9. CI Verification: Wait for CI workflows to run on the draft PR and verify that all status checks pass.
10. Ready for Review: Only mark the PR as ready for review (`gh pr ready`) once all checks pass and all due diligence is verified.
11. Review Clearance: Review and resolve all CodeRabbit AI and GitHub Copilot comments. Ensure Codecov reports show zero errors, zero warnings, and no coverage drop.

This document provides guidance for AI coding agents working on this Home Assistant custom integration project.

## Project Overview

This is **UniFi Insights**, a Home Assistant custom integration that provides monitoring and control of UniFi network infrastructure using the UniFi Network and Protect APIs.

**Integration details:**

- **Domain:** `unifi_insights`
- **Title:** UniFi Insights
- **Class prefix:** `UnifiInsights`
- **Repository:** tinkerologie/ha-unifi-insights

**Key directories:**

- `custom_components/unifi_insights/` — Main integration code
- `custom_components/unifi_insights/coordinators/` — Multi-coordinator system
- `custom_components/unifi_insights/frontend/` — Built Lovelace card bundles served by `frontend.py`
- `frontend/` — TypeScript + Lit source for the custom Lovelace dashboard cards
- `config/` — Home Assistant configuration for local testing
- `tests/` — Unit and integration tests
- `script/` — Development and validation scripts

**Local Home Assistant instance:**

**Use the project's scripts whenever available** (`./script/develop`, `./script/lint`, `./script/mypy`, `./script/setup/bootstrap`). For checks that do not have a wrapper script, use the documented commands (`pre-commit run --all-files`, `pytest`).

**Start Home Assistant:**

```bash
./script/develop
```

**Force restart (when HA is unresponsive or port conflicts):**

```bash
pkill -f "hass --config" || true && ./script/develop
```

**When to restart:** After modifying Python files, `manifest.json`, `services.yaml`, translations, or config flow changes.

**Reading logs:**

- Live: Terminal where `./script/develop` runs
- File: `config/home-assistant.log` (most recent), `config/home-assistant.log.1` (previous)

**Adjusting log levels:**

- Integration logs: `custom_components.unifi_insights: debug` in `config/configuration.yaml`
- Restart HA after changes

**Context-specific instructions:**

Path-specific instructions in `.github/instructions/*.instructions.md` provide targeted guidance by file glob (`applyTo`). This document serves as the primary reference for all agents.

**Other agent entry points:**

- **Claude Code:** See [`CLAUDE.md`](CLAUDE.md) (pointer to this file)
- **Gemini:** See [`GEMINI.md`](GEMINI.md) (pointer to this file)
- **GitHub Copilot:** See [`.github/copilot-instructions.md`](.github/copilot-instructions.md) (compact reference and PR review guidelines)

## Working With Developers

**For workflow basics (small changes, translations, tests, session management):** See `.github/copilot-instructions.md` for quick-reference guidance.

### When Instructions Conflict With Requests

If a developer requests something that contradicts these instructions:

1. **Clarify the intent** — Ask if they want to deviate from the documented guidelines
2. **Confirm understanding** — Restate what you understood to avoid misinterpretation
3. **Suggest instruction updates** — If this represents a permanent change, offer to update these instructions
4. **Proceed once confirmed** — Follow the developer's explicit direction after clarification

### Maintaining These Instructions

- Refine guidelines based on actual project needs
- Remove outdated rules that no longer apply
- Consolidate redundant sections to prevent bloat

**Propose updates when:**

- You notice repeated deviations from documented patterns
- Instructions become outdated or contradict actual code
- New patterns emerge that should be standardized

### Documentation vs. Instructions

**Three types of content with clear separation:**

1. **Agent Instructions** — How AI should write code (`.github/instructions/`, `AGENTS.md`)
2. **Developer Documentation** — Architecture and design decisions (`docs/`)
3. **User Documentation** — End-user guides (`README.md`)

**AI Planning:** Use `.ai-scratch/` for temporary notes (never committed)

**Rules:**

- NEVER create random markdown files in code directories
- NEVER create documentation in `.github/` unless it's a GitHub-specified file
- ALWAYS ask first before creating permanent documentation
- Prefer module docstrings over separate markdown files

### Session and Context Management

**Commit suggestions:**

When a task completes and the developer moves to a new topic, suggest committing changes. Offer a commit message based on the work done.

**Commit message format:** Follow [Conventional Commits](https://www.conventionalcommits.org/) specification

**Common types:** `feat:`, `fix:`, `chore:`, `refactor:`, `docs:`, `test:`

## Custom Integration Flexibility

**This is a CUSTOM integration, not a Home Assistant Core integration.** While we follow Core patterns for quality and maintainability, we have more flexibility in implementation decisions.

**UniFi API library (`unifi-official-api`):**

- All device communication goes through the [`unifi-official-api`](https://pypi.org/project/unifi-official-api/) package from PyPI, imported as `unifi_official_api` and pinned in `manifest.json` `requirements` (see `docs/development/adr/008-depend-on-unifi-official-api-from-pypi.md`)
- Reuse its client methods (`network_client`, `protect_client`, `access_client`, `innerspace_client`, `carrier_fabric_client`) rather than manual HTTP calls
- Pass Home Assistant's shared aiohttp session (`async_get_clientsession(hass)`) into clients (Quality Scale: `inject-websession`)
- NEVER create a custom HTTP client, vendor library code, or add local shims or monkey patches over library behaviour
- **API contract fixes go upstream first:** fix the client, endpoint, model, or exception in [tinkerologie/unifi-official-api](https://github.com/tinkerologie/unifi-official-api), release a new version, then raise the pin here (`manifest.json`, `pyproject.toml`, `requirements.txt`)

**Quality Scale requirements (strict — Bronze through Platinum):**

As an AI agent, **strictly follow the current Home Assistant Integration Quality Scale rules** for every applicable change:

- Canonical rules: <https://developers.home-assistant.io/docs/core/integration-quality-scale/rules>
- Full checklist: <https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist>
- Treat applicable Bronze, Silver, Gold, and Platinum rules as mandatory, not aspirational:
  - **Bronze:** `action-setup` (register actions in `async_setup`), `appropriate-polling`, `brands`, `common-modules`, `config-flow` (with `data_description`), `config-flow-test-coverage`, `dependency-transparency`, `entity-event-setup` (`async_added_to_hass` + `self.async_on_remove`), `entity-unique-id`, `has-entity-name`, `runtime-data` (`entry.runtime_data`), `test-before-configure`, `test-before-setup`, `unique-config-entry`, and documentation rules
  - **Silver:** `action-exceptions` (`HomeAssistantError` / `ServiceValidationError`), `config-entry-unloading`, `entity-unavailable`, `integration-owner`, `log-when-unavailable` (log once at `INFO` when unavailable and once when back online), `parallel-updates` (`PARALLEL_UPDATES` in every platform), `reauthentication-flow`, `test-coverage` (≥95%)
  - **Gold:** `devices`, `diagnostics` (`async_redact_data`), `discovery`, `discovery-update-info`, `dynamic-devices`, `entity-category`, `entity-device-class`, `entity-disabled-by-default`, `entity-translations`, `exception-translations` (`translation_domain=DOMAIN`, `translation_key=...`), `icon-translations` (`icons.json`), `reconfiguration-flow`, `repair-issues`, `stale-devices`
  - **Platinum:** `async-dependency`, `inject-websession`, `strict-typing` (`py.typed` + strict `mypy` + typed `ConfigEntry`)
- Validate changed behavior against applicable rules before considering work complete
- If a rule is not applicable, call that out briefly in the final summary
- If a request conflicts with a rule, explicitly flag the conflict and ask for direction

**Developer expectation:** Generate production-ready code that meets HA 2026.10+ standards and applicable Quality Scale rules.

## Code Style and Quality

**Python:** Python 3.14.2+ (`py314`), 4 spaces, 88 char lines, double quotes, full type hints, async for all I/O

**YAML:** 2 spaces, modern HA syntax (no legacy `platform:` style)

**JSON:** 2 spaces, no trailing commas, no comments

**Validation:** Run these before committing:

```bash
# Run all pre-commit hooks (ruff format + check, codespell, yamllint, mypy)
pre-commit run --all-files

# Or individually:
./script/lint         # Ruff format + check --fix
./script/mypy         # Strict mypy type checking with HA venv
pytest                # Tests (95% total coverage enforced locally)
```

**For comprehensive standards, see:**

- `.github/instructions/python.instructions.md` — Python patterns, imports, type hints
- `.github/instructions/yaml.instructions.md` — YAML structure and HA-specific patterns
- `.github/instructions/json.instructions.md` — JSON formatting and schema validation

## Project-Specific Rules

### Integration Identifiers

This integration uses the following identifiers consistently:

- **Domain:** `unifi_insights`
- **Title:** UniFi Insights
- **Class prefix:** `UnifiInsights`

**When creating new files:**

- Use the domain `unifi_insights` for all DOMAIN references
- Prefix all integration-specific classes with `UnifiInsights`
- Use "UniFi Insights" as the display title
- Never hardcode different values

### Integration Structure

**Package organization:**

- `coordinators/` — Multi-coordinator system (`base`, `config`, `config_sections`, `device`, `protect`, `facade`, `access`, `carrier_fabric`, `innerspace`, `mobility`, `rule_audit`, `site_manager`)
- `config_flow.py` — Config and options flows (`local`, `remote`, and `carrier_fabric` connection modes; reauth and reconfigure)
- `console_identity.py` — Console identity resolution and namespace normalization
- `probe.py` — Service availability probing (Network, Protect, Access, InnerSpace, Carrier Fabric)
- `entity.py` — Core base entity classes (`UnifiInsightsEntity`, `UnifiProtectEntity`) and field helpers (`get_field`, `first_not_none`)
- Every entity lives in its platform module (`sensor.py`, `binary_sensor.py`, `switch.py`, ...), in one section per data source; every base entity lives in `entity.py` (ADR-007, ADR-011)
- `data_transforms.py` & `innerspace_transforms.py` — API response to internal data mapping
- `services.py` & `services.yaml` — Service action implementations and schema definitions
- `websocket_api.py`, `topology.py`, `dashboard_snapshots.py`, `frontend.py` — WebSocket commands, card snapshot builders and Lovelace card bundle registration
- `access_webhook.py` — UniFi Access webhook lifecycle management
- `const.py` — Constants and endpoint paths
- `diagnostics.py` — Three-layer redacted diagnostic data export
- Platform files (17 platforms): `alarm_control_panel.py`, `binary_sensor.py`, `button.py`, `camera.py`, `cover.py`, `device_tracker.py`, `event.py`, `image.py`, `light.py`, `lock.py`, `media_player.py`, `number.py`, `select.py`, `sensor.py`, `siren.py`, `switch.py`, `update.py`

**Do NOT create:**

- `helpers/`, `ha_helpers/`, or similar packages — use the existing module of the right role (`data_transforms.py` for pure transforms, `console_identity.py` for identity and registry lookups, `entity.py` for base entities)
- `common/`, `shared/`, `lib/` — use existing modules
- New top-level packages without explicit approval

**Key patterns:**

- Entities → Coordinator → API Client (never skip layers)
- Store runtime state in `entry.runtime_data` (`UnifiInsightsData` or `CarrierFabricData`); never store per-entry runtime state in `hass.data[DOMAIN]` (use typed `HassKey` only for component-singleton state like frontend registration)
- Use `EntityDescription` dataclasses (`frozen=True, kw_only=True`) for static entity metadata
- Use `get_field()` for mixed camelCase/snake_case field access and `first_not_none()` when chaining numeric/boolean candidates so legitimate `0` or `False` values are never dropped
- `PARALLEL_UPDATES = 0` for coordinator-driven read-only platforms, `1` for action-based platforms

**Code organization principles:**

- Platform modules hold one section per data source; non-platform modules stay under about 2,000 lines (ADR-011)
- Add a new feature on an existing platform as a new `EntityDescription` table and class in that platform module, not as a new module (ADR-007)

### Multi-Coordinator Architecture

The integration uses a multi-coordinator pattern stored in `entry.runtime_data`:

- **Base Coordinator** (`coordinators/base.py`) — Abstract base with common patterns and error handling
- **Config Coordinator** (`coordinators/config.py` & `config_sections.py`) — Site, WiFi, network, firewall, ACL, DNS, voucher, and VPN configuration data (slow polling, 5 min)
- **Device Coordinator** (`coordinators/device.py`) — Network device and client metrics (30s polling)
- **Protect Coordinator** (`coordinators/protect.py`) — Camera, light, sensor, NVR, chime, viewer, doorlock, speaker, alarm hub, link station, fob, and liveview data (WebSocket + polling)
- **Facade Coordinator** (`coordinators/facade.py`) — Unified view combining Config, Device, Protect, InnerSpace, and Site Manager coordinators for entities and services; it holds no domain state and keeps only aggregation, delegated availability, the owner accessors, `async_refresh_or_raise` and pure reads and exports (`async_get_historical_clients`, `async_get_wake_history`, `async_export_network_rules`, the floor-plan download) (the voucher state and commands belong to the config coordinator, ADR-013)
- **Feature Coordinators:**
  - `coordinators/access.py` — UniFi Access doors, users, emergency status, and webhook events
  - `coordinators/innerspace.py` — UniFi InnerSpace floor plans and device placements
  - `coordinators/site_manager.py` — Shared cloud-key Site Manager console/ISP metrics
  - `coordinators/mobility.py` — UniFi Mobility workspaces and mobile routers
  - `coordinators/carrier_fabric.py` — UniFi Carrier Fabric organizations, subscribers, and fabrics (standalone config entry)

**Data flow:**

```
UniFi APIs → unifi-official-api Clients → Specialized Coordinators (Config / Device / Protect / Access / InnerSpace / Site Manager / Mobility) → Facade Coordinator → Entities
```

### Device Info & Home Assistant 2026.10+ Device Registry Rules

All entities must provide consistent `DeviceInfo` via the base entity classes in `entity.py` (or domain-specific bases) and adhere to Home Assistant 2026.8–2026.10+ device registry rules:

- **Single config entry per device (HA 2026.8+):** A device belongs to a single config entry and at most one config subentry. Devices are no longer merged across config entries.
- **Use `DeviceEntry.config_entry_id` and `DeviceEntry.config_subentry_id` (HA 2026.10+):** Never read `device.config_entries`, `device.config_entries_subentries`, or `device.primary_config_entry` (deprecated and log runtime warnings in 2026.10; raise in Core). Use `dr.async_get_device_and_config_entry_for_domain()` when resolving a device and its owning config entry for `DOMAIN`.
- **Use `via_device_id`, not `via_device` (HA 2026.8+):** `DeviceInfo["via_device"]` and `ATTR_VIA_DEVICE` are deprecated because identifiers are only unique per config entry. Resolve the parent device ID with `dr.async_get_device_id_by_identifier(hass, (DOMAIN, parent_id), config_entry_id=entry.entry_id)` or `parent_device.id` and pass `via_device_id`. The minimum supported Home Assistant is 2026.9, so no `via_device` fallback is needed.
- **Config-entry-scoped device lookups (HA 2026.8+):** `DeviceRegistry.async_get_device()` is deprecated. Use `dev_reg.async_get_device_by_identifier((DOMAIN, ...), entry.entry_id)` or `dev_reg.async_get_device_by_connection((CONNECTION_NETWORK_MAC, mac), entry.entry_id)` (the identifier or connection comes first). Inside an entity, prefer `self.device_entry`.
- **Read-only registry collections (HA 2026.9+):** `dev_reg.devices` and `dev_reg.child_devices` are read-only collections (iterate directly; never call `.get()`, `.values()`, `.keys()`, or `device_id in dev_reg.devices`). Never access `dev_reg.deleted_devices`. Use `dr.async_entries_for_config_entry(dev_reg, entry.entry_id)` for entry-scoped iteration.
- **No `default_*` keys in `DeviceInfo` (HA 2026.9+):** `default_manufacturer`, `default_model`, and `default_name` are deprecated and removed from `DeviceInfo` typing; use `manufacturer`, `model`, and `name`.
- **`DeviceInfo` is a dataclass (HA 2026.11+):** Always build device info with `DeviceInfo(...)` keyword arguments, never a dict literal. Never index, `.get()`, `.update()`, `dict()` or `**`-unpack a `DeviceInfo`: that mapping layer still works in 2026.11 but is being deprecated. Build optional fields in a plain `dict[str, Any]` first, then construct `DeviceInfo(**fields)  # type: ignore[typeddict-item, unused-ignore]` (the TypedDict before 2026.11 needs the ignore, the dataclass doesn't). Pass registry fields to `async_get_or_create` from that plain dict, and only the fields that are set: an explicit `None` clears the field on an existing device. Adopt `async_get_or_create_from_info` and attribute access once the minimum supported Home Assistant is 2026.11.
- **Reading device area (HA 2026.9+):** `DeviceEntry.suggested_area` was removed in 2026.9; read `DeviceEntry.area_id` instead (`DeviceInfo["suggested_area"]` remains valid when creating devices).
- **Entity attachment requirement (HA 2026.8+):** An entity may only attach `device_info` if it belongs to a config entry and has a `unique_id`.

### Integration Manifest

**Key fields in `manifest.json`:**

- `integration_type: "hub"` — Gateway to multiple devices
- `iot_class: "local_polling"` — Local polling with WebSocket/webhook push where available
- `dependencies: ["ffmpeg", "media_source", "stream", "wake_on_lan", "webhook", "websocket_api"]`
- `after_dependencies: ["http", "frontend", "lovelace"]` — Ensures custom card static paths and Lovelace resources register cleanly
- `requirements: ["segno==1.6.6", "unifi-official-api[talkback]==3.0.0"]` — QR code generation for WiFi/vouchers, and the UniFi API client library (the `talkback` extra declares PyAV for Protect talkback)

Keep the `unifi-official-api` pin identical in `manifest.json`, `pyproject.toml` `[project].dependencies`, and `requirements.txt`.

### Config Flow

The integration supports three connection modes:

- **Local** (`local`) — Host URL + API key (+ optional `verify_ssl`, optional UniFi Access token/port/webhook)
- **Remote** (`remote`) — Console ID + API key (via UniFi Site Manager API + optional Mobility discovery)
- **Carrier Fabric** (`carrier_fabric`) — Organization ID + API key

**Unique ID:** Resolved deterministically from stable console identity (`resolve_console_identity` gateway/Cloud Key MAC or Protect NVR MAC for `local`, cloud `console_id` for `remote`, and `carrier_<org_id>` or `carrier_key_<sha256[:16]>` for `carrier_fabric`) — never use raw IP addresses, mutable hostnames, or raw API keys.

**Flows:** User setup, discovery, reauth, reconfigure, and options flow.

### Entity Base Classes

All base classes live in `entity.py` (`UnifiAccessEntity`, `UnifiInnerSpaceEntity`, `UnifiMobilityRouterEntity`, `UnifiCarrierFabricEntity` and `UnifiSiteEntity` beside the two below):

- **`UnifiInsightsEntity`** — Network devices; availability gated on the snapshot's captured device availability (`owner_available(coordinator.data, "device")`) and `serviceable` / `is_device_serviceable(device_data)`; `is_device_online` remains the strict connectivity check
- **`UnifiProtectEntity`** — Protect devices; availability gated on the snapshot's captured Protect availability (`owner_available(coordinator.data, "protect")`) and `state == "CONNECTED"`

Read a facade record through its lookup in `data_transforms.py` (`network_device`, `protect_device`, `site_record`, ...) instead of indexing `coordinator.data`; read a section that has no lookup (`innerspace`, `internet_activity`, `vpn_connections`, ...) with `.get()`. Never write into the snapshot (ADR-013). `protect_collection`, `site_section` and `section_by_site` copy, so don't call them per entity or per record on every update; a per-update scan that needs a whole section walks it in place.

## Home Assistant Patterns (2026.10+ Standards)

**Config flow (`config_flow.py`):**

- Support user setup, reauth, reconfigure, and options flow
- Set `unique_id` and call `self._abort_if_unique_id_configured()` in setup/discovery flows (and `self._abort_if_unique_id_mismatch()` in reauth/reconfigure flows)
- Type schemas with `probatio` under `if TYPE_CHECKING: import probatio as vol` (`else: import voluptuous as vol`)
- Do **not** use `FlowHandler.show_advanced_options` or `context["show_advanced_options"]` (deprecated in 2026.5); use form sections for advanced settings
- Do **not** combine a config entry update listener with reloading flow helpers (`async_update_reload_and_abort`, `_abort_if_unique_id_configured(reload_on_update=True)`) — deprecated in 2026.6 and raises an error in 2026.12
- Use modern selectors (`SelectSelector`, `NumberSelector`, `TextSelector` with `pattern`, `DeviceClassSelector`, `StateClassSelector`, and `UnitOfMeasurementSelector` once the minimum supported Home Assistant is 2026.11) and always provide `data_description` in `strings.json`

See `.github/instructions/config_flow.instructions.md` for comprehensive patterns.

**Service actions (`services.py` & `services.yaml`):**

- Register service actions in `async_setup` (not `async_setup_entry`) so they are available and validatable even when no config entry is loaded (Quality Scale: `action-setup`)
- Route every action to the owning console with one `service_targets.resolve_*_call` resolver and run the owner command its typed result holds; never pick the first entry blindly. Target routing lives in `service_targets.py` (ADR-009 seam); `services.py` keeps schemas, handlers and registration
- Do **not** pass `hass` to service extraction helpers (`async_extract_config_entry_ids(service_call)`, `async_extract_entity_ids(service_call)`, `async_extract_entities(entities, service_call)`, `@verify_domain_control(DOMAIN)`) — passing `hass` was removed in HA 2026.10
- Do **not** use `device` filters inside `target:` selectors in `services.yaml` (removed in HA 2026.11)
- Raise `ServiceValidationError` for invalid user input and `HomeAssistantError` for device/API failures, using `translation_domain=DOMAIN` and `translation_key=...` with matching entries under `exceptions` in `strings.json` (Quality Scale: `action-exceptions`, `exception-translations`)

See `.github/instructions/service_actions.instructions.md` for service patterns.

**Coordinator (`coordinators/`):**

- Entities → Coordinator → API Client (never skip layers)
- One owner per write command (ADR-010): the coordinator that owns the data runs the call through `async_execute_command` (Access keeps `_command_error` and Carrier Fabric its conflict retry and typed errors), raises translated errors, publishes a copy-on-write overlay and reconciles. Entities and service actions call it directly (`coordinator.config_coordinator`, `coordinator.device_coordinator`, `coordinator.require_protect_coordinator()`, or the Access and Carrier Fabric coordinators); never look a command up by name, fall back to an API client, mutate cached data, or refresh from an entity
- Raise `ConfigEntryAuthFailed` (triggers reauth) or `UpdateFailed` (retry; `DataUpdateCoordinator` automatically logs once when unavailable and once when restored, satisfying `log-when-unavailable`)
- Use `async_config_entry_first_refresh()` for first update

See `.github/instructions/coordinator.instructions.md` for details.

**Entities & Lifecycle Hooks (HA 2026.10 / 2026.11):**

- Inherit from the custom base class first, then the platform entity class (`class MySensor(UnifiInsightsEntity, SensorEntity):`)
- Read from `coordinator.data`, never call API directly
- Use `EntityDescription` for static metadata (`has_entity_name = True`, `translation_key`, `device_class`, `state_class`, `entity_category`, `entity_registry_enabled_default`)
- **Lifecycle hooks (HA 2026.10+):**
  - Use `async_added_to_hass` for normal subscriptions/listeners and register cleanup via `self.async_on_remove(...)` (Quality Scale: `entity-event-setup`)
  - Use `async_prepare_to_add_to_hass` (new in 2026.10) only when work must happen before registry processing; never override `add_to_platform_start`, `add_to_platform_finish`, or `add_to_platform_abort`
  - **In-place `entity_id` changes (HA 2026.11+):** Any custom entity class that implements `async_prepare_to_add_to_hass`, `async_added_to_hass`, or `async_will_remove_from_hass` must also implement `@callback def async_entity_id_changed(self, old_entity_id: str) -> None:` (calling `super().async_entity_id_changed(old_entity_id)`) to opt in to in-place `entity_id` updates
  - Never read `RestoreStateData.last_states` directly (removed in 2026.11); use `RestoreEntity.async_get_last_state()` or `RestoreStateData.async_get_stored_state(entity_id)`

See `.github/instructions/entities.instructions.md` for entity patterns.

**Frontend & Custom Lovelace Cards (`frontend/` & `frontend.py`):**

- Six card bundles (`topology-card.js`, `site-health-card.js`, `internet-activity-card.js`, `performance-card.js`, `protect-status-card.js`, `timeline-card.js`) are compiled from `frontend/src/` into `custom_components/unifi_insights/frontend/` and served by `frontend.py` via `hass.http.async_register_static_paths` with content-digest cache busting (`?v=<version>-<hash>`)
- **HA 2026 UI/UX conventions:**
  - Build self-contained Lit components using `--ha-*` CSS custom properties/tokens; avoid deprecated `--mdc-*` or `--switch-*` tokens (HA 2026.4–2026.8 migrated components to Web Awesome, using short size values `xs`, `s`, `m`, `l`, `xl`)
  - Implement `getGridOptions()` for Sections view compatibility (`columns` as multiples of 3: `3`, `6`, `9`, `12`, or `"full"`, plus `rows`, `min_rows`, `min_columns`)
  - Use `hass.formatEntityName(stateObj, config.name)` when formatting entity display names
  - Support `prefers-reduced-motion`, ARIA attributes, and keyboard accessibility (`tabindex`, focus states, axe-core checks)

**Repairs (`repairs.py`):**

- Use `ir.async_create_issue()` with `translation_key` and `ir.IssueSeverity`
- Implement `RepairsFlow` for fixable issues and delete issues on resolution

See `.github/instructions/repairs.instructions.md` for comprehensive patterns.

**Entity availability:**

- Gate availability on both coordinator health, read from the snapshot (`owner_available` / `site_section_available` in `data_transforms.py`, plus `last_update_success`), and service eligibility (`serviceable` / `is_device_serviceable` for Network devices, `state == "CONNECTED"` for Protect). `is_device_online` remains the strict Network connectivity check
- Never raise exceptions from `@property` methods; return `None` when value is unknown

**Diagnostics (`diagnostics.py`):**

- **CRITICAL:** Always use the three-pass redaction pipeline (`TO_REDACT`, `CLIENT_TO_REDACT`/`WIFI_TO_REDACT`, and `_anonymize_macs()`) via `async_redact_data()`

## Validation and Testing

**Before committing, run:**

```bash
pre-commit run --all-files   # Full validation (ruff, codespell, yamllint, ./script/mypy)
./script/lint                # Auto-format and fix linting
./script/mypy                # Full strict mypy check with HA venv
pytest                       # Run unit tests (95% total coverage enforced locally)
cd frontend && npm ci        # Dashboard cards: install dependencies
npm run lint && npm run typecheck && npm test -- --coverage
npm run build && npm run size # Rebuild custom_components/unifi_insights/frontend/*.js — commit them
```

**Generate code that passes these checks on first run.** As an AI agent, you should produce higher quality code than manual development:

- Type hints are trivial for you to generate
- Async patterns are well-known to you
- Import management is automatic for you
- Naming conventions can be applied consistently

Aim for zero validation errors in generated code.

### Error Recovery Strategy

**When validation fails:**

1. **First attempt** — Fix the specific error reported by the tool
2. **Second attempt** — If it fails again, reconsider your approach
3. **Third attempt** — If still failing, ask for clarification rather than looping indefinitely
4. **After 3 failed attempts** — Stop and explain what you tried and why it's not working

**When gathering context:**

- Start with search (1-2 queries maximum)
- Read 3-5 most relevant files based on results
- If still unclear, read 2-3 more specific files
- **After ~10 file reads, you should have enough context** — make a decision or ask for clarification
- Don't fall into infinite research loops

## Testing

**Test structure:**

- `tests/` mirrors `custom_components/unifi_insights/`: `tests/<pkg>/test_<module>.py`, with a feature suffix only once a module's tests exceed about 2,000 lines; shared test data lives in `tests/fixtures/` (ADR-007)
- Use fixtures for common setup (Home Assistant mock, coordinator, etc.)
- Mock external API calls
- Every module must be ≥95% (branch coverage enabled, checked per file in CI with `python script/check_coverage_per_file.py coverage.xml --fail-under 95`)

Local `pytest` enforces only the 95% total coverage threshold. To check per-file coverage locally, run `pytest --cov-report=xml`, then `python script/check_coverage_per_file.py coverage.xml --fail-under 95`.

**Running tests:**

```bash
pytest                       # All tests with coverage
pytest tests/test_sensor.py  # Specific test file
pytest -vvs                  # Verbose output
pytest --no-cov              # Without coverage
```

**Important:** Add or update tests whenever your change affects behavior that should be covered by applicable Quality Scale rules (for example config flow, coordinator updates, entities, services, or error handling paths). If you intentionally defer test work, state the gap and rationale.

See `.github/instructions/tests.instructions.md` for comprehensive testing patterns.

## Breaking Changes

**Always warn the developer before making changes that:**

- Change entity IDs or unique IDs (users' automations will break)
- Modify config entry data structure (existing installations will fail)
- Change state values or attributes format (dashboards and automations affected)
- Alter service call signatures (user scripts will break)
- Remove or rename config options (users must reconfigure)

**Never do without explicit approval:**

- Removing config options (even if "unused")
- Changing service parameters or return values
- Modifying how data is stored in config entries
- Renaming entities or changing their device classes
- Changing unique_id generation logic

**How to warn:**

> "This change will modify the entity ID format from `sensor.device_name` to `sensor.device_name_sensor`. Existing users' automations and dashboards will break. Should I proceed, or would you prefer a migration path?"

**When breaking changes are necessary:**

- Document the breaking change in commit message (`BREAKING CHANGE:` footer)
- Consider providing migration instructions
- Suggest version bump
- Update CHANGELOG.md

## File Changes

**Scope Management:**

**Single logical feature or fix:**

- Implement completely even if it spans 5-8 files
- Example: New sensor needs entity class + platform init + translations → implement all together

**Multiple independent features:**

- Implement one at a time
- After completing each feature, suggest committing before proceeding to the next

**Large refactoring (>10 files or architectural changes):**

- Propose a plan first before starting implementation
- Get explicit confirmation from developer

**Translation strategy:**

- Keep `strings.json` in sync with `custom_components/unifi_insights/translations/en.json` for user-facing entities, config flow steps, repair issues, service actions, and `exceptions`
- NEVER update non-English language files in `translations/` automatically

## UniFi-Specific Considerations

- **UniFi Developer Portal:** Consult [developer.ui.com](https://developer.ui.com/) for official UniFi API documentation, endpoint specifications, and latest developer capabilities
- **Machine-readable API references (use these, not the HTML docs):** the portal's documentation pages are JavaScript-rendered, so a plain HTTP fetch returns an empty app shell — an agent gets HTTP 200 and no API content. These endpoints return real content and are the ones to fetch:
  - `https://developer.ui.com/llms.txt` — root index: which APIs exist and their current versions
  - `https://developer.ui.com/{service}/{version}/llms.txt` — full endpoint list for one service
  - `https://developer.ui.com/{service}/{version}/openapi.json` — request/response schemas
  - `https://developer.ui.com/network/v10.6.106/ai-gettingstarted.md` — agent-oriented onboarding for the Network API
  - Current versions as of 2026-10-16: Network `v10.6.106`, Protect `v7.3.70`, Mobility `v1.0.0`, InnerSpace `v1.3.23`, Site Manager `v1.0.0`, Carrier Fabric `v1.0.0`. Always resolve versions from the root index (`llms.txt`) rather than hardcoding them.
- **API Rate Limiting:** Be mindful of UniFi controller API rate limits when setting polling intervals
- **Device Discovery:** Handle dynamic device discovery as UniFi networks can change
- **Connection Resilience:** UniFi controllers may restart or become temporarily unavailable
- **Multiple Sites & Consoles:** Support multi-site and multi-console UniFi deployments; always scope device and coordinator lookups to the owning config entry/console
- **Protect Integration:** Protect entities are handled separately from network devices via `UnifiInsightsProtectCoordinator` and `UnifiProtectEntity`
- **camelCase Tolerance:** UniFi API uses camelCase; use `get_field()` and `first_not_none()` for field access
- **Data Transforms:** Extend `data_transforms.py` (or `innerspace_transforms.py`) when adding new API fields

## Tool Parallelization

**Safe to call in parallel:**

- Multiple file read operations (different files)
- File search + file read + content search (independent read-only operations)

**Never call in parallel:**

- Multiple terminal commands (execute sequentially, wait for output)
- Multiple edits on the same file

**Best practices:**

- Batch independent read operations together
- After gathering context in parallel, provide brief progress update before proceeding
- Terminal commands must always be sequential

## Additional Resources

- [UniFi Developer Portal](https://developer.ui.com/) — Official UniFi API documentation and developer resources
- [UniFi API root index (`llms.txt`)](https://developer.ui.com/llms.txt) — machine-readable list of APIs and versions; the HTML pages are JS-rendered and unreadable to agents
- [UniFi Network OpenAPI spec](https://developer.ui.com/network/v10.6.106/openapi.json) — authoritative Network schemas
- [UniFi Protect OpenAPI spec](https://developer.ui.com/protect/v7.3.70/openapi.json) — authoritative sensor/camera/security schemas
- [Home Assistant Developer Docs](https://developers.home-assistant.io/) — Primary reference
- [Home Assistant Developer Blog](https://developers.home-assistant.io/blog) — Latest deprecations, entity lifecycle hooks, device registry rules, and frontend component updates
- [Integration Quality Scale Rules](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules)
- [Integration Quality Scale Checklist](https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist)
- [Architecture Docs](https://developers.home-assistant.io/docs/architecture_index)
- [Ruff Rules](https://docs.astral.sh/ruff/rules/) — Linter documentation
- [pytest Documentation](https://docs.pytest.org/) — Testing framework
- See `CONTRIBUTING.md` for contribution guidelines

## Issue and Pull Request Governance

These rules apply to every contributor, including AI coding agents. The full org-wide rules are in
[GOVERNANCE.md](https://github.com/tinkerologie/.github/blob/main/GOVERNANCE.md).

1. **Issue first.** Open an issue with the bug or enhancement template before opening a pull
   request. Keep the title prefix `[Bug]:` or `[Enhancement]:`, followed by a space and the title.
   Automation applies the `bug` or `enhancement` label when it can tell the type, and CodeRabbit
   then posts a Coding Plan on the issue. Implement from that plan. The labeler skips issues labeled
   `question`, `duplicate`, `invalid`, `wontfix`, or `no-type`, and never re-adds a type label that
   a maintainer removed.
2. **Link the issue.** In the PR's **Related Issues** section, write `Closes #<number>` (or `Fixes`
   or `Resolves`) when the PR completely resolves the issue; these keywords close it on merge. Use
   `Part of #<number>` or `Related to #<number>` for partial work. The linked issue must exist; an
   issue in another public repository (`owner/repo#123`) is fine. Only authors with write access or
   higher (maintainers) may write `None`, for housekeeping such as CI,
   documentation, or dependency updates; PR governance rejects `None` from everyone else.
3. **One change at a time.** Contributors without write access can have at most 2 open, non-draft
   pull requests here. GitHub enforces this limit, and draft pull requests do not count toward it.
   Do not open PRs that edit the same files as another open PR, such as `CHANGELOG.md`,
   translations, `README.md`, or generated bundles. Wait for the first to merge, rebase, then
   continue.
4. **Act on the Tinkerologie-Bot report.** When PR governance fails, Tinkerologie-Bot posts one comment
   listing every problem as a numbered item. Fix each item by editing the PR description or pushing
   the missing change; the check re-runs and the comment updates to passed.
5. **Rebase before review.** Rebase on `main` before marking a PR ready for review.
6. **Resolve conflicts first.** When a PR gets the `conflicts` label, rebase on `main`,
   resolve the conflicts (keep every `CHANGELOG.md` entry under `[Unreleased]`), re-run the tests,
   and force-push before doing any other work.
7. **Coverage.** `codecov/patch` must pass: at least 90% of added or changed lines must be
   covered by tests. The gate is the percentage; still, add tests for the uncovered lines the
   Codecov PR comment lists, and explain in the PR description any line you leave uncovered. Do
   not lower coverage targets or add coverage ignore rules to make it pass.
8. **Stale items.** Issues and PRs with no activity for 14 days are labeled `stale` and closed 30
   days later unless there is new activity. These labels are exempt:
   - Issues: `pinned`, `security`, `confirmed`, `work-in-progress`, `priority: critical`, `priority: high`.
   - Pull requests: `pinned`, `security`, `work-in-progress`.
