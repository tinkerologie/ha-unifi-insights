# Architectural and design decisions

This index gives the design decisions for UniFi Insights. The full
Architecture Decision Records (ADRs) are in [`adr/`](./adr/).

For the domain vocabulary used by these decisions, see the [glossary](../../GLOSSARY.md).

## Format

Each ADR follows the template from [ADR-001](./adr/001-record-architecture-decisions.md):

- **Date**: when the decision was made or last updated
- **Status**: Proposed, Accepted, Deprecated, or Superseded by ADR-NNN
- **Context**: the problem, and the Home Assistant or UniFi API constraints that apply
- **Decision**: what was decided
- **Alternatives considered**: options with pros and cons
- **Consequences**: positive, negative, and neutral impacts

Use a new ADR to change an accepted decision. Keep the previous ADR as a record.

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
| [007](./adr/007-flat-home-assistant-module-layout.md)                | Flat Home Assistant module layout                             | Accepted              | 2026-10-20 |
| [008](./adr/008-depend-on-unifi-official-api-from-pypi.md)           | Depend on `unifi-official-api` from PyPI                      | Accepted              | 2026-10-19 |
| [009](./adr/009-amend-flat-module-layout.md)                         | Amendments to the flat module layout for the deepening issues | Accepted              | 2026-10-19 |
| [010](./adr/010-one-owner-per-write-command.md)                      | One owner per write command                                   | Accepted              | 2026-10-19 |
| [011](./adr/011-platform-modules-and-flat-layout-homes.md)           | Platform module size and homes for the remaining helpers      | Accepted              | 2026-10-20 |
| [012](./adr/012-pure-protect-state-reconciler.md)                    | A pure reconciler for Protect state                           | Accepted              | 2026-10-20 |
| [013](./adr/013-typed-read-only-facade-snapshot.md)                  | A typed, read-only facade snapshot                            | Accepted              | 2026-10-20 |
| [014](./adr/014-pure-network-inventory-reconciliation.md)            | Reconcile Network inventory before registry changes           | Accepted              | 2026-11-07 |
| [015](./adr/015-defer-unified-protect-updates-subscription.md)       | Defer unified Protect updates subscription                    | Accepted              | 2026-11-10 |

### Record architecture decisions

Record each significant decision in an ADR. Use the next number.
Include the context, decision, alternatives, consequences, date, and status.
Add a summary to this index. Get maintainer approval before publishing a draft.

[ADR-001](./adr/001-record-architecture-decisions.md)

### Multi-coordinator architecture behind a facade

Each data domain has its own `DataUpdateCoordinator`. This keeps
configuration polls off the 30 s device poll. Each coordinator handles
its own errors and recovery. The facade combines Config, Device, Protect,
InnerSpace, and Site Manager data. Access, Mobility, and Carrier Fabric
use their own coordinators.

[ADR-002](./adr/002-multi-coordinator-architecture-behind-a-facade.md)

### Vendor the UniFi API client inside the integration (superseded)

Release 2026.3.0 copied `unifi-official-api` into
`custom_components/unifi_insights/api/`. This let the integration ship API
fixes before a PyPI release. ADR-008 supersedes this decision.

[ADR-003](./adr/003-vendor-the-unifi-api-client.md)

### Stable console identity for config entry unique IDs

For a local entry, `unique_id` uses the gateway or Cloud Key MAC.
If neither is available, it uses the Protect NVR MAC. A remote entry uses
`console_id`. A Carrier Fabric entry uses `carrier_<org_id>`.
The config flow and setup use the same function in `console_identity.py`.

[ADR-004](./adr/004-stable-console-identity-for-unique-ids.md)

### Bundled dashboard cards with versioned WebSocket contracts

The build makes six Lovelace bundles from `frontend/`. The integration
serves them with a content digest in the URL. The cards use versioned
`TypedDict` snapshots through `websocket_api.py`. Each entry uses its own
HMAC key for MAC-based topology node IDs.

[ADR-005](./adr/005-bundled-dashboard-cards-with-versioned-contracts.md)

### Three-layer diagnostics redaction

Diagnostics use three layers in order. They apply `TO_REDACT`, then
`CLIENT_TO_REDACT` and `WIFI_TO_REDACT`. Finally, `_anonymize_macs()` replaces
MAC addresses throughout the payload.

[ADR-006](./adr/006-three-layer-diagnostics-redaction.md)

### Flat Home Assistant module layout

The decision reduced 57 root modules to 33. Base entities moved to
`entity.py`. Platform entities moved to their platform modules. Pure
functions moved to `data_transforms.py`, `dashboard_snapshots.py`, and
`topology.py`. After #345, `coordinators/` was the only subpackage.
The count went from 199 Python files to 71, then to 48 after #341.

