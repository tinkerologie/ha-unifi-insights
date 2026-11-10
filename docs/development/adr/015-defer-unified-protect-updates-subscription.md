# ADR-015 — Defer unified Protect updates subscription

- **Date:** 2026-11-10
- **Status:** Accepted
- **Issue:** [#501](https://github.com/tinkerologie/ha-unifi-insights/issues/501)

## Context

UniFi Protect v7.4.96 introduced a unified WebSocket endpoint at
`/v1/subscribe/updates`, taking an optional query parameter `modelKeys` to stream
combined device and event updates over a single WebSocket connection. Currently,
the integration maintains two separate WebSocket connections to Protect:
`/v1/subscribe/devices` for device state and `/v1/subscribe/events` for event frames.

Replacing the two dedicated streams with `/v1/subscribe/updates` presents several
technical and architectural challenges in the current codebase:

1. **Client library support:** `unifi-official-api==3.1.0` has no client method
   for `/v1/subscribe/updates`. Under ADR-008, the integration cannot vendor shims or
   issue raw HTTP/WebSocket calls.
2. **Payload format differences (ID arrays):** Device messages on the unified
   stream can deliver an array of IDs in a single message payload, whereas
   `protect_ws.py` currently assumes a single string `id` per message frame.
3. **Removal message semantics:** The current parser and reconciler treat incoming
   WebSocket messages as shallow dictionary merges (state updates). `remove` action
   messages would require distinct handling rather than merging into entity cache.
4. **Event retention and removals:** The unified stream does not emit deletion/removal
   frames for historical events. The integration already handles event pruning via
   reconciler retention sweeps.
5. **External arm profile domain:** Unified updates include `externalArmProfile`
   messages, which require a dedicated arm profile reconciler and collection handling
   not yet present in Phase 1.
6. **Diagnostics and health isolation:** Integration diagnostics and runtime health
   monitoring track `devices` and `events` stream states independently. Collapsing
   them into one stream requires redesigning stream failure domains.
7. **Unverified wire format:** The unified stream schema in OpenAPI v7.4.96 has not yet
   been validated against captures from physical hardware.

## Decision

Defer replacing the separate `/v1/subscribe/devices` and `/v1/subscribe/events`
connections with `/v1/subscribe/updates`. Maintain the existing two-stream
architecture until all migration prerequisites are met.

### Prerequisites for migration

Before migrating to `/v1/subscribe/updates`, the following prerequisites must be
satisfied:

- **Upstream client method:** `unifi-official-api` provides a typed subscription
  helper for `subscribe_updates(model_keys: list[str] | None = None)` using the
  `modelKeys` query parameter.
- **Live frame captures:** Verified WebSocket frame captures from physical UniFi OS
  consoles running Protect v7.4.96+ confirming payload envelopes for device, event,
  and removal actions.
- **WebSocket parser extension:** `coordinators/protect_ws.py` extended to support
  payloads with ID arrays and explicit removal actions without breaking backward
  compatibility.
- **Combined health model:** A unified stream health, reconnection, and backoff
  lifecycle model designed for `protect_ws.py` and reflected in diagnostics.

## Alternatives

- **Immediate cutover with raw WebSocket connection:** Bypass `unifi-official-api`
  to connect directly to `/v1/subscribe/updates`. Rejected: directly violates ADR-008
  and bypasses established authentication and token management.
- **Single stream for devices only:** Use `/v1/subscribe/updates?modelKeys=camera,sensor...`
  while keeping `/v1/subscribe/events`. Rejected: adds complexity without reducing
  connection overhead or eliminating any architectural gaps.

## Consequences

- The integration continues running two WebSocket streams (`devices` and `events`)
  per Protect NVR.
- Existing device state preservation, per-group debounce, and event windowing
  behaviours in `coordinators/protect_events.py` remain intact and unaffected.
- No changes to `protect_ws.py` or diagnostic health reporting are required for
  v7.4.96 Phase 1.
