# Contributing to Home Assistant UniFi Insights Integration

Use this guide to prepare and submit changes.

---

## Code of Conduct and Standards

- Be respectful.
- Keep passwords, tokens, API keys, private certificates, and personal data out of commits.
- Run the required local checks before you submit a pull request.

---

## Technical documentation

Use [ASD-STE100 Simplified Technical English, Issue 9][ste] for these documents.

- `README.md`, `CONTRIBUTING.md`, and `GLOSSARY.md`
- `docs/development/ARCHITECTURE.md`
- The prose summaries in `docs/development/DECISIONS.md`

Apply the rules to English technical prose. Legal text, release records, generated files, and source code are outside
this scope. UI strings, agent instructions, and governance files are also
outside this scope. Keep ADR bodies as historical records.
Keep commands, examples, identifiers, product names, UI labels, and link
targets during language changes.

1. Use dictionary words in the specified sense, form, and part of speech (1.1–1.4).
2. Use project terms from the [glossary](GLOSSARY.md) (1.5–1.13).
3. Use American English, except in exact labels and quoted text (1.14).
4. Limit noun groups to three words, subject to the exceptions in 2.2.
5. Use active voice and approved verb forms (3.1–3.7).
6. Write complete sentences and keep each list procedural or descriptive (4.1–4.4).
7. Put the condition before the command (5.4).
8. Give one instruction in each sentence, with a maximum of 20 words (5.1–5.3).
9. Use a maximum of 25 words in a description or note (5.5, 6.3).
10. Give each paragraph one topic and a maximum of six sentences (6.4–6.6).
11. State the instruction and the risk in a warning (7.1–7.3).
12. Use the punctuation and counting rules in section 8.

For the project count, treat an identifier, an exact UI label, a product
name, or a hyphenated term as one word. Count each list item and prose table
cell separately. Count parenthetical text separately and as one word in its
sentence. Leave code blocks and URLs out of the prose count.

Before you submit a change, examine word senses and sentence limits. Compare
examples with the source. Make sure that links and anchors resolve. Use Markdown and spelling tools as aids. Examine the STE rules manually.

[ste]: https://www.asd-ste100.org/assets/files/ASD-STE100_ISSUE9.pdf

## Development Workflow

Before you make a change, read the [glossary](GLOSSARY.md),
[architecture overview](docs/development/ARCHITECTURE.md), and
[architecture decisions](docs/development/DECISIONS.md).
Use this workflow for all contributions.

### 1. Open a GitHub Issue First

1. Open an issue before you start a fix, feature, or enhancement.
2. Use the bug report form (`01-bug-report.yml`) or enhancement form (`02-enhancement-request.yml`).

GitHub disables blank issues. Maintainers close pull requests without an issue.

### 2. CodeRabbit AI Plan Generation

1. Request a plan with the `bug` or `enhancement` label.
2. If necessary, write `@coderabbitai plan` on the issue.
3. Review the Coding Plan.
4. Confirm the plan before you start implementation.

The plan gives the affected files, design choices, implementation steps,
agent prompts, and acceptance criteria.

### 3. Create a Dedicated Branch

- Create the branch from `main`.
  - Features: `git checkout -b feat/<issue-number>-<short-description>`
  - Fixes: `git checkout -b fix/<issue-number>-<short-description>`
  - Documentation: `git checkout -b docs/<issue-number>-<short-description>`
- Make commits on the issue branch.

### 4. Implementation and Local Validation

1. Implement the approved plan.
2. Record the change and its reason in `CHANGELOG.md` under `## [Unreleased]`.
3. Run the required tests, coverage checks, lint checks, type checks, and format checks.
4. Correct errors and warnings before submission.

### 5. Follow the Pull Request Template

1. Use `.github/pull_request_template.md` for the pull request.
2. Describe the changes.
3. Link the issue in **Related Issues**.
4. Paste the local verification output in **Testing Performed**.
5. Confirm every item in **Pre-Submission Governance**.

Use `Fixes #<issue>` or `Closes #<issue>` for complete work. Use
`Part of #<number>` for partial work. `Addresses #<issue>` does not close an
issue.

### 6. Open PR as DRAFT First

1. Open a draft with `gh pr create --draft` or **Create draft pull request** in GitHub.
2. Make sure that the checks pass on the draft.

### 7. Mark Ready for Review

1. Rebase on `main`.
2. After local checks and draft CI pass, mark the pull request **Ready for Review**.

Maintainers can close a pull request or return it to draft if verification
fails or the template is incomplete.

