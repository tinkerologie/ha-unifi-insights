---
applyTo: "custom_components/unifi_insights/coordinators/**/*.py"
---

# Coordinator Instructions

**Applies to:** Data coordinator implementation files

## Multi-Coordinator Architecture

This integration uses a multi-coordinator architecture under `custom_components/unifi_insights/coordinators/`:

- **Base Coordinator** (`coordinators/base.py`) — Abstract base with shared error handling, backoff, and availability logging
- **Config Coordinator** (`coordinators/config.py` & `coordinators/config_sections.py`) — Site, WiFi, firewall, ACL, traffic, NAT, QoS, DNS, VPN, and network configuration data (slow polling)
- **Device Coordinator** (`coordinators/device.py`) — Network device metrics, ports, PoE, SFP, and connected clients (30s default polling)
- **Protect Coordinator** (`coordinators/protect.py`) — Cameras, lights, sensors, NVRs, chimes, viewers, liveviews, and real-time WebSocket event stream. Frames are parsed in `protect_ws.py`; every cache decision (frame merges, stale REST answers, latches, siren guards, eviction) is made by the pure reconciler in `protect_events.py` and applied through the coordinator's `_apply()` (ADR-012). Never change `self.data` in place or hold it across an `await` to write from it.
- **Access Coordinator** (`coordinators/access.py`) — UniFi Access doors, locks, readers, hubs, and access events
- **InnerSpace Coordinator** (`coordinators/innerspace.py`) — UniFi InnerSpace floor plans, zones, and spatial occupancy/environment data
- **Site Manager Coordinator** (`coordinators/site_manager.py`) — UniFi Site Manager cloud multi-site health and ISP metrics
- **Mobility Coordinator** (`coordinators/mobility.py`) — UniFi Mobility cellular/5G devices and SIM metrics
- **Carrier Fabric Coordinator** (`coordinators/carrier_fabric.py`) — UniFi Carrier Fabric OLTs, ONUs, subscribers, and alarms
- **Config sections** (`coordinators/config_sections.py`) — Config coordinator section fetchers, including per-site internet activity reports
- **Rule audit and export** (`coordinators/rule_audit.py`) — Network rule change auditing and snapshot export
- Voucher state parsing lives in `data_transforms.py` (pure, no coordinator); the voucher settings, the latest generated vouchers and the voucher commands belong to the config coordinator (ADR-013)
- **Facade Coordinator** (`coordinators/facade.py`) — Unified view combining sub-coordinators for entity platforms; it holds no domain state and keeps only aggregation, delegated availability, the owner accessors, `async_refresh_or_raise` and pure reads and exports (`async_get_historical_clients`, `async_get_wake_history`, `async_export_network_rules`, the floor-plan download). Its `data` is a read-only `FacadeData` snapshot (ADR-013) built by the pure `build_facade_data`: a `ReadOnlyDict` root that passes each owner's sections through by reference (never copy or write into them, because the WebSocket streams gate on section identity) and captures each owner's availability under `availability`. Readers use the lookups in `data_transforms.py`; the owner-level `*_available` properties stay only for the capture, the WebSocket API and diagnostics

## Write Commands (ADR-010)

- Put each write command on the coordinator that owns the data it changes (`config.py`, `device.py`, `protect.py`, `access.py`, `carrier_fabric.py`), never on the facade. Keep its parameter names and return type stable for entities and service actions.
- Run the API call through `async_execute_command(CommandFailure(...), action, *args)` from `coordinators/base.py` so every failure is a translated `HomeAssistantError`. Access (`_command_error`) and Carrier Fabric (conflict retry and typed errors) keep their own error handling, as ADR-010 records. Pass placeholders that are safe identifiers only.
- After success, publish the written fields as a copy-on-write overlay: replace the data root and every container on the changed path (lists included), then call `async_update_listeners()`. Never mutate a shared record and never call `async_set_updated_data()` for an overlay.
- Apply the reconciliation policy in ADR-010's ownership table. A failed refresh after a successful write is logged (check `last_update_success`) and never fails the write.
- Do not add generation tracking or pending-write guards. The one exception is the Device coordinator's outlet pending-write guard (`reconcile_pending_outlet_writes`, #376), which ADR-010 records: keep it limited to `relay_state` and `cycle_enabled`, released on confirmation, after `OUTLET_WRITE_GUARD_SECONDS` or when the device or site is gone.
- Resolve classic site names only through `UnifiInsightsConfigCoordinator.resolve_legacy_site_name` / `async_resolve_legacy_site_name`; never guess `"default"`.
- Test each command once in `tests/coordinators/test_<owner>.py` against `seeded_owners` (`tests/fixtures/command_owners.py`), which autospecs the real `unifi_official_api` client.

