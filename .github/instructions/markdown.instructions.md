---
applyTo: "**/*.md"
---

# Markdown Instructions

**Applies to:** All Markdown files

## Formatting

- 2-space indentation for nested lists
- No trailing whitespace (except for intentional line breaks)
- One blank line before headings
- Use ATX-style headings (`#`, `##`, `###`)
- Use fenced code blocks with language identifiers

## Rules

- NEVER create random markdown files in code directories
- NEVER create documentation in `.github/` unless it's a GitHub-specified file
- Prefer module docstrings over separate markdown files
- ALWAYS ask before creating permanent documentation

## Existing Documentation

- `README.md` — User-facing documentation
- `CHANGELOG.md` — Release history (YYYY.MM.PATCH format)
- `CONTRIBUTING.md` — Contributor guide
- `AGENTS.md` — AI agent instructions (master file)
- `CLAUDE.md` / `GEMINI.md` — Agent-specific pointers

## Writing Style

- Use sentence case for headings
- Write for non-native English speakers
- Use American English
- Be concise — avoid unnecessary words

- For English technical prose, use the [Issue 9 rules in the contributor guide](../../CONTRIBUTING.md#technical-documentation). Limit instructions to 20 words and descriptions to 25 words per sentence. Preserve literals.
