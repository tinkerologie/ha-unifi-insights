---
agent: "agent"
tools: ["search/codebase", "edit", "search"]
description: "Debug a data coordinator issue in UniFi Insights"
---

# Debug Coordinator Issue

Your goal is to diagnose and fix an issue in the UniFi Insights multi-coordinator system (`custom_components/unifi_insights/coordinators/`).

If not provided, ask for:

- Observed symptoms (entities unavailable, stale values, missing devices, errors in logs)
- Affected coordinator (`config`, `device`, `protect`, `access`, `innerspace`, `site_manager`, `mobility`, `carrier_fabric`, `rule_audit`, `facade`)
- Relevant log traceback or error message

## Diagnostic Checklist

### 1. Check Coordinator Data Flow & `entry.runtime_data`

- Verify `entry.runtime_data` (`UnifiInsightsData` or `CarrierFabricData`) holds initialized clients and coordinators.
- Check whether capability probing in `probe.py` enabled or skipped the optional coordinator.

### 2. Check Error Handling & Availability Logging

- Is `ConfigEntryAuthFailed` (with `translation_domain=DOMAIN`, `translation_key=...`) raised on `UniFiAuthenticationError`?
- Is `UpdateFailed` raised on transient `UniFiConnectionError` / `UniFiTimeoutError` / `UniFiError`?
- Does the coordinator log only once on transition to unavailable and once on recovery (Silver `log-when-unavailable`)?

### 3. Check Data Normalization (`data_transforms.py`)

- Are fields accessed via `get_field()` and `first_not_none()`? (Watch out for `or` expressions that turn `0` or `False` into `None`.)
- Does the API response match the schema in `https://developer.ui.com/{service}/{version}/openapi.json`?

### 4. Check Device Registry & Stale Device Cleanup (HA 2026.8–2026.10+)

- Ensure device lookups use `dev_reg.async_get_device_by_identifier((DOMAIN, device_id), entry.entry_id)` or `dr.async_entries_for_config_entry(dev_reg, entry.entry_id)`.
- Verify code checks `device.config_entry_id` (not deprecated `device.config_entries` or `device.primary_config_entry`) and does not call dict methods on `dev_reg.devices`.
- Verify child devices use `via_device_id` rather than deprecated `via_device`.

### 5. Common Issues

| Symptom                                  | Likely Cause                                                       | Fix                                                                            |
| ---------------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------ |
| Zero values show as `Unknown`            | `val1 or val2` used instead of `first_not_none()`                  | Use `first_not_none()` / `get_field()`                                         |
| All entities unavailable                 | `_async_update_data` raising unhandled exception                   | Map API error to `UpdateFailed`                                                |
| Missing optional domain entities         | Probe skipped or failed silently                                   | Check `probe.py` capability flags in `entry.data`                              |
| Deprecation warnings in HA 2026.10 logs  | Legacy `via_device`, `config_entries`, or `async_get_device` calls | Update to `via_device_id`, `config_entry_id`, `async_get_device_by_identifier` |
| Auth failures loop without reauth prompt | `UpdateFailed` raised instead of `ConfigEntryAuthFailed`           | Raise `ConfigEntryAuthFailed` on 401/403                                       |
