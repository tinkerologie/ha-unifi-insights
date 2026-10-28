---
applyTo: "**/*.json"
---

# JSON Instructions

**Applies to:** All JSON files

## Formatting

- 2-space indentation
- No trailing commas
- No comments (JSON does not support comments)
- UTF-8 encoding
- Final newline

## Key Files

- `manifest.json` — Integration metadata (domain, version, requirements, etc.)
- `strings.json` — Translatable UI strings
- `icons.json` — Custom entity icons (MDI icon references)
- `hacs.json` — Repository metadata for the Home Assistant Marketplace (2026.11+) and HACS. The Marketplace checks `homeassistant` for each release. `zip_release: true` with `filename: "unifi_insights.zip"` makes the Marketplace and HACS install the release asset that `script/build_release_zip.py` builds in `.github/workflows/release.yml`; keep both keys and the asset name in sync.

## Validation

- `manifest.json` is validated by hassfest
- `strings.json` structure must match HA translation schema
- `icons.json` must reference valid MDI icon names
