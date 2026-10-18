# Architecture overview

This document describes the technical architecture of the UniFi Insights custom integration for Home Assistant.

For AI agent instructions, see [`AGENTS.md`](/AGENTS.md). For the decision log, see [`DECISIONS.md`](./DECISIONS.md) and the full records in [`adr/`](./adr/).

> **Status:** this describes `main` as of 2026-10-16. The module consolidation tracked in [#330](https://github.com/tinkerologie/ha-unifi-insights/issues/330) and [ADR-007](./adr/007-flat-home-assistant-module-layout.md) changes the directory layout below; the other sections stay valid.

## Directory structure

```text
custom_components/unifi_insights/
├── __init__.py                 # Setup/unload/migrate, UnifiInsightsData, platform forwarding
├── config_flow.py              # Config flow (user, reauth, reconfigure) + options flow
├── console_identity.py         # Stable console identity shared by config flow and setup
├── probe.py                    # Capability probing (Network, Protect, Access, InnerSpace, Carrier Fabric)
├── const.py                    # Domain, intervals, config keys, attribute names
├── entity.py                   # UnifiInsightsEntity, UnifiProtectEntity, get_field(), first_not_none()
├── *_entity.py                 # Per-feature base entities (access, innerspace, mobility, carrier_fabric)
├── *_sensor.py, storage_entity.py, protect_security_entity.py, network_rule_*.py
│                               # Feature-specific entity satellites (merged into platforms by ADR-007)
├── <platform>.py               # 17 platforms: alarm_control_panel, binary_sensor, button, camera, cover,
│                               # device_tracker, event, image, light, lock, media_player, number, select,
│                               # sensor, siren, switch, update
├── data_transforms.py          # API response → internal dict mapping
├── innerspace_transforms.py    # InnerSpace floor plan and MAC correlation transforms
├── helpers.py, led_level.py, storage_telemetry.py, client_wake.py
│                               # Small pure helpers (dissolved by ADR-007)
├── services.py / services.yaml # Service actions registered in async_setup
├── websocket_api.py            # unifi_insights/topology/* and unifi_insights/protect/* commands
├── topology.py, topology_contract.py, topology_keys.py
│                               # Topology graph, frontend contract, per-entry HMAC node keys
├── site_health.py, performance.py, timeline.py, protect_snapshot.py,
│   internet_activity_snapshot.py, dashboard_contract_utils.py
│                               # Dashboard snapshot builders (merged into dashboard_snapshots.py by ADR-007)
├── frontend.py                 # Serves the built Lovelace card bundles with cache busting
├── access_webhook.py           # Signed UniFi Access webhook lifecycle
├── diagnostics.py              # Three-layer redacted diagnostics export
├── coordinators/               # Multi-coordinator package (see below)
├── api/                        # Vendored unifi-official-api clients (Network, Protect, Access,
│                               # InnerSpace, Site Manager, Mobility, Carrier Fabric)
├── frontend/                   # Built card bundles (topology, site-health, internet-activity,
│                               # performance, protect-status, timeline)
├── icons.json, strings.json, translations/en.json
├── manifest.json
└── py.typed

frontend/                       # TypeScript + Lit sources for the Lovelace cards (Vite build)
tests/                          # pytest suite, 95% per-file branch coverage enforced in CI
script/                         # develop, lint, mypy, test, hassfest, query-api, setup
blueprints/                     # Example automation blueprints shipped with the integration
docs/development/               # This document, DECISIONS.md and adr/
```

## Core components

### API client

**Library:** vendored copy of `unifi-official-api` under `custom_components/unifi_insights/api/` (see [ADR-003](./adr/003-vendor-the-unifi-api-client.md)).

The package provides one async client per UniFi application:

| Client                  | Package               | Used for                                                       |
| ----------------------- | --------------------- | -------------------------------------------------------------- |
| `UniFiNetworkClient`    | `api/network/`        | Sites, devices, clients, WiFi, firewall, routes, vouchers, DPI |
| `UniFiProtectClient`    | `api/protect/`        | Cameras, lights, sensors, chimes, NVR, WebSocket, talkback     |
| `UniFiAccessClient`     | `api/access/`         | Doors, users, emergency status, webhooks                       |
| `UniFiInnerSpaceClient` | `api/innerspace/`     | Floor plans and device placements                              |
| Site Manager client     | `api/site_manager/`   | Cloud console discovery and ISP metrics                        |
| Mobility client         | `api/mobility/`       | Workspaces and mobile routers                                  |
| Carrier Fabric client   | `api/carrier_fabric/` | Organizations, subscribers, fabrics                            |

The vendored tree is planned to move back upstream as `unifi-official-api` 2.0.0 ([tinkerologie/unifi-official-api#41](https://github.com/tinkerologie/unifi-official-api/issues/41)) after #330; see [DECISIONS.md](./DECISIONS.md).

Every client receives Home Assistant's shared `aiohttp` session through `async_get_clientsession(hass)` (Quality Scale `inject-websession`). Responses are Pydantic models with `extra="ignore"`, so new API fields never break parsing. Integration code never issues raw HTTP calls.

### Multi-coordinator architecture

Each config entry owns a set of `DataUpdateCoordinator` subclasses, one per data domain, with intervals chosen for that domain's cost and volatility (see [ADR-002](./adr/002-multi-coordinator-architecture-behind-a-facade.md)):

| Coordinator                           | Module                           | Interval         | Scope                                                     |
| ------------------------------------- | -------------------------------- | ---------------- | --------------------------------------------------------- |
| `UnifiConfigCoordinator`              | `coordinators/config.py`         | 5 min            | Sites, WiFi, networks, firewall, ACL, DNS, vouchers, VPN  |
| `UnifiDeviceCoordinator`              | `coordinators/device.py`         | 30 s             | Network device and client metrics                         |
| `UnifiProtectCoordinator`             | `coordinators/protect.py`        | 30 s + WebSocket | Cameras, lights, sensors, chimes, NVR, viewers, liveviews |
| `UnifiInsightsAccessCoordinator`      | `coordinators/access.py`         | 30 s + webhook   | Doors, users, emergency status                            |
| `UnifiInsightsInnerSpaceCoordinator`  | `coordinators/innerspace.py`     | 5 min            | Floor plans and placements                                |
| `UnifiInsightsSiteManagerCoordinator` | `coordinators/site_manager.py`   | 10 min           | Cloud console and ISP metrics (shared per cloud key)      |
| `UnifiInsightsMobilityCoordinator`    | `coordinators/mobility.py`       | 5 min (1 h idle) | Mobility workspaces and routers                           |
| `UnifiCarrierFabricCoordinator`       | `coordinators/carrier_fabric.py` | 5 min            | Carrier Fabric organizations (standalone entry)           |
| `UnifiFacadeCoordinator`              | `coordinators/facade.py`         | push only        | Unified read view over the coordinators above             |

All coordinators inherit `UnifiBaseCoordinator` (`coordinators/base.py`) for shared error mapping: authentication failures raise `ConfigEntryAuthFailed` (triggers reauth) and connection or server failures raise `UpdateFailed` (retry next interval).

The facade coordinator has no update interval. It subscribes to the specialized coordinators, aggregates their snapshots into one `data` mapping, and fans out a single listener notification. Entities and services read from the facade so they do not need to know which coordinator owns a field.

### Runtime data

```python
@dataclass
class UnifiInsightsData:
    config_coordinator: UnifiConfigCoordinator
    device_coordinator: UnifiDeviceCoordinator
    protect_coordinator: UnifiProtectCoordinator | None
    network_client: UniFiNetworkClient
    protect_client: UniFiProtectClient | None
    innerspace_coordinator: UnifiInsightsInnerSpaceCoordinator | None = None
    innerspace_client: UniFiInnerSpaceClient | None = None
    site_manager_coordinator: UnifiInsightsSiteManagerCoordinator | None = None
    mobility_coordinator: UnifiInsightsMobilityCoordinator | None = None
    access_client: UniFiAccessClient | None = None
    access_coordinator: UnifiInsightsAccessCoordinator | None = None


UnifiInsightsConfigEntry: TypeAlias = ConfigEntry[UnifiInsightsData]
```

Stored in `entry.runtime_data`. Carrier Fabric entries use a separate `CarrierFabricData` dataclass. Component-wide singletons (frontend registration, topology keys) use typed `HassKey` entries; nothing per-entry lives in `hass.data[DOMAIN]`.

### Entity hierarchy

```text
CoordinatorEntity[UnifiFacadeCoordinator]
  ├── UnifiInsightsEntity           # Network devices; available when coordinator.device_available
  │                                 # and is_device_online(device_data)
  └── UnifiProtectEntity            # Protect devices; available when coordinator.protect_available
                                    # and state == "CONNECTED"
CoordinatorEntity[UnifiInsightsAccessCoordinator]
  └── UnifiAccessEntity → UnifiAccessDoorEntity
CoordinatorEntity[UnifiFacadeCoordinator]
  └── UnifiInnerSpaceEntity
CoordinatorEntity[UnifiInsightsMobilityCoordinator]
  └── UnifiMobilityRouterEntity
CoordinatorEntity[UnifiCarrierFabricCoordinator]
  └── UnifiCarrierFabricEntity → UnifiCarrierFabricSubscriberEntity
```

Platform entities use the MRO `UnifiInsightsEntity, SensorEntity` (custom base first). Static metadata lives in frozen, keyword-only `EntityDescription` dataclasses. Field access goes through `get_field()` (camelCase and snake_case tolerant) and `first_not_none()` (keeps legitimate `0` and `False`).

### Config flow

Three connection modes share one domain (see [ADR-004](./adr/004-stable-console-identity-for-unique-ids.md)):

| Mode             | Credentials                                  | Unique ID                                         |
| ---------------- | -------------------------------------------- | ------------------------------------------------- |
| `local`          | Host URL + API key (+ optional Access token) | Gateway or Cloud Key MAC, else Protect NVR MAC    |
| `remote`         | Site Manager API key + console selection     | Cloud `console_id`                                |
| `carrier_fabric` | Organization ID + API key                    | `carrier_<org_id>` or `carrier_key_<sha256[:16]>` |

Flows: user setup, reauth, reconfigure, options. There is no network discovery step; `manifest.json` has empty `zeroconf`, `dhcp`, and `ssdp` matchers. `console_identity.py` resolves the identity for both the flow and `async_setup_entry`, so the two can never disagree. `probe.py` classifies which applications the credentials can reach before the entry is created (`test-before-configure`) and again at setup (`test-before-setup`).

### Device model

One device per physical UniFi device, identified by `(DOMAIN, mac)` or `(DOMAIN, protect_id)`, with the console as the parent via `via_device_id`. Logical sites, WiFi networks, and Mobility workspaces are devices scoped to the owning config entry. Stale devices are removed when they disappear from the coordinator snapshot (`stale-devices`), and `async_remove_config_entry_device` lets users delete the rest.

### Service actions

`services.py` registers every action in `async_setup` (`action-setup`), so they validate even when no entry is loaded. Each action resolves the owning console through `_get_coordinator_for_network_resource` or `_get_coordinator_for_protect_resource`; it never picks the first entry. User input errors raise `ServiceValidationError`; device or API failures raise `HomeAssistantError`. Both carry `translation_domain=DOMAIN` and a `translation_key` defined under `exceptions` in `strings.json`.

### Dashboard cards and WebSocket API

Six Lovelace cards are built from `frontend/src/` with Vite into `custom_components/unifi_insights/frontend/` and served by `frontend.py` through `hass.http.async_register_static_paths` with a content-digest query string for cache busting. `websocket_api.py` exposes `unifi_insights/topology/{sources,get,subscribe,node_details}` and `unifi_insights/protect/{sources,get,subscribe}`. Snapshot payloads follow versioned TypedDict contracts, and topology node ids are HMACs of the MAC under a per-entry key so dashboards never leak raw addresses (see [ADR-005](./adr/005-bundled-dashboard-cards-with-versioned-contracts.md)).

### Push channels

- **Protect WebSocket** (`api/protect/websocket.py`): device and event updates with heartbeat and health checks, falling back to the 30 s poll.
- **Access webhook** (`access_webhook.py`): signed access-granted and access-denied results delivered through Home Assistant's `webhook` component.

### Diagnostics

`diagnostics.py` exports config entry diagnostics through three redaction layers: `TO_REDACT` for secrets, `CLIENT_TO_REDACT` and `WIFI_TO_REDACT` for personal names and SSIDs, and `_anonymize_macs()` for consistent, reversible-only-by-the-author MAC anonymization (see [ADR-006](./adr/006-three-layer-diagnostics-redaction.md)).

## Data flow

```text
┌────────────────────────┐
│  Config entry          │ ← console_identity.py + probe.py decide identity and capabilities
└───────────┬────────────┘
            ▼
┌────────────────────────┐
│  Vendored API clients  │ ← one per UniFi application, shared aiohttp session
└───────────┬────────────┘
            │
   ┌────────┼─────────┬──────────┬──────────┬───────────┬──────────┐
   ▼        ▼         ▼          ▼          ▼           ▼          ▼
┌──────┐ ┌──────┐ ┌───────┐ ┌───────┐ ┌─────────┐ ┌──────────┐ ┌────────┐
│Config│ │Device│ │Protect│ │Access │ │InnerSpc │ │SiteMgr   │ │Mobility│
│5 min │ │30 s  │ │30s+WS │ │30s+WH │ │5 min    │ │10 min    │ │5 min   │
└──┬───┘ └──┬───┘ └──┬────┘ └──┬────┘ └───┬─────┘ └────┬─────┘ └───┬────┘
   └────────┴────────┴─────┬───┴──────────┴────────────┴───────────┘
                           ▼
              ┌────────────────────────┐
              │  Facade coordinator    │ ← aggregates snapshots, single listener fan-out
              └───────────┬────────────┘
         ┌────────────────┼────────────────────┐
         ▼                ▼                    ▼
   ┌───────────┐   ┌──────────────┐   ┌─────────────────────┐
   │ Entities  │   │ Services     │   │ WebSocket API →     │
   │ 17 plat.  │   │ services.py  │   │ dashboard cards     │
   └───────────┘   └──────────────┘   └─────────────────────┘
```

Carrier Fabric entries run a single `UnifiCarrierFabricCoordinator` feeding the sensor platform directly.

## Key design decisions

See [DECISIONS.md](./DECISIONS.md) for the log and [`adr/`](./adr/) for the full records.

## Quality scale

The integration follows the Home Assistant Integration Quality Scale rules through Platinum as mandatory requirements. `custom_components/unifi_insights/quality_scale.yaml` records an evidence-based status for every rule, including the open `todo` items ([#336](https://github.com/tinkerologie/ha-unifi-insights/issues/336)); no tier is claimed in `manifest.json`, which is correct for a custom integration.