The decision set a budget of about 2,000 lines for a module. The file
moves followed #337 to #339. See
[#341](https://github.com/tinkerologie/ha-unifi-insights/issues/341) and
[#343](https://github.com/tinkerologie/ha-unifi-insights/issues/343).

[ADR-007](./adr/007-flat-home-assistant-module-layout.md)

### Depend on `unifi-official-api` from PyPI

At decision time, `manifest.json` pinned `unifi-official-api[talkback]==2.0.0`.
The upstream release published the package to PyPI. The integration
removed the vendored `api/` package (128 files, about 17,400 lines).
This meets `dependency-transparency`. Fix API contracts upstream, then
update the integration pin after a release. See
[#345](https://github.com/tinkerologie/ha-unifi-insights/issues/345).

[ADR-008](./adr/008-depend-on-unifi-official-api-from-pypi.md)

### Amendments to the flat module layout

ADR-009 amends ADR-007 for #337, #338, and #339.
`data_transforms.py` owns `get_field()` and `first_not_none()`.
`entity.py` exports them again. `data_transforms.normalize_mac()` parses
MAC addresses for device reads. Four other rules apply to stored or
visible identities. `service_targets.py` is the 34th root module.
The temporary 3,500-line limit for `coordinators/protect.py` ended in #340
(ADR-012).

[ADR-009](./adr/009-amend-flat-module-layout.md)

### One owner per write command

A write goes to the coordinator that owns its data. Config owns rules,
Wi-Fi, and guest authorization. Device owns restarts, PoE, outlets, and
Network clients. Protect, Access, and Carrier Fabric own their commands.
The facade keeps `async_refresh_or_raise`. ADR-013 moves voucher
commands to Config.

The owner uses `async_execute_command` in `coordinators/base.py`.
It translates errors and applies the command's overlay and refresh rules.
A failed refresh cannot fail a successful write. Config owns the classic
site name mapping. It does not guess `"default"`. Entities and actions
call owners directly. Protect commands fit the limit in ADR-009.
See [#338](https://github.com/tinkerologie/ha-unifi-insights/issues/338).

[ADR-010](./adr/010-one-owner-per-write-command.md)

### Platform module size and homes for the remaining helpers

The 17 platform modules use one section per data source. Each section
has setup, description tables, entities, and discovery as applicable.
These modules have an exception to the 2,000-line budget.
`sensor.py` was about 3,700 lines after the merge.

`console_identity.py` owns `async_get_device_entry()` and site entity
identity. This avoids an import cycle with the coordinators.
`data_transforms.py` owns `get_port_label()` and `is_doorbell_camera_model()`.
A test limits its imports to `homeassistant.util.*` and pure integration
modules. See [#341](https://github.com/tinkerologie/ha-unifi-insights/issues/341).

[ADR-011](./adr/011-platform-modules-and-flat-layout-homes.md)

### A pure reconciler for Protect state

The Protect reconciler is in `coordinators/protect_events.py`.
`reconcile(state, cache, input, now)` returns state, cache, and effects.
Effects include `PublishUpdate`, `RemoveDevice`, `Escalate`, and `Log`.
The function uses only its inputs, including both clocks in `now`.
`coordinators/protect_ws.py` parses frames and tracks stream health.

The coordinator runs I/O and applies results with `_apply()`. It stores
liveviews directly. Separate issues recorded later work for #379 and
#381 through #384. The initial split reduced `coordinators/protect.py`
from 3,482 to about 1,770 lines. This ended the ADR-009 exception.
See [#340](https://github.com/tinkerologie/ha-unifi-insights/issues/340).

[ADR-012](./adr/012-pure-protect-state-reconciler.md)

### A typed, read-only facade snapshot

The facade publishes `FacadeData` as a `ReadOnlyDict` at the root.
Its `TypedDict` uses `ReadOnly` keys and `Mapping` sections. Mypy checks
nested writes. The snapshot shares sections with owners where possible.
Tests use frozen fixtures. The decision rejected a deep freeze,
`MappingProxyType`, a frozen dataclass, and a `ReadOnlyDict` subclass
(`@final`).

Config owns `VoucherSettings`, the latest vouchers, and voucher commands.
Readers use pure lookups in `data_transforms.py` and captured availability.
Protect streams compare input objects by identity. InnerSpace reuses its
view until inputs change. Per-entity reads avoid copying whole sections.
The facade removed `get_site`, `get_device`, `get_device_stats`, and the
per-site `*_available` methods. See
[#342](https://github.com/tinkerologie/ha-unifi-insights/issues/342) (#386 through #405).

[ADR-013](./adr/013-typed-read-only-facade-snapshot.md)

### Reconcile Network inventory before registry changes

The Device coordinator compares site evidence with the current inventory.
It stores the new inventory and publishes the snapshot. Then it removes
stale registry entries.

[ADR-014](./adr/014-pure-network-inventory-reconciliation.md)

### Defer unified Protect updates subscription

UniFi Protect v7.4.96 added `/v1/subscribe/updates`. Migration is deferred
until `unifi-official-api` provides a subscription method, live frames confirm
the envelope, and `protect_ws.py` supports ID arrays and explicit removals.
The integration retains two dedicated streams (`devices` and `events`).
See [#501](https://github.com/tinkerologie/ha-unifi-insights/issues/501).

[ADR-015](./adr/015-defer-unified-protect-updates-subscription.md)

## Future considerations

### Facade coordinator

**Status:** kept

`UnifiInsightsFacadeCoordinator` gives readers one `data` snapshot.
ADR-013 moves voucher state and commands to Config. The facade keeps
aggregation, availability, refresh, and read functions. Some entities
now use owner notifications (#474). A direct subscription to each
owner is still open.

### Repairs

**Status:** not implemented

Issue #334 removed the unused repair flow. If a condition needs a repair
that a user can do, add the repair and its translation. Put the translation
under `"issues"` in `strings.json`. Authentication failures use reauthentication.

### Discovery

**Status:** not implemented

Users add consoles through the setup flow. The Zeroconf, DHCP, and SSDP
matchers in `manifest.json` are empty. If a reliable console match is
available, add the matchers and a `discovery-update-info` path.

---

## Decision review

Review these decisions when:

- UniFi publishes a new major API version (resolve versions from `https://developer.ui.com/llms.txt`)
- Home Assistant Core changes an Integration Quality Scale rule or deprecates a pattern used here
- A module exceeds roughly 2,000 lines or a package gains a third subpackage
- User feedback suggests an architectural problem rather than a bug
