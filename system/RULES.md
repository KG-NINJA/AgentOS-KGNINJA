# Workspace Rules

## Scope

These are legacy `kg-autonomous` runtime-layout rules. Repository development
follows `../AGENTS.md` and `AGENTS.md`; runtime working zones do not limit scoped
source-file maintenance. Consult this document only for runtime-layout work.

## Safety

- Within the legacy layout-maintenance task, do not install packages or create
  application code; maintain only the requested structure, rules and tooling.
- Avoid destructive operations and keep generated outputs inside approved zones.

## Execution

- `tools/verify.sh` and `tools/tidy.sh` use a legacy root allowlist which excludes
  legitimate tracked source/configuration paths in this repository.
- Never run `tools/tidy.sh` on this source checkout. It moves excluded paths into
  `inbox/_tidy/`; it is not a harmless validation command.
- Use the verify/tidy sequence only for explicitly requested maintenance of an
  isolated, compatible runtime after inspecting all move targets and preserving
  user data. Do not run it automatically before or after ordinary development.
- Use the scoped development checks from `AGENTS.md` for repository maintenance.
