# Issue tracker: GitHub

Issues and specs live in GitHub Issues for `tinkerologie/ha-unifi-insights`.
Use the `gh` CLI with `--repo` set explicitly to this repository.

## Repository workflow

Follow `AGENTS.md` for issue forms, CodeRabbit plans, branches, validation,
and pull requests. This configuration supplements that workflow.

Create bugs using `.github/ISSUE_TEMPLATE/01-bug-report.yml` and enhancements
using `.github/ISSUE_TEMPLATE/02-enhancement-request.yml`. Preserve the
required title prefix, fields, and confirmations.

## Operations

- Read: `gh issue view <number> --repo <owner/repo> --json number,title,body,labels,comments`
- List: `gh issue list --repo <owner/repo> --state open --json number,title,body,labels`
- Comment: `gh issue comment <number> --repo <owner/repo> --body-file <file>`
- Label: `gh issue edit <number> --repo <owner/repo> --add-label <label>`
- Close: `gh issue close <number> --repo <owner/repo>`

Use structured arguments or a body file for multiline content.

## Pull requests as a triage surface

**PRs as a request surface: no.**

## Skill terminology

“Publish to the issue tracker” means create a GitHub issue using the
repository workflow. “Fetch the relevant ticket” means read its issue
and comments.

## Wayfinding

Keep the map in an issue labeled `wayfinder:map`. Link child tickets using
GitHub sub-issues when supported; otherwise use a task list in the map
and a `Part of #<parent>` reference in each child.

Use native issue dependencies when available. Otherwise record
`Blocked by: #<number>` references and check that every blocker is closed.
Select the first open, unassigned, unblocked child in map order.

Claim a ticket by assigning the driving developer. On resolution, record
the result, close the ticket, and add its result and link to the map.
