# ADR-013: A typed, read-only facade snapshot

**Date:** 2026-10-20
**Status:** Accepted (decided in the [design review](https://github.com/tinkerologie/ha-unifi-insights/issues/342#issuecomment-6284319707) of [#342](https://github.com/tinkerologie/ha-unifi-insights/issues/342), part of [#343](https://github.com/tinkerologie/ha-unifi-insights/issues/343); amends [ADR-002](./002-multi-coordinator-architecture-behind-a-facade.md) and the ownership table of [ADR-010](./010-one-owner-per-write-command.md))

## Context

[ADR-002](./002-multi-coordinator-architecture-behind-a-facade.md) puts the per-domain coordinators behind a facade that entities and services read, and warns that "the facade can grow into a second data model". [ADR-010](./010-one-owner-per-write-command.md) moved every write except the vouchers to its owner and made the owners' overlays copy-on-write. Measured on `main` @ `c82d9d8`, after #337, #338, #340 and #341:

- No entity, service or platform writes into `coordinator.data` any more. #338 removed the `switch.py` writes.
- `UnifiInsightsFacadeCoordinator` is still `DataUpdateCoordinator[dict[str, Any]]`. Nothing in the type stops a reader from writing into a shared section.
- The facade holds domain state of its own: the per-site voucher settings and the latest generated voucher of each site. `number.py` changes `VoucherSettings` in place with `setattr`.
- The device and config coordinators still wrote into their published data after an `await`, so a facade rebuild in the middle of a refresh could publish dicts that changed later.
- About 110 reads of facade sections outside `coordinators/` (`protect` 51, `devices` 23, `stats` 8, `clients` 8, `wifi` 7, ...) and 71 calls that pass the whole `coordinator.data` to a helper, guarded by about 30 `isinstance(coordinator.data, dict)` checks.
- The test fixture `mock_coordinator` is a hand-written nested dict used by 732 tests, and tests write into it 474 times.

Three constraints shape the answer. `homeassistant.util.read_only_dict.ReadOnlyDict` is `@final` in Home Assistant 2026.10, so strict mypy rejects a subclass. A rebuild runs on every Protect WebSocket frame. And the dashboard WebSocket streams skip a rebuild when every input is the same object as last time, so the facade must pass the owners' sections through by reference.

## Decision

1. **A typed snapshot with a shallow runtime guard.**
   - `FacadeData` is a `TypedDict` whose keys are `ReadOnly[...]` and whose sections are typed as `Mapping`. The facade becomes `DataUpdateCoordinator[FacadeData]`.
   - At runtime the root is a `ReadOnlyDict`. It costs nothing, keeps section identity, and still works with JSON, `async_redact_data` and `isinstance(..., dict)`.
   - A nested write is a strict-mypy error, not a runtime check.
   - Test builders deep-freeze the data they build, so an entity that writes into a section fails in its tests.
2. **Owners publish copy-on-write.** Each owner builds its result locally and assigns `self.data` once, after its last `await`; an overlay replaces every container on the changed path (ADR-010). The facade never copies a section and never writes into one.
3. **Voucher state and commands move to the config coordinator**, which already owns the voucher inventory and its request and commit sequence guards.
   - `VoucherSettings` is a frozen dataclass. `get_voucher_settings(site_id)` returns it and `set_voucher_setting(site_id, field, value)` replaces it. A set notifies nobody: the settings live only in Home Assistant, the number entity writes its own state, and the generate button reads them when pressed.
   - `async_generate_voucher` and `async_delete_voucher` keep their names, parameters and return types. Services reach them through `NetworkSiteTarget.config_commands`.
   - The config coordinator publishes `latest_vouchers` in its own data, copy-on-write. It refreshes each site's record from the inventory it commits (the full poll and the targeted voucher refresh), keeps the record of a site without an inventory, and keeps the sticky expiry, activation time and guest count rules of `refresh_latest_voucher`. The facade passes `latest_vouchers` through by reference.
   - The post-change refresh keeps its policy: an authentication failure starts reauth, a `UniFiError` or `RuntimeError` is logged, and listeners are always notified.
4. **The facade holds no domain state.** It keeps aggregation, listeners, delegated availability, the owner accessors, `async_refresh_or_raise` and pure reads and exports. This amends ADR-002's facade (a typed, read-only projection) and the "Facade" row of ADR-010's ownership table: the voucher commands and the latest-voucher overlay now belong to the config coordinator.
5. **Lookups live in `data_transforms.py`** as pure functions on the snapshot: `network_device`, `network_device_stats`, `network_client`, `site_record`, `rule_record`, `protect_device` and `latest_voucher`. Readers move to them and drop their defensive guards.
6. **Facade data builders** in `tests/fixtures/facade_data.py` replace the hand-written `mock_coordinator` data and the WebSocket tests' in-place seeding.
7. **Availability is captured in the snapshot**, per domain and per site, so a reader gets one consistent answer for one snapshot.
8. **Protect WebSocket streams gate by identity** instead of a per-subscription JSON hash. This is safe because the Protect cache is copy-on-write since ADR-012.
9. **The InnerSpace view stays a derived memo in the facade**: a pure builder with a memo keyed on the identity of its inputs, replacing today's O(N) correlation key. A memo is not domain state.

### Where each part lands

The work lands as a series of pull requests under #342, in this order, without dates:

- Characterization tests for every `unique_id`, each shared section's identity and a rebuild during an in-flight refresh (merged in #386).
- Copy-on-write device and config refreshes (point 2).
- This ADR and the voucher move (points 3 and 4).
- The `FacadeData` type and a pure `build_facade_data`, the lookups, captured availability, the InnerSpace memo, helper signatures moved to `Mapping`, the test builders and Protect identity gating (points 1 and 5 to 9).
- Readers migrated to the lookups, split by area: `entity.py`, the Protect platforms, the Network platforms, and the WebSocket API, dashboards and diagnostics.
- Test modules moved from hand-written data to the builders, in mechanical batches.
- The remaining documentation.

Every `unique_id` and WebSocket contract stays unchanged; `tests/test_unique_ids.py` and `tests/coordinators/test_facade_identity.py` pin them.

## Alternatives considered

### Option A: A frozen dataclass snapshot

- Pros: attribute access, `frozen=True` enforced at runtime, typed fields.
- Cons: every reader of `data["..."]`, every helper that takes the whole mapping, the redaction in diagnostics and the WebSocket JSON would change at once; `isinstance(dict)` and `async_redact_data` stop working.

### Option B: `types.MappingProxyType` around the root

- Pros: a read-only view from the standard library.
- Cons: not a `dict`, so the same readers, guards and JSON encoding break; it protects only the root, like `ReadOnlyDict`, without its compatibility.

### Option C: A `ReadOnlyDict` subclass with typed properties

- Pros: lookups as methods on the snapshot.
- Cons: `ReadOnlyDict` is `@final` in Home Assistant 2026.10, so strict mypy rejects the subclass.

### Option D: A deep freeze, or a validated memo of frozen sections

- Pros: a nested write fails at runtime in production too.
- Cons: measured at about 3 to 6 ms per rebuild, and a rebuild runs on every Protect frame. Freezing lists into tuples also makes `async_redact_data` skip them (ADR-006).

### Option E: A separate voucher coordinator

- Pros: vouchers get an owner of their own.
- Cons: the inventory, its fetch and its sequence guards already live in the config coordinator; a second owner would split them or duplicate them.

### Option F: Keep the vouchers on the facade

- Pros: no change.
- Cons: the facade keeps domain state and an in-place `setattr`, which is exactly what this decision removes.

### Option G: A typed `TypedDict` root as a `ReadOnlyDict`, with frozen test builders (chosen)

- Pros: zero runtime cost, unchanged identity and JSON, nested writes caught by strict mypy, and any entity write fails in tests.
- Cons: a nested write is caught only by mypy and tests, not at runtime.

## Consequences

### Positive

- The facade is a projection of its owners' data: it holds no domain state, and a rebuild during a refresh sees a consistent set of sections.
- Every voucher write has one owner, like every other write since ADR-010; the facade no longer builds a fresh copy of the latest vouchers on each rebuild.
- Readers and tests get small typed lookups instead of nested `isinstance` chains.

### Negative

- Strict mypy and the test builders, not the runtime, enforce read-only sections.
- Until the readers migrate, the type is introduced without removing every guard.

### Neutral

- `VoucherSettings` objects are replaced, not changed, so code that keeps a reference to an older settings object keeps the older values.
- `tests/coordinators/test_config_vouchers.py` holds the voucher command and state tests.
