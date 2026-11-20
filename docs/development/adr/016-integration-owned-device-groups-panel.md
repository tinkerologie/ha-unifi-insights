# ADR-016 — An integration-owned panel for devices grouped by uplink

- **Date:** 2026-11-20
- **Status:** Accepted
- **Issue:** [#519](https://github.com/tinkerologie/ha-unifi-insights/issues/519)

## Context

Users asked for an option that lists UniFi devices under the switch, access
point or gateway they are directly connected to, instead of as one flat list.
Device names, IDs, areas and automations must not change, and the option must
be reversible.

Home Assistant's own integration page cannot show this:

- The frontend nests device rows by `parent_device_id` only, one level deep,
  in `groupDevicesByParent`
  ([`src/data/device/device_registry.ts`](https://github.com/home-assistant/frontend/blob/dev/src/data/device/device_registry.ts)).
  It offers no hook for an integration to change or extend that grouping.
- `parent_device_id` means "a part of the same physical device". A switch and
  the access point plugged into it are independent devices, so setting it
  would misrepresent them and would rewrite registry rows.
- `via_device_id` records a route but does not nest rows on that page, and it
  would also rewrite registry rows.

The link data is uneven. A Network device's classic `uplink` names its current
parent; `last_uplink` keeps the parent from before the device went offline and
carries no freshness marker. A client's v1 `uplinkDeviceId` is current; the
classic `/stat/sta` link can be up to five minutes older. The topology card
(ADR-005) uses all of these on purpose, to draw a best-effort graph.

Every option change used to reload the entry. Setup reconciles registry rows
against the options (client trackers, client buttons, port sensors), so a
reload for a presentation-only option could change rows for no reason.

## Decision

1. **The integration owns a panel.** `frontend.py` registers one
   administrator-only custom panel, **UniFi devices**
   (`/unifi-insights-devices`, element `unifi-insights-devices-panel`, module
   `device-groups-panel.js`), with `panel_custom`. The panel exists while at
   least one loaded Network/Protect entry has the
   `group_devices_by_uplink` option on, and is removed when none does. A lock
   in `hass.data` serialises the register and remove calls. A headless
   instance, or a tree without the bundle, skips registration.
2. **Registry rows stay independent.** The grouping code reads registry rows
   and never creates, updates, merges or removes one. It never sets
   `parent_device_id` or `via_device_id`, and unique IDs do not change.
3. **Grouping is strict.** `device_grouping.py` builds a read-only model per
   entry from the facade snapshot and the entry's own registry rows:
   - A Network device nests under its parent only when the parent comes from
     the current `uplink` (the device coordinator now marks the topology block
     with `uplink_source: current` or `last`), its MAC matches exactly one
     device in the same site, the link is not to itself, and the device
     coordinator is available.
   - A Protect camera that is not also a Network device is resolved to its
     device page through its own camera entity row. It nests under the v1
     `uplinkDeviceId` of the one selected-site client with its MAC. The
     classic client link only corroborates that parent and then supplies the
     port; naming another device makes the camera ambiguous.
   - Edges in a cycle are dropped. Anything that cannot be placed stays at the
     top level with a reason (`no_uplink`, `historical`, `ambiguous`,
     `parent_not_found`, `cycle`, `stale`). A camera without a unique site is
     **Unassigned**, and every other device row of the entry is listed under
     **Other devices**. No device is hidden.
4. **A versioned, allowlisted contract.** The admin-only WebSocket commands
   `unifi_insights/device_groups/sources` and
   `unifi_insights/device_groups/subscribe` send full snapshots (schema
   version 1) with flat rows, a content revision and no IP or MAC addresses.
   A stream is rebuilt on coordinator data changes and on registry events for
   rows the entry owns, sends only new revisions, and ends with
   `entry_unloaded` or `grouping_disabled`.
5. **A grouping-only change is applied in place.** Setup records the entry
   data and the effective options except grouping. When an update changes
   only the grouping option of a loaded entry, `async_reload_entry` updates the
   runtime value, ends the entry's streams if it was turned off, and
   reconciles the panel. Any other change, or any doubt, reloads as before.

## Alternatives considered

- **Set `parent_device_id`.** Gives native nesting, but misrepresents
  independent devices and rewrites registry rows. Rejected.
- **Set `via_device_id` only.** Rewrites registry rows and gives no nesting on
  the integration page. Rejected.
- **A dashboard card or card mode.** The option could not control cards users
  have already placed, and cards are visible to non-administrators. Rejected.
- **Reuse the topology graph's permissive links.** Would nest devices under
  historical or stale parents, which the issue rules out. Rejected; the
  topology card keeps its behaviour.
- **Keep reloading on every option change.** Simple, but gives no guarantee
  that rows survive a presentation-only toggle. Kept only as the fallback.

## Consequences

- Positive: the native device list and every registry row are unchanged in
  both states, and the toggle is reversible without a reload.
- Positive: administrators get a grouped view with links to the existing
  device pages, and devices without a certain link stay visible with a reason.
- Negative: the grouped view lives outside the integration page, so users must
  open the sidebar panel. Native support needs a Home Assistant frontend
  change: a grouping hook, or nesting by a relationship other than
  `parent_device_id`.
- Neutral: the device coordinator's topology block gains an internal
  `uplink_source` key. The topology snapshot serialises an allowlist, so its
  schema version 3 payload and revisions do not change.
