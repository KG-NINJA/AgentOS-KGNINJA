# Project Factory OS Architecture

## Directories
- `system/`: governance docs and operating constraints.
- `factory/templates/`: reusable project skeletons.
- `factory/generator/`: reserved location for non-app generation workflows.
- `factory/queue/`: project ideas and queued requests.
- `workspace/`: active project material.
- `inbox/`: intake area for unclassified files.
- `tools/`: maintenance and policy scripts.
- `runtime/`: operation logs and runtime artifacts.
- `cache/`: temporary working cache.

## Workflow
1. Intake lands in `inbox/`.
2. `tools/classify.sh` routes files into `workspace/docs` or `workspace/core`.
3. New projects are scaffolded from `factory/templates/base-project/`.
4. `tools/verify.sh` guards directory policy.
5. `tools/tidy.sh` normalizes root-level clutter into `inbox/_tidy/`.
