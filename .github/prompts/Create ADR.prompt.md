---
agent: "agent"
tools: ["search/codebase", "edit", "search"]
description: "Create an Architecture Decision Record for a significant design choice"
---

# Create ADR (Architecture Decision Record)

Your goal is to document a significant architectural or design decision for the UniFi Insights integration.

If not provided, ask for:

- What decision was made
- What alternatives were considered
- Why this option was chosen
- What trade-offs and Home Assistant Quality Scale impacts exist

## ADR Template

Draft the ADR in `.ai-scratch/` first for review, then move it to `docs/` only if the developer approves.

```markdown
# ADR-NNN: [Decision Title]

**Date:** YYYY-MM-DD
**Status:** Accepted | Proposed | Deprecated | Superseded

## Context

[What problem or constraint are we solving? Which HA 2026.10+ / IQS rules or UniFi API constraints apply?]

## Decision

[What did we decide to do?]

## Alternatives Considered

### Option A: [Name]

- Pros: ...
- Cons: ...

### Option B: [Name]

- Pros: ...
- Cons: ...

## Consequences

### Positive

- ...

### Negative

- ...

### Neutral

- ...
```

## Existing Architectural Foundations to Reference

- Multi-coordinator system (`config`, `device`, `protect`, `access`, `innerspace`, `site_manager`, `mobility`, `carrier_fabric`, `rule_audit`, `facade`) stored on `entry.runtime_data`
- `unifi-official-api` PyPI client library (ADR-008) using injected `async_get_clientsession(hass)`
- Application capability probing in `probe.py` across Local, Remote, and Carrier Fabric connection modes
- camelCase/snake_case tolerance and falsy-safe resolution via `get_field()` and `first_not_none()`
- Bundled TypeScript Lovelace custom cards in `frontend/` using Home Assistant Web Awesome `--ha-*` design tokens and `getGridOptions()`
