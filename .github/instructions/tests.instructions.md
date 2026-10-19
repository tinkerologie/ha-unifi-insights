---
applyTo: "tests/**/*.py"
---

# Testing Instructions

**Applies to:** All Python test files under `tests/`

## Framework & Coverage Requirements

- **Framework:** `pytest` with `pytest-homeassistant-custom-component` and `pytest-asyncio`
- **Per-file coverage (≥95%):** Every module in `custom_components/unifi_insights/` must maintain **≥95% branch coverage**, enforced in CI via:
  ```bash
  pytest --cov-report=xml
  python script/check_coverage_per_file.py coverage.xml --fail-under 95
  ```
- **Quality Scale coverage rules:**
  - `test-before-configure` & `config-flow-test-coverage` (Bronze): 100% coverage of `config_flow.py` (user, local, remote, carrier_fabric, SSDP discovery, reauth, reconfigure, options flow).
  - `test-before-setup` (Silver): Test `ConfigEntryNotReady` and `ConfigEntryAuthFailed` during `async_setup_entry`.
  - `Integration Quality Scale` Gold: Full test coverage of coordinator refreshes, dynamic/stale device handling, entity states/actions, diagnostics redaction, repairs, and service actions.

## Running Tests

```bash
pytest                                                          # All tests with total coverage check
pytest tests/test_sensor.py                                     # Single test file
pytest -vvs                                                     # Verbose output
pytest --no-cov                                                 # Fast run without coverage
pytest --cov-report=xml && python script/check_coverage_per_file.py coverage.xml --fail-under 95
```

## Fixtures & Setup Patterns

- Use shared fixtures in `tests/conftest.py`
- Mock at the API client boundary (`network_client`, `protect_client`, `access_client`, `innerspace_client`, `site_manager_client`, `mobility_client`, `carrier_fabric_client`, or `probe.py`), not internal coordinator methods
- Access runtime state via `entry.runtime_data`, never `hass.data[DOMAIN]`

## HA 2026.8–2026.11 Test Assertions

- When asserting device registry entries:
  - Check `device_entry.config_entry_id == entry.entry_id` (single config entry per device; never check `config_entries` or `primary_config_entry`).
  - Check `device_entry.via_device_id` (not `via_device`).
  - Look up devices via `dev_reg.async_get_device_by_identifier(entry.entry_id, (DOMAIN, device_id))`.
  - Check `device_entry.area_id` (never `device_entry.suggested_area`, which was removed in HA 2026.9).
- When asserting service action errors:
  - Verify `pytest.raises(ServiceValidationError)` or `pytest.raises(HomeAssistantError)` and assert `err.value.translation_domain == DOMAIN` and `err.value.translation_key == "..."`.
