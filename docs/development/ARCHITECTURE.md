# Architecture overview

This document describes the technical architecture of the UniFi Insights custom integration for Home Assistant.

For AI agent instructions, see [`AGENTS.md`](/AGENTS.md). For the decision log, see [`DECISIONS.md`](./DECISIONS.md) and the full records in [`adr/`](./adr/).

> **Status:** this describes `main` as of 2026-10-20, with the ADR-007 module layout in place (#341). The Protect coordinator is split along ADR-007's `protect_ws.py` / `protect_events.py` seam (#340, [ADR-012](./adr/012-pure-protect-state-reconciler.md)).

## Directory structure

```text
custom_components/unifi_insights/
├── __init__.py                 # Setup/unload/migrate, UnifiInsightsData, platform forwarding
├── config_flow.py              # Config flow (user, reauth, reconfigure) + options flow
├── console_identity.py         # Stable console identity, site entity identity migration, device registry lookups
├── probe.py                    # Capability probing (Network, Protect, Access, InnerSpace, Carrier Fabric)
├── const.py                    # Domain, intervals, config keys, attribute names
├── entity.py                   # Every base entity (network, Protect, Access, InnerSpace, Mobility router,
│                               # Carrier Fabric, site) and the shared discovery helpers
├── <platform>.py               # 17 platforms: alarm_control_panel, binary_sensor, button, camera, cover,
│                               # device_tracker, event, image, light, lock, media_player, number, select,
│                               # sensor, siren, switch, update. Each holds every entity of its platform,
│                               # in one section per data source (ADR-007, ADR-011)
├── data_transforms.py          # Pure transforms: field and MAC primitives, network device read model,
│                               # LED level, NVR storage telemetry, voucher state, port labels
├── innerspace_transforms.py    # InnerSpace floor plan and MAC correlation transforms
├── client_wake.py              # Wake-on-LAN helpers shared by the facade and the button
├── services.py / services.yaml # Service actions registered in async_setup
├── websocket_api.py            # unifi_insights/topology/* and unifi_insights/protect/* commands
├── topology.py                 # Topology frontend contract, per-entry HMAC node keys, graph builder
├── dashboard_snapshots.py      # Site health, performance, timeline, Protect status and internet
│                               # activity snapshot builders, with their shared contract helpers
├── frontend.py                 # Serves the built Lovelace card bundles with cache busting
├── access_webhook.py           # Signed UniFi Access webhook lifecycle
├── diagnostics.py              # Three-layer redacted diagnostics export
├── coordinators/               # Multi-coordinator package (see below)
├── frontend/                   # Built card bundles (topology, site-health, internet-activity,
│                               # performance, protect-status, timeline)
├── icons.json, strings.json, translations/en.json
├── manifest.json
└── py.typed

frontend/                       # TypeScript + Lit sources for the Lovelace cards (Vite build)
tests/                          # pytest suite mirroring the package (tests/coordinators/, test_<module>.py),
                                # shared data in tests/fixtures/; 95% per-file branch coverage in CI
script/                         # develop, lint, mypy, test, hassfest, query-api, setup
blueprints/                     # Example automation blueprints shipped with the integration
docs/development/               # This document, DECISIONS.md and adr/
```

## Core components

### API client

**Library:** [`unifi-official-api`](https://pypi.org/project/unifi-official-api/) from PyPI, pinned in `manifest.json` as `unifi-official-api[talkback]==2.0.0` and imported as `unifi_official_api` (see [ADR-008](./adr/008-depend-on-unifi-official-api-from-pypi.md), which supersedes the vendored copy of [ADR-003](./adr/003-vendor-the-unifi-api-client.md)). API contract fixes go to [tinkerologie/unifi-official-api](https://github.com/tinkerologie/unifi-official-api) first; the integration then raises its pin.

The package provides one async client per UniFi application:

| Client                  | Module                              | Used for                                                       |
| ----------------------- | ----------------------------------- | -------------------------------------------------------------- |
| `UniFiNetworkClient`    | `unifi_official_api.network`        | Sites, devices, clients, WiFi, firewall, routes, vouchers, DPI |
| `UniFiProtectClient`    | `unifi_official_api.protect`        | Cameras, lights, sensors, chimes, NVR, WebSocket, talkback     |
| `UniFiAccessClient`     | `unifi_official_api.access`         | Doors, users, emergency status, webhooks                       |
| `UniFiInnerSpaceClient` | `unifi_official_api.innerspace`     | Floor plans and device placements                              |
| Site Manager client     | `unifi_official_api.site_manager`   | Cloud console discovery and ISP metrics                        |
| Mobility client         | `unifi_official_api.mobility`       | Workspaces and mobile routers                                  |
| Carrier Fabric client   | `unifi_official_api.carrier_fabric` | Organizations, subscribers, fabrics                            |

Every client receives Home Assistant's shared `aiohttp` session through `async_get_clientsession(hass)` (Quality Scale `inject-websession`). Responses are Pydantic models that keep unknown fields (`extra="allow"`), so new API fields never break parsing. Integration code never issues raw HTTP calls.

### Multi-coordinator architecture

Each config entry owns a set of `DataUpdateCoordinator` subclasses, one per data domain, with intervals chosen for that domain's cost and volatility (see [ADR-002](./adr/002-multi-coordinator-architecture-behind-a-facade.md)):

| Coordinator                             | Module                           | Interval         | Scope                                                                                                                           |
| --------------------------------------- | -------------------------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `UnifiInsightsConfigCoordinator`        | `coordinators/config.py`         | 5 min            | Sites, WiFi, networks, firewall, ACL, DNS, vouchers, VPN                                                                        |
| `UnifiInsightsDeviceCoordinator`        | `coordinators/device.py`         | 30 s             | Network device and client metrics                                                                                               |
| `UnifiInsightsProtectCoordinator`       | `coordinators/protect.py`        | 30 s + WebSocket | Cameras, lights, sensors, chimes, NVR, viewers, liveviews (frames parsed in `protect_ws.py`, reconciled in `protect_events.py`) |
| `UnifiInsightsAccessCoordinator`        | `coordinators/access.py`         | 30 s + webhook   | Doors, users, emergency status                                                                                                  |
| `UnifiInsightsInnerSpaceCoordinator`    | `coordinators/innerspace.py`     | 5 min            | Floor plans and placements                                                                                                      |
| `UnifiInsightsSiteManagerCoordinator`   | `coordinators/site_manager.py`   | 10 min           | Cloud console and ISP metrics (shared per cloud key)                                                                            |
| `UnifiInsightsMobilityCoordinator`      | `coordinators/mobility.py`       | 5 min (1 h idle) | Mobility workspaces and routers                                                                                                 |
| `UnifiInsightsCarrierFabricCoordinator` | `coordinators/carrier_fabric.py` | 5 min            | Carrier Fabric organizations (standalone entry)                                                                                 |
| `UnifiInsightsFacadeCoordinator`        | `coordinators/facade.py`         | push only        | Unified read view over the coordinators above                                                                                   |

All coordinators inherit `UnifiInsightsBaseCoordinator` (`coordinators/base.py`) for shared error mapping: authentication failures raise `ConfigEntryAuthFailed` (triggers reauth) and connection or server failures raise `UpdateFailed` (retry next interval). The device, Protect and InnerSpace coordinators detach registry devices the console no longer reports through the base class's `_remove_stale_device`; each keeps its own rule for when a device counts as stale (Protect waits `MAX_CONSECUTIVE_MISSING_POLLS` polls).

The facade coordinator has no update interval. It subscribes to the specialized coordinators, aggregates their snapshots into one `data` mapping, and fans out a single listener notification. Entities and services read from the facade so they do not need to know which coordinator owns a field.

### Protect state reconciliation

The Protect coordinator has two sources that race each other: the "devices" and "events" WebSocket streams and the 30 s REST poll. `coordinators/protect_events.py` holds every decision about how one of them changes the cache, as one pure function (see [ADR-012](./adr/012-pure-protect-state-reconciler.md)). The one exception is liveviews: they have no frames, latches or eviction to reconcile, so `protect.py` writes each liveview answer itself, copy-on-write.

```python
reconcile(state, cache, input, now) -> Step(state, cache, effects)
```

- **Inputs.** A `DeviceFrame` or `EventFrame` (parsed by `protect_ws.py`), a `RestBatch` (one REST answer: `Fetched`, `NotFound`, `TransientError` or `NoAnswer`, with the `RequestStamp` taken by `begin_request` before the request's await), `PollStarted` and `PollCompleted` around the main poll, `StreamReconnected`, `SirenStopped` and a command's `CommandOverlay`.
- **State.** A frozen `ReconcilerState`: the motion, smart-detect and ring latches, per-sensor WebSocket recency and preserve counters, alarm hub tamper records, siren stop guards, the NVR and siren WebSocket generations, and each family's empty-answer, error and missing-poll counts.
- **Effects.** `PublishUpdate` (`async_set_updated_data` for device frames, `async_update_listeners` otherwise), `RemoveDevice` (registry removal through the base coordinator), `Escalate` (the fetcher re-raises a transient error that outlasted its window) and `Log` (written to the `coordinators.protect` logger).
- **Copy-on-write.** The cache is never changed in place: a step that changes something returns a new root that shares every untouched collection and record, and a step that changes nothing returns the same object.
- **Clocks.** The coordinator reads both clocks once per input into an `Instant`: monotonic time for request stamps, WebSocket recency, the tamper race and the siren guard lifetime; wall time for the latch timeout and the siren stop fallback.

The coordinator's `_apply(input)` is the only writer of the state: it commits the new state and cache, then runs the effects in order. The module imports nothing from Home Assistant, the API library or the clock, which an AST test enforces, so `tests/coordinators/test_protect_events.py` drives it table by table.

### Command path

Every write command belongs to the coordinator that owns the data it changes (see [ADR-010](./adr/010-one-owner-per-write-command.md)). Entities and service actions call that owner directly with a typed call: `coordinator.config_coordinator` (rule toggles, Wi-Fi, guest authorization, voucher generate and delete), `coordinator.device_coordinator` (restart, PoE power cycle, outlets, client commands, Wake-on-LAN) and `coordinator.require_protect_coordinator()` (camera, light, chime, viewer, PTZ, alarm, siren, liveview and talkback commands, which raises the translated `no_protect_coordinator` error without Protect). Access entities call their Access coordinator, and Carrier Fabric actions their Carrier Fabric coordinator. The facade keeps only `async_refresh_or_raise` and holds no domain state: the voucher settings and the latest generated vouchers belong to the config coordinator, which publishes `latest_vouchers` copy-on-write ([ADR-013](./adr/013-typed-read-only-facade-snapshot.md)). There is no string dispatch and no fallback to an API client, and no entity calls a client: the Protect owner also serves the camera snapshot and stream reads.

- **Errors.** Each owner runs the API call through `coordinators/base.py`'s `async_execute_command`, which passes a `HomeAssistantError` through and turns any other failure into a translated error (`command_failed` with the English "Unable to ..." message, `invalid_api_response` for unreadable data, or an optional rejection key such as `restart_rejected` for a falsy result). Access entities pass their coordinator's command through the same executor, so a failure the Access coordinator does not translate itself keeps its "Could not ..." message.
- **Optimistic state.** After a successful write the owner publishes the written fields as a copy-on-write overlay of its own data (lists included, so every `ringSettings` entry of a chime changes) and notifies listeners. Entities derive their state from that data instead of setting local attributes. There is no rollback and no generation tracking.
- **Reconciliation.** Rule toggles await the config refresh and retry once after 15 s while the family is unavailable; Wi-Fi, outlet and client block writes request a deferred refresh; Protect settings rely on WebSocket frames and the next poll, while chime ringtone, chime pairing and alarm commands request a Protect refresh. A failed refresh after a successful write is logged and never fails the write.
- **Classic site names.** The config coordinator publishes `legacy_site_names` and owns the only resolver, which never guesses `"default"` and raises `legacy_site_not_found`. The device coordinator reads the published mapping on each poll instead of looking it up.
- **Translation at the edge.** An entity turns a display name or an observed option into the native ID before it calls the owner (the liveview and ringtone selects raise `liveview_not_found` and `ringtone_not_found`); service actions already pass native IDs.

### Network device read model

`data_transforms.py` owns one rule for each network device fact: the online state, the device kind, the site gateway, the ports, the CPU, memory, uptime and uplink metrics, and a client's connection type. The device coordinator builds `SiteNetworkFacts` (one frozen `NetworkDeviceFacts` per device, plus the site's `gateway_id`) once per successful refresh, after the legacy merge, stats reuse and port rates. It publishes them under `network_facts` by replacing the map, never by mutating it. The facts are pruned and cleared with the other site maps, and a failed refresh keeps the last committed facts. The facade passes the map through by reference.

- **Reading the facts.** `site_network_facts(data, site_id)` and `device_facts(data, site_id, device_id)` return the published objects. They build from the raw `devices` and `stats` only when the key was never published (mock data in tests), and log that at debug level. The facade's `get_device_facts` and `get_site_gateway_id` call the same accessors. Entities read `device_facts` on `UnifiInsightsEntity`. The dashboard builders read the site facts, and WebSocket change detection includes their identity.
- **Online state.** The rule follows the Network API spec: only `ONLINE` is online. `OFFLINE`, `CONNECTION_INTERRUPTED`, `ISOLATED` and `U5G_INCORRECT_TOPOLOGY` are offline. Every other value is unknown, including `CONNECTED`, `UP`, `DISCONNECTED` and `DOWN`, which are not network device states. Protect `CONNECTED`, tracker `connected` and WAN `connected` keep their own rules.
- **Gateway.** The gateway checks (legacy type, v1 type, model prefix or "GATEWAY" in the model, a `gateway` or `router` feature) run before the switching check. A UDM Pro with a null `type` that advertises only `switching` is therefore a gateway even without legacy data. The site gateway is the first gateway in the site's device map order.
- **Throughput units.** `uplink.txRateBps` and `rxRateBps` (and `tx_rate_bps`/`rx_rate_bps`) are already bits/s. Only the explicit `*_bytes_per_sec` fallbacks are multiplied by 8. Evidence on #337: same-moment samples on a UDM Pro SE and a USW Pro Max 24 read the v1 rates at 8× the legacy uplink bytes/s, a UCG-Fiber sample agreed, and a UDM Pro read 8.00× across seven devices against the legacy `stat/device` `tx_bytes-r`/`rx_bytes-r` that the UniFi UI shows rates from. The facts keep float bits/s, and the performance and internet activity payloads convert them to integers.
- **Ports need legacy data.** The coordinator fetches only the v1 device overview, whose `interfaces` is a list of names. Port records come from the legacy `/stat/device` merge, so when that fetch fails, port sensors and SFP presence sensors lose their ports together. Each port keeps its raw index for unique IDs (SFP presence IDs use the raw spelling) and a normalized integer index for lookups.
- **Diagnostics.** Diagnostics drop `network_facts` before redaction, because the section repeats `devices` and `stats` as frozen records that the MAC pass cannot walk.
- **MAC policies.** `data_transforms.normalize_mac` is the canonical, validating parser and the topology HMAC input. Four identity-specific policies stay separate on purpose, because they feed stored or user-facing identities: `console_identity.normalize_mac`, `UnifiInsightsDeviceCoordinator._normalize_mac`, `innerspace_transforms._normalize_innerspace_mac` and `service_targets._mac_key`.

### Runtime data

```python
@dataclass
class UnifiInsightsData:
    config_coordinator: UnifiInsightsConfigCoordinator
    device_coordinator: UnifiInsightsDeviceCoordinator
    protect_coordinator: UnifiInsightsProtectCoordinator | None
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
CoordinatorEntity[UnifiInsightsFacadeCoordinator]
  ├── UnifiInsightsEntity           # Network devices; available when coordinator.device_available
  │                                 # and the published online fact is "online"
  ├── UnifiProtectEntity            # Protect devices; available when coordinator.protect_available
  │                                 # and state == "CONNECTED"
  └── UnifiSiteEntity               # One site, under its gateway or site device; each subclass
                                    # decides availability
CoordinatorEntity[UnifiInsightsAccessCoordinator]
  └── UnifiAccessEntity → UnifiAccessDoorEntity
CoordinatorEntity[UnifiInsightsFacadeCoordinator]
  └── UnifiInnerSpaceEntity
CoordinatorEntity[UnifiInsightsMobilityCoordinator]
  └── UnifiMobilityRouterEntity
CoordinatorEntity[UnifiInsightsCarrierFabricCoordinator]
  └── UnifiCarrierFabricEntity → UnifiCarrierFabricSubscriberEntity
```

All base classes live in `entity.py`. Platform entities use the MRO `UnifiInsightsEntity, SensorEntity` (custom base first). Same-shape entities share one class driven by its description: `UnifiInsightsNetworkRuleSwitch` serves all seven network rule families (firewall policies, policy-based routes, VPN clients, port forwards, traffic rules, NAT rules, static routes), `UnifiOutletSwitch` the outlet relay and power-cycle switches, and `UnifiProtectSensor` / `UnifiProtectBinarySensor` every Protect device, NVRs and security devices included. Static metadata lives in frozen, keyword-only `EntityDescription` dataclasses. Field access goes through `get_field()` (camelCase and snake_case tolerant) and `first_not_none()` (keeps legitimate `0` and `False`).

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

`services.py` registers every action in `async_setup` (`action-setup`), so they validate even when no entry is loaded. Target routing lives in `service_targets.py`, a recorded seam ([ADR-009](./adr/009-amend-flat-module-layout.md)): `services.py` keeps the schemas, handlers and registration. Each handler calls one public call-level resolver (`resolve_network_device_call`, `resolve_network_client_call`, `resolve_network_site_call`, `resolve_network_consoles_call`, `resolve_refresh_call`, `resolve_protect_call`, `resolve_protect_console_call`, `resolve_chime_pairing_call`, `resolve_rule_export_call`, `resolve_access_call` or `resolve_carrier_subscriber_call`). The resolver returns a frozen result that holds the owning config entry, the facade to read cached data from (`cache`), the ADR-010 command owner (`device_commands`, `config_commands`, `protect_commands`, `facade_commands`, `export_commands`, `access_commands` or `carrier_commands`) and the native ID and site. The handler then runs that one owner command, the same one the entities use (see [Command path](#command-path)). Routing never picks the first entry: registry targets resolve to their owning entry (`DeviceEntry.config_entry_id`), and Carrier Fabric identifiers are looked up one config entry at a time. User input errors raise `ServiceValidationError`; device or API failures raise `HomeAssistantError`. Both carry `translation_domain=DOMAIN` and a `translation_key` defined under `exceptions` in `strings.json` and `translations/en.json`; an AST guard in `tests/test_translations.py` enforces this for `services.py`, `service_targets.py` and `coordinators/carrier_fabric.py`.

### Dashboard cards and WebSocket API

Six Lovelace cards are built from `frontend/src/` with Vite into `custom_components/unifi_insights/frontend/` and served by `frontend.py` through `hass.http.async_register_static_paths` with a content-digest query string for cache busting. `websocket_api.py` exposes `unifi_insights/topology/{sources,get,subscribe,node_details}` and `unifi_insights/protect/{sources,get,subscribe}`. Snapshot payloads follow versioned TypedDict contracts, and topology node ids are HMACs of the MAC under a per-entry key so dashboards never leak raw addresses (see [ADR-005](./adr/005-bundled-dashboard-cards-with-versioned-contracts.md)).

### Push channels

- **Protect WebSocket** (`unifi_official_api.protect.websocket`): device and event updates with heartbeat and health checks, falling back to the 30 s poll. `coordinators/protect_ws.py` turns both streams' raw frames into typed `DeviceFrame`, `EventFrame` and `RateLimited` values and tracks each stream's health (`websocket_health` in diagnostics); the Protect coordinator applies them through the reconciler (see [Protect state reconciliation](#protect-state-reconciliation)).
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
│  unifi-official-api    │ ← one client per UniFi application, shared aiohttp session
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

Carrier Fabric entries run a single `UnifiInsightsCarrierFabricCoordinator` feeding the sensor platform directly.

## Key design decisions

See [DECISIONS.md](./DECISIONS.md) for the log and [`adr/`](./adr/) for the full records.

## Quality scale

The integration follows the Home Assistant Integration Quality Scale rules through Platinum as mandatory requirements. `custom_components/unifi_insights/quality_scale.yaml` records an evidence-based status for every rule, including the open `todo` items ([#336](https://github.com/tinkerologie/ha-unifi-insights/issues/336)); no tier is claimed in `manifest.json`, which is correct for a custom integration.
