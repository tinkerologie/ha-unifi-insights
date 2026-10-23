# ADR-010: One owner per write command

**Date:** 2026-10-19
**Status:** Accepted (decided in the [design review](https://github.com/tinkerologie/ha-unifi-insights/issues/338#issuecomment-6260292560) of [#338](https://github.com/tinkerologie/ha-unifi-insights/issues/338), part of [#343](https://github.com/tinkerologie/ha-unifi-insights/issues/343); builds on [ADR-002](./002-multi-coordinator-architecture-behind-a-facade.md) and [ADR-009](./009-amend-flat-module-layout.md))

## Context

[ADR-002](./002-multi-coordinator-architecture-behind-a-facade.md) gives each data domain its own coordinator behind a push-only facade, but it does not say who owns a write. At `main` @ `0540ff0` the write path is mixed:

- `entity.async_call_coordinator_action` looks up a facade method by name string, 44 times in 8 modules. When the looked-up attribute is not a coroutine function it runs a `fallback_factory` that calls the API client directly (30 fallbacks in 5 modules). Production never takes a fallback, because every facade method exists. Tests use a `MagicMock` coordinator, so they always take it. `network_rule_switch.py` also stores the facade method name in an `action_method: str` field.
- The facade has 52 public `async_*` methods: 45 write or action entry points (24 of them Protect), 4 reads or exports and 3 refresh or lifecycle methods. Most actions are one-line pass-throughs through `_async_execute_api_action`, whose errors carry no translation key.
- Entities decide refresh and optimistic state. They refresh after 13 writes (11 in `switch.py`, 2 in `network_rule_switch.py`), the facade after 6 more, and `services.py` never. Five switch families patch the config coordinator's data in place through shared references.
- Two legacy site-name resolvers apply different rules. `resolve_legacy_site_name` quietly guesses `"default"`; `_require_verified_legacy_site_name` refuses to guess. The device coordinator keeps its own copy of the mapping and calls `sites.get_legacy_all()` every 30 s, although ADR-002 says the config coordinator owns it.
- `services.py` imports PoE port eligibility from the `button.py` platform.

Access (`coordinators/access.py`) and Carrier Fabric (`coordinators/carrier_fabric.py`) already own their commands: they validate, translate errors, write and refresh.

## Decision

### Ownership

A command belongs to the coordinator that owns the data the command changes and reconciles. That owner does four things for every write: it runs the call, raises a translated error, applies the optimistic overlay and applies the reconciliation policy. Entities and service actions call the owner directly with a typed call; no string dispatch, no fallback and no entity-side refresh or cache mutation.

The facade keeps aggregation, listeners, typed read accessors and the commands that need its own state (vouchers, `async_refresh_or_raise`). Its inheritance does not change: it stays a `DataUpdateCoordinator` with `update_interval=None`, delegated availability, `_sub_coordinator_unsubs` cleanup and voucher state. Entities still read and subscribe only to the facade, so ADR-002 and the deferred ADR-007 subscription question are unchanged.

| Owner          | Commands                                                                                                                                                               | Overlay                                                              | Reconciliation                                                                                                                                                                              |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Config         | Firewall rule, policy-based route, VPN client, port forward, traffic rule, NAT rule and static route enable                                                            | `enabled` on the rule record                                         | Awaited owner refresh; one 15 s retry when the family is still unavailable. A not-found or console-managed error gets one reconciliation refresh, then raises.                              |
| Config         | Wi-Fi network update                                                                                                                                                   | None                                                                 | Deferred owner `async_request_refresh()`                                                                                                                                                    |
| Config         | Guest authorise and unauthorise                                                                                                                                        | None                                                                 | None                                                                                                                                                                                        |
| Device         | Outlet state                                                                                                                                                           | `relay_state` or `cycle_enabled` on the outlet, as an intent overlay | Deferred owner `async_request_refresh()`; the written field is held over polls that still report the old value, and a pre-PUT provenance gate sends only a complete, trusted payload (#376) |
| Device         | Client block and unblock                                                                                                                                               | None                                                                 | Deferred owner `async_request_refresh()`                                                                                                                                                    |
| Device         | Restart (a falsy result raises), PoE power cycle, client reconnect, forget, batch forget and wake                                                                      | None                                                                 | None                                                                                                                                                                                        |
| Protect        | Camera update and its `async_update_camera_settings` alias, HDR mode, video mode, microphone volume, light mode and brightness, chime volume and repeat, viewer update | The written fields                                                   | WebSocket frames and the next poll                                                                                                                                                          |
| Protect        | Chime ringtone and paired cameras                                                                                                                                      | The written fields                                                   | Targeted Protect refresh; its failure does not fail the write                                                                                                                               |
| Protect        | Alarm arm and disarm                                                                                                                                                   | None                                                                 | Refresh after success; the global alarm manager refusal is translated                                                                                                                       |
| Protect        | Siren stop                                                                                                                                                             | `mark_siren_stopped` hook, unchanged                                 | Unchanged                                                                                                                                                                                   |
| Protect        | PTZ preset, patrol start and stop, trigger alarm, create liveview, chime play (deliberately unsupported), siren play and volume, talkback                              | None                                                                 | None                                                                                                                                                                                        |
| Access         | Unlock, lock, gate command, emergency mode, user PIN set and remove, user status (7 commands)                                                                          | Unchanged                                                            | Unchanged: awaited read-back, emergency serialisation and fatal emergency verification                                                                                                      |
| Carrier Fabric | Subscriber suspend and resume                                                                                                                                          | None                                                                 | Unchanged: one conflict retry and an awaited refresh                                                                                                                                        |
| Facade         | Voucher generate and delete, `async_refresh_or_raise`                                                                                                                  | Latest-voucher cache                                                 | Targeted voucher refresh with its sequence guards; a refresh failure is logged, an authentication failure starts reauth                                                                     |

Moved commands keep their parameter names, keyword-only markers and return annotations (`bool` for restart, PoE, outlet and batch forget; `list[Voucher]`; `TalkbackStream`; `**kwargs: Any` on the camera and viewer updates). Owner commands take native or opaque IDs and work without an entity; an entity translates a display name or an observed option into that ID before it calls the owner. `network_rule_switch.py`'s `action_method: str` becomes a typed callable that returns the bound owner command.

No entity calls an API client: the Protect owner also serves the camera snapshot and stream reads (`async_get_camera_snapshot`, `async_create_camera_stream_url`), which return `None` on failure so the camera keeps its static stream fallback. Service actions reach the Protect owner through `require_protect_coordinator()` after the usual console routing.

### Shared executor

`coordinators/base.py` has one stateless module-level function, `async_execute_command`, that the facade and the domain owners call directly. It returns the call's result, passes a `HomeAssistantError` through unchanged and wraps any other exception in a translated `HomeAssistantError` raised `from err`, so the immediate cause stays the API error. A pydantic `ValidationError` is logged with sanitised field names only and raised as `invalid_api_response` with the chain suppressed. An optional rejection key turns a falsy result into a translated error. Access keeps `_command_error` and Carrier Fabric keeps its retry and typed errors; they do not use the executor.

A function rather than a base-class method, because the facade does not inherit `UnifiBaseCoordinator` and must keep its `None` update interval.

### Refresh failure after a successful write

A successful write stays successful when the reconciliation refresh fails. `DataUpdateCoordinator.async_refresh()` can return normally with `last_update_success=False`, so an owner that awaits a refresh inspects that flag, logs the failure and returns. An authentication failure starts reauth, as the voucher path already does. `refresh_failed` is the translated error for the explicit refresh entry points (`async_refresh_or_raise` and the multi-console refresh service), not for writes.

### Optimistic overlay

After a successful write the owner copies every mutable container on the changed path (dictionaries and lists), replaces each ancestor up to its own `data` root and calls `async_update_listeners()`. It never mutates a shared record in place and never calls `async_set_updated_data()`. There is no rollback because the overlay only follows success. A poll that started before the write can finish after the overlay and briefly restore the older value; the next poll or WebSocket frame corrects it. PDU outlet writes are the exception: the controller replaces the whole `outlet_overrides` array, so a stale array would turn earlier outlets back on with the next write. `async_set_outlet_state` therefore holds the coordinator's refresh lock (`_debounced_refresh.async_lock()`) from reading the cached record to publishing. An in-flight poll finishes first, and overlapping outlet commands run one after the other (#371). A poll that starts after the write can still read the controller's older array for a few seconds while the PDU applies the change, and the next outlet command, built from that array, would switch the earlier outlet back (#376). For this reason alone the Device coordinator keeps two separate outlet-only guards, a narrow exception to the rule below.

The first is a per-field intent overlay. After a successful outlet write it records the intended field (`relay_state`, or `cycle_enabled` for a cycle write, whose `state` only repeats the cached relay) with the sent `outlet_overrides` entry and a `time.monotonic()` stamp, keyed by `(site_id, device_id, outlet_index)`; a later write to the same outlet adds its field and renews the stamp. A failed write records nothing. On each successful poll, after the last await and before the single publish, the pure `reconcile_pending_outlet_writes` patches each unconfirmed field into copies of the polled `outlet_overrides` and `outlet_table` entries (appending the sent entry when a non-empty polled array lacks the outlet); every other polled field still updates from that poll. A field is released when every polled array that has the outlet reports the written value, and the whole entry when its device or site is gone from the poll or `OUTLET_WRITE_GUARD_SECONDS` (60 s) have passed since the last write to the outlet, so a write the controller rejects or never applies gives way to the poll within a minute. Release changes only the overlay: confirmation or expiry never drops or establishes payload integrity, and a saved entry is never a provenance input.

The second is a per-device payload-integrity provenance record, evaluated on the controller's untouched raw outlet arrays, captured before the display normalizer and before the overlay. A poll is trusted only when `outlet_table` is a list, `outlet_overrides` is a list or absent, every row is a dictionary with a unique integer index through `index` or `outlet_idx`, the effective baseline covers a persistent required index set, and every baseline row keeps a boolean `relay_state` plus `cycle_enabled` and `name` whenever the raw matching table row carries them (raw field presence decides that requirement, never a normalizer's manufactured default). The effective baseline is a non-empty `outlet_overrides`, or otherwise the seed from a structurally complete table (the #358 cold-start case). The required set is the union of the indexes from trusted polls for the same identity: site, device ID, classic `_id`, normalized MAC and source. It never shrinks while that identity holds; a poll that omits a required index is untrusted, and an identity change discards the record as a cold start. A `GETTING_READY` poll is judged by the same structural rule.

Inside the existing refresh lock, after building the payload and before transport, `async_set_outlet_state` sends a PUT only when that provenance is trusted for the cached identity, the built payload is not `None`, and it has exactly one row for every required raw index (and no other row), the target index and every row's raw-required fields. Otherwise it raises the translated `command_failed` error and sends no PUT, records no pending write, publishes nothing and requests no refresh. Only a successful poll that passes the rule sets trust, and a successful poll that fails it clears trust; a failed refresh changes neither the overlay nor the provenance. This is a structural safeguard, not proof of the physical outlet inventory or the controller's semantic completeness: non-pending sibling values can still be stale during `GETTING_READY`, and writable fields beyond the required ones stay unverified. Apart from these two guards no generation tracking is added; the existing NVR and siren generation counters and voucher sequence guards stay as they are.

### One legacy site-name rule

The config coordinator publishes `legacy_site_names` and `legacy_mapping_failed` in its data and owns the only resolver, `resolve_legacy_site_name(site_id, *, verified_only)`. The order is fixed: the site's verified `internalReference`, then (unless `verified_only`) the published mapping, then a translated `legacy_site_not_found` error. It never guesses `"default"`; a real `"default"` from either source is valid. Rule families, historical clients and batch forget use `verified_only=True`.

Mapped writes call `async_resolve_legacy_site_name(site_id, *, verified_only)`. When resolution fails while the mapping is flagged as failed or lacks the site, it looks up the legacy mapping once more, republishes it and retries before raising, so a lookup that failed in the first refresh does not block writes until the next config poll. Concurrent callers share one lookup, and at most one lookup runs per 60 s, so a classic API that is down is not asked on every write. Verified-only writes and unknown sites never trigger it.

The published mapping follows one lifecycle. A successful lookup replaces it for the selected sites and clears the flag. A failed lookup keeps the last published mapping pruned to the selected sites (empty on the first failure) and sets the flag. No selected sites publishes an empty mapping with the flag cleared. A whole-refresh failure leaves the published mapping unchanged. The per-cycle section inputs stay local, so a cycle whose lookup failed still fetches legacy sections without a site name, as before.

The device coordinator reads the published mapping through the config coordinator's `legacy_site_names` property on each poll instead of calling `sites.get_legacy_all()`, and keeps no copy of its own. Each poll uses the mapping the config coordinator published last, by its own poll or by an on-demand lookup. At setup the config coordinator's first refresh completes before the device coordinator's first refresh, the platforms and the service handlers' targets exist. A site without a mapped name is polled without the legacy device telemetry, as after a failed lookup before. During a lookup outage, device telemetry and mapped commands keep the last-good mapping for still-selected sites, while verified-only writes stay strict.

### PoE eligibility

PoE port lookup and eligibility live in `coordinators/device.py`, next to the power-cycle command, and read #337's port facts (`device_facts`, `NetworkPortFacts`). The `device_not_found`, `port_not_found` and `port_not_poe` validation is shared by the button and the service action. `services.py` no longer imports a platform module.

### Tests

Each command is tested once, in the test file of its owner (`tests/coordinators/test_config.py`, `test_device.py`, `test_protect.py`, the future ADR-007 mirror), against seeded real owners and `unittest.mock.create_autospec` of the real `unifi_official_api` 3.0.0 client and endpoint classes. Calling a method or argument that the library does not have fails the test. Entity tests check only the typed call and the optimistic state.

### Protect module size

Moving the 24 Protect actions and `_async_send_arm_command` adds about 370 facade lines to `coordinators/protect.py` (3,079 lines today), about 3,450 before deduplication. That is inside the 3,500-line cap that [ADR-009](./009-amend-flat-module-layout.md) already records until #340 splits the module; this ADR adds no new exception. Duplicated logic is removed during the move and no satellite file is created. After the move the module is 3,498 lines: the overlay reuses `_deep_merge` (with `into_lists`) instead of a second merge helper, and the ringtone and liveview name checks stay with the select entities that translate them.

## Alternatives considered

### Option A: Keep every command on the facade with typed calls

- Pros: smallest diff; one place to look for writes.
- Cons: the facade keeps growing (17 of the last 40 commits touched it, mostly for one pass-through), and it cannot apply an overlay to data it does not own without the shared-reference mutation this decision removes.

### Option B: Domain coordinators own their commands (chosen)

- Pros: follows the Access and Carrier Fabric precedent; the owner of the data is the only layer that can publish an overlay safely and pick the right reconciliation; facade growth stops.
- Cons: callers need typed access to the owning coordinator; `coordinators/protect.py` grows until #340.

### Option C: Per-domain command service classes held by the facade

- Pros: commands are separate from polling code.
- Cons: a new abstraction and lifecycle for every domain, and the service still has to reach into the coordinator's data to apply an overlay.

## Consequences

### Positive

- One code path per write, the same in production and in tests, so a test can no longer pass on a fallback that production never runs.
- Every command error is translated, and refresh and overlay behaviour is decided once per command family.
- One legacy site-name rule, and one fewer classic API call every 30 s.

### Negative

- Writes that used to guess `"default"` now raise `legacy_site_not_found` when no mapping is known (for example while the classic site lookup stays down; one on-demand lookup per 60 s is tried first).
- `coordinators/protect.py` stays over the 2,000-line budget until #340.

### Neutral

- The work lands in phases inside #338: foundations first, then Network commands, Protect and remaining commands, then removal of the string dispatch.
- #339's service-handler fixtures autospec these owner classes.
