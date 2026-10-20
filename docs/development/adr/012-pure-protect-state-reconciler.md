# ADR-012: A pure reconciler for Protect state

**Date:** 2026-10-20
**Status:** Accepted (decided in the [design review](https://github.com/tinkerologie/ha-unifi-insights/issues/340#issuecomment-6281663377) of [#340](https://github.com/tinkerologie/ha-unifi-insights/issues/340), part of [#343](https://github.com/tinkerologie/ha-unifi-insights/issues/343); implements the `protect_ws.py` / `protect_events.py` seam of [ADR-007](./007-flat-home-assistant-module-layout.md) and ends the size exception in point 4 of [ADR-009](./009-amend-flat-module-layout.md))

## Context

The Protect coordinator has two sources that race each other: the "devices" and "events" WebSocket streams, and a 30 s REST poll. Before #340, `coordinators/protect.py` was 3,482 lines, just under ADR-009's 3,500-line cap. Every rule about which source wins was a private method that changed `self.data` in place:

- sensor door, motion, tamper and leak state preserved against older REST answers;
- the NVR and siren generation guard;
- alarm hub tamper overlays;
- siren stop guards;
- the motion, smart-detect and ring latches and their five-minute sweep;
- the bounded empty, 404 and transient-error windows;
- missing-poll eviction.

The state behind those rules sat in 15 private trackers, read with two clocks. The rules could only be tested by building a coordinator, poking its trackers and calling the private methods: 63 tests and 4 helpers did exactly that. ADR-010 had already made command overlays copy-on-write. The frame and poll paths had not.

## Decision

1. **`coordinators/protect_events.py` is a pure, functional reconciler:**

   ```python
   reconcile(state, cache, input, now) -> Step(state, cache, effects)
   begin_request(state, family, now) -> RequestStamp
   ```

   - `state` is a frozen `ReconcilerState` that replaces the 15 trackers.
   - `now` is an `Instant` that carries both clocks: monotonic for request stamps, WebSocket recency, tamper records and the siren guard TTL; wall time for latch starts and expiry and the siren stop fallback.
   - The inputs are typed: `DeviceFrame`, `EventFrame`, `RestBatch(RequestStamp, outcome)`, `PollStarted`, `StreamReconnected`, `PollCompleted`, `SirenStopped` and `CommandOverlay`.
   - The effects are data: `PublishUpdate(mark_success)`, `RemoveDevice`, `Escalate` and `Log`.
   - The module imports nothing from Home Assistant or `unifi_official_api`, imports no `time` and never reads a clock. A test parses it and enforces this.

2. **`coordinators/protect_ws.py` parses frames and tracks stream health.** It turns a raw frame into a `DeviceFrame`, `EventFrame`, `RateLimited` or nothing, keeps each stream's health, and owns the warn-once flags.
3. **The coordinator does the I/O and carries out the effects.** `coordinators/protect.py` keeps the REST fetches, the family support bookkeeping, the global alarm manager probe, the WebSocket lifecycle, the timers, the debouncer and the commands.
   - `_apply(input)` is the only writer of the reconciler state. It commits the new state and cache, then runs the effects in order.
   - A fetcher stamps its request with `begin_request` before the `await` and applies the answer with no `await` in between.
   - Liveviews are the one REST answer that does not go through the reconciler: they have no frames, latches or eviction, so the coordinator writes them itself, copy-on-write.
   - No coordinator code holds `self.data` or the reconciler state across an `await` to write from it.
4. **The cache is copy-on-write everywhere** (as ADR-010 does for overlays). A step returns a new root when anything changed and shares every untouched collection and record. The notification calls stay exactly as they were: a device frame calls `async_set_updated_data`, which also re-arms the poll timer; an event frame, a siren stop and a command overlay call `async_update_listeners`; a REST answer, a poll boundary and a reconnect notify nobody.
5. **Behavior is preserved, quirks included.** Each quirk found in the review is pinned by a test whose ID contains `quirk` and has its own follow-up issue (see Consequences). Fixing one is a separate, reviewed change.

## Alternatives considered

### Option A: A stateful reconciler object owned by the coordinator

- Pros: fewer types; the coordinator calls methods, as before.
- Cons: the state is still changed in place behind the coordinator's back, so a test still has to build one and inspect private fields, and nothing stops a method from reading a clock or the cache across an `await`.

### Option B: Split `protect.py` by device family

- Pros: every module gets smaller.
- Cons: the races are between sources, not families. Sensors, sirens and alarm hubs each mix frames, REST and timers, so a family split would copy the clocks, generation counters and eviction into several modules.

### Option C: A pure, functional reconciler with effects as data (chosen)

- Pros: every rule is testable as a table of inputs and expected steps (151 tests), with checks that the inputs are never changed. Both clocks are explicit. `protect.py` falls under ADR-007's 2,000-line budget.
- Cons: more types, and every cache change pays for a shallow copy of the collections it touches.

## Consequences

### Positive

- `coordinators/protect.py` is about 1,770 lines, which ends ADR-009's size exception. `protect_events.py` is about 1,690 and `protect_ws.py` about 300.
- Tests reach the coordinator through its real seams: frames through the WebSocket callbacks, REST through the client mocks, polls through the public refresh. Eight characterization scenarios in `tests/coordinators/test_protect_scenarios.py` passed unchanged through the whole refactor.
- Listeners can never see a half-applied step. An input that makes `reconcile` raise commits nothing; once it returns, `_apply` commits the new state and cache before running the effects, so an effect that fails (a listener or the registry removal raising) does not roll the step back.

### Negative

- Each event frame copies its event type's map in the event store, and that store never shrinks. Bounding it is [#379](https://github.com/tinkerologie/ha-unifi-insights/issues/379).
- Reconciler logs keep the `coordinators.protect` logger name, so they read as the coordinator's lines.

### Neutral

- The quirks pinned as `quirk` tests stay as they were until their follow-up issues are decided: the event store grows without limit ([#379](https://github.com/tinkerologie/ha-unifi-insights/issues/379)); the latch sweep runs only on a poll or a reconnect ([#381](https://github.com/tinkerologie/ha-unifi-insights/issues/381)); a reconnect sweep notifies nobody ([#382](https://github.com/tinkerologie/ha-unifi-insights/issues/382)); a REST rebuild of a light can write a false `lastMotionEnd` five minutes later ([#383](https://github.com/tinkerologie/ha-unifi-insights/issues/383)); and a frame with an unknown model key still re-arms the poll timer ([#384](https://github.com/tinkerologie/ha-unifi-insights/issues/384)).
