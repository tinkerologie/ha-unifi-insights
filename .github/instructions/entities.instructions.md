---
applyTo: "custom_components/unifi_insights/alarm_control_panel.py, custom_components/unifi_insights/binary_sensor.py, custom_components/unifi_insights/button.py, custom_components/unifi_insights/camera.py, custom_components/unifi_insights/cover.py, custom_components/unifi_insights/device_tracker.py, custom_components/unifi_insights/event.py, custom_components/unifi_insights/image.py, custom_components/unifi_insights/light.py, custom_components/unifi_insights/lock.py, custom_components/unifi_insights/media_player.py, custom_components/unifi_insights/number.py, custom_components/unifi_insights/select.py, custom_components/unifi_insights/sensor.py, custom_components/unifi_insights/siren.py, custom_components/unifi_insights/switch.py, custom_components/unifi_insights/update.py, custom_components/unifi_insights/entity.py"
---

# Entity Instructions

**Applies to:** Entity platform files and base entity classes

## Base Entity Classes

- **`UnifiInsightsEntity`** (`entity.py`) — Network devices; availability via the snapshot's captured device availability (`owner_available(coordinator.data, "device")`) and `serviceable` / `is_device_serviceable`. `is_device_online` remains the strict connectivity check.
- **`UnifiProtectEntity`** (`entity.py`) — Protect devices; availability via the snapshot's captured Protect availability (`owner_available(coordinator.data, "protect")`) and `state == CONNECTED`.
- **`UnifiAccessEntity` / `UnifiAccessDoorEntity`** (`entity.py`) — UniFi Access doors, readers, and hubs.
- **`UnifiInnerSpaceEntity`** (`entity.py`) — UniFi InnerSpace floor plans and spatial sensors.
- **`UnifiCarrierFabricEntity` / `UnifiCarrierFabricSubscriberEntity`** (`entity.py`) — UniFi Carrier Fabric OLTs, ONUs, and subscribers.
- **`UnifiMobilityRouterEntity`** (`entity.py`) — UniFi Mobility routers, grouped under their workspace device.
- **`UnifiSiteEntity`** (`entity.py`) — Site-level entities (vouchers, client counts, internet activity, network rules, rule change events), grouped under the site's gateway or site device. Each subclass decides availability.

All base classes handle camelCase/snake_case field mapping via `get_field()` and `first_not_none()` (`data_transforms.py`).

## MRO (Method Resolution Order)

**CRITICAL:** Custom integration base class (`CoordinatorEntity` subclass) MUST come before the Home Assistant platform entity class:

```python
# Correct
class MySwitch(UnifiInsightsEntity, SwitchEntity): ...


# Wrong - will cause MRO / CoordinatorEntity initialization errors
class MySwitch(SwitchEntity, UnifiInsightsEntity): ...
```

## EntityDescription Pattern

Use frozen, keyword-only dataclasses for static entity metadata:

```python
@dataclass(frozen=True, kw_only=True)
class UnifiInsightsSensorEntityDescription(SensorEntityDescription):
    """Describe a UniFi Insights sensor."""

    value_fn: Callable[[dict[str, Any]], StateType] | None = None
```

- Assign `EntityCategory.DIAGNOSTIC` or `EntityCategory.CONFIG` appropriately (Gold `entity-category`).
- Set `entity_registry_enabled_default = False` on noisy, advanced, or high-frequency diagnostic entities (Gold `entity-disabled-by-default`).
- Use `suggested_unit_of_measurement` and `suggested_display_precision` where appropriate.

## Platform Setup

Every platform file must implement:

```python
async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiInsightsConfigEntry,  # or UnifiInsightsConfigEntry | CarrierFabricConfigEntry
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up UniFi Insights entities from a config entry."""
```

- Use `UnifiInsightsConfigEntry` for standard console platforms and `CarrierFabricConfigEntry` (or `UnifiInsightsConfigEntry | CarrierFabricConfigEntry` on shared platforms such as `sensor.py`) when the platform supports Carrier Fabric entries.
- Declare `PARALLEL_UPDATES = 0` for read-only coordinator-driven platforms and `PARALLEL_UPDATES = 1` for action-based platforms (Silver `parallel-updates`).