### 8. Automated Quality Gates and Merge Criteria

1. Resolve the CodeRabbit review comments.
2. Resolve blocking GitHub Copilot feedback.
3. Make sure that Codecov meets the coverage target with zero errors and warnings.
4. Make sure that the branch ruleset checks pass.

---

## Local Development and Verification

### Prerequisites and Setup

Use Python 3.14.2 or a subsequent version.

1. Run `script/setup/bootstrap`.
2. Install the Git hooks with `pre-commit install`.

For manual setup, use the Home Assistant version in `script/test` and the
matching test plugin. The plugin names can supply the same fixtures and
share files.

1. Remove existing test plugins with `pip uninstall -y pytest-homeassistant-custom-component pytest-home-assistant pytest-homeassistant`.
2. Install the dependencies with `pip install ".[dev]" "homeassistant==2026.11.0b4" "pytest-homeassistant==2026.11.0b4"`.

### Validation Commands

```bash
# Run tests with pytest and coverage
pytest
pytest --cov=custom_components --cov-report=term-missing

# Linting and formatting with ruff
ruff check .
ruff format --check .

# Static type checking
mypy custom_components/unifi_insights

# Run all pre-commit hooks across all files
pre-commit run --all-files
```

The mypy hook runs `./script/mypy` with the installed Home Assistant
dependencies. It uses `.venv`, `.local/ha-venv`, or the active virtual
environment. Run the setup script before the hook. Mypy needs Home Assistant
types to check the entities and schemas.

The spelling and format hooks omit the generated frontend bundles. Run the
frontend checks on their sources. Build the bundles with `npm run build`.

---

## Commit Message Guidelines

Use Conventional Commits.

- `feat(<scope>): <description>` — New feature
- `fix(<scope>): <description>` — Bug fix
- `docs(<scope>): <description>` — Documentation changes
- `test(<scope>): <description>` — Adding or updating tests
- `refactor(<scope>): <description>` — Code restructuring without functional change
- `chore(<scope>): <description>` — Tooling, dependencies, or repository maintenance

---

## Pull Request Governance Summary

- Put related changes in one pull request.
- Get human verification of AI-assisted changes.
- Run the local tests before you submit the pull request.
- Complete the required template checkboxes.
- Keep the pull request in draft until verification is complete.

## Issue and Pull Request Governance

These rules apply to all contributors. See the organization rules in
[GOVERNANCE.md](https://github.com/tinkerologie/.github/blob/main/GOVERNANCE.md).

1. **Open the issue first.** Use the bug or enhancement form.
   Keep the `[Bug]:` or `[Enhancement]:` prefix and the space after it.
   Automation applies the `bug` or `enhancement` label when it identifies the issue type.
   CodeRabbit posts the Coding Plan. Implement that plan.
   The labeler skips `question`, `duplicate`, `invalid`, `wontfix`, and `no-type`.
   It does not restore a type label that a maintainer removed.
2. **Link the issue.** Use `Closes #<number>`, `Fixes`, or `Resolves` for complete work.
   Use `Part of #<number>` or `Related to #<number>` for partial work.
   The issue must exist. A public issue in another repository can use `owner/repo#123`.
   Only contributors with write access can use `None` for housekeeping changes.
3. **Submit one change at a time.** GitHub limits contributors without write access to two open pull requests.
   Drafts do not count toward the limit.
   If a pull request changes a file you need, wait for it to merge.
   Rebase before you continue.
4. **Read the Tinkerologie-Bot report.** If governance fails, the report lists the problems.
   Correct the pull request description or push the missing change.
   The check runs again and the report shows the result.
5. **Rebase before review.** Rebase on `main` before you mark the pull request ready.
6. **Resolve conflicts first.** If the pull request has the `conflicts` label, rebase on `main`.
   Resolve the conflicts. Keep all `CHANGELOG.md` entries under `[Unreleased]`.
   Run the tests again. Force-push the rebased branch before you continue.
7. **Meet the coverage target.** The `codecov/patch` check requires 90% coverage of added or changed lines.
   Add tests for the uncovered lines in the Codecov comment.
   Explain remaining uncovered lines in the pull request description.
   Keep the coverage targets and coverage ignore rules.
8. **Keep work active.** After 14 days without activity, automation adds the `stale` label.
   After 30 more days without activity, automation closes the issue or pull request.
   These labels prevent this:
   - Issues: `pinned`, `security`, `confirmed`, `work-in-progress`, `priority: critical`, `priority: high`.
   - Pull requests: `pinned`, `security`, `work-in-progress`.
