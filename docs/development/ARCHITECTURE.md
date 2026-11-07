# Architecture overview

This document gives the design of UniFi Insights.

See [AGENTS.md](/AGENTS.md) for agent instructions. See
[DECISIONS.md](./DECISIONS.md) and [adr/](./adr/) for the decision records.
See the [glossary](../../GLOSSARY.md) for project terms.

> **Status.** This overview describes `main` as of 2026-11-07.

The Device coordinator uses `coordinators/device_inventory.py` to find the
registry entries to remove. It stores the inventory and publishes the snapshot
before it removes these entries. See
[ADR-014](./adr/014-pure-network-inventory-reconciliation.md).

## Facade notifications

The facade publishes a snapshot before it sends notifications. Use
`FacadeOwner.DEVICE` or `FacadeOwner.PROTECT` as the listener context for
an entity that reads from that owner. The facade notifies the entity when
its owner updates. Listeners without a `FacadeOwner` receive all updates.
Use the default context for discovery and for entities that read from more
than one owner.

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

**Library.** The integration uses
[`unifi-official-api`](https://pypi.org/project/unifi-official-api/) from PyPI.
The Python import name is `unifi_official_api`. Keep the same version in
`manifest.json`, `pyproject.toml`, and `requirements.txt`.
See [ADR-008](./adr/008-depend-on-unifi-official-api-from-pypi.md).

Fix API contracts in
[unifi-official-api](https://github.com/tinkerologie/unifi-official-api) first.
After the API release, update the library version in the integration.

For lenient Protect lists, the Protect coordinator reads the records and the
completeness flag from one result. An incomplete result keeps the cached
records that the response does not contain.

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

Home Assistant supplies the `aiohttp` session with `async_get_clientsession(hass)`
to meet `inject-websession`. The integration uses the library clients for API
requests. Pydantic models with `extra="allow"` keep fields that the model
does not define.

### Multi-coordinator architecture

Each config entry has the coordinators for its applications. The polling
interval depends on the data. See
[ADR-002](./adr/002-multi-coordinator-architecture-behind-a-facade.md).

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
| `UnifiInsightsFacadeCoordinator`        | `coordinators/facade.py`         | push only        | Read view of Config, Device, Protect, InnerSpace, and Site Manager                                                              |

Config, Device, Protect, and InnerSpace use `UnifiInsightsBaseCoordinator`
from `coordinators/base.py`. The other coordinators extend
`DataUpdateCoordinator` directly. An authentication failure can raise
`ConfigEntryAuthFailed` to start reauthentication. A connection or server
failure can raise `UpdateFailed` to let Home Assistant retry.

Device, Protect, and InnerSpace use `_remove_stale_device` to remove
registry entries. Each coordinator has its own rule for stale devices.
For Network devices, consecutive polls must use the same API source.
An empty result after a nonempty result cannot start a removal.
The Device coordinator keeps an ID if the site still reports the same MAC.
When the v1 list omits a device, the legacy list from the same poll must
also omit its MAC. A failed or empty legacy list cannot confirm removal.
This check uses the list that the poll already fetched.

Device removes a device after three consecutive eligible misses
(`MAX_CONSECUTIVE_MISSING_POLLS = 3`). It keeps known v1 IDs during
legacy fallback. Protect removes a missing device on the fourth poll.
InnerSpace removes missing devices on the first poll.

The facade has no polling interval. It combines Config, Device, Protect,
InnerSpace, and Site Manager data in one snapshot. Access and Mobility
entities use their own coordinators. Carrier Fabric has a separate entry.

`FacadeData` is a `TypedDict` with `ReadOnly` keys and `Mapping` sections.
The root is a `ReadOnlyDict`. `build_facade_data` reuses owner sections by
reference where possible. The InnerSpace view rebuilds when its inputs
change. The facade builds the Site Manager section with the selected host ID.
See [ADR-013](./adr/013-typed-read-only-facade-snapshot.md).

- **Lookups.** The pure lookup functions live in `data_transforms.py`.
  `network_device`, `network_device_stats`, `network_client`, `site_record`,
  `rule_record`, `protect_device`, and `latest_voucher` return a record or `None`.
  `protect_collection`, `site_section`, and `section_by_site` can copy records.
  Use a record lookup for each entity. The WebSocket inputs use the owner sections.
- **Availability.** `data["availability"]` stores owner and site-section availability.
  Entities use `owner_available(data, owner)` and `site_section_available(data, site_id, section)`.
  The facade uses its `*_available` properties for snapshot creation, WebSocket, and diagnostics.
- **WebSocket updates.** Dashboard streams compare input objects by identity.
  Protect streams compare the Protect collections by identity.
- **Tests.** `tests/fixtures/facade_data.py` builds and freezes test snapshots.

### Protect state reconciliation

The Protect coordinator receives WebSocket frames and polls REST every 30 s.
`coordinators/protect_events.py` reconciles those inputs with a pure function.
See [ADR-012](./adr/012-pure-protect-state-reconciler.md).
`protect.py` stores liveviews directly because they have no events to reconcile.

```python
reconcile(state, cache, input, now) -> Step(state, cache, effects)
```

- **Inputs.** `protect_ws.py` parses `DeviceFrame` and `EventFrame`.
  A `RestBatch` carries `Fetched`, `NotFound`, `TransientError`, or `NoAnswer`.
  `begin_request` makes the `RequestStamp` before the request waits.
  Other inputs include `PollStarted`, `PollCompleted`, `StreamReconnected`,
  `SirenStopped`, and `CommandOverlay`.
- **State.** The frozen `ReconcilerState` holds event latches and WebSocket timing.
  It also holds tamper records, siren stop guards, generations, and poll counters.
- **Effects.** `PublishUpdate` notifies listeners.
  `RemoveDevice` removes a registry entry.
  `Escalate` raises an error after the retry window.
  `Log` records a message. `DispatchDigitalInput` sends a digital input event.
- **Copy-on-write.** A change creates a new root. It shares unchanged collections
  and records. If no data changes, the function returns the same object.
- **Clocks.** The coordinator reads both clocks into an `Instant`.
  Monotonic time tracks request stamps, recency, and guards.
  Wall time controls latch timeouts and the siren stop fallback.

The coordinator's `_apply(input)` stores the new state and cache.
Then it runs the effects in order. The reconciler has no Home Assistant,
API library, or clock imports. `tests/coordinators/test_protect_events.py`
tests the decisions.

### Command path

The coordinator that owns the data runs its write commands. See
[ADR-010](./adr/010-one-owner-per-write-command.md).

- `coordinator.config_coordinator` handles rules, Wi-Fi, guest authorization, and vouchers.
- `coordinator.device_coordinator` handles restarts, PoE, outlets, Network clients, and Wake-on-LAN.
- `coordinator.require_protect_coordinator()` gives the owner for Protect commands.
  Without Protect, it raises `no_protect_coordinator`.
- Access and Carrier Fabric use their own command owners.

The facade provides `async_refresh_or_raise`. Config owns voucher settings and
publishes `latest_vouchers`. See [ADR-013](./adr/013-typed-read-only-facade-snapshot.md).
Entities call their coordinator. The Protect coordinator also handles
camera snapshot and stream reads.

- **Errors.** `async_execute_command` passes through `HomeAssistantError`.
  It translates other failures with `command_failed` or a more specific key.
  Examples include `invalid_api_response` and `restart_rejected`.
  Access can keep the translated error or its English message.
- **Optimistic state.** An owner can publish an overlay after a successful write.
  The fields in this overlay depend on the command. For example, chime
  ringtone updates change each `ringSettings` entry.
  Device can keep a new outlet value for up to 60 s while polls return the old value.
- **Reconciliation.** Rule toggles wait for Config and can retry after 15 s.
  Wi-Fi, outlet, and client block commands request a deferred refresh.
  Protect usually waits for a frame or the next poll. Chime ringtone,
  pairing, and alarm commands request a refresh. A refresh failure after
  a successful write goes to the log.
- **Classic site names.** Config publishes `legacy_site_names`.
  If it cannot resolve a name, it raises `legacy_site_not_found`.
  Device reads the published mapping. Neither uses `"default"` as a guess.
- **Native IDs.** Entities convert display values to native IDs before the call.
  Liveview and ringtone selects can raise `liveview_not_found` or `ringtone_not_found`.
  Actions already receive native IDs.

### Network device read model

`data_transforms.py` gives the rules for Network device facts.
These facts include device kind, online state, gateway, ports, and metrics.
The Device coordinator builds `SiteNetworkFacts` after it combines the API data.
The site facts hold one `NetworkDeviceFacts` record per device and a `gateway_id`.
A failed refresh keeps the previous facts. The facade uses the map by reference.

- **Access.** `site_network_facts(data, site_id)` and `device_facts(data, site_id, device_id)`
  return the published records. If no `network_facts` key exists, they can
  use `devices` and `stats` and write a debug message. Entities and cards
  use the same facts. WebSocket change detection uses their identity.
- **Online state.** Only `ONLINE` means online. `OFFLINE`, `CONNECTION_INTERRUPTED`,
  `ISOLATED`, and `U5G_INCORRECT_TOPOLOGY` mean offline. Other values mean unknown.
  Protect, device trackers, and WAN sensors use their own rules.
- **Serviceable Network device.** `serviceable` and `device_serviceable()` are
  separate from `online_state`. Network entities can remain available in
  `ONLINE` and `GETTING_READY` when the Device coordinator is available.
  In `GETTING_READY`, the device applies its configuration. Dashboards and
  Status or WAN sensors can show unknown. `UPDATING`, `ADOPTING`,
  `PENDING_ADOPTION`, and `DELETING` make the entities unavailable.
  A failed Device poll also makes them unavailable.
- **Gateway.** The gateway check uses type, model, or feature data before the
  switching check. This can identify a UDM Pro with a null `type`. The
  first gateway in the site's device map is the site gateway.
- **Throughput.** `uplink.txRateBps`, `rxRateBps`, `tx_rate_bps`, and
  `rx_rate_bps` use bits/s. The conversion multiplies only the
  `*_bytes_per_sec` values by eight. Samples from #337 confirmed this ratio.
  Device facts keep decimal values. The two dashboard payloads use integers.
- **Ports.** The v1 `interfaces` list has names. The legacy `/stat/device`
  response supplies port records. If it fails, both port and SFP sensors
  lose these records. Ports keep their raw index for IDs and an integer
  index for lookups.
- **Diagnostics.** Diagnostics omit `network_facts`. These frozen records
  repeat `devices` and `stats`, and the MAC redaction cannot read them.
- **MAC rules.** `data_transforms.normalize_mac` validates MAC addresses for
  topology. Stored or visible identities use their own rules in
  `console_identity.normalize_mac`, `UnifiInsightsDeviceCoordinator._normalize_mac`,
  `innerspace_transforms._normalize_innerspace_mac`, and `service_targets._mac_key`.

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

The integration stores per-entry data in `entry.runtime_data`. Carrier Fabric
uses `CarrierFabricData`. Frontend registration and topology keys use typed
`HassKey` entries.

### Entity hierarchy

```text
CoordinatorEntity[UnifiInsightsFacadeCoordinator]
  ├── UnifiInsightsEntity           # Network devices; available when owner_available(data, "device")
  │                                 # and the published serviceable fact is true
  ├── UnifiProtectEntity            # Protect devices; available when owner_available(data, "protect")
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

`entity.py` holds the base classes. In the platform class, put the
integration base first, for example `UnifiInsightsEntity, SensorEntity`.
`UnifiInsightsNetworkRuleSwitch` covers seven Network rule families.
`UnifiOutletSwitch` covers outlet relay and power cycle.
`UnifiProtectSensor` and `UnifiProtectBinarySensor` cover Protect devices.
Frozen `EntityDescription` dataclasses hold static metadata.
`get_field()` accepts camelCase and snake_case. `first_not_none()` keeps
values such as `0` and `False`.

### Config flow

Three connection modes share one domain (see [ADR-004](./adr/004-stable-console-identity-for-unique-ids.md)):

| Mode             | Credentials                                  | Unique ID                                         |
| ---------------- | -------------------------------------------- | ------------------------------------------------- |
| `local`          | Host URL + API key (+ optional Access token) | Gateway or Cloud Key MAC, else Protect NVR MAC    |
| `remote`         | Site Manager API key + console selection     | Cloud `console_id`                                |
| `carrier_fabric` | Organization ID + API key                    | `carrier_<org_id>` or `carrier_key_<sha256[:16]>` |

The flow supports setup, reauthentication, reconfiguration, and options.
`manifest.json` has empty `zeroconf`, `dhcp`, and `ssdp` matchers.
The flow and `async_setup_entry` use the same identity from
`console_identity.py`. `probe.py` checks access before it creates the
entry (`test-before-configure`). It checks again during setup
(`test-before-setup`).

### Device model

Each physical device has one registry entry with a console parent
(`via_device_id`). A Network identifier uses `(DOMAIN, "<site_id>_<device_id>")`.
`device_id` is the v1 ID when known. Otherwise, it is the legacy `_id`
or MAC. Protect can reuse identifiers from a matching Network device.
Sites, Wi-Fi networks, and Mobility workspaces also have registry entries.

The coordinators apply their stale device rules (`stale-devices`).
Automatic cleanup keeps devices if their entire site disappears from a poll.
A partial site list can look like a deleted site (#391).
Users can remove entries for deselected or missing sites with
`async_remove_config_entry_device` when the site list is reliable.
They can also remove stale Wi-Fi networks, Mobility routers, and
Carrier Fabric subscribers.

### Service actions

`services.py` registers actions in `async_setup` (`action-setup`).
It holds the schemas, handlers, and registration. `service_targets.py`
resolves the target. See [ADR-009](./adr/009-amend-flat-module-layout.md).

The handler uses one resolver per call. The resolvers are
`resolve_network_device_call`, `resolve_network_client_call`,
`resolve_network_site_call`, `resolve_network_consoles_call`,
`resolve_refresh_call`, `resolve_protect_call`, `resolve_protect_console_call`,
`resolve_chime_pairing_call`, `resolve_rule_export_call`, `resolve_access_call`,
and `resolve_carrier_subscriber_call`.

The frozen result holds the config entry, `cache`, the command owner, and
the native IDs. Owner fields include `device_commands`, `config_commands`,
`protect_commands`, `facade_commands`, `export_commands`, `access_commands`,
and `carrier_commands`. The handler runs the same command that an entity uses.
See [Command path](#command-path).

Registry targets use `DeviceEntry.config_entry_id`. Carrier Fabric searches
one entry at a time. Invalid inputs raise `ServiceValidationError`.
Device or API failures raise `HomeAssistantError`. Both use
`translation_domain=DOMAIN` and a `translation_key` from `exceptions`.
`tests/test_translations.py` checks the keys in `strings.json` and
`translations/en.json` for the action handlers and the Carrier Fabric coordinator.

### Dashboard cards and WebSocket API

Vite builds six Lovelace cards from `frontend/src/`. The bundles go to
`custom_components/unifi_insights/frontend/`. `frontend.py` serves them
through `hass.http.async_register_static_paths`. A content digest in the
URL identifies the current bundle.

`websocket_api.py` provides `unifi_insights/topology/{sources,get,subscribe,node_details}`
and `unifi_insights/protect/{sources,get,subscribe}`. Snapshots use versioned
`TypedDict` contracts. Topology node IDs use an HMAC of the MAC with a
key for each entry. See [ADR-005](./adr/005-bundled-dashboard-cards-with-versioned-contracts.md).

### Push channels

- **Protect WebSocket.** `coordinators/protect_ws.py` reads the device and
  event streams. It gives the reconciler `DeviceFrame`, `EventFrame`, and
  `RateLimited` values. Diagnostics show `websocket_health`. The 30 s poll
  also updates the data. See [Protect state reconciliation](#protect-state-reconciliation).
- **Access webhook.** `access_webhook.py` receives signed access results
  through Home Assistant's `webhook` component.

### Diagnostics

`diagnostics.py` exports config entry diagnostics. `TO_REDACT` identifies
secrets. `CLIENT_TO_REDACT` and `WIFI_TO_REDACT` identify personal names
and SSIDs. `_anonymize_macs()` replaces MAC addresses with consistent
values within each report. See [ADR-006](./adr/006-three-layer-diagnostics-redaction.md).

## Data flow

```text
Config entry -> console_identity.py and probe.py
  -> unifi-official-api clients with the shared aiohttp session
     -> Config, Device, Protect, InnerSpace, Site Manager -> Facade
        -> Entities, services.py, WebSocket API, dashboard cards
     -> Access -> Access entities and actions
     -> Mobility -> Mobility entities
```

Carrier Fabric entries run a single `UnifiInsightsCarrierFabricCoordinator` feeding the sensor platform directly.

## Key design decisions

See [DECISIONS.md](./DECISIONS.md) for the log and [`adr/`](./adr/) for the full records.

## Quality scale

The integration uses the Quality Scale rules through Platinum.
`custom_components/unifi_insights/quality_scale.yaml` records each rule's
status and evidence. See [#336](https://github.com/tinkerologie/ha-unifi-insights/issues/336)
for open items.
