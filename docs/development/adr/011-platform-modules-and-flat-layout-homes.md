# ADR-011: Platform module size and homes for the remaining shared helpers

**Date:** 2026-10-20
**Status:** Accepted (amends [ADR-007](./007-flat-home-assistant-module-layout.md) and [ADR-009](./009-amend-flat-module-layout.md); decided in the design review of [#341](https://github.com/tinkerologie/ha-unifi-insights/issues/341), part of [#343](https://github.com/tinkerologie/ha-unifi-insights/issues/343))

## Context

ADR-007 moves every entity into its standard platform module and sets a budget of about 2,000 lines for every module. ADR-009 amended it for the deepening issues #337, #338 and #339. After those issues landed, the #341 design review measured the moves again on `main`:

- **`sensor.py` cannot fit the budget.** It is already 2,200 lines. The seven sensor satellites (vouchers, site internet activity, Carrier Fabric, InnerSpace, storage, Protect security and Mobility) add about 1,450 lines of sensor code. Unifying the same-shape site and Protect sensor classes and moving pure value functions into `data_transforms.py` was estimated to bring the merged module to about 3,250 lines; after the merge it is 3,724 lines, so well over 2,000 either way. ADR-007 forbids splitting it into satellites.
- **Every other merge fits.** Estimated sizes: `binary_sensor.py` about 1,450 lines, `switch.py` about 1,300 after the rule switch and outlet unification, `entity.py` about 950, `data_transforms.py` about 1,400, `dashboard_snapshots.py` about 950 and `topology.py` about 800.
- **`async_get_device_entry()` cannot live in `entity.py`.** ADR-007 sends it there, but `coordinators/device.py`, `coordinators/protect.py` and `coordinators/innerspace.py` call it, and `entity.py` imports the coordinators. Putting it in `entity.py` creates an import cycle.
- **`data_transforms.py` and Home Assistant imports.** ADR-009 says `data_transforms.py` stays free of Home Assistant imports so that coordinators and dashboard builders can use it. `coordinators/voucher_state.py`, which ADR-007 merges into it, uses `homeassistant.util.dt` to parse and compare timestamps. That module imports nothing from the integration and cannot create a cycle.
- **`helpers.py` holds more than ADR-007 names.** Besides the identity migration, `is_doorbell_camera_model()` and `async_get_device_entry()`, it holds `get_port_label()` and `owned_site_entity_unique_id()`.

Home Assistant Core's own platform modules show that a platform file is sized by the number of entity kinds it serves, not by a fixed limit. Core integrations with many data sources keep one `sensor.py` with one section per source.

## Decision

Amend ADR-007 and ADR-009 as follows. Everything else in them is unchanged.

1. **The 17 platform modules are exempt from the 2,000-line budget.** A platform module holds only `async_setup_entry`, `PARALLEL_UPDATES`, the platform's `EntityDescription` tables, its entity classes and its discovery functions, in one section per data source (Network, Protect, Access, InnerSpace, Mobility, Carrier Fabric). Pure value extraction belongs in `data_transforms.py`, base classes in `entity.py`. Same-shape entity classes are unified before a satellite is merged. The budget still applies to every other module, and the ADR-009 `coordinators/protect.py` exception is unchanged.
2. **`async_get_device_entry()` lives in `console_identity.py`**, with the other registry identity helpers. Coordinators, platforms and `websocket_api.py` import it from there.
3. **`helpers.py` is dissolved as follows.** `console_identity.py` takes the site entity identity helpers: `owned_site_entity_unique_id()`, `async_migrate_site_entity()`, `async_restore_site_entity_identities()`, `async_rekey_site_entities()`, `SiteEntityIdentityError` and `async_get_device_entry()`. `data_transforms.py` takes the pure functions `get_port_label()` and `is_doorbell_camera_model()`.
4. **`data_transforms.py` may import Home Assistant's pure utility modules (`homeassistant.util.*`)** but nothing else from Home Assistant and no integration module other than `const.py` and `innerspace_transforms.py`. A test enforces this allow-list.

## Alternatives considered

### Option A: Split `sensor.py` along a recorded seam

- Pros: every module stays under 2,000 lines.
- Cons: adds a 35th root module that is a satellite in all but name, which is what ADR-007 set out to remove. Contributors would again have two places to look for a sensor.

### Option B: A capped size exception for `sensor.py`, like ADR-009 point 4

- Pros: keeps the budget wording intact.
- Cons: a cap needs an end condition, and there is no planned change that would bring `sensor.py` under 2,000 lines without splitting it. The exception would be permanent in practice.

### Option C: Keep the sensor satellites

- Pros: no large module.
- Cons: ADR-007's target layout is never reached, and seven satellites keep their own base classes and description tables.

### Option D: Exempt platform modules and bound them by role (chosen)

- Pros: matches Home Assistant's file structure and Core practice. Every sensor is in `sensor.py`. The role bound (descriptions, classes and setup only, one section per source) keeps logic out of platform files.
- Cons: `sensor.py` is about 3,700 lines.

## Consequences

### Positive

- ADR-007's target layout can be reached in full, with 34 root modules.
- No import cycle for the device registry lookup, and one home for every registry identity helper.
- `data_transforms.py` keeps its purpose (no entity, coordinator or platform imports), and a test now checks it.

### Negative

- `sensor.py` is the largest module after `coordinators/protect.py`. Reviews of sensor changes rely on its per-source sections.

### Neutral

- The `AGENTS.md` guidance of 200 to 400 lines per module is replaced by "one section per data source; non-platform modules stay under about 2,000 lines" when #341 lands.
- #341 is delivered in three pull requests: shared modules, platform merges, then the test layout. ADR-007 becomes Accepted with the last one.