## Data Access & Availability

- ALWAYS read from `coordinator.data` — never call API clients from an entity, in state properties or in actions.
- Read a facade record through its lookup in `data_transforms.py` (`network_device`, `network_device_stats`, `network_client`, `site_record`, `rule_record`, `protect_device`, `latest_voucher`) instead of indexing `coordinator.data`; read a section that has no lookup (`innerspace`, `internet_activity`, `vpn_connections`, ...) with `.get()`. The snapshot is read-only (ADR-013): never write into it. `protect_collection`, `site_section` and `section_by_site` copy, so don't call them per entity or per record on every update (state properties and `available` of many entities); a per-update scan that needs a whole section walks it in place.
- Read coordinator availability from the snapshot with `owner_available` / `site_section_available`, not from the facade's live `*_available` properties.
- **Write commands (ADR-010):** call the owning coordinator directly with a typed call — `self.coordinator.config_coordinator`, `self.coordinator.device_coordinator`, `self.coordinator.require_protect_coordinator()`, or the Access coordinator for Access entities. Never look a command up by name, never fall back to an API client, and never refresh or patch cached data from the entity. The owner publishes the written fields, so derive state from `coordinator.data` instead of setting `_attr_*` after the call.
- Translate a display name or an observed option into the native ID before calling the owner, and raise a translated `ServiceValidationError` when it is unknown.
- Use `get_field(data, "camelCase", "snake_case")` and `first_not_none(...)` so falsy values (`0`, `False`, `0.0`) are preserved instead of falling back via `or`.
- Never raise exceptions inside `@property` methods; return `None` when a value is unknown/unavailable.

## Entity Naming & Translations

- Set `_attr_has_entity_name = True` on all entities (Bronze `has-entity-name`).
- Set `_attr_translation_key` (or `EntityDescription.translation_key`) and define matching entries in `strings.json` and `translations/en.json` (Gold `entity-translations`).
- Define icon translations in `icons.json` (including `range` state icons where applicable; Gold `icon-translations`).

## Entity Lifecycle Hooks (HA 2026.10–2026.11+)

- **`async_prepare_to_add_to_hass(self) -> None` (HA 2026.10+):** Runs before entity registry entry processing; use it if initialization affects how the entity is registered. Never override `add_to_platform_start`.
- **`async_added_to_hass(self) -> None`:** Register event/dispatcher/coordinator listeners via `self.async_on_remove(...)` rather than manually cleaning up in `async_will_remove_from_hass`.
- **`async_entity_id_changed(self, old_entity_id: str) -> None` (`@callback`, HA 2026.10/2026.11+):** From HA 2026.11, renaming an `entity_id` updates the entity in place instead of removing and re-adding it. Any custom entity class that overrides `async_prepare_to_add_to_hass`, `async_added_to_hass`, or `async_will_remove_from_hass` MUST also implement `async_entity_id_changed` (calling `super().async_entity_id_changed(old_entity_id)`) to opt in to in-place `entity_id` updates.
- **State restoration (HA 2026.11+):** Never access `RestoreStateData.last_states` (removed in 2026.11). Use `RestoreEntity.async_get_last_state()` or `RestoreStateData.async_get_stored_state(entity_id)`.

## Device Registry Rules (HA 2026.8–2026.10+)

