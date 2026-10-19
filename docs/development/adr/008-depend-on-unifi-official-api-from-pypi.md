# ADR-008: Depend on `unifi-official-api` from PyPI

**Date:** 2026-10-19
**Status:** Accepted (supersedes [ADR-003](./003-vendor-the-unifi-api-client.md); implemented in [#345](https://github.com/tinkerologie/ha-unifi-insights/issues/345), part of [#343](https://github.com/tinkerologie/ha-unifi-insights/issues/343))

## Context

[ADR-003](./003-vendor-the-unifi-api-client.md) copied the UniFi API client into `custom_components/unifi_insights/api/` so contract fixes could ship without waiting for an upstream release. By October 2026 the vendored tree was 128 of the integration's 199 Python files (about 17,400 lines), and it had diverged from upstream in both directions.

Home Assistant wants device communication in a library published on PyPI from CI at a tagged release ([development checklist](https://developers.home-assistant.io/docs/development_checklist), [`dependency-transparency`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/dependency-transparency/)). The vendored tree was the reason `quality_scale.yaml` marked that rule `todo`.

[tinkerologie/unifi-official-api#41](https://github.com/tinkerologie/unifi-official-api/issues/41) ported the vendored tree back upstream and removed its Home Assistant import (`protect/talkback.py` now takes an event loop or executor instead of `HomeAssistant`). `unifi-official-api` 2.0.0 was released to PyPI by the upstream `release.yml` workflow on the `v2.0.0` tag, with a PyPI provenance attestation.

Before switching, every symbol and `unittest.mock.patch` target that the integration and its tests import from `api/` (266 pairs across 24 integration modules and 76 test modules) was resolved against the 2.0.0 wheel, and the integration test suite was run against it. All symbols resolved, and the only failures came from tests that imported fakes from the deleted client test modules and one assertion on the talkback constructor.

## Decision

- `manifest.json` `requirements`, `pyproject.toml` `[project].dependencies` and `requirements.txt` pin `unifi-official-api[talkback]==2.0.0`. The `talkback` extra declares PyAV, which Home Assistant's `stream` dependency already provides.
- Integration code imports from `unifi_official_api`. The `custom_components/unifi_insights/api/` package, its tests and its tooling exclusions (Ruff, mypy, Pyright, pre-commit, labeler) are deleted. The library's own test suite covers it.
- The facade creates `TalkbackStream(session, media_url, loop=self.hass.loop)`. The stream keeps the library's default dedicated daemon thread rather than Home Assistant's shared executor, because one playback can run for minutes.
- Diagnostics report `unifi_official_api.__version__` as `library_version`.
- **API contract fixes go upstream first.** A fix to a client, endpoint, model or exception is made in `tinkerologie/unifi-official-api`, released as a new version, and then picked up here by raising the pin. The integration does not add local shims or monkey patches over library behaviour.

## Alternatives considered

### Option A: Keep vendoring (ADR-003)

- Pros: one pull request fixes the contract and the integration together.
- Cons: fails `dependency-transparency`; two diverging copies of the same client; 128 files of the package belong to a different concern.

### Option B: Depend on the library but keep a local adapter layer

- Pros: a missing or changed contract could be patched without an upstream release.
- Cons: reintroduces vendored device-communication code and hides divergence; this is the drift ADR-003 produced.

### Option C: Depend on the PyPI release and fix contracts upstream (chosen)

- Pros: meets `dependency-transparency`; one source of truth for the client; the integration package drops from 199 to 71 Python files, and to 48 once [ADR-007](./007-flat-home-assistant-module-layout.md) lands.
- Cons: a contract fix now needs an upstream release before the integration can ship it.

## Consequences

### Positive

- `dependency-transparency` is satisfied.
- `mypy`, Ruff and Pyright no longer need an `api/` exclusion; mypy type-checks integration calls against the library's `py.typed` annotations.
- The per-file coverage gate covers only integration code.

### Negative

- Two release trains: a field-found contract bug needs a library release and a pin bump.
- Behaviour the integration depends on (`last_result_complete`, the Protect rate-limit deferral hook, sanitized Access errors) now lives in another repository, so library changes must be reviewed against the integration's tests before the pin is raised.

### Neutral

- Entity unique IDs, config entry data, service schemas and WebSocket contracts are unchanged.
- Machine-readable specs (`https://developer.ui.com/{service}/{version}/openapi.json`) stay the source of truth when the library and a controller disagree; the fix lands in the library.
