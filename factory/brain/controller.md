# Factory Brain Controller v1

## Generation Flow
1. Scan `factory/queue/` for idea files.
2. If at least one idea exists, create a new project folder in `workspace/`.
3. Use incremental project names in the format `project-001`, `project-002`, and so on.
4. Route one queued idea into the newly created project's docs as `IDEA.md`.
