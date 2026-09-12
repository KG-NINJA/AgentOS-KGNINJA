# system/ development guidance

For repository maintenance under this directory, follow `../AGENTS.md`.
Read `RULES.md` and `ARCHITECTURE.md` when changing Factory workspace governance.
Preserve generated runtime zones (`workspace/`, `inbox/`, `runtime/`, `cache/`);
these zones do not restrict scoped maintenance of repository source/instructions.

`tools/tidy.sh` moves non-allowlisted paths, including legitimate tracked files in
this checkout. Never run it here or invoke the tracked `.githooks/pre-commit`
which calls it. `tools/verify.sh` rejects the same current tracked paths and is
not a generic development gate. Do not alter hooks or configuration to bypass
an active required check; report such a blocker.

The legacy verify/tidy sequence is only for explicitly requested maintenance of
an isolated runtime matching its allowlist, after inspecting the complete move
targets and preserving user data. Do not perform cleanup for read-only or
instruction-only work. Use `git diff --check` for documents and relevant checks
from `../CONTRIBUTING.md` or `../.github/workflows/` for affected runtime contracts.
