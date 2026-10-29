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

- Use shared fixtures in `tests/conftest.py`; put test data shared between test files in `tests/fixtures/`, never import from another test module
- Name test files after the module they cover: `tests/<pkg>/test_<module>.py` (for example `tests/coordinators/test_protect.py`). Add a feature suffix (`test_sensor_ports.py`) only when the module's tests exceed about 2,000 lines (ADR-007)
- Mock at the API client boundary (`network_client`, `protect_client`, `access_client`, `innerspace_client`, `site_manager_client`, `mobility_client`, `carrier_fabric_client`, or `probe.py`), not internal coordinator methods
- Access runtime state via `entry.runtime_data`, never `hass.data[DOMAIN]`
- Build facade snapshots with the builders in `tests/fixtures/facade_data.py` (`facade_data`, `with_network_device`, `with_protect_device`, `with_sections`, `set_availability`, ...). Snapshots are read-only (ADR-013), and `mock_coordinator.data` is frozen, so never write into `coordinator.data`: assign a new snapshot (`mock_coordinator.data = with_camera(mock_coordinator.data, "cam1", {...})`) before the entity reads it. An owner coordinator's mocked `.data` is not a facade snapshot

## HA 2026.8–2026.11 Test Assertions

- When asserting device registry entries:
  - Check `device_entry.config_entry_id == entry.entry_id` (single config entry per device; never check `config_entries` or `primary_config_entry`).
  - Check `device_entry.via_device_id` (not `via_device`).
  - Look up devices via `dev_reg.async_get_device_by_identifier((DOMAIN, device_id), entry.entry_id)`.
  - Check `device_entry.area_id` (never `device_entry.suggested_area`, which was removed in HA 2026.9).
- When asserting service action errors:
  - Verify `pytest.raises(ServiceValidationError)` or `pytest.raises(HomeAssistantError)` and assert `err.value.translation_domain == DOMAIN` and `err.value.translation_key == "..."`.

## Deprecations and Upcoming Home Assistant Releases

- **Current release (blocking):** the normal test run fails on deprecated use by the integration.
  - `pyproject.toml` `filterwarnings` turns `DeprecationWarning` and `PendingDeprecationWarning` attributed to `custom_components.unifi_insights` modules into errors. Third-party deprecations keep the default action.
  - Home Assistant logs, rather than warns, when an integration uses a deprecated API (`homeassistant.helpers.frame.report_usage`, `DeprecatedConstant`, `deprecated_class`, `deprecated_function`) or when an entity class still defines a deprecated attribute (`custom_components.unifi_insights.<module>::<Class> provides the deprecated <name>`). `tests/fixtures/deprecations.py` collects those log lines when they name `unifi_insights`, and the autouse `fail_on_ha_deprecation_reports` fixture fails the test that logged one. `tests/__init__.py` installs the handler early, so reports logged during import surface on the first test. The `pytest-homeassistant-custom-component` harness has no built-in check for this.
  - Fix the reported call in production code; do not silence the check. A test that triggers a report on purpose must drain it with `DEPRECATION_REPORTS.pop_reports()`.
- **Next release (early warning, non-blocking):** `.github/workflows/ha-next.yml` runs weekly, on demand, and on PRs that touch `pyproject.toml`, `requirements*.txt` or `manifest.json`, on two channels:
  - `release`: the newest Home Assistant on PyPI, beta included. It runs `python script/import_smoke.py`, the test suite (only when a matching `pytest-homeassistant-custom-component` release exists) and `HA_VERSION=<version> ./script/hassfest --skip-plugins ""`.
  - `dev`: the head of Core's `dev` branch, pinned to one commit, weeks before the next beta. `pytest-homeassistant-custom-component` never matches dev, so it runs the import smoke test and Hassfest at that commit.
  - Scheduled and manual runs on `main` keep one issue per channel, labelled `ha-next` and `bug` so CodeRabbit plans the fix.
- **Developer blog (early warning, non-blocking):** `.github/workflows/ha-dev-blog.yml` runs weekly and on demand (input `days`, default 22, so a run catches up after two missed weeks). `script/ha_dev_blog.py` reads the developer blog feed, keeps posts from the last `days` that announce a deprecation, removal or breaking change, and searches the package for the code identifiers those sentences name. It opens one issue per such post that names something the integration uses, labelled `ha-dev-blog` and `enhancement`, and never opens a second issue for the same post. A post page that cannot be read is listed in the job summary and fails the run, after issues open for the posts that were read. Run it locally with `python script/ha_dev_blog.py --days 30`.
- **When ha-next fails:** open the run linked from the `ha-next` issue and read the job summary.
  - Import smoke or test failure: the release removed or deprecated an API the integration uses. Reproduce it with `uv pip install "homeassistant==<version>" "unifi-official-api[talkback]==<pin>" segno numpy PyTurboJPEG` in a scratch venv (for the dev channel, `"homeassistant @ https://github.com/home-assistant/core/archive/<commit>.tar.gz"`) and `python script/import_smoke.py`, then fix it with an import or API that also works on the minimum supported Home Assistant.
  - Install failure: the release needs a newer Python or conflicts with a pinned dependency; check `requires-python` and the pins in `pyproject.toml`.
  - Hassfest failure: run the same command locally; a new validator rule or a changed Core layout may need a `script/hassfest` change.
  - The next passing scheduled or manual run on `main` closes the issue.
