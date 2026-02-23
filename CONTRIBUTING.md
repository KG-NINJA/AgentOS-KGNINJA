# Contributing to Factory OS

## Scope

This repository is focused on autonomous build orchestration, queue safety, and repair reliability.
Contributions should preserve the existing architecture and prefer minimal, testable diffs.

## Development Setup

```bash
bash install.sh
bash factory.sh status
```

## Required Checks Before PR

```bash
bash -n factory.sh install.sh factory/**/*.sh
python3 -m py_compile factory/**/*.py diagnostics/*.py evolution_eval/*.py
bash factory.sh selftest
```

## Pull Request Rules

- Keep changes focused and small.
- Do not include runtime artifacts from `runtime/`, `workspace/`, `.venv/`, or local queue history.
- Update docs when behavior changes.
- Explain operational impact in the PR description.

## Commit Messages

Use concise, imperative style.

Examples:

- `fix: guard watch_queue loop against lease lock contention`
- `chore: harden public repo ignore rules`
