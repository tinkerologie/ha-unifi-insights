# ADR-003: Vendor the UniFi API client inside the integration

**Date:** 2026-10-16 (recorded retroactively; vendored in release 2026.3.0)
**Status:** Superseded by [ADR-008](./008-depend-on-unifi-official-api-from-pypi.md) (2026-10-19)

## Context

Until 2026.2 the integration depended on the `unifi-official-api` package from PyPI. Releases 2026.3 through 2026.10 needed many contract fixes against the official OpenAPI specifications (Network v10.6.106, Protect v7.3.70, Site Manager v1.0.0, Carrier Fabric v1.0.0), plus new clients for Access, InnerSpace, Mobility, and Carrier Fabric. Each fix had to wait for an upstream release before the integration could ship it, and HACS users pull the integration straight from this repository.

Home Assistant's `dependency-transparency`, `async-dependency`, `inject-websession`, and `strict-typing` rules apply to the client as much as to the integration.

## Decision

Copy the client into `custom_components/unifi_insights/api/` and treat it as first-party code:

- One subpackage per UniFi application (`network`, `protect`, `access`, `innerspace`, `site_manager`, `mobility`, `carrier_fabric`), each with `client.py`, `endpoints/`, and `models/` where the API is large enough to need them.
- Shared `base.py`, `auth.py`, `exceptions.py`, and `const.py`.
- Every client takes Home Assistant's `aiohttp` session from `async_get_clientsession(hass)`.
- Pydantic models use `extra="ignore"` so new upstream fields never break parsing.
- The package has a `py.typed` marker and is covered by the same 95% per-file branch coverage gate as the integration.
- `manifest.json` lists only `segno` under `requirements`; `unifi-official-api` is never re-added.

Integration code calls client methods (`network_client.sites.get_all()`, `protect_client.cameras.set_hdr_mode(...)`) and never issues raw HTTP requests. New endpoints are added to the vendored client, not to the coordinator.

## Alternatives considered

### Option A: Keep the PyPI dependency

- Pros: smaller repository; upstream owns the client tests.
- Cons: every spec fix blocks on an upstream release; strict typing and coverage cannot be enforced here.

### Option B: Fork to a separate repository and publish it

- Pros: reusable by other projects; keeps the integration tree small.
- Cons: two release trains for one maintainer; the spec-alignment churn of 2026 would have doubled the work.

### Option C: Vendor the client (chosen)

- Pros: one PR fixes the contract and the integration together; one test suite and one type checker.
- Cons: the `api/` tree is 128 files and about 17,000 lines of the integration package.

## Consequences

### Positive

- Contract mismatches found in the field (for example the ten corrected in 2026.10.2) ship in the next release.
- `mypy --strict`, Ruff, and coverage apply to the whole code path from entity to HTTP.

### Negative

- Upstream improvements must be ported by hand.
- The vendored tree dominates file counts; it is excluded from the module-count targets in [ADR-007](./007-flat-home-assistant-module-layout.md).

### Neutral

- Machine-readable specs (`https://developer.ui.com/{service}/{version}/openapi.json`) are the source of truth when the client and the controller disagree.
- Planned reversal: the vendored tree is to be ported back upstream and released as `unifi-official-api` 2.0.0 ([tinkerologie/unifi-official-api#41](https://github.com/tinkerologie/unifi-official-api/issues/41)); that change will supersede this ADR. See [DECISIONS.md](../DECISIONS.md), future considerations.
