# ADR-009: Amendments to the flat module layout for the deepening issues

**Date:** 2026-10-19
**Status:** Accepted (amends [ADR-007](./007-flat-home-assistant-module-layout.md); decided in the design review of [#337](https://github.com/tinkerologie/ha-unifi-insights/issues/337), [#338](https://github.com/tinkerologie/ha-unifi-insights/issues/338) and [#339](https://github.com/tinkerologie/ha-unifi-insights/issues/339), part of [#343](https://github.com/tinkerologie/ha-unifi-insights/issues/343))

## Context

ADR-007 sets a target of 33 root modules, a size budget of about 2,000 lines per module, and a rule that a new module is justified only by a new platform, coordinator or Home Assistant surface. It also names `data_transforms.py` as the only home for `normalize_mac()` and `entity.py` as the home of `get_field()` and `first_not_none()`.

The coding plans for the three deepening issues each ran into one of these rules:

- **#337 (network device read model)** puts the read model in `data_transforms.py`, which must stay free of Home Assistant imports so that the coordinator and the dashboard builders can use it. `get_field()` and `first_not_none()` live in `entity.py`, which imports Home Assistant and the coordinators, so the pure builders cannot import them from there. The plan also found five MAC normalisers. Four of them protect identities that are already persisted or matched: config entry console identity, legacy device matching, InnerSpace correlation and service-call user input. Replacing them with one parser would change unique IDs and lookups, which #337's acceptance criteria forbid.
- **#338 (one command path for writes)** moves every Protect command to the coordinator that owns Protect data. `coordinators/protect.py` is already 3,081 lines and would reach about 3,450 before #340 splits it.
- **#339 (service target resolver)** removes about 1,300 lines of routing from `services.py`, which is 2,998 lines. Kept in `services.py`, the deduplicated routing would still leave the module at about 2,500 lines. Service routing is not a new platform, coordinator or Home Assistant surface.

## Decision

Amend ADR-007 as follows. Everything else in ADR-007 is unchanged.

1. **`get_field()` and `first_not_none()` live in `data_transforms.py`.** `entity.py` re-exports them so existing call sites do not change. All pure helpers that read UniFi records use the same first-non-`None` semantics. Protect's `_pick_field` keeps its truthy semantics on purpose.
2. **`data_transforms.normalize_mac()` is the canonical MAC parser for reading device data, not the only MAC function.** Four identity-specific policies stay as separate, documented functions: `console_identity.normalize_mac` (config entry unique IDs), `coordinators/device._normalize_mac` (legacy device matching), `innerspace_transforms._normalize_innerspace_mac` (InnerSpace correlation) and `services._mac_key` (service-call input). One parametrised contract test pins all five, so none can drift silently.
3. **`service_targets.py` is an allowed 34th root module.** It holds the service-call target resolution that `services.py` imports. Services and their target routing are a recorded seam, like the `protect_ws.py` / `protect_events.py` split. Only call-level resolvers are public; family resolvers and helpers stay private. The target layout becomes 34 root modules.
4. **`coordinators/protect.py` has a capped size exception of 3,500 lines** until #340 splits it along the recorded `protect_ws.py` / `protect_events.py` seam. No other module gets an exception. When #340 lands, the module returns under the 2,000-line budget and this exception ends.

## Alternatives considered

### Option A: Keep ADR-007 unchanged

- Pros: no exceptions to track.
- Cons: #337 would need a copy of `get_field()` in each module or an import cycle; unifying all MAC normalisers would change unique IDs; `services.py` would stay over budget after #339; #338 would have to leave Protect commands on the facade, which defeats the purpose of one command owner.

### Option B: Amend case by case in each pull request

- Pros: each change is next to the code that needs it.
- Cons: three PRs would each edit ADR-007, and the exceptions would be harder to find. ADR-001 says an accepted change of meaning gets its own record.

### Option C: One amendment ADR for all four (chosen)

- Pros: the exceptions are listed in one place, each with its reason and its end condition.
- Cons: ADR-009 is recorded before #338 and #339 are built, so its numbers are estimates. The cap in point 4 is a hard limit, not an estimate.

## Consequences

### Positive

- The read model, dashboards and coordinators share one set of field and MAC rules without importing Home Assistant.
- Unique IDs stay unchanged, and the identity policies are named and tested instead of being accidental duplicates.
- `services.py` can get under the 2,000-line budget.

### Negative

- The layout has 34 root modules instead of 33.
- `coordinators/protect.py` is over budget until #340 lands.

### Neutral

- ADR-007 stays Proposed until #341 merges. Its target layout and module roles now read together with this ADR.
