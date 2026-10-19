# Claude Code Instructions

This repository uses a shared AI agent instruction system. **All instructions are in [`AGENTS.md`](AGENTS.md).**

Read `AGENTS.md` completely before starting any work. It contains:

- Project overview and integration identifiers
- Package structure and architectural rules
- Code style, validation commands, and quality expectations
- Home Assistant patterns (config flow, coordinator, entities, services)
- Error recovery strategy and breaking change policy
- Workflow rules (scope management, translations, documentation)

## Quick Reference

- **Domain:** `unifi_insights`
- **Title:** UniFi Insights
- **Class prefix:** `UnifiInsights`
- **Main code:** `custom_components/unifi_insights/`
- **Frontend cards:** `frontend/` (compiled into `custom_components/unifi_insights/frontend/`)
- **Validate:** `pre-commit run --all-files`
- **Lint:** `./script/lint` (ruff format + check --fix)
- **Typecheck:** `./script/mypy` (strict mypy with HA venv)
- **Test:** `pytest` (95% total and per-file branch coverage enforced)
- **Run HA:** `./script/develop`
- **Quality Scale:** Strictly follow applicable HA rules (<https://developers.home-assistant.io/docs/core/integration-quality-scale/rules>)

## Path-Specific Instructions

Additional domain-specific guidance is available in `.github/instructions/*.instructions.md`.
These files use `applyTo` globs to indicate which files they cover.
Consult the relevant instruction file when working on specific file types:

- `python.instructions.md` — Python 3.14+ style, async patterns, HA imports, typing
- `entities.instructions.md` — Entity platform patterns, MRO, HA 2026.10+ lifecycle hooks & device registry rules
- `config_flow.instructions.md` — Config/options flows, `probatio` schemas, selectors, reauth/reconfigure
- `coordinator.instructions.md` — Multi-coordinator architecture and `DataUpdateCoordinator` patterns
- `api.instructions.md` — `unifi-official-api` library, `inject-websession`, exception hierarchy
- `service_actions.instructions.md` — Service action registration (`async_setup`), console routing, translated exceptions
- `services_yaml.instructions.md` — Service action YAML schemas and selector rules
- `diagnostics.instructions.md` — Three-layer redaction (`TO_REDACT`, client/WiFi redaction, `_anonymize_macs`)
- `repairs.instructions.md` — Issue registry (`async_create_issue`) and `RepairsFlow` patterns
- `manifest.instructions.md` — `manifest.json` structure, dependencies, and calendar versioning
- `tests.instructions.md` — Testing patterns, fixtures, and ≥95% per-file branch coverage
- `translations.instructions.md` — `strings.json` and `translations/en.json` structure (entities, services, exceptions, issues)
- `configuration_yaml.instructions.md` — Local development `config/configuration.yaml` rules
- `json.instructions.md` — JSON formatting and validation (`manifest.json`, `strings.json`, `icons.json`, `hacs.json`)
- `yaml.instructions.md` — YAML formatting, `services.yaml`, and GitHub Actions workflow conventions
- `markdown.instructions.md` — Markdown formatting and documentation separation rules
