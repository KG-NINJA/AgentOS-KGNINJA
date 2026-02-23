# Factory v1.0 Stable

## Overview
Factory is a deterministic Meaning-driven application compiler. It converts constrained Meaning Markdown into a validated and canonical `runtime/spec.json` through a strict, testable pipeline.

## Architecture
Pipeline flow:

Meaning
→ Validation
→ Data Model Layer
→ AI Task Mapping
→ Blueprint Expansion
→ UI Preference Layer
→ Canonicalization
→ Transaction-safe Spec Write

Core behavior:

- Meaning input is parsed with strict heading contracts and structural validation.
- Parsed Meaning is mapped into a spec with deterministic defaults and explicit override rules.
- Data Model fields are converted into `spec.data_model` and `spec.data_schema`.
- AI task selection drives `contracts.files` through blueprint mapping.
- UI preferences apply deterministic optional file variations.
- Canonical JSON serialization normalizes output before hashing and persistence.
- Spec persistence uses atomic temp-write + fsync + rename semantics.

## Deterministic Guarantees

- Same Meaning input produces identical `runtime/spec.json`.
- Canonical JSON serialization enforces stable key ordering and normalized arrays.
- Stable SHA256 hash is generated from canonicalized spec content.
- Idempotent write path skips rewrite when output is unchanged.
- No random values are used.
- No timestamp-based mutations are used.
- No partial spec file writes are allowed.

## Data Model Layer

- Supports optional `## Data Model` Meaning section.
- Requires `- field_name: type` format per line.
- Generates:
  - `spec.data_model` as a sorted key-value type map.
  - `spec.data_schema` as JSON Schema (`type: object`, sorted `properties`, sorted `required`).
- Enforces maximum of 20 fields.
- Enforces strict type allowlist:
  - `number`, `string`, `boolean`, `array`, `object`.
- Rejects forbidden and unsafe types, duplicates, malformed lines, and nested type patterns.

## Blueprint System

- Supports 10+ `ai_task` values with deterministic artifact blueprints.
- Uses deterministic override priority:
  - Data Model override
  - Functional Intent mapping
  - Default fallback
- Produces stable `spec.contracts.files` sets.
- Normalizes and sorts blueprint file outputs for reproducibility.
- Preserves backward compatibility with existing spec shape while extending contracts.

## UI Preference Layer

- Supports optional `## UI Preference` Meaning section.
- Allowed keys:
  - `theme`, `layout`, `density`, `navigation`.
- Allowed values are strictly validated per key.
- Injects `spec.ui_preferences` with deterministic key ordering.
- Applies deterministic blueprint variations:
  - `theme: dark` adds `core/public/theme-dark.css`.
  - `layout: panel` adds `core/public/panel_layout.js`.
  - `layout: minimal` replaces primary public HTML target with `core/public/minimal.html`.
- UI preference changes are reflected in canonical hash output.

## Sandbox Hardening

- Meaning size limit: 10KB.
- Spec size ceiling: 100KB.
- Forbidden content detection blocks malicious tokens and script injection patterns.
- Path traversal patterns are rejected.
- Validation and write failures trigger rollback-safe behavior.
- Transaction-safe spec writes guarantee no incomplete persisted state.

## Test Coverage

- 38 deterministic tests cover parser, mapping, hardening, and persistence behavior.
- Coverage includes:
  - Structural validation and rejection paths.
  - Required section and bullet enforcement.
  - Duplicate section/key/field rejection.
  - Idempotent generation behavior.
  - Canonical ordering and hash stability.
  - Hash delta validation on controlled input changes.
  - AI task override and blueprint determinism.
  - UI preference injection and variation behavior.
  - Hardening gates: size limits, forbidden content, and no-partial-write guarantees.

## Limitations

- No database engine integration yet.
- No runtime execution engine yet.
- No live rendering layer yet.
- No external API integration layer yet.

## Roadmap (Post v1.0)

- Component Graph Layer
- Runtime DB integration
- CLI UX improvements
- Template pack system
- ZIP export
- GitHub integration
