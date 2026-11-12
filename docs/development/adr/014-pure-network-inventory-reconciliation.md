# ADR-014 — Reconcile Network inventory before registry changes

- **Date:** 2026-11-07
- **Status:** Accepted
- **Issue:** [#475](https://github.com/tinkerologie/ha-unifi-insights/issues/475)

## Context

To find stale devices, the Device coordinator compares the current poll with
previous polls. It uses MAC addresses from both Network listings. If a site poll
fails, the Device coordinator keeps the previous inventory and registry entries
for that site.

## Decision

The Device coordinator collects one evidence record for each site that it polls.
`device_inventory.reconcile_inventory` uses these records and the current
inventory to calculate the next inventory. It also returns the registry entries
to remove, in removal order.

The function uses only its input data. The Device coordinator stores the new
inventory and publishes the snapshot together. Then it removes the specified
registry entries.

See the Protect state function in [ADR-012](012-pure-protect-state-reconciler.md)
and the snapshot rules in [ADR-013](013-typed-read-only-facade-snapshot.md).

## Alternatives

With the rules in the Device coordinator, tests of the removal rules also need
the coordinator and the registry. With a separate inventory owner, the
integration would store the poll results in two places.

## Consequences

The Device coordinator controls changes to the inventory and the registry.
The rules for missing devices, MAC address checks, and changes in polling
source stay the same. The snapshot fields and device identifiers stay the same.
