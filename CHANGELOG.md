# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Device info no longer relies on `DeviceInfo` behaving as a dict, which Home Assistant 2026.11 turned into a dataclass and is deprecating as a mapping. The Protect event entities build a `DeviceInfo` instead of a dict, and the Carrier Fabric organisation, Mobility workspace and InnerSpace devices are registered from plain field dicts. Type checking passes on Home Assistant 2026.10 and on Core dev. [#444](https://github.com/tinkerologie/ha-unifi-insights/issues/444)
- Developer guidance no longer says `UnitOfMeasurementSelector` arrived in Home Assistant 2026.10. It arrived in 2026.11, which is newer than the minimum supported release, so the integration doesn't use it yet. [#446](https://github.com/tinkerologie/ha-unifi-insights/issues/446)
- Releases now ship the integration as `unifi_insights.zip`, with a `SHA256SUMS` file and a signed build provenance attestation. `hacs.json` sets `zip_release`, so from the next release the Marketplace and HACS install the zip and show how many times the latest release's zip was downloaded. After that release, choosing an older release (one without the zip) in the Marketplace or HACS can fail to download. `script/build_release_zip.py` builds the zip reproducibly from `SOURCE_DATE_EPOCH`, every pull request checks that two builds match byte for byte, and the release workflow uploads the assets to a draft before it publishes, so it works with immutable releases. The README describes manual installation from the zip and how to verify it. [#434](https://github.com/tinkerologie/ha-unifi-insights/issues/434)
- Home Assistant changes are caught earlier. The HA Next workflow also tests the head of Core's `dev` branch, weeks before the next beta, and keeps one issue per channel, now labelled `bug` as well as `ha-next`. A weekly developer blog watch opens an `enhancement` issue for each post that deprecates, removes or breaks code the integration uses. The deprecation checks also catch an entity class that still defines a deprecated attribute (`... provides the deprecated ...`). [#438](https://github.com/tinkerologie/ha-unifi-insights/issues/438)
- Tests now fail on deprecated use by the integration in the current Home Assistant: deprecation warnings raised from its modules are errors, and so are Home Assistant's own deprecation reports (`report_usage`, deprecated constants, classes and functions) that name `unifi_insights`. A new weekly HA Next workflow runs an import smoke test, the test suite and Hassfest against the newest Home Assistant, beta included. It never blocks merges and opens or closes one `ha-next` issue instead. `script/hassfest` supports Home Assistant 2026.11's packaged validator. [#436](https://github.com/tinkerologie/ha-unifi-insights/issues/436)
- The CI test job installs the TurboJPEG library on both Ubuntu 24.04 and 26.04 runners. Ubuntu 26.04 renamed the package from `libturbojpeg` to `libturbojpeg0`, so the job now picks whichever name the runner provides and logs it. [#431](https://github.com/tinkerologie/ha-unifi-insights/issues/431)
- Dependency auto-merge now runs only for Dependabot-initiated updates; maintainer repairs use manual review and merging without bypassing bot signature verification. [#427](https://github.com/tinkerologie/ha-unifi-insights/issues/427)
- Updated Vite to 8.3.5 and synchronized the committed dashboard bundles with its reproducible build output. [#427](https://github.com/tinkerologie/ha-unifi-insights/issues/427)
- Validation scripts now consistently select the configured Python environment, fail on Pyright warnings and validate custom requirements against Home Assistant Core's own dependency files. Local and CI Hassfest share a reviewed development commit and pinned Python in an isolated environment, and the integration declares networking and security categories for current catalog validation. Static entity descriptions are frozen and keyword-only, matching current Home Assistant metadata conventions. [#420](https://github.com/tinkerologie/ha-unifi-insights/issues/420)
- The Event Timeline now shows Person, Animal and Car for Protect smart detections, with distinct icons and readable fallback labels. Events use cached camera/device names and refresh when those names change. [#419](https://github.com/tinkerologie/ha-unifi-insights/issues/419)

### Fixed

- The release workflow no longer fails when it verifies the release before GitHub has created the release attestation. It retries for about a minute, and still fails when release immutability is off. [#449](https://github.com/tinkerologie/ha-unifi-insights/issues/449)
- An InnerSpace device that matches a Network or Protect device no longer wipes that device's name, manufacturer, model and serial number. Moving the InnerSpace entities to the matched device passed an empty value for each field that InnerSpace doesn't know, which Home Assistant applies to the device; it now passes only the fields that are set. [#444](https://github.com/tinkerologie/ha-unifi-insights/issues/444)
- Renaming an entity ID no longer removes and re-adds the Access doorbell and result events, the floor plan and voucher QR code images, the camera speaker, the voucher number and the siren on Home Assistant 2026.11 and later. Home Assistant now changes an entity ID in place, but keeps the old re-add for a custom integration's entities until they opt in, and drops it in 2027.11. These entities opt in, still work on 2026.9 and 2026.10, and a test checks that every entity class does. [#445](https://github.com/tinkerologie/ha-unifi-insights/issues/445)
- On Home Assistant 2026.11 and later, client trackers are created enabled again when you turn on **Track WiFi Clients** or **Track Wired Clients**. Home Assistant 2026.11 creates every network-scanner tracker disabled. Trackers you disabled stay disabled. A tracker created disabled by the integration on 2026.11 before this fix stays disabled until you enable it. Client renames on the controller now also reach Home Assistant without a reload: the client's own device is renamed, keeping any name you gave it, and a tracker under its access point or switch takes the new name. [#441](https://github.com/tinkerologie/ha-unifi-insights/issues/441)
- Removing a stale device no longer logs a Home Assistant deprecation warning. The integration removed it with `device_registry.async_update_device(remove_config_entry_id=...)`, which Home Assistant deprecates and will stop supporting in 2027.8; it now calls `async_remove_device`. A device belongs to a single config entry, so the result is the same. [#436](https://github.com/tinkerologie/ha-unifi-insights/issues/436)
- The integration loads on Home Assistant 2026.11. The sensor platform now imports `EntityCategory` from `homeassistant.const`. Home Assistant 2026.11 drops the unofficial copy in `homeassistant.components.sensor`, so the old import failed with an `ImportError`. [#429](https://github.com/tinkerologie/ha-unifi-insights/issues/429)

## [2026.10.4] - 2026-10-26

### Fixed

- Wired and wireless client trackers now report `home` when the client is present in the official connected-clients listing. The official UniFi Network API has no `connected` field on connected client records, so presence in the snapshot determines connection state. An explicit `connected=False` still reports `not_home`. Tracker entity IDs and unique IDs do not change. [#417](https://github.com/tinkerologie/ha-unifi-insights/issues/417)

## [2026.10.3] - 2026-10-25

### Added

- The topology card draws each gateway's internet connections (WANs) as nodes above the gateway, in the tree and tiered layouts and both orientations, so the map reads internet, gateway, infrastructure, clients. Each WAN shows its name and connected or disconnected state, from the same data as the gateway's "<WAN name> Connection" sensors, and updates live; a disconnected WAN is dimmed and labelled **Disconnected**. The list view lists WANs just before their gateway, and administrators can copy a WAN's IP address and open its Connection sensor. The new `show_wan` card option (default `true`, also in the card editor) hides them. The topology snapshot contract is now schema version 2 (new node kind `wan`; WAN IP addresses are never part of the snapshot), so the card and the integration must be updated together. [#347](https://github.com/tinkerologie/ha-unifi-insights/issues/347)
- Added developer documentation under `docs/development/`: `ARCHITECTURE.md` (coordinators, runtime data, entity hierarchy, config flow modes, dashboard cards, diagnostics, data flow), `DECISIONS.md` (decision index and future considerations), and Architecture Decision Records `adr/001` to `adr/007` covering the ADR process, the multi-coordinator architecture behind a facade, the vendored API client, console identity for config entry unique IDs, bundled dashboard cards with versioned WebSocket contracts, three-layer diagnostics redaction, and the proposed flat Home Assistant module layout (ADR-007, 57 to 33 root modules with `coordinators/` and `api/` as the only subpackages). The planned move of the vendored client back to `unifi-official-api` 2.0.0 is recorded as a future consideration ([tinkerologie/unifi-official-api#41](https://github.com/tinkerologie/unifi-official-api/issues/41)). [#330](https://github.com/tinkerologie/ha-unifi-insights/issues/330)

### Changed

- Dashboard cards now use Home Assistant typography, spacing and theme tokens, larger touch controls, visible keyboard focus, reduced-motion support, labelled editors and scrollable content when resized in Sections. Loading, delayed updates and unavailable data are announced accessibly; missing traffic and performance readings stay unknown instead of appearing as zero, and Protect and timeline cards distinguish missing data from healthy empty results. [#413](https://github.com/tinkerologie/ha-unifi-insights/issues/413)
- Frontend typechecking now uses TypeScript 7.0.2, with the TypeScript 6 compatibility API retained for ESLint. No change for users. [#413](https://github.com/tinkerologie/ha-unifi-insights/issues/413)

- UniFi Insights now installs `unifi-official-api[talkback]==3.0.0` (was 2.0.0), pinned in `manifest.json`, `pyproject.toml` and `requirements.txt`. The integration used none of the Protect methods that 3.0.0 removes. 3.0.0 reports a successful answer with no record or with a record that fails to parse as `UniFiNotFoundError` or `UniFiResponseError` with status 200, where 2.0.0 raised `ValueError` or pydantic's `ValidationError`. The setup probe, the config flow and the coordinators now read these types, so a Protect console without an NVR record, an NVR or InnerSpace project that fails to parse, and a polled section that returns an unreadable record are reported as before: setup and the config flow show the same errors, and a polled section keeps its last known data. Two messages change. A change whose answer the console returns unreadable or empty where 2.0.0 raised `ValueError` (for example a generated voucher, a chime's ring settings or a new live view) now reports that the console returned data UniFi Insights could not read, instead of the general command error. During reauthentication and reconfiguration, a console list that the UniFi cloud returns unreadable now reports the site parse error instead of an unknown error.
- The developer documentation now describes the finished read-only facade snapshot: ADR-013 records what landed in which pull request and the deviations from the plan, `ARCHITECTURE.md`, `DECISIONS.md`, `AGENTS.md` and the agent instructions say that readers use the snapshot lookups in `data_transforms.py` and the captured availability (and never call the copying lookups per entity or per record on every update), and ADR-001 and ADR-007 cite the sub-issues of #343 instead of the closed #330. Developer documentation only; no change for users. [#342](https://github.com/tinkerologie/ha-unifi-insights/issues/342)
- The remaining tests that wrote into facade snapshots (among them `tests/test_entity.py`, `tests/test_entity_dynamic_discovery.py`, `tests/test_camera.py`, `tests/test_light.py`, `tests/test_media_player.py`, `tests/test_select.py`, `tests/test_update.py`, `tests/test_data_transforms_consumers.py`, `tests/test_data_transforms_storage.py`, `tests/test_console_identity_site_entities.py`, `tests/test_websocket_api.py`, `tests/test_diagnostics.py`, `tests/test_alarm_control_panel.py` and `tests/test_services_chime_pairing.py`) now build each snapshot with the builders in `tests/fixtures/facade_data.py` and assign it. The shared `mock_coordinator` fixture now holds a frozen snapshot built with `facade_data`, and `with_sections` freezes the sections it replaces, so a test that writes into a facade snapshot fails. Tests that change an owner coordinator's own data are unchanged. No change for users. [#342](https://github.com/tinkerologie/ha-unifi-insights/issues/342)
- The event, device tracker, binary sensor, number and image tests (`tests/test_event.py`, `tests/test_device_tracker.py`, `tests/test_binary_sensor.py`, `tests/test_binary_sensor_water_leak.py`, `tests/test_number.py` and `tests/test_image.py`) now build each facade snapshot they need with the builders in `tests/fixtures/facade_data.py` and assign it, as an owner update would, instead of writing into the mocked facade data in place; a test that simulates an owner update publishes the next snapshot before the entity reads it. The new `with_protect_fields` and `with_protect_collection` builders replace some fields of a Protect device's record, or a whole Protect collection. No change for users. [#342](https://github.com/tinkerologie/ha-unifi-insights/issues/342)
- The switch and button tests (`tests/test_switch.py`, `tests/test_switch_network_rules.py` and `tests/test_button.py`) now build each facade snapshot they need with the builders in `tests/fixtures/facade_data.py` and assign it, as an owner update would, instead of writing into the mocked facade data in place; a test that simulates an owner update publishes the next snapshot before the entity reads it. `tests/test_client_wake.py` already only assigned whole snapshots and is unchanged. No change for users. [#342](https://github.com/tinkerologie/ha-unifi-insights/issues/342)
- The sensor tests (`tests/test_sensor.py` and the `tests/test_sensor_*.py` modules) now build each facade snapshot they need with the builders in `tests/fixtures/facade_data.py` and assign it, as an owner update would, instead of writing into the mocked facade data in place. The new `with_device_stats`, `with_device_fields` and `with_device_stats_fields` builders replace a Network device's statistics, or some fields of its record or statistics. The Carrier Fabric and Mobility tests still change their own coordinators' data. No change for users. [#342](https://github.com/tinkerologie/ha-unifi-insights/issues/342)
- The entities now read the coordinator availability the facade snapshot captured, through the new `owner_available` and `site_section_available` lookups in `data_transforms.py`, instead of asking the facade and the configuration coordinator live, so an entity's state and its availability always come from the same snapshot. A site or section the snapshot does not list has the configuration coordinator's availability, and a missing or malformed availability section reads as available. The facade's per-site `wifi_available`, `vouchers_available`, `firewall_available`, `port_forwards_available`, `traffic_rules_available`, `nat_rules_available`, `static_routes_available` and `internet_activity_available`, which nothing calls any more, are removed. Tests now set availability through the new `with_availability` and `set_availability` builders in `tests/fixtures/facade_data.py`. No change for users: entities, unique IDs, states, attributes and availability are unchanged. [#342](https://github.com/tinkerologie/ha-unifi-insights/issues/342)
- The WebSocket API and the dashboard card builders now read the site, gateway and per-site device and client records they look up through the facade snapshot lookups (`site_record`, `network_device` and `site_section`) instead of indexing the facade data themselves, so a missing or malformed section, site or record reads as a missing record. The change-detection inputs of the dashboard subscriptions and the diagnostics export still read the facade data as published. No change for users: WebSocket payloads, dashboard snapshots and diagnostics are unchanged. [#342](https://github.com/tinkerologie/ha-unifi-insights/issues/342)
- The Network platforms, the Network service targets, the `reconnect_client` action and the topology builder now read Network devices, statistics, clients, sites, WiFi networks, network rules, site-to-site VPNs and the latest vouchers through the facade snapshot lookups, and the new `site_section` and `section_by_site` for one site's or every site's records, instead of indexing the facade data themselves, so a missing or malformed section, site or record reads as a missing record. The facade's `get_site`, `get_device` and `get_device_stats`, which nothing calls any more, are removed. No change for users: entities, unique IDs, states, attributes and availability are unchanged. [#342](https://github.com/tinkerologie/ha-unifi-insights/issues/342)
- The Protect platforms and the Protect service targets now read Protect devices through the facade snapshot lookups, `protect_device` and the new `protect_collection`, instead of indexing the facade's Protect section themselves, so a missing or malformed section, collection or record reads as a missing record. No change for users: entities, unique IDs, states, attributes and availability are unchanged. [#342](https://github.com/tinkerologie/ha-unifi-insights/issues/342)
- The base entities in `entity.py` now read Network devices, device statistics, sites and Protect devices through the facade snapshot lookups in `data_transforms.py` instead of indexing the facade data themselves, so a missing or malformed section reads as a missing record. No change for users: entities, unique IDs, states, attributes and availability are unchanged. [#342](https://github.com/tinkerologie/ha-unifi-insights/issues/342)
- The facade coordinator now publishes a typed, read-only snapshot, `FacadeData` (ADR-013): a pure `build_facade_data` builds it from the other coordinators' data without copying it, and it also records each coordinator's availability. The InnerSpace device matching is redone only when the data it reads changes, the Protect dashboard streams compare Protect collections by identity instead of hashing them on every update, and new lookups in `data_transforms.py` and snapshot builders in `tests/fixtures/facade_data.py` prepare the move of entities and tests to them. No change for users: entities, unique IDs, actions and dashboard payloads are unchanged. [#342](https://github.com/tinkerologie/ha-unifi-insights/issues/342)
- The hotspot voucher settings, the latest generated voucher of each site and the generate and delete voucher commands moved from the facade coordinator to the configuration coordinator, which already owns the voucher inventory. The settings are now replaced instead of changed in place, and the latest vouchers are published copy-on-write. Added ADR-013, which records the plan for a typed, read-only facade snapshot that holds no state of its own. No change for users: the voucher numbers, button, QR code image, sensors and the `generate_voucher` and `delete_voucher` actions behave as before. [#342](https://github.com/tinkerologie/ha-unifi-insights/issues/342)
- Added characterization tests before the facade becomes a read-only snapshot: `tests/test_unique_ids.py` pins the 110 unique IDs, on 14 platforms, that a seeded Network and Protect entry registers, and `tests/coordinators/test_facade_identity.py` pins that each facade section is the object its owner published, that an owner update keeps the sections it does not touch, and what a facade snapshot taken during a device or config refresh sees once the refresh ends. No change for users. [#342](https://github.com/tinkerologie/ha-unifi-insights/issues/342)
- Added ADR-012, which records the pure Protect reconciler from [#340](https://github.com/tinkerologie/ha-unifi-insights/issues/340) and ends ADR-009's size exception for `coordinators/protect.py`. The behavior quirks it keeps are tracked in #379 and #381 to #384. Tests named after deleted Protect coordinator methods were renamed for the behavior they cover.
- How the Protect coordinator reconciles WebSocket frames with REST polls (merging frames, keeping a newer door state, the tamper and siren-stop guards, the motion and ring auto-off, the empty-answer and missing-poll windows) moved to the new `coordinators/protect_events.py` as one pure function, `reconcile(state, cache, input, now)`, which returns the new state, a copy-on-write cache and the effects to run (ADR-007's seam). Nothing changes for users: the same entities, states, notifications and timings, with today's quirks kept and pinned by tests. Its log lines still come from the `custom_components.unifi_insights.coordinators.protect` logger. `coordinators/protect.py` drops from 3,178 to 1,773 lines. [#340](https://github.com/tinkerologie/ha-unifi-insights/issues/340)
- The Protect WebSocket frame parsing, warn-once logging and per-stream health moved from `coordinators/protect.py` to the new `coordinators/protect_ws.py` (ADR-007's seam), with no change for users. Its log lines now come from the `custom_components.unifi_insights.coordinators.protect_ws` logger. `coordinators/protect.py` drops from 3,482 to 3,178 lines. [#340](https://github.com/tinkerologie/ha-unifi-insights/issues/340)
- Added characterization tests for how the Protect coordinator reconciles WebSocket frames with REST polls (a door frame during a sensor poll, a stale siren poll, a siren stopped during a poll, devices missing from polls, and missed motion and ring ends), driven through a fake Protect WebSocket in `tests/fixtures/protect_ws.py`. They pin today's behavior before #340 moves the reconciliation into its own modules. [#340](https://github.com/tinkerologie/ha-unifi-insights/issues/340)
- The test suite now mirrors the package (ADR-007, now Accepted): `tests/coordinators/test_<module>.py` for each coordinator and `test_<module>.py` for each root module, with a feature suffix (`test_sensor_carrier_fabric.py`) only where a module's tests exceed about 2,000 lines. `test_coordinators.py` is split by coordinator, small feature files are folded into their module's tests, and test data shared between files moved to `tests/fixtures/`. Every test kept its name in the move. [#341](https://github.com/tinkerologie/ha-unifi-insights/issues/341)
- Every entity now lives in its platform module and every base entity in `entity.py` (ADR-007, ADR-011): the eleven remaining entity satellite modules (`access_entity.py`, `carrier_fabric_entity.py`, `carrier_fabric_sensor.py`, `innerspace_entity.py`, `mobility_entity.py`, `network_rule_audit_event.py`, `network_rule_switch.py`, `protect_security_entity.py`, `site_internet_activity_sensor.py`, `storage_entity.py`, `voucher_sensor.py`) are merged into `sensor.py`, `binary_sensor.py`, `switch.py`, `event.py` and `device_tracker.py`, leaving 34 root modules. Same-shape entities now share one class: a new `UnifiSiteEntity` base for the eight site-level entities, one `UnifiInsightsNetworkRuleSwitch` for all seven network rule families (firewall policies, policy-based routes and VPN clients joined port forwards, traffic rules, NAT rules and static routes), one `UnifiOutletSwitch` for the relay and power-cycle switches, and one Protect sensor and one Protect binary sensor class. Unique IDs, entity names, devices, states and actions are unchanged. The firewall policy, VPN client and policy-based route switch icons now come from `icons.json` (icon translations), so their state no longer carries an `icon` attribute; the icons look the same, and a policy-based route over a VPN still shows the VPN icon. [#341](https://github.com/tinkerologie/ha-unifi-insights/issues/341)
- Moved shared code to its ADR-007 home, with no change for users: `led_level.py`, `storage_telemetry.py`, `coordinators/voucher_state.py` and the pure parts of `helpers.py` are now in `data_transforms.py`; the identity and device registry parts of `helpers.py` are in `console_identity.py`; `CarrierFabricData` is in `__init__.py`; the six dashboard snapshot builders are in `dashboard_snapshots.py`; `topology_contract.py` and `topology_keys.py` are in `topology.py`; `coordinators/internet_activity.py` is in `coordinators/config_sections.py` and `coordinators/rule_export.py` in `coordinators/rule_audit.py`. The device, Protect and InnerSpace coordinators share one stale-device removal on the base coordinator, and every coordinator class now uses the `UnifiInsights` prefix (for example `UnifiInsightsProtectCoordinator`). Log lines from moved code now come from the new module's logger, still under `custom_components.unifi_insights`. Added ADR-011: platform modules are exempt from the 2,000-line budget and bounded by role instead, and it records where the remaining helpers live. [#341](https://github.com/tinkerologie/ha-unifi-insights/issues/341)
- Removed code that only ran on Home Assistant 2026.6 to 2026.8, which are no longer supported: the `DeviceRegistry.async_get_device` fallback, the `via_device` fallback for Mobility routers, and the multi-owner device checks in Carrier Fabric and the dashboard WebSocket API. On Home Assistant 2026.9 and newer nothing changes. [#366](https://github.com/tinkerologie/ha-unifi-insights/issues/366)
- The README now explains how to install UniFi Insights from the Home Assistant Marketplace, which has HACS built in starting with Home Assistant 2026.11, with an **Open in Marketplace** button. HACS instructions remain for Home Assistant 2026.9 and 2026.10. HACS installs carry over to the Marketplace when you upgrade. [#367](https://github.com/tinkerologie/ha-unifi-insights/issues/367)
- **Breaking:** the minimum supported Home Assistant version is now 2026.9.0 (was 2026.6.0), in `hacs.json`, the README and the three blueprints. UniFi Insights now supports only recent Home Assistant releases, so 2026.6 to 2026.8 are no longer supported. Update Home Assistant before you update UniFi Insights. Action target routing reads each device's single owning config entry (`DeviceEntry.config_entry_id`, added in Home Assistant 2026.8), so on 2026.6 and 2026.7 Carrier subscriber actions would always fail. [#339](https://github.com/tinkerologie/ha-unifi-insights/issues/339)
- Error messages of the UniFi Insights actions are now translated. 48 errors, most of them about a missing, unknown or ambiguous target, were English only before; every action error now has a translation key. The messages shown in Home Assistant are slightly reworded in places (for example, IDs are no longer quoted and "Protect" reads "UniFi Protect"); the English text in the log is unchanged. [#339](https://github.com/tinkerologie/ha-unifi-insights/issues/339)
- The `refresh_data` and `remove_clients` actions no longer show the raw API error text. The error names only what failed, and the details are logged as a warning. `refresh_data` now reports, for example, "Unable to refresh UniFi Insights data from Office, Garage" instead of each console followed by its error text, and `remove_clients` reports, for example, "Failed to remove clients: Office (site default)", one console and site per failure, without the error text. [#339](https://github.com/tinkerologie/ha-unifi-insights/issues/339)
- A Carrier Fabric subscriber ID that is found in more than one config entry now stops the `carrier_suspend_subscriber` or `carrier_resume_subscriber` action with an error that asks you to target the subscriber's entity or device. Before, the action used the first matching entry. [#339](https://github.com/tinkerologie/ha-unifi-insights/issues/339)
- Action target routing moved from `services.py` to the new `service_targets.py` (the services/routing seam allowed by ADR-009). Each action now resolves one typed target and runs one owner command. Registry targets resolve through the device's owning config entry (`config_entry_id`), and identifier lookups are scoped to one config entry. Action schemas, `services.yaml`, routing results and unique IDs are unchanged. Two existing behaviors are kept on purpose and tracked as follow-ups: an explicit `console_id` skips the ownership check for Protect targets ([#355](https://github.com/tinkerologie/ha-unifi-insights/issues/355)), and `target.area_id` is accepted but ignored by single-target actions ([#356](https://github.com/tinkerologie/ha-unifi-insights/issues/356)). [#339](https://github.com/tinkerologie/ha-unifi-insights/issues/339)
- Every change you make through UniFi Insights (switches, buttons, selects, numbers, lights, sirens, the alarm panel, locks, gates and the matching actions) now goes through one command path, run by the part of the integration that owns that data. After a successful change only that data is refreshed, instead of every Network and Protect poll at once, and a change still counts as successful when the refresh after it fails. Recorded as ADR-010. [#338](https://github.com/tinkerologie/ha-unifi-insights/issues/338)
- Firewall rule, policy-based route and VPN client switches now try one more refresh 15 seconds after a change when their rules could not be read, like the port forward, traffic, NAT and static route switches, so they do not stay unavailable until the next poll. [#338](https://github.com/tinkerologie/ha-unifi-insights/issues/338)
- The Protect camera **Status Light** and **High FPS Mode** switches now take their state from the integration's Protect data, which is updated right after a successful change. Before, they kept a value of their own that the next update could reset before Protect reported the change. [#338](https://github.com/tinkerologie/ha-unifi-insights/issues/338)
- Client, outlet, policy-based route and VPN client changes no longer fall back to the classic site name `default` when the classic name of a site is unknown, which could act on the wrong site. The integration now looks the name up once more and otherwise stops with the error "No verified classic site name found for site …". [#338](https://github.com/tinkerologie/ha-unifi-insights/issues/338)
- Restarting a network device, with the restart button or the `unifi_insights.restart_device` action, now reports an error when the console does not accept the restart, instead of succeeding silently. [#338](https://github.com/tinkerologie/ha-unifi-insights/issues/338)
- Error messages for device, Protect and network actions are now translated. [#338](https://github.com/tinkerologie/ha-unifi-insights/issues/338)
- Network device polling now reuses the classic site names that the configuration poll looks up, instead of asking the console for them every 30 seconds. While that lookup fails, device telemetry keeps the last known names. [#338](https://github.com/tinkerologie/ha-unifi-insights/issues/338)
- Added ADR-009, which amends the proposed flat module layout (ADR-007) for the deepening issues: shared field and MAC helpers live in `data_transforms.py`, identity-specific MAC policies stay separate, `service_targets.py` becomes an allowed module, and `coordinators/protect.py` has a capped size exception until #340. [#337](https://github.com/tinkerologie/ha-unifi-insights/issues/337)
- Network device online state now follows the UniFi Network API states. Only `ONLINE` counts as online. `OFFLINE`, `CONNECTION_INTERRUPTED`, `ISOLATED` and `U5G_INCORRECT_TOPOLOGY` count as offline. `PENDING_ADOPTION`, `UPDATING`, `GETTING_READY`, `ADOPTING` and `DELETING` count as unknown. `CONNECTED`, `UP`, `DISCONNECTED` and `DOWN` are not network device states, so they are now unknown in any letter case: lowercase `connected` or `up` no longer makes a device's entities available or its "Device status" sensor on. On the dashboard cards, `CONNECTION_INTERRUPTED` and `ISOLATED` devices now show as offline (before: unknown), and `DISCONNECTED` and `DOWN` devices show as unknown (before: offline). Protect devices are not affected. [#337](https://github.com/tinkerologie/ha-unifi-insights/issues/337)
- Client type is now recognized by an exact match on `WIRED` or `WIRELESS` (after removing a `ClientType.` prefix, in any letter case), instead of a substring match. A decorated value such as `WIRED_GUEST` no longer counts as wired. This affects the wired and wireless client count sensors and the site health card counts, client tracking options and Wake buttons. Topology card clients also fall back to the `connection_type` field. [#337](https://github.com/tinkerologie/ha-unifi-insights/issues/337)
- Port records still come only from the legacy device data. When that fetch fails, port sensors and SFP module sensors lose their ports together and show as unavailable or unknown until it recovers. This is now the documented behavior. [#337](https://github.com/tinkerologie/ha-unifi-insights/issues/337)
- The integration now installs the UniFi API client from PyPI as `unifi-official-api[talkback]==2.0.0` instead of shipping its own copy, which meets Home Assistant's `dependency-transparency` rule. Entities, unique IDs, config entries, services and dashboard cards are unchanged. Diagnostics report the installed library version (`2.0.0`) as `library_version` instead of `1.2.0+vendored`. Recorded as ADR-008, which supersedes ADR-003; API contract fixes now go to [tinkerologie/unifi-official-api](https://github.com/tinkerologie/unifi-official-api) first and reach the integration through a version pin bump. [#345](https://github.com/tinkerologie/ha-unifi-insights/issues/345)
- Component-wide setup retry, Site Manager account and Mobility ownership registries now use typed Home Assistant keys. Added an evidence-based Quality Scale rule inventory without claiming a tier, and exported the Access coordinator. [#336](https://github.com/tinkerologie/ha-unifi-insights/issues/336)
- Standardized entity names and translation keys, synchronized English entity translations, and moved static icons to `icons.json`. Names such as "Device restart", "WiFi QR code", "{port_label} SFP module" and the client reconnect button (named with a `{client_name}` placeholder) now use translations, so some friendly names may change. Displayed icons stay the same. Existing entity IDs and unique IDs are preserved. [#335](https://github.com/tinkerologie/ha-unifi-insights/issues/335)
- Binary sensors use their description categories, and outlet switches use the outlet device class. Port speed now has a data rate device class and the `Mbit/s` unit, which may affect its long-term statistics. [#335](https://github.com/tinkerologie/ha-unifi-insights/issues/335)
- Revised the proposed ADR-007 (flat module layout). It adds a size budget of about 2,000 lines per module: an oversized module is deepened rather than split, and the file moves land after the deepening issues. It records removing the vendored `api/` package as the end state (201 to 48 Python files), and it folds feature-named test files into one test module per code module. [#343](https://github.com/tinkerologie/ha-unifi-insights/issues/343)
- Updated `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`, `.github/instructions/*.instructions.md`, and `.github/prompts/*.prompt.md` to reflect Home Assistant 2026.10+ developer best practices, the Integration Quality Scale (Bronze through Platinum), HA 2026.8–2026.11 device registry and entity lifecycle rules (`via_device_id`, `DeviceEntry.config_entry_id`, `async_prepare_to_add_to_hass`, `async_entity_id_changed`), `probatio` schema typing, HA 2026 Web Awesome `--ha-*` frontend custom card standards, current UniFi Developer Portal OpenAPI versions, and all 17 platforms and 15 coordinator modules. [#331](https://github.com/tinkerologie/ha-unifi-insights/issues/331)

### Removed

- Removed the vendored API client `custom_components/unifi_insights/api/` (128 files, about 17,400 lines), its 24 client test modules, and its Ruff, mypy, Pyright, pre-commit and labeler exclusions. The library's own test suite covers the client. The integration package drops from 199 to 71 Python files. [#345](https://github.com/tinkerologie/ha-unifi-insights/issues/345)
- Removed the unreachable legacy `UnifiInsightsDataUpdateCoordinator` (`coordinator.py`) and its dedicated test suite (`tests/test_coordinator.py`). Live coordinator behaviour was covered in `tests/test_coordinators.py` before removal. Setup, reauthentication, setup retry, and entity behaviour do not change. [#334](https://github.com/tinkerologie/ha-unifi-insights/issues/334)
- Removed six unused data transforms (`map_device_status`, `transform_network_device`, `transform_protect_camera`, `transform_protect_light`, `transform_protect_sensor`, `transform_protect_chime`) from `data_transforms.py`, their `__all__` entries, and five associated sample fixtures in `tests/fixtures/library_responses.py`. [#334](https://github.com/tinkerologie/ha-unifi-insights/issues/334)
- Removed 110 unused constants and the `SCAN_INTERVAL_NORMAL` legacy alias from `const.py` (65 `API_PATH_*`, 24 `WS_*`, 2 `CAMERA_STATE_*`, 5 `ATTR_SENSOR_*`, 3 `ATTR_CHIME_*`, 2 `ATTR_VIEWER_*`, 4 `ATTR_LIVEVIEW_*`, and 5 `SENSOR_MOUNT_*`), keeping the 25 live constants in those families. [#334](https://github.com/tinkerologie/ha-unifi-insights/issues/334)
- Removed the unused `RepairsFlow` scaffold (`repairs.py`) and its tests (`tests/test_repairs.py`) because the integration did not create issues for its handlers. Authentication failures continue to use standard reauthentication. [#334](https://github.com/tinkerologie/ha-unifi-insights/issues/334)

### Fixed

- **Breaking:** Camera Video Mode now has readable labels, including both license plate modes, and no longer shows `unknown` for documented Protect modes. Its select keys `highFps`, `slowShutter`, `lprReflex` and `lprNoneReflex` become `high_fps`, `slow_shutter`, `lpr_reflex` and `lpr_none_reflex` so Home Assistant can translate them. Update state comparisons and `select.select_option` calls that use those four values. Entity IDs, the raw `video_mode` attribute, and `unifi_insights.set_video_mode` values and legacy aliases are unchanged; the action editor also shows readable labels. [#415](https://github.com/tinkerologie/ha-unifi-insights/issues/415)

- The topology card keeps client counts visible for root and unconnected groups when labels are enabled, while regular client groups show their count only once. [#413](https://github.com/tinkerologie/ha-unifi-insights/issues/413)

- A PDU outlet command sent a few seconds after another one no longer switches the earlier outlet back. While the PDU applies a change the controller still reports the old outlet values, and a poll in that window put them back in Home Assistant, so the next command sent them to the controller again. UniFi Insights now keeps each outlet's written on/off or power-cycling value until the controller reports it, for up to 60 seconds; other values from the poll still update, and after 60 seconds the controller's value wins. A command also needs a complete controller baseline now: while the polled outlet data is incomplete, sparse or malformed, it is refused without sending anything, and it works again after a poll that reports every outlet. [#376](https://github.com/tinkerologie/ha-unifi-insights/issues/376) [#371](https://github.com/tinkerologie/ha-unifi-insights/issues/371) [#412](https://github.com/tinkerologie/ha-unifi-insights/pull/412)
- The Protect coordinator no longer keeps every Protect event it receives for as long as Home Assistant runs. Memory grew without limit, and each new event took longer to store on a busy site. Events are now dropped about 25 hours after they arrive, also while no new events come in, which still keeps every event the event timeline card can show (at most the last 24 hours). A late update to an event that started more than 25 hours ago is no longer stored, so it can't bring a dropped event back. The timeline is unchanged, and an event repeated without changes no longer copies the stored events. [#379](https://github.com/tinkerologie/ha-unifi-insights/issues/379)
- Protect WebSocket updates for a device type UniFi Insights does not track (for example a new kind of device) no longer hold off the 30-second Protect poll. Each such update marked the data as refreshed and pushed the poll back, so a console that kept sending them could stop the poll, and with it device updates, the removal of deleted devices and the motion auto-off, from ever running. These updates are now ignored, and each unknown device type is logged once at debug level. [#384](https://github.com/tinkerologie/ha-unifi-insights/issues/384)
- A Protect light's motion no longer gets a false end five minutes after the regular poll refreshes the light. If the light's motion start arrived over the WebSocket, the five-minute auto-off still fired after the poll had rebuilt the light without it, and wrote an end and logged a missed end event. The poll now clears a light's pending auto-off when the light no longer has a motion start, as it already did for cameras. [#383](https://github.com/tinkerologie/ha-unifi-insights/issues/383)
- Protect motion, smart detection and doorbell ring entities no longer stay on while the cameras keep sending WebSocket updates. The five-minute auto-off for an event whose end was missed ran only at the 30-second poll or on a reconnect, and every device update postpones that poll, so with steady traffic it could be put off indefinitely. Every device update now also ends any motion, detection or ring that has been on for five minutes without an end. [#381](https://github.com/tinkerologie/ha-unifi-insights/issues/381)
- When a Protect WebSocket stream reconnects and the five-minute auto-off ends a motion, smart detection or doorbell ring whose end was missed, the motion, detection and doorbell entities now show the end straight away. Before, the end was recorded but entities showed it only at the next update. [#382](https://github.com/tinkerologie/ha-unifi-insights/issues/382)
- You can now delete the devices of a site that was deleted on the UniFi console from the device page. Before, **Delete** was refused because the site was neither polled nor deselected, so these devices stayed unavailable forever. A device can be deleted once the console's site list, from a successful refresh that lists at least one site, no longer has its site; devices of polled sites, and devices that also belong to Protect, Access, clients, WiFi, InnerSpace or Mobility, are still refused. So is a device that a Protect camera or NVR joined by MAC address while Protect still reports that camera or NVR. Automatic cleanup still keeps these devices on purpose, because a partial site list looks the same as a deleted site ([#391](https://github.com/tinkerologie/ha-unifi-insights/issues/391)). [#394](https://github.com/tinkerologie/ha-unifi-insights/issues/394)
- Carrier Fabric subscriber devices now link to their organisation device with `via_device_id` instead of the `via_device` key that Home Assistant deprecated in 2026.8. Sensor setup registers the organisation device in the owning config entry first, so a subscriber always links to its own entry's organisation device, also when another entry has a device with the same identifier. Existing organisation and subscriber devices keep their device IDs, and no device is added twice. [#373](https://github.com/tinkerologie/ha-unifi-insights/issues/373)
- The 19 actions that act on one entity, device or resource (for example `restart_device`, `set_hdr_mode`, `set_chime_volume`, `reconnect_client` and `carrier_suspend_subscriber`) no longer accept an area, floor or label target and then ignore it: `target: {area_id: …}` (or `floor_id`, `label_id`) inside the action data now fails validation with an error that names the field, also when the call names a valid entity as well. In the action editor these actions now show an **Entity** and a **Device** picker instead of Home Assistant's target picker, which always offers areas; the action data keys (`entity_id`, `device_id` and the resource ID fields) are unchanged, so existing automations and scripts that target one entity or device keep working. `set_chime_paired_doorbells` still accepts areas, floors and labels in its **Doorbells** field. [#356](https://github.com/tinkerologie/ha-unifi-insights/issues/356)
- Protect target routing no longer lets an explicit `console_id` skip the ownership check when the call also names a camera, light, chime or viewer: the named console must own the target, and otherwise the call stops with the translated error "UniFi Protect camera … does not belong to the console …". `console_id` still chooses between consoles that both own a target with the same native ID. A named console that has not loaded its Protect devices yet is refused when another console already owns the target; while no console can tell, the named console is still used. Today no camera, light, chime or viewer action accepts `console_id`, so nothing changes for users; `trigger_alarm` and `create_liveview` still route on `console_id` alone. [#355](https://github.com/tinkerologie/ha-unifi-insights/issues/355)
- A network device that the v1 device list leaves out is no longer removed from the Home Assistant device registry, with its entities, while the classic (legacy) device list fetched in the same update still reports it by MAC address. Such an update does not count toward removal, and an update whose classic device list fails or comes back empty removes nothing on a site with a pending removal. The check reuses the classic device list each update already fetches, so it adds no requests to the console. A site without a classic site name is unchanged and still removes a device after 3 updates without it. [#395](https://github.com/tinkerologie/ha-unifi-insights/issues/395)
- The site internet activity sensors and the internet activity dashboard card now recognize a site listed as unavailable whether the published list of such sites is a `set`, a `frozenset` or another read-only set, a list or a tuple; before, the card only recognized a `set` and the sensors only a `set`, list or tuple. Today the configuration coordinator publishes a `set`, so nothing changes for users. [#342](https://github.com/tinkerologie/ha-unifi-insights/issues/342)
- Network devices and their entities are no longer removed from the Home Assistant device registry when the integration falls back to classic endpoints or when an update returns no devices. Devices keep their v1 identifier during fallback by remembering their hardware MAC address, so unique IDs do not change when endpoints alternate. A missing network device is removed on the 3rd consecutive eligible poll in which it is absent, and a device is not removed if its hardware MAC is still present. If Home Assistant restarts while a site is already on fallback, devices are temporarily keyed using fallback identities until the first successful v1 poll restores persistent identifiers; leftover entities may appear unavailable until the device is removed or the config entry is reloaded. If a device was detached before this fix, reload the Network config entry to restore it. [#391](https://github.com/tinkerologie/ha-unifi-insights/issues/391)
- The Network device and configuration polls now publish their results once, after every call of the poll has returned, as new data instead of writing into the data they published before. An update from another part of the integration while a poll was still running (for example a Protect event) could show entities and dashboard cards a mix of old and new data, such as this poll's sites with the previous poll's Wi-Fi networks, and the network device data it showed could change afterwards without an update. A poll that fails now leaves all of the previous data in place; before, it already dropped the data of sites that are no longer selected. Entities, states, unique IDs and polling intervals are unchanged. [#342](https://github.com/tinkerologie/ha-unifi-insights/issues/342)
- PDU and other network device entities now stay available while the device applies a change (`GETTING_READY`). Outlet commands sent during that window are no longer dropped, and Status and WAN status show unknown until connectivity is known again. [#372](https://github.com/tinkerologie/ha-unifi-insights/issues/372)
- The UDM Pro and other gateways that report no device type and only the `switching` feature are now treated as the gateway everywhere. Before, when the legacy device fetch failed, the dashboard cards counted such a gateway as a switch, and site health showed the gateway as missing and the internet as unknown. Gateway detection is now shared by entities and cards, so a device that was not recognized as a gateway before can gain WAN sensors. Existing entity IDs and unique IDs do not change. [#337](https://github.com/tinkerologie/ha-unifi-insights/issues/337)
- The uplink transmit rate and receive rate sensors reported 8 times the real rate: the UniFi API already sends these values in bits per second, and the integration multiplied them by 8. The sensors keep the same entity IDs and units and now show the real rate. History and long-term statistics recorded before this release stay 8 times too high and are not rewritten. [#337](https://github.com/tinkerologie/ha-unifi-insights/issues/337)
- The performance and internet activity cards showed no throughput, because they read fields the integration never stores. They now show the same uplink rates as the sensors. [#337](https://github.com/tinkerologie/ha-unifi-insights/issues/337)
- Port sensors now find ports whose index the console reports as text (for example `"03"`) or as `0`. Before, these sensors stayed unknown or unavailable. The SFP module sensor reads the same port records as the port sensors. The site health card now shows the gateway uptime. A port whose index is not a whole number (for example `1.5` or `"uplink"`) no longer gets port sensors, and a PoE port that reports only `port_idx` can now get a PoE power cycle button. [#337](https://github.com/tinkerologie/ha-unifi-insights/issues/337)
- Console polling failures now rely on Home Assistant's unavailable/recovered transition logs instead of repeated per-poll warnings. Protect per-family failures and device legacy fallback now log once per outage and once on recovery. Client reconnect, Wake-on-LAN and network restart buttons follow device coordinator availability; offline clients remain wakeable while the console is reachable. Removed shadowed availability assignments and redundant rule-audit state writes while preserving every event and availability transition. [#336](https://github.com/tinkerologie/ha-unifi-insights/issues/336)
- The topology card always roots the tree at the gateway. A gateway whose reported uplink is a switch on the same site (for example a mutual uplink over one 10G link between a UCG Fiber and its core switch) no longer makes that switch its parent, in both the tree and tiered layouts. [#346](https://github.com/tinkerologie/ha-unifi-insights/issues/346)

## [2026.10.2] - 2026-10-16

### Changed

- Group clients by uplink device once per site to reduce repeated scans while preserving client ordering and skipping non-string uplink identifiers. [#328](https://github.com/tinkerologie/ha-unifi-insights/issues/328)
- Mobility router entity child linking detects `via_device_id` using `DeviceInfo.__annotations__` instead of `DeviceInfo.__optional_keys__` for Home Assistant 2026.11 dataclass compatibility. [#324](https://github.com/tinkerologie/ha-unifi-insights/issues/324), [home-assistant/core#185211](https://github.com/home-assistant/core/issues/185211)

- Local validation now uses the project environment for the complete mypy check, including Home Assistant's actual schema types. Generated frontend bundles are excluded from source spelling/formatting hooks; source formatting, YAML, copyright notices and keyword-only helper calls were aligned with the repository's existing checks. Runtime schema imports remain compatible with the supported older HA versions. [#283](https://github.com/tinkerologie/ha-unifi-insights/issues/283)

- PR labeler (now the shared tinkerologie workflow) applies at most one type label: the title's Conventional Commit type decides (`feat` → `enhancement`, `fix` → `bug`, other types → neither), then the branch prefix, then a single ticked Type of Change box, then the linked issues. A contradicting type label added by automation is removed; one added by a person is kept. The label is re-evaluated when the title or description is edited, and a CHANGELOG.md-only change no longer adds `documentation`.
- Dependabot pull requests whose largest update is a patch or minor version merge automatically once the checks and commit statuses on their head commit have finished without a failure (a Codecov check that is still pending is not waited for; a Codecov failure blocks); majors, and pull requests with open review threads, stay for review.
- The retained chime **Play** button and `unifi_insights.play_chime_ringtone` action now always raise a clear not supported error and send no request because the official Protect API has no chime playback route. The optional playback `ringtone_id` field remains for automation compatibility; calls with and without it raise the same error. [#268](https://github.com/tinkerologie/ha-unifi-insights/issues/268), [#275](https://github.com/tinkerologie/ha-unifi-insights/issues/275)
- Release workflows hardened (least-privilege job permissions, no expressions in shell scripts, no persisted checkout credentials, no dependency caches); scheduled workflows run on Australia/Brisbane time. LICENSE now contains the complete Apache License 2.0 text.
- Workflow layout tidied after the release hardening (no behaviour change): `permissions:` blocks moved back to the top of their workflow or job in `release.yml`; blank-line layout restored in `ci.yml`; the `id-token: write` comment in `ci.yml` names the Codecov OIDC upload.
- Dashboard card bundle rebuilt with Vite 8.3.4, so the committed bundle matches its source again after the Dependabot Vite bump.
- Tinkerologie-Bot governance report updated to v1.4.3: an older run can no longer post a stale report after a newer run passed. Workflow formatting tidied.
- Repository standardised to the tinkerologie layout (STANDARDS.md in tinkerologie/.github): workflows renamed to the standard set (CI, Validate, Release, PR Governance, ...), every action pinned to a commit SHA, shared Dependabot, `.editorconfig`, `.gitattributes` and `CODEOWNERS` files, and organization-wide labels and community files.
- Tinkerologie-Bot's comment is now a readable "Pull request checklist": each problem is a numbered heading with plain-English guidance and its items listed underneath, and report updates for a pull request are serialised so an older run can never overwrite a newer one.
- Contribution governance: issues without a `bug` or `enhancement` label are labeled automatically when their type can be determined (issues labeled `question`, `duplicate`, `invalid`, `wontfix`, or `no-type` are skipped), so CodeRabbit can plan them. Pull requests that conflict with the default branch are labeled, and their authors are notified in a comment. Stale issues and pull requests are labeled after 14 days without activity and closed 30 days later. PR governance now re-validates a human-authored, non-draft PR when a bot such as CodeRabbit edits its description, instead of skipping the check and reporting a pass, only maintainers with write access may use `None` instead of linking an issue, and a link to an issue that does not exist now fails the check. Codecov patch coverage must now reach 90%.
- Tinkerologie-Bot now posts one comment on pull requests that fail PR governance, listing every problem to fix, and updates it once they are fixed. PR governance runs from the default branch's workflow (`pull_request_target`), so a pull request cannot change its own check.
- Updated repository and documentation URLs to `tinkerologie` organization
- Configured Codecov GitHub Actions workflow for tokenless coverage uploads
- Diagnostics now also redact the address, domain and MAC filters of firewall policies and the domains, address ranges and next hop of policy-based routes.
- Changed `script/test` to provision Home Assistant 2026.10.1 with `pytest-homeassistant-custom-component` 0.13.372 (previously 2026.6.2 and 0.13.338) as the default local test target. The minimum supported Home Assistant version stays 2026.6.0.
- **Breaking change:** The chime **Ringtone** select and `unifi_insights.set_chime_ringtone` now use opaque Protect ringtone IDs instead of the fixed names `default`, `mechanical`, `digital`, `christmas`, `traditional`, `custom1` and `custom2`. The select shows only IDs already used by this console's chimes; the action also accepts IDs not currently in use, which the console validates. Update ringtone automations to use the ID shown by the select or its `chime_ringtone_id` attribute. Entity and action names are unchanged. [#268](https://github.com/tinkerologie/ha-unifi-insights/issues/268)
- **Breaking change:** The camera **Microphone** switch is replaced with a read-only **Microphone** binary sensor reporting `isMicEnabled`, unless the camera reports it has no microphone. The Protect API `PATCH /v1/cameras/{id}` schema does not accept `isMicEnabled` and rejects requests with unknown fields, making the microphone state unwritable. Existing `switch.*_microphone` entity registry entries are automatically removed on upgrade, and the microphone state is now available under `binary_sensor.<camera name>_microphone`. Any automations attempting to toggle the switch must be removed or updated. ([#269](https://github.com/tinkerologie/ha-unifi-insights/issues/269))
- Aligned the vendored UniFi Network API client with OpenAPI spec v10.6.106: updated device adoption path and limit flag, removed non-spec device locate and site lookup methods, updated DPI categories/applications and country lookups to unscoped endpoints with pagination, corrected firewall policy ordering endpoints and query parameters, enabled firewall policy patching, aligned site-to-site VPN tunnel paths, converted network updates to full-object PUT requests, and added voucher deletion by filter.
- The README is reorganized around the supported UniFi products: Network, Protect, InnerSpace, Mobility, Site Manager and Carrier Fabric (ISP). It now has an overview, a product summary, setup steps per connection type, all registered actions and all six dashboard cards. The "How this differs from the official integrations" section is removed. Inaccurate claims are corrected: firmware update entities don't install firmware, there is no PoE port power-cycle action, there is no Recording binary sensor or Recording Mode select, and the topology card is listed as "UniFi Topology". [#230](https://github.com/tinkerologie/ha-unifi-insights/issues/230)
- Vendored Protect client aligned with Protect API v7.3.70: removed undocumented endpoints that nothing called; added users, ULP users and POS transaction ingest.

### Added

- UniFi Protect `media_player` platform for cameras with speaker support (`hasSpeaker: true`). Plays audio via camera talkback transport (RTP/Opus/AAC/Vorbis) with real-time PyAV audio transcoding and pacing, reports read-only volume level, and supports media playback, media stop, and media browsing. ([#221](https://github.com/tinkerologie/ha-unifi-insights/issues/221))

- Optional UniFi Access support on Local entries, using a separate bearer token and Access developer port. Adds door locks and unlock buttons, Gate Hub covers, Lockdown and Evacuation switches, doorbell notifications, signed access-result webhooks, and administrator-only user PIN and status actions. Access failures are isolated from other console applications; security state is read back after commands, and credentials are redacted from diagnostics. [#283](https://github.com/tinkerologie/ha-unifi-insights/issues/283)
- An administrator-only **Actions** menu on the topology card. Right-click or long-press a node in browsers that send a context-menu event on a long-press, or focus it and press the ContextMenu key or Shift+F10, to select it and open the menu. Otherwise, use the **Actions** button in the node details. It offers **Copy IP address**, **Copy MAC address**, **Open device** and **Open entity** when available. Other users may still see a separate **Open device** link in node details. If copying fails, the card shows the value as selectable text for manual copying. ([#282](https://github.com/tinkerologie/ha-unifi-insights/issues/282))
- A `layout` option for the topology card, also in the card editor as **Layout**: `tree` (the default) or `tiered`. The graph view puts gateways, switches, access points, other devices and clients in their own rows (columns with `orientation: horizontal`); the list view ignores this option. ([#282](https://github.com/tinkerologie/ha-unifi-insights/issues/282))
- An admin-only WebSocket command, `unifi_insights/topology/node_details`, that returns one node's IP address and MAC address and, when available, the Home Assistant `device_id` and `entity_id` for the menu. Non-administrators are refused. The topology snapshot and subscription stream never contain MAC addresses, IP addresses, hostnames or firmware versions. ([#282](https://github.com/tinkerologie/ha-unifi-insights/issues/282))
- Three Home Assistant 2026.6.0+ automation blueprints: `infrastructure_offline.yaml`, `ap_overload.yaml` and `secure_subnet_new_client.yaml`. Import them from their My Home Assistant links or copy them into the config blueprints folder; HACS does not import blueprints. The offline alert cannot distinguish an offline device from a controller outage. AP utilization alerts need a sensor you provide. The secure subnet alert requires client tracking, waits 10 seconds after tracker creation, matches IP prefixes, alerts only on first creation and drops runs above 100 concurrent instances with a logged warning. Inputs and limits are in the README. ([#282](https://github.com/tinkerologie/ha-unifi-insights/issues/282))
- Hotspot voucher management entities and QR code ([#279](https://github.com/tinkerologie/ha-unifi-insights/issues/279)):
  - Number entities to configure voucher duration in minutes (1–1,000,000, default 480), guest limit (0–1000), download/upload rate limits (0–100 Mbit/s in 0.1 steps, ×1000 to kbps), and byte quota (0–1,048,576 MB); 0 omits the limit ("not set; UniFi applies its own default").
  - Button entity to generate a new hotspot voucher using the configured parameters.
  - Sensors for active unexpired voucher count, latest voucher activation code, and latest voucher expiration timestamp.
  - Image entity rendering a pure QR code of the latest voucher code for captive portal login.
  - Targeted per-site inventory refresh on voucher generation or deletion rather than waiting for the five-minute polling cycle.
- Storage sensors for UniFi consoles that report their filesystems to the Network application (for example a UDM Pro SE: eMMC, Backup and Temporary). Each filesystem gets a **Used Percentage** sensor, plus **Used** and **Total** sensors that are disabled by default; temporary in-memory filesystems such as /tmp are disabled by default too. A **Storage Nearly Full** problem sensor turns on when a persistent filesystem is 90% full or more. The values come from device data the integration already fetches, so there are no extra requests. Per-drive SMART, temperature, RAID and Drive/UNAS monitoring require supported API fields. Protect NVR aggregate diagnostics are conditional on reported storage fields; the current official `/v1/nvrs` response lacks them. [#281](https://github.com/tinkerologie/ha-unifi-insights/issues/281)
- Conditional Protect NVR storage health and nearly-full diagnostics using validated shared telemetry; preserve existing capacity identities and units, distinguish missing health from healthy, and align dashboard warnings with entity states. ([#281](https://github.com/tinkerologie/ha-unifi-insights/issues/281))
- Port forward and traffic rule switches. Each port forwarding rule and traffic rule on the console becomes a configuration switch on the site's gateway that turns the rule on or off. A switch whose rule is deleted on the console becomes unavailable. It is removed the next time the integration loads, unless it was the last rule of its type on that site, in which case delete it manually. Diagnostics redact port forward addresses and traffic rule domains and address ranges. ([#280](https://github.com/tinkerologie/ha-unifi-insights/issues/280))
- NAT rule and static route switches. Each custom NAT rule and each static route on the console becomes a configuration switch on the site's gateway that turns it on or off. NAT rules that the console manages itself get no switch. Static route switches are disabled by default, because turning off a route can cut Home Assistant off from the console. Both were tested with an API key on one UniFi gateway through the cloud connector. A console can refuse requests for a few seconds while it applies a static route change; the switch then reads the rules again 15 seconds later instead of leaving the site's rule switches unavailable until the next poll. Diagnostics redact NAT match addresses and static route networks and next hops. ([#280](https://github.com/tinkerologie/ha-unifi-insights/issues/280))
- Network rule change events. Each site gets an event entity, Network rule changes, that fires `added`, `modified` or `deleted` when a firewall policy, policy-based route, port forward, traffic rule, NAT rule or static route changes on the console, including changes made from Home Assistant. Changes are found when the configuration is polled (every 5 minutes), so a change that is undone between two polls is not reported, and a rule order change alone is not a change. The event data names the rule (its name or description as typed on the console), its type, whether it is enabled and which fields changed; it never contains the values of other fields such as addresses, ports or domains. Deleting the last rule of a type on a site is reported one poll later. ([#280](https://github.com/tinkerologie/ha-unifi-insights/issues/280))
- `unifi_insights.export_network_rules` action. It returns the current firewall policies, policy-based routes, port forwards, traffic rules, NAT rules and static routes of one console as a versioned document, read from the console when the action runs, with an `ok`, `unsupported` or `failed` status for each rule type of each site; only `ok` is complete. `failed` means the answer could not be read, was not JSON, was incomplete or held a record that could not be parsed; `unsupported` means the console refused the request or the site has no classic name for the five non-firewall rule types. It is a snapshot of those six rule types for comparison, versioning and manual recovery, not a complete console backup: the networks, zones, traffic matching lists, groups and VPN definitions that rules refer to are not included, and there is no import. It requires an admin user and only returns data. By default it keeps each rule's ID, enabled state, type, action, protocol, port settings of port forwards and NAT rules, a port forward's WAN interface name, order index, logging flag, kill switch, matching target, connection state, route distance and gateway type, and replaces names, descriptions, addresses, domains, firewall source and destination, network and client references and anything unrecognised; site IDs become `site-1`, `site-2` by rank among the exported sites, and the console title and entry ID are removed. `redact: false` returns the fields the console sent. ([#280](https://github.com/tinkerologie/ha-unifi-insights/issues/280))
- A **Wake _Client name_** button for each wired client, disabled by default, to wake a device that is off. Buttons sit on the site's gateway device, or on the site device when the site has no gateway, and need **Enable Client Control**. A button you have enabled is always restored after a restart, even if its client is offline, unnamed or absent from history, or history cannot be fetched. A background task also seeds buttons for named wired clients seen in the last 30 days. UniFi has no wake function, so Home Assistant sends the packet through its built-in Wake on LAN integration, loaded automatically as a dependency. The packet goes to the client's network broadcast address when known, otherwise to 255.255.255.255. Turning off **Enable Client Control** removes the Wake buttons. Home Assistant needs host networking and must be on the same VLAN as the device; the device needs Wake-on-LAN enabled in its BIOS and operating system. ([#253](https://github.com/tinkerologie/ha-unifi-insights/issues/253))
- `unifi_insights.reconnect_client` and `unifi_insights.remove_clients` actions for parity with Home Assistant core UniFi Network. Reconnects connected wireless clients to Wi-Fi, and prunes short-lived unnamed historical clients across configured consoles. ([#222](https://github.com/tinkerologie/ha-unifi-insights/issues/222))
- An alarm control panel for the UniFi Protect alarm (Alarm Manager), one for each NVR that reports an arm mode. It shows disarmed, arming, armed away or triggered, and can arm (away) and disarm using the selected arm profile. A status it does not recognise shows as unknown, never as disarmed. Protect's own arm state does not follow the UniFi global alarm manager, so the panel is not created, or becomes unavailable, while the global alarm manager is enabled, and Home Assistant says so if Protect refuses a command because it is. [#220](https://github.com/tinkerologie/ha-unifi-insights/issues/220)
- A **Power cycle** button for each switch port with PoE enabled, and a `unifi_insights.power_cycle_port` action, to restart a hung PoE device (an access point or camera, for example) by cutting and restoring its power. The buttons are disabled by default. Uses the documented `POST …/devices/{deviceId}/interfaces/ports/{portIdx}/actions` endpoint. [#245](https://github.com/tinkerologie/ha-unifi-insights/issues/245)
- UniFi InnerSpace floor-plan image entities. Creates an image entity for each InnerSpace floor plan that has an image (plans without one get no entity until they do), fetching the image on demand, caching it, and going unavailable if it can't be fetched, with one background retry per coordinator update. A floor plan that is removed on the console (or loses its image) has its device removed too, and the image URL shown in the entity attributes has any query string stripped.
- Vendored Site Manager API client coverage for host lookup by ID, interval-based ISP metric querying, and SD-WAN configuration and status endpoints.
- UniFi Mobility API client coverage for workspace admins, device clients, and device configuration (name, network, wireless).
- Vendored Carrier Fabric (ISP) API client coverage for the subscriber writes: create and update a subscriber, attach, re-link or detach its gateway host, and assign a service plan. This completes the Carrier Fabric API v1.0.0 spec in the client. No Home Assistant actions use them yet. [#252](https://github.com/tinkerologie/ha-unifi-insights/issues/252)
- UniFi Mobility support for remote entries. When the cloud API key has Mobility read access, each active Mobility workspace becomes a device with router counts, and each mobile router (UMR, UMR Industrial, UMR Ultra) becomes a device with connectivity, state, clients, WAN source, LTE signal, cellular data usage, VPN and subscription status, and a GPS location tracker, plus diagnostic memory, uptime, firmware, data limit, plan, WAN IP, and ISP sensors. A key without Mobility access is not an error: no re-authentication is requested and no entities are created. Only one remote entry per API key polls Mobility, and diagnostics include a counts-only Mobility summary. [#169](https://github.com/tinkerologie/ha-unifi-insights/issues/169)
- `set_chime_paired_doorbells` action to choose which doorbells ring a chime, using this integration's doorbell ring sensors, like the action in Home Assistant's UniFi Protect integration. Leaving the doorbells empty unpairs them all. A selection that contains no doorbell, a sensor that is not a doorbell ring sensor, or a doorbell on another console is refused instead of changing the chime. A camera counts as a doorbell by its model, not by a doorbell-like name. [#223](https://github.com/tinkerologie/ha-unifi-insights/issues/223)
- Support for UniFi Carrier Fabric (ISP) accounts ([#172](https://github.com/tinkerologie/ha-unifi-insights/issues/172)):
  - Dedicated configuration flow and integration entry type for Carrier Fabric.
  - Aggregated subscriber metric sensors (total, suspended, provisioned, installed, unassigned, pending assignment) and active service plans.
  - Per-plan subscriber count diagnostic sensors (archived plans disabled by default).
  - Optional subscriber tracking with state and service plan sensors per subscriber device.
  - Guarded `carrier_suspend_subscriber` and `carrier_resume_subscriber` service actions with scope validation and write-conflict retry.
  - Diagnostics support with allowlisted fields only; subscriber names and subscriber numbers are redacted.
- UniFi Protect alarm hubs, Thread gateways and keypad fobs. All of these are read-only, because the Protect API only lets integrations rename these devices.
  - **Tamper Detection** binary sensor for each alarm hub. It follows the hub's reported tamper status (Protect 7.3.70 and newer) and tamper events (also on older Protect versions). Until either arrives it shows `unknown`, never a false "clear". It also shows the time and user name of the last tamper event.
  - **Thread Network** problem binary sensor, plus **Thread Role** and **Thread Joined Devices** diagnostic sensors, for link stations and alarm hubs that run a Thread network (Protect 7.3.70 and newer). Gateways without Thread get no Thread entities.
  - **Keypad Beep**, **Keypad Beep Volume** and **Arm Control** diagnostic entities for fobs with a PIN keypad (Protect 7.3.70 and newer). These are disabled by default.
  - Older Protect versions that lack these endpoints are checked once an hour instead of on every poll, and logged once.
  - Diagnostics redact the tamper user name and the Thread network name and IDs.
- UniFi Protect sirens appear as siren entities. Turning one on sounds it for 5, 10, 20 or 30 seconds (Protect's default is 5) and can set its volume first. Turning it off stops it right away. Protect versions without sirens are checked once an hour and logged once. [#219](https://github.com/tinkerologie/ha-unifi-insights/issues/219)

### Removed

- **Breaking change:** The camera **Privacy Mode** switch is removed because the official UniFi Protect API neither reports privacy mode in camera GET responses nor accepts privacy fields in PATCH requests (toggling resulted in a 400 AJV_PARSE_ERROR). Existing `switch.*_privacy_mode` entity registry entries are automatically removed on upgrade. ([#271](https://github.com/tinkerologie/ha-unifi-insights/issues/271))
- **Breaking change:** The `unifi_insights.set_recording_mode` action is removed because recording mode is not in the Protect v7.3.70 camera API (PATCH or GET), and the camera PATCH rejects unknown fields. Automations and scripts calling this action must be removed. ([#272](https://github.com/tinkerologie/ha-unifi-insights/issues/272))

### Fixed

- Redact classic Network API secrets and token aliases, including unterminated quoted values, from response logs using a shared credential policy. Omit malformed bodies when credential values cannot be safely redacted, and omit response bodies for classic Wi-Fi configurations. [#327](https://github.com/tinkerologie/ha-unifi-insights/issues/327)
- Talkback audio now stops when a Protect camera speaker entity is removed, reloaded, or unloaded. [#325](https://github.com/tinkerologie/ha-unifi-insights/issues/325)
- Corrected ten known vendored API contract mismatches against Network v10.6.106 and Protect v7.3.70: network creation no longer injects legacy DHCP fields; traffic lists send `items`; firewall policies and Wi-Fi broadcasts use nested configuration; WAN listing omits the unsupported filter; DPI category and application IDs accept integers; Protect uploads send multipart binary content with a fresh body per retry; and sensor motion sensitivity uses `motionSettings.sensitivity`. [#320](https://github.com/tinkerologie/ha-unifi-insights/issues/320)
- Vendored Network creation methods now raise `ValueError` before sending unsupported or ambiguous legacy DHCP, traffic-list, firewall and Wi-Fi inputs. Supply management-specific network fields and traffic-list `items` when legacy arguments have no supported translation. Traffic-list `description` is unsupported; WAN `filter_str` remains accepted but is ignored.
- Disabling Carrier Fabric subscriber tracking now preserves devices and entities owned by another config entry on older Home Assistant registries. [#323](https://github.com/tinkerologie/ha-unifi-insights/issues/323)
- A temporary Access outage during startup now recovers through polling and entity discovery without reloading the entry. Failed webhook cleanup during a host or port change preserves the old receiver and connection settings instead of orphaning the registration. An identity conflict or save error after cleanup schedules an entry reload to rebuild its webhook. Access shutdown cancels notification-triggered and periodic snapshot reads before closing the client. [#283](https://github.com/tinkerologie/ha-unifi-insights/issues/283)
- Access notification close handshakes now have a firm deadline when a console keeps sending frames without a CLOSE reply, preventing cleanup before reconnect from waiting indefinitely. [#283](https://github.com/tinkerologie/ha-unifi-insights/issues/283)
- The topology card's zoom in, zoom out and fit buttons now use the same rounded shape as the other toolbar buttons, with their icons centred, instead of circles with off-centre icons.
- Site client-count, internet-activity and voucher entities now include the canonical console identity in their unique IDs, so consoles sharing an upstream site ID receive independent entities. Owned logical legacy registry entries with matching translation keys migrate in place, preserving entity IDs, history, custom names, areas, visibility and device links. Ambiguous registry metadata skips the candidate with a warning, and existing physical rows are never reclassified. An occupied target retains the owned legacy entity and logs a warning; a foreign target is never claimed. Console identity changes preflight all affected rows and compensate synchronous failures across native registry and config-entry updates. Legacy local entries whose console identity contains the credential are backfilled before sensor discovery; when no MAC is available, setup and local configuration use the actual host rather than a shared site ID. Reauthentication clears unusable console metadata before rotating the key, so the old credential cannot become a namespace. Local reconfiguration distinguishes actual MAC addresses from stored host URLs, allowing an owned host-based identity to migrate in place when the console later reports its hardware identity. Stale-port cleanup requires a genuine physical-port identity and preserves ambiguous logical or storage rows, including namespaces containing `_port_`. Physical identities, including the `{site_id}_{gateway_id}` gateway device, and upstream resource IDs for Wi-Fi, port forwards, traffic rules, firewall policies, policy routes and VPN clients remain unchanged. Virtual-device identifiers (`site_{site_id}`, `firewall_policies_{site_id}`, `policy_based_routes_{site_id}`, `vpn_clients_{site_id}` and `wifi_{wifi_id}`) remain a separate follow-up requiring a split migration for each owning entry on older Home Assistant device models. [#309](https://github.com/tinkerologie/ha-unifi-insights/issues/309)
- The chime **Ringtone** select and `unifi_insights.set_chime_ringtone` now write the selected ID as `ringSettings[].ringtoneId` for every paired doorbell, preserving each doorbell's volume and repeat count. Missing, malformed or mixed ring settings now show unknown instead of an invented `default` ringtone. [#268](https://github.com/tinkerologie/ha-unifi-insights/issues/268), [#275](https://github.com/tinkerologie/ha-unifi-insights/issues/275)
- Generating hotspot vouchers (`unifi_insights.generate_voucher`) without a note no longer fails: the voucher name the Network API requires now defaults to "Home Assistant".
- Generating hotspot vouchers no longer reports "Unable to generate voucher" after the vouchers were created. The client now reads the API's `{"vouchers": [...]}` response, so a false failure no longer prompts a retry that could create duplicate vouchers.
- Restarting a UniFi Network device (Restart button or `unifi_insights.restart_device`) now uses the documented device-actions endpoint (`POST …/devices/{id}/actions`) instead of the undocumented `…/restart` path.
- The chime Play button no longer passes an extra ringtone argument to the coordinator. Chime playback remains unsupported by the official Protect API. [#268](https://github.com/tinkerologie/ha-unifi-insights/issues/268)
- Camera status light and video mode now send the field names the Protect API documents (`ledSettings`, `videoMode`).
- Floodlight mode and brightness now send the field names the Protect API documents (`lightModeSettings.mode`, `lightDeviceSettings.ledLevel`), and brightness now reads and writes the Protect 1-6 LED level correctly (a fully bright floodlight previously showed about 6%).
- The camera Status Light switch no longer fails on every toggle, and both switches now send the field names the Protect API documents (`ledSettings` and `videoMode`). [#242](https://github.com/tinkerologie/ha-unifi-insights/issues/242)
- Floodlight mode and brightness now send the field names the Protect API documents (`lightModeSettings.mode`, `lightDeviceSettings.ledLevel`). Brightness now reads and writes the Protect 1-6 LED level (a fully bright floodlight previously showed about 6%). The `set_light_level` service and the Light Level number keep their 0-100 range and convert it to the 1-6 level.
- Secrets in logged API responses are now fully redacted. Before, a password, Wi-Fi passphrase, token or API key containing a colon (`:`) or an escaped quote was only partly hidden: debug logs and "Response is not JSON" warnings showed the value up to its last colon, for example `"passphrase": "my:secret: "**REDACTED**"`. Now the whole value is replaced: `"passphrase": "**REDACTED**"`.
- The **Chime Volume** number and `unifi_insights.set_chime_volume` now set the volume where the Protect API keeps it: in the chime's ring settings, for each paired doorbell. Before, they sent a top-level `volume` field that the chime endpoint doesn't accept. A chime with no paired doorbell has no volume to set; the error log records why. [#260](https://github.com/tinkerologie/ha-unifi-insights/issues/260)
- The **Microphone Volume** number and `unifi_insights.set_mic_volume` now go from 1 to 100, the range the Protect API accepts for `micVolume`. Before, they offered 0, which is below the API minimum. `set_mic_volume` with `volume: 0` is now rejected when the action is called, instead of being sent to the console. When a camera doesn't report a volume, the number now shows unknown instead of 0. [#261](https://github.com/tinkerologie/ha-unifi-insights/issues/261)
- Turning a firewall policy switch or WiFi network switch on or off no longer reports an error when the controller applies the change but answers with an empty body. [#262](https://github.com/tinkerologie/ha-unifi-insights/issues/262)
- The chime **Repeat Times** number and `unifi_insights.set_chime_repeat_times` now set the repeat count in the chime's ring settings, for each paired doorbell, instead of sending a top-level `repeatTimes` field the chime endpoint doesn't accept. [#267](https://github.com/tinkerologie/ha-unifi-insights/issues/267)

## [2026.10.1] - 2026-10-04

### Fixed

- WiFi devices (`WiFi: <SSID>`) can now be deleted from the device page once no polled site provides that network any more, for example after deselecting its site in the options or deleting the network on the controller. Previously Home Assistant rejected the removal with "Failed to remove device entry, rejected by integration". WiFi networks still served by a polled site, and any network whose site failed to refresh, still can't be removed. [#213](https://github.com/tinkerologie/ha-unifi-insights/issues/213)
- Topology card: when a card is too short for a node's details, they now open in a dialog over the dashboard, sized to fit them, instead of in a panel cut off by the card. The dialog only scrolls if the screen itself is too short. Cards with room still show the details beside the graph. [#186](https://github.com/tinkerologie/ha-unifi-insights/issues/186)

## [2026.10.0] - 2026-10-01

### Added

- Five new UniFi dashboard cards delivered as modular per-card Lovelace resources (`site-health-card.js`, `internet-activity-card.js`, `performance-card.js`, `protect-status-card.js`, and `timeline-card.js` alongside `topology-card.js`), registered in the card picker as:
  - **UniFi Site Health** (`custom:unifi-insights-site-health-card`)
  - **UniFi Internet Activity** (`custom:unifi-insights-internet-activity-card`)
  - **UniFi Device Performance** (`custom:unifi-insights-performance-card`)
  - **UniFi Protect Status** (`custom:unifi-insights-protect-status-card`)
  - **UniFi Event Timeline** (`custom:unifi-insights-timeline-card`)

- New dashboard snapshot builders on the integration side:
  - `site_health.py`
  - `internet_activity_snapshot.py`
  - `performance.py`
  - `protect_snapshot.py`
  - `timeline.py`
  - `dashboard_contract_utils.py`

- New WebSocket APIs for dashboard cards, following the existing entry/site validation and subscription lifecycle patterns:
  - `unifi_insights/site_health/get`
  - `unifi_insights/site_health/subscribe`
  - `unifi_insights/internet_activity/get`
  - `unifi_insights/internet_activity/subscribe`
  - `unifi_insights/performance/get`
  - `unifi_insights/performance/subscribe`
  - `unifi_insights/protect/sources`
  - `unifi_insights/protect/get`
  - `unifi_insights/protect/subscribe`
  - `unifi_insights/timeline/get`
  - `unifi_insights/timeline/subscribe`

### Fixed

- Topology card narrow bottom-sheet detail panel now expands up to the full available canvas height (`max-height: calc(100% - 8px)` instead of `60%`) with a sticky header/close button, allowing full 8-row switch and access point details (including **Open device**) to display without unnecessary scrolling in 1-column Lovelace Sections cards. [#186](https://github.com/tinkerologie/ha-unifi-insights/issues/186)
- A console without UniFi Protect no longer logs `Response is not JSON for GET /proxy/protect/integration/v1/cameras` on every Home Assistant start, and a console without UniFi Network no longer logs the same warning for `/proxy/network/integration/v1/sites`. The setup probes now treat the console's HTML page as the expected "application not installed" answer and log it at DEBUG. Coordinator polling and redirected responses still warn. [#196](https://github.com/tinkerologie/ha-unifi-insights/issues/196)

### Tests

- Added a real-browser (Playwright/Chromium) regression suite for the topology detail panel (`npm run test:browser`, run in the Frontend CI job). It checks that long switch details stay inside the card and scroll to the **Open device** action in desktop, narrow bottom-sheet, and fixed-height Sections layouts, including after zoom, pan, and shrinking the card into the narrow layout. It also checks that a narrow card with enough room shows the whole detail without scrolling. The suite fails on the v2026.9.9 bundle that had the clipping bug, and the no-scroll check fails on v2026.9.10, where the bottom sheet was still capped at 60% height. [#186](https://github.com/tinkerologie/ha-unifi-insights/issues/186)

## [2026.9.10] - 2026-09-28

### Fixed

- Topology card detail panel popups no longer get truncated at the bottom in narrow bottom-sheet mode or fixed-height Lovelace Sections layouts. `uit-detail-panel` now uses a flex-column host with a shrinkable `.panel` scroll container (`flex: 1 1 auto; min-height: 0`) so narrow panels stay within `max-height: 60%` and scroll internally, while `.body` and `.content` use a shrinkable flex basis (`flex: 1 1 280px` / `240px` below `600px` with `min-height: 0`) so definite-height cards do not overflow and clip the overlay. [#186](https://github.com/tinkerologie/ha-unifi-insights/issues/186)
- Protect WebSocket connections now share the client's request-rate limiter with polling. Rate-limit responses defer further requests without restarting an established events stream, and recovered rate-limit notices are logged at debug level instead of warning. [#185](https://github.com/tinkerologie/ha-unifi-insights/issues/185)

## [2026.9.9] - 2026-09-26

### Added

- Historical **Internet Activity** site-level download and upload sensors for rolling **Last Hour** (`1h`), **Last 24 Hours** (`1d`), **Last 7 Days** (`1w`), and **Last 30 Days** (`1m`) windows (`Internet Download (Last Hour)` / `internet_download_1h`, `Internet Upload (Last Hour)` / `internet_upload_1h`, `Internet Download (Last 24 Hours)` / `internet_download_1d`, `Internet Upload (Last 24 Hours)` / `internet_upload_1d`, `Internet Download (Last 7 Days)` / `internet_download_1w`, `Internet Upload (Last 7 Days)` / `internet_upload_1w`, `Internet Download (Last 30 Days)` / `internet_download_1m`, `Internet Upload (Last 30 Days)` / `internet_upload_1m`), matching the UniFi Network **Internet Activity** (`1H`, `1D`, `1W`, `1M`) time ranges. The config coordinator queries the classic `/stat/report/{5minutes|hourly|daily}.site` endpoints every five minutes (`1d` and `1w` share a single `7d` `hourly` query), groups the entities under each site's gateway device (with virtual site fallback), exposes totals in `B` with a `GB` suggested display unit (`SensorDeviceClass.DATA_SIZE`, `SensorStateClass.MEASUREMENT`), and includes `period`, `unifi_window`, `direction`, and `report_interval` attributes. Consoles that do not support `/stat/report/*.site` skip creating the entities cleanly. [#176](https://github.com/tinkerologie/ha-unifi-insights/issues/176)

### Fixed

- Consoles without the UniFi InnerSpace application installed (or without classic report endpoints) that answer `/proxy/innerspace/integration/v1/project` with HTTP `200 OK` and the UniFi OS HTML shell (`<!doctype html>...`) no longer log a `WARNING (MainThread) [custom_components.unifi_insights.api.base] Response is not JSON for GET /proxy/innerspace/integration/v1/project` during setup or polling. Expected optional-feature probes now thread `expected_unsupported=True` through `BaseUniFiClient` so non-JSON `2xx` responses on the unredirected target path are logged at `DEBUG` while still raising `UniFiResponseError` for `async_probe_innerspace()` to mark InnerSpace unavailable. Unexpected non-JSON responses and login/SSO redirects continue to log at `WARNING`. [#183](https://github.com/tinkerologie/ha-unifi-insights/issues/183)
- Pinned `frontend` TypeScript devDependency to `6.0.3` to match the `typescript-eslint@8.70.1` peer dependency range (`>=4.8.4 <6.1.0`) and restore `npm ci` in the Frontend CI workflow.

## [2026.9.8] - 2026-09-26

### Added

- Network topology dashboard card, served by the integration and listed in the
  card picker as **UniFi Insights Topology**. It draws each site's gateway,
  switches, access points and clients as an interactive graph (pan, zoom,
  pinch, node details with uplink port, speed and PoE) or an accessible list,
  with per-card site selection, client grouping, kind filters, and clear
  loading, partial-data, reconnecting and error states. Configurable entirely
  from the card editor.
  [#166](https://github.com/tinkerologie/ha-unifi-insights/issues/166)
- Network topology WebSocket API (`unifi_insights/topology/sources`,
  `unifi_insights/topology/get` and `unifi_insights/topology/subscribe`)
  exposing a per-site graph of gateways, switches, access points and clients.
  The graph is derived from data the integration already polls, so it adds no
  API calls. It is the groundwork for the network topology dashboard card
  ([#166](https://github.com/tinkerologie/ha-unifi-insights/issues/166)).
  [#165](https://github.com/tinkerologie/ha-unifi-insights/issues/165)
- Topology client nodes now carry their VLAN and network name, and wired
  client links their switch port, taken from the `/stat/sta` response the
  integration already fetches.
  [#165](https://github.com/tinkerologie/ha-unifi-insights/issues/165)
- Remote connections now load account-wide Site Manager host, site, and device inventory, five-minute ISP metrics, and SD-WAN configuration metadata through a shared optional coordinator. Diagnostics include bounded counts, selected-host ISP samples, and collection health without exporting cloud identifiers or raw account data. A Site Manager outage does not prevent the console integration from loading. [#171](https://github.com/tinkerologie/ha-unifi-insights/issues/171)
- UniFi InnerSpace floor-plan, placed device, and unplaced inventory support across local and remote console connections. A dedicated `UniFiInnerSpaceClient` and `UnifiInsightsInnerSpaceCoordinator` poll `/v1/project`, `/v1/floor_plans`, `/v1/access_points`, `/v1/switches`, and `/v1/inventory` independently of Network and Protect, correlating records by normalized MAC (scoped by floor-plan `siteId` when present) and exposing diagnostic **InnerSpace Placement** enum sensors (`placed`, `unplaced`, `unknown`) without modifying existing Network or Protect entity unique IDs, device identifiers, or Home Assistant area assignments. Consoles where only InnerSpace is reachable can now complete setup, and diagnostics export redacted InnerSpace counts and correlation summaries without floor-plan image URLs or raw geometry shapes. [#170](https://github.com/tinkerologie/ha-unifi-insights/issues/170)

### Fixed

- UniFi Protect requests no longer exceed the console's rate limit. The local Protect Integration API allows 10 requests per second per API key, but each Protect poll fired its seven fetches within about 100 ms while camera snapshots drew from the same allowance, so polls regularly got `429 Too Many Requests`. Since 2026.9.2 these were absorbed without a log line, which hid them: one install measured 120 rate-limited requests in 15 minutes with nothing in the log. On some starts the WebSocket `host_id` lookup was the request rejected, leaving Protect on 30-second polling with no push updates. The Protect API client now spaces its requests to stay under the limit, and a rate-limited request, snapshots included, waits out the console's `Retry-After` (1 second) and is retried once. A request that is rate limited again holds the client's other requests for the same wait instead of letting them run into the limit. The Network client is unchanged, since its API is not rate limited this way.
- USL-Environmental sensors now get their water leak binary sensors. These sensors report `mountType: "none"` and no `isLeakDetected` field, so the leak entity was never created even though the sensor supports leak detection. Leak support is now also detected from `featureFlags.waterLeak`, and the state falls back to the `leakDetectedAt` timestamp (set while wet, cleared when dry) when no explicit flag is present. Sensors with a second leak channel (`featureFlags.waterLeak.channelCount >= 2`) additionally get an "External Leak Detection" binary sensor backed by `externalLeakDetectedAt`, and that field now participates in the WebSocket/REST state preservation for the leak group. Existing leak sensors (`mountType: "leak"` / `isLeakDetected`) keep their entity and unique ID unchanged. [#178](https://github.com/tinkerologie/ha-unifi-insights/issues/178)

## [2026.9.7] - 2026-09-24

### Added

- A **WAN Connection** binary sensor for each WAN in use on a gateway (named "WAN", "WAN2" like the UniFi UI), including PPPoE WANs. It follows the gateway's own per-WAN status rather than the port's link light, so a PPPoE session that drops while the cable stays connected shows as disconnected. The raw status, reachability check result and address are attributes. The existing **WAN Status** sensor keeps reporting whether the gateway itself is online. [#162](https://github.com/tinkerologie/ha-unifi-insights/issues/162)
- A **Site-to-Site VPN** binary sensor for each site-to-site VPN tunnel (such as IPsec or SD-WAN), named after the tunnel and attached to the gateway. It follows the gateway's live per-tunnel connection state and turns off when the tunnel is not connected; the tunnel type and raw status are attributes. The site-wide tunnel counts UniFi also reports were not used: they were found to stay at "0 active" even while an IPsec tunnel was up. [#162](https://github.com/tinkerologie/ha-unifi-insights/issues/162)

### Changed

- Removed the redundant vendored API version module and consolidated its version
  constant into the API constants module without changing runtime behavior.

### Fixed

- Gateway detection is now shared by every platform. Gateways recognised only by an advertised gateway/router feature now get the WAN Status sensor, and models such as "Cloud Gateway Max" now get the WAN IP/uptime sensors.

## [2026.9.6] - 2026-09-22

### Changed

- The `Response is not JSON` warning now names the request method and path that produced the non-JSON body. Previously it logged only the (redacted, truncated) response body, so when a console returned an HTML page on one of the several endpoints polled by the config coordinator, the warning could not be attributed to the call that actually failed - a sustained burst of these was undiagnosable for exactly this reason. The path is logged via `url.path`, which omits the query string, so request credentials are never written to the log or captured in a diagnostics upload.

### Fixed

- Deleting a policy-based route, firewall rule, or VPN client on the console no longer leaves its switch entity permanently unavailable. Each of these switches reports `unavailable` once its backing object is gone, but nothing ever removed the entity from the registry - so the entity stayed forever, even after the object it represented no longer existed. A one-time cleanup at startup now removes each of these three switch types when the site that owned the deleted route, rule, or client was itself confirmed present in the same refresh, so a transient config-fetch failure, a console without the feature, or an in-progress first refresh can never be mistaken for "the object is gone" and cause an over-broad prune. `UnifiWifiSwitch` has the identical gap (gated by `wifi_available`) but is intentionally left for a follow-up, since deleting a WiFi network is rarer than deleting a route, rule, or VPN client.

- Uplink TX/RX rate, per-port PoE power, and NVR storage sensors no longer report `Unknown` when a device genuinely reports a reading of `0`. Each of these value functions chained its candidate fields with `or`, which is falsy-based rather than `None`-based, so `0 or <fallback>` discarded the real `0` and fell through to a missing fallback field, landing on `Unknown`. This is user-visible: an uplink briefly idling at exactly `0 B/s` - not uncommon - flipped its rate sensor to `Unknown` until traffic resumed, creating gaps in long-term statistics and `Unknown` landmines for any template or automation testing the state. A new `first_not_none()` helper in `entity.py` walks candidates by `is not None` instead of truthiness, and the affected `sensor.py` value functions (`tx_rate`/`rx_rate`, `port_poe_power`, the int-keyed `poe_ports` stats fallback, and NVR `_get_storage_bytes`) now use it. The ~44 other `or`-chains in the integration (device/client names, MACs, IDs, booleans, and container fallbacks where an empty result is genuinely "no data") are unaffected by this change.

## [2026.9.5] - 2026-09-20

### Changed

- Device registry lookups now call `DeviceRegistry.async_get_device_by_identifier()` on Home Assistant 2026.8 and newer, scoped to this config entry. Home Assistant 2026.9 deprecated `async_get_device()` because identifiers are only unique per config entry, and schedules its removal in 2027.8. Older Home Assistant versions keep using the existing call, so the integration still runs on the 2026.6 minimum declared in `hacs.json`.

### Fixed

- A Protect-only console with no UniFi Network application (such as a standalone UNVR) no longer logs a `Response is not JSON` warning every five minutes. Such a console answers the Network sites endpoint with HTTP 200 and an HTML body, which the config coordinator retried on every five-minute poll. The coordinator now skips that poll entirely when the setup probe reports the Network application as unavailable, and stops polling it for the rest of the session the first time the console answers with a non-JSON body or a 404. The probe runs again on every integration start and reload, so installing the Network application later restores site polling.
- Fall back to classic `system-stats` (`sys_stats`) CPU, memory, and uptime metrics for gateway and console devices (such as the UCG-Max) when the v1 statistics endpoint omits them or when devices are keyed by MAC address, preventing CPU and memory usage sensors from showing "Unknown". [#151](https://github.com/tinkerologie/ha-unifi-insights/issues/151)
- Recognize `UCG`, `UXG`, `UDR`, and `UDW` gateway models alongside `UDM` and `USG` for WAN sensors and gateway entity discovery.

## [2026.9.4] - 2026-09-19

### Added

- New devices and entities now appear on their own, without reloading the integration. Adopting an access point, switch or Protect camera, plugging in an SFP transceiver, or a device gaining a new capability previously left the matching entities missing until the integration was reloaded or Home Assistant was restarted, because all eleven entity platforms only created entities during setup. Every platform now re-checks the coordinator data as it arrives and adds whatever is new, tracking what it has already created so nothing is duplicated, and honouring the **Client control** option for the entities it gates. [#143](https://github.com/tinkerologie/ha-unifi-insights/pull/143)
- A **Sites** option lets a multi-site console poll only the sites you pick. Unselected sites are not queried at all, which cuts API traffic when Home Assistant only needs one site out of many. Leave it empty to keep polling every site. The picker only appears when the console has more than one site. Devices of a site you deselect can now be deleted from the device page, since they will never update again. [#128](https://github.com/tinkerologie/ha-unifi-insights/issues/128)

### Fixed

- Adding the same console twice no longer creates a second config entry on a multi-site controller. The configuration flow and setup derived the console's identity by separate rules that had drifted apart: the flow inspected only the first site's devices and fell back to the host address when that site's id was `default`, while setup scanned every site, consulted the Protect NVR, and skipped ahead to the first non-`default` site id. Setup could therefore adopt a MAC address the flow was never able to derive, so the flow kept computing a host-based identity that no longer matched the stored entry and the duplicate check did not fire. Both paths now share one console predicate and one site-id rule, and the flow scans sites in order until it finds the console - a console in the first site still costs a single request, so only the controllers that were broken pay for the extra lookups.
- A v1 devices endpoint that answers with a server error (HTTP 5xx) on controllers that list device models the API cannot serialize (reported with a USW Pro XG family switch) no longer wipes the site's device sensors. When the site's classic name is known, the device coordinator falls back to the legacy `/stat/device` endpoint, which serves every device, maps its fields into the v1 shape and keeps per-device statistics working; if the classic endpoint is also unreachable, the refresh fails and keeps the last known device state instead of reporting an empty site. [#129](https://github.com/tinkerologie/ha-unifi-insights/issues/129)
- Redact WiFi QR payloads from downloaded diagnostics so they cannot expose WiFi passwords.
- Downloaded diagnostics no longer identify the people on the network. Only passwords and a handful of well-known keys were redacted, so a report still carried each client's name, hostname and `deviceName` (usually the owner's name), the SSID it was connected to, and the MAC addresses of the client and of the access point or switch behind it (`bssid`, `apMac`, `swMac`). Client and Wi-Fi names, SSIDs and classic-API secrets are now redacted, and every MAC address - whatever key it arrives under, including fields UniFi may add later - is replaced with a placeholder that is stable within one report, so the report still shows which access point a client sits behind without disclosing an address. Device, site and camera names are kept so the report stays readable.
- A network device the controller reports without a `model` no longer breaks entity discovery. `model` is nullable on the device model, and the WAN-status gate and the suggested-area lookup both called string methods on it directly, so one such device raised `AttributeError` and stopped the whole discovery pass for its site. [#143](https://github.com/tinkerologie/ha-unifi-insights/pull/143)
- Port sensors discovered after startup are no longer retained for the lifetime of the Home Assistant process. The stale-port sweep runs once at setup, but the list it sweeps was extended on every coordinator update, so each entity created at runtime left behind a strong reference that nothing ever released. [#143](https://github.com/tinkerologie/ha-unifi-insights/pull/143)
- Protect door/window sensors no longer stop being reconciled against the REST API on busy systems. Sensor state was only refreshed inside the main 30s Protect poll, and every WebSocket device update calls `async_set_updated_data()`, which resets the coordinator's refresh timer - so on a console where any camera reports motion more often than every 30s, the sensor poll was postponed indefinitely and door contacts rode entirely on the WebSocket stream. A missed frame then stayed wrong until traffic went quiet. Sensor reconciliation now runs on its own timer that camera traffic cannot postpone, and is triggered immediately (debounced 5s) when the devices WebSocket reconnects, instead of waiting up to 30s for the next poll.
- A slow REST response can no longer overwrite newer WebSocket door state with older data. Previously a poll that started before a door opened could land after the WebSocket had already reported the change, flipping the contact back to its previous state until the following poll. REST responses are now rejected when the cached state is demonstrably newer, comparing against both the in-flight fetch window and the controller's own `openStatusChangedAt`/`motionDetectedAt` timestamps.
  - Preservation is bounded on every path so it can never wedge: at most 3 consecutive polls for the timestamp comparison, and 10 for the WebSocket-recency check. An unbounded version of this would be worse than the bug it fixes - a door stuck `Open` in Home Assistant indefinitely.
  - Once the WebSocket-recency cap trips it latches, letting REST win until REST and the cached value actually agree, rather than resetting and preserving again for another 10 polls. Resetting would turn a sustained disagreement into a repeating flip roughly every 11th poll, and these contacts drive auto-lock automations, so a spurious flip is user-visible.
  - Door, motion, tamper and leak are tracked as independent groups. A WebSocket frame marks only the group whose fields it actually carries, and preservation copies back only the groups it decided to preserve. Ambient telemetry (temperature, humidity, battery, signal) therefore no longer suppresses a reconciliation at all, and a motion frame can no longer drag a stale cached door value over a genuinely newer REST door transition.
- Sensor fetch errors are now bounded the same way the camera and light endpoints already were: absorbed for up to 3 consecutive polls to ride out a blip, then allowed through so a genuine controller outage marks entities unavailable instead of serving a stale cache indefinitely. Authentication failures on the background refresh paths now trigger the Home Assistant reauth flow rather than surfacing as an unhandled task exception.
- Reloading the UniFi Insights config entry no longer leaks a Protect coordinator. A shutdown listener was registered without retaining its unsubscribe callback, so each reload stranded a coordinator and its cached device data for the lifetime of the Home Assistant process.
- Network devices the API lists without an `id` (reported with a UAP-AC-M "AC Mesh") are no longer dropped with `Failed to validate device ... id Field required`. They are now keyed on their MAC address. Their legacy port and PoE metrics load, but the official per-device statistics endpoint is skipped because it can only be addressed by id. [#128](https://github.com/tinkerologie/ha-unifi-insights/issues/128)
- A revoked API key (HTTP 401) now starts Home Assistant's re-authentication flow even when it is first detected by a device, client, statistics, WiFi or firewall refresh; previously those errors were swallowed and the integration kept reporting success.
- When a site's devices or clients cannot be fetched, the device refresh now fails: entities fed by it (devices, ports, client controls, firmware updates) show as unavailable and keep their last known values until the next successful refresh, instead of silently going stale. A 403 on those endpoints no longer sends the entry into a re-authentication loop.
- A WiFi or firewall fetch failure no longer blanks those entities while reporting success. Only that site's WiFi or firewall entities become unavailable, keeping their last known values, and the rest of the integration (including Protect, and setup itself) keeps working.
- One device's statistics timing out or erroring now keeps that device's last known statistics for up to three polls instead of dropping its sensors to unknown, without distorting its port throughput rates.
- Setup no longer asks you to re-authenticate when the console is only temporarily unavailable, for example while it is still starting after a power cut. If the Network or Protect API times out, drops the connection or answers with a server error (5xx) or rate limit, setup is retried automatically. If one application works while the other keeps failing, setup retries a few times and then loads with the working one instead of silently dropping the other or keeping both offline. A Protect console whose NVR has no cameras yet no longer fails setup when the NVR check itself errors.
- The configuration flow now reports "Failed to connect" instead of "Unknown error" or "Invalid authentication" when the console answers with a server error or is temporarily unavailable, including while discovering and validating cloud consoles.
- The `refresh_data` action now actually fetches from the consoles. It called the facade coordinator's own `async_refresh()`, which only re-aggregates data already in memory, so the action re-published the same values and reported success without a single API request.
- `refresh_data` reports failures instead of swallowing them. A coordinator refresh records the problem as `last_update_success` and returns normally rather than raising, so a refresh against an unreachable console was logged and reported as a success.
- One unreachable console no longer stops the others from being refreshed; every console is attempted and the failures are reported together.
- A `site_id` that no configured console owns is now reported as a validation error instead of quietly answering "refreshed".
- Service actions now run against the console that owns the target instead of always the first configured one. With two consoles set up, `restart_device`, the Protect camera, light, PTZ, chime and viewer actions, and guest authorisation were all sent to console 1 regardless of which console owned the device, camera or site.
- `authorize_guest` resolves a client given by MAC address, not only by client ID, so guest actions reach the right site.

### Changed

- Protect sensor `openStatusChangedAt` and `motionDetectedAt` are normalized to integer epoch milliseconds at the model boundary. The controller has been observed sending these as epoch integers, ISO 8601 strings and native datetimes depending on payload path, which previously left REST and WebSocket values for the same field as different Python types. Unparseable values become `None` rather than raising, so a reshaped field cannot drop the whole sensor from a fetch.
- The Protect `Sensor` model now declares `model_key` (`modelKey`), which every other Protect device model already did - `sensor.py` was the only one missing it. Because the model is dumped with `by_alias=True, exclude_none=False` and the field carries a default, every sensor dictionary the coordinator produces now includes `"modelKey": "sensor"`, including for controllers that omitted the field entirely, where the key was previously absent. Nothing in the integration reads that key off a sensor dictionary today.
- Targeting a resource that belongs to a different console now raises a validation error naming the conflict, rather than silently acting on the first console. Single-console setups are unaffected.
- `trigger_alarm` and `create_liveview` accept an optional `console_id` (the integration entry title or its entry ID). It is only needed when more than one UniFi Protect console is configured, because neither action carries a target that can be routed on - `alarm_id` is an alarm manager webhook trigger, not a device.
- The `camera_id` field on the chime actions is documented as what it actually is: a hint for picking the console that owns the chime. This integration applies volume, ringtone and repeat count chime-wide.

### Documentation

- Agent-facing guidance (`AGENTS.md`, `CONTRIBUTING.md`, Copilot instructions, `api.instructions.md`) now points at the UniFi Developer Portal's **machine-readable** endpoints alongside the human docs. The portal's documentation pages are JavaScript-rendered, so a coding agent fetching `developer.ui.com/...` receives HTTP 200 and an empty app shell with no API content - a silent dead end that reads like a successful fetch. The `llms.txt` index, per-service `llms.txt`, `openapi.json` specs and the Network `ai-gettingstarted.md` primer all return real content and are now named explicitly, along with the current service versions and a note to resolve versions from the root index rather than hardcoding them.

## [2026.9.3] - 2026-09-14

### Fixed

- Client device trackers now report `not_home` after a restart instead of sitting at `unavailable` until the client happens to reconnect. [#120](https://github.com/tinkerologie/ha-unifi-insights/pull/120) stopped the registry entry from being deleted, but no entity was created for a client that was absent from the first coordinator poll, so Home Assistant restored the entry as `unavailable` and a Person assigned to that tracker stayed `unknown`. Every surviving registry entry is now given a live entity at setup, and it keeps the name the registry retained rather than reverting to `Client <mac>`. This completes the fix for [#116](https://github.com/tinkerologie/ha-unifi-insights/issues/116).
- Client trackers are looked up by MAC across every site instead of only the site they were created on, so a client that roams between sites is still resolved. The site is now only a starting hint and is updated when the client is found elsewhere.
- An offline client's tracker no longer renders its name twice ("Kitchen Tablet Kitchen Tablet"). A client with no uplink is grouped under a standalone device representing the client itself, and the tracker is that device's primary entity, so the device carries the name and the entity no longer repeats it. Found by testing against a live Home Assistant instance; the path is rare for connected clients but is the normal one for an absent client.
- A client tracker's `mac_address` no longer disappears while the client is away. It was read back out of the live client payload, so it returned `None` for exactly the absent clients a restored tracker exists to represent: the `mac` state attribute vanished whenever the device left the network, and Home Assistant's MAC registration for the entity was skipped. The MAC is now held on the tracker, matching how the core `unifi` and `fritz` device trackers report it.
- Client trackers now declare their own `unique_id` so it is namespaced to this integration rather than being the bare MAC that `ScannerEntity` supplies. Existing trackers registered under the bare MAC are re-keyed in place on the next setup, preserving their name, area and entity ID. If the target ID is already taken, the legacy entry is left alone and still gets a working entity.

## [2026.9.2] - 2026-09-09

### Fixed

- Protect entities (cameras, lights, door sensors, doorbell/smart-detect events, NVR sensors) no longer go unavailable when the _Network_ API has a transient error. Entity availability is now gated on the sub-coordinator that actually provides the data instead of an aggregate fold across all of them, so a Network session drop (`Response is not JSON: <!doctype html>`) leaves Protect entities alone. PDU outlet switches gate on the device coordinator for the same reason.
- Protect devices are no longer permanently removed from the Home Assistant device registry because of a transient polling failure. A single empty list, 404, fetch error or short response used to be enough to purge a device, losing its area assignment, entity customizations and any automation keyed on `device_id`. Recovery is bounded on several fronts:
  - Empty or 404 device collections preserve the cache for up to 3 consecutive polls before clearing, so genuinely unadopted devices are still cleaned up.
  - Fetch errors (transport failures, HTTP 500s) preserve the cache without counting toward that limit - an error means "we could not ask", not "the devices are gone". This now applies to the camera and light endpoints too: previously only a 404 was handled there, so any other error aborted the whole Protect poll and took every Protect entity unavailable. Absorption is bounded at 3 consecutive failed polls per collection, after which the error is allowed through so a genuine controller outage still marks entities unavailable instead of serving a stale cache. Authentication errors are never absorbed, so reauth still triggers immediately.
  - A device missing from its collection must now be absent for more than 3 consecutive polls (~90s) before it is removed from the device registry.
  - When the Protect API returns a device whose payload fails to parse, that device is skipped as before but the response is flagged incomplete: the collection is merged over the cache rather than replacing it, so a field reshaped by a Protect release cannot evict the affected devices. If _every_ device in a family fails to parse, the cache is preserved instead of being treated as an empty controller.

### CI & Testing

- Added GitHub Actions workflow (`.github/workflows/test.yml`) to run pytest with branch coverage and upload reports to Codecov (`codecov/codecov-action@v5`).
- Added `codecov.yml` configuration defining 90% project and patch coverage targets with detailed PR coverage comments.
- Untracked `coverage.xml` build artifact from git tracking.
- Added Codecov coverage status badge to `README.md`.

### Documentation

- Updated the pull request template and developer guidance (`CONTRIBUTING.md`, `AGENTS.md`, Copilot instructions) to reference the [UniFi Developer Portal](https://developer.ui.com/) for official API documentation, latest developer capabilities, and coding agent context.

## [2026.9.1] - 2026-09-03

### Added

- Added support for UniFi Power Distribution Units (PDUs) and SmartPower strips (e.g. USP-PDU-Pro, USP-Strip):
  - Per-outlet relay switches (`UnifiOutletSwitch`) to toggle power state on individual outlets
  - Config switches (`UnifiOutletCycleSwitch`) for outlets supporting automatic modem power cycling
  - Per-outlet metering sensors for metered outlets (power in W, voltage in V, current in A, and power factor)
  - Device-level power total sensors for AC power consumption and AC power budget

### Changed

- Switch entity names now resolve through the entity translation system instead of
  hardcoded `_attr_name` values, satisfying the `entity-translations` quality scale
  rule. Covers the firewall rule, policy-based route, VPN client, client allow and
  WiFi switches. Displayed names and entity IDs are unchanged.

Thanks @delacjus

## [2026.9.0] - 2026-09-02

### Added

- Added VPN Client switch control (`UnifiInsightsVpnClientSwitch`) to enable or disable VPN client interfaces (e.g., Privado VPN, WireGuard, OpenVPN) directly from Home Assistant (closes #79)
- Added Policy-Based Routes (Traffic Routes) switch control (`UnifiInsightsPolicyBasedRouteSwitch`) to enable or disable traffic and VPN client routing rules dynamically from Home Assistant (closes #79)
- Added `VpnClientsEndpoint` and `VpnClient` model in the vendored Network API client targeting `/proxy/network/api/s/{site}/rest/networkconf` with strict `purpose == "vpn-client"` filtering and error envelope propagation
- Added `RoutesEndpoint` and `PolicyBasedRoute` model in the vendored Network API client targeting `/proxy/network/v2/api/site/{site}/trafficroutes`
- Added `get_vpn_clients` and `get_policy_based_routes` caching and retrieval in `UnifiConfigCoordinator` and action delegation in `UnifiFacadeCoordinator`
- Exposed route metadata attributes (`matching_target`, `interface`, `vpn_client_id`, `kill_switch_enabled`, `domains`, `ip_addresses`, `client_macs`, `network_ids`, `target_devices`, `network_id`, `next_hop`, `regions`, `ip_ranges`) on route switch entities
- Exposed VPN client metadata attributes (`client_id`, `purpose`, `vpn_type`, `ip_subnet`, `openvpn_id`, `wireguard_id`, `remote_host`) on VPN client switch entities
- Added automatic gateway device grouping for VPN clients and policy-based routes, with fallback site-level grouping

### Fixed

- Fixed `PolicyBasedRoute` parsing failure for domain-based traffic routes on UniFi Network 10.6+ where `domains` contains object dictionaries (`[{"domain": "...", ...}]`) rather than simple strings
- Fixed `kill_switch_enabled` attribute parsing from live controller payload shape and added warning when disabling a route with an active kill switch
- Fixed site name resolution on multi-site controllers via `resolve_legacy_site_name` on `UnifiFacadeCoordinator`

### Thanks

- Special thanks to [@delacjus](https://github.com/delacjus) for live-hardware testing on UniFi Dream Machine SE (Network 10.6), domain route model alignment, kill switch hardening, and multi-site resolution in PR #105

## [2026.8.4] - 2026-08-30

### Fixed

- Fixed setup retries on Protect-only consoles (for example UNVR/UNVR-Pro) when the Network `sites` endpoint returns HTTP 200 with a non-JSON HTML body; the config coordinator now tolerates this specific response only when Protect is configured, while still failing for non-200 statuses and non-Protect scenarios (closes #102)
- Fixed Protect WebSocket events stream error frames (for example rate-limit notices with an `error` field) being misclassified as parse failures; these frames are now handled explicitly and logged with a dedicated warned-once path so real parse-shape issues remain visible (closes #102)

### Thanks

- Special thanks to [@delacjus](https://github.com/delacjus) for the regression fix and WebSocket stream hardening in PR #102

## [2026.8.3] - 2026-08-30

### Added

- Added 5-minute event auto-off timeout (`STALE_EVENT_TIMEOUT`) and reconciliation logic during REST polls and WebSocket reconnections to prevent latched motion or ring states if an "end" frame is missed (closes #101)
- Added granular per-subscription WebSocket health tracking (`devices` and `events`) to integration diagnostics (closes #101)

### Fixed

- Fixed camera motion, doorbell ring, and smart detection binary sensors remaining permanently `off` by subscribing to the UniFi Protect WebSocket "events" stream and dispatching real-time detection events (closes #101)
- Fixed UniFi Protect entities only updating during periodic REST polling by connecting and driving the live Protect WebSocket "devices" stream with tolerant update envelope parsing and lifecycle management on setup and unload (closes #100)
- Fixed `UnifiInsightsEntity` and `UnifiProtectEntity` reporting `available = True` and serving stale cached device states when their backing coordinator fails — both base entities now gate availability and state updates on `self.coordinator.available` (closes #98)
- Fixed listener leak in `UnifiFacadeCoordinator` by storing unsubscribe callbacks returned by `async_add_listener()` and releasing them idempotently in `async_shutdown()` on config entry unload or reload (closes #99)
- Fixed `UnifiFacadeCoordinator.async_request_refresh()` to concurrently execute `async_refresh()` across all sub-coordinators instead of debounced requests, ensuring immediate state propagation after service calls and user actions (closes #99)
- Fixed missing camera and sensor binary sensor translation keys in `translations/en.json` that caused naming collisions falling back to generic names (closes #101)
- Added `heartbeat=30` to WebSocket connections to detect half-open sockets and trigger bounded reconnection (closes #101)

### Thanks

- Special thanks to [@delacjus](https://github.com/delacjus) for contributing the fixes and enhancements in PRs #98, #99, #100, and #101 (as well as #97 in 2026.8.2), bringing real-time Protect WebSocket device and event streaming, coordinator freshness gating, and lifecycle fixes!

## [2026.8.2] - 2026-08-29

### Fixed

- Fixed API client silently treating 2xx responses with non-JSON bodies (e.g. HTML login redirects or expired sessions from UniFi consoles and reverse proxies) as successful empty responses instead of raising `UniFiResponseError` — on Protect-only consoles like UNVR / UNVR-Pro, this previously allowed entities to freeze and serve stale cached data indefinitely without marking entities unavailable or triggering coordinator retry and backoff (closes #97, refs #93)

## [2026.8.1] - 2026-08-25

### Added

- Added support for standalone Protect-only consoles (e.g., UNVR, UNVR-Instant, UNVR-Pro) in both Local and Remote connection modes — onboarding and integration setup gracefully probe both Network and Protect APIs, enabling Protect monitoring and control even when UniFi Network is not installed or enabled (closes #93)
- Added dual-application connection validation in the config flow (`_async_validate_local_connection` and `_async_validate_remote_console`) to automatically detect available Network and Protect services

### Fixed

- Fixed device coordinator failing on UniFi OS 5.1.31 / Network 10.5.67 consoles returning `features` and `interfaces` as lists (e.g. `["switching"]`, `["accessPoint"]`, `["ports"]`, `["radios"]`) or string `uplink` IDs, which previously broke `Device` model validation and rendered all network entities unavailable; also added isolated per-item validation to `DevicesEndpoint` and `ClientsEndpoint` (closes #94)
- Fixed setup failure on UniFi Dream Router / Dream 7 consoles returning site payloads without an explicit `id` field — `Site` model now gracefully falls back to `internal_reference` or `name` (defaulting to `"default"`), and `SitesEndpoint.get_all()` skips malformed records with friendly `site_parse_error` translation (closes #80)
- Fixed `script/test` failing with `unbound variable` under `set -u` on macOS Bash 3.2

## [2026.8.0] - 2026-08-04

### Added

- Expanded the vendored UniFi Network and Protect API packages with typed models
  and endpoints for link aggregation groups (LAG), multi-chassis LAG domains, and
  switch stacks (Network) and for alarm hubs, arm profiles, relays, sirens,
  speakers, bridges, key fobs, and link stations (Protect) to support Network
  10.4.57 and Protect 7.1.87
- Added tolerant models for the `doorlock` and `viewport` Protect WebSocket
  device types, and routed their real-time updates through the Protect
  coordinator and facade aggregation

## [2026.6.4] - 2026-06-13

### Fixed

- Fixed UniFi Protect cameras showing "does not support play stream service" — `UnifiProtectCamera` overrode `async_stream_source()` but HA's `Camera.async_create_stream()` internally calls `stream_source()`; renamed the method to `stream_source()` so the stream pipeline is correctly wired

## [2026.6.3] - 2026-06-12

### Fixed

- Fixed UniFi Protect devices removed from the controller never being cleaned up — the Protect coordinator only ever added/merged devices into its data, so the stale-device registry cleanup could never detect a removal; each poll now rebuilds the camera/light/sensor/NVR/chime/viewer/liveview collections from the API response, making the Gold-tier stale-device cleanup actually work
- Fixed missing `@callback` decorator on the WiFi QR code image entity's `_handle_coordinator_update` override (same bug class as the 2026.6.2 coordinator fixes)
- Fixed all coordinators relying on Home Assistant's `current_entry` ContextVar fallback instead of passing `config_entry` explicitly to `DataUpdateCoordinator` — HA flags this pattern for removal (core breaks in 2026.8) and the explicit entry also auto-registers coordinator shutdown on entry unload
- Fixed the facade coordinator's `data` being `None` until an external (private) `_aggregate_data()` call from `__init__.py`; the facade now aggregates sub-coordinator data during its own initialization
- Fixed config entry unloading closing the API clients _before_ unloading the platforms — a failed platform unload previously left loaded entities behind with closed clients; clients are now only closed after all platforms unloaded successfully
- Fixed `script/test` being broken: it pinned `homeassistant==2026.4.1` against `pytest-homeassistant-custom-component==0.13.317` (which requires HA 2026.3.1, an unsolvable conflict) and did not install the `segno` manifest requirement; it now installs HA 2026.6.2 with plugin 0.13.338 and `segno`

### Changed

- Service actions are now registered in `async_setup` instead of the first config entry setup, per the Quality Scale `action-setup` rule — actions are validatable even when no entry is loaded, and handlers raise `ServiceValidationError` when no coordinator is available
- Replaced the deprecated `OptionsFlowWithConfigEntry` base class with `OptionsFlow` (the config entry is provided automatically since HA 2024.11)
- Bumped the minimum Home Assistant version to `2026.6.0` in `hacs.json` and the README (HA is now on Python 3.14)
- Added `data_description` helper texts to the reauthentication and reconfigure config flow steps (Quality Scale `config-flow` rule)

### Tests

- Test suite now runs (and passes: 1043 tests, 90.5% coverage) against Home Assistant 2026.6.2 / Python 3.14
- Added a test module for the WiFi QR code image platform (`tests/test_image.py`)
- Updated stale tests that still asserted pre-2026.6.0 behavior (camera snapshot width/height parameters, uppercase tracker unique IDs, the removed global tracked-clients set) and fixed coordinator/config-flow test fixtures that leaked refresh timers and sockets

## [2026.6.2] - 2026-06-06

### Fixed

- Fixed missing `@callback` decorator on `UnifiFacadeCoordinator._handle_coordinator_update` — the method is registered as an event-loop listener via `async_add_listener()` on all three sub-coordinators and must be decorated with `@callback` per HA coding standards (closes #59)
- Fixed missing `@callback` decorators on `UnifiProtectCoordinator._handle_device_update` and `_handle_event_update` — these WebSocket callback handlers call `async_update_listeners()` (itself a `@callback`), so they must also be marked as event-loop callbacks
- Fixed misleading "WebSocket callbacks registered" debug log in the Protect coordinator — the message previously fired even when neither WebSocket callback was actually registered (both are guarded by `hasattr` checks that currently return `False`)

### Notes

- The HA warning "Updating state for switch.firewall_policies_default_crowdsec_bouncer_ban_2 took 0.548 seconds" reported in #59 is **not a code bug**. HA's threshold is 0.4 s and the warning fires only once per entity lifetime (`_slow_reported = True`). The entity's properties are all O(1) dict lookups; the 0.548 s was caused by the UDM-SE host running at 100% CPU, starving HA's event loop. The `@callback` changes above are correctness improvements aligned with HA standards that reduce the chance of async-violation warnings from HA's debug checker.

## [2026.6.1] - 2026-06-04

### Added

- Added **Client Control** option (enabled by default) to the integration's options flow — disabling it prevents creation of client allow/block switch entities and client reconnect button entities, eliminating orphaned unavailable entities when clients leave the network (closes #57)

### Fixed

- Fixed deprecation warning "The deprecated alias ScannerEntity was used from unifi_insights" by updating the import to `homeassistant.components.device_tracker.ScannerEntity`; the old path in `config_entry` is removed in HA Core 2027.6 (closes #58)
- Fixed microphone switch always showing as OFF on Protect v7.1.x — the API renamed the field from `micEnabled` to `isMicEnabled`; both names are now read with `isMicEnabled` taking priority, and the PATCH call now correctly sends `isMicEnabled`
- Fixed High FPS mode switch never being created on Protect v7.1.x cameras that support it — the API removed the `hasHighFpsCapability` feature flag in favour of listing `highFps` in `featureFlags.videoModes`; both detection methods are now checked

## [2026.6.0] - 2026-05-30

### Added

- Added per-WiFi-network connected client count sensors (`sensor.<ssid>_connected_clients`) — shows how many clients are currently on each SSID, updated every polling cycle (closes #49)
- Added WiFi QR code image entities (`image.<ssid>_wifi_qr_code`) — phone cameras can scan these directly to join the network; credentials sourced from the classic API (closes #49)
- Added `authorize_guest` and `unauthorize_guest` coordinator methods wired to the official Network Integration API `POST /clients/{id}/actions` endpoint with `AUTHORIZE_GUEST_ACCESS` / `UNAUTHORIZE_GUEST_ACCESS`; the previously stubbed `authorize_guest` service now works

### Fixed

- Fixed client block, unblock, reconnect, and forget actions returning HTTP 404 — the official Network Integration API does not expose these operations; they are now routed through the classic `POST /api/s/{site}/cmd/stamgr` endpoint (`block-sta`, `unblock-sta`, `kick-sta`, `forget-sta`) which is accessible with the same local API key (closes #44)
- Fixed classic API error envelopes (`{"meta":{"rc":"error",...}}` returned with HTTP 200) now raising `UniFiResponseError` instead of silently succeeding in client station-manager commands
- Fixed UniFi Protect cameras returning HTTP 500 (`AJV_PARSE_ERROR`) when HA requested a snapshot — the Protect Integration API rejects `w`/`h` query parameters; removed them from `get_snapshot`; HA scales the returned JPEG internally
- Fixed Protect model parsing silently dropping all cameras/sensors/lights/chimes/viewers when any single device in the response contains an unrecognised field value (e.g. a new enum from Protect 7.1) — each endpoint's `get_all()` now skips and logs the individual malformed item instead of failing the whole list (closes #52)
- Fixed `CameraState` and `ViewerState` (and `CameraType`) enums mapping unknown values to `UNKNOWN` instead of raising a `ValidationError`; Protect 7.1.x new states no longer break camera entities
- Fixed toggling "Track WiFi clients" / "Track wired clients" in the integration options having no effect — the dedup set previously survived config-entry reloads (stored in `hass.data`), so re-enabling tracking added nothing; the set is now local to each setup call, and the entity registry is reconciled on every reload to remove trackers that are no longer wanted and add those that are
- Fixed stale/historical client trackers polluting the integration when tracking was enabled — only currently-connected clients (from the official `/clients` endpoint) become tracker entities; known-but-disconnected devices from the controller history are not imported
- Fixed transient controller connectivity failures (`UniFiConnectionError`, `UniFiTimeoutError`) during device coordinator site processing logging a full ERROR traceback; they are now a concise WARNING

### Changed

- Bumped minimum Home Assistant version to `2026.5.4` in `manifest.json` and `requirements.txt`
- `script/setup/bootstrap` now installs the **latest available** Home Assistant release on every run (`uv pip install --upgrade ".[dev]" homeassistant`) instead of a hard-pinned version, so the dev environment stays current automatically
- WiFi data is now enriched at config-coordinator level: secrets (SSID, passphrase, security) are pulled from the classic `/rest/wlanconf` endpoint (the official API redacts them), and per-SSID client counts are computed from the classic `/stat/sta` endpoint
- `CameraType`, `CameraState`, and `ViewerState` Protect enums now extend a shared `_TolerantStrEnum` base that maps any unrecognised value to `UNKNOWN` instead of raising
- Added `segno==1.6.6` to `manifest.json` requirements for QR code image generation

- Fixed missing `TELEPORT` value in the vendored `ClientType` enum (alongside `VPN`) to prevent client model validation failures when controllers return this connection type

## [2026.5.0] - 2026-05-05

### Fixed

- Fixed port sensor display names showing generic labels like "Data size" and "Power" by restoring missing sensor translation keys in `translations/en.json`
- Fixed per-port sensor naming to use `{port_label}` translation placeholders so each metric has a distinct name (for example, "Port 1 TX", "Port 1 RX", and "Port 1 PoE Power")
- Fixed local/remote config flow handling for UniFi Network Integration API 404 responses by surfacing a clear `api_unsupported` user-facing error instead of an unexpected exception

## [2026.4.1] - 2026-04-12

### Security

- Fixed pip tar extraction symlink vulnerability (CVE-2025-8869) by raising minimum `pip` to `>=25.3` in `requirements.txt`
- Redacted sensitive field values (API keys, passwords, tokens, credentials, PSKs, passphrases, vouchers) from debug log messages and API error bodies in the vendored API base client
- `UniFiResponseError.__str__()` now returns only the exception type and status code (`ExceptionType(status=<code>)`), preventing `message` or raw response body from leaking into logs or error messages
- Expanded `diagnostics.py` redaction set to cover tokens, credentials, network identifiers (IP, hostname, WAN/LAN IPs), device identifiers (MAC, serial), config entry fields, and location data (latitude, longitude)
- Pinned GitHub Actions `actions/checkout` and `actions/stale` to full SHA digests to mitigate supply chain attacks

### Added

- Added `"homeassistant": "2026.4.1"` minimum version requirement to `manifest.json`
- Added Aikido security scan instructions (`.github/instructions/aikido_rules.instructions.md`) to enforce pre-commit and PR security scanning

### Fixed

- Fixed `script/setup/bootstrap` pre-commit hook installation failing when a global `core.hooksPath` is configured (e.g. by the Aikido devcontainer scanner); the local repo value is now overridden to the default `.git/hooks` path before installing
- Fixed incorrect `unique_id` values in test fixtures — both remote and local config entries now use the API key as `unique_id`, matching the actual config flow behavior (`async_set_unique_id(api_key)`)

### Changed

- Replaced `softprops/action-gh-release` in the release workflow with the native `gh release create` CLI command, removing a third-party action dependency
- Log-line string concatenations in `__init__.py` consolidated to single-line strings (cosmetic)

## [2026.4.0] - 2026-04-09

### Added

- Added `.github/dependabot.yml` configuration for `devcontainers`, `github-actions`, and `pip` updates with daily checks, grouped updates, labels, and commit message prefixes

### Changed

- Updated Home Assistant minimum/runtime version to `2026.4.1` across `requirements.txt`, `hacs.json`, `script/setup/bootstrap`, and `script/test`
- Updated dependency automation to allow future Home Assistant version updates (removed Dependabot ignore rule for `homeassistant`)

### Fixed

- Fixed Protect API validation to probe the NVR endpoint when camera discovery returns an empty list, preventing valid "no cameras" setups from being misclassified as Protect API failures (PR #22)

## [2026.3.2] - 2026-03-31

### Added

- Added SFP/SFP+ port differentiation with user-friendly port names (e.g., "SFP+ 1" instead of "Port 25")
- Added SFP module diagnostic sensors: module model, vendor, type (compliance), and serial number
- Added SFP module presence binary sensor for all SFP/SFP+ ports with module detail attributes
- Added port type extra state attributes (media type, uplink status, network, SFP presence) to all port sensors
- Added auto-pagination support for wired client fetching to handle large networks correctly

### Fixed

- Fixed PoE sensor creation failing on non-PoE devices (e.g., UDM Pro) by checking port PoE capability before creating sensors
- Fixed firewall rule switches to properly filter predefined/system rules and group by gateway device
- Fixed port sensor filtering to skip inactive (DOWN) ports
- Fixed `get_port_metrics` endpoint to check PoE capability before requesting PoE data

### Removed

- Removed port enable/disable switches (unreliable with current UniFi API)
- Removed PoE toggle switches (unreliable with current UniFi API)
- Removed port power cycle buttons for PoE ports

## [2026.3.1] - 2026-03-22

### Fixed

- Fixed Unifi Remote Cloud authentication issue.

## [2026.3.0] - 2026-03-15

### Added

- Expanded the vendored UniFi Network API package with typed models and endpoints for firewall policies, DNS policies, traffic matching lists, vouchers, supporting resources, and legacy site and device lookups
- Added a full vendored UniFi Protect client with endpoint coverage for cameras, sensors, lights, chimes, NVRs, viewers, live views, events, application files, RTSPS streams, talkback sessions, and WebSocket subscriptions
- Added enable and disable switches for user-defined UniFi Network firewall rules
- Added network device temperature sensors backed by legacy controller temperature data when available
- Added a new `script/` command suite for bootstrap, linting, testing, spell checking, hassfest validation, Home Assistant startup, config reset, and HACS sync

### Changed

- Vendored the upstream `unifi-official-api` project into `custom_components/unifi_insights/api`
- Switched the integration to use the local vendored API package instead of an external runtime dependency
- Reworked the remote cloud config flow so API keys discover accessible consoles first, then validate the selected console during setup, reauth, and reconfigure
- Updated the development environment and repository tooling for the new `script/` layout, Python 3.14, and Home Assistant 2026.3.1

### Fixed

- Fixed remote cloud setup and reconfiguration flows for API keys that can access multiple UniFi consoles by prompting for console selection instead of relying on manual console ID entry
- Fixed coordinator updates to merge legacy device temperature data without failing refreshes when legacy controller endpoints are unavailable
- Fixed diagnostics and tests to report and validate the vendored API package instead of the removed external dependency
- Fixed vendored WiFi broadcast updates to send full controller-compatible payloads on update
- Added `_attr_translation_key` to 18 Protect and network entity classes for proper HA name localization
- Removed unused SSDP discovery matchers from `manifest.json` (no `async_step_ssdp` handler existed)
- Downgraded auth, connection, and timeout error logging from `exception` to `warning` to reduce log noise on expected failures
- Deduplicated `_get_client_type` helper into `entity.py` as the shared `get_client_type` function
- Added `mac` and `mac_address` to diagnostics redaction set alongside the existing `macAddress` entry
- Fixed `update.py` `latest_version` to return the actual target firmware version instead of a static placeholder string
- Added complete entity translation entries in `strings.json` for switch, select, number, event, and update platforms
- Fixed pre-existing test failures in diagnostics, entity, and update test modules

### Removed

- Removed `unifi-official-api` from manifest and project dependency declarations
- Removed the legacy `scripts/` helpers in favor of the new `script/` tooling layout

### Technical

- Added a `py.typed` marker for the vendored API package and excluded the vendored subtree from first-party Ruff, mypy, and pre-commit checks
- Updated contributor and agent documentation to reference the vendored API package and the new development script workflow

## [2026.2.0] - 2026-01-22

### Changed

- **BREAKING**: Migrated from custom API implementation to `unifi-official-api` library
- Replaced `unifi_network_api.py` and `unifi_protect_api.py` with official library
- Updated coordinator to use library's pydantic models with `model_dump(by_alias=True)` for camelCase compatibility
- Improved data transformation layer for consistent field naming

### Added

#### UniFi Network

- **WiFi Network Switches**: Enable/disable WiFi networks directly from Home Assistant
- **Client Block/Allow Switches**: Block or allow network clients
- **Device Tracker Platform**: Track wireless and wired clients as device_tracker entities
- **Port Enable Switches**: Enable/disable switch ports
- **PoE Switches**: Control PoE on switch ports
- **Update Platform**: Firmware update entities for network devices

#### UniFi Protect

- **Event Platform**: Motion, ring, and smart detection events as event entities
- **Smart Detection Binary Sensors**: Person, vehicle, animal, and package detection
- **Camera Switches**: Microphone, privacy mode, status light, and high FPS mode controls
- **PTZ Controls**: Move to preset and patrol controls via services
- **Chime Services**: Volume, ringtone, and repeat time controls

#### Integration Features

- **Connection Type Selection**: Support for both Local (direct) and Remote (cloud) connections
- **Options Flow**: Configure WiFi and wired client tracking preferences
- **Reauth Flow**: Handle expired API keys gracefully
- **Reconfigure Flow**: Update connection settings without removing the integration
- **Repairs Platform**: Automatic repair suggestions for common issues
- **Diagnostics**: Enhanced diagnostic data for troubleshooting
- **Icons**: Custom icons for all entity types via icons.json

### Fixed

- Fixed `interfaces` field handling for list vs dict format from different API responses
- Fixed NVR storage sensors showing "Unavailable" when API doesn't provide storage data
- Fixed WiFi switch device info to avoid via_device warnings
- Fixed translations for all new entity types
- Fixed client type detection for wired/wireless client counting
- Fixed availability checks for all entity types

### Removed

- Removed custom `unifi_network_api.py` (replaced by `unifi-official-api` library)
- Removed custom `unifi_protect_api.py` (replaced by `unifi-official-api` library)
- Removed unused test files and API test results

### Technical

- Updated to Home Assistant 2026.1.2 compatibility
- 621 tests with 91.62% code coverage
- Full type hints with `py.typed` marker
- Comprehensive linting with ruff/black
- Pre-commit hooks for code quality

## [2025.06.05] - 2025-06-05

### Added

- Initial release of UniFi Insights integration
- Basic sensor support for UniFi Network devices
- CPU, memory, uptime, and throughput sensors
- Device status binary sensors
- Basic camera support for UniFi Protect
- Light control for UniFi Protect lights
- Number entities for camera/light settings
- Select entities for recording modes and video modes

[Unreleased]: https://github.com/tinkerologie/ha-unifi-insights/compare/v2026.10.1...HEAD
[2026.10.1]: https://github.com/tinkerologie/ha-unifi-insights/compare/v2026.8.2...v2026.10.1
[2026.8.2]: https://github.com/tinkerologie/ha-unifi-insights/compare/v2026.8.1...v2026.8.2
[2026.8.1]: https://github.com/tinkerologie/ha-unifi-insights/compare/v2026.8.0...v2026.8.1
[2026.8.0]: https://github.com/tinkerologie/ha-unifi-insights/compare/v2026.6.4...v2026.8.0
[2026.6.4]: https://github.com/tinkerologie/ha-unifi-insights/compare/v2026.6.3...v2026.6.4
[2026.6.3]: https://github.com/tinkerologie/ha-unifi-insights/compare/v2026.6.2...v2026.6.3
[2026.6.2]: https://github.com/tinkerologie/ha-unifi-insights/compare/v2026.6.1...v2026.6.2
[2026.6.1]: https://github.com/tinkerologie/ha-unifi-insights/compare/v2026.6.0...v2026.6.1
[2026.6.0]: https://github.com/tinkerologie/ha-unifi-insights/compare/v2026.5.0...v2026.6.0
[2026.5.0]: https://github.com/tinkerologie/ha-unifi-insights/compare/v2026.4.1...v2026.5.0
[2026.4.1]: https://github.com/tinkerologie/ha-unifi-insights/compare/v2026.4.0...v2026.4.1
[2026.4.0]: https://github.com/tinkerologie/ha-unifi-insights/compare/v2026.3.2...v2026.4.0
[2026.3.2]: https://github.com/tinkerologie/ha-unifi-insights/compare/v2026.3.1...v2026.3.2
[2026.3.1]: https://github.com/tinkerologie/ha-unifi-insights/compare/v2026.3.0...v2026.3.1
[2026.3.0]: https://github.com/tinkerologie/ha-unifi-insights/compare/v2026.2.0...v2026.3.0
[2026.2.0]: https://github.com/tinkerologie/ha-unifi-insights/compare/v2025.06.05...v2026.2.0
[2025.06.05]: https://github.com/tinkerologie/ha-unifi-insights/releases/tag/v2025.06.05
