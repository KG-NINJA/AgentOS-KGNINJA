# Workspace Rules

## Scope
All autonomous activity is anchored to the `kg-autonomous` root.

## Safety
- Do not install packages.
- Do not create application code.
- Create and maintain only structure, rules, and tooling.
- Avoid destructive operations.

## Execution
- Validate before changes with `tools/verify.sh`.
- Tidy and re-validate after changes using `tools/tidy.sh` then `tools/verify.sh`.
- Keep generated outputs inside approved zones.
