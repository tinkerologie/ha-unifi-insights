# Architectural and design decisions

This document is the index of significant architectural and design decisions for the UniFi Insights integration. Each entry summarizes one decision and links to its full Architecture Decision Record (ADR) in [`adr/`](./adr/).

## Format

Each ADR follows the template from [ADR-001](./adr/001-record-architecture-decisions.md):

- **Date**: when the decision was made or last updated
- **Status**: Proposed, Accepted, Deprecated, or Superseded by ADR-NNN
- **Context**: the problem, and the Home Assistant or UniFi API constraints that apply
- **Decision**: what was decided
- **Alternatives considered**: options with pros and cons
- **Consequences**: positive, negative, and neutral impacts

An accepted ADR is never edited to change its meaning. A new ADR supersedes it instead, so the log shows how long each decision governed the code.

---

## Decision log

| ADR                                                                  | Title                                                         | Status                | Date       |
| -------------------------------------------------------------------- | ------------------------------------------------------------- | --------------------- | ---------- |
| [001](./adr/001-record-architecture-decisions.md)                    | Record architecture decisions                                 | Accepted              | 2026-10-16 |
| [002](./adr/002-multi-coordinator-architecture-behind-a-facade.md)   | Multi-coordinator architecture behind a facade                | Accepted              | 2026-10-16 |
| [003](./adr/003-vendor-the-unifi-api-client.md)                      | Vendor the UniFi API client inside the integration            | Superseded by ADR-008 | 2026-10-16 |
| [004](./adr/004-stable-console-identity-for-unique-ids.md)           | Stable console identity for config entry unique IDs           | Accepted              | 2026-10-16 |
| [005](./adr/005-bundled-dashboard-cards-with-versioned-contracts.md) | Bundled dashboard cards with versioned WebSocket contracts    | Accepted              | 2026-10-16 |
| [006](./adr/006-three-layer-diagnostics-redaction.md)                | Three-layer diagnostics redaction                             | Accepted              | 2026-10-16 |
| [007](./adr/007-flat-home-assistant-module-layout.md)                | Flat Home Assistant module layout                             | Proposed              | 2026-10-16 |
| [008](./adr/008-depend-on-unifi-official-api-from-pypi.md)           | Depend on `unifi-official-api` from PyPI                      | Accepted              | 2026-10-19 |
| [009](./adr/009-amend-flat-module-layout.md)                         | Amendments to the flat module layout for the deepening issues | Accepted              | 2026-10-19 |
| [010](./adr/010-one-owner-per-write-command.md)                      | One owner per write command                                   | Accepted              | 2026-10-19 |
| [011](./adr/011-platform-modules-and-flat-layout-homes.md)           | Platform module size and homes for the remaining helpers      | Accepted              | 2026-10-20 |

### Multi-coordinator architecture behind a facade

One `DataUpdateCoordinator` per data domain (Config 5 min, Device 30 s, Protect 30 s plus WebSocket, Access 30 s plus webhook, InnerSpace 5 min, Site Manager 10 min, Mobility 5 min, Carrier Fabric 5 min), aggregated by a push-only facade coordinator that entities and services read from. Keeps expensive configuration polls off the 30 s path and gives every data source its own error and recovery handling. [ADR-002](./adr/002-multi-coordinator-architecture-behind-a-facade.md)

### Vendor the UniFi API client inside the integration (superseded)

From release 2026.3.0 the upstream `unifi-official-api` package was copied into `custom_components/unifi_insights/api/` so contract fixes could ship without waiting for a PyPI release. Superseded by ADR-008. [ADR-003](./adr/003-vendor-the-unifi-api-client.md)

### Stable console identity for config entry unique IDs

`unique_id` is the gateway or Cloud Key MAC (falling back to the Protect NVR MAC) for local entries, the cloud `console_id` for remote entries, and `carrier_<org_id>` for Carrier Fabric. One module, `console_identity.py`, answers the question for both the config flow and setup, so the two can never drift. [ADR-004](./adr/004-stable-console-identity-for-unique-ids.md)

### Bundled dashboard cards with versioned WebSocket contracts

The six Lovelace cards are compiled from `frontend/` and served by the integration itself with content-digest cache busting. Cards talk to `websocket_api.py` through versioned TypedDict snapshot contracts, and topology node ids are per-entry HMACs of the MAC. [ADR-005](./adr/005-bundled-dashboard-cards-with-versioned-contracts.md)

### Three-layer diagnostics redaction

Diagnostics pass through `TO_REDACT`, then `CLIENT_TO_REDACT` / `WIFI_TO_REDACT`, then `_anonymize_macs()`. A new field is safe by default because the layers run on the whole payload, not on a hand-picked list of keys. [ADR-006](./adr/006-three-layer-diagnostics-redaction.md)

### Flat Home Assistant module layout (proposed)

