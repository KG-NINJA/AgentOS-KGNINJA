# Deterministic Fallback Generator

## Why LLM fallback was removed

The harness fallback path now avoids all LLM dependencies to eliminate unstable behaviors (network/API outages, agent/tool output contamination, non-deterministic formatting).

## Purpose

This fallback isolates harness evolution and measurement by always producing a known-good minimal web_app scaffold. It is designed for reproducible baseline/kernel comparison.

## How it works

On primary generator failure in `factory/generator/codex_generate.sh`, the system calls:

- `factory/generator/local_fallback.sh --project-dir <path>`

`local_fallback.sh` deterministically writes:

- `core/server.js`
- `core/package.json`
- `core/public/app.js`
- `docs/README.md`
- `tests/basic.test.js`

No network calls and no LLM requests are performed.

## Logging markers

`runtime/activity.log` includes:

- `GENERATOR_FALLBACK_INVOCATION=deterministic`
- `GENERATOR_FALLBACK_RC=<rc>`
- `GENERATOR_FINAL_EXIT_SOURCE=local_fallback_success|local_fallback_failure`

## Restore LLM fallback later

If needed, reintroduce an LLM-based fallback by replacing the fallback call in `factory/generator/codex_generate.sh` and restoring the client integration. Keep deterministic fallback available as a control mode.
