# ADR-001: Record architecture decisions

**Date:** 2026-10-16
**Status:** Accepted

## Context

UniFi Insights has grown to 17 platforms, 9 coordinators, 7 vendored API clients, and 6 dashboard cards. The reasons behind structural choices (why a facade coordinator exists, why the API client is vendored, why the config entry unique ID is a MAC) live in commit messages, closed issues, and `AGENTS.md`. New contributors and AI agents re-litigate them, and [#341](https://github.com/tinkerologie/ha-unifi-insights/issues/341) showed that the module layout drifted because no document said what the intended layout was.

The sibling `ha-unraid` project keeps `docs/development/ARCHITECTURE.md`, `docs/development/DECISIONS.md`, and an `adr/` folder. This repository already ships a `Create ADR` prompt under `.github/prompts/` that defines an ADR template.

## Decision

Keep architecture decisions as Architecture Decision Records (ADRs) in `docs/development/adr/`, following the practice described by Michael Nygard and the MADR project:

- One Markdown file per decision, named `NNN-short-title.md` with a zero-padded sequential number.
- Sections: title, date, status, context, decision, alternatives considered, consequences (positive, negative, neutral). This is the template from `.github/prompts/Create ADR.prompt.md`.
- Status lifecycle: `Proposed` → `Accepted` → `Deprecated` or `Superseded by ADR-NNN`. An accepted ADR is not rewritten; a new ADR supersedes it.
- `docs/development/DECISIONS.md` is the index: one table row and one short summary per ADR, plus future considerations and review triggers.
- `docs/development/ARCHITECTURE.md` describes the current system and links to the ADRs that explain it.
- Draft an ADR in `.ai-scratch/` and move it into `docs/development/adr/` only when the maintainer approves, per `AGENTS.md`.

Decisions that pre-date this record are written retroactively from the code and `CHANGELOG.md`; their date is the date they were recorded.

## Alternatives considered

### Option A: Decision log only (`DECISIONS.md` with inline entries)

- Pros: single file, matches `ha-unraid` today.
- Cons: entries grow long, alternatives get trimmed, no stable per-decision link for issues and PRs.

### Option B: Decisions in `AGENTS.md`

- Pros: agents always load it.
- Cons: `AGENTS.md` is instructions, not history; it already exceeds 37 KB and the rules there must not record rationale that changes.

### Option C: ADR files plus an index (chosen)

- Pros: stable links, room for alternatives and consequences, supersession keeps history.
- Cons: two places to update (index and record) when a decision changes.

## Consequences

### Positive

- Issues and PRs can cite `ADR-NNN` instead of re-explaining a choice.
- Superseded decisions remain readable, so the log explains how the code got here.

### Negative

- Contributors must add an index row when adding an ADR; CI does not enforce it.

### Neutral

- `DECISIONS.md` summaries duplicate the first paragraph of each ADR by design.