Consolidate 57 root modules into 33 by moving every base entity into `entity.py`, every platform entity into its standard platform module, pure helpers into `data_transforms.py`, dashboard builders into `dashboard_snapshots.py`, and topology helpers into `topology.py`. `coordinators/` is the only subpackage now that #345 removed the vendored `api/` (199 to 71 Python files, and 48 after #341). No module goes over about 2,000 lines: when one grows past that, deepen it rather than split it, and land the file moves after #337 to #339. Tracked in [#341](https://github.com/tinkerologie/ha-unifi-insights/issues/341), part of [#343](https://github.com/tinkerologie/ha-unifi-insights/issues/343). [ADR-007](./adr/007-flat-home-assistant-module-layout.md)

### Depend on `unifi-official-api` from PyPI

`manifest.json` pins `unifi-official-api[talkback]==2.0.0`, published to PyPI by the upstream release workflow at a tagged release, and the vendored `api/` package (128 files, about 17,400 lines) is deleted. This meets the `dependency-transparency` rule. API contract fixes now go to `tinkerologie/unifi-official-api` first and reach the integration through a pin bump; no local shims. Implemented in [#345](https://github.com/tinkerologie/ha-unifi-insights/issues/345). [ADR-008](./adr/008-depend-on-unifi-official-api-from-pypi.md)

### Amendments to the flat module layout

ADR-007 is amended for the deepening issues #337, #338 and #339. `get_field()` and `first_not_none()` live in `data_transforms.py`, re-exported by `entity.py`. `data_transforms.normalize_mac()` is the canonical MAC parser for reading device data, and four identity-specific MAC policies stay separate and tested. `service_targets.py` is an allowed 34th root module. `coordinators/protect.py` has a size exception capped at 3,500 lines until #340 splits it. [ADR-009](./adr/009-amend-flat-module-layout.md)

### One owner per write command

Each write belongs to the coordinator that owns the data it changes: config (rules, Wi-Fi, guest authorisation), device (restart, PoE, outlets, clients), Protect (cameras, lights, chimes, viewers, alarms, sirens, talkback), Access and Carrier Fabric (unchanged), and the facade only for vouchers and `async_refresh_or_raise`. The owner runs the call through the shared `async_execute_command` in `coordinators/base.py`, raises translated errors, publishes a copy-on-write overlay and applies one recorded reconciliation policy; a failed refresh never fails a successful write. The config coordinator publishes the legacy site-name mapping and owns the only resolver, which never guesses `"default"`. Entities and services call owners with typed calls, replacing the string dispatch and its fallbacks. The Protect commands fit inside ADR-009's 3,500-line cap. Implemented in [#338](https://github.com/tinkerologie/ha-unifi-insights/issues/338). [ADR-010](./adr/010-one-owner-per-write-command.md)

### Platform module size and homes for the remaining helpers

The 17 platform modules are exempt from ADR-007's 2,000-line budget and are bounded by role instead: setup, description tables, entity classes and discovery, in one section per data source. Pure value extraction goes to `data_transforms.py` and base classes to `entity.py`. `sensor.py` is about 3,700 lines after the satellites merge. `async_get_device_entry()` and the site entity identity helpers move to `console_identity.py` (an `entity.py` home would create an import cycle with the coordinators), `get_port_label()` and `is_doorbell_camera_model()` to `data_transforms.py`. `data_transforms.py` may import `homeassistant.util.*` and only the pure integration modules, enforced by a test. Decided in the design review of [#341](https://github.com/tinkerologie/ha-unifi-insights/issues/341). [ADR-011](./adr/011-platform-modules-and-flat-layout-homes.md)

## Future considerations

### Facade coordinator

**Status:** kept

`UnifiInsightsFacadeCoordinator` exists so entities and services read one `data` mapping. It is the largest coordinator module after Protect and duplicates some lookups that the specialized coordinators already own. Once ADR-007 lands and entities use declarative `EntityDescription` fields, revisit whether entities can subscribe to their owning coordinator directly.

### Repairs

**Status:** not implemented

The integration does not create repair issues, and the unused repair-flow scaffold was removed (#334). Developers should add repair issues and matching translations under `"issues"` in `strings.json` when a user-fixable condition requires them. Authentication failures continue to use standard reauthentication rather than repair issues.

### Discovery

**Status:** not implemented

Consoles are added manually through the user step. Zeroconf, DHCP, and SSDP matchers in `manifest.json` are empty; add them (and a `discovery-update-info` path) only if UniFi consoles can be matched reliably without false positives.

---

## Decision review

Review these decisions when:

- UniFi publishes a new major API version (resolve versions from `https://developer.ui.com/llms.txt`)
- Home Assistant Core changes an Integration Quality Scale rule or deprecates a pattern used here
- A module exceeds roughly 2,000 lines or a package gains a third subpackage
- User feedback suggests an architectural problem rather than a bug
