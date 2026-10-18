# ADR-007: Flat Home Assistant module layout

**Date:** 2026-10-16 (revised 2026-10-17: size budget and `api/` end state)
**Status:** Proposed (implementation tracked in [#341](https://github.com/tinkerologie/ha-unifi-insights/issues/341), part of [#343](https://github.com/tinkerologie/ha-unifi-insights/issues/343); originally [#330](https://github.com/tinkerologie/ha-unifi-insights/issues/330))

## Context

`custom_components/unifi_insights/` has 57 top-level Python modules plus 16 in `coordinators/`, not counting the vendored `api/` package. The growth came from adding each feature as its own satellite file (`voucher_sensor.py`, `mobility_entity.py`, `network_rule_switch.py`, six dashboard snapshot builders, three topology files), which hides where a given entity lives and duplicates base-class and `EntityDescription` boilerplate. Several files were dead (the legacy `coordinator.py`, transforms, 110 constants, the repairs scaffold, and the `SCAN_INTERVAL_NORMAL` alias, for 111 constants removed in total; removed in #334), and five modules inside `coordinators/` contain no coordinator.

Home Assistant's guidance is explicit:

- The [integration file structure](https://developers.home-assistant.io/docs/creating_integration_file_structure) places each platform at `<domain>/<platform>.py` and the coordinator in `coordinator.py`.
- The [`common-modules`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/common-modules/) rule places base entities in `entity.py` so "it is easier to find" them.
- Home Assistant Core's two UniFi integrations show the accepted shapes: `unifiprotect` is flat (`data.py`, `utils.py`, `views.py` beside the platforms); `unifi` keeps one `hub/` subpackage for its multi-part hub.

`AGENTS.md` forbids `helpers/`, `common/`, `shared/`, `lib/`, and any new top-level package without approval. #330 first described the move-only consolidation. It was closed and split into the sub-issues of #343, with the file moves in #341. This ADR records the target layout so it doesn't drift again.

## Decision

Adopt a flat Home Assistant layout with exactly two subpackages, `coordinators/` and the vendored `api/`. `api/` is temporary: its removal is part of this layout's end state (see [End state](#end-state-no-vendored-api-client)). Every module has one of five roles, and a new file is allowed only if it does not fit an existing module of the same role.

### Target layout

```text
custom_components/unifi_insights/
├── __init__.py                 # setup/unload/migrate; UnifiInsightsData, CarrierFabricData, typed ConfigEntry aliases
├── manifest.json
├── quality_scale.yaml          # Quality Scale rule tracking, added by #336 (no tier claim in manifest.json)
├── py.typed
│
│   # Setup and identity
├── const.py                    # live constants only
├── config_flow.py              # user, discovery, reauth, reconfigure, options (local / remote / carrier_fabric)
├── console_identity.py         # console identity + site-entity identity migration  ← helpers.py (identity)
├── probe.py                    # capability probing
│
│   # Common modules (Quality Scale common-modules)
├── entity.py                   # ALL base entities: UnifiInsightsEntity, UnifiProtectEntity, UnifiAccessEntity,
│                               #   UnifiInnerSpaceEntity, UnifiMobilityRouterEntity, UnifiCarrierFabricEntity,
│                               #   UnifiSiteEntity (new) + get_field(), first_not_none(), async_get_device_entry()
├── data_transforms.py          # canonical normalize_mac(); LED level, storage telemetry, voucher state,
│                               #   doorbell model check, /stat/device normalization  ← led_level.py,
│                               #   storage_telemetry.py, coordinators/voucher_state.py, helpers.py (transforms)
├── innerspace_transforms.py    # InnerSpace floor plan and MAC correlation (unchanged)
├── client_wake.py              # pure Wake-on-LAN helpers shared by facade and button (unchanged)
│
│   # Platforms (one file per Home Assistant platform, 17 files)
├── alarm_control_panel.py
├── binary_sensor.py            # ← storage_entity.py, protect_security_entity.py, mobility_entity.py (binary parts)
├── button.py
├── camera.py
├── cover.py
├── device_tracker.py           # ← mobility_entity.py (location tracker)
├── event.py                    # ← network_rule_audit_event.py
├── image.py
├── light.py
├── lock.py
├── media_player.py
├── number.py
├── select.py
├── sensor.py                   # ← voucher_sensor.py, site_internet_activity_sensor.py, carrier_fabric_sensor.py,
│                               #   innerspace_entity.py, storage_entity.py, protect_security_entity.py,
│                               #   mobility_entity.py (sensor parts)
├── siren.py
├── switch.py                   # ← network_rule_switch.py
├── update.py
│
│   # Actions, dashboards, push channels, support
├── services.py / services.yaml
├── websocket_api.py
├── dashboard_snapshots.py      # ← site_health.py, performance.py, timeline.py, protect_snapshot.py,
│                               #   internet_activity_snapshot.py, dashboard_contract_utils.py
├── topology.py                 # ← topology_contract.py, topology_keys.py
├── frontend.py
├── access_webhook.py
├── diagnostics.py
├── icons.json, strings.json, translations/en.json
│
├── coordinators/               # one DataUpdateCoordinator subclass per module (plus its direct helpers)
│   ├── __init__.py             # exports every coordinator, including UnifiInsightsAccessCoordinator
│   ├── base.py
│   ├── config.py
│   ├── config_sections.py      # ← coordinators/internet_activity.py
│   ├── device.py               # pure normalization helpers move to data_transforms.py
│   ├── protect.py              # REST polling and orchestration
│   ├── protect_ws.py           # NEW: WebSocket stream adapters and health state machine
│   ├── protect_events.py       # NEW: sensor/door latching, motion/ring/smart-detect timers
│   ├── facade.py
│   ├── access.py
│   ├── innerspace.py
│   ├── site_manager.py
│   ├── mobility.py
│   ├── carrier_fabric.py
│   └── rule_audit.py           # ← coordinators/rule_export.py
│
├── api/                        # vendored client package (unchanged; see ADR-003)
└── frontend/                   # built card bundles (unchanged; see ADR-005)
```

Deleted outright: `coordinator.py` (dead legacy coordinator; removed in #334), `repairs.py` (scaffold never wired; removed in #334), `carrier_fabric_data.py` (into `__init__.py`), `helpers.py`, `led_level.py`, `storage_telemetry.py`, the 12 entity satellites, the 6 dashboard builders, 2 topology helpers, and 3 `coordinators/` helper modules.

| Area                       | Before | After |
| -------------------------- | ------ | ----- |
| Root `.py` modules         | 57     | 33    |
| `coordinators/` modules    | 16     | 15    |
| Subpackages (non-vendored) | 1      | 1     |

### Size budget

No module goes over roughly **2,000 lines**. When a module grows past that, deepen it: remove duplicated logic behind one interface (as #337, #338 and #339 do for value extraction, the command path and service routing). Do not split it into new satellite files. A split is allowed only along a seam recorded in an ADR, such as the `protect_ws.py` / `protect_events.py` split above.

Several modules are already over budget: `coordinators/protect.py` (3,070), `services.py` (3,000), `sensor.py` (2,430). Merging the satellites into them first would push `sensor.py` towards 4,000 lines. So the moves in #341 land **after** #337, #338 and #339, and they bring their own reductions: one `UnifiInsightsNetworkRuleSwitch` for the 7 rule switch families (about 420 lines) and one outlet switch class (about 200).

### End state: no vendored API client

Home Assistant requires device communication to live in a library published on PyPI ([development checklist](https://developers.home-assistant.io/docs/development_checklist), [creating a platform](https://developers.home-assistant.io/docs/creating_platform_index)). The vendored `api/` package (128 files, about 17,400 lines) is 128 of the integration's 201 Python files today. [tinkerologie/unifi-official-api#41](https://github.com/tinkerologie/unifi-official-api/issues/41) moves it back upstream as 2.0.0. ADR-008 records that change and supersedes ADR-003. After it lands, `coordinators/` is the only subpackage.

| Python files    | Today | After #341 | After unifi-official-api#41 |
| --------------- | ----- | ---------- | --------------------------- |
| Root modules    | 57    | 33         | 33                          |
| `coordinators/` | 16    | 15         | 15                          |
| `api/`          | 128   | 128        | 0                           |
| **Total**       | 201   | 176        | 48                          |

### Module roles

1. **Platform** (`<platform>.py`): `async_setup_entry`, `PARALLEL_UPDATES`, the platform's `EntityDescription` tables, and every entity class for that platform, regardless of which UniFi application feeds it. Sections inside the file are ordered by data source (Network, Protect, Access, InnerSpace, Mobility, Carrier Fabric).
2. **Common** (`entity.py`, `data_transforms.py`, `innerspace_transforms.py`, `client_wake.py`): base classes and pure functions with no I/O. `data_transforms.py` is the only home for `normalize_mac()`; duplicates in `topology`, `console_identity`, `coordinators/device`, and `services` are removed.
3. **Coordinator** (`coordinators/<name>.py`): exactly one `DataUpdateCoordinator` subclass plus helpers used only by it. A module with no coordinator does not belong in the package.
4. **Interface** (`config_flow.py`, `services.py`, `websocket_api.py`, `frontend.py`, `access_webhook.py`, `diagnostics.py`, `dashboard_snapshots.py`, `topology.py`): Home Assistant-facing surfaces named after the Home Assistant feature they implement.
5. **Setup and identity** (`__init__.py`, `const.py`, `console_identity.py`, `probe.py`).

A new module is justified only when it introduces a new platform, a new coordinator, or a new Home Assistant surface. A new feature on an existing platform is a new `EntityDescription` table and class in that platform file.

### Test layout (proposed, separate PR)

`tests/` is 110 flat files and 119,000 lines, with `test_coordinators.py` alone at 11,600 lines. Mirror the package instead:

```text
tests/
├── conftest.py
├── fixtures/                   # unchanged
├── api/                        # one test module per vendored client subpackage
│   ├── test_network_client.py, test_network_endpoints.py, test_network_models.py
│   ├── test_protect_client.py, test_protect_endpoints.py, test_protect_websocket.py, test_protect_talkback.py
│   ├── test_access_*.py, test_innerspace_client.py, test_site_manager_client.py,
│   │   test_mobility_client.py, test_carrier_fabric_client.py
│   └── test_base.py
├── coordinators/               # one test module per coordinator module
│   ├── test_base.py, test_config.py, test_device.py, test_protect.py, test_protect_ws.py,
│   │   test_protect_events.py, test_facade.py, test_access.py, test_innerspace.py,
│   │   test_site_manager.py, test_mobility.py, test_carrier_fabric.py, test_rule_audit.py
└── test_<module>.py            # one per root module: test_sensor.py, test_switch.py, test_config_flow.py, ...
```

Rule: `tests/<pkg>/test_<module>.py` mirrors `custom_components/unifi_insights/<pkg>/<module>.py`. A test module may be split with a feature suffix (`test_sensor_ports.py`) only when it exceeds about 2,000 lines. Feature-named test files that don't match a module (`test_access_*.py`, `test_carrier_fabric_*.py`, `test_mobility_*.py` and similar) are folded into the test module of the code they cover. The per-file coverage gate stays at 95%.

### Documentation layout

```text
docs/development/
├── ARCHITECTURE.md             # current system, links to ADRs
├── DECISIONS.md                # index and summaries
└── adr/NNN-title.md            # full records (ADR-001)
```

## Alternatives considered

### Option A: Feature subpackages (`protect/`, `access/`, `mobility/`, each with its own platforms)

- Pros: each UniFi application is self-contained.
- Cons: Home Assistant discovers platforms at `<domain>/<platform>.py`, so every feature package would still need a shim per platform; `AGENTS.md` and the `common-modules` rule both reject it; it multiplies files instead of reducing them.

### Option B: Keep satellites, only delete dead code

- Pros: smallest diff.
- Cons: leaves 29 files defining entities for 17 platforms and the duplicated base classes that caused the drift.

### Option C: Flat layout with `coordinators/` and `api/` (chosen)

- Pros: matches the Home Assistant file structure and `common-modules`; every entity is found by platform; one place for each helper; precedent in Core's `unifi` (`hub/`) and `unifiprotect` (flat).
- Cons: merging the satellites makes the platform files bigger. The [size budget](#size-budget) handles this: duplication is removed before the merges, so no module ends up over about 2,000 lines.

## Consequences

### Positive

- A contributor looking for "the storage sensor" opens `sensor.py`; for "the Protect WebSocket", `coordinators/protect_ws.py`.
- One `normalize_mac()`, one `get_field()`, one stale-device helper on `UnifiBaseCoordinator`.
- The dead `coordinator.py` disappears (completed in #334), so the package no longer has both `coordinator.py` and `coordinators/`.

### Negative

- Platform files are still larger than the 200 to 400 line guidance in `AGENTS.md`. That guidance should be reworded to "one section per data source, and no module over about 2,000 lines" once this lands.
- `AGENTS.md`, `.github/instructions/*.instructions.md`, `.github/prompts/*`, and `.github/labeler.yml` list the current satellites and must be updated in a follow-up issue after #341 merges (they were updated for the current layout in #332 and are out of scope for #341).

### Neutral

- All entity `unique_id`s, config entry data, service schemas, and WebSocket contracts are unchanged; the consolidation is move-only. The Quality Scale fixes are tracked separately in #335 and #336.
- `coordinators/` remains a documented deviation from the single `coordinator.py` in `common-modules` (see [ADR-002](./002-multi-coordinator-architecture-behind-a-facade.md)).