- **Single config entry per device:** Every device belongs to a single config entry (`DeviceEntry.config_entry_id`) and at most one subentry (`DeviceEntry.config_subentry_id`). Never access deprecated `DeviceEntry.config_entries`, `DeviceEntry.config_entries_subentries`, or `DeviceEntry.primary_config_entry`.
- **`via_device_id` replaces `via_device`:** `DeviceInfo["via_device"]` and `ATTR_VIA_DEVICE` are deprecated in HA 2026.8+. Link child devices to parent hubs/switches/OLTs using `via_device_id` resolved via `dr.async_get_device_id_by_identifier(hass, (DOMAIN, parent_id), config_entry_id=entry.entry_id)` (or `parent_device.id`).
- **Config-entry-scoped device lookups:** `dev_reg.async_get_device()` is deprecated in HA 2026.8+. Use `dev_reg.async_get_device_by_identifier((DOMAIN, identifier), entry.entry_id)`, `dev_reg.async_get_device_by_connection(...)`, or `dr.async_get_device_and_config_entry_for_domain(...)`.
- **`DeviceRegistry` collections:** `dev_reg.devices` and `dev_reg.child_devices` are read-only collections, not raw dicts. Do not call `.get()`, `.values()`, `.keys()`, or `id in dev_reg.devices`, and do not access `dev_reg.deleted_devices`.
- **`DeviceInfo` is a dataclass (HA 2026.11+):** Always build device info with `DeviceInfo(...)` keyword arguments, never a dict literal. Never index, `.get()`, `.update()`, `dict()` or `**`-unpack a `DeviceInfo`: that mapping layer still works in 2026.11 but is being deprecated. Build optional fields in a plain `dict[str, Any]` first, then construct `DeviceInfo(**fields)  # type: ignore[typeddict-item, unused-ignore]` (the TypedDict before 2026.11 needs the ignore, the dataclass doesn't). Pass registry fields to `async_get_or_create` from that plain dict, and only the fields that are set: an explicit `None` clears the field on an existing device. Adopt `async_get_or_create_from_info` and attribute access once the minimum supported Home Assistant is 2026.11.
- **`DeviceInfo` fields:** Never use deprecated `default_manufacturer`, `default_model`, or `default_name`. Never read `DeviceEntry.suggested_area` (removed in HA 2026.9; read `DeviceEntry.area_id`).
- **Requirement for `device_info`:** Attaching a device to an entity requires the entity to have both a `config_entry` and a `unique_id` (HA 2026.8+).

## Existing Platforms (17 Total)

| Platform                 | Purpose                                                                                              | Key patterns                                |
| ------------------------ | ---------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| `alarm_control_panel.py` | Protect security / alarm arming                                                                      | Action-based Protect security control       |
| `binary_sensor.py`       | Online/offline, motion, smart detection, door/lock state                                             | Network, Protect, Access, InnerSpace        |
| `button.py`              | Device restart, port power cycle, chime ring, wake-on-LAN                                            | Action-based (`PARALLEL_UPDATES = 1`)       |
| `camera.py`              | Live view, snapshots, RTSPS streams                                                                  | Protect camera streams                      |
| `cover.py`               | Garage/gate door control                                                                             | Access door/gate cover control              |
| `device_tracker.py`      | Wired/wireless client & Mobility device tracking                                                     | Network clients and Mobility devices        |
| `event.py`               | Motion, doorbell ring, smart detect, Access & rule audit events                                      | Event-driven from coordinator / WS          |
| `image.py`               | Guest voucher QR codes, InnerSpace floor plan images                                                 | Dynamic image generation / caching          |
| `light.py`               | Protect floodlight brightness/mode                                                                   | Protect light control                       |
| `lock.py`                | UniFi Access door lock/unlock                                                                        | Access door lock control                    |
| `media_player.py`        | Camera speaker / Talkback audio playback                                                             | Protect camera media player                 |
| `number.py`              | Mic volume, light level, chime volume/repeat, PoE/voucher settings                                   | Numeric configuration controls              |
| `select.py`              | Recording mode, HDR, video mode, light mode, viewer liveview                                         | Enumerated configuration controls           |
| `sensor.py`              | CPU, memory, uptime, TX/RX, PoE, SFP, WAN/ISP, Protect, Access, InnerSpace, Mobility, Carrier Fabric | Coordinator-driven (`PARALLEL_UPDATES = 0`) |
| `siren.py`               | Protect camera alarm siren                                                                           | Protect siren control                       |
| `switch.py`              | Port/PoE enable, WiFi, firewall/traffic/ACL/NAT/QoS rules, Protect toggles                           | Action-based switches                       |
| `update.py`              | Network & Protect device firmware updates                                                            | Firmware version & update status            |