## Runtime Data (`entry.runtime_data`)

- Store coordinators and clients on `entry.runtime_data` (`UnifiInsightsData` or `CarrierFabricData` in `__init__.py`), never in `hass.data[DOMAIN]` (Bronze `runtime-data`).

## Error Handling & Availability Logging

- Raise `ConfigEntryAuthFailed` (with `translation_domain=DOMAIN`, `translation_key=...`) for authentication/authorization failures to trigger the reauth flow.
- Raise `UpdateFailed` for transient network/controller errors so `DataUpdateCoordinator` retries automatically.
- **Log once when unavailable (Silver `log-when-unavailable`):** Log a single `INFO`/`WARNING` message when the controller transitions to unreachable and once when it recovers; never log errors on every failed poll cycle.

```python
async def _async_update_data(self) -> dict[str, Any]:
    try:
        data = await self.api_client.get_data()
    except UniFiAuthenticationError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="auth_failed",
        ) from err
    except (UniFiConnectionError, UniFiTimeoutError, UniFiError) as err:
        raise UpdateFailed(f"Error fetching data: {err}") from err
    return data
```

## First Refresh

Always use `async_config_entry_first_refresh()` during `async_setup_entry`:

```python
await coordinator.async_config_entry_first_refresh()
```

## Dynamic & Stale Device Cleanup (HA 2026.8–2026.10+)

- **Dynamic devices (`dynamic-devices`):** Automatically register new devices discovered on subsequent coordinator refreshes.
- **Stale devices (`stale-devices`):** Remove stale devices from the Home Assistant device registry when they disappear from the controller. The Network device coordinator removes a device on the 3rd consecutive eligible poll in which it is absent (`MAX_CONSECUTIVE_MISSING_POLLS = 3`), requiring source-aware eligibility (the site must have the same polling source as its previous snapshot and not drop from non-empty to zero devices) and a hardware MAC presence guard to protect device identity across v1 and legacy endpoint fallbacks. On a v1 poll, a device v1 omits only counts toward removal when the legacy device list fetched in the same poll also lacks its normalized MAC; while legacy still has it, its counter is held (neither advanced nor reset), and when a counter would advance but that legacy list failed or was empty, the site is not eligible for that poll. The check reuses that list, so it adds no request and cleanup stays after the single publish (ADR-013). A site without a classic site name has no legacy list, and v1 alone decides. Protect keeps a missing device for 3 polls and removes on the 4th, while InnerSpace removes immediately.
- **HA 2026.8–2026.10 Device Registry rules:**
  - Use `dr.async_entries_for_config_entry(dev_reg, entry.entry_id)` or `dev_reg.async_get_device_by_identifier((DOMAIN, device_id), entry.entry_id)`.
  - Use `device.config_entry_id` (single config entry per device) — never access deprecated `device.config_entries`, `device.config_entries_subentries`, or `device.primary_config_entry`.
  - Never call dict methods (`.get()`, `.values()`, `.keys()`) on `dev_reg.devices` or `dev_reg.child_devices`.

## Best Practices

- Never expose API clients directly to entity `@property` getters — always read from `coordinator.data`.
- Normalize API responses via `data_transforms.py` (`get_field`, `first_not_none`) before storing in coordinator dictionaries.
- Clean up WebSocket listeners and background tasks in `async_unload_entry` (Bronze `config-entry-unloading`).
